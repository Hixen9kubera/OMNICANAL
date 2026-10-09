# Migración 0069 — apunte para Eduardo

> **Ya está APLICADA en producción** (kubera, `tukwcvsi`): 9-oct-2026, 07:05 UTC,
> por el chat de Inventario, con la instrucción explícita de Brandon («sube la
> migración, aplica a producción»). No pasó por acta. La tabla **ya tiene datos**
> (cargados a las 07:06 UTC). **En el sandbox NO está aplicada.**

Archivo: `supabase/migrations/0069_almacen_locations.sql`.
Cargador: `backend/scripts/cargar_locations_tex2.py`.

## Por qué existe

Brandon, 9-oct: TEXCO II deja de operarse desde Odoo y pasa a kubera en unas dos
semanas. La ubicación de rack de cada SKU sólo existía en Odoo; ni Woo ni kubera
la tenían. `almacen.locations` es su casa.

## Qué creó

Una tabla nueva, `almacen.locations`: un renglón por SKU y ubicación dentro de
un cedis. No tocó ninguna tabla existente; sólo le puso una llave foránea a
`almacen.almacenes (codigo)`.

| Columna | Qué es |
|---|---|
| `cedis` | Código de la bodega (`TEX2`). **Obligatorio**, con llave a `almacen.almacenes`. |
| `sku` | `citext`. Sin llave a `core.products` (ver «Lo que hay en los datos»). |
| `ubicacion` | Como la pinta el panel: `BLOQUE D-FILA 1-T6`. Sin rack = `SIN UBICAR`. |
| `bloque`, `fila`, `tarima` | La ubicación partida; las tres o ninguna. |
| `piezas` | **Informativa.** Puede ser negativa. El saldo oficial sigue siendo `almacen.stock_almacen.fisico`. |
| `origen` | `odoo` (vino de la foto) o `kubera` (capturado aquí). |
| `archivado_odoo` | El producto está archivado en Odoo: sus piezas no cuentan. |
| `odoo_quant_id`, `odoo_product_id`, `odoo_ubicacion` | De qué renglón de Odoo salió. |
| `entrada_at`, `cargado_at`, `actualizado_at` | Fechas; la última la mantiene un trigger. |

- Único `(cedis, sku, ubicacion)` entre los renglones que no son de producto
  archivado; único `odoo_quant_id`.
- RLS encendido, sin políticas; `service_role` con todo; `anon` y
  `authenticated` sin nada. Mismo patrón que la 0065.
- La función del trigger (`almacen.locations_tocar`) nació en `almacen`, no en
  `ops`, con `search_path = pg_catalog`.
- Dejó su renglón en `ops.migraciones`.

## Cómo se aplicó

1. Ensayo en producción: la migración entera dentro de una transacción que
   terminó en `ROLLBACK`, con 12 comprobaciones y 4 rechazos esperados (sin
   cedis, cedis inexistente, SKU repetido en la misma ubicación, rack a medias).
2. La migración tal cual, por el puerto 5432 (modo sesión), con `lock_timeout`
   de 5 s. Las mismas 12 comprobaciones después del `COMMIT`.
3. El cargador, primero en ensayo y luego con `--aplicar`.

Probada antes en local (Postgres 16): sobre `0064 → 0065 → 0068` y también con
la 0069 antes de la 0068; dos veces en cada caso.

## Lo que hay en los datos (medido el 9-oct)

1,663 renglones · 1,228 SKUs · 476,395 piezas, igual que Odoo.

- **934 renglones `SIN UBICAR`**: 76% de los SKUs de TEXCO II están en la raíz
  de la nave (`TEX2/FERRAFORME`), sin bloque, fila ni tarima.
- **21 renglones negativos**, todos de movimientos hechos en Odoo:
  11 surtidos (TEX2/PICK, 23–30 jul) desde «FERRAFORME Archivar», que nunca tuvo
  mercancía; 7 por la «Adecuación para alta de producto [revertido]» del 18-jul;
  3 por surtir o traspasar de más. Brandon pidió traerlos.
- **37 renglones de productos archivados** (24 SKUs, 22,250 piezas). En 21 SKUs
  hay dos productos de Odoo: el archivado (20-may) conserva el rack y el activo
  (9-jul) lleva la cuenta. Van marcados con `archivado_odoo`; sin ellos el total
  es 454,145 piezas.
- **3 productos sin `default_code`** cuyo nombre es el SKU: se usó el nombre.
- **1 SKU que no está en `core.products`**: `JUGU-1155-NEG`. Por eso no hay
  llave foránea al maestro.

## Lo que queda abierto, y es tuyo decidir

- **Sandbox:** aplicar ahí la 0069 (después de la 0068, o antes: funciona en
  los dos órdenes).
- **`supabase/schema_manifest.json`:** no lo regeneré.
- **`aplicar_migraciones.py`:** `ESQUEMAS_PROPIOS` sigue sin `almacen`.
- **La foto envejece.** Mientras TEXCO II se opere en Odoo, cada surtido mueve
  los quants. El cargador se puede volver a correr (reemplaza lo de
  `origen = 'odoo'`) hasta el día del corte.
- **El día del corte.** El cargador se niega a correr si
  `almacen.almacenes.fuente` de TEX2 ya no es `odoo`, o si hay renglones con
  `origen = 'kubera'`. Como `fuente` es fija por trigger, pasar TEX2 a kubera es
  diseño pendiente, no un `UPDATE`.

## Reversa

`drop table almacen.locations; drop function almacen.locations_tocar();`
No la lee nada todavía.
