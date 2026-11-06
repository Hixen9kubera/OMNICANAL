# Vigilante de cuarentena — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Avisar en la pestaña Inventario, dentro de la hora, qué SKUs llegaron a la cuarentena de Odoo y cuántas piezas, con un popup que lista todo lo que hay en cuarentena y el historial de cada SKU.

**Architecture:** La copia de Odoo (`services/almacen_odoo.py`) vuelve a llenar `almacen.historial_movimientos`, ahora de TEXCO, TEX2 y DROP, cada hora y sin la foto de ubicaciones. Un servicio nuevo (`services/cuarentena.py`) lee las llegadas a `TEXCO/FERRAFORME/CUARENTENA` de ese historial, las cruza con `almacen.cuarentena_revisadas` (migración 0083) y con los quants vivos de Odoo. Tres rutas en `routers/inventario.py` alimentan un botón en el recuadro morado, una franja y un popup.

**Tech Stack:** FastAPI + psycopg2 (pool `supabase_db`), XML-RPC de Odoo en solo lectura (`services.odoo._kw_flujo`), APScheduler, Next.js 14 + TypeScript, Postgres de Supabase.

**Spec:** `docs/superpowers/specs/2026-11-06-vigilante-cuarentena-design.md`

## Global Constraints

- Ubicación de cuarentena en Odoo: id `470719`, ruta `TEXCO/FERRAFORME/CUARENTENA`.
- Avisa llegadas con `fecha >= 2026-11-06T18:00:00Z` (12:00 p.m. CDMX).
- Llegada = renglón del historial con `odoo_destino = ruta`, `odoo_origen <> ruta`.
- Copia: `CEDIS_OBSERVADOS = ("TEX2", "TEXCO", "DROP")`, cada 60 min; conciliación completa diaria a las 09:00 UTC; nunca en cada arranque.
- La copia NO lee ni escribe `almacen.locations`.
- Odoo solo se lee (la guarda de v0.732.0 ya niega cualquier escritura).
- Regla 11: todo lo que espera a Odoo o a la base, en `asyncio.to_thread`.
- Sin productos archivados de Odoo.
- Sin variable nueva: la copia se enciende con la existente `ALMACEN_ODOO_ENABLED`.
- Textos de la pantalla en español, como el resto del panel.
- Nada se sube a `main` ni se aplica en producción sin el dale de Brandon en ese paso.

## Review Focus

1. Odoo no responde al abrir el popup → los pendientes se ven igual y el popup dice que no pudo leer Odoo (prueba en Task 4).
2. Dos personas abren el popup a la vez → nadie truena y cada llegada queda revisada una sola vez (prueba en Task 3).
3. Llega algo nuevo entre que el popup se muestra y se marca → solo se marca lo mostrado; lo nuevo sigue avisando (prueba en Task 3).
4. Renombran la ubicación de cuarentena en Odoo → el popup lo dice en vez de quedarse callado (prueba en Task 3).
5. Un SKU con diagonal (`CALZ-0194-BLN/AZL-40`) en el popup → su historial abre (cubierto por `movimientosInventario`, que ya codifica el SKU; se revisa en Task 6).

---

### Task 1: Migración 0083 `almacen.cuarentena_revisadas`

**Files:**
- Create: `supabase/migrations/0083_almacen_cuarentena_revisadas.sql`
- Modify: `supabase/migrations/RESERVAS.md` (fila 0083)

**Interfaces:**
- Produces: tabla `almacen.cuarentena_revisadas(odoo_move_line_id bigint PK, revisado_at timestamptz, revisado_por text)`.

- [ ] **Step 1: Escribir la migración**

```sql
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

do $$ begin
  if to_regclass('almacen.historial_movimientos') is null then
    raise exception using errcode = 'KB000', message = '0083: falta almacen.historial_movimientos (0070)';
  end if;
end $$;

create table if not exists almacen.cuarentena_revisadas (
  odoo_move_line_id bigint      not null,
  revisado_at       timestamptz not null default now(),
  revisado_por      text        not null default nullif(current_setting('app.usuario', true), ''),
  constraint cuarentena_revisadas_pkey      primary key (odoo_move_line_id),
  constraint cuarentena_revisadas_quien_chk check (length(btrim(revisado_por)) > 0)
);

drop trigger if exists cuarentena_revisadas_solo_agregar on almacen.cuarentena_revisadas;
create trigger cuarentena_revisadas_solo_agregar before update or delete on almacen.cuarentena_revisadas
  for each row execute function ops.tg_solo_agregar();
drop trigger if exists cuarentena_revisadas_sin_truncate on almacen.cuarentena_revisadas;
create trigger cuarentena_revisadas_sin_truncate before truncate on almacen.cuarentena_revisadas
  for each statement execute function ops.tg_sin_truncate();
alter table almacen.cuarentena_revisadas enable always trigger cuarentena_revisadas_solo_agregar,
                                        enable always trigger cuarentena_revisadas_sin_truncate;

alter table almacen.cuarentena_revisadas enable row level security;
revoke all on almacen.cuarentena_revisadas from public, anon, authenticated, service_role;
grant select on almacen.cuarentena_revisadas to service_role;

comment on table almacen.cuarentena_revisadas is
  'Llegadas a la cuarentena de Odoo (TEXCO/FERRAFORME/CUARENTENA) que alguien ya vio al abrir el popup de la pestaña Inventario. Pendiente = llegada del historial sin renglón aquí. Solo se agregan renglones.';

insert into ops.migraciones (migracion, detalle)
select '0083_almacen_cuarentena_revisadas', jsonb_build_object('cuarentena_revisadas', 0)
 where not exists (select 1 from ops.migraciones where migracion = '0083_almacen_cuarentena_revisadas');
commit;
```

