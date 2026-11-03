# Guía de tallas de Mercado Libre al publicar (v0.717.0)

Desde abril-2026 el panel no logró publicar **ni una** prenda ni un par de zapatos en Mercado Libre:
998 intentos fallidos (135 SKUs), 0 éxitos. El modal decía
`NEEDS_MANUAL_CONFIG: GRID_REQUERIDO (configurar guía de tallas en ML)` aunque la guía existiera.
Este documento explica qué exige ML, qué hace ahora el panel, de dónde saca cada dato, qué dice cada
mensaje, qué familias no se publican desde el panel, y qué queda pendiente.

Código: `backend/services/ml_guia_tallas.py` (nuevo) · cableado en `backend/services/publicar_ready.py`
y `backend/services/publicar.py` · pruebas en `backend/tests/test_ml_guia_tallas.py`.

**Es un fix, sin interruptor.** No hay ninguna variable: ni para encenderlo o apagarlo, ni para
ajustarlo. Lo que lo deja inerte es que el dominio de la categoría no exija guía de tallas. La reversa
es revertir el commit (sección 12).

## 1. Lo que exige Mercado Libre (medido, no leído)

Medido el 7-oct-2026 con `POST /items/validate` (no crea nada), sobre la réplica de una publicación
viva de tenis y cambiando una cosa a la vez:

| # | Regla | Qué mandaba el publicador |
|---|---|---|
| 1 | `SIZE_GRID_ID` va como **value_name** = id de la guía. Como value_id ML lo rechaza siempre | como value_id (`vendor/ml_ready/publisher_core.py:307`) |
| 2 | `SIZE_GRID_ROW_ID` es **obligatorio**: value_name = `"<guía>:<renglón>"`. ML no lo deduce | nunca (0 de 998 intentos) |
| 3 | `SIZE` debe ser **idéntico al texto del renglón** («24 MX», no «24») | el value_id de la lista genérica de la categoría |
| 4 | `GENDER` debe ser el de la guía, y la guía debe ser de **esa cuenta** | «Mujer» (el primer valor de la lista) cuando el producto no traía género |
| 5 | `/items/validate` contesta **400 aunque solo haya warnings**: lo que cuenta son las causas `type == "error"` | armaba el mensaje con todas las causas, sin mirar el tipo |
| 6 | 59 dominios exigen guía (`/catalog/charts/MLM/configurations/active_domains`, pública) | — |
| 7 | Para **ropa no existe ni una guía** en ninguna cuenta; para calzado y brasieres sí | — |

Además, lo que el propio fallo de la guía estaba tapando (con la guía arreglada se habría publicado):

- la **marca** salía como la primera de la lista de la categoría (Asics en tenis, 136 de 136 intentos);
- el **color** lo elegía el mapper por subcadena o por «el primero»: «Negro / Rosa» salía «Rosa»,
  «Café» salía «Coral claro», y la meta `ml_attr_COLOR` heredada del padre le ganaba al eje de la
  variante — 50 de las 50 altas que se destrababan el primer día llevaban un color que no era el suyo;
- cuando ML rechazaba la guía o el título, el publicador **quitaba** `SIZE_GRID_ID` o `GENDER` y
  reenviaba (`publisher_core.py:495-516`): por eso el historial decía «falta la guía» donde en realidad
  «la guía se mandó mal».

## 2. Qué hace el panel ahora

Al **crear** una publicación (botón Publicar → vista previa → Confirmar), por cada cuenta y antes de
llamar al publicador vendorizado:

1. Lee el dominio de la categoría. Si no exige guía, **nada cambia**: el payload sale byte a byte igual
   y los mensajes y reintentos son los de siempre. (Una sola excepción, a propósito: si Mercado Libre
   contesta con un error **de guía** —`…fashion_grid…`— en un dominio que el panel no tiene en su lista,
   o cuya categoría no se pudo leer, ese rechazo no se reintenta y el mensaje dice qué hacer: agregar el
   dominio a `DOMINIOS_CON_GUIA`, o reintentar. El envío es el de siempre.)
2. Saca del producto el **género**, el **color** y la **talla** (sección 3). Nada se adivina.
3. **Guía**: la registrada para `(cuenta, dominio, género)` en
   `backend/vendor/ml_ready/size_chart_mapping.py`. Se lee de ML (`GET /catalog/charts/{id}`, 10 min de
   caché) y se comprueba que sea propia (SPECIFIC), activa, de ese dominio y de ese género.
4. **Renglón**: el único renglón de la guía que empata con la talla —
   por **texto** («24 MX» = «24 mx»), por **letra** (L ≡ G, XXL ≡ 2XL, CH ≡ S) o, si la talla es un
   número, por la **columna EU** de la guía. El id y el texto del renglón se copian tal cual. Un renglón
   cuyo largo de pie y su talla MX difieren en más de 2 cm no se usa (sección 5).
5. Con eso el payload lleva `BRAND` (la marca declarada, o Ferrahome), `GENDER`, `SIZE`, `COLOR`,
   `SIZE_GRID_ID` y `SIZE_GRID_ROW_ID` como ML los exige; y `MAIN_COLOR`, si el publicador lo mandaba,
   queda igual al color del producto o se quita. **Nada más cambia.** En el alta, esos atributos se
   reponen en cada `POST /items` de la cadena de reintentos del publicador, y un envío idéntico al que
   ML ya rechazó no se repite.

Si falta cualquiera de esos datos, o hay dos respuestas posibles, **esa cuenta no se publica** y el modal
dice qué falta (los faltantes del producto, todos juntos). Si ninguna cuenta puede salir, la vista
previa lo dice y **no ofrece Confirmar**.

La vista previa enseña una línea por cuenta y una del color, por ejemplo:

> BEKURA · guía de tallas 5602224 «Tenis Ferrahome Mujer»: talla 37 → renglón 5602224:2 «24 MX» (por la
> columna EU de la guía; pie de 24 cm). Género Mujer (lo propuso la IA al crear el producto: revísalo),
> marca Ferrahome.
>
> Color: saldrá como «Negro / Rosa» (eje Color de la variante; texto libre: no está en la lista de la
> categoría). El formulario del Estudio dice «Negro»; la variante es «Negro / Rosa»: se publica
> «Negro / Rosa». El publicador habría mandado «Rosa».

