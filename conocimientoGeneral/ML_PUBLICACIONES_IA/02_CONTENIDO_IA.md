> Extraído de producción el 2026-09-03, commit 1a7da7e.
> ESTO ES UNA COPIA DE CONSULTA. La verdad vive en main; si algo no cuadra,
> gana main y hay que re-extraer.

# Cómo el panel redacta título, descripción y demás contenido con IA

Backend en producción: `v0.371.0` (`backend/main.py:132`).

Este archivo se lee solo. Lleva los **prompts completos y literales**, los
límites de cada canal y las dos trampas que ya costaron dinero: la diferencia
entre **generar** y **mejorar**, y los límites que son en **BYTES y no en
caracteres**.

---

## 0. El mapa en 40 segundos

Hay **un solo botón** que un KAM usa de verdad: **"Mejorar con IA"** en el
Estudio de Producto (`frontend/components/ProductStudio.tsx:590`). Ese botón
llama a `POST /api/ia/mejorar` y, en paralelo, a `POST /api/ia/precio-competencia`
(`ProductStudio.tsx:607-610`).

Todo lo demás (`/api/ia/generar`, `/api/ia/generadores`, `/api/ia/titulo`,
`/api/ia/prompts`) existe, responde, y **casi nada lo usa**. Ver §3 y §9.

```
Estudio de Producto  ──►  POST /api/ia/mejorar  ──►  routers/ia.py:77
                                                        │
                          ┌─────────────────────────────┴─────────────┐
                          │  ia_generadores.mejorar(canal, producto)  │
                          │  services/ia_generadores.py:336           │
                          └─────────────────────────────┬─────────────┘
                                                        │ desvía por canal
        ┌─────────────┬─────────────────┬───────────────┴──────┬──────────────┐
        │ amazon      │ tiktok          │ temu                 │ mercado_libre│ general
        ▼             ▼                 ▼                      ▼              ▼
   amazon_ia     tiktok_ia         temu_ia            (se queda aquí,   (se queda aquí,
   .mejorar()    .mejorar()        .mejorar()          _MEJORAR[..])     _MEJORAR[..])
        │             │                 │                      │
        │             │                 │                      └─► ml_atributos
        │             │                 │                          (atributos REALES
        │             │                 │                           de la categoría ML)
        ▼             ▼                 ▼
   amazon_       tiktok_          temu_contenido
   contenido     contenido        .validar_contenido / .validar_atributos
   .validar()    .validar()
        │             │                 │
        └─────────────┴────────┬────────┘
                               ▼
              terminos_protegidos.revisar_campos()   (marcas registradas)
                               ▼
              channel_content.guardar()  →  enrich.channel_content (BD kubera)
```

**La regla de la casa que gobierna todo esto, escrita en cuatro archivos
distintos: la IA propone, el CÓDIGO valida, y lo que no pasa NO se manda.**
(`amazon_contenido.py:16-17`, `tiktok_contenido.py:4-5`, `temu_contenido.py:6-7`,
`terminos_protegidos.py:10-11`).

---

## 1. Los cinco endpoints (`backend/routers/ia.py`)

| Endpoint | Línea | Qué hace | ¿Escribe algo? |
|---|---|---|---|
| `GET /api/ia/generadores?canal=…` | `ia.py:58-61` | Catálogo de botones por canal | No |
| `POST /api/ia/generar` | `ia.py:64-69` | Ejecuta **un** generador suelto y devuelve texto | **No** |
| `POST /api/ia/mejorar` | `ia.py:77-88` | Genera VARIOS campos a la vez, valida y **persiste** | **Sí**: `enrich.channel_content` |
| `POST /api/ia/precio-competencia` | `ia.py:96-101` | Precio sugerido (solo para mostrar) | No |
| `POST /api/ia/titulo` | `ia.py:111-113` | Atajo histórico (compat) | No |
| `GET /api/ia/prompts` | `ia.py:116-119` | Devuelve `claude.PROMPTS_CANAL` (compat) | No |

### El contrato de entrada: `ProductoCtx` (`ia.py:34-49`)

Lo mismo para todos los POST. Ningún campo es obligatorio:

```python
class ProductoCtx(BaseModel):
    nombre: str = ""
    marca: str | None = None
    modelo: str | None = None
    categoria: str | None = None      # ruta legible "A › B › C"
    ml_cat_id: str | None = None      # id de categoría ML (para traer atributos reales)
    sku: str | None = None
    wc_id: int | None = None          # ahorra una lectura a WordPress (Amazon)
    descripcion: str | None = None
    precio: float | None = None
    costo: float | None = None
    publico: str | None = None
    atributos: list[AtributoIn] = []  # [{nombre, valor}]
```

Cuerpos de petición:

```jsonc
// POST /api/ia/generar
{"canal": "mercado_libre", "generador": "titulo", "producto": { ...ProductoCtx }}

// POST /api/ia/mejorar
{"canal": "amazon", "producto": { ...ProductoCtx }}

// POST /api/ia/precio-competencia
{"producto": { ...ProductoCtx }, "con_lista": true}

// POST /api/ia/titulo   (compat)
{"nombre": "...", "canal": "mercado_libre", "contexto": ""}
```

---

## 2. El motor: `_completar()` — un solo sitio llama al modelo

`backend/services/ia_generadores.py:34-82`. **Todo** el contenido con IA del
panel pasa por aquí (Amazon, TikTok, Temu, ML, competencia, sugerencia de
productType, atributos de TikTok…). La única excepción es `ml_atributos`, que
tiene su propio cliente DeepSeek (§6.2), y `services/claude.py`, que es compat
(§9).

Orden de proveedores, sin configuración: **DeepSeek primero, Claude de
respaldo, y si no hay ninguna clave, error legible.**

```python
def _completar(system: str, user: str, max_tokens: int = 900) -> dict[str, Any]:
    # 1) DeepSeek (API compatible con OpenAI)
    if settings.deepseek_api_key:
        r = httpx.post(
            f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            json={
                "model": settings.deepseek_model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user",   "content": user}],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=90.0,
        )
        ...  # si falla: log.warning("DeepSeek falló, intento Claude")

    # 2) Claude (anthropic)
    if settings.anthropic_api_key:
        cli = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = cli.messages.create(model=_CLAUDE_MODEL, max_tokens=max_tokens,
                                  system=system,
                                  messages=[{"role": "user", "content": user}])
        ...

    return {"ok": False,
            "motivo": "Configura DEEPSEEK_API_KEY o ANTHROPIC_API_KEY para generar contenido."}
```

| Cosa | Valor | Dónde |
|---|---|---|
| Modelo DeepSeek (default) | `deepseek-chat` | `backend/config.py:85` |
| Base URL DeepSeek (default) | `https://api.deepseek.com` | `backend/config.py:84` |
| Modelo Claude (fijo en código) | `claude-opus-4-8` | `ia_generadores.py:28` |
| Temperatura (DeepSeek) | `0.7` | `ia_generadores.py:49` |
| Timeout (DeepSeek) | `90.0` s | `ia_generadores.py:51` |

Devuelve siempre un dict: `{"ok": True, "texto": …, "modelo": …, "proveedor": "deepseek"|"claude"}`
o `{"ok": False, "motivo": …}`.

> **La respuesta llega como TEXTO, no como JSON estructurado.** El JSON se
> extrae después con `_parse_json` (`ia_generadores.py:320-333`), que quita las
> vallas ```` ```json ```` y, si aún no parsea, se queda con el primer `{...}`
> que encuentre por regex. Por eso todos los prompts terminan diciendo
> "Devuelve SOLO JSON válido, sin texto alrededor": no hay `response_format`
> que lo garantice — salvo en `ml_atributos`, que sí lo pide (§6.2).

### El contexto del producto: lo que la IA REALMENTE ve

`ia_generadores._contexto()` (`ia_generadores.py:88-111`). Es lo que se le manda
como mensaje de usuario en Amazon, ML, general y competencia:

```
Nombre actual: {nombre}
Marca: {marca}
Categoría: {categoria}
Precio: ${precio} MXN
Público objetivo: {publico}
Atributos conocidos: {nombre}: {valor}; {nombre}: {valor}; …
Descripción actual:
{descripción SIN HTML, recortada a 1500 caracteres}
```

Cada línea se omite si el campo viene vacío. Si no queda ninguna:
`"Sin datos del producto."`

**Lo que NO viaja al modelo, aunque esté en `ProductoCtx`:** `modelo`, `sku`,
`wc_id`, `costo`. (El `costo` sí viaja, pero solo en `precio-competencia`, que
arma su propio mensaje — `competencia.py:165-177`.) TikTok y Temu arman su
propio contexto y ahí sí incluyen el SKU (`tiktok_ia.py:86-96`,
`temu_contenido.py:219-225`).

---

## 3. GENERAR vs MEJORAR — la diferencia que hay que entender

Esta es **la** distinción del archivo. No son dos versiones de lo mismo.

| | `POST /api/ia/generar` | `POST /api/ia/mejorar` |
|---|---|---|
| Alcance | **UN** campo (el generador que pidas) | **VARIOS** campos en una llamada |
| Salida | Texto libre | JSON estructurado (`campos`) |
| ¿Valida límites? | **NO. Nada.** | Sí, con el validador del canal |
| ¿Reintenta si falla la validación? | No aplica | Sí, **UNA** ronda de reparación |
| ¿Sustituye marcas registradas? | No | Sí (`terminos_protegidos`) |
| ¿Consulta requisitos reales de la categoría? | No | Sí (Amazon/TikTok/Temu/ML) |
| ¿Guarda en la BD? | **No** — se pierde al cerrar | Sí, `enrich.channel_content` |
| Canales que atiende | `amazon` (solo imágenes), `mercado_libre`, `general` | `amazon`, `tiktok`, `temu`, `mercado_libre`, `general` |
| Concurrencia | **Síncrono** (`def generar`, `ia.py:65`) | `async` + `asyncio.to_thread` |

Las consecuencias prácticas:

1. **`generar` NO puede escribir contenido de Amazon.** El único generador que
   le queda a Amazon es el de **imágenes** (`ia_generadores.py:228-231`). Los
   cinco generadores de campo que había (título, highlights, bullets,
   descripción, atributos) **se borraron el 13-ago-2026 por decisión de
   Brandon** — el motivo está escrito en el propio archivo
   (`ia_generadores.py:128-147`):

   > *"dos textos para el mismo campo es una invitación a editar el que no sale
   > a producción. Los bullets son el ejemplo — el viejo pedía el prefijo
   > «[CARACTERÍSTICA EN MAYÚSCULAS]:» y el spec vivo pide oraciones completas:
   > el mismo producto habría salido distinto según qué botón se apretara."*

   Lo mismo pasó con TikTok: `GENERADORES["tiktok"] = []` (`ia_generadores.py:250`).
   El generador borrado pedía título de "máx 45 caracteres" **cuando MX admite
   300** (`ia_generadores.py:246-249`).

2. **`generar` es síncrono en una corrutina.** `routers/ia.py:65` declara
   `def generar` (no `async def`), así que FastAPI lo corre en un threadpool y
   no bloquea el loop. Pero `competencia.precio_competencia` sí es `def` y
   llama `requests.get` y `_completar` de forma directa (`competencia.py:87,
   110, 179`) — funciona por la misma razón. **Ojo con la regla 11 de la casa
   si alguien lo convierte a `async def` sin envolverlo en `asyncio.to_thread`.**

3. **`mejorar` es lo único que deja rastro.** Si el objetivo es "que quede
   escrito", es `mejorar`.

---

## 4. El catálogo de generadores (`GET /api/ia/generadores`)

Fuente única: el diccionario `GENERADORES` (`ia_generadores.py:222-251`). El
frontend lo consume para pintar los botones. La clave `system` **nunca** sale al
frontend (`_PRIVADOS = {"system"}`, `ia_generadores.py:254`).

### Respuesta completa por canal (a 2026-09-03)

```jsonc
// GET /api/ia/generadores?canal=amazon
{"canal": "amazon", "generadores": [
  {"id": "imagenes", "label": "Set de imágenes", "icono": "image",
   "max_tokens": 1800, "tipo": "imagenes",
   "descripcion": "Plan de 5 imágenes + prompts IA"}
]}

