"""Favoritos privados y asociación de Web Push al navegador autenticado."""
from datetime import time
from functools import wraps
import secrets

from flask import jsonify, make_response, request

from avisos import (ALLOWED_LEAD_MINUTES, InvalidSubscription, endpoint_hash,
                    push_configuration, validate_push_subscription)
from rutas import normalize, operating_days

DEVICE_COOKIE = 'bsred_push_dispositivo'
DEVICE_SECONDS = 365 * 24 * 60 * 60


def revoke_device(cur, device_hash):
    cur.execute('''
        UPDATE suscripciones_push SET activa = FALSE, actualizada_en = CURRENT_TIMESTAMP
        WHERE dispositivo_hash = %s RETURNING id;
    ''', (device_hash,))
    row = cur.fetchone()
    if row:
        cur.execute('''
            UPDATE avisos_salida SET estado = 'expirado', reclamo_token = NULL, reclamo_hasta = NULL
            WHERE suscripcion_id = %s AND estado IN ('pendiente', 'enviando');
        ''', (row['id'],))


def schedule_alert_availability(horario):
    if normalize(horario['tipo']) not in ('salida', 'salidas') or normalize(horario['origen']) != 'panguipulli':
        return False, 'Los avisos están disponibles para salidas desde Panguipulli.'
    if horario['salida'] is None or operating_days(horario['dias']) is None:
        return False, 'Falta un horario de salida o días de operación válidos.'
    return True, None


def favorite_json(row):
    horario = {key: row[key] for key in ('id', 'tipo', 'empresa', 'origen', 'destino', 'salida', 'llegada', 'dias', 'anden')}
    for key in ('salida', 'llegada'):
        value = horario[key]
        horario[key] = value.strftime('%H:%M') if isinstance(value, time) else None
    available, reason = schedule_alert_availability(horario)
    return {'horario_id': row['horario_id'], 'minutos_antes': row['anticipacion_min'],
            'horario': horario, 'avisos_disponibles': available, 'motivo_aviso': reason}


