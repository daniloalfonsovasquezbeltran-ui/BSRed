(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const text = (tag, value, cls = '') => { const node = document.createElement(tag); node.textContent = value == null ? '—' : String(value); node.className = cls; return node; };
    const button = (label, action, cls = '') => { const node = text('button', label, 'op-button ' + cls); node.type = 'button'; node.onclick = action; return node; };
    let user = null, generation = 0, currentTab = '', body, feedback, drivers = [];
    function say(message, error = false) { if (feedback) { feedback.textContent = message; feedback.dataset.error = String(error); } }
    async function api(path, method = 'GET', data) {
        if (navigator.onLine === false) throw new Error('Sin conexión. Estas gestiones necesitan internet.');
        const ctrl = new AbortController(), timeout = setTimeout(() => ctrl.abort(), 15000);
        try {
            const response = await fetch(path, { method, credentials: 'same-origin', cache: 'no-store', signal: ctrl.signal,
                headers: method === 'GET' ? {} : { 'Content-Type': 'application/json' }, body: method === 'GET' ? undefined : JSON.stringify(data || {}) });
            const result = await response.json();
            if (!response.ok || result.success === false) {
                if (response.status === 401) document.dispatchEvent(new Event('bsred:sesion-expirada'));
                throw new Error(result.message || result.error || 'No se pudo completar la operación.');
            }
            return result;
        } catch (error) { if (error.name === 'AbortError') throw new Error('La conexión tardó demasiado. Vuelve a intentarlo.'); throw error; }
        finally { clearTimeout(timeout); }
    }
    function field(spec, value) {
        const wrap = text('label', spec.label, 'op-field');
        let node;
        if (spec.options) {
            node = document.createElement('select');
            for (const [id, label] of spec.options) { const option = text('option', label); option.value = String(id); node.append(option); }
        } else if (spec.type === 'textarea') node = document.createElement('textarea');
        else { node = document.createElement('input'); node.type = spec.type || 'text'; }
        node.name = spec.name;
        node.required = !!spec.required;
        for (const prop of ['min','max','step','minLength','maxLength','placeholder']) if (spec[prop] !== undefined) node[prop] = spec[prop];
        if (spec.type === 'checkbox') node.checked = value !== undefined && value !== null ? Boolean(value) : Boolean(spec.default);
        else node.value = value !== undefined && value !== null ? String(value) : String(spec.default != null ? spec.default : spec.options && spec.options.length ? spec.options[0][0] : '');
        wrap.append(node); return wrap;
    }
    function form(title, specs, initial, submit, cancel) {
        const node = document.createElement('form'); node.className = 'op-card op-form';
        node.append(text('h3', title, 'text-lg font-bold'));
        const grid = text('div', '', 'op-grid');
        for (const spec of specs) grid.append(field(spec, initial && initial[spec.name]));
        node.append(grid);
        const actions = text('div', '', 'op-actions'), save = text('button', initial && initial.id ? 'Guardar cambios' : 'Guardar', 'op-button op-primary'); save.type = 'submit';
        actions.append(save); if (cancel) actions.append(button('Cancelar edición', cancel)); node.append(actions);
        node.onsubmit = async event => {
            event.preventDefault(); const data = {};
            for (const spec of specs) {
                const input = node.elements.namedItem(spec.name);
                data[spec.name] = spec.type === 'checkbox' ? input.checked : spec.type === 'number' ? (input.value === '' && spec.nullable ? null : Number(input.value))
                    : spec.numeric ? (input.value === '' ? null : Number(input.value)) : input.value.trim();
                if (spec.nullable && data[spec.name] === '') data[spec.name] = null;
            }
            save.disabled = true;
            try { await submit(data); } catch (error) { say(error.message, true); } finally { save.disabled = false; }
        };
        return node;
    }
    const driverField = () => ({ name: 'chofer_id', label: 'Chofer asignado', numeric: true, options: [['', 'Sin asignar'], ...drivers.filter(d => !d.suspendido).map(d => [d.id, d.nombre])] });
    const busFields = () => [
        { name: 'patente', label: 'Patente', required: true, maxLength: 12 }, { name: 'capacidad', label: 'Capacidad de pasajeros', type: 'number', required: true, min: 1, max: 100, default: 40 },
        { name: 'accesible', label: 'Bus con accesibilidad', type: 'checkbox' }, { name: 'activo', label: 'Bus activo', type: 'checkbox', default: true }, driverField()];
    const routeFields = () => [
        { name: 'tipo', label: 'Tipo', options: [['salida', 'Salida desde el terminal'], ['llegada', 'Llegada al terminal']] },
        { name: 'origen', label: 'Origen', required: true, maxLength: 100, default: 'Panguipulli' }, { name: 'destino', label: 'Destino', required: true, maxLength: 100 },
        { name: 'salida', label: 'Hora de salida · Chile', type: 'time', required: true }, { name: 'llegada', label: 'Hora de llegada · Chile (opcional)', type: 'time', nullable: true },
        { name: 'dias', label: 'Días de operación', required: true, maxLength: 100, default: 'Diario', placeholder: 'Diario o Lunes a Viernes' },
        { name: 'anden', label: 'Andén (opcional)', maxLength: 20, nullable: true }, { name: 'precio', label: 'Tarifa en pesos', type: 'number', min: 0, required: true, default: 0 },
        { name: 'sentido', label: 'Sentido', options: [['ida', 'Ida'], ['vuelta', 'Vuelta']] }, { name: 'activo', label: 'Horario activo', type: 'checkbox', default: true }, driverField()];
    const stopFields = () => [
        { name: 'nombre', label: 'Nombre de la parada', required: true, maxLength: 100 },
        { name: 'latitud', label: 'Latitud', type: 'number', step: 'any', min: -90, max: 90, required: true },
        { name: 'longitud', label: 'Longitud', type: 'number', step: 'any', min: -180, max: 180, required: true },
        { name: 'accesible', label: 'Parada accesible', type: 'checkbox' }, { name: 'activa', label: 'Parada activa', type: 'checkbox', default: true }];
    const definitions = {
        buses: { label: 'Buses', title: 'Registrar bus', fields: busFields, key: 'buses', summary: row => `${row.patente} · ${row.capacidad} pasajeros · ${row.activo ? 'Activo' : 'Inactivo'}${row.accesible ? ' · Accesible' : ''}` },
        horarios: { label: 'Horarios', title: 'Crear horario', fields: routeFields, key: 'horarios', summary: row => `${row.origen} → ${row.destino} · ${row.salida || 'Sin hora'} · ${row.dias} · ${row.sentido || 'ida'} · ${row.activo === false ? 'Inactivo' : 'Activo'}` },
        paradas: { label: 'Paradas', title: 'Registrar parada', fields: stopFields, key: 'paradas', summary: row => `${row.nombre} · ${row.latitud}, ${row.longitud}${row.confirmada ? ' · Confirmada' : ' · Pendiente de confirmar'}${row.activa === false ? ' · Inactiva' : ''}` }
    };
    async function entities(kind, version) {
        const def = definitions[kind], data = await api('/api/empresa/' + kind);
        if (version !== generation || currentTab !== kind) return;
        body.replaceChildren();
        if (data.empresa) body.append(text('p', `${data.empresa.nombre} · Validación: ${data.empresa.estado_validacion || 'pendiente'}`, 'op-note'));
        let editor = text('div', ''); body.append(editor);
        const showForm = row => {
            editor.replaceChildren(form(row ? 'Editar ' + def.label.toLowerCase() : def.title, def.fields(), row,
                async payload => { await api('/api/empresa/' + kind + (row ? '/' + row.id : ''), row ? 'PATCH' : 'POST', payload); say('Cambios guardados.'); await load(kind); }, () => showForm(null)));
        };
        showForm(null);
        const rows = data[def.key] || [];
        if (!rows.length) body.append(text('p', 'Todavía no hay registros.', 'op-note'));
        for (const row of rows) {
            const card = text('article', '', 'op-card'); card.append(text('h3', def.summary(row), 'font-bold'));
            const actions = text('div', '', 'op-actions');
            actions.append(button('Editar', () => { showForm(row); editor.scrollIntoView({ behavior: 'smooth', block: 'start' }); editor.querySelector('input,select')?.focus(); }));
            actions.append(button('Desactivar', async () => {
                if (!confirm('¿Desactivar este registro? Los viajes y horarios afectados se comprobarán en el servidor.')) return;
                try { await api(`/api/empresa/${kind}/${row.id}`, 'DELETE', {}); say('Registro desactivado.'); await load(kind); } catch (error) { say(error.message, true); }
            }, 'op-danger'));
            if (kind === 'horarios') actions.append(button('Calendario y paradas', async () => { try { await routeDetails(row, card); } catch (error) { say(error.message, true); } }));
            card.append(actions); body.append(card);
        }
    }
    async function routeDetails(route, card) {
        card.querySelector('[data-route-details]')?.remove();
        const section = text('section', '', 'op-detail'); section.dataset.routeDetails = String(route.id); card.append(section);
        const [exceptions, stops] = await Promise.all([api(`/api/empresa/horarios/${route.id}/excepciones`), api('/api/empresa/paradas')]);
        section.append(text('h4', 'Excepciones de calendario', 'font-bold'));
        section.append(form('Cambiar una fecha concreta', [
            { name: 'fecha', label: 'Fecha · Chile', type: 'date', required: true }, { name: 'opera', label: 'Opera ese día', type: 'checkbox', default: true },
            { name: 'salida', label: 'Salida alternativa (opcional)', type: 'time', nullable: true }, { name: 'llegada', label: 'Llegada alternativa (opcional)', type: 'time', nullable: true },
            { name: 'motivo', label: 'Motivo', maxLength: 300, nullable: true }], null,
        async payload => { await api(`/api/empresa/horarios/${route.id}/excepciones`, 'POST', payload); say('Calendario actualizado.'); await routeDetails(route, card); }));
        for (const exception of exceptions.excepciones || []) {
            const line = text('div', '', 'op-actions'); line.append(text('span', `${exception.fecha} · ${exception.opera ? 'Opera' : 'No opera'} · ${exception.salida || route.salida} · ${exception.motivo || ''}`));
            line.append(button('Quitar excepción', async () => { try { await api(`/api/empresa/horarios/${route.id}/excepciones/${exception.fecha}`, 'DELETE', {}); await routeDetails(route, card); } catch (error) { say(error.message, true); } })); section.append(line);
        }
        section.append(text('h4', 'Paradas en orden de recorrido', 'font-bold'), text('p', 'Añade cada parada en el orden en que se visita. El administrador confirma su ubicación antes de publicarla como confirmada.', 'op-note'));
        const available = (stops.paradas || []).filter(stop => stop.activa !== false);
        let selected = (route.paradas || []).map(stop => Number(stop.id));
        const ordered = text('ol', '', 'op-ordered'); section.append(ordered);
        function render() {
            ordered.replaceChildren();
            selected.forEach((id, index) => {
                const line = text('li', '', 'op-actions'); line.append(text('span', `${index + 1}. ${available.find(stop => Number(stop.id) === id)?.nombre || 'Parada #' + id}`));
                const up = button('Subir', () => { [selected[index - 1], selected[index]] = [selected[index], selected[index - 1]]; render(); }); up.disabled = index === 0;
                const down = button('Bajar', () => { [selected[index + 1], selected[index]] = [selected[index], selected[index + 1]]; render(); }); down.disabled = index === selected.length - 1;
                line.append(up, down, button('Quitar', () => { selected.splice(index, 1); render(); })); ordered.append(line);
            });
        }
        render(); const addLabel = field({ name: 'parada', label: 'Añadir parada', options: [['', 'Selecciona una parada'], ...available.map(stop => [stop.id, stop.nombre])] });
        section.append(addLabel, button('Añadir al recorrido', () => { const id = Number(addLabel.querySelector('select').value); if (id && !selected.includes(id)) { selected.push(id); render(); } }),
            button('Guardar orden de paradas', async () => { try { await api(`/api/empresa/horarios/${route.id}/paradas`, 'PUT', { parada_ids: selected }); route.paradas = selected.map(id => available.find(stop => Number(stop.id) === id)); say('Orden de paradas guardado.'); } catch (error) { say(error.message, true); } }, 'op-primary'));
    }
    async function notices(version) {
        const [data, routes] = await Promise.all([api('/api/empresa/avisos'), api('/api/empresa/horarios')]);
        if (version !== generation || currentTab !== 'avisos') return; body.replaceChildren();
        body.append(text('p', 'Para cambiar la operación o la hora de salida, registra también una excepción en Horarios.', 'op-note'));
        body.append(form('Publicar aviso operativo', [
            { name: 'horario_id', label: 'Recorrido', numeric: true, required: true, options: (routes.horarios || []).map(row => [row.id, `${row.origen} → ${row.destino} · ${row.salida}`]) },
            { name: 'tipo', label: 'Tipo de aviso', options: [['retraso', 'Retraso'], ['cancelacion', 'Cancelación'], ['informacion', 'Información']] },
            { name: 'fecha', label: 'Fecha · Chile', type: 'date', required: true }, { name: 'demora_min', label: 'Minutos de demora (solo retraso)', type: 'number', min: 0, max: 720, default: 0 },
            { name: 'mensaje', label: 'Mensaje para pasajeros', type: 'textarea', required: true, minLength: 5, maxLength: 500 }], null,
        async payload => { if (payload.tipo === 'retraso' && payload.demora_min < 1) throw new Error('Indica entre 1 y 720 minutos para un retraso.'); if (payload.tipo !== 'retraso') delete payload.demora_min; await api('/api/empresa/avisos', 'POST', payload); say('Aviso publicado.'); await load('avisos'); }));
        for (const row of data.avisos || []) { const card = text('article', '', 'op-card'); card.append(text('h3', `${row.tipo} · ${row.fecha}`, 'font-bold'), text('p', row.mensaje), text('p', `Recorrido #${row.horario_id}${row.demora_min ? ' · ' + row.demora_min + ' minutos de demora' : ''}`, 'op-note'));
            card.append(button('Retirar aviso', async () => { try { await api(`/api/empresa/avisos/${row.id}`, 'DELETE', {}); await load('avisos'); } catch (error) { say(error.message, true); } }, 'op-danger')); body.append(card); }
    }
    function time(value) { const d = new Date(value); return Number.isFinite(d.getTime()) ? d.toLocaleString('es-CL', { timeZone: 'America/Santiago' }) : '—'; }
    async function history(version, page = 1, filter = {}) {
        const audit = currentTab === 'auditoria';
        const prefix = user.rol === 'admin' ? '/api/admin' : user.rol === 'chofer' ? '/api/chofer' : '/api/empresa';
        const path = audit ? prefix + '/auditoria' : prefix + (user.rol === 'chofer' ? '/viajes/historial' : '/viajes');
        const query = new URLSearchParams({ pagina: page, limite: 20 }); for (const [key, value] of Object.entries(filter)) if (value) query.set(key, value);
        const result = await api(path + '?' + query);
        if (version !== generation) return; body.replaceChildren();
        body.append(form('Filtrar por fecha y estado', [
            { name: 'fecha_desde', label: 'Desde', type: 'date' }, { name: 'fecha_hasta', label: 'Hasta', type: 'date' },
            ...(!audit ? [{ name: 'estado', label: 'Estado', options: [['', 'Todos'], ['en_curso', 'En curso'], ['finalizado', 'Finalizado'], ['cancelado', 'Cancelado']] }] : [])], filter,
        async values => { await history(version, 1, values); }));
        const rows = audit ? result.auditoria || [] : result.viajes || [];
        if (!rows.length) body.append(text('p', 'No hay registros con estos filtros.', 'op-note'));
        for (const row of rows) {
            const card = text('article', '', 'op-card');
            if (audit) { card.append(text('h3', `${row.accion || row.evento || 'Cambio'} · ${row.entidad || row.recurso || ''}`, 'font-bold'), text('p', `${time(row.creada_en || row.creado_en || row.fecha)} · ${row.usuario_nombre || row.actor_id || row.usuario_id || 'Sistema'}`, 'op-note'));
                if (row.detalles) card.append(text('pre', typeof row.detalles === 'string' ? row.detalles : JSON.stringify(row.detalles, null, 2), 'op-json'));
            } else card.append(text('h3', `Viaje #${row.id} · ${row.patente || 'Bus sin patente'}`, 'font-bold'), text('p', `${row.origen || ''} → ${row.destino || ''} · ${row.estado || ''}`), text('p', `Inicio: ${time(row.iniciado_en)} · Fin: ${time(row.finalizado_en)}`, 'op-note'));
            body.append(card);
        }
        const pagination = result.paginacion || { pagina: page, paginas: 1, total: rows.length }, controls = text('div', '', 'op-actions');
        const previous = button('Anterior', () => history(version, Number(pagination.pagina) - 1, filter).catch(error => say(error.message, true))); previous.disabled = Number(pagination.pagina) <= 1;
        const next = button('Siguiente', () => history(version, Number(pagination.pagina) + 1, filter).catch(error => say(error.message, true))); next.disabled = Number(pagination.pagina) >= Number(pagination.paginas);
        controls.append(previous, text('span', `Página ${pagination.pagina} de ${Math.max(1, Number(pagination.paginas))} · ${pagination.total} registros`), next); body.append(controls);
    }
    async function fleet(version) {
        const data = await api('/api/empresa/flota'); if (version !== generation || currentTab !== 'flota') return; body.replaceChildren();
        const buses = data.buses || [], trips = data.viajes || [];
        body.append(text('h3', 'Estado de tu flota', 'text-lg font-bold'), text('p', `${buses.length} buses registrados · ${trips.length} viajes activos`, 'op-note'));
        if (!buses.length) body.append(text('p', 'Registra tus buses para consultar la flota.', 'op-note'));
        for (const bus of buses) { const trip = trips.find(row => Number(row.bus_id) === Number(bus.id) || row.patente === bus.patente), card = text('article', '', 'op-card');
            card.append(text('h4', bus.patente, 'font-bold'), text('p', trip ? `En recorrido · ${trip.origen || ''} → ${trip.destino || ''}` : 'Sin viaje activo'), text('p', `${bus.capacidad} pasajeros${bus.accesible ? ' · Accesible' : ''}`, 'op-note'));
            if (trip && trip.eta_minutos != null) card.append(text('p', `Llegada estimada: ${Math.ceil(Number(trip.eta_minutos))} min${trip.llegada_estimada_en ? ' · ' + time(trip.llegada_estimada_en) : ''}`, 'op-note'));
            if (trip) card.append(text('p', trip.ubicacion_en ? `Último GPS: ${time(trip.ubicacion_en)}` : 'Sin ubicación GPS recibida', 'op-note'));
            body.append(card);
        }
    }
    async function validation(kind, version) {
        const key = kind === 'validacion' ? 'empresas' : 'paradas', data = await api('/api/admin/' + key);
        if (version !== generation || currentTab !== kind) return; body.replaceChildren();
        for (const row of data[key] || []) {
            const card = text('article', '', 'op-card'); card.append(text('h3', row.nombre, 'font-bold'));
            if (key === 'empresas') {
                card.append(text('p', `Estado: ${row.estado_validacion || 'pendiente'} · ${row.email || ''}`, 'op-note'));
                card.append(form('Revisar empresa', [ { name: 'estado_validacion', label: 'Decisión', options: [['pendiente', 'Pendiente'], ['aprobada', 'Aprobar'], ['rechazada', 'Rechazar']] }, { name: 'motivo', label: 'Motivo de la revisión', type: 'textarea', maxLength: 500 }], { estado_validacion: row.estado_validacion, motivo: row.revision_motivo },
                    async payload => { await api(`/api/admin/empresas/${row.id}/revision`, 'PATCH', payload); say('Revisión guardada.'); await load(kind); }));
            } else {
                card.append(text('p', `${row.latitud}, ${row.longitud} · ${row.confirmada ? 'Confirmada' : 'Pendiente'} · Empresa ${row.empresa || row.empresa_id}`, 'op-note'));
                const mapLink = text('a', 'Consultar ubicación en OpenStreetMap', 'op-button'); mapLink.href = `https://www.openstreetmap.org/?mlat=${Number(row.latitud)}&mlon=${Number(row.longitud)}#map=17/${Number(row.latitud)}/${Number(row.longitud)}`; mapLink.target = '_blank'; mapLink.rel = 'noopener noreferrer'; card.append(mapLink);
                card.append(button(row.confirmada ? 'Quitar confirmación' : 'Confirmar ubicación', async () => { try { await api(`/api/admin/paradas/${row.id}/revision`, 'PATCH', { confirmada: !row.confirmada }); say('Parada revisada.'); await load(kind); } catch (error) { say(error.message, true); } }, 'op-primary'));
            }
            body.append(card);
        }
        if (!(data[key] || []).length) body.append(text('p', 'No hay registros pendientes.', 'op-note'));
    }
    async function load(tab) {
        currentTab = tab; const version = ++generation; if (body) body.setAttribute('aria-labelledby', 'op-tab-' + tab); say('Cargando...'); body.replaceChildren();
        for (const node of document.querySelectorAll('[data-operacion-tab]')) { const active = node.dataset.operacionTab === tab; node.setAttribute('aria-selected', String(active)); node.tabIndex = active ? 0 : -1; }
        try {
            if (definitions[tab]) await entities(tab, version);
            else if (tab === 'avisos') await notices(version);
            else if (tab === 'flota') await fleet(version);
            else if (tab === 'historial' || tab === 'auditoria') await history(version);
            else await validation(tab, version);
            if (version === generation) say('Datos actualizados.');
        } catch (error) { if (version === generation) { say(error.message, true); body.append(button('Reintentar', () => load(tab))); } }
    }
    async function iniciar(account) {
        user = account; ++generation;
        const root = $('operaciones-panel'); if (!root) return; root.replaceChildren();
        if (!user || user.offline || !['empresa','admin','chofer'].includes(user.rol)) return;
        root.append(text('h2', user.rol === 'admin' ? 'Validación y auditoría' : user.rol === 'empresa' ? 'Operaciones de la empresa' : 'Historial de mis viajes', 'text-xl font-bold'));
        feedback = text('p', '', 'op-note'); feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite'); root.append(feedback);
        const nav = text('div', '', 'op-tabs'); nav.setAttribute('role', 'tablist'); nav.setAttribute('aria-label', 'Gestiones disponibles'); root.append(nav);
        const tabs = user.rol === 'empresa' ? [['buses','Buses'],['horarios','Horarios'],['paradas','Paradas'],['flota','Flota'],['avisos','Avisos'],['historial','Historial'],['auditoria','Auditoría']]
            : user.rol === 'admin' ? [['validacion','Empresas'],['revision-paradas','Paradas'],['auditoria','Auditoría']] : [['historial','Historial']];
        for (const [key, label] of tabs) { const tab = button(label, () => load(key)); tab.dataset.operacionTab = key; tab.setAttribute('role', 'tab'); tab.id = 'op-tab-' + key; tab.setAttribute('aria-controls', 'operaciones-contenido'); nav.append(tab); }
        nav.onkeydown = event => { const tabs = [...nav.querySelectorAll('[role=tab]')], index = tabs.indexOf(document.activeElement); if (index < 0) return; let next; if (event.key === 'ArrowRight') next = (index + 1) % tabs.length; else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length; else if (event.key === 'Home') next = 0; else if (event.key === 'End') next = tabs.length - 1; else return; event.preventDefault(); tabs[next].focus(); tabs[next].click(); };
        body = text('div', '', 'op-content'); body.id = 'operaciones-contenido'; body.setAttribute('role', 'tabpanel'); root.append(body);
        if (user.rol === 'empresa') { try { drivers = (await api('/api/empresa/choferes')).choferes || []; } catch (error) { say(error.message, true); } }
        await load(tabs[0][0]);
    }
    window.BSRedOperaciones = Object.freeze({ iniciar });
})();
