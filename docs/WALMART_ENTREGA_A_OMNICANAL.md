# Walmart MX → Omnicanal: atributos por categoría y contenido con IA

> **Estado al 14-ago-2026:** 237 artículos en Walmart, **221 `PUBLISHED`**, de
> los que **176 tienen stock real**. 147 feeds en el histórico: 925 recibidos,
> 229 exitosos, 696 fallidos. Todo lo de aquí está medido contra la API o
> contra el esquema oficial; lo que no se pudo verificar va marcado.
>
> Hermano de [TEMU_ENTREGA_A_OMNICANAL.md](TEMU_ENTREGA_A_OMNICANAL.md) y
> [TIKTOK_ENTREGA_A_OMNICANAL.md](TIKTOK_ENTREGA_A_OMNICANAL.md).
> El detalle de límites, cuotas y dinero vive en
> [WALMART_MX_MANUAL.md](WALMART_MX_MANUAL.md).

| # | Qué | Dónde | Filas |
|---|---|---|---|
| 1 | Atributos de las 75 categorías | `scripts/walmart_field_requirements.py --csv` | **3,326** |
| 2 | Prompts + validadores | `services/walmart_contenido.py` | — |
| 3 | Poda por categoría | `scripts/publicar_walmart.py::campos_visible` | 3 categorías |
| 4 | Cola de publicación | `walmart_cola.csv` | 458 |

---

## 1 · EL ALTA QUE FUNCIONÓ — payload literal

`POST /v3/feeds?feedType=MP_ITEM_INTL`, multipart, campo `file`. Este es un
artículo **que está vivo hoy**, con el id que Walmart le acuñó:

**`TEC-0047-NEG` → `wpid 13DB0C7APVEE` · `gtin 00469069072203`** (el GTIN lo
genera Walmart bajo la exención; nosotros mandamos `CUSTOM`)

```jsonc
{
  "MPItemFeedHeader": {
    "subCategory": "health_and_beauty_electronics",  // ← la puerta con exención
    "sellingChannel": "marketplace", "processMode": "REPLACE",
    "mart": "WALMART_MEXICO", "locale": "es",        // enums de UN valor
    "version": "3.11", "subset": "EXTERNAL"
  },
  "MPItem": [{
    "Orderable": {
      "sku": "TEC-0047-NEG",
      "productIdentifiers": {"productIdType": "GTIN", "productId": "CUSTOM"},
      "productName": "Cafetera Italiana Moka Express Aluminio 6 Tazas 300ml",
      "brand": "Ferrahome", "manufacturer": "Ferrahome",
      "price": 382.05,
      "ProductTaxCode": 52161500,
      "msiEligible": "No",                     // String, NO booleano
      "shortDescription": "…",
      "keyFeatures": ["Cafetera Italiana Moka Express Aluminio 6 Tazas 300ml"],
      "mainImageUrl": "https://images.weserv.nl/?url=chunche.shop/…&output=jpg",
      "productSecondaryImageURL": ["…", "…"],
      "ShippingWeight": {"measure": 0.3, "unit": "kg"},
      "countryOfOriginAssembly": ["China"],    // array, en español
      "hazardousMaterialsInd": "No", "hasNomCertification": "No"
    },
    "Visible": {
      "Electrónicos": {                        // ← etiqueta EN ESPAÑOL, exacta
        "colorCategory": ["Negro"], "gender": "Unisex",
        "assembledProductLength": {"measure": 10.31, "unit": "cm"},
        "assembledProductWidth":  {"measure": 5.0,   "unit": "cm"},
        "assembledProductHeight": {"measure": 3.0,   "unit": "cm"},
        "assembledProductWeight": {"measure": 0.3,   "unit": "kg"}
      }
    }
  }]
}
```

**Las tres cosas que se pierde la gente:**

| Lo que parece | Lo que es |
|---|---|
| La electrónica va en `electronics_accessories` | va en **`health_and_beauty_electronics`**, cuya etiqueta es **«Electrónicos»**. `electronics_accessories` NUNCA publicó nada |
| El bloque `Visible` es fijo | **cambia por categoría** y un campo de más tumba el lote entero |
| El esquema publicado manda | es la **3.19** y producción corre la **3.11**. Donde chocan, manda lo medido |

