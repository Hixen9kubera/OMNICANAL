# Plan de autenticación y control de acceso — cierre de Temu III.1, III.2, IV.1 y IV.2

> Diseñado el 2026-07-31 tras verificar contra producción que la API responde
> HTTP 200 sin credenciales. Análisis de los 84 endpoints, sus consumidores
> reales y las opciones de autenticación. **Nada de esto está implementado aún.**

---

# PLAN DE IMPLEMENTACIÓN — Cierre de III.1, III.2 y IV.1/IV.2 (cuestionario Temu)

Verificado en el repo antes de escribir esto: `backend/core/seguridad.py` ya tiene `requiere_api_key` con modo observación; `backend/config.py:176-177` ya define `api_key=""` / `auth_enforced=False`; `backend/main.py:84-91` tiene el `allow_origin_regex` abierto a todo `*.railway.app|*.vercel.app` con `allow_credentials=True`; `backend/routers/webhooks.py:79-86` ya tiene `_USER_A_CUENTA = {"3072519654": "BEKURA", "3064478475": "SANCORFASHION"}`; `backend/services/db.py` usa `PooledDB(maxconnections=6)`; `frontend/.env.local` define `NEXT_PUBLIC_API_URL`; hay 20 `fetch()` en `frontend/`.

---

## 1. LA DECISIÓN

**III.1 — Autenticación.** Se implementa **doble vía sobre un único middleware de identidad** en `backend/core/middleware.py`: humanos entran con **Supabase Auth en un proyecto Supabase NUEVO y separado** (JWT Bearer, no el proyecto `tukwcvsi…` de Eduardo) y las máquinas con **API key por identidad** en una tabla nueva `api_keys` de MySQL, reusando y extendiendo `backend/core/seguridad.py::requiere_api_key`; el middleware deja abiertas exactamente tres rutas (`GET /api/health`, `POST /api/webhooks/ml`, `GET /api/webhooks/ml`) declaradas en la constante `RUTAS_ABIERTAS` y sobreescribibles por la variable `AUTH_RUTAS_ABIERTAS` sin redeploy.

**III.2 — RBAC.** Se implementan **tres roles** (`admin`, `operador`, `lectura`) resueltos desde `app_metadata.rol` del JWT de Supabase o desde `api_keys.rol`, y aplicados por una **tabla declarativa de reglas método+prefijo→rol mínimo** en un archivo nuevo `backend/core/rbac.py` (un solo archivo que se le puede enseñar a Temu como evidencia), con flag propio `RBAC_ENFORCED` independiente de `AUTH_ENFORCED`.

**IV.1/IV.2 — Bitácora.** Se implementa una tabla **`auditoria` append-only en el MySQL propio `u531713409_kubera_ml`** (nunca en la BD kubera: regla 4 de CLAUDE.md y rompería las rachas de actas de 14 días), escrita por dos capas —middleware automático para todo POST/PUT/PATCH/DELETE y todo 401/403, más `backend/services/auditoria.py::registrar()` en ~11 puntos de negocio— con **retención de 12 meses aplicada por un job de purga diario en `backend/services/scheduler.py`**, que existe y corre **antes** de que Brandon escriba "12 meses" en el formulario.

---

## 2. PLAN POR FASES

Regla transversal: **cada fase es un deploy independiente, gobernado por su propia variable de Railway, y ninguna fase puede romper la anterior.** Todo lo que enciende/apaga comportamiento vivo (fases 1, 3, 4, 5) requiere el dale de Brandon antes del push (regla 3 de CLAUDE.md). Versión `+0.1` en los dos lugares de `backend/main.py` y entrada en README por fase (regla 9).

---

### FASE 0 — Recortar superficie (sin tocar autenticación)

**Qué se toca.** Lo que hoy regala información y lo que sabotearía cualquier auth futura, pero que no cambia el comportamiento de ningún cliente legítimo.

**Archivos.**
- `backend/main.py` — (a) `FastAPI(..., docs_url=None, redoc_url=None, openapi_url=None)` cuando `settings.app_env == "production"`, gobernado por variable nueva `DOCS_PUBLICAS` (default `false`); (b) eliminar `allow_origin_regex` del `CORSMiddleware` y dejar solo `allow_origins=settings.cors_origins_list`; (c) `GET /api/health` responde `{"status":"ok"}` y el detalle actual (`woocommerce`, `base_datos`, `odoo`, `ambiente`) se muda a `GET /api/health/detalle`; (d) `GET /` deja de publicar `version` y `docs`.
- `backend/config.py` — `docs_publicas: bool = False`.
- `backend/models/schemas.py` — el `response_model=HealthCheck` se mueve al endpoint de detalle.

