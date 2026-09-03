# `generar_contenido_ml.py` — contenido y atributos de ML para una lista de SKUs

> Vive en la rama `conocimiento`. **No está en `main`, así que no puede llegar a
> producción**: Railway solo despliega desde `main`.
> Lógica copiada de producción, commit `1a7da7e` (backend v0.371.0), 2026-09-03.

Le das SKUs. Te devuelve, en un JSON y un CSV, el **título**, la **descripción**
y los **atributos de Mercado Libre** que la IA propone para cada uno — con los
mismos prompts y las mismas validaciones que usa el panel.

**No publica nada. No escribe nada. En ningún lado.**

---

## En 60 segundos

```bash
cd conocimientoGeneral/ML_PUBLICACIONES_IA/scripts

cp .env.ejemplo .env          # y llena las llaves adentro
python generar_contenido_ml.py --verificar
python generar_contenido_ml.py EST-0054-NEG

# → salidas/contenido_ml_20260903_104402.json
# → salidas/contenido_ml_20260903_104402.csv   (ábrelo en Excel)
```

---

## Lo que hace, paso por paso

| # | Paso | Contra qué | Escribe |
|---|---|---|---|
| 1 | Lee el producto (título, descripción, atributos, metas `ml_*`) | WooCommerce, `GET /wp-json/wc/v3/products?sku=` | no |
| 2 | Resuelve en qué categoría de ML va a salir | metas del producto, o `domain_discovery` | no |
| 3 | Trae la ruta, el dominio y la **lista cerrada de atributos** de esa categoría | API **pública** de ML, sin token | no |
| 4 | Pide el **título y la descripción** | DeepSeek o Claude | no |
| 5 | Pide los **atributos**, con el prompt canónico | DeepSeek o Claude | no |
| 6 | **Valida**: solo sobreviven las claves que ML declaró; fuerza `BRAND` | — | no |
| 7 | Arma el semáforo (título largo, obligatorios sin llenar, guía de tallas…) | — | no |
| 8 | Deja el JSON y el CSV | disco, en `salidas/` | **sí, archivos** |

La única petición del script que no es un `GET` es la que va al proveedor de IA.

---

## Ejemplo de punta a punta

### 1 · Las llaves

```bash
cp .env.ejemplo .env
```

Y dentro del `.env` (que **nunca** se sube — el repositorio es público):

```
WC_URL=
WC_CONSUMER_KEY=
WC_CONSUMER_SECRET=

DEEPSEEK_API_KEY=
```

* Las tres de WooCommerce son **obligatorias**. Con permisos de **lectura**
  basta: el script no escribe.
* De IA hace falta **una**: `DEEPSEEK_API_KEY` (lo que usa producción, vive en
  Railway → BackendOmnicanal) o `ANTHROPIC_API_KEY` (el respaldo; ese necesita
  `pip install anthropic`).
* **Mercado Libre no pide nada.** `/categories/{id}` y
  `/categories/{id}/attributes` son públicos. Verificado el 2026-09-03: HTTP 200
  sin cabecera de autorización.

> Nota para el siguiente chat: el `.gitignore` de la raíz tiene `.env.*` y solo
> perdona `.env.example`. Como aquí la plantilla se llama **`.env.ejemplo`**,
> estaba quedando **ignorada en silencio** — el script pedía un archivo que
> nadie iba a recibir. Se arregló con dos negaciones en
> `conocimientoGeneral/.gitignore`, que llevan la explicación al lado. El `.env`
> de verdad sigue ignorado.

### 2 · Comprobar antes de gastar

```bash
python generar_contenido_ml.py --verificar
```

```
generar_contenido_ml.py v1.0.0
Copiado de: produccion commit 1a7da7e (backend v0.371.0), 2026-09-03

[1/3] WooCommerce (solo lectura)…
      OK — https://chunche.shop/wp-json/wc/v3
[2/3] API pública de Mercado Libre (sin token)…
      OK — MLM1055 = Celulares y Smartphones (MLM-CELLPHONES)
[3/3] Proveedor de IA…
      OK — DeepSeek, modelo deepseek-chat (es el que usa producción)

Recordatorio: este script solo hace GET contra Woo y contra ML.
No publica, no escribe, no cambia nada.
```

Si falta algo, lo dice con nombre y apellido y no arranca.

### 3 · La lista de SKUs

Un SKU por línea. `#` es comentario, las líneas vacías se ignoran, y los
repetidos se quitan solos:

```
# mis_skus.txt — pendientes de la semana
EST-0054-NEG
TEC-0661-BLN
ACC-0653-CHE-13-16   # el clon sin limpiar, a ver qué sale
```

### 4 · Correr

```bash
python generar_contenido_ml.py --archivo mis_skus.txt
```

