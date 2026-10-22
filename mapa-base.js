/* Fondo de calles con nombres geográficos, sin comercios ni puntos de interés. */
(() => {
    'use strict';

    const ATRIBUCION = '<a href="https://openfreemap.org/">OpenFreeMap</a> · <a href="https://openmaptiles.org/">© OpenMapTiles</a> · <a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a>';
    const ATRIBUCION_SIMPLE = 'Mapas © <a href="https://www.esri.com/">Esri</a>, HERE, Garmin, <a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a> y comunidad GIS';

    function crear(map, L) {
        if (!map || !L) return null;
        map.setMaxZoom(18);
        const contenedor = map.getContainer();
        contenedor.dataset.mapaBase = 'cargando';
        const referencia = map.getPane('nombres-geograficos') || map.createPane('nombres-geograficos');
        referencia.style.zIndex = '450';
        referencia.style.pointerEvents = 'none';

        // La vista simple permite consultar recorridos cuando el navegador no admite WebGL.
        const opciones = { maxZoom: 18, maxNativeZoom: 16 };
        const baseSimple = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
            ...opciones, attribution: ATRIBUCION_SIMPLE
        }).addTo(map);
        const nombresSimples = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
            ...opciones, pane: 'nombres-geograficos'
        }).addTo(map);
        let capa = null;
        let mapaVectorial = null;
        let listo = false;
        let usandoSimple = false;

        function vistaSimple() {
            if (usandoSimple) return;
            usandoSimple = true;
            if (capa && map.hasLayer(capa)) {
                // Si falla la creación del canvas, el adaptador aún no tiene mapa que retirar.
                if (!capa.getMaplibreMap()) capa.onRemove = () => { capa.getContainer()?.remove(); };
                map.removeLayer(capa);
            }
            if (map.attributionControl) map.attributionControl.removeAttribution(ATRIBUCION);
            if (!map.hasLayer(baseSimple)) baseSimple.addTo(map);
            if (!map.hasLayer(nombresSimples)) nombresSimples.addTo(map);
            contenedor.dataset.mapaBase = 'simple';
        }

        try {
            if (!window.maplibregl || !L.maplibreGL || (typeof maplibregl.supported === 'function' && !maplibregl.supported({ failIfMajorPerformanceCaveat: false }))) {
                vistaSimple();
                return baseSimple;
            }
            capa = L.maplibreGL({
                style: '/mapa-base.json?v=oscuro-20261022',
                pane: 'tilePane',
                padding: 0.1,
                interactive: false,
                attributionControl: false,
                renderWorldCopies: false
            });
            capa.addTo(map);
            mapaVectorial = capa.getMaplibreMap();
            capa.getContainer().style.pointerEvents = 'none';
            if (map.attributionControl) map.attributionControl.addAttribution(ATRIBUCION);
            mapaVectorial.once('load', () => {
                if (usandoSimple) return;
                listo = true;
                if (map.hasLayer(baseSimple)) map.removeLayer(baseSimple);
                if (map.hasLayer(nombresSimples)) map.removeLayer(nombresSimples);
                contenedor.dataset.mapaBase = 'calles';
            });
            mapaVectorial.on('error', () => { if (!listo) vistaSimple(); });
            mapaVectorial.on('webglcontextlost', vistaSimple);
        } catch (_) {
            vistaSimple();
        }
        return capa || baseSimple;
    }

    window.BSRedMapaBase = Object.freeze({ crear });
})();
