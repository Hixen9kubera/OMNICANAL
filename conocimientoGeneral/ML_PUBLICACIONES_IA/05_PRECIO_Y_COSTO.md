> Extraído de producción el 2026-09-03, commit 1a7da7e.
> ESTO ES UNA COPIA DE CONSULTA. La verdad vive en main; si algo no cuadra,
> gana main y hay que re-extraer.

# 05 · Cómo se calcula el precio de venta de una publicación de Mercado Libre

Este archivo se lee solo. No necesitas abrir el repo ni la base para entenderlo,
y al final hay una receta para calcular el precio de un SKU en una hoja de
cálculo sin tocar nada de producción.

Todas las referencias son `archivo:línea` del repo de producción
(`C:/Users/diaz2/OneDrive/Escritorio/omnicanal`). Lo que no verifiqué está
marcado como **[inferido]**.

---

## 0. La versión de 30 segundos

El precio de ML sale de **una sola fórmula** con **cuatro insumos**:

```
precio_sin_iva  = ( costo_unitario × (1 + MARGEN) + fee_envio ) / (1 − pct_comision)
precio_sugerido = precio_sin_iva × (1 + IVA)
precio_base     = precio_sugerido / (1 − DESCUENTO)
```

| Insumo | Valor | De dónde sale |
|---|---|---|
| `MARGEN` | **0.48** (48 %) | Constante del código, `backend/services/costos.py:41` |
| `IVA` | **0.16** (16 %) | Constante, `costos.py:42` |
| `DESCUENTO` | **0.16** (16 %) | Constante, `costos.py:43` |
| `pct_comision` | **varía por categoría** (0.08 – 0.28; la más común 0.15) | API de ML `listing_prices`, con caché en la BD |
| `costo_unitario` | por SKU | packing list del embarque: `precio_usd × 19 + cbm_por_pieza × 7500` |
| `fee_envio` | por peso y por precio | tabla oficial de Mercado Envíos, copiada íntegra en el código |

El motor entero vive en **`backend/services/costos.py`** (850 líneas). Todo lo
demás son adaptadores.

La versión de la fórmula queda sellada en cada fila que se guarda, en la columna
`costing.costos_finales.formula_ver`. Hoy dice, literalmente:

```
costos.py/v2-gold_pro-margen48-iva16-tarifaML202607
```

(`backend/services/costing_mirror.py:42`. Verificado leyendo filas reales de la
BD kubera el 2026-09-03.)

---

## 1. Los dos precios: `precio_sugerido` y `precio_base`

El motor produce **dos** números y es fácil confundirlos:

| Columna | Etiqueta en el panel | Qué es |
|---|---|---|
| `precio_sugerido` | "Precio oferta" | El que sale de la fórmula. El precio al que **quieres** vender. |
| `precio_base` | "Precio regular" | `precio_sugerido / 0.84` — el precio **tachado**, para que la oferta se vea como 16 % de descuento. |

`precio_base` es 100 % determinista: no lleva más información que
`precio_sugerido`. Es puro escaparate (`costos.py:43`, `costos.py:280`).

**Cuál se publica en ML.** El publicador toma el precio **REGULAR** de
WooCommerce, no el de oferta:

- `costos.recalcular()` → `routers/crear.py:687-688` escribe a Woo
  `regular_price = precio_base` y `sale_price = precio_sugerido`.
- `backend/services/publicar_ready.py:394` lee `_regular_price` (con respaldo a
  las variantes y, en último caso, a `_price`).

O sea: **lo que ve el comprador en ML al publicar es `precio_base`** (el alto),
y la oferta se aplica después en ML/Woo. Hay un comentario en
`publicar_ready.py:391-393` que explica por qué: un padre variable no guarda
`_regular_price` propio y caía al `_price` de OFERTA — CAM-0030 se publicó en
$6,514.97 en vez de $7,755.92.

---

## 2. De dónde sale el COSTO del producto

`costo_unitario = costo_producto + costo_cbm`

Vive en la tabla `costing.costos_validados` de la BD kubera (columnas
`costo_producto`, `costo_cbm`, `costo_total`). Ahí lo lee
`costos.costo_desde_validados()` (`costos.py:359-398`).

### 2.1 La fórmula del packing list (avalada por Brandon el 21-ago-2026)

Confirmada en el código, en `backend/services/packing_indice.py:501-560`:

```
flete    = cbm_por_pieza × 7,500 MXN/m³      ← tarifa FIJA
producto = precio_usd    × 19                ← tipo de cambio FIJO
costo    = producto + flete
```

Las dos constantes están juntas en
`backend/services/packing_publicados.py:81-82`:

```python
TARIFA_MXN_M3 = 7500.0     # flete por m³. FIJA, avalada por Brandon el 21-ago
TIPO_CAMBIO   = 19.0
```

Y `cbm_por_pieza` sale de emparejar **por caja**
(`backend/services/packing_indice.py:411-419`):

```
total_fila     = valor_total / precio_usd  →  cantidad_total  →  piezas × cajas
piezas_en_caja = total_fila / num_cajas          (num_cajas de la fila ANCLA)
piezas_grupo   = Σ piezas_en_caja del cartón compartido
cbm_por_pieza  = cbm_caja  / piezas_grupo
peso_pieza     = peso_caja / piezas_grupo
```

