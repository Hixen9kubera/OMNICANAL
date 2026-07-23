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
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from services import db, meli, wp_db
from vendor.amazon_ready import attribute_mapper as amz_mapper
from vendor.ml_ready import ml_api, publisher_core

log = logging.getLogger("omnicanal.publicar_ready")

_CUENTAS_ML = publisher_core.ML_CUENTAS  # ["SANCORFASHION", "BEKURA"]

# Intentos por cuenta al crear en ML (cubre fallos transitorios; los errores
# deterministas de configuración cortan antes — ver crear_ml).
MAX_INTENTOS_ML = 3

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
            backlog_id = cur.lastrowid
        # Espejo kubera: resumen del envío (los blobs payload/response NO viajan).
        from services import kubera_mirror
        kubera_mirror.espejar(
            "services/publicar_ready.py", "_backlog_ml",
            "ml_backlog", "ops.channel_submissions", "INSERT",
            {"canal": "mercado_libre", "cuenta": cuenta, "sku": sku,
             "submission_id": item_id, "operacion": "alta",
             "status": entry.get("ml_status"), "success": success,
             "error_resumen": resultado.get("error"),
             "detail_ref": f"mysql:ml_backlog:{backlog_id}" if backlog_id else None,
             "submitted_at": datetime.now(),
             "published_at": datetime.now() if success else None},
            clave=f"{cuenta}:{sku}")
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


# ── Garantía de que la publicación quede PAUSADA ─────────────────────────────
#
# `POST /items` IGNORA el `status: paused` del payload: Mercado Libre crea el
# item ACTIVO salvo en categorías de catálogo. Medido sobre ml_backlog: de las
# creaciones con HTTP 201, 2670 respondieron `active` y solo 310 `paused`, aun
# cuando el payload llevaba `paused` en el 100% de los casos.
#
# Lo único que las pausa es el `pause_item()` posterior, y su resultado no se
# verificaba ni se registraba: si ML lo rechazaba (p. ej. mientras el item está
# en `picture_download_pending`) o había timeout, la publicación se quedaba
# activa en silencio. Aquí se verifica contra ML y se reintenta.

_INTENTOS_PAUSA = 4


def _estado_item(item_id: str, token: str) -> tuple[str | None, list[str]]:
    """(status, sub_status) del item según ML, o (None, []) si no se pudo leer."""
    try:
        r = requests.get(
            f"{ml_api.ML_API_BASE}/items/{item_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"attributes": "status,sub_status"},
            timeout=15,
        )
        if r.status_code != 200:
            return None, []
        j = r.json()
        sub = j.get("sub_status") or []
        return j.get("status"), sub if isinstance(sub, list) else [sub]
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer el estado de %s: %s", item_id, exc)
        return None, []


def asegurar_pausado(item_id: str, token: str) -> dict[str, Any]:
    """
    Deja el item en `paused`, verificándolo contra ML. Devuelve el desenlace.

    Bloqueante (usa `requests`): llamar con `asyncio.to_thread`.
    """
    http_pausa: int | None = None
    for intento in range(_INTENTOS_PAUSA):
        status, sub = _estado_item(item_id, token)
        if status == "paused":
            return {"pausado": True, "status": status, "sub_status": sub,
                    "intentos": intento, "http_pausa": http_pausa}
        http_pausa = ml_api.pause_item(item_id, token)
        log.info("pause_item(%s) intento %d → HTTP %s (status previo=%s, sub=%s)",
                 item_id, intento + 1, http_pausa, status, sub)
        # ML tarda en aceptar el cambio mientras descarga las imágenes.
        time.sleep(1.5 * (intento + 1))

    status, sub = _estado_item(item_id, token)
    pausado = status == "paused"
    if not pausado:
        log.error("El item %s quedó en status=%r (sub=%s) tras %d intentos de pausa",
                  item_id, status, sub, _INTENTOS_PAUSA)
    return {"pausado": pausado, "status": status, "sub_status": sub,
            "intentos": _INTENTOS_PAUSA, "http_pausa": http_pausa}


