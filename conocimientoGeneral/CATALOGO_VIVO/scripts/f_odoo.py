"""
f_odoo.py — El catálogo de Odoo, EN VIVO, por XML-RPC. Solo `search_read`.

Qué trae de cada `product.product` con referencia interna (SKU):

  · identidad      default_code, name, categoría, fechas
  · INVENTARIO     free_qty («Free to Use»: físico menos lo reservado), qty_available,
                   incoming/outgoing — y free_qty POR ALMACÉN (DROP OFF, TEXCO, TEXCO II)
  · embarque       container_numbers (texto libre: a qué packing list pertenece)
  · ficha          standard_price, units_per_master_box, medidas del cartón…
  · foto           image_128 → miniatura JPEG en `cache/img/odoo/<id>.jpg`

DOS FAMILIAS DE COLUMNAS, y no son igual de confiables (medido en producción):
el módulo de INVENTARIO cuadra al 100% contra sus movimientos; la FICHA del
proveedor se captura a mano y 58% de los productos se contradicen consigo mismos.
Por eso la ficha viaja aparte, en `ficha`, y nadie la usa para costear.
"""
from __future__ import annotations

import base64
import io
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from comun import Cfg, ahora_iso, aviso, escribir_json, num

CAMPOS = [
    "default_code", "name", "categ_id", "product_tmpl_id", "barcode",
    "free_qty", "qty_available", "incoming_qty", "outgoing_qty",
    "container_numbers", "standard_price", "list_price", "costo_usd",
    "units_per_master_box", "cbm_master_box", "cbm_per_product",
    "length", "width", "height", "weight", "create_date", "write_date",
]
LADO = 72            # lado de la miniatura, en px
LOTE_DATOS = 400
LOTE_FOTOS = 60


class Odoo:
    def __init__(self, cfg: Cfg):
        self.url, self.db = cfg("ODOO_URL").rstrip("/"), cfg("ODOO_DB")
        self.user, self.pwd = cfg("ODOO_USER"), cfg("ODOO_PASSWORD")
        if not (self.url and self.db and self.user and self.pwd):
            raise RuntimeError("Odoo sin configurar (ODOO_URL / ODOO_DB / ODOO_USER / ODOO_PASSWORD)")
        comun_ = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.uid = comun_.authenticate(self.db, self.user, self.pwd, {})
        if not self.uid:
            raise RuntimeError("Odoo rechazó las credenciales")

    def leer(self, modelo: str, dominio: list, campos: list[str], **kw: Any) -> list[dict]:
        """`search_read` — el único método que esta carpeta le pide a Odoo."""
        proxy = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        return proxy.execute_kw(self.db, self.uid, self.pwd, modelo, "search_read",
                                [dominio], {"fields": campos, **kw})


def _miniatura(b64: str) -> bytes | None:
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(base64.b64decode(b64)))
        im.load()
    except Exception:  # noqa: BLE001 — una foto rota no tumba el catálogo
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
    salida = io.BytesIO()
    lienzo.save(salida, "JPEG", quality=74, optimize=True)
    return salida.getvalue()