Es decir, la fórmula del enunciado —**`cbm_caja / piezas_grupo × 7500 + usd × 19`**—
**está confirmada en el código**, con un matiz importante: el denominador son las
piezas del **GRUPO que comparte cartón**, no las de un renglón.

### 2.2 La OTRA fórmula de costo (no la confundas)

Existe un segundo camino, el "Resolver" que carga un packing list completo:
`backend/services/packing_costos.py:358-442`. Ese **prorratea el flete real del
contenedor**:

```
costo_por_m3 = costo_contenedor / total_cbm      (default 525,000 MXN, packing_costos.py:40)
costo_cbm_pieza = cbm_por_pieza × costo_por_m3
```

El propio código explica la diferencia (`packing_indice.py:512-515`): con un
contenedor de ~70 m³ los dos dan casi lo mismo (525,000 / 70 = 7,500); con un
archivo parcial, no. El validador SKU-por-SKU usa la **tarifa fija de 7,500**
porque ahí solo hay uno o unos pocos SKUs y no existe el denominador global.

### 2.3 Y una TERCERA: `auto_cbm` desde las dimensiones

Cuando aprietas "Regenerar" en el panel de Costos con `auto_cbm` encendido
(que es el default del endpoint, `routers/crear.py:563`), el flete se re-deriva
**de las dimensiones de la pieza**, no del packing list
(`costos.py:76-92`, `costos.py:595-597`):

```python
TARIFA_CBM_M3 = 7500.0
volumen_m3     = (largo × ancho × alto) / 1_000_000     # cm → m³
costo_cbm      = volumen_m3 × 7500
```

Es la misma tarifa, pero el volumen sale de las dims redondeadas a 2 decimales,
así que **da un número ligeramente distinto** al del packing list. Ejemplo real
(medido, ver §8): ACC-0353-NEG-M tiene `costo_cbm = 2.6136` (packing list); con
sus dims guardadas 8.57 × 6.39 × 6.39 el auto_cbm daría 2.6243. Diferencia
0.4 %. **Ese es exactamente el tipo de pisón que el candado de COSTO VALIDADO
existe para impedir** (§5).

### 2.4 Si escribes el costo a mano

En el Estudio hay un campo "Costo". Lo que teclees ahí se guarda como
`costo_producto` y el flete se pone en **cero**, para que el total mostrado sea
exactamente el que escribiste (`costos.py:600-612`). Para desglosar producto vs
flete está el bloque COSTOS (costo USD + dimensiones), no ese campo.

---

## 3. La comisión de Mercado Libre (Premium / `gold_pro`)

### 3.1 De dónde sale el número

**No es una constante.** Es un porcentaje por CATEGORÍA que se le pregunta a ML.
`costos.pct_comision_ml()` (`costos.py:185-224`) hace **una** llamada:

```
GET https://api.mercadolibre.com/sites/MLM/listing_prices
    ?price=100
    &category_id=<MLM…>
    &listing_type_id=gold_pro
    &logistic_type=xd_drop_off
    &shipping_mode=me2
    &dimensions=<alto>x<ancho>x<largo>,<peso_gramos>
```

Y se queda con `sale_fee_details.percentage_fee`, dividido entre 100
(`costos.py:216-218`). Un precio de referencia de $100 basta porque la comisión
es fija por categoría/tipo de publicación (`PRECIO_REFERENCIA = 100.0`,
`costos.py:44`).

### 3.2 Dónde está el "Premium"

En esta línea, y es lo único que lo hace Premium
(`costos.py:101`, con el comentario de arriba en `costos.py:97-100`):

```python
DEFAULT_LISTING_TYPE = "gold_pro"
```

> "TODO el catálogo se migró a Premium el 2026-07-16 y el publicador vendorizado
> publica gold_pro. Calcular comisiones con gold_special subestimaría el fee
> ~4.5 puntos (ej. 15% vs 19.5%) y el precio sugerido saldría con margen de
> menos."

También la cuenta por default es `BEKURA` (`costos.py:96`) y el envío se pide
como `me2` / `xd_drop_off` (`costos.py:102-103`).

### 3.3 Qué valores hay realmente (medido)

Consulta a `costing.costos_finales` (canal `mercado_libre`) el 2026-09-03,
4,650 filas:

| pct_comision | SKUs |
|---|---|
| 0.1500 | 2,588 |
| 0.1300 | 317 |
| 0.1000 | 272 |
| 0.1350 | 230 |
| 0.1200 | 183 |
| 0.1950 | 171 |
| 0.1800 | 91 |
| 0.1400 | 91 |
| 0.1450 | 34 |
| 0.1600 | 29 |
| 0.1650 | 28 |
| 0.0900 | 14 |
| resto (0.08, 0.11, 0.115, 0.17, 0.175, 0.185, 0.205, 0.28) | ≤ 12 c/u |

**Moraleja para el KAM: 15 % es la apuesta razonable si no tienes el dato, pero
no es "la comisión de ML".** El rango real va de 8 % a 28 %.

### 3.4 El orden de prioridad (y qué pasa si falla el token)

`costos.calcular_pricing()` (`costos.py:256-266`) busca la comisión en este
orden:

1. **`pct_override`** — el campo "Comisión ML (%)" escrito a mano en el panel.
2. **API de ML en vivo** (`pct_comision_ml`). Si devuelve 401, refresca el token
   y reintenta una vez (`costos.py:219-222`).
3. **Caché por categoría**: la comisión **más frecuente** ya guardada para ese
   `ml_cat_id` en `costing.costos_finales` (`costos._comision_categoria_db`,
   `costos.py:47-74`). En este caso la respuesta viene marcada
   `comision_estimada = True`.
4. **Nada.** `calcular_pricing` devuelve `None`. **No se inventa un porcentaje.**

---

## 4. El margen, el IVA y el descuento

```python
MARGEN_DEFAULT   = 0.48   # costos.py:41
IVA_RATE         = 0.16   # costos.py:42
DESCUENTO_BASE   = 0.16   # costos.py:43
```

**Cómo entra el margen.** No es "precio × 1.48". El margen se aplica **solo al
costo del producto**, ANTES de dividir por la comisión y de sumar el IVA
(`costos.calc_precio_sugerido`, `costos.py:174-180`):

```python
numerador      = costo * (1.0 + margen) + fee_envio
precio_sin_iva = numerador / (1.0 - pct)
precio         = round(precio_sin_iva * (1.0 + iva), 2)
```

Dividir entre `(1 − pct)` en vez de multiplicar por `(1 + pct)` es lo que hace
que la comisión salga **completa** del precio final y no se coma el margen.

Consecuencia verificable: por construcción,
`ganancia_neta / costo_unitario ≈ MARGEN`. En el ejemplo del §8 sale
**0.4800**.

**El margen se puede cambiar por petición.** El endpoint acepta `margen` y su
default es la constante (`routers/crear.py:561`, `routers/crear.py:992`). No hay
variable de entorno: para moverlo permanentemente hay que tocar el código.

**El precio a mano manda sobre todo.** Si escribes "Precio regular" o "Precio
oferta" en el Estudio, `costos.aplicar_precio_manual()` (`costos.py:302-354`)
pisa el precio derivado y **rehace el desglose hacia atrás** (comisión, IVA,
ganancia, ROI), y re-evalúa el fee de envío porque en ML depende del precio. Con
dar uno de los dos basta: el otro sale del mismo 16 %.

---

## 5. El flete / envío (`fee_envio`)

### 5.1 Cuándo se incluye

Se incluye **siempre** que `incluir_envio` sea `True`, que es el default en
todos los caminos (`costos.computar`, `costos.asegurar_finales`, y el endpoint
`routers/crear.py:560`). Si se apaga, `fee_envio = 0.0` (`costos.py:276-277`).

No hay lógica de "envío gratis arriba de $299" en el motor: **el fee entra
siempre, para cualquier precio.** [inferido: la política real de ML de envío
gratis no está modelada aquí]

### 5.2 Cómo se elige el número

Es un **lookup directo en la tabla oficial de Mercado Envíos MX (2026-07)**, no
una aproximación (`costos.py:105-171`). Dos ejes:

**Eje 1 — peso efectivo** = `max(peso_real, peso_volumétrico)`, con
`peso_volumétrico = (largo × ancho × alto) / 5000` en cm→kg
(`costos._peso_efectivo`, `costos.py:229-238`).
Si el peso viene en 0, se usa **0.5 kg** por default.
Si no hay dimensiones, el peso volumétrico es 0 y se manda `10x10x10` a ML.

**Eje 2 — tramo de PRECIO del producto**, seis columnas:

| Col | Tramo |
|---|---|
| 0 | $0 – $98.99 |
| 1 | $99 – $198.99 |
| 2 | $199 – $298.99 |
| 3 | $299 – $498.99 |
| 4 | $499 – $998.99 |
| 5 | desde $999 |

**El huevo y la gallina**: la columna depende del precio y el precio depende del
fee. Se resuelve **iterando**: se siembra con un precio de $400 y se itera hasta
8 veces hasta que el fee deja de cambiar (`costos.py:268-274`).

### 5.3 La tabla completa (copiada de `costos.py:111-143`)

Peso ≤ (kg) → costo por columna de precio [0 · 1 · 2 · 3 · 4 · 5]

