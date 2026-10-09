-- ═══════════════════════════════════════════════════════════════════════════
-- 0034 — Dónde vive la caja compartida de un SKU.
--
-- ⚠️ NACIÓ COMO 0033 Y SE RENUMERÓ. Se aplicó a producción el 26-ago-2026
-- llamándose `0033_costing_caja_compartida.sql`; ese mismo día otra rama
-- publicó `0033_ops_odoo_sale_orders.sql` en main. Como esa llegó primero al
-- tronco, esta pasó a 0034. La TABLA no cambió: `costing.caja_compartida` ya
-- existe en kubera desde antes del renombre. Solo cambió el nombre del
-- archivo.
--
-- Estado: APLICADA. Sandbox (yvootpbz) 26-ago-2026, 26/26 incluida la
-- re-corrida; producción (kubera) 26-ago-2026 con el sí de Eduardo, 11/11
-- dentro de la transacción antes de confirmar — entre ellas que
-- `costos_validados` no se movió (15,838 filas y 20 columnas, antes y después).
-- Verificada después desde una conexión nueva: la tabla existe y está vacía.
--
-- Revisada por el consejo (claude-opus, claude-sonnet, claude-haiku); reportes
-- en `agents/counselors/1787794750-wtcaja/`.
--
-- QUÉ RESUELVE
-- ------------
-- Al validar un costeo, el equipo rastrea si el SKU viajó compartiendo una caja
-- master con otros productos del mismo embarque, y con qué renglones del packing
-- list. Ese dato explica de dónde salió el flete prorrateado de la pieza.
--
-- Hoy se reconstruye a mano cada vez. El parser YA lo calcula
-- (`packing_parser`: `comparte_caja`, `tam_grupo`, `piezas_grupo`) y el Resolver
-- lo muestra en pantalla — "caja compartida con el renglón 563, 564, 565" —,
-- pero vive en la memoria del job y se pierde al cerrar. Esta tabla es el lugar
-- donde puede quedarse.
--
-- ESTO ES SOLO EL ESPACIO. No hay proceso, ni escritor, ni cambio de pantalla:
-- la tabla nace vacía y se llena a mano o por quien se decida después.
--
-- POR QUÉ NO ES UNA COLUMNA EN `costos_validados`
-- ------------------------------------------------
-- Esa tabla tiene PK `(sku)` — UNA fila por SKU — y su columna `contenedor` es
-- singular: un SKU que llega en dos embarques sobrescribe y conserva el último.
-- Una columna al lado heredaría ese límite y acabaría describiendo un embarque
-- que ya no es el que produjo el costo vigente. Evidencia que se vuelve mentira
-- sin que nadie lo note, que es peor que no tenerla: invita a confiar.
--
-- Y el fondo del asunto: compartir caja NO es un atributo del producto. Es un
-- hecho de UN EMBARQUE. El mismo SKU puede venir solo en un contenedor y
-- acompañado en el siguiente. Por eso el contenedor va en la llave.
--
-- ESO ÚLTIMO NO ES UNA SUPOSICIÓN, y tampoco se puede comprobar aquí: lo
-- confirmó Eduardo el 26-ago-2026 — un mismo SKU SÍ llega en más de un
-- contenedor. Se intentó medir por `cost_history`, que guarda el snapshot
-- completo de la fila previa: 0 SKUs con más de un contenedor. Pero hay 96
-- filas de historia para 15,838 SKUs, así que ese cero no prueba nada.
--
-- Y es que NO SE PUEDE medir desde la base: con PK `(sku)` el segundo embarque
-- pisa al primero, o sea que el esquema está hecho de forma que este hecho no
-- se recupera de los datos. Esa es justo la razón de la tabla — si la evidencia
-- fuera columna, el primer embarque se perdería EN SILENCIO, y la pantalla
-- mostraría los renglones de un packing list junto a un costo que salió de otro.
--
-- ⚠️ NO SE JUNTA CONTRA `costos_validados.contenedor` — ESA COLUMNA ESTÁ MUERTA
-- ----------------------------------------------------------------------------
-- La versión anterior de este archivo documentaba el join
-- `c.contenedor = v.contenedor`. Está MAL y se corrigió. Medido el 26-ago-2026:
--
--   · `costing_mirror.upsert_validados` — el único escritor vivo hacia kubera —
--     solo toca sku, largo, alto, ancho, peso, costo_producto, costo_cbm,
--     costo_total. `contenedor` NO está.
--   · Un grep de INSERT/UPDATE con `contenedor` en todo el backend: CERO.
--   · El único código que la escribió fue `etl_core_products.py`, RETIRADO con
--     candado duro desde el 27-jul; su reemplazo v2 ni siquiera lee la columna.
--   · El Resolver sí captura contenedores nuevos, pero `packing_comparador`
--     escribe por pymysql al MySQL congelado, no a kubera.
--
-- O sea: `costing.costos_validados.contenedor` es una foto del backfill de
-- julio. Un embarque nuevo NUNCA la mueve. Juntar contra ella haría invisible
-- justo la evidencia nueva. La lectura correcta va al pie de este archivo.
--
-- POR QUÉ HAY UN `contenedor_base` GENERADO
-- ------------------------------------------
-- `packing_comparador` lo dice desde su línea 8: en `costos_validados` los
-- contenedores llevan un sufijo de embarque (`MRKU4831449 - 88`) y el packing
-- list solo trae el código pelón, así que el match exacto devuelve cero filas
-- siempre. Medido en producción: 15,151 de 15,348 filas (98.7%) traen sufijo.
--
-- Si la llave fuera el texto crudo, quien capture copiando el código del archivo
-- crearía una fila que no junta con nada — y quien capture con sufijo crearía
-- una SEGUNDA fila del mismo embarque. `contenedor_base` normaliza en la BD, sin
-- depender de que la persona acierte, y es quien manda en la llave primaria.
--
-- La normalización es reversible y no pierde nada: 102 códigos distintos → 102
-- bases distintas, y CERO contenedores con más de un sufijo (medido 26-ago). El
-- texto crudo se conserva en `contenedor` como constancia de lo que se capturó.
--
-- POR QUÉ `archivo` ES NOT NULL
-- ------------------------------
-- `renglones` son números de fila de un Excel (`fila_excel` en packing_parser),
-- relativos a UN archivo. Un `{563,564,565}` sin saber de qué documento no
-- significa nada: el renglón 564 ¿de cuál? Y el código ya documenta que un mismo
-- contenedor puede llegar en dos tandas con dos packing lists. Sin esta
-- restricción, el dato central de la tabla es ininterpretable.
--
-- POR QUÉ `cbm_origen` TIENE UN VALOR QUE EL PARSER NUNCA PRODUCE
-- ---------------------------------------------------------------
-- En `packing_parser` la columna "Total Volume" del Excel GANA antes de que se
-- evalúe la caja compartida:
--
--     if cbm_total_fila > 0 and piezas > 0:      -> 'total_volume'
--     elif cbm_grupo > 0 and piezas_grupo > 0:   -> 'caja_compartida' | 'caja_propia'
--     else:                                      -> 'sin_datos'
--
-- Un renglón puede tener `comparte_caja = true` y un costo que NO le debe NADA
-- al prorrateo del grupo. Guardar que comparte caja sin guardar de dónde salió
-- el CBM es guardar la conclusión sin el método.
--
-- Pero los cuatro valores de arriba son RESULTADOS DEL PARSER, y aquí nadie va a
-- correr el parser. Una persona capturando en una tabla llamada `caja_compartida`
-- teclearía 'caja_compartida' porque es lo que el nombre sugiere, no porque lo
-- haya medido — y quedaría una adivinanza con cara de dato duro. Por eso existe
-- 'no_parseado', que es el DEFAULT: la captura a mano dice la verdad por
-- omisión y solo miente si alguien se esfuerza.
--
-- LO QUE ESTA LLAVE NO DISTINGUE
-- -------------------------------
-- PK `(sku, contenedor_base)`: si un proveedor parte el mismo SKU en DOS cajas
-- master dentro del mismo embarque, las dos se colapsan en una fila y
-- `renglones` queda con la unión. Se aceptó a propósito — para "con quién
-- comparte caja este SKU" la unión es la respuesta útil, y el caso es raro. Si
-- algún día hace falta la granularidad por caja: agregar `fila_cabecera int` (el
-- renglón que encabeza el grupo, que es lo que identifica una caja master en el
-- parser) y meterla a la PK.
--
-- POR QUÉ SE QUEDA CON EL NOMBRE `caja_compartida`
-- -------------------------------------------------
-- Es una imprecisión: una fila con un solo renglón NO comparte con nadie (se
-- guarda igual, para distinguir "no comparte" de "no se ha revisado"). Dos de
-- los tres revisores propusieron renombrarla. Se conserva porque es la palabra
-- que el equipo ya usa en el Resolver y en la pantalla, y el vocabulario
-- compartido con quien va a capturar vale más que la pureza del nombre.
--
-- QUÉ **NO** HACE
-- ---------------
-- No toca `costos_validados`. No cambia ningún costo, ninguna fórmula y ningún
-- precio. No lee ni escribe nada. Una tabla nueva, vacía.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

