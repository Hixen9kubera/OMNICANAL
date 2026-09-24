-- ═══════════════════════════════════════════════════════════════════════════
-- 0056 — costing.caja_compartida.archivo_sha256: la procedencia de un costo,
--        ligada a la VERSIÓN exacta del packing list. 23-sep-2026.
--
-- `caja_compartida` guardaba `archivo` (el NOMBRE en Drive) + `renglones`. Pero
-- el archivo se sigue editando después de creado (51 de 193 medidos): si
-- alguien inserta una fila, los renglones guardados apuntan a otro producto sin
-- que nada avise, y si lo renombran el vínculo se pierde.
--
-- Con esta columna la procedencia es (huella, renglones): la huella nombra un
-- objeto INMUTABLE del bucket `packing-lists` (0055, ruta `<tipo>/<sha256>.<ext>`),
-- así que el renglón se puede volver a abrir tal como estaba al validarse.
--
-- Sin llave foránea a propósito: la versión pudo leerse de Drive (respaldo) y
-- copiarse al bucket después; la huella es la misma y el join se hace por ella
-- (`costing.packing_archivos.sha256`).
--
-- La escribe y la lee el backend SOLO con PACKING_LEER_STORAGE=true: encender
-- ese flag exige esta migración aplicada. Agregar una columna nullable sin
-- default no reescribe la tabla (y tampoco firma la historia con un DEFAULT).
-- La tabla ya tiene RLS desde su creación (0034); no cambia.
-- ═══════════════════════════════════════════════════════════════════════════

alter table costing.caja_compartida
  add column if not exists archivo_sha256 text
  check (archivo_sha256 is null or archivo_sha256 ~ '^[0-9a-f]{64}$');

comment on column costing.caja_compartida.archivo_sha256 is
  'Huella sha256 del packing list EXACTO del que salieron los renglones. '
  'Abre el objeto inmutable <tipo>/<sha256>.<ext> del bucket packing-lists (0055). '
  'NULL en lo validado antes del 23-sep-2026 o con PACKING_LEER_STORAGE apagado.';
