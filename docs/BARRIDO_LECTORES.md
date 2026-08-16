# Barrido de lectores de `kubera_ml` — quién se rompe si se retira MySQL

> Hecho el 16-ago-2026. Es la tarea que el consejo marcó como **requisito antes
> de borrar cualquier tabla**: hasta hoy, mi barrido solo cubría
> `backend/services/` y `backend/routers/`, y cada "cero lectores" era una
> hipótesis, no un hecho.
>
> Método: SQL real (`FROM` / `JOIN` / `INTO` / `UPDATE` / `CREATE TABLE`), no
> menciones en prosa. 38 tablas buscadas en cuatro superficies.

## Resultado en una tabla

| Superficie | Tablas tocadas | Veredicto |
|---|---|---|
| `frontend/` | **0** | **limpio** — todo pasa por la API |
| `backend/scripts/` | 14 en 33 archivos | la mayoría es andamiaje de la propia migración; **5 scripts operativos no estaban en la lista** |
| **`backend/services/meli.py`** | `ml_tokens`, `ml_tokens_dashboard` | 🛑 **el bloqueador, y es NUESTRO** |
| `MonitoreoOperaciones` | 7 | lee mucho más de lo documentado, pero **sin deploy desde el 23-jun** |
| `MLREgisterDaily` | 1, y escribe | **sin deploy desde el 26-may** — ya no es el renovador |
| `Aplicacion_Excel`, `personal` | 0 | limpio (los aciertos eran la palabra "productos" en prosa y `pymysql` dentro de `.venv`) |
| `publicador`, `KuberaPipelineV1.0` | — | se retiran con el robot de Alibaba (decisión del 23-jul) |

> **Corregido el 16-ago, el mismo día.** La primera versión de este documento
> señalaba a `MLREgisterDaily` como el bloqueador de los tokens. Eduardo avisó
> que ese servicio y `MonitoreoOperaciones` ya no operan, y al medirlo tenía
> razón. **El bloqueador no desapareció: se movió, y resultó estar en casa.**
> Abajo, con lo medido.

## 🛑 Lo más grave: los tokens de Mercado Libre — y el culpable somos nosotros

**Los tres números que lo definen**, medidos el 16-ago:

    ops.ml_tokens (kubera)     0 filas          ← la casa nueva está VACÍA
    MySQL ml_tokens            2 filas, 16:35   ← escritas HOY
    MySQL ml_tokens_dashboard  2 filas, 16:35

La tabla en kubera existe desde hace tiempo, con columnas pensadas para Vault
(`vault_access_secret`, `vault_refresh_secret`), y **nunca se llenó**. Toda la
autenticación de Mercado Libre vive en MySQL.

**Y quién las escribió a las 16:35: nuestro propio backend.**

```
backend/services/meli.py:486
  UPDATE ml_tokens SET access_token=%s, refresh_token=%s, updated_at=NOW() WHERE cuenta=%s
backend/services/meli.py:492
  UPDATE ml_tokens_dashboard SET ...
```

Es la auto-sanación de la **regla 8**: ante un 401 de ML, el backend renueva el
token y lo guarda. Y lo lee de ahí también — `meli.py:424` saca `app_id` y
`client_secret` de `ml_tokens_dashboard`, y `meli.py:434` el `refresh_token` de
`ml_tokens`. **La cadena completa de autenticación está en MySQL.**

### Por qué la primera versión de este documento se equivocó de culpable

Señalé a `MLREgisterDaily` porque su código efectivamente lee y escribe
`ml_tokens` (`backend/app/db/mysql_tokens.py`), y la regla 8 habla de "un proceso
externo irregular". Pero **ese servicio no se despliega desde el 26-may-2026**, y
sus commits recientes son de archivo de ventas y de competidores: el repo se
reutilizó para otra cosa. No hay ningún clon suyo como servicio aparte.

La conclusión —*retirar MySQL mata la autenticación de ML*— **era correcta**. El
culpable, no. Me quedé con el primer código que encajaba con la regla 8 y no
seguí buscando dentro de casa.

**Y la diferencia importa, a favor.** Creía que había que coordinar con otro
equipo y un servicio ajeno. Es nuestro código, en nuestro repo: se resuelve solo,
sin depender de nadie, y sin prisa mientras MySQL siga vivo.

Sigue siendo **bloqueador del retiro del esquema** y no una tarea de limpieza —
lo que cambia es que ahora tiene dueño. Son "nada más autentificadores", sí, pero
sin ellos no hay llamada a la API de ML: se van los webhooks de ventas, publicar,
el sync de inventario y competencia.

**Qué hay que hacer**: llenar `ops.ml_tokens` y repuntar las cuatro consultas de
`meli.py` (dos lecturas, dos escrituras). Es el PASO 6, y ya no depende de "mover
tokens a Vault" ni de apagar un servicio ajeno.

## ⚠️ `MonitoreoOperaciones` lee SIETE tablas, no una

