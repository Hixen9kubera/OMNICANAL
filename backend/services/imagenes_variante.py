"""
imagenes_variante.py — El MODELO de imágenes de UNA variación de WooCommerce.

POR QUÉ EXISTE (medido el 11-sep-2026 sobre producción)
───────────────────────────────────────────────────────
Hasta aquí, "las fotos de una variante" eran `wp_db.imagenes(wc_id)`: su
miniatura + su galería + la portada del padre + la galería del padre. Y la
galería del PADRE es un cajón de sastre de la familia entera:

  • en 2,408 variaciones de 591 familias se cuelan archivos que llevan en el
    NOMBRE el SKU de una hermana (la foto del color rosa en el anuncio del café);
  • 146 padres guardan en su galería las miniaturas de 2+ hijas;
  • 490 padres tienen como portada la miniatura de una hija;
  • 98 variaciones comparten `_thumbnail_id` con una hermana.

Lo propio de una variación es otra cosa: 6,908 de 7,477 tienen `_thumbnail_id`
propio (su ÚNICA foto en Woo) y 219 tienen además `_product_image_gallery`
PROPIA, que escribe Crear por REST (`crear_producto.py`). 140 no tienen ninguna
foto posible.

Este módulo separa las tres cosas —lo propio, lo heredado y lo que se publica—
para que el Estudio (fase 2) y los publicadores dejen de mezclar familias.

LA REGLA DE PUBLICACIÓN (`para_publicar`)
─────────────────────────────────────────
  • Si la variación tiene galería propia NO vacía → SOLO sus fotos
    (principal + galería). Alguien las eligió para ESE SKU.
  • Si no → su principal + las del padre que NO son de una hermana.
  • Sin repetidos, la principal primero (Amazon y ML toman la primera como
    portada: tiene que ser la del color que se vende).

DÓNDE VIVE LA GALERÍA PROPIA (decisión del 14-sep-2026, ver `META_GALERIA_PROPIA`)
─────────────────────────────────────────────────────────────────────────────
En la meta `_kubera_galeria` de la variación, NO en `_product_image_gallery`.
Regla de LECTURA: si la variación tiene la meta nueva —aunque esté vacía—
manda ella; si no la tiene, se usa `_product_image_gallery` (el legado de
Crear, fila de meta_id más alto). Así lo de Crear se sigue viendo hasta la
primera edición, y borrar todas las fotos no resucita las viejas.

Todas las lecturas son SÍNCRONAS (pymysql vía `wp_db`). Desde una corrutina
van en `asyncio.to_thread` (regla 11). Para un `wc_id` que NO es variación
todas devuelven `None` y el llamador sigue con lo de siempre.

Las ESCRITURAS de la galería por variante (fase 2, detrás de
`settings.galeria_variante`) están al final: una sola PUT por la ruta de la
variación, bajo un `asyncio.Lock` por wc_id, y siempre devuelven lo RELEÍDO.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from services import wp_db

log = logging.getLogger("omnicanal.imagenes_variante")

META_MINIATURA = "_thumbnail_id"
META_GALERIA = "_product_image_gallery"

# La galería PROPIA que edita el Estudio. POR QUÉ NO `_product_image_gallery`
# (medido en SÓLO LECTURA el 14-sep-2026, GET por
# `/products/{padre}/variations/{id}` con `_cb`, contra wp_postmeta):
#
#   • `_product_image_gallery` es meta INTERNA del data store de productos: la
#     REST no la enseña en el `meta_data` de una variación. Variación 139212
#     (VEH-0316-ODY-9904): la fila existe en la BD y la REST devuelve 18 metas
#     sin ella. Sin su id, `WC_Data::update_meta_data` cae a `add_meta_data` y
#     cada escritura AGREGA una fila: 180 de sus 219 variaciones ya tienen
#     varias, y WordPress (`get_post_meta(…, true)`) lee la MÁS VIEJA.
#   • Las metas propias con guion bajo SÍ salen con su id: `_crear_procesada_at`
#     (id 856516 en 139212, 856544 en 139214), `_kubera_cbm` (578522) y
#     `_stock_odoo` (596781) en 78727 (ACC-0661-GALAXY-CAF). Y se actualizan EN
#     SU LUGAR: `_stock_odoo` la reescribe el sync en 5,897 variaciones y NINGUNA
#     tiene fila duplicada; `_kubera_cbm` 3,232 y 0 duplicadas.
#   • `_kubera_galeria` no existía en ninguna variación (0 filas): nadie más la
#     escribe. El guion bajo la esconde de la caja "Campos personalizados" de
#     wp-admin, donde alguien podría editarla a mano sin saber qué es.
#
# Valor: ids de adjunto separados por coma, EN ORDEN, SIN la principal (la
# principal sigue siendo `_thumbnail_id`, que es lo que la tienda pinta).
META_GALERIA_PROPIA = "_kubera_galeria"

# Tokens alfanuméricos: `ROP-0509-NEG-XL-2-scaled.jpg` → ROP 0509 NEG XL 2 SCALED JPG.
_TOKEN = re.compile(r"[A-Z0-9]+")


# ── Lecturas de bajo nivel ───────────────────────────────────────────────────

def _ids_csv(valor: Any) -> list[int]:
    """`"12, 13,,x,12"` → [12, 13]: enteros > 0, sin repetir, en su orden."""
    salida: list[int] = []
    for x in str(valor or "").split(","):
        x = x.strip()
        if x.isdigit() and int(x) > 0 and int(x) not in salida:
            salida.append(int(x))
    return salida


def _variacion(wc_id: int) -> tuple[int, int] | None:
    """(wc_id, padre) si el post es una `product_variation` con padre; si no, None."""
    if not wc_id:
        return None
    P = wp_db._prefix()  # noqa: SLF001
    filas = wp_db._fetch_all(  # noqa: SLF001
        f"""SELECT ID, post_type, post_parent FROM {P}posts WHERE ID = %s LIMIT 1""",
        (int(wc_id),))
    if not filas:
        return None
    f = filas[0]
    if f.get("post_type") != "product_variation" or not f.get("post_parent"):
        return None
    return int(f["ID"]), int(f["post_parent"])


def _filas_imagen(post_ids: list[int]) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """
    { post_id: { meta_key: [filas ordenadas por meta_id ASC] } } para miniatura,
    galería de Woo y galería propia. Se devuelven TODAS las filas, no una: hay
    posts con varias.
    """
    ids = sorted({int(i) for i in post_ids if i})
    if not ids:
        return {}
    P = wp_db._prefix()  # noqa: SLF001
    salida: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join(["%s"] * len(chunk))
        for r in wp_db._fetch_all(  # noqa: SLF001
            f"""SELECT post_id, meta_id, meta_key, meta_value FROM {P}postmeta
                 WHERE post_id IN ({ph}) AND meta_key IN (%s, %s, %s)
                 ORDER BY post_id, meta_id""",
            (*chunk, META_MINIATURA, META_GALERIA, META_GALERIA_PROPIA)):
            salida.setdefault(int(r["post_id"]), {}).setdefault(
                r["meta_key"], []).append(
                {"meta_id": int(r["meta_id"]), "value": r.get("meta_value") or ""})
    return salida


def _ganadora(filas: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    La fila que MANDA cuando una meta está repetida: la de meta_id MÁS ALTO.

    POR QUÉ LA ÚLTIMA. De las 219 variaciones con galería propia, 180 tienen
    MÁS DE UNA fila de `_product_image_gallery` y en 169 los valores difieren
    (medido el 11-sep-2026; la miniatura no tiene ningún caso). Los meta_id
    crecen con cada escritura —p. ej. el post 15240 tiene 4 filas con 4
    galerías sucesivas—, así que la más alta es la ÚLTIMA que alguien escribió.

    Los lectores del sistema no se ponen de acuerdo, y conviene saberlo:
    `wp_db.postmeta` (un dict por clave) se queda con la última, pero
    `get_post_meta(…, true)` de WordPress devuelve la PRIMERA (su caché ordena
    por meta_id ASC y toma [0]). El panel y los publicadores usan esta regla;
    la tienda puede estar mirando otra fila.
    """
    return filas[-1] if filas else None


