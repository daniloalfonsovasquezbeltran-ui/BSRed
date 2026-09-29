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
    if not row:
        return {}
    d = dict(row)
    for k, v in d.items():
        if v is not None and not isinstance(v, (int, float, bool, str, list, dict)):
            d[k] = str(v)
    return d

MOCK_HORARIOS = [
    {"id": 1, "origen": "Panguipulli", "destino": "Valdivia", "salida": "08:00", "empresa": "Buses Panguipulli", "anden": "Andén 1", "estado": "A tiempo", "tipo": "salida", "precio": 3500, "dias": "Lunes a Viernes"},
    {"id": 2, "origen": "Panguipulli", "destino": "Los Lagos", "salida": "09:30", "empresa": "Tur Bus", "anden": "Andén 3", "estado": "En ruta", "tipo": "salida", "precio": 2800, "dias": "Diario"}
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
            print(f"⚠️ Error consultando horarios: {e}")

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
        print(f"⚠️ Error al filtrar: {e}")

    return jsonify(lista)

@app.route('/api/horarios/<int:id>/estado', methods=['PUT'])
def actualizar_estado(id):
    datos = request.get_json() or {}
    nuevo_estado = datos.get('estado')
    
    if not nuevo_estado:
        return jsonify({"success": False, "message": "Estado no proporcionado"}), 400
        
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "message": "Sin conexión con BD"}), 500

    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE horarios SET estado = %s WHERE id = %s;", (nuevo_estado, id))
            conn.commit()
            conn.close()
            return jsonify({"success": True, "message": "Estado actualizado"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    datos = request.get_json() or {}
    email = datos.get('email', '').strip()
    password = datos.get('password', '').strip()

    if not email or not password:
        return jsonify({"success": False, "message": "Ingresa correo y contraseña"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "message": "Error de conexión con la base de datos"}), 500

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, nombre, email, rol FROM usuarios WHERE LOWER(email) = LOWER(%s) AND password = %s;", 
                (email, password)
            )
            usuario = cur.fetchone()
            conn.close()
            
            if usuario:
                return jsonify({"success": True, "user": limpiar_fila(usuario)}), 200
            else:
                return jsonify({"success": False, "message": "Usuario no registrado o datos incorrectos"}), 401
    except Exception as e:
        return jsonify({"success": False, "message": "Error interno del servidor"}), 500

@app.route('/api/register', methods=['POST'])
@app.route('/api/registro', methods=['POST'])
def register():
    datos = request.get_json() or {}
    nombre = datos.get('nombre', '').strip()
    email = datos.get('email', '').strip()
    password = datos.get('password', '').strip()
    rol = datos.get('rol', 'pasajero')

    if not nombre or not email or not password:
        return jsonify({"success": False, "message": "Todos los campos son obligatorios"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "message": "Error de conexión con la base de datos"}), 500

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM usuarios WHERE LOWER(email) = LOWER(%s);", (email,))
            if cur.fetchone():
                conn.close()
                return jsonify({"success": False, "message": "El correo ya está registrado"}), 400

            cur.execute(
                "INSERT INTO usuarios (nombre, email, password, rol) VALUES (%s, %s, %s, %s);", 
                (nombre, email, password, rol)
            )
            conn.commit()
            conn.close()
            return jsonify({"success": True, "message": "Cuenta creada con éxito"}), 201
    except Exception as e:
        return jsonify({"success": False, "message": "No se pudo registrar la cuenta"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
    