**Prerrequisito duro (esto es lo que puede romper el panel).** Antes de quitar el regex de CORS hay que obtener el **origen exacto** del FrontendOmnicanal (servicio `3ec32033…`) con `list-domains` de Railway y ponerlo literal en `CORS_ORIGINS`, junto con cualquier dominio custom. Si el dominio queda mal escrito, el panel deja de cargar datos (falla de navegador, no del backend).

**Cómo se prueba que no rompió nada.**
- `curl -s -o /dev/null -w "%{http_code}" https://backendomnicanal-production.up.railway.app/api/health` → `200`, y el deployment de Railway sigue `SUCCESS`/healthy (`railway.json` tiene `healthcheckPath=/api/health` con `restartPolicyType: ON_FAILURE`).
- Abrir el panel y recorrer: `/productos`, `/dashboard`, `/analisis`, `/migracion`, `/ventas`. Consola del navegador sin errores de CORS.
- `curl -X POST .../api/webhooks/ml -d '{"topic":"test","resource":"/x"}'` → `200`.
- Contar filas nuevas en `pedidos_ml` a la hora siguiente (deben seguir entrando ventas).

**Rollback.** `DOCS_PUBLICAS=true` (revierte /docs). Para CORS: volver a agregar el `allow_origin_regex` en un commit de un renglón. Ambos con redeploy de ~2 min.

---

### FASE 1 — Bitácora `auditoria`, desplegada APAGADA

Va primero **a propósito**: no bloquea nada, cierra dos de las cuatro preguntas, y **es el instrumento de censo** que hace segura la Fase 3 (sabrás exactamente quién llama qué antes de exigir token).

**Qué se toca.**
- Tabla nueva `auditoria` en `u531713409_kubera_ml`, creada por `_asegurar_tabla()` con el mismo patrón que `espejo_kubera_log` (`backend/services/kubera_mirror.py:596`). DDL: `id`, `ts DATETIME(3)` UTC, `actor`, `actor_tipo`, `rol`, `accion`, `recurso_tipo`, `recurso_id`, `canal`, `resultado`, `http_status`, `error_texto`, `origen`, `http_metodo`, `http_ruta`, `ip`, `user_agent`, `peticion_id CHAR(36)`, `duracion_ms`, `detalle_json MEDIUMTEXT`; índices `idx_aud_ts`, `idx_aud_actor(actor,ts)`, `idx_aud_accion(accion,ts)`, `idx_aud_recurso(recurso_tipo,recurso_id,ts)`, `idx_aud_resultado(resultado,ts)`.
- **Capa A (automática):** middleware HTTP en `backend/core/middleware.py`, registrado en `backend/main.py` junto al CORS. Genera `peticion_id`, mide `duracion_ms`, lee la IP real de `X-Forwarded-For` (detrás del proxy de Railway `request.client.host` es el proxy), y registra **solo** mutantes (POST/PUT/PATCH/DELETE) y **todo** 401/403. Lista negra de ruido: `/api/health`, `/api/health/detalle`.
- **Capa B (explícita):** `backend/services/auditoria.py::registrar(accion=..., recurso_tipo=..., recurso_id=..., canal=..., detalle={...})`. El `actor` y el `peticion_id` **no se pasan a mano**: viajan en `contextvars.ContextVar` que llena el middleware (y los jobs del scheduler llenan con `sistema:scheduler`).
- **Purga:** job diario en `backend/services/scheduler.py`, `DELETE FROM auditoria WHERE ts < UTC_TIMESTAMP() - INTERVAL %s MONTH LIMIT 5000` en bucle acotado.

**Archivos.** `backend/services/auditoria.py` (nuevo), `backend/core/middleware.py` (nuevo), `backend/core/contexto.py` (nuevo, los ContextVars), `backend/main.py`, `backend/config.py`, `backend/services/scheduler.py`.

**Puntos de inserción de Capa B (los 11, con archivo real):**

| Acción | Archivo |
|---|---|
| `publicacion.crear` | `backend/routers/publicar.py::confirmar` |
| `publicacion.pausar` / `reanudar` | `backend/routers/webhooks.py` (pausar/reanudar) |
| `precio.cambiar` | `backend/routers/crear.py` (`/costos/{sku}/recalcular`, `/costos/bulk`), `backend/services/sync_woo.py` |
| `stock.cambiar` | `backend/services/fanout_stock.py`, `backend/services/inventario.py`, `backend/services/sync_woo.py` |
| `pedido.crear` / `pedido.cancelar` | `backend/services/pedidos_ml.py`, `pedidos_amazon.py`, `pedidos_m2e.py` |
| `config.cambiar` | `backend/services/variables.py` |
| `pii.acceso` | `backend/services/pii.py` (en el descifrado) |
| `media.cambiar` | `backend/routers/imagenes.py` (`/agregar`, `/eliminar`) |
| `auditoria.consulta` | `backend/routers/auditoria.py` (nuevo, GET `/api/auditoria`) |
| `auth.exito` / `auth.fallo` | `backend/core/seguridad.py` (Fase 2) |

