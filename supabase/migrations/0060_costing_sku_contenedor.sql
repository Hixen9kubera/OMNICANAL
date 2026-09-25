-- ═══════════════════════════════════════════════════════════════════════════
-- 0060 — costing.sku_contenedor: en qué contenedor(es) de Kubera llegó cada
--        SKU, con la evidencia que lo respalda. 25-sep-2026.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Estado: aplicada SOLO en el SANDBOX (25-sep-2026). En producción NO: llevarla
-- allá es su propia acta de doble candado.
--
-- POR QUÉ UNA TABLA NUEVA Y NO costing.costos_validados.contenedor
-- Esa columna NO es esto y no se toca:
--   · guarda UNA sola N por SKU, y ≥140 SKUs llegaron legítimamente en dos
--     contenedores (reórdenes, los «-EST»). La PK de costos_validados es el sku:
--     el segundo contenedor no cabe;
--   · la LEE Costos (el filtro de contenedores) y la ESCRIBE «Validar
--     publicados» (packing_publicados.py): cambiar su significado movería lo
--     que ya funciona;
--   · trae el texto crudo del embarque («HPCU4441843 - 86»), no el número.
-- Aquí una fila = (sku, número de contenedor) con NIVEL de confianza, las
-- FUENTES que coinciden y su EVIDENCIA, para poder volver a ella.
--
-- DE DÓNDE SALE (análisis del 25-sep, aprobado por Eduardo)
-- Tres familias independientes que se cruzan por SKU, más un apoyo:
--   · ferraforme — el renglón de Ferraforme (costing.packing_ubicaciones, 0057);
--     la N sale del NOMBRE del archivo (services/embarques.py).
--   · costos     — el linaje de Brandon: costos_validados.contenedor
--     (+ caja_compartida).
--   · odoo_campo — product.template.container_numbers de Odoo, con código y
--     número (el código «pelón», sin número, es evidencia débil y no cuenta).
--   · odoo_oc    — el renglón RECIBIDO de la orden de compra. NO es familia
--     independiente (su N casi siempre se infirió de las otras): solo apoya, y
--     respalda un «multi» cuando DOCUMENTA esa N para ese SKU: su N no es la
--     mayoría del campo de Odoo de los otros SKUs, el Ferraforme de esa N no la
--     contradice, y no es el mismo recibo (misma cantidad) que otra OC de otra N.
-- Niveles: A = Ferraforme alineado, o ≥2 familias coinciden en la N. B = una
-- sola familia fuerte, sin los «refutados» (Odoo dice N, el Ferraforme de esa N
-- existe y no trae el SKU; sin excepción por OC). Dentro de un multi, B también
-- es la N cuyo ÚNICO documento es una OC que la documenta: esa fila no tiene
-- familia (evidencia.familias = []), el SKU sí la tiene en otra de sus N. Los
-- conflictos, refutados y provisionales («NNNN-NNNN») NO se cargan: van a la
-- lista de Brandon. Lo que se carga aunque el Ferraforme de su N no traiga el
-- SKU (costos sola es fuerte) lleva evidencia.contradicho_por.
--
-- QUIÉN LA ESCRIBE
-- Escritor único: scripts/ubicar_skus_contenedor.py (la lógica pura en
-- services/ubicar_contenedores.py). Reemplaza en una transacción las filas de
-- SU `origen` y no toca las de otro. Nadie más escribe aquí por ahora.
--
-- Sin ON DELETE CASCADE (Eduardo, 24-sep): la misma regla que las demás llaves
-- hacia core.products. Borrar un producto que tiene contenedor falla en vez de
-- llevarse la evidencia en silencio.
--
-- ⚠️ SANDBOX: backend/scripts/clonar_a_sandbox.py hace
-- `truncate core.products cascade`, y TRUNCATE … CASCADE sí alcanza esta tabla
-- por la FK: re-clonar core.products la VACÍA sin avisar. Después de re-clonar,
-- volver a correr `ubicar_skus_contenedor.py --aplicar`. (No se puede re-sembrar
-- desde producción: allá la tabla no existe todavía.)
--
-- Idempotente: `if not exists` en tabla e índice; comment/grant/rls se repiten
-- sin daño. RLS + grant en la MISMA migración (patrón 0053/0055/0057/0058).
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists costing.sku_contenedor (
  sku         citext      not null,
  numero      integer     not null,              -- el número de Kubera: «contenedor 80»
  codigo      text,                              -- código principal (ISO o guía), informativo
  nivel       text        not null,
  fuentes     text[]      not null,
  multi       boolean     not null default false,
  evidencia   jsonb       not null default '{}'::jsonb,
  origen      text        not null,              -- 'carga_inicial_2026-09' | 'manual' | …
  cargado_en  timestamptz not null default now(),
  constraint sku_contenedor_pkey primary key (sku, numero),
  -- NO ACTION a propósito (ver el encabezado).
  constraint sku_contenedor_sku_fkey foreign key (sku) references core.products (sku),
  constraint sku_contenedor_numero_chk check (numero > 0),
  constraint sku_contenedor_nivel_chk check (nivel in ('A', 'B')),
  -- Al menos una fuente y solo las cuatro conocidas: una etiqueta mal escrita
  -- truena la carga en vez de quedar como una fuente fantasma.
  constraint sku_contenedor_fuentes_chk check (
    cardinality(fuentes) > 0
    and fuentes <@ array['ferraforme', 'costos', 'odoo_campo', 'odoo_oc']::text[])
);