// GET /api/ia/generadores?canal=mercado_libre
{"canal": "mercado_libre", "generadores": [
  {"id": "titulo",      "label": "Título",        "icono": "type",       "max_tokens": 300,  "descripcion": "Título ML ≤60 caracteres"},
  {"id": "ficha",       "label": "Ficha técnica", "icono": "tags",       "max_tokens": 900,  "descripcion": "Atributos de la publicación"},
  {"id": "descripcion", "label": "Descripción",   "icono": "align-left", "max_tokens": 1000, "descripcion": "Descripción en texto plano"}
]}

// GET /api/ia/generadores?canal=general
{"canal": "general", "generadores": [
  {"id": "titulo",      "label": "Título",      "icono": "type",       "max_tokens": 200, "descripcion": "Título comercial para la tienda"},
  {"id": "descripcion", "label": "Descripción", "icono": "align-left", "max_tokens": 900, "descripcion": "Descripción HTML para WooCommerce"}
]}

// GET /api/ia/generadores?canal=tiktok    → {"canal": "tiktok", "generadores": []}
// GET /api/ia/generadores?canal=temu      → {"canal": "temu",   "generadores": []}   (no existe la clave)
// GET /api/ia/generadores?canal=walmart   → {"canal": "walmart","generadores": []}   (no existe la clave)
```

Un canal desconocido no da error: devuelve lista vacía (`GENERADORES.get(canal, [])`,
`ia_generadores.py:261`).

### Qué se le manda al modelo en `generar`

`ia_generadores.py:419-423`:

- **system** = el `system` del generador (los cinco están abajo, literales)
- **user** =
  ```
  Datos del producto:
  {_contexto(producto)}

  Genera el contenido solicitado siguiendo tus instrucciones.
  ```
- **max_tokens** = el del generador; si no lo trae, `900`

Y devuelve el resultado de `_completar` más `{canal, generador, label, tipo}`
(`ia_generadores.py:424-425`). Si el generador no existe:
`{"ok": False, "motivo": "Generador 'X' no existe para Y."}` (`:417`).

---

## 5. Los prompts de GENERAR, literales

### 5.1 `mercado_libre` / `titulo` — `_ML_TITULO` (`ia_generadores.py:185-190`)

```
Eres experto en publicaciones de Mercado Libre México. Genera un TÍTULO de máximo 60 caracteres, con las palabras clave más buscadas al inicio, sin signos promocionales ni datos de contacto. Devuelve solo el título y, debajo, «(N caracteres)».
```

### 5.2 `mercado_libre` / `ficha` — `_ML_FICHA` (`ia_generadores.py:191-197`)

```
Eres experto en Mercado Libre México. Genera la FICHA TÉCNICA (atributos) del producto para completar la publicación: marca, modelo, color, material, tamaño, contenido del paquete y demás atributos relevantes de su categoría. Devuelve en formato «Atributo: valor», uno por línea; marca con «(sugerido)» lo que estés infiriendo.
```

### 5.3 `mercado_libre` / `descripcion` — `_ML_DESCRIPCION` (`ia_generadores.py:198-203`)

```
Eres experto en Mercado Libre México. Genera una DESCRIPCIÓN en texto plano (sin HTML), clara y persuasiva, con párrafos cortos y viñetas simples con «- ». No incluyas teléfonos, correos, enlaces externos ni datos de contacto (están prohibidos). Enfócate en beneficios, usos y características.
```

### 5.4 `general` / `titulo` — `_GEN_TITULO` (`ia_generadores.py:204-208`)

```
Eres redactor de e-commerce. Genera un título comercial claro y atractivo para la tienda (WooCommerce), con la palabra clave principal al inicio. Devuelve solo el título.
```

### 5.5 `general` / `descripcion` — `_GEN_DESCRIPCION` (`ia_generadores.py:209-214`)

```
Eres redactor de e-commerce. Genera una descripción de producto para WooCommerce en HTML simple (<p>, <ul>, <li>, <strong>): un párrafo de introducción, una lista de características/beneficios y un cierre. Devuelve solo el HTML.
```

### 5.6 `amazon` / `imagenes` — `_AMZ_IMAGENES` (`ia_generadores.py:148-180`)

El único generador de Amazon que sobrevive. **No escribe contenido del
listado**: arma un plan de 5 fotos con sus prompts (`max_tokens: 1800`).

```
Eres un director de arte experto en imágenes de producto para Amazon. A partir de la imagen principal y los datos del producto: 1) DETECTA la categoría, 2) PLANEA un set de 5 imágenes optimizado para esa categoría, 3) GENERA cada imagen con layout, texto exacto y prompt de IA.

PASO 1 — Clasifica en UNA categoría: A) Moda y calzado, B) Electrónicos y gadgets, C) Hogar y cocina, D) Salud/belleza/cuidado personal, E) Mascotas, F) Deportes y fitness, G) Alimentos y bebidas, H) Bebés y maternidad, I) Herramientas y mejoras del hogar, J) Juguetes y juegos.

