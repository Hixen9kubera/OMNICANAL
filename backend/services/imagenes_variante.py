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

Al final del todo, las que NO dependen de ese flag porque las dispara el PADRE:
la copia de sus fotos a las hijas al crearlo (`copiar_fotos_padre`, detrás de
`settings.crear_fotos_a_variantes`) y la sincronización de esas copias cuando
su galería cambia (`sincronizar_copias`, `reemplazar_en_hijas`).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Collection
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


def _galeria_de_crear_vigente(filas_post: dict[str, list[dict[str, Any]]]) -> list[int]:
    """
    Las fotos de galería que hacen a una foto del padre "DE ESTA HERMANA" (ver
    `heredadas`): las que Crear le puso (`_product_image_gallery`, fila
    ganadora) y que siguen en su galería efectiva. NO toda su galería efectiva.

    POR QUÉ (medido el 14-sep-2026 con lecturas reales y escrituras dobles,
    `rev_real_hermanas.py`): desde el sembrado de A1 (`_base_editable`), la
    primera edición de una variante copia a su `_kubera_galeria` las fotos
    GENÉRICAS del padre que ya publicaba. Si esa meta contara como "suya",
    adoptar una foto en MASC-1022-ROS (wc 138777) dejaba a su hermana
    MASC-1022-CAF (wc 138776) de 6 fotos publicables en 0 —regla sin_fotos—, sin
    ningún aviso: el de `_guardar` sólo mide la variante editada. Y cuando CAF
    hiciera su primera edición, su sembrado ya saldría sin ellas y la pérdida
    quedaría fija en su meta.

    Lo de Crear sí es de la hermana (lo subió Crear para ese SKU y el padre lo
    acumula en su cajón de sastre). La intersección con la galería efectiva
    hace que, si en el Estudio le QUITAN a la hermana una foto que Crear le
    puso por error (el "clon sin limpiar"), deje de reclamarla.

    Lo que se pierde a propósito: adoptar una foto genérica del padre ya no la
    "reclama" frente a las hermanas (con el sembrado no hay forma de distinguir
    la adoptada de la sembrada sin guardar más metas). Para reclamarla está la
    principal: la miniatura de una hermana SÍ se sigue descartando.
    """
    legado = filas_post.get(META_GALERIA) or []
    if not legado:
        return []
    efectiva, _, _ = _galeria_efectiva(filas_post)
    return [i for i in _ids_csv(_ganadora(legado)["value"]) if i in efectiva]


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


def _formas_sku(sku: str) -> list[list[str]]:
    """
    Las secuencias de tokens con las que un SKU puede aparecer en un NOMBRE de
    archivo:

      1. el SKU tal cual partido en tokens: `CALZ-0194-BLN/AZL-40` →
         CALZ 0194 BLN AZL 40 (lo que ya se buscaba);
      2. cada segmento separado por `-` reducido a alfanumérico:
         CALZ 0194 BLNAZL 40.

    POR QUÉ LA SEGUNDA (medido en SÓLO LECTURA el 14-sep-2026): WordPress quita
    la diagonal al sanear el nombre del archivo, así que la foto de
    `CALZ-0194-BLN/GRI-40` se llama `CALZ-0194-BLNGRI-40.png` —un token
    BLNGRI— y la forma 1 no casaba nunca. La variación CALZ-0194-BLN/AZL-40
    (wc 27587) heredaba del padre y publicaba BLNGRI-40/-39/-38 de las hermanas
    grises. Hay 293 SKUs con diagonal en el catálogo.

    LÍMITE CONOCIDO (a propósito, sin resolver): una foto nombrada SOLO por el
    color (`zapato-gris-2.jpg`) o con un nombre genérico (`H4da94…webp`) no
    lleva ningún SKU y se sigue publicando como del padre. Adivinar colores por
    palabra suelta daría falsos descartes (GRIS ⊂ GRISACEO, NEGRO en marcas…).
    """
    formas: list[list[str]] = []
    t = _tokens(sku)
    if t:
        formas.append(t)
    segs = [re.sub(r"[^A-Z0-9]", "", s) for s in str(sku or "").upper().split("-")]
    segs = [s for s in segs if s]
    if segs and segs not in formas:
        formas.append(segs)
    return formas


