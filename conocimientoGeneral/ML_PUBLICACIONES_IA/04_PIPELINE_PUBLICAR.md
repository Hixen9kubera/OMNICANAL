# 04 · El pipeline de publicación en Mercado Libre, de punta a punta

> Extraído de producción el 2026-09-03, commit 1a7da7e.
> ESTO ES UNA COPIA DE CONSULTA. La verdad vive en main; si algo no cuadra,
> gana main y hay que re-extraer.

---

## 0. Para qué sirve este archivo

Estás por tocar (o por entender) el botón **Publicar** del Estudio de Producto.
Este documento cuenta **qué pasa exactamente** desde que le das clic hasta que
Mercado Libre contesta, **qué JSON viaja**, **quién valida qué**, **dónde queda
el registro cuando falla** y —al final— **hasta dónde puedes reusar el código
sin escribir una sola letra en ML**.

Todo lo que dice está verificado contra el código del commit `1a7da7e` y contra
la tabla `ml_backlog` de producción (leída con `SELECT`, el 2026-09-03). Lo que
NO pude verificar va marcado como **[inferido]**.

Este archivo se lee solo. No manda a ningún otro archivo.

---

## 1. TL;DR — trece líneas

1. Hay **una sola puerta** para publicar en ML: `POST /api/publicar/confirmar`.
   Ningún script, cron ni job la usa; solo el Estudio del panel.
2. El endpoint decide entre **ACTUALIZAR** (ya existe la publicación) y **CREAR**
   (no existe, o la que había fue borrada en ML).
3. **CREAR** delega en el pipeline vendorizado `backend/vendor/ml_ready/` — el
   que hizo 1,200+ altas. Ese código **no se toca**; se ajustan los adaptadores.
4. Se publica en **las dos cuentas**: primero `SANCORFASHION`, luego `BEKURA`.
5. El payload sale **siempre** con `listing_type_id: "gold_pro"` (Premium),
   `condition: "new"`, `shipping.mode: "me2"` y `status: "paused"`.
6. **ML ignora el `status: paused` del POST.** La pausa real la hace un PUT
   posterior, verificado y reintentado hasta 4 veces.
7. El **99.95 %** de las altas viajan con `family_name` en vez de `title`
   (5,918 de 5,921 medidas) porque casi toda categoría MLM tiene `catalog_domain`.
   Tu título se **corta a 60 caracteres** ahí dentro, sin avisar.
8. Antes de mandar solo hay **tres cortes duros**: sin categoría ML, sin título,
   precio ≤ 0. Todo lo demás lo valida ML.
9. Cuando ML dice que no, el publicador **reintenta hasta 11 veces** con
   correcciones específicas por código de error, en un solo POST tras otro.
10. Todo intento —bueno o malo— queda en **`ml_backlog`** (MySQL) con el payload
    íntegro y la respuesta de ML. Los éxitos además actualizan **`ml_progress`**.
11. El **preview** (`POST /api/publicar/preview`) **no escribe nada en ML**.
    Verificado línea por línea. Es el botón seguro.
12. La línea exacta donde empieza la escritura en ML es
    `backend/vendor/ml_ready/ml_api.py:305` (`POST /items`), y antes que ella
    `ml_api.py:258` (`POST /pictures`). Nada arriba de eso escribe.
13. Desde el **8-jul-2026 ningún alta pre-sube imágenes**: las 566 altas de
    agosto y septiembre viajaron con `{"source": "https://chunche.shop/..."}` y
    ML se las descarga solo. Medido; la causa está abajo.

---

## 2. Vocabulario mínimo

| Palabra | Qué es aquí |
|---|---|
| **SKU** | La llave que une todo: Woo, ML, Amazon, Odoo. Ej. `DEPO-0151-CAF-PLA`. |
| **wc_id** | El `ID` del post de WooCommerce/WordPress. Puede ser un producto o una **variación**. |
| **cuenta** | `BEKURA` (tienda "Kubera") o `SANCORFASHION` (tienda "San Corpe"). Son dos vendedores distintos en ML. |
| **item_id / MLM** | El identificador de una publicación en ML: `MLM6160173394`. |
| **gold_pro** | El tipo de exposición **Premium** de ML (comisión más alta, meses sin intereses). |
| **family_name** | El "título" cuando la categoría pertenece al **catálogo** de ML. En esas categorías ML **no acepta `title`**. |
| **me2** | Mercado Envíos 2 (ML recoge y envía). Es lo único que manda el publicador. |
| **`ml_progress`** | Tabla MySQL: *"¿en qué cuentas está publicado este SKU y con qué MLM?"* Una fila por `cuenta:sku`. |
| **`ml_backlog`** | Tabla MySQL: *bitácora de cada intento*, con el payload y la respuesta completos. |
| **`ml_attr_<ID>`** | Metas de WooCommerce donde la IA deja los atributos de ML ya resueltos. Ej. `ml_attr_COLOR`. |

---

## 3. El camino completo — diagrama de llamadas

### 3.1 Vista de pájaro

```
NAVEGADOR (Estudio de Producto)
  frontend/components/ProductStudio.tsx:867   reqPublicar()   arma el JSON
  frontend/components/ProductStudio.tsx:904   publicarConfirmar(...)
  frontend/lib/api.ts:575                     POST /api/publicar/confirmar
        │
        ▼
BACKEND · ROUTER
  backend/routers/publicar.py:122   confirmar(req)      ← permiso RBAC "operador"
  backend/routers/publicar.py:140     publicar.confirmar(req.a_dict())
  backend/routers/publicar.py:141     _anotar_seguro(...)  → bitácora de PERSONA
        │
        ▼
BACKEND · SERVICIO (orquestador)
  backend/services/publicar.py:454  confirmar(req)
  backend/services/publicar.py:456    _rellenar_desde_guardado(req)   ← enrich.channel_content
  backend/services/publicar.py:458    _confirmar_ml(req)
        │
        ├── ¿dónde está publicado?  publicar.py:685 → _ml_publicaciones(sku)  (publicar.py:144)
        ├── ¿siguen vivos?          publicar.py:693 → _estados_items_ml(pubs) (publicar.py:549)
        │
        ├─────► CAMINO A · ACTUALIZAR   (hay publicaciones vivas)
        │         publicar.py:703  _update_ml_una(cuenta, item_id, title, attrs, desc)
        │         publicar.py:604    PUT  https://api.mercadolibre.com/items/{id}
        │         publicar.py:629    PUT  .../items/{id}/description   (404 → POST en :632)
        │         publicar.py:704  _guardar_backlog_ml(...)            → ml_backlog + ml_progress
        │
        └─────► CAMINO B · CREAR        (no hay ninguna, o todas están muertas)
                  publicar.py:688 / :698 / :713   _crear_ml(sku, wc_id, campos, cuentas)
                  publicar.py:663    publicar_ready.crear_ml(...)
                        │
                        ▼
                  ADAPTADOR (nuestro)
                  backend/services/publicar_ready.py:508  crear_ml()
                    :517  configurar()                    inyecta tokens + gancho de backlog
                    :518  construir_prod(sku, wc_id, campos)   → dict con forma de producto Woo
                    :521  for cuenta in ["SANCORFASHION", "BEKURA"]
                    :538    publisher_core.publish_product(prod, token, False, cuenta)
                    :571    asegurar_pausado(item_id, token)   verifica la pausa contra ML
                        │
                        ▼
                  VENDOR — NO SE TOCA (backend/vendor/ml_ready/)
                  publisher_core.py:374  publish_product()
                    :389   build_payload(prod, token, dry_run, cuenta)   (publisher_core.py:170)
                    :403   ml_api.create_item(payload, token)            → POST /items
                    :405-618  ~11 reintentos por código de error de ML
                    :679   ml_api.pause_item(item_id, token)             → PUT status=paused
                    :691   ml_api.update_description(...)                → PUT/POST /description
                    :664 / :709  save_backlog(...)  ← gancho → publicar_ready.py:62 _backlog_ml
```

