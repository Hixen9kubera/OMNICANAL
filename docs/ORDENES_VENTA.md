# Órdenes de venta propias (Inventario → Órdenes de venta)

> Pedido de Brandon, 2-oct-2026 — ClickUp `86bcbfnkw` (Omnicanal / Bodega).
> **Reescrito el 6-oct-2026**: el módulo se adaptó al esquema que rediseñó
> Eduardo (migraciones `0064` + `0065`, plan v3). El diseño del 2-oct —reserva
> parcial contra la foto de Odoo, `ops.ov_stock`, «regresar a borrador»— **ya
> no existe**.
>
> Quién manda, en este orden: (1) `supabase/migrations/0064_ops_ordenes_venta.sql`
> y `0065_ops_inventario_kubera.sql`; (2) `docs/MIGRACION_0064_0065_GUIA_AGENTE.md`
> (el contrato de la base para el código); (3) este documento, que describe lo
> que el CÓDIGO hace con ese contrato. Si este documento y la guía se
> contradicen, gana la guía y aquí hay un bug.

## 1. Qué es

La **orden de venta** —el documento que le dice a Bodega qué surtir— vivía sólo
en Odoo. Ésta es la orden **propia**: nace, se confirma, aparta stock, se
entrega y se cancela **en kubera (Supabase)**. El módulo no escribe en Odoo, en
WooCommerce ni en ningún marketplace.

| Paso | `ops.ov_ordenes.estado` | En pantalla | Quién |
|---|---|---|---|
| Se captura | `borrador` | BORRADOR | cualquiera que escribe (operador o admin), la API |
| Se confirma y **aparta stock** (un mismo paso) | `confirmada` | CONFIRMADA | operador o admin, con la bandera encendida |
| Bodega la entrega a la paquetería | `entregada` | DELIVERED | operador o admin |
| Se cancela sin que haya salido nada | `cancelada` | CANCELADO | el canal (automático), quien escribe si es borrador, un admin si ya estaba confirmada |
| Se cancela con piezas ya afuera | `entregada_cancelada` | DELIVERED but CANCELLED | el canal o un admin. Abre `devolucion_estado = 'pendiente'` |

Lo que cambió respecto a una orden de Odoo, y por qué:

- **La bodega va por renglón** (`ov_lineas.almacen`) y sólo valen las bodegas de
  kubera que admiten órdenes (`ops.almacenes.admite_ov`). Hoy: `ENSAYO`, la de
  práctica. `TEX3` existe y nace apagada.
- **Apartar es todo o nada.** Si un solo renglón no alcanza, no se confirma
  nada y la pantalla dice cuál: «ACC-0250-NEG pide 4 y hay 2 libres en ENSAYO.
  No se apartó nada.» No hay reserva parcial ni «reintentar reserva».
- **Fuera de borrador el contenido no cambia** (lo impone un trigger de la
  base, no el código). Un error en una confirmada se resuelve **cancelándola**
  o, si es de plano un error de captura, **borrándola** (sólo admin, con motivo;
  queda el rastro de quién y por qué, y el folio no se recicla).
- **La entrega es por renglón** y puede ser parcial: Bodega dice cuántas piezas
  salieron de cada renglón (de 0 a la cantidad). Cada pieza que sale queda en
  el libro (`ops.stock_mov`, motivo `salida_ov`).

## 2. El interruptor

Una sola bandera: **`ordenes_venta`**, fila de `ops.automatizacion_flags`. La
variable de entorno **`ORDENES_VENTA_ENABLED`** (`config.py`, `false` por
omisión) es sólo su **respaldo** para cuando la fila no existe; es la única
variable de entorno del módulo.

- **Apagada (modo prueba):** se crean, guardan y cancelan **borradores**, se
  chatea. **No** se confirma ni se entrega (409 «modo prueba»), y el barrido de
  cancelaciones no hace nada.
- **Encendida:** se habilita confirmar-y-apartar, entregar, y el barrido
  (`ov_auto.revisar`, cada 3 min) que cacha las cancelaciones del canal.
- Si la fila **no se puede leer** (kubera caída), la bandera vale **apagada**.
- Apagar tarda a lo más 30 s en surtir (caché) y no reinicia el contenedor.

