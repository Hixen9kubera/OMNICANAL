# ENCARGO — Pestaña INVENTARIO · Catálogo Maestro

> Pégale esto completo a un chat nuevo. Está escrito para que arranque SIN leer
> el historial de ninguna sesión anterior.
>
> Reconocimiento hecho el **2026-09-02** por 12 agentes (773 llamadas a
> herramientas). Todo número de aquí abajo está **medido**, salvo lo marcado
> `⚠️ POR CONFIRMAR`. Las citas `archivo:línea` **caducan**: el repo vive en
> OneDrive y hay varias sesiones empujando a `main` el mismo día. Verifica antes
> de creer.

---

## 0 · LO QUE HAY QUE CONSTRUIR

Una pestaña **Inventario · Catálogo Maestro** en el panel OMNICANAL, con:

- una tabla del catálogo: **imagen, contenedor, cajas, piezas, variantes**;
- un botón por renglón: **Historial / Trazabilidad — entradas y salidas**.

**Piloto acordado: 10 SKUs.** Nada de barridos completos hasta que el piloto
esté aprobado por Brandon.

```
ROP-0731-BLN   ACC-0907-MET   TV-0001-MET    JUGU-1153-MET   HERR-0343-MET
ELEC-0034-EST  OFI-0412-EST   DEPO-0048-EST  HERR-0146-EST   VEH-0148-EST
```

**El porqué del encargo, en palabras de Brandon:** *"Odoo va a desaparecer y
necesitamos el control de nuestro inventario. Va a desaparecer el Packing List
en Bodega."*

**El frontend se está diseñando en paralelo con Claude Design.** No inventes la
UX: construye el backend y una pantalla funcional, y deja los estilos listos
para recibir el diseño.

---

## 1 · LEE ESTO ANTES DE DISEÑAR NADA

### 🔴 La trampa: lo que una persona escriba se borra solo en ≤20 minutos

Y peor: **la bitácora va a culpar a Odoo de haberlo borrado.**

1. **Hoy no existe NINGUNA ruta por la que una persona escriba stock.** Los
   únicos escritores son `stock_watch._escribir_woo` (automático), `sync_woo`
   (barrido masivo desde Odoo), la compensación de `pedidos_ml`, y el propio Woo
   al vender. Esta pestaña sería **la primera puerta humana al inventario**.
2. Cada 20 min, `backend/services/stock_watch.py` (≈ línea 373) hace
   `destino = ahora_od` — **el `free_qty` de Odoo, sin mirar la foto ni el
   origen del número**. Un ingreso de 500 piezas que Odoo no conoce se revierte
   en la siguiente pasada. No es teoría: **163 de ~300 correcciones desde el
   28-ago SUBIERON el stock de Woo**.
3. El fan-out **empuja el número revertido** a TikTok, Temu, ML y Walmart.
4. El remate: la bitácora escribe `motivo='delta de Odoo (foto 97 -> 97)'` —
   o sea **reporta que Odoo se movió cuando Odoo no se movió** (50 de 293 filas
   medidas ya dicen eso). Quien investigue va a ir a buscar a Odoo, que es
   inocente.

**Y el cepo que cierra la trampa: la salida obvia no sirve.** Apagar
`STOCK_WATCH_ENABLED` para proteger la escritura humana **también mata el tramo
`movidos_woo → fan-out`**, que replica las ventas de Woo a los canales y **no
depende de Odoo**: se congelan los cinco marketplaces. La única salida limpia es
`STOCK_WATCH_ABSOLUTO=false`, y eso reactiva el modo delta, que se abandonó
justo porque **no ve las reservas**.

> **Orden correcto: decidir la precedencia (§3, decisión 1) y arreglar el motivo
> del log ANTES de que exista un botón que la gente pueda usar.**

### 🔴 Contradicción sin resolver, y es la más incómoda