def extraer(cfg: Cfg, salida: Path, con_fotos: bool = True) -> dict[str, Any]:
    o = Odoo(cfg)
    inicio = ahora_iso()
    ctx = {"lang": "es_MX"}

    # 1 · El catálogo: todo producto ACTIVO con referencia interna.
    productos: list[dict] = []
    offset = 0
    while True:
        lote = o.leer("product.product", [["default_code", "!=", False]], CAMPOS,
                      limit=LOTE_DATOS, offset=offset, order="id asc", context=ctx)
        productos.extend(lote)
        aviso(f"odoo: {len(productos)} productos")
        if len(lote) < LOTE_DATOS:
            break
        offset += LOTE_DATOS

    # 2 · free_qty por almacén: la misma lectura con el almacén en el contexto.
    almacenes = o.leer("stock.warehouse", [], ["name", "code"])
    por_almacen: dict[int, dict[str, float]] = {}
    ids = [p["id"] for p in productos]
    for a in almacenes:
        codigo = a.get("code") or a.get("name")
        for i in range(0, len(ids), 1000):
            for f in o.leer("product.product", [["id", "in", ids[i:i + 1000]]],
                            ["free_qty"], context={"warehouse": a["id"]}):
                q = num(f.get("free_qty")) or 0.0
                if q:
                    por_almacen.setdefault(f["id"], {})[codigo] = q
        aviso(f"odoo: almacén {codigo} listo")

    # 3 · Fotos: image_128 → miniatura. Tres hilos; Odoo aguanta eso sin despeinarse.
    carpeta = salida / "cache" / "img" / "odoo"
    carpeta.mkdir(parents=True, exist_ok=True)
    con_foto: set[int] = set()
    if con_fotos:
        pendientes = [i for i in ids if not (carpeta / f"{i}.jpg").exists()]
        con_foto = {i for i in ids if (carpeta / f"{i}.jpg").exists()}
        sin_foto_previas = set(_leer_lista(carpeta / "_sin_foto.txt"))
        pendientes = [i for i in pendientes if i not in sin_foto_previas]
        lotes = [pendientes[i:i + LOTE_FOTOS] for i in range(0, len(pendientes), LOTE_FOTOS)]
        hechos = 0

        def _lote(trozo: list[int]) -> tuple[set[int], set[int]]:
            ok, vacias = set(), set()
            for f in o.leer("product.product", [["id", "in", trozo]], ["image_128"]):
                mini = _miniatura(f["image_128"]) if f.get("image_128") else None
                if mini:
                    (carpeta / f"{f['id']}.jpg").write_bytes(mini)
                    ok.add(f["id"])
                else:
                    vacias.add(f["id"])
            return ok, vacias

        with ThreadPoolExecutor(max_workers=3) as pool:
            for ok, vacias in pool.map(_lote, lotes):
                con_foto |= ok
                sin_foto_previas |= vacias
                hechos += 1
                if hechos % 20 == 0 or hechos == len(lotes):
                    aviso(f"odoo: fotos {hechos}/{len(lotes)} lotes · {len(con_foto)} con foto")
        (carpeta / "_sin_foto.txt").write_text(
            "\n".join(str(i) for i in sorted(sin_foto_previas)), encoding="utf-8")

    filas = []
    for p in productos:
        sku = (p.get("default_code") or "").strip()
        if not sku:
            continue
        filas.append({
            "id": p["id"], "sku": sku, "nombre": (p.get("name") or "").strip(),
            "categoria": (p.get("categ_id") or [None, ""])[1],
            "plantilla": (p.get("product_tmpl_id") or [None])[0],
            "libre": num(p.get("free_qty")) or 0.0,
            "fisico": num(p.get("qty_available")) or 0.0,
            "entrante": num(p.get("incoming_qty")) or 0.0,
            "saliente": num(p.get("outgoing_qty")) or 0.0,
            "por_almacen": por_almacen.get(p["id"], {}),
            "contenedor": (p.get("container_numbers") or "").strip() or None,
            "foto": p["id"] in con_foto,
            "ficha": {
                "costo_ficha": num(p.get("standard_price")),
                "costo_usd": num(p.get("costo_usd")),
                "precio_lista": num(p.get("list_price")),
                "piezas_caja": num(p.get("units_per_master_box")),
                "cbm_caja": num(p.get("cbm_master_box")),
                "cbm_producto": num(p.get("cbm_per_product")),
                "largo": num(p.get("length")), "ancho": num(p.get("width")),
                "alto": num(p.get("height")), "peso": num(p.get("weight")),
            },
            "creado": p.get("create_date"), "editado": p.get("write_date"),
        })
    doc = {
        "fuente": "Odoo · product.product por XML-RPC (search_read)",
        "leido_desde": inicio, "leido_hasta": ahora_iso(),
        "almacenes": [{"codigo": a.get("code"), "nombre": a.get("name")} for a in almacenes],
        "productos": len(filas), "con_foto": len(con_foto), "filas": filas,
    }
    escribir_json(salida / "datos" / "odoo.json", doc)
    aviso(f"odoo: {len(filas)} productos, {len(con_foto)} con foto → datos/odoo.json")
    return {k: v for k, v in doc.items() if k != "filas"}


def _leer_lista(ruta: Path) -> list[int]:
    try:
        return [int(x) for x in ruta.read_text(encoding="utf-8").split() if x.strip().isdigit()]
    except OSError:
        return []
