import os
import hashlib
import re
import secrets
import threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from rutas import route_definition, route_geometry, schedule_windows, RouteDefinitionError, RoutingUnavailable
from avisos import run_notification_cycle, start_notification_scheduler
from favoritos import DEVICE_COOKIE, register_favorites, revoke_device
from choferes import register_drivers
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
CORS(app)

DATABASE_URL = os.environ.get("DATABASE_URL")

SESSION_COOKIE = "bsred_sesion"
VISITOR_COOKIE = "bsred_visitante"
SESSION_SECONDS = 24 * 60 * 60
ONLINE_SECONDS = 90
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor, connect_timeout=8)


def cookie_token(name):
    token = request.cookies.get(name, "")
    return token if TOKEN_PATTERN.fullmatch(token) else None


def token_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def current_user(cur, lock=False):
    token = cookie_token(SESSION_COOKIE)
    if not token:
        return None
    session_hash = token_hash(token)
    if lock:
        # Usuario antes de sesión, también al suspender/eliminar y al iniciar viajes.
        # La consulta final revalida la sesión después de esperar por el usuario.
        cur.execute('SELECT usuario_id FROM sesiones_web WHERE token_hash=%s;', (session_hash,))
        session = cur.fetchone()
        if not session:
            return None
        cur.execute('SELECT id FROM usuarios WHERE id=%s FOR UPDATE;', (session['usuario_id'],))
        if not cur.fetchone():
            return None
    query = """
        SELECT u.id, u.nombre, u.email, LOWER(TRIM(u.rol)) AS rol, u.empresa_id
        FROM sesiones_web s
        JOIN usuarios u ON u.id = s.usuario_id
        WHERE s.token_hash = %s AND s.expira_en > CURRENT_TIMESTAMP AND u.suspendido=FALSE;
    """
    if lock:
        query = query.rstrip().rstrip(';') + ' FOR UPDATE OF s;'
    cur.execute(query, (session_hash,))
    return cur.fetchone()


def set_private_cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=request.is_secure, samesite="Lax", path="/")


