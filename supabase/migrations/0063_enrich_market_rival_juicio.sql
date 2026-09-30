-- ═══════════════════════════════════════════════════════════════════════════
-- 0063 — enrich.market_rival_juicio: ¿este «rival» compite de verdad con
--        NUESTRO producto? Y el historial de términos de búsqueda que se
--        probaron para encontrar mejores rivales. 30-sep-2026.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Estado: aplicada SOLO en el SANDBOX (30-sep-2026). En producción NO: llevarla
-- allá es su propia acta de doble candado. Mientras tanto el código pregunta
-- con to_regclass antes de leer o escribir y, sin tabla, se comporta como si
-- el juez no existiera.
--
-- Editada DESPUÉS de aplicarla en el sandbox (revisión del 30-sep): el estado
-- `sin_candidato` en el check, el uso de `ronda` y la regla del título por
-- periodo. Como no está en producción, se corrigió el archivo en vez de abrir
-- otra migración; el sandbox se pone al día con un delta aparte (idempotente).
--
-- (El número salta la 0062 a propósito: hay una migración de otro frente, sin
-- publicar, que la va a tomar. Hacer fetch y renumerar si al publicar chocara.)
--
-- POR QUÉ
-- La búsqueda de Mercado Libre devuelve lo que comparte PALABRAS con el
-- término, no lo que compite con el producto. Un SKU de zancos para yesero de
-- $2,166 tenía 10 «rivales» y 8 eran refacciones de $142 a $500: el «mínimo del
-- mercado» eran unas correas. En un examen de 774 rivales de 90 SKUs al azar
-- solo el 48 % era el mismo producto.
--
-- POR QUÉ UNA TABLA APARTE Y NO UNA COLUMNA EN LOS RESULTADOS DE BÚSQUEDA
--   · Esa tabla guarda una fila por (término, rival) y un término lo comparten
--     varios SKUs. El veredicto es de la PAREJA (SKU nuestro, rival): el mismo
--     rival puede ser «mismo» para un SKU y «otro paquete» para su hermano.
--   · Cada medición la borra y la reinserta completa: una columna ahí, o una
--     tabla hija con cascada, perdería en cada recaptura veredictos que ya se
--     pagaron.
--
-- SIN LLAVES FORÁNEAS, A PROPÓSITO
-- Ninguna de las dos tablas referencia a nadie. Así sobreviven a la recaptura
-- y al truncate en cascada con que se re-siembra el sandbox. El precio: no hay
-- limpieza automática (ver PURGA al pie).
--
-- LLAVE (sku, canal, externo_id), SIN EL TÉRMINO
-- Si el mismo rival reaparece bajo otro término —pasa: el conjunto de rivales
-- es estable entre búsquedas parecidas— el veredicto se reusa y no se vuelve a
-- pagar. termino_id queda solo como dato informativo (el del último que juzgó).
--
-- CUÁNDO DEJA DE VALER UN VEREDICTO
-- Se guarda el título nuestro y el del rival tal como se juzgaron. Si cualquiera
-- cambia, el veredicto NO está vigente: la vista lo da por no juzgado y el juez
-- lo vuelve a poner en la cola. La versión del prompt también re-juzga, pero
-- eso lo decide el código (la vista no la conoce).
--
-- QUIÉN ESCRIBE
--   market_rival_juicio    → solo backend/services/competencia_juez.py
--   market_termino_intento → solo backend/services/competencia_mejora.py
--
-- Idempotente: todo es «if not exists» o drop + create; se puede correr dos
-- veces seguidas sin daño.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ── 1. El veredicto por pareja ───────────────────────────────────────────────
create table if not exists enrich.market_rival_juicio (
  sku               citext      not null,
  canal             text        not null default 'mercado_libre',
  externo_id        text        not null,
  termino_id        bigint,
  clase             text        not null,
  unidades_nuestras integer,
  unidades_rival    integer,
  motivo            text,
  titulo_nuestro    text,
  titulo_rival      text,
  proveedor         text,
  modelo            text,
  version_prompt    text,
  juzgado_en        timestamptz not null default now(),
  constraint market_rival_juicio_pkey primary key (sku, canal, externo_id),
  constraint market_rival_juicio_clase_ck check
    (clase in ('mismo', 'otro_paquete', 'otra_gama', 'refaccion', 'otro_producto', 'dudoso')),
  constraint market_rival_juicio_un_ck check (unidades_nuestras is null or unidades_nuestras > 0),
  constraint market_rival_juicio_ur_ck check (unidades_rival is null or unidades_rival > 0)
);

alter table enrich.market_rival_juicio enable row level security;
grant all on enrich.market_rival_juicio to service_role;

comment on table enrich.market_rival_juicio is
  'Veredicto de un LLM por pareja (SKU nuestro, rival de la busqueda): si el rival '
  'sirve o no para comparar precio. Sin llaves foraneas a proposito: sobrevive a '
  'la recaptura de la busqueda. Solo cuenta como comparable la clase mismo.';
