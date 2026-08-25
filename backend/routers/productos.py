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

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.marketplaces import Canal, config_canal, es_canal_valido
from models.schemas import (
    DetalleCanal,
    DetalleProducto,
    FiltroActivas,
    Paginacion,
    Producto,
    RespuestaProductos,
)
from services import amazon, ejemplos, inventario, meli, presencia, publicar, studio, woocommerce

log = logging.getLogger("omnicanal.routers.productos")
router = APIRouter(prefix="/api/productos", tags=["productos"])

PER_PAGE_DEFAULT = 40
PER_PAGE_MAX = 100

# ── "Solo activas" — por qué vive aquí y no en el frontend ────────────────────
#
# El filtro tiene que aplicarse DONDE SE PAGINA. Hecho en la pantalla filtraría
# las 40 filas que ya se bajaron y diría "3 resultados" en un canal que tiene
# cientos: un número de página presentado como número del catálogo.
#
# El criterio NO se escribe aquí: es el de `services/publicaciones_panel.py`
# (`activa` + `puede_estar_activa`), el mismo que usa la pestaña de
# publicaciones. Ojo con la palabra: "publicado" y "activo" NO son lo mismo.
# `solo_publicados` contesta "¿existe en el marketplace?" y cuenta las pausadas
# de ML y las 1,253 DISCOVERABLE de Amazon, que se ven y no se pueden comprar.
#
# Y hay canales que NO pueden contestar la pregunta. Ésos no devuelven cero en
# silencio —un cero se lee como "no hay activas"— sino `aplicado=False` con la
# nota. Los que SÍ pueden y dan cero (TikTok hoy) devuelven cero de verdad.
_NOTA_NO_APLICA = {
    "general": "Woo (chunche.shop) es la FUENTE del catálogo y del stock, no un "
               "canal de venta: sus filas no traen estado de publicación, así "
               "que aquí no hay nada que se pueda llamar 'activa'.",
    "ejemplos": "Este canal todavía se pinta con datos de ejemplo: no sale de "
                "`channel.listings` y no tiene estado de publicación real.",
    "mysql": "La rejilla está leyendo la bitácora de MySQL "
             "(SUPABASE_READ_PUBLICACIONES apagado), que sólo sabe si el "
             "publicador subió la publicación — no si se puede comprar. El "
             "filtro no se aplicó: la lista viene SIN filtrar.",
}


def _filtro_activas(canal: str, pedido: bool) -> FiltroActivas | None:
    """El bloque `filtro_activas` de la respuesta. `None` si nadie lo pidió."""
    if not pedido:
        return None
    from services import publicaciones_panel as pp

    vivos = pp.valores_activos(canal)
    campo = pp._DECIDE.get(canal, (None, None))[0]  # noqa: SLF001
    nota = pp.NOTA_CANAL.get(canal)

    if vivos is None:                       # el canal no decide por ninguna columna
        motivo = _NOTA_NO_APLICA["general"] if canal == Canal.GENERAL.value \
            else _NOTA_NO_APLICA["ejemplos"]
        return FiltroActivas(solo_activas=True, aplicado=False, nota=motivo)

    if canal == Canal.MERCADO_LIBRE.value and not meli.puede_filtrar_activas():
        return FiltroActivas(solo_activas=True, aplicado=False,
                             nota=_NOTA_NO_APLICA["mysql"])
    if canal == Canal.AMAZON.value and not amazon.puede_filtrar_activas():
        return FiltroActivas(solo_activas=True, aplicado=False,
                             nota=_NOTA_NO_APLICA["mysql"])

    return FiltroActivas(solo_activas=True, aplicado=True, campo=campo,
                         valores=vivos, nota=nota)


