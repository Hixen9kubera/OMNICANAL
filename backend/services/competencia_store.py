"""
competencia_store.py — Fachada de persistencia del módulo de Competencia.

UN SOLO DESTINO: la BD kubera, esquema `enrich.market_*`, vía
`competencia_supabase` (psycopg2, requiere `SUPABASE_DB_URL`).

NO HAY MODO LOCAL. Antes esto era un store SQLite con un remoto encima, y esa
doble vida costó cara: en Railway el FS es efímero, así que el archivo no existía
y el tab arrancaba vacío; y cuando sí existía, una captura escrita ahí parecía
haber funcionado sin que nadie la leyera nunca. Ahora todas las funciones —
lecturas incluidas — exigen la credencial y revientan sin ella.

Nada de memoria tampoco: la captura escribe cada categoría en cuanto la tiene,
no al final. Un bloqueo a media corrida ya se llevó 49 categorías raspadas.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger("omnicanal.competencia.store")



def disponible() -> bool:
    """
    ¿Hay de dónde leer? Es lo que `/estado` publica como `supabase`.

    Devolvía `True` SIEMPRE, apoyada en que "en el peor caso está el archivo
    local". **Ese respaldo no existía**: `listar_skus` llamaba a
    `_listar_skus_local`, que no está definido en ningún archivo del repo, así
    que sin `SUPABASE_DB_URL` la caída habría sido un `NameError` —no un
    fallback— y `/estado`, que se anuncia como "diagnóstico honesto", habría
    seguido reportando `supabase: true` mientras nada funcionaba.

    Ahora contesta lo que de verdad hay.
    """
    return _remoto() is not None


def _remoto():
    """
    El lector de la BD kubera, o None si no hay `SUPABASE_DB_URL`.

    Se resuelve en cada llamada y no al importar: la variable puede aparecer
    después (Railway aplica cambios de entorno sin rebuild) y no queremos un
    proceso condenado a SQLite por el orden de arranque.
    """
    try:
        from services import competencia_supabase
        return competencia_supabase if competencia_supabase.disponible() else None
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo usar la lectura remota, sigo en local: %s", exc)
        return None


def periodo_actual(hoy: date | None = None) -> str:
    return (hoy or date.today()).replace(day=1).isoformat()

# ── SKUs vigilados ───────────────────────────────────────────────────────────

_CAMPOS_SKU = ("sku", "nombre", "origen_nombre", "categoria_id", "categoria_nombre",
               "raiz_id", "raiz_nombre",
               "ruta", "cat1", "cat2", "cat3", "cat4", "imagen", "ml_item_id",
               "cuenta", "publicado_ml", "termino_general", "termino_origen", "activo")

def listar_skus(solo_activos: bool = True) -> list[dict[str, Any]]:
    """
    Los SKUs vigilados. Sin fallback: llamaba a `_listar_skus_local`, que NO
    EXISTE, así que el "respaldo local" era un `NameError` con un mensaje que no
    decía nada. Se levanta el mismo error explícito que sus hermanas
    (`prioridad`, `ranking_categoria`), que dice qué falta y dónde vive el dato.
    """
    r = _remoto()
    if r:
        return r.listar_skus(solo_activos)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL: los SKUs vigilados viven en "
        "enrich.market_sku_config y no hay de dónde leerlos.")

def actualizar_termino(sku: str, termino: str) -> bool:
    """Corrección manual: marca origen='manual' para que la IA no lo vuelva a pisar.

    Va a `enrich.market_sku_config.termino_id` vía el catálogo de términos. Sin
    `SUPABASE_DB_URL` revienta, igual que el resto de las escrituras del módulo.
    """
    r = _remoto()
    if r:
        return r.actualizar_termino(sku, termino)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL: el término se guarda en enrich.market_sku_config "
        "y no hay a dónde. Define la variable antes de escribir.")


def proponer_termino(sku: str, termino: str) -> bool:
    """Término propuesto por la IA. NO pisa una corrección humana previa."""
    r = _remoto()
    if r:
        return r.proponer_termino(sku, termino)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL: el término se guarda en enrich.market_sku_config "
        "y no hay a dónde. Define la variable antes de escribir.")
# ── Resultados ───────────────────────────────────────────────────────────────

_CAMPOS_RES = ("sku", "tipo", "termino", "periodo", "posicion", "externo_id",
               "titulo", "descripcion", "precio", "moneda", "imagen", "url",
               "seller", "marca", "visitas_30d", "vendidos", "reviews", "rating",
               "envio_gratis", "es_full", "es_nuestro", "sku_nuestro")
def guardar_publicaciones(filas: list[dict[str, Any]]) -> int:
    """Upsert de NUESTRAS publicaciones → `enrich.market_listing_metrics`.

    Refrescos PARCIALES: el paso de precios solo trae precio y el de visitas solo
    visitas; el COALESCE del remoto evita que uno borre lo del otro.
    """
    r = _remoto()
    if r:
        return r.guardar_publicaciones(filas)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL: las publicaciones se guardan en "
        "enrich.market_listing_metrics y no hay a dónde.")

def publicaciones(sku: str | None = None) -> list[dict[str, Any]]:
    """
    Las publicaciones tal como están en la tabla, MÁS la conversión calculada.

    La conversión se agrega aquí y no solo en `tabla()` porque quien lea esta
    función directamente (el detalle de un SKU) también la necesita: sin ella el
    frontend imprimía "undefined%".
    """
    r = _remoto()
    if r:
        return r.publicaciones(sku)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL. Competencia vive 100%% en enrich.market_* "
        "de la BD kubera; este módulo ya no tiene modo local.")

_CAMPOS_RANK = ("categoria_id", "nivel", "periodo", "posicion", "externo_id",
                "titulo", "precio", "precio_lista", "descuento", "vendidos",
                "rating", "seller", "imagen", "url", "visitas_30d", "reviews",
                "id_pagina", "tipo", "item_categoria_id", "item_categoria_nombre",
                "es_nuestro", "sku_nuestro")


def reemplazar_ranking(categoria_id: str, nivel: str, periodo: str,
                       filas: list[dict[str, Any]]) -> int:
    """
    Borra el ranking anterior de esa (categoría, nivel) y escribe el nuevo.
    Sin histórico, igual que `resultados`: es la foto del mes.

    Va SIEMPRE a `enrich.market_bestsellers`. Si no hay `SUPABASE_DB_URL`
    revienta en vez de caer a SQLite: el destino es la BD kubera y una captura
    escrita en un disco que nadie lee es peor que una captura que no corrió —
    parece que sí funcionó.
    """
    r = _remoto()
    if r:
        return r.reemplazar_ranking(categoria_id, nivel, periodo, filas)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL: la captura escribe en enrich.market_bestsellers "
        "y no hay a dónde. Define la variable antes de capturar.")


def prioridad(categorias: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Las subcategorías activas ordenadas por dinero. Sin fallback: la vista vive
    en kubera y no hay gemela local.
    """
    r = _remoto()
    if r:
        return r.prioridad(categorias)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL: la vista de prioridad vive en "
        "enrich.market_categoria_prioridad_v y no hay de dónde leerla.")