```
generar_contenido_ml.py v1.0.0 — 3 SKU(s)
  copiado de : produccion commit 1a7da7e (backend v0.371.0), 2026-09-03
  salidas    : …\ML_PUBLICACIONES_IA\salidas
  cuenta     : BEKURA (solo para el diagnóstico de tallas)
  IA         : deepseek
  ESTE SCRIPT NO ESCRIBE EN WOO, KUBERA, ODOO NI EN NINGÚN MARKETPLACE.

[1/3] EST-0054-NEG … OK (38.8s) — 22 atributos · «Escritorio Industrial Con Estantes…» · 3 aviso(s)
[2/3] TEC-0661-BLN … OK (24.1s) — 18 atributos · «Audífonos Inalámbricos…» · 2 aviso(s)
[3/3] ACC-0653-CHE-13-16 … ERROR (0.9s) — No existe ese SKU en WooCommerce…

Listo. 2 OK, 1 con error, 0 reusados de caché.
  2 SKU(s) con avisos · 1 con `flags` de la IA (datos que ella misma admitió no saber)
  JSON : …\salidas\contenido_ml_20260903_104402.json
  CSV  : …\salidas\contenido_ml_20260903_104402.csv

Nada de esto se aplicó. Para aplicarlo, el panel.
```

**Un SKU que truena no aborta el lote.** Se anota su error en la fila y sigue.

### 5 · Qué sale

**El CSV** (23 columnas, con BOM para que Excel no destroce los acentos):

| columna | qué trae |
|---|---|
| `sku`, `wc_id`, `estado_woo`, `url_woo`, `error` | de qué producto hablamos |
| `categoria_id`, `categoria_origen`, `categoria_ruta` | **en qué categoría va a salir y por qué** |
| `dominio`, `es_catalogo` | si ML te va a pisar el título |
| `titulo_propuesto`, `titulo_caracteres` | el título, y si se pasa de 60 |
| `descripcion_propuesta` | la descripción |
| `atributos_num`, `atributos` | los atributos ya validados, `ID: valor \| ID: valor` |
| `obligatorios_sin_llenar` | los que ML exige y quedaron vacíos |
| `atributos_descartados` | lo que la IA inventó y **no existe** en la categoría |
| `valores_fuera_de_lista` | valores que **no** están en la lista cerrada de ML |
| `flags_ia` | **lo que la IA admitió no saber** |
| `guia_tallas` | `DRESSES:Mujer → SIN GUÍA`, o `no aplica` |
| `avisos` | todo lo anterior, en prosa |
| `proveedor`, `modelo` | quién lo escribió |

**El JSON** trae lo mismo y además: la lista completa de atributos obligatorios
y opcionales de la categoría, cuáles de los opcionales alcanzó a ver la IA,
los atributos que propuso la primera llamada (y que se descartan a propósito,
igual que en producción), y los valores que el matcher del publicador va a
ajustar al vuelo.

Ejemplo real, EST-0054-NEG (categoría `MLM32652`, Libreros):

```
TITULO: Escritorio Industrial Con Estantes Y Cajones Ferrahome MDF   (58 chars)
ATRIBUTOS (22): BRAND=Ferrahome · MODEL=QD-CD1102 · HEIGHT=75 cm · WIDTH=130 cm
  · DEPTH=48 cm · REQUIRES_ASSEMBLY=Sí · INCLUDES_ASSEMBLY_MANUAL=Sí
  · COLOR=Marrón oscuro · MAIN_COLOR=Negro · FINISH=Mate · STYLE=Industrial …
obligatorios sin llenar: (ninguno)
AVISO: El producto tiene DOS categorías y son distintas: panel MLM32652 vs
       predictor MLM437180. Se publica con la del panel.
AVISO: La categoría tiene 24 atributos opcionales y a la IA solo se le muestran
       15 (ml_atributos.py:31). 9 nunca se le piden.
AVISO: Categoría DE CATÁLOGO (dominio MLM-BOOKCASES): al publicar, el payload
       usa `family_name` y OMITE el título.
```

### 6 · Aplicarlo

**Desde el panel.** Ese es el punto de todo esto: el panel registra quién lo
hizo. El archivo es una propuesta; la decisión lleva nombre.

---

## Todas las opciones

| Opción | Para qué |
|---|---|
| `SKU1 SKU2 …` | SKUs sueltos |
| `--archivo RUTA` | lista, un SKU por línea (`#` = comentario) |
| `--salidas RUTA` | dónde dejar los archivos. Default: `../salidas` |
| `--cuenta BEKURA\|SANCORFASHION` | **solo** cambia el diagnóstico de guía de tallas: una guía pertenece a UNA cuenta. Default `BEKURA` |
| `--categoria MLM…` | fuerza esa categoría para todos los SKUs. Para probar |
| `--sin-ia` | lee Woo y ML, arma el semáforo, **no gasta IA** |
| `--rehacer` | ignora lo ya generado y vuelve a pagarle a la IA |
| `--pausa SEGUNDOS` | espera entre SKUs |
| `--verificar` | comprueba llaves y red, y termina |

