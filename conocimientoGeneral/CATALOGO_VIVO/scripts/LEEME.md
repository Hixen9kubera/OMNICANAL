# `catalogo_vivo.py` — el catálogo de Odoo cruzado EN VIVO contra los marketplaces

> Vive en la rama `conocimiento`. **No está en `main`, así que no puede llegar a
> producción**: Railway solo despliega desde `main`.
> Escrito el 6-oct-2026 a pedido de Brandon. Lógica de lectura copiada de
> producción (`origin/main` v0.618.0) y del laboratorio de precios (`sandbox/precios-optimos`).

Le preguntas a cada sistema qué tiene **ahora**, y te deja una página web que
contesta: **qué tenemos, cuánto tenemos, cuánto costó, cuánto costaría prorrateado
y en cuánto se vende**. Una fila por producto de Odoo: foto, título, categoría,
stock libre, costo, costo prorrateado, precio de venta y packing list. Arriba, tres
totales con el mismo peso —**costo total del inventario**, **costo total
prorrateado** y **precio total del inventario**— y el desglose por categoría;
aparte, la lista de lo que hay que limpiar.

> **El stock es `free_qty` de productos activos, y nada más.** Lo reservado para
> pedidos y lo que quedó en productos archivados está prohibido: no se cuenta, no
> se suma y no se muestra (regla de Brandon, 6-oct-2026).

> **Cambio de enfoque (6-oct-2026, por la tarde).** La primera versión giraba
> alrededor de los canales (en cuál está publicado cada producto, con qué
> comisión). Se pidió dejar eso: *los marketplaces no son relevantes, lo que
> importa es el valor de venta; agreguen categorías; el prorrateo, hasta el final;
> después, limpieza de datos.* Los extractores de canal siguen aquí porque de
> ellos sale el precio al que se vende — pero la página ya no habla de canales.

**No publica nada. No escribe nada. En ningún lado.**

---

## En 60 segundos

```bash
cd conocimientoGeneral/CATALOGO_VIVO/scripts

python catalogo_vivo.py todo            # todo lo que tenga credenciales (~15 min)
python catalogo_vivo.py odoo amazon     # o solo algunas etapas
python catalogo_vivo.py pagina          # rearmar la página con lo ya extraído

# → salidas/index.html        ábrela con doble clic
# → salidas/datos.json        el catálogo cruzado
# → salidas/img/hNNN.jpg      las fotos, en hojas
```

`--salida CARPETA` cambia el destino. Úsalo: `salidas/` está fuera de git a
propósito (ver «Lo que no se sube»).

Python: el `.venv` del backend ya trae todo (`httpx`, `psycopg2`, `cryptography`,
`Pillow`). Las credenciales se leen de `scripts/.env` si existe; si no, del `.env`
y `.env.amazon` de la carpeta de producción vecina — **solo se leen**, a un
diccionario en memoria.

---

## Las etapas

| Etapa | De dónde lee | Cómo | Tarda |
|---|---|---|---|
| `odoo` | Odoo | XML-RPC `search_read` de `product.product`: `free_qty` (total y por almacén), `container_numbers`, `image_128` | 3 min |
| `costos` | kubera | **un** `SELECT` a `costing.costos_validados` + el prorrateo de 525k | 3 s |
| `woo` | WooCommerce | REST: productos (precio de catálogo, estado, categoría) y variaciones por SKU | 15 min |
| `amazon` | SP-API | `searchListingsItems` por ventanas de fecha + `getMyFeesEstimates` | 3 min |
| `walmart` | Walmart MX | `GET /v3/items` paginado | 10 s |
| `ml` | Mercado Libre | scan + `/items` + (activas) `sale_price`, `listing_prices`, `shipping_options/free` | 8 min |
| `tiktok` | TikTok Shop | `products/search` + detalle para la foto | 1 min |
| `temu` | Temu | `bg.local.goods.list.query` — **solo desde la IP de producción** (ver abajo) | — |
| `categorias` | Mercado Libre | resuelve cada categoría a su ruta en el árbol de ML (31 raíces) | 5 s |
| `imagenes` | CDNs públicos | baja las miniaturas que hagan falta y arma las hojas | 1 min |
| `pagina` | lo anterior | cruza por SKU y escribe la página | 2 s |

Los cuatro canales (`amazon`, `walmart`, `ml`, `tiktok`) ya no son el tema: se leen
porque **de ahí sale el precio al que se vende**. Si solo quieres refrescar stock y
valor, basta `odoo woo categorias imagenes pagina`.