@app.after_request
def prevent_api_cache(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, private"
        response.vary.add("Cookie")
    return response


@app.route('/api/presencia', methods=['POST'])
def registrar_presencia():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict) or not isinstance(datos.get("visible"), bool):
        return jsonify({"success": False, "message": "Actividad inválida"}), 400
    visitante = cookie_token(VISITOR_COOKIE) or secrets.token_urlsafe(32)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO visitantes_web (visitante_hash, ultima_visita, ultima_actividad)
                VALUES (%s, CURRENT_TIMESTAMP,
                        CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END)
                ON CONFLICT (visitante_hash) DO UPDATE SET
                    ultima_visita = CURRENT_TIMESTAMP,
                    ultima_actividad = CASE WHEN %s THEN CURRENT_TIMESTAMP
                                           ELSE visitantes_web.ultima_actividad END;
            """, (token_hash(visitante), datos["visible"], datos["visible"]))
        conn.commit()
        response = jsonify({"success": True})
        set_private_cookie(response, VISITOR_COOKIE, visitante, 365 * 24 * 60 * 60)
        return response
    except Exception as e:
        app.logger.error("No se pudo registrar presencia: %s", type(e).__name__)
        return jsonify({"success": False, "message": "No se pudo registrar actividad"}), 503
    finally:
        if conn:
            conn.close()


@app.route('/api/sesion', methods=['GET'])
def obtener_sesion():
    if not cookie_token(SESSION_COOKIE):
        return jsonify({"success": False, "message": "Inicia sesión"}), 401
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            user = current_user(cur)
        if user:
            return jsonify({"success": True, "user": user})
        return jsonify({"success": False, "message": "Sesión expirada"}), 401
    except Exception as e:
        app.logger.error("No se pudo consultar la sesión: %s", type(e).__name__)
        return jsonify({"success": False, "message": "No se pudo verificar la sesión"}), 503
    finally:
        if conn:
            conn.close()


@app.route('/api/logout', methods=['POST'])
def logout():
    token = cookie_token(SESSION_COOKIE)
    device = cookie_token(DEVICE_COOKIE)
    conn = None
    try:
        if token or device:
            conn = get_db_connection()
            with conn.cursor() as cur:
                user = current_user(cur, lock=True)
                if user and user['rol'] == 'chofer':
                    cur.execute("SELECT id FROM viajes WHERE chofer_id=%s AND estado='en_curso';", (user['id'],))
                    if cur.fetchone():
                        return jsonify(success=False, message='Finaliza el viaje antes de cerrar sesión.'), 409
                if token:
                    # El usuario ya está bloqueado antes de comprobar el viaje.
                    cur.execute('SELECT usuario_id FROM sesiones_web WHERE token_hash = %s FOR UPDATE;', (token_hash(token),))
                    cur.fetchone()
                if device:
                    revoke_device(cur, token_hash(device))
                if token:
                    cur.execute("DELETE FROM sesiones_web WHERE token_hash = %s;", (token_hash(token),))
            conn.commit()
        response = jsonify({"success": True})
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                               secure=request.is_secure, samesite="Lax")
        return response
    except Exception as e:
        app.logger.error("No se pudo cerrar la sesión: %s", type(e).__name__)
        return jsonify({"success": False, "message": "No se pudo cerrar la sesión"}), 503
    finally:
        if conn:
            conn.close()

# ==========================================
# RUTAS DE PÁGINAS ESTÁTICAS
# ==========================================
@app.route('/')
def index():
    for f in ['index.html', 'index (1).html']:
        if os.path.exists(f):
            return send_file(f)
    return "index.html no encontrado", 404

@app.route('/usuario')
def usuario():
    for f in ['usuario.html', 'usuario (1).html']:
        if os.path.exists(f):
            return send_file(f)
    return "usuario.html no encontrado", 404


@app.route('/<path:filename>')
def public_asset(filename):
    if filename not in {
        'index.html', 'usuario.html', 'manifest.json', 'sw.js', 'telemetria.js', 'mapa-rutas.js', 'favoritos.js',
        'logo.jpg', 'logo_192.png', 'logo_512.png', 'choferes.js'
    }:
        return "Archivo no encontrado", 404
    return send_from_directory(app.root_path, filename)


@app.route('/api/mapa/ruta/<int:horario_id>', methods=['GET'])
def obtener_ruta_mapa(horario_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute('SELECT id, origen, destino, salida, llegada, dias FROM horarios WHERE id = %s;', (horario_id,))
            horario = cur.fetchone()
        conn.close()
        conn = None
        if not horario:
            return jsonify({'success': False, 'message': 'Recorrido no encontrado'}), 404
        definition = route_definition(horario)
        geometry = route_geometry(definition['origen'], definition['destino'], definition['via'])
        now = datetime.now(timezone.utc)
        windows = schedule_windows(horario['salida'], horario['llegada'], horario['dias'], now)
        return jsonify({'success': True, 'horario_id': horario_id, **geometry, **windows,
                        'servidor_en': now.isoformat(),
                        'tipo_recorrido': definition['tipo_recorrido'],
                        'nota_itinerario': definition['nota_itinerario']})
    except RouteDefinitionError as error:
        return jsonify({'success': False, 'message': str(error)}), 422
    except RoutingUnavailable:
        return jsonify({'success': False, 'message': 'Trayecto por carretera temporalmente no disponible'}), 503
    except Exception as error:
        app.logger.error('No se pudo consultar el trayecto: %s', type(error).__name__)
        return jsonify({'success': False, 'message': 'No se pudo cargar este recorrido'}), 503
    finally:
        if conn:
            conn.close()

# ==========================================
# ENDPOINT: RECORRIDOS Y HORARIOS
# ==========================================
@app.route('/api/horarios', methods=['GET'])
def obtener_horarios():
    tab = request.args.get('tab', 'salidas').lower()
    busqueda = request.args.get('q', '').strip()
    
    # Normalizar singular/plural ('salidas' -> 'salida', 'llegadas' -> 'llegada')
    tipo_filtro = 'salida' if 'salida' in tab else 'llegada'
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1. Registrar telemetría de consulta en la BD
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS registro_consultas (
                    id SERIAL PRIMARY KEY,
                    fecha_hora TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    tab VARCHAR(50),
                    termino_busqueda VARCHAR(255)
                );
            """)
            cur.execute(
                "INSERT INTO registro_consultas (tab, termino_busqueda) VALUES (%s, %s);",
                (tab, busqueda)
            )
            conn.commit()
        except Exception as e_tel:
            conn.rollback()

        # 2. Consultar horarios uniendo con la tabla empresas
        sql = """
            SELECT 
                h.id,
                h.tipo,
                h.origen,
                h.destino,
                TO_CHAR(h.salida, 'HH24:MI') AS salida,
                TO_CHAR(h.llegada, 'HH24:MI') AS llegada,
                COALESCE(h.anden, '1') AS anden,
                COALESCE(h.dias, 'Todos los días') AS dias,
                COALESCE(e.nombre, 'Terminal Panguipulli') AS empresa
            FROM horarios h
            LEFT JOIN empresas e ON h.empresa_id = e.id
            WHERE LOWER(h.tipo) LIKE %s
        """
        params = [f"%{tipo_filtro}%"]

        if busqueda:
            sql += """ AND (
                LOWER(h.destino) LIKE %s 
                OR LOWER(h.origen) LIKE %s 
                OR LOWER(COALESCE(e.nombre, '')) LIKE %s
            )"""
            like_val = f"%{busqueda.lower()}%"
            params.extend([like_val, like_val, like_val])

        sql += " ORDER BY h.salida ASC;"
        cur.execute(sql, tuple(params))
        horarios = cur.fetchall()
        cur.close()

        return jsonify(horarios), 200

    except Exception as e:
        print(f"Error consultando horarios: {e}")
        return jsonify([]), 500
    finally:
        if conn:
            conn.close()

