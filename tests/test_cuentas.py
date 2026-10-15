"""Cuentas sobre PostgreSQL local; los transportes de correo siempre se simulan."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import ssl
import unittest
from unittest.mock import MagicMock, patch

import psycopg2
from psycopg2.extensions import make_dsn, parse_dsn
from psycopg2.extras import RealDictCursor
from werkzeug.security import check_password_hash, generate_password_hash

import app as website
import cuentas


MAIL = {'BREVO_API_KEY': 'fixture-api-key-not-real', 'MAIL_FROM_EMAIL': 'bsred@example.invalid',
        'MAIL_FROM_NAME': 'BSRed fixture', 'PUBLIC_BASE_URL': 'https://bsred.onrender.com',
        'SMTP_HOST': '', 'SMTP_USER': '', 'SMTP_PASSWORD': '', 'SMTP_FROM': ''}
CURRENT = 'fixture-password'
NEW = 'nueva-clave-fixture-2026'


class MailTransportTests(unittest.TestCase):
    def test_brevo_uses_fixed_https_fragment_link_and_never_uses_request_host(self):
        with patch.dict(os.environ, MAIL):
            config = cuentas.mail_configuration()
        response = MagicMock()
        response.__enter__.return_value.status = 201
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(cuentas, 'build_opener', return_value=opener) as build:
            cuentas._deliver_reset_email('receiver@example.invalid', 'fixture-token', config)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.brevo.com/v3/smtp/email')
        self.assertEqual(req.get_header('Api-key'), MAIL['BREVO_API_KEY'])
        payload = json.loads(req.data)
        self.assertIn('https://bsred.onrender.com/usuario#restablecer=fixture-token', payload['textContent'])
        self.assertNotIn('?restablecer=', payload['textContent'])
        context = build.call_args.args[0]._context
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        redirect = build.call_args.args[1]
        self.assertIsNone(redirect.redirect_request(None, None, 302, '', {}, 'https://attacker.invalid'))

    def test_smtp_requires_verified_starttls_or_tls_before_login(self):
        config = {'host': 'smtp.example.invalid', 'port': 587, 'username': 'fixture-user',
                  'password': 'fixture-private-password', 'sender': 'BSRed <mail@example.invalid>',
                  'base_url': 'https://bsred.onrender.com'}
        with patch.object(cuentas.smtplib, 'SMTP') as smtp:
            cuentas._deliver_reset_email('receiver@example.invalid', 'fixture-token', config)
        server = smtp.return_value.__enter__.return_value
        calls = [call[0] for call in server.method_calls]
        self.assertLess(calls.index('starttls'), calls.index('login'))
        self.assertEqual(server.starttls.call_args.kwargs['context'].verify_mode, ssl.CERT_REQUIRED)
        with patch.object(cuentas.smtplib, 'SMTP_SSL') as smtp_ssl:
            cuentas._deliver_reset_email('receiver@example.invalid', 'fixture-token', {**config, 'port': 465})
        self.assertEqual(smtp_ssl.call_args.kwargs['context'].verify_mode, ssl.CERT_REQUIRED)

    def test_missing_provider_invalid_origin_and_insecure_smtp_are_unavailable(self):
        with patch.dict(os.environ, {**MAIL, 'BREVO_API_KEY': ''}):
            self.assertIsNone(cuentas.mail_configuration())
        with patch.dict(os.environ, {**MAIL, 'PUBLIC_BASE_URL': 'http://attacker.invalid'}):
            self.assertIsNone(cuentas.mail_configuration())
        with patch.dict(os.environ, {**MAIL, 'BREVO_API_KEY': '', 'SMTP_HOST': 'smtp.example.invalid',
                                    'SMTP_USER': 'fixture', 'SMTP_PASSWORD': 'fixture',
                                    'SMTP_FROM': 'mail@example.invalid', 'SMTP_PORT': '25'}):
            self.assertIsNone(cuentas.mail_configuration())


class AccountPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get('TEST_DATABASE_URL')
        if not url:
            raise RuntimeError('Configura TEST_DATABASE_URL para PostgreSQL local exclusivo')
        params = parse_dsn(url)
        if params.get('host') not in ('localhost', '127.0.0.1') or not params.get('dbname', '').endswith('_test'):
            raise RuntimeError('Las pruebas sólo admiten localhost y una base terminada en _test')
        cls.url = make_dsn(url, options='-c search_path=cuentas_test')
        cls.database = psycopg2.connect(cls.url, cursor_factory=RealDictCursor)
        cls.database.autocommit = True
        with cls.database.cursor() as cur:
            cur.execute('CREATE SCHEMA IF NOT EXISTS cuentas_test;')
            cur.execute('''CREATE TABLE IF NOT EXISTS empresas(id SERIAL PRIMARY KEY,nombre VARCHAR(100));
                CREATE TABLE IF NOT EXISTS usuarios(id SERIAL PRIMARY KEY,nombre VARCHAR(100),email VARCHAR(255) UNIQUE,
                    password VARCHAR(255),rol VARCHAR(50),gps_activo BOOLEAN DEFAULT FALSE);
                CREATE TABLE IF NOT EXISTS horarios(id SERIAL PRIMARY KEY,empresa_id INTEGER REFERENCES empresas(id),
                    tipo VARCHAR(30),origen VARCHAR(100),destino VARCHAR(100),salida TIME,llegada TIME,dias VARCHAR(100),anden VARCHAR(30));''')
            names = ['001_telemetria_web.sql', '002_favoritos_avisos.sql', '004_choferes_viajes.sql',
                     '005_operaciones.sql', '006_cuentas.sql', '007_notificaciones.sql']
            for name in names:
                cur.execute((Path(__file__).resolve().parents[1] / 'migrations' / name).read_text()
                            .replace('public.', 'cuentas_test.'))
            cur.execute((Path(__file__).resolve().parents[1] / 'migrations/006_cuentas.sql').read_text()
                        .replace('public.', 'cuentas_test.'))
        cls.previous_url = website.DATABASE_URL
        website.DATABASE_URL = cls.url
        website.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        website.DATABASE_URL = cls.previous_url
        cls.database.close()

    def query(self, sql, params=()):
        with self.database.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def setUp(self):
        environment = patch.dict(os.environ, MAIL)
        environment.start()
        self.addCleanup(environment.stop)
        self.query('''TRUNCATE empresas, usuarios, horarios, limites_cuentas RESTART IDENTITY CASCADE;
            INSERT INTO empresas(nombre,estado_validacion) VALUES('Empresa fixture','aprobada');
            INSERT INTO usuarios(nombre,email,password,rol,empresa_id,requiere_cambio_password) VALUES
                ('Pasajero','pasajero@example.invalid','fixture-password','pasajero',NULL,FALSE),
                ('Admin','admin@example.invalid','fixture-password','admin',NULL,FALSE),
                ('Empresa','empresa@example.invalid','fixture-password','empresa',1,FALSE),
                ('Chofer','chofer@example.invalid','fixture-password','chofer',1,TRUE),
                ('Suspendido','suspendido@example.invalid','fixture-password','chofer',1,FALSE);
            UPDATE usuarios SET suspendido=TRUE WHERE id=5;
            INSERT INTO horarios(empresa_id,tipo,origen,destino,salida,llegada,dias,anden)
            VALUES(1,'salida','Panguipulli','Valdivia','09:00','11:00','Diario','1');''')
        self.client = website.app.test_client()
        self.mail = []
        queue = patch.object(cuentas, '_queue_email', side_effect=lambda app, email, token, config: self.mail.append((email, token)))
        queue.start()
        self.addCleanup(queue.stop)

    def login(self, name='pasajero', client=None, password=CURRENT):
        client = client or self.client
        result = client.post('/api/login', json={'email': name + '@example.invalid', 'password': password})
        self.assertEqual(result.status_code, 200, result.json)
        return client

    def recover(self, email='pasajero@example.invalid', client=None):
        return (client or self.client).post('/api/cuentas/recuperar', json={'email': email})

    def reset(self, token, client=None, **kwargs):
        return (client or self.client).post('/api/cuentas/restablecer',
                                          json={'token': token, 'password': NEW}, **kwargs)

    def test_auth_required_and_legacy_change_rotates_cookie_without_reading_old_password(self):
        body = {'password_actual': CURRENT, 'password_nuevo': NEW}
        self.assertEqual(self.client.post('/api/cuentas/password', json=body).status_code, 401)
        self.login()
        second = self.login(client=website.app.test_client())
        old_cookie = self.client.get_cookie('bsred_sesion').value
        response = self.client.post('/api/cuentas/password', json=body)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertNotEqual(old_cookie, self.client.get_cookie('bsred_sesion').value)
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        row = self.query('SELECT password,password_hash FROM usuarios WHERE id=1;')[0]
        self.assertEqual(row['password'], '')
        self.assertTrue(check_password_hash(row['password_hash'], NEW))
        self.assertEqual(second.get('/api/sesion').status_code, 401)
        self.assertEqual(self.client.get('/api/sesion').status_code, 200)
        self.assertNotIn('password', response.json['user'])
        self.assertNotIn('password_hash', response.json['user'])

    def test_required_change_hash_accounts_and_all_roles_can_change(self):
        for name in ('admin', 'empresa', 'chofer'):
            user_id = {'admin': 2, 'empresa': 3, 'chofer': 4}[name]
            self.query("UPDATE usuarios SET password='',password_hash=%s WHERE id=%s;",
                       (generate_password_hash(CURRENT), user_id))
            client = self.login(name, website.app.test_client())
            result = client.post('/api/cuentas/password', json={'password_actual': CURRENT, 'password_nuevo': NEW})
            self.assertEqual(result.status_code, 200, result.json)
            self.assertFalse(result.json['user']['requiere_cambio_password'])
            self.assertEqual(result.json['user']['rol'], name if name != 'chofer' else 'chofer')
        self.assertFalse(self.query('SELECT requiere_cambio_password FROM usuarios WHERE id=4;')[0]['requiere_cambio_password'])

    def test_wrong_current_password_reuse_invalid_inputs_and_csrf_do_not_change_credentials(self):
        self.login()
        for body in ({'password_actual': 'incorrecta', 'password_nuevo': NEW},
                     {'password_actual': CURRENT, 'password_nuevo': 'short'},
                     {'password_actual': CURRENT, 'password_nuevo': CURRENT},
                     {'password_actual': ' ' + CURRENT + ' ', 'password_nuevo': CURRENT}, [], None):
            self.assertEqual(self.client.post('/api/cuentas/password', json=body).status_code, 400)
        result = self.client.post('/api/cuentas/password', json={'password_actual': CURRENT, 'password_nuevo': NEW},
                                  headers={'Origin': 'https://attacker.invalid'})
        self.assertEqual(result.status_code, 403)
        self.assertIsNone(self.query('SELECT password_hash FROM usuarios WHERE id=1;')[0]['password_hash'])

    def test_recovery_is_generic_and_stores_only_hash_with_thirty_minute_expiry(self):
        existing = self.recover()
        unknown = self.recover('unknown@example.invalid')
        suspended = self.recover('suspendido@example.invalid')
        self.assertEqual(existing.status_code, 202)
        self.assertEqual(existing.json, unknown.json)
        self.assertEqual(existing.json, suspended.json)
        self.assertEqual(len(self.mail), 1)
        token = self.mail[0][1]
        self.assertNotIn(token, existing.get_data(as_text=True))
        rows = self.query('SELECT token_hash,usuario_id,expira_en-creado_en AS lifetime FROM password_reset_tokens;')
        self.assertEqual(rows[0]['token_hash'].strip(), hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(rows[0]['lifetime'].total_seconds(), 1800)
        self.assertEqual(rows[0]['usuario_id'], 1)

    def test_unconfigured_mail_is_honest_and_creates_no_tokens(self):
        with patch.dict(os.environ, {'BREVO_API_KEY': ''}):
            self.assertFalse(self.client.get('/api/cuentas/config').json['recuperacion_disponible'])
            self.assertEqual(self.recover().status_code, 503)
        self.assertEqual(self.query('SELECT * FROM password_reset_tokens;'), [])
        self.assertEqual(self.mail, [])

    def test_recovery_quota_persists_and_contains_no_raw_email_or_ip(self):
        for _ in range(5):
            self.assertEqual(self.recover().status_code, 202)
        self.assertEqual(len(self.mail), 3)
        rows = self.query('SELECT * FROM limites_cuentas;')
        self.assertEqual(len(rows), 2)
        serialized = str(rows)
        self.assertNotIn('pasajero@example.invalid', serialized)
        self.assertNotIn('127.0.0.1', serialized)
        another = website.app.test_client()
        self.assertEqual(self.recover(client=another).status_code, 202)
        self.assertEqual(len(self.mail), 3)

    def test_reset_single_use_new_link_invalidates_old_and_expiry_is_enforced(self):
        self.recover()
        first = self.mail[-1][1]
        self.recover()
        second = self.mail[-1][1]
        self.assertEqual(self.reset(first).status_code, 400)
        self.query("UPDATE password_reset_tokens SET expira_en=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE token_hash=%s;",
                   (hashlib.sha256(second.encode()).hexdigest(),))
        self.assertEqual(self.reset(second).status_code, 400)
        self.recover()
        third = self.mail[-1][1]
        self.assertEqual(self.reset(third).status_code, 200)
        self.assertEqual(self.reset(third).status_code, 400)
        self.login(password=NEW)

    def test_change_revokes_web_and_android_devices_and_pending_deliveries(self):
        self.login()
        self.query('''INSERT INTO suscripciones_push(usuario_id,endpoint,endpoint_hash,p256dh,auth,dispositivo_hash)
            VALUES(1,'https://fcm.googleapis.com/fcm/send/fixture',repeat('a',64),'fixture','fixture',repeat('b',64));
            INSERT INTO dispositivos_android(usuario_id,dispositivo_hash,token) VALUES(1,repeat('c',64),'fixture-fcm');
            INSERT INTO entregas_notificaciones(canal,dispositivo_android_id,usuario_id,horario_id,event_id,
                expira_en,payload,disponible_en,reintentar_en) VALUES('android',1,1,1,'fixture-event',
                CURRENT_TIMESTAMP+INTERVAL '1 hour','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);''')
        response = self.client.post('/api/cuentas/password', json={'password_actual': CURRENT, 'password_nuevo': NEW})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(self.query('SELECT activa FROM suscripciones_push;')[0]['activa'])
        self.assertFalse(self.query('SELECT activo FROM dispositivos_android;')[0]['activo'])
        self.assertEqual(self.query('SELECT estado FROM entregas_notificaciones;')[0]['estado'], 'expirado')

    def test_change_requires_trip_finish_but_recovery_cancels_gps_and_revokes_sessions(self):
        self.query('UPDATE usuarios SET requiere_cambio_password=FALSE WHERE id=4;')
        self.login('chofer')
        self.query('''INSERT INTO viajes(empresa_id,chofer_id,horario_id,patente,rastreo_hash,latitud,longitud,ubicacion_en)
            VALUES(1,4,1,'ABCD12',repeat('d',64),-39.64,-72.33,CURRENT_TIMESTAMP);''')
        response = self.client.post('/api/cuentas/password', json={'password_actual': CURRENT, 'password_nuevo': NEW})
        self.assertEqual(response.status_code, 409)
        self.recover('chofer@example.invalid')
        self.assertEqual(self.reset(self.mail[-1][1]).status_code, 200)
        trip = self.query('SELECT estado,rastreo_hash,latitud,longitud FROM viajes;')[0]
        self.assertEqual(trip, {'estado': 'cancelado', 'rastreo_hash': None, 'latitud': None, 'longitud': None})
        self.assertEqual(self.client.get('/api/sesion').status_code, 401)

    def test_concurrent_token_consumers_succeed_only_once(self):
        self.recover()
        token = self.mail[-1][1]
        clients = [website.app.test_client(), website.app.test_client()]
        def consume(item):
            i, client = item
            return self.reset(token, client, environ_overrides={'REMOTE_ADDR': f'127.0.0.{i + 2}'}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(consume, enumerate(clients)))
        self.assertEqual(sorted(statuses), [200, 400])
        self.assertEqual(len(self.query('SELECT * FROM password_reset_tokens WHERE usado_en IS NOT NULL;')), 1)

    def test_suspended_account_cannot_consume_reset_token(self):
        self.recover()
        self.query('UPDATE usuarios SET suspendido=TRUE WHERE id=1;')
        self.assertEqual(self.reset(self.mail[-1][1]).status_code, 400)


if __name__ == '__main__':
    unittest.main()