Cada etapa escribe su archivo en `datos/` y su estado en `datos/_estado/`. Si una
falla, las demás quedan intactas y la página dice **«no leído»** con el motivo.
Un canal que no se leyó nunca se rellena con datos viejos.

---

## El inventario contado DESDE LOS PACKING LISTS (7-oct-2026)

La página de arriba parte de Odoo: «qué dice el sistema que hay». Esta parte de lo que
se COMPRÓ, porque Odoo solo sabe lo que le capturaron y su historia empieza en
diciembre de 2025:

    piezas de los packing lists  −  lo que Odoo movió hacia afuera  =  lo que debería quedar

```bash
CACHE=C:/algun/lugar/fuera/de/onedrive      # los packing lists pesan ~3.3 GB
python catalogo_vivo.py pl_bajar pl_leer --cache-pl $CACHE    # 3 + 8 min
python catalogo_vivo.py movimientos inventario                # 1 min
python catalogo_vivo.py titulos                               # 12 min, ~1 USD de DeepSeek
python catalogo_vivo.py categorias_ml mercado_ml              # 25 min + lo que dure
python catalogo_vivo.py mercado_amazon                        # ~3 h (lo manda el límite de Amazon)
python catalogo_vivo.py pagina_pl                             # 20 s → index_pl.html
```

| Etapa | De dónde lee | Qué deja |
|---|---|---|
| `pl_bajar` | `costing.packing_archivos` (índice) + Drive público (GET) | los `.xlsx` originales y los validados por bodega |
| `pl_leer` | esos archivos | por renglón: nombre, cajas, piezas, precio, huella de la foto; en los validados, el SKU |
| `movimientos` | Odoo `stock.move.line` hechos + `purchase.order.line` | entradas y salidas por SKU, por tipo de socio; lo comprado y recibido |
| `inventario` | lo anterior | comprado − salió = queda, por SKU y por contenedor; el empate SKU ↔ renglón |
| `titulos` | DeepSeek | título estilo Mercado Libre, término de búsqueda y unidades por paquete, para cada SKU y cada renglón sin SKU |
| `categorias_ml` | predictor de categorías de ML | categoría para lo que no tenía una confiable |
| `mercado_ml` | kubera (rivales ya juzgados) + catálogo de ML | precio de la competencia en ML |
| `mercado_amazon` | SP-API de Amazon | precio de la competencia en Amazon, por palabra clave |
| `pagina_pl` | todo lo anterior | `index_pl.html` y `datos_pl.json` |

Todas se reanudan: lo ya hecho no se vuelve a pedir. Las dos de mercado procesan
primero lo que más piezas tiene detrás, así que una corrida cortada a la mitad ya
cubre casi todo el inventario.

La llave de DeepSeek va en `scripts/.env` (`DEEPSEEK_API_KEY=`), que no se sube.

Lógica de lectura de packing lists: la de `kubera-exit` de José
(github.com/joseKubera/kubera-exit), copiada en `pl_leer.py`; el lector de originales
es `pl_parser.py`, copia literal del de producción.

### El precio de Mercado Libre viene de un archivo, no de una etapa

`pagina_pl` busca en `<salida>/datos/eduardo_ml/` el paquete «valor de los contenedores a
precio de Mercado Libre» (la cotización por API de la sesión de COMPETENCIA de Eduardo:
`contenedores_v2.csv`, `valor_lineas.csv`, `pm_precios_full.csv`, `pm_precios_sd.csv`,
`correcciones_top50.json`, `valor_resumen.json`). Lo lee `ml_contenedores.py`. Si la carpeta
no está, la página sale como antes, con lo de `mercado_ml`.

**Ese paquete NO se sube**: trae precios de compra de proveedores, y este repositorio es
público. Se copia a mano a la carpeta de salida.

Cómo se une: por SKU; lo que no tiene SKU, por archivo (el `sha256` es el mismo de
`pl_indice.json`) y número de renglón, que es el mismo en los dos lados. Cada fila dice de
cuál de sus cuatro fuentes salió el precio (exacto, nuestro precio publicado, revisado a
mano, banda de categoría), y en la página se ven el mínimo, la media y el máximo.

Hay también una etapa que no estaba en la tabla: `empate_ia` (DeepSeek empareja, dentro de
un contenedor sin validar, los renglones sueltos con los SKUs sueltos; solo cuenta lo que
marca con confianza alta).

---

## Temu: la única etapa que no corre desde una laptop