def _lleva_sku(tokens_archivo: list[str], sku: str) -> bool:
    """
    ¿El nombre del archivo contiene el SKU como secuencia COMPLETA de tokens?

    Por token y no por subcadena, a propósito: `ROP-0509-NEG-XL-1.jpg` NO lleva
    el SKU `ROP-0509-NEG-X` (XL ≠ X), y con `in` sobre el texto sí "casaría" y
    se descartaría la foto de la talla que se vende.

    Se prueba cada forma de `_formas_sku` (el SKU con diagonal pierde la
    diagonal en el nombre del archivo).
    """
    for t in _formas_sku(sku):
        n = len(t)
        if n > len(tokens_archivo):
            continue
        if any(tokens_archivo[i:i + n] == t for i in range(len(tokens_archivo) - n + 1)):
            return True
    return False


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
      • es la miniatura de otra variación del mismo padre, o está en la
        galería que CREAR le puso a esa hermana y ella sigue teniendo (ver
        `_galeria_de_crear_vigente`) — y no es también de ésta: 98 comparten
        miniatura —, o
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
        h_min, _, _ = _imagenes_de_post(filas.get(h, {}))
        h_gal = _galeria_de_crear_vigente(filas.get(h, {}))
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
             sin_fotos: bool, descartes: [{id, motivo}], filas_galeria: n,
             ids: [ids de adjunto en el orden de `urls`] }

    `ids` es lo que usa el SEMBRADO de la galería propia (ver
    `_base_editable`): para copiar lo que hoy se publica hace falta el adjunto,
    no la URL.

    `_propias` / `_heredadas`: lo ya leído por quien llama (la vista del
    Estudio lee las dos cosas y no tiene por qué repetir ~10 consultas). Los
    publicadores no los pasan.

    None si `wc_id` no es una variación: el llamador sigue con `wp_db.imagenes`.
    """
    p = _propias if _propias is not None else propias(wc_id)
    if p is None:
        return None
    urls: list[str] = []
    ids: list[int] = []
    vistos: set[int] = set()

    def _sumar(img: dict[str, Any] | None) -> bool:
        if not img or not img.get("src") or img["id"] in vistos:
            return False
        vistos.add(img["id"])
        urls.append(img["src"])
        ids.append(int(img["id"]))
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
        "ids": ids,
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
    if p.get("galeria_fuente") == "propia" and (p.get("filas_galeria") or 0) > 1:
        # A3. `_kubera_galeria` se escribe por su id (ver `_meta_galeria`) y no
        # debería repetirse nunca; si se repite es que la REST volvió a hacer lo
        # de `_product_image_gallery` (180 de 219 variaciones duplicadas). Manda
        # la fila más alta, pero la tienda y otros lectores pueden ver otra.
        partes.append(f"WooCommerce guardó la galería en {p['filas_galeria']} filas: "
                      f"avisa a sistemas.")
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
    r = _vista_e_info(wc_id)
    return r[0] if r else None


def _vista_e_info(wc_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """(vista, propias, info de `para_publicar`) con UNA sola ronda de lecturas:
    `_guardar` necesita las tres para comparar lo que se publicaba antes y después."""
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
    }, p, info


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
                   galeria: list[int], *,
                   heredadas_antes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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

    A1 · RED DE SEGURIDAD DE LO PUBLICABLE. Antes de escribir se mide
    `para_publicar` con `antes` (y `heredadas_antes` si quien llama ya las
    leyó) y después con lo releído. Si baja el número de fotos que se mandarían
    a los canales, el aviso lo dice: «Se publicarán N fotos (antes M)». El
    sembrado de `_base_editable` hace que eso sólo pase cuando alguien QUITA
    fotos; si aparece en otro caso, es un defecto que conviene ver en pantalla
    y no descubrir en el anuncio.

    A3 · FILAS DUPLICADAS. Tras releer se cuentan las filas de
    `_kubera_galeria` (la relectura es un SELECT de TODAS las filas, ver
    `_filas_imagen`). Más de una → log.warning, y la vista lo avisa.

    HERMANAS. Lo único de esta escritura que cambia lo publicable de OTRA
    variante es la principal: la miniatura de una variación descarta esa foto
    del padre en sus hermanas (ver `heredadas`). Medido con lecturas reales y
    PUT dobles (`rev_real_correcciones.py`): adoptar la segunda heredada en
    MUE-0160-NEG (wc 15203, sin miniatura) la hace principal, y sus 2 hermanas
    pasan de 5 a 4 fotos; en COC-0154-NAR, igual. Es a propósito —la portada de
    un color no debe salir en el anuncio de otro—, pero no puede pasar callado:
    si la principal nueva es una foto del padre se avisa cuántas hermanas sin
    galería propia la heredan ("hasta": alguna ya la descartaba por nombre).

    Devuelve { ok, http, sin_cambios, **vista, aviso }.
    """
    from services import woocommerce

    galeria = [i for i in _limpios(galeria) if i != principal]
    principal_antes = antes.get("principal_guardada")
    galeria_antes = antes.get("galeria_guardada") or []
    cambia_principal = principal != principal_antes
    cambia_galeria = (galeria != galeria_antes or antes.get("galeria_fuente") != "propia")

    http: int | None = None
    info_antes: dict[str, Any] | None = None
    if cambia_principal or cambia_galeria:
        r_antes = await asyncio.to_thread(
            para_publicar, int(wc_id), _propias=antes, _heredadas=heredadas_antes)
        info_antes = r_antes[1] if r_antes else None
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

    releido = await asyncio.to_thread(_vista_e_info, int(wc_id))
    if releido is None:
        raise SinBaseWP(f"No se pudo releer la variación {wc_id} después de escribir.")
    v, despues, info_despues = releido
    cuadra = (despues.get("principal_guardada") == principal
              and despues.get("galeria_guardada") == galeria)
    ok = bool(cuadra and (http is None or http < 300))
    avisos = [v.get("aviso")] if v.get("aviso") else []
    if despues.get("galeria_fuente") == "propia" and (despues.get("filas_galeria") or 0) > 1:
        log.warning("galería variante %s: %s quedó en %s filas tras escribir (la REST "
                    "la duplicó); manda la de meta_id más alto", wc_id,
                    META_GALERIA_PROPIA, despues["filas_galeria"])
    if info_antes is not None:
        n_antes = len(info_antes.get("ids") or [])
        n_despues = len(info_despues.get("ids") or [])
        if n_despues < n_antes:
            regla = ""
            if info_antes.get("regla") != info_despues.get("regla"):
                regla = f"; la regla pasó de {info_antes.get('regla')} a {info_despues.get('regla')}"
            avisos.append(f"Se publicarán {n_despues} fotos (antes {n_antes}{regla}).")
    if (http is not None and http < 300 and principal
            and principal != principal_antes and despues.get("principal_guardada") == principal):
        n_h = await asyncio.to_thread(_hermanas_que_heredan, int(despues["padre"]),
                                      int(wc_id), int(principal))
        if n_h:
            avisos.append(f"La foto {principal} también es del padre y ahora es la principal "
                          f"de esta variante: hasta {n_h} hermana(s) que heredan del padre "
                          f"dejan de publicarla.")
    if not ok:
        avisos.insert(0, (
            f"WooCommerce respondió {http} y la galería releída no es la pedida "
            f"(principal {despues.get('principal_guardada')} en vez de {principal}; "
            f"galería {despues.get('galeria_guardada')} en vez de {galeria}). "
            f"Lo que ves es lo que quedó guardado."))
    return {"ok": ok, "http": http, "sin_cambios": http is None,
            **v, "aviso": " ".join(avisos) or None}


def _hermanas_que_heredan(padre: int, wc_id: int, image_id: int) -> int:
    """
    Cuántas hermanas de `wc_id` heredan del padre (galería efectiva vacía) una
    foto que es del padre (`image_id` en su portada o galería) sin tenerla de
    miniatura. 0 si la foto no es del padre. Dos consultas, sin `heredadas` por
    hermana: es un aviso, no una decisión.
    """
    hermanas = _hermanas(int(padre), int(wc_id))
    if not hermanas:
        return 0
    filas = _filas_imagen([int(padre), *hermanas])
    p_min, p_gal, _ = _imagenes_de_post(filas.get(int(padre), {}))
    if int(image_id) != p_min and int(image_id) not in p_gal:
        return 0
    n = 0
    for h in hermanas:
        h_min, h_gal, _ = _imagenes_de_post(filas.get(h, {}))
        if not h_gal and h_min != int(image_id):
            n += 1
    return n


async def _leer(wc_id: int) -> dict[str, Any]:
    p = await asyncio.to_thread(propias, int(wc_id))
    if p is None:
        raise GaleriaInvalida(f"{wc_id} no es una variación: su galería vive en el producto.")
    return p