create table if not exists costing.caja_compartida (
  -- FK sin cascade: `costos_validados` tampoco lo lleva, y `cost_history` va
  -- derecho SIN FK porque es historia que puede nombrar SKUs muertos. Esto es
  -- evidencia de un embarque que ya ocurrió: debe BLOQUEAR el borrado del
  -- producto y forzar una decisión, no desaparecer callada — sobre todo en F8,
  -- que es una fase de podar. (`on delete set null` es imposible: la columna es
  -- NOT NULL y parte de la llave.)
  --
  -- Ojo con lo que esta FK NO garantiza: `core.products` trae 6,237 de 22,340
  -- SKUs con forma de identificador provisional (medido 26-ago), así que
  -- aceptaría un `0031-0004` sin chistar. El guardián contra provisionales es
  -- `packing_comparador.es_provisional`, no esta restricción.
  sku             citext  not null references core.products(sku) on delete restrict,

  -- El código tal cual se capturó, con sufijo de embarque o sin él.
  contenedor      text    not null check (btrim(contenedor) <> ''),

  -- El mismo código sin el sufijo " - NN". Es la llave real: ver el bloque de
  -- arriba. Misma normalización que `packing_comparador.normalizar_contenedor`,
  -- pero en la BD y determinista.
  contenedor_base text
    generated always as (regexp_replace(btrim(contenedor), '\s*-\s*\d+\s*$', '')) stored,

  -- El packing list del que salieron los renglones. NOT NULL: sin él, los
  -- números de renglón no se pueden interpretar.
  archivo         text    not null check (btrim(archivo) <> ''),

  -- Los renglones del packing list que comparten la caja master, el propio
  -- incluido. Array y no texto: se puede preguntar sin parsear una cadena, y no
  -- hay formato que discutir al capturar.
  -- `cardinality` y NO `array_length(...,1)`: para un array vacío array_length
  -- devuelve NULL, y un CHECK que evalúa a NULL PASA. Medido en el sandbox: con
  -- array_length, un '{}' entraba sin ruido. cardinality devuelve 0 y sí lo
  -- rechaza.
  renglones       int[]   not null
    check (cardinality(renglones) >= 1
           and array_ndims(renglones) = 1
           and array_position(renglones, null) is null),

  -- Con qué OTROS SKUs la comparte — el propio NO va aquí. (Queda definido de
  -- una vez: con 200 filas capturadas bajo criterios distintos ya no se
  -- reconstruye.) Nulo si todavía no se identificaron: el renglón se conoce
  -- siempre, el SKU del vecino no.
  --
  -- `text[]` y NO `citext[]`, aunque `sku` sí sea citext: psycopg2 no conoce el
  -- tipo citext, así que un `citext[]` vuelve a Python como la CADENA CRUDA
  -- '{A,B}' en vez de una lista. Medido en el sandbox el 26-ago. Un array no
  -- puede llevar FK de todos modos, así que citext solo daría una ilusión de
  -- rigor referencial que no existe.
  skus_grupo      text[],

  piezas_grupo    numeric(12,2) check (piezas_grupo is null or piezas_grupo >= 0),
  cbm_grupo       numeric(14,6) check (cbm_grupo    is null or cbm_grupo    >= 0),

  -- 'no_parseado' es el default a propósito: ver el bloque de arriba.
  cbm_origen      text not null default 'no_parseado'
    check (cbm_origen in ('total_volume', 'caja_compartida', 'caja_propia',
                          'sin_datos', 'no_parseado')),

  nota            text,

  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),

  -- CON default, al revés que `revisado_por` en 0032. Allá el argumento era que
  -- un insert de costeo NO es una revisión; aquí cada insert SÍ es un acto de
  -- captura, así que aplica el patrón de 0029 (`ops.process_log.actor`).
  registrado_por  text default nullif(current_setting('app.usuario', true), ''),

  primary key (sku, contenedor_base)
);