def frescura(canal: str = "mercado_libre") -> dict[str, Any]:
    """
    Las tres fechas del encabezado. NO levanta si no hay remoto: es un dato
    decorativo y una fecha ausente no puede tumbar el tab entero.
    """
    r = _remoto()
    if r:
        return r.frescura(canal)
    return {}


def movimiento_del_top(canal: str = "mercado_libre") -> dict[str, dict[str, Any]]:
    """
    El sondeo de `/highlights` por categoría. NO levanta si no hay remoto: sin él
    la vista pierde dos avisos, no los datos.
    """
    r = _remoto()
    if r:
        try:
            return r.movimiento_del_top(canal)
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo leer el sondeo de /highlights: %s", exc)
    return {}


def ranking_categoria(categoria_id: str, nivel: str | None = None,
                      limite: int = 10) -> list[dict[str, Any]]:
    r = _remoto()
    if r:
        return r.ranking_categoria(categoria_id, nivel, limite)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL. Competencia vive 100%% en enrich.market_* "
        "de la BD kubera; este módulo ya no tiene modo local.")

def reemplazar_terminos(categoria_id: str, periodo: str,
                        terminos: list[dict[str, Any]]) -> int:
    """
    Foto del mes de los términos más buscados de una categoría. Sin histórico.

    Va SIEMPRE a `enrich.market_terms`; sin `SUPABASE_DB_URL` revienta, por la
    misma razón que :func:`reemplazar_ranking`.
    """
    r = _remoto()
    if r:
        return r.reemplazar_terminos(categoria_id, periodo, terminos)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL: la captura escribe en enrich.market_terms "
        "y no hay a dónde. Define la variable antes de capturar.")