**Revisa esas líneas antes de confirmar**: la equivalencia EU → MX es la de la guía, no la del producto;
el género puede ser el que propuso la IA (y si el título nombra otro, la línea lo dice); y si el
formulario del Estudio o la meta dicen otro color, la línea lo dice y dice cuál sale.

## 3. De dónde sale cada dato del producto

### Talla — el eje que la variante FIJA

- Se lee del atributo **«Talla»** de WooCommerce con **una** opción.
- En una **variante** tiene que ser un **eje de variación que ella fija** (su meta `attribute_talla` /
  `attribute_pa_talla`). Si la única «Talla» que hay es un atributo de la ficha del **padre**, no se usa:
  WooCommerce le pasa a todas las hijas los atributos que el padre declara y ellas no fijan, así que un
  padre con «Talla = 40» hacía que la variante 42 se publicara como 40.
- Nunca se toma del SKU, ni del eje compuesto «variante» («NEG-40»), ni de la meta `ml_attr_size`.
- **Cómo se arregla:** en WooCommerce, en el producto padre, marcar «Talla» como atributo «usado para
  variaciones» y fijarla en cada variante.

### Por qué el formulario del Estudio no manda

El formulario de atributos del Estudio **no prueba que una persona eligió algo**:

- se **siembra** con los atributos de WooCommerce y vuelve al publicar, tocado o no; en una variante, la
  semilla del eje que ella no fija es **la primera opción de la lista del padre**;
- **«Mejorar con IA» reemplaza todas sus filas** por las que propone la IA (Color, Género y Talla
  incluidos), y viajan tal cual al publicar;
- no tiene cómo **agregar** una fila: si WooCommerce no trae «Género», esa fila solo aparece tras
  «Mejorar con IA» (o si hay contenido guardado del canal).

Desde el backend no se distingue lo que tecleó una persona de lo que puso la IA. Lo que una persona fijó
de verdad está en **WooCommerce**: por eso el formulario **no decide el color** y, en el género, va
**después** del atributo de WooCommerce. Lo que el formulario diga y no se use **no bloquea: se dice**
en la vista previa.

(El caso que lo destapó: a la variante «Negro / Rosa» sin título propio el panel le recomienda «Mejorar
con IA», cuya lista de colores no tiene compuestos. Si la IA deja «Negro» en el formulario de «Negro /
Rosa» y en el de «Negro / Morado», con el formulario por encima del eje las dos salían «Negro», misma
guía, mismo renglón y misma talla: dos publicaciones idénticas. Reproducido con el formulario puesto a
mano; qué contesta la IA en cada caso no se midió —no se la llamó—.)

### Color — solo la estructura de WooCommerce

- En una **variante**: el eje **«Color»** que ella MISMA fija (`attribute_color` / `attribute_pa_color`).
- En un producto **simple**: el atributo «Color» de WooCommerce con **una** opción.

No deciden: el formulario del Estudio, la meta `ml_attr_COLOR` (en una variante es la del padre), la
lista de colores del padre, ni «Color principal». Sin esa fuente → no se publica (`color_no_fijado`), y
el mensaje dice dónde se fija: en **WooCommerce** (el eje de la variante, o el atributo del producto).

Si el formulario o la meta dicen **otro** color, no se bloquea: la línea «Color:» de la vista previa lo
dice («El formulario del Estudio dice «Negro»; la variante es «Negro / Rosa»: se publica «Negro /
Rosa»»). Quien quiera otro color lo cambia en WooCommerce, igual que la talla.

Cómo viaja: si el texto empata **exacto** (sin mirar acentos, mayúsculas ni espacios dobles) con un
valor de la lista COLOR de la categoría, va con su id; si no, va el **texto literal** («Negro / Rosa»,
«Café»). Mercado Libre acepta color libre: 41 de las 159 publicaciones vivas de moda lo llevan. Nunca
por subcadena, nunca «el primero».

`MAIN_COLOR` («Color principal») lo mandaba el publicador desde una meta que la IA escribió para el
padre (985 de 1,001 intentos lo llevaban) y no es obligatorio en ninguna de las 53 categorías de moda
leídas. Ahora: si el color del producto empata exacto con SU lista, se iguala; si no, **se quita**; si
alguna categoría lo exigiera y no hubiera dato, se deja como lo manda el publicador y la vista previa lo
avisa. Nunca queda un color principal que contradiga a `COLOR`.

### Género — basta lo que propone la IA

Decide la **primera** fuente, en este orden, que da **un** género válido (decisión de Brandon,
9-oct-2026: «Género basta con lo que propone la IA»):

| Orden | Fuente | Cómo lo rotula la vista previa |
|---|---|---|
| 1 | el atributo **«Género»** de WooCommerce con **una** sola opción | «atributo Género de WooCommerce» |
| 2 | el **formulario del Estudio** (lo que trae y no es una opción de WooCommerce) | «formulario del Estudio; puede venir de «Mejorar con IA»: revísalo» |
| 3 | la meta `ml_attr_GENDER` / `ml_attr_gender`, que escribe **la IA** de Crear | «lo propuso la IA al crear el producto: revísalo» |

- Una fuente con **varios** géneros (p. ej. el atributo de WooCommerce con dos opciones) o con un valor
  que **no es de ML** («Unisex», «Dama») **no decide y tampoco bloquea**: se pasa a la siguiente, y la
  línea de la vista previa lo dice («Género Hombre (lo propuso la IA al crear el producto: revísalo; el
  atributo Género de WooCommerce trae «Mujer», «Hombre» y no decide)»).
- Una fuente posterior que diga otra cosa tampoco bloquea: se dice («…; la meta de Crear decía
  «Hombre»»).
