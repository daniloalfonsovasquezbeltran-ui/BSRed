-- Preparar antes del backend nuevo. No fuerza cambios en cuentas existentes.
BEGIN;
ALTER TABLE public.usuarios ADD COLUMN IF NOT EXISTS requiere_cambio_password BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS public.password_reset_tokens (
    token_hash CHAR(64) PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES public.usuarios(id) ON DELETE CASCADE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expira_en TIMESTAMPTZ NOT NULL,
    usado_en TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS password_reset_usuario_idx ON public.password_reset_tokens(usuario_id);
CREATE INDEX IF NOT EXISTS password_reset_expira_idx ON public.password_reset_tokens(expira_en);

CREATE TABLE IF NOT EXISTS public.limites_cuentas (
    bucket_hash CHAR(64) PRIMARY KEY,
    intentos INTEGER NOT NULL CHECK (intentos > 0),
    expira_en TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS limites_cuentas_expira_idx ON public.limites_cuentas(expira_en);

ALTER TABLE public.password_reset_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.limites_cuentas ENABLE ROW LEVEL SECURITY;
COMMIT;
