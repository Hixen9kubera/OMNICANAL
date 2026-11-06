# Vigilante de cuarentena en la pestaña Inventario — diseño

Pedido y aprobado por Brandon el 6-nov-2026. Este documento es la especificación; el plan de implementación va
aparte.

## Para qué

Cuando bodega mueve un producto a la ubicación de cuarentena de Odoo, alguien tiene que enterarse y averiguar por
qué: daño, devolución, faltante (como JUGU-0089-PLA). Hoy nadie se entera: el panel no avisa y la copia del
historial de Odoo está apagada desde el 15-oct.

**Éxito:** dentro de la hora siguiente a que un producto llegue a cuarentena en Odoo, la pestaña Inventario lo
avisa con su SKU y sus piezas; desde ahí un clic abre un popup con todo lo que hay en cuarentena y el historial de
movimientos de cada SKU; la primera persona que lo abre lo deja revisado para todos.

## Decisiones de Brandon (6-nov)

| Tema | Decisión |
|---|---|
| Fuente | Odoo. Los movimientos de almacén se siguen registrando ahí. |
| Dónde se guardan los movimientos | `almacen.historial_movimientos`, que debe tener TODOS los movimientos de Odoo |
| Frecuencia | Cada hora |
| Desde cuándo avisa | Llegadas del **6-nov-2026 a las 12:00 p.m. (CDMX)** en adelante (`2026-11-06T18:00:00Z`) |
| Cuándo se quita el aviso | Al abrir el popup. La primera persona que lo abre lo deja revisado para todos, con su nombre y la hora |
| Dónde se ve | Solo en la pestaña Inventario: botón dentro del recuadro morado «Inventario» y una franja debajo |
| Qué muestra el popup | Todos los SKUs que hoy están en cuarentena; los nuevos arriba; cada uno con su historial completo |

## Lo que hay hoy (medido el 6-nov)

- Odoo tiene **una sola** ubicación de cuarentena: `TEXCO/FERRAFORME/CUARENTENA` (id 470719, interna, sin
  almacén, marcada como scrap). El 15-oct tenía 196 productos y 17,697 piezas.
- Producción vuelve a leer Odoo en solo lectura desde la v0.732.0 (5-nov).
- `almacen.historial_movimientos` tiene 6,319 movimientos, **solo de TEXCO II**, y **nada después del 15-oct a
  las 16:40 UTC**: su copia (`services/almacen_odoo.py`) está apagada en Railway (`ALMACEN_ODOO_ENABLED=false`).
- Desde la 0075 (acta 1.11, 28-oct) el historial solo se puede escribir mientras la bodega sea de Odoo (las tres lo
  son), y la foto `almacen.locations` tiene un candado de un solo copiador que haría fallar cada pasada.

## Componentes

### 1. La copia del historial, para los tres almacenes (`services/almacen_odoo.py`)

- `CEDIS_OBSERVADOS = ("TEX2", "TEXCO", "DROP")`.
- **Solo historial.** La pasada ya no lee ni escribe la foto de `almacen.locations` (es del copiador de Eduardo y
  su candado la rechaza). Sin foto no hay cuadre por pasada; el cuadre de la carga inicial se mide una vez, a mano,
  contra los quants de Odoo.
- **Cada 60 minutos** (antes 30). La primera pasada de un almacén sin renglones es la carga completa de su historia
  (TEXCO I y DROP OFF: del orden de 60 mil líneas en total). Después, solo el delta por `write_date` con 15 min de
  traslape.
- **La pasada completa de conciliación es una vez al día, a las 09:00 UTC (03:00 CDMX)**, y ya no en cada
  arranque: con muchos despliegues al día, releer 65 mil líneas en cada uno cargaría a Odoo sin necesidad.
- Registra su frescura en `almacen.copias_odoo` (`historial_hasta`, `ultima_pasada_at`, `ultima_pasada_ok`,
  `ultimo_error` redactado y de ≤500 caracteres). Los campos de la foto se quedan como están.
- Solo lee Odoo, por la guarda de solo lectura de la v0.732.0. Sin productos archivados, como hasta ahora.
- Se sigue encendiendo con `ALMACEN_ODOO_ENABLED` (hoy `false` en Railway). Se calla sola si una bodega deja de ser
  de Odoo.

### 2. Qué es una llegada a cuarentena

Un renglón de `almacen.historial_movimientos` con:
- `odoo_destino = 'TEXCO/FERRAFORME/CUARENTENA'`,
- `odoo_origen <> 'TEXCO/FERRAFORME/CUARENTENA'`,
- `fecha >= '2026-11-06T18:00:00Z'`.

