# 0083 `almacen.cuarentena_revisadas` — nota para Eduardo

**Qué te pido:** revisar `supabase/migrations/0083_almacen_cuarentena_revisadas.sql` y aplicarla con tu acta
(sandbox primero). **No está aplicada en ningún lado.** Brandon pidió subirla para que tú la apliques.

## Para qué es

Vigilante de cuarentena de la pestaña Inventario (pedido de Brandon, 6-nov-2026). Cuando bodega mueve un producto a
la cuarentena de Odoo (`TEXCO/FERRAFORME/CUARENTENA`, ubicación 470719), la pestaña avisa con un globo rojo y una
franja; un popup muestra lo que hay hoy en cuarentena y el historial de cada SKU. La primera persona que abre el
popup deja las llegadas como revisadas **para todos**. Esta tabla es donde se apunta eso.

## Qué crea (y nada más)

| Columna | Tipo | Qué es |
|---|---|---|
| `odoo_move_line_id` | bigint, PK | la línea de Odoo que llevó el producto a cuarentena (= `historial_movimientos.odoo_move_line_id`) |
| `revisado_at` | timestamptz, `now()` | cuándo se abrió el popup |
| `revisado_por` | text, obligatorio | usuario de la sesión (`app.usuario` por omisión; el backend lo manda explícito) |

- Solo agregar: `ops.tg_solo_agregar` (update/delete) y `ops.tg_sin_truncate`, en `enable always`.
- RLS sin políticas; `service_role` solo `select` (como el resto de `almacen`, 0075). Escribe el backend (postgres).
- Fila en `ops.migraciones`. Candado al inicio si faltan la 0070 o los triggers de la 0064.
- `lock_timeout 5s`, `statement_timeout 60s`.

Probada en una base local (fixture → 0064 → 0065 → 0066 → 0068 → 0069 → 0070 → 0083 dos veces): la segunda corrida
no truena; update, delete, truncate, insert sin usuario y usuario en blanco se rechazan.

**Reversa:** `drop table almacen.cuarentena_revisadas;` — sin ella, todas las llegadas vuelven a contar como
pendientes. No hay más dependencias.

## Lo que hace el código (ya en main) mientras tanto

- Lee la tabla directamente, sin detectar si existe. **Hasta que la apliques**, el globo y el popup responden
  «falta la migración 0083» y no muestran nada más. No escribe nada en ningún otro lado.
- **La copia del historial de Odoo (`services/almacen_odoo.py`) cambió**, y te toca de cerca:
  - Copia **TEX2, TEXCO y DROP** (antes solo TEX2), **cada hora** (antes 30 min), y la conciliación completa es
    una vez al día a las 09:00 UTC (antes en cada arranque).
  - **Ya no lee ni escribe `almacen.locations`**: desde tu 0076 es de tu copia.
  - Ahora **firma** cada transacción que escribe con `set_config('app.almacen_escritor', 'copia_odoo:' ||
    txid_current(), true)` (local a la transacción), como pide tu `tg_escritor_por_fuente`. Sin la firma, cada
    pasada habría fallado con 42501 desde el 28-oct.
  - Anota en `almacen.copias_odoo`: `historial_hasta` (= instante en que leyó Odoo), `ultima_pasada_at`,
    `ultima_pasada_ok` y `ultimo_error` (clase y, solo si el mensaje es propio, el mensaje; nunca la URL de Odoo).
    No toca los campos de la foto.
  - **ENCENDIDA el 6-nov-2026 a las 20:48 UTC (dale de Brandon). Primera pasada: TEXCO 58,347 líneas y DROP 382 (cuadre 94/94), TEX2 al día; las tres con `ultima_pasada_ok`. Freno sin deploy: `ALMACEN_ODOO_ENABLED=false`.**
- Ojo con tu chequeo de `copias_odoo.foto_at` (más de 90 min = alerta): esta copia ya no llena la foto, así que
  ese aviso depende de tu copiador.
