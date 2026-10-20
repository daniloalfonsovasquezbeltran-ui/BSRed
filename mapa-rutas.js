(function (global) {
    'use strict';

    const PALETAS = {
        salidas: ['#3b82f6', '#06b6d4', '#10b981', '#6366f1', '#8b5cf6'],
        llegadas: ['#f59e0b', '#ec4899', '#f97316', '#ef4444', '#eab308']
    };

    function prepararCamino(geometria) {
        if (!geometria || geometria.type !== 'LineString' || !Array.isArray(geometria.coordinates)
            || geometria.coordinates.length < 2) throw new Error('Geometría no disponible');
        const puntos = geometria.coordinates.map(coordenada => {
            if (!Array.isArray(coordenada) || !Number.isFinite(coordenada[0]) || !Number.isFinite(coordenada[1])
                || Math.abs(coordenada[0]) > 180 || Math.abs(coordenada[1]) > 90) throw new Error('Geometría no disponible');
            return [coordenada[1], coordenada[0]];
        });
        const acumuladas = [0];
        const rad = valor => valor * Math.PI / 180;
        for (let i = 1; i < puntos.length; i++) {
            const a = puntos[i - 1], b = puntos[i];
            const senoLat = Math.sin(rad(b[0] - a[0]) / 2);
            const senoLon = Math.sin(rad(b[1] - a[1]) / 2);
            const h = senoLat ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * senoLon ** 2;
            acumuladas.push(acumuladas[i - 1] + 12742000 * Math.asin(Math.sqrt(Math.min(1, h))));
        }
        if (acumuladas[acumuladas.length - 1] <= 0) throw new Error('Geometría no disponible');
        return { puntos, acumuladas };
    }

    function interpolar(camino, proporcion) {
        const distancia = camino.acumuladas[camino.acumuladas.length - 1] * proporcion;
        let inicio = 0, fin = camino.acumuladas.length - 1;
        while (inicio + 1 < fin) {
            const medio = Math.floor((inicio + fin) / 2);
            if (camino.acumuladas[medio] <= distancia) inicio = medio;
            else fin = medio;
        }
        const tramo = camino.acumuladas[fin] - camino.acumuladas[inicio];
        const avance = tramo > 0 ? (distancia - camino.acumuladas[inicio]) / tramo : 0;
        return camino.puntos[inicio].map((valor, eje) => valor + (camino.puntos[fin][eje] - valor) * avance);
    }

    function crear(opciones) {
        const { map, L } = opciones;
        const informar = opciones.onEstado || (() => {});
        const detallar = opciones.onDetalle || (() => {});
        const informarRuta = opciones.onRuta || (() => {});
        let horarios = [], tab = 'salidas', seleccionado = null, generacion = 0;
        let cargando = false, desfaseServidor = 0, solicitudesActivas = 0, encuadrePendiente = false;
        const capas = new Map(), cache = new Map(), errores = new Map();
        const marcadoresGPS = new Map(), marcadoresParadas = new Map();
        let posicionesGPS = [], solicitandoGPS = false, ultimaFirmaGPS = null;
        let zoomEnCurso = false;
        const controladores = new Set(), cola = [];
        map.on('zoomstart', () => { zoomEnCurso = true; });
        map.on('zoomend', () => {
            zoomEnCurso = false;
            global.requestAnimationFrame(() => {
                if (encuadrePendiente) encuadrePendiente = !encuadrar();
            });
        });
        if (map.attributionControl) {
            map.attributionControl.addAttribution('Recorridos © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>');
        }

        function clasificacion(datos) {
            if (datos.tipo_recorrido === 'destino_unico') return 'Destino único';
            if (['paradas_confirmadas', 'paradas_sucesivas', 'con_paradas'].includes(datos.tipo_recorrido)) return 'Paradas sucesivas confirmadas';
            return '';
        }

        function gpsReciente(punto) {
            const fecha = Date.parse(punto.ubicacion_en);
            const antiguedad = Date.now() + desfaseServidor - fecha;
            return Number.isFinite(punto.latitud) && Number.isFinite(punto.longitud)
                && Math.abs(punto.latitud) <= 90 && Math.abs(punto.longitud) <= 180
                && Number.isFinite(fecha) && antiguedad >= -30000 && antiguedad < 120000;
        }

        function textoETA(punto) {
            const llegada = Date.parse(punto.llegada_estimada_en);
            if (!gpsReciente(punto) || punto.eta_estado !== 'disponible'
                || !Number.isFinite(punto.precision_m) || punto.precision_m < 0 || punto.precision_m > 100
                || !Number.isFinite(punto.eta_minutos) || punto.eta_minutos < 0
                || !Number.isFinite(punto.distancia_restante_m) || punto.distancia_restante_m < 0
                || !Number.isFinite(llegada)) return '';
            const hora = new Date(llegada).toLocaleTimeString('es-CL', {
                hour: '2-digit', minute: '2-digit', timeZone: 'America/Santiago'
            });
            return `Llegada estimada: ${Math.ceil(punto.eta_minutos)} min · ${hora} (Chile)`;
        }

        function motivoSinETA(punto) {
            const mensajes = {
                esperando_muestras: 'Reuniendo ubicaciones para estimar la llegada.',
                bus_detenido: 'Llegada estimada no disponible mientras el bus está detenido.',
                precision_insuficiente: 'Se necesita una ubicación más precisa para estimar la llegada.',
                fuera_recorrido: 'La posición recibida está fuera del trazado; llegada estimada no disponible.',
                sin_trazado: 'Llegada estimada no disponible para este trazado.',
                sin_gps_reciente: 'Esperando una ubicación reciente para estimar la llegada.'
            };
            return mensajes[punto.eta_estado] || 'Llegada estimada todavía no disponible.';
        }

        function emitirGPS() {
            const ids = Array.from(new Set(posicionesGPS.filter(gpsReciente)
                .map(punto => punto.horario_id).filter(id => Number.isInteger(id) && id > 0))).sort((a, b) => a - b);
            const firma = ids.join(',');
            if (firma === ultimaFirmaGPS) return;
            ultimaFirmaGPS = firma;
            document.dispatchEvent(new CustomEvent('bsred:gps-actualizado', { detail: { horario_ids: ids } }));
        }

        function quitarMarcador(capa) {
            if (capa.marcador) map.removeLayer(capa.marcador);
            capa.marcador = null;
        }

        function recorridoVisible(id) {
            return seleccionado == null || id === seleccionado;
        }

        function limpiar() {
            generacion++;
            controladores.forEach(controlador => controlador.abort());
            capas.forEach(capa => {
                map.removeLayer(capa.linea);
                quitarMarcador(capa);
            });
            capas.clear(); errores.clear(); horarios = []; seleccionado = null; cargando = false; encuadrePendiente = false;
            marcadoresGPS.forEach(marcador => map.removeLayer(marcador)); marcadoresGPS.clear();
            marcadoresParadas.forEach(entrada => map.removeLayer(entrada.marcador)); marcadoresParadas.clear();
            detallar('');
        }

        function vaciarCola() {
            while (solicitudesActivas < 2 && cola.length) {
                const tarea = cola.shift();
                if (tarea.generacion !== generacion) { tarea.resolver(null); continue; }
                solicitudesActivas++;
                tarea.ejecutar().then(tarea.resolver, tarea.rechazar).finally(() => {
                    solicitudesActivas--;
                    vaciarCola();
                });
            }
        }

        function solicitar(id, version) {
            return new Promise((resolver, rechazar) => {
                cola.push({ generacion: version, resolver, rechazar, ejecutar: async () => {
                    const controlador = new AbortController();
                    controladores.add(controlador);
                    let temporizador;
                    const vencimiento = new Promise((_, fallo) => {
                        temporizador = global.setTimeout(() => {
                            fallo(new Error('El trazado tardó demasiado')); controlador.abort();
                        }, 20000);
                        controlador.signal.addEventListener('abort', () => fallo(new Error('Solicitud cancelada')), { once: true });
                    });
                    try {
                        const solicitud = (async () => {
                            const respuesta = await fetch(`/api/mapa/ruta/${encodeURIComponent(id)}`, {
                                credentials: 'same-origin', cache: 'no-store', signal: controlador.signal
                            });
                            const datos = await respuesta.json();
                            if (!respuesta.ok || !datos.success) {
                                const mensaje = typeof datos.message === 'string' ? datos.message : datos.error;
                                const error = new Error(typeof mensaje === 'string' ? mensaje : 'Trazado no disponible');
                                error.mensajeUsuario = true;
                                throw error;
                            }
                            const reloj = Date.parse(datos.servidor_en);
                            if (!Number.isFinite(reloj) || Number(datos.horario_id) !== Number(id)) throw new Error('Trazado no disponible');
                            const camino = prepararCamino(datos.geometry);
                            return { datos, camino, recibido: Date.now(), desfase: reloj - Date.now() };
                        })();
                        return await Promise.race([solicitud, vencimiento]);
                    } finally {
                        global.clearTimeout(temporizador);
                        controladores.delete(controlador);
                    }
                } });
                vaciarCola();
            });
        }

        function ventanaActiva(capa) {
            if (!capa.dato.datos.estimacion_disponible || !Array.isArray(capa.dato.datos.ventanas)) return null;
            const ahora = Date.now() + desfaseServidor;
            for (const ventana of capa.dato.datos.ventanas) {
                const inicio = Date.parse(ventana.salida_en), fin = Date.parse(ventana.llegada_en);
                if (Number.isFinite(inicio) && Number.isFinite(fin) && fin > inicio && inicio <= ahora && ahora < fin) {
                    return { proporcion: (ahora - inicio) / (fin - inicio) };
                }
            }
            return null;
        }

        function popup(capa, esBus) {
            const contenedor = document.createElement('div');
            const nombre = document.createElement('strong');
            nombre.textContent = capa.horario.empresa || 'Servicio de buses';
            const recorrido = document.createElement('p');
            recorrido.textContent = `${capa.horario.origen || ''} → ${capa.horario.destino || ''}`;
            const horas = document.createElement('p');
            horas.textContent = `Salida ${capa.horario.salida || 'sin hora'} · Llegada ${capa.horario.llegada || 'sin hora'}`;
            const precision = document.createElement('p');
            precision.textContent = 'Ubicación estimada por horario';
            const aclaracion = document.createElement('small');
            aclaracion.textContent = esBus
                ? 'Trazado por carretera aproximado. Se estima el avance entre las horas programadas. La posición puede diferir de la ubicación real.'
                : `Trazado por carretera aproximado · OpenStreetMap / OSRM. ${capa.dato.datos.mensaje_estimacion || 'El bus se muestra durante el viaje programado.'}`;
            contenedor.append(nombre, recorrido, horas, precision, aclaracion);
            const tipo = clasificacion(capa.dato.datos);
            if (tipo) {
                const categoria = document.createElement('p');
                categoria.textContent = tipo;
                contenedor.append(categoria);
            }
            if (Array.isArray(capa.dato.datos.localidades)) {
                const lugares = capa.dato.datos.localidades.filter(lugar => typeof lugar === 'string');
                if (lugares.length) {
                    const itinerario = document.createElement('p');
                    itinerario.textContent = `Trazado: ${lugares.join(' → ')}`;
                    contenedor.append(itinerario);
                }
            }
            if (typeof capa.dato.datos.nota_itinerario === 'string') {
                const nota = document.createElement('small');
                nota.textContent = capa.dato.datos.nota_itinerario;
                contenedor.append(nota);
            }
            if (typeof capa.dato.datos.sentido === 'string' && capa.dato.datos.sentido) {
                const sentido = document.createElement('p');
                sentido.textContent = `Sentido: ${capa.dato.datos.sentido}`;
                contenedor.append(sentido);
            }
            if (typeof capa.dato.datos.itinerario_confirmado === 'boolean') {
                const confirmacion = document.createElement('small');
                confirmacion.textContent = capa.dato.datos.itinerario_confirmado
                    ? 'Itinerario con paradas confirmadas.' : 'Las paradas del itinerario completo todavía no están confirmadas.';
                contenedor.append(confirmacion);
            }
            return contenedor;
        }

        function actualizarDetalle() {
            const gps = posicionesGPS.find(p => p.horario_id === seleccionado);
            if (gps) {
                const reciente = gpsReciente(gps);
                detallar(`Bus ${gps.patente} · ${reciente ? 'Ubicación GPS real' : 'Viaje activo: esperando una ubicación GPS reciente'}${reciente ? ' · ' + (textoETA(gps) || motivoSinETA(gps)) : ''}`);
                return;
            }
            const capa = capas.get(seleccionado);
            if (!capa) {
                detallar(seleccionado == null ? '' : (errores.get(seleccionado) || 'Trazado no disponible para el recorrido seleccionado.'));
                return;
            }
            const mensaje = ventanaActiva(capa)
                ? 'Ubicación estimada por horario'
                : (capa.dato.datos.mensaje_estimacion || 'Sin viaje programado en curso');
            detallar(`${capa.horario.origen} → ${capa.horario.destino} · ${mensaje}`);
        }

        function actualizarEstado() {
            const visibles = horarios.filter(horario => recorridoVisible(horario.id));
            const trazadas = visibles.filter(horario => capas.has(horario.id)).length;
            const fallos = visibles.filter(horario => errores.has(horario.id));
            const buses = Array.from(capas.values()).filter(capa => recorridoVisible(capa.horario.id) && capa.marcador).length;
            const esperando = cargando && (seleccionado == null || (!trazadas && !fallos.length));
            let tipo = 'listo', texto;
            if (!visibles.length) { tipo = 'vacio'; texto = 'No hay horarios para estos filtros'; }
            else if (esperando) { tipo = 'cargando'; texto = `Trazando recorridos por carretera (${trazadas}/${visibles.length})…`; }
            else if (!trazadas) { tipo = 'error'; texto = seleccionado == null
                ? 'No se pudieron trazar los recorridos; los horarios siguen disponibles'
                : 'Trazado no disponible para el recorrido seleccionado'; }
            else {
                tipo = fallos.length ? 'parcial' : 'listo';
                texto = `${trazadas} ${trazadas === 1 ? 'recorrido' : 'recorridos'} por carretera · ${buses} buses con ubicación estimada`;
                const sinTrazado = fallos.filter(horario => !capas.has(horario.id)).length;
                if (sinTrazado) texto += ` · ${sinTrazado} sin trazado disponible`;
                if (fallos.length > sinTrazado) texto += ` · ${fallos.length - sinTrazado} trazados sin actualizar`;
            }
            if (marcadoresGPS.size) texto += ` · ${marcadoresGPS.size} buses con GPS real`;
            informar({ tipo, texto, trazadas, total: visibles.length, busesEstimados: buses });
            actualizarDetalle();
        }

        function moverBuses() {
            if (document.visibilityState !== 'visible') return;
            capas.forEach(capa => {
                if (!recorridoVisible(capa.horario.id)) { quitarMarcador(capa); return; }
                if (posicionesGPS.some(p => p.horario_id === capa.horario.id)) { quitarMarcador(capa); return; }
                const ventana = ventanaActiva(capa);
                if (!ventana) { quitarMarcador(capa); return; }
                const posicion = interpolar(capa.dato.camino, ventana.proporcion);
                if (capa.marcador) capa.marcador.setLatLng(posicion);
                else {
                    const icono = L.divIcon({
                        className: 'bus-estimado', iconSize: [34, 34], iconAnchor: [17, 17],
                        html: `<span role="img" aria-label="Bus: ubicación estimada por horario" style="display:flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;background:${capa.color};border:2px solid white;box-shadow:0 2px 8px #0008;font-size:20px">🚌</span>`
                    });
                    capa.marcador = L.marker(posicion, { icon: icono, title: 'Ubicación estimada por horario' })
                        .bindPopup(popup(capa, true), { autoPan: false }).addTo(map);
                    capa.marcador.on('click', () => opciones.onSeleccion && opciones.onSeleccion(capa.horario.id));
                }
            });
            moverGPS();
            moverParadas();
            actualizarEstado();
            emitirGPS();
        }

        function moverParadas() {
            const deseadas = new Map();
            capas.forEach(capa => {
                if (!recorridoVisible(capa.horario.id)) return;
                const paradas = Array.isArray(capa.dato.datos.paradas) ? capa.dato.datos.paradas : [];
                paradas.forEach(parada => {
                    if (parada.confirmada !== true || parada.activa === false || !Number.isInteger(parada.id) || parada.id <= 0
                        || !Number.isFinite(parada.latitud) || !Number.isFinite(parada.longitud)
                        || Math.abs(parada.latitud) > 90 || Math.abs(parada.longitud) > 180) return;
                    if (!deseadas.has(parada.id)) deseadas.set(parada.id, { parada, recorridos: [] });
                    deseadas.get(parada.id).recorridos.push({ capa, parada });
                });
            });
            deseadas.forEach((entrada, id) => {
                const parada = entrada.parada;
                const contenido = document.createElement('div');
                const nombre = document.createElement('strong'); nombre.textContent = parada.nombre;
                const accesibilidad = document.createElement('p');
                accesibilidad.textContent = parada.accesible === true ? 'Accesible según el registro.'
                    : parada.accesible === false ? 'No está marcada como accesible.' : 'Accesibilidad no informada.';
                contenido.append(nombre, accesibilidad);
                entrada.recorridos.forEach(({ capa, parada: vinculo }) => {
                    const recorrido = document.createElement('p');
                    recorrido.textContent = `${capa.horario.origen} → ${capa.horario.destino}${Number.isInteger(vinculo.orden) ? ' · Parada ' + vinculo.orden : ''}${typeof capa.dato.datos.sentido === 'string' ? ' · ' + capa.dato.datos.sentido : ''}`;
                    contenido.append(recorrido);
                });
                let previa = marcadoresParadas.get(id);
                if (!previa) {
                    const icono = L.divIcon({ className: 'parada-oficial', iconSize: [26, 26], iconAnchor: [13, 13],
                        html: '<span role="img" aria-label="Parada oficial confirmada" style="display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;background:#e2e8f0;color:#0f172a;border:2px solid #64748b;box-shadow:0 1px 5px #0008;font-size:16px">🚶</span>' });
                    const marcador = L.marker([parada.latitud, parada.longitud], { icon: icono, title: 'Parada oficial: ' + parada.nombre, zIndexOffset: -200 })
                        .bindPopup(contenido, { autoPan: false }).addTo(map);
                    previa = { marcador }; marcadoresParadas.set(id, previa);
                } else {
                    previa.marcador.setLatLng([parada.latitud, parada.longitud]);
                    previa.marcador.setPopupContent(contenido);
                }
            });
            marcadoresParadas.forEach((entrada, id) => {
                if (!deseadas.has(id)) { map.removeLayer(entrada.marcador); marcadoresParadas.delete(id); }
            });
        }

        function moverGPS() {
            const activos = new Set();
            for (const punto of posicionesGPS) {
                const horario = horarios.find(h => h.id === punto.horario_id);
                if (!horario || !recorridoVisible(horario.id) || !gpsReciente(punto)) continue;
                activos.add(punto.viaje_id);
                const contenido = document.createElement('div');
                for (const texto of [`${horario.origen} → ${horario.destino}`, `Bus ${punto.patente} · Ubicación GPS real`,
                    `Última actualización: ${new Date(punto.ubicacion_en).toLocaleTimeString('es-CL', { timeZone: 'America/Santiago' })}${Number.isFinite(punto.precision_m) ? ' · precisión aproximada ' + Math.round(punto.precision_m) + ' m' : ''}`,
                    textoETA(punto) || motivoSinETA(punto)]) {
                    const parrafo = document.createElement('p'); parrafo.textContent = texto; contenido.append(parrafo);
                }
                const eta = textoETA(punto);
                if (eta) {
                    const distancia = document.createElement('p');
                    distancia.textContent = `Distancia restante estimada: ${punto.distancia_restante_m >= 1000
                        ? (punto.distancia_restante_m / 1000).toLocaleString('es-CL', { maximumFractionDigits: 1 }) + ' km'
                        : Math.round(punto.distancia_restante_m) + ' m'}. Estimación con velocidad GPS reciente; puede variar.`;
                    contenido.append(distancia);
                }
                const tooltip = document.createElement('span');
                tooltip.textContent = `Bus ${punto.patente} · ${eta || 'Ubicación GPS real'}`;
                let marcador = marcadoresGPS.get(punto.viaje_id);
                if (!marcador) {
                    marcador = L.marker([punto.latitud,punto.longitud], {title: 'Bus: ubicación GPS real',icon:L.divIcon({
                        className:'bus-gps-real',iconSize:[36,36],iconAnchor:[18,18],
                        html:'<span role="img" aria-label="Bus: ubicación GPS real" style="display:flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:50%;background:#059669;border:3px solid white;box-shadow:0 2px 8px #0008;font-size:20px">🚌</span>'
                    })}).addTo(map);
                    marcador.bindPopup(contenido,{autoPan:false});
                    marcador.bindTooltip(tooltip, { direction: 'top', offset: [0, -18] });
                    marcador.on('click',() => opciones.onSeleccion && opciones.onSeleccion(punto.horario_id));
                    marcadoresGPS.set(punto.viaje_id,marcador);
                } else { marcador.setLatLng([punto.latitud,punto.longitud]); marcador.setPopupContent(contenido); marcador.setTooltipContent(tooltip); }
            }
            marcadoresGPS.forEach((marcador,id) => { if (!activos.has(id)) { map.removeLayer(marcador); marcadoresGPS.delete(id); } });
        }

        async function cargarGPS() {
            if (solicitandoGPS || document.visibilityState!=='visible') return;
            solicitandoGPS=true;
            try {
                const response=await fetch('/api/mapa/posiciones',{cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(15000)});
                const data=await response.json();
                if (!response.ok || !data.success || !Array.isArray(data.posiciones)) throw new Error('gps');
                posicionesGPS=data.posiciones;
                if (Number.isFinite(Date.parse(data.servidor_en))) desfaseServidor=Date.parse(data.servidor_en)-Date.now();
            } catch (_) { /* Conservar el estado de viaje; ocultar los puntos cuando caducan. */ }
            finally {
                try { moverBuses(); emitirGPS(); }
                finally { solicitandoGPS=false; }
                if (encuadrePendiente && seleccionado != null) encuadrePendiente = !encuadrar();
            }
        }

        function destacar() {
            capas.forEach((capa, id) => {
                if (recorridoVisible(id)) {
                    capa.linea.setStyle({ weight: id === seleccionado ? 6 : 3, opacity: 0.95 });
                    if (!map.hasLayer(capa.linea)) capa.linea.addTo(map);
                    if (id === seleccionado) capa.linea.bringToFront();
                } else {
                    map.removeLayer(capa.linea);
                    quitarMarcador(capa);
                }
            });
        }

        function encuadrar() {
            if (zoomEnCurso) return false;
            const puntos = Array.from(capas.values()).filter(capa => recorridoVisible(capa.horario.id))
                .flatMap(capa => capa.dato.camino.puntos);
            posicionesGPS.filter(p => recorridoVisible(p.horario_id) && horarios.some(h => h.id === p.horario_id) && gpsReciente(p))
                .forEach(p => puntos.push([p.latitud, p.longitud]));
            if (!puntos.length) return false;
            map.fitBounds(L.latLngBounds(puntos), { padding: [40, 40], maxZoom: 12, animate: false });
            return true;
        }

        function incorporar(horario, indice, dato) {
            const existente = capas.get(horario.id);
            const color = PALETAS[tab][indice % PALETAS[tab].length];
            const capa = existente || { horario, color, marcador: null };
            capa.dato = dato;
            if (existente) capa.linea.setLatLngs(dato.camino.puntos);
            else {
                capa.linea = L.polyline(dato.camino.puntos, { color, weight: 3, opacity: 0.95 });
                capa.linea.on('click', () => opciones.onSeleccion && opciones.onSeleccion(horario.id));
                capas.set(horario.id, capa);
            }
            capa.linea.bindPopup(popup(capa, false), { autoPan: false });
            if (capa.marcador) capa.marcador.setPopupContent(popup(capa, true));
            desfaseServidor = dato.desfase;
            destacar();
            moverBuses();
            if (encuadrePendiente && seleccionado === horario.id) encuadrePendiente = !encuadrar();
        }

        async function cargar(refresco) {
            if (cargando || document.visibilityState !== 'visible' || !horarios.length) return;
            const version = generacion;
            cargando = true;
            actualizarEstado();
            await Promise.all(horarios.map(async (horario, indice) => {
                try {
                    const previo = cache.get(horario.id);
                    const dato = !refresco && previo && Date.now() - previo.recibido < 60000
                        ? previo : await solicitar(horario.id, version);
                    if (version !== generacion || !dato) return;
                    cache.set(horario.id, dato); errores.delete(horario.id);
                    incorporar(horario, indice, dato);
                    const tipo = clasificacion(dato.datos);
                    informarRuta(horario.id, {
                        disponible: true, tipo_recorrido: dato.datos.tipo_recorrido,
                        nota_itinerario: dato.datos.nota_itinerario || '',
                        texto: `${tipo ? tipo + ' · ' : ''}Trazado por carretera aproximado`
                    });
                } catch (error) {
                    if (version !== generacion) return;
                    const mensaje = error.mensajeUsuario || error.message === 'El trazado tardó demasiado'
                        ? error.message : 'Trazado por carretera temporalmente no disponible';
                    errores.set(horario.id, mensaje);
                    informarRuta(horario.id, { disponible: !!capas.get(horario.id), texto: capas.has(horario.id) ? 'Último trazado disponible' : mensaje });
                    actualizarEstado();
                }
            }));
            if (version !== generacion) return;
            cargando = false;
            moverBuses();
            if (encuadrePendiente) encuadrePendiente = !encuadrar();
        }

        function mostrar(datos, ajustes = {}) {
            limpiar();
            horarios = datos.filter(horario => Number.isInteger(horario.id) && horario.id > 0);
            tab = ajustes.tab === 'llegadas' ? 'llegadas' : 'salidas';
            seleccionado = horarios.some(horario => horario.id === ajustes.seleccionado) ? ajustes.seleccionado : null;
            encuadrePendiente = true;
            actualizarEstado();
            cargar(false);
            cargarGPS();
        }

        function seleccionar(id) {
            if (id != null && !horarios.some(horario => horario.id === id)) return;
            seleccionado = id == null ? null : id;
            destacar(); moverBuses();
            encuadrePendiente = !encuadrar();
        }

        global.setInterval(moverBuses, 15000);
        global.setInterval(cargarGPS, 15000);
        global.setInterval(() => cargar(true), 60000);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') { moverBuses(); cargar(true); cargarGPS(); }
        });
        global.addEventListener('online', () => { cargar(true); cargarGPS(); });
        cargarGPS();
        return { mostrar, seleccionar, limpiar, refrescar: () => cargar(true) };
    }

    global.BSRedMapaRutas = { crear };
})(window);