**Tres propiedades no negociables del escritor.**
1. **Nunca bloquea:** el INSERT no va en la ruta del request. Cola acotada + workers, el mismo patrón ya probado en `kubera_mirror` v0.15.3. Si la cola se llena, se descarta y se cuenta el descarte.
2. **Falla abierta con red:** si MySQL no responde, se escribe la línea a `stdout` (logs de Railway) como segunda copia. Una venta real nunca se pierde por la auditoría.
3. **Se audita la corrida, no la fila:** el barrido que corrigió 525 SKUs es **1 registro** con `{"afectados":525,"skus_muestra":[...]}`. Desglose por SKU solo si lo originó un humano. Volumen estimado 500–2,000 filas/día.

**Privacidad (para que la bitácora no sea el problema).** `recurso_id` guarda `101154` o el SKU, **nunca** un nombre. `detalle_json` es **lista blanca** de campos (`precio`, `stock`, `estado`, `categoria`, `titulo`, `afectados`) — no se vuelca el body. `_limpiar_detalle()` borra llaves que casen `nombre|name|email|correo|phone|telefono|address|direccion|token|secret|password|key` y trunca. El valor descifrado por `services/pii.py` **jamás** entra al log.

**Regla explícita:** `auditoria` y `api_keys` **NO se agregan a `KUBERA_MIRROR_TABLAS`**. Sumar una tabla a ese CSV es cambio de flujo vivo y además ensuciaría las actas de Eduardo.

**Variables nuevas:** `AUDITORIA_ENABLED=false`, `AUDITORIA_RETENCION_MESES=12`, `AUDITORIA_PURGA_ENABLED=false`, `AUDITORIA_COLA_MAX=2000`.

**Cómo se prueba.**
- Deploy con `AUDITORIA_ENABLED=false`: cero filas, cero cambios de latencia. Comprobar deployment healthy y que el panel funciona igual.
- Encender `AUDITORIA_ENABLED=true` (dale de Brandon) y **medir 48 h**: `SELECT COUNT(*) FROM auditoria WHERE ts > ...` por hora, y vigilar el pool de 6 conexiones — síntoma de problema sería `TooManyConnections` o timeouts en `/api/productos`.
- Verificación de que los flujos vivos siguen: en logs de Railway (backend `96c29d05…`) debe seguir apareciendo `orders_v2 → venta (modo pedidos)` **con** el sufijo `pedido WC #`; `/migracion` debe seguir mostrando la racha de actas intacta; el sync de 15 min debe seguir alimentando `canal_inventario`.
- Encender `AUDITORIA_PURGA_ENABLED=true` con `AUDITORIA_RETENCION_MESES=12` (no borrará nada en un año) y probar la lógica una sola vez en local contra un `auditoria` de prueba con `ts` antiguos. **Nunca probar la purga contra producción.**

**Rollback.** `AUDITORIA_ENABLED=false` → deja de escribir al instante, la tabla queda intacta. La purga se apaga aparte con `AUDITORIA_PURGA_ENABLED=false`. Ningún endpoint cambia de contrato.

---

### FASE 2 — Identidad, en modo OBSERVACIÓN (nadie se bloquea)

**Qué se toca.**

*Backend.*
- `backend/core/seguridad.py` — se extiende con `class Identidad(actor, actor_tipo, rol)` y `async def resolver_identidad(request) -> Identidad`, que acepta **tres formas**: `Authorization: Bearer <JWT de Supabase>` (verificado contra el JWKS/secret del proyecto de auth), `X-API-Key` contra la tabla `api_keys` (hash SHA-256, `secrets.compare_digest`, columna `activa`), o nada → `anonimo`. `requiere_api_key` se conserva tal cual para no romper los 8 endpoints que ya la usan.
- Tabla nueva `api_keys` en MySQL: `id`, `label` (`etl-eduardo`, `cron-deltas`, `brandon-emergencia`), `hash`, `rol`, `activa`, `creada`, `ultimo_uso`. **Una llave por identidad**, no una llave compartida — es lo que hace que `actor` deje de ser `anonimo` y que IV.1 tenga sentido.
- `backend/core/middleware.py` — el mismo middleware de la Fase 1 ahora resuelve identidad, la mete en el ContextVar (por lo que la auditoría empieza a tener `actor` real) y **emite `auth.exito`/`auth.fallo`**. Con `AUTH_ENFORCED=false` **no bloquea**: solo registra qué habría sido 401.
- `backend/routers/auth.py` — se elimina el placeholder que devuelve `admin` fijo; `GET /api/auth/me` devuelve la identidad real del token (o `anonimo`).
- `RUTAS_ABIERTAS = ("/api/health", "/api/health/detalle", "/api/webhooks/ml")` como constante, con override por `AUTH_RUTAS_ABIERTAS` (CSV).

