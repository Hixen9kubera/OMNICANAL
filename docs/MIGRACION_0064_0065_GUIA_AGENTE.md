# 0064 y 0065: qué hace la base y qué le toca a tu código

> **Para:** Brandon y su agente de Claude, que van a programar el código que usa estas tablas
> (`services/ordenes_venta.py`, `services/inventario_libro.py` y los lectores).
> **Fecha:** 6-oct-2026.
> **La verdad, en este orden:**
> 1. `supabase/migrations/0064_ops_ordenes_venta.sql` y `supabase/migrations/0065_ops_inventario_kubera.sql`.
> 2. `backend/scripts/verificar_0064_0065.py`: sus constantes `SQL_*` son las sentencias de referencia que usan las pruebas. Sirven de **patrón**, pero no son código de producción.
> 3. El plan v3 (5-oct), su revisión técnica y DEVOLUCIONES explican el porqué. Cuando esta guía afirma algo que no está en el SQL, dice de dónde sale.
>
> **Esos tres documentos NO están en el repo** (viven con el coordinador). Las citas «plan v3 §x», «revisión C3/SEG-05/H08» o «DEVOLUCIONES §4b» sirven para rastrear el origen. Lo que tu código necesita de ellos está copiado en esta guía: la puerta (§4.9 i), las cancelaciones del canal (§4.9 g), las banderas (§4.8) y `paquete_ids` (§4.5). Para el detalle de stock_watch (plan v3 §4) y del planeador (plan v3 §5), que §5.2 solo enumera, pide el extracto al coordinador **antes** de empezar.
>
> «La base» son las restricciones, triggers, funciones y vistas de las dos migraciones. «Tu código» es todo lo demás.

---

## 1. Estado y reglas de despliegue

### 1.1 Dónde está cada cosa (verificado por el coordinador el 6-oct-2026)

| Ambiente | Estado |
|---|---|
| **Sandbox** (`yvootpbz`) | 0064 y 0065 **APLICADAS** desde el 6-oct 09:08 UTC. Están registradas en `ops.migraciones` (`0064_ops_ordenes_venta`, `0065_ops_inventario_kubera`). |
| **Producción** (`tukwcvsi`) | **APLICADAS** el 6-oct a las 17:40:45 UTC, las dos en una sola transacción, con el acta `_paso_prod_ov_0064_0065`: 71 de 71 comprobaciones después del COMMIT. Están vacías: 6 bodegas sembradas, TEX3 apagada, `ov_folio` en 0, ninguna bandera. `ops.stock_watch_photo` ya tiene `stock_kubera`, en NULL en todas las filas. |
| **Repo** | Rama `feat/ov-almacen-kubera-0064-0065` **empujada a GitHub y sin fusionar a main**. Contiene las dos migraciones, el verificador, esta guía y el `schema_manifest.json` con las 16 relaciones nuevas, medidas en producción. No hay código de servicio. |

- **El encabezado de los `.sql` ya está al día:** aplicadas en producción y en el sandbox. Las «55 pruebas OK» que menciona son de la corrida dentro de una transacción, antes de aplicarlas en el sandbox.
- **Ya puedes fusionar código que lea estas tablas** (SEG-01 se cumplió: las migraciones entraron primero). Va con las banderas apagadas; encender TEX3 o cualquier bandera es otra acta.
- **Pruebas sobre lo aplicado:** en modo por omisión, 54 OK, 0 FALLA y 2 omitidas (las de concurrencia). Con `--concurrencia-con-commit` las dos pasan:
  - dos confirmar simultáneos del mismo SKU: A gana; B espera el candado del saldo y recibe `KB001 no_alcanzo`;
  - un INSERT de renglón durante un confirmar en curso: espera y recibe `42501 ov_lineas_inmutable`.
- **El sandbox ya tiene historia de pruebas.** `OV-00001` a `OV-00006` están canceladas, y hay movimientos de SKUs `ZZCONC-*` y `ZZPRUEBA-*` en `ENSAYO` con saldo 0. El libro y el chat son de solo agregar, así que esa historia no se va. **El contador de folio no está en 0.**

### 1.2 Reglas

1. **SEG-01: el único hallazgo ALTO de la revisión.** Railway despliega todo lo que llega a `main`.
   - La 0064 y la 0065 se aplican en producción **antes** de fusionar cualquier código que las lea.
   - Ese código tolera que no existan (§4.8).
   - Sin las dos cosas pasa esto (encabezado de la 0064): cada venta de TikTok y Temu se queda en espera, y stock_watch falla cerrado, así que Woo se queda quieto.
2. **Producción se toca solo con acta de tres firmas:** acta del agente → verificación del coordinador → sí de Eduardo.
   - El agente **nunca** aplica nada en producción y nunca se conecta a producción.
   - Puede redactar el borrador del acta si se lo piden.
3. **Las dos migraciones van en la misma acta, la 0064 primero.**
   - El paso 0 de la 0065 truena con `KB000` si falta la 0064.
   - El ALTER final de la 0065 (`stock_watch_photo.stock_kubera`, con `lock_timeout` de 3 s) se aplica justo después de una pasada de stock_watch.
4. **PROHIBIDO tocar Odoo desde el código nuevo.** Kubera solo observa Odoo en lectura.
   - El código nuevo (`ordenes_venta`, `inventario_libro`, la puerta y el vigilante de D4) **nunca** escribe en Odoo: no crea, ajusta, traslada, archiva, reserva ni cancela nada.
   - `odoo_ventas` sigue creando, como hoy, la sale.order de la parte que sale de `TEXCO` o `TEX2`. El único cambio ahí es quitar los renglones de TEX3 y no volver a planearlos (plan v3 §5).
   - Nunca se crea, ajusta ni cancela en Odoo nada de TEX3.
5. **No edites la 0064 ni la 0065.**
   - Ya están registradas en el sandbox.
   - El paso 0 **no** compara todo: solo revisa una lista fija de columnas y tipos, columnas prohibidas, y unas 15 a 20 restricciones e índices por migración (por un fragmento de su definición).
   - Si editas algo fuera de esa lista, no hay `KB000`: un CHECK editado no se aplica (`create table if not exists` se salta la tabla), y una función de trigger editada se reemplaza sin aviso (`create or replace`). En los dos casos el archivo y la base quedan distintos y nadie lo ve.
   - Un cambio de esquema es una migración nueva, coordinada: número y acta con el coordinador.

### 1.3 El modelo de negocio (Eduardo, 5-oct)

- **Hay dos sistemas independientes que no se reflejan:** Odoo (`TEXCO`, `TEX2`, `DROP`) y kubera (`TEX3`, `ENSAYO`, `REVISION`).
- **Las OV existen solo en bodegas de kubera.**
  - `TEX3` es TEXCO III y nace **apagada**.
  - Se practica en `ENSAYO`.
- **Las ventas de TikTok y Temu que salen de bodegas de Odoo** siguen creando su orden en Odoo por `odoo_ventas`, como hoy. La parte que sale de TEX3 será una OV automática (`crear_auto`).
- **Los envíos a FULL son OV con `tipo = 'full'`.**
- **Fases (plan v3 §10.1):**
  - **Fase A:** las migraciones en producción, `stock_watch_lee_kubera` encendida con TEX3 vacía y `ordenes_venta` encendida solo en `ENSAYO` (las banderas, en §4.8).
  - **Fase B:** un acta enciende `ov_generacion_auto` y TEX3 (`admite_ov`, `surte_ventas` y `cuenta_para_woo`).

### 1.4 Si ya tienes código escrito contra la versión del 2-oct (`docs/MIGRACION_0064_ORDENES_VENTA.md`)

| Versión del 2-oct | Ahora |
|---|---|
| `ops.ov_stock` (con `otorgado`) y la vista `ops.ov_stock_base_v` | **No existen.** El apartado vive en `ops.stock_almacen.apartado` (0065), por SKU × bodega de kubera. Ya no se reserva contra la foto de Odoo. Si alguna de las dos existe, el paso 0 de la 0064 truena con `KB000` |
| `ov_ordenes.almacen` | No existe (el paso 0 la prohíbe). La bodega va en `ov_lineas.almacen`, con FK compuesta solo a bodegas de kubera |
| `ov_lineas` → `ov_ordenes` con `on delete cascade` | **Sin cascade.** Una OV no se borra: el borrado es lógico (`borrada_at`, `borrada_por`, `borrada_motivo`) |
| `unique (orden_id, sku)` | `ov_lineas_sku_alm_uq`: `unique nulls not distinct (orden_id, sku, almacen)`, diferida. Además `ov_lineas_linea_uq`: `(orden_id, linea)`, también diferida |
| `reservado` entre 0 y `cantidad` | `reservado in (0, cantidad)` (`ov_lineas_reservado_chk`): se aparta todo o nada |
| Sin triggers ni funciones | Triggers de guarda, constraint triggers diferidos y `ops.exigir` (§3) |
| `1 / (case … then 1 else 0 end)` | `ops.exigir(cond, 'motivo')`, que lanza `KB001` |
| `ov_ordenes_mp_uq` con `coalesce(mp_cuenta, '')`; `clave_uq` total | Los dos son parciales: sin `coalesce` y solo entre OV vivas (ni borradas ni `cancelada`). `mp_*` va todo o nada |
| `cancelada_origen` ∈ {manual, marketplace} | Se agrega `sistema` |
| `devolucion_estado` solo en `entregada_cancelada` | También en `entregada` (`ov_ordenes_devol_chk`) |
| — | Columnas nuevas: `tipo`, `full_tienda`, `envio_ref`, `canal_cancelo_at`, `canal_cancelo_ref`; en `ov_lineas`: `almacen`, `fuente`, `entregado`, `entregado_at` y `entregado_por`; en `ov_archivos`: `tipo`, sin default |
| El bucket `ordenes-venta` lo creaba la migración | **No lo crea.** Va aparte (§5) |
| `services/ov_auto.py` | `crear_auto` vive en `services/ordenes_venta.py` (encabezado de la 0064, «QUIÉN ESCRIBE») |

---

## 2. Mapa de tablas, vistas y funciones

Todo vive en el esquema `ops`.
- **Seguridad:** las 14 tablas tienen RLS activa sin políticas, y anon y authenticated no tienen permisos. El backend entra como `postgres`, el dueño: **a él RLS y los grants no lo frenan; los triggers, CHECK y FK sí.**
- **Quién escribe:** lo dicen los encabezados («QUIÉN ESCRIBE»). Los módulos `services/ordenes_venta.py` e `services/inventario_libro.py` **todavía no existen**.
- **Quién lee:** sale del plan v3.

| Objeto | Para qué | Llave | Quién escribe | Quién lee |
|---|---|---|---|---|
| `ops.almacenes` (0064) | Catálogo de bodegas: fuente (`odoo` o `kubera`, fija), preferencia del planeador y banderas | PK `codigo`; `unique (codigo, fuente)` | Solo la migración y un UPDATE con acta. **Tu código no la escribe** | Planeador (`odoo_ventas`), Crear FULL, `temu_guias_compra` (`temu_warehouse_id`), stock_watch (`cuenta_para_woo`), las sentencias de OV y libro (`admite_ov`, `surte_ventas`, en `FOR SHARE`) |
| `ops.almacenes_hist` (0064) | Historia de cada alta y cada cambio de `almacenes` | PK `id` | Solo el trigger `almacenes_hist` | Auditoría |
| `ops.migraciones` (0064) | Registro de cada aplicación de una migración, desde la 0064 | PK `id` | Cada migración, dentro de su propia transacción y antes del commit. En la 0065 va antes del ALTER final de `stock_watch_photo` | Coordinador y actas |
| `ops.ov_folio` (0064) | Contador del folio `OV-00001…`, sin huecos. Una sola fila `(1, ultimo)` | PK `id` (= 1) | `ordenes_venta` (crear_borrador, crear_auto), en la misma sentencia del alta | — |
| `ops.ov_ordenes` (0064) | Encabezado de la OV | PK `id`; `folio` único | `ordenes_venta` | Pantallas; stock_watch (`cub`); `odoo_ventas` (la OV viva por `mp_*`); cancelaciones; guías del día; FULLFILMENT; Crear FULL |
| `ops.ov_lineas` (0064) | Renglón: SKU, cantidad, bodega, apartado y entrega | PK `id` | `ordenes_venta` | Los mismos, más la vista vigía |
| `ops.ov_mensajes` (0064) | Chat y bitácora de la OV. Solo se agrega | PK `id` | `ordenes_venta` (`tipo='sistema'`, en la misma sentencia de cada transición) y la pantalla (`tipo='usuario'`) | Pantalla de la OV |
| `ops.ov_archivos` (0064) | Índice de los PDF; el binario va a Storage | PK `id` | `ordenes_venta`, **después** de subir el objeto. **No tiene escritor mientras no exista el bucket `ordenes-venta`** | Pantalla de la OV |
| `ops.stock_almacen` (0065) | Saldo vivo por SKU × bodega de kubera: `fisico`, `apartado` y `libre` (generada) | PK `(sku, almacen)` | `ordenes_venta` (apartar, entregar, cancelar, y las sentencias de devolución que también escriben `ov_*`) e `inventario_libro` (entradas, puerta, traspasos, conteo, merma, corrección, y lo de devoluciones que no toca `ov_*`) | Planeador (libre de TEX3), stock_watch (`kub`), pantallas, vigía |
| `ops.stock_mov` (0065) | **El libro:** cada cambio del físico. Solo se agrega | PK `id`; `clave` única | Los mismos escritores que `stock_almacen`, **en la misma sentencia** | stock_watch (`entro`), vigía, cuadre y reportes |
| `ops.stock_formato` (0065) | Un archivo de Bodega (lote movido de TEX2 a TEX3). Folio `FMT-00001…` generado | PK `id` | `inventario_libro` | Pantalla «Entradas de Bodega»; stock_watch (`tras`, puerta) |
| `ops.stock_formato_linea` (0065) | Renglón del formato. **La puerta es un dato:** `salida_odoo_at` NULL significa que espera | PK `id` | `inventario_libro` | Lo mismo |
| `ops.stock_formato_evento` (0065) | La huella de cada operación sobre un formato. Solo se agrega | PK `id` | `inventario_libro`, en la misma sentencia de cada operación | Auditoría y pantalla |
| `ops.devoluciones` (0065) | Una fila por SKU y dictamen de cada paquete que entra a `REVISION`. Folio `DEV-00001…` generado | PK `id`; `clave` única | Toda sentencia que también escribe `ov_*` (recibir una venta, que cambia `devolucion_estado`; un dictamen que deja su evento en el chat de la OV): una función de `ordenes_venta`, que la pantalla llama (§5.1, punto 3). Lo que no toca `ov_*` (partir, el retiro de FULL y sus dictámenes): `inventario_libro` | Vigía, aviso de 72 h, cruce con `channel.returns` |
| `ops.stock_watch_photo.stock_kubera` (0065, columna nueva `integer`) | La mitad de kubera de la foto de stock_watch | — | stock_watch | El freno de stock_watch |
| Vista `ops.stock_apartado_descuadre_v` (0065) | Vigía: **solo devuelve filas cuando algo no cuadra**. No depende de `channel.*` | — | — | El aviso cada 15 min |
| Vista `ops.devoluciones_vs_canal_v` (0065) | Vigía del cruce con el canal: lo recibido de más contra `channel.return_items`. Mismas columnas que la anterior | — | — | El aviso cada 15 min |

### 2.1 Las bodegas sembradas (0064, `on conflict do nothing`)

| `codigo` | `nombre` | `fuente` | `odoo_warehouse_id` | `preferencia` | `surte_ventas` | `admite_ov` | `cuenta_para_woo` | `temu_warehouse_id` |
|---|---|---|---|---|---|---|---|---|
| `TEXCO` | TEXCO | odoo | 135 | 1 | true | false | true | `WH-04038973460631627` |
| `TEX2` | TEXCO II | odoo | 150 | 2 | true | false | true | `WH-10610291507351627` |
| `DROP` | DROP OFF | odoo | 142 | NULL | false | false | true | NULL |
| `TEX3` | TEXCO III | kubera | NULL | 3 | **false** | **false** | **false** | NULL |
| `ENSAYO` | Bodega de ensayo | kubera | NULL | NULL | false | **true** | false | NULL |
| `REVISION` | Revisión de devoluciones | kubera | NULL | NULL | false | false | false | NULL |

- **En las filas de Odoo, `cuenta_para_woo` es informativa** (decisión 5 de la 0064): stock_watch lee el total de Odoo, no esta columna.
- **Volver a correr la 0064 no pisa nada:** TEX3 encendida se queda encendida.

### 2.2 Funciones

| Función | Qué hace | EXECUTE para service_role |
|---|---|---|
| `ops.exigir(boolean, text)` | Devuelve 1 si la condición se cumple. Si es falsa **o NULL**, lanza `KB001` con el motivo como mensaje (vacío → `regla_de_negocio`) | sí |
| `ops.verificar_ov(bigint)` | Invariante de la OV (§3.4). Lanza `23514 ov_coherente` | sí |
| `ops.verificar_libro(citext, text)` | Saldo = libro para `(sku, bodega)`. Lanza `23514 stock_libro_cuadra` | sí |
| `ops.verificar_apartado(citext, text)` | Apartado = Σ reservado para `(sku, bodega)`. Lanza `23514 stock_apartado_cuadra` | sí |
| `ops.tg_solo_agregar()`, `ops.tg_sin_truncate()`, `ops.tg_almacenes_guarda()`, `ops.tg_almacenes_hist()`, `ops.tg_ov_folio_guarda()`, `ops.tg_ov_archivos_guarda()`, `ops.tg_ov_ordenes_guarda()`, `ops.tg_ov_lineas_guarda()`, `ops.tg_ov_coherente()`, `ops.tg_stock_almacen_guarda()`, `ops.tg_stock_formato_guarda()`, `ops.tg_stock_formato_linea_guarda()`, `ops.tg_devoluciones_guarda()`, `ops.tg_libro_cuadra()`, `ops.tg_apartado_cuadra()`, `ops.tg_devolucion_de_mas()` | Funciones de trigger (§3) | — |

