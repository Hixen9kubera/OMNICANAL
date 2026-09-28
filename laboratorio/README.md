# Laboratorio de precios

Servicio APARTE del panel (`laboratorio-precios` en Railway) que **propone** precios para
las publicaciones de Mercado Libre FULL y muestra el costo real de cada SKU con el
contenedor de 525,000 MXN prorrateado. Diseño, contratos de datos y algoritmo:
[`backend/sandbox_precios/DISENO.md`](../backend/sandbox_precios/DISENO.md).

| Pieza | Dónde |
|---|---|
| Pipeline (extrae → calcula → `ultimo/*.json`) | `backend/sandbox_precios/pipeline.py` |
| API de solo lectura + web estática + pipeline diario | `backend/sandbox_precios/api.py` |
| Web (Next.js 14, export estático) | `laboratorio/web/` |
| Imagen | `laboratorio/Dockerfile` (contexto = raíz del repo) + `/.dockerignore` |
| Config de Railway | `laboratorio/railway.json` |

## Reglas de solo lectura (no se negocian)

- **SELECT** en kubera, **`search_read`** en Odoo, **GET** a las APIs. `candados.py` hace
  reventar cualquier escritura con `EscrituraProhibida`; si aparece, el bug es de quien
  llamó, nunca se apaga el candado.
- **No se mueve ningún precio.** Cada propuesta sale con `autorizacion: "pendiente"`. No
  existe en esta rama código que haga PUT/POST a un marketplace.
- **El token de ML jamás se renueva desde aquí** (`ml_http.get` relee una vez y aborta).
  Renovarlo rota el refresh_token de producción y puede parar las ventas.
- **No se crean ni modifican tablas de Supabase.** Los resultados son archivos en
  `LAB_DATOS_DIR` (en Railway, un volumen en `/data`).
- **La API solo acepta GET/HEAD/OPTIONS**, más dos POST: `/api/lab/sesion` (llave) y
  `/api/lab/recalcular` (dispara el pipeline). Cualquier otro método → 405 antes del ruteo.
- El repo es **PÚBLICO**: `backend/sandbox_precios/datos/` está en `.git/info/exclude` y
  en `/.dockerignore`. Nunca `git add -A` sin revisar.

## Correr en local

Python del repo principal (el worktree no tiene venv propio) y `cwd = backend/`. Las
credenciales se leen del `.env` del repo principal EN MEMORIA (`_entorno.cargar()`); no se
copia ningún `.env`.

```powershell
$py = "C:\Users\diaz2\OneDrive\Escritorio\omnicanal\backend\.venv\Scripts\python.exe"
cd C:\Users\diaz2\omnicanal-sandbox\backend
$env:PYTHONIOENCODING = "utf-8"

# 1) Pipeline (CLI). Ver `--help` para correr etapas sueltas o sin la API de ML.
& $py -m sandbox_precios.pipeline

# 2) API + web ya exportada en laboratorio/web/out (si existe). NUNCA `uvicorn main:app`.
& $py -m uvicorn sandbox_precios.api:app --port 8010

# 3) Web en modo desarrollo (reenvía /api/lab/* a 127.0.0.1:8010)
cd ..\laboratorio\web
npm ci
npm run dev          # http://localhost:3010
```

Sin `LAB_ACCESS_KEY` la API corre en **modo local abierto** y `/api/lab/estado` lo avisa
(`modo_local: true`). Para probar la llave en local: `$env:LAB_ACCESS_KEY = "<algo largo>"`.

## Qué expone la API (`/api/lab`)

| Ruta | Qué devuelve |
|---|---|
| `GET /salud` | `{"ok": true, ...}` sin llave (healthcheck de Railway) |
| `GET /sesion` · `POST /sesion` | estado de la sesión · `{"llave"}` pone la cookie; `{"salir": true}` la borra |
| `GET /estado` | `estado.json` + `modo_local`, `corriendo`, `snapshots` y `servidor` (pipeline, archivos, horario) |
| `GET /publicaciones` | `canal, cuenta, estado, full, fuente_costo, q, orden, page, per_page` |
| `GET /precios` | `cuenta, estado, razon, confianza, q, orden, page, per_page` |
| `GET /curva/{id}` · `GET /historial/{id}` | curva de la publicación · `historial.json` + un punto por snapshot |
| `GET /packing` · `/contenedores` · `/metricas` · `/parametros` | los JSON del contrato (`/packing` suma `saltados`) |
| `GET /exportar/precios.csv` | mismos filtros que `/precios`, UTF-8 con BOM (Excel) |
| `POST /recalcular` | `{"etapas": [...]\|null, "sin_ml": bool}` → 202 si arranca, 409 si ya corre |

