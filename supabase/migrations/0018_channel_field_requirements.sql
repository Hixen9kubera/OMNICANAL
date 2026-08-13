-- ═══════════════════════════════════════════════════════════════════════════
-- 0018 — Qué EXIGE cada canal, como datos.
--
-- Estado: APLICADA en producción (tukwcvsi) el 12-ago-2026, tras verificarse en
-- sandbox. El encabezado decía "NO APLICADA" y quedó rancio; se corrige el
-- 13-ago porque contradecía a producción y alguien podría "arreglar" el número
-- renumerando una migración ya aplicada — lo que desincroniza el ledger.
--
-- OJO CON EL NÚMERO: hay dos archivos `0018` (éste y
-- `0018_market_search_results_visitas.sql`) y también dos `0004`. NO rompe
-- nada: `aplicar_migraciones.py:93` aplica con `sorted(glob("*.sql"))` — orden
-- alfabético por nombre COMPLETO, no por número. No renumerar.
--
-- QUÉ RESUELVE
-- ------------
-- La pregunta del panel: "para el SKU X y el canal Y, ¿qué campos faltan?".
-- Hoy es incontestable — no porque falte el contenido (eso lo resolvió la
-- 0016), sino porque **no existe en ningún lado la lista de qué pide cada
-- canal**. Se buscó en las 16 migraciones: cero.
--
-- Es la tercera pata de las tres que estaban revueltas:
--   1. lo que el canal EXIGE      -> ESTA TABLA (no existía)
--   2. lo que NOSOTROS tenemos    -> enrich.channel_content   (0016)
--   3. lo que YA SE MANDÓ         -> ops.channel_submissions  (0001)
--
-- ═══════════════════════════════════════════════════════════════════════════
-- LAS TRES DECISIONES DE DISEÑO, Y POR QUÉ
-- ═══════════════════════════════════════════════════════════════════════════
--
-- (A) `campo` GUARDA EL NOMBRE NATIVO DEL CANAL, y `campo_canonico` lo traduce.
--
--     La regla de la casa es un concepto, un nombre. Pero un requisito NO es un
--     concepto nuestro: es lo que la API del canal contesta, y se lee de ahí con
--     SU vocabulario (`item_name`, `productName`, `goodsName`). Forzar el nombre
--     canónico al leerlo perdería la trazabilidad contra el esquema real.
--
--     Así que se guardan los dos: el nativo tal como vino, y el canónico cuando
--     hay equivalencia. La comparación contra el contenido usa el CANÓNICO.
--
--     Beneficio que no se buscaba: el mapeo queda AUDITABLE. Es lo que habría
--     hecho visible que Amazon declaraba `country_of_origin="MX"` por un camino
--     y `"CN"` por el otro durante meses (ver CAMPOS_POR_CANAL.md §6.1) — hoy
--     cada publicador decide por su cuenta y nadie compara.
--
-- (B) `default_value`: hay TRES estados, no dos.
--
--     El encargo modela `obligatorio: bool`. Pero el código muestra que ~7
--     campos de Amazon (`condition_type="new_new"`, `included_components=
--     "1 x Producto"`…) SIEMPRE se llenan con una constante escrita dentro del
--     publicador. Sin distinguirlos, el panel los pintaría en rojo para los
--     22,186 SKUs.
--
--       está en el contenido               -> verde
--       falta PERO hay default_value       -> "lo ponemos nosotros"
--       falta, sin default, y obligatorio  -> rojo
--
--     Y de paso `default_value` es el primer lugar donde esas constantes viven
--     como DATO COMPARABLE en vez de dispersas en cada publicador.
--
-- (C) `leido_at`: sin sello, el modelo miente en verde.
--
--     Los canales cambian sus plantillas sin avisar. Si Temu agrega mañana un
--     obligatorio y nadie relee, el panel seguiría diciendo "no le falta nada" y
--     las publicaciones rebotarían sin explicación. Con el sello, el panel puede
--     decir "requisitos sin verificar" — que es distinto de "está completo".
--
-- ═══════════════════════════════════════════════════════════════════════════

