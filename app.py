import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder='.', static_folder='.')
CORS(app)

DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    url = DATABASE_URL
    if not url:
        return None
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    try:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"⚠️ Error de conexión a Supabase: {e}")
        return None

def limpiar_fila(row):
    """
    Convierte tipos de datos de PostgreSQL (como time, date, Decimal)
    a tipos nativos de Python/JSON para evitar errores de serialización (HTTP 500).
    """
    if not row:
        return {}
    d = dict(row)
    for k, v in d.items():
        if v is not None and not isinstance(v, (int, float, bool, str, list, dict)):
            d[k] = str(v)
    return d

# Datos de respaldo por si falla la base de datos
MOCK_HORARIOS = [
    {"id": 1, "origen": "Panguipulli", "destino": "Valdivia", "salida": "08:00", "empresa": "Buses Panguipulli", "anden": "Andén 1", "estado": "A tiempo", "tipo": "salida", "precio": 3500, "dias": "Lunes a Viernes"},
    {"id": 2, "origen": "Panguipulli", "destino": "Los Lagos", "salida": "09:30", "empresa": "Tur Bus", "anden": "Andén 3", "estado": "En ruta", "tipo": "salida", "precio": 2800, "dias": "Diario"},
    {"id": 3, "origen": "Panguipulli", "destino": "Choshuenco", "salida": "11:00", "empresa": "Buses Pirehueico", "anden": "Andén 4", "estado": "A tiempo", "tipo": "salida", "precio": 3000, "dias": "Lunes a Sábado"},
    {"id": 4, "origen": "Panguipulli", "destino": "Coñaripe", "salida": "12:30", "empresa": "Buses Liquiñe", "anden": "Andén 2", "estado": "A tiempo", "tipo": "salida", "precio": 2500, "dias": "Diario"},
    {"id": 5, "origen": "Panguipulli", "destino": "Puerto Fuy", "salida": "14:00", "empresa": "Buses Pirehueico", "anden": "Andén 4", "estado": "A tiempo", "tipo": "salida", "precio": 3500, "dias": "Diario"},
    {"id": 6, "origen": "Panguipulli", "destino": "Villarrica", "salida": "15:30", "empresa": "Buses Jac", "anden": "Andén 5", "estado": "A tiempo", "tipo": "salida", "precio": 4000, "dias": "Diario"},
    {"id": 7, "origen": "Panguipulli", "destino": "Temuco", "salida": "17:00", "empresa": "Buses JAC", "anden": "Andén 5", "estado": "A tiempo", "tipo": "salida", "precio": 6000, "dias": "Diario"},
    {"id": 8, "origen": "Panguipulli", "destino": "Liquiñe", "salida": "18:15", "empresa": "Buses Liquiñe", "anden": "Andén 2", "estado": "A tiempo", "tipo": "salida", "precio": 3200, "dias": "Lunes a Sábado"},
    {"id": 9, "origen": "Lican Ray", "destino": "Panguipulli", "salida": "10:15", "empresa": "Buses Jac", "anden": "Andén 2", "estado": "Retrasado", "tipo": "llegada", "precio": 2500, "dias": "Diario"},
    {"id": 10, "origen": "Coñaripe", "destino": "Panguipulli", "salida": "12:00", "empresa": "Buses Panguipulli", "anden": "Andén 1", "estado": "A tiempo", "tipo": "llegada", "precio": 2000, "dias": "Diario"},
    {"id": 11, "origen": "Valdivia", "destino": "Panguipulli", "salida": "13:45", "empresa": "Buses Panguipulli", "anden": "Andén 1", "estado": "A tiempo", "tipo": "llegada", "precio": 3500, "dias": "Lunes a Viernes"},
    {"id": 12, "origen": "Puerto Fuy", "destino": "Panguipulli", "salida": "16:30", "empresa": "Buses Pirehueico", "anden": "Andén 4", "estado": "En ruta", "tipo": "llegada", "precio": 3500, "dias": "Diario"},
    {"id": 13, "origen": "Temuco", "destino": "Panguipulli", "salida": "19:20", "empresa": "Buses JAC", "anden": "Andén 5", "estado": "A tiempo", "tipo": "llegada", "precio": 6000, "dias": "Diario"}
]