### 3.2 Qué hace cada pieza, con detalle

#### a) El navegador arma la petición — `frontend/components/ProductStudio.tsx:867-886`

```json
{
  "canal": "mercado_libre",
  "cuenta": "BEKURA",
  "sku": "DEPO-0151-CAF-PLA",
  "wc_id": 118925,
  "item_id": null,
  "campos": {
    "titulo": "...", "descripcion": "...", "highlights": "...",
    "bullets": [], "atributos": [{"nombre": "COLOR", "valor": "Negro"}],
    "backend_search_terms": null,
    "precio_regular": 247.75, "peso": 0.698,
    "largo": 24, "ancho": 6, "alto": 6
  }
}
```

`cuenta` e `item_id` **se ignoran en el camino de ML**: el backend descubre por
su cuenta en qué cuentas está publicado. Solo importan para Amazon/TikTok/Temu.

#### b) El router — `backend/routers/publicar.py:122-170`

Tres cosas y nada más:

1. Llama a `publicar.confirmar`.
2. Anota en la **bitácora de persona** (`ops.process_log`, vía
   `services/bitacora.py:55` `PUBLICAR`), con tres desenlaces: `ok`,
   `rechazado` (HTTPException = validación) y `error` (excepción).
   Existe para poder contestar *"¿cuántas publicaciones lleva Thalía?"* —
   `ops.channel_submissions` guarda el qué y el cuándo, pero no el quién.
3. En caso de excepción manda **alerta a Slack** (`publicar.py:160-169`) porque
   al usuario solo le llega el genérico "ERROR DE CONEXIÓN" y casi nunca lo reporta.

Detalle que costó cuatro publicaciones reales el 1-sep-2026 y está documentado
en el propio archivo (`routers/publicar.py:100-113`): **la anotación va dentro de
`_anotar_seguro`**, porque una llamada directa que arma mal sus argumentos
revienta *antes* de entrar a la función a prueba de fallos, y el `except` del
endpoint convierte un éxito en "error" a los ojos del usuario.

Permiso: `POST /api/publicar/confirmar` exige rol **`operador`**
(`backend/core/rbac.py:143`).

#### c) Relleno desde lo guardado — `backend/services/publicar.py:412-451`

Antes de nada, se completan los campos **vacíos** del formulario con lo que
haya guardado en `enrich.channel_content` (tabla de kubera,
`services/channel_content.py:58`).

**Precedencia: el formulario manda.** Lo guardado solo rellena huecos
(`publicar.py:446`: `if v and not campos.get(k)`). Un campo presente pero vacío
cuenta como ausente. Si la BD no contesta, no lanza: publica con lo que traiga
el formulario.

Se ejecuta **igual en preview y en confirmar** (`publicar.py:181` y `:456`) a
propósito: si la vista previa no leyera lo guardado y el envío sí, el modal
enseñaría una cosa y se publicaría otra.

#### d) ¿Dónde está publicado? — `backend/services/publicar.py:144-172`

```
if settings.supabase_read_publicaciones:      # publicar.py:155
    channel_read.publicaciones_ml([sku])      # → kubera: channel.listings
else:
    SELECT cuenta, ml_item_id FROM ml_progress WHERE sku=%s AND ml_item_id<>''
```

El default en código es `False` (`backend/config.py:749`), o sea **`ml_progress`**;
el valor vivo en Railway no lo pude verificar desde aquí — **[inferido: sigue en
MySQL, porque `ml_progress` tiene 4,194 filas y se sigue escribiendo]**.

**Sin `try/except` a propósito** (comentario en `publicar.py:152-154`): si kubera
no contesta, responder "no hay publicaciones" sería una mentira con consecuencias
— ese resultado decide a qué cuentas se les manda la actualización, y un `None`
se convertiría en un alta duplicada.

#### e) ¿Siguen vivas? — `backend/services/publicar.py:549-582`

Para cada fila registrada se hace `GET /items/{item_id}` y se decide:

```python
vivo = status != "closed" and "deleted" not in sub_status   # publicar.py:576
```

- `404` → `vivo=False, status="not_found"`.
- **Ante duda** (sin token, timeout, 5xx) → **se asume VIVO**: mejor fallar un
  update que crear un duplicado por un error transitorio.

Existe por los casos `MOD-0496-NUDE` y `CAM-0034-BEI` (22-jul): borradas a mano
en el seller central con la bitácora intacta, así que el botón intentaba
actualizar ítems muertos y nunca re-creaba.

El desenlace (`publicar.py:693-725`):

| Situación | Qué hace |
|---|---|
| Ninguna fila en registro | `_crear_ml` en **ambas** cuentas |
| Todas muertas | `_crear_ml` **solo** en las cuentas muertas |
| Unas vivas, otras muertas | actualiza las vivas **y** re-crea las muertas |
| Todas vivas | actualiza y ya |

Al re-crear, el nuevo `ml_item_id` pisa la fila vieja de `ml_progress`
(`ON DUPLICATE KEY UPDATE`) — la bitácora se cura sola.

#### f) `construir_prod` — el traductor — `backend/services/publicar_ready.py:372-473`

Reconstruye el dict con **forma de producto de WooCommerce REST** que espera el
vendor, pero leyendo **la BD de WordPress directo** (`wp_db`) porque la REST de
Woo devuelve 403 por el CDN de Hostinger.

Reglas que valen oro:

| Campo | De dónde sale | Truco |
|---|---|---|
| `title` | `campos.titulo` → si vacío, `post_title` | lo del Estudio manda |
| `description` | `campos.descripcion` → `post_content` → `post_excerpt`, y pasa por `_html_a_plano` (`publicar_ready.py:350`) | ML solo acepta texto plano |
| `price` | `campos.precio_regular` → `_regular_price` → **precio regular de las variantes** → `_price` | `publicar_ready.py:394-398`. El paso por variantes existe porque un **padre variable no guarda `_regular_price` propio** y sin él se caía al `_price`, que es el de **OFERTA**: `CAM-0030` se publicó en $6,514.97 en vez de $7,755.92 |
| `stock` | `_stock_odoo` → `_stock` | `publicar_ready.py:400` |
| `ml_category_id` | meta **`ml_categoria_id`** (picker del PANEL) → si no, `ml_category_id` (predictor de Crear) | `publicar_ready.py:425-427`. **La humana manda** — caso `TEC-1812-NEG`, publicado en "Máquinas de Coser" siendo "Máquinas Sexuales" |
| `wc_categories` | **`[]` si hay categoría elegida en el panel**, si no las de Woo | `publicar_ready.py:457`. Si se pasaran, el vendor las prefiere y **revierte la elección del picker** (caso `CAM-0034-BEI`) |
| `ml_attrs` | todas las metas `ml_attr_*`, sin el prefijo | `publicar_ready.py:458` |
| `wc_attrs` | atributos de Woo `{nombre.lower(): primer_valor}`, **pisados** por `campos.atributos` | `publicar_ready.py:413-418` |
| `images` | `wp_db.imagenes(wc_id)` — URLs públicas de chunche.shop | |
| `weight/length/width/height` | `campos.*` → `_weight/_length/_width/_height` | |

**Ojo, esto sorprende a todo el mundo:** en el camino de **CREAR**, los atributos
que escribiste en el Estudio **no viajan tal cual**. Entran a `wc_attrs` y de ahí
pasan por el mapeador del vendor, que los traduce a IDs de ML y busca su
`value_id`. En el camino de **ACTUALIZAR** sí viajan crudos (ver §7.3).