Puedes llamar las tres `verificar_*` para diagnosticar. Si no hay descuadre, no devuelven nada.

---

## 3. Lo que hace la base (garantías)

Antes de leer las tablas, tres convenciones:

- **Los nombres que lanzan los triggers no son restricciones del catálogo.** Llegan igual en `e.diag.constraint_name`, pero no están en `pg_constraint`. Por ejemplo: `ov_ordenes_inmutable`, `ov_coherente`, `stock_libro_cuadra` o `almacenes_motivo_chk`.
- **Un índice único parcial reporta su nombre de índice como `constraint_name`.** Por ejemplo, `ov_ordenes_mp_uq`.
- **«Al COMMIT»** quiere decir constraint trigger o UNIQUE `DEFERRABLE INITIALLY DEFERRED`. El error no sale en tu `execute`: sale al confirmar (§4.1, punto 6).

### 3.1 `ops.almacenes`

- **Llaves.**
  - `almacenes_pkey` (`codigo`).
  - `almacenes_codigo_fuente_uq` `(codigo, fuente)`: es el destino de las FK compuestas.
  - `almacenes_odoo_uq` (`odoo_warehouse_id`).
  - `almacenes_pref_uq` (`preferencia`), `deferrable initially immediate`: permite intercambiar preferencias en un solo UPDATE.
- **CHECK.**

  | CHECK | Regla |
  |---|---|
  | `almacenes_codigo_chk` | `^[A-Z0-9]{2,12}$` |
  | `almacenes_nombre_chk` | `nombre` no vacío |
  | `almacenes_fuente_chk` | `fuente` ∈ {odoo, kubera} |
  | `almacenes_odoo_chk` | `fuente = 'odoo'` ⇔ hay `odoo_warehouse_id` |
  | `almacenes_pref_chk` | `preferencia` > 0 |
  | `almacenes_ov_chk` | `admite_ov` solo en kubera |
  | `almacenes_surte_chk` | `surte_ventas` exige `preferencia` |
  | `almacenes_woo_chk` | Una bodega de kubera con `cuenta_para_woo` tiene que `surte_ventas` |
  | `almacenes_surte_ov_chk` | Una bodega de kubera con `surte_ventas` tiene que `admite_ov` |

- **Trigger `almacenes_guarda`** (BEFORE INSERT, UPDATE y DELETE):
  - **alta:** exige un `motivo` de 10 caracteres o más (`23514 almacenes_motivo_chk`) y quién: `app.usuario` o `actualizado_por` (`23514 almacenes_quien_chk`);
  - **DELETE:** `42501 almacenes_sin_borrado`;
  - **cambiar `codigo` o `fuente`:** `42501 almacenes_codigo_fuente_fijos`;
  - **cambiar `surte_ventas`, `cuenta_para_woo`, `admite_ov`, `preferencia`, `odoo_warehouse_id` o `temu_warehouse_id`:** exige un `motivo` **nuevo** de 10 caracteres o más (`almacenes_motivo_chk`) y quién: `app.usuario`, o un `actualizado_por` distinto del anterior (`almacenes_quien_chk`);
  - **`nombre`** se edita sin acta;
  - mantiene `actualizado_at` y `actualizado_por`.
- **Trigger `almacenes_hist`** (AFTER INSERT y UPDATE): escribe una fila en `almacenes_hist` con `antes` y `despues` en jsonb, `motivo` y `quien`.
- **Sin TRUNCATE:** `almacenes_sin_truncate`.
- **Lo que impide:**
  - borrar una bodega o cambiarle la fuente;
  - encender una bandera sin rastro;
  - el UPDATE de la fase B copiado del plan v3 §10.1 (paso 12) **sin** `admite_ov = true`, que truena con `23514 almacenes_surte_ov_chk`;
  - que una bodega de kubera cuente para Woo sin `surte_ventas`, o que surta sin `admite_ov` (`almacenes_woo_chk`, `almacenes_surte_ov_chk`).
- **Lo que NO impide:** que el planeador de verdad la surta. Eso depende de `ov_generacion_auto` y del código, y ninguna restricción liga esa bandera con `cuenta_para_woo`: con `ov_generacion_auto` apagado el planeador excluye TEX3 (§5.2, punto 8) aunque la fila diga `cuenta_para_woo = true`. Por eso el acta de la fase B enciende las dos cosas juntas.

### 3.2 `ops.almacenes_hist` y `ops.migraciones`

- **Solo se agregan.** UPDATE, DELETE y TRUNCATE dan `42501`. El constraint es `almacenes_hist_solo_agregar` o `migraciones_solo_agregar`, también en el TRUNCATE (triggers `*_sin_truncate` con `ops.tg_solo_agregar`).
- **`migraciones_nombre_chk`:** `^[0-9]{4}_[a-z0-9_]+$`.
- **`migraciones.quien`:** por omisión, `app.usuario` o `current_user`.

### 3.3 `ops.ov_folio`

- **Restricciones:**
  - `ov_folio_una_fila_chk` (`id = 1`);
  - `ov_folio_ultimo_chk` (`ultimo >= 0`).
- **Trigger `ov_folio_guarda`:** borrar la fila, cambiar el `id` o bajar `ultimo` dan `42501 ov_folio_solo_sube`.
- **Sin TRUNCATE:** `ov_folio_sin_truncate`.
- **Lo que impide:** que el folio regrese y todas las altas choquen con `ov_ordenes_folio_uq`. Ese choque el contrato **no** lo traduce a «ya existía».
- **Para tu código:** el UPDATE del contador bloquea la fila hasta el commit, así que todas las altas hacen fila ahí. Mantén cortas las sentencias de alta.

### 3.4 `ops.ov_ordenes`

**CHECK.** Todos dan `23514` con su nombre:

| CHECK | Regla |
|---|---|
| `ov_ordenes_folio_chk` | `^OV-[0-9]{5,}$` |
| `ov_ordenes_estado_chk` | {borrador, confirmada, entregada, cancelada, entregada_cancelada} |
| `ov_ordenes_tipo_chk` | {venta, full} |
| `ov_ordenes_via_chk` | `creado_via` ∈ {panel, api, claude, automatico} |
| `ov_ordenes_precio_origen_chk` | {manual, marketplace} |
| `ov_ordenes_moneda_chk` | `^[A-Z]{3}$` |
| `ov_ordenes_importes_chk` | `total`, `comision` ≥ 0 |
| `ov_ordenes_rev_chk` | `rev` ≥ 1 |
| `ov_ordenes_canal_chk` | `canal` NULL o ∈ {temu, tiktok, mercado_libre, amazon, walmart, shein, directa, otro} |
| `ov_ordenes_mp_chk` | `mp_canal`, `mp_cuenta` y `mp_orden` todos o ninguno |
| `ov_ordenes_mp_forma_chk` | `mp_canal` en minúsculas, `mp_cuenta` en mayúsculas |
| `ov_ordenes_full_chk` | `tipo='full'` ⇔ hay `full_tienda` |
| `ov_ordenes_full_t_chk` | `full_tienda` ∈ {`meli:Kubera`, `meli:San Corpe`, `amazon`, `walmart`} |
| `ov_ordenes_full_mp_chk` | Una OV full no lleva `mp_orden` |
| `ov_ordenes_full_canal_chk` | Una OV full tiene `canal` ∈ {mercado_libre, amazon, walmart} |
| `ov_ordenes_full_tc_chk` | Cada `full_tienda` va con su `canal` |
| `ov_ordenes_full_env_chk` | Una OV full confirmada tiene `envio_ref` (D10) |
| `ov_ordenes_cancelada_origen_chk` | {manual, marketplace, sistema} |
| `ov_ordenes_conf_chk` | confirmada, entregada y entregada_cancelada ⇒ hay `confirmada_at` |
| `ov_ordenes_borr_chk` | Un borrador no tiene `confirmada_at`, `entregada_at` ni `cancelada_at` |
| `ov_ordenes_entr_chk` | estado ∈ {entregada, entregada_cancelada} ⇔ hay `entregada_at` |
| `ov_ordenes_canc_chk` | estado ∈ {cancelada, entregada_cancelada} ⇔ hay `cancelada_at` |
| `ov_ordenes_canc_o_chk` | Cancelada ⇒ hay `cancelada_origen` |
| `ov_ordenes_canc_m_chk` | Si estuvo confirmada, `cancelada_motivo` ≥ 5 |
| `ov_ordenes_conf_q_chk`, `ov_ordenes_entr_q_chk`, `ov_ordenes_canc_q_chk`, `ov_ordenes_borrada_chk` | El `_at` y el `_por` van juntos |
| `ov_ordenes_borrada_m_chk` | `borrada_motivo` ≥ 10 |
| `ov_ordenes_devolucion_chk` | `devolucion_estado` ∈ {pendiente, recibida, cerrada} |
| `ov_ordenes_devol_chk` | `devolucion_estado` solo en entregada o entregada_cancelada |
| `ov_ordenes_canal_cancelo_chk` | `canal_cancelo_at` solo en confirmada, cancelada o entregada_cancelada |
| `ov_ordenes_canal_cancelo_ref_chk` | `canal_cancelo_at` y `canal_cancelo_ref` van juntos |
| `ov_ordenes_auto_cliente_chk` | `creado_via='automatico'` ⇒ `cliente` = `mp_canal` (SEG-06) |

**Únicos.**
- `ov_ordenes_folio_uq`.
- `ov_ordenes_mp_uq` `(mp_canal, mp_cuenta, mp_orden)`, solo entre OV vivas: con `mp_orden`, sin `borrada_at` y con `estado <> 'cancelada'`.
- `ov_ordenes_clave_uq` `(clave)`, con el mismo filtro de vivas.
- **Una `cancelada` libera su venta y su clave. Una `entregada_cancelada` no.**

**Trigger `ov_ordenes_guarda`** (BEFORE INSERT, UPDATE y DELETE):
- **Cómo nace:** en `borrador`, o en `confirmada` **solo si** `creado_via = 'automatico'`. Cualquier otra cosa da `23514 ov_ordenes_transicion_chk`.
- **Transiciones válidas.** Cualquier otra da `23514 ov_ordenes_transicion_chk`:
  - borrador → confirmada | cancelada;
  - confirmada → entregada | cancelada | entregada_cancelada;
  - entregada → entregada_cancelada;
  - cancelada → entregada_cancelada (el «¿salió?» tardío; que haya estado confirmada lo exige `ov_ordenes_conf_chk`).
- **Lo que no se borra ni cambia.** Todo esto da `42501`:
  - DELETE: `ov_ordenes_sin_borrado`;
  - una OV borrada ya no cambia: `ov_ordenes_inmutable`;
  - `id`, `folio`, `creado_at`, `creado_por` y `creado_via` son fijos: `ov_ordenes_inmutable`.
- **Fuera de borrador solo cambian:** `estado`, `rev`, `actualizado_at`, `entregada_*`, `cancelada_*`, `borrada_*`, `devolucion_estado`, `canal_cancelo_at` y `canal_cancelo_ref`. Lo demás da `42501 ov_ordenes_inmutable`. Una columna nueva nace congelada.
- **Dentro de lo permitido, lo anotado no se reescribe** (`42501 ov_ordenes_inmutable`):
  - `entregada_*`, `cancelada_*` y la marca `canal_cancelo_*` se ponen **una vez**;
  - al pasar de entregada a entregada_cancelada se conserva `entregada_at`;
  - `rev` no baja.
- **`devolucion_estado`:**
  - no vuelve a NULL, ni pasa de `recibida` a `pendiente` (`23514 ov_ordenes_transicion_chk`);
  - una `cerrada` **sí** se reabre (decisión 16).
- Mantiene `actualizado_at`.
- **El trigger no obliga a que `rev` suba.** Subirla en uno en cada escritura le toca a tu código.

**Al COMMIT.**
- **`ov_ordenes_coherente`** y su gemelo `ov_lineas_coherente` llaman a `ops.verificar_ov` en una sola consulta (una sola foto). Si algo falla, lanzan `23514 ov_coherente`. Las reglas:
  - una OV confirmada y viva tiene al menos un renglón sin entregar, y **todo** renglón sin entregar está apartado completo;
  - una entregada tiene todo entregado;
  - fuera de confirmada, o si está borrada, no hay apartado;
  - un borrador no tiene entregas;
  - una `cancelada` no dejó salir piezas: si salieron, tiene que ser `entregada_cancelada`.
- **`ov_ordenes_apartado_cuadra`** (cambios de `estado` o `borrada_at`) llama a `ops.verificar_apartado` por cada `(sku, bodega)` de la OV que aparta.

**Sin TRUNCATE:** `ov_ordenes_sin_truncate`.

### 3.5 `ops.ov_lineas`

- **Llaves.**
  - FK `ov_lineas_orden_fk`, sin cascade.
  - FK compuesta `ov_lineas_almacen_fk`: `(almacen, fuente)` → `almacenes (codigo, fuente)`. Si la bodega no es de kubera, da `23503`. Con `almacen` NULL, en borrador, no se revisa.
- **CHECK.**

  | CHECK | Regla |
  |---|---|
  | `ov_lineas_fuente_chk` | `fuente = 'kubera'` |
  | `ov_lineas_linea_chk` | `linea` > 0 |
  | `ov_lineas_cantidad_chk` | `cantidad` > 0 |
  | `ov_lineas_precio_chk` | `precio_unitario` ≥ 0 |
  | `ov_lineas_reservado_chk` | `reservado in (0, cantidad)` |
  | `ov_lineas_reserva_alm_chk` | Un renglón que aparta tiene bodega |
  | `ov_lineas_entrega_chk` | `entregado`, `entregado_at` y `entregado_por` van juntos; `entregado` entre 0 y `cantidad`; con bodega; con `reservado = 0` |

- **Únicos diferidos.** Truenan **al COMMIT** con `23505`:
  - `ov_lineas_sku_alm_uq` `(orden_id, sku, almacen)`, `nulls not distinct`;
  - `ov_lineas_linea_uq` `(orden_id, linea)`.

  Diferidos permiten que guardar renumere `linea`, o quite y vuelva a poner un SKU, dentro de una sola transacción.
- **Trigger `ov_lineas_guarda`** (BEFORE INSERT, UPDATE y DELETE). Todo lo que rechaza es `42501 ov_lineas_inmutable`:
  - **INSERT:**
    - bloquea la fila de la OV (`FOR NO KEY UPDATE`);
    - rechaza si la OV está borrada;
    - rechaza si ya no es borrador, salvo que la OV haya nacido en esta transacción (`creado_at = now()`; así inserta crear_auto).
  - **UPDATE de `id` u `orden_id`:** rechazado.
  - **Renglón que ya salió** (`entregado_at`): no se edita ni se borra.
  - **Renglón que aparta** (`reservado > 0`): no se borra, y solo cambian `reservado`, `entregado`, `entregado_at` y `entregado_por`.
  - **Renglón sin apartado ni entrega:** bloquea la OV.
    - Si la OV está borrada, rechaza.
    - En borrador, todo se permite.
    - En confirmada, solo pasa el UPDATE de confirmar: `confirmada_at = now()`, `reservado > 0`, y solo cambian `almacen` y `reservado`.
    - En cancelada o entregada_cancelada, solo pasa la salida tardía: `reservado = 0`, `entregado > 0`, y solo cambian `entregado*`.
    - Lo demás se rechaza.
- **Al COMMIT:**
  - `ov_lineas_coherente` (§3.4);
  - `ov_lineas_apartado_cuadra`, después de INSERT, DELETE o un UPDATE de `reservado`, `sku` o `almacen`.
- **Sin TRUNCATE:** `ov_lineas_sin_truncate`.
- **Lo que impide:**
  - colar un renglón sin apartar en una OV confirmada. Es el caso que probó la concurrencia: el INSERT espera al confirmar y después recibe `42501`;
  - reapartar días después en otra bodega;
  - editar lo que ya salió.

### 3.6 `ops.ov_mensajes`

- **CHECK.**

  | CHECK | Regla |
  |---|---|
  | `ov_mensajes_tipo_chk` | {sistema, usuario} |
  | `ov_mensajes_via_chk` | {panel, api, claude, automatico} |
  | `ov_mensajes_cuerpo_chk` | Entre 1 y 4000 caracteres |
  | `ov_mensajes_evento_chk` | NULL o ∈ {`creada`, `borrador_guardado`, `descartada`, `confirmada`, `no_alcanzo`, `entregada_parcial`, `entregada`, `cancelada`, `borrada_admin`, `canal_cancelo`, `devolucion_esperada`, `devolucion_recibida`, `devolucion_aprobada`, `devolucion_merma`, `devolucion_cerrada`} |

