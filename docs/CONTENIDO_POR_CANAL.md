# Contenido y requisitos por canal — dónde vive cada cosa

> **Para quién:** cualquier agente o persona que vaya a tocar la publicación
> multicanal. Escrito para leerse sin el historial de la conversación.
> **Estado:** aplicado en producción (v0.108.0 → v0.115.1, 12-ago-2026).
> **Método:** todo lo que dice está levantado del código o medido contra la base.
> Donde no se pudo verificar, se dice.

---

## LA RESPUESTA CORTA

**JSON dentro de tablas.** No es un punto medio: son dos preguntas distintas y
cada una tiene ganador claro.

| Qué | Dónde | Forma | Por qué |
|---|---|---|---|
| El **contenido** de un producto en un canal | `enrich.channel_content.contenido` | **`jsonb`** | La forma la decide cada canal y CAMBIA con ellos |
| Lo que el canal **exige** | `channel.field_requirements` | **relacional** | Se consulta, se compara y se cuenta |
| Lo que **se mandó** | `ops.channel_submissions` | **relacional** | Bitácora: una fila por intento |

El contenido es `jsonb` porque Amazon usa título/highlights/bullets/descripción/
atributos, ML usa título/ficha/descripción, y de Walmart y TikTok todavía no se
sabe. Columnas obligarían a decidir hoy lo que aún no se conoce, y a migrar cada
vez que se aprenda algo.

Los requisitos son relacionales porque la pregunta es *"¿cuáles faltan?"* —
comparar, contar y filtrar, no guardar un documento.

---

## LAS TRES TABLAS

Estaban revueltas y ahora están separadas. **Confundirlas es el error del que
salió todo este trabajo.**

### 1. `enrich.channel_content` — lo que TENEMOS

Migración `0016`. Llave **`(sku, canal, cuenta)`**.

```
sku · canal · cuenta · account_id · categoria
contenido    jsonb   ← el documento. Llaves CANÓNICAS del panel.
origen       jsonb   ← por campo: woo | ia | manual | calc
spec_version         ← versión del esquema del canal (Walmart 3.11 vs 3.19)
hash_base            ← ¿cambió el producto en Woo desde que se guardó?
updated_at
```

**Por qué la cuenta va en la llave:** en ML hay dos cuentas (BEKURA y
SANCORFASHION) que pueden publicar el MISMO SKU en categorías distintas. Caso
real `EST-0091`: es dos productos según la cuenta.

**Las llaves de `contenido` son CANÓNICAS**, no las nativas del canal: `titulo`,
`descripcion`, `bullets`, `highlights`, `atributos`. La traducción a `item_name`
/ `productName` / `goodsName` vive en el publicador. Así, cuando un marketplace
renombre un campo, se toca un adaptador y no se migran datos.

**Fusiona, no reemplaza.** El panel manda una pestaña a la vez: guardar los
highlights NO debe borrar los bullets. El upsert hace
`contenido || excluded.contenido`. Hay un `reemplazar=true` para el único caso
donde hace falta — borrar un campo.

### 2. `channel.field_requirements` — lo que el canal EXIGE

Migración `0018`. Llave **`(canal, categoria_id, campo)`**.

```
canal · categoria_id · campo · campo_canonico
obligatorio · tipo · valores_permitidos
default_value  ← lo que ponemos si el producto no lo trae
fuente         ← api | codigo | manual
leido_at       ← cuándo se leyó del canal
```

Más `core.canonical_fields`: el diccionario de 15 nombres canónicos, sembrado
desde `routers/publicar.py::CamposPublicar`, **no desde documentación**.

### 3. `ops.channel_submissions` — lo que SE MANDÓ

Existe desde `0001`. Una fila por intento. **No se toca.**

---

## LAS DECISIONES, Y POR QUÉ

### `campo` nativo + `campo_canonico`, los dos

Un requisito NO es un concepto nuestro: es lo que la API del canal contesta, con
SU vocabulario. Amazon exige `condition_type`, `fabric_type`,
`supplier_declared_dg_hz_regulation` — campos que el panel no edita y que no
tienen equivalente posible.

- Con FK canónica obligatoria: **no cabe la mitad de lo que Amazon pide**.
- Solo con el nativo: **se rompe la comparación** contra el contenido.

