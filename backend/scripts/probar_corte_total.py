"""
probar_corte_total.py — ¿Se puede apagar MySQL DE GOLPE? La lista de lo que se
rompe, medida en vez de estimada.

LA PREGUNTA
-----------
El plan de migración va paso por paso: repuntar un grupo de lectores, verificar,
encender, siguiente. Eduardo preguntó si se puede pasar el corte de una sola vez
y dejar de depender de MySQL ya.

Este script contesta con evidencia, no con opinión. El sandbox corre con
`MYSQL_ENABLED=false`: **ya es el mundo de después del retiro**. Se prenden
TODAS las banderas `supabase_read_*` a la vez —el corte total— y se recorre lo
que el panel usa de verdad. Lo que truena, truena aquí y no en producción.

CÓMO LEER EL RESULTADO
----------------------
  [PASA]  el camino ya vive sin MySQL
  [VACIO] no truena, pero contesta vacío  ← EL PELIGROSO
  [TRUENA] falla ruidosamente

**El renglón que importa es VACIO.** Un camino que truena se arregla porque se
ve; uno que contesta vacío se ve igual que "no hay nada" y ahí es donde este
proyecto ya perdió dinero: la tabla `pedidos_ml` congelada contestaba "esa orden
no existe" con total seguridad, y nacieron 964 pedidos fantasma en 4 h 17 min.

SOLO SE LLAMAN FUNCIONES DE LECTURA. Está escrito una por una abajo, a propósito:
llamar a un servicio "para ver qué pasa" puede escribir (`ventas_ml.resumen`
refresca su caché), y esa lección ya se pagó una vez en esta migración.

Uso:
  ...python backend/scripts/probar_corte_total.py
"""
from __future__ import annotations

import os
import sys
import traceback
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
os.environ["MYSQL_ENABLED"] = "false"
os.environ["APP_ENV"] = "staging"

from config import settings  # noqa: E402

