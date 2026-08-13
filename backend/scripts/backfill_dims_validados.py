"""
backfill_dims_validados.py — Rescata a `costing.costos_validados` las dims que
solo viven en el MySQL de `costos_finales`, para poder retirar el respaldo de
`costos._preparar_base` (paso 0 del desmantelamiento, 12-ago-2026).

ALCANCE DELIBERADAMENTE ESTRECHO — solo rellena NULOS en filas QUE YA EXISTEN.

De los 514 SKUs con dims solo en MySQL:
  · 474 NO tienen fila en costing.costos_validados. NO se insertan: una fila
    con dims y sin costo hace que `costo_desde_validados` devuelva
    costo_total = 0, y un "cuesta cero" es peor que un "no sé" — es justo la
    clase de error que este paso 0 está corrigiendo.
  ·  40 sí tienen fila con las dims en NULL. Esas se rellenan.

GUARDA DE PLAUSIBILIDAD: se descarta toda medida <= 0 y toda densidad mayor a
1.5 kg/L. El 14% de los candidatos trae peso de CAJA MASTER capturado como
pieza (mue-0064: 12x10x10 cm y 224 kg = 185 kg/L). Copiar eso infla el flete
por volumen; el dato malo se queda fuera y el SKU se recaptura a mano.

Nunca pisa un valor existente: el UPDATE solo toca columnas en NULL.

Uso:
  ...python backend/scripts/backfill_dims_validados.py               # dry-run
  ...python backend/scripts/backfill_dims_validados.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DENSIDAD_MAX = 1.5  # kg/L — arriba de esto es peso de caja, no de pieza
CAMPOS = ("largo", "alto", "ancho", "peso")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="escribe (default: dry-run)")
    ap.add_argument("--acepto-destino", default="",
                    help="primeros 8 chars de la ref destino (obligatorio con --real)")
    args = ap.parse_args()

    E = cargar(".env")
    ref = (re.search(r"postgres\.([a-z0-9]+):", E["SUPABASE_DB_URL"]) or [None, ""])[1]
    if args.real and args.acepto_destino != ref[:8]:
        sys.exit(f"ABORT: con --real hay que nombrar el destino "
                 f"(--acepto-destino {ref[:8]}).")
    print(f"[{'REAL' if args.real else 'DRY-RUN'}] destino: {ref[:8]}…\n", flush=True)

    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    with my.cursor() as c:
        c.execute("""SELECT sku, largo, alto, ancho, peso FROM costos_finales
                      WHERE largo > 0 AND alto > 0 AND ancho > 0 AND peso > 0""")
        origen = {str(r["sku"]).lower(): r for r in c.fetchall()}
    my.close()

    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)
    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute("""select sku, largo, alto, ancho, peso
                       from costing.costos_validados""")
        destino = {str(r["sku"]).lower(): r for r in c.fetchall()}

    plan, sin_fila, densos, completos = [], 0, [], 0
    for sku, o in origen.items():
        d = destino.get(sku)
        if d is None:
            sin_fila += 1
            continue
        faltantes = [k for k in CAMPOS if d.get(k) is None]
        if not faltantes:
            completos += 1
            continue
        litros = (float(o["largo"]) * float(o["ancho"]) * float(o["alto"])) / 1000.0
        dens = float(o["peso"]) / litros if litros > 0 else 9999
        if dens > DENSIDAD_MAX:
            densos.append((sku, round(dens, 1)))
            continue
        plan.append({"sku": d["sku"], "campos": faltantes,
                     **{k: float(o[k]) for k in CAMPOS}, "densidad": round(dens, 2)})

    print(f"  candidatos en MySQL con dims         : {len(origen)}")
    print(f"  ya completos en kubera               : {completos}")
    print(f"  SIN fila en kubera (no se insertan)  : {sin_fila}")
    print(f"  descartados por densidad > {DENSIDAD_MAX} kg/L : {len(densos)}")
    print(f"  >>> A RELLENAR                       : {len(plan)}\n")
    for p in plan[:10]:
        print(f"    {p['sku']:22s} {p['largo']}x{p['ancho']}x{p['alto']} cm · "
              f"{p['peso']} kg  ({p['densidad']} kg/L)  campos={p['campos']}")
    if densos:
        print("\n  descartados más extremos:")
        for sku, dens in sorted(densos, key=lambda x: -x[1])[:5]:
            print(f"    {sku:22s} {dens} kg/L")

    if not args.real:
        print("\n== DRY-RUN: no se escribió nada ==")
        pg.close()
        return

    if plan:
        with pg.cursor() as c:
            c.execute("select set_config('app.via', 'backfill_dims', true)")
            for p in plan:
                sets = ", ".join(f"{k} = coalesce({k}, %s)" for k in CAMPOS)
                c.execute(f"update costing.costos_validados set {sets} where sku = %s",
                          tuple(p[k] for k in CAMPOS) + (p["sku"],))
        pg.commit()
    print(f"\n== APLICADO: {len(plan)} fila(s) rellenada(s) ==")
    print(json.dumps({"rellenadas": len(plan), "sin_fila": sin_fila,
                      "descartados_densidad": len(densos)}, ensure_ascii=False))
    pg.close()


if __name__ == "__main__":
    main()