@router.get("", response_model=RespuestaProductos)
async def listar_productos(
    canal: str = Query(Canal.GENERAL.value, description="Canal/marketplace"),
    page: int = Query(1, ge=1),
    per_page: int = Query(PER_PAGE_DEFAULT, ge=1, le=PER_PAGE_MAX),
    search: str | None = Query(None, description="Búsqueda por SKU o nombre"),
    solo_publicados: bool = Query(False, description="Solo items publicados en el canal (incluye pausadas de ML y DISCOVERABLE de Amazon)"),
    solo_activas: bool = Query(False, description="Solo publicaciones que se pueden comprar HOY, con el criterio de cada canal (publicaciones_panel). Manda sobre solo_publicados y estados"),
    cuenta: str | None = Query(None, description="Cuenta ML: BEKURA (Kubera) o SANCORFASHION (San Corpe)"),
    orden: str = Query("reciente", description="reciente | stock_desc | stock_asc | precio_desc | precio_asc"),
    estados: str | None = Query(None, description="Filtro de estado, coma-separado: publicado,inactivo"),
    categoria: int | None = Query(None, description="ID de categoría WooCommerce (canal general)"),
    skus: str | None = Query(None, description="Lista de SKUs/términos separados por coma: filtra y busca a la vez"),
    vista: str = Query("productos", description="productos (publish/pending/ready) | crear (draft/inprogress) | omnicanal (todos)"),
):
    if not es_canal_valido(canal):
        raise HTTPException(404, f"Canal desconocido: {canal}")

    estados_lista = [e.strip() for e in estados.split(",")] if estados else None
    skus_lista = [s for s in (skus or "").split(",") if s.strip()] or None

    # Se resuelve ANTES de listar: si el canal (o el camino de lectura) no puede
    # evaluar el criterio, no se filtra y se dice — nunca se devuelve un cero
    # sin explicación.
    filtro_activas = _filtro_activas(canal, solo_activas)
    activas = bool(filtro_activas and filtro_activas.aplicado)

    if canal == Canal.GENERAL.value:
        items_raw, total, total_pages = await woocommerce.listar_productos(
            page=page, per_page=per_page, search=search,
            orden=orden, estados=estados_lista, categoria=categoria, skus=skus_lista,
            vista=vista,
        )
        # Enriquecer con presencia en marketplaces (puntos de colores).
        # Un solo lote: los SKUs de los padres MÁS los de sus variantes, para que
        # cada variante muestre en qué canales está publicada.
        skus = [i["sku"] for i in items_raw]
        skus += [v["sku"] for i in items_raw for v in (i.get("variantes") or [])]
        pres = presencia.presencia_por_sku(skus)
        for it in items_raw:
            it["canales"] = pres.get(it["sku"], [])
            it["origen"] = "woocommerce"
            for v in (it.get("variantes") or []):
                v["canales"] = pres.get(v["sku"], [])
            # PUBLICADO = está en AL MENOS UN canal (regla del panel), no el
            # status de WooCommerce: un producto `inprogress` en Woo pero vivo en
            # Mercado Libre SÍ está publicado (caso CAM-0030, que salía "Sin
            # publicar" estando en las 2 cuentas de ML). Las variantes cuentan:
            # tras separar un padre, las publicaciones cuelgan de SUS SKUs.
            canales_propios = [c for c in it["canales"] if c.get("publicado")]
            canales_variantes = [
                c for v in (it.get("variantes") or [])
                for c in (v.get("canales") or []) if c.get("publicado")
            ]
            it["publicado"] = bool(canales_propios or canales_variantes)

    elif canal == Canal.MERCADO_LIBRE.value:
        items_raw, total = meli.listar(page, per_page, search, solo_publicados, cuenta,
                                       orden=orden, estados=estados_lista, skus_filtro=skus_lista,
                                       solo_activas=activas)
        total_pages = _paginas(total, per_page)

    elif canal == Canal.AMAZON.value:
        items_raw, total = amazon.listar(page, per_page, search, solo_publicados,
                                         orden=orden, estados=estados_lista, skus_filtro=skus_lista,
                                         solo_activas=activas)
        total_pages = _paginas(total, per_page)

    elif canal == Canal.TIKTOK.value:
        # Lee `channel.listings` en kubera (no MySQL: los espejos inversos están
        # apagados desde el 13-ago). Es la primera lectura de listado del panel
        # que va directo a kubera; ML y Amazon siguen en MySQL.
        from services import tiktok_panel
        items_raw, total = tiktok_panel.listar(
            page, per_page, search, solo_publicados, orden=orden,
            estados=estados_lista, skus_filtro=skus_lista, solo_activas=activas)
        total_pages = _paginas(total, per_page)

    elif canal == Canal.TEMU.value:
        # Mismo camino que TikTok: `channel.listings` en kubera. Hasta el 14-ago
        # esta pestaña mostraba datos de EJEMPLO encima de un canal con 160
        # publicaciones vivas.
        from services import temu_panel
        items_raw, total = temu_panel.listar(
            page, per_page, search, solo_publicados, orden=orden,
            estados=estados_lista, skus_filtro=skus_lista, solo_activas=activas)
        total_pages = _paginas(total, per_page)

    elif canal == Canal.WALMART.value:
        # `channel.listings` en kubera, como TikTok y Temu. Antes caía en
        # `ejemplos.py` con 235 artículos reales publicados.
        from services import walmart_panel
        items_raw, total = walmart_panel.listar(
            page, per_page, search, solo_publicados, orden=orden,
            estados=estados_lista, skus_filtro=skus_lista, solo_activas=activas)
        total_pages = _paginas(total, per_page)

    else:  # shein  → ejemplos
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
    return RespuestaProductos(canal=canal, items=items, paginacion=paginacion,
                              filtro_activas=filtro_activas)