- Solo se bloquea cuando **ninguna** fuente da un género válido: `falta el GÉNERO` (y, si alguna trae
  varios, dice cuáles), o `el género «…» no es un género de Mercado Libre` cuando lo que hay no es uno
  de los siete —Mujer, Hombre, Niñas, Niños, Bebés, Sin género, Sin género infantil—. Y cuando la
  **categoría** no admite el género decidido (`no existe en la categoría`).
- **El título nunca bloquea.** Si nombra un género (mujer/dama, hombre/caballero, niña, niño, bebé) que
  no es el decidido, la línea de la vista previa lo dice («…; el título dice «hombre»») y nada más.
  «unisex» en el título es compatible con «Sin género» y «Sin género infantil».
- **La salida de todo bloqueo de género es la misma**, y el mensaje la dice: en WooCommerce, dejar en el
  producto el atributo **«Género» con UNA sola opción** (ese va primero).

No se infiere el género del título ni se hereda de las hermanas.

## 4. Los mensajes y qué hacer con cada uno

| El modal dice… | Qué pasa | Qué hacer |
|---|---|---|
| `falta el GÉNERO` | ninguna fuente da un género: no lo trae, o la que lo trae dice varios (el mensaje dice cuáles: `(el atributo Género de WooCommerce trae «Mujer», «Hombre» y no decide)`) | en WooCommerce, dejar en el producto el atributo «Género» con **una** sola opción, de las que lista el mensaje |
| `el género «…» no es un género de Mercado Libre` | ninguna fuente da un género válido y lo que hay es «Unisex», «Dama»… | ídem, con uno de los siete valores |
| `el género «…» no existe en la categoría` | la categoría solo admite otros (p. ej. «Calzado para Bebés» solo «Bebés») | casi siempre es la **categoría**: corregirla; si la categoría está bien, corregir el atributo «Género» en WooCommerce |
| `no tiene COLOR propio` / `no tiene un COLOR fijado` | la variante no fija un eje Color, o el producto simple no tiene un atributo «Color» de una sola opción (dice qué hay y por qué no cuenta: la lista del padre, la meta, el formulario del Estudio) | en WooCommerce: fijar el eje «Color» de la variante, o dejar un solo valor en el atributo «Color» del producto. Escribirlo en el Estudio **no** lo arregla |
| `no tiene TALLA propia` / `no tiene TALLA` | la variante no fija un eje Talla (o el eje es «variante» = «NEG-40») | hacer de «Talla» un eje de variación en el padre y fijarlo en la variante |
| `la Talla que hay (…) es la del producto padre` | la «Talla» viene de la ficha del padre, no de la variante | ídem |
| `el atributo «Talla» trae N opciones` | un producto simple con varias tallas | dejar una sola |
| `no hay guía registrada para CUENTA · DOMINIO · GÉNERO` | no hay guía en `size_chart_mapping.py` | crear la guía en esa cuenta de ML y registrar su id (sección 7) |
| `la familia CALZ-… está EN REVISIÓN y no se publica desde el panel` | el SKU (o el de su padre) es de una de las seis familias de `FAMILIAS_EN_REVISION` (sección 6) | publicarlo a mano en Mercado Libre; o, cuando se corrija, quitar la familia de la constante en el código |
| `se creó en Mercado Libre y NO quedó pausado: MLM… en CUENTA (su estado en Mercado Libre es «active»)` | el candado de pausa (sección 8); dice el estado real de cada publicación, o que no se pudo leer | pausar esa publicación en ML; en cuanto el panel la lee en un estado que no es `active`, su candado se suelta solo |
| `no se pudo leer la guía … (HTTP 5xx / timeout)` | ML no contestó | reintentar |
| `… (HTTP 401 …)` | el token de esa cuenta venció. En moda este paso **no** lo renueva | se renueva cuando entra un pedido de esa cuenta o al publicar en ella algo que no es de moda; después reintentar (CLAUDE.md regla 8) |
| `… (HTTP 403 …)` | la app del panel no puede leer guías | revisar los permisos de la app de ML. Mientras tanto la moda no se publica (lo demás no se afecta) |
| `… (HTTP 404 …)` | la guía no existe **o esa cuenta no puede verla** | revisar el id y la cuenta en el mapping |
| `la guía … no se puede usar: es de tipo / está dada de baja / es del dominio…` | es STANDARD, está de baja, o es de otro dominio/género/marca | corregir el mapping |
| `… no se puede usar: el renglón X dice 28 MX y 38 cm de largo de pie` | el renglón trae un dato imposible (sección 5) | corregir ese renglón en la guía, en Mercado Libre |
| `la talla «…» no está en la guía` | la guía no tiene ese renglón (lista los que tiene) | agregar el renglón en ML o corregir la talla |
| `la talla «…» cae en N renglones` | la guía repite ese EU en dos renglones, o el número está a la vez en su columna EU y en la MX | en el Estudio, escribir en el atributo Talla de esa variante el texto exacto de uno de los renglones listados («26 MX»); o corregir la guía |
| `GUIA_DE_TALLAS_RECHAZADA (código)` | ML rechazó lo que se envió; el mensaje trae guía, renglón, SIZE, GENDER y COLOR enviados | revisar la guía en ML |
| `GUIA_DE_TALLAS (código): … salió sin ella` | no se pudo leer la categoría, o el dominio es nuevo | reintentar; o agregar el dominio a `DOMINIOS_CON_GUIA` |
| `el publicador no pudo leer la categoría … sin family_name` | ML no contestó al publicador, o el token venció | reintentar (el panel ya reintentó una vez) |

Avisos que **no bloquean** (vista previa):

- `Color: saldrá como «X»…` — qué color viaja, de dónde salió, lo que el formulario del Estudio o la
  meta dicen y **no** se usa («El formulario del Estudio dice «Negro»; la variante es «Negro / Rosa»: se
  publica «Negro / Rosa»») y, si el publicador habría mandado otro, cuál.
- `Título: … ya trae la talla / el color` — ML arma el título con este texto + color + talla; cada variante
  manda SU título (v0.623.0) y suele traerlos: saldrán repetidos y cada talla queda como publicación aparte.