def _cubre(termino: str, titulos: list[str]) -> bool:
    """
    ¿Alguno de nuestros títulos contiene TODAS las palabras del término?

    Por palabras y no por substring: ML busca por tokens, y "funda para moto"
    debe dar positivo con un título que diga "Funda Impermeable Para Moto" aunque
    la frase exacta no aparezca.
    """
    palabras = [p for p in termino.lower().split() if p]
    return any(all(p in t for p in palabras) for t in titulos)


def terminos_categoria(categoria_id: str,
                       titulos_por_tienda: dict[str, str] | None = None,
                       limite: int = 20) -> list[dict[str, Any]]:
    """
    Los términos más buscados de la categoría, marcando cuáles cubre cada tienda.

    La cobertura se calcula POR TIENDA porque el título lo es: MUE-0163-TEL se
    llama "Malla De Tela 6x4m Para Jardin…" en BEKURA y "Lona Sombra Reforzada
    4x6m Para Exterior…" en SANCORFASHION, así que cada una responde a búsquedas
    distintas. Colapsarlas en un solo ✓ esconde justo la decisión: qué título
    arreglar y en cuál tienda.

    `cubierto` queda como el OR de las tiendas (¿alguna nos hace encontrables?) y
    `cubierto_por` dice cuáles.
    """
    r = _remoto()
    if r:
        return r.terminos_categoria(categoria_id, titulos_por_tienda, limite)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL. Competencia vive 100%% en enrich.market_* "
        "de la BD kubera; este módulo ya no tiene modo local.")

def total_terminos(categoria_id: str) -> int:
    """Cuántos términos publica ML para la categoría (0 = no publica ninguno)."""
    r = _remoto()
    if r:
        return r.total_terminos(categoria_id)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL. Competencia vive 100%% en enrich.market_* "
        "de la BD kubera; este módulo ya no tiene modo local.")

def tabla() -> list[dict[str, Any]]:
    """
    Una fila por SKU con sus tres posiciones y el desglose POR TIENDA.

    La conversión se calcula aquí y no en la base: es unidades/visitas, y solo
    tiene sentido si HAY visitas. Con 0 visitas no es 0% — es indefinida, y la UI
    debe mostrar "—" en vez de un cero que se lee como "convierte mal".
    """
    skus = listar_skus()
    pubs: dict[str, list[dict[str, Any]]] = {}
    for p in publicaciones():
        pubs.setdefault(p["sku"], []).append(p)

    out = []
    for s in skus:
        fila = dict(s)
        mis = pubs.get(s["sku"], [])
        tiendas = []
        for p in mis:
            vis, uni = p.get("visitas_30d"), p.get("unidades_30d")
            tiendas.append({
                "cuenta": p["cuenta"],
                "canal": p.get("canal") or "mercado_libre",
                "ml_item_id": p["ml_item_id"],
                # El título es POR TIENDA y suele ser distinto: MUE-0163-TEL es
                # "Malla De Tela 6x4m…" en BEKURA y "Lona Sombra Reforzada 4x6m…"
                # en SANCORFASHION. Con títulos distintos, la cobertura de términos
                # de búsqueda también es distinta por tienda.
                "titulo": p.get("titulo"),
                "url": p.get("url"),
                "imagen": p.get("imagen"),
                "precio": p.get("precio"),
                "precio_lista": p.get("precio_lista"),
                # De cuándo es ese precio. `None` = nunca se confirmó contra ML,
                # así que lo que se muestra viene de `channel.listings.price`, que
                # NO es lo que se cobra (ver precio_al_abrir.py). La pantalla lo
                # dice en vez de presentar los dos casos igual.
                "precio_confirmado_en": p.get("precio_confirmado_en"),
                "estado": p.get("estado"),
                "visitas_30d": vis,
                "unidades_30d": uni,
                "conversion_30d": (round(uni / vis * 100, 2)
                                   if vis and uni is not None else None),
            })
        fila["tiendas"] = tiendas

        v = [t["visitas_30d"] for t in tiendas if t["visitas_30d"] is not None]
        u = [t["unidades_30d"] for t in tiendas if t["unidades_30d"] is not None]
        fila["visitas_30d"] = sum(v) if v else None
        fila["unidades_30d"] = sum(u) if u else None
        fila["conversion_30d"] = (round(sum(u) / sum(v) * 100, 2)
                                  if v and sum(v) and u else None)
        # La foto y el precio de referencia: los de BEKURA si existe, si no la primera.
        # La foto del catálogo (Woo) manda; la de la publicación de ML es respaldo.
        if not fila.get("imagen"):
            ref = next((t for t in tiendas if t["cuenta"] == "BEKURA"),
                       tiendas[0] if tiendas else None)
            fila["imagen"] = (ref or {}).get("imagen")
        fila["actualizado"] = max((p["actualizado_en"] for p in mis), default=None)

        out.append(fila)
    return out


