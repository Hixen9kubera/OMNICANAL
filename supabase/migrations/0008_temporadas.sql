-- 0008_temporadas.sql — Catálogo de TEMPORADAS comerciales con nombre.
--
-- Es solo un rango de fechas etiquetado: el resumen de ventas por temporada se
-- CALCULA de channel.sales_daily_completa al momento de consultar, así que
-- corregir un rango recalcula solo, se pueden agregar temporadas retroactivas,
-- y no hay proceso que "llene" nada al vender. Dos fuentes:
--   sembrada — generada por regla (las fechas fijas del retail mexicano);
--              Hot Sale y Buen Fin se siembran con la fecha típica y se
--              corrigen a mano cuando sale el anuncio oficial (2 ajustes/año)
--   manual   — capturada desde el panel
create table if not exists analytics.temporadas (
  id            serial primary key,
  nombre        text        not null,
  anio          int         not null,
  fecha_inicio  date        not null,
  fecha_fin     date        not null,
  fuente        text        not null default 'manual'
                constraint temporadas_fuente_chk check (fuente in ('sembrada', 'manual')),
  activa        boolean     not null default true,
  notas         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint temporadas_nombre_anio_uk unique (nombre, anio),
  constraint temporadas_rango_chk check (fecha_fin >= fecha_inicio)
);

comment on table analytics.temporadas is
  'Rangos de fechas con nombre (Navidad, Hot Sale…) para leer ventas/precio/margen por temporada. El resumen se deriva de los pedidos: la etiqueta no guarda cifras.';

-- Siembra 2026–2027. Idempotente: on conflict no toca lo que el panel ya editó.
insert into analytics.temporadas (nombre, anio, fecha_inicio, fecha_fin, fuente, notas) values
  ('Reyes',                 2026, '2025-12-26', '2026-01-06', 'sembrada', null),
  ('Día del Amor',          2026, '2026-02-01', '2026-02-14', 'sembrada', null),
  ('Hot Sale',              2026, '2026-05-25', '2026-06-03', 'sembrada', 'fecha típica — corregir con el anuncio AMVO'),
  ('Back to School',        2026, '2026-08-01', '2026-08-31', 'sembrada', null),
  ('Independencia',         2026, '2026-09-01', '2026-09-16', 'sembrada', null),
  ('Halloween y Muertos',   2026, '2026-10-15', '2026-11-02', 'sembrada', null),
  ('Buen Fin',              2026, '2026-11-13', '2026-11-17', 'sembrada', 'fecha típica — corregir con el anuncio oficial'),
  ('Navidad',               2026, '2026-12-01', '2026-12-25', 'sembrada', null),
  ('Reyes',                 2027, '2026-12-26', '2027-01-06', 'sembrada', null),
  ('Día del Amor',          2027, '2027-02-01', '2027-02-14', 'sembrada', null),
  ('Hot Sale',              2027, '2027-05-24', '2027-06-02', 'sembrada', 'fecha típica — corregir con el anuncio AMVO'),
  ('Back to School',        2027, '2027-08-01', '2027-08-31', 'sembrada', null),
  ('Independencia',         2027, '2027-09-01', '2027-09-16', 'sembrada', null),
  ('Halloween y Muertos',   2027, '2027-10-15', '2027-11-02', 'sembrada', null),
  ('Buen Fin',              2027, '2027-11-12', '2027-11-16', 'sembrada', 'fecha típica — corregir con el anuncio oficial'),
  ('Navidad',               2027, '2027-12-01', '2027-12-25', 'sembrada', null)
on conflict (nombre, anio) do nothing;