---

## 2 · LOS ERRORES, TAL CUAL

Reconstruidos de los 147 feeds con `GET /v3/feeds` (5000/min, gratis). Ordenados
por cuántos SKUs mataron:

| SKUs | Mensaje literal | Qué era de verdad |
|---|---|---|
| **98** | `'modelNumber' is not a valid field` | campo de más en Electrónicos |
| **80** | `'gender' is not a valid field` | campo de más en Almacenamiento |
| 53 | `Main image URL setup failed` | imagen (ver §6 del manual) |
| **49** | `You are not authorized to set up 'CUSTOM' Product IDs` | **sin exención de UPC** |
| 41 | `It looks like there was a glitch. Please try again` | transitorio, se reintenta |
| 31 | `` `Linea de Producto` is a required attribute `` | obligatorio que el 3.19 NO declara |
| 31 | `` `Actividad` is a required attribute `` | ídem |
| 31 | `'countPerPack' is not a valid field` | campo de más en Juguetes |
| 21 | `This item is currently under compliance review` | SKU en dos feeds vivos |
| 11 | `We couldn't download the image from your URL` | imagen |
| 6 | `Unexpected system error occurred in item setup` | de Walmart, se reintenta |
| 2 | `'Foto adicional' requires a minimum of '1' entries` | producto con 1 sola imagen |
| 1 | `Prohibited Product Policy: Military and Law Enforcement` | `TEC-1813`, despublicado |

> 🔴 **Solo DOS mensajes hablan de la exención de UPC o de su ausencia.** Los
> demás significan *"morí antes de llegar ahí"*. Walmart valida **por etapas** y
> reporta **solo la primera que falla**. Leer la ausencia del error de UPC como
> "sí tenemos permiso" es lo que mandó lotes enteros a la basura.

---

## 3 · LOS ATRIBUTOS POR CATEGORÍA — el hueco cerrado

### No hay API. La fuente es un archivo.

En TikTok esto se pide con `GET /categories/{id}/attributes`. **En Walmart MX
esa API no existe:** `POST /v3/items/spec` da **404** con credenciales MX, y en
Global está marcada **"US only"** — no llega ni migrando. La fuente es:

```
https://developer.walmart.com/file/mp/mx/MX_MP_ITEM_INTL_SPEC.json
3.9 MB · HTTP 200 SIN credenciales
```

`scripts/walmart_field_requirements.py` lo convierte en tabla:

| | |
|---|---|
| Categorías | **75** |
| Filas (campo × categoría) | **3,326** |
| **Obligatorios** | **445** — 24 comunes del bloque `Orderable` + **421 por categoría** |
| Con lista cerrada (`enum`) | **1,334** |
| Con etiqueta en español | todas — y **es la que Walmart usa al reclamar** |

```bash
python -m scripts.walmart_field_requirements --csv WALMART_FIELD_REQUIREMENTS.csv
python -m scripts.walmart_field_requirements --categoria "Electrónicos"
```

El CSV sale con el esquema de `channel.field_requirements`:
`canal · categoria · bloque · campo · etiqueta_es · obligatorio · tipo ·
lista_cerrada · n_valores · min · max · ejemplos · descripcion ·
veredicto_produccion · evidencia_produccion · exencion_upc`.

### ⚠️ El archivo miente en los dos sentidos — la columna que lo dice

Por eso cada fila trae `veredicto_produccion`. Estas 9 correcciones costaron un
lote cada una y viven en `CORRECCIONES_MEDIDAS`:

| Categoría | Campo | El archivo dice | Producción dice |
|---|---|---|---|
| Electrónicos | `modelNumber` | válido | **RECHAZADO** — 85 de 85 muertos |
| Accesorios Electrónicos | `modelNumber` | válido | **RECHAZADO** — 5 feeds de 85 |
| Almacenamiento | `gender` | válido | **RECHAZADO** — 83 de 83 |
| Juguetes | `countPerPack` | válido | **RECHAZADO** — 33 de 33 |
| Juguetes | `productLine` | opcional | **OBLIGATORIO** — 31 SKUs |
| Juguetes | `activity` | opcional | **OBLIGATORIO** — 31 SKUs |
| Cocina, Decoración y Otros | `size` | opcional | **OBLIGATORIO** — 3 SKUs, 5-ago |
| Cocina, Decoración y Otros | `gender` | opcional | **OBLIGATORIO** — 3 SKUs, 5-ago |
| Otros Electrónicos | `wattage` | opcional | **OBLIGATORIO** — sonda 7-ago |

