"""
publicar_tiktok.py — Publicar y actualizar en TikTok Shop DESDE EL PANEL.

DE DÓNDE SALE
-------------
El publicador vivía como script suelto en el escritorio de otra sesión
(`tk_publicar.py`), que fue con el que se subieron las 900 publicaciones. Esto
es su versión de panel: mismo payload y mismas trampas, pero disparado por una
persona sobre UN producto, con vista previa antes de mandar.

Lo que NO se copió del script, a propósito: el respaldo de categoría por IA en
dos pasos. Ese depende de módulos que viven en aquel scratchpad y elige "la hoja
más próxima" con confianza baja — sirve para un lote de 900 donde el costo de no
publicar es alto, pero en el panel hay una persona enfrente: si no hay categoría
fiable, se dice y se para. Un producto vivo y mal clasificado no da error, y es
el error que más caro sale (`TEC-1812-NEG`).

LAS TRAMPAS DE TIKTOK QUE ESTE MÓDULO RESPETA
---------------------------------------------
· **HTTP 200 no significa que haya funcionado.** El veredicto está en `code`
  del cuerpo. `tiktok.llamar` ya lo traduce a excepción.
· **`AS_DRAFT` casi no valida; `LISTING` valida todo.** Un borrador perfecto
  puede rebotar entero al activarse, así que el modo viaja en la vista previa
  para que se vea ANTES de mandar.
· **Las imágenes van por `uri`**, no por URL: hay que subirlas primero y TikTok
  las rehospeda.
· **Los `SALES_PROPERTY` (Color, Talla) NO van en `product_attributes`**:
  generan variantes. `tiktok_atributos` ya los excluye.
· **Categorías `INVITE_ONLY`**: no rechazan, dejan el producto en `PENDING`
  para siempre. Sin error, sin aviso.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from services import supabase_db as sdb, tiktok as tk

log = logging.getLogger("omnicanal.publicar_tiktok")

CANAL = "tiktok"
CUENTA = "KUBERA"
ALMACEN_VENTAS = "7647893424175580935"   # SALES_WAREHOUSE, NO el de devoluciones
MARCA_ID = "7650172564119684872"         # Ferrahome
MAX_IMAGENES = 5
TITULO_MAX = 300                         # MX y BR; el resto de regiones, 255


def _dims(campos: dict[str, Any]) -> dict[str, Any]:
    """
    Peso y medidas del paquete. Los respaldos son los mismos del script que
    publicó las 900: un producto sin medidas no se puede mandar, y quedarse sin
    publicar por eso sería peor que estimar la caja más común.

    ⚠️ TikTok exige que L+A+H ≤ 160 cm — lo comprueba `tiktok_contenido`.
    """
    return {
        "package_weight": {"value": str(campos.get("peso") or "0.5"), "unit": "KILOGRAM"},
        "package_dimensions": {
            "length": str(campos.get("largo") or "15"),
            "width": str(campos.get("ancho") or "12"),
            "height": str(campos.get("alto") or "8"),
            "unit": "CENTIMETER",
        },
    }


def _atributos_payload(atributos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    De los atributos guardados en `channel_content` al formato de TikTok.

    Los guardamos como `{nombre, campo: "product_attributes.<id>", valor,
    valor_id: [...]}` — el `campo` trae el ID del atributo y `valor_id` los de
    sus valores, que es lo único que TikTok acepta. Un atributo sin IDs no se
    manda: mandarlo por nombre es un rechazo seguro.
    """
    salida: list[dict[str, Any]] = []
    for a in atributos or []:
        campo = str(a.get("campo") or "")
        if not campo.startswith("product_attributes."):
            continue
        aid = campo.split(".", 1)[1]
        ids = [str(v) for v in (a.get("valor_id") or []) if v]
        if ids:
            salida.append({"id": aid, "values": [{"id": v} for v in ids]})
        elif a.get("valor"):
            # Texto libre: TikTok lo admite sin `id` de valor.
            salida.append({"id": aid, "values": [{"name": str(a["valor"])[:120]}]})
    return salida


