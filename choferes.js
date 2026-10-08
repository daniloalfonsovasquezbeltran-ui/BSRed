/* Cuentas por empresa y control de viajes. Ningún rol se autoriza desde el navegador. */
window.BSRedChoferes = (() => {
    let user = null, trip = null, token = null, watch = null, polling = null, sending = false;
    let nativeSequence = 0;
    const pendingNative = new Map();
    let routesLoaded = false, stateVersion = 0;
    const el = id => document.getElementById(id);
    const native = () => window.BSRedAndroid && typeof window.BSRedAndroid.postMessage === 'function';
    const status = (message, company = false) => { el(company ? 'choferes-estado' : 'chofer-estado').textContent = message; };

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

    function nativeCall(action, data = {}) {
        return new Promise((resolve, reject) => {
            const id = String(++nativeSequence);
            const timeout = setTimeout(() => { pendingNative.delete(id); reject(new Error('La app no respondió. Vuelve a abrirla.')); }, 12000);
            pendingNative.set(id, {resolve, reject, timeout});
            window.BSRedAndroid.onmessage = event => {
                try {
                    const result = JSON.parse(event.data), request = pendingNative.get(result.request_id);
                    if (!request) return;
                    clearTimeout(request.timeout); pendingNative.delete(result.request_id);
                    if (result.success) request.resolve(result); else request.reject(new Error(result.message || 'No se pudo activar la ubicación.'));
                } catch (_) { /* Una respuesta inválida no concede permisos. */ }
            };
            window.BSRedAndroid.postMessage(JSON.stringify({action, request_id: id, ...data}));
        });
    }

    async function locationPermission(requestPermission = false) {
        if (native()) {
            const result = await nativeCall(requestPermission ? 'solicitarPermisos' : 'permisos');
            if (!result.autorizado) throw new Error('Permite la ubicación precisa en la app antes de iniciar el viaje.');
            if (!result.gps_habilitado) throw new Error('Activa la ubicación del dispositivo antes de iniciar el viaje.');
            return;
        }
        if (!navigator.geolocation) throw new Error('Este dispositivo no ofrece ubicación.');
        await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve,
            () => reject(new Error('Permite la ubicación y comprueba que el GPS esté activado.')),
            {enableHighAccuracy: true, timeout: 20000, maximumAge: 10000}));
    }

    function stopWebGPS() { if (watch !== null) navigator.geolocation.clearWatch(watch); watch = null; token = null; }

    async function activateGPS(credential) {
        token = credential;
        if (native()) {
            await nativeCall('iniciar', {viaje_id: trip.id, token});
            status('Viaje iniciado. Esperando la primera ubicación de la app. El GPS puede continuar con la pantalla apagada.');
            return;
        }
        if (watch !== null) navigator.geolocation.clearWatch(watch);
        let lastSent = 0;
        watch = navigator.geolocation.watchPosition(async position => {
            if (!trip || !token || sending || Date.now()-lastSent<15000) return;
            sending = true;
            lastSent = Date.now();
            try {
                await api(`/api/chofer/viajes/${trip.id}/ubicacion`, 'POST', {
                    latitud: position.coords.latitude, longitud: position.coords.longitude,
                    precision_m: position.coords.accuracy, ubicacion_en: new Date(position.timestamp).toISOString()
                }, token);
                status('Viaje en curso. Última ubicación enviada: ' + new Date(position.timestamp).toLocaleTimeString('es-CL') + '.');
            } catch (error) {
                if (error.status===401 || error.status===403) stopWebGPS();
                status(error.message + ' Revisa la conexión y el estado del viaje.');
            }
            finally { sending = false; }
        }, () => status('El viaje sigue activo, pero no se está recibiendo GPS. Revisa los permisos.'),
        {enableHighAccuracy: true, maximumAge: 10000, timeout: 20000});
    }

    function renderTrip() {
        el('form-iniciar-viaje').hidden = !!trip;
        el('chofer-finalizar').hidden = !trip;
        el('chofer-viaje').textContent = trip ? `Viaje #${trip.id} · Bus ${trip.patente}` : 'Sin viaje activo.';
    }

    async function driverState(initial = false) {
        const version = stateVersion;
        try {
            const state = await api('/api/chofer/estado');
            if (version!==stateVersion) return;
            const previous = trip;
            trip = state.viaje;
            if (previous && !trip) { stopWebGPS(); if (native()) await nativeCall('detener').catch(() => {}); }
            if (initial || !routesLoaded) {
                el('chofer-recorrido').replaceChildren();
                for (const route of state.horarios) {
                    const option = document.createElement('option'); option.value = route.id;
                    option.textContent = `${route.origen} → ${route.destino} · ${route.salida} · ${route.dias || ''}`;
                    el('chofer-recorrido').append(option);
                }
                el('chofer-iniciar').disabled = !state.horarios.length;
                routesLoaded = true;
                if (!state.horarios.length) status('Tu empresa todavía no tiene recorridos disponibles para esta cuenta.');
                if (trip) status('Tienes un viaje activo. Reanuda el GPS si cambiaste de dispositivo o llegaste de nuevo a esta página.');
            }
            if (native() && trip && trip.ubicacion_en && Date.now()-Date.parse(trip.ubicacion_en)<120000)
                status('Viaje en curso. Última ubicación recibida: ' + new Date(trip.ubicacion_en).toLocaleTimeString('es-CL') + '.');
            renderTrip();
        } catch (error) {
            if (version!==stateVersion) return;
            status(error.message);
            // Una pérdida temporal de red no cancela el seguimiento autorizado.
            if ([401,403,409].includes(error.status)) {
                stopWebGPS();
                if (native()) await nativeCall('detener').catch(() => {});
                el('chofer-iniciar').disabled = true;
            }
        }
    }

    async function companySchedules() {
        const result = await api('/api/empresa/horarios');
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
        const result = await api('/api/empresa/choferes');
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
        user = account; clearInterval(polling); routesLoaded=false; stateVersion++;
        if (user.rol === 'empresa') {
            try { await Promise.all([companySchedules(), companyDrivers()]); status('Las cuentas pertenecen a tu empresa.', true); }
            catch (error) { status(error.message, true); }
        } else if (user.rol === 'chofer') {
            await driverState(true);
            // Se solicita el permiso al abrir la consola, antes de iniciar un viaje.
            try { await locationPermission(); } catch (error) { status(error.message); }
            polling = setInterval(() => { if(document.visibilityState==='visible') driverState(); }, 15000);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        el('choferes-recargar').onclick = () => companyDrivers().catch(error => status(error.message,true));
        el('form-crear-chofer').onsubmit = async event => {
            event.preventDefault(); const form = event.target, button = form.querySelector('button'); button.disabled = true;
            try { await api('/api/empresa/choferes', 'POST', Object.fromEntries(new FormData(form))); form.reset(); status('Cuenta creada. El chofer ya puede iniciar sesión.',true); await companyDrivers(); }
            catch (error) { status(error.message,true); } finally { button.disabled = false; }
        };
        el('form-iniciar-viaje').onsubmit = async event => {
            event.preventDefault(); const button=el('chofer-iniciar'); button.disabled=true;
            try {
                await locationPermission();
                const result=await api('/api/chofer/viajes','POST',{horario_id:Number(el('chofer-recorrido').value),patente:el('chofer-patente').value});
                stateVersion++; trip=result.viaje; renderTrip(); await activateGPS(result.rastreo_token);
            } catch(error) { status(error.message + (trip ? ' El viaje está activo; usa reanudar GPS.' : '')); }
            finally { button.disabled=false; }
        };
        el('chofer-permisos').onclick = async () => {
            try {
                await locationPermission(true);
                if (trip) { const result=await api(`/api/chofer/viajes/${trip.id}/credencial`,'POST',{}); await activateGPS(result.rastreo_token); }
                else status('Ubicación permitida. Puedes iniciar el viaje.');
            } catch(error) { status(error.message); }
        };
        el('chofer-finalizar').onclick = async () => {
            if (!trip || !confirm('¿Llegaste al destino y deseas finalizar el viaje?')) return;
            const button=el('chofer-finalizar'); button.disabled=true;
            let stoppedLocally = !native();
            try {
                stopWebGPS();
                if(native()) { try { await nativeCall('detener'); stoppedLocally=true; } catch (_) { } }
                await api(`/api/chofer/viajes/${trip.id}/finalizar`,'POST',{});
                stateVersion++; trip=null; renderTrip(); status('Viaje finalizado. Se revocó el envío de ubicación.');
            } catch(error) { status((stoppedLocally ? 'Se detuvo el envío local de ubicación. ' : 'No se pudo confirmar la detención del GPS. ') + 'La finalización sigue pendiente: ' + error.message + ' Reintenta al recuperar la conexión.'); } finally { button.disabled=false; }
        };
    });
    return {iniciar};
})();