async def _base_editable(wc_id: int, p: dict[str, Any], *,
                         h: list[dict[str, Any]] | None = None,
                         ) -> tuple[int | None, list[int], list[dict[str, Any]] | None]:
    """
    (principal, galería, heredadas_leídas): la base SEMBRADA sobre la que
    agregan fotos `agregar`, `adoptar` y `reemplazar`.

    A1 · SEMBRADO. Mientras la galería efectiva de la variación está vacía, lo
    que se publica puede incluir fotos del padre (`principal_y_padre` /
    `solo_padre`). En cuanto se escribe la meta con algo dentro, la regla pasa a
    `propias` y el padre deja de opinar. Sin sembrar, adoptar UNA heredada en
    MASC-1022-ROS (wc 138777: principal + 6 del padre = 7 publicables, medido el
    14-sep-2026) dejaba la galería propia con esa sola foto y la siguiente
    publicación mandaba 2 en vez de 7, sin aviso.

    Por eso, la escritura que deja por PRIMERA vez la galería propia con algo
    dentro parte de lo que HOY se publica: los ids de `para_publicar` en su
    orden, sin la principal (ella sigue en `_thumbnail_id`) y, por
    construcción, sin las descartadas (hermanas y adjuntos inexistentes).

    SÓLO CUANDO HACE FALTA (revisión del 14-sep-2026). Sembrar congela en la
    variante una copia de las fotos del padre: lo que después cambie en el
    padre ya no le llega (salvo el reemplazo de la IA, `reemplazar_en_hijas`).
    Si la operación deja la galería propia VACÍA, la herencia sigue viva en
    `para_publicar` y no se pierde nada, así que no se siembra:
      • `quitar`, `hacer_principal` y `reordenar` nunca siembran: con la
        galería vacía sólo pueden tocar la principal. Antes `quitar` la
        principal subía a portada la primera foto del PADRE, y esa miniatura
        nueva la descartaba en todas las hermanas ("es la miniatura de la
        hermana X") — con dobles, la hermana pasaba de [21,20,22,23,24] a
        [21,22,23,24] por quitarle una foto a OTRA variante.
      • `agregar` y `adoptar` siembran sólo si algo va a la GALERÍA (una
        principal nueva sola no apaga la herencia).
      • `reemplazar` siempre: la editada de una heredada tiene que ocupar el
        lugar de la ORIGINAL, que si no se seguiría publicando desde el padre.

    SE DECIDE COMO `para_publicar`: por si la galería efectiva TIENE fotos, no
    por si existe la clave. Una `_kubera_galeria` vacía (o con todos sus ids
    rotos) NO apaga la herencia en `para_publicar` —publica principal + padre—,
    así que también se siembra. Antes se saltaba el sembrado con la clave vacía
    "para no resucitar las del padre", pero ya estaban resucitadas: con dobles,
    VAR con clave "" publicaba 4 (principal_y_padre) y adoptar una heredada la
    dejaba en 2 (propias). Una sola semántica en los dos lugares: "galería
    vacía = hereda del padre" (la de la docstring del módulo).

    Con galería propia no vacía (incluido el legado de Crear) devuelve
    `ids_editables` tal cual: no hay nada que sembrar.

    `h`: heredadas ya leídas por quien llama. `heredadas_leídas` es None si no
    hizo falta leerlas; si se leyeron, `_guardar` las reutiliza en vez de
    repetir ~6 consultas.
    """
    principal, galeria = _base_propia(p)
    if p.get("galeria"):  # misma condición que la regla `propias` de `para_publicar`
        return principal, galeria, h
    if h is None:
        h = await asyncio.to_thread(heredadas, int(wc_id)) or []
    # Con propias y heredadas ya leídas, `para_publicar` no consulta nada.
    r = para_publicar(int(wc_id), _propias=p, _heredadas=h)
    ids = list((r[1].get("ids") if r else None) or [])
    return principal, [i for i in ids if i != principal], h


def _base_propia(p: dict[str, Any]) -> tuple[int | None, list[int]]:
    """(principal, galería) SIN sembrar: sólo lo que el Estudio enseña como propio."""
    principal = int(p["principal"]["id"]) if p.get("principal") else None
    return principal, [i for i in ids_editables(p) if i != principal]


def _exigir_propia(p: dict[str, Any], image_id: int) -> None:
    if int(image_id) not in ids_editables(p):
        raise GaleriaInvalida(
            f"La foto {image_id} no es de esta variante (sus fotos propias son "
            f"{ids_editables(p) or 'ninguna'}).")


async def agregar(wc_id: int, media_ids: list[int]) -> dict[str, Any]:
    """
    Añade adjuntos ya subidos a la galería propia; sin principal, el primero lo
    es. Si alguno va a la galería y la variante heredaba, se siembra antes
    (A1, ver `_base_editable`): la subida se suma a lo que ya se publicaba.
    """
    nuevos = _limpios(media_ids)
    async with candado(wc_id):
        p = await _leer(wc_id)
        principal, galeria = _base_propia(p)
        nuevos = [i for i in nuevos if i not in ([principal] if principal else []) + galeria]
        if principal is None and nuevos:
            principal, nuevos = nuevos[0], nuevos[1:]
        h = None
        if nuevos and not galeria:
            _, galeria, h = await _base_editable(wc_id, p)
            galeria = [i for i in galeria if i != principal]
            nuevos = [i for i in nuevos if i not in galeria]
        return await _guardar(wc_id, p, principal, galeria + nuevos, heredadas_antes=h)


async def quitar(wc_id: int, image_id: int) -> dict[str, Any]:
    """
    Quita una foto PROPIA. Si era la principal, la primera de la galería sube
    a principal (o la variante se queda sin principal). El adjunto NO se borra
    de Media: puede ser la foto del padre o de una hermana.

    No siembra (ver `_base_editable`): en una variante que heredaba, quitar la
    principal la deja sin `_thumbnail_id` y se siguen publicando las mismas del
    padre. NO se sube una foto del padre a portada: esa miniatura la descartaría
    en todas las hermanas.
    """
    image_id = int(image_id)
    async with candado(wc_id):
        p = await _leer(wc_id)
        _exigir_propia(p, image_id)
        principal, galeria = _base_propia(p)
        galeria = [i for i in galeria if i != image_id]
        if principal == image_id:
            principal = galeria.pop(0) if galeria else None
        return await _guardar(wc_id, p, principal, galeria)


async def hacer_principal(wc_id: int, image_id: int) -> dict[str, Any]:
    """Esa foto propia pasa a principal; la anterior, al frente de la galería. No siembra."""
    image_id = int(image_id)
    async with candado(wc_id):
        p = await _leer(wc_id)
        _exigir_propia(p, image_id)
        principal, galeria = _base_propia(p)
        galeria = [i for i in galeria if i != image_id]
        if principal and principal != image_id:
            galeria.insert(0, principal)
        return await _guardar(wc_id, p, image_id, galeria)


