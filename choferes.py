"""Gestión de choferes por empresa y viajes autenticados con GPS sólo durante el viaje."""
from datetime import datetime, timezone
from functools import wraps
import math
import re
import secrets

import psycopg2
from flask import jsonify, request, make_response
from werkzeug.security import generate_password_hash


def trip_json(row):
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in row.items() if key != 'rastreo_hash'}


def stop_driver(cur, driver_id):
    cur.execute("""UPDATE viajes SET estado='cancelado', finalizado_en=CURRENT_TIMESTAMP,
        rastreo_hash=NULL, latitud=NULL, longitud=NULL, ubicacion_en=NULL
        WHERE chofer_id=%s AND estado='en_curso';""", (driver_id,))
    cur.execute('UPDATE usuarios SET gps_activo=FALSE WHERE id=%s;', (driver_id,))
    cur.execute('DELETE FROM sesiones_web WHERE usuario_id=%s;', (driver_id,))
    cur.execute('UPDATE suscripciones_push SET activa=FALSE WHERE usuario_id=%s;', (driver_id,))
    cur.execute("""UPDATE avisos_salida a SET estado='expirado', reclamo_token=NULL, reclamo_hasta=NULL
        FROM suscripciones_push s WHERE a.suscripcion_id=s.id AND s.usuario_id=%s
        AND a.estado IN ('pendiente','enviando');""", (driver_id,))


