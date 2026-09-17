# Prompt — MCP de Omnicanal para research (visitas · ventas por canal · stock libre · cajas)

> Pégalo tal cual al abrir el chat nuevo.
>
> Todo lo que dice está **medido el 17-sep-2026** contra producción (Supabase
> kubera y Odoo). **Re-mide antes de creer**: las coberturas cambian cada semana,
> y varias de ellas son la mitad de la respuesta.

---

## El encargo

Construir un **MCP de Omnicanal** que ayude al research, con cuatro capacidades:

1. **Visitas**, según lo guardado en Supabase.
2. **Ventas por cada canal**, según lo guardado en Supabase.
3. **Stock disponible en bodega — ÚNICAMENTE `free_qty`, sin las reservas.**
4. **Número de cajas y piezas por caja.**

## Regla cero: SOLO LECTURA

La base kubera (`tukwcvsi…`) es **producción operativa**. Desde fuera de la app se
toca **sólo con `SELECT`** (regla de la casa). Este MCP **no escribe nada**: ni una
herramienta de escritura, ni "por si acaso".

⚠️ **Y nunca marques la sesión como read-only.** El DSN va al **pooler en modo
transacción (6543)**, donde las conexiones **se comparten entre clientes**: un
`SET SESSION ... READ ONLY` se queda pegado y lo hereda el siguiente que tome esa
conexión — que puede ser el backend registrando una venta. Ya reventó dos veces.
Si necesitas la garantía: `BEGIN; SET TRANSACTION READ ONLY; …; ROLLBACK;`, o
conéctate al **5432**, o simplemente no marques nada si sólo haces `SELECT`.

---

## 1 · Visitas — está en Supabase, pero **no hay serie diaria**

Dos fuentes, las dos son **fotos que se sobrescriben**:

| Tabla | Grano | Columnas | Medido hoy |
|---|---|---|---|
| `enrich.listing_visits` | **publicación + ventana + cuenta** | `listing_id, dias, cuenta, visitas, dias_datos, consultado_at` | 2,212 filas · `consultado_at` 7-ago → 17-sep |
| `enrich.market_listing_metrics` | **SKU + canal + cuenta + mes** | `sku, canal, cuenta, periodo, listing_id, title, sale_price, list_price, visits_30d, units_30d, estado, fuente_unidades` | 8,165 filas · sólo los periodos **2026-08-01 y 2026-09-01** |

Lo que eso obliga en el diseño:

- **`dias` es la ventana** (7 / 30 / 60), y `dias_datos` dice cuántos días trae de
  verdad. Devolver `visitas` sin esos dos campos es mentir.
- Cada respuesta lleva **`consultado_at`** o **`periodo`**. Una visita sin su fecha
  de medición no se puede comparar con nada.
- `listing_visits` va **por publicación, no por SKU**: para llegar al SKU hay que
  pasar por `channel.listings` (`sku, canal, cuenta, listing_id`).
- **No existe historia día por día.** Si el research la necesita, hay que pedirla a
  ML (`services/competencia_ml.py::visitas_serie`) y hoy **nadie la guarda**.
- Sin dato → **"sin dato"**, nunca `0`. Cero visitas y "no lo medimos" son cosas
  distintas.

---

## 2 · Ventas por canal — Supabase, y la vista ya resuelve lo difícil

**Fuente principal: `channel.sales_daily_completa`** (es una VISTA).

```
date · canal · cuenta · item_id · sku · is_full · units_sold · revenue · sale_fee · fuente
29,339 filas · 27-dic-2025 → 17-sep-2026
```

⚠️ **Esa vista une dos mundos, y por eso trae `fuente`. Exponla siempre.**

| Pieza | Qué es | Cobertura |
|---|---|---|
| `analytics.sales_daily_hist` | el archivo histórico | 17,984 filas · 27-dic-2025 → **15-jul** · **sólo Mercado Libre, sin columna `canal`** |
| `channel.sales_daily` | el flujo vivo | 11,733 filas · desde **16-feb-2026** |