- [ ] **Step 2: Probarla en una base desechable** (puerto 54341, verificar `show data_directory` antes): `backend/tests/ov_fixture.sql` → 0064 → 0065 → 0068 → 0069 → 0070 → 0083 dos veces.
  Expected: las dos corridas sin error; un `insert` con `set_config('app.usuario','a@b',true)` entra; `update`, `delete`, `truncate` y un insert sin usuario fallan.

- [ ] **Step 3: Fila en `RESERVAS.md`:** `| 0083 | almacen_cuarentena_revisadas | sí | no (la aplica su acta) | Vigilante de cuarentena (6-nov) |`.

- [ ] **Step 4: Commit** `git commit -m "0083: almacen.cuarentena_revisadas"`

---

### Task 2: La copia del historial, para tres almacenes y sin foto

**Files:**
- Modify: `backend/services/almacen_odoo.py`
- Modify: `backend/services/scheduler.py:1036-1070`
- Modify: `backend/scripts/copiar_almacen_odoo.py`
- Test: `backend/tests/test_almacen_odoo.py`

**Interfaces:**
- Produces:
  - `CEDIS_OBSERVADOS = ("TEX2", "TEXCO", "DROP")`
  - `sincronizar(cedis, *, completo: bool, abrir, aplicar: bool = True, forzar: bool = False, medir_cuadre: bool = False) -> dict` con llaves `cedis, nombre, completo, aplicado, desde, historial, cuadre` (sin `foto`). Con `medir_cuadre` lee los quants y devuelve `cuadre_filas(...)`; sin él, `cuadre=None`.
  - `registrar_copia(cur, cedis: str, *, ok: bool, historial_hasta: datetime | None, error: str | None) -> None` — `update almacen.copias_odoo set historial_hasta, ultima_pasada_at = now(), ultima_pasada_ok, ultimo_error = left(error, 500)`.
  - `vigilar(completo: bool = False) -> list[dict]` — nunca lanza; registra `ok/error` por almacén con `registrar_copia`.
- Se borran `escribir_locations`, `cuadre` (SQL), `_ultima_completa` y `COMPLETA_CADA`, y sus pruebas.

- [ ] **Step 1: Pruebas que fallan** en `test_almacen_odoo.py`:
  - `test_observa_los_tres_almacenes`: `assert ao.CEDIS_OBSERVADOS == ("TEX2", "TEXCO", "DROP")`.
  - `test_la_pasada_no_toca_locations`: `sincronizar` con un cursor falso; ninguna sentencia ejecutada contiene `almacen.locations`.
  - `test_vigilar_registra_ok_y_error`: `sincronizar` simulado que truena en `"TEXCO"`; `registrar_copia` se llama con `ok=True` para TEX2 y DROP y `ok=False` con el error para TEXCO; `vigilar()` no lanza.
  - `test_vigilar_respeta_completo`: `vigilar(completo=True)` pasa `completo=True` a los tres; `vigilar()` pasa `False`.
  - En `Programador`: el job `almacen_odoo` con `minutes == 60`, y un job `almacen_odoo_completa` con disparador `cron`, `hour=9`, `minute=0`.

- [ ] **Step 2: Correrlas y ver que fallan:** `cd backend && .venv/Scripts/python -m unittest tests.test_almacen_odoo` → FAIL.

- [ ] **Step 3: Implementar** los cambios de **Interfaces** en `almacen_odoo.py`, el job de 60 min y el cron diario en `scheduler.py` (mismo `try` y mismo `to_thread`), y en el script cambiar la salida a solo historial y agregar `--medir-cuadre`.

- [ ] **Step 4: Correr las pruebas:** misma orden → PASS (y `python -m py_compile` de los tres archivos).