def _galeria_efectiva(filas_post: dict[str, list[dict[str, Any]]]
                      ) -> tuple[list[int], str | None, list[dict[str, Any]]]:
    """
    (ids, fuente, filas) de la galería que MANDA en un post:

      • `_kubera_galeria` si hay AL MENOS UNA fila, aunque su valor esté vacío
        → fuente "propia". Vacía significa "el Estudio la dejó sin galería", no
        "no sé": por eso no se cae al legado (resucitaría fotos borradas).
      • si no, `_product_image_gallery` (fila más alta) → fuente "legado".
      • ninguna → ([], None, []).

    En un PADRE nunca se escribe la meta nueva, así que para él esto es la
    galería de Woo de siempre.
    """
    propia = filas_post.get(META_GALERIA_PROPIA) or []
    if propia:
        return _ids_csv(_ganadora(propia)["value"]), "propia", propia
    legado = filas_post.get(META_GALERIA) or []
    if legado:
        return _ids_csv(_ganadora(legado)["value"]), "legado", legado
    return [], None, []


def _imagenes_de_post(filas_post: dict[str, list[dict[str, Any]]]
                      ) -> tuple[int | None, list[int], int]:
    """(miniatura, galería, n_filas_galería) de UN post según la fila ganadora."""
    t = _ganadora(filas_post.get(META_MINIATURA) or [])
    galeria, _, g_filas = _galeria_efectiva(filas_post)
    miniatura = _ids_csv(t["value"])[:1] if t else []
    return (miniatura[0] if miniatura else None, galeria, len(g_filas))


def _adjuntos(ids: list[int]) -> dict[int, dict[str, str]]:
    """
    { id: {src, archivo} } de los adjuntos que EXISTEN. `src` es el `guid`
    (la URL que ya usa `wp_db.imagenes`) o, si viene vacío, la ruta de
    `_wp_attached_file` sobre la base de uploads — el mismo criterio que
    `wp_db.imagenes_por_wc_id`. `archivo` es el nombre del fichero: con él se
    detectan las fotos de una hermana.
    """
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return {}
    P = wp_db._prefix()  # noqa: SLF001
    base: str | None = None
    salida: dict[int, dict[str, str]] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join(["%s"] * len(chunk))
        for r in wp_db._fetch_all(  # noqa: SLF001
            f"""SELECT p.ID, p.guid,
                       (SELECT meta_value FROM {P}postmeta
                         WHERE post_id = p.ID AND meta_key = '_wp_attached_file'
                         LIMIT 1) AS archivo
                  FROM {P}posts p
                 WHERE p.ID IN ({ph}) AND p.post_type = 'attachment'""",
            tuple(chunk)):
            src = (r.get("guid") or "").strip()
            archivo = str(r.get("archivo") or "").strip()
            if not src and archivo:
                if base is None:
                    base = wp_db._base_uploads()  # noqa: SLF001
                if base:
                    src = f"{base}/{archivo.lstrip('/')}"
            if src:
                nombre = (archivo or src).rsplit("/", 1)[-1]
                salida[int(r["ID"])] = {"src": src, "archivo": nombre}
    return salida


