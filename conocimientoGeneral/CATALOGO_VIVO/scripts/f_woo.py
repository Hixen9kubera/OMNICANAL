"""
f_woo.py — El precio de catálogo y la CATEGORÍA de cada producto, de WooCommerce.

WooCommerce (chunche.shop) es el centro del catálogo: ahí cada producto tiene su
precio y su categoría, esté o no publicado en un marketplace. Todo son GET a su
REST (`/wp-json/wc/v3`):

  1. `/products/categories`   las categorías (nombre = hoja de ML; descripción = su id)
  2. `/products`              los productos padre y simples, paginados de 100:
                              precio, estado y sus categorías
  3. `/products?sku=a,b,c`    las VARIACIONES. La REST no las lista con sus padres,
                              pero sí las devuelve cuando se le pregunta por SKU, y
                              varios a la vez. Solo se preguntan los SKUs de Odoo
                              que no aparecieron en el paso 2.

TRES MAÑAS DE ESTE SITIO, todas medidas en producción:
  · LiteSpeed cachea las respuestas: toda lectura lleva `_cb` (cache-bust).
  · El hosting BLOQUEA POR VOLUMEN (un 403 anti-bot por IP). Por eso se va de uno
    en uno y despacio, y ante un 403 se espera un minuto: reintentar de inmediato
    solo lo empeora.
  · Una variación NO trae categorías: se le ponen las de su padre.

OJO CON EL PRECIO. Un borrador que nunca pasó por el Estudio trae precio 1.00 (o el
«Sales Price» de Odoo, que no es un precio de venta). Aquí se entrega lo que hay;
decidir cuál es un precio de verdad es trabajo de `pagina.py`, donde queda escrito.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from comun import Cfg, Red, ahora_iso, aviso, escribir_json, leer_json, num, sku_norm

CAMPOS = "id,sku,name,type,status,price,regular_price,sale_price,categories,parent_id"
_RE_ML = re.compile(r"MLM\d+")
LOTE_SKU = 40


class _Woo:
    def __init__(self, cfg: Cfg):
        self.base = cfg("WC_URL").rstrip("/") + "/wp-json/wc/v3"
        self.auth = (cfg("WC_CONSUMER_KEY") or cfg("WC_KEY"),
                     cfg("WC_CONSUMER_SECRET") or cfg("WC_SECRET"))
        if not (cfg("WC_URL") and all(self.auth)):
            raise RuntimeError("WooCommerce sin configurar (WC_URL / WC_CONSUMER_KEY / WC_CONSUMER_SECRET)")
        self.red = Red(rps=2.0, timeout=60.0)
        self.peticiones = 0

    def get(self, ruta: str, params: dict[str, Any]) -> tuple[list, dict]:
        for intento in range(5):
            r = self.red.get(self.base + ruta, params={**params, "_cb": str(time.time())},
                             auth=self.auth,
                             headers={"User-Agent": "Mozilla/5.0 (catalogo-vivo; solo lectura)"})
            self.peticiones += 1
            if r.status_code == 200:
                return r.json(), dict(r.headers)
            if r.status_code == 403:
                aviso(f"woo: 403 del hosting (anti-bot); espero 60 s (intento {intento + 1}/5)")
                time.sleep(60)
                continue
            raise RuntimeError(f"WooCommerce {ruta}: HTTP {r.status_code} {r.text[:160]}")
        raise RuntimeError("WooCommerce: el hosting sigue bloqueando (403) tras 5 esperas")


def extraer(cfg: Cfg, salida: Path) -> dict[str, Any]:
    woo = _Woo(cfg)
    inicio = ahora_iso()
    datos = salida / "datos"

    # 1 · Categorías (en este sitio NO forman árbol: son hojas de ML, todas en la raíz)
    cats: dict[int, dict[str, Any]] = {}
    categoria_ml: dict[str, str] = {}
    pagina = 1
    while True:
        lote, cab = woo.get("/products/categories",
                            {"per_page": 100, "page": pagina,
                             "_fields": "id,name,parent,description"})
        for c in lote:
            cats[int(c["id"])] = {"nombre": c.get("name") or "", "padre": int(c.get("parent") or 0)}
            # `crear_producto.py` crea cada categoría con la descripción «ML: MLM123456»
            m = _RE_ML.search(c.get("description") or "") or _RE_ML.search(c.get("name") or "")
            if m and c.get("name"):
                categoria_ml[c["name"]] = m.group(0)
        if pagina >= int(cab.get("x-wp-totalpages") or 1) or not lote:
            break
        pagina += 1
    aviso(f"woo: {len(cats)} categorías")

    def _ruta(cid: int) -> list[str]:
        nombres: list[str] = []
        vistos: set[int] = set()
        while cid and cid in cats and cid not in vistos:
            vistos.add(cid)
            nombres.append(cats[cid]["nombre"])
            cid = cats[cid]["padre"]
        return list(reversed(nombres))

    def _mejor_ruta(lista: list[dict[str, Any]] | None) -> list[str]:
        """La ruta más honda entre las categorías del producto (la más específica)."""
        rutas = [_ruta(int(c["id"])) for c in lista or [] if c.get("id")]
        rutas = [r for r in rutas if r and r != ["Uncategorized"] and r != ["Sin categorizar"]]
        return max(rutas, key=len) if rutas else []

    # 2 · Productos padre y simples
    por_id: dict[int, dict[str, Any]] = {}
    pagina, total_paginas = 1, 1
    while pagina <= total_paginas:
        lote, cab = woo.get("/products", {"status": "any", "per_page": 100, "page": pagina,
                                          "orderby": "id", "order": "asc", "_fields": CAMPOS})
        total_paginas = int(cab.get("x-wp-totalpages") or 1)
        for p in lote:
            por_id[int(p["id"])] = p
        if pagina % 10 == 0 or pagina == total_paginas:
            aviso(f"woo: productos página {pagina}/{total_paginas} · {len(por_id)}")
        if not lote:
            break
        pagina += 1

    filas: dict[str, dict[str, Any]] = {}

    def _anota(p: dict[str, Any], ruta: list[str]) -> None:
        sku = sku_norm(p.get("sku"))
        if not sku:
            return
        nueva = {"wc_id": p.get("id"), "tipo": p.get("type"), "estado": p.get("status"),
                 "nombre": p.get("name") or "",
                 "precio": num(p.get("price")), "regular": num(p.get("regular_price")),
                 "oferta": num(p.get("sale_price")), "padre": p.get("parent_id") or None,
                 "ruta": ruta}
        previa = filas.get(sku)
        # Dos productos con el mismo SKU: gana el que trae precio, y luego el publicado.
        if previa and (previa.get("precio") or 0) >= (nueva["precio"] or 0):
            return
        filas[sku] = nueva

    for p in por_id.values():
        _anota(p, _mejor_ruta(p.get("categories")))

    # 3 · Variaciones: los SKUs de Odoo que no salieron como producto propio
    odoo = leer_json(datos / "odoo.json") or {}
    faltan = sorted({sku_norm(f["sku"]) for f in odoo.get("filas") or []} - set(filas))
    faltan = [s for s in faltan if s and "," not in s]
    aviso(f"woo: {len(filas)} SKUs en productos; se preguntan {len(faltan)} más por SKU (variaciones)")
    for i in range(0, len(faltan), LOTE_SKU):
        tanda = faltan[i:i + LOTE_SKU]
        lote, _ = woo.get("/products", {"sku": ",".join(tanda), "per_page": 100, "_fields": CAMPOS})
        for p in lote:
            padre = por_id.get(int(p.get("parent_id") or 0)) or {}
            _anota(p, _mejor_ruta(p.get("categories")) or _mejor_ruta(padre.get("categories")))
        if (i // LOTE_SKU) % 25 == 0 or i + LOTE_SKU >= len(faltan):
            aviso(f"woo: variaciones {min(i + LOTE_SKU, len(faltan))}/{len(faltan)}")

    doc = {
        "fuente": "WooCommerce REST · products + products/categories",
        "leido_desde": inicio, "leido_hasta": ahora_iso(),
        "categorias": len(cats), "productos_padre": len(por_id), "skus": len(filas),
        "con_precio": sum(1 for f in filas.values() if f["precio"]),
        "con_categoria": sum(1 for f in filas.values() if f["ruta"]),
        "peticiones": woo.peticiones, "categoria_ml": categoria_ml, "filas": filas,
    }
    escribir_json(datos / "woo.json", doc)
    aviso(f"woo: {len(filas)} SKUs · {doc['con_precio']} con precio · {doc['con_categoria']} con "
          f"categoría · {woo.peticiones} peticiones")
    return {k: v for k, v in doc.items() if k != "filas"}