comment on column enrich.market_rival_juicio.clase is
  'mismo | otro_paquete | otra_gama | refaccion | otro_producto | dudoso. '
  'Comparable = solo mismo.';
comment on column enrich.market_rival_juicio.unidades_nuestras is
  'Unidades de NUESTRO paquete. Es un dato del SKU: todas sus filas llevan el mismo.';
comment on column enrich.market_rival_juicio.titulo_rival is
  'El titulo del rival TAL COMO SE JUZGO. Si el de la busqueda ya es otro, el '
  'veredicto no esta vigente.';
comment on column enrich.market_rival_juicio.titulo_nuestro is
  'Nuestro titulo tal como se juzgo (sale de la vista de titulo por SKU).';

-- ── 2. Términos candidatos probados por SKU ─────────────────────────────────
create table if not exists enrich.market_termino_intento (
  id                   bigint generated by default as identity primary key,
  sku                  citext      not null,
  canal                text        not null default 'mercado_libre',
  termino_anterior     text,
  termino_anterior_id  bigint,
  termino_candidato    text        not null,
  termino_candidato_id bigint,
  motivo               text,
  estado               text        not null default 'propuesto',
  ronda                integer     not null default 1,
  comparables_antes    integer,
  total_antes          integer,
  comparables_despues  integer,
  total_despues        integer,
  modelo               text,
  creado_en            timestamptz not null default now(),
  resuelto_en          timestamptz,
  resuelto_por         text,
  constraint market_termino_intento_uk unique (sku, canal, termino_candidato),
  constraint market_termino_intento_estado_ck check
    (estado in ('propuesto', 'medido', 'sin_mejora', 'bloqueado', 'error',
                'termino_ok', 'sin_candidato', 'aceptado', 'descartado'))
);

create index if not exists market_termino_intento_sku_ix
  on enrich.market_termino_intento (sku, canal, creado_en desc);

alter table enrich.market_termino_intento enable row level security;
grant all on enrich.market_termino_intento to service_role;

comment on table enrich.market_termino_intento is
  'Historial de terminos de busqueda candidatos por SKU. Sirve para no pagar dos '
  'veces el mismo candidato y para que una persona acepte la sugerencia.';
comment on column enrich.market_termino_intento.estado is
  'propuesto (reclamado, aun sin medir) | medido (es la SUGERENCIA abierta) | '
  'sin_mejora | bloqueado (muro de login; reintentable hasta la ronda 2, donde '
  'queda con resuelto_en y ya cuenta como probado) | error (la medicion o el juicio '
  'no terminaron, reintentable) | termino_ok (el termino actual es correcto) | '
  'sin_candidato (la IA no propuso nada nuevo que probar) | aceptado | descartado. '
  'termino_ok y sin_candidato llevan el termino ACTUAL como candidato.';
comment on column enrich.market_termino_intento.ronda is
  'Rondas que este candidato jugo DE VERDAD para este SKU, contando la que esta en '
  'curso. Cada reclamo suma una (recoger un propuesto huerfano no suma) y un fallo '
  'de NUESTRO lado (la tanda de Apify que truena, el tope de gasto, el proveedor '
  'caido) la devuelve: un error puede quedar en 0. En la ronda 2 un fallo del '
  'propio candidato (cero filas, muro, juicio que no contesta) ya es su respuesta: '
  'error pasa a sin_mejora y bloqueado se cierra.';

-- ── 3. El título de NUESTRO producto, uno solo por SKU ───────────────────────
-- Un SKU publicado en dos cuentas tiene dos títulos. El veredicto se emite
-- contra UNO, y tiene que ser siempre el mismo: si alternara entre corridas,
-- todas las parejas del SKU volverían a la cola y se volverían a pagar. Regla
-- fija: el título de Mercado Libre del periodo MÁS RECIENTE del SKU que tenga
-- título y, si en ese periodo hay dos cuentas, el de la primera (orden
-- alfabético); si no hay ninguno, el nombre del catálogo.
--
-- El periodo va PRIMERO. Ordenando antes por cuenta, una publicación cerrada
-- hace meses en la primera cuenta —o un SKU reciclado— le ganaba al título
-- vigente de la otra, y los rivales se juzgaban contra otro producto. El costo
-- de este orden: a inicio de mes, si una cuenta se captura antes que la otra
-- y sus títulos difieren, el título cambia una vez y esas parejas vuelven a la
-- cola una vez.
drop view if exists enrich.market_rival_comparable_v;
drop view if exists enrich.market_sku_titulo_v;