async def reordenar(wc_id: int, ids: list[int]) -> dict[str, Any]:
    """
    Orden COMPLETO de las propias; el primero queda de principal. Tiene que ser
    exactamente el mismo conjunto: un reordenar no agrega ni quita, y un id de
    más o de menos significa que la pantalla trabaja con una galería vieja.

    El conjunto se valida contra las propias que la pantalla VE. No siembra (ver
    `_base_editable`): con la galería vacía el pedido es a lo sumo la principal,
    y la herencia sigue igual.
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
    Una foto HEREDADA del padre pasa a las propias (mismo adjunto, no se copia
    el archivo). Si la variante no tenía principal, la adoptada lo es — la
    misma regla que `agregar`: una variante sin principal la tienda la pinta
    con la foto del padre. Adoptar una que ya es propia no escribe nada.

    SIN PRINCIPAL (`solo_padre`) la adoptada pasa a portada AUNQUE ya se
    publicara. Revisión del 14-sep-2026: con el sembrado siempre activo, una
    heredada publicable ya estaba en la base y el bloque que la promovía no
    corría; con dobles, variante sin miniatura y publicables [20,22,23],
    adoptar 22 (el color que se vende) dejaba la portada en 20 y
    `_thumbnail_id` vacío —la tienda seguía pintando la del padre y ML/Amazon
    tomaban la 20 de portada—, y el clic sólo congelaba el sembrado. Medido en
    SÓLO LECTURA: 127 variaciones sin foto propia con 2+ heredadas publicables
    (p. ej. MUE-0160-NEG wc 15203, COC-0154-NAR wc 119063). Ahora queda
    principal 22 y se publican [22,20,23]; la galería no se siembra (la
    herencia sigue viva).

    CON PRINCIPAL, la adoptada va a la galería y, si es la primera foto de
    galería, se siembra antes (A1): si ya se publicaba queda en su lugar y lo
    publicable no cambia; si no (p. ej. la foto de una hermana), va al final: +1.
    """
    image_id = int(image_id)
    async with candado(wc_id):
        p = await _leer(wc_id)
        principal, galeria = _base_propia(p)
        if image_id == principal or image_id in galeria:
            return await _guardar(wc_id, p, principal, galeria)
        h = await asyncio.to_thread(heredadas, int(wc_id)) or []
        if not any(x["id"] == image_id and x.get("src") for x in h):
            raise GaleriaInvalida(
                f"La foto {image_id} no es heredada del padre de esta variante.")
        if principal is None:
            return await _guardar(wc_id, p, image_id, galeria, heredadas_antes=h)
        if not galeria:
            _, galeria, h = await _base_editable(wc_id, p, h=h)
        if image_id not in galeria:
            galeria.append(image_id)
        return await _guardar(wc_id, p, principal, galeria, heredadas_antes=h)


async def reemplazar(wc_id: int, id_map: dict[int, int]) -> dict[str, Any]:
    """
    Cierre de "Procesar con IA" para una variante: cada foto editada ocupa el
    lugar de la original si era PROPIA; si era HEREDADA, la editada se agrega a
    la galería propia (copia al escribir: la del padre queda intacta para las
    hermanas). La base se relee DENTRO del candado, porque la IA tarda minutos y
    la galería pudo cambiar mientras tanto; una original que ya no es propia ni
    heredada (la quitaron en ese rato) no resucita.

    Primera escritura (A1): la base sembrada ya trae las heredadas publicables,
    así que la editada de una de ellas ocupa SU lugar (no se va al final) y las
    demás del padre siguen publicándose.
    """
    mapa = {int(k): int(v) for k, v in (id_map or {}).items() if k and v}
    async with candado(wc_id):
        p = await _leer(wc_id)
        principal, base, h = await _base_editable(wc_id, p)
        actuales = ([principal] if principal else []) + base
        galeria = [mapa.get(i, i) for i in base]
        if principal is not None:
            principal = mapa.get(principal, principal)
        ajenas = [k for k in mapa if k not in actuales]
        if ajenas:
            if h is None:
                h = await asyncio.to_thread(heredadas, int(wc_id)) or []
            ids_h = {x["id"] for x in h}
            for k in ajenas:
                if k not in ids_h:
                    log.warning("reemplazar(%s): la original %s ya no es propia ni "
                                "heredada; su editada %s no se agrega", wc_id, k, mapa[k])
                    continue
                if principal is None:
                    principal = mapa[k]
                elif mapa[k] not in galeria:
                    galeria.append(mapa[k])
        return await _guardar(wc_id, p, principal, galeria, heredadas_antes=h)


async def reemplazar_en_hijas(padre: int, id_map: dict[int, int]) -> dict[str, Any]:
    """
    A2 · "Procesar con IA" corrido sobre el SKU PADRE (rama de siempre).

    Desde el 17-sep-2026 corre SIN importar GALERIA_VARIANTE: con
    CREAR_FOTOS_A_VARIANTES las hijas pueden tener `_kubera_galeria` (la copia
    de Crear) con el flag del Estudio apagado, y seguirían con las originales.

    La rama de siempre (`woocommerce.reemplazar_imagenes_galeria`) cambia los
    ids viejos por los nuevos en la galería del padre, en
    `commercekit_image_gallery` y en el `_thumbnail_id` de cada hija que
    apuntaba a una original. Pero NO conoce `_kubera_galeria`: una hija que ya
    adoptó esa foto del padre seguiría publicando la ORIGINAL (sin fondo
    quitado, sin traducir) mientras el padre y la tienda ya enseñan la editada.

    Aquí, por cada hija viva del padre (`post_parent`, nunca prefijo de SKU) y
    SÓLO si su `_kubera_galeria` contiene alguno de los ids viejos, bajo el
    candado de ESA hija: se relee, se sustituyen los ids en su lugar y se
    escribe con `_guardar` (una PUT de `meta_data` por la ruta de variación).
    La principal NO se toca: ya la cubrió la rama de siempre, y duplicarlo
    serían dos escrituras de `image` compitiendo. Si la miniatura nueva quedó
    también dentro de la galería, `_guardar` la quita de la galería (no se
    publica dos veces).

    Las hijas SIN la meta nueva no se tocan: siguen heredando del padre, que ya
    tiene las editadas. Tampoco se siembra (A1) nada: esto no es una edición de
    la hija sino la propagación de una del padre.

    Sin hijas con la meta es una lectura y nada más. Devuelve
        { hijas: n, con_originales: [wc_id], actualizadas: [wc_id],
          fallidas: [{wc_id, error}] }
    """
    mapa = {int(k): int(v) for k, v in (id_map or {}).items() if k and v}
    res: dict[str, Any] = {"hijas": 0, "con_originales": [], "actualizadas": [], "fallidas": []}
    if not mapa or not padre:
        return res
    hijas = await asyncio.to_thread(_hermanas, int(padre), 0)
    res["hijas"] = len(hijas)
    if not hijas:
        return res
    filas = await asyncio.to_thread(_filas_imagen, hijas)

    def _con_originales(h: int) -> bool:
        g = _ganadora(filas.get(h, {}).get(META_GALERIA_PROPIA) or [])
        return bool(g) and any(i in mapa for i in _ids_csv(g["value"]))

    for h in [x for x in hijas if _con_originales(x)]:
        res["con_originales"].append(h)
        try:
            async with candado(h):
                p = await _leer(h)  # relectura DENTRO del candado
                if p.get("galeria_fuente") != "propia":
                    continue
                antes = list(p.get("galeria_guardada") or [])
                nueva = [mapa.get(i, i) for i in antes]
                if nueva == antes:  # la cambiaron mientras la IA corría
                    continue
                r = await _guardar(h, p, p.get("principal_guardada"), nueva)
            if r.get("ok"):
                res["actualizadas"].append(h)
            else:
                res["fallidas"].append({"wc_id": h, "error": r.get("aviso")})
        except Exception as exc:  # noqa: BLE001 — una hija no frena a las demás
            log.warning("reemplazar_en_hijas(%s): hija %s: %s", padre, h, exc)
            res["fallidas"].append({"wc_id": h, "error": str(exc)})
    return res


