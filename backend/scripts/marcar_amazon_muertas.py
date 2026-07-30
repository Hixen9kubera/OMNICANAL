"""
Marca como `closed` las publicaciones de Amazon que YA NO EXISTEN.

QUÉ PROBLEMA RESUELVE (29-jul). El panel decía ~1,660 publicaciones de Amazon y
Seller Central 1,377. La diferencia son listados dados de baja que se quedaron en
`canal_inventario`: la tabla NUNCA borra (no hay un solo DELETE en el repo) y el
barrido de 15 min no le pregunta a Amazon si el listado sigue vivo — copia el
estado de `amazon_progress`, nuestra propia bitácora de publicación.

Esas mismas filas eran las que el alineador reportaba como "ilegibles": no es que
no se pudieran leer, es que Amazon responde **HTTP 404 — no existe**.

DECISIÓN DE BRANDON (29-jul): *"pueden ser porque se tuvieron que borrar de
Amazon, entonces esas podemos descontarlas, OJO pero no borrarlas"*.
Por eso se marca `situacion='closed'` en vez de hacer DELETE:

  · `services/presencia.py` ya descarta `situacion='closed'` → dejan de contar
    como publicadas sin tocar una sola fila más.
  · Se conserva la trazabilidad (qué se publicó y cuándo).
  · No se rompe el espejo `channel.listings` de Lalo, que se alimenta de aquí.

OJO PARA LA MIGRACIÓN: esto SÍ genera deltas en las actas de Lalo. Está anotado
en docs/NOTAS_PARA_LALO_2026-07-29.md.

Uso:
    python -m scripts.marcar_amazon_muertas            # dry-run (solo mide)
    python -m scripts.marcar_amazon_muertas --aplicar
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from urllib.parse import quote

import httpx

logging.disable(logging.WARNING)

PAUSA_S = 0.22


def _muertas() -> tuple[list[str], int, int]:
    """Devuelve (skus_404, vivas, errores) consultando la Listings API."""
    from config import settings
    from services import db, amazon

    skus = [r["sku"] for r in db.fetch_all(
        """SELECT DISTINCT sku FROM canal_inventario
           WHERE canal = 'amazon'
             AND LOWER(COALESCE(situacion,'')) <> 'closed'""")]
    token = asyncio.run(amazon._access_token())
    if not token:
        raise RuntimeError("sin token de Amazon")
    cab = {"x-amz-access-token": token}
    base = "https://sellingpartnerapi-na.amazon.com/listings/2021-08-01/items"
    muertas, vivas, err = [], 0, 0
    for n, s in enumerate(skus, 1):
        try:
            # `quote` OBLIGATORIO: 34 SKUs traen '/', '*' o comillas
            # (BEB-0065-GRI/BLN, ORG-0588-NEG-14.5"). Interpolarlos crudos en la
            # ruta da un 404 FALSO y los marcaría como muertos sin serlo.
            r = httpx.get(
                f"{base}/{settings.amazon_seller_id}/{quote(s, safe='')}",
                headers=cab,
                params={"marketplaceIds": settings.amazon_marketplace_id,
                        "includedData": "summaries"},
                timeout=30.0,
            )
            if r.status_code == 404:
                muertas.append(s)
            elif r.status_code == 200:
                vivas += 1
            else:
                err += 1           # 429/5xx: NO se marca, se reintenta otro día
        except Exception:  # noqa: BLE001
            err += 1
        if n % 200 == 0:
            print(f"   … {n}/{len(skus)} consultadas")
        time.sleep(PAUSA_S)
    return muertas, vivas, err


def main() -> None:
    from services import db
    muertas, vivas, err = _muertas()
    print(f"\nPublicaciones de Amazon consultadas EN VIVO:")
    print(f"   vivas (HTTP 200)          : {vivas}")
    print(f"   MUERTAS (HTTP 404)        : {len(muertas)}")
    print(f"   ilegibles (429/5xx, se dejan como están): {err}")
    print(f"\n   Tras marcar quedarían {vivas} activas "
          f"(Seller Central reporta 1,377).")
    print(f"\n   Ejemplos de muertas: {muertas[:8]}")
    if "--aplicar" not in sys.argv:
        print("\n>>> DRY-RUN. Nada escrito. Correr con --aplicar.")
        return
    print("\n>>> MARCANDO como 'closed' (NO se borra ninguna fila)…")
    with db.get_cursor() as cur:
        for i in range(0, len(muertas), 200):
            lote = muertas[i:i + 200]
            marcas = ",".join(["%s"] * len(lote))
            cur.execute(
                f"""UPDATE canal_inventario SET situacion='closed', updated_at=NOW()
                    WHERE canal='amazon' AND sku IN ({marcas})""", tuple(lote))
    print(f">>> Listo: {len(muertas)} marcadas como closed. Filas borradas: 0.")

    # El espejo channel.listings NO se entera de este UPDATE: solo se dispara
    # desde inventario._upsert() del barrido, y para estos SKUs el barrido manda
    # situacion=NULL (Amazon los 404-ea) que el coalesce del espejo conserva como
    # el valor viejo. Sin esto MySQL dice `closed` y Supabase se queda en
    # PUBLISHED PARA SIEMPRE — fue lo que rompió el acta de Channel del 30-jul
    # (289 divergentes tras 9 días en cero).
    from services import channel_mirror
    r = channel_mirror.backfill_situacion("closed", canal="amazon")
    if r.get("ok"):
        print(f">>> Espejo channel.listings sincronizado: {r['leidas']} filas closed.")
    else:
        print(f">>> OJO: el espejo NO se actualizó ({r.get('motivo')}). "
              f"Correr POST /api/migracion/backfill/channel-situacion en producción, "
              f"o el acta de Channel saldrá con_deltas.")


if __name__ == "__main__":
    main()