PASO 2 — Según la categoría, define las 5 imágenes (IMG1 siempre = producto sobre fondo blanco puro #FFFFFF, sin texto, 85% del encuadre; IMG2–IMG5 según la plantilla de esa categoría: lifestyle, beneficios con iconos, medidas/compatibilidad, Q&A frecuentes, etc.).

REGLAS UNIVERSALES (todas salvo IMG1): texto en español, máx 40 palabras por imagen; tipografía sans-serif (máx 2 familias); paleta de marca o blanco/negro/acento; layout distinto en cada imagen; 1:1, mínimo 1000x1000px (ideal 2000x2000); sin marcas de agua ni logos de terceros; callouts con líneas finas.

REQUISITOS TÉCNICOS DE AMAZON (obligatorios para que la imagen se acepte):
• La imagen debe servirse por protocolo HTTP o HTTPS (nunca FTP ni ruta de archivo local).
• Formato: JPEG, TIFF, PNG o GIF NO animado — se prefiere JPEG.
• Color: RGB o CMYK — se prefiere RGB.
• Debe ser clara y sin pixelar, con al menos 72 ppp.
• Entre 1,000 y 10,000 píxeles en el LADO MÁS LARGO (es lo que habilita la funcionalidad de zoom de Amazon).

FORMATO DE ENTREGA por imagen:
[IMG X — NOMBRE]
Descripción del layout visual
Texto exacto que debe aparecer (en español)
Elementos visuales principales
Prompt de generación para IA de imágenes (en inglés, estilo Midjourney/DALL·E)
```

---

## 6. MEJORAR, canal por canal

`ia_generadores.mejorar()` (`ia_generadores.py:336-410`) es un despachador:

```python
if canal == "amazon":  return await amazon_ia.mejorar(producto)      # :345-347
if canal == "tiktok":  return await tiktok_ia.mejorar(producto)      # :351-353
if canal == "temu":    return await temu_ia.mejorar(producto)        # :358-360
cfg = _MEJORAR.get(canal) or _MEJORAR["mercado_libre"]                # :362
```

**Un canal desconocido cae en el prompt de Mercado Libre**, no da error
(`:362`). Es decir: `POST /api/ia/mejorar` con `canal: "walmart"` o
`canal: "shein"` devuelve contenido redactado con las reglas de ML y `canal`
en la respuesta diciendo lo que pediste. Anotado como **rareza real**, no como
bug reportado.

### 6.1 `mercado_libre` — el prompt renderizado

Se construye por concatenación (`ia_generadores.py:287-297`):
`_ML_TITULO.split(".")[0]` + un bloque + `_NO_CONTRADECIR`. **Esto es lo que
recibe el modelo, literal** (verificado renderándolo):

```
Eres experto en publicaciones de Mercado Libre México. Mejora la publicación de Mercado Libre México. Devuelve SOLO JSON válido:
{"titulo": "<máx 60 caracteres, keywords al inicio>", "descripcion": "<texto plano, párrafos cortos, sin datos de contacto>", "atributos": [{"nombre": "..", "valor": ".."}]}
En atributos incluye los NECESARIOS de la categoría (marca, modelo, color, material, tamaño…) y los secundarios que ayuden a la ficha. No inventes datos que no se puedan inferir del producto.
IMPORTANTE: el TÍTULO y la DESCRIPCIÓN actuales definen QUÉ ES el producto. Si la categoría o los atributos recibidos los contradicen (pueden ser residuos de otro producto), IGNÓRALOS por completo y NO cambies el tipo de producto.
```

- `max_tokens`: **1500** (`ia_generadores.py:288`)
- El bloque `_NO_CONTRADECIR` (`ia_generadores.py:279-284`) existe por un caso
  real: **`ACC-0653-CHE-13-16`**, faros de niebla con la categoría y los
  atributos de unos binoculares (producto clonado sin limpiar). La IA
  regeneraba "binoculares" porque leía la categoría.
- **ML no tiene un módulo `ml_contenido.py`** que valide como el de Amazon…
  **pero el límite de 60 caracteres SÍ se aplica, y de la peor manera.**
  Corregido el 3-sep tras una auditoría: este documento decía *"nadie lo
  comprueba después"* y era falso en las dos mitades.
    · **Se avisa**: `services/publicar.py:30` define `ML_TITULO_MAX = 60`
      y `:251-252` mete un aviso en el preview del panel.
    · **Y se CORTA, en silencio**: `vendor/ml_ready/publisher_core.py:363`
      manda `family_name = prod['title'][:60]` cuando la categoría es de
      catálogo — que es el **99.95% de las altas medidas (5,918 de
      5,921)**. Solo el `else` manda el título entero.
  Por qué importa: quien leyera la versión vieja concluía *"pasarme de 60
  no tiene consecuencia"*. La verdadera es *"te lo cortan sin avisar y se
  lleva lo que quedara al final"*. Recórtalo tú, con criterio.
- **ML SÍ tiene tratamiento especial de atributos** (`ia_generadores.py:384-407`):
  si el producto trae `ml_cat_id`, los atributos que propuso la IA **se
  descartan enteros** y se reemplazan por los de `ml_atributos.generar_atributos()`.

### 6.2 `ml_atributos` — el segundo modelo, con su propio cliente

`backend/services/ml_atributos.py`. Es el único sitio del contenido con IA que
**no** pasa por `_completar`. Tiene su propio POST a DeepSeek
(`ml_atributos.py:211-241`) con diferencias que importan:

| | `_completar` (todo lo demás) | `_deepseek_json` (`ml_atributos`) |
|---|---|---|
| `response_format` | ninguno | `{"type": "json_object"}` |
| `temperature` | `0.7` | **`0.2`** |
| `max_tokens` | por generador | **`4096`** |
| Timeout | 90 s | **120 s** |
| Reintento en HTTP 429 | no | sí: backoff `[10, 20, 10]` s |
| Si no hay clave DeepSeek | cae a Claude | cae a `_completar` (que cae a Claude) |

Antes de llamar al modelo consulta `GET https://api.mercadolibre.com/categories/{id}/attributes`
(`ml_atributos.py:89`), descarta los `hidden`/`read_only` y los de `_SKIP_IDS`
(`:36-41`: `BRAND, MODEL, SELLER_SKU, GTIN, EMPTY_GTIN_REASON,
SELLER_PACKAGE_{WEIGHT,LENGTH,WIDTH,HEIGHT}, ORIGIN, OEM`), y separa
**principales** (`required` o `catalog_required`) de **secundarios**, con sus
`valid_values`. Los secundarios se recortan a **15** (`MAX_SECUNDARIAS`,
`ml_atributos.py:31`) y cada lista de valores válidos se muestra hasta 15
opciones (`:123-125`).

Su prompt de usuario, literal (`ml_atributos.py:142-193`; las llaves `{…}` se
rellenan en tiempo de ejecución):

```
Eres un experto en comercio electronico para Mexico (MercadoLibre).
Tu tarea es generar el MAYOR NUMERO POSIBLE de atributos para publicar un producto.
DEBES INTENTAR LLENAR CADA ATRIBUTO. Solo omite si es absolutamente imposible determinarlo.

## Producto
- SKU: {sku o 'N/A'}
- Nombre en tienda: {nombre}
- Titulo de Alibaba (extrae datos de aqui): {alibaba_titulo o 'N/A'}

## Atributos actuales en WooCommerce (base, respeta los correctos)
{atributos_actuales o 'Sin atributos'}

## Caracteristicas de Alibaba (extrae TODOS los datos posibles)
{caracteristicas_clave o 'N/A'}

## ATRIBUTOS OBLIGATORIOS — debes llenarlos TODOS:
  - {ID} ({Nombre}, tipo: {value_type})
    Valores válidos: {v1, v2, …hasta 15} ... ({N} opciones total)

## ATRIBUTOS OPCIONALES — llena TODOS los que puedas, sé proactivo en inferir:
  - {ID} ({Nombre}, tipo: {value_type})
    Valores válidos: …

## REGLAS DE INFERENCIA (aplica en este orden)
1. USA EL ID del atributo como clave JSON (ej: "COLOR" no "Color"; "BATTERY_TYPE" no "Tipo de bateria")
2. BRAND: siempre "Ferrahome" — nunca la del proveedor
3. MODEL: extrae del titulo Alibaba. Si no hay, genera uno corto logico (ej: FH-BT24V, FH-LED50W)
4. Atributos con valores validos: elige el MAS LOGICO para el tipo de producto
5. Texto libre: usa datos de caracteristicas/titulo. Estima con logica si no hay dato exacto
   - Capacidades: "280Ah-314Ah" -> usa "280 Ah"; rangos -> usa el valor minimo
   - Voltaje Mexico: "Multi voltage" o "100-240V" -> usa "120 V"
   - Potencia: si viene en W, mantener con unidad (ej: "200 W")
6. UNITS_PER_PACK / PACKS_NUMBER: si se vende por unidad -> "1"
7. SALE_FORMAT: si es producto individual -> "Unidad"
8. COLOR desde el SKU: NEG=Negro, BLN=Blanco, ROJ=Rojo, AZU=Azul, VER=Verde,
   NAR=Naranja, GRI=Gris, MOR=Morado, AMR=Amarillo, PLA=Plata, ORO=Dorado, MUL=Multicolor,
   HIT=Multicolor, ROS=Rosa, LIL=Lila, CAF=Cafe, BEI=Beige, MET=Plateado
9. ORIGIN: productos Alibaba/China -> "China"
10. IS_WIRELESS, IS_RECHARGEABLE, WITH_LED_LIGHT, etc.: infiere SI/NO del contexto
11. Dimensiones: si vienen en titulo o caracteristicas, extraelas (convierte a cm si es necesario)
12. EXCLUIR SOLO: codigos OEM especificos del proveedor, datos de fabricacion interna, MOQ

## RESTRICCION ABSOLUTA
- Las UNICAS claves permitidas en "atributos" son los IDs listados arriba + "BRAND" y "MODEL"
- PROHIBIDO inventar claves nuevas
- Valores en ESPAÑOL con ortografia EXACTA a los valores validos listados
- Usa flags SOLO para IDs que sea absolutamente imposible determinar

## SALIDA — devuelve SOLO este JSON:
{
  "atributos": {
    "BRAND": "Ferrahome",
    "MODEL": "FH-BT24V",
    "COLOR": "Negro"
  },
  "flags": ["ID_ATRIBUTO: razon por la que no se pudo determinar"]
}
```

Su **system** es una línea (`ml_atributos.py:259`):
```
Eres un experto en e-commerce para Mexico. Respondes siempre con JSON valido.
```

Validación posterior (`ml_atributos.py:271-274`): solo sobreviven claves que
existan en la categoría **+ `BRAND` y `MODEL`**; `BRAND` se **fuerza** a
`Ferrahome` pase lo que pase.

> ⚠️ **Ojo con la marca: no es la misma en todos los canales.**
> `ml_atributos.MARCA = "Ferrahome"` (`ml_atributos.py:30`) y
> `tiktok_atributos.MARCA = "Ferrahome"` (`tiktok_atributos.py:38`), pero
> `amazon_ia.MARCA = "Generic"` (`amazon_ia.py:64`) — y el publicador de Amazon
> también fuerza `brand = "Generic"` (`services/publicar.py:760`), porque
> `Generic` es lo que sostiene la exención de GTIN. Temu **no fuerza marca**:
> `goodsTrademark` va en null (`temu_contenido.py:55-56`).

### 6.3 `amazon` — el circuito completo (`services/amazon_ia.py`)

Es el más elaborado y el que conviene estudiar. Cinco pasos
(`amazon_ia.mejorar`, `:391-498`):

1. **Resolver el `productType`** con la precedencia de la casa —
   **panel > histórico > detección** (`amazon_ia.py:156-209`):
   - `publicar._pt_resuelto(sku, wc_id)` mira la meta `amz_product_type` (la
     elección humana) y luego el histórico de `amazon_progress`;
   - si eso da `None` (producto recién creado), se pregunta a la **Definitions
     API** de Amazon con `publicar._detectar_product_type`. Su respaldo es
     `HOME`, que tiene sus 110 requisitos cargados.
   - **Nunca escribe la meta `amz_product_type`**: eso convertiría una detección
     automática en una decisión humana (`amazon_ia.py:194-196`).

2. **Leer los requisitos REALES** de ese tipo desde `channel.field_requirements`
   (`amazon_ia.py:416-417`). ⚠️ El cruce correcto es
   `listings.product_type ↔ field_requirements.categoria_id`. **`listings.category_id`
   existe y guarda otra cosa: unir por ahí devuelve cero filas SIN dar error**
   (`amazon_ia.py:24-30`).

3. **Llamar al modelo** con `_SISTEMA + _bloque_atributos(reqs, pt)` y
   `max_tokens=3000` (`amazon_ia.py:419-423`).

4. **Validar** con `amazon_contenido.validar` y, si hay problemas, **UNA sola
   ronda de reparación** (`amazon_ia.py:437-442`). *"No dos: si con los
   problemas enfrente no lo arregla, insistir gasta tokens y tiempo para el
   mismo resultado."*

5. **Sustituir marcas registradas**, quitar acentos del título, borrar
   cualquier atributo de marca, clasificar problemas en **fatales** vs
   **avisos**, y **guardar** en `enrich.channel_content`
   (`amazon_ia.py:444-497`).

#### El prompt de Amazon, LITERAL y renderizado (`amazon_ia.py:70-113`)

2,696 caracteres. Es "el spec de Brandon, literal" según el propio archivo.

```
Eres un experto en optimización de listings para Amazon México (amazon.com.mx), con dominio de los lineamientos vigentes en 2026. Escribe TODO en español de México. Respeta ESTRICTAMENTE los límites y cuenta los caracteres antes de entregar. No inventes datos que no se puedan inferir del producto.

Devuelve SOLO JSON válido, sin markdown ni texto alrededor:
{"titulo": "…", "highlights": "…", "bullets": ["…","…","…","…","…"], "descripcion": "…", "backend_search_terms": "…", "atributos": [{"nombre": "…", "valor": "…"}]}

TÍTULO — máximo 75 caracteres. Mayúscula en cada sustantivo importante. Formato: tipo de producto + característica + material/tamaño. Prohibidos: los signos ! $ * ~, las palabras promocionales (oferta, gratis, mejor, descuento, garantizado, 100%) y los emojis.

ITEM HIGHLIGHTS — máximo 125 caracteres. Es el segundo campo indexable: palabras clave SECUNDARIAS. Materiales, casos de uso, público objetivo o ventaja competitiva. Frase natural, no una lista. No repitas el título.

BULLET POINTS — exactamente 5, cada uno entre 150 y 200 caracteres. En este orden: (1) beneficio principal, no una característica; (2) material, durabilidad o construcción; (3) compatibilidad o casos de uso; (4) facilidad de uso, instalación o mantenimiento; (5) garantía o propuesta diferencial. Oraciones completas, mayúscula inicial, sin emojis y sin listas de keywords.

DESCRIPCIÓN — máximo 2000 caracteres, en PÁRRAFOS (no listas): (1) propuesta de valor y contexto de uso; (2) características técnicas con su beneficio; (3) casos de uso y compatibilidades; cierre con una llamada a la acción natural. Incorpora long-tail con naturalidad.

BACKEND SEARCH TERMS — máximo 249 BYTES en UTF-8, que NO es lo mismo que 249 caracteres: cada acento y cada ñ pesan 2 bytes. ESCRÍBELOS SIN ACENTOS para no desperdiciar espacio (Amazon los normaliza igual). Palabras separadas por espacios, SIN comas ni puntuación. Sinónimos, variaciones regionales y errores comunes de escritura. NO repitas ninguna palabra que ya esté en el título ni en los highlights: ahí se desperdicia el campo. Un byte de más y Amazon ignora el campo ENTERO, sin avisar.

MARCA — la marca es siempre «Generic». No propongas ninguna otra y no menciones marcas registradas de terceros en ningún campo (ni como comparación de estilo: «tipo Pandora», «calidad Bose»). Solo se admite nombrar una marca ajena si el producto es realmente compatible con ella, y entonces escribe «compatible con …».

IMPORTANTE: el TÍTULO y la DESCRIPCIÓN actuales definen QUÉ ES el producto. Si la categoría o los atributos recibidos los contradicen (pueden ser residuos de otro producto), IGNÓRALOS por completo y NO cambies el tipo de producto.
```

#### El bloque de atributos que se le PEGA al final (`amazon_ia.py:116-150`)

No sale de la imaginación del modelo: sale de la base. Se piden **solo los
obligatorios que nadie más llena** — se excluyen los que ya tienen
`default_value` (los pone el publicador: `brand`, `country_of_origin`,
`supplier_declared_dg_hz_regulation`…) y los canónicos de texto
(`titulo`, `descripcion`, `bullets`, `highlights`, `brand`). Tres variantes:

**A) Sin `productType` resuelto:**
```

ATRIBUTOS — no se pudo determinar el tipo de producto de Amazon. Propón los atributos técnicos evidentes del producto (material, color, tamaño, cantidad) y nada más.
```