Se guardan los dos y se compara por el canónico. Beneficio lateral: **el mapeo
queda auditable** — es lo que habría hecho visible que Amazon declaraba
`country_of_origin="MX"` por un camino y `"CN"` por el otro durante meses.

### `default_value`: tres estados, no dos

~7 campos de Amazon SIEMPRE se llenan con una constante del publicador. Sin
distinguirlos, el panel los pintaría en rojo para los 22,186 SKUs.

```
está en el contenido               → verde
falta PERO hay default_value       → "lo ponemos nosotros"
falta, sin default, y obligatorio  → rojo
```

**Medido sobre los 553 productTypes de Amazon, ya cargados completos:**

```
3,354 campos obligatorios
  2,201  tienen canónico   → el panel puede llenarlos
  1,643  tienen respaldo   → el publicador los pone solo
     54  SIN NADIE         → nadie puede llenarlos
```

(Un campo puede tener las dos cosas — `brand` sale del producto y cae a
`"Generic"` —, por eso los números no suman 3,354.)

**Los 54 huérfanos son dos grupos, no 54 problemas:**

- **`fabric_type` en 45 tipos de ropa.** Obligatorio en Amazon, sin equivalente
  canónico y sin respaldo: hoy **nadie puede llenarlo**. Si se va a publicar
  ropa en serio, es lo primero que hay que resolver.
- **9 campos de libro** (`author`, `pages`, `publication_date`, `binding`,
  `subject_code`, `language`, `list_price`, `item_dimensions`, `manufacturer`),
  todos en **un solo tipo**. Kubera no vende libros: ese productType casi seguro
  entró por una detección automática equivocada en algún producto.

### Dos formas de comprobar, según dónde viva el campo

- `campo_canonico = 'atributos'` → se mira **DENTRO** de la lista, por el
  `nombre` de cada entrada, y se exige que el valor no venga vacío. Es el caso
  de Mercado Libre: sus obligatorios por categoría (`BRAND`, `MODEL`…) son
  atributos de la ficha, no campos de primer nivel.
- Cualquier otro canónico → basta la presencia de la llave.
- Sin canónico → nadie puede llenarlo: siempre falta.

**`campo_canonico` dice DÓNDE buscar y `campo` dice QUÉ buscar.** Sin esto, el
semáforo se pondría verde con cualquier atributo aunque faltara el obligatorio
de esa categoría — un falso verde, que es lo que la tercera luz existe para
evitar.

### `leido_at`: el tercer estado del semáforo

Si un canal agrega un obligatorio y nadie relee, el panel diría "no le falta
nada" y las publicaciones rebotarían sin explicación. Por eso el semáforo tiene
**tres luces**:

- `incompleto` — faltan campos. Ámbar.
- `ok` — están todos. Verde.
- **`sin_requisitos` — NO LO SABEMOS.** Gris, y lo dice con todas sus letras.

El tercero es el que importa: pintar verde una categoría cuyos requisitos nadie
leyó es mentir.

### `categoria_id = '*'` como centinela, no NULL

Es parte de la PK y en Postgres dos NULL no colisionan: se colarían duplicados.
Y evita escribir los comunes en cada una de las 1,937 hojas de TikTok.

**Precedencia: la categoría específica gana sobre `'*'`.** Y se resuelve **ANTES**
de filtrar por `obligatorio` — si se filtra primero, la fila específica que dice
`obligatorio=false` desaparece y gana la de `'*'`.

---

## EL FLUJO COMPLETO

```
1. El Estudio genera contenido por canal   (ia_generadores.GENERADORES)
   · AMAZON tiene circuito propio desde v0.137.0 (services/amazon_ia.py):
     los requisitos de su productType alimentan el prompt, el validador
     decide qué se aplica, y el documento se guarda SOLO (origen `ia`)
     sin esperar al botón de Guardar.
2. "Guardar contenido de <canal>"          → PUT .../canal/{canal}/contenido
                                             → enrich.channel_content (fusiona)
3. Al reabrir, el panel lo carga           → GET .../canal/{canal}/contenido
      precedencia: borrador local > servidor > Woo
4. El semáforo compara                     → GET .../canal/{canal}/faltantes
      field_requirements ⨯ channel_content
5. Al publicar, el publicador rellena      → publicar._rellenar_desde_guardado
      EL FORMULARIO MANDA; lo guardado solo llena lo vacío
6. El envío queda registrado               → ops.channel_submissions
```

