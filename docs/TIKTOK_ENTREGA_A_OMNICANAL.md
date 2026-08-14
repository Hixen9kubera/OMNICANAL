# TikTok → Omnicanal: los 5 puntos para abrir el canal

> Respuesta a *"Lo que necesito del chat de TikTok"* de
> [FLUJO_POR_CANAL.md](FLUJO_POR_CANAL.md).
> **Todo medido en vivo el 13-ago-2026.** Archivos en
> `Escritorio\respaldo_tiktok_20260813`.
> Detalle completo en [TIKTOK_INVENTARIO_DE_CAMPOS.md](TIKTOK_INVENTARIO_DE_CAMPOS.md).

| # | Qué | Archivo | Filas |
|---|---|---|---|
| 1 | Censo → `channel.listings` | `TIKTOK_CENSO_LISTINGS.csv` | **900** |
| 2 | Árbol → `channel.categories` | `TIKTOK_CATEGORIAS.csv` | **2,168** |
| 3 | Atributos, ejemplo crudo | `TIKTOK_ATRIBUTOS_EJEMPLO.json` | 2 categorías |
| 4 | `SALES_PROPERTY` | `TIKTOK_SALES_PROPERTY.csv` | 1,872 |
| 5 | Prompt de contenido | aquí abajo | — |

Extra ya listo: **`TIKTOK_FIELD_REQUIREMENTS.csv`** (1,779 filas) para el punto 5
de tu tabla — `channel.field_requirements` sin escribir el cargador.

---

## 1 · Censo de lo publicado

`TIKTOK_CENSO_LISTINGS.csv` — **900 filas, 900 SKUs únicos**, una por SKU.

```
canal · cuenta · shop_id · sku · product_id · sku_id · status
category_id · category_nombre · category_ruta
precio · moneda · stock · warehouse_id
titulo · url · audit_status · create_time · update_time
```

| status | SKUs |
|---|---|
| DRAFT | 599 |
| **ACTIVATE** | **283** |
| FAILED (auditoría) | 11 |
| PENDING | 7 |

Sanidad: **0 sin `category_id`**, **0 sin `warehouse_id`**, y los 900 en
`7647893424175580935` — el de **ventas**. Ninguno cayó en el de devoluciones.

### ⚠️ Dos trampas al construir este censo

**El `status` del `search` se queda viejo.** 55 productos que el listado daba por
vivos venían `DELETED` en su propio detalle. **Manda el detalle.** Sin filtrar
por ahí, `channel.listings` nacía con 55 filas fantasma que nadie sabría de
dónde salieron.

**`products/search` devuelve los borrados** (1,228 → 955 vivos → 900 tras
verificar el detalle). Filtrar `status=DELETED` en los dos pasos.

`category_id` y `warehouse_id` **sólo están en el detalle**, no en el listado:
por eso el export hace ~900 llamadas en vez de leer nuestros payloads. Leer los
payloads contaría lo que creímos mandar, no lo que TikTok tiene.

---

## 2 · Árbol de categorías

`TIKTOK_CATEGORIAS.csv` — las 2,168, con `parent_id` y `nivel` para reconstruir
el árbol.

```
canal · categoria_id · nombre · ruta · parent_id · nivel
is_leaf · permission_status · publicable
```

### Los 451 vs 416: los dos números son correctos

| | |
|---|---|
| `INVITE_ONLY` **en todos los niveles** | **451** ← el que citas |
| `INVITE_ONLY` **que son hoja** | **416** |
| `AVAILABLE` que son hoja | **1,521** |

Los 35 de diferencia son ramas. **Sólo se publica en hojas**
(`12052024 Category is not final category`), así que para publicar el número que
manda es **416 bloqueadas / 1,521 usables**.

Por eso el CSV trae la columna **`publicable`** ya calculada
(`is_leaf AND AVAILABLE`): evita que cada consumidor repita la regla y se
equivoque en un lado o en el otro.

`permission_status` sólo toma dos valores: `AVAILABLE` (1,717) o `INVITE_ONLY`
(451). No hay mezclas.

**Tipo del `id`: `string`.** Es numérico pero se manda como texto.

### 🔴 Qué hace INVITE_ONLY en la práctica — no es lo que parece

Sabíamos que no bloquea el borrador. **Ahora sé qué pasa al activar, y es peor
que un rechazo limpio:**

24 productos quedaron en categorías INVITE_ONLY. **Ninguno llegó a `ACTIVATE`.
Los 7 `PENDING` de toda la tienda son exactamente esos.**

**No rebota la activación: la acepta y deja el producto parado en `PENDING`
indefinidamente.** Sin error, sin aviso, y desde fuera parece "en revisión".

Regla para el cargador: **filtrar por `publicable` ANTES de ofrecer la
categoría.** Los 24 se colaron por el recomendador de TikTok, que sí las ofrece.

