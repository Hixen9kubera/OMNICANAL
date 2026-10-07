# CATALOGO_VIVO — cómo se le pregunta a cada sistema qué tiene AHORA

> Extraído el 6-oct-2026 de `origin/main` (v0.618.0) y de la rama
> `sandbox/precios-optimos` (lab-0.3). Si algo aquí no coincide con `main`,
> **gana `main`** y hay que re-extraer.

Brandon pidió una página con todo el catálogo de Odoo cruzado contra los cinco
marketplaces, leído de las plataformas y no de la base. Este documento es lo que
hubo que saber para hacerlo; el ejecutable está en [scripts/](scripts/).

---

## 1 · Cómo lee producción cada fuente

| Fuente | Dónde lo hace producción | Lo que importa |
|---|---|---|
| Odoo, catálogo y stock | `backend/services/odoo.py::listar_catalogo` | El stock es **`free_qty`**, no `qty_available`: lo físico menos lo reservado. Por almacén: el mismo `search_read` con `context={"warehouse": id}` |
| Odoo, contenedor | `odoo.py::contenedores_por_sku` | `container_numbers` es texto libre y sucio. No se parsea: se extraen códigos con regex (`packing_drive_carpeta.codigos_de`) |
| ML, qué existe | `backend/services/inventario.py::_universo_ml` | `GET /users/{id}/items/search?search_type=scan&status=` por estado. Nunca `ml_progress`: solo conoce lo que publicó el panel |
| ML, el SKU | `inventario.py::_sku_de_item` | `seller_custom_field` → atributo `SELLER_SKU` → variaciones |
| ML, precio cobrado | `backend/services/precios_venta.py::_precio` | `GET /items/{id}/sale_price?context=channel_marketplace` — trae la promoción aplicada |
| ML, comisión y envío | laboratorio: `sandbox_precios/extraer_ml.py` | `GET /sites/MLM/listing_prices?price=&category_id=&listing_type_id=&logistic_type=` y `GET /users/{id}/shipping_options/free?item_id=&item_price=` |
| Amazon | `backend/services/amazon.py::datos_por_sku` | `searchListingsItems` (`GET /listings/2021-08-01/items/{seller}`), `includedData=summaries,offers,fulfillmentAvailability` |
| TikTok | `backend/services/tiktok_censo.py::censar` | `/product/202309/products/search` paginado por `page_token`; contesta HTTP 200 aunque falle (el veredicto va en `code`) |
| Temu | `backend/services/temu.py::listar_productos` | `bg.local.goods.list.query` por cubeta (`goodsSearchType` ENTERO: 1, 4, 5, 6) |
| Temu desde fuera | `backend/routers/investigacion.py` | `POST /api/investigacion/temu {type, params}`: solo admin con sesión, solo `…get`/`…query`, respuesta redactada |
| Walmart | `backend/services/walmart.py::listar_items` | `GET /v3/items?limit=50&offset=`; el token es `client_credentials` y no se cachea |
| Costos | `backend/services/costing_read.py` | `costing.costos_validados`: `costo_producto`, `costo_cbm`, `costo_total`, `contenedor`, `cajas`, `piezas_por_caja` |
| Prorrateo | laboratorio: `sandbox_precios/prorrateo.py::prorrateo_kubera` | `cbm_pieza = costo_cbm / 7500`; el m³ del contenedor se reconstruye sumando sus SKUs |

---

## 2 · Lo que se MIDIÓ el 6-oct-2026 y no estaba escrito en ningún lado

**Amazon lista sin identificadores, pero corta en ~1,000.** `searchListingsItems`
sin `identifiers` devuelve el catálogo completo de la cuenta con `numberOfResults`
y `pagination.nextToken`. Al llegar a mil deja de dar página siguiente aunque
falten. Salida: ordenar por `createdDate` ascendente y, cuando una pasada se
agota, arrancar la siguiente con `createdAfter` = la última fecha vista.