Los informes tratan `free_qty` de Odoo como *"el master"* y *"la verdad"*. La
memoria del proyecto (`odoo-datos-no-confiables`) dice: **«sus números son 0%
confiables (58% se autocontradicen)»**. Nadie reconcilió las dos cosas.

Si el master no es confiable, el modo absoluto no está propagando la verdad:
está **propagando un número dudoso más rápido y a más canales**.

---

## 2 · EL ESTADO REAL, MEDIDO

### 2.1 Dónde vive el stock hoy

| Dónde | Cuánto | Quién escribe |
|---|---|---|
| **Odoo `free_qty`** | 13,141 SKUs · 1,125,654 libres | el ERP. **Es el master** |
| WooCommerce `_stock` | 14,710 SKUs · 1,123,083 piezas | `stock_watch`, cada 20 min |
| `stock_watch_foto` (MySQL) | 14,714 SKUs | la foto que DECIDE |
| `channel.listings` | por canal | sync 15 min + fan-out |

**`STOCK_WATCH_ABSOLUTO` está ENCENDIDO.** No se probó leyendo la variable
(Railway devuelve los valores redactados) sino midiendo el efecto: en 293/293
correcciones desde el 28-ago el destino escrito a Woo es **exactamente** el
`free_qty` de Odoo, y 50 dispararon con **Odoo quieto** (`foto 97 → 97` →
`Woo 98 → 97`), lo cual es imposible en modo delta.

⚠️ **POR CONFIRMAR — y es el número que sostiene medio encargo:**
`free_qty = qty_available − reserved_quantity` (1,130,523 − 4,869 = 1,125,654).
Un solo agente lo midió por XML-RPC y **no dejó artefacto reproducible**; en su
propia medición los quants suman **1,164,255**, un hueco de **33,732 piezas sin
explicar**. Vuélvelo a medir tú antes de apoyar nada aquí.

### 2.2 Lo que NO existe hoy — y es lo que hay que construir

**Ninguna tabla del sistema guarda un DELTA de piezas firmado con una causa y un
responsable.** Ni una.

- `ops.fanout_log` (40,156 filas) es lo más parecido a un libro de bodega, pero
  **en las acciones que mueven inventario real** —`odoo_delta` (1,639) y
  `woo_cambio` (497)— **las columnas `stock_drop`, `objetivo` y `stock_canal`
  están 100% NULAS**. El antes→después vive dentro del texto libre `resultado`
  (`"Woo 7 -> 10"`).
- `channel.listing_history` (319,048 filas) sí guarda antes/después, pero es
  historia **de publicaciones por canal**, sin actor, y con 83 mil filas de
  `corte_channel` donde un salto puede ser un cambio de fuente, no un movimiento.
- `services/bitacora.py` es la entrada única de acciones de persona y funciona
  (1,352 filas con actor, 8 personas). **Pero sus constantes `PRECIO` y `STOCK`
  están declaradas y no tienen ni un solo call site en todo el repo.**
- **No hay absolutamente nada que registre ENTRADAS de mercancía.** Una
  recepción aparece como un `odoo_delta` positivo 20 minutos después: sin
  motivo, sin documento, sin quién.

**Hay UN precedente y vale oro:** 74 filas con `accion='odoo_master'` del
20-ago 18:10–18:24, **con las tres columnas numéricas pobladas**. Corrió una vez
y su escritor ya no está en el repo. Es el molde que esta pestaña necesita.

👉 **Hay que crear tabla nueva** (`ops.stock_movimientos` no existe). Lo que
falta no es una consulta más lista sobre lo existente: **es el dato**.

### 2.3 Contenedor, cajas, piezas: vivos a medias

Viven en **`costing.costos_validados`** (15,838 filas: 15,348 con contenedor,
13,557 con `cajas > 0`, 15,339 con `piezas_por_caja > 0`).

