"""Recordatorios Web Push de salidas programadas, con cola durable en PostgreSQL.

El aviso depende del horario publicado; no afirma que un bus esté saliendo.
Las suscripciones y las claves VAPID nunca se incluyen en logs ni respuestas.
"""
import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from rutas import CHILE_TZ, _local_datetime, _parse_clock, normalize, operating_days


ALLOWED_LEAD_MINUTES = (5, 10, 15)
DEFAULT_LEAD_MINUTES = 10
POLL_SECONDS = 20
CLAIM_SECONDS = 90
MAX_ATTEMPTS = 6
BASE64URL = re.compile(r'^[A-Za-z0-9_-]+={0,2}$')
WINDOWS_PUSH_HOST = re.compile(r'^[a-z0-9-]+\.notify\.windows\.com$')
PUSH_HOSTS = {
    'fcm.googleapis.com',
    'updates.push.services.mozilla.com',
    'web.push.apple.com',
}
_scheduler_lock = threading.Lock()
_scheduler_threads = {}


class InvalidSubscription(ValueError):
    """Datos de PushSubscription inválidos o un destino no autorizado."""


class PushDeliveryError(RuntimeError):
    """Error de entrega sin exponer la URL privada del dispositivo."""

    def __init__(self, status=None, retry_after=None):
        self.status = status
        self.retry_after = retry_after
        super().__init__('No se pudo entregar el recordatorio')


def _decode_base64url(value, maximum=1024):
    if (not isinstance(value, str) or len(value) > maximum
            or not BASE64URL.fullmatch(value)):
        raise ValueError('Clave inválida')
    try:
        return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError('Clave inválida') from error


def _encode_base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def validate_push_endpoint(endpoint):
    """Sólo admite servicios Web Push conocidos, HTTPS y sin redirecciones.

    Una lista cerrada de hosts impide que una suscripción convierta al backend
    en un cliente de URLs arbitrarias, direcciones internas o metadatos cloud.
    """
    if (not isinstance(endpoint, str) or not 20 <= len(endpoint) <= 4096
            or any(character.isspace() or ord(character) < 32 for character in endpoint)
            or '\\' in endpoint):
        raise InvalidSubscription('El destino de notificaciones no es válido')
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname or ''
        permitted = host in PUSH_HOSTS or bool(WINDOWS_PUSH_HOST.fullmatch(host))
        if (parts.scheme != 'https' or not permitted or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None
                or parts.fragment or not parts.path or parts.path == '/'):
            raise ValueError('Destino no permitido')
    except (ValueError, UnicodeError) as error:
        raise InvalidSubscription('El servicio de notificaciones no está permitido') from error
    return endpoint


def endpoint_hash(endpoint):
    return hashlib.sha256(validate_push_endpoint(endpoint).encode('utf-8')).hexdigest()


def validate_push_subscription(data):
    if not isinstance(data, dict) or not isinstance(data.get('keys'), dict):
        raise InvalidSubscription('La suscripción del dispositivo no es válida')
    endpoint = validate_push_endpoint(data.get('endpoint'))
    try:
        public = _decode_base64url(data['keys'].get('p256dh'))
        auth = _decode_base64url(data['keys'].get('auth'))
        if len(public) != 65 or public[0] != 4 or len(auth) != 16:
            raise ValueError('Tamaño de clave inválido')
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
    except (ValueError, TypeError) as error:
        raise InvalidSubscription('Las claves del dispositivo no son válidas') from error
    return {'endpoint': endpoint,
            'endpoint_hash': hashlib.sha256(endpoint.encode('utf-8')).hexdigest(),
            'p256dh': _encode_base64url(public), 'auth': _encode_base64url(auth)}


