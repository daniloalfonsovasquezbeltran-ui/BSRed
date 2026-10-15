(() => {
    'use strict';
    const PREFIX = 'bsred-offline-v1:';
    const SESSION = 'bsred-offline-sesion';
    const REVOKED = 'bsred-offline-revocada';
    const PENDING_LOGOUT = 'bsred-offline-cierre-pendiente';
    const MAX_AGE = 7 * 86400000;
    const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('bsred-sesion') : null;
    let account = null, expirationTimer = null;
    function read(storage, key) { try { return JSON.parse(storage.getItem(key)); } catch (_) { return null; } }
    function write(storage, key, value) { try { storage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; } }
    function privatePurge() {
        try { for (const key of Object.keys(localStorage)) if (key.startsWith(PREFIX + 'favoritos:')) localStorage.removeItem(key); } catch (_) { }
    }
    function revoked() {
        account = null; clearTimeout(expirationTimer);
        try { sessionStorage.removeItem(SESSION); } catch (_) { }
        window.dispatchEvent(new Event('bsred:offline-sesion-revocada'));
    }
    function limpiarSesion() {
        privatePurge(); revoked();
        try { localStorage.setItem(REVOKED, String(Date.now())); } catch (_) { }
        if (channel) channel.postMessage({ tipo: 'cerrar' });
    }
    function sesion(user) {
        if (!user || !Number.isInteger(Number(user.id))) { limpiarSesion(); return; }
        const previous = read(sessionStorage, SESSION);
        if ((account && Number(account.id) !== Number(user.id)) || (previous && Number(previous.user.id) !== Number(user.id))) {
            privatePurge();
            try { localStorage.setItem(REVOKED, String(Date.now() - 1)); } catch (_) { }
            if (channel) channel.postMessage({ tipo: 'cerrar' });
        }
        account = { id: Number(user.id), nombre: String(user.nombre || 'Usuario'), sesion_expira_en: String(user.sesion_expira_en || '') };
        write(sessionStorage, SESSION, { user: account, verificada_en: Date.now() });
        programarVencimiento(account);
    }
    function programarVencimiento(active) {
        clearTimeout(expirationTimer);
        const remaining = Date.parse(active.sesion_expira_en) - Date.now();
        if (Number.isFinite(remaining) && remaining > 0) expirationTimer = setTimeout(limpiarSesion, Math.min(remaining, 2147483647));
    }
    function usuario() {
        if (navigator.onLine !== false || cierrePendiente()) return null;
        const saved = read(sessionStorage, SESSION);
        let revokedAt = 0;
        try { revokedAt = Number(localStorage.getItem(REVOKED)); } catch (_) { }
        if (!saved || !saved.user || !Number.isFinite(saved.verificada_en)
            || Date.now() - saved.verificada_en > 12 * 3600000 || saved.verificada_en <= revokedAt
            || !Number.isFinite(Date.parse(saved.user.sesion_expira_en)) || Date.parse(saved.user.sesion_expira_en) <= Date.now()) return null;
        account = saved.user; programarVencimiento(account);
        return { ...saved.user, rol: 'pasajero', offline: true };
    }
    function guardarFavoritos(user, rows) {
        if (!user || !account || Number(user.id) !== Number(account.id) || !Array.isArray(rows)) return;
        write(localStorage, PREFIX + 'favoritos:' + Number(user.id), { actualizado_en: new Date().toISOString(), favoritos: rows });
    }
    function leerFavoritos(user) {
        const active = account || usuario();
        if (!active || !user || Number(active.id) !== Number(user.id) || !Number.isFinite(Date.parse(active.sesion_expira_en)) || Date.parse(active.sesion_expira_en) <= Date.now()) return null;
        const saved = read(localStorage, PREFIX + 'favoritos:' + Number(user.id));
        return saved && Array.isArray(saved.favoritos) ? saved : null;
    }
    function guardarHorarios(tab, rows, fechaOperacion) {
        if (!['salidas', 'llegadas'].includes(tab) || !Array.isArray(rows)) return;
        write(localStorage, PREFIX + 'horarios:' + tab, { actualizado_en: new Date().toISOString(), fecha_operacion: fechaOperacion || null, horarios: rows });
    }
    function leerHorarios(tab, fechaOperacion) {
        const saved = read(localStorage, PREFIX + 'horarios:' + tab);
        if (!saved || (fechaOperacion && saved.fecha_operacion !== fechaOperacion) || !Array.isArray(saved.horarios) || !Number.isFinite(Date.parse(saved.actualizado_en))
            || Date.now() - Date.parse(saved.actualizado_en) > MAX_AGE) return null;
        return saved;
    }
    function cierrePendiente() { try { return !!localStorage.getItem(PENDING_LOGOUT); } catch (_) { return false; } }
    function marcarCierrePendiente() { try { localStorage.setItem(PENDING_LOGOUT, String(Date.now())); } catch (_) { } limpiarSesion(); }
    function cancelarCierrePendiente() { try { localStorage.removeItem(PENDING_LOGOUT); } catch (_) { } }
    async function reconciliarCierre() {
        if (!cierrePendiente()) return { cerrado: false, pendiente: false };
        if (navigator.onLine === false) return { cerrado: false, pendiente: true };
        const ctrl = new AbortController(), timeout = setTimeout(() => ctrl.abort(), 12000);
        try {
            const response = await fetch('/api/logout', { method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: '{}', signal: ctrl.signal });
            const data = await response.json();
            if (response.status === 409) return { cerrado: false, pendiente: true, viaje_activo: true, mensaje: data.message || 'Finaliza tu viaje antes de cerrar sesión.' };
            if (!response.ok) return { cerrado: false, pendiente: true, mensaje: 'No se pudo confirmar el cierre del servidor. Reintenta con conexión.' };
            cancelarCierrePendiente(); limpiarSesion(); return { cerrado: true, pendiente: false };
        } catch (_) { return { cerrado: false, pendiente: true, mensaje: 'El cierre del servidor sigue pendiente. Se reintentará al recuperar la conexión.' }; }
        finally { clearTimeout(timeout); }
    }
    function fecha(value) {
        const date = new Date(value);
        return Number.isFinite(date.getTime()) ? date.toLocaleString('es-CL', { timeZone: 'America/Santiago' }) : 'fecha no disponible';
    }
    window.addEventListener('storage', event => { if (event.key === REVOKED) revoked(); });
    if (channel) channel.onmessage = event => { if (event.data && event.data.tipo === 'cerrar') revoked(); };
    function estado() {
        for (const node of document.querySelectorAll('[data-estado-conexion]')) {
            node.textContent = navigator.onLine === false ? 'Sin conexión · consulta los datos guardados; los cambios requieren internet.' : 'Con conexión';
            node.dataset.offline = String(navigator.onLine === false);
        }
        document.documentElement.dataset.offline = String(navigator.onLine === false);
    }
    document.addEventListener('DOMContentLoaded', estado);
    window.addEventListener('online', estado); window.addEventListener('offline', estado);
    window.BSRedOffline = Object.freeze({ sesion, usuario, guardarFavoritos, leerFavoritos, guardarHorarios, leerHorarios, limpiarSesion, cierrePendiente, marcarCierrePendiente, cancelarCierrePendiente, reconciliarCierre, fecha });
})();