- `Categoría: el título nombra «sandalia o huarache»…` — el título nombra un tipo de calzado (sandalia /
  huarache, bota / botín, tenis, zapato de seguridad, mocasín, pantufla, tacón / zapatilla) que no es el
  del dominio de la categoría. La guía sigue a la categoría: si el producto no es de ese tipo, la talla
  MX puede salir con hasta 2 cm de diferencia. Es un aviso **por palabras**: también salta donde la
  palabra no contradice a la categoría («zapatillas de ballet» en calzado de baile); ver D9.
- `Si confirmas, se creará SOLO en …` — una cuenta sí sale y la otra no (sección 9).

Lo que diga la Talla del formulario del Estudio **solo elige entre los renglones en los que la talla de
WooCommerce ya cayó**; no reemplaza la talla del producto. Si lo escrito no es uno de esos renglones,
la línea de la vista previa lo dice («no se usó, manda la de WooCommerce»). Y cuando sí elige, la línea
lo avisa («renglón elegido en el Estudio entre los que le tocan; lo escrito ahí puede venir de «Mejorar
con IA»: revísalo»): como todo el formulario, esa Talla pudo escribirla una persona o la IA.

En los dominios con guía, los rechazos de ML ahora salen con su código y su mensaje reales
(`Mercado Libre rechazó el alta — item.attribute.invalid: …`) en vez de `HTTP 400: Validation error`,
y un warning ya no produce etiqueta: el de envío (`shipping.lost_me1_by_user`) tapaba el error real.
El mismo texto queda en `ml_backlog`. Fuera de esos dominios los mensajes no cambian, salvo el de un
rechazo **de guía** (`…fashion_grid…`): ahí —un dominio que ML agregó y falta en `DOMINIOS_CON_GUIA`, o
una categoría que no se pudo leer— el modal dice `GUIA_DE_TALLAS (código): … salió sin ella`, con qué
hacer, y no se reintenta (antes: tres intentos y `HTTP 400: Validation error`, o
`NEEDS_MANUAL_CONFIG: GRID_REQUERIDO`). En ese caso `ml_backlog` conserva la etiqueta del publicador.

Si el arreglo que pide un mensaje se hace en la guía (agregar o corregir un renglón), basta volver a
publicar: antes de bloquear por el contenido de una guía que tenía en caché, el panel la relee una vez.

## 5. La tabla EU → MX de cada guía registrada

La talla de WooCommerce es un número EU; el `SIZE` que se publica es el MX **del renglón de la guía**.
Esta es la conversión que aplica cada guía registrada (leídas el 7-oct-2026; `→` separa EU de MX):

| Cuenta | Dominio · Género | Guía | EU → MX (renglón por renglón) | Ojo |
|---|---|---|---|---|
| BEKURA | SANDALS_AND_CLOGS · Hombre | 5601946 | 37→24 · 38→24.5 · 38→25 · 39→25.5 · 40→26 · 40→26.5 · 41→27 · 42→27.5 · 42→28 · 43→28.5 · 44→29 · 44→29.5 · 45→30 · 45→30.5 · 46→31 | EU repetido: 38, 40, 42, 44, 45 |
| BEKURA | SNEAKERS · Hombre | 5601948 | 40→25 · 41→26 · 42→27 · 43→28 · 44→29 | se usa tal cual; una talla que no tenga queda bloqueada con su motivo |
| BEKURA | SNEAKERS · Mujer | 5602224 | 36→23 · 37→24 · 38→25 · 39→26 · 40→27 · 41→28 | |
| BEKURA | BOOTS_AND_BOOTIES · Hombre | 5602034 | igual que 5601946 | EU repetido: 38, 40, 42, 44, 45 |
| BEKURA | LOAFERS_AND_OXFORDS · Hombre | 5601950 | igual que 5601946 | EU repetido: 38, 40, 42, 44, 45 |
| BEKURA | BRAS · Mujer | 5269931 | M · G · XL (letras) | la L de Woo sale «G» |
| SANCORFASHION | SANDALS_AND_CLOGS · Hombre | 6009679 | igual que 5601946 | EU repetido: 38, 40, 42, 44, 45 |
| SANCORFASHION | SNEAKERS · Hombre | 4538718 | 40→25 · 41→26 · 42→27 · 43→28 · 44→29 | se usa tal cual; una talla que no tenga queda bloqueada con su motivo |
| SANCORFASHION | SNEAKERS · Mujer | 4821199 | 36→23 · 37→24 · 38→25 · 39→26 · 40→27 · 41→28 | |
| SANCORFASHION | SNEAKERS · Sin género | 4827537 | (sin EU) 23 · 24 · 25 · 26 · 27 | sin columna EU (D3) |
| SANCORFASHION | BOOTS_AND_BOOTIES · Hombre | 5601952 | igual que 5601946 | EU repetido: 38, 40, 42, 44, 45 |
| SANCORFASHION | BOOTS_AND_BOOTIES · Sin género infantil | 4572778 | 22→13.5 · 23→15 · 24→15.5 · 25→16 · 26→16.5 · 27→17 · 28→17.5 · 29→18 · 30→18.5 · 31→19 · 31→20 | EU repetido: 31 |
| SANCORFASHION | LOAFERS_AND_OXFORDS · Hombre | 5601954 | igual que 5601946 | EU repetido: 38, 40, 42, 44, 45 |
| SANCORFASHION | SAFETY_FOOTWEAR · Hombre | 4859025 | 38→25 · 39→26 · 40→26.5 · 41→27 · 42→27.5 · 43→28 · 44→28.5 · 45→29 | renglón `4859025:6` (EU 43) dice 28 MX y **38 cm** de largo de pie: no se usa |
| SANCORFASHION | BRAS · Mujer | 4922945 | M · L · XL (letras) | |

Lo que esa tabla le dice a quien decide:

- **La misma talla EU no es el mismo MX en todas las guías.** Hombre EU 40 = `25 MX` en tenis, `26` o
  `26.5 MX` en botas, mocasines y sandalias, `26.5 MX` en calzado de seguridad; EU 45 = `29 MX` en
  seguridad y `30` / `30.5 MX` en botas. Mujer EU 40 = `27 MX`. Un número EU es una sola medida: al
  menos una de esas equivalencias le queda mal al comprador que elige por MX. Por eso importa que la
  **categoría** sea la del producto (el aviso «Categoría:» de la vista previa).
