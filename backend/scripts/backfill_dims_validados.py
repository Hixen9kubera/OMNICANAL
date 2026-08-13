"""
backfill_dims_validados.py — Rescata a `costing.costos_validados` las dims que
solo viven en el MySQL de `costos_finales`, para poder retirar el respaldo de
`costos._preparar_base` (paso 0 del desmantelamiento, 12-ago-2026).

DOS OPERACIONES, ambas solo con dims:
  · RELLENO de filas existentes cuyas dims están en NULL.
  · ALTA de filas para los SKUs cuyo costo YA vive en costing.costos_finales
    (con costo_unitario y precio_sugerido) pero que no tienen fila en
    validados. A esos no les falta el costo: les faltan las dimensiones,
    porque el modelo v4 no puso esas columnas en `costos_finales`.

POR QUÉ UNA FILA "SOLO DIMS" ES SEGURA (verificado en los dos llamadores):
  · `asegurar_finales` corta antes de llegar: esos SKUs tienen precio_sugerido
    en kubera y retorna ahí. Y si llegara, su guarda `costo_unitario <= 0` no
    calcula nada.
  · `_preparar_base` toma las dims de validados y el COSTO de `cf`
    (costing.costos_finales), que sí está poblado.
Sin esas dos verificaciones, una fila con dims y sin costo haría que
`costo_desde_validados` devolviera costo_total = 0 — un "cuesta cero" es peor
que un "no sé".

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

    with pg.cursor() as c:
        c.execute("""select lower(sku::text) from costing.costos_finales
                      where costo_unitario is not null and precio_sugerido is not null""")
        con_costo_kb = {r[0] for r in c.fetchall()}

    plan, altas, densos, completos, sin_costo = [], [], [], 0, 0
    for sku, o in origen.items():
        litros0 = (float(o["largo"]) * float(o["ancho"]) * float(o["alto"])) / 1000.0
        dens0 = float(o["peso"]) / litros0 if litros0 > 0 else 9999
        d = destino.get(sku)
        if d is None:
            # sin fila en validados: solo se da de alta si su costo YA está en
            # kubera (si no, la fila nueva sería un "cuesta cero" fabricado)
            if sku not in con_costo_kb:
                sin_costo += 1
                continue
            if dens0 > DENSIDAD_MAX:
                densos.append((sku, round(dens0, 1)))
                continue
            altas.append({"sku": o["sku"], **{k: float(o[k]) for k in CAMPOS},
                          "densidad": round(dens0, 2)})
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
    print(f"  sin costo en kubera (NO se dan de alta): {sin_costo}")
    print(f"  descartados por densidad > {DENSIDAD_MAX} kg/L : {len(densos)}")
    print(f"  >>> A RELLENAR (filas existentes)    : {len(plan)}")
    print(f"  >>> A DAR DE ALTA (solo dims)        : {len(altas)}\n")
    for p in (plan + altas)[:10]:
        print(f"    {p['sku']:22s} {p['largo']}x{p['ancho']}x{p['alto']} cm · "
              f"{p['peso']} kg  ({p['densidad']} kg/L)  "
              f"{'relleno ' + str(p['campos']) if 'campos' in p else 'ALTA'}")
    if densos:
        print("\n  descartados más extremos:")
        for sku, dens in sorted(densos, key=lambda x: -x[1])[:5]:
            print(f"    {sku:22s} {dens} kg/L")

    if not args.real:
        print("\n== DRY-RUN: no se escribió nada ==")
        pg.close()
        return

    with pg.cursor() as c:
        c.execute("select set_config('app.via', 'backfill_dims', true)")
        for p in plan:
            sets = ", ".join(f"{k} = coalesce({k}, %s)" for k in CAMPOS)
            c.execute(f"update costing.costos_validados set {sets} where sku = %s",
                      tuple(p[k] for k in CAMPOS) + (p["sku"],))
        for a in altas:
            cols = ", ".join(CAMPOS)
            ph = ", ".join(["%s"] * len(CAMPOS))
            c.execute(
                f"insert into costing.costos_validados (sku, {cols}) "
                f"values (%s, {ph}) on conflict (sku) do nothing",
                (a["sku"],) + tuple(a[k] for k in CAMPOS))
    pg.commit()
    print(f"\n== APLICADO: {len(plan)} rellenada(s) · {len(altas)} alta(s) ==")
    print(json.dumps({"rellenadas": len(plan), "altas": len(altas),
                      "sin_costo_en_kubera": sin_costo,
                      "descartados_densidad": len(densos)}, ensure_ascii=False))
    pg.close()


if __name__ == "__main__":
    main()
