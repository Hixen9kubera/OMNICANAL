# MCP de research — Omnicanal · Kubera

Servidor MCP **de sólo lectura** que le da a Claude cuatro datos del negocio
para hacer research. No publica, no cambia precios, no mueve stock, no escribe
en Woo, kubera, Odoo ni en ningún marketplace. **No tiene una sola herramienta
de escritura, ni "por si acaso".**

| Herramienta | Contesta | Fuente |
|---|---|---|
| `visitas` | Tráfico de las publicaciones, por publicación y sumado por SKU | `enrich.listing_visits` + `enrich.market_listing_metrics` (Supabase kubera) |
| `ventas` | Piezas, bruto, comisión y neto por canal / cuenta / SKU / día | `channel.sales_daily_completa` (Supabase kubera) |
| `stock_libre` | Lo que de verdad se puede prometer: `free_qty`, sin las reservas | Odoo en vivo (XML-RPC) |
| `cajas` | Piezas por caja master y cuántas cajas son esas piezas | Odoo en vivo (XML-RPC) |

Cada respuesta trae `fuente`, `cobertura` y `medido_en`. Un número sin eso no
sirve para research.

---

## Las cuatro cosas que este servidor se niega a hacer

**1. No escribe.** El candado vive en `conexion.py::_verificar_lectura`: la
consulta tiene que empezar en `SELECT`/`WITH`, no puede encadenar sentencias, y
no puede contener ningún verbo de escritura **en ninguna parte**. Esa última
regla se ganó a pulso: la primera versión sólo miraba el primer token, y

```sql
with x as (update core.products set sku='x' returning 1) select * from x
```

pasaba de largo — llegó a producción en una prueba y sólo rebotó de suerte,
contra una restricción de unicidad. Nada se escribió, pero el candado no tuvo
nada que ver con eso. Un candado que depende de la suerte no es un candado.

**2. No marca la sesión de solo-lectura.** El DSN va al pooler en modo
transacción (6543), donde las conexiones del servidor **se comparten entre
clientes**: un `set_session(readonly=True)` se queda pegado y lo hereda el
siguiente que tome esa conexión — que puede ser el backend registrando una
venta. Reventó dos veces (12-ago y 17-19-ago de 2026). Aquí sólo se hace
`SELECT`, así que no hace falta marcar nada; verificado con
`show default_transaction_read_only` sobre seis conexiones tras cada prueba.

**3. No importa `main.py`.** Al arrancar, el backend levanta su scheduler y
ése sí escribe en producción contra el `.env` de prod. Aquí se importan módulos
sueltos de `services/`, que son inertes al importarse.

**4. No lee el stock de WooCommerce.** `stock_libre` va a Odoo porque Woo es
una copia, y el 17-sep-2026 se midió que 58 SKUs tienen `_stock` vacío ahí, 36
de ellos con piezas reales en bodega (4,564 piezas). Una respuesta basada en
Woo habría contestado "0" con 98 piezas en el rack.

---

## Instalar

Venv **propio**, separado del backend, y no es manía: el SDK de MCP arrastra
`sse-starlette`, que exige `starlette >= 1.6`, y el backend está clavado en
`fastapi 0.115.6`, que exige `starlette < 0.42`. No pueden convivir. Como este
servidor sólo importa `config`, `services.odoo` y `services.supabase_db`
—ninguno necesita fastapi—, separarlos sale gratis.

```bash
python -m venv backend/mcp_research/.venv
backend/mcp_research/.venv/Scripts/pip install -r backend/mcp_research/requirements.txt
```

Las credenciales salen del `.env` de la raíz del repo, el mismo que usa el
backend: `SUPABASE_DB_URL` (kubera, **no** el proyecto de analytics) y
`ODOO_URL` / `ODOO_DB` / `ODOO_USER` / `ODOO_PASSWORD`. Si falta alguna, el
servidor **no arranca** y lo dice — mejor que contestar "no pude" en cada
pregunta.

## Correr en local (stdio)

```bash
cd backend && mcp_research/.venv/Scripts/python -m mcp_research.server --transport stdio
```

En stdio no se pide llave: el servidor es un proceso hijo de quien lo lanzó, no
hay red de por medio.

Para engancharlo a Claude Desktop o Claude Code, en el archivo de
configuración de MCP:

```json
{
  "mcpServers": {
    "omnicanal-research": {
      "command": "C:\\Users\\diaz2\\OneDrive\\Escritorio\\omnicanal\\backend\\mcp_research\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_research.server", "--transport", "stdio"],
      "cwd": "C:\\Users\\diaz2\\OneDrive\\Escritorio\\omnicanal\\backend"
    }
  }
}
```

## Correr central (HTTP, con llave)

```bash
MCP_AUTH_TOKEN=... python -m mcp_research.server --transport http --port 8080
```

* `GET /salud` — abierto, sin llave. Dice si Supabase y Odoo están configurados.
* `POST /mcp` — Streamable HTTP. Exige `Authorization: Bearer <llave>`; sin ella
  o con una mala, **401**. La comparación va con `hmac.compare_digest`.

**Sin `MCP_AUTH_TOKEN` el servidor no arranca en modo http.** Un servidor
abierto a internet con el DSN de producción adentro no puede ser el
comportamiento por omisión.

`MCP_AUTH_TOKEN` acepta **varias llaves separadas por coma**. Hoy es una sola y
compartida (decisión de Brandon, 17-sep-2026); vale la pena tener presente que
una llave compartida no se puede revocar por persona — cortarle el acceso a
alguien obliga a rotarla para todos. El día que eso estorbe, es añadir una
llave a la lista, no rehacer el servidor.

## Desplegar en Railway

