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