*Frontend — este es el grueso del trabajo y es prerrequisito de la Fase 3.*
- `frontend/lib/supabase.ts` (nuevo) — cliente de Supabase Auth con `NEXT_PUBLIC_SUPABASE_AUTH_URL` y `NEXT_PUBLIC_SUPABASE_AUTH_ANON_KEY` (la anon key es pública por diseño; **no** es una API key secreta, que es justo por qué no se usa `NEXT_PUBLIC_API_KEY`).
- `frontend/app/login/page.tsx` (nuevo) y `frontend/components/SesionGuard.tsx` (nuevo).
- `frontend/lib/api.ts` — `getJSON`/`postJSON` mandan `Authorization: Bearer` y manejan 401 → redirigir a login. **Además hay que arreglar los 4 `fetch` crudos que se saltan los helpers dentro del propio archivo:** `sincronizarDrafts`, `crearProductos`, `refrescarCanal`, `generarIA`.
- **Los 14 `fetch` dispersos fuera de `api.ts`** se centralizan: `frontend/app/analisis/page.tsx` (3), `frontend/app/analisis/categorias/page.tsx` (2), `frontend/app/analisis/estrellas/page.tsx` (1), `frontend/app/analisis/layout.tsx` (1), `frontend/app/dashboard/page.tsx` (2), `frontend/app/migracion/page.tsx` (5).
- **Regresión ya identificada y garantizada:** `frontend/app/migracion/page.tsx:205` llama a `POST /api/migracion/errores/resolver`, que **ya tiene `Depends(requiere_api_key)`**, sin ningún header. Ese botón se rompe con 401 el día del enforcement si no se arregla aquí.

**Dependencia política, no técnica.** La familia `SUPABASE_*` de Railway apunta hoy a la **BD kubera (`tukwcvsi…`), producción operativa de Eduardo**. Se levanta un **proyecto Supabase nuevo, solo para auth** (también gratis, <20 usuarios contra 50k MAU del plan free), con variables de nombre distinto: `SUPABASE_AUTH_URL`, `SUPABASE_AUTH_JWT_SECRET`, `SUPABASE_AUTH_ANON_KEY`. Esto elimina la coordinación con Eduardo y respeta la regla 4 sin negociación.

**Cómo se prueba.**
- Deploy con `API_KEY` definida y `AUTH_ENFORCED=false`: **todo sigue respondiendo 200**, incluido el panel sin login. Probar `/api/productos` sin header → 200 + una fila `auth.fallo` en `auditoria`.
- Login real de Brandon en el panel; `GET /api/auth/me` devuelve `usuario:brandon` con rol.
- **Censo de 5 a 7 días:** `SELECT actor, actor_tipo, http_ruta, COUNT(*) FROM auditoria WHERE accion='auth.fallo' GROUP BY 1,2,3 ORDER BY 4 DESC`. Ese query es el que decide si se puede pasar a la Fase 3. Si aparece un consumidor no documentado (el docstring de `backend/routers/sync.py` menciona un cron hipotético que llamaría `POST /api/sync/leer` — hay que confirmar en la consola de Railway que no exista), se le da su llave en `api_keys` **antes** de apretar.
- Flujos vivos: idénticos a la Fase 1 (webhook, `pedidos_ml`, sync 15 min, actas).

**Rollback.** `AUTH_ENFORCED` ya está en `false`, así que no hay nada que revertir en el backend. Si el login del panel falla, el panel sigue funcionando sin sesión (nada bloquea todavía) — la peor consecuencia es cosmética. Revertir el frontend = redeploy del commit anterior de FrontendOmnicanal.

---

### FASE 3 — Apretar (`AUTH_ENFORCED=true`) — LA FASE PELIGROSA

**Qué se toca.** Una sola variable: `AUTH_ENFORCED=true`. Cero código nuevo, si las fases 0–2 se hicieron bien.

