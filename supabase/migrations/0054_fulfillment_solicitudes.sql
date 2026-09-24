-- ═══════════════════════════════════════════════════════════════════════════
-- 0054 — La SOLICITUD de la planeación semanal se guarda antes de que bodega la recorte
-- ═══════════════════════════════════════════════════════════════════════════
--
-- QUÉ GUARDA. Cada vez que «Crear FULL» (pestaña FULLFILMENT) crea cotizaciones
-- en Odoo, una fila POR TIENDA (ML Kubera, ML San Corpe, Amazon FBA, Walmart WFS)
-- con lo que el panel SUGIRIÓ y lo que la persona PIDIÓ por SKU, los parámetros
-- con que se calculó (cobertura, ventana, mínimo por renglón…), lo que Odoo no
-- tenía (recortes), las órdenes que nacieron y si fue una PRUEBA.
--
-- POR QUÉ HACE FALTA UNA TABLA (Brandon pidió que se justificara, 24-sep-2026):
--
--   1. ES EL ÚNICO LUGAR DONDE PUEDE VIVIR «LO SOLICITADO». La cantidad de la
--      orden de Odoo cambia: la KAM la ajusta y bodega la recorta al validar
--      (Odoo cancela lo que no tuvo: 2,528 piezas en 44 salidas medidas el
--      18-sep). Odoo no guarda la cantidad original del renglón, así que
--      después de esos cambios ya nadie puede decir cuánto se pidió. Por eso
--      las dos primeras etapas del rail (Solicitado, Validado) dicen «por
--      capturar», y la TASA DE VALIDADO del prompt estándar no existe.
--   2. Dice QUIÉN pidió QUÉ desde el panel: la API de Odoo crea como José
--      Enrique, sea quien sea el que apretó el botón.
--   3. Liga la solicitud con sus órdenes (S#####) y separa las pruebas.
--
-- POR QUÉ NO EN `ops.odoo_sale_orders` (la bitácora de TikTok/Temu): esa tabla la
-- LEEN procesos que deciden —el stock que se aparta mientras una venta espera su
-- guía, las «Guías del día» del almacén, los contadores de Automatización— y su
-- forma es de VENTA (un renglón con `cantidad > 0`, una sola orden por venta).
-- Meter ahí la planeación obligaba a filtrar en todos esos lectores y a cambiarle
-- columnas de todos modos.
--
-- SIN ESTA TABLA NADA SE ROMPE: se crea igual en Odoo y la respuesta avisa
-- `solicitud_guardada: false`. Lo que se pierde es el dato del punto 1.
--
-- VOLUMEN: unas pocas filas por semana. (`clave`, `tienda`) es la llave de
-- idempotencia: la misma solicitud reintentada no crea filas de más.
create table if not exists ops.fulfillment_solicitudes (
    clave       text        not null,
    tienda      text        not null check (tienda in ('meli:Kubera', 'meli:San Corpe', 'amazon', 'walmart')),
    creada_at   timestamptz not null default now(),
    prueba      boolean     not null default false,
    quien       text,
    -- {cobertura_dias, ventana_dias, min_piezas, min_ventas, dejar_en_bodega}
    parametros  jsonb       not null default '{}'::jsonb,
    -- [{sku, sugerido, solicitado, van, almacenes: [{almacen, cantidad}]}]
    lineas      jsonb       not null,
    -- [{sku, pedidas, van, porque}] — lo que Odoo no tenía libre al crear
    recortes    jsonb       not null default '[]'::jsonb,
    -- [{id, orden, almacen}] — las cotizaciones que nacieron en Odoo
    ordenes     jsonb       not null default '[]'::jsonb,
    primary key (clave, tienda)
);

create index if not exists fulfillment_solicitudes_creada_idx
    on ops.fulfillment_solicitudes (creada_at desc);

comment on table ops.fulfillment_solicitudes is
    'Lo que la planeación semanal de «Crear FULL» sugirió y lo que se pidió por SKU y tienda, antes de que la KAM o bodega lo recorten en Odoo. Da la etapa Solicitado del rail y la tasa de validado.';

-- RLS activa y sin políticas: el patrón de la casa (ver 0033). Solo entra el
-- backend (service_role / postgres).
alter table ops.fulfillment_solicitudes enable row level security;