- **Un renglón imposible no se usa.** En las guías propias la talla MX es el largo del pie en
  centímetros (la diferencia es 0.0 en todos los renglones leídos menos uno). Si un renglón trae las
  dos cosas y difieren más de 2 cm, la talla que cae ahí no se publica hasta que se corrija la guía en
  Mercado Libre. Hoy es uno: `4859025:6`.
- La equivalencia es la **de la guía**, no la del producto: si el producto trae su propia tabla de
  medidas y no coincide, manda la guía (por eso las tres familias de calzado con tabla propia están en
  `FAMILIAS_EN_REVISION`, sección 6).

## 5b. Guías de ropa: la tabla estándar por género

En ropa Mercado Libre solo acepta guías **propias** de la cuenta (no hay guías estándar de ML como
en calzado), y no existía ninguna. Decisión de Brandon (9-oct-2026): **una tabla de medidas estándar
por género —Hombre, Mujer, Niño—**, la misma para todos los dominios; no una tabla por producto.

Medidas del cuerpo en centímetros (desde–hasta):

| Talla | Mujer: busto / cintura / cadera | Hombre y unisex: pecho / cintura / cadera |
|---|---|---|
| XS | 78–82 / 60–64 / 86–90 | 82–87 / 70–75 / 84–89 |
| S | 83–87 / 65–69 / 91–95 | 88–95 / 76–82 / 90–96 |
| M | 88–92 / 70–74 / 96–100 | 96–103 / 83–89 / 97–103 |
| L | 93–98 / 75–80 / 101–106 | 104–111 / 90–97 / 104–110 |
| XL | 99–105 / 81–87 / 107–113 | 112–119 / 98–106 / 111–117 |
| 2XL | 106–112 / 88–94 / 114–120 | 120–127 / 107–115 / 118–124 |
| 3XL | 113–119 / 95–101 / 121–127 | 128–135 / 116–124 / 125–131 |
| 4XL | 120–126 / 102–108 / 128–134 | 136–143 / 125–133 / 132–138 |
| 5XL | 127–133 / 109–115 / 135–141 | 144–151 / 134–142 / 139–145 |

| Infantil (talla = estatura) | Edad | Estatura | Pecho | Cintura | Cadera |
|---|---|---|---|---|---|
| 90 cm | 2 años | 86–94 | 51–53 | 49–51 | 53–55 |
| 100 cm | 4 años | 95–104 | 54–56 | 51–53 | 56–59 |
| 110 cm | 5 años | 105–114 | 57–59 | 53–55 | 60–62 |
| 120 cm | 7 años | 115–124 | 60–63 | 55–57 | 63–66 |
| 130 cm | 9 años | 125–134 | 64–67 | 57–59 | 67–71 |
| 140 cm | 10 años | 135–144 | 68–72 | 60–62 | 72–76 |
| 150 cm | 12 años | 145–154 | 73–77 | 63–65 | 77–82 |
| 160 cm | 14 años | 155–164 | 78–83 | 66–69 | 83–88 |

La tabla vive en `backend/scripts/crear_guias_ropa_ml.py`, que es quien crea las guías:

1. `python -m scripts.crear_guias_ropa_ml` (desde `backend/`) — ensayo: dice qué crearía, no escribe.
2. `python -m scripts.crear_guias_ropa_ml --aplicar` — crea una guía por cuenta y por clave
   `DOMINIO:Género` (42 claves con productos esperando). Reutiliza la que ya exista con el mismo
   nombre, así que se puede repetir.
3. Pegar en `backend/vendor/ml_ready/size_chart_mapping.py` las líneas que imprime al final y subir
   el cambio. **Sin ese registro la guía existe en Mercado Libre pero el publicador no la usa.**

Cada guía lleva solo las medidas que la ficha de su dominio admite: partes de arriba, pecho; partes
de abajo, cintura y cadera; pijamas, busto y cadera; infantil, además la estatura. No cubre tallas
numéricas de ropa (30, 32…), rangos («S / M») ni «Unitalla»: el producto con esa talla sigue diciendo
«la talla no está en la guía» hasta que se le agregue el renglón en Mercado Libre.

## 6. Familias en revisión (a mano, en el código)

Seis familias de calzado **no se publican desde el panel aunque tengan guía**. Viven en una constante,
`FAMILIAS_EN_REVISION` en `backend/services/ml_guia_tallas.py`; no hay variable que sume ni que libere:

| Familia | Por qué |
|---|---|
| `CALZ-0134`, `CALZ-0129`, `CALZ-0127` | en sus publicaciones vivas la persona eligió, de forma sistemática, otro renglón que el de la columna EU de la guía |
| `CALZ-0089`, `CALZ-0090`, `CALZ-0200` | traen tabla de tallas propia (`_kubera_size_chart`) que puede contradecir a la guía |

- Frena al SKU que **es** la familia (`CALZ-0129`) o **empieza por** `<familia>-` (`CALZ-0129-NEG-40`),
  y a la variante cuyo **padre** es de la familia aunque su SKU no lleve ese prefijo. `CALZ-01290` es
  otra familia: no se frena.
- Es lo primero que se revisa en un dominio con guía: el modal dice `la familia CALZ-… está EN REVISIÓN
  y no se publica desde el panel` (una sola vez, vale para las dos cuentas), no se lee la guía y no se
  envía nada. Fuera de los dominios con guía no interviene.
- **Cómo se sale:** publicar ese producto a mano en Mercado Libre; o, cuando la familia se corrija,
  **quitarla de la constante** y desplegar. Para frenar otra familia: agregarla ahí.