**Precondiciones que deben cumplirse TODAS antes de tocar la variable:**
1. Siete días corridos de censo con **cero** `auth.fallo` de origen desconocido.
2. `frontend/app/migracion/page.tsx` ya manda el header (verificado en producción).
3. `RUTAS_ABIERTAS` cubierta por una prueba automatizada (ver punto 3 del documento).
4. Llaves emitidas en `api_keys` para todo actor legítimo detectado.
5. Ventana elegida: **día hábil, en horario en que Brandon esté frente al panel**, nunca viernes ni de madrugada.

**Cómo se prueba, en este orden, dentro de los primeros 3 minutos:**
```
curl -s -o /dev/null -w "%{http_code}" .../api/health                       → 200  (si no, ABORTAR)
curl -s -o /dev/null -w "%{http_code}" -X POST .../api/webhooks/ml \
     -H 'Content-Type: application/json' -d '{"topic":"test","resource":"/x"}' → 200 (si no, ABORTAR)
curl -s -o /dev/null -w "%{http_code}" .../api/productos                    → 401 (esperado)
curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $K" .../api/productos → 200
```
Se empaqueta como `backend/scripts/humo_auth.py` para que sea un solo comando. Luego: estado del deployment en Railway (debe seguir healthy, sin reinicios), panel completo con login, y **contar filas de `pedidos_ml` de la hora siguiente contra la misma hora del día anterior**.

**Rollback.** `AUTH_ENFORCED=false` vía `set-variables` + `accept-deploy` en Railway. Tiempo real de reversión: ~2-4 min (el cambio de variable dispara redeploy). Ese tiempo cabe holgadamente dentro de la ventana de reintentos de ML, que es de **1 hora con backoff exponencial** — por eso el rollback no pierde ventas si se ejecuta con disciplina.

---

### FASE 4 — RBAC (`RBAC_ENFORCED`)

**Qué se toca.**
- `backend/core/rbac.py` (nuevo): diccionario declarativo `REGLAS` de `(método, prefijo de ruta) → rol mínimo`. Reparto propuesto:
  - `lectura`: todos los GET (`/api/productos`, `/api/fulfillment/*`, `/api/ventas/*`, `/api/canales`).
  - `operador`: `POST /api/productos/{sku}/contenido`, `/api/imagenes/*`, `/api/crear/*` salvo costos, `/api/publicar/preview`, `/api/sync/catalogo`, `/api/ia/*`.
  - `admin`: `POST /api/publicar/confirmar`, `POST /api/sync/woo`, `POST /api/crear/costos/bulk`, `POST /api/crear/costos/{sku}/recalcular`, `POST /api/fanout/encolar`, `POST /api/fanout/inventario/revisar`, `/api/migracion/backfill/*`, `/api/webhooks/pausar|reanudar`, `GET /api/auditoria`.
  - **Regla por defecto: lo no listado exige `admin`.** Un endpoint nuevo nace cerrado, no abierto.
- Roles en `app_metadata.rol` de los usuarios de Supabase Auth y en `api_keys.rol`.
- `backend/core/middleware.py` consulta `rbac.rol_requerido(metodo, ruta)` y emite `403` + fila `resultado='denegado'` en `auditoria`.
- `frontend/` — ocultar botones según `rol` de `/api/auth/me` (cosmético; la autoridad es el backend).

**Trabajo paralelo, no de código:** en WordPress, bajar de **12 administradores de 14 usuarios** a los 2-3 que realmente lo necesiten. Temu pregunta por RBAC del sistema, no del panel; dejar 12 admins contradice por escrito lo que se responda en III.2. Lo hace Brandon en el wp-admin, no un script.

**Cómo se prueba.** Deploy con `RBAC_ENFORCED=false` (observación: registra el 403 que habría dado, no lo da) → censo de 3 días → encender. Prueba dirigida: llave de rol `lectura` contra `POST /api/publicar/confirmar` → 403; llave `admin` → pasa. Flujos vivos: sin cambio (el scheduler no pasa por HTTP).

**Rollback.** `RBAC_ENFORCED=false`. Ortogonal a `AUTH_ENFORCED`: se puede apagar RBAC conservando la autenticación.

---

### FASE 5 — Endurecer el webhook de ML (sin ponerle autenticación)

**Qué se toca.** `backend/routers/webhooks.py`.

1. **Token en el path**: se registra en ML una URL de callback nueva `/api/webhooks/ml/<token-aleatorio-largo>` (variable `ML_WEBHOOK_PATH_TOKEN`), manteniendo **la ruta vieja viva y funcional** durante la transición. La ruta vieja no se retira hasta comprobar en `auditoria` que ML ya solo pega en la nueva.
2. **Filtro por identidad del emisor, siempre DESPUÉS de responder 200**: en `_procesar_ml`, descartar payloads cuyo `user_id` no esté en `_USER_A_CUENTA` (`3072519654`/`3064478475`) o cuyo `application_id` no sea `8902165405612832`. Se audita el descarte con `origen='webhook'`, `resultado='denegado'`.
3. **Allowlist de las 8 IPs de ML en modo ALERTA, nunca bloqueo** (`ML_WEBHOOK_IPS_ALERTA`): ML publica esa lista en una página sin versionado ni compromiso de aviso previo. Si agregan una IP y bloqueamos duro, se dejan de capturar ventas y el síntoma es silencioso.

