# `catalogo_vivo.py` — el catálogo de Odoo cruzado EN VIVO contra los marketplaces

> Vive en la rama `conocimiento`. **No está en `main`, así que no puede llegar a
> producción**: Railway solo despliega desde `main`.
> Escrito el 6-oct-2026 a pedido de Brandon. Lógica de lectura copiada de
> producción (`origin/main` v0.618.0) y del laboratorio de precios (`sandbox/precios-optimos`).

Le preguntas a cada sistema qué tiene **ahora**, y te deja una página web con una
fila por producto de Odoo: foto, título, packing list, stock libre, costo de
producto, prorrateo del contenedor, y en qué canales está publicado, a qué precio
y con qué comisión.

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
| `amazon` | SP-API | `searchListingsItems` por ventanas de fecha + `getMyFeesEstimates` | 3 min |
| `walmart` | Walmart MX | `GET /v3/items` paginado | 10 s |
| `ml` | Mercado Libre | scan + `/items` + (activas) `sale_price`, `listing_prices`, `shipping_options/free` | 8 min |
| `tiktok` | TikTok Shop | `products/search` + detalle para la foto | 1 min |
| `temu` | Temu | `bg.local.goods.list.query` — **solo desde la IP de producción** (ver abajo) | — |
| `imagenes` | CDNs públicos | baja las miniaturas y arma las hojas | 1 min |
| `pagina` | lo anterior | cruza por SKU y escribe la página | 2 s |

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

- **El costo oficial es `costo_producto` de la base**, no el de la ficha de Odoo.
  La página lo pinta en dorado y solo los productos con renglón propio entran al
  total. Lo heredado y lo extrapolado se suman aparte, con su nombre.
- **La ficha de Odoo (`standard_price`) no se usa para costear.** Coincide con la
  base en tres de cada cuatro productos y en el resto viene en otra unidad.
- **Una variante puede estar publicada con el SKU de su padre.** Mercado Libre
  publica sin variaciones; a la variante se le cuelga esa publicación marcada con ↑.
- **El precio de Mercado Libre es el que se cobra hoy** (`sale_price`), con la
  promoción aplicada, no el de lista.
- **Comisión real vs supuesta.** ML y Amazon la calculan por API. Walmart, TikTok y
  Temu no: la página muestra un porcentaje supuesto y lo dice.

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
