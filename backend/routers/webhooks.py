"""
webhooks.py — Receptores de notificaciones (webhooks) de los marketplaces.

Mercado Libre envía un POST a la URL de callback cada vez que cambia un recurso
(item, orden, etc.). Hay que responder 200 rápido (< 500 ms) y procesar aparte.

  POST /api/webhooks/ml         → receptor de notificaciones de Mercado Libre
  GET  /api/webhooks/ml         → responde 200 (para probar accesibilidad)
  GET  /api/webhooks/ml/log     → últimos eventos recibidos (para pruebas)

Cuerpo típico de ML:
  { "resource": "/items/MLM123", "user_id": 123, "topic": "items",
    "application_id": 456, "attempts": 1, "sent": "...", "received": "..." }
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from services import inventario

log = logging.getLogger("omnicanal.webhooks")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Buffer en memoria con los últimos eventos (para ver las pruebas en vivo).
_ULTIMOS: deque[dict[str, Any]] = deque(maxlen=50)


async def _procesar_ml(payload: dict[str, Any]) -> None:
    """Procesa la notificación en segundo plano (tras responder 200)."""
    topic = payload.get("topic")
    resource = payload.get("resource") or ""
    resultado: dict[str, Any] = {"topic": topic, "resource": resource}
    try:
        if topic in ("items", "items_prices", "stock_locations") and "/items/" in resource:
            item_id = resource.rsplit("/", 1)[-1]
            resultado.update(await inventario.refrescar_ml_item_id(item_id))
        elif topic == "orders_v2":
            # Una venta cambia el stock: refrescamos los ítems de la orden.
            resultado["nota"] = "orden recibida (refresco de ítems pendiente de mapear)"
        else:
            resultado["nota"] = f"topic '{topic}' ignorado"
    except Exception as exc:  # noqa: BLE001
        resultado["error"] = str(exc)
    # Guardar el resultado en el log para verlo en /log
    for e in _ULTIMOS:
        if e.get("resource") == resource and e.get("_pendiente"):
            e.update(resultado)
            e["_pendiente"] = False
            break
    log.info("Webhook ML procesado: %s", resultado)


@router.post("/ml")
async def recibir_ml(request: Request, background: BackgroundTasks):
    """Recibe la notificación de Mercado Libre. Responde 200 de inmediato."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    _ULTIMOS.appendleft({
        "recibido": datetime.now(timezone.utc).isoformat(),
        "topic": payload.get("topic"),
        "resource": payload.get("resource"),
        "user_id": payload.get("user_id"),
        "_pendiente": True,
    })
    # Procesar aparte para responder rápido (ML reintenta si tardas).
    background.add_task(_procesar_ml, payload)
    return {"ok": True}


@router.get("/ml")
async def ping_ml():
    """Prueba de accesibilidad (ML valida que la URL responda)."""
    return {"ok": True, "servicio": "webhook Mercado Libre", "listo": True}


@router.get("/ml/log")
async def log_ml():
    """Últimas notificaciones recibidas (para ver tus pruebas en vivo)."""
    return {"total": len(_ULTIMOS), "eventos": list(_ULTIMOS)}