def _skus(post_ids: list[int]) -> dict[int, str]:
    ids = sorted({int(i) for i in post_ids if i})
    if not ids:
        return {}
    P = wp_db._prefix()  # noqa: SLF001
    ph = ",".join(["%s"] * len(ids))
    return {int(r["post_id"]): str(r.get("meta_value") or "").strip()
            for r in wp_db._fetch_all(  # noqa: SLF001
                f"""SELECT post_id, meta_value FROM {P}postmeta
                     WHERE meta_key = '_sku' AND post_id IN ({ph})""", tuple(ids))
            if str(r.get("meta_value") or "").strip()}


def _hermanas(padre: int, wc_id: int) -> list[int]:
    """
    Las OTRAS variaciones vivas del mismo padre. El parentesco sale de
    `post_parent`, NUNCA del prefijo del SKU: hay SKUs reciclados y familias
    cuyo prefijo no coincide con el del padre.
    """
    P = wp_db._prefix()  # noqa: SLF001
    return [int(r["ID"]) for r in wp_db._fetch_all(  # noqa: SLF001
        f"""SELECT ID FROM {P}posts
             WHERE post_parent = %s AND post_type = 'product_variation'
               AND post_status <> 'trash' AND ID <> %s""",
        (int(padre), int(wc_id)))]


def _tokens(texto: str) -> list[str]:
    return _TOKEN.findall(str(texto or "").upper())


def _lleva_sku(tokens_archivo: list[str], sku: str) -> bool:
    """
    ¿El nombre del archivo contiene el SKU como secuencia COMPLETA de tokens?

    Por token y no por subcadena, a propósito: `ROP-0509-NEG-XL-1.jpg` NO lleva
    el SKU `ROP-0509-NEG-X` (XL ≠ X), y con `in` sobre el texto sí "casaría" y
    se descartaría la foto de la talla que se vende.
    """
    t = _tokens(sku)
    if not t or len(t) > len(tokens_archivo):
        return False
    n = len(t)
    return any(tokens_archivo[i:i + n] == t for i in range(len(tokens_archivo) - n + 1))


def _img(i: int | None, adj: dict[int, dict[str, str]]) -> dict[str, Any] | None:
    if not i or i not in adj:
        return None
    return {"id": int(i), "src": adj[i]["src"]}


# ── El modelo ────────────────────────────────────────────────────────────────

def propias(wc_id: int) -> dict[str, Any] | None:
    """
    Las fotos que son DE la variación:

        { principal: {id, src} | None,     ← su `_thumbnail_id` (≠ 0)
          galeria:   [{id, src}],          ← su galería efectiva (ver abajo)
          galeria_fuente: "propia" | "legado" | None,
          galeria_meta_key: str | None,    ← la meta de donde salió
          filas_galeria: n,                ← cuántas filas de esa meta hay
          galeria_meta_id: int | None,     ← la fila que manda (la más alta)
          galeria_filas_distintas: n,      ← cuántos valores distintos hay
          principal_guardada: int | None,  ← el id tal cual está en la BD
          galeria_guardada: [ids],         ← ídem, incluidos los rotos
          ids_rotos: [ids],                ← apuntan a adjuntos que ya no existen
          padre: wc_id_del_padre }

    Galería efectiva: `_kubera_galeria` si existe (aunque vacía), si no
    `_product_image_gallery` (ver `_galeria_efectiva`). Con filas duplicadas
    manda la de meta_id más alto (ver `_ganadora`). La galería se devuelve TAL
    CUAL está guardada (si repite la principal, la repite); `para_publicar` y
    `ids_editables` deduplican. `principal_guardada`/`galeria_guardada` sirven
    para verificar una escritura contra la BD sin que un adjunto borrado la
    haga parecer fallida.

    None si `wc_id` no es una variación.
    """
    v = _variacion(wc_id)
    if not v:
        return None
    wc_id, padre = v
    filas = _filas_imagen([wc_id]).get(wc_id, {})
    miniatura, galeria_ids, n_filas = _imagenes_de_post(filas)
    _, fuente, g_filas = _galeria_efectiva(filas)
    adj = _adjuntos(([miniatura] if miniatura else []) + galeria_ids)
    ganadora = _ganadora(g_filas)
    todos = ([miniatura] if miniatura else []) + galeria_ids
    return {
        "wc_id": wc_id,
        "padre": padre,
        "principal": _img(miniatura, adj),
        "galeria": [x for x in (_img(i, adj) for i in galeria_ids) if x],
        "galeria_fuente": fuente,
        "galeria_meta_key": {"propia": META_GALERIA_PROPIA,
                             "legado": META_GALERIA}.get(fuente or ""),
        "filas_galeria": n_filas,
        "galeria_meta_id": ganadora["meta_id"] if ganadora else None,
        "galeria_filas_distintas": len({tuple(_ids_csv(f["value"])) for f in g_filas}),
        "principal_guardada": miniatura,
        "galeria_guardada": galeria_ids,
        "ids_rotos": [i for i in todos if i not in adj],
    }


