"""
categorias.py — Categoría de DEPARTAMENTO por prefijo de SKU.

Los drafts creados desde Odoo no traen categoría (Odoo tiene todo en "All").
Como categoría provisional se usa el departamento que codifica el prefijo del
SKU (TEC→Tecnología, ROP→Ropa…), confirmado por el usuario el 2026-07-02.
El flujo de creación la sustituye después por la categoría de Mercado Libre.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services import woocommerce

log = logging.getLogger("omnicanal.categorias")

# Prefijo de SKU → nombre de categoría en WooCommerce.
MAPEO_PREFIJO: dict[str, str] = {
    "TEC": "Tecnología", "ROP": "Ropa", "ACC": "Accesorios", "ORG": "Organización",
    "MUE": "Muebles", "COC": "Cocina", "DEC": "Decoración", "ILUM": "Iluminación",
    "HOG": "Hogar", "JUEG": "Juegos", "CORR": "Corrales", "CAM": "Camas y Sábanas",
    "MASC": "Mascotas", "BEB": "Bebés", "CALZ": "Calzado", "JUGU": "Juguetes",
    "OFI": "Oficina", "BAÑ": "Baño", "EST": "Estanterías", "VEH": "Vehículos",
    "VIA": "Viaje", "CUNA": "Cunas", "VAR": "Varios", "MES": "Mesas",
    "SIL": "Sillas", "PEL": "Peluches", "MUN": "Disfraces", "PAS": "Bebés",
    "EC": "Termos", "TEK": "Hogar", "ACO": "Deportes", "ORT": "Accesorios",
    "CEL": "Tecnología", "ELEC": "Electrónica",
    # Segunda tanda (2026-07-03), nombres derivados de los productos reales:
    "HERR": "Herramientas", "HIG": "Higiene y Limpieza", "DEPO": "Deportes",
    "COM": "Hogar", "TEX": "Textiles", "BAN": "Baño", "SEG": "Seguridad",
    "JAR": "Jardín", "ART": "Arte y Manualidades", "PAP": "Papelería",
    "EDU": "Educación", "DEP": "Salud y Belleza", "CAS": "Hogar",
    "ESCR": "Oficina", "CONS": "Consumibles", "JUG": "Juguetes",
    "BAS": "Exhibición y Maniquíes", "MAN": "Exhibición y Maniquíes",
    "MOD": "Exhibición y Maniquíes", "ALIM": "Mascotas", "LUZ": "Iluminación",
    "CART": "Varios", "MONT": "Herramientas", "VAL": "Herramientas",
    "ROBB": "Mascotas",
}

# Prefijos que no estén en el mapeo caen aquí (nadie se queda sin categoría).
CATEGORIA_FALLBACK = "Varios"

# Cache nombre (lower) → id de categoría WC
_ids_categoria: dict[str, int] = {}


def categoria_para_sku(sku: str) -> str | None:
    """Departamento según el prefijo del SKU (fallback 'Varios'; None si no hay SKU)."""
    prefijo = (sku or "").split("-")[0].strip().upper()
    if not prefijo:
        return None
    return MAPEO_PREFIJO.get(prefijo, CATEGORIA_FALLBACK)


async def asegurar_categoria(cli, nombre: str) -> int | None:
    """Devuelve el id de la categoría WC con ese nombre; la crea si no existe."""
    clave = nombre.strip().lower()
    if clave in _ids_categoria:
        return _ids_categoria[clave]
    try:
        r = await cli.get("/products/categories", params={
            "search": nombre, "per_page": 100, "_fields": "id,name",
        })
        if r.status_code == 200:
            for c in r.json():
                if c["name"].strip().lower() == clave:
                    _ids_categoria[clave] = c["id"]
                    return c["id"]
        rc = await cli.post("/products/categories", json={"name": nombre})
        if rc.status_code in (200, 201):
            _ids_categoria[clave] = rc.json()["id"]
            log.info("Categoría creada en Woo: %s (id %d)", nombre, _ids_categoria[clave])
            return _ids_categoria[clave]
        # 400 term_exists → el término ya existe (p. ej. con otro case)
        data = rc.json() if rc.headers.get("content-type", "").startswith("application/json") else {}
        existente = (data.get("data") or {}).get("resource_id")
        if existente:
            _ids_categoria[clave] = existente
            return existente
        log.warning("No se pudo crear la categoría %s: %s", nombre, rc.text[:120])
    except Exception as exc:  # noqa: BLE001
        log.warning("asegurar_categoria(%s) falló: %s", nombre, exc)
    return None


async def asignar_departamentos(pausa: float = 1.0) -> dict[str, Any]:
    """
    Asigna la categoría de departamento (por prefijo de SKU) a TODOS los drafts
    que hoy no tienen categoría en Woo. No toca productos ya categorizados.
    """
    drafts = await woocommerce.indice_candidatos()
    objetivo = [
        d for d in drafts
        if not [c for c in (d.get("categorias") or []) if c.lower() != "uncategorized"]
    ]

    asignados, sin_mapeo, errores = 0, 0, 0
    async with woocommerce._client() as cli:
        # Resolver/crear todas las categorías necesarias primero.
        pendientes: list[tuple[int, int]] = []  # (wc_id, cat_id)
        for d in objetivo:
            nombre = categoria_para_sku(d["sku"])
            if not nombre:
                sin_mapeo += 1
                continue
            cat_id = await asegurar_categoria(cli, nombre)
            if cat_id:
                pendientes.append((d["wc_id"], cat_id))
            else:
                errores += 1

        for i in range(0, len(pendientes), 50):
            lote = pendientes[i:i + 50]
            payload = {"update": [
                {"id": wc_id, "categories": [{"id": cat_id}]} for wc_id, cat_id in lote
            ]}
            try:
                r = await cli.post("/products/batch", json=payload, timeout=300.0)
                r.raise_for_status()
                asignados += sum(1 for u in r.json().get("update", []) if not u.get("error"))
            except Exception as exc:  # noqa: BLE001
                errores += len(lote)
                log.warning("asignar_departamentos: lote %d falló: %s", i // 50 + 1, exc)
            await asyncio.sleep(pausa)

    log.info("Departamentos asignados: %d (%d sin mapeo, %d errores)",
             asignados, sin_mapeo, errores)
    return {"ok": True, "asignados": asignados, "sin_mapeo": sin_mapeo, "errores": errores}