🔴 **Las columnas `contenedor` / `cajas` / `piezas_por_caja` están MUERTAS EN
ESCRITURA.** `costing_mirror.upsert_validados` nombra 8 columnas en su INSERT y
ninguna es ésas. Las 8 filas creadas después del corte del 13-ago las tienen en
NULL. Son una **foto histórica migrada**, no un dato vivo.

🔴 **Y la conclusión que ningún informe sacó: el número de contenedor viene de
Odoo (`container_numbers`), que es hoy la única fuente que sigue creciendo. Si
Odoo se va, el CONTENEDOR se va con él** — y "contenedor" es una de las cinco
columnas que pide el encargo.

Lo único que hoy guarda procedencia es **`costing.caja_compartida`** (23 filas
desde el 28-ago): de qué archivo y qué renglones salió un costo.

**El "packing list" no es un dato del sistema:** es el `.xlsx` del proveedor en
una carpeta pública de Google Drive (188 archivos, 105 útiles), leído raspando
HTML sobre una semilla congelada en `backend/services/data/packing_lists_drive.json`,
y empatado al SKU por foto (sha256 → dHash ≤8/64) y, si falla, por título con IA.

⚠️ Hay **dos pantallas que dicen hacer lo mismo y no lo hacen**: el *Resolver
desde packing list* (viejo) escribe a la `costos_validados` de **MySQL —
congelada desde el 13-ago—**, o sea que "guarda" sin mover ningún precio; el
*Validar publicados* (nuevo) escribe a kubera y sí funciona.

### 2.4 Variantes: el terreno más engañoso del catálogo

- La única verdad estructural es `wp_posts.post_type` (`product` vs
  `product_variation`) unido por `post_parent`. En kubera la traducción es
  **`core.products.wc_parent_id` y NADA más**: `has_variations` y `parent_sku`
  están **vacías en las 22,363 filas** (el propio repo lo documenta).
- Medido: **7,285 productos** (1,501 padres con hijas + 5,784 simples) y
  **7,428 variaciones**. **7,426 SKUs son variantes de algo** — la mitad del
  catálogo.
- 🔴 **El nombre del SKU NO distingue padre de simple**: 5,765 productos
  SIMPLES tienen sufijo igual que una variante. Cualquier diseño que deduzca la
  jerarquía del nombre nace roto.
- 🔴 **La regla "NUNCA publicar un SKU padre" NO está implementada en ninguna
  parte.** Al contrario: `publicar_ready.py` y `publicar_walmart.py` están
  escritos **expresamente** para publicar padres variables resolviendo el precio
  desde las variantes. Hoy hay **~1,286 renglones de publicación sobre SKUs
  padre** en `channel.listings` (ML 816, Amazon 256, TikTok 160, Walmart 42,
  general 11, Temu 1).

### 2.5 Imágenes

La imagen principal **no vive en kubera**: es el attachment que apunta la
postmeta `_thumbnail_id` de WordPress (`wp_posts.guid` = URL); la galería es la
CSV `_product_image_gallery`. `enrich.product_media` es una **caché de
procesamiento** (1,711 SKUs de 22,363), no una fuente.

👉 **El helper que debe usar la tabla nueva es `wp_db.imagenes_por_wc_id`**
(`backend/services/wp_db.py`, envuelto async en `woocommerce.py`): 3 queries por
lote, **resuelve VARIANTES** —que el REST `include=` no devuelve— y hereda la
miniatura del padre cuando la variante no tiene propia. Va directo a MySQL, así
que **LiteSpeed no interviene y no hace falta `_cb`**.

### 2.6 Los 10 SKUs del piloto — por qué son justo éstos

**Son mercancía QUE NO HA LLEGADO.** Los 9 que existen en Odoo tienen
`free_qty = 0` y `qty_available = 0`, pero **`incoming_qty` entre 20 y 992
piezas**, y ninguno se ha vendido nunca. El stock de Woo y el de Odoo coinciden
en 0 — pero coinciden **porque nada existe todavía**, no porque el sync
funcione.