@router.get("/_categorias/lista")
async def listar_categorias():
    """Categorías de WooCommerce (con productos) para el filtro de la vista General."""
    try:
        return await woocommerce.listar_categorias()
    except Exception:  # noqa: BLE001
        return []


@router.get("/{sku}/studio")
async def studio_metadata(sku: str, wc_id: int | None = Query(None, description="wc_id para leer postmeta")):
    """
    Metadata extra para el Estudio del producto (pestaña PRODUCTOS):
    costo, precios, peso/dimensiones (+ volumen m³), categoría ML con TODOS sus
    subniveles, y campos de Alibaba/atributos (postmeta) si hay WPDB_*.
    El estado de publicación se consulta EN VIVO (ML/Amazon) para que sea real-time.
    """
    m = studio.metadata(sku, wc_id)
    try:
        m["estado"] = await publicar.estado_live(sku)
    except Exception:  # noqa: BLE001
        pass  # si falla la consulta en vivo, se queda el estado de la DB
    return m


@router.get("/{sku}", response_model=DetalleProducto)
async def detalle_producto(sku: str, refrescar: bool = False):
    """
    Vista 360°: el SKU en WooCommerce + cada marketplace.
    Lee del cache (canal_inventario). Con ?refrescar=true sincroniza en vivo ese
    SKU (lo usa el botón de refrescar); el resto del tiempo NO toca las APIs, para
    no hacer consultas una-por-una (el sync masivo corre en segundo plano).
    """
    if refrescar:
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
        descripcion_corta=base.get("descripcion_corta"),
        atributos=base.get("atributos", []),
        precio_base=base.get("precio_base"),
        precio_oferta=base.get("precio_oferta"),
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

    # Amazon (cache). IGUALDAD EXACTA, nunca `search=` — ése hace LIKE de prefijo
    # y con 693 SKUs que son prefijo de otros el detalle acababa mostrando la
    # publicación de OTRO producto (auditoría 29-jul).
    a = amazon.por_sku(sku)
    if a:
        dc = DetalleCanal(
            canal=Canal.AMAZON.value,
            publicado=a["publicado"], item_id=a["item_id"], url=a["url"],
            precio=a["precio"], stock=a["stock"],
            full=a["full"], full_label=a["full_label"],
            categoria_id=a["categoria_id"], categoria_path=a["categoria_path"],
            estado=a["estado"],
        )
        detalle.canales.append(_aplicar_inv(Canal.AMAZON.value, "", dc))

    # TikTok Shop (kubera). Faltaba entero: el cajón mostraba General, ML y
    # Amazon, y un producto publicado en TikTok no tenía dónde verse.
    #
    # No pasa por `_aplicar_inv`: ese enriquece desde `canal_inventario` con la
    # convención de ML/Amazon (FULL/FBA), y en TikTok el stock ya viene de la
    # misma tabla que lo alimenta todo. Añadirlo dos veces solo podía
    # contradecirse.
    from services import tiktok_panel
    tk = tiktok_panel.datos_de(sku)
    if tk:
        detalle.canales.append(DetalleCanal(
            canal=Canal.TIKTOK.value,
            publicado=tk["publicado"], item_id=tk["item_id"], url=tk["url"],
            precio=tk["precio"], precio_base=tk["precio_base"],
            stock=tk["stock"], stock_real=tk["stock"],
            # TikTok Shop MX trabaja con almacenes DEL VENDEDOR: no hay
            # equivalente a FULL o FBA, así que `full` va en falso y sin
            # etiqueta. Las 900 publicaciones están en el almacén de ventas.
            full=False, full_label=None,
            categoria_id=tk["categoria_id"], categoria_path=tk["categoria_path"],
            estado=tk["estado"], situacion=tk.get("situacion"),
            extra={"cuenta": "KUBERA"},
        ))

    # Temu (kubera). Mismo trato que TikTok y por la misma razón: 160
    # publicaciones vivas que el cajón no mostraba.
    from services import temu_panel
    tm = temu_panel.datos_de(sku)
    if tm:
        detalle.canales.append(DetalleCanal(
            canal=Canal.TEMU.value,
            publicado=tm["publicado"], item_id=tm["item_id"], url=tm["url"],
            precio=tm["precio"], precio_base=tm["precio_base"],
            stock=tm["stock"], stock_real=tm["stock"],
            # Temu tampoco tiene bodega propia del marketplace para nosotros.
            full=False, full_label=None,
            categoria_id=tm["categoria_id"], categoria_path=tm["categoria_path"],
            # `estado` trae la etiqueta cuando el código está verificado y el
            # código crudo cuando no. `situacion` siempre lleva el código: si
            # alguien ve "Temu 3/2" en pantalla, es que ese aún no se decodifica.
            estado=tm["estado"], situacion=tm.get("situacion"),
            extra={"cuenta": "TEMU"},
        ))

    # Walmart MX (kubera). Mismo trato que TikTok y Temu.
    from services import walmart_panel
    wm = walmart_panel.datos_de(sku)
    if wm:
        detalle.canales.append(DetalleCanal(
            canal=Canal.WALMART.value,
            publicado=wm["publicado"], item_id=wm["item_id"], url=wm["url"],
            precio=wm["precio"], precio_base=wm["precio_base"],
            # Walmart no devuelve stock en /v3/items: se muestra vacío en vez
            # de un 0 que se leería como "agotado".
            stock=wm["stock"], stock_real=wm["stock"],
            full=False, full_label=None,
            categoria_id=wm["categoria_id"], categoria_path=wm["categoria_path"],
            estado=wm["estado"], situacion=wm.get("situacion"),
            extra={"cuenta": "WALMART"},
        ))

    return detalle


