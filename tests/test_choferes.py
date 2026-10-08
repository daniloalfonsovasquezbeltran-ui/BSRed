"""Cuentas empresariales, revocación y viajes con PostgreSQL real exclusivo local."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Event, current_thread
from time import monotonic
import unittest
from unittest.mock import patch
import psycopg2
from psycopg2.extensions import parse_dsn, make_dsn
from psycopg2.extras import RealDictCursor
import app as website

class ChoferesIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url=os.environ['TEST_DATABASE_URL']; params=parse_dsn(url)
        if params.get('host') not in ('localhost','127.0.0.1') or not params.get('dbname','').endswith('_test'):
            raise RuntimeError('Base de pruebas local exclusiva requerida')
        cls.url=make_dsn(url,options='-c search_path=choferes_test')
        cls.database=psycopg2.connect(cls.url);cls.database.autocommit=True
        cls.previous_url=website.DATABASE_URL;website.DATABASE_URL=cls.url
        with cls.database.cursor() as cur:
            cur.execute('DROP SCHEMA IF EXISTS choferes_test CASCADE; CREATE SCHEMA choferes_test;')
            cur.execute('''CREATE TABLE empresas(id SERIAL PRIMARY KEY,nombre VARCHAR(100) NOT NULL);
                CREATE TABLE usuarios(id SERIAL PRIMARY KEY,nombre VARCHAR(100),email VARCHAR(255) UNIQUE,password VARCHAR(255),rol VARCHAR(50),gps_activo BOOLEAN DEFAULT FALSE);
                CREATE TABLE horarios(id SERIAL PRIMARY KEY,empresa_id INTEGER REFERENCES empresas(id),tipo VARCHAR(30),origen VARCHAR(100),destino VARCHAR(100),salida TIME,llegada TIME,dias VARCHAR(100),anden VARCHAR(30));''')
            for name in ['001_telemetria_web.sql','002_favoritos_avisos.sql','004_choferes_viajes.sql']:
                cur.execute((Path(__file__).resolve().parents[1]/'migrations'/name).read_text().replace('public.','choferes_test.'))
    @classmethod
    def tearDownClass(cls):
        cls.database.close();website.DATABASE_URL=cls.previous_url
    def setUp(self):
        with self.database.cursor() as cur:
            cur.execute('TRUNCATE empresas,usuarios,horarios RESTART IDENTITY CASCADE;')
            cur.execute("INSERT INTO empresas(nombre) VALUES('Empresa A'),('Empresa B');")
            cur.execute('''INSERT INTO usuarios(nombre,email,password,rol,empresa_id) VALUES
                ('Empresa A','a@example.invalid','fixture-password','empresa',1),
                ('Empresa B','b@example.invalid','fixture-password','empresa',2),
                ('Chofer A','driver@example.invalid','fixture-password','chofer',1),
                ('Pasajero','passenger@example.invalid','fixture-password','pasajero',NULL),
                ('Admin','admin@example.invalid','fixture-password','admin',NULL),
                ('Pruebas','test@example.invalid','fixture-password','empresa',NULL);''')
            cur.execute("INSERT INTO horarios(empresa_id,tipo,origen,destino,salida,llegada,dias,anden) VALUES(1,'salida','Panguipulli','Valdivia','09:00','11:00','Todos los días','1'),(2,'salida','Panguipulli','Temuco','09:00','11:00','Todos los días','2');")
        self.company=self.login('a');self.other=self.login('b');self.driver=self.login('driver')
    def login(self,name,password='fixture-password'):
        client=website.app.test_client();r=client.post('/api/login',json={'email':name+'@example.invalid','password':password})
        self.assertEqual(r.status_code,200,r.json);return client
    def create(self,client=None,**extra):
        return (client or self.company).post('/api/empresa/choferes',json={'nombre':'Nuevo Chofer','email':'new@example.invalid','password':'fixture-password','telefono':'+56912345678','licencia':'A3',**extra})
    def start(self,client=None,route=1,plate='ABCD12'):
        return (client or self.driver).post('/api/chofer/viajes',json={'horario_id':route,'patente':plate})
    def point(self,trip,**extra):
        return self.driver.post('/api/chofer/viajes/'+str(trip['viaje']['id'])+'/ubicacion',headers={'Authorization':'Bearer '+trip['rastreo_token']},json={'latitud':-39.64,'longitud':-72.34,'precision_m':12,'ubicacion_en':datetime.now(timezone.utc).isoformat(),**extra})
    def test_only_company_can_manage_and_public_signup_cannot_create_driver(self):
        self.assertEqual(self.create(website.app.test_client()).status_code,401)
        for name in ['admin','passenger','driver']:
            self.assertEqual(self.create(self.login(name)).status_code,403)
        self.assertEqual(self.create(self.login('test')).status_code,409)
        self.assertEqual(self.company.post('/api/registro',json={'nombre':'Fake','email':'fake@example.invalid','password':'fixture-password','rol':'chofer'}).status_code,400)
    def test_creation_forces_company_and_role_and_hashes_password(self):
        r=self.create(empresa_id=2,rol='admin');self.assertEqual(r.status_code,201)
        with self.database.cursor() as c:
            c.execute('SELECT empresa_id,rol,password,password_hash FROM usuarios WHERE id=%s;',(r.json['id'],));row=c.fetchone()
        self.assertEqual(row[:3],(1,'chofer',''));self.assertNotEqual(row[3],'fixture-password')
        client=self.login('new');self.assertEqual(client.get('/api/sesion').json['user']['empresa_id'],1)
        listed=self.company.get('/api/empresa/choferes').json['choferes'];self.assertNotIn('password_hash',listed[0]);self.assertNotIn('password',listed[0])
        self.assertEqual(self.other.get('/api/empresa/choferes').json['choferes'],[])
    def test_invalid_duplicate_and_cross_origin_creation(self):
        for fields in [{'password':'short'},{'email':'bad'},{'nombre':''},{'telefono':['bad']}]:self.assertEqual(self.create(**fields).status_code,400)
        self.assertEqual(self.create(email='DRIVER@example.invalid').status_code,409)
        self.assertEqual(self.company.post('/api/empresa/choferes',json={},headers={'Origin':'https://attacker.invalid'}).status_code,403)
    def test_other_company_cannot_suspend_delete_or_list_routes(self):
        self.assertEqual(self.other.patch('/api/empresa/choferes/3',json={'suspendido':True}).status_code,404)
        self.assertEqual(self.other.delete('/api/empresa/choferes/3').status_code,404)
        self.assertEqual([h['id'] for h in self.company.get('/api/empresa/horarios').json['horarios']],[1])
    def test_driver_routes_and_start_owned_trip(self):
        self.assertEqual([h['id'] for h in self.driver.get('/api/chofer/estado').json['horarios']],[1])
        self.assertEqual(self.start(route=2).status_code,404)
        self.assertEqual(self.start().status_code,201)
        self.assertEqual(self.start().status_code,409)
        self.assertEqual(self.driver.post('/api/logout').status_code,409)
        self.assertEqual(self.company.post('/api/chofer/viajes',json={'horario_id':1,'patente':'ABCD12'}).status_code,403)
    def test_assigned_route_is_not_available_to_other_driver(self):
        new=self.create().json['id']
        with self.database.cursor() as c:c.execute('UPDATE horarios SET chofer_id=%s WHERE id=1;',(new,))
        self.assertEqual(self.driver.get('/api/chofer/estado').json['horarios'],[])
        self.assertEqual(self.start().status_code,404)
    def test_same_bus_cannot_start_two_trips(self):
        self.assertEqual(self.start().status_code,201);self.create()
        self.assertEqual(self.start(self.login('new'),plate='AB-CD12').status_code,409)
    def test_registration_errors_do_not_expose_private_database_details(self):
        with patch.object(website,'get_db_connection',side_effect=RuntimeError('fixture-private-detail')):
            result=self.company.post('/api/registro',json={'nombre':'Nueva','email':'error@example.invalid','password':'fixture-password','rol':'empresa'})
        self.assertEqual(result.status_code,503)
        self.assertNotIn('fixture-private-detail',result.get_data(as_text=True))
    def test_concurrent_start_has_exactly_one_active_trip(self):
        clients=[self.login('driver'),self.login('driver')]
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda client:self.start(client).status_code,clients))
        self.assertEqual(sorted(results),[201,409])
    def test_suspension_revokes_sessions_tracking_and_reactivation_needs_new_login(self):
        second=self.login('driver');trip=self.start().json
        self.assertEqual(self.point(trip).status_code,200)
        tracking='/api/chofer/viajes/'+str(trip['viaje']['id'])+'/rastreo'
        headers={'Authorization':'Bearer '+trip['rastreo_token']}
        self.assertEqual(self.driver.get(tracking,headers=headers).status_code,200)
        self.assertEqual(self.company.patch('/api/empresa/choferes/3',json={'suspendido':True}).status_code,200)
        self.assertEqual(self.driver.get(tracking,headers=headers).status_code,401)
        for client in [self.driver,second]:self.assertEqual(client.get('/api/sesion').status_code,401)
        self.assertEqual(self.point(trip).status_code,401)
        self.assertEqual(self.driver.post('/api/login',json={'email':'driver@example.invalid','password':'fixture-password'}).status_code,401)
        self.assertEqual(self.company.patch('/api/empresa/choferes/3',json={'suspendido':False}).status_code,200)
        self.assertEqual(second.get('/api/sesion').status_code,401)
        self.assertIsNone(self.login('driver').get('/api/chofer/estado').json['viaje'])
    def test_delete_preserves_cancelled_trip_and_allows_recreate_email(self):
        trip=self.start().json
        self.assertEqual(self.company.delete('/api/empresa/choferes/3').status_code,200)
        self.assertEqual(self.point(trip).status_code,401)
        with self.database.cursor() as c:
            c.execute('SELECT chofer_id,estado FROM viajes WHERE id=%s;',(trip['viaje']['id'],));self.assertEqual(c.fetchone(),(None,'cancelado'))
        self.assertEqual(self.create(email='driver@example.invalid').status_code,201)
    def test_gps_requires_trip_token_and_valid_fresh_fix(self):
        trip=self.start().json;path='/api/chofer/viajes/'+str(trip['viaje']['id'])+'/ubicacion'
        self.assertEqual(self.driver.post(path,json={}).status_code,401)
        for fields in [{'latitud':91},{'longitud':True},{'precision_m':3000},{'ubicacion_en':(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()},{'ubicacion_en':'bad'}]:self.assertEqual(self.point(trip,**fields).status_code,400)
        self.assertEqual(self.point(trip).status_code,200)
        point=self.driver.get('/api/mapa/posiciones').json['posiciones'][0]
        self.assertEqual(point['latitud'],-39.64);self.assertNotIn('chofer_id',point);self.assertNotIn('rastreo_hash',point)
    def test_finish_stops_public_gps_and_is_idempotent(self):
        trip=self.start().json;self.point(trip);path='/api/chofer/viajes/'+str(trip['viaje']['id'])+'/finalizar'
        self.assertEqual(self.company.post(path,json={}).status_code,403)
        for _ in range(2):self.assertEqual(self.driver.post(path,json={}).status_code,200)
        self.assertEqual(self.point(trip).status_code,401)
        self.assertEqual(self.driver.get('/api/mapa/posiciones').json['posiciones'],[])
        self.assertEqual(self.driver.post('/api/logout').status_code,200)
    def test_old_fix_never_overwrites_new_and_stale_points_are_hidden(self):
        trip=self.start().json;self.point(trip)
        old=(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()
        r=self.point(trip,latitud=-38,ubicacion_en=old);self.assertFalse(r.json['actualizada'])
        with self.database.cursor() as c:c.execute("UPDATE viajes SET ubicacion_en=CURRENT_TIMESTAMP-INTERVAL '3 minutes';")
        row=self.driver.get('/api/mapa/posiciones').json['posiciones'][0];self.assertIsNone(row['latitud']);self.assertIsNone(row['longitud'])
    def test_credential_refresh_revokes_old_token_and_expiry_blocks_gps(self):
        trip=self.start().json;path='/api/chofer/viajes/'+str(trip['viaje']['id'])+'/credencial'
        r=self.driver.post(path,json={});self.assertEqual(r.status_code,200)
        self.assertEqual(self.point(trip).status_code,401)
        trip['rastreo_token']=r.json['rastreo_token'];self.assertEqual(self.point(trip).status_code,200)
        with self.database.cursor() as c:c.execute("UPDATE viajes SET iniciado_en=CURRENT_TIMESTAMP-INTERVAL '25 hours';")
        self.assertEqual(self.point(trip).status_code,401);self.assertEqual(self.driver.post(path,json={}).status_code,409)
    def test_company_signup_creates_its_own_company(self):
        r=self.company.post('/api/registro',json={'nombre':'Nueva empresa','email':'newcompany@example.invalid','password':'fixture-password','rol':'empresa'})
        self.assertEqual(r.status_code,201)
        user=self.login('newcompany').get('/api/sesion').json['user'];self.assertIsNotNone(user['empresa_id']);self.assertNotIn(user['empresa_id'],[1,2])
    def test_legacy_flag_endpoint_cannot_change_another_account(self):
        self.assertEqual(self.driver.post('/api/chofer/gps',json={'email':'a@example.invalid','activo':True}).status_code,410)
        with self.database.cursor() as c:c.execute('SELECT COUNT(*) FROM usuarios WHERE gps_activo=TRUE;');self.assertEqual(c.fetchone()[0],0)

    def test_concurrent_suspension_and_start_never_leaves_a_running_trip(self):
        with ThreadPoolExecutor(2) as pool:
            suspend=pool.submit(self.company.patch,'/api/empresa/choferes/3',json={'suspendido':True})
            start=pool.submit(self.start)
            self.assertEqual(suspend.result().status_code,200)
            self.assertIn(start.result().status_code,[201,401])
        with self.database.cursor() as c:
            c.execute("SELECT COUNT(*) FROM viajes WHERE estado='en_curso';");self.assertEqual(c.fetchone()[0],0)
    def test_concurrent_finish_and_position_cannot_restore_tracking(self):
        trip=self.start().json
        with ThreadPoolExecutor(2) as pool:
            point=pool.submit(self.point,trip)
            finish=pool.submit(self.driver.post,'/api/chofer/viajes/'+str(trip['viaje']['id'])+'/finalizar',json={})
            self.assertEqual(finish.result().status_code,200)
            self.assertIn(point.result().status_code,[200,401])
        self.assertEqual(self.driver.get('/api/mapa/posiciones').json['posiciones'],[])

    def wait_for_database_lock(self, application_name):
        deadline=monotonic()+5
        while monotonic()<deadline:
            with self.database.cursor() as cur:
                cur.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock');",(application_name,))
                if cur.fetchone()[0]:
                    return
            Event().wait(.01)
        self.fail('La solicitud concurrente no esperó por el bloqueo esperado')

    def favorite_during_driver_management(self, deleting=False):
        favorite_locked=Event();release_favorite=Event()
        class GatedCursor(RealDictCursor):
            def execute(cur, query, params=None):
                if current_thread().name.startswith('driver-management'):
                    super(GatedCursor,cur).execute("SET application_name='bsred_driver_management_test';")
                result=super(GatedCursor,cur).execute(query,params)
                if current_thread().name.startswith('favorite-write') and 'FOR UPDATE OF s' in query:
                    favorite_locked.set()
                    if not release_favorite.wait(8):
                        raise RuntimeError('La prueba no liberó el favorito')
                return result
        with patch.object(website,'RealDictCursor',GatedCursor), ThreadPoolExecutor(1,thread_name_prefix='favorite-write') as favorites, ThreadPoolExecutor(1,thread_name_prefix='driver-management') as management:
            favorite=favorites.submit(self.driver.put,'/api/favoritos/1',json={'minutos_antes':10})
            try:
                self.assertTrue(favorite_locked.wait(5))
                action=management.submit(self.company.delete,'/api/empresa/choferes/3') if deleting else management.submit(self.company.patch,'/api/empresa/choferes/3',json={'suspendido':True})
                self.wait_for_database_lock('bsred_driver_management_test')
            finally:
                release_favorite.set()
            self.assertEqual(favorite.result(timeout=10).status_code,200)
            self.assertEqual(action.result(timeout=10).status_code,200)
        self.assertEqual(self.driver.get('/api/sesion').status_code,401)

    def test_favorite_and_suspension_do_not_deadlock(self):
        self.favorite_during_driver_management()

    def test_favorite_and_deletion_do_not_deadlock(self):
        self.favorite_during_driver_management(deleting=True)

    def test_logout_serializes_with_starting_a_trip(self):
        checked_trip=Event();release_logout=Event()
        class GatedCursor(RealDictCursor):
            def execute(cur, query, params=None):
                if current_thread().name.startswith('trip-start'):
                    super(GatedCursor,cur).execute("SET application_name='bsred_logout_start_test';")
                result=super(GatedCursor,cur).execute(query,params)
                if current_thread().name.startswith('driver-logout') and "SELECT id FROM viajes WHERE chofer_id" in query:
                    checked_trip.set()
                    if not release_logout.wait(8):
                        raise RuntimeError('La prueba no liberó el cierre de sesión')
                return result
        with patch.object(website,'RealDictCursor',GatedCursor), ThreadPoolExecutor(1,thread_name_prefix='driver-logout') as logouts, ThreadPoolExecutor(1,thread_name_prefix='trip-start') as starts:
            logout=logouts.submit(self.driver.post,'/api/logout')
            try:
                self.assertTrue(checked_trip.wait(5))
                # Otro cliente usa la misma sesión que está cerrándose.
                starting_client=website.app.test_client()
                starting_client.set_cookie(website.SESSION_COOKIE,self.driver.get_cookie(website.SESSION_COOKIE).value)
                start=starts.submit(self.start,starting_client)
                self.wait_for_database_lock('bsred_logout_start_test')
            finally:
                release_logout.set()
            self.assertEqual(logout.result(timeout=10).status_code,200)
            self.assertEqual(start.result(timeout=10).status_code,401)
        with self.database.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM viajes WHERE estado='en_curso';")
            self.assertEqual(cur.fetchone()[0],0)
