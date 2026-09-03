# Atributos de Mercado Libre con IA — cómo funciona de verdad

> Extraído de producción el 2026-09-03, commit 1a7da7e.
> ESTO ES UNA COPIA DE CONSULTA. La verdad vive en main; si algo no cuadra,
> gana main y hay que re-extraer.

Backend en ese commit: `version="0.371.0"` (`backend/main.py:132`).

---

## 0. La idea en una frase

**La IA NO decide QUÉ atributos existen. Mercado Libre sí.**

El código le pregunta a la API pública de ML qué atributos tiene la categoría, arma
una lista cerrada de IDs permitidos, se la mete a DeepSeek dentro del prompt, y
al volver **tira todo lo que no esté en esa lista**. La IA solo rellena valores.

Ese es el contrato: **la IA propone, el código valida.** Lo que sigue es dónde
está cada pieza, y dónde el contrato tiene agujeros reales.

---

## 1. Mapa de las piezas

| Qué | Archivo | Papel |
|---|---|---|
| El servicio canónico | `backend/services/ml_atributos.py` (285 líneas) | Trae los atributos de ML, arma el prompt, llama a DeepSeek, valida |
| Llamador 1 — alta de producto | `backend/services/crear_producto.py:764-792` (`atributos_ml`) | Paso 5/6 de "Crear Productos" |
| Persistencia (el único que escribe) | `backend/services/crear_producto.py:977-982` | Metas `ml_attr_<ID>` + `ml_atributos` |
| Llamador 2 — botón "Mejorar con IA" | `backend/services/ia_generadores.py:384-407` | Canal Mercado Libre del Estudio |
| Endpoint del botón | `backend/routers/ia.py:77-88` → `POST /api/ia/mejorar` | Lo llama `frontend/lib/api.ts:558` |
| Quien LEE los `ml_attr_` al publicar | `backend/services/publicar_ready.py:458-459` | Los mete en `prod["ml_attrs"]` |
| Quien los convierte a payload de ML | `backend/vendor/ml_ready/attribute_mapper.py` + `publisher_core.py:222,318` | Aquí sí se validan los VALORES contra las listas de ML |

⚠️ `backend/vendor/` **NO SE TOCA** (regla 1 de `CLAUDE.md`). Es el pipeline que
publicó 1,200+ productos. Se ajustan los adaptadores, nunca el vendor.

---

## 2. De dónde salen las listas cerradas de valores permitidos

De la **API pública de Mercado Libre, en vivo, sin token**:

```
GET https://api.mercadolibre.com/categories/{cat_id}/attributes
```

`backend/services/ml_atributos.py:89`. Verificado el 2026-09-03 con
`curl` sin credenciales sobre `MLM1055`: **HTTP 200**.

No es una tabla, no es Supabase, no es MySQL, no es un JSON del repo.
Es la API viva.

### El filtro exacto (`ml_atributos.py:91-108`)

Por cada atributo que devuelve ML:

1. Si `tags.hidden` **o** `tags.read_only` → **se descarta**.
2. Si el ID está en `_SKIP_IDS` → **se descarta**.
3. Lo que sobrevive se queda como `{id, name, value_type, valid_values}`, donde
   `valid_values` es `[v["name"] for v in a.get("values", [])]` — o sea, los
   **nombres** de los valores permitidos, no sus IDs.
4. Se parte en dos: si `tags.required` **o** `tags.catalog_required` →
   **PRINCIPALES**; el resto → **SECUNDARIAS**.

`_SKIP_IDS` completo (`ml_atributos.py:36-41`) — atributos que **no** se le piden
a la IA porque los gestiona el código aparte:

```python
_SKIP_IDS = {
    "BRAND", "MODEL", "SELLER_SKU", "GTIN", "EMPTY_GTIN_REASON",
    "SELLER_PACKAGE_WEIGHT", "SELLER_PACKAGE_LENGTH",
    "SELLER_PACKAGE_WIDTH", "SELLER_PACKAGE_HEIGHT",
    "ORIGIN", "OEM",
}
```

(BRAND y MODEL se le vuelven a permitir en la validación de salida — ver §5.)

### La caché

`_cat_cache: dict[str, dict]` a nivel de módulo, con `asyncio.Lock`
(`ml_atributos.py:74-86`). **No tiene TTL ni invalidación**: una categoría
consultada una vez queda cacheada mientras viva el proceso del backend. Se limpia
sola cuando Railway reinicia el contenedor (que pasa, por ejemplo, cada vez que se
cambia una variable de entorno).

### Si la llamada a ML falla

`except Exception` → `log.warning("atributos MeLi %s: %s")` → devuelve
`{"principales": [], "secundarias": []}` (`ml_atributos.py:111-113`).
**No aborta**. El prompt se arma igual, con "(ninguno)" en ambas listas, y
entonces `ids_validos` queda en `{"BRAND", "MODEL"}` — la salida útil es
prácticamente nula pero nadie grita. Timeout de esa llamada: **15 s**
(`httpx.AsyncClient(timeout=15.0)`, línea 88).

