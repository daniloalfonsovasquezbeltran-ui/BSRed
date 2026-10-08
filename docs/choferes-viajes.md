# Choferes y viajes

La cuenta con rol `empresa` usa su `usuarios.empresa_id` verificado en el servidor. Sólo puede crear, listar, suspender, reactivar o eliminar choferes de esa empresa. No se permite elegir una empresa o rol desde el formulario. El registro público sólo crea pasajeros o una empresa nueva con su propia identidad; nunca choferes o administradores. La cuenta de pruebas existente sin empresa vinculada se conserva y recibe un aviso explicativo.

El creador solicita nombre, correo y contraseña inicial (10–128 caracteres); teléfono y licencia son opcionales. Las nuevas contraseñas se almacenan con hash de Werkzeug. El inicio de sesión mantiene compatibilidad con las cuentas anteriores sin leer ni devolver su contraseña. Los listados no devuelven contraseñas ni credenciales de rastreo. Las tablas `usuarios` y `viajes` tienen RLS sin políticas públicas; las sesiones del backend autorizan las operaciones.

Suspender bloquea el inicio de sesión, revoca todas las sesiones y avisos del chofer y cancela su viaje activo. Reactivar permite un nuevo inicio de sesión; no revive sesiones ni viajes. Eliminar borra la cuenta y sus relaciones privadas; conserva el viaje cancelado sin referencia al chofer para no perder el registro operativo. El correo puede usarse para una cuenta nueva.

El chofer elige un recorrido de su empresa disponible para él y la patente del bus. Se permite un viaje activo por chofer y por patente de una empresa. Los horarios asignados a otro chofer no están disponibles. Se comprueba la ubicación antes de iniciar. Al llegar se pulsa «Finalizar viaje»: se revoca la credencial, se borran las coordenadas del viaje y se detiene el servicio local. Antes de cerrar sesión debe finalizarse el viaje.

El rastreo usa una credencial aleatoria específica del viaje; sólo su hash se guarda en la base. Autoriza el GPS durante las primeras 24 horas del viaje, puede reemplazarse desde la sesión del chofer y deja de servir al finalizar, suspender o eliminar. Se reciben coordenadas reales, precisión y hora de la medición. Se rechazan puntos fuera de rango, de más de dos minutos, demasiado futuros o con precisión peor que 2 km. Un punto antiguo nunca reemplaza uno nuevo. Los bloqueos y los índices únicos protegen los inicios, cierres y suspensiones concurrentes, también frente a favoritos y cierre de sesión.

El mapa consulta `/api/mapa/posiciones` cada 15 segundos mientras está visible. Muestra varios buses por recorrido, indicando GPS real, precisión y última actualización. La posición real funciona incluso si el trazado de una carretera no está confirmado. Si un viaje está activo sin GPS reciente, se oculta el punto; no se reemplaza por una estimación. Los recorridos sin viaje activo conservan las estimaciones por horario claramente identificadas. No se publica el nombre, correo o ID de la cuenta del chofer.

La app Android (`BSRed_app`) abre el sitio en WebView. Solicita ubicación precisa y notificaciones al abrirse, pero activa el servicio de ubicación sólo al iniciar el viaje. El puente nativo usa `WebViewCompat` limitado al origen HTTPS de BSRed y al marco principal. Las otras páginas se abren fuera de la app y no pueden acceder al puente. Se mantiene la verificación normal de TLS, se bloquean archivos locales y tráfico HTTP, y el servicio no se exporta.

El servicio de tipo `location` funciona en primer plano con notificación persistente y actualiza aproximadamente cada 15 segundos. Se usa `LocationManager` de Android; no se exige Google Play Services. No se pide permiso de ubicación «siempre»: el servicio se inicia con la app visible y permiso de ubicación precisa. La cadencia depende del GPS y de la red. Si no hay red se conserva sólo la última medición en memoria y se reintenta; no se envía una medición vencida. También se verifica la credencial cuando falta GPS, para detener el servicio tras una revocación. No se registran ubicaciones ni tokens en logs. La credencial local está en preferencias privadas y el respaldo de la app está desactivado.

El vencimiento y la revocación se comprueban en el servidor. Sin conexión, el servicio puede seguir obteniendo la última medición local hasta que se finalice desde la app o recupere la red y compruebe su credencial; estas mediciones no llegan al mapa.

En navegador el seguimiento sólo funciona mientras la página permanece abierta y el sistema permite las actualizaciones. Para pantalla apagada o cambio de aplicación debe instalarse la nueva app Android. Los permisos denegados o el GPS apagado impiden iniciar; pueden revisarse con el botón de permisos. «Reanudar GPS» permite volver a una sesión de viaje desde la consola.

## Instalación y validación

Aplicar `migrations/004_choferes_viajes.sql` antes de desplegar el servidor. No altera las asociaciones existentes. Las cuentas de empresa nuevas se asocian automáticamente a la empresa creada en la misma transacción; una asociación existente ausente exige intervención explícita, sin inferir propiedad por nombre.

Servidor: ejecutar `python -m unittest discover -s tests -q` con `TEST_DATABASE_URL` de PostgreSQL local exclusivo. La suite usa datos ficticios y esquemas aislados; nunca debe apuntar a producción.

Android: JDK 17, SDK 34, Build Tools 34.0.0 y Gradle 8.13. Desde su repositorio ejecutar `bash gradlew :app:assembleDebug :app:lintDebug :app:testDebugUnitTest`. El wrapper tiene el SHA256 oficial de la distribución. Se retiraron plantillas Compose sin uso que impedían compilar el proyecto AppCompat original.

Las pruebas locales de navegador usan geolocalización simulada para comprobar el mapa y la gestión sin crear cuentas de producción. Compilar, pasar lint y ejecutar pruebas JVM no sustituye una prueba física de permisos, pantalla apagada, reconexión y consumo de batería. Probar el APK en un Android real antes de usarlo en una operación de buses. Para actualizar una instalación anterior, compilar y firmar con la misma clave usada en esa instalación; las claves de firma nunca deben compartirse en Git ni en chat.
