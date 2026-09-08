-- ═══════════════════════════════════════════════════════════════════════════
-- 0048 — «no encontramos nada» y «no nos dejaron ver» dejan de ser lo mismo.
--
-- EL CASO QUE LA PIDE. `casco integral moto` se corrió CINCO veces y las cinco
-- terminó igual: `resultados = 0`, que la pantalla enseña como «este SKU no
-- tiene competencia directa». El registro de Apify decía otra cosa:
--
--     url:       listado.mercadolibre.com.mx/casco-integral-moto
--     loadedUrl: mercadolibre.com.mx/gz/account-verification?go=…
--     retryCount: 8   ·   errorMessages: ["BLOQUEADO", …×9]
--
-- ML no contestó «no hay»: contestó el muro de login, 45 veces seguidas. En la
-- MISMA corrida y con el mismo proxy, «tenis hombre» trajo 48 resultados, y
-- «casco para moto» —mismas palabras, misma categoría— trajo 10. O sea que el
-- cero no era del mercado, era nuestro, y la pantalla lo estaba presentando
-- como un hecho del mercado.
--
-- POR QUÉ UNA COLUMNA Y NO INFERIRLO. Porque `resultados = 0` es exactamente el
-- mismo valor en los dos casos y no hay de dónde sacar la diferencia después:
-- el motivo sólo se conoce en el instante del raspado, mirando el `#error` que
-- devuelve Apify. Si no se guarda ahí, se pierde.
--
-- LOS TRES ESTADOS, y qué significa cada uno para quien lee la pantalla:
--   'ok'        · se raspó y trajo filas.
--   'vacio'     · se raspó bien y ML no tiene nada. ESTE sí es un hecho del
--                 mercado y se puede decir «no hay competencia directa».
--   'bloqueado' · ML nos mandó a verificarnos. No sabemos qué hay.
--
-- NULL = las 1,058 filas que ya estaban medidas antes de esta migración. No se
-- inventa su estado: `resultados > 0` es claramente 'ok', pero hoy NINGUNA fila
-- tiene 0 (porque el caller se saltaba el guardado), así que no hay nada
-- ambiguo que rellenar y el backfill sería adivinar. Se deja NULL a propósito y
-- se llena solo con la próxima medición de cada término.
--
-- BLINDAJE: `enrich.market_search_term` ya tiene RLS y su grant desde la 0025;
-- agregar una columna no cambia eso. No se toca ninguna vista, así que tampoco
-- aplica el despojo de `security_invoker` de la 0042→0045.
--
-- Idempotente: se puede correr dos veces sin daño.
-- ═══════════════════════════════════════════════════════════════════════════

alter table enrich.market_search_term
  add column if not exists estado text;

do $$
begin
  if not exists (
    select 1 from pg_constraint
     where conname = 'market_search_term_estado_ck'
       and conrelid = 'enrich.market_search_term'::regclass
  ) then
    alter table enrich.market_search_term
      add constraint market_search_term_estado_ck
      check (estado is null or estado in ('ok', 'vacio', 'bloqueado'));
  end if;
end $$;

comment on column enrich.market_search_term.estado is
  'Cómo terminó la ÚLTIMA medición: ok | vacio | bloqueado. Separa «ML no tiene '
  'nada» (vacio, hecho del mercado) de «ML no nos dejó ver» (bloqueado, problema '
  'nuestro). NULL = medido antes de la 0048, sin registro del motivo.';

-- Para el vigilante: «¿cuántos términos llevamos sin poder ver?» no debe
-- barrer 1,828 filas. Parcial, porque los bloqueados son la minoría.
create index if not exists market_search_term_bloqueados_ix
  on enrich.market_search_term (canal, medido_en)
  where estado = 'bloqueado';