### ⚠️ Dos truncamientos que la IA no ve

Esto importa mucho y no está escrito en ningún otro lado:

| Truncamiento | Dónde | Efecto |
|---|---|---|
| **Máx. 15 atributos secundarios** | `MAX_SECUNDARIAS = 15` (línea 31), aplicado en `build_prompt` línea 133 | Si la categoría tiene 22 opcionales, la IA solo ve 15. Los otros 7 nunca se le piden. |
| **Máx. 15 valores por atributo** | `_fmt_attr_list` líneas 123-125 | De un `COLOR` con 51 valores, la IA ve 15 y una nota `... (51 opciones total)`. |

El prompt le exige a la IA *"ortografía EXACTA a los valores válidos listados"* —
pero para COLOR le lista 15 de 51. Si el producto es "Verde militar" y ese valor
está en la posición 30, la IA no lo tiene delante. Lo que escriba se salva (o no)
más tarde, en el matcher difuso del publisher (§7).

**Medición real, hecha el 2026-09-03 contra la API viva** — categoría `MLM1055`
(Celulares y Smartphones):

- ML devuelve **193** atributos.
- **165** salen por `hidden`/`read_only`.
- Sobreviven **3 PRINCIPALES** y **22 SECUNDARIAS** (de las 22, se mandan 15).
- `COLOR` tiene **51** valores válidos; se le muestran **15**.

---

## 3. EL PROMPT CANÓNICO — copiado literal

Esto es `build_prompt` en `backend/services/ml_atributos.py:131-193`, tal cual.
Las llaves dobles `{{` `}}` del final son escape de f-string de Python: en el
texto que le llega al modelo son llaves simples.

**Mensaje `system`** (`ml_atributos.py:259`):

```
Eres un experto en e-commerce para Mexico. Respondes siempre con JSON valido.
```

**Mensaje `user`** (la plantilla completa, `ml_atributos.py:142-193`):

```
Eres un experto en comercio electronico para Mexico (MercadoLibre).
Tu tarea es generar el MAYOR NUMERO POSIBLE de atributos para publicar un producto.
DEBES INTENTAR LLENAR CADA ATRIBUTO. Solo omite si es absolutamente imposible determinarlo.

## Producto
- SKU: {sku or 'N/A'}
- Nombre en tienda: {nombre}
- Titulo de Alibaba (extrae datos de aqui): {alibaba_titulo or 'N/A'}

## Atributos actuales en WooCommerce (base, respeta los correctos)
{atributos_actuales or 'Sin atributos'}

## Caracteristicas de Alibaba (extrae TODOS los datos posibles)
{caracteristicas_clave or 'N/A'}

## {principales_str}
## {secundarias_str}

## REGLAS DE INFERENCIA (aplica en este orden)
1. USA EL ID del atributo como clave JSON (ej: "COLOR" no "Color"; "BATTERY_TYPE" no "Tipo de bateria")
2. BRAND: siempre "{MARCA}" — nunca la del proveedor
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
{{
  "atributos": {{
    "BRAND": "{MARCA}",
    "MODEL": "FH-BT24V",
    "COLOR": "Negro"
  }},
  "flags": ["ID_ATRIBUTO: razon por la que no se pudo determinar"]
}}
```

Notas literales sobre el prompt:

- `{MARCA}` es la constante `MARCA = "Ferrahome"` (`ml_atributos.py:30`).
  Está **hardcodeada**, no sale de config. Su gemela en el alta es
  `crear_producto.py:761` `MARCA_FIJA = "Ferrahome"`.
- El prompt está escrito **sin acentos a propósito en casi todo el cuerpo**
  ("comercio electronico", "bateria", "razon"), pero pide valores "en ESPAÑOL
  con ortografia EXACTA". Es como está en producción.
- `9. ORIGIN: ... -> "China"` es **letra muerta**: `ORIGIN` está en `_SKIP_IDS`,
  así que ese ID nunca aparece en las listas y la validación de salida lo tira.
  Lo mismo con la mención de OEM en la regla 12.

### Cómo se ven `{principales_str}` y `{secundarias_str}` rellenos

Formato de `_fmt_attr_list` (`ml_atributos.py:116-128`). Renderizado real del
2026-09-03 para `MLM1055` — esto es literalmente lo que va dentro del prompt:

```
ATRIBUTOS OBLIGATORIOS — debes llenarlos TODOS:
  - IS_DUAL_SIM (Es Dual SIM, tipo: boolean)
    Valores válidos: No, Sí
  - COLOR (Color, tipo: string)
    Valores válidos: Coral claro, Coral, Naranja claro, Naranja oscuro, Caqui, Verde lima, Verde musgo, Chocolate, Suela, Marrón claro, Marrón oscuro, Dorado, Rojo, Gris, Rosa ... (51 opciones total)
  - CARRIER (Compañía telefónica, tipo: list)
    Valores válidos: Desbloqueado, Unefon, Iusacell, Movistar, Nextel, AT&T, Telcel, Axtel, Bait

ATRIBUTOS OPCIONALES — llena TODOS los que puedas, sé proactivo en inferir:
  - LINE (Línea, tipo: string)
  - INTERNAL_MEMORY (Memoria interna, tipo: number_unit)
  - RAM (Memoria RAM, tipo: number_unit)
  - ACCESSORIES_INCLUDED (Accesorios incluidos, tipo: string)
    Valores válidos: 1 cable USB, 1 manual de usuario, 1 cargador
  - TELECOMMUNICATION_HOMOLOGATION_NUMBER (Número de homologación de telecomunicaciones, tipo: string)
  - GRADING (Estado del reacondicionado, tipo: list)
    Valores válidos: Excelente, Bueno, Aceptable
  - SIM_CARD_SLOTS_NUMBER (Cantidad de ranuras para tarjeta SIM, tipo: number)
  - ESIMS_NUMBER (Cantidad de eSIMs, tipo: number)
  - OPERATING_SYSTEM_NAME (Nombre del sistema operativo, tipo: string)
    Valores válidos: Android, iOS, Windows Phone
  - MOBILE_NETWORK (Red móvil, tipo: string)
    Valores válidos: 2G, 3G, 4G/LTE, 5G
  - PROCESSOR_MODEL (Modelo del procesador, tipo: string)
    Valores válidos: Intel XScale PXA270, MediaTek MT6572M, Snapdragon 615
  - PROCESSOR_SPEED (Velocidad del procesador, tipo: number_unit)
  - IS_GAMING_CELLPHONE (Es celular de juego, tipo: boolean)
    Valores válidos: No, Sí
  - IS_RUGGED_CELLPHONE (Es celular robusto, tipo: boolean)
    Valores válidos: No, Sí
  - MAIN_COLOR (Color principal, tipo: list)
    Valores válidos: Negro, Azul, Rojo, Violeta, Marrón, Verde, Naranja, Celeste, Rosa, Dorado, Plateado, Amarillo, Gris, Blanco, Azul acero ... (55 opciones total)
```

Si una lista viene vacía, `_fmt_attr_list` escribe literalmente
`"{label}: (ninguno)"` (línea 118).

---

## 4. Qué modelo, con qué parámetros

`_deepseek_json` — `backend/services/ml_atributos.py:211-241`.

| Parámetro | Valor | Línea |
|---|---|---|
| Proveedor | **DeepSeek** (API compatible con OpenAI) | 222 |
| Endpoint | `{settings.deepseek_base_url}/chat/completions` — default `https://api.deepseek.com` | `config.py:84` |
| Modelo | `settings.deepseek_model` — default **`deepseek-chat`** | `config.py:85` |
| Auth | header `Authorization: Bearer {settings.deepseek_api_key}` | 224 |
| `response_format` | **`{"type": "json_object"}`** (modo JSON forzado) | 231 |
| `temperature` | **0.2** | 232 |
| `max_tokens` | **4096** (default del parámetro, ningún llamador lo cambia) | 211, 233 |
| Timeout HTTP | **120 s** (`httpx.AsyncClient(timeout=120.0)`) | 220 |
| Reintentos | **solo en HTTP 429**, backoff `[10, 20, 10]` segundos → hasta 3 reintentos | 218, 236-239 |
| Parseo | `json.loads(r.json()["choices"][0]["message"]["content"])` — directo, sin red de seguridad | 241 |

### El fallback a Claude, y cuándo NO ocurre

`ml_atributos.py:213-216`:

```python
if not settings.deepseek_api_key:
    from services.ia_generadores import _completar
    r = await asyncio.to_thread(_completar, system, user, max_tokens)
    return _parse_json(r.get("texto", "")) if r.get("ok") else {}
```

⚠️ **El fallback solo se activa si NO HAY clave de DeepSeek.** Si la clave existe
pero la llamada falla (500, timeout, 401, 429 tras 3 reintentos), el
`r.raise_for_status()` de la línea 240 lanza, lo atrapa `generar_atributos`
(líneas 264-266) con un `log.warning`, y `result` queda en `{}`. **No se intenta
Claude.** El producto sale con un solo atributo: `BRAND: Ferrahome`.

Ese fallback, cuando sí corre, usa `ia_generadores._completar`
(`ia_generadores.py:34-81`), que prueba DeepSeek → Claude
(`_CLAUDE_MODEL = "claude-opus-4-8"`, línea 29) con `temperature: 0.7` y sin
`response_format`. De ahí que ese camino sí pase por `_parse_json`
(`ml_atributos.py:197-208`), que quita cercas ```` ```json ```` y, si aun así no
parsea, busca el primer `{...}` con regex. Si nada funciona: `{}`.

---

## 5. LA VALIDACIÓN — el contrato "la IA propone, el código valida"

Todo pasa en `generar_atributos`, `ml_atributos.py:245-284`. Son cinco líneas y
son lo más importante del archivo:

```python
atributos_raw = result.get("atributos", {}) or {}
flags = result.get("flags", []) or []