**Amazon sí da la comisión.** `getMyFeesEstimates` (`/products/fees/v0/feesEstimate`,
20 por llamada, ~0.5 por segundo) devuelve `ReferralFee`, cierre y FBA al precio
que se le mande. Es un cálculo: no guarda nada. OJO: es una ESTIMACIÓN al precio
publicado, no lo que Amazon cobró en un pedido — para eso sigue haciendo falta
Finances API (pendiente #5 de CLAUDE.md).

**TikTok contestó desde la laptop; Temu no.** TikTok también tiene lista blanca de
IP, pero ese día esta máquina pasó. Temu respondió `5000003 NOT_IN_IP_WHITE_LIST`.

**TikTok guarda lo borrado.** De cada tres productos que lista, uno viene con
estado `DELETED`. No es una publicación: hay que quitarlo antes de contar.

**Una de cada nueve publicaciones de ML lleva un SKU que no existe en Odoo**, y
casi todas son SKU padre. Odoo lleva el inventario por variante y ML publica
plano. Cruzar solo por SKU exacto deja a cientos de variantes con stock como «sin
publicar» teniendo a su padre publicado. Hay que colgarles la publicación del
padre — marcada, porque no es lo mismo que tener la propia.

**La ficha de Odoo no sirve para costear, y ahora se sabe cuánto.**
`standard_price × 19` reproduce `costo_producto` de la base en tres de cada cuatro
productos que tienen ambos. Pero en la línea «Productos Agente» el campo viene en
otra unidad, y usarlo como respaldo valuaba unos cuantos cientos de productos en
más que todo el resto del catálogo. La regla de Brandon («de Odoo, solo la foto y
el contenedor») queda confirmada con medición.

**A la base de costos le falta un tercio del inventario.** De los productos de
Odoo con stock, poco más de seis de cada diez tienen renglón propio en
`costing.costos_validados`. Lo demás son variantes sin renglón (heredan del padre
o de una hermana) y embarques recientes que todavía no se costean.

**El número de contenedor es mejor llave que el código.** `TXGU7222939 contenedor
7` en Odoo es `SZLS50224700 - 7` en la base: dos códigos del mismo embarque.
Comparando por el ordinal coinciden casi nueve de cada diez; por código, algo más de ocho.

---

## 2b · Precio de venta y categorías: lo que se midió al cambiar el enfoque

La tarde del 6-oct se pidió dejar los canales y poner el foco en **cuánto vale en
el mercado**, con categorías. Lo que hubo que averiguar:

**Ninguna fuente le pone precio a todo.** Dos de cada tres productos con stock no
están publicados en ningún marketplace. Para esos, el único precio que existe es el
de catálogo en WooCommerce — cuando lo tiene.

**El «Sales Price» de Odoo (`list_price`) no es un precio de venta.** En el
catálogo propio vale `1` o repite el costo en dólares de la ficha. En «Productos
Agente» trae cualquier cosa. Contra los productos que sí tienen precio vivo en un
marketplace no coincide prácticamente nunca. Y como hay flujos que lo copian tal
cual a WooCommerce (y de ahí a un feed), aparece disfrazado de precio en otros
lados: hay que descartarlo **por igualdad con el campo de Odoo**, no por fuente.

**Un borrador de WooCommerce vale `1.00`.** Es el marcador de lo que nunca pasó por
el Estudio. No es un precio.

**La REST de WooCommerce sí devuelve variaciones, si se le pregunta por SKU.**
`GET /products` lista solo padres y simples. Pero `GET /products?sku=A,B,C` trae
también variaciones (`type: variation`, con su `parent_id`) y acepta varios SKUs
separados por coma. Con lotes de 40 se cubren miles de variantes sin ir padre por
padre. Una variación no trae categorías: son las de su padre.

**Las categorías de WooCommerce NO forman árbol.** Son ~1,700 hojas, todas en la
raíz: el nombre es el de la hoja de Mercado Libre y la **descripción** dice
`ML: MLM123456` (así las crea `crear_producto.py::get_or_create_wc_categoria`).
Agrupar por ellas no contesta «qué tenemos». Lo que sirve es resolver ese id contra
el árbol de ML.

**El árbol de ML se baja entero en una llamada.** `GET /sites/MLM/categories/all`
devuelve las ~12,000 categorías con su `path_from_root` (30 MB, pide token). Y
`GET /categories/{id}` es **público**, sin token, para resolver de una en una.
Las raíces son 31.

**El prefijo del SKU no es una categoría.** La taxonomía de la casa
(`packing_taxonomia.SUBCATEGORIAS`) dice que `PAS-` es «Paseo Bebé», y bajo ese
prefijo hay bozales para perro y cambiadores de agua para pecera; `MES-` trae
toallas; `OFI-`, un hacha de cocina. Sirve como último recurso para no dejar un
producto sin clasificar, **marcado como estimado**, y nada más.

**Cuatro de cada diez categorías de WooCommerce no traen el id de ML.** Las creó
otro flujo y solo llevan el nombre — que casi siempre es ambiguo en el árbol
(«Tenis» existe siete veces: el calzado y el deporte). Se resuelven así: si todas
las candidatas cuelgan de la misma raíz, la raíz es segura; si no, el prefijo del
SKU desempata; si tampoco, no se adivina. Y se descartan las raíces que no son
mercancía: «Tecnología» existe en ML… dentro de *Servicios › Servicios de
Reparación*.

**El árbol de ML se contradice a sí mismo en dos nombres.** La raíz `MLM1071` se
llama «Mascotas» en el árbol y «Animales y Mascotas» en la lista de raíces; y
«Deportes y Fitness » trae un espacio al final. Sin normalizar, la misma categoría
sale en dos renglones.

**Multiplicar piezas por precio no siempre es valuar.** Lo que más infla un «valor
de venta» no son los precios altos, son dos desajustes de UNIDAD y de IDENTIDAD:

- *Paquete contra pieza.* La publicación vende «100 piezas» y Odoo cuenta bolsas
  sueltas: el valor sale cien veces mayor. Se detecta cuando el título de la
  publicación trae una cantidad que el nombre de Odoo no trae.
- *SKU reciclado.* La publicación es de otro producto que heredó el SKU (pendiente
  #7 de CLAUDE.md). Se nota porque el precio no guarda ninguna proporción con el
  costo.

Los dos se mandan a **revisión**: el precio se muestra, pero no se suma. El corte
para «desproporcionado» se midió sobre lo que hoy sí se vende (ahí solo el 3% más
alto pasa de 25 veces su costo); cuando no hay costo, se compara contra la mediana
de su subcategoría. La distribución con sus cifras no se escribe en este
repositorio: sale en `datos.json`, que no se sube.

**Un puñado de SKUs puede ser un cuarto del inventario.** Once referencias con
diez mil piezas o más concentraban más de la cuarta parte de las piezas libres. Con
esa forma, un total es tan bueno como el conteo de esos once: van primero en
cualquier limpieza.

---

## 2c · Contar el stock y prorratear: lo que se midió al comparar contra otro reporte

El mismo día apareció otra página de inventario con un par de puntos porcentuales
más de SKUs «con stock» que esta. Las dos leían Odoo y ninguna estaba mal. Lo que
hubo que averiguar para explicarlo:

**«Con stock» son al menos cinco números distintos**, con los mismos datos:

| Cómo se cuenta | Contra `free_qty` de activos |
|---|---|
| `free_qty` > 0, productos activos con referencia interna | la base: **lo que se pidió** |
| `qty_available` > 0 (físico, aunque esté reservado) | más: suma los que tienen todo reservado |
| lo anterior, sumando por SKU las piezas de productos archivados | más todavía |
| una fila por SKU y bodega | más: un SKU en dos bodegas cuenta dos veces |
| SKUs distintos en vez de productos | un poco menos: hay referencias repetidas |

**La regla de la casa (Brandon, 6-oct-2026): solo `free_qty` de productos
activos. Reservados y archivados, prohibidos** — no se cuentan, no se suman y no
se muestran. Lo de abajo sirve para EXPLICAR una diferencia, no para traerla.

**Archivar un producto en Odoo no le quita sus piezas.** `search_read` no ve los
archivados (el `active_test` del contexto los esconde), pero sus existencias siguen
ahí, con `free_qty` positivo, en un producto que nadie puede vender. Un reporte que
lea `stock.quant` o que pase `active_test: False` las encuentra y las suma al SKU.
Casi todos los archivados con piezas eran GEMELOS de un producto activo con la
misma referencia, creados el mismo día en una carga masiva y con cantidades casi
iguales a las del activo: huele a la misma mercancía capturada dos veces, pero eso
solo lo decide un conteo en bodega.

**Un SKU puede tener existencia en una bodega y cero en total.** Negativo en una
(se surtió de donde el sistema decía que no había) y positivo en otra por la misma
cantidad. Contado por bodega «tiene»; contado por producto, no.

**La hora casi no explica nada.** Entre dos lecturas con tres horas de diferencia
cambió la existencia física de un puñado de SKUs. Si dos reportes del mismo día
difieren por cientos de SKUs, es el criterio, no el reloj.

**Cómo se concilia.** No comparando totales: bajando las dos listas y cruzándolas
SKU por SKU. Todo lo que uno tiene y el otro no cae en una de estas cajas: todo
reservado · solo en un archivado · neto en cero · sin referencia interna · se movió
entre lecturas. Si sobra algo fuera de esas cajas, ahí sí hay un error que buscar.
Y el árbitro es el contador del propio Odoo (`search_count` con el mismo dominio).

**El prorrateo se rompe por el volumen, no por la fórmula.** El volumen por pieza
de la base se reconstruye del flete (`costo_cbm / 7500`), y ese dato falla seguido:
el cartón capturado como si fuera la pieza. Como el contenedor se reparte POR
VOLUMEN, un puñado de esos errores —poco más del uno por ciento de las piezas— se
llevaba más de un tercio de todo el prorrateo. Tres pruebas baratas los separan:
más de 1.5 m³ por pieza; prorrateo mayor al precio al que se vende; prorrateo de
más de diez veces su costo de producto (ahí solo queda el 2% más alto, y conviven
errores con mercancía voluminosa y barata de verdad: por eso es «revisar»). Lo que
cae ahí se muestra y no suma. Un prorrateo inverosímil tampoco se hereda a las
variantes ni entra a la mediana del contenedor.

---

## 2d · Contar desde los packing lists: lo que se midió el 7-oct-2026

**Los packing lists se bajan de Drive sin credenciales.** `costing.packing_archivos`
guarda el `drive_file_id` de cada uno y las carpetas son públicas. Binario:
`drive.google.com/uc?export=download&id=`; si es grande, Drive contesta un aviso de
antivirus y el archivo sale por `drive.usercontent.google.com/download?…&confirm=t`.
Hoja nativa de Google: `docs.google.com/spreadsheets/d/<id>/export?format=xlsx`. La
copia que producción guarda en Storage pide una llave de servicio; no hace falta.

**Un contenedor tiene DOS archivos y no dicen lo mismo.** El original del proveedor
no trae SKU. El validado de bodega (carpeta Ferraforme) es el mismo archivo con la
columna «SKU ODOO» y lo contado en físico. El conteo de bodega es la unidad en que
Odoo mueve: donde el proveedor declara cartones o paquetes, bodega cuenta piezas (o al
revés). Para restar movimientos de Odoo hay que usar el de bodega.

**Tres maneras de contar dos veces la misma mercancía**, y las tres pasaron:
sumar el original y el validado de un mismo contenedor; sumar a un SKU las piezas
que dice la base de costos cuando esas piezas ya están entre los renglones sin SKU de
su contenedor; y sumar dos archivos de un contenedor que resultan ser copia uno del
otro (se distinguen comparando sus cantidades renglón por renglón: una copia coincide,
dos proveedores en el mismo contenedor no).

**El renglón del original se ubica por la foto.** Bodega copia el archivo del
proveedor, así que el renglón donde anotó el SKU trae incrustada LA MISMA imagen
(mismo sha1) que su renglón en el original: empata la mayoría sin IA. En contenedores
sin validar funciona lo mismo contra la foto del producto en Odoo (`ir.attachment`,
`res_field = image_1920`, da el sha1 sin bajar la imagen), pero SOLO entre los SKUs que
Odoo o la base de costos mandan a ese contenedor: en todo el catálogo las fotos se
repiten demasiado.

**Lo que Odoo llama «cliente» es casi todo traslado.** Las entregas a ubicación de
cliente incluyen los envíos a Full, FBA y WFS: en la muestra, más de tres cuartas
partes de las piezas «entregadas» iban a Mercado Libre Full. Se separan por el NOMBRE
del socio (es un contacto nuevo por orden: el id no sirve). Y la mitad del historial de
movimientos son traslados de rack a rack: para contar entradas y salidas hay que
quedarse con los que cruzan la frontera de la bodega (un lado interno y el otro no).

**El buscador de Mercado Libre no se puede leer.** `/sites/MLM/search` contesta 403
a cualquier aplicación y la página pública redirige a un muro de verificación:
producción lo resuelve con un navegador de pago y eso no se hace desde aquí. Lo que SÍ
está abierto: `domain_discovery/search` (el predictor de categoría, sin token) y
`/products/search` + `/products/{id}/items` (el catálogo). El catálogo rinde poco: de
catorce productos que devuelve una búsqueda, entre cero y cuatro tienen oferta activa.

**Amazon sí da precio de la competencia por palabra clave**, con dos llamadas:
`searchCatalogItems` y `getCompetitivePricing` (20 ASIN, una llamada cada dos
segundos: ese límite manda toda la corrida). Hay que contar una vez cada familia de
variantes.

**Sin juez, el promedio no sirve.** «Zapatero organizador» devuelve zapateros de
todo tipo y accesorios. Un modelo de lenguaje barato, con las reglas del juez de
Competencia de producción y la respuesta compactada (`[[1,"m",1],…]`), cuesta
centésimas de centavo por producto. Con `deepseek-flash` hay que mandar
`"thinking": {"type": "disabled"}`: si no, razona, tarda el triple y cobra el
razonamiento como salida.

**Multiplicar piezas por el precio del paquete infla N veces.** La publicación vende
«paquete de 5 focos» y el inventario cuenta focos. Si el nombre con que se compró no
habla de paquete ni trae ese número, el precio de referencia se parte entre N. Y un
precio sostenido por UNA sola publicación, o un producto sin SKU cuyo nombre son dos
palabras («thermos»), se muestra pero no se suma: sin esas dos reglas, tres renglones
valían más que todo lo demás junto.

## 2e · Unir el precio de mercado de otro archivo: lo que se midió el 7-oct-2026

Eduardo cotizó en la API de Mercado Libre cada línea de los packing lists originales. Al
cargar su archivo en esta página salieron cuatro cosas que no eran obvias.

**Sus filas y las nuestras son las mismas.** El `sha256` de sus archivos es el del índice
de `costing.packing_archivos`, y su «fila de Excel» es nuestro renglón más uno. Casi todas
sus líneas caen en un renglón con el mismo nombre. Por eso lo que no tiene SKU se une por
archivo y renglón, sin adivinar por texto. Dos archivos no casan (uno se leyó aquí con el
lector genérico): esos renglones se quedan sin precio en vez de tomar el de otro.

**Su unidad no es la nuestra, y hay dos casos opuestos que no se distinguen solos.** Él
cuenta con el packing list del proveedor y en unidades vendibles (un «paquete de 10 focos»
es una unidad); aquí se cuenta con el conteo de bodega cuando existe. Donde difieren:

- *Otra unidad.* El proveedor anotó paquetes y bodega contó piezas. Su precio × nuestras
  piezas infla el valor tantas veces como piezas trae el paquete.
- *Anclaje parcial.* Él ligó al SKU un solo renglón y bodega contó todo lo que llegó de ese
  SKU (y Odoo lo confirma). Su valor repartido entre nuestras piezas deja el producto en
  centavos.

Lo que decide entre los dos es el dinero, que no depende de cómo se cuente: **el FOB del
renglón es el mismo se cuenten paquetes o piezas.** La regla quedó así: si las piezas
coinciden (±25%), el valor del producto es el suyo; si no, se usa el conteo de bodega con
su precio por unidad y su mismo tope —el producto no vale más de 10 veces el FOB de sus
renglones—. El primer caso queda topado; el segundo pasa sin tocarse. Cuando el conteo
difiere, el COSTO por pieza también hay que rehacerlo con el FOB total entre las piezas de
bodega: si no, cualquier estimado por costo hereda el mismo error.

**Un insumo no es «todas sus líneas excluidas».** Un SKU puede tener una línea grande
excluida (el costal, la caja) y otra chica, mal anclada, con precio. Si se pide que TODAS
estén excluidas, el SKU entero se valúa con el precio de la chica. Es insumo cuando la
mayor parte de sus piezas lo es.

**Con un precio de Mercado Libre para casi todo, Amazon se estima mejor desde ahí que
desde el costo.** Lo que no se pudo medir en Amazon se estima con su precio de Mercado
Libre × la razón entre los dos precios, medida en los productos de su misma categoría que
tienen los dos (una tabla para cuando el de ML es del producto y otra para cuando es banda
de categoría: no dan la misma razón). El costo × múltiplo, que era el primer recurso, daba
disparates justo donde la unidad del costo estaba mal. Y un «medido» de Amazon a muchas
veces el precio de Mercado Libre del MISMO producto casi siempre es otro producto con el
mismo nombre —un título de dos palabras no le da al juez con qué rechazar— o un paquete
contra una pieza: no se toma como medido.

**Antes de fiarse del total, compararlo con el del otro.** Todo lo comprado, valuado con
estas reglas, da prácticamente su total para sus contenedores. Si no hubiera cuadrado, el
error habría estado en la unión, no en los precios.

**Un SKU sin nombre no es un SKU sin información.** Hay SKUs que bodega anotó en el validado
y que Odoo no conoce: su renglón viene vacío o en chino, así que la etapa de títulos los
saltaba, y sin título no hay término de búsqueda: tampoco se buscaban en Amazon. Se ven
fácil porque en la página su título es el propio código. Casi todos tienen de dónde
titularse: la revisión a mano y el nombre traducido del archivo de precios de Eduardo, el
texto de su renglón (el modelo lee chino) y, si no hay texto, la foto del renglón. Lo que
no tiene ni texto ni foto resultó no ser mercancía: son notas de bodega escritas en la
celda del SKU («llegaron 4 piezas de más»). **Antes de decir «están todos los títulos»,
contar las filas de la página cuyo título es igual a su SKU.**

**El mismo packing list puede estar dos veces con el código mal escrito.** Un contenedor
apareció una vez con su número y otra como «sin número», porque la segunda copia tenía una
letra de más en el nombre: el mismo archivo, byte por byte. Los repetidos solo se buscaban
DENTRO de cada contenedor, así que sus piezas se contaron doble. Ahora un archivo «sin
número» con los mismos renglones y las mismas cantidades que otro ya contado es copia. Se
notó porque al filtrar ese contenedor la página mostraba más piezas de las que decía su
renglón. Dos cosas más salieron de ahí:

- *Dos archivos iguales tienen el mismo sha256 y dos ids.* Si un tercero (el archivo de
  precios de Eduardo) identifica el archivo por su sha256, sus líneas tienen que valer para
  los dos ids: el inventario se queda con uno solo, y no siempre es el que el diccionario
  guardó al último. Sin eso, el contenedor bueno se quedaba sin precio.
- *Filtrar por contenedor no es filtrar filas.* Un producto sin SKU junta los renglones con
  el mismo nombre de varios contenedores. Al filtrar un contenedor hay que contar solo sus
  piezas de ahí (y repartir en esa proporción lo que salió y lo que queda), no la fila entera.

**Dos contenedores con las mismas fotos no son un duplicado.** Hay pedidos que se parten en
dos contenedores hermanos: mismos productos, mismas fotos, cantidades casi iguales pero no
idénticas. Lo que distingue una copia de un hermano son las cantidades renglón por renglón.

---

## 3 · Tres cosas que NO se hacen, aunque el código lo permitiría

1. **Renovar tokens.** El de ML se rota al usarse. Un script que lo renueve deja a
   producción con un refresh_token muerto.
2. **Leer los tokens sin permiso expreso.** Viven cifrados en `ops.ml_tokens` y
   `ops.tiktok_tokens`. Sacarlos de la base de producción es una lectura sensible:
   se pide autorización a Brandon cada vez.
3. **Entrar a `/investigacion` con una llave de máquina.** Ese endpoint es para una
   persona con sesión, a propósito. Si no hay sesión, Temu se reporta como no leído.
