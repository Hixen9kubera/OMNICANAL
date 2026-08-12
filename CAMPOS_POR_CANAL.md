# Qué campos necesita cada canal — inventario levantado del CÓDIGO

> **Estado:** insumo para diseñar `channel_requirements`. Nada implementado.
> **Fecha:** 2026-08-12 · **Checkout:** `fa62418` v0.106.0
> **Método:** leído de los publicadores que corren hoy, NO de la documentación
> de los marketplaces. Este proyecto ya se quemó con eso: el esquema publicado
> de Walmart dice 3.19 y producción corre 3.11; la doc decía `WALMART_MX` y el
> sistema quería `WALMART_MEXICO`.

---

## 0. Resumen de una línea

De los 6 canales del panel, **dos publican de verdad desde el repo** (ML y
Amazon), **uno publica desde un script suelto** (Walmart), **uno publica desde
fuera del control de versiones** (TikTok) y **dos no tienen ni una línea de
código** (Temu, Shein).

Y de lo que sí se manda, **una parte grande es constante**: el mismo texto para
todo el catálogo.

---

## 1. MERCADO LIBRE — `vendor/ml_ready/publisher_core.py::build_payload`

El único publicador maduro. Su propio docstring lista las reglas:

| Campo | Origen | Nota |
|---|---|---|
| `category_id` | panel / mapeo WC | override de `wc_category_mapping` sobre la meta |
| `title` | Woo | ≤60 |
| `available_quantity` | **stock real** (línea 346) | |
| `price` | Woo | |
| `attributes` | Woo + categoría | |
| `pictures` | Woo | |
| `listing_type_id` | **constante** `gold_pro` | |
| `shipping.mode` | **constante** `me2` | |
| `free_shipping` | calculado | `precio > $149` |
| `BRAND` | **constante** `Ferrahome` | |
| `CONDITION` | **constante** `Nuevo` | |
| `SELLER_SKU` | sku | |
| `sale_terms` | **constante** | garantía vendedor 30 días |
| `description` | Woo | **llamada aparte**, no va en el item |

---

## 2. AMAZON — `services/publicar.py:687-720`

22 atributos. Ordenados por de dónde salen:

**Del producto (6):** `item_name`, `product_description`, `color`, `material`,
`item_length_width_height`, `purchasable_offer` / `list_price`

**Del SKU, repetido (3):** `part_number`, `model_name`, `model_number` — los
tres son el SKU.

**Constantes (7):**
```
condition_type ............. "new_new"
country_of_origin .......... "MX"          ← ver §6.1
included_components ........ "1 x Producto"
warranty_description ....... "Garantía del vendedor"
supplier_declared_dg_hz_regulation .. "not_applicable"
number_of_items ............ 1
supplier_declared_has_product_identifier_exemption .. True
```

**Del producto con respaldo (3):** `brand`/`manufacturer`
(`_attr_from(atributos,"BRAND","Generic")`, línea 678), `color`
(respaldo `"Multicolor"`), `material` (respaldo `"Mixto"`).

> **Corregido el 12-ago.** Una versión previa de este documento contaba 9
> constantes e incluía `brand` entre ellas. `brand` sale del producto; el
> respaldo es `"Generic"`, no `"Ferrahome"` — ese último es el respaldo de ML
> (`publisher_core.py:50`) y de Walmart, no el de Amazon.

**Fijo, y probablemente mal (1):**
```python
"fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT",
                              "quantity": 10, ...}]
```
**Publica 10 unidades siempre**, sin mirar el stock. ML sí lee el stock real
(línea 346). Son dos criterios distintos para el mismo dato en el mismo panel.

**Los bullets SÍ llegan del panel, por los dos caminos.**

> **Corregido el 12-ago.** Una versión previa afirmaba que Amazon mandaba
> "5 bullets genéricos de plantilla", citando
> `vendor/amazon_ready/attribute_mapper.py:102`. **Es falso.** Esa plantilla es
> un respaldo que se sobrescribe:
>
> - `publicar.py:676` — `campos.get("bullets") or ["Producto de alta calidad,
>   práctico y duradero."]` → los del panel, respaldo de UNA línea.
> - `publicar_ready.py:600` — llama al mapper y luego **pisa** `bullet_point`
>   con los del panel. Su propio comentario lo dice: *"El Studio manda
>   bullets/highlights ya redactados por la IA: pisan los que `_build_bullets`
>   genera"*.
>
> El error nació de leer un archivo que no es el que decide. Queda anotado
> porque es exactamente el modo de fallo que este inventario existe para
> evitar.

**Ausentes de verdad:** `item_highlights` y `backend_search_terms`.

`item_highlights` es el caso más claro de la brecha: el panel **tiene el
generador escrito** (`ia_generadores.py`, *"campo indexable ≤125 caracteres"*),
el comentario de `publicar_ready.py:596` dice *"bullets **y highlights**… pisan
los que genera el mapper"* — **pero el código solo pisa `bullet_point`.** Los
highlights se nombran, nunca se implementan, y `item_highlights` no aparece en
ningún payload. Se generan y se tiran.

