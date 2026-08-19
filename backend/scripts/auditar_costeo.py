"""
auditar_costeo.py - Auditoria de calidad de datos de costing.costos_validados.

SOLO LECTURA. No corrige nada: detecta, mide en DINERO y ordena para que el
equipo lo arregle en el sistema de costeo.

Que revisa, y por que asi
-------------------------
El flete de importacion (costo_cbm) ya viene DENTRO del costo base
(costo_total = costo_producto + costo_cbm), asi que cada defecto de dimension o
peso es dinero directo.

La tarifa de flete es FIJA (7500 por m3) y no cambia por embarque. Eso vuelve el
flete una funcion PURA del volumen:

    flete_correcto = (largo x alto x ancho / 1e6) x 7500

y por lo tanto AUDITAR EL FLETE ES AUDITAR LAS DIMENSIONES POR PIEZA. No hay
tarifa que averiguar ni contenedor que consultar: cualquier costo_cbm que no
cuadre con esa formula viene de dims mal capturadas.

Verificado contra produccion (18-ago-2026):
  - Las dims son de la PIEZA, no de la caja master: contra el volumen por unidad
    que MIDE Amazon (ops.fba_snapshot.per_unit_volume, 757 SKUs) la razon
    mediana es 0.86x; dividir entre piezas_por_caja la deja en 0.03-0.09x. El
    error tipico es capturar la caja master como si fuera la pieza.
  - Solo 3,095 de 15,393 SKUs (20 pct) cumplen hoy la tarifa fija. 6,940 (45 pct)
    estan por debajo del 10 pct de ella, casi siempre porque el flete se dividio
    entre piezas_por_caja al cargarlo. El bloque 3 mide ese cumplimiento.
  - Los umbrales absolutos (densidad>1.5, piezas_por_caja<1) son sintomas, no
    jueces: solo explican 14 de los 65 SKUs cuyo costo modelado resulta
    imposible contra su precio real de venta (bloque 5).

Uso:
  backend/.venv/Scripts/python.exe backend/scripts/auditar_costeo.py
  backend/.venv/Scripts/python.exe backend/scripts/auditar_costeo.py --dias 90 --top 40
  backend/.venv/Scripts/python.exe backend/scripts/auditar_costeo.py --tsv reporte.tsv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg2

RAIZ = Path(__file__).resolve().parent.parent.parent

# La tarifa de flete es FIJA y no cambia por embarque (confirmado por Eduardo,
# 18-ago-2026). Es la misma constante que usa backend/services/costos.py, asi que
# el flete correcto de cualquier SKU es exactamente volumen_pieza_m3 x TARIFA.
# Eso vuelve el flete una funcion PURA del volumen: para auditarlo solo hay que
# auditar las dimensiones por pieza.
TARIFA_CBM_M3 = 7500.0
# Tolerancia contra la tarifa fija: +-2 pct absorbe redondeos de captura.
TOLERANCIA = 0.02
# Un contenedor con pocos SKUs no da una mediana confiable (bloque 2, informativo).
MIN_SKUS_CONTENEDOR = 20


def url_prod() -> str:
    for linea in (RAIZ / ".env").read_text(encoding="utf-8").splitlines():
        if linea.startswith("SUPABASE_DB_URL="):
            return linea.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("ABORT: .env no tiene SUPABASE_DB_URL.")


# Ventas de la ventana + la base con tarifa implicita y mediana por contenedor.
# Se reusa en todos los bloques.
CTE_BASE = """
with ventas as (
  select oi.sku,
         sum(oi.cantidad)::int uds,
         sum(oi.cantidad * oi.precio_unitario) ingreso,
         sum(oi.cantidad * oi.precio_unitario) / nullif(sum(oi.cantidad), 0) precio
  from channel.order_items oi
  join channel.orders o
    on o.external_order_id = oi.external_order_id
   and o.canal = oi.canal and o.cuenta = oi.cuenta
  where o.creado_at >= now() - (%(dias)s || ' days')::interval
    and lower(o.estado_canal) not in ('cancelled', 'canceled')
  group by 1
),
base as (
  select c.sku, c.contenedor, c.costo_producto, c.costo_cbm, c.costo_total,
         c.largo, c.alto, c.ancho, c.peso, c.piezas_por_caja ppc, c.cajas,
         (c.largo * c.alto * c.ancho) / 1000000.0 vol_m3,
         case when c.largo * c.alto * c.ancho > 0
              then c.costo_cbm / ((c.largo * c.alto * c.ancho) / 1000000.0) end tarifa,
         (c.largo * c.alto * c.ancho) / 1000000.0 * %(tarifa)s flete_correcto,
         case when c.peso > 0 and c.largo * c.alto * c.ancho > 0
              then c.peso / ((c.largo * c.alto * c.ancho) / 1000.0) end densidad,
         coalesce(v.uds, 0) uds, coalesce(v.ingreso, 0) ingreso, v.precio
  from costing.costos_validados c
  left join ventas v on v.sku = c.sku
),
medianas as (
  select contenedor,
         percentile_cont(0.5) within group (order by tarifa) tarifa_med,
         count(*) n
  from base
  where contenedor is not null and tarifa is not null and costo_cbm > 0
  group by 1
  having count(*) >= %(min_skus)s
)
"""

Q_CENSO = CTE_BASE + """
select 'filas totales' concepto, count(*) skus, sum(uds) uds, 0::numeric dinero from base
union all select 'sin costo_producto (null o 0)', count(*), sum(uds), round(sum(costo_cbm*uds)::numeric,0)
  from base where coalesce(costo_producto,0) = 0