- **FK:** `ov_mensajes_orden_fk`.
- **Solo se agrega:** `42501 ov_mensajes_solo_agregar`, también en el TRUNCATE.
- **Lo que no garantiza:** que `tipo='sistema'` lleve `evento`, ni la ausencia de datos personales en `cuerpo`.

### 3.7 `ops.ov_archivos`

- **CHECK.**

  | CHECK | Regla |
  |---|---|
  | `ov_archivos_tipo_chk` | {envio_full, comprobante, factura}. **Sin default** |
  | `ov_archivos_sha_chk` | Hex de 64 caracteres en minúsculas |
  | `ov_archivos_bytes_chk` | > 0 |
  | `ov_archivos_borrado_chk` | `borrado_at` y `borrado_por` van juntos |

- **Únicos e índices:**
  - `ov_archivos_vivo_uq` `(orden_id, sha256)` entre filas no borradas;
  - `ov_archivos_orden_fk`.
- **Trigger `ov_archivos_guarda`:**
  - DELETE: `42501 ov_archivos_sin_borrado`;
  - un UPDATE solo pasa `borrado_at` y `borrado_por` de NULL a valor, una vez. Lo demás da `42501 ov_archivos_inmutable`.
- **Sin TRUNCATE:** `ov_archivos_sin_truncate`.
- **Orden con Storage** (no comparten transacción): primero subes el objeto y **después** insertas la fila; para borrar, primero marcas `borrado_at` y **después** borras el objeto.

### 3.8 `ops.stock_almacen`

- **Llaves.**
  - `stock_almacen_pkey` `(sku, almacen)`.
  - FK compuesta `stock_almacen_almacen_fk`: un saldo en una bodega de Odoo no se puede ni escribir (`23503`).
- **CHECK.**

  | CHECK | Regla |
  |---|---|
  | `stock_almacen_fuente_chk` | `fuente = 'kubera'` |
  | `stock_almacen_fisico_chk` | `fisico` ≥ 0 |
  | `stock_almacen_apartado_chk` | `apartado` ≥ 0 |

  **No hay CHECK `apartado <= fisico`, a propósito:** un conteo puede dejar `libre` < 0, y la vista lo avisa.
- **Columna generada:** `libre = fisico − apartado`, stored. No se escribe.
- **Sin FK a `core.products`, a propósito.**
- **Trigger `stock_almacen_guarda`** (BEFORE INSERT y UPDATE):
  - `sku`, `almacen` y `fuente` son fijos: `42501 stock_almacen_llave_fija`;
  - **al subir `apartado`:** `fisico − apartado` no puede quedar < 0 (`23514 stock_almacen_libre_guarda`), y la bodega tiene que ser de kubera con `admite_ov` (`23514 stock_almacen_admite_ov_guarda`);
  - mantiene `actualizado_at`.
- **Al COMMIT:**
  - **`stock_almacen_cuadra`** (INSERT, UPDATE de `fisico` o DELETE) llama a `ops.verificar_libro`: `fisico` = Σ `delta` = `saldo_despues` del último movimiento. Si no, `23514 stock_libro_cuadra`;
  - **`stock_almacen_apartado_cuadra`** (INSERT, UPDATE de `apartado` o DELETE) llama a `ops.verificar_apartado`: `apartado` = Σ `reservado` de los renglones sin entregar de OV confirmadas y vivas, y ningún renglón de una OV no confirmada aparta. Si no, `23514 stock_apartado_cuadra`.
- **Sin TRUNCATE:** `stock_almacen_sin_truncate`. El DELETE no tiene guarda de fila, pero `stock_almacen_cuadra` lo rechaza si el libro de esa llave no suma 0. **No borres filas en 0: se quedan.**
- **Lo que impide:**
  - un `update … set fisico = …` sin su movimiento;
  - apartar más de lo libre;
  - apartar fuera de kubera o en una bodega sin `admite_ov`;
  - un apartado que no cuadra con los renglones.

### 3.9 `ops.stock_formato`

- **Llaves y FK:**
  - `stock_formato_almacen_fk`, compuesta, solo kubera;
  - `stock_formato_reemplaza_fk`;
  - `stock_formato_dividido_fk`.
- **CHECK.**

  | CHECK | Regla |
  |---|---|
  | `stock_formato_fuente_chk` | `fuente = 'kubera'` |
  | `stock_formato_estado_chk` | {por_confirmar, confirmado, descartado} |
  | `stock_formato_nombre_chk` | `archivo_nombre` no vacío |
  | `stock_formato_hash_chk` | `^[0-9a-f]{64}$` |
  | `stock_formato_reemplaza_chk` | `reemplaza_a` ≠ `id` |
  | `stock_formato_dividido_chk` | `dividido_de` ≠ `id`, y nunca junto con `reemplaza_a` |
  | `stock_formato_conf_chk` | confirmado ⇔ hay `confirmado_at`, y con `confirmo_bodega` no vacío |
  | `stock_formato_conf_q_chk` | `confirmado_at` y `confirmado_por` van juntos |
  | `stock_formato_desc_chk` | descartado ⇒ `descartado_motivo` ≥ 5 |
  | `stock_formato_desc_q_chk` | descartado ⇔ hay `descartado_at`, y `descartado_at` y `descartado_por` van juntos |
  | `stock_formato_rev_chk` | `rev` ≥ 1 |

- **Columna generada:** `folio = 'FMT-' || lpad(id, 5)`. Una identidad puede dejar huecos: no los ve un cliente.
- **Único parcial `stock_formato_hash_uq` (`archivo_hash`):** entre formatos no descartados y que no son hijos (`dividido_de is null`).
- **Trigger `stock_formato_guarda`:**
  - DELETE: `42501 stock_formato_sin_borrado`;
  - nace `por_confirmar`; si no, `23514 stock_formato_transicion_chk`;
  - un hijo (`dividido_de`) tiene el mismo `archivo_hash` y la misma bodega que el padre; si no, `23514 stock_formato_dividido_hash_chk`;
  - **`confirmado` y `descartado` son terminales:** cualquier cambio da `42501 stock_formato_inmutable`;
  - en `por_confirmar`, `id`, `archivo_hash`, `cargado_por`, `cargado_at`, `dividido_de` y `almacen` son fijos (`42501 stock_formato_inmutable`).
- **Sin TRUNCATE:** `stock_formato_sin_truncate`.
- **El trigger no obliga a que `stock_formato.rev` suba:** el candado optimista es de tu sentencia.

### 3.10 `ops.stock_formato_linea`

- **Llaves.**
  - FK `stock_formato_linea_formato_fk`, sin cascade.
  - **`stock_formato_linea_uq` `(formato_id, sku, ubicacion)`, `nulls not distinct`, INMEDIATO:** un SKU repetido en la misma ubicación truena a media sentencia.
- **CHECK.**

  | CHECK | Regla |
  |---|---|
  | `stock_formato_linea_fila_chk` | `fila` > 0 |
  | `stock_formato_linea_cantidad_chk` | `cantidad` > 0 |
  | `stock_formato_linea_cant_arch_chk` | `cantidad_archivo` > 0 |
  | `stock_formato_linea_sku_arch_chk` | `sku_archivo` no vacío |
  | `stock_formato_linea_rev_chk` | `rev` ≥ 1. **Ojo:** el trigger también lanza este mismo nombre |
  | `stock_formato_linea_via_chk` | `salida_odoo_via` ∈ {tex2_bajo, tex2_cero, movimiento} |
  | `stock_formato_linea_salida_chk` | `salida_odoo_at` y `salida_odoo_via` van juntos; `movimiento` exige `salida_odoo_ref` |

- **Trigger `stock_formato_linea_guarda`:**
  - **INSERT:**
    - en un formato que no está `por_confirmar`: `42501 stock_formato_linea_inmutable`;
    - un renglón nunca nace con la puerta abierta: `23514 stock_formato_linea_puerta_chk`.
  - **Con la puerta abierta** (`salida_odoo_at` puesto): no se edita ni se borra (`42501 stock_formato_linea_inmutable`).
  - **DELETE:** solo en un formato `por_confirmar`.
  - **Fijos siempre:** `id`, `formato_id`, `fila`, `sku_archivo` y `cantidad_archivo` (`42501`).
  - **`rev`** solo sube de uno en uno: `23514 stock_formato_linea_rev_chk`.
  - **Formato `por_confirmar`:** se edita libre, pero la puerta no se abre (`23514 stock_formato_linea_puerta_chk`).
  - **Formato `descartado`:** nada cambia (`42501`).
  - **Formato `confirmado`:**
    - solo cambian `cantidad`, `nota`, `rev`, `odoo_tex2_al_confirmar`, `salida_odoo_at`, `salida_odoo_via`, `salida_odoo_ref` y `odoo_tex2_al_abrir` (`42501`);
    - `cantidad` solo baja (`23514 stock_formato_linea_solo_baja`);
    - bajarla exige una `nota` nueva de 5 caracteres o más (`23514 stock_formato_linea_nota_chk`) y `rev = rev + 1` (`23514 stock_formato_linea_rev_chk`).
- **Sin TRUNCATE:** `stock_formato_linea_sin_truncate`.

### 3.11 `ops.stock_formato_evento`

- **CHECK.**
  - `stock_formato_evento_evento_chk` ∈ {`cargado`, `renglon_editado`, `renglon_quitado`, `dividido`, `reemplazado`, `descartado`, `confirmado`, `puerta_abierta`, `cantidad_bajada`}.
  - `stock_formato_evento_nota_chk`: `cantidad_bajada` exige `nota` ≥ 5.
- **`linea_id` va sin FK:** el renglón pudo quitarse.
- **Solo se agrega:** `42501 stock_formato_evento_solo_agregar`, también en el TRUNCATE.

### 3.12 `ops.stock_mov` (el libro)

- **Llaves.**
  - `stock_mov_clave_uq` (`clave`).
  - FK compuesta `stock_mov_almacen_fk`.
  - FK `stock_mov_ov_linea_fk`.
  - **`stock_mov_salida_ov_uq`** `(ov_linea_id) where motivo = 'salida_ov'`: una sola salida por renglón.
- **CHECK.**

  | CHECK | Regla |
  |---|---|
  | `stock_mov_fuente_chk` | `fuente = 'kubera'` |
  | `stock_mov_delta_chk` | `delta` ≠ 0 |
  | `stock_mov_saldo_chk` | `saldo_despues` ≥ 0 |
  | `stock_mov_motivo_chk` | {entrada, salida_ov, traspaso_salida, traspaso_entrada, devolucion, ajuste_conteo, merma, correccion} |
  | `stock_mov_signo_chk` | entrada, traspaso_entrada y devolucion: > 0. salida_ov, traspaso_salida y merma: < 0. ajuste_conteo y correccion: cualquier signo |
  | `stock_mov_nota_chk` | ajuste_conteo, merma y correccion exigen `nota` ≥ 5 |
  | `stock_mov_ref_chk` | `entrada` exige `ref` |
  | `stock_mov_ov_chk` | `ov_linea_id` **solo y siempre** en salida_ov y devolucion |
  | `stock_mov_clave_chk` | La forma de la clave según el motivo (§4.5) |

- **Solo se agrega:** `42501 stock_mov_solo_agregar`, también en el TRUNCATE.
- **Al COMMIT:**
  - **`stock_mov_cuadra`** (INSERT): la cadena `saldo_despues = anterior + delta` (`23514 stock_libro_cadena`) y `ops.verificar_libro` (`23514 stock_libro_cuadra`);
  - **`stock_mov_devolucion_cuadra`** (INSERT con `motivo = 'devolucion'`): Σ devuelto del renglón ≤ `entregado`. Si no, `23514 stock_devolucion_de_mas`. Es el tope real, también con dos recepciones simultáneas.
- **«El último movimiento por id» es válido solo si** todo escritor bloquea antes la fila de saldo y la tiene hasta el commit (0065, comentario de la sección 8). Ese es tu contrato (§4.3).

### 3.13 `ops.devoluciones`

- **Llaves.**
  - `devoluciones_clave_uq`: la clave anti doble clic.
  - FK `devoluciones_ov_linea_fk` y `devoluciones_partida_fk`.
- **CHECK.**

  | CHECK | Regla |
  |---|---|
  | `devoluciones_partida_chk` | `partida_de` ≠ `id` |
  | `devoluciones_origen_chk` | {venta, retiro_full} |
  | `devoluciones_cantidad_chk` | > 0 |
  | `devoluciones_paquete_ref_chk` | ≥ 4 caracteres |
  | `devoluciones_paquete_ids_chk` | Al menos uno, sin NULL, cada uno `[A-Z0-9]+` |
  | `devoluciones_empaque_chk` | {cerrado, abierto, danado, sin_empaque} |
  | `devoluciones_dictamen_v_chk` | {vendible, merma, reparar, proveedor} |
  | `devoluciones_venta_chk` | `origen='venta'` ⇔ hay `ov_linea_id` |
  | `devoluciones_retiro_chk` | `retiro_full` exige `canal` y `cuenta` |
  | `devoluciones_nota_chk` | Lo que no es venta lleva `nota` ≥ 5 |
  | `devoluciones_dictamen_chk` | `dictamen`, `dictamen_at` y `dictamen_por` van juntos |
  | `devoluciones_resuelta_chk` | `resuelta_at` solo con dictamen vendible, merma o proveedor. `reparar` no resuelve |
  | `devoluciones_clave_chk` | ≥ 8 caracteres |
  | `devoluciones_rev_chk` | ≥ 1 |

- **Columna generada:** `folio = 'DEV-' || lpad(id, 5)`.
- **Únicos parciales: la llave del hecho.**
  - `devoluciones_recepcion_uq` `(ov_linea_id, sku, paquete_ids)`;
  - `devoluciones_retiro_uq` `(canal, cuenta, sku, paquete_ids) where origen = 'retiro_full'`;
  - los dos sin las filas partidas (`partida_de is null`). **Un `23505` de cualquiera de los dos significa «ya recibida».**
- **Trigger `devoluciones_guarda`:**
  - DELETE: `42501 devoluciones_sin_borrado`;
  - resuelta (`resuelta_at`) es terminal: `42501 devoluciones_inmutable`;
  - `id`, `origen`, `ov_linea_id`, `sku`, `recibido_at`, `recibido_por`, `clave` y `partida_de` son fijos (`42501 devoluciones_inmutable`);
  - **normaliza `paquete_ids`:** lo ordena y le quita repetidos, en cada INSERT y UPDATE;
  - una fila partida es del mismo paquete, renglón (o retiro), SKU y canal que su original, y la original no puede ser partida. Si no, `23514 devoluciones_partida_igual_chk`;
  - si la fila lleva `canal`, `cuenta` o `external_order_id` y tiene `ov_linea_id`, tienen que ser exactamente el `mp_*` de su OV, y la OV tiene que tener `mp_orden`. Si no, `23514 devoluciones_venta_ov_chk`.
- **Sin TRUNCATE:** `devoluciones_sin_truncate`.
- **`cantidad` y `dictamen` sí cambian antes de resolver.** Partir el paquete baja una fila y agrega otra con `partida_de`.

### 3.14 Las vistas vigía

**Columnas:** `problema`, `sku`, `almacen`, `ref`, `esperado`, `encontrado` y `detalle`. Las dos vistas tienen `security_invoker = on`.

**`ops.stock_apartado_descuadre_v`.** Valores de `problema`:

| `problema` | Qué detecta |
|---|---|
| `apartado_descuadrado` | `apartado` ≠ Σ `reservado` |
| `apartado_en_ov_no_confirmada` | Un renglón aparta en una OV que no está confirmada |
| `libre_negativo` | `libre` < 0. **Legítimo tras un conteo** |
| `apartado_sin_admite_ov` | Apartado en una bodega sin `admite_ov` |
| `devolucion_de_mas` | Lo devuelto de un renglón pasa de lo entregado |
| `formato_sin_cuadrar` | Formato confirmado: Σ de entradas con `ref = folio` ≠ Σ de renglones con la puerta abierta |
| `revision_descuadrada` | El físico de `REVISION` por SKU ≠ Σ `cantidad` de sus devoluciones abiertas |

**`ops.devoluciones_vs_canal_v`.** `problema = 'devuelto_de_mas_vs_canal'`: lo recibido por `external_return_id` pasa de lo que dice `channel.return_items`.

**Qué frena la base y qué solo ve la vista.** Los únicos constraint triggers son `stock_mov_cuadra`, `stock_almacen_cuadra`, los `*_apartado_cuadra` y `stock_mov_devolucion_cuadra`:
- **Frenados al COMMIT** (la vista es la segunda red): `apartado_descuadrado`, `apartado_en_ov_no_confirmada` y `devolucion_de_mas`.
- **Sin nada en la base que los frene** (la vista es la **única** red): `formato_sin_cuadrar` y `revision_descuadrada`. Ningún trigger liga la puerta (`salida_odoo_at`) con su `entrada` en el libro, ni una devolución abierta con el físico de `REVISION`. Una sentencia de puerta o de devolución mal escrita confirma sin error, y solo la vista la ve.
- **Pueden aparecer sin bug:** `apartado_sin_admite_ov`, si un acta apaga `admite_ov` con apartado vivo (la guarda de `stock_almacen` solo actúa al **subir** el apartado), y `libre_negativo`, tras un conteo.

### 3.15 `ops.stock_watch_photo.stock_kubera`

- **Qué es:** `integer` nullable.
- **La regla vive en el `comment`, no en un CHECK:**
  - NULL **solo** antes del encendido de TEX3, cuando ninguna bodega de kubera contaba;
  - ya encendida, se guarda 0 y **nunca** NULL (`coalesce(kub, 0)`).