Es decir: el piloto es exactamente **el caso de la ENTRADA de contenedor**, que
es lo que hoy no registra nadie. Buen piloto.

🔴 **Y esconden un defecto grave. Los sufijos `-EST` y `-MET` NO son colores:**
son el **relleno del generador de SKU** (`packing_taxonomia.ATTR_DEFAULT = "EST"`,
y `MET` = "Metal" como material). Son el sufijo nº 1 y nº 3 del catálogo (1,832
y 998 de 22,363 SKUs).

Pero un segundo diccionario, **`variables.py::COLOR_MAP`, los lee como COLORES**
("EST"→Estampado, "MET"→Metálico) y agrupa por los dos primeros segmentos del
SKU. Eso ya **fusionó en WooCommerce 104 pares `-EST`/`-MET` bajo un solo padre
variable, y en 34 de ellos los dos "colores" son PRODUCTOS DISTINTOS** según
Odoo (refractómetro + hebilla de mancuerna; cierra-puertas + funda de palanca).

El piloto trae los dos lados de la falla:
- `HERR-0343-MET` y `JUGU-1153-MET` **sí** son variaciones reales con
  `attribute_color = Metálico`;
- los otros 7 son **productos simples sueltos** cuyo sufijo no corresponde a
  ninguna variante;
- **`DEPO-0048-EST` no existe ni en Woo ni en Odoo**: es un costo huérfano de
  packing list cuyo producto real vive bajo `DEPO-0048-MET`, que a su vez no
  tiene costo.

### 2.7 Si Odoo desaparece, qué se cae

- **La cadena viva**: Odoo → `stock_watch` (20 min) → Woo → fan-out →
  TikTok/Temu/ML/Walmart. Si Odoo enmudece, `stock_watch` **aborta la pasada
  completa** y con ella muere también el tramo que replica los cambios de Woo a
  los canales, **que ni siquiera necesitaba a Odoo**.
- La mayoría de los puntos **falla CERRADA** (aborta, no vacía), que es el buen
  diseño. Tres excepciones peligrosas:
  - `odoo._uid()` cachea el fallo con `@lru_cache` y **jamás se auto-sana**: una
    caída de dos minutos deja Odoo desconectado hasta el próximo deploy;
  - el ETL de las 06:15 escribe `odoo_id = NULL` en los 13,126 productos de
    `core.products` si Odoo no contesta *(⚠️ leído en el código, nunca observado
    en vivo)*;
  - `/api/health` llama a `odoo.ping()` sin timeout de socket configurado
    *(⚠️ mismo caso: código leído, congelamiento no observado)*.
- **Odoo es la ÚNICA puerta de entrada de SKUs nuevos** (el robot de Alibaba
  está desconectado desde el 23-jul).
- **Odoo es el único lugar donde existe el stock POR ALMACÉN** — TEXCO (135) /
  TEXCO II (150) / DROP OFF (142). Eso es lo que hace irremplazable el reparto de
  las órdenes de venta de TikTok/Temu. **No tiene casa en ninguna base.**
- La foto de DROP OFF de Análisis está **hardcodeada y caducada**
  (`fulfillment.py`, ~línea 2846: 2026-08-31, 97 SKUs, 11,171 piezas).

---

## 3 · LAS CINCO DECISIONES DE BRANDON — antes de una línea de código

Estas **no son técnicas**. Pregúntaselas; no las resuelvas tú.

**1 · ¿Quién tiene la última palabra sobre el número: la persona en bodega o
Odoo?** Es la madre de todas. Hoy Odoo gana siempre, cada 20 minutos, sin
excepción. Si un ajuste humano debe ganar, hace falta un mecanismo de
precedencia **que no existe en ninguna parte del sistema** (el único precedente
es el candado de COSTO VALIDADO, `revisado_at`, que vive dentro del SQL). **Sin
esta decisión la pestaña no puede escribir nada.**

