# Migración 0068 — apunte para Eduardo

> **Ya está APLICADA en producción** (kubera, `tukwcvsi`): 9-oct-2026, 06:48 UTC,
> por el chat de Órdenes de venta, con el dale explícito de Brandon («aplícalo
> desde aquí»). No pasó por acta. Este apunte es para que la leas después y
> decidas lo que queda abierto. **En el sandbox NO está aplicada.**

Archivo: `supabase/migrations/0068_mudanza_ov_a_ventas_y_almacen.sql`.

## Qué hizo

Mudó las nueve tablas de las órdenes de venta propias, de `ops` a los esquemas
que tu 0066 creó vacíos, repartidas como dice el comentario de cada esquema:

| Esquema | Tablas |
|---|---|
| `ventas` | `ov_folio`, `ov_ordenes`, `ov_lineas`, `ov_mensajes`, `ov_archivos` |
| `almacen` | `almacenes`, `almacenes_hist`, `stock_almacen`, `stock_mov` |

Con `alter table … set schema`: no se copió ni se borró ningún dato. Filas,
índices, llaves foráneas, triggers, secuencias de identidad, RLS y privilegios
viajaron con cada tabla. Todo en una transacción, con `lock_timeout` de 5 s.

**Medido antes y después** (mismas cuentas en los dos lados): `ov_folio` 1,
`ov_ordenes` 1, `ov_lineas` 1, `ov_mensajes` 12, `ov_archivos` 0, `almacenes` 6,
`almacenes_hist` 6, `stock_almacen` 0, `stock_mov` 0. La vista
`ops.stock_apartado_descuadre_v` da 0 filas. Quedó su renglón en
`ops.migraciones` (`tablas_movidas: 9`).

## Lo que NO se movió

- **Las funciones siguen en `ops`** (los triggers, `ops.verificar_*`,
  `ops.exigir`). Sólo se volvieron a crear las 14 que nombraban las tablas por
  su esquema, **copiadas tal cual de producción** (`pg_get_functiondef`),
  cambiando únicamente `ops.<tabla>` por su nombre nuevo (25 referencias).
- `ops.devoluciones`, `ops.stock_formato*`, `ops.migraciones`,
  `ops.automatizacion_flags` y la vista `ops.stock_apartado_descuadre_v`.
  Brandon pidió mover sólo lo de este chat. Las llaves foráneas de
  `ops.devoluciones` y `ops.stock_formato` hacia las tablas mudadas siguen
  vivas (ahora cruzan de esquema).

## El puente que quedó en `ops` — pendiente de quitar

En `ops` hay ahora una **vista** con el nombre viejo de cada una de las nueve
tablas (`select * from <esquema>.<tabla>`, `security_invoker`). Son vistas
simples: se leen y se escriben igual que la tabla.

Por qué existen: el código desplegado decía `ops.<tabla>`, y además
`services/fanout_bodegas.py` (el tablero de bodegas, de otro chat) lee
`ops.almacenes`, `ops.stock_almacen` y `ops.stock_mov`. Con el puente nada se
cayó al aplicar. **Probado antes en local**: con las tablas ya mudadas, el
código viejo pasó sus 305 pruebas a través de las vistas (candados, `on
conflict`, `for update`, concurrencia).

Lo que hay que saber del puente:

1. **Quién sigue usándolo:** `fanout_bodegas.py` y sus pantallas, y
   `backend/scripts/verificar_0064_0065.py`. El módulo de órdenes de venta ya
   dice `ventas.` / `almacen.` (v0.628.0).
2. **Una columna nueva en la tabla NO aparece sola en su vista** (`select *` se
   congela al crear la vista). Si agregas columnas antes de quitar el puente,
   hay que recrear la vista.
3. **La 0064 y la 0065 ya no se pueden volver a correr** en una base con la
   0068: en `ops` hay vistas con esos nombres y sus `alter table` / `create
   trigger` truenan. Para una base nueva el orden es 0064 → 0065 → 0068.
4. Para quitarlo, cuando ya nadie diga `ops.<tabla>`: `drop view` de las nueve.
   No lo hice: Brandon pidió no borrar nada.

## Lo que queda abierto, y es tuyo decidir

- **Sandbox:** aplicar ahí la 0066, la 0067 y la 0068. (La 0066 y la 0067 no
  están en el repo; la 0068 crea los dos esquemas si faltan, así que no depende
  de ellas.)
- **`supabase/schema_manifest.json`:** no lo regeneré. La foto de producción
  cambió (dos esquemas con tablas, nueve vistas nuevas en `ops`).
- **`aplicar_migraciones.py`:** su lista `ESQUEMAS_PROPIOS` no incluye `ventas`
  ni `almacen`.
- **Tu guía** (`docs/MIGRACION_0064_0065_GUIA_AGENTE.md`) y el verificador
  siguen diciendo `ops.<tabla>`. Funcionan por el puente; no los edité.
- **Las funciones**: si las quieres también en `ventas` / `almacen`, es otra
  migración. Hoy `ops.exigir` y los triggers se llaman por su nombre en `ops`.
- **La bandera `ordenes_venta` no tiene fila**: está encendida por el respaldo
  `ORDENES_VENTA_ENABLED=true` en Railway desde el 6-oct (pedido de Brandon). Lo
  formal sigue siendo tu acta con la fila y la variable de vuelta en `false`.

## Reversa

`drop view ops.<tabla>` de las nueve, `alter table <esquema>.<tabla> set schema
ops`, y volver a crear las 14 funciones con el texto de la 0064/0065. El código
de v0.628.0 dejaría de encontrar las tablas: habría que regresar también el
commit.