| ≤ kg | $0–98.99 | $99–198.99 | $199–298.99 | $299–498.99 | $499–998.99 | ≥$999 |
|---|---|---|---|---|---|---|
| 0.3 | 25.00 | 32.00 | 35.00 | 52.40 | 65.50 | 65.50 |
| 0.5 | 28.50 | 34.00 | 38.00 | 56.00 | 70.00 | 70.00 |
| 1.0 | 33.00 | 38.00 | 39.00 | 59.60 | 74.50 | 74.50 |
| 2.0 | 35.00 | 40.00 | 41.00 | 67.60 | 84.50 | 84.50 |
| 3.0 | 37.00 | 46.00 | 48.00 | 76.00 | 88.50 | 95.00 |
| 4.0 | 39.00 | 50.00 | 54.00 | 82.40 | 95.50 | 103.00 |
| 5.0 | 40.00 | 53.00 | 59.00 | 88.00 | 102.50 | 110.00 |
| 7.0 | 45.00 | 59.00 | 70.00 | 98.00 | 122.50 | 122.50 |
| 9.0 | 51.00 | 67.00 | 81.00 | 111.60 | 139.50 | 139.50 |
| 12.0 | 59.00 | 78.00 | 96.00 | 129.20 | 161.50 | 161.50 |
| 15.0 | 69.00 | 92.00 | 113.00 | 152.00 | 190.00 | 190.00 |
| 20.0 | 81.00 | 108.00 | 140.00 | 178.00 | 222.50 | 222.50 |
| 30.0 | 102.00 | 137.00 | 195.00 | 225.20 | 281.50 | 281.50 |
| 40.0 | 126.00 | 170.00 | 250.00 | 279.20 | 349.00 | 349.00 |
| 50.0 | 163.00 | 220.00 | 305.00 | 361.20 | 451.50 | 451.50 |
| 60.0 | 183.00 | 247.00 | 334.00 | 405.60 | 507.00 | 507.00 |
| 70.0 | 188.00 | 254.00 | 363.00 | 416.40 | 520.50 | 520.50 |
| 80.0 | 196.00 | 264.00 | 392.00 | 433.60 | 542.00 | 542.00 |
| 90.0 | 220.00 | 297.00 | 421.00 | 487.60 | 609.50 | 609.50 |
| 100.0 | 254.00 | 343.00 | 450.00 | 562.40 | 703.00 | 703.00 |
| 125.0 | 288.00 | 389.00 | 523.00 | 637.20 | 796.50 | 796.50 |
| 150.0 | 382.00 | 516.00 | 694.00 | 846.00 | 1057.50 | 1057.50 |
| 175.0 | 476.00 | 643.00 | 865.00 | 1054.80 | 1318.50 | 1318.50 |
| 200.0 | 570.00 | 770.00 | 1036.00 | 1263.60 | 1579.50 | 1579.50 |
| 225.0 | 664.00 | 897.00 | 1207.00 | 1472.40 | 1840.50 | 1840.50 |
| 250.0 | 758.00 | 1024.00 | 1378.00 | 1681.20 | 2101.50 | 2101.50 |
| 275.0 | 852.00 | 1151.00 | 1549.00 | 1890.00 | 2362.50 | 2362.50 |
| 300.0 | 946.00 | 1278.00 | 1720.00 | 2098.40 | 2623.00 | 2623.00 |
| 325.0 | 1040.00 | 1406.00 | 1892.00 | 2308.00 | 2885.00 | 2885.00 |
| 350.0 | 1134.00 | 1533.00 | 2063.00 | 2516.80 | 3146.00 | 3146.00 |

Más de 350 kg: se repite la última fila (`costos.py:142`, `costos.py:153`).

### 5.4 ⚠️ Este fee MIENTE, y está documentado

`backend/services/envio_real.py:1-20` (fase 0 de "Márgenes en Análisis", Eduardo,
6-ago-2026):

> "El envío estimado (`costing.costos_finales.costo_fee_envio`) demostró mentir
> en las dos direcciones: el peso del packing list mezcla unidades (pieza / caja
> master / total del renglón), así que a Malla Sombra le inventaba una pérdida de
> $200k (fee $349 con peso de caja) y a 141 SKUs con venta les puso el flete en
> $0, inflando el top de márgenes."

El número **real** existe: `GET /shipments/{id}/costs` devuelve lo que ML le
cobró al vendedor por ese embarque. Verificado contra embarques reales: $88.00
(SANCOR) y $82.40 (BEKURA) por la malla que el estimado ponía en $349.

**Para el KAM:** el `costo_fee_envio` de la tabla sirve para **fijar** el precio;
no lo uses para juzgar el margen de una venta ya ocurrida. Para eso está el
envío real de `ml_envio_real` / el tab de Análisis.

Medido el 2026-09-03: 4 SKUs de 4,650 tienen `costo_fee_envio = 0`.

---

## 6. La marca **COSTO VALIDADO** (`revisado_at`)

### 6.1 Qué es

Una columna en `costing.costos_validados`: `revisado_at` (timestamp) +
`revisado_por` (correo). Significa: *este costo se reconstruyó a mano desde el
packing list, con su renglón verificado por imagen, y no debe moverse.*

Medido el 2026-09-03: **53 SKUs marcados** de 15,844 filas de costo. Es una
marca joven y escasa.

### 6.2 Qué CONGELA — y qué NO

**Congela: el COSTO. Nada más.**

El candado no vive en Python: vive **dentro del SQL del UPSERT**
(`backend/services/costing_mirror.py:96-134`). La cláusula literal es:

```sql
on conflict (sku) do update set
  largo = excluded.largo, alto = excluded.alto, ancho = excluded.ancho,
  peso = excluded.peso, costo_producto = excluded.costo_producto,
  costo_cbm = excluded.costo_cbm, costo_total = excluded.costo_total
where costos_validados.revisado_at is null      -- ← EL CANDADO
  and (…) is distinct from (…)
```

Y el comentario de arriba explica por qué está ahí y no en el llamador
(`costing_mirror.py:108-111`):

> "La guarda vive AQUI, en el SQL, y no en quien llama: por esta funcion pasan el
> espejo, el corte F6 y el reproceso de errores, y basta que uno se olvide para
> perder el dato."