_CAMPOS_BUSQ = ("termino", "periodo", "posicion", "externo_id", "titulo", "precio",
                "precio_lista", "descuento", "vendidos", "rating", "seller",
                "imagen", "url", "visitas_30d", "reviews", "envio_gratis",
                "es_full", "catalog_id", "es_nuestro", "sku_nuestro")


def reemplazar_busqueda(termino: str, periodo: str,
                        filas: list[dict[str, Any]]) -> int:
    """
    Resultados del buscador para UN término → `enrich.market_search_results`.

    El término se registra en el catálogo (`market_search_term`) y queda marcado
    como MEDIDO aunque la corrida venga vacía: se pagó igual, y sin la marca el
    siguiente barrido lo volvería a correr.
    """
    r = _remoto()
    if r:
        return r.reemplazar_busqueda(termino, periodo, filas)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL: la búsqueda se guarda en "
        "enrich.market_search_results y no hay a dónde.")


def busqueda(termino: str, limite: int = 5) -> list[dict[str, Any]]:
    """Los resultados guardados de un término. Vacío = no se ha medido."""
    r = _remoto()
    if r:
        return r.busqueda(termino, limite)
    raise RuntimeError(
        "No hay SUPABASE_DB_URL. Competencia vive 100%% en enrich.market_* "
        "de la BD kubera; este módulo ya no tiene modo local.")

def terminos_medidos() -> set[str]:
    """Qué términos ya tienen búsqueda. Es lo que evita volver a pagar por ellos."""
    r = _remoto()
    if r:
        return r.terminos_medidos()
    raise RuntimeError(
        "No hay SUPABASE_DB_URL. Competencia vive 100%% en enrich.market_* "
        "de la BD kubera; este módulo ya no tiene modo local.")