-- «Qué SKUs llegaron en el contenedor N». Por sku ya lo cubre la PK.
create index if not exists sku_contenedor_numero_idx
  on costing.sku_contenedor (numero);

-- El candado, donde nace el objeto. RLS activa y 0 políticas = solo pasa quien
-- hace bypass (service_role y postgres).
alter table costing.sku_contenedor enable row level security;
grant all on costing.sku_contenedor to service_role;

comment on table costing.sku_contenedor is
  'En qué contenedor(es) de Kubera llegó cada SKU, con nivel de confianza, fuentes y evidencia. '
  'Una fila por (sku, numero): un SKU puede llegar en varios. NO es costing.costos_validados.contenedor '
  '(una sola N por SKU, la usa Costos). Escritor único: scripts/ubicar_skus_contenedor.py.';
comment on column costing.sku_contenedor.sku is
  'SKU de Kubera (core.products). FK sin cascade: borrar el producto falla si tiene contenedor.';
comment on column costing.sku_contenedor.numero is
  'Número de contenedor de Kubera («contenedor 80»), el que da services/embarques.py a partir del código.';
comment on column costing.sku_contenedor.codigo is
  'Código principal del embarque (ISO del contenedor o guía). Informativo: la llave es numero.';
comment on column costing.sku_contenedor.nivel is
  'A: Ferraforme alineado, o dos o más familias independientes coinciden en la N. '
  'B: una sola familia fuerte (Ferraforme no alineado, costos sola u Odoo con número), sin refutados; '
  'dentro de un multi, también la N cuyo único documento es una OC recibida que la documenta '
  '(sin familia: evidencia.familias vacío).';
comment on column costing.sku_contenedor.fuentes is
  'Qué fuentes dan esta N: subconjunto de {ferraforme, costos, odoo_campo, odoo_oc}. '
  'odoo_oc (OC recibida) apoya, pero no cuenta como familia independiente para el nivel.';
comment on column costing.sku_contenedor.multi is
  'true: el SKU está en más de un contenedor y CADA N tiene documento primario '
  '(Ferraforme, costos u OC recibida que documenta esa N: su N no es la mayoría del campo de Odoo, '
  'el Ferraforme de esa N no la contradice y no repite el recibo de otra OC). '
  'Todas sus N se cargan con multi = true.';
comment on column costing.sku_contenedor.evidencia is
  'Con qué volver al papel: archivo y fila de Ferraforme, valor crudo de costos, texto y nombre del producto '
  'en Odoo, OC. contradicho_por: el Ferraforme de esa N existe y no trae el SKU (se cargó igual).';
comment on column costing.sku_contenedor.origen is
  'Qué carga escribió la fila (carga_inicial_2026-09, manual, …). El cargador solo usa orígenes carga_… '
  'y reemplaza solo las de su origen: manual es de personas y nunca lo borra.';
comment on column costing.sku_contenedor.cargado_en is
  'Cuándo se escribió la fila.';
