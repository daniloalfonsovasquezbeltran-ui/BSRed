"""Distancia GPS observada y estimación de llegada; no inventa velocidades ni paradas."""
from datetime import datetime, timedelta, timezone
import math

from flask import jsonify, request
from rutas import route_definition, route_geometry, route_geometry_points, RouteDefinitionError

EARTH_M = 6371000


def distance_m(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 2*EARTH_M*math.asin(math.sqrt(min(1, max(0, h))))


def gps_motion(previous, lat, lon, accuracy, at):
    """Métrica aproximada sin historial de coordenadas; descarta saltos imposibles."""
    result = {'distancia_m': float(previous.get('distancia_m') or 0),
              'velocidad_mps': None, 'velocidad_en': at,
              'muestras_movimiento': int(previous.get('muestras_movimiento') or 0)}
    old_at = previous.get('ubicacion_en')
    if old_at is None or previous.get('latitud') is None or previous.get('longitud') is None:
        return result
    seconds = (at-old_at).total_seconds()
    if seconds<=0:
        return result
    metres = distance_m(previous['latitud'], previous['longitud'], lat, lon)
    # El filtro físico se aplica incluso con muestras muy próximas o imprecisas.
    tolerance=max(30,accuracy+float(previous.get('precision_m') or 0))
    if metres > 40*seconds+tolerance:
        result['salto_imposible']=True
        return result
    if not 5 <= seconds <= 120 or accuracy > 100 or (previous.get('precision_m') or 0) > 100:
        return result
    speed = metres/seconds
    if speed > 40:
        return result
    noise = max(8, (accuracy+float(previous.get('precision_m') or 0))/2)
    speed = speed if metres > noise else 0
    if speed:
        result['distancia_m'] += metres
        result['muestras_movimiento'] += 1
    old_speed = previous.get('velocidad_mps')
    result['velocidad_mps'] = speed if old_speed is None else (speed+old_speed)/2
    return result


def path_remaining(coordinates, lat, lon):
    """Proyecta el GPS sobre el recorrido y devuelve distancia restante y separación."""
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    accumulated = [0.0]
    for a, b in zip(coordinates, coordinates[1:]):
        accumulated.append(accumulated[-1]+distance_m(a[1],a[0],b[1],b[0]))
    best = None
    scale = math.cos(math.radians(lat))
    for index, (a,b) in enumerate(zip(coordinates, coordinates[1:])):
        ax, ay = math.radians(a[0]-lon)*EARTH_M*scale, math.radians(a[1]-lat)*EARTH_M
        bx, by = math.radians(b[0]-lon)*EARTH_M*scale, math.radians(b[1]-lat)*EARTH_M
        dx,dy = bx-ax,by-ay
        length2 = dx*dx+dy*dy
        fraction = min(1,max(0,-(ax*dx+ay*dy)/length2)) if length2 else 0
        separation = math.hypot(ax+fraction*dx,ay+fraction*dy)
        remaining = accumulated[-1]-(accumulated[index]+fraction*(accumulated[index+1]-accumulated[index]))
        if best is None or separation < best[1]:
            best = (remaining,separation)
    return best


def estimate_arrival(row, geometry, now=None):
    now = now or datetime.now(timezone.utc)
    unavailable = {'eta_minutos': None, 'llegada_estimada_en': None,
                   'distancia_restante_m': None, 'eta_estado': 'esperando_muestras'}
    if row.get('latitud') is None or row.get('ubicacion_en') is None or (now-row['ubicacion_en']).total_seconds()>120:
        return {**unavailable,'eta_estado':'sin_gps_reciente'}
    if (row.get('precision_m') or 0)>100:
        return {**unavailable,'eta_estado':'precision_insuficiente'}
    if not geometry:
        return {**unavailable,'eta_estado':'sin_trazado'}
    if (row.get('muestras_movimiento') or 0)<2 or row.get('velocidad_mps') is None:
        return unavailable
    speed = row['velocidad_mps']
    if speed < 1.2:
        return {**unavailable,'eta_estado':'bus_detenido'}
    speed_at = row.get('velocidad_en')
    if not speed_at or (now-speed_at).total_seconds()>90:
        return unavailable
    projected = path_remaining(geometry.get('geometry',{}).get('coordinates'),row['latitud'],row['longitud'])
    if projected is None:
        return {**unavailable,'eta_estado':'sin_trazado'}
    remaining, separation = projected
    if separation > max(150,3*float(row.get('precision_m') or 0)):
        return {**unavailable,'eta_estado':'fuera_recorrido'}
    seconds = remaining/speed
    if not math.isfinite(seconds) or seconds>12*3600:
        return unavailable
    return {'eta_minutos': math.ceil(seconds/60), 'llegada_estimada_en': (now+timedelta(seconds=seconds)).isoformat(),
            'distancia_restante_m': round(remaining), 'eta_estado':'disponible',
            'eta_fuente':'Estimación por recorrido y velocidad GPS reciente; puede variar.'}


def schedule_stops(cur, horario_id):
    cur.execute('''SELECT p.id,p.nombre,p.latitud,p.longitud,p.accesible,p.confirmada,p.activa,hp.orden
        FROM horario_paradas hp JOIN paradas_oficiales p ON p.id=hp.parada_id
        JOIN horarios h ON h.id=hp.horario_id WHERE hp.horario_id=%s AND p.empresa_id=h.empresa_id ORDER BY hp.orden;''',(horario_id,))
    rows=cur.fetchall()
    complete = len(rows)>=2 and all(r['confirmada'] and r['activa'] for r in rows)
    public=[dict(r) for r in rows if r['confirmada'] and r['activa']]
    return public,complete


def schedule_geometry(horario, stops, complete, cache_only=False):
    if complete:
        points=[{'nombre':s['nombre'],'lat':s['latitud'],'lon':s['longitud']} for s in stops]
        return route_geometry_points(points,cache_only=cache_only)
    definition=route_definition(horario)
    return route_geometry(definition['origen'],definition['destino'],definition['via'],cache_only=cache_only)


def register_geo(app, connection):
    @app.route('/api/mapa/paradas')
    def public_stops():
        identifier=request.args.get('horario_id')
        if identifier and (not identifier.isdigit() or not 0<int(identifier)<2147483648):
            return jsonify(success=False,message='Recorrido inválido.'),400
        conn=None
        try:
            conn=connection()
            with conn.cursor() as cur:
                cur.execute('''SELECT DISTINCT p.id,p.nombre,p.latitud,p.longitud,p.accesible,e.nombre AS empresa
                    FROM paradas_oficiales p JOIN empresas e ON e.id=p.empresa_id
                    JOIN horario_paradas hp ON hp.parada_id=p.id JOIN horarios h ON h.id=hp.horario_id
                    WHERE p.confirmada AND p.activa AND h.activo AND h.empresa_id=p.empresa_id
                    AND e.estado_validacion='aprobada' '''+('AND h.id=%s ' if identifier else '')+'ORDER BY p.nombre,p.id LIMIT 500;',
                    (int(identifier),) if identifier else ())
                return jsonify(success=True,paradas=cur.fetchall())
        except Exception as error:
            app.logger.error('No se pudieron consultar paradas: %s',type(error).__name__)
            return jsonify(success=False,message='Paradas temporalmente no disponibles.'),503
        finally:
            if conn:conn.close()
