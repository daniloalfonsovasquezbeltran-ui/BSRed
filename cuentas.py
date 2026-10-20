"""Cambio y recuperación de claves, con enlaces de un uso y correo TLS."""
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
import base64
import hashlib
import json
import os
import re
import secrets
import smtplib
import ssl
import threading
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

from flask import jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from choferes import stop_driver

SESSION_COOKIE = 'bsred_sesion'
SESSION_SECONDS = 24 * 60 * 60
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')
EMAIL_PATTERN = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')
RECOVERY_MESSAGE = 'Si existe una cuenta activa con ese correo, recibirás instrucciones. Si no llegan, contacta al administrador.'


def _valid_email(value):
    return (isinstance(value, str) and 3 <= len(value) <= 255
            and not any(ord(c) < 32 for c in value) and EMAIL_PATTERN.fullmatch(value))


def smtp_configuration():
    """Sólo configuración del operador; nunca deriva el enlace de cabeceras HTTP."""
    host = os.environ.get('SMTP_HOST', '').strip()
    username = os.environ.get('SMTP_USER', '').strip()
    password = os.environ.get('SMTP_PASSWORD', '')
    sender = os.environ.get('SMTP_FROM', '').strip()
    base = os.environ.get('PUBLIC_BASE_URL', 'https://bsred.onrender.com').strip().rstrip('/')
    try:
        port = int(os.environ.get('SMTP_PORT', '587'))
        origin = urlsplit(base)
        address = parseaddr(sender)[1]
        if (not host or any(c.isspace() or ord(c) < 32 for c in host)
                or not username or not password or port not in (465, 587)
                or not _valid_email(address) or '\r' in sender or '\n' in sender
                or origin.scheme != 'https' or not origin.hostname
                or origin.username or origin.password or origin.query or origin.fragment
                or origin.path not in ('', '/')):
            return None
        return {'host': host, 'port': port, 'username': username, 'password': password,
                'sender': sender, 'base_url': base}
    except (ValueError, TypeError):
        return None


def mail_configuration():
    key = os.environ.get('BREVO_API_KEY', '').strip()
    sender = os.environ.get('MAIL_FROM_EMAIL', '').strip()
    name = os.environ.get('MAIL_FROM_NAME', 'BSRed').strip()
    gmail = {field: os.environ.get(variable, '').strip() for field, variable in {
        'client_id': 'GMAIL_CLIENT_ID', 'client_secret': 'GMAIL_CLIENT_SECRET',
        'refresh_token': 'GMAIL_REFRESH_TOKEN'}.items()}
    gmail_requested = any(gmail.values())
    if gmail_requested and not all(gmail.values()):
        return None
    provider = dict(gmail, provider='gmail') if gmail_requested else (
        {'provider': 'brevo', 'api_key': key} if key else None)
    if provider and _valid_email(sender) and 1 <= len(name) <= 120 and not any(ord(c) < 32 for c in name):
        base = os.environ.get('PUBLIC_BASE_URL', 'https://bsred.onrender.com').strip().rstrip('/')
        try:
            parts = urlsplit(base)
            if (parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
                    and not parts.query and not parts.fragment and parts.path in ('', '/')):
                return dict(provider, sender=sender, name=name, base_url=base)
        except ValueError:
            pass
    if gmail_requested:
        return None
    smtp = smtp_configuration()
    return dict(smtp, provider='smtp') if smtp else None


def _email_content(token, config):
    link = config['base_url'] + '/usuario#restablecer=' + token
    return ('Recibimos una solicitud para restablecer tu contraseña de BSRed.\n\n'
            'Abre este enlace, válido durante 30 minutos y para un solo uso:\n' + link
            + '\n\nSi no hiciste esta solicitud, puedes ignorar el correo.\n')


def _mail_opener():
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    return build_opener(HTTPSHandler(context=ssl.create_default_context()), NoRedirect())


