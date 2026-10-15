"""
pricing_preparar_foto.py — Convierte el «Monitor de precios FULL» de José (el
artifact) en la FOTO que sirve la pestaña PRICING (`/api/pricing/*`).

    python backend/scripts/pricing_preparar_foto.py ENTRADA HOJAS SALIDA

  ENTRADA  el JSON de datos del artifact (`{"meta", "pubs", "riv"}`) o el HTML
           publicado: de ahí se saca el `<script id="datos" type="application/json">`.
  HOJAS    carpeta con las hojas de miniaturas del artifact (`s0.jpg`, `s1.jpg`…:
           cuadrículas de 20×20 celdas de 90 px).
  SALIDA   carpeta de la foto: `monitor.json.gz` + `img/p*.jpg` + `img/s*.jpg`.

QUÉ CAMBIA RESPECTO AL ARTIFACT, Y POR QUÉ
    Las miniaturas de NUESTRAS publicaciones vienen regadas en las 27 hojas
    (16 MB): pintar la tabla obligaba a bajarlas todas. Aquí se re-empacan en
    hojas propias (`p0.jpg`, `p1.jpg`, ~1 MB) y el `img` de cada publicación
    pasa a apuntar ahí. Las hojas de rivales (`s*.jpg`) se copian tal cual y la
    pantalla las pide solo al abrir «lo scrapeado» de un SKU (casi siempre cae
    en una sola hoja). Los números NO se tocan: ni precios, ni ventas, ni la
    revisión de competencia.

SOLO LEE Y ESCRIBE ARCHIVOS. No toca kubera, Woo, ML ni Odoo.
"""
from __future__ import annotations

import gzip
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

CELDA = 90
LADO = 20
_DATOS_HTML = re.compile(r'<script id="datos" type="application/json">(.*?)</script>', re.S)


def leer_datos(entrada: Path) -> dict:
    texto = entrada.read_text(encoding="utf-8")
    if entrada.suffix.lower() in (".html", ".htm"):
        m = _DATOS_HTML.search(texto)
        if not m:
            raise SystemExit(f"{entrada}: no trae <script id=\"datos\">")
        texto = m.group(1)
    datos = json.loads(texto)
    for llave in ("meta", "pubs", "riv"):
        if llave not in datos:
            raise SystemExit(f"{entrada}: falta «{llave}»")
    return datos


def celda_de(hojas: Path, indice: int, abiertas: dict[int, Image.Image]) -> Image.Image:
    """El recorte de 90×90 de la celda `indice` en las hojas originales."""
    h, pos = divmod(indice, LADO * LADO)
    if h not in abiertas:
        abiertas[h] = Image.open(hojas / f"s{h}.jpg").convert("RGB")
    x, y = (pos % LADO) * CELDA, (pos // LADO) * CELDA
    return abiertas[h].crop((x, y, x + CELDA, y + CELDA))


def preparar(entrada: Path, hojas: Path, salida: Path) -> dict:
    datos = leer_datos(entrada)
    img = datos["meta"].get("img") or {}
    if img.get("celda", CELDA) != CELDA or img.get("lado", LADO) != LADO:
        raise SystemExit(f"hojas de {img} y este script espera {CELDA} px × {LADO}")

    (salida / "img").mkdir(parents=True, exist_ok=True)
    abiertas: dict[int, Image.Image] = {}

    # 1. Las miniaturas de nuestras publicaciones, re-empacadas en orden.
    con_img = [p for p in datos["pubs"] if p.get("img") is not None]
    por_hoja = LADO * LADO
    n_p = (len(con_img) + por_hoja - 1) // por_hoja
    for k in range(n_p):
        lienzo = Image.new("RGB", (CELDA * LADO, CELDA * LADO), "white")
        for pos, p in enumerate(con_img[k * por_hoja:(k + 1) * por_hoja]):
            lienzo.paste(celda_de(hojas, p["img"], abiertas),
                         ((pos % LADO) * CELDA, (pos // LADO) * CELDA))
        lienzo.save(salida / "img" / f"p{k}.jpg", "JPEG", quality=85, optimize=True,
                    progressive=True)
    for nuevo, p in enumerate(con_img):
        p["img"] = nuevo

    # 2. Las hojas de rivales: solo las que algún rival usa, tal cual.
    usadas = sorted({r[7] // por_hoja for v in datos["riv"].values() for r in v["r"]
                     if r[7] is not None})
    for h in usadas:
        shutil.copyfile(hojas / f"s{h}.jpg", salida / "img" / f"s{h}.jpg")

    datos["meta"]["img"] = {"celda": CELDA, "lado": LADO, "pubs": "p", "riv": "s",
                            "n_pubs": n_p, "hojas_riv": usadas}
    datos["meta"]["preparado"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    crudo = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (salida / "monitor.json.gz").write_bytes(gzip.compress(crudo, 9, mtime=0))
    return {"pubs": len(datos["pubs"]), "skus_con_rivales": len(datos["riv"]),
            "hojas_pubs": n_p, "hojas_riv": len(usadas), "json_bytes": len(crudo)}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    print(json.dumps(preparar(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])),
                     ensure_ascii=False))