**Lo que NO se hace aquí y por qué:** no se valida firma HMAC. **Las notificaciones de marketplace de Mercado Libre no tienen firma** — el `x-signature` con HMAC-SHA256 que aparece al buscar es de **Mercado Pago**, producto distinto. Quien proponga "validá la firma" está mezclando productos.

**El argumento fuerte, que ya es cierto hoy:** el handler usa `payload["resource"]` solo como **puntero** y re-consulta la orden con `meli.obtener_orden(order_id)` contra la API de ML. Un webhook falsificado **no inyecta datos**; a lo más provoca un fetch. El endpoint es no autenticado pero **no autoritativo**, y eso es exactamente lo que se escribe en III.1.

**Cómo se prueba.** POST falsificado con `user_id` desconocido → `200` y cero filas nuevas en `pedidos_ml`, más una fila `denegado` en `auditoria`. Venta real de prueba (o la siguiente venta orgánica) → pedido creado normalmente. Vigilar en `auditoria` que la tasa de webhooks/hora no caiga.

**Rollback.** Volver a apuntar la URL de callback en el DevCenter de ML a la ruta vieja (que sigue viva). El filtro por `user_id` se apaga con `ML_WEBHOOK_FILTRO_ENABLED=false`.

---

### FASE 6 — Evidencia y cierre del cuestionario

`backend/routers/auditoria.py` (nuevo): `GET /api/auditoria` con filtros por `accion`, `actor`, `resultado`, rango de fechas — **rol admin**, y cada lectura genera su propia fila `auditoria.consulta`. Página `frontend/app/auditoria/page.tsx`. Con eso Brandon puede sacar capturas reales para adjuntar: "accesos denegados del último mes", "quién cambió precios", "quién publicó en marketplaces".

---

## 3. EL PUNTO MÁS PELIGROSO

**Es uno solo, y no es "romper el webhook": es la constante `RUTAS_ABIERTAS` del middleware de la Fase 3.** De ella cuelgan dos fallas, y la segunda es peor que la primera.

**Falla A — el webhook de ML.** Si `POST /api/webhooks/ml` empieza a devolver 401, ML reintenta con backoff exponencial durante 1 hora y después **deshabilita el topic**. A partir de ahí se dejan de capturar **ventas reales**, y el síntoma es silencioso: no hay error, simplemente dejan de aparecer pedidos.

**Falla B — el healthcheck, que es peor.** `backend/railway.json` declara `healthcheckPath=/api/health` con `restartPolicyType: ON_FAILURE`. Si `/api/health` devuelve 401, **Railway declara el deploy fallido y entra en bucle de reinicio**. Eso tumba el backend entero: el webhook, el scheduler in-process (sync de 15 min, `odoo_watch`, fan-out, sondeos de Amazon y M2E) y el panel. Es decir, un error en una lista de strings puede apagar todo el negocio, no solo un webhook.

**Mitigación, en seis controles apilados:**

1. **Allowlist antes que enforcement, en el mismo commit.** El middleware evalúa `RUTAS_ABIERTAS` **antes** de mirar `AUTH_ENFORCED`. No hay orden de ejecución en que la ruta abierta pueda dar 401.
2. **Guarda absoluta sobre el webhook:** el handler de `/api/webhooks/ml` se envuelve de forma que **cualquier** excepción, incluida una de auth, devuelva `200` con cuerpo vacío. A ML nunca se le responde ≠200, pase lo que pase.
3. **Prueba automatizada:** `backend/scripts/humo_auth.py` corre con `AUTH_ENFORCED=true` en local y falla el build si `/api/health` o `/api/webhooks/ml` no dan 200 sin credenciales. Es la única prueba que se ejecuta obligatoriamente antes del push de la Fase 3.
4. **Escotilla sin código:** `AUTH_RUTAS_ABIERTAS` (CSV) permite abrir una ruta olvidada cambiando una variable, sin commit.
5. **Reversión medida contra la ventana de ML:** `AUTH_ENFORCED=false` + `accept-deploy` revierte en 2-4 min; la ventana de reintentos de ML es de **1 hora**. Mientras se revierta dentro de ese margen, no se pierde ni una venta — los reintentos las entregan.
6. **Detección activa, no pasiva:** en las 2 horas siguientes al enforcement, comparar `SELECT COUNT(*) FROM pedidos_ml WHERE creado > NOW() - INTERVAL 1 HOUR` contra la misma hora del día anterior. Si cae a cero, revertir sin investigar primero.