# ── Fotos del PADRE guardadas en sus hijas (Crear) y su sincronización ───────
#
# POR QUÉ (17-sep-2026). Crear procesa un PADRE con UNA URL de Alibaba y Apify
# (happitap~alibaba-product-scraper) devuelve una lista PLANA de ~6 fotos, sin
# fotos por color: no hay forma de repartirlas por variante. Hasta aquí esas
# fotos sólo llegaban a la portada + galería del padre y las hijas publicaban
# lo HEREDADO (`para_publicar`: principal + padre sin lo de hermanas), que se
# recalcula en cada lectura y se ensucia en cuanto el padre acumula fotos de
# hermanas (2,408 variaciones de 591 familias, medido el 11-sep).
#
# Con `settings.crear_fotos_a_variantes`, al procesar el padre se COPIAN a la
# `_kubera_galeria` de cada hija. Dos familias medidas marcan los límites:
#   • MASC-1022: las variantes son el MISMO producto en colores → la copia es
#     justo lo que se quiere;
#   • VEH-0316: cada variante es OTRO producto con su propia URL de Alibaba y
#     ya se procesó sola → tiene galería propia y NUNCA se pisa.
#
# La regla es la de `_decidir`; `plan_fotos_padre` sólo lee y
# `copiar_fotos_padre` / `sincronizar_copias` aplican con `_guardar` bajo el
# candado de cada hija. `_thumbnail_id` de las hijas nunca se toca: es la foto
# del color que se vende, y la tienda la pinta.

# A lo sumo 3 hijas escribiendo a la vez: cada una es GET + PUT a chunche.shop
# más ~8 SELECT de relectura; una familia de 20 tallas en paralelo sería una
# ráfaga contra el WAF de Hostinger (el 403 intermitente de siempre).
_CONCURRENCIA_HIJAS = 3


def _resumen_vacio(padre: int | None, **extra: Any) -> dict[str, Any]:
    return {"padre": int(padre or 0), "hijas": 0, "copiadas": 0, "actualizadas": 0,
            "omitidas": 0, "con_fotos_propias": 0, "fallidas": 0, "detalle": [], **extra}


def hijas_vivas(padre: int) -> list[int]:
    """Las variaciones no borradas de `padre` (por `post_parent`). SÍNCRONA."""
    return _hermanas(int(padre), 0) if padre else []


def fotos_del_padre(padre: int) -> list[int]:
    """
    [portada] + galería del PADRE, sin repetidos y sólo adjuntos existentes.

    Es la "lista del padre" que se copia a las hijas y contra la que se
    reconoce una copia intacta. Sale de MySQL (sin caché de LiteSpeed, regla 5):
    alimenta escrituras. SÍNCRONA → `asyncio.to_thread` desde corrutinas.
    """
    if not padre:
        return []
    filas = _filas_imagen([int(padre)]).get(int(padre), {})
    p_min, p_gal, _ = _imagenes_de_post(filas)
    ids = _limpios(([p_min] if p_min else []) + p_gal)
    adj = _adjuntos(ids)
    return [i for i in ids if i in adj]


def copia_para(miniatura: int | None, lista: list[int],
               excluir: Collection[int] = ()) -> list[int]:
    """
    La copia de `lista` que le toca a una hija: sin su `_thumbnail_id` y sin
    las fotos de sus HERMANAS (`excluir`, ver `_excluidas_por_hermanas`).

    La principal vive en `_thumbnail_id` y `_guardar` la quita de la galería de
    todos modos; si la copia la trajera, la comparación "copia intacta" fallaría
    siempre contra lo releído y la hija pasaría por "personalizada" a la primera.
    """
    fuera = set(excluir)
    return [i for i in _limpios(lista) if i != miniatura and i not in fuera]


