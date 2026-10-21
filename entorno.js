/* Contexto de presentación; no identifica personas ni concede permisos. */
(() => {
    'use strict';

    const consultas = [
        window.matchMedia('(max-width: 767px)'),
        window.matchMedia('(max-width: 1023px)'),
        window.matchMedia('(pointer: coarse)'),
        window.matchMedia('(display-mode: standalone)'),
        window.matchMedia('(display-mode: minimal-ui)'),
        window.matchMedia('(display-mode: window-controls-overlay)')
    ];
    let estado = null;
    let pendiente = false;

    function actualizar() {
        const agenteMovil = Boolean(navigator.userAgentData && navigator.userAgentData.mobile)
            || /Android|iPhone|iPad|iPod|IEMobile|Windows Phone|Mobile/i.test(navigator.userAgent || '')
            || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
        const tactil = consultas[2].matches || navigator.maxTouchPoints > 0;
        const ancho = window.innerWidth || document.documentElement.clientWidth;
        const compacto = ancho < 768 || (ancho < 1024 && (agenteMovil || consultas[2].matches));
        const puente = window.BSRedAndroid;
        const instalada = navigator.standalone === true || consultas.slice(3).some(consulta => consulta.matches);
        const canal = puente && typeof puente.postMessage === 'function' ? 'android' : instalada ? 'pwa' : 'web';
        const siguiente = Object.freeze({ canal, compacto, movil: agenteMovil, tactil });
        if (estado && Object.keys(siguiente).every(clave => siguiente[clave] === estado[clave])) return;

        estado = siguiente;
        document.documentElement.dataset.canal = estado.canal;
        document.documentElement.dataset.compacto = String(estado.compacto);
        document.dispatchEvent(new CustomEvent('bsred:entorno', { detail: estado }));
    }

    function programar() {
        if (pendiente) return;
        pendiente = true;
        window.requestAnimationFrame(() => { pendiente = false; actualizar(); });
    }

    window.BSRedEntorno = Object.freeze({ obtener: () => estado });
    actualizar();
    for (const consulta of consultas) {
        if (typeof consulta.addEventListener === 'function') consulta.addEventListener('change', programar);
        else if (typeof consulta.addListener === 'function') consulta.addListener(programar);
    }
    window.addEventListener('resize', programar, { passive: true });
    window.addEventListener('orientationchange', programar, { passive: true });
    window.addEventListener('pageshow', actualizar);
    window.addEventListener('focus', programar);
    document.addEventListener('DOMContentLoaded', actualizar, { once: true });
})();
