from flask import Flask, render_template, jsonify, request
import os
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

# ==========================================
# CONEXIÓN A LA BASE DE DATOS (SUPABASE)
# ==========================================
def get_db_connection():
    """
    Establece y retorna una conexión activa con la base de datos PostgreSQL.
    Obtiene las credenciales desde la variable de entorno DATABASE_URL.
    """
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise Exception("Error: La variable de entorno DATABASE_URL no está configurada.")
    return psycopg2.connect(url, cursor_factory=RealDictCursor)


# ==========================================
# RUTAS DE PÁGINAS (VISTAS HTML)
# ==========================================
@app.route('/')
def index():
    """Ruta principal: Muestra el mapa e interfaz pública para pasajeros."""
    return render_template('index.html')

@app.route('/usuario')
def usuario():
    """Ruta de perfil/dashboard: Muestra el panel según el rol del usuario."""
    return render_template('usuario.html')


# ==========================================
# API ENDPOINTS (DATOS Y AUTENTICACIÓN)
# ==========================================

@app.route('/api/horarios', methods=['GET'])
def get_horarios():
    """
    Obtiene la lista de horarios filtrados por tipo (salida/llegada)
    y opcionalmente por término de búsqueda (origen, destino, empresa).
    """
    tab = request.args.get('tab', 'salidas')
    query_search = request.args.get('q', '').strip()
    
    # Mapeo del tab visual al campo 'tipo' en la base de datos
    tipo_filtro = 'salida' if tab == 'salidas' else 'llegada'

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if query_search:
            sql = """
                SELECT * FROM horarios 
                WHERE tipo = %s AND (
                    LOWER(origen) LIKE LOWER(%s) OR 
                    LOWER(destino) LIKE LOWER(%s) OR 
                    LOWER(empresa) LIKE LOWER(%s)
                )
                ORDER BY salida ASC
            """
            wildcard = f"%{query_search}%"
            cur.execute(sql, (tipo_filtro, wildcard, wildcard, wildcard))
        else:
            sql = "SELECT * FROM horarios WHERE tipo = %s ORDER BY salida ASC"
            cur.execute(sql, (tipo_filtro,))

        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(rows)

    except Exception as e:
        print("❌ Error en consulta /api/horarios:", e)
        # Retorna lista vacía en lugar de romper la aplicación
        return jsonify([]), 500


@app.route('/api/registro', methods=['POST'])
def registro_usuario():
    """
    Registra un nuevo usuario en la tabla 'usuarios' de Supabase.
    """
    try:
        datos = request.get_json()
        nombre = datos.get('nombre')
        email = datos.get('email')
        password = datos.get('password')
        rol = datos.get('rol', 'pasajero')

        if not nombre or not email or not password:
            return jsonify({'error': 'Todos los campos son obligatorios'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # Insertar nuevo registro en la tabla usuarios
        sql = """
            INSERT INTO usuarios (nombre, email, password, rol) 
            VALUES (%s, %s, %s, %s) 
            RETURNING id, nombre, email, rol;
        """
        cur.execute(sql, (nombre, email, password, rol))
        nuevo_usuario = cur.fetchone()
        
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            'mensaje': 'Usuario registrado exitosamente',
            'usuario': nuevo_usuario
        }), 201

    except psycopg2.IntegrityError:
        return jsonify({'error': 'El correo electrónico ya está registrado'}), 400
    except Exception as e:
        print("❌ Error en registro:", e)
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/login', methods=['POST'])
def login_usuario():
    """
    Verifica las credenciales del usuario en la base de datos.
    """
    try:
        datos = request.get_json()
        email = datos.get('email')
        password = datos.get('password')

        conn = get_db_connection()
        cur = conn.cursor()

        sql = "SELECT id, nombre, email, rol FROM usuarios WHERE email = %s AND password = %s"
        cur.execute(sql, (email, password))
        usuario_encontrado = cur.fetchone()

        cur.close()
        conn.close()

        if usuario_encontrado:
            return jsonify({'status': 'ok', 'usuario': usuario_encontrado}), 200
        else:
            return jsonify({'error': 'Credenciales incorrectas'}), 401

    except Exception as e:
        print("❌ Error en login:", e)
        return jsonify({'error': 'Error interno del servidor'}), 500


# ==========================================
# INICIALIZACIÓN DEL SERVIDOR
# ==========================================
if __name__ == '__main__':
    # Obtiene el puerto asignado por Render (o 5000 por defecto en local)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)