---

## 3 · La llamada de atributos

```
GET /product/202309/categories/{category_id}/attributes
    ?shop_cipher=<cipher>&locale=es-MX
```

`TIKTOK_ATRIBUTOS_EJEMPLO.json` trae **dos categorías crudas** a propósito:
`601104` (Ventiladores: 19 atributos, 5 obligatorios, de los dos tipos) y
`600032` (Frascos de vacío: 8 atributos, 0 obligatorios). Así el cargador se
prueba contra los dos casos.

### 🔴 Tres cosas que el cargador tiene que saber

**1. La llave de obligatorio es `is_requried`, no `is_required`.** Es errata de
TikTok, no mía. Leer la grafía correcta devuelve `false` para todo: así se
documentó "cero obligatorios en todas las hojas", que es falso. Censo completo
sobre las 1,521 hojas AVAILABLE, 0 errores: **759 (49.9%) exigen atributos.**

**2. Lista cerrada = la llave `values` VIENE. Texto libre = NO VIENE.** No es un
array vacío: la llave está ausente. Comprobar con `if "values" in a`, no con
`if a["values"]`.

```jsonc
// lista cerrada
{"id": "100107", "name": "Tipo de garantía", "type": "PRODUCT_PROPERTY",
 "is_requried": true, "is_customizable": true, "is_multiple_selection": false,
 "values": [{"id": "1000054", "name": "Garantía del proveedor"},
            {"id": "1000057", "name": "Sin garantía"}, …]}

// texto libre — fíjate en que NO hay "values"
{"id": "102269", "name": "Dirección de Fabricante Nacional/Importador",
 "type": "PRODUCT_PROPERTY", "is_requried": true,
 "is_customizable": true, "is_multiple_selection": true}
```

**3. TikTok exige ID de atributo Y de valor**, nunca texto:

```json
{"id": "100107", "values": [{"id": "1000054"}]}      // lista cerrada
{"id": "102270", "values": [{"name": "127 V / 60 Hz"}]}  // texto libre
```

### Los obligatorios que existen en todo MX — son siete

| atributo | id | hojas | tipo | default sugerido |
|---|---|---|---|---|
| Tipo de garantía | `100107` | **694** | enum (4) | `1000054` *Garantía del proveedor* |
| Productos importados | `102254` | **609** | enum (2) | `1000058` *Sí* |
| Nombre de Fabricante Nacional/Importador | `102268` | 146 | libre | ⚠️ **vacío a propósito** |
| Dirección de Fabricante Nacional/Importador | `102269` | 146 | libre | ⚠️ **vacío a propósito** |
| Consumo de energía (V/W/Hz) | `102270` | 146 | libre | — |
| Tipo de instalación | `100795` | 11 | enum (14) | — |
| Nº de autorización SENASICA | `102514` | 1 | libre | — |

Los dos defaults son decisión de Brandon (12-ago). Los del importador **nacen
sin `default_value` a propósito**: son datos legales de Kubera y rellenarlos con
algo inventado sería declarar en falso. **Son el único bloqueo real hoy: 59 SKUs
publicados no pueden venderse por eso.**

⚠️ **Estos obligatorios NO se ven en borrador.** `AS_DRAFT` no los valida;
`LISTING` sí. Un semáforo verde para borrador **no** significa publicable.

⚠️ `/categories/{id}/rules` **no sirve** para predecirlos en MX: devuelve
`manufacturer.is_required` vacío aunque LISTING lo exija. La fuente buena es
esta llamada de atributos.

---

## 4 · SALES_PROPERTY — generan variantes

`TIKTOK_SALES_PROPERTY.csv` — 1,872 filas `(categoria_id, atributo)`.

| SALES_PROPERTY | hojas | % de 1,521 |
|---|---|---|
| **Color** | **1,275** | **83.8%** |
| Talla | 257 | 16.9% |
| Especificación | 237 | 15.6% |
| Edición | 46 | 3.0% |
| Tamaño de ropa de cama · Quilate | 13 c/u | 0.9% |
| Capacidad de almacenamiento · Tipo de cristal | 8 c/u | 0.5% |

**Color aparece en 8 de cada 10 hojas.** Meterlo en `product_attributes` no da
error: **crea el producto mal**, con una sola variante y el color perdido.

Para el modelo: `color` **no va** en `contenido.atributos` — necesita su propio
lugar. `services/tiktok_atributos.py` ya los excluye
(`type != "SALES_PROPERTY"`); el generador nuevo debe hacer lo mismo.

---

## 5 · El prompt de contenido

Para `tiktok_ia.py`. El validador ya está: **`services/tiktok_contenido.py`**,
con `validar(contenido, original)` y `validar_publicable(producto)`.

### ⚠️ Primero, la corrección que ya detectaste