async def _categoria(sku: str, titulo: str, token: str, cipher: str) -> tuple[str | None, str]:
    """
    (category_id, origen). Precedencia: lo que YA tiene publicado > el
    recomendador de TikTok.

    El recomendador falla el 49% (medido sobre 245 productos), así que su
    respuesta se marca como tal para que el humano la vea en la vista previa.
    """
    from services import tiktok_panel
    ya = tiktok_panel.categoria_de(sku)
    if ya:
        return ya, "la que ya tiene en TikTok"
    try:
        rec = await tk.llamar("/product/202309/categories/recommend", token,
                              {"shop_cipher": cipher},
                              {"product_title": titulo[:255]}, "POST")
        cad = rec.get("categories") or []
        cid = rec.get("leaf_category_id") or (cad[-1].get("id") if cad else None)
        return (str(cid) if cid else None), "recomendador de TikTok (falla el 49%)"
    except Exception as exc:  # noqa: BLE001
        log.warning("recomendador de categoría (%s): %s", sku, exc)
        return None, "el recomendador no contestó"


def _publicable(categoria_id: str | None) -> tuple[bool, str | None]:
    """
    ¿Se puede publicar en esa categoría? Solo las HOJAS admiten producto, y las
    `INVITE_ONLY` lo aceptan y lo dejan en `PENDING` para siempre.

    ⚠️ Hoy esto NO se puede comprobar contra la base: `channel.categories` no
    tiene columnas para `is_leaf` ni `permission_status` (pendiente de dos
    columnas de Eduardo). Así que se avisa en la vista previa en vez de
    afirmar lo que no sabemos.
    """
    if not categoria_id:
        return False, "sin categoría no se puede publicar"
    return True, ("No se puede verificar si la categoría admite publicación "
                  "(falta guardar `is_leaf`/`permission_status`): si es de las "
                  "restringidas, TikTok la aceptará y la dejará en PENDING sin avisar.")


async def _armar(req: dict[str, Any], token: str, cipher: str,
                 subir_imagenes: bool) -> dict[str, Any]:
    import asyncio

    campos = dict(req.get("campos") or {})
    sku = str(req.get("sku") or "")
    wc_id = req.get("wc_id")

    # Imágenes, precio, stock y medidas salen de WooCommerce por el MISMO camino
    # que ML y Amazon (`publicar_ready.construir_prod`), que ya aplica la regla
    # de la casa: lo que se editó en el Studio pisa lo que trae Woo. Pedirle al
    # frontend que mande las imágenes sería una segunda fuente que se
    # desincroniza — y el Studio ni siquiera las conoce.
    prod: dict[str, Any] = {}
    if wc_id:
        try:
            from services import publicar_ready, wp_db
            if wp_db.disponible():
                prod = await asyncio.to_thread(
                    publicar_ready.construir_prod, sku, int(wc_id), campos)
        except Exception as exc:  # noqa: BLE001
            log.warning("construir_prod(%s) falló: %s — se usa solo el formulario",
                        sku, exc)
    campos.setdefault("imagenes", prod.get("images") or [])
    for k, origen in (("precio_regular", "price"), ("peso", "weight"),
                      ("largo", "length"), ("ancho", "width"), ("alto", "height"),
                      ("stock", "stock")):
        if not campos.get(k) and prod.get(origen):
            campos[k] = prod[origen]

    titulo = (campos.get("titulo") or prod.get("title") or "").strip()
    if not titulo:
        raise RuntimeError("Falta el título.")
    if len(titulo) > TITULO_MAX:
        raise RuntimeError(f"El título tiene {len(titulo)} caracteres y TikTok "
                           f"admite {TITULO_MAX}.")
    precio = campos.get("precio_regular")
    if not precio or float(precio) <= 0:
        raise RuntimeError("Sin precio: TikTok rechaza el producto.")

    cat_id, cat_origen = await _categoria(sku, titulo, token, cipher)
    if not cat_id:
        raise RuntimeError("No se pudo determinar la categoría de TikTok. "
                           "Publica una vez desde el panel de TikTok o elige la "
                           "categoría antes de mandar.")

    # Imágenes: se suben ANTES y viajan por `uri`. En la vista previa NO se
    # suben (no se tocan medios para mirar), y por eso el payload de preview
    # muestra cuántas se enviarán en vez de sus identificadores.
    uris: list[dict[str, str]] = []
    imagenes = [u for u in (campos.get("imagenes") or []) if u][:MAX_IMAGENES]
    if subir_imagenes:
        for u in imagenes:
            uri = await tk.subir_imagen(u, token)
            if uri:
                uris.append({"uri": uri})
        if not uris:
            raise RuntimeError("Ninguna imagen se pudo subir a TikTok "
                               "(se convierten a JPEG ≥1000 px antes de mandarlas).")

    desc = (campos.get("descripcion") or prod.get("description") or "").strip() \
        or f"<p>{titulo}</p>"
    payload: dict[str, Any] = {
        "save_mode": req.get("save_mode") or "LISTING",
        "title": titulo[:TITULO_MAX],
        "description": desc[:10000],
        "category_id": str(cat_id),
        "brand_id": MARCA_ID,
        "main_images": uris,
        "product_attributes": _atributos_payload(campos.get("atributos") or []),
        **_dims(campos),
        "skus": [{
            "seller_sku": sku,
            "price": {"amount": f"{float(precio):.2f}", "currency": "MXN"},
            "inventory": [{"warehouse_id": ALMACEN_VENTAS,
                           "quantity": max(int(campos.get("stock") or 0), 0)}],
        }],
        "is_cod_allowed": False,
    }
    return {"payload": payload, "categoria_id": str(cat_id),
            "categoria_origen": cat_origen, "imagenes": len(imagenes)}