comment on table costing.caja_compartida is
  'Evidencia de que un SKU viajó compartiendo caja master en un embarque, y con '
  'qué renglones del packing list. Es un hecho del EMBARQUE, no del producto: '
  'por eso la llave lleva contenedor. Nace vacía; no la escribe ningún proceso. '
  'NO se junta contra costos_validados.contenedor — esa columna no tiene '
  'escritor vivo desde el 27-jul-2026.';

comment on column costing.caja_compartida.contenedor_base is
  'El código de contenedor sin el sufijo de embarque " - NN". Es la llave real: '
  'el packing list trae el código pelón y costos_validados lo guarda sufijado '
  '(98.7% de las filas), así que el match exacto contra el texto crudo falla.';

comment on column costing.caja_compartida.renglones is
  'Renglones del packing list que comparten la caja master, incluido el propio, '
  'relativos al archivo nombrado en `archivo`. Un solo elemento = no comparte '
  'con nadie (fila guardada de todos modos, para poder distinguir "no comparte" '
  'de "no se ha revisado").';

comment on column costing.caja_compartida.cbm_origen is
  'De dónde salió el CBM por pieza en packing_parser. total_volume = la columna '
  'del Excel ganó y el grupo NO participó en el costo, aunque comparta caja. '
  'no_parseado (el default) = lo capturó una persona sin correr el parser: NO '
  'adivinar uno de los otros cuatro valores a mano.';