def heredadas(wc_id: int) -> list[dict[str, Any]] | None:
    """
    Portada + galería del PADRE, cada una marcada:

        [{ id, src, origen: "portada"|"galeria", archivo,
           se_publica: bool, motivo: str | None }]

    `se_publica=False` cuando la foto es de una HERMANA:
      • es la miniatura o está en la galería propia de otra variación del
        mismo padre (y no es también de ésta — 98 comparten miniatura), o
      • el NOMBRE del archivo lleva el SKU de una hermana y no el propio
        (comparación por token, ver `_lleva_sku`), o
      • el adjunto ya no existe.

    Una foto del padre que también es propia de la variante se marca
    publicable con su motivo: `para_publicar` la deduplica.

    None si `wc_id` no es una variación.
    """
    v = _variacion(wc_id)
    if not v:
        return None
    wc_id, padre = v
    hermanas = _hermanas(padre, wc_id)
    filas = _filas_imagen([wc_id, padre, *hermanas])

    p_min, p_gal, _ = _imagenes_de_post(filas.get(padre, {}))
    del_padre: list[int] = []
    for i in ([p_min] if p_min else []) + p_gal:
        if i not in del_padre:
            del_padre.append(i)
    if not del_padre:
        return []

    o_min, o_gal, _ = _imagenes_de_post(filas.get(wc_id, {}))
    mias = set(o_gal) | ({o_min} if o_min else set())

    skus = _skus([wc_id, *hermanas])
    sku_propio = skus.get(wc_id, "")
    # id → (sku de la hermana, "miniatura" | "galería")
    de_hermana: dict[int, tuple[str, str]] = {}
    for h in hermanas:
        h_min, h_gal, _ = _imagenes_de_post(filas.get(h, {}))
        etiqueta = skus.get(h) or f"wc_id {h}"
        if h_min:
            de_hermana.setdefault(h_min, (etiqueta, "miniatura"))
        for i in h_gal:
            de_hermana.setdefault(i, (etiqueta, "foto de galería"))
    skus_hermanas = [skus[h] for h in hermanas if skus.get(h)]

    adj = _adjuntos(del_padre)
    salida: list[dict[str, Any]] = []
    for i in del_padre:
        origen = "portada" if i == p_min else "galeria"
        a = adj.get(i)
        fila: dict[str, Any] = {"id": i, "src": a["src"] if a else None,
                                "origen": origen,
                                "archivo": a["archivo"] if a else None,
                                "se_publica": True, "motivo": None}
        if not a:
            fila.update(se_publica=False, motivo="el adjunto ya no existe")
        elif i in mias:
            fila["motivo"] = "también es foto propia de la variante"
        elif i in de_hermana:
            sku_h, cual = de_hermana[i]
            fila.update(se_publica=False,
                        motivo=f"es la {cual} de la hermana {sku_h}")
        else:
            toks = _tokens(a["archivo"])
            ajenas = [s for s in skus_hermanas if _lleva_sku(toks, s)]
            if ajenas and not _lleva_sku(toks, sku_propio):
                fila.update(se_publica=False,
                            motivo=f"el archivo {a['archivo']} lleva el SKU de "
                                   f"la hermana {ajenas[0]}")
        salida.append(fila)
    return salida


def para_publicar(wc_id: int, *, _propias: dict[str, Any] | None = None,
                  _heredadas: list[dict[str, Any]] | None = None,
                  ) -> tuple[list[str], dict[str, Any]] | None:
    """
    (urls, info): lo que se manda al canal para esta variación.

    info = { regla: "propias" | "principal_y_padre" | "solo_padre" | "sin_fotos",
             propias: n, heredadas_usadas: n, descartadas_hermanas: n,
             sin_fotos: bool, descartes: [{id, motivo}], filas_galeria: n }

    `_propias` / `_heredadas`: lo ya leído por quien llama (la vista del
    Estudio lee las dos cosas y no tiene por qué repetir ~10 consultas). Los
    publicadores no los pasan.

    None si `wc_id` no es una variación: el llamador sigue con `wp_db.imagenes`.
    """
    p = _propias if _propias is not None else propias(wc_id)
    if p is None:
        return None
    urls: list[str] = []
    vistos: set[int] = set()

    def _sumar(img: dict[str, Any] | None) -> bool:
        if not img or not img.get("src") or img["id"] in vistos:
            return False
        vistos.add(img["id"])
        urls.append(img["src"])
        return True

    descartes: list[dict[str, Any]] = []
    n_hermanas = 0
    if p["galeria"]:
        # Alguien eligió fotos PARA ESTE SKU: el padre ya no opina.
        _sumar(p["principal"])
        for g in p["galeria"]:
            _sumar(g)
        n_propias = len(urls)
        regla = "propias"
    else:
        _sumar(p["principal"])
        n_propias = len(urls)
        for h in (_heredadas if _heredadas is not None else heredadas(wc_id)) or []:
            if h["se_publica"]:
                _sumar(h)
            else:
                descartes.append({"id": h["id"], "motivo": h["motivo"]})
                if "hermana" in (h["motivo"] or ""):
                    n_hermanas += 1
        if not urls:
            regla = "sin_fotos"
        elif n_propias:
            regla = "principal_y_padre"
        else:
            regla = "solo_padre"

    info = {
        "regla": "sin_fotos" if not urls else regla,
        "propias": n_propias,
        "heredadas_usadas": len(urls) - n_propias,
        "descartadas_hermanas": n_hermanas,
        "sin_fotos": not urls,
        "descartes": descartes,
        "filas_galeria": p["filas_galeria"],
    }
    return urls, info


def aviso_legible(info: dict[str, Any] | None) -> str | None:
    """La línea que ve quien publica, a partir del `info` de `para_publicar`."""
    if not info:
        return None
    if info.get("sin_fotos"):
        extra = (f" ({info['descartadas_hermanas']} del padre descartadas por ser "
                 f"de hermanas)") if info.get("descartadas_hermanas") else ""
        return ("Fotos: sin fotos propias ni heredables" + extra +
                ". El canal lo rechazará o saldrá sin imágenes.")
    partes = [f"{info['propias']} propia(s) de la variante"]
    if info.get("regla") == "propias":
        partes.append("su galería propia manda: las del padre no se usan")
    else:
        partes.append(f"{info['heredadas_usadas']} del padre")
        if info.get("descartadas_hermanas"):
            partes.append(f"{info['descartadas_hermanas']} del padre descartada(s) "
                          f"por ser de hermanas")
    return "Fotos: " + " · ".join(partes) + "."