**2 · ¿Quién captura una entrada de contenedor, y contra qué papel?** Si la
captura hace bodega con el packing list en mano, el packing list deja de ser un
insumo de costeo y pasa a ser **un documento operativo** — y eso cambia la
pestaña entera.

**3 · ¿"Disponible" es lo FÍSICO o lo LIBRE?** Odoo publica `free_qty` (físico −
reservado). Woo no tiene el concepto: `wp_wc_reserved_stock` tiene **0 filas**.
Si muestras físico, los canales sobrevenden lo apartado; si muestras libre,
bodega ve un número que no coincide con el anaquel. Tercera pata: hay **141
salidas en `waiting` que nadie clasificó** entre ventas y traspasos internos —
**hoy un traspaso entre TEXCO y TEX2 saca mercancía de la venta sin razón
comercial**.

**4 · ¿El inventario es por almacén o un solo bote?** `free_qty` suma TEXCO +
TEX2 + DROP OFF, así que **hoy se publica en los canales DROP mercancía que está
en TEXCO**. Si Brandon quiere por almacén, la tabla necesita la dimensión de
ubicación **desde la primera migración**: metérsela después significa volver a
contar.

**5 · ¿El conteo físico es por CAJA o por PIEZA, y quién arregla los rotos?**
Los packing lists son una fila **por caja**; Odoo cuenta piezas; Woo vende
piezas. Y las columnas puente están rotas: **1,786 SKUs con `cajas = 0` y 29 con
`piezas_por_caja` entre 0 y 1** — un divisor menor que uno **multiplica** el
flete.

---

## 4 · LA PREGUNTA QUE DEFINE EL ESQUEMA

**¿La pestaña ES el master del inventario, o es un visor?**

- **Visor** → basta leer. Ni siquiera hace falta libro de movimientos: es una
  vista sobre `stock_watch_foto` + `channel.listings` + WordPress.
- **Master** → `stock_watch` tiene que dejar de leer `odoo.listar_catalogo()`
  y leerla a ELLA, y hace falta un **saldo inicial**: 1,125,654 piezas en 13,141
  SKUs que alguien tiene que dar por buenas. **Nadie ha preguntado si alguien va
  a contar la bodega.**

Y su segunda mitad: **¿cuál es la FILA del catálogo maestro?** Si es el SKU,
7,426 de 14,710 son variantes, 1,286 renglones de publicación cuelgan de padres,
y hay 81 `listing_id` de ML que reclaman dos SKUs. **Agrupar por SKU sin decidir
el grano hereda el doble conteo desde el día uno.**

Tercera, más corta: **¿cuánta historia se guarda?** `channel.listing_history`
crece ~10,000 filas/día y nadie propuso retención. `ops.webhook_events` ya se
purga a 3 días con un `pg_cron`.

---

## 5 · CÓMO SE AGREGA UNA PESTAÑA (receta verificada y re-ejecutada)

Toca **siete archivos** y una tabla de permisos que muerde en silencio:

1. Router nuevo: `backend/routers/inventario.py` con
   `APIRouter(prefix="/api/inventario")`.
2. `backend/main.py`: importarlo (la tupla del import, ~línea 25) **y**
   `include_router` (~línea 206). **Dos sitios.**
3. 🔴 **`backend/core/rbac.py`** — hoy `rol_requerido("GET","/api/inventario")`
   devuelve **`admin`**, porque lo no listado nace cerrado. **La regla es por
   (MÉTODO, PREFIJO): listar el `GET` NO cubre el `POST` ni el `PUT`.** Van tres
   incidentes reales de KAMs topándose con un 403 por esto.
4. `python backend/scripts/auditar_rbac.py` — hoy da
   `170 rutas declaradas · 170 clasificadas · 0 por omision`, exit 0. Con una
   ruta sin clasificar sale 1. **Córrelo antes de dar por terminado.**