todos = meli_attrs.get("principales", []) + meli_attrs.get("secundarias", [])
ids_validos = {a["id"] for a in todos} | {"BRAND", "MODEL"}
atributos = {k: str(v) for k, v in atributos_raw.items() if k in ids_validos and v}
atributos["BRAND"] = MARCA  # forzar marca
```

Desglosado:

1. **Lista blanca de CLAVES.** `ids_validos` = todos los IDs que ML declaró para
   esa categoría (principales + secundarias **completas**, no solo las 15 que se
   mostraron), más `BRAND` y `MODEL`. Cualquier clave inventada por la IA se cae
   en silencio. Sin excepción, sin log, sin aviso.
2. **Valores falsy fuera.** El `and v` tira `""`, `None`, `0`, `[]`, `False`.
   ⚠️ Esto significa que un `IS_DUAL_SIM: false` legítimo se pierde.
3. **Todo a `str()`.** Si la IA devuelve una lista (p. ej. `FEATURES: ["a","b"]`),
   el valor guardado es la representación Python `"['a', 'b']"`. No hay
   normalización. *(Inferido de la lectura del código; no lo verifiqué con una
   respuesta real de la IA.)*
4. **BRAND se fuerza SIEMPRE**, después del filtro. Aunque la IA la omita o
   ponga la del proveedor, queda `Ferrahome`.
5. **Ningún VALOR se valida contra `valid_values` aquí.** Ese es el hueco
   grande: si la IA escribe `COLOR: "Verde militar"` y ML solo acepta
   `"Verde oscuro"`, esta función lo deja pasar tal cual. La confrontación
   contra la lista cerrada de ML ocurre **mucho después**, al publicar (§7).

### Lo que devuelve

```python
{
  "atributos":     {ID: "valor", ...},   # ya filtrado, BRAND forzada
  "flags":         [...],                # crudo, sin tocar
  "atributos_str": "BRAND: Ferrahome | COLOR: Negro | ...",
  "num":           len(atributos),       # cuenta BRAND
  "validos":       True/False,
  "meli_attrs":    {"principales": [...], "secundarias": [...]},
}
```

`atributos_str` sale de `_format_atributos` (línea 48): `" | ".join(f"{k}: {v}")`
saltando los falsy.

`validos` sale de `_calc_atributos_validos_str` (líneas 52-70). Traducido:
**"¿hay al menos un atributo que no sea de los cuatro básicos?"** Parte
`atributos_str` por `|`, quita los de `_ATTRS_EXCLUIDOS` (`url_alibaba`,
`alibaba_price`, `alibaba_title_original`, `ml_category_id`, `categoria_meli_id`)
y busca alguna clave que **no** contenga ninguno de estos fragmentos
(`_ATTRS_BASICOS_RE`, línea 42):

```
"peso", "dimen", "medida", "talla", "tamaño", "marca", "brand", "variante", "variant"
```

Si solo hay `BRAND` → `"brand"` está en la lista de básicos → `validos = False`.
*(Nota menor observada en el código: la función parte el string por `|`, así que
un valor que contenga una barra vertical rompería el conteo. No lo vi ocurrir.)*

---

## 6. Dónde se guarda, y quién lo lee después

### La escritura — hay UN SOLO escritor

`backend/services/crear_producto.py:977-982`, dentro del alta de producto:

```python
# Atributos ML → metas `ml_attr_<ID>` (lo que LEE el publisher en
# construir_prod) + un `ml_atributos` JSON de respaldo/trazabilidad.
if atributos:
    for _aid, _aval in atributos.items():
        meta.append({"key": f"ml_attr_{_aid}", "value": str(_aval)})
    meta.append({"key": "ml_atributos", "value": json.dumps(atributos, ensure_ascii=False)})
```

Se guarda **duplicado a propósito**: N metas `ml_attr_<ID>` (las operativas) y una
meta `ml_atributos` con el JSON entero (respaldo/trazabilidad). Van dentro del
`payload["meta_data"]` de un `PUT` a la REST de WooCommerce
(`_actualizar_wc(wc_id, payload)`, línea 985).

Justo debajo, línea 1007: `tiene_attrs = len(atributos) >= 2  # BRAND + al menos 1 más`
— así se decide si "atributos" aparece en la lista de "lo que falta" del resumen.

