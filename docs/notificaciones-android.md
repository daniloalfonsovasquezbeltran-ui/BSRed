# Avisos operativos y notificaciones Android

La empresa aprobada publica avisos para sus propios recorridos activos desde
`/api/empresa/avisos`: retraso (1–720 minutos), cancelación o información.
El mensaje admite 5–500 caracteres y una fecha de Chile entre hoy y los
próximos 30 días. Se limita la publicación a cinco avisos por recorrido y
veinte por empresa en una hora. Crear y eliminar deja auditoría transaccional
sin copiar el texto libre, correos o tokens.

`GET /api/avisos?horario_id=ID` publica los avisos vigentes de empresas
aprobadas y recorridos activos. No expone al autor ni sus datos privados.
Publicar no ejecuta llamadas Push en la petición: la cola persistente entrega
los avisos a los dispositivos autorizados de quienes guardaron ese recorrido
como favorito. Un aviso puede seguir vigente después de la hora original de
salida, por ejemplo una cancelación o un retraso.

Los recordatorios de salida también funcionan en Android nativo. Tanto Web
Push como Android respetan `horario_excepciones`, días de operación, horas
especiales y `America/Santiago`; una fecha omitida no genera recordatorio.
No se asumen feriados: la empresa debe registrar las excepciones.
Los horarios inactivos y empresas sin aprobación no generan entregas.

## Configurar Firebase

1. En Firebase Console crea o selecciona un proyecto y añade una aplicación
   Android con el identificador **`com.example.bsred`**. Si se cambia ese
   identificador en el proyecto Android, registra el nuevo valor en Firebase.
2. Descarga su configuración Android y extrae los valores siguientes. Son
   configuración pública de cliente; no incluyen claves privadas de servidor.

   | Variable de Render | Campo de la configuración Android |
   | --- | --- |
   | `ANDROID_FIREBASE_API_KEY` | `client[].api_key[].current_key` |
   | `ANDROID_FIREBASE_APP_ID` | `client[].client_info.mobilesdk_app_id` |
   | `ANDROID_FIREBASE_PROJECT_ID` | `project_info.project_id` |
   | `ANDROID_FIREBASE_SENDER_ID` | `project_info.project_number` |

3. Habilita la API **Firebase Cloud Messaging API (HTTP v1)** del mismo
   proyecto. Crea una cuenta de servicio con permiso para enviar mensajes
   FCM; usa el rol específico de Firebase Cloud Messaging correspondiente,
   sin conceder propietario/editor del proyecto para esta función.
4. Genera su credencial JSON y guárdala **sólo como variable secreta** de
   Render `FCM_SERVICE_ACCOUNT_JSON`, conservando el JSON completo y los
   saltos de línea escapados de `private_key`. No subir ese archivo a Git,
   añadirlo al APK ni compartirlo por chat. La cuenta y la configuración
   pública deben corresponder al mismo proyecto.
5. Aplica las migraciones 005, 006 y 007 antes de desplegar este backend.
   Conserva el programador de Supabase y su secreto de disparo de avisos.
   El servicio gratuito de Render necesita ese disparador para despertarse;
   los threads no funcionan mientras el proceso está suspendido.
6. Instala el APK actualizado. Inicia sesión, guarda un recorrido como
   favorito y pulsa **Activar avisos Android**. Autoriza las notificaciones
   del sistema cuando Android lo solicite.

`GET /api/notificaciones/android/config` devuelve únicamente los cuatro
valores públicos y `disponible`. La app inicializa Firebase con ellos; no
requiere empaquetar la cuenta de servicio. La disponibilidad exige un par de
configuraciones consistente y una clave RSA válida. La autorización efectiva
de Google y la entrega física deben comprobarse con un dispositivo real.
Si faltan las claves, la web sigue funcionando y muestra la configuración
pendiente; no se anuncian notificaciones nativas como activas.

## Dispositivo y entrega

`POST /api/notificaciones/android/dispositivos` registra `{token}` desde una
sesión y origen de BSRed válidos. Una cookie privada identifica el dispositivo.
La respuesta sólo incluye éxito y un `binding_id` opaco; no devuelve el token.
La asociación idempotente conserva ese identificador. Reactivar o cambiar
el token lo renueva. Un token asociado a otra cuenta devuelve conflicto y no
se transfiere silenciosamente. `DELETE` revoca el dispositivo. Logout,
suspensión y cambios/restablecimientos de contraseña también lo revocan.

El bridge central `BSRedNativeCall` multiplexa respuestas por identificador
para que GPS y notificaciones puedan operar a la vez. Tras registrar, el JS
comunica `vincularNotificaciones {binding_id, token}` a Android. El servicio
nativo sólo muestra un mensaje cuyo `binding_id` coincida con su asociación
privada actual. Desvincular borra ese vínculo; un mensaje viejo pendiente no
se muestra en una cuenta nueva. Los tokens no se guardan en localStorage.

El emisor usa OAuth2 con JWT RS256 y FCM HTTP v1. Las URLs de OAuth y FCM son
fijas, conservan TLS y no siguen redirecciones. El mensaje es **data-only**:
Android comprueba tipo, URL de BSRed, expiración y evento antes de mostrarlo.
Los identificadores son `salida:HORARIO:UTC_EPOCH` u `operativo:AVISO`. Para
`salida_programada` se descarta si pasó `salida_en`; para los demás tipos se
usa `expira_en`. El TTL nunca supera el vencimiento del evento. El sistema
Android y la red pueden demorar o impedir la entrega; no existe garantía
de recepción en cada dispositivo.

`entregas_notificaciones` usa claves únicas por evento y dispositivo,
claims con `SKIP LOCKED`, leases de 90 segundos y hasta seis intentos con
backoff/`Retry-After`. Verifica de nuevo cuenta, favorito, titular del token,
aprobación empresarial y vigencia antes de enviar. Las respuestas FCM
`UNREGISTERED` desactivan ese token; un 404 del proyecto sin ese código no
prueba que el token haya caducado. Los registros terminales se conservan
30 días. RLS impide el acceso público a tokens, cola y registros operativos
directos; sólo las APIs autorizadas publican su vista permitida.

Las pruebas usan un PostgreSQL local aislado, claves de prueba efímeras y
transportes sustitutos. Verifican JWT firmado, payload data-only, permisos,
calendario, titularidad, caducidad, reintentos y concurrencia sin enviar
mensajes a Google ni a usuarios reales.

Referencias oficiales para la configuración y HTTP v1:
https://firebase.google.com/docs/cloud-messaging/auth-server y
https://firebase.google.com/docs/reference/fcm/rest/v1/projects.messages/send.