def _anotar_pausa_backlog(item_id: str, pausa: dict[str, Any]) -> None:
    """Deja constancia en `ml_backlog` cuando el item NO pudo pausarse."""
    if pausa.get("pausado"):
        return
    aviso = (f"NO_PAUSADO: quedó status={pausa.get('status')!r} "
             f"sub_status={pausa.get('sub_status')} tras {pausa.get('intentos')} intentos "
             f"(último pause_item → HTTP {pausa.get('http_pausa')})")
    try:
        with db.get_cursor() as cur:
            cur.execute(
                "UPDATE ml_backlog SET error=%s WHERE ml_item_id=%s ORDER BY id DESC LIMIT 1",
                (aviso, item_id),
            )
        # Espejo kubera: la pausa fallida queda como evento de submission.
        from services import kubera_mirror
        kubera_mirror.espejar(
            "services/publicar_ready.py", "_anotar_pausa_backlog",
            "ml_backlog", "ops.channel_submissions", "UPDATE",
            {"canal": "mercado_libre", "submission_id": item_id,
             "operacion": "pausa", "status": pausa.get("status"),
             "success": False, "error_resumen": aviso,
             "submitted_at": datetime.now()},
            clave=item_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo anotar la pausa en ml_backlog (%s): %s", item_id, exc)


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

    # Categoría ML: hay DOS escritores con llaves distintas. `ml_categoria_id`
    # la guarda el selector del PANEL (elección humana); `ml_category_id` la
    # guarda el predictor de Crear Productos. La humana MANDA — caso
    # TEC-1812-NEG: el predictor eligió "Máquinas de Coser" y el panel decía
    # "Máquinas Sexuales"; se publicó en coser por leer solo la del predictor.
    cat_panel = str(meta.get("ml_categoria_id") or "").strip()
    cat_crear = str(meta.get("ml_category_id") or "").strip()
    cat_id = cat_panel or cat_crear
    cat_nombre = str(meta.get("ml_categoria_path") or "")
    if cat_panel:
        try:  # nombre legible desde los niveles que guarda el picker del panel
            niveles = json.loads(meta.get("ml_categoria_niveles") or "[]")
            if niveles:
                cat_nombre = " > ".join(n.get("name", "") for n in niveles)
        except Exception:  # noqa: BLE001
            pass

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
        "ml_category_id":   cat_id,
        "ml_category_name": cat_nombre,
        # Con categoría elegida en el panel NO se pasan las categorías WC:
        # publisher_core prefiere el mapeo "ML: MLM###" de la categoría de Woo
        # sobre la meta, y eso revertía la elección del picker (caso
        # CAM-0034-BEI: el panel decía Colchones Inflables MLM69819 y se publicó
        # en Colchonetas Aislantes MLM419960 por la categoría WC del producto).
        # Sin elección en el panel, el mapeo WC sigue siendo el fallback.
        "wc_categories":    [] if cat_id else wp_db.categorias_wc(wc_id),
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


async def crear_ml(sku: str, wc_id: int, campos: dict[str, Any],
                   cuentas: list[str] | None = None) -> dict[str, Any]:
    """
    Crea la publicación usando `publisher_core.publish_product`, con pre-upload
    de imágenes, cadena de GTIN y todos sus reintentos.

    Por omisión crea en AMBAS cuentas; `cuentas` la restringe (p. ej. re-crear
    solo donde la publicación anterior fue eliminada en ML).
    """
    configurar()
    prod = await asyncio.to_thread(construir_prod, sku, wc_id, campos)

    resultados: list[dict[str, Any]] = []
    for cuenta in (cuentas or _CUENTAS_ML):
        token = meli._access_token(cuenta)
        if not token:
            resultados.append({"cuenta": cuenta, "item_id": "", "ok": False,
                               "error": f"Sin token para {cuenta}", "ml_status": None})
            continue
        # Reintentos: hasta MAX_INTENTOS_ML por cuenta. Es RARO que una cuenta
        # publique y la otra no (SANCORFASHION fallaba donde BEKURA lograba); un
        # fallo transitorio (timeout, 5xx, token en transición) se supera al
        # reintentar. NO se reintentan los errores DETERMINISTAS de configuración
        # (GTIN real requerido / config manual): el mismo payload fallará igual y
        # solo spamearía a ML — esos necesitan acción humana, no otro intento.
        r = {"success": False}
        intentos = 0
        for intento in range(1, MAX_INTENTOS_ML + 1):
            intentos = intento
            try:
                r = await asyncio.to_thread(
                    publisher_core.publish_product, dict(prod), token, False, cuenta
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("publish_product reventó (%s / %s, intento %d)", cuenta, sku, intento)
                r = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
            if r.get("success"):
                break
            if r.get("gtin_error") or r.get("needs_manual_config"):
                log.info("publish %s/%s: error determinista (%s) — no se reintenta",
                         cuenta, sku, r.get("error"))
                break
            if intento < MAX_INTENTOS_ML:
                log.warning("publish %s/%s falló (intento %d/%d): %s — reintentando",
                            cuenta, sku, intento, MAX_INTENTOS_ML, r.get("error"))
                await asyncio.sleep(2.0 * intento)  # backoff 2s, 4s

        item_id = r.get("ml_item_id") or ""
        fila: dict[str, Any] = {
            "cuenta":    cuenta,
            "item_id":   item_id,
            "ok":        bool(r.get("success")),
            "error":     r.get("error"),
            "ml_status": r.get("ml_status"),
            "url":       r.get("ml_url"),
            "intentos":  intentos,
        }

        # Toda alta debe quedar PAUSADA. ML ignora el `status` del POST, así que
        # se verifica y reintenta, y se avisa si no se logró.
        if fila["ok"] and item_id:
            pausa = await asyncio.to_thread(asegurar_pausado, item_id, token)
            fila["pausado"] = pausa["pausado"]
            fila["estado_ml"] = pausa["status"]
            if not pausa["pausado"]:
                await asyncio.to_thread(_anotar_pausa_backlog, item_id, pausa)
                fila["aviso"] = (
                    f"La publicación se creó pero quedó {pausa['status'] or 'en estado desconocido'}: "
                    f"páusala a mano en Mercado Libre ({item_id})."
                )

        resultados.append(fila)

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
                           schema: dict[str, Any] | None = None,
                           preparar_imagenes: bool = True) -> dict[str, Any]:
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

    # Imágenes: Amazon las ingiere por URL pública (las MISMAS que usa ML). El
    # mapper del vendor no las agrega, así que se inyectan aquí (en el adaptador):
    # 1 principal + hasta 8 secundarias (Amazon admite 9). Son atributos estándar
    # del schema, así que sobreviven el filtro de _amazon_attrs_final.
    #
    # Antes de enviarlas se dejan "Amazon-ready" (≥1000 px en el lado más largo,
    # RGB, JPEG) — ver services/imagenes_amazon. Las que ya cumplen NO se tocan.
    # En la VISTA PREVIA se llama con preparar_imagenes=False para no subir medios.
    imgs = [u for u in (prod.get("images") or []) if u][:9]
    if imgs and preparar_imagenes:
        try:
            from services import imagenes_amazon
            imgs, _avisos = await imagenes_amazon.preparar_para_amazon(sku, imgs)
        except Exception as exc:  # noqa: BLE001
            log.warning("preparar imágenes Amazon (%s): %s — se envían las originales", sku, exc)
    if imgs:
        attrs["main_product_image_locator"] = [
            {"media_location": imgs[0], "marketplace_id": mp}
        ]
        for i, url in enumerate(imgs[1:9], start=1):
            attrs[f"other_product_image_locator_{i}"] = [
                {"media_location": url, "marketplace_id": mp}
            ]
    return attrs
