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
| POST | `/api/ia/titulo` | Genera título optimizado con Claude |

Documentación interactiva: **`/docs`** (Swagger UI).

---

## 💻 Cómo correr en local

> Requisitos: **Python 3.12+** y **Node 18+**. Las credenciales ya están en
> `.env` y `.env.amazon` en la raíz.

### 1) Backend (FastAPI) — puerto 8000

**PowerShell (Windows):**
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

**bash/macOS/Linux:**
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 2) Frontend (Next.js) — puerto 3000

```bash
cd frontend
npm install
# apunta al backend local (ya viene en .env.local)
npm run dev
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
