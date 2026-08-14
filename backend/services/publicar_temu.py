"""
publicar_temu.py — Publicar en Temu DESDE EL PANEL, con vista previa.

Pieza 5. `scripts/publicar_temu.py` publica por TANDAS (el pipeline de 3 fases
que subió el lote de 201); esto es el otro camino: un producto, un botón, y el
payload a la vista antes de mandarlo. Mismo par `preview()` / `confirmar()` que
`publicar_tiktok.py`.

EL PAYLOAD ES EL DE ELLOS, A PROPÓSITO. `armar_payload` de su script ya está
verificado contra altas reales (`ACC-0017-MUL` → `608007295444247`), así que
aquí se reproduce con las mismas cinco decisiones que costaron medirlas:

  · `extCatName`, NO `catId`. **v3 ignora en silencio lo que no conoce**: se
    pidió catId 1761 y publicó en 1769, sin un solo error.
  · `basePrice` en DECIMAL de 2 cifras, no en centavos. Mandarlo en centavos es
    lo que dejó dos productos vivos a 100× su precio.
  · `packageInfo` aunque la doc lo dé por opcional: sin él Temu asume 100 g y
    10×20×30 cm — y cobra volumétrico.
  · `variations` por NOMBRE (`{"name":"Color","value":"MUL"}`). Temu acuña o
    reutiliza el `specId` solo; no hace falta `spec.id.get`, que olía a
    get-or-create.
  · `attributes` como `{name, value[]}` de TEXTO. Se validan por `vid` contra la
    plantilla (ver `temu_contenido.validar_atributos`) y se mandan por nombre.

LO QUE MÁS SE CUIDA: NO QUEMAR EL SKU
─────────────────────────────────────
Un fallo DESPUÉS de que Temu acepta el alta **quema el `externalSkuId` para
siempre** (`150010090`); borrar no lo libera. Por eso, antes de mandar nada:

  1. `out.sn.check` — si el SKU ya existe, se ABORTA y se dice cuál es su
     `goodsId`. Nunca se reintenta a ciegas.
  2. El payload se registra en la bitácora ANTES de salir, no después.

Y el candado que Temu no da: `success: true` puede venir con el fallo real
anidado en `result.success`. Eso ya lo levanta `services/temu.llamar`.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("omnicanal.publicar_temu")

CANAL = "temu"
CUENTA = "TEMU"

# La ÚNICA plantilla de flete que existe en la tienda; se llama "test". Omitir
# `costTemplate` no ayuda: Temu aplica la default de la tienda, que es la misma.
PLANTILLA_FLETE = "LFT-18510029444014331627"
DIAS_DESPACHO = 2
TITULO_MAX = 500
DESC_MAX = 2000
MAX_IMAGENES = 5


def _medida(valor: Any, defecto: float, tope: float) -> str:
    """Una medida saneada, como cadena. Un 0 en Woo se vuelve el default."""
    try:
        f = round(float(valor or 0), 1)
    except (TypeError, ValueError):
        f = 0.0
    return f"{min(max(f or defecto, 0.1), tope):g}"


def _contenido(sku: str) -> dict[str, Any]:
    """Lo que la IA dejó guardado para Temu en `enrich.channel_content`."""
    from services import supabase_db as sdb
    try:
        filas = sdb.fetch_all(
            """select contenido from enrich.channel_content
                where sku = %(sku)s::citext and canal = %(canal)s limit 1""",
            {"sku": sku, "canal": CANAL})
        return (filas or [{}])[0].get("contenido") or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("publicar_temu._contenido(%s): %s", sku, exc)
        return {}


async def _armar(req: dict[str, Any]) -> dict[str, Any]:
    """El payload y sus avisos. NO sube imágenes ni llama a Temu."""
    import asyncio

    sku = str(req.get("sku") or "").strip()
    if not sku:
        raise RuntimeError("Falta el SKU.")
    campos = dict(req.get("campos") or {})
    wc_id = req.get("wc_id")
    avisos: list[str] = []

    # Imágenes, precio, stock y medidas salen de Woo por el MISMO camino que ML,
    # Amazon y TikTok: lo editado en el Studio pisa lo que trae Woo.
    prod: dict[str, Any] = {}
    if wc_id:
        try:
            from services import publicar_ready, wp_db
            if wp_db.disponible():
                prod = await asyncio.to_thread(
                    publicar_ready.construir_prod, sku, int(wc_id), campos)
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"No se pudo leer el producto de Woo: {exc}")
    campos.setdefault("imagenes", prod.get("images") or [])
    for k, origen in (("precio_regular", "price"), ("peso", "weight"),
                      ("largo", "length"), ("ancho", "width"),
                      ("alto", "height"), ("stock", "stock")):
        if not campos.get(k) and prod.get(origen):
            campos[k] = prod[origen]

    # El contenido con IA manda sobre el de Woo: es el que se escribió PARA Temu.
    guardado = await asyncio.to_thread(_contenido, sku)
    titulo = (campos.get("titulo") or guardado.get("titulo")
              or prod.get("title") or "").strip()
    descripcion = (campos.get("descripcion") or guardado.get("descripcion")
                   or prod.get("description") or "").strip()
    bullets = campos.get("bullets") or guardado.get("bullets") or []
    atributos = campos.get("atributos") or guardado.get("atributos") or []

    if not titulo:
        raise RuntimeError("Falta el título.")
    if not atributos:
        avisos.append("Sin atributos: Temu los autocompleta y el producto suele "
                      "caer en Borrador. Genera el contenido con IA primero.")

    # La categoría: la de su publicación si ya existe, o la que eligió el panel.
    from services import temu_ia
    cat_id, cat_ruta = await asyncio.to_thread(temu_ia._categoria, sku)  # noqa: SLF001
    cat_id = str(campos.get("categoria_id") or cat_id or "").strip()
    if not cat_id:
        raise RuntimeError("Sin categoría de Temu. Elígela antes de publicar: "
                           "la categoría decide qué atributos existen.")

    precio = campos.get("precio_regular")
    try:
        precio = float(precio or 0)
    except (TypeError, ValueError):
        precio = 0.0
    if precio <= 0:
        raise RuntimeError("Sin precio: Temu rechaza el alta (150011003 basePrice).")
    if precio < 10:
        # No se bloquea, se AVISA: `basePrice` es lo que cobramos nosotros, y un
        # precio roto en Woo se convierte en vender a pérdida sin que nada falle.
        avisos.append(f"Precio muy bajo (${precio:.2f}). En Temu `basePrice` es "
                      f"lo que NOSOTROS cobramos: revisa que no venga roto de Woo.")

    stock = int(campos.get("stock") or 0)
    if stock < 1:
        raise RuntimeError("Sin stock: publicar sin piezas es una venta que se "
                           "cancela, y las cancelaciones pegan en la métrica de "
                           "la tienda.")

    imagenes = [u for u in (campos.get("imagenes") or []) if u][:MAX_IMAGENES]
    if not imagenes:
        raise RuntimeError("Sin imágenes: Temu las exige (es uno de los cinco "
                           "campos que sí valida).")

    sufijo = sku.split("-")[-1] if "-" in sku else ""
    payload = {
        "language": "es",
        "goodsBasic": {
            "goodsName": titulo[:TITULO_MAX],
            "goodsDesc": (descripcion or titulo)[:DESC_MAX],
            "externalGoodsId": sku,
            # extCatName, NO catId: v3 descarta `catId` sin decir nada.
            "extCatName": str(cat_id),
            "shipmentLimitDay": DIAS_DESPACHO,
            "costTemplate": PLANTILLA_FLETE,
            "productType": 1,
            "bulletPoints": bullets,
        },
        "attributes": atributos,
        "skuList": [{
            "externalSkuId": sku,
            "images": [],                      # se llenan al confirmar
            "price": {"basePrice": {"amount": f"{precio:.2f}", "currency": "MXN"}},
            "quantity": stock,
            "packageInfo": {
                "weight": _medida((campos.get("peso") or 0) * 1000, 500.0, 9999999.9),
                "length": _medida(campos.get("largo"), 20.0, 9999.9),
                "width": _medida(campos.get("ancho"), 20.0, 9999.9),
                "height": _medida(campos.get("alto"), 20.0, 9999.9),
            },
            "variations": [{"name": "Color", "value": (sufijo or "Standard")[:40]}],
        }],
    }
    return {"payload": payload, "categoria_id": cat_id, "categoria_ruta": cat_ruta,
            "imagenes": imagenes, "avisos": avisos}


async def preview(req: dict[str, Any]) -> dict[str, Any]:
    """El payload REAL antes de mandarlo, sin subir medios ni tocar Temu."""
    from services import temu
    sku = str(req.get("sku") or "").strip()
    if not temu.disponible():
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": "Temu no está configurado (faltan las TEMU_*)."}
    try:
        armado = await _armar(req)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(exc)}

    avisos = list(armado["avisos"])
    # ¿El SKU ya está en Temu? Se pregunta AQUÍ para que la vista previa lo diga
    # antes de que alguien apriete publicar: reintentar sobre un SKU ya dado de
    # alta es lo que lo quema para siempre.
    try:
        ya = await temu.skus_ya_publicados([sku])
        if sku in ya:
            avisos.append(f"⚠️ Este SKU YA existe en Temu (goodsId {ya[sku]}). "
                          f"Publicarlo otra vez devuelve 150010090 y NO se puede "
                          f"deshacer: el identificador queda ocupado para siempre.")
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"No se pudo comprobar si el SKU ya existe: {exc}")

    avisos.append(f"{len(armado['imagenes'])} imagen(es) se convertirán a JPEG "
                  f"1000px y se subirán a Temu al confirmar.")
    avisos.append(f"Flete: plantilla {PLANTILLA_FLETE} (la única de la tienda, "
                  f"se llama \"test\").")
    return {
        "ok": True, "canal": CANAL, "sku": sku,
        "categoria_id": armado["categoria_id"],
        "categoria_ruta": armado["categoria_ruta"],
        "titulo": armado["payload"]["goodsBasic"]["goodsName"],
        "descripcion": armado["payload"]["goodsBasic"]["goodsDesc"],
        "atributos": len(armado["payload"]["attributes"]),
        "payload": armado["payload"],
        "avisos": avisos,
    }


async def confirmar(req: dict[str, Any]) -> dict[str, Any]:
    """Publica de verdad. Aborta si el SKU ya existe."""
    from services import temu
    sku = str(req.get("sku") or "").strip()
    if not temu.disponible():
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": "Temu no está configurado (faltan las TEMU_*)."}
    try:
        armado = await _armar(req)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(exc)}

    # ── CANDADO 1: el SKU no puede estar ya dado de alta ─────────────────────
    try:
        ya = await temu.skus_ya_publicados([sku])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": f"No se pudo verificar si el SKU ya existe, y sin esa "
                          f"comprobación no se publica: {exc}"}
    if sku in ya:
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": f"El SKU ya existe en Temu (goodsId {ya[sku]}). No se "
                          f"reintenta: un alta repetida lo quema para siempre.",
                "goods_id": ya[sku]}

    # ── Imágenes: Woo → JPEG 1000px → WordPress → Temu ───────────────────────
    # Temu rehospeda CONSERVANDO el tamaño que recibe: con la URL cruda quedan
    # en 800×800 y convertidas en 1000×1000. La URL kwcdn hay que guardarla, no
    # se puede recalcular.
    urls: list[str] = []
    try:
        from services import imagenes_amazon as ia, woocommerce as wc
        import asyncio
        for i, u in enumerate(armado["imagenes"], 1):
            crudo = await ia._descargar(u)  # noqa: SLF001
            if not crudo:
                continue
            jpeg, _, _ = await asyncio.to_thread(ia._a_jpeg, crudo, 1000)  # noqa: SLF001
            sub = await wc.subir_imagen_wp(f"temu-{sku}-{i}.jpg", jpeg, "image/jpeg")
            if not sub:
                continue
            r = await temu.llamar("temu.local.goods.image.v2.upload",
                                  {"fileUrl": sub[1], "usage": 1})
            imgs = r.get("images") or []
            if imgs:
                urls.append(imgs[0]["url"])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": f"Las imágenes no se pudieron preparar: {exc}"}
    if not urls:
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": "Ninguna imagen se pudo subir a Temu."}

    payload = armado["payload"]
    payload["skuList"][0]["images"] = urls

    # ── CANDADO 2: el payload queda registrado ANTES de mandarlo ─────────────
    log.info("TEMU alta %s · payload: %s", sku,
             json.dumps(payload, ensure_ascii=False)[:1500])

    try:
        res = await temu.llamar("temu.local.goods.v3.add", payload, timeout=90.0)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(exc),
                "payload": payload}

    goods_id = str(res.get("goodsId") or res.get("goods_id") or "")
    log.info("TEMU alta %s → goodsId %s", sku, goods_id or "(sin id)")
    return {"ok": True, "canal": CANAL, "sku": sku, "goods_id": goods_id,
            "categoria_id": armado["categoria_id"],
            "avisos": armado["avisos"], "respuesta": res}