Ya **no** están frenados: los tenis de Hombre (decisión de Brandon, 9-oct-2026: se usa la guía
registrada —BEKURA 5601948, SANCORFASHION 4538718—; la talla que esa guía no tenga queda bloqueada con
su motivo), ni las cinco familias de ropa con tabla propia (`ROP-0123`, `ROP-0177`, `ROP-0240`,
`ROP-0456`, `ROP-0465`): saldrán con la guía de ropa que se registre para su dominio y género, aunque
su tabla propia diga otra cosa (sección 11: no se lee `_kubera_size_chart`).

## 7. Cómo registrar una guía nueva

1. Crearla en la cuenta de Mercado Libre (panel de ML o `POST /catalog/charts`). En calzado, llenar la
   columna **EU en todos los renglones y sin repetir**, y que el largo de pie sea el de la talla MX. En
   ropa, nombrar los renglones como las tallas de WooCommerce (S, M, L, XL, 2XL… o CH/M/G/XG; se
   comparan normalizadas).
2. Agregar su id en `backend/vendor/ml_ready/size_chart_mapping.py`, en la cuenta correcta, con la llave
   `"DOMINIO:Género"` (el género escrito exactamente como en ML).
3. Desplegar. En la vista previa de un producto de ese dominio debe aparecer la línea de la guía.

El panel **no** busca guías por su cuenta ni usa las STANDARD de Mercado Libre: solo lo registrado
(regla 2 de la casa: la elección humana manda sobre cualquier detector).

## 8. El candado de pausa

Toda alta debe quedar pausada, pero `POST /items` ignora `status: paused`: lo único que pausa es el
`PUT` posterior, y puede fallar. Hasta ahora eso no podía hacer daño en moda, porque moda no creaba nada.

Solo para altas **con guía**: si una termina creada y **sin pausar**,

- en ese mismo clic **no se crea en la otra cuenta**, y
- las siguientes altas de moda se frenan antes de enviar nada: «se creó en Mercado Libre y NO quedó
  pausado: MLM… en CUENTA (su estado en Mercado Libre es «active»). No se crea nada más de moda hasta
  revisarlo». La vista previa también lo dice.

Cómo funciona:

- **Es un conjunto, no una casilla.** Si dos personas publican moda a la vez y las dos publicaciones
  quedan sin pausar, el candado recuerda las dos: pausar la última no suelta a la primera, y el mensaje
  las lista todas.
- **Cada una se suelta sola** cuando, al releerla (una lectura por publicación en cada intento), su
  estado es **cualquiera que no sea `active`**: pausada, cerrada, en revisión, inactiva… — ya no está
  vendiendo sin que nadie la haya visto. (Si solo soltara con «pausada», una publicación que Mercado
  Libre dejara en revisión frenaría toda la moda hasta reiniciar el backend, con un mensaje que pide
  pausar algo que no se puede pausar.)
- **Falla cerrado.** Si no se puede leer el estado (sin token, sin red, una excepción), esa publicación
  se queda en el candado y el mensaje dice «no se pudo leer su estado en Mercado Libre».
- **Si la verificación de pausa revienta** (se cae la red justo después de crear), la publicación se
  trata como NO pausada: se pone el candado, la fila lleva su aviso y el alta termina de contarse en vez
  de acabar en «Error de conexión» con una publicación viva que nadie anotó.
- **Si lo que revienta es el propio publicador después de crear** —su primera pausa, que va antes que
  esa verificación, o la subida de la descripción—, el alta **no se reintenta**: reintentar es otro
  `POST /items`, o sea otra publicación (medido el 9-oct: con la red caída eran tres de la misma talla,
  activas, sin fila en `ml_backlog` y sin candado). Se termina con la que ya existe: un intento más de
  descripción, su fila en `ml_backlog` / `ml_progress`, y la pausa se verifica como en el punto anterior.
  Si la descripción no subió, el aviso de la fila lo dice. En los logs: `Mercado Libre YA creó MLM…`.
- **El mensaje dice el estado real** que se leyó, no un «ACTIVA» fijo.
- Vive en memoria **por proceso**: se vacía al reiniciar el backend (y, si el backend corriera con más
  de un worker, cada uno tendría el suyo).

Fuera de moda nada de esto corre: un alta sin guía ni pone ni mira el candado, y si su verificación de
pausa revienta, falla como siempre. Tampoco el rescate del punto anterior: ver el límite de la sección 11.

## 9. Alta parcial

Si una cuenta sí puede publicarse y la otra no (p. ej. `CALZ-0193`: hay guía de calzado de seguridad
solo en SANCORFASHION), la vista previa lo avisa antes de confirmar. Al confirmar, la cuenta que sí sale
se crea y el resultado es **correcto** (`ok` con `parcial`): el modal enseña el renglón rojo de la que
no salió, con su motivo, y ofrece solo «Cerrar». El panel no completa después la cuenta que faltó:
«Actualizar» solo toca donde ya existe la publicación.

Cuando una cuenta ya está viva y la otra fue eliminada en ML, la vista previa de «actualizar» también
resuelve la guía de la que se va a re-crear y suma sus líneas. Solo en moda: antes pregunta lo barato
(¿el dominio de la categoría exige guía? — una lectura de metas; el dominio de una categoría se
recuerda) y, si no la exige, no arma ningún payload ni pide tokens.

## 10. Decisiones de negocio pendientes

Cerradas por Brandon el 9-oct-2026 (ya no están en la tabla): **D1**, tenis de Hombre — se usa la guía
registrada (BEKURA 5601948, SANCORFASHION 4538718; EU 40 = «25 MX»), aunque SANCORFASHION tenga otra
propia, la 4550104, con EU 40 = «27 MX» y 27 publicaciones vivas—; y **D5**, quién captura el género —
basta lo que propone la IA (sección 3).