**Hay DOS constructores, en relación primario/respaldo** (no dos botones, como
dijo una versión previa). `_amazon_attrs_final` (`publicar.py:834-844`):

```python
if wc_id and wp_db.disponible():
    try:    candidatos = await publicar_ready.atributos_amazon(...)   # PRIMARIO
    except: log.warning("... se usa el mapeo propio")
if candidatos is None:
    candidatos = _amazon_attributes(...)                              # RESPALDO
```

El respaldo entra cuando **la BD de WordPress no responde** — y los 403
intermitentes de Hostinger (pendiente #1 de `CLAUDE.md`) hacen que sí se use.

---

## 3. WALMART — `scripts/publicar_walmart.py::_item`

Script suelto, no está en el backend. Solo publica a **2 categorías** con
exención de UPC: `costumes` (Disfraces, folio 15728342) y `home_other`
("Cocina, Decoración y Otros", folio 15751007).

**`Orderable`** — del producto: `sku`, `productName`, `shortDescription`,
`price`, `ShippingWeight`, dimensiones, imágenes.
Constantes: `productIdentifiers = {GTIN, CUSTOM}`, `countryOfOriginAssembly =
["China"]`, `hasNomCertification = "No"`, `brand/manufacturer` (Woo o
"Ferrahome"), `ProductTaxCode` (clave SAT por categoría).

**`Visible`** — 8 campos, todos de Woo con respaldo constante:
`countPerPack=1`, `material` (Woo o `"Plástico"`), `colorCategory`,
`modelNumber`, `assembledProduct` largo/ancho/alto/peso.
Más `size` y `gender` **solo si la categoría lo pide** (`pide_genero`), con
`"Unitalla"` de respaldo y el género **derivado del título**.

**Trampas ya codificadas:** máximo 2 decimales (causó 6 rechazos del primer
lote); la clave de `Visible` es el nombre de la categoría en español, exacto.

> **Corregido el 12-ago.** Una versión previa listaba una trampa de acento en
> `hasNomCertification` (`"Sí"` vs `"Si"`). **No existe en el código**: ese
> campo solo se manda como constante `"No"` (línea 503). La afirmación venía de
> un documento de especificación, no del repo — justo lo que este inventario
> dice no hacer.

---

## 4. TIKTOK — no hay publicador en el repo

`routers/tiktok.py` tiene `autorizar`, `callback`, `reparar-tiendas`,
`explorar`, `estado`. **Ningún endpoint de publicar.** El publicador
(`tk_publicar.py`) vive en un scratchpad fuera de git.

Del payload real (visto en un ejemplo, no verificable aquí): `title`,
`description`, `category_id`, `brand_id`, `main_images[].uri`, `package_weight`,
`package_dimensions`, `skus[].seller_sku`, `skus[].price`,
`skus[].inventory[].quantity`, `is_cod_allowed`, `save_mode`.

El panel tiene **un solo generador** para TikTok: "Título viral".

---

## 5. TEMU y SHEIN — cero código

Grep de `goodsBasic|skuList|catId|priceorder` en todo `backend/`: **ningún
archivo**. No hay publicador, ni generadores en el panel, ni metas en Woo.

---

## 6. LO QUE APARECIÓ AL COMPARAR CANALES

### 6.1 · El país de origen tenía TRES valores — resuelto el 12-ago

```
Amazon, mapper vendorizado → "CN"       (vendor/…/attribute_mapper.py:222)
Amazon, mapeo propio       → "MX"       (publicar.py:698 y :747)
Walmart                    → ["China"]  (publicar_walmart.py:501)
```

Y dentro de Amazon **no eran dos opciones sino una bifurcación silenciosa**: el
mapper es el primario, el mapeo propio el respaldo, y el respaldo entra cuando
la BD de WordPress no contesta. O sea que **el país declarado dependía del
estado de la red al momento de publicar** — mismo producto, mismo botón, dos
declaraciones distintas.

**Resuelto** (decisión de Eduardo, 12-ago): se unifica en `"MX"` desde el
adaptador `publicar_ready.py:625`, que pisa al mapper igual que ya hacía con los
bullets. `vendor/` no se tocó (regla 1 de la casa). Walmart sigue con `"China"`
y queda como pendiente aparte.

Esto es exactamente lo que un catálogo de requisitos hace visible: la
contradicción existió durante meses porque cada publicador decidía por su cuenta
y **nadie tenía dónde compararlos**.

### 6.2 · El stock tiene dos criterios

ML manda el stock real; Amazon manda 10 fijo. Ningún archivo explica por qué.

### 6.3 · Lo que el panel genera y nadie recibe

| Generador escrito | ¿Lo manda alguien? |
|---|---|
| Amazon · Título | sí |
| Amazon · **Item Highlights** | **NO — el campo no existe en ningún payload** |
| Amazon · Bullet Points | sí — por los dos caminos *(corregido 12-ago)* |
| Amazon · Descripción | sí |
| Amazon · Atributos | parcial |
| Amazon · Set de imágenes | plan, no campo |
| ML · Título, Ficha, Descripción | sí |
| TikTok · Título viral | **no hay publicador** |

Queda **un solo campo** claramente generado y tirado: `item_highlights`. Es
poco, pero el punto no cambia — nada de lo que el panel genera **se guarda**:
si no se publica en esa misma sesión, se pierde.

### 6.4 · Las constantes son el 40% de lo que se manda

`Ferrahome`, `Nuevo`, `"1 x Producto"`, `"Garantía del vendedor"`, `Plástico`,
`Unitalla`, `not_applicable`… Ninguna vive en una tabla: están escritas dentro
de cada publicador, y **difieren entre canales sin que nadie lo note** (§6.1).

---

## 7. QUÉ IMPLICA PARA `channel_requirements`

### 7.1 · Un requisito tiene tres estados, no dos

El encargo modela `obligatorio: bool`. Lo que el código muestra es que hay tres
situaciones distintas:

| Situación | Ejemplo | Qué debe pintar el panel |
|---|---|---|
| **Lo pide y lo tenemos del producto** | `titulo` | verde / falta |
| **Lo pide y lo resolvemos con una constante** | `condition_type = "new_new"` | no es "falta": es "lo ponemos nosotros" |
| **Lo pide y no lo tenemos ni por constante** | `item_highlights` | falta de verdad |

Si no se distingue el segundo caso, el panel va a marcar como faltantes ~9
campos de Amazon que en realidad siempre se llenan. **La tabla necesita saber
que un campo se satisface por defecto, y con qué valor** — que además es dónde
deberían vivir las constantes hoy dispersas.

### 7.2 · El nombre del campo es el contrato

El encargo propone `c.datos ? r.campo`. Para que eso funcione, el `campo` del
requisito y la llave del contenido tienen que coincidir **exacto**. Con la regla
de nombres canónicos (`precio`, no `sale_price`), eso significa que
`channel_requirements.campo` guarda el nombre **canónico**, y el publicador
traduce. Conviene decirlo explícito o cada cargador lo va a llenar con el nombre
nativo del canal.

*(Nota menor: la consulta del encargo dice `c.datos`; la columna de
`enrich.ai_content` se llama `payload`.)*

### 7.3 · `categoria_id` no puede tener FK

Los cinco tipos son incompatibles: `MLM162997` (ML), `HOME` (Amazon),
`costumes` (Walmart), `25725` (Temu), `913416` (TikTok). Tiene que ser `text`
libre. Y `channel.categories` está modelada a la medida de ML — no sirve de
padre.

### 7.4 · Los cinco comunes no deben repetirse por categoría

`titulo`, `descripcion`, `precio`, `dimensiones`, `atributos` los pide toda
categoría. Escribir una fila por cada una de las 1,937 hojas de TikTok son
~10,000 filas para decir cinco cosas. Hace falta un `categoria_id = null` (o
sentinela) que signifique "todo este canal".

### 7.5 · Falta el eje de la CUENTA

`enrich.ai_content` tiene PK `(sku, canal, cuenta)` por el caso `EST-0091` — el
mismo SKU es dos productos según la cuenta de ML. Pero `channel_requirements` se
propone con llave `(canal, categoria_id, campo)`, sin cuenta. Probablemente esté
bien —los requisitos son del canal, no de la cuenta— pero conviene decidirlo a
propósito, no por omisión.

---

## 8. PREGUNTAS AL CONSEJO

1. **¿El caso "se satisface por constante" va en `channel_requirements`** (con
   su valor por omisión) **o en otra tabla?** Es lo que evita que el panel
   marque en rojo 9 campos que siempre se llenan solos. Y sería el primer lugar
   donde las constantes viven como dato y no dispersas en cada publicador.

2. **¿Se congela el nombre canónico como contrato explícito?** Sin eso, cada
   cargador va a escribir el nombre nativo de su canal y el join no encuentra
   nada.

3. **¿Requisitos primero o contenido primero?** El encargo hace requisitos
   primero. Pero con `enrich.ai_content` vacía y sin escritor, la consulta del
   panel contestaría *"le falta todo"* para los 22,186 SKUs. ¿Se acepta ese
   intermedio, o conviene entrelazarlos por canal (Amazon completo de punta a
   punta, luego el siguiente)?

4. **¿Qué se hace con las contradicciones que esto destapa** (§6.1 país de
   origen, §6.2 stock)? ¿El catálogo de requisitos las arregla, o solo las
   reporta y se arreglan aparte?

5. **¿Vale la pena modelar Temu y Shein ahora**, sin una línea de código ni
   experiencia con sus APIs? ¿O el catálogo solo cubre los canales que ya
   publican, y crece cuando el canal exista?