**B) El tipo no exige atributos extra:**
```

ATRIBUTOS — el tipo «{pt}» no exige atributos extra: los obligatorios los cubren el título, la descripción, los bullets y los valores por omisión del publicador. Aun así, propón los atributos técnicos evidentes (material, color, tamaño).
```

**C) El caso normal:**
```

ATRIBUTOS — el tipo de producto de Amazon es «{pt}» y estos son sus campos OBLIGATORIOS que hoy no tiene nadie quien los llene. Devuélvelos en `atributos` usando EXACTAMENTE ese nombre en `nombre`:
  · {campo_1}
  · {campo_2}
  · …
Si un valor no se puede inferir del producto, NO lo inventes: omite esa entrada. Puedes añadir después otros atributos técnicos evidentes (material, color, tamaño, cantidad) con nombre libre.
```

#### El mensaje de reparación (`amazon_ia.py:542-551`)

Se manda con el MISMO `system`, en la segunda y última llamada:

```
{user original}

Tu respuesta anterior fue:
{el JSON que devolvió, tal cual}

El validador la rechazó por esto:
  · {problema 1}
  · {problema 2}

Corrige ÚNICAMENTE esos campos, deja los demás idénticos y devuelve otra vez el JSON completo. Cuenta los caracteres uno por uno antes de responder; para los términos de búsqueda cuenta BYTES (los acentos pesan 2, escríbelos sin acentos).
```

#### La forma de la respuesta de Amazon (`amazon_ia.py:478-489`)

```jsonc
{
  "ok": true, "canal": "amazon",
  "proveedor": "deepseek", "modelo": "deepseek-chat",
  "campos": { "titulo": "...", "highlights": "...", "bullets": [...],
              "descripcion": "...", "backend_search_terms": "...",
              "atributos": [{"nombre": "...", "valor": "..."}] },
  "rechazados": [{"campo": "titulo", "motivo": "titulo: 82 caracteres, máximo 75"}],
  "avisos":     ["bullet 3: 148 caracteres, rango 150-200"],
  "problemas":  ["... la lista cruda del validador, sin clasificar ..."],
  "terminos_detectados": ["Se detectó 'Bose' en la descripción — reemplazado por 'audio de alta fidelidad'"],
  "product_type": "HOME", "product_type_origen": "deteccion",
  "requisitos": {"estado": "incompleto", "product_type": "HOME",
                 "obligatorios": 110, "cubiertos": [...], "sin_cubrir": [...]},
  "guardado": {"ok": true, "sku": "...", "canal": "amazon", "campos": [...]}
}
```

**`campos` es lo único publicable.** Un campo que salió en `rechazados` **no
aparece en `campos`** (`amazon_ia.py:471-476`). Las únicas llaves que viajan son
las canónicas (`_LLAVES`, `amazon_ia.py:354-355`): `titulo`, `highlights`,
`bullets`, `descripcion`, `backend_search_terms`, `atributos`. Lo que el modelo
invente de más (`seo_title`, `keywords`…) se descarta.

**`requisitos.estado`** tiene **tres** valores, y no son dos
(`amazon_ia.py:561-588`): `ok`, `incompleto` y `sin_requisitos`. *"sin
requisitos leídos NO es lo mismo que 'no le falta nada'."*

#### Las dos correcciones deterministas que sí se aplican

1. **Título sin acentos** (`amazon_ia.py:450-451` → `ia_generadores._sin_acentos`,
   `:120-125`). Normaliza a NFKD y borra los combinantes, o sea que **`ñ` se
   convierte en `n`**. Solo afecta al TÍTULO; el resto conserva su ortografía.
2. **Fuera cualquier atributo de marca** (`amazon_ia.py:455-457`): se filtran
   los atributos cuyo `nombre` sea `brand`, `marca`, `manufacturer` o
   `fabricante`.

### 6.4 `tiktok` — `services/tiktok_ia.py`

Mismo contrato que Amazon, con tres diferencias que **no son de estilo**
(`tiktok_ia.py:13-24`):

- **Amazon trunca; TikTok acepta el borrador y rebota al vender.**
  `save_mode=AS_DRAFT` casi no valida; `LISTING` valida todo. Medido el
  12-ago-2026: de 970 productos publicados, **la mitad traía algo que solo
  salió a la luz al pasarlos a la venta** (`tiktok_contenido.py:14-19`).
  Por eso se valida contra lo que exige LISTING.
- **El título de MX admite 300 caracteres**, no 255 (`tiktok_contenido.py:22-25, 33`).
- **TikTok exige ID de atributo Y ID de valor**; el nombre no sirve.

La categoría sale de `channel.listings.category_id` — **en Amazon vive en
`product_type`; cruzar por la columna equivocada devuelve cero filas sin dar
error** (`tiktok_ia.py:109-113`).

#### Prompt de TikTok, literal (`tiktok_ia.py:56-83`) — `max_tokens=2000`

```
Eres redactor de fichas de producto para TikTok Shop México.

Mejoras el título y la descripción de un producto del catálogo. No inventas: sólo reescribes con lo que te doy.

REGLAS
1. NO INVENTES DATOS. Si no sabes el material, los watts, la capacidad o las medidas, no los menciones. Un dato inventado se publica sin dar error y nadie se entera hasta que un cliente reclama.
2. El TÍTULO manda: es lo que busca la gente. Empieza por QUÉ ES el producto, después su rasgo distintivo. Hasta 300 caracteres — úsalos, pero sin rellenar con palabras vacías. Sin emojis, sin MAYÚSCULAS sostenidas, sin signos de admiración.
3. Escribe como busca un comprador mexicano, no como habla un catálogo chino. «Hervidor eléctrico» y no «Kettle 1.7L Multifuncional Home Appliance».
4. NADA de promesas que no controlamos: envío gratis, entrega en X días, garantía de por vida, «el mejor», «#1», precios.
5. La DESCRIPCIÓN en HTML simple: <p>, <ul>, <li>, <strong>. Nada de <img>, <script>, <table> ni estilos.
6. 3 a 6 puntos clave, cada uno un beneficio concreto, no un adjetivo.

SALIDA — sólo JSON:
{"titulo": "<máx 300 caracteres>", "descripcion_html": "<HTML simple>", "puntos_clave": ["<punto 1>", "…"], "palabras_clave": ["<lo que teclearía un comprador>"], "confianza": 0.0, "flags": ["<lo que NO pudiste confirmar del producto>"]}
```

Su mensaje de usuario **NO** es `_contexto()`; es propio (`tiktok_ia.py:86-96`):

