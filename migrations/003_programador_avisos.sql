-- Sólo Supabase. Antes: aplicar 002 y guardar bsred_notification_cron_token
-- en Supabase Vault con el mismo NOTIFICATION_CRON_TOKEN de Render.
-- No contiene claves ni crea servicios de pago.
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

CREATE OR REPLACE FUNCTION public.bsred_disparar_avisos_salida(
    instante TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
) RETURNS BIGINT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $function$
DECLARE
    credencial TEXT;
    solicitud BIGINT;
BEGIN
    -- El trabajo se revisa cada minuto, pero sólo despierta Render cuando hay
    -- una salida favorita con dispositivos activos y un aviso próximo.
    -- Dos minutos de margen permiten iniciar un servicio gratuito suspendido.
    IF NOT EXISTS (
        SELECT 1 FROM public.favoritos_recorridos f
        JOIN public.suscripciones_push s ON s.usuario_id = f.usuario_id AND s.activa
        JOIN public.horarios h ON h.id = f.horario_id
        CROSS JOIN generate_series(0, 1) AS desplazamiento(dia)
        CROSS JOIN LATERAL (
            SELECT (instante AT TIME ZONE 'America/Santiago')::date + desplazamiento.dia AS fecha,
                   regexp_replace(translate(lower(trim(h.dias)), 'áéíóúüñ', 'aeiouun'), '\s+', ' ', 'g') AS dias
        ) calendario
        CROSS JOIN LATERAL (
            SELECT (calendario.fecha + h.salida) AT TIME ZONE 'America/Santiago' AS base
        ) hora
        CROSS JOIN LATERAL (
            -- Durante el retroceso de hora, usar la primera ocurrencia igual
            -- que zoneinfo en el despachador, en lugar de retrasar el aviso.
            SELECT CASE WHEN (hora.base - INTERVAL '1 hour') AT TIME ZONE 'America/Santiago'
                              = calendario.fecha + h.salida
                        THEN hora.base - INTERVAL '1 hour' ELSE hora.base END AS salida_en,
                   extract(isodow FROM calendario.fecha)::integer AS dia_semana,
                   ARRAY['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo'] AS nombres,
                   array_remove(regexp_split_to_array(calendario.dias, '[\s,;]+'), 'y') AS dias_lista
        ) salida
        WHERE lower(trim(h.origen)) = 'panguipulli'
          AND lower(trim(h.tipo)) IN ('salida', 'salidas')
          AND h.salida IS NOT NULL
          AND (salida.salida_en AT TIME ZONE 'America/Santiago')::date = calendario.fecha
          AND (salida.salida_en AT TIME ZONE 'America/Santiago')::time = h.salida
          AND salida.salida_en > instante
          AND salida.salida_en - make_interval(mins => f.anticipacion_min::integer)
                <= instante + INTERVAL '2 minutes'
          AND (
              calendario.dias IN ('diario', 'todos los dias', 'todos', 'lunes a domingo')
              OR (
                  calendario.dias ~ '^(lunes|martes|miercoles|jueves|viernes|sabado|domingo) a (lunes|martes|miercoles|jueves|viernes|sabado|domingo)$'
                  AND (salida.dia_semana - array_position(salida.nombres, split_part(calendario.dias, ' a ', 1)) + 7) % 7
                      <= (array_position(salida.nombres, split_part(calendario.dias, ' a ', 2))
                          - array_position(salida.nombres, split_part(calendario.dias, ' a ', 1)) + 7) % 7
              )
              OR (cardinality(salida.dias_lista) > 0 AND salida.dias_lista <@ salida.nombres
                  AND salida.nombres[salida.dia_semana] = ANY(salida.dias_lista))
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.avisos_salida a WHERE a.suscripcion_id = s.id
              AND a.horario_id = h.id AND a.salida_en = salida.salida_en
              AND (a.estado IN ('enviado', 'error') OR a.enviado_en IS NOT NULL)
          )
    ) THEN
        RETURN NULL;
    END IF;

    SELECT decrypted_secret INTO credencial FROM vault.decrypted_secrets
    WHERE name = 'bsred_notification_cron_token' LIMIT 1;
    IF credencial IS NULL OR length(credencial) < 32 THEN
        RAISE EXCEPTION 'Falta configurar la credencial privada del programador BSRed';
    END IF;

    SELECT net.http_post(
        url := 'https://bsred.onrender.com/api/interno/avisos-salida',
        headers := jsonb_build_object('Content-Type', 'application/json',
                                     'Authorization', 'Bearer ' || credencial),
        body := '{}'::jsonb,
        timeout_milliseconds := 60000
    ) INTO solicitud;
    RETURN solicitud;
END;
$function$;

-- Sólo el propietario/cron puede invocarla; no es un RPC público.
REVOKE ALL ON FUNCTION public.bsred_disparar_avisos_salida(TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.bsred_disparar_avisos_salida(TIMESTAMPTZ) FROM anon, authenticated;

-- cron.schedule con el mismo nombre actualiza su programación sin duplicarla.
SELECT cron.schedule('bsred-avisos-salida', '* * * * *',
                     'SELECT public.bsred_disparar_avisos_salida();');
