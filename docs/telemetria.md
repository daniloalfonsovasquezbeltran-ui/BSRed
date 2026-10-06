# Telemetría del sitio web

El administrador existente en `usuarios` entra con su correo y contraseña de la
base de datos. El servidor verifica el rol `admin`; modificar el navegador no
concede acceso. Las sesiones anteriores del navegador deben iniciar sesión de
nuevo después de actualizar la aplicación.

El panel muestra:

- **Visitantes de las últimas 24 horas:** navegadores únicos con actividad dentro
  de una ventana móvil de 24 horas, incluidos invitados y el propio administrador.
- **Visitantes en línea:** navegadores con una página visible y un aviso de
  actividad recibido hace menos de 90 segundos. El navegador avisa cada 30
  segundos y el panel se actualiza cada 10 segundos.

Las pestañas comparten una identidad anónima. Web Locks serializa los avisos
entre pestañas para evitar duplicados al abrir varias por primera vez. En
navegadores antiguos sin Web Locks, el primer acceso simultáneo puede producir
un duplicado; después de recibir la cookie las pestañas quedan deduplicadas.
Otro navegador, dispositivo o borrar cookies genera un visitante nuevo. Estas
cifras no identifican personas físicas ni miden pestañas ocultas, bots o clientes
que bloqueen JavaScript como si fueran personas verificadas.

El historial comienza al activar la medición. Las consultas anteriores de
`registro_consultas` no permiten reconstruir visitantes únicos. El gráfico de
consultas usa el día calendario de Chile y se presenta por separado.

## Activación

1. Ejecutar `migrations/001_telemetria_web.sql` contra la base usada por Render.
   La migración crea dos tablas y sus índices, habilita RLS sin políticas
   públicas y no altera las cuentas ni los horarios. Puede ejecutarse otra vez.
2. Configurar `DATABASE_URL` en Render. La aplicación usa su conexión PostgreSQL
   habitual, no un token de administración de Supabase.
3. Publicar el código y comprobar inicio de sesión con la cuenta real del
   administrador. La cookie es HttpOnly, SameSite=Lax y Secure en HTTPS.
4. Abrir el sitio desde otro navegador y comprobar que aumenta visitantes/en
   línea; cerrar ese navegador y esperar hasta 90 segundos más el intervalo de
   actualización para que deje de estar en línea.

Las tablas guardan hashes aleatorios de visitantes y sesiones, nunca IP ni
contraseñas nuevas. Las sesiones expiran en 24 horas. Como mantenimiento, pueden
eliminarse las sesiones expiradas y los visitantes sin actividad desde hace más
de 30 días sin afectar las métricas de 24 horas.

## Pruebas con una base local exclusiva

Las pruebas crean datos de ejemplo y vacían sus tablas. Rechazan servidores
externos y nombres de base que no terminen en `_test`.

```sh
docker run -d --name bsred-telemetry-test \
  -p 127.0.0.1:15432:5432 \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=bsred_test \
  -e POSTGRES_PASSWORD=bsred_local_fixture \
  -e POSTGRES_DB=bsred_test postgres:16
# Esperar a que pg_isready confirme que PostgreSQL acepta conexiones.
docker exec bsred-telemetry-test pg_isready -U bsred_test -d bsred_test
TEST_DATABASE_URL=postgresql://bsred_test:bsred_local_fixture@127.0.0.1:15432/bsred_test \
  python -m unittest discover -s tests -v
docker rm -f bsred-telemetry-test
```

Los valores anteriores son sólo credenciales de una base local de prueba.
