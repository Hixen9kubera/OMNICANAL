"""
publicar_ready.py — Adaptador entre OMNICANAL y el pipeline vendorizado.

El código que publicó 1200+ productos vive intacto en `backend/vendor/`. Este
módulo es lo único nuevo: traduce nuestro mundo (lecturas de `wp_db` + las
ediciones que el usuario hizo en el Studio) al dict `prod` con forma de producto
de WooCommerce REST que `publisher_core.build_payload` espera, e inyecta los
tokens de `ml_tokens` y la persistencia en `ml_backlog` / `ml_progress`.

No reimplementa reglas de negocio: gold_pro, me2, EMPTY_GTIN_REASON, sale_terms,
SIZE_GRID_ID, pre-upload de imágenes, cadena de GTIN y todos los reintentos
salen de `vendor/ml_ready/`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from services import db, meli, wp_db
from vendor.amazon_ready import attribute_mapper as amz_mapper
from vendor.ml_ready import ml_api, publisher_core

log = logging.getLogger("omnicanal.publicar_ready")

_CUENTAS_ML = publisher_core.ML_CUENTAS  # ["SANCORFASHION", "BEKURA"]

_configurado = False


# ── Cableado del pipeline vendorizado ────────────────────────────────────────

def _backlog_ml(backlog_key: str, entry: dict[str, Any]) -> None:
    """
    Gancho `save_backlog` del publisher: escribe en `ml_backlog` y refleja el
    alta en `ml_progress` (que es lo que lee el estado del Studio).

    `backlog_key` viene como "CUENTA:SKU"; `entry` es el dict que arma su código.
    """
    resultado = entry.get("result") or {}
    success = bool(resultado.get("success"))
    cuenta = entry.get("cuenta") or ""
    sku = backlog_key.split(":", 1)[-1] if ":" in backlog_key else backlog_key
    item_id = entry.get("ml_item_id")
    wc_id = entry.get("wc_id")

    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO ml_backlog
                   (run_key, cuenta, sku, wc_id, ml_item_id, ml_url, success, error,
                    ml_status, desc_status, pics_preuploaded, payload, ml_response,
                    published_at, gtin_error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    f"studio:{cuenta}:{sku}", cuenta, sku, wc_id, item_id,
                    entry.get("ml_url"),
                    1 if success else 0,
                    resultado.get("error"),
                    entry.get("ml_status"),
                    entry.get("desc_status"),
                    entry.get("pics_preuploaded") or 0,
                    json.dumps(entry.get("payload"), ensure_ascii=False, default=str),
                    json.dumps(entry.get("ml_response"), ensure_ascii=False, default=str)[:65000],
                    datetime.now() if success else None,
                    1 if resultado.get("gtin_error") else 0,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo guardar en ml_backlog (%s): %s", backlog_key, exc)

    if success and item_id:
        try:
            with db.get_cursor() as cur:
                cur.execute(
                    """INSERT INTO ml_progress
                       (prog_key, cuenta, sku, wc_id, ml_item_id, success, published_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, 1, NOW(), NOW())
                       ON DUPLICATE KEY UPDATE ml_item_id=VALUES(ml_item_id), wc_id=VALUES(wc_id),
                           success=1, published_at=NOW(), updated_at=NOW()""",
                    (f"{cuenta}:{sku}", cuenta, sku, wc_id, item_id),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo actualizar ml_progress (%s): %s", backlog_key, exc)


def configurar() -> None:
    """Inyecta tokens y persistencia en el pipeline vendorizado (idempotente)."""
    global _configurado
    if _configurado:
        return
    ml_api.configurar_tokens(
        get_token_fn=meli._access_token,
        refresh_token_fn=meli.refrescar_token,
    )
    # save_gtin_fn queda sin definir: escribir en WooCommerce REST devuelve 403
    # por el CDN de Hostinger. El GTIN encontrado se usa igual en la publicación,
    # solo no se persiste de vuelta en WC.
    publisher_core.configurar(save_backlog_fn=_backlog_ml)
    _configurado = True


# ── Construcción del `prod` con forma WooCommerce ────────────────────────────

def _html_a_plano(html: str) -> str:
    """Copia de wc_api._html_to_plain (publicaciones_ready)."""
    if not html:
        return ''
    text = re.sub(r'<li[^>]*>', '- ', html)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<p[^>]*>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&nbsp;', ' ').replace('&#8211;', '-').replace('&#8212;', '-')
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def construir_prod(sku: str, wc_id: int, campos: dict[str, Any]) -> dict[str, Any]:
    """
    Reconstruye el dict que devuelve `wc_api.parse_product()`, leyendo de la BD
    de WordPress en vez de la REST de Woo (bloqueada por el CDN con 403).

    Lo que el usuario editó en el Studio (`campos`) pisa lo que hay en WooCommerce.
    """
    wc_id = int(wc_id)
    post = wp_db.producto_wp(wc_id) or {}
    meta = wp_db.postmeta_todo(wc_id)
    atributos = wp_db.atributos_wc(wc_id)

    titulo = (campos.get("titulo") or "").strip() or (post.get("post_title") or "")

    descripcion = (campos.get("descripcion") or "").strip()
    if not descripcion:
        descripcion = post.get("post_content") or post.get("post_excerpt") or ""
    descripcion = _html_a_plano(descripcion)

    precio = _f(campos.get("precio_regular")) or _f(meta.get("_regular_price")) or _f(meta.get("_price"))

    stock = meta.get("_stock_odoo") or meta.get("_stock")
    try:
        stock = int(float(stock)) if stock not in (None, "") else 0
    except (TypeError, ValueError):
        stock = 0

    def _dim(campo: str, clave: str) -> str:
        v = campos.get(campo)
        if v not in (None, ""):
            return str(v)
        return str(meta.get(clave) or "")

    # Atributos WC en la forma que espera build_attributes: {nombre_lower: primer_valor}.
    wc_attrs = {a["name"].lower(): a["options"][0] for a in atributos if a.get("options")}
    # Lo que el usuario editó en el Studio tiene prioridad.
    for a in (campos.get("atributos") or []):
        nombre, valor = a.get("nombre"), str(a.get("valor") or "").strip()
        if nombre and valor:
            wc_attrs[nombre.lower()] = valor

    return {
        "wc_id":            wc_id,
        "sku":              sku,
        "title":            titulo,
        "price":            precio,
        "description":      descripcion,
        "images":           wp_db.imagenes(wc_id),
        "weight":           _dim("peso", "_weight"),
        "length":           _dim("largo", "_length"),
        "width":            _dim("ancho", "_width"),
        "height":           _dim("alto", "_height"),
        "stock":            stock,
        "ml_category_id":   meta.get("ml_category_id", "") or "",
        "ml_category_name": meta.get("ml_categoria_path", "") or "",
        "wc_categories":    wp_db.categorias_wc(wc_id),
        "ml_attrs":         {k[len("ml_attr_"):]: v for k, v in meta.items()
                             if k.startswith("ml_attr_") and v},
        "wc_attrs":         wc_attrs,
        # Amazon hace `t.get("name")` sobre cada tag: van como dicts, no strings.
        "tags":             wp_db.tags_wc(wc_id),
        "meta":             meta,
        # Forma REST para el mapper de Amazon (_extract_pa_attrs lee prod['attributes'])
        "attributes":       atributos,
        "regular_price":    precio,
        "name":             titulo,
        "short_description": _html_a_plano(post.get("post_excerpt") or ""),
        "stock_quantity":   stock,
        "dimensions":       {"length": _dim("largo", "_length"),
                             "width":  _dim("ancho", "_width"),
                             "height": _dim("alto", "_height")},
    }


# ── Mercado Libre: preview (dry-run) y creación real ─────────────────────────

async def preview_crear_ml(sku: str, wc_id: int, campos: dict[str, Any],
                           cuenta: str) -> dict[str, Any]:
    """
    Payload exacto que se enviaría a `POST /items`, sin llamar a ML para crear.

    `dry_run=True` evita el pre-upload de imágenes (que sí hace la publicación
    real), así la vista previa es rápida.
    """
    configurar()
    prod = await asyncio.to_thread(construir_prod, sku, wc_id, campos)
    token = meli._access_token(cuenta)
    if not token:
        return {"ok": False, "motivo": f"Sin token de Mercado Libre para {cuenta}."}

    payload = await asyncio.to_thread(
        publisher_core.build_payload, prod, token, True, cuenta
    )
    if payload is None:
        faltantes = []
        if not prod["ml_category_id"]:
            faltantes.append("categoría ML (`ml_category_id`)")
        if not prod["title"]:
            faltantes.append("título")
        if prod["price"] <= 0:
            faltantes.append("precio")
        return {"ok": False, "motivo": "Faltan datos: " + ", ".join(faltantes)}

    return {"ok": True, "cuenta": cuenta, "payload": payload, "prod": prod}


async def crear_ml(sku: str, wc_id: int, campos: dict[str, Any]) -> dict[str, Any]:
    """
    Crea la publicación en AMBAS cuentas usando `publisher_core.publish_product`,
    con pre-upload de imágenes, cadena de GTIN y todos sus reintentos.
    """
    configurar()
    prod = await asyncio.to_thread(construir_prod, sku, wc_id, campos)

    resultados: list[dict[str, Any]] = []
    for cuenta in _CUENTAS_ML:
        token = meli._access_token(cuenta)
        if not token:
            resultados.append({"cuenta": cuenta, "item_id": "", "ok": False,
                               "error": f"Sin token para {cuenta}", "ml_status": None})
            continue
        try:
            r = await asyncio.to_thread(
                publisher_core.publish_product, dict(prod), token, False, cuenta
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("publish_product reventó (%s / %s)", cuenta, sku)
            r = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        resultados.append({
            "cuenta":    cuenta,
            "item_id":   r.get("ml_item_id") or "",
            "ok":        bool(r.get("success")),
            "error":     r.get("error"),
            "ml_status": r.get("ml_status"),
            "url":       r.get("ml_url"),
        })

    return {
        "ok": all(r["ok"] for r in resultados),
        "canal": "mercado_libre",
        "modo": "crear",
        "resultados": resultados,
        "registrado_en": "ml_backlog",
    }


# ── Amazon: atributos con su mapper ──────────────────────────────────────────

def cachear_schema_amazon(product_type: str, schema: dict[str, Any]) -> None:
    """
    Guarda el JSON Schema del productType donde su `attribute_mapper` lo busca.

    `_resolve_unit_count_type()` lee `data/schemas/{productType}.json` para sacar
    el enum válido de `unit_count.type`. Sin el archivo cae a ("unidad", "es_MX"),
    que es justo el valor que Amazon rechazó antes. Nuestro backend ya baja el
    schema en vivo desde SP-API, así que lo dejamos aquí para que lo lea.
    """
    try:
        destino: Path = amz_mapper.SCHEMAS_DIR
        destino.mkdir(parents=True, exist_ok=True)
        (destino / f"{product_type}.json").write_text(
            json.dumps(schema, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo cachear el schema de %s: %s", product_type, exc)


async def atributos_amazon(sku: str, wc_id: int, campos: dict[str, Any], mp: str,
                           product_type: str | None,
                           schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Atributos de Amazon vía `build_payload_attributes` (su mapper, verbatim)."""
    if schema and product_type:
        await asyncio.to_thread(cachear_schema_amazon, product_type, schema)
    prod = await asyncio.to_thread(construir_prod, sku, wc_id, campos)

    # El Studio manda bullets/highlights ya redactados por la IA: pisan los
    # que `_build_bullets` genera a partir del nombre/color/material.
    attrs = await asyncio.to_thread(
        amz_mapper.build_payload_attributes, prod, sku, mp, product_type
    )
    bullets = [b for b in (campos.get("bullets") or []) if str(b).strip()]
    if bullets:
        attrs["bullet_point"] = [
            {"value": str(b)[:500], "language_tag": "es_MX", "marketplace_id": mp}
            for b in bullets[:5]
        ]
    return attrs
