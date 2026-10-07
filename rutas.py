"""Trayectos por carretera y ventanas de viaje; nunca simula datos GPS."""
from contextlib import contextmanager
from datetime import date, datetime, time as clock_time, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


CHILE_TZ = ZoneInfo('America/Santiago')
ROUTER_URL = 'https://router.project-osrm.org/route/v1/driving/'
CACHE_TTL = 7 * 24 * 60 * 60
MAX_STALE = 30 * 24 * 60 * 60

# Centros de localidades. Las paradas y los terminales exactos pueden
# configurarse después sin convertir un centro de ciudad en una posición GPS.
PLACES = {
    'panguipulli': ('Panguipulli', -39.6419909, -72.3333801),
    'choshuenco': ('Choshuenco', -39.8372162, -72.0823409),
    'neltume': ('Neltume', -39.8505613, -71.9429185),
    'conaripe': ('Coñaripe', -39.5678530, -72.0074170),
    'lican ray': ('Lican Ray', -39.4897238, -72.1553851),
    'puerto fuy': ('Puerto Fuy', -39.8732995, -71.8944513),
    'valdivia': ('Valdivia', -39.8141262, -73.2459859),
    'temuco': ('Temuco', -38.7358908, -72.5905380),
    'los lagos': ('Los Lagos', -39.8634009, -72.8129655),
}

# Un destino compuesto no prueba que sus localidades sean paradas sucesivas.
# Sólo se incorporan aquí itinerarios contrastados con fuentes de la operación.
VERIFIED_ITINERARIES = {}


class RouteDefinitionError(ValueError):
    pass


class RoutingUnavailable(RuntimeError):
    pass


def normalize(text):
    value = unicodedata.normalize('NFKD', str(text or ''))
    return ' '.join(''.join(c for c in value if not unicodedata.combining(c)).lower().split())


def resolve_places(origen, destino, via=()):
    names = [origen, *via, destino]
    result = []
    for name in names:
        key = normalize(name)
        if '/' in key:
            raise RouteDefinitionError('Destino compuesto sin itinerario confirmado')
        if key not in PLACES:
            raise RouteDefinitionError(f'Localidad sin ubicación configurada: {name}')
        label, lat, lon = PLACES[key]
        place = {'nombre': label, 'lat': lat, 'lon': lon}
        if not result or result[-1] != place:
            result.append(place)
    if len(result) < 2:
        raise RouteDefinitionError('El recorrido necesita origen y destino diferentes')
    return result


def route_definition(horario):
    origen, destino = horario['origen'], horario['destino']
    if '/' not in str(destino) and '/' not in str(origen):
        return {'origen': origen, 'destino': destino, 'via': [],
                'tipo_recorrido': 'destino_unico',
                'nota_itinerario': 'Trayecto sugerido por carretera entre localidades; no es una traza GPS.'}
    definition = VERIFIED_ITINERARIES.get(horario['id'])
    if not definition:
        raise RouteDefinitionError('El horario agrupa destinos; falta confirmar si son paradas o servicios distintos')
    return dict(definition)


def cache_directory():
    directory = Path(os.environ.get('ROUTE_CACHE_DIR', '/tmp/bsred-routing-cache'))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@contextmanager
def provider_lock(directory):
    # Compartido por los workers Gunicorn: evita consultas duplicadas y ráfagas.
    with (directory / 'provider.lock').open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def validate_geometry(data):
    if not isinstance(data, dict):
        raise RoutingUnavailable('Respuesta de carreteras inválida')
    geometry = data.get('geometry', {})
    if not isinstance(geometry, dict):
        raise RoutingUnavailable('El proveedor no devolvió un trazado válido')
    coordinates = geometry.get('coordinates', [])
    if geometry.get('type') != 'LineString' or not isinstance(coordinates, list) or not 2 <= len(coordinates) <= 50000:
        raise RoutingUnavailable('El proveedor no devolvió un trazado válido')
    for point in coordinates:
        if (not isinstance(point, list) or len(point) != 2
                or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in point)
                or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
            raise RoutingUnavailable('Coordenadas de carretera inválidas')
    for field in ('distance_m', 'duration_s'):
        value = data.get(field)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise RoutingUnavailable('Distancia o duración de carretera inválida')
    return data


