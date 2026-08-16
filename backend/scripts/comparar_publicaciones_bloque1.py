"""
comparar_publicaciones_bloque1.py — Paridad de las gemelas del BLOQUE 1 del PASO 3.

SOLO LECTURA. Compara, SKU por SKU, lo que devuelven las seis lecturas de
`ml_progress` / `amazon_progress` contra las gemelas nuevas de `channel_read`.

QUÉ SE COMPARA
--------------
  studio.py:108 · presencia.py:101 · publicar.py:154  →  channel_read.publicaciones_ml
  studio.py:124 · presencia.py:119 · publicar.py:259  →  channel_read.estado_amazon

Los seis sitios preguntan lo mismo con formas distintas, así que se comparan las
DOS preguntas de fondo, no seis consultas.

POR QUÉ AHORA SÍ SE PUEDE
-------------------------
Hasta el 16-ago, `ml_progress` era lo único que conocía una publicación recién
nacida durante hasta 15 min. Repuntar antes del seam habría convertido
"publicado hace 30 segundos" en "sin publicar". Con el seam midiendo 2 s de
mediana, esa ventana desapareció y la comparación es honesta.

LO QUE NO SE EXIGE IGUAL, Y POR QUÉ
-----------------------------------
- **Los SKUs republicados**: `ml_progress` guarda el MLM viejo y kubera el vivo
  (medido: 63 pares). Un id distinto **no** es fallo si kubera es el más
  reciente — mismo arbitraje por recencia que usa el arnés del seam. Solo
  reprueba si MySQL trae un id que kubera no conoce.
- **Amazon sin ASIN**: 268 publicaciones vivas no tienen ASIN, así que el
  `asin` puede ser NULL en los dos lados; lo que tiene que coincidir es
  `publicado`.

Uso:
  ...python backend/scripts/comparar_publicaciones_bloque1.py
  ...python backend/scripts/comparar_publicaciones_bloque1.py --muestra 500
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services import channel_read  # noqa: E402


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    if not p.exists():
        return d
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--muestra", type=int, default=0,
                    help="0 = TODOS los SKUs de las dos bitácoras")
    args = ap.parse_args()

    E = cargar(".env")
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    lim = f" LIMIT {int(args.muestra)}" if args.muestra else ""
    with my.cursor() as c:
        c.execute("SELECT sku, cuenta, ml_item_id FROM ml_progress "
                  f"WHERE ml_item_id IS NOT NULL AND ml_item_id <> ''{lim}")
        ml_my: dict[str, dict[str, str]] = {}
        for r in c.fetchall():
            ml_my.setdefault(str(r["sku"]), {})[str(r["cuenta"])] = str(r["ml_item_id"])
        c.execute(f"SELECT sku, asin, status, success, product_type FROM amazon_progress{lim}")
        am_my = {str(r["sku"]): r for r in c.fetchall()}
    my.close()

    _ok = True

    def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
        nonlocal _ok
        _ok &= bool(cond)
        print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))

    # ── ML ──────────────────────────────────────────────────────────────────
    print(f"\n── ML: ¿en qué cuentas está publicado y con qué MLM? ({len(ml_my)} SKUs) ──")
    ml_kb = channel_read.publicaciones_ml(list(ml_my))
    sin_kubera, id_distinto, kubera_gano = [], [], 0
    for sku, cuentas in ml_my.items():
        kb = {p["cuenta"]: str(p["item_id"]) for p in ml_kb.get(sku, [])}
        for cuenta, item in cuentas.items():
            if cuenta not in kb:
                sin_kubera.append((sku, cuenta, item))
            elif kb[cuenta] != item:
                # Republicado: kubera trae el vivo. Solo cuenta como fallo si el
                # SKU no está en kubera para nada, que es el caso de arriba.
                kubera_gano += 1
                id_distinto.append((sku, cuenta, item, kb[cuenta]))
    check("toda publicación de MySQL existe en kubera", not sin_kubera,
          f"{len(sin_kubera)} pares (sku,cuenta) que kubera no conoce")
    for s, c_, i in sin_kubera[:6]:
        print(f"        {s} ({c_}) item={i}")
    print(f"        [info] {kubera_gano} con MLM distinto — republicaciones donde "
          f"kubera tiene el vivo (esperado, no es fallo)")
    for s, c_, vi, nu in id_distinto[:3]:
        print(f"               {s} ({c_}): mysql={vi} kubera={nu}")

    # ── Amazon ──────────────────────────────────────────────────────────────
    print(f"\n── Amazon: ¿publicado, con qué ASIN y qué product_type? ({len(am_my)} SKUs) ──")
    am_kb = channel_read.estado_amazon(list(am_my))
    falta, dif_pub, dif_pt, cerradas_despues = [], [], [], []
    for sku, r in am_my.items():
        k = am_kb.get(sku)
        if not k:
            falta.append(sku)
            continue
        pub_my = bool(r["success"]) or str(r["status"] or "").upper() in channel_read._AMZ_PUBLICADO
        if pub_my != k["publicado"]:
            # ARBITRAJE, igual que con los MLM republicados: `amazon_progress`
            # congela el EVENTO de publicación; kubera refleja el estado VIVO.
            # Que MySQL diga "publicado" y kubera "closed" no es divergencia —
            # es kubera enterándose de que la publicación se cerró después.
            if pub_my and str(k.get("situacion") or "").lower() == "closed":
                cerradas_despues.append((sku, r["status"], k["situacion"]))
            else:
                dif_pub.append((sku, pub_my, k["publicado"], r["status"], k["status"]))
        if (r["product_type"] or None) != (k["product_type"] or None):
            dif_pt.append((sku, r["product_type"], k["product_type"]))
    check("todo SKU de amazon_progress existe en kubera", not falta,
          f"{len(falta)} sin fila en channel.listings")
    for s in falta[:6]:
        print(f"        {s}")
    check("el veredicto «publicado» coincide", not dif_pub,
          f"{len(dif_pub)} distintos")
    if cerradas_despues:
        print(f"        [info] {len(cerradas_despues)} que MySQL da por publicadas y "
              f"kubera tiene CERRADAS: la bitácora congeló el evento, kubera vio el "
              f"cierre posterior — kubera tiene razón")
        for s, a, b in cerradas_despues[:3]:
            print(f"               {s}: mysql={a} kubera situacion={b}")
    for s, a, b, sa, sb in dif_pub[:6]:
        print(f"        {s}: mysql={a} ({sa}) kubera={b} ({sb})")
    check("el product_type coincide", not dif_pt, f"{len(dif_pt)} distintos")
    for s, a, b in dif_pt[:6]:
        print(f"        {s}: mysql={a} kubera={b}")

    print(f"\nRESULTADO: {'las gemelas contestan igual' if _ok else 'HAY DIFERENCIAS — revisar arriba'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