# ── La vista del Estudio (GET /api/imagenes/{sku} con GALERIA_VARIANTE) ─────

class SinBaseWP(RuntimeError):
    """
    Sin la base de WordPress no se puede saber si un wc_id es variación ni
    cuáles son sus fotos propias. Quien lee cae a lo de siempre; quien ESCRIBE
    no puede —editaría la galería del padre, la de toda la familia— y responde
    503.
    """


class GaleriaInvalida(ValueError):
    """Pedido que no casa con la galería real (id ajeno, orden incompleto…) → 400."""


def resolver(sku: str | None, wc_id: int | None) -> tuple[int, int] | None:
    """
    (wc_id_variación, padre) si el par (sku, wc_id) nombra una VARIACIÓN; None
    si nombra un producto (simple o padre) o nada.

    MANDA EL SKU, igual que `publicar._asegurar_wc_id` (defecto de la v0.498:
    el panel llegó a mandar el SKU de una variante con el wc_id de OTRA fila).
    Aquí equivocarse es peor que al publicar: se editarían las fotos de la
    hermana. Si el `_sku` del wc_id no casa con el pedido, se resuelve por SKU.

    Lanza `SinBaseWP` si la base de WordPress no responde — también cuando
    `wp_db.disponible()` dijo True desde su caché de 300 s y MySQL se cayó
    dentro de esa ventana: el error de pymysql (OperationalError,
    InterfaceError) se traduce a `SinBaseWP`. Sin eso se escapaba sin atrapar
    y, con el flag encendido, el GET daba 502 y eliminar/agregar/procesar 500
    para TODOS los SKUs —simples y padres incluidos—, cuando el contrato pide
    que una no-variación responda idéntico a hoy.
    """
    if not wp_db.disponible():
        raise SinBaseWP("La base de WordPress no está disponible.")
    try:
        return _resolver(sku, wc_id)
    except SinBaseWP:
        raise
    except Exception as exc:  # noqa: BLE001 — pymysql caído a media ventana
        raise SinBaseWP(f"La base de WordPress no respondió: {exc}") from exc


def _resolver(sku: str | None, wc_id: int | None) -> tuple[int, int] | None:
    sku = str(sku or "").strip()
    if wc_id:
        real = _skus([int(wc_id)]).get(int(wc_id), "")
        if not sku or real.upper() == sku.upper():
            return _variacion(int(wc_id))
        log.warning("galería: wc_id %s tiene SKU %r y se pidió %r — se resuelve por SKU",
                    wc_id, real or "(vacío)", sku)
    if not sku:
        return None
    fila = wp_db.productos_por_sku([sku]).get(sku) or {}
    if fila.get("tipo") != "variation" or not fila.get("wc_id"):
        return None
    return _variacion(int(fila["wc_id"]))


def ids_editables(p: dict[str, Any]) -> list[int]:
    """
    Las fotos propias como UNA lista ordenada: principal primero y luego la
    galería, sin repetidos y solo adjuntos que existen. Es lo que el Estudio
    ve, reordena y manda de vuelta; los ids rotos no entran y, por lo tanto, se
    limpian con la primera escritura.
    """
    salida: list[int] = []
    for img in [p.get("principal"), *(p.get("galeria") or [])]:
        if img and img.get("id") and img["id"] not in salida:
            salida.append(int(img["id"]))
    return salida


def _aviso_vista(p: dict[str, Any], info: dict[str, Any]) -> str | None:
    """
    Solo lo que merece atención (None si nada): la pantalla pinta `aviso` como
    advertencia, y la regla de publicación ya viaja aparte en `regla`.
    """
    partes: list[str] = []
    if info.get("sin_fotos"):
        partes.append(aviso_legible(info) or "Sin fotos.")
    if p.get("ids_rotos"):
        partes.append(f"{len(p['ids_rotos'])} foto(s) guardada(s) apuntan a adjuntos "
                      f"que ya no existen; se limpian al guardar la galería.")
    if p.get("galeria_fuente") == "legado" and (p.get("galeria_filas_distintas") or 0) > 1:
        partes.append(f"La galería de esta variante viene de Crear y WordPress guarda "
                      f"{p['galeria_filas_distintas']} versiones distintas: se muestra la "
                      f"más reciente, y la primera edición la deja fija.")
    return " ".join(partes) or None


def vista(wc_id: int) -> dict[str, Any] | None:
    """
    La respuesta de `GET /api/imagenes/{sku}` para una VARIACIÓN (sin `sku` ni
    `progreso`, que los pone el router). Misma forma de hoy + los campos del
    contrato de la fase 2:

        { wc_id, parent_id, es_variacion: true,
          portada: {id, src, position} | None,     ← la principal (o None)
          imagenes: [{id, src, position}],         ← SOLO las propias
          es_variante: true, padre_wc_id, principal_id,
          heredadas: [{id, src, se_publica, motivo}],
          regla, aviso }

    `heredadas` excluye las que ya son propias (se verían dos veces, y
    "adoptarlas" no haría nada) y las de adjunto inexistente (sin `src` no hay
    qué enseñar ni qué adoptar). `regla` es la de `para_publicar`.

    None si `wc_id` no es una variación.
    """
    p = propias(wc_id)
    if p is None:
        return None
    h = heredadas(wc_id) or []
    _, info = para_publicar(wc_id, _propias=p, _heredadas=h) or ([], {})
    srcs = {int(i["id"]): i["src"] for i in [p.get("principal"), *p["galeria"]] if i}
    imagenes = [{"id": i, "src": srcs[i], "position": n}
                for n, i in enumerate(ids_editables(p))]
    propias_ids = {i["id"] for i in imagenes}
    principal = p.get("principal")
    return {
        "wc_id": p["wc_id"],
        "parent_id": p["padre"],
        "es_variacion": True,
        "portada": imagenes[0] if principal else None,
        "imagenes": imagenes,
        "es_variante": True,
        "padre_wc_id": p["padre"],
        "principal_id": int(principal["id"]) if principal else None,
        "heredadas": [{"id": x["id"], "src": x["src"], "se_publica": x["se_publica"],
                       "motivo": x["motivo"]}
                      for x in h if x.get("src") and x["id"] not in propias_ids],
        "regla": info.get("regla"),
        "aviso": _aviso_vista(p, info),
    }


