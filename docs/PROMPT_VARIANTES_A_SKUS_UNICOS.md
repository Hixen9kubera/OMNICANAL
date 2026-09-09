# Prompt — Separar las variantes en SKUs únicos dentro del panel

> Pégalo tal cual al abrir el chat nuevo. Todo lo que dice está **medido el
> 9-sep-2026** contra producción; los números vienen con su consulta para que
> los puedas volver a comprobar en vez de creerlos.

---

## El encargo

En el panel Omnicanal, un producto variable se ve como **una sola fila: la del
padre**. Quiero que **cada variante sea su propia fila**, con su SKU, su precio,
su stock y su foto — como el SKU único que en realidad es.

Antes de escribir código, léete esto entero. Hay dos trampas medidas que ya
costaron incidentes reales, y una consecuencia que aparece justo al hacer este
cambio.

---

## 1 · Cómo está hoy

El listado del panel consulta **solo los padres**:

```python
# backend/services/woocommerce.py:463
where = ["p.post_type = 'product'", "p.post_status <> 'trash'"]
```

Las variantes (`post_type = 'product_variation'`) **nunca son fila**. Buscar el
SKU de una variante sí funciona, pero no por lo que parece: la caja de búsqueda
traduce variante → padre con `wp_db.expandir_con_padres` y te devuelve al padre
(`woocommerce.py:477-496`). El comentario lo dice: *"un SKU de variante jamás
matchea contra el SKU (más corto) de su padre"*.

**La escala:**

| | filas |
|---|---|
| productos (`product`) | 7,288 · *1,998 publish · 4,380 draft · 782 pending · 128 ready* |
| **variantes** (`product_variation`) | **7,477** · *7,376 publish · 101 draft* |
| padres que tienen variantes | 1,504 |

O sea: **7,477 SKUs reales que hoy no existen como fila.** Y si el padre deja su
lugar a sus hijas, el listado pasa de ~7,288 a ~13,261 filas — casi el doble.

---

## 2 · Qué tiene propio una variante, y qué no

Esto es lo que decide el diseño, así que va medido sobre las 7,477:

| campo | lo tienen propio | **tienen que heredarlo del padre** |
|---|---|---|
| SKU | 7,475 | 2 (no tienen SKU) |
| precio | 6,017 | **1,460** |
| stock | 5,737 | **1,740** |
| miniatura | 6,899 | **578** |
| galería | 795 | **6,682** |
| **descripción** | **0** | **las 7,477** |

**Ninguna variante tiene descripción propia.** Ni una. La fila de una variante
**no se puede armar leyendo solo su post**: siempre hay que ir al padre por algo.

### El patrón ya existe, cópialo

`wp_db.imagenes()` ya resuelve exactamente este problema desde **v0.421.0**: la
miniatura propia de la variante va primero —es la foto de ESE color— y detrás la
galería del padre, sin repetir ids. Léelo (`backend/services/wp_db.py`) antes de
inventar otra herencia; el orden importa y ahí está explicado por qué.

---

## 3 · ⚠️ Trampa medida: NO agrupes por el prefijo del SKU

`variables.py::parse_sku` toma **los dos primeros segmentos** del SKU como
"padre":

```python
parent = f"{parts[0]}-{parts[1]}"     # variables.py:111
```

Eso ya causó daño real. Está escrito en `backend/services/odoo.py:640-645`:

> agrupar por los dos primeros segmentos […] **ya fusionó en WooCommerce 104
> pares `-EST`/`-MET` bajo un solo padre, de los cuales 34 son productos
> DISTINTOS** (un refractómetro con una hebilla de mancuerna, un cierra-puertas
> con una funda de palanca). El prefijo es una PISTA, no un parentesco.

Y en Odoo pasa lo mismo al revés: `JUGU-1153-MET` y `JUGU-1153-MET-B` comparten
prefijo pero viven en **plantillas distintas** (117750 y 117721), así que para
Odoo no son variantes.

**El parentesco es ESTRUCTURAL y ya está en los datos:**

- WooCommerce → `wp_posts.post_parent` + `post_type = 'product_variation'`
- Odoo → `product_tmpl_id`

Nunca lo deduzcas del texto del SKU.

---

## 4 · ⚠️ Lo que este cambio va a destapar: 293 filas rotas

**334 SKUs del catálogo llevan una diagonal dentro** (`CALZ-0194-BLN/AZL-40`,
`TEC-1407-HONDA-15/20`, `ORG-0529-MET/NEG`…). De esos, **293 son variantes.**

Y toda ruta que lleve el SKU **dentro de la ruta** devuelve 404 para ellos.
Probado con el enrutador real:

```
GET /api/crear/costos/TEC-1639-NEG%2FVER   → 404
GET /api/crear/costos/TEC-1639-NEG/VER     → 404
GET /api/crear/costos/TEC-0935-ROS         → 200
```

El frontend **sí** codifica la diagonal con `encodeURIComponent`, pero el
servidor la decodifica antes de enrutar y el parámetro `{sku}` no puede abarcar
un `/`. Afecta a `/api/crear/costos/{sku}`, `/api/productos/{sku}/studio`,
`/api/imagenes/{sku}`, `/api/productos/{sku}/contenido` y compañía.

**Hoy esas 293 están escondidas detrás de su padre.** En cuanto sean fila
propia, van a ser 293 filas visibles que no pueden abrir su ficha, ni sus fotos,
ni su costo — con el síntoma exacto que ya reportó Andrea Pardo: *"no muestra
fotos ni descripción, solo toma el costo"* (el costo sí llega porque el listado
va por query string, no por la ruta).

**Decídelo ANTES de mostrarlas, no después.** Las salidas razonables son: (a)
arreglar el enrutamiento de SKUs con diagonal, (b) mostrarlas marcadas como no
navegables, o (c) sanear esos 334 SKUs. No es una decisión de implementación —
súbesela a Brandon.

---

## 5 · Trampas del SQL que ya están resueltas y hay que respetar

- **398 SKUs tienen más de una fila `_price`** en `wp_postmeta` (dato sucio de
  WordPress, ajeno a Omnicanal). Por eso el listado lee `_stock` y `_price` con
  **subconsulta correlacionada + `MIN(meta_id)`**, no con `LEFT JOIN`: el JOIN
  multiplicaba la fila y duplicaba productos en pantalla (caso COC-0153,
  ago-2026). Cualquier consulta nueva mantiene ese patrón —
  `woocommerce.py:498-508`.
- El filtro de estado sale de `VISTAS`, y **cada pestaña ve lo suyo**: Productos
  `publish/pending/ready`, Crear `draft/inprogress`, Omnicanal todo. Decide en
  cuáles aplica la separación; no es obvio que sea en todas.

---

## 6 · Ayudantes que YA existen — no los reimplementes

En `backend/services/wp_db.py`:

| función | qué da |
|---|---|
| `variantes_por_padre(padres)` | las hijas de cada padre, por lotes |
| `padre_de(wc_id)` | el `post_parent` de una variación (None si no lo es) |
| `sku_padre(sku)` / `skus_padre(skus)` | variante → SKU del padre, por estructura |
| `expandir_con_padres(terminos)` | lo que ya usa la búsqueda |
| `productos_por_sku(skus)` | `{wc_id, tipo, parent_id, stock, costo}` masivo |
| `imagenes(wc_id)` | miniatura propia + galería del padre (v0.421.0) |

---

## 7 · Reglas de la casa que aplican aquí

1. **Busca primero en `conocimientoGeneral`** (rama `conocimiento`, worktree
   `Escritorio\omnicanal-conocimiento`). Es la regla 14 y va antes de construir
   nada nuevo.
2. **`backend/vendor/` NO SE TOCA.** Se ajustan los adaptadores.
3. **Nunca se publica un SKU padre, siempre la variante** (Brandon). Este cambio
   va en la misma dirección, pero ojo: mostrar ≠ publicar.
4. **Odoo es el MASTER del inventario.** Si tocas stock, `free_qty` — no
   `qty_available`, que no descuenta lo comprometido en borradores
   (VIA-0024-NEG: 30 piezas, 29 comprometidas, 1 vendible, y Woo ofrecía 14).
5. **Regla 11:** en una corrutina, nada de red o disco síncrono. `wp_db` es
   pymysql: va en `asyncio.to_thread`.
6. **LiteSpeed cachea chunche.shop**: toda lectura que alimente una escritura
   lleva `_cb`.
7. Versión `+0.1` en `backend/main.py` (dos lugares) y entrada DETALLADA en
   README por cada cambio. `git pull --rebase` antes de push: hay varias
   sesiones trabajando en `main` a la vez.

---

## 8 · Lo que hay que preguntarle a Brandon antes de codificar

1. **¿El padre desaparece del listado, o convive con sus hijas?** Si convive, el
   mismo producto se cuenta dos veces en todos los totales de la pantalla.
2. **¿En qué pestañas?** Productos, Crear y Omnicanal usan `VISTAS` distintas.
3. **Las 293 variantes con diagonal** (sección 4): ¿se arregla el enrutamiento,
   se marcan, o se sanean los SKUs?
4. **~13,261 filas** en vez de ~7,288: ¿paginación, o hay que medir el
   rendimiento del listado primero?

Empieza por medir, confirma estas cuatro con él, y recién entonces escribe.