```
PRODUCTO
  SKU:          {sku o '(sin sku)'}
  Título hoy:   {nombre}
  Descripción:  {descripción sin HTML, 1200 caracteres}
  Categoría:    {ruta o '(sin categoría de TikTok)'}
  Atributos confirmados: {n: v; n: v}  o  '(ninguno)'

Mejora el contenido y devuelve SOLO el JSON indicado.
```

**Traducción de llaves en el borde** (`tiktok_ia.py:47-50`): el prompt pide
`descripcion_html` y `puntos_clave`; el documento guardado usa `descripcion` y
`bullets`, que es como se llaman en todos los canales.

### 6.5 `temu` — tres llamadas, a veces cuatro (`services/temu_ia.py`)

No es diseño: es el orden que impone Temu (`temu_ia.py:9-30`).

1. **La categoría primero** — determina qué atributos existen. `template.get`
   solo responde en hojas.
2. **El contenido** (título, descripción, bullets), con la categoría ya sabida.
3. **Los atributos, en DOS vueltas**, porque la cascada es circular: qué
   condicionales se activan depende de lo contestado en los duros. *"Meter los
   20 atributos de golpe hace que el modelo llene voltajes de productos sin
   electricidad — que es justo lo que manda productos a Borrador."* La segunda
   vuelta **solo ocurre si algo se destrabó**: medido el 13-ago, **de 89
   productos, solo 13 la necesitaron**.

**El `system` de Temu es una sola línea** (`temu_ia.py:59`), todo va en el user:
```
Devuelve SOLO JSON válido, sin texto alrededor.
```

#### Prompt de CONTENIDO de Temu (`temu_contenido.py:214-275`) — `max_tokens=2000`

```
Eres un especialista en listings para TEMU México. Escribes en español de México.

Tu tarea: reescribir el título y la descripción de este producto para que se
entiendan solos y aparezcan en las búsquedas de Temu.

## EL PRODUCTO
- SKU: {sku}
- Título actual (de WooCommerce): {titulo_woo}
- Descripción actual: {descripcion_woo, 1200 caracteres}
- Categoría de Temu: {categoria_ruta}
- Atributos que ya tiene en Woo: {json}
- Color por el sufijo del SKU ({sufijo}): {color}      ← solo si el sufijo está en la tabla

## CÓMO SE COMPRA EN TEMU
El comprador llega por búsqueda y decide con la foto y el título. No hay marca
que lo respalde: el título tiene que decir QUÉ ES, PARA QUÉ SIRVE y su rasgo
distintivo (material, medida, cantidad de piezas, compatibilidad).

## REGLAS DEL TÍTULO
1. Entre 60 y 120 caracteres. El techo duro son 500, pero un título
   larguísimo no lo lee nadie.
2. Empieza por el sustantivo del producto, no por un adjetivo ni por la marca.
3. Incluye el dato que el comprador usa para filtrar: medida, capacidad, número
   de piezas, material o compatibilidad. Si el título viejo lo trae, consérvalo.
4. **Nada de PALABRAS EN MAYÚSCULA SOSTENIDA**, ni emojis, ni signos como ! $ * ~.
5. **Prohibido lo promocional**: "oferta", "gratis", "el mejor", "100%",
   "envío gratis", "descuento". Temu tiene un detector propio y tumba el listing.
6. No inventes atributos que no estén en el título, la descripción o los datos
   de Woo. Si no sabes el material, no lo pongas.
7. Sin nombre de marca ajena. Si el título viejo trae una, quítala.
8. **Cuidado con las marcas que parecen palabras comunes.** Temu las detecta y
   marca el listing por posible infracción. Usa el término genérico:
   velcro → "cierre de gancho y bucle" o "cierre ajustable" · curita → "vendaje
   adhesivo" · kleenex → "pañuelo desechable" · diurex → "cinta adhesiva" ·
   tupper/tupperware → "recipiente hermético" · maicena → "fécula de maíz" ·
   jacuzzi → "bañera de hidromasaje" · vaselina → "gelatina de petróleo" ·
   ziploc → "bolsa con cierre hermético" · post-it → "nota adhesiva" ·
   frisbee → "disco volador" · thermos → "termo". Medido el 14-ago: "velcro"
   en el título dispara "Potentially Infringing Terms"; quitándolo pasa.

## REGLAS DE LA DESCRIPCIÓN
1. Máximo 2000 caracteres, en párrafos cortos, texto plano (sin HTML).
2. Di qué es, para qué sirve, de qué está hecho y qué incluye la caja.
3. Nada de promesas de envío, precio, garantía ni devoluciones: eso lo pone Temu
   y ponerlo tú puede tumbar el listing.

## BULLETS
5 frases de máximo 200 caracteres, cada una un beneficio concreto y
verificable del producto. Empiezan con mayúscula. Sin emojis.

## SALIDA — SOLO este JSON, sin texto alrededor

{
  "titulo": "<título nuevo>",
  "descripcion": "<descripción nueva>",
  "bullets": ["<bullet 1>", "…"],
  "flags": ["<qué dato te faltó y tuviste que omitir>"],
  "confianza": 0.0
}

`confianza` es tu estimación honesta de 0 a 1. Un 0.5 sincero vale más que un
0.9 inflado: el panel usa ese número para decidir qué se revisa a mano.
```

#### Prompt de ATRIBUTOS de Temu (`temu_contenido.py:298-342`) — `max_tokens=1800`

La cabecera cambia entre la primera y la segunda vuelta:

- 1ª vuelta: `Estos atributos son obligatorios SIEMPRE en esta categoría.`
- 2ª vuelta: `SEGUNDA VUELTA. Con los valores que ya elegiste se DESTRABARON estos atributos, que ahora son obligatorios.`

```
Eres un catalogador de producto para TEMU México.

## EL PRODUCTO
- SKU: {sku}
- Título: {titulo}
- Descripción: {descripcion, 900 caracteres}
- Categoría de Temu: {categoria_ruta}
- Atributos que ya tiene en Woo: {json}
- Color por el sufijo del SKU ({sufijo}): {color}

## {cabecera}
OBLIGATORIOS — llénalos TODOS:
  - pid={pid} · "{nombre}" (elige máx. {n})
      valores: {vid}={valor} | {vid}={valor} | …  …y N más
      NUMÉRICO: escribe un número (rango {min}–{max})  unidades: {…}

OPCIONALES — llena los que puedas inferir con seguridad:   ← solo en la 1ª vuelta, hasta 12
  …

## REGLAS DURAS

1. **Devuelve el `pid` del atributo y el `vid` del valor.** Los dos salen de las
   listas de arriba. **Está PROHIBIDO inventar un vid**: un valor inventado no
   da error, se publica mal y nadie se entera.

2. **Los NUMÉRICOS** (los que dicen `NUMÉRICO`) llevan `vid: null` y el número
   en `numero`, respetando el rango. Si no lo sabes con certeza, déjalo fuera.

3. **Di la verdad sobre la energía.** Si el producto usa pilas, dilo; si se
   enchufa, dilo. Contestar "sin electricidad" para ahorrarte preguntas mete un
   dato falso al catálogo. Eso sí: elige el valor MÁS PRECISO que aplique — por
   ejemplo, si las pilas no son recargables, "Batería no recargable" es la
   respuesta correcta y además evita que se pidan datos que no existen.

4. **No fuerces.** Si un atributo no se deduce del título, la descripción o los
   datos de Woo, déjalo fuera y anótalo en `flags`. Es mejor un producto con 3
   atributos ciertos que con 8 inventados.

5. Respeta el máximo de valores que indica cada atributo.

## SALIDA — SOLO este JSON, sin texto alrededor

{
  "atributos": [
    {"pid": <pid>, "vid": <vid o null si es numérico>,
      "numero": "<solo para numéricos>", "razon": "<breve>"}
  ],
  "flags": ["<pid o nombre>: por qué no se pudo determinar"],
  "confianza": 0.0
}
```

**Por qué el validador de atributos es la garantía y no el prompt**
(`temu_contenido.py:31-38`): *"En la prueba de 89 productos el modelo **inventó
10 `vid`** pese a que el prompt lo prohíbe explícitamente; el validador los
rechazó."*

**El chequeo que evita el Borrador**: `faltantes()` (`temu_contenido.py:451-464`)
lista los obligatorios (duros + condicionales activados) que quedaron sin
llenar. Si no viene vacío, `temu_ia` lo devuelve como aviso literal
(`temu_ia.py:223-225`):

```
OBLIGATORIOS SIN LLENAR: {…}. Publicar así deja que Temu autocomplete y el producto cae en Borrador.
```

Medido el 13-ago con la primera tanda: de 6 productos, **los 2 eléctricos
cayeron en Borrador y los 4 no-eléctricos no** (`temu_contenido.py:27-33`).

### 6.6 `general` — WooCommerce (`ia_generadores.py:304-312`)

`max_tokens=1200`. Prompt literal:

```
Eres redactor de e-commerce (WooCommerce). Devuelve SOLO JSON válido:
{"titulo": "<título comercial claro>", "descripcion": "<HTML simple: <p>, <ul>, <li>, <strong>>", "atributos": [{"nombre": "..", "valor": ".."}]}
```

Sin validador, sin atributos reales, sin persistencia por canal (el Estudio
trata `general` aparte: *"General vive en WooCommerce"*,
`ProductStudio.tsx:660`).

---

## 7. LOS LÍMITES — y la trampa de los BYTES

### 7.1 Tabla completa

| Canal | Campo | Límite | Unidad | Dónde está escrito |
|---|---|---|---|---|
| Amazon | `titulo` | **75** | caracteres | `amazon_contenido.py:34` |
| Amazon | `highlights` | **125** | caracteres | `amazon_contenido.py:35` |
| Amazon | `bullets` (cada uno) | **150–200** | caracteres | `amazon_contenido.py:36` |
| Amazon | `bullets` (cuántos) | **exactamente 5** | — | `amazon_contenido.py:40` |
| Amazon | `descripcion` | **2000** | caracteres | `amazon_contenido.py:37` |
| Amazon | **`backend_search_terms`** | **249** | ⚠️ **BYTES UTF-8** | `amazon_contenido.py:38` |
| TikTok | `titulo` | **300** (MX y BR; el resto 255) | caracteres | `tiktok_contenido.py:33` |
| TikTok | `descripcion` (HTML) | **10 000** | caracteres | `tiktok_contenido.py:34` |
| TikTok | `puntos_clave` | **3–6** | cuántos | `tiktok_contenido.py:37` |
| Temu | `titulo` | **500** techo, **60–120** ideal | caracteres | `temu_contenido.py:76-77` |
| Temu | `descripcion` | **2000** | caracteres | `temu_contenido.py:78` ⚠️ ver nota |
| Temu | `bullets` | **5**, máx **200** c/u | caracteres | `temu_contenido.py:79-80` ⚠️ |
| Mercado Libre | `titulo` | **60** | caracteres | `ia_generadores.py:186` (**solo en el prompt: nadie lo valida**) |