- **La base no lo impone.** Le toca a stock_watch.

---

## 4. El contrato para el código

### 4.1 Una transición = una sentencia

Cada transición (crear, guardar, confirmar, entregar, cancelar, puerta, conteo, devolución…) es **un solo `execute`**: un `WITH` con todas sus escrituras y un `SELECT` final con `ops.exigir(...)`. Las reglas:

1. **Todo viaja junto:** estado, saldo, libro, renglones y mensaje o evento. Una sentencia es atómica. Dos sentencias en una transacción, detrás del pool, no lo son. La revisión técnica (C10, §5.3) lo explica así: SteadyDB puede repetir un `execute` que falló con OperationalError en **otra** conexión; las sentencias anteriores de esa transacción no van con él.
2. **Cada parte que escribe se encadena a la CTE guardia del encabezado** (`from o join …`). Si el CAS falla (`rev` o estado), nada más escribe, y `ops.exigir` deshace todo.
3. **Siempre `ops.exigir(cond, 'motivo')`**, una por cada cosa que tiene que cuadrar. Nunca `1/0`: un `22012` significa que copiaste una sentencia de la v2.
4. **Cada fila se toca una vez por sentencia.** Dos partes de un `WITH` que modifican la misma fila dan un resultado indefinido. Por eso confirmar pone `almacen` y `reservado` del renglón en **un** UPDATE.
5. **Una CTE de candados (`select … for update`) tiene que leerse en otra parte**, o Postgres no la ejecuta. Márcala `materialized`. Las CTE que escriben sí corren siempre.
6. **Los errores diferidos salen al COMMIT**, no en tu `execute`:
   - con `sdb.execute_returning(...)`, el commit va dentro: la excepción sale de esa llamada y la fila no llega;
   - con `with sdb.get_cursor() as cur:`, sale al cerrar el `with`.
   - **Ninguna escritura externa que dependa del resultado** (Slack, compra de guía de Temu, sale.order de `odoo_ventas`) antes de que el commit regrese bien.
   - **Las lecturas de Odoo y la subida del objeto a Storage van ANTES de la sentencia**, nunca dentro del `with` (§3.7, §4.7).
7. **El mensaje `tipo='sistema'` de cada transición va en la misma sentencia,** con su `evento` del catálogo. El evento del formato, igual. **Ojo:** un `KB001` deshace también el mensaje. Si quieres dejar `no_alcanzo` en el chat, escríbelo en **otra** transacción, después del fallo.
8. **Todas las CTE ven la misma foto.** Lo que escribe una CTE lo pasas a las demás por su `RETURNING`; releer la tabla no lo muestra.
9. **Un `UPDATE … where rev = %(rev)s` que no encuentra fila no es error en sí:** devuelve 0 filas. Envuélvelo en `ops.exigir(... = 1, 'motivo')` para que llegue como `KB001` con nombre.

### 4.2 Bloqueo optimista con `rev`

| Tabla | Quién | Cómo |
|---|---|---|
| `ov_ordenes.rev` | Personas (pantalla, API) | `where id = %(id)s and rev = %(rev)s and estado = <esperado> and borrada_at is null` y `set rev = rev + 1`. El trigger solo impide que **baje**: subirla es tuyo |
| `ov_ordenes` | Procesos (cancelación del canal, marcas de devolución) | CAS **por estado**, sin `rev`: `where id = … and estado = <esperado> and borrada_at is null`, más los filtros propios de cada marca (la del canal, en §4.9 g). Igual suben `rev`, para que la pantalla abierta vea el cambio. Reintento acotado; 0 filas significa que se relee, no que se pisa (comment de `ov_ordenes.rev`, revisión C7) |
| `stock_formato.rev` | Personas | `where id = … and rev = %(rev)s and estado = 'por_confirmar'` y `rev = rev + 1`. Sin trigger que lo fuerce |
| `stock_formato_linea.rev` | Bajar la cantidad en un formato confirmado | `where id = … and rev = %(rev)s and salida_odoo_at is null` y `rev = rev + 1`. **El trigger exige el +1** |
| `devoluciones.rev` | Dictamen o partir | `rev = rev + 1`. Sin trigger que lo fuerce |

Un `KB001` de tipo `*_cambio_rev` le dice a la persona «alguien la cambió, recarga». Antes de mostrarlo, **relee**: si el pool reintentó tras una conexión muerta (`_reintentar_transitorio` en `services/supabase_db.py`), tu propia escritura pudo haberse aplicado ya, y lo que ves es la segunda vuelta.

### 4.3 Orden de candados

Todas las sentencias bloquean en el mismo orden global. La regla escrita del encabezado de la 0065 es:

> «SIEMPRE: bodega FOR SHARE → saldo FOR UPDATE en orden (sku, almacen) → UPDATE → INSERT en el libro → ops.exigir(…), en UNA sentencia.»

Desplegado:

1. **La fila guardia del documento.**
   - `update ops.ov_ordenes … where id and rev/estado`;
   - en el alta de un borrador, `update ops.ov_folio`;
   - en un formato, `select … for share`. En «dividir», `for update`.
   - **Excepción: crear_auto.** `SQL_CREAR_AUTO` toma `almacenes` y `stock_almacen` primero y `ov_folio` al final (`alm → x → s → fo`), porque el folio solo sube si todo alcanzó (`fo` depende de `s`). **No la «corrijas» según esta lista:** perderías el folio sin huecos, o pondrías todas las altas en fila detrás de los candados de saldo. No hay ciclo: ninguna sentencia toma `ov_folio` y después `stock_almacen` (el alta de borrador no toca saldo).
2. **`ops.almacenes` de las bodegas involucradas, `FOR SHARE`.**
   - Espera a un acta que esté cambiando banderas.
   - Vuelve a validar `admite_ov` y `surte_ventas` dentro del candado (C11: lo que Python decidió antes se vuelve a exigir aquí).
3. **Renglones de formato:** `FOR UPDATE` en `order by l.id` (la CTE `lk` de `SQL_PUERTA`).
4. **`ops.stock_almacen`:** `FOR UPDATE` con `order by sa.sku, sa.almacen`, en una CTE `materialized` que se lee después. Un upsert entra con `order by sku`.
   - Si el traspaso toca dos bodegas, las dos filas van en el mismo orden. Por ejemplo, en `SQL_DEV_VENDIBLE`, `(sku, 'ENSAYO')` va antes que `(sku, 'REVISION')`.
5. **Escrituras:** UPDATE del saldo → INSERT en `stock_mov` con `saldo_despues` sacado del `RETURNING` del saldo → renglones, devoluciones y encabezado → mensaje o evento.
6. **`ops.exigir(...)` final.**

Además:
- **`ov_lineas_guarda` bloquea la fila de la OV (`FOR NO KEY UPDATE`)** cuando insertas renglones o tocas uno sin apartado. Por eso guardar empieza por el UPDATE de la OV.
- **Un `40P01` (deadlock)** significa que alguna sentencia rompió este orden. Reintenta una vez y avisa: es un bug.

### 4.4 Quién y cuándo; `app.usuario`

- **Las columnas `*_por` las pone tu código como parámetro:** `creado_por`, `confirmada_por`, `entregada_por`, `cancelada_por`, `borrada_por`, `entregado_por`, `cargado_por`, `confirmado_por`, `descartado_por`, `recibido_por`, `dictamen_por`, `stock_mov.quien`, `stock_formato_evento.quien`, `ov_mensajes.autor` y `ov_archivos.subido_por`.
  - Valores, según los comments de la 0064: un correo, `'servicio'` o `'automatico'`.
  - **De dónde sale:** `core.actor.actual()`. En crons, sondeos y webhooks vale `''` (nadie lo fijó): ahí pon `'automatico'` (`core.actor.AUTOMATICO`). **Nunca `''` ni `None`:** `creado_por`, `ov_mensajes.autor`, `stock_mov.quien`, `stock_formato_evento.quien`, `cargado_por`, `recibido_por` y `subido_por` son NOT NULL, y `None` da `23502`.
  - Los `*_nombre` son opcionales.
  - **Las tablas de OV y libro NO leen `app.usuario`.**
- **`creado_via` y `ov_mensajes.via`** solo aceptan {panel, api, claude, automatico} (`ov_ordenes_via_chk`, `ov_mensajes_via_chk`). `core.actor.origen_actual()` devuelve otro catálogo (`panel`, `script`, `claude`, `cron`): **no lo pases tal cual**, o da `23514`. Tradúcelo así:

  | Quién llama | `via` |
  |---|---|
  | Una persona en el panel (`request.state.identidad.tipo == 'persona'`) | `panel` |
  | La llave de máquina (`identidad.tipo == 'maquina'`, actor `servicio`) | `api`. **Hoy el middleware fija origen `panel` para todo**, también con `X-API-Key`: la distinción la haces tú con `identidad.tipo` |
  | Un chat de Claude que corre un script (`--via claude`) | `claude`. No hay cable desde una petición HTTP |
  | crear_auto, el vigilante de D4, cualquier cron (`cron`) | `automatico` |
  | Crear FULL | `panel` o `api`, **nunca `automatico`**: una OV full no tiene `mp_canal`, y con `automatico` `ov_ordenes_auto_cliente_chk` exige `cliente` = `mp_canal` (NULL). Además nace en borrador (decisión 12) |
  | Un script sin `--via` (`script`) | No tiene equivalente: decide con el coordinador antes de escribir |

  `stock_mov.via` no tiene CHECK; usa los mismos valores.
- **Los `*_at` van con `now()`, nunca con hora del cliente.** Dos guardas dependen de eso:
  - **`creado_at`:** usa el default. `ov_lineas_guarda` admite renglones en una OV ya confirmada solo si `creado_at = now()`, que es como inserta crear_auto;
  - **`confirmada_at = now()`:** sin eso, el UPDATE de confirmar sobre los renglones da `42501 ov_lineas_inmutable`.
- **`app.usuario` solo lo leen `ops.almacenes`** (guarda, historia y default de `actualizado_por`) **y `ops.migraciones`** (default de `quien`).
  - `sdb.get_cursor()` ya manda `set_config('app.usuario', …, true)` y `set_config('app.origen', …, true)` cuando hay actor (`_marcar_actor`). Lo hace en una sentencia aparte, dentro de la misma transacción.
  - En un acta sobre `ops.almacenes`, pon `select set_config('app.usuario', %(q)s, true);` **en el mismo execute** que el UPDATE. Un `set_config` mandado aparte se pierde si el pool repite la sentencia (C10).
- **Siempre `set_config(…, true)`, que es LOCAL a la transacción.** Nunca `SET` de sesión: en el pooler, la siguiente petición heredaría la firma.

### 4.5 Claves de idempotencia: formato exacto por caso

**`ops.stock_mov.clave`.** La forma la impone `stock_mov_clave_chk`; la unicidad, `stock_mov_clave_uq`.

| `motivo` | Regex del CHECK | Formato de negocio (comment de la columna) |
|---|---|---|
| `entrada` | `^(fmt:[0-9]+:.+\|dev:[0-9]+:recibe)$` | `fmt:<formato_id>:<sku>` (puerta de un formato; una sola entrada por formato y SKU); `dev:<devolucion_id>:recibe` (retiro de FULL que entra a REVISION) |
| `salida_ov` | `^ov:[0-9]+:linea:[0-9]+:salida$` | `ov:<orden_id>:linea:<linea_id>:salida`. **La misma** para entregar y para «¿salió?». Además, `stock_mov_salida_ov_uq` (una por renglón) |
| `traspaso_salida` | `^(dev:[0-9]+\|tras:.+):sale$` | `dev:<id>:sale` (dictamen vendible); `tras:<uuid al abrir>:sale` |
| `traspaso_entrada` | `^(dev:[0-9]+\|tras:.+):entra$` | `dev:<id>:entra`; `tras:<uuid al abrir>:entra` |
| `devolucion` | `^dev:[0-9]+:recibe$` | `dev:<devolucion_id>:recibe` |
| `ajuste_conteo` | `^conteo:[^:]+:[^:]+:[^:]+$` | `conteo:<sesion>:<sku>:<almacen>`. **Ninguna de las tres partes puede llevar `:`.** Usa un uuid sin dos puntos para la sesión |
| `merma` | `^(dev:[0-9]+:merma\|merma:.+)$` | `dev:<id>:merma` (dictamen merma); `merma:<uuid al abrir>` |
| `correccion` | `^corr:.+$` | `corr:<uuid al abrir el formulario>` |

**Otras claves.**

| Caso | Columna | Formato | Qué lo impone |
|---|---|---|---|
| OV de una venta (crear_auto) | `ov_ordenes.clave` | `mp:<canal>:<cuenta>:<orden>` | `ov_ordenes_clave_uq` y `ov_ordenes_mp_uq` (únicos entre vivas). **La forma no tiene CHECK**: la pone tu código. `mp_canal` en minúsculas y `mp_cuenta` en mayúsculas (`ov_ordenes_mp_forma_chk`) |
| OV de Crear FULL | `ov_ordenes.clave` | `full:<solicitud>:<tienda>:<bodega>` | `ov_ordenes_clave_uq` |
| Alta del panel | `ov_ordenes.clave` | El uuid de la pantalla | `ov_ordenes_clave_uq` |
| Recepción de devolución (el hecho) | `devoluciones` | `(ov_linea_id, sku, paquete_ids)`; en un retiro de FULL, `(canal, cuenta, sku, paquete_ids)` | `devoluciones_recepcion_uq` / `devoluciones_retiro_uq` |
| Doble clic en «Recibir devolución» | `devoluciones.clave` | El uuid de la pantalla al abrir (≥ 8 caracteres) | `devoluciones_clave_uq`, `devoluciones_clave_chk` |
| El mismo archivo de Bodega | `stock_formato.archivo_hash` | sha256 en hex minúsculas | `stock_formato_hash_chk`, `stock_formato_hash_uq` |
| El mismo PDF en una OV | `ov_archivos (orden_id, sha256)` | `ruta = <folio>/<sha256>.pdf` (comment) | `ov_archivos_vivo_uq` |

**`paquete_ids`:**
- es `paquete_ref` normalizado (comment de la columna): un id por elemento, en mayúsculas, sin espacios ni guiones;
- **la función, como la usan las pruebas:** parte `paquete_ref` por `/` (y por `,` o `;` si Bodega los usa: confírmalo); en cada pedazo quita espacios y guiones **por dentro**, pasa a mayúsculas y descarta los vacíos. Ejemplos del verificador: `«4471-2290 11»` → `[4471229011]`; `«GUIA-B 77 / GUIA-A 12»` → `[GUIAB77, GUIAA12]`. **No partas por espacios:** dos turnos darían arreglos distintos para la misma caja y `devoluciones_recepcion_uq` dejaría de verla;
- **escríbela una sola vez** y úsala en la pantalla y en el cruce diario contra el folio de iFull en Odoo, que pide la misma normalización (DEVOLUCIONES §4b y §7.2);
- el trigger lo ordena y le quita repetidos;
- **nunca lleva nombre ni dirección.**

### 4.6 Qué hacer ante cada error

Distingue los errores por `e.pgcode` y `e.diag.constraint_name`, **nunca por el texto**. La única excepción es `KB001`, cuyo mensaje **es** el motivo por contrato.

| SQLSTATE | Cuándo sale | Cómo se identifica | Reacción del código |
|---|---|---|---|
| **`KB001`** | Un `ops.exigir` falso o NULL | `e.diag.message_primary` = motivo | **Depende del motivo** (tabla abajo). Los de negocio: 409 con el motivo y **sin alerta**. Los de invariante (`renglones_no_cuadran`, `cancelar_no_cuadra`, `devolucion_no_cuadra`, `*_escrituras_no_cuadran`, `regla_de_negocio`): son bugs, se registran y **avisan** |
| **`23505`** | Único repetido | `constraint_name` | Ver la tabla de 23505 abajo. El pool **no** reintenta errores de integridad |
| **`23514`** | CHECK, transición inválida o invariante al COMMIT | `constraint_name` (§3) | Casi siempre es un bug de tu sentencia o una validación previa que faltó: **no reintentes**, registra y avisa. Hay excepciones de usuario, abajo |
| **`23503`** | FK: bodega que no es de kubera, u OV, formato o renglón inexistente | `constraint_name` | Validación que faltó en Python. Elige el mensaje por `constraint_name`: `*_almacen_fk` → 400 «esa bodega no es de kubera»; `*_orden_fk`, `*_formato_fk` o `*_ov_linea_fk` → 400 «la OV, el formato o el renglón no existe». **La FK no revisa `admite_ov`:** `TEX3` apagada o `REVISION` pasan en un borrador y fallan al confirmar (`KB001 renglon_sin_plan_o_sin_saldo`). Valida `admite_ov` en Python al capturar el renglón |
| **`23502`** | NOT NULL: un `*_por`, `autor` o `quien` vacío (`None`) | `e.diag.column_name` | Bug: el actor no se resolvió (§4.4). Registra y avisa |
| **`42501`** | Solo agregar, inmutable, sin borrado o sin TRUNCATE | `constraint_name`: `<tabla>_solo_agregar`, `*_inmutable`, `*_sin_borrado`, `<tabla>_sin_truncate`, `ov_folio_solo_sube`, `almacenes_codigo_fuente_fijos`, `stock_almacen_llave_fija` | Bug: intentaste editar algo congelado. Excepción: `ov_lineas_inmutable` al insertar o editar un renglón mientras otra transacción confirmaba es concurrencia legítima: «la OV ya se confirmó, recarga». Ojo: 42501 también es «permiso insuficiente»; sin `constraint_name`, es un problema de rol o grant |
| **`55006`** | TRUNCATE con eventos diferidos pendientes | — | Tu código nunca hace TRUNCATE |
| **`55P03`** | Venció `lock_timeout` | — | «Bodega ocupada, intenta de nuevo». Reintentar la sentencia completa es seguro, porque es atómica y está guardada; hazlo **una** vez como máximo (el pool pudo haberla repetido ya, C10). Si sigue, responde 503 o 409 |
| **`57014`** | Venció `statement_timeout` | — | Igual que 55P03. Si se repite, avisa: una sentencia lenta o un candado colgado |
| **`40P01`** | Deadlock | — | Reintenta una vez y avisa: se rompió el orden de §4.3 |
| **`42P01`** | La tabla no existe | — | No debe pasar si preguntas antes con `to_regclass` (§4.8). Si pasa, sigue el camino de hoy y avisa una vez |
| **`22012`** | División entre cero | — | Alguien copió una sentencia de la v2 con `1/0`: reemplázala por `ops.exigir` |
| **`KB000`** | Paso 0 de una migración | — | Solo en migraciones. Tu código nunca lo ve |