→ **"Ventas por canal" antes del 16-feb-2026 es Mercado Libre o nada.** La
herramienta tiene que decir *"datos desde tal fecha"* en vez de devolver ceros
para Amazon, Walmart, TikTok o Temu en un periodo donde nadie los medía.

Para el detalle por orden: `channel.orders` (35,205 filas, desde 17-feb) y
`channel.order_items` (35,206).

**Tres cosas que se confunden y cambian el número:**

- **`creado_at` de `channel.orders` es la fecha de COMPRA del cliente**, no cuándo
  la procesamos nosotros. No son lo mismo y ya causó un error de lectura.
- **Mercado Libre tiene DOS cuentas**: `BEKURA` = «Kubera» y `SANCORFASHION` =
  «San Corpe» (`core.accounts`: `legacy_code` → `label`). Un total de
  "mercado_libre" sin separar cuenta esconde la mitad de la información.
- **`es_fulfillment` discrepa en 40%** entre `orders` y `order_items`. Si la
  herramienta lo usa, tiene que decir de cuál de las dos lo tomó.
- En Amazon, `item_id` es el **OrderItemId**, no el ASIN.

---

## 3 · Stock libre — ⚠️ **esto NO está en Supabase**

`free_qty` vive **sólo en Odoo** (`product.product`, por XML-RPC). Es exactamente
lo que Brandon pidió: **físico menos lo reservado**.

**Medido hoy:**

| | |
|---|---|
| productos con código en Odoo | 13,189 |
| con `free_qty > 0` | 8,283 |
| **donde `free_qty ≠ qty_available`** (hay reservas) | **160** |

Y ahí está el porqué del encargo:

```
ACC-0069-BEI-VER-XL    físico 100  ·  libre   0
TEC-1355-NEG           físico 100  ·  libre   2
JUGU-0089-PLA          físico 354  ·  libre 203
```

**Las tres trampas:**

1. **NO uses `odoo.stock_por_sku`**, que existe y sería lo cómodo: devuelve
   `qty_available`, **sin descontar reservas**. Reusa `odoo._uid()` / `_models()` y
   pide `free_qty` explícito — el molde exacto está en
   `backend/scripts/publicar_amazon.py::stock_odoo`, con su porqué escrito.
2. **NO leas el stock de WooCommerce ni de `channel.listings.stock_own`.** Son
   copias, y el 17-sep se midió que **58 SKUs tienen `_stock` vacío en Woo** (36 de
   ellos con piezas en Odoo, 4,564 piezas) porque el vigilante salta a los que
   tienen ese campo vacío. Una respuesta basada en Woo habría dicho "0" con 98
   piezas en bodega — eso es literalmente el incidente que destapó todo.
3. **Por almacén** (TEXCO, TEXCO II, DROP OFF) usa
   `services/odoo_ventas.py::libre_por_almacen`. El `free_qty` global no distingue
   almacén, y DROP OFF se suele excluir a propósito.

---

## 4 · Cajas y piezas por caja — Odoo, y las cajas son un **derivado**

**Campo real:** `units_per_master_box` — *«Unidades por caja master»*, entero.
(También existe `cbm_master_box`, el volumen de esa caja.)

**Cobertura medida hoy, sobre 13,189 productos:**

| | |
|---|---|
| con piezas por caja | **9,926 (75.3%)** |
| sin el dato | **3,263** |
| …y de los que lo tienen, **valen 1** | **644** |

**Un factor de 1 no describe una caja: describe la falta del dato.** Trátalo como
ausente.

- **Odoo no registra cajas.** `cajas = piezas ÷ piezas_por_caja` es un **cálculo**.
  Devuélvelo rotulado como estimado, y **null cuando el factor falta o vale 1**.
