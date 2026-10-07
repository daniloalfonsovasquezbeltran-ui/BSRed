"""Rutas y horarios; las pruebas de API usan sólo PostgreSQL local de prueba."""
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import psycopg2
from psycopg2.extensions import make_dsn, parse_dsn

import app as website
import rutas


ROAD_FIXTURE = {
    'geometry': {'type': 'LineString', 'coordinates': [
        [-72.3312, -39.6430], [-72.4900, -39.7300],
        [-72.8228, -39.8511], [-73.2459, -39.8142],
    ]},
    'distance_m': 118400.0,
    'duration_s': 7300.0,
}


def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


class PlaceAndScheduleTests(unittest.TestCase):
    def test_places_normalize_accents_unicode_and_whitespace(self):
        places = rutas.resolve_places('  PANGUIPULLI  ', '  CON\u0303ARIPE  ')
        self.assertEqual([p['nombre'] for p in places], ['Panguipulli', 'Coñaripe'])
        ray = rutas.resolve_places('Panguipulli', '  Lícan   Ray  ')
        self.assertEqual(ray[-1]['nombre'], 'Lican Ray')
        for place in places:
            self.assertTrue(-90 <= place['lat'] <= 90)
            self.assertTrue(-180 <= place['lon'] <= 180)

    def test_explicit_stops_preserve_order_and_remove_adjacent_duplicates(self):
        places = rutas.resolve_places('Panguipulli', 'Neltume', ['Panguipulli', 'Choshuenco', 'Choshuenco'])
        self.assertEqual([p['nombre'] for p in places], ['Panguipulli', 'Choshuenco', 'Neltume'])

    def test_compounds_require_verified_itinerary_not_slash_inference(self):
        for destination in ('Choshuenco / Neltume', 'Coñaripe / Lican Ray'):
            with self.subTest(destination=destination), self.assertRaises(rutas.RouteDefinitionError):
                rutas.resolve_places('Panguipulli', destination)
        with patch.dict(rutas.VERIFIED_ITINERARIES, {}, clear=True):
            with self.assertRaises(rutas.RouteDefinitionError):
                rutas.route_definition({'id': 1, 'origen': 'Panguipulli', 'destino': 'Choshuenco / Neltume'})

    def test_unknown_or_identical_localities_do_not_invent_routes(self):
        for origin, destination in (('Panguipulli', 'Localidad inexistente'), ('Panguipulli', 'Panguipulli')):
            with self.subTest(origin=origin, destination=destination), self.assertRaises(rutas.RouteDefinitionError):
                rutas.resolve_places(origin, destination)

    def test_verified_compound_uses_confirmed_stops_instead_of_display_label(self):
        confirmed = {'origen': 'Panguipulli', 'destino': 'Neltume', 'via': ['Choshuenco'],
                     'tipo_recorrido': 'paradas_confirmadas', 'nota_itinerario': 'Fixture contrastada'}
        with patch.dict(rutas.VERIFIED_ITINERARIES, {1: confirmed}, clear=True):
            definition = rutas.route_definition({'id': 1, 'origen': 'Panguipulli', 'destino': 'Choshuenco / Neltume'})
        self.assertEqual(definition, confirmed)
        places = rutas.resolve_places(definition['origen'], definition['destino'], definition['via'])
        self.assertEqual([p['nombre'] for p in places], ['Panguipulli', 'Choshuenco', 'Neltume'])

    def test_daily_windows_use_chile_calendar_and_utc_timestamps(self):
        result = rutas.schedule_windows(time(7), time(8, 30), 'Diario', utc('2026-10-07T14:00:00Z'))
        self.assertTrue(result['estimacion_disponible'])
        self.assertIsNone(result['mensaje_estimacion'])
        self.assertEqual(len(result['ventanas']), 3)
        departures = [utc(w['salida_en']) for w in result['ventanas']]
        self.assertEqual(departures, [utc(f'2026-10-{day:02d}T10:00:00Z') for day in (6, 7, 8)])
        for window in result['ventanas']:
            self.assertEqual(utc(window['llegada_en']) - utc(window['salida_en']), timedelta(minutes=90))
            self.assertEqual(utc(window['salida_en']).utcoffset(), timedelta(0))

    def test_day_ranges_skip_sunday_and_accept_accented_weekdays(self):
        now = utc('2026-10-11T15:00:00Z')
        result = rutas.schedule_windows('07:00', '08:00', 'Lunes a Sábado', now)
        self.assertEqual([utc(w['salida_en']).astimezone(rutas.CHILE_TZ).weekday()
                          for w in result['ventanas']], [5, 0])
        weekday = rutas.schedule_windows('07:00', '08:00', 'Miércoles, viernes y sábado', utc('2026-10-07T15:00:00Z'))
        self.assertEqual(len(weekday['ventanas']), 1)
        self.assertEqual(utc(weekday['ventanas'][0]['salida_en']).astimezone(rutas.CHILE_TZ).weekday(), 2)

    def test_utc_midnight_does_not_advance_chile_operating_day(self):
        result = rutas.schedule_windows('07:00', '08:00', 'Diario', utc('2026-10-08T01:00:00Z'))
        local_dates = [utc(w['salida_en']).astimezone(rutas.CHILE_TZ).date().isoformat()
                       for w in result['ventanas']]
        self.assertEqual(local_dates, ['2026-10-06', '2026-10-07', '2026-10-08'])

    def test_overnight_service_uses_departure_weekday(self):
        result = rutas.schedule_windows('23:30', '01:00', 'Lunes a Viernes', utc('2026-10-10T04:15:00Z'))
        self.assertEqual(len(result['ventanas']), 1)
        window = result['ventanas'][0]
        self.assertEqual(utc(window['salida_en']), utc('2026-10-10T02:30:00Z'))
        self.assertEqual(utc(window['llegada_en']), utc('2026-10-10T04:00:00Z'))

    def test_invalid_schedule_or_unknown_days_has_no_estimate(self):
        invalid = [('25:00', '08:00', 'Diario'), ('07:00', None, 'Diario'),
                   ('07:00', '07:00', 'Diario'), ('07:00', '08:00', 'Cuando haya pasajeros')]
        for departure, arrival, days in invalid:
            with self.subTest(departure=departure, arrival=arrival, days=days):
                result = rutas.schedule_windows(departure, arrival, days, utc('2026-10-07T14:00:00Z'))
                self.assertFalse(result['estimacion_disponible'])
                self.assertEqual(result['ventanas'], [])
                self.assertTrue(result['mensaje_estimacion'])

    def test_missing_dst_hour_does_not_create_impossible_departure(self):
        result = rutas.schedule_windows('00:30', '02:00', 'Diario', utc('2026-09-06T12:00:00Z'))
        local_departures = [utc(w['salida_en']).astimezone(rutas.CHILE_TZ) for w in result['ventanas']]
        self.assertEqual([d.day for d in local_departures], [5, 7])
        self.assertTrue(all((d.hour, d.minute) == (0, 30) for d in local_departures))


class RoutingProviderAndCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='bsred-routes-test-')
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {'ROUTE_CACHE_DIR': self.directory.name})
        env.start()
        self.addCleanup(env.stop)
        sleeper = patch.object(rutas.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def cached_file(self):
        return next(Path(self.directory.name).glob('*.json'))

    def age_cache(self, seconds):
        path = self.cached_file()
        data = json.loads(path.read_text())
        data['cached_at'] = rutas.time.time() - seconds
        path.write_text(json.dumps(data))

    def response(self, payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response

    def test_provider_returns_road_geometry_with_fixed_https_destination(self):
        payload = {'code': 'Ok', 'routes': [{'geometry': ROAD_FIXTURE['geometry'],
                                           'distance': ROAD_FIXTURE['distance_m'],
                                           'duration': ROAD_FIXTURE['duration_s']}]}
        with patch.object(rutas, 'urlopen', return_value=self.response(payload)) as opening:
            result = rutas._fetch_route(rutas.resolve_places('Panguipulli', 'Valdivia'))
        self.assertEqual(result, ROAD_FIXTURE)
        request = opening.call_args.args[0]
        self.assertTrue(request.full_url.startswith('https://router.project-osrm.org/route/v1/driving/'))
        self.assertIn('geometries=geojson', request.full_url)
        self.assertIn('overview=full', request.full_url)

    def test_untrusted_url_cannot_select_provider_destination(self):
        with patch.object(rutas, 'urlopen') as opening:
            with self.assertRaises(rutas.RouteDefinitionError):
                rutas.route_geometry('https://169.254.169.254/latest/meta-data/', 'Valdivia')
        opening.assert_not_called()

    def test_malformed_provider_payload_has_controlled_failure(self):
        payloads = [[], {'code': 'NoRoute', 'routes': []},
                    {'code': 'Ok', 'routes': [{'geometry': None, 'distance': 10, 'duration': 10}]},
                    {'code': 'Ok', 'routes': [{'geometry': {'type': 'Point', 'coordinates': [0, 0]},
                                              'distance': 10, 'duration': 10}]}]
        for payload in payloads:
            with self.subTest(payload=payload), patch.object(rutas, 'urlopen', return_value=self.response(payload)):
                with self.assertRaises(rutas.RoutingUnavailable):
                    rutas._fetch_route(rutas.resolve_places('Panguipulli', 'Valdivia'))

    def test_invalid_geometry_and_nonpositive_metrics_are_rejected(self):
        invalids = [({'type': 'LineString', 'coordinates': [[0, 0]]}, 10, 10),
                    ({'type': 'LineString', 'coordinates': [[0, 0], [181, 0]]}, 10, 10),
                    ({'type': 'LineString', 'coordinates': [[0, 0], [0, float('nan')]]}, 10, 10),
                    (ROAD_FIXTURE['geometry'], 0, 10), (ROAD_FIXTURE['geometry'], 10, -1)]
        for geometry, distance, duration in invalids:
            with self.subTest(geometry=geometry, distance=distance, duration=duration):
                with self.assertRaises(rutas.RoutingUnavailable):
                    rutas.validate_geometry({'geometry': geometry, 'distance_m': distance, 'duration_s': duration})

    def test_normalized_route_reuses_cache_and_reports_real_source(self):
        with patch.object(rutas, '_fetch_route', side_effect=lambda places: deepcopy(ROAD_FIXTURE)) as provider:
            first = rutas.route_geometry('Panguipulli', 'Valdivia')
            second = rutas.route_geometry(' PANGUIPULLI ', ' VALDIVIA ')
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first['localidades'], ['Panguipulli', 'Valdivia'])
        self.assertEqual(first['geometry'], ROAD_FIXTURE['geometry'])
        self.assertEqual(first['fuente'], 'OpenStreetMap / OSRM')
        self.assertFalse(first['trazado_cache_vencido'])

    def test_provider_failure_uses_only_previously_valid_recent_cache(self):
        with patch.object(rutas, '_fetch_route', return_value=deepcopy(ROAD_FIXTURE)):
            rutas.route_geometry('Panguipulli', 'Valdivia')
        self.age_cache(rutas.CACHE_TTL + 60)
        with patch.object(rutas, '_fetch_route', side_effect=rutas.RoutingUnavailable('fixture')):
            stale = rutas.route_geometry('Panguipulli', 'Valdivia')
        self.assertTrue(stale['trazado_cache_vencido'])
        self.assertEqual(stale['geometry'], ROAD_FIXTURE['geometry'])
        self.age_cache(rutas.MAX_STALE + 60)
        with patch.object(rutas, '_fetch_route', side_effect=rutas.RoutingUnavailable('fixture')):
            with self.assertRaises(rutas.RoutingUnavailable):
                rutas.route_geometry('Panguipulli', 'Valdivia')

    def test_missing_or_corrupted_cache_does_not_invent_a_straight_route(self):
        with patch.object(rutas, '_fetch_route', side_effect=rutas.RoutingUnavailable('fixture')):
            with self.assertRaises(rutas.RoutingUnavailable):
                rutas.route_geometry('Panguipulli', 'Valdivia')
        with patch.object(rutas, '_fetch_route', return_value=deepcopy(ROAD_FIXTURE)):
            rutas.route_geometry('Panguipulli', 'Valdivia')
        self.cached_file().write_text('{invalid json')
        with patch.object(rutas, '_fetch_route', side_effect=rutas.RoutingUnavailable('fixture')):
            with self.assertRaises(rutas.RoutingUnavailable):
                rutas.route_geometry('Panguipulli', 'Valdivia')


class RouteApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ.get('TEST_DATABASE_URL')
        if not cls.url:
            raise RuntimeError('Configura TEST_DATABASE_URL para PostgreSQL local exclusivo')
        params = parse_dsn(cls.url)
        if params.get('host') not in ('localhost', '127.0.0.1') or not params.get('dbname', '').endswith('_test'):
            raise RuntimeError('Las pruebas sólo admiten localhost y una base terminada en _test')
        cls.database = psycopg2.connect(cls.url)
        cls.database.autocommit = True
        with cls.database.cursor() as cursor:
            cursor.execute('CREATE SCHEMA IF NOT EXISTS rutas_test;')
            cursor.execute('''CREATE TABLE IF NOT EXISTS rutas_test.horarios (
                id SERIAL PRIMARY KEY, origen VARCHAR(100), destino VARCHAR(100),
                salida TIME, llegada TIME, dias VARCHAR(100)
            );''')
        cls.previous_url = website.DATABASE_URL
        website.DATABASE_URL = make_dsn(cls.url, options='-c search_path=rutas_test')
        website.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        website.DATABASE_URL = cls.previous_url
        with cls.database.cursor() as cursor:
            cursor.execute('DROP SCHEMA rutas_test CASCADE;')
        cls.database.close()

    def setUp(self):
        with self.database.cursor() as cursor:
            cursor.execute('TRUNCATE rutas_test.horarios RESTART IDENTITY;')
            cursor.execute('''INSERT INTO rutas_test.horarios (origen, destino, salida, llegada, dias) VALUES
                ('Panguipulli', 'Valdivia', '09:00', '11:00', 'Diario'),
                ('Panguipulli', 'Choshuenco / Neltume', '07:00', '08:30', 'Lunes a Sábado'),
                ('Valdivia', 'Panguipulli', '10:30', NULL, 'Diario');''')
        self.client = website.app.test_client()
        self.directory = tempfile.TemporaryDirectory(prefix='bsred-api-routes-test-')
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {'ROUTE_CACHE_DIR': self.directory.name})
        env.start()
        self.addCleanup(env.stop)

    def test_public_route_has_geojson_schedule_and_explicit_estimation_metadata(self):
        with patch.object(rutas, '_fetch_route', return_value=deepcopy(ROAD_FIXTURE)):
            response = self.client.get('/api/mapa/ruta/1')
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertTrue(data['success'])
        self.assertEqual(data['horario_id'], 1)
        self.assertEqual(data['geometry'], ROAD_FIXTURE['geometry'])
        self.assertEqual(data['localidades'], ['Panguipulli', 'Valdivia'])
        self.assertGreater(data['distance_m'], 0)
        self.assertGreater(data['duration_s'], 0)
        self.assertEqual(data['tipo_recorrido'], 'destino_unico')
        self.assertTrue(data['nota_itinerario'])
        self.assertEqual(len(data['ventanas']), 3)
        self.assertTrue(data['estimacion_disponible'])
        self.assertIsNone(data['mensaje_estimacion'])
        self.assertEqual(utc(data['servidor_en']).utcoffset(), timedelta(0))
        self.assertNotIn('gps', data)

    def test_unknown_id_and_unverified_compound_do_not_call_router(self):
        with patch.dict(rutas.VERIFIED_ITINERARIES, {}, clear=True), patch.object(rutas, '_fetch_route') as provider:
            self.assertEqual(self.client.get('/api/mapa/ruta/999').status_code, 404)
            response = self.client.get('/api/mapa/ruta/2')
        provider.assert_not_called()
        self.assertEqual(response.status_code, 422)
        self.assertFalse(response.json['success'])
        self.assertNotIn('geometry', response.json)

    def test_missing_arrival_keeps_route_without_fake_bus_estimation(self):
        with patch.object(rutas, '_fetch_route', return_value=deepcopy(ROAD_FIXTURE)):
            response = self.client.get('/api/mapa/ruta/3')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['estimacion_disponible'])
        self.assertEqual(response.json['ventanas'], [])
        self.assertTrue(response.json['mensaje_estimacion'])

    def test_router_or_database_failure_returns_503_without_geometry(self):
        with patch.object(rutas, '_fetch_route', side_effect=rutas.RoutingUnavailable('fixture')):
            response = self.client.get('/api/mapa/ruta/1')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json['success'])
        self.assertNotIn('geometry', response.json)
        with patch.object(website, 'get_db_connection', side_effect=psycopg2.OperationalError('fixture')):
            response = self.client.get('/api/mapa/ruta/1')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('geometry', response.json)


if __name__ == '__main__':
    unittest.main()
