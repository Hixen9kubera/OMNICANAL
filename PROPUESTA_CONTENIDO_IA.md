# PROPUESTA — Persistencia de contenido generado por IA, por producto y por canal

**Autor:** Eduardo · **Fecha:** 2026-08-10 · **Estado:** para revisión, nada creado aún

---

## Contexto de negocio

José pidió en Slack (7-ago): *"Cada que se publique un producto en omnicanal que
ya se genere [el prompt]. Ahorita córrelo para todos y a partir de mañana ya que
sea por cada uno se genere el prompt."*

Eso son dos modos sobre el mismo dato: una corrida masiva de una vez, y luego
generación por producto al publicar. Ambos necesitan dónde escribir. Hoy no lo
hay de forma utilizable, y por eso traigo esto antes de construir nada.

---

## Qué existe hoy (medido el 7-ago, no estimado)

Hay **cuatro** representaciones paralelas del mismo dato, en tres almacenes, con
dos convenciones de llave distintas:

### 1. Tabla MySQL `atributos_ia` — 5,380 SKUs, 4,355 con JSON real (81%)

```
sku (PK), estado, atributos_json, atributos_str, num_atributos,
atributos_validos, flags, modelo_ia, procesado_at, updated_at
```

- Guarda los atributos por **ID nativo de Mercado Libre** (`BRAND`, `MODEL`,
  `COLOR`). Ésa es la forma correcta y hay que conservarla.
- `flags` guarda el razonamiento de descarte de la IA
  (ej. *"ANALOG_CAMERA_LENS_FOCAL_LENGTH: No aplica a dash cam"*).
  **Ese dato no existe en ningún otro lado.**
- **Congelada desde el 22-jul**: ya nadie escribe ahí. Verificado por búsqueda —
  no hay un solo `INSERT`/`UPDATE`/`REPLACE` contra `atributos_ia` en todo
  `backend/`; la única mención viva es un docstring en `services/ml_atributos.py`
  que documenta de dónde se portó la lógica.
- **Mono-canal**: no tiene columna de canal. Todo es Mercado Libre implícito.

### 2. Meta de WooCommerce `ml_attributes` — 208 productos

El mismo dato, pero indexado por nombre en español: `{"Marca": ..., "Color": ...}`

### 3. Metas `ml_attr_<X>`

- 777 productos con `ml_attr_brand` (por ID)
- 208 productos con `ml_attr_Marca` (por nombre en español)

El mismo atributo bajo dos convenciones.

### 4. Supabase `enrich.ai_attributes` — existe, vacía, sin escritor

Esta es la que faltaba en el diagnóstico y **es la que decide dónde debe vivir
la tabla nueva**. Ya está creada en `supabase/migrations/0001_esquema_v4.sql`:

```sql
create table enrich.ai_attributes (
  sku        citext primary key references core.products(sku),
  attributes jsonb,
  is_valid   boolean,
  model_used text,
  updated_at timestamptz not null default now()
);
```

Es el destino que el esquema v4 ya le había asignado a `atributos_ia`. Pero:

- **No tiene escritor.** No aparece en el mapa de UPSERTS de `kubera_mirror.py`
  ni en `KUBERA_MIRROR_TABLAS`. Está vacía.
- **Arrastra los dos mismos defectos** que acabamos de diagnosticar en
  `atributos_ia`: PK de un solo campo (mono-canal) y solo atributos
  (`attributes`), sin lugar para los otros cuatro campos.

Se diseñó antes de que existiera este requerimiento. No está mal — está
incompleta.

---

## Por qué no alcanza con extender `atributos_ia`

Sólo cubre **atributos**. El prompt que se va a correr genera **cinco campos**
por producto:

| Campo | Nota |
|---|---|
| `titulo` | límite por canal (Walmart 200, ML 60) |
| `bullets` | Amazon: 5; Walmart `keyFeatures`: 5 |
| `descripcion` | sin HTML en Walmart, máx. 3,900 |
| `atributos` | por ID nativo del canal |
| `backend_search_terms` | **se mide en BYTES, no caracteres** — 249 máx. en Amazon |

Cuatro de los cinco no tienen dónde vivir.

---

## Lo que propongo

Una tabla con el payload completo del canal, con PK **(sku, canal, cuenta)**:

| Columna | Qué guarda |
|---|---|
| `sku` | identificador del producto |
| `canal` | `mercado_libre` \| `amazon` \| `walmart` \| `temu` \| `tiktok` — son los ids de `core.channels`, la FK los obliga (`meli` se rechaza) |
| `cuenta` | BEKURA \| SANCORFASHION \| '' — ver decisión (c) |
| `categoria` | la categoría **de ese canal**, no la de Woo |
| `payload` | JSON con los 5 campos + atributos por ID nativo |
| `origen` | JSON, por campo: `woo` \| `const` \| `ia` \| `calc` |
| `flags` | JSON, el razonamiento de descarte de la IA |
| `modelo_ia` | qué modelo lo generó |
| `spec_version` | versión del esquema del canal (Walmart 3.11 vs 4.X) |
| `estado` | `pendiente` \| `ok` \| `error` \| `obsoleto` |
| `hash_woo` | hash del producto en Woo al momento de generar |
| `generado_at` | cuándo lo produjo la IA |
| `actualizado` | cuándo se tocó la fila |

