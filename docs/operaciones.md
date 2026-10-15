# Empresas, flota y operación

Aplicar `migrations/005_operaciones.sql` después de las migraciones 001, 002 y
004. Es transaccional y repetible. Las empresas existentes conservan estado
`aprobada`; el valor predeterminado para nuevas empresas es `pendiente`.
Las cuentas pendientes pueden preparar buses, horarios y paradas. La publicación
pública y el inicio de viajes requieren aprobación en el backend principal.

`register_operations(app, connection, current_user, token_hash)` registra las
rutas usando sesiones existentes y roles consultados en PostgreSQL. Las
mutaciones exigen mismo origen y JSON de hasta 8 KiB, salvo DELETE sin cuerpo.
El backend deriva la empresa de la sesión y nunca acepta cambiar el propietario
mediante el formulario. Las tablas nuevas tienen RLS habilitado sin políticas
públicas: se consultan únicamente desde el backend.

## Recursos empresariales

| Recurso | Métodos | Campos principales |
| --- | --- | --- |
| `/api/empresa/buses` | GET, POST | `patente`, `capacidad` (1–100), `accesible`, `activo`, `chofer_id` opcional |
| `/api/empresa/buses/<id>` | PATCH, DELETE | Mismos campos; DELETE desactiva |
| `/api/empresa/horarios` | GET, POST | `tipo`, `origen`, `destino`, `salida`, `llegada`, `dias`, `anden`, `precio`, `sentido`, `activo`, `chofer_id` |
| `/api/empresa/horarios/<id>` | PATCH, DELETE | DELETE desactiva sin eliminar el historial |
| `/api/empresa/horarios/<id>/excepciones` | GET, POST | `fecha`, `opera`, `salida`, `llegada`, `motivo` |
| `/api/empresa/horarios/<id>/excepciones/<fecha>` | DELETE | Fecha civil `YYYY-MM-DD` |
| `/api/empresa/paradas` | GET, POST | `nombre`, `latitud`, `longitud`, `accesible`, `activa` |
| `/api/empresa/paradas/<id>` | PATCH, DELETE | Editar reinicia la confirmación; DELETE desactiva |
| `/api/empresa/horarios/<id>/paradas` | PUT | `parada_ids`: lista ordenada, propia, activa y sin duplicados |

Las patentes se normalizan sin espacios ni guiones y son únicas por empresa.
Los conductores asignados deben ser choferes propios no suspendidos. Los
recursos con un viaje activo no pueden modificarse hasta finalizarlo. Los
bloqueos siguen usuario, empresa, referencias a conductor y recurso para
coordinarse con el inicio de viajes.

Las horas usan `HH:MM`; las fechas corresponden a Chile. `tipo` admite `salida`
o `llegada`; `sentido`, `ida` o `vuelta`. El precio es un entero no negativo o
null. Los días se reconocen como `Diario`, rangos como `Lunes a Viernes` o
listas de días. Una llegada anterior a la salida representa un viaje nocturno.

`operation_day(cur, horario_id, fecha, dias, salida, llegada)` devuelve
`{opera, salida, llegada, motivo, excepcion}`. Una excepción de fecha reemplaza
la operación semanal y puede cambiar sus horas. No se presupone operación ni
suspensión en feriados: la empresa debe registrar una excepción explícita.

## Revisión administrativa

`GET /api/admin/empresas` muestra empresas y revisión. El administrador usa
`PATCH /api/admin/empresas/<id>/revision` con `estado_validacion` (`pendiente`,
`aprobada` o `rechazada`) y `motivo`; rechazar exige motivo.

`GET /api/admin/paradas` muestra las propuestas. Sólo
`PATCH /api/admin/paradas/<id>/revision` con `confirmada` puede confirmar una
parada activa. Crear o editar una parada no la convierte en oficial. No se
agregan ubicaciones o paradas inventadas.

## Flota, historial y auditoría

`GET /api/empresa/flota` devuelve `buses`, viajes activos y la empresa. Los
viajes incluyen conductor, patente, horario, duración, distancia acumulada y
última ubicación mientras el viaje está activo, con su fecha y precisión.
No se devuelven tokens de rastreo, hashes ni contraseñas. Una ubicación antigua
debe interpretarse por `ubicacion_en`, no como posición actual.

`GET /api/empresa/viajes` y `GET /api/chofer/viajes/historial` aceptan
`fecha_desde`, `fecha_hasta`, `estado`, `pagina` y `limite` (1–100). Responden
`viajes` y `paginacion: {pagina, limite, total, paginas}`. Las fechas filtran el
inicio según `America/Santiago`; cada conductor sólo ve sus propios viajes.

`GET /api/empresa/auditoria` limita los eventos a la propia empresa;
`GET /api/admin/auditoria` permite al administrador revisarlos todos. Ambos
usan la misma paginación y devuelven `auditoria`.

`auditoria.record_audit` participa en la misma transacción que la mutación.
Los detalles usan una lista explícita de campos permitidos; se excluyen
credenciales, texto libre, contactos y coordenadas. Los errores no dejan una
mutación parcialmente confirmada ni filtran mensajes privados de PostgreSQL.

Las pruebas de integración sólo admiten PostgreSQL en localhost y una base
terminada en `_test`; usan un esquema aislado `operaciones_test`.
