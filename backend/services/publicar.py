"""
publicar.py — Paso 4: actualizar/publicar en el canal seleccionado.

  • preview(req)   → arma y DEVUELVE lo que se enviaría. NO escribe nada.
  • confirmar(req) → ejecuta el cambio EN VIVO y lo registra en la bitácora.

Mercado Libre → PUT /items/{id} (título+atributos) + PUT /items/{id}/description.
                Registro en ml_backlog. Token de ml_tokens.
Amazon        → PATCH /listings/2021-08-01/items/{seller}/{sku}
                (item_name, bullet_point, product_description). Registro en
                amazon_backlog. product_type de amazon_progress. Token LWA (SP-API).
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import datetime
from typing import Any

import httpx

from config import settings
from services import db, meli

log = logging.getLogger("omnicanal.publicar")

_ML = "https://api.mercadolibre.com"
ML_TITULO_MAX = 60
AMZ_TITULO_MAX = 200


def _plain(texto: str | None) -> str:
    if not texto:
        return ""
    t = re.sub(r"<\s*br\s*/?\s*>", "\n", texto, flags=re.IGNORECASE)
    t = re.sub(r"</\s*p\s*>", "\n\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _ml_publicaciones(sku: str | None) -> list[dict[str, Any]]:
    """
    Cuentas de Mercado Libre donde el SKU está publicado, con su item_id.
    Fuente: ml_progress (una fila por cuenta:sku). Se publica a TODAS (BEKURA y
    San Corpe) — el catálogo de Kubera vive en ambas.
    """
    if not sku:
        return []
    try:
        rows = db.fetch_all(
            """SELECT cuenta, ml_item_id FROM ml_progress
               WHERE sku=%s AND ml_item_id IS NOT NULL AND ml_item_id <> ''""",
            (sku,),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("_ml_publicaciones(%s): %s", sku, exc)
        return []
    por_cuenta: dict[str, str] = {}
    for r in rows:
        por_cuenta[r["cuenta"]] = r["ml_item_id"]  # última gana
    return [{"cuenta": c, "item_id": i} for c, i in por_cuenta.items()]


# ═══════════════════════════ VISTA PREVIA ═══════════════════════════════════
def preview(req: dict[str, Any]) -> dict[str, Any]:
    canal = req.get("canal")
    if canal == "mercado_libre":
        return _preview_ml(req)
    if canal == "amazon":
        return _preview_amazon(req)
    return {"ok": False, "canal": canal, "motivo": "Canal no soportado todavía (ML y Amazon)."}


def _preview_ml(req: dict[str, Any]) -> dict[str, Any]:
    campos = req.get("campos") or {}
    title = (campos.get("titulo") or "").strip()
    attrs = [
        {"id": a["nombre"], "value_name": str(a["valor"]).strip()}
        for a in (campos.get("atributos") or [])
        if a.get("nombre") and str(a.get("valor") or "").strip()
    ]
    desc = _plain(campos.get("descripcion"))
    pubs = _ml_publicaciones(req.get("sku"))
    cuentas = [p["cuenta"] for p in pubs]

    avisos: list[str] = []
    if not pubs:
        avisos.append("Este producto no está publicado en ninguna cuenta de Mercado Libre.")
    elif len(pubs) == 1:
        avisos.append(f"Solo está publicado en {cuentas[0]} (la otra cuenta no lo tiene aún).")
    if len(title) > ML_TITULO_MAX:
        avisos.append(f"El título tiene {len(title)} caracteres (Mercado Libre máx {ML_TITULO_MAX}).")
    return {
        "ok": True, "canal": "mercado_libre", "sku": req.get("sku"),
        "cuentas": cuentas, "publicaciones": pubs,
        "titulo": title or None, "descripcion": desc or None,
        "cambios": [{"etiqueta": a["id"], "valor": a["value_name"]} for a in attrs],
        "operaciones": {"titulo": bool(title), "atributos": len(attrs), "descripcion": bool(desc)},
        "avisos": avisos,
    }


def _product_type_amazon(sku: str | None) -> str | None:
    if not sku:
        return None
    try:
        row = db.fetch_one(
            """SELECT product_type FROM amazon_progress
               WHERE sku=%s AND product_type IS NOT NULL
               ORDER BY updated_at DESC LIMIT 1""",
            (sku,),
        )
        return (row or {}).get("product_type")
    except Exception as exc:  # noqa: BLE001
        log.warning("_product_type_amazon(%s): %s", sku, exc)
        return None


def _preview_amazon(req: dict[str, Any]) -> dict[str, Any]:
    campos = req.get("campos") or {}
    sku = req.get("sku")
    pt = _product_type_amazon(sku)
    title = (campos.get("titulo") or "").strip()
    bullets = [b.strip() for b in (campos.get("bullets") or []) if b and b.strip()]
    desc = _plain(campos.get("descripcion"))
    avisos: list[str] = []
    if not pt:
        avisos.append("Sin product_type en amazon_progress: el producto no está publicado en Amazon (actualizar requiere publicación previa).")
    if not req.get("item_id"):
        avisos.append("Sin ASIN: el producto no parece publicado en Amazon.")
    if len(title) > AMZ_TITULO_MAX:
        avisos.append(f"El título tiene {len(title)} caracteres (Amazon máx {AMZ_TITULO_MAX}).")
    return {
        "ok": True, "canal": "amazon", "sku": sku, "item_id": req.get("item_id"),
        "product_type": pt,
        "titulo": title or None, "descripcion": desc or None,
        "cambios": [{"etiqueta": f"Bullet {i + 1}", "valor": b} for i, b in enumerate(bullets)],
        "operaciones": {"titulo": bool(title), "bullets": len(bullets), "descripcion": bool(desc)},
        "avisos": avisos,
    }


# ═══════════════════════════ CONFIRMAR (EN VIVO) ═════════════════════════════
async def confirmar(req: dict[str, Any]) -> dict[str, Any]:
    canal = req.get("canal")
    if canal == "mercado_libre":
        return await _confirmar_ml(req)
    if canal == "amazon":
        return await _confirmar_amazon(req)
    return {"ok": False, "motivo": "Canal no soportado todavía (ML y Amazon)."}


def _error_ml(resp: dict[str, Any]) -> str | None:
    if not isinstance(resp, dict):
        return None
    causas = resp.get("cause") or []
    msgs = [c.get("message") for c in causas if isinstance(c, dict) and c.get("message")]
    if msgs:
        return " · ".join(msgs)[:500]
    return resp.get("message") or resp.get("error")


def _guardar_backlog_ml(cuenta, sku, wc_id, item_id, success, error, ml_status, desc_status, payload, ml_response):
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO ml_backlog
                   (run_key, cuenta, sku, wc_id, ml_item_id, ml_url, success, error,
                    ml_status, desc_status, pics_preuploaded, payload, ml_response,
                    published_at, gtin_error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,0)""",
                (f"studio:{cuenta}:{sku}", cuenta or "", sku or "", wc_id, item_id, None,
                 1 if success else 0, error, ml_status, desc_status,
                 json.dumps(payload, ensure_ascii=False),
                 json.dumps(ml_response, ensure_ascii=False)[:65000],
                 datetime.now() if success else None),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo guardar en ml_backlog: %s", exc)


