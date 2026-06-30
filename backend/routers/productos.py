"""
productos.py — Endpoints de productos por canal.

  GET /api/productos?canal=general&page=1&per_page=40&search=...
      → lista paginada (40/pág) proyectada al canal solicitado.
        GENERAL viene de WooCommerce en vivo; ML/Amazon del cache DB;
        TikTok/Walmart/Temu/Shein de datos de ejemplo.

  GET /api/productos/{sku}
      → detalle 360°: el producto en TODOS los canales a la vez.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from core.marketplaces import Canal, config_canal, es_canal_valido
from models.schemas import (
    DetalleCanal,
    DetalleProducto,
    Paginacion,
    Producto,
    RespuestaProductos,
)
from services import amazon, ejemplos, inventario, meli, presencia, woocommerce

log = logging.getLogger("omnicanal.routers.productos")
router = APIRouter(prefix="/api/productos", tags=["productos"])

PER_PAGE_DEFAULT = 40
PER_PAGE_MAX = 100


@router.get("", response_model=RespuestaProductos)
async def listar_productos(
    canal: str = Query(Canal.GENERAL.value, description="Canal/marketplace"),
    page: int = Query(1, ge=1),
    per_page: int = Query(PER_PAGE_DEFAULT, ge=1, le=PER_PAGE_MAX),
    search: str | None = Query(None, description="Búsqueda por SKU o nombre"),
    solo_publicados: bool = Query(False, description="Solo items publicados en el canal"),
    cuenta: str | None = Query(None, description="Cuenta ML: BEKURA (Kubera) o SANCORFASHION (San Corpe)"),
):
    if not es_canal_valido(canal):
        raise HTTPException(404, f"Canal desconocido: {canal}")

    if canal == Canal.GENERAL.value:
        items_raw, total, total_pages = await woocommerce.listar_productos(
            page=page, per_page=per_page, search=search
        )
        # Enriquecer con presencia en marketplaces (puntos de colores)
        skus = [i["sku"] for i in items_raw]
        pres = presencia.presencia_por_sku(skus)
        for it in items_raw:
            it["canales"] = pres.get(it["sku"], [])
            it["origen"] = "woocommerce"

    elif canal == Canal.MERCADO_LIBRE.value:
        items_raw, total = meli.listar(page, per_page, search, solo_publicados, cuenta)
        total_pages = _paginas(total, per_page)

    elif canal == Canal.AMAZON.value:
        items_raw, total = amazon.listar(page, per_page, search, solo_publicados)
        total_pages = _paginas(total, per_page)

    else:  # tiktok / walmart / temu / shein  → ejemplos
        items_raw, total = ejemplos.listar(canal, page, per_page, search)
        total_pages = _paginas(total, per_page)

    # Imágenes: los canales de marketplace/ejemplo comparten el producto de
    # WooCommerce (vía wc_id). Traemos la imagen en una sola llamada por lote.
    if canal != Canal.GENERAL.value:
        wc_ids = [it["wc_id"] for it in items_raw if it.get("wc_id")]
        if wc_ids:
            imgs = await woocommerce.imagenes_por_wc_id(wc_ids)
            for it in items_raw:
                if not it.get("imagen") and it.get("wc_id") in imgs:
                    it["imagen"] = imgs[it["wc_id"]]

        # Inventario en vivo cacheado (precio real + desglose stock_real/full/fba).
        inv = inventario.leer_inventario([it["sku"] for it in items_raw])
        for it in items_raw:
            clave = f"{canal}|{it.get('cuenta') or ''}"
            datos = inv.get(it["sku"], {}).get(clave)
            if not datos:
                continue
            if datos.get("precio") is not None:
                it["precio"] = float(datos["precio"])
            it["stock_real"] = datos.get("stock_real")
            it["stock_full"] = datos.get("stock_full")
            it["stock_fba"] = datos.get("stock_fba")
            it["situacion"] = datos.get("situacion")
            it["full"] = bool(datos.get("es_full"))
            # El stock mostrado en la tarjeta es el real (lo que se sincroniza)
            if datos.get("stock_real") is not None:
                it["stock"] = datos["stock_real"]

    items = [Producto(**i) for i in items_raw]
    paginacion = Paginacion(
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        tiene_anterior=page > 1,
        tiene_siguiente=page < total_pages,
    )
    return RespuestaProductos(canal=canal, items=items, paginacion=paginacion)


@router.get("/{sku}", response_model=DetalleProducto)
async def detalle_producto(sku: str):
    """Vista 360°: el SKU en WooCommerce + cada marketplace."""
    # Sincroniza este SKU en vivo para que el detalle nunca salga incompleto.
    try:
        await inventario.sincronizar_sku(sku)
    except Exception:  # noqa: BLE001
        pass

    base = await woocommerce.obtener_producto_por_sku(sku)
    if not base:
        # Puede existir en el cache aunque no en WooCommerce
        base = {"sku": sku, "nombre": sku}

    detalle = DetalleProducto(
        sku=sku,
        wc_id=base.get("wc_id"),
        nombre=base.get("nombre", sku),
        imagen=base.get("imagen"),
        imagenes=base.get("imagenes", []),
        marca=base.get("marca"),
        descripcion=base.get("descripcion"),
        canales=[],
    )

    # Canal GENERAL (WooCommerce)
    detalle.canales.append(DetalleCanal(
        canal=Canal.GENERAL.value,
        publicado=base.get("estado") == "publish",
        precio=base.get("precio"),
        precio_base=base.get("precio_base"),
        stock=base.get("stock"),
        stock_real=base.get("stock"),
        categoria_path=base.get("categoria_path", []),
        categoria_id=base.get("categoria_id"),
        url=base.get("url"),
        estado=base.get("estado"),
    ))

    # Inventario en vivo cacheado para este SKU (todas las cuentas/canales)
    inv = inventario.leer_inventario([sku]).get(sku, {})

    def _aplicar_inv(canal: str, cuenta: str, dc: DetalleCanal) -> DetalleCanal:
        datos = inv.get(f"{canal}|{cuenta}")
        if datos:
            if datos.get("precio") is not None:
                dc.precio = float(datos["precio"])
            dc.stock_real = datos.get("stock_real")
            dc.stock_full = datos.get("stock_full")
            dc.stock_fba = datos.get("stock_fba")
            dc.situacion = datos.get("situacion")
            dc.full = bool(datos.get("es_full"))
            if datos.get("stock_real") is not None:
                dc.stock = datos["stock_real"]
        return dc

    # Mercado Libre (cache) — una entrada por cuenta publicada
    ml_items, _ = meli.listar(search=sku, per_page=5)
    cuentas_vistas: set[str] = set()
    for m in ml_items:
        cta = m.get("cuenta") or ""
        if cta in cuentas_vistas:
            continue
        cuentas_vistas.add(cta)
        dc = DetalleCanal(
            canal=Canal.MERCADO_LIBRE.value,
            publicado=m["publicado"], item_id=m["item_id"], url=m["url"],
            precio=m["precio"], precio_base=m["precio_base"], stock=m["stock"],
            full=m["full"], full_label=m["full_label"],
            categoria_id=m["categoria_id"], categoria_path=m["categoria_path"],
            estado=m["estado"], extra={"cuenta": cta},
        )
        detalle.canales.append(_aplicar_inv(Canal.MERCADO_LIBRE.value, cta, dc))

    # Amazon (cache)
    az_items, _ = amazon.listar(search=sku, per_page=1)
    if az_items:
        a = az_items[0]
        dc = DetalleCanal(
            canal=Canal.AMAZON.value,
            publicado=a["publicado"], item_id=a["item_id"], url=a["url"],
            precio=a["precio"], stock=a["stock"],
            full=a["full"], full_label=a["full_label"],
            categoria_id=a["categoria_id"], categoria_path=a["categoria_path"],
            estado=a["estado"],
        )
        detalle.canales.append(_aplicar_inv(Canal.AMAZON.value, "", dc))

    return detalle


def _paginas(total: int, per_page: int) -> int:
    return max(1, (total + per_page - 1) // per_page)
