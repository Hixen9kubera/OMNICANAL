-- ═══════════════════════════════════════════════════════════════════════════
-- 0031 — Fecha real de publicación en `channel.listings`, para el tab
-- Métricas de Análisis (KPI "publicaciones activadas por semana").
--
-- `channel.listings` no guardaba la fecha en que un listing se publicó por
-- primera vez en el canal: solo `updated_at` (última vez que el sync lo tocó,
-- que cambia con cada cambio de precio/stock y no sirve para esto). La API de
-- Mercado Libre sí la trae (`date_created` de `GET /items/{id}`, ya se pide
-- hoy y se descarta) — services/inventario.py la captura ahora en las 3
-- rutas que arman `rows` para el sync, y channel_mirror.escribir_tanda la
-- persiste. Idempotente.
-- ═══════════════════════════════════════════════════════════════════════════
alter table channel.listings add column if not exists date_published timestamptz;

create index if not exists idx_channel_listings_date_published
  on channel.listings(date_published);

comment on column channel.listings.date_published is
  'Fecha real de publicación en el canal (date_created de GET /items/{id} de '
  'Mercado Libre). Se captura UNA sola vez: ni el sync ni el backfill la '
  'sobreescriben una vez que tiene valor (coalesce hacia el valor existente '
  'en channel_mirror.escribir_tanda). NULL = aún no capturada (pendiente de '
  'backfill_fecha_publicacion_ml.py) o canal sin soporte (Amazon/general).';
