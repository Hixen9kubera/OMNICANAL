"""
publicar.py — Paso 4: actualizar la publicación en el canal seleccionado.

Fase 1: Mercado Libre. Dos operaciones:
  • preview(req)   → arma y DEVUELVE el payload que se enviaría. NO escribe nada.
  • confirmar(req) → ejecuta el update EN VIVO y registra el resultado en ml_backlog.

Actualiza:
  • título + atributos → PUT /items/{item_id}
  • descripción        → PUT /items/{item_id}/description  (POST si es nueva)

Los nombres de atributos de WooCommerce (BRAND, COLOR, MODEL…) coinciden con los
IDs de atributo de Mercado Libre, así que se mapean directo a {id, value_name}.
El token se lee de ml_tokens (lo mantiene fresco el pipeline externo).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx

from services import db, meli

log = logging.getLogger("omnicanal.publicar")

_ML = "https://api.mercadolibre.com"
ML_TITULO_MAX = 60


def _plain(texto: str | None) -> str:
    """HTML → texto plano (la descripción de ML es plain_text)."""
    if not texto:
        return ""
    t = re.sub(r"<\s*br\s*/?\s*>", "\n", texto, flags=re.IGNORECASE)
    t = re.sub(r"</\s*p\s*>", "\n\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _payload_ml(campos: dict[str, Any]) -> tuple[str, list[dict], str]:
    title = (campos.get("titulo") or "").strip()
    attrs = [
        {"id": a["nombre"], "value_name": str(a["valor"]).strip()}
        for a in (campos.get("atributos") or [])
        if a.get("nombre") and str(a.get("valor") or "").strip()
    ]
    desc = _plain(campos.get("descripcion"))
    return title, attrs, desc


def preview(req: dict[str, Any]) -> dict[str, Any]:
    """Arma la vista previa del payload. NO escribe nada."""
    canal = req.get("canal")
    if canal != "mercado_libre":
        return {"ok": False, "motivo": "Por ahora solo Mercado Libre (fase 1)."}

    campos = req.get("campos") or {}
    title, attrs, desc = _payload_ml(campos)

    avisos: list[str] = []
    if not req.get("item_id"):
        avisos.append("Este producto no parece publicado en esta cuenta (falta item_id): no se puede actualizar.")
    if len(title) > ML_TITULO_MAX:
        avisos.append(f"El título tiene {len(title)} caracteres (Mercado Libre permite máx {ML_TITULO_MAX}).")
    if not title and not attrs and not desc:
        avisos.append("No hay nada que actualizar (título, atributos y descripción vacíos).")

    return {
        "ok": True,
        "canal": canal,
        "cuenta": req.get("cuenta"),
        "item_id": req.get("item_id"),
        "sku": req.get("sku"),
        "operaciones": {
            "titulo": bool(title),
            "atributos": len(attrs),
            "descripcion": bool(desc),
        },
        "payload_item": {**({"title": title} if title else {}), **({"attributes": attrs} if attrs else {})},
        "descripcion": desc,
        "avisos": avisos,
    }


def _error_ml(resp: dict[str, Any]) -> str | None:
    if not isinstance(resp, dict):
        return None
    causas = resp.get("cause") or []
    msgs = [c.get("message") for c in causas if isinstance(c, dict) and c.get("message")]
    if msgs:
        return " · ".join(msgs)[:500]
    return (resp.get("message") or resp.get("error"))


def _guardar_backlog(cuenta, sku, wc_id, item_id, success, error, ml_status, desc_status, payload, ml_response) -> None:
    """Registra el intento en ml_backlog (misma tabla que el pipeline)."""
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO ml_backlog
                   (run_key, cuenta, sku, wc_id, ml_item_id, ml_url, success, error,
                    ml_status, desc_status, pics_preuploaded, payload, ml_response,
                    published_at, gtin_error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,0)""",
                (
                    f"studio:{cuenta}:{sku}", cuenta or "", sku or "", wc_id, item_id, None,
                    1 if success else 0, error, ml_status, desc_status,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(ml_response, ensure_ascii=False)[:65000],
                    datetime.now() if success else None,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo guardar en ml_backlog: %s", exc)


async def confirmar(req: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta el update EN VIVO en Mercado Libre y registra en ml_backlog."""
    canal = req.get("canal")
    if canal != "mercado_libre":
        return {"ok": False, "motivo": "Por ahora solo Mercado Libre (fase 1)."}

    cuenta = req.get("cuenta")
    item_id = req.get("item_id")
    sku = req.get("sku")
    wc_id = req.get("wc_id")
    if not item_id:
        return {"ok": False, "motivo": "No hay item_id: el producto no está publicado en esa cuenta."}

    title, attrs, desc = _payload_ml(req.get("campos") or {})
    token = meli._access_token(cuenta)
    if not token:
        return {"ok": False, "motivo": f"No hay token disponible para la cuenta {cuenta}."}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload_item: dict[str, Any] = {}
    if title:
        payload_item["title"] = title
    if attrs:
        payload_item["attributes"] = attrs

    ml_status: int | None = None
    desc_status: int | None = None
    ml_response: dict[str, Any] = {}
    error: str | None = None

    async with httpx.AsyncClient(timeout=30.0) as cli:
        # 1) título + atributos
        if payload_item:
            r = await cli.put(f"{_ML}/items/{item_id}", json=payload_item, headers=headers)
            ml_status = r.status_code
            try:
                ml_response = r.json()
            except Exception:  # noqa: BLE001
                ml_response = {"text": r.text[:500]}
            if r.status_code not in (200, 201):
                error = _error_ml(ml_response) or f"HTTP {r.status_code}"

        # 2) descripción (solo si el paso anterior no falló)
        if desc and not error:
            rd = await cli.put(f"{_ML}/items/{item_id}/description", json={"plain_text": desc}, headers=headers)
            desc_status = rd.status_code
            if rd.status_code == 404:
                rd = await cli.post(f"{_ML}/items/{item_id}/description", json={"plain_text": desc}, headers=headers)
                desc_status = rd.status_code
            if desc_status not in (200, 201):
                error = error or f"Descripción: HTTP {desc_status}"

    success = (ml_status in (200, 201) or ml_status is None) and error is None
    # Si no hubo nada que enviar, no es éxito ni error real:
    if not payload_item and not desc:
        return {"ok": False, "motivo": "No había nada que actualizar."}

    _guardar_backlog(
        cuenta, sku, wc_id, item_id, success, error, ml_status, desc_status,
        {"title": title, "attributes": attrs, "description": desc}, ml_response,
    )

    return {
        "ok": bool(success),
        "canal": canal,
        "item_id": item_id,
        "ml_status": ml_status,
        "desc_status": desc_status,
        "error": error,
        "ml_response": None if success else ml_response,
        "registrado_en": "ml_backlog",
    }