La pantalla **muestra** la bandera, quién la movió y cuándo; **no la mueve**.
Encenderla es un flujo vivo: lo hace un acta de Eduardo con el dale de Brandon
(regla 3). Hay una segunda bandera, `ov_generacion_auto` (crear la orden sola a
partir de la venta), que se muestra pero **no tiene todavía quién la use**: su
llamador es el planeador, que es otra tarea (§9).

## 3. Dónde vive (migraciones 0064 y 0065)

`ops.ov_folio` (contador), `ops.ov_ordenes`, `ops.ov_lineas`, `ops.ov_mensajes`
(chat y bitácora, sólo agregar), `ops.ov_archivos` (índice de PDF),
`ops.almacenes` (catálogo de bodegas), `ops.stock_almacen` (el saldo: `fisico`,
`apartado`; **libre = fisico − apartado**) y `ops.stock_mov` (el libro, sólo
agregar).

**Estado al 6-oct-2026:** aplicadas en el **sandbox**; **producción no las
tiene**. Van por acta de Eduardo, y **antes** que el código. Mientras falten,
nada truena: el servicio pregunta `to_regclass` (caché de 60 s), la pestaña
dice «faltan las migraciones» y ninguna ruta escribe.

`null` es «no se sabe», nunca cero: un SKU sin fila de saldo en su bodega se
pinta «sin dato» y, para apartar, vale lo mismo que no tener (falla cerrado).

## 4. Las reglas de la escritura

El SQL vigente son las constantes `SQL_*` de `backend/services/ordenes_venta.py`
(18). Los patrones salen de `backend/scripts/verificar_0064_0065.py`.

1. **Una transición = un solo `execute`**: estado, saldo, libro, renglones y
   mensaje de bitácora en un `WITH` que termina en `ops.exigir(...)`, con
   `set local lock_timeout = '4s'; set local statement_timeout = '15s';` en el
   mismo envío. El pool (SteadyDB) puede repetir un `execute` en otra conexión:
   dos sentencias no son atómicas, una sí.
2. **Orden de candados siempre igual** (guía §4.3): la fila de la orden →
   `ops.almacenes` `FOR SHARE` → `ops.stock_almacen` `FOR UPDATE` en orden
   `(sku, almacen)` → escrituras → `ops.exigir`.
3. **`rev` es el candado optimista de las personas.** Si la orden cambió, 409
   «La orden cambió mientras tanto; se recargó». Los procesos (el canal) usan
   compare-and-set **por estado** y aguantan ver la misma cancelación dos veces.
4. **Un reintento propio no es un conflicto.** Cada transición deja su marca
   (`datos.op`) en el mensaje; si el candado falla y la marca está, es mi
   escritura que sí entró.
5. **Los errores se distinguen por código, nunca por el texto**: `KB001` (el
   motivo viene en `diag.message_primary`), `23505/23514/42501` por
   `constraint_name`. A la persona le llega siempre un texto en español de
   `TEXTO_MOTIVO`; lo inesperado sale como 502 genérico y el detalle va al log.
6. **Los `*_at` los pone la base** (`now()`); **quién** entra siempre como
   parámetro (correo, `servicio` o `automatico`) y la `via` es del catálogo
   `panel | api | claude | automatico`.
7. **Regla 13:** ni un `SET` de sesión; sólo `set local`.

| Operación | Desde | Hacia | Qué hace además | Evento del chat |
|---|---|---|---|---|
| `crear_borrador` | — | `borrador` | toma folio en la misma sentencia; idempotente por `clave` | `creada` |
| `guardar` | `borrador` | `borrador` | renglones renumerados y agrupados por (SKU, bodega) | `borrador_guardado` |
| `confirmar` | `borrador` | `confirmada` | aparta todo o nada; si no alcanza deja el aviso en el chat | `confirmada` / `no_alcanzo` |
| `entregar` | `confirmada` | `confirmada` (parcial) o `entregada` | por renglón, una vez por renglón; baja `fisico` y `apartado`; escribe `salida_ov` | `entregada_parcial` / `entregada` |
| `cancelar` | `borrador`, `confirmada` | `cancelada`, o `entregada_cancelada` si ya salió algo | suelta lo apartado que no salió | `cancelada` / `devolucion_esperada` |
| `borrar` (admin) | cualquiera | (no cambia) `borrada_at/por/motivo` | suelta el apartado | `borrada_admin` |
| `canal_cancelo` | `confirmada`, `entregada` (el borrador lo cancela el barrido con `cancelar`) | ver §7 | — | `cancelada` / `canal_cancelo` / `devolucion_esperada` |
| `responder_salio` | `confirmada` con marca | `entregada_cancelada` (sí) o `cancelada` (no) | si salió, registra la salida en el libro | `devolucion_esperada` / `cancelada` |
| `salio_tarde` (admin) | `cancelada` que estuvo confirmada | `entregada_cancelada` | registra la salida que nadie marcó | `devolucion_esperada` |

