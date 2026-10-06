# `catalogo_vivo.py` — el catálogo de Odoo cruzado EN VIVO contra los marketplaces

> Vive en la rama `conocimiento`. **No está en `main`, así que no puede llegar a
> producción**: Railway solo despliega desde `main`.
> Escrito el 6-oct-2026 a pedido de Brandon. Lógica de lectura copiada de
> producción (`origin/main` v0.618.0) y del laboratorio de precios (`sandbox/precios-optimos`).

Le preguntas a cada sistema qué tiene **ahora**, y te deja una página web que
contesta tres cosas: **qué tenemos, cuánto tenemos y cuánto vale en el mercado**.
Una fila por producto de Odoo: foto, título, categoría, stock libre, precio de
venta, valor de venta, costo de producto y packing list. Arriba, el total y el
desglose por categoría; aparte, la lista de lo que hay que limpiar.

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
- **El prorrateo de 525k va al final.** Está calculado y se ve en el detalle de
  cada fila, pero no entra a ninguna cifra de la página.
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
