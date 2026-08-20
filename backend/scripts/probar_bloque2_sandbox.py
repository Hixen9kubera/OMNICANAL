"""
probar_bloque2_sandbox.py — Las REJILLAS de ML y Amazon sin MySQL.

Mismo método que `probar_bloque1_sandbox.py`, y por la misma razón: el sandbox
corre con `MYSQL_ENABLED=false`, o sea que **ya es el mundo de después del
retiro del esquema**. Las dos corridas lado a lado dicen qué pasa hoy y qué pasa
con el repunte.

QUÉ ES DISTINTO DEL BLOQUE 1
----------------------------
El bloque 1 eran preguntas puntuales ("¿está publicado este SKU?"). Aquí lo que
se repunta es la TABLA PAGINADA del panel, así que no basta con que conteste:
tiene que contestar con la MISMA FORMA, respetar los filtros, el orden y el
conteo. Una rejilla que devuelve las filas correctas pero pagina mal está rota
igual.

Por eso las pruebas de abajo no miran solo "¿trajo algo?" sino:

  · la forma de la fila (las llaves que consume el frontend)
  · que `total` no dependa de la página
  · que la búsqueda filtre DE VERDAD (y no devuelva lo mismo que sin filtrar)
  · que el orden por precio salga ordenado
  · que `solo_publicados` sea un subconjunto del total
  · que dos páginas seguidas no repitan filas

Uso:
  ...python backend/scripts/probar_bloque2_sandbox.py
"""
from __future__ import annotations

import os
import sys
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
os.environ["SUPABASE_READ_CHANNEL"] = "true"
os.environ["APP_ENV"] = "staging"

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")

from services import amazon, meli  # noqa: E402

_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def con_bandera(valor: bool, fn, **k):
    previo = settings.supabase_read_publicaciones
    settings.supabase_read_publicaciones = valor
    try:
        return fn(**k), None
    except Exception as exc:  # noqa: BLE001
        return None, exc
    finally:
        settings.supabase_read_publicaciones = previo


_LLAVES = {"sku", "wc_id", "odoo_id", "nombre", "precio", "precio_base",
           "stock", "estado", "categoria_id", "categoria_path", "publicado",
           "item_id", "url", "cuenta", "full", "full_label", "origen"}


def probar(nombre: str, listar, contar, llaves: set, **extra) -> None:
    print(f"\n{'═' * 70}\n  {nombre}\n{'═' * 70}")

    # ── el mundo de hoy, sin MySQL ──────────────────────────────────────────
    (viejo, e_v) = con_bandera(False, listar, page=1, per_page=10, **extra)
    v_items, v_total = viejo if e_v is None else ([], 0)
    print(f"  bandera APAGADA -> {len(v_items)} filas, total={v_total}"
          + (f"  EXCEPCION {e_v}" if e_v else ""))
    check("apagada sin MySQL: la rejilla sale VACIA", not v_items and not v_total,
          "el try/except la convierte en «no hay publicaciones»")

    # ── con el repunte ──────────────────────────────────────────────────────
    (nuevo, e_n) = con_bandera(True, listar, page=1, per_page=10, **extra)
    if e_n is not None:
        check("prendida: la rejilla responde", False, str(e_n)[:160])
        return
    items, total = nuevo
    print(f"  bandera PRENDIDA-> {len(items)} filas, total={total}")
    check("prendida: la rejilla responde", bool(items), f"{len(items)} filas")
    if not items:
        return

    check("la fila trae las llaves que consume el frontend",
          set(items[0]) == llaves,
          f"sobran {set(items[0]) - llaves} · faltan {llaves - set(items[0])}")
    con_nombre = sum(1 for i in items if i["nombre"] != i["sku"])
    check("los nombres llegan (no el SKU pelon)", con_nombre >= len(items) * 0.8,
          f"{con_nombre}/{len(items)} con nombre real")

    # ── el total no puede depender de la pagina ─────────────────────────────
    (p2, _) = con_bandera(True, listar, page=2, per_page=10, **extra)
    check("el total es el mismo en la pagina 2", p2 and p2[1] == total,
          f"pag1={total} pag2={p2[1] if p2 else '?'}")
    if p2 and p2[0]:
        repetidos = {i["sku"] for i in items} & {i["sku"] for i in p2[0]}
        check("la pagina 2 no repite filas de la 1", not repetidos,
              f"{len(repetidos)} repetidos: {list(repetidos)[:3]}")

    # ── la busqueda tiene que FILTRAR ───────────────────────────────────────
    termino = items[0]["sku"][:8]
    (bus, e_b) = con_bandera(True, listar, page=1, per_page=10,
                             search=termino, **extra)
    check("la busqueda filtra de verdad",
          e_b is None and bus[1] < total and bus[1] > 0,
          f"«{termino}» -> {bus[1] if e_b is None else e_b} de {total}")

    # ── solo_publicados es un subconjunto ───────────────────────────────────
    (pub, e_p) = con_bandera(True, listar, page=1, per_page=10,
                             solo_publicados=True, **extra)
    check("solo_publicados es subconjunto del total",
          e_p is None and 0 < pub[1] <= total,
          f"{pub[1] if e_p is None else e_p} de {total}")
    if e_p is None and pub[0]:
        check("y todas las filas salen como publicadas",
              all(i["publicado"] for i in pub[0]))

    # ── el orden por precio tiene que salir ordenado ────────────────────────
    (ord_, e_o) = con_bandera(True, listar, page=1, per_page=15,
                              orden="precio_desc", **extra)
    if e_o is None and ord_[0]:
        precios = [i["precio"] for i in ord_[0] if i["precio"] is not None]
        check("orden=precio_desc sale de mayor a menor",
              precios == sorted(precios, reverse=True),
              f"{precios[:4]}")
    else:
        check("orden=precio_desc responde", False, str(e_o))

    # ── el conteo de publicados ─────────────────────────────────────────────
    (c_v, _) = con_bandera(False, contar)
    (c_n, e_c) = con_bandera(True, contar)
    print(f"  contar_publicados: apagada -> {c_v}   prendida -> {c_n}")
    check("contar_publicados apagada sin MySQL: 0", c_v == 0)
    check("contar_publicados prendida: cuenta", e_c is None and c_n > 0, str(e_c))
    check("y no cuenta mas de lo que hay en la rejilla", e_c is None and c_n <= total,
          f"{c_n} publicados de {total} filas")


def main() -> None:
    print(f"SANDBOX {_ref[:8]}... · MYSQL_ENABLED={settings.mysql_enabled}")
    probar("MERCADO LIBRE — meli.listar / contar_publicados (4 sitios)",
           meli.listar, meli.contar_publicados, _LLAVES)
    llaves_amz = set(amazon._normalizar({"sku": "X"}))
    probar("AMAZON — amazon.listar / contar_publicados (3 sitios)",
           amazon.listar, amazon.contar_publicados, llaves_amz)

    print("\n── ¿alguien intento abrir MySQL? ──")
    from services import db
    check("el pool de MySQL nunca se creo", db._pool is None)

    print(f"\nRESULTADO: {'el bloque 2 sobrevive sin MySQL' if _ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
