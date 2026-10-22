# Recorridos y ubicación estimada

La vista de calles usa un tema oscuro de OpenFreeMap con el estilo Liberty adaptado en
`mapa-base.json`. El estilo Liberty de OpenFreeMap, derivado de OSM Liberty y
Mapbox Open Styles, se adapta para BSRed eliminando puntos de interés y
priorizando nombres en español y aplicando colores oscuros con etiquetas claras. Los avisos de licencia y créditos están en
`licenses/`. Conserva nombres de localidades, sectores, calles y cursos de
agua. Se excluyen las capas de puntos de interés y aeropuertos; no aparecen
marcadores de negocios. Los nombres de localidades prefieren español cuando
la cartografía dispone de ese dato. Los recorridos, buses y paradas de BSRed
siguen siendo capas de Leaflet independientes del fondo.

El renderizador local es MapLibre GL JS 5.24.0, integrado con Leaflet mediante
`@maplibre/maplibre-gl-leaflet` 0.1.4; sus licencias están en `licenses/`.
`mapa-base.js` mantiene una vista sencilla de Esri Dark Gray con su capa de
nombres hasta completar la carga. Esa vista también permite usar el mapa si el
navegador no admite el renderizador o falla su carga inicial. Las imágenes Esri
se amplían desde el nivel nativo 16 hasta el 18 para evitar solicitar niveles
vacíos en la zona. La cartografía externa necesita conexión a internet; los
archivos de la aplicación se conservan mediante el service worker.

El mapa usa geometría vial de OpenStreetMap a través de OSRM. Ya no une el
origen y el destino con una línea recta. Cuando no hay paradas confirmadas,
las coordenadas representan centros de localidades. Si una empresa configura
un itinerario con dos o más paradas activas confirmadas por administración,
se consulta la carretera entre esas coordenadas en el orden registrado para
ese sentido. OSRM propone una ruta para
vehículos por carretera; esa propuesta no verifica por sí sola el itinerario
comercial de un bus ni restricciones específicas de circulación de buses.

Los horarios con un solo destino se muestran como tales. Un texto que agrupa
localidades con `/` no permite saber si son paradas sucesivas o servicios
independientes. Sin una fuente del operador, esos horarios conservan su texto
y muestran el motivo por el que su trazado no está confirmado. No se deducen
paradas sucesivas del texto; la empresa puede registrar el itinerario mediante
[la gestión de operación](operaciones.md).

Las localidades se contrastaron con OpenStreetMap/Nominatim. La información
turística municipal de [Siete Lagos](https://sietelagos.cl/) confirma conexiones
generales entre Panguipulli, Coñaripe, Choshuenco, Neltume y Puerto Fuy, pero
remite a los horarios e itinerarios de cada compañía. No demuestra el orden
de paradas de los horarios agrupados existentes.

## Posición del bus

Los viajes activos publican GPS real reciente mediante
`GET /api/mapa/posiciones`. El mapa oculta puntos de más de dos minutos y nunca
reemplaza un viaje activo sin señal por una posición programada. Las paradas
confirmadas incluyen nombre y accesibilidad; cada horario indica su sentido.

Cuando hay muestras recientes de movimiento con precisión suficiente y un
trazado vial ya disponible en caché, se calcula la distancia restante y una
ETA aproximada. Se retira con señal antigua, poca precisión, velocidad
insuficiente o desvío importante del recorrido. La ETA no contempla tráfico.
El sondeo GPS no inicia peticiones nuevas a OSRM.

Para recorridos sin viaje activo, durante una ventana de salida y
llegada válida el marcador muestra **Ubicación estimada por horario**: avanza
a velocidad uniforme por la distancia del trazado. No incluye retrasos,
detenciones, tráfico ni cancelaciones. Fuera del viaje programado se retira
el marcador. Las horas y los días se interpretan en `America/Santiago`, con
los cambios de hora de Chile, y se sincronizan con el reloj del servidor.

El navegador mueve el marcador cada 15 segundos y consulta las ventanas cada
60 segundos mientras la página está visible. Los refrescos no cambian el
encuadre elegido por el usuario. Colores y selección siguen vinculados al
horario correspondiente; una respuesta anterior no reemplaza nuevos filtros.

## Selección y vista Todos

Elegir un recorrido muestra exclusivamente su trazado, sus buses y sus paradas
confirmadas. Volver a pulsar ese recorrido conserva la selección. Si su trazado
no está disponible, se informa el problema sin mostrar trazados ajenos; su GPS
reciente puede seguir visible. Las respuestas tardías y los refrescos respetan
la selección.

El botón **Todos los recorridos** activa la vista general de los horarios que
cumplen los filtros actuales. El botón marcado y el título del mapa distinguen
esta vista de **Recorrido seleccionado**. Los filtros conservan la selección
mientras el recorrido siga incluido; si lo excluyen, vuelve la vista general.
Cambiar entre Salidas y Llegadas también restablece la vista general.

## Servidor y disponibilidad

`GET /api/mapa/ruta/<id>` devuelve GeoJSON, distancia, fuente, localidades,
ventanas UTC, paradas confirmadas, sentido y reloj del servidor. Las ventanas
respetan las excepciones de cada fecha. Sólo lee horarios; no agrega consultas a
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

La empresa puede ordenar sus paradas en el panel de Horarios. Sólo se emplea
ese itinerario cuando todas sus paradas están activas y confirmadas. Editar una
parada reinicia su confirmación; no se agregaron paradas inventadas.

Para los recorridos anteriores, `VERIFIED_ITINERARIES` en `rutas.py` admite origen, destino final, localidades
intermedias ordenadas, tipo de recorrido y nota de procedencia. Incorporar
sólo itinerarios confirmados por una fuente del servicio correspondiente.
No basta el orden de las palabras en el horario ni la cercanía geográfica.
Si son servicios separados, deben guardarse como horarios distintos con sus
propias salidas y llegadas antes de estimar un bus para cada uno.

Las pruebas de rutas se ejecutan junto con las de telemetría siguiendo
[la preparación de PostgreSQL local](telemetria.md#pruebas-con-una-base-local-exclusiva).
