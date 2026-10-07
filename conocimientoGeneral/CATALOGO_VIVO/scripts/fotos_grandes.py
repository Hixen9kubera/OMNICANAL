"""
fotos_grandes.py — Las fotos de los productos al doble de tamaño, para verlas en grande en la página.

La página pinta cada foto desde un mosaico de 72 px (`img/hNNN.jpg`): sirve para la tabla y no para
ver qué producto es. Al dar clic en una foto se abre en grande, y para eso hace falta una imagen mejor.

Esta etapa arma un SEGUNDO juego de hojas, `img/gNNN.jpg`, con la misma numeración que el mosaico
chico (el índice `i` de cada fila), pero a 144 px. De dónde sale cada foto:

  · Odoo: `image_256` del producto (SOLO LECTURA; se guarda en `cache/img/odoo256/`).
  · Si la foto de la fila es la de un marketplace: la misma URL pública, pedida en grande (GET).
  · Si no se consigue ninguna, se amplía la de 72 px: se ve borrosa, pero el índice no queda vacío.

Solo se bajan las fotos de los SKUs del inventario (`inventario_pl.json`). Se reanuda sola:
lo que ya está en el caché no se vuelve a pedir. Las fotos que vienen del packing list (las filas
sin foto en Odoo) NO pasan por aquí: la página las amplía desde su mosaico de 72 px.
"""
from __future__ import annotations

import base64
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from comun import Cfg, Red, aviso, escribir_json, leer_json, sku_norm

LADO, COLS, POR_HOJA = 144, 12, 144