---

## 4. El payload — qué se le manda a Mercado Libre, de verdad

### 4.1 Alta real que ML aceptó (`ml_backlog` id 6489, 2026-09-02 21:23:26)

`DEPO-0151-CAF-PLA`, cuenta `BEKURA`, resultado `HTTP 201`, item `MLM6160173394`,
descripción `HTTP 200`, `pics_preuploaded = 0`.

```json
{
  "category_id": "MLM118805",
  "price": 247.75,
  "currency_id": "MXN",
  "available_quantity": 1,
  "buying_mode": "buy_it_now",
  "listing_type_id": "gold_pro",
  "condition": "new",
  "status": "paused",
  "pictures": [
    {"source": "https://chunche.shop/wp-content/uploads/2026/08/HTB1ZNXKmruWBuNjSszgq6z8jVXaZ.webp"},
    {"source": "https://chunche.shop/wp-content/uploads/2026/08/HTB19LuxmrSYBuNjSspfq6AZCpXai.webp"}
  ],
  "attributes": [
    {"id": "SELLER_SKU",               "value_name": "DEPO-0151-CAF-PLA"},
    {"id": "BRAND",                    "value_name": "Ferrahome"},
    {"id": "MODEL",                    "value_name": "A047"},
    {"id": "INVOICE_PRODUCT_NAME",     "value_name": "Set 5 espatulas para parrilla de acero inoxidable con mango de madera"},
    {"id": "MEASURE_UNIT_DESCRIPTION", "value_name": "Pieza"},
    {"id": "IVA_FOR_RESALE",           "value_id":   "81861616"},
    {"id": "IEPS",                     "value_id":   "82021095"},
    {"id": "PART_NUMBER",              "value_name": "DEPO-0151-CAF-PLA"},
    {"id": "EMPTY_GTIN_REASON",        "value_id":   "17055161", "value_name": "Otra razón"},
    {"id": "SELLER_PACKAGE_WEIGHT",    "value_name": "698 g"},
    {"id": "SELLER_PACKAGE_LENGTH",    "value_name": "24 cm"},
    {"id": "SELLER_PACKAGE_WIDTH",     "value_name": "6 cm"},
    {"id": "SELLER_PACKAGE_HEIGHT",    "value_name": "6 cm"},
    {"id": "TOTAL_LENGTH",             "value_name": "24 cm"},
    {"id": "WIDTH",                    "value_name": "6 cm"},
    {"id": "WEIGHT",                   "value_name": "698 g"}
  ],
  "sale_terms": [
    {"id": "WARRANTY_TYPE", "value_id":   "2230280"},
    {"id": "WARRANTY_TIME", "value_name": "30 días"}
  ],
  "shipping": {
    "mode": "me2",
    "local_pick_up": false,
    "free_shipping": true
  },
  "family_name": "Set 5 espatulas parrilla acero inoxidable mango madera"
}
```

**La descripción NO va en este JSON.** Se sube en una llamada aparte, después de
que el ítem existe (`publisher_core.py:691`).

### 4.2 Alta real con imágenes pre-subidas (`ml_backlog` id 5217, 2026-07-08)

Es el mismo molde, pero muestra las dos diferencias que importan: `pictures` por
`id` en vez de `source`, y atributos resueltos a `value_id`.

```json
{
  "category_id": "MLM187612",
  "price": 490.01,
  "currency_id": "MXN",
  "available_quantity": 270,
  "buying_mode": "buy_it_now",
  "listing_type_id": "gold_pro",
  "condition": "new",
  "status": "paused",
  "pictures": [
    {"id": "736682-MLM113069636518_072026"},
    {"id": "845685-MLM113069046772_072026"},
    {"id": "979660-MLM113069489060_072026"},
    {"id": "950853-MLM114291743477_072026"}
  ],
  "attributes": [
    {"id": "SELLER_SKU",            "value_name": "TEC-1031-NEG"},
    {"id": "BRAND",                 "value_id":   "7259182"},
    {"id": "MODEL",                 "value_name": "FH-DB6IN1"},
    {"id": "COLOR",                 "value_id":   "52049"},
    {"id": "SALE_FORMAT",           "value_id":   "1359391"},
    {"id": "WEIGHT",                "value_name": "20.7 kg"},
    {"id": "UNITS_PER_PACK",        "value_name": "1"},
    {"id": "MAIN_COLOR",            "value_id":   "2450295"},
    {"id": "WITH_WEIGHT_INDICATOR", "value_id":   "242084"},
    {"id": "DUMBBELL_MATERIAL",     "value_id":   "511071"},
    {"id": "WITH_COATING",          "value_id":   "242084"},
    {"id": "SHAPE",                 "value_id":   "39977660"},
    {"id": "IS_ADJUSTABLE",         "value_id":   "242085"},
    {"id": "COATING_MATERIALS",     "value_id":   "511081"},
    {"id": "LENGTH",                "value_name": "58 cm"},
    {"id": "PART_NUMBER",           "value_name": "TEC-1031-NEG"},
    {"id": "EMPTY_GTIN_REASON",     "value_id":   "17055161", "value_name": "Otra razón"},
    {"id": "SELLER_PACKAGE_WEIGHT", "value_name": "20000 g"},
    {"id": "SELLER_PACKAGE_LENGTH", "value_name": "56 cm"},
    {"id": "SELLER_PACKAGE_WIDTH",  "value_name": "24 cm"},
    {"id": "SELLER_PACKAGE_HEIGHT", "value_name": "15 cm"},
    {"id": "GTIN",                  "value_name": "0000000000000"}
  ],
  "sale_terms": [
    {"id": "WARRANTY_TYPE", "value_id": "2230280"},
    {"id": "WARRANTY_TIME", "value_name": "30 días"}
  ],
  "shipping": {"mode": "me2", "local_pick_up": false, "free_shipping": true},
  "family_name": "Set de mancuernas y barra ajustable 20kg cemento"
}
```

Notas de este ejemplo: el GTIN `0000000000000` es el **placeholder**
(`publisher_core.py:454`) y convive con `EMPTY_GTIN_REASON`; ML lo aceptó (201).

### 4.3 De dónde sale cada campo

| Campo del payload | Valor | Se fija en |
|---|---|---|
| `category_id` | meta `ml_categoria_id` del panel, o el mapeo de la categoría WC si esta lo sobrescribe | `publisher_core.py:184-198` |
| `price` | precio **regular** (nunca el de oferta) | `publicar_ready.py:394-398` |
| `currency_id` | `"MXN"` fijo | `publisher_core.py:45` |
| `available_quantity` | `prod['stock']`, o `1` si es 0/vacío | `publisher_core.py:212` |
| `buying_mode` | `"buy_it_now"` fijo | `publisher_core.py:48` |
| `listing_type_id` | **`"gold_pro"` fijo** | `publisher_core.py:46` |
| `condition` | `"new"` fijo | `publisher_core.py:47` |
| `status` | `"paused"` — **ML lo ignora**, ver §6 | `publisher_core.py:350` |
| `pictures` | hasta **10**; `{"id": …}` si se pre-subió, `{"source": url}` si no | `publisher_core.py:327-340` |
| `attributes` | ver bloque de abajo | `publisher_core.py:220-321` |
| `sale_terms` | `WARRANTY_TYPE` (vendedor) + `WARRANTY_TIME` | `publisher_core.py:118-163` |
| `shipping.mode` | `"me2"` fijo | `publisher_core.py:355` |
| `shipping.free_shipping` | `price > 149.0` | `publisher_core.py:51, 324` |
| `title` / `family_name` | `family_name` (**recortado a 60**) si la categoría tiene `catalog_domain`; si no, `title` completo | `publisher_core.py:361-365` |