**`23505` por `constraint_name`.**

| `constraint_name` | Significa | Reacción |
|---|---|---|
| `ov_ordenes_clave_uq`, `ov_ordenes_mp_uq` | **En un alta** (crear_borrador, crear_auto): la OV de esa clave o venta ya existe | Relee y devuelve `ya_existia`, que cuenta como éxito. En crear_auto basta con volver a correr la **misma** sentencia: con la foto nueva, `previa` la ve (patrón en §4.9) |
| `ov_ordenes_clave_uq`, `ov_ordenes_mp_uq` | **En un UPDATE que saca una OV de `cancelada`** (la salida tardía, §4.9 h): la venta ya tiene **otra** OV viva con la misma clave. Pasa porque una `cancelada` libera su clave y, si la canceló una persona o el sistema, la venta vuelve al planeador y crear_auto crea otra (plan v3 §5) | **Nunca** es `ya_existia`: la `salida_ov` no se escribió y el libro tiene piezas de más. Conflicto: avisa y resuélvelo a mano (por ejemplo, cancelar antes la OV nueva) |
| `devoluciones_recepcion_uq`, `devoluciones_retiro_uq` | Esa caja ya se recibió para ese renglón o retiro | Relee la devolución viva y devuelve «ya recibida» |
| `devoluciones_clave_uq` | Doble clic en la misma pantalla | Relee por `clave` y devuelve esa fila |
| `stock_mov_clave_uq` | Ese hecho ya está en el libro (puerta, conteo de esa sesión, merma o corrección de ese uuid). La sentencia entera se deshizo | Relee el movimiento por `clave`. Si es el mismo hecho, devuelve «ya aplicado». Si difiere (por ejemplo, otro conteo con la misma sesión), avisa: es una clave mal armada |
| `stock_mov_salida_ov_uq` | Ese renglón ya tiene su salida | Relee la OV. Lo normal es que llegue antes un `KB001` por estado o `rev` |
| `stock_formato_hash_uq` | Ese archivo ya está vivo como otro formato | Busca el formato vivo por `archivo_hash` y di «Este archivo ya es el FMT-…» |
| `ov_archivos_vivo_uq` | Ese PDF ya está en la OV | Relee y devuelve `ya_existia`. El objeto de Storage que subiste queda huérfano: aceptado por diseño |
| `ov_lineas_sku_alm_uq`, `ov_lineas_linea_uq` (al COMMIT) | Guardar dejó dos renglones con el mismo SKU y bodega, o el mismo número de `linea` | Bug de guardar o validación faltante: agrupa por SKU y bodega, y renumera `linea` de 1 a n |
| `stock_formato_linea_uq` | La carga trae el mismo SKU y ubicación dos veces | Súmalos en Python antes de insertar (plan v3 §3: «se suman, con aviso») |
| `ov_ordenes_folio_uq` | El contador se desfasó | **Nunca** es `ya_existia`. Avisa: alguien tocó `ov_folio` |

**`23514` que sí son de usuario.**
- **`stock_devolucion_de_mas`, al COMMIT:** dos recepciones simultáneas del mismo renglón. Relee y di «ya se devolvió todo lo entregado».
- **`ov_ordenes_conf_chk`** en una salida tardía: esa OV nunca estuvo confirmada.
- **`stock_almacen_fisico_chk` o `stock_mov_saldo_chk`:** no hay físico para esa salida o corrección. Por ejemplo, después de un conteo que dejó `libre` < 0. Pide un conteo, o entrega menos.
- **`ov_ordenes_canal_cancelo_chk` al entregar:** el canal ya canceló y la OV espera el «¿salió?» (§4.9).
- **`almacenes_*`:** solo en actas.

**Motivos de `KB001` que usan las sentencias del verificador.** Mantén estos nombres. Las sentencias nuevas nombran los suyos en `snake_case`.

Hay dos clases, y el clasificador las separa:
- **De negocio (sin alerta):** todos los de la tabla salvo los de la línea siguiente. Por omisión, 409 con el motivo; la columna «Qué hace el código» dice cuándo releer o cuándo es éxito.
- **De invariante (bug, con alerta):** `renglones_no_cuadran`, `cancelar_no_cuadra`, `devolucion_no_cuadra` y `regla_de_negocio`.
- **Mixtos:** `entrega_no_cuadra`, `puerta_no_cuadra` y `dividir_no_cuadra` juntan en un solo `exigir` una condición de negocio (relee) y los conteos de escrituras (bug). **Al adaptarlas, parte ese `exigir` en dos:** el motivo de siempre para lo de negocio, y `<operacion>_escrituras_no_cuadran` (por ejemplo `entrega_escrituras_no_cuadran`) para los conteos, que sí avisa.

| Motivo | Sentencia (constante del verificador) | Qué hace el código |
|---|---|---|
| `ov_no_esta_en_borrador_o_cambio_rev` | `SQL_CONFIRMAR` | Relee. Puede ser `rev` vieja, ya confirmada, borrada, o **una OV full sin `envio_ref`**: el WHERE la excluye. Valida `envio_ref` antes para dar un mensaje claro |
| `renglon_sin_plan_o_sin_saldo` | `SQL_CONFIRMAR` | Algún renglón no vino en el plan, su bodega no es de kubera con `admite_ov`, o **no tiene fila en `stock_almacen`**, que equivale a saldo 0 |
| `no_alcanzo` | `SQL_CONFIRMAR`, `SQL_CREAR_AUTO` | No hay `libre` suficiente; no se apartó nada. En crear_auto también significa que la bodega no tiene `surte_ventas` o `admite_ov` (C11), o que el SKU no tiene fila de saldo. **Antes de regresar la venta al planeador, relee la OV viva por `mp_*`** (revisión, hallazgo 3) |
| `renglones_no_cuadran` | `SQL_CONFIRMAR`, alta de borrador (`Ctx.ov`), `SQL_FORMATO_CARGA` | Bug: el número de renglones escritos no coincide |
| `ov_no_esta_confirmada_o_cambio_rev` | `SQL_ENTREGAR` | Relee: `rev` vieja, ya entregada, cancelada o borrada |
| `entrega_no_cuadra` | `SQL_ENTREGAR` | Algún renglón pedido ya salió, no aparta completo, trae `n` fuera de 0..`cantidad`, o su bodega no es de kubera. Relee. **Mixto:** si `s` cuadra con `p` pero `r` o `m` no, es bug |
| `ov_no_cancelable_o_cambio_rev` | `SQL_CANCELAR` | Relee |
| `cancelar_no_cuadra` | `SQL_CANCELAR` | Bug |
| `formato_no_esta_por_confirmar` | `SQL_FORMATO_CONFIRMAR` | «Ya estaba confirmado» o «alguien lo cambió, recarga»: relee |
| `formato_sin_renglones` | `SQL_FORMATO_CONFIRMAR` | «El formato no tiene renglones» |
| `dividir_no_cuadra` | `SQL_FORMATO_DIVIDIR` | El padre no está `por_confirmar`, algún SKU no estaba en él, o un SKU tiene más de un renglón: la sentencia compara los renglones movidos contra el número de SKUs. Con varias ubicaciones, adáptala. **Mixto:** `i` ≠ `d` es bug |
| `puerta_no_cuadra` | `SQL_PUERTA` | El formato no está confirmado, o su bodega no es de kubera: relee. **Mixto:** si los conteos de escrituras (`s`, `m` contra `e`) no coinciden, es bug |
| `devolucion_de_mas_o_sin_venta` | `SQL_DEV_RECIBIR` | La OV no está entregada ni entregada_cancelada, o Σ devuelto + n > entregado (foto del inicio de la sentencia) |
| `devolucion_no_cuadra` | `SQL_DEV_RECIBIR` | Bug |
| `dictamen_no_cuadra` | `SQL_DEV_VENDIBLE` | Ya estaba resuelta (relee), ya tenía dictamen, o no hay fila de saldo en `REVISION` para ese SKU. **Ojo:** el patrón solo toma filas con `dictamen is null`, así que una fila en `reparar` (que no resuelve) nunca pasa a vendible con él. Es un hueco (§5.3, punto 23) |
| `salio_tarde_no_cuadra` | `SQL_SALIO_TARDE` | La OV no está `cancelada`, o no tiene renglones sin entrega con fila de saldo |
| `salio_no_cuadra` | «¿salió?» con el paquete enviado (prueba `salio_canal_cancelo_con_paquete_enviado`) | La OV no está `confirmada` con la marca `canal_cancelo_at`, o está borrada. Los renglones que ya se entregaron en una entrega parcial quedan fuera, y eso es lo correcto |
| `canal_cancelo_no_aplica` | La marca del paso 1 de §4.9 (g). Nombre de esta guía | Relee. Si ya tiene la marca, es la misma cancelación vista otra vez: devuelve `ya_marcada` como **éxito**. Si está `entregada`, usa el patrón de la OV entregada (§4.9 g). Si está cancelada o borrada, regístralo sin alerta |
| `canal_cancelo_entregada_no_aplica` | El canal cancela una OV `entregada` (§4.9 g). Nombre de esta guía | Relee. Si ya es `entregada_cancelada`, devuelve `ya_cancelada` como éxito |
| `recontar` | `SQL_CONTEO` | El físico cambió desde que se contó: pide recontar |
| `regla_de_negocio` | El default de `ops.exigir` con motivo vacío | No mandes motivos vacíos |

### 4.7 Reglas del pooler

El backend entra por Supavisor en modo transacción, puerto 6543: **la conexión del servidor se comparte entre clientes.**

1. **Sin estado de sesión.**
   - **Sí:** `SET LOCAL`, `set_config(…, true)`, `pg_advisory_xact_lock(…)`.
   - **No:** `SET` a secas, `set_session(...)`, `SET SESSION CHARACTERISTICS …`, advisory locks de sesión, prepared statements con nombre, cursores con nombre, LISTEN/NOTIFY.
   - **Nunca `set_session(readonly=True)`.** Ya envenenó el pool y tiró escrituras de producción (regla 13 del `CLAUDE.md`). Si de verdad necesitas solo lectura: `BEGIN; SET TRANSACTION READ ONLY; …; ROLLBACK;`, o el puerto 5432.
2. **Los timeouts van en el mismo `execute` que la sentencia,** como `SET LOCAL`. Los valores de la revisión (C3) son `lock_timeout` de 4 s y `statement_timeout` de 15 s. Con 6 conexiones en el pool (`maxconnections=6`, `blocking=True`), una espera larga de candado tumba el backend.
3. **Nada de red dentro de la transacción.**
   - Lee Odoo **antes**: la puerta recibe `%(listos)s` ya leído, y confirmar formato recibe `%(tex2)s`.
   - Sube a Storage **antes** de insertar `ov_archivos`.
   - Compra la guía de Temu **después** del commit de crear_auto. El plan v3 §5 dice: OV primero, compra después. Lo mismo con la sale.order que `odoo_ventas` crea, como hoy, para la parte de TEXCO o TEX2: después del commit de la OV.
   - Avisa a Slack después del commit.
4. **Dentro de una corrutina,** toda llamada a `sdb.*` va en `asyncio.to_thread` (regla 11 del `CLAUDE.md`).
5. **`application_name` no identifica conexiones:** a través del 6543 siempre vale `'Supavisor'`. Para identificar una conexión, usa `select pg_backend_pid()` **dentro de la misma transacción** (así lo hace el verificador desde `bf6edd4`).
6. **Valores interpolados.** psycopg2 interpola en el cliente: el planificador ve constantes. Un `%` literal en una sentencia con parámetros va como `%%`. Los arreglos de SKU van `sku = any(%(skus)s::citext[])`, nunca `sku::text = …`, porque eso anula el índice citext.

### 4.8 Tolerar que las tablas no existan

Desde el 6-oct a las 17:40 UTC existen en producción. La guarda se queda: no cuesta nada y protege cualquier ambiente sin las migraciones, como un sandbox recreado o una base local.

Hay código que lee estas tablas **sin depender de una bandera**:
- el planeador, Crear FULL y Temu leen `ops.almacenes` en cada llamada;
- la sentencia de kubera de stock_watch y los candados `hay_stock_kubera` se evalúan siempre.

Todo ese código pregunta primero, con caché de 60 s (revisión, hallazgo 1):

```sql
select to_regclass('ops.almacenes') is not null and to_regclass('ops.stock_almacen') is not null;
```

- **Si da false:** sigue el camino de hoy (las bodegas fijas de hoy, sin kubera) y avisa **una vez**.
- **Prueba de esto** (revisión, hallazgo 1): el código nuevo con 0 cambios en Woo cuando las tablas no existen. **No busques una base sin las migraciones** (el único sandbox ya las tiene): simula el `false` reemplazando la función del caché de `to_regclass` en la prueba.

**Las cuatro banderas.** Todas viven como fila en `ops.automatizacion_flags` (`flag`, `valor`, `motivo`, `actualizado_por`), con el mismo molde que `odoo_ventas_enabled` en `services/odoo_ventas.py` (caché corto, lectura en `asyncio.to_thread`). Así se apagan sin reiniciar el contenedor (regla 12 del `CLAUDE.md`). Las dos primeras eran variables en el plan v3; la revisión SEG-05 las pasa a filas y deja la variable solo como respaldo.

| Llave en `ops.automatizacion_flags` | Variable de respaldo (vale `false`) | Qué gobierna | Quién la lee |
|---|---|---|---|
| `ordenes_venta` | `ORDENES_VENTA_ENABLED` | Las pantallas y rutas de OV | `ordenes_venta` y sus rutas |
| `stock_watch_lee_kubera` | `STOCK_WATCH_LEE_KUBERA` | Que stock_watch sume el libre de kubera | stock_watch |
| `ov_generacion_auto` | La que elija el código; si no hay, apagada | crear_auto y que el planeador asigne a TEX3 | El planeador y quien llame a crear_auto (`odoo_ventas`, plan v3 §5) |
| `inventario_libro` | La que elija el código; si no hay, apagada | Formatos, puerta, conteo y devoluciones | `inventario_libro` y sus rutas |

- **Las filas NO se siembran** (decisión 13 de la 0064): sin fila, manda la variable de respaldo, que tiene que valer `false`. La fila la crea el acta que la enciende, con su motivo.
- **Si la lectura falla** (tabla ausente, kubera caída, error), la bandera se toma **apagada** en todo lo que aparta o vende (SEG-05). Ojo: el molde de `odoo_ventas` cae al valor de la variable; aquí la variable vale `false`, así que da lo mismo, pero no la pongas en `true`.

### 4.9 Patrones SQL

Todos salen del verificador (`backend/scripts/verificar_0064_0065.py`). Donde dice «adaptado», el cambio se indica. Ninguno es código de producción tal cual: cambia `'prueba'` por la vía real, agrega `*_nombre`, y para los textos de `cuerpo` y `nota` aplica la regla de no meter datos del comprador.

**(a) La envoltura.** Un solo `execute`. El esqueleto es de esta guía; los `set local` son la recomendación C3.

```sql
set local lock_timeout = '4s';
set local statement_timeout = '15s';
with o as ( /* CTE guardia: update … where id = %(id)s and rev = %(rev)s and estado = … returning … */ ),
     /* … candados materialized, escrituras encadenadas a o … */
     msg as ( /* insert into ops.ov_mensajes … from o */ )
select ops.exigir((select count(*) from o) = 1, '<motivo>')
     + ops.exigir(/* lo demás cuadra */, '<motivo>') as cuadra;
```

**(b) Alta de borrador.** Adaptado de `Ctx.ov`:
- sale el parámetro `creado_at` de la prueba, porque el default es `now()`;
- se agregan `creado_nombre` y `autor_nombre`, que existen en la 0064;
- faltan `titulo`, `imagen` y `precio_unitario`.

Un `23505 ov_ordenes_clave_uq` significa: relee por `clave` y devuelve `ya_existia`. El folio no se consume, porque la sentencia entera se deshace.

