BEGIN;
-- Las publicaciones y aprobaciones sólo se modifican desde el backend.
-- Las consultas públicas pasan por sus endpoints con filtros de validación.
ALTER TABLE public.empresas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.horarios ENABLE ROW LEVEL SECURITY;
-- Las empresas que ya operaban conservan su publicación. Los registros nuevos
-- quedan pendientes hasta revisión; el backend también fija ese estado.
ALTER TABLE public.empresas ADD COLUMN IF NOT EXISTS estado_validacion VARCHAR(12) NOT NULL DEFAULT 'aprobada'
    CHECK (estado_validacion IN ('pendiente','aprobada','rechazada'));
ALTER TABLE public.empresas ALTER COLUMN estado_validacion SET DEFAULT 'pendiente';
ALTER TABLE public.empresas ADD COLUMN IF NOT EXISTS revision_motivo VARCHAR(500);
ALTER TABLE public.empresas ADD COLUMN IF NOT EXISTS validada_en TIMESTAMPTZ;
ALTER TABLE public.empresas ADD COLUMN IF NOT EXISTS validada_por INTEGER REFERENCES public.usuarios(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS public.buses (
    id SERIAL PRIMARY KEY,
    empresa_id INTEGER NOT NULL REFERENCES public.empresas(id),
    patente VARCHAR(12) NOT NULL,
    capacidad INTEGER NOT NULL CHECK (capacidad BETWEEN 1 AND 100),
    accesible BOOLEAN NOT NULL DEFAULT FALSE,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    chofer_id INTEGER REFERENCES public.usuarios(id) ON DELETE SET NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (empresa_id, patente)
);
ALTER TABLE public.buses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.horarios ADD COLUMN IF NOT EXISTS sentido VARCHAR(8) NOT NULL DEFAULT 'ida' CHECK (sentido IN ('ida','vuelta'));
ALTER TABLE public.horarios ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE public.horarios ADD COLUMN IF NOT EXISTS precio INTEGER;
ALTER TABLE public.horarios ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS public.horario_excepciones (
    horario_id INTEGER NOT NULL REFERENCES public.horarios(id) ON DELETE CASCADE,
    fecha DATE NOT NULL,
    opera BOOLEAN NOT NULL,
    salida TIME,
    llegada TIME,
    motivo VARCHAR(300),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (horario_id, fecha)
);
ALTER TABLE public.horario_excepciones ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.paradas_oficiales (
    id SERIAL PRIMARY KEY,
    empresa_id INTEGER NOT NULL REFERENCES public.empresas(id),
    nombre VARCHAR(100) NOT NULL,
    latitud DOUBLE PRECISION NOT NULL CHECK (latitud BETWEEN -90 AND 90),
    longitud DOUBLE PRECISION NOT NULL CHECK (longitud BETWEEN -180 AND 180),
    accesible BOOLEAN NOT NULL DEFAULT FALSE,
    activa BOOLEAN NOT NULL DEFAULT TRUE,
    confirmada BOOLEAN NOT NULL DEFAULT FALSE,
    confirmada_por INTEGER REFERENCES public.usuarios(id) ON DELETE SET NULL,
    confirmada_en TIMESTAMPTZ,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE public.paradas_oficiales ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS public.horario_paradas (
    horario_id INTEGER NOT NULL REFERENCES public.horarios(id) ON DELETE CASCADE,
    parada_id INTEGER NOT NULL REFERENCES public.paradas_oficiales(id),
    orden INTEGER NOT NULL CHECK (orden >= 0),
    PRIMARY KEY (horario_id, parada_id),
    UNIQUE (horario_id, orden)
);
ALTER TABLE public.horario_paradas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.viajes ADD COLUMN IF NOT EXISTS bus_id INTEGER REFERENCES public.buses(id) ON DELETE SET NULL;
ALTER TABLE public.viajes ADD COLUMN IF NOT EXISTS distancia_m DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (distancia_m >= 0);
ALTER TABLE public.viajes ADD COLUMN IF NOT EXISTS velocidad_mps DOUBLE PRECISION;
ALTER TABLE public.viajes ADD COLUMN IF NOT EXISTS velocidad_en TIMESTAMPTZ;
ALTER TABLE public.viajes ADD COLUMN IF NOT EXISTS muestras_movimiento INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS viajes_bus_id_activo_idx ON public.viajes(bus_id) WHERE estado='en_curso' AND bus_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS viajes_empresa_historial_idx ON public.viajes(empresa_id, iniciado_en DESC, id DESC);
CREATE INDEX IF NOT EXISTS viajes_chofer_historial_idx ON public.viajes(chofer_id, iniciado_en DESC, id DESC);

CREATE TABLE IF NOT EXISTS public.auditoria_eventos (
    id BIGSERIAL PRIMARY KEY,
    actor_id INTEGER REFERENCES public.usuarios(id) ON DELETE SET NULL,
    actor_rol VARCHAR(30) NOT NULL,
    empresa_id INTEGER REFERENCES public.empresas(id) ON DELETE SET NULL,
    accion VARCHAR(80) NOT NULL,
    entidad VARCHAR(30) NOT NULL,
    entidad_id VARCHAR(80),
    detalles JSONB NOT NULL DEFAULT '{}'::jsonb,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS auditoria_empresa_fecha_idx ON public.auditoria_eventos(empresa_id, creado_en DESC, id DESC);
ALTER TABLE public.auditoria_eventos ENABLE ROW LEVEL SECURITY;
COMMIT;
