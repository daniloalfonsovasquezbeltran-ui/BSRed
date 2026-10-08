"""Pruebas reales de PostgreSQL. Requieren una base local exclusiva de prueba."""
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import psycopg2
from psycopg2.extensions import parse_dsn

import app as website


class TelemetriaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ.get('TEST_DATABASE_URL')
        if not cls.url:
            raise RuntimeError('Configura TEST_DATABASE_URL para una base PostgreSQL local exclusiva')
        params = parse_dsn(cls.url)
        if params.get('host') not in ('localhost', '127.0.0.1') or not params.get('dbname', '').endswith('_test'):
            raise RuntimeError('Las pruebas sólo admiten localhost y una base terminada en _test')
        cls.previous_url = website.DATABASE_URL
        website.DATABASE_URL = cls.url
        website.app.config['TESTING'] = True
        cls.database = psycopg2.connect(cls.url)
        cls.database.autocommit = True
        with cls.database.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY, nombre VARCHAR(100), email VARCHAR(255) UNIQUE,
                    password VARCHAR(255), rol VARCHAR(50), gps_activo BOOLEAN DEFAULT FALSE
                );
                CREATE TABLE IF NOT EXISTS empresas (id SERIAL PRIMARY KEY, nombre VARCHAR(100));
                CREATE TABLE IF NOT EXISTS horarios (
                    id SERIAL PRIMARY KEY, tipo VARCHAR(50), origen VARCHAR(100), destino VARCHAR(100),
                    salida TIME, llegada TIME, anden VARCHAR(50), dias VARCHAR(100),
                    empresa_id INTEGER REFERENCES empresas(id)
                );
                CREATE TABLE IF NOT EXISTS registro_consultas (
                    id SERIAL PRIMARY KEY, fecha_hora TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    tab VARCHAR(50), termino_busqueda VARCHAR(255)
                );
            ''')
            migration = (Path(__file__).resolve().parents[1] / 'migrations/001_telemetria_web.sql').read_text()
            cur.execute(migration)
            cur.execute((Path(__file__).resolve().parents[1] / 'migrations/002_favoritos_avisos.sql').read_text())
            cur.execute((Path(__file__).resolve().parents[1] / 'migrations/004_choferes_viajes.sql').read_text())
            cur.execute(migration)  # La preparación debe poder repetirse sin perder datos.

    @classmethod
    def tearDownClass(cls):
        cls.database.close()
        website.DATABASE_URL = cls.previous_url

    def setUp(self):
        with self.database.cursor() as cur:
            cur.execute('TRUNCATE visitantes_web, sesiones_web, registro_consultas, horarios, empresas, usuarios RESTART IDENTITY CASCADE;')
            cur.execute('''
                INSERT INTO usuarios (nombre, email, password, rol) VALUES
                    ('Admin fixture', 'admin@example.invalid', 'fixture-password', ' ADMIN '),
                    ('Pasajero fixture', 'pasajero@example.invalid', 'fixture-password', 'pasajero');
            ''')
        self.client = website.app.test_client()

    def login(self, client=None, email='admin@example.invalid'):
        client = client or self.client
        response = client.post('/api/login', json={'email': email, 'password': 'fixture-password'})
        self.assertEqual(response.status_code, 200)
        return response

    def metrics(self):
        response = self.client.get('/api/admin/telemetria')
        self.assertEqual(response.status_code, 200)
        return response.json['metricas']

    def test_only_database_admin_can_read_metrics(self):
        self.assertEqual(self.client.get('/api/admin/telemetria').status_code, 401)
        self.assertEqual(self.client.get('/api/admin/telemetria', headers={'X-User-Role': 'admin'}).status_code, 401)
        self.login(email='pasajero@example.invalid')
        self.assertEqual(self.client.get('/api/admin/telemetria').status_code, 403)
        self.login()
        self.assertEqual(self.client.get('/api/admin/telemetria').status_code, 200)
        with self.database.cursor() as cur:
            cur.execute("UPDATE usuarios SET rol='pasajero' WHERE email='admin@example.invalid';")
        self.assertEqual(self.client.get('/api/admin/telemetria').status_code, 403)

    def test_session_is_server_verified_opaque_and_expires(self):
        response = self.login()
        token = self.client.get_cookie(website.SESSION_COOKIE).value
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', response.headers['Set-Cookie'])
        with self.database.cursor() as cur:
            cur.execute('SELECT token_hash FROM sesiones_web;')
            self.assertEqual(cur.fetchone()[0].strip(), hashlib.sha256(token.encode()).hexdigest())
            cur.execute("UPDATE sesiones_web SET expira_en=CURRENT_TIMESTAMP - INTERVAL '1 second';")
        self.assertEqual(self.client.get('/api/sesion').status_code, 401)
        self.assertEqual(self.client.get('/api/admin/telemetria').status_code, 401)

    def test_https_cookie_is_secure(self):
        response = self.client.post('/api/login', base_url='https://localhost', json={
            'email': 'admin@example.invalid', 'password': 'fixture-password'
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('Secure', response.headers['Set-Cookie'])

    def test_failed_login_and_logout_revoke_previous_session(self):
        self.login()
        token = self.client.get_cookie(website.SESSION_COOKIE).value
        response = self.client.post('/api/login', json={'email': 'admin@example.invalid', 'password': 'incorrecta'})
        self.assertEqual(response.status_code, 401)
        self.client.set_cookie(website.SESSION_COOKIE, token)
        self.assertEqual(self.client.get('/api/sesion').status_code, 401)
        self.login()
        token = self.client.get_cookie(website.SESSION_COOKIE).value
        self.assertEqual(self.client.post('/api/logout').status_code, 200)
        self.client.set_cookie(website.SESSION_COOKIE, token)
        self.assertEqual(self.client.get('/api/sesion').status_code, 401)

    def test_registration_cannot_create_an_admin(self):
        response = self.client.post('/api/registro', json={
            'nombre': 'Intruso', 'email': 'intruso@example.invalid', 'password': 'fixture', 'rol': 'admin'
        })
        self.assertEqual(response.status_code, 400)
        with self.database.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM usuarios WHERE email='intruso@example.invalid';")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_repeated_heartbeats_and_multiple_tabs_count_once(self):
        browser = website.app.test_client()
        for _ in range(3):
            self.assertEqual(browser.post('/api/presencia', json={'visible': True}).status_code, 200)
        second_tab = website.app.test_client()
        second_tab.set_cookie(website.VISITOR_COOKIE, browser.get_cookie(website.VISITOR_COOKIE).value)
        second_tab.post('/api/presencia', json={'visible': True})
        self.login()
        metrics = self.metrics()
        self.assertEqual(metrics['visitantes_24h'], 1)
        self.assertEqual(metrics['usuarios_online'], 1)

    def test_different_browsers_count_separately(self):
        for _ in range(2):
            browser = website.app.test_client()
            browser.post('/api/presencia', json={'visible': True})
        self.login()
        metrics = self.metrics()
        self.assertEqual(metrics['visitantes_24h'], 2)
        self.assertEqual(metrics['usuarios_online'], 2)

    def test_rolling_windows_expire_without_a_disconnect_request(self):
        with self.database.cursor() as cur:
            cur.execute('''
                INSERT INTO visitantes_web VALUES
                  (%s, CURRENT_TIMESTAMP - INTERVAL '23 hours 59 minutes', CURRENT_TIMESTAMP - INTERVAL '23 hours'),
                  (%s, CURRENT_TIMESTAMP - INTERVAL '24 hours 1 minute', CURRENT_TIMESTAMP - INTERVAL '24 hours 1 minute'),
                  (%s, CURRENT_TIMESTAMP - INTERVAL '80 seconds', CURRENT_TIMESTAMP - INTERVAL '80 seconds'),
                  (%s, CURRENT_TIMESTAMP - INTERVAL '100 seconds', CURRENT_TIMESTAMP - INTERVAL '100 seconds');
            ''', tuple(str(i) * 64 for i in range(1, 5)))
        self.login()
        metrics = self.metrics()
        self.assertEqual(metrics['visitantes_24h'], 3)
        self.assertEqual(metrics['usuarios_online'], 1)

    def test_hidden_visit_is_recorded_without_marking_online(self):
        browser = website.app.test_client()
        browser.post('/api/presencia', json={'visible': False})
        self.login()
        self.assertEqual(self.metrics()['visitantes_24h'], 1)
        self.assertEqual(self.metrics()['usuarios_online'], 0)
        browser.post('/api/presencia', json={'visible': True})
        self.assertEqual(self.metrics()['usuarios_online'], 1)
        browser.post('/api/presencia', json={'visible': False})
        self.assertEqual(self.metrics()['usuarios_online'], 1)  # Otra pestaña puede seguir visible.

    def test_invalid_presence_payload_and_cookie_are_handled(self):
        for payload in (None, [], {}, {'visible': 'true'}, {'visible': 1}):
            self.assertEqual(self.client.post('/api/presencia', json=payload).status_code, 400)
        self.client.set_cookie(website.VISITOR_COOKIE, 'valor-invalido')
        self.assertEqual(self.client.post('/api/presencia', json={'visible': True}).status_code, 200)
        self.assertTrue(website.TOKEN_PATTERN.fullmatch(self.client.get_cookie(website.VISITOR_COOKIE).value))

    def test_zero_metrics_are_real_and_not_simulated(self):
        self.login()
        metrics = self.metrics()
        for name in ('visitantes_24h', 'usuarios_online', 'consultas_diarias', 'andenes_programados'):
            self.assertEqual(metrics[name], 0)
        self.assertNotIn('puntualidad', metrics)
        self.assertNotIn('capacidad_terminal', metrics)

    def test_schedule_queries_do_not_become_unique_visitors(self):
        self.client.get('/api/horarios')
        self.client.get('/api/horarios')
        self.login()
        metrics = self.metrics()
        self.assertEqual(metrics['consultas_diarias'], 2)
        self.assertEqual(metrics['visitantes_24h'], 0)
        self.assertEqual(metrics['usuarios_online'], 0)

    def test_failed_database_does_not_return_stale_or_fake_numbers(self):
        self.login()
        with patch.object(website, 'get_db_connection', side_effect=psycopg2.OperationalError('fixture')):
            response = self.client.get('/api/admin/telemetria')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json['success'])
        self.assertNotIn('metricas', response.json)

    def test_private_files_and_api_caches_are_not_exposed(self):
        for path in ('/.env', '/app.py', '/migrations/001_telemetria_web.sql'):
            self.assertEqual(self.client.get(path).status_code, 404)
        response = self.client.get('/api/sesion')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn('Cookie', response.headers['Vary'])


if __name__ == '__main__':
    unittest.main()
