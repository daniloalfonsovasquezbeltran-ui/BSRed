Las cuentas nuevas de chofer reciben una clave inicial con cambio obligatorio. Las cuentas existentes conservan su estado. El usuario debe reemplazar la clave inicial antes de usar las funciones protegidas; el cambio admite claves de 10 a 128 caracteres.

`POST /api/cuentas/password` recibe `password_actual` y `password_nuevo`. Verifica claves hash y cuentas antiguas sin devolver su contraseña, actualiza el hash y rota la cookie HttpOnly de la sesión actual. Revoca las otras sesiones, los avisos de todos los dispositivos, las credenciales Android y los enlaces de recuperación anteriores. Con un viaje activo responde 409: primero hay que finalizarlo.

La recuperación requiere un proveedor de correo configurado. `GET /api/cuentas/config` informa `recuperacion_disponible`; sólo comprueba la configuración local, no la autorización del proveedor ni la entrega. Si no hay proveedor, la interfaz lo explica y el endpoint devuelve 503 sin prometer un correo.

Para conservar exactamente `bsredpanguipulli@gmail.com` como remitente, usar Gmail API por HTTPS. Render gratuito bloquea SMTP saliente en 25, 465 y 587.

1. Habilitar Gmail API en el proyecto Google Cloud `bsred-6e122` y crear un cliente OAuth de aplicación web con URI de redirección `https://developers.google.com/oauthplayground`.
2. En OAuth Playground, usar ese cliente propio y autorizar únicamente `https://www.googleapis.com/auth/gmail.send`, iniciando sesión con **`bsredpanguipulli@gmail.com`**. Intercambiar el código por tokens.
3. Guardar `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` y `GMAIL_REFRESH_TOKEN` sólo en Render. Definir también `MAIL_FROM_EMAIL=bsredpanguipulli@gmail.com`, `MAIL_FROM_NAME=BSRed` y `PUBLIC_BASE_URL=https://bsred.onrender.com`. No se usa la contraseña de Gmail ni la cuenta de servicio Firebase.
4. Revisar el consentimiento OAuth: con una aplicación externa en estado Testing y este permiso, el refresh token caduca a los siete días. Para uso continuado, pasar a Production y completar la verificación que Google requiera. Los tokens también pueden revocarse o caducar por otras causas.
5. Volver a desplegar y consultar la configuración. Verificar la entrega del formulario con una cuenta propia; las pruebas automatizadas sustituyen el transporte y no envían correos.

Los pasos completos están en [Activar Gmail](activar-servicios.md#correo-desde-gmail-por-https).

Brevo sigue disponible por HTTPS mediante `BREVO_API_KEY`, `MAIL_FROM_EMAIL`, `MAIL_FROM_NAME` y `PUBLIC_BASE_URL`, con un remitente y dominio autenticados. [Brevo sustituye los remitentes de dominios gratuitos, incluido Gmail](https://help.brevo.com/hc/en-us/articles/14925263522578-Comply-with-Gmail-Yahoo-and-Microsoft-s-requirements-for-email-senders), incluso en correo transaccional, por una dirección de su propio dominio. Por eso esa opción no conserva el remitente Gmail exacto.

Como alternativa en un alojamiento que permita SMTP, se admiten `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` y `SMTP_FROM`. Puerto 465 usa TLS desde la conexión; 587 exige STARTTLS. Se verifica el certificado y no se permite una alternativa sin cifrado.

`POST /api/cuentas/recuperar` recibe `email` y devuelve el mismo 202 para una cuenta activa, un correo desconocido o una cuota agotada. El proveedor recibe el envío en segundo plano; los errores se registran únicamente por su clase, sin dirección, enlace ni clave. Se limitan las solicitudes por IP y correo mediante contadores persistentes con identificadores hash.

El enlace tiene la forma `/usuario#restablecer=TOKEN`. El fragmento no llega al servidor HTTP y la interfaz lo borra del historial inmediatamente. El token sólo queda en memoria del formulario; no se almacena en el navegador. La base conserva únicamente su hash, vence a los 10 minutos y sólo se consume una vez. El servidor aplica ese máximo desde la creación también a los enlaces antiguos emitidos con un plazo mayor. Solicitar otro enlace invalida los anteriores.

El correo incluye una versión HTML con el estilo de BSRed y un botón para restablecer la contraseña, además de una alternativa de texto con el mismo enlace. El diseño está en `templates/correo-recuperacion.html`; usa estilos en línea y no carga imágenes, fuentes ni seguimiento externos. Gmail y SMTP envían ambas versiones como `multipart/alternative`; Brevo recibe `htmlContent` y `textContent`.

`POST /api/cuentas/restablecer` recibe `token` y `password`. Bloquea primero el usuario y después el token, revalida vencimiento/uso/suspensión y revoca todas las sesiones, avisos y credenciales. Un restablecimiento también cancela los viajes activos y borra su ubicación GPS. No inicia una sesión automáticamente.

Aplicar `migrations/006_cuentas.sql` antes de desplegar. Las tablas privadas de recuperación y cuotas tienen RLS sin políticas públicas. Los endpoints no deben cachearse. Detrás de Render, la configuración de proxy del backend debe resolver la IP del cliente sólo a partir de saltos de confianza; el módulo no confía directamente en cabeceras reenviadas por el cliente.