def _deliver_brevo_email(email, token, config):
    payload = {'sender': {'email': config['sender'], 'name': config['name']},
               'to': [{'email': email}], 'subject': 'Restablece tu contraseña de BSRed',
               'textContent': _email_content(token, config)}
    req = Request('https://api.brevo.com/v3/smtp/email', method='POST',
                  headers={'api-key': config['api_key'], 'Content-Type': 'application/json',
                           'Accept': 'application/json'},
                  data=json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    opener = _mail_opener()
    with opener.open(req, timeout=12) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError('El proveedor de correo no aceptó el envío')
        response.read(65536)


def _deliver_gmail_email(email, token, config):
    """Envía desde la cuenta autorizada usando sólo el permiso gmail.send."""
    opener = _mail_opener()
    authorization = Request('https://oauth2.googleapis.com/token', method='POST',
                            headers={'Content-Type': 'application/x-www-form-urlencoded',
                                     'Accept': 'application/json'},
                            data=urlencode({'grant_type': 'refresh_token',
                                            'client_id': config['client_id'],
                                            'client_secret': config['client_secret'],
                                            'refresh_token': config['refresh_token']}).encode('ascii'))
    with opener.open(authorization, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError('Google no autorizó el envío de recuperación')
        credentials = json.loads(response.read(65536))
    access = credentials.get('access_token') if isinstance(credentials, dict) else None
    token_type = credentials.get('token_type', '') if isinstance(credentials, dict) else ''
    if (not isinstance(access, str) or not access or len(access) > 8192 or not access.isascii()
            or any(c.isspace() or ord(c) < 32 for c in access)
            or not isinstance(token_type, str) or token_type.lower() != 'bearer'):
        raise RuntimeError('Google no devolvió una autorización válida')

    message = EmailMessage()
    message['From'] = formataddr((config['name'], config['sender']))
    message['To'] = email
    message['Subject'] = 'Restablece tu contraseña de BSRed'
    message.set_content(_email_content(token, config))
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')
    req = Request('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', method='POST',
                  headers={'Authorization': 'Bearer ' + access, 'Content-Type': 'application/json',
                           'Accept': 'application/json'},
                  data=json.dumps({'raw': raw}).encode('utf-8'))
    with opener.open(req, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError('Gmail no aceptó el envío de recuperación')
        result = json.loads(response.read(65536))
    if not isinstance(result, dict) or not isinstance(result.get('id'), str) or not result['id'].strip():
        raise RuntimeError('Gmail no confirmó el envío de recuperación')


def _deliver_reset_email(email, token, config):
    if config.get('provider') == 'gmail':
        return _deliver_gmail_email(email, token, config)
    if config.get('provider') == 'brevo':
        return _deliver_brevo_email(email, token, config)
    message = EmailMessage()
    message['From'] = config['sender']
    message['To'] = email
    message['Subject'] = 'Restablece tu contraseña de BSRed'
    message.set_content(_email_content(token, config))
    context = ssl.create_default_context()
    if config['port'] == 465:
        with smtplib.SMTP_SSL(config['host'], 465, timeout=12, context=context) as server:
            server.login(config['username'], config['password'])
            server.send_message(message)
    else:
        with smtplib.SMTP(config['host'], 587, timeout=12) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(config['username'], config['password'])
            server.send_message(message)


def _queue_email(app, email, token, config):
    def deliver():
        try:
            _deliver_reset_email(email, token, config)
        except Exception as error:
            # Las excepciones SMTP pueden incluir direcciones o credenciales.
            app.logger.error('No se pudo entregar recuperación de cuenta: %s', type(error).__name__)
    threading.Thread(target=deliver, name='bsred-recuperacion', daemon=True).start()


def _rate_limit(cur, scope, value, maximum, seconds):
    digest = hashlib.sha256((scope + '\0' + str(value)).encode('utf-8')).hexdigest()
    cur.execute('''INSERT INTO limites_cuentas (bucket_hash, intentos, expira_en)
        VALUES (%s, 1, CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'))
        ON CONFLICT (bucket_hash) DO UPDATE SET
            intentos = CASE WHEN limites_cuentas.expira_en <= CURRENT_TIMESTAMP
                            THEN 1 ELSE LEAST(limites_cuentas.intentos + 1, 1000000) END,
            expira_en = CASE WHEN limites_cuentas.expira_en <= CURRENT_TIMESTAMP
                            THEN EXCLUDED.expira_en ELSE limites_cuentas.expira_en END
        RETURNING intentos;''', (digest, seconds))
    return cur.fetchone()['intentos'] <= maximum


def _cleanup(cur):
    for table, primary_key in [('limites_cuentas', 'bucket_hash'), ('password_reset_tokens', 'token_hash')]:
        # Nombres internos constantes; nunca esperar por filas usadas por otra recuperación.
        cur.execute(f'''WITH vencidos AS (SELECT {primary_key} FROM {table}
            WHERE expira_en < CURRENT_TIMESTAMP - INTERVAL '1 day'
            LIMIT 200 FOR UPDATE SKIP LOCKED)
            DELETE FROM {table} WHERE {primary_key} IN (SELECT {primary_key} FROM vencidos);''')


def _public_user(cur, user_id):
    cur.execute('''SELECT id, nombre, email, LOWER(TRIM(rol)) AS rol, empresa_id,
        requiere_cambio_password FROM usuarios WHERE id=%s;''', (user_id,))
    return dict(cur.fetchone())


def _new_password_valid(value):
    return (isinstance(value, str) and 10 <= len(value) <= 128
            and not any(ord(c) < 32 for c in value))


def _update_password(cur, user_id, password):
    cur.execute('''UPDATE usuarios SET password='', password_hash=%s,
        requiere_cambio_password=FALSE WHERE id=%s;''', (generate_password_hash(password), user_id))
    stop_driver(cur, user_id)
    from notificaciones import revoke_android_user
    revoke_android_user(cur, user_id)
    cur.execute('''UPDATE entregas_notificaciones SET estado='expirado', reclamo_token=NULL, reclamo_hasta=NULL
        WHERE usuario_id=%s AND estado IN ('pendiente','enviando');''', (user_id,))
    cur.execute('''UPDATE password_reset_tokens SET usado_en=CURRENT_TIMESTAMP
        WHERE usuario_id=%s AND usado_en IS NULL;''', (user_id,))


def register_accounts(app, connection, current_user, token_hash):
    @app.before_request
    def protect_accounts():
        if request.method != 'POST' or not request.path.startswith('/api/cuentas/'):
            return None
        origin = request.headers.get('Origin')
        if (origin and origin.rstrip('/') != request.host_url.rstrip('/')) or (
                not origin and request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site')):
            return jsonify(success=False, message='La solicitud debe realizarse desde BSRed.'), 403
        if request.content_length and request.content_length > 8192:
            return jsonify(success=False, message='Solicitud demasiado grande.'), 413
        if not request.is_json:
            return jsonify(success=False, message='La solicitud debe contener datos JSON.'), 400

    @app.route('/api/cuentas/config')
    def accounts_configuration():
        return jsonify(success=True, recuperacion_disponible=mail_configuration() is not None)

    @app.route('/api/cuentas/password', methods=['POST'])
    def change_password():
        data = request.get_json(silent=True)
        if (not isinstance(data, dict) or not isinstance(data.get('password_actual'), str)
                or not 1 <= len(data['password_actual']) <= 128
                or not _new_password_valid(data.get('password_nuevo'))):
            return jsonify(success=False, message='Indica tu contraseña actual y una nueva de 10 a 128 caracteres.'), 400
        conn = None
        try:
            conn = connection()
            with conn.cursor() as cur:
                user = current_user(cur, lock=True)
                if not user:
                    return jsonify(success=False, message='Inicia sesión con una cuenta activa.'), 401
                allowed = _rate_limit(cur, 'cambio-usuario', user['id'], 5, 900)
                ip_allowed = _rate_limit(cur, 'cambio-ip', request.remote_addr or '', 20, 900)
                if not allowed or not ip_allowed:
                    conn.commit()
                    return jsonify(success=False, message='Demasiados intentos. Intenta más tarde.'), 429
                cur.execute('SELECT password_hash FROM usuarios WHERE id=%s;', (user['id'],))
                stored = cur.fetchone()['password_hash']
                if stored:
                    valid = check_password_hash(stored, data['password_actual'])
                    reused = check_password_hash(stored, data['password_nuevo']) if valid else False
                else:
                    # Comparación dentro de PostgreSQL para cuentas antiguas: no leer la clave.
                    cur.execute('SELECT 1 FROM usuarios WHERE id=%s AND password=%s;',
                                (user['id'], data['password_actual'].strip()))
                    valid = bool(cur.fetchone())
                    cur.execute('SELECT 1 FROM usuarios WHERE id=%s AND password=%s;',
                                (user['id'], data['password_nuevo']))
                    reused = bool(cur.fetchone())
                if not valid:
                    conn.commit()
                    return jsonify(success=False, message='La contraseña actual no es correcta.'), 400
                if reused:
                    conn.commit()
                    return jsonify(success=False, message='Elige una contraseña diferente.'), 400
                cur.execute("SELECT id FROM viajes WHERE chofer_id=%s AND estado='en_curso';", (user['id'],))
                if cur.fetchone():
                    conn.commit()
                    return jsonify(success=False, message='Finaliza el viaje antes de cambiar tu contraseña.'), 409
                _update_password(cur, user['id'], data['password_nuevo'])
                token = secrets.token_urlsafe(32)
                cur.execute('''INSERT INTO sesiones_web (token_hash, usuario_id, expira_en)
                    VALUES (%s, %s, CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'));''',
                    (token_hash(token), user['id'], SESSION_SECONDS))
                public = _public_user(cur, user['id'])
            conn.commit()
            response = jsonify(success=True, user=public, message='Contraseña actualizada. Las otras sesiones se han cerrado.')
            response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                                secure=request.is_secure, samesite='Lax', path='/')
            return response
        except Exception as error:
            app.logger.error('No se pudo cambiar contraseña: %s', type(error).__name__)
            return jsonify(success=False, message='No se pudo cambiar la contraseña. Intenta nuevamente.'), 503
        finally:
            if conn:
                conn.close()

    @app.route('/api/cuentas/recuperar', methods=['POST'])
    def recover_account():
        data = request.get_json(silent=True)
        email = data.get('email', '').strip().lower() if isinstance(data, dict) and isinstance(data.get('email'), str) else ''
        if not _valid_email(email):
            return jsonify(success=False, message='Indica un correo válido.'), 400
        config = mail_configuration()
        if config is None:
            return jsonify(success=False, message='La recuperación por correo todavía no está disponible. Contacta al administrador.'), 503
        conn = None
        mail = None
        try:
            conn = connection()
            with conn.cursor() as cur:
                allowed_ip = _rate_limit(cur, 'recuperar-ip', request.remote_addr or '', 5, 900)
                allowed_email = _rate_limit(cur, 'recuperar-email', email, 3, 1800)
                if allowed_ip and allowed_email:
                    cur.execute('''SELECT id, email FROM usuarios WHERE LOWER(TRIM(email))=%s
                                   AND suspendido=FALSE FOR UPDATE;''', (email,))
                    user = cur.fetchone()
                    if user:
                        token = secrets.token_urlsafe(32)
                        cur.execute('''UPDATE password_reset_tokens SET usado_en=CURRENT_TIMESTAMP
                            WHERE usuario_id=%s AND usado_en IS NULL;''', (user['id'],))
                        cur.execute('''INSERT INTO password_reset_tokens (token_hash, usuario_id, expira_en)
                            VALUES (%s, %s, CURRENT_TIMESTAMP + INTERVAL '30 minutes');''',
                            (token_hash(token), user['id']))
                        mail = (user['email'], token)
                _cleanup(cur)
            conn.commit()
            if mail:
                _queue_email(app, *mail, config)
            return jsonify(success=True, message=RECOVERY_MESSAGE), 202
        except Exception as error:
            app.logger.error('No se pudo solicitar recuperación: %s', type(error).__name__)
            return jsonify(success=False, message='El servicio no está disponible en este momento. Intenta más tarde.'), 503
        finally:
            if conn:
                conn.close()

    @app.route('/api/cuentas/restablecer', methods=['POST'])
    def reset_password():
        data = request.get_json(silent=True)
        if (not isinstance(data, dict) or not isinstance(data.get('token'), str)
                or not TOKEN_PATTERN.fullmatch(data['token']) or not _new_password_valid(data.get('password'))):
            return jsonify(success=False, message='El enlace o la contraseña no son válidos.'), 400
        conn = None
        try:
            conn = connection()
            with conn.cursor() as cur:
                if not _rate_limit(cur, 'restablecer-ip', request.remote_addr or '', 10, 900):
                    conn.commit()
                    return jsonify(success=False, message='Demasiados intentos. Intenta más tarde.'), 429
                digest = token_hash(data['token'])
                cur.execute('SELECT usuario_id FROM password_reset_tokens WHERE token_hash=%s;', (digest,))
                row = cur.fetchone()
                user_id = row['usuario_id'] if row else None
                if user_id is not None:
                    cur.execute('SELECT id FROM usuarios WHERE id=%s AND suspendido=FALSE FOR UPDATE;', (user_id,))
                    user = cur.fetchone()
                else:
                    user = None
                if user:
                    cur.execute('''SELECT token_hash FROM password_reset_tokens WHERE token_hash=%s
                        AND usuario_id=%s AND usado_en IS NULL AND expira_en>CURRENT_TIMESTAMP FOR UPDATE;''',
                        (digest, user_id))
                    valid = bool(cur.fetchone())
                else:
                    valid = False
                if not valid:
                    conn.commit()
                    return jsonify(success=False, message='El enlace no es válido o ya venció. Solicita uno nuevo.'), 400
                _update_password(cur, user_id, data['password'])
            conn.commit()
            response = jsonify(success=True, message='Contraseña restablecida. Inicia sesión con tu nueva contraseña.')
            response.delete_cookie(SESSION_COOKIE, path='/', httponly=True, secure=request.is_secure, samesite='Lax')
            return response
        except Exception as error:
            app.logger.error('No se pudo restablecer contraseña: %s', type(error).__name__)
            return jsonify(success=False, message='No se pudo restablecer la contraseña. Intenta más tarde.'), 503
        finally:
            if conn:
                conn.close()
