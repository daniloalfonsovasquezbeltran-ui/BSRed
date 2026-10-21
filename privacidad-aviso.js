(() => {
    'use strict';

    const KEY = 'bsred-privacidad-aceptada';
    const COOKIE = 'bsred_privacidad_aceptada';

    function aceptada() {
        try {
            if (localStorage.getItem(KEY) === '1') return true;
        } catch (_) { }
        try {
            return document.cookie.split(';').some(cookie => cookie.trim() === `${COOKIE}=1`);
        } catch (_) {
            return false;
        }
    }

    function recordar() {
        try {
            localStorage.setItem(KEY, '1');
            return;
        } catch (_) { }
        try {
            document.cookie = `${COOKIE}=1; Max-Age=34560000; Path=/; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
        } catch (_) { }
    }

    function mostrar() {
        if (aceptada()) return;

        const anterior = document.activeElement;
        const aviso = document.createElement('dialog');
        aviso.className = 'aviso-privacidad';
        aviso.setAttribute('aria-labelledby', 'privacidad-titulo');
        aviso.setAttribute('aria-describedby', 'privacidad-descripcion');
        aviso.innerHTML = `
            <h2 id="privacidad-titulo" tabindex="-1">Tu privacidad en BSRed</h2>
            <p id="privacidad-descripcion">Antes de continuar, lee y acepta nuestra política de privacidad.
                Explica cómo usamos los datos de tu cuenta, la ubicación y las notificaciones.</p>
            <a class="privacidad-enlace" href="/privacidad.html" target="_blank" rel="noopener">Leer la política de privacidad <span>(abre otra pestaña)</span></a>
            <button type="button" class="privacidad-aceptar">Aceptar y continuar</button>
            <p class="privacidad-recordatorio">Recordaremos tu aceptación en este navegador.</p>
        `;

        function cerrar() {
            window.removeEventListener('storage', sincronizar);
            aviso.close();
            aviso.remove();
            document.documentElement.classList.remove('privacidad-pendiente');
            if (anterior && anterior.isConnected) anterior.focus({ preventScroll: true });
        }

        function sincronizar(evento) {
            if (evento.key === KEY && evento.newValue === '1') cerrar();
        }

        aviso.addEventListener('cancel', evento => evento.preventDefault());
        aviso.querySelector('button').addEventListener('click', () => {
            recordar();
            cerrar();
        });
        window.addEventListener('storage', sincronizar);
        document.body.append(aviso);
        document.documentElement.classList.add('privacidad-pendiente');
        aviso.showModal();
        aviso.querySelector('h2').focus({ preventScroll: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mostrar, { once: true });
    } else {
        mostrar();
    }
})();
