(() => {
    'use strict';
    const KEY = 'bsred-accesibilidad-v1';
    let prefs = { escala: 1, contraste: false };
    try { const saved = JSON.parse(localStorage.getItem(KEY)); if (saved) prefs = { escala: [1, 1.15, 1.3].includes(saved.escala) ? saved.escala : 1, contraste: saved.contraste === true }; } catch (_) { }
    function apply() {
        document.documentElement.style.fontSize = `${prefs.escala * 100}%`;
        document.documentElement.dataset.contraste = String(prefs.contraste);
        try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (_) { }
    }
    apply();
    function montar() {
        for (const parent of document.querySelectorAll('[data-accesibilidad]')) {
            const summary = document.createElement('summary'); summary.textContent = 'Accesibilidad';
            const details = document.createElement('details'); details.className = 'accesibilidad'; details.append(summary);
            const controls = document.createElement('div'); controls.className = 'a11y-controls';
            for (const [scale, label] of [[1, 'Texto normal'], [1.15, 'Texto grande'], [1.3, 'Texto muy grande']]) {
                const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
                button.setAttribute('aria-pressed', String(prefs.escala === scale));
                button.onclick = () => { prefs.escala = scale; apply(); for (const item of controls.querySelectorAll('[data-escala]')) item.setAttribute('aria-pressed', String(Number(item.dataset.escala) === scale)); };
                button.dataset.escala = String(scale); controls.append(button);
            }
            const contrast = document.createElement('button'); contrast.type = 'button'; contrast.textContent = 'Alto contraste'; contrast.setAttribute('aria-pressed', String(prefs.contraste));
            contrast.onclick = () => { prefs.contraste = !prefs.contraste; apply(); contrast.setAttribute('aria-pressed', String(prefs.contraste)); };
            controls.append(contrast); details.append(controls); parent.append(details);
        }
        for (const input of document.querySelectorAll('input,select,textarea')) {
            if (input.labels && input.labels.length) continue;
            if (!input.id) continue;
            const label = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
            if (label) continue;
            if (!input.hasAttribute('aria-label')) input.setAttribute('aria-label', input.placeholder || input.name || 'Campo del formulario');
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', montar, { once: true }); else montar();
})();
