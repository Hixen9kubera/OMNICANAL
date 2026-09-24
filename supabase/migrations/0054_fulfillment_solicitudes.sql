-- ═══════════════════════════════════════════════════════════════════════════
-- 0054 — La SOLICITUD de FULL se guarda antes de que bodega la recorte
-- ═══════════════════════════════════════════════════════════════════════════
--
-- QUÉ GUARDA. Cada vez que «Crear FULL» (pestaña FULLFILMENT, v0.556.0) crea
-- la cotización en Odoo, una fila con lo que el panel SUGIRIÓ y lo que la
-- persona PIDIÓ por SKU, los parámetros con que se calculó (cobertura, mínimo
-- por SKU…), lo que Odoo no tenía (recortes) y las órdenes que nacieron.
--
-- POR QUÉ HACE FALTA UNA TABLA (Brandon pidió que se justificara, 24-sep-2026):
--
--   1. ES EL ÚNICO LUGAR DONDE PUEDE VIVIR «LO SOLICITADO». La cantidad de la
--      orden de Odoo cambia: la KAM la ajusta y bodega la recorta al validar
--      (Odoo cancela lo que no tuvo: 2,528 piezas en 44 salidas medidas el
--      18-sep). Odoo no guarda la cantidad original del renglón, así que
--      después de esos cambios ya nadie puede decir cuánto se pidió. Por eso
--      las dos primeras etapas del rail (Solicitado, Validado) llevan meses
--      diciendo «por capturar», y la TASA DE VALIDADO no existe.
--   2. Dice QUIÉN pidió QUÉ desde el panel: la API de Odoo crea como José
--      Enrique, sea quien sea el que apretó el botón.
--   3. Liga la solicitud con sus órdenes (S#####). Con eso, cuando la salida
--      se valida y ML avisa la llegada, se puede medir la cadena completa:
--      sugerido → solicitado → validado → enviado → recibido.
--
-- SIN ESTA TABLA NADA SE ROMPE: «Crear FULL» crea igual la orden en Odoo y
-- avisa que la solicitud no se guardó (`solicitud_guardada: false`). Lo que se
-- pierde es el dato del punto 1, que después ya no se puede reconstruir.
--
-- VOLUMEN: una fila por FULL creado desde el panel (unas pocas por semana).
-- `clave` es la llave de idempotencia que genera el navegador: la misma
-- solicitud reintentada no crea dos órdenes ni dos filas.
create table if not exists ops.fulfillment_solicitudes (
    clave       text        primary key,
    creada_at   timestamptz not null default now(),
    cuenta      text        not null check (cuenta in ('Kubera', 'San Corpe')),
    quien       text,
    -- {cobertura_dias, min_piezas, min_ventas_30, dejar_en_bodega}
    parametros  jsonb       not null default '{}'::jsonb,
    -- [{sku, sugerido, solicitado, van, almacenes: [{almacen, cantidad}]}]
    lineas      jsonb       not null,
    -- [{sku, pedidas, van, porque}] — lo que Odoo no tenía libre al crear
    recortes    jsonb       not null default '[]'::jsonb,
    -- [{id, orden, almacen}] — las cotizaciones que nacieron en Odoo
    ordenes     jsonb       not null default '[]'::jsonb
);

create index if not exists fulfillment_solicitudes_creada_idx
    on ops.fulfillment_solicitudes (creada_at desc);

comment on table ops.fulfillment_solicitudes is
    'Lo que «Crear FULL» sugirió y lo que se pidió por SKU, antes de que la KAM o bodega lo recorten en Odoo. Da la etapa Solicitado del rail y la tasa de validado.';

-- RLS activa y sin políticas: el patrón de la casa (ver 0033). Solo entra el
-- backend (service_role / postgres).
alter table ops.fulfillment_solicitudes enable row level security;
