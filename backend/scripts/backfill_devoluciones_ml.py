"""
backfill_devoluciones_ml.py — puebla `channel.returns` con las devoluciones de ML.

DRY-RUN POR DEFAULT. Sin `--aplicar` no escribe nada: solo dice qué escribiría.

QUÉ RESUELVE AL CAPTURAR (y por qué cada una tiene su trampa)
-------------------------------------------------------------
La API de claims NO trae SKU, ni precio, ni monto. Todo eso sale de cruzar
`claim.resource_id` contra `channel.order_items` (empató 61/61 en los 7 días
medidos). Tres decisiones que NO son obvias:

  1. `es_fulfillment` se copia de **order_items**, JAMÁS de `orders`. Esa
     discrepa en el 40.11% de las líneas de ML (10,941 de 27,280), siempre en
     el mismo sentido (orders=false / items=true; el inverso no existe). Leer
     la de `orders` reportaría 2 devoluciones FULL de 62 en vez de 59.

  2. `venta_contaba` = ¿la orden estaba DENTRO de sales_daily al capturar? Se
     calcula con el MISMO filtro de la vista (migración 0030): `lower(estado)
     not in (cancelled, invalid, canceled)` — en minúsculas, porque cada canal
     escribe la cancelación con su propia caja. Sin esta columna el KPI resta
     devoluciones ya descontadas y subestima las ventas netas ~30%.

  3. `piezas` sale de `returns.orders[].return_quantity`, pero las devoluciones
     `low_cost` (ML reembolsa sin pedir el retorno) llegan con `orders: []`.
     Para esas se usa `claim.claimed_quantity`, que es el MISMO número en 62 de
     62 medidas. Si no, se perderían 3 de cada 62 devoluciones.

IDEMPOTENTE: `on conflict (canal, cuenta, claim_id) do update`. Correrlo dos
veces no duplica. Los importes se REESCRIBEN a propósito — mientras la
devolución esté abierta su estado cambia (label_generated → shipped → …).

USO
---
    python backend/scripts/backfill_devoluciones_ml.py                 # dry-run
    python backend/scripts/backfill_devoluciones_ml.py --aplicar
    python backend/scripts/backfill_devoluciones_ml.py --recolectar --dias 7

Sin `--recolectar` lee el crudo que dejó `sondear_devoluciones_ml.py` /
`recolectar` en `reportes/devoluciones_7d.jsonl`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

RAIZ = Path(__file__).resolve().parents[2]
CRUDO = RAIZ / "reportes" / "devoluciones_7d.jsonl"

# El filtro de cancelación, IDÉNTICO al de la vista channel.sales_daily (0030).
# Si esta lista se separa de la de allá, `venta_contaba` empieza a mentir.
CANCELADOS = ("cancelled", "invalid", "canceled")


def dsn() -> str:
    txt = (RAIZ / ".env").read_text()
    return re.search(r"^SUPABASE_DB_URL=(.*)$", txt, re.M).group(1).strip().strip('"').strip("'")


def _num(v, defecto=None):
    """'1.0' → 1. La API manda las cantidades como string decimal."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return defecto


def armar(filas: list[dict], cn) -> tuple[list[dict], list[str]]:
    """Cruza cada devolución contra kubera y devuelve (filas_a_escribir, avisos)."""
    oids = sorted({str((f.get("search") or {}).get("resource_id")) for f in filas})

    cur = cn.cursor()
    # El pedido: SKU, precio congelado y el es_fulfillment BUENO (order_items).
    cur.execute("""
        select o.external_order_id, o.cuenta, o.estado_canal,
               i.item_id, i.sku, i.cantidad, i.precio_unitario, i.es_fulfillment
        from channel.orders o
        join channel.order_items i using (canal, cuenta, external_order_id)
        where o.canal = 'mercado_libre' and o.external_order_id = any(%s)
    """, (oids,))
    lineas: dict[str, list[dict]] = {}
    for r in cur.fetchall():
        d = dict(zip(["oid", "cuenta", "estado", "item_id", "sku",
                      "cantidad", "precio", "full"], r))
        lineas.setdefault(d["oid"], []).append(d)

    cur.execute("select id, channel_id, legacy_code from core.accounts")
    cuentas = {(c, lc): i for i, c, lc in cur.fetchall()}

    out, avisos = [], []
    for f in filas:
        s = f.get("search") or {}
        det = f.get("detalle") or {}
        ret = f.get("returns") or {}
        cuenta = f.get("cuenta")
        claim_id = str(s.get("id"))
        oid = str(s.get("resource_id"))

        ords = ret.get("orders") or []
        item = ords[0] if ords else {}
        item_id = item.get("item_id")

        # piezas: return_quantity, y claimed_quantity para las low_cost sin orders[]
        piezas = _num(item.get("return_quantity"))
        if piezas is None:
            piezas = _num(det.get("claimed_quantity"), 0)
            if ords == []:
                avisos.append(f"{claim_id}: sin orders[] ({ret.get('subtype')}), "
                              f"piezas desde claimed_quantity={piezas}")

        # el pedido: si el item_id empata, esa línea; si no, la única del pedido
        cands = lineas.get(oid, [])
        m = [l for l in cands if str(l["item_id"]) == str(item_id)]
        linea = m[0] if m else (cands[0] if len(cands) == 1 else None)
        if not cands:
            avisos.append(f"{claim_id}: orden {oid} NO está en channel.orders "
                          f"→ sin SKU ni precio")
        elif not linea:
            avisos.append(f"{claim_id}: orden {oid} tiene {len(cands)} líneas y el "
                          f"item_id {item_id} no empató → sin SKU")

        sku    = linea["sku"] if linea else None
        precio = linea["precio"] if linea else None
        full   = bool(linea["full"]) if linea else False
        valor  = (float(precio) * piezas) if (precio is not None and piezas) else None

        # ¿la venta seguía contando en sales_daily? Mismo filtro que la 0030.
        estado_orden = (cands[0]["estado"] if cands else None)
        contaba = (None if estado_orden is None
                   else (estado_orden or "").lower() not in CANCELADOS)

        res = det.get("resolution") or {}
        env = (ret.get("shipments") or [{}])[0]

        out.append({
            "canal": "mercado_libre", "cuenta": cuenta, "claim_id": claim_id,
            "account_id": cuentas.get(("mercado_libre", cuenta)),
            "external_order_id": oid,
            "return_id": str(ret.get("id")) if ret.get("id") is not None else None,
            "item_id": item_id, "sku": sku, "piezas": piezas,
            "precio_unitario": precio, "valor": valor, "comision_devuelta": None,
            "es_fulfillment": full, "venta_contaba": contaba,
            "estado": ret.get("status"), "estado_dinero": ret.get("status_money"),
            "subtipo": ret.get("subtype"), "etapa": s.get("stage"),
            "estado_claim": s.get("status"), "motivo_id": s.get("reason_id"),
            "resolucion_motivo": res.get("reason"),
            "cobertura_ml": res.get("applied_coverage"),
            "cerrado_por": res.get("closed_by"),
            "shipment_id": str(env.get("shipment_id")) if env.get("shipment_id") else None,
            "estado_envio": env.get("status"), "tracking": env.get("tracking_number"),
            "creado_at": s.get("date_created"),
            "cerrado_at": res.get("date_created"),
            "actualizado_canal_at": s.get("last_updated"),
        })
    return out, avisos


