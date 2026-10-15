/* Preferencias del dispositivo Android. Los tokens sólo viajan por HTTPS al backend. */
(() => {
    'use strict';
    let nativeSequence = 0;
    const pending = new Map();
    let bridge = null;

    function attachBridge() {
        const port = window.BSRedAndroid;
        if (!port || typeof port.postMessage !== 'function') return false;
        if (bridge === port && port.onmessage === receive) return true;
        bridge = port;
        port.onmessage = receive;
        return true;
    }

    function receive(event) {
        try {
            const result = JSON.parse(event.data);
            const request = pending.get(String(result.request_id));
            if (!request) return;
            clearTimeout(request.timeout);
            pending.delete(String(result.request_id));
            if (result.success) request.resolve(result);
            else request.reject(new Error(result.message || 'No se pudo completar la operación en Android.'));
        } catch (_) { /* Datos inválidos no conceden permiso ni registran dispositivos. */ }
    }

    window.BSRedNativeCall = (action, data = {}, milliseconds = 65000) => new Promise((resolve, reject) => {
        if (!attachBridge()) { reject(new Error('Esta función requiere la aplicación Android.')); return; }
        const requestId = 'native-' + Date.now() + '-' + (++nativeSequence);
        const timeout = setTimeout(() => {
            pending.delete(requestId);
            reject(new Error('La app no respondió. Comprueba la conexión y vuelve a intentarlo.'));
        }, milliseconds);
        pending.set(requestId, {resolve, reject, timeout});
        try { bridge.postMessage(JSON.stringify({...data, action, request_id: requestId})); }
        catch (_) {
            clearTimeout(timeout); pending.delete(requestId);
            reject(new Error('No se pudo comunicar con la app Android.'));
        }
    });

    let user = null, version = 0, busy = false, state = null, token = null, refreshPending = false;
    let statusText = '', activeController = null;
    const mount = () => document.getElementById('avisos-android');
    function node(tag, text, classes = '') {
        const element = document.createElement(tag);
        element.textContent = text;
        element.className = classes;
        return element;
    }
    function render() {
        const root = mount();
        if (!root) return;
        root.hidden = !window.BSRedAndroid;
        root.replaceChildren();
        if (root.hidden) return;
        root.append(node('h3', 'Avisos en Android', 'font-bold text-white'));
        root.append(node('p', 'Recibe salidas programadas y avisos de tus recorridos favoritos.', 'text-sm text-slate-400'));
        root.append(node('p', statusText || (!user ? 'Inicia sesión para vincular este dispositivo.' : 'Comprobando avisos…'), 'text-sm text-slate-300'));
        if (!user) return;
        const enabled = state && state.habilitado && state.autorizado;
        const button = node('button', busy ? 'Procesando…' : enabled ? 'Desactivar avisos Android' : 'Activar avisos Android',
                            'mt-3 px-4 py-3 rounded-xl bg-blue-600 text-white font-bold');
        button.type = 'button'; button.disabled = busy;
        button.addEventListener('click', () => enabled ? disable() : enable());
        root.append(button);
    }
    async function api(method, value, sessionVersion) {
        const controller = new AbortController(); activeController = controller;
        const timeout = setTimeout(() => controller.abort(), 65000);
        try {
            const response = await fetch('/api/notificaciones/android/dispositivos', {
                method, credentials: 'same-origin', cache: 'no-store',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token: value}), signal: controller.signal
            });
            const body = await response.json();
            if (sessionVersion !== version) throw new Error('La sesión cambió.');
            if (!response.ok || !body.success) {
                const error = new Error(body.message || 'No se pudo guardar el dispositivo.');
                error.status = response.status; throw error;
            }
            return body;
        } finally {
            clearTimeout(timeout);
            if (activeController === controller) activeController = null;
        }
    }
    function describe(result) {
        if (!result.configurado) return 'Los avisos Android requieren completar Firebase en el servidor.';
        if (!result.autorizado) return 'Permite las notificaciones en Android para recibir avisos.';
        return result.habilitado ? 'Avisos Android activados para tus favoritos.' : 'Los avisos Android están desactivados en este dispositivo.';
    }
    async function refresh() {
        if (busy) { refreshPending=true; return; }
        if (!user || !attachBridge()) { render(); return; }
        const current = version;
        busy = true; render();
        try {
            const result = await window.BSRedNativeCall('notificacionesEstado');
            if (current !== version) return;
            state = result;
            if (result.habilitado && result.autorizado && result.configurado && result.token) {
                const registered = await api('POST', result.token, current);
                await window.BSRedNativeCall('vincularNotificaciones', {binding_id: registered.binding_id, token: result.token});
                if (current !== version) return;
                token = result.token;
            }
            statusText = describe(result);
        } catch (error) {
            if (current === version) {
                statusText = error.message;
                if (error.status===401 || error.status===409) {
                    await window.BSRedNativeCall('desvincularNotificaciones').catch(() => {});
                    if (current===version) {state={...state,habilitado:false};token=null;}
                }
            }
        }
        finally { if (current === version) { busy = false; render(); if(refreshPending){refreshPending=false;refresh();} } }
    }
    async function enable() {
        if (!user || busy) return;
        const current = version; busy = true; render();
        try {
            const result = await window.BSRedNativeCall('solicitarNotificaciones');
            if (current !== version) return;
            state = result;
            if (!result.configurado || !result.autorizado || !result.token) { statusText = describe(result); return; }
            const registered = await api('POST', result.token, current);
            await window.BSRedNativeCall('vincularNotificaciones', {binding_id: registered.binding_id, token: result.token});
            if (current !== version) return;
            token = result.token; statusText = describe(result);
        } catch (error) {
            if (current === version) {
                statusText = error.message;
                if (error.status===401 || error.status===409) {
                    await window.BSRedNativeCall('desvincularNotificaciones').catch(() => {});
                    if (current===version) {state={...state,habilitado:false};token=null;}
                }
            }
        }
        finally { if (current === version) { busy = false; render(); if(refreshPending){refreshPending=false;refresh();} } }
    }
    async function disable() {
        if (!user || busy) return;
        const current = version; busy = true; render();
        try {
            const previous = token || (state && state.token);
            // Revocar en servidor primero permite reintentar si se pierde la red.
            if (previous) await api('DELETE', previous, current);
            if (current !== version) return;
            await window.BSRedNativeCall('desvincularNotificaciones');
            if (current !== version) return;
            token = null; state = {...state, habilitado: false};
            statusText = 'Avisos Android desactivados en este dispositivo.';
        } catch (error) { if (current === version) statusText = error.message; }
        finally { if (current === version) { busy = false; render(); if(refreshPending){refreshPending=false;refresh();} } }
    }
    function iniciar(account) {
        const changed = (!user && account) || (user && !account) || (user && account && user.id !== account.id);
        if (!changed) { render(); return; }
        const previous = user;
        version++; user = account || null; busy = false; state = null; token = null; statusText = ''; refreshPending=false;
        if (activeController) activeController.abort();
        render();
        if (previous && (!user || previous.id !== user.id) && attachBridge()) {
            const current=version;
            window.BSRedNativeCall('desvincularNotificaciones').catch(() => {}).then(() => {
                if (current===version && user) refresh();
            });
        } else if (user) refresh();
    }
    window.addEventListener('bsred:android-notificaciones', refresh);
    window.addEventListener('online', refresh);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
    window.BSRedNotificacionesAndroid = Object.freeze({iniciar, refrescar: refresh});
})();