⚠️ **`ml_attr_*` vive SOLO en WooCommerce.** No tiene espejo en kubera ni en
ningún lado (`docs/PROMPT_ALMACENAMIENTO_POR_CANAL.md:176`: *"Sin espejo en
ninguna parte (si Woo se pierde, se pierde)"*).

Censo de producción según `docs/PROMPT_ALMACENAMIENTO_POR_CANAL.md:164`
(medición de esa nota, **no re-verificada por mí el 2026-09-03**):
**992 claves `ml_attr_*` distintas, 7,720 filas, 987 posts** (779 productos +
208 variaciones). Las más comunes: `ml_attr_brand` (778), `ml_attr_MODEL` (699),
`ml_attr_COLOR` (507), `ml_attr_Marca` (208), `ml_attr_Talla` (159).

Las claves en minúscula y en español (`ml_attr_brand`, `ml_attr_Marca`) son
**históricas**, de antes de este servicio; el código de hoy escribe el ID de ML
tal cual (`ml_attr_COLOR`). El publisher tolera ambas (§7).

### La lectura

`backend/services/publicar_ready.py:458-459`, dentro de `construir_prod`:

```python
"ml_attrs": {k[len("ml_attr_"):]: v for k, v in meta.items()
             if k.startswith("ml_attr_") and v},
```

Es decir: **todas** las metas `ml_attr_*` del post, con el prefijo quitado, van a
`prod["ml_attrs"]`. Ese dict es lo que consume el vendor al publicar.

### ⚠️ El botón "Mejorar con IA" NO guarda nada en `ml_attr_*`

Esto es una asimetría real y confunde a cualquiera que la descubra en caliente:

- **Alta (Crear Productos)** → `atributos_ml` → `ml_atributos.generar_atributos`
  → metas `ml_attr_<ID>`. Persiste.
- **Estudio, botón "Mejorar con IA", canal Mercado Libre**
  (`ia_generadores.py:384-405`) → llama al mismo servicio, y **traduce el
  resultado a `[{nombre, valor}]` usando el nombre legible de ML**:

  ```python
  todos = r["meli_attrs"]["principales"] + r["meli_attrs"]["secundarias"]
  nombre_por_id = {a["id"]: a["name"] for a in todos}
  if r["atributos"]:
      data["atributos"] = [
          {"nombre": nombre_por_id.get(k, k), "valor": v}
          for k, v in r["atributos"].items()
      ]
  ```

  Eso vuelve al frontend y se queda en el formulario. Si el usuario aprieta
  "Guardar contenido" (`POST /api/productos/{sku}/contenido` →
  `woocommerce.guardar_contenido_wc`, `woocommerce.py:1800`), se guardan como
  **atributos nativos de WooCommerce por NOMBRE**, no como metas `ml_attr_*`.
  Camino distinto, tabla distinta, matcher distinto al publicar.

Si el atributo no se resuelve en `nombre_por_id` (porque su ID quedó fuera de las
listas), se usa el ID crudo como nombre.

---

## 7. Qué pasa después, al publicar (dónde SÍ se validan los valores)

`backend/vendor/ml_ready/publisher_core.py:222` llama a
`build_attributes(prod['ml_attrs'], ml_category_attrs, prod.get('wc_attrs', {}))`.

Ahí, en `backend/vendor/ml_ready/attribute_mapper.py:663-739`, por cada atributo
que ML pide para la categoría:

1. **Busca el valor**, en este orden (líneas 697-703):
   `ml_attrs[ID exacto]` → `ml_attrs[id.lower()]` → `ml_attrs[nombre.lower()]` →
   normalizados. Por eso conviven `ml_attr_COLOR` y `ml_attr_color`.
2. Si no lo encuentra, cae a los atributos nativos de WooCommerce vía
   `WC_TO_ML_ID` (líneas 708-715), un diccionario de ~600 entradas en español
   (`'voltaje' → 'VOLTAGE'`, `'tipo de bateria' → 'BATTERY_TYPE'`, …). Es lo que
   rescata el camino del Estudio descrito arriba.
3. **Si el atributo tiene lista cerrada** (`allowed_vals`), corre `_find_value_id`
   (líneas 742-773) — un matcher difuso de tres pasadas:
   - match exacto normalizado,
   - match por substring en cualquier dirección,
   - match por tokens (todos los tokens del valor de ML presentes en el valor
     nuestro).

   Si **acierta** → `{'id': ATTR, 'value_id': <id de ML>}`.
   Si **falla y el atributo es required** → ⚠️ **usa el PRIMER valor de la lista**
   (`allowed_vals[0]['id']`, línea 725). Un dato arbitrario, no una omisión.
   Si falla y es opcional → se omite en silencio.
4. **Si acepta texto libre** → pasa por `_validate_value` (líneas 635-660):
   `number_unit` se formatea con la unidad por defecto, `number` que no sea
   numérico se **omite** (`"N/A"` no se manda), edades numéricas reciben `" años"`.

`_normalize` (línea 776) separa número de unidad (`"120v"` → `"120 v"`), así que
un `"120V"` de la IA sí matchea `"120 V"` de ML.

Después, `publisher_core.py:318` corre `build_secondary_attributes` para llenar
los opcionales que quedaron vacíos con lo que haya en `wc_attrs`/`ml_attrs` y las
dimensiones. Y `publisher_core` rellena por su cuenta lo que `_SKIP_IDS` dejó
fuera del prompt: `BRAND` (línea 228, `DEFAULT_BRAND`), `SELLER_SKU` (232),
`MODEL` (240, fallback al título recortado a 60), `PART_NUMBER` (245, fallback
al SKU), `MANUFACTURER` (250), `GTIN`/`EMPTY_GTIN_REASON` (255-261) y las cuatro
`SELLER_PACKAGE_*` (280-286).

**Resumen del contrato en dos capas:**

| Capa | Valida | No valida |
|---|---|---|
| `ml_atributos.generar_atributos` | Las **CLAVES** contra los IDs de la categoría | Los valores |
| `attribute_mapper.build_attributes` (vendor) | Los **VALORES** contra `allowed_vals` y el `value_type` | Que el valor sea *cierto* |

Nadie, en ninguna capa, valida que el dato sea **verdadero**.

---

## 8. Cuando la IA se inventa un dato

### El caso conocido

Documentado en `docs/TIKTOK_MANUAL.md:167-171` (salió en TikTok, pero el defecto
es del mismo patrón y aplica igual a ML):

> **La IA rellena datos que ella misma admite no saber.** Caso real: puso
> `"1.5V"` y en la MISMA respuesta anotó *"voltaje no confirmado en descripción"*.
> Pasaba el validador porque el atributo es texto libre. **Un dato inventado NO da
> error: se publica**, y después nadie sabe cuál era mentira. Regla: lo que la IA
> marque en `flags` NO se manda.

Repetido en `docs/TIKTOK_ENTREGA_A_OMNICANAL.md:260` y
`docs/TIKTOK_INVENTARIO_DE_CAMPOS.md:325`.

### Por qué pasa, exactamente

Son tres cosas que se suman, y las tres están en el código de arriba:

1. **El prompt EXIGE rellenar.** Líneas 143-144: *"generar el MAYOR NUMERO
   POSIBLE"*, *"DEBES INTENTAR LLENAR CADA ATRIBUTO"*, y la regla 5 lo autoriza
   explícitamente: *"Estima con logica si no hay dato exacto"*. Un LLM al que se
   le pide estimar, estima.
2. **El `flags` existe y NADIE lo usa.** `generar_atributos` lo devuelve
   (`ml_atributos.py:279`), pero:
   - `crear_producto.atributos_ml` hace `return r.get("atributos", {})`
     (línea 789) — **descarta `flags`**.
   - `ia_generadores.mejorar` solo lee `r["atributos"]` y `r["meli_attrs"]`
     (líneas 399-405) — **descarta `flags`**.

   Verificado: `grep -rn "flags" backend/services/crear_producto.py
   backend/services/ia_generadores.py backend/routers/` no devuelve ni un uso
   relacionado con esto. **`flags` se calcula, viaja y muere.** No se guarda, no
   se muestra, no filtra nada.
3. **Texto libre no tiene con qué chocar.** En `attribute_mapper`, un atributo
   sin `allowed_vals` solo se valida por `value_type`. `"1.5 V"` en un
   `number_unit` es sintácticamente perfecto. Pasa.

### Y hay un cuarto agravante, específico de ML

Si el atributo **sí** tiene lista cerrada, es *required*, y el matcher no
encuentra parecido, `attribute_mapper.py:725` mete `allowed_vals[0]['id']` — el
**primer valor de la lista de ML**. Ni siquiera es una alucinación de la IA: es
el código eligiendo un valor arbitrario para que ML no rechace la publicación.

### Qué hacer con eso (lo que ya está decidido en la casa)

La regla escrita en `docs/TIKTOK_MANUAL.md:171` es: **"lo que la IA marque en
`flags` NO se manda"**. En el circuito de Mercado Libre **esa regla todavía no
está implementada**. Al revisar atributos de un producto, un `ml_attr_VOLTAGE`,
`ml_attr_BATTERY_TYPE`, `ml_attr_POWER` o cualquier número con unidad merece
verificación humana contra la ficha del proveedor antes de dar por bueno el
listing.

---

## 9. Los dos disparadores, resumidos

### A) Alta de producto — pestaña "Crear Productos"

`crear_producto._procesar` (`crear_producto.py:854+`), concurrencia
`asyncio.Semaphore(2)`. Es el paso **5/6** del alta
(`docs/FLUJO_POR_CANAL.md:46`). Llama a `atributos_ml`
(`crear_producto.py:764-792`) con:

| Argumento | De dónde sale |
|---|---|
| `cat_id` | La categoría de ML resuelta en el paso 4/6. **Si es vacío, `atributos_ml` devuelve `{}` sin llamar a la IA** (línea 772) |
| `nombre` | El título del producto |
| `alibaba_titulo` | `scrape["titulo"]` (Apify) |
| `atributos_actuales` | **`""` — siempre vacío en este camino** (línea 785) |
| `caracteristicas_clave` | `scrape["caracteristicas_clave"]`, o `json.dumps(scrape["specs"])[:1500]` |
| `sku` | El SKU |

Si algo revienta: `log.warning("atributos ML para %s falló")` y `return {}`
(líneas 790-792). El alta **no se detiene**.

### B) Botón "Mejorar con IA" — Estudio, canal Mercado Libre

`POST /api/ia/mejorar` (`routers/ia.py:77-88`) → `ia_generadores.mejorar`.
Ese endpoint primero hace su llamada genérica de contenido y **solo después**,
si `canal == "mercado_libre"` y `producto["ml_cat_id"]` no está vacío
(`ia_generadores.py:384`), pide los atributos reales y **reemplaza** los que
venían en `data["atributos"]`.

| Argumento | De dónde sale |
|---|---|
| `cat_id` | `producto["ml_cat_id"]` — lo manda el frontend. **Sin él, este bloque no corre** |
| `nombre` | `producto["nombre"]` |
| `alibaba_titulo` | también `producto["nombre"]` (no hay título de Alibaba aquí) |
| `atributos_actuales` | `"nombre: valor; ..."` de los atributos que el usuario ya tiene en pantalla |
| `caracteristicas_clave` | La descripción, sin HTML, cortada a 1500 caracteres |

Si falla: `log.warning("mejorar ML atributos: %s")` y se devuelven los atributos
que hubiera generado el prompt genérico (líneas 406-407).

---

## 10. CÓMO REUSARLO SIN TOCAR PRODUCCIÓN

Un script suelto puede reproducir **el 100% del razonamiento** —listas de ML,
prompt, llamada, validación de claves y el matcheo de valores del vendor— sin
escribir en ningún sistema. Lo único imposible es que el resultado llegue a un
listing.

### Lo que necesitas

| Pieza | Qué es | Cuesta |
|---|---|---|
| `GET https://api.mercadolibre.com/categories/{cat_id}/attributes` | Las listas cerradas | **Nada. Es público, sin token.** Verificado 2026-09-03: HTTP 200 sin credenciales |
| Un `cat_id` de ML | p. ej. `MLM1055` | Lo tienes si conoces el producto; si no, ver abajo |
| `DEEPSEEK_API_KEY` | Para la llamada al modelo | Vive en Railway (`BackendOmnicanal`). **Es la única credencial obligatoria** |
| Los tres textos del producto | título, título de Alibaba, características | Se pueden pegar A MANO. No hace falta leer Woo |
| `MARCA = "Ferrahome"` | Constante | Copiar el literal |

**Lo que NO necesitas:** token de Mercado Libre, acceso a WooCommerce, acceso a
la BD `kubera`, acceso a MySQL, ni Odoo. El servicio `ml_atributos.py` no toca
ninguno de ellos: es API pública de ML + DeepSeek, y punto.

### La receta

1. **Copia las funciones puras** de `backend/services/ml_atributos.py`. Son
   independientes y no importan nada del proyecto salvo `config.settings`:
   - `_SKIP_IDS`, `MARCA`, `MAX_SECUNDARIAS`
   - `get_meli_all_attributes` (cámbiale `settings` por nada — no usa)
   - `_fmt_attr_list` y `build_prompt`
   - el bloque de validación de `generar_atributos` (líneas 268-283)

   Sustituye `from config import settings` por leer `DEEPSEEK_API_KEY` del
   entorno, y `settings.deepseek_base_url` / `settings.deepseek_model` por
   `https://api.deepseek.com` / `deepseek-chat`.

2. **Si no tienes el `cat_id`** y solo tienes el título, el predictor de ML
   también es público:
   `GET https://api.mercadolibre.com/sites/MLM/domain_discovery/search?q=<título>`.
   ⚠️ Ojo con la regla de la casa: **la elección del PANEL manda sobre cualquier
   predictor** (meta `ml_categoria_id` > `ml_category_id`). Caso real:
   TEC-1812-NEG se publicó en "Máquinas de Coser" siendo "Máquinas Sexuales" por
   ignorar el panel (`CLAUDE.md`, regla 2).

3. **Para dry-run sin gastar IA**: sáltate `_deepseek_json` y pega tú mismo un
   JSON `{"atributos": {...}, "flags": [...]}`. El resto del pipeline
   (validación de claves, `atributos_str`, `validos`) es puro y determinista.

4. **Para simular el publicado**, `backend/vendor/ml_ready/attribute_mapper.py`
   también es puro: `build_attributes(ml_attrs, ml_category_attrs, wc_attrs)`
   recibe el JSON crudo de `/categories/{id}/attributes` como `ml_category_attrs`
   y te devuelve la lista de atributos exacta que iría al payload de ML, **sin
   llamar a ML**. Es la única forma de ver, offline, qué valores sobreviven al
   matcher difuso y cuáles se reemplazan por `allowed_vals[0]`.

5. **Sugerencia con valor añadido**: reproduce el pipeline pero **imprime
   `flags`**. Hoy nadie los ve (§8). Un script suelto que muestre lado a lado
   *"lo que la IA puso"* vs *"lo que la IA admitió no saber"* es información que
   producción genera y tira a la basura.

### Lo que es IMPOSIBLE sin escribir

- **Que los atributos lleguen a un producto real.** El único escritor es
  `crear_producto.py:977-982`, y escribe metas `ml_attr_<ID>` con un `PUT` a la
  REST de WooCommerce. Sin ese `PUT`, `publicar_ready.construir_prod` no ve nada
  y `prod["ml_attrs"]` sale vacío.
- **Que un producto ya publicado cambie de atributos.** Eso exige tanto el `PUT`
  a Woo como una llamada de actualización a ML con token de la cuenta.
- **Ver el catálogo real de qué SKU tiene qué atributo.** Vive en `wp_postmeta`
  de WordPress. Es LECTURA (`CLAUDE.md`: *"Las 72 tablas `wp_*`: lectura directa
  OK, DDL/DML no"*), pero necesita las credenciales `WPDB_*`.
- **Comparar contra lo que hay publicado en ML.** Requiere token de la cuenta
  (BEKURA / SANCORFASHION).

### Un pie de página que ahorra una tarde

`chunche.shop` está **en mantenimiento (503) desde el 19-ago-2026**. La REST API
de Woo no se ve afectada, pero si un script tuyo intenta leer la tienda por HTML
va a fallar por una razón que no tiene nada que ver con los atributos.

---

## 11. Errata y trampas, en una lista

| # | Qué | Dónde |
|---|---|---|
| 1 | La IA solo ve **15 de los N** atributos secundarios de la categoría | `ml_atributos.py:31,133` |
| 2 | La IA solo ve **15 de los N** valores válidos por atributo, aunque el prompt le exija "ortografía EXACTA" | `ml_atributos.py:123-125` |
| 3 | **`flags` se genera y nadie lo lee.** Es el mecanismo antimentira del prompt, y está desconectado | `ml_atributos.py:279` vs `crear_producto.py:789`, `ia_generadores.py:399-405` |
| 4 | Si DeepSeek falla y **hay** clave, NO hay fallback a Claude. Solo hay fallback si NO hay clave | `ml_atributos.py:213`, `240` |
| 5 | Si `/categories/{id}/attributes` falla, no aborta: sigue con listas vacías | `ml_atributos.py:111-113` |
| 6 | La caché de categorías **no expira**; muere con el proceso | `ml_atributos.py:74` |
| 7 | El filtro `and v` tira valores falsy legítimos (`false`, `0`) | `ml_atributos.py:273` |
| 8 | Ningún valor se valida contra `valid_values` en este servicio; solo al publicar | `ml_atributos.py:273` vs `attribute_mapper.py:718-726` |
| 9 | Atributo *required* con lista cerrada y sin match → **primer valor de la lista**, arbitrario | `attribute_mapper.py:725,737` |
| 10 | Las reglas 9 (ORIGIN) y 12 (OEM) del prompt son letra muerta: esos IDs están en `_SKIP_IDS` | `ml_atributos.py:36-41,174,177` |
| 11 | El botón "Mejorar con IA" NO guarda `ml_attr_*`; guarda atributos nativos de Woo por nombre | `ia_generadores.py:402-405`, `woocommerce.py:1800` |
| 12 | `MARCA = "Ferrahome"` está hardcodeada en dos archivos, no en config | `ml_atributos.py:30`, `crear_producto.py:761` |
| 13 | `ml_attr_*` no tiene espejo: si Woo se pierde, se pierde | `docs/PROMPT_ALMACENAMIENTO_POR_CANAL.md:176` |

---

## Anexo — qué verifiqué yo y qué no

**Verificado leyendo el código en el commit 1a7da7e:** todo lo de §1 a §7 y §9,
con `archivo:línea` en cada afirmación. El punto 3 de §11 lo verifiqué además con
un `grep` de `flags` sobre `crear_producto.py`, `ia_generadores.py` y
`backend/routers/`.

**Verificado contra la API viva de Mercado Libre el 2026-09-03:** que
`/categories/{id}/attributes` responde 200 sin token; la forma de la respuesta; y
los conteos de `MLM1055` (193 / 165 / 3 / 22 / COLOR 51). El bloque de prompt
renderizado de §3 lo generé aplicando el filtro exacto del código a esa respuesta.

**NO verificado (tomado de docs del repo):** el censo de 992 claves / 7,720 filas
/ 987 posts de `ml_attr_*` (fuente: `docs/PROMPT_ALMACENAMIENTO_POR_CANAL.md:164`)
y el caso del `"1.5V"` (fuente: `docs/TIKTOK_MANUAL.md:167-171`). Ninguno de los
dos lo re-medí contra la base.

**Inferido, no probado:** que un valor de tipo lista devuelto por la IA quedaría
guardado como `"['a', 'b']"` por el `str(v)` de la línea 273.