COLUMNAS = """canal, cuenta, claim_id, account_id, external_order_id, return_id,
    item_id, sku, piezas, precio_unitario, valor, comision_devuelta,
    es_fulfillment, venta_contaba, estado, estado_dinero, subtipo, etapa,
    estado_claim, motivo_id, resolucion_motivo, cobertura_ml, cerrado_por,
    shipment_id, estado_envio, tracking, creado_at, cerrado_at,
    actualizado_canal_at"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="escribe (default: dry-run)")
    ap.add_argument("--crudo", default=str(CRUDO))
    args = ap.parse_args()

    p = Path(args.crudo)
    if not p.exists():
        sys.exit(f"No hay crudo en {p}. Corre primero el recolector.")
    filas = [json.loads(l) for l in p.open() if l.strip()]
    print(f"crudo: {len(filas)} devoluciones ({p.name})")

    cn = psycopg2.connect(dsn(), connect_timeout=30)
    try:
        datos, avisos = armar(filas, cn)

        # Resumen ANTES de escribir: es lo que se revisa en el dry-run.
        from collections import Counter
        print(f"\nfilas a escribir: {len(datos)}")
        print(f"  por cuenta      : {dict(Counter(d['cuenta'] for d in datos))}")
        print(f"  FULL / DROP     : FULL={sum(1 for d in datos if d['es_fulfillment'])} "
              f"DROP={sum(1 for d in datos if not d['es_fulfillment'])}")
        print(f"  venta_contaba   : {dict(Counter(d['venta_contaba'] for d in datos))}")
        print(f"  estado          : {dict(Counter(d['estado'] for d in datos))}")
        print(f"  estado_dinero   : {dict(Counter(d['estado_dinero'] for d in datos))}")
        print(f"  subtipo         : {dict(Counter(d['subtipo'] for d in datos))}")
        print(f"  con SKU         : {sum(1 for d in datos if d['sku'])}/{len(datos)}")
        print(f"  con valor       : {sum(1 for d in datos if d['valor'])}/{len(datos)}")
        tot = sum(float(d['valor']) for d in datos if d['valor'])
        rest = sum(float(d['valor']) for d in datos if d['valor'] and d['venta_contaba'])
        print(f"  VALOR devuelto  : ${tot:,.2f}")
        print(f"    de eso RESTABLE de ventas (venta_contaba): ${rest:,.2f}")
        print(f"    ya descontado (orden cancelada)          : ${tot - rest:,.2f}")
        if avisos:
            print(f"\n  avisos ({len(avisos)}):")
            for a in avisos:
                print(f"    · {a}")

        if not args.aplicar:
            print("\nDRY-RUN: no se escribió nada. Repite con --aplicar.")
            return 0

        cur = cn.cursor()
        actualiza = ", ".join(
            f"{c.strip()} = excluded.{c.strip()}"
            for c in COLUMNAS.replace("\n", " ").split(",")
            if c.strip() not in ("canal", "cuenta", "claim_id"))
        orden = [c.strip() for c in COLUMNAS.replace("\n", " ").split(",")]
        tuplas = [tuple(d[c] for c in orden) for d in datos]
        execute_values(cur, f"""
            insert into channel.returns ({COLUMNAS})
            values %s
            on conflict (canal, cuenta, claim_id) do update set {actualiza}
        """, tuplas)
        cn.commit()
        print(f"\n✅ escritas {cur.rowcount} filas en channel.returns")
        cur.execute("select count(*) from channel.returns")
        print(f"   total en la tabla: {cur.fetchone()[0]}")
    finally:
        cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
