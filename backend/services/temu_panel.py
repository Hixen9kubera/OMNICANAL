"""
temu_panel.py — Lo que el PANEL necesita saber de Temu: qué hay publicado.

Mismo papel que `tiktok_panel.py` y misma fuente (`channel.listings` en kubera).
Hasta el 14-ago la pestaña Temu mostraba **datos de ejemplo** (`ejemplos.py`),
porque `routers/productos.py` mandaba ahí todo canal que no fuera General, ML,
Amazon o TikTok — una maqueta encima de un canal con 160 publicaciones vivas.

LA DIFERENCIA CON TIKTOK, Y POR QUÉ IMPORTA
-------------------------------------------
TikTok dice `ACTIVATE` cuando algo está a la venta, así que el panel puede
afirmarlo. **Temu contesta números** (`status4VO`/`subStatus4VO`: 2/8, 3/2,
4/7…) y no publica qué significan. Solo dos están VERIFICADOS, cruzando
productos cuyo estado real se conocía por el Seller Center:

    2/8     → Incompleto   (los 4 publicados el 13-ago)
    5/None  → Borrador     (los 2 con precio 0.00)

Los otros cinco códigos —87 publicaciones— **no se traducen**. Se muestran
crudos y se dicen crudos. Suponerles significado sería repetir el error que en
TikTok habría atado el fan-out a una casualidad del dato.

CONSECUENCIA OPERATIVA: mientras no se sepa qué código significa "a la venta",
**Temu no entra al fan-out de stock**. No se le escribe inventario a un canal
del que no se sabe qué publicaciones venden.
"""
from __future__ import annotations

import logging
from typing import Any

from services import supabase_db as sdb
from services.temu import ESTADOS

log = logging.getLogger("omnicanal.temu_panel")

CANAL = "temu"