**Los `attributes`, en el orden en que se arman** (`publisher_core.py:220-321`):

1. `BRAND` → **`"Ferrahome"`** si el mapeador no trajo uno (`publisher_core.py:50, 228`).
2. `SELLER_SKU` → el SKU (`:232`).
3. Todo lo que devuelve `build_attributes(...)` (`attribute_mapper.py:663`):
   busca cada atributo de la categoría en `ml_attrs` (por ID de ML, por nombre,
   normalizado) y si no, en `wc_attrs` vía el diccionario `WC_TO_ML_ID`
   (~600 nombres en español → IDs de ML, `attribute_mapper.py:18-620`).
4. `MODEL` → `ml_attrs['model']`, o el título recortado a 60 (`:239-241`).
5. `PART_NUMBER` → el SKU (`:244-245`).
6. `MANUFACTURER` → el valor de `BRAND`, solo si la categoría lo pide (`:248-252`).
7. `GTIN` → `_barcode` / `ml_attr_gtin|ean|upc` / `_gtin`, solo si existe (`:254-260`).
8. `EMPTY_GTIN_REASON` → **siempre**, `value_id: "17055161"` ("Otra razón") (`:261-262`).
9. Las **cuatro dimensiones de paquete juntas o ninguna**, en enteros con unidad
   (`"698 g"`, `"24 cm"`) — y solo si la **densidad** cae entre 0.001 y 30 g/cm³
   (`:264-286`).
10. `SIZE_GRID_ID` — solo ropa/calzado, buscado por `(cuenta, dominio, género)`
    en `size_chart_mapping.py:14-46` (`:288-308`).
11. `DEPTH` → `length` si la categoría lo pide (`:310-314`).
12. **Características secundarias**: `build_secondary_attributes`
    (`attribute_mapper.py:794`) rellena los atributos **opcionales** de la
    categoría con lo que encuentre en `wc_attrs` / `ml_attrs` / dimensiones.

> ⚠️ **La trampa que produce datos falsos y aceptados.**
> `attribute_mapper.py:735-737`: si un atributo es **requerido** por la categoría
> y no hay ningún valor del producto, se manda **el primer valor de la lista de
> valores permitidos**. Y en `:724-725`, si hay valor pero no hace match, también.
> Por eso en el ejemplo aparecen `IVA_FOR_RESALE` e `IEPS` con `value_id` que
> nadie eligió. ML lo acepta —es un valor legal de la categoría— pero **puede ser
> el equivocado**. Si un atributo importa, escríbelo en el producto.

---

## 5. Validaciones: quién dice que no, y cuándo

### 5.1 Antes de mandar (nuestras)

**Cortes duros — el payload no se arma y no se llama a ML:**

| Corte | Dónde | Mensaje que ve el usuario |
|---|---|---|
| Sin `ml_category_id` | `publisher_core.py:199-201` | "Faltan datos: categoría ML (`ml_category_id`)" |
| Sin título | `publisher_core.py:203-205` | "Faltan datos: título" |
| Precio ≤ 0 | `publisher_core.py:207-209` | "Faltan datos: precio" |
| Nada que enviar (ni título, ni atributos, ni descripción) — solo camino ACTUALIZAR | `publicar.py:682-683` | "No había nada que enviar." |
| Sin `wc_id` | `publicar.py:657-659` | "Sin wc_id: no se puede leer el producto de WooCommerce." |
| Sin BD de WordPress | `publicar.py:660-661` | "Sin conexión a la BD de WordPress (configura WPDB_* en Railway)." |
| Sin token de la cuenta | `publicar_ready.py:523-525` | "Sin token para BEKURA" |

**Podas silenciosas — se quita algo del payload y se sigue:**

| Poda | Dónde |
|---|---|
| Atributos de tipo `grid_id`, `grid_row_id`, `picture` — nunca se mandan | `attribute_mapper.py:623, 691` |
| Atributo `number` con valor no numérico (ej. `"N/A"`) → se omite | `attribute_mapper.py:649-653` |
| Atributo `number_unit` sin unidad válida ni `default_unit` → se omite | `attribute_mapper.py:890-914` |
| Atributo con lista de valores permitidos, sin match y **no requerido** → se omite | `attribute_mapper.py:726` |
| Dimensiones de paquete con densidad fuera de `[0.001, 30] g/cm³` → se omiten **las cuatro** | `publisher_core.py:270-276` |
| Imágenes por encima de **10** | `publisher_core.py:44, 327` |

**Avisos que solo se pintan (no bloquean):**

- Título de más de **60** caracteres (`publicar.py:30, 251-252`). El aviso sale en
  el preview; **el envío no lo corta en `title`, pero sí en `family_name`**
  (`publisher_core.py:363`, `[:60]`), que es el 99.95 % de los casos.
- "N imagen(es) se pre-subirán a ML (escaladas a ≥500×250) al confirmar"
  (`publicar.py:246`).
- "La publicación anterior fue eliminada en ML — se CREARÁ una nueva (pausada)"
  (`publicar.py:234-237`).

### 5.2 Lo que valida Mercado Libre (y solo se sabe al mandar)

Todo lo demás. Estas son las familias reales, medidas sobre los 2,532 fallos de
`ml_backlog`:

| Familia de error | Fallos | Qué significa |
|---|---|---|
| `HTTP 400` genérico | 1,372 | validación de ML que no cae en ninguna categoría con nombre |
| `NEEDS_MANUAL_CONFIG` | 472 | necesita a una persona (ver desglose abajo) |
| `GEMINI_ERROR` | 317 | **no es de ML**: el editor de imágenes con IA rechazó una foto y anota en la misma tabla |
| `GTIN_INVALIDO` | 207 | la categoría exige código de barras real |
| `BODY_INVALID_FIELDS` | 101 | el cuerpo no trae lo que la categoría pide (p. ej. `family_name`) |
| `HTTP 401` | 42 | token de ML muerto |
| `HTTP 500` / `429` | 1 / 1 | ML de mal humor |

Desglose de `NEEDS_MANUAL_CONFIG` (472):

| Motivo | Veces |
|---|---|
| `GRID_REQUERIDO` — falta guía de tallas en ML | 367 |
| `ME1_INACTIVO` — la categoría no admite ME2 (ver §7.4) | 71 |
| `IMAGES_TOO_SMALL` — subir imágenes ≥500×250 al producto en Woo | 32 |
| `AI_POLICY` — Gemini rechazó una imagen | 2 |

Ejemplos textuales de respuestas reales de ML:

```json
{"cause": [{"department": "supply", "cause_id": 7810, "type": "error",
  "code": "item.attribute.missing_conditional_required",
  "references": ["item.attributes"],
  "message": "The attributes [GTIN] are required for category [MLM187128]. Check the attribute is present in the attributes list or in all variation's attributes_combination or attributes."}],
 "message": "Validation error", "error": "validation_error", "status": 400}
```

```json
{"cause": [{"department": "structured-data", "cause_id": 3510, "type": "error",
  "code": "invalid.item.attribute.values", "references": ["item.name"],
  "message": "Attribute [MAIN_COLOR] is not valid, item values [(null:['Negro'])]"}],
 "message": "Validation error", "error": "validation_error", "status": 400}
```

Ese segundo es del **camino ACTUALIZAR** y su causa está en §7.3.

---

## 6. Premium (`gold_pro`): qué significa y dónde se fija