async def _update_ml_una(cuenta: str, item_id: str, title: str, attrs: list[dict],
                         desc: str) -> dict[str, Any]:
    """Actualiza UNA publicación de ML (una cuenta). Devuelve el resultado."""
    token = meli._access_token(cuenta)
    if not token:
        return {"cuenta": cuenta, "item_id": item_id, "ok": False,
                "error": f"Sin token para {cuenta}", "ml_status": None, "desc_status": None}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload_item: dict[str, Any] = {}
    if title:
        payload_item["title"] = title
    if attrs:
        payload_item["attributes"] = attrs

    ml_status = desc_status = None
    ml_response: dict[str, Any] = {}
    error: str | None = None
    async with httpx.AsyncClient(timeout=30.0) as cli:
        if payload_item:
            r = await cli.put(f"{_ML}/items/{item_id}", json=payload_item, headers=headers)
            ml_status = r.status_code
            try:
                ml_response = r.json()
            except Exception:  # noqa: BLE001
                ml_response = {"text": r.text[:500]}
            if r.status_code not in (200, 201):
                error = _error_ml(ml_response) or f"HTTP {r.status_code}"
        if desc and not error:
            rd = await cli.put(f"{_ML}/items/{item_id}/description", json={"plain_text": desc}, headers=headers)
            desc_status = rd.status_code
            if rd.status_code == 404:
                rd = await cli.post(f"{_ML}/items/{item_id}/description", json={"plain_text": desc}, headers=headers)
                desc_status = rd.status_code
            if desc_status not in (200, 201):
                error = error or f"Descripción: HTTP {desc_status}"

    success = (ml_status in (200, 201) or ml_status is None) and error is None
    return {"cuenta": cuenta, "item_id": item_id, "ok": bool(success), "error": error,
            "ml_status": ml_status, "desc_status": desc_status, "_response": ml_response}