union all select 'sin costo_cbm (null o 0)', count(*), sum(uds), 0::numeric
  from base where coalesce(costo_cbm,0) = 0
union all select 'sin dimensiones', count(*), sum(uds), 0::numeric
  from base where coalesce(largo,0)*coalesce(alto,0)*coalesce(ancho,0) = 0
union all select 'sin peso (null o 0)', count(*), sum(uds), round(sum(costo_cbm*uds)::numeric,0)
  from base where coalesce(peso,0) = 0
union all select 'sin piezas_por_caja', count(*), sum(uds), 0::numeric
  from base where ppc is null
union all select 'piezas_por_caja < 1 (divisor)', count(*), sum(uds), round(sum(costo_cbm*uds)::numeric,0)
  from base where ppc < 1
union all select 'densidad > 1.5 kg/L', count(*), sum(uds), round(sum(costo_cbm*uds)::numeric,0)
  from base where densidad > 1.5
union all select 'densidad > 5 kg/L (grave)', count(*), sum(uds), round(sum(costo_cbm*uds)::numeric,0)
  from base where densidad > 5
union all select 'sin contenedor', count(*), sum(uds), 0::numeric
  from base where contenedor is null
order by 4 desc, 2 desc
"""

Q_CONTENEDORES = CTE_BASE + """
select case when m.tarifa_med < 1500
            then 'B. flete DIVIDIDO entre piezas_por_caja (mal)'
            else 'A. flete en la tarifa fija (bien)' end estado,
       count(distinct b.contenedor) contenedores,
       count(*) skus,
       round(percentile_cont(0.5) within group (order by m.tarifa_med)::numeric, 0) tarifa_mxn_m3,
       round((percentile_cont(0.5) within group (order by m.tarifa_med) / 19)::numeric, 0) tarifa_usd_m3,
       sum(b.uds) uds,
       round(sum(b.costo_cbm * b.uds)::numeric, 0) flete_reconocido
