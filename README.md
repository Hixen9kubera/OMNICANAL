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

### v0.16.2 — Backfill de pedidos históricos → channel.orders

`POST /api/migracion/backfill/channel-orders?max_items=5000`: copia el
histórico completo de `pedidos_ml` (3,522 pedidos desde el 13-may: BEKURA
1,867 · SANCORFASHION 1,605 · AMAZON 50) al esquema v4, con el mismo upsert
del seam v0.16.0 — idempotente y sin alterar los pedidos ya espejados en
vivo (el conflicto congela total/comisión/skus/creado_at). Mismo mapeo
cuenta→canal que `_ESPEJO_ORIGEN`. Limitación conocida: los SKUs del
histórico vienen del CSV MySQL truncado a 255 (los pedidos en vivo llevan el
array completo). Reporta cada pedido fallido (hasta 100) para revisión.
Versión 0.16.2.

### v0.16.3 — Backfill de pedidos por tandas (offset)

La corrida completa (3,522 upserts seriales) excede el timeout del proxy de
Railway: la respuesta (con el reporte de fallos) se perdía aunque el trabajo
terminara en el servidor. `backfill_channel_orders` acepta `offset` y se
corre en tandas de ~500 que sí regresan su reporte. Versión 0.16.3.

### v0.16.4 — Comparador orders-deltas: pedidos entra al camino al corte

Nuevo job de paridad `backend/scripts/comparar_orders.py` (patrón de
comparar_channel: pasada completa → reconfirmación 75 s → acta en
`migration.reconciliation_runs`, dominio `orders-deltas`) y dominio
"Pedidos" registrado en `_DOMINIOS_DELTAS` (/migracion muestra su racha).
Reglas propias del dominio: filas calientes <20 min excluidas (cola del
espejo), SKUs por SUBCONJUNTO (CSV MySQL trunca a 255 vs array completo),
`solo_en_supabase` SÍ es delta (sin fusión ETL), `creado_at` no se compara,
y COMISIÓN 0 en MySQL = no observada (hallazgo real de la 1ª corrida: 7
pedidos con comisión congelada en 0 cuyo valor correcto solo está en
Supabase — el espejo más fiel que la fuente). Detalles psycopg2: `skus`
se lee `::text[]` (citext[] llega como cadena si no). Primera corrida
limpia el 23-jul: 3,533 vs 3,558 pedidos, DELTA = 0 → racha 1/14.
Corre como servicio Railway `deltas-orders` (clon de deltas-channel).
Versión 0.16.4.

---

### v0.17.0 — 4 correcciones: título Amazon sin acentos · comisión $0 se rellena · picker de categoría persiste · precio regular verificado

Cuatro arreglos pedidos por Brandon (2026-07-23):

1. **Título de Amazon SIN acentos.** El prompt de mejorar Amazon ahora pide el
   título sin tildes, y hay un **blindaje determinista** (`_sin_acentos`, NFKD)
   que los quita del título tras el parseo — por si el LLM deja alguno. Solo
   afecta el TÍTULO de Amazon; el resto del contenido conserva su ortografía.

2. **Comisión $0 → valor real.** Congelar la comisión protege el dato histórico
   de la venta, PERO un `0` no es histórico: es un dato que **nunca se calculó**
   (token de ML caído al crearse el pedido). El `ON DUPLICATE` de `pedidos_ml`
   ahora permite el paso **0 → valor** (`comision=IF(comision=0, VALUES, comision)`);
   un valor ya puesto (>0) sigue siendo **inmutable**. Corrección histórica:
   **641 pedidos** ML (BEKURA+SANCOR, no cancelados) re-consultados a ML por su
   `sale_fee` real y actualizados (**641/641** resueltos, comisión ML total
   ahora ≈ **$239,558** entre ambas cuentas). Se **excluyeron** los cancelados
   (una venta cancelada no tiene comisión
   neta) y **Amazon** (comisión sigue en 0 hasta tener Finances API, pendiente #5).
   Auditoría del día: la vista de pedidos coincide con las métricas de ML
   (captura de BEKURA: $36,789 / 88 ventas ≈ pedidos $36,902 / 89).

3. **Picker de categoría ML: ahora PERSISTE.** Bug: elegir una categoría en el
   Estudio solo cambiaba estado local (`catMlId`) y **nunca se guardaba** — al
   recargar volvía la anterior, y "Mejorar con IA" seguía leyendo los niveles
   VIEJOS (por eso ACC-0653 regeneraba "binoculares" tras cambiar la categoría).
   Fix: nuevo endpoint `POST /api/crear/categoria-ml` que escribe `ml_categoria_id`
   + niveles + path (las metas que lee el publicador — elección humana que MANDA);
   el picker ahora guarda al elegir, actualiza el breadcrumb en vivo y alimenta a
   la IA con la categoría VIGENTE. Además se aclaró en la UI que la categoría ML
   **es la que se envía a Mercado Libre** y la de WooCommerce es **solo para la
   tienda web** (por eso aparecen las dos).

4. **Precio REGULAR verificado en todos los canales.** Confirmado: ML
   (`publicar_ready`: `precio_regular`→`_regular_price`) y Amazon (`publicar`:
   solo `precio_regular`) publican con el **precio regular**, nunca el de oferta.
   El Estudio precarga `precio_regular = precio_base` (no `precio_sugerido`).


---

### v0.17.1 — Reintentos al publicar en ML (hasta 3×) + reestructura variable de CAM-0030

- **Reintentos por cuenta en `crear_ml`** (`MAX_INTENTOS_ML=3`): al crear una
  publicación ML, un fallo se reintenta hasta 3 veces con backoff (2s, 4s). Cubre
  fallos **transitorios** (timeout, 5xx, token en transición) — resuelve el caso
  "raro" de que BEKURA publique y SANCORFASHION no. **NO se reintentan** los
  errores **deterministas** de configuración (`gtin_error`, `needs_manual_config`):
  el mismo payload fallaría igual y solo spamearía a ML; ésos requieren acción
  humana. Cada resultado reporta `intentos`.
- **Por qué SANCORFASHION fallaba y BEKURA no** (diagnóstico, no era transitorio):
  la categoría de colchones (MLM121837) en SANCOR **rechaza el placeholder GTIN
  `0000000000000` Y el `EMPTY_GTIN_REASON` ("sin código universal")** y exige un
  código de barras REAL; en BEKURA la misma categoría acepta el placeholder. Es
  una restricción **a nivel de cuenta** de ML (SANCOR en un tier de validación de
  GTIN más estricto). El fix real para SANCOR es un **GTIN real** en `_barcode`
  del producto — el reintento no lo resuelve porque no es transitorio.
- **Publisher = solo SIMPLES** (confirmado): `construir_prod` arma UN producto por
  `wc_id`; no lee variaciones. Al publicar CAM-0030 se envía el **padre como
  simple** (BEKURA ya tiene su ítem `MLM5792668714` pausado — se deja pausado, es
  la política: toda publicación nace paused vía `asegurar_pausado`).
- **Reestructura CAM-0030 en Woo** (organización de catálogo): `agrupar_bases`
  colgó `CAM-0030-IND` (era producto suelto) como 4ª variación del padre 104732
  (stock 258 preservado) y se activaron `MAT`/`QUE` (estaban draft). Padre
  variable `inprogress` con EST/IND/MAT/QUE. (Nota: los sufijos de talla se
  parsean como `Modelo: Ind/Mat/Que` y `EST` cae en `Color: Estampado` — imperfecto
  pero irrelevante para ML, que recibe el padre como simple.)
### v0.17.2 — El espejo de pedidos adopta la regla de comisión 0→valor

La corrección v0.17.0 (641 comisiones rellenadas en MySQL) dejó a
`channel.orders` retratando el pasado: sus comisiones seguían congeladas en 0
y el comparador `orders-deltas` habría roto la racha (con razón) en su día 2.
`_up_channel_orders` adopta la MISMA cláusula que el ON DUPLICATE de MySQL:
`comision = 0 → excluded.comision`, nunca re-tocar un valor ya puesto. Se
re-corrieron las tandas del backfill para propagar las 641 correcciones y el
comparador verificó paridad. Versión 0.17.2.

### v0.17.3 — Seam Crear → core.products: el catálogo tiene registro civil

Con KuberaPipelineV1.0 desconectándose y Odoo en retiro, el panel queda como
la ÚNICA sala de partos de SKUs — pero `core.products` (la columna vertebral
del v4: todo le hace FK) se alimentaba solo por ETL desde la tabla congelada
del robot. Consecuencia real: 82 SKUs sin registrar y FK violations en
`ops.channel_submissions` al publicar/editar productos nuevos (CAM-0030,
JUGU-1177 — 5 errores en /migracion el 23-jul).

Hecho (GO de Eduardo):

- **Backfill**: re-corrida del ETL oficial `etl_core_products.py` →
  core.products 22,067 → 22,150 SKUs (+83); paridad de costos delta 0 en el
  mismo run. Los 5 errores FK se re-aplicaron con `/errores/reprocesar`
  (5/5 ok) → lista de limpieza en CERO.
- **Seam permanente**: `crear_producto.py` (paso 9, tras el éxito en Woo)
  espeja el nacimiento a `core.products` vía `_up_core_product` — upsert por
  sku que solo toca name/wc_id/status/source; lo que enriquece el ETL
  (odoo_id, brand, parent_sku, tags) NUNCA se pisa (probado contra DEV:
  idempotente, sin duplicar, source original conservado). Censo: nueva
  tarjeta "crear (nacimiento)" (origen `wp_posts`). Se enciende agregando
  `wp_posts` a `KUBERA_MIRROR_TABLAS`. Versión 0.17.3.

### v0.17.4 — INCIDENTE y recuperación: TRUNCATE CASCADE de etl_core_products

**Qué pasó (24-jul ~05:27 UTC).** La re-corrida de `etl_core_products.py`
(para registrar los 82 SKUs del hallazgo FK) ejecutó su
`TRUNCATE core.products CASCADE` (línea 282): además de reconstruir
core+costing (que quedaron perfectos), **vació en silencio TODAS las tablas
con FK a core.products** que el script NO recarga: `channel.listings`,
`ops.channel_submissions`, `enrich.product_media` (+ `supplier_data`,
`ai_attributes`, `product_category` — verificado con pg_stat el 24-jul:
esas 3 estaban VÍRGENES, cero inserciones históricas; sin daño ni aviso
necesario). El acta channel-deltas
del 24-jul lo cazó en horas (`con_deltas`, 3,758 solo_en_mysql): el sistema
de auditoría funcionó. Racha de channel: reiniciada.

**Recuperación (mismo día, todo desde las fuentes MySQL intactas):**
`etl_channel_listings` → 5,616 listings (deltas 0); backfill product-media →
262/262; nuevo `POST /api/migracion/backfill/channel-submissions?tabla=…`
(payloads idénticos al espejo en vivo, idempotente por detail_ref) →
reconstruye ml_backlog/amazon_backlog/ml_image_edit_backlog.

**⚠️ REGLA NUEVA: `etl_core_products` es herramienta de FASE (demolición +
reconstrucción). NO re-correrlo con el espejo vivo salvo que inmediatamente
después se re-corran etl_channel_listings + los 3 backfills de submissions/
media Y se avise a José por sus tablas enrich/category. Para "solo faltan
SKUs nuevos" el camino correcto es el seam v0.17.3 (automático) — no el ETL.
Propuesta pendiente a Eduardo: cambiar el TRUNCATE CASCADE por upsert.**
Versión 0.17.4.
### 📣 Aviso 2026-07-23 — DECISIÓN: KuberaPipelineV1.0 se DESCONECTA

**Para el equipo y las sesiones de Claude de Brandon** (decisión comunicada por
Eduardo, dueño de la migración):

- El pipeline externo **KuberaPipelineV1.0** (robot de Alibaba: scraping →
  atributos IA → alta masiva) **se va a desconectar**. NO se redirige a la BD
  kubera: sus 13 tablas de MySQL pasan a **legado congelado** (ETL one-shot de
  las valiosas al corte; retiro del resto — `ml_estado` está vacía y
  `odoo_sync_*`/`sync_procesados` muertas desde mayo).
- **NO correr más tandas del robot** (última corrida detectada: 22-jul 00:36).
  Coordinar fecha de desconexión con quien lo opera.
- El servicio **`publicador`** (Railway) lee las tablas del robot
  (`productos`, `atributos_ia`) → retirarlo en el mismo movimiento; además
  trae el `client_secret` de ML expuesto (pendiente #8, rotar al retirarlo).
- **Implicación crítica**: la tabla `productos` de MySQL (fuente única del ETL
  de `core.products`) queda muerta. Desde hoy los productos nacen SOLO en el
  panel (Crear) → hace falta el **seam Crear → core.products** vía el espejo
  + backfill de los **82 SKUs** que ya faltan (hallazgo del 23-jul: FK
  violations de `ops.channel_submissions` con CAM-0030 — están en la vista
  "errores para limpieza" de /migracion, NO marcarlos resueltos sin insertar
  antes los productos). Ese seam es ahora EL bloqueador del corte.
- La creación de productos en el panel NO se ve afectada: el flujo Crear no
  depende del robot.

---

### v0.17.2 — Campo GTIN / código de barras en el Estudio (desbloquea SANCORFASHION)

- **Nuevo campo "Código de barras / GTIN"** en el Estudio del producto (sección de
  categoría). Se guarda en WooCommerce (`_barcode`) vía `POST /api/crear/gtin`
  (valida 8-14 dígitos); el metadata del Estudio ahora lo expone (`wp_db` lee
  `_barcode`/`_gtin`). El publisher ML ya lo usaba de primera opción en su cadena
  de GTIN — ahora es editable desde el panel.
- **Probado en vivo**: con un GTIN en el campo, la publicación a **SANCORFASHION
  SÍ tuvo éxito** (item creado y pausado) — el campo fluye al payload (confirmado
  en preview) y ML lo acepta al crear. Sin GTIN, la categoría de colchones
  (MLM121837) en SANCOR rechaza (placeholder + EMPTY_GTIN_REASON), por eso BEKURA
  publicaba y SANCOR no. **El GTIN debe ser REAL** (ML lo valida contra su base);
  la prueba usó un GTIN de formato válido y se limpió después (publicación de
  prueba cerrada, campo borrado).
- **Cómo usarlo**: poner el código de barras real del producto en el campo →
  guardar → Publicar en SANCOR.

### v0.17.5 — Suma FULL/FBA en el detalle de producto (Omnicanal)

Primera pieza visible del modelo **Drop/Full** acordado con Eduardo (24-jul):
DROP = almacén propio (el número de Woo, compartido entre canales no-FULL);
FULL/FBA = bodegas del marketplace (un cajón por cuenta ML + el de Amazon),
solo lectura — el marketplace las descuenta al vender y nosotros las leemos.

- En el **drawer de detalle** (clic a un producto en la pestaña Omnicanal), la
  tarjeta **General** ahora muestra una tercera métrica junto al stock real:
  **FULL/FBA** = suma de las piezas del SKU en bodegas de marketplace (FULL de
  BEKURA + FULL de SANCORFASHION + FBA). Se calcula al momento desde los
  canales del detalle — **nunca se almacena** (un total guardado se congela y
  miente; mismo patrón que las comisiones en 0).
- **El stock real NO se modifica** — solo se le pone la suma al lado. La
  columna aparece únicamente si el producto tiene piezas en bodegas; si no,
  la tarjeta General queda como siempre (2 columnas).
- Las tarjetas por cuenta de ML y la de Amazon (que ya mostraban su FULL/FBA
  individual) quedan intactas.
- Verificado en local con datos reales: TEC-1032-NEG-SOL → FULL/FBA 539 u
  (192 BEKURA + 347 SANCORFASHION + 0 FBA); CAM-0030 (sin piezas en bodegas)
  → tarjeta normal sin columna extra.
- Un solo archivo tocado: `frontend/components/ProductDetailDrawer.tsx`
  (feature de UI/lectura — deploy directo a main). Versión 0.17.5.

### v0.17.6 — CAM-0030 separado por tamaño · precio REGULAR en padres variables · "Publicado" por canal

**1. CAM-0030 separado en 3 publicaciones ML independientes** (6 ítems: 3 tamaños
× 2 cuentas, TODAS pausadas). Hallazgo previo: la publicación viva tenía
atributos de MATRIMONIAL (135×190) pero `SELLER_SKU=CAM-0030-IND` — el SKU es lo
que liga la venta al inventario, así que una venta habría descontado el
individual. Corregido a `CAM-0030-MAT` en ambas cuentas + publicadas Individual
(100×190) y Queen (160×200) con sus imágenes y los mismos atributos.

| Tamaño | BEKURA | SANCORFASHION |
|---|---|---|
| Matrimonial | MLM3183258785 | MLM5793156390 |
| Individual | MLM3188977035 | MLM5802621580 |
| Queen | MLM3188977305 | MLM5802621930 |

⚠️ **Ojo — un PUT de precio/stock REACTIVA la publicación** (ML lo avisa: "se
reactivaron porque hiciste cambios en su stock o estado"). Pasó con BEKURA Queen;
se volvió a pausar. **Tras cualquier edición masiva, re-verificar el status.**

**2. FIX precio REGULAR en padres variables** (bug real, encontrado al publicar):
un padre `variable` NO guarda `_regular_price` propio, así que `construir_prod`
caía al `_price` — que es el de OFERTA. CAM-0030 se publicó en **$6,514.97** en
vez de **$7,755.92**. Nuevo `wp_db.precio_regular_variantes(wc_id)` (mínimo de
las variantes) se consulta ANTES del fallback; los 4 ítems afectados se
corrigieron a mano. La regla "siempre precio regular" ya se cumple también para
variables.

**3. "Publicado / Sin publicar" ahora se decide por CANAL, no por WooCommerce.**
Antes el badge usaba `status == publish` de Woo: CAM-0030 salía "Sin publicar"
estando vivo en las 2 cuentas de ML. Ahora: **≥1 canal con publicación viva →
Publicado; 0 canales → Sin publicar**, y las **variantes cuentan** (tras separar
un padre, las publicaciones cuelgan de los SKUs hijos). `presencia_por_sku` suma
`canal_inventario` como fuente PRIMARIA (sync 15 min + webhooks = lo más fresco;
las `closed` no cuentan). Verificado: CAM-0030 → `publicado=True`;
TEC-2365-ROJ (sin canales) → `False`.

**4. Propuesta DDL (NO aplicada, requiere GO de Eduardo)** en
[`docs/arquitectura_bd/propuesta_inventario_drop_full.sql`](docs/arquitectura_bd/propuesta_inventario_drop_full.sql):
`channel.order_items` (cantidades por línea — hoy `orders.skus` es un array sin
unidades) + `channel.inventory_moves` (ledger append-only drop/full/fba con
venta/devolución/cancelación/ajuste, idempotente por orden+sku) + vistas de
conciliación foto-vs-ledger. Versión 0.17.6.

### v0.17.7 — Notificador de alertas a Slack (Fase 1)

Respuesta a los 3 incidentes de la semana que nadie detectó a tiempo (acta rota
del TRUNCATE, cron fantasma de deltas-orders, ingest de José caído 2 días).
Canal dedicado **#alertas-omnicanal** vía webhook entrante de Slack; el
remitente es este backend (app "Kubera Alertas" — buzón de un solo sentido, no
hay bot real).

**Arquitectura** (`services/alertas.py`): regla mnemónica — *si algo TRUENA
avisa el que trona (push, segundos); si algo FALTA avisa el que vigila (job
cada 15 min)*:

- **Push (tiempo real)**: error nuevo del espejo kubera
  (`kubera_mirror._persistir_error`) y refresh de token ML fallido
  (`meli.refrescar_token`, cazador del `invalid_grant`).
- **Vigilante de ausencias** (scheduler, `ALERTAS_MIN=15`): (1) actas de
  `migration.reconciliation_runs` — después de las `ALERTAS_ACTAS_HORA_UTC=8`
  cada dominio debe tener acta HOY y en 'ok'; (2) silencio de ventas —
  `ALERTAS_SILENCIO_HORAS=4` sin filas nuevas en `pedidos_ml` dentro del
  horario hábil CDMX (9-21); (3) tokens rancios — `ml_tokens_dashboard` sin
  renovar en 12 h (el renovador externo corre ~cada 6 h).
- **Anti-spam**: candado de enfriamiento POR TIPO (espejo 30 min, actas 6 h…);
  el primer aviso sale al instante, los repetidos se cuentan y el siguiente
  real anexa "(+N repetidas silenciadas)". `resumen_estado()` para diagnóstico.
- **Encendido/apagado sin deploy**: todo el módulo es no-op sin
  `SLACK_WEBHOOK_URL` (variable en Railway — la URL es la llave del canal:
  JAMÁS en el repo; lección del client_secret del publicador). Sin URL, el
  scheduler ni registra el job.
- Probado en local contra datos reales: envío ok, candado ok (2 suprimidas
  contadas), vigilante completo sin explotar y sin falsas alarmas (venta 0.0 h,
  tokens 1.1 h). Fase 2 pendiente: 500s de `/publicar/confirmar`, Woo 403,
  deploys fallidos (nativo de Railway); Fase 3: resumen mañanero.
  Versión 0.17.7.

### v0.17.8 — Alertas Fase 2: 500 al publicar · racha de 403 de Woo · deploys

- **500 al publicar** (push): `/api/publicar/confirmar` envuelto — cualquier
  excepción NO controlada avisa a Slack con SKU, cuenta y causa, y se re-lanza
  igual. Es el fin del "ERROR DE CONEXIÓN" mudo: el equipo se entera aunque el
  usuario no reporte. Los `HTTPException` (validación) NO alertan — esos ya
  llegan legibles al modal (v0.15.1). Candado por SKU (`publicar_500:<sku>`,
  30 min).
- **Racha de 403 de WooCommerce** (contador ventana): nuevo
  `alertas.avisar_si_racha(tipo, texto, umbral, ventana_min)` — cuenta
  ocurrencias en ventana deslizante y solo alerta al cruzar el umbral. Aplicado
  al 403 del WAF de Hostinger (pendiente #1): 1 fallo = parpadeo (silencio);
  **5 en 10 min = bloqueo real** → 🟡 con el conteo. Probado: 4 callados, el
  5to alerta, el 6to lo suprime el candado.
- **Deploys fallidos** (config, 0 código): Railway trae webhooks de proyecto
  con Muxer nativo de Slack — pegar la MISMA URL del canal en Settings →
  Webhooks del proyecto `Hixen9Proyects` y avisa solo de deployment
  failed/crashed (+ alertas de volumen/CPU). El agente de Railway no puede
  crearlos por API: es un paso manual de dashboard (2 clics).
  Versión 0.17.8.

---

### v0.18.0 — FAN-OUT de stock DROP (en DRY-RUN) + Dashboard de operaciones

**El problema**: una venta no-FULL descuenta en Woo (30→29) pero **nadie avisa a
los otros canales** — verificado: `sync_woo.py` solo empuja Odoo→Woo y el sync de
15 min solo LEE. SANCORFASHION y Amazon seguían ofreciendo 30 → sobreventa.

**`services/fanout_stock.py`** replica el stock DROP a las publicaciones ACTIVAS
y no-FULL de todos los canales. Decisiones de diseño (cada una por un motivo):

- **Se encola el SKU, NUNCA un delta.** Al procesarlo se LEE el stock actual de
  Woo → idempotente por naturaleza (un mensaje repetido da el mismo resultado) y
  auto-sanable. Con "resta 1" un duplicado descuadra el inventario para siempre
  — y ML manda webhooks EN RÁFAGA (regla 6).
- **Solo publicaciones vivas.** Escribirle a una PAUSADA la **REACTIVA** (pasó
  con CAM-0030 el 24-jul, ML lo avisa explícitamente). Además una pausada no vende.
- **Solo no-FULL**: las piezas FULL/FBA son del marketplace.
- **Comparar antes de escribir**: ahorra rate-limit y **mata el eco** (al escribir
  en ML vuelve un webhook `items`; como el valor ya coincide, el ciclo muere).
- **Debounce 5 s por SKU**; seam fire-and-forget en `pedidos_ml.sincronizar` (si
  el fan-out falla, la venta NO se ve afectada).

**3 bugs que cazó el DRY-RUN antes de tocar producción** (justificación viva del modo):

1. **Vocabulario de estados por canal**: el filtro `situacion='active'` ignoraba
   las **1,616 publicaciones vivas de Amazon** (usa `PUBLISHED`) — el canal DROP
   más grande. Normalizado a `{active, published, publish}`.
2. **`item_id` NULL en Amazon**: su identificador es el **SKU** (la Listings API
   direcciona `/items/{seller}/{sku}`); filtrar por `item_id` dejaba fuera sus
   1,631 filas. SKUs con destino: 18 → **1,620**.
3. **319 publicaciones FULL con 0 piezas** se clasificaban como no-FULL por el
   heurístico de stock → ahora manda la bandera `es_full`.

**Dato operativo revelado**: hoy en ML **todo lo activo es FULL** (422 BEKURA +
373 SANCOR) y **todos los no-FULL están pausados** → el único canal que consume
DROP en vivo es **Amazon** (1,616 listings MFN).

**Dashboard** (`/dashboard`, primera pestaña, antes "PRONTO"): estado de los
flags, métricas, cola en vivo y **bitácora** con el motivo de cada decisión
(escribir / sin cambio / omitir), filtrable a solo errores. Poll 10 s.

**Flags** (Railway): `FANOUT_ENABLED` (false), `FANOUT_DRY_RUN` (**true**),
`FANOUT_CANALES` (CSV, encendido gradual), `FANOUT_RESERVA` (colchón).

> ⚠️ **TABLA TEMPORAL `fanout_log`** (MySQL local, se crea sola): bitácora que
> sobrevive a los deploys — sin ella cada reinicio de Railway borraría el
> historial del dry-run. **Local a propósito, NO entra en la migración.**
> **PENDIENTE: BORRARLA cuando el fan-out pase a producción** (acuerdo con
> Brandon, 24-jul) — decidir antes si su historial se conserva o se descarta.

Versión 0.18.0.

---

### v0.18.1 – v0.18.3 — INCIDENTE DE SOBREVENTA en Amazon: causa, corrección y auditoría (2026-07-27)

**Síntoma reportado por Brandon**: hubo sobreventa en Amazon "aunque lo hayamos
sincronizado". SKUs de ejemplo: TEC-0837-NEG, SIL-0019-NEG, TEC-0874-NEG (este
último con `_stock = -1` en Woo: la huella de haber vendido sin existencia).

**Causa raíz (NO era la sync Odoo→Woo del 17-jul).** De los 91 SKUs con
sobreventa activa, **90 tenían 0 también en Odoo** → Woo estaba correcto. El
desactualizado era **Amazon**: nada empujaba Woo → Amazon (el sync de 15 min
solo LEE y `sync_woo.py` solo va de Odoo a Woo). Es el hueco que cierra el
fan-out.

**⚠️ El diagnóstico evitó un desastre**: correr la sync masiva Odoo→Woo que se
pidió habría **BORRADO 16,646 piezas en 172 SKUs**, dejando 70 en cero (12,739
piezas) — producto nacido en Woo tras el corte que Odoo no conoce. Se verificó
ANTES de ejecutar.

**v0.18.1 — FIX CRÍTICO: el dry-run del fan-out MENTÍA sobre Amazon.**
`canal_inventario.stock_real` viene NULL en el 100% de las filas de Amazon (el
batch lo escribe así: *"FBM se lee en refresco individual"*), y el fan-out hacía
`int(stock_real or 0)` → reportaba **"Amazon tiene 0"** en las 1,614 filas. Falso:
había listings con 540, 150 y hasta 1,999 piezas. Correcciones:
- `_amazon_en_vivo()`: lee cantidad y estado REALES por SP-API antes de decidir.
- **DESCONOCIDO ≠ 0**: si no se sabe qué tiene el canal, se OMITE (no se escribe
  a ciegas).
- Estado real (**BUYABLE** vs **DISCOVERABLE**) en vez de `situacion`, que venía
  de nuestra bitácora `amazon_progress`: el **76% de lo que decíamos PUBLISHED
  está DORMIDO** y escribirle stock lo despertaría.
- Se compara contra `attributes.fulfillment_availability` (lo que fijamos, se
  actualiza al instante) y NO contra `fulfillmentAvailability` (vista servida por
  Amazon, va con retraso) — si no, se reescribiría en bucle.

**v0.18.2 — Sobreventa corregida**: `scripts/corregir_stock_amazon.py`
(idempotente, dry-run por defecto, solo toca BUYABLE, registra en `fanout_log`).
**91/91 SKUs corregidos, 5,693 piezas fantasma retiradas**, 0 errores. Los
mayores: JUGU-0171-MUL 1000→0, TEC-1028-NEG-NAR-M 540→0, TEC-0828-MET-DOR 240→0.

**v0.18.3 — Auditoría Woo vs Odoo (12,904 SKUs comparables)**:

| | SKUs | Piezas |
|---|---|---|
| Iguales | **12,667 (98.2%)** | — |
| Odoo > Woo | 65 | +2,158 |
| Odoo < Woo | 172 | −16,646 |
| Solo en Woo | 1,518 | — |
| Solo en Odoo | 96 | — |

**Segunda causa encontrada**: en **26 SKUs** la diferencia Woo−Odoo es
EXACTAMENTE el stock que vive en las bodegas FULL de ML (HERR-0034-AZL-127V
600/400/FULL=198; TEC-0492-MUL 307/207/100). **Al enviar mercancía a FULL, Odoo
SÍ descuenta la salida del almacén propio y Woo NO** → Woo ofrecía en los canales
DROP piezas que están físicamente en la bodega de Mercado Libre. Es la misma
sobreventa desde el otro lado. `scripts/corregir_stock_woo_full.py` alineó
Woo=Odoo solo en ese grupo verificado: **26/26, 1,526 piezas retiradas**.

**Aplicado con dale de Brandon**: los **65 SKUs donde Odoo tiene más**
(+2,158 pzas) — Odoo es la fuente de verdad para resurtidos y envíos a FULL.

**SIN TOCAR (requieren decisión/conteo físico):**
- **33 AMBIGUOS**: tienen FULL pero el gap NO cuadra. Dos casos: `gap < FULL`
  (Woo ya descontó parte) y `gap > FULL` (hay otra merma además del FULL, ej.
  DEC-0018-VER gap=200 con FULL=20). Aplicar Odoo a ciegas borraría stock real o
  dejaría piezas fantasma.
- **113 con divergencia sin FULL** (72 publicados + 41 draft): **los 113 SÍ
  existen en Odoo** — 58 con stock parcial y **50 declarados en CERO por Odoo
  teniendo 12,097 pzas en Woo**.

**QUÉ FALTA POR CERRAR (la raíz)**: cuando se envía mercancía a FULL nadie
descuenta en Woo. Mientras sea manual, la deriva vuelve. Opciones: (1) detectar
los ingresos a FULL por API de ML (`/stock/fulfillment/operations/search`), (2)
auditoría diaria Woo-vs-Odoo-vs-FULL con alerta, (3) proceso manual disciplinado.

---

### 📣 Hallazgo 2026-07-27 — Censo de publicaciones HUÉRFANAS en ML (para Brandon/Hixen9)

Censo por API (`/users/{id}/items/search?status=active`, ambas cuentas, solo
lectura) cruzado contra `ml_progress` — motivado por ventas no-FULL de ML que el
tablero no explicaba:

| Cuenta | Activas reales | En radar (ml_progress) | HUÉRFANAS | FULL | no-FULL |
|---|---|---|---|---|---|
| BEKURA | 471 | 408 | **63** | 62 | 1 |
| SANCORFASHION | 434 | 361 | **73** | 73 | 0 |

- **Huérfana** = publicación creada FUERA del publicador (manual/vieja):
  invisible para `canal_inventario`, el espejo channel, el fan-out y la página
  /inventario. La afirmación "todo lo activo es FULL" (v0.18.0) es cierta al
  99.3% — pero solo sobre lo rastreado.
- **Riesgo de sobreventa: BAJO** — 135/136 huérfanas son FULL (venden de bodega
  de ML). La única no-FULL activa es `ORG-0399-MET` (cross_docking, 120 pzas,
  0 ventas).
- **El costo real es VENTA PERDIDA, no sobreventa** — caso `TEC-0551-PLU`
  (BEKURA, MLM4685754218, `cross_docking`): 673 ventas históricas, ~93 en sus
  últimas 2 semanas, se vendió hasta el CERO y ML la pausó sola el 27-jul.
  Vendía 6-7 pzas/día y nadie la reabasteció porque ningún tablero la veía.
  (En SANCOR el mismo SKU ya es FULL con stock — MLM4690541010.)
- **Tercer tipo de logística detectado**: `cross_docking` (vende de almacén
  propio, ML recolecta). Para el fan-out cuenta como no-FULL (consume DROP);
  hoy el código solo distingue fulfillment/no-fulfillment — le aplica, pero
  conviene tenerlo en el vocabulario.
- **Subconteo del mundo FULL**: las 135 huérfanas FULL tienen piezas en bodega
  ML que NO suman en canal_inventario ni en /inventario.

**Recomendación (territorio del sync/fan-out — coordinar antes de mover):**
1. Que el descubrimiento del sync sea por **API del seller** (como este censo)
   y no solo por `ml_progress` — ninguna publicación manual vuelve a ser
   invisible.
2. **Adoptar** las 136 huérfanas (alta en `ml_progress` o fuente alterna) para
   que tablero, espejo y fan-out las vean.
3. `TEC-0551-PLU` BEKURA: decidir reabastecer/reactivar — vendía fuerte.

Script del censo reproducible: sesión de Eduardo 27-jul (solo lectura).
### v0.19.0 — Movimientos de FULL/FBA → Woo (el webhook que ML ya mandaba y tirábamos)

**El hueco**: al mandar mercancía a FULL, esas piezas SALEN del almacén propio.
Odoo lo registra (a mano) y **Woo no** → Woo seguía ofreciéndolas en los canales
DROP. Causó la sobreventa del 27-jul (26 SKUs, 1,526 pzas).

**EL HALLAZGO**: ML **ya nos avisa en segundos** y lo estábamos ignorando. En los
logs de producción:

```
[fbm_stock_operations] /stock/fulfillment/operations/3963089046712909973 → "sin acción"
[stock-locations]      /user-products/MLMU3866432728/stock                → "sin acción"
```

Resolviendo la operación se sabe **qué pasó, cuántas piezas y el total**:
`{"type": "TRANSFER_DELIVERY", "detail": {"available_quantity": 5}}`.

**Por qué nunca entró**: el filtro pedía `stock_locations` (guion BAJO) y un
recurso `/items/…`; ML manda `stock-locations` (guion MEDIO) con
`/user-products/…`. Doble desajuste — verificado en logs.

**`services/stock_full.py`** — tabla de decisión por tipo de operación:

| Tipo | Qué es | Efecto en Woo |
|---|---|---|
| `TRANSFER_DELIVERY` (+) | llegó mercancía a FULL | **RESTA** (salió de bodega) |
| `WITHDRAWAL_DELIVERY` (−) | retiro de FULL | **SUMA** (regresó) |
| `SALE_CONFIRMATION` (−) | venta desde FULL | no toca (sale del almacén de ML) |
| `SALE_CANCELATION` (+) | cancelación | no toca |
| `QUARANTINE_*` | cuarentena de ML | no toca |
| `ADJUSTMENT` (±) | ajuste de ML | no toca, pero **AVISA** |

**5 defensas contra falsos positivos** (el riesgo real de automatizar esto):
1. **Idempotencia por `operation_id`** — ML manda ráfagas (se vio el mismo evento
   3 veces en 2 s). Reutiliza `fanout_log`: **no se creó tabla nueva**.
2. Solo los tipos de la tabla mueven stock; el resto se registra.
3. **Verificación cruzada**: antes de restar, confirma contra
   `/inventories/{id}/stock/fulfillment` que la bodega de ML respalda el
   movimiento. Si no cuadra → `full_sospechoso`, no toca Woo.
4. **Tope de cordura**: nunca deja Woo en negativo.
5. Todo a la bitácora que ya pinta el Dashboard.

Tras ajustar Woo **encola el fan-out** del SKU: los canales DROP se enteran solos.

**Amazon FBA**: sin webhook (Notifications API da **403** — requiere rol extra +
cola SQS/EventBridge). Se detecta por **comparación de fotos** del inventario FBA
(`/fba/inventory/v1/summaries`, que sí responde 200) cada 15 min; si SUBIÓ, llegó
mercancía → se resta de Woo. Son pocos SKUs en FBA (7), así que es barato.

**Flag**: `FULL_WATCH_ENABLED` (default **false**) — encenderlo MUEVE INVENTARIO
REAL. Versión 0.19.0.

---

### v0.19.1 — INCIDENTE (30 min): `WITHDRAWAL_DELIVERY` inflaba el stock de Woo

**Qué pasó.** A los ~5 min de encender `FULL_WATCH_ENABLED` (27-jul 19:26) el
Dashboard mostró un patrón raro: `TEC-1804-BLN` subía de 1 en 1 cada ~20 s
(0 → 14 en 8 minutos). El mapeo de v0.19.0 trataba `WITHDRAWAL_DELIVERY` como
"la mercancía regresó a la bodega → SUMA a Woo". **Está mal.**

**Lo que realmente significa** (verificado contra la API):
```
WITHDRAWAL_RESERVATION  −15   el vendedor PIDE retirar 15 pzas
WITHDRAWAL_DELIVERY     −1 ×N ML las va ENTREGANDO de a una
```
La mercancía sale de la bodega de ML pero va **EN TRÁNSITO**: todavía no está
en el almacén. Y cuando llegue, el almacén la captura en Odoo (**el restock
SIEMPRE entra por Odoo**) → sumarla aquí la contaría **DOS VECES**.

**Contención (en este orden, a petición de Brandon):**
1. `FULL_WATCH_ENABLED=false` y **esperar a que el deploy aplicara**, confirmando
   que dejaron de llegar eventos antes de tocar datos.
2. Medir el daño con la bitácora: **19 pzas fantasma en 3 SKUs**
   (TEC-1804-BLN +14, TEC-0965-BLN +4, TEC-0324-MUL +1).
3. Revertir en Woo y verificar contra Odoo → los 3 quedaron **idénticos a Odoo**.

**Alcance real del daño: NINGUNO hacia afuera.** El fan-out (encendido y
escribiendo) **no alcanzó a propagar** el stock fantasma: esos SKUs no están
BUYABLE en Amazon, así que el candado los omitió. Cero eventos `escribir` en la
ventana del incidente. El error vivió 30 min y solo dentro de Woo.

**Corrección**: `WITHDRAWAL_*` y `TRANSFER_RESERVATION` pasan a **no tocar Woo**
(solo avisan). El único tipo que mueve stock es `TRANSFER_DELIVERY` (llegó
mercancía a FULL → resta, porque ésa sí salió del almacén).

**Lección para el rediseño**: los tipos de operación de ML se descubrieron por
muestreo, y el muestreo NO vio `TRANSFER_RESERVATION` ni `WITHDRAWAL_RESERVATION`
(aparecieron al encender). Antes de volver a encender: correr el vigilante en
modo **solo-registro** (sin escribir) hasta ver el catálogo COMPLETO de tipos con
tráfico real. Versión 0.19.1.

### v0.20.0 — ETL de categorías ML + monitoreo completo del camino al corte (Eduardo)

**Contexto.** El censo de espejos detectó el ÚLTIMO escritor vivo sin cobertura:
`categorias_ml` (la escriben `crear_producto.py`/`costos.py` al curar categoría).
Además /migracion solo monitoreaba 2 de los dominios con actas.

**Qué se construyó:**
1. **`scripts/etl_channel_categories.py`** (mismo esqueleto que el ETL v2 de
   core: dry-run default, cero truncate, identidad vía id_map, watchdog
   anti-caso-pedidos): puebla `channel.categories` (árbol ML: 2,674 categorías
   con nombre+ruta desde `categorias_ml`, SIN llamadas a la API) y
   `channel.product_category` (13,680 asignaciones sku→categoría). **La
   elección del PANEL manda** (regla 2): las metas `ml_categoria_id` de Woo se
   cargan al final con `source='panel'` y pisan al predictor. Backfill aplicado
   el 2026-07-27 con 0 descartes y 0 issues; re-corrida = 0 cambios
   (idempotente). Acta: dominio `categorias-etl`.
2. **Cron `etl-core-products` ahora encadena los dos ETL** (06:15 UTC):
   maestro primero, categorías después (la FK sku→core.products exige ese
   orden). `railway.etl-core.json` con `railwayConfigFile` propio
   (anti-herencia del uvicorn — la causa raíz del cuelgue de deltas-orders).
3. **/migracion "Camino al corte" ahora muestra los 5 dominios**: Maestro
   (ETL), Categorías (ETL), Costos, Channel y Pedidos. Para dominios ETL,
   resultado 'ok' = corrió bien (los cambios van en conteos) — sin esto, un
   día con sincronización legítima pintaba punto rojo.

**Pendiente que queda de este frente:** el SEAM en vivo
`crear_producto/costos → channel.product_category` (agregarlo al censo del
espejo kubera como los demás; mientras, el cron de las 06:15 cierra el hueco
cada 24 h). Versión 0.20.0.

### v0.21.0 — DECISIÓN P4 aplicada: el precio sugerido es POR CANAL (Eduardo)

`costing.costos_finales` pasó de PK `(sku)` a **PK `(sku, canal)`** — migración
`supabase/migrations/0003_p4_precio_por_canal.sql`, ADR completo en
`docs/arquitectura_bd/DECISION_P4_precio_por_canal.md`. Las 4,353 filas
existentes quedaron `canal='mercado_libre'` (la fórmula actual es ML-céntrica);
`costos_validados` NO cambia (lo físico no depende del canal).

Código adaptado: `costing_mirror.espejar_finales` upsertea por `(sku, canal)`
con canal fijo `mercado_libre` (cuando el motor calcule otros canales, el
llamador lo pasará), y `comparar_costos.py` compara MySQL contra la fila
`canal='mercado_libre'` — la racha de deltas sigue intacta. Sandbox: paquete
`supabase/migrations/` + `schema_manifest.json` regenerado incluyen el cambio.
Se hizo AHORA porque nadie lee aún de costing: el mismo cambio después del
switch de lecturas (F5) habría sido una migración con consumidores encima.
Versión 0.21.0.

### v0.22.0 — Espejo de nacimientos tolerante al desfase de SKU (Eduardo)

**Caso ROBB-0004 (2026-07-27):** el seam Crear → `core.products` falló con
`UniqueViolation products_wc_id_key (wc_id=88490)`. La cola de Crear traía el
SKU base `ROBB-0004`, pero el producto vive en Woo como `ROBB-0004-MET` — y el
acta en `core.products` (cargada por el ETL) ya tenía ese `wc_id` bajo el SKU
con sufijo. El `ON CONFLICT (sku)` no empató y el INSERT chocó con la única de
`wc_id`. Dos fixes:

1. `crear_producto.py`: el acta de nacimiento viaja con el **`_sku` REAL de
   Woo** (lo devuelve el PUT del paso 6/6), no con el SKU de la cola.
2. `kubera_mirror._up_core_product`: si el `wc_id` ya tiene acta, se
   **actualiza esa fila** (name/status, sin pisar su sku canónico) y solo si
   no existe se inserta con `ON CONFLICT (sku)`. Cubre reciclados, renombrados
   con `editar_sku` y base-vs-sufijo. Con esto, `/errores/reprocesar` sí
   repara el error pendiente (antes re-chocaba con la misma restricción).

Versión 0.22.0.

### v0.22.1 — Nombres honestos: la analítica se separa de la operativa (ANALYTICS_SUPABASE_*) (Eduardo)

Preparación del candado de producción (F5). `supabase_rest.py` (presencia ML /
dataset: `products_snapshot`, `daily_stock`, `ml_accounts` — único consumidor
de analítica) ahora lee `ANALYTICS_SUPABASE_URL` + `ANALYTICS_SUPABASE_SERVICE_ROLE_KEY`
con **fallback** a `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` — si las nuevas
no existen, comportamiento idéntico (cero riesgo en este deploy).

El plan que habilita: en producción se duplican los valores actuales de
analítica hacia `ANALYTICS_*`, y entonces la familia `SUPABASE_*` puede
re-apuntarse a la BD kubera (`SUPABASE_URL=https://tukwcvsi….supabase.co` +
`SUPABASE_PROD_REF=tukwcvsi…`) → `validar_ambiente()` queda activo también en
producción, con los nombres diciendo la verdad: `SUPABASE_*` = operativa,
`ANALYTICS_*` = dailytrack. Ese switch de variables lleva dale de Brandon
(regla 3) y se aplica con un solo accept-deploy. Staging/local no necesitan
`ANALYTICS_*` (no leen analítica real). Versión 0.22.1.

### v0.23.0 — F5 arranca: primer flag de lectura (COSTOS) con equivalencia probada (Eduardo)

El primer dominio que aprende a LEER de la BD kubera. Nuevo
`services/costing_read.py`: gemelas Postgres de las 3 lecturas de costos del
panel (`/api/crear/costos`, `/costos/{sku}`, `/costos/_contenedores`) —
mismas formas de respuesta, con las trampas de traducción resueltas
(P4: join a `costos_finales` fija `canal='mercado_libre'`; `p.nombre` ≡
`core.products.name`; LIKE→ILIKE). Flag **`SUPABASE_READ_COSTING`**
(default false = comportamiento idéntico): encendido, los 3 GET leen de
kubera con **fallback automático a MySQL ante cualquier error** — apagar el
flag o fallar la lectura = MySQL al instante. Los logs de costos siguen en
MySQL en ambas rutas (su destino ops.process_log es fase aparte).

**Equivalencia demostrada** con `scripts/comparar_lecturas_costing.py`
(solo lectura, ambas fuentes, muestra reproducible seed=42): contenedores
102=102 idénticos, conteo global 15,411=15,411, detalle 150 SKUs × 15
campos = 2,250 comparaciones con 0 diferencias, listado página 1 con
secuencia y nombres idénticos → veredicto EQUIVALENTE. El arnés queda como
prueba de regresión: correrlo antes de encender el flag en cada ambiente.
Versión 0.23.0.

### v0.23.1 — Suite de CAOS del sandbox: 14 pruebas destructivas y de seguridad (Eduardo)

A petición de Eduardo, antes de encender flags: probar lo que el camino feliz
no ejerce. `scripts/suite_caos_sandbox.py` (candado triple: aborta si el
destino no es el sandbox) + `sembrar_sandbox.py` (muestra real desechable
desde MySQL, solo lectura) + `aplicar_migraciones.py --recrear`
(DROP SCHEMA CASCADE + re-aplicar).

**Resultado: 14/14 PASA.** Por familia:
- **Candado (3/3)**: staging→prod y prod→otro-proyecto BLOQUEAN el arranque
  (RuntimeError); la config coherente arranca. El candado no es decorativo.
- **Inyección SQL (2/2)**: 4 payloads (`'; drop table…`, `' OR '1'='1`, …) por
  `search` no ejecutan nada — las tablas quedan intactas; un `orden` malicioso
  cae al default (el dict de ORDEN nunca interpola entrada del usuario).
- **Integridad (4/4)**: la FK rechaza costo de SKU huérfano; el CHECK rechaza
  SKU con espacio (la regla de identidad vive en la BD, no solo en el ETL);
  la PK (sku,canal) acepta 2 canales del mismo SKU y bloquea el duplicado —
  **P4 verificada en el motor**.
- **RLS/llaves (3/3)**: service_role lee, anon 401, sin apikey 401.
- **Fallback (1/1)**: una DSN inválida lanza excepción → el router cae a MySQL
  (es el camino que protege al panel con el flag encendido).
- **Resiliencia (1/1)**: `statement_timeout` corta una consulta de 5 s a los
  0.8 s — nada se cuelga indefinidamente.

**Prueba mayor**: se DESTRUYÓ el sandbox entero (drop cascade de los 6
esquemas) y se recreó desde `supabase/migrations/` → **PARIDAD OK 31/31**.
El sandbox es desechable de verdad y el paquete de migraciones está completo.
**Producción verificada intacta** tras todo el ejercicio: 22,154 productos /
15,411 validados / 4,353 finales / 5,714 listings / 5,591 orders / 13,680
categorías, 0 filas cobaya, 0 filas de siembra. Versión 0.23.1.

### v0.26.0 — Absorción de dailytrack F1: retención de webhooks, channel.order_items y archivo histórico (Eduardo)

Arranca la absorción de las series de dailytrackMeli (`xaxbkijc`, muertas
desde el 15-jul por disco lleno/timeouts de su ingest-cron; alcance acordado:
`daily_sales` + `daily_stock`, `daily_visits` FUERA por ahora). Decisiones de
Eduardo 2026-07-28: historia comprimida (foto diaria → registro de cambios),
cancelados se guardan y la vista los excluye, corte del día en
`America/Mexico_City`.

- **Retención de `ops.webhook_events` = 3 días** (migración
  `0004_retencion_webhook_events.sql`, APLICADA en kubera). La tabla era el
  79% de la BD (154 de 194 MB, +17k filas/día). Purga inicial: 129,675 filas
  respaldadas en `backups/ops_webhook_events_hasta_id_130513_2026-07-28.csv.gz`
  + `VACUUM FULL` → tabla 33 MB, base 74 MB. Queda `ops.purgar_webhook_events()`
  (borra por lotes, bitácora en `ops.process_log`) programada con **pg_cron
  diario 08:20 UTC** (fuera de la ventana de ETLs). Era el bloqueante para
  meter las series sin repetir el 53100 que mató a dailytrack.
- **`channel.order_items` + vista `channel.sales_daily`** (migración
  `0005_order_items_sales_daily.sql`, APLICADA en kubera y sandbox —
  PARIDAD OK). Las líneas de cada venta con CANTIDADES e `item_id` (lo que el
  array `skus` de `channel.orders` no guarda); la vista agrega por
  día×cuenta×item excluyendo cancelados = equivalente 1:1 de `daily_sales`.
- **Seam en `pedidos_ml.sincronizar`** (junto al espejo de orders, v0.16.0):
  espeja las líneas vía tabla-origen virtual **`pedidos_ml_items`** — nace
  APAGADO; encender = sumarla a `KUBERA_MIRROR_TABLAS` (variable Railway, sin
  deploy, dale de Brandon). El upsert (`_up_channel_order_items`) asegura el
  padre con DO NOTHING (sin carrera de FK entre workers), congela importes y
  solo admite comisión 0→valor (regla v0.17.0). Sirve a ML, Amazon y M2E: los
  tres embudan en `sincronizar` con items ya normalizados — cero llamadas
  nuevas a las APIs.
- **Esquema `analytics` para el archivo congelado** (migración
  `0006_analytics_dailytrack_hist.sql`, APLICADA): `sales_daily_hist`
  (espejo de daily_sales, dic-2025→15-jul) y `stock_hist` (daily_stock
  COMPRIMIDA run-length: medido con 200 series completas, el 91.1% de las
  365,542 filas era idéntica al día previo → se archiva solo el ~9% con
  vigencia `[valid_from, valid_to)`; vista `stock_hist_dia` reconstruye
  cualquier día). Por qué es obligatorio: en el traslape 1–15 jul dailytrack
  registró $3.19M en ventas y `channel.orders` solo $0.24M (7%) — esa
  historia no existe en ningún otro lado y no se puede reconstruir.
- **ETL one-shot EJECUTADO** (`scripts/etl_dailytrack_hist.py`, dry-run
  default, reintentos ante los 503 del origen, compresión en vuelo):
  `sales_daily_hist` **17,984/17,984** filas con totales verificados al
  centavo (60,325 uds / $36,866,979.64 / fee $5,699,855.58);
  `stock_hist` **365,542 → 32,848 filas (9.0%)**, 11,298 items, 10,952
  vigentes al corte. Reconstrucción verificada contra el origen: 19-jun
  10,200/10,200 y 15-jul 10,952/10,952 EXACTOS + muestreo de 30 items campo
  por campo sin deltas (20-may +50 = días con el cron origen caído por
  cuenta: el archivo interpola presencia a propósito — cierre por ausencia
  solo cuando la cuenta sí reportó ese día).
- **Backfill de order_items EJECUTADO** (`scripts/backfill_order_items.py`,
  desde Woo): 5,819 líneas / 5,826 orders (los 7 sin línea son ventas que
  entraron DURANTE la corrida — las cubre el seam al encender), 4,195
  item_id enriquecidos vía channel.listings, 6,940 piezas (el dato que el
  array `skus` no tenía), consistencia línea-vs-encabezado 5,111/5,154
  (<$1; el resto es envío incluido en el total del pedido).
- **Acta de paridad registrada** (`migration.reconciliation_runs` dominio
  `analytics-hist`, id 59): las dos fuentes son **COMPLEMENTARIAS, no
  duplicadas** — la serie murió el 15-jul y el webhook nació el 17-jul, no
  existe ventana con ambas completas (traslape 13-may→15-jul: hist 37,068
  uds / $17.3M vs nuevo 491 uds / $0.20M). Se probó corrimiento ±1 día y
  UTC vs MX: los faltantes son días parciales nuestros, no fechas corridas.
  **Empalme canónico: hist ≤ 15-jul, sales_daily ≥ 16-jul.**
- Pendientes de esta fase: dale de Brandon para encender `pedidos_ml_items`,
  F2 (cierre diario de stock), regenerar `schema_manifest.json` con las
  tablas nuevas, aviso a José (su ingest-cron redespliega cada 2 días aunque
  ya no escribe), y al final dump completo (incluye daily_visits) + baja de
  `xaxbkijc`. Versión 0.26.0.

---

### 🔍 AUDITORÍA del vigilante FULL/FBA (2026-07-27) — VEREDICTO: **NO ACTIVAR**

Auditoría adversarial de `services/stock_full.py` antes de encenderlo: 4 revisores
en paralelo (correctitud, cobertura de tipos, simulación con datos reales,
robustez) + verificación que intentó REFUTAR cada hallazgo. **Los 4 votaron
`no_activar`; 7 de 8 hallazgos sobrevivieron a la refutación.** Todo solo-lectura.

**El modo SOLO-REGISTRO evitó un incidente peor que el de la mañana.**

#### 🔴 CRÍTICO 1 — `TRANSFER_DELIVERY` NO es "llegó mercancía a FULL"

Es un **barajeo interno de ML**: `TRANSFER_RESERVATION` mueve piezas de
`available` al bucket `transfer`, y `TRANSFER_DELIVERY` las regresa. **El `total`
de la bodega no cambia.**

> Evidencia: 102 operaciones `TRANSFER_DELIVERY` en 60 inventarios de ambas
> cuentas — en NINGUNA cambió `result.total`. Par observado:
> `TRANSFER_RESERVATION {available −N, transfer +N}` ↔
> `TRANSFER_DELIVERY {available +N, transfer −N}`, total constante.

Como era **el único tipo mapeado a `resta`** y ninguno suma, activarlo habría
desinflado Woo de forma monótona: **−329 pzas solo en la muestra**, y el fan-out
habría propagado esos ceros a los 1,616 listings de Amazon. Mismo error de
razonamiento que `WITHDRAWAL_DELIVERY`, invertido de signo.

#### 🔴 CRÍTICO 2 — el ingreso real es `INBOUND_RECEPTION` y NO está mapeado

Verificado en producción: `type=INBOUND_RECEPTION` con `inbound_id` (una
recepción de **129 pzas** en SANCORFASHION; otra de **17 pzas** de CUNA-0018 el
18-jul en ráfaga de 9 avisos/11 s). Hoy cae en `full_ignorado` → **el hueco de
las 1,526 pzas que motivó el módulo seguiría abierto**. Es decir: restaría por lo
que no debe y no restaría por lo que sí.

#### 🟠 Las tres defensas no defendían

1. **Verificación cruzada = sello de goma**: compara un NIVEL de stock (40-120
   pzas) contra un DELTA (1-6). Simulada sobre 55 operaciones reales: **0
   bloqueadas**. No habría atajado ninguno de los dos bugs. Además tira el
   `result` que la propia operación ya trae y hace un GET duplicado que devuelve
   el total ACTUAL, no el del momento.
2. **Idempotencia sella lo NO aplicado**: `_ya_procesada` solo mira si existe
   fila con ese `operation_id`, y `_registrar` escribe también en
   `full_sospechoso` / `full_sin_sku` / errores de Woo. Con un 502 del WAF de
   Hostinger (pendiente #1) el movimiento **se pierde para siempre**.
3. **`revisar_fba` revienta en cada ejecución** (choque de event loops:
   `asyncio.run` dentro del `AsyncIOScheduler`) — **nunca ha corrido**. Y aunque
   corriera, no guarda su propia foto: descontaría el mismo ingreso en cada
   vuelta.

*(Descartado por el verificador: la race condition en `_ajustar_woo` — la ráfaga
citada como evidencia no existe en los datos.)*

#### 📐 REGLA que deja la auditoría

**Un tipo de operación solo mueve stock si se DEMUESTRA que cambia el `total` de
la bodega** — nunca por lo que sugiera su nombre. Los dos bugs del día nacieron
de interpretar semántica sin medir el efecto.

Tabla correcta a implementar (pendiente):

| Tipo | Efecto |
|---|---|
| `INBOUND_RECEPTION` | **RESTA** (ingreso real a FULL) |
| `TRANSFER_*` | no toca (barajeo interno) |
| `WITHDRAWAL_*` | no toca (tránsito; llega por Odoo) |
| `SALE_*` / `QUARANTINE_*` / `ADJUSTMENT` | no toca |

#### Estado

`FULL_WATCH_ENABLED=false` (**apagado**, se queda así). El código de v0.19.0-0.19.2
sigue desplegado pero INERTE. **Los hallazgos NO están corregidos**: reescribir
`EFECTO_EN_WOO`, la verificación cruzada (usar el `result` de la operación), la
idempotencia (marcar solo lo aplicado) y `revisar_fba` (event loop + foto propia).

El **fan-out DROP → canales NO depende de este módulo** y sigue vivo y sano
(`FANOUT_ENABLED=true`, `DRY_RUN=false`): es el que resolvió la sobreventa.

### 2026-07-28 — Incidente actas: backend pirata en etl-core-products + total 0 de Amazon (Eduardo)

**Actas de Maestro/Categorías no generadas (28-jul).** Causa: `etl-core-products`
NO tenía seteado el `railwayConfigFile` (el candado anti-uvicorn que deltas-orders
sí lleva desde el 24-jul). El push de v0.23.1 (02:49 UTC) auto-desplegó el
servicio, `backend/railway.json` pisó su config y arrancó un SEGUNDO backend
(scheduler completo, sin tokens — todos sus jobs fallaban en seco) que bloqueó
el cron de las 06:15. Fix: `railwayConfigFile=backend/railway.etl-core.json`
seteado en el servicio vía API. LECCIÓN OPERATIVA: ese campo solo se re-resuelve
al crear un deployment nuevo DESDE EL REPO (un `redeploy` reutiliza la config
vieja) — este mismo commit existe también para disparar ese deployment fresco.

**Acta de Pedidos con_deltas (28-jul).** Dos hallazgos encadenados: (1) un pedido
solo_en_mysql — reparado con el backfill idempotente completo `pedidos_ml →
channel.orders` (5,729 en 12 tandas, 0 fallos); (2) el pedido Amazon
`701-5603407-3803465` con total 0 en MySQL vs 195.98 en el espejo: nació de un
sondeo en estado Pending (OrderTotal aún no existe), la regla de inmutabilidad
lo congeló en 0, y un sondeo posterior sí llevó el total real al espejo
(analogía exacta de la regla 6 de comparar_orders, pero en `total`). Verificado
contra SP-API (`OrderTotal=195.98 MXN`) y rellenado 0→195.98 en `pedidos_ml`
(precedente v0.17.0: comisión 0→valor). El pedido Woo #108248 sigue en 0 y el
`ON DUPLICATE` de pedidos_amazon no rellena total 0→valor — AMBOS pendientes
(ver Pendientes).

---

### v0.23.2 — El total $0 de los pedidos de Amazon (hallazgo de Eduardo + Claude, en paralelo)

**Qué pasaba.** Amazon NO publica los importes mientras la orden está **Pending**:
`OrderTotal` e `ItemPrice` llegan vacíos. `pedidos_amazon._normalizar` hace
`float(... or 0)` → la venta nacía congelada en **$0.00**… y ahí se quedaba,
porque el `ON DUPLICATE` de `pedidos_ml` actualizaba estado y comisión pero
**no el total**. Mismo patrón exacto que la comisión en 0 (v0.17.0).

Detectado el 28-jul por Eduardo (Slack) y por esta sesión de forma independiente,
al comparar el dashboard de Amazon ($1,652.10 / 7 uds) contra el tab Ventas.

**Alcance real: 14 pedidos**, no 1. Y en DOS capas — Eduardo señaló la segunda:
`pedidos_ml` alimenta las métricas, pero **el pedido de WooCommerce es el
registro histórico congelado** y también decía $0. Quien auditara Woo veía una
venta de $0 que fueron $195.98.

**Las 3 piezas aplicadas (con dale de Brandon):**
1. **Fix de fondo** — `total=IF(total=0, VALUES(total), total)` en el
   `ON DUPLICATE` (en `pedidos_ml.py`, no en `pedidos_amazon.py`: éste
   normaliza y delega). Un total >0 sigue siendo inmutable.
2. **Backfill `pedidos_ml`**: 13 revisados → **5 recuperados** ($391.96,
   $195.98 ×4). Los otros 8 siguen en $0 legítimamente: 6 **cancelados** y
   1 **Pending** (Amazon nunca publica su importe) + 1 MCF `S01-…`.
3. **Backfill de los pedidos de Woo**: **6 órdenes** corregidas vía REST
   (nunca tocando `wp_*`, regla de la casa): se reparte el total entre las
   líneas y se recalcula `_ml_neto`.

**Verificación de una venta real** (28-jul): 2 órdenes, 7 unidades — coincide
exacto con el dashboard. La de 07:53 desglosa `ItemPrice 336.21 + ItemTax 53.80
= OrderTotal 390.01`. El dashboard reporta ~$476 para esa orden: la diferencia
(~$86) **no aparece en la API** (ni `ShippingPrice` ni `PromotionDiscount`
traen valor) — hipótesis no confirmada: envío cobrado al comprador.

**Pendiente conocido que esto NO resuelve**: la comisión de Amazon sigue en 0
(falta Finances API).

---

### v0.31.0 — Alertas: el candado anti-spam sobrevive a los deploys + aviso por CAMBIO de estado

**Síntoma** (reportado por Eduardo con captura del canal): el mismo aviso
*"Acta de Pedidos salió `con_deltas`"* saliendo a las **10:06, 10:14 y 10:30**,
pese al candado de enfriamiento de 6 h para el tipo `acta`.

**Causa raíz — el candado vivía en la RAM del proceso.** Los deploys de
producción de ese día fueron a las **16:02, 16:11 y 16:27 UTC** (= 10:02, 10:11
y 10:27 CDMX): cada deploy reinicia el contenedor, `_ultimo_envio` nace vacío y
el vigilante — que arranca a los 150 s — vuelve a avisar de la MISMA condición.
Las alertas salieron ~3-4 min después de cada arranque, una por una. Que el
candado sí funcionaba con el proceso vivo lo prueba la propia captura: 2:12 →
8:27 con *"+24 repetidas silenciadas"* = las 6 h exactas.

**Segundo defecto, de diseño**: un acta con deltas **sigue con deltas todo el
día**. Aun sin reiniciar, `avisar()` la repetía cada ventana, y la recuperación
(volver a `ok`) no se anunciaba nunca — había que ir a mirar /migracion.

#### Las dos correcciones

1. **Candado PERSISTIDO** en la tabla nueva `alertas_estado` (MySQL kubera_ml;
   temporal y regenerable — borrarla solo deja salir una vez más el primer aviso
   de cada tipo). La BD manda sobre la RAM, así que un deploy ya no reabre la
   ventana. Todo es best-effort: si MySQL no está, degrada al candado en memoria
   y **sigue avisando** (probado).
2. **`alertas.avisar_estado(tipo, estado, …)`** — alertas por CAMBIO de estado
   para condiciones que duran horas: avisa al entrar en falla, avisa si la falla
   CAMBIA (`ausente` → `con_deltas`), **avisa la recuperación con ✅**, y
   mientras nada cambie se calla (recordatorio a lo mucho 1×/día). El vigilante
   de actas es su primer usuario; el resto de las alertas conserva `avisar()`,
   que ahora también es a prueba de deploys.

`resumen_estado()` expone lo persistido (estado vigente, minutos desde el último
aviso y suprimidas por tipo) para diagnóstico.

**Probado** con un doble de MySQL que emula las 5 sentencias reales, simulando
deploys (borrar la RAM dejando la BD intacta): **12/12 pasa** — 3 deploys con la
misma falla → **1 aviso** (antes 3); 5 llamadas seguidas → 1 aviso y 4 suprimidas
contadas; vencida la ventana el re-aviso anexa el conteo; el acta con deltas
calla 8 pasadas del vigilante y 3 deploys; `con_deltas`→`ausente` sí avisa;
la recuperación sale con ✅; un tipo que nunca falló no inventa recuperación;
y con MySQL caído sigue avisando 1 vez y frenando la repetida.

Versión 0.31.0.

---

### v0.28.1 — El total $0 de Amazon, TERCERA capa: el espejo no podía sanar (Eduardo)

**Síntoma.** El acta de Pedidos salía `con_deltas` a diario desde el 28-jul
(alerta de Slack, +24 repeticiones silenciadas). Acta #66 (29-jul 07:19 UTC):
`divergentes_confirmados=6`, `solo_en_mysql=0`, `solo_en_supabase=0` — no
faltaba ningún pedido, solo divergía un campo.

**Diagnóstico.** Los divergentes (7 al momento del fix) son TODOS de Amazon,
con el total real en `pedidos_ml` y **$0.00 en `channel.orders`**. Es el mismo
hallazgo de v0.23.2 (Amazon no publica `OrderTotal` mientras la orden está
*Pending* → la venta nace en $0 y la inmutabilidad la congela), pero en una
**tercera capa que quedó fuera de aquel fix**: se reparó MySQL
(`total=IF(total=0, VALUES(total), total)`) y los pedidos de Woo por REST,
mientras que `_up_channel_orders` del espejo **no tenía `total` en su
`DO UPDATE`** — sí la regla equivalente para `comision` (v0.17.0), no para
`total`. Consecuencia: MySQL sanaba y el espejo **no podía sanar nunca**, así
que el acta iba a salir `con_deltas` indefinidamente.

**Fix.** Misma cláusula que ya existía para comisión, ahora también en total:
`total = case when coalesce(channel.orders.total,0) = 0 then excluded.total
else channel.orders.total end` — un total >0 sigue siendo inmutable; solo se
permite el paso 0 → valor real, una vez. Con eso, el backfill idempotente
`pedidos_ml → channel.orders` sana las filas viejas y las nuevas se
autocorrigen en el siguiente sondeo.

**Nota de método** (para quien diagnostique esto en local): `comparar_orders.py`
resuelve su destino con `cargar_env("env.staging")` — en Railway esa variable
ES producción, pero **en local apunta al sandbox**. Correrlo tal cual desde una
laptop compara MySQL prod contra el clon y arroja cientos de deltas falsos.
Y al leer `channel.orders.skus` con psycopg2 hay que castear (`skus::text[]`):
`citext[]` llega como texto crudo y toda comparación de SKUs da falso positivo.

---

### v0.28.0 — Fan-out DROP de Mercado Libre: la pausa deja de ser un muro

Faltaba el canal más grande. El fan-out ya replicaba stock a Amazon, pero de ML
solo podía tocar **32 publicaciones**: las 2,278 DROP restantes están PAUSADAS, y
escribirles stock las REACTIVA (ML lo avisa; pasó con CAM-0030 el 24-jul). Como
Brandon pidió que todas se queden pausadas, el fan-out las omitía por diseño.

#### El hallazgo

Mandar `status` **junto con** `available_quantity` en la MISMA petición: ML
respeta el estado explícito y solo cambia la cantidad.

```
PUT /items/MLM…  {"available_quantity": 75, "status": "paused"}
→ 200 · sub_status sigue en paused_by_seller · stock actualizado
```

Probado en las DOS cuentas antes de escribir una línea de código. También se
probó `PUT /user-products/{id}/stock` (el endpoint de inventario): **404**, es
solo de lectura.

#### Cómo quedó

- El estado se **LEE antes de escribir**, nunca se asume: mandar `paused` a
  ciegas PAUSARÍA una publicación activa — el desastre opuesto.
- **Verificación posterior**: si a pesar del blindaje despertara, se re-pausa en
  el acto y queda anotado en la bitácora.
- Solo entran `active` y `paused`. `under_review`, `closed` e `inactive` siguen
  fuera a propósito: ahí manda ML, no nosotros.
- FULL sigue intocable (esa bodega es del marketplace).

#### Lo que destapó

De **2,310** publicaciones ML DROP sincronizables, **706 están desalineadas**
contra Woo:

| | pubs | piezas |
|---|---|---|
| ML ofrece de MÁS (sobreventa el día que se reactiven) | 538 | 51,263 |
| ML ofrece de MENOS (venta perdida) | 168 | 24,969 |

Casos gruesos: `TEC-0991-BLN-AIR` con **10,000 en ML y 5,000 en Woo** en ambas
cuentas; `ORG-0781-AZL-ROS-VER` 7,200 vs 2,300. Al estar pausadas hoy no venden,
así que el riesgo no es de hoy: es del día que se reactiven con el stock rancio.

#### Alineación masiva EJECUTADA (dale de Brandon, 28-jul)

Las **705** se alinearon contra Woo — que ya traía el stock real de Odoo — en las
DOS cuentas por igual. Resultado: **705 escritas, 0 reactivadas, 0 desalineadas**
al recontar. Muestra aleatoria de 10 verificada en vivo contra ML: pausa intacta
y cantidad exacta en todas.

Se corrió por tandas (25 de prueba → 508 → 172), ordenadas por exceso descendente
para atacar primero el riesgo de sobreventa. **Dos fallas transitorias** en la
tanda larga, ambas por la duración (31 min):

- **147 "sin token de {cuenta}"** — el token de ML expiró a media corrida.
  `_access_token` no se auto-sana (solo `meli.obtener_orden` lo hace, en un 401:
  regla 8). Se reprocesaron con el token fresco: 172 escritas, 0 errores.
- **La conexión MySQL se cayó** y esos 147 errores no quedaron en `fanout_log`
  (la escritura a ML sí se intentó; lo que falló fue el registro). El log en
  disco sí los tiene.

Ninguna afectó lo escrito, pero las dos van a volver a morder en cualquier
corrida larga: falta reintento con refresh de token y reconexión.

---

### v0.27.0 — Vigilante de inventario: Odoo →delta→ Woo →cambio→ canales

Cierra el círculo del inventario. La auditoría de la sincronización Odoo→Woo
(v0.25.0) dejó ver que "todo sincronizado" era imposible por dos huecos:

**1. El fan-out solo se disparaba con VENTAS.** Verificado: sus únicos
disparadores automáticos eran `pedidos_ml` y `stock_full` — y éste último está
apagado. Cuando llegaron 198 piezas de `CUNA-0018-MET` a Odoo y se empujaron a
Woo, **ningún canal se enteró**. Es el mismo hueco del *ratchet* que dejó la
auditoría anterior: el fan-out podía llevar una publicación a 0 pero nunca
revivirla al volver mercancía.

**2. El empuje Odoo→Woo mandaba el VALOR ABSOLUTO.** Correcto cuando Odoo era el
maestro; desde el 17-jul **Woo es la fuente de verdad de las VENTAS** y Odoo ya
no registra esas bajas. Poner `Woo = Odoo` le devuelve el stock a lo que Woo bajó
*porque vendió*: **resucita mercancía vendida**. Por eso `odoo_watch.auto_push`
llevaba meses apagado — el candado era correcto, lo que estaba mal era el empuje.

#### La idea: Odoo es un ORIGEN DE CAMBIOS, no el valor verdadero

Woo conserva su absoluto (que trae las ventas) y Odoo aporta solo su **delta**
(llegaron 50, salieron 17 a FULL). Si Odoo no cambia, Woo no se toca — el
escenario de resurrección **no puede ocurrir por construcción**.

```
Odoo ──(delta)──► Woo ──(cualquier cambio)──► Amazon / ML   [vía fan-out]
```

El segundo tramo se dispara con **cualquier** cambio de stock en Woo contra la
foto anterior: venta, delta de Odoo, ingreso a FULL, la compensación FULL/FBA o
una edición a mano en wp-admin. Al ser foto-contra-foto **no se puede evadir**,
que es la diferencia con enganchar cada escritor uno por uno.

Probado en vivo con la simulación de los dos caminos: `HERR-0303-MET` con Odoo
460→500 propone **Woo 500 → 540**, no "Woo = 500". El empuje absoluto se habría
comido las 40 piezas que llegaron.

#### Candados (nace apagado; encenderlo MUEVE INVENTARIO REAL)

- `STOCK_WATCH_ENABLED=false` — no corre.
- `STOCK_WATCH_SOLO_REGISTRO=true` — anota lo que haría, sin escribir.
- `STOCK_WATCH_TOPE=300` — **cortacircuitos**: si una pasada ve más cambios que
  el tope, NO aplica nada y avisa. Una edición masiva en Odoo (o un Odoo que
  responde vacío) no puede vaciar todos los canales de un golpe. Se salta con
  `?forzar=true` cuando el volumen ya se revisó y es real.
- **Odoo mudo ≠ todo en cero**: si `listar_catalogo` viene vacío se aborta la
  pasada. (La lección de "DESCONOCIDO ≠ 0" del dry-run de Amazon.)
- La **primera pasada solo levanta la foto base** y nunca escribe.
- En solo-registro la foto **no absorbe lo pendiente**: si absorbiera, un delta
  observado desaparecería y al pasar a modo vivo esas piezas no se aplicarían
  nunca. Lo detectó la prueba de las dos pasadas.

`odoo_watch` conserva su campana intacta, pero su `auto_push` (absoluto) ahora se
niega a correr si el vigilante nuevo está encendido: se pisarían entre sí.

#### Cobertura

La foto de `odoo_watch` vive en `productos.stock_odoo`, que solo cubre **5,381**
SKUs (tabla legada del robot, congelada). Ésta cubre el catálogo completo:
**13,000 de Odoo / 14,422 de Woo — 14,518 SKUs** en la foto, 33 s por pasada.

**Tabla nueva**: `stock_watch_foto` (sku, stock_woo, stock_odoo, actualizado).
Es **temporal** y regenerable — borrarla hace que la siguiente pasada levante la
foto base sola, sin escribir. Queda anotada junto con `fanout_log` en
[docs/TABLAS_TEMPORALES.md](docs/TABLAS_TEMPORALES.md), el registro para el
borrado al cerrar la migración.

#### Panel

`GET /api/fanout/inventario/estado` · `POST /api/fanout/inventario/revisar` ·
`GET /api/fanout/inventario/pendientes`. Todo se anota en `fanout_log` con
acciones propias (`odoo_delta`, `woo_cambio`, `stock_watch_freno`).

---

### v0.25.0 — La protección de stock FULL/FBA nunca se guardó: ahora se COMPENSA

Auditando la sincronización Odoo→Woo con el fan-out ya encendido apareció un SKU
con stock **negativo** en Woo (`MUE-0307-GRI` en **−5**, contra 2 en Odoo). El hilo
llevó a un supuesto que llevaba meses dado por bueno.

#### El hallazgo: `_order_stock_reduced` se manda, pero Woo NO lo persiste

Desde el día 1, un pedido FULL/FBA nace con la meta `_order_stock_reduced=yes`
para que Woo no toque bodega (la pieza sale del almacén del marketplace, no del
nuestro). **Esa meta nunca quedó escrita.** Se verificó en vivo: un `PUT` a la
REST responde **200** y la meta **no aparece** en `wp_wc_orders_meta`, mientras
las metas `_ml_*` del mismo request sí quedan. La REST de Woo la filtra por ser
interna — lo que la regla 7 de CLAUDE.md ya advertía para la LECTURA resultó ser
cierto también para la ESCRITURA.

Entonces, ¿por qué llevaba meses funcionando? Porque Woo **sí honra la meta
dentro de la misma petición** (la pone en el objeto en memoria antes de correr el
hook), aunque no la guarde. De ahí que:

| Origen | Cómo nace | Resultado |
|---|---|---|
| **FULL de ML** | ya en su estado final, de un solo golpe | el hook ve la meta en memoria → **no descuenta** ✅ |
| **FBA de Amazon** | `on-hold` (Amazon los manda *Pending*) y **después** se pasa a `completed` | esa **transición posterior** relee el pedido de la BD, donde la meta **ya no está** → **descuenta** ❌ |

La protección de FULL funcionaba **por accidente de forma**, no por la meta. Y el
candado de cancelación (mandar `_order_stock_reduced=no` antes de cancelar) era
igual de inútil por la misma razón.

- **Daño real, medido y acotado**: **6 pedidos** de **4,989** protegidos —
  todos FBA, todos `MUE-0307-GRI`, **7 piezas**. Woo llegó a −5; ya está en
  **2 = Odoo**. Barrido completo: **0 pendientes**.

#### El fix (opción A): compensar después de la transición

Como la meta no se puede escribir, se corrige el efecto. Tras cada escritura a Woo
de un pedido protegido se lee **lo que Woo realmente descontó** — `_reduced_stock`
por línea, su contabilidad de verdad — y se devuelve.

- **Cero ruido**: un pedido FULL de ML no tiene `_reduced_stock`, así que la
  compensación no hace nada (verificado en producción).
- **Idempotente**: cada devolución se sella en `fanout_log` como
  `full_compensado`; un pedido sellado no se vuelve a tocar. Los 6 pedidos
  corregidos a mano se sembraron ya sellados para que el código no los duplique.
- **La línea no guarda el SKU**: guarda `_product_id`/`_variation_id`. La primera
  versión buscaba `_sku` y devolvía `None` — habría corrido sin compensar nada.
  Se resuelve el producto por id (variación si existe, si no el padre).
- **Cancelaciones**: al reponer, Woo **BORRA** `_reduced_stock` (medido: de 665
  pedidos cancelados en julio, **cero** conservan una sola línea). Por eso la
  foto se toma **ANTES** del `PUT` de cancelación; si no, la reversión leería
  vacío y el stock quedaría inflado. Con la foto, se resta lo que Woo repuso de
  más y queda como `full_compensado_revertido`.

#### De paso, confirmado que lo demás sí funciona

Los pedidos **no-FULL sí descuentan** (los que aparecen sin `_reduced_stock` son
justo los cancelados, donde Woo borra la meta): el comportamiento de negocio del
día 1 está intacto.

---

### v0.24.0 — Correcciones de las DOS auditorías: descuento fantasma + tabla FULL reescrita

Aplica lo que las auditorías adversariales del 27-28 jul dejaron confirmado.

#### 🔴 Bug NUEVO que destapó la auditoría del fan-out: descuento FANTASMA

**Mercado Libre manda avisos TARDÍOS del ciclo de vida** (medido: **+28 días** al
cerrar una orden, **+20** al expirar una impaga). Esas ventas de junio se
registraban en Woo por PRIMERA VEZ el 27-28 de julio: como no existía pedido
previo, `accion == "creado"` → nacían `completed` **y descontaban stock**. Pero
esa mercancía salió del almacén hace un mes y **ya estaba reflejada** en la carga
de Odoo del 17-jul: se restó dos veces. Y el fan-out, haciendo bien su trabajo,
**replicó cada baja a Amazon**.

> `ACC-0250-NEG` cayó **74 → 67 en 17 horas** por 6 ventas del 29-30 de JUNIO, y
> las 6 bajas se escribieron en Amazon (74→73→72→70→69→68→67).

- **Fix**: `DIAS_VENTA_VIEJA = 5` — una venta con más de 5 días nace marcada
  `_order_stock_reduced=yes`, igual que las FULL: se registra como histórico
  **sin mover bodega**. El fan-out NO se tocó: es un espejo fiel de Woo, y taparlo
  ahí dejaría Woo mal y Amazon bien (peor).
- **Corrección de datos**: 10 pedidos afectados en 4 SKUs. Se devolvieron las
  piezas y se alineó todo contra Odoo — `ACC-0250-NEG` volvió a **74 = Odoo**.

#### 🔴 `stock_full`: la tabla de decisión estaba invertida

- **`TRANSFER_DELIVERY` → `None`** (era el ÚNICO mapeado a `resta`). NO significa
  "llegó mercancía": es un barajeo INTERNO de ML entre sus buckets. Medido: **102
  operaciones en 60 inventarios y el `total` de la bodega no cambió NI UNA VEZ**.
  Activarlo habría desinflado Woo de forma monótona (−329 pzas solo en la muestra).
- **`INBOUND_RECEPTION` → `resta`** (NUEVO): es el ingreso REAL a FULL, verificado
  en producción (129 pzas en SANCORFASHION, 17 de CUNA-0018 con su `inbound_id`).
  Sin él, el hueco de las 1,526 pzas seguía abierto.

#### 🟠 Las tres defensas que no defendían

1. **Verificación cruzada**: comparaba un NIVEL de stock (40-120) contra un DELTA
   (1-6) — tautología que no bloqueó **ninguna** de 55 operaciones simuladas.
   Ahora usa el `result.total` que **la propia operación ya trae** (foto exacta
   del instante) y exige coherencia, en vez de un GET posterior con el total actual.
2. **Idempotencia**: sellaba operaciones que NO se aplicaron — con un 502 del WAF
   de Hostinger (pendiente #1) el movimiento se perdía para siempre. Ahora solo
   sellan las acciones realmente aplicadas y sin `ERROR`.
3. **Vigilante de FBA**: moría en CADA ejecución (`asyncio.run` dentro del
   AsyncIOScheduler) — **nunca corrió**. El token ahora se pide en un hilo con
   loop propio cuando ya hay uno activo (verificado en vivo). El mismo bug estaba
   en `_escribir_amazon` del fan-out. Además guarda **foto propia** en
   `fanout_log`: antes se apoyaba en `canal_inventario.stock_fba`, que el sync
   refresca cada 15 min → el mismo ingreso se habría descontado en cada vuelta.

#### Qué dejó la auditoría del fan-out (lo que SÍ funciona)

8 escrituras reales a Amazon, **0 errores**, verificadas contra SP-API
(`ACC-0250-NEG` 67/67, `MES-0065-NEG` 2/2, `TEC-1031-NEG` 255/255). Cero fugas:
las 15 ventas no-FULL generaron sus 15 eventos. Latencia 7-13 s. Sin eco.
**5 de 8 sospechas fueron DESCARTADAS** por el verificador adversarial.

Dato estructural: **el fan-out nunca ha escrito en Mercado Libre y hoy no puede**
— no existe ni una publicación ML `active` + no-FULL (las 782 activas son FULL;
las 2,308 no-FULL están pausadas; caché contrastado 16/16 contra ML en vivo). Hoy
es un flujo **exclusivamente Amazon**, y cubre ~40% de su catálogo vivo (el resto
está DISCOVERABLE = dormido y se omite a propósito).

#### Estado

`FULL_WATCH_ENABLED` sigue **apagado**; `FULL_WATCH_SOLO_REGISTRO` en `true`.
El fan-out DROP sigue **encendido y escribiendo**. Versión 0.24.0.

### 2026-07-29 — Estudio: los campos de precio/costo dejan de ser una trampa (Eduardo)

**Incidente `TEC-2352-GRI`.** Se escribió `629` en "Precio regular" del Estudio,
se pulsó Guardar (dijo "Contenido guardado en WooCommerce") y al publicar salió
en ML a **$374.11** en ambas cuentas (`ml_backlog` #5785/#5786 con
`"price": 374.11`, MLM3214874713 y MLM3214887995, pausadas). El 629 nunca
existió: Woo conservó `_regular_price` 374.11 / `_sale_price` 314.25.

Cadena del defecto — tres eslabones, todos en `ProductStudio.tsx`:

1. Los campos "Precio regular / Precio oferta / Costo" eran **inputs editables**
   dentro de una sección que el propio código rotulaba `(solo lectura)`: un
   espejo de Woo disfrazado de formulario.
2. `guardarContenidoWoo()` manda **solo** `titulo`, `descripcion` y `atributos`
   — el precio no viaja a ningún endpoint. Y tampoco entra al borrador local
   (ese cubre título/descripción/atributos), así que vivía solo en estado React.
3. El `useEffect` de metadata hace `setCampos({...})` de **reemplazo completo**;
   el `onGuardado?.()` posterior al guardado recarga el producto y repuebla el
   campo con el valor de Woo. Al publicar, `reqPublicar()` ya mandaba el viejo
   (y `construir_prod` cae igual a `_regular_price` si llega vacío).

**Arreglo:** `Campo` acepta `soloLectura` y pinta el valor como espejo (mismo
look que Stock, ya probado en esa misma grid) con la nota "Se cambia en COSTOS
↓". Se retiró el botón **"Usar como precio"** del panel de competencia: tenía el
mismo defecto — escribía el campo fantasma, parecía aplicado y se publicaba el
precio viejo; su nota ahora dirige a ajustar costo/margen en COSTOS.

El precio en este sistema es un valor **derivado** (costo + margen + comisión +
envío): su único escritor sancionado es **COSTOS → "Guardar costo y precios"**,
que recalcula, persiste en `costos_finales` y sincroniza a Woo (regular/oferta,
replicando a variantes). Ahora la UI lo refleja en vez de contradecirlo.

Sin cambio de versión: `backend/main.py` estaba con trabajo sin commitear de
otra sesión (router `fulfillment`) y bumpear ahí habría arrastrado su WIP.

### v0.33.0 — El precio SÍ se edita desde el Estudio (y se guarda de verdad)

Corrección de rumbo sobre lo anterior: la intención de Lalo era **poder fijar el
precio desde el Estudio**, no cerrar el campo. Volvió a ser editable, pero ahora
persiste — que era lo que faltaba.

**Backend.** `costos.aplicar_precio_manual()`: si llegan `precio_base` y/o
`precio_sugerido` como override, el precio escrito a mano MANDA sobre el
derivado del costo, y el desglose se rehace **hacia atrás** (comisión, IVA,
ganancia, ROI). Clave: el **fee de envío se re-evalúa**, porque en ML depende
del precio — en la prueba, subir 374.11 → 629 mueve el flete de $0 a $84.50; sin
recalcularlo, la ganancia mostrada saldría inflada en esos $84.50. Con dar uno
de los dos precios basta: el otro sale de la misma relación `DESCUENTO_BASE`.
Valores 0/vacíos/no numéricos se ignoran (el cálculo queda intacto).
`RecalcularCostos` expone los dos campos; `_preparar_base` los ignora por su
whitelist, así que no ensucian `costos_validados`.

**Frontend.** "Precio regular" y "Precio oferta" vuelven a ser editables (Costo
y Stock siguen siendo espejo: el costo se edita en COSTOS como "Costo producto
(USD)"). Al tocarlos aparece **"Guardar precios"**, que va por el MISMO escritor
que "Guardar costo y precios" → `costos_finales` + WooCommerce (replicando a
variantes). Esto es obligatorio, no cosmético: el Estudio lee el precio de
`costos_finales` ([studio.py](backend/services/studio.py)), así que escribir
solo en Woo dejaría el panel mostrando el viejo — la misma trampa otra vez.

**La causa raíz, atacada aparte:** el `useEffect` de metadata hacía
`setCampos({...})` de reemplazo y llega DESPUÉS de abrir el modal — si escribías
rápido, te pisaba el precio en silencio. Ahora un ref `preciosTocados` protege lo
tecleado, y mientras haya un precio sin guardar la UI avisa: *"Cambiaste el
precio: sin guardar, se publica el anterior"*. `Regenerar` / `Guardar costo y
precios` siguen mandando (recalculan desde el costo y limpian el precio manual).

Limitación conocida: fijar precio requiere que el SKU tenga costo capturado (el
cálculo necesita `costo_unitario > 0`); si no, el endpoint responde 422 y el
modal dice que primero se registre el costo en COSTOS. Versión 0.33.0.

### v0.33.1 — "Guardar costo y precios" conserva el precio puesto a mano

Quedaban DOS botones que guardaban precios con semánticas opuestas: el nuevo
"Guardar precios" respetaba lo tecleado y "Guardar costo y precios" lo
recalculaba y lo pisaba. Observación de Lalo, y tenía razón: el botón que dice
guardar debe guardar **lo que se ve**. Ahora `guardarCosto()` reenvía el precio
manual como override cuando el usuario tocó los campos, así que los tres
controles quedan sin ambigüedad:

| Control | Qué hace con el precio |
|---|---|
| **Guardar precios** (junto a los campos) | Guarda el precio que escribiste. Atajo sin bajar a COSTOS |
| **Guardar costo y precios** (COSTOS) | Guarda costo, dims **y** tu precio si lo fijaste; si no lo tocaste, el derivado |
| **Regenerar costo** (COSTOS) | Vuelve a derivar el precio del costo — es el modo de DESCARTAR el precio manual |

El texto de ayuda bajo los botones ahora lo dice explícitamente, para que
"Regenerar" no borre un precio sin avisar. Versión 0.33.1.

### v0.33.2 — Prueba end-to-end en producción con TEC-2352-GRI (+ fix del acuse)

Se ejercitó el flujo completo desde el panel real: abrir el Estudio, escribir
**629** en Precio regular, pulsar **Guardar precios**. Resultado medido en las
tres capas (antes → después):

| Capa | Antes | Después |
|---|---|---|
| MySQL `costos_finales.precio_base` | 374.11 | **629.00** |
| WooCommerce `_regular_price` (wc_id 100924) | 374.11 | **629.00** |
| kubera `costing.costos_finales` (espejo) | 374.11 | **629.00** |

Las tres con el mismo `updated_at` (18:29:54) — el dual-write viajó solo, así
que la racha de actas de Costos no se rompe. Al reabrir el Estudio de cero, el
campo carga 629 desde `costos_finales`: la persistencia es real, no de pantalla.
`precio_sugerido` quedó en 314.25 porque solo se tocó el campo de arriba —
"guarda lo que se ve" — y por eso comisión/flete no se movieron (se derivan del
precio de oferta).

**Defecto que destapó la prueba:** el bloque del acuse colgaba de
`preciosEditados`, que se limpia al guardar bien → el "Precio guardado y
sincronizado con WooCommerce" se desmontaba en el mismo instante en que debía
leerse. Ahora el bloque sigue montado mientras haya mensaje (en gris, ya sin
botón) y solo el botón depende de que haya cambios pendientes. Versión 0.33.2.

### v0.33.3 — La nota del precio sigue al canal seleccionado

La ayuda bajo "Precio regular" decía **"Se publica en ML"** fija, así que con
Amazon seleccionado mentía (reportado por Lalo con captura). Ahora sale del
canal activo: `Se publica en ${canalInfo?.label}` — "Se publica en Amazon",
"Se publica en Mercado Libre" — y en **General** no se muestra, porque ahí el
precio ES el del catálogo de WooCommerce y la nota sobraría. Versión 0.33.3.

### v0.34.0 — Observabilidad del flag F5: contador kubera-vs-fallback + alerta Slack + guardia de plausibilidad (Eduardo)

El fallback de las lecturas de costos (v0.23.0) era MUDO: si kubera fallara,
el panel seguiría vivo desde MySQL y nadie sabría que el flag no está probando
nada. Tres piezas lo hacen visible, TODO inerte mientras el flag siga apagado:

1. **`services/lecturas_fuente.py`** — contadores en memoria por dominio de
   quién respondió cada lectura (kubera vs fallback), expuestos en
   `GET /api/migracion/estado` (campo `lecturas`) y pintados en /migracion
   como chip: "Costos: 1,240 kubera (100%)" (verde; rojo si hubo fallback,
   con tooltip del último error). Esa cifra es la evidencia que autorizará el
   corte del dominio ("N días al 100% kubera").
2. **Alerta Slack al primer fallback** — `alertas.avisar("lectura_fallback:costing", …)`
   en el except de los 3 GET: aviso en #alertas-omnicanal en segundos, con el
   anti-spam del sistema existente.
3. **Guardia de plausibilidad** — el fallo que no truena: kubera respondiendo
   "bien" pero VACÍO (0 contenedores / total=0 sin filtros) con MySQL operando
   se trata como fallo → fallback + alerta, en vez de pintar un panel vacío.
   Condicionada a `mysql_enabled` para que staging (sandbox vacío, sin MySQL)
   pueda devolver vacío legítimamente.

Publicado desde worktree aislado (el árbol principal traía WIP de otra
sesión). Versión 0.34.0.

### v0.36.0 — El acta de Channel: el `closed` de Amazon ya viaja al espejo

**Qué rompió la racha (acta del 30-jul, 289 divergentes tras 9 días en cero).**
`scripts/marcar_amazon_muertas.py` (29-jul) marca como `closed` los listados que
Amazon responde 404, con un `UPDATE canal_inventario` **directo**. El espejo
`channel.listings` no se enteró: no lee la tabla, es un dual-write que se
dispara desde `inventario._upsert()`. Lo que no pasa por ahí, no llega.
Resultado medido: 289 filas `closed` en MySQL vs `PUBLISHED` en Supabase
(amazon PUBLISHED 333 = 289 + los 48 legítimos).

**Y no se curaba sola** — esto es lo importante. Para esos SKUs Amazon devuelve
404, así que el barrido manda `situacion=NULL`, y ambos lados conservan el valor
previo ante un NULL (política correcta: "no lo observé en esta pasada"). MySQL
conserva `closed` ✅, el espejo conserva `PUBLISHED` ❌. Cada barrido
reconfirmaba la divergencia: `parpadeos_descartados=0`, las 289 sobrevivían la
re-verificación de 75 s. Una re-corrida NO rescataba el día.

**Arreglo, en dos piezas:**

1. `channel_mirror.backfill_situacion(situacion, canal)` — re-espeja a
   `channel.listings` la situación que hoy tiene `canal_inventario`, usando el
   **mismo escritor** que el sync (`espejar_inventario`), no un UPDATE aparte:
   así conserva el `set_config` de la vía para el trigger de historia y la
   resolución cuenta→uuid. Idempotente por el `where ... is distinct from` del
   upsert, así que no ensucia `channel.listing_history`.
   Expuesto en `POST /api/migracion/backfill/channel-situacion`.
2. `marcar_amazon_muertas.py` llama a ese backfill al `--aplicar`, y si el
   espejo está apagado lo dice en pantalla con la instrucción de correr el
   endpoint en producción. La recaída queda cerrada en el origen.

Nota de convivencia: la nota que la otra sesión dejó ese día
(`docs/NOTAS_PARA_LALO_2026-07-29.md`) pedía coordinar el cambio ANTES de
aplicarlo, justo por este efecto; se ejecutó 34 min después. El dato de MySQL es
el correcto (decisión de Brandon: descontar sin borrar) — el que estaba
desactualizado era el espejo. Versión 0.36.0.

---

### v0.37.0 — Fix: actualizar en ML ya no truena por `family_name` (BODY_INVALID_FIELDS)

**Síntoma.** Al **actualizar** (no crear) una publicación viva desde el Estudio,
ML devolvía `400 BODY_INVALID_FIELDS` y no se guardaba nada. La respuesta cruda
(en `ml_backlog.ml_response`, no en `ml_backlog.error` que solo tenía el genérico)
lo decía textual: `cause 374` · *"You cannot modify the title if the item has a
family_name"*. Cuando ML mete un ítem en una **familia** (catalogación), prohíbe
cambiar el título.

**Causa.** `publicar._update_ml_una` metía **siempre** el `title` en el cuerpo del
`PUT /items/{id}`. ML rechazaba el PUT **completo**, así que tampoco entraban los
`attributes` y la descripción se saltaba (`if desc and not error`). En el panel:
"sale error" y cero cambios.

**Alcance medido (30-jul).** ~**1,657 SKUs** distintos / ~3,428 intentos con
`family_name` en `ml_backlog` — bug general de todo ítem ya catalogado por ML
(ej. `ORG-0846-MUL`, `ACC-0468-NEG`, `MUE-0178-ROS`).

**Arreglo (auto-sanación, como los reintentos del pipeline `ready`).** Nuevo
`_es_error_family_name(resp)` (cause 374 / texto `family_name`). Si el PUT
devuelve 400 por esa causa, se reintenta **sin `title`** conservando los
`attributes`; si el título era lo único, se omite el PUT y se pasa directo a la
descripción. El caso normal (ítems sin familia) no cambia. Resultado: atributos y
descripción **sí** se guardan; el título queda como ML lo exige. Versión 0.37.0.

### v0.41.0 — Sección ANÁLISIS: reabastecimiento + Estrellas leyendo la BD kubera en vivo (Eduardo)

Primer LECTOR de producción de la BD kubera con cara de usuario: la sección
**Análisis** del navbar (clon del tablero kubera-fulfillment de José, que
muere junto con dailytrackMeli). Todo es LECTURA — ninguna escritura nueva.

1. **Migración `0007_fulfillment_vistas.sql`** (aplicada a sandbox y a
   producción el 30-jul, precheck previo de insumos): 2 vistas derivadas al
   vuelo (regla v4) + parámetros `RESTOCK` en `costing.pricing_params`.
   - `channel.sales_daily_completa`: ventas SIN hueco — historia rescatada de
     dailytrack (27-dic-2025 → 15-jul) ∪ flujo vivo de `order_items` (16-jul→).
   - `channel.restock_panel`: Bollinger 45 d (k 1.5, contando días en cero),
     stock mín/máx, sugerido a FULL y semáforo.
2. **`routers/fulfillment.py`** (`/api/fulfillment/*`): `meta`, `dashboard`,
   `tabla` (orden con dirección `dir` real e invertible, nulls last en ambas),
   `detalle` (serie diaria de un SKU) y `estrellas` (Pareto all-time con
   ambos pares share/acumulado en una pasada; ventas sin SKU fuera del
   ranking pero declaradas en `sin_sku` para que los totales cuadren).
   Precio = SOLO publicación activa (max() mentía en 27% de los SKUs);
   costo = `costos_validados.costo_total` con fallback a `costo_unitario`;
   DROP = listing `canal='general'` (bodega Woo real).
3. **Frontend `app/analisis/`**: layout con banner por sub-sección; submenú
   desplegable en el navbar (variante definitiva); Reabastecimiento con modal
   de detalle por sparkline; Estrellas (tabs por cuenta, toggle uds/$,
   Pareto SVG top-50 con cortes 50/80/90 sobre el universo completo);
   esqueletos honestos de Amazon FBA y Reportes con sus bloqueos declarados.
   Componente `Ayuda` ("?" por columna con descripción en lenguaje llano).
   Navbar: Ventas abre la barra y Dashboard se renombró **Operaciones**
   (misma ruta `/dashboard`).

Sabido y aceptado al publicar: la serie viva de ventas por SKU depende de
`pedidos_ml_items`, que sigue FUERA de `KUBERA_MIRROR_TABLAS` (regla 3 —
pendiente el dale de Brandon): stock y precios se mueven en tiempo real
(webhook + barrido 15 min), las VENTAS del panel quedan al 28-jul hasta
encender esa tabla. El DROP vivo (F2, `stock_watch_foto` → listings canal
general) también sigue pendiente; hoy está al 24-jul.

### v0.41.1 — Las ventas del panel, en vivo: `pedidos_ml_items` ENCENDIDO (Eduardo)

Cierra el pendiente que dejó v0.41.0. **El dale lo dio Eduardo** como dueño de
la migración; la regla 3 nombra a Brandon y queda anotado que la decisión fue
de Eduardo, evaluada así: es una escritura ESPEJO hacia kubera — no toca Woo,
ML, Amazon, stock ni un solo cliente; el upsert exige la orden padre
(`DO NOTHING` si falta, sin huérfanos) y los fallos van a `espejo_kubera_log`
sin tumbar el pedido.

Dos pasos, en este orden:

1. **Backfill del hueco** que dejó tener el flag apagado: `channel.orders` iba
   vivo al 30-jul pero `order_items` se quedó en el 28 — **1,263 pedidos sin
   líneas** (376 del 28, 550 del 29, 270 del 30). Nuevo modo
   `--solo-faltantes` en `backend/scripts/backfill_order_items.py`: en vez de
   barrer las ~58 páginas de Woo, le pregunta a kubera qué pedidos no tienen
   líneas y se los pide por `include` — 13 peticiones, 110 s. Resultado: 1,263
   líneas, 942 con `item_id` enriquecido desde listings, 0 pedidos ajenos.
   Sirve igual cada vez que el flag se apague y se vuelva a encender.
2. **`pedidos_ml_items` sumado a `KUBERA_MIRROR_TABLAS`** (variable Railway).
   De ahí en adelante cada webhook de ML escribe encabezado Y líneas en el
   mismo seam, sin llamadas extra: los datos ya venían en memoria.

`channel.sales_daily` es una vista sobre `order_items`, así que la serie del
panel avanza sola desde el momento del encendido. Sigue pendiente el DROP vivo
(F2).

### v0.42.0 — F2: el DROP en vivo, y el acta deja de auditar un fósil (Eduardo)

Última pieza para que ANÁLISIS esté completo en tiempo real. El canal `general`
(bodega propia) mostraba stock del **24-jul** porque su fuente estaba muerta:
`canal_inventario` dejó de observar ese canal el 14-jul y quedaron **20 filas
fósiles**. La verdad de la bodega propia es `stock_watch_foto` — la foto de Woo
que el vigilante de Brandon reescribe cada 20 min.

1. **`channel_mirror.sincronizar_drop()`** — lee `stock_watch_foto` y la espeja
   a `channel.listings` canal `general`, en bloque con `execute_values` (13k
   SKUs fila por fila serían 13k viajes cada 20 min; así son 21 s). Solo viaja
   `stock_own`: precio, situación y FULL son de los marketplaces y van NULL
   para que el `coalesce` conserve lo que hubiera. Los SKUs con `stock_woo`
   NULL se saltan — Woo no gestiona su stock y un 0 inventado sería peor que
   callar. El `where ... is distinct from` lo hace idempotente: segunda pasada
   = 0 cambios, sin ensuciar `channel.listing_history`.
2. **Job propio en el scheduler** (`DROP_MIRROR_ENABLED`, cada 20 min) y no un
   gancho al final de `stock_watch`: si el vigilante está apagado o su pasada
   aborta (Odoo mudo), el DROP del panel debe refrescarse igual.
3. **Regla 5 del acta de Channel** — `comparar_channel.py` deja de auditar el
   canal `general` en AMBOS lados. Es la extensión de su regla 3 (NULL = no
   observado) a un canal entero: comparar contra el fósil del 14-jul marcaría
   divergente justo lo que se acaba de actualizar bien. Verificado en seco
   contra producción antes y después de la carga: **0 divergencias las dos
   veces**, canales comparados `['amazon','mercado_libre']`.

Primera carga aplicada a producción: **12,942 SKUs, 1,103,767 piezas** — el
total exacto de `stock_watch_foto`. Con esto el panel deja de mentir sobre lo
que hay en bodega propia, que es justo el número del que depende el "enviable"
del sugerido de reabasto.

### v0.42.1 — Precio de venta: una línea por cuenta, siempre (Eduardo)

La celda colapsaba las cuentas en un solo precio cuando coincidían (`$989` con
las etiquetas `BK SC` debajo) y solo las separaba cuando diferían. Eran dos
formatos para leer la misma columna, y el colapsado obligaba a DEDUCIR que
ambas cuentas estaban en el mismo precio. Ahora siempre `BK $989` / `SC $989`:
ver el número repetido comunica el hecho sin inferencia.

Ordenado por cuenta (no por precio) para que el ojo encuentre la misma cuenta
en el mismo renglón entre filas. Deduplicado por cuenta+precio: dos
publicaciones de la misma cuenta al mismo precio son una línea; a precios
distintos son dos — y eso hay que verlo, no esconderlo. "Sin activa" no cambia.

Sin costo de espacio: la altura de fila sigue en 49–54 px (esas filas ya eran
de dos líneas por otras columnas) y no aparece scroll horizontal.

### v0.42.2 — Los datos personales del comprador se guardan CIFRADOS

**Por qué.** El cuestionario de cumplimiento de Temu **rechazó la solicitud de
API** con una exigencia explícita: *"For storage of personally identifiable
information (PII) such as names, phone numbers, addresses, and emails,
encryption is required. In this context, the term 'user' refers specifically to
the recipient."* Pedían además una captura de la base que demostrara el cifrado.
La respuesta que estaba marcada ("Yes") era falsa: todo estaba en texto plano.

**Qué guardábamos de verdad** (censo en vivo del 30-jul, no del caché):

| Dato | Cantidad | Dónde |
|---|---|---|
| Nombre del comprador | **7,279** | `wp_wc_order_addresses` |
| Nick de ML | **7,095** | meta `_ml_comprador` |
| Correo | 0 | — |
| Teléfono | 0 | — |
| Dirección | 0 | — |

El nombre es el **único** dato personal en toda la base. No hay un solo correo,
teléfono ni dirección de ningún comprador.

**Cómo quedó.** `services/pii.py` (escrito el 29-jul y que nunca se había
conectado) ahora sí se usa desde `pedidos_ml.py`: el nombre, el apellido y el
nick se cifran con **Fernet** (AES-128-CBC + HMAC-SHA256) antes de escribirse en
WooCommerce. La llave vive solo en la variable `PII_KEY` de Railway, nunca en el
repositorio.

Tres decisiones de diseño que importan:

- **Prefijo `enc:`** → la operación es idempotente. Un valor ya cifrado se
  reconoce y no se vuelve a cifrar, así que el barrido de históricos se puede
  repetir sin dañar nada.
- **Sin llave NO se escribe en claro.** Si `PII_KEY` falta, `cifrar()` devuelve
  el marcador genérico `"Comprador"`. Se prefiere perder el dato a guardarlo en
  claro creyendo que va cifrado — un fallo silencioso aquí sería exactamente lo
  que el cuestionario castiga. El script de barrido directamente **aborta**.
- **Nada del panel lee el nombre.** Se verificó: se escribe en 3 lugares y se lee
  en 0. Cifrarlo no rompe ningún flujo. La única consecuencia visible es que el
  admin de WooCommerce muestra `enc:gAAAAA…` en la lista de pedidos.

**Histórico.** `scripts/cifrar_pii_historico.py` cierra los pedidos ya
existentes. Usa SQL directo sobre `wc_order_addresses` y `wc_orders_meta` — una
excepción acotada a la regla de no hacer DML sobre `wp_*`, y por seguridad, no
por comodidad: son tablas de **almacenamiento plano** de HPOS (no derivadas como
`wc_product_meta_lookup`), mientras que un `PUT /orders/{id}` dispararía los
hooks de actualización de WooCommerce sobre 7,279 pedidos de producción.

**Verificado en producción** con el pedido 100969 antes de correr el barrido:

```
en la base : enc:gAAAAABqa76WbjRBfCtd23LE-llQPti_8x33GU0LE6p9Qng…
por la API : enc:gAAAAABqa76WbjRBfCtd23LE-llQPti_8x33GU0LE6p9Qng…
descifrado : Cecilia
```

La API REST devuelve exactamente lo mismo que la base — no hay caché sirviendo
el valor viejo — y la llave recupera el original. El flag `--verificar <pedido>`
del script reproduce esta comprobación cuando haga falta.

**Ojo con los conteos**: durante la prueba el total subió de 7,279 a 7,281 en
segundos. No es un error del barrido — son **ventas entrando en vivo** por el
webhook. Por eso el orden correcto es: poner `PII_KEY` en Railway → desplegar →
barrer el histórico. Al revés, los pedidos que lleguen entre el barrido y el
deploy nacerían en claro.

**Lo que NO resuelve.** El cuestionario de Temu sigue con huecos reales que no se
tapan con cifrado: la API del panel responde **sin autenticación** (III.1) y no
hay bitácora de auditoría (IV.1/IV.2). Ver
`docs/PROCEDIMIENTO_NOTIFICACION_BRECHAS.md` para la parte de incidentes (V.1).
Versión 0.42.2.

### v0.43.0 — Análisis crece: Categorías con árbol completo, margen por cuenta y navbar reorganizado (Eduardo)

Cuatro piezas en un paquete, todas probadas en sandbox antes de publicar:

1. **Sección CATEGORÍAS** (`/analisis/categorias` + `GET /api/fulfillment/
   categorias` y `/categorias/publicaciones`). Réplica EN VIVO del reporte
   ventas_por_categoria de José (xlsx del 19-jul) con su mismo drill y más
   profundo: el árbol COMPLETO de ML (hasta 7 niveles; el xlsx cortaba en 4)
   → publicaciones individuales (MLM…, cuenta, título congelado de la venta,
   situación, uds, $, precio, 1ª venta). La categoría viene de
   `channel.product_category` + `channel.categories` (99.9%% de lo vendido
   clasifica; las ventas de Amazon también, porque la taxonomía se aplica por
   SKU). El backend manda las HOJAS con su ruta y la UI arma el árbol con
   acumulados por nivel — un query para cualquier profundidad; las
   publicaciones se piden al expandir (tope 200). FULL JOIN con el catálogo
   listado: una categoría con publicaciones y CERO ventas también viaja —
   buscar "Caminadoras" en 60 días responde "existe y no vendió", no "no
   existe"; la UI las esconde salvo al buscar (buscador en la cabecera de la
   columna, filtra por la ruta completa, con nota de que alcanza lo oculto).
   "Días en venta" del xlsx NO se replica: listings no guarda la fecha de
   creación — se muestra la 1ª venta del período, declarado en el pie.
2. **Margen por cuenta** en Reabastecimiento: la columna Margen se desglosa
   renglón a renglón alineada con la de precio (mismo orden vía
   `preciosDeVenta()`). Antes un solo número con el precio activo MÁS BARATO:
   TEC-0977-NEG-800W decía 73.8%% y callaba el 88.3%% de BEKURA. El costo es
   uno por SKU: entre cuentas solo cambia el precio.
3. **Navbar reorganizado**: ANÁLISIS abre la barra y VENTAS dejó de ser
   pestaña — vive en el submenú de Análisis (ruta `/ventas` intacta: página
   autónoma). El item se marca activo también cuando la ruta pertenece a una
   entrada del submenú fuera de su prefijo. Submenú: Análisis · Ventas ·
   Categorías · Estrellas · Amazon FBA · Reportes.
4. **Lenguaje de usuario** en todo /analisis: 24 textos visibles reescritos
   sin jerga (nombres de tablas, versiones, "como el archivo de José",
   Bollinger → "ritmo de venta considerando sus picos"). Los términos de la
   casa (FULL/DROP/FBA/SKU/ASIN) se quedan; los comentarios del código
   conservan el detalle técnico. + **Aviso "Sección en desarrollo"** en el
   layout (cubre las 5 vistas; Ventas fuera a propósito): pide reportar
   cifras que no cuadren al equipo de tecnología.

Sembrado del sandbox: `channel.categories` (2,679) y `product_category`
(13,689) copiadas desde producción — estaban vacías y TODO caía en "Sin
categoría". Producción es el origen: nada que aplicar allá.

### v0.47.1 — El KPI de ventas ya suma TODO lo vendido (Eduardo detectó la discrepancia)

Eduardo comparó el panel contra los dashboards de ML y el KPI "UDS 7D" decía
2,564 cuando la propia gráfica de abajo sumaba 3,243. Diagnóstico con datos:

- **674–818 uds/7d perdidas por el KPI** (creciendo): salía de `filas` (la
  tabla de reabastecimiento, solo SKUs con publicación viva) y perdía la venta
  de publicaciones YA CERRADAS — venta real. La gráfica usaba la serie
  completa: el panel se contradecía a sí mismo.
- Arreglo: `uds_periodo`/`venta_periodo` se derivan EN PYTHON de la MISMA
  serie que pinta la gráfica — un solo dato mostrado dos veces; no pueden
  divergir. Verificado: suma(barras) == SQL directo a la vista, al centavo.
  El resto de KPIs (productos, activos, stock) sigue saliendo de la tabla de
  listados, donde ese filtro es correcto.
- Los dos KPIs llevan ahora un "?" que responde la pregunta que originó todo:
  contra el dashboard de ML no cuadra exacto porque aquí las canceladas se
  excluyen y los días se cortan con horario de México (el resto del hueco de
  Eduardo: +266 uds de canceladas y ~350 de ventana, medidos el 31-jul).

PENDIENTE detectado en el mismo diagnóstico, NO corregido aquí: la ventana
`date > current_date - N` usa current_date en UTC — por las tardes-noches de
México el panel muestra N-1 días de datos reales. Corregirlo mueve cifras en
todos los endpoints de fulfillment; se decide aparte.

---

### v0.44.0 — Crear Producto: guard "sin costo → no crear" (deja de pisar el nombre/imagen de Odoo)

**Síntoma.** Al mandar a crear SKUs **sin costo/precio cargado**, el flujo
scrapeaba Alibaba, **sobrescribía el nombre y la imagen originales de Odoo** con
los del scrape y recién al final decidía el estado: sin precio → `inprogress`. El
producto quedaba en limbo — su identidad de Odoo destruida y sin entrar a la cola
de validación (`pending`/`ready`). Pasó con 11 SKUs en una tanda (BEB-0126-BLN,
COC-0152-MET, MIC-0003-AZL…); el único que completó fue el que sí tenía precio
(JAR-0008-MET → `pending`), confirmando la causa.

**Causa.** `crear_producto._procesar` hacía el `PUT` a WooCommerce (nombre,
imágenes, etc.) **antes** de verificar si el producto podía quedar completo. Un
SKU sin `costos_validados` (costo base) ni `costos_finales` (precio) nunca puede
fijar precio, pero igual se le pisaba nombre+imagen.

**Arreglo.** Nuevo `_tiene_costo_base(sku)` (precio en costos_finales o costo en
costos_validados). Se llama como **guard al inicio de `_procesar`**, ANTES de
scrapear/subir imágenes/sobrescribir: si el SKU no tiene con qué fijar precio,
aborta con "Falta costo/precio: agrégalo en Costos antes de crear" y **deja el
draft intacto** en Crear Productos. Un fallo de lectura de la DB no bloquea (best
effort). Recuperación de los 11 dañados: nombre e imagen restaurados desde Odoo
(la imagen original sobrevivía como adjunto en WordPress) y devueltos a `draft`.
Versión 0.44.0.

---

### v0.45.0 — Crear Producto: guard "scrape en falso" (Product Not Available / 0 imágenes)

**Complemento del v0.44.0.** El guard de costo NO cubre el caso donde el SKU SÍ
tiene precio pero **el scrape de Alibaba devuelve basura**: la página de listing
muerto (`"Product Not Available"`, típico de URLs `alilens` expiradas o geo-
bloqueo) trae un título NO vacío, así que pasaba el chequeo `if not scrape["titulo"]`
y sobrescribía el nombre/imagen de Odoo con la basura (caso real: MUE-0178-ROS).

**Arreglo.** Nuevo `_scrape_invalido(titulo, imagenes)` — True si el título
contiene un centinela de página muerta (`product not available`, `no longer
available`, `page not found`, `item not found`) o si **no vino ninguna imagen**.
Se chequea justo tras el scrape: si es inválido, aborta con un error legible
(título + nº de imágenes) y **deja el producto intacto**. Versión 0.45.0.

---

### v0.46.0 — Crear Producto: opción "crear sin costo" (opt-in)

El guard de v0.44.0 bloquea SIEMPRE la creación sin costo. Pero hay un flujo
legítimo: crear el producto (scrape + imágenes + categoría + atributos) y **poner
el precio a mano después** en el Estudio. Para eso se agrega un **check opt-in
"Crear sin costo"** en la barra de acción de Crear Productos (apagado por
defecto). Cuando se marca, el request manda `permitir_sin_costo=true`, que viaja
`router → encolar → _procesar` y **salta el guard** de costo (el producto queda en
`inprogress`, sin precio, hasta que se capture a mano). Sin marcar, el guard sigue
protegiendo el caso accidental. No cambia el guard de "scrape en falso"
(Product Not Available / 0 imágenes), que siempre aplica. Versión 0.46.0.

---

### v0.47.0 — "Crear sin costo" cae en Productos (pending), no en limbo + costo pre-lleno con el precio de Alibaba

Dos ajustes al flujo "sin costo" (v0.46.0), a pedido:

1. **No más limbo `inprogress`.** Al crear con "sin costo", el producto ya NO se
   queda en `inprogress` (que se percibe como limbo); pasa a **`pending`**, que en
   la vista Productos es la cola de "validar" (filtro *Inactivos / Sin publicar*).
   Ahí se completa a mano — capturar el precio en el Estudio. Un solo cambio en
   `_procesar`: `status_final = "pending" if (completo or permitir_sin_costo) else
   "inprogress"`.
2. **Costo pre-lleno con el precio de Alibaba.** El Estudio ahora pre-llena
   "Costo producto (USD)" con el precio scrapeado de Alibaba **solo si el campo
   está vacío** (sin costo validado/final). Es una sugerencia editable: el scrape
   a veces trae mal el precio, así que se revisa y se da *Regenerar* (no se guarda
   solo). Antes ese número solo aparecía en el campo de referencia "Precio
   Alibaba"; había que copiarlo a mano al campo de costo. Recordatorio del modelo:
   el precio de Alibaba es el **costo del proveedor (USD)**, no el precio de venta
   — el panel calcula el precio con flete (CBM) + comisión + margen. Versión 0.47.0.

### v0.47.2 — La ventana de los períodos ya se corta con horario de México (Eduardo)

Cierra el pendiente que dejó v0.47.1. `current_date` es la fecha DEL SERVIDOR
—UTC en Railway— pero las ventas están fechadas en horario de México (así las
construye `channel.sales_daily`). Desde las 6 de la tarde de México el servidor
ya cambió de día y la ventana se corría: **"7 días" entregaba 6**, y el total
cambiaba según la HORA a la que abrieras el panel. No se perdía ninguna venta:
se preguntaba por un rango equivocado. Era la mitad de la discrepancia que
Eduardo detectó contra el panel de ML (la otra mitad, las canceladas, ya está
explicada en el "?" de los KPIs).

Arreglo: las 9 consultas del router pasan por `_mx()`, que sustituye
`current_date` por `(now() at time zone 'America/Mexico_City')::date` — la
pregunta queda en la misma zona horaria que el dato. Se descartó hacerlo
configurable por variable: Kubera opera en México y la vista ya está fechada
así en duro; una segunda fuente de verdad sobre la zona horaria sería otro
lugar donde desincronizarse.

Verificado: la ventana se corre un día hacia atrás en todos los períodos
(dias=7 arranca 27-jul en vez de 28-jul; dias=30, 4-jul en vez de 5-jul) y
`_mx()` no deja ningún `current_date` vivo en las 4 constantes SQL.

Efecto visible: de tarde-noche los totales SUBEN (aparece el día que faltaba).
De mañana no cambia nada. También corre un día la ventana de 45 días del
sugerido de reabasto — efecto mínimo, pero es cifra sobre la que se actúa.

### v0.47.3 — Los KPIs dicen QUÉ cuentan (Eduardo)

"¿Puedes explicarme la suma de las unidades? No me queda muy claro." El
tooltip decía de dónde salía el número pero no qué cuenta como una unidad, que
era justo la duda: **cuenta PIEZAS, no pedidos** — un pedido de 3 piezas suma
3. No es un matiz menor: de las 1,304 líneas de la semana, 364 son de 3 o más
piezas y aportan 2,587 de las 3,777 unidades (68% del total).

Ahora cada KPI tiene su propio texto en vez de uno compartido, porque el mismo
párrafo era medio correcto en cada uno: "cuenta piezas" es cierto de las
UNIDADES y no dice nada de los pesos; "es venta bruta, sin comisión ni costo"
es al revés. Ambos conservan el "último día en curso" y la reconciliación
contra el panel de ML (canceladas + ventana horaria).

---

### v0.59.0 — Pegar SKUs de variantes ya encuentra sus productos (Eduardo)

Eduardo pegó 20 SKUs para publicar y el panel "a veces me aparecen dos SKUs nada
más, a veces 'preparando catálogo' y a veces solo en blanco y se traba". Eran
tres fallas distintas apiladas:

- **Variante → padre.** Los buscadores indexan solo productos PADRE
  (`post_type='product'`; las 7,301 variaciones quedan fuera) y matchean con
  "el término CABE dentro del SKU". El SKU de una variante es más LARGO que el
  de su padre (`ACC-0069-ROS-2XL` vs `ACC-0069`), así que **nunca podía
  coincidir**: 15 de los 20 SKUs eran variantes. Nuevo `wp_db.skus_padre()` /
  `expandir_con_padres()` traduce cada variante a su padre por ESTRUCTURA
  (`post_parent`), no por el nombre del SKU, en una sola consulta, y lo AÑADE a
  la búsqueda. Aplica a Crear (`listar_candidatos_agrupados`) y a
  Productos/Omnicanal (`_buscar_wc_ids_wp`). Con los 20 SKUs reales: **5
  productos antes → 14 después**, cobertura completa.
- **"Preparando el catálogo…" para siempre.** El panel reintenta cada 4 s
  mientras `completo` sea false, y `completo` salía de `drafts_completo()`, que
  solo lo enciende el escaneo por API — escaneo que **con MySQL nunca corre**,
  porque el listado se lee fresco de MySQL. O sea: false eterno. Ahora
  `completo=True` cuando `wp_db.disponible()`.
- **Ya no gira sobre lo imposible.** Con búsqueda o filtro puesto, cero
  resultados es una RESPUESTA, no un índice a medio construir: se acabaron los
  45 reintentos (~4 min en blanco) y sale *"No se encontró ninguno de esos SKUs
  por crear · puede que ya estén publicados: búscalos en Productos"*.
- **El tope de términos sube de 10 a 60** en `buscar_drafts`: pegar 20 SKUs
  descartaba la mitad en silencio. Lo que rebase se registra en el log.

Verificado contra MySQL de producción con los 20 SKUs del reporte: 15 variantes
traducidas a 9 padres, 7 grupos en Crear (draft) y 7 en Productos (2 pending, 4
publish, 1 pending), los 14 productos cubiertos. `py_compile` y `tsc` limpios.
Versión 0.59.0.

---

### v0.58.0 — El listado de Productos deja de arrastrarse: 16 s → 4 s en frío (Eduardo)

Eduardo reportó lentitud al buscar SKUs ("Preparando el catálogo…" eterno).
Perfilado llamada por llamada, el tiempo se iba en 4 goteras, todas contra
Hostinger:

1. **El árbol de categorías por REST, en serie** (~15 llamadas × 0.8 s ≈ 12 s)
   cada vez que el caché de 30 min moría — o sea, tras CADA deploy. Ahora sale
   de wp_db en UNA consulta (1,664 categorías idénticas a REST, 0.55 s), con
   el REST como respaldo.
2. **N+1 de variantes**: una llamada REST por padre `variable` (semáforo 3).
   Ahora TODAS las del lote en 3-4 queries wp_db (`variantes_por_padre`).
   Arnés `comparar_variantes_wpdb.py`: 34 idénticas, 6 donde el SQL es MEJOR
   (REST da nombre null si el atributo no liga al padre) y 0 diferencias
   reales; el orden de opciones replica la inserción de `_product_attributes`
   (caso TEC-1661 "Negro / 5 canales").
3. **La búsqueda LIKE (COUNT + ids) en serie y BLOQUEANDO el event loop** —
   mientras alguien buscaba, el backend entero se pausaba. Ahora en paralelo
   y en hilo.
4. **Plan-B `?sku=` innecesario** cuando la búsqueda ya resolvió el término
   como SKU exacto.

Números (frío = proceso recién desplegado): filtro de SKUs 16.4 s → 3.9 s;
caliente 1.4 s; página general 3.6 s frío / 1.4 s caliente. Sin cambios de
contrato (mismas formas de salida). Probado en staging antes del pase.

Hallazgo aparte del reporte original: `ACC-0196-NEG` y `MUN-0020-MUL` están en
**draft** — Productos no muestra drafts por diseño (viven en Crear/Omnicanal);
por eso el "0 productos" de la captura, no solo la lentitud. Versión 0.58.0.

---

### v0.57.0 — El Excel de categorías abre plegado y se descarga desde Reportes (Eduardo)

Afinación del Excel tipo José con el visto bueno de Eduardo sobre archivo real:

- **Agrupación nativa de Excel** en la hoja Categorias (botones +/− al margen,
  niveles 1–7, summaryBelow=false porque el encabezado va arriba de su
  bloque). El archivo **abre 100%% plegado**: solo las ~28 categorías
  principales visibles; el detalle (subniveles y publicaciones) se abre por
  nivel o por rama.
- **"Sin categoría" siempre al final** en ambas hojas, venda lo que venda.
- **La descarga vive en Análisis → Reportes**: la página (placeholder del
  catálogo de fulfillment de José) estrena su primera tarjeta viva — "Ventas
  por categoría (Excel)" con filtros de cuenta y período relativo/absoluto.
  El botón se retiró de /analisis/categorias; el catálogo pendiente sigue
  debajo intacto.
- Probado en staging (sandbox) antes del pase, por instrucción de Eduardo:
  producción no se tocó hasta su dale. Verificado con Excel real: 28 filas
  visibles al abrir, totales de julio intactos ($5.76M / 16,565 uds), 0
  errores de fórmula, tsc limpio. Versión 0.57.0.

---

### v0.56.0 — Análisis/Categorías: período X→X y Exportar a Excel tipo José (Eduardo)

El reporte vivo de /analisis/categorias aprende lo que le faltaba frente al
xlsx de José: **período absoluto** (dos date-pickers `desde`/`hasta` que mandan
sobre los botones de días; la X vuelve a los relativos) y **Exportar a Excel**
(botón verde) que genera el archivo con los filtros elegidos.

- Backend: `GET /api/fulfillment/categorias[/publicaciones]` aceptan
  `desde`/`hasta` (YYYY-MM-DD, tope 2 años) además de `dias`; el SQL pasa de
  `date > hoy - dias` a `date between desde and hasta` (mismo conteo de días).
  Nuevo `GET /api/fulfillment/categorias/excel` + `services/
  reporte_categorias_xlsx.py` (openpyxl, nueva dependencia): hoja **Resumen**
  por categoría principal (SKUs con venta, uds, ventas $, %% con fórmula,
  publicaciones, activas, TOTAL con SUM) y hoja **Categorias** con el árbol
  completo (subtotales SUBTOTAL(9,…) por nivel) y las publicaciones de cada
  hoja (SKU, tienda, título, MLM ID, situación, uds, $, precio, 1ª/últ. venta).
- Sin columna de margen (acordado 04-ago: para después) y con las
  limitaciones declaradas: venta REAL del período (no sold_quantity×precio del
  snapshot), "días en venta" no existe (va 1ª venta), y un SKU con doble
  clasificación cuenta en ambas ramas (convención de la página).
- Verificado contra kubera: julio 2026 = $5.76M / 16,565 uds / 689 SKUs;
  Excel recalculado con Excel real, 0 errores de fórmula. Versión 0.56.0.

---

### v0.55.0 — F5 Core: flag de lectura de los lookups SKU→wc_id (Eduardo)

Cuarto y último dominio F5. Flag **`SUPABASE_READ_CORE`** (default false;
encendido en producción el 04-ago con dale de Eduardo): `pedidos_ml.resolver_producto` (ruta caliente
de cada venta) y la categoría-ML de `costos.py` leen su lookup SKU→wc_id de
`core.products` vía el nuevo `services/core_read.py` (contador + alerta + chip
"Core" en /migracion). Regla propia del dominio: **None en kubera NO es
concluyente** (el seam Crear→core.products no existe; un SKU del día aparece
hasta el ETL de las 06:15) — se reconsulta MySQL sin alertar; solo la excepción
es fallback.

Alcance deliberado: `ejemplos.py` (usa precio/stock_odoo, no viajan al maestro)
y el respaldo-DB del listado de `woocommerce.py` (lee `productos` CONGELADO;
su primario es wp_db vivo) quedan FUERA — la gemela `buscar_wc_ids` queda lista
en core_read.py para F6.

**Equivalencia con arbitraje** (`scripts/comparar_lecturas_core.py`): 905 SKUs
(634 vendidos 30d + 300 azar): 665 iguales, 0 ausentes en kubera, y 27
diferencias arbitradas contra WordPress VIVO → **kubera correcto en las 27,
MySQL en 0**: son SKUs que se volvieron variación después del congelamiento del
23-jul; MySQL conserva el id viejo (hoy los pedidos de esos SKUs se crean
contra el producto equivocado) y kubera trae la variación real. Encender este
flag además CORRIGE ese defecto. Listado F6: kubera 9,732 ⊇ mysql 5,271.
Veredicto EQUIVALENTE. Versión 0.55.0.

---

### v0.49.0 — F5 Pedidos: flag de lectura del tab Ventas con equivalencia probada (Eduardo)

Tercer dominio que aprende a leer de la BD kubera. Flag **`SUPABASE_READ_ORDERS`**
(default false): las dos consultas del tab Ventas en fuente=pedidos
(`_pedidos_horario` y `_pedidos_rango` de `ventas_ml.py`, sobre `pedidos_ml`)
leen sus filas agregadas de `channel.orders` vía el nuevo
`services/orders_read.py`, con fallback automático a MySQL + contador
kubera-vs-fallback + alerta Slack (mismo patrón que costos/channel; chip
"Pedidos" en /migracion). Traducción clave: `creado` (DATETIME naive UTC) ≡
`creado_at at time zone 'utc'` — el corte de rango y la hora CDMX
(`- interval '6 hours'`) replican `HOUR(DATE_SUB(creado, INTERVAL 6 HOUR))`.
OJO: aquí 0 filas es respuesta VÁLIDA (rango sin ventas) — sin guardia de
plausibilidad por conteo; solo una excepción dispara el fallback.

**Equivalencia** (`scripts/comparar_lecturas_orders.py`): 15 días (14 cerrados
estrictos + hoy) × 5 cuentas × ambas gemelas + rango de 7 días = **0
diferencias, EQUIVALENTE a la primera pasada** (el espejo de channel.orders ya
traía racha de actas en cero). La vista histórica `?fuente=ml`
(ventas_horarias) NO viaja por el flag: es caché de la API de ML, no dominio
migrable. Versión 0.49.0.

---

### v0.48.0 — F5 Channel: flag de lectura de inventario/presencia con equivalencia probada (Eduardo)

Segundo dominio que aprende a leer de la BD kubera. Flag **`SUPABASE_READ_CHANNEL`**
(default false): `inventario.leer_inventario`, la fuente canal_inventario de
`presencia.py` y `GET /api/sync/estado` leen de `channel.listings` con fallback
automático a MySQL + contador kubera-vs-fallback + alerta Slack (mismo patrón
que costos). Nuevo `services/channel_read.py` con la traducción completa
(cuenta↔core.accounts.legacy_code con la convención '' para amazon/general;
alcance ml+amazon — 'general' se unifica en F6).

**Tres hallazgos de modelo que el arnés destapó y se corrigieron:**
1. `channel.listings` no tenía `logistic_type`/`stock_fba`/`currency` que el
   panel usa → migración `0004_channel_cache_cols.sql` + backfill (5,440 filas)
   + el espejo ahora las escribe.
2. El only-if-changed del espejo nunca rellenó `listing_id` en filas estables
   desde la fusión → backfill único + `listing_id` entra al is-distinct-from.
3. Los "fantasmas" del ETL de fusión (filas-identidad todo-NULL sin equivalente
   en canal_inventario) se excluyen de las gemelas; las filas reales sin
   item_id sí viajan. Convención stock: amazon reporta en `stock_fba` y deja
   `stock_full` NULL (se normaliza en la lectura).

**Equivalencia** (`scripts/comparar_lecturas_channel.py`, dominio caliente:
identidad exacta + tolerancia 2% en precio/stock por timing del sync): 539
filas 0 faltantes/sobrantes, identidad 0 diferencias, calientes 0.37%,
presencia 508=508, resumen amazon 1,666=1,666 (BEKURA +1 fila residual en
kubera, 0.05%, anotada). Veredicto EQUIVALENTE. Versión 0.48.0.

### v0.50.2 — La API deja de responderle a cualquiera (Temu III.1 y III.2)

**Por qué.** El cuestionario de seguridad de Temu rechazó dos respuestas, y las
dos eran mentira. Verificado contra producción antes de tocar nada:

```
200  /api/productos          200  /api/fanout/estado
200  /api/migracion/errores  200  /api/canales
```

Cuatro de cuatro sin credencial. Y `routers/auth.py` era un maniquí: devolvía
siempre `{"autenticado": true, "usuario": "kubera", "rol": "admin"}` sin
verificar nada, y ningún endpoint lo usaba. Nunca hubo autenticación.

**Lo que se construyó.**

- **`core/middleware.py`** — puerta única de la API. Aplica la credencial a los
  84 endpoints de una vez, en vez de router por router.
- **`core/identidad.py`** — dos formas de identificarse: `Authorization: Bearer`
  (Supabase Auth, personas) y `X-API-Key` (máquinas). El token se verifica
  contra Supabase y el resultado se cachea 5 min, así que una sesión provoca
  como mucho una llamada de red cada 5 minutos.
- **`core/rbac.py`** — tabla declarativa de 36 reglas `(método, prefijo) → rol
  mínimo`. Un solo archivo legible que sirve como evidencia para el
  cuestionario.
- **`routers/auth.py`** — deja de mentir: refleja la identidad real.
- **`/docs`, `/redoc` y `/openapi.json` cerrados en producción.** Publicaban el
  mapa completo de los 84 endpoints; `DOCS_PUBLICAS=true` los reabre sin deploy.

**`core.usuarios` ya existía y ya tenía el diseño correcto.** No hubo que
inventar nada ni levantar un Supabase aparte: la tabla del equipo de migración
trae `CHECK (rol IN ('admin','operador','lectura'))` —los mismos tres roles— y
`FK id → auth.users(id) ON DELETE CASCADE`. Es decir, **Supabase Auth guarda la
contraseña y `core.usuarios` guarda el rol**; no hay columna de contraseña
porque nunca debió haberla. Ese `ON DELETE CASCADE` es además la respuesta a la
pregunta III.3: al borrar el usuario, su perfil y permisos se van con él.

**El peligro real no era el webhook — era el healthcheck.** `railway.json`
declara `healthcheckPath=/api/health` con `restartPolicyType=ON_FAILURE`: un 401
ahí hace que Railway dé el deploy por muerto y entre en BUCLE DE REINICIO,
tumbando webhook, scheduler (sync de 15 min, `odoo_watch`, fan-out, sondeos de
Amazon y M2E) y panel. Un error en una lista de strings apaga la operación
entera. Blindajes, en orden de ejecución:

1. Las rutas abiertas se evalúan **antes** que `AUTH_ENFORCED`. No existe orden
   en que `/api/health` o el webhook puedan dar 401.
2. `OPTIONS` siempre pasa (preflight de CORS; bloquearlo mata el panel).
3. El handler de `/api/webhooks/ml` envuelve **todo** su cuerpo: cualquier
   excepción responde 200. A ML nunca se le contesta distinto — si lo hiciera,
   reintenta 1 h y después deshabilita el topic, y se dejan de capturar ventas
   reales sin ningún error visible.
4. El middleware **falla abierto**: si revienta, la petición pasa. Un bug en la
   autenticación no puede convertirse en una caída total.
5. `AUTH_RUTAS_ABIERTAS` (CSV) abre una ruta olvidada **sin commit**.
6. `RBAC_ENFORCED` es independiente de `AUTH_ENFORCED`: se puede exigir
   credencial sin aplicar roles todavía.

**Hallazgo que bajó el riesgo**: el scheduler NO llama la API por HTTP (registra
funciones en el mismo proceso) y los crons de Railway abren MySQL/Postgres
directo. La categoría "consumidor interno por HTTP" está prácticamente vacía, así
que exigir token rompe mucho menos de lo que se temía.

**`scripts/humo_auth.py` — 48 pruebas, todas pasando.** Es la que decide si se
puede desplegar. Cubre los dos modos en procesos separados (config lee las
variables al importarse):

```
python -m scripts.humo_auth --observacion   →  9/9   nadie se bloquea
python -m scripts.humo_auth                 → 39/39  el estado final
```

Verifica que `/api/health` y el webhook den 200 sin credencial incluso con el
enforcement encendido, que el webhook aguante cuerpos inválidos, que el
preflight de CORS pase, que el resto dé 401, que una ruta no listada exija
`admin`, y las 13 combinaciones de rol —incluida la que importa para el
*need-to-know*: **un `operador` no puede publicar a marketplaces, ni mover
precios en masa, ni ver la bitácora**.

**Despliegue en dos tiempos.** Sin `API_KEY` definida el middleware es inerte y
todo sigue igual que hoy. Con `API_KEY` + `AUTH_ENFORCED=false` entra en
OBSERVACIÓN: nada se bloquea, solo se registra en logs quién habría recibido 401
— ese censo es lo que hace seguro apretar después. Revertir es cambiar una
variable: 2-4 min, dentro de la ventana de reintentos de ML (1 h), así que no se
pierde ni una venta. Versión 0.50.2.

---

### v0.49.1 — Fix: cada variante muestra SU costo, no el del padre

Reporte de Eduardo (3-ago): "el 24-jul cambiamos los costos de los colchones
CAM-0030-IND y CAM-0030-MAT y no se guardaron". La auditoría descartó pérdida
de datos —los valores reportados no existen en `costos_logs` (204 registros),
`costos_validados`, `costos_finales`, kubera ni en la postmeta de Woo: la
petición nunca llegó al servidor— pero destapó por qué el trabajo *parece*
perderse: **el panel pintaba el costo del padre en las 4 variantes**.

`woocommerce.listar_productos` hacía `v["costo"] = it["costo"]` para toda
variante, con el supuesto explícito de que "todas son la MISMA pieza física
(solo cambia color/talla/cantidad)". Es cierto para color o estampado y
**falso para tallas**: individual, matrimonial y queen son piezas distintas
con costo distinto. CAM-0030-MAT tenía 1,102.50 guardado en Woo y en
`costos_validados`, y la pantalla mostraba 2,674.71 (el del padre). Capturar
un costo por talla era invisible → se leía como "no se guardó".

**Ahora**: `wp_db.precios_y_costo_por_wc_id` también lee la meta `costo` de
cada variación y la devuelve como `costo_variantes` {sku: costo} en el padre.
La variante con costo propio muestra el suyo; la que no lo tiene hereda el del
padre —comportamiento de siempre— pero viaja marcada con `costo_propio: false`
y la tabla la pinta en gris itálico con la etiqueta HEREDADO y un tooltip. Un
costo heredado ya no se puede confundir con uno capturado.

Verificado contra producción con el caso del reporte: IND/EST/QUE $2,674.71
HEREDADO · MAT $1,102.50 propio. Ojo para negocio: IND, EST y QUE **no tienen
costo capturado**, y el de MAT es puro flete (`costo_producto = 0`,
`costo_cbm = 1,102.50`), así que su precio sugerido y su margen son ficticios.
Versión 0.49.1.

---

### v0.49.2 — Fix: las variantes heredan la categoría ML del padre (y el error dice qué falta)

Reporte de Eduardo (3-ago): el Estudio contesta *"No se pudo guardar el costo"*
en los colchones. En los logs, tres intentos con **422** sobre
`POST /api/crear/costos/CAM-0030-IND/recalcular`.

**Causa.** Para guardar un costo hay que calcular el precio, para eso hace falta
la comisión de ML, y la comisión sale de la **categoría** del SKU. `costos.
_resolver_cat_ml` la buscaba en dos lugares (`categorias_ml` y la postmeta de Woo
vía la tabla `productos`) y ninguno tiene a las variantes de este colchón —
`productos` no tiene ni una. Sin categoría no hay comisión, `computar` devuelve
None y el endpoint corta con 422 **antes de escribir nada**. Por eso el padre y
`-MAT` sí guardaban (traen la categoría en su fila de `costos_finales`) y `-EST`
también (está en `categorias_ml`), pero `-IND` y `-QUE` no.

**Arreglo 1 — herencia por la estructura de Woo.** `_resolver_cat_ml` gana un
tercer origen, el mapa `channel.product_category` de la BD kubera (el mismo que
agrupa el panel de Análisis, donde el padre CAM-0030 tiene `MLM121837` con
`source='panel'`, o sea elección humana → regla de la casa 2). Y cuando el SKU
no tiene categoría propia, hereda la del PADRE: las variantes de un producto
viven en la misma categoría de ML. El padre se resuelve con el nuevo
`wp_db.sku_padre()`, por `post_parent` de WooCommerce — no por el nombre del SKU,
que sería una heurística sobre la convención de nombres.

**Arreglo 2 — el error deja de ser mudo.** El backend ya explicaba qué faltaba
(*"…ingresa la Comisión ML (%)"*), pero `postJSON`/`getJSON` lanzaban
`Error("API 422: …")` y los editores hacían `catch {}` con un texto fijo. Ahora
lanzan `ApiError` con el `detail` de FastAPI y los cuatro `catch` de costos lo
pintan vía `mensajeDeError(e, respaldo)`. `Error.message` no cambia, así que
ninguna otra vista se ve afectada.

**Verificado contra producción** (con `/preview`, que no escribe): antes
`CAM-0030-IND` + costo → 422; ahora resuelve `MLM121837` heredada, comisión 17%
y precio sugerido \$3,460.77; `-QUE` igual con \$3,909.67. Y en el navegador, un
SKU sin costo ahora muestra el texto del backend en vez del genérico.
Versión 0.49.2.

---

### v0.49.3 — Fix: la guardia de plausibilidad de CHANNEL dejaba de leer de kubera por lotes legítimamente vacíos

Slack repitiendo *"⚠️ Lectura de CHANNEL cayó a MySQL (inventario): kubera
devolvió 0 filas para el lote (implausible)"*, y el contador del dominio en
**kubera 1 · fallback 5**: el dominio parecía roto sin estarlo.

**Causa.** La guardia de `inventario.leer_inventario` (v0.48.0) trataba
cualquier lote vacío como fallo. Pero vacío NO es implausible en este dominio:
`channel_read` solo mira `mercado_libre` y `amazon`, y de los **22,156**
productos solo **2,027 (9.1%)** tienen publicación viva ahí — los otros
**20,129 devuelven 0 filas de forma legítima**. Peor: dos de los tres
llamadores piden **un solo SKU** (`productos.py` y `canales.py`, las fichas de
producto), así que abrir la ficha de un producto no publicado disparaba alerta,
tiraba la lectura a MySQL… y MySQL tampoco tenía esas filas (el arnés de
equivalencia de v0.48.0 ya probó que las dos fuentes coinciden). El fallback no
corregía nada; solo ensuciaba el testigo que autoriza el corte del dominio.

Las otras dos guardias del mismo commit estaban bien calibradas y no se tocan:
`presencia` no tiene guardia (vacío es normal) y `resumen_por_canal` sí la
tiene, pero sobre un agregado global, donde vacío sí es imposible.

**Arreglo.** Nuevo `channel_read.hay_datos()` — un `select 1 … limit 1` que solo
corre cuando la lectura volvió vacía — y la guardia pasa a preguntar por la
TABLA, no por el lote: `if not out_kb and mysql_enabled and not hay_datos()`.
Conserva intacto lo que la guardia buscaba (kubera perdió el dominio) y elimina
el falso positivo. Un lote legítimamente vacío ahora cuenta como lectura
`kubera`, que es lo correcto: kubera sí respondió.

**Verificado contra producción**: tres lotes seguidos (`JUGU-0083-PLA` y
`OFI-0496-MET` sin publicación, `OFI-0493-AMA` con una en Amazon) → 3 lecturas
kubera, **0 fallbacks, 0 alertas**; y forzando `hay_datos()=False` la guardia
sigue disparando y cayendo a MySQL como antes. Versión 0.49.3.

---

### v0.49.4 — Fix: guardar el costo de una VARIANTE reventaba con 500 (y el número tecleado se perdía en silencio)

Con la categoría ya heredada (v0.49.2), el guardado de los colchones dejó de dar
422 y pasó a dar **500**. La traza de producción del 3-ago 18:55:

```
crear.py:673 costos_recalcular → crear.py:631 _sync_woo_costo → r.raise_for_status()
httpx.HTTPStatusError: 404 Not Found for '…/wc/v3/products/104743'
```

**Bug 1 — la variación no se actualiza por `/products/{id}`.** `104743` es
`CAM-0030-MAT`, una variación. Ese endpoint la deja **LEER** (GET 200) pero
rechaza el PUT con 404; el update va a `/products/{padre}/variations/{id}`. Y
como el 404 subía sin capturar, tumbaba la petición entera **después** de haber
escrito `costos_validados` + `costos_finales`: el usuario veía "no se pudo
guardar" con el dato ya guardado, y reintentaba. Ahora
`obtener_producto_por_sku` trae `parent_id`, `_sync_woo_costo` arma la ruta
según el tipo, y un fallo de Woo ya no es 500: el endpoint responde
`sincronizado_woo:false` + `sync_error`, deja alerta en Slack, y la pantalla
dice *"Costo guardado en la base, pero WooCommerce NO se actualizó: …"* en vez
de mentir en cualquiera de las dos direcciones.

**Bug 2 — el costo tecleado no viajaba.** Las dos bitácoras del caso (24-jul id
182 y 3-ago id 208) llegaron con `overrides: {ml_cat_id, pct_comision}` y **sin
`costo_producto`**, aunque la persona escribió la cifra. La causa es el parseo:

```ts
const numOrNull = (v: string) => (v.trim() ? Number(v) || null : null);
```

`Number("1,625.84")` es `NaN`, y `NaN || null` es `null` → el campo se enviaba
vacío, el backend recalculaba con el costo viejo y la pantalla decía "guardado".
Escribir la cifra como se escribe en español bastaba para perderla. Nuevo
`lib/numeros.ts::aNumero()`, tolerante a separador de millar, símbolo de moneda,
espacios y coma decimal — y que ya no confunde `0` con vacío (`Number("0") ||
null` daba null). Lo usan los dos editores para costo, dims, peso, tipo de
cambio, margen y comisión. 12 casos probados: `1,625.84` · `$1,842.86` ·
`1 625.84` · `1625,84` · `1,625` · `1,5` · `0` · `""` · `abc` → todos correctos.

Versión 0.49.4.

---

### v0.50.0 — Los listings guardan el precio de LISTA: se puede ver el descuento vivo de ML (paso 1 de 2)

`channel.listings` tenía la columna `price_base` desde el ETL original y estaba
**100% vacía** — 0 de 4,044 filas de ML y 0 de 1,776 de Amazon. Nunca se cableó:
su fuente (`canal_inventario` en MySQL) no tiene precio base, y el lector de ML
solo tomaba `item.get("price")`. Resultado: el panel mostraba el precio real
pero no permitía distinguir "este es mi precio" de "ML le está aplicando −75%".

**El nombre engaña, y hay que decirlo.** ML expone tres campos y solo uno sirve:

| Campo de ML | Qué es |
|---|---|
| `price` | Lo que cobra hoy — el real (ya lo teníamos) |
| `original_price` | El precio tachado; NULL si no hay campaña ← **el que sirve** |
| `base_price` | **NO es el precio de lista**: viene igual a `price` (0 de 20 activas difieren) |

**Qué se hizo.** `inventario._precio_lista(item)` devuelve `original_price` y,
cuando no hay promoción, `price` — nunca NULL. Es deliberado: el espejo trata
NULL como "no observado" y conserva el valor anterior, así que una promoción
terminada se quedaría pegada para siempre. Con esto la regla queda limpia:
`price < price_base` ⇒ hay descuento vivo, y la resta dice cuánto. Los tres
puntos de `inventario.py` que arman filas de ML lo incluyen, y
`channel_mirror.espejar_inventario` escribe `price_base` en el upsert y lo suma
al `is distinct from`.

**Sin migraciones ni DDL**: la columna ya existía en Postgres, y `canal_inventario`
no se toca porque su `INSERT` lista columnas explícitamente y la llave extra del
diccionario se ignora sola. Tampoco hay llamadas nuevas a ML: el sync ya pedía
el item completo, así que `original_price` viajaba en la respuesta sin usarse.

**No rompe las actas**: el comparador usa
`CAMPOS = ("precio", "stock_own", "stock_full", "es_full", "situacion", "listing_id")`
— `price_base` no está, así que no puede generar divergencias ni cortar la racha
del dominio channel.

**Verificado contra producción** (refresco real de 3 publicaciones):
`MLM2945686509` 103.96 sobre 415.84 = **−75%**, `MLM2914395541` 299.00 sobre
706.38 = **−58%**, y `MLM3183258785` 7,755.92 = 7,755.92 sin promoción.

**Paso 2 pendiente, a propósito**: la columna todavía NO se pinta en el panel.
Primero se mide un día cuánto crece la escritura del espejo — al entrar
`price_base` al `is distinct from`, cada arranque y fin de promoción cuenta como
cambio y genera fila de historia. Amazon queda fuera de esta vuelta: su precio
de lista viene por otro camino de SP-API, sin verificar. Versión 0.50.0.

---

### v0.50.1 — Fix: recostear el padre ya no borra el precio de las variantes que tienen costo propio

Última pieza del caso de los colchones. Al guardar el costo de un padre variable,
`_sync_woo_costo` replicaba su precio a **todas** sus variantes:

```python
# Se replica el MISMO costo/precio a todas
# (misma pieza física, solo cambia color/talla).
```

Ese supuesto es cierto para color o estampado y **falso para tallas**. La
consecuencia era silenciosa y cara: alguien capturaba el costo de individual y
de matrimonial, quedaban bien… y el siguiente guardado del padre aplanaba las
cuatro al precio del padre. El trabajo se perdía sin ningún aviso.

**Ahora la réplica es solo para las variantes SIN costo propio.** Nuevo
`_tiene_costo_propio(variacion)`, que usa la misma señal que pinta la tabla desde
v0.49.1 —la meta `costo` de la variación en Woo— así que lo que se ve en pantalla
y lo que decide la escritura no pueden divergir. Las variantes respetadas se
registran en el log por SKU, para que la omisión sea auditable y no un silencio.

**Verificado contra producción** re-sincronizando el padre CAM-0030 con su costo
actual (2,674.71 → regular 7,755.92 / oferta 6,514.97, o sea sin cambiar ningún
valor): `-IND` conservó 4,119.96 / 3,460.77 y `-MAT` 7,721.07 / 6,485.70, ambas
con su `date_modified` intacto; `-EST` y `-QUE`, que no tienen costo propio,
siguen heredando el del padre. Partición confirmada sobre el payload real:

```
CONSERVAN su precio (costo propio): CAM-0030-IND, CAM-0030-MAT
reciben el del padre (sin costo)  : CAM-0030-EST, CAM-0030-QUE
```

Con esto cierra el hilo completo: v0.49.1 hizo visible el costo por variante,
v0.49.2 permitió calcularlo, v0.49.4 permitió guardarlo, y v0.50.1 evita que se
pierda. Versión 0.50.1.

### v0.51.0 — Crear Productos: se acabó el limbo (todo termina en Productos)

**El síntoma.** Al crear un producto sin costo el proceso "terminaba" pero el
producto no aparecía en Productos: quedaba en `inprogress` y seguía listado en
Crear como no creado. Pasaba por lotes (~16 y 13 en días seguidos) y cada vez
había que empujarlo a mano con `permitir_sin_costo=true` por la API.

**El mecanismo.** Las vistas reparten por estado: Productos es
`publish/pending/ready` y Crear es `draft/inprogress`. Dejar un producto en
`inprogress` lo volvía **estructuralmente invisible** justo en la pestaña donde
se le captura el costo. Medido en los 7 días previos: de 87 creaciones, **33
cayeron en `inprogress`**, y de 38 errores **34 eran el guard de costo** que
abortaba antes de empezar — unos **67 SKUs atorados por semana**.

**Los cuatro cambios:**

1. **El desenlace siempre es `pending`.** `inprogress` se retira como resultado
   posible del flujo de creación.
2. **La falta de costo ya no aborta.** El guard existía porque sin costo el
   producto acababa en `inprogress`; con el punto 1 esa premisa desaparece.
   Lo que SÍ sigue abortando es un scrape inservible de Alibaba — ahí el riesgo
   real es pisar el nombre/imagen de Odoo con basura.
3. **La completitud lee el precio de las VARIANTES.** Un padre variable no
   guarda `_regular_price` propio, así que se le declaraba "sin precio"
   teniéndolo: de los 85 atorados, **44 eran variables y 41 ya tenían precio en
   sus variantes ($1,364–$14,378)**. Se reusa `wp_db.precio_regular_variantes`,
   la misma corrección que `publicar_ready` ya aplicaba al publicar.
4. **Se retiró la casilla "Crear sin costo"** del panel: con el punto 1 ya no
   cambiaba nada y era un control que no hacía nada. El parámetro
   `permitir_sin_costo` se sigue aceptando en la API (vestigial) para no romper
   llamadas existentes.

**Limpieza del limbo acumulado.** `POST /api/crear/destrabar` pasa a `pending`
los que quedaron en `inprogress`. Son productos YA procesados —85/85 con scrape
e imagen de portada, 84/85 con categoría y descripción— a los que solo les quedó
mal la etiqueta: **no se re-scrapea ni se vuelve a llamar a Apify/IA**. Por
defecto es simulacro; con `aplicar=true` lo hace, por lotes de 50 (2 peticiones
en vez de 85).

**Por qué mover en bloque es seguro:** `inprogress` y `pending` son ambos
"inactivo" para el panel, ningún cron actúa sobre `pending`, publicar es siempre
manual, y `publicar_ready` rechaza con *"Faltan datos: precio"* — un `pending` a
medias no puede colarse a Mercado Libre.

**Lo que este cambio NO resuelve** (diagnosticado, pendiente de decisión):
Apify **sí** trae el costo de Alibaba, pero `_procesar` lo guarda solo como meta
de Woo (`alibaba_price`, `cbm_producto`) y **nunca en `costos_validados`**, que
es de donde `_tiene_costo_base` y `costos.asegurar_finales` leen. Medido: de los
30 SKUs más recientes solo **3 tienen fila en `costos_validados`**, pero **25 de
26 tienen `alibaba_price` en Woo**. El dato llega y se estaciona en la tabla
equivocada. Sembrar `costos_validados` desde el scrape exige topes de sensatez
primero: se detectaron valores inverosímiles (precio $21,245.91, peso 500 kg,
cbm 1.5 m³) que vienen de la heurística `_precio_alibaba_real`. Versión 0.51.0.

### v0.52.0 — El KAM manda en sus pestañas, y el equipo completo cabe a la vez

Dos decisiones de Brandon (4-ago) sobre el panel de roles de v0.50.2, y un
problema de concurrencia que la segunda destapó.

**1. El KAM hace TODO dentro de sus pestañas.** El reparto de v0.50.2 asumía
que un rol no-admin miraba y editaba contenido pero no publicaba ni movía
precios. Brandon lo corrigió: publicar y actualizar **es** el trabajo del KAM.
El corte ya no es *mirar vs escribir* sino **trabajo comercial vs
infraestructura**. Un KAM publica a Mercado Libre y Amazon, recalcula costos
(incluido el masivo) y corre Competencia; lo que no puede es apagar la captura
de ventas, correr backfills de migración, barrer stock de todo el catálogo con
`sync/woo`, empujar inventario con `fanout` ni leer la bitácora. Eso sigue
siendo el *need-to-know* que responde la pregunta III.2 de Temu: un error ahí
no daña un producto, daña el sistema o borra el rastro de quién hizo qué.

**Dos pestañas estaban rotas y nadie lo habría sabido hasta el enforcement.**
Al mapear endpoint por endpoint contra lo que llama cada pestaña aparecieron
dos huecos en la tabla de v0.50.2: **Análisis** (`/api/fulfillment`) estaba
marcada admin, y **Competencia** no estaba listada — o sea caía en el
`ROL_POR_DEFECTO = "admin"`. Un KAM se habría topado con un 403 en dos de sus
seis pestañas el día que se encendiera `RBAC_ENFORCED`. `/api/fulfillment`
queda en `operador` y no en `lectura` porque su consulta devuelve `costo` y
`margen_pct`: es el P&L, no inventario a secas.

**2. Los once conectados al mismo tiempo.** Brandon pidió que aguantara al
equipo completo en simultáneo. La verificación de identidad tenía dos defectos
que con un solo usuario no se ven:

- **Estampida.** Abrir el panel dispara ~8 llamadas en paralelo. Sin candado,
  las 8 verificaban el MISMO token contra Supabase y consultaban 8 veces
  `core.usuarios`. Once personas = ~88 verificaciones simultáneas contra el
  pool de 6 conexiones de `supabase_db`, que además es `blocking=True`.
- **Event loop congelado.** `core.usuarios` se lee con psycopg2, que es
  SÍNCRONO, y se llamaba directo desde una corrutina. Mientras esa consulta
  corría, el servidor entero dejaba de atender: los demás usuarios, el webhook
  de ML **y el healthcheck de Railway** — que con `restartPolicyType=ON_FAILURE`
  reinicia el deploy si no responde.

La cura es la misma que ya usa `pedidos_ml` contra las ráfagas de webhooks: un
**candado por token** (el primero verifica, los demás esperan y leen el caché
tibio) y la consulta a la base movida a un hilo con `asyncio.to_thread`. Se
agregó además caché negativo de 15 s para que una sesión vencida no golpee
Supabase en cada petición.

**Medido con `scripts/humo_concurrencia.py`** (21 pruebas), que reproduce las
88 peticiones simultáneas con dobles que cuentan llamadas. La misma prueba
corrida contra el código anterior:

| | Antes | Ahora |
|---|---|---|
| Verificaciones contra Supabase | 88 | **11** |
| Consultas a `core.usuarios` | 88 | **11** |
| Tiempo hasta que entra el equipo | 22.12 s | **0.32 s** |
| Latidos del servidor mientras tanto | 5 | **21** |

**Navbar:** deja de decir "Kubera / admin" fijo — muestra el correo real y su
rol ("Admin" / "KAM"), y por fin hay botón **Salir**. Con una contraseña
compartida entre once personas, ver con qué cuenta se entró es la única forma
de notar el error antes de mover algo.

`humo_auth.py` pasó de 39 a **58 pruebas** (casos de rol uno por pestaña, más
la comprobación de que un método no listado —DELETE, PUT— también nace cerrado).
Nada de esto enciende un flujo: `AUTH_ENFORCED` y `RBAC_ENFORCED` siguen en
observación. Versión 0.52.0.

### v0.52.1 — Fix: el alta de usuarios guardaba la cuenta pero NO el rol (y no avisaba)

Al dar de alta al equipo (4-ago) las 11 cuentas se crearon en Supabase Auth,
pero **los 11 perfiles fallaron**: `core.usuarios` quedó en 0 filas.

**La causa.** `crear_usuarios.py` escribía el perfil por la API REST, y
**PostgREST solo expone `public` y `graphql_public`** — cualquier escritura a
`core.usuarios` responde `PGRST106`. El script imprimía el error en una línea
recortada a 110 caracteres por usuario, entre once líneas de "creado": se veía
como ruido, no como un fallo. La cura correcta NO es exponer `core` a la API
pública (sería abrirle el esquema del equipo de migración a cualquiera): el
perfil ahora se escribe por conexión DIRECTA a Postgres (`KUBERA_DB_URL` o
`SUPABASE_DB_URL`).

**Y si no hay cadena de conexión, no se inventa nada:** imprime el `INSERT ...
ON CONFLICT` listo para pegar en el editor SQL de Supabase, con las comillas
simples de los nombres ya escapadas.

**Por qué no era grave, y por qué igual había que arreglarlo:** un usuario sin
fila en `core.usuarios` no queda suelto — `identidad._perfil_en_kubera` le da
el rol MÍNIMO (`lectura`), nunca admin. O sea el equipo entraría, pero sin
poder hacer su trabajo, y sin ninguna señal de por qué. Versión 0.52.1.

### v0.52.2 — QA previo al enforcement: el modo observación nunca observó, y un 401 esperaba a los admins

Antes de encender la autenticación, dos hallazgos que habrían salido en
producción y con gente adentro.

**1. El censo llevaba semanas vacío.** El plan era "leer el log de observación
para saber a quién romperíamos antes de encender". Ese log **nunca se escribió**:
`core/middleware.py` corta en la línea 126 —

```python
if not settings.api_key and not quien.autenticado:
    return await call_next(request)   # ← retorna ANTES de registrar nada
```

— y en Railway **`API_KEY` no existe**, igual que `AUTH_ENFORCED` y
`RBAC_ENFORCED`. Consecuencia práctica: encender solo `AUTH_ENFORCED` no haría
nada, y encender solo `RBAC_ENFORCED` sí aplicaría, restringiendo únicamente a
quien inicia sesión mientras el que no inicia conserva acceso total. El orden
correcto es **`API_KEY` → `AUTH_ENFORCED` → `RBAC_ENFORCED`**, y el primer paso
no bloquea a nadie: solo enciende el censo.

**2. Un 401 esperaba a los admins con sesión.** `core/seguridad.py::
requiere_api_key` nació cuando la única credencial era `X-API-Key` y solo miraba
ese header. Protege 9 endpoints, y entre ellos está
`POST /api/migracion/errores/resolver` — que es un **botón** de la página
/migracion. Con `AUTH_ENFORCED=true`, un admin que hubiera iniciado sesión
correctamente recibía 401 ahí: la puerta principal lo dejaba pasar y la
dependencia interna lo rebotaba.

Ahora pasa quien cumpla una de dos: manda la `X-API-Key` correcta (crons y
scripts) **o** trae sesión válida y su rol alcanza según `core/rbac.py`. Se
consulta `rbac.permite` en vez de confiar en `RBAC_ENFORCED`, porque esos 9
endpoints ya estaban protegidos antes de este rollout y no pueden quedar más
flojos durante la ventana en que el RBAC sigue en observación.

`humo_auth.py` pasó de 58 a **63 pruebas**: admin con sesión pasa, KAM con
sesión NO, anónimo NO, máquina con llave sí, llave equivocada no. Versión 0.52.2.

---

### v0.53.0 — SYNC_DESDE_ML (apagado): el sondeo puede leer el catálogo VIVO de ML, no la bitácora del publicador

**Fase A de la propuesta del 4-ago (Eduardo). Se despliega APAGADA** — con la
bandera en false el lote se arma de `ml_progress` exactamente como siempre
(verificado: mismo lote, mismos SKUs). Encenderla es flujo vivo → dale de
Brandon (regla 3).

**El problema medido.** El panel no sabe qué está publicado en ML; sabe qué
publicó él. Tanto el sondeo como el webhook resuelven la identidad contra
`ml_progress`, así que lo publicado/republicado por fuera es invisible:

- **517 publicaciones vivas** que el sondeo no recorre (186 activas VENDIENDO);
- **253 muertas** (ML ya las borró) que el sondeo sigue refrescando — y como el
  panel guarda UNA fila por (sku, canal, cuenta), el cadáver pisa la fila de la
  publicación real cada ciclo. Es el síntoma "aparece pausado pero está activo"
  (caso testigo `CUNA-0011-AZL`: la vieja `inactive/deleted` en ml_progress, la
  real activa con 25 pzas en FULL y 134 vendidas, invisible);
- **313 SKUs con venta en 30d sin publicación activa visible** ($3.08M), de los
  cuales estas huérfanas explican 91;
- **2,066 avisos de webhooks descartados en 3 días** ("item_id no está en
  ml_progress") — 1 de cada 3 avisos de items.

**Qué hace la bandera.** `sincronizar_ml` arma el lote de
`/users/{uid}/items/search` (active+paused, paginación scan, universo cacheado
30 min) con la MISMA rotación de siempre (lo nunca visto primero, luego lo más
rancio). El SKU se resuelve del PROPIO item — nuevo `_sku_de_item()`:
`seller_custom_field` → atributo `SELLER_SKU` → variaciones, con `ml_progress`
de respaldo; sin SKU legible NO se escribe (se registra en el log). Las muertas
simplemente ya no aparecen en el universo → dejan de pisar filas.

**Verificado en seco contra ML real** (con `_upsert` interceptado, cero
escrituras): el lote nuevo toma primero exactamente las huérfanas conocidas
(TEC-0551-PLU, OFI-0076-NEG, MASC-0044-NEG…), resuelve su SKU del atributo y
captura hasta el precio de lista (CAM-0005-NEG $1,350 sobre $3,000). Con la
bandera apagada, lote idéntico al histórico.

**Convivencia con la migración (pedido explícito de Eduardo):**
- La fila que produce el modo nuevo es LA MISMA (ni una llave más): el espejo
  `channel_mirror.espejar_inventario` no se tocó y el dual-write escribe ambos
  lados en la misma pasada → las actas no ven diferencia (su comparador tampoco
  incluye campos nuevos).
- De los 570 SKUs que entrarían, **554 ya existen en core.products**; los 16
  restantes (MUN-*/JUGU-*, publicados por fuera, nunca conocidos por el
  maestro) entrarían como `draft/backend-dualwrite` — la costura diseñada para
  identidades nuevas. Cero cambios de esquema, cero migraciones.
- Los flags F5 de lectura (`SUPABASE_READ_*`) no se tocan.

**Modo reporte** (paso 2 del plan): `backend/scripts/reporte_sync_desde_ml.py`
— solo lectura, lista qué entra (con SKU resuelto), qué sale (muertas) y qué se
omitiría. Corrida del 4-ago: entran 770 (517 vivas nuevas + 253 posiciones de
muertas), salen 253, 10 sin SKU legible.

**Plan de encendido**: bandera on con `SYNC_BATCH` reducido las primeras rondas
(las ~138 filas nuevas entran repartidas) → 48 h vigilando actas del dominio
channel → verificación: CUNA-0011-AZL muestra su publicación real y los 313
bajan. Reversión: `SYNC_DESDE_ML=false`, sin deploy. **Nota para la transición
a webhooks**: NO apagar el sondeo sin esta bandera encendida — el webhook
descarta lo que no está en ml_progress y sin barrido ese punto ciego se vuelve
invisible (la fase B, resolver el SKU también en el webhook, viene después de
estabilizar esta). Versión 0.53.0.

---

### v0.54.0 — Capturar el costo desde Producto (y que el panel no mienta 15 min)

Cierra lo que v0.51.0 dejó abierto. Ese cambio logró que los productos llegaran
a Productos sin costo, pero **al llegar el campo Costo estaba bloqueado**: había
que bajar al bloque COSTOS o irse a la vista de Costos. Tres arreglos:

**1. El campo "Costo" del Estudio se edita y se guarda.** Va por el mismo
escritor sancionado (`costos.recalcular` → `costos_validados` + `costos_finales`
+ Woo). Lo capturado ahí es el costo **TOTAL**: se guarda tal cual, sin sumarle
un flete derivado de las dimensiones, para que el número guardado sea
exactamente el tecleado. Para desglosar producto vs flete sigue estando el
bloque COSTOS (costo USD + dims). Override nuevo `costo_unitario`.

**2. Capturar costo ya NO exige categoría ML.** Antes, un producto sin categoría
no tenía comisión, `calcular_pricing` devolvía `None` y **se perdía todo** — ni
el costo se guardaba. Y como la regla de la casa es no inventar porcentajes
("NADA de porcentaje fijo inventado"), la salida no era un fallback: ahora
`recalcular` distingue los dos motivos de fallo. Sin costo base no hay nada que
guardar; **sin comisión, el costo SÍ se registra** y la respuesta avisa
*"Costo guardado. NO se calculó el precio: el producto no tiene categoría ML
asignada"*. Separa capturar el costo de derivar el precio, que estaban soldados.

**3. El cambio de estado se refleja al instante.** El precio y el costo de las
listas ya se leían frescos de MySQL, pero el ÍNDICE del catálogo vive en caché
con TTL de 15 min y **nada lo invalidaba**: un producto que cambiaba de estado
tardaba hasta un cuarto de hora en aparecer en su pestaña (le pasó al destrabado
de los 85). `woocommerce.actualizar_estado_en_cache()` parchea la fila —no
invalida el índice, porque reconstruirlo por la vía API son ~90 requests contra
un hosting que bloquea por volumen— y se llama al crear y al destrabar.

**Correcciones al diagnóstico de v0.51.0** (señaladas por Lalo, verificadas):

- El guard de costo **no dejaba el producto en `draft`**: lo dejaba en el estado
  que ya tenía, y de los 34 bloqueados **25 ya habían sido creados antes**, o sea
  estaban en `inprogress`. Eso revela que las dos fallas no eran independientes
  sino un **círculo**: `inprogress` → se ve en Crear → se reintenta → el guard lo
  rechaza → sigue en `inprogress`. Los cambios de v0.51.0 lo cortan en dos
  puntos. (Y el "~67 atorados/semana" de esa entrada sobreestima: los conjuntos
  se traslapan.)
- **`alibaba_price` NO es fuente de costo.** Nadie lo lee para costear: solo se
  muestra en el Estudio y está en la lista de EXCLUIDOS de `ml_atributos`. Es
  traza. La afirmación de v0.51.0 de que "Apify guarda el costo en la tabla
  equivocada" estaba mal planteada — ese precio nunca fue el costo.
- Por lo mismo, **`_precio_alibaba_real()` no contamina el costeo**: sus valores
  raros son ruido de visualización, no llegan a `costos_validados`. Se retira esa
  advertencia de riesgo.

Verificado: 5 casos de prueba del override y del guardado sin precio (lo tecleado
manda, el flete no se re-suma, valores inválidos se ignoran, el costo persiste
con aviso, y sin costo sigue devolviendo `None`). Versión 0.54.0.

---


### v0.59.1 — Barrido de cierre: una publicación borrada en ML ya no deja su fila congelada

Complemento de SYNC_DESDE_ML (v0.53.0, encendido el 4-ago con dale de Eduardo).
El universo consulta solo `active+paused`, así que cuando ML borra o cierra una
publicación ésta SALE del universo y nadie volvería a preguntarle su estado: la
fila quedaría congelada en el último valor visto — irónicamente, el sistema
viejo sí la habría marcado muerta porque releía ml_progress sin filtrar.

Ahora `_lote_desde_ml` detecta las filas que el panel cree vivas cuyo item ya
no aparece en el catálogo y las suma al lote (tope 15 por ronda y cuenta): el
detalle SÍ responde con el estado final (p. ej. `inactive/deleted`, caso
CUNA-0011-AZL) y al escribirse dejan de cumplir el filtro — el barrido se
auto-termina. Nunca frena la ronda normal (try/except alrededor).

Verificado en seco simulando una fila congelada con el cadáver real de CUNA
(`MLM2883075833`): el barrido lo tomó, leyó su estado en ML y produjo la fila
`inactive` con SKU resuelto, sin escribir nada en la prueba. Backlog inicial en
producción: 0 (la incorporación del 4-ago ya lo había limpiado) — la pieza es
preventiva: con ella el estado activas/pausadas del panel pasa de "100%% exacto
hoy" (auditoría de 150 publicaciones contra ML en vivo: 150/150) a 100%%
sostenido. Auditable en el log: "barrido de cierre <cuenta>: N fila(s)…".
Versión 0.59.1.

### v0.60.0 — La sesión ya se renueva sola, y el panel es exclusivo de quien esté dado de alta

Las dos piezas que faltaban para poder encender el enforcement sin romperle el
día a nadie.

**1. El token moría a la hora y nadie lo renovaba.** `lib/sesion.ts` guardaba el
`refresh_token` desde el día uno y **nunca lo usaba**. El token de acceso de
Supabase dura 1 hora: quien entrara a las 9:00 habría visto el panel dejar de
responder a las 10:00, sin mensaje que lo explicara. Ahora hay dos redes:

- Un **temporizador** que renueva 5 minutos antes de vencer (el caso normal).
- Un **reintento ante un 401** en `lib/api.ts`: se renueva y se repite la
  llamada UNA vez. Cubre lo que el temporizador no puede — la laptop durmió, el
  navegador congeló la pestaña, el equipo ahorró batería.

Y un candado `enVuelo` en la renovación, por la misma razón que el del backend:
al despertar la laptop **todas** las peticiones pendientes fallan a la vez y
pedirían renovar en paralelo. Supabase **rota** el token de refresco, así que la
primera renovación invalida el de las demás — sin candado, cerraría la sesión de
alguien que sí la tenía. También se re-verifica al volver el foco a la pestaña.

Si la renovación falla por red, **no** se cierra sesión (se reintenta luego);
solo un 400/401 del propio Supabase —refresco caducado o revocado— cierra.

**2. El acceso es exclusivo de los dados de alta.** Antes, quien se autenticaba
bien pero no tenía fila en `core.usuarios` entraba con rol `lectura`. Con Google
encendido eso deja de ser aceptable: **cualquier cuenta del dominio** —un
empleado nuevo, una cuenta de servicio— pasaría el filtro de Google y vería el
panel. Ahora se rechaza.

Con la distinción que hace que esto no sea peligroso: `_perfil_en_kubera`
devuelve un tercer valor que separa **"la base contestó y no está en la lista"**
(rechazar) de **"no se pudo consultar la base"** (NO rechazar, degradar al rol
mínimo). Confundirlos costaría caro en las dos direcciones: por un lado dejaría
entrar a cualquiera con correo de la empresa, por el otro convertiría un hipo de
Supabase en una caída total del panel.

Google prueba **quién** eres; que además te toque entrar lo decide
`core.usuarios`. Dar de alta a alguien nuevo es agregar su fila.

`humo_concurrencia.py` pasó de 21 a **27 pruebas** (dado de alta entra con su
rol, sin fila rechazado, dado de baja rechazado, base caída no bloquea pero
degrada). `humo_auth.py` sigue en 63. Versión 0.60.0.

### v0.61.0 — "Continuar con Google": entrar sin teclear contraseña

El correo de Kubera está en **Google Workspace** (verificado: el MX de
`kubera.mx` apunta a `smtp.google.com` y el SPF incluye `_spf.google.com`), así
que cada dirección `@kubera.mx` **ya es** una cuenta de Google. No es "entrar con
un Gmail personal": es el correo de trabajo, el mismo que está en
`core.usuarios`.

**Lo que se gana no es comodidad, es control.** Se acaban las 11 contraseñas que
había que repartir por canal privado, y dar de baja a alguien pasa a ser un solo
movimiento: se cierra su cuenta de Workspace y queda fuera del panel. Si mañana
activan verificación en dos pasos en Workspace, el panel la hereda gratis.

**Quién decide qué.** Google prueba **quién** eres; que además te toque entrar lo
decide `core.usuarios` en el backend (v0.60.0). Un correo del dominio que no esté
dado de alta se rechaza — el frontend no es autoridad de nada.

**El token viene en el fragmento de la URL** (`#access_token=…`) y se BORRA de la
barra apenas se recoge. No es cosmética: el fragmento queda en el historial del
navegador y se lo lleva cualquier captura de pantalla; un token ahí es una sesión
prestada a quien tome la laptop.

El botón va **arriba** del formulario de correo, que se conserva como respaldo.
Si Google devuelve `access_denied` (la persona canceló) se dice tal cual, en vez
de un error genérico.

Un detalle de honestidad en el mensaje de error: cuando el backend no confirma el
acceso, `quienSoy()` devuelve lo mismo si el correo no está dado de alta que si
falló la red, y desde el navegador no se puede distinguir. Por eso el mensaje NO
afirma la causa — decirle "no tienes acceso" a alguien que sí lo tiene manda a
soporte a buscar donde no es.

**Configuración necesaria fuera del código:** proveedor Google habilitado en
Supabase (proyecto `tukwcvsitthplhswsblt`), consent screen de Google Cloud en
**Interno**, y el dominio del panel en Authentication → **URL Configuration** —
sin eso Supabase se niega a devolver al usuario después de Google. Versión 0.61.0.

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

### v0.62.0 — Walmart MX: el lote del 4-ago no había publicado NADA, y ya sabemos por qué

El forense de los **88 feeds** de la cuenta arrojó un número incómodo: **cero
items exitosos**. Ni el 31-jul ni el 4-ago. `GET /v3/items` daba 404 no porque
Walmart tardara en publicar, sino porque nada pasó jamás la validación.

**El "9 feeds sin fallos" era un bug nuestro.** `publicar()` sondeaba 8 ciclos de
14 s y, si Walmart seguía procesando, devolvía `INPROGRESS` — que el resumen
contaba como aceptado. Ahora `INPROGRESS` significa "sin veredicto" y el estado
real se consulta en una pasada aparte, al final de la corrida.

**La causa raíz nº1 era el TIEMPO, no el formato.** Ocho rechazos decían *"We
couldn't download the image, because the URL isn't in the correct format"*. Pero
las URLs son ASCII puro, terminan en `.jpg`, van por HTTPS y responden 200 con
ocho User-Agents distintos (curl, Java, Apache-HttpClient, bot, Chrome), con HEAD
y con Range. Lo que las mataba es otra cosa: las imágenes de `MASC-0033` se
subieron a WordPress a las **16:25:26 UTC** y el feed salió a las **16:25:28** —
dos segundos después, cuando el CDN de Hostinger todavía no las servía. Es la
regla de la casa nº5 (LiteSpeed cachea chunche.shop) pegando desde el otro lado.

El publicador ahora va en **cuatro fases**: preparar todas las imágenes, esperar
la propagación, **revalidar cada URL contra el servidor público**, y recién
entonces publicar — en tandas de 8 con descanso, porque la cuota es el cuello de
botella real y el 4-ago se comió 11 disfraces que ni alcanzaron a salir.

**El esquema oficial es público y nadie lo sabía.**
`https://developer.walmart.com/file/mp/mx/MX_MP_ITEM_INTL_SPEC.json` — 3.9 MB,
HTTP 200 sin credenciales. Trae los 75 `subCategory`, los 75 grupos `Visible` con
su etiqueta en español y la lista `required` de cada uno. Ahí se confirmó que
`mart`, `locale` y `sellingChannel` son enums de **un solo valor**: `WALMART_MX`
y `es_MX` nunca iban a funcionar. Antes de sondear con feeds de prueba, leer ese
archivo.

**Segunda categoría abierta**: `home_other` con clave `Visible` "Cocina,
Decoración y Otros", exención folio **15751007**. Y con ella una trampa nueva: el
esquema publicado es de la versión **3.19** y nosotros mandamos **3.11**, así que
sus `required` NO son los mismos. La categoría de cocina rechazó tres artículos
pidiendo `Talla` y `Género`, que el esquema dice que esa categoría no pide. Se
agregaron y pasaron. Corolario útil: si Walmart se queja de solo dos o tres
atributos, la categoría está bien; el síntoma de categoría equivocada es una
lluvia de atributos absurdos.

**La clave del SAT estaba mal.** `53102700` es "Uniformes", no ropa genérica.
Disfraces usa ahora `60141401` "Disfraces o accesorios", verificada contra el
catálogo oficial del SAT (52,513 claves). Cada categoría lleva la suya.

Se agregó `--categoria`, `--skus` y `--espera` al publicador, y una lista
`EXCLUIDOS` que documenta en el código por qué un SKU no se manda, para no
volver a gastarle cuota ni a redescubrir el motivo.

### v0.64.0 — TikTok Shop deja de depender de M2E: vía propia de autorización

TikTok es el único canal del omnicanal que hoy no está en nuestras manos: la
conexión de M2E quedó en `is_valid=false` en julio y nunca se re-autorizó, así
que ni catálogo ni pedidos fluyen. Con la app de ISV que Brandon dio de alta en
el **Partner Center** (`partner.tiktokshop.com` → App & Service, *Enable API*),
el canal pasa a tener su propia puerta.

Lo que exige ese alta es un **Redirect URL**: la dirección a la que TikTok
devuelve al seller con el `authorization code` cuando aprueba la app. Se
registró la nuestra:

```
https://backendomnicanal-production.up.railway.app/api/tiktok/callback
```

**No sirve reusar la de Google.** Es otro flujo OAuth: el handler del login del
panel valida contra Google su propio `state` y `code`, y un `code` de TikTok ahí
solo consigue ensuciar el único mecanismo de autenticación que ya corre en
producción con `AUTH_ENFORCED` encendido.

Tres piezas nuevas:

| Endpoint | Qué hace |
|---|---|
| `GET /api/tiktok/autorizar` | Redirige al seller a la pantalla de consentimiento con un `state` fresco |
| `GET /api/tiktok/callback` | Recibe el `code`, lo canjea por tokens y los guarda. **Público** |
| `GET /api/tiktok/estado` | Diagnóstico: si hay conexión viva y hasta cuándo, sin exponer el token |

El callback va en `RUTAS_ABIERTAS` del middleware por la misma razón que el
webhook de ML: **TikTok no puede mandar nuestra `X-API-Key`**. Lo que lo protege
no es la credencial sino un **`state` firmado con HMAC y TTL de 15 min**, emitido
por nosotros y verificado a la vuelta; un `code` sin `state` válido se rechaza
antes de tocar la red.

Los tokens (`access` ~7 días, `refresh` ~1 año) se guardan **cifrados con Fernet
en `tiktok_tokens`**, reusando la `DB_ENCRYPTION_KEY` que ya cifra `ml_tokens` —
sin inventar un esquema nuevo de secretos. Si esa llave falta, el módulo **se
niega a guardar**: mejor quedarse sin token que tenerlo en claro.

Se aceptan `code` y `auth_code` como nombre del parámetro, porque el Partner
Center ha usado ambos según la versión del flujo.

**Nace APAGADO** (`TIKTOK_ENABLED=false`): con el interruptor abajo los endpoints
responden 503 y no se llama a TikTok. Encenderlo es cambio de flujo vivo y
espera el dale de Brandon (regla 3). Variables nuevas: `TIKTOK_ENABLED`,
`TIKTOK_APP_KEY`, `TIKTOK_APP_SECRET`, `TIKTOK_SERVICE_ID`,
`TIKTOK_REDIRECT_URI`.

Pendiente para cuando se encienda: el Redirect URL es **editable después** del
alta ("You may edit it after submission"), pero al autorizar tiene que coincidir
carácter por carácter con el que mandemos — una diagonal final de más y TikTok
rechaza el canje.

### v0.63.0 — Walmart MX por LOTE: 40 productos dejaron de ser 40 llamadas

Se estaba mandando **un artículo por feed**. Y `MPItem` es un **array** que
admite **10,000 artículos / 10 MB** por feed. Ese detalle, que estaba en la
documentación desde el principio, es el que explica todos los cortes por cuota:
`REQUEST_THRESHOLD_VIOLATED` **cuenta llamadas, no artículos**.

El 4-ago la cuota se comió 11 disfraces que nunca salieron. El 5-ago, con tandas
de 8 y 15 s entre productos, se volvió a comer 6 en el artículo número 13. No era
cuestión de esperar más entre envíos: era mandar de a uno.

**Medido**: cada artículo pesa ~2.6 KB ya serializado, así que en un feed de
9 MB caben **~3,500 artículos**. Los 12 pendientes de "Cocina, Decoración y
Otros" salieron en **1 llamada de 31 KB**, y los 6 disfraces que la cuota había
rechazado, en otra. Dieciocho envíos convertidos en dos.

**No se pierde granularidad.** Walmart valida artículo por artículo aunque vayan
cientos juntos: un dato malo en uno NO tumba a los demás.
`GET /v3/feeds/{feedId}?includeDetails=true` devuelve el estado y el error
**por SKU**, así que la fase 4 ahora consulta el feed y va marcando cuáles
pasaron y cuáles no, en rondas, hasta que Walmart resuelve o se acaban las
rondas. Al final imprime los `feedId` de la corrida para poder volver a
consultarlos después sin adivinar.

`_armar()` se partió en dos: `_item()` arma una entrada de `MPItem` y `_sobre()`
arma el envoltorio con todas adentro. Flags nuevos: `--lote` (artículos por
feed, 200 por omisión) y `--rondas` (cuántas veces preguntar el veredicto).

### v0.67.0 — La caja de búsqueda también traduce variante → padre (Eduardo)

Cierre del hueco que dejó la v0.59.0. Ahí se conectó `expandir_con_padres()` al
parámetro `skus` ("Filtrar SKUs") pero **no** al parámetro `search` (la caja
"SKU o nombre…"), así que el mismo SKU encontraba o no encontraba según en qué
recuadro lo escribieras.

Lo reportó Eduardo con `JUGU-1179-NEG`: le puso su link de Alibaba, le dio
crear, y "no me sale en productos y tampoco ya me sale en crear". Eran dos cosas
a la vez, y la creación **no** había fallado:

- `JUGU-1179-NEG` es una `product_variation` (padre `JUGU-1179`, id 104668).
  Ninguna vista lista variantes como renglón propio — salen dentro del padre.
- La creación funcionó: el padre pasó a `pending` (comportamiento nuevo desde
  la v0.51.0), o sea que **salió de Crear y llegó a Productos**. Por eso "ya no
  sale en crear": se graduó.
- Y no aparecía en Productos porque lo buscó en la caja de búsqueda, que se
  había quedado sin la traducción. Verificado: `search='JUGU-1179-NEG'` → 0
  resultados; con el arreglo → 1 (`JUGU-1179`, pending).

Aplicado en las dos vistas: `_buscar_wc_ids_wp` (Productos/Omnicanal) y
`listar_candidatos_agrupados` (Crear). Para no pagar una consulta de más en cada
tecleo, `expandir_con_padres` ahora solo busca padre de términos **sin espacios**
— el texto libre ("disfraz de bruja") ni siquiera dispara la consulta.

Verificado contra MySQL de producción: `JUGU-1179-NEG` 0 → 1;
`ACC-0069-ROS-2XL`, `ROP-0505-VER-110CM` y `ROP-0374-NEG-ROJ-2XL` 0 → 1 cada
uno; texto libre intacto y sin consulta extra (3 y 13 resultados, los de antes).
Versión 0.67.0.

---

### v0.65.0 — Seam de ciclo de vida core.products: publish y papelera EN VIVO, ML y Amazon (Eduardo)

El nacimiento (Crear → core.products, v0.24) dejaba al maestro kubera ciego a
lo que pasa DESPUÉS: publicar y borrar llegaban hasta el ETL de las 06:15.
Con el dale de Eduardo (canalizando a Brandon):

1. **Publicar → publish en vivo**: `_marcar_publicado_en_woo` ahora también
   espeja el cambio a core.products (sku del PADRE para variaciones —
   `sku_padre` en el SELECT — para no casar sku de variación con wc_id del
   padre). Y el flujo de **Amazon** por fin la llama: cerraba un hueco de la
   regla de Brandon (29-jul) — publicado solo en Amazon dejaba Woo en
   draft/inprogress. Temu/TikTok no aplican (sin flujo de publicar).
2. **Papelera/eliminados**: la auditoría de Crear marca `trash`/`deleted` en
   el maestro al confirmarlos en Woo (la fila no se borra). Con candado
   `solo_por_wc_id` (revisión de Eduardo): los marcados destructivos solo
   escriben si el acta aún apunta a ese wc_id — un SKU reciclado fuera de
   Crear (fila viva con wc_id nuevo) queda intacto. Complementa el max_id
   por SKU de la auditoría y el update-por-wc_id del upsert (ROBB-0004).

Mismas garantías del espejo kubera: best-effort, cola + reproceso en
/migracion, jamás bloquea publicar. Probado en sandbox (update sin pisar
nombre, insert de respaldo, papelera, idempotencia, reciclado protegido).
Con esto el corte F6 de Core queda a expensas solo de su racha de actas.
Versión 0.65.0.

---

### v0.64.0 — El margen REAL: lo que queda después de que Mercado Libre cobra

Hasta ahora el panel calculaba el margen contra el costo del producto y ya. Ese
número es de CATÁLOGO: no descuenta la comisión ni el envío, que es justo lo que
convierte una venta rentable en una que no lo es. `TEC-0492-MUL` aparentaba
**10.6%** y en realidad **pierde $92.96 por pieza** — con 230 unidades vendidas
en 60 días.

**Costo Base** = producto + flete de importación.
**Costo Final** = Base + comisión REAL de la venta + envío estimado.
**Margen** = (precio − Costo Final) ÷ precio, sobre el precio REALIZADO.

La comisión NO es una tasa supuesta: sale de `channel.order_items.comision`,
promediada por unidad, así que ya trae la de CADA canal. Solo entran líneas con
comisión > 0 — Amazon la registra en cero hasta tener Finances API, y
promediarla abarataría el costo; esos SKUs quedan en "—" en vez de mentir.

**Dónde se ve**: columna *Margen neto* en la tabla de Análisis (ordenable;
ascendente es el filtro de lo que vende mal), la ventana de precio/margen por
canal — donde se ve que el MISMO producto deja distinto según dónde se venda
(`TEC-0552-NEG`: 42.5% en BEKURA contra 35.4% en SANCOR) — y una columna
*Margen* en el árbol de Categorías, hasta la publicación individual.

**El denominador no es la venta total** sino la venta con costo capturado, y el
costo solo se acumula en las ramas que lo tienen. Dividir la ganancia de media
categoría entre la venta entera la haría verse peor de lo que es. Cuando la
cobertura no es total el número va en gris con asterisco y el tooltip declara
sobre cuánto se midió ("sobre $360,554 de $413,041, 87% de la venta").

#### Un solo reporte, y sin contradecirse

Las dos tarjetas de Reportes eran el mismo período con dos rangos de fecha y dos
botones. Ahora es **un Excel de tres hojas**: *Resumen* por categoría, el *árbol*
con sus publicaciones y *Ventas*, una fila por línea vendida (sustituye al CSV
suelto, que se retira con su endpoint).

**Fuente única: los PEDIDOS.** El margen solo puede salir de ahí, y mezclar
fuentes haría que una columna dijera una venta y la de al lado calculara margen
sobre otra. Al fusionarlas aparecieron dos contradicciones reales, que separadas
nadie habría notado: la hoja de detalle no filtraba cancelados ni exigía SKU
($2.32M contra $2.04M del resumen), y al agregar por categoría el bloque de
margen absorbía la comisión de SKUs sin costo capturado ($15.7k y $83.3k de
desfase). Corregidas, el libro cuadra al centavo — los 4 centavos que quedan son
redondeo por línea contra redondeo por SKU.

**La página de Categorías migró a la misma fuente**, porque leía
`sales_daily_completa` mientras su propio Excel leía los pedidos. No se
duplicó la consulta: se convergió a una sola familia `_SQL_CAT_*` donde el
filtro de categoría es un parámetro, así que el mismo query sirve al desglose de
una rama y al libro completo. Efecto declarado: esa vista deja de ver el
histórico rescatado de dailytrack (en la ventana de prueba, de $4.02M a $2.04M).
No se perdieron ventas — se dejó de mezclar dos universos. Para el histórico
completo sigue estando Estrellas.

#### MXN y USD dejan de parecerse

En Costos y en el Estudio conviven las dos monedas y nada lo decía: el costo del
producto se CAPTURA en dólares pero se GUARDA en pesos (× tipo de cambio), y
todo lo demás es peso. Dos casillas idénticas lado a lado con **19× de
diferencia** entre teclear en una o en la otra: un 1,625 en la equivocada son
$30,891. Peor aún, en el Estudio hay DOS campos llamados "Costo" con monedas
distintas a media pantalla de distancia.

Nuevo `components/Moneda.tsx` con el tratamiento en un solo lugar: el dólar en
ámbar (borde, fondo y chip), el peso en el tono neutro del panel. **Color +
etiqueta, nunca color solo** — quien no distinga ámbar de índigo sigue leyendo
USD y MXN en letras, y cada chip lleva su title. La conversión en vivo pasó de
un "≈" al margen a "= $58,907.89 MXN es lo que se guarda".

Aplicado donde se CAPTURA (Costos, CostoEditor, Estudio) y donde solo se
MUESTRA (tabla de Productos, Variantes, ficha, tarjeta y Crear Productos): un
"$1.00" en una tarjeta tampoco dice de qué moneda habla. El precio de Alibaba va
en dólares y no por suposición sobre esa plataforma — ese valor siembra el campo
de costo en USD.

#### Notas de la versión

También: mini-chip de situación junto a NO VENTA (la etiqueta habla del período,
no de la publicación); precio y margen muestran el promedio REALIZADO en vez de
una línea por cuenta; y `/api/fulfillment/canales` nació después de la auditoría
de `fetch()` sin token de la v0.61.x, así que se le había escapado — ya usa
`fetchSesion`. Auditado el frontend completo: 0 llamadas crudas y 0 descargas
por `<a href>`, que tampoco mandan el token.

Sin migraciones que aplicar y sin variables nuevas en Railway. Versión 0.64.0.

### v0.66.0 — Un costo increíble ya no se pinta como margen

Al encender el margen neto sobre datos reales quedó a la vista que el problema
no era el cálculo sino los insumos: **119 SKUs** con venta en 60 días tienen un
costo capturado MAYOR que su precio de venta, y 32 lo superan más de 3 veces
(`TEC-0406-AZL`: precio $269, costo $30,058 — **111×**). El agregado se delata
solo: la "pérdida" implicada ($2.33M) supera a la venta ($1.94M), lo cual es
imposible. Un `−978%` en pantalla se lee como un hecho y puede costar que
alguien baje una publicación rentable.

La regla vive en UN lugar (`frontend/lib/margen.ts`): si el costo supera al
precio realizado por más de **3×**, la celda de margen neto NO pinta un número
falso — muestra `⚠ costo?` en ámbar con el aviso completo en el tooltip
("Costo no creíble: N× el precio…, revísalo en Costos"). El umbral es 3× y no
1× a propósito: vender bajo costo existe (liquidación, error de precio) y eso
SÍ debe verse en rojo; arriba de 3× ya no es una decisión comercial, es un
dato mal capturado.

En Categorías la misma regla aplica en el agregado SQL (`porsku_c.creible` en
`_SQL_CAT_HOJAS`): los SKUs de costo no creíble salen de los promedios de rama
y el asterisco de cobertura cuenta la venta que quedó fuera. Sin esto, marcar
solo las hojas dejaba las ramas envenenadas — Herramientas decía **−173.9%**
por culpa de un puñado de costos rotos; limpio queda **−21%** con 87%% de
venta medible. Electrónica: −65.9%% → −13.2%%.

La lista de los 119 SKUs a recapturar (con dimensiones, flete y contenedor
para reconstruir el costo real) se entregó aparte como CSV — el panel avisa,
pero el dato solo se arregla capturándolo bien.

Sin migraciones que aplicar y sin variables nuevas en Railway. Versión 0.66.0.

### v0.68.0 — Márgenes reales: el envío deja de ser un estimado (fase 0)

Nueva sección **Análisis › Márgenes** (`/analisis/margenes`): los 10 SKUs más
vendidos POR CUENTA (30 días) con el margen sobre el **Costo Final** y los tres
cobros de Meli REALES — requisito de Eduardo del 6-ago:

- **Precio prom** = ingreso ÷ unidades de los pedidos (realizado), con
  insignia de PROMO cuando la publicación activa vende bajo su precio de
  lista (`channel.listings.price_base`) — el caso Malla Sombra: lista $960,
  promo $355, y el "misterio" del margen rojo era una decisión comercial.
- **Comisión /u** = `sale_fee` que ML cobró de verdad (ya se guardaba).
- **Envío /u** = **NUEVO**: el cobro real de ML por embarque
  (`GET /shipments/{id}/costs` → `senders[].cost`, con descuentos aplicados),
  prorrateado por unidad en carritos mixtos. El estimado de costing mentía en
  las dos direcciones: peso de caja capturado como pieza inflaba fees ($349
  contra $88 real en MUE-0163-TEL) y 141 SKUs con venta tenían el fee en $0,
  inflando el top de márgenes (VAR-0037-EST decía 47%% y gana 5%%).

La pieza nueva es `services/envio_real.py`: consulta los embarques a la API de
ML y cachea el costo POR ORDEN en MySQL (`ml_envio_real`, tabla nuestra, mismo
terreno que `amazon_imagenes`). Cada carga del panel consulta hasta
`presupuesto` órdenes faltantes (250 por default) y el frontend refresca solo
mientras `pendientes > 0` — así la primera carga grande no pelea con el timeout
del proxy y las siguientes solo pagan las órdenes nuevas. Un reintento nunca
pisa un costo real con NULL (COALESCE, mismo patrón que la comisión 0→valor).

Esto es la FASE 0 acordada: el caché es del panel. La fase 1 (persistir el
envío como parte del modelo de pedidos en `channel.order_shipments`, con seam
en vivo + backfill que bebe de este caché) queda a decisión de Eduardo — el
endpoint `/api/fulfillment/margenes-reales` solo cambiaría de dónde lee.
`/margenes-top` (envío estimado) queda vivo mientras tanto para la tarjeta
vieja de Omnicanal.

Sin migraciones que aplicar y sin variables nuevas en Railway. Versión 0.68.0.

### v0.69.0 — Márgenes: de una pestaña aparte a un popup, con visitas y dos guardas nuevas

Cierra el requisito "Márgenes en Omnicanal: 10 SKUs más vendidos, estructura de
precio promedio y sus costos, margen sobre el COSTO FINAL con todos los cobros
de Meli". La v0.68.0 lo entregó como sección propia; aquí se mueve a donde se
usa y se le suman las piezas que faltaban para que el número sea creíble.

**Deja de ser sección: es un botón.** "Productos más vendidos" vive junto al
selector de período de Análisis y abre un popup (`MargenesRealesModal`). Dentro
se filtra por cuenta (Ambas / Kubera / San Corpe), por estado de la publicación
(Todas / Activas / Pausadas) y por período (7/30/60/90 d). `/analisis/margenes`
se retira. "Ambas" es UNA lista general de 10, con chip BK/SC por fila — no las
dos tablas al mismo tiempo.

**Estado de la publicación + filtro.** Chip ACTIVA / PAUSADA / SIN PUB. por
fila, y el filtro va al BACKEND (`?estado=`): el top se corta en SQL, así que
filtrar en el cliente daría "las que sobrevivan de 10" y no el top 10 de las
activas. De 20 filas del top, 15 están pausadas — distinguir lo que sangra hoy
de lo que ya se detuvo era imposible antes.

**Visitas y conversión REALES** (`services/visitas_ml.py`). Sale del mismo
endpoint que usa Competencia (`/items/{id}/visits/time_window`). ML no acepta
multiget aquí — una llamada por publicación — así que se cachea en MySQL
(`ml_visitas`, TTL 6 h). Está en el popup y en la columna Visitas·CR% de la
tabla principal, que llevaba meses de adorno. En la tabla la conversión se
calcula con `uds_ml` y NO con las unidades totales: incluir las ventas de
Amazon, que no aporta visitas, inflaría el CR%% de cualquier SKU que venda allá.
Regla de TODO O NADA: si falta medir alguna publicación del SKU, la celda dice
"—" en vez de dividir media medición entre las unidades completas
(MUE-0163-TEL llegó a mostrar "209 visitas · 378.5%%" teniendo 13,331).

**El costo dudoso ya no esconde el margen.** Antes, con costo > 3× el precio, la
celda se quedaba en "⚠ costo?" y la ganancia en "—". Esconderlo sacaba al SKU
del análisis junto con la señal de que ahí pasa algo. Ahora margen y ganancia se
muestran SIEMPRE, en ámbar y con ⚠ — no en el rojo/verde que se lee como
veredicto. Aplicado en los tres lugares que comparten `lib/margen.ts`: popup,
tabla y árbol de Categorías.

**Marca "2 productos"** (`services/ficha_ml.py`). Un SKU publicado en las dos
cuentas debería ser el mismo objeto; si la bodega de ML pesó 40 g en una y 60 g
en la otra, no lo es — y comparten un costo, un inventario y un margen que no le
corresponden a uno de los dos. Solo cuenta lo que ML PESÓ (`PACKAGE_WEIGHT`),
nunca lo que declaramos: mezclar ambas fuentes llevaba el censo de 26 hallazgos
sólidos a 462 casi todos falsos. El título tampoco sirve (de 67 SKUs con títulos
distintos, la mayoría eran el mismo producto dicho de dos formas). Casos que ya
salta: TEC-0393-ROS (40/60 g), CUNA-0011-GRI (580/1140 g), MASC-0044-NEG
(1040/1820 g) y TEC-0324-MUL, una "aspiradora industrial" de 1 kg en una cuenta
y 14 kg en la otra.

**Rendimiento.** Una página fría llegó a tardar 18 s: se esperaban las ~100
llamadas de visitas de la página. Ahora los pares van en ORDEN DE FILA y solo se
bloquea por las 40 primeras (~20 filas, lo que se lee primero); el resto y la
ficha de peso se completan en segundo plano. Los cachés se escriben con UN solo
INSERT en vez de uno por fila — cada fila era un viaje de red al MySQL de
Hostinger. Medido: 7 s en frío / 2.8 s tibia, contra 18 s / 3.6 s.

Además, un bug PREEXISTENTE: `dash?.skus.skus_catalogo` protegía solo a `dash`,
así que un error de la API (o un reinicio) tumbaba la pestaña Análisis entera
con "cannot read properties of undefined". Ya va con opcional en los dos
niveles.

Tablas nuevas, todas NUESTRAS en MySQL (mismo terreno que `amazon_imagenes`):
`ml_envio_real`, `ml_visitas`, `ml_ficha`. Sin migraciones en la BD kubera y sin
variables nuevas en Railway. Versión 0.69.0.

### v0.70.0 — CORTE F6 de COSTOS: kubera pasa a ser la fuente de escritura (Eduardo)

Primer corte de escritura de la migración. Con la racha del acta de costos
cumplida (14/14 el 06-ago), el dominio COSTOS invierte la fuente de verdad:
con `SUPABASE_WRITE_COSTING=true` (nuevo flag, apagado por omisión) las tres
escrituras de `services/costos.py` van PRIMERO a la BD kubera y MySQL queda de
**espejo inverso** (opción A):

- `costos_validados` → `costing.costos_validados` (primaria, síncrona)
- `costos_finales`   → `costing.costos_finales` (primaria; P4: canal fijo
  `mercado_libre` mientras el motor sea ML-céntrico)
- bitácora `costos_logs` → `ops.process_log` (primaria; el panel sigue leyendo
  `costos_logs`, que el espejo inverso mantiene completa)

Piezas nuevas y movidas:

- **`services/costing_write.py`** (nuevo): decide el orden del corte. Primaria
  kubera con la MISMA atribución de `cost_history` del espejo (`set_config`
  transaccional) y los MISMOS upserts que validaron la racha —
  `costing_mirror` ahora expone `upsert_validados` / `upsert_finales` /
  `insertar_log` a nivel cursor y tanto el espejo F3 como el corte F6 y el
  reproceso los comparten (cero SQL duplicado). El SQL de MySQL tampoco se
  duplica: `costos.py` se lo pasa como thunk.
- **Espejo inverso**: tras la primaria, MySQL se escribe en hilo best-effort.
  Si MySQL falla: log + `ops.migration_issues` + Slack
  (`espejo_inverso:costing`) — la operación de negocio jamás se rompe.
- **Resiliencia con kubera caída** (el riesgo nuevo del corte): el negocio NO
  se bloquea. Se escribe MySQL como en el mundo viejo y el evento kubera queda
  ENCOLADO en `espejo_kubera_log` con payload reproducible; los handlers
  nuevos `costing.costos_validados` / `costing.costos_finales` de
  `kubera_mirror._UPSERTS` lo re-aplican con
  `POST /api/migracion/errores/reprocesar` (la bitácora encola con la forma de
  `ops.process_log`, que ya tenía handler). Slack avisa por la vía del espejo.
- **Lecturas internas del motor** bajo el flag: `costo_desde_validados`, la
  semilla de `_preparar_base` y la caché de comisiones
  (`_comision_categoria_db`) leen de kubera con fallback a MySQL (mismo patrón
  F5: `lecturas_fuente` + alerta). `None` de kubera reconsulta MySQL: si un
  evento quedó encolado por una caída, el espejo tiene la fila. OJO modelo v4:
  `costing.costos_finales` no lleva dims — durante la transición se
  complementan del MySQL espejo; al retirar MySQL las dims viven SOLO en
  `costos_validados` (el contrato).
- El acta diaria (`deltas-costos` 06:30) NO cambia: compara ambos lados y
  sigue debiendo dar cero — ahora audita el espejo inverso. Criterio de cierre
  de la transición: 14 días de actas invertidas en cero → retirar flag de
  lectura, espejo inverso y cron; tablas MySQL de costos a legado (F8).

Pruebas en el SANDBOX (`backend/scripts/probar_corte_costing.py`, patrón de la
suite de caos: guardia triple de ref, MySQL 100% stubeado, fila cobaya
`ZZZ-CORTE-F6` que se limpia al final): **15/15 PASAN** — primaria + espejo
inverso + identidad `core.products`, lectura con fallback y alerta, caída de
kubera (negocio no truena, evento encolado, Slack), reproceso del payload
encolado (el valor de la cola gana) y flag OFF (mundo viejo intacto).

Revertir = `SUPABASE_WRITE_COSTING=false` (vuelve el dual-write clásico, cero
deploys). Sin migraciones que aplicar. Variable nueva en Railway:
`SUPABASE_WRITE_COSTING` (encender SOLO con el dale — es la primera vez que
escrituras de un dominio vivo cambian de casa). Los cortes de los demás
dominios (core/orders/channel/categorías) esperan su propia racha 14/14; el
candado de alertas (`alertas_estado`) NO se corta a propósito: debe sobrevivir
con kubera caída (es quien avisa de esas caídas) y su fusión a
`ops.process_log` es tarea del cierre (F8).

### v0.71.0 — CORTES F6 de PEDIDOS y CHANNEL (Eduardo)

Los otros dos dominios con racha cumplida se cortan con el mismo patrón de la
v0.70.0 (opción A, espejo inverso). Verificado contra
`migration.reconciliation_runs` el 06-ago: **orders-deltas 15/14** y
**channel-deltas 17/14** (costing va en 19).

**PEDIDOS** — flag `SUPABASE_WRITE_ORDERS` (apagado por omisión). El registro
de cada venta (`pedidos_ml.sincronizar`, por donde pasan ML, Amazon y
Temu/TikTok) escribe PRIMERO `channel.orders` + `channel.order_items` en UNA
transacción kubera, reutilizando los upserts del seam
(`_up_channel_orders`/`_up_channel_order_items` — la semántica que validó la
racha: estados se mueven, importes congelados, 0 → valor real una sola vez).
`pedidos_ml` MySQL pasa a espejo inverso en hilo (fallo → log + issue +
Slack `espejo_inverso:orders`; el fallback de lectura F5 del tab Ventas sigue
fresco). Con kubera caída: MySQL aguanta y los DOS payloads viajan por el
espejo clásico (workers → cola `espejo_kubera_log`, reprocesable) + Slack
`escritura_fallback:orders`. Las LÍNEAS respetan el censo
(`pedidos_ml_items` en `KUBERA_MIRROR_TABLAS`): el corte no enciende flujos
que el censo tenga apagados. Módulo nuevo: `services/orders_write.py`; el
SQL de MySQL viaja como thunk desde `pedidos_ml.py` (cero duplicación).

**CHANNEL** — flag `SUPABASE_WRITE_CHANNEL` (apagado por omisión). Cada tanda
del sync de inventario (15 min) escribe PRIMERO `channel.listings` (kubera,
vía `corte_channel` para el trigger de historia) y `canal_inventario` MySQL
queda de espejo inverso en hilo. `channel_mirror` expone la tanda a nivel
cursor (`escribir_tanda`, compartida por espejo F3 / corte / backfill) y
`escribir_primario` decide el orden. Este dominio NO lleva cola a propósito:
con kubera caída MySQL aguanta y el SIGUIENTE ciclo (full-refresh por tanda)
auto-sana — Slack avisa (`escritura_fallback:channel` /
`espejo_inverso:channel`). `backfill_situacion` y `sincronizar_drop` (bodega
propia) operan también bajo el corte (`activo() or corte_activo()`).

Pruebas en el SANDBOX (`backend/scripts/probar_corte_orders_channel.py`,
mismo arnés: guardia triple, MySQL stubeado, cobayas `ZZZ-CAOS-1` /
`ZZZ-CORTE-CH` limpiadas al final): **20/20 PASAN** — primarias + espejos
inversos, paridad de semántica (total congelado 100 vs 999, comisión 0→55 y
luego inmutable ante 77, estados siempre se mueven), caídas de kubera en
ambos dominios (negocio no truena, eventos por espejo/cola en pedidos,
auto-sanado en channel) y flags OFF (mundo viejo intacto).

Revertir = apagar el flag del dominio (cada uno independiente, cero deploys).
Sin migraciones que aplicar. Variables nuevas en Railway:
`SUPABASE_WRITE_ORDERS` y `SUPABASE_WRITE_CHANNEL` — encender SOLO con el
dale (pedidos es el flujo vivo más caliente: webhooks en ráfaga). Con esto,
TRES de los cinco dominios tienen su corte listo; core y categorías esperan
racha (van 11/14 ambos).

### v0.72.0 — El Excel de reportes deja de calcular margen

Petición de Eduardo (7-ago): quitar los márgenes del reporte descargable, en
las tres hojas. Se retiran las columnas **Ganancia** y **Margen %** de
`Resumen`, `Categorias` y `Ventas`.

**Por qué, y por qué no es una pérdida.** El margen del libro salía de
`costing.costos_validados` / `costos_finales`, y esa base tiene tres defectos
ya medidos en producción:

| Defecto | Alcance | Efecto en el margen |
|---|---|---|
| `costo_producto` es un precio USD×19 (placeholder, no costo medido) | 4,606 de 15,395 filas (~30% del catálogo) | El margen es una resta contra un número inventado |
| Peso de la CAJA capturado como peso de la pieza | ~536 SKUs | Infla `costo_fee_envio` (~$231k fantasma en 60 días) |
| `piezas_por_caja < 1` — multiplica el flete en vez de dividirlo | 30 SKUs | TEC-0406-AZL llega a 111× su flete real |

Una celda que dice "36.0%" se lee como un hecho. Con esa base no lo es, y el
reporte se comparte fuera del panel, donde la advertencia no viaja con el
archivo. Las columnas de COSTO se quedan —son el dato crudo— y quien necesite
la resta la arma en su tabla dinámica sabiendo qué está restando.

**Qué cambia en cada hoja**

- `Resumen`: 12 → 10 columnas. Se van Ganancia y Margen %; `Venta con costo`
  se queda pero cambia de papel: era el denominador del margen, ahora es el
  medidor de COBERTURA (cuánto de lo vendido tiene costo capturado). Su
  comentario de celda se reescribió para decir eso. Los `SUM` del TOTAL se
  reindexaron a B,C,D,F,G,H,I,J.
- `Categorias`: 16 → 14 columnas. Los `SUBTOTAL(9,…)` por nivel ahora abarcan
  F:K (antes F:L, donde L era Ganancia); se retiró la fórmula `SUMIF` que
  calculaba el margen del nivel sobre la venta con costo.
- `Ventas`: 17 → 15 columnas. Sin las dos fórmulas por renglón.

El archivo pasa a llamarse `ventas_costos_*.xlsx` y la tarjeta de
`/analisis/reportes` dice "Ventas y costos (Excel)" con la razón de la
ausencia a la vista, para que nadie la busque como si fuera un bug.

Verificado con un libro sintético que incluye filas CON y SIN costo: 10/14/15
columnas, cero encabezados de margen o ganancia, `SUBTOTAL` y `SUM` apuntando
a las columnas nuevas, y las filas sin costo en blanco (no en cero).

Nada de esto toca la captura de costos ni las vistas de Análisis, que siguen
mostrando margen con su marca de "costo dudoso". Sin migraciones y sin
variables nuevas en Railway. Versión 0.72.0.

**Pendiente propuesto (no construido):** marcar cada celda vacía con el motivo
—columna `Diagnóstico` en texto, relleno ámbar + comentario, y una cuarta hoja
de leyenda que cuente cuántas filas trae cada motivo—. Bloqueado a propósito
hasta saber DÓNDE se captura cada dato faltante, para que el mensaje diga
"captúralo en tal pantalla" y no solo "falta el costo".

### v0.73.0 — Columna Diagnóstico: cada hueco del reporte dice por qué está vacío

Petición de Eduardo (7-ago), sobre la v0.72.0: "para los que tienen campos
vacíos, una columna describiendo el tipo de problema". Las hojas `Ventas` y
`Categorias` ganan una última columna **Diagnóstico** que nombra el problema
del renglón y dice dónde se arregla.

**El hallazgo que salió al construirla: el reporte fabricaba ceros.** La
columna "Envío est." se pintaba con `float(... or 0)`, así que un SKU sin
`costo_fee_envio` salía como **envío $0** — indistinguible de un envío
gratis real. Medido sobre 60 días: **3,076 renglones con envío en $0, de los
cuales CERO eran envío gratis**. Los 3,076 eran falta de dato. Ahora la celda
va vacía, en ámbar, y el diagnóstico dice cuál de las dos es.

**Las seis reglas**, calibradas contra los 689 SKUs con venta en 60 días:

| Diagnóstico | Regla | Renglones (de 9,844) |
|---|---|---|
| COSTO MAYOR QUE LA VENTA | `costo_base > ingreso` | 2,161 (22.0%) |
| COSTO PLACEHOLDER | `costo_producto` múltiplo exacto de 19 | 1,974 (20.1%) |
| SIN DATO DE ENVÍO | `costo_fee_envio IS NULL` | 1,188 (12.1%) |
| SIN COSTO | sin fila en `costos_validados` ni `costos_finales` | 924 (9.4%) |
| PESO DE CAJA | densidad > 1.5 kg/L | 824 (8.4%) |
| CAJAS EN CERO | `cajas = 0` | 230 (2.3%) |
| FLETE MULTIPLICADO ×N | `piezas_por_caja < 1` | 1 |

74.2% de los renglones traen diagnóstico. Cada fila muestra el problema MÁS
GRAVE en claro y lista los demás entre paréntesis, así que se puede filtrar
por el prefijo sin perder el resto.

**La unidad de `costos_validados.peso` quedó verificada**, no supuesta: son
KILOGRAMOS. Comparada contra la báscula de ML (`ml_ficha.peso_g`, solo
`PACKAGE_WEIGHT`) sobre 344 publicaciones, la mediana de `peso×1000 / peso_ML`
es 1.000. Sin esa comprobación el umbral de densidad habría sido un adivine, y
una bandera equivocada es peor que ninguna bandera.

**Va como TEXTO, no solo como color.** El relleno ámbar marca la celda que
falta, pero el diagnóstico vive en una columna de texto porque el color no
sobrevive a un copiar-y-pegar ni a una exportación a CSV, y este archivo se
comparte fuera del panel.

Insumos nuevos en `_SQL_MARGEN_LINEAS` y `_SQL_CAT_PUBS` (`fee_envio_unit`,
`tiene_validado`, `tiene_final`, `piezas_por_caja`, `cajas`, `costo_producto`,
`peso`, `largo`, `alto`, `ancho`): se leen para diagnosticar, no se pintan.
Nada de esto ESCRIBE en costing — solo lo lee. Sin migraciones y sin variables
nuevas. Versión 0.73.0.

### v0.74.0 — El reporte usa el envío REAL de Mercado Libre, no el estimado

Cierra el pendiente que quedó abierto desde la v0.68.0. La columna de envío ya
no es el cálculo por peso/dimensiones de `costing.costos_finales`: es lo que ML
**cobró de verdad** por cada embarque (`GET /shipments/{id}/costs`).

**Cuánto mentía el estimado.** Sobre los mismos 60 días:

| | Estimado (antes) | Real de ML (ahora) |
|---|---|---|
| Envío total | $1,118,123 | **$741,119** |
| Como % del ingreso | 30.0% | **18.4%** |
| Costo final / venta con costo | 154.3% | **142.4%** |

El estimado inflaba el flete en **$377,004 en 60 días**. Caso concreto:
`TEC-0049-NEG` (máscara de motocross) tenía envío estimado de **$152**; ML
cobró **$35**. Es el mismo SKU que el diagnóstico marca como PESO DE CAJA
(12.58 kg capturados, 4.6 kg/L) — la causa y su efecto, ahora visibles juntos.

**El reparto.** ML cobra por EMBARQUE, no por pieza: una orden con tres
artículos tiene un solo cobro. `envio_real.aplicar_a_lineas()` lo reparte entre
las líneas de su orden en proporción a las UNIDADES (misma convención que el
popup de Análisis). Repartir por importe le cargaría al artículo caro de un
carrito mixto un flete que no le toca. El costo final se rearma con el envío ya
resuelto, para que la columna y el total no digan cosas distintas.

**Cobertura: 99.3%** (9,786 de 9,856 renglones). El caché `ml_envio_real` se
llenó de 40.7% a **100% de los 9,804 pedidos de ML del período en 161 s**. Lo
que falte en descargas futuras se consulta en segundo plano (presupuesto 400
por descarga), así que la siguiente sale más completa sin que ésta espere.

**Nueva columna "Origen envío"** (`ML real` / `estimado` / `sin dato`, y
`mezclado` en el árbol cuando una publicación tiene de ambos). Una columna que
mezcla fuentes en silencio es la misma trampa que el margen retirado en la
v0.72.0; aquí la fuente viaja al lado del número, renglón por renglón, y el
Resumen declara la cobertura en su nota de encabezado.

Detalles que importan: un `costo_vendedor = 0` es una respuesta legítima de ML
(el comprador pagó el envío) y se distingue de `NULL` comparando contra None,
no por verdad/falsedad. Sin MySQL (staging solo-Supabase) el reporte no se cae:
se queda con el estimado y lo dice. En `Categorias`, "Origen envío" y
"Diagnóstico" van al FINAL para que el bloque numérico F..K siga contiguo y los
`SUBTOTAL(9,…)` no tengan que saltarse una columna de texto — verificado tras
el cambio.

Sin migraciones y sin variables nuevas. Versión 0.74.0.

### v0.75.0 — El reporte avisa cuando el rango pedido no es el rango con datos

Al llenar el caché de envío para el histórico salió que **el botón "Histórico
(400 días)" no tiene histórico**: en 400 días hay 9,822 pedidos de ML, y 9,668
son de julio y agosto de 2026.

| Mes | `channel.orders` (ML) | `pedidos_ml` (registro viejo) |
|---|---|---|
| 2026-02 | 1 | 1 |
| 2026-05 | 4 | 4 |
| 2026-06 | 182 | 181 |
| 2026-07 | 7,749 | 7,608 |
| 2026-08 | 3,276 | 3,513 |

Verificado contra las DOS fuentes por separado, para descartar pérdida de datos
en la migración: coinciden. No falta historia — **no existe**. La captura de
pedidos arrancó de verdad a finales de junio de 2026.

El riesgo no es el archivo vacío, es el archivo que parece lleno: pedir 400
días devuelve un libro que aparenta cubrir un año y cubre siete semanas, y los
meses sin captura se leen como meses malos. Es el mismo error de siempre —
tomar la ausencia de dato por un dato.

Ahora la hoja `Resumen` escribe en **A2** (fila que estaba libre; no se corre
ninguna fórmula) uno de dos avisos en ámbar, y ninguno si el rango pedido sí
tiene datos de principio a fin:

- *"OJO CON EL RANGO: se pidió desde 2025-07-03, pero la primera venta
  capturada es del 2026-02-16 (hay ventas en 59 días distintos, hasta
  2026-08-07). Los meses anteriores no salen bajos: salen sin captura."*
- *"SIN VENTAS en el rango pedido (…). El libro va vacío: no es que no haya
  margen, es que no hay pedidos capturados en esas fechas."*

El conteo de **días distintos con venta** es deliberado: la primera fecha por
sí sola engaña (ese único pedido de febrero haría creer que hay serie desde
entonces).

Probado en los tres escenarios —histórico con datos parciales, rango normal
cubierto, y rango sin ventas— y contra producción con `dias=400`. Sin
migraciones y sin variables nuevas. Versión 0.75.0.

### v0.76.0 — Vista previa del Excel, y el aviso de rango deja de gritar siempre

Petición de Eduardo (7-ago): poder ver qué trae el archivo antes de bajarlo.
Botón **Previsualizar** en la tarjeta de `/analisis/reportes`, junto a
Descargar, con endpoint nuevo `GET /api/fulfillment/categorias/excel/preview`.

Muestra, sin bajar los ~1.4 MB: líneas, unidades, SKUs distintos e ingreso;
dos barras de cobertura (**envío con el cobro real de ML** y **venta con costo
capturado**); el censo de diagnósticos que va a traer; y el tamaño de cada
hoja. Arriba de todo, el aviso de rango si aplica.

**Refactor que hace honesta la vista previa.** La preparación de datos —tres
consultas, envío real, agregado por publicación, relleno en segundo plano— se
extrajo a `_datos_reporte()`, que ahora usan LOS DOS endpoints. Si cada uno
armara sus datos, la vista previa acabaría prometiendo un archivo distinto del
que llega, que es justo lo que una previsualización no puede hacer. De paso se
arregló un `p` que se sombreaba a sí mismo (el dict de parámetros y la variable
del `for p in pubs`).

**DEFECTO CORREGIDO de la v0.75.0.** El aviso de rango se disparaba con
`primera_venta > desde`, o sea CASI SIEMPRE: pedir 60 días arranca el 09-jun y
la primera venta es del 10-jun — un día de hueco y ya avisaba. Un aviso que
sale siempre enseña a ignorarlo, que es peor que no avisar. Ahora
`rango_parcial()` exige las DOS condiciones: hueco inicial de **más de 7 días**
Y de **más del 10% de la ventana**. Verificado:

| Caso | Antes | Ahora |
|---|---|---|
| 60 días, vende al día siguiente | avisaba | **callado** |
| 7 días, vende al 2º día | avisaba | **callado** |
| 90 días, primera venta a los 30 | avisaba | avisa (33.3%) |
| 400 días, primera venta a los 227 | avisaba | avisa (56.9%) |

La regla vive en `reporte_categorias_xlsx.rango_parcial()` y la consumen el
Excel (Resumen!A2) y la vista previa: la previa avisa exactamente cuando el
archivo va a avisar, no con un umbral paralelo.

**"Pedidos" salió de los mosaicos**: en estos datos cada pedido trae
exactamente una línea (verificado: `max_lineas = 1` sobre 9,875 pedidos de 60
días), así que era la misma cifra dos veces. En su lugar va **Unidades**, que
sí difiere (11,649 unidades en 9,879 líneas; hay pedidos de hasta 40 piezas).
El campo sigue en el JSON por si ML alguna vez manda órdenes multi-línea.

Cambiar cualquier filtro borra la vista previa: dejarla en pantalla
describiendo otro rango sería peor que no mostrarla. Probado el endpoint en
cuatro escenarios contra producción (60d, 400d, una cuenta, rango sin ventas);
tarda 1.4–7 s. Typecheck y `next build` limpios. Sin migraciones y sin
variables nuevas. Versión 0.76.0.

### v0.77.0 — La vista previa se calcula sola, y sale del flex que la aplastaba

Dos correcciones a la v0.76.0, ambas reportadas por Eduardo con una captura.

**El layout estaba roto.** `{errorBaja}` y `<VistaPrevia/>` quedaron como
hermanos DENTRO de `<div className="flex items-start gap-3">`, así que se
volvían un tercer ítem del flex y le robaban el ancho al texto: la descripción
de la tarjeta se aplastaba a **una palabra por renglón** y el panel se montaba
encima. El error ya venía así de antes; la vista previa solo lo hizo visible.
Ahora los dos van FUERA de la fila, como hijos directos de la tarjeta.

Medido en el navegador, antes y después: la descripción pasó de una columna de
una palabra a **1,126 px en 3 renglones**; el panel es hijo directo de la
tarjeta (`panel_dentro_de_fila_flex: false`), mide 1,180 px —el mismo ancho que
la fila de filtros— y va debajo de todo. Sin scroll horizontal, y los 8 chips
de diagnóstico envuelven sin desbordar.

**Fuera el botón "Previsualizar".** Era un paso de más para algo que siempre
quieres ver: ahora la previa se calcula sola con los filtros puestos, y se
actualiza al cambiarlos. Con retardo de 450 ms y `AbortController`, porque cada
cálculo son 1.4–7 s en el servidor: ir de "7 días" a "Histórico" dispararía
cuatro peticiones si saliera en cada clic, y sin abortar la que pinta sería la
que llegue antes, no la última pedida.

Mientras recalcula, el panel se atenúa y dice "Actualizando la vista previa…"
en vez de vaciarse, con `min-h-[132px]` y un esqueleto para la primera carga:
la tarjeta no salta entre estados.

**Verificado en el navegador contra el sandbox**, no solo con typecheck: la
previa carga sola al abrir (200 OK), y al pulsar "Histórico" se recalcula y
aparece el aviso —"227 días (56.9% del rango) van sin captura, hay ventas en 58
días distintos"—. De paso quedó ejercitada la degradación sin MySQL que exige
`aplicar_a_lineas`: en staging `MYSQL_ENABLED=false`, y el panel reporta
"0 real · 6,605 estimado · 2,848 sin dato" en vez de caerse.

Sin migraciones y sin variables nuevas. Versión 0.77.0.

### v0.78.0 — La tarjeta "Más reportes" dice la verdad de hoy: qué falta para creerle a los números

Petición de Eduardo (7-ago): la tarjeta seguía pidiendo decisiones que ya no
aplican, y lo que hoy limita el reporte no es construirlo — es **confiar en sus
cifras**.

**Fuera "Antes de construirla hace falta".** Los dos bloqueos (elegir el precio
sugerido oficial, decidir dónde guardar los archivos) se retiran. También sale
"Carpeta de descargas" de *Datos que necesita*: no era una fuente de datos sino
el mismo prerrequisito listado dos veces. El contador queda en **3 de 3 listas**
en vez de 3 de 4.

**Entra "Para que los números cierren al 100%"** (prop `numeros` en
`FulfillmentPendiente`, junto a `bloqueos` — que se conserva porque
`/analisis/fba` lo usa). Va aparte a propósito: no impide *construir*, impide
*creer*. Siete puntos, **3 resueltos** —tachados, para que se vea el avance y no
parezca un muro— y 4 pendientes:

| | Punto |
|---|---|
| ✓ | Envío: el cobro REAL de ML por embarque, no una estimación por peso |
| ✓ | Comisión: la que ML cobró de verdad en cada venta |
| ✓ | Huecos señalados: cada renglón dice por qué le falta un dato |
| • | Costo de compra: ~⅓ del catálogo trae precio de lista en dólares, no lo pagado |
| • | Productos sin costo: ~1 de cada 7 SKUs vendidos |
| • | Pesos mal capturados: ~500 productos con peso de caja como pieza |
| • | Historial corto: ventas capturadas desde finales de junio |

Redactado **en grueso a propósito**: sin nombres de tablas ni cifras al detalle.
Para el detalle está la columna Diagnóstico del propio Excel — la tarjeta es el
mapa, no el terreno.

Verificado en el navegador contra el sandbox: el bloque ámbar viejo ya no
existe, la sección nueva pinta sus 7 puntos con 3 tachados, ocupa el ancho
completo y no desborda.

**Hallazgo que NO se tocó:** en móvil (375 px) la página desborda 53 px, y el
culpable son las filas de filtros preexistentes (cuentas, períodos y las dos
fechas), no las secciones nuevas — ninguna de ellas se sale. Queda anotado, no
corregido: arreglarlo toca el layout de los filtros y eso es otro cambio.

Sin migraciones y sin variables nuevas. Versión 0.78.0.

### v0.79.0 — Inventario accionable: Inmovilizado e Invisible

Primer reporte de inventario (Eduardo, 7-ago). No es un volcado del almacén:
son las **dos poblaciones sobre las que se puede actuar hoy**, y son problemas
opuestos.

| | Qué es | Producción, 30 días |
|---|---|---|
| **Inmovilizado** | El mercado no lo quiere: hay stock en FULL y no vende, así que paga renta a ML todos los días | **292 SKUs**, 13,839 unidades — y **294 de ellos NUNCA han vendido una pieza** |
| **Invisible** | El mercado sí lo quiere y no se lo estamos ofreciendo: vendió, tiene stock, y ninguna publicación está activa | **97 SKUs**, 2,497 unidades vendidas que hoy no se pueden repetir |

Casos que las definen: `JUGU-0261-LIL` (karaoke, 272 en FULL + 648 en bodega,
cero ventas históricas) y `TEC-0393-ROS` (291 unidades vendidas en 30 días,
2,394 en bodega, sus dos publicaciones pausadas).

**El filtro que hace creíble a Invisible.** Solo entra lo pausado CON STOCK. Lo
pausado sin stock está agotado —que es la razón correcta para pausar— y
pertenece a "Reponer", todavía sin construir. Ese filtro bajó la hoja de 187 a
**97 SKUs**: sin él, la mitad no sería accionable y la hoja perdería
credibilidad al primer vistazo.

**Sin valorizar en dinero, a propósito.** Es la misma trampa del margen que se
retiró en la v0.72.0: `costo_producto` es un precio USD×19 de relleno en ~30%
del catálogo, así que un total de "inventario valuado en $X" sería ficción con
formato de hecho. Lo que sí se mide con datos confiables: cuánto hay, dónde
está y cuánto lleva sin moverse.

**Dos trampas del modelo de datos, resueltas:**

1. `max(stock_own)` y NO `sum`: el stock propio está espejeado en CADA
   publicación del mismo SKU. Sumarlo por publicación cuenta la misma pieza
   varias veces — medido: 1,109,525 unidades en el canal `general` contra
   343,045 en `mercado_libre`, que son las MISMAS piezas.
2. `stock_full` filtrado a `canal='mercado_libre'`: FULL es un concepto de ML.
   Sin el filtro, la columna "En FULL" no cuadraba con la suma de Bekura +
   Sancor (272 contra 200 en JUGU-0261-LIL) porque se colaba `stock_full` de
   publicaciones de Amazon, cuyo equivalente es `stock_fba`. Verificado tras el
   arreglo: 0 filas donde no cuadre, de 292.

Endpoints `GET /api/fulfillment/inventario/excel` y `.../preview`, con
`_datos_inventario()` compartido —misma razón que en el reporte de ventas: una
previa que arma sus propios datos acaba prometiendo un archivo distinto del que
llega—. Tarjeta propia en `/analisis/reportes` con previa automática de dos
bloques. El libro trae una portada "Cómo leer" y cada hoja lleva su criterio
escrito dentro, porque el Excel se comparte fuera del panel.

Verificado en el navegador contra el sandbox: los dos bloques lado a lado, misma
altura, 1,180 px, sin desbordes ni scroll horizontal. Sin migraciones y sin
variables nuevas. Versión 0.79.0.

### v0.80.0 — WooCommerce no es un canal, y "publicación viva" se dice distinto en cada marketplace

Dos correcciones al reporte de inventario, pedidas por Eduardo (7-ago):
WooCommerce es **nuestro puente de registro**, no un canal de venta; y el
diagnóstico de Inmovilizado debe **describir, no recetar**.

**Woo deja de ser una tienda, pero sigue siendo el almacén.** Su fila ya no
aparece en la columna "Cuentas" ni cuenta como publicación. Lo que SÍ conserva
es el stock propio, y no por comodidad: **en 47 de los 97 SKUs de Invisible el
valor cambia si se le excluye**, porque el `stock_own` de las publicaciones de
marketplace es un espejo que puede venir viejo. Ahora se toma el de Woo y solo
se cae al espejo si no existe.

**"Viva" no significa lo mismo en cada canal.** El criterio contaba solo
`situacion = 'active'`, que es vocabulario de Mercado Libre; **Amazon usa
`buyable` / `published`**. Se descubrió revisando los estados por canal, no
buscándolo.

Efecto medido en la hoja Invisible, **97 → 85 SKUs**:

| Salieron | Por qué |
|---|---|
| 3 | Sí estaban a la venta en Amazon — nunca fueron invisibles |
| 9 | Su stock propio era **fantasma**: la publicación de ML espejeaba un valor viejo y Woo dice 0 |

Los 9 del stock fantasma son el hallazgo colateral: la hoja los ofrecía como
"reactivables" cuando no había nada con qué surtir.

**El diagnóstico de Inmovilizado ya no receta.** Antes decía "Sacarlo deja de
pagar renta" y "evalúa retirarlo de FULL". Qué hacer con un inmovilizado
—retirarlo, liquidarlo, dejar de comprarlo— depende de temporada, contrato y
planes que el reporte no conoce; ahora solo declara el hecho ("jamás vendió una
pieza, y aun así ocupa lugar en FULL"). **Invisible sí conserva la sugerencia**,
a petición de Eduardo, porque ahí la acción es una sola y no admite matices: hay
stock y la publicación está apagada.

Verificado tras el cambio: las cuentas que aparecen son solo Bekura, Sancor y
Amazon; "En FULL" sigue cuadrando con Bekura + Sancor en las 293 filas. Sin
migraciones y sin variables nuevas. Versión 0.80.0.

### v0.83.0 — TikTok Shop CONECTADO: tres bugs que dejaban el canal inservible con el token bueno, y la pestaña de Webhooks

La app quedó publicada y la tienda **KUBERA** (`shop_id 7494659908378395724`,
region MX, **seller_type LOCAL**) autorizada. Pero entre "token válido" y "canal
que funciona" había tres bugs.

#### 🔴 Sin `shop_cipher` no se publica NADA

`guardar()` buscaba las tiendas en `data["granted_shops"]` / `data["shops"]`, y
**el canje del token no trae ninguna de esas llaves**. Caía SIEMPRE al respaldo
y guardaba el `open_id` como si fuera shop_id, con el cipher en `NULL`:

```
antes →  shop_id = CGqC6QAAAACagEFIP-HAFurH…   (eso es el open_id)
         shop_cipher = NULL
después → shop_id = 7494659908378395724
         shop_cipher = ROW_FnkQ_QAAAA…
```

El cipher es query param **obligatorio** en Create Product, Update Inventory,
Update Price y la Events API. Se piden aparte con
`GET /authorization/202309/shops`, y los campos se llaman **`cipher` e `id`** —
no `shop_cipher` ni `shop_id`.

#### 🔴 El `state` caducaba ANTES que el permiso de TikTok

`_STATE_TTL` estaba en 900 (15 min) y el `auth_code` de TikTok dura **30**. Entre
los minutos 16 y 29, TikTok mandaba un code **válido** y nuestro propio candado
lo rechazaba. El síntoma habría sido *"autoricé y no sirvió"*, sin nada raro del
lado de TikTok. El 7-ago funcionó solo porque Brandon fue rápido.

#### 🔴 No había forma de firmar peticiones

TikTok no se conforma con el token: firma cada llamada. El HMAC que existía era
solo para el `state`. Se agregan `_firmar()`, `llamar()` y
`tiendas_autorizadas()`.

**Cómo se verificó la firma sin poder llamar:** TikTok respondió
`36009033 IP not in allow list`. Ese error **prueba** que la firma es correcta —
pasó la validación criptográfica y se frenó en la capa de red. Una firma mal
armada devuelve error de firma. El bloqueo fue la evidencia.

#### 🖼️ Las imágenes las rehospeda TikTok — el problema de Walmart NO aplica

El producto que ya existía en la tienda lo demostró: sus imágenes viven en
`p16-oec-sg.ibyteimg.com` con un `uri` propio, a 1000×1000. **Se suben a TikTok
y el producto referencia el `uri`** — TikTok nunca entra a nuestro servidor, así
que el infierno del WebP de `chunche.shop` (v0.82.0 de Walmart) no se repite.
El costo es otro: ~300 productos × 3 imágenes = **~900 subidas** antes de crear.

#### 🔒 La allowlist de IPs y su trampa

TikTok tiene lista de IPs permitidas por app. **Los cambios quedan en BORRADOR
hasta presionar "Publicar cambios"**: la IP aparece en la lista y sigue
bloqueando. Costó ~15 min de diagnóstico y seis reintentos.

#### Lo que se agregó

| | |
|---|---|
| `POST /api/tiktok/reparar-tiendas` | admin — rellena el cipher **sin re-autorizar** |
| `/webhooks` (frontend) | pestaña solo-admin: los 4 canales y el log en vivo |

La pestaña distingue tres situaciones que conviene no confundir: **vivo**,
**en observación**, y **sin webhook porque la plataforma no los ofrece**. Amazon
(sondeo SP-API cada 5 min) y Temu (sondeo M2E cada 10 min) son el tercer caso:
**no son un pendiente**, y la pantalla lo dice para que nadie los busque.

#### Datos del canal al 7-ago

- **2,168 categorías** de México, **1,937 son HOJA** (solo ahí se publica).
- **1 producto ya publicado**: `TEC-1212-NEG-150MTS`, categoría hoja
  `913416 "Accesorios de audio y video"`. Vino de M2E o se subió a mano.
- `seller_type = LOCAL` ⇒ las APIs `global_product_*` **no sirven**: son para
  vendedores intra-UE y globales.
- `/seller/202309/shops` **rechaza** el `shop_cipher` (*"is not required for
  this request"*). El cipher no es universal.

Verificado antes del push: las **63 pruebas** de `humo_auth.py`, typecheck y
build del frontend.

### v0.82.0 — TikTok Shop: el canal deja de depender de M2E, y su webhook nace en modo OBSERVACIÓN

Kubera obtuvo credenciales propias en el TikTok Shop Partner Center (app
`tiktotapi`, 7-ago). Hasta hoy TikTok entraba por **M2E Cloud**, cuyo conector
lleva con `is_valid=false` desde julio. Esto abre la vía directa.

**El bloqueador que nadie había visto:** el código del OAuth de TikTok
(`routers/tiktok.py`, `services/tiktok.py`, ~450 líneas) llevaba días escrito en
disco y **nunca se commiteó**. La URL de redirección ya estaba registrada en el
Partner Center, así que producción respondía **401** a la vuelta del seller:

```
200  /api/health           ✓
200  /api/webhooks/ml      ✓
401  /api/tiktok/callback  ✗   ← TikTok recibiría esto
```

**El receptor del webhook nace SIN TOCAR LA BASE DE DATOS.** No es minimalismo:
la consola de TikTok ofrece cuatro temas (tipos 2, 3, 4 y 5) y **ninguno es
"pedido creado"**, así que el catálogo real de eventos solo se descubre viéndolo
llegar. Es el mismo método que se usó con M2E — loguear el crudo hasta que la
primera venta confirme el esquema — y evita diseñar tablas contra un formato
imaginado. El plan es observar **dos semanas** tras publicar 300 productos.

| Ruta | Quién entra | Qué hace |
|---|---|---|
| `POST /api/webhooks/tiktok` | abierta | verifica firma, loguea, **cero BD**, responde 200 siempre |
| `GET /api/webhooks/tiktok` | abierta | prueba de accesibilidad (TikTok la valida al guardar) |
| `GET /api/webhooks/tiktok/log` | **cerrada** | últimos 300 eventos en memoria |
| `GET /api/webhooks/activos` | **cerrada** | panel de administración de los 4 canales |

**Por qué `/log` va cerrado:** el scope `seller.order.info` viene marcado por el
propio TikTok como *"Datos confidenciales — contiene información personal de los
clientes"*. La apertura del middleware es por **igualdad exacta**, no por
prefijo, así que `/api/webhooks/tiktok/log` NO hereda la de
`/api/webhooks/tiktok`. Verificado con pruebas, no supuesto.

**La firma HOY SOLO OBSERVA.** Calcula el HMAC, registra el veredicto y **no
rechaza nada**: el algoritmo exacto de TikTok no está confirmado contra su
documentación, y un verificador mal implementado tiraría eventos legítimos sin
dejar rastro. Como en fase 1 no se persiste ni se actúa, un evento falso solo
ensucia el log. Al confirmarse el esquema pasa a rechazar — y ahí deja de ser
opcional: la URL es pública.

**Guarda absoluta, igual que en ML:** el cuerpo entero va dentro de un `try` y
ningún fallo cambia la respuesta 200. Si devolviéramos otra cosa, TikTok
reintenta y puede deshabilitar la suscripción — un fallo silencioso en el que
simplemente dejan de llegar eventos.

**El panel `/activos` distingue tres situaciones** que conviene no confundir:
vivo, construido-pero-apagado, y *sin webhook porque la plataforma no los
ofrece*. Amazon (sondeo SP-API cada 5 min) y Temu (sondeo M2E cada 10 min)
caen en la tercera: **no son un pendiente**, y la pestaña lo dice así para que
nadie los busque.

**Nace apagado** (`TIKTOK_ENABLED=false`): hablar con un marketplace vivo es
cambio de flujo (regla 3). El webhook en observación funciona sin encender el
canal.

Verificado antes del push: las **63 pruebas** de `humo_auth.py` y 6 del receptor,
todas bajo condiciones de producción (`API_KEY` puesta y `AUTH_ENFORCED=true`).
Sin eso, `requiere_api_key` devuelve en la línea 48 y todo pasa — dos "fallos"
iniciales resultaron ser ese artefacto local, no un hueco real.

### v0.81.0 — El inventario se agrupa por FAMILIA: la publicación vive en el padre, las ventas llegan en el hijo

Reportado por Eduardo con `ORG-0841` (7-ago): el reporte lo daba por
"nunca vendió" con 127 piezas en FULL, pero su publicación de Sancor lleva
**44 ventas en 7 días**. El motivo: **la publicación está registrada con el SKU
PADRE (`ORG-0841`) y las ventas entran con el del HIJO (`ORG-0841-AZL-L`)**.
Cruzados por SKU a secas, el padre parecía muerto.

**Alcance del error: 74 de 292 filas de Inmovilizado eran falsas** (25%).

La relación buena es **`wc_parent_id`**, poblada en 7,299 productos.
`parent_sku` y `has_variations` están **VACÍAS en las 22,186 filas** de
`core.products` — son columnas muertas, no usarlas.

| | Antes | Ahora |
|---|---|---|
| Inmovilizado | 292 | **218** |
| Invisible | 85 | **83** |

Además las filas se consolidan: `DEC-0012-ROJ`, `-ROS` y `-BLN` eran tres
renglones de 230 piezas; ahora es **una familia con 687 en FULL**, que es la
unidad en la que se decide.

**Y un doble conteo que apareció al agrupar.** El `stock_own` de una
publicación de marketplace es un espejo, y en un producto con variantes el
espejo del PADRE trae el acumulado de los hijos: `DEC-0012` tiene 26,100 en su
publicación de Sancor contra 25,410 sumando sus 8 variantes en Woo. Sumar padre
+ hijos daba **51,510 — el doble del inventario real**. La regla ahora:

> Woo (`general`) es el almacén de registro y guarda una fila por variante:
> esas se **suman** entre hermanos. El espejo del marketplace solo entra si la
> familia no tiene NINGUNA fila en Woo, y ahí se toma el **máximo**, jamás la
> suma. Nunca se mezclan las dos fuentes.

**Contrastado contra la API de ML en vivo** (678 publicaciones, 35 llamadas):
217 de 218 filas de Inmovilizado coinciden exacto en unidades de FULL. El único
"falso positivo" de Invisible fue `TEC-0393-ROS`, y no es un error: **ML lo
reporta `active` desde las 20:45 de hoy** — alguien lo reactivó después de que
el reporte lo señalara. Nuestro espejo llevaba 39 h sin refrescar esa fila.

**El reporte de ventas NO se toca.** Se midió: de los 96 SKUs vendidos sin
costo capturado, 27 son variantes y solo **6** tienen un padre con costo. Y
heredar el costo del padre a una variante es una suposición distinta —dos
variantes pueden costar diferente— que además cae en "no tocar costos". Se deja
como está.

Sin migraciones y sin variables nuevas. Versión 0.81.0.

### v0.84.0 — CORTES F6 de CORE y CATEGORÍAS: los últimos dos dominios (Eduardo)

Con esto los CINCO dominios de la migración tienen su corte. Estos dos son de
naturaleza distinta a costos/pedidos/channel: **WooCommerce no se retira** (es
la fuente de verdad del catálogo), así que aquí el corte no invierte un espejo
— cambia QUIÉN mantiene a kubera y CUÁNDO:

**CORE — flag `SUPABASE_WRITE_CORE`.** Los tres seams de ciclo de vida
(nacimiento en `crear_producto`, publish en `publicar_ready`, trash/deleted de
la auditoría de `crear.py`) dejan la cola best-effort y escriben
`core.products` **SÍNCRONO en la misma petición** vía el nuevo
`services/core_write.py` — mismo upsert de siempre
(`_up_core_product`: update-por-wc_id primero, candado `solo_por_wc_id`).
Si kubera está caída: el evento cae a la cola del espejo clásico
(`espejo_kubera_log`, reprocesable desde /migracion) + Slack
(`escritura_fallback:core`) — el flujo de negocio jamás se bloquea. Flag
apagado = seam encolado de la v0.65, sin cambios.

**CATEGORÍAS — flag `SUPABASE_WRITE_CATEGORIAS`.** La elección de categoría
del panel (la que MANDA, regla 2) ganó su seam: al guardarse
(`POST /api/crear/categoria-ml`), `services/categorias_write.py` escribe
síncrono el árbol (`channel.categories`, nombre+ruta del path_from_root) y la
asignación (`channel.product_category`, source='panel') — kubera se entera al
momento, no hasta el ETL de las 06:15. El sku se resuelve por `wc_id` contra
`core.products` (por eso este corte va de la mano del de core); sin acta, el
evento queda en la cola y el reproceso lo aplica cuando el acta exista.
Handler nuevo `channel.product_category` en `kubera_mirror._UPSERTS`. Flag
apagado = hoy exacto (solo el ETL nocturno; el censo filtra `wp_postmeta`).

**Los ETLs pasan de POBLADORES a AUDITORES.** Siguen corriendo como respaldo,
pero su acta se vuelve estricta: `resultado='ok'` ya no significa "corrió" —
significa "**no tuve nada que corregir**". El conteo nuevo `seam_gap`
(insertar+actualizar que el seam debió cubrir) marca `con_deltas` si es >0 y
rompe la racha de /migracion: el MISMO criterio de 14 días en cero de los
demás dominios, ahora midiendo lo correcto. El hueco viene en CERO desde el
08-ago (verificado en las actas del 08/09/10), así que la racha no se rompe
al cambiar la vara — se vuelve honesta.

Pruebas en el SANDBOX (`backend/scripts/probar_corte_core_categorias.py`,
mismo arnés: guardia triple, MySQL/Slack stubeados, cobayas limpiadas):
**12/12 PASAN** — ciclo de vida primario (nacimiento, publish por wc_id,
candado de reciclados), elección y re-elección de categoría, wc_id sin acta →
cola → reproceso, caídas de kubera en ambos dominios y flags OFF.

Revertir = apagar el flag del dominio (independientes, cero deploys). Sin
migraciones que aplicar. Variables nuevas en Railway: `SUPABASE_WRITE_CORE` y
`SUPABASE_WRITE_CATEGORIAS`. Al cierre de las cinco transiciones (14 actas en
cero cada una): retirar flags de lectura y espejos inversos, crons deltas
fuera, tablas MySQL a legado y F8.


### v0.85.0 — La tabla de Análisis deja de resumir el margen y lo DESGLOSA (Eduardo)

Tarea: *"Pulir tabla de Análisis (quitar columnas Cobertura, Sugerido, Margen
sin fees) y agregar costos/márgenes como los más vendidos"*.

**Qué pregunta contesta ahora la tabla.** Nació como tablero de
reabastecimiento —clon del de José— y las columnas lo delataban: COBERTURA
(cuántos días dura el stock) y SUGERIDO A FULL (piezas a mandar) contestaban
"qué reponer". La pregunta que hoy se le hace es otra: "qué deja dinero". Esas
dos salen, y con ellas el **MARGEN BRUTO** — el que no descuenta los cobros del
canal. Convivía con el neto en columnas contiguas y obligaba a decidir cuál de
los dos leer; el que decide es el neto, y el bruto sigue estando canal por
canal en el modal de precio y margen (clic en Precio o en Margen).

**Entra el bloque de costos del popup "Productos más vendidos"**, con las
mismas definiciones: `Costo base · Comisión /u · Envío /u · Costo final ·
Margen · Ganancia`. Leídas de izquierda a derecha, la fila explica sola de
dónde sale su margen — que es justo lo que antes obligaba a salir de la tabla y
buscar el producto en el popup, donde además solo caben 10 SKUs por cuenta.
Nada de esto es una tasa supuesta: la comisión es la que el canal cobró en los
pedidos del período (`channel.order_items.comision`) y el precio es el
REALIZADO (ingreso ÷ unidades, ya ponderado entre cuentas).

**EL ENVÍO REAL, la pieza que faltaba.** El popup vale por eso: el estimado de
`costing.costos_finales.costo_fee_envio` miente en las dos direcciones ($349
estimados contra $88 reales en Malla Sombra; 141 SKUs con venta y flete en $0).
Ahora la tabla también consulta el cobro real del embarque a ML
(`services/envio_real.py`, caché `ml_envio_real` en MySQL) para los SKUs de la
página. Diferencia deliberada contra el popup: **ahí el margen se queda vacío
hasta tener el envío real; aquí NO**. Son 2,681 SKUs — esperar a tenerlos todos
dejaría la columna en blanco durante días. La celda muestra el envío que haya y
DICE cuál es: etiqueta `REAL` en verde o `EST` en gris, `*` cuando el real solo
cubre parte de las piezas, y el estimado viejo en el tooltip cuando difiere.
Mientras queden piezas con estimado, junto al contador de SKUs aparece
*"consultando envíos — faltan N piezas"*; la página se refresca sola cada 60 s
y cada vuelta avanza otro tanto (presupuesto `envios`, 150 embarques por carga,
cada pedido se consulta UNA vez y queda cacheado).

**Un solo cálculo para la celda, el tooltip y el ORDEN.** `_rehacer_costos()`
rearma costo final, margen y ganancia en el backend después de resolver el
envío, así que no hay dos aritméticas conviviendo. Regla que gobierna todo el
bloque, heredada del popup: sin costo base o sin comisión real **no hay costo
final** — celda vacía antes que un número inventado (por eso lo que solo vende
en Amazon sigue sin margen: comisión en cero hasta Finances API). El costo
implausible (>3× el precio) se sigue pintando en ÁMBAR con ⚠, no en rojo.

**Y una marca más, del mismo espíritu: «sin envío capturado».** 248 SKUs de
producción no tienen envío por ningún lado — ni el estimado por peso y medidas,
ni un pedido del que leer el cobro real. Su costo final se arma solo con costo
base + comisión, así que el margen sale optimista; sin marca se leían igual de
firmes que los completos. Ahora ese costo final va en ÁMBAR con la nota debajo,
y la etiqueta NOMBRA lo que falta: "sin envío capturado", no un genérico "sin
costo" — la columna de al lado es Costo base, y ahí un "sin costo capturado"
haría creer que lo ausente es esa otra cosa.

**Comisión y envío se explican SOLOS al pasar el cursor.** Las dos celdas
muestran un promedio PONDERADO por unidades, y un promedio sin desglose no dice
qué cuenta lo está jalando: TEC-2162-NEG cobra $92.21 por pieza en BEKURA y
$105.87 en SANCORFASHION, y la celda decía $97.94 a secas. Ahora el backend
manda el desglose (`comisiones` por canal/cuenta desde `channel.order_items`,
`envios` por cuenta desde las líneas de pedido) y la celda abre un panel al
pasar el cursor — sin clic y sin modal, que para leer dos renglones sería peor
que el problema. El panel va posicionado FIJO (no `absolute`) porque la tabla
vive en un `overflow-x-auto` que lo recortaría, y se pinta ARRIBA de la celda
cuando no cabe abajo. La comisión declara además cuántas piezas del período no
aportaron comisión (las de Amazon, que la reporta en cero); el envío dice por
cuenta cuántas piezas ya tienen cobro real y cuántas siguen pendientes, con
cuatro textos distintos según haya cobro real, estimado, ninguno de los dos con
ventas esperando, o ninguno de los dos sin ventas — decir "todavía se muestra el
estimado" cuando no hay estimado es de lo que hace desconfiar de toda la tabla.

De paso, el desglose por cuenta se calcula AUNQUE no haya MySQL: las líneas de
pedido salen de kubera y solo el cobro real necesita el caché y los tokens de
ML. En staging el panel sigue diciendo qué cuenta vendió y cuántas piezas están
esperando su cobro — falta el número, no el contexto.

**Orden nuevo**: `costo`, `comision`, `costo_final`, `margen_neto` y
`ganancia`. El de ganancia es el que faltaba para la pregunta en pesos — un 60%
sobre tres piezas pesa menos que un 15% sobre trescientas. Ordenar por margen
ascendente sigue siendo el "filtro" de lo que vende mal. Salen del whitelist
`cobertura`, `sugerido` y `margen`; el orden se decide sobre las ~2,700 filas
con el envío ESTIMADO (el refinamiento real solo alcanza a las 50 de la página):
mueve centavos en el margen, no el ranking, y queda dicho en el código.

`channel.restock_panel` **no se toca** — se quitó la columna, no el cálculo; su
CTE sale de la consulta porque ya nadie lo lee. Sin migraciones. UNA variable
nueva, `TABLA_ENVIO_REAL_PRESUPUESTO` (default 150): cuántos embarques consulta
la tabla a ML por carga. En 0 la tabla usa solo lo ya cacheado y deja de llamar a
ML — es el apagador sin deploy si esas llamadas estorban, porque son lo único de
esta vista que sale a un tercero y la página se refresca cada 60 s. El desglose
por cuenta del panel NO depende de ella: sale de kubera. Probado en el SANDBOX (`APP_ENV=staging`), donde `MYSQL_ENABLED=false`
deja sin caché de envíos y toda la columna cae al estimado marcado `EST`: es
exactamente el camino de degradación que se quería verificar. Versión 0.85.0.

**Herramienta nueva: `backend/scripts/actualizar_sandbox.py`.** El sandbox se
quedaba días atrás y las pantallas se probaban contra cifras viejas (`sembrar_
sandbox.py` solo trae 300 SKUs de costos desde MySQL: ni listings ni pedidos).
Este copia por UPSERT `core`, `channel` y `costing` desde la BD kubera de
producción — que se lee en transacciones READ ONLY — sin borrar nada, así que
no propaga bajas y respeta las filas propias del sandbox. Tres cosas que costó
descubrir y quedan documentadas en su cabecera:

1. **El pooler filtra estado de SESIÓN.** Las dos DSN entran por el pooler de
   Supabase en modo transacción (6543): varios clientes se turnan la misma
   conexión del servidor. Abrir producción con
   `options=-c default_transaction_read_only=on` —la protección obvia— deja
   conexiones del pool en read-only para QUIEN SEA que las tome después, el
   backend de Railway incluido. Así se envenenó el pool del sandbox el 10-ago y
   `aplicar_migraciones.py` falló con "cannot execute CREATE EXTENSION in a
   read-only transaction" sin que nadie hubiera tocado nada. El candado correcto
   es `set transaction read only`, que muere con la transacción.
2. **Los triggers falsean la copia.** `touch` pone `updated_at = now()` e `hist`
   escribe `listing_history` / `cost_history`: copiando con ellos activos el
   sandbox queda con todas las fechas del momento de la copia y una historia de
   cambios inventada — así llegó `costing.cost_history` a 19,767 filas contra 52
   en producción. Se apagan por tabla dentro de la misma transacción y se
   verifica al final que quedaron activos.
3. **Las secuencias se quedan atrás.** `listing_history` estaba en 19,220 con
   ids hasta 42,551 (carga vieja), así que cualquier UPDATE a un listing chocaba
   contra la PK al escribir su historial. El script hace `setval` de todas las
   secuencias de los tres esquemas.

Corrida del 10-ago: sandbox al día con producción (22,186 productos · 19,678
listings · 12,810 pedidos), con `listings.max(updated_at)` IDÉNTICO al de
producción — la prueba de que no se falsearon fechas. `aplicar_migraciones.py`
completo solo sirve para un sandbox nuevo (la `0001` no es idempotente): al
sandbox existente se le aplicó a mano la `0009`, que le faltaba.


### v0.86.0 — Las columnas de costo se explican solas al pasar el cursor (Eduardo)

Continuacion directa de v0.85.0: ahi entro el bloque de costos, y con el la
pregunta de siempre — "de donde sale este numero?". El `title` nativo del
navegador no puede contestarla: solo sabe pintar texto corrido, y lo que estas
columnas tienen que mostrar es una CUENTA. En renglones se lee de un vistazo; en
un parrafo no se lee. Cuatro columnas mas pasan a la tarjeta flotante que ya
estrenaron Comision y Envio:

**Costo base** — el costo por pieza convertido en lo que costo todo lo vendido
(`$813.63 x 584 piezas = $475,160`), y el aviso de costo poco creible. Ese aviso
hasta ahora solo vivia en Margen y en Ganancia, que son las VICTIMAS; ahora
tambien aparece en el ORIGEN: "es 4.5x el precio al que se vendio ($179.44)".

**Costo final** — la suma en renglones con linea de total: costo base +
comision + envio (etiquetado real o estimado). Cuando falta el envio, el aviso
ambar explica que ese total va incompleto y que el margen sale optimista.

**Ganancia** — la cuenta completa, que es lo que mas faltaba: precio real de
venta - costo final = deja por pieza, x piezas vendidas = ganancia del periodo.
Un renglon con signo negativo explica una perdida mejor que cualquier tooltip.

**Visitas · CR%** — visitas, unidades de ML y conversion, mas la LECTURA del
par: arriba de 5% dice que si vende poco es porque no la ven; abajo, que el
problema esta en la ficha o el precio. En ambar los dos matices que la celda no
puede callar: unidades de Amazon que no cuentan (no aportan visitas) y ventanas
en las que ML devolvio menos dias de los pedidos.

MARGEN se queda con el tooltip viejo a proposito: esa celda es un boton que abre
el modal por canal, y una tarjeta al pasar el cursor competiria con el clic.

Verificado que la tarjeta no se recorta ni en la columna mas a la izquierda
(Visitas) ni en la ultima fila de la pagina, donde se voltea hacia arriba. Se
retiro el componente `Cifra`, que ya no usaba nadie. Solo frontend: sin
migraciones, sin variables y sin cambios de API. Version 0.86.0.

### v0.87.0 — Análisis: dos columnas más que se explican solas, y fuera el "?" repetido

Tres ajustes a la v0.86.0, pedidos por Eduardo (8-ago).

**Fuera la multiplicación del Costo base.** El panel mostraba
`Por pieza $4,229.19 × 183 piezas vendidas = $773,942`. Ese total no es de esa
columna: el costo del período ya lo cuentan Costo final y Ganancia, cada uno
con los cobros que le tocan. Aquí se leía como si fuera un total propio y
competía con ellos. Queda solo el costo por pieza y su explicación.

**Uds · $Venta** y **FULL · Propio** ganan panel al pasar el cursor, como las
columnas de costo:

- *Uds · $Venta* — piezas, importe y **precio promedio** (importe ÷ piezas,
  que es de donde sale la columna Precio venta), más la aclaración de que es
  del período elegido, suma TODAS las cuentas y es venta bruta. Cuando no
  vendió, lo dice sin ambigüedad: habla del período, no de la publicación.
- *FULL · Propio* — las dos bodegas por separado con la advertencia que evita
  la confusión más cara del panel: **no se suman**. Una publicación FULL solo
  surte de la bodega de Meli, así que reponer es mover de Propio a FULL, no
  comprar. Si vendió y FULL está en cero, el panel dice si hay piezas propias
  con qué reponer.

**El "?" del encabezado ya no sale por `id`, solo si la columna lo pide con
`info`.** Donde la celda se explica sola al pasar el cursor, el signo repetía
lo mismo. Reparto final:

| Sin "?" (la celda tiene panel) | Con "?" (no hay panel) |
|---|---|
| Visitas · Uds/$Venta · FULL/Propio · Costo base · Comisión · Envío · Costo final · Ganancia | Producto · Estado · Edad s/v · Precio venta · Margen |

Esas cinco conservan el "?" porque su celda no tiene panel —Precio venta y
Margen solo llevan `title` nativo—. Cuando lo tengan, se les quita el `info` y
listo.

Verificado en el navegador contra el sandbox: los tres paneles pintan su
contenido, el de Costo base ya no trae la multiplicación, el reparto de "?"
quedó como arriba y la página no desborda. De paso se corrigió un "1 piezas
propias" que salía en singular. Sin migraciones y sin variables nuevas.
Versión 0.87.0.

### v0.88.0 — Fuera la lectura de dailytrackMeli: el proyecto ya no existe

`dailytrackMeli` (`xaxbkijc…`) **desapareció**: su hostname dejó de resolver
—tres intentos, mientras `supabase.com` y los otros dos proyectos resuelven
bien—. Antes de eso su Postgres llevaba ~2 semanas devolviendo
`53100: No space left on device`, que en el plan gratuito **restringe la
organización entera**: por eso el panel de Supabase marcaba servicios
restringidos aunque kubera solo ocupa 183 MB de 500.

`presencia.py` seguía llamándolo en **cada carga de la página Productos**. La
llamada fallaba siempre y solo llenaba el log. Se retira.

**No se reemplaza porque no hace falta.** El bloque de arriba ya lee
`channel_read.presencia()` → `channel.listings`, que es la misma información y
mejor. Censo del 7-ago contra la API de Mercado Libre:

| | `products_snapshot` | `channel.listings` |
|---|---|---|
| Publicaciones de Sancor | 1,000 de 2,320 (**43%**) | 2,320 (**100%**) |
| Acuerdo con ML | perdió **44 de 44** | **99.8%** |

El snapshot no solo estaba viejo (último 27-jul): le faltaba **el 59% de una
cuenta**, seguramente por un tope de paginación o una corrida cortada. En los
24 SKUs donde las dos fuentes discrepaban, ML le dio la razón a `listings`
**siempre**.

Tampoco se pierde nada más: la URL de los puntos de esta vista no se usa, y el
enlace «Ver publicación» del Estudio sale de `studio.metadata`, no de aquí.

Verificado contra producción: 40 SKUs resueltos en 0.28 s, los 40 con Mercado
Libre y Amazon — y con MySQL apagado, o sea **todo desde `channel.listings`**.
Sin migraciones y sin variables nuevas. Versión 0.88.0.

**Queda huérfano** `services/supabase_rest.py`: ya no lo importa nadie (solo
quedan menciones en comentarios de `config.py`). Su retiro, junto con las
variables `ANALYTICS_SUPABASE_*` de Railway, es una limpieza aparte — ojo con
el fallback: si se borran esas variables sin quitar el módulo, `_analytics_url()`
cae a `SUPABASE_*` y le pediría `products_snapshot` a kubera, donde no existe.

### v0.89.0 — Análisis: Edad s/v se explica sola, y las filas que venden sin costo se ven

Dos ajustes a la tabla de Análisis, más un script de mantenimiento que todavía
NO se ha corrido contra producción.

**Edad s/v gana panel** y pierde el "?" del encabezado, con lo que el reparto de
la v0.87.0 se reduce a cuatro columnas con signo (Producto · Estado · Precio
venta · Margen).

Lo que el panel dice y el número solo no podía: **esta columna no respeta el
período de arriba.** Uds y $Venta miran los días elegidos; la edad sale de
`max(date)` sobre todo el historial, sin filtro de fechas. Por eso un producto
puede tener 0 piezas vendidas en 60 días y aun así edad de 3 días —vendió justo
antes de que empezara la ventana—, y leído sin saber eso el número parece
contradecir la celda de al lado.

El panel también separa dos vacíos que se veían igual: el guion **no es un dato
que falte**, es que ese SKU nunca ha vendido. No es un caso raro: **12,358 de
los 13,475 SKUs listados** jamás han registrado una venta. Y cuando hay stock
encima, lo dice con la cifra.

**Las filas que vendieron SIN costo capturado ahora se ven.** Su Costo final,
su Margen y su Ganancia salen vacíos, así que ordenar por cualquiera de esas
tres columnas las mandaba al fondo justo cuando más había que verlas.

El criterio de a quién marcar importa tanto como la marca:

| | SKUs | Marca |
|---|---|---|
| Sin costo capturado | 5,493 de 13,475 (41%) | guion en gris normal, ya no casi invisible |
| **Sin costo y CON venta en el período** | **~126** | **franja ámbar en el renglón + guion ámbar en negritas** |

Marcar los 5,493 habría vuelto la marca invisible por repetición. Un SKU dormido
sin costo es una tarea de captura; uno que vendió 159 piezas y $48,376 sin costo
es un hueco en el estado de resultados. La franja va en el **borde izquierdo**
justamente para que no dependa del orden ni del scroll horizontal; las filas no
marcadas y la cabecera llevan un borde transparente del mismo ancho para que las
columnas no se corran. El panel de Costo base cuantifica cuánto se movió a
ciegas.

Verificado en el navegador contra el sandbox: las dos ramas de cada panel, 2
filas marcadas de 50 en la vista por defecto (proporcionado, no invade), y los
tres bordes izquierdos —cabecera, fila normal y fila marcada— midiendo 4px.

**`backend/scripts/actualizar_comision.py` (nuevo, todavía sin correr en
producción).** El 99% del catálogo tiene la comisión de un solo lote del
24-jul, con promedio 13.13%; lo que ML cobró de verdad en 60 días fue 16.19%
—$92,892 sobre $3.7M de venta—. Todo lo recalculado individualmente desde
agosto sale en 17–19%, así que el motor está bien y lo que está mal es el dato
guardado.

No se usa «Regenerar costo» del panel porque ese camino manda `auto_cbm=true` y
`sincronizar_woo=true`: rederiva el flete del contenedor desde las dimensiones,
recalcula los precios y **los empuja a WooCommerce**, pisando los puestos a
mano. El script toca `pct_comision` y `costo_comision`, nada más; el precio solo
con `--con-precio`, y aun entonces con el costo y el envío ya guardados.

Cuatro candados: dry-run por defecto, `--real` exige nombrar la ref destino a
mano, `--real` se niega si el corte F6 está apagado (lee de kubera como
primaria), y escribe por `costing_write.guardar_finales`, heredando cola de
reproceso y alertas. El porcentaje sale de la API de ML y, si no contesta, del
promedio real medido por categoría en nuestros propios pedidos; fuera de
[5%, 25%] se descarta por absurdo —el filtro que faltó el 24-jul, cuando
quedaron 316 SKUs con 0% guardado como cobro real—.

Probado end-to-end contra el **sandbox** sobre 5 SKUs al azar, comparando la
fila completa antes y después: `costos_validados` sin un solo cambio y, en
`costos_finales`, solo `pct_comision` y `costo_comision` entre las columnas de
negocio. `cost_history` y `ops.process_log` registraron cada cambio atribuido al
script por nombre. **Falta ejercitar la ruta de la API de ML**, que necesita
tokens: la primera corrida en producción debe ser en seco y con `--sku`.

Sin migraciones y sin variables nuevas. Versión 0.89.0.

### v0.90.0 — El acta estricta de core y categorías mide el hueco del seam, no el trabajo del ETL

El 11-ago las actas de **Maestro** y **Categorías** salieron `con_deltas` y
rompieron las rachas del corte F6 sin que nada hubiera fallado. Las dos alertas
eran falsas por construcción: el criterio que estrenó la v0.84.0 contaba como
"hueco del seam" cualquier fila que el ETL tuviera que escribir, incluidos
campos que **ningún seam escribe ni puede escribir**.

**Qué pasó ese día.** En core, 38 updates y 0 inserts:

| Campo que cambió | SKUs | Quién lo escribe |
|---|---|---|
| `odoo_id` | 35 | solo este ETL (viene de Odoo) |
| `name` | 2 | el panel… o quien edite por fuera |
| `source` | 1 | lo recalcula este ETL |

Los 35 son un bloque consecutivo de `odoo_id` 125119–125153: una carga en lote
del lado de Odoo que le puso id a SKUs que ya existían. El seam del corte
(`kubera_mirror._up_core_product`) escribe `name`, `wc_id`, `status` y `source`
— y su propio comentario ya decía que lo que enriquecen otras vías (`odoo_id`…)
no se pisa. El acta le estaba reclamando al corte un campo que el corte nunca
prometió cubrir. **De los 38, solo 2 eran señal real**: dos títulos editados
fuera del panel, que es exactamente lo que la regla debe detectar.

En categorías, las asignaciones —lo único que el seam del panel posee— salieron
impecables (0 insert, 0 update, 13,722 sin cambio). El `con_deltas` lo
provocaron **2 nodos del árbol de ML** que cambiaron de nombre o ruta: Mercado
Libre renombrando sus propias categorías.

**El ajuste.** `seam_gap` ahora cuenta solo lo que un seam pudo haber cubierto:

- **core**: `CAMPOS_SEAM = (name, wc_id, status)`. Fuera `odoo_id` y
  `wc_parent_id` (los llena el ETL desde Odoo y Woo) y `has_variations`, que
  además está muerta —vacía en las 22,186 filas—. `source` queda fuera aunque el
  seam lo escriba: el ETL lo recalcula como la lista de tablas MySQL donde
  aparece el SKU, así que contra el `panel_crear` del seam siempre difiere. Un
  INSERT sí cuenta siempre — un SKU que nace sin pasar por el panel es
  precisamente el hueco que se está midiendo.
- **categorías**: solo asignaciones, más los nodos de árbol que entran **por una
  elección del panel** (si el panel eligió una categoría y su nodo no está,
  `categorias_write.registrar` falló). Que ML agregue o renombre categorías es
  trabajo normal del ETL.

Con el criterio nuevo, el 11-ago habría dado **core 2** (los dos títulos
editados fuera del panel) y **categorías 0**.

El ETL sigue aplicando todos esos updates: lo que cambia es qué se cuenta como
hueco, no qué se escribe. Y para que la próxima alerta no obligue a bucear en
los logs de Railway, el acta gana dos contadores nuevos —
`updates_fuera_de_seam` en core, `arbol_fuera_de_seam` en categorías — y el
reporte de core una `muestra_seam_gap` con los SKUs culpables y sus campos.

Probado con dry-run contra producción (lectura pura, sin `--real`): core
0 insert / 0 update / 22,186 sin cambio, categorías 0/0 con 13,722 asignaciones
sin cambio.

Sin migraciones y sin variables nuevas. Versión 0.90.0.

### v0.91.0 — Análisis: fuera el precio promedio del panel de Uds · $Venta

Ajuste chico pedido por Eduardo (11-ago). El panel de **Uds · $Venta** mostraba
un tercer renglón, *Precio promedio (importe ÷ piezas)*, que se retira.

Repetía un dato que ya está en la tabla: **`importe ÷ piezas` es exactamente la
columna Precio venta**, que el SQL arma con el mismo `venta / uds`
(`coalesce(v.venta / nullif(v.uds, 0), l.precio)`). En la fila con la que se
verificó, el panel decía $1,962.54 y la columna Precio venta, dos a la derecha,
$1,962.54.

Es el mismo criterio de la v0.87.0 al quitar el `× N piezas vendidas` del Costo
base: una cifra derivada que compite con la columna que ya la muestra. Queda
anotado en el comentario del componente para que no reaparezca.

El panel conserva piezas, importe y la aclaración de que es del período elegido,
suma todas las cuentas y es venta bruta. Bajó de 145 a 121 px de alto.

Verificado en el navegador contra el sandbox: el panel ya no menciona el precio
promedio, sin errores de consola. Sin migraciones y sin variables nuevas.
Versión 0.91.0.

### v0.92.0 — El webhook de WooCommerce: el registro civil se entera de las ediciones a mano

`POST /api/webhooks/woo`. Nace de una alerta del 11-ago que resultó tener razón:
el acta de Maestro salió `con_deltas` porque **OFI-0079-BLN tenía en Woo un
título distinto al que el panel había registrado**, y el ETL de las 06:15 tuvo
que corregirlo.

El barrido del catálogo explicó por qué, y no era un caso aislado:

| Señal | Medida |
|---|---|
| Fichas guardadas alguna vez desde wp-admin | **444** (montefeni 231, Brandon 65, valeria 63, Thalia 28, José 21, cinthya 16, Andrea 16) |
| SKUs con título dejado por el panel en `crear_logs` | 253 |
| …de esos, con OTRO título hoy en Woo | **39**, los 39 modificados DESPUÉS del paso del panel |
| Productos publicados cuyo slug ya no corresponde al título | 1,339 de 3,509 (38%) |

Y las ediciones son **correcciones legítimas**: "20 moños" → "40 moños",
"Product Not Available" → el título de verdad, el caso TEC-1812-NEG que ya vive
en el CLAUDE.md. La gente arregla fichas en WordPress, como debe ser.

**El problema no era de disciplina sino de arquitectura.** Los tres seams del
corte F6 de core (nacimiento, publish, auditoría) cubren lo que pasa *por el
panel*. Nada de lo que se edite fuera existe para ellos, y no se arregla
poniendo un cuarto y un quinto avisador: hay que **escuchar en la fuente**.

WooCommerce trae webhooks nativos. Con `product.updated` apuntando a este
endpoint, cada cambio llega con la ficha completa venga de donde venga —
wp-admin, la API REST, otro plugin, WP-CLI— y de ahí salen `sku`, `name`,
`wc_id` y `status`, que es exactamente lo que el registro civil necesita. El
evento entra por `core_write.registrar`, el mismo camino de los otros tres
seams: escritura síncrona a `core.products` y, si kubera está caída, cola en
`espejo_kubera_log` reprocesable.

Decisiones que vale la pena conocer:

- **GUARDA ABSOLUTA**: responde 200 siempre, incluso con kubera caída o con la
  petición rota. Woo deshabilita un webhook tras 5 entregas fallidas seguidas y
  el síntoma sería silencioso — dejan de llegar eventos y nadie se entera hasta
  que el acta lo delata semanas después.
- **Sin firma no se escribe.** Se valida `X-WC-Webhook-Signature` (HMAC-SHA256
  del cuerpo en base64). Sin secreto configurado el endpoint queda en
  OBSERVACIÓN: registra lo que llega y no toca el maestro. Una firma que no se
  puede verificar no autoriza a escribir en el registro civil.
- **Caché por SKU**: `product.updated` dispara con CADA cambio, incluidos los
  nuestros — el sync de inventario cada 15 minutos y el descuento de stock de
  cada venta. Si la terna (name, status, wc_id) es idéntica a la del evento
  anterior, se descarta sin viajar a kubera. El upsert ya era no-op, pero el
  viaje no.
- **Límite conocido**: no cubre escrituras directas a la base de WordPress.
  Es justamente el caso del único de los 39 sin `_edit_last`, que sigue sin
  explicación.

`GET /api/webhooks/woo` responde el estado (para que wp-admin valide la URL al
guardar) y `GET /api/webhooks/woo/log` (con API key) muestra qué llegó y qué se
hizo con cada evento.

Pruebas sandbox `backend/scripts/probar_webhook_woo.py`: **9/9 pasan** — ping de
alta, firma inválida, sin secreto, flag apagado, alta del acta, evento repetido,
edición fuera del panel seguida en vivo, ficha sin sku y kubera caída.

Variables nuevas: `WOO_WEBHOOK_ENABLED` (default false) y `WOO_WEBHOOK_SECRET`.
**Nace APAGADO**: encenderlo es crear el webhook en wp-admin de producción y
poner las dos variables — flujo vivo, va con el dale de Brandon.

Sin migraciones. Versión 0.92.0.

---

### v0.93.0 — `enrich.ai_content`: el contenido de IA deja de vivir en cuatro lugares (Eduardo)

José pidió (Slack, 7-ago) que el prompt se genere para todo el catálogo de una
vez y luego por producto al publicar. Son dos modos sobre el mismo dato y
**ninguno tenía dónde escribir**. Antes de construir el generador había que
resolver la persistencia.

El prompt produce **cinco campos** por producto: título, bullets, descripción,
atributos y `backend_search_terms` (éste se mide en BYTES, no caracteres — 249
máx. en Amazon). Hoy sólo uno de los cinco tiene casa, y mal:

- **MySQL `atributos_ia`** (5,380 SKUs, 4,355 con JSON) está **congelada desde
  el 22-jul**: no hay un solo INSERT/UPDATE contra ella en todo `backend/`, la
  única mención viva es un docstring. Guarda solo atributos y no tiene columna
  de canal — todo es Mercado Libre implícito. Su columna `flags` (el
  razonamiento de descarte de la IA) no existe en ningún otro lado.
- **Metas de Woo** `ml_attributes` (208) y `ml_attr_<X>` (777 por ID + 208 por
  nombre en español): el mismo atributo bajo dos convenciones.
- **`enrich.ai_attributes`** (esta BD, migración 0001): existía, **vacía y sin
  escritor** — no aparece en el mapa de UPSERTS de `kubera_mirror.py` ni en
  `KUBERA_MIRROR_TABLAS`. Conteo verificado contra sandbox Y producción: 0
  filas en ambos. Y arrastraba los dos mismos defectos: PK de un solo campo y
  solo `attributes`. Se diseñó antes de que existiera este requerimiento.

**La tabla nueva tiene PK `(sku, canal, cuenta)`**, con `canal` referenciando
`core.channels`. No es `(sku, canal)`: en ML hay dos cuentas que pueden
publicar el MISMO SKU en categorías distintas, y los atributos derivan de la
categoría (caso `EST-0091`, ya documentado). Es la misma llave que ya usan
`canal_inventario` y `channel.listings`.

`estado` es el backlog (`pendiente` → el "córrelo para todos" procesa
pendientes; el "por cada uno al publicar" inserta en pendiente). **No existe
`publicado`**: ese evento vive en `ml_progress`/`amazon_progress` y dos tablas
diciendo lo mismo se contradicen. En su lugar está `obsoleto`, que es lo que
`hash_woo` necesita — la huella del producto en Woo al generar, para saber qué
regenerar cuando cambie. **No meter el `updated_at` de Woo en ese hash**:
cualquier toque irrelevante marcaría todo el catálogo como obsoleto.

Probado en sandbox con transacción revertida (cero filas persistidas): la FK
rechaza `canal='meli'` (el id correcto es `mercado_libre`), el CHECK rechaza
`estado='publicado'`, la FK rechaza SKUs fuera de `core.products`, y el mismo
SKU convive en BEKURA y SANCORFASHION.

Migración **0010**, aplicada en sandbox y en la BD kubera. La tabla nace
**vacía**: la siembra desde `atributos_ia` (4,355 JSON con sus `flags` y su
`modelo_ia`) queda pendiente como backfill en `kubera_mirror.py`, y siembra
uno de los cinco campos — los otros cuatro hay que generarlos igual. El retiro
de `enrich.ai_attributes` va en migración aparte, ya con el conteo confirmado.

Propuesta completa: `PROPUESTA_CONTENIDO_IA.md`. Versión 0.93.0.

---

### v0.94.0 — `enrich.market_*`: el destino de Competencia, con canal desde el principio (Eduardo)

El módulo de Competencia se construyó contra un SQLite local y se subió a
Supabase en un esquema aislado (`propuestas`) para no tocar los esquemas del
equipo mientras se validaba. Ya está en vivo: **1,584 SKUs · 3,118
publicaciones · 3,000 filas de ranking · 1,816 de búsqueda · 5,789 términos**.
Toca pagar la deuda y darle destino definitivo.

El plan se sometió a revisión de tres agentes independientes antes de tocar
nada (`PLAN_COMPETENCIA_v2.md`). Los tres, por separado, señalaron lo mismo:

**1. Faltaba `canal`.** Las tablas propuestas no lo tenían y `sku_market_config`
tenía PK de un solo campo — el mismo defecto que acababa de costar el retiro de
`atributos_ia` y `enrich.ai_attributes`. Lo irónico: el esquema `propuestas`
que se va a borrar **ya lo tenía resuelto** (*"El canal está desde el principio
para que un ASIN de Amazon sea otra fila y no un rediseño"*), y el router que
el plan conserva ya recibe `canal` como parámetro. Se estaba borrando previsión
ya escrita. Las cinco tablas nacen con `canal` en la PK y FK a `core.channels`.

**2. Las métricas no van en `channel.listings`.** El plan quería colgarle 5
columnas (title, sale_price, visits_30d, units_30d). El análisis de seguridad
era correcto —`channel_mirror.py` usa listas explícitas de columnas y el
trigger `fn_listing_history` no las mira—, pero se retiran por otras tres
razones: se perdía la dimensión `periodo` (esa tabla es el ESTADO ACTUAL del
listing, no una ventana de 30 días); `visits_30d` no tiene equivalente en
Amazon (el sustituto es BSR + Buy Box, no es que quede NULL: el concepto no
existe); y existe `etl_channel_listings.py` con un `truncate channel.listings`
cuya lista de columnas **ya está desactualizada** (le faltan las de 0004 y
0009). Van a `enrich.market_listing_metrics`, PK `(sku, canal, cuenta, periodo)`.

**3. Faltaban RLS, grants e índices.** El patrón del repo los da tabla por
tabla; el `alter default privileges` de la 0001 solo aplica si las crea el
mismo rol. Y el esquema viejo tenía índices parciales sobre `es_nuestro` —los
que arman la corona— que el DDL nuevo no reproducía.

**4. El `drop` no va el mismo día**, y el diff de verificación tiene que cubrir
los ~18 endpoints de lectura, no solo `/vista`.

**5. Prefijo de dominio.** `search_results` a secas es demasiado genérico para
un `enrich` compartido que ya tiene `supplier_data`, `ai_attributes`,
`product_media`, `odoo_viability` y `ai_content`.

**Migración 0011** — cinco tablas: `market_bestsellers`, `market_search_results`,
`market_terms`, `market_sku_config` y `market_listing_metrics`, con RLS, grants
y cuatro índices. **Migración 0012** — `channel.categories` gana `parent_id`,
`root_id` y `root_name`. Ambas aplicadas en sandbox y en la BD kubera; las
tablas nacen vacías y las 2,692 filas de categorías quedaron intactas.

**El backfill del árbol no llama a la API de ML.** El plan estimaba una pasada
por `GET /categories/{id}` para ~988 categorías; son 2,692 y **cero llamadas**:
el árbol completo ya está descargado offline en `wp_ml_categorias` (12,256
categorías, todas con `parent_id`, 31 raíces). Cobertura medida: 2,692 de
2,692. `backend/scripts/backfill_categories_arbol.py` lo recorre en memoria —
dry-run por default, `--destino prod` + `--acepto-destino` para producción,
idempotente, y **reporta las no cubiertas en vez de asumir cero** porque ese
árbol lo mantiene un proceso ajeno al panel. Aplicado: 2,692 con raíz, 30
raíces distintas.

⚠️ **No parsear `path` para sacar la raíz**: usa DOS separadores distintos
(`›` en 2,612 filas y `>` en 2) y guarda nombres, no ids. Y el backfill **no es
one-shot**: `etl_channel_categories.py` inserta las categorías nuevas con esas
tres columnas en NULL.

**Pre-chequeo de colisión de PK, corrido contra producción en solo lectura**:
cero colisiones por la llave nueva en las cuatro tablas que migran. El
`insert…select` del paso 3 no va a perder filas.

**`schema_manifest.json` regenerado.** Estaba roto desde antes de este trabajo:
le faltaban `channel.order_items`, `enrich.ai_content`, tres vistas de 0005/0007
y cinco columnas de `channel.listings` (de 0004 y 0009). El chequeo de paridad
llevaba semanas reportando diferencias heredadas. Ahora da **PARIDAD OK**:
41 tablas, cero faltantes, cero extras.

Falta el paso 3 (migrar el dato de `propuestas`), las vistas, el repunte del
backend y el retiro del esquema — que va en dos tiempos, con `rename` primero.
Y `competencia_resultados` tiene 295 filas, no cero como decía el plan: hay que
decidir si se tiran o se archivan antes del `drop`. Versión 0.94.0.

---

### v0.94.1 — Competencia paso 2: las imágenes, y dos escrituras que se cancelan (Eduardo)

`backfill_product_media_wc.py` copia las imágenes de Competencia a
`enrich.product_media` con `kind='wc'`. **1,572 filas** (el plan estimaba
~1,541), aplicado en la BD kubera, segunda corrida idempotente. La tabla queda
con 1,572 `wc` + 522 `amazon`.

**No toca WooCommerce.** El plan decía "backfill desde WooCommerce", pero la URL
ya está capturada en `propuestas.competencia_skus.imagen` (1,572 de 1,584 SKUs),
así que es un `insert…select` dentro de la misma base. Además de ser instantáneo,
esquiva el 403 intermitente del WAF de Hostinger (pendiente conocido #1).

Idempotente **por construcción**, no por procedimiento: el índice único
`uq_product_media_sku_kind_url` existe desde la migración 0002 y el insert usa
`on conflict do nothing`. (La revisión del consejo lo había marcado como riesgo
de duplicados leyendo solo la 0001 — falsa alarma, el índice está en la 0002.)

**Se cancelan las dos filas sueltas del paso 2.** El plan v1 quería insertar una
fila en `channel.product_category` (CAM-0030-IND) y otra en `channel.listings`
(TEC-0631-PLA/BEKURA, la única de las 3,118 sin fila por PK). Las necesitaba
porque ahí iban a vivir las métricas. Al moverlas a
`enrich.market_listing_metrics` —que **no tiene FK a `channel.listings`**— dejan
de hacer falta: TEC-0631-PLA está en `core.products` y sus 2 filas de métricas
migran igual; CAM-0030-IND trae su categoría en `competencia_skus` y el
`coalesce` de la vista la resuelve. Dos escrituras menos a tablas del equipo, y
`channel.listings` sí está auditada por `comparar_channel.py`.

También resuelto: **`source='real'` sí existe** — 940 filas en producción, junto
a `predictor` (5,277), `panel` (5,166) y `costos_ml` (2,340). Lo desactualizado
es el comentario del DDL (`'ml_ia'|'manual'|'woocommerce'`), no el dato.

Verificado antes de escribir: ningún `comparar_*.py` audita `product_media` y la
tabla no tiene triggers — no mueve ninguna acta. Versión 0.94.1.
### v0.95.0 — Desmantelamiento de CHANNEL, paso 1: el interruptor del espejo inverso

Primera pieza del retiro de un dominio migrado. Channel es el ensayo elegido:
22 días de actas en cero, cero lectores externos de `canal_inventario` (censo
del 10-ago: clones de los 7 repos + muestreo de conexiones, solo Railway), su
propio servicio de deltas en Railway, y el diseño auto-sanable del corte — si
algo sale mal, el ciclo de 15 min lo repara solo.

Flag nuevo `CHANNEL_ESPEJO_INVERSO` (default **true** = comportamiento de hoy,
este deploy NO cambia nada). En false, cada tanda del sync deja de copiarse a
`canal_inventario`: MySQL queda congelado a propósito, que es el primer
movimiento del retiro.

Lo que este flag NO toca, a propósito:

- **El respaldo de emergencia sigue vivo.** Con kubera caída, la tanda se
  escribe a MySQL como en el mundo viejo y el siguiente ciclo auto-sana kubera.
  Ese camino es de resiliencia, no de migración, y se queda hasta F8.
- Las lecturas F5 (`SUPABASE_READ_CHANNEL` con fallback a MySQL) siguen como
  están; su retiro es el paso 3, días después, cuando MySQL congelado confirme
  que nadie lo extraña.

Al encender el retiro (apagar el flag) hay que apagar EN EL MISMO MOVIMIENTO el
cron `deltas-channel`: con MySQL congelado, el acta compararía contra una foto
vieja y reportaría divergencia por construcción. La racha de channel (22 al
11-ago) queda CERRADA como cumplida en ese momento — el acta ya no corre más.

Pruebas sandbox `backend/scripts/probar_retiro_channel.py`: **5/5 pasan** —
con flag el espejo copia, sin flag MySQL queda congelado y kubera recibe, y con
kubera caída MySQL absorbe la tanda (la emergencia intacta).

Apagar el flag es flujo vivo: va con dale de Brandon. Sin migraciones; una
variable nueva. Versión 0.95.0.

### v0.95.1 — Channel: el retiro ENCENDIDO (flag apagado + acta retirada)

Ejecución del paso 1 con dale de Brandon (11-ago, vía Eduardo):
`CHANNEL_ESPEJO_INVERSO=false` en producción y el cron `deltas-channel`
convertido en aviso de retiro — su `startCommand` ya no compara nada, imprime
que `canal_inventario` está congelado a propósito. La racha de channel cierra
en 22/14, cumplida. Reversa completa: flag a true + restaurar el startCommand
de `railway.deltas-channel.json`; el ciclo de 15 min repuebla MySQL solo.

---

### v0.96.0 — Competencia paso 3: las 15,307 filas ya viven en `enrich.market_*` (Eduardo)

`migrar_competencia_enrich.py` movió el dato de `propuestas.competencia_*` a
las cinco tablas nuevas, todo dentro de la BD kubera con `insert…select`:

| destino | filas | esperado |
|---|---|---|
| `market_bestsellers` | 3,000 | 3,000 ✓ |
| `market_search_results` | 1,816 | 1,816 ✓ |
| `market_terms` | 5,789 | 5,789 ✓ |
| `market_sku_config` | 1,584 | 1,584 ✓ |
| `market_listing_metrics` | 3,118 | 3,118 ✓ |

**Cero pérdidas, segunda corrida idempotente** (0 insertadas, `on conflict do
nothing` en todo). El diagnóstico previo salió limpio: cero SKUs fuera de
`core.products`, cero métricas sin periodo, cero colisiones por las PK nuevas
— incluida la de `(sku, canal, cuenta, periodo)` que preocupaba por el caso de
dos listings del mismo SKU. **`propuestas` quedó intacta**: sigue siendo lo que
lee el backend hasta el paso 5.

Guardas del script: `canal='mercado_libre'` explícito, `sku_nuestro` se anula
si no está en el maestro (0 casos), `distinct on` determinista para duplicados
de PK (0 casos), acta en `reconciliation_runs` dominio `F3-migracion-enrich`.

**El cron de captura no existe.** El plan pedía apagar `railway.competencia.json`
durante la ventana; verificado contra Railway, ningún servicio lo usa — las
capturas siempre fueron manuales. El candado real es no correr
`competencia_subir.py` mientras conviven los dos esquemas.

**Línea base congelada antes de migrar** en `verificacion_competencia/`
(git-ignorada): vistas + tablas de `propuestas`, ~9 MB. La base HTTP de los 14
GET del router queda para antes del paso 5: el backend ya exige `X-API-Key` y
la llave no está en los env locales (Railway la redacta por OAuth).

Casos del plan verificados en el destino: `CAM-0030-IND` con sus dos cuentas y
sus precios de descuento reales ($3,294 BEKURA / $3,899 SANCORFASHION, no el
$7,755.92 del listing); 3,118 de 3,118 con `visits_30d`. Matiz: el conteo "785
con descuento" del plan era `precio < precio_lista` en el origen; contra
`channel.listings.price` da 645 porque ese price se refresca cada 15 min —
consecuencia esperada de descartar `precio_lista` por diseño.

Siguen: paso 4 (vistas `market_skus_v` / `market_publicaciones_v`), 5 (repuntar
backend), 6 (frontend), 7 (rename + drop en dos tiempos). Versión 0.96.0.

---

### v0.97.0 — Competencia paso 4: las vistas `market_*_v`, verificadas byte a byte (Eduardo)

Migración **0013**: `enrich.market_skus_v` y `enrich.market_publicaciones_v`
reproducen la forma exacta de las vistas de `propuestas` (contrato tomado de
`pg_get_viewdef`, no reescrito de memoria) sobre las tablas nuevas. Aplicada en
sandbox y producción; **PARIDAD OK** en el manifiesto.

**La verificación**: `market_skus_v` idéntica a la línea base congelada —
1,584 de 1,584 filas, byte a byte. `market_publicaciones_v` dio 20 diferencias
contra la línea base… que resultaron ser deriva del dato VIVO: donde el origen
no capturó precio, la vista cae a `channel.listings.price`, que el sync
refresca cada 15 min. Comparadas las dos vistas **en la misma transacción**
(`repeatable read`): **3,118 de 3,118 idénticas**. Moraleja para el paso 5: el
diff de publicaciones debe capturarse mismo instante, no contra fotos viejas.

**Tres columnas que el plan creyó derivables y NO lo son** (todo medido):

- `estado`: `l.status` es el estado del PUBLICADOR (`published`/`error`); el
  capturado es el del LISTING en ML (`active`/`paused`/`under_review`).
  Difieren en **3,118 de 3,118** — la premisa "ya existe en channel.listings"
  era falsa en semántica, no solo en frescura.
- `list_price`: 314 filas ya diferían del `l.price` vivo al migrar. El precio
  de lista DEL PERIODO es parte de la medición (y solo se capturó donde hay
  descuento: 785 filas — de ahí salía el conteo 785 del plan).
- `fuente_unidades`: 3,118 no nulo, sin consumidor hoy, pero retirarlo rompía
  la fidelidad de la vista. Cuesta una columna.

Las tres van en `market_listing_metrics` como foto del periodo, con backfill
dentro de la misma 0013 en un `DO $$` guardado por la existencia de
`propuestas` — en sandbox pasa sin tocar datos.

Decisiones de derivación medidas: `nombre` = `core.products.name` (el fallback
almacenado se usó 0 veces), categoría = panel primero y medida como fallback
(misma prioridad que la vista vieja; 1 caso, `CAM-0030-IND`), y la **raíz sale
de la categoría MEDIDA** — derivarla vía panel daba 79 diferencias; vía medida,
cero. `market_skus_v` gana la columna `canal` como única adición deliberada.

`propuestas` sigue intacta y el backend sigue leyéndola: el switch es el paso
5. Versión 0.97.0.

### v0.98.0 — Desmantelamiento de COSTOS y PEDIDOS, paso 1: los interruptores listos

El molde que channel estrenó en v0.95.0/v0.95.1, aplicado a los dos dominios
que mueven dinero. Flags nuevos `COSTING_ESPEJO_INVERSO` y
`ORDERS_ESPEJO_INVERSO` (default **true** = comportamiento de hoy; este deploy
no cambia nada). En false, el espejo inverso deja de copiar a MySQL:
`costos_validados`/`costos_finales`/`costos_logs` y `pedidos_ml` quedan
congeladas, primer movimiento del retiro.

**La diferencia con channel, dicha sin rodeos:** channel se auto-repara (su
sync es full-refresh cada 15 min — revertir el flag repuebla MySQL solo). Estos
dos son POR EVENTO: revertir el flag NO recupera lo que no se copió. Si el
retiro lleva días y hay que volver, la reversa completa necesita un backfill
desde kubera. Por eso estos dos esperaron al censo de lectores (24 h de
conexiones en curso) y por eso van después del ensayo.

Lo que NO tocan los flags, a propósito: la resiliencia entera vive en el camino
de error y queda intacta — kubera caída → MySQL absorbe + evento a la cola
(costing) / espejo clásico (orders) + Slack. Verificado en las pruebas.

Al encenderlos: apagar en el MISMO movimiento los crons `deltas-costos` y
`deltas-orders` (editar sus `railway.*.json` — un redeploy no re-resuelve el
config file, solo un push). Las rachas cierran cumplidas (costing 26, orders 20
al 11-ago).

Pruebas sandbox `backend/scripts/probar_retiro_costing_orders.py`: **11/11
pasan** — por dominio: con flag copia, sin flag congela mientras kubera avanza,
y con kubera caída MySQL absorbe con su evento encolado.

Encenderlos es flujo vivo: censo de 26 h limpio + dale de Brandon. Sin
migraciones; dos variables nuevas. Versión 0.98.0.
---

### v0.99.0 — Competencia paso 5: el backend lee `enrich.market_*` (Eduardo)

El switch: `competencia_supabase.py` deja de leer `propuestas` y lee las tablas
y vistas nuevas. 10 consultas repuntadas; la única que sigue en `propuestas` es
`resultados()` (`competencia_resultados`, 295 filas): el plan la declaró sin
lectores y `/detalle` resultó leerla — queda documentada como decisión previa
al rename del paso 7a.

**Verificación pre-deploy, la parte importante**: el módulo viejo y el nuevo se
corrieron LADO A LADO contra producción, mismo instante, comparando la salida
de las 11 funciones. Primer intento: 4 diferían — exactamente las que leen
tablas directas, porque la columna `canal` nueva se filtraba al API y los
campos retirados desaparecían. Se corrigió lo primero (pop de `canal`: la tabla
es multicanal, el API lo expondrá cuando sea a propósito) y se descontó lo
segundo (retiros documentados). Segundo intento: **11 de 11 equivalentes**.

Poda: **GET `/visitas-propias` retirado**. Llamaba a
`competencia_store.visitas_propias()`, que NUNCA existió en el código —
respondía 500 en el 100% de los casos, con y sin parámetro, así que nadie pudo
haberlo consumido. El dato (`visits_30d`) ya viaja en `/vista` y `/sku/{sku}`.
El POST del mismo nombre (el refresco) sigue vivo.

Línea base HTTP de los 14 GET capturada ANTES del switch con la `API_KEY` (vía
Railway CLI; `AUTH_ENFORCED=true` ya alcanza estas rutas): 17 archivos en
`verificacion_competencia/`, `/vista` completo con 3.5 MB. El diff después del
deploy cierra este paso. `propuestas` sigue intacta como red de seguridad:
rollback = revertir este commit. Versión 0.99.0.

### v0.99.1 — /api/webhooks/woo abierto en el middleware (faltó en v0.92.0)

El endpoint del webhook de Woo nació protegido por el candado de auth sin
querer: WooCommerce no puede mandar nuestra `X-API-Key`, así que cada entrega
habría recibido 401 — y Woo deshabilita un webhook tras 5 fallas seguidas, en
silencio. Detectado hoy al ir a crear los webhooks en wp-admin: el ping de
verificación contestó "Falta la credencial".

`/api/webhooks/woo` entra a `RUTAS_ABIERTAS` con la misma regla que ML y
TikTok: al webhook lo protege su firma HMAC (`X-WC-Webhook-Signature`), no la
credencial — y sin firma válida el endpoint no escribe nada. La coincidencia
es exacta: `/api/webhooks/woo/log` sigue cerrado con API key.

Versión 0.99.1.
---

### v0.100.0 — Competencia paso 6: la poda, y adiós al fósil de /detalle (Eduardo)

Decisión tomada: **`/detalle` se retira completo** — endpoint, funciones y
tabla. Era el fósil de la primera versión del módulo (cuando se capturaba por
SKU); tras pasar a buscar por término quedó devolviendo ~93 bytes. Sus 295
filas están archivadas en `verificacion_competencia/` antes de morir con el
esquema en el paso 7.

Lo notable de la poda es lo que REVELÓ:

- **`detalleCompetencia` y `topCategoriaCompetencia` (api.ts): exportadas y
  jamás llamadas.** Ni un solo uso en la UI. El "consumidor" que se creía que
  tenía /detalle era una confusión de nombres con `detalleSkuCompetencia`
  (el cajón del SKU, que llama a `/sku/{sku}` y está vivísimo).
- **`posiciones()` llevaba meses muerta en producción sin que nadie lo
  notara**: leía el SQLite local directamente —sin delegar al modo Supabase—
  y en Railway ese archivo es efímero y siempre está vacío. `pos_gen`,
  `pos_tit`, `pos_cat` y `periodo` de `/tabla`: siempre None. Retirados.
- Con `resultados()` fuera del módulo supabase, el backend quedó con **cero
  lecturas de `propuestas`**: el esquema viejo ya no tiene ni un lector.

También: tipos muertos fuera de types.ts (`CompetenciaDetalle`,
`CompetenciaPosicion`, `TipoCompetencia`…), `CompetenciaResultado` adelgazado
al contrato real del paso 5, `descuento` fantasma fuera de `RankingCategoria`,
y la columna "vis" de ResultadosBusqueda que ya solo pintaba "—".

Se CONSERVA el pipeline local de captura (`reemplazar_resultados`,
`top_categoria`, la tabla SQLite `resultados`): escribe local, no toca
`propuestas`, y el modo local lo usa.

Verificación: sintaxis ok, `tabla()` (393 filas, sin llaves residuales) y
`vista()` funcionales contra producción, `tsc --noEmit` exit 0. Con esto,
`propuestas` queda listo para el rename del paso 7a: cero lectores.
Versión 0.100.0.

---

### v0.104.0 — El receptor de Temu, y la firma de TikTok que habría tirado todo

**`POST /api/webhooks/temu`**, en observación y sin base de datos, mismo patrón
que ML y TikTok: guarda absoluta (200 pase lo que pase), anillo de 300 eventos
en memoria, el crudo completo a los logs de Railway, y `GET /temu/log` cerrado
tras la credencial porque los eventos traen datos del comprador.

**Por qué el endpoint va ANTES de suscribir nada.** La consola de Temu (Partner
Platform → Webhook → Create webhook) pide una *"Push website"* y la **valida al
guardarla**: si la URL no responde, rechaza el alta con *"The Push website is
invalid"*. Brandon lo topó escribiendo un nombre en vez de una URL. Por eso
`/api/webhooks/temu` entra también en `RUTAS_ABIERTAS` del middleware: un 401
ahí no solo perdería eventos, impediría dar de alta la suscripción. La apertura
es por igualdad EXACTA, así que `/temu/log` no la hereda.

**El alta es MANUAL, y está confirmado.** De los 129 permisos que Temu concedió
a la app, **ninguno es de eventos**; `appSubscribeStatus` viene en 0 con las
listas vacías. No hay forma de suscribir por API. Los eventos que importan, con
el nombre que muestra la consola: `Order status change event` (la venta),
`trade logistics address changed` y `Aftersales status change event` (para
devolver stock). El de *Supply chain* es para ERP con almacén cooperativo y no
aplica.

**Lo que esta fase tiene que averiguar.** Los endpoints de pedido de Temu **NO
traen precio** — verificado sobre las 2 ventas reales: hay `quantity`,
`extCode` (nuestro SKU), `goodsName`, estados y tiempos, ningún monto. Si el
webhook tampoco lo trae, el pedido de Woo no se puede congelar con su precio
real. Se decide viendo llegar el primer evento, no adivinando.

**Fix aparte, en la firma de TikTok.** El verificador calculaba
`HMAC-SHA256(cuerpo, app_secret)` y el algoritmo real es
`HMAC-SHA256(app_key + cuerpo_crudo, app_secret)`. Hoy no molestaba porque solo
observa, pero el día que pasara a rechazar habría tirado **el 100% de los
eventos legítimos**, con el síntoma "dejaron de entrar ventas de TikTok" y
ningún error a la vista.

También entran: `services/tiktok_atributos.py` (prompt + validador de atributos
del publicador masivo) y tres documentos de investigación en `docs/`.
### v0.136.1 — Walmart: la exención no vivía donde creíamos, y un campo de más mataba lotes enteros

**El hallazgo que cambia el plan.** La exención de UPC de electrónica **no está
en "Accesorios Electrónicos"** (`electronics_accessories`), que es donde el
código apuntaba. Está en **"Electrónicos"** (`health_and_beauty_electronics`).
La sonda del 7-ago mandó **el mismo SKU** (`TEC-0018-NEG`) a cinco puertas de
electrónica y solo esa pasó el filtro de UPC; `electrical`, `electronics_cables`
y `large_appliances` contestaron *"not authorized to set up 'CUSTOM' Product
IDs"*. La prueba definitiva son los **182 artículos que publicaron por esa
puerta** ese mismo día. `electronics_accessories` **nunca llegó a probarse**:
sus cinco feeds de 85 murieron antes, en `modelNumber`.

**La poda.** Mandar un campo que la categoría no define no es un aviso — tumba
el artículo y con él el lote. Tres feeds completos murieron el 7-ago, cada uno
por UN campo de más:

| Campo de más | Categoría | Muertos |
|---|---|---|
| `modelNumber` | Electrónicos | 85 de 85 |
| `gender` | Almacenamiento | 83 de 83 |
| `countPerPack` | Juguetes | 33 de 33 |

Ahora cada categoría lleva su `campos_visible` (lista blanca) y `_item()`
recorta contra ella. Las categorías que no la declaran se comportan igual que
antes, así que el cambio no puede romper lo que ya funcionaba.

**El esquema oficial no es la autoridad.** El JSON publicado es la 3.19 y
producción corre la 3.11: la 3.19 dice que `modelNumber` es válido en
electrónica y producción lo rechaza. Sirve para descubrir nombres y listas
cerradas; la obligatoriedad y la validez se verifican contra lo que de verdad
se manda.

**La regla para leer la evidencia, ahora escrita en el código.** Walmart valida
**por etapas** y solo reporta la primera que falla. Que *no* aparezca el error
de UPC **no prueba** que haya exención: el artículo pudo morir antes. Solo
valen dos pruebas: un SKU que llegó a `SUCCESS` (exención sí) o un
*"not authorized"* explícito (exención no). Las demás categorías se mudaron a
`CATEGORIAS_POR_CONFIRMAR`, cada una con qué evidencia hay y cuál falta:
Muebles tiene **rechazo explícito** (39 SKUs), y Almacenamiento, Juguetes,
Papelería y Herramientas **no tienen ninguna evidencia** — un lote no es un
piloto.

**Tamaño de lote y ritmo.** `TAM_LOTE` baja de 200 a **50** y
`PAUSA_ENTRE_LOTES` sube de 20 s a **360 s**. Los 50 alinean tres cosas: lo
medido (343 truenan con `GMP_GATEWAY_API`, 85 pasan), el único número que
Walmart publica cerca de la realidad (50 SKUs/call) y el tope de 50 entidades
del detalle del feed. Los 360 s son la cuota real: **10 feeds/hora**; con 20 s
la hora entera se quemaba en 3.3 minutos y el resto moría en
`REQUEST_THRESHOLD_VIOLATED` — que es lo que tumbó 19 de 24 productos sin que
hubiera nada malo en sus datos.

**El veredicto por SKU ya no miente.** `consultar_feed()` pedía
`includeDetails=true` **sin `limit`**: el default de Walmart es 20 y el máximo
50, así que un feed de 50 devolvía el detalle de 20 y los otros 30 se daban por
buenos sin haberlos mirado. Ahora va `limit=50` explícito y se pagina con
`offset` — porque **`nextCursor` no llega en MX**. El mismo bug estaba en el
censo: `GET /v3/items` declaraba `totalItems=237` y devolvía 200 sin cursor, y
37 artículos eran invisibles.

**Números al 12-ago:** 237 artículos en Walmart (221 `PUBLISHED`), de los que
**176 tienen stock real**. Con las tres exenciones probadas hay **458 SKUs más
listos para mandar** en 11 feeds, o sea un techo de **634** contra la meta de
600. Muebles (96), Almacenamiento (129), Juguetes (53) y Papelería (27) siguen
esperando exención — no hacen falta para llegar.

### v0.106.0 — Walmart deja rastro: el feed ya no muere en una variable local (Eduardo)

**El problema.** `publicar_walmart.py` armaba el payload en memoria, mandaba el
feed, recibía el `feedId`… y lo dejaba morir en una variable local (línea 638).
Nada se persistía. Para saber qué pasó con un artículo había que volver a
preguntarle a Walmart — y preguntarle **cuesta**: el corte por
`REQUEST_THRESHOLD_VIOLATED` tumbó **19 de 24 productos** del segundo lote sin
que hubiera nada malo en sus datos. Por eso `estado_walmart.py` está diseñado
para hacer 3 llamadas en total. La pregunta era cara porque no había dónde
guardarla.

**Qué hace ahora.** Con `--aplicar`, cada artículo enviado deja una fila en
`ops.channel_submissions` (`canal='walmart'`, `submission_id=feedId`,
`operacion='alta'`, `status='ENVIADO'`), y el veredicto de FASE 4 se escribe
**encima** cuando Walmart contesta. Los que quedan en `INPROGRESS` conservan su
fila: se pueden re-consultar después sin volver a publicar. `--sin-bitacora`
la apaga.

**Idempotente SIN DDL.** Cada INSERT trae su propio `where not exists` sobre
`(canal, submission_id, sku)`, y el veredicto es UPDATE, no INSERT — por eso las
6 rondas de consulta de FASE 4 no duplican. Probado contra filas reales de
producción, sin escribir: el mismo feed insertaría 0 filas, un feed nuevo
insertaría 1.

**Por qué no se creó un índice único**, que era la recomendación del consejo:
`ops.channel_submissions` no tiene ninguno y **hoy no se le puede crear**. El
pre-chequeo (`scripts/prechequeo_unique_submissions.py`, solo lectura) midió
22,946 filas con **una** colisión de `detail_ref`: las 369 filas de tiktok del
11-ago comparten `tiktok:lote:20260811` sobre 252 SKUs distintos — el publicador
de TikTok, que vive fuera del repo, escribió un ref de LOTE en vez de uno por
fila. Aquí el `detail_ref` se escribe **por fila**
(`walmart:feed:<feedId>:<sku>`), que es como debió hacerse allá.

También se descartó el `unique (canal, submission_id, sku)` que se había
sugerido: **habría roto producción**. Para ML el `submission_id` es el
`ml_item_id` (`kubera_mirror.py:851`) y se reusa entre los eventos `alta`,
`actualizacion`, `imagen` y `pausa` del mismo SKU — hay **84 grupos** vivos que
ese índice habría rechazado. Medido antes de escribir una línea.

**Nunca rompe la publicación.** Si la BD no contesta, se anota y el script sigue
mandando: publicar es su trabajo, registrar es el extra. Los SKUs que no están en
`core.products` (FK) no se registran y se listan en el resumen, con la nota de
que los agrega el cron `etl-core-products` de las 06:15 UTC.

Verificado: `py_compile` OK; los tres statements validados con `EXPLAIN` contra
el esquema de producción (sintaxis, columnas y tipos) sin ejecutar DML; guarda de
idempotencia probada contra filas reales. Versión 0.106.0.

---

### v0.103.0 — El padre viaja con la variante: se cierra el hueco del seam (Eduardo)

Cuatro de los siete huecos que el acta de core reportó la madrugada del 12-ago
eran padres variables (`ACC-0816`, `ROP-0795`, `TEC-2344`, `ROP-0874`): el seam
escribió sus 24 variantes en vivo y a ellos NO, y el ETL de las 00:15 tuvo que
darlos de alta.

**Causa, reproducida en producción.** Al guardar una variación, WooCommerce
sincroniza el padre y le mueve `post_modified` POR DENTRO — pero eso no es un
guardado de producto, así que **no dispara `product.updated` del padre**. Queda
un padre que parece modificado y del que nadie se entera.

La prueba: se tocó SOLO la variante `ACC-0816-MUL`. En Woo los dos quedaron con
`modificado = 15:54:16 UTC`; en el registro, la variante entró a las 09:55:09 y
el padre se quedó con la marca del ETL (00:15:50).

**El arreglo.** El evento de la variante trae `parent_id` pero nada más del
padre, así que el resto se lee de wp_posts (`wp_db.ficha_basica`) y se registra
junto con la variante. El registro se extrajo a `_registrar_acta` para que padre
y variante pasen por el MISMO candado anti-repetidos. El padre se intenta
siempre, incluso si la variante se descartó por repetida — puede seguir sin
acta. Envuelto en try/except: jamás rompe el evento.

**Dos cosas que quedaron descartadas en el camino**, y conviene dejarlas
escritas para no volver a perseguirlas:

- **Los borradores SÍ disparan webhook.** Se probó tocando un `draft`
  (`DEPO-0014-NEG`) y un `pending` (`JUGU-1179`): ambos se registraron, con
  ~80 s de latencia (entrega asíncrona por wp-cron). No confundir "no llegó"
  con "aún no llega".
- **`core.products.source` NO sirve para rastrear quién escribió una fila.** El
  ETL la reescribe cada noche (`source = excluded.source`, calculada como la
  unión de fuentes), así que hoy ninguna fila conserva su `source` de seam. El
  testigo bueno es `created_at`, que sí sobrevive.

Queda pendiente el otro hueco del acta: los `odoo_only` (SKUs que existen en
Odoo y no en Woo), que dependen del botón manual "Sincronizar Odoo". Versión
0.103.0.

---

### v0.102.0 — El vigilante deja de pedirle acta a un dominio retirado (Eduardo)

El 12-ago a las 02:06 CDMX el bot de Slack mandó tres alertas. Dos eran reales;
la tercera decía *"Acta de Channel NO generada hoy — revisar el cron deltas en
Railway"* y mandaba a revisar **un cron apagado a propósito**: Channel se retiró
la víspera en la v0.95.1 (`CHANNEL_ESPEJO_INVERSO=false`, cron convertido en
aviso de retiro) con su racha cerrada en **22/14, cumplida**. Se apagó el
dominio y nadie lo sacó de la lista que vigila `_revisar_actas`.

Nuevo `_DOMINIOS_RETIRADOS` en `routers/migracion.py`: el vigilante lo salta,
pero el dominio **se queda** en `_DOMINIOS_DELTAS` para que /migracion conserve
el expediente. Eso no había que arreglarlo — la racha se calcula desde el último
día CON acta, así que la tarjeta de Channel sigue mostrando su 22/14 cumplido
aunque hoy no haya corrida.

Las otras dos alertas del día (`core-etl-v2` y `categorias-etl` en `con_deltas`,
`seam_gap` 17 y 15) son legítimas y se atendieron con re-corrida el mismo día,
que es lo que rescata el día para la racha.

Versión 0.102.0.

---

### v0.101.0 — Competencia paso 7a: `propuestas` se retira sin destruirse (Eduardo)

Migración **0014**: `alter schema propuestas rename to propuestas_retirado` en
la BD kubera. Mismo efecto funcional que el drop (nadie lo encuentra por su
nombre), pero **reversible** con un rename de vuelta. En sandbox es no-op:
`propuestas` nunca existió ahí — se creó ad-hoc durante el MVP y jamás estuvo
trackeada en migraciones.

Precondiciones al ejecutar: cero lectores (la última lectura se podó en
v0.100.0), cero escritores (el cron nunca existió), y el contenido a salvo por
triplicado — 15,307 filas migradas con conteos exactos, 295 archivadas en JSON,
línea base completa en `verificacion_competencia/`.

Verificado tras el rename: los 9 objetos intactos bajo el nombre nuevo, las
vistas `enrich.market_*_v` vivas (1,584 / 3,118), y los 4 endpoints del smoke
en 200.

**El 7b quedó agendado**: tarea `competencia-paso-7b-drop`, corrida única el
**lunes 18-ago 09:45**. Re-verifica todo (panel sano, cero lectores, conteos
mínimos de enrich, respaldos presentes) y solo si TODO pasa aplica la 0015
(`drop … cascade`) y la documenta; ante cualquier falla aborta sin tocar nada.
Una semana de enfriamiento: la deuda no cobra intereses, el drop sí es
irreversible. Versión 0.101.0.

### v0.105.0 — Análisis: el margen alcanza hasta abril, y el costo dudoso se marca antes

Dos cambios que se descubrieron juntos investigando por qué la tabla no podía
mirar más atrás de julio.

**El selector de período no servía para margen.** Pedir 60 días daba 597 SKUs
con margen; pedir 120 daba **598 — uno más**. La causa: la comisión salía de
`channel.order_items`, que solo tiene detalle orden por orden desde el 15/16 de
julio, cuando el webhook empezó a capturar bien. Antes de esa fecha no hay
pedidos que leer, así que la columna se quedaba vacía sin decir por qué.

Peor: donde sí había datos, medía sobre una MUESTRA. En `TEC-1284-NEG-27"` la
comisión salía de 8 piezas de las 175 vendidas (4.6%) — las otras 167 son de
junio y nunca entraron a `channel.orders`.

**Se agregó un relleno, NO una sustitución.** `channel.sales_daily_completa`
cose `analytics.sales_daily_hist` (hasta el 15-jul) con `channel.sales_daily`
(desde el 16-jul) y trae `sale_fee` en las dos ramas. Pero la primera versión
—sustituir una fuente por la otra— se descartó al medirla: de 699 SKUs con las
dos, solo 53% coincidían dentro de 5%, y las diferencias grandes iban en la
dirección equivocada. En `TEC-0664-BLN` el histórico daba $4.25/u contra
$11.89/u de los pedidos; sobre un producto de ~$85 eso es una tasa de 5%, y ML
no cobra 5%.

El diagnóstico: el `sale_fee` histórico es **sólido en agregado pero ruidoso al
repartirlo por SKU**.

| fuente | tasa implícita mensual | filas creíbles (9–22%) |
|---|---|---|
| hist | 14–17% | 14,908 de 16,066 (92.8%) |
| vivo | 16% | 4,388 de 4,425 (99.2%) |

Por eso el histórico entra **solo donde no hay ni una línea de pedido**. Medido
comparando la consulta vieja contra la nueva, fila por fila:

| ventana | ya tenían margen | cambiaron de valor | ganan margen |
|---|---|---|---|
| 60 días | 598 | **0** | +107 |
| 120 días | 598 | **0** | +283 |

Cero regresiones. Y el panel de Comisión declara el origen de cada renglón:
`pedidos` es el cobro orden por orden, `historico` el agregado diario.

**Los 283 que ganan margen son una autopsia, no un tablero.** De ellos, 280 no
tienen una sola venta reciente; su mediana es de **66 días sin vender**, contra
7 de los que ya tenían margen. Son productos que se detuvieron. El riesgo era
ordenar por margen, ver un −141% y tratarlo como fuga activa. Por eso el aviso
ámbar del panel dice además desde cuándo no vende, y solo cuando pasa de 30
días.

**Umbral de costo dudoso: 3× → 1.5×.** Al destapar esos 283, **56 quedaban en
rojo con un costo entre 1× y 3× el precio**: creíbles a primera vista y sin nada
que avisara. `TEC-1284-NEG-27"` se vende en $1,960 con un costo de $4,229 (2.2×)
y mostraba −137.9% como si fuera un producto ruinoso. Las marcadas pasan de 45 a
96 en la ventana de 60 días. El precio de bajarlo: una liquidación real a menos
de dos tercios del costo ahora sale marcada aunque el dato esté bien — se aceptó
ese falso positivo, porque es más barato dudar de un costo correcto que dar por
bueno uno inventado. La página de Categorías usa el mismo umbral y no cambió:
ninguna de sus 28 ramas lo cruza, la agregación diluye los costos malos.

**De paso**, el panel de Comisión culpaba a Amazon de TODAS las piezas sin
comisión. Casi nunca era cierto: en `TEC-1284` eran 156 piezas de junio, no de
Amazon. Ahora nombra las dos causas posibles en vez de afirmar la equivocada.

**Lo que NO se tocó y queda declarado:** el Excel de categorías sigue leyendo
`order_items`, así que conserva su límite de julio en adelante. No es descuido —
su SQL necesita `item_id`, título y precio POR LÍNEA, y la vista es agregada por
día. Cambiarlo es un rediseño, no un ajuste. Igual `detalle` y `margenes_top`,
que sí admitirían el mismo tratamiento.

Verificado en el navegador contra el sandbox: los dos orígenes en el panel, el
marcado en ámbar-600/500 contra una fila normal, el volteo del panel en la
última fila visible, y Categorías sin ruido nuevo. Sin migraciones y sin
variables nuevas. Versión 0.105.0.

### v0.107.0 — Retiro de COSTOS y PEDIDOS: MySQL congelado en los dos dominios de dinero

Ejecución del paso 1 con el dale de Brandon (que cubre los cinco cortes) y el
go de Eduardo: `COSTING_ESPEJO_INVERSO=false` y `ORDERS_ESPEJO_INVERSO=false` en
producción, y los crons `deltas-costos` y `deltas-orders` convertidos en aviso
de retiro por sus config files (`railway.deltas-costos.json` y
`railway-deltas.json` — este último con nombre fuera de patrón y sin
`cronSchedule` propio: su horario vive en el servicio).

`costos_validados`, `costos_finales`, `costos_logs` y `pedidos_ml` quedan
congeladas a propósito. Rachas cerradas cumplidas: **costing 27/14, orders
21/14**.

**El censo de lectores que autorizó esto**, y lo que costó: la primera corrida
(11-ago) tenía un defecto propio — agrupaba las IPs por lo anterior al primer
`:`, que en IPv6 es el prefijo y no el puerto, así que TODAS las máquinas
residenciales caían en un mismo bucket que se leyó como "mi sonda". Corregido a
`rsplit(":", 1)`. Con la agrupación buena aparecieron tres IPv6 distintas y una
IPv4, todas confirmadas por Eduardo como equipo conectando en local. Sumado al
censo de código del 10-ago (ningún repo externo lee estas tablas), el
desmantelamiento quedó autorizado.

Hallazgo extra: **el egress de Railway cambió** de `162.220.232.251` a
`152.55.177.181` (misma huella: 3 conexiones ociosas del pool y las queries del
backend). Cualquier plan futuro que dependa de una allowlist por IP tiene que
contar con que esa IP se mueve sola.

Reversa: los dos flags a `true` + restaurar los `startCommand`. **Ojo, y aquí
sí duele**: estos espejos son POR EVENTO, no full-refresh como channel —
revertir NO repuebla lo que no se copió, así que una reversa tras días necesita
backfill desde kubera. Dentro de las primeras horas es trivial.

La resiliencia queda intacta: kubera caída → MySQL absorbe + evento a la cola
(costing) o al espejo clásico (orders). Ese camino vive en el manejo de error y
no depende de los flags.

Sin migraciones. Versión 0.107.0.

### v0.107.1 — Costos y Pedidos al vigilante de retirados (o dos falsas alarmas a las 2 a.m.)

Cabo suelto de la v0.107.0, señalado por Eduardo antes de que ocurriera:
`costing-deltas` y `orders-deltas` entran a `_DOMINIOS_RETIRADOS`. Su cron dejó
de escribir acta a propósito, así que sin esto el vigilante de ausencias
(08:00 UTC) habría avisado *"Acta de Costos NO generada hoy — revisar el cron
deltas en Railway"* y mandado a revisar dos crons apagados adrede. Es
exactamente lo que pasó con channel el 12-ago a las 02:06 CDMX.

Siguen listados en `_DOMINIOS_DELTAS`: /migracion conserva el expediente y
muestra sus rachas cumplidas (27/14 y 21/14), solo dejan de vigilarse.

Regla que queda escrita en el código: **el dominio se apunta como retirado en el
mismo commit que lo apaga.** Versión 0.107.1.

---

### v0.108.0 — El contenido por canal deja de vivir en el navegador (Eduardo)

**El problema.** El Estudio ya sabía GENERAR contenido por canal —`ia_generadores.GENERADORES`
tiene 6 tipos para Amazon (título, item highlights, bullet points, descripción,
atributos, plan de imágenes), 3 para ML y 1 para TikTok— y no sabía guardarlo:
`POST /api/ia/generar` devuelve el texto y ahí muere. El único botón de guardar
(`POST /api/productos/{sku}/contenido`) escribe a WooCommerce y **no recibe canal**.

Los borradores por canal existían, pero en `localStorage` (`studioStore.ts`):
sobrevivían al recargar la página y **no salían de esa máquina**. Ni el
publicador ni el resto del equipo los veían.

**Qué entra.**

- **Migración 0016 · `enrich.channel_content`** — llave `(sku, canal, cuenta)`,
  `contenido jsonb` + `origen jsonb`. La cuenta va en la llave por el caso
  `EST-0091`: el mismo SKU es dos productos según la cuenta de ML.
- **`services/channel_content.py`** — leer, guardar y resumen. **Reusa el pool de
  `kubera_mirror`** en vez de abrir uno propio: ese pool está acotado a 6
  conexiones a propósito (el 23-jul se perdieron 60 eventos por
  `TooManyConnections` con un pool de 3).
- **Tres endpoints** en `routers/productos.py`: `PUT`/`GET` por canal y un resumen
  para pintar las pestañas sin traerse los documentos.
- **Botón en el Estudio**, en todos los canales menos General — General ya tiene
  el suyo y su destino es WooCommerce, la fuente de verdad del canal web.

**FUSIONA, no reemplaza.** El panel manda una pestaña a la vez: si guardar los
highlights borrara los bullets, sería peor que no guardar. El upsert hace
`contenido || excluded.contenido`, y hay un `reemplazar` para el único caso donde
hace falta — borrar un campo.

**Se retiran `enrich.ai_content` y `enrich.ai_attributes`.** La 0010 se escribió el
10-ago suponiendo que este contenido sería de IA. Al leer los publicadores resultó
falso: es MEZCLADO —copiado de Woo, escrito a mano, constante del código, o
generado— y su propia columna `origen` (woo|const|ia|calc) ya lo anticipaba.
Reusarla habría dejado `flags`, `modelo_ia`, `error_texto` y `generado_at` vacías:
exactamente cómo nacieron `core.products.parent_sku` y `has_variations`, columnas
muertas que alguien usó creyéndolas vivas y produjeron 74 de 292 filas falsas en
un reporte de Inmovilizado. Ambas verificadas en **0 filas y 0 lectores** dentro
de la misma transacción del `drop`.

**Además, dos correcciones en el publicador de Amazon:**

- **`item_highlights` se conecta.** `campos["highlights"]` YA llegaba del frontend
  (`routers/publicar.py:29`) y nadie lo leía; el comentario de
  `publicar_ready.py:596` decía "bullets **y highlights**… pisan los que genera el
  mapper" pero solo se pisaban los bullets. El nombre real del atributo **no se
  pudo verificar** (sin esquemas cacheados ni credenciales de Amazon en local), y
  como `_amazon_attrs_final` filtra contra `schema["properties"]`, un nombre
  inventado se descarta EN SILENCIO. Así que se prueba contra el esquema real del
  productType y, si ninguno encaja, se registra un warning **con los nombres que
  ese esquema sí ofrece**: el bueno sale en la primera corrida en vez de nunca.
- **El país de origen se unifica en `"MX"`.** Había TRES valores: `"CN"` en el
  mapper vendorizado, `"MX"` en el mapeo propio, `["China"]` en Walmart. Y dentro
  de Amazon no eran dos opciones sino una bifurcación silenciosa —
  `_amazon_attrs_final` usa el mapper como primario y el mapeo propio como respaldo
  **cuando la BD de WordPress no contesta**, y los 403 intermitentes de Hostinger
  (pendiente #1) hacen que ese respaldo sí se use. El país declarado dependía del
  estado de la red al publicar. Se corrige en el adaptador, no en `vendor/`
  (regla 1). Decisión de Eduardo; es declaración aduanal y los tres sitios donde
  vive quedan anotados en el comentario.

**`CAMPOS_POR_CANAL.md`** — inventario de qué campos manda cada canal, levantado
del código de los publicadores y no de la documentación. Es el insumo para diseñar
`channel_requirements`. Sus cuatro afirmaciones falsas de la primera versión quedan
marcadas con lo que sí dice el código, no borradas.

**Verificado.** Sandbox recreado con las 15 migraciones (`--recrear`):
`columnas_diferentes: []` en las otras 42 tablas. Viaje redondo con escrituras
reales contra la tabla nueva — fusiona sin borrar, conserva la categoría cuando el
guardado no la manda, y la FK rechaza un SKU que no está en el maestro (el endpoint
lo traduce a un 409 legible). `tsc --noEmit` limpio, `py_compile` OK. Aplicada en
producción con pre-chequeo de 0 filas dentro de la misma transacción.

**Aviso sobre `schema_manifest.json`:** se regeneró contra producción y eso destapa
que las tablas `enrich.market_*` fueron reestructuradas fuera de las migraciones
(`market_search_term` con 464 filas sin DDL en archivo, y `termino_id` en tres
tablas donde la 0011 pone texto). **No es de este cambio** — `channel_content` no
aparece en ninguna diferencia. El candado de paridad no se rompió: estaba dando
falso verde con un manifiesto viejo. Queda como pendiente con dueño aparte.
Versión 0.108.0.

---

### v0.109.0 — Publicar deja de exigir que la pestaña esté abierta (Eduardo)

Cierra el ciclo que abrió la v0.108.0: ya se podía GUARDAR el contenido por
canal, pero publicar seguía leyendo **solo el formulario abierto**, así que
preparar y publicar tenían que ser la misma sesión.

**Las dos mitades.**

- **El Estudio carga lo guardado** al abrir una pestaña de canal. Sin esto,
  guardabas, reabrías y veías lo de WooCommerce otra vez: parecía que el
  guardado no había servido. Precedencia **borrador local > servidor > Woo** —
  el local va primero porque es trabajo sin subir de esa máquina, y pisarlo con
  lo del servidor le borraría a alguien lo que estaba escribiendo.
- **`publicar._rellenar_desde_guardado`** completa `campos` desde
  `enrich.channel_content`. **El formulario MANDA**: lo guardado solo rellena lo
  que venga vacío (regla 2 de la casa — la elección humana del momento gana). Un
  campo presente pero EN BLANCO cuenta como ausente, o un formulario a medio
  llenar bloquearía el respaldo justo cuando más sirve.
- Se llama desde `preview` **y** desde `confirmar`. Si solo lo hiciera el envío,
  el modal enseñaría una cosa y se publicaría otra — y la vista previa existe
  para que lo que se revisa sea lo que sale.

**Dos bugs que salieron al probar el panel local contra el sandbox**, y que en
producción no habrían aparecido nunca:

1. **`channel_content` estaba muerto en staging.** Pedía `KUBERA_DB_URL` y
   `env.staging` define solo `SUPABASE_DB_URL`. En producción las dos apuntan a
   la misma base, así que el fallo estaba latente hasta que alguien probara en
   staging: el guardado respondía *"KUBERA_DB_URL no configurada"* mientras el
   resto del panel funcionaba. Ahora resuelve `supabase_db_url` primero.
   Eso obligó además a **quitar el reuso del pool de `kubera_mirror`** (se
   construye sobre la variable que falta, así que ataba el módulo a un ambiente):
   ahora tiene el suyo, `maxconnections=3` y `mincached=0` — no abre una
   conexión hasta que alguien guarda.
2. **El mensaje amable de la FK nunca disparaba.** Se detectaba buscando `"core"`
   en el texto del error, pero Postgres reporta `table "products"` a secas, sin
   el esquema. El panel recibía el error crudo de la base. Ahora se detecta por
   el nombre de la constraint (`channel_content_sku_fkey`), y hay mensaje propio
   para las FK de cuenta y de canal.

**Verificado por HTTP real contra el sandbox** (backend local con `APP_ENV=staging`,
candado de ambiente confirmando `ref=yvootpbz`): guardar devuelve 3 campos; leer
los devuelve con su categoría; guardar solo `descripcion` deja 4 y **no borra
nada**; un SKU fuera del maestro da un 409 legible; un canal inválido da 400. Y
el relleno del publicador probado con el servicio real — el formulario gana en
`titulo`, lo guardado aporta `bullets` y `highlights`. `tsc --noEmit` limpio,
`py_compile` OK.

**Lo que NO se verificó:** el botón en el navegador. El listado de Productos está
fijo en `canal: "general"` (`page.tsx:94`), que lee WooCommerce en vivo, y
`env.staging` no tiene credenciales de Woo — sin productos no hay modal que
abrir. Queda pendiente y necesita `WPDB_*` de lectura en staging.

De paso: el `POST /api/sync/catalogo` que el panel dispara al abrir **es de solo
lectura** (refresca índices leyendo la BD de WordPress). Queda anotado porque se
venía tratando como si escribiera. Versión 0.109.0.

---

### v0.110.0 — `origen` y `categoria`: el guardado por canal deja de guardar a medias (Eduardo)

La prueba en producción de la v0.109.0 (`ACC-0091`) funcionó, y al cotejar la
fila contra la base aparecieron **dos columnas vacías**: `origen` y `categoria`.
Ninguna rompía nada, pero se habrían quedado así — que es exactamente cómo
nacieron `core.products.parent_sku` y `has_variations`.

**`origen` — quién escribió cada campo.** Hoy no hay forma de saber si un título
lo generó la IA o lo redactó una persona. Importa porque "Mejorar con IA" **pisa
lo que había**: si alguien trabajó esos bullets, se pierden sin aviso.

Se DERIVA al guardar, comparando, en vez de rastrear cada tecla:

```
igual a lo que produjo la IA  ->  ia
igual a lo que tiene Woo      ->  woo
ninguna de las dos            ->  manual
```

Se eligió comparar sobre envolver cada `setState` porque los campos se editan
desde muchos sitios y un wrapper por input se desincroniza en cuanto alguien
agregue uno nuevo. Lo que la IA produjo se guarda en un `useRef` (no estado: no
debe repintar).

**`categoria` — contra qué comparar los requisitos.** Es la que conecta con el
encargo de `channel_requirements`: la pregunta *"¿qué le falta a este SKU?"* no
se puede contestar sin saber en qué categoría va, porque **los obligatorios
cuelgan de la categoría**.

**La resuelve el BACKEND, no el panel** (`_categoria_del_canal`). En Amazon el
tipo sigue la precedencia de la regla 2 —panel > histórico > detección, la que
nació del caso `TEC-1812-NEG` publicado en "Máquinas de Coser"— y esa lógica ya
vive en `publicar._pt_resuelto`. Copiarla a React sería la misma regla en dos
lugares, y el día que alguien cambie una la otra empieza a mentir. Además el
Estudio ni siquiera conoce el tipo: `TipoAmazonPicker` lo maneja por dentro y no
lo expone al padre. En ML sí lo manda el front, porque ese picker sí vive en esa
pantalla. El cliente puede mandarla; si no viene, el backend la resuelve.

**Verificado contra el sandbox**, endpoint real:

```
PUT sin mandar categoría, SKU con tipo en la meta del panel (HERR-0029):
  categoria -> 'PROTECTIVE_GLOVE'      (la resolvió el backend)
  origen    -> {'titulo':'ia', 'highlights':'manual'}
```

**Un falso acierto que hay que dejar anotado:** la primera verificación leyó
`categoria: "HOME"` y se dio por buena. Era **residuo de una prueba anterior**,
conservado por el `coalesce(excluded.categoria, …)` del upsert — el mismo que
existe para no borrar la categoría cuando el guardado no la manda. En fila
limpia salía `None`, y de ahí se llegó a la causa real: ese SKU no tenía tipo
asignado. Al verificar un upsert con `coalesce`, la fila tiene que estar limpia o
se está leyendo el pasado. Versión 0.110.0.

---

### v0.111.0 — Competencia: Deportes y Fitness completa, y la captura deja de tumbar MySQL

Se cerraron las tres primeras categorías padre del módulo de Competencia
(Deportes y Fitness, Herramientas, Recuerdos y Fiestas) y en el camino salieron
cuatro cosas rotas que no se veían.

**El camino de ESCRITURA seguía apuntando a `propuestas`.** El switch de la
v0.99.0 repuntó las lecturas a `enrich.market_*`, pero la captura escribía en un
SQLite local y `competencia_subir.py` empujaba la foto completa a un esquema que
la 0014 ya había renombrado. Capturar una categoría nueva era imposible. Y
repuntar ese script tal cual habría sido peor que dejarlo roto: hace `delete
from` + reinsert de TODO, así que contra las tablas nuevas habría borrado las
15,307 filas migradas. Ahora la captura escribe directo, acotada a su propia
`(canal, categoria_id, nivel)` y en una sola transacción; sin `SUPABASE_DB_URL`
revienta en vez de caer al archivo, porque una captura escrita en un disco que
nadie lee es peor que una que no corrió.

**Una categoría padre nueva son DOS pasos.** Sus SKUs suelen ya existir en
`market_sku_config` pero con `activo=false`, y `listar_skus()` filtra por eso: de
1,584 filas el panel rendía 393. `activar_raiz()` los prende; sin ese paso la
raíz se raspa como 'hoja' y sus SKUs siguen invisibles.

**Migración 0015 — la raíz se mezclaba con la ruta.** En `market_skus_v` el
nombre y la ruta salían de la categoría del PANEL y `raiz_id` SOLO de la que
midió Competencia. Cuando pertenecían a raíces distintas, la fila mostraba una y
se agrupaba bajo otra: en pantalla se veían "Licoreras" y "Veladores" —ruta de
Hogar— colgadas de Deportes y Fitness. 79 de 1,584 filas así; con el arreglo las
coherentes pasan de 0 a 1,567. Se agregan `padre_id` y `padre_nombre`; el nombre
NO puede salir de `channel.categories` (solo tiene las hojas) y se toma del
penúltimo segmento de la ruta.

**Migración 0017 — los términos eran dos problemas con la misma forma.** Los de
`/trends` son gratis y masivos (5,853 filas en 222 categorías, hasta 50 por
categoría) y solo se leen en bloque: se empaquetan en un array JSON por
categoría, 222 filas. Los MEDIDOS cuestan una corrida de Apify cada uno y su
texto se repetía en 1,816 filas para 326 distintos: se normalizan en
`enrich.market_search_term` con FK. El FK no es por los 35 KB de texto — es para
que "un término medido una vez sirve a todos los SKUs que lo comparten" sea
garantía de la base y no convención del código.

**Apify sí sirve para capturar una raíz.** Se había descartado porque "tira
`id_pagina`", el id que resuelve la subcategoría de cada fila y por tanto los
nichos. Era un parseo faltante, no una limitación: la pageFunction ya devolvía el
`url`. Con `_pagina_y_tipo()` quedó equivalente al navegador local — que ML
bloquea a las ~50 consultas por IP, como volvió a pasar a mitad de esta captura.

**La captura se tumbaba su propia base.** `meli._access_token()` hace dos
consultas a MySQL por llamada y no cachea, y `competencia_ml` lo llamaba en cada
petición: ~3,800 consultas contra un plan de 500 conexiones/hora. A media corrida
MySQL empezó a rechazar conexiones, el token dejó de leerse y 538 filas se
guardaron sin visitas. Caché con TTL —y caché NEGATIVA, porque sin ella el fallo
se retroalimenta— en `competencia_ml` y no en `meli.py`, que está en el camino de
los pedidos vivos. Las 538 se rellenaron después: 3,964 de 3,964 con visitas.

Cierre de las tres: Deportes y Fitness 77/85 subcategorías con top y 143/143 SKUs
con término medido; Herramientas 49/55 y 99/99; Recuerdos 14/18 y 32/32. Las 18
subcategorías sin top son de las que Mercado Libre NO publica más vendidos —
verificado una por una contra `/highlights`— y reintentar no cambia nada. Costo
total en Apify: $1.42. Versión 0.111.0.

---

### v0.110.1 — El `origen` comparaba manzanas con peras

Salió al verificar la v0.110.0 en producción con `ACC-0091`: cuatro campos
marcados `ia` y el título `manual`. Era correcto —Eduardo sí editó el título—
pero al revisar por qué, apareció un hueco que no se había disparado por suerte.

`contenido` se arma con `.trim()` en los textos y filtrando bullets vacíos; lo
que la IA produjo se guardaba **crudo** en el `useRef`. Se comparaban sin
normalizar, así que **un solo espacio al final del texto que devolvió la IA**
marcaba el campo como `manual` sin que nadie lo hubiera tocado.

Ahora los dos lados se normalizan igual antes de comparar. Probado con los
cuatro casos: espacio final ya no produce falso `manual`, texto realmente
editado sigue dando `manual`, un bullet vacío que se filtra no rompe la
igualdad, y bullets distintos siguen dando `manual`.

El modo de fallo era benigno —`manual` protege de más, nunca de menos— pero
ensuciaba el dato justo en la columna que existe para saber qué revisar.
Versión 0.110.1.

### v0.112.0 — CORE: paso 3, fuera el respaldo de lectura a MySQL

Con el go de Eduardo ("retirar ya"), core y categorías cierran su ventana. Estos
dos no tenían paso 1 que ejecutar: **nunca hubo espejo inverso que apagar.**
Verificado en el código — el único `UPDATE` a `productos` en todo el backend es
`odoo_watch.py:57` (stock de Odoo, ajeno al registro civil) y `categorias_ml` no
la escribe nadie. Sus tablas de MySQL están de hecho congeladas desde el corte
del 10-ago.

Lo que sí quedaba es el paso 3: los dos lookups SKU→wc_id dejan de reconsultar
MySQL. Un miss en `core.products` ya no cae a `productos`, sigue a **Woo**, que
es la autoridad:

- `pedidos_ml.resolver_producto` — la ruta caliente de cada venta.
- `costos._cat_ml_de` — categoría ML para la comisión.

El fallback nació cuando el seam Crear→core no existía y un SKU del día
aparecía hasta el ETL de las 06:15. Ese hueco lo cerraron el corte (v0.84) y el
webhook de Woo (v0.92).

**Medido antes de quitarlo** con `comparar_lecturas_core.py` contra producción,
sobre 995 SKUs (727 vendidos en 30 días + 300 al azar):

| | |
|---|---|
| iguales | 731 |
| difieren | 28 |
| ausentes en kubera | **0** |
| arbitraje contra Woo vivo | **kubera correcto 28 · MySQL correcto 0** |
| listado | MySQL 5,271 ⊂ kubera 9,793 |

O sea que el respaldo no era neutral: **era peor.** Las 28 discrepancias son
variantes donde MySQL guardaba el `wc_id` viejo y sin padre — kubera coincidió
con Woo en las 28. Veredicto del arnés: EQUIVALENTE.

**Los ETLs de las 06:15 se quedan corriendo.** Dejan de ser compuerta de la
migración y pasan a vigilante permanente: son lo único que compara Woo contra
kubera, y es justo lo que destapó las ediciones de títulos del 11-ago. Por eso
core-etl-v2 y categorias-etl NO entran a `_DOMINIOS_RETIRADOS`.

`SUPABASE_READ_CORE` se queda como interruptor de reversa: apagarlo manda los
lookups directo a Woo. Versión 0.112.0.

### v0.113.0 — Paso 3 de channel, costos y pedidos: se acabaron los respaldos a MySQL

Los tres dominios retirados dejan de reconsultar MySQL cuando kubera falla. Con
esto **ninguna lectura del panel toca ya las tablas de la migración**.

**El argumento cambió de signo, y por eso esto urgía.** Mientras MySQL era un
espejo fresco, el fallback era una red de seguridad. Desde que congelamos los
espejos (11 y 12-ago) es lo contrario: caer ahí sirve datos viejos **sin avisar
a nadie**. Medido con los arneses de paridad el mismo día:

| Dominio | Arnés | Qué mostró |
|---|---|---|
| costing | **EQUIVALENTE** | sin diferencias |
| channel | con diferencias | 354 filas, 0 solo-en-MySQL y 0 solo-en-kubera, pero **12 campos desfasados**: `ORG-0451` decía `cross_docking/inactive` en MySQL cuando la publicación ya estaba `fulfillment/active` |
| orders | con diferencias | MySQL con 1,429 completados de BEKURA contra 1,450 en kubera, y 21 pedidos atorados en `processing` que ya se habían completado |

Ninguna de esas diferencias es un fallo: es la firma de un espejo congelado
haciendo su trabajo. Pero son exactamente los números que el panel habría
mostrado como buenos si la lectura se hubiera caído a MySQL — un tab de VENTAS
reportando 21 pedidos menos, sin que nadie sepa que están mal.

Sitios intervenidos:

- **channel** — `inventario.leer_inventario`, `presencia`, `GET /api/sync/estado`
- **costing** — `crear.py` (contenedores, detalle, listado) y
  `costos.costo_desde_validados`, que alimenta precios: un costo viejo ahí sale
  caro y en silencio
- **orders** — el agregador del tab Ventas

Se van también las guardias de plausibilidad ("0 contenedores con MySQL lleno es
sospechoso", "listado sin filtros con total 0"): existían para decidir cuándo
caer al espejo. Sin espejo al cual caer, un error de kubera ahora es un error
visible, que es lo que queremos.

Los flags `SUPABASE_READ_*` se quedan como interruptor de reversa: apagarlos
devuelve el camino MySQL completo, con el aviso de que esas tablas están
congeladas.

Pruebas sandbox tras el cambio: `probar_retiro_costing_orders.py` 11/11,
`probar_corte_core_categorias.py` 12/12. Versión 0.113.0.

### v0.113.1 — El CLAUDE.md deja de describir una migración que ya terminó

La sección de migración era del 27-jul y mandaba a "no tocar los jobs
`deltas-costos`/`deltas-channel`" (retirados) y hablaba de una "regla de corte
de 14 días de actas en cero" (cumplida en los cinco dominios). Cualquier sesión
nueva la leía como estado actual.

Lo importante que ahora dice, y que es la trampa más fácil de pisar: **las
tablas de MySQL están congeladas**, así que los flags `SUPABASE_READ_*` —el
interruptor de reversa— mandan las lecturas a datos de agosto. El panel no
diría "error": diría cifras viejas como si fueran de hoy. Misma advertencia
para cualquier `SELECT` nuevo que apunte a `costos_*`, `pedidos_ml` o
`canal_inventario`.

También queda escrito qué NO se retira (los ETLs de 06:15 como vigilante
permanente del catálogo, el webhook de Woo, la resiliencia con kubera caída,
`alertas_estado` y `espejo_kubera_log`), el estado por dominio con sus rachas,
las mañas de los crons de Railway, y **los seis pendientes de F8** en orden.

Sin cambios de código. Versión 0.113.1.

---

### v0.114.0 — Qué exige cada canal, preguntándoselo al canal (Eduardo)

Tercera y última pata de las tres que estaban revueltas. Ahora existen por
separado: lo que el canal **EXIGE** (esta migración), lo que **tenemos**
(`enrich.channel_content`, v0.108.0) y lo que **se mandó**
(`ops.channel_submissions`). Sin la primera, la pregunta del panel —*"¿qué le
falta a este SKU?"*— era incontestable: se buscó en las 17 migraciones y no
existía nada.

**Migración 0018 · `channel.field_requirements` + `core.canonical_fields`.**

**`campo` guarda el nombre NATIVO del canal y `campo_canonico` lo traduce.** Una
vuelta anterior del consejo pidió solo el canónico con FK. No cierra: Amazon
exige `condition_type`, `fabric_type`, `supplier_declared_dg_hz_regulation` —
campos que **no son conceptos del panel**, nadie edita y no tienen equivalente
posible. Con FK obligatoria no cabe la mitad de lo que Amazon pide; sin nombre
canónico se rompe la comparación contra el contenido. Se guardan los dos y se
compara por el canónico. Los tres revisores respaldaron la decisión — el
argumento decisivo: fusionarlos destruiría lo único que la tabla puede dar,
comparar *"¿ML y Amazon piden título?"* sin saber que uno se llama `title` y
otro `item_name`. Y deja el mapeo AUDITABLE, que es lo que habría hecho visible
la contradicción `MX`/`CN` del país de origen.

**`default_value`: tres estados, no dos.** ~7 campos de Amazon siempre se
llenan con una constante del publicador. Sin distinguirlos el panel los pintaría
en rojo para los 22,186 SKUs. Medido: de 73 obligatorios, 47 tienen canónico, 24
tienen respaldo y **solo 2 quedan sin nada**.

**`leido_at`:** si un canal agrega un obligatorio y nadie relee, el panel diría
"no le falta nada" y las publicaciones rebotarían sin explicación.

**Cargador `cargar_requisitos_amazon.py`** — la tabla nace CON su escritor, que
era la condición que puso el consejo (el precedente es `enrich.ai_attributes`:
creada vacía, sin escritor, muerta meses hasta que la 0016 la dropeó).

Lee el JSON Schema de SP-API Definitions por productType. **Alcance: los 12 más
usados, no los 558.** Casi todos los 558 tienen uno o dos productos, y son 558
llamadas a una API que ya nos cortó por exceso de peticiones en Walmart. Cargados
**1,563 campos de 12 tipos**.

**Lee de `channel.listings`, NO de `amazon_progress`** — esa quedó congelada al
cerrarse la migración el 12-ago, y un SELECT ahí devuelve el pasado sin decirlo.
Se nota: `ARTIFICIAL_PLANT` sale 43 en MySQL y 41 en la gemela viva, y el top-12
real trae `HEADPHONES` donde el congelado ponía otro tipo.

**Lo que el cargador reportó y valía el ejercicio:** los obligatorios que el
panel **no puede pedirle a nadie**. Salieron 4, y uno era real — **`brand`**:
Amazon lo exige, sale de un dato del producto (no es constante) y no tenía dónde
editarse. **3,060 de 7,264 productos traen atributo BRAND; los otros 4,204 se
publican en Amazon como `"Generic"` y en ML como `"Ferrahome"`.** Se agregó al
diccionario canónico; que los canales usen criterios distintos es decisión de
negocio y conviene que la vea Brandon.

`item_length_width_height` queda SIN mapear a propósito: un atributo de Amazon
cubre tres canónicos (largo/ancho/alto) y el modelo guarda uno por fila.
Inventar la correspondencia sería la suposición que este cargador existe para
evitar.

**Dos errores propios que el consejo cazó en la consulta de referencia**, antes
de que algún cargador la copiara: no filtraba por `cuenta` (caso `EST-0091`: si
BEKURA tiene el campo y SANCORFASHION no, el panel pintaba verde algo que a una
cuenta le falta), y la precedencia `'*'` vs categoría no estaba definida. **El
primer arreglo de la precedencia estaba mal** y solo se vio corriéndolo: poner
`and obligatorio` en el mismo WHERE que el `distinct on` descartaba la fila de la
categoría específica justo cuando decía `obligatorio=false`. Ahora se resuelve en
un CTE antes de filtrar. Medido en sandbox, no razonado.

NO se agrega columna de restricciones (título 60 en ML vs 75 en Amazon, 2
decimales en Walmart): esta tabla contesta "¿está el campo?", no "¿está bien?", y
una columna que nace vacía es como nacieron `parent_sku` y `has_variations`.
Queda dicho para que la ausencia sea decisión y no olvido. Versión 0.114.0.

---

### v0.114.1 — Manifiesto al día: el candado vuelve a decir la verdad

`schema_manifest.json` regenerado contra producción tras aplicar la 0018 y correr
el cargador. Ahora incluye `channel.field_requirements` y `core.canonical_fields`
(45 tablas).

**El candado de paridad vuelve a `PARIDAD OK`.** La regeneración anterior (v0.108.0)
había destapado que `enrich.market_*` estaba reestructurada fuera de las
migraciones — `market_search_term` con 464 filas sin DDL en archivo, `termino_id`
donde la 0011 ponía texto. Eso **ya no aparece**: el equipo escribió sus
migraciones 0015 y 0017 mientras tanto, y producción y el árbol vuelven a decir
lo mismo. Versión 0.114.1.

---

### v0.115.0 — El semáforo, y por qué tiene tres luces y no dos (Eduardo)

Cierra el trabajo de los cuatro pasos: el panel ya puede contestar *"¿qué le
falta a este SKU para publicarse en este canal?"*. Cruza lo que el canal exige
(`channel.field_requirements`, v0.114.0) contra lo que tenemos
(`enrich.channel_content`, v0.108.0).

**Tres estados, no dos:**

- **`incompleto`** — faltan campos que nadie llena. Ámbar, con la lista.
- **`ok`** — están todos. Verde.
- **`sin_requisitos`** — **no lo sabemos**. Gris, y lo dice con todas sus letras:
  *"No quiere decir que esté completo, quiere decir que no sabemos."*

Ese tercero es el que importa. De los 558 productTypes de Amazon solo hay 12 con
requisitos leídos: pintar verde una categoría sin leer sería mentir, y el sello
`leido_at` existe justo para no hacerlo.

Y aparte, los campos con respaldo no cuentan como faltantes: se listan como *"los
llena el publicador solo"*. Sin esa distinción, un disfraz mostraría 6 campos en
rojo cuando solo 4 lo son.

**`brand` se queda en `"Generic"`** (decisión de Eduardo). No se hace campo
editable: se le pone `default_value`, así que el semáforo lo cuenta como
automático y no como hueco. Refleja lo que el publicador ya hace —
`_attr_from(atributos,"BRAND","Generic")`: del producto si lo trae (3,060 de
7,264), "Generic" en los otros 4,204. La divergencia con ML, que publica todo
como "Ferrahome", sigue abierta y es decisión de negocio.

**Verificado por HTTP y con el servicio real contra el sandbox**, los cuatro
comportamientos: categoría sin cargar → `sin_requisitos`; COSTUME_OUTFIT sin
contenido → 4 faltantes + 2 automáticos; tras guardar título, descripción y
bullets → queda solo `fabric_type`; sin ese último → `ok`. `tsc --noEmit` limpio.

**NO verificado: el semáforo en el navegador.** Mismo bloqueo que la v0.109.0 —
el listado de Productos está fijo en `canal:"general"` (`page.tsx:94`), que lee
WooCommerce en vivo, y `env.staging` no tiene esas credenciales. Sin productos no
hay modal que abrir. Versión 0.115.0.

---

### v0.115.1 — El cargador aguanta una corrida de 541 tipos

La carga inicial fueron 12 productTypes. Para los 541 restantes hacían falta dos
cosas que a esa escala dejan de ser opcionales:

- **`--saltar-cargados`**: omite los tipos que ya tienen requisitos. El upsert ya
  hacía la carga idempotente, pero re-correrla repetía las llamadas a Amazon. Con
  esto una corrida cortada se retoma sin volver a pedir lo que ya está.
- **Pausa de 5 s tras un fallo** (antes seguía al mismo ritmo de 1 s). Casi todo
  fallo a esta escala es corte por exceso de peticiones, y seguir igual garantiza
  que los siguientes también caigan — es como Walmart nos tumbó 19 de 24
  productos en el segundo lote.

El token de SP-API ya venía cacheado (`amazon._access_token`), así que 541 tipos
no son 541 renovaciones. Versión 0.115.1.

---

### v0.115.2 — Amazon completo (553 tipos), y la trampa del pooler que ya nos había mordido

**Los 553 productTypes de Amazon cargados: 64,125 requisitos.** Cero tipos del
catálogo sin cubrir.

```
3,354 obligatorios
  2,201  con canónico  → el panel puede llenarlos
  1,643  con respaldo  → el publicador los pone solo
     54  SIN NADIE
```

Los 54 huérfanos son **dos grupos, no 54 problemas**: `fabric_type` en 45 tipos
de ropa —obligatorio en Amazon, sin canónico y sin respaldo, hoy nadie puede
llenarlo— y 9 campos de libro (`author`, `pages`, `publication_date`…) todos en
un solo productType, que Kubera no vende: casi seguro entró por una detección
automática equivocada.

**El error que costó media corrida.** `tipos_mas_usados` abría producción con
`set_session(readonly=True)`, que parece lo prudente y es lo contrario: las DSN
entran por el pooler de Supabase en modo TRANSACCIÓN, varios clientes se turnan
la MISMA conexión del servidor, y un ajuste de SESIÓN se queda pegado y lo
hereda quien la tome después. La carga murió en el tipo 296 con
`cannot execute INSERT in a read-only transaction`, envenenada por su propia
lectura.

**Está documentado en el encabezado de `actualizar_sandbox.py`**, que ya lo
había sufrido el 10-ago — y ese archivo se leyó y se citó horas antes de
escribir el mismo error. Corregido a `set transaction read only`, que muere con
la transacción. Verificado: leer y después escribir en la misma corrida funciona.

Lo grave no era el script: el mismo patrón se usó en las verificaciones de todo
el día contra producción, y una conexión envenenada la puede tomar el backend de
Railway. No hubo incidente —los procesos eran cortos—, pero el riesgo era real.
Queda como trampa #7 en `docs/CONTENIDO_POR_CANAL.md`.

**`docs/CONTENIDO_POR_CANAL.md`** — documento de traspaso del trabajo completo,
escrito para leerse sin el historial: dónde vive cada cosa (JSON dentro de
tablas, y por qué), las tres tablas, el flujo de seis pasos, las decisiones con
su porqué, lo verificado y cómo, las 7 trampas medidas y lo que falta.
Versión 0.115.2.

---

### v0.117.0 — 964 pedidos fantasma: el registro de pedidos deja de preguntarle al espejo

**INCIDENTE del 12-ago-2026, 19:12→23:29 UTC (4 h 17 min).** El paso 1 del
desmantelamiento de PEDIDOS congeló `pedidos_ml` — correcto, kubera ya era el
registro. Pero tres consultas del flujo de ALTA seguían preguntándole a esa
tabla, y una foto detenida contesta con seguridad lo que ya no sabe:

- **El candado de idempotencia** (`pedidos_ml.py:429`) devolvía SIEMPRE "no
  existe", así que cada aviso de ML creaba OTRO pedido en Woo. ML manda ráfagas
  por venta (creada→pagada→enviada): **964 pedidos fantasma, $409,741**, el 85%
  de todo lo creado en la ventana. Es la reincidencia del 17-jul (86 órdenes con
  2-7 copias), ahora por una causa distinta.
- **La marca de agua** (`pedidos_amazon._desde()`) se quedaba fija, pidiendo
  siempre la misma ventana. Amazon **no duplicó por casualidad**: no tuvo
  tráfico en esas 4 h. La bomba estaba armada igual.
- **El dedupe** de los sondeos (Amazon y M2E/TEMU/TIKTOK) veía "sin cambio" al
  revés y reprocesaba.

Los tres lectores se mudan a `channel.orders` en `orders_write` —
`wc_order_id_previo`, `ultimo_actualizado`, `estados_wc`. **Regla de la fuente:
se lee de donde se está escribiendo.** Con kubera arriba, el registro; si kubera
cae, `guardar()` hace que MySQL absorba y ahí sí es la fresca. Nunca al revés.
Un None equivocado en el candado crea un fantasma, así que el error se propaga
en vez de asumir "es nueva".

**Y el vigilante de silencio de ventas**, que leía la misma tabla congelada:
gritó *"sin ventas nuevas en 4.1 h"* en pleno día récord — 1,861 pedidos, el
último de hacía segundos. Ahora mira las tres cuentas en `channel.orders`.
Verificado: vería la última venta hace 1.4 min → no alerta.

Es el mismo error que la v0.102.0 (el acta de channel): **se retira un dominio y
su vigilante se queda mirando la tabla apagada.** Van dos; conviene revisar el
resto antes de apagar costing.

Contención: `ORDERS_ESPEJO_INVERSO=true` (descongela el espejo) paró la
duplicación en el siguiente ciclo — verificado 7 min seguidos en ratio 1.00
exacto. Los 964 fantasma se mandaron a PAPELERA (recuperables), y los 16 que
habían descontado stock se cancelaron primero para que Woo devolviera la pieza.

**El desmantelamiento de PEDIDOS queda desbloqueado**: con los lectores mudados,
`pedidos_ml` ya se puede volver a congelar. Versión 0.117.0.

---

### v0.116.0 — Mercado Libre completo, y el semáforo aprende a mirar dentro (Eduardo)

**Los pasos 1-3 ya servían para ML y no hubo que replicarlos** — y no por
casualidad: la llave `(sku, canal, cuenta)` de `enrich.channel_content` existe
**por ML**, que es el único canal con dos cuentas. Amazon fue el que se adaptó.
Verificado en el caso difícil (`EST-0091`): el mismo SKU guarda y lee contenido
distinto en BEKURA y SANCORFASHION, y el publicador toma el de la cuenta
correcta.

**Paso 4 · `cargar_requisitos_ml.py`.** 1,058 categorías, 2,765 filas.

**ML no encaja igual que Amazon, y eso cambia la forma.** El esquema de Amazon
trae el payload COMPLETO por tipo. `/categories/{id}/attributes` de ML devuelve
**solo la ficha técnica** — 57 atributos para MLM1071, de los cuales **uno** es
obligatorio. Título, precio, stock e imágenes no están ahí: ML los exige para
TODAS las categorías. Por eso el cargador escribe en dos niveles: los comunes
como `categoria_id='*'` (levantados del publicador vendorizado, no de la doc) y
los atributos obligatorios con su `MLM…`. Es justo para lo que existe el
centinela: sin él habría que repetir 12 campos en 1,058 categorías.

**Y el semáforo aprende a mirar DENTRO de `atributos`.** Los obligatorios de ML
no son campos de primer nivel: viven como `{"nombre":"BRAND","valor":"…"}`
dentro de la llave `atributos`. Comparar solo la presencia de la llave habría
puesto el semáforo **en verde con cualquier atributo**, aunque faltara justo el
obligatorio de esa categoría.

Sin columna nueva: **`campo_canonico` dice DÓNDE buscar y `campo` dice QUÉ
buscar.** Si el canónico es `atributos`, se busca por el `nombre` de cada
entrada y se exige que el valor no venga vacío.

Probado con siete casos, los dos que importan:

```
titulo + OTRO atributo   -> faltan BRAND y MODEL   (ya no da falso verde)
titulo + BRAND VACIO     -> BRAND sigue faltando   (presente ≠ lleno)
```

La primera versión los dejaba sin canónico para no mentir en verde, pero el
panel los etiquetaba *"no editable desde el panel"* — falso, los atributos sí se
editan en el Estudio. Mirar dentro resuelve las dos cosas. Versión 0.116.0.

### v0.117.1 — El CLAUDE.md dice la verdad del desmantelamiento (y por qué se revirtió)

La sección de migración afirmaba que las tablas de MySQL estaban congeladas y
advertía sobre los flags de lectura. Tras el incidente de los 964 pedidos
fantasma **los tres espejos se reactivaron** (`CHANNEL_`, `COSTING_` y
`ORDERS_ESPEJO_INVERSO=true`, dale de Eduardo), así que ese texto había pasado
de guía a información falsa para cualquier sesión que entrara mañana.

Lo que ahora dice, y es la lección que costó $409,741 en pedidos fantasma:

> **Congelar una tabla es cambiar el contrato de LECTURA, no solo el de
> escritura.** Verificar que kubera quede al día y que el espejo deje de
> escribir NO alcanza. Un arnés de paridad mide si los datos coinciden, no si
> alguien toma decisiones con ellos. Y un `None` de una tabla detenida no
> significa "no existe": significa "ya no sé".

Se documenta el incidente completo (candado de idempotencia ciego, marca de
agua de Amazon fija —que no duplicó por falta de tráfico, no por diseño—, y el
vigilante de ventas gritando en día récord), el estado real de cada flag, y la
**tabla de lectores pendientes de repuntar** que salió del barrido: el peor es
`fanout_stock.py:260`, que decide a qué publicaciones empujar stock leyendo
`canal_inventario` sin ningún camino a kubera.

Ese barrido entra a F8 como **paso 0**: mientras esos lectores decidan con
MySQL, ningún espejo se puede volver a apagar. Cada dominio se apaga cuando SUS
lectores ya miran a kubera, no antes.

También se corrige la tabla de estado por dominio, que ahora refleja la
asimetría deliberada: las lecturas del panel ya no tocan MySQL (paso 3), pero
las escrituras vuelven a espejarse para los lectores internos que faltan.

Sin cambios de código. Versión 0.117.1.

### v0.118.0 — El fan-out deja de decidir con el espejo: `_destinos` lee kubera

Primer lector repuntado del **paso 0** de F8, y el que más urgía. `_destinos()`
decide **a qué publicaciones se les escribe stock en el marketplace**, y lo
decidía leyendo `canal_inventario` — el espejo MySQL — sin ningún camino a
kubera.

El 12-ago ese espejo estuvo congelado unas horas y dejó el riesgo a la vista:
con una foto detenida, el fan-out le escribe a publicaciones cerradas creyéndolas
vivas y no ve las nuevas. Es la misma causa raíz de los 964 pedidos fantasma del
mismo día — decidir leyendo una tabla que ya no se escribe.

Ahora lee `channel.listings` vía `channel_read.leer_inventario`, que ya traducía
a los nombres de columna de siempre (`item_id`, `stock_real`, `es_full`…), así
que el resto de la función no cambió ni una línea.

**Sin respaldo a MySQL, a propósito**: si kubera no responde, `_destinos` revienta
y ese SKU no se sincroniza esa vuelta. Fallar es barato —el stock se propaga en
el siguiente ciclo—; escribirle stock equivocado a un marketplace, no.

**Medido antes de subirlo** sobre 120 SKUs con publicación, comparando la lista
de destinos de cada fuente: **117 idénticos y 3 con diferencia de una o dos
piezas en `stock_full`** (15/14, 78/76, 9/8) — artefacto de leer las dos bases
con segundos de diferencia mientras el sync corre. En los tres, `es_full=1` en
ambas fuentes, así que la decisión (`FULL/FBA, no se toca`) es la misma. Salida
idéntica en 120 de 120.

Queda en el archivo un detalle que no es de la migración: `fanout_log`, la
bitácora propia del fan-out, sigue en MySQL y así se queda.

Versión 0.118.0.

### v0.119.0 — El sync de ML deja de consultarse en el espejo

Segundo lector del **paso 0**. `_lote_desde_ml` decidía dos cosas leyendo
`canal_inventario` — la misma tabla que ese sync escribe:

- **la rotación**: qué publicaciones tocan esta ronda (lo nunca visto primero,
  luego lo más rancio);
- **el barrido de cierre**: filas que el registro cree vivas (`active`/`paused`)
  cuyo item ya no está en el catálogo de ML, para leerles su estado final.

El segundo es el que se degradaba feo con el espejo congelado. El barrido está
diseñado para **auto-terminarse**: al escribir el estado final, la fila deja de
cumplir el filtro y no vuelve a salir. Con la tabla detenida esa escritura nunca
llegaba, así que las mismas publicaciones se colaban en cada ronda para siempre,
desplazando trabajo real del lote.

Ahora ambos leen `channel.listings` vía dos gemelas nuevas en `channel_read`:
`vistos_ml(cuenta)` y `vivas_ml(cuenta)`.

**Las fechas vuelven NAIVE en UTC a propósito.** `channel.listings.updated_at`
es `timestamptz` y el llamador ordena comparando contra `datetime(1970,1,1)`:
mezclar aware con naive lanza `TypeError` y habría tumbado la rotación del sync
en la primera ronda. Se normaliza en `channel_read` para que el llamador no
cambie.

**Medido antes de subirlo**, por cuenta:

| | vistos (MySQL / kubera) | vivas (MySQL / kubera) |
|---|---|---|
| BEKURA | 2,227 / 2,228 | **2,127 / 2,127** |
| SANCORFASHION | 2,273 / 2,272 | **2,157 / 2,157** |

`vivas` —el insumo con consecuencia— coincide exacto en ambas cuentas. Las
diferencias de ±1 en `vistos` son el espejo yendo un hilo atrás, y ese dato solo
ordena prioridad. El `sorted` real del llamador se ejecutó contra las fechas de
kubera sin reventar.

Se conserva el `try/except` que ya tenían: si kubera no responde, la rotación
degrada a orden arbitrario y el barrido se salta esa ronda — nunca frena el sync.

Versión 0.119.0.

### v0.120.0 — Costos deja de decidir con el espejo (y aparece un bug de 2,270 SKUs)

Tercer lector del **paso 0**. Tres consultas de `costos.py` dejan de preguntarle
a MySQL. Una de ellas destapó un error que llevaba semanas afectando precios.

**1. La categoría ML, y el bug.** `_cat_ml_de` preguntaba PRIMERO a
`categorias_ml` —tabla sin una escritura desde el 22-jul— y solo después miraba
el mapa de kubera. Al medirlo para quitarla:

| | |
|---|---|
| filas en `categorias_ml` (MySQL) | 12,399 |
| filas en `channel.product_category` (kubera) | 13,733 |
| SKUs solo en MySQL | **1** |
| **SKUs con categoría DISTINTA** | **2,270** |

Ejemplo: `edu-0011-pla` decía `MLM456620` en MySQL y `MLM190037` en kubera. Para
esos 2,270 la comisión —y por lo tanto el precio— se calculaba con la categoría
equivocada, porque la tabla congelada le ganaba a **la elección del panel**, que
por regla 2 de la casa es la que MANDA. No era solo una tabla vieja: era una
violación de la regla, silenciosa.

**2. `asegurar_finales`.** Leía `costos_finales` de MySQL para decidir si
recalcular el precio. Con el espejo detenido, un "no tiene precio" falso dispara
recálculos que pisan lo bueno, y un "sí tiene" falso deja al SKU sin precio.
Paridad medida: 4,128 SKUs con precio en ambas bases, **cero diferencias en los
dos sentidos**.

**3. La comisión por categoría** ya consultaba kubera primero; se retira el
respaldo. Una comisión vieja de una tabla detenida se vuelve un precio mal
calculado sin aviso.

**Lo que NO se tocó, y por qué.** El complemento de dimensiones de
`_preparar_base` se queda leyendo MySQL: `costing.costos_finales` no tiene
columnas de dims (contrato v4: viven en `costos_validados`) y **514 SKUs tienen
su tamaño y peso SOLO en el espejo**. Quitarlo hoy los dejaría sin poder derivar
el flete por volumen. Necesita un backfill previo a `costing.costos_validados`
— queda anotado como el último pendiente del paso 0.

Pruebas sandbox `probar_retiro_costing_orders.py`: 11/11. Versión 0.120.0.

### v0.121.0 — Backfill de dims, y el bloqueador real para apagar MySQL

`backend/scripts/backfill_dims_validados.py`: rescata a
`costing.costos_validados` las dimensiones que solo vivían en el MySQL de
`costos_finales`. Aplicado: **36 filas**, todas a las que solo les faltaba
`peso`, rellenando NULOS y sin pisar un solo valor existente.

Dos cosas que el script deja documentadas porque cambian la conclusión:

**El `peso` que se copió es VOLUMÉTRICO, no medido.** Las 36 filas dan
exactamente 0.17 kg/L — se derivó del volumen. No es ideal, pero es el mismo
valor que el código ya usaba vía el respaldo, así que copiarlo preserva el
comportamiento en vez de empeorarlo.

**El 14% de los candidatos trae peso de CAJA MASTER como pieza** y se descarta
con una guarda de densidad > 1.5 kg/L (`mue-0064`: 12×10×10 cm y 224 kg = 185
kg/L). Copiar eso habría inflado el flete por volumen.

**Y el hallazgo que importa: el bloqueador no eran las dims.** De los 514
candidatos, 474 **no tienen fila de costo en kubera en absoluto**:

| | |
|---|---|
| existen en `core.products` | 474 (son productos reales) |
| con publicación VIVA en algún canal | **122** |
| con `costo_producto` capturado solo en MySQL | 474 |

No les falta el tamaño: les falta la semilla de costeo completa. Por eso el
respaldo de `_preparar_base` **sigue en pie** — quitarlo hoy dejaría 122 SKUs
vendiéndose sin poder recalcular su costo.

El script NO los inserta a propósito: una fila con dims y sin costo hace que
`costo_desde_validados` devuelva `costo_total = 0`, y un "cuesta cero" es peor
que un "no sé" — es justo la clase de error que este paso 0 corrige.

Reconstruir esas 474 filas desde `costos_finales` es una decisión de negocio
(qué significa "validado" para un costo derivado), no un movimiento mecánico.
Queda como el último pendiente antes de poder apagar `costos_*` en MySQL.

Versión 0.121.0.

### v0.123.0 — Se cierran los dos huecos que dejó el incidente de los fantasma

Cola del 12-ago. Limpiar los 964 pedidos fantasma y reactivar el espejo dejó dos
cosas a medias que ningún proceso cierra solo.

**1. El registro apuntaba a pedidos en la papelera (145 filas).** Durante el
incidente cada orden de ML generó decenas de pedidos en Woo; `channel.orders`
guarda UNA fila por orden, así que su `wc_order_id` quedó apuntando al ÚLTIMO
fantasma creado. La limpieza conservó el MÁS ANTIGUO —el que tiene la historia
real y el stock— y mandó el resto a la papelera.

No era cosmético: el candado de idempotencia lee ese `wc_order_id`, así que el
próximo cambio de estado del canal se habría escrito sobre el pedido muerto.
`repuntar_channel_orders_wc.py` lo devuelve al superviviente (el no-papelera más
antiguo de la misma orden). Sin sustituto NO se toca la fila: es preferible un
puntero roto y visible a uno inventado. Aplicadas 145, sin sustituto 0.

**2. El espejo tenía un hueco de 143 pedidos.** Reactivar
`ORDERS_ESPEJO_INVERSO` reanudó la escritura HACIA ADELANTE pero no rellenó
hacia atrás (en la ventana congelada kubera registró 446 movimientos y MySQL 3).
Importa aunque MySQL ya no sea el registro: `wc_order_id_previo` cae a MySQL si
kubera no responde, y para esos 143 habría contestado "no existe" — el mismo
fallo del 12-ago esperando otro disparador. `backfill_pedidos_ml.py` los escribe
con el MISMO `ON DUPLICATE KEY UPDATE` del flujo vivo, candado de importes
incluido (comisión y total solo admiten 0 → valor real).

**El orden importaba**: el backfill DESPUÉS del repunte, o se habrían propagado
los 145 punteros muertos también al espejo.

Los dos scripts calculan su diff en cada corrida — idempotentes y
auto-verificables, correrlos de nuevo da cero —, ensayo en seco por defecto,
`--real` para aplicar, y transacción única que revierte todo ante un fallo.

Verificado aparte de la salida de los scripts: 0 de 14,919 filas apuntando a
papelera, kubera 14,919 = MySQL 14,919, y los repuntados apuntan a pedidos vivos
con el espejo coincidiendo.

Pendientes de la auditoría, sin urgencia mientras los espejos estén encendidos:
`sync_woo.py:51` (empuja costo a TODO el catálogo leyendo MySQL, sin camino a
kubera) y `crear_producto.py:583` (la categoría ML al publicar). Versión 0.123.0.

---

### v0.122.0 — 401 SKUs recuperan sus dimensiones en kubera

Corrección a la conclusión de la v0.121.0: **a esos 474 SKUs no les faltaba el
costo, les faltaban las dimensiones.** Los 474 ya tienen su fila en
`costing.costos_finales` con `costo_unitario` y `precio_sugerido` poblados; lo
que no tienen es dims, porque el modelo v4 no puso esas columnas ahí (viven en
`costos_validados`). Leí "sin fila en validados" como "sin costo" y no lo era.

El script ahora da de alta filas **solo con dims**, y eso es seguro porque los
dos únicos llamadores lo toleran — verificado línea por línea:

- `asegurar_finales` corta antes: esos SKUs tienen `precio_sugerido` en kubera y
  retorna ahí. Si llegara, su guarda `costo_unitario <= 0` no calcula nada.
- `_preparar_base` toma las dims de validados y el **costo de `cf`**
  (`costing.costos_finales`), que sí está poblado.

Sin esas dos verificaciones una fila "solo dims" haría que
`costo_desde_validados` devolviera `costo_total = 0`, y un "cuesta cero" es peor
que un "no sé".

Aplicado: **401 altas** y 36 rellenos (v0.121.0). El hueco pasó de 514 SKUs a
**73**, y esos 73 son exactamente los descartados por la guarda de densidad
(peso de caja master capturado como pieza: `mue-0064` a 185 kg/L). De ellos, 14
tienen publicación viva.

```
kubera costos_validados con dims: 14,355 → 14,792
```

Queda pendiente decidir qué hacer con esos 73 antes de retirar el respaldo de
`_preparar_base`: hoy alimentan el cálculo con un peso falso —que infla el costo
de envío— y sin él quedarían sin peso, que el panel ya marca en ámbar. Ninguna
de las dos es correcta; la correcta es recapturarlos.

Versión 0.122.0.

### v0.124.0 — COSTOS cierra el círculo: ni una lectura toca MySQL

Último paso del dominio. Con el corte encendido, `costos.py` ya no consulta el
espejo en ninguna ruta: se retiran el complemento de dims de `_preparar_base` y
la reconsulta de `costo_desde_validados`.

**El complemento de dims.** Existía porque `costing.costos_finales` no lleva
esas columnas y 514 SKUs las tenían solo en MySQL. Se migraron 437 a
`costos_validados` (v0.121.0 y v0.122.0). Los **73 restantes quedan fuera a
propósito**: su peso es el de la CAJA MASTER capturado como pieza (`mue-0064`:
12×10×10 cm y 224 kg = 185 kg/L). Con el complemento calculaban su envío con ese
peso falso —costo inflado, margen peor de lo real, y nadie lo cuestionaba—; sin
él se quedan sin peso, que el panel **ya marca en ámbar**. Un dato ausente y
señalado es mejor que uno falso e invisible. 14 de esos 73 tienen publicación
viva y la solución real es recapturarlos.

**La reconsulta de validados.** Con el corte encendido, un `None` de kubera
ahora significa "este SKU no tiene costo validado" y así se propaga. Medido:
kubera es superset —15,830 filas contra las 15,429 de MySQL, **cero SKUs
exclusivos del espejo**—, así que reconsultarlo solo podía devolver datos viejos.

**Cambia un contrato, y el test lo dice.** `probar_corte_costing.py` verificaba
que una lectura fallida de kubera cayera al espejo con alerta. Ahora verifica
que **PROPAGUE**. Propagar es más seguro que devolver `None`: con `None`,
`_preparar_base` armaría el costo desde un `cf` también vacío y calcularía sobre
CERO — la misma familia de error que dejó 964 pedidos fantasma ese día por
confundir "no sé" con "no hay".

Las cuatro lecturas a MySQL que quedan en el archivo corren **solo con el corte
apagado**, que es el interruptor de reversa.

Pruebas sandbox: `probar_corte_costing.py` 15/15 · `probar_retiro_costing_orders.py`
11/11. Versión 0.124.0.

### v0.125.0 — El flujo de Crear deja de preguntarle a una tabla muerta

Paso 0 de los últimos lectores de costos: `crear_producto.py` (5 sitios) y
`creacion.py` (2). Se miden los cinco antes de tocarlos.

**Cuatro tenían paridad exacta** — `_tiene_costo_base` 15,903 contra 15,903,
`ml_cat_id` 3,813, costos 4,376, contenedores 15,348, cero de diferencia en
ambos sentidos. El quinto no, y ahí estaba el problema.

**`_categoria_curada` leía `categorias_ml`, que nadie escribe desde el 22-jul.**
Kubera y esa tabla discrepan en **2,270 SKUs**, y en todos los muestreados
MySQL traía `predictor` —la adivinanza del detector— contra el `panel` de
kubera, que es la corrección humana. O sea: **la creación violaba la regla 2 de
la casa en uno de cada seis SKUs con categoría**, y llevaba haciéndolo tres
semanas. El ejemplo no podía ser más literal: `TEC-1812-NEG` sale hoy como
`MLM190965` = *Máquinas Sexuales* en kubera; el predictor de MySQL fue el que
lo mandó a "Máquinas de Coser", el incidente que originó la regla. Kubera
además cubre 13,733 SKUs contra 12,399.

El único SKU que parecía existir solo en MySQL resultó ser una fila con un
**salto de línea** pegado al SKU (`'CALZ-0170-NEG-XL\n'`). Cero huérfanos reales.

**Un hueco que sí era real, y se tapó.** El mapa de kubera guarda el id pero el
NOMBRE vive en `channel.categories`, y 75 categorías en uso lo tenían en NULL —
1,468 SKUs que se habrían publicado sin categoría de WooCommerce. MySQL no
podía taparlo: de esas 75 llena **cero** (son elecciones del panel posteriores
al congelamiento). `backfill_nombres_categorias.py` las resuelve contra la API
pública de ML: 75 de 75, sin fallos. Escribe solo sobre NULL. Hueco actual: 0.

Nuevas gemelas: `channel_read.categoria_curada`, `costing_read.costos_por_sku`
y `costing_read.contenedores_por_sku` (en lotes de 800, como las originales).

Con esto **ya no queda ningún lector de costos apuntando al espejo** y
`costos_*` puede volver a congelarse. Falta `competencia_captura.py`, que lee
`categorias_ml` para decidir a qué SKUs seguirles la competencia — no bloquea
nada, solo encoge su alcance.

Pruebas sandbox: `probar_corte_costing.py` 15/15 · `probar_retiro_costing_orders.py`
11/11. Lecturas nuevas verificadas contra producción en solo lectura.
Versión 0.125.0.

### v0.126.0 — Competencia deja el espejo, y una advertencia sobre el mapa de categorías

Últimos cuatro lectores de tablas congeladas. `competencia_captura.py` mide la
competencia de cada SKU, y para saber **a quién medir** le preguntaba a
`productos` y `categorias_ml`.

**Los wc_id de la maestra estaban podridos y nadie lo sabía.** 332 SKUs tienen
un `wc_id` distinto en MySQL que en kubera. Le pregunté a WordPress cuál existe:
**kubera acierta en los 332 y MySQL en ninguno** — sus ids apuntan a posts
borrados de SKUs reciclados. Ese wc_id trae la foto y el título del producto,
así que la captura venía mostrando el producto ANTERIOR. Y `MUE-0163-TEL`, el
caso que el comentario del código señalaba como "la maestra no lo conoce", sí
está en kubera con el mismo wc_id 11154: el respaldo a WordPress deja de ser el
camino normal para volver a ser lo que dice su nombre.

**⚠️ Y una advertencia que corrige lo que escribí en v0.125.0.** Ahí dije que
los 2,270 SKUs donde kubera y MySQL discrepan eran "la corrección humana contra
la adivinanza del detector". Es más matizado. `source` tiene cuatro valores
—`predictor` 5,281, `panel` 5,172, `costos_ml` 2,340, `real` 940— así que
`panel` sí distingue algo real. Pero **2,833 de los 5,172 `panel` están en
bloques de 8 o más SKUs de la misma FAMILIA compartiendo una sola categoría**, y
los bloques no son coherentes: la familia `CORR-` mete pasamanos, frenos de
disco, reposapiés y bolsas de bicicleta en "Corrales" de mascotas; `DEPO`
mezcla aletas de natación con vendas de boxeo; `JUGU`, calcomanías de pared con
un xilófono. Eso es asignación por PREFIJO de SKU, no elección producto por
producto.

No cambia el código —la regla 2 de la casa dice que el panel manda, y el MySQL
congelado tampoco era mejor: era un predictor viejo— pero sí cambia cuánto vale
la salida. **Medir la competencia de un nicho equivocado da un número que
parece dato y es ruido**, y el mismo mapa alimenta la comisión con la que se
calculan precios. Vale una revisión aparte de los bloques por familia.

El JOIN de tres tablas se parte en dos mundos: `productos` y `categorias_ml`
salen de kubera, `ml_progress` sigue en MySQL —es bitácora del publicador, no
está congelada— y se juntan en Python.

**Detalle de las gemelas de lote**: devolvían el SKU con la ortografía de la
base. Con `citext` la consulta acierta pero la llave no coincidía con la que
pasó el llamador, y un `dict.get` fallaba en silencio. Las cuatro (las dos de
costos, `categorias_de` y `nombres_y_wc`) ahora devuelven la llave que se les
pidió.

Nuevas gemelas: `channel_read.skus_de_categoria`, `skus_por_categorias`,
`categorias_de` (parte el `path` en cat1..cat4) y `core_read.nombres_y_wc`.

**Con esto no queda ningún lector interno apuntando al espejo de costos, core ni
categorías.** Los tres pueden congelarse; falta el dale para apagar el
interruptor. Channel y orders siguen pendientes de su propio barrido.

Pruebas sandbox: `probar_corte_costing.py` 15/15 · `probar_retiro_costing_orders.py`
11/11. Lecturas nuevas verificadas contra producción en solo lectura, con
WordPress de árbitro en los wc_id. Versión 0.126.0.

### v0.127.0 — Barrido de channel y orders: el último cierra el círculo

Barrido completo de los dos dominios que faltaban.

**Orders no necesitó ni una línea.** Sus tres lecturas de `pedidos_ml`
(`orders_write`) solo van a MySQL cuando kubera está **caída** — que es
exactamente cuando MySQL es el fresco, porque ahí `guardar()` lo hace absorber
la escritura. Y `ventas_ml` cae a MySQL solo con el flag apagado. La regla ya
estaba escrita en el archivo desde el incidente: *se lee de donde se está
escribiendo, nunca al revés*. Es lo que se ve cuando una lección quedó bien
aprendida: el barrido no encuentra nada porque ya no hay nada.

**Channel tenía dos sin protección**, ambos repuntados:

- `stock_full.py:364` — la SEMILLA del vigilante de FBA. Es la referencia
  contra la que se compara lo que responde Amazon, así que una semilla vieja no
  produce un error: produce una **alerta fantasma** de un movimiento que nunca
  ocurrió. Paridad medida: 1,790 SKUs en kubera contra 1,680 en el espejo,
  **cero con valor distinto**.
- `inventario.plan_dry_run` — decide qué stock habría que escribir en cada
  canal.

**El canal `general` queda fuera del plan a propósito**, aunque el SELECT viejo
lo nombrara. En kubera `general` es el catálogo Woo COMPLETO —13,092 filas—
mientras `canal_inventario` solo tenía 21 legadas: no son la misma cosa.
Copiarlo tal cual habría multiplicado el plan por cuatro con filas que nadie
pidió sincronizar, y además Woo es la FUENTE del stock, no un destino al que
empujarlo (ese camino es `sync_woo.py`). Sin `general`, la paridad es 4,877
contra 4,501 y solo 2 filas viven únicamente en el espejo.

Nuevas gemelas: `channel_read.stock_fba_amazon` y `channel_read.no_full`.

**Con esto los cinco dominios tienen el círculo completo.** Ningún flujo vivo
lee ya del espejo de MySQL. Lo que queda apuntando ahí son scripts de
mantenimiento que se corren a mano (`alinear_ml_drop`, `marcar_amazon_muertas`,
`publicar_walmart`, `sync_odoo_woo_seguro` y cuatro más): dejarán de servir
cuando se retire el esquema y se repuntan o archivan en F8. `channel_mirror` y
`etl_channel_listings` leen MySQL por diseño — son el espejo.

CLAUDE.md: la tabla de "lectores por repuntar" se reemplaza por la de cerrados,
con la versión en que cayó cada uno.

Pruebas sandbox: `probar_corte_orders_channel` 20/20 · `probar_retiro_channel`
5/5 · `probar_corte_core_categorias` 12/12 · `probar_corte_costing` 15/15 ·
`probar_retiro_costing_orders` 11/11. Versión 0.127.0.

### v0.128.0 — Los tres blockers que faltaban para poder apagar MySQL

Barrido exhaustivo de los lectores de las cinco tablas que se congelan al
apagar los espejos (`costos_validados`, `costos_finales`, `costos_logs`,
`pedidos_ml`, `canal_inventario`). Aparecieron tres que ningún barrido anterior
había tocado, y el primero era grave.

**1. El TURNO del sync de 15 minutos** — `inventario.py`, camino ML y camino
Amazon. El `LEFT JOIN canal_inventario` de esas dos consultas **no traía
datos: ordenaba**. `ORDER BY (ci.sku IS NULL) DESC, ci.updated_at ASC LIMIT n`
es lo que hace que el barrido recorra todo el catálogo corrida a corrida.

Con la tabla congelada, `updated_at` deja de avanzar → el orden queda fijo →
**el sync barre los mismos N SKUs cada 15 minutos y el resto del catálogo no se
vuelve a observar nunca**. Y ese sync es lo que alimenta `channel.listings`: la
fuente de verdad dejaría de refrescarse para casi todo el catálogo **sin un
solo error en los logs**. Es la misma familia que los 964 pedidos fantasma —
una lectura congelada que decide, aquí *a quién mirar*.

El turno ahora se arma con `channel.listings.updated_at`. Verificado con datos
reales: BEKURA 1,944 publicaciones con 47 nunca vistas al frente, y **584 marcas
de tiempo distintas** (1,844 en SANCORFASHION) — o sea, el orden avanza de
verdad. `ml_progress` y `amazon_progress` siguen en MySQL porque son bitácoras
del publicador, vivas; el orden se arma en Python sobre ~1,900 filas.

**2. `sync_woo` le habría escrito costos viejos a la tienda.** Su
`_costos_finales()` no es un dato de pantalla: se compara contra la meta `costo`
del producto en WooCommerce y, si difiere, **se escribe**. Congelado, cada
recálculo del panel se desharía en el siguiente barrido: la tienda volvería al
costo viejo. Ahora sale de kubera. Paridad medida: 4,376 contra 4,376, **cero
SKUs con valor distinto** — el repunte no mueve un solo precio hoy.

**3. El listado de publicaciones ML** (`meli.listar`) mostraba el precio del
espejo. Ahora se le pega el precio vivo de `costing.costos_finales`. Cuando se
ordena POR precio no basta con reemplazarlo al final —el `ORDER BY` del SQL
usaría el viejo—, así que en ese caso se trae el conjunto filtrado (≤2k filas
por cuenta), se le pega el precio vivo y se ordena y pagina en Python.

**Lo que se revisó y está limpio** (verificado, no supuesto): el ETL de las
06:15 no borra nada y Woo/Odoo tienen precedencia sobre lo congelado, así que
no se contamina; `seam_gap` solo mide `name/wc_id/status`, que vienen de Woo;
la alerta de silencio de ventas ya lee kubera; `orders_write` y `ventas_ml`
solo caen a MySQL con kubera caída o el flag apagado. Quedan dos cosméticos: la
bitácora de costos del panel (`crear.py:583`, últimos 10 movimientos) dejaría de
mostrar los nuevos, y `studio._dinero_mysql`, un respaldo que hoy no corre
porque WordPress está configurado.

No pude leer el valor de `SYNC_DESDE_ML` en Railway (el token OAuth devuelve
solo nombres), así que **se endurecieron los dos caminos**, el activo y el de
respaldo. Es lo correcto igual: la variable se puede voltear.

Pruebas sandbox: `probar_retiro_channel` 5/5 · `probar_corte_orders_channel`
20/20 · `probar_corte_costing` 15/15 · `probar_retiro_costing_orders` 11/11 ·
`probar_corte_core_categorias` 12/12. Versión 0.128.0.

### v0.129.0 — El cron de las 06:15 deja de abrir el MySQL viejo

Los dos ETLs que corren encadenados a las 06:15 leían cuatro tablas de
`kubera_ml`. Eran **el último proceso vivo que dependía del esquema viejo para
algo que no fuera el espejo**: mientras leyeran de ahí, retirarlo habría dejado
a los SKUs de packing list fuera del padrón sin que nadie se enterara.

**`etl_core_products_v2`** — las tres fuentes de EXISTENCIA pasan a su gemela en
kubera. Medido antes de tocar nada: `costos_validados` 15,429→15,830,
`categorias_ml` 12,839→13,733, `costos_finales` 4,376→4,376, y **ninguno de los
SKUs que cambian de lado provoca un alta**: todos ya están en el padrón, porque
este ETL nunca borra.

`productos` **se retira como fuente**. Su precedencia (1, debajo de Woo) solo
decidiría para SKUs ausentes de WooCommerce, y **no hay ninguno**: los 5,381
están en Woo, que gana siempre. `odoo_id` se sobreescribe desde el Odoo vivo y
`has_variations` está muerta.

**Verificado con un A/B en seco contra producción**, que es lo que da la
confianza: original 3 altas · 26 updates; repuntado 3 altas · 1,718 updates
**tocando solo `source`**, y con la **misma lista de `seam_gap`** (los mismos
SEG-0030-* por títulos editados en Woo). Mismo padrón, mismo diagnóstico.

En el primer intento eran 6,443 updates porque las filas perdían la palabra
`productos` de su `source`. Haber nacido en esa tabla es un **hecho histórico**:
si el padrón ya lo dice, se conserva. Borrarlo habría reescrito 5,381 filas para
perder información.

**`etl_channel_categories`** — se retira `categorias_ml`. Nadie la escribe desde
el 22-jul y sus 12,839 asignaciones ya están cargadas; releerlas cada mañana
solo re-afirmaba lo mismo. La fuente viva es la elección del panel
(`wp_postmeta.ml_categoria_id`), que además MANDA por la regla 2. Dry-run contra
producción: árbol 0 inserts / 0 updates, asignaciones 8 altas del panel.

**Y se arregla de raíz el hueco de nombres de v0.125.0.** Cuando el panel elegía
una categoría desconocida, este ETL insertaba el nodo **sin nombre**, esperando
a un "builder de identidad" que nunca llegó — así se acumularon las 75
categorías mudas que dejaban 1,468 SKUs sin categoría de WooCommerce al
publicar. Ahora se le pregunta a la API pública de ML. Con `urllib` y no
`httpx`: el contenedor de este cron solo instala `pymysql` y `psycopg2-binary`.
Si la API falla, el nodo entra sin nombre como antes y el hueco se ve — no se
inventa un nombre.

Pruebas: A/B en seco contra producción de los dos ETLs (no escriben nada sin
`--real`). Versión 0.129.0.

### v0.130.0 — El detector de fallas silenciosas (y lo que encontró antes de apagar)

Un arnés de paridad ya no sirve para vigilar el apagado de los espejos: MySQL
deja de ser la referencia. Lo que hay que vigilar es otra cosa — **que kubera
siga MOVIÉNDOSE**. Porque congelar una tabla no produce errores, produce datos
que dejan de moverse mientras todo parece bien. Los 964 pedidos fantasma no
lanzaron una sola excepción.

`vigilar_congelacion.py` toma cinco latidos, cada uno con su umbral: que el
turno del sync avance, que entren pedidos, que se escriban costos, que el padrón
reciba altas, y que las tablas del espejo estén efectivamente quietas (lo que
confirma que el flag tomó efecto).

El detalle que lo hace funcionar: **el turno se mide con la marca MÁS VIEJA, no
con la más nueva.** Con el barrido atorado los mismos SKUs se refrescan cada 15
minutos y el "último visto" se ve perfecto — justo el punto ciego que dejó pasar
el incidente.

**Y en su primera corrida, ANTES de apagar nada, encontró algo:**

| Última revisión | Mercado Libre | Amazon |
|---|---|---|
| < 6 h | 196 | 25 |
| 6–24 h | 166 | — |
| 1–2 días | 214 | 35 |
| 2–7 días | 2,134 | 93 |
| **> 7 días** | **1,653** | **1,237** |

**2,890 publicaciones VIVAS llevan más de 7 días sin revisarse** — el 64% de las
vivas. El barrido toca ~77 por hora y a ese ritmo no le da la vuelta al
catálogo.

**No bloquea el apagado, y conviene entender por qué**: el mismo barrido escribe
las dos tablas, la de kubera y el espejo, así que **las dos están igual de
viejas**. Apagar la copia no cambia la frescura de nada — no hay riesgo
diferencial. Es un problema del barrido, previo e independiente, que nadie había
medido porque nadie miraba la marca más vieja.

Por eso el umbral de ese latido quedó en 240 h y no en 2: mide **empeoramiento
contra la línea base real**, no salud. Un detector calibrado contra un ideal que
no existe grita todos los días y se vuelve ruido.

Correrlo antes de apagar deja la línea base; correrlo después dice si algo dejó
de moverse. Versión 0.130.0.

### v0.131.0 — El procedimiento de apagado, escrito para el equipo

[docs/APAGADO_ESPEJOS_MYSQL.md](docs/APAGADO_ESPEJOS_MYSQL.md): qué se apaga y
qué NO (ningún flujo de negocio: solo la copia a la base vieja), la línea base
del detector antes de tocar nada, qué debe verse después, y cómo se revierte —
una variable, sin deploy, y se puede revertir **un dominio solo**.

Lleva dos advertencias que no son del apagado pero que quien lo ejecute tiene
que saber: **no apretar el botón de sincronizar stock** (pondría en cero 8,120
SKUs porque Odoo no conoce el stock de drop) y no correr a mano los ocho
scripts de mantenimiento que todavía leen MySQL.

Y deja anotado el problema previo que salió al medir: el barrido de 15 min no
le da la vuelta al catálogo —2,890 publicaciones vivas con 7+ días sin
revisar—, con la explicación de por qué no bloquea el apagado (las dos tablas
están igual de viejas, las escribe el mismo barrido).

CLAUDE.md apunta al procedimiento desde la sección de migración. Versión 0.131.0.

### v0.132.0 — La bitácora de creación se muda a kubera (y traía la hora mal)

`crear_logs` era la última bitácora en MySQL. Ya se espejaba a
`ops.process_log`, así que parecía un repunte de trámite. No lo fue: al medir
salieron tres cosas.

**1. Faltaba la primera semana.** `crear_logs` arrancó el 15-jul y el espejo se
encendió el 23. Esas **378 filas** nunca viajaron. Cargadas.

**2. El `wc_id` se perdía en el camino.** El espejo lo excluye del detalle
(`{k: v for k, v in extra.items() if k != "wc_id"}`) y `ops.process_log` no
tiene esa columna. Pero `/auditoria` lo necesita: es con lo que le pregunta a
WooCommerce si el producto sigue vivo. **1,629 filas rellenadas** y el espejo
corregido para que ya no lo tire.

El relleno destapó un clásico: `not (detalle ? 'wc_id')` sobre un `detalle`
NULO devuelve NULL, no TRUE, así que esas filas se caían del WHERE en silencio.
Y eran justo las que más importaban — el espejo guarda `detalle` nulo cuando lo
único que traía era el wc_id que él mismo excluye. El primer intento rellenó
503 de 2,132.

**3. Y la peor: `created_at` guardaba la hora de la ESCRITURA DEL ESPEJO, no la
del evento.** Para el camino normal da igual (décimas de diferencia), pero un
evento reprocesado desde `espejo_kubera_log` entra horas después. **60 filas con
más de 1 h de desfase, la peor con 17.6 h.**

No es cosmético: el historial busca el ÚLTIMO evento de cada SKU, así que una
fila con la hora equivocada se cuela al frente. Medido con el arnés de paridad:
**invertía el estado de 50 SKUs** — productos terminados que el panel mostraba
"procesando", y 49 que no aparecían al filtrar por "completado". Corregidas las
60, y `kubera_mirror` ahora recibe la hora del evento para que no vuelva a
pasar.

**Las gemelas** (`bitacora_read`) ordenan por FECHA, no por id. En MySQL el id
era monotónico; en kubera es una secuencia, y el backfill cargó julio DESPUÉS de
todo agosto — ordenar por id habría mostrado julio como "lo último".

Paridad final contra el par MySQL, **10 de 10**: mismo total (275), mismos SKUs,
mismo estado por SKU (0 difieren), mismo wc_id (0 difieren), mismos conteos
filtrando por estado, mismo historial por SKU, y los 270 completados de
`/auditoria` con su wc_id.

Pruebas sandbox: 15/15 · 11/11 · 20/20 · 5/5 · 12/12. Versión 0.132.0.

### v0.139.0 — Competencia: todo el raspado por actor de Apify, y se va el modo local

Cuatro cambios que sostienen una sola regla: **lo que la API de Mercado Libre da
se pide por API; lo que no, se raspa con un ACTOR de Apify** — nunca con un
navegador local, que ML corta a las ~50 consultas por IP. Pasó dos veces a mitad
de una captura.

**Selenium retirado.** Borrados `competencia_mas_vendidos.py`,
`competencia_busqueda.py` y sus cuatro scripts. Con ellos se va la cadena que
solo existía para el navegador —`correr`, `medir_sku`, `_medir_busqueda`,
`_medir_categoria`— y los endpoints `/correr`, `/corrida` y `/detalle`, ninguno
con consumidor en el panel. La captura raspa con Apify; lo único que le faltaba a
esa ruta, `id_pagina`, se deriva ahora del propio href. Son DOS ids distintos y
confundirlos cuesta: el `wid` es el ITEM real y sirve para `/visits`; el de la
RUTA (`/up/MLMU…`, `/p/MLM…`) es el que `/products/{id}/items` necesita para
resolver la subcategoría de cada fila, o sea los nichos.

**Actor de respaldo.** `apify~puppeteer-scraper` entra cuando el principal no
trae nada aprovechable. La primera versión preguntaba `if not filas` y nunca se
disparaba: cuando el actor no logra raspar, la corrida termina SUCCEEDED y el
dataset trae una entrada por URL con `items: []` — no está vacío, pero no hay
nada que guardar. La decisión se movió a quien llama, que es quien sabe qué
cuenta como útil. Las dos `pageFunction` se reescribieron en el subconjunto común
de ambas APIs (`page.waitForTimeout` es de Playwright), así que la misma función
corre en los dos actores. El de respaldo exige aprobar permisos una vez en la
consola de Apify: sin eso responde 403.

**Fuera el actor especializado.** `apify_ml_actor` y sus dos funciones muertas.
Cobraba $0.09 por corrida más $0.003 por item, devolvía ~5 orgánicos contra ~48 y
no etiquetaba los resultados con la consulta que los trajo.

**Fuera SQLite y fuera la memoria.** `competencia_store` pierde el DDL, `_con()`
y `asegurar_schema()`: sus funciones exigen `SUPABASE_DB_URL` y revientan sin
ella. El modo local costaba de dos maneras — en Railway el FS es efímero y el tab
arrancaba vacío, y cuando el archivo sí existía una captura escrita ahí parecía
haber funcionado sin que nadie la leyera jamás. Y la captura escribe por tanda de
20 en vez de acumular todo y guardar al final, que es lo que hizo perder 49
categorías ya raspadas cuando ML bloqueó a media corrida.

**Migración 0018: el buscador recupera sus visitas.** La 0017 había retirado
`visitas_30d` de `market_search_results` leyendo "4 filas llenas de 1,816" como
columna muerta. Estaba desconectada del escritor, no muerta: el pipeline SÍ pide
esas visitas y el subidor viejo no las incluía en su INSERT. Vuelve, con índice
parcial, y el panel las pinta junto al precio — en «soldadora inverter» el #2 es
el más barato Y el que se lleva 69,440 visitas, tres veces más que el #1; con
solo el precio esa lectura no aparece. Al rellenar se volvió a caer en la trampa
conocida: `/visits` sobre un id de CATÁLOGO responde 0 sin error, y 1,893 de las
1,894 filas en cero venían de un `/p/` o un `MLMU`.

Cierre de las tres categorías trabajadas: Deportes y Fitness 77/85 subcategorías
con top y 143/143 SKUs con término medido; Herramientas 49/55 y 99/99; Recuerdos
14/18 y 32/32. Los huecos restantes se verificaron uno por uno contra
`/highlights` y `/trends`: son categorías de las que ML no publica nada.
`docs/COMPETENCIA_ORIGEN_DE_DATOS.md` documenta de dónde viene cada campo de cada
tabla, con su cobertura medida. Versión 0.139.0.

---

### v0.164.1 — core-etl-v2 vuelve a timbrar por un par de días (Eduardo)

Ventana abierta a propósito para comprobar si la v0.164.0 cerró el hueco de los
padres. Es la única forma de saberlo sin entrar a /migracion a mano.

  · sale `ok` dos días seguidos → el arreglo sirvió, y toca decidir si se deja
    vigilado (lo sano: es el único auditor del seam) o se vuelve a callar.
  · sale `con_deltas` → queda otro hueco. Las altas de core.products a la hora
    de la corrida dicen exactamente qué SKUs fueron, igual que el 14-ago.

`categorias-etl` sigue callado. REVISAR EL 16-AGO-2026: si nadie lo mira, la
ventana se queda abierta y vuelve el aviso diario que se pidió callar. Versión 0.164.1.

---

### v0.164.0 — El seam del padre: dos huecos que el 14-ago dejó a la vista

Investigación de las 4 altas que el ETL tuvo que hacer el 14-ago. Son DOS causas
distintas, no una.

**Dos eran padres de variantes nuevas** (`CALZ-0318`, `COM-0027`): la variante
se creó 17:15:49 UTC, el padre cambió de fecha 3 s después, la variante SÍ se
registró en vivo y el padre no. Es el patrón que arregló la v0.103.0 — y el
arreglo está intacto: se comprobó tocando `CALZ-0318-EST` y el padre entró un
segundo después (02:38:56 / 02:38:57). Así que el hueco estaba en otro lado.

**EL CANDADO SELLABA ANTES DE ESCRIBIR.** `_registrar_acta` marcaba la foto en
`_WOO_ULTIMO` y DESPUÉS llamaba a `core_write.registrar`. Un fallo de escritura
dejaba el SKU sellado igual, y como la foto de un padre (nombre, estado, wc_id)
casi nunca cambia, todos los eventos siguientes se descartaban por "sin cambios":
el padre se quedaba sin acta hasta que el proceso reiniciara. Misma trampa del
12-ago — un caché que dice "ya está atendido" sin saberlo. Ahora se sella
DESPUÉS del write.

**Y el padre ya no depende de que el canal mande `parent_id`.** Si el payload no
lo trae, se resuelve por `post_parent` desde el id del propio evento
(`wp_db.padre_de`). El vínculo vive en wp_posts; no hacía falta confiar en un
campo que el canal puede omitir según el tema.

**Las otras dos** (`COC-0011`, `COC-0012`) nacieron 19:41 UTC, dentro de la
tormenta de deploys fallidos del `python-multipart`. Hipótesis no comprobable
hoy —los eventos ya no están—, pero encaja: mientras el backend no arranca, los
webhooks de esa ventana se pierden. Daño colateral de aquel bloqueo.

Probado contra datos reales: `padre_de` acierta en las dos variantes y devuelve
None en producto simple / id inexistente; `_acta_del_padre` resuelve igual con y
sin `parent_id`; y el candado NO sella tras un fallo (el siguiente evento
reintenta) pero sí tras un write bueno (el repetido no vuelve a escribir).
Versión 0.164.0.

---

### v0.163.0 — Las actas de los dos ETLs dejan de timbrar en Slack (Eduardo)

Petición de Eduardo tras tres días de actas rojas. Nuevo `_DOMINIOS_SIN_ALERTA`
en `routers/migracion.py`: el acta **se sigue escribiendo** y se sigue viendo en
/migracion con su racha; lo único que se quita es el aviso de Slack. Distinto de
`_DOMINIOS_RETIRADOS`, donde el cron ya ni genera acta.

**Lo que se calla NO es un falso positivo**, y conviene que quede escrito. Las
tres alarmas tuvieron causas reales y distintas:

- 12-ago: `seam_gap` 17 y 15 — 4 padres variables que el webhook no anunciaba
  (arreglado en la v0.103.0) y 3 `odoo_only`.
- 13-ago: 3 altas `odoo_only` — SKUs nacidos en Odoo que nadie cruzó a Woo. Es
  el alta manual que la v0.137.0 automatiza con cron.
- 14-ago: 4 altas de productos de WOO. Esa sí es fuga del seam y sigue sin
  diagnosticar.

Con este cambio **ningún dominio timbra ya por actas**: los otros tres están
retirados, así que `_revisar_actas` queda sin nada que vigilar. `seam_gap` es lo
que mide que el dual-write en vivo no perdió nada — la misma familia de señal
que delató los 964 pedidos fantasma del 12-ago. Mientras esto siga así hay que
mirar /migracion a mano.

Reactivar = quitar el dominio del conjunto. Versión 0.163.0.

---

### v0.162.0 — El vigilante de FBA también esperaba con el backend detenido

Mismo defecto que v0.160.0, en la otra mitad del mismo archivo. `revisar_fba`
—el job que detecta ingresos a la bodega de Amazon comparando fotos— es `async`
y hacía, dentro de la corrutina: el token de Amazon (consulta a la BD), la foto
previa (dos consultas más) y el **barrido PAGINADO** del inventario FBA con
`httpx.Client` **síncrono a 30 s por página**. Cada vez que le tocaba turno
—y le toca cada pocos minutos— detenía el backend entero.

Este no lo cazó el vigilante: se encontró **leyendo**, buscando el mismo patrón
después de que el volcado de las 23:46 dijera "petición HTTP síncrona" sin poder
nombrar al culpable. Encontrar por patrón lo que el instrumento enseñó a
reconocer es, seguramente, el mejor rendimiento de haberlo construido.

Toda la recolección sale a un hilo de una sola vez. La comparación, la decisión
y el ajuste a Woo **no cambian**.

---

### v0.160.0 — El aviso de FULL esperaba a Mercado Libre con el backend detenido

Primer volcado legible del vigilante (v0.159.0), y señaló algo que ninguna de las
capas anteriores había tocado:

```
EVENT LOOP ATASCADO 5.5 s | PRINCIPAL: _client.py:825 request > … >
  http11.py:177 _receive_response_headers > sync.py:128 read > ssl.py:1105 read
```

`sync.py` y `ssl.read`: una petición HTTP **síncrona** esperando respuesta, con
el event loop detenido. El dueño es `stock_full.procesar_operacion`, que atiende
el webhook `fbm_stock_operations` (los movimientos de la bodega FULL de ML). Es
`async`, pero hacía **tres `httpx.get` síncronos con timeout de 25 s cada uno**:
la operación, el inventario y el ítem. Más el token y el candado de
idempotencia, que son consultas a MySQL. Con los avisos de FULL llegando en
ráfaga —se ven decenas por minuto en los logs— el backend pasaba buena parte del
tiempo parado esperando a ML.

Las tres lecturas se agrupan en una función y salen a un hilo de una sola vez;
el candado de idempotencia también. La lógica de decisión (la tabla de tipos, la
verificación cruzada, el tope de cordura) **no se tocó**: sigue corriendo igual y
en el mismo orden.

Es el mismo defecto de v0.157.0 y v0.158.0 en su tercer disfraz: kubera, MySQL y
ahora HTTP. La regla que queda escrita: **en una corrutina, nada que espere a la
red o al disco puede llamarse de forma síncrona.**

---

### v0.159.0 — El vigilante habla en una sola línea (o el volcado no sirve)

El primer volcado del vigilante llegó **revuelto**: Railway parte los mensajes
multilínea en registros sueltos y los reordena por milisegundo. Resultado: se
veían los marcos, pero no cuáles eran del hilo principal — que es exactamente el
dato por el que existe el instrumento. Hubo que deducirlo por el nombre del
hilo, y en el atascón de las 23:24 ya no se pudo.

Ahora la pila viaja **encadenada en una línea**, del marco más antiguo al más
reciente (`archivo:línea función > …`); el último eslabón es dónde está parado.
De los demás hilos solo se imprimen los que tienen código NUESTRO: los del pool
esperando trabajo llenaban el volcado sin decir nada.

Un instrumento que no se puede leer cuando hace falta no es un instrumento.

---

### v0.158.0 — Análisis deja de congelar el backend mientras consulta

Con el camino de ventas e inventario ya en hilos (v0.157.0), el vigilante del
event loop volvió a hablar — y ahora señalaba a un solo archivo:

```
EVENT LOOP ATASCADO 5.6 s   routers/fulfillment.py:1494 -> supabase_db.fetch_all
EVENT LOOP ATASCADO 6.2 s   routers/fulfillment.py:1519 -> _envio_real_en_filas -> fetch_all
```

Son las lecturas de la tabla de Análisis. Mismo defecto, otro archivo: 25
llamadas `sdb.fetch_*` síncronas dentro de funciones async. Medido: 0.98 s el
query base del dashboard, y hasta 6 s la tabla — todo eso con el backend
entero detenido, sin atender a nadie más.

Se añaden tres envolturas (`_fetch_all`, `_fetch_one`, `_fetch_scalar`) que
hacen el mismo query en un hilo, y las 25 llamadas pasan por ahí. Misma
consulta, mismos parámetros, mismo resultado: lo único que cambia es que el que
espera es un hilo y no el backend. **Toda lectura nueva de este router va por
las envolturas.**

Medición en producción antes de este commit (v0.157.0, con el camino de ventas
ya arreglado): picos de 8 s al arrancar y estabilización en **0.45 s**, sin un
solo timeout — contra el 0.4 → 3.6 → 13 s → timeout de antes, que no se
recuperaba nunca.

---

### v0.157.0 — El vigilante habló: kubera se consultaba dentro de la corrutina

**La causa, por fin, señalada por el propio backend.** El vigilante del event
loop (v0.156.0) volcó la pila del hilo principal en cinco atascones seguidos.
Los cinco terminan en lo mismo:

| Hora | Dónde estaba parado el backend |
|---|---|
| 23:04 | webhook ML → `orders_write.wc_order_id_previo` → `supabase_db.fetch_one` |
| 23:05 | webhook ML → `orders_write.guardar` → `kubera_mirror._up_channel_order_items` |
| 23:06 | webhook ML → `inventario._upsert` → `channel_mirror.escribir_tanda` |
| 23:09 | sync de inventario → `inventario._upsert` → `channel_mirror.escribir_tanda` |
| 23:10 | sync de inventario → `inventario._upsert` → `channel_mirror.escribir_tanda` |

**psycopg2 es bloqueante.** Cada aviso de venta de ML y cada tanda del sync de
inventario escribían a kubera DENTRO de la corrutina: mientras Postgres
contestaba, el backend entero no atendía a nadie. Con la CPU al 1.2% y la
memoria plana, porque no estaba trabajando — estaba esperando.

Esta lección ya estaba escrita en este repo. El docstring de `_procesar_ml` la
dice textual desde el 15-jul: *"psycopg2 es I/O bloqueante y ejecutarlo directo
en la corrutina congela el event loop entero"*. Se aplicó al espejo del evento
(`asyncio.to_thread(_guardar_supabase, …)`) y **no** a las escrituras que llegaron
después con el corte de la migración — que son las del camino caliente.

Por eso empezó ahora y no antes: desde el corte, kubera es la escritura
PRIMARIA de pedidos e inventario. Antes esas líneas iban a MySQL y el bloqueo
era corto; ahora cada venta son varios viajes a Postgres, y las ventas de ML
llegan en ráfaga.

**El arreglo**: las tres escrituras salen a un hilo.

- `pedidos_ml`: el candado de idempotencia (`wc_order_id_previo`) y el registro
  completo (`orders_write.guardar` / los dos `espejar`) van por `to_thread`.
- `inventario`: `_upsert` era síncrono y lo llamaban CINCO funciones async. Se
  añade `_upsert_async` y los cinco pasan por ahí.

Sin cambio de comportamiento: se escribe lo mismo, en el mismo orden, con los
mismos candados. Lo único que cambia es QUIÉN espera — un hilo en vez del
backend completo.

**Nota de método.** Este incidente costó tres diagnósticos: el webhook de TikTok
(v0.154.0) y el llenado del envío real (v0.155.0) eran defectos reales y están
arreglados, pero ninguno era la causa. Los dos se "confirmaron" apagando algo y
viendo mejorar el panel — sin notar que cambiar una variable en Railway
REINICIA el contenedor, y que el reinicio era lo que mejoraba. Un flag que
arregla al reiniciar no ha demostrado nada. Lo que cerró el caso fue instrumentar.

---

### v0.155.0 — El panel se tumbaba solo: 2,241 órdenes que se pedían para siempre

**No era TikTok.** La v0.154.0 arregló un defecto real (el webhook de TikTok
trabajaba antes de contestar), pero la conclusión estaba mal: al reencender el
canal el backend se volvió a degradar, así que se apagó otra vez. **Con TikTok
apagado siguió igual** — 10 s, 22 s, 25 s y timeout en un simple `/api/health` —
y en la ventana degradada **no entró ni un solo evento de TikTok**. Lo que
revivía al backend no era apagar la variable: era el **reinicio** que provoca
cambiarla. Un flag que "arregla" al reiniciar no ha demostrado nada.

**La causa real, medida.** El envío real de ML (`services/envio_real.py`,
v0.68.0) cachea en MySQL lo que ML cobró por cada embarque, y Análisis manda
llenar el caché en cada carga. Los números de producción:

| | |
|---|---|
| Órdenes de ML en la ventana de 60 días que pide el panel | 15,487 |
| Ya en el caché | 13,246 |
| **Pendientes, carga tras carga** | **2,241** |

Esas 2,241 contestan **200 pedidas de una en una, con un segundo entre ellas**.
Solo fallan **en ráfaga**: ML responde 403 y el código hacía

```python
if r.status_code != 200:
    return  # sin fila: se reintenta en la siguiente carga
```

Sin fila, la orden sigue "pendiente" y —como la lista va ordenada y se toman las
primeras N— **vuelve a la cabeza**. Cada carga del panel volvía a pedir las
mismas órdenes que ya sabíamos que iban a fallar. El reintento era lo que las
hacía fallar: un tapón que no se podía destapar solo.

**Y se multiplicaba por cuatro.** La tabla, el dashboard, el reporte y su vista
previa piden su propio llenado, y uno lo lanza con `create_task` sin esperarlo.
El semáforo de 8 es *por llenado*, no en total: con la pestaña abierta eran
cuatro ráfagas simultáneas contra ML. Por eso ML tiraba 403 (y por eso en los
logs se ve la misma orden pedida dos veces en el mismo milisegundo).

**El tercer filo: bloqueo del event loop.** `envio_real.leer()` es MySQL
síncrono y le llegan ~15,000 pares: **1.46 s medidos**, dentro de la corrutina,
dos veces por carga. Mientras corre, el backend entero no atiende a nadie — el
mismo defecto que ya se había pagado en los webhooks el 15-jul y que está
documentado en `_procesar_ml`. Con la CPU al 1.5% y todo colgado, el síntoma
era idéntico.

**Los tres arreglos:**

1. **El intento se registra.** Un fallo de ML escribe la fila con costo NULL, así
   que entra en la regla de reintento a las 24 h y **la lista avanza**. El
   `COALESCE` del guardado impide que ese NULL pise un costo real.
2. **Un solo llenado a la vez** (`asyncio.Lock`). El que llega tarde se va sin
   hacer nada; el que corre llena el mismo caché para todos.
3. **Las lecturas síncronas salen del event loop** (`asyncio.to_thread`): el
   `leer()` de los dos endpoints, el de dentro del llenado y la lectura del token.

**Verificado en producción antes de subir**: dos llenados en paralelo → el
segundo devuelve 0 (candado), 6 órdenes nuevas al caché, 0 nulos.

Queda medido y **no** arreglado aquí: los 25 `sdb.fetch_*` síncronos de
`routers/fulfillment.py` siguen en la corrutina (~1 s el del dashboard). Es
preexistente y ya no se multiplica, pero es la siguiente deuda de ese archivo.

---

### v0.154.0 — El webhook de TikTok trabajaba antes de contestar, y eso tumbó el panel

**Incidente, 13-ago 22:2x.** Brandon: *"ya está en producción pero no se alcanza
a visualizar la página… se queda cargando"*.

El frontend contestaba en 0.2 s y el backend **no contestaba en absoluto**: DNS,
TCP y TLS completaban en 0.2 s y después silencio hasta el timeout. Y sin
embargo el proceso estaba vivo (procesando webhooks en el log) con la **CPU al
1.5%** y 226 MB. Un servicio ocupado con la CPU ociosa es I/O, no cómputo.

Lo dijeron los registros del proxy:

```
POST /api/webhooks/ml → 499 · 985 ms · varias por segundo
"client has closed the request before the server could send a response"
```

**Mercado Libre corta a ~1 s.** Si no alcanzamos a acusar recibo, reintenta — y
cada reintento es más trabajo, que hace más lenta la siguiente respuesta. Un
lazo que se alimenta solo, y el panel esperando turno detrás.

**La causa era mía y estaba a la vista en el mismo archivo.** El handler de ML
usa `BackgroundTasks` desde siempre: acusa recibo y trabaja después. El de
TikTok, que escribí hoy, llamaba a `pedidos_tiktok.procesar()` **antes** de
responder — traer la orden de TikTok y escribir el pedido en Woo son varios
segundos con el proceso sin atender a nadie. Con eventos llegando seguido, eso
bastó para empujar las respuestas de ML por encima de su límite de 1 s.

Dos patrones para el mismo problema conviviendo en 200 líneas de distancia, y
copié el que no era.

**Contención inmediata**: `PEDIDOS_TIKTOK_ENABLED=false` (variable, sin deploy).
**Arreglo**: el webhook de TikTok acusa recibo primero y procesa en segundo
plano, con su propio `try` — una tarea de fondo que revienta se lleva la traza
al log de Starlette y nadie la ve.

Versión 0.154.0.

### v0.153.0 — El semáforo marcaba en rojo lo que sí estaba, y Ventas no dejaba ver TikTok

Dos cosas que Brandon vio en el panel, y las dos eran del panel, no del canal.

**1 · Obligatorios "faltantes" en productos que están A LA VENTA.**
`MUN-0023-MUL` está publicado y vendiendo en TikTok, y el semáforo le marcaba
**ocho obligatorios en rojo**: categoría, imágenes, peso, las tres medidas,
stock y precio. Los ocho existen — pero viven en WooCommerce y en el canal, no
en `enrich.channel_content`, que guarda lo EDITORIAL. El semáforo miraba una
sola de las dos fuentes.

Ahora recibe también **lo que el publicador va a encontrar**, sacado de la misma
función que arma el envío (`publicar_ready.construir_prod`): si el publicador lo
va a mandar, el panel no puede decir que falta. Lo cubierto por esa vía se
devuelve aparte (`del_producto`) en vez de esconderse.

**Un semáforo que marca en rojo lo que sí está enseña a ignorarlo**, que es peor
que no tenerlo.

**2 · Un error mío al cargar los requisitos, que ese rojo destapó.**
Mapeé el canónico `dimensiones` a NULL **en bloque**, razonando que "un campo
cubre tres canónicos". Eso es cierto en Amazon (`item_length_width_height` es UN
atributo con las tres medidas dentro) y **falso en TikTok**, que las entrega
separadas:

```
package_weight.value       → peso
package_dimensions.length  → largo
package_dimensions.width   → ancho
package_dimensions.height  → alto
```

Cada una mapea limpio y no había nada que inventar. El mapeo pasa a ser por
NOMBRE DE CAMPO, que es lo que permite distinguir estos cuatro de cualquier
otro "dimensiones" futuro. Con eso, los tres productos publicados que se
midieron pasan de `incompleto` a **`ok`, sin faltantes**.

**3 · Ventas no dejaba entrar a TikTok ni a Temu.** El tab los mostraba como
"Pronto" y el backend contestaba *"canal aún sin ventas integradas"*… mientras
`ventas_ml._CUENTAS_PEDIDOS` ya los incluía y **sus pedidos llevaban meses
guardándose**. El dato estaba; la puerta no. Medido tras abrirla: Temu **2
pedidos / $598.11** en 30 días, TikTok 0 (su única venta es más vieja). Walmart
y Shein siguen en "Pronto" porque de verdad no tienen entrada de pedidos.

**Y de paso, las columnas de Eduardo.** Ya aplicó `is_leaf` y `disponibilidad`
(en `text`, conservando el valor nativo — su corrección, y tiene razón: 35 de
las 451 restringidas ni siquiera son hoja, y un booleano no distinguiría por
cuál de las dos razones está bloqueada). Recargadas las 2,168 categorías:
**1,717 AVAILABLE / 451 INVITE_ONLY**, con 1,521 hojas publicables.

Versión 0.153.0.

### v0.152.0 — Publicar de verdad destapó dos cosas que ninguna prueba veía

Prueba de extremo a extremo pedida por Brandon con un SKU real
(`CONS-0016-EST`, contadora de monedas, desde su URL de Alibaba): crear, mejorar
con IA por canal y publicar en los tres. **Publicó ML y Amazon; TikTok destapó
un bug y un dato faltante.**

**El bug: el modelo del endpoint tiraba los IDs de los atributos.**
`AtributoIn` declaraba `{nombre, valor}` y nada más, así que **pydantic
descartaba `campo` y `valor_id`** en cuanto la petición entraba. El generador
los guardaba bien, el panel los mandaba, y el publicador armaba el payload sin
ellos. TikTok contestó:

```
12052104 · Parameter `Tipo de garantía` is invalid because the request is
missing product attribute ID `100107`. Provide this product attribute and retry.
```

…teniendo el atributo guardado con su `valor_id: ["1000054"]`. El mensaje decía
"falta el atributo" cuando lo que faltaba era **su id**, que es de las
distancias más caras entre el síntoma y la causa.

Se arregla en los dos extremos: el modelo ahora acepta `campo` y `valor_id`, y
`publicar_tiktok` los **recupera desde lo guardado** cruzando por nombre cuando
el formulario no los trae — con un detalle: si alguien editó el texto del valor,
el id viejo NO se reusa, porque ya no corresponde.

**El dato faltante: stock 0.** TikTok exige `[1, 99999]` y el producto tenía
cero. Ahora se comprueba antes de mandar, así que el panel dice *"Sin stock:
TikTok exige al menos 1 pieza"* en vez de dejar que el canal conteste con un
código.

**Lo que la prueba confirmó, además:**

| Etapa | Resultado |
|---|---|
| Crear (6 pasos) | ✅ 5 imágenes · 8 atributos ML · categoría MLM187307 |
| Contenido Amazon al alta | ✅ 6 campos · tipo `HOME` |
| Contenido TikTok al alta | ✅ 3 campos, sin atributos — no tiene categoría todavía, como se documentó |
| Categoría TikTok recomendada | ✅ **"Contadoras de dinero"**, aceptada con un clic |
| Mejorar con IA (TikTok) | ✅ 11 atributos de ESA categoría, con sus IDs |
| Publicar Mercado Libre | ✅ `MLM5981232724` y `MLM5981268600`, pausados en ambas cuentas |
| Publicar Amazon | ✅ `ACCEPTED`, 0 issues, 1 intento |
| Publicar TikTok | ❌ bloqueado por stock 0 — **y el fallo quedó en `ops.channel_submissions`** |

**Y un hueco de Amazon que solo se ve con un producto en español.**
`_detectar_product_type` busca en la Definitions API con las tres primeras
palabras del título; el nuestro está en español y ese buscador indexa en inglés.
Resultado para *"Contadora y Clasificadora de Monedas"*: **HOME**, el respaldo
genérico. Se agrega `amazon_ia.sugerir_tipo`, que pregunta al buscador de Amazon
y, si no ayuda, deja que la IA elija entre los 553 tipos cargados — validando el
id contra la tabla.

Medido: *"Microfono inalambrico UHF"* → `MICROPHONE` y *"Motosierra electrica"*
→ `CHAINSAW` los resuelve el propio buscador de Amazon. Pero para la contadora
de monedas **no hay tipo bueno**: los únicos parecidos entre los 553 son
`COIN_PURSE_POUCH` y `MONEY_BANK` (una hucha), y la IA se negó a forzarlo —
`coin counter machine` y `money counter` devuelven **vacío** en la propia API de
Amazon. Ahí `HOME` no es un error del panel: es lo que hay.

Versión 0.152.0.

### v0.148.0 — La categoría de TikTok se recomienda sola, pero la elige una persona

Petición de Brandon sobre el picker nuevo: que **siempre** proponga una
categoría y que además se pueda escoger otra. Las dos cosas, con una regla que
no se negocia: **la recomendación NO se guarda sola.**

Si se guardara, dejaría de poder distinguirse de una elección humana — y toda la
precedencia del panel se apoya en esa diferencia (regla 2 de la casa). La
máquina propone; una persona aprieta "Usar esta categoría".

**Dos fuentes, en orden:**

1. **El recomendador de TikTok**, que es el del propio canal.
2. **La IA eligiendo entre hojas REALES** de nuestro catálogo, cuando el
   recomendador no contesta o propone una categoría que no conocemos. Las
   candidatas salen de buscar las palabras del título (quitando colores,
   medidas y palabras vacías) y **el id se valida contra esa lista**: un id
   inventado no da error, deja el producto vivo y mal clasificado.

**La prueba en vivo enseñó por qué hacen falta las dos.** Con el recomendador
del canal:

```
Termo de acero inoxidable        → Frascos de vacío     ✅
Motosierra eléctrica 21V         → Motosierras          ✅
Bolsas de dulces de halloween    → Juguetes de peluche  ❌
```

Ese último es el 49% de fallo, ocurriendo en la primera tanda de tres. Con el
respaldo de IA sobre categorías reales, el mismo producto sale a **Manualidades
de fieltro** con confianza 0.7, y la motosierra a Motosierras con 0.98.

Y cuando nada encaja, contesta que no encaja: *"Xilófono de bambú para
meditación tibetana"* devuelve **sin categoría** con el motivo escrito, en vez
de colocarlo en lo más parecido. No clasificar es una respuesta legítima.

En el panel: la recomendación aparece con su origen, su confianza y su porqué;
el botón la acepta; el buscador sigue ahí para elegir otra; y si ya la eligió una
persona no se gasta la llamada — para eso está "Recomendar otra categoría".

La sugerencia usa el título que se ve en pantalla, no el de WooCommerce: si
acaba de mejorarse con IA, la recomendación tiene que salir de ese texto.

Versión 0.148.0.

### v0.147.0 — Cada canal, su categoría; y TikTok aparece en el detalle

Cuatro cosas que Brandon vio en el panel el 13-ago. Una de ellas resultó no ser
un error.

**1 · TikTok no salía en el detalle de un producto.** El cajón de Omnicanal
armaba su lista con General, Mercado Libre y Amazon, y TikTok simplemente no
estaba: un producto publicado ahí no tenía dónde verse. Ahora trae su tarjeta
con precio del canal, stock y estado.

No lleva tercera columna de FULL/FBA, y no es un olvido: **TikTok Shop MX
trabaja con almacenes DEL VENDEDOR** — hay dos, ventas y devoluciones, y las 900
publicaciones están en el de ventas. No existe un equivalente a que el
marketplace te guarde la mercancía, así que el total dice *"nuestra bodega"* en
vez de dejar un `—` que se lee como "no sabemos".

**2 · La pestaña decía 283 y hay 900.** El contador mostraba solo los que están
a la venta, con el argumento de que poner 900 prometía catálogo que nadie puede
comprar. Brandon pidió lo contrario y tiene razón operativa: **los 599
borradores, los 11 rechazados y los 7 atorados son trabajo que existe y hay que
ver.** Esconderlos los volvía invisibles justo para quien tiene que
destrabarlos. Cuántos se venden se sigue viendo con el filtro "Solo publicados"
y con el estado de cada tarjeta.

**3 · "La pestaña Productos no muestra información" — resultó ser cierto lo que
mostraba.** Se midió antes de tocar nada: los cuatro productos de la captura
(`VIA-0006-AMA`, `VEH-0356-NEG-BLN`, `VEH-0337-NEG`, `VEH-0268-GRI`) están en
`pending` y **no tienen precio, ni costo, ni canal** — ni en Woo, ni en
`costing.costos_finales`, ni en `channel.listings`. La tabla decía la verdad.
Con productos que sí tienen datos las columnas se llenan enteras:

```
DEC-0018-VER  publish  $130.01 / $156.68  6,100 u   amazon · mercado_libre
TEC-0697-MET  publish  $559.61 / $660.54    156 u   amazon · mercado_libre
```

El orden por omisión es "más recientes", que es justo lo que sube los borradores
recién creados —los más vacíos— a la primera página. No se cambió: es el orden
correcto para la pestaña donde se trabaja lo nuevo.

**4 · Cada canal mostraba la categoría de Mercado Libre.** Estando en Amazon o
en TikTok, el Estudio pintaba el picker de ML y la categoría de WooCommerce, que
no viajan a esos canales. Ahora **cada canal muestra la suya y solo la suya**:

| Canal | Qué es su categoría | Picker |
|---|---|---|
| Mercado Libre | `MLM…` del árbol | el de siempre |
| Amazon | `productType` de lista plana | `TipoAmazonPicker`, ya existía |
| TikTok | id numérico **de hoja** | **nuevo** |

`CategoriaTikTokPicker` busca entre las 2,168 categorías cargadas y **solo
ofrece hojas** —las intermedias rechazan con `12052024 Category is not final
category`—, guarda en `channel.product_category` con `source='panel'` (donde ya
viven las 5,166 elecciones humanas de ML) y **manda sobre el recomendador de
TikTok**, que falla el 49%. Verificado: elegir "Termómetros de cocina" hace que
el publicador y el semáforo usen esa, no la que el canal tenía.

**Dos cosas que el buscador enseñó al probarlo:**

- **`ilike` no ignora acentos.** Buscar `audifon` no encontraba «Audífonos» y el
  picker salía vacío como si el catálogo no tuviera la categoría. Se normaliza
  con `translate` en los dos lados (no con la extensión `unaccent`: no está
  instalada y pedirla sería un cambio de esquema para un buscador).
- **TikTok tiene su propio vocabulario**: no existe «Audífonos», existe
  «Auriculares». Cuando no hay resultados el picker lo dice, en vez de dejar
  concluir que la categoría no está.

Y cuando el producto no tiene categoría del canal, el hueco **se explica**: dice
que no está publicado, que se puede elegir ahí mismo, y qué pasa si no se elige.

Versión 0.147.0.

### v0.146.1 — El validador de TikTok existía en mi disco y no en el repo

`services/tiktok_contenido.py` —el validador que escribió el hilo de TikTok y
que `tiktok_ia` usa en cada generación— **nunca se había commiteado**. Estaba en
el working tree, así que todas las pruebas locales pasaron; en producción, el
primer clic en "Mejorar con IA" para TikTok habría sido un `ImportError`.

No tumbaba el arranque porque el import es perezoso (dentro de la función), lo
que lo hacía peor: el backend levantaba sano y solo fallaba esa acción.

Se detectó revisando `git status` al cerrar, no probando — las pruebas no podían
verlo. Es exactamente el modo de fallo que este proyecto lleva semanas
documentando: **la ausencia de un error no es evidencia de éxito**, y un `?? `
en `git status` es tan importante como un test en rojo cuando hay varias
sesiones escribiendo en el mismo árbol.

Versión 0.146.1.

### v0.146.0 — El fan-out ya sabe escribirle a TikTok (y no lo hace todavía)

Última de las seis etapas. `_escribir_tiktok` entra en `_ESCRITORES`, y con eso
TikTok deja de ser un destino que se descarta por *"sin escritor implementado"*.

**Dos cosas que TikTok exige y no se pueden adivinar:**

1. **El `sku_id` de la variante, no el `seller_sku`.** El endpoint es
   `/product/202309/products/{product_id}/inventory/update` y dentro pide
   `skus[].id`, un id propio de TikTok. `channel.listings` no tiene columna para
   guardarlo, así que se lee del producto antes de escribir: una llamada más,
   pero siempre correcta — si TikTok recreó la variante, el id nuevo se toma
   solo.
2. **El almacén de VENTAS.** La tienda tiene dos y el otro es el de
   devoluciones: escribirle stock ahí no da error y no vende nada.

**El bug que se evitó mirando antes de escribir.** `_destinos` decidía si una
publicación está viva con `situacion`, y en TikTok esa columna guarda el
veredicto de la **auditoría** (`APPROVED`/`FAILED`/`NONE`/`PRE_APPROVED`), no si
está a la venta — eso lo dice `status` (`ACTIVATE`). Con la lógica de ML, el
canal entero se habría descartado con *"situación desconocida"*. Hoy `APPROVED`
coincide con `ACTIVATE` en las 900 publicaciones, así que meter `"approved"` en
la lista de vivas habría *funcionado*… atado a una casualidad del dato. Se
agregó `estado_canal` a `channel_read` y se mira el campo que manda.

**Nace con interruptor propio, `FANOUT_TIKTOK`, aparte de `FANOUT_CANALES`.**
El valor de esa lista no se puede leer desde fuera de Railway, así que si TikTok
dependiera solo de ella, un deploy podría **encender escrituras a un marketplace
vivo sin que nadie lo hubiera decidido**. Con el flag propio el deploy es inerte
y encenderlo es un acto explícito; para escribir hacen falta las dos cosas.

Probado con una diferencia de stock simulada (sin tocar Woo ni TikTok):

```
FANOUT_TIKTOK=false → omitir   "el escritor está listo, falta encenderlo"
FANOUT_TIKTOK=true  → escribir
```

Y un dato tranquilizador del plan real: en los SKUs medidos, TikTok ya tiene
**exactamente** el stock de WooCommerce (7,200 · 4,890 · 156 · 144), así que
encenderlo no va a provocar una avalancha de correcciones.

Versión 0.146.0.

### v0.145.0 — La venta de TikTok se vuelve pedido

El receptor de `/api/webhooks/tiktok` existía desde la v0.104.0 y estaba en modo
**observar**: registraba el evento y no escribía nada. Entra la otra mitad,
`services/pedidos_tiktok.py`, con el mismo aparato de ML y Amazon:

```
evento → id de la orden → la orden COMPLETA por API
       → pedidos_ml.sincronizar (candado, precio congelado, idempotencia,
         pedido de Woo, channel.orders)
```

**No se reimplantó nada de eso.** `pedidos_ml.sincronizar` sigue siendo el único
sitio donde nace un pedido, venga del canal que venga; lo demás son traductores.

**Del evento solo se toma el ID.** Líneas, precios, estado y comprador se piden
a la API, por dos razones: un evento es el aviso de que algo cambió, no un
documento contable; y **la URL es pública** — armar el pedido con lo que llega
dejaría que cualquiera inventara una venta. Con este diseño, un evento falso a
lo más provoca una consulta que no encuentra nada.

**Dos detalles que se miden, no se suponen:**

- **TikTok manda una línea por unidad**, no una línea con cantidad: dos piezas
  del mismo SKU llegan como dos entradas. Se agrupan por (sku, precio), así que
  el pedido dice «2 × $660.54» y no dos renglones iguales. Verificado con un
  evento de prueba.
- **El id viene con nombre distinto según el evento**, así que se prueban cuatro
  alias (`order_id`, `orderId`, `order_no`, `id`) en vez de fijar uno. El
  esquema real se confirma con el primer evento verdadero — el mismo criterio
  que el receptor de Temu.

⚠️ **Estos pedidos DESCUENTAN stock.** La mercancía sale de nuestra bodega — el
`warehouse_id` de TikTok es dónde la recogen, no quién la guarda —, así que NO
llevan la protección de ML FULL ni de Amazon FBA.

`UNPAID` no genera pedido a propósito: todavía no es una venta, y crearlo ahí
descontaría inventario por algo que puede no pagarse nunca.

Nace APAGADO (`PEDIDOS_TIKTOK_ENABLED`): crear pedidos toca inventario y
contabilidad. Con el flag apagado el receptor sigue exactamente como estaba.

Versión 0.145.0.

### v0.144.0 — Publicar en TikTok desde el panel

El publicador de TikTok vivía como script suelto en el escritorio de otra sesión
—con él se subieron las 900 publicaciones— y el panel no podía mandar nada.
`services/publicar_tiktok.py` es su versión de panel: mismo payload y mismas
trampas, pero sobre UN producto, disparado por una persona y con vista previa.

**Lo que NO se copió, a propósito:** el respaldo de categoría por IA en dos
pasos. Elige "la hoja más próxima" con confianza baja, que es razonable en un
lote de 900 donde no publicar sale caro; en el panel hay alguien enfrente, así
que sin categoría fiable **se para y se dice**. Un producto vivo y mal
clasificado no da error — es el error de `TEC-1812-NEG`.

**De dónde sale cada dato.** Imágenes, precio, stock y medidas salen de
WooCommerce por el MISMO camino que ML y Amazon (`publicar_ready.construir_prod`,
que ya aplica "lo editado en el Studio pisa lo de Woo"). El título, la
descripción y los atributos salen de `enrich.channel_content` — o sea, de lo que
escribió la IA de la v0.143.0. Pedirle las imágenes al frontend habría sido una
segunda fuente que se desincroniza, y el Studio ni siquiera las conoce.

**Crear y actualizar son endpoints distintos**, y el backend elige según haya o
no `listing_id`: crear sobre un producto que ya existe genera un **duplicado**
que en TikTok se borra a mano.

Probado contra la API real **sin crear ningún producto**:

- **Vista previa completa** de `TEC-0697-MET`: categoría 600032 (*"la que ya
  tiene en TikTok"*), 6 atributos con sus IDs de valor, precio $660.54, stock
  156, dimensiones reales, 4 imágenes contadas y cuatro avisos — entre ellos que
  el modo `LISTING` lo deja a la venta y que el borrador *casi no valida*.
- **Subida de imagen real**: `principal_TEC-0697-MET.jpg` → `uri`
  `tos-alisg-i-aphluv4xwc-sg/7f9d17f36…`. Era la pieza más frágil (multipart con
  la firma armada a mano, porque el cuerpo no entra en la cadena firmada) y es
  la que decide si el producto sale con fotos.

**Lo que la vista previa NO puede prometer todavía:** si la categoría admite
publicación. `channel.categories` no guarda `is_leaf` ni `permission_status`, y
las `INVITE_ONLY` **no rechazan** — aceptan el producto y lo dejan en `PENDING`
para siempre, sin error. Mientras falten esas dos columnas, el aviso lo dice con
todas sus letras en vez de afirmar lo que no sabemos.

Cada envío queda en `ops.channel_submissions` (`detail_ref='panel:publicar'`) y
el resultado se refleja en `channel.listings` al instante: sin eso, el panel
diría "no publicado" de algo que uno acaba de publicar.

Versión 0.144.0.

### v0.143.0 — TikTok escribe con IA: el mismo contrato de Amazon, con las trampas de TikTok

`services/tiktok_ia.py`, hermano de `amazon_ia`: resuelve la categoría real del
SKU, pide el contenido con **el prompt que entregó el hilo de TikTok** (literal),
lo pasa por `tiktok_contenido.validar` con una ronda de reparación, pide los
**atributos a la API de esa categoría** y los valida contra su lista cerrada, y
guarda todo en `enrich.channel_content` con `origen: ia`.

**Las tres diferencias con Amazon que no son de estilo:**

1. **Amazon trunca; TikTok acepta el borrador y rebota al vender.** `AS_DRAFT`
   casi no valida y `LISTING` valida todo, así que se valida contra lo que exige
   LISTING. Un semáforo verde de borrador no significa publicable.
2. **El título de MX admite 300 caracteres.** El prompt que había en el panel
   pedía **45** — 255 caracteres tirados en el campo que más pesa para que a uno
   lo encuentren. En la prueba real el título salió de 127.
3. **TikTok exige ID de atributo Y de valor**, nunca texto. Por eso los
   atributos se piden en vivo a `/product/202309/categories/{id}/attributes` (los
   IDs de valor **no** están en `field_requirements`: ahí quedaron los nombres,
   que sirven para revisar, no para publicar).

**Se guarda el nombre nativo Y el legible.** Cada atributo queda como
`{nombre: "Material", campo: "product_attributes.100701", valor: "Acero
inoxidable", valor_id: ["1000006"]}`. El nativo es lo que el semáforo compara
contra `channel.field_requirements`; el legible es lo único que una persona
puede revisar. Guardar solo uno obliga a elegir entre que el semáforo funcione o
que el panel se entienda — así que el semáforo ahora acepta **`campo` o
`nombre`**, y de paso deja de dar falso rojo en Mercado Libre, donde el
requisito dice `BRAND` y el contenido guarda `Marca`.

**El bug que avisó el hilo de TikTok, arreglado donde vive.**
`tiktok_atributos.validar` no fundía atributos repetidos por `id`, y TikTok
rechaza el producto entero con `12052254` cuando el mismo id aparece dos veces
(pasa con los `is_multiple_selection`: *Material: Plástico* + *Material:
Metal*). Afectó a **40 de 1,221** payloads y lo parcheaba un script suelto, así
que cualquier consumidor nuevo heredaba el bug. Ahora se funden en una entrada
con varios `values`, dentro del servicio.

**Probado en vivo con dos productos publicados**, guardando en producción:

```
TEC-0697-MET · categoría 600032 · título 127/300 · 5 puntos · 6 atributos con su ID de valor
VAR-0048-EST · categoría 1011720 · título  86/300 · 5 puntos
```

Dos cosas que la prueba enseñó y conviene tener presentes:

- **La IA descartó sola dos atributos** que no pudo confirmar («el termo conserva
  calor, no calienta») y quedaron en avisos, sin guardarse. Es la regla de la
  casa funcionando: un dato inventado no da error, se publica, y después nadie
  sabe cuál era mentira.
- **El validador comprueba que el valor EXISTA en la lista, no que sea el
  correcto.** Para un termo de 1.9 L eligió *«Más de 2000 ml»* pudiendo elegir
  *«1000-2000 ml»*: las dos son válidas para TikTok. Ese acierto no se puede
  automatizar con lista cerrada — lo revisa una persona en el panel.

**Y un hallazgo del catálogo, no del código:** `TEC-0697-MET` se llama *"Termo
… 20 Oz"* en WooCommerce y su propia descripción dice **64 oz** dos veces. La IA
hizo lo correcto —creerle a la descripción— y al hacerlo destapó que el título
publicado está mal. Hay que revisar cuántos más están así.

El enganche del alta queda tras `TIKTOK_IA_EN_CREAR`, **apagado**. Ojo con lo que
significa encenderlo: un producto recién creado **todavía no está publicado en
TikTok**, así que no tiene categoría en el canal — se le genera título y
descripción (que son del producto), y los atributos entran cuando el SKU ya
tenga categoría. El resultado lo dice en vez de callarlo.

Versión 0.143.0.

### v0.142.0 — TikTok deja de ser una maqueta: el canal se abre con sus 900 publicaciones

**Lo que decía el panel y lo que era verdad.** La pestaña TikTok llevaba meses
con la leyenda *"Próximamente — pendiente de credenciales"* y datos de ejemplo
(`services/ejemplos.py`), mientras la tienda KUBERA publicaba desde julio: **900
publicaciones, 283 a la venta y 1,883 envíos registrados**. No faltaban
credenciales; faltaba que el panel supiera mirarlas.

Se abre con la entrega del chat de TikTok (cuatro CSV medidos en vivo el 13-ago)
y un cargador propio, `scripts/cargar_tiktok.py`:

| Destino | Filas |
|---|---|
| `core.accounts` | la cuenta **KUBERA** (`shop_id 7494659908378395724`) |
| `core.channels.is_active` | `false` → **`true`** (el flag rancio) |
| `channel.categories` | **2,168** (1,937 hojas, 1,521 publicables) |
| `channel.field_requirements` | **1,779** en 760 categorías |
| `channel.listings` | **900** · 283 ACTIVATE · 599 DRAFT · 11 FAILED · 7 PENDING |

**El diccionario canónico tumbó la carga dos veces, y por eso ahora se contrasta
entero.** `field_requirements.campo_canonico` tiene FK contra
`core.canonical_fields`, así que un nombre que no exista aborta la transacción
completa. La entrega traía tres que no están, y se descubrían de uno en uno por
error de FK. Se enumeraron **los once valores distintos de una vez** y se
resolvieron según lo que son:

- `precio` → `precio_regular` y `marca` → `brand`: el mismo concepto con otro
  nombre. El diccionario se sembró desde el código, y manda el código.
- `dimensiones` → **NULL**: `package_dimension` cubre TRES canónicos
  (largo/ancho/alto) y el modelo guarda uno por fila. Es el mismo caso que el
  cargador de Amazon dejó sin mapear a propósito con
  `item_length_width_height`. Y "sin canónico" ya significa "nadie puede
  llenarlo" en el semáforo — que es la verdad, no un hueco.

**El panel lee de kubera, no de MySQL.** `services/tiktok_panel.py` es la primera
lectura de listado del panel que va directo a `channel.listings`: ML y Amazon
siguen leyendo MySQL, pero los espejos inversos están apagados desde el 13-ago y
sembrar un canal nuevo en una tabla congelada sería crear una foto que nadie va
a actualizar. El nombre del producto sale de `core.products`, que es el registro
civil; `channel.listings` no guarda títulos a propósito.

`status` y `situacion` viajan **separados**: el primero es del producto
(ACTIVATE/DRAFT) y el segundo de la auditoría de TikTok (APPROVED/FAILED).
Aplastarlos escondería justo el motivo por el que algo no se vende — hay 11
`FAILED` que lo demuestran.

**El fan-out ve TikTok y no le escribe, con motivo escrito.** Al sumar `tiktok`
a `channel_read.CANALES` sus filas entran en `_destinos`, pero `_ESCRITORES` no
tiene escritor de TikTok y `FANOUT_CANALES` no lo incluye: cada destino se omite
con *"sin escritor implementado"* en vez de recibir stock. Verificado **antes**
de tocar la lista.

**Lo que el canal enseñó apenas se pudo mirar** — 20 publicaciones **vivas** con
el precio desviado contra Mercado Libre: 5 por arriba de 3× (`TEC-1775-NEG` está
a **12×**: $22,171 contra $1,849) y 15 por debajo de 0.6×. El promedio del
catálogo es 1.30×, así que no es una regla de margen: son casos sueltos. Ninguno
se habría visto sin la pestaña.

**Lo que NO se pudo guardar:** `is_leaf`, `permission_status` y `publicable` de
las categorías — `channel.categories` no tiene esas columnas y el esquema es de
Eduardo. Importa más de lo que parece: las `INVITE_ONLY` **no rebotan al
activar**, dejan el producto en `PENDING` para siempre, sin error (los 7
`PENDING` de la tienda son exactamente eso). Pide dos columnas.

Versión 0.142.0.

### v0.141.0 — El producto recién creado ya sabe en qué categoría de Amazon va

**El hueco que dejó la v0.137.0, medido.** El contenido de Amazon se genera con
los obligatorios de su `productType`… y un producto **recién creado no tiene
ninguno**: `_pt_resuelto` mira la meta del panel (que nadie llenó todavía) y el
histórico de `amazon_progress` (donde un SKU nuevo no está). Devolvía `None`, y
con `None` no hay requisitos que leer.

Verificado con un SKU inexistente, que es exactamente la situación del alta:

```
antes   product_type: None    requisitos: sin_requisitos (0 obligatorios)
        atributos: Material, Color, Cantidad…   ← inventados, no los que Amazon pide
después product_type: LAMP    requisitos: ok (5 de 5 cubiertos)
        origen: deteccion
```

Se agrega el tercer escalón de la precedencia —**panel > histórico >
detección**— usando `publicar._detectar_product_type`, que es **la misma llamada
que hace el publicador al publicar**. Eso es lo que importa: si el generador
detectara por su cuenta, el contenido se escribiría para una categoría y se
publicaría en otra.

Su respaldo cuando ninguna keyword pega es `HOME`, que tiene sus **110
requisitos** cargados: el respaldo tampoco deja al generador a ciegas.

**Lo que NO hace, a propósito:** escribir la meta `amz_product_type`. Esa es la
elección humana del panel y manda sobre todo lo demás (regla 2 de la casa);
rellenarla desde una detección automática convertiría una corazonada en una
decisión de persona. El origen viaja en la respuesta (`deteccion`) y el panel lo
muestra.

Cuesta una llamada más a la Definitions API por producto creado. Versión 0.141.0.

### v0.137.0 — Amazon: la IA propone, el código valida, y el contenido ya no se pierde

**El circuito que Mercado Libre tenía y Amazon no.** En ML, "Mejorar con IA"
saca los atributos de la categoría REAL (`ml_atributos`) y el resultado se
persiste. En Amazon existía la mitad: se pedía un JSON, se le quitaban los
acentos al título y se devolvía. Nadie comprobaba los límites, los atributos se
los inventaba el modelo sin mirar lo que Amazon exige de esa categoría, no había
términos de búsqueda, y si no publicabas en la misma sesión se perdía todo.

Entra `services/amazon_ia.py`, que encadena cuatro piezas:

1. **Los requisitos REALES** del `productType`, leídos de
   `channel.field_requirements` (64,125 filas / 553 tipos, cargadas el 12-ago).
   ⚠️ El cruce es `listings.product_type = field_requirements.categoria_id`.
   `listings.category_id` existe y guarda otra cosa: unir por ahí devuelve cero
   filas **sin dar error**, y el panel diría "sin requisitos" en todo el
   catálogo teniendo 64,125 cargados.
2. **El prompt de Brandon**, literal: título 75, highlights 125, cinco bullets
   de 150–200 en su orden, descripción 2000 y **términos de búsqueda de 249
   BYTES** (no caracteres: cada acento pesa 2, y un byte de más hace que Amazon
   ignore el campo ENTERO, en silencio).
3. **El validador** (`amazon_contenido`) más **una** ronda de reparación con los
   problemas de vuelta.
4. **El documento persistido** en `enrich.channel_content` con `origen: ia`,
   `categoria` = el productType y `spec_version` = `amazon-mx-2026-07`.

**Dos niveles de rechazo, y el segundo salió de la primera corrida en vivo.**
Al probar los tres SKUs reales, **dos perdieron los términos de búsqueda
completos** —245 bytes de palabras clave— porque repetían UNA palabra del
título. En uno de ellos, la palabra era «de». Amazon no castiga eso: solo
desaprovecha espacio. Así que ahora el criterio es **qué hace Amazon con el
campo**: si lo trunca, lo ignora entero o puede suprimir el listado (límites,
signos, promos, emojis) es FATAL y **no se manda**; si solo se aparta del estilo
pedido (un bullet de 148, repetir palabras) es AVISO, se manda y se reporta. Y
las palabras vacías («de», «para», «cm») dejaron de contar como repetición.

**El detector de marcas registradas es determinista, con lista.** 86 marcas con
su reemplazo en español, sin semántica ni confianza en el modelo — la lección de
`TEC-1812-NEG`. Con una guarda que importa: **detrás de «compatible con» NO
sustituye**, solo avisa. Convertir "funda para iPhone 15" en "funda para
smartphone 15" es corrupción silenciosa del contenido. Fuera de la lista a
propósito: `puma` (animal), `vans` (vehículo) y `honda` (adjetivo: "olla honda").

**Lo que se descubrió con los 553 esquemas ya cargados** (antes había que
adivinar, ahora es una consulta):
- `generic_keyword` —el nombre real de los términos de búsqueda— existe en
  **551 de 553**. Los dos que no: `ABIS_BOOK` y `MAPS`. Ya viaja en el payload.
- De los cuatro candidatos que el código probaba para "Item Highlights",
  **tres no existen en ningún esquema**: `item_highlights`, `product_highlights`
  y `key_product_features` salen 0 de 553. El único destino real es
  `special_feature`, y **falta en 289 tipos** (CHAINSAW entre ellos): ahí el
  texto se genera y no tiene dónde ir. La lista se recortó a lo medido.
- **La marca ya era `Generic` por el mapper del vendor**; quien metía la del
  producto era el camino de RESPALDO (`_amazon_attributes`), el que se usa justo
  cuando WordPress da 403. Misma clase de bug que el `CN`/`MX` de julio: dos
  caminos declarando cosas distintas del mismo producto. Cerrado por decisión de
  Brandon (13-ago): las siguientes publicaciones, todas con `Generic`.

**Probado EN VIVO contra producción**, tres SKUs publicados de tres categorías
distintas a propósito:

| SKU | productType | Resultado |
|---|---|---|
| `DEC-0018-VER` | ARTIFICIAL_PLANT | 6 campos · **7/7 obligatorios**, `fabric_type` incluido |
| `TEC-1013-NEG` | MICROPHONE | 6 campos · 6/6 · términos 242 bytes |
| `HERR-0032-ROJ-21V` | CHAINSAW | 6 campos · 6/6 · 222 bytes con «leña» y «cabaña» (220 caracteres: la cuenta por bytes se ve) |

`fabric_type` es el que el propio `CONTENIDO_POR_CANAL.md` daba por perdido —
*"obligatorio en 45 tipos de ropa y sin nadie que lo llene"*. Con los requisitos
alimentando el prompt, lo llena la IA y llega al payload.

Y el ciclo completo, sin publicar: `POST /api/publicar/preview` con **el
formulario VACÍO** devolvió el título, los bullets, la descripción y
`generic_keyword` traídos de `enrich.channel_content`, con `brand` y
`manufacturer` en `Generic` y `fabric_type` en "Plástico". Antes de esto, un
formulario vacío se publicaba vacío.

**En el panel**: campo *Términos de búsqueda* con **contador en BYTES** (rojo al
pasar de 249) y, bajo el botón, el parte del generador — qué no se aplicó y por
qué, qué marcas se sustituyeron, cuántos obligatorios cubre y si quedó guardado.
Un rechazo invisible sería tan malo como el silencio de Amazon.

**Lo que NO se encendió:** el enganche del alta (pestaña Crear) queda tras
`AMAZON_IA_EN_CREAR`, **apagado**. Encenderlo cambia un flujo vivo: cada alta
gasta 1-2 llamadas de IA y escribe en producción. El código está probado por los
dos lados (apagado devuelve `None`; encendido generó y guardó los 6 campos de
`TEC-1013-NEG`) y va después del acta de `core.products`, porque la tabla tiene
FK contra el maestro.

**Un solo prompt, y el viejo se borró.** `ia_generadores` tenía SEIS prompts de
Amazon más: los cinco por campo (`GENERADORES`, servidos por
`/api/ia/generadores` y `/api/ia/generar` — dos endpoints que **hoy no llama
ninguna pantalla**) y el JSON de `_MEJORAR["amazon"]`, que era el del botón
hasta esta versión. Se eliminaron por decisión de Brandon: dos textos para el
mismo campo es una invitación a editar el que no sale a producción. No era
teórico — el bullet viejo pedía el prefijo «[CARACTERÍSTICA EN MAYÚSCULAS]:» y
el spec vivo pide oraciones completas, así que el mismo producto salía distinto
según qué botón se apretara. De Amazon solo sobrevive el planificador de
imágenes, que no escribe contenido del listado.

Sin migraciones: `enrich.channel_content` ya existía (0016) y no la audita
ningún ETL, así que escribir ahí no toca ninguna racha. Versión 0.137.0.

### v0.135.0 — Se retira deltas-orders: los tres crons de deltas quedan cerrados

`pedidos_ml` volvió a congelarse (última escritura 05:00 UTC del 13-ago), así
que `deltas-orders` habría sacado `con_deltas` **por construcción** en su corrida
de las 07:15 — la misma razón por la que se retiraron channel el 11-ago y costos
el 12. Racha cerrada en **21/14, cumplida**.

Hay un motivo extra que no aplicaba a los otros dos: **su regla 4 quedó
invertida por el corte F6.** Decía *"solo_en_supabase es delta: channel.orders
nace exclusivamente de pedidos_ml, así que una fila que MySQL no conoce es una
anomalía real"*. Hoy es al revés — `channel.orders` se escribe PRIMERO y el
espejo después, así que una fila solo en Supabase no es anomalía: es el espejo
yendo atrás. Fueron exactamente los 143 y los 23 que hubo que rellenar el 12 y
13-ago.

Nace `railway.deltas-orders.json` con el aviso de retiro, siguiendo el patrón de
los otros dos. El servicio apuntaba a `backend/railway-deltas.json` —con guion, y
ese archivo NO existe en el repo—, así que además se corrige la referencia rota.

También se les pone `watchPatterns` a los tres para que solo se reconstruyan si
se toca su propio archivo de configuración. Sin eso, cada push al repo los
redesplegaba y `deltas-channel` ya había fallado dos veces en un día generando
alertas de un servicio retirado — ruido que enseña a ignorar alertas, que es
justo lo que casi cuesta caro el 12-ago.

Los tres dominios de deltas quedan cerrados: channel 22/14, costos 27/14,
orders 21/14. Versión 0.135.0.

---

### v0.134.0 — La alerta de duplicados suena una vez, no cada hora

La v0.133.0 acertó en QUÉ vigilar y falló en CUÁNDO hablar: usaba `avisar()`,
que es por enfriamiento. Un duplicado dura hasta que alguien lo atiende, así que
el mismo caso volvía a sonar cada ventana — el 13-ago sonó a las 23:35 y otra
vez a las 00:38 por los MISMOS tres pedidos, con 4 repeticiones suprimidas
además. Repetir lo ya reportado no informa: entrena a ignorar la alerta, que es
justo lo que casi cuesta caro el 12-ago.

Pasa a `avisar_estado()`, y el estado es la **huella del conjunto** de órdenes
duplicadas, no su número: si aparece una nueva la huella cambia y vuelve a
sonar —que es lo que hay que saber—, pero mientras sea el mismo caso hay
silencio. Va hasheada (sha1, 12 chars) porque `alertas_estado.estado` es
varchar(30) y no cabe la lista; la huella real mide 17.

Además estrena recuperación: cuando ya no queda ninguno avisa *"Sin pedidos
duplicados en Woo — resuelto"*. Antes el `if not filas: return` nunca cerraba el
estado, así que un caso atendido se quedaba mudo para siempre.

Recordatorio semanal y no diario: un duplicado sin atender no cambia de urgencia
cada 24 h, y la ventana de 24 h del `having` ya apaga el aviso solo cuando la
copia envejece.

Verificado simulando siete vueltas del vigilante: 3 duplicados → SUENA; los
mismos, dos vueltas → callado; aparece un cuarto → SUENA; los mismos → callado;
ninguno → SUENA la recuperación; sigue limpio → callado. **3 avisos donde antes
habrían sido 7.** Versión 0.134.0.

---

### v0.133.0 — La alerta que faltaba: pedidos DUPLICADOS

Pregunta de Eduardo: *"¿tenemos alguna alerta si comienza a duplicarse algo?"*
La respuesta era **no**, y al medirlo salió que ya está pasando.

**La señal estaba invertida.** La única alerta de pedidos era la de SILENCIO
—sin ventas nuevas en N horas—, que mide el problema contrario. El 12-ago esa
alerta gritó "sin ventas en 4.1 h" mientras se creaban **964 pedidos fantasma**:
no solo no avisó del desastre, avisó de lo opuesto. Los 4 h 17 min que corrió
el incidente fueron exactamente el tiempo que tardó una persona en notarlo.

**Y no es hipotético.** Medido el 13-ago: **7 órdenes de ML con dos pedidos en
Woo en los últimos 7 días**, tres de ellas de anoche. Los patrones son dos:

| Orden | Separación |
|---|---|
| `2000017882564134` | 2 segundos — la ráfaga clásica de ML |
| `2000017802673864` | 48 segundos |
| `2000017905227724` | **13 minutos** — esto no es una ráfaga |

**Se cuenta en WooCommerce, no en nuestras tablas.** `channel.orders` tiene
llave por orden del marketplace: un duplicado la SOBREESCRIBE y ahí es
invisible. El pedido de más solo existe en la tienda.

**La ventana va sobre la copia MÁS NUEVA**, no sobre las dos. Filtrando ambas se
escapaba el caso peor —un reintento que llega días después del original: el
pedido viejo queda fuera de la ventana, el grupo se queda con una sola fila y el
duplicado pasa invisible—. Y de paso el aviso se apaga solo cuando la copia
envejece, en vez de repetir para siempre uno que ya se atendió.

El aviso incluye el playbook: agrupar por meta `_ml_order_id`, y **cancelar
antes de mandar a la papelera** los que hayan descontado stock, o Woo devuelve
piezas que nunca salieron (la lección de los 16 del incidente).

Consulta: 0.83 s. Ventana 24 h, enfriamiento 60 min. Probado contra producción
con el envío interceptado: 24 h → 3 duplicados · 1 h → silencio.

Pruebas sandbox: 15/15 · 11/11 · 20/20. Versión 0.133.0.

### v0.139.0 — El alta de SKUs de Odoo deja de esperar a que alguien apriete un botón

Era el ÚNICO paso del pipeline que dependía de una persona. Todo lo demás corre
solo —el sync de inventario cada 15 min, `odoo_watch` cada 30, `stock_watch`,
los ETLs de medianoche— pero un SKU nuevo en Odoo no cruzaba a Woo hasta que
alguien entraba a Crear y apretaba "Sincronizar Odoo".

Mientras tanto ese SKU existe en Odoo y no en la tienda, así que el ETL nocturno
lo da de alta como `odoo_only` y el acta sale `con_deltas`. **Las TRES altas del
hueco del 13-ago fueron exactamente eso**: `ACC-0816-AMA`, `-AZL` y `-ROS`,
esperando el clic. No es una fuga del seam — es un paso manual que nadie dio.

Nuevo job `alta_skus_odoo` en el scheduler: cada 6 h, lote de 100, el mismo
`sincronizar_drafts` que el botón ya usaba. Seis horas alcanzan de sobra para
llegar antes del ETL de las 00:15.

**CANDADO DE INVENTARIO — el alta solo trae la IDENTIDAD del SKU.** Hasta ahora
`_borrador_wc` sembraba `manage_stock` y `stock_quantity` con el `free_qty` de
Odoo. Eso no puede ir en un cron:

- `free_qty` es 0 SIEMPRE para los 45 SKUs de tipo `consu` — por definición, no
  porque no haya piezas. Sembrar ese 0 al nacer mete el producto al catálogo
  declarando "no hay", y el fan-out lo propaga a los canales.
- El inventario lo gobiernan `stock_watch` (Odoo→Woo por DELTA) y el sync de
  canales. El alta no tiene por qué opinar.

Manualmente el botón podía permitírselo: había una persona mirando el resultado.
Un cron cada seis horas, no.

**Reversible sin tocar código**: `SYNC_ODOO_INCLUIR_STOCK=true` restaura el
comportamiento anterior. Verificado en los dos sentidos — con el candado puesto
el payload no lleva `manage_stock` ni `stock_quantity` y conserva nombre, sku,
tipo, status, precio, imagen y categoría; levantado, vuelven `manage_stock=True`
y `stock_quantity=42`; y el resto del payload es idéntico byte a byte entre
ambos casos.

Los otros dos interruptores también son variables: `SYNC_ODOO_SKUS_ENABLED` para
apagarlo entero y `SYNC_ODOO_SKUS_MIN` / `_LIMITE` para el ritmo y el lote.
Versión 0.139.0.

---

### v0.136.0 — El umbral que medía el paso del tiempo

Primera verificación tras 14 h con los espejos apagados. **Los tres siguen
congelados** —`costos_finales` 70 h, `pedidos_ml` 13.5 h, `canal_inventario`
14 h— y kubera recibiendo con normalidad. El apagado aguantó.

Pero el latido del barrido cruzó a ALTO, y no porque algo empeorara: **el umbral
estaba mal de origen.**

Medía la antigüedad de la marca más vieja contra un tope fijo de 240 h. Si el
barrido no alcanza esa fila, su antigüedad crece **1:1 con el reloj**, así que el
umbral se cruza solo. Cruzó a las 14 h exactas de ponerlo —226.1 + 14.1 = 240.2—
con la marca más vieja **intacta al segundo**: `2026-08-03 18:14:31.360898`, la
misma de la línea base.

Un umbral absoluto sobre algo que crece con el calendario mide el paso del
tiempo, no la salud. Habría gritado todos los días hasta volverse ruido, que es
exactamente el modo en que muere un detector.

**Ahora mide cuánto tardaría UNA VUELTA COMPLETA** al catálogo al ritmo actual:
`total ÷ tocadas por hora`. No crece solo, y dice lo único que importa — si el
barrido alcanza a observar todo antes de que el dato envejezca. Tope: 72 h.

Lectura de hoy: **156 h de ciclo** (43/h sobre 6,639 publicaciones) y **5,593
con 7+ días sin revisarse**. El diagnóstico es el mismo de ayer, ahora con un
número que se puede seguir: cuando el arreglo del barrido funcione, este número
baja; antes, solo subía porque pasaba el tiempo.

Se conserva la marca más vieja en la salida, pero como DATO y no como umbral:
si no cambia entre corridas, esa fila no la alcanza el barrido. Versión 0.136.0.

### v0.138.0 — El barrido: dos métricas mal leídas y el bug que sí existe

Corrección de lo publicado en v0.130.0 y v0.136.0. Las dos midieron el barrido
sobre `channel.listings.updated_at` leyéndola como "cuándo se revisó". **No es
eso: es "cuándo CAMBIÓ".**

El upsert de `channel_mirror` lleva `where … is distinct from …`, así que el
UPDATE solo dispara si el dato cambió, y `trg_touch_listings` (BEFORE UPDATE)
solo entonces toca la fecha. Una publicación pausada con precio y stock estables
se visita cada 15 minutos y conserva su fecha de hace diez días.

Así que "5,593 publicaciones con 7+ días sin revisarse" en realidad decía
"5,593 cuyo precio y stock no han cambiado en 7+ días" — que es lo normal, no
una falla. El primer intento midió el paso del tiempo; el segundo midió un ciclo
que no existe. **Ninguna columna registra la VISITA**, así que la cobertura del
barrido hoy no es medible desde la base, y el latido se queda en `n/d` en vez de
inventar un número.

**Pero con la columna bien entendida aparece un bug REAL, y es otro.** El turno
(`inventario._lote_desde_ml`) se ordena por esa misma `updated_at`:

```python
orden = sorted(ids, key=lambda i: (i in vistos, vistos.get(i) or epoca))
```

Una publicación estable tiene la fecha más vieja → sale elegida → se visita →
**como no cambió, su fecha no se mueve** → vuelve a salir elegida la ronda
siguiente, para siempre. Y las que sí cambian se van al fondo de la fila. Es la
misma familia de los otros tres hallazgos de esta semana: **la marca de "ya lo
hice" no la pone el hacerlo.**

El arreglo pide una columna `last_seen_at` escrita en CADA visita, pase lo que
pase con el dato — que de paso es la única forma de medir la cobertura. Es un
cambio de esquema en producción, así que va aparte y con su dale.

De camino, un dato de los logs que sí es real: **9 ítems por ronda vuelven "sin
SKU legible"** y nunca se escriben. Como nunca entran a `vistos`, ordenan
PRIMERO en todas las rondas: son 9 lugares del lote de 80 quemados cada vez.

También se corrige `docs/APAGADO_ESPEJOS_MYSQL.md`, que llevaba el número malo.
El apagado de los espejos no está en cuestión: sigue verificado. Versión 0.138.0.

### v0.140.0 — El barrido está bien: se comprobó contra Mercado Libre

Cierre del hilo abierto en v0.130.0 y continuado en v0.136.0 y v0.138.0. Las
tres publicaron un problema del barrido; **no existe**, y la pregunta de Eduardo
fue la que lo destapó: *"¿el 'se revisa' en qué consiste? Si es que entren y no
haya modificaciones, no creo que haya problema."*

Tenía razón. Se midió contra la fuente en vez de razonarlo: se tomaron **24
publicaciones —las 12 más rancias por `updated_at` y las 12 que cambiaron hoy—
y se le preguntó su estado real a la API de ML**. Precio, stock y situación:
**cero diferencias en las 24**.

O sea que el turno puede repetir a las publicaciones estables sin consecuencia,
porque son justamente las que no tienen nada nuevo que contar. Lo que en v0.138.0
se describió como "un bug real" es un comportamiento sin daño observable.

**Y la primera lectura de esa prueba también estaba mal**, lo que vale anotar
porque es la misma trampa de siempre: salían 10 de 12 con diferencia de stock,
todas con kubera en 0. No era stock viejo — el stock vive en `stock_full` SOLO
si la publicación es FULL, y si no, en `stock_own`. Leer siempre `stock_full`
daba 0 y marcaba diferencias inventadas. Tres lecturas equivocadas seguidas de
la misma tabla: dos de `updated_at` y una de las columnas de stock.

Lo único que queda es una carencia de instrumentación: sin un `last_seen_at`
escrito en cada visita no se puede medir la cobertura del barrido. Es para PODER
MEDIR, no para arreglar nada. Baja de "bug" a "sería bueno tenerlo".

Se corrigen la nota del detector y `docs/APAGADO_ESPEJOS_MYSQL.md`. Versión 0.140.0.

---

### v0.149.0 — Las categorías de TikTok que aceptan el producto y lo entierran (Eduardo)

`channel.categories` gana dos columnas. Las pidió el compañero que abrió el canal
y el cargador ya las esperaba: `cargar_tiktok.py` leía los datos de la entrega y
los **tiraba en cada corrida**, diciéndolo en su propia salida — *"is_leaf,
permission_status y publicable NO SE GUARDAN"*.

**Por qué duele el segundo.** Las categorías `INVITE_ONLY` **no rechazan el
producto**: lo aceptan y lo dejan en `PENDING` para siempre, sin error y sin
aviso. Verificado contra producción: de las 900 publicaciones de TikTok, 599
`DRAFT`, 283 `ACTIVATE`, 11 `FAILED` y **7 `PENDING`** — y esos 7 son los que
cayeron en categorías restringidas. Sin este dato la vista previa solo podía
decir "no se puede verificar"; ahora bloquea.

**`disponibilidad` es TEXT, no boolean** (corrección del consejo sobre la
propuesta original). Un boolean aplasta el porqué: TikTok da
`AVAILABLE`/`INVITE_ONLY` y **35 de las 451 restringidas ni siquiera son hoja**,
o sea dos motivos distintos por los que no sirve. Es el mismo criterio que ya
sigue esta base: `channel.listings` guarda `status` Y `situacion` por separado
porque "aplastarlas perdería justo la que explica por qué algo no se vende".
`publicable` NO lleva columna: se deriva con `is_leaf and disponibilidad =
'AVAILABLE'`.

**`is_leaf` en inglés** (la otra corrección). El argumento "un concepto, un
nombre" es correcto pero apunta a no usar el vocabulario NATIVO del canal
(`permission_status`, `is_final_category`), no a elegir idioma.
`channel.categories` es 100% inglés, y el esquema ya arrastra un caso de
vocabulario duplicado para el mismo concepto — `is_fulfillment` en
`channel.listings` contra `es_fulfillment` en `channel.order_items`.
(`disponibilidad` se queda en español por petición explícita, y anotado.)

**La numeración se resolvió midiendo:** `aplicar_migraciones.py:93` aplica con
`sorted(glob("*.sql"))` — alfabético por nombre COMPLETO, no por número. Por eso
los dos `0018` y los dos `0004` conviven sin romper nada. Se toma `0019` y NO se
renumera: `0018_channel_field_requirements.sql` ya está aplicada en producción y
renumerar una migración aplicada desincroniza el ledger. Su encabezado seguía
diciendo `NO APLICADA` — quedó rancio y se corrige aquí, porque invitaba
justamente a ese error.

**Un candado en el cargador.** Al poblar `disponibilidad` se usaba
`.get("permission_status")`, que si la columna del CSV se llama distinto devuelve
`None` y deja las 2,168 filas en NULL **sin que nadie se entere** — el fallo
silencioso que estas columnas existen para evitar. Y **no se puede verificar
desde otra máquina**: los CSV viven en el escritorio de quien hizo la entrega.
Ahora aborta con el nombre de las columnas que sí trae.

`country_of_origin` queda FUERA a propósito: el consejo coincidió en que
colapsarlo a un valor global reintroduce el punto ciego del incidente original
(Amazon declaraba `MX` por un camino y `CN` por otro sin que nadie comparara). Su
casa es `core.canonical_fields`, como dato comparable por canal.

Aplicada en producción con pre-chequeo en la misma transacción; 4,860 filas
intactas y 0 con dato — la migración no siembra. Manifiesto regenerado:
**`PARIDAD OK`**, 45 tablas. Versión 0.149.0.

### v0.150.0 — Análisis: el tamaño y las cuentas se explican, y el filtro acepta varios

Tres cosas en la columna Producto y sus filtros.

**El chip de tamaño abre su tarjeta.** La letra S/M/L/XL sale del lado más largo
y sola no dice nada: un SKU de 29 cm y otro de 31 caen en categorías distintas
sin que se vea por qué. Ahora muestra las tres medidas, **señala cuál decide la
letra** y da los cortes, para que la clasificación se pueda comprobar en vez de
creerse. Sin medidas capturadas lo dice, y aclara que sin ellas tampoco hay
flete de importación calculable.

De paso **detecta el peso de caja**: arriba de 1.5 kg por litro avisa que suele
ser el peso de la CAJA master capturado como si fuera el de una pieza. En
`MUE-0163-TEL` son 39.36 kg en 12.2 L — 3.2 kg/L — y con ese peso el flete
estimado sale muy alto.

**Los puntos de cuenta abren el censo de publicaciones.** Decían *cuántas*
cuentas, no cuáles ni cómo, y "está en tres cuentas" con las tres pausadas se
leía igual que con las tres vendiendo. `TEC-1284-NEG-27"` lo muestra: tres
publicaciones, **ninguna comprable**.

Dos decisiones que salieron de medir, no de suponer:

- **El estado no se normaliza a activa/pausada.** En Amazon `DISCOVERABLE` es
  la mayoría (1,258 de 1,501) y significa que existe y se encuentra, pero **no
  que se pueda comprar**. Tratarla como activa inflaría el conteo de vivas;
  como pausada diría que está detenida. Se nombra tal cual —"visible, no
  comprable"— y se marca si vende.
- **No se reusó el agregado `precios`** que ya existía: filtra
  `price is not null` y se comería **267 publicaciones de ML y 115 de Amazon**.
  Una sin precio sigue siendo una publicación, y esconderla haría que la
  tarjeta contradiga a los puntos, que sí las cuentan.

**El filtro de tamaño acepta varios.** Filtrar por tamaño casi siempre es
preguntar por un rango —lo chico que cabe en un sobre, lo grande que paga flete
caro— y con una sola opción había que mirar la tabla dos veces para comparar S
contra M. Va como botón con casillas y no como `<select multiple>` nativo, que
se ve mal y se opera peor. "Todos" es limpiar, por eso va como botón aparte.

| selección | SKUs |
|---|---|
| Todos | 2,687 |
| S | 923 |
| S · M | 1,695 |
| S · M · L | 2,011 |
| 4 de 5 | 2,039 |

**Dos defectos corregidos en el camino**, ambos destapados al probarlo:

- **Un 400 que dejaba la página en blanco.** El filtro pasó a aceptar lista pero
  la validación, veinte líneas más arriba, seguía comparando la cadena completa
  contra el conjunto: `"S,M"` se rechazaba. Ahora valida cada elemento y el
  mensaje nombra solo el malo (`tam=S,Z` → «tam inválido: Z»).
- **Una carrera de respuestas en `cargar()`.** No cancelaba nada: con dos
  filtros seguidos ganaba la respuesta que llegara última, no la última pedida,
  y la tabla acababa mostrando un filtro que ya no estaba seleccionado. Con el
  `select` de una opción casi no se notaba; con casillas se marcan tres seguidas
  y es fácil de provocar. Cada carga lleva ahora su número y descarta su
  resultado si ya no es la vigente.

Verificado contra el sandbox con clon de producción: las cuatro ramas de las
tarjetas (con medidas, sin medidas, con peso sospechoso, con y sin publicación
vendible), los cinco conteos del filtro, y que Escape, el clic fuera y "Todos"
cierran o limpian. Sin migraciones y sin variables nuevas. Versión 0.150.0.

### v0.151.0 — Análisis deja de lanzar una consulta por clic (y de morirse cuando la API falla)

Dos arreglos que salieron de medir el filtro de tamaño recién estrenado.

**Una ráfaga de clics ya no lanza una consulta por cada uno.** La consulta de la
tabla es cara: medida en vivo con el espía de peticiones tarda **de 3.6 a 11.5
segundos**, y empeora cuando varias se enciman porque se estorban entre ellas.

```
tam=S        pedida     0 ms  →  respondida   3,639 ms
tam=S,M      pedida 1,007 ms  →  respondida   8,764 ms
tam=S,M,L    pedida 1,998 ms  →  respondida  11,527 ms
```

Marcar tres tamaños seguidos lanzaba tres consultas completas cuando solo
interesa el resultado de la última — y escribir `TEC-1284` en el buscador
lanzaba **ocho**, una por tecla, porque tampoco tenía respiro. Ahora cada cambio
cancela el temporizador anterior y la ráfaga se colapsa en UNA sola consulta:
medido, tres clics rápidos producen una petición (`tam=S,M,L`) con el resultado
correcto. 350 ms no se perciben al cambiar un filtro suelto.

**Un error de la API ya no deja la página en blanco.** Las peticiones de
`dashboard` y `tabla` eran las únicas del archivo sin revisar `r.ok` antes de
leer el cuerpo —`detalle` y `canales` sí lo hacían—, así que la respuesta de
error entraba igual al estado y el render reventaba con *"Cannot read properties
of undefined"*. Pasó dos veces el mismo día: con un 400 de validación y con un
502 del backend.

Verificado apagando el backend y recargando: la página **sigue viva** —con su
encabezado y sus filtros— y muestra «Error al cargar», en vez de desaparecer sin
decir qué pasó.

Sin migraciones y sin variables nuevas. Versión 0.151.0.

### v0.153.0 — Análisis: la tabla se ve ocupada mientras carga

Reporte de Eduardo (13-ago): *«coloco el filtro pero no pasa nada»*, con captura
del filtro de tamaño en «L · XL» y la tabla mostrando SKUs S y M.

**No estaba roto.** El backend filtraba bien —`tam=L,XL` devuelve 344 filas,
todas L o XL— y la petición salía con el parámetro correcto. Lo que fallaba es
que la consulta tarda **2.4 s** y durante ese rato la tabla seguía mostrando el
resultado ANTERIOR sin ninguna señal: el botón ya decía «L · XL» y las filas
eran las de antes, así que se leía como que el filtro no servía.

El ícono de Actualizar sí giraba, pero vive arriba del todo, lejos de donde mira
quien acaba de mover un filtro. Ahora la tabla **se atenúa** y aparece
«Actualizando…» encima de ella. Se conserva la intención original de no
parpadear con el auto-refresco de 60 s: la tabla vieja sigue legible debajo, no
se vacía ni se reemplaza por un esqueleto.

De paso quedó medido de dónde salía la lentitud, y confirma que el respiro de la
v0.151.0 era el arreglo correcto:

| | tiempo |
|---|---|
| Una consulta sola | **2.4 – 2.8 s** |
| Tres a la vez (lo de antes del respiro) | **7.8 s** |

No se estorbaban por casualidad: son la misma consulta pesada compitiendo por la
base. Con el respiro, tres clics producen una sola petición y el resultado llega
tres veces más rápido.

Queda pendiente lo de fondo: **2.4 s siguen siendo muchos** para un filtro. La
consulta arma 8 CTEs sobre 20,642 publicaciones y 15,487 pedidos en cada carga.
Optimizarla es trabajo aparte y merece su propia revisión.

Verificado: el aviso se pinta durante la carga y desaparece al terminar; el
contenedor nuevo está en el bundle servido; `tsc` limpio. **No pude filmarlo
durante un cambio de filtro** —el panel del navegador estaba oculto y tumbaba
los scripts de muestreo—, así que esa parte queda observada en la carga inicial,
que usa exactamente el mismo indicador.

Sin migraciones y sin variables nuevas. Versión 0.153.0.

### v0.168.0 — Temu escribe con IA: el mismo contrato, con su cascada

Piezas 2 y 4. `services/temu_ia.py` es el ADAPTADOR que conecta los prompts y
validadores que ya existían (`temu_contenido.py`) con el panel: resuelve la
categoría, pide la plantilla real, llama a la IA, valida y guarda en
`enrich.channel_content`. Mismo papel que `tiktok_ia.py` y `amazon_ia.py`.

**Por qué son tres llamadas y a veces cuatro** — no es diseño, es el orden que
impone Temu:

1. La categoría va primero porque **la categoría determina qué atributos
   existen**: `template.get` solo responde en hojas.
2. El contenido, ya con la hoja sabida.
3. Los atributos **en dos vueltas**, porque la cascada es circular: qué
   condicionales se activan depende de lo que se contestó en los duros. No se
   puede preguntar "¿qué voltaje?" antes de saber si el producto se enchufa.
   La segunda vuelta solo ocurre si algo se destrabó (13 de 89 productos).

**Probado contra un producto real** (`OFI-0057-NEG`, un pistón de gas):

| | |
|---|---|
| Categoría | 12438, resuelta desde la publicación |
| Título | 85 caracteres |
| Atributos validados | 9, incluido "Uso sin electricidad" |
| Llamadas a la IA | 2 (no necesitó vuelta condicional) |
| Obligatorios sin llenar | **ninguno** → no caería en Borrador |

Entendió que es una **refacción** y no una silla, que es justo donde el
recomendador de Temu se equivoca.

**El chequeo que evita el Borrador.** `faltantes()` dice qué obligatorios
quedaron vacíos; si no viene vacío, publicar deja que Temu autocomplete y el
producto **cae en Borrador en vez de publicarse**. Se devuelve como aviso al
panel en vez de esconderse.

También `scripts/cargar_temu_categorias.py`: recorre el árbol con
`bg.local.goods.cats.get` (`language=es` da los nombres traducidos) y llena
`channel.categories` con `path`, `is_leaf` y `disponibilidad`. `is_leaf` importa
porque el selector solo debe ofrecer hojas — ofrecer una intermedia es ofrecer
un error.

---

### v0.167.0 — Temu: 2,086 reglas de campo, y los duros son tres conceptos

Pieza 3 de las seis. `scripts/cargar_temu_requisitos.py` lee
`bg.local.goods.template.get` de las **144 categorías en uso** y llena
`channel.field_requirements`: 2,098 atributos + 5 filas globales.

**Las cinco globales son las únicas que Temu valida de verdad.** El sondeo del
13-ago quitó un campo a la vez en 12 altas de prueba y **las 12 publicaron**. De
todo el payload solo se hacen respetar `goodsName`, `externalGoodsId`,
`externalSkuId`, `images` y `basePrice`. Lo demás no truena al publicar — pero
un producto sin sus atributos cae en "Incompleto" y no se vende, que es
justamente donde están 90 de nuestras 160 publicaciones.

**Duro vs condicional, ahora medido.** De los obligatorios: **285 duros y 260
condicionales**. Los duros son ~2 por hoja y se concentran en tres conceptos,
tal como predecía el manual:

| Atributo duro | En cuántas hojas |
|---|---|
| Material | 72 |
| Características de la batería | 55 |
| Modo de alimentación | 39 |
| Fuente de alimentación | 36 |

Las tres listas de energía traen salida de emergencia ("Sin electricidad", "Sin
batería"), así que **un producto no eléctrico se cierra con Material + dos
"no"** — y el catálogo Kubera es casi todo no eléctrico. Cada fila guarda su
`pid` y la lista CERRADA de `vid`+valor: es lo que impide que la IA invente un
valor (10 inventados en 89 productos durante la prueba del 13-ago).

---

### v0.166.0 — Temu deja de ser una maqueta: 160 publicaciones vivas en el panel

La pestaña Temu mostraba **datos de ejemplo** (`ejemplos.py`) encima de un canal
con 160 publicaciones reales. Ahora lee de `channel.listings`, igual que TikTok.

**El cliente de la API** (`services/temu.py`, solo lectura por ahora). Se firma
MD5 en mayúsculas de `app_secret + clave+valor ordenados y CONCATENADOS +
app_secret`, sin separadores; todo va en el cuerpo de un POST al mismo `router`
y el endpoint se elige con `type`. Es la misma trampa del webhook: el ejemplo de
la propia doc de Temu firma con `clave=valor&` y no reproduce ni sus propios
ejemplos.

**Aparecieron dos cubetas que la doc no conocía.** `docs/TEMU_MANUAL.md`
documentó `goodsSearchType` 1 y 4 —era lo que había el 12-ago—, pero dos
productos que la doc daba por publicados (`ILUM-0089-PLA`, `HERR-0374-MUL`) no
salían en el censo. Sondeando de 0 a 7 aparecieron la **5** (2 productos) y la
**6** (1). Ninguna cubeta da error: las vacías contestan lista vacía, así que
**una cubeta que no se pregunta no se extraña**. Censo real: 174 publicaciones,
160 con SKU nuestro, 14 de prueba que se omiten.

**El precio, resuelto midiendo.** En `list.query`, `price` y
`retailPrice.amount` vienen en PESOS con decimales (338.46) pero `marketPrice`
viene en CENTAVOS (11400 = $114.00) — el mismo concepto con dos formatos en la
MISMA respuesta. Se usa `price`.

**El estado NO se traduce, y eso es la feature.** Temu contesta números
(`2/8`, `3/2`, `4/7`, `5/None`…) y no documenta qué significan. Se decodificaron
DOS cruzando productos cuyo estado real se conocía por el Seller Center:

| Código | Significa | Cómo se supo |
|---|---|---|
| `2/8` | Incompleto | los 4 publicados el 13-ago, los 4 coinciden |
| `5/None` | Borrador | los 2 con precio 0.00 |

Los otros cinco códigos (87 publicaciones) se muestran crudos —"Temu 3/2"— y se
dicen crudos. **Consecuencia operativa: Temu NO entra al fan-out de stock.** No
se le escribe inventario a un canal del que no se sabe qué publicaciones venden;
es exactamente el error que en TikTok habría atado el fan-out a una casualidad.

Piezas: `services/temu.py`, `services/temu_panel.py`,
`scripts/cargar_temu.py` (dry-run por default), y las ramas de Temu en
`routers/productos.py` (listado + tarjeta del cajón) y `routers/canales.py`.

⚠️ Las credenciales `TEMU_*` viven **solo en el `.env` local**: no están en
Railway, así que en producción el cliente se reporta como no configurado. El
cargador se corre a mano hasta que se decida meterlas.

---

### v0.165.0 — Paso 1 del desmantelamiento: las tres cachés de Márgenes salen de MySQL

Primer grupo de las 31 tablas que quedan (plan y dictamen del consejo en
[docs/PLAN_31_TABLAS.md](docs/PLAN_31_TABLAS.md)). `ml_envio_real` (13,735),
`ml_ficha` (971) y `ml_visitas` (1,485) pasan a `enrich.order_shipping_cost`,
`enrich.listing_weight` y `enrich.listing_visits`.

**Se eligieron como primeras a propósito**, y el producto de este paso no son
las tres tablas: es el **instructivo** para las otras 28. Cada una tiene
exactamente un lector y un escritor, los dos en su propio servicio, sin crons
detrás — el caso más limpio que queda para probar el método antes de gastarlo
en el grupo del publicador, que tiene ~19 lectores.

**El instructivo, en seis pasos:** gemela → copia → comparación → escritor →
lector → verificación. Cada uno con su artefacto:
`supabase/migrations/0020_enrich_margenes.sql`, `backend/scripts/backfill_margenes.py`
(idempotente, dry-run por default, y **verifica solo** al final: conteos por
tabla y muestra de 200 filas valor por valor) y `backend/services/margenes_read.py`.

**Flag propio, no el de channel.** El primer intento reusó
`SUPABASE_WRITE_CHANNEL` y eso rompía la propiedad que salvó los cinco cortes:
la reversa por dominio. Ahora es `SUPABASE_WRITE_MARGENES`, **nace apagado**, y
este deploy no cambia nada hasta que se encienda.

**Detalles que costaron una corrida cada uno:**

- `medido` es `TINYINT(1)` en MySQL y `boolean` en Postgres — 0/1 no se
  convierte solo. Se traduce en Python y no con un cast en SQL, para que el
  NULL siga siendo NULL ("no sé") y no se vuelva `false` ("no medido").
- `consultado_at` se preserva del origen. Es lo que decide si hay que volver a
  llamar a ML, que **acepta un ítem por llamada** en visitas y en costos de
  envío. Re-sellarlo con `now()` haría que el primer barrido creyera que todo
  está fresco y sirviera datos viejos durante un TTL entero.

**Deuda que se documenta y NO se resuelve:** `enrich.market_listing_metrics.visits_30d`
(módulo Competencia) y `enrich.listing_visits` (Márgenes) guardan lo mismo,
pedido al MISMO endpoint. Medido: 323 publicaciones en las dos, 257 con números
distintos — pero la diferencia es esperable, son ventanas MÓVILES consultadas en
fechas distintas. Lo real es el desperdicio de llamadas. Converger cambia la
semántica del tab (ventana variable 7/30/60 contra la fija de 30) y eso es
decisión de producto. Queda anotado en la migración para que nadie cree un
tercer caché sin saber que ya hay dos.

Probado: migración aplicada a sandbox y a producción; backfill verificado en los
dos (13,735 + 971 + 1,485, cero diferencias); gemelas contra el par MySQL
**11/11**, incluyendo que la ventana de visitas no se mezcle entre 7, 30 y 60
días. Versión 0.165.0.

### v0.167.0 — Paso 2: la foto del vigilante de inventario sale de MySQL

Segunda tabla de las 31 ([docs/PLAN_31_TABLAS.md](docs/PLAN_31_TABLAS.md)) y la
más caliente que quedaba: `stock_watch_foto` (14,640 filas) →
`ops.stock_watch_photo`.

**No es un caché, es la memoria de un proceso que mueve inventario real.** El
consejo la sacó del grupo de cachés por eso. Estado verificado hoy en Railway:
`STOCK_WATCH_ENABLED=true` y **`STOCK_WATCH_SOLO_REGISTRO=false`** — el vigilante
está escribiendo stock en Woo de verdad (70 `odoo_delta` y 22 `woo_cambio` en
48 h), y `DROP_MIRROR_ENABLED=true` la lee cada 20 min para ser la ÚNICA fuente
del canal `general` de `channel.listings` (13,103 publicaciones).

**La trampa propia de esta tabla.** No guarda un valor, guarda el ESTADO
ANTERIOR:

    delta = odoo_ahora − odoo_en_la_foto     →     Woo += delta

Si la foto se congela pero sigue legible, `odoo_en_la_foto` nunca avanza y **el
mismo delta se re-aplica cada 20 minutos, para siempre**. No es el error de los
964 pedidos fantasma —aquél fue un `None` leído como "no existe"— es peor: aquél
se detenía al arreglarlo, éste se acumula mientras dure.

Por eso el orden va **al revés que en el paso 1**: primero la ESCRITURA a los
dos lados, después días de comparación, y la LECTURA al final. Dos flags en vez
de uno: `SUPABASE_WRITE_STOCK_WATCH` y `SUPABASE_READ_STOCK_WATCH`, ambos en
`false` por defecto. Con los dos apagados este deploy no cambia nada.

**Un defecto encontrado de paso, y arreglado.** `stock_watch._foto()` tenía
`except Exception: return {}` con el comentario "tabla aún no creada" — pero
`_asegurar_schema()` corre justo antes, así que ese `except` ya solo atrapaba
fallos reales de la base. Y ahí el `{}` no decía "no hay foto", decía "no sé":
`revisar()` lo tomaba por PRIMERA PASADA, y la primera pasada **absorbe en la
foto todo lo pendiente sin aplicarlo**. O sea que un parpadeo de MySQL tiraba a
la basura, en silencio, los deltas de Odoo y los cambios de Woo de esa vuelta —
mercancía que nunca llegó a los canales. Ahora el error propaga, la pasada se
aborta con estado `foto_no_disponible` y no se escribe nada. Vacío de verdad
(primera corrida) sigue devolviendo `{}`: roto y vacío ya no se confunden. **Es
la misma familia de los candados del paso 0, que siguen pendientes.**

**Detalles que no son cosméticos:**

- **`citext` y no `text`.** La PK de MySQL usa `utf8mb4_uca1400_ai_ci`, que es
  case-insensitive. Con `text`, una foto que allá era UNA fila se partiría en
  dos y la primera pasada vería un delta que no existió. (Verificado: 14,640
  entraron y 14,640 salieron — no había colisiones, pero el tipo protege igual.)
- **`actualizado` sigue significando "cuándo se MIRÓ", no "cuándo cambió".** Se
  reescribe en toda la tabla cada pasada aunque el número no se mueva. Ahorrar
  esas escrituras con un `where ... is distinct from` la convertiría en otra
  cosa, y esa confusión exacta (`channel.listings.updated_at`) ya produjo tres
  diagnósticos equivocados seguidos.
- **Los dos lados sellan la pasada con el MISMO instante**, pasado desde Python.
  Si cada uno pusiera su reloj (`now()` de Postgres contra `datetime.now()`), el
  arnés vería una diferencia en cada fila de cada pasada y habría que inventarle
  una tolerancia — un arnés que ya no compara.
- **El espejo del DROP sigue el MISMO flag que el vigilante**, no uno propio: si
  los dos lectores de la foto pudieran apuntar a lados distintos, el panel
  mostraría un stock y el vigilante decidiría con otro.
- **Sin `coalesce` en el upsert**, al revés que en el paso 1: aquí un NULL es
  informativo ("Woo no gestiona stock de este SKU") y tiene que poder pisar a un
  número anterior.

**Dos arneses nuevos.** `backfill_stock_watch_foto.py` aborta si la foto de
origen lleva más de 60 min sin refrescarse (copiar una foto detenida es sembrar
el error, no migrarlo) y verifica las 14,640 filas enteras, no una muestra.
`comparar_stock_watch_foto.py` se corre cada mañana durante la observación y
mide cinco cosas: que las dos fotos estén VIVAS, mismo censo, mismos valores
—NULL incluido—, **el mismo delta que se aplicaría** (que los datos coincidan es
la hipótesis; que la decisión coincida es la conclusión) y el desfase contra
`channel.listings` canal `general`.

**Anotado para después:** `odoo_watch` guarda su foto en `productos.stock_odoo`
(MySQL), la otra razón por la que `productos` no está congelada — pendiente 1b
de F8. Esta tabla ya tiene esa columna y cubre 13,039 SKUs contra los 4,786 de
aquélla: cuando toque decidir si a ese vigilante se le da casa o se apaga, la
casa ya existe. No se hace ahora para no mezclar dos flujos vivos en un cambio.

Probado: migración aplicada a sandbox y a producción; backfill verificado en los
dos (**14,640 filas × 4 columnas = 58,560 celdas idénticas, cero diferencias**);
arnés de comparación en verde en los cinco bloques, incluidas **0 publicaciones
`general` desfasadas** contra la foto. Versión 0.167.0.

### v0.169.0 — Paso 3, primer rescate: las 44 bajas por marca de Amazon

Primer sub-paso del grupo 4 ([docs/PLAN_31_TABLAS.md](docs/PLAN_31_TABLAS.md)),
y el censo previo cambió el tamaño del paso.

**Tres correcciones al plan, medidas contra producción:**

1. **La bitácora destino ya existía y está viva.** `ops.channel_submissions`
   tiene 24,558 eventos y sigue escribiendo (`mercado_libre` 18,244, `amazon`
   4,358, `tiktok` 1,886, `temu` 70). El paso 3 no arranca de cero.

2. **Los "269 fallidos" de ML del plan eran FILAS, no SKUs.** `ml_progress`
   tiene llave `cuenta:sku`, así que un SKU intentado en las dos cuentas cuenta
   doble. Son **138 SKUs, y los 138 ya tienen su motivo en la bitácora**. Ese
   lado no necesitaba rescate — lo di por hueco al principio por leer filas
   como SKUs, y la medición lo corrigió.

3. **El hueco real era de Amazon y es otra cosa.** 126 SKUs con `success=0`,
   82 ya cubiertos, **44 huérfanos**: todos entre el 30-jun y el 6-jul, todos
   anteriores al arranque del espejo (24-jul), ninguno posterior.

**Y no son fallos de publicación.** Los 44 están en `DELETED` con motivo
`bulk_deleted_ip_brand` (30) o `amz_infringement_deleted` (14): **Amazon les dio
de baja el listing por marca / propiedad intelectual**, después de haber vivido
publicados desde el 23-jun.

`channel.listings` ya contesta bien la pregunta "¿está publicado?" — los 44
figuran ahí como `DELETED`. Lo que no existía en kubera es el **por qué**, y esa
diferencia cuesta dinero: un SKU que solo dice `DELETED` se ve como candidato a
re-publicar, y re-publicarlo lo vuelve a tumbar con el historial de infracciones
de la cuenta de por medio. Es literalmente el caso que el consejo describió:
`channel.listings` es superset **solo de los éxitos**.

**Decisiones del rescate que no son obvias:**

- **`operacion='baja_ip'`, no `'alta'`.** No fue un alta que salió mal: fue una
  baja impuesta desde afuera. Registrarla como alta fallida haría creer que
  alguien intentó publicar el 30-jun, y no pasó eso — el `published_at` de los
  44 es del 23-jun y salió BIEN.
- **`created_at` = la fecha del origen, no `now()`.** La bitácora se lee en
  orden cronológico; sellar un evento de junio con la fecha de la copia lo
  pondría al frente de la fila y contaría una historia falsa. Mismo error que
  hubo que corregir en `crear_logs` (60 filas con hasta 17.6 h de desfase
  invirtieron el estado de 50 SKUs). El script lo **verifica**: cuenta cuántas
  filas quedaron selladas después del 24-jul y reprueba si hay alguna.
- **Idempotente por `detail_ref`**, no por conteo, y solo toca SKUs sin ningún
  evento de fallo: nunca pisa lo que el espejo ya trajo. Verificado
  reejecutándolo: la segunda corrida no inserta nada.

**Censo de lectores del grupo 4, de paso:** son **25 sitios vivos** en
`services/` (no 19), y **24 preguntan lo mismo** — "¿está publicado y con qué
id?" → `channel.listings`, que ya tiene `listing_id`, `url`, `status`,
`situacion` y hasta `product_type`. Lo único sin casa ahí es `published_at`, y
la bitácora sí lo tiene.

**El dato que más cambia el riesgo del paso 3:** ningún código lee el MOTIVO del
fallo. Verificado — los únicos usos de `gtin_error`/`error_label` en código vivo
son escrituras, o leen el diccionario de resultado en memoria, no la tabla. Los
138 + 44 los consulta una persona, no un flujo. El "split por intención" del
consejo sigue siendo correcto, pero su riesgo es de **archivo histórico**, no de
una decisión automática que se vaya a equivocar.

Probado: dry-run, corrida real y reejecución inerte. El hueco pasó de 44 a **0**
y las 44 fechas viajaron intactas (0 selladas con fecha de copia).
Versión 0.169.0.

### v0.170.0 — Paso 3, bloque 1: el seam de publicación (APAGADO)

Iba a repuntar los tres archivos de menos riesgo del grupo 4 (`studio`,
`presencia`, `publicar`) y **paré antes de tocarlos**, porque el barrido
descubrió que el paso 3 no empieza donde el plan decía.

**`ml_progress` y `amazon_progress` NO son bitácoras congeladas: están vivas.**
`ml_progress` se escribió hoy 18:25, `amazon_progress` ayer (24 y 12 escrituras
en 7 días). Y la cadena real es:

    publicar  →  ml_progress / amazon_progress  (MySQL)
                          ↓  sync de 15 min (inventario.py)
                   channel.listings  (kubera)

Verificado en el código: **el publicador no tiene seam a `channel.listings`**.
Escribe MySQL y nada más; kubera se entera hasta el siguiente sync, y ese sync
lee de `ml_progress`. Así que `ml_progress` no es "la bitácora del publicador":
es el **acta de nacimiento** de la publicación, y durante hasta 15 minutos es lo
único que sabe que ese listing existe.

Por eso el bloque de `presencia.py` que consulta `ml_progress` después de
`channel.listings` **no es redundancia, es la red de lo recién publicado**.
Repuntarlo sin el seam convierte "publicado hace 30 segundos" en "sin publicar".
Misma forma que el bloqueador del corte de `core`: el seam Crear →
`core.products`.

**Qué se agrega** (`services/publicacion_seam.py`, `SUPABASE_SEAM_PUBLICAR`,
apagado): al publicar, `channel.listings` se entera en el momento. ML manda
`listing_id` y `url`; Amazon manda `status` y `product_type` — **no
`listing_id`**, porque el ASIN todavía no existe al publicar (los 1,791
registros de `amazon_progress` nacidos así tienen `asin` NULL).

**Lo que el seam NO toca, y es lo importante:** `is_fulfillment` jamás. El upsert
del sync lo escribe SIN `coalesce`, así que mandar un `false` por "no sé"
apagaría el FULL de una publicación que sí lo es — y al republicar un SKU
reciclado eso sería un dato falso sobre dónde está la mercancía. Precio, stock,
situación y logística tampoco: los observa el sync contra el marketplace.

La escritura va en un hilo (regla 11) y es best-effort **mientras sea espejo**:
un fallo degrada la frescura, no la verdad, y queda anotado en
`ops.migration_issues`. Está escrito en el módulo que **el día que se repunten
los lectores ese `except` tiene que morir** — ahí un fallo silencioso sería una
publicación invisible, que es la trampa del paso 0.

**Arnés nuevo** (`comparar_seam_publicar.py`) y su línea base de hoy: 203
publicaciones de ML y 31 de Amazon en 14 días, **todas presentes en kubera**, 0
sin `listing_id`. Arbitra por RECENCIA en vez de exigir igualdad: los 2 SKUs con
id distinto (`TEC-0404-MET` en las dos cuentas) son republicaciones donde kubera
tiene el vivo y `ml_progress` el viejo — informativo, no fallo. Es el fenómeno de
los 63 pares que citó el consejo.

**Una métrica que se construyó y se tiró.** La primera versión medía "cuánto
tarda kubera en enterarse" restando `published_at` de `listings.updated_at`, e
imprimía una mediana de 889 minutos. El número era real y medía otra cosa:
`updated_at` significa **cuándo CAMBIÓ el dato**, no cuándo se visitó, y
`listing_history` ni siquiera registra `listing_id`. Es la misma trampa que
invalidó `turno_sync` en `vigilar_congelacion.py`. Se reemplazó por el síntoma
que sí es observable —publicaciones sin `listing_id` en kubera— y el porqué
quedó escrito en el código para que nadie lo reponga.

**Defecto encontrado de paso, aún sin corregir:** en `presencia.py` el bloque de
ML tiene guardia contra duplicar (`if MERCADO_LIBRE in acc: continue`) y el de
Amazon no. Con `SUPABASE_READ_CHANNEL=true`, `channel_read.presencia()` ya
devuelve Amazon, así que **1,387 SKUs salen con `n=2` para una sola
publicación**. Se corrige al repuntar ese archivo, que es el paso siguiente.

Sin migraciones. La variable nace apagada: este deploy no cambia nada.
Encenderla mete una escritura en el flujo de PUBLICAR — negocio vivo, regla 3,
dale de Brandon. Versión 0.170.0.

### v0.171.0 — Amazon dejaba de contarse doble en los puntos de la vista GENERAL

`presencia.py` arma los "puntos de colores" acumulando por `(sku, canal)`, y su
`_agregar` **no reemplaza: SUMA** (`n += 1`) cuando el canal ya existe. El bloque
de Mercado Libre tenía la guardia para no duplicar:

    if Canal.MERCADO_LIBRE.value in acc.get(r["sku"], {}):
        continue

y **el de Amazon no la tenía**. Con `SUPABASE_READ_CHANNEL=true`,
`channel_read.presencia()` ya devuelve Amazon (`CANALES` lo incluye), así que
cada SKU presente en las dos fuentes salía con **`n=2` para una sola
publicación**: los 1,387 que `channel.listings` tiene con `item_id`.

Medido antes y después sobre 120 SKUs reales de producción: **120 con `n=2`
antes, 0 después**.

El bloque de `amazon_progress` **no se borra**: sigue siendo la red de lo recién
publicado, igual que el de ML. Un SKU sin `listing_id` en `listings` —Amazon no
asigna el ASIN al publicar— no entra por arriba y necesita esa vuelta. Lo que se
corrige es que cuente dos veces al mismo.

El defecto entró al descubierto: apareció midiendo el bloque 1 del paso 3, no
por un reporte. Salió a la luz porque el corte de channel encendió la primera
fuente y nadie volvió a mirar la segunda.

Sin migraciones ni variables nuevas. Versión 0.171.0.

### v0.172.0 — Paso 4: la historia de las dos tablas de imágenes, y 21,816 fechas mentirosas

**Lo que se completó.** `ml_image_edit_backlog` → `ops.channel_submissions`
(faltaban 368) y `amazon_imagenes` → `enrich.product_media` (faltaban 156). Las
dos quedaron 1:1: **12,592/12,592** y **678/678**.

**Una hipótesis mía que la medición tumbó.** Di por hecho que lo faltante era
historia anterior al espejo, como en las 44 bajas de Amazon. Las fechas dijeron
otra cosa: las 156 imágenes son **del 4 al 13 de agosto**, con el espejo
encendido y la tabla en `KUBERA_MIRROR_TABLAS`. No era historia: era pérdida
reciente.

**El mecanismo, que sí quedó probado en el código.** `kubera_mirror.espejar()`
encola con `put_nowait`; si la cola está llena (`_COLA_MAX = 500` por worker) el
evento **se descarta**, y `_registrar()` lo anota **solo en un buffer en memoria
de 500 eventos**, no en `espejo_kubera_log` —la tabla que sobrevive al
reinicio—. Por eso el log de errores estaba vacío mientras faltaban 156 filas.
Verificado además que las 156 no estaban bajo otro SKU: faltaban de verdad.

No está probado que esas 156 se hayan ido por ahí (el buffer murió con un
reinicio). Lo que está probado es que **el canal de pérdida silenciosa existe**.
Queda anotado como pendiente: un descarte por cola llena debe persistirse y
poder reprocesarse, como cualquier otro error del espejo.

**Y de paso, el defecto grande: 21,816 eventos fechados el día equivocado.**

    ml_image_edit_backlog  11,978
    ml_backlog              5,556
    amazon_backlog          4,282
                           ──────
                           21,816   promedio 47 días de desfase, el peor 128

El 24-jul un `TRUNCATE CASCADE` de `etl_core_products` vació la bitácora y se
reconstruyó con `backfill_channel_submissions`. Ese backfill llenaba
`submitted_at` con la fecha real pero **no pasaba nada para `created_at`**, que
tomó su `default now()`. Resultado: eventos de marzo figurando como del 24-jul.

Es el MISMO defecto de `crear_logs` (60 filas, hasta 17.6 h) a 360 veces la
escala — allá lo produjo un reproceso ocasional, aquí una restauración completa.
Allá SÍ hizo daño porque el historial busca el último evento por SKU e invirtió
el estado de 50; aquí todavía no hay quien ordene por esa columna (verificado:
solo la tocan scripts, con `exists`). Se arregla antes de que lo haya, porque
esta bitácora es lo que va a quedar cuando MySQL se retire, y un archivo
histórico con las fechas mal no es un archivo histórico.

Corregido: `created_at := submitted_at` en las 21,816. La bitácora pasó de
empezar el 24-jul a abarcar **del 17-mar al 14-ago**. Y la causa raíz quedó
tapada: `_up_channel_submissions` ahora acepta `creado` —igual que
`_up_process_log`, donde el arreglo ya existía— y los tres payloads del backfill
lo pasan. Sin eso, el próximo backfill volvería a sellar mal.

**Corrección al plan:** `amazon_imagenes` **no es una caché regenerable**. Cada
fila es descargar la imagen, WebP→JPEG, escalar a ≥1000 px y a veces Real-ESRGAN.
Perder una no es "se vuelve a consultar": es reprocesar y subir OTRA copia a
WordPress con otro `wp_media_id`.

**Nota de llaves, para quien repunte el lector:** en MySQL la PK de
`amazon_imagenes` es el SHA-1 de la URL, **única global**; en kubera el índice
único es `(sku, kind, source_url)`. La misma URL usada por dos SKUs es UNA fila
allá y DOS aquí. No es pérdida y los conteos no tienen por qué cuadrar — el
lector pregunta "¿ya procesé esta URL?" sin filtrar por SKU.

Sin migraciones ni variables nuevas. Versión 0.172.0.

### v0.181.0 — Las 17 columnas de Análisis caben sin scroll

Pedido de Eduardo: con las medidas (v0.175.0) la tabla llegó a 17 columnas y
**Ganancia se salía del borde**. Dos cambios chicos, medidos y no estimados.

**Se ensancha el lienzo.** `max-w-[1600px]` → `1800px` en el `<main>` de
`app/analisis/layout.tsx` **y en `AppNavbar`**. Van los dos juntos a propósito:
si solo crece el contenido, en pantallas grandes la barra queda angosta respecto
a la tarjeta y se nota. Cada archivo lleva una nota apuntando al otro.

**Se aprieta lo que yo mismo agregué.** Las cuatro columnas de medida pasan de
`px-2` a `px-1` —son números cortos y no necesitan ese aire— y la celda de
producto de `max-w-[270px]` a `185px`. El título ya venía truncado antes del
cambio, así que lo que se pierde son caracteres de un texto que igual no cabía.
El encabezado manda el ancho de la columna, así que `Th` recibe una prop
`compacto` para igualar el `px-1`; apretar solo la celda no habría servido.

**Medido en el sandbox, no calculado.** El ancho MÍNIMO del contenido de la
tabla —el que decide si hay scroll— bajó a **1,428 px**. Medirlo requiere
encoger la ventana: la tabla es `w-full` y a pantalla ancha se estira para
llenar el contenedor, así que comparar `scrollWidth` contra el contenedor ahí
siempre da "justo cabe" y no dice nada.

A 1642 px de pantalla (la de Eduardo) el contenedor mide 1,582: **154 px de
holgura**, sin scroll horizontal, con Ganancia completa dentro del borde. Por
debajo de ~1,490 px vuelve a aparecer la barra de scroll, que es el
comportamiento que ya tenía y para lo que está el `overflow-x-auto`.

Sin migraciones ni variables nuevas. Versión 0.181.0.

### v0.176.0 — El reclamo va antes de crear: se cierra la ventana del relevo

El 14-ago la orden 2000017937146172 quedó duplicada en Woo (#123068 y #123069,
3 s de diferencia) **un segundo después del relevo de contenedores** del deploy
de las 19:12:23. `_locks` serializa dentro de UN proceso; un deploy tiene dos, y
cada uno se creyó el primero. El registro tampoco alcanzaba: se escribía DESPUÉS
de crear en Woo, así que al proceso viejo lo mataron con el pedido ya creado y
sin rastro en kubera.

A diferencia de los 15 duplicados anteriores, éste **no era FULL**: los dos
pedidos descontaron pieza y faltó una unidad real. Se canceló el duplicado (Woo
devolvió la pieza, `_reduced_stock` 1→0) y se mandó a papelera.

`orders_write.reclamar` gana el derecho a crear con un INSERT atómico sobre la
PK `(canal, cuenta, external_order_id)` — eso sí cruza procesos. `wc_order_id`
queda NULL hasta completar: es la señal de "reclamado, aún sin pedido".

El perdedor NO crea: espera 4 s por si el ganador está terminando, y si sigue sin
pedido le pregunta a **Woo** (`wp_db.pedido_por_ml_order_id`), que es donde el
duplicado se vería. Si Woo tampoco lo tiene, el ganador murió sin crear y el
perdedor toma el relevo.

Y `liberar` suelta el reclamo si la creación falla: dejarlo puesto haría que el
siguiente aviso viera "ya reclamada" y no la creara nunca — perder la venta en
silencio es peor que el duplicado que se evita. Solo borra si `wc_order_id` sigue
NULL, nunca una venta completada.

Si kubera no responde, `reclamar` devuelve True: arriesgar un duplicado
detectable es mejor que perder la venta.

Sandbox 8/8 (`probar_reclamo_pedidos.py`): el primero gana y el segundo pierde,
el reclamo nace con wc_order_id NULL, liberar suelta el vacío y respeta el
completado, y tras completar `wc_order_id_previo` contesta el id real. Versión 0.176.0.
### v0.211.1 — La columna Tamaño con colores, y por qué NO son verde/ámbar/rojo

Pedido de Eduardo: *«ponlo por colores dependiendo de su tamaño»*. Revisado en
el sandbox antes de subir, como lo pidió.

| categoría | color |
|---|---|
| Chico | gris (`slate-100`) |
| Mediano | celeste (`sky-100`) |
| Grande | índigo (`indigo-100`) |
| Extra grande | fucsia (`fuchsia-100`) |
| sin medidas | guion tenue, **sin chip** |

**Se evitaron a propósito el verde, el ámbar y el rojo.** En esta tabla ya
significan margen sano, aviso y pérdida; un tamaño en rojo se leería como un
problema, y ser grande no es un defecto — es un costo de flete más alto. La
rampa va de neutro a intenso, que se entiende como «cada vez más» en lugar de
«cada vez peor». El color aquí es una ESCALA, no un semáforo.

**«Sin medidas» no lleva chip**: pintarle un color lo haría parecer una
categoría más y es justo lo contrario — la ausencia del dato.

Verificado en el sandbox: las cinco categorías presentes con su color y sin
scroll horizontal.

### v0.211.0 — El tamaño sale del nombre y se vuelve columna, en palabras

Pedido de Eduardo (18-ago): una columna que diga **Chico / Mediano / Grande** y
quitar la etiqueta pegada al nombre del producto.

**Por qué la etiqueta estorbaba donde estaba.** El chip `M` vivía junto al SKU
compitiendo por espacio con los puntos de cuenta y la marca de peso divergente,
en una celda ya truncada — y siendo un dato de PRODUCTO, no de identidad. Como
etiqueta tampoco se podía ordenar por ella.

**Las letras siguen siendo el formato de la API.** El filtro y el orden viajan
como `S/M/L/XL` igual que siempre; las palabras viven solo en la pantalla.
Cambiar el código en el backend habría roto el filtro y cualquier enlace
guardado, para ganar nada.

| código | columna | corte |
|---|---|---|
| `S` | Chico | lado mayor < 30 cm |
| `M` | Mediano | < 60 cm |
| `L` | Grande | < 120 cm |
| `XL` | **Extra grande** | ≥ 120 cm |
| `S/C` | — | sin medidas capturadas |

`XL` necesitaba nombre: el pedido mencionaba tres palabras y las categorías son
cuatro. El filtro de arriba habla ahora el mismo idioma que la columna.

**Ordena por el lado mayor REAL, no por la palabra.** Alfabéticamente `L` va
antes que `M` y que `S` — justo al revés del tamaño. Se agrega `lado_mayor` al
CTE `filas` y `_ORDEN` mapea `tam → lado_mayor`, así que además ordena bien
DENTRO de una misma categoría. Verificado en los dos sentidos: descendente
arranca en `CAM-0003-MAD` (199 cm) y ascendente en los `S/C` sin medidas.

La tabla pasa de 17 a 18 columnas y **sigue cabiendo sin scroll** a 1642 px: la
columna nueva se compensa con el espacio que liberó el chip.

### v0.210.0 — El reporte FBA se refresca solo cada mañana

Cierre de la pestaña (Eduardo, 18-ago): un cron diario pide el reporte a la
Reports API a las **13:10 UTC (07:10 de México)** y reemplaza el snapshot antes
de que empiece el día. Nace **encendido** por pedido explícito — la reversa es
`FBA_REFRESCO_AUTO=false` (o mover `FBA_REFRESCO_HORA_UTC`), sin deploy, como
todo flujo.

El job usa `refrescar_programado`, que a diferencia del botón ESPERA el candado
si hay un refresco corriendo en vez de saltárselo: el diario no debe perderse
por coincidir con un clic. Sin credenciales de Amazon (staging) marca el error
legible y no toca el snapshot — el mismo comportamiento ya probado.

La fase 2 quedó verificada de punta a punta en producción el mismo día: disparo
manual a las 20:17, Amazon generó el reporte 50275020683 en ~30 s, y el
snapshot pasó de 1,258 SKUs (CSV de la mañana) a 1,259 (fresco). La comparación
entre las dos fotos capturó el movimiento del día: ~1,950 unidades pasaron de
"en camino" a "disponibles" — Amazon recibió el embarque durante la jornada.

### v0.209.0 — El reporte FBA se trae solo: botón «Traer de Amazon»

Fase 2 de la pestaña (Eduardo, 18-ago: *«continuemos con la implementación»*).
La subida manual del CSV queda, y aparece el camino sin manos: **la Reports API
de SP-API** genera el mismo reporte (`GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA`,
verificado: son exactamente las columnas del export de Seller Central) y el
backend lo descarga y lo carga al terminar.

**Un solo código para las dos puertas.** El parseo y el guardado se mudan del
router a `services/fba_reporte.py`: la subida manual y el refresco automático
pasan por las MISMAS funciones. Si un día se corrige uno y no el otro, la
pestaña diría cosas distintas según cómo entró el dato — por eso no hay copia.

**El refresco corre en segundo plano.** Amazon tarda de uno a varios minutos en
generar el reporte; `POST /api/fba/refrescar` dispara y contesta de inmediato,
un `asyncio.Lock` impide dos refrescos encimados, y la página va leyendo el
avance (`solicitando → esperando → descargando → listo`) refrescándose sola
cada 10 s hasta que el snapshot aterriza. Todo el HTTP es httpx **async**
(regla 11); el guardado sale a hilo.

**Si falla, falla legible y sin daño**: el estado dice por qué (probado en el
sandbox sin credenciales: *«sin credenciales de Amazon en este ambiente»*) y el
snapshot cargado **no se toca** — el parser valida columnas antes de escribir,
y el guardado es una transacción.

Verificado en el sandbox: disparo → error limpio → snapshot intacto (1,258
SKUs del CSV siguen ahí); página con los dos botones y el aviso. La prueba con
credenciales reales solo puede pasar en producción — se corre al desplegar.

### v0.208.0 — Amazon FBA deja de ser placeholder: inventario, cobertura y plan de envío

Pedido de Eduardo (18-ago): desarrollar la pestaña `/analisis/fba` con el
prompt original de José (prompt 1, pieza FBA) y el export **"Manage FBA
Inventory"** de Seller Central que compartió (1,258 SKUs).

**La fuente es el reporte subido, no el sync — y la diferencia es enorme.** El
sync de inventario veía **20 SKUs con FBA (1,299 uds)**; el reporte fresco trae
**101 SKUs con 2,224 uds en bodega MÁS 3,426 EN CAMINO** (inbound), que la API
de summaries que usamos ni reporta por separado. El reporte además trae el
**ASIN** de los 1,258 (bloqueo #1 histórico de la pestaña) y el **volumen por
unidad medido por Amazon** — verificado que viene en cm³ contra dimensiones
conocidas del costeo.

**Piezas nuevas:**

- **Migración 0023** — `ops.fba_snapshot` (mismo carácter que
  `ops.stock_watch_photo`: foto operativa, no caché). Aplicada al **sandbox**;
  producción con el visto de Eduardo, como manda el encabezado de la 0020.
- **`POST /api/fba/reporte`** — sube el CSV (cp1252 verificado, TAB detectado,
  valida columnas con error legible). Cada subida REEMPLAZA la foto en una
  sola transacción (delete+insert): snapshot, no historial.
- **`GET /api/fba`** — el tablero: foto FBA ⋈ ventas de Amazon del período ⋈
  costeo. Por SKU: disponible/reservado/en camino, venta diaria, **cobertura**
  (disponible ÷ venta diaria) con el semáforo del restock original
  (**14/30/50 días** → crítico/alerta/ok, arriba sobrado), y el **plan de
  envío**: piezas para dejar N días de cobertura descontando lo disponible y
  lo que ya viene en camino, con su volumen en m³ usando la medida de Amazon.
  `AGOTADO` va aparte y primero: vende y FBA está en cero.
- **La página** — KPIs, subida del reporte ahí mismo, filtros por urgencia,
  ASIN como liga a amazon.com.mx, y la sección "venden en Amazon y NO están
  en FBA" (los candidatos que el reporte no puede ver).
- **Divergencia de volumen** ⚠ — Amazon midió la unidad empaquetada; ~10-15%
  arriba del costeo es empaque, 2× es captura mala. Misma lógica que el peso
  de la báscula de ML. Caso `ACC-0091-AST-PLA`: 509 cm³ medidos por Amazon
  contra 21,318 del costeo.

**Del prompt original quedan FUERA a propósito**: capacidad contratada (no
vive en ningún lado ni la trae el reporte) y tier por peso (sin la tabla de
tarifas de Amazon el tier es un adorno). El repo fuente de José no está en
esta máquina; se construyó desde la descripción del prompt.

**Verificado en el sandbox con el CSV real**: subida 200 (1,258 SKUs), KPIs
exactos contra el perfil medido a mano del archivo (2,173 disponibles, 51
reservadas, 3,426 en camino), 110 filas en el tablero, filtros 10/110/31, y
la página renderizando de punta a punta. **La foto que revela**: 10 SKUs
venden y están AGOTADOS en FBA (encabeza `MUE-0307-GRI`, 36 uds/60d), 31
tienen stock sin una venta (2,144 de las 2,173 uds disponibles), y 67 SKUs
están llegando (en tránsito). Lo que vende no está, y lo que está no vende.

⚠ **Para encender en producción falta aplicar la migración 0023** (una tabla
nueva en `ops`; no toca nada existente). Hasta entonces la pestaña contesta
503/error de tabla al subir.

### v0.207.2 — TikTok repite seller_sku: el censo colapsa duplicados antes del upsert

Segunda corrida real (18:47): `ON CONFLICT DO UPDATE cannot affect row a
second time` — TikTok permite VARIOS productos con el mismo seller_sku
(re-publicados) y dos filas con la misma llave en un `execute_values` truenan.
El censo ahora deduplica por producto (la paginación puede repetir) y por SKU
con prioridad del status que vende (ACTIVATE > PENDING > DRAFT > FAILED), y
anota cuántos colapsó. Versión 0.207.2.

### v0.207.1 — El censo de TikTok tronaba en el upsert (11 valores, 12 columnas)

La primera pasada real del censo (18-ago 18:40, encendido con dale de Brandon)
paginó las 13 páginas con el token recién auto-renovado — **el auto-refresh
reactivo quedó probado en producción: se sanó solo a las 18:28, tres minutos
después del deploy** — y murió al escribir: `INSERT has more target columns
than expressions`. Faltaba el `template` de `execute_values` con el `now()`
final (el mismo que `cargar_tiktok` sí lleva). Una línea. Versión 0.207.1.

### v0.207.0 — Auditoría del fan-out: dos escritores muertos, un tramo roto, y el mapa que faltaba

Nadie había dibujado el fan-out completo. Al dibujarlo (encargo de Brandon,
auditoría con verificación adversarial — **[docs/AUDITORIA_FANOUT.md](docs/AUDITORIA_FANOUT.md)**,
ahí vive el diagrama y toda la evidencia) apareció el titular: **de los tres
escritores, solo Mercado Libre estaba vivo.**

- **Amazon murió el 29-jul 17:13** por un desfase de vocabulario: v0.33.0
  empezó a guardar el estado REAL (`BUYABLE`/`DISCOVERABLE`) en `situacion` y
  `_SITUACIONES_VIVAS` nunca aprendió las palabras nuevas — la guarda descarta
  un BUYABLE con el motivo falso "escribirle la REACTIVARÍA" ANTES de llegar a
  la lectura en vivo que sí sabía. Inversión perversa: solo las filas MUERTAS
  (PUBLISHED rancio del COALESCE) pasaban la guarda.
- **TikTok murió el ~15-ago**: el access_token dura ~7 días y `refrescar()`
  llevaba desde el 8-ago sin UN SOLO llamador. 4 errores `105002` y 3 días de
  silencio — las alertas tampoco vigilaban TikTok.
- **El tramo «delta de Odoo → canales» no existía**: `stock_watch` aplicaba el
  delta a Woo pero no encolaba, y la foto lo absorbía. 755 deltas, 48 llegaron
  a canales — todos de carambola. Peor: si la escritura a Woo FALLABA, la foto
  absorbía igual y el delta se perdía para siempre (ORG-0785: 60 pzas).

#### La decisión de arquitectura (Brandon, 18-ago)

**ML/Amazon/Walmart = fulfillment** (FULL/FBA/WFS; sus bodegas). **Amazon SALE
del fan-out** — no se le alimenta stock (`FANOUT_CANALES=mercado_libre,tiktok`,
ya guardado en Railway; aplica con este deploy). Las DROP pausadas de ML SÍ
siguen (higiene para el día que reactiven). **TikTok/Temu (después SHEIN) =
ÚNICAMENTE DROP**: el destino real.

#### Lo construido

1. **Tramo Odoo→canales CERRADO** (`stock_watch.py`): `_escribir_woo` ahora
   reporta QUÉ SKUs quedaron escritos OK (lee los errores POR ÍTEM del batch,
   no solo el status); cada delta aplicado se ENCOLA al fan-out
   (`motivo="delta de Odoo aplicado"`); la foto absorbe SOLO lo escrito OK —
   lo fallido conserva memoria y se reintenta, con la falla anotada.
2. **TikTok se auto-sana** (la regla 8 de ML, aplicada): `tiktok.llamar`
   detecta `105002`, refresca con `refrescar_y_guardar()` (UPDATE por
   `shop_id` — NUNCA `guardar()`, cuyo respaldo insertaría una fila con
   `shop_cipher=NULL` y rompería el canal al revés) y reintenta UNA vez.
   Además: job proactivo `tiktok_token` (`TIKTOK_REFRESH_ENABLED`, nace
   apagado), `POST /api/tiktok/token/refrescar`, y el vigilante de Slack ahora
   avisa si el token vence en <24 h.
3. **Censo de TikTok por API** (`services/tiktok_censo.py`, job
   `TIKTOK_CENSO_ENABLED` nace apagado, `POST /api/tiktok/censo`): el hueco
   grande no era el token — son los 599 DRAFT del espejo que `tk_activar.py`
   activa desde el ESCRITORIO sin reflejar: publicaciones ya a la venta que el
   fan-out omite como borrador. Sobreventa en potencia, medida (11 omisiones
   14-18 ago).
4. **Alineación inicial** (`POST /api/fanout/alinear?canal=…&confirmar=true`):
   el fan-out es 100% por evento y no tenía barrido — revivir el token no
   recupera nada retroactivamente. Encola los vivos del canal (TikTok: 285
   ACTIVATE, solo ~24 divergen) y cada SKU pasa por TODAS las guardas.
5. **Temu, piezas inertes**: entra a `channel_read.CANALES` (por fin visible
   para `_destinos`, aunque sea para omitirse con motivo), rama propia
   DROP-only (a lo PUBLICADO se le escribe aunque el código crudo `4/7` no
   distinga activo/inactivo; `Incompleto`/`Borrador`/desconocido = falla
   cerrada), candado `FANOUT_TEMU` (nace apagado), `_reflejar` en
   `publicar_temu` (las altas nuevas dejan de ser invisibles hasta el
   siguiente censo manual), y la sonda **`scripts/sondear_temu_stock.py`**:
   `bg.local.goods.stock.edit` JAMÁS se ha llamado (parámetros NO VERIFICADOS)
   — el canario de 1 SKU confirma su forma antes de construir `_escribir_temu`.
6. **One-shot Amazon** (`scripts/apagar_amazon_fantasma.py`, decisión: qty 0
   SOLO a los 5 BUYABLE MFN con 878 pzas fantasma): el dry-run verificó EN
   VIVO y encontró que **el problema ya se había apagado solo** — 4 de 5 dan
   HTTP 404 (Amazon los eliminó; el caché estaba rancio) y `CONS-0016-EST` ya
   está DISCOVERABLE. Cero escrituras; 5 verificaciones selladas en
   `fanout_log` (`apagar_mfn`). Las 419 DISCOVERABLE con cantidad no se tocan
   (decisión).

**Cancelaciones/devoluciones** quedan documentadas, no tocadas: ninguna
dispara el fan-out directo (la guarda exige `accion=="creado"`); Woo repone
solo y `stock_watch` lo atrapa como `woo_cambio` ~20 min después. Y el candado
de cancelación de CLAUDE.md resultó VESTIGIO: la meta no persiste por REST; la
defensa real es la reversión de compensación.

**Orden de encendido propuesto** (cada dale por separado): refrescar token →
alinear TikTok → flags de refresh/censo → sonda Temu → escritor → FANOUT_TEMU.

Versión 0.207.0.

### v0.204.0 — El acta decía cuántos rompieron la racha, no cuáles

`seam_gap` es un número, y un número no se puede atender. El 18-ago la alerta
sonó con 31 y para saber qué SKUs eran hubo que ir a los logs del cron en
Railway: el ETL arma la lista, la imprime y el acta se queda solo con el total.
Ahora `reconciliation_runs.conteos` guarda `seam_gap_skus`, y de la MISMA lista
salen el stdout y el acta, para que no puedan contar cosas distintas.

Antes de guardarla había que arreglarla, porque tal como estaba habría guardado
menos de lo que rompe:

**1. No incluía las altas.** `seam_gap = len(inserts) + len(updates_seam)`, pero
`muestra_seam_gap` solo miraba `updates_seam`. Un día cuyo hueco fueran puras
altas —el 13-ago fueron 3, el 14-ago 4— habría dejado en el acta una lista
**vacía** junto a un `seam_gap` distinto de cero: el acta contradiciéndose sola.
Y un SKU que nace sin pasar por el panel es justo lo que el seam debía cubrir.

**2. Se recortaba en silencio, en 15.** Con el gap de 31 del 18-ago se habrían
guardado 15 y quien leyera el acta creería que ya los vio todos. Ahora se guardan
todos y, si algún día no cupieran (tope 500), el acta dice cuántos se quedaron
fuera en `seam_gap_omitidos`. Un recorte mudo es la misma clase de mentira que ya
invalidó `turno_sync`, `padron` y la métrica de retraso del seam.

Cada fila lleva su `tipo`, que es lo que dice dónde mirar: `alta` o `update` (con
los `campos` que difieren) en core; `asignacion_alta`, `asignacion_cambio` o
`nodo_del_panel` en categorías.

**Va en los dos ETLs.** El de categorías tenía el hueco idéntico —el acta
guardaba el total de sus tres sumandos y ninguno de los nombres— y también salió
`con_deltas` el 18-ago. Arreglar solo uno dejaba medio auditor mudo.

Dry-run contra el sandbox: en core la lista da 6 y cuadra con
`insertar + updates_seam`; en categorías da 58 y cuadra con los tres sumandos
(38 cambios de asignación, 12 altas, 8 nodos del panel). `seam_gap_omitidos` 0 en
ambos.

### v0.203.0 — La alerta del acta nombraba un espejo que ya no existe

El aviso de Slack decía *"hay deltas MySQL↔Supabase"* y, si faltaba el acta,
*"revisar el cron deltas en Railway"*. Las dos frases quedaron sin referente:
los crons `deltas-*` están retirados y ya no escriben acta, así que los únicos
dominios que llegan a ese aviso son los dos ETLs de las 06:15 — y ésos **no
abren MySQL desde la v0.129.0**: comparan Woo y Odoo vivos contra kubera.

Lo que reportan se llama `seam_gap`: lo que cambió en la fuente y ningún seam en
vivo alcanzó a cubrir. El texto ahora lo dice así.

No es cosmético. Con el nombre viejo el aviso se leía como residuo del espejo
apagado —algo que ya se dio por muerto— y se ignoraba, siendo que es el único
auditor del seam que queda vivo. Se descubrió preguntando justamente eso:
*"¿no habíamos apagado ya todas las alertas de deltas?"*.

Caso del 18-ago-2026 que lo destapó: `core-etl-v2` salió `con_deltas` con
`seam_gap=31`, y los 31 eran **el mismo campo, `name`**, en SKUs de variante
(`CAM-0004-*`, `ACC-0883-*`, `DEC-0153-*`, `DEC-0182-*`, `TEC-0066-*`…). Sus
productos padre se renombraron en Woo entre las 17:15 del 17-ago y las 05:28 del
18-ago; Woo dispara `product.updated` del padre pero no uno por variación, así
que el nombre derivado de cada variante se quedó viejo en `core.products` hasta
que el ETL lo corrigió a las 06:17. Verificado contra WordPress: después de la
corrida los 67 registros tocados coinciden exactamente con `wp_posts`.

Queda anotado, sin tocar: **el acta no guarda `muestra_seam_gap`**. El ETL la
arma y la imprime, pero `reconciliation_runs.conteos` solo recibe contadores, así
que cuando suena la alerta no hay forma de saber *cuáles* SKUs la rompieron sin
ir a los logs del cron en Railway. Es lo que hace falta para que el aviso sea
accionable.

### v0.202.0 — El flete ya estaba en el Costo Base; lo que faltaba era avisar cuándo NO está

Eduardo pidió **sumarle el flete al Costo Base** en la tabla de Análisis y en
Productos más vendidos, y marcar con advertencia los que no lo tengan.

**La primera mitad no se hizo, y a propósito: el flete YA está adentro.**
Verificado **contra producción** antes de tocar nada:

| tabla | filas | `costo = producto + flete` |
|---|---|---|
| `costing.costos_validados` | 15,431 con total | **15,431** |
| `costing.costos_finales` | 4,376 | **4,376** |

Sumarlo otra vez habría inflado el costo ~31% y hundido todos los márgenes. El
contrato ya estaba escrito en el propio código (*«Costo Base = producto + flete
de importación»*), y los datos lo confirman fila por fila.

**La segunda mitad sí hacía falta, y es real.** El flete pesa **31.1% del costo
en promedio**, así que un flete en cero no es «no aplica»: es un costo al que le
falta un tercio, con el margen saliendo optimista y nada que lo diga. Son **403
SKUs del catálogo** (medido en producción), y entre ellos `MAN-0495-BLN`
(producto $741, flete $0) — que **sí tiene medidas** (90×36×45), o sea que no es
que no se pueda calcular, es que no se capturó.

Ahora ese renglón sale en **ámbar con ⚠** en las dos vistas, y la tarjeta del
Costo Base explica qué significa.

**Y de paso, la tarjeta ahora abre el costo**: `…del cual, flete $X (N%)`. Eso
contesta sola una pregunta que venía apareciendo todo el día — por qué tantos
SKUs salen con «COSTO DUDOSO»:

| SKU | producto | flete | flete % | pista |
|---|---|---|---|---|
| `TEC-0393-ROS` | $190 | **$1,148** | **86%** | piezas/caja = **0.53** |
| `JUEG-0012-MUL` | $19 | $571 | **97%** | piezas/caja = **0.33** |
| `TEC-0793-NEG-4PZ` | $107 | $603 | 85% | caja 84×33×60, 27.7 kg |
| `MUE-0163-TEL` | $94 | $179 | 66% | **39.36 kg** en 12 L |
| `CAS-0005-BLN` | **$0** | $96 | 100% | sin costo de producto |

No es que el producto sea caro: **es el flete, y el flete está inflado por
defectos de captura ya documentados** — el divisor `piezas_por_caja` fraccionario
(0.33 multiplica el flete por 3) y el peso de la CAJA capturado como el de la
pieza (el mismo que la columna Peso marca en ámbar desde v0.175.0). Aparece
además una tercera clase: **cuatro SKUs con `costo_producto` en 0**, donde el
«costo» es puro flete.

**19 de los SKUs que vendieron en 30 días traen el flete arriba del 60%** del
costo (medido en producción, no en el clon).

**Ojo con medir esto en el sandbox.** El clon trae el costeo del 24-jul y
producción recostea a mano: `TEC-0794-NEG-8PZ-GUANTS` estaba en 47×32×**61 cm**
con flete **$332.61**, y el 17-ago se corrigió a 47×32×**8.71 cm** con flete
**$98.25** — el costo base cayó de $466 a **$233**. Un número de costeo leído del
clon puede tener semanas.

Backend: `costo_flete` viaja en `_BASE` y en `_SQL_MARGEN_REAL_TOP` **sin
sumarse a nada** — existe solo para poder avisar. En la vista fundida por SKU se
toma el primero no nulo, porque el costeo es por SKU y no cambia entre cuentas.

Sin migraciones ni variables nuevas. Versión 0.202.0.

### v0.200.0 — Las cuentas se ven igual en las dos pantallas, y se ve en cuál está activa

Eduardo, sobre el renglón fundido de v0.199.0: *«que se vea en cuál de las 2
cuentas está activa si aplica el caso, o que muestre que en ambas está activa o
en ninguna»*, y *«que se vea con los colores como en la tabla general y su
tarjeta»*.

**El problema real eran dos paletas para lo mismo.** El popup pintaba iniciales
`BK` en índigo y `SC` en celeste; la tabla de Análisis pinta puntos, `BK` en
celeste y `SC` en violeta. El mismo SKU se veía de dos maneras según dónde se
mirara, y el celeste significaba una cuenta en una pantalla y otra en la otra.

**Se comparte el código, no se copia.** `PanelHover` sale de `analisis/page.tsx`
a `components/PanelHover.tsx`, y `CUENTA_DOT` / `CUENTA_INI` / `CANAL_CORTO` a
`lib/canales.ts`. Dos copias del mismo panel se separan a la primera corrección
que solo se haga en una — que es exactamente cómo nacieron las dos paletas.

**El popup usa ahora el mismo patrón que la tabla:** los puntos de color por
cuenta y, al pasar el cursor, la tarjeta con el censo cuenta por cuenta
(`● Meli · BK — pausada`), cerrando con la lectura de conjunto.

**La etiqueta de estado se queda CORTA.** Se probó escribiendo el alcance
—`ACTIVA SOLO EN BK`, `EN NINGUNA`, `ACTIVA EN AMBAS`— y Eduardo pidió quitarlo:
sobra. Los puntos ya dicen en qué cuentas está y la tarjeta cuál está activa, así
que el texto largo repetía lo mismo, ensanchaba el renglón y obligaba a partirlo
en dos líneas. Queda `ACTIVA` / `PAUSADA` / `SIN PUB.` como en la tabla.

**Un hallazgo del propio cambio:** de los 10 más vendidos, **7 están a la venta
en una sola cuenta** y 2 en ninguna. Solo uno está activo en ambas. Eso no se
veía cuando el SKU salía dos veces compitiendo consigo mismo.

**Verificado contra el clon de producción:** los puntos salen celeste/violeta
como en la tabla, con el estado por cuenta correcto en datos reales
(`TEC-0778-NEG` activa en las dos, `MUE-0163-TEL` solo en Sancor,
`TEC-2162-NEG` en ninguna). La tabla de Análisis sigue montando sus
`PanelHover` después de la mudanza (10 por fila) y `next build` compila las 19
rutas. **No se pudo probar la apertura de la tarjeta con el cursor**: el panel
del navegador no estaba desplegado, y el disparo sintético del evento tampoco
abre la tarjeta YA EXISTENTE de la tabla —o sea que es límite de la prueba, no
del cambio—.

Sin migraciones ni variables nuevas. Versión 0.200.0.

### v0.199.0 — «Productos más vendidos»: un renglón por SKU, y el top ya no pierde productos

Pedido de Eduardo: en la lista **General** que se muestre *«nada más por SKU de
ambas cuentas, y si está pausada en una y encendida en otra, que se vea como en
la tabla general»*. `TEC-2162-NEG` salía dos veces —2º y 4º lugar— y
`TEC-0778-NEG` también, una BK PAUSADA y otra SC ACTIVA.

**Y buscándolo apareció algo peor.** La lista General se armaba **en el
navegador**, fundiendo los dos top-10 por cuenta y reordenando. Eso no solo
repetía SKUs: **perdía productos**. Un SKU con 80 piezas en cada cuenta no entra
al top-10 de ninguna por separado, y sumado sí es de los más vendidos. Un top que
sale de fundir dos listas ya recortadas no es el top.

**Medido en el clon de producción, la versión vieja dejaba fuera 455 unidades**
repartidas en 8 de los 10 SKUs del top real, y a uno lo hacía **invisible por
completo**:

| SKU | uds reales | lo que sumaban las pestañas | perdidas |
|---|---|---|---|
| `TEC-1606-NEG` | 164 | **0** | 164 |
| `TEC-0778-NEG` | 243 | 173 | 70 |
| `VAR-0456-NEG` | 160 | 90 | 70 |
| `TEC-0551-PLU` | 211 | 150 | 61 |
| `TEC-0982-ROS` | 158 | 108 | 50 |

`TEC-1606-NEG` es el 8º producto más vendido y no aparecía en ninguna parte.

**El ranking se muda al backend.** Un CTE `g` numera por SKU sumando las cuentas,
y la consulta trae las filas por cuenta de los dos conjuntos: el top de cada
cuenta (para sus pestañas) y todas las del top general (para poder fundirlas con
su envío y sus visitas, que se miden por publicación y por cuenta).

**Una sola función arma las dos vistas.** `armar(grupo)` recibe las filas por
cuenta de un mismo SKU: una para las pestañas, las dos para General. Si cada
vista hiciera su aritmética, el mismo SKU acabaría con dos márgenes distintos
según dónde se mire. Todo se **re-pondera sobre los crudos**: por eso la consulta
ahora devuelve `comision_total` y `uds_com` además del promedio — promediar
promedios da un número que no es de nadie. El precio promedio sale de
`ingreso ÷ unidades` del total, y el envío real de
`suma de cobros ÷ unidades cubiertas`.

**El estado entre cuentas** usa la misma regla que ya usaba el CTE `est` dentro
de una cuenta: **si en alguna está activa, el producto se puede comprar**, y eso
es lo que describe la etiqueta. Los precios de publicación se toman de una cuenta
donde esté activa — el precio de una pausada no es el que ve el comprador.

Las **visitas** se suman entre cuentas con la regla de todo-o-nada endurecida:
falta la medición de UNA publicación de cualquiera de las dos y el renglón se
queda sin conversión, que es lo correcto.

**Verificado contra el clon de producción:** 10 renglones, **0 duplicados**;
`TEC-2165-NEG-2PZ` con estados `{activa, pausada}` resuelve a **ACTIVA**; y el
cuadre unidad por unidad de los SKUs visibles en las dos pestañas (uds, ingreso,
precio promedio y estado) da 2 de 2 sin diferencias. En pantalla, cada renglón
lleva sus chips `BK`/`SC` y una sola etiqueta de estado.

Sin migraciones ni variables nuevas. Versión 0.199.0.

### v0.188.0 — El cron de deltas-orders llevaba tres días retirado en el papel y vivo en Railway

El chequeo diario de arneses encontró actas de `orders-deltas` del 13, 14 y
15-ago en `migration.reconciliation_runs`. Ese dominio está en
`_DOMINIOS_RETIRADOS` desde el 12-ago, y el comentario que lo declara dice, con
todas sus letras, que los retirados *«no generan acta»*.

Generaba acta todos los días. Y no un aviso de retiro: la comparación completa.
Los logs de Railway del 15-ago lo muestran de punta a punta — arranca 07:17,
`Pasada 1: mysql_fríos=15115 supabase=16734`, espera 75 s para reconfirmar 1,511
sospechosos, y a las 07:19 escribe `Acta registrada → resultado: con_deltas`.

**Se había retirado dos veces y ninguna sirvió.** El 12-ago (v0.107.0) se le
puso un aviso de retiro en `railway-deltas.json`; el 13-ago (v0.135.0) otro, ya
en `railway.deltas-orders.json`. El servicio hasta muestra ese aviso como su
`startCommand`. Pero **un cron de Railway re-ejecuta el último deployment
EXITOSO**, y el de este servicio seguía siendo uno del **29-jul** (`913f205`),
anterior a los dos retiros. Editar el config file cambia lo que correría en el
*próximo* deployment; mientras no haya deployment nuevo, el cron repite el
binario viejo, con el `startCommand` viejo adentro.

Por qué los otros dos sí murieron, y no por donde se creía: `deltas-costos` se
quedó **sin `cronSchedule`** (su `startCommand` sigue siendo
`python scripts/comparar_costos.py`, el script de verdad) y `deltas-channel`
tiene su deployment en **`SKIPPED`**, así que no hay nada que re-ejecutar. O
sea que ninguno de los tres se detuvo por el aviso de retiro que se les escribió.

Retirado de verdad quitándole el `cronSchedule` por API — que **sí** toma efecto
sin deployment, al revés de lo que decía CLAUDE.md — y borrándolo también de
`railway.deltas-orders.json` para que un rebuild futuro no lo resucite. No se
forzó un redeploy a propósito: reconstruir justo este servicio es lo que el
24-jul lo levantó como un segundo backend rogue.

**Lo que se estaba midiendo mientras tanto:** nada útil, y cada día peor. La
comparación mira `pedidos_ml`, congelada a propósito desde el 13-ago, contra
`channel.orders`, que sigue viva. El lado congelado quedó clavado en 15,115 y el
vivo pasó de 15,940 a 16,734 en un solo día. Los 400 divergentes que reportó son
casi todos el mismo dibujo (`estado_wc processing vs completed`): pedidos que
avanzaron en kubera y que MySQL ya no puede enterarse. La brecha crece sola —
justamente la alarma que suena todos los días y enseña a ignorarla.

No timbró en Slack porque `_DOMINIOS_RETIRADOS` lo excluye del vigilante de
ausencias. Estaba escribiendo en una tabla de producción sin que nadie lo viera.

CLAUDE.md corregido con lo medido: cómo se retira un cron de verdad, por qué el
`startCommand` no alcanza, y que `railway-deltas.json` quedó huérfano (el
`configFile` del servicio apunta a `railway.deltas-orders.json` desde el
13-ago). La regla que queda: **un cron está muerto cuando deja de aparecer su
efecto, no cuando su `startCommand` dice que está retirado.**

### v0.186.0 — El renglón de Inmovilizado dice que es una FAMILIA, y dónde están las piezas

Eduardo, viendo la hoja: *«¿por qué aparece CAM-0030? el SKU es padre y no va a
vender, los que van a vender son los hijos»*.

El dato estaba bien y el reporte también: ese renglón **no es el SKU padre, es
la familia entera**, y desde el 7-ago suma tanto el stock como las ventas de los
hijos. `CAM-0030` es un colchón comprimido con 4 variantes; sus 230 piezas en
FULL están todas en `CAM-0030-IND` (150 en Sancor, 80 en Bekura), y **ni el
padre ni una sola variante ha vendido jamás una pieza**. Está donde debe estar.

**Lo que estaba mal era lo que el renglón DECÍA.** Mostraba un código de SKU
padre para una fila que significa "familia". Quien lo revisa busca las ventas de
`CAM-0030`, no encuentra ninguna —nunca las va a haber, un padre con variantes
no se vende— y concluye que el reporte miente. Un reporte que obliga a
consultar la base para creerle no sirve.

Ahora el renglón lo declara. En el Excel, una columna nueva **«Dónde está el
stock»**:

```
CAM-0030   Colchon comprimido.   CAM-0030-IND · Sancor · 150 | CAM-0030-IND · Bekura · 80
```

y el diagnóstico cierra con *«la cuenta cubre al padre y a sus 4 variantes»*,
para que «nunca ha vendido» se lea como lo que es: de la familia completa, no
solo del padre. En la vista previa del panel, una segunda línea bajo el SKU:

```
CAM-0030  Colchon comprimido.              230 en FULL · nunca vendió
  ↳ familia de 4 variantes — el stock está en CAM-0030-IND (SANCORFASHION 150) · CAM-0030-IND (BEKURA 80)
```

**Detalles que costaron una iteración.** El CTE `pub` colapsa cada publicación
duplicada a una fila, y hay que elegir con qué SKU nombrarla: se **prefiere el
hijo**, porque decir `CAM-0030` donde las piezas son de `CAM-0030-IND` es
volver a mandar al lector a buscar ventas de un código que nunca las tendrá.
Y la columna solo aparece cuando el stock vive en un SKU **distinto** del que
nombra el renglón: en un producto simple publicado en dos cuentas repetiría el
código de la columna 1 y el reparto que ya dan «FULL Bekura» y «FULL Sancor».
Con ese filtro son 23 renglones de 222 los que la llevan — exactamente las 23
familias.

**Verificado contra el clon de producción:** el Excel se arma completo (40,020
bytes, tres hojas, 13 columnas) y la vista previa renderiza las tres familias
del top con su desglose. Los totales no se mueven: esto solo agrega
explicación, no cambia ningún número.

Sin migraciones ni variables nuevas. Versión 0.186.0.
### v0.198.0 — Walmart deja de ser una maqueta: 235 artículos y sus 75 categorías

Piezas 1 y 2. La pestaña Walmart mostraba datos de EJEMPLO encima de una cuenta
con 237 artículos reales. Ahora lee `channel.listings`, como TikTok y Temu.

**El cliente** (`services/walmart.py`, solo lectura). Autenticación en dos pasos:
Basic `client_id:secret` → `POST /v3/token` → el token viaja en
`WM_SEC.ACCESS_TOKEN`. Las otras tres cabeceras (`WM_SVC.NAME`,
`WM_QOS.CORRELATION_ID`, `WM_MARKET: mx`) son obligatorias en TODAS las llamadas
y sin ellas la API contesta 400 sin decir cuál falta. Se pagina con `offset`.

**El censo**: 237 declarados, 237 traídos, 2 de prueba omitidos
(`PRUEBA-IMG-*`), **235 reales**.

| Estado | Artículos |
|---|---|
| PUBLISHED | 207 |
| UNPUBLISHED | 27 |
| SYSTEM_PROBLEM | 1 |

**Lo bueno de este canal:** el estado es una PALABRA. En Temu hubo que
decodificar siete números cruzando totales del Seller Center; aquí el panel
puede afirmar qué está publicado.

**⚠️ LA TRAMPA DE WALMART: dos taxonomías que no se tocan.** El canal usa dos
clasificaciones distintas y **no comparten un solo valor** (medido: 100
`productType` contra 76 categorías del esquema, **0 en común**):

| | Qué es | Ejemplos | Dónde vive |
|---|---|---|---|
| `productType` | lo que Walmart ASIGNÓ tras publicar; la hoja fina | "Licuadoras", "Abanicos de Mano" | `listings.category_id` |
| categoría del esquema | la que decide **qué campos exige** | "Electrónicos", "Juguetes" | `field_requirements.categoria_id` |

Cruzarlas devuelve **cero filas sin dar error** — el mismo defecto que en Amazon
(`product_type` vs `category_id`). Por eso `walmart_panel.categoria_esquema()`
no adivina: resuelve con los MISMOS patrones que usa el publicador
(`CATEGORIAS_AUTORIZADAS`), para que el semáforo y el alta no puedan
contradecirse.

**Y lo que el censo destapó:** **105 de 235 artículos están en «Por Defecto»**.
Eso no es una categoría, es el hueco — Walmart no supo dónde ponerlos. Casi la
mitad del catálogo está sin categorizar.

**Las 75 categorías** entran a `channel.categories` desde la MISMA fuente que
indexa los requisitos, y con su disponibilidad real:

| Disponibilidad | Categorías |
|---|---|
| **AUTORIZADA** (exención probada) | **3** — Disfraces · Cocina, Decoración y Otros · Electrónicos |
| POR_CONFIRMAR | 6 |
| SIN_EXENCION | 66 |

Esa columna importa: publicar en una categoría sin exención de UPC es un lote
que rebota. El selector puede ofrecer las 75, pero solo 3 están probadas.

---

### v0.197.0 — Walmart: 3,331 reglas de campo, y las 5 correcciones que el CSV no llevaba

Cierra el pendiente #7 de `docs/WALMART_ENTREGA_A_OMNICANAL.md`: los atributos
por categoría de Walmart MX entran a `channel.field_requirements`, que es de
donde el panel pinta el semáforo y de donde la IA saca las listas cerradas.

| Canal | Reglas | Obligatorios | Categorías |
|---|---|---|---|
| Amazon | 64,125 | 3,354 | 553 |
| **Walmart** | **3,331** | **446** | **76** |
| Mercado Libre | 2,765 | 2,765 | 1,059 |
| Temu | 2,086 | 546 | 145 |
| TikTok | 1,779 | 1,767 | 760 |

**Walmart no tiene API de atributos** — es la diferencia con sus hermanos. En
TikTok se piden con `GET /categories/{id}/attributes`; aquí ese endpoint no
existe (`POST /v3/items/spec` da 404 con credenciales MX y en Global está
marcado "US only"). La fuente es el esquema público `MX_MP_ITEM_INTL_SPEC.json`
(3.9 MB, se descarga sin credenciales — verificado: HTTP 200, 3,958,699 bytes).
Por eso las filas quedan con `fuente='manual'`: no es una API que se
reconsulte en caliente, es un archivo con versión, y llamarlo `api` invitaría a
creer que se refresca solo.

**LO QUE ESTE COMMIT AGREGA A LA ENTREGA.** `CORRECCIONES_MEDIDAS` declara
**nueve** correcciones de producción y el CSV solo llevaba **cuatro**. Las cinco
que faltaban son campos que el esquema 3.19 **no lista** para esa categoría, así
que no había fila donde colgar el veredicto — y son justo las más caras, porque
cuatro son `RECHAZADO`:

| Categoría | Campo | Veredicto | Evidencia |
|---|---|---|---|
| Electrónicos | `modelNumber` | RECHAZADO | *85 de 85 muertos, 7-ago* |
| Accesorios Electrónicos | `modelNumber` | RECHAZADO | *5 feeds de 85* |
| Almacenamiento | `gender` | RECHAZADO | *83 de 83 muertos* |
| Juguetes | `countPerPack` | RECHAZADO | *33 de 33 muertos* |
| Otros Electrónicos | `wattage` | OBLIGATORIO | *sonda 7-ago* |

Son campos que **matan el lote entero** si se mandan. Dejarlos fuera de la tabla
era perder lo único que no se puede deducir del archivo: lo medido contra
producción. Ahora entran como filas propias, marcadas `solo_medicion: true` y
con su evidencia, para que quien las lea entienda por qué existen.

**`(todas)` se traduce a `'*'`**, la convención que ya usan los otros canales
para "aplica a cualquier categoría"; sin eso el semáforo no encontraría los 24
campos comunes. Y el veredicto de producción viaja dentro de
`valores_permitidos` junto a la etiqueta en español, la lista cerrada y los
límites — todo lo que la IA necesita para elegir sin inventar.

---

### v0.196.0 — Walmart: los 445 atributos obligatorios, y el contenido que llevamos mal en los 221 publicados

**El hueco.** Temu y TikTok ya piden los atributos obligatorios de cada
categoría y llenan la ficha con IA. Walmart no: el publicador manda un bloque
fijo, y hasta ahora lo único que se había hecho era **quitar** los campos que
tumbaban el lote. Nadie estaba **llenando** los que Walmart pide.

**De dónde salen los obligatorios, sin API.** En TikTok es
`GET /categories/{id}/attributes`. En Walmart MX esa API **no existe**:
`POST /v3/items/spec` da 404 con credenciales MX y en Global está marcada
"US only" — no llega ni migrando. La fuente es el esquema público
`MX_MP_ITEM_INTL_SPEC.json` (3.9 MB, HTTP 200 sin credenciales).
`scripts/walmart_field_requirements.py` lo convierte en tabla:

| | |
|---|---|
| Categorías | **75** |
| Filas (campo × categoría) | **3,326** |
| **Obligatorios** | **445** — 24 comunes + **421 por categoría** |
| Con lista cerrada | **1,334** |

Sale con el esquema de `channel.field_requirements`, y con dos columnas que no
tiene ningún otro canal: `veredicto_produccion` y `evidencia_produccion`.
Existen porque **el archivo es la 3.19 y producción corre la 3.11**, y donde
chocan manda lo medido. Las 9 correcciones conocidas (`modelNumber` rechazado
donde el archivo lo lista, `productLine` y `activity` obligatorios donde el
archivo dice que no…) viven en `CORRECCIONES_MEDIDAS` y cada una costó un lote.

**Lo que estábamos dejando sobre la mesa:** el publicador manda 6 campos en
Electrónicos de los 27 que la categoría admite; en "Cocina, Decoración y Otros"
manda 10 de 81. Son **68 atributos opcionales sin usar** en una sola categoría,
y `offerScore`/`contentScore` de la Listing Quality API miden justamente eso.

**El contenido que ya publicamos está mal, y se puede medir.** Sobre los 244
payloads de Electrónicos del 7-ago:

| Hallazgo | Cuántos |
|---|---|
| `keyFeatures` con UNA viñeta, copia literal del título | **244 de 244 (100%)** |
| Título fuera del rango 50–75 | 80 |
| Título con MAYÚSCULAS sostenidas | 25 |
| `brand` = `"Ferrahome"` por default | **244 de 244** |
| **Frases penalizadas VIVAS en Walmart** | **5** |

Lo de `keyFeatures` es lo más caro y lo más fácil: el propio esquema dice, en la
descripción del campo, *"Recomendamos encarecidamente utilizar mínimo tres"* y
**"Deben de ser diferentes al Título del Producto y no repetirse en la
Descripción"**. Mandamos exactamente lo contrario, 244 veces. Se corrige con
`MP_MAINTENANCE`, sin republicar. Los 5 con frase penalizada —`garantizada`,
`lo mejor` ×2, `la mejor`, `sin fallas`— son del tipo que puede **inactivar** el
producto.

**`services/walmart_contenido.py`** trae los dos prompts (contenido y atributos)
y, sobre todo, los validadores. **27 pruebas, 27 pasan.** El prompt pide; el
código garantiza:

- Título 50–75 (regla de negocio) con tope duro **200**, que sale de
  `productName.maxLength` y **cierra el pendiente #7 del manual**, donde el 200
  figuraba como `[SUPUESTO]` heredado sin fuente.
- Beneficios 3–8, ≤50 caracteres, **distintos del título y ausentes de la
  descripción** — la regla literal del esquema que hoy violan los 244.
- Todo valor de lista cerrada se comprueba contra la lista real de esa
  categoría; lo que no cuadra no se publica.
- Un campo con veto medido se descarta **antes** de mirar el esquema, para que
  el motivo que quede en la bitácora sea la evidencia de producción y no un
  "no existe" genérico.
- `[FALTA DATO]` ⇒ el campo no se publica. Un hueco es mejor que una mentira:
  en Walmart un dato inventado no da error, publica.

**Dos comprobaciones que existen por un caso real.** La primera: las frases
penalizadas se buscan **con límite de palabra**. Por subcadena, `"cura"` caza
dentro de *manicura*, *pedicura* y *oscuras*, y `"la mejor"` dentro de *"mejora
tu visibilidad"* — medido sobre los 244, la versión ingenua marcaba 8 artículos
y **3 eran falsos positivos**. La segunda: **el título propuesto tiene que
compartir alguna palabra con el original**. Un título puede quedar impecable de
forma y describir otro producto; es el mismo modo de fallo que en TikTok mandó
un cono veterinario a *Joyas para disfraces*, con confianza y sin error.

Entregable completo en
[docs/WALMART_ENTREGA_A_OMNICANAL.md](docs/WALMART_ENTREGA_A_OMNICANAL.md),
hermano de los de Temu y TikTok. **No se mandó ningún feed**: los 147 feeds
analizados son históricos y todo se leyó con `GET`.

### v0.185.0 — Inmovilizado contaba dos veces las piezas de un producto con variantes

Eduardo pidió investigar la hoja de **Inmovilizado** sospechando que hubiera
SKUs padre que no venden *a propósito*. Ese hueco ya estaba cerrado; buscándolo
apareció otro, y este sí estaba inflando el número.

**Lo que se revisó primero (y salió limpio).** El arreglo del 7-ago —*la
publicación vive en el padre, las ventas llegan en el hijo*— sigue haciendo su
trabajo. Hoy 23 de los 222 inmovilizados son SKUs padre. Para cada uno se
buscaron ventas de CUALQUIER SKU de su familia, enlazado o no, incluso por
prefijo de texto: **cero en los 23**. Ni el padre ni un solo hijo ha vendido
nunca una pieza. Están muertos de verdad, no mal clasificados.

Contexto del catálogo, de paso: de las 1,486 familias con variantes, **1,408
(95%) no han vendido nada jamás**, ni el padre ni ningún hijo.

**El defecto que sí había.** `channel.listings` guarda una fila por SKU, pero en
un producto con variantes el padre y el hijo comparten el **mismo
`listing_id`**: son la misma publicación de Mercado Libre vista desde dos SKUs.
Como el CTE `lst` cuelga a los dos de la misma clave, `sum(stock_full)` sumaba
las dos filas y contaba las mismas piezas dos veces.

Los casos no dejan lugar a dudas:

| familia | publicación | filas | reportaba | real |
|---|---|---|---|---|
| `DEC-0014` | MLM3136223689 | `DEC-0014` 200 + `DEC-0014-BLN` 200 | 400 | **200** |
| `JUGU-0268` | MLM5741078908 | 199 + 199 | 398 | **199** |
| `HERR-0035` | MLM3097569251 | 191 + 191 | 382 | **191** |
| `CUNA-0052` | MLM3136277755 | 90 + 90 | 180 | **90** |

Cuando los dos números no empatan exacto (`TEC-0794`: 89 y 90) es la misma
publicación leída por el sync en momentos distintos, no dos existencias.

**El arreglo.** Un CTE `pub` colapsa a **una fila por publicación real**
—`coalesce(listing_id, 'sku:'||sku)` como clave— y toma `max` del stock, por el
mismo motivo por el que `prop` ya usaba `max` para el stock propio: las dos
filas son la misma pieza. `s` pasa a contar sobre `pub` en vez de sobre `lst`;
`prop` se queda con `lst`, porque el stock propio se resuelve por variante y ya
tiene su propia regla. Los conteos de publicaciones (`pubs`, `activas`,
`pausadas`) también salen de `pub`, así que una publicación duplicada deja de
contar como dos.

**Medido.** En todo el FULL de Mercado Libre: 40 publicaciones duplicadas,
**2,024 unidades fantasma de 34,766 (5.8%)**. En la hoja de Inmovilizado, 13 de
los 222 SKUs traían el stock inflado: **9,436 → 8,914 unidades**.

**Verificado corriendo el SQL real del archivo contra el clon de producción,
antes y después:** las mismas 222 filas —**ningún SKU entra ni sale de la
hoja**, solo se corrige el conteo—, 209 de 222 sin un solo cambio, y ninguna
otra columna se movió. La hoja de Invisible queda idéntica (104 SKUs, 8,029
unidades, cero correcciones): sus poblaciones son disjuntas por construcción,
una exige ventas y la otra exige que no las haya.

Sin migraciones ni variables nuevas. Versión 0.185.0.

### v0.184.0 — Los tres canales sugieren categoría al abrir su pestaña

Brandon, mirando el Estudio: *"tiktok y amazon no sugieren categoría primero"*.
Tenía razón, y por dos motivos distintos.

**Amazon no sugería porque nadie podía pedírselo.** `amazon_ia.sugerir_tipo`
existía desde v0.137.0 y **ningún endpoint la llamaba** — el mismo patrón que ya
había aparecido con la IA de Temu al crear productos: función escrita, función
huérfana. El picker solo sabía buscar, así que había que adivinar el término
—en inglés, porque la Definitions API indexa así— o dejar que el detector
automático eligiera AL PUBLICAR, que es el peor momento para enterarse de que
eligió mal. Es como *"Contadora y Clasificadora de Monedas"* acabó en `HOME`.

Ahora hay `GET /api/publicar/amazon/tipo/sugerido` y el picker lo pide solo.
Medido con el producto de prueba: *"set de plastilina ligera 48 colores"* →
`TOYS_AND_GAMES`.

**TikTok sí sugería, y ese era el problema.** Lo pedía en silencio al montar el
componente, así que si la API de TikTok tardaba —tarda— la pantalla se veía
igual que si no existiera recomendador. Sigue siendo automático, pero ya no es
invisible.

**Temu lo hacía solo con un botón.** Si hay que apretar algo para ver la
sugerencia, en la práctica nadie la ve y el producto se publica con lo que haya.
Ahora se pide al presionar la pestaña, y el botón queda como *"Sugerir otra"*.

**La regla que queda pareja en los tres:** proponer es del panel, **decidir es
de la persona**. La sugerencia nunca se guarda sola — hay que aceptarla
("Usar esta categoría" / "Usar este tipo"). Guardarla sola la volvería
indistinguible de una elección humana, y toda la precedencia del panel se apoya
en esa diferencia (regla 2 de la casa).

**Y la categoría de WooCommerce ya solo se ve en General.** En un marketplace
confundía: cada canal tiene su propia taxonomía —y en Temu la categoría además
DECIDE qué atributos existen—, así que verla al lado de la del canal invitaba a
creer que alguna de las dos se envía. Ninguna sale de ahí: la de Woo es de la
tienda web.

---

### v0.183.0 — Publicar en Temu desde el panel: cuatro defectos que solo salían en pantalla

Prueba en vivo pedida por Brandon: abrir el panel, apretar *Mejorar con IA* y
*Publicar a Temu* con `JUGU-0053-MUL`. Salió publicado —`goodsId
607904216261491`— pero **el camino tenía cuatro defectos que ninguna prueba de
backend habría visto**, porque cada uno vivía en la costura entre dos piezas.

**1. Temu decía "PRONTO".** `core/marketplaces.py` lo marcaba
`habilitado: False` con la leyenda *"pendiente de credenciales"* — cuando las
credenciales llevaban horas en Railway y el canal ya tenía 352 publicaciones.
La pestaña estaba deshabilitada encima de un canal vivo.

**2. El semáforo reventaba en Temu.** `channel_content` usaba un **NUL
literal** (`chr(0)`) como centinela de "sin categoría", y **psycopg2 lo rechaza
en un parámetro**: *"A string literal cannot contain NUL (0x00) characters"*. El
semáforo se apagaba entero y el panel no decía por qué. Ahora el centinela es
`__sin_categoria__`; el NUL parecía el "vacío perfecto" y es justo el que no se
puede mandar.

**3. Faltaba la rama de Temu** en `_categoria_del_canal`, que devolvía `None` y
activaba el centinela anterior. Con ella, panel > publicación, igual que TikTok.

**4. Un 422 al publicar, y el panel solo decía "No se pudo generar la vista
previa".** El generador de Temu produce los atributos en el formato del canal
—`{name, value: [...]}`, que es lo que pide `v3.add`— y el modelo del panel
exige `{nombre, valor}`, el idioma de ML, Amazon y TikTok. El mensaje en
pantalla no nombraba ni el canal ni el campo; el error real se leyó
interceptando la petición del navegador:

```json
{"loc":["body","campos","atributos",0,"nombre"],"msg":"Field required",
 "input":{"name":"Grupo de edad aplicable","value":["3 años+"]}}
```

`AtributoIn` acepta ahora las dos formas y normaliza; y `publicar_temu` traduce
DE VUELTA al formato de Temu antes de armar el payload, sin confiar en quién
llamó — mandar `{nombre, valor}` a `v3.add` no da error: **v3 ignora en
silencio** lo que no reconoce y el producto se publica sin atributos.

**El selector de categoría de Temu** (`CategoriaTemuPicker`) cierra la paridad
con Amazon y TikTok: dice cuál tiene y de dónde sale, la sugiere (Temu propone
candidatas y la IA elige, **con permiso de decir que ninguna encaja**) y deja
cambiarla — solo HOJAS, porque las intermedias no tienen plantilla. Se guarda en
`channel.product_category` con `source='panel'` y MANDA sobre el recomendador.
Para el producto de prueba eligió *Arte, Manualidades y Costura > Modelismo >
Sets de herramientas para pasatiempos*, descartando las de jardín, pesca y
juguete.

**Cinco códigos de estado más, decodificados.** Brandon leyó los totales del
Seller Center (Activo/Inactivo 182 · Incompleto 172 · Borrador 14) y se cruzaron
contra el censo por código: `5/None` = 14 y `2/8+3/2+3/3` = 172, exactos. Eso
convierte tres códigos en "Incompleto" y deja el resto como vendible. Ojo: los
conteos se MUEVEN mientras Temu procesa un lote (entre dos censos con minutos de
diferencia, `2/8` pasó de 129 a 133), así que el cruce vale por la suma.

**Y un hallazgo del censo: 4 SKUs están publicados DOS veces** en Temu
(`ACC-0160-AZL`, `TEC-1509-MET-300W`, `MUE-0364-BLN/DOR`, `MUE-0293-AZL`). Dos
publicaciones del mismo SKU compiten entre sí y parten el inventario; y
`ACC-0160-AZL` es además uno de los dos con precio a 100×.

---

### v0.179.0 — Temu: la categoría se elige (y el alta ya pasa por su IA)

Auditoría en vivo con `ORG-0261-NEG` — un soporte de pared para casco de moto —
y salieron **tres huecos reales**, los tres cerrados aquí.

**1. El alta NO pasaba por la IA de Temu.** `temu_ia.generar_para_alta` existía
desde v0.168.0 y **nadie la llamaba**: `crear_producto` solo invocaba Amazon y
TikTok. Ya está conectada, con el mismo matiz de TikTok y más marcado — sin
publicación no hay hoja, y en Temu la hoja DETERMINA qué atributos existen, así
que del alta sale el texto y los atributos entran cuando el SKU tenga categoría.

**2. No se podía elegir categoría, así que un producto NUEVO no se podía
publicar.** La vista previa lo rechazaba con "Sin categoría de Temu", que era
correcto pero sin salida. Ahora `temu_panel` trae buscador (solo HOJAS: las
intermedias no tienen plantilla), guardado en `channel.product_category` —donde
ya viven las 5,166 elecciones humanas de ML— y **recomendador**: Temu propone
candidatas con `category.recommend` y la IA elige entre ellas **con permiso de
decir que ninguna sirve**. Esa salida es la que lo vuelve portero en vez de
adivino.

Medido con el producto de prueba: Temu propuso 5 candidatas y la IA eligió
*Automotriz > Motocicletas > Equipo de protección > Accesorios para casco >
**Hardware para casco***, con su razón. La elección del panel MANDA sobre
cualquier recomendador (regla 2 de la casa).

**3. Las medidas de relleno pasaban en silencio.** El producto trae 1×1×1 cm y
0.1 kg en Woo — eso no es una medida chiquita, es un hueco. Y **Temu cobra
volumétrico**, así que ese hueco se convierte en un cobro equivocado sin que
nada dé error. Ahora la vista previa lo advierte.

**Lo que la auditoría confirmó que SÍ funciona**, contra la API real:
`out.sn.check` dice que el SKU está libre; la cadena categoría → plantilla →
IA → validación cierra en 2 llamadas; y el validador **rechaza un valor
inventado** ("Kryptonita" → *no existe en esta categoría*) mientras acepta
"Hierro", que sí está entre los 71 valores de esa hoja.

---

### v0.175.0 — La venta de Temu se vuelve pedido (y el sobre cifrado se abre)

Pieza 6. El receptor de `/api/webhooks/temu` deja la fase de observación:
descifra, verifica la firma y encola el pedido.

**El presupuesto es 6× más estricto que el de ML.** Temu da **500 ms** antes de
contar la entrega como fallida (reintentos 2m, 10m, 30m, 1h ×3, 12h ×2 y luego
abandona el mensaje). Así que el receptor solo descifra, verifica y acusa
recibo; traer la orden y escribir el pedido van a `BackgroundTasks`. Trabajar
antes de contestar es exactamente lo que tumbó el panel el 13-ago.

**El sobre**: `{"eventData": "<base64>"}` con AES-128-CBC/PKCS5, donde la clave
**y** el IV son los primeros 16 bytes del `app_secret`. Se descifra PRIMERO
porque la firma se calcula sobre el texto ya en claro.

**La firma**: HMAC-SHA256 sobre las cabeceras `x-tm-*` más el `eventData`
descifrado, ordenadas y concatenadas clave+valor **sin separadores**. El ejemplo
de la doc de Temu firma con `clave=valor&` y no reproduce ni sus propios
ejemplos: copiarlo habría rechazado el 100% de los eventos legítimos. Y como de
los dos ejemplos oficiales uno lleva `x-tm-ext-param` y el otro no, se calculan
**las dos variantes** y basta con que una cuadre. Probado de ida y vuelta:
cifrado→descifrado idéntico, las dos variantes válidas, una firma falsa
rechazada.

**Lo que Temu NO da, y queda dicho.** Medido contra las dos órdenes reales:
`bg.order.detail.v2.get` devuelve `productList[].extCode` (nuestro SKU),
`quantity` y `orderStatus`, pero **ni un campo de dinero** — `paymentInfo` viene
en `null`, y las dos APIs de importes están bloqueadas con `3000032`. Así que el
pedido se crea con el precio de CATÁLOGO y eso queda registrado. Inventar un
precio verosímil habría contaminado el análisis de márgenes sin dar error.

**El enum de `orderStatus` no está documentado**, así que el mapa tiene UN código
(`4` → processing, que es lo único con evidencia: las dos ventas reales) y todo
lo demás **se registra sin crear pedido**. El modo de fallo importa: un código
desconocido que no crea pedido se arregla reprocesando; uno que sí lo crea
descuenta stock de una venta que quizá se canceló.

Nace apagado (`PEDIDOS_TEMU_ENABLED`).

---

### v0.174.0 — Publicar en Temu desde el panel, sin quemar el SKU

Pieza 5. `services/publicar_temu.py` con el par `preview()` / `confirmar()`, como
TikTok. El pipeline por tandas del otro chat (`scripts/publicar_temu.py`) sigue
para lotes; esto es el otro camino: un producto, un botón, y el payload a la
vista antes de mandarlo.

**El payload es el de ellos a propósito** — ya está verificado contra altas
reales (`ACC-0017-MUL` → `608007295444247`) y trae las cinco decisiones que
costó medir: `extCatName` y no `catId` (v3 ignora en silencio lo que no
conoce: se pidió 1761 y publicó en 1769), `basePrice` en decimal de 2 cifras
(en centavos es lo que dejó dos productos a 100×), `packageInfo` aunque la doc
lo dé por opcional (sin él Temu asume 100 g y 10×20×30 y cobra volumétrico),
`variations` por NOMBRE (Temu acuña el `specId` solo, sin `spec.id.get`), y
`attributes` como texto validado por `vid`.

**Los dos candados contra el quemado.** Un fallo DESPUÉS de que Temu acepta el
alta quema el `externalSkuId` para siempre y borrar no lo libera:

1. `out.sn.check` antes de mandar nada. Si el SKU ya existe se ABORTA y se dice
   su `goodsId`. La vista previa también lo advierte, antes de que nadie apriete.
2. El payload se registra en la bitácora ANTES de salir, no después.

**Y un defecto propio corregido gracias a su entrega:** `services/temu.py` daba
por buena cualquier respuesta con `success: true` de primer nivel. Su medición
mostró que hay endpoints —`goods.delete` es el caso— que contestan bien por
fuera y traen el veredicto REAL anidado en `result.success` ("data under review
cannot be deleted"). Ahora se lee el de adentro: un cambio que no ocurrió ya no
se reporta como hecho.

Además: "Mejorar con IA" ya despacha a `temu_ia` (`ia_generadores`), y el
Estudio habilita Temu (`esTemu`) para que el botón exista.

---

### v0.175.0 — Las medidas salen de la tarjeta y se vuelven columnas

Pedido de Eduardo: el chip de tamaño **vuelve a ser una etiqueta muda** y las
medidas que solo se veían pasando el cursor pasan a ser **cuatro columnas
propias** de la tabla de Análisis: Largo, Ancho, Alto y Peso.

**Por qué el cambio.** La tarjeta resolvía "¿por qué este SKU es M?" para UN
renglón a la vez, y solo si sabías que había algo debajo del cursor. Las
preguntas reales del catálogo son de barrido —cuáles son los bultos grandes,
a cuáles les falta medida, cuáles traen un peso imposible— y esas no se
contestan renglón por renglón. En columna se contestan ordenando.

**Lo que se conserva de la tarjeta**, porque era información y no adorno:

- **El lado que decide la letra** va resaltado en el renglón (semibold, más
  oscuro) con su `title`. Sin eso, un SKU de 29 cm y otro de 31 caen en
  categorías distintas sin que se vea por qué — que era justo lo que la
  tarjeta explicaba.
- **El aviso de densidad.** Arriba de 1.5 kg/L el peso se pinta en ÁMBAR con
  la cuenta en el `title`: es el peso de la CAJA master capturado como si fuera
  el de una pieza, defecto conocido de ~536 SKUs que infla el envío estimado.
  Al pasar las medidas a columnas, esta era la única señal que se habría
  perdido. Ahora además se puede **ordenar por Peso** para irlos sacando.
- Los cortes de la letra (30 / 60 / 120 cm) y el "sin medidas no hay flete
  calculable" se mudan a la ayuda `?` del encabezado de Largo.

Un SKU sin medida sale con guion en gris, no con cero: cero es una medida y
"no la capturaron" no lo es.

**Backend.** Cuatro claves nuevas en `_ORDEN` (`largo`, `ancho`, `alto`,
`peso`). Las columnas ya venían en el CTE `filas` desde que existía el chip, así
que la consulta no cambia; solo se permite ordenar por ellas. El `nulls last`
que ya estaba puesto aplica igual: un SKU sin medida no es "el más chico", es
uno del que no sabemos, y va al final se ordene como se ordene.

La tabla pasa de 13 a 17 columnas y ya vivía en `overflow-x-auto`, así que el
ancho extra hace scroll horizontal en vez de apretar las demás.

Sin migraciones ni variables nuevas. Versión 0.175.0.

---

### v0.173.0 — Paso 4 cerrado: la caché de imágenes lee de kubera, y `?fuente=ml` deja de mentir

**1. El aviso de la vista detenida.** `GET /api/ventas/resumen?fuente=ml` sirve
una caché de la API de Mercado Libre que, con `VENTAS_ML_REFRESH=false`, **ya no
se refresca**: `asegurar_dia` regresa sin pedir nada y la respuesta entrega la
última foto que quedó. Nada en el JSON lo decía, y el campo `actualizado`
empeoraba la confusión — traía la hora de ESA petición, así que números de hace
días se presentaban con la hora de hace un segundo.

Es la misma familia de todo lo que costó dinero en esta migración: una fuente
detenida que contesta con seguridad lo que ya no sabe. No se retira la vista
—eso es decisión de producto—: se la deja honesta. La respuesta ahora trae

    "fuente": "ml",
    "aviso": {"tipo": "cache_detenida", "motivo": "VENTAS_ML_REFRESH=false",
              "texto": "…", "dias_sin_datos_frescos": N},
    "datos_hasta": "2026-08-13T22:18:15"

`datos_hasta` es la escritura más nueva **en el rango pedido**, no de toda la
tabla: un rango viejo puede estar completo aunque la caché lleve días quieta.
`fuente: "ml"` se agregó por simetría — `resumen_pedidos` ya lo traía y el
frontend ya lo lee. El panel **nunca** pide `fuente=ml` (`ventasHorario()` no
manda el parámetro), así que esta vista es solo por API y ahí va el aviso.

**2. El lector de imágenes** (`SUPABASE_READ_MEDIA`, nuevo):
`imagenes_amazon._cache_get` puede leer de `enrich.product_media` en vez de
`amazon_imagenes`. **Paridad verificada URL por URL: 678 de 678, cero sin
respuesta y cero distintas.**

La sutileza está en la llave. En MySQL la PK es el SHA-1 de la URL, **única
global**: una imagen usada por dos SKUs es UNA fila. En kubera el índice único es
`(sku, kind, source_url)` y da DOS. Por eso la gemela **no filtra por SKU** — la
pregunta es "¿ya procesé ESTA URL?", no "¿para este SKU?". Filtrar por SKU haría
reprocesar la misma imagen una vez por producto, y reprocesar no es gratis ni
idempotente: baja la imagen, WebP→JPEG, escala a ≥1000 px, a veces Real-ESRGAN, y
**sube otra copia a WordPress con otro `wp_media_id`**.

**El `except → None` de ese lector se conserva a propósito**, y quedó escrito por
qué: aquí un fallo significa "no sé si ya la procesé" y equivocarse cuesta
reprocesar — caro, no incorrecto. Es el mismo patrón que en los candados del
paso 0 produce un movimiento de inventario duplicado. **La diferencia no es el
patrón: es qué pasa cuando la respuesta está mal.** Distinguirlos es lo que
evita tanto el bug como la sobrecorrección.

**Un error mío, con su daño medido.** Probando el aviso corrí `ventas_ml.resumen`
en local, donde `ventas_ml_refresh` **default es True** (en Railway es false), y
la función refrescó de verdad: escribió en `ventas_horarias`/`ventas_sync` de
producción y gastó llamadas a ML. Alcance: **4 filas** de `ventas_sync` (11 y
12-ago, ambas cuentas) re-selladas y sus buckets horarios reescritos con datos
reales de la API; `ventas_horarias` sigue en 2,016 filas — reemplazó, no agregó.
Sin efecto de negocio: el panel no lee esa caché. La última escritura ORGÁNICA
sigue siendo la del 13-ago 22:18.

La lección, que vale más que el incidente: **llamar una función de servicio en
local puede ESCRIBIR en producción**, porque el `.env` apunta ahí y los defaults
de `config.py` no son los de Railway. Probar contra producción se hace con
`SELECT`, o forzando la variable al valor real (`VENTAS_ML_REFRESH=false python …`),
que es como se hizo la segunda vez.

Sin migraciones. `SUPABASE_READ_MEDIA` nace apagada. Versión 0.173.0.

### v0.175.0 — Paso 0: migración y pruebas en sandbox, y el consejo encuentra un bloqueante

Nada de esto está conectado: `candados_read.py` existe pero **ningún candado lo
usa todavía**. La migración `0022` está aplicada **solo en sandbox**.

**Lo construido y probado en sandbox:**

- `0022_candados_fanout.sql` — `channel.orders.stock_compensado_at`,
  `ops.fulfillment_operations`, `ops.fba_watermark`.
- `candados_read.py` — las tres gemelas. **Ninguna tiene `except`**, y eso es el
  punto entero del paso.
- `migrar_candados_paso0.py` — copia los 23 estados y las 99 marcas con sus
  fechas reales. Corrido en sandbox: 6 + 17 + 99, todo verificado.
- `probar_candados_paso0.py` — **las 6 pruebas pasan**. T2 (kubera caída →
  propaga) y T4 (el candado nuevo ni busca ni recrea la bitácora) son las que
  hoy fallarían con el código viejo.

**Y el consejo (opus, sonnet, haiku) encontró algo que bloquea el plan.**

Verificado por mi cuenta en `pedidos_ml.py`, la secuencia real es:

    1. lee channel.orders (ancla de idempotencia)   ~436
    2. CREA el pedido en Woo                        ~477
    3. `_ya_compensado(wc_id)` — en la CONDICIÓN de un `if`, fuera de todo `try`   ~510
    4. ESCRIBE channel.orders                       ~583

Si el candado propaga en el paso 3, la excepción sube **con el pedido ya creado
en Woo y sin registrar**. ML reintenta, el paso 1 no encuentra previo, y **crea
un segundo pedido**. O sea: propagar ingenuamente **reproduce el patrón de los
964 fantasma que dice prevenir**.

El arreglo no es "no propagar": es **persistir `channel.orders` ANTES de
compensar**. Entonces el reintento es idempotente a nivel pedido y la
compensación puede fallar sin duplicar nada.

**Un agujero que ya existe hoy, sin tocar nada.** La escritura a `channel.orders`
(~583) está envuelta en `except Exception: log.warning` (~596). Si falla, el
pedido queda en Woo pero no en el registro, y el siguiente aviso crea el
duplicado. **El ancla anti-duplicado no es el candado: es el orden de
operaciones más la durabilidad de ese registro.** Busqué el mensaje en los logs
de Railway retenidos y no aparece — el agujero es real en el código, pero no
tengo evidencia de que se haya disparado en la ventana que los logs conservan.

**Otras tres correcciones que acepto:**

1. **La compensación no es un booleano.** Escribe tres valores distintos
   (`full_compensado`, `_error`, `_revertido`) y `_ya_compensado` solo mira el
   primero: una compensación PARCIAL cuenta como hecha y sus líneas fallidas no
   reintentan nunca, y tras una reversión el candado sigue diciendo `True`. Una
   columna `bool` copiaría ese bug con checksum. Necesita estado tri-valor.
2. **La marca de agua se actualiza al OBSERVAR, no al aplicar.** Si se atara a
   "operación aplicada", en modo solo-registro nunca avanzaría, y el día que
   quiten el solo-registro el vigilante vería todo como nuevo. El script de
   migración ya toma la última fila `fba_%` sea `sim`, aplicada o error — lo que
   faltaba era decirlo.
3. **Quitar el `CREATE TABLE IF NOT EXISTS` es PASO 5, no PASO 0.** Lo llaman
   CINCO sitios; quitarlo antes de repuntar los 8 escritores los deja
   escribiendo en una tabla zombi. Va después de repuntarlos y antes del DROP,
   nunca junto con ninguno de los dos.

**Y una crítica a mi propia evidencia que también acepto.** Sostuve que la marca
de agua no es `channel.listings.stock_fba` con "96 de 99 difieren". Opus tiene
razón en que ese número es casi una tautología —dos relojes distintos marcan
horas distintas— y no prueba nada. La conclusión sigue siendo correcta, pero el
argumento bueno es el MECANISMO: **el sync pisa la referencia entre pasadas**, que
es el mismo defecto que ya se arregló en `stock_watch._foto()` en el paso 2.

**La doble lectura de días se cambia por otra cosa.** Con 23 filas casi estáticas
prueba poco. El riesgo real de la compensación está en el camino de ESCRITURA,
así que va doble-escritura + comparación one-shot + la matriz T1-T6.

Informes completos en `agents/counselors/1786735774-revisin-paso-0-*`.
Versión 0.175.0.

### v0.177.0 — Paso 0 se rebasa sobre el RECLAMO, y el reclamo le cambia el diseño

Rebasado sobre `da3f720` (v0.176.0, el reclamo). Sigue sin conectarse nada:
`0022` está **solo en sandbox** y ningún candado usa el código nuevo todavía.

**El reclamo cierra la ventana que el consejo marcó como bloqueante — pero no
todo el problema.** Con el reclamo, si `_ya_compensado` propaga después de crear
en Woo, ML reintenta, el reintento pierde el reclamo, espera 4 s y **le pregunta
a Woo**, encuentra el pedido y lo adopta. No hay duplicado. El ancla dejó de ser
el registro y pasó a ser **la fila del reclamo, que se inserta ANTES de tocar
Woo**.

Lo que el reclamo NO cubre y sigue haciendo falta: con la compensación
propagando, `_registrar()` nunca corre, así que la venta **no queda en kubera**
hasta que un reintento lo logre — y cada reintento paga los 4 s de espera. El
arreglo sigue siendo **mover el registro antes de la compensación**. Los dos
cambios son complementarios, no alternativos.

**Y el reclamo obliga a cambiar mi diseño en un punto concreto.** Desde
v0.176.0, `wc_order_id` es **NULL a propósito** mientras el pedido está reclamado
y aún no creado — es la señal de "reclamado, sin pedido". Buscar la compensación
por esa columna fallaría **justo en los casos revueltos**: el relevo de
contenedores de un deploy y el reintento de ML.

    ya_compensado(canal, cuenta, external_order_id)   ← la PK, nunca nula
    ya_compensado(wc_id)                              ← lo que iba a hacer

De paso desaparece el índice `ix_orders_wc_order_id` que la migración creaba: no
hace falta y habría sido el índice equivocado.

**Dos columnas, no una** (lo marcó opus y tiene razón). La compensación escribe
TRES acciones distintas —`full_compensado`, `_error`, `_revertido`— y el candado
viejo solo mira la primera: **tras una reversión seguiría diciendo "ya
compensado" para siempre**. Ahora hay `stock_compensado_at` y
`stock_revertido_at`, y compensado vale solo si es posterior a la última
reversión. La prueba nueva lo cubre: revertir → deja de contar; compensar de
nuevo → vuelve a contar.

**Una deuda que se hereda y se dice en voz alta:** una compensación PARCIAL
(unas líneas devueltas, otras con error) cuenta como hecha, así que las líneas
fallidas no se reintentan nunca. Ese defecto **ya existe** en `fanout_log`;
mudarlo de casa no lo cura, y curarlo aquí sería cambiar el comportamiento del
flujo de pedidos dentro de un paso que es de mudanza. Queda como pendiente
propio, escrito en la migración.

**Sandbox: 8/8**, incluidos los dos casos nuevos de reversión. T2 (kubera caída →
propaga) y T4 (el candado nuevo ni busca ni recrea la bitácora) siguen siendo las
que hoy fallarían con el código viejo.

**Choque de versión, de paso:** tres commits distintos salieron como v0.175.0
(el mío, Temu y las medidas). Esta entrada renumera solo la mía hacia adelante;
las otras dos se quedan como están.

Sin migraciones en producción y sin variables nuevas. Versión 0.177.0.

### v0.178.0 — El registro de la venta va antes de la compensación

Cambio de UNA cosa: en `pedidos_ml._sincronizar_serializado`, el bloque que
escribe `channel.orders` (y su espejo) se mueve **antes** de la compensación
FULL/FBA. Nada más cambia — verificado, es un movimiento puro: los dos bloques
quedan byte a byte idénticos y el resto del archivo intacto; la diferencia son
las 16 líneas del comentario nuevo.

**Por qué.** Con el orden viejo, si la compensación se caía —o si su candado
PROPAGABA, que es justo lo que el paso 0 viene a hacer— la excepción subía con
el pedido **ya creado en Woo** y la venta **sin registrar en kubera**.

El RECLAMO de v0.176.0 evita que eso se convierta en duplicado: el reintento
pierde el reclamo, le pregunta a Woo y adopta el pedido que ya existía. Pero no
evita lo otro — la venta se queda sin registro hasta que un reintento lo consiga,
y cada reintento paga los 4 s de espera del perdedor. **Los dos arreglos son
complementarios, no alternativos**, y este es la mitad que faltaba.

Registrar primero pone el registro a salvo de todo lo que venga después: la
compensación ahora puede fallar RUIDOSAMENTE sin arrastrar la venta. Que es la
precondición para que el paso 0 pueda quitar el `except: return False` sin
recrear el desastre que dice prevenir.

**Es seguro por construcción**, y por eso el movimiento se pudo hacer sin tocar
una línea: nada del registro depende de la compensación (`encabezado` sale de
`orden`/`payload`/`comision`/`skus`, todos calculados antes de crear en Woo) y la
compensación no lee nada que el registro produzca. `protegido` se define dentro
del bloque de compensación y solo se usa ahí — verificado.

Sin migraciones ni variables nuevas. Versión 0.178.0.

### v0.180.0 — El espejo deja de perder filas EN SILENCIO

`kubera_mirror.espejar()` encola con `put_nowait`. Si la cola está llena
(`_COLA_MAX = 500` por worker) el evento **se descarta** — eso ya era así y es
una decisión razonable: el espejo nunca debe frenar al flujo real. Lo que no era
razonable es lo que pasaba después: el descarte se anotaba **solo en el ring de
500 eventos EN MEMORIA**, así que moría con el siguiente reinicio y nunca llegaba
a `espejo_kubera_log` —la tabla que sobrevive y la que alimenta el reproceso de
/migracion—.

**El único camino del espejo que PIERDE datos era también el único que no se
podía ni ver ni reintentar.**

Se descubrió midiendo el PASO 4: faltaban **156 filas de `amazon_imagenes` del 4
al 13-ago**, con el espejo encendido y la tabla en `KUBERA_MIRROR_TABLAS`, y el
log de errores **vacío**. No hay prueba de que esas 156 se fueran por aquí —el
ring ya se había perdido en algún reinicio— pero el canal de pérdida silenciosa
sí quedó probado, leyendo el código.

Ahora un descarte también se persiste, **con su payload**, que es lo que permite
reprocesarlo desde /migracion como cualquier otro error del espejo.

**Dos detalles que no son cosméticos:**

- **Va en un hilo.** `_persistir_error` escribe en MySQL y `espejar()` promete no
  bloquear a quien la llama —que a veces es una corrutina, regla 11—. Mismo
  patrón que el aviso a Slack de `alertas.py`.
- **ACOTADO a 4 rescates concurrentes.** La cola se llena justamente en ráfaga:
  sin tope serían cientos de hilos a la vez. Si ni eso alcanza, el evento se
  pierde igual — pero se pierde **a gritos** (`log.error` con la clave y la
  tabla), que es lo contrario de lo que pasaba antes. Medido: 60 descartes
  seguidos no pasan del tope.

El aviso a Slack no se desborda: `alertas.avisar` ya tiene candado anti-spam por
tipo y cuenta las suprimidas.

**Probado sin tocar ninguna base** (`probar_rescate_cola_llena.py`, 3/3): se
llenan las colas de verdad, se stubea `db.execute` y se verifica que el descarte
deje fila con su payload, que el rescate esté acotado y que `espejar` siga sin
lanzar ni bloquear (0.0 ms).

De la propia prueba salió una lección de método: la primera versión tomaba la
primera escritura capturada y reprobaba — porque las colas se rellenan con
tuplas basura y los workers REALES persisten sus propios errores. Medía su
montaje, no el descarte. Ahora busca la fila del `ColaLlenaError`.

Sin migraciones ni variables nuevas. Versión 0.180.0.

### v0.182.0 — Las tres gemelas de Márgenes devolvían la fecha con zona (y vaciaron una columna)

Defecto del PASO 1 (v0.165.0), reportado desde Análisis: la columna
**«Visitas · CR%» salió vacía** para todas las filas. En los logs, una vez por
minuto:

    [WARNING] omnicanal.fulfillment: visitas no disponibles en la tabla:
              can't compare offset-naive and offset-aware datetimes

**La causa es mía y es de contrato.** `margenes_read` promete en su docstring
que cada función *"devuelve EXACTAMENTE la misma forma que su gemela MySQL"*. No
era cierto: en MySQL `consultado_at` era `DATETIME` → naive; en kubera es
`timestamptz` → psycopg2 lo devuelve **con zona**. Los tres consumidores calculan
su TTL contra `datetime.utcnow()` —naive— y comparar aware con naive **lanza
`TypeError`**. El `try` del llamador se la tragaba: no rompía nada a la vista,
solo desaparecía el dato.

**Los tres estaban afectados; dos fallaban invisible.** Medido, no supuesto:

| gemela | qué pasaba |
|---|---|
| `visitas_ml` | **fallaba y se veía** — un warning por minuto |
| `envio_real` | **no fallaba, por casualidad**: compara detrás de `costo_vendedor is None and …` y hoy hay **0 filas con costo NULL**, así que el `and` corta antes. La primera con NULL lo dispara |
| `ficha_ml` | **fallaba en silencio**: `completar()` corre en un `create_task`, así que su excepción no deja warning y la marca de peso dejaba de converger |

**El arreglo va en el LECTOR, no en las tres comparaciones.** `margenes_read`
normaliza `consultado_at` a UTC sin zona antes de devolverlo, que es lo que hace
cierto el contrato que ya estaba escrito. Parchear cada llamador también
funcionaría, pero dejaría la trampa puesta para **las 28 tablas que faltan del
instructivo**: cada `timestamptz` que se migre traería el mismo defecto.

**La lección de método, que vale más que el bug.** La paridad del paso 1 se
verificó celda por celda —13,735 + 971 + 1,485, **cero diferencias**— y aun así
esto se rompió. Un arnés que compara VALORES no ve un cambio de TIPO. El arnés
nuevo (`probar_gemelas_margenes.py`) mide lo que aquél no podía:

- el tipo (`tzinfo` ausente en las tres),
- **la operación real**: repite el cálculo de TTL de cada consumidor, en vez de
  mirar el tipo y darlo por bueno,
- que normalizar no corrió el instante,
- y fuerza el camino de `envio_real` con costo NULL, para que la prueba no
  dependa del accidente de que hoy no haya ninguno.

Verificado que la prueba TIENE DIENTES: con `_naive_utc` desactivado reproduce
el error exacto de producción. Sin eso, un arnés que pasa no prueba nada.

Gracias a quien lo reportó con el log, la línea y el diagnóstico ya hecho.

Sin migraciones ni variables nuevas. Versión 0.182.0.

### v0.187.0 — Paso 0: las casas nuevas ya existen en producción, y siguen vacías de tráfico

Aplicado a producción `0022_candados_fanout.sql` y copiado el estado. **Nada
encendido**: es la mitad inerte del paso 0, a propósito.

    channel.orders.stock_compensado_at / stock_revertido_at   ← 2 columnas
    ops.fulfillment_operations                                ← tabla nueva
    ops.fba_watermark                                         ← tabla nueva

Copiado y verificado: **6 compensaciones · 17 operaciones · 99 marcas de agua**,
todas con su fecha real preservada (no la de la copia — la lección de las 21,816
filas de `ops.channel_submissions`).

**La verificación que más importa no es que los números cuadren, es que NO
cuadren donde no deben:** 96 de las 99 marcas siguen difiriendo del `stock_fba`
de `channel.listings`. Si fueran 0, significaría que alguien las recalculó desde
el barrido y volvió a meter el doble conteo que `stock_full.py` documenta.

**Por qué «sin encender» aquí es sólido.** No depende de que nadie mueva un
interruptor: **el interruptor no existe**. Verificado línea por línea:

- los candados vivos siguen leyendo `fanout_log` (`pedidos_ml.py:393`,
  `stock_full.py:137` y `:372`),
- **nada llama a `candados_read`** — cero referencias en `services/` y `routers/`,
- **no hay variable en `config.py`** que pudiera activarlo.

Encenderlo requiere un cambio de código deliberado, no un flag que alguien mueva
por error.

**Se sube también para que quede REGISTRADO que `0022` ya está aplicada.** Una
migración aplicada a mano y no anotada en el repo es una invitación a que alguien
la vuelva a correr. El código de este commit es inerte —scripts que nadie llama y
documentación—, pero el hecho que registra no lo es.

Lo que falta del paso 0: conectar los candados detrás de un flag apagado → días
de doble lectura → encender (**toca inventario, regla 3**). Y ya en el paso 5,
quitar el `CREATE TABLE IF NOT EXISTS` y repuntar los 8 escritores.

Sin variables nuevas. Versión 0.187.0.

### v0.189.0 — El latido `padron` medía altas y decía "el cron no corrió"

Encontrado corriendo el chequeo diario de arneses: `vigilar_congelacion.py`
marcaba **`[ALTO] padron — 58.7 h`** con el mensaje *"el ETL de las 06:15 no dio
de alta nada — revisar el cron"*.

**El cron corrió perfecto.** Las actas lo dicen:

    core-etl-v2   ok   2026-08-16 06:17
    core-etl-v2   ok   2026-08-15 06:17

Lo que pasó es que no hubo productos nuevos que dar de alta — el último fue el
14-ago. El latido mide `max(created_at)` de `core.products`, o sea **cuándo entró
el último producto NUEVO**, y lo reportaba como **cuándo corrió el ETL**. Son
cosas distintas: sin altas, el contador crece 1:1 con el reloj y cruza el umbral
solo, sin que nada haya empeorado.

Es la tercera vez que aparece el mismo defecto en este proyecto y ya tiene su
frase escrita dos veces en ese mismo archivo: **un umbral absoluto sobre algo que
crece con el calendario mide el paso del tiempo, no la salud.** Los otros dos
casos fueron `turno_sync` (misma solución) y la métrica de retraso del arnés del
paso 3, que se construyó y se tiró el mismo día.

**Se retira sin reemplazo, y no se pierde vigilancia**: `alertas._revisar_actas`
ya vigila ESE MISMO ETL cada 15 min contra `migration.reconciliation_runs`
—verificado: `core-etl-v2` es el único dominio en su lista de vigilados— y avisa
por Slack *"Acta de Maestro (ETL) NO generada hoy"* si el cron falla. Eso mide lo
que el latido decía medir, y mejor.

**No se borra la línea, se neutraliza** — mismo trato que `turno_sync`. Sigue
mostrando el dato como `[ n/d] padron  último producto nuevo hace N h`, fuera del
veredicto y con la explicación al lado. Borrarla haría que dentro de unos meses
alguien la vuelva a agregar sin saber que ya se descartó y por qué.

Actualizados también el manifiesto de [docs/ARNESES.md](docs/ARNESES.md)
(`alarma_si_contiene` pierde `[ALTO] padron`) y las instrucciones del agente
diario, que tenía ese latido en su tabla como alarma grave.

**Lo que se gana:** los tres arneses quedan **sin falsas alarmas conocidas**.
`costos`, `turno_sync` y ahora `padron` están marcados como benignos, así que a
partir de aquí *si algo suena, es real*. Una alarma que suena todos los días sin
consecuencia no es precaución: entrena a la gente a ignorar el tablero —
exactamente lo que el propio instructivo del chequeo advierte.

Verificado corriéndolo: pasa de `REVISAR lo marcado ALTO` (exit 1) a
`todo late` (exit 0), con los mismos datos y sin tocar nada más.

Sin migraciones ni variables nuevas. Versión 0.189.0.

### v0.190.0 — El barrido de lectores: los tokens de ML son un bloqueador, no una limpieza

Hecha la tarea que el consejo marcó como **requisito antes de borrar cualquier
tabla**. Hasta hoy el barrido solo cubría `backend/services/` y
`backend/routers/`: cada "cero lectores" fuera de ahí era una hipótesis.
Documento completo en [docs/BARRIDO_LECTORES.md](docs/BARRIDO_LECTORES.md).

Método: SQL real (`FROM`/`JOIN`/`INTO`/`UPDATE`/`CREATE TABLE`), no menciones en
prosa. 38 tablas en cuatro superficies.

**🛑 El hallazgo que cambia una prioridad.** `MLREgisterDaily` **lee y ESCRIBE
`ml_tokens`** en el MySQL que se va a retirar:

    SELECT cuenta AS nickname, access_token, refresh_token FROM ml_tokens
    UPDATE ml_tokens SET access_token=%s, refresh_token=%s WHERE cuenta=%s

Es el único escritor de esa tabla, y es el "proceso externo irregular" que
menciona la regla 8. **Si el esquema se retira sin mover esto, los tokens de ML
dejan de renovarse y se cae la integración entera** — webhooks de ventas,
publicación, sync de inventario, competencia. No es un panel que se congela: es
la arteria. Estaba anotado como PASO 6 de limpieza; **sube a bloqueador del
retiro**.

**⚠️ `MonitoreoOperaciones` lee SIETE tablas, no una.** CLAUDE.md decía "lee
`productos`". Medido: `ml_backlog` (su columna vertebral — casi todos sus KPIs y
el export a Excel), `amazon_progress`, `amazon_backlog`, `scraping_alibaba`,
`atributos_ia`, `costos_ml` y `productos`. La decisión pendiente deja de ser
"repuntar una tabla o avisar que se congela": **el panel entero deja de
funcionar**. Y tres de esas tablas son del robot de Alibaba, desconectado desde
el 23-jul, así que esa sección ya muestra historia congelada.

**Los scripts de mantenimiento son 13, no 8.** Aparecieron cinco que no estaban
en la lista: `corregir_stock_amazon`, `actualizar_comision`,
`backfill_dims_validados`, `competencia_analisis` y `reporte_sync_desde_ml`.
Ninguno es flujo vivo —se corren a mano— pero dejarán de funcionar sin aviso.

**✅ El frontend está limpio: cero tablas.** Todo pasa por la API, como debía
ser. `Aplicacion_Excel` y `personal` también (sus aciertos eran la palabra
"productos" en prosa y `pymysql` dentro de un `.venv`).

**Lo que el barrido dice del método, y es lo que más vale.** Las tres veces que
este proyecto midió en vez de suponer, el número cambió: los lectores del grupo 4
eran 25 y no 19; `ml_progress` estaba viva y no congelada; y ahora
`MonitoreoOperaciones` lee 7 y no 1, y los scripts son 13 y no 8. **Ningún conteo
se movió a la baja — la estimación de memoria siempre subestimó.**

Queda anotado lo que NO se barrió: `publicador` y `KuberaPipelineV1.0` (se
retiran con el robot, pero conviene confirmarlo y no asumirlo), `MCPPruebaWOO`, y
el SQL armado dinámicamente, que este método no ve.

Sin cambios de código. Versión 0.190.0.

### v0.191.0 — Paso 6: el verificador que decide si es seguro migrar los tokens de ML

Sin tocar tokens. Lo que se agrega es **la forma de saber si se pueden tocar**.
Plan completo en [docs/PASO_6_TOKENS.md](docs/PASO_6_TOKENS.md).

**El barrido, cerrado.** Son **cuatro consultas**, todas en `meli.py` (`:424` y
`:434` leen; `:486` y `:492` escriben), más `meli.py:246` que lee el token
vigente y `alertas.py:392` que vigila la frescura. Nada más en todo el repo:
lo demás son comentarios y el censo del espejo. `tiktok.py` NO toca estas tablas
—reusa la misma `DB_ENCRYPTION_KEY`, que es otra cosa—.

**Por qué este paso no se parece a los anteriores.** ML **rota el
`refresh_token` en cada uso**: no es un dato que se copia, es una credencial que
se consume. Dos renovadores no divergen — **se invalidan mutuamente y la cuenta
pierde la sesión**. En cambio la doble ESCRITURA sí es segura, y es lo que ya
hace `meli.py`: refresca UNA vez y guarda el resultado en las dos tablas. El
peligro no es escribir en dos lados; es *refrescar* en dos.

Por eso la pregunta a contestar no es *"¿coinciden los datos?"* sino **"¿hay
otro renovador vivo?"**.

**El bloqueo P3 hay que re-medirlo, no heredarlo.** El esquema v4 dejó
`ops.ml_tokens` creada y bloqueada *"hasta acuerdo con el dueño de
`ml_tokens_dashboard` (sistema externo)"*. Ese supuesto es de julio. Hoy:
`MLREgisterDaily` —el candidato obvio— **no se despliega desde el 26-may**, y
las dos tablas tienen el **mismo `updated_at` al segundo** y la **misma huella
de `refresh_token`**: firma de un único escritor, el nuestro. Pero "no observé a
nadie" no es "no hay nadie", que es la lección de toda esta migración.

**`verificar_tokens_ml.py`** (nuevo, solo lectura). **Nunca imprime un token ni
el `client_secret`**: fechas, longitudes y una huella de 8 hex que solo dice si
el valor cambió. Dos señales independientes:

- el desfase entre los `updated_at` de las dos tablas;
- **la huella del `refresh_token`**, que vale más porque no depende del reloj:
  como ML lo rota en cada uso, huellas distintas significan que alguien renovó
  por su cuenta.

Y acumula evidencia entre corridas. **El criterio para desbloquear P3 exige
renovaciones reales**: sin ellas, una racha limpia no prueba nada —nadie
escribió, ni nosotros ni un tercero— y el script lo dice en vez de dar un verde
vacío. Es el mismo error que el "0 avisos" de un panel que nadie había abierto.

Línea base: las dos cuentas con Δ = 0 s y huellas idénticas. Verificado que el
chequeo tiene dientes.

**Dato que habilita el diseño original**: `supabase_vault` está instalado y el
esquema `vault` existe, con **0 secretos**. `ops.ml_tokens` tiene **0 filas**. La
casa está construida y vacía.

**Y una tarea que NO es de migración y va ANTES**: `ml_tokens_dashboard` guarda
el `client_secret` de la app de ML, y ese secreto **está expuesto en el repo
externo `publicador`** (pendiente #9, sin rotar). Moverlo a Vault sin rotarlo lo
cambia de lugar sin dejar de estar comprometido.

Sin migraciones ni variables nuevas. Versión 0.191.0.

### v0.192.0 — El arnés del paso 3 era un detector ciego: el seam SÍ funcionaba

**El seam de publicación lleva dos días funcionando y el arnés decía que estaba
apagado.** Medido: **22 publicaciones en 48 h llegaron a `channel.listings` con
una mediana de 2 segundos** y la peor en 23 s. Antes eso tardaba hasta 15 min.

**Por qué el arnés no lo veía.** Su bloque 3 buscaba el rastro en
`channel.listing_history.detectado_via = 'publicar'`. Pero ese trigger **solo
registra seis columnas** —`price`, `stock_own`, `stock_full`, `situacion`,
`is_fulfillment` y `status`— y el seam, para Mercado Libre, escribe
**`listing_id` y `url`**: ninguna de las dos está en esa lista.

Construí un detector ciego para el caso exacto que venía a detectar, y encima
reportaba "APAGADO" con el flag encendido. Peor que no medir: **medir y mentir**.

**Es el cuarto caso del mismo defecto en este proyecto:**

| | Medía | Se leía como |
|---|---|---|
| `turno_sync` | cuándo cambió el dato | cuándo se revisó |
| `padron` | último producto nuevo | si el ETL corrió |
| la métrica de retraso (tirada el 14-ago) | tiempo hasta el próximo cambio | retraso del seam |
| **bloque 3 del paso 3** | cambios en 6 columnas | **si el seam escribió** |

Los tres primeros salieron midiendo. **Éste salió de una pregunta de Eduardo**
—"¿está igual con los arneses?"— que obligó a contar publicaciones en vez de
confiar en el detector.

**Lo que mide ahora**: el desfase entre la hora de publicación y el `updated_at`
de esa fila en `channel.listings`. Segundos = seam; minutos = lo alcanzó el
sync. Es la misma pregunta, pero contra una fuente que **sí registra lo que el
seam toca**.

**Y un segundo error, cometido y corregido en la misma sesión**: la primera
versión del cálculo juzgaba los 14 días completos y reprobaba con "82 por el
sync" — publicaciones ANTERIORES a que el seam existiera, que obviamente alcanzó
el barrido. Ahora solo juzga lo publicado dentro de `--ventana-seam-h` (48 h) y
dice cuántas ignoró. **Medir bien y juzgar mal da un rojo tan inútil como un
verde vacío.**

Se conserva la vista de vías de `listing_history` como informativa, con su límite
escrito al lado: no puede ver el seam de ML.

**Estado real de los cuatro arneses**, que era la pregunta de fondo:

| Arnés | ¿Tiene eventos que observar? |
|---|---|
| Latido | sí — 787 pedidos en 24 h |
| Paso 2 · foto | sí — 14,639 filas reescritas |
| Paso 3 · seam | **sí, 22 publicaciones** — y el arnés no las veía |
| Paso 6 · tokens | casi no — 2 renovaciones en 24 h; ahí sí falta ventana |

Solo el de tokens estaba realmente esperando eventos. El del seam no esperaba
nada: estaba roto.

Sin migraciones ni variables nuevas. Versión 0.192.0.

### v0.198.0 — El candado de los scripts que le preguntan a una tabla muerta

Cuatro scripts de mantenimiento decidían qué escribir en Woo, ML o Amazon
consultando tablas de MySQL congeladas el 13-ago. **No fallaban: contestaban.**
Es el mecanismo de los 964 pedidos fantasma, con la única diferencia de que a
estos los dispara una persona.

**El candado** (`backend/scripts/_candado_congelado.py`) **mide** la edad real de
la tabla al correr, en vez de llevar la fecha a mano: si mañana se repunta el
script o revive la tabla, se quita solo. Bloquea la **escritura** y deja pasar el
**dry-run** —un dry-run que solo imprime es inofensivo y sirve para entender— y
si no puede medir con `--aplicar`, **aborta igual**: dejar pasar una escritura
porque falló la comprobación sería el mismo defecto que viene a tapar.

| Script | Candado |
|---|---|
| `sync_odoo_woo_seguro` | aborta con `--aplicar` |
| `corregir_status_publicados` | aborta con `--aplicar` |
| `corregir_stock_woo_full` | aborta: superado por `sync_odoo_woo_seguro` |
| `alinear_ml_drop` | `--aplicar` ahora exige `--en-vivo` |

**El peor de los cuatro se degradaba solo.** `sync_odoo_woo_seguro` existe para
NO resucitar mercancía vendida: excluye los SKUs cuyo hueco de stock se explica
por ventas de los últimos 30 días. Pero pregunta por `NOW() - INTERVAL 30 DAY`, y
la ventana avanza con el calendario mientras `pedidos_ml` se quedó en el 13-ago.
Hoy cubre parte. **En un mes daría `ventas = 0` para todo, ninguna exclusión se
dispararía, y se convertiría en el sync ciego que existe para no ser** — sin un
solo error en pantalla.

`corregir_stock_woo_full` resultó estar superado, no roto: empuja Woo = Odoo
absoluto sobre una lista de un archivo del 27-jul que ya no está en el repo.
Su lectura de `canal_inventario` es decorativa (solo se imprime).

`alinear_ml_drop` no necesitaba aborto: **ya traía su propio camino honesto**
(`--en-vivo` le pregunta a ML por cada publicación). El caché era el atajo
barato. Ahora `--aplicar` lo exige.

**Y la lista de "5 congelados" de v0.194.0 estaba mal.** Al abrir los archivos
para trancarlos, dos de los cinco acusados no tenían el defecto:
`actualizar_comision` **lee kubera** y ya escribe por el camino F6;
`backfill_dims_validados` lee MySQL **a propósito** (rescata dims que solo viven
ahí). Y `alinear_ml_drop`, que estaba en el grupo "sano", sí leía congelado. El
error de método: clasifiqué por la tabla del `FROM`, no por si esa lectura
**decide una escritura** — la lección de CLAUDE.md aplicada a medias. Contar
tablas es barato; leer el código es lo que dice quién se equivoca.

**Cuatro scripts NO llevan candado a propósito** (`marcar_amazon_muertas`,
`alinear_amazon_drop`, `publicar_walmart`, `sincronizar_ml_huerfanas`): usan el
caché congelado solo para armar la lista de candidatos y después preguntan en
vivo al canal. Fallan por omisión, no actuando mal. Trancarlos sería ruido.

**`costos_finales` quedó FUERA del mapa del candado** aunque también esté
detenida: sus escrituras legítimas siempre fueron esporádicas (14 filas el 5-ago,
2 el 10-ago, ninguna varios días), así que un umbral de frescura sobre ella
mediría el paso del calendario, no si alguien la escribe. Es la trampa que este
proyecto ya pisó cuatro veces. Solo entran tablas que, vivas, se escribían al
menos cada hora.

**Dos tropiezos del propio candado, conservados en el código porque valen.** La
primera versión abortaba por un `UnicodeEncodeError` de un `─` decorativo: sí
abortó, pero por el crash, no por decisión — una guarda que se cae por un
carácter de adorno no es una guarda. El parche fue un `except UnicodeEncodeError`
que **nunca dispara**, porque la consola de Windows no levanta la excepción:
reemplaza en silencio y el mensaje sale picado (`apag?n`) justo cuando más
importa entenderlo. La versión final no pregunta si la consola aguanta: manda
algo que aguanta cualquiera (translitera NFKD).

Pruebas nuevas: `probar_candado_congelado.py`, **7 checks, todos en verde**,
incluida la de falla cerrada y la de la consola cp1252.

⚠️ **Hallazgo aparte, sin tocar** (apareció midiendo, no lo buscaba):
`HERR-0104-EST` tiene fila de costos en **MySQL** (hoy 07:16) y **ninguna** en
`costing.costos_finales` de kubera, aunque sí está en `core.products`. Es el
único SKU así de 4,377. Cuarenta minutos antes, `EDU-0004-PLA` fue al revés
—kubera sí, MySQL no—, que es lo correcto bajo el corte F6. Mismo día, resultados
opuestos: apunta a una rama condicional en el camino de costos de Crear. **No se
tocó nada**; queda anotado para revisar.

Sin migraciones ni variables nuevas. Versión 0.198.0.

---

### v0.195.0 — Competencia: "Solo top" no filtraba, y un comando cierra una categoría

**El botón "Solo top" contaba bien y no escondía nada.** La condición que
conserva una subcategoría era `s.skus.length > 0 || (sin filtro de SKU ni de
rango)`. Esa segunda mitad existe para que el árbol siga navegable cuando no hay
filtro — pero `soloTop` ES un filtro, y al no contarlo como tal la rama derecha
se cumplía siempre y pasaban las 922 subcategorías. Ahora, con el botón activo,
una subcategoría donde NO estamos en el top desaparece de la lista: de 26 raíces
y 922 subcategorías quedan 1 y 1.

Y lo que deja ver es la línea base del objetivo: de **5,243 posiciones del top
capturadas en 26 categorías, ocupamos exactamente UNA** — TEC-1768-BLN, #12 de
Ganchos para Cortinas. Verificado que no es un fallo del marcado: cruzar nuestras
publicaciones contra los `externo_id` de todos los rankings da esa misma
coincidencia y ninguna más.

**Fuera el selector de orden de SKUs.** El orden dentro de cada subcategoría
queda fijo en "más visitas primero", que ya era el default; las otras tres
opciones no aportaban. El orden de las SUBCATEGORÍAS se conserva — ése es el que
decide dónde mirar.

**`scripts/competencia_cerrar_raiz.py`** encadena los cuatro pasos que se corrían
a mano: prender los SKUs de la raíz, capturar el top de la raíz y de cada
subcategoría por Apify, proponer términos con IA y medirlos. Es REANUDABLE —cada
paso salta lo ya hecho y nada se paga dos veces— y `--pendientes` descubre solo
las raíces con trabajo, ordenadas de menor a mayor para que un fallo se vea en
los primeros minutos y no a las tres horas.

Ese orden se ganó a golpes: lanzado con `nohup … &`, el lote recorrió 20
categorías en 10 minutos sin hacer NADA. `subprocess.run(cmd)` heredaba stdin y
con `nohup` ese descriptor queda cerrado, así que cada Python hijo moría con
«Fatal Python error: init_sys_streams». Lo peor no fue el fallo sino que era
invisible: el script imprimía su resumen con los contadores en cero y seguía.
Ahora va `stdin=DEVNULL` y se registra el código de salida de cada paso.

**Un SKU con comilla perdía su término.** `TEC-1284-NEG-27"` se quedaba sin
término general y no era el prompt: el modelo NORMALIZA el SKU al repetirlo y
devolvía `TEC-1284-NEG-27`. El cruce exacto no lo encontraba y lo descartaba en
silencio. `proponer()` reconcilia por forma normalizada antes de darlo por
perdido. De paso, `_correr_actor` ya dice POR QUÉ falla: "Expecting value: line 1
column 1" era `r.json()` sobre una respuesta HTML de Apify, y el mensaje no
decía nada.

Estado de la captura: 26 categorías raíz con SKUs, 5,243 posiciones de ranking y
922 subcategorías vigiladas. Versión 0.195.0.

---

### v0.194.0 — Los tres pendientes que no eran técnicos, medidos

Solo documentación: ninguna migración, ninguna variable, ningún flujo tocado.
Lo que cambia es que tres decisiones que estaban escritas de memoria ahora
tienen número.

**`MonitoreoOperaciones` SE RETIRA** (decisión de Eduardo). Leía 7 tablas y no
1, pero no se despliega desde el 23-jun. No hay nada que repuntar: se da de
baja. Deja de ser bloqueador del retiro del esquema. Vale avisar antes de
apagarlo que tres de sus tablas son del robot de Alibaba, desconectado desde el
23-jul — quien abriera ese tablero llevaba meses leyendo historia congelada.

**Los 13 scripts de mantenimiento: cinco ya están rotos, no lo estarán.** El
plan decía que dejarían de servir *el día del retiro*. Al cruzar cada script
con la frescura real de la tabla que lee, cinco (`sync_odoo_woo_seguro`,
`actualizar_comision`, `backfill_dims_validados`, `corregir_stock_woo_full`,
`corregir_status_publicados`) leen tablas **congeladas desde el 10 y el
13-ago**. Correrlos hoy no falla: contesta con seguridad lo que era cierto hace
seis días — el patrón de los 964 pedidos fantasma, con la única diferencia de
que a estos los dispara una persona. Propuesta: candado que aborte al arrancar
**antes** de repuntar nada. `marcar_amazon_muertas` es la excepción y el
modelo: ya escribe en los dos lados.

**`odoo_watch` está vivo, y su único producto son avisos.** 5,381 SKUs
vigilados, 76 avisos de campana en 7 días. Su `auto_push` no solo está apagado:
**no se puede encender** —`odoo_watch.py:159` lo bloquea porque manda el valor
ABSOLUTO de Odoo y resucitaría mercancía vendida—. Así que darle casa en kubera
(`ops.odoo_stock_photo`, mismo molde que `ops.stock_watch_photo`) es un trabajo
conocido de ~5 pasos, y la pregunta que decide si vale la pena no es técnica:
quién lee esos avisos y qué hace con ellos.

**`MUN-0023-MUL` resuelto: no era de migración.** La bitácora lo da por
publicado el 6-ago; la respuesta cruda de Amazon guardada en `amazon_backlog`
dice **`ACCEPTED`**, que significa *"recibí tu envío"*, no *"tu publicación está
viva"*. Nuestro publicador lo anota como `PUBLISHED, success=1` en el acto. En
kubera su fila es **el único cascarón totalmente vacío de las 1,791**, y en 10
días el sync nunca lo reportó. El producto existe —vivo en las dos cuentas de
ML y en TikTok—; la publicación de Amazon nunca llegó a existir. No bloquea el
paso 3: la gemela contesta igual que el lector viejo, que es su trabajo. Queda
como pendiente de producto.

Y una nota de método: el intento de confirmarlo preguntándole a Amazon desde
local devolvió `None`, **y no era evidencia** — el `.env` local no tiene
credenciales de SP-API, así que "no existe" y "no pude preguntar" salen
idénticos. Se descartó y la conclusión se sostiene solo en datos.

Sin migraciones ni variables nuevas. Versión 0.194.0.

### v0.193.0 — Paso 3, bloque 1: las gemelas medidas y los 50 `product_type` rescatados

**Nada repuntado todavía.** Los seis sitios siguen leyendo MySQL; lo que se
agrega son las gemelas, su arnés y el dato que faltaba para que la paridad sea
real.

**Se puede empezar porque el seam ya funciona** (medido: 2 s de mediana). Antes,
`ml_progress` era lo único que conocía una publicación recién nacida durante
hasta 15 min, y repuntar habría convertido "publicado hace 30 s" en "sin
publicar". El orden importaba.

**Gemelas nuevas** en `channel_read`: `publicaciones_ml(skus)` y
`estado_amazon(skus)`. Cubren los seis sitios del bloque 1 (`studio.py:108/124`,
`presencia.py:101/119`, `publicar.py:154/259`), que preguntan lo mismo de seis
formas.

**ML: paridad perfecta.** Las 1,979 publicaciones de `ml_progress` existen en
kubera. Los 76 con MLM distinto son republicaciones donde kubera tiene el vivo.

**Amazon: elegí mal la columna y el arnés lo cachó.** La primera gemela leía
"publicado" de `status` y daba **50 falsos negativos**. Medido sobre los 1,791:

    solo `status`        →  50 discrepancias
    solo `situacion`     → 322
    solo `listing_id`    → 278
    **`status` O `situacion`** →   4

Las dos columnas las llenan caminos distintos —`situacion` la trae el sync desde
la API de Amazon, `status` venía de la bitácora del publicador— y **ninguna sola
cubre el catálogo**. Elegir una era elegir mal, y el número lo dijo.

De las 4 que quedaban, **3 son kubera teniendo razón**: MySQL dice PUBLISHED del
28-jul y kubera dice `closed` del 3-ago. La bitácora congela el EVENTO; kubera
refleja el estado vivo. Mismo arbitraje que con los MLM republicados, ahora
escrito en el arnés.

**Y el hueco que sí había que tapar: 50 `product_type` que solo vivían en la
bitácora.** No es un dato decorativo — la **regla 2** define la prioridad al
publicar en Amazon como *panel > histórico `amazon_progress` > detección por
título*, así que sin rescatarlos esos 50 caían a la detección automática, que es
justo lo que publicó una máquina sexual en "Máquinas de Coser".

`rescatar_product_type_amazon.py` **solo rellena, nunca pisa**
(`where product_type is null`): si kubera ya tiene valor, ese vale más porque lo
trajo el sync desde Amazon. Corrido: **50 de 50, cero pisados, cero distintos**.
La paridad de `product_type` pasó de 50 diferencias a **0**.

**Queda 1 caso de 1,791 sin resolver**: `MUN-0023-MUL`, que la bitácora da por
publicado el 6-ago y cuya fila de Amazon en kubera está vacía —sin ASIN, sin
estado—. Amazon nunca lo reportó. No bloquea el repunte: es 1 entre 1,791 y la
dirección es la conservadora (se vería como no publicado, que es lo que el canal
dice).

Sin migraciones ni variables nuevas. Versión 0.193.0.
