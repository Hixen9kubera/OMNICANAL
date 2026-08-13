"""
tiktok_panel.py — Lo que el PANEL necesita saber de TikTok: qué hay publicado.

QUÉ RESUELVE
------------
La pestaña TikTok existía desde siempre y mostraba **datos de ejemplo**
(`services/ejemplos.py`), porque `routers/productos.py` mandaba ahí todo canal
que no fuera General, ML o Amazon. Con la tienda publicando desde julio, esa
pantalla era una maqueta encima de un canal vivo.

DE DÓNDE LEE, Y POR QUÉ DE AHÍ
------------------------------
De `channel.listings` en la BD kubera, que es donde el censo dejó las 900
publicaciones. **No de MySQL**: desde el 13-ago los espejos inversos están
apagados y `canal_inventario` no recibe nada — leer de ahí sería consultar una
foto que ya nadie actualiza (el mismo error que dejó 964 pedidos fantasma).

ML y Amazon todavía se listan desde MySQL (`meli.listar`, `amazon.listar`); este
módulo es el primero que hace la lectura del panel contra kubera directamente.
Cuando esos dos se muden, éste es el molde.

EL NOMBRE DEL PRODUCTO SALE DE `core.products`
----------------------------------------------
`channel.listings` no guarda título — a propósito: el título por canal vive en
`enrich.channel_content` y el del catálogo en el maestro. Se une por SKU con
`core.products.name`, que es el registro civil.
"""
from __future__ import annotations

import logging
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.tiktok_panel")

CANAL = "tiktok"

# TikTok llama ACTIVATE a lo que está a la venta. El resto (DRAFT, PENDING,
# FAILED) existe pero no se vende, y esa diferencia es la que el panel pinta.
ESTADO_VIVO = "ACTIVATE"

_SEL = """
    select l.sku::text as sku, p.wc_id, p.name as nombre,
           l.price, l.stock_own, l.status, l.situacion, l.listing_id, l.url,
           l.category_id, c.name as categoria_nombre, c.path as categoria_path
      from channel.listings l
      join core.products p on p.sku = l.sku
      left join channel.categories c
             on c.channel_id = %(canal)s and c.category_id = l.category_id
     where l.canal = %(canal)s
"""

_ORDEN = {
    "reciente": "l.updated_at desc nulls last",
    "stock_desc": "l.stock_own desc nulls last",
    "stock_asc": "l.stock_own asc nulls last",
    "precio_desc": "l.price desc nulls last",
    "precio_asc": "l.price asc nulls last",
}


def _normalizar(r: dict[str, Any]) -> dict[str, Any]:
    publicado = (r.get("status") or "") == ESTADO_VIVO
    return {
        "sku": r["sku"],
        "wc_id": r.get("wc_id"),
        "nombre": r.get("nombre") or r["sku"],
        "precio": float(r["price"]) if r.get("price") is not None else None,
        "precio_base": float(r["price"]) if r.get("price") is not None else None,
        "stock": r.get("stock_own"),
        # `status` es del producto y `situacion` es de la AUDITORÍA de TikTok:
        # un ACTIVATE con auditoría FAILED existe, y aplastarlos en un solo
        # texto escondería justo el motivo por el que algo no se vende.
        "estado": r.get("status") or "sin publicar",
        "situacion": r.get("situacion"),
        "categoria_id": r.get("category_id"),
        "categoria_path": ([{"id": r.get("category_id"),
                             "nombre": r.get("categoria_nombre") or r.get("category_id")}]
                           if r.get("category_id") else []),
        "publicado": publicado,
        "item_id": r.get("listing_id"),
        "url": r.get("url"),
        "full": None,
        "full_label": None,
        "origen": "db",
    }