**Qué significa.** En ML el `listing_type_id` decide la exposición y la comisión.
`gold_special` es **Clásica**; **`gold_pro` es Premium**: más comisión para el
vendedor y **meses sin intereses** para el comprador. El catálogo de Kubera está
100 % en Premium, y **`costos.py` calcula precios asumiendo comisión Premium**.
Si alguien publicara una Clásica, el precio calculado estaría mal.

**Dónde se fija.** Una sola línea, una sola constante:

```python
# backend/vendor/ml_ready/publisher_core.py:46
DEFAULT_LISTING_TYPE = "gold_pro"      # Premium
```

y se copia al payload en `publisher_core.py:348`:

```python
'listing_type_id':    DEFAULT_LISTING_TYPE,
```

**No hay override, no hay variable de entorno, no hay parámetro.** Todo lo que
crea el pipeline nace Premium. Y ese archivo está bajo la regla de la casa
nº 1: `backend/vendor/` **no se toca**.

**Medido en `ml_backlog`** (2026-09-03), sobre las 5,921 altas con payload:

| `listing_type_id` | Altas | Primera | Última |
|---|---|---|---|
| `gold_special` (Clásica) | 2,109 | 2026-03-17 19:58 | 2026-05-08 17:01 |
| `gold_pro` (Premium) | 3,812 | 2026-05-12 21:00 | 2026-09-02 21:23 |

O sea: **la constante cambió a `gold_pro` entre el 8 y el 12 de mayo de 2026**, y
desde entonces no ha vuelto a producir una Clásica. (El "17-jul" que menciona
`CLAUDE.md` es la fecha en que el **catálogo entero** quedó migrado a Premium —
las publicaciones viejas se convirtieron; el publicador ya llevaba dos meses
naciendo Premium.)

---

## 7. Manejo de errores

### 7.1 Los reintentos dentro de ML — `publisher_core.py:405-618`

`publish_product` **no se rinde al primer no**. Manda el POST, lee el `cause` de
la respuesta y corrige. En orden:

| # | Se dispara con | Corrección | Línea |
|---|---|---|---|
| 1 | `HTTP 401` | refresca el token de la cuenta y reintenta | `:405-413` |
| 2 | `HTTP ≥ 500` | espera **15 s** y reintenta | `:416-419` |
| 3 | `item.attribute.missing_conditional_required` con `GTIN` | busca GTIN: `_barcode` de Woo → catálogo de ML (`/sites/MLM/search`) → **UPC Item DB** (API pública) → placeholder `0000000000000` | `:421-463` |
| 4 | `item.attribute.invalid_sale_units` | agrega `UNITS_PER_PACK = 1` | `:465-473` |
| 5 | `item.pictures.invalid_size` | re-sube las imágenes con escalado automático | `:475-492` |
| 6 | `invalid.title.gender` | **quita** `GENDER`/`GENDER_NAME` | `:494-504` |
| 7 | `invalid/missing.fashion_grid.grid_id.values` | **quita** `SIZE_GRID_ID` | `:506-516` |
| 8 | `item.attribute.value_name.invalid` + "type picture" | quita esos atributos | `:518-530` |
| 9 | `item.attribute.invalid_sale_units` (2ª vuelta) | quita `SALE_FORMAT`, `UNITS_PER_PACK`, `UNITS_PER_PACKAGE` para vender como unidad | `:532-542` |
| 10 | `invalid(.format).seller.package.dimensions` | dimensiones por defecto: **1 kg, 30×20×15 cm** | `:544-557` |
| 11 | `missing.seller.package.dimensions` | las mismas por defecto | `:559-571` |
| 12 | `sale_term.invalid_value_id` / `value_id_required` | **lee el `value_id` correcto del propio mensaje de error de ML** con un regex, y si no, cae a `6150835` | `:573-596` |
| 13 | `product_identifier.invalid_format` | reintenta **sin GTIN**, solo con `EMPTY_GTIN_REASON` | `:598-618` |

Cada uno de esos es **un POST más a `/items`**. Un alta difícil puede golpear la
API media docena de veces antes de rendirse.

Y por encima, en el adaptador, hay **hasta 3 intentos por cuenta**
(`publicar_ready.py:55, 535`, backoff de 2 s y 4 s), **salvo** cuando el error es
**determinista** — `gtin_error` o `needs_manual_config` — porque el mismo payload
va a fallar igual y solo spamearía a ML (`publicar_ready.py:546-549`).

### 7.2 La pausa, que es más frágil de lo que parece

`POST /items` **ignora** el `status: "paused"` del payload y crea el ítem
**activo**, salvo en categorías de catálogo. Está medido y escrito en
`publicar_ready.py:260-270`: de las creaciones con HTTP 201, **2,670 respondieron
`active` y solo 310 `paused`**, aun cuando el payload llevaba `paused` en el 100 %
de los casos.

Lo único que las pausa es el `PUT` posterior. Y hay **dos** capas:

1. `publisher_core.py:679` — `ml_api.pause_item(...)`, un solo intento, sin
   verificar. Si ML lo rechaza (por ejemplo mientras el ítem está en
   `picture_download_pending`) o hay timeout, la publicación **se queda activa
   en silencio**.
2. `publicar_ready.py:294-318` — `asegurar_pausado(...)`: hasta **4 intentos**,
   leyendo el estado real contra ML entre uno y otro, con espera creciente
   (1.5 s, 3 s, 4.5 s, 6 s). Si no lo logra, escribe
   `NO_PAUSADO: quedó status=… sub_status=… tras N intentos` en `ml_backlog`
   (`publicar_ready.py:321-345`) y devuelve al panel el aviso *"La publicación se
   creó pero quedó activa: páusala a mano en Mercado Libre (MLM…)"*.

### 7.3 El camino ACTUALIZAR manda los atributos **crudos**

Esto no es una opinión, es un fallo medido de anteayer.

En `publicar.py:676-680` los atributos del formulario se convierten así:

```python
attrs = [
    {"id": a["nombre"], "value_name": str(a["valor"]).strip()}
    for a in (campos.get("atributos") or [])
    if a.get("nombre") and str(a.get("valor") or "").strip()
]
```

Sin mapeador, sin `WC_TO_ML_ID`, sin buscar `value_id`. Lo que escribiste es lo
que viaja. Payload real (`ml_backlog` id 6479, `ACC-0275`, `SANCORFASHION`,
2026-09-02 17:46, **HTTP 400**):

```json
{
  "title": "Guantes Calidos Termicos Resistentes Al Viento Deportivos",
  "attributes": [
    {"id": "BRAND",      "value_name": "Ferrahome"},
    {"id": "MODEL",      "value_name": "FH-5076"},
    {"id": "Color",      "value_name": "Negro"},
    {"id": "Talla",      "value_name": "XL | L | M"},
    {"id": "SIZE",       "value_name": "XL | L | M"},
    {"id": "MAIN_COLOR", "value_name": "['Negro']"},
    {"id": "GENDER",     "value_name": "['Hombre']"}
  ],
  "description": "Estos guantes son ideales para salir a andar en bici…"
}
```

Tres cosas mal en un solo envío: `"Color"` y `"Talla"` **no son IDs de ML** (los
IDs son `COLOR` y `SIZE`); y `"['Negro']"` es una **lista de Python convertida a
texto** que se coló desde el generador de atributos. ML contestó
`invalid.item.attribute.values` para `MAIN_COLOR` y `GENDER`.

**Consecuencia práctica para un KAM:** si actualizas atributos desde el Estudio,
usa **IDs de Mercado Libre en mayúsculas** (`COLOR`, `SIZE`, `MAIN_COLOR`,
`GENDER`) y valores en texto plano. Si necesitas la traducción automática, la
única vía que la hace es **crear**, no actualizar.

