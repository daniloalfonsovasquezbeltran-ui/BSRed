"""Gestión empresarial con PostgreSQL real, exclusivamente local y aislado."""
from datetime import date, time
import os
from pathlib import Path
import unittest

from flask import Flask
import psycopg2
from psycopg2.extensions import make_dsn, parse_dsn
from psycopg2.extras import RealDictCursor

import app as website
from auditoria import record_audit
from operaciones import operation_day, register_operations


class OperationsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ['TEST_DATABASE_URL']
        params = parse_dsn(url)
        if params.get('host') not in ('localhost', '127.0.0.1') or not params.get('dbname', '').endswith('_test'):
            raise RuntimeError('Estas pruebas requieren PostgreSQL local y base terminada en _test')
        cls.url = make_dsn(url, options='-c search_path=operaciones_test')
        cls.database = psycopg2.connect(cls.url, cursor_factory=RealDictCursor)
        cls.database.autocommit = True
        with cls.database.cursor() as cur:
            cur.execute('DROP SCHEMA IF EXISTS operaciones_test CASCADE;CREATE SCHEMA operaciones_test;')
            cur.execute('''CREATE TABLE empresas(id SERIAL PRIMARY KEY,nombre VARCHAR(100) NOT NULL);
                CREATE TABLE usuarios(id SERIAL PRIMARY KEY,nombre VARCHAR(100),email VARCHAR(255) UNIQUE,
                    password VARCHAR(255),rol VARCHAR(50),gps_activo BOOLEAN DEFAULT FALSE);
                CREATE TABLE horarios(id SERIAL PRIMARY KEY,empresa_id INTEGER REFERENCES empresas(id),tipo VARCHAR(30),
                    origen VARCHAR(100),destino VARCHAR(100),salida TIME,llegada TIME,dias VARCHAR(100),anden VARCHAR(30));
                INSERT INTO empresas(nombre) VALUES('Empresa anterior');''')
            root = Path(__file__).resolve().parents[1]
            for filename in ('001_telemetria_web.sql', '002_favoritos_avisos.sql', '004_choferes_viajes.sql',
                             '005_operaciones.sql', '005_operaciones.sql', '006_cuentas.sql', '007_notificaciones.sql'):
                cur.execute((root / 'migrations' / filename).read_text().replace('public.', 'operaciones_test.'))
            cur.execute('SELECT estado_validacion FROM empresas WHERE id=1;')
            cls.legacy_state = cur.fetchone()['estado_validacion']
        cls.previous_url = website.DATABASE_URL
        website.DATABASE_URL = cls.url
        cls.application = Flask('operaciones-tests')
        cls.application.config['TESTING'] = True
        register_operations(cls.application, website.get_db_connection, website.current_user, website.token_hash)

    @classmethod
    def tearDownClass(cls):
        website.DATABASE_URL = cls.previous_url
        with cls.database.cursor() as cur:
            cur.execute('DROP SCHEMA operaciones_test CASCADE;')
        cls.database.close()

    def setUp(self):
        with self.database.cursor() as cur:
            cur.execute('TRUNCATE empresas,usuarios,horarios,limites_cuentas RESTART IDENTITY CASCADE;')
            cur.execute("INSERT INTO empresas(nombre,estado_validacion)VALUES('Empresa A','aprobada'),('Empresa B','aprobada'),('Borrador','pendiente');")
            cur.execute('''INSERT INTO usuarios(nombre,email,password,rol,empresa_id)VALUES
                ('Empresa A','a@example.invalid','fixture-password','empresa',1),
                ('Empresa B','b@example.invalid','fixture-password','empresa',2),
                ('Chofer A','driver@example.invalid','fixture-password','chofer',1),
                ('Chofer B','otherdriver@example.invalid','fixture-password','chofer',2),
                ('Admin','admin@example.invalid','fixture-password','admin',NULL),
                ('Pasajero','passenger@example.invalid','fixture-password','pasajero',NULL),
                ('Borrador','draft@example.invalid','fixture-password','empresa',3);
                INSERT INTO horarios(empresa_id,tipo,origen,destino,salida,llegada,dias,anden)VALUES
                (1,'salida','Panguipulli','Valdivia','09:00','11:00','Diario','1'),
                (2,'salida','Panguipulli','Temuco','10:00','12:00','Diario','2');''')
        self.company = self.login('a')
        self.other = self.login('b')
        self.admin = self.login('admin')

    def login(self, name):
        login = website.app.test_client()
        response = login.post('/api/login', json={'email': name + '@example.invalid', 'password': 'fixture-password'})
        self.assertEqual(response.status_code, 200, response.json)
        client = self.application.test_client()
        client.set_cookie(website.SESSION_COOKIE, login.get_cookie(website.SESSION_COOKIE).value)
        return client

    def bus(self, client=None, **values):
        return (client or self.company).post('/api/empresa/buses', json={
            'patente': 'AB-CD12', 'capacidad': 40, 'accesible': True, 'chofer_id': 3, **values})

    def stop(self, client=None, **values):
        return (client or self.company).post('/api/empresa/paradas', json={
            'nombre': 'Parada fixture', 'latitud': -39.64, 'longitud': -72.33, 'accesible': True, **values})

    def schedule(self, client=None, **values):
        return (client or self.company).post('/api/empresa/horarios', json={
            'tipo': 'salida', 'origen': 'Panguipulli', 'destino': 'Valdivia', 'salida': '23:30',
            'llegada': '01:00', 'dias': 'Lunes a Viernes', 'anden': '1', 'precio': 4000,
            'sentido': 'ida', 'chofer_id': 3, **values})

    def row(self, query, params=()):
        with self.database.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def test_migration_preserves_existing_companies_and_defaults_new_to_pending(self):
        self.assertEqual(self.legacy_state, 'aprobada')
        with self.database.cursor() as cur:
            cur.execute("INSERT INTO empresas(nombre)VALUES('Nueva')RETURNING estado_validacion;")
            self.assertEqual(cur.fetchone()['estado_validacion'], 'pendiente')
            cur.execute("SELECT COUNT(*)AS total FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='operaciones_test' AND c.relname IN ('buses','paradas_oficiales','horario_paradas','horario_excepciones','auditoria_eventos') AND c.relrowsecurity;")
            self.assertEqual(cur.fetchone()['total'], 5)

    def test_roles_are_server_verified_and_session_revocation_takes_effect(self):
        self.assertEqual(self.bus(self.application.test_client()).status_code, 401)
        for name in ('driver', 'admin', 'passenger'):
            self.assertEqual(self.bus(self.login(name)).status_code, 403)
        with self.database.cursor() as cur:
            cur.execute("UPDATE usuarios SET rol='pasajero' WHERE id=1;")
        self.assertEqual(self.bus().status_code, 403)

    def test_forced_password_change_blocks_company_mutations_but_allows_reads(self):
        with self.database.cursor() as cur:
            cur.execute('UPDATE usuarios SET requiere_cambio_password=TRUE WHERE id=1;')
        response = self.bus()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json['code'], 'PASSWORD_CHANGE_REQUIRED')
        self.assertEqual(self.row('SELECT COUNT(*)AS total FROM buses;')['total'], 0)
        self.assertEqual(self.row('SELECT COUNT(*)AS total FROM auditoria_eventos;')['total'], 0)
        response = self.company.get('/api/empresa/buses')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['buses'], [])

    def test_bus_crud_normalization_ownership_and_soft_delete(self):
        created = self.bus(empresa_id=2, activo=True)
        self.assertEqual(created.status_code, 201, created.json)
        item_id = created.json['id']
        row = self.row('SELECT empresa_id,patente,capacidad,accesible,chofer_id FROM buses WHERE id=%s;', (item_id,))
        self.assertEqual((row['empresa_id'], row['patente'], row['chofer_id']), (1, 'ABCD12', 3))
        self.assertEqual(self.company.get('/api/empresa/buses').json['buses'][0]['chofer_nombre'], 'Chofer A')
        self.assertEqual(self.other.patch(f'/api/empresa/buses/{item_id}', json={'capacidad': 30}).status_code, 404)
        self.assertEqual(self.other.delete(f'/api/empresa/buses/{item_id}').status_code, 404)
        self.assertEqual(self.company.patch(f'/api/empresa/buses/{item_id}', json={'capacidad': 30}).status_code, 200)
        self.assertEqual(self.company.delete(f'/api/empresa/buses/{item_id}').status_code, 200)
        self.assertFalse(self.row('SELECT activo FROM buses WHERE id=%s;', (item_id,))['activo'])
        self.assertEqual(self.bus().status_code, 409)

    def test_invalid_bus_values_or_foreign_driver_leave_no_rows(self):
        for values in ({'capacidad': 0}, {'capacidad': True}, {'patente': 'ONLYLETTERS'},
                       {'accesible': 'true'}, {'chofer_id': 4}):
            with self.subTest(values=values):
                self.assertIn(self.bus(**values).status_code, (400, 404))
        self.assertEqual(self.row('SELECT COUNT(*)AS total FROM buses;')['total'], 0)
        self.assertEqual(self.row('SELECT COUNT(*)AS total FROM auditoria_eventos;')['total'], 0)

    def test_pending_company_can_prepare_drafts(self):
        response = self.bus(self.login('draft'), patente='EFGH34', chofer_id=None)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.row('SELECT empresa_id FROM buses WHERE id=%s;', (response.json['id'],))['empresa_id'], 3)

    def test_schedule_crud_cross_company_validation_and_history_preservation(self):
        response = self.schedule(empresa_id=2, anden=None)
        self.assertEqual(response.status_code, 201, response.json)
        item_id = response.json['id']
        self.assertEqual(self.row('SELECT empresa_id FROM horarios WHERE id=%s;', (item_id,))['empresa_id'], 1)
        self.assertEqual(self.other.patch(f'/api/empresa/horarios/{item_id}', json={'precio': 5}).status_code, 404)
        self.assertEqual(self.company.patch(f'/api/empresa/horarios/{item_id}', json={'precio': 5000, 'sentido': 'vuelta'}).status_code, 200)
        self.assertEqual(self.company.delete(f'/api/empresa/horarios/{item_id}').status_code, 200)
        self.assertFalse(self.row('SELECT activo FROM horarios WHERE id=%s;', (item_id,))['activo'])
        for values in ({'precio': -1}, {'dias': 'cuando quiera'}, {'salida': '25:00'}, {'sentido': 'otro'}, {'chofer_id': 4}):
            self.assertIn(self.schedule(**values).status_code, (400, 404))

    def test_active_trip_blocks_bus_schedule_and_stop_changes(self):
        bus_id = self.bus().json['id']
        stop_id = self.stop().json['id']
        self.assertEqual(self.company.put('/api/empresa/horarios/1/paradas', json={'parada_ids': [stop_id]}).status_code, 200)
        with self.database.cursor() as cur:
            cur.execute("INSERT INTO viajes(empresa_id,chofer_id,horario_id,patente,bus_id)VALUES(1,3,1,'ABCD12',%s);", (bus_id,))
        for response in (self.company.delete(f'/api/empresa/buses/{bus_id}'),
                         self.company.patch('/api/empresa/horarios/1', json={'precio': 3}),
                         self.company.patch(f'/api/empresa/paradas/{stop_id}', json={'nombre': 'Cambio'}),
                         self.company.put('/api/empresa/horarios/1/paradas', json={'parada_ids': []})):
            self.assertEqual(response.status_code, 409, response.json)

    def test_exception_overrides_weekday_and_is_owned(self):
        path = '/api/empresa/horarios/1/excepciones'
        self.assertEqual(self.company.post(path, json={'fecha': '2026-12-25', 'opera': False, 'motivo': 'Feriado'}).status_code, 201)
        self.assertEqual(self.other.get(path).status_code, 404)
        with self.database.cursor() as cur:
            result = operation_day(cur, 1, date(2026, 12, 25), 'Diario', time(9), time(11))
            self.assertFalse(result['opera'])
            self.assertTrue(result['excepcion'])
        self.assertEqual(self.company.post(path, json={'fecha': '2026-12-25', 'opera': True, 'salida': '10:00', 'llegada': '12:00', 'motivo': None}).status_code, 201)
        with self.database.cursor() as cur:
            result = operation_day(cur, 1, date(2026, 12, 25), 'Lunes', time(9), time(11))
            self.assertTrue(result['opera'])
            self.assertEqual(result['salida'], time(10))
        self.assertEqual(len(self.company.get(path).json['excepciones']), 1)
        self.assertEqual(self.company.delete(path + '/2026-12-25').status_code, 200)
        self.assertEqual(self.company.post(path, json={'fecha': '2026-02-30', 'opera': True}).status_code, 400)

    def test_stop_confirmation_requires_admin_and_changes_invalidate_it(self):
        response = self.stop(confirmada=True, empresa_id=2)
        self.assertEqual(response.status_code, 201)
        item_id = response.json['id']
        self.assertFalse(self.row('SELECT confirmada FROM paradas_oficiales WHERE id=%s;', (item_id,))['confirmada'])
        self.assertEqual(self.company.patch(f'/api/admin/paradas/{item_id}/revision', json={'confirmada': True}).status_code, 403)
        self.assertEqual(self.admin.patch(f'/api/admin/paradas/{item_id}/revision', json={'confirmada': True}).status_code, 200)
        self.assertTrue(self.row('SELECT confirmada FROM paradas_oficiales WHERE id=%s;', (item_id,))['confirmada'])
        self.assertEqual(self.company.patch(f'/api/empresa/paradas/{item_id}', json={'nombre': 'Parada corregida'}).status_code, 200)
        self.assertFalse(self.row('SELECT confirmada FROM paradas_oficiales WHERE id=%s;', (item_id,))['confirmada'])
        self.assertEqual(self.other.delete(f'/api/empresa/paradas/{item_id}').status_code, 404)

    def test_stop_assignment_preserves_order_and_rejects_foreign_inactive_duplicate(self):
        first = self.stop(nombre='Primera').json['id']
        second = self.stop(nombre='Segunda').json['id']
        foreign = self.stop(self.other, nombre='Ajena').json['id']
        path = '/api/empresa/horarios/1/paradas'
        self.assertEqual(self.company.put(path, json={'parada_ids': [second, first]}).status_code, 200)
        stops = self.company.get('/api/empresa/horarios').json['horarios'][0]['paradas']
        self.assertEqual([item['id'] for item in stops], [second, first])
        for ids in ([first, first], [foreign]):
            self.assertIn(self.company.put(path, json={'parada_ids': ids}).status_code, (400, 404))
        self.company.delete(f'/api/empresa/paradas/{first}')
        self.assertEqual(self.company.put(path, json={'parada_ids': [first]}).status_code, 404)
        for values in ({'latitud': True}, {'latitud': 91}, {'longitud': float('nan')}):
            self.assertEqual(self.stop(**values).status_code, 400)

    def test_company_review_is_admin_only_and_rejection_requires_reason(self):
        path = '/api/admin/empresas/3/revision'
        self.assertEqual(self.company.patch(path, json={'estado_validacion': 'aprobada'}).status_code, 403)
        self.assertEqual(self.admin.patch(path, json={'estado_validacion': 'rechazada'}).status_code, 400)
        self.assertEqual(self.admin.patch(path, json={'estado_validacion': 'rechazada', 'motivo': 'Faltan antecedentes'}).status_code, 200)
        row = self.row('SELECT estado_validacion,validada_por FROM empresas WHERE id=3;')
        self.assertEqual((row['estado_validacion'], row['validada_por']), ('rechazada', 5))
        self.assertEqual(self.admin.patch(path, json={'estado_validacion': 'aprobada'}).status_code, 200)
        self.assertEqual(len(self.admin.get('/api/admin/empresas').json['empresas']), 3)

    def test_fleet_and_history_are_scoped_paginated_and_do_not_expose_credentials(self):
        bus_id = self.bus().json['id']
        with self.database.cursor() as cur:
            cur.execute("""INSERT INTO viajes(empresa_id,chofer_id,horario_id,patente,bus_id,rastreo_hash,latitud,longitud,ubicacion_en)
                VALUES(1,3,1,'ABCD12',%s,%s,-39.64,-72.33,CURRENT_TIMESTAMP);""", (bus_id, 'a' * 64))
            cur.execute("""INSERT INTO viajes(empresa_id,chofer_id,horario_id,patente,estado,finalizado_en,distancia_m)
                VALUES(1,3,1,'OLD123','finalizado',CURRENT_TIMESTAMP,12000),
                (2,4,2,'OTHR12','finalizado',CURRENT_TIMESTAMP,500);""")
        fleet = self.company.get('/api/empresa/flota').json
        self.assertEqual(len(fleet['buses']), 1)
        self.assertEqual(len(fleet['viajes']), 1)
        self.assertEqual(fleet['viajes'][0]['latitud'], -39.64)
        self.assertIn('eta_estado', fleet['viajes'][0])
        self.assertIsNone(fleet['viajes'][0]['eta_minutos'])
        history = self.company.get('/api/empresa/viajes?limite=1').json
        self.assertEqual(history['paginacion']['total'], 2)
        self.assertEqual(history['paginacion']['paginas'], 2)
        self.assertEqual(self.login('driver').get('/api/chofer/viajes/historial').json['paginacion']['total'], 2)
        self.assertEqual(self.other.get('/api/empresa/viajes').json['paginacion']['total'], 1)
        for item in fleet['viajes'] + history['viajes']:
            for forbidden in ('rastreo_hash', 'rastreo_token', 'password', 'password_hash'):
                self.assertNotIn(forbidden, item)
        self.assertEqual(self.company.get('/api/empresa/viajes?limite=101').status_code, 400)
        self.assertEqual(self.company.get('/api/empresa/viajes?fecha_desde=2026-10-20&fecha_hasta=2026-10-01').status_code, 400)

    def test_audit_filters_sensitive_details_and_keeps_company_scope(self):
        self.bus()
        self.bus(self.other, patente='OTHR12', chofer_id=4)
        with self.database.cursor() as cur:
            record_audit(cur, {'id': 1, 'rol': 'empresa', 'empresa_id': 1}, 'prueba.evento', 'bus', 1,
                         {'password': 'private-fixture', 'token': 'private-fixture', 'campos': ['patente', 'password_hash'],
                          'estado': 'finalizado', 'latitud': -39.64})
        audit = self.company.get('/api/empresa/auditoria').json
        self.assertEqual(audit['paginacion']['total'], 2)
        self.assertTrue(all(item['empresa_id'] == 1 for item in audit['auditoria']))
        content = str(audit)
        self.assertNotIn('private-fixture', content)
        self.assertNotIn('password_hash', content)
        self.assertNotIn('latitud', content)
        self.assertEqual(self.admin.get('/api/admin/auditoria').json['paginacion']['total'], 3)
        self.assertEqual(self.company.get('/api/empresa/auditoria?fecha_desde=2099-01-01').json['paginacion']['total'], 0)
        self.assertEqual(self.company.get('/api/admin/auditoria').status_code, 403)

    def test_cross_origin_invalid_json_and_oversized_payloads_are_rejected(self):
        for method, path, data in [('post', '/api/empresa/buses', {}),
                                   ('put', '/api/empresa/horarios/1/paradas', {'parada_ids': []}),
                                   ('patch', '/api/admin/empresas/1/revision', {'estado_validacion': 'aprobada'})]:
            client = self.admin if '/admin/' in path else self.company
            self.assertEqual(getattr(client, method)(path, json=data, headers={'Origin': 'https://attacker.invalid'}).status_code, 403)
        self.assertEqual(self.company.post('/api/empresa/buses', data='plain').status_code, 400)
        self.assertEqual(self.company.post('/api/empresa/buses', json=[]).status_code, 400)
        self.assertEqual(self.company.post('/api/empresa/buses', json={'padding': 'x' * 9000}).status_code, 413)


if __name__ == '__main__':
    unittest.main()
