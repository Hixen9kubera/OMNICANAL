# 🛰️ OMNICANAL · Kubera

Panel omnicanal para gestionar el catálogo de **WooCommerce**, publicar en cada
**marketplace** (Mercado Libre ×2 cuentas, Amazon; Temu/TikTok vía M2E) y
registrar **cada venta como pedido de Woo con su precio real congelado**.

> **Fuente de verdad (ventas E inventario):** WooCommerce (`chunche.shop`)
> desde el 2026-07-17 · **Odoo:** en retiro (solo vigilado) ·
> **Cache/control:** MySQL · **Vínculo entre todo:** el **SKU**.
>
> 🤖 **¿Eres una sesión de Claude (u otra IA) llegando en frío?** Lee primero
> **[CLAUDE.md](CLAUDE.md)**: estado operativo actual, reglas de la casa
> (aprendidas con incidentes reales), flags de producción, mapa de piezas,
> pendientes y playbooks de diagnóstico. La bitácora versión por versión está
> más abajo en este README.

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

## 🔔 Versión 0.15 — Webhooks de Mercado Libre + campana de notificaciones

**Fecha:** 1–2 jul 2026. Sobre la v0.14.

**Qué se construyó:**
- **Receptor de webhooks de Mercado Libre** (`POST /api/webhooks/ml`): recibe la
  notificación, responde **200 de inmediato** (ML reintenta si tardas) y **procesa
  aparte** en segundo plano.
  - `topic = items / items_prices / stock_locations` → **refresca ese ítem** en el
    cache (`refrescar_ml_item_id`).
  - `topic = orders_v2` → una venta cambia el stock: **resincroniza los ítems de la
    orden**.
  - Otros topics (shipments, payments, questions…) se **registran** sin acción de
    stock.
- **Persistencia en base de datos** (tabla **`webhook_eventos`**): antes las
  notificaciones vivían solo en memoria y se perdían al reiniciar. Ahora sobreviven
  reinicios/redeploys.
- **Campana de notificaciones** en el navbar (`NotificationBell`): sondea
  `GET /api/webhooks/notificaciones` cada 30 s, muestra un **badge** con las no
  leídas, íconos y etiqueta por topic (Venta, Cambio de publicación, Envío…) y el
  "hace X min". El "leído" se guarda en `localStorage`.
- **Interruptor de registro en runtime** (para pausar sin redesplegar):
  - `GET|POST /api/webhooks/pausar` → responde 200 a ML pero **NO guarda** ni procesa.
  - `GET|POST /api/webhooks/reanudar` → reactiva el guardado.
  - `GET /api/webhooks/estado` → `{ "registro_activo": true|false }`.
  - Persistente: variable de entorno **`WEBHOOK_REGISTRO=false`** deja el registro
    pausado por defecto tras un reinicio.

### Tabla `webhook_eventos`

| Columna | Para qué |
|---|---|
| `id` | PK autoincremental |
| `canal` | `mercado_libre` (preparada para más canales) |
| `topic` | items / orders_v2 / shipments / … |
| `resource` | recurso notificado (`/items/MLM…`, `/orders/…`) |
| `user_id`, `cuenta` | dueño de la notificación |
| `sku`, `procesado`, `resultado` | resultado del procesamiento en background |
| `recibido` | fecha/hora de recepción (UTC) |

**Endpoints nuevos:**
`POST /api/webhooks/ml`, `GET /api/webhooks/ml` (ping), `GET /api/webhooks/ml/log`,
`GET /api/webhooks/notificaciones`, `.../pausar`, `.../reanudar`, `.../estado`.

**URL del webhook (Railway):**
`https://backendomnicanal-production.up.railway.app/api/webhooks/ml`

**Otros marketplaces (investigación):** Amazon usa **SQS/EventBridge** (no callback
HTTP directo); TikTok Shop, Walmart y Temu sí exponen **webhooks HTTP** (pendientes
de credenciales). El receptor está listo para generalizarse.

---

## 🎨 Versión 0.2 — Pestaña PRODUCTOS + Estudio de producto con IA por canal

**Fecha:** 2 jul 2026. Sobre la v0.15.

Se activa la pestaña **PRODUCTOS** del navbar (antes "próximamente") con un
**estudio de producto**: una ventana superpuesta que se **desliza desde la derecha**
para ver la ficha completa y **generar contenido optimizado por canal con IA**.

**Frontend:**
- **Navbar navegable**: `Omnicanal` (`/omnicanal`) y `Productos` (`/productos`) ahora
  son rutas reales con estado activo según la URL; el resto sigue "próximamente".
- **Página `/productos`** (`app/productos/page.tsx`): lista el catálogo de
  WooCommerce en **forma de lista** mostrando **título, descripción corta, categoría,
  precio y presencia por canal**; con **buscador parcial** y **paginación arriba y
  abajo**. Al hacer clic en un producto se abre el estudio.