La Open API de Temu solo contesta desde la IP de Railway (`5000003
NOT_IN_IP_WHITE_LIST`). Camino para leerla:

1. En el panel de producción, con sesión de **admin**: `/investigacion`.
2. Tipo `bg.local.goods.list.query`, params `{"goodsSearchType": 1, "pageNo": 1, "pageSize": 50}`.
   Repetir para las cubetas `1`, `4`, `5` y `6` y todas sus páginas.
3. Juntar los `goodsList` de todas las respuestas en `datos/temu_crudo.json`:
   `{"leido": "<fecha ISO>", "goods": [ … ]}`.
4. `python catalogo_vivo.py temu imagenes pagina`.

---

## Lo que hay que saber antes de fiarse de un número

- **Qué se cuenta como «tener».** Productos ACTIVOS de Odoo con referencia interna
  y `free_qty` mayor a cero. Otro reporte que cuente la existencia física
  (`qty_available`), que sume por SKU las piezas de productos archivados, o que
  cuente una fila por SKU y bodega, da MÁS SKUs y más piezas con los mismos datos.
  No es que uno esté mal: es otro criterio. El de esta página es el que se pidió,
  y `pagina.py` ya ni siquiera manda el físico a la página. Si dos reportes no
  cuadran, se cruzan SKU por SKU (ver `CONOCIMIENTO.md`, sección 2c) — no se
  cambia el criterio.
- **El precio de venta se busca en orden, y cada fila dice cuál se usó:** el más
  bajo al que hoy se vende en un marketplace → el más bajo de sus publicaciones
  pausadas → el precio de catálogo de WooCommerce → sin precio. Lo que no tiene
  precio NO suma: no se inventa.
- **Dos cosas parecen precio y no lo son**, y se descartan vengan de donde vengan:
  el `1.00` de un borrador sin trabajar, y el «Sales Price» de Odoo copiado tal
  cual (en el catálogo propio es el costo en dólares o un 1).
- **La categoría es la de Mercado Libre** (31 raíces y sus ramas). Sale de la
  publicación o de lo que el panel guardó en WooCommerce. Si no hay, se hereda de
  otra variante; y como último recurso se ESTIMA por el prefijo del SKU, marcada:
  ese prefijo se asignó a ojo (`PAS-` «Paseo Bebé» trae bozales para perro).
- **El costo oficial es `costo_producto` de la base**, no el de la ficha de Odoo.
  Solo los productos con renglón propio entran a ese total; lo heredado y lo
  extrapolado se suman aparte.
- **El costo prorrateado es un TERCER total, no un ajuste al costo.** Es un
  estimado: el costo del contenedor repartido entre sus piezas por volumen. Tiene
  su propia columna por producto (unitario y total) y su propio bloque arriba; no
  se suma al costo de producto. Solo suma el que sale del volumen del propio
  producto: lo heredado de una variante y lo extrapolado con la mediana del
  contenedor se muestran aparte.
- **El prorrateo es tan bueno como el volumen por pieza de la base, y ese dato
  falla** (el cartón capturado como si fuera la pieza). Como el reparto es por
  volumen, unos pocos errores se llevan buena parte de la cuenta. Por eso va **a
  revisión** —se ve tachado y no suma— cuando la pieza mide más de 1.5 m³, cuando
  el prorrateo pasa del precio al que se vende, o cuando pasa de 10 veces su costo
  de producto.
- **Una variante puede estar publicada con el SKU de su padre.** Mercado Libre
  publica sin variaciones; a la variante le sirve el precio de esa publicación.
- **El valor de venta es a precio de lista**: antes de comisiones, envíos e
  impuestos.

---

## Lo que NO hace, y por qué

| No hace | Por qué |
|---|---|
| Renovar el token de Mercado Libre o TikTok | Renovar ROTA el refresh_token de producción y paran las ventas. Si el token caducó, se relee una vez de kubera y, si no sirve, la cuenta sale «no leída» |
| Marcar la sesión de kubera como solo lectura | Regla 13 de la casa: el pooler comparte conexiones y la marca se queda pegada |
| Usar un caché cuando un canal no contesta | Un número de caché parece una verificación y no lo es |
| Mandar algo que no sea una lectura | `comun.py` solo deja salir GET y las cinco lecturas-con-otro-verbo de su lista blanca |

## Lo que no se sube

El repositorio es **público**. `salidas/` y cualquier carpeta de `--salida` llevan
costos, márgenes y stock: no van a git. Aquí solo vive el **cómo**.