> **Temu, honestidad del código** (`temu_contenido.py:82-83`): *"NO VERIFICADO:
> el máximo real de `goodsDesc` y de `bulletPoints`. Nadie los ha medido contra
> la API; 2000 y 200 son topes conservadores nuestros, no de Temu."*

> **Amazon, matiz por categoría** (`amazon_contenido.py:31-33`): el título son
> 75 **salvo en categorías con guía propia (joyería, apparel), donde puede ser
> MENOR**. Ese requisito por categoría no está tabulado todavía; 75 es el techo
> genérico.

### 7.2 ⚠️ La trampa de los BYTES

**Es la más cara del contenido de Amazon.** Lo dice el propio archivo
(`amazon_contenido.py:19-22`):

> *"⚠️ EL LÍMITE DE `backend_search_terms` ES EN BYTES, NO EN CARACTERES. Con
> acentos y ñ, UTF-8 gasta 2 bytes por letra: un texto de 240 caracteres con 15
> acentos son 255 bytes. **Un byte de más y Amazon ignora TODO el campo, sin
> avisar.** Es la trampa más cara de este archivo."*

**Qué pasa cuando te pasas:** Amazon no rebota. **Descarta el campo entero**,
en silencio. Pierdes los ~249 bytes de palabras clave de golpe y el listado se
publica correctamente — no hay error que investigar.

**Por qué duele el doble:** `backend_search_terms` es invisible para el
comprador. Nadie va a mirar el listado y notar que faltan.

**Medido aquí mismo (2026-09-03), replicando las funciones exactas del archivo:**

| Texto | `len()` | `bytes_utf8()` |
|---|---|---|
| `organizador de cocina` | 21 | 21 |
| `organizador de cocina para niños con diseño ergonómico y máxima capacidad` | 73 | **77** |
| 237 caracteres sin acentos | 237 | 237 |
| **los mismos 237 con 6 acentos** | 237 | **243** |

Seis acentos = seis bytes. Con 15 acentos, 249 caracteres son 264 bytes: el
campo se pierde entero aunque el contador de caracteres diga que cabe.

**Las tres defensas que hay en producción:**

1. **El prompt lo pide.** `_SISTEMA` dice *"ESCRÍBELOS SIN ACENTOS para no
   desperdiciar espacio (Amazon los normaliza igual)"* (`amazon_ia.py:97-103`).
2. **El validador lo mide en bytes de verdad** (`amazon_contenido.py:136-142`):
   ```python
   def bytes_utf8(texto: str) -> int:
       """Lo que Amazon cuenta en `backend_search_terms`. NO uses len()."""
       return len((texto or "").encode("utf-8"))
   ```
   Y el mensaje de error dice las dos cifras a propósito:
   `"backend_search_terms: 264 BYTES (249 caracteres), máximo 249. Amazon ignoraría el campo ENTERO"`.
3. **El publicador recorta como última red**, porque a ese punto puede llegar
   texto **tecleado a mano en el panel** (`services/publicar.py:764-769`,
   `services/publicar_ready.py:676-684`):
   ```python
   def cabe_en_bytes(texto: str, tope: int = SEARCH_TERMS_MAX_BYTES) -> str:
       """Recorta a `tope` BYTES sin partir un carácter a la mitad."""
       b = (texto or "").encode("utf-8")
       if len(b) <= tope:
           return texto or ""
       return b[:tope].decode("utf-8", errors="ignore").rstrip()
   ```
   Medido: una cadena de 399 caracteres / 479 bytes sale con 207 caracteres /
   **248 bytes** — no 249, porque el corte cae en medio de una `ñ` y el
   `errors="ignore"` la descarta entera en vez de dejar un byte inválido.

**En Amazon el campo se llama `generic_keyword`.** El publicador traduce
`backend_search_terms` → `generic_keyword` (`publicar.py:769` + `:778`,
`publicar_ready.py:681-684`). Verificado contra los 553 esquemas cargados:
`generic_keyword` existe en **551**; los dos que no lo tienen son `ABIS_BOOK` y
`MAPS` (`publicar_ready.py:670-673`).

### 7.3 Otro sitio donde bytes ≠ caracteres, aunque no se llame así

`amazon_ia.hash_base()` (`amazon_ia.py:321-341`) usa **sha1** a propósito: *"40
caracteres — el ancho exacto de la columna"*. `temu_ia._hash_base()`
(`temu_ia.py:106-110`) usa sha256 recortado a 32. No confundir: son huellas de
la base con la que se generó el contenido, para saber después si el producto
cambió en Woo. **Nadie las compara automáticamente hoy** (`amazon_ia.py:328-330`).

---

## 8. Los validadores, campo por campo

### 8.1 Amazon — `amazon_contenido.validar()` (`amazon_contenido.py:87-152`)

**NO corrige. Reporta.** El comentario lo explica (`:92-93`): *"Truncar un
título en silencio sería repetir el pecado de Amazon dentro de nuestra propia
casa — el que lo escribió tiene que enterarse de que no cupo."*

Lista de comprobaciones:

| Campo | Qué comprueba |
|---|---|
| `titulo` | vacío · >75 caracteres · signos `! $ * ~ ^ ¡` · emojis · palabras promocionales |
| `highlights` | >125 caracteres · palabras promocionales |
| `bullets` | menos de 5 · cada uno fuera de 150–200 · no empieza con mayúscula · emojis |
| `descripcion` | >2000 caracteres |
| `backend_search_terms` | >249 **bytes** · lleva comas · repite palabras del título/highlights |

Las palabras promocionales (`_PROMO`, `amazon_contenido.py:46-51`):
`oferta, ofertas, gratis, mejor, barato, descuento, promocion, promoción,
garantizado, envio gratis, envío gratis, 100%, el mas vendido, el más vendido,
numero 1, número 1, liquidacion, liquidación, rebaja, ahorra, regalo`.

> **Rareza verificada por medición (2026-09-03):** en `_promocionales()`
> (`amazon_contenido.py:78-84`) el patrón de búsqueda usa `p` **con acentos**
> mientras el texto ya viene sin ellos, así que las cinco entradas acentuadas
> (`promoción`, `liquidación`, `envío gratis`, `el más vendido`, `número 1`)
> **nunca pueden coincidir**. **No es un agujero**: las cinco tienen gemela sin
> acento en la misma lista, así que la detección funciona igual — verificado:
> `"Set de Ollas en Promoción Especial"` → detecta `promocion`. Es código
> muerto, no una falla.

#### La lista de palabras vacías — y por qué existe (`amazon_contenido.py:59-68`)

```python
_VACIAS = {"a","al","ante","con","contra","de","del","desde","e","el","en",
           "entre","hasta","la","las","lo","los","mas","o","para","por",
           "que","se","sin","sobre","su","sus","tras","un","una","unas",
           "uno","unos","y","cm","mm","ml","kg","g","v","w"}
```

> *"Sin esta lista, la comprobación de 'no repitas el título' saltaba por un
> «de» y tiraba los 245 bytes de términos completos. Medido en la primera
> corrida en vivo (13-ago): **2 de 3 SKUs perdieron el campo entero, uno por
> «de»**."*

#### FATAL vs AVISO — la clasificación que salva campos (`amazon_ia.py:366-385`)

No todos los problemas cuestan lo mismo. El criterio es **qué hace AMAZON con
el campo**:

- **FATAL** → Amazon lo trunca, lo ignora entero o puede suprimir el listado
  (título fuera de límite o con signos/promos, highlights o descripción
  pasados, términos de búsqueda pasados de bytes, emojis).
  **NO SE MANDA**: vuelve como `rechazado` y lo escribe una persona.
- **AVISO** → el contenido es publicable pero se aparta del estilo pedido
  (un bullet de 148 en vez de 150, menos de 5 bullets, repetir palabras).
  **SE MANDA** y se reporta en el panel.

La regla técnica es una lista de cuatro subcadenas (`amazon_ia.py:381-385`):

```python
_AVISOS = ("repite ", "rango ", "no empieza con mayúscula", "se esperan ")

def _es_fatal(problema: str) -> bool:
    return not any(a in problema for a in _AVISOS)
```

O sea: **todo es fatal por omisión** salvo esas cuatro formas.

### 8.2 TikTok — `tiktok_contenido.validar()` (`tiktok_contenido.py:77-158`)

Diferencia de fondo con Amazon: **sí limpia el HTML solo** (*"una etiqueta
prohibida es un error mecánico y no una decisión de redacción"*, `:82-84`).
Todo lo demás se reporta.

Etiquetas permitidas (`tiktok_contenido.py:46`):
`p, br, ul, ol, li, strong, b, em, i`. **`<table>` se prohíbe a propósito**:
*"TikTok LO CONVIERTE EN IMAGEN, así que el texto deja de ser texto y no hay
forma de editarlo después"* (`:43-46`).

**LA COMPROBACIÓN QUE IMPORTA** — no es de formato, es de **sentido**
(`tiktok_contenido.py:106-118`):

```python
if original and titulo:
    base = _palabras(original.get("titulo") or "")
    if base and not (base & _palabras(titulo)):
        problemas.append(
            "titulo: no comparte ninguna palabra con el original — "
            "puede estar describiendo otro producto")
```

> *"Caso real del 12-ago: el recomendador mandó un 'Collar de recuperación para
> gato' (cono veterinario) a la categoría de joyería de disfraces — con toda
> confianza y sin dar error. Si la propuesta no conserva NADA del sustantivo
> original, se descarta: es más barato quedarse con el título feo que vender
> otra cosa."*

Compara palabras de **≥4 letras**, sin acentos (`tiktok_contenido.py:73-74`).

Y: **si la IA marcó `flags`, eso se convierte en problema**
(`tiktok_contenido.py:154-157`). *"Un dato inventado NO da error: se publica y
nadie sabe cuál era mentira."* En atributos pasa lo mismo
(`tiktok_ia.py:293-294`): cada flag se anota como rechazo `sin confirmar → …`.

También hay un validador aparte de lo que **no** es contenido y sin embargo
hace rebotar un LISTING (`tiktok_contenido.validar_publicable`, `:161-194`):
stock fuera de `[1, 99999]` (**el 0 no es válido en TikTok**), peso `0`,
`L+A+H > 160 cm`, imágenes fuera de `1–9`.

### 8.3 Temu — `temu_contenido.validar_contenido()` (`temu_contenido.py:348-383`)

Comprueba título (vacío, >500, emojis, promocionales, **≥3 palabras en
mayúscula sostenida**), descripción (>2000, **"parece traer HTML"**,
promocionales) y bullets (>200, emojis).

Nota que vale la pena copiar (`temu_contenido.py:95-99`): **`"regalo"` NO está
en la lista de Temu a propósito** — *"'regalo' y 'regalar' son descripción
legítima de producto, no reclamo promocional — Temu tiene un atributo que se
llama literalmente 'Ocasión para regalar'. Meterlo marcaba en rojo la mitad de
las descripciones de joyería sin que hubiera nada que corregir"*. (En Amazon
sí está: `amazon_contenido.py:50`.)

