import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request, send_from_directory
from datetime import datetime
from dotenv import load_dotenv

# Carga variables locales si existe archivo .env
load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')

# Obtener URL de Supabase desde las variables de entorno
DATABASE_URL = os.getenv('DATABASE_URL')

def obtener_conexion():
    """Conexión segura a la base de datos de Supabase."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

@app.route('/')
def root():
    return send_from_directory(app.static_folder, 'index.html')

# ==========================================
# ENDPOINTS DE BASE DE DATOS (USUARIOS)
# ==========================================

@app.route('/api/usuarios', methods=['GET'])
def obtener_usuarios():
    try:
        conn = obtener_conexion()
        cur = conn.cursor()
        cur.execute("SELECT id, nombre, email, rol, empresa_id FROM usuarios;")
        usuarios = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(usuarios)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/registro', methods=['POST'])
def registrar_usuario():
    data = request.json or {}
    nombre = data.get('nombre')
    email = data.get('email')
    password = data.get('password')
    rol = data.get('rol', 'pasajero')

    if not nombre or not email or not password:
        return jsonify({"error": "Faltan campos requeridos"}), 400

    try:
        conn = obtener_conexion()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO usuarios (nombre, email, password_hash, rol) VALUES (%s, %s, %s, %s) RETURNING id, nombre, email, rol;",
            (nombre, email, password, rol)
        )
        nuevo_usuario = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return jsonify(nuevo_usuario), 201
    except Exception as e:
        return jsonify({"error": "Error al registrar usuario o email duplicado"}), 400

# ==========================================
# RECORRIDOS TERMINAL PANGUIPULLI
# ==========================================
BASE = [
    # ---------------- SALIDAS DESDE PANGUIPULLI ----------------
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Buses Liquiñe",   "salida": "06:30", "llegada": "07:40", "anden": "1", "dias": "Diario"},
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Buses Carrasco",  "salida": "08:00", "llegada": "09:10", "anden": "1", "dias": "Diario"},
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Buses Liquiñe",   "salida": "11:30", "llegada": "12:40", "anden": "2", "dias": "Lun-Sáb"},
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Jet Sur",         "salida": "15:45", "llegada": "16:55", "anden": "2", "dias": "Diario"},
    {"tipo": "salida", "sector": "Coñaripe",  "origen": "Panguipulli", "destino": "Coñaripe",  "empresa": "Buses Carrasco",  "salida": "19:00", "llegada": "20:10", "anden": "3", "dias": "Lun-Vie"},

    {"tipo": "salida", "sector": "Liquiñe",   "origen": "Panguipulli", "destino": "Liquiñe",   "empresa": "Buses Liquiñe",   "salida": "07:15", "llegada": "09:00", "anden": "1", "dias": "Diario"},
    {"tipo": "salida", "sector": "Liquiñe",   "origen": "Panguipulli", "destino": "Liquiñe",   "empresa": "Buses Liquiñe",   "salida": "16:00", "llegada": "17:45", "anden": "2", "dias": "Lun-Sáb"},

    {"tipo": "salida", "sector": "Choshuenco","origen": "Panguipulli", "destino": "Choshuenco","empresa": "Buses Pirehueico", "salida": "06:45", "llegada": "08:00", "anden": "3", "dias": "Diario"},
    {"tipo": "salida", "sector": "Neltume",   "origen": "Panguipulli", "destino": "Neltume",   "empresa": "Buses Pirehueico", "salida": "10:30", "llegada": "12:00", "anden": "3", "dias": "Diario"},
    {"tipo": "salida", "sector": "Puerto Fuy","origen": "Panguipulli", "destino": "Puerto Fuy","empresa": "Buses Pirehueico", "salida": "13:15", "llegada": "15:00", "anden": "3", "dias": "Diario"},
    {"tipo": "salida", "sector": "Puerto Fuy","origen": "Panguipulli", "destino": "Puerto Fuy","empresa": "Buses Pirehueico", "salida": "17:30", "llegada": "19:15", "anden": "3", "dias": "Diario"},

    {"tipo": "salida", "sector": "Los Lagos", "origen": "Panguipulli", "destino": "Los Lagos", "empresa": "Buses Méndez",    "salida": "07:15", "llegada": "08:00", "anden": "4", "dias": "Lun-Sáb"},
    {"tipo": "salida", "sector": "Los Lagos", "origen": "Panguipulli", "destino": "Los Lagos", "empresa": "Buses Méndez",    "salida": "09:00", "llegada": "09:45", "anden": "4", "dias": "Diario"},
    {"tipo": "salida", "sector": "Los Lagos", "origen": "Panguipulli", "destino": "Los Lagos", "empresa": "Buses Méndez",    "salida": "12:30", "llegada": "13:15", "anden": "5", "dias": "Diario"},
    {"tipo": "salida", "sector": "Los Lagos", "origen": "Panguipulli", "destino": "Los Lagos", "empresa": "Buses Méndez",    "salida": "17:45", "llegada": "18:30", "anden": "5", "dias": "Lun-Vie"},

    {"tipo": "salida", "sector": "Lanco",     "origen": "Panguipulli", "destino": "Lanco",     "empresa": "Buses Lanco",     "salida": "06:40", "llegada": "07:30", "anden": "4", "dias": "Diario"},
    {"tipo": "salida", "sector": "Lanco",     "origen": "Panguipulli", "destino": "Lanco",     "empresa": "Buses Lanco",     "salida": "13:00", "llegada": "13:50", "anden": "4", "dias": "Diario"},

    {"tipo": "salida", "sector": "Villarrica","origen": "Panguipulli", "destino": "Villarrica","empresa": "Jet Sur",         "salida": "06:50", "llegada": "08:30", "anden": "6", "dias": "Diario"},
    {"tipo": "salida", "sector": "Villarrica","origen": "Panguipulli", "destino": "Villarrica","empresa": "JAC",             "salida": "10:00", "llegada": "11:40", "anden": "6", "dias": "Diario"},
    {"tipo": "salida", "sector": "Villarrica","origen": "Panguipulli", "destino": "Villarrica","empresa": "Jet Sur",         "salida": "14:15", "llegada": "15:55", "anden": "7", "dias": "Lun-Sáb"},
    {"tipo": "salida", "sector": "Villarrica","origen": "Panguipulli", "destino": "Villarrica","empresa": "JAC",             "salida": "18:20", "llegada": "20:00", "anden": "7", "dias": "Diario"},

    {"tipo": "salida", "sector": "Lican Ray", "origen": "Panguipulli", "destino": "Lican Ray", "empresa": "Buses Carrasco",  "salida": "09:30", "llegada": "10:45", "anden": "6", "dias": "Diario"},
    {"tipo": "salida", "sector": "Temuco",    "origen": "Panguipulli", "destino": "Temuco",    "empresa": "JAC",             "salida": "07:30", "llegada": "10:00", "anden": "7", "dias": "Diario"},
    {"tipo": "salida", "sector": "Temuco",    "origen": "Panguipulli", "destino": "Temuco",    "empresa": "JAC",             "salida": "15:00", "llegada": "17:30", "anden": "7", "dias": "Diario"},

    {"tipo": "salida", "sector": "Valdivia",  "origen": "Panguipulli", "destino": "Valdivia",  "empresa": "JAC",             "salida": "06:15", "llegada": "08:15", "anden": "5", "dias": "Lun-Sáb"},
    {"tipo": "salida", "sector": "Valdivia",  "origen": "Panguipulli", "destino": "Valdivia",  "empresa": "Buses Pirihueico","salida": "11:00", "llegada": "13:00", "anden": "5", "dias": "Diario"},
    {"tipo": "salida", "sector": "Valdivia",  "origen": "Panguipulli", "destino": "Valdivia",  "empresa": "JAC",             "salida": "16:30", "llegada": "18:30", "anden": "5", "dias": "Diario"},

    {"tipo": "salida", "sector": "Huerquehue","origen": "Panguipulli", "destino": "Huerquehue","empresa": "Servicios Rurales Panguipulli", "salida": "07:00", "llegada": "07:40", "anden": "8", "dias": "Lun-Vie"},
    {"tipo": "salida", "sector": "Huerquehue","origen": "Panguipulli", "destino": "Huerquehue","empresa": "Servicios Rurales Panguipulli", "salida": "13:30", "llegada": "14:10", "anden": "8", "dias": "Lun-Vie"},
    {"tipo": "salida", "sector": "Huerquehue","origen": "Panguipulli", "destino": "Huerquehue","empresa": "Servicios Rurales Panguipulli", "salida": "18:10", "llegada": "18:50", "anden": "9", "dias": "Lun-Vie"},

    # ---------------- LLEGADAS HACIA PANGUIPULLI ----------------
    {"tipo": "llegada","sector": "Coñaripe",  "origen": "Coñaripe",  "destino": "Panguipulli", "empresa": "Buses Liquiñe",   "salida": "07:10", "llegada": "08:20", "anden": "1", "dias": "Diario"},
    {"tipo": "llegada","sector": "Coñaripe",  "origen": "Coñaripe",  "destino": "Panguipulli", "empresa": "Buses Carrasco",  "salida": "12:45", "llegada": "13:55", "anden": "2", "dias": "Lun-Sáb"},
    {"tipo": "llegada","sector": "Coñaripe",  "origen": "Coñaripe",  "destino": "Panguipulli", "empresa": "Jet Sur",         "salida": "17:10", "llegada": "18:20", "anden": "3", "dias": "Diario"},

    {"tipo": "llegada","sector": "Liquiñe",   "origen": "Liquiñe",   "destino": "Panguipulli", "empresa": "Buses Liquiñe",   "salida": "09:30", "llegada": "11:15", "anden": "1", "dias": "Diario"},
    {"tipo": "llegada","sector": "Liquiñe",   "origen": "Liquiñe",   "destino": "Panguipulli", "empresa": "Buses Liquiñe",   "salida": "18:00", "llegada": "19:45", "anden": "2", "dias": "Lun-Sáb"},

    {"tipo": "llegada","sector": "Puerto Fuy","origen": "Puerto Fuy","destino": "Panguipulli", "empresa": "Buses Pirehueico", "salida": "06:00", "llegada": "07:45", "anden": "3", "dias": "Diario"},
    {"tipo": "llegada","sector": "Neltume",   "origen": "Neltume",   "destino": "Panguipulli", "empresa": "Buses Pirehueico", "salida": "12:30", "llegada": "14:00", "anden": "3", "dias": "Diario"},
    {"tipo": "llegada","sector": "Puerto Fuy","origen": "Puerto Fuy","destino": "Panguipulli", "empresa": "Buses Pirehueico", "salida": "15:30", "llegada": "17:15", "anden": "3", "dias": "Diario"},

    {"tipo": "llegada","sector": "Los Lagos", "origen": "Los Lagos", "destino": "Panguipulli", "empresa": "Buses Méndez",    "salida": "08:15", "llegada": "09:00", "anden": "4", "dias": "Lun-Sáb"},
    {"tipo": "llegada","sector": "Los Lagos", "origen": "Los Lagos", "destino": "Panguipulli", "empresa": "Buses Méndez",    "salida": "13:30", "llegada": "14:15", "anden": "5", "dias": "Diario"},
    {"tipo": "llegada","sector": "Los Lagos", "origen": "Los Lagos", "destino": "Panguipulli", "empresa": "Buses Méndez",    "salida": "19:00", "llegada": "19:45", "anden": "5", "dias": "Lun-Vie"},

    {"tipo": "llegada","sector": "Lanco",     "origen": "Lanco",     "destino": "Panguipulli", "empresa": "Buses Lanco",     "salida": "07:45", "llegada": "08:35", "anden": "4", "dias": "Diario"},

    {"tipo": "llegada","sector": "Villarrica","origen": "Villarrica","destino": "Panguipulli", "empresa": "Jet Sur",         "salida": "07:00", "llegada": "08:40", "anden": "6", "dias": "Diario"},
    {"tipo": "llegada","sector": "Villarrica","origen": "Villarrica","destino": "Panguipulli", "empresa": "JAC",             "salida": "12:15", "llegada": "13:55", "anden": "6", "dias": "Diario"},
    {"tipo": "llegada","sector": "Villarrica","origen": "Villarrica","destino": "Panguipulli", "empresa": "Jet Sur",         "salida": "16:20", "llegada": "18:00", "anden": "7", "dias": "Lun-Sáb"},

    {"tipo": "llegada","sector": "Temuco",    "origen": "Temuco",    "destino": "Panguipulli", "empresa": "JAC",             "salida": "11:00", "llegada": "13:30", "anden": "7", "dias": "Diario"},
    {"tipo": "llegada","sector": "Temuco",    "origen": "Temuco",    "destino": "Panguipulli", "empresa": "JAC",             "salida": "17:30", "llegada": "20:00", "anden": "7", "dias": "Diario"},

    {"tipo": "llegada","sector": "Valdivia",  "origen": "Valdivia",  "destino": "Panguipulli", "empresa": "JAC",             "salida": "08:45", "llegada": "10:45", "anden": "5", "dias": "Diario"},
    {"tipo": "llegada","sector": "Valdivia",  "origen": "Valdivia",  "destino": "Panguipulli", "empresa": "Buses Pirihueico","salida": "14:00", "llegada": "16:00", "anden": "5", "dias": "Diario"},

    {"tipo": "llegada","sector": "Huerquehue","origen": "Huerquehue","destino": "Panguipulli", "empresa": "Servicios Rurales Panguipulli", "salida": "06:30", "llegada": "07:10", "anden": "8", "dias": "Lun-Vie"},
    {"tipo": "llegada","sector": "Huerquehue","origen": "Huerquehue","destino": "Panguipulli", "empresa": "Servicios Rurales Panguipulli", "salida": "12:50", "llegada": "13:30", "anden": "8", "dias": "Lun-Vie"},
    {"tipo": "llegada","sector": "Huerquehue","origen": "Huerquehue","destino": "Panguipulli", "empresa": "Servicios Rurales Panguipulli", "salida": "17:50", "llegada": "18:30", "anden": "9", "dias": "Lun-Vie"},
]

def normaliza_registro(h):
    reg = dict(h)
    reg["actualizado"] = datetime.now().isoformat(timespec='seconds')
    reg["hora_salida"] = reg.get("salida")
    reg["hora_llegada"] = reg.get("llegada")
    reg["departure"]   = reg.get("salida")
    reg["arrival"]     = reg.get("llegada")
    reg["from"] = reg.get("origen")
    reg["to"]   = reg.get("destino")
    reg["company"] = reg.get("empresa")
    reg["andén"] = reg.get("anden")
    reg["platform"] = reg.get("anden")
    reg["bay"] = reg.get("anden")
    return reg

DATA = [normaliza_registro(h) for h in BASE]

def filtrar(lista):
    tab = (request.args.get("tab") or "").strip().lower()
    q   = (request.args.get("q") or "").strip().lower()
    origen  = (request.args.get("origen")  or "").strip().lower()
    destino = (request.args.get("destino") or "").strip().lower()
    empresa = (request.args.get("empresa") or "").strip().lower()
    sector  = (request.args.get("sector")  or "").strip().lower()
    out = list(lista)

    if tab in ("salidas", "llegadas"):
        tipo = "salida" if tab == "salidas" else "llegada"
        out = [h for h in out if h.get("tipo") == tipo]
    if origen:
        out = [h for h in out if origen in (h.get("origen","") + " " + h.get("from","")).lower()]
    if destino:
        out = [h for h in out if destino in (h.get("destino","") + " " + h.get("to","")).lower()]
    if empresa:
        out = [h for h in out if empresa in (h.get("empresa","") + " " + h.get("company","")).lower()]
    if sector:
        out = [h for h in out if sector in h.get("sector","").lower()]
    if q:
        campos = ("sector","origen","destino","empresa","dias","from","to","company","anden","andén","platform","bay")
        out = [h for h in out if any(q in str(h.get(k,"")).lower() for k in campos)]
    return out

@app.route('/api/horarios', methods=['GET'])
def horarios():
    return jsonify(filtrar(DATA))

@app.route('/api/salidas', methods=['GET'])
def salidas():
    return jsonify([h for h in filtrar(DATA) if h.get("tipo") == "salida"])

@app.route('/api/llegadas', methods=['GET'])
def llegadas():
    return jsonify([h for h in filtrar(DATA) if h.get("tipo") == "llegada"])

@app.route('/healthz')
def healthz():
    return jsonify({"ok": True, "database": "Conectada" if DATABASE_URL else "Sin configurar"})

@app.after_request
def cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    if request.path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
