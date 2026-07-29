# Tablas TEMPORALES — para borrar al cerrar la migración

> **Para qué es este archivo.** Estamos migrando a la BD centralizada (kubera).
> Todo lo que se crea mientras tanto en MySQL (`u531713409_kubera_ml`) y que
> **no** debe sobrevivir al corte se anota aquí, para poder borrarlo de un
> jalón cuando Brandon dé la indicación — sin tener que ir a adivinar cuál era
> cuál. Registro pedido por Brandon el 2026-07-28.

## Cómo usar esto

1. **Antes de borrar**: confirmar que el dato ya vive en la BD kubera (o que no
   hace falta conservarlo). Estas tablas son bitácora/estado operativo, no
   registros de negocio: ninguna es fuente de verdad de ventas ni de inventario.
2. **El borrado va con dale explícito de Brandon.** Ninguna se borra sola.
3. Al agregar una tabla nueva al proyecto: **anotarla aquí en el mismo commit**
   (o dejar claro que es permanente y por qué).

---

## Tablas a borrar

| Tabla | La crea | Qué guarda | Se puede borrar cuando |
|---|---|---|---|
| `fanout_log` | `services/fanout_stock.py::_asegurar_schema` | Bitácora del fan-out DROP: cada réplica de stock a Amazon/ML, con SKU, canal, cuenta, objetivo, resultado y ms. También la usan `stock_full` (movimientos FULL/FBA), la compensación FULL/FBA de `pedidos_ml` (`full_compensado`) y `stock_watch` (`odoo_delta`, `woo_cambio`, `stock_watch_freno`). Es lo que pinta el Dashboard. | El fan-out pase a producción con su bitácora en kubera. **Ojo**: al borrarla se pierde el sello de idempotencia de `full_compensado` — antes de borrar, verificar que ningún pedido FULL/FBA quede pendiente de compensar (si no, podría compensarse dos veces). |
| `stock_watch_foto` | `services/stock_watch.py::_asegurar_schema` | Foto anterior de stock por SKU (`stock_woo`, `stock_odoo`) para detectar cambios contra la pasada previa. ~14,400 filas, una por SKU. | El vigilante de inventario viva contra kubera. Es **regenerable**: al borrarla, la siguiente pasada levanta la foto base sola y **no escribe nada** en esa pasada. Sin riesgo de pérdida. |
| `alertas_estado` | `services/alertas.py::_persistente` | Candado anti-spam de las alertas de Slack: por tipo de alerta, cuándo salió el último aviso, cuántas se suprimieron y el último estado visto (`ok` / `con_deltas` / `ausente`…). ~8 filas, una por tipo. Vive en la BD y no en memoria porque el proceso muere en cada deploy y el candado se perdía (v0.31.0). | **DECISIÓN (Eduardo, 2026-07-29): se FUSIONA con `ops.process_log` al terminar la migración** — no se borra a secas, su estado pasa a vivir ahí. Es **regenerable**: al borrarla, el primer aviso de cada tipo sale una vez más y la recuperación de una falla en curso no se anuncia. Sin riesgo de pérdida — no es dato de negocio. |

## Tablas que NO son temporales (no borrar)

Se anotan para que nadie las confunda con las de arriba:

- `pedidos_ml`, `ventas_horarias`, `ventas_sync` — registro histórico de ventas.
- `espejo_kubera_log` — errores del espejo; **local a propósito**, sobrevive con
  Supabase caído. Ver CLAUDE.md.
- `canal_inventario` — es de la migración (Eduardo/José): **leer sí, alterar no**.
- `webhook_eventos` — campana del panel.
- `amazon_imagenes`, `ml_backlog`, `ml_progress`, `amazon_progress` — bitácoras
  del publicador.
- `productos`, `scraping_alibaba`, `pipeline_runs` — legado de
  KuberaPipelineV1.0 (robot desconectado, ver CLAUDE.md #8). No las creamos
  nosotros; su retiro va con el corte de ese pipeline, no con esta lista.

---

## Borrado (cuando Brandon lo indique)

```sql
DROP TABLE IF EXISTS stock_watch_foto;
DROP TABLE IF EXISTS fanout_log;
-- alertas_estado: NO va en el borrado ciego — se fusiona con ops.process_log
-- (decisión de Eduardo, 29-jul). Migrar su estado ANTES de soltar el DROP.
DROP TABLE IF EXISTS alertas_estado;
```