def _fetch_route(places):
    coordinates = ';'.join(f"{p['lon']:.6f},{p['lat']:.6f}" for p in places)
    url = ROUTER_URL + coordinates + '?' + urlencode({
        'geometries': 'geojson', 'overview': 'full', 'steps': 'false', 'alternatives': 'false'
    })
    request = Request(url, headers={
        'User-Agent': 'BSRed/1.0 (+https://bsred.onrender.com)', 'Accept': 'application/json'
    })
    try:
        with urlopen(request, timeout=8) as response:
            body = response.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise RoutingUnavailable('Respuesta de rutas demasiado grande')
            payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get('code') != 'Ok' or not payload.get('routes'):
            raise RoutingUnavailable('No hay un trayecto por carretera disponible')
        route = payload['routes'][0]
        if not isinstance(route, dict):
            raise RoutingUnavailable('El proveedor no devolvió un trazado válido')
        return validate_geometry({'geometry': route.get('geometry'),
                                  'distance_m': route.get('distance'),
                                  'duration_s': route.get('duration')})
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, KeyError) as error:
        raise RoutingUnavailable('El servicio de carreteras no está disponible') from error


def _read_cache(path):
    try:
        data = json.loads(path.read_text())
        validate_geometry(data)
        if not isinstance(data.get('cached_at'), (int, float)):
            return None
        return data
    except (OSError, ValueError, TypeError, AttributeError, RoutingUnavailable):
        return None


def route_geometry(origen, destino, via=()):
    places = resolve_places(origen, destino, via)
    directory = cache_directory()
    key = hashlib.sha256(json.dumps([ROUTER_URL, places, 1], sort_keys=True).encode()).hexdigest()
    path = directory / (key + '.json')
    with provider_lock(directory):
        cached = _read_cache(path)
        age = time.time() - cached['cached_at'] if cached else float('inf')
        if age < CACHE_TTL:
            result = cached
        else:
            last_request = directory / 'last-request'
            try:
                wait = 1 - (time.time() - float(last_request.read_text()))
                if wait > 0:
                    time.sleep(min(wait, 1))
            except (OSError, ValueError):
                pass
            last_request.write_text(str(time.time()))
            try:
                result = _fetch_route(places)
                result['cached_at'] = time.time()
                temporary = directory / (key + f'.{os.getpid()}.tmp')
                temporary.write_text(json.dumps(result))
                temporary.replace(path)
            except RoutingUnavailable:
                if not cached or age > MAX_STALE:
                    raise
                result = cached
    return {k: result[k] for k in ('geometry', 'distance_m', 'duration_s')} | {
        'localidades': [place['nombre'] for place in places],
        'fuente': 'OpenStreetMap / OSRM',
        'trazado_actualizado_en': datetime.fromtimestamp(result['cached_at'], timezone.utc).isoformat(),
        'trazado_cache_vencido': time.time() - result['cached_at'] >= CACHE_TTL,
    }


WEEKDAYS = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']


def operating_days(value):
    days = normalize(value)
    if days in ('diario', 'todos los dias', 'todos', 'lunes a domingo'):
        return set(range(7))
    for start in range(7):
        for end in range(7):
            if days == f'{WEEKDAYS[start]} a {WEEKDAYS[end]}':
                span = (end - start) % 7
                return {(start + offset) % 7 for offset in range(span + 1)}
    selected = set()
    for token in days.replace(',', ' ').replace(';', ' ').split():
        if token in WEEKDAYS:
            selected.add(WEEKDAYS.index(token))
        elif token not in ('y',):
            return None
    return selected or None


def _parse_clock(value):
    if isinstance(value, clock_time):
        return value.replace(tzinfo=None)
    return clock_time.fromisoformat(str(value))


def _local_datetime(day, clock):
    naive = datetime.combine(day, clock)
    local = naive.replace(tzinfo=CHILE_TZ)
    # La transición de verano puede omitir una hora: no inventar ese viaje.
    if local.astimezone(timezone.utc).astimezone(CHILE_TZ).replace(tzinfo=None) != naive:
        return None
    return local


def schedule_windows(salida, llegada, dias, now=None):
    unavailable = {'ventanas': [], 'estimacion_disponible': False,
                   'mensaje_estimacion': 'No hay horario y días suficientes para estimar este bus.'}
    try:
        departure, arrival = _parse_clock(salida), _parse_clock(llegada)
    except (ValueError, TypeError):
        return unavailable
    if departure == arrival:
        return unavailable
    days = operating_days(dias)
    if days is None:
        return unavailable
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(CHILE_TZ).date()
    windows = []
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        start = _local_datetime(day, departure)
        end = _local_datetime(day + timedelta(days=arrival < departure), arrival)
        if start is None or end is None or end <= start:
            continue
        windows.append({'salida_en': start.astimezone(timezone.utc).isoformat(),
                        'llegada_en': end.astimezone(timezone.utc).isoformat()})
    return {'ventanas': windows, 'estimacion_disponible': True, 'mensaje_estimacion': None}
