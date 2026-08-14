# Second Opinion Request

## Question
# Revisión de arquitectura: las 31 tablas que quedan en MySQL

## Contexto (leer @CLAUDE.md para el detalle completo)

Kubera migró su panel omnicanal de un MySQL compartido (`u531713409_kubera_ml`,
Hostinger) a Supabase Postgres ("BD kubera"). **La migración de los 5 dominios
está CERRADA**: costos, pedidos, channel, core y categorías escriben y leen
kubera, y desde el 13-ago los espejos inversos a MySQL están APAGADOS.

Queda el desmantelamiento: **31 tablas de MySQL que nunca fueron parte de la
migración**. Este brief propone agruparlas y ordenarlas. Quiero que lo revisen
críticamente.

### La lección que domina todo el diseño

El 12-ago se congeló `pedidos_ml` creyendo que era inocuo (kubera ya era el
registro). Tres consultas del flujo de alta seguían leyéndola para DECIDIR:

- El candado de idempotencia respondía siempre "esta orden no existe" → cada
  webhook de ML creaba otro pedido en WooCommerce. **964 pedidos fantasma en
  4 h 17 min, $409,741.**
- La marca de agua de Amazon quedó fija (no duplicó por casualidad: no tuvo
  tráfico esa tarde).
- El vigilante de "silencio de ventas" gritó "sin ventas" en día récord.

**Regla resultante: congelar una tabla es cambiar el contrato de LECTURA, no
solo el de escritura. Un `None` de una tabla detenida no significa "no existe":
significa "ya no sé".** Un arnés de paridad mide si los datos coinciden, no si
alguien decide con ellos.

Esta semana aparecieron TRES bugs más de la misma familia — "la marca de *ya lo
hice* no la pone el hacerlo":
1. El turno del sync ordenaba por una fecha que solo cambia si el dato cambió.
2. La bitácora de creación guardaba la hora de la ESCRITURA DEL ESPEJO, no la
   del evento (60 filas con hasta 17.6 h de desfase invertían el estado de 50 SKUs).
3. `fanout_log._ya_compensado` (ver abajo).

---

## Los 7 grupos propuestos

### GRUPO 1 — ARCHIVO (11 tablas) — YA HECHO
`scraping_alibaba`, `atributos_ia`, `imagenes_producto`, `costos_ml`,
`backlog_errores`, `odoo_ranking`, `odoo_sync_backlog`, `odoo_sync_procesados`,
`sync_procesados`, `pipeline_runs`, `ml_estado`.

Congeladas desde el 22-jul (se desconectó el robot de Alibaba). Cero escritores,
cero lectores vivos. Ya se respaldaron: dumps `.sql.gz` con conteo de filas
verificado, checksums SHA256, dos copias (local + OneDrive) y manifiesto en Drive.

### GRUPO 2 — CACHÉS (5 tablas)

**`stock_watch_foto`** (13,060 filas, escrita cada 20 min).
- Entradas: `@backend/services/stock_watch.py` — foto de stock Woo/Odoo por SKU.
- Salidas: (a) el propio vigilante compara foto vs foto para detectar cambios;
  (b) **`@backend/services/channel_mirror.py:298` la lee para poblar
  `channel.listings` canal `general`** (`DROP_MIRROR_ENABLED=true`), que es la
  fuente del "stock real del DROP" en el tab de fulfillment.
- Cadena: MySQL → espejo → kubera → panel. **NO es caché: es fuente.**
- El propio código la marca "TABLA TEMPORAL — se borra al cerrar la migración".

**`ventas_horarias` + `ventas_sync`** (caché de la vista histórica de ML).
- Entradas: solo `ventas_ml._guardar_dia`, con guardia `VENTAS_ML_REFRESH=false`.
  Sin gemela en kubera A PROPÓSITO (el censo del espejo la marca `tabla_kubera: None`).
- Salidas: solo la vista `?fuente=ml` del tab Ventas. El tab normal usa
  `channel.orders` desde julio.

**`amazon_imagenes`** (678 filas) — ya espejada a `enrich.product_media`.
Una sola salida viva: el cache-get que decide si re-procesar una imagen.

**`ml_image_edit_backlog`** (12,702 filas) — ya espejada a
`ops.channel_submissions`. **Cero salidas vivas** (escritura pura).

### GRUPO 3 — BITÁCORAS (2 tablas)

**`fanout_log`** (6,107 filas). Parecía bitácora pura. **NO LO ES:**