def _vapid_settings():
    public_string = os.environ.get('VAPID_PUBLIC_KEY', '').strip()
    private_string = os.environ.get('VAPID_PRIVATE_KEY', '').strip()
    subject = os.environ.get('VAPID_SUBJECT', 'https://bsred.onrender.com').strip()
    enabled = os.environ.get('PUSH_ENABLED', 'true').strip().lower() not in ('0', 'false', 'no', 'off')
    if not enabled or not public_string or not private_string:
        return None
    try:
        public = _decode_base64url(public_string)
        raw_private = _decode_base64url(private_string)
        if len(raw_private) == 32:
            private = ec.derive_private_key(int.from_bytes(raw_private, 'big'), ec.SECP256R1())
        else:
            private = serialization.load_der_private_key(raw_private, password=None)
        if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
            return None
        matching_public = private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        if len(public) != 65 or not secrets.compare_digest(public, matching_public):
            return None
        uri = urlsplit(subject)
        if not ((uri.scheme == 'https' and uri.hostname and not uri.username and not uri.password)
                or (uri.scheme == 'mailto' and '@' in uri.path)):
            return None
        return {'public': _encode_base64url(public),
                'private': _encode_base64url(private.private_numbers().private_value.to_bytes(32, 'big')),
                'subject': subject}
    except (ValueError, TypeError):
        return None


def push_configuration():
    settings = _vapid_settings()
    return {'habilitado': settings is not None,
            'disponible': settings is not None,
            'clave_publica': settings['public'] if settings else None,
            'anticipaciones_min': list(ALLOWED_LEAD_MINUTES),
            'anticipacion_default_min': DEFAULT_LEAD_MINUTES,
            'zona_horaria': 'America/Santiago'}


def generate_vapid_keys():
    """Genera el par para variables privadas; el llamador decide dónde guardarlo."""
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {'VAPID_PUBLIC_KEY': _encode_base64url(public),
            'VAPID_PRIVATE_KEY': _encode_base64url(private.private_numbers().private_value.to_bytes(32, 'big'))}