**Regla:** el JSON sirve para descubrir **nombres de campo, etiquetas en
español, listas cerradas y unidades** — todo eso sí coincide. La
**obligatoriedad y la validez** se verifican contra la 3.11 con un feed de 1 SKU,
y el resultado se escribe en esa tabla. No se adivina.

### Lo que estamos dejando sobre la mesa

El publicador manda un bloque fijo. Contra lo que la categoría admite:

| Categoría | Campos | Obligatorios | Mandamos hoy | **Opcionales sin usar** |
|---|---|---|---|---|
| Electrónicos | 27 | 5 | 6 | **17** (3 con lista cerrada) |
| Disfraces | 32 | 8 | 10 | **20** (4 con lista cerrada) |
| Cocina, Decoración y Otros | 81 | 9 | 10 | **68** (15 con lista cerrada) |

No es cosmética: `offerScore` y `contentScore` de la Listing Quality API
(`POST /v3/insights/items/listingQuality/items`, 2/min) miden qué tan completa
está la ficha, y una ficha pelona se entierra en los resultados.

---

## 4 · EL CONTENIDO QUE YA PUBLICAMOS ESTÁ MAL

Medido sobre los 244 payloads de Electrónicos que se enviaron el 7-ago:

| Hallazgo | Cuántos |
|---|---|
| **`keyFeatures` con UNA sola viñeta, copia literal del título** | **244 de 244 (100%)** |
| Título fuera del rango 50–75 | 80 de 244 |
| Título con MAYÚSCULAS sostenidas | 25 |
| `brand` = `"Ferrahome"` puesto por default | **244 de 244** |
| **Frases penalizadas VIVAS en Walmart hoy** | **5** |

Los 5 con frase penalizada, que pueden inactivar el producto:

```
TEC-1023-NEG      "garantizada"
TEC-1284-NEG-27"  "lo mejor"
TEC-1372-MET      "la mejor"
TEC-1769-PLA      "lo mejor"
TEC-2192-HONDA    "sin fallas"
```

> El de `keyFeatures` es el más caro y el más fácil: el propio esquema dice, en
> su descripción del campo, *"Recomendamos encarecidamente utilizar mínimo tres
> funciones clave"* y **"Deben de ser diferentes al Título del Producto y no
> repetirse en la Descripción"**. Mandamos exactamente lo contrario, 244 veces.
> Se corrige con `MP_MAINTENANCE`, sin re-publicar.

