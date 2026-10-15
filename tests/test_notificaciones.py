"""Entrega nativa/operativa con PostgreSQL local y transporte siempre sustituido."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec,padding,rsa
from flask import Flask,request
import psycopg2
from psycopg2.extensions import make_dsn,parse_dsn
from psycopg2.extras import RealDictCursor

import avisos
import notificaciones as notifications

NOW=datetime(2026,10,7,11,50,tzinfo=timezone.utc)
TOKEN='local-android-token-fixture-123456789'


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def firebase_fixture():
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    account={'type':'service_account','project_id':'bsred-local-test',
             'client_email':'fixture@bsred-local-test.iam.gserviceaccount.com',
             'private_key':key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()).decode()}
    return {'ANDROID_FIREBASE_API_KEY':'AIzaLocalFixtureNotARealFirebaseKey123',
            'ANDROID_FIREBASE_APP_ID':'1:123456789:android:0123456789abcdef',
            'ANDROID_FIREBASE_PROJECT_ID':'bsred-local-test','ANDROID_FIREBASE_SENDER_ID':'123456789',
            'FCM_SERVICE_ACCOUNT_JSON':json.dumps(account)},key


class FcmProtocolTests(unittest.TestCase):
    def test_oauth_signed_rs256_and_data_only_delivery_never_follows_redirects(self):
        import requests
        env,key=firebase_fixture()
        oauth=requests.Response();oauth.status_code=200
        oauth._content=b'{"access_token":"fixture-access-token","expires_in":3600}'
        pushed=requests.Response();pushed.status_code=200;pushed._content=b'{}'
        payload={'title':'Salida programada','body':'Panguipulli → Valdivia','event_id':'salida:1:42',
                 'url':'/?recorrido=1','horario_id':1,'tipo':'salida_programada',
                 'expira_en':(NOW+timedelta(minutes=10)).isoformat(),'binding_id':'fixture-binding'}
        notifications._oauth_cache.clear()
        with patch.dict(os.environ,env),patch('requests.post',side_effect=[oauth,pushed]) as send:
            notifications.send_fcm(TOKEN,payload,600)
        self.assertEqual(send.call_count,2)
        token_request,delivery=send.call_args_list
        for call in (token_request,delivery):
            self.assertTrue(call.kwargs['verify']);self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(token_request.args[0],notifications.OAUTH_URL)
        assertion=token_request.kwargs['data']['assertion'].split('.')
        key.public_key().verify(base64.urlsafe_b64decode(assertion[2]+'='*(-len(assertion[2])%4)),
                                '.'.join(assertion[:2]).encode(),padding.PKCS1v15(),hashes.SHA256())
        claims=json.loads(base64.urlsafe_b64decode(assertion[1]+'='*(-len(assertion[1])%4)))
        self.assertEqual(claims['aud'],notifications.OAUTH_URL)
        message=delivery.kwargs['json']['message']
        self.assertNotIn('notification',message)
        self.assertEqual(message['data']['binding_id'],'fixture-binding')
        self.assertEqual(message['android']['ttl'],'600s')

    def test_public_config_never_exposes_service_account_and_mismatch_disables(self):
        env,_=firebase_fixture()
        with patch.dict(os.environ,env):
            config=notifications.android_configuration()
            self.assertTrue(config['disponible'])
            self.assertNotIn('private_key',json.dumps(config))
            with patch.dict(os.environ,{'ANDROID_FIREBASE_PROJECT_ID':'different-project'}):
                self.assertFalse(notifications.android_configuration()['disponible'])

    def test_unregistered_fcm_token_is_classified_as_expired(self):
        import requests
        env,_=firebase_fixture()
        response=requests.Response();response.status_code=404
        response._content=b'{"error":{"details":[{"errorCode":"UNREGISTERED"}]}}'
        with patch.dict(os.environ,env),patch.object(notifications,'_oauth_token',return_value='fixture'),patch('requests.post',return_value=response):
            with self.assertRaises(avisos.PushDeliveryError) as caught:
                notifications.send_fcm(TOKEN,{'event_id':'operativo:1','expira_en':NOW.isoformat()},60)
        self.assertEqual(caught.exception.status,410)


class NotificationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url=os.environ.get('TEST_DATABASE_URL')
        if not url:
            raise RuntimeError('Se necesita PostgreSQL local exclusivo de prueba')
        parsed=parse_dsn(url)
        if parsed.get('host') not in ('localhost','127.0.0.1') or not parsed.get('dbname','').endswith('_test'):
            raise RuntimeError('Sólo localhost con base terminada en _test')
        cls.url=make_dsn(url,options='-c search_path=notificaciones_test')
        cls.database=psycopg2.connect(cls.url,cursor_factory=RealDictCursor)
        cls.database.autocommit=True
        with cls.database.cursor() as cur:
            cur.execute('CREATE SCHEMA IF NOT EXISTS notificaciones_test;')
            cur.execute('''CREATE TABLE IF NOT EXISTS usuarios(id SERIAL PRIMARY KEY,nombre TEXT,email TEXT UNIQUE,
                            password TEXT,rol TEXT,gps_activo BOOLEAN DEFAULT FALSE);
                           CREATE TABLE IF NOT EXISTS empresas(id SERIAL PRIMARY KEY,nombre TEXT);
                           CREATE TABLE IF NOT EXISTS horarios(id SERIAL PRIMARY KEY,tipo TEXT,origen TEXT,destino TEXT,
                            salida TIME,llegada TIME,anden TEXT,dias TEXT,empresa_id INTEGER REFERENCES empresas(id));''')
            migrations=Path(__file__).resolve().parents[1]/'migrations'
            for prefix in ('001','002','004','005','006','007'):
                source=next(migrations.glob(prefix+'_*.sql')).read_text().replace('public.','notificaciones_test.')
                cur.execute(source)
                if prefix=='007':cur.execute(source)
        cls.app=Flask('notification-tests')
        def authenticated(cur,lock=False):
            session=request.cookies.get('test-session','')
            cur.execute('''SELECT u.id,u.rol,u.empresa_id,u.requiere_cambio_password FROM usuarios u JOIN sesiones_web s ON s.usuario_id=u.id
                           WHERE s.token_hash=%s AND s.expira_en>CURRENT_TIMESTAMP AND NOT u.suspendido
                           ;''',(hashlib.sha256(session.encode()).hexdigest(),))
            return cur.fetchone()
        notifications.register_notifications(cls.app,cls.connection,authenticated,
                                             lambda value:hashlib.sha256(value.encode()).hexdigest())

    @classmethod
    def connection(cls):
        return psycopg2.connect(cls.url,cursor_factory=RealDictCursor)

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def setUp(self):
        with self.database.cursor() as cur:
            cur.execute('TRUNCATE empresas,usuarios,horarios RESTART IDENTITY CASCADE;')
            cur.execute('''INSERT INTO empresas(nombre,estado_validacion) VALUES('Empresa fixture','aprobada'),('Otra empresa','pendiente');
                           INSERT INTO usuarios(nombre,email,rol,empresa_id) VALUES
                            ('Pasajero','pasajero@example.invalid','pasajero',NULL),
                            ('Empresa','empresa@example.invalid','empresa',1),
                            ('Otra','otra@example.invalid','empresa',2),('Otro pasajero','otro@example.invalid','pasajero',NULL);
                           INSERT INTO horarios(tipo,origen,destino,salida,llegada,anden,dias,empresa_id) VALUES
                            ('salida','Panguipulli','Valdivia','09:00','11:00','1','Diario',1),
                            ('salida','Panguipulli','Temuco','09:00','12:00','2','Diario',2);
                           INSERT INTO favoritos_recorridos(usuario_id,horario_id,anticipacion_min) VALUES(1,1,10);
                           INSERT INTO dispositivos_android(usuario_id,dispositivo_hash,token) VALUES(1,repeat('a',64),'local-android-token-fixture-123456789');''')
        self.client=self.app.test_client()

    def login(self,uid):
        value='local-session-fixture-'+str(uid)
        with self.database.cursor() as cur:
            cur.execute('''INSERT INTO sesiones_web(token_hash,usuario_id,expira_en) VALUES(%s,%s,CURRENT_TIMESTAMP+INTERVAL '1 hour')
                           ON CONFLICT(token_hash) DO UPDATE SET expira_en=EXCLUDED.expira_en;''',
                        (hashlib.sha256(value.encode()).hexdigest(),uid))
        self.client.set_cookie('test-session',value)

    def notice(self,kind='retraso',expiry=None):
        with self.database.cursor() as cur:
            cur.execute('''INSERT INTO avisos_operativos(empresa_id,horario_id,tipo,fecha,demora_min,mensaje,
                           creado_por,creado_en,expira_en) VALUES(1,1,%s,%s,%s,'Aviso operativo de prueba',2,%s,%s) RETURNING id;''',
                        (kind,NOW.astimezone(notifications.CHILE_TZ).date(),15 if kind=='retraso' else None,NOW,
                         expiry or NOW+timedelta(hours=1)))
            return cur.fetchone()['id']

    def web_subscription(self):
        public=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
        with self.database.cursor() as cur:
            cur.execute('''INSERT INTO suscripciones_push(usuario_id,endpoint,endpoint_hash,p256dh,auth,dispositivo_hash)
                           VALUES(1,'https://fcm.googleapis.com/fcm/send/local-fixture',repeat('b',64),%s,%s,repeat('c',64));''',
                        (b64(public),b64(b'0123456789abcdef')))

    def test_native_registration_private_idempotent_and_account_bound(self):
        self.assertEqual(self.client.post('/api/notificaciones/android/dispositivos',json={'token':TOKEN}).status_code,401)
        self.login(1)
        new='new-local-fcm-token-fixture-123456789'
        with patch.object(notifications,'android_configuration',return_value={'disponible':True}):
            first=self.client.post('/api/notificaciones/android/dispositivos',json={'token':new})
            second=self.client.post('/api/notificaciones/android/dispositivos',json={'token':new})
            self.assertEqual(first.status_code,200)
            self.assertEqual(first.json['binding_id'],second.json['binding_id'])
            self.assertNotIn(new,json.dumps(first.json))
            self.assertIn('HttpOnly',first.headers['Set-Cookie'])
            self.login(4)
            self.assertEqual(self.client.post('/api/notificaciones/android/dispositivos',json={'token':new}).status_code,409)

    def test_company_notices_own_approved_only_and_audited(self):
        today=datetime.now(notifications.CHILE_TZ).date().isoformat()
        payload={'horario_id':1,'tipo':'retraso','fecha':today,'demora_min':20,'mensaje':'Salida con retraso por tráfico.'}
        self.login(3)
        self.assertEqual(self.client.post('/api/empresa/avisos',json=payload).status_code,403)
        self.login(2)
        other=dict(payload,horario_id=2)
        self.assertEqual(self.client.post('/api/empresa/avisos',json=other).status_code,404)
        self.assertEqual(self.client.post('/api/empresa/avisos',json=payload,headers={'Origin':'https://attacker.invalid'}).status_code,403)
        created=self.client.post('/api/empresa/avisos',json=payload)
        self.assertEqual(created.status_code,201)
        self.assertEqual(len(self.client.get('/api/avisos?horario_id=1').json['avisos']),1)
        with self.database.cursor() as cur:
            cur.execute('SELECT detalles FROM auditoria_eventos;')
            self.assertNotIn('mensaje',cur.fetchone()['detalles'])
        self.assertEqual(self.client.delete('/api/empresa/avisos/'+str(created.json['aviso']['id']),json={}).status_code,200)

    def test_initial_password_blocks_native_registration_and_company_publication_without_writes(self):
        with self.database.cursor() as cur:
            cur.execute('UPDATE usuarios SET requiere_cambio_password=TRUE WHERE id IN(1,2);')
        self.login(1)
        with patch.object(notifications,'android_configuration',return_value={'disponible':True}):
            native=self.client.post('/api/notificaciones/android/dispositivos',json={'token':'new-local-native-token-blocked-123456789'})
        self.assertEqual(native.status_code,403)
        self.assertEqual(native.json['code'],'PASSWORD_CHANGE_REQUIRED')
        self.login(2)
        company=self.client.post('/api/empresa/avisos',json={
            'horario_id':1,'tipo':'informacion','mensaje':'Información operativa de prueba.',
            'fecha':datetime.now(notifications.CHILE_TZ).date().isoformat()})
        self.assertEqual(company.status_code,403)
        self.assertEqual(company.json['code'],'PASSWORD_CHANGE_REQUIRED')
        with self.database.cursor() as cur:
            cur.execute('SELECT COUNT(*) AS total FROM dispositivos_android;')
            self.assertEqual(cur.fetchone()['total'],1)
            cur.execute('SELECT COUNT(*) AS total FROM avisos_operativos;')
            self.assertEqual(cur.fetchone()['total'],0)
            cur.execute('SELECT COUNT(*) AS total FROM auditoria_eventos;')
            self.assertEqual(cur.fetchone()['total'],0)

    def test_scheduled_native_delivery_has_binding_and_is_deduplicated(self):
        sent=[]
        send=lambda *args:sent.append(args)
        notifications.run_extended_cycle(self.connection,fcm_sender=send,now=NOW)
        notifications.run_extended_cycle(self.connection,fcm_sender=send,now=NOW+timedelta(seconds=20))
        self.assertEqual(len(sent),1)
        payload=sent[0][1]
        self.assertEqual(payload['event_id'],'salida:1:'+str(int((NOW+timedelta(minutes=10)).timestamp())))
        self.assertEqual(payload['tipo'],'salida_programada')
        self.assertTrue(payload['binding_id'])
        self.assertEqual(payload['expira_en'],payload['salida_en'])

    def test_calendar_exception_inactive_unapproved_and_password_reset_prevent_delivery(self):
        sent=[]
        with self.database.cursor() as cur:
            cur.execute("INSERT INTO horario_excepciones(horario_id,fecha,opera) VALUES(1,'2026-10-07',FALSE);")
        notifications.run_extended_cycle(self.connection,fcm_sender=lambda *args:sent.append(args),now=NOW)
        self.assertEqual(sent,[])
        with self.database.cursor() as cur:
            cur.execute('DELETE FROM horario_excepciones; UPDATE usuarios SET requiere_cambio_password=TRUE WHERE id=1;')
        notifications.run_extended_cycle(self.connection,fcm_sender=lambda *args:sent.append(args),now=NOW)
        self.assertEqual(sent,[])
        with self.database.cursor() as cur:
            cur.execute("UPDATE usuarios SET requiere_cambio_password=FALSE; UPDATE empresas SET estado_validacion='rechazada' WHERE id=1;")
        notifications.run_extended_cycle(self.connection,fcm_sender=lambda *args:sent.append(args),now=NOW)
        self.assertEqual(sent,[])

    def test_operational_notice_reaches_web_and_android_even_after_original_departure(self):
        self.notice('cancelacion');self.web_subscription()
        web=[];android=[]
        later=NOW+timedelta(minutes=11)
        notifications.run_extended_cycle(self.connection,web_sender=lambda *args:web.append(args),
                                         fcm_sender=lambda *args:android.append(args),now=later)
        notifications.run_extended_cycle(self.connection,web_sender=lambda *args:web.append(args),
                                         fcm_sender=lambda *args:android.append(args),now=later)
        self.assertEqual(len(web),1);self.assertEqual(len(android),1)
        self.assertEqual(android[0][1]['tipo'],'cancelacion')
        self.assertNotIn('salida_en',android[0][1])

    def test_retry_after_past_expiry_never_resurrects_and_unregister_disables(self):
        self.notice(expiry=NOW+timedelta(minutes=5))
        failed=[]
        def sender(*args):
            failed.append(args)
            raise avisos.PushDeliveryError(429,'900')
        # Sólo operativo para que no exista recordatorio de salida adicional.
        with self.database.cursor() as cur:cur.execute("UPDATE horarios SET origen='Valdivia' WHERE id=1;")
        notifications.run_extended_cycle(self.connection,fcm_sender=sender,now=NOW)
        notifications.run_extended_cycle(self.connection,fcm_sender=sender,now=NOW+timedelta(minutes=1))
        self.assertEqual(len(failed),1)
        self.notice('informacion')
        def expired(*args):raise avisos.PushDeliveryError(410)
        notifications.run_extended_cycle(self.connection,fcm_sender=expired,now=NOW+timedelta(minutes=2))
        with self.database.cursor() as cur:
            cur.execute('SELECT activo FROM dispositivos_android WHERE id=1;')
            self.assertFalse(cur.fetchone()['activo'])

    def test_parallel_workers_claim_once_and_revoke_cancels_queue(self):
        entered=threading.Event();release=threading.Event();sent=[]
        def sender(*args):
            sent.append(args);entered.set()
            if not release.wait(5):raise RuntimeError('fixture timeout')
        with ThreadPoolExecutor(max_workers=2) as executor:
            first=executor.submit(notifications.run_extended_cycle,self.connection,fcm_sender=sender,now=NOW)
            self.assertTrue(entered.wait(5))
            second=executor.submit(notifications.run_extended_cycle,self.connection,fcm_sender=sender,now=NOW)
            try:second.result(timeout=5)
            finally:release.set()
            first.result(timeout=5)
        self.assertEqual(len(sent),1)
        with self.database.cursor() as cur:
            notifications.revoke_android_user(cur,1)
            cur.execute('SELECT activo FROM dispositivos_android WHERE id=1;')
            self.assertFalse(cur.fetchone()['activo'])

    def test_expired_worker_lease_recovers_same_job_and_respects_transient_backoff(self):
        notifications._enqueue_extended(self.connection,NOW-timedelta(minutes=1),False,True)
        with self.database.cursor() as cur:
            cur.execute('''UPDATE entregas_notificaciones SET estado='enviando',intentos=1,
                           reclamo_token='interrupted-fixture',reclamo_hasta=%s RETURNING id;''',
                        (NOW-timedelta(seconds=1),))
            original=cur.fetchone()['id']
        calls=[]
        def temporary(*args):
            calls.append(args)
            raise avisos.PushDeliveryError(503)
        notifications.run_extended_cycle(self.connection,fcm_sender=temporary,now=NOW)
        notifications.run_extended_cycle(self.connection,fcm_sender=temporary,now=NOW+timedelta(seconds=1))
        self.assertEqual(len(calls),1)
        delivered=[]
        notifications.run_extended_cycle(self.connection,fcm_sender=lambda *args:delivered.append(args),now=NOW+timedelta(seconds=41))
        self.assertEqual(len(delivered),1)
        with self.database.cursor() as cur:
            cur.execute('SELECT id,intentos,estado FROM entregas_notificaciones;')
            jobs=cur.fetchall()
        self.assertEqual(len(jobs),1)
        self.assertEqual((jobs[0]['id'],jobs[0]['intentos'],jobs[0]['estado']),(original,3,'enviado'))

    def test_reassigned_device_cannot_receive_previous_account_queue(self):
        notifications._enqueue_extended(self.connection,NOW-timedelta(minutes=1),False,True)
        with self.database.cursor() as cur:
            cur.execute('''UPDATE dispositivos_android SET usuario_id=4 WHERE id=1;
                           INSERT INTO favoritos_recorridos(usuario_id,horario_id) VALUES(4,1);''')
        delivered=[]
        notifications.run_extended_cycle(self.connection,fcm_sender=lambda *args:delivered.append(args),now=NOW)
        self.assertEqual(delivered,[])

    def test_explicit_reactivation_rotates_binding_and_resumes_unsent_reminder(self):
        self.login(1)
        new='new-local-fcm-token-reactivate-123456789'
        with self.database.cursor() as cur:cur.execute('UPDATE dispositivos_android SET activo=FALSE;')
        with patch.object(notifications,'android_configuration',return_value={'disponible':True}):
            first=self.client.post('/api/notificaciones/android/dispositivos',json={'token':new})
            notifications._enqueue_extended(self.connection,NOW-timedelta(minutes=1),False,True)
            self.assertEqual(self.client.delete('/api/notificaciones/android/dispositivos',json={'token':new}).status_code,200)
            again=self.client.post('/api/notificaciones/android/dispositivos',json={'token':new})
        self.assertNotEqual(first.json['binding_id'],again.json['binding_id'])
        sent=[]
        notifications.run_extended_cycle(self.connection,fcm_sender=lambda *args:sent.append(args),now=NOW)
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0][1]['binding_id'],again.json['binding_id'])