`backend/railway.mcp-research.json` deja el servicio listo: `rootDirectory` =
`backend`, y arranca con el transporte http. **Falta crear el servicio y darle
las variables** — eso no se hizo sin el dale de Brandon.

Un detalle del build: Nixpacks instala solo `backend/requirements.txt` en la
fase de instalación, y el `buildCommand` mete después el del MCP. El resultado
es una imagen donde `fastapi` queda con una versión de starlette que no le
sirve — **da igual, este servicio nunca lo importa**, pero conviene saberlo
antes de asustarse con el aviso de pip en el log.

---

## Lo que se midió, porque cambia cómo se leen las respuestas

Todo del **17-sep-2026**.

### Visitas: no hay serie diaria

Las dos fuentes son **fotos que se sobrescriben**. `dias` es la ventana pedida
—**7, 30, 60 y 90**— y `dias_datos` cuántos días trae de verdad: 2,727 visitas
en una ventana de 60 con 32 días de datos no es lo mismo que 2,727 en 60 de 60.
Por eso las dos columnas van siempre pegadas a la cifra.

`listing_visits` va por publicación, no por SKU. Y **93 publicaciones de ML
sirven a DOS SKUs** (de 8,667 listing_id distintos), varias con visitas: ML las
cuenta una vez, para el anuncio. Sumarlas a cada SKU las duplica, dividirlas a
la mitad las inventa. Aquí se suman y se marca cuánto viene de publicaciones
compartidas (`visitas_en_listings_compartidos`).

Si hace falta la historia día por día, no está aquí: hay que pedírsela a ML
(`services/competencia_ml.py::visitas_serie`) y hoy nadie la guarda.

### Ventas: la cobertura no es pareja, y Walmart no es cero

```
mercado_libre  hist   2025-12-27 → 2026-07-15    60,325 piezas
mercado_libre  vivo   2026-07-16 → 2026-09-17    35,018 piezas
amazon         vivo   2026-07-16 → 2026-09-17       451 piezas
temu           vivo   2026-08-10 → 2026-09-17        91 piezas
tiktok         vivo   2026-08-12 → 2026-09-16       163 piezas
walmart               SIN UNA SOLA FILA
```

Preguntar por marzo devuelve Mercado Libre y nada más. La herramienta lo **dice**
(`cobertura`, `canales_sin_datos`) en vez de contestar 0 para los demás: un 0 se
lee como "no vendió", y la verdad es "no se midió".

**Walmart MX sí vende** — lo que falta es la ingesta, no las ventas. Nunca se
reporta como 0.

Mercado Libre son **dos cuentas**: `BEKURA` = «Kubera» y `SANCORFASHION` = «San
Corpe». Un total de `mercado_libre` sin separar cuenta esconde la mitad de la
información. (Rareza real: 44 renglones traen `cuenta='AMAZON'` bajo
`canal='mercado_libre'`.) En Amazon, `item_id` es el **OrderItemId**, no el ASIN.

`diagnostico=true` mide en vivo los renglones que `channel.sales_daily` tiene y
la vista `completa` no expone, porque la vista corta por fecha y el flujo vivo
empezó antes del corte: 11 de Amazon (2→14-jul) y 171 de ML (16-feb→15-jul).

### Stock: `free_qty`, nunca `qty_available`

```
productos con código en Odoo .................. 13,189
con free_qty > 0 ............................... 8,283
donde free_qty ≠ qty_available (hay reservas) .... 160
```

Esos 160 son el motivo del encargo. `TEC-1355-NEG`: **físico 100, libre 2**.

No se usa `odoo.stock_por_sku`, que sería lo cómodo y devuelve `qty_available`.
Por almacén tampoco se usa `odoo_ventas.libre_por_almacen`: esa función deja
**DROP OFF fuera a propósito** (para planear envíos está bien), y aquí hay que
poder enseñarlo. Los almacenes se leen vivos de Odoo — hoy TEXCO (135), DROP OFF
(142) y TEXCO II (150); PAROLERA (143) está archivado.

`libre` puede salir **negativo**. Eso no es escasez: es que se descontaron
piezas que nunca se dieron de alta. Con `orden='libre_asc'` son justamente los
primeros que aparecen, y la respuesta lo avisa.

### Cajas: son un derivado, y el factor 1 es una trampa

Odoo **no cuenta cajas**. Lo único vivo es `units_per_master_box`:

```
con piezas por caja .... 9,926 (75.3%)
sin el dato ............ 3,263
…de los que lo tienen, 644 valen 1
```

Un factor de 1 no describe una caja, describe la **falta del dato**. Aquí sale
`null`, no "una caja por pieza".

> ⚠️ `inventario_maestro._cajas` hace la misma cuenta con la guarda `f < 1`, así
> que con factor 1 pasa de largo y devuelve las piezas — al revés de lo que
> promete su propio docstring. Aquí la guarda es `f <= 1`. **El bug no se copió
> y aquel archivo no se tocó**: es del panel y tiene su propio contrato.

Esto tampoco es el packing list: `services/packing_cajas.py` son cartones de
importación —lo que el proveedor embarcó— y contestan otra pregunta. Aquí manda
Odoo, que dice lo que **hay**.

---

## Topes

Ninguna respuesta viene sin límite: el catálogo tiene ~13,200 SKUs y las ventas
29 mil renglones. `limite` va a 50 por omisión y topa en **500**; las listas de
SKUs topan en 500 y Odoo se pide en lotes de 200 por dentro. **Cuando una
respuesta se corta, lo dice** — truncar en silencio es cómo se saca una
conclusión de media tabla creyendo que era la tabla entera.

`stock_libre(todos=true)` barre el catálogo completo: ~24 s la primera vez, y
luego 10 minutos de caché.
