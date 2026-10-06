(() => {
    'use strict';

    // Web Locks serializa también la primera visita, antes de tener la cookie
    // HttpOnly. Así la segunda pestaña recibe la identidad ya creada por la primera.
    let enviando = false;

    async function enviarPresencia() {
        // La pestaña puede haberse ocultado mientras esperaba a otra pestaña.
        if (document.visibilityState !== 'visible' || navigator.onLine === false) return;
        const controlador = new AbortController();
        const tiempoLimite = window.setTimeout(() => controlador.abort(), 8000);
        try {
            const respuesta = await fetch('/api/presencia', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                cache: 'no-store',
                body: JSON.stringify({ visible: true }),
                signal: controlador.signal
            });
            if (respuesta.ok) document.dispatchEvent(new Event('bsred:presencia'));
        } finally {
            window.clearTimeout(tiempoLimite);
        }
    }

    async function registrarPresencia() {
        if (enviando || document.visibilityState !== 'visible' || navigator.onLine === false) return;
        enviando = true;
        try {
            if (navigator.locks && typeof navigator.locks.request === 'function') {
                await navigator.locks.request('bsred-presencia', enviarPresencia);
            } else {
                // Navegadores antiguos siguen reutilizando la cookie existente,
                // pero no pueden serializar primeras visitas entre pestañas.
                await enviarPresencia();
            }
        } catch (_) {
            // Se reintenta en el siguiente intervalo o al recuperar conexión.
        } finally {
            enviando = false;
        }
    }

    registrarPresencia();
    window.setInterval(registrarPresencia, 30000);
    document.addEventListener('visibilitychange', registrarPresencia);
    window.addEventListener('online', registrarPresencia);

    // Actualizar también clientes que conservan el worker anterior, que
    // permitía servir respuestas de API guardadas durante una desconexión.
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' }).catch(() => {});
    }
})();