def _cuadro(datos: bytes) -> bytes | None:
    """La foto centrada en un lienzo blanco de LADO × LADO."""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(datos))
        im.load()
    except Exception:  # noqa: BLE001
        return None
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        fondo = Image.new("RGB", im.size, (255, 255, 255))
        fondo.paste(im, mask=im.split()[-1])
        im = fondo
    else:
        im = im.convert("RGB")
    im.thumbnail((LADO, LADO), Image.LANCZOS)
    lienzo = Image.new("RGB", (LADO, LADO), (255, 255, 255))
    lienzo.paste(im, ((LADO - im.width) // 2, (LADO - im.height) // 2))
    sal = io.BytesIO()
    lienzo.save(sal, "JPEG", quality=88)
    return sal.getvalue()


def construir(cfg: Cfg, salida: Path) -> dict[str, Any]:
    from PIL import Image

    datos = salida / "datos"
    cat = leer_json(salida / "datos.json")
    mapa = leer_json(datos / "imagenes.json")
    if not cat or not mapa:
        raise RuntimeError("faltan datos.json / datos/imagenes.json: corre antes `imagenes` y `pagina`")
    # Solo los productos que salen en la pagina de packing lists (los SKUs del inventario).
    skus = set((leer_json(datos / "inventario_pl.json") or {}).get("skus") or {})
    usados = sorted({r["i"] for r in cat["P"] if r.get("i") is not None and (not skus or sku_norm(r["s"]) in skus)})
    de_odoo: dict[int, str] = {}
    for ident, i in (mapa.get("odoo") or {}).items():
        de_odoo.setdefault(i, ident)
    de_url: dict[int, str] = {}
    for url, i in (mapa.get("url") or {}).items():
        de_url.setdefault(i, url)

    c_odoo = salida / "cache" / "img" / "odoo256"
    c_url = salida / "cache" / "img" / "url256"
    c_chica_odoo = salida / "cache" / "img" / "odoo"
    c_chica_url = salida / "cache" / "img" / "url"
    c_odoo.mkdir(parents=True, exist_ok=True)
    c_url.mkdir(parents=True, exist_ok=True)

    # 1 · Odoo: la de 256 px de cada producto que todavía no está en el caché.
    faltan = [int(de_odoo[i]) for i in usados if i in de_odoo and not (c_odoo / f"{de_odoo[i]}.jpg").exists()]
    if faltan:
        from f_odoo import Odoo

        o = Odoo(cfg)
        aviso(f"fotos grandes: pidiendo a Odoo {len(faltan)} fotos de 256 px")
        for n in range(0, len(faltan), 60):
            for f in o.leer("product.product", [["id", "in", faltan[n:n + 60]]], ["image_256"],
                            context={"active_test": False}):
                if f.get("image_256"):
                    (c_odoo / f"{f['id']}.jpg").write_bytes(base64.b64decode(f["image_256"]))
            if (n // 60) % 20 == 19:
                aviso(f"fotos grandes: {min(n + 60, len(faltan))}/{len(faltan)} de Odoo")

    # 2 · Marketplaces: la misma URL pública, sin achicar.
    def ruta_url(url: str) -> Path:
        return c_url / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")

    urls = [de_url[i] for i in usados if i not in de_odoo and i in de_url and not ruta_url(de_url[i]).exists()]
    if urls:
        red = Red(rps=30.0, timeout=25.0)

        def bajar(url: str) -> bool:
            try:
                r = red.get(url, imagen=True, reintentos=2)
                cuadro = _cuadro(r.content) if r.status_code == 200 else None
            except Exception:  # noqa: BLE001
                cuadro = None
            if cuadro:
                ruta_url(url).write_bytes(cuadro)
            return bool(cuadro)

        aviso(f"fotos grandes: bajando {len(urls)} fotos de los marketplaces")
        with ThreadPoolExecutor(max_workers=12) as pool:
            ok = sum(pool.map(bajar, urls))
        aviso(f"fotos grandes: {ok} de {len(urls)} bajaron")

    # 3 · Hojas, con el MISMO índice que el mosaico chico.
    carpeta = salida / "img"
    for viejo in carpeta.glob("g*.jpg"):
        viejo.unlink()
    tope = (max(usados) + 1) if usados else 0
    hojas = (tope + POR_HOJA - 1) // POR_HOJA
    por_hoja: dict[int, list[int]] = {}
    for i in usados:
        por_hoja.setdefault(i // POR_HOJA, []).append(i)
    buenas = ampliadas = 0
    peso = 0
    for h in range(hojas):
        indices = por_hoja.get(h) or []
        filas = ((max(indices) % POR_HOJA) // COLS + 1) if indices else 1
        hoja = Image.new("RGB", (COLS * LADO, filas * LADO), (255, 255, 255))
        for i in indices:
            crudo: bytes | None = None
            if i in de_odoo and (c_odoo / f"{de_odoo[i]}.jpg").exists():
                crudo = _cuadro((c_odoo / f"{de_odoo[i]}.jpg").read_bytes())
            elif i in de_url and ruta_url(de_url[i]).exists():
                crudo = ruta_url(de_url[i]).read_bytes()
            if crudo:
                buenas += 1
            else:                                   # no hay grande: la chica, ampliada
                chica = (c_chica_odoo / f"{de_odoo[i]}.jpg") if i in de_odoo else (
                    c_chica_url / (hashlib.sha1(de_url[i].encode()).hexdigest() + ".jpg") if i in de_url else None)
                if chica is None or not chica.exists():
                    continue
                crudo = _cuadro(chica.read_bytes())
                ampliadas += 1
            if crudo:
                n = i % POR_HOJA
                hoja.paste(Image.open(io.BytesIO(crudo)), ((n % COLS) * LADO, (n // COLS) * LADO))
        destino = carpeta / f"g{h:03d}.jpg"
        hoja.save(destino, "JPEG", quality=72, optimize=True, progressive=True)
        peso += destino.stat().st_size
    doc = {"lado": LADO, "cols": COLS, "por_hoja": POR_HOJA, "hojas": hojas, "fotos": buenas, "ampliadas": ampliadas}
    escribir_json(datos / "fotos_grandes.json", doc)
    aviso(f"fotos grandes: {buenas} fotos en {hojas} hojas ({peso / 1e6:.1f} MB) · {ampliadas} ampliadas de la chica")
    return {**doc, "mb": round(peso / 1e6, 1)}
