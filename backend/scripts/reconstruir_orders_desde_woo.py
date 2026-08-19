"""
reconstruir_orders_desde_woo.py — Rellena `channel.orders` con lo que exista en
WooCommerce y le falte al registro. Es la red de seguridad que reemplaza a MySQL.

POR QUÉ EXISTE. Cuando kubera no responde al registrar una venta, el pedido YA
está creado en Woo y lo que falla es el apunte. Hoy eso lo atrapa MySQL —la
venta cae en `pedidos_ml` y el reintento se encola en `espejo_kubera_log`—, pero
las dos tablas son de la base que se está retirando: al apagarla, el colchón se
va con ella.

Y NO SIRVE CONFIAR EN QUE EL CANAL REINTENTE. El webhook de ML contesta 200
SIEMPRE y a propósito (si devolviera otra cosa, ML deshabilita el topic tras una
hora y las ventas dejan de entrar en silencio), y el procesamiento ocurre
DESPUÉS de esa respuesta. Cuando la escritura falla, ML ya se olvidó.

El apunte perdido no es cosmético: es el candado de idempotencia. Sin él, el
siguiente aviso de esa orden no encuentra rastro y crea OTRO pedido — así
nacieron los 964 fantasma del 12-ago-2026.

QUÉ HACE. Woo es la fuente: cada pedido guarda `_ml_order_id`, `_ml_cuenta`,
importes y estado. Se compara contra `channel.orders` y:

  · lo que está en Woo y NO en el registro  → se inserta
  · lo que apunta a un pedido en papelera   → se repunta al superviviente

NO reescribe filas sanas: los importes de una venta ya registrada son históricos
y no se re-tocan. Solo rellena huecos, así que correrlo dos veces deja el
segundo en cero.

    python backend/scripts/reconstruir_orders_desde_woo.py            # dry-run
    python backend/scripts/reconstruir_orders_desde_woo.py --real     # aplica
    python backend/scripts/reconstruir_orders_desde_woo.py --dias 30  # ventana
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Misma tabla que services/pedidos_ml._ESPEJO_ORIGEN: la cuenta dice el canal.
_CANAL = {"BEKURA": "mercado_libre", "SANCORFASHION": "mercado_libre",
          "AMAZON": "amazon", "TEMU": "temu", "TIKTOK": "tiktok"}
_VERDADERO = ("si", "sí", "yes", "1", "true")


def _wp():
    """WordPress. Mismo respaldo que services/wp_db: sin WPDB_HOST, el del panel."""
    return pymysql.connect(
        host=os.environ.get("WPDB_HOST") or os.environ["DB_HOST"],
        port=int(os.environ.get("WPDB_PORT") or 3306),
        user=os.environ["WPDB_USER"], password=os.environ["WPDB_PASSWORD"],
        database=os.environ["WPDB_NAME"], cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=20)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="aplica (sin esto, dry-run)")
    ap.add_argument("--dias", type=int, default=15,
                    help="ventana hacia atrás en días (0 = todo el historial)")
    args = ap.parse_args()

    P = os.environ.get("WPDB_PREFIX") or "wp_"
    wp = _wp()
    pg = psycopg2.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = False
    pc = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    ventana = ("AND o.date_created_gmt >= UTC_TIMESTAMP() - INTERVAL %d DAY"
               % int(args.dias)) if args.dias else ""
    with wp.cursor() as c:
        c.execute(
            "SELECT o.id wc_id, o.status, o.total_amount total, "
            "       o.date_created_gmt creado, "
            "       MAX(CASE WHEN m.meta_key='_ml_order_id' THEN m.meta_value END) ext, "
            "       MAX(CASE WHEN m.meta_key='_ml_cuenta'   THEN m.meta_value END) cuenta, "
            "       MAX(CASE WHEN m.meta_key='_ml_estado'   THEN m.meta_value END) estado_canal, "
            "       MAX(CASE WHEN m.meta_key='_ml_comision' THEN m.meta_value END) comision, "
            "       MAX(CASE WHEN m.meta_key='_ml_es_full'  THEN m.meta_value END) es_full "
            f"  FROM {P}wc_orders o "
            f"  JOIN {P}wc_orders_meta m ON m.order_id = o.id "
            f" WHERE o.status <> 'trash' {ventana} "
            "  GROUP BY o.id HAVING ext IS NOT NULL")
        woo = {r["ext"]: r for r in c.fetchall()}
        # Estado de TODOS los pedidos: dice si un puntero apunta a uno muerto.
        c.execute(f"SELECT id, status FROM {P}wc_orders")
        vivo = {r["id"]: r["status"] for r in c.fetchall()}
        # Superviviente por orden: el más antiguo que NO está en papelera.
        c.execute(f"SELECT m.meta_value ext, MIN(o.id) wc_id "
                  f"  FROM {P}wc_orders_meta m "
                  f"  JOIN {P}wc_orders o ON o.id = m.order_id "
                  f" WHERE m.meta_key='_ml_order_id' AND o.status <> 'trash' "
                  f" GROUP BY 1")
        superviviente = {r["ext"]: r["wc_id"] for r in c.fetchall()}
        # SKUs de cada pedido. La línea guarda `_variation_id` (0 si es simple)
        # y `_product_id`; el SKU vive en el postmeta de ese post. Sin esto la
        # fila reconstruida quedaba con `skus` en NULL y el tab de Ventas no
        # podía atribuir la venta a ningún producto.
        if woo:
            ids = tuple(r["wc_id"] for r in woo.values())
            ph = ",".join(["%s"] * len(ids))
            c.execute(
                "SELECT oi.order_id, sk.meta_value sku "
                f"  FROM {P}woocommerce_order_items oi "
                f"  LEFT JOIN {P}woocommerce_order_itemmeta pi "
                "         ON pi.order_item_id=oi.order_item_id AND pi.meta_key='_product_id' "
                f"  LEFT JOIN {P}woocommerce_order_itemmeta vi "
                "         ON vi.order_item_id=oi.order_item_id AND vi.meta_key='_variation_id' "
                f"  JOIN {P}postmeta sk "
                "    ON sk.post_id = COALESCE(NULLIF(vi.meta_value,'0'), pi.meta_value) "
                "   AND sk.meta_key='_sku' "
                f" WHERE oi.order_id IN ({ph}) AND sk.meta_value <> ''", ids)
            por_pedido: dict[int, list[str]] = {}
            for r in c.fetchall():
                por_pedido.setdefault(r["order_id"], []).append(r["sku"])
            for r in woo.values():
                r["skus"] = sorted(set(por_pedido.get(r["wc_id"], [])))
    wp.close()

    pc.execute("select external_order_id, wc_order_id from channel.orders")
    registro = {r["external_order_id"]: r["wc_order_id"] for r in pc.fetchall()}

    faltan = [r for e, r in woo.items() if e not in registro]
    rotos = [(e, w, superviviente.get(e)) for e, w in registro.items()
             if w is not None and vivo.get(w, "trash") == "trash"
             and superviviente.get(e) and superviviente[e] != w]

    print(f"pedidos en Woo (ventana {args.dias or 'completa'}) : {len(woo)}")
    print(f"filas en channel.orders                       : {len(registro)}")
    print(f"\nFALTAN en el registro : {len(faltan)}")
    print(f"punteros a papelera   : {len(rotos)}")
    for r in faltan[:5]:
        print(f"   + {str(r['ext']):<20} {str(r['cuenta']):<15} wc={r['wc_id']} "
              f"{r['status']} ${r['total']}")
    for e, w, v in rotos[:5]:
        print(f"   ~ {e:<20} {w} -> {v}")

    if not faltan and not rotos:
        print("\nNada que reconstruir: el registro está al día.")
        return
    if not args.real:
        print(f"\n[DRY-RUN] insertaría {len(faltan)} y repuntaría {len(rotos)}. "
              "Usa --real para aplicar.")
        return

    try:
        if faltan:
            psycopg2.extras.execute_batch(pc, """
                insert into channel.orders
                  (canal, cuenta, external_order_id, wc_order_id, estado_canal,
                   estado_wc, total, comision, es_fulfillment, skus, creado_at,
                   actualizado_at)
                values (%(canal)s, %(cuenta)s, %(ext)s, %(wc)s, %(ec)s, %(ew)s,
                        %(total)s, %(com)s, %(full)s, %(skus)s, %(creado)s, now())
                on conflict (canal, cuenta, external_order_id) do nothing
            """, [{
                "canal": _CANAL.get(r["cuenta"], str(r["cuenta"] or "").lower()),
                "cuenta": r["cuenta"],
                "ext": r["ext"],
                "wc": r["wc_id"],
                "ec": r["estado_canal"],
                "ew": str(r["status"] or "").replace("wc-", ""),
                "total": r["total"],
                "com": r["comision"] or 0,
                "full": str(r["es_full"] or "").strip().lower() in _VERDADERO,
                "skus": r.get("skus") or None,
                "creado": r["creado"],
            } for r in faltan], page_size=200)
        if rotos:
            psycopg2.extras.execute_batch(
                pc,
                "update channel.orders set wc_order_id=%(wc)s, actualizado_at=now() "
                " where external_order_id=%(ext)s",
                [{"wc": v, "ext": e} for e, _w, v in rotos], page_size=200)
        pg.commit()
    except Exception:
        pg.rollback()
        print("\nERROR: se revirtió TODO (transacción única).")
        raise

    # Verificación en la misma corrida: el hueco debe quedar en cero.
    pc.execute("select external_order_id from channel.orders")
    ahora = {r["external_order_id"] for r in pc.fetchall()}
    resto = [e for e in woo if e not in ahora]
    print(f"\n== APLICADO ==  insertadas={len(faltan)}  repuntadas={len(rotos)}  "
          f"siguen faltando={len(resto)}")
    pg.close()
    if resto:
        print("   OJO, quedaron pendientes:", resto[:10])
        sys.exit(1)


def reconstruir(dias: int = 2) -> dict:
    """
    Entrada para el scheduler: corre en modo REAL sobre una ventana corta.

    Ventana de 2 días por omisión: el hueco que interesa cerrar es el de las
    últimas horas (kubera caída al registrar), no el historial. Barrer 15 días
    cada vez sería pagar la consulta grande para encontrar siempre cero.
    """
    import contextlib, io as _io
    salida = _io.StringIO()
    argv = sys.argv
    sys.argv = ["reconstruir", "--real", "--dias", str(dias)]
    try:
        with contextlib.redirect_stdout(salida):
            main()
    except SystemExit:
        pass
    finally:
        sys.argv = argv
    texto = salida.getvalue()
    return {"ok": "siguen faltando=0" in texto or "al día" in texto,
            "salida": texto.strip().splitlines()[-1] if texto.strip() else ""}


if __name__ == "__main__":
    main()