def _excluidas_por_hermanas(hijas: list[int], filas: dict[int, dict[str, list[dict[str, Any]]]],
                            skus: dict[int, str], adj: dict[int, dict[str, str]],
                            candidatas: list[int]) -> dict[int, set[int]]:
    """
    { hija: ids de `candidatas` que son de OTRA hija }, con el MISMO criterio que
    `heredadas`: la miniatura de una hermana, la galería que Crear le puso a una
    hermana y ella sigue teniendo (`_galeria_de_crear_vigente`), o un archivo
    que lleva el SKU de una hermana y no el propio. Lo que Crear le puso a la
    propia hija nunca se excluye.

    POR QUÉ (revisión del 17-sep-2026, probado con dobles): `copia_para` sólo
    quitaba la miniatura de la propia hija. Con GALERIA_VARIANTE apagada, subir
    una foto desde el Estudio de la variante FAM-0001-B la agrega al PADRE como
    `FAM-0001-B-foto`, y `sincronizar_copias` la metía en todas las copias
    intactas —junto con la miniatura de otra hermana—, mientras que la hermana
    que hereda las descartaba. Es la suciedad que ya afectaba a 2,408
    variaciones de 591 familias (medido el 11-sep): la copia tiene que publicar
    lo mismo que publicaría la herencia.

    A diferencia de `heredadas`, la galería efectiva de la hija NO cuenta como
    "suya": en una hija con copia, esa galería ES la lista del padre, y contarla
    haría que ninguna foto de hermana se excluyera nunca.
    """
    cand = _limpios(candidatas)
    if not cand or not hijas:
        return {h: set() for h in hijas}
    miniaturas: dict[int, int | None] = {}
    de_crear: dict[int, set[int]] = {}
    for h in hijas:
        miniaturas[h], _, _ = _imagenes_de_post(filas.get(h, {}))
        de_crear[h] = set(_galeria_de_crear_vigente(filas.get(h, {})))
    tokens = {i: _tokens(adj[i]["archivo"]) for i in cand if i in adj}
    salida: dict[int, set[int]] = {}
    for h in hijas:
        otras = [x for x in hijas if x != h]
        de_otra: set[int] = set()
        for x in otras:
            if miniaturas[x]:
                de_otra.add(int(miniaturas[x]))
            de_otra |= de_crear[x]
        skus_otras = [skus[x] for x in otras if skus.get(x)]
        sku_h = skus.get(h, "")
        fuera: set[int] = set()
        for i in cand:
            if i in de_crear[h]:
                continue
            if i in de_otra:
                fuera.add(i)
            elif i in tokens and skus_otras:
                if (any(_lleva_sku(tokens[i], s) for s in skus_otras)
                        and not _lleva_sku(tokens[i], sku_h)):
                    fuera.add(i)
        salida[h] = fuera
    return salida


def _es_copia_intacta(galeria: list[int], miniatura: int | None,
                      lista_antes: list[int], excluir: Collection[int]) -> bool:
    """
    ¿`galeria` es la copia que se le escribió a partir de `lista_antes`?

    Es la lista anterior, EN SU ORDEN, a la que sólo le faltan fotos que hoy son
    de una hermana (`excluir`). No se exige igualdad con la copia filtrada de
    hoy porque el filtro depende del estado de las hermanas, que cambia: una
    copia escrita antes de que una foto pasara a ser miniatura de otra hija la
    trae, y no por eso alguien la personalizó. Lo que sí delata una edición en el
    Estudio: una foto que no venía del padre, otro orden, o quitar una foto que
    no es de ninguna hermana → no es intacta y no se toca.

    Límite conocido (a propósito): si una foto dejó de ser de una hermana
    después de copiar (la hermana cambió de miniatura), a la copia le "falta" y
    la hija se toma por personalizada. Se prefiere congelar una copia a pisar
    una edición.
    """
    base = copia_para(miniatura, lista_antes)
    if not galeria or not base:
        return False
    fuera = set(excluir)
    j = 0
    for i in base:
        if j < len(galeria) and galeria[j] == i:
            j += 1
        elif i not in fuera:
            return False
    return j == len(galeria)


def _decidir(miniatura: int | None, galeria: list[int], fuente: str | None,
             lista_antes: list[int], lista_nueva: list[int], *,
             copiar_sin_fotos: bool, procesada: bool = False,
             excluir: Collection[int] = (), en_crear: bool = False,
             familia: str | None = None) -> tuple[str, str, list[int]]:
    """
    (accion, motivo, galería) para UNA hija. `galeria` = su galería efectiva
    (ver `_galeria_efectiva`) con sólo adjuntos existentes; `fuente` de dónde
    salió ("propia" = `_kubera_galeria`, "legado" = `_product_image_gallery`).
    `excluir`: las fotos de sus hermanas (`_excluidas_por_hermanas`).

      0. `procesada` (lleva `_crear_procesada_at`: Crear la procesó SOLA, con
         SU URL de Alibaba) → "omitir" SIEMPRE, también si guarda una copia
         intacta. Medido el 17-sep-2026 en VEH-0316-ACC-0211 (wc 139214):
         procesada sola, principal 143358 propia y galería vacía; con la regla 1
         recibía las fotos del PADRE, que es OTRO producto. Y en la revisión del
         mismo día (dobles): una variante que recibió la copia y DESPUÉS se
         procesó sola caía en "actualizar" en cada corrida del padre.
      0b. `en_crear`: la hija está en la cola de Crear o procesándose → "omitir".
         Su propio alta está a punto de escribir sus fotos.
      0c. `familia` (sólo Crear): alguna hija de la familia se procesó sola o
         está en Crear → la familia se trabaja POR VARIANTE (cada una es otro
         producto, VEH-0316) y no se copia a ninguna. Medido en sólo lectura:
         sin esto, 15 variantes de VEH-0316 pasaban de `sin_fotos` a publicar
         fotos de otro producto. En MASC-1022 (mismo producto en colores, sin
         procesadas) no cambia nada.
      1. Sin fotos propias efectivas (sin metas, legado vacío o `_kubera_galeria`
         vacía) y `copiar_sin_fotos` (Crear) → "copiar" la lista nueva.
         `sincronizar_copias` NO copia a éstas: una hija que hereda del padre ya
         ve el cambio sin escribir nada, y crearle la meta congelaría la copia.
      2. `_kubera_galeria` ya igual a la copia nueva → "omitir" (nada que
         escribir). Copia intacta de la lista ANTERIOR (`_es_copia_intacta`) →
         "actualizar".
      3. Cualquier otra cosa → "omitir": fotos propias (el legado de Crear de una
         variante procesada sola) o una copia que alguien personalizó.
    """
    if procesada:
        return "omitir", "ya tiene fotos propias (procesada sola en Crear)", galeria
    if en_crear:
        return "omitir", "se está procesando sola en Crear", galeria
    if familia and copiar_sin_fotos:
        return "omitir", f"familia trabajada por variante ({familia})", galeria
    nueva = copia_para(miniatura, lista_nueva, excluir)
    if not galeria:
        if not copiar_sin_fotos:
            return "omitir", "sin copia del padre: sigue heredando", []
        if not nueva:
            # Una meta vacía cambiaría de dónde se lee sin sumar ninguna foto.
            return "omitir", "el padre no tiene más fotos que su miniatura y las de hermanas", []
        return "copiar", "sin fotos propias", nueva
    if fuente == "propia":
        if galeria == nueva:
            return "omitir", "copia del padre, ya al día", galeria
        if _es_copia_intacta(galeria, miniatura, lista_antes, excluir):
            return "actualizar", "copia del padre, actualizada", nueva
    return "omitir", f"ya tiene fotos propias ({len(galeria)})", galeria


