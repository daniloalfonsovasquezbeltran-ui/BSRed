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
        const controladores = new Set(), cola = [];
        if (map.attributionControl) {
            map.attributionControl.addAttribution('Recorridos © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>');
        }

        function clasificacion(datos) {
            if (datos.tipo_recorrido === 'destino_unico') return 'Destino único';
            if (['paradas_confirmadas', 'paradas_sucesivas', 'con_paradas'].includes(datos.tipo_recorrido)) return 'Paradas sucesivas confirmadas';
            return '';
        }

        function quitarMarcador(capa) {
            if (capa.marcador) map.removeLayer(capa.marcador);
            capa.marcador = null;
        }

        function limpiar() {
            generacion++;
            controladores.forEach(controlador => controlador.abort());
            capas.forEach(capa => {
                map.removeLayer(capa.linea);
                quitarMarcador(capa);
            });
            capas.clear(); errores.clear(); horarios = []; seleccionado = null; cargando = false; encuadrePendiente = false;
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
            return contenedor;
        }

        function actualizarDetalle() {
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
            const buses = Array.from(capas.values()).filter(capa => capa.marcador).length;
            let tipo = 'listo', texto;
            if (!horarios.length) { tipo = 'vacio'; texto = 'No hay horarios para estos filtros'; }
            else if (cargando) { tipo = 'cargando'; texto = `Trazando recorridos por carretera (${capas.size}/${horarios.length})…`; }
            else if (!capas.size) { tipo = 'error'; texto = 'No se pudieron trazar los recorridos; los horarios siguen disponibles'; }
            else {
                tipo = errores.size ? 'parcial' : 'listo';
                texto = `${capas.size} recorridos por carretera · ${buses} buses con ubicación estimada`;
                const sinTrazado = Array.from(errores.keys()).filter(id => !capas.has(id)).length;
                if (sinTrazado) texto += ` · ${sinTrazado} sin trazado disponible`;
                if (errores.size > sinTrazado) texto += ` · ${errores.size - sinTrazado} trazados sin actualizar`;
            }
            informar({ tipo, texto, trazadas: capas.size, total: horarios.length, busesEstimados: buses });
            actualizarDetalle();
        }

        function moverBuses() {
            if (document.visibilityState !== 'visible') return;
            capas.forEach(capa => {
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
            actualizarEstado();
        }

        function destacar() {
            capas.forEach((capa, id) => {
                capa.linea.setStyle({ weight: id === seleccionado ? 6 : 3, opacity: seleccionado == null || id === seleccionado ? 0.95 : 0.45 });
                if (id === seleccionado) capa.linea.bringToFront();
            });
        }

        function encuadrar() {
            const elegida = capas.get(seleccionado);
            const puntos = elegida ? elegida.dato.camino.puntos : Array.from(capas.values()).flatMap(capa => capa.dato.camino.puntos);
            if (puntos.length) map.fitBounds(L.latLngBounds(puntos), { padding: [40, 40], maxZoom: 12 });
        }

        function incorporar(horario, indice, dato) {
            const existente = capas.get(horario.id);
            const color = PALETAS[tab][indice % PALETAS[tab].length];
            const capa = existente || { horario, color, marcador: null };
            capa.dato = dato;
            if (existente) capa.linea.setLatLngs(dato.camino.puntos);
            else {
                capa.linea = L.polyline(dato.camino.puntos, { color, weight: 3, opacity: 0.95 }).addTo(map);
                capa.linea.on('click', () => opciones.onSeleccion && opciones.onSeleccion(horario.id));
                capas.set(horario.id, capa);
            }
            capa.linea.bindPopup(popup(capa, false), { autoPan: false });
            if (capa.marcador) capa.marcador.setPopupContent(popup(capa, true));
            desfaseServidor = dato.desfase;
            destacar();
            moverBuses();
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
            if (encuadrePendiente) { encuadrar(); encuadrePendiente = false; }
        }

        function mostrar(datos, ajustes = {}) {
            limpiar();
            horarios = datos.filter(horario => Number.isInteger(horario.id) && horario.id > 0);
            tab = ajustes.tab === 'llegadas' ? 'llegadas' : 'salidas';
            seleccionado = ajustes.seleccionado == null ? null : ajustes.seleccionado;
            encuadrePendiente = true;
            actualizarEstado();
            cargar(false);
        }

        function seleccionar(id) {
            seleccionado = id;
            destacar(); actualizarDetalle(); encuadrar();
        }

        global.setInterval(moverBuses, 15000);
        global.setInterval(() => cargar(true), 60000);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') { moverBuses(); cargar(true); }
        });
        global.addEventListener('online', () => cargar(true));
        return { mostrar, seleccionar, limpiar, refrescar: () => cargar(true) };
    }

    global.BSRedMapaRutas = { crear };
})(window);
