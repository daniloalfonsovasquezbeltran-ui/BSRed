# Recorridos y ubicación estimada

El mapa usa geometría vial de OpenStreetMap a través de OSRM. Ya no une el
origen y el destino con una línea recta. Las coordenadas representan centros
de localidades, no terminales ni paradas exactas. OSRM propone una ruta para
vehículos por carretera; esa propuesta no verifica por sí sola el itinerario
comercial de un bus ni restricciones específicas de circulación de buses.

Los horarios con un solo destino se muestran como tales. Un texto que agrupa
localidades con `/` no permite saber si son paradas sucesivas o servicios
independientes. Sin una fuente del operador, esos horarios conservan su texto
y muestran el motivo por el que su trazado no está confirmado. No se cambia
la base de datos ni se crea una posición para esos servicios.

Las localidades se contrastaron con OpenStreetMap/Nominatim. La información
turística municipal de [Siete Lagos](https://sietelagos.cl/) confirma conexiones
generales entre Panguipulli, Coñaripe, Choshuenco, Neltume y Puerto Fuy, pero
remite a los horarios e itinerarios de cada compañía. No demuestra el orden
de paradas de los horarios agrupados existentes.

## Posición del bus

La base actual no almacena coordenadas GPS. Durante una ventana de salida y
llegada válida, el marcador muestra **Ubicación estimada por horario**: avanza
a velocidad uniforme por la distancia del trazado. No incluye retrasos,
detenciones, tráfico ni cancelaciones. Fuera del viaje programado se retira
el marcador. Las horas y los días se interpretan en `America/Santiago`, con
los cambios de hora de Chile, y se sincronizan con el reloj del servidor.

El navegador mueve el marcador cada 15 segundos y consulta las ventanas cada
60 segundos mientras la página está visible. Los refrescos no cambian el
encuadre elegido por el usuario. Colores y selección siguen vinculados al
horario correspondiente; una respuesta anterior no reemplaza nuevos filtros.

## Servidor y disponibilidad

`GET /api/mapa/ruta/<id>` devuelve GeoJSON, distancia, fuente, localidades,
ventanas UTC y reloj del servidor. Sólo lee horarios; no agrega consultas a
la telemetría. Un itinerario sin definir devuelve 422, uno inexistente 404 y
una indisponibilidad de carreteras 503. Los horarios siguen visibles si falla
el mapa.

El servidor usa HTTPS verificado contra `router.project-osrm.org`, sin clave
API. Caché por recorrido durante siete días, bloqueo entre workers y máximo
una solicitud por segundo al proveedor. Si el proveedor falla, puede usarse
un trazado obtenido anteriormente durante hasta 30 días; la respuesta marca
que su caché está vencido. Nunca se reemplaza por una línea inventada.
`ROUTE_CACHE_DIR` permite cambiar el directorio; el predeterminado es
`/tmp/bsred-routing-cache`, efímero en Render. El servicio público de OSRM no
garantiza disponibilidad; un despliegue con mayor tráfico puede sustituirlo
por una instancia propia revisando `ROUTER_URL`.

## Confirmar itinerarios agrupados

`VERIFIED_ITINERARIES` en `rutas.py` admite origen, destino final, localidades
intermedias ordenadas, tipo de recorrido y nota de procedencia. Incorporar
sólo itinerarios confirmados por una fuente del servicio correspondiente.
No basta el orden de las palabras en el horario ni la cercanía geográfica.
Si son servicios separados, deben guardarse como horarios distintos con sus
propias salidas y llegadas antes de estimar un bus para cada uno.

Las pruebas de rutas se ejecutan junto con las de telemetría siguiendo
[la preparación de PostgreSQL local](telemetria.md#pruebas-con-una-base-local-exclusiva).
