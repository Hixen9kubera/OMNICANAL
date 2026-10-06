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

## 2b · Precio de venta y categorías: lo que se midió al cambiar el enfoque

La tarde del 6-oct se pidió dejar los canales y poner el foco en **cuánto vale en
el mercado**, con categorías. Lo que hubo que averiguar:

**Ninguna fuente le pone precio a todo.** Dos de cada tres productos con stock no
están publicados en ningún marketplace. Para esos, el único precio que existe es el
de catálogo en WooCommerce — cuando lo tiene.

**El «Sales Price» de Odoo (`list_price`) no es un precio de venta.** En el
catálogo propio vale `1` o repite el costo en dólares de la ficha. En «Productos
Agente» trae cualquier cosa. Contra los productos que sí tienen precio vivo en un
marketplace no coincide prácticamente nunca. Y como hay flujos que lo copian tal
cual a WooCommerce (y de ahí a un feed), aparece disfrazado de precio en otros
lados: hay que descartarlo **por igualdad con el campo de Odoo**, no por fuente.

**Un borrador de WooCommerce vale `1.00`.** Es el marcador de lo que nunca pasó por
el Estudio. No es un precio.

**La REST de WooCommerce sí devuelve variaciones, si se le pregunta por SKU.**
`GET /products` lista solo padres y simples. Pero `GET /products?sku=A,B,C` trae
también variaciones (`type: variation`, con su `parent_id`) y acepta varios SKUs
separados por coma. Con lotes de 40 se cubren miles de variantes sin ir padre por
padre. Una variación no trae categorías: son las de su padre.

**Las categorías de WooCommerce NO forman árbol.** Son ~1,700 hojas, todas en la
raíz: el nombre es el de la hoja de Mercado Libre y la **descripción** dice
`ML: MLM123456` (así las crea `crear_producto.py::get_or_create_wc_categoria`).
Agrupar por ellas no contesta «qué tenemos». Lo que sirve es resolver ese id contra
el árbol de ML.

**El árbol de ML se baja entero en una llamada.** `GET /sites/MLM/categories/all`
devuelve las ~12,000 categorías con su `path_from_root` (30 MB, pide token). Y
`GET /categories/{id}` es **público**, sin token, para resolver de una en una.
Las raíces son 31.

**El prefijo del SKU no es una categoría.** La taxonomía de la casa
(`packing_taxonomia.SUBCATEGORIAS`) dice que `PAS-` es «Paseo Bebé», y bajo ese
prefijo hay bozales para perro y cambiadores de agua para pecera; `MES-` trae
toallas; `OFI-`, un hacha de cocina. Sirve como último recurso para no dejar un
producto sin clasificar, **marcado como estimado**, y nada más.

**Cuatro de cada diez categorías de WooCommerce no traen el id de ML.** Las creó
otro flujo y solo llevan el nombre — que casi siempre es ambiguo en el árbol
(«Tenis» existe siete veces: el calzado y el deporte). Se resuelven así: si todas
las candidatas cuelgan de la misma raíz, la raíz es segura; si no, el prefijo del
SKU desempata; si tampoco, no se adivina. Y se descartan las raíces que no son
mercancía: «Tecnología» existe en ML… dentro de *Servicios › Servicios de
Reparación*.

**El árbol de ML se contradice a sí mismo en dos nombres.** La raíz `MLM1071` se
llama «Mascotas» en el árbol y «Animales y Mascotas» en la lista de raíces; y
«Deportes y Fitness » trae un espacio al final. Sin normalizar, la misma categoría
sale en dos renglones.

**Multiplicar piezas por precio no siempre es valuar.** Lo que más infla un «valor
de venta» no son los precios altos, son dos desajustes de UNIDAD y de IDENTIDAD:

- *Paquete contra pieza.* La publicación vende «100 piezas» y Odoo cuenta bolsas
  sueltas: el valor sale cien veces mayor. Se detecta cuando el título de la
  publicación trae una cantidad que el nombre de Odoo no trae.
- *SKU reciclado.* La publicación es de otro producto que heredó el SKU (pendiente
  #7 de CLAUDE.md). Se nota porque el precio no guarda ninguna proporción con el
  costo.

Los dos se mandan a **revisión**: el precio se muestra, pero no se suma. El corte
para «desproporcionado» se midió sobre lo que hoy sí se vende (ahí solo el 3% más
alto pasa de 25 veces su costo); cuando no hay costo, se compara contra la mediana
de su subcategoría. La distribución con sus cifras no se escribe en este
repositorio: sale en `datos.json`, que no se sube.

**Un puñado de SKUs puede ser un cuarto del inventario.** Once referencias con
diez mil piezas o más concentraban más de la cuarta parte de las piezas libres. Con
esa forma, un total es tan bueno como el conteo de esos once: van primero en
cualquier limpieza.

---

## 3 · Tres cosas que NO se hacen, aunque el código lo permitiría

1. **Renovar tokens.** El de ML se rota al usarse. Un script que lo renueve deja a
   producción con un refresh_token muerto.
2. **Leer los tokens sin permiso expreso.** Viven cifrados en `ops.ml_tokens` y
   `ops.tiktok_tokens`. Sacarlos de la base de producción es una lectura sensible:
   se pide autorización a Brandon cada vez.
3. **Entrar a `/investigacion` con una llave de máquina.** Ese endpoint es para una
   persona con sesión, a propósito. Si no hay sesión, Temu se reporta como no leído.