# ==========================================
# ENDPOINT: TELEMETRÍA REAL EN VIVO
# ==========================================
@app.route('/api/admin/telemetria', methods=['GET'])
def telemetria_admin():
    if not cookie_token(SESSION_COOKIE):
        return jsonify({"success": False, "message": "Inicia sesión"}), 401
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        user = current_user(cur)
        if not user:
            return jsonify({"success": False, "message": "Sesión expirada"}), 401
        if user['rol'] != 'admin':
            return jsonify({"success": False, "message": "Acceso sólo para administradores"}), 403

        # Un registro por navegador, compartido entre pestañas y páginas.
        cur.execute("""
            SELECT COUNT(*) FILTER (
                       WHERE ultima_visita >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
                   ) AS visitantes_24h,
                   COUNT(*) FILTER (
                       WHERE ultima_actividad >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                   ) AS usuarios_online,
                   CURRENT_TIMESTAMP AS actualizado_en
            FROM visitantes_web;
        """, (ONLINE_SECONDS,))
        visitantes = cur.fetchone()

        # Consultas de horarios del día calendario en Chile; no son visitantes.
        cur.execute("""
            SELECT CASE
                       WHEN EXTRACT(HOUR FROM fecha_hora AT TIME ZONE 'America/Santiago') >= 6
                        AND EXTRACT(HOUR FROM fecha_hora AT TIME ZONE 'America/Santiago') < 10 THEN 0
                       WHEN EXTRACT(HOUR FROM fecha_hora AT TIME ZONE 'America/Santiago') < 12
                        AND EXTRACT(HOUR FROM fecha_hora AT TIME ZONE 'America/Santiago') >= 10 THEN 1
                       WHEN EXTRACT(HOUR FROM fecha_hora AT TIME ZONE 'America/Santiago') < 15
                        AND EXTRACT(HOUR FROM fecha_hora AT TIME ZONE 'America/Santiago') >= 12 THEN 2
                       WHEN EXTRACT(HOUR FROM fecha_hora AT TIME ZONE 'America/Santiago') < 19
                        AND EXTRACT(HOUR FROM fecha_hora AT TIME ZONE 'America/Santiago') >= 15 THEN 3
                       ELSE 4
                   END AS grupo, COUNT(*) AS total
            FROM registro_consultas
            WHERE fecha_hora >= (
                (CURRENT_TIMESTAMP AT TIME ZONE 'America/Santiago')::date::timestamp
                AT TIME ZONE 'America/Santiago'
            ) AND fecha_hora <= CURRENT_TIMESTAMP
            GROUP BY 1;
        """)
        afluencia = [0, 0, 0, 0, 0]
        for grupo in cur.fetchall():
            afluencia[grupo['grupo']] = grupo['total']
        total_consultas = sum(afluencia)
        max_val = max(afluencia) or 1
        alturas_pct = [int(val / max_val * 100) for val in afluencia]

        # 3. Choferes con GPS activo
        cur.execute("""SELECT COUNT(DISTINCT v.chofer_id) AS total FROM viajes v JOIN usuarios u ON u.id=v.chofer_id
            WHERE u.suspendido=FALSE AND v.estado='en_curso' AND v.iniciado_en>CURRENT_TIMESTAMP-INTERVAL '24 hours'
            AND v.ubicacion_en>CURRENT_TIMESTAMP-INTERVAL '2 minutes';""")
        choferes_gps = cur.fetchone()['total']

        # 4. Total de empresas registradas (desde la tabla empresas)
        cur.execute("SELECT COUNT(*) as total FROM empresas;")
        empresas_totales = cur.fetchone()['total']

        # Andenes presentes en los horarios, sin simular ocupación física.
        cur.execute("SELECT COUNT(DISTINCT anden) as ocupados FROM horarios WHERE anden IS NOT NULL;")
        andenes_ocupados = cur.fetchone()['ocupados'] or 0

        cur.close()

        return jsonify({
            "success": True,
            "metricas": {
                "visitantes_24h": visitantes['visitantes_24h'],
                "usuarios_online": visitantes['usuarios_online'],
                "consultas_diarias": total_consultas,
                "buses_activos": choferes_gps,
                "andenes_programados": andenes_ocupados,
                "empresas_registradas": empresas_totales,
                "choferes_gps": choferes_gps
            },
            "actualizado_en": visitantes['actualizado_en'].isoformat(),
            "ventana_online_segundos": ONLINE_SECONDS,
            "grafico_afluencia_pct": alturas_pct,
            "grafico_afluencia_raw": afluencia
        }), 200

    except Exception as e:
        app.logger.error("No se pudo consultar telemetría: %s", type(e).__name__)
        return jsonify({"success": False, "message": "Telemetría temporalmente no disponible"}), 503
    finally:
        if conn:
            conn.close()

