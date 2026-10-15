"""Avisos operativos, registro Android y entrega durable Web Push/FCM.

Los tokens FCM y la cuenta de servicio sólo se usan en el servidor. Este
módulo no envía mensajes al registrar un dispositivo o publicar un aviso.
"""
import base64
from datetime import date, datetime, time as clock_time, timedelta, timezone
from functools import wraps
import hashlib
import json
import os
import re
import secrets
import threading
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import jsonify, make_response, request
from psycopg2.extras import Json

from avisos import (PushDeliveryError, InvalidSubscription, CLAIM_SECONDS, MAX_ATTEMPTS, ALLOWED_LEAD_MINUTES,
                    _cursor, _retry_delay, notification_payload, send_web_push,
                    push_configuration)
from rutas import CHILE_TZ, _local_datetime, normalize

DEVICE_COOKIE = 'bsred_push_dispositivo'
TOKEN_RE = re.compile(r'^[A-Za-z0-9:_-]{20,4096}$')
PROJECT_RE = re.compile(r'^[a-z][a-z0-9-]{4,61}[a-z0-9]$')
EVENT_RE = re.compile(r'^[A-Za-z0-9:_-]{1,160}$')
OAUTH_URL = 'https://oauth2.googleapis.com/token'
FCM_SCOPE = 'https://www.googleapis.com/auth/firebase.messaging'
_oauth_lock = threading.Lock()
_oauth_cache = {}


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _firebase_public():
    values = {key: os.environ.get(name, '').strip() for key, name in {
        'apiKey': 'ANDROID_FIREBASE_API_KEY', 'applicationId': 'ANDROID_FIREBASE_APP_ID',
        'projectId': 'ANDROID_FIREBASE_PROJECT_ID', 'gcmSenderId': 'ANDROID_FIREBASE_SENDER_ID'}.items()}
    if (not re.fullmatch(r'[A-Za-z0-9_-]{20,200}', values['apiKey'])
            or not PROJECT_RE.fullmatch(values['projectId'])
            or not re.fullmatch(r'[0-9]{6,30}', values['gcmSenderId'])
            or not re.fullmatch(r'1:' + re.escape(values['gcmSenderId']) + r':android:[a-fA-F0-9]{8,100}',
                                values['applicationId'])):
        return None
    return values