---

## Decisiones de diseño que pido que revisen

### (a) `estado` ES el backlog

`atributos_ia` ya usa ese patrón (`pendiente`/`ok`/`error`) y funciona. El
*"córrelo para todos"* es procesar los pendientes; el *"por cada uno al
publicar"* es insertar en `pendiente`. No hace falta cola aparte.

### (b) `hash_woo` es el detector de obsolescencia

Si el producto cambia en Woo, el hash deja de coincidir y sabes que ese payload
hay que regenerar. Sin eso, en un mes nadie va a saber cuáles están al día.

**Definir qué entra al hash.** Propongo: título + descripción + precio +
categorías + lista de IDs de imágenes. Si entra el `updated_at` de Woo, el hash
cambia con cada toque irrelevante y todo se marca obsoleto siempre.

### (c) `cuenta` en la PK — cambio respecto al borrador

El borrador proponía PK `(sku, canal)`. Eso asume que un SKU tiene **un solo**
contenido por canal, y en Mercado Libre eso no se cumple: hay dos cuentas
(BEKURA y SANCORFASHION) y el mismo SKU puede estar publicado en ambas, incluso
en **categorías distintas** — y los atributos derivan de la categoría. El caso
`EST-0091` ya está documentado como dos productos diferentes según la cuenta.

El patrón de la casa ya resuelve esto: `canal_inventario` tiene PK
`(sku, canal, cuenta)` y `channel.listings` tiene `(sku, account_id, canal)`.
Propongo lo mismo, con `cuenta = ''` cuando el canal es de cuenta única
(Walmart, Amazon hoy).

Agregar una columna a la PK después es caro. Ahora es gratis.

### (d) `estado = 'publicado'` sale del enum

Publicar no es un estado del contenido, es un evento del envío, y ya vive en
`ml_progress` / `amazon_progress` / `walmart_progress`. Si se queda aquí, dos
tablas dicen lo mismo y en algún momento se van a contradecir.

Lo reemplazo por `obsoleto`, que es el estado que la decisión (b) necesita y el
borrador no tenía: *el hash ya no coincide, hay que regenerar*.

---

## Volumen esperado

~2,000 productos publicables × 5 canales = **10,000 filas** como techo.
Hoy realistas: ~5,400 de Mercado Libre (migrando desde `atributos_ia`) más lo
que se genere. El payload pesa ~2-4 KB por fila → **20-40 MB** en el techo.
No es un problema de tamaño para ninguno de los dos motores.

---

## Migración

`atributos_ia` **no se tira**: sus 4,355 JSON entran como el bloque de atributos
de `canal='meli'`, con sus `flags` y su `modelo_ia` intactos. Es un
`INSERT ... SELECT`.

Con dos salvedades honestas:

1. Siembra **uno de los cinco campos**. Título, bullets, descripción y
   `backend_search_terms` nacen vacíos y hay que generarlos igual. El valor real
   de la siembra son los `flags`, que no están en ningún otro lado.
2. La cuenta se siembra en `''` porque `atributos_ia` no la tiene. Si después
   resulta que un SKU necesita contenido distinto por cuenta, esa fila se
   duplica y se ajusta. No hay forma de recuperar el dato retroactivamente.

---

## Las preguntas que sólo ustedes pueden contestar

**1. ¿Dónde vive?**

Con el hallazgo de `enrich.ai_attributes`, la pregunta cambia de forma: no es
*"¿dónde creo una tabla nueva?"* sino *"¿completo la que el v4 ya reservó?"*.

Mi recomendación: **`enrich.ai_content` en la BD kubera**, y `enrich.ai_attributes`
se retira sin migrar nada (está vacía). Razones: el esquema v4 ya había decidido
que este dato pertenece a `enrich`; nacer en MySQL significa migrarlo después; y
`jsonb` con GIN permite consultar dentro del payload, que es justo lo que se
necesita para auditar qué generó la IA.

Alternativa legítima: nacer en MySQL si el publicador tiene que leerlo en la ruta
crítica y no queremos meter Supabase ahí todavía. Dejo el DDL de los dos abajo
para que la decisión no cueste trabajo.

**2. ¿Entra al espejo (`KUBERA_MIRROR_TABLAS`) o se escribe directo?**
Si nace en kubera, escritura directa. El espejo es para tablas que ya existían
en MySQL; ésta no tiene por qué nacer con deuda.

**3. ¿Convención de nombres?**
Seguí la de v4 (inglés, snake_case, esquema `enrich`) para la versión Postgres y
la de `kubera_ml` (español) para la de MySQL. Díganme cuál prefieren.