-- ── El diccionario canónico ────────────────────────────────────────────────
-- Sembrado desde lo que el panel YA maneja (`routers/publicar.py::CamposPublicar`
-- y las llaves de `enrich.channel_content.contenido`), NO desde documentación.
-- El encargo hablaba de `precio` y `dimensiones`; el código dice
-- `precio_regular` y `largo/ancho/alto/peso`. Manda el código.
create table core.canonical_fields (
  campo text primary key,
  tipo  text not null,
  label text not null,
  nota  text
);

insert into core.canonical_fields (campo, tipo, label, nota) values
  ('titulo',         'texto', 'Título',        'Límite por canal: ML 60, Amazon 75, Walmart 200'),
  ('descripcion',    'texto', 'Descripción',   'En ML va en llamada aparte, no en el item'),
  ('highlights',     'texto', 'Item Highlights','Amazon. Se generaba y se tiraba hasta v0.108.0'),
  ('bullets',        'lista', 'Bullet Points', 'Amazon: hasta 5'),
  ('atributos',      'lista', 'Atributos',     'Los obligatorios dependen de la CATEGORÍA'),
  -- Entra por lo que reportó el cargador de Amazon: es OBLIGATORIO en los 12
  -- tipos cargados, sale de un dato del producto (no es constante) y hoy NO
  -- tiene dónde editarse. 3,060 de 7,264 productos traen atributo BRAND; los
  -- otros 4,204 se publican en Amazon como "Generic" y en ML como "Ferrahome".
  -- Que los canales usen criterios distintos es decisión de negocio y NO la
  -- resuelve esta tabla; lo que sí resuelve es que el dato tenga dónde vivir.
  ('brand',          'texto', 'Marca',         'Amazon: obligatorio. ML publica todo como Ferrahome; Amazon cae a Generic sin BRAND'),
  ('precio_regular', 'numero','Precio regular','NO el de oferta: en variables el padre viene vacío'),
  ('peso',           'numero','Peso (kg)',     'Walmart: máximo 2 decimales'),
  ('largo',          'numero','Largo (cm)',    'Walmart: máximo 2 decimales'),
  ('ancho',          'numero','Ancho (cm)',    'Walmart: máximo 2 decimales'),
  ('alto',           'numero','Alto (cm)',     'Walmart: máximo 2 decimales'),
  ('sku',            'texto', 'SKU',           'Llave: une todos los canales'),
  ('stock',          'numero','Stock',         'Woo es la fuente de verdad'),
  ('imagenes',       'lista', 'Imágenes',      'Walmart exige 2; TikTok y Temu rehospedan'),
  ('categoria_id',   'texto', 'Categoría',     'Tipo de dato DISTINTO en cada canal');


