# Favoritos y avisos al dispositivo

Un usuario con sesión puede guardar un horario como favorito. Los avisos se
habilitan para salidas con origen Panguipulli y anticipación de 5, 10 o 15
minutos; la selección inicial es 10 minutos. Cada dispositivo debe autorizar
las notificaciones mediante el botón de la aplicación. Un favorito por sí
solo no autoriza notificaciones.

El aviso indica una **salida programada** del terminal de Panguipulli. Se
calcula con el horario y los días de operación publicados, en
`America/Santiago`; no usa GPS ni confirma la salida efectiva de un bus. El
horario de llegada no es necesario para recordar una salida. Una hora que no
existe durante el cambio al horario de verano se omite.

## Configuración privada del backend

Las variables de Render son:

- `VAPID_PUBLIC_KEY`: clave pública P-256, base64url, punto sin comprimir de
  65 bytes. Esta clave se comparte con el navegador.
- `VAPID_PRIVATE_KEY`: clave privada P-256, base64url de los 32 bytes privados
  o DER. Sólo se guarda en el backend; nunca se añade al repositorio.
- `VAPID_SUBJECT`: URI de contacto VAPID. La selección inicial es
  `https://bsred.onrender.com`.
- `PUSH_ENABLED=false`: pausa explícita de envíos. Si no se configura, un par
  VAPID válido habilita la función.
- `NOTIFICATIONS_SCHEDULER_ENABLED=true`: ejecuta el despachador cada 20
  segundos en los workers del servicio web. Su valor inicial es `false`.

`avisos.generate_vapid_keys()` genera un par sin guardar archivos ni imprimir
las claves. Un par ya en uso debe conservarse: cambiarlo puede requerir que
los dispositivos vuelvan a suscribirse.

El plan gratuito de Render puede suspender el servicio web por inactividad.
El thread del servicio no procesa avisos mientras el proceso está suspendido.
Para cubrir ese caso, el despliegue debe configurar un disparador externo
(por ejemplo, PostgreSQL `pg_cron` y `pg_net` de Supabase) que invoque el
endpoint interno protegido por `NOTIFICATION_CRON_TOKEN`. El token se guarda
como secreto tanto en Render como en Supabase Vault; no se incluye en URLs,
archivos públicos o logs. El thread aprovecha el proceso web existente y no
crea un servicio de pago.

La migración `002_favoritos_avisos.sql` crea las tablas y habilita RLS sin
políticas públicas. La migración `003_programador_avisos.sql`, exclusiva de
Supabase, habilita `pg_cron`/`pg_net` y registra `bsred-avisos-salida` cada minuto.
Su función sólo llama a Render cuando existe una salida favorita próxima con
dispositivos activos, días válidos y sin entrega confirmada. Empieza a despertar
Render hasta dos minutos antes del aviso para cubrir el arranque del servicio;
el despachador no envía antes de la anticipación elegida. No mantiene el servicio
despierto si no hay avisos próximos. El arranque, la red y el proveedor pueden
retrasar un aviso; su TTL y el service worker impiden mostrarlo tras la salida.

La función se ejecuta como propietario, con `search_path` vacío, y no es
invocable por `anon`/`authenticated`. Lee `bsred_notification_cron_token` de
Vault y envía el token en Authorization, nunca en la URL ni en la definición
del trabajo. El endpoint devuelve 202 al aceptar procesamiento en segundo
plano, no confirma que un dispositivo haya recibido la notificación.

Cerrar sesión revoca los avisos de este navegador. Los favoritos permanecen
guardados y los demás dispositivos siguen activos. Una cookie HttpOnly vincula
la suscripción al dispositivo; iniciar otra sesión no reactiva los avisos sin
pulsar Activar. Las mutaciones requieren JSON y el origen de la aplicación;
la suscripción también bloquea la fila de sesión para coordinarse con logout.

## Cola y privacidad

`favoritos_recorridos` relaciona usuario, horario y anticipación.
`suscripciones_push` guarda el endpoint, la clave pública del dispositivo,
el secreto de cifrado `auth` y su estado activo. El endpoint es una
capacidad privada y no se devuelve en listados ni se registra en logs.
`avisos_salida` conserva cada intento y estado, con una clave única por
suscripción, horario y fecha de salida UTC.

Los workers reclaman un aviso con `FOR UPDATE SKIP LOCKED`, token propio y
lease de 90 segundos, y cierran la conexión a PostgreSQL antes del envío HTTP.
Esto coordina varios workers y permite recuperar claims tras reinicios. Se
vuelve a comprobar el favorito, la suscripción y el horario antes de enviar.
Las preferencias de anticipación nuevas actualizan avisos que todavía no se
han intentado. Los errores temporales reintentan con espera creciente hasta
seis intentos y respetan `Retry-After`. Una respuesta 404/410 desactiva la
suscripción caducada. Los avisos vencidos no se envían y la cola terminal se
conserva 30 días.

El TTL del proveedor vence al llegar la hora de salida. El service worker también
debe descartar mensajes recibidos después de `salida_en`. Un `tag` estable
identifica el mismo horario y fecha: si una interrupción ocurre después de
que el proveedor aceptó el aviso, su reintento reemplaza esa notificación en
el dispositivo. El servidor no puede garantizar entrega exactamente una
vez ante una pérdida de confirmación HTTP.

Sólo se admiten endpoints HTTPS de proveedores reconocidos:
`fcm.googleapis.com`, `updates.push.services.mozilla.com`,
`web.push.apple.com` y un subdominio de `notify.windows.com`. Se rechazan IPs,
URLs internas, credenciales en la URL, puertos distintos de 443 y fragments.
El cliente de envío conserva la validación TLS y no sigue redirecciones.
Las claves de suscripción deben ser un punto P-256 válido de 65 bytes y un
secreto `auth` de 16 bytes.

## Disponibilidad en el dispositivo

Web Push depende de permisos y soporte del navegador. En iPhone/iPad se
requiere iOS/iPadOS 16.4 o posterior y la aplicación instalada en la pantalla
de inicio para autorizar notificaciones. La entrega también depende de la
conectividad y los ajustes del sistema; activar el permiso no garantiza que
el dispositivo muestre cada aviso.

Las pruebas del despachador usan PostgreSQL local y un emisor sustituto;
nunca envían notificaciones a suscripciones de producción. La validación
del cifrado y VAPID puede usar `send_web_push` con un transporte HTTP
interceptado sin acceso a la red.

Referencia del protocolo y la biblioteca:
https://github.com/web-push-libs/pywebpush#sending-data-using-webpush-one-call