| # | Decisión | Estado en el código |
|---|---|---|
| D2 | Seis guías de Hombre (sandalias, botas, mocasines; ambas cuentas) repiten EU 38, 40, 42, 44 y 45 en dos renglones | esas tallas salen como «cae en 2 renglones»; se destraban una por una desde el Estudio, o corrigiendo las guías |
| D3 | Guías sin columna EU (4827537, tenis Sin género de SANCORFASHION) | un número suelto no empata: hay que completar la columna EU o registrar otra guía |
| D4 | Familias vivas con conversión propia (`CALZ-0134`, `CALZ-0129`, `CALZ-0127`) o con tabla de tallas propia (`_kubera_size_chart`): `CALZ-0089`, `CALZ-0090`, `CALZ-0200` | **en revisión** (`FAMILIAS_EN_REVISION`, sección 6): se publican a mano. Decidir por familia si vale la conversión de la guía y, entonces, quitarla de la constante. Las cinco de ropa con tabla propia (`ROP-0123`, `ROP-0177`, `ROP-0240`, `ROP-0456`, `ROP-0465`) ya **no** se frenan |
| D6 | Guías de ROPA: no existe ninguna (44 claves dominio × género, ~1,039 SKUs) | sin guía no se publica |
| D7 | 413 SKUs están en «Calzado para Bebés» y 311 traen tallas de adulto | el mensaje de género fuera de categoría los destapa; hay que recategorizar |
| D8 | El renglón `4859025:6` de la guía de calzado de seguridad (28 MX con 38 cm) | la talla EU 43 de esa guía no se publica hasta corregirlo en ML |
| D9 | 78 SKUs que el título llama «sandalia» o «zapato de seguridad» están en la categoría de tenis: `CALZ-0181` «ZANDALIAS DE CORRER» ×27, `CALZ-0198` «SANDALIA DE CORREA» ×25 y `CALZ-0175` «ZAPATO DE SEGURIDAD» ×26 | la vista previa avisa a los 78 (no bloquea); hay que corregir el título o la categoría. El aviso es **por palabras**: de los 2,789 pendientes salta en 105, y los otros 27 no son de este caso — «zapatillas de ballet» en calzado de baile (11), «zapatillas…» en sandalias (14), «ZAPATILLAS DE TENNIS» en tenis (1), y uno que sí acierta: «botas de lluvia» en la categoría de sandalias |
| D10 | 16 publicaciones vivas de SANCORFASHION (`CALZ-0117`, `CALZ-0121`, `CALZ-0140`) usan la guía **4539046**, que no está en el mapping; la registrada para tenis de Mujer es la 4821199. Las dos dan el mismo `SIZE` y el mismo largo de pie para EU 36–40 («23…27 MX») | si alguien fija el género y los ejes de las hermanas pendientes de esas familias (p. ej. `CALZ-0121-BEI-BLN-36…40`), saldrían con la 4821199 y sus hermanas vivas seguirían en la 4539046: misma talla, otra guía. Decidir si se registra la 4539046 o se migran las vivas |
| D11 | La guía **5602224** (BEKURA, tenis de Mujer) no la usa ninguna publicación viva: las 19 altas de BEKURA son su estreno. Y el único título de `CALZ-0176` en el sondeo es «tenis blancos mujer casuales con mariposas bordadas», con 10 de sus 21 SKUs negros | acompañar la **primera alta de CADA cuenta** (la de `CALZ-0193` solo prueba SANCORFASHION) y revisar en Mercado Libre el título de la variante: si no tiene título propio saldría bajo «tenis blancos…» (el sondeo no trae el título de cada variante: no se pudo medir) |

## 11. Lo que este cambio NO hace (límites conocidos)

- No crea guías en Mercado Libre ni les agrega renglones.
- No cambia `family_name` ni cómo se agrupan las tallas en una familia (cada variante sale con su
  título; la vista previa avisa cuando el título ya trae la talla o el color).
- No usa `variations` (ML ya no las acepta en estas cuentas).
- No toca la actualización de publicaciones vivas.
- No lee la tabla de medidas propia del producto (`_kubera_size_chart`): manda la guía.
- No corrige categorías mal puestas ni infiere el género.
- No completa una cuenta que quedó sin publicar cuando la otra sí entró.
- Las variantes cuyo único eje es «variante» («NEG-40») se bloquean por falta de talla y de color
  propios (hoy 612 pendientes; 751 contando las ya publicadas).
- **Hermanas en el mismo renglón:** dos SKUs del mismo padre y color con tallas `XXL` y `2XL` caen en el
  mismo renglón (son la misma talla escrita de dos formas): serían dos publicaciones iguales. No se
  detecta; hay que dejar una sola escritura en WooCommerce.
- **Ejes de taxonomía (`pa_color`, `pa_talla`):** para esos ejes `wp_db._atributos_de_variacion` entrega
  el identificador interno del término («negro-amarillo») y no su etiqueta («Negro / Amarillo»); el
  color viajaría así (la vista previa lo enseña en la línea «Color:»). Es un defecto previo de la lectura
  de WooCommerce, común a todos los canales. Hoy 1 sola variante de moda usa `pa_color` (las demás usan
  ejes personalizados, que guardan el texto tal cual) y no está entre las altas que hoy se destraban.
  **Ya no se puede corregir desde el Estudio** (el formulario no decide el color): mientras no se arregle
  esa lectura, esa variante no debe publicarse si la línea «Color:» enseña el identificador.
- **El formulario del Estudio no decide el color ni manda en el género** (sección 3). Consecuencias:
  - para cambiar el color de una variante, o para elegir entre los colores o géneros que WooCommerce ya
    lista, hay que hacerlo **en WooCommerce**; escribirlo en el Estudio no lo cambia (se dice en la vista
    previa, no bloquea);
  - un género que solo existe en el formulario (o en la meta de la IA) publica **rotulado**; no se
    distingue si lo tecleó una persona o lo dejó «Mejorar con IA»;
  - la Talla del formulario sigue eligiendo entre los renglones en los que la talla de WooCommerce ya
    cayó (es la única salida de «cae en N renglones»), y también puede venir de «Mejorar con IA»: la
    línea lo avisa. No abre renglones que no sean de esa talla.
- `0XL` y `1XL` no se equiparan a `L` ni a `XL` (solo empatan con un renglón escrito igual).
- **El género que propone la IA basta, y el título no lo frena.** Un producto «para hombre» al que la
  IA le puso «Sin género» o «Mujer» se publica con ese género y con su guía; la línea de la vista previa
  dice «el título dice «hombre»», pero no bloquea. Quien confirma es quien lo revisa. Si el género está
  mal, se corrige en WooCommerce (atributo «Género» con una opción).