```sql
with f as (
  update ops.ov_folio set ultimo = ultimo + 1 where id = 1 returning ultimo
), o as (
  insert into ops.ov_ordenes (folio, estado, tipo, cliente, canal, mp_canal, mp_cuenta, mp_orden,
                              full_tienda, envio_ref, creado_por, creado_nombre, creado_via, clave)
  select 'OV-' || lpad(f.ultimo::text, greatest(5, length(f.ultimo::text)), '0'), 'borrador', %(tipo)s,
         %(cliente)s, %(canal)s, %(mc)s, %(mu)s, %(mo)s, %(ft)s, %(env)s, %(q)s, %(nombre)s, %(via)s, %(clave)s
    from f
  returning id, folio, rev
), l as (
  insert into ops.ov_lineas (orden_id, linea, sku, cantidad, almacen)
  select o.id, t.ord, t.e->>0, (t.e->>1)::int, t.e->>2
    from o, jsonb_array_elements(%(lineas)s::jsonb) with ordinality as t(e, ord)
  returning id
), m as (
  insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, autor_nombre, via)
  select o.id, 'sistema', 'creada', %(cuerpo)s, %(q)s, %(nombre)s, %(via)s from o
  returning id
)
select o.id, o.folio, o.rev,
       ops.exigir((select count(*) from l) = jsonb_array_length(%(lineas)s::jsonb), 'renglones_no_cuadran') as cuadra
  from o;
```

**Guardar un borrador.** El verificador no tiene la sentencia. La prueba `guardar_borrador_llaves_diferidas` muestra lo que la base permite dentro de una transacción: intercambiar `linea`, borrar y volver a insertar el mismo SKU, y `update ops.ov_ordenes set …, rev = rev + 1`. Hazlo en **una** sentencia:
1. CTE guardia: `update ops.ov_ordenes … where id and rev and estado = 'borrador' and borrada_at is null`;
2. delete, update e insert de renglones, todos encadenados a la guardia;
3. el mensaje `borrador_guardado`;
4. `ops.exigir`.

Las llaves de renglón son diferidas: los duplicados truenan al COMMIT (§4.6).

**(c) Confirmar: aparta todo o nada.** `SQL_CONFIRMAR`, tal cual. `%(plan)s` = `[{"id": <linea_id>, "almacen": "<codigo>"}]` para **todos** los renglones.

```sql
with o as (
  update ops.ov_ordenes v
     set estado = 'confirmada', confirmada_at = now(), confirmada_por = %(q)s, rev = v.rev + 1
   where v.id = %(id)s and v.rev = %(rev)s and v.estado = 'borrador' and v.borrada_at is null
     and (v.tipo <> 'full' or v.envio_ref is not null)
  returning v.id
), plan as materialized (
  select (e->>'id')::bigint as linea_id, e->>'almacen' as almacen
    from jsonb_array_elements(%(plan)s::jsonb) e
), alm as (
  select a.codigo from ops.almacenes a
   where a.codigo in (select almacen from plan) and a.fuente = 'kubera' and a.admite_ov
     for share
), x as materialized (
  select sa.sku, sa.almacen, li.cantidad as n, li.id as linea_id
    from o
    join ops.ov_lineas li on li.orden_id = o.id
    join plan on plan.linea_id = li.id
    join alm on alm.codigo = plan.almacen
    join ops.stock_almacen sa on sa.sku = li.sku and sa.almacen = plan.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update ops.stock_almacen sa
     set apartado = sa.apartado + x.n
    from x
   where sa.sku = x.sku and sa.almacen = x.almacen and sa.libre >= x.n
  returning sa.sku, sa.almacen
), f as (
  update ops.ov_lineas li set almacen = x.almacen, reservado = li.cantidad
    from x where li.id = x.linea_id
  returning li.id
), msg as (
  insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
  select o.id, 'sistema', 'confirmada', 'Confirmada: ' || (select count(*) from x) || ' renglones apartados', %(q)s, 'panel'
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_esta_en_borrador_o_cambio_rev')
     + ops.exigir((select count(*) from x) > 0
                  and (select count(*) from x) = (select count(*) from ops.ov_lineas where orden_id = %(id)s),
                  'renglon_sin_plan_o_sin_saldo')
     + ops.exigir((select count(*) from s) = (select count(*) from x), 'no_alcanzo')
     + ops.exigir((select count(*) from f) = (select count(*) from x), 'renglones_no_cuadran') as cuadra
```

Al adaptarla:
- pon `confirmada_nombre` y la `via` real;
- `confirmada_at` tiene que ser `now()` (§4.4);
- después del commit, pide una pasada de stock_watch sin forzarla (revisión C13).

**(d) crear_auto.** `SQL_CREAR_AUTO` (verificador, líneas 461-508): la **forma** tal cual (candados, `previa`, folio, resultado), pero **no sus columnas**. Lo esencial:
- `alm` exige `a.fuente = 'kubera' and a.admite_ov and a.surte_ventas`, en `FOR SHARE`;
- `previa` busca **por la clave o por la venta**, entre OV vivas;
- el folio solo sube si no hay `previa` y todo alcanzó;
- la OV nace `'confirmada'`, con `creado_via = 'automatico'`, `cliente` = `mp_canal` y `precio_origen = 'marketplace'`;
- los renglones nacen con `reservado` = `n`;
- el resultado es `(id, resultado ∈ {'creada', 'ya_existia'}, cuadra)`;
- el orden de candados es la excepción de §4.3 (`ov_folio` al final).

**Todo el contenido va en el INSERT: no hay segunda oportunidad.** La OV nace `confirmada`, y fuera de borrador solo cambian estado, entrega, cancelación, borrado, devolución y la marca del canal (`42501 ov_ordenes_inmutable`). Un renglón que aparta solo cambia `reservado` y `entregado*`. El patrón de la prueba no trae ese contenido, y copiado tal cual deja cada OV automática para siempre con `total` 0, precio 0 y sin título. Agrega:
- en `ov_ordenes`: `fecha_venta`, `total`, `comision` y `moneda` de la venta (con `precio_origen = 'marketplace'`), y `descripcion` o `entrega_limite` si los tienes;
- en `ov_lineas`: `titulo`, `imagen` y `precio_unitario` (agrégalos a `%(lineas)s`);
- `guia` y `paqueteria` tampoco cambian después: mientras la decisión 20 siga abierta (§6, 0064-7), lo que no tengas al crear no se puede anotar en la OV.

Conserva `creado_via = 'automatico'`, `creado_por = 'automatico'` y la `via` `automatico` en los mensajes.

**Antes de llamarla, agrupa `%(lineas)s` por SKU.** Con un SKU repetido, el `UPDATE … from x` aparta una sola vez, y la comprobación final da `no_alcanzo` engañoso.

El manejo del `23505` (revisión, hallazgo 3), como patrón:

```python
try:
    fila = sdb.execute_returning(SQL_CREAR_AUTO, p)
except psycopg2.errors.UniqueViolation as e:
    if e.diag.constraint_name not in ("ov_ordenes_clave_uq", "ov_ordenes_mp_uq"):
        raise
    fila = sdb.execute_returning(SQL_CREAR_AUTO, p)   # foto nueva: previa ve la OV y devuelve ya_existia
```

El contrato de esa revisión:
- `ya_existia` es éxito;
- `KB001` se responde con su motivo y sin alerta, salvo los de invariante (§4.6);
- un `no_alcanzo` vuelve al planeador **solo después de releer la OV viva por `mp_*`**;
- a Slack van solo los SQLSTATE inesperados.

**(e) Entregar.** `SQL_ENTREGAR` (líneas 372-429). `%(lineas)s` = `[{"id": <linea_id>, "n": <piezas que salieron>}]`.
- **Cómo funciona:**
  - cada renglón se entrega **una vez**: `entregado = n`, con `0 ≤ n ≤ cantidad`, y lo que no salió se suelta (`apartado − cantidad`);
  - el movimiento `salida_ov` solo se escribe si `n > 0`;
  - la OV pasa a `entregada` cuando no queda ningún renglón sin `entregado_at`. Si no, sigue `confirmada`, y la regla `ov_coherente` lo vigila.
- **Al adaptarla:**
  - el evento: la prueba escribe siempre `entregada_parcial`; usa `entregada` cuando quedan 0;
  - `ref` = folio;
  - la `via` real.

**(f) Cancelar.** `SQL_CANCELAR`, tal cual. Funciona desde `borrador` o `confirmada` y suelta exactamente lo reservado.

```sql
with o as (
  update ops.ov_ordenes v
     set estado = 'cancelada', cancelada_at = now(), cancelada_por = %(q)s, cancelada_origen = %(origen)s,
         cancelada_motivo = %(motivo)s, rev = v.rev + 1
   where v.id = %(id)s and v.rev = %(rev)s and v.estado in ('borrador', 'confirmada') and v.borrada_at is null
  returning v.id
), x as materialized (
  select sa.sku, sa.almacen, li.reservado as n, li.id as linea_id
    from o
    join ops.ov_lineas li on li.orden_id = o.id and li.reservado > 0 and li.entregado_at is null
    join ops.stock_almacen sa on sa.sku = li.sku and sa.almacen = li.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update ops.stock_almacen sa set apartado = sa.apartado - x.n
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku
), r as (
  update ops.ov_lineas li set reservado = 0 from x where li.id = x.linea_id returning li.id
), msg as (
  insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
  select o.id, 'sistema', 'cancelada', 'Cancelada: ' || %(motivo)s, %(q)s, 'panel' from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_cancelable_o_cambio_rev')
     + ops.exigir((select count(*) from s) = (select count(*) from x)
                  and (select count(*) from r) = (select count(*) from x), 'cancelar_no_cuadra') as cuadra
```

- **Si algún renglón ya salió** (`entregado > 0`), esta sentencia truena al COMMIT con `23514 ov_coherente`. Esa cancelación es `entregada_cancelada`:
  - hay que poner `entregada_*` y `cancelada_*`;
  - soltar el apartado de lo que no salió;
  - y probablemente marcar `devolucion_estado = 'pendiente'`.
  - El verificador no trae esa sentencia: escríbela con la misma forma.
- **La cancelación del canal** usa CAS por estado y `cancelada_origen = 'marketplace'` (§4.2). Qué hacer según el estado de la OV:

  | La OV está | El paquete | Patrón |
  |---|---|---|
  | `confirmada` | No ha salido | Cancelar (f), con CAS por estado en vez de `rev` |
  | `confirmada` | El canal dice que ya va en camino | La marca y el «¿salió?» (g) |
  | `entregada` | Ya salió | A `entregada_cancelada` con devolución pendiente (g, al final) |
  | `cancelada`, `entregada_cancelada` o borrada | — | Nada: ya está. Regístralo sin alerta |

  Las cancelaciones de TikTok y Temu llegan por sondeo y por webhook, **y se repiten**: cada patrón tiene que aguantar ver la misma cancelación dos veces.

**(g) «¿Salió?»: el canal canceló con el paquete ya enviado.** Adaptado de la prueba `salio_canal_cancelo_con_paquete_enviado`: la prueba fija `'IN_TRANSIT'`, y aquí el estado del canal va como parámetro. Paso 1, la marca (CAS por estado; no suelta el apartado):

```sql
with o as (update ops.ov_ordenes set canal_cancelo_at = now(), canal_cancelo_ref = %(ref_canal)s, rev = rev + 1
            where id = %(id)s and estado = 'confirmada' and canal_cancelo_at is null and borrada_at is null
            returning id),
m as (insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
      select id, 'sistema', 'canal_cancelo', 'El canal canceló con el paquete en tránsito: ¿salió?', 'automatico', 'automatico' from o
      returning id)
select ops.exigir((select count(*) from o) = 1, 'canal_cancelo_no_aplica') as cuadra
```

- **Por qué los dos filtros de más** (la prueba no los trae): sin `canal_cancelo_at is null`, ver otra vez la misma cancelación reescribe la marca con otro `now()` y da `42501 ov_ordenes_inmutable` («la cancelación del canal ya está anotada»); sin `borrada_at is null`, una OV borrada da el mismo `42501`. Los dos se leerían como bug (§4.6) en cada sondeo repetido.
- **Con 0 filas** llega `KB001 canal_cancelo_no_aplica`: relee. Si ya tiene la marca, devuelve `ya_marcada` como éxito; si está `entregada`, usa el patrón de abajo.
- `canal_cancelo_ref` es el estado del canal: `IN_TRANSIT`, `AWAITING_COLLECTION`, `4` o `5` de Temu, o `shipped`.
- La marca se pone una vez. Con ella, la OV ya **no** puede pasar a `entregada` (`ov_ordenes_canal_cancelo_chk`).
- **Paso 2, cuando Bodega contesta:**
  - **sí salió:** la sentencia de la misma prueba lleva a `entregada_cancelada`, con `entregada_*`, `cancelada_*` y `devolucion_estado = 'pendiente'`; el saldo baja `fisico` y `apartado`; la `salida_ov` lleva la clave de entregar; y el renglón queda `reservado = 0` con `entregado`;
  - **no salió:** cancelar normal, que suelta el apartado.

**El canal cancela una OV ya `entregada`.** Plan v3 §5: «si la pieza ya salió, la OV queda en `entregada_cancelada`, con devolución pendiente». El verificador no trae la sentencia; esta sale de las restricciones (`entregada → entregada_cancelada` es válida, `ov_ordenes_canc_m_chk` pide motivo ≥ 5 y no hay renglones que tocar):

```sql
with o as (
  update ops.ov_ordenes v
     set estado = 'entregada_cancelada', cancelada_at = now(), cancelada_por = 'automatico',
         cancelada_origen = 'marketplace', cancelada_motivo = %(motivo)s,
         devolucion_estado = coalesce(v.devolucion_estado, 'pendiente'), rev = v.rev + 1
   where v.id = %(id)s and v.estado = 'entregada' and v.borrada_at is null
  returning v.id
), m as (
  insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
  select o.id, 'sistema', 'devolucion_esperada', 'El canal canceló una venta ya entregada: se espera la devolución',
         'automatico', 'automatico' from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'canal_cancelo_entregada_no_aplica') as cuadra
```

- `coalesce`: si la OV ya tenía `devolucion_estado` (por ejemplo `recibida`), no lo regreses a `pendiente`: da `23514 ov_ordenes_transicion_chk`.
- Con 0 filas, relee: si ya es `entregada_cancelada`, devuelve `ya_cancelada` como éxito.

**(h) «¿Salió?» tardío: la OV ya estaba `cancelada`** (decisión 15). Usa `SQL_SALIO_TARDE`:
- la OV pasa de `cancelada` a `entregada_cancelada`, con `entregada_*` y `devolucion_estado = 'pendiente'`;
- `fisico` baja `cantidad`;
- `salida_ov` lleva la misma clave de entregar;
- el renglón recibe `entregado`, con el evento `devolucion_esperada`.

Después va la devolución normal. Si la OV fue un borrador cancelado, da `23514 ov_ordenes_conf_chk`.

**Ojo con el `23505`.** Al pasar a `entregada_cancelada`, la OV vuelve a entrar en `ov_ordenes_clave_uq` y `ov_ordenes_mp_uq`. Si una persona o el sistema la había cancelado, la venta regresó al planeador y crear_auto pudo crear **otra** OV viva con la misma clave `mp:…`: entonces esta sentencia truena con `23505`. **No es `ya_existia`** (§4.6): la salida no se escribió y TEX3 tiene piezas de más en el libro. Avisa y resuélvelo a mano.

**(i) Formatos de Bodega.**
- **`SQL_FORMATO_CARGA`:** encabezado, renglones y el evento `cargado` en una sentencia; **no toca el saldo**.
  - Antes, valida en Python (plan v3 §3, paso 2). En corto: SKU vacío, desconocido en `core.products` y en Odoo, o padre `variable` → renglón con problema, y el desconocido **bloquea** la confirmación; cantidad 0, negativa, decimal o texto → problema; archivado en Odoo → se acepta con aviso y se mapea al código activo; cantidad atípica (más de 3 veces lo de TEX2 en Odoo, o más de 5,000) → aviso; mismo SKU y cantidad en otro formato en 7 días → aviso; SKU de riesgo D (`DEPO-0001-ROS`, `DEPO-0001-AZL`, `JUEG-0012-MUL`) → aviso de confirmar con conteo físico.
  - Suma los renglones de igual SKU y ubicación.
  - Guarda `sku_archivo` tal como venía, `sku` mapeado al código activo, y `cantidad_archivo` = `cantidad`.
  - **Lee Odoo antes y guarda en cada renglón** `odoo_tex2_al_cargar` (`qty_available` de TEX2 con `context={"warehouse": 150}`) y `odoo_total_al_cargar` (`free_qty` total, registro). El patrón de la prueba solo guarda el primero: agrega el segundo. **Sin `odoo_tex2_al_cargar`, la señal `tex2_bajo` de la puerta no se puede calcular.**
- **`SQL_FORMATO_CONFIRMAR`:**
  - CAS con `rev` y `estado = 'por_confirmar'`;
  - `confirmo_bodega` no vacío;
  - `odoo_tex2_al_confirmar` sale de `jsonb_each_text(%(tex2)s::jsonb)` y se cruza por `t.k::citext`, porque Odoo da float (H06).