Mínimos de motivo: 5 caracteres para cancelar una confirmada, 10 para borrar.

## 5. Quién es quién, y qué puede

| Llega con… | `autor` | `via` | rol |
|---|---|---|---|
| Sesión del panel | el correo | `panel` | el de `core.usuarios` |
| `X-API-Key` | `servicio` | `api` | admin |
| `X-API-Key` + cabecera `X-Origen: claude` | `servicio` | `claude` | admin |
| El barrido (`ov_auto`) | `automatico` | `automatico` | admin |

Así se contesta «¿la creó una persona, el automático o Claude?»: está en
`creado_por` + `creado_via` y se ve en la lista y en el chat. **Convención:**
toda llamada de una sesión de Claude a esta API manda `X-Origen: claude`.

Los permisos se deciden **en el servicio** (`permisos()`), porque dependen del
estado de la orden; `core/rbac.py` sólo pone el piso por verbo (`GET` lectura,
`POST`/`PUT` operador, `DELETE` admin).

| Acción | Quién | Cuándo |
|---|---|---|
| crear, guardar | quien escribe | borrador |
| confirmar | quien escribe | borrador con renglones, bandera encendida |
| entregar | quien escribe | confirmada, bandera encendida, sin «¿salió?» pendiente |
| cancelar | quien escribe / **admin** | borrador / confirmada |
| contestar «¿salió?» | quien escribe | confirmada que el canal canceló en camino |
| «salió tarde» | **admin** | cancelada que estuvo confirmada |
| borrar | **admin** | cualquiera no borrada |
| chatear | quien escribe | no borrada |
| subir PDF / bajar PDF / quitar PDF | quien escribe / quien escribe / **admin** | con bucket |

Administradores (medido en `core.usuarios` el 2-oct-2026): Brandon, Eduardo y
José. No existe rol «almacén»: la entrega la marca un operador y queda escrito
quién fue. La pantalla recibe `permisos.porque[accion]` y lo pone en el botón
apagado.

## 6. API (`/api/ordenes-venta`, `routers/ordenes_venta.py`)

Todas `async def` + `asyncio.to_thread` (regla 11). Las formas exactas están en
`frontend/components/ordenes/tipos.ts`.

| Método y ruta | Qué hace |
|---|---|
| `GET /estado` | banderas (sólo lectura), bodegas, si hay bucket, quién soy |
| `GET ` `?estado&q&canal&pagina&por_pagina` | lista paginada + conteos |
| `POST ` | alta en borrador (`DatosOrden + {clave}`) |
| `GET /skus?q=` | buscador con existencias por bodega |
| `GET /marketplace/venta?orden=&canal=` | una venta, para prellenar con su precio |
| `GET /marketplace/pendientes?dias=&canal=` | ventas DROP recientes sin orden propia |
| `POST /conciliar` | corre el barrido de cancelaciones ahora |
| `GET /{ref}` | detalle; `ref` = id o folio |
| `PUT /{id}` `DatosOrden + {rev}` | guarda el borrador |
| `POST /{id}/confirmar` `{rev}` | confirma y aparta |
| `POST /{id}/entregar` `{rev, lineas?: [{id, n}]}` | DELIVERED; sin `lineas` = todo lo pendiente |
| `POST /{id}/cancelar` `{rev, motivo}` | cancela |
| `POST /{id}/salio` `{rev, salio}` | contesta el «¿salió?» |
| `POST /{id}/salio-tarde` `{rev}` | admin: salió y nadie lo marcó |
| `DELETE /{id}` `{rev, motivo}` | admin: borra (queda el rastro). El motivo va en el cuerpo, no en la URL |
| `GET /{id}/mensajes?desde_id=&esperar=` | chat (long-poll) |
| `POST /{id}/mensajes` `{cuerpo, clave?}` | manda un mensaje |
| `POST /{id}/archivos` (multipart `pdf` + `tipo`) | adjunta un PDF |
| `GET /{id}/archivos/{archivo_id}` | baja el PDF |
| `DELETE /{id}/archivos/{archivo_id}` | admin: quita el PDF |

