-- Reemplaza sólo el gate del programador ya instalado por 003.
-- Requiere 005, 006 y 007. No crea trabajos cron ni modifica la credencial Vault.
CREATE OR REPLACE FUNCTION public.bsred_disparar_avisos_salida(
    instante TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
) RETURNS BIGINT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $function$
DECLARE
    credencial TEXT;
    solicitud BIGINT;
    hay_trabajo BOOLEAN;
BEGIN
    WITH dispositivos AS (
        SELECT 'web'::text AS canal, id, usuario_id, NULL::text AS vinculo_id
        FROM public.suscripciones_push WHERE activa
        UNION ALL
        SELECT 'android'::text, id, usuario_id, vinculo_id
        FROM public.dispositivos_android WHERE activo AND vinculo_id <> ''
    ), destinatarios AS (
        SELECT d.canal, d.id AS dispositivo_id, d.usuario_id, d.vinculo_id,
               f.horario_id, f.anticipacion_min, h.empresa_id, h.tipo, h.origen,
               h.salida, h.dias
        FROM dispositivos d
        JOIN public.usuarios u ON u.id=d.usuario_id AND NOT u.suspendido AND NOT u.requiere_cambio_password
        JOIN public.favoritos_recorridos f ON f.usuario_id=d.usuario_id
        JOIN public.horarios h ON h.id=f.horario_id AND h.activo
        JOIN public.empresas e ON e.id=h.empresa_id AND e.estado_validacion='aprobada'
    ), salidas AS (
        SELECT d.*, salida.salida_en
        FROM destinatarios d
        CROSS JOIN generate_series(0,1) AS desplazamiento(dia)
        CROSS JOIN LATERAL (
            SELECT (instante AT TIME ZONE 'America/Santiago')::date + desplazamiento.dia AS fecha,
                   regexp_replace(translate(lower(trim(d.dias)), 'áéíóúüñ', 'aeiouun'), '\s+', ' ', 'g') AS dias
        ) calendario
        LEFT JOIN public.horario_excepciones x ON x.horario_id=d.horario_id AND x.fecha=calendario.fecha
        CROSS JOIN LATERAL (
            SELECT COALESCE(x.salida,d.salida) AS hora_salida,
                   extract(isodow FROM calendario.fecha)::integer AS dia_semana,
                   ARRAY['lunes','martes','miercoles','jueves','viernes','sabado','domingo'] AS nombres,
                   array_remove(regexp_split_to_array(calendario.dias,'[\s,;]+'),'y') AS dias_lista
        ) servicio
        CROSS JOIN LATERAL (
            SELECT (calendario.fecha + servicio.hora_salida) AT TIME ZONE 'America/Santiago' AS base
        ) hora
        CROSS JOIN LATERAL (
            -- zoneinfo usa la primera ocurrencia durante el retroceso de hora.
            SELECT CASE WHEN (hora.base - INTERVAL '1 hour') AT TIME ZONE 'America/Santiago'
                              = calendario.fecha + servicio.hora_salida
                        THEN hora.base - INTERVAL '1 hour' ELSE hora.base END AS salida_en
        ) salida
        WHERE lower(trim(d.origen))='panguipulli' AND lower(trim(d.tipo)) IN ('salida','salidas')
          AND servicio.hora_salida IS NOT NULL
          AND (salida.salida_en AT TIME ZONE 'America/Santiago')::date=calendario.fecha
          AND (salida.salida_en AT TIME ZONE 'America/Santiago')::time=servicio.hora_salida
          AND salida.salida_en>instante AND salida.salida_en<=instante+INTERVAL '1 day'
          AND salida.salida_en-make_interval(mins=>d.anticipacion_min::integer)<=instante+INTERVAL '2 minutes'
          AND COALESCE(x.opera,
              calendario.dias IN ('diario','todos los dias','todos','lunes a domingo')
              OR (calendario.dias ~ '^(lunes|martes|miercoles|jueves|viernes|sabado|domingo) a (lunes|martes|miercoles|jueves|viernes|sabado|domingo)$'
                  AND (servicio.dia_semana-array_position(servicio.nombres,split_part(calendario.dias,' a ',1))+7)%7
                      <= (array_position(servicio.nombres,split_part(calendario.dias,' a ',2))
                          -array_position(servicio.nombres,split_part(calendario.dias,' a ',1))+7)%7)
              OR (cardinality(servicio.dias_lista)>0 AND servicio.dias_lista<@servicio.nombres
                  AND servicio.nombres[servicio.dia_semana]=ANY(servicio.dias_lista)))
    )
    SELECT EXISTS (
        SELECT 1 FROM salidas s WHERE
        (s.canal='web' AND NOT EXISTS (
            SELECT 1 FROM public.avisos_salida a
            WHERE a.suscripcion_id=s.dispositivo_id AND a.horario_id=s.horario_id AND a.salida_en=s.salida_en
              AND (a.estado IN ('enviado','error') OR a.enviado_en IS NOT NULL OR a.intentos>=6
                   OR (a.estado='expirado' AND a.reintentar_en>=a.salida_en)
                   OR (a.estado='pendiente' AND a.intentos>0 AND a.reintentar_en>instante)
                   OR (a.estado='enviando' AND (a.reclamo_hasta IS NULL OR a.reclamo_hasta>instante)))
        )) OR (s.canal='android' AND NOT EXISTS (
            SELECT 1 FROM public.entregas_notificaciones q
            WHERE q.canal='android' AND q.dispositivo_android_id=s.dispositivo_id
              AND q.event_id='salida:'||s.horario_id::text||':'||floor(extract(epoch FROM s.salida_en))::bigint::text
              AND (q.usuario_id<>s.usuario_id OR q.estado IN ('enviado','error') OR q.enviado_en IS NOT NULL OR q.intentos>=6
                   OR (q.estado='expirado' AND q.reintentar_en>=q.expira_en)
                   OR (q.estado='pendiente' AND q.intentos>0 AND q.reintentar_en>instante)
                   OR (q.estado='enviando' AND (q.reclamo_hasta IS NULL OR q.reclamo_hasta>instante))
                   OR (q.payload ? 'binding_id' AND q.payload->>'binding_id' IS DISTINCT FROM s.vinculo_id))
        ))
    ) OR EXISTS (
        -- Entregas operativas ya preparadas: sólo pendientes o leases vencidas.
        SELECT 1 FROM public.entregas_notificaciones q
        JOIN destinatarios d ON d.usuario_id=q.usuario_id AND d.horario_id=q.horario_id AND d.canal=q.canal
          AND d.dispositivo_id=CASE WHEN q.canal='web' THEN q.suscripcion_id ELSE q.dispositivo_android_id END
        JOIN public.avisos_operativos a ON a.id=q.aviso_id AND a.horario_id=d.horario_id AND a.empresa_id=d.empresa_id
        WHERE q.expira_en>instante AND a.expira_en>instante AND q.disponible_en<=instante
          AND q.enviado_en IS NULL AND q.intentos<6
          AND ((q.estado='pendiente' AND q.reintentar_en<=instante)
               OR (q.estado='enviando' AND q.reclamo_hasta<=instante))
          AND (q.canal<>'android' OR NOT q.payload ? 'binding_id'
               OR q.payload->>'binding_id'=d.vinculo_id)
    ) OR EXISTS (
        -- El primer aviso aún no tiene cola si Render estaba suspendido.
        -- Sólo despertarlo cuando exista al menos un destinatario sin entrega.
        SELECT 1 FROM public.avisos_operativos a
        JOIN destinatarios d ON d.horario_id=a.horario_id AND d.empresa_id=a.empresa_id
        WHERE a.expira_en>instante AND a.creado_en<=instante
          AND a.fecha<=(instante AT TIME ZONE 'America/Santiago')::date
          AND NOT EXISTS (
              SELECT 1 FROM public.entregas_notificaciones q
              WHERE q.canal=d.canal AND q.event_id='operativo:'||a.id::text
                AND CASE WHEN d.canal='web' THEN q.suscripcion_id ELSE q.dispositivo_android_id END=d.dispositivo_id
          )
    ) INTO hay_trabajo;

    IF NOT hay_trabajo THEN
        RETURN NULL;
    END IF;
    SELECT decrypted_secret INTO credencial FROM vault.decrypted_secrets
    WHERE name='bsred_notification_cron_token' LIMIT 1;
    IF credencial IS NULL OR length(credencial)<32 THEN
        RAISE EXCEPTION 'Falta configurar la credencial privada del programador BSRed';
    END IF;
    SELECT net.http_post(
        url:='https://bsred.onrender.com/api/interno/avisos-salida',
        headers:=jsonb_build_object('Content-Type','application/json','Authorization','Bearer '||credencial),
        body:='{}'::jsonb, timeout_milliseconds:=10000
    ) INTO solicitud;
    RETURN solicitud;
END;
$function$;
REVOKE ALL ON FUNCTION public.bsred_disparar_avisos_salida(TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.bsred_disparar_avisos_salida(TIMESTAMPTZ) FROM anon, authenticated;