create view enrich.market_sku_titulo_v as
select v.sku,
       v.canal,
       coalesce(m.title, v.nombre) as titulo,
       v.categoria_nombre
  from enrich.market_skus_v v
  left join lateral (
        select lm.title
          from enrich.market_listing_metrics lm
         where lm.sku = v.sku
           and lm.canal = v.canal
           and coalesce(lm.title, '') <> ''
         order by lm.periodo desc, lm.cuenta
         limit 1
       ) m on true;

alter view enrich.market_sku_titulo_v set (security_invoker = on);
grant select on enrich.market_sku_titulo_v to service_role;
comment on view enrich.market_sku_titulo_v is
  'El titulo con el que se juzga cada SKU: el de Mercado Libre de su periodo mas '
  'reciente con titulo (la primera cuenta si hay dos) y, si no hay, el nombre del '
  'catalogo.';

-- ── 4. Rivales de cada SKU con su veredicto ──────────────────────────────────
-- LA definición de «comparable» para quien lea desde SQL. Es la misma regla
-- que competencia_juez.filas() en Python: si se toca una, se toca la otra.
--   vigente    = hay veredicto y los dos títulos siguen siendo los que se juzgaron
--   cuenta     = rival AJENO con precio (lo que entra a un conteo)
--   comparable = cuenta, vigente y clase mismo
-- Quien lea debe tratar «cuenta y no vigente» como PENDIENTE —no sé—, nunca
-- como no comparable: con pendientes no se puede afirmar cuántos rivales reales
-- tiene un SKU.
create view enrich.market_rival_comparable_v as
select cfg.sku,
       cfg.canal,
       cfg.termino_id,
       r.externo_id,
       r.posicion,
       r.titulo,
       r.precio,
       coalesce(r.es_nuestro, false) as es_nuestro,
       r.capturado_en,
       j.clase,
       j.motivo,
       j.unidades_nuestras,
       j.unidades_rival,
       j.juzgado_en,
       (j.sku is not null
          and j.titulo_rival is not distinct from r.titulo
          and j.titulo_nuestro is not distinct from t.titulo) as vigente,
       (not coalesce(r.es_nuestro, false) and coalesce(r.precio, 0) > 0) as cuenta,
       (not coalesce(r.es_nuestro, false) and coalesce(r.precio, 0) > 0
          and j.sku is not null
          and j.titulo_rival is not distinct from r.titulo
          and j.titulo_nuestro is not distinct from t.titulo
          and j.clase = 'mismo') as comparable
  from enrich.market_sku_config cfg
  join enrich.market_search_results r on r.termino_id = cfg.termino_id
  left join enrich.market_sku_titulo_v t on t.sku = cfg.sku and t.canal = cfg.canal
  left join enrich.market_rival_juicio j
    on j.sku = cfg.sku and j.canal = cfg.canal and j.externo_id = r.externo_id
 where cfg.canal = 'mercado_libre';

alter view enrich.market_rival_comparable_v set (security_invoker = on);
grant select on enrich.market_rival_comparable_v to service_role;
comment on view enrich.market_rival_comparable_v is
  'Rivales del termino asignado a cada SKU con su veredicto. comparable = rival '
  'ajeno con precio, veredicto vigente y clase mismo.';

commit;

-- ── VERIFICACIÓN (a mano, después de aplicar) ────────────────────────────────
--   select count(*) from enrich.market_rival_juicio;                   -- 0 al nacer
--   select count(*), count(*) filter (where cuenta) from enrich.market_rival_comparable_v;
--   select sku, count(*) from enrich.market_sku_titulo_v group by 1 having count(*) > 1;  -- 0 filas
--
-- ── PURGA (manual; no hay cascada que limpie) ────────────────────────────────
--   Veredictos de rivales que ya no aparecen en ninguna búsqueda del SKU y
--   tienen más de 90 días; términos candidatos sin SKU asignado cuyo intento
--   terminó en sin_mejora o descartado.
--
-- ── REVERSA ─────────────────────────────────────────────────────────────────
--   ANTES de los drop: apagar las banderas (COMPETENCIA_JUEZ_ENABLED,
--   _VISIBLE y _ESCRITURA) para que nada vuelva a escribir, y respaldar
--   market_termino_intento o correr primero la PURGA. Esa tabla es el ÚNICO
--   rastro de dos cosas que viven fuera de ella: qué términos del catálogo son
--   candidatos (nacen sin origen y, sin ella, no se distinguen de una búsqueda
--   suelta) y qué término tenía cada SKU antes de aceptar una sugerencia (el
--   aceptado queda como corrección 'manual'). Guardar el resultado de:
--     select sku, termino_anterior, termino_anterior_id, termino_candidato,
--            termino_candidato_id, estado, resuelto_en
--       from enrich.market_termino_intento;
--
--   drop view  if exists enrich.market_rival_comparable_v;
--   drop view  if exists enrich.market_sku_titulo_v;
--   drop table if exists enrich.market_termino_intento;
--   drop table if exists enrich.market_rival_juicio;
