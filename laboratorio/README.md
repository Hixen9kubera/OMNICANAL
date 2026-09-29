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
  llamó, nunca se apaga el candado. Qué cubre:
  - **kubera**: el pool de `supabase_db` queda envuelto; toda conexión da un cursor de
    lista blanca y cada sentencia pasa un lector del SQL (una sola SELECT/WITH, sin
    `SET`, `INTO`, `FOR UPDATE/SHARE`, `set_config(…, false)`, `pg_terminate_backend`,
    `lo_*`, `dblink`, `query_to_xml`…) y viaja detrás de
    `SET TRANSACTION READ ONLY; SELECT set_config('statement_timeout', '120s', true);`
    en el MISMO mensaje: Postgres rechaza cualquier escritura aunque el filtro fallara.
    Es **por transacción** (muere con el COMMIT/ROLLBACK), nunca por sesión: la regla 13
    se respeta y `default_transaction_read_only` de la conexión compartida sigue en `off`
    (medido en vivo).
  - **Odoo**: solo `execute_kw` con métodos de lectura, con timeout de socket
    (`LAB_ODOO_TIMEOUT_S`, 180 s), también en el `authenticate`.
  - **HTTP** (httpx, requests, urllib/xmlrpc): solo GET/HEAD/OPTIONS, más POST a
    `api.anthropic.com` y al XML-RPC de Odoo. Un POST/PUT/PATCH/DELETE a Woo, a un
    marketplace, a Slack o al Storage revienta antes de abrir la conexión.
  - **Arranque**: si un candado crítico no se puede instalar (un módulo no importa, una
    función de producción cambió de nombre), el proceso NO arranca.
- **No se mueve ningún precio.** Cada propuesta sale con `autorizacion: "pendiente"`. No
  existe en esta rama código que haga PUT/POST a un marketplace.
- **El token de ML jamás se renueva desde aquí** (`ml_http.get` relee una vez y aborta).
  Renovarlo rota el refresh_token de producción y puede parar las ventas.
- **No se crean ni modifican tablas de Supabase.** Los resultados son archivos en
  `LAB_DATOS_DIR` (en Railway, un volumen en `/data`).
- **La API solo acepta GET/HEAD/OPTIONS**, más dos POST: `/api/lab/sesion` (llave) y
  `/api/lab/recalcular` (dispara el pipeline). Cualquier otro método → 405 antes del ruteo.
- El repo es **PÚBLICO**: `backend/sandbox_precios/datos/` lo excluye
  `backend/sandbox_precios/.gitignore` (versionado: viaja con la rama, así que cubre
  cualquier clon, no solo esta copia) y `/.dockerignore`. Nunca `git add -A` sin revisar;
  antes de un commit, `git ls-files backend/sandbox_precios/datos` debe salir vacío.

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
#    Sin llave está CERRADA (503); para abrirla solo en esta máquina:
$env:LAB_MODO_LOCAL = "1"
& $py -m uvicorn sandbox_precios.api:app --host 127.0.0.1 --port 8010