**NO congela el precio.** El UPSERT de `costing.costos_finales`
(`costing_mirror.py:153-195`) **no tiene ninguna condición sobre `revisado_at`**.
Verificado leyendo el SQL completo. Y `costos._preparar_base` lo dice explícito
(`costos.py:575-577`):

> "CANDADO: con el costo validado, lo guardado manda sobre los overrides y sobre
> el auto_cbm. El precio SI se sigue recalculando -- lo que se congela es el
> COSTO, no el margen ni la comision."

Es decir, con un SKU validado:

| Cosa | ¿Se puede mover? |
|---|---|
| `costo_producto`, `costo_cbm`, `costo_total` | **NO** (el UPSERT las ignora) |
| `largo`, `ancho`, `alto`, `peso` | **NO** (van en el mismo UPSERT) |
| `pct_comision` | **SÍ** — se vuelve a consultar a ML |
| `costo_fee_envio` | **SÍ** — se recalcula |
| `precio_sugerido`, `precio_base` | **SÍ** — se recalculan y se escriben |

Congelar el costo pero no el precio es **deliberado**: la comisión de ML y la
tarifa de envío cambian solos, y el precio tiene que seguirlos.

### 6.3 Cómo se pone y se quita

- Poner: `POST /api/crear/costos/{sku}/revisar` →
  `costing_write.marcar_revisado(sku, True)` (`costing_write.py:134-172`).
  Escribe `revisado_at = now()` y `revisado_por` = el correo real del usuario,
  leído de `current_setting('app.usuario')`.
- Quitar: `DELETE /api/crear/costos/{sku}/revisar` (`routers/crear.py:774-780`)
  o, en SQL, `revisado_at = null`. **No hay estado oculto**
  (`costing_mirror.py:113`).
- La marca solo existe en kubera. La `costos_validados` de MySQL quedó congelada
  el 13-ago y no tiene esas columnas (`costing_write.py:138-142`).

### 6.4 El estado "movido"

El listado de Costos ofrece un filtro `revisado=movido`:
`revisado_at is not null and updated_at > revisado_at`
(`costing_read.py:148-149`). Son los que se marcaron y después alguien tocó la
fila. **No es un error, es un aviso de que hay que volver a mirarlos.**

### 6.5 Un detalle del código que conviene saber

En el camino de "Regenerar" (`costos.recalcular`, `costos.py:804-849`) hay dos
guardas de Python que **no llegan a dispararse**, porque la bandera interna
`_bloqueado` no viaja en el diccionario que devuelve `costos.computar()` (que
expone la llave como `costo_bloqueado`, `costos.py:749`, no como `_bloqueado`):

- `costos.py:844` → `if calc.get("_bloqueado")` nunca es verdad, así que el aviso
  *"El COSTO de este SKU esta validado y no se movio"* de
  `routers/crear.py:823-824` **no se muestra**.
- `costos.py:467` → el atajo de `_guardar_validados` tampoco corta, así que la
  escritura **sí viaja** a la base… y ahí la bloquea el SQL.

**Consecuencia práctica:** el costo SÍ queda protegido (el candado del §6.2 hace
su trabajo), pero el panel no te avisa que lo estuvo. Si regeneras un SKU
validado y el costo "no cambia", eso es el candado funcionando, no un bug.
*(Hallazgo de lectura de código el 2026-09-03; no lo ejecuté contra el panel.)*

---

## 7. Cuando falta la categoría: **no hay comisión → no hay precio**

Esta es la causa nº 1 de "el SKU no tiene precio".

### 7.1 La cadena

`costos.calcular_pricing()` devuelve `None` si no consiguió comisión
(`costos.py:265-266`), y sin categoría la API de ML no puede darla. Entonces:

- `costos.computar()` devuelve `None` (`costos.py:732-733`).
- `costos.recalcular()` **guarda el COSTO igual** y devuelve
  `{"sin_precio": True, "motivo_sin_precio": "el producto no tiene categoría ML
  asignada"}` (`costos.py:816-834`).
- El endpoint responde con `ok: True` y el aviso *"Costo guardado. NO se calculó
  el precio: … Asígnale la categoría y vuelve a guardar para derivarlo."*
  (`routers/crear.py:797-802`).
- `costos.asegurar_finales()` simplemente se rinde con un log
  (`costos.py:784-786`).

El comentario del código es explícito (`costos.py:816-823`):

> "sin comisión (casi siempre: sin categoría ML asignada) → el COSTO sí existe y
> se puede registrar; lo único que no se puede es DERIVAR el precio (y aquí no se
> inventa un %)."

### 7.2 Dónde se busca la categoría, en orden

`costos._resolver_cat_ml()` (`costos.py:680-704`):

1. **Override** del panel (`ml_cat_id` en la petición).
2. La que ya está en `costing.costos_finales.ml_cat_id`.
3. **Mapa `channel.product_category` de kubera** (`costos._cat_ml_kubera`,
   `costos.py:617-633`) — la elección humana, la que **manda** (regla 2 de la
   casa).