### Por qué **no** hay `--dry-run`

Una bandera `--dry-run` sirve para distinguir "ensayo" de "de verdad". **Aquí no
existe el "de verdad"**: el script no tiene ninguna ruta de código que escriba en
un sistema vivo. Poner la bandera sería fingir que hay un modo peligroso, y
quien la viera apagada supondría —con toda razón— que entonces el script **sí**
aplica cambios. No los aplica nunca.

`--sin-ia` es otra cosa: no evita escrituras (no hay ninguna), evita **el gasto**.

### Reanudable

Cada SKU terminado se guarda en `salidas/skus/<SKU>.json`. Si lo vuelves a
correr, se reusa ese archivo y **no se le vuelve a pagar a la IA**. Para
regenerar, `--rehacer`. Las corridas con `--sin-ia` **no** llenan esa caché, a
propósito: si no, la siguiente corrida normal reusaría una ficha sin contenido.

---

## Cómo leer el resultado sin creerle de más

Cinco cosas que este script te dice y que conviene entender:

1. **`flags_ia` es lo más valioso del archivo.** Son los datos que la IA misma
   admitió no poder determinar. **Producción los calcula y los tira a la
   basura** (`crear_producto.py:789`, `ia_generadores.py:399-405`). Un
   `ml_attr_VOLTAGE` o cualquier número con unidad que aparezca aquí merece
   verificación humana contra la ficha del proveedor.
   El caso conocido (`docs/TIKTOK_MANUAL.md:167-171`): la IA puso `"1.5V"` y en
   la MISMA respuesta anotó *"voltaje no confirmado en descripción"*. Pasó el
   validador porque el atributo es texto libre. **Un dato inventado no da error:
   se publica.**

2. **`valores_fuera_de_lista` predice un daño silencioso.** Si el atributo es
   obligatorio y tiene lista cerrada y el valor no matchea, al publicar el
   pipeline mete **el primer valor de la lista**
   (`vendor/ml_ready/attribute_mapper.py:737`). No es una alucinación de la IA:
   es el código eligiendo cualquier cosa para que ML no rechace el alta.

3. **La IA no ve la categoría completa.** Solo 15 atributos opcionales y 15
   valores por atributo (`ml_atributos.py:31, 123-125`), aunque la categoría
   tenga 24 y 51. El prompt le exige "ortografía EXACTA" de una lista que no le
   enseñaron entera. El aviso te dice cuántos quedaron fuera.

4. **`categoria_origen` importa más de lo que parece.** Si dice
   `panel (ml_categoria_id)`, hay una elección humana y es la que manda. Si dice
   `predictor/costeo` o `domain_discovery EN VIVO`, **nadie eligió esa
   categoría**: la adivinó un detector. Y la categoría no solo decide dónde
   aparece el producto: **decide la comisión, y por lo tanto el precio**.

5. **El título de 60 caracteres nadie lo valida en producción.** No existe un
   `ml_contenido.py`. El prompt lo pide y ya. Aquí se cuenta y sale como aviso.

---

## Lo que este script NO puede hacer, y no por falta de ganas

| Cosa | Por qué no |
|---|---|
| Que los atributos queden guardados en el producto | El único escritor es `crear_producto.py:977-982`, que hace `PUT` a la REST de Woo. Sin ese `PUT`, `publicar_ready.construir_prod` no ve nada |
| Que la categoría elegida persista | Solo la escribe `POST /api/crear/categoria-ml`, que también hace `PUT` a Woo |
| Publicar, actualizar o pausar en Mercado Libre | Es escritura en el marketplace |
| Registrar el contenido en `enrich.channel_content` | Es escritura en la BD kubera. `POST /api/ia/mejorar` sí lo hace; este script no |
| Dar de alta una guía de tallas | Se crea en el dashboard de ML de la cuenta **y** se registra en `size_chart_mapping.py`, que vive en `main` |

Lo que sí puedes sacar de aquí: **la lista exacta** de guías que hay que crear,
de valores que van a fallar y de datos que la IA se inventó — antes de gastar el
intento.

---

## Problemas comunes

