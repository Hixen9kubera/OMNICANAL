-- ═══════════════════════════════════════════════════════════════════════════
-- 0053 — ENRICH: la SALUD de cada publicación, en cualquier canal, con historia
--        (enrich.listing_health + enrich.listing_health_hist)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Estado: NO APLICADA en producción. Sandbox primero; producción solo con el sí
-- de Eduardo (doble candado).
--
-- Eduardo pidió (23-sep) guardar calidad y experiencia de compra CON HISTORIA
-- DIARIA y en tablas que sirvan igual para Walmart, Amazon, TikTok y Temu. Un
-- primer diseño solo de ML (`enrich.listing_quality`, llave `item_id`, columnas
-- con el vocabulario de ML, sin historia) vivió únicamente en el sandbox de la
-- rama feat/calidad-ml; sus 535 filas se copiaron aquí. Nunca existió en
-- producción ni en main.
--
-- FORMATO LARGO: UNA FILA POR (PUBLICACIÓN, MÉTRICA). Investigado el 23-sep con
-- la documentación oficial de cada canal: nadie ofrece lo mismo. ML da score
-- 0-100 + nivel + pendientes con liga; Walmart da score + subscores; Amazon no
-- da puntaje, solo issues ERROR/WARNING; TikTok da diagnósticos por campo;
-- Temu solo banderas de tráfico. Por eso `valor` y `nivel` son OPCIONALES y lo
-- propio de cada canal va en `detalle` (jsonb). Una métrica nueva (p. ej.
-- `trafico` de Temu) es una fila nueva con otro `metrica`, NO una migración.
--
-- `nivel` VA NORMALIZADO (bueno | medio | malo) para poder cruzar canales;
-- la etiqueta del canal viaja intacta en `nivel_canal` («Profesional», «Mala»).
-- Mapeo de ML: calidad good→bueno, medium→medio, bad→malo; experiencia
-- green→bueno, orange/yellow→medio, red→malo.
--
-- LOS TRES ESTADOS (lección de la 0041: «no pude preguntar» no es «no tiene»):
--   medida        el canal contestó con algo que decir (el check lo exige).
--   no_calculada  el canal contestó que NO la califica (ML: 400 «Only status
--                 active is supported»). Es una respuesta y se guarda, con el
--                 message en `motivo`.
--   sin_datos     el canal contestó pero aún no tiene con qué calificar (ML
--                 experiencia gray / value -1: sin ventas). NUNCA un 0.
-- Red caída, 401, 429 agotado o 5xx NO escriben fila: se reintenta la vuelta
-- siguiente, y una métrica que falló conserva su fila anterior intacta.
--
-- LA HISTORIA PESA POCO A PROPÓSITO. `listing_health_hist` guarda una fila por
-- (publicación, métrica, DÍA DE MÉXICO) con los escalares completos, pero el
-- jsonb (`pendientes`, `detalle`) solo cuando su `huella` cambia respecto al
-- día anterior guardado; los demás días va NULL. Medido: 1,057 activas × 2
-- métricas/día ≈ 770k filas/año de ~200 B, ~80 MB/año con los 5 canales en vez
-- de 544 MB. Sin particionado ni retención por ahora; si hace falta, retención
-- con pg_cron como `ops.purgar_webhook_events` (0050).
--
-- UN SOLO ESCRITOR: `services/calidad_ml.py`, en UNA transacción por tanda
-- (upsert de cada métrica aquí + upsert del día en la historia).
-- Dos procesos escribiendo la misma fila es como nació el aleteo de
-- `is_fulfillment` en channel.listings; aquí no se repite.
--
-- `account_id` sigue la convención de channel.listings (uuid a core.accounts);
-- el escritor lo resuelve desde `core.accounts.legacy_code` una vez por barrido.
--
-- NUMERACIÓN: la última en esta rama es 0052 (limpieza, preparada). Verificar
-- contra origin/main con fetch antes de fusionar: la rama de Accesos también
-- usa 0051/0052. Los números repetidos no rompen el runner (orden por nombre).
--
-- Idempotente: `create table if not exists` con las constraints dentro (si la
-- tabla ya existe, la sentencia entera se salta), índices `if not exists`, y
-- `comment`/`grant`/`enable rls` se pueden repetir sin daño. Verificado
-- corriéndolo DOS VECES seguidas en el sandbox.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ───────────────────────────────────────────────────────────────────────────
-- 1) enrich.listing_health — lo ÚLTIMO de cada (publicación, métrica)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists enrich.listing_health (
    canal         text         not null references core.channels(id),
    account_id    uuid         not null references core.accounts(id),
    listing_id    text         not null,              -- con lo que el canal califica: MLM…, SKU en Amazon, id de TikTok/Temu/Walmart
    metrica       text         not null,              -- 'calidad' | 'experiencia' | …
    estado        text         not null,              -- 'medida' | 'no_calculada' | 'sin_datos'
    valor         numeric(5,2),                       -- 0..100 si el canal da número
    nivel         text,                               -- normalizado: bueno | medio | malo
    nivel_canal   text,                               -- la etiqueta del canal, tal cual
    n_pendientes  smallint,                           -- null = la métrica no tiene pendientes
    pendientes    jsonb        not null default '[]'::jsonb,
    detalle       jsonb        not null default '{}'::jsonb,
    motivo        text,                               -- message del canal si no_calculada/sin_datos
    medido_en     timestamptz,                        -- cuándo lo calculó el CANAL
    capturado_en  timestamptz  not null default now(),-- cuándo preguntamos nosotros
    constraint listing_health_pkey
        primary key (canal, account_id, listing_id, metrica),
    constraint listing_health_metrica_chk
        check (metrica ~ '^[a-z][a-z_]{1,39}$'),
    constraint listing_health_estado_chk
        check (estado in ('medida', 'no_calculada', 'sin_datos')),
    constraint listing_health_valor_chk
        check (valor between 0 and 100),
    constraint listing_health_nivel_chk
        check (nivel in ('bueno', 'medio', 'malo')),
    constraint listing_health_n_pendientes_chk
        check (n_pendientes >= 0),
    constraint listing_health_pendientes_chk
        check (jsonb_typeof(pendientes) = 'array'),
    constraint listing_health_detalle_chk
        check (jsonb_typeof(detalle) = 'object'),
    -- «medida» tiene que traer algo que decir: número, nivel o pendientes.
    -- «medida» tiene que decir algo, pero no siempre con número o nivel: las
    -- banderas de Temu o las tasas de posventa de Walmart viven en `detalle`.
    constraint listing_health_medida_chk
        check (estado <> 'medida' or valor is not null or nivel is not null
               or n_pendientes is not null or detalle <> '{}'::jsonb)
);