- El aviso «Categoría: el título nombra…» es por palabras: salta también donde la palabra no contradice a
  la categoría (D9).
- El candado de pausa vive en memoria por proceso (sección 8).
- **Fuera de moda, un publicador que revienta después de crear sigue duplicando.** Es un defecto
  **previo** a este cambio (igual en v0.625.0) y de todas las categorías: si la red se cae en la pausa
  que el publicador hace justo después de crear, el alta se reintenta y cada intento crea otra
  publicación (medido: tres con la red caída, dos si se cae una sola vez; la primera queda sin pausar y
  sin bitácora). En las altas **con guía** ya no pasa (sección 8); fuera de moda no se tocó, porque
  ahí este fix no cambia nada. Arreglarlo para todo es una decisión aparte.
- **Con Mercado Libre caído, todo espera un poco más.** Antes de llamar al publicador se lee la
  categoría para saber si exige guía (con el token y, si no contesta, por la ruta pública: hasta 15 s
  cada lectura), y un fallo no se recuerda. Medido con `/categories` sin contestar: la vista previa de
  un producto que **no** es de moda hace 5 lecturas en vez de 1 (hasta +60 s) y su alta 5 en vez de 3
  (hasta +30 s por cuenta), para terminar en el mismo error de siempre. Corre en un hilo: no congela
  el backend. Con Mercado Libre contestando el costo es una lectura por categoría, que se recuerda.
- Los demás rellenos del publicador en atributos que no son marca, género, talla ni color siguen como
  en todo el catálogo.

## 12. No tiene interruptor: la reversa es revertir el commit

Es un fix, no un flujo nuevo: no nace detrás de ninguna variable y no hay nada que encender ni apagar en
Railway.

| Qué | Cómo | Efecto |
|---|---|---|
| Todo | revertir el commit (`git revert`) y desplegar | vuelve el publicador de antes (en moda, `GRID_REQUERIDO`) |
| Una familia | agregarla a `FAMILIAS_EN_REVISION` en `backend/services/ml_guia_tallas.py` y desplegar | esa familia deja de publicarse desde el panel con el mensaje «EN REVISIÓN»; lo demás sigue |
| Un género de un dominio en una cuenta | quitar su guía de `backend/vendor/ml_ready/size_chart_mapping.py` y desplegar | esa clave contesta «no hay guía registrada» y no se publica |

Señal de vida en los logs: una línea `guia_tallas sku=… cuenta=… estado=…` por cada resolución, y
`guardia de create_item instalada` en la primera alta con guía tras un arranque.

## 13. Diagnóstico exprés

1. **El mensaje ya dice qué falta** (sección 4).
2. **La vista previa es el diagnóstico**: no escribe nada, y enseña por cuenta la guía, el renglón, el
   texto de talla, el color que viaja y de dónde salió el género.
3. **Logs de Railway** (BackendOmnicanal): buscar `guia_tallas`. `codigo=guia_en_revision` = una de las
   seis familias de la sección 6. `NO quedó pausada` = el candado de pausa (y `se suelta su candado`
   cuando deja de estar activa). `Mercado Libre YA creó MLM… y el publicador lanzó después` = el
   publicador se interrumpió tras crear y el alta no se reintentó (sección 8): revisar esa publicación
   en Mercado Libre. `reventó — se sigue con lo de siempre` = una etiqueta o una clasificación
   de rechazo falló y el alta siguió con la del publicador.
4. **Historial** (solo `SELECT`): en `ml_backlog`, los rechazos de ML en moda empiezan con
   `Mercado Libre rechazó el alta —` y traen el payload que de verdad salió. Lo que el panel bloqueó
   antes de enviar no deja fila ahí (no hubo envío): queda en la bitácora de persona.

## 14. No verificado contra Mercado Libre

- **Ninguna alta real con este código.** Lo que SÍ se verificó, el 9-oct-2026, con el código final y sin crear nada: la vista
  previa real de las 34 variantes de `CALZ-0176` y `CALZ-0193`, en las dos cuentas, enviada a
  `POST /items/validate` con el token de la app `8902165405612832`. Las 48 que resuelven salieron
  válidas (19 BEKURA + 29 SANCORFASHION): cero causas `fashion_grid`, con `SIZE` y `BRAND` solo por
  value_name, `COLOR` compuesto como texto libre y sin `MAIN_COLOR`. Validar no es crear: la primera
  alta real de cada cuenta va acompañada. Y siete productos que no son de moda, en las dos cuentas,
  dieron un payload idéntico al del pipeline vendorizado por sí solo.
- **Las guías de ropa** (sección 5b): el script arma las 42 claves sin red, pero ninguna se ha creado
  todavía en Mercado Libre; la primera vez conviene crear una sola (`--clave PAJAMAS:Mujer`) y mirarla.
- **Tenis de Hombre con la guía registrada** (5601948 / 4538718): ninguno de los pendientes del sondeo
  cae hoy ahí (ningún tenis pendiente trae «Hombre»), así que ni se simuló un caso real ni se validó
  contra Mercado Libre. La primera alta de tenis de Hombre también va acompañada.
- Esas dos familias no traen medidas de paquete; ML las exige y el reintento de siempre del publicador
  (1 kg, 30×20×15 cm) las completa. Conviene capturarlas en WooCommerce.
- Publicar con una guía STANDARD (por eso no se usan).
- **Qué deja «Mejorar con IA» en el formulario del Estudio** para el Color, el Género y la Talla de una
  variante: no se llamó a la IA. Para el color el código ya no depende de eso (el formulario no lo
  decide); para el **género sí**: lo que la IA deje en el formulario, o en la meta de Crear, decide la
  guía. Conviene mirarlo en la primera alta.
- Qué contesta Mercado Libre al leer una guía con el token de la **otra** cuenta (sin token contesta 404).
- Que la publicación quede pausada. Si la fila dice «creada pero NO pausada», pausarla a mano en ML: el
  candado de pausa frena las siguientes hasta entonces.