Errores: `400` validación · `403` permiso (con el porqué) · `404` · `409` la
orden cambió, no alcanzó el stock, modo prueba, faltan las migraciones o el
bucket · `413` PDF grande · `502` kubera o Storage no contestan.

### Chat en vivo

`ops.ov_mensajes`: `tipo = 'sistema'` lo escribe la sentencia de cada
transición (con `evento` del catálogo cerrado de la base y el detalle en
`datos`); `tipo = 'usuario'` es lo que escribe una persona.

Long-poll asíncrono: `GET …/mensajes?desde_id=N&esperar=25`. Si hay mensajes
nuevos contesta al instante; si no, la corrutina espera en `ov_bus` (en
memoria) y cualquier escritura sobre esa orden la despierta. Mientras espera no
ocupa hilo ni conexión de base. Topes: 300 esperas en el proceso y 6 por
persona, en tramos de 3 s. Persistir primero, avisar después: si el contenedor
se reinicia, lo peor es que el mensaje llegue al vencer la espera.

### PDF

`ov_archivos.tipo` ∈ `comprobante | factura | envio_full`, 15 MB, bucket
privado `ordenes-venta`. **El bucket todavía no existe**: mientras falte, subir
está apagado y la pantalla dice por qué. **Las guías con la dirección del
comprador no se guardan en kubera** (decisión D5 de la guía); la pantalla lo
advierte junto al botón.

## 7. Cuando el canal cancela (`services/ov_auto.py`)

`revisar()` corre cada 3 min con la bandera encendida, y a mano con
`POST /conciliar`. **Sólo lee** `channel.orders` y la bitácora de Automatización
(`ops.odoo_sale_orders`); sólo escribe en `ops.ov_*` y sólo por medio de
`ordenes_venta.canal_cancelo`, firmado `automatico`. No toca ningún flujo vivo.
Nunca lanza.

Una venta está cancelada si `channel.orders.estado_wc = 'cancelled'`, o
`estado_canal` dice `cancelled/canceled`, o (Temu) `estado_canal = '3'`, o
Automatización la marcó. Según cómo esté la orden propia:

| La orden está… | …y el canal | Resultado |
|---|---|---|
| `confirmada` | canceló **sin** señal de envío | `cancelada` (origen `marketplace`); suelta lo apartado |
| `confirmada` | canceló **con** señal de envío | se **marca** (`canal_cancelo_at/ref`) y se le pregunta a Bodega **«¿salió?»**. No se puede entregar hasta contestar |
| `entregada` | canceló | `entregada_cancelada` + devolución pendiente |
| `borrador` ligado a esa venta | canceló | `cancelada` (origen `marketplace`): un borrador no aparta nada ni tiene paquete, así que no se pregunta |
| `cancelada`, `entregada_cancelada`, borrada | canceló | nada |

«Señal de envío» = el canal reporta `IN_TRANSIT`, `AWAITING_COLLECTION`,
`SHIPPED`, `DELIVERED` o `COMPLETED` (Temu: `4` o `5`), o la bitácora dice
`cancelada_revisar` o `cancelada_devuelta`. Es conservador a propósito: si el
canal vio salir la pieza, soltar su apartado sin preguntar sería volver a
ofrecer algo que ya no está en la bodega.

Si Bodega contesta **sí salió** → `entregada_cancelada` (se registra la salida
y espera devolución). **No salió** → `cancelada`. Y si una orden se canceló y
después se descubre que sí había salido, un admin usa **«salió tarde»**.