Y el filtro barato vs el caro (`temu_contenido.py:86-88`): Temu tiene su propio
detector, `temu.local.goods.illegal.vocabulary.check`, *"probado y devuelve
PASS/FAIL"*. La lista local es la primera pasada; la autoritativa es la API.

---

## 9. Marcas registradas — `services/terminos_protegidos.py`

Corre **después** de la IA y **antes** de quitarle los acentos al título
(`amazon_ia.py:444-446`; TikTok en `tiktok_ia.py:188-192`).

**Por qué no se le deja a la IA** (`terminos_protegidos.py:4-11`):

> *"El prompt ya le dice a la IA que no use marcas de terceros. Eso no basta: un
> modelo que redacta 'acabado tipo Pandora' o 'sonido estilo Bose' no está
> desobedeciendo, está describiendo — y Amazon no contesta con un error, retira
> el listado."*

Hace **dos** cosas y **una la evita a propósito**:

1. **Sustituye** por un genérico en español (`pandora` → `charms estilo
   europeo`, `bose` → `audio de alta fidelidad`, `velcro` → `cierre de gancho y
   bucle`…). La lista es finita y auditable: ~110 entradas
   (`terminos_protegidos.py:43-142`), agrupadas en joyería/moda, personajes y
   juguetes, electrónica y audio, herramientas, hogar y cocina, marcas que se
   volvieron nombre común, y automotriz.
2. **Reporta** cada sustitución con texto fijo:
   `"Se detectó 'Pandora' en la descripción — reemplazado por 'charms estilo europeo'"`.
3. **NO sustituye dentro de una frase de compatibilidad.** La guarda es una
   regex cerrada de 6 formas (`terminos_protegidos.py:150-154`):
   `compatible con/para`, `apto/s para`, `para uso con`, `funciona con`,
   `diseñado para`, `reemplazo de/para`. Ahí la marca es información legítima:
   *"cambiarla convierte una funda de iPhone en una funda de 'smartphone 15':
   corrupción silenciosa del contenido"*. Se REPORTA, no se toca.

**Fuera de la lista a propósito** (`terminos_protegidos.py:67-70`): `puma` (es
un animal → estampados), `vans` (tipo de vehículo) y `honda` (adjetivo español:
"olla honda"). *"El riesgo de un falso positivo es mayor que el de la mención."*

**Nuestras, nunca se tocan** (`:145`): `ferrahome`, `generic`, `kubera`.

Cubre título, highlights, descripción, términos de búsqueda, **cada bullet por
separado** (para poder decir en cuál) y **el VALOR de cada atributo** — *"que es
donde la IA mete la marca cuando el prompt le prohíbe ponerla en el título"*
(`terminos_protegidos.py:213-250`).

---

## 10. Los dos endpoints de COMPATIBILIDAD

Existen, responden y **no comparten nada** con lo anterior: viven en
`services/claude.py`, que llama a Anthropic **directo, sin pasar por
`_completar`** y **sin fallback a DeepSeek**. Si `ANTHROPIC_API_KEY` no está
configurada, devuelven `{"ok": False, "titulo": <el nombre original>, "motivo":
"ANTHROPIC_API_KEY no configurada"}` (`claude.py:52-53`).

### `GET /api/ia/prompts` → `claude.PROMPTS_CANAL` (`claude.py:24-37`)

Devuelve **exactamente** este diccionario. **Estos tres textos NO son los que
usa el panel hoy** — son de la primera versión y quedaron ahí:

```json
{
  "mercado_libre": "Eres experto en publicaciones de Mercado Libre México. Genera un título de máximo 60 caracteres, atractivo y con palabras clave de búsqueda.",
  "amazon": "Eres experto en listings de Amazon. Genera un título y 5 bullet points siguiendo las guías de estilo de Amazon.",
  "tiktok": "Eres experto en TikTok Shop. Genera un título corto y llamativo, orientado a contenido viral."
}
```

> ⚠️ **Si alguien busca "el catálogo de prompts" y encuentra este endpoint, se
> lleva los tres equivocados.** El catálogo vivo de `generar` es
> `GET /api/ia/generadores` (y ni siquiera expone el `system`, §4). Los prompts
> vivos de `mejorar` son `amazon_ia._SISTEMA`, `tiktok_ia._SISTEMA`,
> `temu_contenido.build_prompt_*` y `ia_generadores._MEJORAR` — todos copiados
> literales arriba.

### `POST /api/ia/titulo` (`claude.py:49-71`)

`max_tokens=300`, modelo `claude-opus-4-8` (`claude.py:21`). Mensaje de usuario:

```
Producto: {nombre}
Contexto: {contexto}
Devuelve solo el título optimizado.
```

Un canal desconocido cae a `mercado_libre` (`claude.py:55`).

---

## 11. `POST /api/ia/precio-competencia` (`services/competencia.py`)

**Solo para mostrar. No cambia ningún campo.** Dos modos.

### Modo A (`con_lista: true`, el default)

1. Arma una **query genérica** que quita la marca propia
   (`competencia._query`, `:71-82`): *"NO usa la marca/modelo propios, que son
   únicos de Kubera y no los tiene ningún competidor"*. Toma las primeras 8
   palabras de más de 2 letras.
2. Busca en **SerpApi** `google_shopping`, `gl=mx`, `hl=es`, hasta 25
   resultados (`:105-127`, `:153`). Sin `SERPAPI_KEY` devuelve lista vacía.
3. **La API pública de ML ya NO se usa**: hay una función `_buscar_ml` (`:85-102`)
   pero está desconectada — *"dejó de permitir búsqueda anónima (403)"*
   (`:151-153`).
4. Si hay fuentes con precio, usa `_PROMPT_CON_LISTA`; si no, cae al modo B.

`max_tokens=1300` (`competencia.py:179`). Prompt literal (`:35-52`):

```
Eres analista de precios de e-commerce en México. Te doy un PRODUCTO y una lista de PUBLICACIONES DE COMPETENCIA obtenidas en vivo (marketplace, título, precio en MXN, url).

Tarea:
1. Descarta las que NO sean el mismo producto o uno muy similar.
2. Calcula el rango por marketplace (mín, mediana, máx).
3. Sugiere UN 'precio de competencia' en MXN para Mercado Libre: competitivo pero rentable (considera el costo del producto si se indica).
4. Explica en 1-2 líneas el porqué.

REGLAS: usa SOLO los precios provistos, NO inventes. Si no hay datos suficientes, precio_sugerido = null y explica por qué.

Responde SOLO en JSON válido (NO repitas la lista de fuentes):
{"precio_sugerido": <MXN o null>, "moneda": "MXN", "rango": {"min": <n>, "max": <n>, "mediana": <n>}, "por_marketplace": [{"marketplace": "..", "min": <n>, "max": <n>, "n": <int>}], "razonamiento": ".."}
```

Mensaje de usuario (`:164-169`) — **las URLs se omiten a propósito para no
inflar el prompt**; se devuelven aparte al frontend:

```
PRODUCTO: {nombre}
Categoría: {categoria}
Costo (MXN): {costo o 'n/d'}

COMPETENCIA (búsqueda: '{query}'):
- [{marketplace}] {titulo} — ${precio}
- …
```

### Modo B (`con_lista: false`, o sin fuentes)

`_PROMPT_SIN_LISTA` (`competencia.py:54-68`) — **sin datos en vivo**:

```
Eres analista de precios de e-commerce en México con amplio conocimiento de Mercado Libre, Amazon, Walmart, Temu y TikTok Shop en México. Te doy un PRODUCTO. Con tu conocimiento del mercado mexicano, estima el precio típico de venta de este producto (o uno muy similar) en cada marketplace y sugiere el 'precio de competencia' más adecuado para Mercado Libre: competitivo pero rentable (considera el costo si se indica).

IMPORTANTE: es una ESTIMACIÓN sin datos en vivo; si no tienes suficiente certeza, indícalo en el razonamiento y da un rango amplio.

Responde SOLO en JSON válido:
{"precio_sugerido": <MXN o null>, "moneda": "MXN", "rango": {"min": <n>, "max": <n>, "mediana": <n>}, "por_marketplace": [{"marketplace": "..", "estimado_min": <n>, "estimado_max": <n>}], "razonamiento": "..", "aviso": "estimación sin datos en vivo"}
```

Mensaje de usuario (`:172-177`):

```
PRODUCTO: {nombre}
Categoría: {categoria}
Marca: {marca} | Modelo: {modelo}
Costo (MXN): {costo o 'n/d'}
```

**Cómo saber en qué modo salió**: la respuesta trae `con_lista` (booleano real,
no lo que pediste) y `fuentes_encontradas` (`competencia.py:187-195`).
`con_lista: false` con `fuentes_encontradas: 0` = **no hubo datos en vivo, es
una estimación de memoria del modelo.**

---

## 12. Dónde queda guardado lo que produce `mejorar`