class ContenidoReq(BaseModel):
    wc_id: int | None = None
    titulo: str | None = None
    descripcion: str | None = None
    atributos: list[dict] | None = None  # [{nombre, valor}] — atributos custom


@router.post("/{sku}/contenido")
async def guardar_contenido(sku: str, req: ContenidoReq):
    """
    Guarda el CONTENIDO del producto (título/descripción/atributos custom) en
    WooCommerce. Lo usa el botón "Guardar contenido" del canal General del Estudio.
    Preserva los atributos de variación (no rompe productos variables).
    """
    wc_id = req.wc_id
    if not wc_id:
        p = await woocommerce.obtener_producto_por_sku(sku)
        wc_id = p.get("wc_id") if p else None
    if not wc_id:
        raise HTTPException(400, "No se pudo resolver el producto en WooCommerce.")
    ok = await woocommerce.guardar_contenido_wc(
        int(wc_id),
        titulo=req.titulo,
        descripcion=req.descripcion,
        atributos=req.atributos,
    )
    if not ok:
        raise HTTPException(502, "No se pudo guardar el contenido en WooCommerce.")
    return {"ok": True, "sku": sku, "wc_id": wc_id}


# ══════════════════════════════════════════════════════════════════════════════
# CONTENIDO POR CANAL — lo que el Estudio edita antes de publicar
#
# El endpoint de arriba (`POST /{sku}/contenido`) guarda el canal GENERAL en
# WooCommerce y no recibe canal. Estos tres son el resto: guardan en
# `enrich.channel_content`, con llave (sku, canal, cuenta).
#
# Por qué hacían falta: el Estudio ya generaba contenido por canal (los 6
# generadores de Amazon, 3 de ML, 1 de TikTok) y no tenía dónde guardarlo — si
# no publicabas en la misma sesión, se perdía.
# ══════════════════════════════════════════════════════════════════════════════

