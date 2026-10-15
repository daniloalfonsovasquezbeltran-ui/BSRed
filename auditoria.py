"""Auditoría transaccional privada: nunca registra contraseñas ni credenciales."""
from datetime import date, datetime
import re

from psycopg2.extras import Json


DETAIL_KEYS = frozenset({
    'empresa_id', 'campos', 'estado_validacion', 'activo', 'confirmada',
    'chofer_id', 'bus_id', 'horario_id', 'parada_ids', 'fecha', 'opera',
    'viaje_id', 'estado', 'cantidad', 'tipo',
})
SENSITIVE = re.compile(r'password|secret|token|credencial|hash|email|telefono|licencia|latitud|longitud', re.I)


def safe_details(details):
    result = {}
    for key, value in (details.items() if isinstance(details, dict) else ()):
        if key not in DETAIL_KEYS:
            continue
        if key == 'campos' and isinstance(value, (list, tuple)):
            result[key] = [str(item)[:50] for item in value[:40]
                           if isinstance(item, str) and not SENSITIVE.search(item)]
        elif key == 'parada_ids' and isinstance(value, (list, tuple)):
            result[key] = [item for item in value[:40] if type(item) is int and item > 0]
        elif isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        elif value is None or type(value) in (int, bool):
            result[key] = value
        elif isinstance(value, str) and not SENSITIVE.search(value):
            result[key] = value[:100]
    return result


def record_audit(cur, actor, action, entity, entity_id, details=None):
    """Usar el mismo cursor/transacción de la mutación; actor viene del servidor."""
    if not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}', action):
        raise ValueError('Acción de auditoría inválida')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,29}', entity):
        raise ValueError('Entidad de auditoría inválida')
    clean = safe_details(details)
    owner = clean.get('empresa_id', actor.get('empresa_id'))
    cur.execute('''INSERT INTO auditoria_eventos
        (actor_id,actor_rol,empresa_id,accion,entidad,entidad_id,detalles)
        VALUES(%s,%s,%s,%s,%s,%s,%s);''',
        (actor['id'], actor['rol'], owner, action, entity,
         str(entity_id)[:80] if entity_id is not None else None, Json(clean)))
