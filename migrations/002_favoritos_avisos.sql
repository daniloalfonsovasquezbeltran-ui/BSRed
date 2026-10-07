-- Favoritos y suscripciones privadas. Repetible, sin alterar cuentas ni horarios.
BEGIN;

CREATE TABLE IF NOT EXISTS public.favoritos_recorridos (
    usuario_id INTEGER NOT NULL REFERENCES public.usuarios(id) ON DELETE CASCADE,
    horario_id INTEGER NOT NULL REFERENCES public.horarios(id) ON DELETE CASCADE,
    anticipacion_min SMALLINT NOT NULL DEFAULT 10 CHECK (anticipacion_min IN (5, 10, 15)),
    creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (usuario_id, horario_id)
);
CREATE INDEX IF NOT EXISTS favoritos_recorridos_horario_idx
    ON public.favoritos_recorridos (horario_id);

CREATE TABLE IF NOT EXISTS public.suscripciones_push (
    id BIGSERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES public.usuarios(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,
    endpoint_hash CHAR(64) NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    dispositivo_hash CHAR(64) NOT NULL UNIQUE,
    activa BOOLEAN NOT NULL DEFAULT TRUE,
    creada_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizada_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS suscripciones_push_activas_idx
    ON public.suscripciones_push (usuario_id) WHERE activa;

CREATE TABLE IF NOT EXISTS public.avisos_salida (
    id BIGSERIAL PRIMARY KEY,
    suscripcion_id BIGINT NOT NULL REFERENCES public.suscripciones_push(id) ON DELETE CASCADE,
    horario_id INTEGER NOT NULL REFERENCES public.horarios(id) ON DELETE CASCADE,
    salida_en TIMESTAMPTZ NOT NULL,
    avisar_en TIMESTAMPTZ NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente'
        CHECK (estado IN ('pendiente', 'enviando', 'enviado', 'expirado', 'error')),
    reclamo_token TEXT,
    reclamo_hasta TIMESTAMPTZ,
    intentos INTEGER NOT NULL DEFAULT 0,
    reintentar_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    enviado_en TIMESTAMPTZ,
    ultimo_error TEXT,
    UNIQUE (suscripcion_id, horario_id, salida_en)
);
CREATE INDEX IF NOT EXISTS avisos_salida_pendientes_idx
    ON public.avisos_salida (avisar_en, reintentar_en)
    WHERE estado IN ('pendiente', 'enviando');

-- Sin políticas públicas: sólo el backend propietario lee las suscripciones.
ALTER TABLE public.favoritos_recorridos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.suscripciones_push ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.avisos_salida ENABLE ROW LEVEL SECURITY;

COMMIT;