async def preview(req: dict[str, Any]) -> dict[str, Any]:
    """El payload REAL antes de mandarlo, sin subir medios ni tocar TikTok."""
    sku = str(req.get("sku") or "")
    token, cipher = tk.access_token(), tk.cipher()
    avisos: list[str] = []
    if not (token and cipher):
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": "TikTok no está conectado (falta token o shop_cipher). "
                          "Reautoriza la tienda desde el panel."}
    try:
        armado = await _armar(req, token, cipher, subir_imagenes=False)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(exc)}

    payload = armado["payload"]
    _, aviso_cat = _publicable(armado["categoria_id"])
    if aviso_cat:
        avisos.append(aviso_cat)
    avisos.append(f"Categoría {armado['categoria_id']} — {armado['categoria_origen']}.")
    avisos.append(f"{armado['imagenes']} imagen(es) se subirán a TikTok al confirmar "
                  f"(se convierten a JPEG ≥1000 px).")
    if not payload["product_attributes"]:
        avisos.append("Sin atributos: el producto se publica, pero no aparece en los "
                      "filtros de búsqueda. Genera el contenido con IA primero.")
    if payload["save_mode"] == "LISTING":
        avisos.append("Modo LISTING: queda A LA VENTA en cuanto TikTok lo apruebe. "
                      "El borrador (AS_DRAFT) casi no valida — lo que rebota, rebota aquí.")

    from services import tiktok_panel
    ya = tiktok_panel.categoria_de(sku)
    return {
        "ok": True, "canal": CANAL, "sku": sku,
        "product_type": armado["categoria_id"],
        "product_type_origen": armado["categoria_origen"],
        "titulo": payload["title"], "descripcion": payload["description"],
        "operaciones": {"titulo": True, "descripcion": True,
                        "atributos": len(payload["product_attributes"]),
                        "imagenes": armado["imagenes"]},
        "payload": payload,
        "avisos": avisos,
        "ya_publicado": bool(ya),
    }