# ── Escritura de bajo nivel ──────────────────────────────────────────────────
#
# Todo sale por `woocommerce.ruta_escritura` → `/products/{padre}/variations/{id}`:
# por `/products/{id}` la REST contesta 200 y NO persiste. NUNCA se escribe
# `images[]` (una variación lo ignora en silencio) ni se toca el padre, las
# hermanas o `commercekit_image_gallery`.

def _filas_galeria(wc_id: int) -> list[dict[str, Any]]:
    """Filas de la meta NUEVA (`_kubera_galeria`); el legado no se escribe más."""
    return (_filas_imagen([wc_id]).get(int(wc_id), {}).get(META_GALERIA_PROPIA) or [])


async def _ruta_variacion(wc_id: int) -> str:
    from services import woocommerce

    if not await asyncio.to_thread(_variacion, int(wc_id)):
        raise GaleriaInvalida(f"{wc_id} no es una variación: su galería vive en el producto.")
    ruta = await woocommerce.ruta_escritura(int(wc_id))
    if "/variations/" not in ruta:
        raise SinBaseWP(f"No se pudo resolver la ruta de variación de {wc_id}.")
    return ruta


async def _meta_galeria(cli: Any, ruta: str, valor: str) -> list[dict[str, Any]]:
    """
    El `meta_data` para escribir `_kubera_galeria` EN SU LUGAR.

    Lectura previa con `_cb` (regla 5: una respuesta cacheada por LiteSpeed no
    traería una meta recién creada y la escritura duplicaría la fila). Si la
    REST enseña la meta, se manda con su `id` —`update_meta_data` la actualiza
    por id—; si no existe, sin id: Woo la crea. Si la lectura falla se manda
    sin id igual: `update_meta_data` sin id busca por CLAVE entre las metas que
    cargó, y esta clave sí la carga (ver `META_GALERIA_PROPIA`).
    """
    ids: list[int] = []
    rg = await cli.get(ruta, params={"_fields": "id,meta_data", "_cb": str(time.time())})
    if rg.status_code == 200:
        ids = [int(m["id"]) for m in (rg.json().get("meta_data") or [])
               if m.get("key") == META_GALERIA_PROPIA and m.get("id")]
    else:
        log.warning("galería %s: GET previo → %s; se escribe por clave", ruta, rg.status_code)
    return ([{"id": i, "key": META_GALERIA_PROPIA, "value": valor} for i in ids]
            or [{"key": META_GALERIA_PROPIA, "value": valor}])


def _limpios(ids: list[Any] | None) -> list[int]:
    salida: list[int] = []
    for i in ids or []:
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in salida:
            salida.append(i)
    return salida


async def fijar_principal(wc_id: int, image_id: int) -> dict[str, Any]:
    """
    Pone `image_id` como foto principal (`_thumbnail_id`) de la variación, sin
    tocar su galería.

    Con `{"image": {"id": …}}` por la ruta de variación: una variación acepta
    UNA `image`. Devuelve LO LEÍDO después de escribir, no lo mandado:
        { ok, http, pedido, principal: {id, src}|None  ← de la BD (sin caché),
          image_rest: {id, src}|None                   ← GET con `_cb` }
    `ok` exige HTTP 2xx Y que la BD diga que la principal es la pedida.

    Bajo nivel: NO toma el candado. Las operaciones del Estudio usan `guardar`.
    """
    from services import woocommerce

    image_id = int(image_id)
    if image_id <= 0:
        raise GaleriaInvalida("image_id debe ser un adjunto (> 0).")
    ruta = await _ruta_variacion(int(wc_id))

    image_rest: dict[str, Any] | None = None
    async with woocommerce._client() as cli:  # noqa: SLF001
        r = await cli.put(ruta, json={"image": {"id": image_id}}, timeout=60.0)
        http = r.status_code
        if http >= 400:
            log.warning("fijar_principal(%s): PUT %s → %s %s", wc_id, ruta, http, r.text[:160])
        # `_cb`: LiteSpeed cachea chunche.shop (regla 5).
        rg = await cli.get(ruta, params={"_fields": "id,image", "_cb": str(time.time())})
        if rg.status_code == 200:
            im = rg.json().get("image") or {}
            if im.get("id"):
                image_rest = {"id": int(im["id"]), "src": im.get("src")}

    leido = await asyncio.to_thread(propias, int(wc_id))
    principal = (leido or {}).get("principal")
    return {"ok": bool(http < 300 and principal and principal["id"] == image_id),
            "http": http, "pedido": image_id,
            "principal": principal, "image_rest": image_rest}