También aquí vive la **auto-sanación de `family_name`** (`publicar.py:614-625`):
ML prohíbe cambiar el `title` de un ítem catalogado (`cause 374`). Si eso pasa,
se reintenta **sin `title`** conservando los `attributes`; y si el título era lo
único que iba, se salta el PUT y se actualiza solo la descripción.

### 7.4 El error que manda a la gente a un botón que no existe

ML devuelve `shipping.lost_me1_by_user` y el vendor lo traduce a
*"ME1_INACTIVO (activar Mercado Envíos 1 en dashboard ML)"*. **Ese botón no
existe**: ML descontinuó ME1 y las preferencias de envío de ambas cuentas
devuelven `modes: []`.

La causa real es la **categoría**: no admite `me2`, que es lo único que manda el
publicador (`publisher_core.py:355`). Verificado en vivo:

```
Monederos y Carteras (MLM5527)   shipping_options: ['carrier', 'custom']
Gorros (MLM174552)               shipping_options: ['custom', 'carrier']
```

Como `vendor/` no se toca, el mensaje **se reescribe en el adaptador**
(`publicar_ready.py:139-174`) a `CATEGORIA_SIN_ME2 (categoría MLM…)`, explicando
que hay que definir un costo de envío propio — una decisión de negocio, no un
ajuste técnico. Son **71 fallos** en la bitácora.

### 7.5 Dónde queda el registro — las bitácoras del publicador

#### `ml_backlog` (MySQL `u531713409_kubera_ml`) — el archivo forense

Escrita por **dos** rutas: el gancho del vendor para las **altas**
(`publicar_ready.py:62-113`) y directamente para las **actualizaciones**
(`publicar.py:503-533`).

```sql
CREATE TABLE `ml_backlog` (
  `id`               int(11) NOT NULL AUTO_INCREMENT,
  `run_key`          varchar(150) NOT NULL COMMENT 'cuenta:sku',
  `cuenta`           varchar(50)  NOT NULL,
  `sku`              varchar(100) NOT NULL,
  `wc_id`            int(11)      DEFAULT NULL,
  `ml_item_id`       varchar(60)  DEFAULT NULL,
  `ml_url`           text         DEFAULT NULL,
  `success`          tinyint(1)   NOT NULL DEFAULT 0,
  `error`            text         DEFAULT NULL,
  `ml_status`        smallint(6)  DEFAULT NULL COMMENT 'HTTP status de POST /items',
  `desc_status`      smallint(6)  DEFAULT NULL COMMENT 'HTTP status de PUT /description',
  `pics_preuploaded` tinyint(4)   DEFAULT 0,
  `payload`          longtext     DEFAULT NULL CHECK (json_valid(`payload`)),
  `ml_response`      longtext     DEFAULT NULL CHECK (json_valid(`ml_response`)),
  `published_at`     datetime     DEFAULT NULL,
  `created_at`       datetime     NOT NULL DEFAULT current_timestamp(),
  `gtin_error`       tinyint(1)   NOT NULL DEFAULT 0 COMMENT '1 si fallo por GTIN invalido',
  PRIMARY KEY (`id`),
  KEY `idx_sku` (`sku`), KEY `idx_cuenta` (`cuenta`),
  KEY `idx_success` (`success`), KEY `idx_created` (`created_at`),
  KEY `idx_gtin_error` (`gtin_error`)
) COMMENT='Historial de publicaciones WC→ML';
```

Guarda **el payload íntegro y la respuesta íntegra** (esta última recortada a
65,000 caracteres). Es la única fuente que permite reconstruir *qué se mandó*.
`run_key` es `"studio:CUENTA:SKU"` en las filas nacidas del panel.

Es **append-only en la práctica**: una fila por intento, sin llave única. Los
2,532 fallos siguen ahí.

#### `ml_progress` — el estado, no la historia

```sql
CREATE TABLE `ml_progress` (
  `prog_key`     varchar(150) NOT NULL COMMENT 'cuenta:sku',
  `cuenta`       varchar(50)  NOT NULL,
  `sku`          varchar(100) NOT NULL,
  `wc_id`        int(11)      DEFAULT NULL,
  `ml_item_id`   varchar(60)  DEFAULT NULL,
  `ml_url`       text         DEFAULT NULL,
  `success`      tinyint(1)   NOT NULL DEFAULT 0,
  `error`        text         DEFAULT NULL,
  `gtin_error`   tinyint(1)   NOT NULL DEFAULT 0,
  `dry_run`      tinyint(1)   NOT NULL DEFAULT 0,
  `published_at` datetime     DEFAULT NULL,
  `updated_at`   datetime     NOT NULL DEFAULT current_timestamp()
                              ON UPDATE current_timestamp(),
  PRIMARY KEY (`prog_key`)
) COMMENT='Estado de publicaciones (reemplaza progress.json)';
```

**Solo se escribe cuando el alta salió bien** (`publicar_ready.py:115-127`,
`publicar.py:534-546`), con `ON DUPLICATE KEY UPDATE`. Un fallo **no deja rastro
aquí** — para eso está `ml_backlog`.

#### Los otros cinco destinos del mismo evento

| Destino | Qué recibe | Dónde |
|---|---|---|
| `ops.channel_submissions` (kubera) | **resumen** del envío: canal, cuenta, sku, `submission_id`, operación (`alta`/`actualizacion`/`pausa`), status, éxito, `error_resumen`, `detail_ref` = `mysql:ml_backlog:<id>`. **Los blobs `payload`/`ml_response` NO viajan** | `publicar_ready.py:99-111`, `publicar.py:519-531` |
| `channel.listings` (kubera) | el MLM recién nacido, **en el momento**, sin esperar los 15 min del sync | `publicacion_seam.registrar`, llamado en `publicar_ready.py:132-135` |
| **WooCommerce** | si el producto estaba en `draft`/`inprogress`, pasa a **`publish`** (regla de Brandon: publicado en una cuenta ⇒ visible en el panel) | `publicar_ready.py:177-241`, `PUT /products/{id}` en `:219-220` |
| `core.products` (kubera) | el cambio de ciclo de vida draft→publish | `core_write.registrar`, `publicar_ready.py:234-239` |
| `ops.process_log` (kubera) | **quién** publicó, con `ok`/`rechazado`/`error` y duración | `routers/publicar.py:114-119` |
| **Slack** | alerta solo en excepción no controlada | `routers/publicar.py:160-169` |

Ninguno de esos puede tumbar la publicación: todos están envueltos en
`try/except` que como mucho anotan un warning.

---

## 8. Números medidos hoy (2026-09-03, `SELECT` sobre `ml_backlog`)

| Métrica | Valor |
|---|---|
| Filas en `ml_backlog` | **6,455** (3,923 ok · 2,532 fallidas) |
| Rango | 2026-03-17 19:58 → 2026-09-02 21:23 |
| Filas que son **altas** (payload con `listing_type_id`) | 5,921 |
| Filas que son **actualizaciones** | 215 |
| Filas sin payload (p. ej. `GEMINI_ERROR`) | 319 |
| Altas con **`family_name`** | **5,918** |
| Altas con **`title`** | **3** |
| Altas con `free_shipping: true` | 5,744 (97 %) |
| Altas con imágenes **pre-subidas** | 4,587 · **última: 2026-07-08 18:05** |
| Altas de agosto+septiembre con pre-subida | **0 de 566** |
| `ml_progress`: filas con éxito | 4,194 (2,129 SKUs · BEKURA 2,246 · SANCORFASHION 2,217) |
| Descripción: `desc_status` en altas OK | 200 → 3,906 · 400 → 7 · 500 → 1 · `NULL` → 9 |