def _fcm_settings():
    public = _firebase_public()
    raw = os.environ.get('FCM_SERVICE_ACCOUNT_JSON', '')
    if public is None or not raw or len(raw) > 65536:
        return None
    try:
        account = json.loads(raw)
        if (not isinstance(account, dict) or account.get('type') != 'service_account'
                or account.get('project_id') != public['projectId']
                or not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com',
                                    account.get('client_email', ''))):
            return None
        key = serialization.load_pem_private_key(account['private_key'].encode('ascii'), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            return None
        return {'project': public['projectId'], 'email': account['client_email'], 'key': key,
                'cache_key': hashlib.sha256(raw.encode()).hexdigest()}
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        return None


def android_configuration():
    public = _firebase_public()
    return {'disponible': _fcm_settings() is not None, 'firebase': public}


def _oauth_token(settings):
    import requests
    cache_key = settings['cache_key']
    with _oauth_lock:
        cached = _oauth_cache.get(cache_key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        now = int(time.time())
        header = _b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
        claims = _b64(json.dumps({'iss': settings['email'], 'scope': FCM_SCOPE,
                                'aud': OAUTH_URL, 'iat': now, 'exp': now + 3600},
                               separators=(',', ':')).encode())
        signing_input = (header + '.' + claims).encode('ascii')
        signature = settings['key'].sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        assertion = signing_input.decode() + '.' + _b64(signature)
        try:
            response = requests.post(OAUTH_URL, data={
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion},
                timeout=15, allow_redirects=False, verify=True)
            if response.status_code != 200:
                raise PushDeliveryError(response.status_code, response.headers.get('Retry-After'))
            data = response.json()
            token = data.get('access_token')
            expires = data.get('expires_in')
            if not isinstance(token, str) or not token or not isinstance(expires, int) or expires < 60:
                raise PushDeliveryError(503)
            _oauth_cache.clear()
            _oauth_cache[cache_key] = (token, time.time() + min(3600, expires))
            return token
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            raise PushDeliveryError(503) from None


def send_fcm(token, payload, ttl):
    """FCM HTTP v1 con OAuth firmado y payload data-only; nunca usa legacy keys."""
    import requests
    settings = _fcm_settings()
    if settings is None:
        raise PushDeliveryError(503)
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise PushDeliveryError(410)
    if not EVENT_RE.fullmatch(str(payload.get('event_id', ''))):
        raise PushDeliveryError(400)
    data = {key: str(value) for key, value in payload.items() if value is not None
            and key in ('title', 'body', 'url', 'horario_id', 'event_id', 'tipo', 'expira_en', 'salida_en','binding_id')}
    if 'expira_en' not in data or len(json.dumps(data, ensure_ascii=False).encode()) > 3500:
        raise PushDeliveryError(400)
    message = {'token': token, 'data': data, 'android': {
        'priority': 'high', 'ttl': str(max(1, min(86400, int(ttl)))) + 's',
        'collapse_key': hashlib.sha256(data['event_id'].encode()).hexdigest()[:32]}}
    try:
        response = requests.post('https://fcm.googleapis.com/v1/projects/' + settings['project'] + '/messages:send',
                                 json={'message': message}, headers={'Authorization': 'Bearer ' + _oauth_token(settings)},
                                 timeout=15, allow_redirects=False, verify=True)
        if response.status_code == 200:
            return
        try:
            error = response.json().get('error', {})
            codes = {item.get('errorCode') for item in error.get('details', []) if isinstance(item, dict)}
        except (ValueError, TypeError, AttributeError):
            codes = set()
        # Un proyecto inexistente también devuelve404; sólo UNREGISTERED
        # prueba que el token de un dispositivo haya caducado.
        status = 410 if 'UNREGISTERED' in codes else 503 if response.status_code==404 else response.status_code
        raise PushDeliveryError(status, response.headers.get('Retry-After'))
    except requests.RequestException:
        raise PushDeliveryError(503) from None


def revoke_android_device(cur, device_hash):
    cur.execute('''UPDATE dispositivos_android SET activo=FALSE, actualizado_en=CURRENT_TIMESTAMP
                   WHERE dispositivo_hash=%s RETURNING id;''', (device_hash,))
    rows = cur.fetchall()
    for row in rows:
        cur.execute('''UPDATE entregas_notificaciones SET estado='expirado', reclamo_token=NULL,
                       reclamo_hasta=NULL WHERE dispositivo_android_id=%s
                       AND estado IN ('pendiente','enviando');''', (row['id'],))


def revoke_android_user(cur, user_id):
    cur.execute('''UPDATE dispositivos_android SET activo=FALSE, actualizado_en=CURRENT_TIMESTAMP
                   WHERE usuario_id=%s RETURNING id;''', (user_id,))
    rows = cur.fetchall()
    for row in rows:
        cur.execute('''UPDATE entregas_notificaciones SET estado='expirado', reclamo_token=NULL,
                       reclamo_hasta=NULL WHERE dispositivo_android_id=%s
                       AND estado IN ('pendiente','enviando');''', (row['id'],))


def _serialize_notice(row):
    return {key: (value.isoformat() if isinstance(value, (date, datetime)) else value)
            for key, value in row.items() if key in (
                'id', 'empresa_id', 'horario_id', 'tipo', 'fecha', 'demora_min', 'mensaje',
                'creado_en', 'expira_en', 'empresa', 'origen', 'destino')}


def register_notifications(app, get_connection, current_user, token_hash):
    def device_cookie():
        value = request.cookies.get(DEVICE_COOKIE, '')
        return value if re.fullmatch(r'[A-Za-z0-9_-]{43}', value) else None

    def account_api(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if request.method != 'GET':
                origin = request.headers.get('Origin')
                if ((origin and origin.rstrip('/') != request.host_url.rstrip('/'))
                        or (not origin and request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'))):
                    return jsonify(success=False, message='La solicitud debe realizarse desde BSRed.'), 403
                if not request.is_json or (request.content_length and request.content_length > 8192):
                    return jsonify(success=False, message='Solicitud JSON inválida.'), 400
            connection = None
            try:
                connection = get_connection()
                with _cursor(connection) as cur:
                    user = current_user(cur, lock=request.method != 'GET')
                    if not user:
                        return jsonify(success=False, message='Inicia sesión.'), 401
                    if request.method != 'GET' and user.get('requiere_cambio_password'):
                        return jsonify(success=False, code='PASSWORD_CHANGE_REQUIRED',
                                       message='Cambia tu contraseña inicial antes de continuar.'), 403
                    response = make_response(function(cur, user, *args, **kwargs))
                if response.status_code < 400:
                    connection.commit()
                else:
                    connection.rollback()
                return response
            except Exception as error:
                if connection:
                    connection.rollback()
                app.logger.error('No se pudo completar la operación de avisos: %s', type(error).__name__)
                return jsonify(success=False, message='No se pudo completar la operación.'), 503
            finally:
                if connection:
                    connection.close()
        return wrapped

    @app.get('/api/notificaciones/android/config')
    def android_config():
        return jsonify(success=True, **android_configuration())

    @app.route('/api/notificaciones/android/dispositivos', methods=['POST', 'DELETE'])
    @account_api
    def android_devices(cur, user):
        body = request.get_json(silent=True)
        token = body.get('token') if isinstance(body, dict) else None
        if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
            return jsonify(success=False, message='El token del dispositivo no es válido.'), 400
        cookie = device_cookie()
        if request.method == 'DELETE':
            if cookie:
                cur.execute('''SELECT id FROM dispositivos_android WHERE usuario_id=%s
                               AND dispositivo_hash=%s AND token=%s FOR UPDATE;''',
                            (user['id'], token_hash(cookie), token))
                if cur.fetchone():
                    revoke_android_device(cur, token_hash(cookie))
            return jsonify(success=True)
        if not android_configuration()['disponible']:
            return jsonify(success=False, message='Configura Firebase para activar los avisos Android.'), 503
        cookie = cookie or secrets.token_urlsafe(32)
        digest = token_hash(cookie)
        cur.execute('''SELECT id,usuario_id,dispositivo_hash FROM dispositivos_android
                       WHERE token=%s FOR UPDATE;''', (token,))
        existing = cur.fetchone()
        if existing and (existing['usuario_id'] != user['id'] or existing['dispositivo_hash'].strip() != digest):
            return jsonify(success=False, message='Desvincula primero los avisos de la otra cuenta o dispositivo.'), 409
        cur.execute('''SELECT id,usuario_id,token,activo FROM dispositivos_android
                       WHERE dispositivo_hash=%s FOR UPDATE;''', (digest,))
        previous = cur.fetchone()
        if previous and previous['usuario_id'] != user['id'] and (previous['token'] == token or previous['activo']):
            return jsonify(success=False, message='Desvincula primero la cuenta anterior.'), 409
        if previous and (previous['usuario_id'] != user['id'] or previous['token'] != token):
            cur.execute('DELETE FROM entregas_notificaciones WHERE dispositivo_android_id=%s;', (previous['id'],))
        cur.execute('''INSERT INTO dispositivos_android(usuario_id,dispositivo_hash,token,activo)
                       VALUES(%s,%s,%s,TRUE) ON CONFLICT(dispositivo_hash) DO UPDATE SET
                       vinculo_id=CASE WHEN dispositivos_android.usuario_id=EXCLUDED.usuario_id
                                          AND dispositivos_android.token=EXCLUDED.token AND dispositivos_android.activo
                                       THEN dispositivos_android.vinculo_id ELSE gen_random_uuid()::text END,
                       usuario_id=EXCLUDED.usuario_id,token=EXCLUDED.token,activo=TRUE,
                       actualizado_en=CURRENT_TIMESTAMP RETURNING vinculo_id;''', (user['id'], digest, token))
        response = jsonify(success=True,binding_id=cur.fetchone()['vinculo_id'])
        response.set_cookie(DEVICE_COOKIE, cookie, max_age=365*86400, httponly=True,
                            secure=request.is_secure, samesite='Lax', path='/')
        return response

    def company(cur, user):
        if user['rol'] != 'empresa' or not user.get('empresa_id'):
            return None
        cur.execute("SELECT id FROM empresas WHERE id=%s AND estado_validacion='aprobada';", (user['empresa_id'],))
        return cur.fetchone()

    @app.route('/api/empresa/avisos', methods=['GET', 'POST'])
    @account_api
    def company_notices(cur, user):
        if not company(cur, user):
            return jsonify(success=False, message='Se requiere una empresa aprobada.'), 403
        if request.method == 'GET':
            cur.execute('''SELECT a.*,h.origen,h.destino FROM avisos_operativos a
                           JOIN horarios h ON h.id=a.horario_id WHERE a.empresa_id=%s
                           ORDER BY a.creado_en DESC LIMIT 100;''', (user['empresa_id'],))
            return jsonify(success=True, avisos=[_serialize_notice(row) for row in cur.fetchall()])
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(success=False, message='Datos del aviso inválidos.'), 400
        route_id, kind, message = body.get('horario_id'), body.get('tipo'), body.get('mensaje')
        delay = body.get('demora_min')
        if (type(route_id) is not int or route_id <= 0 or kind not in ('retraso','cancelacion','informacion')
                or not isinstance(message, str) or not 5 <= len(message.strip()) <= 500
                or any(ord(c) < 32 and c not in '\n\t' for c in message)
                or (kind == 'retraso' and (type(delay) is not int or not 1 <= delay <= 720))
                or (kind != 'retraso' and delay is not None)):
            return jsonify(success=False, message='Revisa recorrido, tipo, mensaje y demora (1 a 720 min).'), 400
        now = datetime.now(timezone.utc)
        try:
            day = date.fromisoformat(body.get('fecha', ''))
            today = now.astimezone(CHILE_TZ).date()
            if not today <= day <= today + timedelta(days=30):
                raise ValueError()
        except (ValueError, TypeError):
            return jsonify(success=False, message='Indica una fecha entre hoy y los próximos 30 días.'), 400
        cur.execute('''SELECT id FROM horarios WHERE id=%s AND empresa_id=%s AND activo=TRUE;''',
                    (route_id, user['empresa_id']))
        if not cur.fetchone():
            return jsonify(success=False, message='El recorrido no pertenece a tu empresa o está inactivo.'), 404
        cur.execute('''SELECT COUNT(*) AS total,COUNT(*) FILTER(WHERE horario_id=%s) AS recorrido
                       FROM avisos_operativos WHERE empresa_id=%s
                         AND creado_en>CURRENT_TIMESTAMP-INTERVAL '1 hour';''',(route_id,user['empresa_id']))
        recent=cur.fetchone()
        if recent['total']>=20 or recent['recorrido']>=5:
            return jsonify(success=False,message='Espera antes de publicar más avisos para este recorrido.'),429
        expiry = datetime.combine(day + timedelta(days=1), clock_time(6), CHILE_TZ).astimezone(timezone.utc)
        cur.execute('''INSERT INTO avisos_operativos
                       (empresa_id,horario_id,tipo,fecha,demora_min,mensaje,creado_por,expira_en)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *;''',
                    (user['empresa_id'],route_id,kind,day,delay,message.strip(),user['id'],expiry))
        row = cur.fetchone()
        from auditoria import record_audit
        record_audit(cur, user, 'crear_aviso', 'aviso_operativo', row['id'],
                     {'horario_id': route_id, 'tipo': kind, 'fecha': day.isoformat()})
        return jsonify(success=True, aviso=_serialize_notice(row)), 201

    @app.delete('/api/empresa/avisos/<int:notice_id>')
    @account_api
    def remove_notice(cur,user,notice_id):
        if not company(cur,user):
            return jsonify(success=False,message='Se requiere una empresa aprobada.'),403
        cur.execute('DELETE FROM avisos_operativos WHERE id=%s AND empresa_id=%s RETURNING horario_id,fecha;',
                    (notice_id,user['empresa_id']))
        row=cur.fetchone()
        if not row:
            return jsonify(success=False,message='Aviso no encontrado.'),404
        from auditoria import record_audit
        record_audit(cur,user,'eliminar_aviso','aviso_operativo',notice_id,
                     {'horario_id':row['horario_id'],'fecha':row['fecha']})
        return jsonify(success=True)

    @app.get('/api/avisos')
    def public_notices():
        route = request.args.get('horario_id')
        if route is not None and (not route.isascii() or not route.isdigit() or len(route)>10 or not 1 <= int(route) <= 2147483647):
            return jsonify(success=False, message='Recorrido inválido.'), 400
        connection = None
        try:
            connection = get_connection()
            with _cursor(connection) as cur:
                cur.execute('''SELECT a.*,e.nombre AS empresa,h.origen,h.destino
                               FROM avisos_operativos a JOIN horarios h ON h.id=a.horario_id
                               JOIN empresas e ON e.id=a.empresa_id
                               WHERE a.expira_en>CURRENT_TIMESTAMP AND h.activo=TRUE AND h.empresa_id=a.empresa_id
                                 AND e.estado_validacion='aprobada'
                                 AND (%s::INTEGER IS NULL OR a.horario_id=%s)
                               ORDER BY a.creado_en DESC LIMIT 100;''', (int(route) if route else None, int(route) if route else None))
                rows = cur.fetchall()
            connection.commit()
            return jsonify(success=True, avisos=[_serialize_notice(row) for row in rows])
        except Exception as error:
            app.logger.error('No se pudieron consultar los avisos: %s', type(error).__name__)
            return jsonify(success=False, message='No se pudieron consultar los avisos.'), 503
        finally:
            if connection:
                connection.close()


def calendar_departures(cur, horario, now):
    """Fechas UTC de servicio: las excepciones del operador prevalecen."""
    from operaciones import operation_day
    today = now.astimezone(CHILE_TZ).date()
    result = []
    for offset in (0, 1):
        day = today + timedelta(days=offset)
        service = operation_day(cur, horario['horario_id'], day, horario['dias'],
                                horario['salida'], horario.get('llegada'))
        if not service['opera'] or service['salida'] is None:
            continue
        local = _local_datetime(day, service['salida'])
        if local is not None:
            departure = local.astimezone(timezone.utc)
            if now < departure <= now + timedelta(days=1):
                result.append(departure)
    return result


def _queue(cur, channel, device_id, user_id, route_id, event_id, payload, available, expires,
           notice_id=None, departure=None):
    # Los índices parciales impiden duplicar el mismo evento por dispositivo.
    web_id, android_id = (device_id, None) if channel == 'web' else (None, device_id)
    suffix = 'ON CONFLICT DO NOTHING'
    if departure is not None:
        suffix = f'''ON CONFLICT(dispositivo_android_id,event_id) WHERE canal='android'
                    DO UPDATE SET disponible_en=EXCLUDED.disponible_en,payload=EXCLUDED.payload,
                    estado=CASE WHEN entregas_notificaciones.estado='expirado' THEN 'pendiente'
                                ELSE entregas_notificaciones.estado END,
                    reintentar_en=CASE WHEN entregas_notificaciones.intentos=0 OR entregas_notificaciones.estado='expirado'
                                      THEN EXCLUDED.reintentar_en ELSE entregas_notificaciones.reintentar_en END
                    WHERE entregas_notificaciones.usuario_id=EXCLUDED.usuario_id
                      AND (entregas_notificaciones.estado='pendiente'
                        OR (entregas_notificaciones.estado='expirado' AND entregas_notificaciones.enviado_en IS NULL
                            AND entregas_notificaciones.reintentar_en<entregas_notificaciones.expira_en
                            AND entregas_notificaciones.intentos<{MAX_ATTEMPTS}))'''
    cur.execute('''INSERT INTO entregas_notificaciones
                   (canal,suscripcion_id,dispositivo_android_id,usuario_id,horario_id,aviso_id,event_id,
                    salida_en,expira_en,payload,disponible_en,reintentar_en)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ''' + suffix + ';',
                (channel,web_id,android_id,user_id,route_id,notice_id,event_id,departure,
                 expires,Json(payload),available,available))
    return cur.rowcount


def _enqueue_extended(get_connection, now, allow_web, allow_android):
    connection = get_connection()
    count = 0
    try:
        with _cursor(connection) as cur:
            if allow_android:
                cur.execute('''SELECT d.id AS device_id,d.usuario_id,f.horario_id,f.anticipacion_min,
                                      h.origen,h.destino,h.tipo,h.salida,h.llegada,h.dias,h.anden,
                                      e.nombre AS empresa
                               FROM dispositivos_android d JOIN usuarios u ON u.id=d.usuario_id
                               JOIN favoritos_recorridos f ON f.usuario_id=d.usuario_id
                               JOIN horarios h ON h.id=f.horario_id JOIN empresas e ON e.id=h.empresa_id
                               WHERE d.activo AND NOT u.suspendido AND NOT u.requiere_cambio_password
                                 AND h.activo AND e.estado_validacion='aprobada'
                                 AND LOWER(TRIM(h.origen))='panguipulli'
                                 AND LOWER(TRIM(h.tipo)) IN ('salida','salidas');''')
                for row in cur.fetchall():
                    if row['anticipacion_min'] not in ALLOWED_LEAD_MINUTES:
                        continue
                    for departure in calendar_departures(cur, row, now):
                        event = f"salida:{row['horario_id']}:{int(departure.timestamp())}"
                        payload = notification_payload(row | {'salida_en':departure}, now)
                        payload.update(event_id=event, expira_en=departure.isoformat())
                        count += _queue(cur,'android',row['device_id'],row['usuario_id'],row['horario_id'],event,
                                        payload,departure-timedelta(minutes=row['anticipacion_min']),departure,
                                        departure=departure)
            cur.execute('''SELECT a.*,h.origen,h.destino,e.nombre AS empresa
                           FROM avisos_operativos a JOIN horarios h ON h.id=a.horario_id
                           JOIN empresas e ON e.id=a.empresa_id
                           WHERE a.expira_en>%s AND h.activo AND e.estado_validacion='aprobada'
                             AND h.empresa_id=a.empresa_id
                             AND a.fecha<=%s;''', (now,now.astimezone(CHILE_TZ).date()+timedelta(days=1)))
            notices = cur.fetchall()
            for notice in notices:
                event = f"operativo:{notice['id']}"
                labels = {'retraso':'Retraso','cancelacion':'Cancelación','informacion':'Información'}
                title = 'BSRed · ' + labels[notice['tipo']]
                delay = f" · {notice['demora_min']} min" if notice['demora_min'] else ''
                payload = {'title':title,'body':(f"{notice['origen']} → {notice['destino']}{delay}. "
                                               +notice['mensaje'])[:1000],
                           'url':f"/?recorrido={notice['horario_id']}",'horario_id':notice['horario_id'],
                           'event_id':event,'tag':event.replace(':','-'),'tipo':notice['tipo'],
                           'expira_en':notice['expira_en'].isoformat()}
                available = max(notice['creado_en'],datetime.combine(notice['fecha'],clock_time(),CHILE_TZ).astimezone(timezone.utc))
                for channel, table, active in [('web','suscripciones_push','activa'),('android','dispositivos_android','activo')]:
                    if not (allow_web if channel=='web' else allow_android):
                        continue
                    # table/column provienen exclusivamente de la lista fija anterior.
                    cur.execute(f'''SELECT d.id,d.usuario_id FROM {table} d JOIN usuarios u ON u.id=d.usuario_id
                                    JOIN favoritos_recorridos f ON f.usuario_id=d.usuario_id
                                    WHERE d.{active} AND NOT u.suspendido AND NOT u.requiere_cambio_password
                                      AND f.horario_id=%s;''', (notice['horario_id'],))
                    for device in cur.fetchall():
                        count += _queue(cur,channel,device['id'],device['usuario_id'],notice['horario_id'],event,
                                        payload,available,notice['expira_en'],notice_id=notice['id'])
            cur.execute('''UPDATE entregas_notificaciones SET estado='expirado',reclamo_token=NULL,reclamo_hasta=NULL
                           WHERE expira_en<=%s AND estado IN ('pendiente','enviando');''',(now,))
            cur.execute('''DELETE FROM entregas_notificaciones WHERE expira_en<%s
                           AND estado IN ('enviado','expirado','error');''',(now-timedelta(days=30),))
        connection.commit()
        return count
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _claim_extended(get_connection, now, allow_web, allow_android):
    connection = get_connection()
    try:
        with _cursor(connection) as cur:
            cur.execute('''SELECT id FROM entregas_notificaciones
                           WHERE expira_en>%s AND disponible_en<=%s AND intentos<%s
                             AND ((canal='web' AND %s) OR (canal='android' AND %s))
                             AND ((estado='pendiente' AND reintentar_en<=%s)
                               OR (estado='enviando' AND reclamo_hasta<=%s))
                           ORDER BY expira_en,id FOR UPDATE SKIP LOCKED LIMIT 1;''',
                        (now,now,MAX_ATTEMPTS,allow_web,allow_android,now,now))
            row = cur.fetchone()
            if not row:
                connection.commit()
                return None
            cur.execute('''UPDATE entregas_notificaciones SET estado='enviando',reclamo_token=%s,
                           reclamo_hasta=%s,intentos=intentos+1 WHERE id=%s RETURNING *;''',
                        (secrets.token_urlsafe(32),now+timedelta(seconds=CLAIM_SECONDS),row['id']))
            job = dict(cur.fetchone())
        connection.commit()
        return job
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _current_extended(get_connection, job, now):
    connection = get_connection()
    try:
        with _cursor(connection) as cur:
            table, active, idcolumn = ('suscripciones_push','activa','suscripcion_id') if job['canal']=='web' else ('dispositivos_android','activo','dispositivo_android_id')
            cur.execute(f'''SELECT d.*,f.anticipacion_min,h.id AS horario_id,h.tipo,h.origen,h.destino,
                                   h.salida,h.llegada,h.dias,h.anden,h.empresa_id,e.nombre AS empresa
                            FROM {table} d JOIN usuarios u ON u.id=d.usuario_id
                            JOIN favoritos_recorridos f ON f.usuario_id=d.usuario_id
                            JOIN horarios h ON h.id=f.horario_id JOIN empresas e ON e.id=h.empresa_id
                            WHERE d.id=%s AND d.usuario_id=%s AND f.horario_id=%s AND d.{active}
                              AND NOT u.suspendido AND NOT u.requiere_cambio_password
                              AND h.activo AND e.estado_validacion='aprobada'
                              AND EXISTS(SELECT 1 FROM entregas_notificaciones q WHERE q.id=%s
                                         AND q.estado='enviando' AND q.reclamo_token=%s);''',
                        (job[idcolumn],job['usuario_id'],job['horario_id'],job['id'],job['reclamo_token']))
            device = cur.fetchone()
            if not device or now>=job['expira_en']:
                return None
            if job['aviso_id'] is not None:
                cur.execute('''SELECT id FROM avisos_operativos WHERE id=%s AND expira_en>%s AND empresa_id=%s;''',
                            (job['aviso_id'],now,device['empresa_id']))
                if not cur.fetchone():
                    return None
            else:
                if (normalize(device['origen'])!='panguipulli' or normalize(device['tipo']) not in ('salida','salidas')
                        or job['salida_en'] not in calendar_departures(cur,device,now)):
                    return None
            connection.commit()
            return dict(device)
    finally:
        connection.close()


def _finish_extended(get_connection, job, now, error=None, expired=False, defer_until=None, device=None):
    connection = get_connection()
    state, retry_at, code = 'enviado',None,None
    if expired or now>=job['expira_en']:
        state='expirado'
    elif defer_until:
        state,retry_at='pendiente',defer_until
    elif error:
        retry_at=now+timedelta(seconds=_retry_delay(error,job['intentos'],now))
        transient=error.status is None or error.status==429 or 500<=error.status<=599
        state=('pendiente' if transient and job['intentos']<MAX_ATTEMPTS and retry_at<job['expira_en']
               else 'expirado' if retry_at>=job['expira_en'] else 'error')
        code=f'HTTP {error.status}' if error.status else 'Entrega temporalmente no disponible'
    try:
        with _cursor(connection) as cur:
            table,idcolumn,active=('suscripciones_push','suscripcion_id','activa') if job['canal']=='web' else ('dispositivos_android','dispositivo_android_id','activo')
            if error and error.status in (404,410) and device:
                cur.execute(f'SELECT id FROM {table} WHERE id=%s FOR UPDATE;',(job[idcolumn],))
                cur.fetchone()
            cur.execute('''UPDATE entregas_notificaciones SET estado=%s,reclamo_token=NULL,reclamo_hasta=NULL,
                           reintentar_en=COALESCE(%s,reintentar_en),ultimo_error=%s,
                           enviado_en=CASE WHEN %s='enviado' THEN %s ELSE enviado_en END
                           WHERE id=%s AND estado='enviando' AND reclamo_token=%s;''',
                        (state,retry_at,code,state,now,job['id'],job['reclamo_token']))
            if cur.rowcount and error and error.status in (404,410) and device:
                stamp='actualizada_en' if job['canal']=='web' else 'actualizado_en'
                secret_field='endpoint' if job['canal']=='web' else 'token'
                cur.execute(f'''UPDATE {table} SET {active}=FALSE,{stamp}=%s
                                WHERE id=%s AND usuario_id=%s AND {secret_field}=%s;''',
                            (now,job[idcolumn],job['usuario_id'],device[secret_field]))
        connection.commit()
        return state
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def run_extended_cycle(get_connection, web_sender=None, fcm_sender=None, now=None, max_deliveries=40):
    allow_web=web_sender is not None or push_configuration()['disponible']
    allow_android=fcm_sender is not None or _fcm_settings() is not None
    counts={'preparados':0,'enviado':0,'pendiente':0,'expirado':0,'error':0}
    if not allow_web and not allow_android:
        return counts
    clock=(lambda:now) if now is not None else (lambda:datetime.now(timezone.utc))
    initial=clock()
    if initial.tzinfo is None:
        raise ValueError('Se necesita una fecha con zona horaria')
    web_sender=web_sender or send_web_push
    fcm_sender=fcm_sender or send_fcm
    counts['preparados']=_enqueue_extended(get_connection,initial,allow_web,allow_android)
    started=time.monotonic()
    for _ in range(max_deliveries):
        if time.monotonic()-started>=20:
            break
        job=_claim_extended(get_connection,clock(),allow_web,allow_android)
        if not job:
            break
        device=_current_extended(get_connection,job,clock())
        error,defer_until=None,None
        if device:
            payload=dict(job['payload'])
            if job['salida_en'] is not None:
                defer_until=job['salida_en']-timedelta(minutes=device['anticipacion_min'])
                if defer_until<=clock():
                    defer_until=None
                    payload.update(notification_payload(device|{'salida_en':job['salida_en']},clock()))
            if defer_until is None:
                try:
                    ttl=max(1,int((job['expira_en']-clock()).total_seconds()))
                    if job['canal']=='android':
                        payload['binding_id']=device['vinculo_id']
                        fcm_sender(device['token'],payload,ttl)
                    else:
                        web_sender({'endpoint':device['endpoint'],'keys':{'p256dh':device['p256dh'],'auth':device['auth']}},payload,ttl)
                except PushDeliveryError as failure:
                    error=failure
                except InvalidSubscription:
                    error=PushDeliveryError(410)
                except Exception:
                    error=PushDeliveryError()
        state=_finish_extended(get_connection,job,clock(),error=error,expired=device is None,
                               defer_until=defer_until,device=device)
        counts[state]+=1
    return counts