async def fijar_galeria(wc_id: int, ids: list[int]) -> dict[str, Any]:
    """
    Escribe la galería PROPIA (`_kubera_galeria`) con `ids` en ese orden, sin
    tocar la principal. Lista vacía = galería propia vacía (la meta QUEDA, con
    valor vacío: el legado de Crear ya no vuelve a mandar).

    Escribe SOLO esa meta y por su id (ver `_meta_galeria`). Nunca
    `_product_image_gallery`: sus filas viejas se quedan como están —la tienda
    no usa la galería de una variación— y dejan de leerse en cuanto existe la
    nueva.

    Devuelve lo RELEÍDO de la BD:
        { ok, http, pedido, galeria: [{id, src}], filas: [{meta_id, value}],
          filas_con_otro_valor, aviso }
    `filas_con_otro_valor` > 0 querría decir que la REST duplicó la meta (lo que
    le pasaba al legado); se mide en vez de suponer que no pasa.

    Bajo nivel: NO toma el candado.
    """
    from services import woocommerce

    pedido = _limpios(ids)
    ruta = await _ruta_variacion(int(wc_id))
    valor = ",".join(str(i) for i in pedido)
    async with woocommerce._client() as cli:  # noqa: SLF001
        meta = await _meta_galeria(cli, ruta, valor)
        rp = await cli.put(ruta, json={"meta_data": meta}, timeout=60.0)
        http = rp.status_code
        if http >= 400:
            log.warning("fijar_galeria(%s): PUT %s → %s %s", wc_id, ruta, http, rp.text[:160])

    filas = await asyncio.to_thread(_filas_galeria, int(wc_id))
    leido = await asyncio.to_thread(propias, int(wc_id))
    ganadora = _ganadora(filas)
    ids_ganadora = _ids_csv(ganadora["value"]) if ganadora else None
    otras = [f for f in filas if _ids_csv(f["value"]) != pedido]
    aviso = None
    if len(filas) > 1:
        aviso = (f"Hay {len(filas)} filas de {META_GALERIA_PROPIA} "
                 f"({len(otras)} con otro valor): la REST duplicó la meta.")
    return {"ok": bool(http < 300 and ids_ganadora == pedido),
            "http": http, "pedido": pedido,
            "galeria": (leido or {}).get("galeria") or [],
            "filas": filas, "filas_con_otro_valor": len(otras), "aviso": aviso}


# ── Operaciones del Estudio (con candado, siempre devuelven la vista releída) ─

# G5. Un candado por variación: dos clics seguidos (subir y reordenar, o el fin
# de un "Procesar con IA" mientras alguien quita una foto) harían leer-modificar-
# escribir sobre la misma base y el último pisaría al primero — la misma carrera
# que el candado de pedidos (regla 6). Un proceso, un event loop: basta el dict.
_candados: dict[int, asyncio.Lock] = {}


def candado(wc_id: int) -> asyncio.Lock:
    return _candados.setdefault(int(wc_id), asyncio.Lock())


async def _guardar(wc_id: int, antes: dict[str, Any], principal: int | None,
                   galeria: list[int]) -> dict[str, Any]:
    """
    Escribe principal + galería propia en UNA sola PUT y devuelve la vista
    RELEÍDA. Quien llama ya tiene el candado y leyó `antes` DENTRO de él.

    Una PUT y no dos (`fijar_principal` + `fijar_galeria`): si Woo rechaza la
    imagen (`woocommerce_variation_invalid_image_id`, 400) no se escribe NADA,
    en vez de quedar la galería nueva con la principal vieja.

    Quitar la principal manda `{"image": {"id": 0}}`: en el controlador v3 de
    variaciones (`set_variation_image`, WooCommerce trunk leído el 14-sep) un id
    0 sin `src` hace `set_image_id('')`. NO PROBADO contra la tienda: si su
    versión es más vieja y contesta 400, no se escribe nada y la relectura lo
    dice (`ok: false` + aviso).

    Devuelve { ok, http, sin_cambios, **vista, aviso }.
    """
    from services import woocommerce

    galeria = [i for i in _limpios(galeria) if i != principal]
    principal_antes = antes.get("principal_guardada")
    galeria_antes = antes.get("galeria_guardada") or []
    cambia_principal = principal != principal_antes
    cambia_galeria = (galeria != galeria_antes or antes.get("galeria_fuente") != "propia")

    http: int | None = None
    if cambia_principal or cambia_galeria:
        ruta = await _ruta_variacion(int(wc_id))
        cuerpo: dict[str, Any] = {}
        async with woocommerce._client() as cli:  # noqa: SLF001
            if cambia_galeria:
                cuerpo["meta_data"] = await _meta_galeria(
                    cli, ruta, ",".join(str(i) for i in galeria))
            if cambia_principal:
                cuerpo["image"] = {"id": int(principal or 0)}
            rp = await cli.put(ruta, json=cuerpo, timeout=60.0)
            http = rp.status_code
            if http >= 400:
                log.warning("galería variante %s: PUT %s → %s %s",
                            wc_id, ruta, http, rp.text[:200])

    despues = await asyncio.to_thread(propias, int(wc_id))
    v = await asyncio.to_thread(vista, int(wc_id))
    if despues is None or v is None:
        raise SinBaseWP(f"No se pudo releer la variación {wc_id} después de escribir.")
    cuadra = (despues.get("principal_guardada") == principal
              and despues.get("galeria_guardada") == galeria)
    ok = bool(cuadra and (http is None or http < 300))
    avisos = [v.get("aviso")] if v.get("aviso") else []
    if not ok:
        avisos.insert(0, (
            f"WooCommerce respondió {http} y la galería releída no es la pedida "
            f"(principal {despues.get('principal_guardada')} en vez de {principal}; "
            f"galería {despues.get('galeria_guardada')} en vez de {galeria}). "
            f"Lo que ves es lo que quedó guardado."))
    return {"ok": ok, "http": http, "sin_cambios": http is None,
            **v, "aviso": " ".join(avisos) or None}


async def _leer(wc_id: int) -> dict[str, Any]:
    p = await asyncio.to_thread(propias, int(wc_id))
    if p is None:
        raise GaleriaInvalida(f"{wc_id} no es una variación: su galería vive en el producto.")
    return p