Tabla `enrich.channel_content` en la **BD kubera** (`channel_content.py:58`).
Llave `(sku, canal, cuenta)`. Un documento jsonb por canal.

Se escribe con `channel_content.guardar()` (`channel_content.py:447-492`):

- **Por omisión FUSIONA**: mandar solo `{"highlights": "..."}` conserva lo
  demás. `reemplazar=True` pisa el documento entero.
- **Nunca lanza**: devuelve `{"ok": False, "motivo": …}`.
- Canales válidos (FK contra `core.channels`, `channel_content.py:62`):
  `general, mercado_libre, amazon, tiktok, walmart, temu, shein`.
  **`'meli' NO existe — la FK lo rechaza.**
- Las **llaves del documento son canónicas**: `titulo`, `descripcion`,
  `bullets`, `highlights`, `atributos`. *"La traducción a `item_name` /
  `productName` / `goodsName` vive en el publicador, no aquí."*
  `backend_search_terms` es la nueva; el publicador la traduce a
  `generic_keyword` (`amazon_ia.py:353-355`).
- Se marca `origen: {campo: "ia"}` por cada campo escrito
  (`amazon_ia.py:494`, `tiktok_ia.py:245`, `temu_ia.py:240`) y una
  `spec_version`:

| Canal | `spec_version` | Dónde |
|---|---|---|
| Amazon | `amazon-mx-2026-07` | `amazon_ia.py:59` |
| TikTok | `tiktok-mx-2026-08` | `tiktok_ia.py:44` |
| Temu | `temu-v3-2026-08` | `temu_ia.py:57` |

**Errores típicos del guardado, ya traducidos a español** (`channel_content.py:474-486`):
- `channel_content_sku_fkey` → *"El SKU X todavía no está en el maestro
  (core.products). Lo agrega el ETL de las 06:15 UTC."*
- `channel_content_account_id_fkey` → la cuenta no existe en `core.accounts`.
- `channel_content_canal_fkey` → el canal no existe en `core.channels`.

---

## 13. Las banderas (`backend/config.py`)

| Variable | Default en código | Efecto |
|---|---|---|
| `DEEPSEEK_API_KEY` | `""` | Sin ella se salta DeepSeek y va a Claude |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | — |
| `DEEPSEEK_MODEL` | `deepseek-chat` | — |
| `ANTHROPIC_API_KEY` | `""` | Respaldo de `_completar`; **único** proveedor de `/api/ia/titulo` |
| `SERPAPI_KEY` | `""` | Sin ella, `precio-competencia` cae siempre al modo B |
| `AMAZON_IA_EN_CREAR` | **`False`** | Generar contenido de Amazon al **crear** cada producto |
| `TIKTOK_IA_EN_CREAR` | **`False`** | Igual, TikTok |
| `TEMU_IA_EN_CREAR` | **`False`** | Igual, Temu |

Los tres `*_IA_EN_CREAR` nacen apagados **a propósito**: *"Encenderlo cambia lo
que hace un flujo vivo —cada alta gasta 1-2 llamadas de IA y escribe en
producción—, y eso lleva el dale de Brandon, no un deploy silencioso"*
(`amazon_ia.py:602-604`). Los ganchos están en
`services/crear_producto.py:1060, 1081, 1099` y **nunca lanzan**: crear un
producto no puede fallar porque la IA esté caída.

---

## 14. Cosas medidas y rarezas que conviene saber antes de tocar nada

1. **Rama muerta en `ia_generadores.mejorar`.** Las líneas 379-380 quitan
   acentos al título "si el canal es amazon", pero la línea 345 ya devolvió
   para `amazon`. **Es inalcanzable.** El quitado real vive en
   `amazon_ia.py:450-451`. (Verificado leyendo el flujo de control.)

2. **Un canal desconocido en `mejorar` cae en el prompt de Mercado Libre.**
   `_MEJORAR.get(canal) or _MEJORAR["mercado_libre"]` (`ia_generadores.py:362`).
   No hay entradas para `walmart` ni `shein`, aunque ambos existen en
   `channel_content.CANALES`.

3. **Cinco entradas de `_PROMO` de Amazon son código muerto** (las acentuadas).
   Verificado por medición; **la detección funciona igual** porque cada una
   tiene gemela sin acento. Ver §8.1.

4. **La marca no es la misma en todos los canales.** `Ferrahome` en ML y
   TikTok, `Generic` en Amazon, ninguna en Temu. Ver §6.2.

5. **`_sin_acentos` convierte `ñ` en `n`** en el título de Amazon. Es
   deliberado (`ia_generadores.py:120-125`), pero cambia palabras: *"niños"* →
   *"ninos"*.

6. **Solo hay UNA ronda de reparación**, en los tres canales que la tienen
   (Amazon `amazon_ia.py:434-442`, TikTok `tiktok_ia.py:182-186`, Temu
   `temu_ia.py:163-175`). Si el segundo intento tampoco pasa, se reporta.

7. **`generar` no guarda nada.** El módulo `channel_content` se escribió justo
   por eso (`channel_content.py:6-13`): *"si no publicabas en la misma sesión,
   lo generado se perdía."*

8. **El Estudio no aplica lo rechazado.** El frontend solo escribe los campos
   que llegan en `campos` (`ProductStudio.tsx:617-622`), y pinta el parte
   (`rechazados`, `avisos`, `terminos_detectados`, `requisitos`) debajo del
   botón (`:625`, `setReporteIA`).

9. **Los prompts de ML y de `general` NO tienen validador.** El "máximo 60
   caracteres" del título de ML es una petición, no una garantía: nadie lo
   comprueba después. Si el modelo devuelve 71, se aplica tal cual.

---

## CÓMO REUSARLO SIN TOCAR PRODUCCIÓN

**La regla de esta carpeta: aquí se LEE y se producen ARCHIVOS. Nada escribe en
Woo, en kubera, en Odoo ni en ningún marketplace.**

### Lo que puedes reusar tal cual, sin código

1. **Los prompts.** Están completos y literales arriba. Cópialos a un chat, a
   un Google Sheet, a un script propio. Son texto: no llaman a nada.
2. **Las tablas de límites (§7.1).** Es el resumen que un KAM necesita para
   revisar a mano lo que un modelo (o una persona) escribió.
3. **Las dos listas de sustitución de marcas** — la de `terminos_protegidos`
   (§9) y la que va dentro del prompt de Temu (§6.5, regla 8).
4. **La clasificación FATAL vs AVISO** (§8.1). Sirve para decidir qué se
   arregla ya y qué puede esperar.

### Lo que puedes construir aquí, en `conocimientoGeneral/`

Un script que reciba una lista de SKUs y **produzca un `.md` o un `.csv` con el
contenido propuesto**, sin aplicarlo. Tres piezas y ninguna necesita
producción:

- **El contador de bytes.** Cópialo entero, son 3 líneas
  (`amazon_contenido.py:54-56`). Úsalo para auditar el
  `backend_search_terms` de cualquier texto:
  ```python
  def bytes_utf8(texto: str) -> int:
      return len((texto or "").encode("utf-8"))
  ```
  Y el recortador seguro (`amazon_contenido.py:155-164`), que no parte
  caracteres:
  ```python
  def cabe_en_bytes(texto: str, tope: int = 249) -> str:
      b = (texto or "").encode("utf-8")
      if len(b) <= tope:
          return texto or ""
      return b[:tope].decode("utf-8", errors="ignore").rstrip()
  ```
- **El validador de Amazon completo.** `backend/services/amazon_contenido.py`
  son 164 líneas y **no importa `config`, ni la BD, ni la red**: solo `re` y
  `unicodedata`. Se puede copiar entero a esta carpeta y correr contra
  cualquier texto. Es lo que se hizo para medir §7.2 y §8.1.
- **El proveedor de IA.** No copies `_completar`; escribe uno propio con tu
  clave. La forma es un POST a
  `{DEEPSEEK_BASE_URL}/chat/completions` con `model`, `messages`, `max_tokens`
  y `temperature` — API compatible con OpenAI. Con eso y el prompt literal
  reproduces lo que hace producción, sin usar producción.

### Lo que NO se puede sacar de aquí sin escribir en producción

Es importante que quede claro, porque parece que sí:

| Cosa | Por qué no |
|---|---|
| **`POST /api/ia/mejorar`** | **Escribe** en `enrich.channel_content` de la BD kubera. Un `mejorar` de prueba deja una fila real con `origen: ia`. |
| Los **requisitos reales de la categoría** de Amazon | Salen de `channel.field_requirements` en kubera. Se pueden **LEER con SELECT** (nunca marcando la sesión read-only, §pooler 6543), pero no están en esta carpeta. |
| Los **atributos reales de una categoría ML** | `GET api.mercadolibre.com/categories/{id}/attributes` — **es API pública y sí se puede llamar desde aquí**, no necesita token. Es la única de las cuatro que sí se puede replicar sola. |
| Los **atributos de TikTok** con sus `value_id` | Necesitan `access_token` + `shop_cipher` de la tienda viva (`tiktok_ia.py:138-140`). |
| La **plantilla de una hoja de Temu** con sus `vid` | Necesita las credenciales de Temu (`temu_ia.py:88-103`). |
| **`_pt_resuelto` / la detección de `productType`** | Llama a la SP-API de Amazon con el token de la cuenta. |

### Si de verdad hace falta correr `mejorar` contra algo

Se hace **desde el panel**, con un SKU real y con nombre. Es una decisión y las
decisiones llevan nombre — la trazabilidad (`origen: ia`, `spec_version`,
`hash_base`) es exactamente lo que se pierde cuando alguien corre un script
suelto.

### Un aviso final sobre el sandbox

Si alguien quiere probar el circuito completo **con base de datos**, el sandbox
(`yvootpbz…`) lleva clones de producción y es donde van las cobayas. La BD
kubera (`tukwcvsi…`) es **producción operativa**: desde fuera de la app se toca
**SOLO CON `SELECT`**, y **nunca** marcando la sesión como read-only — el DSN
apunta al pooler en modo transacción (6543), donde las conexiones se comparten
y un `SET SESSION … READ ONLY` se queda pegado y lo hereda el backend que
registra una venta. Ya reventó dos veces. Si necesitas la garantía:
`BEGIN; SET TRANSACTION READ ONLY; …; ROLLBACK;`.