El paso 5 se llama desde `preview` **y** desde `confirmar`: si solo lo hiciera el
envío, el modal enseñaría una cosa y se publicaría otra.

### Quién llena los requisitos

`backend/scripts/cargar_requisitos_amazon.py` — lee el JSON Schema de SP-API
Definitions por productType.

**Lee de `channel.listings`, NO de `amazon_progress`**: esa tabla de MySQL quedó
congelada al cerrarse la migración el 12-ago, y un SELECT ahí devuelve el pasado
sin decir que lo es. Se nota: `ARTIFICIAL_PLANT` sale 43 en la congelada y 41 en
la gemela viva.

---

## LO VERIFICADO, Y CÓMO

| Qué | Cómo |
|---|---|
| Guardar / leer / fusionar | HTTP real contra el sandbox |
| La fusión no borra | guardar solo `descripcion` deja 4 campos, no 1 |
| FK a `core.products` | SKU inexistente → 409 legible |
| `origen` distingue | 4 campos `ia` + 1 `manual` en producción (`ACC-0091`) |
| `categoria` la resuelve el backend | `HERR-0029` → `PROTECTIVE_GLOVE` sin mandarla |
| Los 3 estados del semáforo | servicio real contra sandbox |
| La precedencia de categoría | fila específica con `obligatorio=false` desaparece de faltantes |
| Migraciones | sandbox recreado desde cero, `PARIDAD OK`, 45 tablas |

**NO verificado:** el botón y el semáforo en el navegador durante el desarrollo.
El listado de Productos está fijo en `canal:"general"` (`page.tsx:94`), que lee
WooCommerce en vivo, y `env.staging` no tiene esas credenciales. Eduardo lo
confirmó a mano en producción.

---

## TRAMPAS MEDIDAS

1. **`ficha` de ML NO es una llave de contenido.** Es el id del BOTÓN en
   `ia_generadores.GENERADORES`; lo que produce cae en `atributos`. El frontend
   nunca lee `campos.ficha`. No le busques canónico.
2. **`item_length_width_height` no está mapeado a propósito.** Un atributo de
   Amazon cubre tres canónicos (largo/ancho/alto) y el modelo guarda uno por
   fila. Inventar la correspondencia sería la suposición que el cargador existe
   para evitar.
3. **Al verificar un upsert con `coalesce`, la fila tiene que estar limpia.** Un
   `categoria: "HOME"` leído como éxito era residuo de una prueba anterior.
4. **`parent_sku` y `has_variations` de `core.products` están MUERTAS.** La
   relación viva es `wc_parent_id` (7,299 productos). Usar las muertas produjo
   74 de 292 filas falsas en un reporte de Inmovilizado.
5. **El canal es `'mercado_libre'`, nunca `'meli'`.** Hay FK que lo rechaza.
6. **Las tablas MySQL de los 5 dominios están congeladas** desde el 12-ago. Un
   SELECT ahí devuelve agosto sin avisar.
7. **NUNCA `set_session(readonly=True)` contra el pooler de Supabase.** Es la
   más cara de esta lista: las DSN entran por el pooler en modo TRANSACCIÓN,
   varios clientes se turnan la MISMA conexión del servidor, y un ajuste de
   SESIÓN se queda pegado y lo hereda quien la tome después — **el backend de
   producción incluido**. La "protección" se convierte en una caída de
   escrituras ajena.

   Ya mordió dos veces: tumbó la carga de migraciones el 10-ago (documentado en
   el encabezado de `actualizar_sandbox.py`) y mató a media corrida la carga de
   los 541 productTypes el 12-ago, envenenada por su propia lectura
   (`cannot execute INSERT in a read-only transaction`).

   Lo correcto es `set transaction read only` como primera sentencia de la
   transacción: muere con ella y no contamina a nadie.

---

## LO QUE FALTA