```python
def _ya_compensado(wc_id: int) -> bool:
    """¿Ya le devolvimos el stock a este pedido? (evita compensar dos veces)."""
    try:
        return bool(db.fetch_one(
            "SELECT id FROM fanout_log WHERE item_id=%s AND accion='full_compensado' LIMIT 1", ...))
    except Exception:
        return False
```

Es el candado anti-doble-compensación de stock en pedidos FULL
(`@backend/services/pedidos_ml.py`). Si la tabla desaparece, el `except`
devuelve `False` = "no se ha compensado" → **compensa otra vez → stock fantasma
en Woo.** Misma familia exacta que los 964.
- Entradas: 8 sitios (4 servicios vivos + 4 scripts manuales).
- Otras salidas: `stock_full.py:354` **parsea la referencia del FBA desde el
  TEXTO del campo `resultado`** con una regex; y el panel lo muestra.

**`webhook_eventos`** (la campana del panel). Entradas: solo `odoo_watch`, ya
espejada a `ops.webhook_events`. Salidas: 3 endpoints del panel (aún MySQL).

### GRUPO 4 — EL PUBLICADOR (4 tablas) — el más caro

**`ml_progress`** (4,142 filas: 3,873 éxitos, 269 fallos, 2,098 SKUs).
- Entradas: 2 (los adaptadores del publicador). **SIN espejo ni gemela.**
- Salidas: **19 sitios en 9 archivos**. Los que deciden: `publicar._ml_publicaciones`
  (qué cuentas ya tienen publicación → evita re-publicar), `inventario` (5 sitios:
  identidad SKU↔item, respaldo del universo, camino histórico), `presencia`,
  `competencia_captura` (4), el listado ML del panel (`@backend/services/meli.py`),
  `studio`, contadores.

**`amazon_progress`** (1,791 filas) — 8 lectores en 6 archivos. El delicado:
`publicar._product_type_amazon`, que es parte de la **cadena de prioridad de la
regla 2 de la casa** (panel > histórico > detección por título).

**`ml_backlog` (60 MB) + `amazon_backlog` (186 MB)** — payloads completos de
cada publicación. Ya espejados a kubera. **Cero salidas vivas.** Son el 85% del
tamaño de toda la base.

#### MEDICIÓN CLAVE (hecha hoy contra producción)

`channel.listings` (kubera, alimentada por el sync de 15 min desde el universo
vivo de ML) vs `ml_progress`:

| | |
|---|---|
| pares (sku\|cuenta) exitosos en `ml_progress` | 3,873 |
| pares en `channel.listings` | 4,597 |
| **en `ml_progress` y NO en `listings`** | **0** |
| en `listings` y NO en `ml_progress` | 724 |
| **con `ml_item_id` DISTINTO** | **63** |

Ejemplos de los 63: `veh-0021-neg|sancorfashion` progress=MLM5042201126 vs
listings=MLM2814475081. Son SKUs republicados: `progress` guarda el item que
ÉL creó; `listings` el que está VIVO hoy.

`ml_progress` tiene lo que `listings` no puede tener: `success`, `error`,
`gtin_error`, `ml_url`, `dry_run`, `published_at`, `wc_id` — y los **269
intentos FALLIDOS**, que nunca llegaron a ser publicación.

Precedente: en `competencia_captura` medimos algo análogo con `productos.wc_id`
vs `core.products.wc_id` — 332 discrepaban y **WordPress le dio la razón a
kubera en los 332** (los ids de MySQL apuntaban a posts borrados).

### GRUPO 5 — MÁRGENES (3 tablas)
`ml_envio_real` (13,345), `ml_ficha` (971), `ml_visitas` (1,485).

Cachés de API que alimentan **solo el tab de Márgenes** (`routers/fulfillment.py`),
a demanda, sin crons. Cada una tiene exactamente **1 lectura + 1 escritura**, las
dos en su propio servicio (`@backend/services/envio_real.py`, `ficha_ml.py`,
`visitas_ml.py`). Valor real: `ml_envio_real` guarda lo que ML COBRÓ de verdad
por cada envío (el estimado mentía en las dos direcciones: a un SKU le inventaba
$200k de pérdida y a 141 les puso flete $0).