4. Postmeta `ml_category_id` de WooCommerce (vía `wc_id` de `core.products`).
5. **La categoría del PADRE**, si el SKU es una variante. Sin esta herencia el
   costeo se caía con 422 en cuanto una variante no tenía categoría propia (caso
   real citado en el código: `CAM-0030-IND` / `-QUE`, colchones por talla).

Nota histórica que vale oro (`costos.py:640-649`): antes se preguntaba primero a
la tabla `categorias_ml` de MySQL, y **2,270 SKUs de 12,399 tenían ahí una
categoría DISTINTA** a la de `channel.product_category`. Para esos, la comisión
—y por lo tanto el precio— se calculaba con la categoría equivocada.

### 7.3 Cuánto pesa hoy (medido el 2026-09-03)

En `costing.costos_finales`, canal `mercado_libre`:

| | filas | sin precio |
|---|---|---|
| Con `ml_cat_id` | 4,091 | 1 |
| **Sin `ml_cat_id`** | **559** | **243** |

O sea: **el 43 % de los SKUs sin categoría no tienen precio**, contra el 0.02 %
de los que sí la tienen. La correlación es tan fuerte que basta mirar la
categoría para saber por qué falta el precio.

---

## 8. Ejemplo numérico REAL, de punta a punta

**SKU: `ACC-0353-NEG-M`** (existe hoy en producción; datos leídos de la BD kubera
el 2026-09-03 con `SELECT`).

### Lo que hay guardado

`costing.costos_validados`:

| campo | valor |
|---|---|
| contenedor | `TXGU7518788 - 49` |
| largo × ancho × alto (pieza, cm) | 8.57 × 6.39 × 6.39 |
| peso (kg) | 0.081 |
| costo_producto | 76.0000 |
| costo_cbm | 2.6136 |
| costo_total | 78.6136 |
| revisado_at | 2026-09-02 20:56:16 UTC |
| revisado_por | andrea.pardo@kubera.mx |

`costing.caja_compartida` (la procedencia — de qué renglón salió el número):

| campo | valor |
|---|---|
| archivo | `TXGU7518788 Lista de empaque.xlsx` |
| renglones | 222, 223, 225 |
| piezas_grupo | 264 |
| cbm_grupo | 0.091872 m³ |
| cbm_origen | `caja_compartida` |

`costing.costos_finales` (canal `mercado_libre`):

| campo | valor |
|---|---|
| ml_cat_id | `MLM126136` |
| pct_comision | 0.1950 |
| costo_unitario | 78.61 |
| costo_fee_envio | 35.00 |
| costo_comision | 36.66 |
| **precio_sugerido** | **218.08** |
| **precio_base** | **259.62** |
| formula_ver | `costos.py/v2-gold_pro-margen48-iva16-tarifaML202607` |

### Paso 1 — el costo, desde el packing list

Un cartón de ~0.092 m³ compartido por 264 piezas (renglones 222/223/225):

```
cbm_por_pieza = 0.092 m³ / 264 piezas      = 0.00034848 m³
flete         = 0.00034848 × 7,500 MXN/m³  = 2.6136
producto      = 4.00 USD × 19              = 76.00
costo_total   = 76.00 + 2.6136             = 78.6136
```

*(El `cbm_grupo = 0.091872` de la tabla es `cbm_por_pieza` redondeado a 6
decimales × 264; el flete se calculó con el valor sin redondear. De ahí la
diferencia en el 4º decimal.)*

**El motor de precio usa `78.61`**, no `78.6136`:
`costos.py:598-599` redondea `costo_producto + costo_cbm` a 2 decimales.

### Paso 2 — la comisión

Categoría `MLM126136`, `listing_type_id = gold_pro` → **pct = 0.1950 (19.5 %)**.

### Paso 3 — el fee de envío

```
peso_volumétrico = 8.57 × 6.39 × 6.39 / 5000 = 0.0700 kg
peso_efectivo    = max(0.081, 0.0700)        = 0.081 kg   → fila "≤ 0.3 kg"
precio estimado 218.08                       → columna "$199–298.99" (col 2)
fee_envio        = 35.00                                       ✅ coincide con la BD
```

### Paso 4 — el precio

```
numerador       = 78.61 × 1.48 + 35.00 = 116.3428 + 35.00 = 151.3428
precio_sin_iva  = 151.3428 / (1 − 0.195) = 151.3428 / 0.805 = 188.0035
precio_sugerido = 188.0035 × 1.16 = 218.0840 → 218.08        ✅ coincide con la BD
precio_base     = 218.08 / (1 − 0.16) = 218.08 / 0.84 = 259.6190 → 259.62  ✅
```

### Paso 5 — el desglose (lo que muestra el panel)

```
costo_comision = (218.08 / 1.16) × 0.195 = 188.00 × 0.195 = 36.66   ✅
iva_mnt        = 218.08 − 188.00                          = 30.08
ganancia_neta  = 218.08 − 36.66 − 35.00 − 30.08 − 78.61   = 37.73
roi            = 37.73 / 78.61                            = 0.4800  ← = el MARGEN
```

Que el ROI dé exactamente 0.48 no es coincidencia: es la fórmula construida para
que el margen sobreviva a la comisión, al IVA y al flete.

### Otro ejemplo para contrastar (mismo día, misma BD)

