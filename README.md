# BSRed
Horarios, recorridos y operación de buses de Panguipulli.

La medición de visitantes y presencia para administradores se describe en
[Telemetría del sitio web](docs/telemetria.md).

Los trazados por carretera y la posición estimada de buses se describen en
[Recorridos y ubicación estimada](docs/mapa-rutas.md).

Los favoritos y las notificaciones antes de la salida se describen en
[Avisos al dispositivo](docs/avisos-dispositivo.md).

La gestión de empresas, flota, horarios, excepciones por fecha, paradas,
historial y auditoría se describe en [Operación](docs/operaciones.md).
Las empresas nuevas requieren revisión administrativa para publicar recorridos;
las paradas se muestran como oficiales después de confirmarlas.

El cambio de contraseña y los enlaces de recuperación de un uso se describen
en [Cuentas](docs/cuentas.md). Los avisos Android y las incidencias de los
recorridos están documentados en [Notificaciones](docs/notificaciones-android.md).
Para habilitar los proveedores sigue [Activar Firebase y correo](docs/activar-servicios.md).
Las claves privadas se configuran en Render y no se guardan en este repositorio.

La web permite consultar los horarios guardados sin conexión, mostrando su fecha
de actualización. Los favoritos guardados son de sólo lectura y se borran al
cerrar sesión o caducar la sesión local. Los datos sin conexión no confirman
posiciones GPS ni habilitan funciones de empresa o conductor.

Las migraciones nuevas se aplican en orden después de las existentes:
`005_operaciones.sql`, `006_cuentas.sql`, `007_notificaciones.sql` y
`008_programador_operativo.sql`. La última actualiza la función del programador
existente sin crear otro trabajo cron. La ETA requiere GPS reciente con precisión
y movimiento suficientes; no contempla tráfico y se oculta cuando faltan datos.

Las pruebas usan PostgreSQL 16 exclusivo en localhost, con nombre terminado
en `_test`. Con `TEST_DATABASE_URL` apuntando a esa base:

```bash
python -m unittest discover -s tests -q
```

Las pruebas truncan sus tablas locales. No deben apuntar a producción.
