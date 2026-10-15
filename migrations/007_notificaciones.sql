-- Tokens Android y avisos operativos privados. Migración repetible y aditiva.
BEGIN;
CREATE TABLE IF NOT EXISTS public.dispositivos_android (
    id BIGSERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES public.usuarios(id) ON DELETE CASCADE,
    dispositivo_hash CHAR(64) NOT NULL UNIQUE,
    token TEXT NOT NULL UNIQUE,
    vinculo_id TEXT NOT NULL DEFAULT gen_random_uuid()::text,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE public.dispositivos_android ADD COLUMN IF NOT EXISTS vinculo_id TEXT NOT NULL DEFAULT gen_random_uuid()::text;
CREATE INDEX IF NOT EXISTS dispositivos_android_usuario_idx
    ON public.dispositivos_android(usuario_id) WHERE activo;

CREATE TABLE IF NOT EXISTS public.avisos_operativos (
    id BIGSERIAL PRIMARY KEY,
    empresa_id INTEGER NOT NULL REFERENCES public.empresas(id) ON DELETE CASCADE,
    horario_id INTEGER NOT NULL REFERENCES public.horarios(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL CHECK (tipo IN ('retraso','cancelacion','informacion')),
    fecha DATE NOT NULL,
    demora_min SMALLINT CHECK (demora_min BETWEEN 1 AND 720),
    mensaje TEXT NOT NULL CHECK (char_length(mensaje) BETWEEN 5 AND 500),
    creado_por INTEGER REFERENCES public.usuarios(id) ON DELETE SET NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expira_en TIMESTAMPTZ NOT NULL,
    CHECK ((tipo='retraso' AND demora_min IS NOT NULL)
           OR (tipo<>'retraso' AND demora_min IS NULL)),
    CHECK (expira_en>creado_en)
);
CREATE INDEX IF NOT EXISTS avisos_operativos_vigentes_idx
    ON public.avisos_operativos(horario_id,fecha,expira_en);

CREATE TABLE IF NOT EXISTS public.entregas_notificaciones (
    id BIGSERIAL PRIMARY KEY,
    canal TEXT NOT NULL CHECK(canal IN ('web','android')),
    suscripcion_id BIGINT REFERENCES public.suscripciones_push(id) ON DELETE CASCADE,
    dispositivo_android_id BIGINT REFERENCES public.dispositivos_android(id) ON DELETE CASCADE,
    usuario_id INTEGER NOT NULL REFERENCES public.usuarios(id) ON DELETE CASCADE,
    horario_id INTEGER NOT NULL REFERENCES public.horarios(id) ON DELETE CASCADE,
    aviso_id BIGINT REFERENCES public.avisos_operativos(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL CHECK(char_length(event_id) BETWEEN 1 AND 160),
    salida_en TIMESTAMPTZ,
    expira_en TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    disponible_en TIMESTAMPTZ NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente'
        CHECK(estado IN ('pendiente','enviando','enviado','expirado','error')),
    reclamo_token TEXT,
    reclamo_hasta TIMESTAMPTZ,
    intentos INTEGER NOT NULL DEFAULT 0,
    reintentar_en TIMESTAMPTZ NOT NULL,
    enviado_en TIMESTAMPTZ,
    ultimo_error TEXT,
    CHECK((canal='web' AND suscripcion_id IS NOT NULL AND dispositivo_android_id IS NULL)
       OR (canal='android' AND dispositivo_android_id IS NOT NULL AND suscripcion_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS entregas_notificaciones_web_evento_idx
    ON public.entregas_notificaciones(suscripcion_id,event_id) WHERE canal='web';
CREATE UNIQUE INDEX IF NOT EXISTS entregas_notificaciones_android_evento_idx
    ON public.entregas_notificaciones(dispositivo_android_id,event_id) WHERE canal='android';
CREATE INDEX IF NOT EXISTS entregas_notificaciones_pendientes_idx
    ON public.entregas_notificaciones(disponible_en,reintentar_en)
    WHERE estado IN ('pendiente','enviando');
ALTER TABLE public.dispositivos_android ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.avisos_operativos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.entregas_notificaciones ENABLE ROW LEVEL SECURITY;
COMMIT;