La cuarentena no pertenece a ningún almacén, así que cada llegada aparece una sola vez: en el renglón del almacén de
donde salió. Las salidas de cuarentena no avisan.

### 3. Tabla nueva `almacen.cuarentena_revisadas` (migración 0083)

| Columna | Tipo | Qué es |
|---|---|---|
| `odoo_move_line_id` | bigint, llave primaria | la llegada que ya se vio |
| `revisado_at` | timestamptz, `now()` | cuándo se abrió el popup |
| `revisado_por` | text, obligatorio | el usuario de la sesión |

**Pendiente** = toda llegada sin renglón aquí. Solo se agregan renglones; nunca se borran ni se cambian. RLS
encendido; `service_role` solo lee, como el resto de `almacen`. Se registra en `ops.migraciones` y en
`supabase/migrations/RESERVAS.md` (0080 y 0081 están reservadas; 0083 es la siguiente libre).

### 4. API (`routers/inventario.py`, registradas antes de las rutas comodín `/{sku:path}`)

- `GET /api/inventario/cuarentena/pendientes` — solo kubera, sin Odoo, para el globo y la franja: SKUs pendientes
  con piezas y llegadas.
- `GET /api/inventario/cuarentena` — para el popup:
  - `pendientes` (lo mismo de arriba);
  - `en_cuarentena`: los SKUs con piezas hoy en la ubicación 470719, leídos de Odoo en vivo (quants), con nombre,
    piezas y su última llegada si la hay en el historial;
  - `copia`: la frescura de `almacen.copias_odoo`;
  - si Odoo no responde, `en_cuarentena` viene vacío con el motivo y los pendientes siguen.
  - Orden: los nuevos primero, de la llegada más reciente a la más vieja; después los demás, igual.
- `POST /api/inventario/cuarentena/revisar` `{ids: [odoo_move_line_id…]}` — guarda como revisadas exactamente las
  llegadas que se le mostraron a esa persona (`on conflict do nothing`), con `revisado_por` = usuario de la sesión.
  Sin usuario de sesión responde 401 y no guarda nada; ids que no son llegadas pendientes se ignoran.
- El historial de cada SKU reutiliza `GET /api/inventario/{sku}/movimientos`, el mismo del botón «Historial».

Todo lo que toca Odoo o la base va en `asyncio.to_thread` (regla 11).

### 5. Pantalla (`app/inventario/page.tsx` + componente nuevo del popup)

- **Botón «Cuarentena» dentro del recuadro morado**, siempre visible, con globo rojo y el número de SKUs pendientes.
- **Franja** entre el recuadro y las tarjetas, solo con pendientes: «Llegaron a cuarentena N SKUs (P piezas):
  SKU-A (x), SKU-B (y)… · Ver». Al tocarla también abre el popup.
- **Popup:** frescura de la copia arriba; la lista con la marca «Nuevo» en los pendientes; al tocar un SKU se
  despliega su historial. Se cierra con Esc o con un clic fuera.
- Al abrirse marca como revisados los pendientes que mostró; el globo y la franja se van para todos.

## Lo que NO hace

- No escribe en Odoo ni toca stock, Woo o canales.
- No escribe la foto `almacen.locations`.
- No avisa salidas de cuarentena ni nada fuera de la pestaña Inventario.
- No cuenta productos archivados.

## Errores

- Odoo caído: la pasada falla con aviso en el log y en `copias_odoo.ultimo_error`; la siguiente reintenta. El popup
  enseña los pendientes y dice que no pudo leer lo que hay hoy en cuarentena.
- Una pasada a medias no deja nada inconsistente: las escrituras del historial son repetibles.

## Pruebas

- Unitarias: qué es una llegada, el orden del popup, `revisar` (idempotente, solo los ids dados), el job cada 60 min
  y la conciliación diaria, el modo «solo historial».
- En local, antes de subir: base desechable (puerto 54341) con las migraciones hasta la 0083, la copia leyendo Odoo
  real en solo lectura y el panel apuntando al backend local. Requiere el usuario y la llave nuevos de Odoo en
  `.env.respaldo`.

## Encendido (cada paso con su dale)

1. Aplicar la 0083 en producción: ensayo con `ROLLBACK`, luego de verdad.
2. Subir el código a `main`.
3. `ALMACEN_ODOO_ENABLED=true` en Railway. Reinicia el backend; la primera pasada carga la historia de TEXCO I y
   DROP OFF.
4. Apunte para Eduardo: vuelve la copia del historial en los tres almacenes, sin la foto.
