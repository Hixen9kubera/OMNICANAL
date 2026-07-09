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


# ── Estado de publicación EN VIVO (consulta las APIs, no la DB) ──────────────
_ml_user_ids: dict[str, Any] = {}


async def _ml_user_id(cuenta: str, token: str, cli: httpx.AsyncClient) -> Any:
    if cuenta in _ml_user_ids:
        return _ml_user_ids[cuenta]
    uid = None
    try:
        r = await cli.get(f"{_ML}/users/me", headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            uid = r.json().get("id")
    except Exception:  # noqa: BLE001
        pass
    _ml_user_ids[cuenta] = uid
    return uid


async def _ml_live(sku: str) -> list[dict[str, Any]]:
    """Cuentas donde el SKU tiene una publicación viva (active/paused) en ML."""
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15.0) as cli:
        for cuenta in ("BEKURA", "SANCORFASHION"):
            token = meli._access_token(cuenta)
            if not token:
                continue
            uid = await _ml_user_id(cuenta, token, cli)
            if not uid:
                continue
            try:
                r = await cli.get(
                    f"{_ML}/users/{uid}/items/search",
                    params={"seller_sku": sku, "status": "active,paused"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if r.status_code == 200:
                    res = r.json().get("results") or []
                    if res:
                        out.append({"cuenta": cuenta, "item_id": res[0]})
            except Exception:  # noqa: BLE001
                pass
    return out


async def _amazon_live(sku: str) -> dict[str, Any]:
    """¿El SKU tiene un listing vivo en Amazon? (Listings Items API)."""
    from services import amazon as amz

    token = await amz._access_token()
    if not token:
        return {"publicado": False, "asin": None, "status": None}
    enc = urllib.parse.quote(str(sku), safe="")
    try:
        async with httpx.AsyncClient(base_url=settings.amazon_sp_api_endpoint, timeout=20.0) as cli:
            r = await cli.get(
                f"/listings/2021-08-01/items/{settings.amazon_seller_id}/{enc}",
                params={"marketplaceIds": settings.amazon_marketplace_id, "includedData": "summaries"},
                headers={"x-amz-access-token": token},
            )
        if r.status_code == 200:
            summaries = r.json().get("summaries") or []
            if summaries:
                s = summaries[0]
                st = s.get("status") or []
                return {"publicado": True, "asin": s.get("asin"),
                        "status": ",".join(st) if isinstance(st, list) else st}
    except Exception:  # noqa: BLE001
        pass
    return {"publicado": False, "asin": None, "status": None}


async def estado_live(sku: str) -> dict[str, Any]:
    """
    Estado de publicación combinando EN VIVO (APIs) + REGISTRO (DB).
    Si el API en vivo no lo halla pero el registro dice publicado, se muestra
    publicado (el listing puede existir con otro SKU o estar suprimido).
    """
    from services import studio

    ml_live = await _ml_live(sku)
    amazon_live = await _amazon_live(sku)
    db_estado = studio.estado_publicacion(sku)

    # ML: unión (por cuenta) de lo vivo + el registro
    por_cuenta: dict[str, dict] = {p["cuenta"]: {**p, "fuente": "registro"} for p in db_estado.get("ml", [])}
    for p in ml_live:
        por_cuenta[p["cuenta"]] = {**p, "fuente": "vivo"}
    ml = list(por_cuenta.values())

    # Amazon: publicado si lo confirma el API en vivo O el registro
    da = db_estado.get("amazon", {}) or {}
    amazon = {
        "publicado": bool(amazon_live.get("publicado")) or bool(da.get("publicado")),
        "asin": amazon_live.get("asin") or da.get("asin"),
        "status": amazon_live.get("status") or da.get("status"),
        "fuente": "vivo" if amazon_live.get("publicado") else ("registro" if da.get("publicado") else None),
    }
    return {"ml": ml, "amazon": amazon}


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
async def preview(req: dict[str, Any]) -> dict[str, Any]:
    canal = req.get("canal")
    if canal == "mercado_libre":
        return await _preview_ml(req)
    if canal == "amazon":
        return _preview_amazon(req)
    return {"ok": False, "canal": canal, "motivo": "Canal no soportado todavía (ML y Amazon)."}


async def _payload_crear_ml(req: dict[str, Any]) -> dict[str, Any] | None:
    """Payload real de `publisher_core.build_payload` (dry-run) para la vista previa."""
    from services import publicar_ready, studio, wp_db

    sku, wc_id = req.get("sku"), req.get("wc_id")
    if not wc_id:
        wc_id = (studio.metadata(sku, None) or {}).get("wc_id")
    if not (sku and wc_id and wp_db.disponible()):
        return None
    try:
        return await publicar_ready.preview_crear_ml(
            str(sku), int(wc_id), req.get("campos") or {}, "BEKURA"
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("preview_crear_ml(%s) falló: %s", sku, exc)
        return None


async def _preview_ml(req: dict[str, Any]) -> dict[str, Any]:
    campos = req.get("campos") or {}
    title = (campos.get("titulo") or "").strip()
    attrs = [
        {"id": a["nombre"], "value_name": str(a["valor"]).strip()}
        for a in (campos.get("atributos") or [])
        if a.get("nombre") and str(a.get("valor") or "").strip()
    ]
    desc = _plain(campos.get("descripcion"))
    pubs = _ml_publicaciones(req.get("sku"))
    modo = "actualizar" if pubs else "crear"
    cuentas = [p["cuenta"] for p in pubs] if pubs else ["BEKURA", "SANCORFASHION"]

    avisos: list[str] = []
    payload: dict[str, Any] | None = None
    if not pubs:
        avisos.append("No está publicado en ML: se CREARÁ una nueva publicación (pausada) en ambas cuentas.")
        # En modo crear el payload lo arma el pipeline de publicaciones_ready.
        r = await _payload_crear_ml(req)
        if r and r.get("ok"):
            payload = r["payload"]
            n_pics = len(payload.get("pictures") or [])
            avisos.append(f"{n_pics} imagen(es) se pre-subirán a ML (escaladas a ≥500×250) al confirmar.")
        elif r and r.get("motivo"):
            avisos.append(r["motivo"])
    elif len(pubs) == 1:
        avisos.append(f"Solo está publicado en {cuentas[0]} (la otra cuenta no lo tiene aún).")
    if len(title) > ML_TITULO_MAX:
        avisos.append(f"El título tiene {len(title)} caracteres (Mercado Libre máx {ML_TITULO_MAX}).")

    return {
        "ok": True, "canal": "mercado_libre", "sku": req.get("sku"), "modo": modo,
        "cuentas": cuentas, "publicaciones": pubs,
        "titulo": title or None, "descripcion": desc or None,
        "cambios": [{"etiqueta": a["id"], "valor": a["value_name"]} for a in attrs],
        "operaciones": {"titulo": bool(title), "atributos": len(attrs), "descripcion": bool(desc)},
        "payload": payload,
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
    if not title:
        avisos.append("Falta el título.")
    if not campos.get("precio_regular"):
        avisos.append("Sin precio: revisa el precio regular antes de publicar.")
    if len(title) > AMZ_TITULO_MAX:
        avisos.append(f"El título tiene {len(title)} caracteres (Amazon máx {AMZ_TITULO_MAX}).")
    avisos.append("Publicar CREA el listing en Amazon. Si faltan atributos obligatorios de la categoría, Amazon lo rechazará y verás el motivo aquí y en amazon_backlog.")
    return {
        "ok": True, "canal": "amazon", "sku": sku,
        "product_type": pt or "(se detecta automáticamente)",
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
    # Reflejar en ml_progress (lo que lee el estado) al publicar/actualizar OK.
    if success and item_id:
        try:
            with db.get_cursor() as cur:
                cur.execute(
                    """INSERT INTO ml_progress (prog_key, cuenta, sku, wc_id, ml_item_id, success, published_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, 1, NOW(), NOW())
                       ON DUPLICATE KEY UPDATE ml_item_id=VALUES(ml_item_id), wc_id=VALUES(wc_id),
                           success=1, published_at=NOW(), updated_at=NOW()""",
                    (f"{cuenta}:{sku}", cuenta or "", sku or "", wc_id, item_id),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo actualizar ml_progress: %s", exc)


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


async def _crear_ml(sku: str, wc_id, campos: dict) -> dict[str, Any]:
    """
    Crear publicación nueva en ML → delega en el pipeline de `publicaciones_ready`
    (vendorizado en `backend/vendor/ml_ready/`), que es el que logró 1200+ altas.

    Aporta sobre la implementación anterior: pre-upload de imágenes con escalado
    a ≥500×250, cadena de GTIN (_barcode → catálogo ML → UPC Item DB), SIZE_GRID_ID
    para ropa/calzado, validación de densidad en las dims de paquete, y ~10
    reintentos específicos por código de error de ML.
    """
    from services import publicar_ready, studio

    if not wc_id:
        wc_id = (studio.metadata(sku, wc_id) or {}).get("wc_id")
    if not wc_id:
        return {"ok": False, "motivo": "Sin wc_id: no se puede leer el producto de WooCommerce."}
    if not wp_db_disponible():
        return {"ok": False, "motivo": "Sin conexión a la BD de WordPress (configura WPDB_* en Railway)."}

    return await publicar_ready.crear_ml(sku, int(wc_id), campos)


def wp_db_disponible() -> bool:
    from services import wp_db
    return wp_db.disponible()


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
        return {"ok": False, "motivo": "No había nada que enviar."}

    pubs = _ml_publicaciones(sku)
    if not pubs:
        # No está publicado en ninguna cuenta → CREAR nuevo en ambas.
        return await _crear_ml(sku, wc_id, campos)

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
    return {"ok": ok_all, "canal": "mercado_libre", "modo": "actualizar",
            "resultados": resultados, "registrado_en": "ml_backlog"}


def _attr_from(atributos: list[dict], nombre: str, default: str) -> str:
    for a in atributos or []:
        if (a.get("nombre") or "").upper() == nombre.upper() and str(a.get("valor") or "").strip():
            return str(a["valor"]).strip()
    return default


def _num(v: Any, default: float = 1.0) -> float:
    try:
        n = float(v)
        return n if n > 0 else default
    except (ValueError, TypeError):
        return default


def _amazon_attributes(sku: str, campos: dict[str, Any], mp: str) -> dict[str, Any]:
    """Atributos para crear/reemplazar el listing (Listings API, requirements=LISTING)."""
    atributos = campos.get("atributos") or []
    title = (campos.get("titulo") or "").strip()[:AMZ_TITULO_MAX] or "Producto"
    desc = (_plain(campos.get("descripcion")) or "Producto de alta calidad.")[:2000]
    bullets = [b.strip() for b in (campos.get("bullets") or []) if b and b.strip()] \
        or ["Producto de alta calidad, práctico y duradero."]
    brand = _attr_from(atributos, "BRAND", "Generic")
    color = _attr_from(atributos, "COLOR", "Multicolor")
    material = _attr_from(atributos, "MATERIAL", "Mixto")
    price = _num(campos.get("precio_regular"), 1.0)
    l_val = _num(campos.get("largo")); w_val = _num(campos.get("ancho")); h_val = _num(campos.get("alto"))

    def V(value: Any) -> list[dict]:
        return [{"value": value, "marketplace_id": mp}]

    return {
        "brand": V(brand),
        "supplier_declared_has_product_identifier_exemption": V(True),
        "item_name": V(title),
        "product_description": V(desc),
        "bullet_point": [{"value": b, "marketplace_id": mp} for b in bullets[:5]],
        "condition_type": V("new_new"),
        "manufacturer": V(brand),
        "part_number": V(sku),
        "model_name": V(sku),
        "model_number": V(sku),
        "country_of_origin": V("MX"),
        "color": V(color),
        "material": V(material),
        "supplier_declared_dg_hz_regulation": V("not_applicable"),
        "number_of_items": V(1),
        "list_price": [{"currency": "MXN", "value_with_tax": price, "marketplace_id": mp}],
        "included_components": V("1 x Producto"),
        "warranty_description": V("Garantía del vendedor"),
        "item_length_width_height": [{
            "length": {"value": l_val, "unit": "centimeters"},
            "width": {"value": w_val, "unit": "centimeters"},
            "height": {"value": h_val, "unit": "centimeters"},
            "marketplace_id": mp,
        }],
        "purchasable_offer": [{
            "currency": "MXN", "marketplace_id": mp,
            "our_price": [{"schedule": [{"value_with_tax": price}]}],
        }],
        "fulfillment_availability": [{
            "fulfillment_channel_code": "DEFAULT", "quantity": 10, "marketplace_id": mp,
        }],
    }


async def _detectar_product_type(token: str, nombre: str, mp: str) -> str:
    """Busca el productType más relevante por keywords del título. Fallback HOME."""
    kw = " ".join((nombre or "").split()[:3]) or "home"
    try:
        async with httpx.AsyncClient(base_url=settings.amazon_sp_api_endpoint, timeout=20.0) as cli:
            r = await cli.get(
                "/definitions/2020-09-01/productTypes",
                params={"keywords": kw[:50], "marketplaceIds": mp},
                headers={"x-amz-access-token": token},
            )
        if r.status_code == 200:
            tipos = r.json().get("productTypes", [])
            if tipos:
                return tipos[0]["name"]
    except Exception as exc:  # noqa: BLE001
        log.warning("_detectar_product_type: %s", exc)
    return "HOME"


# Valores seguros conocidos para atributos comunes (si el enum del esquema lo permite).
_AMZ_DEFAULTS: dict[str, Any] = {
    "supplier_declared_dg_hz_regulation": "not_applicable",
    "supplier_declared_has_product_identifier_exemption": True,
    "is_fragile": False, "batteries_required": False, "batteries_included": False,
    "is_assembly_required": False, "contains_liquid_contents": False,
    "is_expiration_dated_product": False, "country_of_origin": "MX",
    "condition_type": "new_new",
}
# Booleanos que muchas categorías exigen aunque no estén en `required` del esquema.
_AMZ_COMUNES = [
    "is_fragile", "batteries_required", "batteries_included", "is_assembly_required",
    "contains_liquid_contents", "supplier_declared_dg_hz_regulation",
    "supplier_declared_has_product_identifier_exemption",
]


async def _amazon_schema(token: str, product_type: str, mp: str) -> dict[str, Any] | None:
    """Esquema del productType (properties + required) desde SP-API Definitions."""
    try:
        async with httpx.AsyncClient(base_url=settings.amazon_sp_api_endpoint, timeout=30.0) as cli:
            r = await cli.get(
                f"/definitions/2020-09-01/productTypes/{product_type}",
                params={"marketplaceIds": mp, "requirements": "LISTING", "locale": "es_MX"},
                headers={"x-amz-access-token": token},
            )
            if r.status_code != 200:
                return None
            link = (r.json().get("schema") or {}).get("link", {}).get("resource")
            if not link:
                return None
            rs = await cli.get(link, timeout=30.0)
            if rs.status_code != 200:
                return None
            schema = rs.json()
        return {"properties": schema.get("properties", {}), "required": schema.get("required", []) or []}
    except Exception as exc:  # noqa: BLE001
        log.warning("_amazon_schema(%s): %s", product_type, exc)
        return None


def _amz_default_value(node: dict[str, Any], mp: str, attr_name: str = "", depth: int = 0) -> Any:
    """
    Construye un valor por defecto VÁLIDO para un atributo del esquema Amazon,
    de forma recursiva (arrays, objetos anidados, unidades y enums incluidos).
    """
    if depth > 6:
        return "N/A"
    enum = node.get("enum")
    if enum:
        conocido = _AMZ_DEFAULTS.get(attr_name)
        if conocido is not None and conocido in enum:
            return conocido
        return False if False in enum else enum[0]
    t = node.get("type")
    if t == "array":
        return [_amz_default_value(node.get("items", {}), mp, attr_name, depth + 1)]
    if t == "object":
        obj: dict[str, Any] = {}
        props = node.get("properties", {})
        requeridos = node.get("required") or list(props.keys())
        for k in requeridos:
            if k == "marketplace_id":
                obj[k] = mp
            elif k == "language_tag":
                obj[k] = "es_MX"
            elif k in props:
                obj[k] = _amz_default_value(props[k], mp, attr_name if k == "value" else k, depth + 1)
        if "marketplace_id" in props and "marketplace_id" not in obj:
            obj["marketplace_id"] = mp
        return obj
    if t == "boolean":
        c = _AMZ_DEFAULTS.get(attr_name)
        return c if isinstance(c, bool) else False
    if t in ("integer", "number"):
        return 1
    c = _AMZ_DEFAULTS.get(attr_name)
    return c if isinstance(c, str) else "N/A"


async def _amazon_attrs_final(sku: str, wc_id, campos: dict[str, Any], mp: str,
                              product_type: str | None,
                              schema: dict[str, Any] | None) -> dict[str, Any]:
    """
    Atributos de Amazon: los construye `build_payload_attributes` de
    `publicaciones_amazon` (vendorizado), y aquí solo se filtran contra el
    esquema real del productType y se rellenan los `required` que falten.

    Si no hay WPDB o falla la lectura, cae a `_amazon_attributes` (el mapeo
    propio) para no dejar el botón muerto.
    """
    from services import publicar_ready, wp_db

    candidatos: dict[str, Any] | None = None
    if wc_id and wp_db.disponible():
        try:
            candidatos = await publicar_ready.atributos_amazon(
                str(sku), int(wc_id), campos, mp, product_type, schema
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("build_payload_attributes falló (%s): %s — se usa el mapeo propio", sku, exc)
    if candidatos is None:
        candidatos = _amazon_attributes(str(sku), campos, mp)

    # El título y la descripción del Studio mandan sobre lo que trae WooCommerce.
    titulo = (campos.get("titulo") or "").strip()
    if titulo:
        candidatos["item_name"] = [{"value": titulo[:200], "language_tag": "es_MX", "marketplace_id": mp}]
    desc = _plain(campos.get("descripcion"))
    if desc:
        candidatos["product_description"] = [{"value": desc[:2000], "language_tag": "es_MX", "marketplace_id": mp}]

    if not schema:
        return candidatos
    props = schema["properties"]
    attrs = {k: v for k, v in candidatos.items() if k in props}
    for k in _AMZ_COMUNES + list(schema["required"]):
        if k in props and k not in attrs:
            attrs[k] = _amz_default_value(props[k], mp, k)
    return attrs


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
    # Reflejar en amazon_progress (lo que lee el estado) al publicar OK.
    if success:
        try:
            with db.get_cursor() as cur:
                cur.execute(
                    """INSERT INTO amazon_progress (sku, wc_id, seller_id, marketplace_id, product_type,
                           status, success, published_at, last_submitted, updated_at)
                       VALUES (%s, %s, %s, %s, %s, 'PUBLISHED', 1, NOW(), NOW(), NOW())
                       ON DUPLICATE KEY UPDATE wc_id=VALUES(wc_id), product_type=VALUES(product_type),
                           status='PUBLISHED', success=1, published_at=NOW(), updated_at=NOW()""",
                    (sku or "", wc_id, settings.amazon_seller_id, settings.amazon_marketplace_id, product_type),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo actualizar amazon_progress: %s", exc)


async def _confirmar_amazon(req: dict[str, Any]) -> dict[str, Any]:
    """Publica (crea/reemplaza) el listing en Amazon vía Listings API."""
    from services import amazon as amz

    sku = req.get("sku")
    wc_id = req.get("wc_id")
    campos = req.get("campos") or {}
    if not (campos.get("titulo") or "").strip():
        return {"ok": False, "motivo": "Falta el título para publicar en Amazon."}
    token = await amz._access_token()
    if not token:
        return {"ok": False, "motivo": "No hay token de Amazon (SP-API). Revisa las credenciales LWA."}

    mp = settings.amazon_marketplace_id
    seller = settings.amazon_seller_id
    pt = _product_type_amazon(sku) or await _detectar_product_type(token, campos.get("titulo") or "", mp)
    schema = await _amazon_schema(token, pt, mp)
    props = (schema or {}).get("properties", {})
    attributes = await _amazon_attrs_final(str(sku), wc_id, campos, mp, pt, schema)
    sku_enc = urllib.parse.quote(str(sku), safe="")

    body: dict[str, Any] = {}
    amz_response: dict[str, Any] = {}
    status = "ERROR"
    errors: list[dict] = []
    http_ok = False
    intentos = 0
    MAX_INTENTOS = 4

    async with httpx.AsyncClient(base_url=settings.amazon_sp_api_endpoint, timeout=60.0) as cli:
        while True:
            body = {"productType": pt, "requirements": "LISTING", "attributes": attributes}
            try:
                r = await cli.put(
                    f"/listings/2021-08-01/items/{seller}/{sku_enc}",
                    params={"marketplaceIds": mp, "includedData": "issues"},
                    headers={"x-amz-access-token": token, "Content-Type": "application/json"},
                    json=body,
                )
                http_ok = r.status_code < 400
                try:
                    amz_response = r.json()
                except Exception:  # noqa: BLE001
                    amz_response = {"text": r.text[:500]}
            except Exception as exc:  # noqa: BLE001
                amz_response = {"error": str(exc)}
                http_ok = False
                break

            status = amz_response.get("status") or f"HTTP {r.status_code}"
            errors = [i for i in (amz_response.get("issues") or []) if i.get("severity") == "ERROR"]
            if (http_ok and status == "ACCEPTED" and not errors) or intentos >= MAX_INTENTOS - 1:
                break

            # Rellenar los atributos que Amazon reporta como faltantes y reintentar.
            faltantes: set[str] = set()
            for i in errors:
                es_falta = "MISSING_ATTRIBUTE" in (i.get("categories") or []) \
                    or "requiere" in (i.get("message", "").lower())
                if es_falta:
                    for an in (i.get("attributeNames") or []):
                        if an in props and an not in attributes:
                            faltantes.add(an)
            if not faltantes:
                break
            for an in faltantes:
                attributes[an] = _amz_default_value(props[an], mp, an)
            intentos += 1

    success = http_ok and status == "ACCEPTED" and not errors
    error = None
    if not success:
        error = ", ".join(f"{i.get('code','')}: {i.get('message','')[:80]}" for i in errors[:4]) or f"status={status}"

    _guardar_backlog_amazon(sku, wc_id, pt, status, success, len(errors),
                            amz_response.get("issues") or [], body, amz_response)
    return {"ok": bool(success), "canal": "amazon", "status": status, "product_type": pt,
            "issue_count": len(errors), "error": None if success else error,
            "intentos": intentos + 1,
            "respuesta": None if success else amz_response, "registrado_en": "amazon_backlog"}