if (settings.supabase_db_url or "").split("postgres.")[-1][:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")

# EL CORTE TOTAL: todas las banderas de lectura, a la vez. Se incluyen tambien
# las de escritura de tokens, porque sin doble escritura kubera nunca tendria el
# par y la sonda mediria una tabla vacia en vez del mecanismo.
_BANDERAS = [n for n in dir(settings)
             if n.startswith("supabase_read_") or n == "supabase_write_tokens"]
for n in _BANDERAS:
    setattr(settings, n, True)

_RES: list[tuple[str, str, str]] = []


def sonda(nombre: str, fn, vacio=lambda r: not r) -> None:
    """Corre una lectura y clasifica: PASA / VACIO / TRUENA."""
    try:
        r = fn()
    except Exception as exc:  # noqa: BLE001
        linea = ""
        for m in reversed(traceback.extract_tb(exc.__traceback__)):
            if "backend" in m.filename and "scripts" not in m.filename:
                linea = f"{Path(m.filename).name}:{m.lineno}"
                break
        _RES.append((nombre, "TRUENA", f"{type(exc).__name__}: {str(exc)[:70]} @ {linea}"))
        return
    if vacio(r):
        _RES.append((nombre, "VACIO", f"devolvio {str(r)[:60]}"))
    else:
        n = len(r) if hasattr(r, "__len__") else r
        _RES.append((nombre, "PASA", f"{n} …" if not isinstance(n, int) else f"{n}"))


def main() -> None:
    print(f"CORTE TOTAL simulado — sandbox, MySQL apagado, "
          f"{len(_BANDERAS)} banderas de lectura en true\n")

    from services import (amazon, channel_read, costing_read, inventario,  # noqa
                          meli, presencia, studio)

    # SKUs reales del sandbox para que las sondas tengan con qué trabajar.
    from services import supabase_db as sdb
    skus = [f["sku"] for f in sdb.fetch_all(
        """select sku::text as sku from channel.listings
            where canal='mercado_libre' and nullif(listing_id,'') is not null
            group by 1 order by 1 limit 5""")]
    sku = skus[0] if skus else "NO-HAY"

    # ── Lo repuntado (bloques 1 y 2) ────────────────────────────────────────
    print("── lo que ya se repunto ──")
    sonda("meli.listar (rejilla ML)", lambda: meli.listar(page=1, per_page=5)[0])
    sonda("meli.contar_publicados", lambda: meli.contar_publicados(), lambda r: not r)
    sonda("amazon.listar (rejilla Amazon)", lambda: amazon.listar(page=1, per_page=5)[0])
    sonda("amazon.contar_publicados", lambda: amazon.contar_publicados(), lambda r: not r)
    sonda("studio.estado_publicacion", lambda: studio.estado_publicacion(sku),
          lambda r: not r["ml"] and not r["amazon"]["publicado"])
    sonda("presencia.presencia_por_sku", lambda: presencia.presencia_por_sku(skus))
    sonda("channel_read.presencia", lambda: channel_read.presencia(skus))

    # ── Lo que NO se ha tocado (bloques 3 y 4, y los otros pasos) ───────────
    print("── lo que falta ──")
    sonda("inventario.leer_inventario", lambda: inventario.leer_inventario(skus))
    sonda("costing_read.precios_de", lambda: costing_read.precios_de(skus))
    sonda("channel_read.stock_fba_amazon (semilla del vigilante FBA)",
          lambda: channel_read.stock_fba_amazon())

    from services import competencia_captura, orders_write, stock_full
    sonda("orders_write.wc_order_id_previo (candado de idempotencia)",
          lambda: orders_write.wc_order_id_previo("0000000000"),
          lambda r: False)
    sonda("competencia_captura._nuestras_publicaciones",
          lambda: competencia_captura._nuestras_publicaciones())

    # ── Las dos sondas que hay que hacer CON DATO ───────────────────────────
    # Preguntar en seco no sirve: `_ya_procesada` de una operacion que no
    # existe devuelve False, y ese False es la respuesta CORRECTA. Una sonda que
    # no distingue "no existe el camino" de "la tabla esta vacia" comete el
    # mismo error que persigue — asi que aqui se SIEMBRA, se pregunta y se
    # limpia. Es la unica forma de que un candado se pueda sondear.
    from services import candados_read, tokens_read, meli as _m
    _OP = "SONDA-corte-total"
    _CTA = "SONDA-CUENTA"
    try:
        candados_read.marcar_aplicada(_OP, "SONDA-SKU", "AMAZON", "fba_ingreso")
        sonda("stock_full._ya_procesada (candado de bodega — PASO 0)",
              lambda: stock_full._ya_procesada(_OP),
              # Con la operacion YA sellada, la respuesta correcta es True.
              # False aqui significaria que el candado no recuerda: mercancia
              # movida dos veces.
              lambda r: r is not True)
        f = _m._fernet()
        tokens_read.guardar(_CTA, _m._enc(f, "SONDA-no-es-un-token"),
                            _m._enc(f, "SONDA-refresh"))
        sonda("meli._access_token (LOS TOKENS — paso 6)",
              lambda: _m._access_token(_CTA), lambda r: not r)
    finally:
        sdb.execute("delete from ops.fulfillment_operations where operacion_id=%s", (_OP,))
        sdb.execute("delete from ops.ml_tokens where cuenta=%s", (_CTA,))

    # ── Reporte ─────────────────────────────────────────────────────────────
    print(f"\n{'=' * 74}")
    orden = {"TRUENA": 0, "VACIO": 1, "PASA": 2}
    for nombre, estado, det in sorted(_RES, key=lambda x: orden[x[1]]):
        marca = {"PASA": "[PASA] ", "VACIO": "[VACIO]", "TRUENA": "[TRUENA]"}[estado]
        print(f"  {marca} {nombre:46s} {det}")
    print("=" * 74)

    n_pasa = sum(1 for r in _RES if r[1] == "PASA")
    n_vacio = sum(1 for r in _RES if r[1] == "VACIO")
    n_truena = sum(1 for r in _RES if r[1] == "TRUENA")
    print(f"\n  PASA {n_pasa}   ·   VACIO {n_vacio}   ·   TRUENA {n_truena}\n")
    if n_vacio:
        print("  Los VACIO son los caros: no avisan. Cada uno es un lugar donde el")
        print("  panel diria «no hay» en vez de «no pude preguntar».")
    print("\n  VEREDICTO SOBRE EL CORTE DE GOLPE:")
    if n_vacio == 0 and n_truena == 0:
        print("  Todo lo sondeado vive sin MySQL. Falta ampliar las sondas antes")
        print("  de afirmar que el corte completo es seguro.")
    else:
        print(f"  NO todavia: {n_vacio + n_truena} de {len(_RES)} caminos sondeados")
        print("  no sobreviven. La lista de arriba es el trabajo que falta, en orden.")


if __name__ == "__main__":
    main()
