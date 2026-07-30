"""
Alineación de las publicaciones AMAZON DROP (MFN) contra el stock de WooCommerce.

Gemelo de `alinear_ml_drop.py`, pero Amazon obliga a leer EN VIVO: su fila en
`canal_inventario` trae `stock_real = NULL` (solo guarda `stock_fba`), así que no
hay caché contra el cual comparar. Se consulta la Listings Items API publicación
por publicación con `fanout_stock._amazon_en_vivo`, que además dice si está
BUYABLE (dormida = se omite, escribirle la DESPERTARÍA).

DIFERENCIA IMPORTANTE CON ML: las publicaciones de Amazon SÍ están vendiendo. Un
desajuste aquí no es un riesgo futuro como en las pausadas de ML — es sobreventa
hoy. Por eso se ordenan por exceso descendente.

No toca FBA (bodega de Amazon) ni publicaciones dormidas.

Uso:
    python -m scripts.alinear_amazon_drop            # dry-run (solo mide)
    python -m scripts.alinear_amazon_drop --aplicar
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

logging.disable(logging.WARNING)

PAUSA_S = 0.25


def _arg(nombre: str, defecto: int | None = None) -> int | None:
    if nombre in sys.argv:
        try:
            return int(sys.argv[sys.argv.index(nombre) + 1])
        except (IndexError, ValueError):
            return defecto
    return defecto


def candidatos(limite: int | None = None) -> dict:
    from services import db, wp_db, fanout_stock
    P = wp_db._prefix()
    # NO se filtra por situacion='PUBLISHED' (bug corregido el 29-jul).
    # -----------------------------------------------------------------
    # Hasta v0.35.0 la `situacion` de Amazon era una copia de nuestra bitácora y
    # SIEMPRE decía 'PUBLISHED', así que ese filtro funcionaba de casualidad.
    # Desde que el barrido escribe el estado REAL (BUYABLE / DISCOVERABLE /
    # ACCEPTED), el filtro empezó a excluir publicaciones —incluidas las 38
    # BUYABLE, que son las que SÍ están vendiendo— y el hueco CRECE conforme el
    # barrido avanza: al completar la vuelta habría cubierto cero.
    # Ahora se excluye solo lo que de verdad no toca: `closed` (dada de baja).
    # De la vendibilidad se encarga `_amazon_en_vivo`, que lee el estado del
    # momento; el estado del caché nunca decide si se escribe.
    skus = [r["sku"] for r in db.fetch_all(
        """SELECT DISTINCT sku FROM canal_inventario
           WHERE canal='amazon' AND es_full=0
             AND LOWER(COALESCE(situacion,'')) <> 'closed'""")]
    if limite:
        skus = skus[:limite]
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

    desalineados, dormidas, ilegibles, iguales, sin_woo = [], 0, 0, 0, 0
    for n, s in enumerate(skus, 1):
        v = fanout_stock._amazon_en_vivo(s)
        if not v.get("ok"):
            ilegibles += 1
        elif not v.get("vendible"):
            dormidas += 1
        elif v.get("cantidad") is None:
            ilegibles += 1
        elif woo.get(s) is None:
            sin_woo += 1
        elif int(v["cantidad"]) == woo[s]:
            iguales += 1
        else:
            desalineados.append({"sku": s, "actual": int(v["cantidad"]),
                                 "objetivo": woo[s]})
        if n % 200 == 0:
            print(f"   … {n}/{len(skus)} leídas")
        time.sleep(PAUSA_S)
    desalineados.sort(key=lambda x: -(x["actual"] - x["objetivo"]))
    return {"desalineados": desalineados, "iguales": iguales, "dormidas": dormidas,
            "ilegibles": ilegibles, "sin_woo": sin_woo, "total": len(skus)}


def _anotar(c: dict, ok: bool, msg: str) -> None:
    from services import db, fanout_stock
    try:
        fanout_stock._asegurar_schema()
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                    item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,0,%s,%s,'amazon','',%s,%s,%s,%s,0)""",
                (datetime.now(timezone.utc).replace(tzinfo=None), c["sku"][:64],
                 "alineacion masiva Amazon DROP (Woo->Amazon)", c["objetivo"],
                 c["objetivo"], c["sku"][:64],
                 "escribir" if ok else "escribir_error", c["actual"], msg[:255]))
    except Exception as exc:  # noqa: BLE001
        print(f"   (no se pudo anotar {c['sku']}: {str(exc)[:80]})")


def main() -> None:
    aplicar = "--aplicar" in sys.argv
    r = candidatos(limite=_arg("--muestra"))
    d = r["desalineados"]
    mas = [c for c in d if c["actual"] > c["objetivo"]]
    menos = [c for c in d if c["actual"] < c["objetivo"]]
    print(f"\nPublicaciones Amazon DROP leídas: {r['total']}")
    print(f"   coinciden con Woo : {r['iguales']}")
    print(f"   DESALINEADAS      : {len(d)}")
    print(f"      ofrece de MAS  : {len(mas):4d}  ({sum(c['actual']-c['objetivo'] for c in mas):,} pzas)"
          "  <-- SOBREVENTA HOY")
    print(f"      ofrece de MENOS: {len(menos):4d}  ({sum(c['objetivo']-c['actual'] for c in menos):,} pzas)")
    print(f"   dormidas (se omiten): {r['dormidas']}   ilegibles: {r['ilegibles']}"
          f"   sin match en Woo: {r['sin_woo']}")
    print("\n   Top 12 sobreventa:")
    for c in mas[:12]:
        print(f"      {c['sku']:24s} amazon={c['actual']:6d} -> woo={c['objetivo']:6d}"
              f"  (sobran {c['actual']-c['objetivo']})")
    if not aplicar:
        print("\n>>> DRY-RUN. Nada escrito. Correr con --aplicar.")
        return

    from services import fanout_stock
    ok = err = 0
    t0 = time.time()
    for i, c in enumerate(d, 1):
        try:
            bien, msg = fanout_stock._escribir_amazon("", c["sku"], c["objetivo"])
        except Exception as exc:  # noqa: BLE001
            bien, msg = False, f"{type(exc).__name__}: {exc}"
        ok, err = (ok + 1, err) if bien else (ok, err + 1)
        _anotar(c, bien, msg)
        if not bien or i % 25 == 0 or i == len(d):
            print(f"   [{i}/{len(d)}] {'OK ' if bien else 'ERR'} {c['sku']:24s} "
                  f"{c['actual']} -> {c['objetivo']}  {msg[:70]}")
        time.sleep(0.4)
    print(f"\n>>> Listo: {ok} escritas, {err} con error, {round(time.time()-t0)} s.")


if __name__ == "__main__":
    main()
