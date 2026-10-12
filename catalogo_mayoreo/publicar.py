"""
publicar.py — Sube un catálogo recién generado al módulo y lo pone a la vista.

    python publicar.py --carpeta <…/reportes/catalogo_vivo/mayoreo> [--url https://…]

Se corre desde la máquina que generó el catálogo (la etapa `mayoreo` de CATALOGO_VIVO). Sube
`catalogo.json` y las hojas de fotos a la antesala del módulo y, solo si llegó todo, pide que se
publique: la tienda nunca ve un catálogo a medias. Sube todas las hojas cada vez (~40 MB).

La dirección y la llave se leen de `MAYOREO_URL` y `MAYOREO_ADMIN_KEY`, del entorno o de un `.env`
junto a este archivo (que no entra a git). La llave nunca se imprime.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

AQUI = Path(__file__).resolve().parent


def entorno() -> dict[str, str]:
    valores = {}
    try:
        for linea in (AQUI / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                k, _, v = linea.partition("=")
                valores[k.strip()] = v.strip()
    except OSError:
        pass
    valores.update({k: v for k, v in os.environ.items() if k.startswith("MAYOREO_")})
    return valores


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):                  # la consola de Windows no es UTF-8 por omisión
        flujo.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--carpeta", required=True, help="la carpeta con catalogo.json y fotos/")
    ap.add_argument("--url", default="", help="dirección del módulo (por omisión, MAYOREO_URL)")
    ap.add_argument("--solo-revisar", action="store_true", help="no sube nada: dice qué subiría")
    a = ap.parse_args()
    env = entorno()
    url = (a.url or env.get("MAYOREO_URL", "")).rstrip("/")
    llave = env.get("MAYOREO_ADMIN_KEY", "")
    carpeta = Path(a.carpeta)
    doc = json.loads((carpeta / "catalogo.json").read_text(encoding="utf-8"))
    hojas = [carpeta / "fotos" / f"m{n:03d}.jpg" for n in range(doc["fotos"]["hojas"])]
    faltan = [h.name for h in hojas if not h.is_file()]
    if faltan:
        print(f"faltan {len(faltan)} hojas de fotos en {carpeta}: {faltan[:5]}", file=sys.stderr)
        return 1
    peso = sum(h.stat().st_size for h in hojas) + (carpeta / "catalogo.json").stat().st_size
    print(f"catálogo {doc['version']} · corte {doc['corte']} · {len(doc['P']):,} productos · {len(hojas)} hojas · {peso / 1e6:.1f} MB → {url or '(sin dirección)'}")
    if a.solo_revisar:
        return 0
    if not url or not llave:
        print("faltan MAYOREO_URL o MAYOREO_ADMIN_KEY (entorno o .env junto a este archivo)", file=sys.stderr)
        return 1
    cab = {"X-Admin-Key": llave}
    with httpx.Client(timeout=120, headers=cab) as c:
        def sube(ruta: Path) -> None:
            nombre = "catalogo.json" if ruta.name == "catalogo.json" else f"fotos/{ruta.name}"
            for intento in (1, 2, 3):
                r = c.put(f"{url}/api/mayoreo/admin/archivo/{nombre}", content=ruta.read_bytes())
                if r.status_code == 200:
                    return
                if r.status_code in (401, 404):
                    raise SystemExit(f"el módulo rechazó la llave ({r.status_code})")
                if intento == 3:
                    raise SystemExit(f"{nombre}: {r.status_code} {r.text[:120]}")

        with ThreadPoolExecutor(max_workers=3) as pool:
            for n, _ in enumerate(pool.map(sube, hojas), 1):
                if n % 25 == 0 or n == len(hojas):
                    print(f"  fotos {n}/{len(hojas)}")
        sube(carpeta / "catalogo.json")
        r = c.post(f"{url}/api/mayoreo/admin/publicar")
        if r.status_code != 200:
            print(f"no se publicó: {r.status_code} {r.text[:200]}", file=sys.stderr)
            return 1
        print("a la vista:", r.json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