- **La puerta: qué se lee en Odoo** (plan v3 §3; la base solo guarda los nombres en `stock_formato_linea_via_chk`). Es lo único que impide contar dos veces la misma pieza, en TEX2 de Odoo y en TEX3 de kubera: **no inventes umbrales ni la abras antes de tiempo.** Python lee Odoo en solo lectura, por SKU, y basta una señal:

  | `via` | Se abre cuando |
  |---|---|
  | `tex2_bajo` | `qty_available` de TEX2 (`context={"warehouse": 150}`) ≤ `max(0, odoo_tex2_al_cargar − cantidad)` |
  | `tex2_cero` | `qty_available` de TEX2 ≤ 0. Cubre a Odoo con menos de lo que movió Bodega, y a Odoo que bajó antes de la carga |
  | `movimiento` | Un `stock.move.line` con `state = 'done'` que saca el SKU de TEX2 desde 7 días antes de la carga, hacia una ubicación que **no** es de cliente. Bodega da la referencia (por ejemplo `TEX2/INT/00123`) o Python la busca. Va en `ref` (→ `salida_odoo_ref`, obligatoria con esta vía) |

  - **Varios renglones del mismo SKU** (dos ubicaciones): `SQL_PUERTA` los abre todos juntos, así que el umbral de `tex2_bajo` usa la Σ `cantidad` de los renglones que esperan (deducción de la sentencia, no del plan).
  - **La puerta mira que la salida ocurrió, no cuánto salió.** Lo que entra al libro es siempre la cantidad del formato. `tex2` de `%(listos)s` es el `qty_available` de TEX2 al abrir y queda en `odoo_tex2_al_abrir`, como registro.
  - **Si Odoo falla, no se abre nada:** los SKUs siguen esperando y se venden por Odoo. El error esconde, nunca cuenta dos veces.
  - **Límite de `tex2_bajo`:** una venta de TEX2 surtida en el intervalo también baja `qty_available` y puede abrir la puerta antes de que Bodega registre toda la salida. El doble conteo queda acotado a esas piezas y a ese rato.
- **`SQL_PUERTA`:**
  - `%(listos)s` = `[{"sku", "via", "ref", "tex2"}]`, **ya leído de Odoo en solo lectura** con la tabla de arriba;
  - bloquea los renglones en orden (`lk`), hace el upsert del saldo con `order by e.sku` y escribe la `entrada` con `fmt:<id>:<sku>` y `ref` = folio;
  - sin SKUs listos, abre 0 y no es error. Una segunda pasada tampoco abre nada;
  - corre al confirmar y al inicio de cada pasada de stock_watch (plan v3 §4.1). Un SKU que espera no está en `stock_almacen` y se sigue vendiendo por Odoo, una sola vez.
- **`SQL_FORMATO_DIVIDIR`:** el padre `por_confirmar` va `for update`; el hijo nace con el mismo `archivo_hash` y la misma bodega, y `dividido_de` = padre; los renglones se mueven con delete más insert; los eventos son `dividido` y `cargado`.
- **Bajar la cantidad de un renglón que espera.** Adaptado de la prueba `formato_puerta_y_entrada_al_libro`: la prueba filtra por `formato_id`, `sku` y `rev = 1`; aquí se filtra por `id` y la `rev` leída:

  ```sql
  update ops.stock_formato_linea set cantidad = %(n)s, nota = %(nota)s, rev = rev + 1
   where id = %(linea_id)s and rev = %(rev)s and salida_odoo_at is null returning id
  ```

  Si devuelve 0 filas, recarga. En producción va en un `WITH` con el evento `cantidad_bajada` (`nota` ≥ 5) y `ops.exigir`.
- **Descartar:**
  - `estado = 'descartado'`, con `descartado_at`, `descartado_por` y un `descartado_motivo` de 5 caracteres o más;
  - evento `descartado`;
  - es terminal.
- **Reemplazar:** en una sentencia, se descarta el anterior con el motivo «reemplazado por FMT-…» y se inserta el nuevo con `reemplaza_a`, con el evento `reemplazado`. El verificador no trae esta sentencia.

**(j) Devoluciones.**
- **`SQL_DEV_RECIBIR`** (venta):
  - fila en `devoluciones`;
  - upsert de `(sku, 'REVISION')`;
  - movimiento `devolucion`, con `dev:<id>:recibe` y `ov_linea_id`;
  - la OV pasa a `devolucion_estado = 'recibida'`.
  - Al adaptarla: `empaque` como parámetro (la prueba fija `'cerrado'`); el evento `devolucion_recibida`; y `canal`, `cuenta` y `external_order_id` NULL o idénticos al `mp_*` de la OV.
  - **Dónde vive:** escribe `ov_ordenes`, así que va en `ordenes_venta` como función (§5.1, punto 3), y la pantalla de devoluciones la llama. No la partas en dos módulos: serían dos sentencias. Lo mismo vale para vendible y merma de una venta si escriben su evento en `ov_mensajes`.
- **`SQL_DEV_VENDIBLE`** (dictamen vendible):
  - dictamen y `resuelta_at`;
  - traspaso con `dev:<id>:sale` y `dev:<id>:entra`, con los candados en orden.
  - **Al adaptarla:** la prueba fija el destino en `'ENSAYO'`, y el destino es `ov_lineas.almacen` (revisión §3.11). El `ref` usa `'DEV-' || d.id`, que **no** es el folio generado (`DEV-00001`): usa `d.folio`. Escribe el evento `devolucion_aprobada`.
- **Merma:** movimiento `merma` en `REVISION` con `dev:<id>:merma` y nota ≥ 5, más `dictamen = 'merma'` y `resuelta_at`.
- **Partir el paquete por dictamen.** Sale de la prueba `devolucion_llave_de_recepcion_y_partida`: baja `cantidad` de la original y agrega otra fila con `partida_de`, del mismo paquete, renglón y SKU, **sin** movimiento `recibe`.
- **Retiro de FULL que llega a TEX3.** El verificador no trae la sentencia; la deducción es por las restricciones:
  - fila con `origen = 'retiro_full'`, `canal`, `cuenta`, nota ≥ 5 y sin `ov_linea_id`;
  - movimiento **`entrada`** en `REVISION` con `clave = dev:<id>:recibe`, `ref` = folio `DEV-…` y sin `ov_linea_id`.

**(k) Conteo.** `SQL_CONTEO`, tal cual. Se aplica contra «lo que vi»; `conteo:S1` es la sesión fija de la prueba.

```sql
with x as (
  select sa.sku, sa.almacen, sa.fisico from ops.stock_almacen sa
   where sa.sku = %(sku)s and sa.almacen = %(alm)s and sa.fisico = %(visto)s
     for update
), s as (
  update ops.stock_almacen sa set fisico = %(contado)s
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku, sa.almacen, sa.fisico, x.fisico as antes
), m as (
  insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave, nota, quien, via)
  select s.sku, s.almacen, s.fisico - s.antes, s.fisico, 'ajuste_conteo',
         'conteo:S1:' || s.sku || ':' || s.almacen, 'Conteo de prueba', %(q)s, 'prueba'
    from s where s.fisico <> s.antes
  returning id
)
select ops.exigir((select count(*) from s) = 1, 'recontar') as cuadra
```

- Puede dejar `libre` < 0. Es legítimo: la vista lo avisa.
- Al adaptarla: la sesión es un uuid por sesión de conteo, la nota es real, y la `via` es la real.

**Clasificador de errores.** Es un patrón de esta guía; no existe en el repo. Recibe la operación, porque el mismo `23505` es éxito en un alta y conflicto en una salida tardía (§4.6):

```python
YA_EXISTIA = {"devoluciones_recepcion_uq", "devoluciones_retiro_uq", "devoluciones_clave_uq",
              "stock_mov_clave_uq", "stock_mov_salida_ov_uq", "stock_formato_hash_uq", "ov_archivos_vivo_uq"}
YA_EXISTIA_ALTA = {"ov_ordenes_clave_uq", "ov_ordenes_mp_uq"}      # solo en crear_borrador y crear_auto
ALTAS = {"crear_borrador", "crear_auto"}
KB001_QUE_AVISAN = {"renglones_no_cuadran", "cancelar_no_cuadra", "devolucion_no_cuadra", "regla_de_negocio"}

def clasificar(e: psycopg2.Error, operacion: str) -> tuple[str, str | None]:
    nombre = e.diag.constraint_name
    if e.pgcode == "KB001":
        motivo = e.diag.message_primary or "regla_de_negocio"
        if motivo in KB001_QUE_AVISAN or motivo.endswith("_escrituras_no_cuadran"):
            return "inesperado", motivo                  # invariante: registrar y avisar
        return "negocio", motivo                         # 409 con el motivo, sin alerta
    if e.pgcode == "23505" and (nombre in YA_EXISTIA or (nombre in YA_EXISTIA_ALTA and operacion in ALTAS)):
        return "ya_existia", nombre                      # releer y devolver lo que existe
    if e.pgcode in ("55P03", "57014"):
        return "ocupado", None                           # un reintento como máximo
    if e.pgcode == "40P01":
        return "deadlock", None                          # un reintento y aviso
    return "inesperado", nombre                          # registrar y avisar (23505 de clave fuera de un alta, 23502…)
```

`stock_mov_clave_uq` es «ya aplicado» solo si al releer es el mismo hecho (§4.6); si difiere, avisa.

---

## 5. Lo que NO hace la base (le toca al código o queda pendiente)

### 5.1 Prioridad 0: sin esto no hay merge ni fase A

1. **Acta de producción de la 0064 y la 0065, ANTES del merge** (SEG-01): tres firmas, y el ALTER de `stock_watch_photo` justo después de una pasada.
2. **Tolerancia con `to_regclass`** en todo lector que no depende de una bandera (§4.8).
3. **`services/ordenes_venta.py`, con todas sus sentencias de negocio** (el plan v3 §10.1, paso 3, se lo asigna a Brandon):
   - `crear_borrador`, `guardar`, `confirmar`, `entregar` (parcial y total), `cancelar` (manual, sistema y marketplace) y `cancelar` con entrega parcial (a `entregada_cancelada`);
   - `borrar`: lógico, de admin. Si la OV aparta, hay que soltar en la misma sentencia; `ov_coherente` exige que una borrada no aparte;
   - `crear_auto`;
   - la cancelación del canal en sus tres casos (§4.9 f y g): confirmada sin salir, la marca `canal_cancelo` con la respuesta del «¿salió?», y la OV ya `entregada` (→ `entregada_cancelada` con devolución pendiente);
   - el «¿salió?» tardío;
   - **las sentencias de devolución que también escriben `ov_*`:** recibir la devolución de una venta (`SQL_DEV_RECIBIR`, que cambia `devolucion_estado`), los dictámenes de una venta que dejan su evento en `ov_mensajes` (`devolucion_aprobada`, `devolucion_merma`) y las demás marcas de `devolucion_estado`. Son funciones que llaman la pantalla y los canales;
   - el mensaje de usuario (`tipo='usuario'`, sin `evento`).

   **Por qué esas devoluciones viven aquí y no en `inventario_libro`:** recibir escribe la fila de `devoluciones`, el saldo, el libro y `ov_ordenes.devolucion_estado` en **una** sentencia (comment de `ops.devoluciones`), y la 0064 dice «QUIÉN ESCRIBE: solo `services/ordenes_venta.py` (… marcas de devolución …). Las devoluciones y los canales lo LLAMAN; no escriben `ov_*`». Partirla entre dos módulos rompería §4.1. Es la lectura que cumple los dos encabezados, aunque el plan v3 (§10.1, paso 4) le asigna las devoluciones a `inventario_libro`: **confírmalo con el coordinador antes de escribirlas**, y si decide otra cosa (por ejemplo, una excepción documentada a «solo `ordenes_venta` escribe `ov_*`»), que quede escrito aquí.
4. **`services/inventario_libro.py`** (plan v3 §10.1, paso 4, asignado a Sistemas):
   - formatos: carga, editar o quitar renglón, dividir, reemplazar, descartar, confirmar y bajar cantidad;
   - **la puerta**, con su lectura de Odoo en solo lectura (las tres señales y sus umbrales en §4.9 i);
   - conteo, merma, corrección y traspaso (`tras:`);
   - lo de devoluciones que no toca `ov_*`: partir el paquete, y recibir y dictaminar un retiro de FULL. Cuándo pasa `devolucion_estado` a `cerrada` es un hueco (§5.3, punto 23).
5. **El aviso cada 15 min, que lee LAS DOS vistas:** `ops.stock_apartado_descuadre_v` y `ops.devoluciones_vs_canal_v`. Tienen las mismas columnas. Avisa con 1 fila o más, y **pon la consulta de la segunda aparte**: depende de `channel.return_items`, y si truena no debe tapar la primera.
6. **Las cuatro banderas, apagadas por omisión** (`ordenes_venta`, `stock_watch_lee_kubera`, `ov_generacion_auto` e `inventario_libro`), como filas de `ops.automatizacion_flags` con su variable de respaldo en `false`. La tabla completa está en §4.8:
   - **las filas NO se siembran** (decisión 13 de la 0064). Las crea el acta que las enciende, con su motivo;
   - si una bandera no se puede leer, se toma como apagada.

### 5.2 Prioridad 1: requisitos de la fase B (encender TEX3)

7. **stock_watch** (plan v3 §4):
   - la puerta al inicio de la pasada;
   - la sentencia de kubera (`kub`, `tras`, `entro`, `cub` y `pend`);
   - la suma por SKU `max(0, max(0, Odoo) + libre_TEX3 − pendientes)`;
   - el freno por fuente (`explicado()`) con la dosis;
   - el aviso a Slack;
   - `listar_catalogo` que lanza si una página falla;
   - los cuatro candados `hay_stock_kubera` (`sync_woo`, `sync_odoo_woo_seguro`, `odoo_watch._empujar_a_woo`, `corregir_stock_woo_full`);
   - `fanout_vivo`;
   - **la regla de `stock_kubera`:** NULL solo antes del encendido, y después `coalesce(kub, 0)`.
8. **El planeador y `odoo_ventas`** (plan v3 §5):
   - leen `ops.almacenes` por `preferencia`;
   - el faltante siempre va a la primera bodega de Odoo con `surte_ventas`;
   - las acciones nuevas `ov_kubera` y `cancelada_ov`;
   - el reintento no replanea las piezas de la OV;
   - `temu_guias_compra` lee `temu_warehouse_id` por `codigo`;
   - las copias de la regla de pendientes.
   - **Con `ov_generacion_auto` apagado, el planeador excluye TEX3** (revisión SEG-05).
9. **Regularizar `ops.odoo_sale_orders` en main.**
   - En el sandbox (medido el 6-oct) y en producción (según la revisión) la tabla tiene `cuenta` y PK `(canal, cuenta, external_order_id)`. Sus renglones tienen `cuenta`, `stock_libre` y `medido_at`, más el trigger `trg_touch_odoo_sale_orders`.
   - **Nada de eso está en las migraciones de main:** las 0034–0036 no están en main.
   - La sentencia de kubera de stock_watch depende de `cuenta`: un sandbox armado solo desde main no la tendría.
   - Va con su catálogo de `accion` (`NOT VALID`, inventario y `VALIDATE`) y las transiciones con CAS.
10. **La tabla del vigilante de D4** (ventas DROP de ML de SKUs que solo están en TEX3), y el vigilante mismo.
    - No tiene nombre, columnas, llave ni migración.
    - **Es requisito de la fase B:** sin ella, cada venta DROP de ML de un SKU de TEX3 resucita en Woo.
11. **D3:** el `temu_warehouse_id` de TEX3 antes de encenderla. Hoy no hay CHECK que lo exija (decisión 8).
12. **El UPDATE de la fase B lleva `admite_ov = true`**, o truena con `almacenes_surte_ov_chk`. Hay que corregirlo en el plan v3 §10.1, paso 12.
13. **Los buckets de Storage** (decisión 8 de la 0065; encabezado de la 0064):
    - **`ordenes-venta`:** privado, 15 MB, solo `application/pdf`, sin políticas. **Mientras no exista, `ov_archivos` no tiene escritor.**
    - **`inventario-kubera`:** privado, 10 MB, xlsx, csv y fotos jpeg o webp, rutas por hash. La retención está por decidir.
    - El sandbox está en el plan Free: 50 MB por archivo y 1 GB de Storage.

### 5.3 Prioridad 2: deuda conocida y límites declarados

14. **SEG-12.**
    - `confirmo_bodega` es texto libre (D9): la base **no** garantiza «quien carga no confirma».
    - Falta `confirmo_bodega_id` como FK a `core.usuarios`, más permisos puntuales (`core.usuario_permisos`, en la propuesta de la revisión).
    - Mientras tanto, el control es RBAC en las rutas.
15. **H08.**
    - `mp_*` es texto libre: la base no comprueba que la venta exista en `channel.orders`.
    - Valídalo en Python en las capturas manuales de ML, TikTok y Temu.
    - El trigger quedó fuera (decisión 14): antes hay que resolver qué pasa cuando la venta llega a `channel.orders` después que la OV.
16. **H05/D8.** El formato se deduplica por los **bytes** del archivo. Excel puede volver a guardar el mismo lote con otro hash. Pendiente: dedupe por lote o por contenido, con una salida `repite_de`.
17. **H17.** No hay vista de ubicaciones. `stock_almacen.ubicacion` es solo la del último formato o conteo.
18. **SEG-08.**
    - Después de la fase B, el libro es el **único** registro de TEX3, y no hay PITR: el RPO es de un día.
    - Falta la exportación nocturna del libro y de `stock_almacen` a un bucket privado.
    - La reversa con datos reales es operativa (banderas), nunca DROP.
