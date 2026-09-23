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

# Datos de respaldo por si la base de datos no responde
MOCK_HORARIOS = [
    {"id": 1, "origen": "Panguipulli", "destino": "Valdivia", "salida": "08:00", "empresa": "Buses Panguipulli", "anden": "1", "estado": "A tiempo", "tipo": "salida", "precio": 3500, "dias": "Lunes a Viernes"},
    {"id": 2, "origen": "Panguipulli", "destino": "Los Lagos", "salida": "09:30", "empresa": "Tur Bus", "anden": "3", "estado": "En ruta", "tipo": "salida", "precio": 2800, "dias": "Diario"},
    {"id": 3, "origen": "Lican Ray", "destino": "Panguipulli", "salida": "10:15", "empresa": "Buses Jac", "anden": "2", "estado": "Retrasado", "tipo": "llegada", "precio": 2500, "dias": "Diario"},
    {"id": 4, "origen": "Panguipulli", "destino": "Choshuenco", "salida": "11:00", "empresa": "Buses Pirehueico", "anden": "4", "estado": "A tiempo", "tipo": "salida", "precio": 3000, "dias": "Lunes a Sábado"},
    {"id": 5, "origen": "Coñaripe", "destino": "Panguipulli", "salida": "12:00", "empresa": "Buses Panguipulli", "anden": "1", "estado": "A tiempo", "tipo": "llegada", "precio": 2000, "dias": "Diario"}
]

# RUTA 1: PÁGINA PRINCIPAL (index.html)
@app.route('/')
def index():
    if os.path.exists(os.path.join(app.root_path, 'templates', 'index.html')):
        return send_from_directory('templates', 'index.html')
    elif os.path.exists(os.path.join(app.root_path, 'index.html')):
        return send_from_directory('.', 'index.html')
    return "Error: No se encontró el archivo index.html", 404

# RUTA 2: PANEL DE USUARIO (usuario.html) - Solución al error 404
@app.route('/usuario')
def usuario():
    if os.path.exists(os.path.join(app.root_path, 'templates', 'usuario.html')):
        return send_from_directory('templates', 'usuario.html')
    elif os.path.exists(os.path.join(app.root_path, 'usuario.html')):
        return send_from_directory('.', 'usuario.html')
    return "Error: No se encontró el archivo usuario.html", 404

# OBTENER HORARIOS (con soporte de filtros tab y busqueda)
@app.route('/api/horarios', methods=['GET'])
def obtener_horarios():
    tab = request.args.get('tab', '')
    q = request.args.get('q', '').lower()

    conn = get_db_connection()
    lista = MOCK_HORARIOS

    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM horarios ORDER BY salida ASC;")
                res = cur.fetchall()
                if res:
                    lista = res
            conn.close()
        except Exception as e:
            print(f"Error consultando base de datos: {e}")

    # Filtrar según tab y q si vienen desde index.html
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

    return jsonify(lista)

# ACTUALIZAR ESTADO DE VIAJE
@app.route('/api/horarios/<int:id>/estado', methods=['PUT'])
def actualizar_estado(id):
    datos = request.get_json() or {}
    nuevo_estado = datos.get('estado')
    
    if not nuevo_estado:
        return jsonify({"success": False, "message": "Estado no proporcionado"}), 400
        
    conn = get_db_connection()
    if not conn:
        for h in MOCK_HORARIOS:
            if h["id"] == id:
                h["estado"] = nuevo_estado
        return jsonify({"success": True, "message": "Estado actualizado"})

    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE horarios SET estado = %s WHERE id = %s;", (nuevo_estado, id))
            conn.commit()
            conn.close()
            return jsonify({"success": True, "message": "Estado actualizado correctamente"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# INICIO DE SESIÓN
@app.route('/api/login', methods=['POST'])
def login():
    datos = request.get_json() or {}
    email = datos.get('email')
    password = datos.get('password')

    conn = get_db_connection()
    if not conn:
        return jsonify({
            "success": True,
            "user": {"nombre": "Usuario Pasajero", "email": email, "rol": "pasajero"},
            "message": "Inicio de sesión exitoso"
        })

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nombre, email, rol FROM usuarios WHERE email = %s AND password = %s;", (email, password))
            usuario = cur.fetchone()
            conn.close()
            if usuario:
                return jsonify({"success": True, "user": usuario, "message": "Bienvenido"})
            else:
                return jsonify({"success": False, "message": "Credenciales incorrectas"}), 401
    except Exception as e:
        return jsonify({"success": False, "message": "Error al iniciar sesión"}), 500

# REGISTRO DE USUARIOS (Soporta /api/register y /api/registro)
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
        return jsonify({"success": False, "message": "El correo ya se encuentra registrado o hubo un error"}), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)