1. **Amazon está COMPLETO**: 553 de 553 productTypes, 64,125 requisitos. Cero
   tipos del catálogo sin cubrir. ~~Lo que falta es `fabric_type`: obligatorio
   en 45 tipos de ropa y sin nadie que lo llene.~~ **RESUELTO en v0.137.0**: los
   obligatorios sin canónico y sin respaldo se le piden a la IA por su nombre
   nativo (`services/amazon_ia._bloque_atributos`). Medido en vivo con
   `DEC-0018-VER` (ARTIFICIAL_PLANT): 7 de 7 obligatorios cubiertos,
   `fabric_type` entre ellos, y llega al payload.
2. **Mercado Libre COMPLETO**: 1,058 categorías, 2,765 filas. Su forma es
   distinta a la de Amazon — `/categories/{id}/attributes` devuelve solo la
   ficha técnica, así que los comunes van como `categoria_id='*'` y los
   atributos obligatorios con su `MLM…`.
3. **Walmart, TikTok, Temu, Shein sin requisitos.** Cada uno necesita su
   cargador leyendo su propia API. Temu y Shein **no tienen una línea de código
   en el repo**: modelarlos hoy sería adivinar.
4. **`brand`**: decisión de Brandon (13-ago) — **las siguientes publicaciones,
   todas con `Generic`**, sin campo editable. En Amazon ya se cumple en los dos
   caminos: el mapper del vendor siempre puso `Generic`
   (`vendor/amazon_ready/attribute_mapper.py:204`) y el de RESPALDO
   (`publicar._amazon_attributes`, el que se usa cuando WordPress da 403) tomaba
   la marca del producto — ahí nacía el 3,060 / 4,204. Cerrado en v0.137.0.
   **Sigue abierto fuera de Amazon**: ML publica `"Ferrahome"`
   (`ml_atributos.MARCA`, `crear_producto.MARCA_FIJA`), TikTok igual
   (`tiktok_atributos.MARCA`) y Walmart lo toma del atributo con respaldo
   `"Ferrahome"` (`scripts/publicar_walmart.py:644`). Unificarlos toca a los
   otros publicadores y no se hizo sin coordinar con sus chats.
5. **`country_of_origin`**: unificado en `"MX"` por decisión de Eduardo. Walmart
   sigue mandando `"China"`. Es declaración aduanal.
6. **Sin columna de restricciones** (título 60 en ML vs 75 en Amazon, 2 decimales
   en Walmart). Esta tabla contesta "¿está el campo?", no "¿está bien?". La
   ausencia es decisión, no olvido: una columna que nace vacía es como nacieron
   `parent_sku` y `has_variations`.
7. **Sin refresco automático de `leido_at`.** Hoy se recarga a mano.

---

## ARCHIVOS

```
supabase/migrations/0016_enrich_channel_content.sql
supabase/migrations/0018_channel_field_requirements.sql
backend/services/channel_content.py          leer / guardar / faltantes / requisitos
backend/services/amazon_ia.py                el generador de Amazon (v0.137.0)
backend/services/amazon_contenido.py         los límites, validados
backend/services/terminos_protegidos.py      marcas registradas, lista cerrada
backend/routers/productos.py                 4 endpoints
backend/scripts/cargar_requisitos_amazon.py  el cargador
frontend/components/ProductStudio.tsx        botón + semáforo + parte de la IA
CAMPOS_POR_CANAL.md                          inventario por canal
```

## LO QUE SE MIDIÓ CON LOS 553 ESQUEMAS YA CARGADOS (13-ago)

La tabla dejó de ser solo para el semáforo: es el censo de atributos de Amazon
que antes había que adivinar con una llamada por tipo.

| Atributo | En cuántos de los 553 | Qué significa |
|---|---|---|
| `generic_keyword` | **551** | Es el destino real de los *backend search terms*. Faltan solo `ABIS_BOOK` y `MAPS` |
| `special_feature` | 264 | El ÚNICO destino posible de "Item Highlights" |
| `item_highlights` | **0** | No existe en el esquema de Listings, pese a llamarse así en la guía |
| `product_highlights` | **0** | idem |
| `key_product_features` | **0** | idem |

Consecuencia: en **289 tipos** (CHAINSAW entre ellos) los highlights se generan
y **no tienen dónde ir**. El adaptador lo dice en el log con nombre y apellido
en vez de dejar que desaparezcan.

Y `valores_permitidos` está **NULL en las 64,125 filas** de Amazon: el cargador
no trajo los enums. Por eso el prompt pide los campos por nombre pero no puede
ofrecerle a la IA la lista de valores válidos.