# ==========================================
# ENDPOINT: CONTROL GPS DEL CHOFER
# ==========================================
@app.route('/api/chofer/gps', methods=['POST'])
def actualizar_gps_chofer():
    return jsonify(success=False, message='Inicia un viaje desde la consola de chofer. La bandera GPS anterior ya no se utiliza.'), 410

# ==========================================
# ENDPOINTS: LOGIN Y REGISTRO
# ==========================================
@app.route('/api/login', methods=['POST'])
def login():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict) or not all(isinstance(datos.get(k), str) for k in ('email', 'password')):
        return jsonify({"success": False, "message": "Credenciales inválidas"}), 400
    email = datos.get('email', '').strip().lower()
    password = datos.get('password', '')
    if not email or not password or len(password)>128:
        return jsonify(success=False, message='Credenciales inválidas'), 400
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, nombre, email, LOWER(TRIM(rol)) AS rol, empresa_id, password_hash FROM usuarios WHERE LOWER(TRIM(email)) = %s AND suspendido=FALSE FOR UPDATE;",
            (email,)
        )
        usuario_db = cur.fetchone()
        if usuario_db:
            stored_hash = usuario_db.pop('password_hash')
            if stored_hash:
                valid = check_password_hash(stored_hash, password)
            else:
                cur.execute('SELECT 1 FROM usuarios WHERE id=%s AND password=%s;', (usuario_db['id'],password.strip()))
                valid = bool(cur.fetchone())
            if not valid:
                usuario_db = None
        previous_token = cookie_token(SESSION_COOKIE)
        if previous_token:
            cur.execute("DELETE FROM sesiones_web WHERE token_hash = %s;", (token_hash(previous_token),))
        device = cookie_token(DEVICE_COOKIE)
        if device:
            revoke_device(cur, token_hash(device))
        if usuario_db:
            token = secrets.token_urlsafe(32)
            cur.execute("""
                INSERT INTO sesiones_web (token_hash, usuario_id, expira_en)
                VALUES (%s, %s, CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'));
            """, (token_hash(token), usuario_db['id'], SESSION_SECONDS))
            conn.commit()
            response = jsonify({"success": True, "user": usuario_db})
            set_private_cookie(response, SESSION_COOKIE, token, SESSION_SECONDS)
            return response
        conn.commit()
        response = jsonify({"success": False, "message": "Credenciales inválidas"})
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                               secure=request.is_secure, samesite="Lax")
        return response, 401
    except Exception as e:
        app.logger.error("No se pudo iniciar sesión: %s", type(e).__name__)
        return jsonify({"success": False, "message": "No se pudo iniciar sesión"}), 503
    finally:
        if conn:
            conn.close()