-- Los conteos y filtros de la pantalla («cuántas malas en calidad de ML»). La
-- PK ya sirve `listing_id = any(...)` por (canal, account_id) y el escritor.
create index if not exists listing_health_canal_metrica_nivel_ix
    on enrich.listing_health (canal, metrica, nivel);

comment on table enrich.listing_health is
  'Salud de cada publicación por métrica (calidad, experiencia de compra, …) en '
  'cualquier canal: UNA fila por (canal, account_id, listing_id, metrica), la '
  'última medición. Formato largo: valor y nivel son opcionales y lo propio del '
  'canal va en detalle. Un error de red/401/429/5xx no escribe fila. Escritor '
  'único: services/calidad_ml.py. La serie diaria vive en listing_health_hist.';
comment on column enrich.listing_health.canal is
  'core.channels(id): mercado_libre, walmart, amazon, tiktok, temu.';
comment on column enrich.listing_health.account_id is
  'core.accounts(id), convención de channel.listings. El escritor lo resuelve '
  'desde core.accounts.legacy_code (BEKURA, SANCORFASHION, …).';
comment on column enrich.listing_health.listing_id is
  'El identificador con el que el CANAL califica la publicación: el item MLM… en '
  'ML; en Amazon el SKU del vendedor y NO el ASIN (los issues son por SKU y un '
  'ASIN puede tener varios SKUs: 1,825 publicaciones sobre 1,396 ASIN el 23-sep); '
  'el id propio en TikTok/Temu/Walmart.';
comment on column enrich.listing_health.metrica is
  'Qué se mide: calidad | experiencia hoy; una nueva es una fila nueva, sin DDL. '
  'Minúsculas y guion bajo, 2 a 40 caracteres.';