Y `shortDescription` es la descripción de Woo con el HTML quitado: viñetas
aplastadas en un párrafo (*"Características principales: … Beneficios que vas a
notar: …"*), con contradicciones internas — `TEC-0047-NEG` se titula
*"6 Tazas"* y su descripción dice *"disponible en versión de 3 a 6 tazas"*.

---

## 5 · LOS PROMPTS

Ambos en `services/walmart_contenido.py`. Se arman con
`build_prompt_contenido(...)` y `build_prompt_atributos(...)`, que **inyectan la
lista real de campos de esa categoría** — no una lista escrita a mano.

### 5.1 · Contenido (título, descripción, beneficios)

Es el prompt de Brandon, con los límites del esquema puestos donde iban:

```
Actúa como especialista en optimización de listados (content merchandising)
para Walmart Marketplace México. Te doy información cruda de un producto y la
reescribes siguiendo ESTRICTAMENTE las reglas de abajo.

NO INVENTES DATOS TÉCNICOS que no te dé (medidas, materiales, certificaciones,
potencias, capacidades). Si falta un dato, escribe "[FALTA DATO: qué falta]" en
vez de inventarlo. En Walmart un dato inventado NO da error: se publica y nadie
se entera hasta que un cliente reclama.

PRODUCTO
    SKU / Categoría / Nombre-marca hoy / Título hoy / Ficha cruda
    Atributos conocidos / Palabras clave

1 · TÍTULO
   · Entre 50 y 75 caracteres. Tope duro 200.
   · [Marca] + [Artículo] + [Característica o material] + [Modelo/tamaño/color]
   · Sin MAYÚSCULAS sostenidas, sin emojis, sin símbolos promocionales.
   · NO repitas la categoría si es redundante. NADA de keyword stuffing.
   · Si no hay marca reconocida, usa el fabricante o "Sin marca".
   · Escribe como busca un comprador mexicano, no como habla un catálogo chino.

2 · DESCRIPCIÓN
   · UN PÁRRAFO corrido. Nada de viñetas, nada de HTML. Máximo 4000.
   · Usos, funcionalidades y características. Keywords de forma natural.

3 · CARACTERÍSTICAS — especificaciones OBJETIVAS y verificables.

4 · BENEFICIOS
   · Entre 3 y 8 viñetas. Máximo 50 caracteres cada una.
   · Qué GANA el cliente, no la ficha técnica repetida.
   · DEBEN ser distintas del título y NO repetir la descripción.
   · PROHIBIDO: "Garantizado", "Efecto inmediato", "arte de magia", promesas
     absolutas/médicas, superlativos no comprobables, precios, ofertas, envíos.

SALIDA — SOLO JSON: {titulo, descripcion, caracteristicas[], beneficios[],
                     palabras_clave[], marca, confianza, flags[]}
```

Los límites **50-75** y **3-8 viñetas** son regla de negocio de Kubera. Los
**200**, **4000** y **50 caracteres por viñeta** salen del esquema
(`productName.maxLength`, `shortDescription.maxLength`, y la descripción literal
de `keyFeatures`). El 200 **cierra el pendiente #7 del manual**, que lo tenía
como `[SUPUESTO]` heredado.

### 5.2 · Atributos

Le pasa a la IA los obligatorios de esa categoría **y hasta 12 opcionales**, cada
uno con su etiqueta en español, su descripción y su lista cerrada completa:

```
OBLIGATORIOS de esta categoría — si falta alguno, Walmart RECHAZA el artículo:
  · colorCategory — «Gama Color»
      LISTA CERRADA, copia uno EXACTO: Cedro | Aqua | Rojo | … (36 valores)
  · assembledProductWeight — «»
      LISTA CERRADA, copia uno EXACTO: lb | kg | oz | g

OPCIONALES — llénalos SOLO si el dato está en la información de arriba:
  · technology — «Tecnología»
      Indicar las características especiales que mejoran el funcionamiento…
      ejemplo: SensoCare
  · isCordless — «Es Inalámbrico»
      LISTA CERRADA, copia uno EXACTO: Sí | No

REGLAS
1. Donde diga LISTA CERRADA, copia un valor EXACTO, con acentos y mayúsculas.
   Un valor fuera de la lista tumba el artículo entero.
2. Si un obligatorio no se deduce de lo dado, ponlo en `flags`, no lo inventes.
3. NO llenes un opcional "por llenarlo".
4. Las medidas van con número y unidad por separado, nunca "10 cm" junto.
```

---

## 6 · QUÉ DECIDE EL CÓDIGO, NO EL PROMPT

`validar_contenido()` y `validar_atributos()`. **27 pruebas, 27 pasan.**

| Comprobación | Por qué |
|---|---|
| Título 50–75, tope 200 · descripción ≤4000 | **Reporta, no trunca**: cortar a media palabra queda peor |
| Beneficios 3–8, ≤50 caracteres | El límite es del esquema |
| **Beneficio ≠ título · beneficio ∉ descripción** | Regla LITERAL del esquema. Hoy la violan 244 de 244 |
| Descripción sin viñetas ni HTML | Walmart la quiere en un párrafo |
| Frases penalizadas, **con límite de palabra** | Ver abajo |
| `[FALTA DATO]` ⇒ el campo NO se publica | Un hueco es mejor que una mentira |
| Sin emojis · sin MAYÚSCULAS sostenidas | |
| **El título conserva palabras del original** | La que más importa — ver abajo |
| Valor fuera de lista cerrada ⇒ se descarta | La IA copia mal por más que el prompt insista |
| Campo con veto MEDIDO ⇒ se descarta **primero** | Con el mensaje de producción, no un "no existe" genérico |
| Medidas redondeadas a 2 decimales | `cannot exceed 2 decimal points` |
| Obligatorios faltantes se reportan | Es lo que evita mandar un artículo que va a rebotar |

### 🔴 Las dos comprobaciones que existen por un caso real

**1 · El límite de palabra en las frases penalizadas.** Buscarlas como
subcadena bloquea productos legítimos: `"cura"` caza dentro de *mani**cura***,
*pedi**cura*** y *os**cura**s*; `"la mejor"` dentro de *"mejora tu
visibilidad"*. Medido sobre los 244: la versión ingenua marcaba 8 artículos y
**3 eran falsos positivos**. Con `(?<![a-z0-9])…(?![a-z0-9])` quedan los 5
reales.

**2 · El título tiene que compartir palabras con el original.** Un título puede
quedar impecable de forma y **describir otro producto**. Es el mismo modo de
fallo que en TikTok mandó un cono veterinario a *Joyas para disfraces*, con
confianza y sin error. Si la propuesta no comparte **ni una palabra** con el
original, se descarta: es más barato quedarse con el título feo que vender otra
cosa.

---

## 7 · LO QUE SE SUBIÓ AL BACKEND

| Archivo | Qué es | Estado |
|---|---|---|
| **`services/walmart_contenido.py`** | Prompts + validadores + catálogo de campos | **nuevo** |
| **`scripts/walmart_field_requirements.py`** | Los 3,326 campos → CSV, con `CORRECCIONES_MEDIDAS` | **nuevo** |
| `scripts/publicar_walmart.py` | `campos_visible` por categoría + la poda en `_item()` | v0.107.0 |
| `scripts/estado_walmart.py` | Consulta de estado | ya existía |

El esquema (3.9 MB) **no se versiona en el repo**. Los dos módulos lo buscan en
`WM_SPEC_JSON`, en el respaldo del escritorio y en el directorio actual, en ese
orden, y dicen de dónde bajarlo si no lo encuentran.

---

## 8 · LO QUE FALTA, CON DUEÑO

| # | Qué | Dueño |
|---|---|---|
| 1 | 🔴 **Confirmar en Seller Center** qué categorías tienen exención. Los folios 15776196 y 15822204 llegaron sin categoría; COLCHONES sigue pendiente. Desbloquea 225 SKUs | Brandon |
| 2 | 🔴 **Corregir los 5 SKUs con frase penalizada** que están vivos — `MP_MAINTENANCE`, sin republicar | Código, con su dale |
| 3 | 🔴 **`keyFeatures` de los 221 publicados**: hoy es 1 viñeta = el título. Mínimo 3 y distintas | Código, con su dale |
| 4 | **De dónde salen peso y volumen.** 281 de los 458 en cola pagarían volumétrico: 2,674 kg facturables contra 440 reales | Decisión de negocio |
| 5 | `brand` = "Ferrahome" en los 244. ¿Marca real, "Sin marca", o por familia? | Decisión de negocio |
| 6 | Conectar `walmart_contenido` al publicador en 3 fases, como Temu (`fichas` → `enriquecer` → `publicar`) | Código |
| 7 | Cargar `WALMART_FIELD_REQUIREMENTS.csv` a `channel.field_requirements` | Código |
| 8 | Piloto de 1 SKU en Almacenamiento (sin `gender`) y Juguetes (con `activity` + `productLine`) | Código, con su dale |
| 9 | La lista de frases penalizadas es **regla de negocio, no cita documental** — Walmart no publica una que hayamos podido leer | Brandon confirma |
| 10 | Clave SAT `52161500` = "Equipos audiovisuales" le queda chica: por esa puerta entran herramientas, autopartes y artículos deportivos. No frena la publicación, pero el CFDI sale mal | Facturación |

> ⚠️ **Nada de esto se ha ejecutado contra Walmart.** No se mandó ningún feed
> para escribir este documento: los 147 feeds analizados son históricos y todo
> se leyó con `GET`, que es gratis (5000/min).