class ContenidoCanalReq(BaseModel):
    # Llaves CANÓNICAS del panel: titulo, descripcion, bullets, highlights,
    # atributos… NO los nombres nativos del canal (`item_name`, `productName`).
    # La traducción vive en el publicador.
    contenido: dict
    # Por campo: woo | ia | manual | calc. Es lo que deja saber qué revisar.
    origen: dict | None = None
    categoria: str | None = None
    spec_version: str | None = None
    hash_base: str | None = None
    # Por omisión FUSIONA: mandar solo {"highlights": "..."} conserva el resto.
    # true pisa el documento entero — el único modo de BORRAR un campo.
    reemplazar: bool = False


async def _categoria_del_canal(sku: str, canal: str) -> str | None:
    """
    La categoría que ese canal tiene hoy para el SKU.

    Se resuelve AQUÍ y no en el frontend porque la lógica ya existe y tiene
    precedencia propia: en Amazon `_pt_resuelto` aplica panel > histórico >
    detección (regla 2 de la casa), y el Estudio ni siquiera conoce el tipo —
    `TipoAmazonPicker` lo maneja por dentro y no lo expone al padre.
    Duplicar esa resolución en React sería una segunda verdad que se desincroniza.

    Nunca lanza: sin categoría se guarda el contenido igual y la columna queda
    nula. Es un dato para comparar contra los requisitos del canal, no un
    requisito para guardar.
    """
    try:
        if canal == "amazon":
            from services import publicar, studio
            wc_id = (studio.metadata(sku, None) or {}).get("wc_id")
            pt, _origen = publicar._pt_resuelto(sku, wc_id)  # noqa: SLF001
            return pt
        if canal == "mercado_libre":
            from services import studio
            cat = (studio.metadata(sku, None) or {}).get("categoria_ml") or {}
            return cat.get("category_id") or None
        if canal == "temu":
            # Panel > publicación, igual que TikTok. Sin esto el Estudio pedía
            # los requisitos con categoría None y el semáforo se caía.
            from services import temu_panel
            return temu_panel.categoria_de(sku)
        if canal == "tiktok":
            # ⚠️ En TikTok la categoría vive en `listings.category_id`; en Amazon
            # vive en `product_type`. Cruzar los requisitos por la columna
            # equivocada devuelve cero filas SIN dar error.
            from services import tiktok_panel
            return tiktok_panel.categoria_de(sku)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo resolver la categoría de %s en %s: %s", sku, canal, exc)
    return None


@router.put("/{sku}/canal/{canal}/contenido")
async def guardar_contenido_canal(sku: str, canal: str, req: ContenidoCanalReq,
                                  cuenta: str = Query("")):
    """Guarda el contenido de un SKU para un canal. Fusiona salvo `reemplazar`."""
    from services import channel_content

    if not es_canal_valido(canal):
        raise HTTPException(400, f"Canal '{canal}' inválido.")
    # El cliente puede mandarla; si no, se resuelve con la lógica del canal.
    categoria = req.categoria or await _categoria_del_canal(sku, canal)
    res = await channel_content.guardar(
        sku, canal, req.contenido, cuenta=cuenta, origen=req.origen,
        categoria=categoria, spec_version=req.spec_version,
        hash_base=req.hash_base, reemplazar=req.reemplazar,
    )
    if not res.get("ok"):
        # 409: el caso normal es el SKU que aún no está en core.products (lo
        # agrega el cron de las 06:15). No es culpa del cliente ni del servidor.
        raise HTTPException(409, res.get("motivo") or "No se pudo guardar.")
    return res


@router.get("/{sku}/canal/{canal}/contenido")
async def leer_contenido_canal(sku: str, canal: str, cuenta: str = Query("")):
    """El contenido guardado. `existe:false` si nunca se guardó nada."""
    from services import channel_content

    if not es_canal_valido(canal):
        raise HTTPException(400, f"Canal '{canal}' inválido.")
    doc = await channel_content.leer(sku, canal, cuenta)
    if doc is None:
        return {"existe": False, "sku": sku, "canal": canal, "cuenta": cuenta,
                "contenido": {}, "origen": {}}
    return {"existe": True, **doc}