def register_favorites(app, get_connection, current_user, cookie_token, token_hash, set_private_cookie):
    @app.before_request
    def protect_favorite_mutations():
        if request.method not in ('POST', 'PUT', 'DELETE', 'PATCH'):
            return None
        protected = (request.path.startswith('/api/favoritos')
                     or request.path.startswith('/api/notificaciones')
                     or request.path in ('/api/logout', '/api/login'))
        if not protected:
            return None
        origin = request.headers.get('Origin')
        if origin:
            if origin.rstrip('/') != request.host_url.rstrip('/'):
                return jsonify(success=False, message='La solicitud debe realizarse desde BSRed.'), 403
        elif request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
            return jsonify(success=False, message='La solicitud debe realizarse desde BSRed.'), 403
        if request.content_length and request.content_length > 8192:
            return jsonify(success=False, message='Solicitud demasiado grande.'), 413
        if request.path not in ('/api/logout',) and not request.is_json:
            return jsonify(success=False, message='La solicitud debe contener datos JSON.'), 400

    def account_api(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            if not cookie_token('bsred_sesion'):
                return jsonify(success=False, message='Inicia sesión para usar tus favoritos.'), 401
            conn = None
            try:
                conn = get_connection()
                with conn.cursor() as cur:
                    user = current_user(cur, lock=request.method != 'GET')
                    if not user:
                        return jsonify(success=False, message='Tu sesión ha expirado. Inicia sesión nuevamente.'), 401
                    response = make_response(func(cur, user, *args, **kwargs))
                if response.status_code < 400:
                    conn.commit()
                else:
                    conn.rollback()
                return response
            except Exception as error:
                app.logger.error('No se pudo consultar favoritos o avisos: %s', type(error).__name__)
                return jsonify(success=False, message='No se pudo completar la operación. Inténtalo nuevamente.'), 503
            finally:
                if conn:
                    conn.close()
        return wrapped

    @app.route('/api/favoritos', methods=['GET'])
    @account_api
    def list_favorites(cur, user):
        cur.execute('''
            SELECT f.horario_id, f.anticipacion_min, h.id, h.tipo, h.origen, h.destino,
                   h.salida, h.llegada, h.dias, h.anden, e.nombre AS empresa
            FROM favoritos_recorridos f JOIN horarios h ON h.id = f.horario_id
            LEFT JOIN empresas e ON e.id = h.empresa_id
            WHERE f.usuario_id = %s ORDER BY h.salida, h.id;
        ''', (user['id'],))
        return jsonify(success=True, favoritos=[favorite_json(row) for row in cur.fetchall()])

    @app.route('/api/favoritos/<int:horario_id>', methods=['PUT', 'DELETE'])
    @account_api
    def update_favorite(cur, user, horario_id):
        if request.method == 'DELETE':
            cur.execute('DELETE FROM favoritos_recorridos WHERE usuario_id = %s AND horario_id = %s;',
                        (user['id'], horario_id))
            cur.execute('''
                UPDATE avisos_salida a SET estado = 'expirado', reclamo_token = NULL, reclamo_hasta = NULL
                FROM suscripciones_push s WHERE a.suscripcion_id = s.id AND s.usuario_id = %s
                AND a.horario_id = %s AND a.estado IN ('pendiente', 'enviando');
            ''', (user['id'], horario_id))
            return jsonify(success=True)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(success=False, message='Indica la anticipación del aviso.'), 400
        lead = data.get('minutos_antes', 10)
        if type(lead) is not int or lead not in ALLOWED_LEAD_MINUTES:
            return jsonify(success=False, message='Elige 5, 10 o 15 minutos de anticipación.'), 400
        cur.execute('SELECT id FROM horarios WHERE id = %s;', (horario_id,))
        if not cur.fetchone():
            return jsonify(success=False, message='El recorrido ya no está disponible.'), 404
        cur.execute('''
            INSERT INTO favoritos_recorridos (usuario_id, horario_id, anticipacion_min)
            VALUES (%s, %s, %s) ON CONFLICT (usuario_id, horario_id)
            DO UPDATE SET anticipacion_min = EXCLUDED.anticipacion_min;
        ''', (user['id'], horario_id, lead))
        return jsonify(success=True, horario_id=horario_id, minutos_antes=lead)

    @app.route('/api/notificaciones/config', methods=['GET'])
    def notification_config():
        config = push_configuration()
        return jsonify(success=True, disponible=config['disponible'],
                       public_key=config['clave_publica'], minutos_antes=[5, 10, 15])

    @app.route('/api/notificaciones/suscripciones', methods=['POST', 'DELETE'])
    @account_api
    def manage_subscription(cur, user):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(success=False, message='Los datos del dispositivo no son válidos.'), 400
        device_token = cookie_token(DEVICE_COOKIE)
        if request.method == 'DELETE':
            try:
                digest = endpoint_hash(data.get('endpoint'))
            except InvalidSubscription as error:
                return jsonify(success=False, message=str(error)), 400
            if device_token:
                cur.execute('''
                    SELECT id FROM suscripciones_push WHERE usuario_id = %s
                    AND endpoint_hash = %s AND dispositivo_hash = %s FOR UPDATE;
                ''', (user['id'], digest, token_hash(device_token)))
                if cur.fetchone():
                    revoke_device(cur, token_hash(device_token))
            return jsonify(success=True)
        if not push_configuration()['disponible']:
            return jsonify(success=False, message='Los avisos al dispositivo todavía no están disponibles.'), 503
        try:
            subscription = validate_push_subscription(data.get('subscription'))
        except InvalidSubscription as error:
            return jsonify(success=False, message=str(error)), 400
        device_token = device_token or secrets.token_urlsafe(32)
        device_hash = token_hash(device_token)
        cur.execute('''
            SELECT id, dispositivo_hash FROM suscripciones_push WHERE endpoint_hash = %s FOR UPDATE;
        ''', (subscription['endpoint_hash'],))
        existing = cur.fetchone()
        if existing and existing['dispositivo_hash'].strip() != device_hash:
            return jsonify(success=False, message='Desactiva y vuelve a activar los avisos en este dispositivo.'), 409
        # Un mismo navegador conserva su vínculo privado al cambiar de cuenta.
        # Revocar primero evita que la cola anterior se entregue a la cuenta nueva.
        cur.execute('''
            SELECT id, usuario_id, endpoint_hash, activa FROM suscripciones_push
            WHERE dispositivo_hash = %s FOR UPDATE;
        ''', (device_hash,))
        previous = cur.fetchone()
        if previous and (previous['usuario_id'] != user['id']
                         or previous['endpoint_hash'].strip() != subscription['endpoint_hash']):
            cur.execute("DELETE FROM avisos_salida WHERE suscripcion_id = %s;", (previous['id'],))
        cur.execute('''
            INSERT INTO suscripciones_push
                (usuario_id, endpoint, endpoint_hash, p256dh, auth, dispositivo_hash, activa)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (dispositivo_hash) DO UPDATE SET
                usuario_id = EXCLUDED.usuario_id, endpoint = EXCLUDED.endpoint,
                endpoint_hash = EXCLUDED.endpoint_hash, p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
                activa = TRUE, actualizada_en = CURRENT_TIMESTAMP;
        ''', (user['id'], subscription['endpoint'], subscription['endpoint_hash'],
              subscription['p256dh'], subscription['auth'], device_hash))
        response = jsonify(success=True)
        set_private_cookie(response, DEVICE_COOKIE, device_token, DEVICE_SECONDS)
        return response

    @app.route('/api/notificaciones/suscripciones/estado', methods=['POST'])
    @account_api
    def subscription_state(cur, user):
        data = request.get_json(silent=True)
        try:
            digest = endpoint_hash(data.get('endpoint') if isinstance(data, dict) else None)
        except InvalidSubscription as error:
            return jsonify(success=False, message=str(error)), 400
        device = cookie_token(DEVICE_COOKIE)
        if not device:
            return jsonify(success=True, activa=False)
        cur.execute('''
            SELECT activa FROM suscripciones_push WHERE usuario_id = %s AND endpoint_hash = %s
            AND dispositivo_hash = %s;
        ''', (user['id'], digest, token_hash(device)))
        row = cur.fetchone()
        return jsonify(success=True, activa=bool(row and row['activa']))