# 3) Web en modo desarrollo (reenvía /api/lab/* a 127.0.0.1:8010)
cd ..\laboratorio\web
npm ci
npm run dev          # http://localhost:3010
```

Sin `LAB_ACCESS_KEY` la API está **CERRADA** (todo 503) en cualquier host. Con
`LAB_MODO_LOCAL=1` (y solo fuera de Railway) corre en **modo local abierto**, que atiende
únicamente conexiones que entran por loopback y a nombre de `localhost` (`Host` y
`X-Forwarded-Host`: frena el DNS rebinding); `/api/lab/estado` lo avisa (`modo_local: true`).
Para probar la llave en local: `$env:LAB_ACCESS_KEY = "<algo largo>"`.

Con llave, la cookie `lab_sesion` es `v1.<emitida>.<HMAC>`: el servidor verifica la firma
y la edad (14 días), el logout la revoca, y rotar `LAB_ACCESS_KEY` o subir
`LAB_SESION_VERSION` cierra todas. **Freno de intentos**: cada llave mala del formulario y
cada `Bearer` malo cuentan; una cookie inválida cuenta una vez por IP y valor (y se le borra
al navegador, para que una sesión vencida no deje a nadie bloqueado). 10 fallos por IP en
15 min → 429; además un tope GLOBAL de 60 fallos por minuto (IPs falsificadas no multiplican
intentos; mientras alguien insista, el formulario da 429, pero las cookies válidas siguen
entrando). En Railway la IP es `X-Real-IP` o el último salto de `X-Forwarded-For`; fuera,
el socket. Todo POST exige `application/json`, mismo origen y ≤ 4 KB de cuerpo.
`GET /api/lab/sesion` solo mira la COOKIE: un `Bearer` ahí no se evalúa (esa ruta no pasa por
el freno y era un oráculo para adivinar la llave sin límite). El logout solo revoca una cookie
VÁLIDA (con basura se llenaba la lista negra y los logouts legítimos dejaban de revocar). La web
resuelve el `volver` de `/login` con `new URL(…, origin)` y exige el mismo origen
(`/\example.com` y `/%09/example.com` redirigían fuera).

`_entorno` carga del `.env` del repo principal SOLO la lista blanca de abajo y quita del
proceso cualquier credencial de escritura que llegue por otro lado (también en Railway):
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `MELI_CLIENT_SECRET*`, `WC_*`, `AMAZON_*`,
`SLACK_*`, `DB_HOST/USER/PASSWORD/NAME`, `KUBERA_DB_URL`, `APP_ENV`… Si aparece un `.env`,
`.env.amazon` o `env.staging` en la raíz del worktree, no arranca (`config.py` los leería
completos, sin lista blanca).

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
| `LAB_ACCESS_KEY` | una llave aleatoria de ≥16 caracteres | Sin ella (o más corta), en Railway TODO responde 503; solo `/salud` contesta 200 con `cerrado: true`, para que el deploy cerrado SÍ entre y no se quede vivo el anterior. La cookie (`v1.<emitida>.<HMAC>`) caduca a los 14 días verificado en el servidor; rotar la llave cierra todas las sesiones |
| `LAB_SESION_VERSION` | `1` (opcional) | Subirla (`2`, `3`…) cierra todas las sesiones sin cambiar la llave |
| `LAB_DATOS_DIR` | `/data` | Ya viene en la imagen; dejarla explícita documenta el volumen |
| `LAB_AUTO_PIPELINE` | `true` | Corrida diaria dentro del mismo proceso, en un hilo |
| `LAB_PIPELINE_HORA_UTC` | `09:00` | 03:00 CDMX. Lejos del cron de visitas de producción (12:00 UTC; el cliente de ML del laboratorio igual se pausa de **11:50 a 12:30 UTC**), de los ETL de las 06:15 y del barrido de competencia (13:00 UTC días 1 y 16). No ponerla entre 11:50 y 12:30 |
| `LAB_ML_RPS` | `4` (opcional, tope 5) | Freno global a la API de ML: el cupo es por aplicación y se comparte con producción. Un 429 frena a todos los hilos |
| `LAB_ML_PRESUPUESTO_DIA` | `15000` (opcional) | GET a ML por día UTC (una extracción completa ≈ 8,500); al agotarse, la extracción se detiene |
| `LAB_STATEMENT_TIMEOUT` · `LAB_ODOO_TIMEOUT_S` | `120s` · `180` (opcionales) | Tiempo máximo por consulta a kubera (por transacción) y por llamada a Odoo |
| `LAB_RECALCULAR_WEB` | `true` (opcional) | `false` quita el botón «Recalcular» (el pipeline diario sigue) |
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
| `ANTHROPIC_API_KEY` | último peldaño del empate SKU↔renglón (IA por título/foto) | recomendada |
| `SUPABASE_READ_CORE`, `SUPABASE_READ_PUBLICACIONES`, `SUPABASE_READ_CHANNEL` | que `costos`/`meli`/`inventario` lean kubera y no el MySQL congelado | sí (misma paridad que producción) |
| `PL_DRIVE_CARPETA_ID` | carpeta de packing lists en Drive | opcional (hay default) |

**NO referenciar** (y si llegan, `_entorno` las quita del proceso al arrancar y lo dice
en el log, solo con el nombre):

- **`SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY`**. La service role salta RLS en Storage y
  en PostgREST: es escritura total. Los packing lists bajan de **Drive público** —
  `packing_drive_carpeta.bajar` cae solo a Drive cuando el bucket no está a la mano— y
  `PACKING_LEER_STORAGE=true` (forzada) solo sirve para leer el ÍNDICE
  `costing.packing_archivos` por SELECT. Comprobado el 28-sep: los 34 packing lists del
  último `packing100` salieron de los bytes de Drive (misma huella sha256); de los 55 en
  caché, 40 son idénticos byte a byte a la copia del bucket y los otros 15 son hojas
  nativas de Google, que Drive exporta de nuevo en cada bajada. `packing100` corrió
  completo sin la llave (100/100 resueltos).
- `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`: `packing100` solo usa Anthropic, y el candado HTTP
  no deja POST a otro anfitrión.
- `MELI_CLIENT_SECRET`/`MELI_APP_ID` (solo sirven para renovar tokens, y eso está
  prohibido aquí), `WC_*`/`WPDB_*`/`DB_*` (el laboratorio no lee Woo ni MySQL),
  `KUBERA_DB_URL`, `AMAZON_*`, `SLACK_*`, `APP_ENV` (`production` encendería
  comportamientos de `main.py` que aquí no aplican; `staging` haría leer `env.staging`),
  `SUPABASE_WRITE_*` y cualquier `*_ENABLED` de flujos.

`_entorno` además fuerza, pase lo que pase: `MYSQL_ENABLED=false` (el pool MySQL ni se
crea: sus datos son de agosto), `SLACK_WEBHOOK_*=""`, `VENTAS_ML_REFRESH=false`,
`SYNC_ENABLED=false`, `KUBERA_MIRROR_ENABLED=false`, `SUPABASE_WRITE_COSTING=false`,
`TOKENS_SOLO_KUBERA=true` y `PACKING_LEER_STORAGE=true`.

**Lo que el código NO puede cerrar** (necesita el visto bueno de Brandon o Eduardo, porque
toca producción): `SUPABASE_DB_URL` es el rol de producción, con escritura, y las
credenciales de Odoo también escriben. Los candados viven dentro del proceso; alguien con
`railway ssh` y esas variables podría escribir sin ellos. La solución de fondo es un rol
Postgres `lab_lectura` (solo `SELECT` en los esquemas que usa, `ops.ml_tokens` sin la
columna `refresh_token`, `ALTER ROLE … SET default_transaction_read_only = on`, que no
contamina a producción porque el pooler separa por usuario) y un usuario de Odoo de solo
lectura.

### Conexiones a kubera

El pool de `supabase_db` abre hasta **6 conexiones** al pooler en modo transacción (6543),
el mismo que usa producción. El pipeline corre a las 03:00 CDMX justamente para no
competir por él en horario de ventas. Nunca marcar la SESIÓN como read-only (regla 13 de
CLAUDE.md). El candado marca cada TRANSACCIÓN: `SET TRANSACTION READ ONLY` y
`set_config('statement_timeout', …, true)` van en el mismo mensaje que la consulta y
mueren con su COMMIT/ROLLBACK. Van juntos a propósito: SteadyDB repite en una conexión
NUEVA la sentencia que falla por red (fuera de la transacción); si la guardia fuera en una
sentencia aparte, el reintento correría sin ella. Medido en vivo: dentro de la transacción
`transaction_read_only=on` y `statement_timeout` = el del laboratorio; después del COMMIT la
misma conexión vuelve a `off` y a su `statement_timeout` de siempre, y
`default_transaction_read_only` nunca deja de ser `off`.

## Imagen

```bash
docker build -f laboratorio/Dockerfile -t laboratorio-precios .
docker run --rm -p 8010:8080 -e LAB_ACCESS_KEY=... -v lab-datos:/data laboratorio-precios
```

Etapa node (20-slim): `npm ci` + `LAB_EXPORT=1 npm run build` → `out/`. Etapa python
(3.12-slim, la versión de producción): `pip install -r backend/sandbox_precios/requirements.txt`
(= `backend/requirements.txt` + `tzdata`), copia `backend/` (sin `scripts/`, `tests/`,
`routers/`, `main.py`, `mcp_research/`, `vendor/` ni `models/`: escritores de producción que
el laboratorio no importa, verificado importando la copia recortada) y el `out/` a
`/app/web`, y arranca `uvicorn sandbox_precios.api:app --no-proxy-headers
--no-server-header` desde `/app/backend`. Sin `--proxy-headers`: uvicorn tomaba la IP del
salto de más a la izquierda de `X-Forwarded-For`, que escribe el cliente; `api._ip` lee en
Railway `X-Real-IP` (la que Railway documenta para la IP del cliente) o, si falta, el último
salto de `X-Forwarded-For`, el que agrega su proxy. **Al primer deploy**, confirmar en los
logs (`credencial inválida … desde <ip>`) que la IP que aparece es la del cliente y no una
interna de Railway: si fuera interna, todos compartirían cubo y el freno por IP se volvería
global (el tope global de 60/min sigue igual).

La imagen corre como **root** a propósito: el volumen de Railway se monta con dueño root. No
cambia el riesgo de fondo: quien entre por `railway ssh` tiene las variables del servicio con
cualquier usuario; lo que sí se cortó es el código de producción que escribiría sin candados.

## Cómo revertir

Borrar el servicio `laboratorio-precios` (y su volumen) en Railway. No hay nada más que
deshacer: no escribe en ninguna base, no tiene webhooks, no tiene crons fuera de su propio
proceso, y referenciar variables de BackendOmnicanal no modifica al backend. Para
apagarlo sin borrarlo: quitar `LAB_ACCESS_KEY` → el nuevo deploy entra (su `/salud` da 200)
y todo lo demás responde 503; o `LAB_AUTO_PIPELINE=false` para dejar solo la consulta.
Ojo: si un deploy falla el healthcheck, Railway deja vivo el ANTERIOR — por eso `/salud`
no se cae cuando falta la llave.