Altas por mes (todas, ok):
`mar` 398/215 · `abr` 1,493/493 · `may` 1,151/700 · `jun` 1,914/1,386 ·
`jul` 832/536 · `ago` 541/478 · `sep` (2 días) 126/115.

### 8.1 Dos hallazgos que salen solo de mirar los números

**a) El pre-upload de imágenes está muerto desde el 8-jul-2026.**

| Mes | Altas | Con pre-upload | Por URL |
|---|---|---|---|
| 2026-03 | 398 | 392 | 6 |
| 2026-04 | 1,493 | 1,432 | 61 |
| 2026-05 | 897 | 774 | 123 |
| 2026-06 | 1,912 | 1,857 | 55 |
| 2026-07 | 655 | **132** | 523 |
| 2026-08 | 451 | **0** | 451 |
| 2026-09 | 115 | **0** | 115 |

`preupload_picture` (`ml_api.py:215-270`) descarga la imagen de chunche.shop con
3 intentos y, si no puede, devuelve `None` y el payload cae a `{"source": url}`
(`publisher_core.py:337`). El fallback **funciona** —las altas siguen saliendo
201— pero significa que **ML descarga las fotos por su cuenta** desde
chunche.shop, y que el escalado a ≥500×250 de `_ensure_min_size`
(`ml_api.py:162-212`) **ya no se aplica a nada**.
**[inferido]** la causa es la descarga desde chunche.shop: el 403 intermitente
del WAF/CDN de Hostinger (pendiente conocido nº 1) y/o el modo mantenimiento del
sitio. No lo pude confirmar sin los logs de Railway.

**b) Prácticamente ninguna categoría acepta `title`.**
5,918 de 5,921 altas viajaron con `family_name`. Traducción para un KAM: **el
título que escribes se recorta a 60 caracteres sin avisar** (`publisher_core.py:363`)
y ML puede además reemplazar lo que se ve por el nombre del producto de catálogo.
El aviso del preview ("el título tiene N caracteres") es informativo, no bloquea.

---

## 9. Trampas conocidas (la lista corta que evita perder una tarde)

1. **`vendor/` no se toca.** Los arreglos van en `services/publicar_ready.py` o
   `services/publicar.py`. La única excepción sancionada es
   `vendor/ml_ready/size_chart_mapping.py`, que es configuración (ahí se
   registran las guías de tallas).
2. **La elección del panel manda.** `ml_categoria_id` (picker) gana sobre
   `ml_category_id` (predictor). Y si hay categoría del panel, **las categorías de
   Woo no se pasan** (`publicar_ready.py:457`) porque el vendor las prefiere y
   revertiría la elección.
3. **`BRAND` es `"Ferrahome"`** por defecto (`publisher_core.py:50`), no la marca
   del producto. Solo la desplaza un atributo de marca que traiga el mapeador.
4. **`EMPTY_GTIN_REASON` siempre viaja.** Si la categoría exige GTIN real, el
   placeholder `0000000000000` se rechaza y el error queda como `GTIN_INVALIDO`,
   que **no se reintenta** (necesita un código de barras de verdad).
5. **El `preview` de ML no consulta nada que escriba**, pero sí gasta llamadas a
   la API de ML (categoría, atributos, sale_terms, estado de ítems) con el token
   real.
6. **`_marcar_publicado_en_woo` cambia el producto en Woo.** Publicar en ML
   dispara un `PUT /products/{id}` con `status: publish` si estaba en draft. Si
   estás probando, esto **sí** modifica la tienda.
7. **Se publica en las dos cuentas**, en el orden `SANCORFASHION`, `BEKURA`
   (`publisher_core.py:53`). Los timestamps del ejemplo lo confirman: 21:23:21 y
   21:23:26.
8. **`ml_api.upload_pictures` (`:320`), `activate_item` (`:381`) y
   `preupload_picture_from_bytes` (`:273`) no los llama nadie** en todo el
   backend. Verificado con grep. Son armas cargadas que nadie dispara — pero
   `activate_item` es un `PUT status=active`: no lo llames por curiosidad.
9. **Los tokens de ML** se leen de `ml_tokens_dashboard` / `ml_tokens` (cifrados
   con Fernet), arbitrando por `updated_at` (`services/meli.py:268`). El
   `save_gtin_fn` está deliberadamente **sin conectar**
   (`publicar_ready.py:253-255`): el GTIN encontrado se usa en la publicación
   pero **no se persiste** de vuelta en Woo, porque la REST de Woo devuelve 403.
10. **`GEMINI_ERROR` en `ml_backlog` no es un error de ML.** 317 filas. Vienen del
    editor de imágenes con IA, que anota en la misma tabla. Si estás contando
    fallos de publicación, réstalos.

---

## 10. CÓMO REUSARLO SIN TOCAR PRODUCCIÓN

### 10.1 La línea exacta

**Todo lo que está por encima de estas cuatro llamadas es armado de datos.
Debajo de ellas, ya estás escribiendo en Mercado Libre:**

| # | Línea | Qué escribe |
|---|---|---|
| **1** | `backend/vendor/ml_ready/ml_api.py:258-259` — `requests.post(f"{ML_API_BASE}/pictures", …)` | **sube una imagen** a la cuenta de ML y la deja ahí |
| **2** | `backend/vendor/ml_ready/ml_api.py:305-306` — `requests.post(f"{ML_API_BASE}/items", …)` | **crea la publicación**. Es *la* línea |
| **3** | `backend/vendor/ml_ready/ml_api.py:356 / :362 / :370` | descripción (PUT/POST) y pausa (PUT) sobre un ítem existente |
| **4** | `backend/services/publicar.py:604 / :617 / :629 / :632` | el camino ACTUALIZAR: `PUT /items/{id}` y `PUT|POST /items/{id}/description` |

**El interruptor que las separa es un booleano**, `dry_run`:

```python
# backend/vendor/ml_ready/publisher_core.py:329
if raw_images and not dry_run:      # ← la puerta 1
    ... ml_api.preupload_picture(url, token) ...

# backend/vendor/ml_ready/publisher_core.py:393-397
if dry_run:
    print("  [DRY RUN] Payload construido OK — no se envía a ML")
    return {'success': True, 'ml_item_id': 'DRY_RUN', 'dry_run': True,
            'payload': payload}
```

- `publicar_ready.py:493` llama `build_payload(prod, token, True, cuenta)` → **dry-run**.
- `publicar_ready.py:539` llama `publish_product(prod, token, False, cuenta)` → **en vivo**.

Ese `True`/`False` es la frontera entera del sistema.

### 10.2 Lo que puedes reusar sin escribir en ningún lado

**Puramente en memoria — sin red, sin BD, sin nada:**

| Función | Archivo:línea | Qué te da |
|---|---|---|
| `build_attributes(ml_attrs, ml_category_attrs, wc_attrs)` | `vendor/ml_ready/attribute_mapper.py:663` | la lista de `attributes` de ML |
| `build_secondary_attributes(prod, category_attrs, existing_ids)` | `attribute_mapper.py:794` | los atributos opcionales que se pueden llenar |
| `_find_value_id(value, allowed_vals)` | `attribute_mapper.py:742` | el `value_id` de ML más cercano a un texto |
| `_validate_value`, `_format_number_unit`, `_normalize` | `attribute_mapper.py:635 / :890 / :776` | normalización de valores y unidades |
| `WC_TO_ML_ID` | `attribute_mapper.py:18-620` | ~600 nombres en español → IDs de ML. Es un diccionario; cópialo |
| `get_chart_id(cuenta, domain, gender)` | `vendor/ml_ready/size_chart_mapping.py:38` | el `SIZE_GRID_ID` de una guía de tallas |
| `warranty_days_for_sku(sku)` | `vendor/ml_ready/publisher_core.py:112` | 15 días para `ROP-`/`CALZ-`, 30 el resto |
| `_plain(texto)` / `_html_a_plano(html)` | `services/publicar.py:34` / `publicar_ready.py:350` | HTML de Woo → texto plano para ML |
| `_ensure_min_size(bytes)` | `vendor/ml_ready/ml_api.py:162` | JPEG RGB ≥500×250, escalando o rellenando con blanco. **Solo transforma bytes; no sube nada** |

