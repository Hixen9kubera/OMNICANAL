"""
Alineación masiva de las publicaciones ML DROP contra el stock de WooCommerce.

CONTEXTO (28-jul). Woo quedó alineado con Odoo hoy (99.4% ya coincidía + 69
correcciones). Esta corrida propaga esa verdad a las publicaciones DROP de las
DOS cuentas de ML, que llevaban meses sin sincronizar porque están PAUSADAS y
escribirles stock las reactivaba.

El blindaje de la pausa vive en `fanout_stock._escribir_ml` (manda `status` junto
al stock, lee el estado antes y verifica después). Aquí solo se decide QUÉ
publicaciones tocar y a qué ritmo.

QUÉ NO HACE
  · No toca FULL (esa bodega es del marketplace).
  · No toca `under_review`, `closed` ni `inactive`: ahí manda ML.
  · No reactiva nada: si una pausada despertara, `_escribir_ml` la re-pausa.

Uso:
    python -m scripts.alinear_ml_drop                    # dry-run
    python -m scripts.alinear_ml_drop --aplicar          # escribe
    python -m scripts.alinear_ml_drop --aplicar --limite 200 --offset 0
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

logging.disable(logging.WARNING)

PAUSA_S = 0.6          # respiro entre escrituras (límites de ML)


def _arg(nombre: str, defecto: int | None = None) -> int | None:
    if nombre in sys.argv:
        try:
            return int(sys.argv[sys.argv.index(nombre) + 1])
        except (IndexError, ValueError):
            return defecto
    return defecto


def candidatos() -> list[dict]:
    """Publicaciones ML DROP cuyo stock difiere del de Woo."""
    from services import db, wp_db
    P = wp_db._prefix()
    ml = db.fetch_all(
        """SELECT sku, cuenta, item_id, stock_real, situacion
           FROM canal_inventario
           WHERE canal='mercado_libre' AND es_full=0
             AND LOWER(situacion) IN ('active','paused')
             AND item_id IS NOT NULL""")
    skus = sorted({m["sku"] for m in ml})
    woo: dict[str, int] = {}
    for i in range(0, len(skus), 900):
        lote = "','".join(skus[i:i + 900])
        for r in wp_db._fetch_all(
            f"""SELECT sk.meta_value sku, st.meta_value s
                FROM {P}postmeta sk
                JOIN {P}posts p ON p.ID=sk.post_id AND p.post_status<>'trash'
                LEFT JOIN {P}postmeta st ON st.post_id=p.ID AND st.meta_key='_stock'
                WHERE sk.meta_key='_sku' AND sk.meta_value IN ('{lote}')"""):
            try:
                if r["s"] not in (None, ""):
                    woo[r["sku"]] = int(float(r["s"]))
            except (TypeError, ValueError):
                pass
    fuera = []
    for m in ml:
        w = woo.get(m["sku"])
        if w is None or m["stock_real"] is None:
            continue
        if int(m["stock_real"]) != w:
            fuera.append({**m, "objetivo": w, "actual": int(m["stock_real"])})
    # Los que ofrecen DE MÁS primero: son los que pueden causar sobreventa.
    fuera.sort(key=lambda x: -(x["actual"] - x["objetivo"]))
    return fuera


def _anotar(c: dict, ok: bool, msg: str) -> None:
    from services import db, fanout_stock
    try:
        fanout_stock._asegurar_schema()
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                    item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,0,%s,%s,'mercado_libre',%s,%s,%s,%s,%s,0)""",
                (datetime.now(timezone.utc).replace(tzinfo=None), c["sku"][:64],
                 "alineacion masiva ML DROP (Woo->ML)", c["objetivo"], c["objetivo"],
                 c["cuenta"], c["item_id"], "escribir" if ok else "escribir_error",
                 c["actual"], msg[:255]))
    except Exception as exc:  # noqa: BLE001
        print(f"   (no se pudo anotar {c['sku']}: {str(exc)[:80]})")


def main() -> None:
    aplicar = "--aplicar" in sys.argv
    limite, offset = _arg("--limite"), _arg("--offset", 0) or 0

    todos = candidatos()
    mas = [c for c in todos if c["actual"] > c["objetivo"]]
    menos = [c for c in todos if c["actual"] < c["objetivo"]]
    print(f"Publicaciones ML DROP desalineadas: {len(todos)}")
    print(f"   ML ofrece de MAS:   {len(mas):4d}  ({sum(c['actual']-c['objetivo'] for c in mas):,} pzas)")
    print(f"   ML ofrece de MENOS: {len(menos):4d}  ({sum(c['objetivo']-c['actual'] for c in menos):,} pzas)")
    pausadas = sum(1 for c in todos if (c["situacion"] or "").lower() == "paused")
    print(f"   de esas, PAUSADAS: {pausadas} (se escriben conservando la pausa)\n")

    tanda = todos[offset:offset + limite] if limite else todos[offset:]
    print(f"Tanda: {len(tanda)} publicaciones (offset {offset}"
          + (f", limite {limite}" if limite else "") + ")\n")
    if not aplicar:
        for c in tanda[:15]:
            print(f"   {c['sku']:24s} {c['cuenta']:14s} {c['situacion']:7s} "
                  f"{c['actual']:6d} -> {c['objetivo']:6d}")
        if len(tanda) > 15:
            print(f"   … y {len(tanda)-15} más")
        print("\n>>> DRY-RUN. Nada escrito. Correr con --aplicar.")
        return

    from services import fanout_stock
    ok = err = 0
    t0 = time.time()
    for i, c in enumerate(tanda, 1):
        try:
            bien, msg = fanout_stock._escribir_ml(c["cuenta"], c["item_id"], c["objetivo"])
        except Exception as exc:  # noqa: BLE001
            bien, msg = False, f"{type(exc).__name__}: {exc}"
        ok, err = (ok + 1, err) if bien else (ok, err + 1)
        _anotar(c, bien, msg)
        if not bien or i % 25 == 0 or i == len(tanda):
            marca = "OK " if bien else "ERR"
            print(f"   [{i}/{len(tanda)}] {marca} {c['sku']:22s} {c['cuenta']:14s} "
                  f"{c['actual']} -> {c['objetivo']}  {msg[:70]}")
        time.sleep(PAUSA_S)
    print(f"\n>>> Listo: {ok} escritas, {err} con error, "
          f"{round(time.time()-t0)} s.")


if __name__ == "__main__":
    main()