- [ ] **Step 5: Commit** `git commit -m "Copia de Odoo: historial de los tres almacenes, cada hora, sin foto"`

---

### Task 3: Servicio `services/cuarentena.py`

**Files:**
- Create: `backend/services/cuarentena.py`
- Test: `backend/tests/test_cuarentena.py`

**Interfaces:**
- Consumes: el historial que llena la Task 2; la tabla de la Task 1; `services.odoo._kw_flujo`.
- Produces:
  - `UBICACION_ID = 470719`, `RUTA = "TEXCO/FERRAFORME/CUARENTENA"`, `DESDE = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)`.
  - `pendientes(cur) -> list[dict]` — por SKU: `{sku, piezas, ultima, llegadas: [{odoo_move_line_id, fecha, piezas, desde, documento, quien, cedis}]}`, de la llegada más reciente a la más vieja.
  - `ultimas_llegadas(cur, skus: list[str]) -> dict[str, dict]` — la llegada más reciente de cada SKU, de cualquier fecha.
  - `en_cuarentena() -> dict` — `{ok: bool, motivo: str | None, skus: [{sku, nombre, piezas}]}`. Lee la ubicación `UBICACION_ID` y sus quants con `quantity > 0` de productos activos. Si el `complete_name` de la ubicación no es `RUTA`, `ok=False` y `motivo` lo dice. Si Odoo falla, `ok=False` con el motivo; nunca lanza.
  - `armar_popup(pend: list[dict], en_cuar: list[dict], ultimas: dict) -> list[dict]` — renglones `{sku, nombre, piezas, nuevo: bool, llegada: dict | None}`; primero los `nuevo`, por `llegada.fecha` desc; luego los demás, igual, y los sin llegada al final. Un pendiente que ya no está en cuarentena se incluye con `piezas=0`.
  - `revisar(cur, ids: list[int], quien: str) -> int` — inserta solo los ids que son llegadas pendientes (`fecha >= DESDE`), `on conflict do nothing`; devuelve los insertados. Con `ids` vacío no toca la base.

- [ ] **Step 1: Pruebas que fallan:**
  - `test_orden_del_popup`: dos pendientes (fechas 7-nov y 8-nov) y un viejo; el orden es 8-nov, 7-nov, viejo; los dos primeros `nuevo=True`.
  - `test_pendiente_que_ya_salio_aparece_con_cero`.
  - `test_revisar_sin_ids_no_toca_la_base` (cursor falso sin sentencias).
  - `test_revisar_solo_marca_lo_mostrado`: el SQL recibe exactamente los ids dados y lleva `on conflict do nothing`.
  - `test_en_cuarentena_ruta_cambiada`: `_kw_flujo` falso que devuelve otra `complete_name` → `ok=False` y el motivo menciona la ruta.
  - `test_en_cuarentena_odoo_caido`: `_kw_flujo` que lanza → `ok=False`, `skus=[]`.

- [ ] **Step 2: Correrlas** → FAIL.
- [ ] **Step 3: Implementar** las funciones de **Interfaces**. El SQL de llegadas filtra por `odoo_destino = RUTA and odoo_origen <> RUTA and fecha >= DESDE` y hace `left join almacen.cuarentena_revisadas`.
- [ ] **Step 4: Correrlas** → PASS.
- [ ] **Step 5: Commit** `git commit -m "Cuarentena: llegadas, lo que hay hoy en Odoo y revisar"`

---

### Task 4: Rutas en `routers/inventario.py`

**Files:**
- Modify: `backend/routers/inventario.py` (antes del bloque «Registro DIFERIDO de la ruta comodín»)
- Test: `backend/tests/test_cuarentena_rutas.py`

**Interfaces:**
- Consumes: `services.cuarentena` (Task 3), `core.actor.actual()`, `supabase_db.get_cursor`.
- Produces:
  - `GET /api/inventario/cuarentena/pendientes` → `{pendientes, skus: int, piezas: int, copia: {almacen, historial_hasta, ultima_pasada_at, ultima_pasada_ok}[]}`.
  - `GET /api/inventario/cuarentena` → `{pendientes, lista: armar_popup(...), odoo: {ok, motivo}, copia}`.
  - `POST /api/inventario/cuarentena/revisar` cuerpo `{ids: list[int]}` → `{revisadas: int}`; 401 sin usuario de sesión.

- [ ] **Step 1: Pruebas que fallan** con `TestClient` y el servicio simulado:
  - `test_pendientes_no_llama_a_odoo` (`en_cuarentena` no se llama).
  - `test_popup_con_odoo_caido_trae_pendientes`: `en_cuarentena` → `{ok: False, motivo: "x", skus: []}`; respuesta 200 con `pendientes` y `odoo.ok == False`.
  - `test_revisar_sin_sesion_401`: `actor.actual()` → `""` → 401 y `revisar` no se llama.
  - `test_revisar_con_sesion` → 200 `{revisadas: 2}` y `revisar` recibió los ids y el usuario.
  - `test_cuarentena_no_la_traga_la_ruta_comodin`: `GET /api/inventario/cuarentena` no llega a la ficha de un SKU llamado «cuarentena».
