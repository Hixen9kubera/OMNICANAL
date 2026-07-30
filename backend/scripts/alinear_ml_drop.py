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


def _refrescar_en_vivo(filas: list[dict]) -> list[dict]:
    """
    Reemplaza `stock_real`/`situacion` del caché por lo que dice ML AHORA.

    Usa el endpoint multi-get (`/items?ids=…&attributes=…`), 20 por llamada: 2,300
    publicaciones salen en ~115 peticiones en vez de 2,300.
    Si un lote falla, esas filas se quedan con el valor del caché y se marcan
    `_vivo=False` para no afirmar que se verificaron.
    """
    import httpx
    from services import meli
    por_cuenta: dict[str, list[dict]] = {}
    for f in filas:
        por_cuenta.setdefault(f["cuenta"], []).append(f)
    for cuenta, grupo in por_cuenta.items():
        token = meli._access_token(cuenta)
        if not token:
            print(f"   (sin token de {cuenta}: {len(grupo)} filas se quedan con el caché)")
            continue
        cab = {"Authorization": f"Bearer {token}"}
        for i in range(0, len(grupo), 20):
            lote = grupo[i:i + 20]
            ids = ",".join(x["item_id"] for x in lote)
            try:
                r = httpx.get("https://api.mercadolibre.com/items",
                              headers=cab,
                              params={"ids": ids,
                                      "attributes": "id,available_quantity,status"},
                              timeout=40.0)
                if r.status_code != 200:
                    continue
                vivos = {}
                for e in r.json():
                    b = e.get("body") or {}
                    if b.get("id"):
                        vivos[b["id"]] = b
                for x in lote:
                    b = vivos.get(x["item_id"])
                    if b:
                        x["stock_real"] = b.get("available_quantity")
                        x["situacion"] = b.get("status")
                        x["_vivo"] = True
            except Exception as exc:  # noqa: BLE001
                print(f"   (lote de {cuenta} falló: {str(exc)[:70]})")
            time.sleep(0.2)
    n = sum(1 for f in filas if f.get("_vivo"))
    print(f"   verificadas EN VIVO contra ML: {n} de {len(filas)}")
    return [f for f in filas
            if str(f.get("situacion") or "").lower() in ("active", "paused")]


def candidatos(en_vivo: bool = False) -> list[dict]:
    """
    Publicaciones ML DROP cuyo stock difiere del de Woo.

    `en_vivo=False` (default) compara contra el CACHÉ `canal_inventario`, que el
    sync refresca cada 15 min. Es barato pero el caché puede tener DÍAS de
    antigüedad en las filas que el sync todavía no revisitó, así que un "0
    desalineadas" solo significa "0 según el caché".

    `en_vivo=True` le pregunta a Mercado Libre por CADA publicación (`/items` en
    lotes de 20 con `attributes=`). Es la verificación de verdad — la que puede
    afirmar que el canal está alineado. Tarda unos minutos.
    """
    from services import db, wp_db
    P = wp_db._prefix()
    ml = db.fetch_all(
        """SELECT sku, cuenta, item_id, stock_real, situacion
           FROM canal_inventario
           WHERE canal='mercado_libre' AND es_full=0
             AND LOWER(situacion) IN ('active','paused')
             AND item_id IS NOT NULL""")
    if en_vivo:
        ml = _refrescar_en_vivo(ml)
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

    todos = candidatos(en_vivo="--en-vivo" in sys.argv)
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