**Leen, pero no escriben** (consumen token/cuota de ML, o abren la BD de WordPress
en `SELECT`):

| Función | Archivo:línea | Lee de |
|---|---|---|
| `construir_prod(sku, wc_id, campos)` | `services/publicar_ready.py:372` | BD de WordPress (`SELECT`) |
| `get_category_info` / `get_category_attributes` / `get_category_sale_terms` | `ml_api.py:65 / :77 / :93` | `GET /categories/...` de ML |
| `build_sale_terms(category_id, token, sku)` | `publisher_core.py:118` | ML (los sale_terms de la categoría) |
| `search_gtin_in_catalog` | `ml_api.py:110` | `GET /sites/MLM/search` |
| `search_gtin_upc` | `ml_api.py:134` | API pública de UPC Item DB |
| `resolve_ml_category_from_wc` | `vendor/ml_ready/wc_category_mapping.py:93` | REST de categorías de Woo |
| `_estados_items_ml(pubs)` | `services/publicar.py:549` | `GET /items/{id}` |
| `_ml_publicaciones(sku)` | `services/publicar.py:144` | `ml_progress` o `channel.listings` |
| `_ml_live(sku)` / `estado_live(sku)` | `services/publicar.py:62 / :115` | `GET /users/{uid}/items/search` |

**La receta segura para armar un payload y mirarlo:**

```python
from services import publicar_ready, meli
from vendor.ml_ready import publisher_core

publicar_ready.configurar()                    # publicar_ready.py:244 — solo cablea ganchos
prod  = publicar_ready.construir_prod(sku, wc_id, campos)      # SELECT a WordPress
token = meli._access_token("BEKURA")
payload = publisher_core.build_payload(prod, token, True, "BEKURA")   # ← dry_run=True
```

Con `dry_run=True`, `build_payload` hace **solo GETs** a ML y devuelve el dict.
Verificado: la única escritura dentro de `build_payload` es el pre-upload de
imágenes de `publisher_core.py:331-338`, y está bajo `if raw_images and not dry_run`.

Y desde fuera, **sin escribir una línea de código**: `POST /api/publicar/preview`
con el mismo cuerpo que usarías para confirmar. Devuelve el payload completo en
`respuesta["payload"]` y no escribe en ML. Es la forma más barata de ver qué
saldría.

### 10.3 Lo que NO puedes reusar sin escribir en algún lado

| Pieza | Qué escribe, y dónde |
|---|---|
| `publisher_core.publish_product(...)` con `dry_run=False` | **POST /items en ML**, PUT de pausa, PUT/POST de descripción, y `save_backlog` → `ml_backlog` + `ml_progress` + `ops.channel_submissions` + `channel.listings` + Woo `publish` + `core.products` |
| `publicar_ready.crear_ml(...)` | lo anterior, en **las dos cuentas** |
| `services/publicar.confirmar(...)` y `_confirmar_ml(...)` | idem, más el camino de ACTUALIZAR |
| `ml_api.create_item` · `preupload_picture` · `pause_item` · `update_description` · `upload_pictures` · `activate_item` | escriben en ML, sin excepción |
| `publicar_ready._marcar_publicado_en_woo(...)` | `PUT /products/{id}` en **WooCommerce** (`publicar_ready.py:219-220`) |
| `publicar_ready._backlog_ml(...)` · `publicar._guardar_backlog_ml(...)` · `_anotar_pausa_backlog(...)` | `INSERT`/`UPDATE` en MySQL |
| `publicacion_seam.registrar(...)` · `core_write.registrar(...)` · `kubera_mirror.espejar(...)` | escriben en la BD **kubera** |
| `bitacora.anotar(...)` | `INSERT` en `ops.process_log` (kubera) |

**No hay flag de entorno que apague el envío a ML.** No existe un
`PUBLICAR_ML_ENABLED`. Lo único que separa "armar" de "publicar" es el parámetro
`dry_run` y **qué función llamas**. Si tu script llama a `publish_product` con
`dry_run=False`, publica. Punto.

### 10.4 Checklist antes de correr cualquier cosa que toque este pipeline

- [ ] ¿Estoy llamando a `build_payload` (armado) o a `publish_product` (envío)?
- [ ] Si es `build_payload`, ¿el tercer argumento es `True`?
- [ ] ¿Mi script importa `publicar_ready.crear_ml` o `publicar.confirmar`? Si sí, **publica**.
- [ ] ¿Voy a leer kubera? Solo `SELECT`, y **nunca** marques la sesión como
      read-only: el DSN apunta al pooler en modo transacción (6543) y las
      conexiones **se comparten**; envenenar una tumba la escritura de pedidos en
      producción. Si necesitas la garantía:
      `BEGIN; SET TRANSACTION READ ONLY; …; ROLLBACK;`
- [ ] ¿Voy a leer `ml_backlog`/`ml_progress` (MySQL)? `SELECT` sí; `ALTER`/`DELETE` no.
- [ ] Encender o apagar cualquier flujo vivo necesita el dale de Brandon **antes**
      del push (regla 3 de la casa).

---

## 11. Diagnóstico exprés

| Síntoma | Primera cosa a mirar |
|---|---|
| "ERROR DE CONEXIÓN al publicar" | Mensaje genérico del frontend para **cualquier** fallo del fetch. Puede ser un 500 del backend (logs de Railway en `/api/publicar/confirmar`) o el navegador bloqueando la petición (extensión/antivirus: probar en incógnito). Desde v0.15.1 los errores de validación de ML sí llegan legibles al modal. |
| "Se publicó pero está activa, no pausada" | `SELECT error FROM ml_backlog WHERE ml_item_id='MLM…'` → busca `NO_PAUSADO:`. Pausar a mano. |
| "Dice que ya está publicado y no lo está" | `ml_progress` tiene la fila pero el ítem se borró en ML. El sistema ya lo detecta (`_estados_items_ml`) y re-crea; si no, revisa que haya token de esa cuenta (ante duda **asume vivo**). |
| "Publicó en la categoría equivocada" | ¿La meta `ml_categoria_id` está puesta? Si no, gana `ml_category_id` del predictor; y si el producto tiene categoría en Woo con `"ML: MLM###"` en su descripción, esa **sobrescribe** (`publisher_core.py:184-198`). |
| "El precio salió más bajo" | Es el `_price` (oferta) en vez del `_regular_price`. Mira `publicar_ready.py:394-398` y si el SKU es una variación. |
| "El título salió cortado" | `family_name[:60]` (`publisher_core.py:363`). 99.95 % de los casos. |
| "Falla por guía de tallas" | 367 casos. Faltan guías para ~25 dominios de ROPA. Se crean en ML y se registran en `size_chart_mapping.py`. Los BRAS con guía fallan por falta del atributo `GENDER` en el producto. |
| "Qué se mandó exactamente" | `SELECT payload, ml_response, error, ml_status FROM ml_backlog WHERE sku=%s ORDER BY id DESC` |

---

*Fin. Verificado contra `1a7da7e` y contra `ml_backlog` de producción el
2026-09-03. Cualquier línea que no cuadre: gana `main`.*
