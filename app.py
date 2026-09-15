import os
import re
import dns.resolver
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, jsonify, session

app = Flask(__name__, template_folder='.', static_folder='.')
app.secret_key = os.environ.get('SECRET_KEY', 'clave_secreta_bsred_panguipulli')

# Conexión a Supabase mediante la variable de entorno DATABASE_URL
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    url = DATABASE_URL
    if not url:
        raise ValueError("La variable DATABASE_URL no está configurada.")
    
    # Ajustar prefijo de PostgreSQL si viene con formato antiguo
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
        
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    return conn

def es_correo_valido(email):
    # Validar formato con Expresiones Regulares
    patron = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    if not re.match(patron, email):
        return False, "El formato del correo electrónico no es válido."
    
    # Validar que el dominio tenga servidores MX (servidores de correo reales)
    try:
        dominio = email.split('@')[1]
        registros = dns.resolver.resolve(dominio, 'MX')
        if len(registros) > 0:
            return True, "Correo válido"
        else:
            return False, "El dominio del correo no posee servidores de correo activos."
    except Exception:
        return False, f"El dominio '@{email.split('@')[1]}' no existe en internet."

# -------------------------------------------------------------
# RUTAS DE PÁGINAS Y APIs
# -------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')

# API: Obtener todos los horarios
@app.route('/api/horarios', methods=['GET'])
def get_horarios():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
            SELECT 
                h.id, 
                e.nombre AS empresa, 
                h.origen, 
                h.destino, 
                TO_CHAR(h.salida, 'HH24:MI') AS salida, 
                TO_CHAR(h.llegada, 'HH24:MI') AS llegada, 
                h.tipo, 
                h.dias, 
                h.anden, 
                h.estado, 
                h.precio,
                u.nombre AS chofer
            FROM horarios h
            JOIN empresas e ON h.empresa_id = e.id
            LEFT JOIN usuarios u ON h.chofer_id = u.id
            ORDER BY h.salida ASC;
        """
        cur.execute(query)
        horarios = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(horarios)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# API: Registro de usuarios con validación de correo real
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    nombre = data.get('nombre', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()
    rol = data.get('rol', 'pasajero')

    if not nombre or not email or not password:
        return jsonify({'success': False, 'message': 'Todos los campos son obligatorios'}), 400

    # Validar dominio de correo real
    es_valido, msg = es_correo_valido(email)
    if not es_valido:
        return jsonify({'success': False, 'message': msg}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    # Verificar si el correo ya existe en Supabase
    cur.execute("SELECT id FROM usuarios WHERE LOWER(email) = %s", (email,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Este correo ya está registrado en BSRed.'}), 400

    # Insertar usuario
    cur.execute(
        "INSERT INTO usuarios (nombre, email, password, rol) VALUES (%s, %s, %s, %s) RETURNING id;",
        (nombre, email, password, rol)
    )
    nuevo_id = cur.fetchone()['id']
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'success': True, 'message': '¡Cuenta creada con éxito! Ya puedes iniciar sesión.'})

# API: Login de usuarios con detección de errores
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.id, u.nombre, u.email, u.password, u.rol, u.empresa_id, e.nombre AS empresa_nombre 
        FROM usuarios u
        LEFT JOIN empresas e ON u.empresa_id = e.id
        WHERE LOWER(u.email) = %s
    """, (email,))
    user = cur.fetchone()

    if not user:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'El correo ingresado no existe en el sistema.'}), 404

    if user['password'] != password:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Contraseña incorrecta.'}), 401

    session['user_id'] = user['id']
    session['rol'] = user['rol']
    cur.close()
    conn.close()

    return jsonify({
        'success': True,
        'message': f'¡Bienvenido/a {user["nombre"]}!',
        'user': {
            'id': user['id'],
            'nombre': user['nombre'],
            'email': user['email'],
            'rol': user['rol'],
            'empresa_id': user['empresa_id'],
            'empresa_nombre': user['empresa_nombre']
        }
    })

# API: Cambiar estado del recorrido (Chofer / Empresa / Admin)
@app.route('/api/horarios/<int:id>/estado', methods=['PUT'])
def cambiar_estado(id):
    data = request.json
    nuevo_estado = data.get('estado')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE horarios SET estado = %s WHERE id = %s", (nuevo_estado, id))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'success': True, 'message': 'Estado del viaje actualizado correctamente.'})

if __name__ == '__main__':
    app.run(debug=True)