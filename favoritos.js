(() => {
    'use strict';

    const ANTICIPACIONES = [5, 10, 15];
    const PENDIENTE = 'bsred-favorito-pendiente';

    function elemento(etiqueta, clases = '', texto = '') {
        const nodo = document.createElement(etiqueta);
        nodo.className = clases;
        nodo.textContent = texto;
        return nodo;
    }

    function bytesPublicos(clave) {
        const base64 = String(clave).replace(/-/g, '+').replace(/_/g, '/');
        const decodificado = atob(base64 + '='.repeat((4 - base64.length % 4) % 4));
        return Uint8Array.from(decodificado, caracter => caracter.charCodeAt(0));
    }

    async function conLimite(promesa, milisegundos = 12000) {
        let limite;
        try {
            return await Promise.race([promesa, new Promise((_, rechazar) => {
                limite = window.setTimeout(() => rechazar(new Error('Tiempo de espera agotado')), milisegundos);
            })]);
        } finally { window.clearTimeout(limite); }
    }

    function esCompatible() {
        return window.isSecureContext && 'serviceWorker' in navigator
            && 'PushManager' in window && 'Notification' in window;
    }

    function crear(opciones = {}) {
        let usuario = null;
        let versionSesion = 0;
        let versionLectura = 0;
        let favoritos = new Map();
        let cargados = false;
        let cargando = false;
        let config = null;
        let suscripcion = null;
        let dispositivoActivo = false;
        let dispositivoEnCurso = false;
        let versionDispositivo = 0;
        const enCurso = new Set();
        const solicitudes = new Set();
        let mensajeDispositivo = 'Comprobando avisos en este dispositivo...';

        function informar(mensaje, error = false) {
            if (!opciones.estado) return;
            opciones.estado.textContent = mensaje;
            opciones.estado.className = error ? 'text-sm text-amber-400' : 'text-sm text-slate-400';
        }

        async function solicitar(url, metodo = 'GET', cuerpo) {
            if (metodo !== 'GET' && cuerpo === undefined) cuerpo = {};
            const controlador = new AbortController();
            solicitudes.add(controlador);
            const limite = window.setTimeout(() => controlador.abort(), 12000);
            try {
                const respuesta = await fetch(url, {
                    method: metodo, credentials: 'same-origin', cache: 'no-store',
                    headers: cuerpo === undefined ? {} : { 'Content-Type': 'application/json' },
                    body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
                    signal: controlador.signal
                });
                const datos = respuesta.status === 204 ? null : await respuesta.json();
                if (!respuesta.ok || (datos && datos.success === false)) {
                    const error = new Error(datos && (datos.message || datos.error) || 'No se pudo guardar el cambio.');
                    error.status = respuesta.status;
                    throw error;
                }
                return datos;
            } finally {
                window.clearTimeout(limite);
                solicitudes.delete(controlador);
            }
        }

        function manejarError(error, mensaje) {
            if (error.status === 401) {
                actualizarSesion(null);
                informar('Tu sesión expiró. Inicia sesión nuevamente.', true);
                if (opciones.onSesionVencida) opciones.onSesionVencida();
            } else informar(mensaje, true);
        }

        function notificarCambio() {
            renderizarFavoritos();
            if (typeof opciones.onCambio === 'function') opciones.onCambio();
        }

        function focoActual() {
            const actual = document.activeElement;
            if (!actual) return null;
            return actual.dataset.favoritoId ? { favorito: actual.dataset.favoritoId }
                : actual.id ? { id: actual.id } : null;
        }

        function restaurarFoco(foco) {
            if (!foco || (document.activeElement && document.activeElement !== document.body)) return;
            const nodo = foco.id ? document.getElementById(foco.id)
                : document.querySelector(`[data-favorito-id="${Number(foco.favorito)}"]`);
            if (nodo && !nodo.disabled) nodo.focus({ preventScroll: true });
        }

        function crearBoton(horario) {
            const id = Number(horario.id);
            const guardado = favoritos.has(id);
            const boton = elemento('button', 'boton-favorito ml-3', guardado ? '★' : '☆');
            boton.type = 'button';
            boton.dataset.favoritoId = String(id);
            boton.setAttribute('aria-pressed', String(guardado));
            boton.setAttribute('aria-label', `${guardado ? 'Quitar de' : 'Añadir a'} favoritos: ${horario.origen || ''} a ${horario.destino || ''}`);
            boton.title = guardado ? 'Quitar de favoritos' : 'Guardar recorrido favorito';
            boton.disabled = enCurso.has(id) || Boolean(usuario && !cargados);
            boton.onclick = () => alternar(horario);
            return boton;
        }

        function guardarPendiente(id) {
            try { sessionStorage.setItem(PENDIENTE, String(id)); } catch (_) { /* El almacenamiento puede no estar disponible. */ }
        }

        async function guardar(id, minutos, horario) {
            if (!usuario || enCurso.has(id)) return;
            const version = versionSesion;
            const foco = focoActual();
            enCurso.add(id);
            notificarCambio();
            try {
                await solicitar(`/api/favoritos/${id}`, 'PUT', { minutos_antes: minutos });
                if (version !== versionSesion) return;
                const previo = favoritos.get(id);
                favoritos.set(id, { ...(previo || {}), horario_id: id, minutos_antes: minutos,
                    horario: horario || previo && previo.horario || {},
                    avisos_disponibles: previo ? previo.avisos_disponibles : undefined });
                informar('Recorrido guardado en tus favoritos. Activa los avisos en tu perfil para recibirlos en este dispositivo.');
                await refrescar();
            } catch (error) {
                if (version === versionSesion) manejarError(error, 'No se pudo guardar el favorito. Revisa la conexión e inténtalo nuevamente.');
            } finally {
                if (version === versionSesion) {
                    enCurso.delete(id);
                    notificarCambio();
                    restaurarFoco(foco);
                }
            }
        }

        async function quitar(id) {
            if (!usuario || enCurso.has(id)) return;
            const version = versionSesion;
            const foco = focoActual();
            enCurso.add(id);
            notificarCambio();
            try {
                await solicitar(`/api/favoritos/${id}`, 'DELETE');
                if (version !== versionSesion) return;
                favoritos.delete(id);
                informar('Recorrido eliminado de tus favoritos.');
            } catch (error) {
                if (version === versionSesion) manejarError(error, 'No se pudo eliminar el favorito. Revisa la conexión e inténtalo nuevamente.');
            } finally {
                if (version === versionSesion) {
                    enCurso.delete(id);
                    notificarCambio();
                    restaurarFoco(foco);
                }
            }
        }

        async function alternar(horario) {
            const id = Number(horario.id);
            if (!Number.isInteger(id) || id <= 0) return;
            if (!usuario) {
                guardarPendiente(id);
                informar('Inicia sesión para guardar este recorrido.');
                if (opciones.solicitarLogin) opciones.solicitarLogin();
                return;
            }
            if (!cargados) {
                await refrescar();
                if (!cargados) return;
            }
            if (favoritos.has(id)) await quitar(id);
            else await guardar(id, 10, horario);
        }

        async function refrescar() {
            if (!usuario) return;
            const version = versionSesion;
            const lectura = ++versionLectura;
            cargando = true;
            renderizarFavoritos();
            try {
                const datos = await solicitar('/api/favoritos');
                if (version !== versionSesion || lectura !== versionLectura) return;
                if (!datos || !Array.isArray(datos.favoritos)) throw new Error('Respuesta incompleta');
                favoritos = new Map(datos.favoritos.filter(item => Number.isInteger(Number(item.horario_id)))
                    .map(item => [Number(item.horario_id), item]));
                cargados = true;
                informar(favoritos.size ? `${favoritos.size} recorrido${favoritos.size === 1 ? '' : 's'} guardado${favoritos.size === 1 ? '' : 's'}.`
                    : 'Marca la estrella de un recorrido en el mapa para guardarlo.');
            } catch (error) {
                if (version === versionSesion && lectura === versionLectura) manejarError(error,
                    'No se pudieron actualizar tus favoritos. Revisa la conexión e inténtalo nuevamente.');
            } finally {
                if (version === versionSesion && lectura === versionLectura) {
                    cargando = false;
                    notificarCambio();
                }
            }
        }

        function renderizarFavoritos() {
            if (!opciones.lista) return;
            opciones.lista.replaceChildren();
            if (!usuario) return;
            if (!favoritos.size) {
                const estado = elemento('div', 'bg-slate-900 border border-slate-800 rounded-xl p-5 text-sm text-slate-400',
                    cargando ? 'Cargando favoritos...' : cargados ? 'Aún no has guardado recorridos.' : 'Tus favoritos no están disponibles.');
                if (!cargados && !cargando) {
                    const reintentar = elemento('button', 'block mt-3 text-blue-400 underline', 'Reintentar');
                    reintentar.type = 'button';
                    reintentar.onclick = refrescar;
                    estado.append(reintentar);
                }
                opciones.lista.append(estado);
                return;
            }
            favoritos.forEach((favorito, id) => {
                const horario = favorito.horario || {};
                const tarjeta = elemento('article', 'bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3');
                tarjeta.append(elemento('h3', 'font-bold text-white', `${horario.origen || 'Origen'} → ${horario.destino || 'Destino'}`),
                    elemento('p', 'text-sm text-slate-300', `${horario.empresa || 'Servicio de buses'} · Salida ${horario.salida || 'sin horario'} · ${horario.dias || 'Días no informados'}`),
                    elemento('p', 'text-xs text-slate-400', `Andén ${horario.anden || 'sin asignar'}`));
                const controles = elemento('div', 'flex flex-wrap gap-3 items-center');
                if (favorito.avisos_disponibles === true) {
                    const etiqueta = elemento('label', 'text-sm text-slate-300', 'Avisar ');
                    etiqueta.htmlFor = `anticipacion-${id}`;
                    const selector = elemento('select', 'ml-2 bg-slate-800 border border-slate-600 rounded-lg p-2 text-white');
                    selector.id = `anticipacion-${id}`;
                    selector.setAttribute('aria-label', `Anticipación del aviso para ${horario.destino || 'este recorrido'}`);
                    ANTICIPACIONES.forEach(minutos => {
                        const opcion = elemento('option', '', `${minutos} minutos antes`);
                        opcion.value = String(minutos);
                        selector.append(opcion);
                    });
                    selector.value = String(favorito.minutos_antes || 10);
                    selector.disabled = enCurso.has(id);
                    selector.onchange = () => guardar(id, Number(selector.value), horario);
                    etiqueta.append(selector);
                    controles.append(etiqueta);
                } else tarjeta.append(elemento('p', 'text-xs text-amber-400', favorito.motivo_aviso
                    || 'Este recorrido se guarda como favorito. Los avisos se envían para salidas desde Panguipulli con horario disponible.'));
                const enlace = elemento('a', 'text-sm text-blue-400 underline', 'Ver en el mapa');
                const esLlegada = String(horario.origen || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim().toLowerCase() !== 'panguipulli';
                enlace.href = `/?recorrido=${id}${esLlegada ? '&tab=llegadas' : ''}`;
                controles.append(enlace);
                const eliminar = elemento('button', 'ml-auto px-3 py-2 rounded-lg border border-rose-500/40 text-rose-300 text-sm', 'Quitar favorito');
                eliminar.type = 'button';
                eliminar.disabled = enCurso.has(id);
                eliminar.onclick = () => quitar(id);
                controles.append(eliminar);
                tarjeta.append(controles);
                opciones.lista.append(tarjeta);
            });
        }

        function renderizarDispositivo() {
            if (!opciones.notificaciones) return;
            const contenedor = opciones.notificaciones;
            contenedor.replaceChildren();
            const estado = elemento('p', 'text-sm text-slate-300', mensajeDispositivo);
            estado.setAttribute('role', 'status');
            estado.setAttribute('aria-live', 'polite');
            contenedor.append(estado);
            if (!usuario || !esCompatible()) return;
            const boton = elemento('button', 'mt-4 px-4 py-3 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold',
                dispositivoActivo ? 'Desactivar avisos en este dispositivo' : 'Activar avisos en este dispositivo');
            boton.type = 'button';
            boton.disabled = dispositivoEnCurso || (!dispositivoActivo
                && (!config || !config.disponible || Notification.permission === 'denied'));
            boton.onclick = dispositivoActivo ? desactivar : activar;
            contenedor.append(boton);
            if (!config && !dispositivoEnCurso) {
                const reintentar = elemento('button', 'ml-3 mt-3 text-sm text-blue-400 underline', 'Reintentar conexión');
                reintentar.type = 'button';
                reintentar.onclick = refrescarDispositivo;
                contenedor.append(reintentar);
            }
            if (dispositivoActivo) {
                const favoritosLink = elemento('a', 'block mt-3 text-sm text-blue-400 underline', 'Elegir recorridos y anticipación');
                favoritosLink.href = '#favoritos';
                favoritosLink.onclick = event => {
                    if (typeof window.cambiarSeccion === 'function') {
                        event.preventDefault();
                        window.cambiarSeccion('favoritos');
                    }
                };
                contenedor.append(favoritosLink);
            }
        }

        async function registroWorker() {
            let registro = await conLimite(navigator.serviceWorker.getRegistration('/'));
            if (!registro) registro = await conLimite(navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' }));
            if (registro.active) return registro;
            let limite;
            try {
                return await Promise.race([navigator.serviceWorker.ready, new Promise((_, rechazar) => {
                    limite = window.setTimeout(() => rechazar(new Error('No se pudo preparar las notificaciones.')), 12000);
                })]);
            } finally { window.clearTimeout(limite); }
        }

        async function refrescarDispositivo() {
            if (!opciones.notificaciones || !usuario || dispositivoEnCurso) return;
            const version = versionSesion;
            const lectura = ++versionDispositivo;
            if (!esCompatible()) {
                const esIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
                    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
                mensajeDispositivo = esIOS
                    ? 'En iPhone o iPad, abre BSRed en Safari, añade la página a la pantalla de inicio y ábrela desde ese icono para activar los avisos.'
                    : 'Este navegador no permite avisos con la página cerrada. Prueba un navegador actualizado con notificaciones web.';
                renderizarDispositivo();
                return;
            }
            dispositivoEnCurso = true;
            mensajeDispositivo = 'Comprobando avisos en este dispositivo...';
            renderizarDispositivo();
            try {
                const configuracion = await solicitar('/api/notificaciones/config');
                if (version !== versionSesion || lectura !== versionDispositivo) return;
                config = configuracion;
                if (!config || (config.disponible && typeof config.public_key !== 'string')) throw new Error('Configuración incompleta');
                const registro = await registroWorker();
                const actual = await conLimite(registro.pushManager.getSubscription());
                if (version !== versionSesion || lectura !== versionDispositivo) return;
                suscripcion = actual;
                dispositivoActivo = false;
                if (actual) {
                    const estado = await solicitar('/api/notificaciones/suscripciones/estado', 'POST', { endpoint: actual.endpoint });
                    if (version !== versionSesion || lectura !== versionDispositivo) return;
                    dispositivoActivo = estado && estado.activa === true;
                }
                mensajeDispositivo = !config.disponible
                    ? 'Los avisos todavía no están disponibles. Puedes guardar tus recorridos y activarlos cuando el servicio esté listo.'
                    : dispositivoActivo && Notification.permission === 'denied'
                        ? 'Este dispositivo está vinculado a tu cuenta, pero el navegador bloquea los avisos. Permite las notificaciones en los ajustes de este sitio.'
                    : dispositivoActivo
                        ? 'Avisos activados para tu cuenta en este dispositivo.'
                    : Notification.permission === 'denied'
                        ? 'Las notificaciones están bloqueadas. Permítelas en los ajustes de este sitio en tu navegador y vuelve a intentarlo.'
                        : 'Los avisos están desactivados en este dispositivo. Actívalos para recibir las salidas de tus favoritos.';
            } catch (error) {
                if (version === versionSesion && lectura === versionDispositivo) {
                    config = null;
                    mensajeDispositivo = 'No se pudo comprobar el servicio de avisos. Revisa la conexión e inténtalo nuevamente.';
                    if (error.status === 401) manejarError(error, '');
                }
            } finally {
                if (version === versionSesion && lectura === versionDispositivo) {
                    dispositivoEnCurso = false;
                    renderizarDispositivo();
                }
            }
        }

        async function activar() {
            if (!usuario || dispositivoEnCurso || !config || !config.disponible || !esCompatible()) return;
            const version = versionSesion;
            // El permiso nace del gesto del usuario, antes de cualquier espera de red.
            const permiso = Notification.permission === 'default'
                ? Notification.requestPermission() : Promise.resolve(Notification.permission);
            dispositivoEnCurso = true;
            ++versionDispositivo;
            mensajeDispositivo = 'Activando avisos...';
            renderizarDispositivo();
            let creada = false;
            let suscripcionOperacion = null;
            try {
                if (await permiso !== 'granted') {
                    mensajeDispositivo = 'No se activaron los avisos. Puedes permitir las notificaciones en los ajustes de este sitio.';
                    return;
                }
                const registro = await registroWorker();
                if (version !== versionSesion) return;
                suscripcionOperacion = await conLimite(registro.pushManager.getSubscription());
                if (version !== versionSesion) return;
                // Una suscripción que el servidor ya no reconoce puede haber
                // caducado en el proveedor. Renovarla requiere este click explícito.
                if (suscripcionOperacion && !dispositivoActivo) {
                    let eliminado = false;
                    try { eliminado = await conLimite(suscripcionOperacion.unsubscribe()) === true; }
                    catch (_) { /* El permiso se puede restablecer desde los ajustes. */ }
                    if (version !== versionSesion) return;
                    if (!eliminado) {
                        mensajeDispositivo = 'No se pudo renovar el vínculo de este dispositivo. Desactiva las notificaciones de BSRed en los ajustes del navegador y vuelve a permitirlas antes de activar los avisos.';
                        return;
                    }
                    suscripcion = null;
                    suscripcionOperacion = null;
                }
                if (!suscripcionOperacion) {
                    const nueva = registro.pushManager.subscribe({ userVisibleOnly: true,
                        applicationServerKey: bytesPublicos(config.public_key) });
                    try { suscripcionOperacion = await conLimite(nueva, 20000); }
                    catch (error) {
                        nueva.then(tardia => tardia.unsubscribe()).catch(() => {});
                        throw error;
                    }
                    creada = true;
                }
                if (version !== versionSesion) return;
                suscripcion = suscripcionOperacion;
                await solicitar('/api/notificaciones/suscripciones', 'POST', { subscription: suscripcionOperacion.toJSON() });
                if (version !== versionSesion) return;
                dispositivoActivo = true;
                mensajeDispositivo = 'Avisos activados para tu cuenta en este dispositivo. Recibirás los avisos de tus favoritos con salida desde Panguipulli.';
            } catch (error) {
                if (error.status === 409 && suscripcionOperacion && version === versionSesion) {
                    let eliminado = false;
                    try { eliminado = await conLimite(suscripcionOperacion.unsubscribe()) === true; }
                    catch (_) { /* Los ajustes del navegador permiten restablecer el vínculo. */ }
                    if (version === versionSesion) {
                        suscripcion = null;
                        dispositivoActivo = false;
                        mensajeDispositivo = eliminado
                            ? 'El dispositivo está listo para renovar su vínculo. Pulsa Activar avisos en este dispositivo otra vez.'
                            : 'No se pudo renovar el vínculo de este dispositivo. Desactiva las notificaciones de BSRed en los ajustes del navegador y vuelve a permitirlas antes de activar los avisos.';
                    }
                    return;
                }
                if (creada && suscripcionOperacion && version === versionSesion) {
                    await conLimite(suscripcionOperacion.unsubscribe()).catch(() => {});
                    suscripcion = null;
                }
                if (version === versionSesion) {
                    mensajeDispositivo = 'No se pudieron activar los avisos. Revisa la conexión y el permiso del navegador e inténtalo nuevamente.';
                    if (error.status === 401) manejarError(error, '');
                }
            } finally {
                if (version === versionSesion) {
                    dispositivoEnCurso = false;
                    renderizarDispositivo();
                }
            }
        }

        async function desactivar() {
            if (!usuario || dispositivoEnCurso || !suscripcion) return;
            const version = versionSesion;
            dispositivoEnCurso = true;
            ++versionDispositivo;
            mensajeDispositivo = 'Desactivando avisos...';
            renderizarDispositivo();
            try {
                await solicitar('/api/notificaciones/suscripciones', 'DELETE', { endpoint: suscripcion.endpoint });
                if (version !== versionSesion) return;
                dispositivoActivo = false;
                await conLimite(suscripcion.unsubscribe()).catch(() => {});
                suscripcion = null;
                mensajeDispositivo = 'Avisos desactivados en este dispositivo. Tus favoritos siguen guardados.';
            } catch (error) {
                if (version === versionSesion) {
                    mensajeDispositivo = 'No se pudieron desactivar los avisos. Revisa la conexión e inténtalo nuevamente.';
                    if (error.status === 401) manejarError(error, '');
                }
            } finally {
                if (version === versionSesion) {
                    dispositivoEnCurso = false;
                    renderizarDispositivo();
                }
            }
        }

        async function desvincularNavegador() {
            // El servidor revoca este dispositivo al cerrar sesión. Borrar también
            // la suscripción local evita reutilizarla con otra cuenta en el equipo.
            if (!esCompatible()) return;
            try {
                let limite;
                const desvincular = (async () => {
                    const registro = await navigator.serviceWorker.getRegistration('/');
                    if (!registro) return;
                    const actual = await registro.pushManager.getSubscription();
                    if (actual) await actual.unsubscribe();
                })();
                try {
                    await Promise.race([desvincular, new Promise(resolve => { limite = window.setTimeout(resolve, 3000); })]);
                } finally { window.clearTimeout(limite); }
            } catch (_) { /* El logout del servidor sigue siendo la autoridad. */ }
        }

        function actualizarSesion(nueva) {
            const anterior = usuario && String(usuario.id || usuario.email);
            const siguiente = nueva && String(nueva.id || nueva.email);
            if (anterior === siguiente) return;
            usuario = nueva || null;
            ++versionSesion;
            ++versionLectura;
            ++versionDispositivo;
            solicitudes.forEach(controlador => controlador.abort());
            favoritos = new Map();
            cargados = false;
            cargando = false;
            enCurso.clear();
            config = null;
            suscripcion = null;
            dispositivoActivo = false;
            dispositivoEnCurso = false;
            mensajeDispositivo = 'Comprobando avisos en este dispositivo...';
            notificarCambio();
            renderizarDispositivo();
            if (!usuario) {
                informar('Marca ★ para guardar un recorrido. Necesitas iniciar sesión.');
                return;
            }
            const version = versionSesion;
            refrescar().then(async () => {
                if (!cargados || version !== versionSesion) return;
                let pendiente;
                try {
                    pendiente = Number(sessionStorage.getItem(PENDIENTE));
                    sessionStorage.removeItem(PENDIENTE);
                } catch (_) { return; }
                if (Number.isInteger(pendiente) && pendiente > 0 && !favoritos.has(pendiente)) {
                    await guardar(pendiente, 10);
                }
            });
            refrescarDispositivo();
        }

        window.addEventListener('online', () => {
            if (!usuario) return;
            refrescar();
            refrescarDispositivo();
        });
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && usuario) {
                refrescar();
                refrescarDispositivo();
            }
        });
        const controlador = { actualizarSesion, crearBoton, refrescar, refrescarDispositivo, desvincularNavegador };
        informar('Marca ★ para guardar un recorrido.');
        actualizarSesion(opciones.usuario || null);
        return controlador;
    }

    window.BSRedFavoritos = Object.freeze({ crear });
})();
