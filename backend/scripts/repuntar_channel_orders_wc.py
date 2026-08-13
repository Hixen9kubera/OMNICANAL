"""
repuntar_channel_orders_wc.py — devuelve a `channel.orders` el `wc_order_id`
VIVO cuando el que tiene apunta a un pedido de WooCommerce que ya no existe
(papelera o borrado).

POR QUÉ EXISTE. El 12-ago-2026 el incidente de los pedidos fantasma creó decenas
de pedidos en Woo por cada orden de ML. `channel.orders` guarda UNA fila por
orden, así que su `wc_order_id` quedó apuntando al ÚLTIMO fantasma creado. La
limpieza conservó el MÁS ANTIGUO de cada grupo — el que tiene la historia real y
el stock — y mandó el resto a la papelera: 145 filas del registro quedaron
apuntando a un pedido muerto.

Eso no es cosmético: el candado de idempotencia del alta lee ese `wc_order_id`,
así que el próximo cambio de estado que mande el canal se escribiría sobre el
pedido equivocado.

CRITERIO. Sustituto = el pedido NO-papelera más antiguo con el mismo
`_ml_order_id`. Sin sustituto, la fila NO se toca y se reporta: preferimos un
puntero roto y visible a uno inventado.

    python scripts/repuntar_channel_orders_wc.py           # dry-run
    python scripts/repuntar_channel_orders_wc.py --real    # aplica
"""
from __future__ import annotations

import argparse
import os

import psycopg2
import psycopg2.extras
import pymysql


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="aplica (sin esto, dry-run)")
    args = ap.parse_args()

    # Mismo respaldo que services/wp_db.py: sin WPDB_HOST se usa el del panel.
    wp = pymysql.connect(
        host=os.environ.get("WPDB_HOST") or os.environ["DB_HOST"],
        port=int(os.environ.get("WPDB_PORT") or 3306),
        user=os.environ["WPDB_USER"], password=os.environ["WPDB_PASSWORD"],
        database=os.environ["WPDB_NAME"], cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=20)
    P = os.environ.get("WPDB_PREFIX") or "wp_"
    pg = psycopg2.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = False
    pc = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    pc.execute("select external_order_id, wc_order_id from channel.orders "
               "where wc_order_id is not null")
    registro = pc.fetchall()

    # Estado real de cada pedido en Woo (HPOS). Lo que no aparece, no existe.
    with wp.cursor() as c:
        c.execute(f"SELECT id, status FROM {P}wc_orders")
        estado = {r["id"]: r["status"] for r in c.fetchall()}
        # Superviviente por orden: el más antiguo que NO está en papelera.
        c.execute(f"""SELECT m.meta_value ml_id, MIN(o.id) vivo
                       FROM {P}wc_orders_meta m
                       JOIN {P}wc_orders o ON o.id = m.order_id
                      WHERE m.meta_key = '_ml_order_id' AND o.status <> 'trash'
                      GROUP BY 1""")
        vivo_por_orden = {r["ml_id"]: r["vivo"] for r in c.fetchall()}

    rotos = [r for r in registro
             if estado.get(r["wc_order_id"]) in (None, "trash")]
    arreglables, sin_sustituto = [], []
    for r in rotos:
        v = vivo_por_orden.get(str(r["external_order_id"]))
        (arreglables if v and v != r["wc_order_id"] else sin_sustituto).append((r, v))

    print(f"filas de channel.orders con wc_order_id : {len(registro)}")
    print(f"apuntan a un pedido muerto             : {len(rotos)}")
    print(f"   con sustituto vivo (se repuntan)    : {len(arreglables)}")
    print(f"   SIN sustituto (no se tocan)         : {len(sin_sustituto)}")
    for (r, v) in arreglables[:5]:
        print(f"      ml={r['external_order_id']}  {r['wc_order_id']} -> {v}")
    for (r, _) in sin_sustituto[:5]:
        print(f"      SIN SUSTITUTO ml={r['external_order_id']} wc={r['wc_order_id']}")

    if not arreglables:
        print("\nNada que repuntar.")
        return
    if not args.real:
        print(f"\n[DRY-RUN] repuntaría {len(arreglables)} fila(s). Usa --real para aplicar.")
        return

    try:
        psycopg2.extras.execute_batch(
            pc,
            "update channel.orders set wc_order_id = %(wc)s, actualizado_at = now() "
            " where external_order_id = %(ext)s",
            [{"wc": v, "ext": r["external_order_id"]} for (r, v) in arreglables],
            page_size=200)
        pg.commit()
    except Exception:
        pg.rollback()
        print("\nERROR: se revirtió TODO el repunte (transacción única).")
        raise

    # Verificación en la misma corrida.
    pc.execute("select external_order_id, wc_order_id from channel.orders "
               "where wc_order_id is not null")
    quedan = [r for r in pc.fetchall()
              if estado.get(r["wc_order_id"]) in (None, "trash")]
    print(f"\n== APLICADO ==  repuntadas={len(arreglables)}   "
          f"apuntando a muerto ahora={len(quedan)}")


if __name__ == "__main__":
    main()