- ⚠️ `inventario_maestro._cajas` hace ese cálculo pero **con factor 1 devuelve
  cajas = piezas**, al contrario de lo que dice su propio docstring. Si lo reusas,
  corrige eso; no copies el bug.
- ⚠️ `services/packing_cajas.py` son **cartones del packing list de importación**,
  no cajas de envío. No los mezcles: contestan otra pregunta.

---

## Cómo conectarse (ya existe, no lo reinventes)

| Qué | Dónde | Ojo |
|---|---|---|
| Supabase kubera | `SUPABASE_DB_URL` · `services/supabase_db.py` (pool + reintentos, `fetch_all`) | pooler 6543: conexiones compartidas (regla cero) |
| Odoo | `ODOO_URL/DB/USER/PASSWORD` · `services/odoo.py::_uid/_models` | `ir.model.fields` **no** es legible con este usuario: usa `fields_get` |
| Traducir cuentas | `core.accounts` | `legacy_code` → `label` |

⚠️ **Dos proyectos de Supabase, y el `.env` local mezcla:** `SUPABASE_URL` y
`SUPABASE_SERVICE_ROLE_KEY` apuntan a **analytics** (`xaxbkijc…`), mientras
`SUPABASE_DB_URL` apunta a **kubera** (`tukwcvsi…`). Todo lo de este MCP vive en
kubera, por conexión directa a Postgres — no por la API REST.

**Regla 11 de la casa:** en una corrutina, nada síncrono. `psycopg2` y el
`xmlrpc` de Odoo van en `asyncio.to_thread`.

⚠️ **No importes la app del backend** para reusar sus servicios: al arrancar,
**el scheduler escribe en producción** contra el `.env` de prod. Importa módulos
de `services/` sueltos, nunca `main.py`.

---

## Forma sugerida del MCP

- **Python** (el repo lo es), SDK oficial de MCP, servidor **stdio** para Claude
  Desktop / Claude Code.
- **Una herramienta por pregunta**, con parámetros `sku` / `canal` / `cuenta` /
  `desde` / `hasta` y **un límite obligatorio**: el catálogo tiene 13,189 SKUs y
  una respuesta sin tope se come el contexto.
- **Cada respuesta lleva su metadato**: `fuente`, `cobertura` (desde → hasta) y
  `medido_en`. Un número sin eso no sirve para research.
- **Ninguna herramienta de escritura.** Ni a Supabase, ni a Odoo, ni a Woo, ni a
  los marketplaces.
- Vive **fuera de `backend/`** o en `backend/mcp/`, y **no toca `backend/vendor/`**.
- En la organización hay un repo viejo llamado **`MCPPruebaWOO`**: vale la pena
  abrirlo antes de empezar, por si sirve de molde. (No lo revisé.)
- **Busca primero en `conocimientoGeneral`** (regla 14, rama `conocimiento`).

---

## Lo que hay que preguntarle a Brandon antes de codificar

1. **¿Research de qué, exactamente?** Resurtido, precios, qué publicar, qué
   descontinuar — cada uno pide cruces distintos. Con eso se decide si son cuatro
   herramientas sueltas o una que ya entrega el cruce armado.
2. **¿Visitas por publicación o por SKU?** Un SKU tiene hasta dos publicaciones de
   ML (Kubera y San Corpe) y sumarlas o no cambia la respuesta.
3. **¿Sólo lo guardado, o puede preguntar en vivo?** Él dijo *"según los datos
   guardados en BD supabase"* para visitas y ventas — pero **stock libre y cajas
   obligan a llamar a Odoo**, porque no están en Supabase. Conviene que quede
   explícito.
4. **¿Ventas en piezas, en dinero, o las dos?** ¿`revenue` bruto o descontando
   `sale_fee`?
5. **¿Quién lo va a usar y desde dónde?** Si se instala en el Claude de cada KAM,
   las credenciales de producción de Supabase y Odoo terminan en cada laptop. Es
   una decisión de seguridad, no de implementación: quizá convenga un servidor
   central con una llave por persona.