- [ ] **Step 2: Correrlas** → FAIL.
- [ ] **Step 3: Implementar** las tres rutas (todo en `asyncio.to_thread`).
- [ ] **Step 4: Correrlas** → PASS; y `tests.test_inventario_flujo` sigue pasando.
- [ ] **Step 5: Commit** `git commit -m "Inventario: rutas de cuarentena"`

---

### Task 5: Pantalla — botón, franja y popup

**Files:**
- Modify: `frontend/lib/types.ts`, `frontend/lib/api.ts`
- Create: `frontend/components/CuarentenaPopup.tsx`
- Modify: `frontend/app/inventario/page.tsx` (componente `Banner` y el cuerpo de `InventarioPage`)

**Interfaces:**
- Consumes: las rutas de la Task 4; `movimientosInventario(sku, causa, limite, dias, signal)` existente.
- Produces:
  - Tipos `LlegadaCuarentena`, `PendienteCuarentena`, `RenglonCuarentena`, `CuarentenaResp`, `CuarentenaPendientesResp`.
  - `cuarentenaPendientes(): Promise<CuarentenaPendientesResp>`, `cuarentena(): Promise<CuarentenaResp>`, `revisarCuarentena(ids: number[]): Promise<{revisadas: number}>` (con `postJSON`).
  - `<CuarentenaPopup abierto onCerrar onRevisado />`.

- [ ] **Step 1: Tipos y funciones de API.**
- [ ] **Step 2: `CuarentenaPopup`:** carga `cuarentena()`; arriba la frescura de la copia («Historial de Odoo al día hasta …»); la lista con la marca «Nuevo»; al tocar un SKU despliega su historial con `movimientosInventario(sku, "reales", 200)` (fecha, causa, piezas, de → a, documento, quién); si `odoo.ok` es falso, un aviso con el motivo; Esc y clic fuera cierran. Al terminar de cargar llama `revisarCuarentena(ids de los pendientes mostrados)` y después `onRevisado()`.
- [ ] **Step 3: En `page.tsx`:** pedir `cuarentenaPendientes()` al montar; en `Banner`, un botón «Cuarentena» junto al de recargar, con globo rojo con el número de SKUs pendientes; debajo del `Banner`, la franja (solo con pendientes): «Llegaron a cuarentena N SKUs (P piezas): SKU (x), …» con «Ver»; los dos abren el popup; `onRevisado` vacía los pendientes.
- [ ] **Step 4: Verificar:** `cd frontend && npx tsc --noEmit` → sin errores.
- [ ] **Step 5: Commit** `git commit -m "Inventario: botón, franja y popup de cuarentena"`

---

### Task 6: Prueba en local, entrega y encendido

**Files:**
- Modify: `README.md` (entrada de la versión), `backend/main.py` (versión, dos lugares), `CLAUDE.md` (fila del mapa de piezas)
- Create: `docs/VIGILANTE_CUARENTENA_NOTA_EDUARDO.md`

- [ ] **Step 1: Local de punta a punta.** Base desechable (Task 1) + `copiar_almacen_odoo.py --destino local --cedis TEXCO --aplicar --medir-cuadre` (y TEX2, DROP) con la llave de Odoo de `.env.respaldo`; backend local con el scheduler apagado; panel local. Expected: cuadre por SKU contra Odoo; con una llegada sembrada después de `DESDE`, el globo, la franja y el popup aparecen; al abrir el popup se van; un SKU con diagonal abre su historial. Captura para Brandon.
- [ ] **Step 2: Suite:** `.venv/Scripts/python -m unittest discover -s tests` y `npx tsc --noEmit` → solo las fallas que ya traía `main`.
- [ ] **Step 3: README, versión, CLAUDE.md y nota para Eduardo.**
- [ ] **Step 4 (dale de Brandon): 0083 en producción** — ensayo con `ROLLBACK` por el puerto 5432, luego de verdad; comprobaciones como en la 0069/0070.
- [ ] **Step 5 (dale de Brandon): subir a `main`** desde el worktree (`git pull --rebase`, push) y vigilar el despliegue.
- [ ] **Step 6 (dale de Brandon): `ALMACEN_ODOO_ENABLED=true`** en Railway; verificar en el log la carga inicial de TEXCO y DROP y la línea de cada hora; `copias_odoo.ultima_pasada_ok = true` en los tres.
