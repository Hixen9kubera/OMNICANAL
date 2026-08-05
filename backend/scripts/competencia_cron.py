"""
competencia_cron.py — La corrida MENSUAL de competencia (cron de Railway).

Se corre como servicio Cron aparte, NO desde el scheduler embebido del web: el
web es un solo proceso y un cron mensual en proceso no dispara si justo ese día
el servicio se reinició. Config-as-code en `backend/railway.competencia.json`
(mismo patrón que railway.deltas-*.json), con `restartPolicyType: NEVER`.

Uso:
    python scripts/competencia_cron.py                  # corre el mes
    python scripts/competencia_cron.py --dry-run        # solo dice qué haría y cuánto costaría
    python scripts/competencia_cron.py --skus SKU1,SKU2 # subconjunto (pruebas)
    python scripts/competencia_cron.py --sembrar        # re-siembra los SKUs del MVP antes de medir

Salida por stdout para que quede en los logs de Railway. Código de salida != 0 si
la corrida falla, para que el cron se marque como fallido.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from config import settings  # noqa: E402
from services import competencia_captura, competencia_scraper, competencia_store  # noqa: E402

# Los 8 SKUs del MVP. MUE-0163-TEL no está en la tabla maestra `productos` — su
# nombre sale de WooCommerce (lookup huérfana) — y NO está publicado en Mercado
# Libre, así que sus búsquedas se miden pero "mi posición" siempre dirá "fuera".
MVP = [
    "MUE-0163-TEL", "TEC-1407-FORD", "TEC-0249-NEG-MOR", "TEC-0265-NEG-VER",
    "TEC-1765-MET", "ACC-0191-NEG-VER", "TEC-1211-PLA-NISSAN-2.5", "TEC-1539-AZL-XL",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="No mide nada: solo reporta el plan y el costo estimado.")
    ap.add_argument("--skus", default="", help="Lista separada por comas.")
    ap.add_argument("--sembrar", action="store_true",
                    help="Re-siembra los SKUs del MVP (nombre/categoría + término IA).")
    args = ap.parse_args()

    print("═══ Competencia · corrida mensual ═══")
    print(f"base local: {competencia_store.RUTA_DB}")
    if not competencia_scraper.disponible():
        print("AVISO: APIFY_API_KEY ausente — las búsquedas general/título no "
              "traerán ficha; solo correrá el ranking de categoría (API).")

    if args.sembrar:
        print("\n── Siembra ──")
        r = competencia_captura.sembrar_skus(MVP, con_ia=True)
        print(f"  guardados: {r.get('guardados')}")
        for clave in ("sin_registro_en_productos", "sin_publicacion_ml",
                      "sin_termino_general"):
            v = r.get(clave) or []
            if v:
                print(f"  ⚠ {clave}: {', '.join(v)}")

    pedidos = [s.strip() for s in args.skus.split(",") if s.strip()] or None
    vigilados = competencia_store.listar_skus()
    if pedidos:
        vigilados = [s for s in vigilados if s["sku"] in set(pedidos)]

    if not vigilados:
        print("\nERROR: no hay SKUs vigilados. Corre con --sembrar primero.")
        return 2

    # Las búsquedas se deduplican por término: varios SKUs comparten el general.
    terminos = {s["termino_general"].strip().lower()
                for s in vigilados if s.get("termino_general")}
    titulos = {s["nombre"].strip().lower() for s in vigilados if s.get("nombre")}
    busquedas = len(terminos | titulos)
    costo = competencia_scraper.costo_estimado(
        busquedas, settings.competencia_top, settings.competencia_con_detalle)

    print(f"\n── Plan ──")
    print(f"  SKUs vigilados     : {len(vigilados)}")
    print(f"  búsquedas de Apify : {busquedas} (deduplicadas por término)")
    print(f"  items por búsqueda : {settings.competencia_top} "
          f"(detalle={settings.competencia_con_detalle})")
    print(f"  costo estimado     : ~${costo} USD")
    print(f"  llamadas de visitas: ~{len(vigilados) * 3 * competencia_captura.TOPE_VISITAS} "
          f"(1 por publicación, ML no acepta multiget)")
    for s in vigilados:
        print(f"    {s['sku']:26} general=«{s.get('termino_general') or '—'}»  "
              f"cat={s.get('categoria_id') or '—'}")

    if args.dry_run:
        print("\n--dry-run: no se midió nada.")
        return 0

    print("\n── Midiendo ──")
    r = asyncio.run(competencia_captura.correr(origen="cron", skus=pedidos))
    if not r.get("ok"):
        print(f"ERROR: {r.get('motivo')}")
        return 1

    print(f"  periodo    : {r['periodo']}")
    print(f"  SKUs       : {r['skus']}")
    print(f"  resultados : {r['resultados']}")
    print(f"  visitas OK : {r['visitas_ok']}")
    print(f"  búsquedas  : {r['busquedas']}  (~${r['costo_apify_usd']} USD)")
    for a in r.get("avisos") or []:
        print(f"  ⚠ {a}")
    print("\nListo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