_SEL = """
    select l.sku::text as sku, p.wc_id, p.name as nombre,
           l.price, l.stock_own, l.status, l.situacion, l.listing_id, l.url,
           l.category_id, c.name as categoria_nombre
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


def etiqueta_estado(codigo: str | None) -> str:
    """El código de Temu en palabras, SOLO si está verificado."""
    if not codigo:
        return "sin publicar"
    return ESTADOS.get(codigo) or f"Temu {codigo}"


def _normalizar(r: dict[str, Any]) -> dict[str, Any]:
    codigo = r.get("status")
    return {
        "sku": r["sku"],
        "wc_id": r.get("wc_id"),
        "nombre": r.get("nombre") or r["sku"],
        "precio": float(r["price"]) if r.get("price") is not None else None,
        "precio_base": float(r["price"]) if r.get("price") is not None else None,
        "stock": r.get("stock_own"),
        "estado": etiqueta_estado(codigo),
        # El código crudo viaja aparte: la etiqueta es para leer, el código es
        # para depurar y para el día que se decodifiquen los cinco que faltan.
        "situacion": codigo,
        "categoria_id": r.get("category_id"),
        "categoria_path": ([{"id": r.get("category_id"),
                             "nombre": r.get("categoria_nombre") or r.get("category_id")}]
                           if r.get("category_id") else []),
        # "publicado" = existe en Temu. NO significa "se vende": eso todavía no
        # se puede afirmar (ver el encabezado).
        "publicado": True,
        "item_id": r.get("listing_id"),
        "url": r.get("url"),
        "full": None,
        "full_label": None,
        "origen": "db",
    }


def listar(page: int = 1, per_page: int = 40, search: str | None = None,
           solo_publicados: bool = False, orden: str = "reciente",
           estados: list[str] | None = None,
           skus_filtro: list[str] | None = None,
           solo_activas: bool = False) -> tuple[list[dict[str, Any]], int]:
    """
    Publicaciones de Temu con los filtros de la pantalla. (items, total).

    `solo_activas` es el único filtro de estado que este canal admite, y NO
    afirma que vendan: se queda con la cubeta `VENDIBLES` (`4/7`, 59 de 461),
    que en el propio Seller Center se llama literalmente "Activo o inactivo".
    Por eso `publicaciones_panel` las marca `puede_estar_activa` y el censo
    viaja con su `NOTA_CANAL`: el filtro acota, no promete.
    """
    where, params = [], {"canal": CANAL}
    if search:
        where.append("(l.sku::text ilike %(like)s or p.name ilike %(like)s)")
        params["like"] = f"%{search}%"
    if skus_filtro:
        where.append("l.sku::text = any(%(skus)s)")
        params["skus"] = list(skus_filtro)
    if solo_activas:
        from services import publicaciones_panel
        frag = publicaciones_panel.filtro_sql_activas(CANAL)
        if frag:
            where.append(frag[0])
            params.update(frag[1])
    # `solo_publicados` no filtra nada aquí a propósito: todas las filas de esta
    # tabla SON publicaciones de Temu. Lo que faltaba era distinguir cuáles
    # PUEDEN venderse, y eso es `solo_activas` (arriba).

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
        log.warning("temu_panel.listar falló: %s", exc)
        return [], 0


def contar_publicados() -> int:
    """TODAS las publicaciones del canal (mismo criterio que TikTok: lo que
    existe se ve, aunque no se venda — un borrador es trabajo por destrabar)."""
    try:
        r = sdb.fetch_all("select count(*) as n from channel.listings where canal=%(c)s",
                          {"c": CANAL})
        return int((r or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_panel.contar_publicados falló: %s", exc)
        return 0


def resumen_estados() -> list[dict[str, Any]]:
    """Cuántas publicaciones hay por estado, con su etiqueta cuando se conoce."""
    try:
        filas = sdb.fetch_all(
            """select status, count(*) n, coalesce(sum(stock_own),0) piezas
                 from channel.listings where canal=%(c)s
                group by status order by n desc""", {"c": CANAL})
        return [{"codigo": f["status"], "etiqueta": etiqueta_estado(f["status"]),
                 "publicaciones": int(f["n"]), "piezas": int(f["piezas"] or 0)}
                for f in filas]
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_panel.resumen_estados falló: %s", exc)
        return []


def datos_de(sku: str) -> dict[str, Any] | None:
    """La publicación de Temu de UN SKU, ya normalizada, o None."""
    try:
        filas = sdb.fetch_all(f"{_SEL} and l.sku = %(sku)s::citext limit 1",
                              {"canal": CANAL, "sku": sku})
        return _normalizar(filas[0]) if filas else None
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_panel.datos_de(%s) falló: %s", sku, exc)
        return None


# ── LA CATEGORÍA: buscarla, elegirla y recomendarla ──────────────────────────
#
# Es la pieza que faltaba para publicar un producto NUEVO. Un SKU que ya está en
# Temu trae su hoja en la publicación; uno que no, no tiene de dónde sacarla — y
# sin hoja no hay atributos que pedir, porque en Temu la categoría es la que
# DETERMINA qué atributos existen.
#
# La elección del PANEL manda sobre cualquier recomendador (regla 2 de la casa).
# Se guarda en `channel.product_category`, donde ya viven las 5,166 elecciones
# humanas de Mercado Libre: mismo concepto, otro canal, y su PK (sku, channel_id)
# ya lo admite.

def buscar_categorias(q: str, limite: int = 25) -> list[dict[str, Any]]:
    """Buscador por nombre o ruta. SOLO HOJAS: `template.get` rechaza las
    intermedias ("The catId not a leaf category"), así que ofrecerlas sería
    ofrecer un error.

    Sin acentos en los DOS lados: `ilike` ignora mayúsculas pero no diacríticos,
    y buscar "audifon" dejaba el picker vacío como si el catálogo no tuviera la
    categoría. Se usa `translate` porque la extensión `unaccent` no está en la
    base y pedirla sería un cambio de esquema para un buscador.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []
    try:
        return sdb.fetch_all(
            """select category_id, name, path
                 from channel.categories
                where channel_id = %(canal)s and is_leaf
                  and (translate(lower(name), 'áéíóúüñ', 'aeiouun')
                         like translate(lower(%(like)s), 'áéíóúüñ', 'aeiouun')
                    or translate(lower(path), 'áéíóúüñ', 'aeiouun')
                         like translate(lower(%(like)s), 'áéíóúüñ', 'aeiouun'))
                order by length(name), name
                limit %(limite)s""",
            {"canal": CANAL, "like": f"%{q}%", "limite": limite})
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_panel.buscar_categorias(%s) falló: %s", q, exc)
        return []


def guardar_categoria(sku: str, categoria_id: str) -> dict[str, Any]:
    """La categoría de Temu ELEGIDA EN EL PANEL. Manda sobre el recomendador."""
    try:
        filas = sdb.fetch_all(
            """select name, path, is_leaf from channel.categories
                where channel_id=%s and category_id=%s""", (CANAL, categoria_id))
        if not filas:
            return {"ok": False, "motivo": f"La categoría {categoria_id} no existe en Temu."}
        if not filas[0].get("is_leaf"):
            return {"ok": False,
                    "motivo": f"La categoría {categoria_id} no es una hoja: Temu solo "
                              f"acepta hojas y su plantilla de atributos no responde."}
        sdb.execute(
            """insert into channel.product_category
                 (sku, channel_id, category_id, source, updated_at)
               values (%s::citext, %s, %s, 'panel', now())
               on conflict (sku, channel_id) do update set
                 category_id = excluded.category_id, source = 'panel',
                 updated_at = now()""",
            (sku, CANAL, categoria_id))
        return {"ok": True, "sku": sku, "categoria_id": categoria_id,
                "nombre": filas[0].get("name"), "path": filas[0].get("path")}
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_panel.guardar_categoria(%s): %s", sku, exc)
        return {"ok": False, "motivo": str(exc)}


