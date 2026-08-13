"""
backfill_pedidos_ml.py — rellena en `pedidos_ml` los pedidos que el espejo
inverso no alcanzó a escribir mientras estuvo apagado.

POR QUÉ EXISTE. El 12-ago-2026, el paso 1 del desmantelamiento de PEDIDOS puso
`ORDERS_ESPEJO_INVERSO=false` y `pedidos_ml` dejó de recibir escrituras. Al
reactivarlo tras el incidente de los 964 pedidos fantasma, el espejo **reanudó
hacia adelante pero no rellenó hacia atrás**: quedaron 143 pedidos que viven en
kubera y no en MySQL (en la ventana congelada kubera registró 446 movimientos y
MySQL 3).

POR QUÉ IMPORTA, aunque MySQL ya no sea el registro. El candado de idempotencia
del alta (`orders_write.wc_order_id_previo`) lee kubera, pero **cae a MySQL si
kubera no responde**. Para esos 143 MySQL contestaría "no existe" y el alta
crearía un pedido duplicado en Woo — el mismo fallo del 12-ago esperando otro
disparador. El hueco no se cierra solo.

QUÉ HACE. Calcula el diff él mismo (kubera menos MySQL) en cada corrida, así que
es idempotente y verificable: correrlo dos veces deja el segundo en cero. Escribe
con el MISMO `ON DUPLICATE KEY UPDATE` del flujo vivo (`pedidos_ml.sincronizar`),
incluido el candado de importes: comisión y total solo admiten 0 → valor real,
nunca se re-pisa un valor ya puesto.

    python scripts/backfill_pedidos_ml.py             # dry-run: dice qué haría
    python scripts/backfill_pedidos_ml.py --real      # aplica
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg2
import psycopg2.extras
import pymysql

# Mismo upsert que services/pedidos_ml.py: la comisión y el total NO se re-tocan
# (congelan el dato histórico de la venta) SALVO que estén en 0 — un 0 no es
# histórico, es un dato que nunca se pudo capturar.
_UPSERT = """
INSERT INTO pedidos_ml (ml_order_id, cuenta, wc_order_id, estado_ml, estado_wc,
                        total, comision, es_full, skus, creado, actualizado)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE wc_order_id=VALUES(wc_order_id),
    estado_ml=VALUES(estado_ml), estado_wc=VALUES(estado_wc),
    comision=IF(comision=0, VALUES(comision), comision),
    total=IF(total=0, VALUES(total), total),
    actualizado=VALUES(actualizado)
"""


def _conexiones():
    my = pymysql.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"], cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=20, autocommit=False)
    pg = psycopg2.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = True
    return my, pg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="aplica (sin esto, dry-run)")
    ap.add_argument("--limite", type=int, default=0, help="0 = todos")
    args = ap.parse_args()

    my, pg = _conexiones()
    pc = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Diff calculado en cada corrida: idempotente y auto-verificable.
    with my.cursor() as cur:
        cur.execute("SELECT ml_order_id FROM pedidos_ml")
        en_mysql = {str(r["ml_order_id"]) for r in cur.fetchall()}
    pc.execute("""select external_order_id, cuenta, wc_order_id, estado_canal,
                         estado_wc, total, comision, es_fulfillment, skus,
                         creado_at, actualizado_at
                    from channel.orders""")
    kubera = pc.fetchall()
    faltan = [r for r in kubera if str(r["external_order_id"]) not in en_mysql]
    if args.limite:
        faltan = faltan[:args.limite]

    print(f"kubera channel.orders : {len(kubera)}")
    print(f"MySQL  pedidos_ml     : {len(en_mysql)}")
    print(f"FALTAN en el espejo   : {len(faltan)}")
    if not faltan:
        print("\nNada que rellenar: el espejo está al día.")
        return

    por_cuenta: dict[str, int] = {}
    for r in faltan:
        por_cuenta[r["cuenta"] or "(sin cuenta)"] = por_cuenta.get(r["cuenta"] or "(sin cuenta)", 0) + 1
    print("   por cuenta:", por_cuenta)
    print("\n   muestra:")
    for r in faltan[:5]:
        print(f"      {r['external_order_id']:<20} {str(r['cuenta']):<15} "
              f"wc={r['wc_order_id']} {r['estado_wc']} ${r['total']}")

    if not args.real:
        print(f"\n[DRY-RUN] escribiría {len(faltan)} fila(s). Usa --real para aplicar.")
        return

    filas = [(
        str(r["external_order_id"]), r["cuenta"], r["wc_order_id"],
        r["estado_canal"], r["estado_wc"], r["total"], r["comision"],
        1 if r["es_fulfillment"] else 0,
        ",".join(s for s in (r["skus"] or []) if s)[:255],
        r["creado_at"].replace(tzinfo=None) if r["creado_at"] else None,
        r["actualizado_at"].replace(tzinfo=None) if r["actualizado_at"] else None,
    ) for r in faltan]

    escritas = 0
    try:
        with my.cursor() as cur:
            for i in range(0, len(filas), 200):
                lote = filas[i:i + 200]
                cur.executemany(_UPSERT, lote)
                escritas += len(lote)
                print(f"   {escritas}/{len(filas)}")
        my.commit()
    except Exception:
        my.rollback()
        print("\nERROR: se revirtió TODO el backfill (transacción única).")
        raise

    # Verificación en la misma corrida: el diff debe quedar en cero.
    with my.cursor() as cur:
        cur.execute("SELECT ml_order_id FROM pedidos_ml")
        ahora = {str(r["ml_order_id"]) for r in cur.fetchall()}
    resto = [r for r in kubera if str(r["external_order_id"]) not in ahora]
    print(f"\n== APLICADO ==  escritas={escritas}   faltantes ahora={len(resto)}")
    if resto:
        print("   OJO: quedaron pendientes, revisar:", [r["external_order_id"] for r in resto[:10]])
        sys.exit(1)


if __name__ == "__main__":
    main()