def _exigir_propia(p: dict[str, Any], image_id: int) -> None:
    if int(image_id) not in ids_editables(p):
        raise GaleriaInvalida(
            f"La foto {image_id} no es de esta variante (sus fotos propias son "
            f"{ids_editables(p) or 'ninguna'}).")


async def agregar(wc_id: int, media_ids: list[int]) -> dict[str, Any]:
    """Añade adjuntos ya subidos a la galería propia; sin principal, el primero lo es."""
    nuevos = _limpios(media_ids)
    async with candado(wc_id):
        p = await _leer(wc_id)
        actuales = ids_editables(p)
        nuevos = [i for i in nuevos if i not in actuales]
        principal = p["principal"]["id"] if p.get("principal") else None
        galeria = [i for i in actuales if i != principal]
        if principal is None and nuevos:
            principal, nuevos = nuevos[0], nuevos[1:]
        return await _guardar(wc_id, p, principal, galeria + nuevos)


async def quitar(wc_id: int, image_id: int) -> dict[str, Any]:
    """
    Quita una foto PROPIA. Si era la principal, la primera de la galería sube
    a principal (o la variante se queda sin principal). El adjunto NO se borra
    de Media: puede ser la foto del padre o de una hermana.
    """
    image_id = int(image_id)
    async with candado(wc_id):
        p = await _leer(wc_id)
        _exigir_propia(p, image_id)
        actuales = ids_editables(p)
        principal = p["principal"]["id"] if p.get("principal") else None
        galeria = [i for i in actuales if i != principal and i != image_id]
        if principal == image_id:
            principal = galeria.pop(0) if galeria else None
        return await _guardar(wc_id, p, principal, galeria)


async def hacer_principal(wc_id: int, image_id: int) -> dict[str, Any]:
    """Esa foto propia pasa a principal; la anterior, al frente de la galería."""
    image_id = int(image_id)
    async with candado(wc_id):
        p = await _leer(wc_id)
        _exigir_propia(p, image_id)
        actuales = ids_editables(p)
        principal = p["principal"]["id"] if p.get("principal") else None
        galeria = [i for i in actuales if i != principal and i != image_id]
        if principal and principal != image_id:
            galeria.insert(0, principal)
        return await _guardar(wc_id, p, image_id, galeria)


async def reordenar(wc_id: int, ids: list[int]) -> dict[str, Any]:
    """
    Orden COMPLETO de las propias; el primero queda de principal. Tiene que ser
    exactamente el mismo conjunto: un reordenar no agrega ni quita, y un id de
    más o de menos significa que la pantalla trabaja con una galería vieja.
    """
    pedido = [int(i) for i in ids or []]
    async with candado(wc_id):
        p = await _leer(wc_id)
        actuales = ids_editables(p)
        if len(pedido) != len(set(pedido)) or set(pedido) != set(actuales):
            raise GaleriaInvalida(
                f"El orden no trae exactamente las fotos propias de la variante: "
                f"llegaron {pedido}, hay {actuales}. Recarga la galería.")
        if not pedido:  # sin fotos propias: solo limpia ids rotos, si los hay
            return await _guardar(wc_id, p, None, [])
        return await _guardar(wc_id, p, pedido[0], pedido[1:])


async def adoptar(wc_id: int, image_id: int) -> dict[str, Any]:
    """
    Una foto HEREDADA del padre pasa a la galería propia (mismo adjunto, no se
    copia el archivo). Si la variante no tenía principal, la adoptada lo es —
    la misma regla que `agregar`: una variante sin principal la tienda la pinta
    con la foto del padre. Adoptar una que ya es propia no escribe nada.
    """
    image_id = int(image_id)
    async with candado(wc_id):
        p = await _leer(wc_id)
        actuales = ids_editables(p)
        principal = p["principal"]["id"] if p.get("principal") else None
        galeria = [i for i in actuales if i != principal]
        if image_id not in actuales:
            h = await asyncio.to_thread(heredadas, int(wc_id)) or []
            if not any(x["id"] == image_id and x.get("src") for x in h):
                raise GaleriaInvalida(
                    f"La foto {image_id} no es heredada del padre de esta variante.")
            if principal is None:
                principal = image_id
            else:
                galeria.append(image_id)
        return await _guardar(wc_id, p, principal, galeria)


async def reemplazar(wc_id: int, id_map: dict[int, int]) -> dict[str, Any]:
    """
    Cierre de "Procesar con IA" para una variante: cada foto editada ocupa el
    lugar de la original si era PROPIA; si era HEREDADA, la editada se agrega a
    la galería propia (copia al escribir: la del padre queda intacta para las
    hermanas). La base se relee DENTRO del candado, porque la IA tarda minutos y
    la galería pudo cambiar mientras tanto; una original que ya no es propia ni
    heredada (la quitaron en ese rato) no resucita.
    """
    mapa = {int(k): int(v) for k, v in (id_map or {}).items() if k and v}
    async with candado(wc_id):
        p = await _leer(wc_id)
        actuales = ids_editables(p)
        principal = p["principal"]["id"] if p.get("principal") else None
        galeria = [mapa.get(i, i) for i in actuales if i != principal]
        if principal is not None:
            principal = mapa.get(principal, principal)
        ajenas = [k for k in mapa if k not in actuales]
        if ajenas:
            h = {x["id"] for x in (await asyncio.to_thread(heredadas, int(wc_id)) or [])}
            for k in ajenas:
                if k not in h:
                    log.warning("reemplazar(%s): la original %s ya no es propia ni "
                                "heredada; su editada %s no se agrega", wc_id, k, mapa[k])
                    continue
                if principal is None:
                    principal = mapa[k]
                elif mapa[k] not in galeria:
                    galeria.append(mapa[k])
        return await _guardar(wc_id, p, principal, galeria)
