"""
probar_bloque34_sandbox.py — Competencia (4 sitios) e Inventario (8) sin MySQL.

Mismo método que los bloques 1 y 2: el sandbox corre con `MYSQL_ENABLED=false`,
así que ya es el mundo de después del retiro.

POR QUÉ ESTE ES DISTINTO
------------------------
`inventario` es el único bloque cuyas lecturas deciden **a qué publicaciones se
les va a escribir stock**. Un hueco en los bloques anteriores se ve en una
pantalla; aquí se ve en la bodega. Por eso las pruebas de abajo no solo miran
que conteste: comprueban que el universo no se INFLE ni se ENCOJA de forma que
cambie a quién se visita.

Y hay una traducción que había que verificar, no suponer: en MySQL el universo
salía de `success = 1` ("nació bien", un hecho congelado) y aquí sale del estado
de hoy, excluyendo `closed`. Se mide cuánto cambia eso.

Uso:
  ...python backend/scripts/probar_bloque34_sandbox.py
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

from services import channel_read, competencia_captura, inventario  # noqa: E402

_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def con_bandera(valor: bool, fn, *a, **k):
    previo = settings.supabase_read_publicaciones
    settings.supabase_read_publicaciones = valor
    try:
        return fn(*a, **k), None
    except Exception as exc:  # noqa: BLE001
        return None, exc
    finally:
        settings.supabase_read_publicaciones = previo


def main() -> None:
    print(f"SANDBOX {_ref[:8]}... · MYSQL_ENABLED={settings.mysql_enabled}")
    from services import supabase_db as sdb
    skus = [f["sku"] for f in sdb.fetch_all(
        """select sku::text as sku from channel.listings
            where canal='mercado_libre' and nullif(listing_id,'') is not null
            group by 1 order by 1 limit 5""")]

    # ══ BLOQUE 3 — Competencia ══════════════════════════════════════════════
    print(f"\n{'═' * 70}\n  BLOQUE 3 — Competencia (4 sitios)\n{'═' * 70}")
    v, e_v = con_bandera(False, competencia_captura._nuestras_publicaciones)
    n, e_n = con_bandera(True, competencia_captura._nuestras_publicaciones)
    print(f"  _nuestras_publicaciones: apagada -> {len(v or {})}   prendida -> {len(n or {})}")
    check("apagada sin MySQL: vacio (toda la competencia pareceria ajena)",
          e_v is None and not v)
    check("prendida: el indice invertido responde", e_n is None and len(n) > 100,
          f"{len(n or {})} publicaciones" if e_n is None else str(e_n))
    if n:
        una = next(iter(n.values()))
        check("y cada entrada trae sku y cuenta", set(una) == {"sku", "cuenta"},
              str(una))
        # Un item cerrado SIGUE siendo nuestro: si se filtrara por situacion,
        # apareceriamos como competencia de nosotros mismos.
        cerradas = sdb.fetch_all(
            """select l.listing_id as i from channel.listings l
                where l.canal='mercado_libre'
                  and lower(coalesce(l.situacion,'')) = 'closed'
                  and nullif(l.listing_id,'') is not null limit 3""")
        if cerradas:
            faltan = [c["i"] for c in cerradas if c["i"] not in n]
            check("las publicaciones CERRADAS siguen contando como nuestras",
                  not faltan, f"faltan {faltan}")

    vivas, e = con_bandera(True, channel_read.publicaciones_ml_vivas, skus)
    todas, _ = con_bandera(True, channel_read.publicaciones_ml, skus)
    check("publicaciones_ml_vivas es subconjunto de publicaciones_ml",
          e is None and sum(len(x) for x in vivas.values())
          <= sum(len(x) for x in todas.values()),
          f"vivas {sum(len(x) for x in vivas.values())} de "
          f"{sum(len(x) for x in todas.values())}")

    # ══ BLOQUE 4 — Inventario ═══════════════════════════════════════════════
    print(f"\n{'═' * 70}\n  BLOQUE 4 — Inventario (8 sitios) — el que mueve stock\n{'═' * 70}")

    for cuenta in ("BEKURA", "SANCORFASHION"):
        u, e = con_bandera(True, channel_read.universo_ml, cuenta)
        r, e2 = con_bandera(True, channel_read.respaldo_identidad_ml, cuenta)
        print(f"  {cuenta:15s} universo {len(u or [])} · respaldo {len(r or {})}")
        check(f"universo_ml({cuenta}) responde", e is None and bool(u), str(e))
        check(f"  y ninguna fila viene sin item_id",
              e is None and all(x["ml_item_id"] for x in u))
        # OJO con el chequeo ingenuo: el universo es una LISTA de filas y el
        # respaldo un DICCIONARIO por item_id. Compararlos por largo mide las
        # COLISIONES, no la cobertura. La cobertura se mide por item_id.
        ids_u = {x["ml_item_id"] for x in (u or [])}
        check("  el respaldo cubre a todo el universo (por item_id)",
              e2 is None and ids_u <= set(r or {}),
              f"{len(ids_u - set(r or {}))} item_id del universo sin respaldo")
        colisiones = len(u or []) - len(ids_u)
        check("  y se sabe cuantos item_id estan duplicados", True,
              f"{colisiones} filas comparten item_id con otra "
              f"(SKU padre vs variante — ver la nota en respaldo_identidad_ml)")

    ua, e = con_bandera(True, channel_read.universo_amazon)
    check("universo_amazon responde", e is None and bool(ua),
          f"{len(ua or [])} publicaciones")
    check("  y todas traen sku", e is None and all(x["sku"] for x in ua))

    # El dueño de un item: la pregunta del webhook de ML.
    if skus:
        pub = channel_read.publicaciones_ml(skus[:1]).get(skus[0], [])
        if pub:
            item = pub[0]["item_id"]
            d, e = con_bandera(True, channel_read.dueno_de_item_ml, item)
            check("dueno_de_item_ml encuentra al dueño", e is None and d
                  and d["sku"].lower() == skus[0].lower(), f"{item} -> {d}")
    d, e = con_bandera(True, channel_read.dueno_de_item_ml, "MLM0000000000")
    check("y devuelve None con un item que no existe (no truena)",
          e is None and d is None, str(e) if e else str(d))

    ex, e = con_bandera(True, channel_read.existe_en_amazon, skus[0] if skus else "X")
    check("existe_en_amazon responde un booleano", e is None and isinstance(ex, bool),
          f"{skus[0] if skus else '?'} -> {ex}")

    # Los dos caminos completos de inventario, sin MySQL.
    v, e_v = con_bandera(False, inventario.leer_inventario, skus)
    n, e_n = con_bandera(True, inventario.leer_inventario, skus)
    check("inventario.leer_inventario sigue respondiendo con la bandera prendida",
          e_n is None and bool(n), str(e_n))
    check("  y ve lo mismo o mas que con la bandera apagada",
          e_v is None and e_n is None and len(n) >= len(v),
          f"apagada {len(v or [])} · prendida {len(n or [])}")

    print("\n── ¿alguien intento abrir MySQL? ──")
    from services import db
    check("el pool de MySQL nunca se creo", db._pool is None)

    print(f"\nRESULTADO: {'bloques 3 y 4 sobreviven sin MySQL' if _ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