@app.route('/api/registro', methods=['POST'])
def registro():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict) or not all(isinstance(datos.get(k), str) for k in ('nombre', 'email', 'password')):
        return jsonify({"success": False, "message": "Datos de registro inválidos"}), 400
    if not isinstance(datos.get('rol', 'pasajero'), str):
        return jsonify({"success": False, "message": "Tipo de cuenta inválido"}), 400
    nombre = datos.get('nombre', '').strip()
    email = datos.get('email', '').strip().lower()
    password = datos.get('password', '')
    rol = datos.get('rol', 'pasajero').strip().lower()
    if rol not in ('pasajero', 'empresa'):
        return jsonify({"success": False, "message": "Tipo de cuenta no permitido"}), 400
    if not 2 <= len(nombre) <= 100 or len(email)>255 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or not 8 <= len(password) <= 128:
        return jsonify(success=False, message='Indica nombre, correo válido y una contraseña de al menos 8 caracteres.'), 400
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        empresa_id = None
        if rol == 'empresa':
            cur.execute('INSERT INTO empresas(nombre) VALUES(%s) RETURNING id;', (nombre,))
            empresa_id = cur.fetchone()['id']
        cur.execute("INSERT INTO usuarios (nombre, email, password, password_hash, rol, empresa_id) VALUES (%s, %s, '', %s, %s, %s);",
                    (nombre, email, generate_password_hash(password), rol, empresa_id))
        conn.commit()
        cur.close()
        return jsonify({"success": True, "message": "Usuario registrado"}), 201
    except psycopg2.IntegrityError:
        return jsonify({"success": False, "message": "El correo ya está registrado"}), 400
    except Exception as e:
        app.logger.error('No se pudo registrar la cuenta: %s', type(e).__name__)
        return jsonify(success=False, message='No se pudo registrar la cuenta. Inténtalo nuevamente.'), 503
    finally:
        if conn:
            conn.close()

register_favorites(app, get_db_connection, current_user, cookie_token, token_hash, set_private_cookie)
register_drivers(app, get_db_connection, current_user, token_hash)

_dispatch_lock = threading.Lock()
_dispatch_thread = None


@app.route('/api/interno/avisos-salida', methods=['POST'])
def dispatch_departure_notifications():
    global _dispatch_thread
    expected = os.environ.get('NOTIFICATION_CRON_TOKEN', '')
    received = request.headers.get('Authorization', '')
    if not expected or not secrets.compare_digest(received, 'Bearer ' + expected):
        return jsonify(success=False, message='No autorizado.'), 401
    with _dispatch_lock:
        if _dispatch_thread is None or not _dispatch_thread.is_alive():
            def process():
                try:
                    run_notification_cycle(get_db_connection)
                except Exception as error:
                    app.logger.error('No se pudo procesar avisos de salida: %s', type(error).__name__)
            _dispatch_thread = threading.Thread(target=process, name='bsred-despacho-avisos', daemon=True)
            _dispatch_thread.start()
    return jsonify(success=True, aceptado=True), 202


start_notification_scheduler(get_db_connection, app.logger)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
