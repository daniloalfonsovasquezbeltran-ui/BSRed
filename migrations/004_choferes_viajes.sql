BEGIN;
ALTER TABLE public.usuarios ADD COLUMN IF NOT EXISTS empresa_id INTEGER REFERENCES public.empresas(id);
ALTER TABLE public.usuarios ADD COLUMN IF NOT EXISTS suspendido BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.usuarios ADD COLUMN IF NOT EXISTS telefono VARCHAR(30);
ALTER TABLE public.usuarios ADD COLUMN IF NOT EXISTS licencia VARCHAR(40);
ALTER TABLE public.usuarios ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE public.horarios ADD COLUMN IF NOT EXISTS chofer_id INTEGER REFERENCES public.usuarios(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS usuarios_empresa_rol_idx ON public.usuarios (empresa_id, rol);
CREATE UNIQUE INDEX IF NOT EXISTS usuarios_email_normalizado_idx ON public.usuarios (LOWER(TRIM(email)));
-- Las identidades y permisos se gestionan mediante sesiones del backend.
ALTER TABLE public.usuarios ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS public.viajes (
    id BIGSERIAL PRIMARY KEY,
    empresa_id INTEGER NOT NULL REFERENCES public.empresas(id),
    chofer_id INTEGER REFERENCES public.usuarios(id) ON DELETE SET NULL,
    horario_id INTEGER NOT NULL REFERENCES public.horarios(id),
    patente VARCHAR(12) NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'en_curso' CHECK (estado IN ('en_curso','finalizado','cancelado')),
    iniciado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finalizado_en TIMESTAMPTZ,
    rastreo_hash CHAR(64),
    latitud DOUBLE PRECISION,
    longitud DOUBLE PRECISION,
    precision_m DOUBLE PRECISION,
    ubicacion_en TIMESTAMPTZ,
    CHECK (latitud IS NULL OR latitud BETWEEN -90 AND 90),
    CHECK (longitud IS NULL OR longitud BETWEEN -180 AND 180)
);
CREATE UNIQUE INDEX IF NOT EXISTS viajes_chofer_activo_idx ON public.viajes (chofer_id) WHERE estado='en_curso';
CREATE UNIQUE INDEX IF NOT EXISTS viajes_bus_activo_idx ON public.viajes (empresa_id, patente) WHERE estado='en_curso';
CREATE INDEX IF NOT EXISTS viajes_horario_estado_idx ON public.viajes (horario_id, estado);
ALTER TABLE public.viajes ENABLE ROW LEVEL SECURITY;
-- La antigua bandera por sí sola no es una ubicación GPS.
UPDATE public.usuarios SET gps_activo=FALSE WHERE gps_activo=TRUE;
COMMIT;