def _procesadas(post_ids: list[int]) -> set[int]:
    """Las variaciones que Crear procesó SOLAS (`wp_db.META_PROCESADA`)."""
    ids = sorted({int(i) for i in post_ids if i})
    if not ids:
        return set()
    P = wp_db._prefix()  # noqa: SLF001
    ph = ",".join(["%s"] * len(ids))
    return {int(r["post_id"]) for r in wp_db._fetch_all(  # noqa: SLF001
        f"""SELECT DISTINCT post_id FROM {P}postmeta
             WHERE meta_key = %s AND meta_value <> '' AND post_id IN ({ph})""",
        (wp_db.META_PROCESADA, *ids))}


# (wc_ids, skus) de lo que está en cola o procesándose en Crear. Lo arma quien
# llama (crear_producto) DESDE LA CORRUTINA: su dict de progreso lo muta el event
# loop y no se itera desde un hilo.
EnCrear = tuple[set[int], set[str]]
_NADA_EN_CREAR: EnCrear = (set(), set())


def _activa(wc_id: int, sku: str | None, en_crear: EnCrear) -> bool:
    ids, skus = en_crear
    return int(wc_id) in ids or bool(sku and sku in skus)


def _plan(padre: int, lista_antes: list[int], lista_nueva: list[int], *,
          copiar_sin_fotos: bool, en_crear: EnCrear = _NADA_EN_CREAR) -> dict[str, Any]:
    """Plan de SÓLO LECTURA sobre las hijas vivas de `padre` (ver `_decidir`)."""
    padre = int(padre or 0)
    hijas = _hermanas(padre, 0) if padre else []
    filas = _filas_imagen(hijas)
    skus = _skus(hijas)
    procesadas = _procesadas(hijas)
    minis: dict[int, int | None] = {}
    gals: dict[int, tuple[list[int], str | None]] = {}
    todos: list[int] = [*_limpios(lista_antes), *_limpios(lista_nueva)]
    for h in hijas:
        m, g, _ = _imagenes_de_post(filas.get(h, {}))
        _, fuente, _ = _galeria_efectiva(filas.get(h, {}))
        minis[h], gals[h] = m, (g, fuente)
        todos += g
    adj = _adjuntos(todos)  # UNA consulta para padre + todas las hijas
    antes = [i for i in _limpios(lista_antes) if i in adj]
    nueva = [i for i in _limpios(lista_nueva) if i in adj]
    excluidas = _excluidas_por_hermanas(hijas, filas, skus, adj, [*antes, *nueva])
    activas = {h for h in hijas if _activa(h, skus.get(h), en_crear)}
    familia = None
    if copiar_sin_fotos and (procesadas or activas):
        partes = []
        if procesadas:
            partes.append(f"{len(procesadas)} procesada(s) sola(s)")
        if activas:
            partes.append(f"{len(activas)} en Crear")
        familia = ", ".join(partes)
    variantes = []
    for h in sorted(hijas):
        g, fuente = gals[h]
        galeria = [i for i in g if i in adj]
        accion, motivo, destino = _decidir(minis[h], galeria, fuente, antes, nueva,
                                           copiar_sin_fotos=copiar_sin_fotos,
                                           procesada=h in procesadas,
                                           excluir=excluidas.get(h, set()),
                                           en_crear=h in activas, familia=familia)
        variantes.append({"wc_id": h, "sku": skus.get(h, ""), "accion": accion,
                          "motivo": motivo, "galeria": destino,
                          "miniatura": minis[h], "galeria_actual": galeria,
                          "fuente": fuente, "procesada": h in procesadas,
                          "excluidas": sorted(excluidas.get(h, set()))})
    return {"padre": padre, "lista_antes": antes, "lista_padre": nueva,
            "familia": familia, "variantes": variantes}


def plan_fotos_padre(padre_id: int, lista_antes: list[int],
                     lista_padre: list[int], *,
                     en_crear: EnCrear = _NADA_EN_CREAR) -> dict[str, Any]:
    """
    Qué haría `copiar_fotos_padre` en cada hija viva, SIN escribir:

        { padre, lista_padre, lista_antes, familia,
          variantes: [{wc_id, sku, accion: "copiar"|"actualizar"|"omitir",
                       motivo, galeria, miniatura, galeria_actual, fuente,
                       procesada, excluidas}] }

    `galeria` es la que se escribiría (o la actual, si se omite); `excluidas`,
    las fotos del padre que son de una hermana y no entran en la copia. SÍNCRONA.
    """
    return _plan(padre_id, lista_antes, lista_padre, copiar_sin_fotos=True,
                 en_crear=en_crear)