### GRUPO 6 — TOKENS (3 tablas)
`ml_tokens`, `ml_tokens_dashboard`, `tiktok_tokens` → Supabase Vault.
`ops.ml_tokens` ya existe (vacía). Bloqueado por decisión, no por técnica.
**Hallazgo:** el repo externo `MLREgisterDaily` (servicio Railway que Eduardo creía
sin uso) es EL RENOVADOR de esos tokens — su `.env` apunta a `ml_tokens` con la
llave Fernet y el OAuth del refresh. Apagarlo y migrar a Vault son EL MISMO
movimiento.

### GRUPO 7 — ANDAMIAJE (2 tablas + 1 columna) — al final
`alertas_estado` y `espejo_kubera_log` deben **sobrevivir con kubera caída** (son
el candado de alertas y la cola de errores del espejo). Y `productos.stock_odoo`:
`odoo_watch` la escribe cada 30 min y `core.products` no tiene esa columna; hoy
ese vigilante solo ve 4,786 de los 13,030 SKUs de Odoo porque su lista dejó de
crecer el 23-jul.

---

## Orden propuesto

**5 → 2 → 3 → 4**, con 6 en paralelo (depende de terceros) y 7 al final siempre.
Razón: el 5 resultó el más barato (1 lector + 1 escritor cada una) y el 4 el más
caro (~27 lectores sin gemela).

---

## LO QUE QUIERO QUE REVISEN (sean críticos y opinen fuerte)

1. **La pregunta arquitectónica del grupo 4.** Dado que `channel.listings` es
   superset de `ml_progress` y en 63 casos MÁS FRESCO: ¿los ~19 lectores de
   `ml_progress` deberían partirse en dos destinos — los que preguntan "¿está
   publicado / con qué item_id?" → `channel.listings`, y los que preguntan
   "¿cómo salió el intento?" → una bitácora tipo `ops.channel_submissions`? ¿O
   conviene una gemela 1:1 de `ml_progress` en kubera, más simple pero que
   perpetúa un segundo registro de la misma verdad? ¿Qué riesgos ven en cada
   opción? Ojo: los 63 con item_id distinto sugieren que HOY ya hay lectores
   decidiendo con un id viejo.

2. **¿El orden propuesto es el correcto?** Argumento en contra que quiero que
   consideren: quizá conviene atacar el grupo 4 PRIMERO, mientras el equipo
   tiene fresco el método del barrido de lectores, en vez de dejarlo al final
   cuando ya nadie recuerde el procedimiento.

3. **¿Qué NO estoy viendo?** Busquen en el código lectores/escritores que se me
   hayan escapado, especialmente los que DECIDEN. Mi barrido fue por `grep` de
   nombres de tabla en `backend/services/` y `backend/routers/`; puede haber SQL
   construido dinámicamente, o lectores en `backend/scripts/`, en el frontend, o
   en los repos externos.

4. **`fanout_log`**: ¿el candado `_ya_compensado` debería vivir en la bitácora, o
   es señal de que falta una tabla de estado propia (p. ej. una marca en
   `channel.orders`)? Un candado que se apoya en una bitácora de texto y que
   además se parsea con regex (`stock_full.py:354`) me parece frágil.

5. **Criterio para "archivar vs migrar".** Propongo: se archiva lo que no tiene
   lectores vivos; se migra lo que sí. Los backlogs (246 MB, cero lectores) se
   archivan y purgan. ¿Están de acuerdo? ¿Hay valor forense en conservar los
   payloads que justifique llevarlos a Postgres?

6. **Riesgos de secuencia.** ¿Ven dependencias entre grupos que rompan el orden
   propuesto? Ejemplo que me preocupa: `stock_watch_foto` alimenta el canal
   `general` de `channel.listings`, que a su vez lo consume el fan-out de stock
   (`@backend/services/fanout_stock.py`) — que decide a qué publicaciones
   empujar inventario.

## Restricciones del proyecto

- Producción operativa: NO se insertan datos de prueba en la BD kubera
  (`tukwcvsi…`); las pruebas van a un sandbox (`yvootpbz…`).
- Cambios que encienden/apagan flujos de negocio vivos requieren aprobación
  humana antes del push.
- Reversibilidad: el patrón usado en toda la migración es
  flag por dominio + reversa sin deploy. Toda propuesta debería preservarlo.
- El equipo trabaja en `main` en paralelo (varias versiones al día).

## Instructions
You are providing an independent second opinion. Be critical and thorough.
- Analyze the question in the context provided
- Identify risks, tradeoffs, and blind spots
- Suggest alternatives if you see better approaches
- Be direct and opinionated — don't hedge
- Structure your response with clear headings
- Keep your response focused and actionable
