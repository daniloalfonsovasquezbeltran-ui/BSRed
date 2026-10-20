# Activar notificaciones Android y recuperación por correo

La aplicación y el servidor funcionan sin estas credenciales. Cada pantalla informa si el servicio está pendiente de configuración. Las credenciales privadas se guardan en **Render → servicio BSRed → Environment**, nunca en GitHub ni en el chat.

## Firebase para Android

1. Abre https://console.firebase.google.com/ con tu cuenta Google y crea un proyecto BSRed. Google Analytics es opcional para esta función.
2. Agrega una aplicación **Android** con el identificador exacto `com.example.bsred`.
3. Descarga su `google-services.json`. Este archivo contiene la configuración del cliente; no es la clave privada del servidor.
4. Añade estas variables en Render a partir de ese archivo:

| Variable en Render | Campo de google-services.json |
| --- | --- |
| `ANDROID_FIREBASE_PROJECT_ID` | `project_info.project_id` |
| `ANDROID_FIREBASE_SENDER_ID` | `project_info.project_number` |
| `ANDROID_FIREBASE_APP_ID` | `client[0].client_info.mobilesdk_app_id` |
| `ANDROID_FIREBASE_API_KEY` | `client[0].api_key[0].current_key` |

Si registraste varias aplicaciones, elige el cliente cuyo `package_name` sea `com.example.bsred`.

5. En **Configuración del proyecto → Cloud Messaging**, comprueba que esté habilitada la API de Firebase Cloud Messaging HTTP v1.
6. En **Configuración del proyecto → Cuentas de servicio**, genera una clave privada del servidor. Guarda el JSON completo como valor privado de `FCM_SERVICE_ACCOUNT_JSON` en Render. Esa clave no va en la aplicación, GitHub ni el chat. El proyecto de la cuenta debe coincidir con `ANDROID_FIREBASE_PROJECT_ID`.
7. Guarda los cambios del servicio en Render. Con el código nuevo desplegado, la aplicación obtiene sólo la configuración pública y permite activar los avisos desde la cuenta del usuario.

No se necesita incluir `google-services.json` en el proyecto Android: esta versión inicializa Firebase con la configuración pública que proporciona BSRed por HTTPS. Para actualizar una aplicación ya instalada, conserva la firma original.

## Correo desde Gmail por HTTPS

Para enviar recuperación desde **`bsredpanguipulli@gmail.com`**, usar Gmail API con OAuth. El plan gratuito de Render restringe SMTP; la API usa HTTPS. La cuenta Google que administra Firebase puede ser distinta de la cuenta Gmail remitente.

1. En el proyecto [`bsred-6e122`, habilita Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=bsred-6e122).
2. Configura el consentimiento en **Google Auth Platform** y solicita únicamente `https://www.googleapis.com/auth/gmail.send`, que permite enviar sin leer el buzón. Si el estado es **Testing**, añade `bsredpanguipulli@gmail.com` como usuario de prueba.
3. Crea un cliente OAuth de tipo **Aplicación web**. Añade exactamente `https://developers.google.com/oauthplayground` a sus URI de redirección autorizadas.
4. Abre [OAuth Playground](https://developers.google.com/oauthplayground). En el engranaje, activa **Use your own OAuth credentials** e introduce el ID y secreto de ese cliente. Autoriza el permiso `https://www.googleapis.com/auth/gmail.send` con la cuenta **`bsredpanguipulli@gmail.com`**.
5. Pulsa **Exchange authorization code for tokens**. Guarda el refresh token y las credenciales del cliente sólo en **Render → BSRed → Environment**:

| Variable | Valor |
| --- | --- |
| `GMAIL_CLIENT_ID` | ID del cliente OAuth propio |
| `GMAIL_CLIENT_SECRET` | Secreto de ese cliente, guardado sólo en Render |
| `GMAIL_REFRESH_TOKEN` | Refresh token autorizado por la cuenta remitente, guardado sólo en Render |
| `MAIL_FROM_EMAIL` | `bsredpanguipulli@gmail.com` |
| `MAIL_FROM_NAME` | `BSRed` |
| `PUBLIC_BASE_URL` | `https://bsred.onrender.com` |

6. Para uso continuado, revisa el estado de publicación OAuth. En una aplicación externa en **Testing**, el refresh token para este permiso caduca a los siete días. Usa **Production** y completa la verificación que Google requiera; los tokens siguen sujetos a revocación y otras causas de caducidad. Consulta [la documentación de expiración OAuth](https://developers.google.com/identity/protocols/oauth2#expiration).
7. Guarda las variables y despliega el servicio. `GET /api/cuentas/config` sólo comprueba que exista configuración válida; no demuestra autorización de Google ni entrega. Verifica la entrega con una cuenta propia.

El remitente debe ser la cuenta que autorizó OAuth. No se necesita su contraseña de Gmail ni una clave de cuenta de servicio Firebase. Los secretos OAuth y refresh tokens no se adjuntan al chat ni se guardan en el repositorio. Las pruebas automatizadas no envían correos.

## Alternativa: correo con Brevo por HTTPS

Brevo permite usar un dominio propio autenticado. [Sustituye los remitentes Gmail y otros dominios gratuitos](https://help.brevo.com/hc/en-us/articles/14925263522578-Comply-with-Gmail-Yahoo-and-Microsoft-s-requirements-for-email-senders), también en mensajes transaccionales, por una dirección como `nombre@ID.t-sender-sib.com`. Para conservar `bsredpanguipulli@gmail.com`, utiliza Gmail API.

1. Crea una cuenta en https://www.brevo.com/ y verifica el correo de la aplicación.
2. Configura un remitente con nombre **BSRed** y una dirección de tu dominio propio. Completa la verificación del remitente y la autenticación del dominio que solicite el proveedor.
3. En la sección **SMTP y API → API keys**, crea una clave de API.
4. Añade en Render:

| Variable | Valor |
| --- | --- |
| `BREVO_API_KEY` | Clave privada de la API, guardada sólo en Render |
| `MAIL_FROM_EMAIL` | Dirección del remitente verificado |
| `MAIL_FROM_NAME` | `BSRed` |
| `PUBLIC_BASE_URL` | `https://bsred.onrender.com` |

5. Guarda la configuración. La opción de recuperación se habilita cuando el servidor detecta el proveedor configurado. Comprueba también en Brevo que la cuenta tenga autorizado el envío transaccional.

La presencia de credenciales no garantiza la entrega: la verificación del remitente, la autorización del proveedor y la conectividad deben funcionar. Los correos de recuperación sólo se envían cuando el usuario solicita recuperar su cuenta. Los enlaces son de un uso y caducan a los 30 minutos.

## Validación en un teléfono

Instala la versión nueva, inicia sesión y activa los avisos del dispositivo. Comprueba un recorrido favorito, una demora/cancelación y el cierre de sesión. Para GPS prueba pantalla apagada, pérdida y recuperación de red, GPS desactivado y permisos revocados. Las pruebas automáticas y la compilación no sustituyen este piloto físico.
