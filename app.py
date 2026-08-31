import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request, send_from_directory
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')

DATABASE_URL = os.getenv('DATABASE_URL')

def obtener_conexion():
    """Conexión segura a la base de datos externa de Supabase."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

@app.route('/')
def root():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/usuario')
def usuario():
    return send_from_directory(app.static_folder, 'usuario.html')

@app.route('/api/usuarios', methods=['GET'])
def obtener_usuarios():
    try:
        conn = obtener_conexion()
        cur = conn.cursor()
        cur.execute("SELECT id, nombre, email, rol FROM usuarios;")
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

@app.route('/api/horarios', methods=['GET'])
def horarios():
    tab = request.args.get("tab", "").strip().lower()
    q = request.args.get("q", "").strip().lower()
    
    try:
        conn = obtener_conexion()
        cur = conn.cursor()
        
        query = """
            SELECT id, tipo, sector, origen, destino, empresa, 
                   to_char(salida, 'HH24:MI') as salida, 
                   to_char(llegada, 'HH24:MI') as llegada, 
                   anden, dias 
            FROM horarios 
            WHERE 1=1
        """
        params = []
        
        if tab in ("salidas", "llegadas"):
            tipo = "salida" if tab == "salidas" else "llegada"
            query += " AND tipo = %s"
            params.append(tipo)
            
        if q:
            query += " AND (LOWER(origen) LIKE %s OR LOWER(destino) LIKE %s OR LOWER(empresa) LIKE %s)"
            params.extend(['%'+q+'%', '%'+q+'%', '%'+q+'%'])
            
        cur.execute(query, tuple(params))
        datos = cur.fetchall()
        
        cur.close()
        conn.close()
        return jsonify(datos)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
