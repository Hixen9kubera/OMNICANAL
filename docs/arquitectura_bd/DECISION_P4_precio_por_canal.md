# DECISIÓN P4 — El precio sugerido es POR CANAL

- **Decidió:** Eduardo · **Fecha:** 2026-07-27 · **Estado:** APLICADA
- **Pregunta original** (plan maestro v4, §5 P4): ¿un SKU puede tener precio
  sugerido distinto por cuenta/canal? Bloqueaba la PK final de
  `costing.costos_finales`.

## Decisión

El precio sugerido se modela **por canal** (`general` / `mercado_libre` /
`amazon` / …), no único por SKU. Racional: las comisiones, fees de envío e IVA
efectivo difieren por marketplace — un precio único obliga a sobre-cobrar en
un canal o perder margen en otro.

## Qué cambió (migración `supabase/migrations/0003_p4_precio_por_canal.sql`)

1. `costing.costos_finales`: columna `canal text not null default
   'mercado_libre'` con FK a `core.channels`, y **PK (sku, canal)**.
2. Las 4,353 filas existentes quedaron como `canal='mercado_libre'` (la
   fórmula actual de `costos.py` es ML-céntrica: comisión ML, tarifa de envío
   ML, `gold_pro`).
3. `costing.costos_validados` NO cambia: dimensiones/peso/costo físico son
   del producto, no del canal.
4. `costing_mirror.espejar_finales` escribe `canal='mercado_libre'` fijo y
   upsertea por `(sku, canal)`; cuando el motor calcule precios de otros
   canales, el llamador pasará el canal.
5. `comparar_costos.py` compara MySQL (por-SKU, ML-céntrico) contra la fila
   `canal='mercado_libre'` de kubera — la racha de deltas no se ve afectada.

## Trabajo futuro que esta decisión habilita (no incluido aquí)

- Motor de costos multi-canal en `costos.py` (fórmula por canal: comisión
  Amazon vía Finances API, fee de envío por canal) → filas `canal='amazon'`
  y `canal='general'`.
- `costing.channel_costs` (la tabla bosquejada en el DDL v4 como épica
  futura) si se requieren conceptos de costo por canal con vigencias.
