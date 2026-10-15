"""Operación empresarial aislada por empresa, revisión y auditoría privada."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from functools import wraps
import math
import re

import psycopg2
from flask import jsonify, make_response, request

from auditoria import record_audit
from rutas import operating_days


class OperationError(ValueError):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def serialize(value):
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime('%H:%M')
    if isinstance(value, Decimal):
        return float(value)
    return value


def operation_day(cur, horario_id, fecha, dias, salida, llegada):
    """Resuelve una fecha civil de Chile. Feriados requieren excepción explícita."""
    cur.execute('SELECT opera,salida,llegada,motivo FROM horario_excepciones WHERE horario_id=%s AND fecha=%s;',
                (horario_id, fecha))
    exception = cur.fetchone()
    if exception:
        return {'opera': exception['opera'], 'salida': exception['salida'] or salida,
                'llegada': exception['llegada'] or llegada, 'motivo': exception['motivo'], 'excepcion': True}
    days = operating_days(dias)
    return {'opera': days is not None and fecha.weekday() in days,
            'salida': salida, 'llegada': llegada, 'motivo': None, 'excepcion': False}


def register_operations(app, connection, current_user, token_hash):
    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise OperationError('Completa los datos con un objeto JSON.')
        return data

    def text(value, label, maximum=100, required=True):
        if value is None and not required:
            return ''
        if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
            raise OperationError(f'Revisa el campo {label}.')
        value = value.strip()
        if required and not value:
            raise OperationError(f'Completa el campo {label}.')
        return value

    def positive_id(value, nullable=False):
        if nullable and value is None:
            return None
        if type(value) is not int or not 0 < value <= 2147483647:
            raise OperationError('El identificador no es válido.')
        return value

    def boolean(value):
        if type(value) is not bool:
            raise OperationError('Indica un valor verdadero o falso.')
        return value

    def clock(value, nullable=False):
        if value is None and nullable:
            return None
        if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?', value):
            raise OperationError('Indica una hora válida en formato HH:MM.')
        try:
            return time.fromisoformat(value)
        except ValueError:
            raise OperationError('Indica una hora válida en formato HH:MM.') from None

    def civil_date(value):
        try:
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError
            return date.fromisoformat(value)
        except ValueError:
            raise OperationError('Indica una fecha válida en formato YYYY-MM-DD.') from None

    def page():
        try:
            number = int(request.args.get('pagina', '1'))
            limit = int(request.args.get('limite', '20'))
            if not 1 <= number <= 100000 or not 1 <= limit <= 100:
                raise ValueError
        except ValueError:
            raise OperationError('La página debe ser positiva y el límite entre 1 y 100.') from None
        return number, limit

    def pagination(number, limit, total):
        return {'pagina': number, 'limite': limit, 'total': total, 'paginas': (total + limit - 1) // limit}

    def owned(cur, table, item_id, company_id, lock=True):
        # table sólo se recibe desde constantes del módulo.
        cur.execute(f'SELECT * FROM {table} WHERE id=%s AND empresa_id=%s' + (' FOR UPDATE;' if lock else ';'),
                    (item_id, company_id))
        result = cur.fetchone()
        if not result:
            raise OperationError('Recurso no encontrado en tu empresa.', 404)
        return result

    def driver(cur, driver_id, company_id):
        if driver_id is None:
            return
        # Referencias de usuarios antes de bloquear bus/horario: mismo orden que
        # inicio y suspensión de viaje, evitando inversiones por las FK.
        cur.execute("""SELECT id FROM usuarios WHERE id=%s AND empresa_id=%s
            AND LOWER(TRIM(rol))='chofer' AND suspendido=FALSE FOR SHARE;""", (driver_id, company_id))
        if not cur.fetchone():
            raise OperationError('El chofer debe estar activo y pertenecer a tu empresa.', 404)

    def inactive_trip(cur, entity, item, company_id):
        if entity == 'bus':
            cur.execute("""SELECT id FROM viajes WHERE empresa_id=%s AND estado='en_curso'
                AND (bus_id=%s OR patente=%s) LIMIT 1;""", (company_id, item['id'], item['patente']))
        else:
            cur.execute("SELECT id FROM viajes WHERE empresa_id=%s AND horario_id=%s AND estado='en_curso' LIMIT 1;",
                        (company_id, item['id']))
        if cur.fetchone():
            raise OperationError('Finaliza el viaje activo antes de modificar este recurso.', 409)

    @app.before_request
    def protect_operations_mutations():
        if request.method not in ('POST', 'PATCH', 'PUT', 'DELETE') or not request.path.startswith(('/api/empresa/', '/api/admin/')):
            return None
        origin = request.headers.get('Origin')
        if (origin and origin.rstrip('/') != request.host_url.rstrip('/')) or (
                not origin and request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site')):
            return jsonify(success=False, message='La solicitud debe realizarse desde BSRed.'), 403
        if (request.content_length and request.content_length > 8192) or len(request.get_data(cache=True)) > 8192:
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
                        user = current_user(cur, lock=request.method != 'GET')
                        if not user:
                            raise OperationError('Inicia sesión con una cuenta activa.', 401)
                        if request.method != 'GET' and user.get('requiere_cambio_password'):
                            raise OperationError('Cambia tu contraseña inicial antes de continuar.', 403,
                                                 'PASSWORD_CHANGE_REQUIRED')
                        if user['rol'] != role:
                            raise OperationError('Esta operación no corresponde a tu cuenta.', 403)
                        if role in ('empresa', 'chofer'):
                            if not user.get('empresa_id'):
                                raise OperationError('La cuenta no tiene una empresa asociada.', 409)
                            cur.execute('SELECT id,nombre,estado_validacion,revision_motivo FROM empresas WHERE id=%s FOR SHARE;',
                                        (user['empresa_id'],))
                            company = cur.fetchone()
                            if not company:
                                raise OperationError('La empresa asociada no está disponible.', 409)
                            user = {**user, '_empresa': company}
                        response = make_response(func(cur, user, *args, **kwargs))
                    if response.status_code < 400:
                        conn.commit()
                    else:
                        conn.rollback()
                    return response
                except OperationError as error:
                    if conn:
                        conn.rollback()
                    payload = {'success': False, 'message': str(error)}
                    if error.code:
                        payload['code'] = error.code
                    return jsonify(payload), error.status
                except psycopg2.IntegrityError:
                    if conn:
                        conn.rollback()
                    return jsonify(success=False, message='El registro está duplicado o tiene referencias incompatibles.'), 409
                except Exception as error:
                    if conn:
                        conn.rollback()
                    app.logger.error('No se pudo gestionar la operación: %s', type(error).__name__)
                    return jsonify(success=False, message='No se pudo completar la operación. Inténtalo nuevamente.'), 503
                finally:
                    if conn:
                        conn.close()
            return wrapped
        return decorate

    def bus_values(data, creating=False):
        result = {}
        if creating or 'patente' in data:
            plate = re.sub(r'[\s-]', '', text(data.get('patente'), 'patente', 20).upper())
            if not re.fullmatch(r'[A-Z0-9]{4,12}', plate) or not re.search('[A-Z]', plate) or not re.search('[0-9]', plate):
                raise OperationError('Indica una patente válida de 4 a 12 caracteres.')
            result['patente'] = plate
        if creating or 'capacidad' in data:
            value = data.get('capacidad')
            if type(value) is not int or not 1 <= value <= 100:
                raise OperationError('La capacidad debe ser un entero entre 1 y 100.')
            result['capacidad'] = value
        for key in ('accesible', 'activo'):
            if key in data or creating:
                result[key] = boolean(data.get(key, key == 'activo'))
        if 'chofer_id' in data or creating:
            result['chofer_id'] = positive_id(data.get('chofer_id'), nullable=True)
        if not result:
            raise OperationError('Indica al menos un campo para modificar.')
        return result

    def update(cur, table, item_id, values, extra=''):
        assignments = ','.join(f'{key}=%s' for key in values)
        cur.execute(f'UPDATE {table} SET {assignments},actualizado_en=CURRENT_TIMESTAMP{extra} WHERE id=%s;',
                    (*values.values(), item_id))

    @app.route('/api/empresa/buses', methods=['GET', 'POST'])
    @account('empresa')
    def operations_buses(cur, user):
        if request.method == 'GET':
            cur.execute('''SELECT b.*,u.nombre AS chofer_nombre FROM buses b
                LEFT JOIN usuarios u ON u.id=b.chofer_id WHERE b.empresa_id=%s ORDER BY b.activo DESC,b.patente,b.id;''', (user['empresa_id'],))
            return jsonify(success=True, buses=serialize(cur.fetchall()), empresa=user['_empresa'])
        values = bus_values(body(), True)
        driver(cur, values['chofer_id'], user['empresa_id'])
        cur.execute('''INSERT INTO buses(empresa_id,patente,capacidad,accesible,activo,chofer_id)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING id;''',
            (user['empresa_id'], values['patente'], values['capacidad'], values['accesible'], values['activo'], values['chofer_id']))
        item_id = cur.fetchone()['id']
        record_audit(cur, user, 'bus.creado', 'bus', item_id, {'campos': list(values)})
        return jsonify(success=True, id=item_id), 201

    @app.route('/api/empresa/buses/<int:item_id>', methods=['PATCH', 'DELETE'])
    @account('empresa')
    def operations_bus(cur, user, item_id):
        values = bus_values(body()) if request.method == 'PATCH' else {'activo': False}
        if 'chofer_id' in values:
            driver(cur, values['chofer_id'], user['empresa_id'])
        item = owned(cur, 'buses', item_id, user['empresa_id'])
        inactive_trip(cur, 'bus', item, user['empresa_id'])
        update(cur, 'buses', item_id, values)
        record_audit(cur, user, 'bus.actualizado' if request.method == 'PATCH' else 'bus.desactivado', 'bus', item_id,
                     {'campos': list(values), **{key: value for key, value in values.items() if key in ('activo', 'chofer_id')}})
        return jsonify(success=True)

    def schedule_values(data, creating=False):
        result = {}
        for key, limit in (('origen', 100), ('destino', 100), ('anden', 30), ('dias', 100)):
            if key in data or creating:
                result[key] = text(data.get(key, '' if key == 'anden' else None), key, limit, key != 'anden')
        if 'dias' in result and operating_days(result['dias']) is None:
            raise OperationError('Indica días reconocibles, por ejemplo Diario o Lunes a Viernes.')
        for key, options, default in (('tipo', ('salida', 'llegada'), 'salida'), ('sentido', ('ida', 'vuelta'), 'ida')):
            if key in data or creating:
                value = text(data.get(key, default), key, 20).lower()
                if value not in options:
                    raise OperationError(f'El campo {key} no es válido.')
                result[key] = value
        for key in ('salida', 'llegada'):
            if key in data or creating:
                result[key] = clock(data.get(key), nullable=key == 'llegada')
        if 'precio' in data or creating:
            value = data.get('precio')
            if value is not None and (type(value) is not int or not 0 <= value <= 10000000):
                raise OperationError('El precio debe ser un entero no negativo.')
            result['precio'] = value
        if 'chofer_id' in data or creating:
            result['chofer_id'] = positive_id(data.get('chofer_id'), nullable=True)
        if 'activo' in data or creating:
            result['activo'] = boolean(data.get('activo', True))
        if not result:
            raise OperationError('Indica al menos un campo para modificar.')
        return result

    def attach_stops(cur, rows):
        if not rows:
            return rows
        ids = [row['id'] for row in rows]
        cur.execute('''SELECT hp.horario_id,hp.orden,p.id,p.nombre,p.latitud,p.longitud,p.accesible,p.activa,p.confirmada
            FROM horario_paradas hp JOIN paradas_oficiales p ON p.id=hp.parada_id
            JOIN horarios h ON h.id=hp.horario_id AND h.empresa_id=p.empresa_id
            WHERE hp.horario_id=ANY(%s) ORDER BY hp.horario_id,hp.orden;''', (ids,))
        by_id = {row['id']: row for row in rows}
        for row in rows:
            row['paradas'] = []
        for stop in cur.fetchall():
            by_id[stop['horario_id']]['paradas'].append(dict(stop))
        return rows

    @app.route('/api/empresa/horarios', methods=['GET', 'POST'])
    @account('empresa')
    def operations_schedules(cur, user):
        if request.method == 'GET':
            cur.execute('''SELECT h.*,e.nombre AS empresa,u.nombre AS chofer_nombre FROM horarios h
                JOIN empresas e ON e.id=h.empresa_id LEFT JOIN usuarios u ON u.id=h.chofer_id
                WHERE h.empresa_id=%s ORDER BY h.activo DESC,h.salida,h.id;''', (user['empresa_id'],))
            return jsonify(success=True, horarios=serialize(attach_stops(cur, cur.fetchall())), empresa=user['_empresa'])
        values = schedule_values(body(), True)
        if values['salida'] == values['llegada']:
            raise OperationError('Salida y llegada deben ser distintas.')
        driver(cur, values['chofer_id'], user['empresa_id'])
        columns = ['empresa_id', *values.keys()]
        cur.execute(f"INSERT INTO horarios({','.join(columns)}) VALUES({','.join(['%s'] * len(columns))}) RETURNING id;",
                    (user['empresa_id'], *values.values()))
        item_id = cur.fetchone()['id']
        record_audit(cur, user, 'horario.creado', 'horario', item_id, {'campos': list(values)})
        return jsonify(success=True, id=item_id), 201

    @app.route('/api/empresa/horarios/<int:item_id>', methods=['PATCH', 'DELETE'])
    @account('empresa')
    def operations_schedule(cur, user, item_id):
        values = schedule_values(body()) if request.method == 'PATCH' else {'activo': False}
        if 'chofer_id' in values:
            driver(cur, values['chofer_id'], user['empresa_id'])
        item = owned(cur, 'horarios', item_id, user['empresa_id'])
        if values.get('salida', item['salida']) == values.get('llegada', item['llegada']):
            raise OperationError('Salida y llegada deben ser distintas.')
        inactive_trip(cur, 'horario', item, user['empresa_id'])
        update(cur, 'horarios', item_id, values)
        record_audit(cur, user, 'horario.actualizado' if request.method == 'PATCH' else 'horario.desactivado',
                     'horario', item_id, {'campos': list(values), 'activo': values.get('activo', item['activo'])})
        return jsonify(success=True)

    @app.route('/api/empresa/horarios/<int:item_id>/excepciones', methods=['GET', 'POST'])
    @app.route('/api/empresa/horarios/<int:item_id>/excepciones/<fecha>', methods=['DELETE'])
    @account('empresa')
    def operations_exceptions(cur, user, item_id, fecha=None):
        item = owned(cur, 'horarios', item_id, user['empresa_id'], request.method != 'GET')
        if request.method == 'GET':
            cur.execute('SELECT * FROM horario_excepciones WHERE horario_id=%s ORDER BY fecha;', (item_id,))
            return jsonify(success=True, excepciones=serialize(cur.fetchall()))
        inactive_trip(cur, 'horario', item, user['empresa_id'])
        if request.method == 'DELETE':
            day = civil_date(fecha)
            cur.execute('DELETE FROM horario_excepciones WHERE horario_id=%s AND fecha=%s RETURNING fecha;', (item_id, day))
            if not cur.fetchone():
                raise OperationError('Excepción no encontrada.', 404)
            record_audit(cur, user, 'excepcion.eliminada', 'excepcion', f'{item_id}:{day}', {'horario_id': item_id, 'fecha': day})
            return jsonify(success=True)
        data = body()
        day = civil_date(data.get('fecha'))
        operates = boolean(data.get('opera'))
        departure = clock(data.get('salida'), True)
        arrival = clock(data.get('llegada'), True)
        if operates and (departure or item['salida']) == (arrival or item['llegada']):
            raise OperationError('Salida y llegada deben ser distintas.')
        reason = text(data.get('motivo', ''), 'motivo', 300, False)
        cur.execute('''INSERT INTO horario_excepciones(horario_id,fecha,opera,salida,llegada,motivo)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(horario_id,fecha) DO UPDATE SET
            opera=EXCLUDED.opera,salida=EXCLUDED.salida,llegada=EXCLUDED.llegada,motivo=EXCLUDED.motivo,
            actualizado_en=CURRENT_TIMESTAMP;''', (item_id, day, operates, departure, arrival, reason or None))
        record_audit(cur, user, 'excepcion.guardada', 'excepcion', f'{item_id}:{day}',
                     {'horario_id': item_id, 'fecha': day, 'opera': operates})
        return jsonify(success=True), 201

    def stop_values(data, creating=False):
        result = {}
        if 'nombre' in data or creating:
            result['nombre'] = text(data.get('nombre'), 'nombre', 100)
        for key, bound in (('latitud', 90), ('longitud', 180)):
            if key in data or creating:
                value = data.get(key)
                if type(value) not in (int, float) or not -bound <= value <= bound or not math.isfinite(value):
                    raise OperationError('Indica coordenadas válidas para la parada.')
                result[key] = value
        for key in ('accesible', 'activa'):
            if key in data or creating:
                result[key] = boolean(data.get(key, key == 'activa'))
        if not result:
            raise OperationError('Indica al menos un campo para modificar.')
        return result

    @app.route('/api/empresa/paradas', methods=['GET', 'POST'])
    @account('empresa')
    def operations_stops(cur, user):
        if request.method == 'GET':
            cur.execute('SELECT * FROM paradas_oficiales WHERE empresa_id=%s ORDER BY activa DESC,nombre,id;', (user['empresa_id'],))
            return jsonify(success=True, paradas=serialize(cur.fetchall()), empresa=user['_empresa'])
        values = stop_values(body(), True)
        columns = ['empresa_id', *values.keys()]
        cur.execute(f"INSERT INTO paradas_oficiales({','.join(columns)}) VALUES({','.join(['%s'] * len(columns))}) RETURNING id;",
                    (user['empresa_id'], *values.values()))
        item_id = cur.fetchone()['id']
        record_audit(cur, user, 'parada.creada', 'parada', item_id, {'campos': list(values)})
        return jsonify(success=True, id=item_id), 201

    @app.route('/api/empresa/paradas/<int:item_id>', methods=['PATCH', 'DELETE'])
    @account('empresa')
    def operations_stop(cur, user, item_id):
        values = stop_values(body()) if request.method == 'PATCH' else {'activa': False}
        owned(cur, 'paradas_oficiales', item_id, user['empresa_id'])
        cur.execute('''SELECT h.id FROM horarios h JOIN horario_paradas hp ON hp.horario_id=h.id
            WHERE hp.parada_id=%s AND h.empresa_id=%s ORDER BY h.id FOR UPDATE OF h;''', (item_id, user['empresa_id']))
        cur.fetchall()
        cur.execute('''SELECT v.id FROM viajes v JOIN horario_paradas hp ON hp.horario_id=v.horario_id
            WHERE hp.parada_id=%s AND v.empresa_id=%s AND v.estado='en_curso' LIMIT 1;''', (item_id, user['empresa_id']))
        if cur.fetchone():
            raise OperationError('Finaliza el viaje activo antes de modificar sus paradas.', 409)
        update(cur, 'paradas_oficiales', item_id, values, ',confirmada=FALSE,confirmada_por=NULL,confirmada_en=NULL')
        record_audit(cur, user, 'parada.actualizada' if request.method == 'PATCH' else 'parada.desactivada',
                     'parada', item_id, {'campos': list(values), 'confirmada': False})
        return jsonify(success=True)

    @app.route('/api/empresa/horarios/<int:item_id>/paradas', methods=['PUT'])
    @account('empresa')
    def operations_schedule_stops(cur, user, item_id):
        data = body()
        ids = data.get('parada_ids')
        if not isinstance(ids, list) or len(ids) > 40 or any(type(item) is not int or item <= 0 for item in ids) or len(set(ids)) != len(ids):
            raise OperationError('Indica una lista ordenada de hasta 40 paradas sin duplicados.')
        # Paradas antes del horario: evita inversión frente a editar una parada.
        if ids:
            cur.execute('SELECT id FROM paradas_oficiales WHERE id=ANY(%s) AND empresa_id=%s AND activa=TRUE ORDER BY id FOR SHARE;',
                        (ids, user['empresa_id']))
            if len(cur.fetchall()) != len(ids):
                raise OperationError('Todas las paradas deben estar activas y pertenecer a tu empresa.', 404)
        item = owned(cur, 'horarios', item_id, user['empresa_id'])
        inactive_trip(cur, 'horario', item, user['empresa_id'])
        cur.execute('DELETE FROM horario_paradas WHERE horario_id=%s;', (item_id,))
        for order, stop_id in enumerate(ids):
            cur.execute('INSERT INTO horario_paradas(horario_id,parada_id,orden) VALUES(%s,%s,%s);', (item_id, stop_id, order))
        record_audit(cur, user, 'horario.paradas', 'horario', item_id, {'parada_ids': ids})
        return jsonify(success=True)

    @app.route('/api/admin/empresas', methods=['GET'])
    @account('admin')
    def operations_admin_companies(cur, user):
        cur.execute('''SELECT id,nombre,estado_validacion,revision_motivo,validada_en,validada_por
            FROM empresas ORDER BY CASE estado_validacion WHEN 'pendiente' THEN 0 ELSE 1 END,nombre,id;''')
        return jsonify(success=True, empresas=serialize(cur.fetchall()))

    @app.route('/api/admin/empresas/<int:item_id>/revision', methods=['PATCH'])
    @account('admin')
    def operations_company_review(cur, user, item_id):
        data = body()
        state = data.get('estado_validacion')
        if state not in ('pendiente', 'aprobada', 'rechazada'):
            raise OperationError('Indica un estado de validación reconocido.')
        reason = text(data.get('motivo', ''), 'motivo', 500, False)
        if state == 'rechazada' and not reason:
            raise OperationError('Indica el motivo del rechazo.')
        cur.execute('SELECT id FROM empresas WHERE id=%s FOR UPDATE;', (item_id,))
        if not cur.fetchone():
            raise OperationError('Empresa no encontrada.', 404)
        cur.execute('''UPDATE empresas SET estado_validacion=%s,revision_motivo=%s,
            validada_en=CURRENT_TIMESTAMP,validada_por=%s WHERE id=%s;''', (state, reason or None, user['id'], item_id))
        record_audit(cur, user, 'empresa.revisada', 'empresa', item_id, {'empresa_id': item_id, 'estado_validacion': state})
        return jsonify(success=True)

    @app.route('/api/admin/paradas', methods=['GET'])
    @account('admin')
    def operations_admin_stops(cur, user):
        cur.execute('''SELECT p.*,e.nombre AS empresa FROM paradas_oficiales p JOIN empresas e ON e.id=p.empresa_id
            ORDER BY p.confirmada,p.nombre,p.id;''')
        return jsonify(success=True, paradas=serialize(cur.fetchall()))

    @app.route('/api/admin/paradas/<int:item_id>/revision', methods=['PATCH'])
    @account('admin')
    def operations_stop_review(cur, user, item_id):
        confirmation = boolean(body().get('confirmada'))
        # Mismo orden empresa -> parada utilizado por sus propietarios.
        cur.execute('SELECT empresa_id FROM paradas_oficiales WHERE id=%s;', (item_id,))
        candidate = cur.fetchone()
        if not candidate:
            raise OperationError('Parada no encontrada.', 404)
        cur.execute('SELECT id FROM empresas WHERE id=%s FOR SHARE;', (candidate['empresa_id'],))
        cur.fetchone()
        cur.execute('SELECT id,activa,empresa_id FROM paradas_oficiales WHERE id=%s FOR UPDATE;', (item_id,))
        stop = cur.fetchone()
        if confirmation and not stop['activa']:
            raise OperationError('No se puede confirmar una parada inactiva.', 409)
        cur.execute('''UPDATE paradas_oficiales SET confirmada=%s,confirmada_por=%s,
            confirmada_en=CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
            actualizado_en=CURRENT_TIMESTAMP WHERE id=%s;''', (confirmation, user['id'] if confirmation else None, confirmation, item_id))
        record_audit(cur, user, 'parada.revisada', 'parada', item_id, {'empresa_id': stop['empresa_id'], 'confirmada': confirmation})
        return jsonify(success=True)

    def trip_query(cur, user, scope, active=False):
        number, limit = page()
        clauses = ['v.empresa_id=%s' if scope == 'empresa' else 'v.chofer_id=%s']
        params = [user['empresa_id'] if scope == 'empresa' else user['id']]
        if active:
            clauses.append("v.estado='en_curso'")
        state = request.args.get('estado')
        if state:
            if state not in ('en_curso', 'finalizado', 'cancelado'):
                raise OperationError('El estado del viaje no es válido.')
            clauses.append('v.estado=%s')
            params.append(state)
        lower = civil_date(request.args['fecha_desde']) if 'fecha_desde' in request.args else None
        upper = civil_date(request.args['fecha_hasta']) if 'fecha_hasta' in request.args else None
        if lower and upper and lower > upper:
            raise OperationError('La fecha inicial no puede superar la final.')
        if lower:
            clauses.append("(v.iniciado_en AT TIME ZONE 'America/Santiago')::date >= %s")
            params.append(lower)
        if upper:
            clauses.append("(v.iniciado_en AT TIME ZONE 'America/Santiago')::date <= %s")
            params.append(upper)
        where = ' AND '.join(clauses)
        cur.execute('SELECT COUNT(*) AS total FROM viajes v WHERE ' + where + ';', tuple(params))
        total = cur.fetchone()['total']
        # La ubicación se expone sólo mientras el viaje está activo. Nunca se
        # incluye rastreo_hash ni credenciales del conductor.
        cur.execute('''SELECT v.id,v.empresa_id,v.chofer_id,v.bus_id,v.horario_id,v.patente,v.estado,
            v.iniciado_en,v.finalizado_en,v.distancia_m,v.velocidad_mps,v.velocidad_en,v.muestras_movimiento,
            u.nombre AS chofer_nombre,h.origen,h.destino,
            EXTRACT(EPOCH FROM(COALESCE(v.finalizado_en,CURRENT_TIMESTAMP)-v.iniciado_en))::BIGINT AS duracion_s,
            CASE WHEN v.estado='en_curso' THEN v.latitud END AS latitud,
            CASE WHEN v.estado='en_curso' THEN v.longitud END AS longitud,
            CASE WHEN v.estado='en_curso' THEN v.precision_m END AS precision_m,
            CASE WHEN v.estado='en_curso' THEN v.ubicacion_en END AS ubicacion_en
            FROM viajes v LEFT JOIN usuarios u ON u.id=v.chofer_id JOIN horarios h ON h.id=v.horario_id
            WHERE ''' + where + ' ORDER BY v.iniciado_en DESC,v.id DESC LIMIT %s OFFSET %s;',
            (*params, limit, (number - 1) * limit))
        trips = cur.fetchall()
        if active:
            from geo import estimate_arrival, schedule_geometry, schedule_stops
            from rutas import RouteDefinitionError, RoutingUnavailable
            for trip in trips:
                stops, complete = schedule_stops(cur, trip['horario_id'])
                try:
                    geometry = schedule_geometry({'id': trip['horario_id'], 'origen': trip['origen'],
                                                  'destino': trip['destino']}, stops, complete, cache_only=True)
                except (RouteDefinitionError, RoutingUnavailable):
                    geometry = None
                trip.update(estimate_arrival(trip, geometry))
        return serialize(trips), pagination(number, limit, total)

    @app.route('/api/empresa/flota', methods=['GET'])
    @account('empresa')
    def operations_fleet(cur, user):
        cur.execute('''SELECT b.*,u.nombre AS chofer_nombre FROM buses b LEFT JOIN usuarios u ON u.id=b.chofer_id
            WHERE b.empresa_id=%s ORDER BY b.activo DESC,b.patente;''', (user['empresa_id'],))
        buses = serialize(cur.fetchall())
        trips, pages = trip_query(cur, user, 'empresa', True)
        return jsonify(success=True, buses=buses, viajes=trips, paginacion=pages, empresa=user['_empresa'])

    @app.route('/api/empresa/viajes', methods=['GET'])
    @account('empresa')
    def operations_company_history(cur, user):
        trips, pages = trip_query(cur, user, 'empresa')
        return jsonify(success=True, viajes=trips, paginacion=pages)

    @app.route('/api/chofer/viajes/historial', methods=['GET'])
    @account('chofer')
    def operations_driver_history(cur, user):
        trips, pages = trip_query(cur, user, 'chofer')
        return jsonify(success=True, viajes=trips, paginacion=pages)

    def audit_list(cur, user, admin=False):
        number, limit = page()
        clauses = [] if admin else ['empresa_id=%s']
        params = [] if admin else [user['empresa_id']]
        lower = civil_date(request.args['fecha_desde']) if 'fecha_desde' in request.args else None
        upper = civil_date(request.args['fecha_hasta']) if 'fecha_hasta' in request.args else None
        if lower and upper and lower > upper:
            raise OperationError('La fecha inicial no puede superar la final.')
        if lower:
            clauses.append("(creado_en AT TIME ZONE 'America/Santiago')::date >= %s")
            params.append(lower)
        if upper:
            clauses.append("(creado_en AT TIME ZONE 'America/Santiago')::date <= %s")
            params.append(upper)
        where = 'WHERE ' + ' AND '.join(clauses) if clauses else ''
        params = tuple(params)
        cur.execute('SELECT COUNT(*) AS total FROM auditoria_eventos ' + where + ';', params)
        total = cur.fetchone()['total']
        cur.execute('SELECT id,actor_id,actor_rol,empresa_id,accion,entidad,entidad_id,detalles,creado_en FROM auditoria_eventos '
                    + where + ' ORDER BY creado_en DESC,id DESC LIMIT %s OFFSET %s;', (*params, limit, (number - 1) * limit))
        return jsonify(success=True, auditoria=serialize(cur.fetchall()), paginacion=pagination(number, limit, total))

    @app.route('/api/empresa/auditoria', methods=['GET'])
    @account('empresa')
    def operations_company_audit(cur, user):
        return audit_list(cur, user)

    @app.route('/api/admin/auditoria', methods=['GET'])
    @account('admin')
    def operations_admin_audit(cur, user):
        return audit_list(cur, user, True)