5. `frontend/lib/types.ts` — reusa `Paginacion`.
6. `frontend/lib/api.ts` — helper con `getJSON`/`postJSON`. **NUNCA `fetch`
   pelón** (incidente del 5-ago documentado en el propio archivo).
7. `frontend/app/inventario/page.tsx` — la carpeta ES la ruta. **`SesionGuard`
   NO se pone en la página**: ya lo monta `app/layout.tsx`. Modelos a copiar:
   `app/costos/page.tsx` y `app/monitoreo/page.tsx`.
8. `frontend/components/AppNavbar.tsx` — la entrada en el array `ITEMS`.
9. Versión `+0.1` en `backend/main.py` (**líneas 132 y 214**) + entrada
   DETALLADA en `README.md`.

---

## 6 · REGLAS DE LA CASA QUE APLICAN AQUÍ

1. **`backend/vendor/` NO SE TOCA.** Se ajustan los adaptadores.
2. **En una corrutina, nada síncrono de red o disco.** `psycopg2`, `pymysql`,
   `httpx` sin `await`, `requests`, `xmlrpc`: todo eso **detiene el backend
   entero**. Va en `asyncio.to_thread`. Costó un apagón de cinco horas.
3. 🔴 **NUNCA marques la sesión read-only contra kubera.** El DSN apunta al
   pooler en **modo transacción (6543)** y las conexiones **se comparten**: un
   `set_session(readonly=True)` se queda pegado y lo hereda el backend de
   producción registrando una venta. Reventó dos veces. Si necesitas la
   garantía: `BEGIN; SET TRANSACTION READ ONLY; …; ROLLBACK;`.
4. **Cambios que encienden o apagan flujos vivos** (stock masivo, variables de
   producción): mostrar qué se va a encender y **esperar el dale de Brandon**.
   Features de lectura/UI: deploy directo a `main`.
5. **`git pull --rebase` antes de cada push** — hay varias sesiones en `main`.
6. **Cruces siempre en vivo.** Nunca contra `canal_inventario`, `ml_progress` ni
   `amazon_progress`: el caché ya ocultó 754 publicaciones de ML.
7. **MySQL `kubera_ml` está congelado desde el 13-ago.** Leer de ahí devuelve
   datos de agosto. Un `None` de una tabla detenida no significa "no existe":
   significa **"ya no sé"**.

---

## 7 · CÓMO EMPEZAR

1. **Contesta §3 y §4 con Brandon.** No escribas esquema antes.
2. **Re-mide lo marcado `⚠️ POR CONFIRMAR`**, empezando por las reservas.
3. **Mide los 10 del piloto tú mismo** — no te fíes de §2.6, verifícalo.
4. Diseña `ops.stock_movimientos` con lo que el precedente `odoo_master` ya
   demostró: antes, después, delta, causa, documento, **actor**.
5. Construye la lectura primero (tabla + historial). **Escritura solo después de
   la decisión 1.**
6. Piloto de 10 → visto bueno de Brandon → catálogo completo.

**Y lo que no debes hacer:** no toques `stock_watch` ni apagues
`STOCK_WATCH_ENABLED` sin entender §1 completa. Apagarlo congela cinco
marketplaces.

---

## 8 · AVISO SOBRE ESTE DOCUMENTO

El material salió de 12 agentes. Un crítico de completitud auditó **6 de los 11
informes** (los otros 5 se perdieron por un recorte mío del material, no por
falta de trabajo) y encontró:

- una afirmación **falsa** (`odoo_master` "nunca se implementó" — tiene 74 filas);
- un "cero discrepancias en 14,714 filas" que en realidad es **13,080
  comparables**, y el 11% restante es justo donde viven los problemas;
- un conteo inflado ~1.5× (869 publicaciones sobre padres → **1,286**);
- y varias afirmaciones marcadas *verificado* cuya evidencia es **código leído,
  no evento observado**.

**Trata cada número de aquí como una hipótesis bien fundada, no como un hecho.**
Vuelve a medir lo que vaya a decidir tu diseño.