**Riesgo secundario, ya identificado y con dueño:** `POST /api/migracion/errores/resolver` es la **única regresión funcional garantizada** del enforcement (el botón del panel de migración lo llama sin header). Se arregla en la Fase 2, no en la 3.

---

## 4. ESFUERZO

| Fase | Contenido | Backend | Frontend | Total |
|---|---|---|---|---|
| 0 | CORS exacto, /docs off, /health recortado | 2-3 h | 0 h | **2-3 h** |
| 1 | Tabla `auditoria`, middleware capa A, `services/auditoria.py`, 11 puntos capa B, purga | 10-14 h | 0 h | **10-14 h** |
| 2 | `api_keys`, Supabase Auth (proyecto nuevo), `resolver_identidad`, login + centralizar 20 `fetch` | 6-8 h | 8-12 h | **14-20 h** |
| 3 | Enforcement + censo + humo | 2-3 h | 0 h | **2-3 h** (+7 días de espera) |
| 4 | `core/rbac.py`, roles, observación→enforcement | 5-7 h | 2-3 h | **7-10 h** (+ 2-3 h de Brandon en WordPress) |
| 5 | Endurecer webhook ML (path token, filtro user_id, IPs en alerta) | 4-6 h | 0 h | **4-6 h** |
| 6 | `GET /api/auditoria` + página + capturas para el formulario | 3-4 h | 3-4 h | **6-8 h** |
| | | | | **45-64 h** |

**Calendario realista:** ~3 semanas, dominadas por los dos periodos de observación obligatorios (7 días en Fase 2→3, 3 días en Fase 4). Costo de infraestructura: **$0/mes** (Supabase free cubre 50k MAU contra <20 usuarios; la tabla `auditoria` a ~200 MB/año cabe en el MySQL de Hostinger).

---

## 5. QUÉ RESPUESTAS DEL CUESTIONARIO CAMBIAN, FASE POR FASE

| Al terminar | III.1 Autenticación | III.2 RBAC | IV.1 Bitácora | IV.2 Retención | ¿Mandar el formulario? |
|---|---|---|---|---|---|
| **Fase 0** | Sin cambio | Sin cambio | Sin cambio | Sin cambio | No |
| **Fase 1** | Sin cambio | Sin cambio | **SE PUEDE RESPONDER** — la tabla existe, escribe y se puede demostrar con datos reales | **SE PUEDE RESPONDER** — 12 meses con purga automatizada ya corriendo | No todavía (`actor` sería `anonimo` en el 100%) |
| **Fase 2** | Parcial — "implementado, en despliegue gradual". No se responde "sí" en firme | Sin cambio | Mejora: `actor` real, más `auth.exito`/`auth.fallo` | Sin cambio | No |
| **Fase 3** | **SE PUEDE RESPONDER SÍ** — toda la API exige credencial salvo el webhook, documentado como no autoritativo | Sin cambio | Completa: "intentos de acceso fallidos" con datos reales | Sin cambio | **Sí para III.1, IV.1 y IV.2** |
| **Fase 4** | Firme | **SE PUEDE RESPONDER SÍ** — 3 roles, reglas declarativas, WordPress saneado | Suma `resultado='denegado'` por rol | Sin cambio | **Sí — momento recomendado para mandar el formulario completo** |
| **Fase 5** | Se refuerza (párrafo del webhook queda impecable) | — | — | — | Ya mandado; mejora la defensa si repreguntan |
| **Fase 6** | — | — | Adjuntar capturas de la bitácora en vivo | — | Material de respaldo |

**Recomendación a Brandon:** **mandar el formulario al terminar la Fase 4**, con la Fase 5 ya calendarizada. Mandarlo antes de la Fase 3 obliga a escribir "en implementación" en III.1, que es la respuesta que hace que un revisor de seguridad pida evidencia adicional y reinicie el ciclo.

**Redacción lista para IV (verdadera el día que se manda, no antes):**

