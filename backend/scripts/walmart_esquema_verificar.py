"""
walmart_esquema_verificar.py — ¿Walmart cambió su especificación?

LA PREGUNTA QUE CONTESTA
────────────────────────
"La ficha dice «Listo para Walmart», Walmart la rebota por un campo que no está
en la lista y los requisitos se leyeron hace dos meses: seguro actualizaron la
especificación, ¿la volvemos a leer?"

Pasó el 2-oct con `screenSize` en «Juguetes de bebé». Y la respuesta fue NO: el
archivo público era idéntico byte a byte al que se cargó el 17-ago. Lo que
producción (3.11) exige de más no está en NINGÚN archivo publicado (3.19); solo
lo enseña el veredicto de un feed, y va en `CORRECCIONES_MEDIDAS`.

Este script lo comprueba en un minuto, sin recargar nada:

  1. Baja el esquema público de hoy y compara su SHA-256 con la huella del que
     está cargado (`SPEC_SHA256_CARGADO`).
  2. Si kubera contesta, compara categoría por categoría los OBLIGATORIOS del
     archivo contra los de `channel.field_requirements` (bloque Visible).
  3. Lista las correcciones medidas, que son lo que el archivo no dice.

SOLO LECTURA: un GET a developer.walmart.com y, si hay DSN, un SELECT.

    python -m scripts.walmart_esquema_verificar            # resumen
    python -m scripts.walmart_esquema_verificar --detalle  # diferencias por campo
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def _bajar(url: str) -> bytes:
    import httpx
    r = httpx.get(url, timeout=180.0, follow_redirects=True)
    r.raise_for_status()
    return r.content


def obligatorios_del_archivo(spec: dict) -> dict[str, set[str]]:
    vis = (spec["properties"]["MPItem"]["items"]["properties"]["Visible"]
           ["properties"])
    return {cat: set(bloque.get("required") or []) for cat, bloque in vis.items()}


def version_del_archivo(spec: dict) -> str:
    try:
        return (spec["properties"]["MPItemFeedHeader"]["properties"]["version"]
                ["enum"][0])
    except Exception:  # noqa: BLE001
        return "?"


def _obligatorios_de_la_bd() -> dict[str, set[str]] | None:
    try:
        from services import supabase_db as sdb
        filas = sdb.fetch_all(
            """select categoria_id as cat, campo, obligatorio,
                      coalesce(valores_permitidos->>'bloque', 'Visible') as bloque
                 from channel.field_requirements
                where canal = 'walmart' and categoria_id <> '*'""")
    except Exception as exc:  # noqa: BLE001
        print(f"   (kubera no contestó: {exc} — se compara solo la huella)")
        return None
    out: dict[str, set[str]] = {}
    for f in filas:
        if f["bloque"] != "Visible":
            continue
        out.setdefault(f["cat"], set())
        if f["obligatorio"]:
            out[f["cat"]].add(f["campo"])
    return out


def main() -> int:
    from scripts.walmart_field_requirements import (CORRECCIONES_MEDIDAS,
                                                    SPEC_SHA256_CARGADO,
                                                    SPEC_URL,
                                                    SPEC_VERSION_CARGADA)
    detalle = "--detalle" in sys.argv

    print("=" * 78)
    print("¿CAMBIÓ LA ESPECIFICACIÓN DE WALMART MX?")
    print("=" * 78)
    crudo = _bajar(SPEC_URL)
    huella = hashlib.sha256(crudo).hexdigest()
    spec = json.loads(crudo)
    version = version_del_archivo(spec)
    igual = huella == SPEC_SHA256_CARGADO
    print(f"   archivo de hoy : {len(crudo):,} bytes · versión {version}")
    print(f"   SHA-256 de hoy : {huella}")
    print(f"   SHA-256 cargado: {SPEC_SHA256_CARGADO}  (versión {SPEC_VERSION_CARGADA})")
    print(f"   → {'IDÉNTICO: Walmart no ha publicado nada nuevo.' if igual else 'DISTINTO: Walmart publicó una versión nueva.'}")

    del_archivo = obligatorios_del_archivo(spec)
    de_la_bd = _obligatorios_de_la_bd()
    difieren = 0
    if de_la_bd is not None:
        print(f"\n   categorías: {len(del_archivo)} en el archivo · {len(de_la_bd)} en kubera")
        for cat in sorted(set(del_archivo) | set(de_la_bd)):
            a, b = del_archivo.get(cat), de_la_bd.get(cat)
            if a is None or b is None:
                difieren += 1
                print(f"   ✗ «{cat}»: solo está en {'kubera' if a is None else 'el archivo'}")
                continue
            # Lo que producción exige de más se CARGA como obligatorio: no es
            # una diferencia con el archivo, es lo medido.
            medidos = {campo for (c, campo), (v, _) in CORRECCIONES_MEDIDAS.items()
                       if c == cat and v == "OBLIGATORIO"}
            sobran, faltan = (b - a) - medidos, a - b
            if sobran or faltan:
                difieren += 1
                print(f"   ✗ «{cat}»: el archivo pide de más {sorted(faltan) or '—'} · "
                      f"kubera pide de más {sorted(sobran) or '—'}")
            elif detalle:
                print(f"   ✓ «{cat}»: {len(a)} obligatorios, iguales")
        print(f"   → {difieren} categoría(s) con diferencias en los obligatorios.")

    print("\n   LO QUE EL ARCHIVO NO DICE (medido en feeds; el semáforo, la IA y el")
    print("   publicador lo leen en caliente, sin recargar el catálogo):")
    for (cat, campo), (veredicto, evidencia) in sorted(CORRECCIONES_MEDIDAS.items()):
        print(f"     · {cat:28} {campo:22} {veredicto:11} {evidencia[:70]}")

    print()
    if igual and not difieren:
        print("VEREDICTO: no hay nada que recargar. Si Walmart rebota un campo que no")
        print("está en la lista, anótalo en CORRECCIONES_MEDIDAS (y ármalo en `_item()`).")
        return 0
    print("VEREDICTO: hay diferencias. Recarga el catálogo con")
    print("    python -m scripts.walmart_field_requirements")
    print("y actualiza SPEC_SHA256_CARGADO con la huella de arriba.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
