# Categoría de Mercado Libre y guías de tallas — cómo se decide de verdad

> Extraído de producción el 2026-09-03, entre los commits 1a7da7e (v0.371.0)
> y 07cf6e4 (v0.380.0) — `main` avanzó 14 commits mientras se escribía esto.
> Comprobado: NINGÚN archivo de ML cambió en esa ventana, así que las citas
> `archivo:línea` de aquí siguen siendo válidas.
> ESTO ES UNA COPIA DE CONSULTA. La verdad vive en main; si algo no cuadra,
> gana main y hay que re-extraer.

Backend al cerrar: `version="0.380.0"` (`backend/main.py:132`).
Sitio de ML: `MLM` (México) — `backend/config.py:265`.
Cuentas: `BEKURA` ("Kubera") y `SANCORFASHION` ("San Corpe").

---

## 0. La idea en una frase

**La categoría de ML no la decide un detector: la decide la meta que un humano
guardó en el panel. Todo lo demás es respaldo.**

Hay **tres** cosas que pueden proponer una categoría —un predictor de ML, la
categoría de WooCommerce, y una elección humana— y el orden entre ellas es la
regla 2 de la casa. Equivocarse **no da error**: el producto se publica, queda
vivo, y nadie se entera hasta que un cliente lo busca donde no está.

---

## 1. Las cuatro llaves que hay que conocer

Todas son **metas de WooCommerce** (`wp_postmeta`), colgadas del producto.

| Meta | Quién la escribe | Quién la lee | Qué significa |
|---|---|---|---|
| **`ml_categoria_id`** | `POST /api/crear/categoria-ml` (`backend/routers/crear.py:426`) | El publicador: `backend/services/publicar_ready.py:425` | **La elección HUMANA. MANDA.** |
| `ml_category_id` | el mismo endpoint (`crear.py:427`) **y** el sync de costos (`crear.py:644`, vía `_sync_woo_costo`) | el publicador como 2º recurso (`publicar_ready.py:426`) y el Estudio (`backend/services/wp_db.py:598`) | La del predictor / la del costeo |
| `ml_categoria_path`, `ml_categoria_nivel_1..5`, `ml_categoria_niveles` | `crear.py:429-435` | `wp_db.py:598-618` (breadcrumb del Estudio), `publicar_ready.py:431` | Texto legible de la ruta |
| **`ml_dominio_id`** | `crear.py:431` — y **nadie más** | **NADIE.** Cero lectores en todo el repo | El `catalog_domain` de la categoría, guardado como dato muerto |

> **Medido el 2026-09-03 contra `wp_postmeta` (solo SELECT):**
> `ml_categoria_path` 5,296 · `ml_categoria_id` 5,283 · `ml_category_id` 3,722 ·
> `ml_category_name` 3,722 · `ml_categoria_niveles` 3,229 · `ml_dominio_id` 3,196.

**El `ml_dominio_id` es un dato muerto**: se escribe y jamás se consulta
(verificado con `grep -rn "ml_dominio_id"` sobre todo el repo: **una sola
aparición, la del escritor**). El dominio que de verdad se usa al publicar se
vuelve a pedir EN VIVO a la API de ML — ver §6.

---

## 2. El predictor automático de ML: `domain_discovery`

### Cómo se le pregunta

```
GET https://api.mercadolibre.com/sites/MLM/domain_discovery/search?q=<título>&limit=<n>
```

Se usa en dos sitios, con matices distintos:

**a) El buscador del picker del Estudio** — `backend/routers/crear.py:503`
(endpoint `GET /api/crear/categorias-ml?q=&limite=`):

```python
r = await _get(f"/sites/{settings.ml_site_id}/domain_discovery/search",
               {"limit": limite, "q": q})
```

Devuelve por candidato `category_id`, `category_name` y `domain_name`. Después,
para cada uno, el backend pide `/categories/{cid}` en paralelo
(`crear.py:523-533`) solo para armar el `path` ("Nivel1 > … > hoja").
Intenta con el token de ML de BEKURA y, si el token está vencido, **reintenta sin
auth** porque `domain_discovery` y `/categories` son públicos
(`crear.py:490-500`).

**b) El alta de producto ("Crear Productos")** — `backend/services/crear_producto.py:624`,
dentro de `categoria_ml(sku, titulo)`. Ahí es el **último** recurso de una
cadena de tres:

```
1. Categoría CURADA por SKU   → channel.product_category de kubera (source='panel' preferido)
2. costos_finales.ml_cat_id   → la que se usó para calcular la comisión
3. domain_discovery(título)   → la adivinanza
```

Detalle real que costó tiempo (`crear_producto.py:625`):

```python
params={"limit": 3, "q": titulo},  # limit=1 devuelve [] en ML (bug); 3 sí trae
```

### ⚠️ `domain_discovery` NO es un índice del árbol — es un predictor

Esto está medido y anotado en el propio código del picker
(`frontend/components/CategoriaMLPicker.tsx:58-68`):

> Hay categorías perfectamente publicables que **NUNCA sugiere**. Caso real:
> `MLM31513` "Hogar, Muebles y Jardín > Baños > Lavabos para Baño" — hoja,
> `status: enabled`, `listing_allowed: true`, **15,377 publicaciones** — y aun
> así buscar "lavabo", "lavamanos" u "ovalín" solo devuelve `MLM189323`
> (Construcción) y `MLM455948` (Accesorios Náuticos).

Por eso el picker acepta que le **pegues el ID directo**: si lo tecleado
contiene un `MLM\d{3,}` (o una URL de ML que lo contenga), resuelve por lookup
en vez de buscar por nombre (`CategoriaMLPicker.tsx:69`):

```ts
const idSuelto = q.trim().match(/\bMLM\d{3,}\b/i)?.[0]?.toUpperCase() ?? "";
```