Listas paginadas: `{"generado_at","total","page","per_page","paginas","orden","filas",
"conteos","facetas","sin_datos"}`; `per_page` se TOPA a 1000 (no se rechaza: la web pide
1000 y sigue paginando con lo que le llegue). Los filtros aceptan varios valores separados por coma;
`orden` es un campo (con puntos para anidados: `costo.unitario`, `unidades_dia.actual`) y
`-` para descendente; los `null` van siempre al final. `conteos` trae `canal` y
`canal:cuenta` (para las píldoras) ignorando esos dos filtros; `facetas` cuenta cada
dimensión ignorando su propio filtro. Sin archivo todavía: las listas devuelven
`sin_datos: true` y filas vacías; los documentos, 404 con `sin_datos: true`.

## Despliegue en Railway

Servicio nuevo en el proyecto `Hixen9Proyects`, **conectado a la rama
`sandbox/precios-optimos`** (no a `main`; los servicios de producción siguen desplegando
solo `main`). La rama tiene que estar en GitHub, y el repo es público: se publica el
CÓDIGO del laboratorio, nunca sus datos.

1. *Settings → Source*: repo `OMNICANAL`, rama `sandbox/precios-optimos`, **Root
   Directory vacío** (el contexto de Docker es la raíz: la imagen copia `backend/`).
2. *Settings → Config-as-code*: `/laboratorio/railway.json` (builder DOCKERFILE,
   `dockerfilePath: laboratorio/Dockerfile`, healthcheck `/api/lab/salud`, 1 réplica).
3. *Volumen* montado en **`/data`** (≈1 GB sobra al inicio; los snapshots diarios crecen
   unos MB por día). Sin volumen, cada deploy empieza sin historia de precios.
4. *Networking*: generar un dominio de Railway.
5. Variables (abajo). La primera vez, sin `ultimo/estado.json`, el pipeline corre solo al
   arrancar si `LAB_AUTO_PIPELINE=true`.

### Variables del servicio

**Propias del laboratorio**

| Variable | Valor | Por qué |
|---|---|---|
| `LAB_ACCESS_KEY` | una llave aleatoria de ≥16 caracteres | Sin ella (o más corta), en Railway TODO responde 503; solo `/salud` contesta 200 con `cerrado: true`, para que el deploy cerrado SÍ entre y no se quede vivo el anterior. Rotarla cierra todas las sesiones |
| `LAB_DATOS_DIR` | `/data` | Ya viene en la imagen; dejarla explícita documenta el volumen |
| `LAB_AUTO_PIPELINE` | `true` | Corrida diaria dentro del mismo proceso, en un hilo |
| `LAB_PIPELINE_HORA_UTC` | `09:00` | 03:00 CDMX. Lejos del cron de visitas de producción (12:00 UTC; el cliente de ML del laboratorio igual se pausa de **11:50 a 12:30 UTC**), de los ETL de las 06:15 y del barrido de competencia (13:00 UTC días 1 y 16). No ponerla entre 11:50 y 12:30 |
| `LAB_ML_RPS` | `4` (opcional) | Freno global a la API de ML: el cupo es por aplicación y se comparte con producción |
| `LAB_HISTORIAL_MAX_DIAS` | `400` (opcional) | Días de snapshots que `/historial` mantiene en memoria |

`PORT` y `RAILWAY_ENVIRONMENT` los pone Railway. `RAILWAY_ENVIRONMENT` es lo que hace que
`_entorno` NO busque `.env` y que la API falle cerrada sin llave. `LAB_WEB_DIR=/app/web`
viene en la imagen.

**Referenciadas desde BackendOmnicanal** (`${{BackendOmnicanal.NOMBRE}}`; referenciar no
reinicia ni toca al backend). Es la lista EXACTA de lo que leen los `services.*` que el
laboratorio reusa (`config.py` + `services/supabase_db, meli, tokens_read, odoo,
packing_storage, packing_drive_carpeta, packing_comparador, packing_sku,
packing_publicados, costos, publicaciones_panel, inventario`):