| Síntoma | Qué pasa |
|---|---|
| `[FALTA CONFIGURACIÓN] … WC_URL, WC_CONSUMER_KEY…` | No hay `.env` o está incompleto. Copia `.env.ejemplo` |
| `[FALTA UNA LLAVE DE IA]` | Ni DeepSeek ni Anthropic. O configúralas, o corre con `--sin-ia` |
| `[FALTA UNA LIBRERÍA] … pip install anthropic` | Vas a usar Claude y falta el SDK oficial |
| `No existe ese SKU en WooCommerce` | El SKU está mal escrito, o es una variación cuyo SKU no coincide |
| `Sin categoría de Mercado Libre` | El producto no tiene categoría ni el predictor supo adivinar. Elígela en el picker del Estudio: sin ella no hay atributos ni comisión |
| `La respuesta de la IA se CORTÓ…` | El JSON quedó a medias por el tope de tokens. Casi siempre es una descripción de Woo larguísima |
| `Es un SKU PADRE (type=variable)` | En ML se publica la **variante**, nunca el padre |
| Todo lento | Cada SKU son 2 llamadas a la IA. Medido con Claude: ~40 s por SKU. DeepSeek suele ser más rápido |

Un pie de página que ahorra una tarde: **`chunche.shop` está en mantenimiento
(503) desde el 19-ago-2026**. La REST API de Woo —que es la que usa este
script— **no se ve afectada**. Si algo tuyo intenta leer la tienda por HTML, va
a fallar por una razón que no tiene nada que ver con esto.

---

## Qué se copió de producción, y por qué copiado y no importado

La regla de esta carpeta es **copiar**. Si importáramos `backend/`, esta carpeta
se rompería el día que producción cambie —y, peor, dependería de que producción
**no** cambie. Todo lo de abajo está copiado a mano del commit `1a7da7e`:

| De dónde | Qué |
|---|---|
| `backend/services/ml_atributos.py` | `MARCA`, `MAX_SECUNDARIAS`, `_SKIP_IDS`, el filtro de `get_meli_all_attributes`, `_fmt_attr_list`, **`build_prompt` literal**, el `system`, `_parse_json`, el bloque de validación |
| `backend/services/ia_generadores.py` | `_ML_TITULO`, `_NO_CONTRADECIR`, el `system` de `mejorar` para ML, `_contexto`, `_sin_html` |
| `backend/vendor/ml_ready/size_chart_mapping.py` | `CHARTS_BY_ACCOUNT` (15 guías) y `get_chart_id` |
| `backend/vendor/ml_ready/attribute_mapper.py` | `_find_value_id` (el matcher de 3 pasadas) y `_normalize` |

**Si algo aquí no coincide con `main`, gana `main`** y hay que re-extraer.

### Diferencias a propósito

1. Producción es asíncrona (httpx + asyncio). Aquí todo es **síncrono y con la
   biblioteca estándar**: un KAM no debería instalar nada para correr esto. (Si
   usa Claude, sí hace falta el SDK `anthropic`.)
2. Producción parsea los atributos con `json.loads` directo
   (`ml_atributos.py:241`); aquí pasan por `_parse_json`, que además tolera
   cercas ` ```json `. Más laxo, nunca más estricto.
3. **`max_tokens` con Claude**: producción usa 1500 para el contenido, calibrado
   para DeepSeek. Los modelos Claude actuales razonan antes de contestar y ese
   razonamiento cuenta contra `max_tokens`: con 1500 la respuesta salió cortada
   a media llave (medido el 2026-09-03 con EST-0054-NEG). Por eso aquí hay un
   piso de 8000 **solo para Claude**. El prompt es idéntico.
4. El modelo de Claude por omisión aquí es `claude-opus-5`; producción usa
   `claude-opus-4-8` (`ia_generadores.py:28`). Se calca poniendo
   `ANTHROPIC_MODEL=claude-opus-4-8` en el `.env`.

---

## Antes de dar por terminado cualquier cambio aquí

```bash
python conocimientoGeneral/verificar_aislamiento.py
```

Sale con código 1 si encuentra una escritura a producción, un secreto escrito o
un import desde `backend/`. Con este script dentro, sale limpio.

---

## Ver también

* [`../01_ATRIBUTOS_IA.md`](../01_ATRIBUTOS_IA.md) — cómo funcionan de verdad los
  atributos con IA: el prompt canónico, los dos truncamientos, las 13 trampas.
* [`../02_CONTENIDO_IA.md`](../02_CONTENIDO_IA.md) — generar vs. mejorar, todos
  los prompts literales, los límites por canal.
* [`../03_CATEGORIA_Y_TALLAS.md`](../03_CATEGORIA_Y_TALLAS.md) — los tres
  decisores de categoría y por qué faltan 24 dominios de guías de tallas.
* [`../04_PIPELINE_PUBLICAR.md`](../04_PIPELINE_PUBLICAR.md) — qué pasa después,
  cuando alguien aprieta Publicar.
* [`../05_PRECIO_Y_COSTO.md`](../05_PRECIO_Y_COSTO.md) — por qué cambiar la
  categoría cambia el precio.