**4. ¿jsonb o json? ¿GIN?**
`jsonb` y sí, un GIN sobre `payload`. Con 10,000 filas el índice es barato y sin
él no se puede preguntar *"¿qué SKUs tienen bullets vacíos?"* sin escanear todo.
En MySQL/MariaDB no existe equivalente: ahí el `longtext + json_valid` es lo que
hay, y las consultas por contenido van por `JSON_EXTRACT` sin índice.

**5. ¿Quién es dueño de la escritura?**
Propongo que **sólo el backend de omnicanal escriba** y todo lo demás lea. Es la
misma regla que ya aplica al panel sobre las categorías (*"la elección del panel
manda"*).

---

## DDL — opción kubera (Postgres) · recomendada

```sql
-- Reemplaza enrich.ai_attributes (vacía, sin escritor, mono-canal).
drop table if exists enrich.ai_attributes;

create table enrich.ai_content (
  sku          citext not null references core.products(sku),
  canal        text   not null references core.channels(id),
  cuenta       text   not null default '',
  categoria    text,
  payload      jsonb  not null,
  origen       jsonb,
  flags        jsonb,
  modelo_ia    text,
  spec_version text,
  estado       text not null default 'pendiente'
               check (estado in ('pendiente','ok','error','obsoleto')),
  hash_woo     char(40),
  generado_at  timestamptz,
  updated_at   timestamptz not null default now(),
  primary key (sku, canal, cuenta)
);

create index idx_ai_content_pendientes on enrich.ai_content (canal, estado);
create index idx_ai_content_payload    on enrich.ai_content using gin (payload);

comment on column enrich.ai_content.hash_woo is
  'sha1 de titulo+descripcion+precio+categorias+ids_imagenes de Woo. '
  'NO incluir updated_at de Woo: cualquier toque marcaria todo obsoleto.';
```

> Ojo con la FK a `core.products(sku)`: hoy hay **82 SKUs faltantes** en el
> maestro (el mismo bloqueo que tiene el backfill de `ops.channel_submissions`).
> Esos productos no van a poder registrar contenido hasta que se resuelva.

## DDL — opción omnicanal (MySQL)

```sql
CREATE TABLE `producto_payload` (
  `sku`          varchar(100) NOT NULL,
  `canal`        varchar(24)  NOT NULL COMMENT 'meli|amazon|walmart|temu|tiktok',
  `cuenta`       varchar(50)  NOT NULL DEFAULT '',
  `categoria`    varchar(64)  DEFAULT NULL COMMENT 'categoria DEL CANAL, no la de Woo',
  `payload`      longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
                 CHECK (json_valid(`payload`)),
  `origen`       longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL
                 COMMENT 'por campo: woo|const|ia|calc' CHECK (json_valid(`origen`)),
  `flags`        longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL
                 COMMENT 'razonamiento de descarte de la IA' CHECK (json_valid(`flags`)),
  `modelo_ia`    varchar(60) DEFAULT NULL,
  `spec_version` varchar(20) DEFAULT NULL,
  `estado`       varchar(12) NOT NULL DEFAULT 'pendiente'
                 COMMENT 'pendiente|ok|error|obsoleto',
  `hash_woo`     char(40) DEFAULT NULL,
  `generado_at`  datetime DEFAULT NULL,
  `actualizado`  timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`sku`,`canal`,`cuenta`),
  KEY `idx_pendientes` (`canal`,`estado`),
  KEY `idx_actualizado` (`actualizado`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci
  COMMENT='Contenido generado por IA, por producto y canal';
```

Tres ajustes respecto al borrador: `sku` a `varchar(100)` (es lo que usan
`ml_backlog` y `amazon_backlog`; `varchar(64)` no coincide con ninguna tabla
existente), `JSON` escrito como `longtext + json_valid` (es la convención de
todas las tablas de `kubera_ml`; `JSON` a secas es un alias que se guarda
distinto), y `ENUM` cambiado a `varchar` para que el espejo a Postgres no
necesite traducir el tipo.

### Siembra desde `atributos_ia` (aplica a las dos opciones)

```sql
INSERT INTO producto_payload
      (sku, canal, cuenta, payload, flags, modelo_ia, estado, generado_at)
SELECT sku, 'meli', '',
       JSON_OBJECT('atributos', JSON_EXTRACT(atributos_json, '$')),
       flags, modelo_ia,
       CASE WHEN atributos_validos = 1 THEN 'ok' ELSE 'pendiente' END,
       procesado_at
  FROM atributos_ia
 WHERE atributos_json IS NOT NULL AND json_valid(atributos_json);
-- esperado: 4,355 filas
```

---

## Nota de alcance

No toqué nada de los esquemas de la migración ni de los ETLs. Esto es una
propuesta para que la tabla nazca donde ustedes decidan, con su convención.

Lo único que sí requiere decisión antes de escribir código: `enrich.ai_attributes`
existe y está vacía. Si `enrich.ai_content` se aprueba, esa tabla se retira; si
no, hay que decir explícitamente qué se hace con ella, porque tal como está no
sirve para lo que José pidió.