**Límites conocidos:** (a) el barrido ve sólo el estado **actual** de
`channel.orders`: si la ingesta pisa `IN_TRANSIT` con `CANCELLED`, la señal de
envío se pierde y la orden se cancela sin preguntar (la salida es «salió
tarde»); (b) Temu no actualiza `channel.orders` al cancelar: su cancelación sólo
se ve si el vigilante de Automatización la marcó; (c) la cancelación **parcial**
de Temu (baja la cantidad de un renglón) no se atiende; (d) vigila las
entregadas de los últimos 45 días.

## 8. Pantalla

`/inventario/ordenes`, tercera pestaña de Inventario. Componentes en
`frontend/components/ordenes/`.

- **Lista:** KPIs-filtro por estado, buscador, y por fila: folio, mini-traza,
  cliente/canal, orden de marketplace, piezas y apartado, bodega(s), total,
  quién y cuándo. Marca «¿Salió?» mientras la pregunta está abierta.
- **Documento:** una sola interfaz para crear y para ver. Encabezado, renglones
  (con su bodega y el `libre` de ese SKU en esa bodega), totales, PDF, y a la
  derecha el chat. `#OV-00012` abre esa orden; `#nueva` el alta.
- **Traza animada:** un `<canvas>` por orden dibujado en **un solo Web Worker**
  (`OffscreenCanvas`): corre en el dispositivo de quien mira, fuera del hilo de
  la interfaz, y al servidor no le cuesta nada. Un bucle a ~30 fps que **se
  detiene** cuando no hay nada vivo y visible; respeta
  `prefers-reduced-motion`; sin `OffscreenCanvas` pinta el mismo riel en SVG.
  La entrega parcial deja el tramo a DELIVERED a medias; el «¿salió?» pendiente
  lo pinta en ámbar.

## 9. Lo que falta (y de quién es)

| Pendiente | Por qué importa | Quién |
|---|---|---|
| Aplicar 0064 y 0065 en producción | Sin ellas la pestaña dice «faltan las migraciones» | Eduardo (acta), antes que el código |
| Subir el código a `main` | Railway despliega lo que llega a `main` | Brandon da el OK |
| Encender la bandera `ordenes_venta` | Habilita confirmar, entregar y el barrido | Acta + dale de Brandon (regla 3) |
| **`inventario_libro`** (entradas, conteo, correcciones) | **Sin él no hay cómo meter stock a una bodega de kubera desde la pantalla**: no se puede confirmar ninguna orden real | Sistemas |
| Bucket `ordenes-venta` | Sin él no se adjuntan PDF | Eduardo |
| Devoluciones (recibir, dictaminar, cerrar) | Hoy la orden sólo muestra `devolucion_estado`; los pasos operativos no están definidos | Coordinador + ClickUp `86bcc3jf6` |
| Retención de los PDF | No caducan solos | Decisión, ClickUp `86bcc3jdy` |
| Planeador (quién llama a `crear_auto`), `stock_watch` leyendo kubera, `odoo_ventas` | Es lo que convierte a esta orden en el reemplazo real de la de Odoo. Son flujos vivos | Fase B del plan v3 |
| Órdenes tipo `full` (envío a FULL) | El servicio las rechaza al crear; se capturan en Crear FULL | Otra tarea |
| Aviso de las vistas vigía de la 0065 | Nadie mira todavía `stock_apartado_descuadre_v` | Por asignar |
| Usuario de almacén | No hay rol «almacén»: se da de alta como `operador` | Brandon (correo de la cuenta) |

Las dudas abiertas para el coordinador de la migración están en la tarea de
ClickUp `86bcbfnkw`.

## 10. Cómo se prueba

`unittest` (no pytest), desde `backend/`:

- `tests.test_ordenes_venta` — unitarias, sin base ni red.
- `tests.test_ordenes_venta_api` — router, bus del chat, scheduler y barrido.
- `tests.test_ordenes_venta_bd` — integración contra un Postgres **local y
  desechable**: con `OV_TEST_DSN` apuntando a `127.0.0.1` la suite tira y recrea
  esa base con `tests/ov_fixture.sql` → `0064` → `0065`. Sin la variable se
  omite. Tiene candado: no acepta un host que no sea local.

Frontend: `npx tsc --noEmit` y `node components/ordenes/pruebas/*.prueba.cjs`.

**Nunca contra producción, y el sandbox sólo como dice la guía (§7).**
