Las cuentas nuevas de chofer reciben una clave inicial con cambio obligatorio. Las cuentas existentes conservan su estado. El usuario debe reemplazar la clave inicial antes de usar las funciones protegidas; el cambio admite claves de 10 a 128 caracteres.

`POST /api/cuentas/password` recibe `password_actual` y `password_nuevo`. Verifica claves hash y cuentas antiguas sin devolver su contraseña, actualiza el hash y rota la cookie HttpOnly de la sesión actual. Revoca las otras sesiones, los avisos de todos los dispositivos, las credenciales Android y los enlaces de recuperación anteriores. Con un viaje activo responde 409: primero hay que finalizarlo.

La recuperación requiere un proveedor de correo configurado. `GET /api/cuentas/config` informa `recuperacion_disponible`; si no hay proveedor, la interfaz lo explica y el endpoint devuelve 503 sin prometer un correo.

Render gratuito bloquea las conexiones SMTP salientes en 25, 465 y 587. Para este servicio se recomienda Brevo por HTTPS:

1. Crear una cuenta en Brevo y verificar el remitente `bsredpanguipulli@gmail.com` siguiendo las instrucciones del proveedor. La entrega desde un dominio propio autenticado puede requerir configurar SPF/DKIM; la aceptación del remitente depende del proveedor.
2. Crear una clave de API transaccional y guardarla en las variables privadas de Render como `BREVO_API_KEY`. No pegarla en el chat ni versionarla.
3. Definir `MAIL_FROM_EMAIL=bsredpanguipulli@gmail.com`, `MAIL_FROM_NAME=BSRed` y `PUBLIC_BASE_URL=https://bsred.onrender.com`.
4. Volver a desplegar el servicio y verificar el formulario con una cuenta propia. Las pruebas automatizadas usan transporte simulado y no mandan correos reales.

Como alternativa en un alojamiento que permita SMTP, se admiten `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` y `SMTP_FROM`. Puerto 465 usa TLS desde la conexión; 587 exige STARTTLS. Se verifica el certificado y no se permite una alternativa sin cifrado.

`POST /api/cuentas/recuperar` recibe `email` y devuelve el mismo 202 para una cuenta activa, un correo desconocido o una cuota agotada. El proveedor recibe el envío en segundo plano; los errores se registran únicamente por su clase, sin dirección, enlace ni clave. Se limitan las solicitudes por IP y correo mediante contadores persistentes con identificadores hash.

El enlace tiene la forma `/usuario#restablecer=TOKEN`. El fragmento no llega al servidor HTTP y la interfaz lo borra del historial inmediatamente. El token sólo queda en memoria del formulario; no se almacena en el navegador. La base conserva únicamente su hash, vence a los 30 minutos y sólo se consume una vez. Solicitar otro enlace invalida los anteriores.

`POST /api/cuentas/restablecer` recibe `token` y `password`. Bloquea primero el usuario y después el token, revalida vencimiento/uso/suspensión y revoca todas las sesiones, avisos y credenciales. Un restablecimiento también cancela los viajes activos y borra su ubicación GPS. No inicia una sesión automáticamente.

Aplicar `migrations/006_cuentas.sql` antes de desplegar. Las tablas privadas de recuperación y cuotas tienen RLS sin políticas públicas. Los endpoints no deben cachearse. Detrás de Render, la configuración de proxy del backend debe resolver la IP del cliente sólo a partir de saltos de confianza; el módulo no confía directamente en cabeceras reenviadas por el cliente.
