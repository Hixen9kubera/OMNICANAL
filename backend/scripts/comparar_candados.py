"""
comparar_candados.py — ¿Contestan lo mismo los candados de MySQL y los de kubera?

SOLO LECTURA. Es la comprobación previa a encender `SUPABASE_READ_CANDADOS`, y
la única de toda la migración donde equivocarse **mueve mercancía**.

LAS DOS DIRECCIONES NO PESAN IGUAL
----------------------------------
Un candado contesta "¿ya hice esto?". Hay dos formas de que los dos lados
difieran, y una es MUCHO peor:

  MySQL dice SÍ · kubera dice NO   → el movimiento SE VUELVE A APLICAR.
                                     Stock movido dos veces. **Esto es lo caro.**
  MySQL dice NO · kubera dice SÍ   → el movimiento se omite. Se nota porque el
                                     stock no cuadra, y se corrige a mano.

Por eso el reporte separa las dos y solo la primera reprueba.

LOS TRES CANDADOS, Y SUS LLAVES DISTINTAS
------------------------------------------
  1. operaciones de bodega   `fanout_log.item_id`  ↔  ops.fulfillment_operations
  2. compensación por pedido `fanout_log.item_id`  ↔  channel.orders (por PK)
     OJO: en MySQL la llave es el `wc_id` de Woo; en kubera es
     (canal, cuenta, external_order_id). No se comparan directo — hay que pasar
     por `channel.orders.wc_order_id`.
  3. marca de agua del FBA   texto de `resultado`  ↔  ops.fba_watermark
     Aquí no hay "sí/no": hay un NÚMERO, y un número distinto también mueve
     stock de más o de menos.

Uso:
  ...python backend/scripts/comparar_candados.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_APLICADAS = ("full_ingreso", "full_retiro", "fba_ingreso")
_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def main() -> None:
    E = {}
    for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            E[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)

    print("COMPARACION DE LOS TRES CANDADOS — produccion, solo lectura\n")

    # ── 1. Operaciones de bodega ────────────────────────────────────────────
    print("── 1. operaciones de bodega (mueven FULL/FBA) ──")
    ph = ",".join(["%s"] * len(_APLICADAS))
    with my.cursor() as c:
        c.execute(f"""SELECT DISTINCT item_id FROM fanout_log
                       WHERE accion IN ({ph})
                         AND (resultado IS NULL OR resultado NOT LIKE 'ERROR%%')
                         AND item_id IS NOT NULL AND item_id <> ''""", _APLICADAS)
        mysql_si = {str(r["item_id"]) for r in c.fetchall()}
    with pg.cursor() as c:
        c.execute("select operacion_id from ops.fulfillment_operations")
        kubera_si = {str(r[0]) for r in c.fetchall()}
    peligro = mysql_si - kubera_si
    inocuo = kubera_si - mysql_si
    print(f"     MySQL dice aplicadas: {len(mysql_si):5d}   kubera: {len(kubera_si):5d}")
    check("ninguna operacion que MySQL da por hecha le falta a kubera",
          not peligro,
          f"{len(peligro)} SE VOLVERIAN A APLICAR: {sorted(peligro)[:4]}"
          if peligro else "cero")
    if inocuo:
        print(f"     [info] {len(inocuo)} en kubera y no en MySQL — se omitirian, "
              f"no se duplican: {sorted(inocuo)[:3]}")

    # ── 2. Compensacion por pedido ──────────────────────────────────────────
    print("\n── 2. compensacion de stock por pedido ──")
    with my.cursor() as c:
        c.execute("""SELECT DISTINCT item_id FROM fanout_log
                      WHERE accion = 'full_compensado' AND item_id <> ''""")
        wc_mysql = {str(r["item_id"]) for r in c.fetchall()}
    with pg.cursor() as c:
        # La llave NO es la misma: MySQL guarda el wc_id de Woo, kubera la PK del
        # pedido. Se traduce por `channel.orders.wc_order_id`.
        c.execute("""select wc_order_id::text, stock_compensado_at, stock_revertido_at
                       from channel.orders where wc_order_id is not null""")
        ordenes = {r[0]: (r[1], r[2]) for r in c.fetchall()}
    kubera_comp = {w for w, (comp, rev) in ordenes.items()
                   if comp and (rev is None or rev < comp)}
    conocidos = wc_mysql & set(ordenes)
    peligro2 = {w for w in conocidos if w not in kubera_comp}
    print(f"     MySQL dice compensados: {len(wc_mysql):5d}   "
          f"kubera: {len(kubera_comp):5d}")
    print(f"     de los de MySQL, kubera conoce el pedido: {len(conocidos)}")
    check("ningun pedido compensado en MySQL aparece SIN compensar en kubera",
          not peligro2,
          f"{len(peligro2)} se COMPENSARIAN OTRA VEZ: {sorted(peligro2)[:4]}"
          if peligro2 else "cero")
    fuera = wc_mysql - set(ordenes)
    if fuera:
        print(f"     [info] {len(fuera)} wc_id que kubera no tiene como pedido "
              f"(viejos o de otra fuente): {sorted(fuera)[:3]}")

    # ── 3. Marca de agua del FBA ────────────────────────────────────────────
    print("\n── 3. marca de agua del FBA (un NUMERO, no un si/no) ──")
    with my.cursor() as c:
        c.execute("""SELECT f.sku, f.resultado FROM fanout_log f
                      JOIN (SELECT sku, MAX(id) mx FROM fanout_log
                             WHERE accion LIKE 'fba_%%' GROUP BY sku) u ON u.mx = f.id""")
        marca_my = {}
        for r in c.fetchall():
            m = re.search(r"→\s*(\d+)", str(r["resultado"] or ""))
            if m:
                marca_my[str(r["sku"])] = int(m.group(1))
    with pg.cursor() as c:
        c.execute("select sku::text, stock_fba from ops.fba_watermark")
        marca_kb = {r[0]: int(r[1]) for r in c.fetchall()}
    print(f"     MySQL (sacada del texto): {len(marca_my):5d} SKUs   "
          f"kubera (columna): {len(marca_kb):5d}")
    comunes = set(marca_my) & set(marca_kb)
    distintos = [(s, marca_my[s], marca_kb[s]) for s in comunes
                 if marca_my[s] != marca_kb[s]]
    solo_my = set(marca_my) - set(marca_kb)
    check("los SKUs con marca en los dos lados tienen el MISMO numero",
          not distintos,
          f"{len(distintos)} distintos: {distintos[:3]}" if distintos else
          f"{len(comunes)} coinciden")
    # Un SKU con marca en MySQL y sin ella en kubera se compara contra la semilla
    # del sync — puede ver un ingreso que no ocurrio, o perderse uno.
    check("ningun SKU tiene marca en MySQL y no en kubera",
          not solo_my,
          f"{len(solo_my)} sin marca en kubera: {sorted(solo_my)[:4]}"
          if solo_my else "cero")

    my.close()
    pg.close()
    print(f"\nVEREDICTO: {'los tres candados contestan igual — se puede encender' if _ok else 'NO ENCENDER: revisar lo marcado FALLA'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
