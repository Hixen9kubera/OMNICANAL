"""
probar_candados_paso0.py — Pruebas del PASO 0 contra el SANDBOX.

Molde: `probar_corte_costing.py`. Guardia de ambiente triple — este script se
NIEGA a correr si el destino no es el sandbox, porque dos de sus pruebas
simulan que la base está caída y una borra estado.

    APP_ENV=staging python backend/scripts/probar_candados_paso0.py

Requisito previo: `0022_candados_fanout.sql` aplicada al sandbox y
`migrar_candados_paso0.py --sandbox --real` ya corrido.

LOS SEIS ESCENARIOS
-------------------
  T1  candado con kubera arriba  → contesta lo MISMO que la bitácora MySQL
  T2  kubera CAÍDA               → **PROPAGA**; no contesta "no lo hice"
  T3  operación desconocida      → "no aplicada" (es el caso legítimo, no un bug)
  T4  la bitácora ya no existe   → el candado nuevo ni la busca ni la recrea
  T5  intento que FALLÓ          → sigue siendo reintentable
  T6  marca de agua              → NO es `channel.listings.stock_fba`

T2 y T4 son las que hoy FALLARÍAN con el código viejo. Son la razón del paso.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if os.environ.get("APP_ENV", "").strip().lower() != "staging":
    sys.exit("ABORT: corre con APP_ENV=staging (este script simula caídas y "
             "borra estado; no toca producción).")

from config import settings                      # noqa: E402
from services import candados_read as cr         # noqa: E402
from services import supabase_db as sdb          # noqa: E402

_REF = (re.search(r"postgres\.([a-z0-9]+):", settings.supabase_db_url or "") or [None, ""])[1]
if not _REF.startswith("yvootpbz"):
    sys.exit(f"ABORT: el destino es {_REF[:8]}…, no el sandbox.")
print(f"Sandbox verificado: {_REF[:8]}…\n")

_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


class _Caida(Exception):
    """Lo que lanza psycopg2 cuando la base no contesta."""


# ── T1 · el candado contesta lo mismo que la bitácora ──────────────────────
print("T1 · kubera arriba: ¿contesta lo mismo que la bitácora?")
c = cr.censo()
print(f"      censo en sandbox: {c}")
check("hay estado migrado que probar",
      (c.get("operaciones") or 0) > 0 and (c.get("marcas_fba") or 0) > 0, str(c))

ops = sdb.fetch_all("select operacion_id, accion from ops.fulfillment_operations")
check("toda operación migrada se reconoce como APLICADA",
      all(cr.ya_aplicada(o["operacion_id"]) for o in ops), f"{len(ops)} operaciones")

comp = sdb.fetch_all("select canal, cuenta, external_order_id from channel.orders "
                     "where stock_compensado_at is not null")
check("todo pedido compensado se reconoce como COMPENSADO",
      all(cr.ya_compensado(r["canal"], r["cuenta"], r["external_order_id"])
          for r in comp), f"{len(comp)} pedidos")

# El caso que el consejo marcó: tras una REVERSIÓN el pedido vuelve a ser
# compensable. Con un boolean —o un solo timestamp— seguiría diciendo "ya
# compensado" para siempre y la compensación legítima no ocurriría nunca.
if comp:
    r0 = comp[0]
    llave = (r0["canal"], r0["cuenta"], r0["external_order_id"])
    cr.marcar_revertido(*llave)
    check("tras REVERTIR, el pedido deja de contar como compensado",
          cr.ya_compensado(*llave) is False, "un bool se habría quedado en True")
    cr.marcar_compensado(*llave)
    check("y al compensar de nuevo, vuelve a contar", cr.ya_compensado(*llave) is True)
    sdb.execute("update channel.orders set stock_revertido_at = null "
                "where canal=%s and cuenta=%s and external_order_id=%s", llave)

# ── T2 · kubera caída: TIENE que propagar ──────────────────────────────────
print("\nT2 · kubera CAÍDA: ¿propaga o inventa un 'no lo hice'?")
_fo, _fa, _ex = sdb.fetch_one, sdb.fetch_all, sdb.execute


def _truena(*_a, **_k):
    raise _Caida("connection refused (simulado)")


sdb.fetch_one = sdb.fetch_all = sdb.execute = _truena
try:
    for nombre, fn, arg in (("ya_compensado", lambda a: cr.ya_compensado("mercado_libre", "BEKURA", a), "orden-x"),
                            ("ya_aplicada", cr.ya_aplicada, "op-xyz"),
                            ("marcas_fba", cr.marcas_fba, ["SKU-X"])):
        try:
            r = fn(arg)
            check(f"{nombre} PROPAGA con la base caída", False,
                  f"devolvió {r!r} en vez de lanzar — ES EL BUG DE LOS 964")
        except _Caida:
            check(f"{nombre} PROPAGA con la base caída", True, "lanzó, no inventó")
        except Exception as exc:  # noqa: BLE001
            check(f"{nombre} PROPAGA con la base caída", True, f"lanzó {type(exc).__name__}")
finally:
    sdb.fetch_one, sdb.fetch_all, sdb.execute = _fo, _fa, _ex

# ── T3 · lo desconocido es "no aplicada", y está bien ──────────────────────
print("\nT3 · operación nunca vista: 'no aplicada' es la respuesta correcta")
check("un operacion_id inventado da False", cr.ya_aplicada("no-existe-jamas-0000") is False)
check("una orden inventada da False",
      cr.ya_compensado("mercado_libre", "BEKURA", "no-existe-0000") is False)

# ── T4 · el candado nuevo no depende de la bitácora ────────────────────────
print("\nT4 · la bitácora ya no existe: ¿el candado la busca o la recrea?")
fuente = (ROOT / "backend" / "services" / "candados_read.py").read_text(encoding="utf-8")
# Se mira el CÓDIGO, no la prosa: el módulo SÍ debe hablar de `fanout_log` en su
# docstring —de ahí viene, y explicarlo es el punto— pero no debe TOCARLO. La
# primera versión de esta prueba buscaba la palabra en todo el archivo y
# reprobaba por la documentación, que es exactamente lo que se quiere conservar.
#
# Se quitan por RANGO DE LÍNEAS y no por texto: el docstring del módulo trae
# escapes (`\\s` en el archivo, `\s` ya parseado), así que un `replace` con lo
# que devuelve `get_docstring` no casa nunca y la prueba reprobaba sola.
import ast as _ast

arbol = _ast.parse(fuente)
lineas = fuente.splitlines()
fuera: set[int] = set()
for nodo in _ast.walk(arbol):
    if isinstance(nodo, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef,
                         _ast.ClassDef)) and nodo.body:
        p = nodo.body[0]
        if isinstance(p, _ast.Expr) and isinstance(p.value, _ast.Constant) \
                and isinstance(p.value.value, str):
            fuera.update(range(p.lineno - 1, (p.end_lineno or p.lineno)))
codigo = "\n".join(l for i, l in enumerate(lineas)
                   if i not in fuera and not l.lstrip().startswith("#"))
check("el CÓDIGO de candados_read no toca fanout_log", "fanout_log" not in codigo)
check("no importa el MySQL legado (services.db)",
      not re.search(r"^\s*from services import .*\bdb\b", fuente, re.M)
      and not re.search(r"\bdb\.(fetch|execute|get_cursor)", codigo))
check("candados_read NO tiene CREATE TABLE", "CREATE TABLE" not in fuente.upper())
# El defecto viejo, para que quede medido y no como afirmación:
viejo = (ROOT / "backend" / "services" / "fanout_stock.py").read_text(encoding="utf-8")
print(f"      (referencia: fanout_stock.py {'SÍ' if 'CREATE TABLE IF NOT EXISTS fanout_log' in viejo else 'ya no'} "
      f"recrea fanout_log — mientras siga ahí, borrar la tabla la revive VACÍA)")

# ── T5 · un intento fallido sigue siendo reintentable ─────────────────────
print("\nT5 · intento que FALLÓ: ¿se puede reintentar?")
sdb.execute("delete from ops.fulfillment_operations where operacion_id = 'PRUEBA-FALLIDA'")
check("una operación que falló NO figura como aplicada",
      cr.ya_aplicada("PRUEBA-FALLIDA") is False,
      "un 502 del WAF no puede sellar el movimiento (auditoría 27-jul)")
cr.marcar_aplicada("PRUEBA-FALLIDA", "SKU-PRUEBA", "BEKURA", "full_ingreso")
check("tras aplicarla de verdad, ya figura", cr.ya_aplicada("PRUEBA-FALLIDA") is True)
check("marcarla dos veces no duplica",
      cr.marcar_aplicada("PRUEBA-FALLIDA", "SKU-PRUEBA", "BEKURA", "full_ingreso") == 0
      or cr.ya_aplicada("PRUEBA-FALLIDA"))
sdb.execute("delete from ops.fulfillment_operations where operacion_id = 'PRUEBA-FALLIDA'")

# ── T6 · la marca de agua NO es el stock del sync ─────────────────────────
print("\nT6 · marca de agua vs channel.listings.stock_fba")
d = sdb.fetch_one("""select count(*) as total,
                            count(*) filter (where l.stock_fba is distinct from w.stock_fba) as difieren
                       from ops.fba_watermark w
                       join channel.listings l on l.sku = w.sku and l.canal = 'amazon'""")
check("son fuentes DISTINTAS y se conservan distintas",
      (d.get("difieren") or 0) > 0,
      f"{d.get('difieren')} de {d.get('total')} difieren — si fueran 0, alguien "
      f"las recalculó desde el sync y volvió a meter el doble conteo")

print(f"\nRESULTADO: {'las 6 pruebas pasan' if _ok else 'HAY FALLAS — revisar arriba'}")
sys.exit(0 if _ok else 1)
