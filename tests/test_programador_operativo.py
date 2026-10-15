"""Programador operativo real de PostgreSQL con transporte HTTP/Vault aislados."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unittest

import psycopg2
from psycopg2.extensions import parse_dsn
from psycopg2.extras import RealDictCursor, Json


BASE = datetime(2026, 10, 15, 11, 48, tzinfo=timezone.utc)  # Jueves 08:48 en Chile.
DEPARTURE = BASE + timedelta(minutes=12)
SECRET = 'cron-operativo-fixture-not-a-real-private-token'


class OperationalCronPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get('TEST_DATABASE_URL')
        if not url:
            raise RuntimeError('Configura TEST_DATABASE_URL para PostgreSQL local exclusivo')
        params = parse_dsn(url)
        if params.get('host') not in ('localhost', '127.0.0.1') or not params.get('dbname', '').endswith('_test'):
            raise RuntimeError('Las pruebas sólo admiten localhost y una base terminada en _test')
        cls.database = psycopg2.connect(url, cursor_factory=RealDictCursor)
        cls.database.autocommit = True
        with cls.database.cursor() as cur:
            cur.execute('''CREATE SCHEMA IF NOT EXISTS cron_operativo_test;
                CREATE SCHEMA IF NOT EXISTS cron_operativo_net_test;
                CREATE SCHEMA IF NOT EXISTS cron_operativo_vault_test;
                CREATE TABLE IF NOT EXISTS cron_operativo_test.empresas(id SERIAL PRIMARY KEY,nombre VARCHAR(100));
                CREATE TABLE IF NOT EXISTS cron_operativo_test.usuarios(id SERIAL PRIMARY KEY,nombre VARCHAR(100),
                    email VARCHAR(255) UNIQUE,password VARCHAR(255),rol VARCHAR(50),gps_activo BOOLEAN DEFAULT FALSE);
                CREATE TABLE IF NOT EXISTS cron_operativo_test.horarios(id SERIAL PRIMARY KEY,
                    empresa_id INTEGER REFERENCES cron_operativo_test.empresas(id),tipo VARCHAR(30),
                    origen VARCHAR(100),destino VARCHAR(100),salida TIME,llegada TIME,dias VARCHAR(100),anden VARCHAR(30));
                CREATE TABLE IF NOT EXISTS cron_operativo_net_test.requests(id BIGSERIAL PRIMARY KEY,
                    url TEXT,headers JSONB,body JSONB,timeout_milliseconds INTEGER);
                CREATE TABLE IF NOT EXISTS cron_operativo_vault_test.decrypted_secrets(name TEXT PRIMARY KEY,decrypted_secret TEXT);
                CREATE OR REPLACE FUNCTION cron_operativo_net_test.http_post(
                    url TEXT,headers JSONB,body JSONB,timeout_milliseconds INTEGER
                ) RETURNS BIGINT LANGUAGE sql AS $fixture$
                    INSERT INTO cron_operativo_net_test.requests(url,headers,body,timeout_milliseconds)
                    VALUES($1,$2,$3,$4) RETURNING id;
                $fixture$;
            ''')
            migrations = Path(__file__).resolve().parents[1] / 'migrations'
            for name in ['001_telemetria_web.sql', '002_favoritos_avisos.sql', '004_choferes_viajes.sql',
                         '005_operaciones.sql', '006_cuentas.sql', '007_notificaciones.sql']:
                cur.execute((migrations / name).read_text().replace('public.', 'cron_operativo_test.'))
            gate = (migrations / '008_programador_operativo.sql').read_text()
            gate = re.sub(r'^REVOKE[^;]+FROM anon, authenticated;\s*', '', gate, flags=re.MULTILINE)
            gate = (gate.replace('public.', 'cron_operativo_test.')
                    .replace('vault.decrypted_secrets', 'cron_operativo_vault_test.decrypted_secrets')
                    .replace('net.http_post', 'cron_operativo_net_test.http_post'))
            cur.execute(gate)
            cur.execute(gate)  # Repetir la migración conserva firma y permisos.

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def query(self, sql, params=()):
        with self.database.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def setUp(self):
        self.query('''TRUNCATE cron_operativo_test.empresas,cron_operativo_test.usuarios,
            cron_operativo_test.horarios,cron_operativo_net_test.requests,
            cron_operativo_vault_test.decrypted_secrets RESTART IDENTITY CASCADE;
            INSERT INTO cron_operativo_test.empresas(nombre,estado_validacion)
                VALUES('Aprobada','aprobada'),('Pendiente','pendiente');
            INSERT INTO cron_operativo_test.usuarios(nombre,email,password,rol)
                VALUES('Pasajero','passenger@example.invalid','fixture','pasajero'),
                      ('Otra cuenta','other@example.invalid','fixture','pasajero');
            INSERT INTO cron_operativo_test.horarios(empresa_id,tipo,origen,destino,salida,llegada,dias)
                VALUES(1,'salida','Panguipulli','Valdivia','09:00','11:00','Diario');
            INSERT INTO cron_operativo_vault_test.decrypted_secrets VALUES('bsred_notification_cron_token',%s);''', (SECRET,))

    def device(self, channel='android'):
        self.query('''INSERT INTO cron_operativo_test.favoritos_recorridos(usuario_id,horario_id)
                      VALUES(1,1) ON CONFLICT DO NOTHING;''')
        if channel == 'android':
            return self.query('''INSERT INTO cron_operativo_test.dispositivos_android
                (usuario_id,dispositivo_hash,token,vinculo_id)
                VALUES(1,%s,'fixture-fcm','fixture-binding') RETURNING id;''',
                (hashlib.sha256(b'android-fixture').hexdigest(),))[0]['id']
        return self.query('''INSERT INTO cron_operativo_test.suscripciones_push
            (usuario_id,endpoint,endpoint_hash,p256dh,auth,dispositivo_hash)
            VALUES(1,'https://fcm.googleapis.com/fcm/send/not-real',%s,'fixture','fixture',%s) RETURNING id;''',
            (hashlib.sha256(b'web-fixture').hexdigest(), hashlib.sha256(b'web-device').hexdigest()))[0]['id']

    def notice(self, **overrides):
        values = {'company': 1, 'day': BASE.date(), 'created': BASE - timedelta(minutes=1),
                  'expiry': BASE + timedelta(hours=2), **overrides}
        return self.query('''INSERT INTO cron_operativo_test.avisos_operativos
            (empresa_id,horario_id,tipo,fecha,mensaje,creado_en,expira_en)
            VALUES(%s,1,'cancelacion',%s,'Aviso fixture de cancelación',%s,%s) RETURNING id;''',
            (values['company'], values['day'], values['created'], values['expiry']))[0]['id']

    def queued(self, device_id=1, channel='android', state='pendiente', notice_id=None,
               available=None, retry=None, lease=None, payload=None, owner=1, attempts=0):
        event = f'operativo:{notice_id}' if notice_id is not None else f'salida:1:{int(DEPARTURE.timestamp())}'
        return self.query('''INSERT INTO cron_operativo_test.entregas_notificaciones
            (canal,suscripcion_id,dispositivo_android_id,usuario_id,horario_id,aviso_id,event_id,salida_en,
             expira_en,payload,disponible_en,reintentar_en,estado,reclamo_hasta,intentos)
            VALUES(%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id;''',
            (channel, device_id if channel == 'web' else None, device_id if channel == 'android' else None,
             owner, notice_id, event, None if notice_id is not None else DEPARTURE,
             BASE + timedelta(hours=2) if notice_id is not None else DEPARTURE, Json(payload or {}),
             available or BASE, retry or BASE, state, lease, attempts))[0]['id']

    def dispatch(self, now=BASE):
        return self.query('SELECT cron_operativo_test.bsred_disparar_avisos_salida(%s) AS id;', (now,))[0]['id']

    def calls(self):
        return self.query('SELECT * FROM cron_operativo_net_test.requests;')

    def block_departure(self):
        self.query("UPDATE cron_operativo_test.horarios SET salida='18:00';")

    def test_no_recipients_never_wakes_render_or_reads_vault(self):
        self.notice()
        self.query('DELETE FROM cron_operativo_vault_test.decrypted_secrets;')
        self.assertIsNone(self.dispatch())
        self.assertEqual(self.calls(), [])

    def test_native_only_and_web_only_use_same_prewarm_fixed_url_and_private_header(self):
        self.device()
        self.assertIsNone(self.dispatch(BASE - timedelta(seconds=1)))
        self.assertIsNotNone(self.dispatch())
        call = self.calls()[0]
        self.assertEqual(call['url'], 'https://bsred.onrender.com/api/interno/avisos-salida')
        self.assertEqual(call['headers'], {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + SECRET})
        self.assertEqual(call['timeout_milliseconds'], 10000)
        self.assertEqual(call['body'], {})
        self.query('UPDATE cron_operativo_test.dispositivos_android SET activo=FALSE;')
        self.assertIsNone(self.dispatch())
        self.device('web')
        self.assertIsNotNone(self.dispatch())

    def test_cancelled_date_and_time_override_replace_normal_service(self):
        self.device()
        self.query('''INSERT INTO cron_operativo_test.horario_excepciones(horario_id,fecha,opera)
                      VALUES(1,%s,FALSE);''', (BASE.date(),))
        self.assertIsNone(self.dispatch())
        self.query("UPDATE cron_operativo_test.horario_excepciones SET opera=TRUE,salida='10:00';")
        self.assertIsNone(self.dispatch())
        self.assertIsNotNone(self.dispatch(BASE + timedelta(hours=1)))

    def test_explicit_extra_service_overrides_invalid_or_nonoperating_calendar(self):
        self.device()
        self.query("UPDATE cron_operativo_test.horarios SET dias='Domingo',salida=NULL;")
        self.assertIsNone(self.dispatch())
        self.query('''INSERT INTO cron_operativo_test.horario_excepciones(horario_id,fecha,opera,salida)
                      VALUES(1,%s,TRUE,'09:00');''', (BASE.date(),))
        self.assertIsNotNone(self.dispatch())

    def test_unapproved_inactive_suspended_and_required_password_recipients_do_not_wake(self):
        self.device()
        changes = [
            ("UPDATE cron_operativo_test.empresas SET estado_validacion='pendiente' WHERE id=1;",
             "UPDATE cron_operativo_test.empresas SET estado_validacion='aprobada' WHERE id=1;"),
            ('UPDATE cron_operativo_test.horarios SET activo=FALSE;', 'UPDATE cron_operativo_test.horarios SET activo=TRUE;'),
            ('UPDATE cron_operativo_test.usuarios SET suspendido=TRUE WHERE id=1;',
             'UPDATE cron_operativo_test.usuarios SET suspendido=FALSE WHERE id=1;'),
            ('UPDATE cron_operativo_test.usuarios SET requiere_cambio_password=TRUE WHERE id=1;',
             'UPDATE cron_operativo_test.usuarios SET requiere_cambio_password=FALSE WHERE id=1;'),
        ]
        for change, restore in changes:
            self.query(change)
            self.assertIsNone(self.dispatch())
            self.query(restore)
        self.assertEqual(self.calls(), [])

    def test_native_terminal_retry_or_lease_job_prevents_repeated_wakeup(self):
        self.device()
        identifier = self.queued(state='enviado')
        for state in ('enviado', 'error'):
            self.query('UPDATE cron_operativo_test.entregas_notificaciones SET estado=%s;', (state,))
            self.assertIsNone(self.dispatch())
        self.query("UPDATE cron_operativo_test.entregas_notificaciones SET estado='pendiente',intentos=1,reintentar_en=%s;",
                   (BASE + timedelta(minutes=3),))
        self.assertIsNone(self.dispatch())
        self.query("UPDATE cron_operativo_test.entregas_notificaciones SET estado='enviando',reclamo_hasta=%s;",
                   (BASE + timedelta(seconds=90),))
        self.assertIsNone(self.dispatch())
        self.query('UPDATE cron_operativo_test.entregas_notificaciones SET reclamo_hasta=%s WHERE id=%s;',
                   (BASE - timedelta(seconds=1), identifier))
        self.assertIsNotNone(self.dispatch())

    def test_web_terminal_and_expired_retry_after_departure_prevent_wakeup(self):
        self.device('web')
        self.query('''INSERT INTO cron_operativo_test.avisos_salida
            (suscripcion_id,horario_id,salida_en,avisar_en,estado)
            VALUES(1,1,%s,%s,'enviado');''', (DEPARTURE, BASE))
        self.assertIsNone(self.dispatch())
        self.query("UPDATE cron_operativo_test.avisos_salida SET estado='expirado',reintentar_en=%s,intentos=1;",
                   (DEPARTURE + timedelta(minutes=1),))
        self.assertIsNone(self.dispatch())

    def test_raw_notice_bootstraps_missing_delivery_without_waiting_for_departure(self):
        self.device()
        self.block_departure()
        notice = self.notice()
        self.assertIsNotNone(self.dispatch())
        self.queued(notice_id=notice, state='enviado')
        self.assertIsNone(self.dispatch())

    def test_operational_queue_only_wakes_for_due_or_expired_lease(self):
        self.device('web')
        self.block_departure()
        notice = self.notice()
        identifier = self.queued(channel='web', notice_id=notice, retry=BASE + timedelta(minutes=1))
        self.assertIsNone(self.dispatch())
        self.query('UPDATE cron_operativo_test.entregas_notificaciones SET reintentar_en=%s;', (BASE,))
        self.assertIsNotNone(self.dispatch())
        self.query("UPDATE cron_operativo_test.entregas_notificaciones SET estado='enviando',reclamo_hasta=%s;",
                   (BASE + timedelta(seconds=30),))
        self.assertIsNone(self.dispatch())
        self.query('UPDATE cron_operativo_test.entregas_notificaciones SET reclamo_hasta=%s WHERE id=%s;',
                   (BASE - timedelta(seconds=1), identifier))
        self.assertIsNotNone(self.dispatch())

    def test_future_notice_expired_notice_wrong_company_and_wrong_binding_do_not_wake(self):
        self.device()
        self.block_departure()
        self.notice(day=BASE.date() + timedelta(days=1))
        self.assertIsNone(self.dispatch())
        self.query('DELETE FROM cron_operativo_test.avisos_operativos;')
        self.notice(created=BASE - timedelta(hours=3), expiry=BASE - timedelta(minutes=1))
        self.assertIsNone(self.dispatch())
        self.query('DELETE FROM cron_operativo_test.avisos_operativos;')
        self.notice(company=2)
        self.assertIsNone(self.dispatch())
        self.query('DELETE FROM cron_operativo_test.avisos_operativos;')
        notice = self.notice()
        self.queued(notice_id=notice, payload={'binding_id': 'previous-binding'})
        self.assertIsNone(self.dispatch())

    def test_queue_owner_must_still_own_active_device(self):
        self.device()
        self.block_departure()
        notice = self.notice()
        self.queued(notice_id=notice, owner=2)
        self.assertIsNone(self.dispatch())

    def test_chile_midnight_and_both_dst_transitions_match_dispatcher(self):
        self.device()
        self.query("UPDATE cron_operativo_test.horarios SET salida='00:05',dias='Viernes';")
        self.assertIsNotNone(self.dispatch(datetime(2026, 10, 16, 2, 53, tzinfo=timezone.utc)))
        self.query("UPDATE cron_operativo_test.horarios SET salida='00:30',dias='Diario';")
        self.assertIsNone(self.dispatch(datetime(2026, 9, 6, 4, 18, tzinfo=timezone.utc)))
        self.query("UPDATE cron_operativo_test.horarios SET salida='23:30',dias='Sábado';")
        self.assertIsNotNone(self.dispatch(datetime(2026, 4, 5, 2, 18, tzinfo=timezone.utc)))
        self.assertIsNone(self.dispatch(datetime(2026, 4, 5, 3, 18, tzinfo=timezone.utc)))

    def test_function_is_private_definer_with_empty_search_path_and_no_embedded_secret(self):
        row = self.query('''SELECT p.prosecdef,p.proconfig,pg_get_functiondef(p.oid) AS definition,
            EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
                   WHERE x.grantee=0 AND x.privilege_type='EXECUTE') AS public_execute
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='cron_operativo_test' AND p.proname='bsred_disparar_avisos_salida';''')[0]
        self.assertTrue(row['prosecdef'])
        self.assertIn('search_path=""', row['proconfig'])
        self.assertFalse(row['public_execute'])
        self.assertNotIn(SECRET, row['definition'])


if __name__ == '__main__':
    unittest.main()
