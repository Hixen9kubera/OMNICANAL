"""
indexar_ferraforme.py — Llena ``costing.packing_ubicaciones`` (0057): en qué
renglón del packing list ORIGINAL está cada SKU, según Ferraforme.

Para cada versión VIGENTE de Ferraforme en el bucket (la más reciente de cada
archivo de Drive):
  1. se lee su tabla (solo lectura, sin fotos);
  2. se buscan sus originales candidatos por ``contenedor_base`` (la versión
     vigente de cada original con el mismo código);
  3. se coteja fila por fila contra cada candidato y gana el que más filas
     alinea (ver :func:`packing_ferraforme.ubicar`);
  4. se REEMPLAZAN en la tabla las filas de ese archivo de Ferraforme.

Idempotente: con el bucket igual, re-correrlo deja la tabla igual. Va después
de ``copiar_packing_lists.py``: una versión nueva de un original deja viejas
sus ubicaciones (el backend las ignora porque la huella ya no coincide) hasta
que esto se vuelve a correr.

CANDADO: el mismo de la copia — no escribe en producción sin ``--produccion``.

Uso (desde la raíz del worktree, con env.staging):
  python backend/scripts/indexar_ferraforme.py --dry-run
  python backend/scripts/indexar_ferraforme.py [--filtro KOCU4642556]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(Path(__file__).resolve().parent)]
from services import packing_ferraforme as pf, packing_storage as st  # noqa: E402
import copiar_packing_lists as copia  # noqa: E402 — cargar_env y el candado de destino


def _codigos(nombre: str) -> set[str]:
    return {m[0] for m in copia.CODIGO.finditer((nombre or "").upper())}


def vigentes(pg) -> list[dict]:
    with pg, pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""select distinct on (tipo, drive_file_id, coalesce(miembro, ''))
                              tipo, drive_file_id, miembro, sha256, ruta, nombre, contenedor_base
                         from costing.packing_archivos
                        where drive_file_id is not null
                        order by tipo, drive_file_id, coalesce(miembro, ''),
                                 drive_modified_at desc nulls last, id desc""")
        return cur.fetchall()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--filtro", help="solo los Ferraforme cuyo nombre contenga esto")
    ap.add_argument("--dry-run", action="store_true", help="coteja y reporta, no escribe")
    ap.add_argument("--env", type=Path, default=ROOT / "env.staging")
    ap.add_argument("--produccion", action="store_true")
    a = ap.parse_args()

    url, llave, db, ref = copia.destino(copia.cargar_env(a.env), a.produccion)
    pg = psycopg2.connect(db, connect_timeout=20)
    todo = vigentes(pg)
    ferras = [v for v in todo if v["tipo"] == "ferraforme"]
    originales = [v for v in todo if v["tipo"] == "original"]
    if a.filtro:
        ferras = [f for f in ferras
                  if a.filtro.lower() in (f["nombre"] + (f["miembro"] or "")).lower()]
    print(f"Destino: {ref[:8]}… · {len(ferras)} Ferraforme · {len(originales)} originales"
          f"{' · DRY-RUN' if a.dry_run else ''}", flush=True)

    def tabla(v: dict) -> pf.Tabla:
        return pf.leer_tabla(st.bajar(v["ruta"], url=url, llave=llave))

    tot = {"skus": set(), "renglones": 0, "alineados": 0, "sin_par": 0, "fallos": 0}
    for i, fv in enumerate(ferras, 1):
        t0 = time.monotonic()
        nombre = fv["miembro"] or fv["nombre"]
        try:
            tf = tabla(fv)
            # Por CUALQUIER código compartido, no solo el más largo: el nombre
            # "256059868 TRHU6215242 contenedor 1" lleva BL y contenedor, y su
            # original se llama por el BL.
            codigos = _codigos(fv["nombre"]) | _codigos(fv["miembro"] or "")
            cands = [o for o in originales if _codigos(o["nombre"]) & codigos]
            mejor, ub, n_mejor = None, pf.ubicar(tf, None), -1
            for o in cands:                       # uno a la vez: cada tabla pesa
                u = pf.ubicar(tf, tabla(o))
                n = sum(x["alineado"] for x in u)
                if n > n_mejor:
                    mejor, ub, n_mejor = o, u, n
            alineados = sum(x["alineado"] for x in ub)
            if not a.dry_run:
                with pg, pg.cursor() as cur:
                    cur.execute("""delete from costing.packing_ubicaciones
                                    where ferraforme_file_id = %s
                                      and coalesce(ferraforme_miembro, '') = coalesce(%s, '')""",
                                (fv["drive_file_id"], fv["miembro"]))
                    psycopg2.extras.execute_values(cur, """
                        insert into costing.packing_ubicaciones
                            (sku, fuente_sku, contenedor_base, ferraforme_file_id, ferraforme_miembro,
                             ferraforme_sha256, ferraforme_fila, original_file_id,
                             original_sha256, original_fila, alineado, cotejo,
                             codigo_proveedor, texto)
                        values %s""", [
                        (x["sku"], x["fuente_sku"], fv["contenedor_base"], fv["drive_file_id"],
                         fv["miembro"],
                         fv["sha256"], x["ferraforme_fila"],
                         mejor["drive_file_id"] if mejor else None,
                         mejor["sha256"] if mejor else None,
                         x["original_fila"], x["alineado"], x["cotejo"],
                         x["codigo_proveedor"], x["texto"])
                        for x in ub])
            tot["renglones"] += len(ub)
            tot["alineados"] += alineados
            tot["skus"] |= {x["sku"] for x in ub if x["alineado"]}
            tot["sin_par"] += mejor is None
            pct = f"{100 * alineados / len(ub):.0f}%" if ub else "—"
            modo = next((x["cotejo"] for x in ub if x["cotejo"]), "—")
            print(f"[{i}/{len(ferras)}] {len(ub):5} renglones con SKU · {alineados:5} alineados "
                  f"({pct:>4}, {modo}) · {time.monotonic() - t0:5.1f} s · {nombre}"
                  f"\n        ↳ original: {mejor['nombre'] if mejor else '— sin par en el bucket'}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 — un archivo raro no para el índice
            tot["fallos"] += 1
            print(f"[{i}/{len(ferras)}] FALLO {type(exc).__name__}: {str(exc)[:200]} · {nombre}",
                  flush=True)
    pg.close()
    print(f"Resumen: {tot['renglones']} renglones con SKU · {tot['alineados']} alineados · "
          f"{len(tot['skus'])} SKUs ubicados · {tot['sin_par']} sin original en el bucket · "
          f"{tot['fallos']} fallos", flush=True)


if __name__ == "__main__":
    main()