async def confirmar(req: dict[str, Any]) -> dict[str, Any]:
    """Crea o actualiza el producto en TikTok. EN VIVO."""
    sku = str(req.get("sku") or "")
    token, cipher = tk.access_token(), tk.cipher()
    if not (token and cipher):
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": "TikTok no está conectado (falta token o shop_cipher)."}
    try:
        armado = await _armar(req, token, cipher, subir_imagenes=True)
    except Exception as exc:  # noqa: BLE001
        _registrar(sku, False, error=f"armado: {exc}", status="build_failed")
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(exc)}

    payload = armado["payload"]
    item_id = str(req.get("item_id") or "").strip() or _listing_id(sku)
    # Crear y actualizar son endpoints distintos: crear con un producto que ya
    # existe genera un DUPLICADO, que en TikTok hay que borrar a mano.
    ruta = (f"/product/202309/products/{item_id}" if item_id
            else "/product/202309/products")
    operacion = "update_product" if item_id else "create_product"
    try:
        data = await tk.llamar(ruta, token, {"shop_cipher": cipher}, payload, "POST")
    except Exception as exc:  # noqa: BLE001
        _registrar(sku, False, error=str(exc), status="api_failed", operacion=operacion)
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(exc)[:400],
                "payload": payload}

    pid = str(data.get("product_id") or item_id or "")
    _registrar(sku, True, submission_id=pid, operacion=operacion)
    _reflejar(sku, pid, payload, armado["categoria_id"])
    return {"ok": True, "canal": CANAL, "sku": sku, "product_id": pid,
            "operacion": operacion,
            "url": f"https://shop.tiktok.com/view/product/{pid}" if pid else None,
            "modo": payload["save_mode"],
            "registrado_en": "ops.channel_submissions + channel.listings"}


def _listing_id(sku: str) -> str | None:
    try:
        filas = sdb.fetch_all(
            "select listing_id from channel.listings "
            "where canal=%s and sku=%s::citext and listing_id is not null limit 1",
            (CANAL, sku))
        return (filas or [{}])[0].get("listing_id")
    except Exception:  # noqa: BLE001
        return None


def _registrar(sku: str, ok: bool, submission_id: str | None = None,
               error: str | None = None, status: str | None = None,
               operacion: str = "create_product") -> None:
    """A `ops.channel_submissions`, la bitácora común de los tres canales.

    Nunca revienta la publicación: perder el registro es malo, perder el envío
    que YA se hizo es peor.
    """
    try:
        ahora = datetime.now(timezone.utc)
        sdb.execute(
            """insert into ops.channel_submissions
                 (canal, cuenta, sku, submission_id, operacion, status, success,
                  error_resumen, detail_ref, submitted_at, published_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (CANAL, CUENTA, sku, submission_id, operacion,
             status or ("published" if ok else "failed"), ok,
             (error or "")[:2000] or None, "panel:publicar", ahora,
             ahora if ok else None))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo registrar el envío de %s: %s", sku, exc)


def _reflejar(sku: str, product_id: str, payload: dict[str, Any],
              categoria_id: str) -> None:
    """
    Deja el resultado en `channel.listings` para que el panel lo muestre YA.

    Sin esto habría que esperar al siguiente censo para ver lo que uno acaba de
    publicar, y el panel diría "no publicado" de un producto que sí lo está —
    justo la desincronización que este canal tenía antes de abrirse.
    """
    try:
        sku_precio = (payload.get("skus") or [{}])[0]
        precio = float((sku_precio.get("price") or {}).get("amount") or 0)
        stock = int(((sku_precio.get("inventory") or [{}])[0]).get("quantity") or 0)
        estado = "ACTIVATE" if payload.get("save_mode") == "LISTING" else "DRAFT"
        sdb.execute(
            """insert into channel.listings
                 (sku, account_id, canal, listing_id, url, status, price,
                  stock_own, is_fulfillment, category_id, currency, store_name,
                  updated_at)
               select %s::citext, a.id, %s, %s, %s, %s, %s, %s, false, %s, 'MXN', %s, now()
                 from core.accounts a
                where a.channel_id=%s and a.legacy_code=%s
               on conflict (sku, account_id, canal) do update set
                 listing_id=excluded.listing_id, url=excluded.url,
                 status=excluded.status, price=excluded.price,
                 stock_own=excluded.stock_own, category_id=excluded.category_id,
                 updated_at=now()""",
            (sku, CANAL, product_id,
             f"https://shop.tiktok.com/view/product/{product_id}" if product_id else None,
             estado, precio, stock, categoria_id, CUENTA, CANAL, CUENTA))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo reflejar %s en channel.listings: %s", sku, exc)