| Variable | Para qué | ¿Obligatoria? |
|---|---|---|
| `SUPABASE_DB_URL` | kubera: todo sale de aquí (SELECT) | sí |
| `DB_ENCRYPTION_KEY` | Fernet para descifrar el access_token de ML en `ops.ml_tokens` | sí (sin ella no hay API de ML) |
| `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD` | `container_numbers` y stock (`search_read`) | sí |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | bajar packing lists del bucket de kubera (`PACKING_LEER_STORAGE` va forzada a `true`) | sí para el costo exacto |
| `ANTHROPIC_API_KEY` | último peldaño del empate SKU↔renglón (IA por título/foto) | recomendada |
| `DEEPSEEK_API_KEY`, `GEMINI_API_KEY` | respaldos de IA de `packing_comparador`/`packing_sku` | opcional (ojo: DeepSeek no está en producción desde el 27-sep) |
| `SUPABASE_READ_CORE`, `SUPABASE_READ_PUBLICACIONES`, `SUPABASE_READ_CHANNEL` | que `costos`/`meli`/`inventario` lean kubera y no el MySQL congelado | sí (misma paridad que producción) |
| `PL_DRIVE_CARPETA_ID` | carpeta de packing lists en Drive | opcional (hay default) |

**Fijar en el servicio** (no referenciar):

| Variable | Valor | Por qué |
|---|---|---|
| `MYSQL_ENABLED` | `false` | El laboratorio no debe leer MySQL: sus datos son de agosto (CLAUDE.md, migración). Si una etapa revienta con "MySQL deshabilitado", es un lector colgado del esquema viejo: se repunta a kubera, no se reconecta |

**NO referenciar**: `MELI_CLIENT_SECRET`/`MELI_APP_ID` (solo sirven para renovar tokens, y
eso está prohibido aquí), `WC_*`/`WPDB_*`/`DB_*` (el laboratorio no lee Woo ni MySQL),
`AMAZON_*`, `APP_ENV` (`production` encendería comportamientos de `main.py` que aquí no
aplican), `SUPABASE_WRITE_*` y cualquier `*_ENABLED` de flujos. `_entorno` además fuerza
`VENTAS_ML_REFRESH=false`, `SYNC_ENABLED=false`, `KUBERA_MIRROR_ENABLED=false`,
`SUPABASE_WRITE_COSTING=false` y `TOKENS_SOLO_KUBERA=true` pase lo que pase.

### Conexiones a kubera

El pool de `supabase_db` abre hasta **6 conexiones** al pooler en modo transacción (6543),
el mismo que usa producción. El pipeline corre a las 03:00 CDMX justamente para no
competir por él en horario de ventas. Nunca marcar la sesión como read-only (regla 13 de
CLAUDE.md): el candado del laboratorio filtra SQL por texto, no toca la sesión.

## Imagen

```bash
docker build -f laboratorio/Dockerfile -t laboratorio-precios .
docker run --rm -p 8010:8080 -e LAB_ACCESS_KEY=... -v lab-datos:/data laboratorio-precios
```

Etapa node (20-slim): `npm ci` + `LAB_EXPORT=1 npm run build` → `out/`. Etapa python
(3.12-slim, la versión de producción): `pip install -r backend/sandbox_precios/requirements.txt`
(= `backend/requirements.txt` + `tzdata`), copia `backend/` y el `out/` a `/app/web`, y
arranca `uvicorn sandbox_precios.api:app --proxy-headers` desde `/app/backend`.

## Cómo revertir

Borrar el servicio `laboratorio-precios` (y su volumen) en Railway. No hay nada más que
deshacer: no escribe en ninguna base, no tiene webhooks, no tiene crons fuera de su propio
proceso, y referenciar variables de BackendOmnicanal no modifica al backend. Para
apagarlo sin borrarlo: quitar `LAB_ACCESS_KEY` → el nuevo deploy entra (su `/salud` da 200)
y todo lo demás responde 503; o `LAB_AUTO_PIPELINE=false` para dejar solo la consulta.
Ojo: si un deploy falla el healthcheck, Railway deja vivo el ANTERIOR — por eso `/salud`
no se cae cuando falta la llave.