def listar(page: int = 1, per_page: int = 40, search: str | None = None,
           solo_publicados: bool = False, orden: str = "reciente",
           estados: list[str] | None = None,
           skus_filtro: list[str] | None = None) -> tuple[list[dict[str, Any]], int]:
    """Publicaciones de TikTok con los filtros de la pantalla. (items, total)."""
    where, params = [], {"canal": CANAL}
    if search:
        where.append("(l.sku::text ilike %(like)s or p.name ilike %(like)s)")
        params["like"] = f"%{search}%"
    if solo_publicados or (estados and "publicado" in estados and "inactivo" not in estados):
        where.append("l.status = %(vivo)s")
        params["vivo"] = ESTADO_VIVO
    elif estados and "inactivo" in estados and "publicado" not in estados:
        where.append("l.status is distinct from %(vivo)s")
        params["vivo"] = ESTADO_VIVO
    if skus_filtro:
        where.append("l.sku::text = any(%(skus)s)")
        params["skus"] = list(skus_filtro)

    filtro = (" and " + " and ".join(where)) if where else ""
    orden_sql = _ORDEN.get(orden, _ORDEN["reciente"])
    params["limit"] = per_page
    params["offset"] = max(0, (page - 1) * per_page)
    try:
        filas = sdb.fetch_all(
            f"{_SEL}{filtro} order by {orden_sql} limit %(limit)s offset %(offset)s",
            params)
        total = sdb.fetch_all(
            f"""select count(*) as n from channel.listings l
                  join core.products p on p.sku = l.sku
                 where l.canal = %(canal)s{filtro}""", params)
        return [_normalizar(f) for f in filas], int((total or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.listar falló: %s", exc)
        return [], 0


def contar_publicados() -> int:
    """
    TODAS las publicaciones del canal, no solo las que están a la venta.

    Empezó contando solo los `ACTIVATE` (283 de 900) con el argumento de que
    poner 900 prometía catálogo que nadie puede comprar. Brandon pidió lo
    contrario, y tiene razón operativa: **los 599 borradores y los 11 rechazados
    son trabajo que existe y hay que ver**. Esconderlos en el contador los volvía
    invisibles justo para quien tiene que destrabarlos.

    Cuántos se venden se sigue viendo: el botón "Solo publicados" filtra, y cada
    tarjeta lleva su estado.
    """
    try:
        filas = sdb.fetch_all(
            "select count(*) as n from channel.listings where canal=%(canal)s",
            {"canal": CANAL})
        return int((filas or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.contar_publicados falló: %s", exc)
        return 0


def resumen_estados() -> dict[str, int]:
    """{ACTIVATE: 283, DRAFT: 599, …} — para explicar el número de la pestaña."""
    try:
        filas = sdb.fetch_all(
            "select coalesce(status,'?') as s, count(*) as n from channel.listings "
            "where canal=%(canal)s group by 1 order by 2 desc", {"canal": CANAL})
        return {f["s"]: int(f["n"]) for f in filas}
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.resumen_estados falló: %s", exc)
        return {}


def datos_de(sku: str) -> dict[str, Any] | None:
    """
    La publicación de TikTok de UN SKU, ya normalizada, o None.

    La usa el detalle de Omnicanal: sin esto el cajón mostraba General, ML y
    Amazon, y TikTok no existía aunque el producto estuviera publicado.
    """
    try:
        filas = sdb.fetch_all(
            f"{_SEL} and l.sku = %(sku)s::citext limit 1",
            {"canal": CANAL, "sku": sku})
        return _normalizar(filas[0]) if filas else None
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.datos_de(%s) falló: %s", sku, exc)
        return None


def buscar_categorias(q: str, limite: int = 25) -> list[dict[str, Any]]:
    """
    Buscador de categorías de TikTok por nombre, como el picker de ML.

    SOLO DEVUELVE HOJAS. Las intermedias rechazan con `12052024 Category is not
    final category`, así que ofrecerlas sería ofrecer un error.

    ⚠️ `es hoja` se DERIVA aquí (`not exists` un hijo) porque
    `channel.categories` todavía no tiene la columna — está pedida a Eduardo.
    Lo que NO se puede derivar es si la categoría está restringida
    (`INVITE_ONLY`): ese dato se cargó y se tuvo que tirar por la misma razón, y
    es el que deja el producto en `PENDING` para siempre sin dar error. Mientras
    falte, el picker lo advierte en vez de fingir que lo sabe.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []
    try:
        # SIN ACENTOS EN LOS DOS LADOS. `ilike` ignora mayúsculas pero NO
        # diacríticos: buscar "audifon" no encontraba "Audífonos" y el picker
        # salía vacío como si el catálogo no tuviera la categoría. Se usa
        # `translate` en vez de la extensión `unaccent` porque no está instalada
        # en la base y pedirla sería un cambio de esquema para un buscador.
        return sdb.fetch_all(
            """select c.category_id, c.name, c.path
                 from channel.categories c
                where c.channel_id = %(canal)s
                  and (translate(lower(c.name), 'áéíóúüñ', 'aeiouun')
                         like translate(lower(%(like)s), 'áéíóúüñ', 'aeiouun')
                    or translate(lower(c.path), 'áéíóúüñ', 'aeiouun')
                         like translate(lower(%(like)s), 'áéíóúüñ', 'aeiouun'))
                  and not exists (select 1 from channel.categories h
                                   where h.channel_id = c.channel_id
                                     and h.parent_id = c.category_id)
                order by length(c.name), c.name
                limit %(limite)s""",
            {"canal": CANAL, "like": f"%{q}%", "limite": limite})
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.buscar_categorias(%s) falló: %s", q, exc)
        return []


def _palabras_clave(titulo: str, tope: int = 4) -> list[str]:
    """Las palabras del título que sirven para buscar categoría.

    Se quitan las vacías y las de menos de 4 letras: "de", "con", "para" y los
    colores/medidas no acercan a ninguna categoría, y sí ensucian el solape.
    """
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", (titulo or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    vacias = {"para", "con", "sin", "los", "las", "del", "una", "unos", "unas",
              "por", "mas", "muy", "kit", "set", "pza", "pzas", "color", "negro",
              "blanco", "rojo", "azul", "verde", "gris", "multicolor", "cm", "mm"}
    palabras = [p for p in re.findall(r"[a-z]{4,}", t) if p not in vacias]
    # Se prioriza el ORDEN del título: en español el sustantivo va primero
    # ("Termo de acero…"), y esa primera palabra es casi siempre la categoría.
    return palabras[:tope]


async def sugerir_categoria(sku: str, titulo: str) -> dict[str, Any]:
    """
    Una categoría RECOMENDADA, siempre — pero como sugerencia, nunca guardada.

    POR QUÉ NO SE GUARDA SOLA. Si la recomendación se escribiera como elección
    del panel, dejaría de poder distinguirse de una decisión humana, y el panel
    entero se apoya en esa diferencia (regla 2 de la casa). El caso que lo
    enseñó: un "Collar de recuperación para gato" —un cono veterinario— acabó
    clasificado en joyería de disfraces, con confianza y sin error. La máquina
    propone; una persona acepta.

    DOS FUENTES, en orden:
      1. **El recomendador de TikTok**, que es el del propio canal. Falla el 49%
         (medido sobre 245 productos), así que su respuesta viaja marcada.
      2. **La IA eligiendo entre hojas REALES** de nuestro catálogo cuando el
         recomendador no contesta o propone una categoría que no conocemos.

    Devuelve siempre la misma forma; `category_id` en None significa "no me
    atrevo", que es una respuesta legítima y mejor que inventar.
    """
    from services import tiktok as tk

    vacio = {"category_id": None, "name": None, "path": None,
             "origen": None, "confianza": None, "motivo": None}

    # ── 1) El recomendador del canal ─────────────────────────────────────────
    token, cipher = tk.access_token(), tk.cipher()
    if token and cipher and titulo:
        try:
            rec = await tk.llamar("/product/202309/categories/recommend", token,
                                  {"shop_cipher": cipher},
                                  {"product_title": titulo[:255]}, "POST")
            cad = rec.get("categories") or []
            cid = rec.get("leaf_category_id") or (cad[-1].get("id") if cad else None)
            if cid:
                filas = sdb.fetch_all(
                    "select category_id, name, path from channel.categories "
                    "where channel_id=%s and category_id=%s", (CANAL, str(cid)))
                if filas:
                    return {**filas[0], "origen": "recomendador de TikTok",
                            "confianza": None,
                            "motivo": "Es la que propone el propio canal. Acierta "
                                      "poco más de la mitad de las veces: revísala."}
        except Exception as exc:  # noqa: BLE001
            log.info("tiktok_panel: el recomendador no contestó (%s)", exc)

    # ── 2) La IA, eligiendo entre hojas REALES ───────────────────────────────
    candidatas: list[dict[str, Any]] = []
    for palabra in _palabras_clave(titulo):
        for c in buscar_categorias(palabra, 8):
            if c["category_id"] not in {x["category_id"] for x in candidatas}:
                candidatas.append(c)
        if len(candidatas) >= 20:
            break
    if not candidatas:
        return {**vacio, "motivo": "Ninguna categoría de TikTok coincide con las "
                                   "palabras del título. TikTok usa su propio "
                                   "vocabulario: prueba a buscar a mano."}

    from services import ia_generadores
    lista = "\n".join(f"  {c['category_id']} · {c['path'] or c['name']}"
                      for c in candidatas)
    prompt = (
        f"Producto: {titulo}\n\n"
        f"Categorías posibles de TikTok Shop México (todas son finales):\n{lista}\n\n"
        "Elige la que MEJOR describe el producto. Si ninguna encaja de verdad, "
        "devuelve category_id vacío: es preferible no clasificar a clasificar mal.\n"
        'Devuelve SOLO JSON: {"category_id": "<id o vacío>", "confianza": 0.0, '
        '"motivo": "<una frase>"}'
    )
    try:
        import asyncio
        r = await asyncio.to_thread(
            ia_generadores._completar,  # noqa: SLF001
            "Eres un catalogador de producto. Devuelves SOLO JSON válido.",
            prompt, 300)
        if not r.get("ok"):
            return {**vacio, "motivo": "La IA no contestó."}
        d = ia_generadores._parse_json(r.get("texto", "")) or {}  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.sugerir_categoria(%s): %s", sku, exc)
        return {**vacio, "motivo": f"No se pudo sugerir: {exc}"}

    cid = str(d.get("category_id") or "").strip()
    # LA GARANTÍA NO ES EL PROMPT. Se comprueba que el id exista de verdad entre
    # las candidatas: un id inventado no da error, deja el producto mal
    # clasificado y vivo.
    elegida = next((c for c in candidatas if c["category_id"] == cid), None)
    if not elegida:
        return {**vacio, "motivo": (d.get("motivo") or
                                    "La IA no encontró ninguna que encaje.")}
    return {**elegida, "origen": "IA sobre categorías reales",
            "confianza": d.get("confianza"),
            "motivo": d.get("motivo") or "Elegida entre las categorías que coinciden "
                                         "con el título."}


def guardar_categoria(sku: str, categoria_id: str) -> dict[str, Any]:
    """
    La categoría de TikTok ELEGIDA EN EL PANEL. Manda sobre el recomendador.

    Se guarda en `channel.product_category` —donde ya viven las 5,166 elecciones
    humanas de Mercado Libre con `source='panel'`— y no en una tabla nueva: es
    exactamente el mismo concepto para otro canal, y su PK `(sku, channel_id)` ya
    lo admite.
    """
    try:
        filas = sdb.fetch_all(
            "select name, path from channel.categories where channel_id=%s and category_id=%s",
            (CANAL, categoria_id))
        if not filas:
            return {"ok": False, "motivo": f"La categoría {categoria_id} no existe en TikTok."}
        sdb.execute(
            """insert into channel.product_category (sku, channel_id, category_id, source, updated_at)
               values (%s::citext, %s, %s, 'panel', now())
               on conflict (sku, channel_id) do update set
                 category_id = excluded.category_id, source = 'panel', updated_at = now()""",
            (sku, CANAL, categoria_id))
        f = filas[0]
        return {"ok": True, "sku": sku, "categoria_id": categoria_id,
                "nombre": f.get("name"), "ruta": f.get("path")}
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.guardar_categoria(%s) falló: %s", sku, exc)
        return {"ok": False, "motivo": str(exc)[:300]}


def categoria_elegida(sku: str) -> dict[str, Any] | None:
    """La elección del PANEL, si existe, con su nombre legible."""
    try:
        filas = sdb.fetch_all(
            """select pc.category_id, c.name, c.path
                 from channel.product_category pc
                 left join channel.categories c
                        on c.channel_id = pc.channel_id and c.category_id = pc.category_id
                where pc.sku = %s::citext and pc.channel_id = %s""",
            (sku, CANAL))
        return filas[0] if filas else None
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.categoria_elegida(%s) falló: %s", sku, exc)
        return None


def categoria_de(sku: str) -> str | None:
    """
    El `category_id` de TikTok para ese SKU — la llave con la que se buscan sus
    requisitos.

    ⚠️ En TikTok la categoría vive en `listings.category_id`; en Amazon vive en
    `listings.product_type`. Cruzar `field_requirements` por la columna
    equivocada devuelve cero filas SIN dar error, y el semáforo diría
    "sin requisitos" con 1,779 cargados.

    PRECEDENCIA, la misma regla de la casa que en ML y Amazon: **la elección del
    PANEL manda** sobre lo que el canal tenga hoy. Si alguien eligió categoría en
    el Estudio, es la que se usa para pedir requisitos y para publicar.
    """
    elegida = categoria_elegida(sku)
    if elegida and elegida.get("category_id"):
        return str(elegida["category_id"])
    try:
        filas = sdb.fetch_all(
            """select category_id from channel.listings
                where canal=%(canal)s and sku=%(sku)s::citext and category_id is not null
                limit 1""", {"canal": CANAL, "sku": sku})
        return (filas or [{}])[0].get("category_id")
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.categoria_de(%s) falló: %s", sku, exc)
        return None
