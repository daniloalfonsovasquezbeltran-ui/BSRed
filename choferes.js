/* Cuentas por empresa y control de viajes. Ningún rol se autoriza desde el navegador. */
window.BSRedChoferes = (() => {
    let user = null, trip = null, token = null, watch = null, polling = null, sending = null;
    let nativeSequence = 0;
    const pendingNative = new Map();
    let stateVersion = 0, driverStatePending = null, stateKnown = false, starting = false, finishing = false;
    let routes = [], buses = [], company = null, finishPending = false;
    let nativeSnapshot = null, nativeDiagnosticPending = null, legacyBridge = null, legacyListener = null;
    const el = id => document.getElementById(id);
    const native = () => window.BSRedAndroid && typeof window.BSRedAndroid.postMessage === 'function';
    const status = (message, company = false) => {
        const node = el(company ? 'choferes-estado' : 'chofer-estado');
        if (node) node.textContent = message;
    };

    function accountRestriction() {
        if (user && user.requiere_cambio_password === true) return 'Cambia tu contraseña inicial en Mi cuenta antes de iniciar o reanudar un viaje.';
        if (!company || company.estado_validacion !== 'aprobada') {
            return company && company.estado_validacion === 'rechazada'
                ? 'La empresa no está aprobada para operar. Consulta con su administrador.'
                : 'La empresa necesita aprobación antes de iniciar o reanudar viajes.';
        }
        return '';
    }

    function startRestriction() {
        return accountRestriction() || (!routes.length ? 'No hay recorridos operativos disponibles para tu cuenta hoy.' : '')
            || (!buses.length ? 'Tu empresa debe registrar y habilitar un bus permitido para tu cuenta.' : '');
    }

    function renderControls() {
        const restriction = startRestriction();
        const explanation = el('chofer-restricciones');
        if (explanation) explanation.textContent = restriction;
        const start = el('chofer-iniciar');
        if (start) start.disabled = !!trip || starting || finishing || driverStatePending || !!restriction;
        const permissions = el('chofer-permisos');
        if (permissions) permissions.disabled = starting || finishing || finishPending || !!accountRestriction();
        const finish = el('chofer-finalizar');
        if (finish) finish.disabled = finishing;
    }

    async function api(path, method = 'GET', data, bearer) {
        const headers = {};
        if (data !== undefined) headers['Content-Type'] = 'application/json';
        if (bearer) headers.Authorization = 'Bearer ' + bearer;
        const response = await fetch(path, {method, headers, credentials: 'same-origin', cache: 'no-store',
            body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(65000)});
        const result = await response.json();
        if (!response.ok || !result.success) {
            const error = new Error(result.message || 'No se pudo completar la operación.');
            error.status = response.status; throw error;
        }
        return result;
    }

    function nativeCall(action, data = {}, wait = 12000) {
        if (typeof window.BSRedNativeCall === 'function') return window.BSRedNativeCall(action, data, wait);
        return new Promise((resolve, reject) => {
            const bridge = window.BSRedAndroid;
            if (!bridge || typeof bridge.postMessage !== 'function') { reject(new Error('El control nativo no está disponible.')); return; }
            const id = 'chofer-legacy-' + (++nativeSequence);
            const timeout = setTimeout(() => { pendingNative.delete(id); reject(new Error('La app no respondió. Vuelve a abrirla.')); }, wait);
            pendingNative.set(id, {resolve, reject, timeout});
            if (legacyBridge !== bridge || bridge.onmessage !== legacyListener) {
                const previous = bridge.onmessage;
                legacyListener = event => {
                    try {
                        const result = JSON.parse(event.data), request = pendingNative.get(result.request_id);
                        if (request) {
                            clearTimeout(request.timeout); pendingNative.delete(result.request_id);
                            if (result.success) request.resolve(result); else request.reject(new Error(result.message || 'No se pudo completar la operación de ubicación.'));
                            return;
                        }
                    } catch (_) { /* Una respuesta inválida no concede permisos. */ }
                    if (typeof previous === 'function') previous.call(bridge, event);
                };
                legacyBridge = bridge;
                bridge.onmessage = legacyListener;
            }
            try { bridge.postMessage(JSON.stringify({action, request_id: id, ...data})); }
            catch (_) { clearTimeout(timeout); pendingNative.delete(id); reject(new Error('No se pudo comunicar con la app.')); }
        });
    }

    function epochDate(value) {
        if (!Number.isFinite(value) || value <= 0) return '';
        const date = new Date(value);
        return Number.isFinite(date.getTime()) ? date.toLocaleString('es-CL', { timeZone: 'America/Santiago' }) : '';
    }

    function renderNativeDiagnostic(message = '') {
        const box = el('chofer-diagnostico');
        if (!box) return;
        box.replaceChildren();
        const append = text => {
            const paragraph = document.createElement('p'); paragraph.textContent = text; box.append(paragraph);
        };
        if (message) append(message);
        if (!nativeSnapshot) return;
        const snapshot = nativeSnapshot;
        const updated = epochDate(snapshot.actualizado_en);
        const age = Date.now() - snapshot.actualizado_en;
        const interrupted = !updated || age > 120000 || age < -30000;
        append(interrupted
            ? 'El seguimiento de la app no tiene una actualización reciente y puede estar interrumpido. Usa Reanudar GPS para comprobarlo.'
            : snapshot.activo === true ? 'Servicio de ubicación de la app activo.' : 'Servicio de ubicación de la app detenido.');
        const phases = {
            activo: 'Seguimiento activo.', enviando: 'Enviando ubicaciones.', esperando_gps: 'Esperando una ubicación del dispositivo.',
            sin_red: 'El envío espera conexión.', sin_permiso: 'Falta permiso de ubicación.', gps_apagado: 'La ubicación del dispositivo está desactivada.',
            error_envio: 'No se pudo confirmar el último envío.', detenido: 'Seguimiento detenido.', credencial_expirada: 'La autorización de seguimiento venció.'
        };
        if (phases[snapshot.estado]) append(phases[snapshot.estado]);
        if (typeof snapshot.mensaje === 'string' && snapshot.mensaje.trim()) append(snapshot.mensaje);
        if (updated) append('Diagnóstico actualizado: ' + updated + ' (Chile).');
        if (Number.isInteger(snapshot.viaje_id) && snapshot.viaje_id > 0) append('Viaje registrado en la app: #' + snapshot.viaje_id + '.');
        const permission = snapshot.permiso_ubicacion;
        const permissionText = permission === true || ['precisa', 'preciso', 'precise', 'fine', 'concedido'].includes(permission)
            ? 'preciso permitido' : ['aproximada', 'aproximado', 'approximate', 'coarse'].includes(permission)
                ? 'solo aproximado' : permission === false || ['denegado', 'denied', 'sin_permiso', 'none'].includes(permission)
                    ? 'no concedido' : 'sin verificar';
        append('Permiso de ubicación: ' + permissionText + '.');
        append('GPS del dispositivo: ' + (snapshot.gps_habilitado === true ? 'activado' : snapshot.gps_habilitado === false ? 'desactivado' : 'sin verificar') + '.');
        append('Conexión del dispositivo: ' + (snapshot.red_disponible === true ? 'disponible' : snapshot.red_disponible === false ? 'sin conexión' : 'sin verificar') + '.');
        if (snapshot.ahorro_bateria === true) append('El ahorro de batería está activado y puede interrumpir el seguimiento.');
        if (snapshot.bateria_sin_restricciones === false) append('Revisa las restricciones de batería de la app para mantener el seguimiento con la pantalla apagada.');
        if (snapshot.bateria_sin_restricciones === true) append('La app está configurada sin restricciones de batería.');
        for (const [field, label] of [['iniciado_en', 'Seguimiento iniciado'], ['vence_en', 'Autorización hasta'],
            ['ultimo_envio_en', 'Último envío registrado'], ['ultima_ubicacion_en', 'Última ubicación detectada']]) {
            const date = epochDate(snapshot[field]); if (date) append(label + ': ' + date + ' (Chile).');
        }
        const button = el('chofer-permisos');
        if (button) button.textContent = interrupted || snapshot.activo !== true ? 'Reanudar GPS / revisar permisos' : 'Comprobar permisos / reanudar GPS';
    }

    async function nativeDiagnostic() {
        if (!native() || nativeDiagnosticPending) return;
        const version = stateVersion;
        const request = { version };
        nativeDiagnosticPending = request;
        try {
            const response = await nativeCall('rastreoEstado');
            if (version !== stateVersion) return;
            const source = response.estado;
            if (!source || typeof source !== 'object') throw new Error('Diagnóstico no disponible.');
            nativeSnapshot = {};
            for (const field of ['activo', 'estado', 'mensaje', 'actualizado_en', 'viaje_id', 'iniciado_en', 'vence_en',
                'ultimo_envio_en', 'ultima_ubicacion_en', 'permiso_ubicacion', 'gps_habilitado', 'red_disponible',
                'ahorro_bateria', 'bateria_sin_restricciones']) nativeSnapshot[field] = source[field];
            renderNativeDiagnostic();
        } catch (_) {
            if (version === stateVersion) renderNativeDiagnostic('No se pudo consultar el diagnóstico de la app. Esta consulta no detiene un viaje ni el GPS existente.');
        } finally { if (nativeDiagnosticPending === request) nativeDiagnosticPending = null; }
    }

    async function locationPermission(requestPermission = false) {
        if (native()) {
            const result = await nativeCall(requestPermission ? 'solicitarPermisos' : 'permisos', {}, requestPermission ? 65000 : 12000);
            if (result.autorizado !== true) throw new Error('Permite la ubicación precisa en la app antes de iniciar el viaje.');
            if (result.gps_habilitado !== true) throw new Error('Activa la ubicación del dispositivo antes de iniciar el viaje.');
            return;
        }
        if (!navigator.geolocation) throw new Error('Este dispositivo no ofrece ubicación.');
        await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve,
            () => reject(new Error('Permite la ubicación y comprueba que el GPS esté activado.')),
            {enableHighAccuracy: true, timeout: 20000, maximumAge: 10000}));
    }

    function stopWebGPS() {
        if (watch !== null && navigator.geolocation) navigator.geolocation.clearWatch(watch);
        watch = null; token = null; sending = null;
    }

    async function activateGPS(credential) {
        if (!trip || typeof credential !== 'string' || !credential) throw new Error('No se recibió una autorización válida para el rastreo.');
        const tripId = trip.id, version = stateVersion;
        token = credential;
        if (native()) {
            await nativeCall('iniciar', {viaje_id: tripId, token});
            if (version !== stateVersion || !trip || trip.id !== tripId) return;
            status('Viaje iniciado. Esperando la primera ubicación de la app. El GPS puede continuar con la pantalla apagada.');
            nativeDiagnostic();
            return;
        }
        if (watch !== null) navigator.geolocation.clearWatch(watch);
        let lastSent = 0;
        watch = navigator.geolocation.watchPosition(async position => {
            if (version !== stateVersion || !trip || trip.id !== tripId || !token || finishPending || sending || Date.now()-lastSent<15000) return;
            const shipment = { tripId, version };
            sending = shipment;
            lastSent = Date.now();
            try {
                await api(`/api/chofer/viajes/${tripId}/ubicacion`, 'POST', {
                    latitud: position.coords.latitude, longitud: position.coords.longitude,
                    precision_m: position.coords.accuracy, ubicacion_en: new Date(position.timestamp).toISOString()
                }, token);
                if (version !== stateVersion || !trip || trip.id !== tripId) return;
                status('Viaje en curso. Última ubicación enviada: ' + new Date(position.timestamp).toLocaleTimeString('es-CL') + '.');
            } catch (error) {
                if (version !== stateVersion || !trip || trip.id !== tripId) return;
                if (error.status===401 || error.status===403) stopWebGPS();
                status(error.message + ' Revisa la conexión y el estado del viaje.');
            }
            finally { if (sending === shipment) sending = null; }
        }, () => { if (version === stateVersion && trip && trip.id === tripId && !finishPending) status('El viaje sigue activo, pero no se está recibiendo GPS. Revisa los permisos.'); },
        {enableHighAccuracy: true, maximumAge: 10000, timeout: 20000});
    }

    function renderTrip() {
        if (el('form-iniciar-viaje')) el('form-iniciar-viaje').hidden = !!trip;
        if (el('chofer-finalizar')) el('chofer-finalizar').hidden = !trip;
        if (el('chofer-viaje')) el('chofer-viaje').textContent = trip
            ? `Viaje #${trip.id} · Bus ${trip.patente}${finishPending ? ' · Finalización pendiente de confirmar' : ''}`
            : stateKnown ? 'Sin viaje activo.' : 'Esperando confirmar el estado del viaje.';
        renderControls();
    }

    function fillOptions(select, choices, label, empty) {
        if (!select) return;
        const previous = select.value;
        select.replaceChildren();
        if (choices.length !== 1) {
            const placeholder = document.createElement('option'); placeholder.value = '';
            placeholder.textContent = choices.length ? 'Selecciona una opción' : empty;
            placeholder.disabled = true; placeholder.selected = true; select.append(placeholder);
        }
        for (const choice of choices) {
            const option = document.createElement('option'); option.value = String(choice.id); option.textContent = label(choice); select.append(option);
        }
        if (choices.some(choice => String(choice.id) === previous)) select.value = previous;
        else select.value = choices.length === 1 ? String(choices[0].id) : '';
    }

    function renderOptions() {
        fillOptions(el('chofer-recorrido'), routes, route => `${route.origen} → ${route.destino} · ${route.salida} · ${route.dias || ''}`, 'Sin recorridos operativos disponibles');
        fillOptions(el('chofer-bus'), buses, bus => `${bus.patente}${Number.isInteger(bus.capacidad) ? ' · ' + bus.capacidad + ' plazas' : ''}${bus.accesible === true ? ' · Accesible' : ''}`, 'Sin buses registrados permitidos');
        if (!el('chofer-bus') && el('chofer-patente')) {
            // Compatibilidad con una página antigua: una patente siempre debe
            // corresponder a un ID que el servidor autorizó en este estado.
            const input = el('chofer-patente');
            if (buses.length === 1 && !input.value) input.value = buses[0].patente;
        }
    }

    async function driverState() {
        nativeDiagnostic();
        if (driverStatePending || !user || user.rol !== 'chofer') return;
        const version = stateVersion;
        const request = { version };
        driverStatePending = request;
        renderControls();
        try {
            const state = await api('/api/chofer/estado');
            if (version!==stateVersion) return;
            if (!Object.prototype.hasOwnProperty.call(state, 'viaje') || (state.viaje !== null
                && (!state.viaje || !Number.isInteger(state.viaje.id) || state.viaje.id <= 0))) {
                throw new Error('No se pudo verificar el estado del viaje. Vuelve a intentarlo.');
            }
            const previous = trip;
            trip = state.viaje;
            stateKnown = true;
            company = state.empresa || null;
            const account = state.user || state.usuario;
            if (account && typeof account.requiere_cambio_password === 'boolean') user.requiere_cambio_password = account.requiere_cambio_password;
            routes = (Array.isArray(state.horarios) ? state.horarios : []).filter(route => Number.isInteger(route.id)
                && route.id > 0 && route.activo !== false && route.disponible !== false && route.operativo_hoy !== false);
            buses = (Array.isArray(state.buses) ? state.buses : []).filter(bus => Number.isInteger(bus.id) && bus.id > 0
                && typeof bus.patente === 'string' && bus.patente.trim() && bus.activo !== false
                && (bus.chofer_id == null || Number(bus.chofer_id) === Number(user.id)));
            if (previous && !trip) { stopWebGPS(); if (native()) await nativeCall('detener').catch(() => {}); }
            if (version !== stateVersion) return;
            if (!trip && finishPending) { finishPending = false; status('El servidor confirmó que el viaje terminó.'); }
            renderOptions();
            if (accountRestriction()) status(accountRestriction());
            else if (finishPending) status('La finalización está pendiente de confirmar. Reintenta al recuperar la conexión.');
            else if (!trip && startRestriction()) status(startRestriction());
            else if (trip && native() && trip.ubicacion_en && Date.now()-Date.parse(trip.ubicacion_en)<120000) {
                status('Viaje en curso. Última ubicación recibida: ' + new Date(trip.ubicacion_en).toLocaleTimeString('es-CL') + '.');
            }
            else if (trip) status('Tienes un viaje activo. Usa Reanudar GPS si cambiaste de dispositivo o se interrumpió el seguimiento.');
            renderTrip();
        } catch (error) {
            if (version!==stateVersion) return;
            status(error.message);
            // Una pérdida temporal de red no cancela el seguimiento autorizado.
            if ([401,403,409].includes(error.status)) {
                stopWebGPS();
                if (native()) await nativeCall('detener').catch(() => {});
                routes = []; buses = [];
            }
        } finally {
            if (driverStatePending === request) driverStatePending = null;
            if (version === stateVersion) renderControls();
        }
    }

    async function companySchedules() {
        const version = stateVersion;
        const result = await api('/api/empresa/horarios');
        if (version !== stateVersion || !user || user.rol !== 'empresa') return;
        const body = el('tabla-empresa-horarios'); body.replaceChildren();
        for (const route of result.horarios) {
            const row = document.createElement('tr');
            for (const text of [route.tipo, `${route.origen} → ${route.destino}`, route.salida, route.anden || '—', route.dias || '—']) {
                const cell = document.createElement('td'); cell.className = 'p-3'; cell.textContent = text; row.append(cell);
            }
            body.append(row);
        }
    }

    async function companyDrivers() {
        const version = stateVersion;
        const result = await api('/api/empresa/choferes');
        if (version !== stateVersion || !user || user.rol !== 'empresa') return;
        el('choferes-empresa').textContent = 'Empresa asociada: ' + result.empresa.nombre;
        const list = el('lista-choferes'); list.replaceChildren();
        if (!result.choferes.length) list.textContent = 'Tu empresa todavía no tiene cuentas de chofer.';
        for (const driver of result.choferes) {
            const card = document.createElement('div'); card.className = 'p-4 bg-slate-800 rounded-xl space-y-2';
            const title = document.createElement('h4'); title.className = 'font-bold'; title.textContent = driver.nombre;
            const details = document.createElement('p'); details.className = 'text-sm';
            details.textContent = `${driver.email} · ${driver.suspendido ? 'Suspendido' : 'Activo'}${driver.telefono ? ' · ' + driver.telefono : ''}${driver.licencia ? ' · Licencia: ' + driver.licencia : ''}`;
            card.append(title, details);
            for (const action of ['estado','eliminar']) {
                const button = document.createElement('button'); button.type = 'button'; button.className = 'p-2 mr-3 rounded-lg border border-slate-500 text-sm';
                button.textContent = action === 'eliminar' ? 'Eliminar cuenta' : driver.suspendido ? 'Reactivar cuenta' : 'Suspender cuenta';
                button.onclick = async () => {
                    const message = action === 'eliminar' ? `¿Eliminar permanentemente la cuenta de ${driver.nombre}? Se cerrarán sus sesiones y su viaje activo.`
                        : driver.suspendido ? `¿Reactivar la cuenta de ${driver.nombre}?` : `¿Suspender a ${driver.nombre}? Se cerrarán sus sesiones y su viaje activo.`;
                    if (!confirm(message)) return;
                    button.disabled = true;
                    try {
                        await api(`/api/empresa/choferes/${driver.id}`, action === 'eliminar' ? 'DELETE' : 'PATCH',
                            action === 'eliminar' ? undefined : {suspendido: !driver.suspendido});
                        status('Cuenta actualizada.', true); await companyDrivers();
                    } catch (error) { status(error.message, true); button.disabled = false; }
                };
                card.append(button);
            }
            list.append(card);
        }
    }

    async function iniciar(account) {
        const previousUser = user;
        user = { ...account }; clearInterval(polling); stateVersion++;
        driverStatePending = null; nativeDiagnosticPending = null;
        if (!previousUser || previousUser.id !== user.id) {
            stopWebGPS(); trip = null; company = null; routes = []; buses = []; nativeSnapshot = null;
            stateKnown = false; finishPending = false;
        }
        renderTrip();
        const version = stateVersion;
        if (user.requiere_cambio_password === true) {
            status('Cambia tu contraseña inicial en Mi cuenta antes de realizar operaciones.', user.rol === 'empresa');
            if (user.rol === 'chofer') nativeDiagnostic();
            return;
        }
        if (user.rol === 'empresa') {
            try {
                await Promise.all([companySchedules(), companyDrivers()]);
                if (version === stateVersion) status('Las cuentas pertenecen a tu empresa.', true);
            } catch (error) { if (version === stateVersion) status(error.message, true); }
        } else if (user.rol === 'chofer') {
            await driverState();
            if (version !== stateVersion) return;
            // Se solicita el permiso al abrir la consola, antes de iniciar un viaje.
            if (!accountRestriction() && !finishPending) {
                try { await locationPermission(); } catch (error) { if (version === stateVersion) status(error.message); }
            }
            if (version !== stateVersion) return;
            polling = setInterval(() => { if(document.visibilityState==='visible') driverState(); }, 15000);
        }
    }

    function selectedBus() {
        const select = el('chofer-bus');
        if (select) return buses.find(bus => bus.id === Number(select.value));
        const input = el('chofer-patente');
        const plate = input ? input.value.replace(/[\s-]/g, '').toUpperCase() : '';
        return buses.find(bus => bus.patente.replace(/[\s-]/g, '').toUpperCase() === plate);
    }

    function eligibleStart() {
        if (!user || user.rol !== 'chofer') throw new Error('Inicia sesión con tu cuenta de chofer.');
        if (trip) throw new Error('Finaliza el viaje activo antes de iniciar otro.');
        const reason = startRestriction(); if (reason) throw new Error(reason);
        const routeId = Number(el('chofer-recorrido') && el('chofer-recorrido').value);
        if (!routes.some(route => route.id === routeId)) throw new Error('Selecciona un recorrido operativo permitido para tu cuenta.');
        const bus = selectedBus();
        if (!bus) throw new Error('Selecciona uno de los buses registrados y permitidos para tu cuenta.');
        return { horario_id: routeId, bus_id: bus.id };
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (el('choferes-recargar')) el('choferes-recargar').onclick = () => companyDrivers().catch(error => status(error.message,true));
        if (el('form-crear-chofer')) el('form-crear-chofer').onsubmit = async event => {
            event.preventDefault(); const form = event.target, button = form.querySelector('button'); button.disabled = true;
            try { await api('/api/empresa/choferes', 'POST', Object.fromEntries(new FormData(form))); form.reset(); status('Cuenta creada. El chofer ya puede iniciar sesión.',true); await companyDrivers(); }
            catch (error) { status(error.message,true); } finally { button.disabled = false; }
        };
        if (el('form-iniciar-viaje')) el('form-iniciar-viaje').onsubmit = async event => {
            event.preventDefault(); if (starting || finishing || driverStatePending) return;
            const version = stateVersion;
            starting = true; renderControls();
            try {
                eligibleStart();
                await locationPermission();
                if (version !== stateVersion) return;
                const payload = eligibleStart();
                const result=await api('/api/chofer/viajes','POST',payload);
                if (version !== stateVersion) return;
                if (!result.viaje || !Number.isInteger(result.viaje.id) || result.viaje.id <= 0) {
                    stateKnown = false;
                    throw new Error('No se pudo confirmar el viaje iniciado. Actualiza su estado antes de volver a intentarlo.');
                }
                stateVersion++; nativeDiagnosticPending = null;
                trip=result.viaje; stateKnown = true; finishPending = false;
                renderTrip(); await activateGPS(result.rastreo_token);
            } catch(error) {
                if (version === stateVersion || (version + 1 === stateVersion && trip)) status(error.message + (trip ? ' El viaje está activo; usa reanudar GPS.' : ''));
            } finally { starting = false; renderControls(); }
        };
        if (el('chofer-permisos')) el('chofer-permisos').onclick = async () => {
            const version = stateVersion;
            try {
                if (starting || finishing || finishPending) throw new Error('Espera la confirmación de la operación pendiente antes de reanudar GPS.');
                const restriction = accountRestriction(); if (restriction) throw new Error(restriction);
                await locationPermission(true);
                if (version !== stateVersion) return;
                if (trip) {
                    const tripId = trip.id;
                    const result=await api(`/api/chofer/viajes/${tripId}/credencial`,'POST',{});
                    if (version !== stateVersion || !trip || trip.id !== tripId) return;
                    await activateGPS(result.rastreo_token);
                }
                else status('Ubicación permitida. Puedes iniciar el viaje.');
            } catch(error) { if (version === stateVersion) status(error.message); }
            finally { nativeDiagnostic(); }
        };
        if (el('chofer-finalizar')) el('chofer-finalizar').onclick = async () => {
            if (!trip || finishing || starting || !confirm('¿Llegaste al destino y deseas finalizar el viaje?')) return;
            const tripId = trip.id;
            const version = ++stateVersion;
            nativeDiagnosticPending = null;
            finishing = true; finishPending = true; renderTrip();
            let stoppedLocally = !native();
            try {
                stopWebGPS();
                if(native()) { try { await nativeCall('detener'); stoppedLocally=true; } catch (_) { } }
                if (version !== stateVersion || !trip || trip.id !== tripId) return;
                await api(`/api/chofer/viajes/${tripId}/finalizar`,'POST',{});
                if (version !== stateVersion || !trip || trip.id !== tripId) return;
                stateVersion++; nativeDiagnosticPending = null;
                trip=null; finishPending = false; stateKnown = true; renderTrip();
                status('Viaje finalizado. Se revocó el envío de ubicación.');
            } catch(error) {
                if (version === stateVersion && trip && trip.id === tripId) status((stoppedLocally ? 'Se detuvo el envío local de ubicación. ' : 'No se pudo confirmar la detención del GPS. ') + 'La finalización sigue pendiente: ' + error.message + ' Reintenta al recuperar la conexión.');
            } finally { finishing = false; renderTrip(); nativeDiagnostic(); }
        };
    });
    const viajeActivo = () => !!trip || finishPending || (!stateKnown && !!(nativeSnapshot && nativeSnapshot.activo === true));
    return { iniciar, viajeActivo, estadoConocido: () => stateKnown, puedeCerrarSesion: () => stateKnown && !viajeActivo() };
})();