async def _confirmar_ml(req: dict[str, Any]) -> dict[str, Any]:
    sku = req.get("sku")
    wc_id = req.get("wc_id")
    campos = req.get("campos") or {}
    title = (campos.get("titulo") or "").strip()
    attrs = [
        {"id": a["nombre"], "value_name": str(a["valor"]).strip()}
        for a in (campos.get("atributos") or [])
        if a.get("nombre") and str(a.get("valor") or "").strip()
    ]
    desc = _plain(campos.get("descripcion"))
    if not title and not attrs and not desc:
        return {"ok": False, "motivo": "No había nada que actualizar."}

    pubs = _ml_publicaciones(sku)
    if not pubs:
        return {"ok": False, "motivo": "No está publicado en ninguna cuenta de Mercado Libre."}

    resultados: list[dict[str, Any]] = []
    for p in pubs:
        res = await _update_ml_una(p["cuenta"], p["item_id"], title, attrs, desc)
        _guardar_backlog_ml(
            p["cuenta"], sku, wc_id, p["item_id"], res["ok"], res["error"],
            res["ml_status"], res["desc_status"],
            {"title": title, "attributes": attrs, "description": desc},
            res.pop("_response", {}),
        )
        resultados.append(res)

    ok_all = all(r["ok"] for r in resultados)
    return {"ok": ok_all, "canal": "mercado_libre", "resultados": resultados,
            "registrado_en": "ml_backlog"}


def _amazon_patches(title: str, bullets: list[str], desc: str, mp: str) -> list[dict]:
    patches: list[dict] = []
    if title:
        patches.append({"op": "replace", "path": "/attributes/item_name",
                        "value": [{"value": title, "language_tag": "es_MX", "marketplace_id": mp}]})
    if bullets:
        patches.append({"op": "replace", "path": "/attributes/bullet_point",
                        "value": [{"value": b, "language_tag": "es_MX", "marketplace_id": mp} for b in bullets]})
    if desc:
        patches.append({"op": "replace", "path": "/attributes/product_description",
                        "value": [{"value": desc, "language_tag": "es_MX", "marketplace_id": mp}]})
    return patches


def _guardar_backlog_amazon(sku, wc_id, product_type, status, success, issue_count, issues, payload, amz_response):
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO amazon_backlog
                   (sku, wc_id, seller_id, marketplace_id, submission_id, product_type,
                    status, success, issue_count, issues, payload, amz_response,
                    submitted_at, published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (sku or "", wc_id, settings.amazon_seller_id, settings.amazon_marketplace_id,
                 None, product_type, status, 1 if success else 0, issue_count,
                 json.dumps(issues, ensure_ascii=False)[:65000],
                 json.dumps(payload, ensure_ascii=False)[:65000],
                 json.dumps(amz_response, ensure_ascii=False)[:65000],
                 datetime.now(), datetime.now() if success else None),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo guardar en amazon_backlog: %s", exc)


async def _confirmar_amazon(req: dict[str, Any]) -> dict[str, Any]:
    from services import amazon as amz

    sku = req.get("sku")
    wc_id = req.get("wc_id")
    pt = _product_type_amazon(sku)
    if not pt:
        return {"ok": False, "motivo": "No hay product_type en amazon_progress (requiere publicación previa)."}
    token = await amz._access_token()
    if not token:
        return {"ok": False, "motivo": "No hay token de Amazon (SP-API). Revisa las credenciales LWA."}

    campos = req.get("campos") or {}
    title = (campos.get("titulo") or "").strip()
    bullets = [b.strip() for b in (campos.get("bullets") or []) if b and b.strip()]
    desc = _plain(campos.get("descripcion"))
    mp = settings.amazon_marketplace_id
    patches = _amazon_patches(title, bullets, desc, mp)
    if not patches:
        return {"ok": False, "motivo": "No había nada que actualizar."}

    body = {"productType": pt, "patches": patches}
    sku_enc = urllib.parse.quote(str(sku), safe="")
    seller = settings.amazon_seller_id
    amz_response: dict[str, Any] = {}
    http_status: int | None = None
    try:
        async with httpx.AsyncClient(base_url=settings.amazon_sp_api_endpoint, timeout=40.0) as cli:
            r = await cli.patch(
                f"/listings/2021-08-01/items/{seller}/{sku_enc}",
                params={"marketplaceIds": mp, "includedData": "issues"},
                headers={"x-amz-access-token": token, "Content-Type": "application/json"},
                json=body,
            )
        http_status = r.status_code
        try:
            amz_response = r.json()
        except Exception:  # noqa: BLE001
            amz_response = {"text": r.text[:500]}
        http_ok = r.status_code < 400
    except Exception as exc:  # noqa: BLE001
        amz_response = {"error": str(exc)}
        http_ok = False

    status = amz_response.get("status") or (f"HTTP {http_status}" if http_status else "ERROR")
    issues = amz_response.get("issues") or []
    errors = [i for i in issues if i.get("severity") == "ERROR"]
    success = http_ok and status == "ACCEPTED" and not errors
    error = None
    if not success:
        error = ", ".join(f"{i.get('code','')}: {i.get('message','')[:60]}" for i in errors[:3]) \
            or amz_response.get("errors") and str(amz_response["errors"])[:200] or f"status={status}"

    _guardar_backlog_amazon(sku, wc_id, pt, status, success, len(errors), issues, body, amz_response)
    return {"ok": bool(success), "canal": "amazon", "status": status,
            "issue_count": len(errors), "error": None if success else error,
            "respuesta": None if success else amz_response, "registrado_en": "amazon_backlog"}