`TEC-0792-NEG` — contenedor `PCIU9532241 - 56`, cat `MLM162997`, pct **0.15**,
costo 106.58, dims 11.67 × 44 × 11, peso 3.1 kg.

```
peso_vol  = 11.67 × 44 × 11 / 5000 = 1.13 kg  →  peso_efectivo = 3.1 kg  → fila "≤ 4.0"
precio ≈ 327  → columna "$299–498.99" (col 3) → fee = 82.40              ✅
(106.58 × 1.48 + 82.40) / 0.85 = 240.1384 / 0.85 = 282.5158
282.5158 × 1.16 = 327.7183 → 327.72                                      ✅
327.72 / 0.84  = 390.14                                                  ✅
```

---

## 9. Dónde vive cada número

| Dato | Tabla / lugar | Notas |
|---|---|---|
| costo base + dims + peso | `costing.costos_validados` (BD kubera) | PK `sku`. Aquí vive `revisado_at`. |
| precio + comisión + fee | `costing.costos_finales` (BD kubera) | **PK `(sku, canal)`** — hoy todo es `canal='mercado_libre'`. Toda consulta nueva DEBE filtrar canal. |
| procedencia del costo | `costing.caja_compartida` | archivo, renglones exactos, piezas_grupo, cbm_grupo. Llave `(sku, contenedor_base)`. |
| bitácora | `ops.process_log` (`proceso='costos'`) | Espejo viejo: `costos_logs` en MySQL. |
| precio en la tienda | WooCommerce `_regular_price` / `_sale_price` | `regular_price = precio_base`, `sale_price = precio_sugerido`. |
| precio vivo en ML | `channel.listings.price_sale` / `price_base` | Es lo que ML **cobra hoy**, no lo que calculamos. |

⚠️ **Las tablas `costos_validados` / `costos_finales` de MySQL están CONGELADAS
desde el 13-ago-2026.** Leerlas devuelve datos de agosto. La verdad es kubera.

---

## 10. Trampas medidas (no teóricas)

1. **El fee de envío guardado miente en las dos direcciones** (§5.4). $349 donde
   lo real eran $88. Sirve para fijar precio, no para auditar margen.
2. **Comisión ≠ 15 % fijo.** Va de 8 % a 28 % según categoría (§3.3).
3. **Categoría equivocada = precio equivocado.** 2,270 SKUs tenían dos
   categorías distintas en dos tablas (`costos.py:640-649`).
4. **73 SKUs tienen el peso de la CAJA MASTER capturado como pieza**
   (`costos.py:550-558`). Caso citado: `mue-0064`, 12×10×10 cm y **224 kg** =
   185 kg/L. Con ese peso el fee de envío se dispara y el precio sale inflado.
   Se dejaron a propósito **sin peso** (el panel los marca en ámbar) porque *un
   dato ausente y señalado es mejor que uno falso e invisible.*
5. **Un packing list sin columna de precio NO produce costo cero.** Si no hay
   `precio_usd` ni costo guardado en kubera, la fila sale **incompleta** con su
   motivo (`packing_indice.py:546-552`). El comentario explica el porqué: un
   costo que es puro flete es *"un precio de venta por debajo de lo que costó la
   mercancía"* — y encima se aprobaba solo y quedaba con candado.
6. **`packing_comparador.guardar()` escribe a la MySQL congelada.** No la uses.
   `packing_publicados.py:42-44` lo llama *"el peor modo de fallo posible,
   silencioso"*.
7. **Las dimensiones de la pieza son una ESTIMACIÓN** cuando vienen del packing
   list: ≤10 piezas por caja → se divide el lado más largo; >10 → raíz cúbica
   sobre los tres lados (`packing_costos.dims_pieza`, `packing_costos.py:63-100`).
   El volumen se conserva exacto; la forma no. Y la forma es lo que determina el
   peso volumétrico y por lo tanto el fee.
8. **El fee se resuelve iterando** porque depende del precio que depende del fee.
   Un SKU justo en la frontera de un tramo (p. ej. $298.99 vs $299.00) puede
   saltar de columna y cambiar el precio final.

---

## CÓMO REUSARLO SIN TOCAR PRODUCCIÓN

Objetivo: **calcular el precio de un SKU en una hoja de cálculo, sin escribir
nada en ningún lado.**

### Lo que necesitas juntar (4 números)

| # | Dato | Cómo lo consigues sin escribir |
|---|---|---|
| 1 | `costo_unitario` (MXN) | Panel → **Costos** → busca el SKU → columna "Costo total". O `SELECT costo_total FROM costing.costos_validados WHERE sku='…'`. |
| 2 | `pct_comision` (decimal) | Panel → cajón del producto → "Comisión ML (%)" ÷ 100. O `SELECT pct_comision FROM costing.costos_finales WHERE sku='…' AND canal='mercado_libre'`. Si no hay: **15 % es la apuesta razonable**, pero es una apuesta. |
| 3 | `peso` (kg) y `largo/ancho/alto` (cm) | Misma fila de `costos_validados`, o el panel de Costos. |
| 4 | `MARGEN` | **0.48**, salvo que te digan otra cosa. |

### La receta (5 pasos, hazla en la hoja)

