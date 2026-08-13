"""Cierra una categoría RAÍZ completa: prende, captura, propone y mide.

Los cuatro pasos del flujo, en orden y con sus dependencias:

  1. PRENDER   los SKUs de la raíz (`activar_raiz`). Sin esto no existen para el
     módulo: `listar_skus()` filtra `WHERE activo` y todo lo demás parte de ahí.
  2. CAPTURAR  el top de la raíz y de cada subcategoría, con actor de Apify
     (~$0.007/página). Los términos de `/trends` entran gratis en la misma pasada.
  3. PROPONER  el término general de cada SKU con IA. Gratis.
  4. MEDIR     esos términos en el buscador, con Apify (~$0.007 cada uno).

REANUDABLE. Cada paso salta lo que ya está hecho, así que si se corta a media
corrida se relanza el mismo comando y sigue donde iba. Nada se paga dos veces:
los rankings ya capturados no se re-piden y los términos ya medidos tampoco.

VARIAS RAÍCES, UNA DETRÁS DE OTRA. No en paralelo: dos corridas simultáneas en la
misma cuenta de Apify hacen que una espere turno, y encima compiten por la cuota
de MySQL de donde sale el token de ML.

DEJARLO CORRIENDO SIN QUE LA MÁQUINA SE DUERMA
----------------------------------------------
Ya no hay navegador local —el raspado corre en Apify— así que la pantalla puede
apagarse; lo único que hay que evitar es que el SISTEMA se suspenda mientras
sondeamos:

    caffeinate -is backend/.venv/bin/python \\
        backend/scripts/competencia_cerrar_raiz.py MLM1648 MLM1500 MLM1132

`-i` evita el sleep por inactividad y `-s` el del sistema con corriente. No hace
falta `-d`: que se apague la pantalla no afecta.

USO
---
    # Ver qué haría y cuánto costaría, sin gastar
    ... competencia_cerrar_raiz.py MLM1648 --dry-run

    # Cerrar varias, una tras otra
    ... competencia_cerrar_raiz.py MLM1648 MLM1500 MLM1132

    # Saltarse la medición de términos (el paso caro)
    ... competencia_cerrar_raiz.py MLM1648 --sin-medir
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path

RAIZ_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ_DIR.parent))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from services import competencia_supabase, supabase_db  # noqa: E402

PY = sys.executable
COSTO_PAGINA = 0.007


def corre(script: str, *args: str) -> int:
    """Lanza uno de los scripts del módulo y deja su salida a la vista."""
    cmd = [PY, str(RAIZ_DIR / script), *args]
    print(f"\n$ {' '.join(cmd[1:])}", flush=True)
    return subprocess.run(cmd).returncode


def estado(raiz: str) -> dict:
    return supabase_db.fetch_one("""
     with s as (select * from enrich.market_skus_v where raiz_id=%s),
          a as (select * from s where activo),
          -- Las subcategorías se cuentan sobre TODOS los SKUs de la raíz, no
          -- solo los activos: el paso 1 los va a prender, así que estimar sobre
          -- los activos daría 0 páginas por capturar en una raíz virgen.
          sub as (select distinct categoria_id from s)
     select coalesce((select max(raiz_nombre) from s), %s) nom,
       (select count(*) from s) skus,
       (select count(*) from a) activos,
       (select count(*) from sub) subs,
       (select count(*) from enrich.market_bestsellers
         where categoria_id=%s and nivel='raiz') raiz,
       (select count(*) from sub where exists (select 1 from enrich.market_bestsellers b
           where b.categoria_id=sub.categoria_id)) con_top,
       (select count(*) from sub join enrich.market_terms t using (categoria_id)) con_kw,
       (select count(*) from s where termino_general is not null) con_term,
       (select count(distinct s.sku) from s
          join enrich.market_sku_config c on c.sku=s.sku and c.canal=s.canal
          join enrich.market_search_term st on st.id=c.termino_id
         where st.medido_en is not null) medidos
    """, (raiz, raiz, raiz))


def pinta(e: dict, titulo: str) -> None:
    print(f"\n  ── {titulo}")
    print(f"     SKUs {e['activos']}/{e['skus']} activos · top raíz {e['raiz']} · "
          f"subcats con top {e['con_top']}/{e['subs']} · keywords {e['con_kw']}/{e['subs']}")
    print(f"     términos {e['con_term']}/{e['skus']} propuestos · "
          f"{e['medidos']}/{e['skus']} medidos")


async def cerrar(raiz: str, args) -> None:
    e0 = estado(raiz)
    if not e0 or not e0["skus"]:
        print(f"\n═══ {raiz}: no hay SKUs bajo esa raíz. Se salta.")
        return

    print(f"\n{'═'*74}\n═══ {e0['nom']} ({raiz})\n{'═'*74}")
    pinta(e0, "antes")

    faltan_pag = e0["subs"] - e0["con_top"] + (0 if e0["raiz"] else 1)
    # Igual con los términos: se van a medir los de TODOS los SKUs de la raíz.
    faltan_term = max(0, e0["skus"] - e0["medidos"])
    print(f"\n     falta capturar : {faltan_pag} páginas  ≈ ${faltan_pag * COSTO_PAGINA:.2f}")
    print(f"     falta medir    : ~{int(faltan_term/1.2)} términos ≈ "
          f"${faltan_term/1.2 * COSTO_PAGINA:.2f}")
    if args.dry_run:
        print("\n     [dry-run] no se ejecutó nada.")
        return

    t0 = time.time()
    # 1. Prender
    n = competencia_supabase.activar_raiz(raiz)
    print(f"\n  1/4 activar_raiz → {n} SKUs prendidos"
          + ("  (ya estaban)" if not n else ""))

    # 2. Capturar: la RAÍZ primero, porque es la que da los nichos.
    if not e0["raiz"]:
        corre("competencia_rankings_apify.py", "--raiz", raiz, "--solo", raiz, "--execute")
    corre("competencia_rankings_apify.py", "--raiz", raiz, "--execute")

    # 3. Proponer términos (gratis)
    corre("competencia_proponer_terminos.py", "--raiz", raiz)

    # 4. Medir (lo caro). `--sin-titulo`: solo búsqueda general, decisión vieja.
    if not args.sin_medir:
        corre("competencia_buscar_apify.py", "--raiz", raiz, "--sin-titulo", "--execute")

    e1 = estado(raiz)
    pinta(e1, f"después · {time.time()-t0:.0f}s")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("raices", nargs="+", help="Ids de raíz (p. ej. MLM1648 MLM1500)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sin-medir", action="store_true",
                    help="Captura y propone, pero no mide los términos en Apify.")
    args = ap.parse_args()

    if not supabase_db.disponible():
        print("✗ Falta SUPABASE_DB_URL.")
        return 2

    raices = [r.strip().upper() for r in args.raices]
    t0 = time.time()
    for i, raiz in enumerate(raices, 1):
        print(f"\n\n########  {i}/{len(raices)}  ########")
        try:
            await cerrar(raiz, args)
        except KeyboardInterrupt:
            print("\n\nInterrumpido. Relanza el MISMO comando: cada paso salta lo "
                  "que ya está hecho y nada se paga dos veces.")
            return 130
        except Exception as exc:  # noqa: BLE001
            print(f"\n  ! {raiz} falló: {type(exc).__name__}: {exc}")
            print("    Sigo con la siguiente raíz.")
    print(f"\n\n{'═'*74}\nLISTO: {len(raices)} raíces en {(time.time()-t0)/60:.0f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