from base b join medianas m using (contenedor)
group by 1 order by 1
"""

Q_OUTLIERS = CTE_BASE + """
select b.sku, b.contenedor,
       round((b.costo_cbm / nullif(b.flete_correcto, 0))::numeric, 3) pct_de_la_tarifa,
       b.uds, round(b.precio::numeric, 0) precio_venta,
       round(b.costo_producto::numeric, 0) costo_prod,
       round(b.costo_cbm::numeric, 0) flete_hoy,
       round(b.flete_correcto::numeric, 0) flete_correcto,
       round(((b.flete_correcto - b.costo_cbm) * b.uds)::numeric, 0) flete_faltante,
       b.largo, b.alto, b.ancho, b.ppc, b.cajas
from base b
where b.flete_correcto > 0 and b.uds > 0
  and abs(b.costo_cbm - b.flete_correcto) > %(tol)s * b.flete_correcto
order by abs((b.flete_correcto - b.costo_cbm) * b.uds) desc
limit %(top)s
"""

Q_CUMPLIMIENTO = CTE_BASE + """
select case when costo_cbm between flete_correcto*(1-%(tol)s) and flete_correcto*(1+%(tol)s)
                 then 'a. YA en la tarifa fija'
            when costo_cbm > flete_correcto            then 'b. por ARRIBA de la tarifa'
            when costo_cbm >= 0.40 * flete_correcto    then 'c. entre 40 y 98 pct'
            when costo_cbm >= 0.10 * flete_correcto    then 'd. entre 10 y 40 pct'
            else                                            'e. menos del 10 pct' end tramo,
       count(*) skus, count(*) filter (where uds > 0) con_venta, sum(uds) uds,
       round(sum(costo_cbm * uds)::numeric, 0)                   flete_reconocido,
       round(sum(flete_correcto * uds)::numeric, 0)              flete_correcto,
       round(sum((flete_correcto - costo_cbm) * uds)::numeric, 0) faltante
from base where flete_correcto > 0 and costo_cbm > 0
group by 1 order by 1
"""

Q_IMPOSIBLES = CTE_BASE + """
select b.sku,
       round((b.costo_total / nullif(b.precio, 0))::numeric, 1) costo_sobre_precio,
       b.uds, round(b.precio::numeric, 0) precio_venta,
       round(b.costo_producto::numeric, 0) costo_prod,
       round(b.costo_cbm::numeric, 0) flete,
       round((b.costo_producto / 19)::numeric, 1) costo_prod_usd,
       case when b.ppc < 1 then 'ppc<1 ' else '' end ||
       case when b.densidad > 1.5 then 'densidad ' else '' end ||
       case when coalesce(b.peso,0) = 0 then 'sin_peso ' else '' end ||
       case when b.ppc is null then 'sin_ppc ' else '' end ||
       case when coalesce(b.costo_producto,0) = 0 then 'prod_0 ' else '' end ||
       case when abs(b.costo_cbm - b.flete_correcto) > %(tol)s * b.flete_correcto
            then 'flete_fuera' else '' end flags
from base b
where b.uds > 0 and b.precio > 0 and b.costo_total / b.precio >= 2
order by b.costo_total / b.precio desc
limit %(top)s
"""

Q_BASCULAS = CTE_BASE + """
, amz as (select sku, avg(per_unit_volume) vol_amz
          from ops.fba_snapshot where per_unit_volume > 0 group by 1)
select b.sku, b.uds,
       round((b.vol_m3 * 1000000)::numeric, 0) vol_costeo_cm3,
       round(a.vol_amz::numeric, 0) vol_amazon_cm3,
       round((b.vol_m3 * 1000000 / a.vol_amz)::numeric, 1) veces,
       round(b.costo_cbm::numeric, 0) flete_hoy,
       round((a.vol_amz / 1000000.0 * m.tarifa_med)::numeric, 0) flete_si_amazon,
       round(((b.costo_cbm - a.vol_amz / 1000000.0 * m.tarifa_med) * b.uds)::numeric, 0) dinero_de_mas
