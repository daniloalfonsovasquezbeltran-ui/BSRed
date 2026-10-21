/* Cuenta y recuperación: el token del enlace permanece sólo en memoria. */
(() => {
    'use strict';
    let revision = 0;
    let tokenRestablecer = null;

    function element(tag, text, classes) {
        const node = document.createElement(tag);
        if (text) node.textContent = text;
        if (classes) node.className = classes;
        return node;
    }

    function mount(title, explanation) {
        const panel = document.getElementById('cuentas-panel');
        if (!panel) return null;
        panel.hidden = false;
        panel.classList.remove('hidden');
        panel.replaceChildren();
        const card = element('section', '', 'bg-slate-900 border border-slate-700 rounded-2xl p-5 space-y-4');
        card.append(element('h2', title, 'text-lg font-bold'),
            element('p', explanation, 'text-sm text-slate-300'));
        panel.append(card);
        return card;
    }

    function field(form, name, label, type = 'password', current = false) {
        const wrapper = element('label', label, 'block text-sm text-slate-200');
        const input = document.createElement('input');
        input.name = name;
        input.type = type;
        input.required = true;
        input.maxLength = type === 'email' ? 255 : 128;
        if (type === 'password' && !current) input.minLength = 10;
        input.autocomplete = type === 'email' ? 'email' : current ? 'current-password' : 'new-password';
        input.className = 'block w-full mt-1 p-3 rounded-xl bg-slate-800 border border-slate-600';
        wrapper.append(input);
        form.append(wrapper);
        return input;
    }

    function feedback(form) {
        const status = element('p', '', 'text-sm text-slate-300');
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        form.append(status);
        return status;
    }

    function submit(form, label) {
        const button = element('button', label, 'rounded-xl bg-blue-600 hover:bg-blue-500 px-4 py-3 font-semibold');
        button.type = 'submit';
        form.append(button);
        return button;
    }

    async function api(path, payload) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch(path, {
                method: payload === undefined ? 'GET' : 'POST',
                credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
                headers: payload === undefined ? {} : { 'Content-Type': 'application/json' },
                body: payload === undefined ? undefined : JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.message || 'No se pudo completar la solicitud.');
            return data;
        } catch (error) {
            if (error.name === 'AbortError') throw new Error('La solicitud tardó demasiado. Intenta nuevamente.');
            if (error instanceof TypeError) throw new Error('No se pudo conectar con BSRed. Revisa tu conexión.');
            throw error;
        } finally {
            clearTimeout(timeout);
        }
    }

    function back(card) {
        const link = element('a', 'Volver al inicio', 'inline-block text-blue-400 hover:underline text-sm');
        link.href = '/';
        card.append(link);
    }

    function recovery() {
        const local = ++revision;
        const card = mount('Recuperar contraseña', 'Escribe el correo de tu cuenta para solicitar un enlace de un solo uso.');
        if (!card) return;
        const form = element('form', '', 'space-y-4');
        const email = field(form, 'email', 'Correo de la cuenta', 'email');
        const status = feedback(form);
        const button = submit(form, 'Enviar instrucciones');
        button.disabled = true;
        status.textContent = 'Comprobando disponibilidad del correo…';
        card.append(form);
        back(card);
        api('/api/cuentas/config').then(config => {
            if (local !== revision) return;
            if (config.recuperacion_disponible) {
                status.textContent = '';
                button.disabled = false;
            } else {
                status.textContent = 'La recuperación por correo todavía no está disponible. Contacta al administrador de BSRed.';
            }
        }).catch(error => {
            if (local !== revision) return;
            status.textContent = error.message + ' Vuelve a abrir esta página para reintentar.';
        });
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (button.disabled) return;
            button.disabled = true;
            status.textContent = 'Solicitando instrucciones…';
            try {
                const result = await api('/api/cuentas/recuperar', { email: email.value.trim() });
                if (local !== revision) return;
                status.textContent = result.message;
                email.value = '';
            } catch (error) {
                if (local === revision) status.textContent = error.message;
            } finally {
                if (local === revision) button.disabled = false;
            }
        });
    }

    function reset() {
        const local = ++revision;
        const card = mount('Restablecer contraseña', 'Elige una contraseña de 10 a 128 caracteres. El enlace vence a los 10 minutos y sólo puede usarse una vez.');
        if (!card) return;
        const form = element('form', '', 'space-y-4');
        const password = field(form, 'password', 'Nueva contraseña');
        const confirmation = field(form, 'confirmacion', 'Confirma la nueva contraseña');
        const status = feedback(form);
        const button = submit(form, 'Guardar nueva contraseña');
        card.append(form);
        back(card);
        if (!tokenRestablecer || !/^[A-Za-z0-9_-]{43}$/.test(tokenRestablecer)) {
            status.textContent = 'El enlace no es válido. Solicita uno nuevo.';
            button.disabled = true;
            return;
        }
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (button.disabled) return;
            if (password.value !== confirmation.value) {
                status.textContent = 'Las contraseñas no coinciden.';
                return;
            }
            button.disabled = true;
            status.textContent = 'Restableciendo contraseña…';
            try {
                const result = await api('/api/cuentas/restablecer', { token: tokenRestablecer, password: password.value });
                if (local !== revision) return;
                tokenRestablecer = null;
                password.value = confirmation.value = '';
                password.disabled = confirmation.disabled = true;
                status.textContent = result.message;
            } catch (error) {
                if (local === revision) {
                    status.textContent = error.message;
                    button.disabled = false;
                }
            }
        });
    }

    function iniciar(user) {
        const local = ++revision;
        if (!user) {
            const panel = document.getElementById('cuentas-panel');
            if (panel) panel.replaceChildren();
            return;
        }
        tokenRestablecer = null;
        const mandatory = user.requiere_cambio_password === true;
        const card = mount(mandatory ? 'Cambia tu contraseña inicial' : 'Cambiar contraseña', mandatory
            ? 'Antes de continuar, reemplaza la contraseña inicial que te entregó tu empresa por una que sólo tú conozcas.'
            : 'Al cambiarla, las otras sesiones y los avisos de tus dispositivos se desactivan. Puedes volver a activar los avisos después.');
        if (!card) return;
        const form = element('form', '', 'space-y-4');
        const current = field(form, 'password_actual', 'Contraseña actual', 'password', true);
        const password = field(form, 'password_nuevo', 'Nueva contraseña');
        const confirmation = field(form, 'confirmacion', 'Confirma la nueva contraseña');
        const status = feedback(form);
        const button = submit(form, 'Actualizar contraseña');
        card.append(form);
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (button.disabled) return;
            if (password.value !== confirmation.value) {
                status.textContent = 'Las contraseñas no coinciden.';
                return;
            }
            button.disabled = true;
            status.textContent = 'Actualizando contraseña…';
            try {
                const result = await api('/api/cuentas/password', {
                    password_actual: current.value, password_nuevo: password.value
                });
                if (local !== revision) return;
                current.value = password.value = confirmation.value = '';
                status.textContent = result.message;
                window.dispatchEvent(new CustomEvent('bsred:sesion-actualizada', { detail: { user: result.user } }));
            } catch (error) {
                if (local === revision) status.textContent = error.message;
            } finally {
                if (local === revision) button.disabled = false;
            }
        });
    }

    function procesarRestablecimiento() {
        const fragment = window.location.hash.slice(1);
        if (fragment !== 'recuperar' && !fragment.startsWith('restablecer=')) return false;
        // Borrar antes de consultar la red o escribir el token en cualquier nodo.
        history.replaceState(null, '', window.location.pathname + window.location.search);
        if (fragment === 'recuperar') recovery();
        else {
            tokenRestablecer = fragment.slice('restablecer='.length);
            reset();
        }
        return true;
    }

    window.BSRedCuentas = Object.freeze({ iniciar, procesarRestablecimiento });
})();
