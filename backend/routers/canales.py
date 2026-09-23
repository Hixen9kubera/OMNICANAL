"""
canales.py — Registro de canales y refresco contra la API en vivo.

  GET  /api/canales
       → config de las pestañas (id, label, color, habilitado, totales).
         General trae DOS totales (sin borradores y catálogo completo): ver
         `listar_canales`.

  POST /api/canales/{canal}/refrescar/{sku}
       → refresca en vivo precio/stock/FULL/categoría de ese SKU contra la API
         del marketplace (Mercado Libre o Amazon) — el "botón refrescar".
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from core.marketplaces import Canal, lista_canales, subcuentas
from models.schemas import CanalInfo, SubCuentaInfo
from services import amazon, meli, woocommerce

log = logging.getLogger("omnicanal.routers.canales")
router = APIRouter(prefix="/api/canales", tags=["canales"])


async def _total_general(vista: str | None) -> int | None:
    """Total de UN listado de General, best-effort: si falla, None («—» en el
    panel), nunca 0. Es la misma llamada que pinta la pestaña, con una fila
    por página: solo interesa el total.

    `vista=None` deja la vista por defecto de `listar_productos` — la de la
    pestaña General."""
    extra = {} if vista is None else {"vista": vista}
    try:
        _, total, _ = await woocommerce.listar_productos(page=1, per_page=1, **extra)
        return total
    except Exception as exc:  # noqa: BLE001
        log.warning("Total de General (vista=%s) no disponible: %s",
                    vista or "por defecto", exc)
        return None


@router.get("", response_model=list[CanalInfo])
async def listar_canales(incluir_totales: bool = True):
    """
    Config de las pestañas con su total.

    General lleva DOS totales, y NO son el mismo número (el 23-sep-2026 el
    panel decía 3,035 en la pestaña y 7,288 en el encabezado de Omnicanal, y
    nadie sabía por qué):

    - `total_productos` — lo que cuenta la pestaña General: vista «productos»
      (publish/pending/ready/private), SIN borradores. Medido en producción:
      publish 1,996 + pending 914 + ready 128 = 3,038 (3,035 en vivo).
    - `total_catalogo` — lo que cuenta el encabezado de Omnicanal y su «Todas»:
      vista «omnicanal», TODOS los estados. Los mismos 3,038 + 4,250 draft
      = 7,288. La diferencia es todo lo que queda fuera de la vista
      «productos» (draft, inprogress…); medido, hoy son solo draft. El
      frontend la calcula solo si los dos son números.

    En ninguno cuentan las variantes: van dentro del padre. Los dos siguen el
    mismo `LISTADO_APLANADO` que el encabezado (aquí no se pasa `aplanar`),
    así que si alguien lo enciende cambian de unidad juntos.

    Los dos se piden en paralelo y cada uno por su cuenta: si uno falla queda
    en None y el otro sigue. Los demás canales no llevan `total_catalogo`.

    Los conteos de los demás canales leen la base con funciones síncronas: van
    en `asyncio.to_thread` (regla 11), uno tras otro como antes.
    """
    salida: list[CanalInfo] = []
    # Totales de General (best-effort, en paralelo)
    total_general = total_catalogo = None
    if incluir_totales:
        total_general, total_catalogo = await asyncio.gather(
            _total_general(None), _total_general("omnicanal"),
        )

    for cfg in lista_canales():
        total = None
        catalogo = None
        subs: list[SubCuentaInfo] = []
        if cfg["id"] == Canal.GENERAL.value:
            total = total_general
            catalogo = total_catalogo
        elif cfg["id"] == Canal.MERCADO_LIBRE.value:
            total = (await asyncio.to_thread(meli.contar_publicados)
                     if incluir_totales else None)
            for s in subcuentas(cfg["id"]):
                subs.append(SubCuentaInfo(
                    **s,
                    total_productos=(await asyncio.to_thread(meli.contar_publicados, s["id"])
                                     if incluir_totales else None),
                ))
        elif cfg["id"] == Canal.AMAZON.value:
            total = (await asyncio.to_thread(amazon.contar_publicados)
                     if incluir_totales else None)
        elif cfg["id"] == Canal.TIKTOK.value:
            # A LA VENTA (ACTIVATE), no "existe": de las 900 publicaciones, 599
            # son borradores. Poner 900 en la pestaña sería prometer catálogo
            # que nadie puede comprar.
            from services import tiktok_panel
            total = (await asyncio.to_thread(tiktok_panel.contar_publicados)
                     if incluir_totales else None)
        elif cfg["id"] == Canal.TEMU.value:
            # Las 160 que existen en Temu. Cuántas de ellas se venden no se
            # puede afirmar todavía: Temu devuelve el estado como número y solo
            # dos de sus siete códigos están verificados (ver temu_panel).
            from services import temu_panel
            total = (await asyncio.to_thread(temu_panel.contar_publicados)
                     if incluir_totales else None)
        elif cfg["id"] == Canal.WALMART.value:
            from services import walmart_panel
            total = (await asyncio.to_thread(walmart_panel.contar_publicados)
                     if incluir_totales else None)
        salida.append(CanalInfo(**cfg, total_productos=total,
                                total_catalogo=catalogo, subcuentas=subs))
    return salida


@router.post("/{canal}/refrescar/{sku:path}")
async def refrescar(canal: str, sku: str, cuenta: str | None = None):
    """
    Refresca un SKU en vivo contra TODOS los canales y devuelve el inventario
    actualizado. Tolerante a fallos (un canal caído no rompe la respuesta).
    """
    from services import inventario
    res = await inventario.sincronizar_sku(sku)
    inv = (await asyncio.to_thread(inventario.leer_inventario, [sku])).get(sku, {})
    return {"sku": sku, "ok": res.get("ok", True),
            "actualizados": res.get("actualizados", 0),
            "inventario": {k: dict(v) for k, v in inv.items()}}