- **`ProductStudio`** (overlay, `components/ProductStudio.tsx`):
  - **La categoría se muestra primero**, luego el resto del contenido (galería de
    imágenes, título con contador de caracteres, descripción, precio regular/oferta,
    atributos).
  - **Selector de canal a editar** arriba: al elegir un canal, **todo el panel cambia
    de color** (igual que en Omnicanal) y muestra el **estado de ese canal**
    (publicado, precio, stock, FULL, link) si el producto ya está publicado ahí.
  - **Botones de IA por canal, uno por tipo de contenido** ("Actualizar contenido para
    {canal}"). Cada botón dispara el agente/prompt específico de ese canal, con
    **animación de carga**, y muestra el resultado en tarjetas con **Copiar** y
    **Usar** (rellena el campo de título/descripción).

**Backend — generadores de contenido por canal** (`services/ia_generadores.py`):
- Registro `GENERADORES` (fuente única de verdad) con los tipos por canal:
  - **Amazon** (instrucciones vigentes 27-jul-2026, provistas por Kubera): **Título**
    (≤75), **Item Highlights** (≤125), **5 Bullet Points** (150–200 c/u), **Descripción**
    (≤2000), **Atributos Amazon** por categoría, y **Set de 5 imágenes** (detección de
    categoría A–J + layout + texto exacto + **prompt de IA en inglés** por imagen).
  - **Mercado Libre**: Título (≤60), Ficha técnica / atributos, Descripción (texto plano).
  - **General (WooCommerce)**: Título, Descripción (HTML). **TikTok**: título viral.
- **Proveedor de IA con fallback**: usa **DeepSeek** si `DEEPSEEK_API_KEY` está
  configurada; si no, cae a **Claude** (`ANTHROPIC_API_KEY`). Si no hay ninguna,
  devuelve un mensaje claro en vez de fallar.
- Endpoints: `GET /api/ia/generadores?canal=…` (pinta los botones) y
  `POST /api/ia/generar` (ejecuta un generador sobre el producto).
- **WooCommerce** ahora expone `atributos`, `descripcion_corta`, `precio_oferta` en el
  detalle y `descripcion_corta` en la lista (para la vista PRODUCTOS).

**Nuevas variables de entorno (opcionales):**
`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL` (default `https://api.deepseek.com`),
`DEEPSEEK_MODEL` (default `deepseek-chat`).

**Nota:** el estudio es de **edición/generación de contenido**; guardar/publicar los
cambios de vuelta en cada canal queda para una próxima iteración.

---

## 🖼️ Versión 0.3 — Editor de imágenes por producto (galería WooCommerce + IA)

**Fecha:** 10 jul 2026. Sobre la v0.2.

Se añade un **editor de imágenes** dentro del **ProductStudio**:

- **Galería interactiva por producto**: al pasar el mouse sobre una imagen aparecen
  sus controles — **flags de IA** (Fondo = quitar fondo · Texto = traducir + quitar
  logos · Modelo = cambiar persona) y **eliminar** la imagen.
- **Procesar con IA (on-demand)**: edita cada imagen con Gemini según sus flags
  (8 combinaciones, portadas del pipeline CLI), la sube a WordPress Media y
  **reemplaza** la anterior en WooCommerce en **UN solo PUT** (evita la race
  condition), incluyendo variaciones. La imagen editada se refleja **en tiempo real**.
- **Label de carga por imagen**: paso actual, avance N/total y **error por imagen**.

**Backend:**
- `services/imagenes_editor.py`: motor async (flags → prompt Gemini, `describe_person`
  solo si `cambiar_modelo`, job de progreso en memoria, backlog en `ml_image_edit_backlog`).
- `services/woocommerce.py`: `galeria_producto` / `reemplazar_imagenes_galeria` /
  `eliminar_imagen_galeria` (resuelven el padre si es variación).
- `routers/imagenes.py`: `GET /api/imagenes/{sku}`, `POST …/procesar`,
  `GET …/progreso`, `POST …/eliminar`.

**Frontend:** galería editable en `ProductStudio` + tipos y cliente API de imágenes.

---

## 🧩 Versión 0.4 — Estudio de producto: contenido, imágenes con IA y atributos ML

**Fecha:** 10 jul 2026. Sobre la v0.3.

### Contenido del producto (canal General)
- **Borradores persistentes**: los cambios de título/descripción/atributos se
  autoguardan en `localStorage` y **sobreviven al recargar** la página, con botón
  **"Descartar borrador"** (recarga desde WooCommerce).
- **Botón "Guardar contenido"** (solo canal General): persiste título/descripción/
  atributos a WooCommerce **preservando los atributos de variación**
  (`POST /api/productos/{sku}/contenido`).
- **Límite de caracteres del título por canal**: Mercado Libre 60, Amazon 200
  (contador en rojo al exceder).

### Editor de imágenes con IA (galería WooCommerce)
- **4 flags independientes** por imagen: **Fondo** (quitar fondo), **Traducir texto**,
  **Quitar logos** y **Modelo** (cambiar persona). Antes "traducir" y "quitar logos"
  iban juntos.
- **Agregar imágenes** con botón **"+"**: clic (selector de archivos) o
  **arrastrar y soltar** (`POST /api/imagenes/{sku}/agregar`).
- **Fixes de caché (LiteSpeed)**: las lecturas y escrituras de galería van con
  cache-bust → ya no aparecen imágenes viejas al recargar, ni se revierten las
  imágenes editadas al procesar un segundo grupo.

### Amazon (publicación)
- **Imágenes al publicar**: el payload de Amazon ahora incluye las imágenes
  (`main/other_product_image_locator`) → el listing ya no queda sin fotos.
- **Payload visible en la vista previa** de Amazon (antes solo se veía el de ML).
- Verificado que ML/Amazon **publican con el precio REGULAR**.

### Atributos de Mercado Libre (nuevo `services/ml_atributos.py`)
- Port del pipeline canónico: consulta la categoría ML y separa **PRINCIPALES**
  (obligatorios) y **SECUNDARIOS** con sus valores válidos; prompt rico + DeepSeek
  (`json_object`, temp 0.2) con validación contra IDs válidos.
- **Crear Productos** usa el servicio y guarda los atributos como `ml_attr_<ID>`
  (lo que lee el publisher) → los atributos ahora **sí llegan a Mercado Libre**.
- **"Mejorar con IA" (canal Mercado Libre)** trae los atributos reales de la
  categoría (principales + secundarios) con nombre legible.

---

## 📸 Versión 0.5 — Imágenes listas para Amazon (WebP → JPEG, ≥1000 px, zoom)

**Fecha:** 14 jul 2026. Sobre la v0.4.

### El problema (diagnosticado con datos reales)

Aunque en la v0.4 ya se enviaban las imágenes a Amazon (`main_product_image_locator` /
`other_product_image_locator_N`), **los listings seguían sin fotos**. Al medir las imágenes
reales de la tienda aparecieron **dos incumplimientos**:

| Producto | Imágenes reales |
|---|---|
| `HERR-0029` | 720×720, 800×800, 1024×1024, 1024×1024, 800×800, 800×800 — **todas `.webp`** |
| `EST-0091` | 800×800 ×5, 1024×1024 — **todas `.webp`** |

1. **Formato** — WooCommerce guarda las imágenes en **`.webp`**, y **Amazon NO acepta WebP**
   (solo JPEG, TIFF, PNG y GIF no animado; prefiere JPEG).
2. **Tamaño** — Amazon exige entre **1,000 y 10,000 px en el lado más largo**: es lo que
   habilita el **zoom**. La mayoría del catálogo está en **720–1024 px**, así que **no cumple**.

### Requisitos oficiales de imagen de Amazon (los que ahora se garantizan)

- Servida por **HTTP o HTTPS** (nunca FTP ni ruta de archivo local).
- Formato **JPEG, TIFF, PNG o GIF no animado** — se prefiere **JPEG**.
- Color **RGB o CMYK** — se prefiere **RGB**.
- **Clara y sin pixelar**, mínimo **72 ppp**.
- Entre **1,000 y 10,000 px** en el **lado más largo** (necesario para el zoom).

### La solución: `services/imagenes_amazon.py`

Un paso **"Amazon-ready"** que corre **al confirmar la publicación** y transforma solo lo
necesario. **NO toca la galería de WooCommerce ni la de Mercado Libre**: genera una versión
paralela y usa esas URLs únicamente en el payload de Amazon.

Con `L` = lado más largo de la imagen original:

| Caso | Acción | Resultado |
|---|---|---|
| `1000 ≤ L ≤ 10000` **y** formato válido | **No se toca** (ni se descarga ni se sube nada) | Se usa la URL original |
| Tamaño OK pero **formato inválido** (WebP) | **Convierte a JPEG sin reescalar** | Misma resolución, cero pérdida |
| `500 ≤ L < 1000` | **(A) Lanczos ×2** | 1000–2000 px, JPEG |
| `L < 500` | **(B) Fallback IA: Real-ESRGAN ×4** (Replicate). Si falla → Lanczos | ≥1000 px, JPEG |
| `L > 10000` | Reduce a 10000 | JPEG |

La salida **siempre** es **RGB + JPEG** (calidad 90, progresivo).

**Por qué Real-ESRGAN y no Gemini** (que sí usamos en el editor de imágenes): Gemini es un
modelo **generativo** — al "mejorar" una imagen la **regenera** y puede alterar el producto.
**Real-ESRGAN es super-resolución pura**: sube la resolución **sin inventar ni cambiar el
contenido**. Para fotos de producto que van a un marketplace, eso es lo correcto.

> Con el catálogo actual (720–1024 px) **el fallback de IA no se activa**: todo lo resuelve el
> reescalado clásico (A) o la simple conversión de formato. El costo extra es **$0**.

### Caché — tabla `amazon_imagenes`

Para no reprocesar ni duplicar medios en cada publicación, el resultado se cachea por
**hash de la URL de origen**:

| Columna | Para qué |
|---|---|
| `src_hash` | PK — sha1 de la URL original |
| `sku`, `src_url`, `amz_url` | trazabilidad + la URL final que va a Amazon |
| `wp_media_id` | id del medio subido a WordPress |
| `ancho`, `alto`, `metodo` | resultado y método usado (`lanczos` / `convert` / `real-esrgan`) |

La tabla **se crea sola**. Si editas una imagen con el editor de IA, **cambia su URL → cambia
el hash → se vuelve a optimizar automáticamente**.

### Vista previa: avisa antes de publicar

La vista previa **no sube medios ni tarda** (`preparar_imagenes=False`): solo **mide** y avisa.
Ejemplo real:

> *"De 6 imagen(es): 5 miden menos de 1000 px (sin eso Amazon no habilita el zoom) y 6 están en
> un formato que Amazon NO acepta (WebP) [800x800 WEBP, …]. Al publicar se optimizarán
> automáticamente a ≥1000 px, JPEG RGB."*

### Prompt del set de imágenes de Amazon

El generador **"Set de 5 imágenes"** (`ia_generadores._AMZ_IMAGENES`) ahora incluye los
**requisitos técnicos de Amazon** (HTTP/HTTPS, JPEG preferido, RGB, ≥72 ppp, 1,000–10,000 px)
para que el set que planea la IA nazca ya conforme.

### Archivos tocados

- **Nuevo**: `backend/services/imagenes_amazon.py` — optimización + caché + fallback IA.
- `backend/services/publicar_ready.py` → `atributos_amazon(..., preparar_imagenes=True)`.
- `backend/services/publicar.py` → `_amazon_attrs_final(..., preparar_imagenes)`; la vista previa
  llama con `False` y añade el diagnóstico de tamaño/formato.
- `backend/services/ia_generadores.py` → requisitos técnicos en `_AMZ_IMAGENES`.
- Dependencias: **Pillow** (ya estaba en `requirements.txt`) y **`REPLICATE_API_KEY`** (solo para
  el fallback de IA).

---

## 📈 Versión 0.6 — Tab VENTAS en vivo (por hora, comparativa semanal) + base de pedidos ML→WC

### Qué es

La pestaña **Ventas** deja de decir "Pronto": muestra las ventas REALES de Mercado Libre
segmentadas **por hora (00:00–23:00)**, de **ambas cuentas** (Kubera/BEKURA y
San Corpe/SANCORFASHION), **siempre comparadas contra la semana pasada en %**.

- **General** = todas las cuentas sumadas. **Mercado Libre** permite elegir cuenta
  (Kubera / San Corpe / Todas). La vista entera cambia de color según el canal
  (índigo General, amarillo ML), igual que en Omnicanal. Amazon/TikTok/Walmart/Temu/Shein
  aparecen "Pronto" hasta integrar sus órdenes.
- Filtros: **Hoy / Ayer / Últimos 7 días** + rango personalizado (hasta 31 días).
- KPIs: ventas brutas, pedidos, unidades, ticket promedio y canceladas (con monto).
- Gráfica de **48 barras** (24 h × actual/semana pasada) con tooltip por hora
  (montos, pedidos y delta %), pico del día señalado y hora actual marcada.
- **EN VIVO**: si el rango incluye hoy, se refresca solo cada 60 s.
- Desglose por cuenta con % de participación; clic en la tarjeta = filtrar esa cuenta.

### La comparativa honesta (detalle importante)

HOY siempre va incompleto: compararlo contra el día COMPLETO de la semana pasada da un
−60% engañoso a media mañana. Cuando el rango es "hoy", el backend agrega la comparativa
**"a la misma hora"** (`totales.parcial`: semana pasada hasta la hora actual) y el frontend
la usa en el banner, los KPIs y las tarjetas de cuenta. Ejemplo real de la prueba:
día completo −58.8% (engañoso) vs misma hora **−10.0%** (real). Los rangos cerrados
(ayer, 7 días) comparan contra el mismo rango de 7 días atrás.

### De dónde salen los datos (y de dónde NO)

De la **API de órdenes de ML** (`/orders/search` filtrado por `order.date_created`),
con el **precio real de cada venta** (`total_amount`). NO se usa Supabase (dejó de ser el
registro de ventas) ni el catálogo (sus precios cambian todo el tiempo). Solo cuentan
órdenes `paid`; las `cancelled` se reportan aparte. Horas bucketizadas en **CDMX (UTC−6
fijo** — México abolió el horario de verano en 2022).

### Caché (tablas `ventas_horarias` + `ventas_sync`)

Un día por cuenta son ~4–10 páginas de la API. Cada (cuenta, día) se agrega UNA vez a
24 renglones por hora en MySQL:

| Día consultado | Regla de refresco |
|---|---|
| HOY | TTL 3 min (ventas en vivo) |
| ayer/antier | TTL 15 min (cancelaciones tardías) |
| > 2 días | **FINAL**: no se vuelve a pedir a ML |

La frescura del rango completo se checa en **una sola** consulta (28 sueltas costaban ~8 s).
Al arrancar el backend se precalientan los últimos 14 días por cuenta en segundo plano;
como la tabla persiste entre deploys, tras el primer llenado solo se refresca HOY.

### Endpoints

- `GET /api/ventas/horario?canal=general|mercado_libre&cuenta=&desde=&hasta=` →
  24 buckets actual+previo, totales con deltas, `parcial` (solo hoy), desglose por cuenta.
- `GET /api/ventas/dias?dias=7` → serie diaria para tendencias.

### Base del flujo ventas ML → pedidos WooCommerce (preparada, aún sin conectar)

- `meli.obtener_orden(order_id)` ahora devuelve la orden COMPLETA normalizada (SKU,
  `unit_price` real, comisión `sale_fee`, estado, comprador, envío y si es FULL por
  `logistic_type == "fulfillment"`). Antes se descartaba todo excepto los item_id.
- **Nuevo** `services/pedidos_ml.py`: convierte una venta de ML en pedido de WC con el
  **precio congelado** (línea con `subtotal`/`total` explícitos), comisión y neto en metas
  `_ml_*`, idempotente (reenvíos del webhook actualizan estado, no duplican), resolución
  de SKU **directo contra Woo** (el espejo local está incompleto: 66/177 SKUs vendidos
  faltaban ahí pero SÍ existen en Woo), líneas sueltas para SKUs sin producto y
  `proteger_stock` para no descontar inventario (pruebas/histórico/FULL).
  **Verificado con 14 ventas reales** (todas cuadraron al centavo); queda pendiente
  conectarlo al webhook cuando se decida la estrategia de stock (transición Odoo→WC).

### Archivos tocados

- **Nuevos**: `backend/services/ventas_ml.py`, `backend/routers/ventas.py`,
  `backend/services/pedidos_ml.py`, `frontend/app/ventas/page.tsx`.
- `backend/services/meli.py` → `obtener_orden()` completa; `obtener_orden_items()` la envuelve.
- `backend/main.py` → router `ventas` + warmup del caché en `lifespan` + v0.6.0.
- `frontend/components/AppNavbar.tsx` → "Ventas" activo (`/ventas`).
- `frontend/lib/types.ts` + `frontend/lib/api.ts` → tipos `Ventas*` y `ventasHorario()`.

---

## ⭐ Versión 0.7 — Todo el catálogo de ML a PREMIUM (gold_pro)

### Qué se hizo

Las 2 cuentas de Mercado Libre quedan 100% en publicación **Premium** (`gold_pro`),
por decisión de negocio (Premium da meses sin intereses y mejor exposición).

**Foto ANTES de la migración** (escaneo completo vía `/users/{id}/items/search`):

| Cuenta | Premium | Clásica | Total |
|---|---|---|---|
| BEKURA | 2,016 (98%) | 41 | 2,057 |
| SANCORFASHION | 893 (42%) | **1,219** | 2,112 |

**Migración**: `POST /items/{id}/listing_type {"id":"gold_pro"}` sobre toda clásica no
cerrada (activas, pausadas y en revisión aceptan el cambio — validado con canario de 5).
Idempotente y re-ejecutable; log CSV por ítem. Las `closed` se omiten (ML no las revive).

### Publicaciones nuevas

**No hubo que tocar nada**: el pipeline vendorizado ya publica Premium desde siempre
(`vendor/ml_ready/publisher_core.py: DEFAULT_LISTING_TYPE = "gold_pro"`). Las clásicas
eran publicaciones anteriores a ese pipeline.

### Comisiones en el módulo de Costos (cambio con impacto)

`services/costos.py` calculaba el % de comisión consultando `listing_prices` con
`gold_special` (clásica). Con el catálogo en Premium eso **subestimaba el fee ~4.5
puntos** (medido en vivo: 15%→19.5% y 12%→16.5% según categoría), y el precio sugerido
salía con margen de menos. `DEFAULT_LISTING_TYPE` pasa a `gold_pro`: **los precios
sugeridos suben** para compensar la comisión Premium real. ⚠️ Avisar al equipo de
costos/precios: los % que verán en el panel ahora reflejan Premium.

---

## 🛒 Versión 0.8 — Pedidos ML→WC ENCENDIDOS (modo registro) + vigilante de Odoo + fix de categoría

### Pedidos automáticos (la venta se congela como pedido de Woo)

El webhook `orders_v2` — que ML ya manda a este backend — ahora, además de
resincronizar stock, **crea/actualiza el pedido en WooCommerce** vía
`pedidos_ml.sincronizar()` con el **precio REAL de la venta congelado**, la comisión
de ML y el neto en metas `_ml_*`. Idempotente: los webhooks repetidos de la misma
venta (pago→envío→entrega) actualizan el estado, no duplican.

| Flag (env) | Default | Qué hace |
|---|---|---|
| `PEDIDOS_WC_ENABLED` | `true` | Crea el pedido por cada venta de ML |
| `PEDIDOS_WC_DESCUENTA_STOCK` | `false` | **Modo REGISTRO**: el pedido nace con `_order_stock_reduced` y NO baja inventario (Odoo sigue siendo el maestro). Ponerlo `true` = el corte de inventario a Woo |

Probado end-to-end con la venta real `#2000017468364824` → pedido WC `#101133`
(FULL, $396, `processing`, stock intacto). `GET /api/webhooks/estado` muestra los flags.

### Vigilante de Odoo (`services/odoo_watch.py`)

Responde a "¿cómo cachamos un cambio de stock hecho en Odoo?": cada
`ODOO_WATCH_MIN` min (30) compara `qty_available` contra la última foto
(`productos.stock_odoo`), actualiza la foto, y **avisa en la campana**
("Odoo: stock 12 → 8", canal `odoo`). Con `ODOO_WATCH_AUTO_PUSH=true` además
empuja SOLO los SKUs cambiados a Woo (encender tras la carga inicial). Primer
arranque con foto vieja → un solo aviso-resumen (sin inundar la campana).

### Carga inicial Odoo→Woo (medida, lista para disparar)

`POST /api/sync/woo` (ya existía) alinea stock+costos. Dry-run del 2026-07-17:
12,923 SKUs en Odoo, 12,806 en Woo (99.1%), **solo 525 difieren** (434 suben,
65 bajan, 26 quedan en 0). El barrido masivo se dispara manualmente desde el
panel/endpoint — decisión de negocio, no automática.

### Fix: la categoría del PANEL manda sobre la del predictor

Caso real TEC-1812-NEG: el panel decía **Máquinas Sexuales** (`ml_categoria_id`,
del selector) pero se publicó en **Máquinas de Coser** (`ml_category_id`, del
predictor de Crear). `publicar_ready.construir_prod` ahora prefiere
`ml_categoria_id` (elección humana) y deriva el nombre de `ml_categoria_niveles`.
El ítem pausado de San Corpe se corrigió EN VIVO con `PUT /items/{id}`
(`category_id` → aceptado); el cerrado de BEKURA requiere republicar (los
cerrados devuelven `category_id.not_modifiable`).

### Webhook DESVINCULADO de MySQL + candado de cancelación FULL (pedido de Brandon)

- **`WEBHOOK_GUARDA_MYSQL=false` (default)**: las notificaciones de ML ya NO se
  insertan en `webhook_eventos` (MySQL) — se procesan al vuelo (stock + pedido).
  El espejo idempotente de Supabase (`ops.webhook_events`) es independiente y lo
  gobierna `SUPABASE_DUAL_WRITE`. Consecuencia: la campana deja de mostrar
  eventos de ML salvo que se encienda `SUPABASE_READ_WEBHOOKS` (Fase 5).
- **Candado de cancelación**: un pedido FULL cancelado hacía que Woo "devolviera"
  a bodega una pieza que salió del almacén de ML (la marca `_order_stock_reduced`
  dispara el restock del hook de cancelación). Ahora: al cancelar un pedido
  protegido primero se pone la marca en `no` (sin restock), y un pedido que NACE
  cancelado ya no lleva la marca. Los no-FULL cancelados sí reponen (correcto).
- Verificación de credenciales (2026-07-17): el repo no contiene secretos
  hardcodeados (vendor recibe tokens por inyección; solo `.env.example` con
  placeholders). El `client_secret` expuesto conocido vive en el repo externo
  `publicador` — su rotación sigue pendiente allá.

### v0.8.1 — Los pedidos se ven en la pestaña VENTAS

`/api/ventas/horario` ahora incluye `pedidos_wc`: los pedidos ML→WC creados en el
rango (tabla `pedidos_ml`), con desglose por cuenta (Kubera/San Corpe), FULL vs
propios y cancelados. El tab muestra el panel "Pedidos en WooCommerce · Registro
vivo" bajo los KPIs, respeta el filtro de cuenta y se refresca cada 60 s.

### v0.8.2 — Modo "puros pedidos de Woo" (sync de datos de ML apagable)

Pedido de Brandon (2026-07-17): estos días la operación vive de los PEDIDOS de
WooCommerce; las lecturas de datos a ML se apagan sin tocar el flujo de pedidos.

| Variable | Efecto con `false` |
|---|---|
| `SYNC_ENABLED` | Apaga el sync de inventario cada 15 min (ML+Amazon) y las resincronizaciones de ítems que disparaba el webhook. Ya NO mata al vigilante de Odoo (ahora es independiente). |
| `VENTAS_ML_REFRESH` | El tab Ventas deja de pedirle datos nuevos a ML: sirve el caché de días cerrados; la gráfica de HOY queda congelada al momento del apagado. El panel de PEDIDOS sigue vivo (lee nuestra tabla, 0 llamadas a ML/Woo). |

Lo ÚNICO que sigue hablando con ML: `obtener_orden` por cada venta (sin la orden
no hay pedido) — 1 lectura por webhook de venta.

### v0.9.0 — La pestaña VENTAS vive de los PEDIDOS (General y canales)

Decisión de Brandon (2026-07-17): la operación vive de pedidos y webhooks.
El tab entero se alimenta de `pedidos_ml` (cero llamadas a ML):

- **General** = TODOS los pedidos; **Mercado Libre** filtra los mismos pedidos
  y las cuentas (Kubera/San Corpe) diferencian cada pedido por su `cuenta`.
- Cuentan como venta los pedidos PAGADOS (processing/completed); `pending` aún
  no es dinero y `cancelled` va aparte con su monto.
- Sin métrica de Unidades (los pedidos no la guardan — honestidad ante todo).
- La comparativa semanal muestra "s/ base" hasta que el registro cumpla 7 días
  (24-jul); un "+100% vs cero" es ruido y se eliminó de `_delta_pct`.
- La vista histórica de la API de ML sigue disponible con `?fuente=ml` (para
  reconciliar contra lo que reporta Mercado Libre cuando se quiera).

### v0.10.0 — AMAZON entra al registro de pedidos (sondeo cada 5 min)

Amazon no tiene webhook simple (su vía real exige AWS+SQS); con ~4 órdenes/día
un sondeo de 5 min ES tiempo real en la práctica. `services/pedidos_amazon.py`
reutiliza `pedidos_ml.sincronizar` (mismo candado, misma idempotencia, misma
tabla con `cuenta='AMAZON'`, `creado`=PurchaseDate):

- **FBA (AFN)** → protegido (almacén de Amazon, como FULL) · **MFN** → descuenta
  bodega en Woo · estados: Shipped→completed, Unshipped→processing,
  Pending→on-hold (no cuenta como venta), Canceled→cancelled.
- Job `pedidos_amazon` en el scheduler (flags `PEDIDOS_AMAZON_ENABLED`/`_MIN`).
- Tab Ventas: pastilla **Amazon activa** (naranja), General suma ML+Amazon,
  chip Amazon en el panel. Carga histórica: 36 órdenes (27 completadas $31k,
  2 FBA, 7 canceladas) protegidas (sus MFN salieron antes del corte).
- Comisión de Amazon pendiente (Finances API) — se registra 0 por ahora.
- Escala: mismas ~288 llamadas/día aunque el volumen crezca ×100 (paginado);
  upgrade a SQS = solo cambiar el timbre, la tubería es la misma.
- NO toca nada de la migración (canal_inventario, channel/costing/core/ops/
  migration, espejos, ETLs quedan intactos).

### v0.11.0 — Órdenes de Temu/TikTok conectadas (M2E Cloud) + auditoría de publicación

- **`services/pedidos_m2e.py`**: sondeo cada 10 min de `order/find` por canal en
  la API de M2E Cloud (token en `M2E_API_TOKEN`, se genera en M2E → Settings →
  Catalog → API). Mismo motor de pedidos (cuenta='TEMU'/'TIKTOK', descuentan
  bodega — no hay FULL en esos canales). Parseo defensivo + log del JSON crudo
  de las primeras órdenes (el esquema se confirma con la primera venta real).
  TikTok se salta mientras su conexión esté inválida (re-autorizar en M2E).
- La API pública de M2E NO publica listados (verificado a fondo: rutas 404 +
  docs) — listar en Temu/TikTok es el panel web de M2E; catálogo e inventario
  ya fluyen solos desde Woo (PATCH probado con {"products":[...]} → 200).
- **Auditoría de los 131 "Ready"** (2026-07-20): base sana (precio/categoría/
  fotos ✓). Bloqueos reales, por historial de ml_backlog: 108 SKUs por
  **GUÍA DE TALLAS faltante** (~25 dominios de ropa sin guía en ambas cuentas;
  hoy solo existen calzado+bras en `vendor/ml_ready/size_chart_mapping.py`),
  11 por ME1 inactivo, 5 por imágenes chicas, 2 GTIN. BRAS con guía pero
  productos sin atributo GÉNERO también fallan (la guía se busca por
  dominio+género). Alta de guías: dashboard ML o POST /catalog/charts →
  chart_ids al mapping.

### v0.12.0 — Tipo de producto de AMAZON visible y editable (como la categoría de ML)

Amazon no tiene categorías: tiene PRODUCT TYPES (cada uno con su esquema de
atributos). Ahora el Studio, en el canal Amazon, muestra el tipo que se usaría
HOY y permite cambiarlo:

- **Prioridad**: `amz_product_type` (elección del PANEL, meta en Woo) →
  histórico `amazon_progress.product_type` → detección automática por título.
  La misma regla que las categorías de ML: la elección humana MANDA.
- `GET /api/publicar/amazon/tipos?q=` — buscador con la relevancia de Amazon
  (Definitions API). `GET/POST /api/publicar/amazon/tipo` — leer/guardar la
  elección. El preview expone `product_type_origen` (panel/historial/auto).
- UI: `TipoAmazonPicker` en el Studio (chip con el tipo + origen, buscador con
  resultados en vivo, guardado a Woo). Probado en vivo: "guantes seguridad" →
  PROTECTIVE_GLOVE, guardado en HERR-0029, resolvedor devuelve origen=panel.
- Nota: el cambio de tipo aplica al PUBLICAR/actualizar; Amazon puede pedir
  atributos distintos del nuevo tipo (el flujo de issues los negocia).

### v0.12.1 — Fix: respuestas tardías de "Mejorar con IA" contaminaban el borrador de OTRO producto

Caso real (ACC-0653-CHE-13-16): el usuario pidió Mejorar con IA en un producto
(binoculares), cambió a los faros de niebla antes de que la IA respondiera
(~20-30 s), y la respuesta aterrizó en los campos del producto ABIERTO; el
autosave del borrador la persistió bajo el SKU equivocado (localStorage).
WooCommerce y Amazon nunca se contaminaron (verificado: producto Woo correcto,
amazon_progress/backlog vacíos) — el daño era solo el borrador local.

Fix: candado `pedidoVigente` (sku:canal) en `mejorarConIA` — si al llegar la
respuesta el usuario ya no está en el mismo producto+canal, se DESCARTA entera
(mejora y competencia). Limpieza de borradores contaminados: botón
"Descartar borrador" del Studio (el borrador vive en el navegador del usuario).

### v0.12.2 — Purga global de borradores contaminados (studioStore v1→v2)

El caso ACC-0653 persistía porque los borradores contaminados por la carrera
(pre-v0.12.1) seguían en el localStorage del navegador, UNO POR CANAL (por eso
el texto "mutaba" entre capturas: cada canal guardó una corrida distinta de
Mejorar del producto equivocado). Verificado server-side limpio: el detalle 360
de ACC-0653 devuelve faros. Solución de raíz: la clave del almacén de borradores
sube `v1→v2` — TODOS los borradores viejos quedan huérfanos en todos los
navegadores y los campos recargan desde WooCommerce (lo guardado/publicado no
se toca; solo se pierden ediciones locales no guardadas). El botón "Descartar
borrador" ahora es visible (chip rojo junto al título).

### v0.13.0 — Espejo kubera (dual-write propio) + página /migracion en tiempo real

**Qué es.** Fase de DESCUBRIMIENTO de la migración a la BD centralizada
"kubera" (Postgres/Supabase, esquema v4): cada escritor `.py` que puebla MySQL
y que el trabajo de Eduardo/José aún no espeja, ahora replica su escritura en
la tabla equivalente del v4 y REGISTRA cada intento (éxito y error). Los
errores que aparezcan (FKs huérfanas, tipos, colisiones) son el plan de
limpieza previo al corte y se ven en la nueva página **/migracion** del panel.

**Censo escritor→tablas** (21 entradas, hardcodeado en
`services/kubera_mirror.py::CENSO` — es lo que alimenta la UI):
- **A espejar (7 seams, este módulo)**: `odoo_watch._avisar_campana`
  (campana→`ops.webhook_events`), `publicar_ready._backlog_ml` y
  `_anotar_pausa_backlog` (ml_backlog→`ops.channel_submissions`),
  `publicar._guardar_backlog_ml` y `_guardar_backlog_amazon`
  (ml/amazon_backlog→`ops.channel_submissions`), `imagenes_editor._backlog`
  (ml_image_edit_backlog→`ops.channel_submissions`),
  `imagenes_amazon._cache_put` (amazon_imagenes→`enrich.product_media`),
  `crear_producto._persistir_log` (crear_logs→`ops.process_log`).
  Siempre resumen + `detail_ref='mysql:<tabla>:<id>'`: los blobs NO viajan.
- **Cubierto por el compañero (NO se duplica)**: webhooks ML
  (`SUPABASE_DUAL_WRITE`), `canal_inventario` (channel_mirror), costos
  (costing_mirror), y los upserts de `ml_progress`/`amazon_progress` (el
  estado del listing viaja por channel.listings).
- **GAP sin destino v4**: `pedidos_ml` (pedidos ML/Amazon/M2E — el corazón del
  tab Ventas). Propuesta de DDL en
  `docs/arquitectura_bd/propuesta_ops_orders.sql`, PENDIENTE del GO de
  Eduardo. No se espeja nada de pedidos hasta entonces.
- **No aplica**: `ventas_horarias`/`ventas_sync` (caché regenerable),
  `productos.stock_odoo` (foto local, Odoo en retiro). **Bloqueado**:
  `ml_tokens*` (P3, secretos→Vault).

**Arquitectura** (`services/kubera_mirror.py`): pool propio de 3 conexiones a
`KUBERA_DB_URL` (`connect_timeout=4`, `blocking=False`), `espejar()`
fire-and-forget (executor si hay loop, hilo daemon si no) con try/except
total — un fallo del espejo JAMÁS toca el flujo; upserts idempotentes según
las llaves del v4 (`ON CONFLICT` en webhook_events; dedup por `detail_ref` en
submissions/process_log; update-else-insert en product_media);
`set_config('app.via','kubera_mirror',true)` y `statement_timeout` por
transacción (compatible pooler 6543). Registro: ring buffer de 500 eventos +
contadores por (archivo, función, tabla) + errores persistidos en la tabla
LOCAL nueva **`espejo_kubera_log`** (MySQL, a propósito: si kubera está caída
el error se guarda igual; columnas resuelto/resuelto_ts para la limpieza).

**Flags** (Railway, apagables sin deploy): `KUBERA_MIRROR_ENABLED`
(default **false** — el código en main es inerte), `KUBERA_MIRROR_TABLAS`
(CSV de tablas origen para encendido gradual), `KUBERA_DB_URL` (en DEV el
Supabase de desarrollo). **Encenderlo en producción = cambio de flujo vivo:
esperar el dale de Brandon** (regla 3).

**Página /migracion** (+ navbar "Migración"): tarjeta por escritor con estado
(verde=activo, ámbar=apagado, azul=cubierto, gris=gap/no aplica), contadores
ok/error, latencia media y último evento; feed en vivo (poll 5 s) con error
expandible; vista "Errores para limpieza" agrupados por (archivo, tabla,
tipo) con ejemplo, payload y botón **Marcar resuelto** (la lista ES el plan
de limpieza). Endpoints: `GET /api/migracion/estado|eventos|errores`,
`POST /api/migracion/errores/resolver` (con `requiere_api_key`).

**Pruebas ejecutadas** (2026-07-22):
- *Inocuidad*: flag OFF → 200 llamadas en 0.03 ms totales, cero eventos;
  flag ON con BD inalcanzable → el llamador regresa en <1 ms, el error queda
  en ring buffer y en `espejo_kubera_log`. El flujo actual, intacto.
- *Corrida real* contra un Postgres 16 local con el DDL v4 aplicado
  (`ESQUEMA_kubera_v4_propuesto.sql`; solo fallaron las piezas
  Supabase-only: `auth.users` y grants a `service_role`): filas verificadas
  por SELECT en `ops.webhook_events` (idempotencia comprobada: re-envío no
  duplica), `ops.channel_submissions` (dedup por detail_ref),
  `enrich.product_media` (upsert actualiza sin duplicar), `ops.process_log`;
  y un **error FK inducido** (SKU fantasma vs `core.products`) capturado sin
  interrumpir nada y visible/resoluble en /migracion (botón probado
  end-to-end). 7 ok / 1 error en contadores.
- Pendiente con credencial real: apuntar `KUBERA_DB_URL` al Supabase DEV
  (la credencial no vive en esta máquina) y repetir la corrida.

**Hallazgo para el DDL v4** (para Eduardo): `enrich.product_media` no tiene
UNIQUE natural — un índice único `(sku, kind, source_url)` volvería atómico
el upsert del espejo (hoy se emula con update-else-insert).

### Archivos tocados (v0.13.0)

- **Nuevo** `services/kubera_mirror.py` (censo + espejo + registro),
  `routers/migracion.py`, `frontend/app/migracion/page.tsx`,
  `docs/arquitectura_bd/propuesta_ops_orders.sql`.
- Llamadas `espejar()` en: `services/odoo_watch.py`,
  `services/publicar_ready.py`, `services/publicar.py`,
  `services/imagenes_amazon.py`, `services/imagenes_editor.py`,
  `services/crear_producto.py` (siempre tras el éxito MySQL; en
  imagenes_editor/crear_producto el INSERT ahora captura `lastrowid` para el
  detail_ref — mismo SQL, mismo autocommit).
- `config.py` → `kubera_db_url`, `kubera_mirror_enabled`,
  `kubera_mirror_tablas`. `main.py` → router migracion + versión 0.13.0.
- `frontend/components/AppNavbar.tsx` → entrada "Migración".

### Archivos tocados

- `routers/webhooks.py` → pedido WC en la rama `orders_v2` + flags en `/estado`.
- `services/pedidos_ml.py` → `sincronizar(..., orden=)` acepta la orden prefetched.
- **Nuevo** `services/odoo_watch.py` + job en `services/scheduler.py`.
- `services/publicar_ready.py` → prioridad de categoría del panel.
- `config.py` → `pedidos_wc_*`, `odoo_watch_*`.

### v0.14.0 — /migracion gráfica: camino al corte (racha 14 días) + actividad del espejo

**Contexto.** El espejo kubera quedó ENCENDIDO en producción el 2026-07-22
(dale de Brandon, vía Eduardo): `KUBERA_MIRROR_ENABLED=true`,
`KUBERA_MIRROR_TABLAS=crear_logs` (encendido gradual), `KUBERA_DB_URL` como
variable de referencia `${{ SUPABASE_DB_URL }}` en Railway. En staging está
encendido sin filtro de tablas. Mismo día: GO de Eduardo al GAP de pedidos —
`channel.orders` creada en la BD kubera (ver
`docs/arquitectura_bd/propuesta_ops_orders.sql`, marcada APLICADA) + índice
único `uq_product_media_sku_kind_url` en `enrich.product_media` (el upsert del
espejo ya puede ser atómico). El seam de `pedidos_ml` → `channel.orders` queda
LISTO PARA CONSTRUIRSE (censo: pasar de `gap_sin_destino` a `a_espejar`).

**Qué se construyó.** La página /migracion ahora es el monitor gráfico en
tiempo real de TODA la migración, no solo del espejo:

1. **"Camino al corte"** — tarjeta por dominio (Costos, Channel) con la racha
   de días consecutivos con actas de deltas en CERO (criterio de corte: 14),
   barra de progreso, los últimos 14 días como puntos (verde ok / rojo
   con_deltas / gris sin acta) y la última acta con hora y resultado. Fuente:
   `GET /api/migracion/deltas` (nuevo), que lee
   `migration.reconciliation_runs` de la BD kubera vía `services/supabase_db`
   (solo lectura, best-effort: sin BD configurada devuelve
   `disponible=false` y la página no se rompe). Regla de racha: la ÚLTIMA
   acta del día manda (una re-corrida que corrige el delta conserva el día);
   racha = días CONSECUTIVOS en ok terminando en el día más reciente.
2. **"Actividad del espejo"** — gráfica de barras apiladas (ok verde / error
   rojo) por minuto de los últimos 30 min, construida del ring buffer de
   `/api/migracion/eventos` que ya se pollea cada 5 s. Sin librerías nuevas:
   divs + Tailwind, el mismo patrón de la gráfica del tab Ventas.

### Archivos tocados (v0.14.0)

- `routers/migracion.py` → `GET /deltas` (actas + racha por dominio;
  `OBJETIVO_RACHA=14`).
- `frontend/app/migracion/page.tsx` → secciones "Camino al corte" y
  "Actividad del espejo" (poll de actas cada 60 s; serie de 30 min con
  `useMemo` sobre los eventos existentes).
- `backend/main.py` → versión 0.14.0 (dos lugares).

### v0.14.1 — Fix: /migracion sin barra de navegación

La página /migracion no montaba `<AppNavbar />` (cada página lo monta por su
cuenta; el layout no lo trae) — al entrar se perdían las pestañas del panel.
Reporte de Eduardo. Se envolvió igual que las demás páginas:
`<div className="min-h-screen"><AppNavbar /><main …>`. Versión 0.14.1.

### v0.14.2 — Fix: barras invisibles en "Actividad del espejo"

Las columnas de la gráfica no tenían altura definida (`h-full` faltante), así
que las alturas porcentuales de las barras se resolvían a 0 — la gráfica salía
"vacía" aun con eventos (reporte de Eduardo, con los PRIMEROS 8 eventos reales
del espejo en producción: crear_logs → ops.process_log, 8 ok / 0 error,
~400 ms, 20:25 UTC del 2026-07-22). Versión 0.14.2.

### v0.14.3 — La categoría del panel manda también sobre WooCommerce al publicar

**Incidencia (reporte de Eduardo, caso CAM-0034-BEI):** el panel mostraba la
categoría corregida (MLM69819 Colchones Inflables) pero la publicación salió
con la inicial (MLM419960 Colchonetas Aislantes). Causa: además de las metas
`ml_categoria_id`/`ml_category_id` (arreglo del caso TEC-1812-NEG), el vendor
tiene un TERCER decisor: `publisher_core.build_payload` consulta
`wc_category_mapping` y, si la categoría WooCommerce del producto trae el
patrón `"ML: MLM###"` en su description, ESA gana sobre la meta (política
vieja "las KAMs editan la categoría en Woo"). CAM-0034-BEI seguía asignado en
Woo a "Colchonetas Aislantes" (term 1852) → override silencioso. El mapeo
además se cachea 1 h en memoria.

**Arreglo (adaptador, vendor intacto):** `publicar_ready.construir_prod` ya no
pasa `wc_categories` al pipeline cuando el producto tiene categoría elegida
(`ml_categoria_id` del panel o `ml_category_id` del picker/predictor) — sin
insumo, el override no puede activarse y la elección del panel manda (regla de
la casa #2). Sin elección en el panel, el mapeo WC sigue siendo el fallback,
igual que antes. `wc_categories` no tiene otro consumidor (verificado con grep:
solo `publisher_core`/`wc_category_mapping`).

**Operativo pendiente:** los 2 items pausados de CAM-0034-BEI creados el
22-jul con la categoría vieja (MLM5781002168 BEKURA, MLM3175968815
SANCORFASHION) hay que borrarlos en ML + limpiar sus filas de `ml_progress`, y
republicar ya con este fix. Versión 0.14.3.

### v0.15.0 — El publicador detecta publicaciones eliminadas en ML y las re-crea

**Incidencia de fondo (3 casos el 22-jul: TEC-1812-NEG, MOD-0496-NUDE,
CAM-0034-BEI):** al dar de baja una publicación en el seller central, la
bitácora `ml_progress` queda congelada diciendo "publicado". El botón del
Studio decidía crear/actualizar leyendo SOLO esa bitácora → intentaba
actualizar items muertos y nunca re-creaba; el remedio era SQL manual
(borrar las filas) con ventana de duplicados si alguien publicaba en medio.

**Cambios (`services/publicar.py` + `services/publicar_ready.py`, vendor
intacto):**

- `_estados_items_ml()`: antes de decidir el modo, `GET /items/{id}` por cada
  cuenta registrada (~1 s). Item `closed` o con `deleted` en sub_status (o
  404) = muerto → esa cuenta pasa a modo CREAR; vivo (`active`/`paused`) →
  actualizar como siempre. Ante duda (sin token, timeout, 5xx) se asume vivo:
  mejor fallar un update que crear un duplicado por error transitorio.
- `crear_ml(..., cuentas=[...])`: el alta ahora puede restringirse a cuentas
  específicas (antes era todo-o-nada en ambas) → resuelve el caso mixto
  TEC-1812 (una cuenta viva, la otra eliminada).
- Caso mixto en `_confirmar_ml`: actualiza las vivas y re-crea (pausada) en
  las muertas en la misma confirmación; cada fila de resultado lleva
  `modo` propio ("crear"/"actualizar") para que el modal pinte lo correcto.
- La bitácora se cura sola: el hook de creación pisa la fila vieja de
  `ml_progress` con el item nuevo — ya NO hace falta borrar filas a mano.
- Preview honesto: el modal avisa por cuenta, p. ej. *"BEKURA: la publicación
  anterior (MLM…) fue eliminada en Mercado Libre — se CREARÁ una nueva
  (pausada)."* — antes el modo actualizar salía sin ningún aviso.
- Frontend: `PublicarResultadoCuenta.modo` opcional y el modal usa
  `(r.modo ?? resultadoPub.modo)` (una línea en ProductStudio.tsx).

**Flujo operativo nuevo** cuando se dé de baja una publicación: usuarios la
borran en ML → botón Publicar del Studio → el panel avisa y re-crea pausada.
Sin SQL, sin ventana de duplicados (la verificación es en vivo). Versión
0.15.0.

### v0.15.1 — Hotfix: `_error_ml` tronaba con `cause` no-lista (500 disfrazado de "Error de conexión")

Caso EST-0091 (22-jul, ~01:06 y 01:53 UTC del 23): al actualizar la
publicación viva de SANCORFASHION, ML respondió un error cuyo `cause` venía
como ENTERO; `_error_ml` lo iteraba a ciegas → `TypeError: 'int' object is
not iterable` → 500 → el modal lo pintaba como "Error de conexión al
publicar" (mensaje del catch genérico del frontend) y el flujo abortaba ANTES
de re-crear la cuenta muerta (por eso "no se publicó en BEKURA"). Fix:
`_error_ml` ahora acepta `cause` como lista, dict suelto o escalar, y castea
`message`/`error` a str. Con esto el modal muestra el ERROR REAL de
validación de ML. Versión 0.15.1.

### v0.15.2 — Espejo kubera: pool 3→6 + reproceso de errores pendientes

La madrugada del 23-jul una tanda de creaciones dejó 60 eventos
`crear_logs → ops.process_log` sin espejar (`TooManyConnections`: el pool
local del espejo topaba en 3 conexiones y por diseño NO espera — registra el
error con su payload y suelta). Dos cambios (área del espejo propio, pedido
por Eduardo):

- **Pool 3→6 conexiones** (`maxcached` 2→3) en `kubera_mirror._get_pool` —
  sigue sin bloquear; solo aguanta ráfagas del pipeline de Crear.
- **`kubera_mirror.reprocesar_errores()`** + endpoint
  `POST /api/migracion/errores/reprocesar?max_items=500`: re-aplica los
  errores `resuelto=0` desde su `payload_json` (secuencial, una conexión,
  upserts idempotentes) y los marca `resuelto=1`. Los payloads truncados/
  ilegibles se saltan y se reportan. A diferencia de `/errores/resolver`
  (que solo marca), este SÍ escribe los datos perdidos. Versión 0.15.2.

### v0.15.3 — Espejo kubera: cola acotada + 2 workers (la ráfaga ya no puede tirar intentos)

**Mismo incidente que v0.15.2, atacado de raíz** (los dos fixes se
complementan: se desarrollaron en paralelo y este se montó encima). Con el
despacho original (un hilo por intento y ~420 ms por escritura a Supabase),
CUALQUIER ráfaga con más concurrencia que el pool pierde intentos — subir el
pool a 6 aleja el umbral pero no lo elimina (~10% perdido en la del 23-jul).

**Fix (`services/kubera_mirror.py`):** `espejar()` ya no despacha hilos — solo
hace `put_nowait` en **colas acotadas (500 c/u)** que drenan **2 workers
daemon** con **afinidad por clave**: la misma (tabla, clave) cae siempre en el
mismo worker → los eventos de una misma orden/SKU se aplican en orden FIFO
(dos updates en ráfaga no pueden invertirse — carrera real cazada por la
prueba local); claves distintas van en paralelo. ≤2 conexiones en uso del
pool de 6: el pool no puede agotarse por ráfagas y quedan 4 para
`reprocesar_errores`. El llamador sigue sin esperar nada (100 llamadas
encoladas en 1.2 ms, medido). Cola llena (≈7 min de ráfaga sostenida) = el
intento se descarta PERO queda como evento `ColaLlenaError` en memoria (sin
escribir MySQL en el camino crítico). Probado contra Postgres local: ráfaga
de 100 → 100 espejadas, 0 perdidas, 0 TooManyConnections, y orden por clave
verificado. Con esto: la cola PREVIENE pérdidas nuevas y el reproceso de
v0.15.2 RECUPERA las históricas — tras correrlo, el grupo
`TooManyConnectionsError` queda saldado.

### v0.16.0 — Pedidos espejados a `channel.orders` (GAP cerrado con el GO de Eduardo)

Eduardo aplicó el DDL propuesto (`docs/arquitectura_bd/propuesta_ops_orders.sql`,
2026-07-22) en la BD kubera — `channel.orders` + trigger touch + el índice
único `uq_product_media_sku_kind_url` en `enrich.product_media` — y dejó como
siguiente paso el seam. Hecho:

- **`services/pedidos_ml.py::sincronizar`**: tras el upsert exitoso en MySQL
  `pedidos_ml`, el pedido viaja a `channel.orders` vía `kubera_mirror.espejar`.
  El mapeo cuenta→canal/tarjeta: BEKURA/SANCORFASHION→`mercado_libre`
  (tarjeta pedidos_ml.py), AMAZON→`amazon` (tarjeta pedidos_amazon.py),
  TEMU/TIKTOK→`temu`/`tiktok` (tarjeta pedidos_m2e.py) — los contadores de
  /migracion cuentan donde el censo los espera.
- **Semántica FIEL a MySQL**: en conflicto (PK canal+cuenta+orden) solo se
  mueven `wc_order_id`/estados/`actualizado_at`; total, comisión, skus y
  creado_at quedan CONGELADOS al primer registro. Bonus: `skus` va como array
  citext[] COMPLETO (el CSV de MySQL trunca a 255 chars).
- `enrich.product_media` pasa a upsert **atómico** (`ON CONFLICT` sobre el
  índice nuevo) — se retira el update-else-insert.
- Censo: las 3 entradas de pedidos pasan de `gap_sin_destino` a `a_espejar`.
- Probado contra Postgres local con el DDL aplicado: alta + re-envío (no
  duplica, estado se mueve, total congelado), FK de canal OK para
  amazon/temu, atribución por tarjeta correcta.

**OJO — sigue INERTE en producción**: `KUBERA_MIRROR_TABLAS=crear_logs` no
incluye `pedidos_ml`; espejar pedidos se enciende agregando `pedidos_ml` al
CSV (dale de Brandon). Versión 0.16.0.

### v0.16.1 — Backfill de amazon_imagenes → enrich.product_media + encendido de tablas

Complemento del monitoreo del espejo (GO de Eduardo). El índice único
`(sku, kind, source_url)` ya existía (lo creó Eduardo el 22-jul) y el upsert
atómico llegó en v0.16.0 — faltaba el historial y el encendido:

- **`POST /api/migracion/backfill/product-media?max_items=1000`**: copia
  one-shot del caché `amazon_imagenes` de MySQL (254 imágenes, 87 SKUs) al
  destino; idempotente. De paso verifica el índice: sin él, el ON CONFLICT
  fallaría aquí y no en el flujo vivo.
- **Tablas encendidas** en `KUBERA_MIRROR_TABLAS`: se suman `amazon_imagenes`
  y `ml_image_edit_backlog` (quedando: crear_logs, ml_backlog, amazon_backlog,
  amazon_imagenes, ml_image_edit_backlog). `webhook_eventos` fuera a propósito
  (volumen + dual-write existente). `pedidos_ml` NO se enciende aún — el seam
  v0.16.0 está listo pero es flujo de ventas: dale de Brandon pendiente.
  Versión 0.16.1.

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