> **IV.1** — Yes. The backend maintains an append-only audit table (`auditoria`) in our own MySQL database, separate from the analytics and migration databases. Each record captures actor identity and type, role, action, affected resource identifier, channel, UTC timestamp with millisecond precision, source IP, user agent, request correlation id, and outcome (success / denied / error). Logged events include authentication failures, access to buyer personal data, marketplace listing and delisting, price and stock changes, order creation and cancellation, configuration changes, and reads of the audit log itself. Audit records store **only** resource identifiers (order id, SKU) — never buyer names or other personal data, which are stored encrypted (Fernet: AES-128-CBC + HMAC-SHA256) and are never written to logs. The application has insert-only access; the log is readable by administrator role only, and such reads are themselves audited.
>
> **IV.2** — 12 months, enforced by an automated daily purge job. The period follows common industry practice (PCI-DSS 10.7: one year of history, three months immediately available) and exceeds the window needed to detect, investigate and notify a security breach under Mexico's LFPDPPP. Because audit records contain no personal data — only order identifiers — retaining the log does not extend retention of buyer personal data, which is anonymized in the order system after 30 days.

**Advertencia legal, sin adorno:** la LFPDPPP **no fija** un número de meses de retención para logs de auditoría; exige medidas de seguridad (art. 19) y notificación de vulneraciones (art. 20). El "12 meses" se justifica por PCI-DSS 10.7, no por ley mexicana. México reformó el marco en 2025 (traslado de funciones del extinto INAI a la Secretaría Anticorrupción y Buen Gobierno) — **quién es hoy la autoridad ante la que se notifica debe confirmarlo el abogado de Kubera antes de escribirlo en el formulario.**

---

## 6. LO QUE NO SE VA A HACER (y por qué)

1. **No se le pone autenticación al webhook de ML.** ML no puede mandar nuestro header. Un 401 aquí cuesta ventas reales. Se endurece por otras vías (Fase 5) y se declara honestamente en III.1 como endpoint no autenticado pero **no autoritativo**.
2. **No se valida firma HMAC del webhook.** No existe para notificaciones de marketplace de Mercado Libre; eso es Mercado Pago. Prometerlo sería prometer algo imposible.
3. **No se bloquea por IP de ML.** La lista de 8 IPs vive en una página sin versionado ni compromiso de aviso. Bloquear duro convierte un cambio de infraestructura de ML en pérdida silenciosa de ventas. Queda en modo alerta.
4. **No se construye JWT propio** (opción B del análisis). Sería reimplementar reseteo de contraseña, rotación, bloqueo por fuerza bruta y revocación — mantenimiento alto sin equipo de seguridad. Supabase lo regala a $0.
5. **No se pone Cloudflare Access ni ningún proxy delante.** Dos razones concretas: la retención de logs del plan gratuito es de **24 horas** (insuficiente para IV.1) y agrega un modo de falla nuevo justo delante del webhook, que tiene un presupuesto de 500 ms.
6. **No se mete nada en la BD kubera (`tukwcvsi…`) ni en los esquemas `core`/`channel`/`costing`/`ops`/`migration`.** Regla 4, y una fila nuestra rompería la racha de actas de 14 días de Eduardo. La auditoría vive en MySQL propio — que además es lo correcto: si el log viviera en Supabase y Supabase se cayera, dejaríamos de registrar exactamente durante el incidente que hay que investigar.
7. **No se agregan `auditoria` ni `api_keys` a `KUBERA_MIRROR_TABLAS`.**
8. **No se auditan los GET de lectura normales** (`/api/productos`, listados del panel). Es ruido que entierra la señal y castiga un pool de 6 conexiones. Se auditan mutaciones, denegaciones y accesos a PII.
9. **No se audita fila por fila en corridas masivas.** El barrido de 525 SKUs es 1 registro con el conteo. Sin esta regla la tabla se vuelve un SIEM y el hosting compartido no lo aguanta.
10. **No se implementa rate limiting en esta ronda.** `POST /api/ia/*` quema créditos de DeepSeek/Anthropic/Gemini/SerpApi sin tope, pero tras la Fase 3 ya requiere credencial, que es el 90% de la mitigación. Un limitador real es trabajo aparte, posterior.
11. **No se hace MFA obligatorio.** Supabase lo soporta y queda disponible; hacerlo obligatorio en un equipo que hoy tiene 12 de 14 usuarios como administradores garantiza que alguien se quede fuera y se termine desactivando. Se ofrece, no se impone.
12. **No se toca `backend/vendor/`** (regla 1), ni los ETLs, espejos o crons de Eduardo/José (regla 4).
13. **No se rota el `client_secret` de ML expuesto en el repo externo `publicador`.** Es un pendiente real y conocido, pero es trabajo manual de Brandon en el DevCenter de ML, fuera del alcance de estas cuatro preguntas.
14. **No se hace pentest ni certificación ISO/SOC 2.** El cuestionario no lo pide y no cabe en el presupuesto de tiempo.
15. **No se borran usuarios de WordPress con un script.** Bajar de 12 administradores a 2-3 lo hace una persona revisando caso por caso; automatizarlo puede dejar a alguien sin acceso a la tienda en producción.