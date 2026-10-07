"""Gate SQL real de Supabase, con Vault y transporte HTTP locales de prueba."""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import unittest

import psycopg2
from psycopg2.extensions import parse_dsn
from psycopg2.extras import RealDictCursor


BASE = datetime(2026, 10, 7, 11, 48, tzinfo=timezone.utc)  # Miércoles 08:48 en Chile.
SECRET = 'bsred-fixture-cron-secret-not-used-outside-tests'


class NotificationCronPostgresTests(unittest.TestCase):
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
            cur.execute('''
                CREATE SCHEMA IF NOT EXISTS cron_favoritos_test;
                CREATE SCHEMA IF NOT EXISTS cron_favoritos_net_test;
                CREATE SCHEMA IF NOT EXISTS cron_favoritos_vault_test;
                CREATE TABLE IF NOT EXISTS cron_favoritos_test.usuarios (id INTEGER PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS cron_favoritos_test.horarios (
                    id INTEGER PRIMARY KEY, tipo TEXT, origen TEXT, destino TEXT, salida TIME, dias TEXT
                );
                CREATE TABLE IF NOT EXISTS cron_favoritos_net_test.requests (
                    id BIGSERIAL PRIMARY KEY, url TEXT, headers JSONB, body JSONB, timeout_milliseconds INTEGER
                );
                CREATE TABLE IF NOT EXISTS cron_favoritos_vault_test.decrypted_secrets (
                    name TEXT PRIMARY KEY, decrypted_secret TEXT
                );
                CREATE OR REPLACE FUNCTION cron_favoritos_net_test.http_post(
                    url TEXT, headers JSONB, body JSONB, timeout_milliseconds INTEGER
                ) RETURNS BIGINT LANGUAGE sql AS $fixture$
                    INSERT INTO cron_favoritos_net_test.requests(url, headers, body, timeout_milliseconds)
                    VALUES ($1, $2, $3, $4) RETURNING id;
                $fixture$;
            ''')
            migrations = Path(__file__).resolve().parents[1] / 'migrations'
            cur.execute((migrations / '002_favoritos_avisos.sql').read_text()
                        .replace('public.', 'cron_favoritos_test.'))
            gate = (migrations / '003_programador_avisos.sql').read_text()
            gate = re.sub(r'^CREATE EXTENSION[^;]+;\s*', '', gate, flags=re.MULTILINE)
            gate = re.sub(r'^REVOKE[^;]+FROM anon, authenticated;\s*', '', gate, flags=re.MULTILINE)
            gate = re.sub(r'^SELECT cron\.schedule\([\s\S]+?;\s*$', '', gate, flags=re.MULTILINE)
            gate = (gate.replace('public.', 'cron_favoritos_test.')
                    .replace('vault.decrypted_secrets', 'cron_favoritos_vault_test.decrypted_secrets')
                    .replace('net.http_post', 'cron_favoritos_net_test.http_post'))
            cur.execute(gate)  # Compila la función PL/pgSQL real, sin emular su lógica.

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def query(self, sql, params=()):
        with self.database.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def setUp(self):
        self.query('''TRUNCATE cron_favoritos_test.avisos_salida,
                      cron_favoritos_test.suscripciones_push, cron_favoritos_test.favoritos_recorridos,
                      cron_favoritos_test.horarios, cron_favoritos_test.usuarios,
                      cron_favoritos_net_test.requests, cron_favoritos_vault_test.decrypted_secrets
                      RESTART IDENTITY CASCADE;''')
        self.query('''INSERT INTO cron_favoritos_vault_test.decrypted_secrets VALUES
                      ('bsred_notification_cron_token', %s);
                      INSERT INTO cron_favoritos_test.usuarios VALUES (1);
                      INSERT INTO cron_favoritos_test.horarios VALUES
                      (1, 'salida', 'Panguipulli', 'Valdivia', '09:00', 'Diario');''', (SECRET,))

    def selected_fixture(self):
        self.query('''
            INSERT INTO cron_favoritos_test.favoritos_recorridos (usuario_id, horario_id, anticipacion_min)
            VALUES (1, 1, 10);
            INSERT INTO cron_favoritos_test.suscripciones_push
                (usuario_id, endpoint, endpoint_hash, p256dh, auth, dispositivo_hash)
            VALUES (1, 'https://fcm.googleapis.com/fcm/send/not-a-real-device',
                    repeat('a', 64), 'no-network-fixture-public-key', 'no-network-fixture-auth', repeat('b', 64));
        ''')

    def dispatch(self, instant=BASE):
        result = self.query('SELECT cron_favoritos_test.bsred_disparar_avisos_salida(%s) AS request_id;', (instant,))
        return result[0]['request_id']

    def calls(self):
        return self.query('SELECT url, headers, body, timeout_milliseconds FROM cron_favoritos_net_test.requests;')

    def test_no_favorites_or_inactive_device_do_not_wake_render(self):
        self.assertIsNone(self.dispatch())
        self.selected_fixture()
        self.query('UPDATE cron_favoritos_test.suscripciones_push SET activa=FALSE;')
        self.assertIsNone(self.dispatch())
        self.assertEqual(self.calls(), [])

    def test_two_minute_prewarm_uses_fixed_url_private_token_and_timeout(self):
        self.selected_fixture()
        early = datetime(2026, 10, 7, 11, 47, 59, tzinfo=timezone.utc)
        self.assertIsNone(self.dispatch(early))
        self.assertEqual(self.dispatch(), 1)
        self.assertEqual(self.calls(), [{
            'url': 'https://bsred.onrender.com/api/interno/avisos-salida',
            'headers': {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + SECRET},
            'body': {}, 'timeout_milliseconds': 60000,
        }])

    def test_arrivals_and_other_origins_do_not_wake_render(self):
        self.selected_fixture()
        self.query("UPDATE cron_favoritos_test.horarios SET tipo='llegada';")
        self.assertIsNone(self.dispatch())
        self.query("UPDATE cron_favoritos_test.horarios SET tipo='salida', origen='Valdivia';")
        self.assertIsNone(self.dispatch())
        self.assertEqual(self.calls(), [])

    def test_invalid_days_and_missing_time_do_not_wake_render(self):
        self.selected_fixture()
        for days in ('Cuando haya pasajeros', 'lunes, nonsense', '', None):
            self.query('UPDATE cron_favoritos_test.horarios SET dias=%s;', (days,))
            self.assertIsNone(self.dispatch())
        self.query("UPDATE cron_favoritos_test.horarios SET dias='Diario', salida=NULL;")
        self.assertIsNone(self.dispatch())
        self.assertEqual(self.calls(), [])

    def test_weekday_range_accented_list_and_wrapping_range_match_operating_days(self):
        self.selected_fixture()
        for days in ('Lunes a Sábado', 'Miércoles, viernes y sábado'):
            with self.subTest(days=days):
                self.query('UPDATE cron_favoritos_test.horarios SET dias=%s;', (days,))
                self.assertIsNotNone(self.dispatch())
                thursday = datetime(2026, 10, 8, 11, 48, tzinfo=timezone.utc)
                if ',' in days:
                    self.assertIsNone(self.dispatch(thursday))
        self.query("UPDATE cron_favoritos_test.horarios SET dias='Viernes a Lunes';")
        self.assertIsNone(self.dispatch())  # Miércoles está fuera del rango circular.
        sunday = datetime(2026, 10, 11, 11, 48, tzinfo=timezone.utc)
        self.assertIsNotNone(self.dispatch(sunday))

    def test_utc_midnight_keeps_chile_departure_weekday(self):
        self.selected_fixture()
        self.query("UPDATE cron_favoritos_test.horarios SET dias='Miércoles', salida='23:30';")
        wednesday_in_chile = datetime(2026, 10, 8, 2, 18, tzinfo=timezone.utc)
        self.assertIsNotNone(self.dispatch(wednesday_in_chile))
        self.query("UPDATE cron_favoritos_test.horarios SET dias='Jueves';")
        self.assertIsNone(self.dispatch(wednesday_in_chile))

    def test_next_chile_day_departure_can_prewarm_before_local_midnight(self):
        self.selected_fixture()
        self.query("UPDATE cron_favoritos_test.horarios SET dias='Jueves', salida='00:05';")
        before_midnight = datetime(2026, 10, 8, 2, 53, tzinfo=timezone.utc)
        self.assertIsNotNone(self.dispatch(before_midnight))

    def test_departure_boundary_and_departed_service_never_wake_render(self):
        self.selected_fixture()
        for instant in (datetime(2026, 10, 7, 12, tzinfo=timezone.utc),
                        datetime(2026, 10, 7, 12, 1, tzinfo=timezone.utc)):
            with self.subTest(instant=instant):
                self.assertIsNone(self.dispatch(instant))
        self.assertEqual(self.calls(), [])

    def test_nonexistent_dst_hour_does_not_create_a_shifted_departure(self):
        self.selected_fixture()
        self.query("UPDATE cron_favoritos_test.horarios SET salida='00:30';")
        impossible_departure_window = datetime(2026, 9, 6, 4, 18, tzinfo=timezone.utc)
        self.assertIsNone(self.dispatch(impossible_departure_window))
        self.assertEqual(self.calls(), [])

    def test_repeated_dst_hour_uses_first_departure_and_does_not_repeat_later(self):
        self.selected_fixture()
        self.query("UPDATE cron_favoritos_test.horarios SET dias='Sábado', salida='23:30';")
        first_window = datetime(2026, 4, 5, 2, 18, tzinfo=timezone.utc)
        self.assertIsNotNone(self.dispatch(first_window))
        later_repeated_window = datetime(2026, 4, 5, 3, 18, tzinfo=timezone.utc)
        self.assertIsNone(self.dispatch(later_repeated_window))
        departure = datetime(2026, 4, 5, 2, 30, tzinfo=timezone.utc)
        delivered = datetime(2026, 4, 5, 2, 20, tzinfo=timezone.utc)
        self.query('''INSERT INTO cron_favoritos_test.avisos_salida
                      (suscripcion_id, horario_id, salida_en, avisar_en, estado, enviado_en)
                      VALUES (1, 1, %s, %s, 'enviado', %s);''', (departure, delivered, delivered))
        self.assertIsNone(self.dispatch(datetime(2026, 4, 5, 2, 21, tzinfo=timezone.utc)))
        self.assertEqual(len(self.calls()), 1)

    def test_already_delivered_or_permanent_failure_avoids_http_call(self):
        self.selected_fixture()
        departure = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
        self.query('''INSERT INTO cron_favoritos_test.avisos_salida
                      (suscripcion_id, horario_id, salida_en, avisar_en, estado, enviado_en)
                      VALUES (1, 1, %s, %s, 'enviado', %s);''', (departure, BASE, BASE))
        self.assertIsNone(self.dispatch())
        self.query("UPDATE cron_favoritos_test.avisos_salida SET estado='error', enviado_en=NULL;")
        self.assertIsNone(self.dispatch())
        self.assertEqual(self.calls(), [])

    def test_missing_vault_secret_errors_only_when_delivery_is_due(self):
        self.query('DELETE FROM cron_favoritos_vault_test.decrypted_secrets;')
        self.assertIsNone(self.dispatch())
        self.selected_fixture()
        with self.assertRaises(psycopg2.Error):
            self.dispatch()
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
