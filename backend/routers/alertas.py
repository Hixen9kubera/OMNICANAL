"""
alertas.py — El detalle de una alerta, calculado AL VUELO.

  GET /api/alertas/margen-negativo    → las publicaciones que hoy pierden dinero
  GET /api/alertas/costo-sin-validar  → los más vendidos con el costo sin verificar

POR QUÉ NO HAY TABLA. Se decidió que el panel muestra **el estado de HOY**, no
el seguimiento (Eduardo, 7-sep-2026). Guardar la lista obligaría a mantenerla al
día y a contestar "¿esto sigue siendo cierto?" cada vez que alguien la abre —
y una lista guardada que envejece es exactamente la trampa que ya costó los 964
pedidos fantasma: *una foto detenida contesta con seguridad lo que ya no sabe*.
Preguntando en vivo, la respuesta no puede quedar vieja.

DE DÓNDE SALE. De `services/alertas.censo_margen()` y `censo_top_sin_costo()`,
las MISMAS que usa la alarma diaria. No hay una segunda consulta: si el aviso de
Slack dice "8 en margen negativo", esta pantalla enseña esas 8. Cuando la alarma
y la pantalla arman su propia lista, tarde o temprano se contradicen, y quien
las lee deja de creerle a las dos.

CUÁNTO CUESTA. `censo_margen` recorre las publicaciones comprables de los cinco
canales y `censo_top_sin_costo` corre tres veces el SQL del top. No es gratis, y
por eso esto NO se llama al pintar la campana: se llama cuando alguien abre la
alerta, que es una vez y a propósito.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from services import alertas

log = logging.getLogger("omnicanal.routers.alertas")
router = APIRouter(prefix="/api/alertas", tags=["alertas"])


@router.get("/margen-negativo")
async def margen_negativo() -> dict[str, Any]:
    """Las publicaciones de SKUs con costo validado que HOY están en margen negativo."""
    censo = await asyncio.to_thread(alertas.censo_margen)
    if censo is None:
        raise HTTPException(503, "No se pudo leer el censo de márgenes.")

    # Peor primero: quien abre esto quiere saber por dónde empezar.
    negativas = sorted(censo["negativas"], key=lambda x: x["margen_pct"])
    items = [{
        "sku": n["sku"],
        "canal": n["canal"],
        "tienda": n["tienda"],
        "titulo": n.get("titulo"),
        "margen_pct": n["margen_pct"],
        "precio": n["precio"],
        "costo": n["costo"],
        # `dudoso` separa dos problemas que piden acciones OPUESTAS: en la
        # pérdida real hay que mover el precio o bajar la publicación; en el
        # costo dudoso hay que revisar el COSTEO — bajarla sería tapar un error
        # de captura vendiendo menos.
        "dudoso": bool(n["dudoso"]),
        "veces_precio": round(n["costo"] / n["precio"], 1) if n["precio"] else None,
    } for n in negativas]

    return {
        "items": items,
        "total": len(items),
        "perdida_real": sum(1 for i in items if not i["dudoso"]),
        "costo_dudoso": sum(1 for i in items if i["dudoso"]),
        # El universo viaja SIEMPRE: "0 en negativo" sobre 2 evaluadas de 781 no
        # significa lo mismo que sobre 781 de 781.
        "evaluadas": censo["evaluadas"],
        "universo": censo["universo"],
        "skus": [i["sku"] for i in items],
    }


@router.get("/costo-sin-validar")
async def costo_sin_validar() -> dict[str, Any]:
    """Los más vendidos cuyo costo NO está verificado hoy."""
    top = await asyncio.to_thread(alertas.censo_top_sin_costo)
    if top is None:
        raise HTTPException(503, "No se pudo leer el ranking de más vendidos.")

    # Mismo criterio que la alarma: "sin verificar" incluye la revisión que
    # quedó ATRÁS — si la fila se movió después de marcarse, esa marca ya no
    # cubre los números de hoy.
    items = [{
        "sku": sku,
        "rank": d["rn"],
        "unidades": d["uds"],
        "donde": " y ".join(d["pestanas"]) if len(d["pestanas"]) > 1 else d["donde"],
        # Distingue "nunca se revisó" de "se revisó y la fila cambió después".
        # No es lo mismo para quien lo va a atender.
        "motivo": "revisión desactualizada" if d["revisado"] and d["movida"]
                  else "sin revisar",
    } for sku, d in sorted(top.items(), key=lambda kv: kv[1]["rn"])
        if not d["revisado"] or d["movida"]]

    return {
        "items": items,
        "total": len(items),
        "top_total": len(top),
        "skus": [i["sku"] for i in items],
    }
