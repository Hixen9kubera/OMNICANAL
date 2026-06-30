# 🛰️ OMNICANAL · Kubera

Panel omnicanal para gestionar y visualizar las publicaciones de **WooCommerce**
(tu centro) y su estado en cada **marketplace**: Mercado Libre (principal),
Amazon, TikTok Shop, Walmart, Temu y Shein.

> **Centro de verdad:** WooCommerce (`chunche.shop`) · **Inventario:** Odoo ·
> **Cache de marketplaces:** MySQL · **Vínculo entre todo:** el **SKU**.

---

## 📑 Tabla de contenido

1. [Qué hace](#-qué-hace)
2. [Arquitectura](#-arquitectura)
3. [Estructura de carpetas](#-estructura-de-carpetas)
4. [Fuentes de datos y modelo](#-fuentes-de-datos-y-modelo)
5. [Canales y colores](#-canales-y-colores)
6. [API del backend](#-api-del-backend)
7. [Cómo correr en local](#-cómo-correr-en-local)
8. [Variables de entorno](#-variables-de-entorno)
9. [Deploy en Railway](#-deploy-en-railway)
10. [Subir a GitHub](#-subir-a-github)
11. [Qué se construyó (bitácora)](#-qué-se-construyó-bitácora)
12. [Pendientes y estrategias propuestas](#-pendientes-y-estrategias-propuestas)

---

## ✅ Qué hace

- **Vista GENERAL**: lista las **3,834** publicaciones de WooCommerce, de **40 en
  40**, con paginación **arriba y abajo**.
- **Pestañas por marketplace** con su **color de marca**; al seleccionar una, toda
  la interfaz cambia de color.
- **Mercado Libre con 2 cuentas**: sub-botones **Kubera** (default) y **San Corpe**
  (+ "Todas"), cada una con su propio conteo.
- Por cada producto y canal se muestra: **precio**, **stock**, **categoría con
  todos sus niveles**, si tiene **FULL/FBA**, estado de publicación y link.
- **Buscador** por SKU/nombre, filtro **"solo publicados"**, y **detalle 360°**
  (un panel que muestra el producto en todos los canales a la vez, con botón de
  **refrescar en vivo** contra la API de ML/Amazon).
- **Navbar superior** de la app (Dashboard, Productos, Omnicanal, Canales, Ventas,
  Facturas, Reportes, Automatización). Solo **Omnicanal** está activo; el resto se
  muestra como **"próximamente"**.

---

## 🏗 Arquitectura

```
┌────────────────────┐        HTTP/JSON        ┌──────────────────────┐
│   Next.js (App      │  ───────────────────▶  │   FastAPI (backend)   │
│   Router + TS +     │  ◀───────────────────  │   /api/productos …    │
│   Tailwind)         │                         └─────────┬────────────┘
│   :3000             │                                   │
└────────────────────┘                ┌──────────────────┼───────────────────┐
                                       ▼                  ▼                   ▼
                               WooCommerce REST     MySQL (cache)        APIs marketplaces
                               (GENERAL, 3,834)   ml_progress, costos,   ML /items, Amazon
                                                  amazon_progress…       SP-API (refresco)
                                       ▲
                                       │
                                     Odoo (XML-RPC, stock real)
```

- **Backend (FastAPI)**: expone la API, aplica la **estrategia híbrida** (lee del
  cache MySQL para que la UI vuele y refresca contra la API en vivo bajo demanda).
- **Frontend (Next.js)**: interfaz profesional, temática por canal, paginada.

---

## 📂 Estructura de carpetas

```
omnicanal/
├── backend/                      # FastAPI
│   ├── main.py                   # app, CORS, routers, health check
│   ├── config.py                 # lee .env y .env.amazon (pydantic-settings)
│   ├── requirements.txt
│   ├── railway.json / Procfile   # deploy
│   ├── core/
│   │   └── marketplaces.py       # registro de canales: ids, colores, cuentas ML
│   ├── models/
│   │   └── schemas.py            # contratos Pydantic de la API
│   ├── routers/
│   │   ├── productos.py          # GET /api/productos (paginado 40, por canal)
│   │   ├── canales.py            # GET /api/canales + refresco en vivo
│   │   ├── ia.py                 # POST /api/ia/titulo (Claude)
│   │   └── auth.py               # placeholder de sesión
│   └── services/
│       ├── db.py                 # conexión MySQL
│       ├── woocommerce.py        # cliente WooCommerce (GENERAL) + categorías
│       ├── meli.py               # Mercado Libre (cache DB + refresco; 2 cuentas)
│       ├── amazon.py             # Amazon SP-API (cache DB + LWA + refresco)
│       ├── ejemplos.py           # datos de muestra (TikTok/Walmart/Temu/Shein)
│       ├── odoo.py               # Odoo XML-RPC (stock real)
│       ├── claude.py             # generación de listings con IA
│       └── presencia.py          # "puntos de colores": en qué canales está cada SKU
│
├── frontend/                     # Next.js (App Router + TS + Tailwind)
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx              # redirige a /omnicanal
│   │   ├── globals.css
│   │   └── omnicanal/page.tsx    # página principal (estado + theming)
│   ├── components/
│   │   ├── AppNavbar.tsx         # navbar superior (Omnicanal activo, resto "pronto")
│   │   ├── MarketplaceTabs.tsx   # pestañas con color de marca
│   │   ├── AccountTabs.tsx       # sub-cuentas de Mercado Libre
│   │   ├── ProductGrid.tsx       # grid 40/pág + skeleton
│   │   ├── ProductCard.tsx       # tarjeta de producto
│   │   ├── ChannelDots.tsx       # puntos de presencia por canal (GENERAL)
│   │   ├── Pagination.tsx        # paginación (arriba y abajo)
│   │   └── ProductDetailDrawer.tsx # detalle 360° por canal + refrescar
│   ├── lib/
│   │   ├── api.ts                # cliente del backend
│   │   ├── types.ts             # tipos (espejo de schemas.py)
│   │   └── theme.ts             # colores/variables por canal
│   ├── package.json
│   ├── tailwind.config.ts
│   └── railway.json
│
├── .env / .env.amazon            # credenciales reales (NO se suben a git)
├── .env.example / .env.amazon.example
├── .gitignore
└── README.md
```

---

## 🗃 Fuentes de datos y modelo

| Canal | Fuente | Tablas / endpoints |
|---|---|---|
| **GENERAL** | WooCommerce REST en vivo | `/wp-json/wc/v3/products` (+ categorías) |
| **Mercado Libre** | Cache MySQL + API | `productos` + `ml_progress` + `costos_finales` (+ `/items/{id}`) |
| **Amazon** | Cache MySQL + SP-API | `productos` + `amazon_progress` (+ Listings API) |
| **TikTok/Walmart/Temu/Shein** | Datos de ejemplo | derivados de `productos` |

**Vínculo por SKU.** La tabla `productos` (≈4,944 filas) es el puente maestro:
`sku ↔ wc_id ↔ odoo_id`, con nombre, precio, `stock_odoo`, categorías, etc.

**Mercado Libre — 2 cuentas** (columna `ml_progress.cuenta` y tabla `ml_tokens`):

| Cuenta interna | Etiqueta UI | Publicados |
|---|---|---|
| `BEKURA` | **Kubera** (default) | 1,595 |
| `SANCORFASHION` | **San Corpe** | 1,563 |

**FULL / FBA.** En Mercado Libre el "FULL" se detecta por
`shipping.logistic_type == "fulfillment"`; en Amazon, por canal de cumplimiento
FBA. Se completa al usar **refrescar en vivo** en el detalle del producto.

---

## 🎨 Canales y colores

| Canal | Color | Estado |
|---|---|---|
| General | Índigo `#4F46E5` | ✅ Activo (WooCommerce) |
| Mercado Libre | Amarillo `#FFE600` / azul `#2D3277` | ✅ Activo (2 cuentas) |
| Amazon | Naranja `#FF9900` / navy `#232F3E` | ✅ Activo |
| TikTok Shop | Negro / rosa `#FE2C55` | ⏳ Próximamente |
| Walmart | Azul `#0071DC` / amarillo `#FFC220` | ⏳ Próximamente |
| Temu | Naranja `#FB7701` | ⏳ Próximamente |
| Shein | Negro / violeta `#7C3AED` | ⏳ Próximamente |

Los colores viven en `backend/core/marketplaces.py` (fuente única); el frontend
los consume desde `/api/canales`, así que cambiarlos ahí actualiza toda la UI.

---

## 🔌 API del backend

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/health` | Estado de WooCommerce, DB y Odoo |
| GET | `/api/canales` | Config de canales (colores, totales, subcuentas) |
| GET | `/api/productos?canal=&page=&per_page=40&search=&solo_publicados=&cuenta=` | Lista paginada por canal |
| GET | `/api/productos/{sku}` | Detalle 360° del SKU en todos los canales |
| POST | `/api/canales/{canal}/refrescar/{sku}?cuenta=` | Refresca precio/stock/FULL en vivo |
| POST | `/api/sync/leer?canal=&cuenta=&limite=` | Lee inventario en vivo y llena el cache `canal_inventario` |
| GET | `/api/sync/plan?limite=` | Plan de sincronización en **modo simulación** (dry-run) |
| GET | `/api/sync/estado` | Resumen del cache: SKUs por canal, totales real/FULL/FBA |
| POST | `/api/ia/titulo` | Genera título optimizado con Claude |

Documentación interactiva: **`/docs`** (Swagger UI).

---

## 💻 Cómo correr en local

> Requisitos: **Python 3.12+** y **Node 18+**. Las credenciales ya están en
> `.env` y `.env.amazon` en la raíz.

### ⭐ Comando único (recomendado)

Un solo comando hace el setup (si falta) y levanta **backend + frontend** juntos:

```powershell
.\dev.ps1
```

- Backend → **http://localhost:8000** (`/docs` para la API)
- Frontend → **http://localhost:3000**

`Ctrl+C` detiene ambos. La primera vez crea el entorno de Python e instala las
dependencias automáticamente.

> Alternativa multiplataforma con npm (requiere `npm install` en la raíz una vez,
> y que el venv del backend ya exista):
> ```bash
> npm install      # instala 'concurrently' (solo la primera vez)
> npm run dev      # levanta backend + frontend a la vez
> ```

### Arranque manual (dos terminales)

Si prefieres correrlos por separado:

```powershell
# Terminal 1 — backend
cd backend; python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt; uvicorn main:app --reload --port 8000
```
```powershell
# Terminal 2 — frontend
cd frontend; npm install; npm run dev
```

Abre **http://localhost:3000** → redirige a **/omnicanal**.

---

## 🔑 Variables de entorno

Todas están documentadas en **`.env.example`** y **`.env.amazon.example`**.
Resumen de las que usa el backend:

- **Odoo**: `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD`
- **WooCommerce**: `WC_URL`, `WC_CONSUMER_KEY`, `WC_CONSUMER_SECRET`
- **MySQL**: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- **IA**: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, …
- **Amazon** (`.env.amazon`): `AMAZON_LWA_CLIENT_ID`, `AMAZON_LWA_CLIENT_SECRET`,
  `AMAZON_REFRESH_TOKEN`, `AMAZON_SELLER_ID`, `AMAZON_MARKETPLACE_ID`
- **App**: `CORS_ORIGINS` (orígenes del frontend, coma-separados)

Frontend: `NEXT_PUBLIC_API_URL` → URL pública del backend.

---

## 🚂 Deploy en Railway

Es un **monorepo con 2 servicios** (backend y frontend). En Railway se crean dos
servicios desde el mismo repo, cada uno con su **Root Directory**.

### Servicio 1 — Backend (`backend/`)
1. New Service → Deploy from GitHub → repo `OMNICANAL`.
2. **Settings → Root Directory** = `backend`.
3. **Variables**: pega TODO lo del `.env` y `.env.amazon` (Odoo, WooCommerce, DB,
   IA, Amazon…). Agrega `CORS_ORIGINS` con la URL del frontend.
4. Railway detecta `railway.json`:
   - Build: `NIXPACKS` (instala `requirements.txt`)
   - Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Healthcheck: `/api/health`

### Servicio 2 — Frontend (`frontend/`)
1. New Service → mismo repo.
2. **Settings → Root Directory** = `frontend`.
3. **Variables**: `NEXT_PUBLIC_API_URL` = URL pública del **backend** (ej.
   `https://omnicanal-backend.up.railway.app`).
4. Railway detecta `railway.json`:
   - Build: `npm run build`
   - Start: `npm run start` (Next lee `PORT` automáticamente)

> **Importante:** `NEXT_PUBLIC_API_URL` se "hornea" en build, así que si cambias la
> URL del backend, vuelve a desplegar el frontend.

### Comandos equivalentes con Railway CLI

```bash
npm i -g @railway/cli
railway login
railway link            # vincula al proyecto

# Backend
railway up --service backend

# Frontend
railway up --service frontend
```

---

## 🐙 Subir a GitHub

El repositorio destino es **https://github.com/Hixen9kubera/OMNICANAL.git**.
Los `.env*` reales están en `.gitignore`, así que **no se suben credenciales**.

```bash
cd "ruta/al/omnicanal"
git init
git add .
git commit -m "OMNICANAL: backend FastAPI + frontend Next.js (v1)"
git branch -M main
git remote add origin https://github.com/Hixen9kubera/OMNICANAL.git
git push -u origin main
```

> Si el repo ya tenía commits, usa `git pull --rebase origin main` antes del push,
> o `git push -u origin main --force` si quieres reemplazar su contenido.

---

## 📝 Qué se construyó (bitácora)

Sesión de construcción (resumen de decisiones y trabajo):

1. **Exploración**: se leyeron `.env` y `.env.amazon`; se confirmó conectividad con
   WooCommerce (3,834 productos), MySQL (21 tablas) y Odoo.
2. **Decisiones de arquitectura** (acordadas contigo):
   - GENERAL = **WooCommerce**.
   - Datos por canal = **híbrido** (cache DB + refresco en vivo).
   - Vínculo entre canales = **SKU**.
   - Marketplaces sin credenciales = **pestañas con datos de ejemplo**.
3. **Backend FastAPI**: config, registro de canales, modelos, servicios (WooCommerce,
   MySQL, Mercado Libre, Amazon, Odoo, Claude, ejemplos, presencia) y routers
   (productos, canales, ia, auth). Health check verde (WooCommerce/DB/Odoo).
4. **Mercado Libre con 2 cuentas** (BEKURA=Kubera default, SANCORFASHION=San Corpe):
   filtro por cuenta, conteos por cuenta y token por cuenta.
5. **Frontend Next.js**: navbar superior (Omnicanal activo, resto "próximamente"),
   pestañas con color de marca, sub-cuentas de ML, grid de 40 con paginación
   arriba/abajo, buscador, filtro "solo publicados", y **detalle 360°** con
   refresco en vivo. Build de producción sin errores de TypeScript.
6. **Deploy**: `railway.json`/`Procfile` para ambos servicios, `.gitignore` que
   protege los secretos y plantillas `.env.example`.

---

## 🔄 Sincronización de inventario (v0.1)

El objetivo central de OMNICANAL: mantener el inventario **sincronizado entre
canales**. Implementado en esta versión.

### Modelo de stock

```
STOCK TOTAL = stock_real + stock_full (ML) + stock_fba (Amazon)
```

- **`stock_real`** → unidades en TU almacén (vendidas por ti / Flex / FBM).
  **Es lo único que se sincroniza** entre Woo + ML(no-FULL) + Amazon(FBM).
- **`stock_full`** → bodega de Mercado Libre (FULL). Solo se muestra, no se toca.
- **`stock_fba`** → bodega de Amazon (FBA). Solo se muestra, no se toca.
- **Fuente de verdad** del `stock_real`: **Odoo** (`qty_available`).

### Tabla cache `canal_inventario`

`sku · canal · cuenta · item_id · precio · stock_real · stock_full · stock_fba ·
es_full · logistica · situacion · updated_at` (PK: `sku, canal, cuenta`).
Se crea sola al arrancar. La UI lee de aquí (rápida) y muestra el desglose en
tarjetas y en el detalle 360°.

### Cómo funciona

1. **Lector** (`services/inventario.py`): consulta en vivo cada canal y guarda en
   `canal_inventario`.
   - **Mercado Libre**: desencripta el token de `ml_tokens` (Fernet con
     `DB_ENCRYPTION_KEY`) y llama `/items/{id}` → precio, `available_quantity`,
     `logistic_type` (`fulfillment` ⇒ FULL), `status`.
   - **Amazon**: LWA + `/fba/inventory/v1/summaries` → `fulfillableQuantity` (FBA).
2. **Programación** (`services/scheduler.py`): APScheduler corre el lector cada
   `SYNC_INTERVAL_MIN` (15 por defecto). Configurable con variables de entorno.
3. **Escritura (dry-run)**: `GET /api/sync/plan` compara el maestro (Odoo) contra
   el `stock_real` cacheado de cada canal y devuelve **qué cambiaría**, sin
   escribir nada. La escritura en vivo se activará tras revisar el plan.

### De polling a Webhooks (siguiente paso)

El sync cada 15 min es el método inicial. Para tiempo real se usan webhooks; al
activarlos se pone `SYNC_ENABLED=false` y se apaga el polling:

- **Mercado Libre — Notifications**: en la app de ML, configurar el *callback URL*
  (ej. `https://backend.../api/webhooks/ml`) y suscribirse a los *topics*
  `items` y `orders_v2`. ML hará `POST` con `{resource, topic, user_id}` cada vez
  que cambie un ítem o entre una venta → el backend relee ese ítem y actualiza
  `canal_inventario` + propaga el `stock_real`.
- **Amazon — SP-API Notifications**: suscribirse (vía la Notifications API + AWS
  SQS) a `ANY_OFFER_CHANGED` y `FBA_INVENTORY_AVAILABILITY_CHANGES`. Amazon
  publica en una cola SQS; un consumidor lee y actualiza el cache.
- **WooCommerce — Webhooks**: en WooCommerce → Ajustes → Avanzado → Webhooks,
  crear uno de `Product updated` apuntando a `/api/webhooks/woo`.

> Pendiente de implementar el endpoint `/api/webhooks/*` y, en el caso de Amazon,
> el consumidor de la cola SQS. La lógica de relectura por SKU ya existe
> (`inventario.sincronizar_*`), así que el webhook solo dispara esa función.

### Devoluciones (situación por canal)

Se modeló el campo `situacion` por canal (ej. ML `active/paused`, Amazon
`PUBLISHED/INVALID`). El caso de **devolución** (un producto que bajó stock y se
restaura al llegar a Odoo) se lee de la API de órdenes/claims de cada canal y se
reflejará en `situacion` por canal en una próxima iteración.

---

## 🧾 Versión 0.1 — registro de implementación

**Fecha:** 30 jun 2026. Construido sobre la v1 base (FastAPI + Next.js).

Añadido en esta versión:
- 🖼️ **Imágenes** en todos los canales (se toman de WooCommerce por lote vía `wc_id`).
- 💰 **Precio real por tienda** y 📦 **desglose de stock** (real / FULL / FBA) en
  tarjetas y en el detalle 360°.
- 🔐 **Desencriptado de tokens de Mercado Libre** (Fernet) para lectura en vivo.
- 🗃️ Tabla **`canal_inventario`** como cache de inventario por canal y cuenta.
- 🔄 **Lector de inventario en vivo** (ML por cuenta, Amazon FBA) + endpoints
  `/api/sync/*`.
- ⏱️ **Sincronización programada cada 15 min** (APScheduler), apagable con
  `SYNC_ENABLED=false`.
- 🧪 **Plan de sincronización en modo simulación** (`/api/sync/plan`): Odoo → canales.
- 🏷️ Campo **`situacion`** por canal (estatus del listing).

Nuevas variables de entorno (backend): `DB_ENCRYPTION_KEY`, `SYNC_ENABLED`,
`SYNC_INTERVAL_MIN`, `SYNC_BATCH`.

---

## 🧾 Versión 0.11 — correcciones y mejoras de UX

**Fecha:** 30 jun 2026. Sobre la v0.1.

**Errores corregidos:**
- 🐛 **500 al abrir el detalle** de algunos productos: un error de red (TLS) de
  `httpx` no se capturaba. Ahora `obtener_producto_por_sku` y todo el endpoint de
  detalle son **tolerantes a fallos** (devuelven datos parciales, nunca 500).
- 🐛 **502 al refrescar** un SKU que no existe en Amazon Listings (404). El botón
  de refresco ahora usa el sync por SKU resiliente (no rompe).
- 🐛 **Búsqueda en GENERAL no encontraba por SKU** (WooCommerce no busca SKU con
  `search`). Ahora hay **búsqueda parcial** por SKU o nombre (pocos caracteres),
  resuelta contra la tabla `productos`.

**Mejoras:**
- 💰 **Precio de Amazon** vía Pricing API v0 (lotes de 20) + lectura en vivo de un
  SKU (Listings API: precio, FBA/FBM, situación, ASIN en una sola llamada).
- ⚡ **Sincronización en vivo al abrir el detalle** (`sincronizar_sku`): lee ML
  (ambas cuentas), Amazon y WooCommerce **en paralelo** y tolerante a fallos, para
  que el detalle 360° nunca salga incompleto.
- 🎯 **Columnas por canal correctas**: Mercado Libre muestra **FULL** (no FBA),
  Amazon muestra **FBA** (no FULL), General solo stock propio.
- 🏷️ Etiqueta **"CANALES"** sobre los puntos de colores + **tarjeta de leyenda
  desplegable** que explica: punto relleno = publicado, solo borde = sin publicar,
  sin punto = no está en ese canal, y el color de cada canal.

---

## 🧾 Versión 0.13 — filtros, orden y vistas

**Fecha:** 30 jun 2026. Sobre la v0.11.

**Nuevo:**
- 🔀 **Toggle de vista**: Mosaico (tarjetas) o **Lista** (tabla compacta con
  imagen, SKU, categoría, precio, stock con FULL/FBA, estado y canales).
- ↕️ **Orden** por **stock** (mayor↔menor) y **precio** (mayor↔menor).
- 🗂️ **Filtro por categoría** (vista General) — categorías reales de WooCommerce
  vía `GET /api/productos/_categorias/lista`.
- 🧠 **Filtro inteligente de estado** (en vista Lista): Publicados/Activos,
  Inactivos/Sin publicar, o combinados.
- 🔧 La vista General resuelve búsqueda/estado/orden contra la tabla `productos` y
  trae los datos de WooCommerce por `wc_id` (más potente y rápido).

**Error reportado y atendido:**
- ⚠️ **`401 Unauthorized` de Mercado Libre** (p. ej. `GET /items/MLM... → 401`):
  el **token de una cuenta (San Corpe) estaba expirado**, por eso esa cuenta salía
  vacía. Se agregó **renovación automática de token ante 401** usando el
  `refresh_token` + las credenciales de la app. **Requiere configurar
  `MELI_APP_ID` y `MELI_CLIENT_SECRET`**; sin ellas no se puede renovar (los tokens
  de ML expiran a las ~6 h) y la cuenta seguirá vacía hasta que el proceso externo
  los actualice.

**Notas / limitaciones conocidas:**
- El **orden por stock en General** usa `productos.stock_odoo`, que puede estar
  desactualizado; el stock real fresco se va llenando con el sync de inventario.
- El filtro por categoría aplica a la vista **General** (WooCommerce). Las
  categorías por marketplace (ML/Amazon multinivel) quedan para una próxima
  iteración (junto con "suma total de stock" y "categoría general de ML").

Nuevas variables de entorno (opcionales, para renovar tokens ML):
`MELI_APP_ID`, `MELI_CLIENT_SECRET`.

---

## 🧾 Versión 0.14 — pool de conexiones + arquitectura "leer del cache"

**Fecha:** 30 jun 2026. Sobre la v0.13.

**Error crítico corregido — `max_connections_per_hour` (500):**
- El MySQL de Hostinger limita las **conexiones nuevas por hora a 500**. El código
  abría **una conexión por consulta** → se agotaba el límite → fallaban las
  consultas y el stock salía en 0/vacío.
- **Solución:** **pool de conexiones** (DBUtils `PooledDB`) que **reutiliza ~6
  conexiones** y casi no crea nuevas. Esto baja el consumo de cientos/miles de
  conexiones por hora a un puñado.

**Cambio de arquitectura (lo que pediste): leer del cache, sincronizar en lote:**
- La UI ahora **lee del cache `canal_inventario`** (rápido) y **NO** hace consultas
  a las APIs una-por-una al navegar/abrir detalle.
- El **detalle 360°** ya no sincroniza al abrir; con el botón *refrescar*
  (`?refrescar=true`) sí hace una lectura en vivo de ese SKU (a demanda).
- El **sync en segundo plano es progresivo**: cada corrida toma primero los SKUs
  que faltan en el cache y luego los más viejos, así cubre todo el catálogo con el
  tiempo. Arranca ~30 s después de iniciar y se repite cada `SYNC_INTERVAL_MIN`.

### Estructura de base de datos (cache de inventario)

La tabla **`canal_inventario`** es el corazón del cache (una fila por SKU + canal +
cuenta):

| Columna | Para qué |
|---|---|
| `sku, canal, cuenta` | llave (PK) |
| `item_id` | id del listing (ml_item_id / asin) |
| `precio`, `precio_base` | precio del canal |
| `stock_real` | stock propio (lo que se sincroniza) |
| `stock_full`, `stock_fba` | bodega ML / Amazon (solo lectura) |
| `es_full`, `logistica` | tipo de logística |
| `situacion` | estatus del listing (active/paused/PUBLISHED…) |
| `updated_at` | última sincronización (para el sync progresivo) |

**Flujo:** las APIs (ML/Amazon/Woo) → escriben en `canal_inventario` (sync en lote
o webhook) → la UI lee de `canal_inventario`. Cuando se implementen **webhooks**,
solo actualizan las filas afectadas y se apaga el polling (`SYNC_ENABLED=false`).

**Mejora propuesta (siguiente):** guardar también `nombre`, `imagen` y `categoria`
en `canal_inventario` para que TODA la UI (incluido General) se pinte desde la DB
sin llamar a WooCommerce en cada vista.

### ¿MySQL (Hostinger) o Supabase?

- Tus datos fuente (`productos`, `ml_progress`, `amazon_progress`, `costos_finales`,
  `ml_tokens`) **ya viven en MySQL de Hostinger**, así que el cache convive ahí.
- Con el **pool**, el límite de 500/hora deja de ser problema en operación normal.
- **Supabase (Postgres)** sería más holgado en conexiones (pooler PgBouncer, sin
  tope horario) y conviene si el límite vuelve a apretar con mucho tráfico, pero
  requiere proyecto + credenciales y mantener dos bases (fuente en MySQL, cache en
  Postgres). **Recomendación:** seguir en MySQL + pool por ahora; migrar el cache a
  Supabase solo si el límite vuelve a ser un cuello de botella.

---

## 🚀 Pendientes y estrategias propuestas

**Inmediato (cuando lleguen credenciales):**
- Conectar TikTok Shop, Walmart, Temu y Shein: basta con sustituir
  `services/ejemplos.py` por el cliente real de cada canal (la UI ya está lista).

**Estrategias recomendadas:**
- **Sincronización por colas**: un worker (Railway cron / RabbitMQ) que refresque el
  cache de ML/Amazon en segundo plano, en lugar de solo bajo demanda.
- **Edición en masa** (como en tu pizarrón): seleccionar productos y publicar/actualizar
  en lote por canal, con **prompt de IA editable por canal y por tienda**.
- **Categorías inteligentes**: usar Claude + el predictor de categorías de ML para
  sugerir la categoría correcta al publicar.
- **Semáforo de salud por SKU**: indicador de qué falta para publicar (precio, fotos,
  dimensiones, atributos) reutilizando `ml_estado` / `costos_finales`.
- **Autenticación**: añadir login (JWT/Supabase) sobre el placeholder `auth.py`.
- **Tabla de mapeo de canales**: para canales sin SKU directo, una tabla
  `canal_listing (sku, canal, cuenta, listing_id)` que centralice los vínculos.

---

*Hecho para Kubera — panel omnicanal sobre WooCommerce.*
