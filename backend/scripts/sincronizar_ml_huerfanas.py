"""
Sincroniza las publicaciones de ML que NO están en `canal_inventario`.

QUÉ DESTAPÓ ESTO (30-jul). Brandon preguntó si de verdad se habían revisado TODAS
las publicaciones de las dos cuentas. Al pedirle el censo a ML:

    BEKURA          ML 2,188  ·  nuestro caché 1,865  ->  faltan 323
    SANCORFASHION   ML 2,240  ·  nuestro caché 1,836  ->  faltan 404

**794 publicaciones vivas en Mercado Libre que nuestro sistema nunca ha visto.**
Ni el fan-out ni el alineador las tocaron jamás.

POR QUÉ FALTAN: `canal_inventario` para ML se llena desde `ml_progress`, la
bitácora de NUESTRO publicador. Toda publicación creada fuera de él (a mano en
ML, o por el publicador externo que se retiró) no entra nunca. Mismo patrón que
Amazon, que se llena de `amazon_progress`.

OJO CON EL SKU: ML lo guarda en `attributes.SELLER_SKU`, NO en
`seller_custom_field`. Leyendo el campo equivocado estas 794 parecían "sin SKU"
(y por lo tanto imposibles de sincronizar). Con el campo correcto, **777 tienen
SKU y 566 de esos SKUs existen en Woo**.

QUÉ SINCRONIZA
  · Solo DROP: `cross_docking` y `xd_drop_off`. Las 487 `fulfillment` son bodega
    de ML y no se tocan.
  · Solo si el SKU existe en Woo con stock gestionado (si no, no hay verdad
    contra la cual alinear).
  · Respeta la pausa: usa `fanout_stock._escribir_ml`, que manda `status` junto
    al stock y verifica después.

Uso:
    python -m scripts.sincronizar_ml_huerfanas            # dry-run
    python -m scripts.sincronizar_ml_huerfanas --aplicar
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

import httpx

logging.disable(logging.WARNING)

DROP = {"cross_docking", "xd_drop_off"}
PAUSA_S = 0.6


def _ids_en_ml(cuenta: str, token: str) -> list[str]:
    """Todos los item_id de la cuenta (scan paginado)."""
    uid = httpx.get("https://api.mercadolibre.com/users/me",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30.0).json().get("id")
    ids, scroll = [], None
    while True:
        params = {"limit": 100, "search_type": "scan"}
        if scroll:
            params["scroll_id"] = scroll
        r = httpx.get(f"https://api.mercadolibre.com/users/{uid}/items/search",
                      headers={"Authorization": f"Bearer {token}"},
                      params=params, timeout=40.0)
        if r.status_code != 200:
            break
        j = r.json()
        ids += j.get("results") or []
        scroll = j.get("scroll_id")
        if not j.get("results") or not scroll:
            break
        time.sleep(0.15)
    return ids


def huerfanas() -> list[dict]:
    """Publicaciones DROP de ML, con SKU en Woo, que no están en el caché."""
    from services import db, meli, wp_db
    P = wp_db._prefix()
    fuera: list[dict] = []
    for cuenta in ("BEKURA", "SANCORFASHION"):
        token = meli._access_token(cuenta)
        if not token:
            print(f"   (sin token de {cuenta}: se omite)")
            continue
        ids = _ids_en_ml(cuenta, token)
        conocidos = {x["item_id"] for x in db.fetch_all(
            """SELECT item_id FROM canal_inventario
               WHERE canal='mercado_libre' AND cuenta=%s AND item_id IS NOT NULL""",
            (cuenta,))}
        faltan = [i for i in ids if i not in conocidos]
        print(f"   {cuenta}: ML {len(ids)} · caché {len(conocidos)} · fuera {len(faltan)}")
        for i in range(0, len(faltan), 20):
            lote = faltan[i:i + 20]
            try:
                r = httpx.get("https://api.mercadolibre.com/items",
                              headers={"Authorization": f"Bearer {token}"},
                              params={"ids": ",".join(lote),
                                      "attributes": "id,status,available_quantity,"
                                                    "attributes,shipping"},
                              timeout=40.0)
                if r.status_code != 200:
                    continue
                for e in r.json():
                    b = e.get("body") or {}
                    ats = {a.get("id"): (a.get("values") or [{}])[0].get("name")
                           for a in (b.get("attributes") or [])}
                    # El SKU vive en attributes.SELLER_SKU, no en seller_custom_field
                    sku = (ats.get("SELLER_SKU") or "").strip()
                    log_tipo = (b.get("shipping") or {}).get("logistic_type")
                    if not sku or log_tipo not in DROP:
                        continue
                    if str(b.get("status") or "").lower() not in ("active", "paused"):
                        continue
                    fuera.append({"cuenta": cuenta, "item_id": b.get("id"), "sku": sku,
                                  "situacion": b.get("status"),
                                  "actual": b.get("available_quantity") or 0,
                                  "logistica": log_tipo})
            except Exception as exc:  # noqa: BLE001
                print(f"      (lote falló: {str(exc)[:60]})")
            time.sleep(0.15)

    # Stock de Woo por SKU (la verdad contra la cual alinear)
    skus = sorted({f["sku"] for f in fuera})
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

    listos = []
    for f in fuera:
        w = woo.get(f["sku"])
        if w is None:
            continue
        # `max(0, …)` OBLIGATORIO: Woo sí admite stock negativo y ML NO. Sin este
        # candado se intentó escribir -1 en TEC-0011-NEG y ML lo rechazó con
        # HTTP 400 (bien hecho). Los otros alineadores ya clampaban; este nació
        # sin la regla. Un negativo en Woo se trata como cero: no hay nada que
        # ofrecer.
        objetivo = max(0, w)
        if int(f["actual"]) == objetivo:
            continue
        listos.append({**f, "objetivo": objetivo})
    listos.sort(key=lambda x: -(x["actual"] - x["objetivo"]))
    return listos


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
                 "sync ML huerfanas (fuera de canal_inventario)",
                 c["objetivo"], c["objetivo"], c["cuenta"], c["item_id"],
                 "escribir" if ok else "escribir_error", c["actual"], msg[:255]))
    except Exception as exc:  # noqa: BLE001
        print(f"   (no se pudo anotar {c['sku']}: {str(exc)[:70]})")


def main() -> None:
    print("Censando Mercado Libre…")
    lista = huerfanas()
    mas = [c for c in lista if c["actual"] > c["objetivo"]]
    menos = [c for c in lista if c["actual"] < c["objetivo"]]
    print(f"\nHuérfanas DROP desalineadas (con SKU en Woo): {len(lista)}")
    print(f"   ofrecen de MAS   : {len(mas):4d}  ({sum(c['actual']-c['objetivo'] for c in mas):,} pzas)")
    print(f"   ofrecen de MENOS : {len(menos):4d}  ({sum(c['objetivo']-c['actual'] for c in menos):,} pzas)")
    act = sum(1 for c in lista if (c["situacion"] or "").lower() == "active")
    print(f"   de esas, ACTIVAS (vendiendo): {act}\n")
    for c in lista[:15]:
        print(f"   {c['sku']:24s} {c['cuenta']:14s} {c['situacion']:7s} "
              f"{c['actual']:6d} -> {c['objetivo']:6d}")
    if len(lista) > 15:
        print(f"   … y {len(lista)-15} más")
    if "--aplicar" not in sys.argv:
        print("\n>>> DRY-RUN. Nada escrito. Correr con --aplicar.")
        return

    from services import fanout_stock
    print("\n>>> APLICANDO (respetando la pausa)…")
    ok = err = 0
    for i, c in enumerate(lista, 1):
        try:
            bien, msg = fanout_stock._escribir_ml(c["cuenta"], c["item_id"], c["objetivo"])
        except Exception as exc:  # noqa: BLE001
            bien, msg = False, f"{type(exc).__name__}: {exc}"
        ok, err = (ok + 1, err) if bien else (ok, err + 1)
        _anotar(c, bien, msg)
        if not bien or i % 25 == 0 or i == len(lista):
            print(f"   [{i}/{len(lista)}] {'OK ' if bien else 'ERR'} {c['sku']:22s} "
                  f"{c['cuenta']:14s} {c['actual']} -> {c['objetivo']}  {msg[:60]}")
        time.sleep(PAUSA_S)
    print(f"\n>>> Listo: {ok} escritas, {err} con error.")


if __name__ == "__main__":
    main()