**Sin esa salida, esas categorías son INALCANZABLES desde el panel.** Si un KAM
dice "no me sale la categoría en el buscador", la respuesta es: búscala en
mercadolibre.com.mx, copia el `MLM…` y pégalo en la misma caja.

---

## 3. `POST /api/crear/categoria-ml` — qué escribe exactamente

`backend/routers/crear.py:398-449`. Cuerpo: `{"wc_id": 1234, "category_id": "MLM447349"}`.

### Paso a paso

1. **Valida contra ML** (`crear.py:412-415`): `GET https://api.mercadolibre.com/categories/{cat_id}`
   **sin token** (público). Si no es 200 → `404 Categoría no encontrada`.
2. **Deriva** de la respuesta (`crear.py:417-421`):
   - `niveles` = `[{id, name}, …]` desde `path_from_root`
   - `ruta` = los `name` unidos con `" > "`
   - `nombre_hoja` = `d["name"]`
   - `dominio_id` = `d["settings"]["catalog_domain"]`
3. **Escribe a WooCommerce** un solo `PUT /products/{wc_id}` con este
   `meta_data` (`crear.py:422-435`) — **estas son todas las llaves, textual**:

   | key | value |
   |---|---|
   | `ml_categoria_id` | `MLM447349` |
   | `ml_category_id` | `MLM447349` *(la misma — a propósito, para que no se contradigan)* |
   | `ml_category_name` | nombre de la hoja |
   | `ml_categoria_path` | `"Nivel1 > Nivel2 > … > hoja"` |
   | `ml_categoria_niveles` | JSON `[{"id":"MLM1574","name":"Hogar…"}, …]` |
   | `ml_dominio_id` | `MLM-BOOKCASES` (o `""`) |
   | `ml_categoria_nivel_1` … `ml_categoria_nivel_5` | un nombre por nivel, **máximo 5** |

4. **Avisa a kubera** (`crear.py:444-445`) con
   `categorias_write.registrar(wc_id, cat_id, nombre_hoja, ruta)`, en
   `run_in_threadpool` (psycopg2 bloquea y esto es un handler async). Escribe el
   árbol (`channel.categories`) y la asignación (`channel.product_category`,
   `source='panel'`). **Nunca rompe el guardado en Woo**: si kubera está caída,
   el evento cae a la cola `espejo_kubera_log` y se reprocesa desde `/migracion`
   (`backend/services/categorias_write.py:52-68`).
5. Devuelve `{ok, wc_id, category_id, name, path, niveles, domain}`.

**Comentario del propio código** (`crear.py:400-405`), que vale como
declaración de intención:

> `ml_categoria_id` es la elección HUMANA y MANDA sobre el predictor de Crear
> (`ml_category_id`). Sin este guardado, el picker solo cambiaba estado local y
> la categoría no persistía (bug 2026-07-23).

Ese endpoint **nació el 23-jul-2026** en el commit `802ef15` (v0.17.0). Antes,
elegir en el picker no persistía nada.

### Los otros dos endpoints del mismo tema

| Endpoint | Archivo | Qué hace | Token |
|---|---|---|---|
| `GET /api/crear/categorias-ml?q=&limite=` | `crear.py:475-537` | Busca por nombre (domain_discovery) + arma paths | opcional |
| `GET /api/crear/categorias-ml/{cat_id}` | `crear.py:452-472` | Detalle de UNA por ID: nombre + path | **ninguno** |

⚠️ Truco: `GET /api/crear/categorias-ml/{cat_id}` devuelve `"domain": ""`
**siempre** — y no es un bug: `/categories/{id}` no trae el dominio en la forma
que espera el picker; solo `domain_discovery` lo trae (`crear.py:470`).

---

## 4. La cadena de precedencia al PUBLICAR (hay TRES decisores, no dos)

Cuando se aprieta Publicar, el adaptador arma `prod` y el vendor arma el payload.
En ese camino la categoría se puede cambiar tres veces.

### Decisor 1 y 2 — el adaptador: panel > predictor

`backend/services/publicar_ready.py:420-435`:

```python
cat_panel = str(meta.get("ml_categoria_id") or "").strip()
cat_crear = str(meta.get("ml_category_id") or "").strip()
cat_id = cat_panel or cat_crear
```

Con el comentario que cuenta el incidente (`publicar_ready.py:420-424`):

> Categoría ML: hay DOS escritores con llaves distintas. `ml_categoria_id` la
> guarda el selector del PANEL (elección humana); `ml_category_id` la guarda el
> predictor de Crear Productos. La humana MANDA — caso TEC-1812-NEG.

Si hay `cat_panel`, el nombre legible se re-deriva del JSON `ml_categoria_niveles`
(`publicar_ready.py:429-435`), no del `ml_categoria_path`.

### Decisor 3 — el vendor: la categoría de WooCommerce

`backend/vendor/ml_ready/publisher_core.py:184-198` consulta
`wc_category_mapping.resolve_ml_category_from_wc()`:

```python
new_ml_id, motivo = resolve_ml_category_from_wc(prod.get('wc_categories', []), cached_ml_id)
if new_ml_id and motivo == 'override':
    category_id = new_ml_id     # ← la categoría de Woo GANA
```

`backend/vendor/ml_ready/wc_category_mapping.py` descarga **todas** las
categorías de Woo por REST y busca en el campo `description` el patrón
`ML: MLM###` (regex `\bML[:\s]*\s*(MLM\d+)`, línea 23). Política vieja: *"las
KAMs editan la categoría del producto en el admin de WooCommerce"*. El mapeo se
**cachea 1 hora en memoria** (`_CACHE_TTL_SECONDS = 3600`, línea 28).

> **Medido el 2026-09-03:** de **1,703** categorías `product_cat` en WordPress,
> **1,029** llevan el patrón `ML: MLM###` en su descripción. O sea: el tercer
> decisor está armado y activo en el 60% del árbol de Woo.