-- ── Los requisitos ─────────────────────────────────────────────────────────
create table channel.field_requirements (
  canal        text not null references core.channels(id),

  -- '*' = aplica a TODO el canal. No es null porque forma parte de la PK y en
  -- Postgres dos nulls no colisionan: se colarían duplicados.
  -- Sin esto habría que escribir los 5 comunes en cada una de las 1,937 hojas
  -- de TikTok — ~10,000 filas para decir cinco cosas.
  --
  -- PRECEDENCIA: la fila de la CATEGORÍA ESPECÍFICA gana sobre la de '*'.
  -- La PK permite que el mismo campo exista en las dos (p. ej. `atributos`
  -- obligatorio en general pero no en una categoría), y sin esta regla la
  -- consulta devolvería las dos filas sin decir cuál manda. Es la misma forma
  -- de la regla 2 de la casa: lo específico gana sobre lo general.
  -- La consulta de referencia del final la aplica con `distinct on`.
  categoria_id text not null default '*',

  -- El nombre NATIVO del canal, tal como lo contesta su API. Sin FK: cada canal
  -- inventa los suyos.
  campo        text not null,

  -- La traducción a nuestro vocabulario. NULL cuando el campo es propio del
  -- canal y no tiene equivalencia (`condition_type` de Amazon no es un concepto
  -- que el panel maneje). La comparación contra el contenido usa ESTA columna.
  campo_canonico text references core.canonical_fields(campo),

  obligatorio  boolean not null default false,

  -- OJO con la precedencia: cuando `campo_canonico` NO es null, el tipo
  -- autoritativo es el de `core.canonical_fields` — éste puede repetirlo y los
  -- dos podrían contradecirse. Aquí `tipo` manda SOLO para los campos nativos
  -- sin equivalencia canónica, que son los que no están en el diccionario.
  tipo         text,

  valores_permitidos jsonb,      -- listas cerradas (Temu manda pid+vid)

  -- Qué ponemos nosotros cuando el producto no lo trae. NULL = no hay respaldo.
  default_value jsonb,

  -- De dónde salió esta fila. Sin esto no se distingue lo MEDIDO de lo supuesto.
  -- Con CHECK porque es la convención del esquema para enums cerrados
  -- (core.usuarios.rol, enrich.ai_content.estado): un typo silencioso aquí
  -- haría pasar por 'api' algo que alguien escribió a mano.
  fuente       text not null default 'api'
               check (fuente in ('api', 'codigo', 'manual')),

  -- Cuándo se leyó por última vez del canal. Si envejece, el panel dice
  -- "requisitos sin verificar", NO "no le falta nada".
  leido_at     timestamptz,
  updated_at   timestamptz not null default now(),

  primary key (canal, categoria_id, campo)
);

create index idx_field_req_canal on channel.field_requirements (canal, obligatorio);
create index idx_field_req_canonico on channel.field_requirements (campo_canonico);

comment on table channel.field_requirements is
  'Qué exige cada canal, por categoría. Se compara contra '
  'enrich.channel_content para contestar "¿qué le falta a este SKU?". '
  'NO es lo que tenemos (eso es channel_content) ni lo que mandamos '
  '(ops.channel_submissions).';

comment on column channel.field_requirements.campo is
  'Nombre NATIVO del canal (item_name, productName…). El canónico va en '
  'campo_canonico: guardar los dos deja auditable el mapeo.';

comment on column channel.field_requirements.default_value is
  'Lo que ponemos cuando el producto no lo trae. Es lo que evita pintar en '
  'rojo los campos que siempre se llenan solos — y el primer lugar donde las '
  'constantes de los publicadores viven como dato comparable.';

comment on column channel.field_requirements.leido_at is
  'Última lectura del canal. Si envejece, el panel debe decir "sin verificar", '
  'no "completo": los canales cambian sus plantillas sin avisar.';

alter table core.canonical_fields enable row level security;
alter table channel.field_requirements enable row level security;
grant all on core.canonical_fields to service_role;
grant all on channel.field_requirements to service_role;