@router.get("/{sku}/canal/{canal}/faltantes")
async def faltantes_canal(sku: str, canal: str, cuenta: str = Query("")):
    """
    El semáforo: qué le falta a este SKU para publicarse en este canal.

    La categoría la resuelve el backend con la precedencia del canal (en Amazon,
    panel > histórico > detección), igual que al guardar.
    """
    from services import channel_content

    if not es_canal_valido(canal):
        raise HTTPException(400, f"Canal '{canal}' inválido.")
    categoria = await _categoria_del_canal(sku, canal)
    return await channel_content.faltantes(sku, canal, cuenta, categoria,
                                           await _datos_publicables(sku))


async def _datos_publicables(sku: str) -> dict[str, Any]:
    """
    Lo que el producto YA TIENE fuera del documento editorial: imágenes,
    precio, stock, peso y medidas.

    Sale de la MISMA función que usa el publicador (`construir_prod`), a
    propósito: el semáforo tiene que medir lo que se va a mandar, no una
    aproximación. Si el publicador lo va a encontrar, el panel no puede decir
    que falta.

    Nunca lanza: sin estos datos el semáforo simplemente vuelve a ser estricto,
    que es como estaba.
    """
    try:
        from services import publicar_ready, studio, wp_db
        if not wp_db.disponible():
            return {}
        wc_id = (await asyncio.to_thread(studio.metadata, sku, None) or {}).get("wc_id")
        if not wc_id:
            return {}
        p = await asyncio.to_thread(publicar_ready.construir_prod, sku, int(wc_id), {})
        return {
            "imagenes": p.get("images") or [],
            "precio_regular": p.get("price"),
            "stock": p.get("stock"),
            "peso": p.get("weight"),
            "largo": p.get("length"), "ancho": p.get("width"), "alto": p.get("height"),
            "titulo": p.get("title"), "descripcion": p.get("description"),
            "sku": sku,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudieron leer los datos publicables de %s: %s", sku, exc)
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# CATEGORÍA POR CANAL — cada canal, su mundo
#
# ML tiene su picker desde hace meses y Amazon el suyo (`TipoAmazonPicker`, que
# escribe la meta `amz_product_type`). TikTok no tenía ninguno: se publicaba con
# lo que dijera su recomendador, que falla el 49% de las veces (medido).
#
# La elección se guarda en `channel.product_category`, donde ya viven las 5,166
# elecciones humanas de ML con `source='panel'`: es el mismo concepto para otro
# canal y su PK `(sku, channel_id)` ya lo admite. Y MANDA sobre el recomendador,
# igual que `ml_categoria_id` manda sobre el predictor (regla 2 de la casa).
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/categorias/tiktok")
def buscar_categorias_tiktok(q: str = Query(..., min_length=2),
                             limite: int = Query(25, ge=1, le=60)):
    """Categorías de TikTok por nombre. SOLO HOJAS: las intermedias rechazan."""
    from services import tiktok_panel
    return {"canal": "tiktok", "resultados": tiktok_panel.buscar_categorias(q, limite)}


class CategoriaCanalReq(BaseModel):
    categoria_id: str


@router.post("/{sku}/canal/tiktok/categoria")
def guardar_categoria_tiktok(sku: str, req: CategoriaCanalReq):
    """Guarda la categoría de TikTok elegida en el panel."""
    from services import tiktok_panel
    r = tiktok_panel.guardar_categoria(sku, req.categoria_id.strip())
    if not r.get("ok"):
        raise HTTPException(400, r.get("motivo") or "No se pudo guardar.")
    return r


@router.get("/{sku}/canal/tiktok/categoria/sugerida")
async def sugerir_categoria_tiktok(sku: str, titulo: str = Query("")):
    """
    Una categoría RECOMENDADA para el SKU. Sugerencia: no se guarda.

    Se guarda cuando una persona la acepta (el POST de arriba). Escribirla sola
    la volvería indistinguible de una elección humana, y toda la precedencia del
    panel se apoya en esa diferencia.
    """
    from services import tiktok_panel, woocommerce
    if not titulo:
        p = await woocommerce.obtener_producto_por_sku(sku)
        titulo = (p or {}).get("nombre") or ""
    return await tiktok_panel.sugerir_categoria(sku, titulo)


@router.get("/{sku}/canal/tiktok/categoria")
def leer_categoria_tiktok(sku: str):
    """
    Qué categoría de TikTok tiene el SKU y DE DÓNDE sale.

    Tres respuestas posibles, y la diferencia importa: `panel` (alguien la
    eligió), `canal` (es la que tiene publicada hoy) o ninguna — y entonces el
    Estudio debe decir por qué no hay, no dejar el hueco en blanco.
    """
    from services import tiktok_panel
    elegida = tiktok_panel.categoria_elegida(sku)
    if elegida and elegida.get("category_id"):
        return {"origen": "panel", **elegida}
    cid = tiktok_panel.categoria_de(sku)
    if not cid:
        return {"origen": None, "category_id": None, "name": None, "path": None}
    from services import supabase_db as sdb
    f = (sdb.fetch_all("select name, path from channel.categories "
                       "where channel_id='tiktok' and category_id=%s", (cid,)) or [{}])[0]
    return {"origen": "canal", "category_id": cid,
            "name": f.get("name"), "path": f.get("path")}


# ── Lo mismo para TEMU ───────────────────────────────────────────────────────
# Mismo contrato que TikTok, y hace más falta todavía: en Temu la categoría no
# es solo dónde aparece el producto, es la que DETERMINA qué atributos existen
# (`template.get` solo responde en hojas). Sin elegirla no hay contenido que
# generar ni alta que mandar.

@router.get("/categorias/temu")
def buscar_categorias_temu(q: str = Query(..., min_length=2),
                           limite: int = Query(25, ge=1, le=60)):
    """Categorías de Temu por nombre o ruta. SOLO HOJAS."""
    from services import temu_panel
    return {"canal": "temu", "resultados": temu_panel.buscar_categorias(q, limite)}


@router.post("/{sku}/canal/temu/categoria")
def guardar_categoria_temu(sku: str, req: CategoriaCanalReq):
    """Guarda la categoría de Temu elegida en el panel."""
    from services import temu_panel
    r = temu_panel.guardar_categoria(sku, req.categoria_id.strip())
    if not r.get("ok"):
        raise HTTPException(400, r.get("motivo") or "No se pudo guardar.")
    return r


@router.get("/{sku}/canal/temu/categoria/sugerida")
async def sugerir_categoria_temu(sku: str, titulo: str = Query("")):
    """
    Categoría RECOMENDADA para el SKU. Sugerencia: NO se guarda sola.

    Dos pasos: Temu propone candidatas (`category.recommend`) y la IA elige
    entre ellas **con permiso de decir que ninguna sirve** (catId 0). Esa salida
    es la que la vuelve portera en vez de adivina — medido sobre 89 productos,
    corrigió la primera opción del recomendador en el 37% y apartó 11 que no
    encajaban en ninguna.
    """
    from services import temu_panel, woocommerce
    if not titulo:
        p = await woocommerce.obtener_producto_por_sku(sku)
        titulo = (p or {}).get("nombre") or ""
    return await temu_panel.sugerir_categoria(sku, titulo)


@router.get("/{sku}/canal/temu/categoria")
def leer_categoria_temu(sku: str):
    """Qué categoría de Temu tiene el SKU y DE DÓNDE sale (panel / canal / nada)."""
    from services import temu_panel
    elegida = temu_panel.categoria_elegida(sku)
    if elegida and elegida.get("category_id"):
        return {"origen": "panel", **elegida}
    cid = temu_panel.categoria_de(sku)
    if not cid:
        return {"origen": None, "category_id": None, "name": None, "path": None}
    from services import supabase_db as sdb
    f = (sdb.fetch_all("select name, path from channel.categories "
                       "where channel_id='temu' and category_id=%s", (cid,)) or [{}])[0]
    return {"origen": "canal", "category_id": cid,
            "name": f.get("name"), "path": f.get("path")}


@router.get("/{sku}/canales/contenido")
async def resumen_contenido_canales(sku: str):
    """Qué canales tienen contenido y cuántos campos — para pintar las pestañas
    sin traerse los documentos completos."""
    from services import channel_content

    return {"sku": sku, "canales": await channel_content.resumen(sku)}


def _paginas(total: int, per_page: int) -> int:
    return max(1, (total + per_page - 1) // per_page)
