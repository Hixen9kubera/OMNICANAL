"""
backfill_costos_finales.py — Calcula el COSTO FINAL de los SKUs que ya tienen
costo validado y publicación viva, pero se quedaron sin fila en
`costing.costos_finales`. Sin costo final el panel no puede decir el margen:
sale "sin costo del canal" aunque la insignia diga VALIDADO.

POR QUÉ HACEN FALTA DOS COSAS (26-ago-2026)
  · La v0.276.0 arregla el CAUSANTE: `asegurar_finales` no buscaba la categoría
    de ML y se rendía teniendo el dato a la mano. Pero esa función sólo se llama
    AL CREAR un producto (`crear_producto.py`), así que arregla los nuevos.
  · Los que ya existen no los toca nadie. Para eso es este script.

MEDIDO ANTES DE ESCRIBIR NADA
  15,838 SKUs con costo validado · 4,420 con costo final · 11,491 sin él.
  De esos, 166 están PUBLICADOS Y ACTIVOS — los únicos que importan aquí, y los
  166 tienen su categoría guardada en `channel.product_category`.

DRY-RUN POR DEFAULT. Escribe sólo con --aplicar.

GUARDA DE PLAUSIBILIDAD: se SALTA (no se calcula) todo SKU con
`piezas_por_caja < 1`. Un divisor menor que uno no divide: multiplica el flete
—TEC-0393-ROS trae 0.53, casi el doble— y el costo saldría inflado con cara de
dato bueno. Esos se listan aparte para revisarlos a mano.

    python -m scripts.backfill_costos_finales            # simulacro
    python -m scripts.backfill_costos_finales --aplicar
"""
from __future__ import annotations

import argparse
import logging
import sys

log = logging.getLogger("omnicanal.backfill_costos_finales")

SQL_CANDIDATOS = """
    select distinct v.sku, v.piezas_por_caja
      from costing.costos_validados v
      join channel.listings l
        on l.sku = v.sku and l.situacion = 'active'
     where not exists (select 1 from costing.costos_finales f where f.sku = v.sku)
     order by v.sku
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true",
                    help="ESCRIBE en costing.costos_finales (sin esto, simulacro)")
    ap.add_argument("--limite", type=int, default=0, help="tope de SKUs a procesar")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from services import costos, supabase_db as sdb

    filas = sdb.fetch_all(SQL_CANDIDATOS)
    if args.limite:
        filas = filas[: args.limite]

    sospechosos = [f for f in filas
                   if f.get("piezas_por_caja") is not None
                   and float(f["piezas_por_caja"]) < 1]
    procesables = [f for f in filas if f not in sospechosos]

    print(f"candidatos            : {len(filas)}")
    print(f"  a calcular          : {len(procesables)}")
    print(f"  SALTADOS por pzs/caja < 1 : {len(sospechosos)}")
    for f in sospechosos[:15]:
        print(f"      {f['sku']:<24} piezas_por_caja={f['piezas_por_caja']}")
    if len(sospechosos) > 15:
        print(f"      … y {len(sospechosos) - 15} más")
    print(f"\n{'APLICANDO' if args.aplicar else 'SIMULACRO (no escribe)'}\n")

    ok = sin_cat = sin_costo = fallo = 0
    for f in procesables:
        sku = f["sku"]
        cat = costos._resolver_cat_ml(sku)
        if not cat:
            sin_cat += 1
            print(f"  {sku:<24} SIN CATEGORÍA — se salta")
            continue
        base = costos.costo_desde_validados(sku)
        if not base or base["costo_unitario"] <= 0:
            sin_costo += 1
            print(f"  {sku:<24} sin costo utilizable en validados")
            continue
        if not args.aplicar:
            print(f"  {sku:<24} calcularía con cat={cat} costo={base['costo_unitario']}")
            ok += 1
            continue
        try:
            fila = costos.asegurar_finales(sku, cat)
            if fila and fila.get("precio_sugerido"):
                ok += 1
                print(f"  {sku:<24} OK  cat={cat} psug={fila['precio_sugerido']}")
            else:
                fallo += 1
                print(f"  {sku:<24} no devolvió precio")
        except Exception as exc:  # noqa: BLE001
            fallo += 1
            print(f"  {sku:<24} ERROR {type(exc).__name__}: {str(exc)[:90]}")

    print(f"\nRESUMEN  calculados={ok}  sin_categoria={sin_cat}  "
          f"sin_costo={sin_costo}  fallos={fallo}  saltados={len(sospechosos)}")
    if not args.aplicar:
        print("Nada escrito. Para aplicar: --aplicar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
