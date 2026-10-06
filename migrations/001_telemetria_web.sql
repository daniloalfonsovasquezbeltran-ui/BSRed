-- Ejecutar una vez antes de desplegar el código nuevo. Es repetible y aditiva.
BEGIN;

CREATE TABLE IF NOT EXISTS public.visitantes_web (
    visitante_hash CHAR(64) PRIMARY KEY,
    ultima_visita TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ultima_actividad TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS visitantes_web_ultima_visita_idx
    ON public.visitantes_web (ultima_visita);
CREATE INDEX IF NOT EXISTS visitantes_web_ultima_actividad_idx
    ON public.visitantes_web (ultima_actividad);

CREATE TABLE IF NOT EXISTS public.sesiones_web (
    token_hash CHAR(64) PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES public.usuarios(id) ON DELETE CASCADE,
    expira_en TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sesiones_web_expira_en_idx
    ON public.sesiones_web (expira_en);

-- Sin políticas públicas: sólo el backend propietario consulta estos registros.
ALTER TABLE public.visitantes_web ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sesiones_web ENABLE ROW LEVEL SECURITY;

COMMIT;