def categoria_elegida(sku: str) -> dict[str, Any] | None:
    """La elección del panel, si existe, con su nombre legible."""
    try:
        filas = sdb.fetch_all(
            """select pc.category_id, c.name, c.path
                 from channel.product_category pc
                 left join channel.categories c
                        on c.channel_id = pc.channel_id and c.category_id = pc.category_id
                where pc.sku = %s::citext and pc.channel_id = %s""", (sku, CANAL))
        return filas[0] if filas else None
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_panel.categoria_elegida(%s): %s", sku, exc)
        return None


def categoria_de(sku: str) -> str | None:
    """La categoría que MANDA: la del panel primero, la de su publicación después."""
    elegida = categoria_elegida(sku)
    if elegida and elegida.get("category_id"):
        return str(elegida["category_id"])
    d = datos_de(sku)
    return str(d["categoria_id"]) if d and d.get("categoria_id") else None


# El prompt es el del pipeline de tandas (`scripts/publicar_temu.py`), a
# propósito: ya está medido. Sobre 89 productos corrigió la primera opción del
# recomendador de Temu en 33 casos (37%) y apartó 11 que no encajaban en
# ninguna — un palillo para cabello que iba a "Tenedores", un removedor de pelo
# para muebles que iba a "Cepillos para perro".
_PROMPT_CAT = """Eres un catalogador de producto para TEMU Mexico.

PRODUCTO: {titulo}

El recomendador de Temu propuso estas categorias. Elige la que DE VERDAD
corresponde al producto.

{lista}

REGLAS
1. Fijate en QUE ES el producto, no en las palabras que aparecen en el titulo.
   Una REFACCION no va en la categoria del aparato completo: un piston de
   repuesto para silla NO va en "Sillas de oficina". Un proyector de luces NO
   va en "Series de luces".
2. Si NINGUNA corresponde, devuelve catId 0. Publicar en la categoria
   equivocada no da error: el producto queda donde nadie lo busca.

SALIDA — solo JSON:
{{"catId": <catId elegido o 0>, "razon": "<breve>"}}"""


async def sugerir_categoria(sku: str, titulo: str) -> dict[str, Any]:
    """
    Recomienda UNA hoja de Temu para este producto, o ninguna.

    Dos pasos, y el segundo es el que importa: Temu propone candidatas
    (`category.recommend`) y la IA elige entre ellas **con permiso de decir que
    ninguna sirve**. Esa salida es la que convierte al recomendador en portero
    en vez de adivino: sin ella el modelo siempre elige algo.

    La ruta legible sale de `channel.categories` —el árbol ya cargado— y no de
    caminar la API en vivo.
    """
    import asyncio
    from services import ia_generadores, temu

    titulo = (titulo or "").strip()
    if not titulo:
        return {"ok": False, "motivo": "sin título con el que recomendar"}
    try:
        r = await temu.llamar("bg.local.goods.category.recommend",
                              {"goodsName": titulo[:120]})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"el recomendador de Temu falló: {exc}"}

    cands = list(dict.fromkeys([str(c) for c in (r.get("catIdList") or [])]
                               + ([str(r["catId"])] if r.get("catId") else [])))
    if not cands:
        return {"ok": False, "motivo": "Temu no propuso ninguna categoría"}

    filas = sdb.fetch_all(
        """select category_id, name, path, is_leaf from channel.categories
            where channel_id=%s and category_id = any(%s)""", (CANAL, cands))
    porid = {str(f["category_id"]): f for f in filas}
    # Solo hojas: las intermedias no tienen plantilla y no se pueden publicar.
    opciones = [porid[c] for c in cands if c in porid and porid[c].get("is_leaf")]
    if not opciones:
        return {"ok": False, "motivo": "las candidatas de Temu no son hojas conocidas",
                "candidatas": cands}

    lista = "\n".join(f"- catId {o['category_id']}: {o['path']}" for o in opciones)
    res = await asyncio.to_thread(
        ia_generadores._completar,  # noqa: SLF001
        "Devuelve SOLO JSON válido.", _PROMPT_CAT.format(titulo=titulo, lista=lista), 400)
    elegido, razon = None, None
    if res.get("ok"):
        d = ia_generadores._parse_json(res.get("texto", "")) or {}  # noqa: SLF001
        cid = str(d.get("catId") or "0")
        razon = d.get("razon")
        if cid != "0" and cid in porid:
            elegido = cid

    return {
        "ok": True, "sku": sku,
        "sugerida": (porid[elegido] if elegido else None),
        "razon": razon,
        "ninguna": elegido is None,
        "candidatas": [{"categoria_id": o["category_id"], "path": o["path"]}
                       for o in opciones],
    }