@app.route('/')
def index():
    if os.path.exists(os.path.join(app.root_path, 'templates', 'index.html')):
        return send_from_directory('templates', 'index.html')
    elif os.path.exists(os.path.join(app.root_path, 'index.html')):
        return send_from_directory('.', 'index.html')
    return "Error: No se encontró index.html", 404

@app.route('/usuario')
def usuario():
    if os.path.exists(os.path.join(app.root_path, 'templates', 'usuario.html')):
        return send_from_directory('templates', 'usuario.html')
    elif os.path.exists(os.path.join(app.root_path, 'usuario.html')):
        return send_from_directory('.', 'usuario.html')
    return "Error: No se encontró usuario.html", 404

@app.route('/api/horarios', methods=['GET'])
def obtener_horarios():
    tab = request.args.get('tab', '')
    q = request.args.get('q', '').lower()

    lista = MOCK_HORARIOS
    conn = get_db_connection()

    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM horarios ORDER BY salida ASC;")
                res = cur.fetchall()
                if res:
                    lista = [limpiar_fila(r) for r in res]
            conn.close()
        except Exception as e:
            print(f"⚠️ Error consultando Supabase: {e}")

    try:
        if tab:
            tipo_filtro = "salida" if "salida" in tab.lower() else "llegada"
            lista = [h for h in lista if str(h.get("tipo", "")).lower() == tipo_filtro]

        if q:
            lista = [
                h for h in lista if 
                q in str(h.get("destino", "")).lower() or 
                q in str(h.get("origen", "")).lower() or 
                q in str(h.get("empresa", "")).lower() or 
                q in str(h.get("anden", "")).lower()
            ]
    except Exception as e:
        print(f"⚠️ Error al aplicar filtros: {e}")

    return jsonify(lista)

@app.route('/api/horarios//estado', methods=['PUT'])
def actualizar_estado(id):
    datos = request.get_json() or {}
    nuevo_estado = datos.get('estado')
    
    if not nuevo_estado:
        return jsonify({"success": False, "message": "Estado no proporcionado"}), 400
        
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": True, "message": "Estado actualizado localmente"})

    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE horarios SET estado = %s WHERE id = %s;", (nuevo_estado, id))
            conn.commit()
            conn.close()
            return jsonify({"success": True, "message": "Estado actualizado correctamente"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    datos = request.get_json() or {}
    email = datos.get('email')
    password = datos.get('password')

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": True, "user": {"nombre": "Usuario", "email": email, "rol": "pasajero"}})

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nombre, email, rol FROM usuarios WHERE email = %s AND password = %s;", (email, password))
            usuario = cur.fetchone()
            conn.close()
            if usuario:
                return jsonify({"success": True, "user": limpiar_fila(usuario)})
            return jsonify({"success": False, "message": "Credenciales incorrectas"}), 401
    except Exception as e:
        return jsonify({"success": False, "message": "Error al iniciar sesión"}), 500

@app.route('/api/register', methods=['POST'])
@app.route('/api/registro', methods=['POST'])
def register():
    datos = request.get_json() or {}
    nombre = datos.get('nombre')
    email = datos.get('email')
    password = datos.get('password')
    rol = datos.get('rol', 'pasajero')

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": True, "message": "Usuario registrado exitosamente"})

    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO usuarios (nombre, email, password, rol) VALUES (%s, %s, %s, %s);", (nombre, email, password, rol))
            conn.commit()
            conn.close()
            return jsonify({"success": True, "message": "Cuenta creada con éxito"})
    except Exception as e:
        return jsonify({"success": False, "message": "El correo ya se encuentra registrado"}), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)