comment on column enrich.listing_health.estado is
  'medida (el canal contestó con algo que decir) | no_calculada (el canal dice '
  'que no la califica, p. ej. ML 400 «Only status active is supported») | '
  'sin_datos (el canal aún no tiene con qué calificar, p. ej. experiencia gray '
  'de ML). sin_datos NUNCA es un 0: valor y nivel van NULL.';
comment on column enrich.listing_health.valor is
  '0..100 cuando el canal da número (ML: score de calidad, reputation.value de '
  'experiencia). NULL si el canal no da número o no la calificó.';
comment on column enrich.listing_health.nivel is
  'Nivel NORMALIZADO para cruzar canales: bueno | medio | malo. ML calidad '
  'good/medium/bad; ML experiencia green, orange o yellow, red.';
comment on column enrich.listing_health.nivel_canal is
  'La etiqueta del canal tal cual («Profesional», «Estándar», «Buena», «Mala»).';
comment on column enrich.listing_health.n_pendientes is
  'Cuántas cosas pide corregir el canal. NULL si la métrica no tiene pendientes '
  '(experiencia) o si el canal no la calificó.';
comment on column enrich.listing_health.pendientes is
  'Lista normalizada de lo que el canal pide corregir, en su orden: '
  '[{clave, titulo, accion, link, grupo, severidad}]; un campo ausente es que el '
  'canal no lo da. En ML calidad, grupo = clave del bucket y cada pendiente va '
  'UNA sola vez aquí (no dentro de detalle.grupos).';
comment on column enrich.listing_health.detalle is
  'Lo propio del canal y la métrica. ML calidad: {level, level_wording, '
  'user_product_id, grupos:[{clave, tipo, titulo, score, estado, n_variables}]}. '
  'ML experiencia: {color, consecuencia, accion_principal, razon, '
  'recomendaciones, ia, por_categoria, status_ml, status_texto}.';
comment on column enrich.listing_health.motivo is
  'Por qué no_calculada o sin_datos, con el message del canal cuando lo manda.';
comment on column enrich.listing_health.medido_en is
  'Cuándo calculó el CANAL la métrica (ML calculated_at), si lo dice. No '
  'confundir con capturado_en.';
comment on column enrich.listing_health.capturado_en is
  'Cuándo preguntamos nosotros. Una métrica que falló en la vuelta no lo toca.';

-- ───────────────────────────────────────────────────────────────────────────
-- 2) enrich.listing_health_hist — una fila por (publicación, métrica, día)
-- ───────────────────────────────────────────────────────────────────────────
-- Con llave foránea a channels y accounts: son tablas chicas y estables, no
-- frenan la escritura. SIN llave a listing_health a propósito: la historia no
-- se debe ir si algún día se limpia la fila actual.
create table if not exists enrich.listing_health_hist (
    canal         text         not null references core.channels(id),
    account_id    uuid         not null references core.accounts(id),
    listing_id    text         not null,
    metrica       text         not null,
    dia           date         not null,              -- día de MÉXICO
    estado        text         not null,
    valor         numeric(5,2),
    nivel         text,
    nivel_canal   text,
    n_pendientes  smallint,
    huella        text         not null,              -- md5(pendientes::text || detalle::text)
    pendientes    jsonb,                              -- NULL salvo que cambie la huella
    detalle       jsonb,                              -- NULL salvo que cambie la huella
    capturado_en  timestamptz  not null,
    constraint listing_health_hist_pkey
        primary key (canal, account_id, listing_id, metrica, dia),
    constraint listing_health_hist_metrica_chk
        check (metrica ~ '^[a-z][a-z_]{1,39}$'),
    constraint listing_health_hist_estado_chk
        check (estado in ('medida', 'no_calculada', 'sin_datos')),
    constraint listing_health_hist_valor_chk
        check (valor between 0 and 100),
    constraint listing_health_hist_nivel_chk
        check (nivel in ('bueno', 'medio', 'malo')),
    constraint listing_health_hist_n_pendientes_chk
        check (n_pendientes >= 0),
    -- `detalle` NULL = igual que el día anterior guardado (ver huella).
    constraint listing_health_hist_medida_chk
        check (estado <> 'medida' or valor is not null or nivel is not null
               or n_pendientes is not null or detalle is null
               or detalle <> '{}'::jsonb),
    constraint listing_health_hist_huella_chk
        check (huella ~ '^[0-9a-f]{32}$'),
    -- El jsonb viaja en pareja: o se guardan los dos (cambió la huella) o
    -- ninguno (igual que el día anterior guardado).
    constraint listing_health_hist_jsonb_chk
        check ((pendientes is null) = (detalle is null)),
    constraint listing_health_hist_pendientes_chk
        check (jsonb_typeof(pendientes) = 'array'),
    constraint listing_health_hist_detalle_chk
        check (jsonb_typeof(detalle) = 'object')
);