from base b join amz a on a.sku = b.sku
join medianas m using (contenedor)
where b.vol_m3 > 0 and b.vol_m3 * 1000000 / a.vol_amz >= 2 and b.uds > 0
order by dinero_de_mas desc
limit %(top)s
"""


def correr(cur, titulo: str, sql: str, params: dict, tsv: list) -> None:
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    filas = cur.fetchall()
    print("\n" + "=" * 78 + "\n" + titulo + "\n" + "=" * 78)
    anchos = [
        max([len(c)] + [len(str(f[i] if f[i] is not None else "")) for f in filas])
        for i, c in enumerate(cols)
    ]
    print("  ".join(c.ljust(anchos[i]) for i, c in enumerate(cols)))
    print("  ".join("-" * a for a in anchos))
    for f in filas:
        print("  ".join(str(v if v is not None else "").ljust(anchos[i]) for i, v in enumerate(f)))
    print(f"({len(filas)} filas)")
    tsv.append((titulo, cols, filas))


def main() -> None:
    ap = argparse.ArgumentParser(description="Auditoria de calidad del costeo (solo lectura).")
    ap.add_argument("--dias", type=int, default=60, help="ventana de ventas para pesar por dinero")
    ap.add_argument("--top", type=int, default=25, help="cuantas filas por listado")
    ap.add_argument("--tsv", help="ademas, volcar todo a este archivo TSV")
    args = ap.parse_args()

    params = {"dias": args.dias, "min_skus": MIN_SKUS_CONTENEDOR,
              "tarifa": TARIFA_CBM_M3, "tol": TOLERANCIA, "top": args.top}

    cn = psycopg2.connect(url_prod(), connect_timeout=30)
    cn.set_session(readonly=True, autocommit=True)   # candado: produccion no se toca
    cur = cn.cursor()

    print(f"Auditoria de costing.costos_validados - ventana de ventas: {args.dias} dias")
    tsv: list = []
    correr(cur, "1. CENSO DE DEFECTOS (dinero = flete reconocido en la ventana)",
           Q_CENSO, params, tsv)
    correr(cur, "2. COMO QUEDO EL FLETE POR CONTENEDOR\n"
                "   La tarifa NO cambia por embarque. Un contenedor cuya tarifa implicita\n"
                "   sale muy por debajo de la fija es uno donde el flete se dividio entre\n"
                "   piezas_por_caja al cargarlo.",
           Q_CONTENEDORES, params, tsv)
    correr(cur, f"3. CUMPLIMIENTO DE LA TARIFA FIJA ({TARIFA_CBM_M3:.0f} por m3)",
           Q_CUMPLIMIENTO, params, tsv)
    correr(cur, "4. SKUs FUERA DE LA TARIFA FIJA, por dinero\n"
                "   flete_correcto = volumen_pieza_m3 x tarifa. Como la tarifa no cambia,\n"
                "   toda diferencia sale de las DIMENSIONES por pieza.",
           Q_OUTLIERS, params, tsv)
    correr(cur, "5. COSTO MODELADO >= 2x EL PRECIO AL QUE SE VENDE (imposible)\n"
                "   'flags' vacio = ningun detector clasico lo explica.",
           Q_IMPOSIBLES, params, tsv)
    correr(cur, "6. VOLUMEN DEL COSTEO >= 2x EL QUE MIDE AMAZON (segunda bascula)",
           Q_BASCULAS, params, tsv)

    if args.tsv:
        with open(args.tsv, "w", encoding="utf-8") as fh:
            for titulo, cols, filas in tsv:
                fh.write("# " + titulo.splitlines()[0] + "\n")
                fh.write("\t".join(cols) + "\n")
                for f in filas:
                    fh.write("\t".join("" if v is None else str(v) for v in f) + "\n")
                fh.write("\n")
        print(f"\nTSV escrito en {args.tsv}")

    cn.close()


if __name__ == "__main__":
    main()
