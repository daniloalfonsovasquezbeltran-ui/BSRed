"""Favoritos y cola Web Push con PostgreSQL local y entrega de prueba sin red."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import psycopg2
from psycopg2.extensions import make_dsn, parse_dsn
from psycopg2.extras import RealDictCursor

import app as website
import avisos


NOW = datetime(2026, 10, 7, 11, 50, tzinfo=timezone.utc)  # Miércoles 08:50 en Chile.
DEPARTURE = NOW + timedelta(minutes=10)


def base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def subscription_fixture(suffix='one'):
    public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {
        'endpoint': 'https://fcm.googleapis.com/fcm/send/bsred-test-' + suffix,
        'keys': {'p256dh': base64url(public), 'auth': base64url(b'0123456789abcdef')},
    }


def vapid_fixture():
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {'PUSH_ENABLED': 'true',
            'VAPID_PUBLIC_KEY': base64url(public),
            'VAPID_PRIVATE_KEY': base64url(private.private_numbers().private_value.to_bytes(32, 'big')),
            'VAPID_SUBJECT': 'https://localhost',
            'NOTIFICATION_CRON_TOKEN': 'fixture-cron-token-not-a-production-secret'}


class PushValidationTests(unittest.TestCase):
    def test_known_push_service_accepts_actual_p256_key(self):
        subscription = subscription_fixture()
        clean = avisos.validate_push_subscription(subscription)
        self.assertEqual(clean['endpoint'], subscription['endpoint'])
        self.assertEqual(clean['endpoint_hash'], hashlib.sha256(subscription['endpoint'].encode()).hexdigest())
        self.assertEqual(clean['p256dh'], subscription['keys']['p256dh'])

    def test_untrusted_internal_redirect_and_userinfo_endpoints_are_rejected(self):
        invalid = [
            'http://fcm.googleapis.com/fcm/send/fixture',
            'https://127.0.0.1/private',
            'https://169.254.169.254/latest/meta-data/',
            'https://fcm.googleapis.com.attacker.invalid/fcm/send/fixture',
            'https://fcm.googleapis.com@attacker.invalid/fcm/send/fixture',
            'https://attacker.invalid@fcm.googleapis.com/fcm/send/fixture',
            'https://fcm.googleapis.com:8443/fcm/send/fixture',
            'https://fcm.googleapis.com/fcm/send/fixture#fragment',
        ]
        for endpoint in invalid:
            subscription = subscription_fixture()
            subscription['endpoint'] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                avisos.validate_push_subscription(subscription)

    def test_invalid_keys_and_payload_are_rejected(self):
        invalid = [None, [], {}, {'endpoint': 'https://fcm.googleapis.com/fcm/send/fixture'},
                   {'endpoint': 'https://fcm.googleapis.com/fcm/send/fixture',
                    'keys': {'p256dh': base64url(b'\x04' + b'\x00' * 64), 'auth': base64url(b'a' * 16)}},
                   {'endpoint': 'https://fcm.googleapis.com/fcm/send/fixture',
                    'keys': {'p256dh': '!', 'auth': 'not-an-auth-key'}}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                avisos.validate_push_subscription(payload)

    def test_departures_respect_chile_weekdays_and_utc_midnight(self):
        dates = avisos.departure_windows('09:00', 'Lunes a Viernes', NOW)
        self.assertEqual(dates, [DEPARTURE, DEPARTURE + timedelta(days=1)])
        sunday = datetime(2026, 10, 11, 11, 50, tzinfo=timezone.utc)
        self.assertEqual(avisos.departure_windows('09:00', 'Lunes a Sábado', sunday),
                         [datetime(2026, 10, 12, 12, tzinfo=timezone.utc)])
        night = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)  # Aún miércoles en Chile.
        self.assertEqual(avisos.departure_windows('23:30', 'Miércoles', night),
                         [datetime(2026, 10, 8, 2, 30, tzinfo=timezone.utc)])

    def test_departure_skips_dst_missing_hour_and_unknown_calendar(self):
        before_change = datetime(2026, 9, 5, 16, tzinfo=timezone.utc)
        self.assertEqual(avisos.departure_windows('00:30', 'Diario', before_change), [])
        for clock, days in [('25:00', 'Diario'), ('09:00', 'Cuando haya pasajeros'), (None, 'Diario')]:
            with self.subTest(clock=clock, days=days):
                self.assertEqual(avisos.departure_windows(clock, days, NOW), [])

    def test_web_push_encrypts_payload_for_actual_receiver_with_verified_https(self):
        import http_ece
        import requests

        receiver = ec.generate_private_key(ec.SECP256R1())
        receiver_public = receiver.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        subscription = {'endpoint': 'https://fcm.googleapis.com/fcm/send/bsred-test-encryption',
                        'keys': {'p256dh': base64url(receiver_public), 'auth': base64url(b'0123456789abcdef')}}
        payload = {'title': 'Salida programada', 'body': 'Panguipulli → Valdivia',
                   'tag': 'fixture-encryption-1', 'url': '/?recorrido=1'}
        response = requests.Response()
        response.status_code = 201
        response._content = b''
        with patch.dict(os.environ, vapid_fixture()), patch('requests.Session.request', return_value=response) as send:
            avisos.send_web_push(subscription, payload, 600)
        send.assert_called_once()
        outgoing = send.call_args.kwargs
        self.assertTrue(outgoing['verify'])
        self.assertFalse(outgoing['allow_redirects'])
        encrypted = outgoing['data']
        self.assertNotIn(payload['body'].encode(), encrypted)
        decrypted = http_ece.decrypt(encrypted, private_key=receiver,
                                     auth_secret=b'0123456789abcdef', version='aes128gcm')
        self.assertEqual(json.loads(decrypted), payload)

    def test_web_push_redirect_is_failure_without_following_untrusted_url(self):
        import requests

        response = requests.Response()
        response.status_code = 302
        response.headers['Location'] = 'https://169.254.169.254/latest/meta-data/'
        response._content = b''
        with patch.dict(os.environ, vapid_fixture()), patch('requests.Session.request', return_value=response) as send:
            with self.assertRaises(avisos.PushDeliveryError):
                avisos.send_web_push(subscription_fixture(), {'tag': 'fixture-no-redirect'}, 60)
        send.assert_called_once()
        self.assertFalse(send.call_args.kwargs['allow_redirects'])


class FavoritesPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get('TEST_DATABASE_URL')
        if not url:
            raise RuntimeError('Configura TEST_DATABASE_URL para PostgreSQL local exclusivo')
        params = parse_dsn(url)
        if params.get('host') not in ('localhost', '127.0.0.1') or not params.get('dbname', '').endswith('_test'):
            raise RuntimeError('Las pruebas sólo admiten localhost y una base terminada en _test')
        cls.url = make_dsn(url, options='-c search_path=favoritos_test')
        cls.database = psycopg2.connect(cls.url, cursor_factory=RealDictCursor)
        cls.database.autocommit = True
        with cls.database.cursor() as cur:
            cur.execute('CREATE SCHEMA IF NOT EXISTS favoritos_test;')
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
            ''')
            migrations = Path(__file__).resolve().parents[1] / 'migrations'
            migration_paths = [migrations / '001_telemetria_web.sql', next(migrations.glob('002_*.sql'))]
            for migration_path in migration_paths:
                migration = migration_path.read_text().replace('public.', 'favoritos_test.')
                cur.execute(migration)
                cur.execute(migration)  # La preparación se puede repetir sin perder datos.
        cls.previous_url = website.DATABASE_URL
        website.DATABASE_URL = cls.url
        website.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        website.DATABASE_URL = cls.previous_url
        cls.database.close()

    def setUp(self):
        environment = patch.dict(os.environ, vapid_fixture())
        environment.start()
        self.addCleanup(environment.stop)
        with self.database.cursor() as cur:
            cur.execute('''TRUNCATE avisos_salida, suscripciones_push, favoritos_recorridos,
                           sesiones_web, horarios, empresas, usuarios RESTART IDENTITY CASCADE;''')
            cur.execute('''
                INSERT INTO usuarios (nombre, email, password, rol) VALUES
                    ('Pasajero fixture', 'pasajero@example.invalid', 'fixture-password', 'pasajero'),
                    ('Otra cuenta fixture', 'otra@example.invalid', 'fixture-password', 'pasajero'),
                    ('Admin fixture', 'admin@example.invalid', 'fixture-password', 'admin');
                INSERT INTO empresas (nombre) VALUES ('Empresa fixture');
                INSERT INTO horarios (tipo, origen, destino, salida, llegada, anden, dias, empresa_id) VALUES
                    ('salida', 'Panguipulli', 'Valdivia', '09:00', '11:00', '1', 'Diario', 1),
                    ('salida', 'Panguipulli', 'Temuco', '09:00', '11:30', '2', 'Domingo', 1),
                    ('llegada', 'Valdivia', 'Panguipulli', '09:00', '11:00', '1', 'Diario', 1),
                    ('salida', 'Valdivia', 'Temuco', '09:00', '11:00', '1', 'Diario', 1);
            ''')
        self.client = website.app.test_client()

    def connection(self):
        return psycopg2.connect(self.url, cursor_factory=RealDictCursor)

    def login(self, client=None, email='pasajero@example.invalid'):
        client = client or self.client
        response = client.post('/api/login', json={'email': email, 'password': 'fixture-password'})
        self.assertEqual(response.status_code, 200)
        return client

    def query(self, sql, params=()):
        with self.database.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def subscribed_fixture(self, suffix='one', user_id=1):
        clean = avisos.validate_push_subscription(subscription_fixture(suffix))
        rows = self.query('''INSERT INTO suscripciones_push
            (usuario_id, endpoint, endpoint_hash, p256dh, auth, activa, dispositivo_hash)
            VALUES (%s, %s, %s, %s, %s, TRUE, %s) RETURNING id;''',
            (user_id, clean['endpoint'], clean['endpoint_hash'], clean['p256dh'], clean['auth'],
             hashlib.sha256(('device-' + suffix).encode()).hexdigest()))
        return rows[0]['id']

    def favorite_fixture(self, user_id=1, schedule=1, minutes=10):
        self.query('''INSERT INTO favoritos_recorridos (usuario_id, horario_id, anticipacion_min)
                      VALUES (%s, %s, %s);''', (user_id, schedule, minutes))

    def test_favorites_require_verified_session(self):
        self.assertEqual(self.client.get('/api/favoritos').status_code, 401)
        self.assertEqual(self.client.put('/api/favoritos/1', json={}).status_code, 401)
        self.assertEqual(self.client.delete('/api/favoritos/1', json={}).status_code, 401)
        self.login()
        self.query("UPDATE sesiones_web SET expira_en=CURRENT_TIMESTAMP-INTERVAL '1 second';")
        self.assertEqual(self.client.get('/api/favoritos').status_code, 401)

    def test_favorites_are_idempotent_and_owned_by_account(self):
        self.login()
        for minutes in (10, 15, 15):
            response = self.client.put('/api/favoritos/1', json={'minutos_antes': minutes})
            self.assertEqual(response.status_code, 200)
        rows = self.query('SELECT usuario_id, horario_id, anticipacion_min FROM favoritos_recorridos;')
        self.assertEqual(rows, [{'usuario_id': 1, 'horario_id': 1, 'anticipacion_min': 15}])
        other = self.login(website.app.test_client(), 'otra@example.invalid')
        self.assertEqual(other.get('/api/favoritos').status_code, 200)
        self.assertEqual(other.delete('/api/favoritos/1', json={}).status_code, 200)
        self.assertEqual(len(self.query('SELECT * FROM favoritos_recorridos;')), 1)
        for _ in range(2):
            self.assertEqual(self.client.delete('/api/favoritos/1', json={}).status_code, 200)
        self.assertEqual(self.query('SELECT * FROM favoritos_recorridos;'), [])

    def test_admin_can_save_favorite_and_invalid_lead_is_rejected(self):
        self.login(email='admin@example.invalid')
        self.assertEqual(self.client.put('/api/favoritos/1', json={}).status_code, 200)
        self.assertEqual(self.query('SELECT anticipacion_min FROM favoritos_recorridos;')[0]['anticipacion_min'], 10)
        for minutes in (True, 0, 7, 30, '10', None):
            with self.subTest(minutes=minutes):
                response = self.client.put('/api/favoritos/1', json={'minutos_antes': minutes})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.put('/api/favoritos/9999', json={}).status_code, 404)

    def test_list_is_account_private_and_arrivals_can_be_saved_without_departure_alerts(self):
        self.login()
        for schedule in (1, 3, 4):
            self.assertEqual(self.client.put(f'/api/favoritos/{schedule}', json={}).status_code, 200)
        response = self.client.get('/api/favoritos')
        self.assertEqual(response.status_code, 200)
        rows = {favorite['horario_id']: favorite for favorite in response.json['favoritos']}
        self.assertEqual(set(rows), {1, 3, 4})
        self.assertTrue(rows[1]['avisos_disponibles'])
        for schedule in (3, 4):
            self.assertFalse(rows[schedule]['avisos_disponibles'])
            self.assertTrue(rows[schedule]['motivo_aviso'])
        other = self.login(website.app.test_client(), 'otra@example.invalid')
        self.assertEqual(other.get('/api/favoritos').json['favoritos'], [])

    def test_config_exposes_only_public_key_and_disables_mismatched_keys(self):
        response = self.client.get('/api/notificaciones/config')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['disponible'])
        self.assertEqual(response.json['public_key'], os.environ['VAPID_PUBLIC_KEY'])
        self.assertNotIn(os.environ['VAPID_PRIVATE_KEY'], response.get_data(as_text=True))
        with patch.dict(os.environ, {'VAPID_PRIVATE_KEY': vapid_fixture()['VAPID_PRIVATE_KEY']}):
            response = self.client.get('/api/notificaciones/config')
        self.assertFalse(response.json['disponible'])
        self.assertIsNone(response.json['public_key'])

    def test_subscription_registration_is_idempotent_and_device_cookie_is_private(self):
        self.login()
        subscription = subscription_fixture()
        for _ in range(2):
            response = self.client.post('/api/notificaciones/suscripciones', json={'subscription': subscription})
            self.assertEqual(response.status_code, 200)
        rows = self.query('SELECT usuario_id, endpoint, activa FROM suscripciones_push;')
        self.assertEqual(rows, [{'usuario_id': 1, 'endpoint': subscription['endpoint'], 'activa': True}])
        self.assertIsNotNone(self.client.get_cookie('bsred_push_dispositivo'))
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', response.headers['Set-Cookie'])
        state = self.client.post('/api/notificaciones/suscripciones/estado', json={'endpoint': subscription['endpoint']})
        self.assertEqual(state.status_code, 200)
        self.assertTrue(state.json['activa'])
        for _ in range(2):
            self.assertEqual(self.client.delete('/api/notificaciones/suscripciones',
                             json={'endpoint': subscription['endpoint']}).status_code, 200)
        self.assertFalse(self.query('SELECT activa FROM suscripciones_push;')[0]['activa'])

    def test_logout_revokes_only_current_device_and_other_account_cannot_manage_it(self):
        first = self.login()
        second = self.login(website.app.test_client())
        subscriptions = [subscription_fixture('first'), subscription_fixture('second')]
        for client, subscription in zip((first, second), subscriptions):
            response = client.post('/api/notificaciones/suscripciones', json={'subscription': subscription})
            self.assertEqual(response.status_code, 200)
        other = self.login(website.app.test_client(), 'otra@example.invalid')
        response = other.post('/api/notificaciones/suscripciones/estado', json={'endpoint': subscriptions[1]['endpoint']})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['activa'])
        self.assertEqual(other.delete('/api/notificaciones/suscripciones',
                         json={'endpoint': subscriptions[1]['endpoint']}).status_code, 200)
        self.assertTrue(self.query('SELECT activa FROM suscripciones_push WHERE endpoint=%s;',
                                  (subscriptions[1]['endpoint'],))[0]['activa'])
        self.assertEqual(first.post('/api/logout').status_code, 200)
        active = {row['endpoint']: row['activa'] for row in self.query('SELECT endpoint, activa FROM suscripciones_push;')}
        self.assertFalse(active[subscriptions[0]['endpoint']])
        self.assertTrue(active[subscriptions[1]['endpoint']])

    def test_internal_dispatcher_rejects_browser_session_and_bad_bearer(self):
        self.login(email='admin@example.invalid')
        for headers in ({}, {'Authorization': 'Bearer incorrect'}):
            response = self.client.post('/api/interno/avisos-salida', json={}, headers=headers)
            self.assertEqual(response.status_code, 401)
        called = threading.Event()

        def dispatch(*_):
            called.set()
            return {'enviado': 0}

        with patch.object(website, 'run_notification_cycle', side_effect=dispatch) as run:
            response = self.client.post('/api/interno/avisos-salida', json={}, headers={
                'Authorization': 'Bearer ' + os.environ['NOTIFICATION_CRON_TOKEN']})
            self.assertEqual(response.status_code, 202)
            self.assertTrue(response.json['aceptado'])
            self.assertTrue(called.wait(5))
            run.assert_called_once()

    def test_same_origin_json_guard_rejects_cross_site_mutations(self):
        self.login()
        for method, path, payload in [('put', '/api/favoritos/1', {}),
                                      ('delete', '/api/favoritos/1', {}),
                                      ('post', '/api/notificaciones/suscripciones', {'subscription': subscription_fixture()})]:
            response = getattr(self.client, method)(path, json=payload,
                                                   headers={'Origin': 'https://attacker.invalid'})
            self.assertEqual(response.status_code, 403)
        response = self.client.put('/api/favoritos/1', data='minutos_antes=10',
                                   content_type='application/x-www-form-urlencoded')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.query('SELECT * FROM favoritos_recorridos;'), [])

    def test_invalid_subscription_does_not_contact_arbitrary_server(self):
        self.login()
        payload = subscription_fixture()
        payload['endpoint'] = 'https://169.254.169.254/latest/meta-data/'
        response = self.client.post('/api/notificaciones/suscripciones', json={'subscription': payload})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.query('SELECT * FROM suscripciones_push;'), [])

    def test_worker_only_sends_due_panguipulli_departures_on_operating_days(self):
        self.subscribed_fixture()
        for schedule in (1, 2, 3, 4):
            self.favorite_fixture(schedule=schedule)
        sent = []
        sender = lambda subscription, payload, ttl: sent.append((subscription, payload, ttl))
        avisos.run_notification_cycle(self.connection, sender=sender, now=NOW - timedelta(minutes=1))
        self.assertEqual(sent, [])
        avisos.run_notification_cycle(self.connection, sender=sender, now=NOW)
        avisos.run_notification_cycle(self.connection, sender=sender, now=NOW + timedelta(seconds=20))
        self.assertEqual(len(sent), 1)
        _, payload, ttl = sent[0]
        self.assertEqual(payload['horario_id'], 1)
        self.assertIn('Salida programada', payload['title'] + ' ' + payload['body'])
        self.assertIn('09:00', payload['body'])
        self.assertTrue(0 < ttl <= 600)
        today = self.query('SELECT horario_id, estado FROM avisos_salida WHERE salida_en=%s;', (DEPARTURE,))
        self.assertEqual(today, [{'horario_id': 1, 'estado': 'enviado'}])

    def test_worker_expired_subscription_is_deactivated_without_retry(self):
        identifier = self.subscribed_fixture()
        self.favorite_fixture()
        with patch.object(avisos, 'send_web_push', side_effect=AssertionError('No red de producción')):
            avisos.run_notification_cycle(self.connection,
                sender=lambda *_: (_ for _ in ()).throw(avisos.PushDeliveryError(status=410)), now=NOW)
        self.assertFalse(self.query('SELECT activa FROM suscripciones_push WHERE id=%s;', (identifier,))[0]['activa'])
        calls = []
        avisos.run_notification_cycle(self.connection, sender=lambda *args: calls.append(args),
                                      now=NOW + timedelta(minutes=1))
        self.assertEqual(calls, [])

    def test_worker_transient_failure_retries_same_job_without_reporting_sent(self):
        self.subscribed_fixture()
        self.favorite_fixture()
        avisos.run_notification_cycle(self.connection,
            sender=lambda *_: (_ for _ in ()).throw(avisos.PushDeliveryError(status=503)), now=NOW)
        job = self.query('SELECT * FROM avisos_salida WHERE salida_en=%s;', (DEPARTURE,))[0]
        self.assertEqual(job['estado'], 'pendiente')
        self.assertIsNone(job['enviado_en'])
        self.assertGreater(job['reintentar_en'], NOW)
        sent = []
        avisos.run_notification_cycle(self.connection, sender=lambda *args: sent.append(args),
                                      now=job['reintentar_en'] + timedelta(seconds=1))
        self.assertEqual(len(sent), 1)
        jobs = self.query('SELECT estado, intentos FROM avisos_salida WHERE salida_en=%s;', (DEPARTURE,))
        self.assertEqual(jobs, [{'estado': 'enviado', 'intentos': 2}])

    def test_changing_advance_updates_pending_delivery_time(self):
        self.subscribed_fixture()
        self.favorite_fixture(minutes=10)
        sent = []
        sender = lambda *args: sent.append(args)
        avisos.run_notification_cycle(self.connection, sender=sender, now=NOW - timedelta(minutes=4))
        self.assertEqual(sent, [])
        self.query('UPDATE favoritos_recorridos SET anticipacion_min=15;')
        avisos.run_notification_cycle(self.connection, sender=sender, now=NOW - timedelta(minutes=3))
        self.assertEqual(len(sent), 1)

    def test_transient_retry_does_not_persist_private_endpoint_or_exception(self):
        identifier = self.subscribed_fixture()
        self.favorite_fixture()
        private_endpoint = self.query('SELECT endpoint FROM suscripciones_push WHERE id=%s;',
                                      (identifier,))[0]['endpoint']

        def failing_sender(*_):
            raise RuntimeError('Network failure to ' + private_endpoint)

        avisos.run_notification_cycle(self.connection, sender=failing_sender, now=NOW)
        job = self.query('SELECT estado, ultimo_error FROM avisos_salida WHERE salida_en=%s;', (DEPARTURE,))[0]
        self.assertEqual(job['estado'], 'pendiente')
        self.assertNotIn(private_endpoint, job['ultimo_error'])

    def test_retry_after_cannot_deliver_after_departure(self):
        self.subscribed_fixture()
        self.favorite_fixture()
        result = avisos.run_notification_cycle(self.connection,
            sender=lambda *_: (_ for _ in ()).throw(avisos.PushDeliveryError(status=429, retry_after='900')),
            now=NOW)
        self.assertEqual(result['enviado'], 0)
        self.assertEqual(self.query('SELECT estado FROM avisos_salida WHERE salida_en=%s;',
                                   (DEPARTURE,))[0]['estado'], 'expirado')
        sent = []
        avisos.run_notification_cycle(self.connection, sender=lambda *args: sent.append(args),
                                      now=NOW + timedelta(minutes=1))
        self.assertEqual(sent, [])
        self.assertEqual(self.query('SELECT estado FROM avisos_salida WHERE salida_en=%s;',
                                   (DEPARTURE,))[0]['estado'], 'expirado')

    def test_worker_does_not_send_after_departure_or_removed_favorite(self):
        self.subscribed_fixture()
        self.favorite_fixture()
        sent = []
        sender = lambda *args: sent.append(args)
        avisos.run_notification_cycle(self.connection, sender=sender, now=NOW - timedelta(minutes=1))
        self.query('DELETE FROM favoritos_recorridos;')
        avisos.run_notification_cycle(self.connection, sender=sender, now=NOW)
        self.assertEqual(sent, [])
        self.favorite_fixture()
        avisos.run_notification_cycle(self.connection, sender=sender, now=DEPARTURE + timedelta(seconds=1))
        self.assertEqual(sent, [])

    def test_parallel_workers_claim_job_once(self):
        self.subscribed_fixture()
        self.favorite_fixture()
        started = threading.Event()
        release = threading.Event()
        sent = []

        def sender(*args):
            sent.append(args)
            started.set()
            if not release.wait(5):
                raise AssertionError('No se liberó el emisor de prueba')

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(avisos.run_notification_cycle, self.connection, sender=sender, now=NOW)
            self.assertTrue(started.wait(5), 'El primer trabajador no reclamó el aviso')
            second = executor.submit(avisos.run_notification_cycle, self.connection, sender=sender, now=NOW)
            try:
                second.result(timeout=5)
            finally:
                release.set()
            first.result(timeout=5)
        self.assertEqual(len(sent), 1)
        jobs = self.query('SELECT estado FROM avisos_salida WHERE salida_en=%s;', (DEPARTURE,))
        self.assertEqual(jobs, [{'estado': 'enviado'}])

    def test_crashed_worker_lease_is_recovered_without_duplicate_job(self):
        self.subscribed_fixture()
        self.favorite_fixture()
        avisos.run_notification_cycle(self.connection, sender=lambda *_: None,
                                      now=NOW - timedelta(minutes=1))
        self.query('''UPDATE avisos_salida SET estado='enviando', intentos=1,
                      reclamo_token='crashed-worker-fixture', reclamo_hasta=%s
                      WHERE salida_en=%s;''', (NOW - timedelta(seconds=1), DEPARTURE))
        sent = []
        sender = lambda *args: sent.append(args)
        avisos.run_notification_cycle(self.connection, sender=sender, now=NOW)
        avisos.run_notification_cycle(self.connection, sender=sender, now=NOW + timedelta(seconds=20))
        self.assertEqual(len(sent), 1)
        jobs = self.query('SELECT estado, intentos, reclamo_token FROM avisos_salida WHERE salida_en=%s;',
                          (DEPARTURE,))
        self.assertEqual(jobs, [{'estado': 'enviado', 'intentos': 2, 'reclamo_token': None}])


if __name__ == '__main__':
    unittest.main()