CLAUDE.md dice *"lee `productos` de MySQL"*. Medido en su código:

| Tabla | Dónde |
|---|---|
| `ml_backlog` | `/api/summary`, `/daily`, `/hourly`, `/errors`, `/products`, `/catalogo`, `/ready`, y el export a Excel |
| `amazon_progress` | `/api/amazon/summary`, `/errors`, `/products` |
| `amazon_backlog` | `/api/amazon/summary`, `/daily`, `/errors` |
| `scraping_alibaba` | `/api/pipeline/summary` |
| `atributos_ia` | `/api/pipeline/summary` |
| `costos_ml` | `/api/pipeline/summary` |
| `productos` | `/api/pipeline/productos` (con JOIN a las tres de arriba) |

`ml_backlog` es su columna vertebral: casi todos sus KPIs salen de ahí.

**Pero el servicio no se despliega desde el 23-jun-2026**, y Eduardo confirma que
ya no opera. Así que la decisión pendiente se simplifica: no hay nada que
repuntar — **hay que confirmarlo y darlo de baja formalmente**.

Lo que sí conviene saber antes de apagarlo: tres de las tablas que consume
(`scraping_alibaba`, `atributos_ia`, `costos_ml`) son del robot de Alibaba,
desconectado desde el 23-jul. O sea que **si alguien todavía abre ese tablero,
lleva meses leyendo historia congelada** — y eso vale la pena decirlo antes de
que alguien tome una decisión con esos números.

## `backend/scripts/` — separar andamiaje de operación

De los 33 archivos, la mayoría son **de la propia migración** y mueren con ella:
`comparar_*`, `probar_*`, `backfill_*`, `migrar_*`, `rescatar_*`,
`etl_channel_listings` (el espejo), `etl_core_products` (retirado con candado) y
`sembrar_sandbox` (obsoleto).

Lo que sí es **operación** y hay que repuntar o archivar:

| Script | Tablas | ¿Estaba en la lista de 8? |
|---|---|---|
| `alinear_ml_drop` · `alinear_amazon_drop` | `canal_inventario`, `fanout_log` | sí |
| `marcar_amazon_muertas` | `canal_inventario` | sí |
| `corregir_status_publicados` · `corregir_stock_woo_full` | `canal_inventario` | sí |
| `sincronizar_ml_huerfanas` | `canal_inventario`, `fanout_log` | sí |
| `publicar_walmart` | `canal_inventario`, `amazon_imagenes` | sí |
| `sync_odoo_woo_seguro` | `pedidos_ml` | sí |
| **`corregir_stock_amazon`** | `fanout_log` | **NO** |
| **`actualizar_comision`** | `costos_finales` | **NO** |
| **`backfill_dims_validados`** | `costos_finales` | **NO** |
| **`competencia_analisis`** | `categorias_ml`, `ml_progress`, `productos` | **NO** |
| **`reporte_sync_desde_ml`** | `ml_progress` | **NO** |

**Son 13, no 8.** Ninguno es un flujo vivo —se corren a mano— pero dejarán de
funcionar sin aviso el día del retiro.

## Lo que el barrido confirma del método

Las veces que este proyecto midió en vez de suponer, el número cambió:

- los lectores del grupo 4 eran **25 y no 19**,
- `ml_progress` estaba **viva**, no congelada,
- `MonitoreoOperaciones` lee **7 tablas y no 1**,
- y los scripts de mantenimiento son **13 y no 8**.

Ningún conteo se movió a la baja: **la estimación de memoria siempre subestimó.**

**Y una lección distinta, del error del mismo día.** Contar bien no alcanza:
este documento contó bien las tablas de `ml_tokens` y aun así **atribuyó mal el
riesgo**, porque me quedé con el primer código que encajaba con la regla 8
—un repo externo— y no seguí buscando dentro de casa. El número era correcto y
la conclusión también; el dueño, no.

Lo que lo destapó no fue otra medición: fue que **Eduardo dijo "ese servicio ya
no opera"** y eso obligó a preguntar *"entonces quién escribió a las 16:35"*.
Sin esa pregunta, el documento habría mandado a coordinar con un equipo ajeno
para arreglar algo que está en nuestro propio `meli.py`.

Moraleja para el próximo barrido: **cuando una tabla resulte estar viva, la
siguiente pregunta no es "¿quién la lee?" sino "¿quién la escribió la última
vez?"** — esa segunda pregunta apunta al dueño real.

## Qué queda por barrer

- **`publicador` y `KuberaPipelineV1.0`**: no se revisaron a fondo porque se
  retiran con el robot de Alibaba. Conviene confirmarlo antes del retiro, no
  asumirlo — es exactamente el tipo de suposición que este barrido desmintió
  tres veces.
- **`MCPPruebaWOO`**: sin revisar.
- **SQL dinámico**: el método busca nombres de tabla literales. Una consulta
  armada con f-strings a partir de una variable no aparece. En este repo el
  riesgo es bajo (los nombres van en el literal), pero no es cero.