-- ═══════════════════════════════════════════════════════════════════════════
-- ESTA MIGRACIÓN NO SIEMBRA REQUISITOS. A PROPÓSITO.
--
-- Las filas se llenan con un cargador por canal que LEE DEL CANAL (Amazon:
-- /definitions/2020-09-01/productTypes/{tipo}; ML: /categories/{id}/attributes;
-- TikTok: /product/202309/categories/{id}/attributes). Sembrarlas a mano aquí
-- sería escribir del lado de la documentación en vez del código — el error que
-- este trabajo lleva todo el día evitando (Walmart 3.19 vs 3.11 en producción,
-- `WALMART_MX` vs `WALMART_MEXICO`).
--
-- Temu y Shein NO se modelan todavía: cero código en el repo, así que cualquier
-- fila sería adivinanza.
--
-- ── TRAMPA PARA QUIEN ESCRIBA EL CARGADOR DE ML ────────────────────────────
-- `ia_generadores.GENERADORES["mercado_libre"]` lista un generador con
-- `id="ficha"` ("Ficha técnica"). **`ficha` NO es una llave de contenido**: es
-- el id del BOTÓN, y lo que produce cae en `atributos`. Verificado — el
-- frontend nunca lee `campos.ficha`; el resultado de la IA solo trae
-- titulo/descripcion/atributos/highlights/bullets. No le busques canónico.
--
-- ── LO QUE ESTA TABLA NO VA A CONTESTAR, Y ES A PROPÓSITO ──────────────────
-- Contesta "¿está el campo?" (presencia), no "¿está BIEN?" (validez). Los
-- límites medidos —título 60 en ML y 75 en Amazon, 2 decimales en Walmart, 2
-- imágenes mínimo— son POR CANAL, y `canonical_fields.nota` es compartida entre
-- canales: no puede decir "60 aquí, 75 allá". `valores_permitidos` cubre enums
-- cerrados, no longitudes ni rangos.
--
-- NO se agrega una columna `restricciones` hoy porque nadie la llenaría, y una
-- columna que nace vacía es como nacieron `core.products.parent_sku` y
-- `has_variations` (74 de 292 filas falsas en Inmovilizado por usarlas
-- creyéndolas vivas). Se agrega cuando exista quien la escriba y quien la lea.
-- Queda dicho para que la ausencia sea una decisión y no un olvido.
-- ═══════════════════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--   select count(*) from core.canonical_fields;              -- 14
--   select count(*) from channel.field_requirements;         -- 0 al inicio
--
--   -- La consulta que pinta el panel ("¿qué le falta?").
--   --
--   -- DOS COSAS QUE NO SE PUEDEN OMITIR, y que una primera versión de este
--   -- comentario omitía (las cazó el consejo antes de que nadie las copiara):
--   --
--   -- 1. `c.cuenta = :cuenta` en el JOIN. `enrich.channel_content` tiene la
--   --    cuenta en la PK por el caso EST-0091: el MISMO sku es dos productos
--   --    distintos según la cuenta de ML. Sin filtrarla, si BEKURA tiene el
--   --    campo y SANCORFASHION no, el join encuentra la fila de BEKURA y el
--   --    panel pinta VERDE un campo que a la otra cuenta le falta.
--   --
--   -- 2. `distinct on` para la precedencia: el mismo campo puede tener fila en
--   --    '*' y en la categoría. Gana la específica.
--   --
--   -- 3. La precedencia se resuelve ANTES de filtrar por `obligatorio`, en un
--   --    CTE aparte. Una primera versión metía `and r.obligatorio` en el mismo
--   --    WHERE que el `distinct on`, y eso descartaba la fila de la categoría
--   --    ESPECÍFICA cuando decía `obligatorio=false` — justo el caso que la
--   --    precedencia existe para resolver. Sobrevivía la de '*' y el campo
--   --    salía como faltante. Medido en sandbox, no razonado.
--
--   with efectivo as (
--     select distinct on (campo) *
--       from channel.field_requirements
--      where canal = :canal
--        and categoria_id in ('*', :categoria)
--      order by campo, (categoria_id <> '*') desc   -- específica gana a '*'
--   )
--   select e.campo, e.campo_canonico, e.default_value
--     from efectivo e
--     left join enrich.channel_content c
--            on c.sku = :sku and c.canal = :canal and c.cuenta = :cuenta
--           and c.contenido ? e.campo_canonico
--    where e.obligatorio
--      and c.sku is null;
--
--   -- Las que traen default_value son "lo ponemos nosotros", no "falta".
--
-- ROLLBACK
--   drop table channel.field_requirements;
--   drop table core.canonical_fields;
-- ═══════════════════════════════════════════════════════════════════════════