El prompt de hoy en `_MEJORAR["tiktok"]` pide **"máx 45 caracteres"**. El límite
real de MX es **300** (doc de Create Product: `[1,255]` global, `[1,300]` BR y
MX). Son **255 caracteres tirados** en el campo que más pesa para que te
encuentren. El prompt de abajo ya usa 300.

### El prompt

```
Eres redactor de fichas de producto para TikTok Shop México.

Mejoras el título y la descripción de un producto del catálogo. No inventas:
sólo reescribes con lo que te doy.

PRODUCTO
  SKU:          {sku}
  Título hoy:   {titulo_actual}
  Descripción:  {descripcion_actual}
  Categoría:    {ruta_categoria}
  Atributos confirmados: {atributos_validados}

REGLAS
1. NO INVENTES DATOS. Si no sabes el material, los watts, la capacidad o las
   medidas, no los menciones. Un dato inventado se publica sin dar error y
   nadie se entera hasta que un cliente reclama.
2. El TÍTULO manda: es lo que busca la gente. Empieza por QUÉ ES el producto,
   después su rasgo distintivo. Hasta 300 caracteres — úsalos, pero sin
   rellenar con palabras vacías. Sin emojis, sin MAYÚSCULAS sostenidas, sin
   signos de admiración.
3. Escribe como busca un comprador mexicano, no como habla un catálogo chino.
   "Hervidor eléctrico" y no "Kettle 1.7L Multifuncional Home Appliance".
4. NADA de promesas que no controlamos: envío gratis, entrega en X días,
   garantía de por vida, "el mejor", "#1", precios.
5. La DESCRIPCIÓN en HTML simple: <p>, <ul>, <li>, <strong>. Nada de <img>,
   <script>, <table> ni estilos.
6. 3 a 6 puntos clave, cada uno un beneficio concreto, no un adjetivo.

SALIDA — sólo JSON:
{
  "titulo": "<máx 300 caracteres>",
  "descripcion_html": "<HTML simple>",
  "puntos_clave": ["<punto 1>", "..."],
  "palabras_clave": ["<lo que teclearía un comprador>"],
  "confianza": 0.0,
  "flags": ["<lo que NO pudiste confirmar del producto>"]
}
```

### Qué decide el código (no el prompt)

| Comprobación | Por qué |
|---|---|
| Título ≤ 300 · descripción ≤ 10,000 | **Rechaza, no trunca**: cortar a media palabra queda peor que el original |
| Lista blanca de HTML | `<table>` se acepta pero **TikTok lo convierte en imagen**: el texto deja de ser editable |
| `<img>` con aviso propio | sólo URLs rehospedadas por TikTok; una de chunche.shop se rechaza **al publicar**, no al guardar |
| Sin emojis · sin MAYÚSCULAS sostenidas | |
| Promesas no verificables | ⚠️ lista **supuesta**, no medida — TikTok no publica una |
| `flags` ⇒ descarte del dato | un dato inventado **no da error**. Caso real en atributos: puso `"1.5V"` y anotó *"voltaje no confirmado"* |
| **El título conserva palabras del original** | La que más importa — ver abajo |

### 🔴 Por qué existe la última comprobación

Un título puede quedar impecable de forma y **describir otro producto**. Caso
real del 12-ago, del recomendador de categoría: *"Collar de recuperación para
gato"* (cono veterinario) acabó en **Accesorios de moda → Joyas para disfraces**.
Con confianza, sin error, y sin quedar marcado como aproximado.

El mismo modo de fallo aplica al contenido. Si la propuesta no comparte **ni una
palabra** con el original, se descarta: es más barato quedarse con el título feo
que vender otra cosa.

### Y aparte, los bloqueantes que no son contenido

`validar_publicable()` los revisa por separado porque vienen de Woo, no de la IA
— y son los que hacen rebotar un borrador que se veía perfecto:

| | |
|---|---|
| stock | `[1, 99999]` — el 0 **no** es válido |
| peso | > 0 kg (18 SKUs traen `0.0`, y algunos **188 y 871 kg**) |
| dimensiones | **L+A+H ≤ 160 cm** |
| imágenes | 1–9 |

---

## Lo que no te puedo dar todavía

1. **Razón social y dirección fiscal de Kubera** como Importador → 59 SKUs
   bloqueados. Es de Brandon.
2. **La lista de palabras prohibidas está SUPUESTA**, por analogía con Amazon.
   TikTok no publica una y no la medí.
3. **`tiktok_atributos.validar` tiene un bug abierto**: no funde atributos
   repetidos por `id` y `LISTING` los rechaza (`12052254`). Afectó a 40 de 1,221
   payloads. Hoy sólo lo parcha `tk_activar.py::fundir_repetidos` — **el
   generador nuevo lo arrastrará** si no se arregla en el servicio.