### Cómo se neutraliza ese tercer decisor

`backend/services/publicar_ready.py:457`:

```python
"wc_categories":    [] if cat_id else wp_db.categorias_wc(wc_id),
```

**Si el producto ya tiene categoría elegida, NO se le pasan las categorías de Woo
al vendor.** Sin insumo, el override no puede activarse. Sin elección, el mapeo
de Woo sigue siendo el respaldo, igual que antes.

### El orden final, en una línea

```
ml_categoria_id (panel)  >  ml_category_id (predictor/costeo)  >  mapeo "ML: MLM###" de la categoría WC
```

Y si al final no hay ninguna, el vendor aborta el SKU
(`publisher_core.py:199-201`: `[!] Sin ml_category_id — saltando {sku}`).

---

## 5. Los dos incidentes que explican la regla

### TEC-1812-NEG — "Máquinas Sexuales" publicado en "Máquinas de Coser"

**Qué pasó.** El panel decía **Máquinas Sexuales** (guardado en `ml_categoria_id`
por el selector). La publicación salió en **Máquinas de Coser** (el valor de
`ml_category_id`, que había puesto el predictor de Crear). El publicador leía
**solo** la llave del predictor, así que la corrección humana no llegaba nunca al
payload. Fuente: `README.md:977-984`.

**Por qué nadie lo cachó antes.** Porque **no hubo error**. ML aceptó el item, lo
creó, quedó activo. Un producto mal clasificado se ve idéntico a uno bien
clasificado desde el panel; lo único que cambia es que nadie lo encuentra.
Como lo resume `docs/FLUJO_POR_CANAL.md:114-116`:

> Ninguna taxonomía se traduce a otra, y **equivocarse NO da error**: el
> producto queda vivo y mal clasificado.

**Cómo se arregló.** Dos cosas distintas:

1. **El código**: `publicar_ready.construir_prod` pasó a preferir
   `ml_categoria_id` y a derivar el nombre de `ml_categoria_niveles`
   (`publicar_ready.py:425-435`).
2. **Los items que ya existían**: el de San Corpe estaba **pausado** y se
   corrigió EN VIVO con `PUT /items/{id}` cambiando `category_id` — ML lo
   aceptó. El de BEKURA estaba **cerrado**, y ahí ML responde
   `category_id.not_modifiable`: hubo que **republicar**.

> **La lección operativa, para un KAM:** una categoría equivocada se arregla
> sola si la publicación está **activa o pausada**. Si está **cerrada**, ya no —
> hay que crear otra. Corrígela antes de cerrar nada.

**El coletazo.** TEC-1812-NEG volvió a aparecer el 22-jul junto a MOD-0496-NUDE
y CAM-0034-BEI: al dar de baja publicaciones en el seller central, la bitácora
`ml_progress` seguía diciendo "publicado" y el botón intentaba *actualizar* items
muertos. Desde v0.15.0 el panel verifica en vivo con `GET /items/{id}` y re-crea
pausada donde haga falta (`README.md:6727-6760`).

### CAM-0034-BEI — el tercer decisor que nadie sabía que existía

Reporte de Eduardo. El panel mostraba la categoría corregida (`MLM69819`
Colchones Inflables) y la publicación salió con `MLM419960` (Colchonetas
Aislantes). El arreglo de TEC-1812 estaba puesto y **aun así falló**: el producto
seguía asignado en Woo a "Colchonetas Aislantes" (term 1852), cuya descripción
llevaba el patrón `ML: MLM###`, y `wc_category_mapping` hizo un **override
silencioso**. De ahí salió la línea `"wc_categories": [] if cat_id else …`
(`README.md:6702-6718`).

**Moraleja de los dos juntos:** cuando la categoría publicada no coincide con la
del panel, hay tres sitios donde mirar, no uno.

---

## 6. El dominio (`ml_dominio_id`) — para qué sirve y para qué NO

El **dominio** es el eje de catálogo de ML: un identificador tipo
`MLM-SNEAKERS`, `MLM-DRESSES`, `MLM-BOOKCASES`. Sale de
`/categories/{id}` → `settings.catalog_domain`. Varias categorías distintas
pueden compartir dominio (medido: `MLM6585` "Calzado > Tenis", `MLM454734`
"Ropa y Calzado > Tenis" y `MLM438537` "Indumentaria > Tenis" son las tres
`SNEAKERS`).

**Sirve para tres cosas dentro del pipeline:**

1. **Decidir si la categoría es "de catálogo"** — `publisher_core.py:214-218`:
   ```python
   cat_info = ml_api.get_category_info(category_id, token)
   is_catalog_category = bool(cat_info.get('settings', {}).get('catalog_domain'))
   ```
   Si lo tiene, el payload usa `family_name` y **omite `title`**
   (`publisher_core.py:361`). Esto explica un desconcierto clásico: *"puse un
   título y ML publicó otro"* — en categorías de catálogo el título no es tuyo.
2. **Elegir la guía de tallas** — es la mitad de la llave del mapping (§7).
3. **Mostrarlo en el picker** ("Dominio ML: …", `CategoriaMLPicker.tsx:116`).

**Para lo que NO sirve: para nada persistido.** La meta `ml_dominio_id` que
escribe `crear.py:431` **no la lee nadie**. El dominio se vuelve a pedir en vivo,
con token, en cada publicación (`publisher_core.py:215` y `:291`). Consecuencia
práctica: **la meta puede estar desactualizada y no pasa nada**, y también
**no te sirve para saber qué se va a usar al publicar** — para eso hay que
preguntarle a `/categories/{id}`.

