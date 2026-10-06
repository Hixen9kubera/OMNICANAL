# CATALOGO_VIVO — cómo se le pregunta a cada sistema qué tiene AHORA

> Extraído el 6-oct-2026 de `origin/main` (v0.618.0) y de la rama
> `sandbox/precios-optimos` (lab-0.3). Si algo aquí no coincide con `main`,
> **gana `main`** y hay que re-extraer.

Brandon pidió una página con todo el catálogo de Odoo cruzado contra los cinco
marketplaces, leído de las plataformas y no de la base. Este documento es lo que
hubo que saber para hacerlo; el ejecutable está en [scripts/](scripts/).

---

## 1 · Cómo lee producción cada fuente

| Fuente | Dónde lo hace producción | Lo que importa |
|---|---|---|
| Odoo, catálogo y stock | `backend/services/odoo.py::listar_catalogo` | El stock es **`free_qty`**, no `qty_available`: lo físico menos lo reservado. Por almacén: el mismo `search_read` con `context={"warehouse": id}` |
| Odoo, contenedor | `odoo.py::contenedores_por_sku` | `container_numbers` es texto libre y sucio. No se parsea: se extraen códigos con regex (`packing_drive_carpeta.codigos_de`) |
| ML, qué existe | `backend/services/inventario.py::_universo_ml` | `GET /users/{id}/items/search?search_type=scan&status=` por estado. Nunca `ml_progress`: solo conoce lo que publicó el panel |
| ML, el SKU | `inventario.py::_sku_de_item` | `seller_custom_field` → atributo `SELLER_SKU` → variaciones |
| ML, precio cobrado | `backend/services/precios_venta.py::_precio` | `GET /items/{id}/sale_price?context=channel_marketplace` — trae la promoción aplicada |
| ML, comisión y envío | laboratorio: `sandbox_precios/extraer_ml.py` | `GET /sites/MLM/listing_prices?price=&category_id=&listing_type_id=&logistic_type=` y `GET /users/{id}/shipping_options/free?item_id=&item_price=` |
| Amazon | `backend/services/amazon.py::datos_por_sku` | `searchListingsItems` (`GET /listings/2021-08-01/items/{seller}`), `includedData=summaries,offers,fulfillmentAvailability` |
| TikTok | `backend/services/tiktok_censo.py::censar` | `/product/202309/products/search` paginado por `page_token`; contesta HTTP 200 aunque falle (el veredicto va en `code`) |
| Temu | `backend/services/temu.py::listar_productos` | `bg.local.goods.list.query` por cubeta (`goodsSearchType` ENTERO: 1, 4, 5, 6) |
| Temu desde fuera | `backend/routers/investigacion.py` | `POST /api/investigacion/temu {type, params}`: solo admin con sesión, solo `…get`/`…query`, respuesta redactada |
| Walmart | `backend/services/walmart.py::listar_items` | `GET /v3/items?limit=50&offset=`; el token es `client_credentials` y no se cachea |
| Costos | `backend/services/costing_read.py` | `costing.costos_validados`: `costo_producto`, `costo_cbm`, `costo_total`, `contenedor`, `cajas`, `piezas_por_caja` |
| Prorrateo | laboratorio: `sandbox_precios/prorrateo.py::prorrateo_kubera` | `cbm_pieza = costo_cbm / 7500`; el m³ del contenedor se reconstruye sumando sus SKUs |

---

## 2 · Lo que se MIDIÓ el 6-oct-2026 y no estaba escrito en ningún lado

**Amazon lista sin identificadores, pero corta en ~1,000.** `searchListingsItems`
sin `identifiers` devuelve el catálogo completo de la cuenta con `numberOfResults`
y `pagination.nextToken`. Al llegar a mil deja de dar página siguiente aunque
falten. Salida: ordenar por `createdDate` ascendente y, cuando una pasada se
agota, arrancar la siguiente con `createdAfter` = la última fecha vista.

**Amazon sí da la comisión.** `getMyFeesEstimates` (`/products/fees/v0/feesEstimate`,
20 por llamada, ~0.5 por segundo) devuelve `ReferralFee`, cierre y FBA al precio
que se le mande. Es un cálculo: no guarda nada. OJO: es una ESTIMACIÓN al precio
publicado, no lo que Amazon cobró en un pedido — para eso sigue haciendo falta
Finances API (pendiente #5 de CLAUDE.md).

**TikTok contestó desde la laptop; Temu no.** TikTok también tiene lista blanca de
IP, pero ese día esta máquina pasó. Temu respondió `5000003 NOT_IN_IP_WHITE_LIST`.

**TikTok guarda lo borrado.** De cada tres productos que lista, uno viene con
estado `DELETED`. No es una publicación: hay que quitarlo antes de contar.

**Una de cada nueve publicaciones de ML lleva un SKU que no existe en Odoo**, y
casi todas son SKU padre. Odoo lleva el inventario por variante y ML publica
plano. Cruzar solo por SKU exacto deja a cientos de variantes con stock como «sin
publicar» teniendo a su padre publicado. Hay que colgarles la publicación del
padre — marcada, porque no es lo mismo que tener la propia.

**La ficha de Odoo no sirve para costear, y ahora se sabe cuánto.**
`standard_price × 19` reproduce `costo_producto` de la base en tres de cada cuatro
productos que tienen ambos. Pero en la línea «Productos Agente» el campo viene en
otra unidad, y usarlo como respaldo valuaba unos cuantos cientos de productos en
más que todo el resto del catálogo. La regla de Brandon («de Odoo, solo la foto y
el contenedor») queda confirmada con medición.

**A la base de costos le falta un tercio del inventario.** De los productos de
Odoo con stock, poco más de seis de cada diez tienen renglón propio en
`costing.costos_validados`. Lo demás son variantes sin renglón (heredan del padre
o de una hermana) y embarques recientes que todavía no se costean.

**El número de contenedor es mejor llave que el código.** `TXGU7222939 contenedor
7` en Odoo es `SZLS50224700 - 7` en la base: dos códigos del mismo embarque.
Comparando por el ordinal coinciden casi nueve de cada diez; por código, algo más de ocho.

---

## 3 · Tres cosas que NO se hacen, aunque el código lo permitiría

1. **Renovar tokens.** El de ML se rota al usarse. Un script que lo renueve deja a
   producción con un refresh_token muerto.
2. **Leer los tokens sin permiso expreso.** Viven cifrados en `ops.ml_tokens` y
   `ops.tiktok_tokens`. Sacarlos de la base de producción es una lectura sensible:
   se pide autorización a Brandon cada vez.
3. **Entrar a `/investigacion` con una llave de máquina.** Ese endpoint es para una
   persona con sesión, a propósito. Si no hay sesión, Temu se reporta como no leído.