19. **SEG-10.** stock_watch no tiene un latido durable. Falta una tabla de pasadas y un aviso de frescura fuera del backend.
20. **El límite de «la misma caja con ids distintos»** (decisión 6 de la 0065).
    - Si dos turnos capturan la misma caja con ids distintos (guía en uno, paquete en otro), `devoluciones_recepcion_uq` no la ve.
    - Lo frenan el tope de lo entregado (`stock_devolucion_de_mas`) y el cruce diario.
21. **Los grants.**
    - Solo las cinco tablas de solo agregar quedaron en SELECT e INSERT.
    - Las demás conservan `grant all` a service_role, y las vistas su `grant select` sin `revoke all` previo.
    - Fue una decisión, pero la defensa contra PostgREST es parcial.
22. **Lo que la base no comprueba:**
    - que `total` sea la Σ de los renglones;
    - que `imagen` sea una URL http(s);
    - que un mensaje `sistema` lleve `evento`;
    - la ausencia de datos personales en `cuerpo`, `nota` o `cliente` (salvo en las automáticas);
    - que `ov_archivos` no guarde etiquetas con la dirección del comprador (D5: `tipo` no lo impide);
    - quién puede borrar («solo admin»);
    - el tope blando de las KAM (plan v3 §6).
23. **Huecos sin decisión en el SQL. Pregúntalos, no los inventes:**
    - qué movimiento lleva el dictamen `proveedor`. Para resolver hay que sacar la pieza de REVISION, o la vigía da `revision_descuadrada`;
    - la bodega destino de un `vendible` que viene de un `retiro_full` (no tiene renglón de OV);
    - cuándo pasa `devolucion_estado` a `cerrada`;
    - **cómo se resuelve una devolución en `reparar`.** `reparar` no resuelve (`devoluciones_resuelta_chk`), y la base sí deja cambiar el dictamen antes de resolver. Pero `SQL_DEV_VENDIBLE` solo toma `dictamen is null`, así que con el patrón una fila en `reparar` (por ejemplo, la que nace al partir el paquete en la prueba) se queda para siempre en REVISION. Pregunta si vendible y merma aceptan `dictamen is null or dictamen = 'reparar'`, con candado de `rev`;
    - qué significa el evento `descartada`;
    - a qué módulo le tocan las sentencias de devolución que escriben `ov_*` (§5.1, punto 3: esta guía propone `ordenes_venta`; el plan v3 dice `inventario_libro`).
24. **Documentos por corregir:**
    - el plan v3 §10.1, paso 12: la fase B lleva `admite_ov = true`;
    - DEVOLUCIONES §4b: anotar la reapertura de una `cerrada` (decisión 16).
25. **Del plan y la revisión, para el código:**
    - pedir una pasada de stock_watch al confirmar o cancelar (C13);
    - etiquetas bajo pedido a la API, sin guardarlas (D5);
    - `guias_del_dia` suma los renglones de las OV;
    - Crear FULL y FULLFILMENT con OV `full` (plan v3 §6).

---

## 6. Decisiones tomadas por omisión que Eduardo puede cambiar

Cada una se cambia con un UPDATE con acta o con una migración chica.

| # | Decisión (encabezado) | Qué cambia si Eduardo la cambia |
|---|---|---|
| 0064-1 | D1: el código es `TEX3`, fijo; el nombre «TEXCO III» es provisional | El nombre: un UPDATE sin acta. El código **no se puede cambiar** (`almacenes_codigo_fuente_fijos`) y la fila no se borra: otro código es otra bodega (alta con acta) |
| 0064-2 | TEX3 nace apagada (`surte_ventas`, `cuenta_para_woo` y `admite_ov` en false) | Encender es un UPDATE con acta: `set_config('app.usuario', …, true)` en el mismo envío y un motivo nuevo de 10 caracteres o más. La fase B pone las tres banderas juntas (`almacenes_surte_ov_chk`, `almacenes_woo_chk`). `admite_ov` solo se puede encender antes, para OV manuales |
| 0064-3 | D13: TEX2 sigue surtiendo hasta que Odoo la muestre vacía | Apagarla es un UPDATE con acta |
| 0064-4 | D12: REVISION se siembra (kubera, sin `admite_ov` ni Woo) | Nada de esquema |
| 0064-5 | Filas de Odoo con `cuenta_para_woo = true`, informativo | Nada de esquema |
| 0064-6 | D11: los triggers van donde nace cada tabla | Quitar uno es una migración (`drop trigger`). Deshabilitar un trigger es su propia acta |
| 0064-7 | D10: el número de envío se exige al confirmar una OV full (`ov_ordenes_full_env_chk`). La decisión 20 (fijarlo después) sigue abierta | Si se aprueba la 20: una migración que agrega `guia`, `paqueteria` (y `envio_ref`, si se quiere) a la lista `permitidas` de `ops.tg_ov_ordenes_guarda`. Si además se quiere confirmar sin envío: quitar `ov_ordenes_full_env_chk` y la condición `envio_ref` de las sentencias |
| 0064-8 | D3: sin CHECK de `temu_warehouse_id` para TEX3 | Si Temu vende desde TEX3: `check (not surte_ventas or temu_warehouse_id is not null)` (revisión §6.2) |
| 0064-9 | `ov_archivos.tipo` ∈ {envio_full, comprobante, factura} | Un tipo nuevo es una migración que amplía `ov_archivos_tipo_chk` |
| 0064-10 | `ov_mensajes.evento` es un catálogo cerrado | Un evento nuevo es una migración que amplía `ov_mensajes_evento_chk` |
| 0064-11 | En las automáticas, `cliente` = `mp_canal` (SEG-06) | Cambiar `ov_ordenes_auto_cliente_chk`. Nunca el comprador |
| 0064-12 | Solo crear_auto hace nacer una OV confirmada. Si salió alguna pieza, la OV es `entregada_cancelada` | Cambiar `ops.tg_ov_ordenes_guarda` (cómo nace) y `ops.verificar_ov` |
| 0064-13 | Las banderas `ov_generacion_auto` e `inventario_libro` no se siembran | Nada de esquema: las filas las crea el acta |
| 0064-14 | Sin trigger «la venta existe en `channel.orders`» (H08) | Un trigger nuevo en `ov_ordenes`, solo para capturas manuales de ML, TikTok y Temu |
| 0064-15 | «¿Salió?» tardío: cancelada → entregada_cancelada, con `salida_ov`, y después devolución a REVISION | La alternativa («cancelada es terminal, la caja vuelve al anaquel sin movimiento») quita esa transición de `ops.tg_ov_ordenes_guarda` y la excepción de salida tardía de `ops.tg_ov_lineas_guarda` |
| 0064-16 | `devolucion_estado`: no vuelve a NULL ni de recibida a pendiente, pero una `cerrada` se reabre | La regla estricta («solo hacia adelante») cambia `ops.tg_ov_ordenes_guarda`, y la segunda devolución tras cerrar se queda sin camino |
| 0065-1 | D14: la PUERTA. Sus columnas son nulables y el comportamiento es código | Aceptar el doble conteo no está especificado en el esquema (revisión §6.2): requiere su propio diseño |
| 0065-2 | D9: `confirmo_bodega` es texto no vacío | SEG-12: `confirmo_bodega_id` como FK a `core.usuarios` más permisos. Cambia `stock_formato_conf_chk` |
| 0065-3 | D12: REVISION es de kubera; sin origen `sin_venta` (D-R15) | Agregarlo amplía `devoluciones_origen_chk` y obliga a revisar `devoluciones_venta_chk`, `devoluciones_nota_chk` y las llaves de recepción |
| 0065-4 | D6: saldo por SKU × bodega; `ubicacion` es informativa | El saldo por ubicación es una tabla hija nueva, sin tocar esta |
| 0065-5 | La cantidad del formato es > 0 | Admitir 0 relaja `stock_formato_linea_cantidad_chk`, y la puerta tendría que excluir esos renglones (`stock_mov_delta_chk` no admite `delta = 0`) |
| 0065-6 | La idempotencia de las devoluciones sale del hecho (`devoluciones_recepcion_uq` y `devoluciones_retiro_uq`). Límite: la misma caja con ids distintos | Una llave distinta es otra migración de índices |
| 0065-7 | `odoo_*` son `numeric(14,3)`; la cantidad de Bodega es entera | — |
| 0065-8 | Fuera de esta migración: el bucket `inventario-kubera`, la tabla de D4 y los cambios a `odoo_sale_orders` | Cada uno es su migración o configuración (§5) |
| 0065-9 | D8: dedupe por `archivo_hash` | H05: `lote_ref` o `contenido_hash` únicos, con `repite_de`. Columnas e índice nuevos |
| 0065-10 | TEX3 no surte; crear_auto vuelve a comprobar `surte_ventas` dentro del candado | Es código (C11) |
| 0065-11 | «Dividir» con `dividido_de`. Límite: con el padre descartado y un hijo vivo, el mismo archivo puede volver a entrar como padre nuevo | Cerrarlo cambia `stock_formato_hash_uq` o la guarda |
| 0065-12 | Bajar la cantidad lleva candado optimista con `stock_formato_linea.rev` | — |

---

## 7. Cómo probar

1. **Solo en el sandbox** (`yvootpbz`). **Nunca en producción**, ni para leer, desde el agente.
2. **Con el verificador.** Desde la raíz del repo. El comando de ejemplo supone `env.staging` en `../OMNICANAL/` (la estructura de carpetas de Eduardo); si tu repo vive en otra ruta, pasa la tuya:

   ```bash
   grep '^SUPABASE_DB_URL=' RUTA/env.staging | cut -d= -f2- \
     | backend/.venv/Scripts/python.exe backend/scripts/verificar_0064_0065.py
   # o, sin stdin:
   backend/.venv/Scripts/python.exe backend/scripts/verificar_0064_0065.py --env RUTA/env.staging
   ```

   - **Qué protege solo:**
     - toma el DSN de stdin, o lee de `env.staging` (`--env RUTA`; por omisión, `<raíz>/../OMNICANAL/env.staging`) **solo** la línea `SUPABASE_DB_URL`;
     - se niega si el DSN no es `yvootpbz` o menciona `tukwcvsi`;
     - usa el puerto 5432, con una conexión propia;
     - nunca imprime el DSN ni marca la sesión como de solo lectura.
   - **Códigos de salida** (la docstring del verificador dice otra cosa; manda el código):
     - **0:** todo pasó;
     - **1:** alguna prueba falló, **o** se negó o no pudo conectar: sin DSN, DSN que no es del sandbox, fallo de conexión, `--en-transaccion` junto con `--concurrencia-con-commit`, o una migración que falla al aplicarse en `--en-transaccion`. Para distinguirlos, lee la línea `ABORTO` o `FALLA`;
     - **2:** solo si una migración trae control de transacción, si la transacción de prueba cambió, o si faltan las migraciones en el modo por omisión.
   - **Modos:**
     - **por omisión:** las pruebas sobre lo ya aplicado, cada una en su transacción con ROLLBACK. **Resultado esperado hoy: 54 OK · 0 FALLA · 2 OMITIDA.** Córrelo después de cualquier cambio;
     - **`--en-transaccion`:** aplica la 0064 y la 0065 dos veces dentro de una transacción, corre todo en SAVEPOINTs y hace ROLLBACK. Es el modo para probar una migración **antes** de aplicarla;
     - **`--concurrencia-con-commit`** (solo en modo por omisión): las dos pruebas de concurrencia. **Son las únicas que hacen COMMIT y dejan historia** en `ENSAYO`. Piden su propio permiso: no las corras sin él.
3. **Prueba tu código igual que el verificador:**
   - en `ENSAYO` (`admite_ov = true`);
   - con SKUs `ZZPRUEBA-*`;
   - cada caso en una transacción que termina en ROLLBACK;
   - antes del ROLLBACK, `set constraints all immediate`. **Sin eso nunca ves los errores diferidos**, porque el COMMIT no llega;
   - para probar crear_auto en `ENSAYO`, enciende `surte_ventas` **y** una `preferencia` libre **dentro** de la transacción de la prueba, con un motivo nuevo de 10 caracteres o más y `set_config('app.usuario', …, true)`, como hace `crear_auto_idempotente` (`set surte_ventas = true, preferencia = 9`). Sin `preferencia` truena con `23514 almacenes_surte_chk`, porque ENSAYO nace con NULL; y tiene que ser libre, porque `almacenes_pref_uq` es único y 1, 2 y 3 ya están tomadas. Deja que el ROLLBACK lo deshaga.

   **El problema: `sdb` hace COMMIT por dentro.** `sdb.get_cursor()` confirma al salir del `with`, y `sdb.execute_returning()` lo usa. Llamar a una función de servicio desde una prueba deja historia permanente. Por eso:
   - **diseña cada función para que se pueda probar sin `sdb`:** su SQL como constante del módulo, y la ejecución en una función que recibe el cursor (la de producción le pasa el de `sdb.get_cursor()`). La prueba abre su propia conexión psycopg2 al 5432 del sandbox, corre la sentencia con ese cursor y termina en `set constraints all immediate` + ROLLBACK, como el verificador;
   - el «ciclo completo en `ENSAYO`» que pide el plan v3 (§10.1, paso 3) se arma así, encadenando las sentencias dentro de la misma transacción de prueba;
   - **un ciclo con COMMIT real** (por ejemplo, a través de las rutas HTTP) deja historia que no se borra (punto 4): solo con permiso por escrito del coordinador, en `ENSAYO`, con SKUs `ZZPRUEBA-*` y sin tocar `ops.almacenes`.
4. **Nunca hagas COMMIT de pruebas** (salvo `--concurrencia-con-commit` o el ciclo del punto 3, cada uno con su permiso). **Lo de solo agregar no se puede limpiar:**
   - `ov_ordenes` no se borra y `ov_folio` solo sube: un folio consumido queda consumido;
   - `ov_mensajes`, `stock_mov`, `stock_formato_evento`, `almacenes_hist` y `migraciones` son de solo agregar;
   - `stock_formato`, `devoluciones` y `ov_archivos` no se borran.
   - Si te equivocas, el único arreglo es otro movimiento (`correccion`) o una cancelación, y la historia se queda.
5. **No hagas COMMIT de cambios a `ops.almacenes` en el sandbox.** La prueba `semilla_almacenes` exige las seis filas con sus banderas exactas y exactamente 6 altas en `almacenes_hist`.
6. **Si levantas el backend contra el sandbox**, `env.staging` trae Woo y MySQL de **producción**. Según el plan v3 §10.1 y la revisión SEG-02:
   - `STOCK_WATCH_SOLO_REGISTRO=true`;
   - el fan-out apagado;
   - las `WC_*` de escritura vacías;
   - `odoo_ventas` en solo registro;
   - la compra de guías de Temu apagada.

---

## 8. Prohibiciones y trampas

1. **El repo es PÚBLICO.**
   - Nada de PII ni secretos en el código, las pruebas, los fixtures, los logs, los commits ni los documentos: ni DSN, tokens o llaves, ni nombres, direcciones o teléfonos de compradores.
   - Datos de prueba: `ZZPRUEBA-*`, cuentas como `CUENTAPRUEBA`.
2. **`env.staging` trae credenciales de producción de MySQL y Woo.** Lee **solo** la línea `SUPABASE_DB_URL`, sin cargar el archivo entero, como `leer_dsn` del verificador.
3. **Nunca `set_session(readonly=True)`** ni ningún estado de sesión en el pooler (§4.7).
4. **El código nuevo nunca escribe en Odoo** (§1.2, regla 4). La puerta y el vigilante de D4 solo leen. Si una sentencia necesita un dato de Odoo, se lee antes y se pasa como parámetro. La sale.order de la parte de TEXCO o TEX2 la sigue creando `odoo_ventas`, como hoy.
5. **Nunca toques producción desde el agente.** Ni aplicar, ni conectar, ni «solo un SELECT». Producción va por acta (§1.2).
6. **Las migraciones van antes que el código** (SEG-01). No fusiones a `main` ni hagas push sin el OK. Railway despliega lo que llegue a `main`.
7. **No edites la 0064 ni la 0065, ni deshabilites triggers** (`alter table … disable trigger` es su propia acta). Un cambio es una migración nueva, coordinada.
8. **No «arregles» un descuadre escribiendo el saldo:**
   - la base lo rechaza al COMMIT (`stock_libro_cuadra`, `stock_apartado_cuadra`);
   - toda corrección es un movimiento con nota (`correccion`, `ajuste_conteo`);
   - nunca UPDATE, DELETE ni TRUNCATE del libro.
9. **No uses las tablas de la versión del 2-oct** (`ov_stock`, `ov_stock_base_v`, `ov_ordenes.almacen`): no existen (§1.4).
10. **`cliente` nunca es el comprador.** Las etiquetas con dirección no se guardan en kubera (D5): se piden a la API al imprimir.
11. **Primero el sistema propio, después el externo:** OV antes que la sale.order que `odoo_ventas` crea para la parte de Odoo, y OV antes que la compra de la guía de Temu. Un fallo a la mitad tiene que **esconder**, nunca duplicar.
12. **Las sentencias del verificador son patrón, no producción:** traen `'prueba'`, `'ENSAYO'`, `'conteo:S1:'` y `'DEV-' || d.id` fijos (§4.9).
13. **Cambiar una variable en Railway reinicia el contenedor.** Por eso las cuatro banderas de negocio viven como filas en `ops.automatizacion_flags`, y la variable de entorno es solo el respaldo en `false` (§4.8, revisión SEG-05).
