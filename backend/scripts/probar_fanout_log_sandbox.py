"""
probar_fanout_log_sandbox.py — La BITÁCORA del fan-out sin MySQL.

QUÉ ARREGLA
-----------
`fanout_log` guardaba dos cosas en la misma tabla: la MARCA de idempotencia
("¿ya moví esta mercancía?") y la BITÁCORA ("qué se intentó y cómo salió"). La
migración 0022 se llevó la marca y dejó el historial sin casa.

El censo del 20-ago encontró que esa tabla tiene **9 lectores** y solo los DOS
que deciden estaban cubiertos. Los otros cuatro —las pantallas del fan-out— se
habrían quedado en blanco el día del corte.

Es el mismo error que el consejo nos había señalado para los dos candados —*dos
conceptos distintos en una tabla porque ya existe y es cómoda*— cometido esta
vez por nosotros mientras arreglábamos justo eso.

LO QUE SE PRUEBA
----------------
Con `MYSQL_ENABLED=false` —el mundo de después del corte—, los cuatro lectores
contestan desde kubera y contestan lo MISMO que contestaban:

  · `historial` respeta el límite, el orden y el filtro de errores
  · `resumen` agrupa por acción y por canal
  · `movimientos_full` filtra por prefijo y por ventana de horas
  · `pendientes_inventario` filtra por la lista de acciones del vigilante

Y lo que más importa: **la escritura doble deja el evento en kubera aunque MySQL
no exista**, que es lo que evita perder la bitácora en el corte.

Escribe en el sandbox con marcas `PRUEBA-` y limpia al final.

Uso:
  ...python backend/scripts/probar_fanout_log_sandbox.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    if not p.exists():
        return d
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


_ST = cargar("env.staging")
if not _ST.get("SUPABASE_DB_URL"):
    sys.exit("ABORT: env.staging sin SUPABASE_DB_URL.")
os.environ["SUPABASE_DB_URL"] = _ST["SUPABASE_DB_URL"]
os.environ["APP_ENV"] = "staging"
os.environ["MYSQL_ENABLED"] = "false"

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION y este script ESCRIBE.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")

from services import fanout_read, fanout_stock  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

_ok = True
_SKU = "PRUEBA-FANOUT"


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def con(bandera: str, valor: bool, fn, *a, **k):
    previo = getattr(settings, bandera)
    setattr(settings, bandera, valor)
    try:
        return fn(*a, **k), None
    except Exception as exc:  # noqa: BLE001
        return None, exc
    finally:
        setattr(settings, bandera, previo)


def limpiar() -> None:
    sdb.execute("delete from ops.fanout_log where sku = %s", (_SKU,))


def main() -> None:
    print(f"SANDBOX {_ref[:8]}... · MYSQL_ENABLED={settings.mysql_enabled}\n")
    limpiar()
    ahora = datetime.now(timezone.utc)

    # ── El mundo de hoy, sin MySQL ──────────────────────────────────────────
    v, e_v = con("supabase_read_fanout_log", False, fanout_stock.historial, 10)
    print(f"  historial() apagada y sin MySQL -> {v}")
    check("apagada sin MySQL: el dashboard sale VACIO",
          e_v is None and v == [],
          "cuatro pantallas del fan-out en blanco, sin un error que lo diga")

    # ── La escritura doble: el evento tiene que quedar aunque MySQL no exista ─
    evento = {"ts_dt": ahora, "sku": _SKU, "motivo": "prueba de bitacora",
              "dry_run": False, "stock_drop": 10, "objetivo": 7, "ms": 42,
              "acciones": [
                  {"canal": "mercado_libre", "cuenta": "BEKURA",
                   "item_id": "MLM-PRUEBA-1", "accion": "full_ingreso",
                   "stock_actual_canal": 3, "resultado": "OK aplicado"},
                  {"canal": "amazon", "cuenta": "AMAZON",
                   "item_id": "ASIN-PRUEBA", "accion": "fba_ingreso",
                   "stock_actual_canal": 1, "resultado": "ERROR no se pudo"},
              ]}
    settings.supabase_write_fanout_log = True
    try:
        fanout_stock._persistir(evento)
    finally:
        settings.supabase_write_fanout_log = False

    filas = sdb.fetch_all(
        "select accion, canal, resultado from ops.fanout_log where sku=%s order by id",
        (_SKU,))
    check("SIN MySQL, _persistir deja los eventos en ops.fanout_log",
          len(filas) == 2, f"{len(filas)} filas — se esperaban 2 (una por accion)")
    if len(filas) == 2:
        check("  una fila POR ACCION, con su canal",
              {f["accion"] for f in filas} == {"full_ingreso", "fba_ingreso"}
              and {f["canal"] for f in filas} == {"mercado_libre", "amazon"},
              str(filas))

    # ── Los cuatro lectores, con la bandera prendida ────────────────────────
    h, e = con("supabase_read_fanout_log", True, fanout_stock.historial, 50)
    check("historial() contesta desde kubera", e is None and h, str(e))
    if h:
        check("  y respeta el limite", len(h) <= 50)
        check("  con las mismas llaves que la version MySQL",
              set(h[0]) == {"ts", "sku", "motivo", "dry_run", "stock_drop",
                            "objetivo", "canal", "cuenta", "item_id", "accion",
                            "stock_canal", "resultado", "ms"},
              str(sorted(h[0]))[:110])

    err, e = con("supabase_read_fanout_log", True, fanout_stock.historial, 50, True)
    check("historial(solo_errores) filtra de verdad",
          e is None and err is not None
          and all(str(x["resultado"]).startswith("ERROR") for x in err),
          f"{len(err or [])} filas, todas ERROR")
    # OJO: comparar los LARGOS no sirve. Con la tabla llena las dos consultas
    # topan en el limite y dan 50 = 50 — este chequeo pasaba con 2 filas de
    # prueba y reprobo en cuanto hubo datos de verdad. Una prueba que solo
    # funciona con la tabla vacia no es una prueba.
    #
    # Lo que demuestra que el filtro EXCLUYE es que el historial completo traiga
    # al menos una fila que NO es error. Eso no depende del tope.
    check("  y el filtro EXCLUYE (el historial completo trae no-errores)",
          h is not None
          and any(not str(x["resultado"] or "").startswith("ERROR") for x in h),
          f"{sum(1 for x in (h or []) if not str(x['resultado'] or '').startswith('ERROR'))}"
          f" de {len(h or [])} no son error")

    r, e = con("supabase_read_fanout_log", True, fanout_stock.resumen)
    check("resumen() agrupa por accion y por canal",
          e is None and r and r.get("por_accion") and r.get("por_canal"),
          str(e) if e else f"{len(r.get('por_accion', {}))} acciones")
    if r:
        check("  y cuenta los errores aparte",
              r.get("errores") is not None and int(r["errores"]) >= 1,
              f"errores={r.get('errores')}")

    m, e = con("supabase_read_fanout_log", True, fanout_read.movimientos_full, 24)
    check("movimientos_full filtra full_* y fba_*",
          e is None and m is not None
          and all(x["accion"].startswith(("full_", "fba_")) for x in m),
          str(e) if e else f"{len(m or [])} movimientos")

    # La ventana de horas tiene que EXCLUIR: si trae lo mismo con 0 horas, no
    # esta filtrando y la pantalla mostraria historia vieja como si fuera de hoy.
    m0, e0 = con("supabase_read_fanout_log", True, fanout_read.movimientos_full, 0)
    check("  y la ventana de horas EXCLUYE (con 0 h no trae lo de hace rato)",
          e0 is None and len(m0 or []) < len(m or [1]),
          f"0 h -> {len(m0 or [])} · 24 h -> {len(m or [])}")

    # OJO: `all()` sobre una lista VACIA devuelve True. La primera version de
    # este chequeo pasaba sin haber probado nada, porque el evento de arriba solo
    # traia acciones full_/fba_ y `pendientes_inventario` no encontraba ninguna.
    # Un chequeo que pasa con cero filas no es un chequeo. Se siembra una accion
    # del vigilante de inventario para que TENGA algo que encontrar.
    fanout_read.registrar({"ts": ahora, "sku": _SKU, "accion": "woo_cambio",
                           "motivo": "prueba inventario", "dry_run": False,
                           "resultado": "OK sembrado"})
    pi, e = con("supabase_read_fanout_log", True, fanout_read.pendientes_inventario, 20)
    check("pendientes_inventario encuentra la accion del vigilante",
          e is None and pi and any(x["sku"] == _SKU and x["accion"] == "woo_cambio"
                                   for x in pi),
          str(e) if e else f"{len(pi or [])} eventos")
    check("  y NO cuela las acciones que no son suyas (full_/fba_)",
          pi is not None
          and all(x["accion"] in fanout_read._ACCIONES_INVENTARIO for x in pi)
          and not any(x["accion"].startswith(("full_", "fba_")) for x in pi))

    # ── LOS CUATRO ESCRITORES, no solo uno ──────────────────────────────────
    # El primer intento espejo SOLO `_persistir`, y al encender la escritura
    # doble en produccion llegaron 4 eventos a kubera mientras MySQL sumaba 14:
    # faltaban todos los `full_ignorado`, o sea los de `stock_full`. Cuatro
    # sitios escriben esta bitacora y el censo lo decia; se hizo uno.
    #
    # Esta prueba existe para que eso no se pueda repetir en silencio.
    print()
    from services import pedidos_ml, stock_full, stock_watch  # noqa: F401
    import inspect
    faltan = []
    for mod, fn in ((fanout_stock, "_persistir"),
                    (stock_full, "_registrar"),
                    (stock_watch, "_anotar"),
                    (pedidos_ml, "_compensar_stock_protegido")):
        try:
            fuente = inspect.getsource(getattr(mod, fn))
        except Exception:  # noqa: BLE001
            faltan.append(f"{mod.__name__}.{fn} (no se pudo leer)")
            continue
        if "fanout_read.espejar" not in fuente:
            faltan.append(f"{mod.__name__}.{fn}")
    check("los CUATRO escritores de fanout_log pasan por el mismo espejo",
          not faltan, f"sin espejar: {faltan}" if faltan else
          "fanout_stock, stock_full, stock_watch y pedidos_ml")

    # Y que de verdad escriba: se llama al de stock_full, que era el olvidado.
    settings.supabase_write_fanout_log = True
    try:
        stock_full._registrar(_SKU, "PRUEBA-op-espejo", "AMAZON", "full_ignorado",
                              5, 5, "PRUEBA sin efecto")
    finally:
        settings.supabase_write_fanout_log = False
    hay = sdb.fetch_one(
        "select accion from ops.fanout_log where sku=%s and accion='full_ignorado'",
        (_SKU,))
    check("  y stock_full (el que faltaba) llega de verdad a kubera", bool(hay),
          str(hay))

    from services import db
    check("el pool de MySQL nunca se creo", db._pool is None)

    limpiar()
    print(f"\n  (limpieza: las filas PRUEBA- se borraron del sandbox)")
    print(f"\nRESULTADO: {'la bitacora del fan-out sobrevive al corte' if _ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