-- «Todo lo de un día». La serie de una publicación la sirve la PK, y también
-- la lectura de la huella del día anterior (prefijo + dia desc).
create index if not exists listing_health_hist_dia_ix
    on enrich.listing_health_hist (dia);

comment on table enrich.listing_health_hist is
  'Serie diaria de enrich.listing_health: UNA fila por (canal, account_id, '
  'listing_id, metrica, dia), dia en hora de México. Los escalares van siempre; '
  'pendientes y detalle solo cuando la huella cambia respecto al día anterior '
  'guardado (los demás días NULL: la historia pesa ~80 MB/año y no 544). Si el '
  'mismo día se mide dos veces, la fila del día se reemplaza. Una métrica que '
  'falló no escribe historia ese día.';
comment on column enrich.listing_health_hist.dia is
  'Día de MÉXICO de la medición: (now() at time zone ''America/Mexico_City'')::date.';
comment on column enrich.listing_health_hist.huella is
  'md5(pendientes::text || detalle::text) de ese día, siempre presente. Si es '
  'igual a la del día anterior guardado, pendientes y detalle se guardan NULL.';
comment on column enrich.listing_health_hist.pendientes is
  'Copia de listing_health.pendientes SOLO el día en que cambió la huella; NULL '
  'los demás. Para reconstruir un día, tomar el último no NULL hasta esa fecha.';
comment on column enrich.listing_health_hist.detalle is
  'Copia de listing_health.detalle SOLO el día en que cambió la huella; NULL los '
  'demás. Viaja en pareja con pendientes.';
comment on column enrich.listing_health_hist.capturado_en is
  'Cuándo preguntamos (el capturado_en de la medición que quedó como la del día).';

-- ───────────────────────────────────────────────────────────────────────────
-- 3) El candado, donde nace el objeto (0016, 0049, 0051). Sin políticas: RLS
--    activa y 0 políticas = solo pasa quien hace bypass (service_role y
--    postgres). Lo revisa backend/scripts/verificar_rls.py.
-- ───────────────────────────────────────────────────────────────────────────
alter table enrich.listing_health enable row level security;
alter table enrich.listing_health_hist enable row level security;
grant all on enrich.listing_health to service_role;
grant all on enrich.listing_health_hist to service_role;

commit;

-- VERIFICACIÓN
--   select to_regclass('enrich.listing_health'),
--          to_regclass('enrich.listing_health_hist');                -- no null
--   select relname, relrowsecurity from pg_class
--    where oid in ('enrich.listing_health'::regclass,
--                  'enrich.listing_health_hist'::regclass);          -- true, true
--   select conrelid::regclass, contype, count(*) from pg_constraint
--    where conrelid in ('enrich.listing_health'::regclass,
--                       'enrich.listing_health_hist'::regclass)
--    group by 1, 2 order by 1, 2;
--     -- listing_health:      c 8, f 2, p 1
--     -- listing_health_hist: c 10, f 2, p 1
--   select has_table_privilege('service_role', 'enrich.listing_health_hist',
--                              'insert');                              -- true
--   python backend/scripts/verificar_rls.py                           -- exit 0
--   Idempotencia: correr el archivo dos veces seguidas; la segunda no falla.
-- REVERSA (antes, revertir el código que las lee y el barrido que las escribe)
--   drop table enrich.listing_health_hist;
--   drop table enrich.listing_health;
--   -- nada depende de ellas; lo actual se re-siembra con el job o con
--   -- calidad_ml_sandbox.py, la historia perdida NO se recupera.