-- El contenedor completo de un vistazo. Va sobre la base normalizada, que es
-- con lo que se busca.
create index if not exists idx_caja_compartida_contenedor
  on costing.caja_compartida (contenedor_base);

-- Regla de la casa: deny-by-default en todo lo nuevo (0022/0025). Sin políticas,
-- anon y authenticated no leen nada; el backend entra con service_role.
alter table costing.caja_compartida enable row level security;

-- `create trigger` no admite `if not exists`, así que sin este drop la
-- migración revienta en una re-corrida — y el aplicador del sandbox pasa todos
-- los archivos en orden. Mismo patrón que 0002:42.
drop trigger if exists trg_touch_caja_compartida on costing.caja_compartida;
create trigger trg_touch_caja_compartida before update on costing.caja_compartida
  for each row execute function core.fn_touch_updated_at();

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- CÓMO SE CAPTURA (referencia; no lo hace ningún proceso hoy)
-- ═══════════════════════════════════════════════════════════════════════════
-- `contenedor` se captura como venga — el código pelón del packing list o el
-- sufijado de costos_validados. `contenedor_base` normaliza y es quien manda,
-- así que las dos formas caen en la MISMA fila y el upsert corrige en vez de
-- duplicar.
--
--   insert into costing.caja_compartida
--          (sku, contenedor, archivo, renglones, skus_grupo, piezas_grupo, cbm_origen)
--   values ('MUE-0163-TEL', 'MRKU4831449', 'PACKING LIST MRKU4831449.xlsx',
--           '{563,564,565,566,567}', '{ORG-0319-PLA,KIT-0514-EST}', 240,
--           'no_parseado')
--   on conflict (sku, contenedor_base) do update
--      set contenedor = excluded.contenedor,
--          archivo = excluded.archivo,
--          renglones = excluded.renglones,
--          skus_grupo = excluded.skus_grupo,
--          piezas_grupo = excluded.piezas_grupo,
--          cbm_origen = excluded.cbm_origen,
--          nota = excluded.nota,
--          registrado_por = nullif(current_setting('app.usuario', true), '');
--
-- ═══════════════════════════════════════════════════════════════════════════
-- CÓMO SE LEE desde la pantalla de Costos
-- ═══════════════════════════════════════════════════════════════════════════
-- Por SKU y NADA MÁS. No se filtra por contenedor contra `costos_validados`:
-- esa columna es un fósil sin escritor vivo (ver el bloque de arriba), así que
-- ese filtro escondería justo la evidencia de los embarques nuevos.
--
--   left join lateral (
--     select c.contenedor, c.renglones, c.skus_grupo, c.cbm_origen, c.archivo
--       from costing.caja_compartida c
--      where c.sku = p.sku
--      order by c.created_at desc
--      limit 1
--   ) caja on true
--
-- Y si se quieren TODOS los embarques del SKU, que es lo honesto cuando hay más
-- de uno, la consulta es directa:
--
--   select contenedor, archivo, renglones, skus_grupo, cbm_origen
--     from costing.caja_compartida
--    where sku = %(sku)s
--    order by created_at desc;
--
-- Buscar quién más iba en un renglón (la tabla es chica; esto es un barrido, no
-- un índice — `= any(...)` NO usa GIN, solo `@>`, `<@` y `&&` lo harían):
--
--   select sku, contenedor, renglones from costing.caja_compartida
--    where contenedor_base = 'MRKU4831449' and renglones @> array[564];
--
-- REVERSIÓN COMPLETA
--   drop table if exists costing.caja_compartida;
-- ═══════════════════════════════════════════════════════════════════════════