> **Medido el 2026-09-03:** 3,196 productos tienen `ml_dominio_id` con valor, en
> **441 dominios distintos**. Los más poblados: `MLM-TOOLS_RENTAL_SERVICES`
> (292), `MLM-MODEL_NAVAL_ACCESSORIES` (262), `MLM-CLOTHING_LOTS` (262),
> `MLM-CARS_AND_VANS` (236), `MLM-OFFICE_SOFTWARES` (164).
> *(Que un producto de Kubera esté en "Servicios de Renta de Herramientas" o
> "Accesorios de Modelismo Naval" es, en sí, señal de que la clasificación
> automática se equivocó bastante. No lo verifiqué producto por producto.)*

---

## 7. Guías de tallas (`SIZE_GRID_ID`)

### 7.1 Qué son y dónde viven

ML exige, en ropa y calzado, un **SIZE_GRID_ID**: el id de una "guía de tallas"
creada previamente **en la cuenta del vendedor**. No es un atributo que se
invente: es un objeto que existe o no existe en ML.

El catálogo de las que tenemos vive en:

**`backend/vendor/ml_ready/size_chart_mapping.py`** — **47 líneas, y es la ÚNICA
excepción sancionada dentro de `backend/vendor/`** (regla 1 de `CLAUDE.md`:
*"`backend/vendor/` NO SE TOCA — excepción sancionada: `size_chart_mapping.py`
es CONFIG"*). O sea: **aquí sí se edita**, es donde se registran guías nuevas.

Contenido completo, tal cual está hoy (líneas 14-35):

```python
CHARTS_BY_ACCOUNT = {
    'BEKURA': {
        'SANDALS_AND_CLOGS:Hombre':    '5601946',
        'SNEAKERS:Hombre':             '5601948',
        'SNEAKERS:Mujer':              '5602224',
        'BOOTS_AND_BOOTIES:Hombre':    '5602034',
        'LOAFERS_AND_OXFORDS:Hombre':  '5601950',
        'BRAS:Mujer':                  '5269931',
    },
    'SANCORFASHION': {
        'SANDALS_AND_CLOGS:Hombre':    '6009679',
        'SNEAKERS:Hombre':             '4538718',  # 4550104 tambien existe, 4538718 es la mas nueva
        'SNEAKERS:Mujer':              '4821199',
        'SNEAKERS:Sin género':         '4827537',
        'BOOTS_AND_BOOTIES:Hombre':    '5601952',
        'BOOTS_AND_BOOTIES:Sin género infantil': '4572778',
        'LOAFERS_AND_OXFORDS:Hombre':  '5601954',
        'SAFETY_FOOTWEAR:Hombre':      '4859025',
        'BRAS:Mujer':                  '4922945',
    },
}
```

**15 chart_ids en total: BEKURA 6, SANCORFASHION 9.**
La llave es `"DOMINIO:GÉNERO"`, y el género debe coincidir **exactamente** con el
`value_name` del atributo `GENDER` de ML: `"Hombre"`, `"Mujer"`, `"Niñas"`,
`"Niños"`, `"Sin género"`, `"Sin género infantil"` (comentario en las líneas 12-13).

La función es tres líneas (líneas 38-46):

```python
def get_chart_id(cuenta, domain, gender):
    if not cuenta or not domain or not gender:
        return None
    return CHARTS_BY_ACCOUNT.get(cuenta, {}).get(f'{domain}:{gender}')
```

**Devuelve `None` si falta cualquiera de los tres** — incluido el género.

### 7.2 Cómo se aplica al publicar

`backend/vendor/ml_ready/publisher_core.py:288-308`:

```python
if cuenta:
    domain = (cat_info.get('settings', {}) or {}).get('catalog_domain', '') or ''
    domain = domain.replace('MLM-', '')          # ← se le quita el prefijo
    if domain:
        gender = (prod['ml_attrs'].get('gender')
                  or prod['ml_attrs'].get('GENDER')
                  or prod.get('wc_attrs', {}).get('gender', ''))
        if isinstance(gender, list) and gender:
            gender = gender[0]
        gender = str(gender).strip().strip("[]'\" ")
        chart_id = get_chart_id(cuenta, domain, gender)
        if chart_id and 'SIZE_GRID_ID' not in _attr_ids():
            attributes.append({'id': 'SIZE_GRID_ID', 'value_id': chart_id})
```

Tres cosas que hay que ver aquí:

- El dominio se saca **en vivo** de `cat_info` (la llamada a `/categories/{id}`),
  **no** de la meta `ml_dominio_id`.
- El género sale del producto: primero `ml_attrs['gender']` / `['GENDER']` (las
  metas `ml_attr_<ID>` que dejó la IA de atributos), luego el atributo de
  WooCommerce. Viene con limpieza defensiva porque a veces llega como lista o
  con corchetes pegados.
- **Es la CUENTA la que decide**: BEKURA y SANCORFASHION tienen guías distintas
  para el mismo dominio, porque una guía pertenece a una cuenta de vendedor. Por
  eso `publish_product` recibe `cuenta` y `preview_crear_ml` también
  (`publicar_ready.py:492-494`).

### 7.3 Los dos reintentos

`publisher_core.py:506-516` — si ML responde 400 con
`invalid.fashion_grid.grid_id.values` o `missing.fashion_grid.grid_id.values`, el
publicador **quita el `SIZE_GRID_ID` y reintenta una vez**. Si aun así falla,
se marca como configuración manual (§7.4).

`publisher_core.py:495-504` — hay un reintento hermano para
`invalid.title.gender`: quita `GENDER`/`GENDER_NAME` y reintenta. Ojo con el
efecto lateral: **un producto que perdió GENDER ya no puede matchear su guía de
tallas**, porque la llave es `DOMINIO:GÉNERO`.

### 7.4 Por qué hay SKUs bloqueados, y cuántos son de verdad

El error se etiqueta en `publisher_core.py:637-658`:

```python
if code in ('missing.fashion_grid.grid_id.values', 'invalid.fashion_grid.grid_id.values'):
    manual_reasons.append('GRID_REQUERIDO (configurar guía de tallas en ML)')
…
error_label = f"NEEDS_MANUAL_CONFIG: {' | '.join(manual_reasons)}"
```

y se guarda en la bitácora `ml_backlog` (MySQL).

**La cifra que circula (108 SKUs)** viene de la auditoría del **2026-07-20** sobre
los 131 productos en estado "Ready" (`README.md:6489-6497`): 108 bloqueados por
guía de tallas, 11 por ME1 inactivo, 5 por imágenes chicas, 2 por GTIN.

**Lo que yo medí hoy en `ml_backlog` (solo SELECT, 2026-09-03):**

| Medición | Valor |
|---|---|
| Filas con `GRID_REQUERIDO` | **367** intentos |
| **SKUs distintos** afectados | **125** |
| De ésos, que **nunca** lograron publicarse | **124** |
| Ventana | 2026-04-21 → **2026-08-18** (sigue vivo) |
| Cuentas | los 125 fallaron en **ambas** (BEKURA y SANCORFASHION) |
| Categorías de ML distintas implicadas | **42** |

> Es decir: la cifra "108" es real pero es una **foto de julio sobre los
> 'Ready'**. El bloqueo acumulado es de **125 SKUs**, y el intento más reciente
> es del **18-ago-2026** — no es un pendiente cerrado.

### 7.5 Qué dominios faltan — la lista medida

Cruzando las 42 categorías que fallaron contra la API pública de ML, salen **30
dominios distintos**. De ésos, **24 no tienen NINGUNA guía** en
`size_chart_mapping.py`:

| Dominio SIN guía | SKUs | Ejemplo de categoría |
|---|---|---|
| `DRESSES` | 18 | MLM112156 · Ropa, Bolsas y Calzado > Vestidos |
| `PANTS` | 12 | MLM194175 · Pantalones |
| `SWEATERS_AND_CARDIGANS` | 7 | MLM437522 · Sweaters |
| `BLOUSES` | 5 | MLM194159 · Blusas |
| `UNDERPANTS` | 5 | MLM194115 · Boxers y Trusas |
| `PAJAMAS` | 4 | MLM194132 · Pijamas |
| `SWEATSHIRTS_AND_HOODIES` | 4 + 2 | MLM115350 · Sudaderas y Hoodies · MLM194181 |
| `JACKETS_AND_COATS` | 3 + 1 | MLM112197 · Chamarras · MLM432860 (táctica) |
| `SHIRTS` | 3 | MLM194157 · Camisas |
| `HEELS_AND_WEDGES` | 3 | MLM193324 · Zapatillas y Tacones |
| `TACTICAL_PANTS` | 3 | MLM432858 · Pantalones Tácticos |
| `PANTIES` | 3 | MLM194123 · Calzones |
| `VESTS` | 3 | MLM194211 · Chalecos |
| `SPORT_SHORTS` | 2 + 2 + 1 | MLM424929 / MLM194182 / MLM424842 |
| `SLIPPERS` | 2 | MLM193321 · Pantuflas |
| `SKIRTS` | 2 + 1 | MLM7697 · Faldas · MLM457421 (Disfraces) |
| `SPORT_PANTS` | 1 + 1 + 1 | MLM194184 / MLM424930 / MLM424841 |
| `LEGGINGS` | 1 | MLM429668 · Ropa para Embarazadas > Leggins |
| `SHORTS` | 1 | MLM109276 · Bermudas y Shorts |
| `ROBES` | 1 | MLM420168 · Batas de bebé |
| `DANCE_SNEAKERS_AND_SHOES` | 1 + 1 | MLM432525 / MLM417484 · Calzado de Danza |
| `NIGHTGOWNS` | 1 | MLM194135 · Camisones |
| `SPORT_SKIRTS` | 1 | MLM424858 · Faldas deportivas |
| `PONCHOS` | 1 | MLM188714 · Ponchos |

Y **6 dominios que SÍ tienen guía y aun así fallaron** — éstos son el caso
interesante:

| Dominio CON guía | SKUs que fallaron | Por qué (lo más probable) |
|---|---|---|
| `SANDALS_AND_CLOGS` | 9 | solo hay guía `:Hombre` en ambas cuentas |
| `SNEAKERS` | 6 + 1 + 1 | hay `:Hombre`, `:Mujer`, y `:Sin género` solo en SANCOR |
| `BRAS` | 5 + 2 | solo `:Mujer`; y `README` avisa: *"BRAS con guía pero productos sin atributo GÉNERO también fallan"* |
| `LOAFERS_AND_OXFORDS` | 1 | solo `:Hombre` |
| `BOOTS_AND_BOOTIES` | 1 + 1 | `:Hombre` en ambas, `:Sin género infantil` solo en SANCOR |
| `SAFETY_FOOTWEAR` | 1 | solo `:Hombre`, y solo en SANCORFASHION |

> **La causa raíz no es solo "faltan guías": es que la llave lleva GÉNERO.**
> Un tenis de niño en una cuenta que solo tiene `SNEAKERS:Hombre` falla igual que
> si no hubiera ninguna guía. Y un producto **sin el atributo `GENDER`** falla
> siempre, porque `get_chart_id` devuelve `None` cuando el género viene vacío
> (`size_chart_mapping.py:43-44`).

### 7.6 Cómo se destraba (el procedimiento)

Del `README.md:6494-6497` y del docstring del propio módulo (líneas 6-8):

1. **Crear la guía en ML** — dashboard de ML de la cuenta, o
   `POST /catalog/charts`. **Es por cuenta**: una guía de BEKURA no le sirve a
   SANCORFASHION.
2. **Registrar el `chart_id`** en `CHARTS_BY_ACCOUNT` con la llave
   `"DOMINIO:GÉNERO"` exacta.
3. **Relanzar** la publicación de esos SKUs.
4. Si el producto no tiene atributo `GENDER`, **primero hay que ponérselo** — sin
   género no hay llave.

> ⚠️ **Esto ES un cambio de producción**: `size_chart_mapping.py` está dentro de
> `backend/vendor/` y se despliega desde `main`. Desde esta carpeta de
> conocimiento **no se toca**. Lo que sí se puede hacer aquí es preparar la lista
> exacta de guías que hay que crear y las llaves que habría que añadir.

Hay además una meta `_kubera_size_chart` en Woo (**8 productos**, medido hoy) con
un JSON `{domain, site_id, main_attribute, rows[]}` y estado
`'pending_ml_dashboard'`. **No tiene escritor en este repo**
(`docs/PROMPT_ALMACENAMIENTO_POR_CANAL.md:169`): son guías redactadas a mano,
esperando que alguien las dé de alta en el dashboard de ML.

---

## 8. Dónde MÁS se decide la categoría (y por qué importa)

La categoría no solo determina dónde aparece el producto. **Determina la
comisión, y por lo tanto el precio.**

### 8.1 kubera es el mapa maestro

`channel.product_category` (esquema `channel` de la BD kubera). La lee
`backend/services/channel_read.py:169-199` (`categoria_curada`), que busca por SKU
exacto y, si no, por **prefijo padre** (`CATEG-####`), y **ordena preferiendo
`source='panel'`**:

```sql
order by (pc.sku = %(s)s::citext) desc,
         (pc.source = 'panel') desc
```

> **Medido el 2026-09-03 en kubera (solo SELECT):**
> `channel.product_category` para `mercado_libre` tiene **13,799** asignaciones:
> **`panel` 5,292 · `predictor` 5,227 · `costos_ml` 2,340 · `real` 940**.
> El árbol `channel.categories` tiene **2,737** nodos; hay **2,147** categorías
> distintas en uso. Última asignación: 2026-09-03 06:18 UTC (está vivo).

**Por qué eso importa**: en el barrido del 12-ago se midió que la vieja tabla
MySQL `categorias_ml` y kubera **discrepaban en 2,270 SKUs**, y en todos los
muestreados MySQL traía `predictor` contra el `panel` de kubera. Publicar desde
MySQL era publicar en la categoría que **adivinó** el detector, ignorando la que
un humano ya había corregido — *uno de cada seis SKUs con categoría*
(`crear_producto.py:524-531`, `channel_read.py:177-183`).

### 8.2 La categoría fija la comisión

`backend/services/costos.py`:

- `_resolver_cat_ml(sku)` (línea 680) resuelve la categoría para costear:
  kubera → postmeta `ml_category_id` de Woo → **y si el SKU es una VARIANTE sin
  categoría propia, HEREDA la del padre** (resuelto por `post_parent` de
  WooCommerce, no por el nombre del SKU). Sin esa herencia el costeo revienta con
  422: *"sin categoría no hay comisión, y sin comisión no hay precio que
  guardar"* (caso real: `CAM-0030-IND`/`-QUE`, colchones por talla).
- Con la categoría se pide `GET /sites/MLM/listing_prices` con precio de
  referencia $100 para sacar el `pct_comision` (`costos.py:185-223`).
- Si eso falla, cae a la comisión más frecuente cacheada para ese `ml_cat_id`
  (`costos.py:47-72`) y, en último caso, a `COMISION_FALLBACK`, marcando
  `comision_estimada=True` (`costos.py:251-263`).

**Traducción para un KAM:** cambiar la categoría en el picker **cambia el precio
que se va a publicar**. Por eso el picker vive junto a Costos y el propio texto
del componente lo dice: *"Sin categoría — busca por nombre abajo para poder
generar el costo"* (`CategoriaMLPicker.tsx:119`).

### 8.3 El sync de costos escribe categoría a Woo (y solo una de las dos llaves)

`backend/routers/crear.py:629-652` (`_categoria_ml_meta`), llamado desde
`_sync_woo_costo` (línea 685) cuando la fila de costos trae `ml_cat_id`. Escribe:
`ml_category_id`, `ml_category_name`, `ml_categoria_path`,
`ml_categoria_nivel_1..5`.

**No escribe `ml_categoria_id`.** Es correcto por diseño (el costeo no es una
elección humana), pero tiene la consecuencia de §9.

---

## 9. La trampa medida: lo que ves en el Estudio puede no ser lo que publica

`backend/services/wp_db.py:598` — la lista de metas que el Estudio pide:

```python
"ml_category_id", "ml_categoria_path",
"ml_categoria_nivel_1", … "ml_categoria_nivel_5",
```

**`ml_categoria_id` NO está en esa lista.** Por lo tanto:

- `studio.metadata(sku)["categoria_ml"]["category_id"]` (`wp_db.py:639-643`) es
  **`ml_category_id`** — la del predictor/costeo.
- `routers/productos._categoria_del_canal(sku, "mercado_libre")`
  (`backend/routers/productos.py:571-574`), que es lo que se usa para cruzar el
  producto contra los requisitos del canal, devuelve **esa misma**.
- El publicador, en cambio, usa `ml_categoria_id` (`publicar_ready.py:425-427`).

El breadcrumb visible sí es el bueno (viene de `ml_categoria_path` y los
`nivel_N`), pero **el ID que el Estudio expone puede ser otro**.

> **Medido el 2026-09-03 en `wp_postmeta`:**
> - **2,569** productos tienen `ml_categoria_id` **sin** `ml_category_id`.
> - **185** productos tienen **las dos y son DISTINTAS**.
>
> Ejemplo real (`EST-0054-NEG`), leído por `meta_id` (orden de inserción):
>
> | meta_id | key | valor |
> |---|---|---|
> | 32029 | `ml_category_id` | `MLM437180` |
> | 32030 | `ml_category_name` | `Escritorios` |
> | 581158-581161 | `ml_categoria_nivel_1..3` + `path` | `… > Muebles para el Hogar > Libreros` |
> | 581162 | **`ml_categoria_id`** | **`MLM32652`** |
>
> Comprobado contra la API pública de ML: `MLM32652` = **Libreros**
> (`MLM-BOOKCASES`), `MLM437180` = **Escritorios** (`MLM-HOME_OFFICE_DESKS`).
> Otro: `TEC-0661-BLN` → panel `MLM7533` (Electrónica > Audio > Audífonos)
> contra predictor `MLM6777` (Computación > PC Gaming > Audífonos).
>
> **Se publica en la del panel (la correcta). Pero el ID que ves en el Estudio y
> el que se usa para cruzar requisitos son el otro.** No determiné qué escritor
> dejó ese `ml_category_id` viejo sin actualizar — el endpoint del panel escribe
> las dos llaves desde v0.17.0 (23-jul-2026), así que estos casos son anteriores
> o vienen de un escritor externo a este repo. **NO VERIFICADO.**

**Regla práctica**: si tienes que saber en qué categoría va a salir un producto,
**no mires el ID del Estudio: mira `ml_categoria_id`** (o el `path`, que sí sigue
al panel).

---

## 10. Resumen operativo en 8 líneas

1. La categoría de ML se elige en el **picker del Estudio**, y eso llama a
   `POST /api/crear/categoria-ml`.
2. Ese endpoint valida contra ML, y escribe **7 familias de metas** en Woo +
   avisa a kubera con `source='panel'`.
3. Al publicar mandan, en este orden: **`ml_categoria_id` > `ml_category_id` >
   mapeo `ML: MLM###` de la categoría de Woo**.
4. El buscador del picker es un **predictor**, no un índice: si no aparece,
   **pega el `MLM…`**.
5. El **dominio** decide si la categoría es de catálogo (te pisa el título) y qué
   guía de tallas aplica; se pide **en vivo**, la meta `ml_dominio_id` no la lee
   nadie.
6. Las guías de tallas viven en **`size_chart_mapping.py`** (15 ids), con llave
   **`DOMINIO:GÉNERO` y por CUENTA**. Sin género no hay guía.
7. **125 SKUs** siguen bloqueados por `GRID_REQUERIDO`; faltan **24 dominios de
   ropa** completos y varias combinaciones de género en los 6 que sí tienen.
8. Una categoría mal puesta se corrige en vivo si el item está **activo o
   pausado**; si está **cerrado**, hay que republicar.

---

## 11. CÓMO REUSARLO SIN TOCAR PRODUCCIÓN

Un script suelto puede reproducir **toda la decisión de categoría y de talla** sin
escribir en ningún sistema. Lo único imposible es que la elección quede guardada.

### 11.1 Lo que es GRATIS y PÚBLICO (sin token, sin credenciales)

Verificado el 2026-09-03 con `httpx` sin cabecera de autorización: **HTTP 200**.

| Para qué | Llamada |
|---|---|
| Categoría sugerida por el título | `GET https://api.mercadolibre.com/sites/MLM/domain_discovery/search?q=<título>&limit=3` |
| Ruta completa + dominio de una categoría | `GET https://api.mercadolibre.com/categories/{MLM…}` → `path_from_root`, `settings.catalog_domain` |
| Atributos obligatorios de la categoría | `GET https://api.mercadolibre.com/categories/{MLM…}/attributes` |

⚠️ `limit=1` en `domain_discovery` **devuelve `[]`** (bug de ML anotado en
`crear_producto.py:625`). Usa `limit=3` como mínimo.

Ejemplo mínimo, sin nada del proyecto:

```python
import httpx

def sugerir(titulo: str, n: int = 3):
    r = httpx.get("https://api.mercadolibre.com/sites/MLM/domain_discovery/search",
                  params={"q": titulo, "limit": n}, timeout=20)
    return r.json() if r.status_code == 200 else []

def detalle(cat_id: str):
    d = httpx.get(f"https://api.mercadolibre.com/categories/{cat_id}", timeout=20).json()
    return {
        "id":     cat_id,
        "nombre": d.get("name", ""),
        "ruta":   " > ".join(p["name"] for p in d.get("path_from_root", [])),
        "dominio": (d.get("settings") or {}).get("catalog_domain") or "",
        "es_catalogo": bool((d.get("settings") or {}).get("catalog_domain")),
    }
```

Con eso ya replicas **el 100% del razonamiento de `GET /api/crear/categorias-ml`**
(`crear.py:475-537`): buscar, deduplicar por `category_id`, y completar el `path`
con una llamada por candidato.

### 11.2 Consultar las tallas, offline

`size_chart_mapping.py` **no llama a nada**: es un `dict` y una función de tres
líneas. Cópialo tal cual a tu script (o `import`álo por ruta, en modo lectura) y
pregúntale:

```python
CHARTS_BY_ACCOUNT = { … }   # copiar de §7.1, son 15 entradas

def tiene_guia(cuenta, dominio, genero):
    dominio = dominio.replace("MLM-", "")           # como publisher_core.py:292
    return CHARTS_BY_ACCOUNT.get(cuenta, {}).get(f"{dominio}:{genero}")
```

**Simulador útil que hoy no existe en el panel**: dado un `MLM…` y una cuenta,
decir *"esto va a fallar por guía de tallas"* antes de intentar publicar.

```python
def diagnostico(cat_id, cuenta, genero):
    d = detalle(cat_id)
    if not d["dominio"]:
        return "OK — categoría sin catalog_domain, no pide guía de tallas"
    if not genero:
        return f"BLOQUEA — dominio {d['dominio']} y el producto no tiene GENDER"
    cid = tiene_guia(cuenta, d["dominio"], genero)
    return (f"OK — SIZE_GRID_ID={cid}" if cid
            else f"BLOQUEA — falta guía '{d['dominio'].replace('MLM-','')}:{genero}' en {cuenta}")
```

Todo público, todo sin token, cero escrituras.

### 11.3 Consultar qué categoría tiene HOY un producto (solo lectura)

Tres fuentes, de más fiable a menos:

| Fuente | Cómo | Riesgo |
|---|---|---|
| **kubera** `channel.product_category` | `SELECT` filtrando `channel_id='mercado_libre'`, preferir `source='panel'` | ✅ el mapa maestro |
| **Woo `wp_postmeta`** | `SELECT` de `ml_categoria_id` (¡esa, no `ml_category_id`!) | ✅ es lo que publica |
| API REST de Woo | `GET /wp-json/wc/v3/products/{id}` | ⚠️ pasa por LiteSpeed |

SQL de kubera, listo para pegar (**solo SELECT**):

```sql
select pc.sku, pc.category_id, pc.source, ct.name as categoria
  from channel.product_category pc
  left join channel.categories ct
         on ct.category_id = pc.category_id
        and ct.channel_id  = pc.channel_id
 where pc.channel_id = 'mercado_libre'
   and pc.sku = 'EST-0054-NEG';
```

🔴 **Las tres reglas al conectarte a kubera**, y no son negociables:

1. **SOLO `SELECT`.** Es producción operativa.
2. **NUNCA marques la sesión como read-only** — ni `cn.set_session(readonly=True)`
   ni `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`. El DSN apunta al
   **pooler en modo transacción (6543)** y las conexiones **se comparten entre
   clientes**: la marca se queda pegada y la hereda el backend de producción
   registrando una venta. **Ya reventó dos veces.** Si de verdad necesitas la
   garantía: `BEGIN; SET TRANSACTION READ ONLY; …; ROLLBACK;` (muere con el
   commit), o conéctate al **5432**, o simplemente no marques nada.
3. Si ves `ReadOnlySqlTransaction` en un log de producción, no busques el bug en
   producción: busca qué diagnóstico corrió antes.

Para Woo (`wp_postmeta`), el equivalente en MySQL:

```sql
select p.meta_value as panel, q.meta_value as predictor, r.meta_value as ruta
  from wp_postmeta s
  left join wp_postmeta p on p.post_id=s.post_id and p.meta_key='ml_categoria_id'
  left join wp_postmeta q on q.post_id=s.post_id and q.meta_key='ml_category_id'
  left join wp_postmeta r on r.post_id=s.post_id and r.meta_key='ml_categoria_path'
 where s.meta_key='_sku' and s.meta_value='EST-0054-NEG';
```

Credenciales: `WPDB_NAME` / `WPDB_USER` / `WPDB_PASSWORD` (host: `WPDB_HOST` o,
si está vacío, `DB_HOST`) del `.env`. Las 72 tablas `wp_*`: **lectura directa OK,
DDL/DML no.**

### 11.4 Simular el payload completo, sin publicar

`backend/services/publicar_ready.py:478-505` (`preview_crear_ml`) llama a
`publisher_core.build_payload(prod, token, dry_run=True, cuenta)` y **no crea
nada**: `dry_run=True` se salta el pre-upload de imágenes. Es el mismo código que
publica, sin el `POST /items`. Si tienes token de ML de lectura, es la forma más
fiel de ver qué saldría — incluido el `SIZE_GRID_ID`.

Desde fuera del backend, y **sin token**, se puede llegar bastante lejos copiando
solo la lógica pura: la resolución de precedencia (§4) son cuatro líneas, y
`wc_category_mapping.resolve_ml_category_from_wc()` es una función sin efectos
secundarios (`wc_category_mapping.py:93-111`).

### 11.5 Lo que es IMPOSIBLE sin escribir

- **Que una categoría elegida quede guardada.** El único escritor de
  `ml_categoria_id` es `POST /api/crear/categoria-ml`, que hace `PUT` a
  WooCommerce. No hay forma de fijar la elección sin ese `PUT`.
- **Que una guía de tallas nueva sirva.** Requiere (a) crearla en el dashboard de
  ML de la cuenta —o `POST /catalog/charts`, que es escritura en ML— y (b) editar
  `size_chart_mapping.py`, que vive en `main` y se despliega.
- **Corregir la categoría de un item ya publicado.** Es `PUT /items/{id}` contra
  ML: escritura en el marketplace. (Y solo funciona si el item está activo o
  pausado.)
- **Reprocesar los 125 SKUs bloqueados.** Publicar es escribir.

### 11.6 Ideas de valor que este material habilita sin escribir nada

1. **Reporte "categoría del panel vs. categoría del Estudio"** — cruzar
   `ml_categoria_id` contra `ml_category_id` en `wp_postmeta` y listar las **185**
   divergentes con sus rutas resueltas contra la API pública. Hoy nadie las ve.
2. **Lista de compra de guías de tallas** — la tabla de §7.5 generada al día:
   qué `DOMINIO:GÉNERO` hay que dar de alta en cada cuenta, ordenada por SKUs
   desbloqueados. Es exactamente el insumo que le falta a quien tiene que
   crearlas en el dashboard de ML.
3. **Semáforo previo a publicar** — `diagnostico()` de §11.2 corrido sobre una
   lista de SKUs: dice cuáles van a chocar con `GRID_REQUERIDO`, con
   `catalog_domain` (título pisado) o con falta de `GENDER`, **antes** de gastar
   un intento.
4. **Auditoría del tercer decisor** — listar las **1,029** categorías de Woo con
   patrón `ML: MLM###` y marcar cuáles apuntan a una categoría de ML distinta de
   la del panel para los productos que tienen asignadas. Es el caso CAM-0034-BEI,
   buscado a propósito en vez de esperar a que aparezca.