```
① peso_efectivo = MAX( peso_kg ; largo × ancho × alto / 5000 )
                   (si peso_kg = 0, usa 0.5)

② fee_envio    = busca en la TABLA del §5.3
                 fila  = primer "≤ kg" que sea ≥ peso_efectivo
                 col   = tramo del precio → arranca suponiendo col 3 ($299–498.99)
                         y REPITE los pasos ②–③ hasta que la columna no cambie

③ precio_sin_iva  = ( costo_unitario × 1.48 + fee_envio ) / ( 1 − pct_comision )

④ precio_sugerido = REDONDEAR( precio_sin_iva × 1.16 ; 2 )      ← "Precio oferta"

⑤ precio_base     = REDONDEAR( precio_sugerido / 0.84 ; 2 )     ← "Precio regular"
                                                                   (el que se publica)
```

Comprobación de que no te equivocaste:

```
ganancia_neta = precio_sugerido
              − (precio_sugerido / 1.16) × pct_comision     (comisión)
              − fee_envio
              − (precio_sugerido − precio_sugerido / 1.16)  (IVA)
              − costo_unitario

ganancia_neta / costo_unitario  debe dar ≈ 0.48
```

Si no da 0.48, te equivocaste en algún paso.

### Ejemplo verificado, para copiar en la hoja

**`ACC-0353-NEG-M`** — costo 78.61 · comisión 19.5 % · peso 0.081 kg ·
8.57 × 6.39 × 6.39 cm

| Paso | Cuenta | Resultado |
|---|---|---|
| ① | `MAX(0.081 ; 8.57×6.39×6.39/5000)` = `MAX(0.081 ; 0.0700)` | **0.081 kg** → fila "≤ 0.3" |
| ② | precio ≈ 218 → columna "$199–298.99" | **fee = 35.00** |
| ③ | `(78.61 × 1.48 + 35.00) / (1 − 0.195)` = `151.3428 / 0.805` | **188.00** |
| ④ | `188.0035 × 1.16` | **218.08** ← precio oferta |
| ⑤ | `218.08 / 0.84` | **259.62** ← precio regular, el que va a ML |

Comprobación: comisión `188.00 × 0.195 = 36.66` · IVA `30.08` ·
ganancia `218.08 − 36.66 − 35.00 − 30.08 − 78.61 = 37.73` ·
`37.73 / 78.61 = 0.48` ✅

**Los cinco números coinciden al centavo con lo que hay guardado en producción
hoy.** (Verificado con `SELECT` el 2026-09-03.)

### Fórmula lista para pegar en Excel / Google Sheets

Con `A1 = costo_unitario`, `B1 = pct_comision` (decimal), `C1 = fee_envio`:

```
Precio oferta   =REDONDEAR( (A1*1.48 + C1) / (1-B1) * 1.16 ; 2 )
Precio regular  =REDONDEAR( (A1*1.48 + C1) / (1-B1) * 1.16 / 0.84 ; 2 )
Comisión $      =REDONDEAR( ((A1*1.48+C1)/(1-B1)) * B1 ; 2 )
Ganancia neta   =REDONDEAR( (A1*1.48+C1)/(1-B1) - ((A1*1.48+C1)/(1-B1))*B1 - C1 - A1 ; 2 )
```

### Lo que NO puedes hacer desde una hoja

- **Consultar la comisión real de una categoría nueva.** Eso exige el token de ML
  y una llamada a `/sites/MLM/listing_prices`. Sin token, el sistema usa la
  comisión histórica más frecuente de esa categoría y marca
  `comision_estimada = True`.
- **Saber el envío REAL de una venta ya hecha.** El fee de la tabla es el
  estimado (§5.4).
- **Cambiar el margen del 48 % de forma permanente.** No hay variable de entorno:
  está en el código (`costos.py:41`).
- **Poner o quitar COSTO VALIDADO.** Es una escritura a producción (§6.3).

### Reglas de seguridad si abres la BD

- Contra kubera/Supabase: **SOLO `SELECT`**.
- **NUNCA** marques la SESIÓN como read-only (`set_session(readonly=True)` ni
  `SET SESSION … READ ONLY`). El DSN apunta al pooler en modo transacción (6543)
  y las conexiones **se comparten**: envenenarla tumba la escritura de pedidos en
  producción. Ya reventó dos veces. Si necesitas la garantía, hazla **por
  transacción**: `BEGIN; SET TRANSACTION READ ONLY; …; ROLLBACK;`
- Filtra siempre `canal = 'mercado_libre'` en `costing.costos_finales`: su PK es
  `(sku, canal)`.

---

## Preguntas que este archivo NO responde

1. Por qué el margen es 48 % y no otro número — no hay justificación en el
   código, solo la constante.
2. Si ML aplica envío gratis arriba de cierto precio y si eso debería restarse
   del fee. El motor no lo modela.
3. Qué pasa con el precio cuando el canal no es ML (Amazon, Temu, TikTok,
   Walmart). `costos_finales` está preparada por canal, pero **hoy solo se
   calcula `mercado_libre`** (`costing_mirror.py:167-169`).
4. Si el aviso perdido de "costo validado, no se movió" (§6.5) alguna vez se ve
   en el panel por otro camino. No lo probé contra la UI.