async def _aplicar(padre: int, lista_antes: list[int], lista_nueva: list[int], *,
                   copiar_sin_fotos: bool, quien: str,
                   en_crear: Callable[[], EnCrear] | None = None) -> dict[str, Any]:
    """
    Aplica el plan hija por hija. NUNCA lanza: una hija que falla no frena a las
    demás, y quien llama (un alta de Crear, una edición del padre) ya escribió
    lo suyo y no debe darse por fallido por esto.

    El plan sólo ELIGE a quién abrirle el candado: la decisión se REPITE dentro
    del candado con la hija releída —y con su marca de procesada y el estado de
    Crear vueltos a mirar—, porque entre el plan y la escritura alguien pudo
    editarla en el Estudio o Crear pudo terminar de procesarla sola.

    Quien llama debe tener el candado del PADRE (`candado(padre)`) desde antes de
    leer `lista_antes`: dos ediciones del padre solapadas dejaban a una hija con
    una copia que ya no casaba con ninguna lista futura (revisión 17-sep-2026).
    """
    res = _resumen_vacio(padre)
    try:
        foto_crear = en_crear() if en_crear else _NADA_EN_CREAR
        plan = await asyncio.to_thread(_plan, int(padre or 0), lista_antes, lista_nueva,
                                       copiar_sin_fotos=copiar_sin_fotos, en_crear=foto_crear)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s(%s): no se pudo leer a las hijas: %s", quien, padre, exc)
        res["error"] = f"No se pudo leer a las variantes: {exc}"
        return res
    antes, nueva = plan["lista_antes"], plan["lista_padre"]
    res["hijas"] = len(plan["variantes"])
    res["familia_por_variante"] = plan["familia"]
    familia_ids = {int(v["wc_id"]) for v in plan["variantes"]}
    familia_skus = {v["sku"] for v in plan["variantes"] if v["sku"]}
    sem = asyncio.Semaphore(_CONCURRENCIA_HIJAS)

    async def _una(v: dict[str, Any]) -> dict[str, Any]:
        fila = {k: v[k] for k in ("wc_id", "sku", "accion", "motivo", "galeria")}
        if v["accion"] == "omitir":
            return fila
        h = int(v["wc_id"])
        try:
            async with sem, candado(h):
                p = await _leer(h)  # relectura DENTRO del candado
                procesada = bool(v.get("procesada")) or h in await asyncio.to_thread(
                    _procesadas, [h])
                ahora = en_crear() if en_crear else _NADA_EN_CREAR
                familia = plan["familia"]
                if copiar_sin_fotos and not familia and (
                        familia_ids & ahora[0] or familia_skus & ahora[1]):
                    familia = "una variante entró a Crear"
                accion, motivo, galeria = _decidir(
                    p.get("principal_guardada"),
                    [int(x["id"]) for x in p.get("galeria") or []],
                    p.get("galeria_fuente"), antes, nueva,
                    copiar_sin_fotos=copiar_sin_fotos, procesada=procesada,
                    excluir=v.get("excluidas") or (),
                    en_crear=_activa(h, v["sku"], ahora), familia=familia)
                fila.update(accion=accion, motivo=motivo, galeria=galeria)
                if accion == "omitir":
                    return fila
                # La principal se pasa TAL CUAL está guardada: `_guardar` no
                # manda `image` y `_thumbnail_id` no se toca.
                r = await _guardar(h, p, p.get("principal_guardada"), galeria)
            if not r.get("ok"):
                fila.update(accion="fallida",
                            error=r.get("aviso") or f"HTTP {r.get('http')}")
        except Exception as exc:  # noqa: BLE001 — una hija no frena a las demás
            log.warning("%s(%s): hija %s: %s", quien, padre, h, exc)
            fila.update(accion="fallida", error=str(exc)[:300])
        return fila

    for f in await asyncio.gather(*[_una(v) for v in plan["variantes"]]):
        res["detalle"].append(f)
        if f["accion"] == "copiar":
            res["copiadas"] += 1
        elif f["accion"] == "actualizar":
            res["actualizadas"] += 1
        elif f["accion"] == "fallida":
            res["fallidas"] += 1
        else:
            res["omitidas"] += 1
            if str(f.get("motivo") or "").startswith("ya tiene fotos propias"):
                res["con_fotos_propias"] += 1
    if res["copiadas"] or res["actualizadas"] or res["fallidas"]:
        log.info("%s(%s): %d copiadas, %d actualizadas, %d omitidas (%d con fotos "
                 "propias), %d fallidas", quien, padre, res["copiadas"],
                 res["actualizadas"], res["omitidas"], res["con_fotos_propias"],
                 res["fallidas"])
    return res


async def copiar_fotos_padre(padre_id: int, lista_antes: list[int],
                             lista_padre: list[int] | None = None, *,
                             en_crear: Callable[[], EnCrear] | None = None,
                             ) -> dict[str, Any]:
    """
    Crear, tras el PUT del padre: guarda sus fotos en la `_kubera_galeria` de
    las hijas según `_decidir` (con `copiar_sin_fotos`).

    `lista_antes`: `fotos_del_padre` leída ANTES del PUT (reconoce las copias
    intactas de una corrida anterior). `lista_padre`: los ids que devolvió el
    PUT (`images[].id`, portada primero); None → se relee MySQL. `en_crear`:
    devuelve lo que está en cola/procesándose en Crear (ver `_decidir` 0b/0c).

    Quien llama tiene el candado del padre desde antes de leer `lista_antes`.

    Devuelve { padre, hijas, copiadas, actualizadas, omitidas,
               con_fotos_propias, fallidas, familia_por_variante,
               detalle: [...] } y nunca lanza.
    """
    if lista_padre is None:
        lista_padre = await leer_fotos_padre(padre_id)
        if lista_padre is None:
            return _resumen_vacio(padre_id,
                                  error="No se pudieron releer las fotos del padre.")
    if not _limpios(lista_padre):
        return _resumen_vacio(padre_id, aviso="el padre quedó sin fotos")
    return await _aplicar(int(padre_id), lista_antes or [], lista_padre,
                          copiar_sin_fotos=True, quien="copiar_fotos_padre",
                          en_crear=en_crear)


async def leer_fotos_padre(padre: int | None) -> list[int] | None:
    """`fotos_del_padre` desde una corrutina; None si la base no contesta (quien
    llama se salta la copia/sincronización en vez de fallar su escritura)."""
    if not padre:
        return None
    try:
        if not await asyncio.to_thread(wp_db.disponible):
            return None
        return await asyncio.to_thread(fotos_del_padre, int(padre))
    except Exception as exc:  # noqa: BLE001
        log.warning("leer_fotos_padre(%s): %s", padre, exc)
        return None


async def sincronizar_copias(padre_id: int, lista_antes: list[int] | None,
                             lista_despues: list[int] | None) -> dict[str, Any]:
    """
    Después de TODA escritura de la galería del PADRE (quitar, agregar, IA):
    cada hija cuya `_kubera_galeria` es la copia intacta de `lista_antes` pasa
    a la copia de `lista_despues`. Las que personalizaron no se tocan, y las que
    heredan tampoco (ya ven el cambio).

    POR QUÉ. Sin esto la copia de Crear se congela: quitar del padre una foto
    equivocada la dejaba publicándose en todas las hijas copiadas, porque la
    regla `propias` ya no mira al padre. No depende de GALERIA_VARIANTE: basta
    con que existan hijas con la meta; si no hay, son unos SELECT y nada más.

    `lista_antes`/`lista_despues` None = no se pudieron leer → no se toca nada.
    Quien llama tiene `candado(padre_id)` desde ANTES de leer `lista_antes` y
    hasta aquí: sin él, dos ediciones solapadas del padre (quitar la 11 y luego
    la 12 mientras la primera sincroniza) dejaban a una hija con [10,12,13]
    frente a un padre [10,13], y ya no casaba con ninguna lista futura
    (revisión del 17-sep-2026, probado con dobles).
    Nunca lanza. Devuelve el resumen de `copiar_fotos_padre` (+ `sin_cambios`).
    """
    if lista_antes is None or lista_despues is None or not padre_id:
        return _resumen_vacio(padre_id, sin_cambios=True,
                              aviso="no se pudo leer la galería del padre")
    if _limpios(lista_antes) == _limpios(lista_despues):
        return _resumen_vacio(padre_id, sin_cambios=True)
    return await _aplicar(int(padre_id), lista_antes, lista_despues,
                          copiar_sin_fotos=False, quien="sincronizar_copias")