def departure_windows(salida, dias, now=None):
    """Salidas de hoy y mañana en Chile, incluso sin hora de llegada."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Se necesita una fecha con zona horaria')
    try:
        clock = _parse_clock(salida)
    except (ValueError, TypeError):
        return []
    weekdays = operating_days(dias)
    if weekdays is None:
        return []
    today = now.astimezone(CHILE_TZ).date()
    departures = []
    for offset in (0, 1):
        day = today + timedelta(days=offset)
        if day.weekday() in weekdays:
            local = _local_datetime(day, clock)
            if local is not None:
                departure = local.astimezone(timezone.utc)
                if departure > now:
                    departures.append(departure)
    return departures


def notification_payload(job, now=None):
    now = now or datetime.now(timezone.utc)
    departure = job['salida_en']
    minute = max(1, math.ceil((departure - now).total_seconds() / 60))
    departure_label = departure.astimezone(CHILE_TZ).strftime('%H:%M')
    route_id = job['horario_id']
    company = str(job.get('empresa') or 'Bus')[:100]
    destination = str(job.get('destino') or 'tu destino')[:160]
    platform = str(job.get('anden') or '').strip()[:40]
    platform_label = f' · Andén {platform}' if platform else ''
    return {'title': 'BSRed · Salida programada',
            'body': (f'Panguipulli → {destination} · {company} · Salida {departure_label}'
                     f'{platform_label} · Faltan {minute} min.'),
            'tag': f'bsred-salida-{route_id}-{int(departure.timestamp())}',
            'url': f'/?recorrido={route_id}', 'horario_id': route_id,
            'salida_en': departure.isoformat(), 'tipo': 'salida_programada'}


def send_web_push(subscription, payload, ttl):
    """Cifra con RFC 8188 y VAPID; no sigue redirects ni desactiva TLS."""
    import requests
    from pywebpush import WebPushException, webpush

    settings = _vapid_settings()
    if settings is None:
        raise PushDeliveryError()
    clean = validate_push_subscription(subscription)

    class SafePushSession(requests.Session):
        def request(self, method, url, **kwargs):
            validate_push_endpoint(url)
            kwargs['allow_redirects'] = False
            kwargs['verify'] = True
            return super().request(method, url, **kwargs)

    info = {'endpoint': clean['endpoint'],
            'keys': {'p256dh': clean['p256dh'], 'auth': clean['auth']}}
    headers = {'Urgency': 'high', 'Topic': payload['tag'][-32:]}
    host = urlsplit(clean['endpoint']).hostname or ''
    if WINDOWS_PUSH_HOST.fullmatch(host):
        headers['X-WNS-Type'] = 'wns/raw'
    try:
        with SafePushSession() as session:
            response = webpush(info, data=json.dumps(payload, ensure_ascii=False),
                               vapid_private_key=settings['private'],
                               vapid_claims={'sub': settings['subject']},
                               requests_session=session, headers=headers,
                               timeout=15, ttl=max(1, min(900, int(ttl))))
            if not 200 <= response.status_code <= 202:
                raise PushDeliveryError(response.status_code)
    except WebPushException as error:
        response = error.response
        status = response.status_code if response is not None else None
        retry_after = response.headers.get('Retry-After') if response is not None else None
        raise PushDeliveryError(status, retry_after) from None
    except requests.RequestException:
        raise PushDeliveryError() from None


def _cursor(connection):
    from psycopg2.extras import RealDictCursor
    return connection.cursor(cursor_factory=RealDictCursor)


def _enqueue_departures(get_connection, now):
    connection = get_connection()
    queued = 0
    try:
        with _cursor(connection) as cursor:
            cursor.execute('''
                SELECT p.id AS suscripcion_id, f.horario_id, f.anticipacion_min,
                       h.origen, h.tipo, h.salida, h.dias
                FROM favoritos_recorridos f
                JOIN suscripciones_push p ON p.usuario_id = f.usuario_id AND p.activa
                JOIN horarios h ON h.id = f.horario_id
                WHERE LOWER(TRIM(h.origen)) = 'panguipulli'
                      AND LOWER(TRIM(h.tipo)) IN ('salida', 'salidas');
            ''')
            favorites = cursor.fetchall()
            for favorite in favorites:
                if favorite['anticipacion_min'] not in ALLOWED_LEAD_MINUTES:
                    continue
                for departure in departure_windows(favorite['salida'], favorite['dias'], now):
                    if departure - now > timedelta(days=1):
                        continue
                    notify_at = departure - timedelta(minutes=favorite['anticipacion_min'])
                    cursor.execute('''
                        INSERT INTO avisos_salida
                            (suscripcion_id, horario_id, salida_en, avisar_en, estado,
                             intentos, reintentar_en)
                        VALUES (%s, %s, %s, %s, 'pendiente', 0, %s)
                        ON CONFLICT (suscripcion_id, horario_id, salida_en)
                        DO UPDATE SET avisar_en = EXCLUDED.avisar_en,
                            estado = CASE WHEN avisos_salida.estado = 'expirado'
                                          AND avisos_salida.enviado_en IS NULL
                                          THEN 'pendiente' ELSE avisos_salida.estado END,
                            reintentar_en = CASE WHEN avisos_salida.estado = 'expirado'
                                          AND avisos_salida.enviado_en IS NULL
                                          THEN EXCLUDED.reintentar_en
                                          WHEN avisos_salida.intentos = 0
                                          THEN EXCLUDED.reintentar_en
                                          ELSE avisos_salida.reintentar_en END
                        WHERE avisos_salida.estado = 'pendiente'
                            OR (avisos_salida.estado = 'expirado'
                                AND avisos_salida.enviado_en IS NULL
                                AND avisos_salida.reintentar_en < avisos_salida.salida_en
                                AND avisos_salida.intentos < %s);
                    ''', (favorite['suscripcion_id'], favorite['horario_id'],
                          departure, notify_at, notify_at, MAX_ATTEMPTS))
                    queued += cursor.rowcount
            cursor.execute('''
                UPDATE avisos_salida SET estado = 'expirado', reclamo_token = NULL,
                    reclamo_hasta = NULL
                WHERE estado IN ('pendiente', 'enviando') AND salida_en <= %s;
            ''', (now,))
            cursor.execute('''
                DELETE FROM avisos_salida WHERE salida_en < %s
                    AND estado IN ('enviado', 'expirado', 'error');
            ''', (now - timedelta(days=30),))
        connection.commit()
        return queued
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _claim_job(get_connection, now):
    """El lock sólo cubre el claim; ninguna conexión queda abierta durante HTTP."""
    connection = get_connection()
    token = secrets.token_urlsafe(32)
    try:
        with _cursor(connection) as cursor:
            cursor.execute('''
                SELECT a.id FROM avisos_salida a
                JOIN suscripciones_push p ON p.id = a.suscripcion_id AND p.activa
                JOIN favoritos_recorridos f ON f.usuario_id = p.usuario_id
                                           AND f.horario_id = a.horario_id
                WHERE a.salida_en > %s AND a.avisar_en <= %s
                    AND a.intentos < %s
                    AND ((a.estado = 'pendiente' AND a.reintentar_en <= %s)
                         OR (a.estado = 'enviando' AND a.reclamo_hasta <= %s))
                ORDER BY a.salida_en, a.id
                FOR UPDATE OF a SKIP LOCKED LIMIT 1;
            ''', (now, now, MAX_ATTEMPTS, now, now))
            row = cursor.fetchone()
            if row is None:
                connection.commit()
                return None
            cursor.execute('''
                UPDATE avisos_salida SET estado = 'enviando', reclamo_token = %s,
                    reclamo_hasta = %s, intentos = intentos + 1
                WHERE id = %s RETURNING id, suscripcion_id, horario_id, salida_en,
                    avisar_en, intentos, reclamo_token;
            ''', (token, now + timedelta(seconds=CLAIM_SECONDS), row['id']))
            job = dict(cursor.fetchone())
        connection.commit()
        return job
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _current_delivery(get_connection, job, now):
    """Vuelve a comprobar la suscripción, el favorito y el horario tras el claim."""
    connection = get_connection()
    try:
        with _cursor(connection) as cursor:
            cursor.execute('''
                SELECT p.id AS suscripcion_id, p.usuario_id, p.endpoint, p.endpoint_hash,
                       p.p256dh, p.auth, f.anticipacion_min, h.id AS horario_id,
                       h.origen, h.destino, h.tipo, h.salida, h.dias, h.anden,
                       COALESCE(e.nombre, 'Bus') AS empresa
                FROM avisos_salida a
                JOIN suscripciones_push p ON p.id = a.suscripcion_id AND p.activa
                JOIN favoritos_recorridos f ON f.usuario_id = p.usuario_id
                                           AND f.horario_id = a.horario_id
                JOIN horarios h ON h.id = a.horario_id
                LEFT JOIN empresas e ON e.id = h.empresa_id
                WHERE a.id = %s AND a.estado = 'enviando' AND a.reclamo_token = %s;
            ''', (job['id'], job['reclamo_token']))
            row = cursor.fetchone()
        connection.commit()
        if (row is None or normalize(row['origen']) != 'panguipulli'
                or normalize(row['tipo']) not in ('salida', 'salidas')
                or row['anticipacion_min'] not in ALLOWED_LEAD_MINUTES
                or job['salida_en'] not in departure_windows(row['salida'], row['dias'], now)):
            return None
        return dict(row)
    finally:
        connection.close()


def _retry_delay(error, attempts, now):
    fallback = min(300, POLL_SECONDS * (2 ** max(0, attempts - 1)))
    if error.retry_after is not None:
        try:
            return max(POLL_SECONDS, min(3600, int(error.retry_after)))
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(str(error.retry_after))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                return max(POLL_SECONDS, min(3600, math.ceil((date - now).total_seconds())))
            except (ValueError, TypeError, OverflowError):
                pass
    return fallback


def _finish_job(get_connection, job, now, error=None, expired=False, defer_until=None, delivery=None):
    connection = get_connection()
    try:
        state = 'enviado'
        retry_at = None
        message = None
        if expired or now >= job['salida_en']:
            state = 'expirado'
        elif defer_until is not None:
            state, retry_at = 'pendiente', defer_until
        elif error is not None:
            status = error.status
            message = f'HTTP {status}' if status is not None else 'Entrega temporalmente no disponible'
            retry_at = now + timedelta(seconds=_retry_delay(error, job['intentos'], now))
            transient = status is None or status == 429 or 500 <= status <= 599
            if transient and job['intentos'] < MAX_ATTEMPTS and retry_at < job['salida_en']:
                state = 'pendiente'
            else:
                state = 'expirado' if retry_at >= job['salida_en'] else 'error'
        with _cursor(connection) as cursor:
            if error is not None and error.status in (404, 410) and delivery is not None:
                # Las APIs de revocación bloquean primero la suscripción y
                # después sus avisos. Mantener ese orden evita deadlocks.
                cursor.execute('SELECT id FROM suscripciones_push WHERE id = %s FOR UPDATE;',
                               (job['suscripcion_id'],))
                cursor.fetchone()
            cursor.execute('''
                UPDATE avisos_salida SET estado = %s, reclamo_token = NULL,
                    reclamo_hasta = NULL, reintentar_en = COALESCE(%s, reintentar_en),
                    enviado_en = CASE WHEN %s = 'enviado' THEN %s ELSE enviado_en END,
                    ultimo_error = %s
                WHERE id = %s AND estado = 'enviando' AND reclamo_token = %s;
            ''', (state, retry_at, state, now, message, job['id'], job['reclamo_token']))
            updated = cursor.rowcount
            if updated and error is not None and error.status in (404, 410) and delivery is not None:
                cursor.execute('''
                    UPDATE suscripciones_push SET activa = FALSE, actualizada_en = %s
                    WHERE id = %s AND usuario_id = %s AND endpoint_hash = %s;
                ''', (now, job['suscripcion_id'], delivery['usuario_id'], delivery['endpoint_hash']))
        connection.commit()
        return state
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def run_notification_cycle(get_connection, sender=None, now=None, max_deliveries=40):
    """Prepara salidas y entrega sólo avisos vigentes; apto para cron o workers.

    ``sender(subscription, payload, ttl)`` puede sustituirse por un doble de
    pruebas. Sin ese doble se necesitan las claves privadas VAPID del servidor.
    """
    if sender is None:
        if _vapid_settings() is None:
            return {'preparados': 0, 'enviado': 0, 'pendiente': 0, 'expirado': 0, 'error': 0}
        sender = send_web_push
    clock = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
    started = time.monotonic()
    initial = clock()
    if initial.tzinfo is None:
        raise ValueError('Se necesita una fecha con zona horaria')
    counts = {'preparados': _enqueue_departures(get_connection, initial),
              'enviado': 0, 'pendiente': 0, 'expirado': 0, 'error': 0}
    for _ in range(max_deliveries):
        if time.monotonic() - started >= 20:
            break
        claimed_at = clock()
        job = _claim_job(get_connection, claimed_at)
        if job is None:
            break
        delivery = _current_delivery(get_connection, job, clock())
        error = None
        expired = delivery is None
        defer_until = None
        if delivery is not None:
            notify_at = job['salida_en'] - timedelta(minutes=delivery['anticipacion_min'])
            if notify_at > clock():
                defer_until = notify_at
            elif clock() >= job['salida_en']:
                expired = True
            else:
                subscription = {'endpoint': delivery['endpoint'],
                                'keys': {'p256dh': delivery['p256dh'], 'auth': delivery['auth']}}
                try:
                    validate_push_subscription(subscription)
                    sender(subscription, notification_payload(job | delivery, clock()),
                           max(1, int((job['salida_en'] - clock()).total_seconds())))
                except PushDeliveryError as failure:
                    error = failure
                except InvalidSubscription:
                    error = PushDeliveryError(410)
                except Exception:
                    # Nunca persistir textos de excepción: pueden contener tokens del endpoint.
                    error = PushDeliveryError()
        state = _finish_job(get_connection, job, clock(), error=error,
                            expired=expired, defer_until=defer_until, delivery=delivery)
        counts[state] += 1
    return counts


def start_notification_scheduler(get_connection, logger):
    """Un thread por proceso; SKIP LOCKED y claves únicas coordinan Gunicorn."""
    enabled = os.environ.get('NOTIFICATIONS_SCHEDULER_ENABLED', 'false').strip().lower()
    if enabled not in ('1', 'true', 'yes', 'on') or _vapid_settings() is None:
        return None
    pid = os.getpid()
    with _scheduler_lock:
        existing = _scheduler_threads.get(pid)
        if existing is not None and existing.is_alive():
            return existing

        def loop():
            while True:
                try:
                    run_notification_cycle(get_connection)
                except Exception as error:
                    logger.error('No se pudo procesar avisos de salida: %s', type(error).__name__)
                time.sleep(POLL_SECONDS)

        worker = threading.Thread(target=loop, name='bsred-avisos-salida', daemon=True)
        _scheduler_threads[pid] = worker
        worker.start()
        return worker