def rankings_por_categoria() -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Todo el ranking de una vez, agrupado por (categoria_id, nivel)."""
    r = _remoto()
    if r:
        return r.rankings_por_categoria()
    raise RuntimeError(
        "No hay SUPABASE_DB_URL. Competencia vive 100%% en enrich.market_* "
        "de la BD kubera; este módulo ya no tiene modo local.")

def conteo_terminos() -> dict[str, int]:
    """Cuántos términos hay por categoría, en una sola consulta."""
    r = _remoto()
    if r:
        return r.conteo_terminos()
    raise RuntimeError(
        "No hay SUPABASE_DB_URL. Competencia vive 100%% en enrich.market_* "
        "de la BD kubera; este módulo ya no tiene modo local.")

def vista(canal: str = "mercado_libre") -> list[dict[str, Any]]:
    """
    El árbol que pinta el tab: raíz → subcategorías → nuestros SKUs.

    Un nivel por cada cosa que se pidió ver:
      raíz         top de más vendidos de la categoría padre
      subcategoría conteo de NUESTROS SKUs (el filtro) + su top y sus términos
      SKU          la brecha contra el mercado, visible sin filtrar nada

    La BRECHA es la columna que manda. El precio solo no dice nada; $1,053 contra
    una mediana de $199 sí. Se calcula contra la MEDIANA del ranking de la
    subcategoría y no contra el promedio: un solo producto de $1,189 en el top 20
    de Tapetes movería el promedio y taparía el problema.
    """
    filas = [f for f in tabla()
             if any((t.get("canal") or "mercado_libre") == canal for t in f["tiendas"])
             or not f["tiendas"]]

    raices: dict[str, dict[str, Any]] = {}
    for f in filas:
        rid = f.get("raiz_id") or "sin_categoria"
        r = raices.setdefault(rid, {
            "raiz_id": f.get("raiz_id"),
            "raiz_nombre": f.get("raiz_nombre") or "Sin categoría",
            "subcategorias": {},
            "skus": [],
        })
        cid = f.get("categoria_id") or "sin_categoria"
        sub = r["subcategorias"].setdefault(cid, {
            "categoria_id": f.get("categoria_id"),
            "categoria_nombre": f.get("categoria_nombre") or "Sin categoría",
            "n_skus": 0,
        })
        sub["n_skus"] += 1
        r["skus"].append(f)

    # Precargados: con ~900 subcategorías, pedir el ranking y el conteo de términos
    # una por una eran ~1,800 viajes a la base por carga de página.
    rk = rankings_por_categoria()
    nterm = conteo_terminos()

    # El sondeo GRATIS de /highlights, una consulta para todo el árbol. De aquí
    # salen `top_movido` (¿ML se movió desde que pagamos por raspar?) y
    # `ml_publica` (¿hay ranking ahí, o raspar sería tirar dinero?).
    hl = movimiento_del_top(canal)

    def _avisos(destino: dict[str, Any], cid: str | None,
                top: list[dict[str, Any]]) -> None:
        """
        Cuelga los avisos del sondeo en una categoría: si ML publica ranking ahí,
        cuándo se movió, y **CUÁNTO** se movió.

        ── POR QUÉ EL "CUÁNTO" ─────────────────────────────────────────────────
        "ML ya se movió" no distingue un empujón de un vuelco. Que el #7 se
        intercambie con el #8 y que cambie el #1 son la misma pastilla, y llevan
        a decisiones opuestas: lo primero no vale una recaptura, lo segundo sí.

        Medido el 1-sep-2026: de las 470 categorías capturadas ESE MISMO DÍA, 198
        ya salían como movidas — el 42%. Con ese volumen, un aviso que no gradúa
        se vuelve ruido y se deja de mirar.

        Se compara POSICIÓN POR POSICIÓN contra lo que el sondeo diario ya tiene
        guardado (`market_highlights.entradas`): ni una llamada más, ni un peso.
        El join es por `id_pagina`, que es el id que devuelve /highlights — el
        `externo_id` del raspador es la publicación, otro espacio de ids.
        """
        h = hl.get(cid) if cid else None
        # None = todavía no se ha sondeado. NO es lo mismo que False.
        destino["ml_publica"] = ((h.get("n") or 0) > 0) if h else None
        cambio = h.get("cambio_en") if h else None
        destino["top_cambio_en"] = cambio
        nuestra = max((x["capturado_en"] for x in top if x.get("capturado_en")),
                      default=None)
        try:
            destino["top_movido"] = bool(cambio and nuestra and cambio > nuestra)
        except TypeError:
            # Fechas de tipos distintos: mejor no afirmar nada que afirmar mal.
            destino["top_movido"] = None

        # ── Cuánto se movió ────────────────────────────────────────────────
        destino["top_movidas"] = None
        destino["top_comparadas"] = None
        destino["top_primero_cambio"] = None
        # LA VENTANA ES EL TOP 10, la misma de `TOPE_HUELLA`. Sin acotarla se
        # comparaban las 20 posiciones y el aviso decía "19 de 19 movidas" —
        # cierto, pero midiendo un tramo donde el orden es ruido y que no es el
        # que dispara la pastilla.
        VENTANA = 10
        vivo = {e.get("p"): e.get("id") for e in ((h or {}).get("entradas") or [])
                if e.get("p") and e.get("id") and e["p"] <= VENTANA}
        nuestro = {x.get("posicion"): x.get("id_pagina") for x in top
                   if x.get("posicion") and x.get("id_pagina")
                   and x["posicion"] <= VENTANA}
        # Sólo las posiciones que están en AMBOS: una que sólo tenemos nosotros
        # no se movió, es que ML dejó de publicarla, y contarla como movimiento
        # inflaría el aviso justo donde menos se sabe.
        comunes = sorted(set(vivo) & set(nuestro))
        if comunes:
            destino["top_comparadas"] = len(comunes)
            destino["top_movidas"] = sum(1 for p in comunes if vivo[p] != nuestro[p])
            if 1 in comunes:
                destino["top_primero_cambio"] = vivo[1] != nuestro[1]

    out = []
    for r in raices.values():
        # Ranking y términos de la raíz.
        r["top"] = rk.get((r["raiz_id"], "raiz"), [])[:10] if r["raiz_id"] else []
        r["terminos_raiz"] = nterm.get(r["raiz_id"], 0) if r["raiz_id"] else 0
        _avisos(r, r["raiz_id"], r["top"])

        for cid, sub in r["subcategorias"].items():
            top = rk.get((cid, "hoja"), [])[:20] if sub["categoria_id"] else []
            precios = sorted(x["precio"] for x in top if x.get("precio"))
            sub["top"] = top
            sub["n_ranking"] = len(top)
            sub["mediana"] = (precios[len(precios) // 2] if len(precios) % 2
                              else (precios[len(precios) // 2 - 1] +
                                    precios[len(precios) // 2]) / 2) if precios else None
            sub["precio_min"] = precios[0] if precios else None
            sub["precio_max"] = precios[-1] if precios else None
            sub["n_terminos"] = nterm.get(cid, 0) if sub["categoria_id"] else 0
            # SIN CAPTURAR ≠ ML NO PUBLICA. Son cosas distintas y llevan a
            # acciones opuestas: una es "córrele la captura", la otra es "no
            # insistas, ahí no hay nada". Esta vista NO puede distinguirlas —
            # solo sabe lo que tiene guardado— así que dice lo único que le
            # consta: que no lo hemos capturado.
            #
            # Medido el 4-ago: 174 de 176 subcategorías salían como "ML no
            # publica" y TODAS las revisadas sí tenían ranking y términos en ML.
            # El mensaje viejo mandaba a no reintentar justo donde sí había datos.
            sub["sin_capturar"] = not top and not sub["n_terminos"]
            _avisos(sub, sub["categoria_id"], top)
            # AHORA SÍ se pueden distinguir. El comentario de arriba pedía esto:
            # el sondeo de /highlights dice si ML publica ranking ahí, y con eso
            # "no lo hemos capturado" deja de confundirse con "ahí no hay nada".
            # `ml_publica is False` es afirmativo: ML contestó y vino vacío.
            sub["sin_datos_ml"] = sub["ml_publica"] is False
            # Volumen del NICHO: lo que se vende al mes en toda la subcategoría,
            # sumando el top. `vendidos` es cota inferior (ML redondea a "+50mil"),
            # así que sirve para ordenar nichos, no como cifra exacta.
            sub["volumen_mercado"] = sum(x["vendidos"] or 0 for x in top) or None
            # Tráfico del NICHO: la suma de visitas de su top. Junto con la mediana
            # es lo que permite buscar categorías de ticket alto y mucha demanda,
            # que es distinto de "dónde tenemos más SKUs".
            sub["visitas_mercado"] = sum(x["visitas_30d"] or 0 for x in top) or None
            # POSICIÓN DE LA SUBCATEGORÍA EN EL TOP DEL PADRE. Es el criterio más
            # fuerte de oportunidad: si el #1 de toda la categoría vive en nuestra
            # subcategoría, ahí está la pelea que importa. Se une por la categoría
            # que trae cada fila del ranking raíz, no por id de publicación: el
            # padre y la hoja rankean publicaciones distintas del mismo nicho.
            sub["pos_en_raiz"] = min(
                (x["posicion"] for x in r["top"] if x.get("item_categoria_id") == cid),
                default=None)

        for f in r["skus"]:
            sub = r["subcategorias"].get(f.get("categoria_id") or "sin_categoria", {})
            med = sub.get("mediana")
            f["pos_en_raiz"] = sub.get("pos_en_raiz")
            f["volumen_mercado"] = sub.get("volumen_mercado")
            # ¿ESTAMOS en el top de nuestra subcategoría? Es el logro que la vista
            # debe celebrar con una corona: significa que una de nuestras
            # publicaciones aparece entre los más vendidos del nicho. Se busca por
            # `sku_nuestro`, que `_marcar` llena cruzando el ranking contra nuestras
            # publicaciones — no por título, que difiere por tienda.
            mias = [x["posicion"] for x in (sub.get("top") or [])
                    if x.get("sku_nuestro") == f["sku"]]
            f["posicion_top"] = min(mias) if mias else None
            f["en_top"] = bool(mias)
            # El precio de referencia: el más bajo que realmente cobramos.
            precios = [t["precio"] for t in f["tiendas"] if t.get("precio")]
            f["precio_ref"] = min(precios) if precios else None
            f["mediana_mercado"] = med
            f["brecha"] = (round(f["precio_ref"] / med, 2)
                           if med and f["precio_ref"] else None)
            f["sin_datos_ml"] = sub.get("sin_datos_ml", False)
            f["sin_capturar"] = sub.get("sin_capturar", False)
            f["n_terminos"] = sub.get("n_terminos", 0)
            # La trampa que ya apareció dos veces: la publicación PAUSADA es la que
            # tiene el tráfico y las ventas. Se marca aquí para que la vista lo grite.
            act = [t for t in f["tiendas"] if t.get("estado") == "active"]
            pau = [t for t in f["tiendas"] if t.get("estado") == "paused"]
            f["pausada_es_la_que_vende"] = bool(
                pau and max((t["visitas_30d"] or 0) for t in pau) >
                max([(t["visitas_30d"] or 0) for t in act] or [0]))

        # ── Nuestros 5 con más oportunidad ──────────────────────────────────
        # Manda la posición de su subcategoría en el top del padre (#1, luego #2…):
        # competir donde vive el más vendido de toda la categoría es la mejor
        # apuesta. Los que no aparecen ahí se ordenan por el volumen del nicho, que
        # es el otro camino a "puede competir por volumen de venta", y al final por
        # lo que ya vendemos.
        r["oportunidad"] = sorted(
            r["skus"],
            key=lambda f: (f.get("pos_en_raiz") or 99,
                           -(f.get("volumen_mercado") or 0),
                           -(f.get("unidades_30d") or 0)))[:5]

        # Cada subcategoría carga SUS SKUs y su path completo: es lo que se abre al
        # desplegar la fila (primero el top del nicho, luego los nuestros).
        for cid, sub in r["subcategorias"].items():
            mios = [f for f in r["skus"] if (f.get("categoria_id") or "sin_categoria") == cid]
            sub["skus"] = mios
            sub["ruta"] = next((f.get("ruta") for f in mios if f.get("ruta")), None)
            # El padre inmediato con su id: es lo que permite auditar de qué
            # cuelga la subcategoría sin salir del panel. Todos los SKUs de una
            # subcategoría comparten padre, así que basta el primero que lo traiga.
            sub["padre_id"] = next((f.get("padre_id") for f in mios if f.get("padre_id")), None)
            sub["padre_nombre"] = next(
                (f.get("padre_nombre") for f in mios if f.get("padre_nombre")), None)

        r["subcategorias"] = sorted(
            r["subcategorias"].values(),
            key=lambda s: (s.get("pos_en_raiz") or 99,
                           -(s.get("volumen_mercado") or 0),
                           s["categoria_nombre"]))
        r["skus"].sort(key=lambda f: (f.get("categoria_nombre") or "", f["sku"]))
        r["n_skus"] = len(r["skus"])
        r["n_publicaciones"] = sum(len(f["tiendas"]) for f in r["skus"])
        out.append(r)
    out.sort(key=lambda r: (-r["n_skus"], r["raiz_nombre"]))
    return out


def _ahora() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