def register_drivers(app, connection, current_user, token_hash):
    @app.before_request
    def protect_driver_mutations():
        if request.method not in ('POST', 'PATCH', 'DELETE') or not request.path.startswith(('/api/empresa/', '/api/chofer/')):
            return None
        origin = request.headers.get('Origin')
        if (origin and origin.rstrip('/') != request.host_url.rstrip('/')) or (
                not origin and request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site')):
            return jsonify(success=False, message='La solicitud debe realizarse desde BSRed.'), 403
        if request.content_length and request.content_length > 8192:
            return jsonify(success=False, message='Solicitud demasiado grande.'), 413
        if request.method != 'DELETE' and not request.is_json:
            return jsonify(success=False, message='La solicitud debe contener datos JSON.'), 400

    def account(role):
        def decorate(func):
            @wraps(func)
            def wrapped(*args, **kwargs):
                conn = None
                try:
                    conn = connection()
                    with conn.cursor() as cur:
                        user = current_user(cur)
                        if not user:
                            return jsonify(success=False, message='Inicia sesión con una cuenta activa.'), 401
                        if request.method != 'GET':
                            # Un mismo orden para iniciar viaje, recibir GPS y suspender/eliminar.
                            cur.execute('SELECT id FROM usuarios WHERE id=%s FOR UPDATE;', (user['id'],))
                            cur.fetchone()
                            user = current_user(cur)
                            if not user:
                                return jsonify(success=False, message='La cuenta ya no está activa.'), 401
                        if user['rol'] != role:
                            return jsonify(success=False, message='Esta operación no corresponde a tu cuenta.'), 403
                        if not user['empresa_id']:
                            return jsonify(success=False, message='La cuenta no tiene una empresa asociada. Contacta al administrador.'), 409
                        cur.execute('SELECT id FROM empresas WHERE id=%s;', (user['empresa_id'],))
                        if not cur.fetchone():
                            return jsonify(success=False, message='La empresa asociada ya no está disponible.'), 409
                        response = make_response(func(cur, user, *args, **kwargs))
                    if response.status_code < 400:
                        conn.commit()
                    else:
                        conn.rollback()
                    return response
                except psycopg2.IntegrityError:
                    return jsonify(success=False, message='El correo ya está registrado o el bus ya tiene un viaje activo.'), 409
                except Exception as error:
                    app.logger.error('No se pudo gestionar choferes o viajes: %s', type(error).__name__)
                    return jsonify(success=False, message='No se pudo completar la operación. Inténtalo nuevamente.'), 503
                finally:
                    if conn:
                        conn.close()
            return wrapped
        return decorate

    def schedules(cur, user, driver=False):
        cur.execute('''SELECT h.id,h.tipo,h.origen,h.destino,h.salida,h.llegada,h.dias,h.anden,e.nombre AS empresa
            FROM horarios h JOIN empresas e ON e.id=h.empresa_id WHERE h.empresa_id=%s
            ''' + (' AND (h.chofer_id IS NULL OR h.chofer_id=%s)' if driver else '') + ' ORDER BY h.salida,h.id;',
            (user['empresa_id'], user['id']) if driver else (user['empresa_id'],))
        return [{**row, 'salida': row['salida'].strftime('%H:%M') if row['salida'] else None,
                 'llegada': row['llegada'].strftime('%H:%M') if row['llegada'] else None} for row in cur.fetchall()]

    @app.route('/api/empresa/horarios')
    @account('empresa')
    def company_schedules(cur, user):
        return jsonify(success=True, horarios=schedules(cur, user))

    @app.route('/api/empresa/choferes', methods=['GET', 'POST'])
    @account('empresa')
    def drivers(cur, user):
        if request.method == 'GET':
            cur.execute('''SELECT id,nombre,email,telefono,licencia,suspendido FROM usuarios
                WHERE empresa_id=%s AND LOWER(TRIM(rol))='chofer' ORDER BY nombre,id;''', (user['empresa_id'],))
            rows=cur.fetchall()
            cur.execute('SELECT id,nombre FROM empresas WHERE id=%s;', (user['empresa_id'],))
            return jsonify(success=True, choferes=rows, empresa=cur.fetchone())
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(success=False, message='Completa los datos del chofer.'), 400
        values = {}
        for key, limit in [('nombre',100),('email',255),('telefono',30),('licencia',40),('password',128)]:
            value = data.get(key, '')
            if not isinstance(value, str) or len(value) > limit or any(ord(c)<32 for c in value):
                return jsonify(success=False, message='Revisa los campos del formulario.'), 400
            values[key] = value if key == 'password' else value.strip()
        if len(values['nombre'])<2 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']) or len(values['password'])<10:
            return jsonify(success=False, message='Indica nombre, correo válido y una contraseña de al menos 10 caracteres.'), 400
        cur.execute('SELECT 1 FROM usuarios WHERE LOWER(TRIM(email))=%s;', (values['email'].lower(),))
        if cur.fetchone():
            return jsonify(success=False, message='El correo ya está registrado.'), 409
        cur.execute('''INSERT INTO usuarios(nombre,email,password,password_hash,rol,empresa_id,telefono,licencia)
            VALUES(%s,%s,'',%s,'chofer',%s,%s,%s) RETURNING id;''',
            (values['nombre'],values['email'].lower(),generate_password_hash(values['password']),
             user['empresa_id'],values['telefono'] or None,values['licencia'] or None))
        return jsonify(success=True, id=cur.fetchone()['id']), 201

    @app.route('/api/empresa/choferes/<int:driver_id>', methods=['PATCH','DELETE'])
    @account('empresa')
    def change_driver(cur, user, driver_id):
        cur.execute("SELECT id FROM usuarios WHERE id=%s AND empresa_id=%s AND LOWER(TRIM(rol))='chofer' FOR UPDATE;",
                    (driver_id,user['empresa_id']))
        if not cur.fetchone():
            return jsonify(success=False, message='Chofer no encontrado en tu empresa.'), 404
        if request.method == 'PATCH':
            data = request.get_json(silent=True)
            if not isinstance(data,dict) or type(data.get('suspendido')) is not bool:
                return jsonify(success=False, message='Indica si deseas suspender o reactivar la cuenta.'), 400
            if data['suspendido']:
                stop_driver(cur,driver_id)
            cur.execute('UPDATE usuarios SET suspendido=%s WHERE id=%s;', (data['suspendido'],driver_id))
        else:
            stop_driver(cur,driver_id)
            cur.execute('UPDATE horarios SET chofer_id=NULL WHERE chofer_id=%s;', (driver_id,))
            cur.execute('DELETE FROM usuarios WHERE id=%s;', (driver_id,))
        return jsonify(success=True)

    @app.route('/api/chofer/estado')
    @account('chofer')
    def driver_state(cur, user):
        cur.execute("SELECT id,horario_id,patente,estado,iniciado_en,ubicacion_en FROM viajes WHERE chofer_id=%s AND estado='en_curso';",(user['id'],))
        trip=cur.fetchone()
        return jsonify(success=True,horarios=schedules(cur,user,True),viaje=trip_json(trip) if trip else None)

    @app.route('/api/chofer/viajes', methods=['POST'])
    @account('chofer')
    def start_trip(cur,user):
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or type(data.get('horario_id')) is not int or not isinstance(data.get('patente'),str):
            return jsonify(success=False,message='Elige un recorrido y la patente del bus.'),400
        plate=re.sub(r'[\s-]','',data['patente'].upper())
        if not re.fullmatch(r'[A-Z0-9]{4,12}',plate) or not re.search('[A-Z]',plate) or not re.search('[0-9]',plate):
            return jsonify(success=False,message='Indica una patente válida de 4 a 12 caracteres.'),400
        cur.execute('SELECT id FROM horarios WHERE id=%s AND empresa_id=%s AND (chofer_id IS NULL OR chofer_id=%s) FOR SHARE;',
                    (data['horario_id'],user['empresa_id'],user['id']))
        if not cur.fetchone():
            return jsonify(success=False,message='Ese recorrido no está disponible para tu cuenta.'),404
        token=secrets.token_urlsafe(32)
        cur.execute('''INSERT INTO viajes(empresa_id,chofer_id,horario_id,patente,rastreo_hash)
            VALUES(%s,%s,%s,%s,%s) RETURNING id,horario_id,patente,estado,iniciado_en;''',
            (user['empresa_id'],user['id'],data['horario_id'],plate,token_hash(token)))
        return jsonify(success=True,viaje=trip_json(cur.fetchone()),rastreo_token=token),201

    @app.route('/api/chofer/viajes/<int:trip_id>/credencial', methods=['POST'])
    @account('chofer')
    def refresh_tracking(cur,user,trip_id):
        token=secrets.token_urlsafe(32)
        cur.execute("""UPDATE viajes SET rastreo_hash=%s WHERE id=%s AND chofer_id=%s AND estado='en_curso'
            AND iniciado_en>CURRENT_TIMESTAMP-INTERVAL '24 hours' RETURNING id;""",(token_hash(token),trip_id,user['id']))
        if not cur.fetchone():
            return jsonify(success=False,message='El viaje ya no permite rastreo. Finalízalo e inicia uno nuevo.'),409
        return jsonify(success=True,rastreo_token=token)

    @app.route('/api/chofer/viajes/<int:trip_id>/finalizar', methods=['POST'])
    @account('chofer')
    def finish_trip(cur,user,trip_id):
        cur.execute("""UPDATE viajes SET estado='finalizado',finalizado_en=CURRENT_TIMESTAMP,
            rastreo_hash=NULL,latitud=NULL,longitud=NULL,ubicacion_en=NULL
            WHERE id=%s AND chofer_id=%s AND estado='en_curso' RETURNING id;""",(trip_id,user['id']))
        if not cur.fetchone():
            cur.execute("SELECT id FROM viajes WHERE id=%s AND chofer_id=%s AND estado='finalizado';",(trip_id,user['id']))
            if not cur.fetchone():
                return jsonify(success=False,message='Viaje activo no encontrado.'),404
        cur.execute('UPDATE usuarios SET gps_activo=FALSE WHERE id=%s;',(user['id'],))
        return jsonify(success=True)

    @app.route('/api/chofer/viajes/<int:trip_id>/rastreo')
    def tracking_state(trip_id):
        header=request.headers.get('Authorization','')
        token=header.removeprefix('Bearer ')
        if not header.startswith('Bearer ') or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
            return jsonify(success=False),401
        conn=None
        try:
            conn=connection()
            with conn.cursor() as cur:
                cur.execute("""SELECT v.id FROM viajes v JOIN usuarios u ON u.id=v.chofer_id
                    WHERE v.id=%s AND v.rastreo_hash=%s AND v.estado='en_curso' AND u.suspendido=FALSE
                    AND LOWER(TRIM(u.rol))='chofer' AND u.empresa_id=v.empresa_id
                    AND v.iniciado_en>CURRENT_TIMESTAMP-INTERVAL '24 hours';""",(trip_id,token_hash(token)))
                if not cur.fetchone():return jsonify(success=False),401
            return jsonify(success=True)
        except Exception as error:
            app.logger.error('No se pudo verificar rastreo: %s',type(error).__name__)
            return jsonify(success=False),503
        finally:
            if conn:conn.close()

    @app.route('/api/chofer/viajes/<int:trip_id>/ubicacion',methods=['POST'])
    def position(trip_id):
        authorization=request.headers.get('Authorization','')
        token=authorization.removeprefix('Bearer ')
        if not authorization.startswith('Bearer ') or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
            return jsonify(success=False,message='Credencial de rastreo requerida.'),401
        data=request.get_json(silent=True)
        try:
            if not isinstance(data,dict):raise ValueError()
            lat,lon,accuracy=(data[k] for k in ('latitud','longitud','precision_m'))
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (lat,lon,accuracy)):raise ValueError()
            at=datetime.fromisoformat(data['ubicacion_en'].replace('Z','+00:00'))
            age=(datetime.now(timezone.utc)-at).total_seconds()
            if not (-90<=lat<=90 and -180<=lon<=180 and 0<=accuracy<=2000 and -30<=age<=120):raise ValueError()
        except (ValueError,TypeError,KeyError,AttributeError):
            return jsonify(success=False,message='Ubicación inválida o demasiado antigua.'),400
        conn=None
        try:
            conn=connection()
            with conn.cursor() as cur:
                cur.execute('SELECT chofer_id,empresa_id FROM viajes WHERE id=%s AND rastreo_hash=%s;',(trip_id,token_hash(token)))
                row=cur.fetchone()
                if not row or not row['chofer_id']:return jsonify(success=False),401
                cur.execute("SELECT id FROM usuarios WHERE id=%s AND empresa_id=%s AND suspendido=FALSE AND LOWER(TRIM(rol))='chofer' FOR UPDATE;",(row['chofer_id'],row['empresa_id']))
                if not cur.fetchone():return jsonify(success=False),401
                cur.execute("""UPDATE viajes SET latitud=%s,longitud=%s,precision_m=%s,ubicacion_en=%s
                    WHERE id=%s AND rastreo_hash=%s AND estado='en_curso'
                    AND iniciado_en>CURRENT_TIMESTAMP-INTERVAL '24 hours'
                    AND (ubicacion_en IS NULL OR ubicacion_en<%s) RETURNING id;""",
                    (lat,lon,accuracy,at,trip_id,token_hash(token),at))
                updated=cur.fetchone()
                if not updated:
                    cur.execute("SELECT id FROM viajes WHERE id=%s AND rastreo_hash=%s AND estado='en_curso' AND iniciado_en>CURRENT_TIMESTAMP-INTERVAL '24 hours';",(trip_id,token_hash(token)))
                    if not cur.fetchone():return jsonify(success=False),401
                else:cur.execute('UPDATE usuarios SET gps_activo=TRUE WHERE id=%s;',(row['chofer_id'],))
            conn.commit()
            return jsonify(success=True,actualizada=bool(updated))
        except Exception as error:
            app.logger.error('No se pudo guardar ubicación: %s',type(error).__name__)
            return jsonify(success=False),503
        finally:
            if conn:conn.close()

    @app.route('/api/mapa/posiciones')
    def public_positions():
        conn=None
        try:
            conn=connection()
            with conn.cursor() as cur:
                cur.execute("""SELECT v.id AS viaje_id,v.horario_id,v.patente,
                    CASE WHEN v.ubicacion_en>CURRENT_TIMESTAMP-INTERVAL '2 minutes' THEN v.latitud END AS latitud,
                    CASE WHEN v.ubicacion_en>CURRENT_TIMESTAMP-INTERVAL '2 minutes' THEN v.longitud END AS longitud,
                    v.precision_m,v.ubicacion_en
                    FROM viajes v JOIN usuarios u ON u.id=v.chofer_id
                    WHERE v.estado='en_curso' AND u.suspendido=FALSE AND LOWER(TRIM(u.rol))='chofer'
                    AND u.empresa_id=v.empresa_id
                    AND v.iniciado_en>CURRENT_TIMESTAMP-INTERVAL '24 hours' ORDER BY v.ubicacion_en DESC;""")
                return jsonify(success=True,posiciones=[trip_json(row) for row in cur.fetchall()],servidor_en=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            app.logger.error('No se pudo consultar posiciones: %s',type(error).__name__)
            return jsonify(success=False,message='Ubicaciones temporalmente no disponibles.'),503
        finally:
            if conn:conn.close()
