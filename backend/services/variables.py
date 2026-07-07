"""
variables.py — Agrupa drafts simples de WooCommerce en PRODUCTOS VARIABLES.

Toma los SKUs que comparten base (CATEG-####) y los convierte en un producto
variable (padre + variaciones), igual que el pipeline original
(Proyecto_Jose/create_variable_products.py). El sufijo del SKU se parsea a
atributos legibles (NEG→Negro, MUL→Multicolor, S/M/L/XL, medidas…).

Por grupo de ≥2 miembros:
  1. Elige el mejor simple como padre (más imágenes, luego más stock).
  2. PUT: lo convierte a type=variable, sku=base, con atributos de variación.
  3. DELETE los demás simples (libera sus SKUs).
  4. POST una variación por cada miembro (sku, atributos, imagen, precio, stock).

Los simples ÚNICOS (grupo de 1) NO se tocan.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Any

from services import woocommerce

log = logging.getLogger("omnicanal.variables")

# ── Diccionarios de sufijo → valor (del pipeline original) ──────────────────────
COLOR_MAP = {
    "NEG": "Negro", "BLN": "Blanco", "BLA": "Blanco", "GRI": "Gris", "ROJ": "Rojo",
    "AZL": "Azul", "VER": "Verde", "AMA": "Amarillo", "ROS": "Rosa", "MOR": "Morado",
    "NAR": "Naranja", "CAF": "Café", "BEI": "Beige", "DOR": "Dorado", "PLA": "Plateado",
    "MET": "Metálico", "VIN": "Vino", "LIL": "Lila", "BRO": "Bronce", "COR": "Coral",
    "CRE": "Crema", "TUR": "Turquesa", "TRA": "Transparente", "OLI": "Olivo",
    "NUD": "Nude", "ESM": "Esmeralda", "FUC": "Fucsia",
    "AZLMAR": "Azul Marino", "AZLCLA": "Azul Claro", "AZLOSC": "Azul Oscuro",
    "AZLREY": "Azul Rey", "AZLM": "Azul Marino", "AZLC": "Azul Claro",
    "GRIOSC": "Gris Oscuro", "GRIC": "Gris Claro", "GRICLA": "Gris Claro",
    "CAFCLA": "Café Claro", "CAFOSC": "Café Oscuro", "ROSCLA": "Rosa Claro",
    "VERCLA": "Verde Claro", "VEROSC": "Verde Oscuro", "MAR": "Marino",
    "MUL": "Multicolor", "EST": "Estampado",
    "VINO": "Vino", "NEGRO": "Negro", "GRIS": "Gris", "AZUL": "Azul", "ROSA": "Rosa",
    "ORO": "Dorado", "BRONCE": "Bronce", "OLIVO": "Olivo", "ROSOSC": "Rosa Oscuro",
    "ROSCL": "Rosa Claro", "AMACLA": "Amarillo Claro", "AZLTUR": "Azul Turquesa",
    "CELEST": "Celeste", "TURQ": "Turquesa", "OPALINA": "Opalina", "HOLOG": "Holográfico",
    "MULPAST": "Multicolor Pastel", "YEL": "Amarillo", "OLV": "Olivo",
}
TALLA_ROPA_MAP = {
    "XS": "XS", "S": "S", "M": "M", "L": "L", "XL": "XL", "XXL": "XXL",
    "2XL": "2XL", "3XL": "3XL", "4XL": "4XL", "5XL": "5XL", "XXXL": "3XL",
    "G": "Grande", "EG": "Extra Grande", "UNITALLA": "Unitalla", "TU": "Unitalla",
    "6T": "6 años", "8T": "8 años", "10T": "10 años", "12T": "12 años",
}
TALLA_GEN_MAP = {
    "80CM": "80 cm", "90CM": "90 cm", "100CM": "100 cm", "110CM": "110 cm",
    "120CM": "120 cm", "130CM": "130 cm", "140CM": "140 cm", "150CM": "150 cm",
    "160CM": "160 cm", "2M": "2 m", "3M": "3 m",
}
LADO_MAP = {"DER": "Derecho", "IZQ": "Izquierdo", "TRA": "Trasero", "FRON": "Frontal"}
VOLTAJE_MAP = {
    "5V": "5 V", "9V": "9 V", "10V": "10 V", "12V": "12 V", "21V": "21 V",
    "24V": "24 V", "48V": "48 V", "110V": "110 V", "220V": "220 V", "400V": "400 V",
}
MEDIDA_CM_MAP = {str(n): f"{n} cm" for n in
    [80, 90, 100, 105, 110, 115, 120, 125, 130, 140, 150, 155, 160, 165, 180, 200, 220]}

_COLORES = set(COLOR_MAP)
_TROPA = set(TALLA_ROPA_MAP)
_TCALZ = {str(n) for n in range(14, 50)}
_TGEN = set(TALLA_GEN_MAP)
_LADO = set(LADO_MAP)
_VOLT = set(VOLTAJE_MAP)
_MEDIDA = set(MEDIDA_CM_MAP)

DIM_LABEL = {"color": "Color", "talla_ropa": "Talla", "talla_calz": "Talla",
             "talla_gen": "Talla", "lado": "Lado", "voltaje": "Voltaje",
             "medida_cm": "Medida", "modelo": "Modelo"}


def _classify(t: str) -> str:
    t = t.upper().strip()
    if t in _COLORES: return "color"
    if t in _TROPA: return "talla_ropa"
    if t in _TCALZ: return "talla_calz"
    if t in _TGEN: return "talla_gen"
    if t in _LADO: return "lado"
    if t in _VOLT: return "voltaje"
    if t in _MEDIDA: return "medida_cm"
    if re.match(r"^\d+$", t) and 14 <= int(t) <= 49: return "talla_calz"
    if re.match(r"^\d+$", t) and 50 <= int(t) <= 300: return "medida_cm"
    return "modelo"


def _normalize(token: str, dim: str) -> str:
    t = token.upper().strip()
    maps = {"color": COLOR_MAP, "talla_ropa": TALLA_ROPA_MAP, "talla_gen": TALLA_GEN_MAP,
            "lado": LADO_MAP, "voltaje": VOLTAJE_MAP, "medida_cm": MEDIDA_CM_MAP}
    if dim in maps:
        return maps[dim].get(t, token)
    if dim == "talla_calz":
        return token  # 14-49 tal cual
    return token.title()  # modelo


def parse_sku(sku: str) -> dict[str, Any]:
    """{parent, attributes:{Color, Talla,...}}. parent = primeros 2 segmentos."""
    parts = (sku or "").strip().split("-")
    if len(parts) < 2:
        return {"parent": sku, "attributes": {}, "suffixes": []}
    parent = f"{parts[0]}-{parts[1]}"
    attrs: dict[str, str] = {}
    for tok in parts[2:]:
        dim = _classify(tok)
        label = DIM_LABEL[dim]
        val = _normalize(tok, dim)
        attrs[label] = f"{attrs[label]} / {val}" if label in attrs else val
    return {"parent": parent, "attributes": attrs, "suffixes": parts[2:]}


# ── Conversión de un grupo a producto variable ──────────────────────────────────

_CAMPOS = ("id,sku,name,type,status,regular_price,sale_price,stock_quantity,"
           "weight,dimensions,images,meta_data")


def _elegir_padre(prods: list[dict]) -> dict:
    return max(prods, key=lambda p: (len(p.get("images") or []), p.get("stock_quantity") or 0))


async def _crear_variacion(cli, padre_id: int, p: dict, attrs: dict[str, str]) -> bool:
    """POST una variación bajo `padre_id` con los datos del simple `p`."""
    vdata: dict[str, Any] = {
        "sku": p.get("sku"), "manage_stock": True,
        "stock_quantity": p.get("stock_quantity") or 0,
        "weight": p.get("weight") or "", "dimensions": p.get("dimensions") or {},
        "attributes": [{"name": lbl, "option": val} for lbl, val in attrs.items()],
        "status": "publish",
    }
    if p.get("regular_price"): vdata["regular_price"] = p["regular_price"]
    if p.get("sale_price"): vdata["sale_price"] = p["sale_price"]
    imgs_p = p.get("images") or []
    if imgs_p and imgs_p[0].get("id"):
        vdata["image"] = {"id": imgs_p[0]["id"]}
    for m in (p.get("meta_data") or []):
        if m.get("key") in ("costo", "_purchase_price", "cost_price"):
            vdata.setdefault("meta_data", []).append({"key": m["key"], "value": m["value"]})
    rv = await cli.post(f"/products/{padre_id}/variations", json=vdata, timeout=90.0)
    if rv.status_code in (200, 201):
        return True
    log.warning("variación %s falló HTTP %d: %s", p.get("sku"), rv.status_code, rv.text[:100])
    return False


async def convertir_grupo(cli, base: str, wc_ids: list[int]) -> dict[str, Any]:
    """Convierte los simples de `wc_ids` en un producto variable `base`."""
    # 1. traer datos completos de cada miembro
    r = await cli.get("/products", params={
        "include": ",".join(map(str, wc_ids)), "status": "any",
        "per_page": len(wc_ids), "_fields": _CAMPOS})
    r.raise_for_status()
    prods = r.json()
    if len(prods) < 2:
        return {"base": base, "ok": False, "motivo": "menos de 2 miembros vivos"}

    parsed = {p["id"]: parse_sku(p.get("sku") or "") for p in prods}

    # ¿ya hay un padre variable? Puede estar ENTRE los miembros (draft a medias)
    # o EXISTIR APARTE con sku=base en otro status (inprogress/publish/ready) —
    # ese no viene en el índice de drafts, así que lo buscamos por sku=base.
    padre_var = next((p for p in prods if p.get("type") == "variable"), None)
    if not padre_var:
        rb = await cli.get("/products", params={"sku": base, "status": "any",
                                                "_fields": "id,sku,type"})
        existente = next((x for x in (rb.json() if rb.status_code == 200 else [])
                          if x.get("type") == "variable"), None)
        if existente:
            padre_var = existente  # colgamos los simples draft a este padre
    if padre_var:
        simples = [p for p in prods if p["id"] != padre_var["id"] and p.get("type") != "variable"]
        if not simples:
            return {"base": base, "ok": True, "saltado": "ya variable, sin simples sueltos"}
        creadas = 0
        for p in simples:
            await cli.delete(f"/products/{p['id']}", params={"force": "true"}, timeout=60.0)
            await asyncio.sleep(0.3)
            if await _crear_variacion(cli, padre_var["id"], p, parsed[p["id"]]["attributes"]):
                creadas += 1
            await asyncio.sleep(0.4)
        return {"base": base, "ok": True, "modo": "attach", "padre_wc_id": padre_var["id"],
                "variaciones_agregadas": creadas}

    # opciones por dimensión (para el atributo del padre)
    dim_opts: dict[str, list[str]] = defaultdict(list)
    for p in prods:
        for label, val in parsed[p["id"]]["attributes"].items():
            if val not in dim_opts[label]:
                dim_opts[label].append(val)
    if not dim_opts:
        return {"base": base, "ok": False, "motivo": "sin atributos parseables"}

    padre = _elegir_padre(prods)

    # imágenes del padre: dedup del padre + primera de cada variante
    img_ids, imgs = set(), []
    for p in [padre] + [x for x in prods if x["id"] != padre["id"]]:
        for im in (p.get("images") or [])[:1]:
            if im.get("id") and im["id"] not in img_ids:
                img_ids.add(im["id"]); imgs.append({"id": im["id"]})

    attrs_padre = [{"name": lbl, "options": opts, "visible": True, "variation": True}
                   for lbl, opts in dim_opts.items()]

    # 2. PUT: convertir el padre a variable
    payload = {"type": "variable", "sku": base, "name": padre.get("name") or base,
               "manage_stock": False, "attributes": attrs_padre, "images": imgs}
    rp = await cli.put(f"/products/{padre['id']}", json=payload, timeout=120.0)
    if rp.status_code != 200:
        return {"base": base, "ok": False, "motivo": f"PUT padre HTTP {rp.status_code}: {rp.text[:120]}"}

    # 3. DELETE los demás simples (libera SKUs)
    for p in prods:
        if p["id"] != padre["id"]:
            await cli.delete(f"/products/{p['id']}", params={"force": "true"}, timeout=60.0)
            await asyncio.sleep(0.3)

    # 4. POST una variación por cada miembro (incluye el que era padre)
    creadas = 0
    for p in prods:
        if await _crear_variacion(cli, padre["id"], p, parsed[p["id"]]["attributes"]):
            creadas += 1
        await asyncio.sleep(0.4)

    return {"base": base, "ok": True, "padre_wc_id": padre["id"],
            "variaciones": creadas, "atributos": dict(dim_opts)}


def base_de(sku: str) -> str:
    """Base del SKU (primeros 2 segmentos): TEC-0002-NEG → TEC-0002."""
    parts = (sku or "").strip().split("-")
    return f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else (sku or "").strip()


async def agrupar_bases(bases: set[str], pausa: float = 0.5) -> dict[str, Any]:
    """
    Agrupa SOLO las bases dadas (para el sync: agrupa lo recién creado, y si la
    base ya es un padre variable, cuelga los nuevos simples en modo ATTACH).
    Lee los drafts actuales de esas bases directo de MySQL/WC.
    """
    if not bases:
        return {"grupos_procesados": 0, "convertidos": 0}
    from services import wp_db
    drafts = wp_db.indice_drafts() if wp_db.disponible() else await woocommerce.indice_candidatos()
    grupos: dict[str, list[int]] = defaultdict(list)
    for d in drafts:
        b = base_de(d.get("sku") or "")
        if b in bases:
            grupos[b].append(d["wc_id"])
    objetivo = [(b, ids) for b, ids in grupos.items() if len(ids) >= 2]

    resultados = []
    async with woocommerce._client() as cli:
        for base, ids in objetivo:
            try:
                res = await convertir_grupo(cli, base, ids)
            except Exception as exc:  # noqa: BLE001
                res = {"base": base, "ok": False, "motivo": str(exc)[:150]}
            resultados.append(res)
            await asyncio.sleep(pausa)
    ok = sum(1 for r in resultados if r.get("ok") and not r.get("saltado"))
    return {"grupos_procesados": len(resultados), "convertidos": ok, "detalle": resultados}


async def agrupar(limite: int | None = None, pausa: float = 1.0) -> dict[str, Any]:
    """
    Agrupa TODOS los drafts en productos variables (grupos ≥2). `limite` corta
    el número de grupos (para pruebas). Idempotente: salta los ya convertidos.
    """
    from services import wp_db
    drafts = wp_db.indice_drafts() if wp_db.disponible() else await woocommerce.indice_candidatos()
    grupos: dict[str, list[int]] = defaultdict(list)
    for d in drafts:
        sku = (d.get("sku") or "").strip()
        parts = sku.split("-")
        base = f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else sku
        grupos[base].append(d["wc_id"])
    multi = {b: ids for b, ids in grupos.items() if len(ids) >= 2}
    objetivo = list(multi.items())
    if limite:
        objetivo = sorted(objetivo, key=lambda x: len(x[1]))[:limite]  # los más chicos primero

    resultados = []
    async with woocommerce._client() as cli:
        for base, ids in objetivo:
            try:
                res = await convertir_grupo(cli, base, ids)
            except Exception as exc:  # noqa: BLE001
                res = {"base": base, "ok": False, "motivo": str(exc)[:150]}
            resultados.append(res)
            log.info("grupo %s → %s", base, res)
            await asyncio.sleep(pausa)

    ok = sum(1 for r in resultados if r.get("ok") and not r.get("saltado"))
    return {"grupos_procesados": len(resultados), "convertidos": ok, "detalle": resultados}
