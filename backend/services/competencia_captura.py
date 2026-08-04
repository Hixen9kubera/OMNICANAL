"""
competencia_captura.py — La corrida mensual: tres mediciones por SKU.

Las tres preguntas del módulo, cada una con su fuente:

  1. GENERAL   — "¿dónde estoy en la búsqueda genérica?" ("lona para exterior")
                 Compites por DESCUBRIMIENTO contra todo el mundo.
                 Fuente: scraper (la posición orgánica no existe por API).
  2. TÍTULO    — "¿dónde estoy contra mi competencia directa?"
                 Fuente: scraper, con el título completo del producto.
  3. CATEGORÍA — "¿quiénes son los mejores de mi categoría?"
                 Fuente: API `/highlights`, que da el ranking OFICIAL con posición.

Y sobre todas ellas se pegan las VISITAS de la API (`/visits/items`), que sí
funcionan para publicaciones ajenas y son el dato más confiable del módulo.

Sin histórico: cada corrida borra la medición anterior del SKU y la reescribe
(ver `competencia_store.reemplazar_resultados`).

AHORRO REAL: varios SKUs comparten término general (los 3 de Tapetes dan todos
"tapetes para auto"). Las búsquedas se deduplican y el resultado se reusa entre
los SKUs que lo comparten — solo cambia a quién se le marca `es_nuestro`. Con 7
SKUs eso baja de 14 búsquedas a 12, y en catálogos grandes la diferencia crece.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings
from services import (
    competencia_mas_vendidos, competencia_ml, competencia_scraper, competencia_store,
    competencia_terminos, db, supabase_db,
)

log = logging.getLogger("omnicanal.competencia.captura")

# Cuántas publicaciones de cada medición se enriquecen con visitas. Cada visita
# es UNA llamada a la API (ML no acepta multiget), así que este número es el
# costo en tiempo de la corrida.
TOPE_VISITAS = 25

# Caché del barrido de pedidos de la API de ML, por proceso. Es account-wide: la
# misma foto sirve para cualquier item de la corrida. None = todavía no se corrió.
_barrido_unidades: dict[str, int] | None = None


def limpiar_cache_unidades() -> None:
    """Fuerza un barrido nuevo. Para corridas largas que quieran refrescar."""
    global _barrido_unidades
    _barrido_unidades = None


# ── Nuestras publicaciones, para saber "dónde estoy" ────────────────────────

def _nuestras_publicaciones() -> dict[str, dict[str, str]]:
    """{ ml_item_id: {sku, cuenta} } de TODO lo publicado en ML (ambas cuentas)."""
    try:
        filas = db.fetch_all(
            "SELECT ml_item_id, sku, cuenta FROM ml_progress "
            "WHERE ml_item_id IS NOT NULL AND ml_item_id <> ''")
        return {f["ml_item_id"]: {"sku": f["sku"], "cuenta": f.get("cuenta") or ""}
                for f in filas}
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudieron leer nuestras publicaciones: %s", exc)
        return {}


def _marcar(filas: list[dict[str, Any]], nuestras: dict[str, dict[str, str]]) -> None:
    for f in filas:
        mio = nuestras.get(f.get("externo_id") or "")
        if mio:
            f["es_nuestro"] = True
            f["sku_nuestro"] = mio["sku"]


async def _visitas(filas: list[dict[str, Any]], tope: int = TOPE_VISITAS) -> int:
    """Visitas de 30 días, de a una llamada (ML no acepta multiget)."""
    objetivo = [f for f in filas[:tope]
                if (f.get("externo_id") or "").startswith("MLM")
                and not (f.get("externo_id") or "").startswith("MLMU")]
    if not objetivo:
        return 0
    res = await asyncio.gather(
        *(asyncio.to_thread(competencia_ml.visitas_30d, f["externo_id"]) for f in objetivo),
        return_exceptions=True)
    ok = 0
    for fila, v in zip(objetivo, res):
        if isinstance(v, int):
            fila["visitas_30d"] = v
            ok += 1
    return ok


# Las cuentas de ML y cómo se llaman en nuestras tablas.
CUENTAS = (("bekura", "BEKURA"), ("sancorfashion", "SANCORFASHION"))


def _unidades_por_item(item_ids: list[str]) -> tuple[dict[str, int], str]:
    """
    { item_id: unidades vendidas en 30 días }, y de qué fuente salió.

    PRIMERO SUPABASE, porque es lo que habrá en producción: `channel.order_items`
    tiene la cantidad y `channel.orders` la fecha. Es una sola consulta y no crece
    con cuántos SKUs se vigilen.

    La API de ML queda como RESPALDO para cuando falte `SUPABASE_DB_URL` (hoy, en
    local) — barre los pedidos por cuenta, ~140 páginas cada una.

    DESFASE MEDIDO, hay que saberlo: para MUE-0163-TEL/SANCORFASHION el espejo de
    Supabase da 514 unidades y la API de ML 566. El espejo va atrás. Por eso la
    respuesta incluye la fuente: un número sin saber de dónde viene no es un dato.

    Y no se usa `items.sold_quantity`: ese es el acumulado HISTÓRICO (1,159 en ese
    mismo SKU), no el del periodo.
    """
    if not item_ids:
        return {}, "vacio"

    if supabase_db.disponible():
        marcas = ",".join(["%s"] * len(item_ids))
        try:
            filas = supabase_db.fetch_all(f"""
                SELECT oi.item_id, SUM(oi.cantidad)::int AS unidades
                  FROM channel.order_items oi
                  JOIN channel.orders o
                    ON o.external_order_id = oi.external_order_id
                   AND o.canal = oi.canal
                 WHERE o.creado_at >= now() - interval '30 days'
                   AND oi.item_id IN ({marcas})
                 GROUP BY 1
            """, tuple(item_ids))
            return {f["item_id"]: f["unidades"] for f in filas}, "supabase"
        except Exception as exc:  # noqa: BLE001
            log.warning("Unidades desde Supabase fallaron, caigo a la API: %s", exc)

    # El barrido cubre la CUENTA COMPLETA, no los item_ids que se piden, así que
    # su resultado sirve para todas las tandas de la misma corrida. Sin esta caché
    # se repetía por tanda: ~280 páginas de la API cada vez, y medir 1,581 SKUs por
    # tandas de 120 se iba en 14 barridos idénticos.
    global _barrido_unidades
    if _barrido_unidades is None:
        barrido: dict[str, int] = {}
        for tok, _ in CUENTAS:
            try:
                barrido.update(competencia_ml.unidades_vendidas_30d(tok))
            except Exception as exc:  # noqa: BLE001
                log.warning("Unidades de %s fallaron: %s", tok, exc)
        _barrido_unidades = barrido
        log.info("Barrido de pedidos: %s items con venta en 30 días", len(barrido))
    barrido = _barrido_unidades
    if not barrido:
        return {}, "ninguna"
    # El barrido cubrió las cuentas completas, así que un item ausente vendió 0.
    return {i: barrido.get(i, 0) for i in item_ids}, "ml_api"


async def refrescar_visitas_propias(skus: list[str] | None = None) -> dict[str, Any]:
    """
    Visitas de 30 días de NUESTRAS publicaciones, por SKU y por cuenta.

    Es GRATIS (API de ML, no scraper), así que se refresca cuando se quiera, sin
    esperar la corrida mensual.

    QUÉ SE CONSULTA Y POR QUÉ ASÍ
    -----------------------------
    Las publicaciones se piden a **ML**, no a `ml_progress`: nuestra tabla no
    conoce las publicaciones creadas fuera del pipeline del panel. Caso real —
    MUE-0163-TEL está activo en las dos tiendas y `ml_progress` no tiene ninguna
    de las dos, así que el panel lo mostraba como "sin publicar". `ml_progress` se
    sigue leyendo, pero solo para detectar y reportar el desfase.

    Y las visitas van de a UNA llamada por publicación, porque no hay atajo:
      • `/visits/items?ids=A,B` → 400 "maximum amount of items to query is 1",
        incluso con items propios.
      • `/users/{uid}/items_visits/time_window` responde 200 pero da el TOTAL de la
        cuenta (222,547 en BEKURA) e IGNORA `ids`: no desglosa por item.
    """
    vigilados = [s["sku"] for s in competencia_store.listar_skus()]
    objetivo = [s for s in vigilados if not skus or s in set(skus)]
    if not objetivo:
        return {"ok": False, "motivo": "No hay SKUs vigilados que refrescar."}

    # 1. Lo que cree nuestra tabla (solo para comparar).
    marcas = ",".join(["%s"] * len(objetivo))
    try:
        en_bd = {(r["sku"], r["cuenta"]): r["ml_item_id"] for r in db.fetch_all(
            f"SELECT sku, cuenta, ml_item_id FROM ml_progress "
            f"WHERE sku IN ({marcas}) AND ml_item_id IS NOT NULL AND ml_item_id <> ''",
            tuple(objetivo))}
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer ml_progress: %s", exc)
        en_bd = {}

    # 2. Lo que dice ML, que es la autoridad. Una búsqueda por (sku, cuenta).
    pares = [(sku, tok, etiqueta) for sku in objetivo for tok, etiqueta in CUENTAS]
    hallados = await asyncio.gather(
        *(asyncio.to_thread(competencia_ml.items_por_sku, sku, tok)
          for sku, tok, _ in pares),
        return_exceptions=True)

    pubs, solo_en_ml, solo_en_bd = [], [], []
    for (sku, _tok, etiqueta), ids in zip(pares, hallados):
        ids = ids if isinstance(ids, list) else []
        for iid in ids:
            # `canal` explícito desde el inicio: cuando entre Amazon un ASIN es
            # otra fila con canal='amazon' y la vista no cambia.
            pubs.append({"sku": sku, "cuenta": etiqueta, "ml_item_id": iid,
                         "canal": "mercado_libre"})
            if en_bd.get((sku, etiqueta)) != iid:
                solo_en_ml.append(f"{sku}/{etiqueta}={iid}")
        if not ids and (sku, etiqueta) in en_bd:
            solo_en_bd.append(f"{sku}/{etiqueta}={en_bd[(sku, etiqueta)]}")

    if not pubs:
        return {"ok": True, "publicaciones": 0, "con_visitas": 0,
                "aviso": "Ninguno de esos SKUs tiene publicación activa en ML."}

    # 3. Ficha (foto, precio, URL) y visitas: dos llamadas por publicación, con
    #    el token de SU cuenta — el de BEKURA no puede leer las de SANCORFASHION.
    tok_de = {etiqueta: tok for tok, etiqueta in CUENTAS}
    fichas, visitas = await asyncio.gather(
        asyncio.gather(*(asyncio.to_thread(competencia_ml.detalle_item,
                                           p["ml_item_id"], tok_de[p["cuenta"]])
                         for p in pubs), return_exceptions=True),
        asyncio.gather(*(asyncio.to_thread(competencia_ml.visitas_30d,
                                           p["ml_item_id"], tok_de[p["cuenta"]])
                         for p in pubs), return_exceptions=True),
    )

    # 4. Unidades vendidas de 30 días. Supabase primero (es la fuente de
    #    producción); la API de ML como respaldo mientras falte SUPABASE_DB_URL.
    unidades, fuente_unidades = _unidades_por_item([p["ml_item_id"] for p in pubs])

    ok = 0
    for p, ficha, v in zip(pubs, fichas, visitas):
        if isinstance(ficha, dict):
            p.update({k: ficha.get(k) for k in
                      ("titulo", "precio", "url", "imagen", "estado")})
        p["visitas_30d"] = v if isinstance(v, int) else None
        # 0 solo si el barrido SÍ corrió: sin barrido es "no sé", no "no vendió".
        # Un 0 falso se lee como "convierte mal", que es peor que un hueco.
        # 0 solo si la fuente SÍ respondió: sin fuente es "no sé", no "no vendió".
        # Un 0 falso se lee como "convierte mal", que engaña más que un hueco.
        p["unidades_30d"] = unidades.get(p["ml_item_id"], 0) if unidades else None
        if p["visitas_30d"] is not None:
            ok += 1

    guardadas = competencia_store.guardar_publicaciones(pubs)
    # Refresca `publicado_ml` y el item de referencia con lo que ML acaba de decir.
    competencia_store.marcar_publicadas(pubs)

    log.info("Visitas propias: %s publicaciones, %s con dato, %s fuera de ml_progress",
             guardadas, ok, len(solo_en_ml))
    return {"ok": True, "skus": len(objetivo), "publicaciones": guardadas,
            "con_visitas": ok, "fuente_unidades": fuente_unidades,
            "sin_dato": [f"{p['sku']}/{p['cuenta']}" for p in pubs
                         if p["visitas_30d"] is None],
            # El desfase con nuestra tabla es un hallazgo, no ruido: son
            # publicaciones vivas fuera del radar del panel.
            "fuera_de_ml_progress": solo_en_ml,
            "en_ml_progress_pero_no_en_ml": solo_en_bd}


# ── Alta de los SKUs vigilados ──────────────────────────────────────────────

LARGO_TITULO_ML = 60


def sugerir_titulo(sku: str, limite_terminos: int = 25) -> dict[str, Any]:
    """
    UN título de máximo 60 caracteres, construido a partir de la COMPETENCIA
    DIRECTA: los títulos que hoy ganan el ranking de la subcategoría.

    El insumo son dos cosas medidas, no intuiciones: los títulos de los líderes
    (los que ya se venden en ese nicho) y los términos que ML publica en /trends.
    La IA solo redacta; no decide qué tiene demanda.

    Todo lo que la IA declara se RECALCULA aquí antes de devolverlo: el largo real
    y qué términos cubre de verdad. En la prueba con Fundas la IA presumía cubrir
    22 términos y el título solo cubría 2 — sin esta verificación la sugerencia
    sería una promesa.
    """
    fila = next((s for s in competencia_store.listar_skus(False) if s["sku"] == sku), None)
    if not fila:
        return {"ok": False, "motivo": f"{sku} no está entre los SKUs vigilados."}
    cat = fila.get("categoria_id")
    if not cat:
        return {"ok": False, "motivo": f"{sku} no tiene categoría de ML."}

    pubs = competencia_store.publicaciones(sku)
    titulos = {p["cuenta"]: p["titulo"] for p in pubs if p.get("titulo")}
    if not titulos:
        titulos = {"catálogo": fila.get("nombre") or ""}

    top = competencia_store.ranking_categoria(cat, "hoja", limite=20)
    lideres = [(t.get("posicion"), t.get("titulo")) for t in top if t.get("titulo")][:10]
    if not lideres:
        return {"ok": False, "motivo": "No hay ranking guardado de esta subcategoría: "
                                       "sin competencia directa no hay de dónde partir."}

    terminos = competencia_store.terminos_categoria(cat, titulos, limite_terminos)
    faltantes = [t["termino"] for t in terminos if not t.get("cubierto")]

    from services import ia_generadores

    system = (
        "Eres experto en posicionamiento (SEO) de Mercado Libre México. Te doy los "
        "títulos de las publicaciones que hoy LIDERAN el ranking de una "
        "subcategoría, el título actual de nuestro producto y los términos más "
        "buscados que nuestro título no cubre.\n"
        f"Propón UN SOLO título para nuestro producto que:\n"
        f"  1. NO pase de {LARGO_TITULO_ML} caracteres — es el límite de ML y es "
        "estricto, cuenta los caracteres,\n"
        "  2. describa el MISMO producto: no cambies medidas, piezas, color ni "
        "material del título actual,\n"
        "  3. use la estructura y el vocabulario de los títulos líderes, que son "
        "los que ya funcionan en ese nicho,\n"
        "  4. meta los términos faltantes de más arriba de la lista, que son los de "
        "mayor volumen,\n"
        "  5. se lea natural, no como una lista de palabras.\n"
        'Responde SOLO JSON: {"titulo":"...","porque":"una frase",'
        '"cubre":["..."]}'
    )
    usuario = (
        f"CATEGORÍA: {fila.get('categoria_nombre') or cat}\n"
        f"NUESTRO TÍTULO ACTUAL:\n"
        + "\n".join(f"- {c}: {t}" for c, t in titulos.items())
        + "\n\nTÍTULOS DE LA COMPETENCIA DIRECTA (los líderes del ranking):\n"
        + "\n".join(f"#{p}: {t}" for p, t in lideres)
        + ("\n\nTÉRMINOS MÁS BUSCADOS QUE NO CUBRIMOS (en orden de volumen):\n"
           + "\n".join(f"{i}. {t}" for i, t in enumerate(faltantes, 1))
           if faltantes else "\n\nNuestro título ya cubre los términos publicados.")
    )

    res = ia_generadores._completar(system, usuario, max_tokens=500)
    if not res.get("ok"):
        return {"ok": False, "motivo": f"La IA no respondió: {res.get('error')}"}
    txt = (res.get("texto") or "").strip()
    try:
        import json as _json
        datos = _json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"La IA no devolvió JSON válido: {exc}",
                "crudo": txt[:400]}

    titulo = (datos.get("titulo") or "").strip()
    if not titulo:
        return {"ok": False, "motivo": "La IA no devolvió título."}

    # Se recalcula todo: el largo y la cobertura REAL. La IA se equivoca en los dos.
    cubre = [x for x in faltantes
             if competencia_store._cubre(x, [titulo.lower()])]
    actuales = {c: len(t or "") for c, t in titulos.items()}
    return {
        "ok": True,
        "sku": sku,
        "categoria_nombre": fila.get("categoria_nombre"),
        "titulo": titulo,
        "largo": len(titulo),
        "excede_60": len(titulo) > LARGO_TITULO_ML,
        "porque": datos.get("porque"),
        "cubre_verificado": cubre,
        "cubre_declarado": [x for x in (datos.get("cubre") or []) if x],
        "faltantes": faltantes,
        "titulos_actuales": titulos,
        "largos_actuales": actuales,
        "lideres": [t for _, t in lideres],
    }


def skus_de_categoria(categoria_id: str) -> dict[str, Any]:
    """
    TODOS nuestros SKUs de una categoría, con su barra completa por tienda.

    Dos orígenes, y hay que distinguirlos porque no valen lo mismo:
      • Los VIGILADOS traen la barra medida (título por tienda, precio, visitas,
        ventas, conversión) desde `publicaciones`.
      • Los del CATÁLOGO que nadie mide traen lo que sí se sabe sin medirlos:
        si están publicados, en qué cuenta, su MLM y su link, desde `ml_progress`.
        Sus visitas y ventas van en None — no en 0. Un cero diría "no lo ven"
        cuando la verdad es "no lo hemos medido", y son cosas distintas.

    El título de los no vigilados sale de WooCommerce, que es la fuente del
    catálogo; el de ML puede diferir y por eso se marca el origen.
    """
    try:
        cat = db.fetch_all(
            "SELECT sku FROM categorias_ml WHERE category_id = %s ORDER BY sku",
            (categoria_id,))
    except Exception as exc:  # noqa: BLE001
        log.warning("skus_de_categoria(%s): %s", categoria_id, exc)
        return {"categoria_id": categoria_id, "skus": [], "error": str(exc)}
    todos = [r["sku"] for r in cat]
    if not todos:
        return {"categoria_id": categoria_id, "skus": []}

    vigilados = {s["sku"]: s for s in competencia_store.listar_skus(False)
                 if s.get("categoria_id") == categoria_id}
    medidas: dict[str, list[dict[str, Any]]] = {}
    for p in competencia_store.publicaciones():
        if p["sku"] in vigilados:
            medidas.setdefault(p["sku"], []).append(p)

    # Publicaciones de los que NO están vigilados, desde la bitácora del publicador.
    faltan = [s for s in todos if s not in medidas]
    progreso: dict[str, list[dict[str, Any]]] = {}
    if faltan:
        try:
            marcas = ",".join(["%s"] * len(faltan))
            for r in db.fetch_all(
                f"SELECT sku, cuenta, ml_item_id, ml_url FROM ml_progress "
                f"WHERE sku IN ({marcas}) AND success = 1 AND ml_item_id IS NOT NULL "
                f"ORDER BY sku, cuenta", tuple(faltan)):
                progreso.setdefault(r["sku"], []).append(r)
        except Exception as exc:  # noqa: BLE001
            log.warning("skus_de_categoria: no se pudo leer ml_progress: %s", exc)

    nombres = _nombres_desde_woo(faltan) if faltan else {}
    fotos = _fotos_desde_woo(faltan) if faltan else {}

    salida = []
    for sku in todos:
        if sku in medidas:
            v = vigilados[sku]
            salida.append({
                "sku": sku,
                "nombre": v.get("nombre"),
                "imagen": v.get("imagen"),
                "vigilado": True,
                "publicado": any(t.get("ml_item_id") for t in medidas[sku]),
                "tiendas": medidas[sku],
            })
            continue
        pubs = progreso.get(sku) or []
        salida.append({
            "sku": sku,
            "nombre": nombres.get(sku),
            "imagen": fotos.get(sku),
            "vigilado": False,
            "publicado": bool(pubs),
            "tiendas": [{
                "cuenta": p["cuenta"],
                "canal": "mercado_libre",
                "ml_item_id": p["ml_item_id"],
                "url": p.get("ml_url"),
                "titulo": nombres.get(sku),
                "titulo_origen": "woocommerce",
                "imagen": None,
                "precio": None,
                "estado": None,
                # None, NO 0: no medido ≠ sin visitas.
                "visitas_30d": None,
                "unidades_30d": None,
                "conversion_30d": None,
            } for p in pubs],
        })
    return {
        "categoria_id": categoria_id,
        "skus": salida,
        "n_total": len(salida),
        "n_vigilados": sum(1 for s in salida if s["vigilado"]),
        "n_publicados": sum(1 for s in salida if s["publicado"]),
        "aviso": ("Los SKUs sin vigilancia muestran su publicación pero no sus "
                  "visitas ni ventas: esos datos solo salen de medirlos."),
    }


def terminos_de_subcategoria(categoria_id: str, limite: int = 30) -> dict[str, Any]:
    """
    La barra de términos de una subcategoría: qué se busca ahí y qué cubrimos.

    La cobertura se mide contra TODOS nuestros títulos de esa subcategoría juntos
    (los 3 SKUs de Tapetes son 6 publicaciones), porque la pregunta del nivel de
    subcategoría es "¿este nicho nos encuentra?", no "¿esta publicación?".
    """
    skus = [s for s in competencia_store.listar_skus(False)
            if s.get("categoria_id") == categoria_id]
    titulos: dict[str, str] = {}
    for s in skus:
        for p in competencia_store.publicaciones(s["sku"]):
            if p.get("titulo"):
                titulos[f"{s['sku']} · {p['cuenta']}"] = p["titulo"]
    terminos = competencia_store.terminos_categoria(categoria_id, titulos, limite)
    return {
        "categoria_id": categoria_id,
        "terminos": terminos,
        "total": competencia_store.total_terminos(categoria_id),
        "cubiertos": sum(1 for t in terminos if t.get("cubierto")),
        "skus": [s["sku"] for s in skus],
        "aviso": None if terminos else
                 "Mercado Libre no publica términos de búsqueda de esta categoría.",
    }


def sugerir_palabras_subcategoria(categoria_id: str,
                                  limite_terminos: int = 30) -> dict[str, Any]:
    """
    Palabras clave que conviene usar en TODA la subcategoría.

    A diferencia de la sugerencia por SKU, aquí el insumo son dos cosas reales: los
    términos que ML publica y los TÍTULOS DE LOS LÍDERES del nicho (los que ya
    ganan el ranking). La IA agrupa y prioriza; no inventa demanda.
    """
    datos = terminos_de_subcategoria(categoria_id, limite_terminos)
    terminos = datos["terminos"]
    if not terminos:
        return {"ok": False, "motivo": datos["aviso"]}

    top = competencia_store.ranking_categoria(categoria_id, "hoja", limite=20)
    lideres = [t["titulo"] for t in top if t.get("titulo")][:10]
    faltantes = [t["termino"] for t in terminos if not t.get("cubierto")]

    from services import ia_generadores

    system = (
        "Eres experto en posicionamiento (SEO) de Mercado Libre México. Te doy una "
        "subcategoría, los términos que la gente MÁS BUSCA ahí, cuáles de esos NO "
        "cubren nuestros títulos, y los títulos de los vendedores que hoy LIDERAN "
        "el ranking.\n"
        "Devuelve las palabras clave que conviene usar en los títulos de esa "
        "subcategoría, ordenadas por lo que más tráfico puede ganar. Usa solo "
        "vocabulario que aparezca en los términos buscados o en los títulos de los "
        "líderes: NO inventes palabras nuevas. Agrupa las variantes (singular/plural, "
        "sinónimos) y para cada una di por qué conviene, en una frase corta.\n"
        'Responde SOLO JSON: {"palabras":[{"palabra":"...","porque":"...",'
        '"variantes":["..."]}],"evitar":["..."]}'
    )
    usuario = (
        f"SUBCATEGORÍA: {datos['categoria_id']}\n"
        f"TÉRMINOS MÁS BUSCADOS:\n"
        + "\n".join(f"{t['posicion']}. {t['termino']}"
                    f"{'' if t.get('cubierto') else '   ← NO lo cubrimos'}"
                    for t in terminos)
        + f"\n\nTÍTULOS DE LOS LÍDERES DEL RANKING:\n"
        + "\n".join(f"- {t}" for t in lideres)
    )

    res = ia_generadores._completar(system, usuario, max_tokens=1200)
    if not res.get("ok"):
        return {"ok": False, "motivo": f"La IA no respondió: {res.get('error')}"}
    txt = (res.get("texto") or "").strip()
    try:
        import json as _json
        d = _json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"La IA no devolvió JSON válido: {exc}",
                "crudo": txt[:400]}

    # Se marca cuáles de las palabras propuestas salen de verdad de la demanda
    # medida. Una palabra que no aparece en ningún término buscado ni en ningún
    # título líder es invención de la IA, y hay que poder distinguirla.
    corpus = " ".join([t["termino"] for t in terminos] + lideres).lower()
    palabras = []
    for p in d.get("palabras") or []:
        w = (p.get("palabra") or "").strip()
        if not w:
            continue
        palabras.append({
            "palabra": w,
            "porque": p.get("porque"),
            "variantes": p.get("variantes") or [],
            "respaldada": all(x in corpus for x in w.lower().split()),
        })
    # CANDADO: la IA metió en "evitar" los términos #1 y #2 más buscados de Tapetes
    # ("tapetes carro", "tapetes de carro"). Un término que ML publica es DEMANDA
    # MEDIDA; recomendar no usarlo es un consejo destructivo. Se descartan de la
    # lista de evitar y se reportan aparte para no esconder que lo intentó.
    medidos = {t["termino"].lower() for t in terminos}
    evitar, descartados = [], []
    for x in (d.get("evitar") or []):
        if not x:
            continue
        (descartados if x.lower() in medidos else evitar).append(x)
    if descartados:
        log.warning("sugerir_palabras_subcategoria(%s): la IA quiso evitar términos "
                    "que SÍ se buscan: %s", categoria_id, descartados)
    return {
        "ok": True,
        "categoria_id": categoria_id,
        "faltantes": faltantes,
        "palabras": palabras,
        "evitar": evitar,
        "evitar_descartados": descartados,
    }


def nichos_del_top(raiz: dict[str, Any], tope: int = 5) -> list[dict[str, Any]]:
    """
    Los nichos donde SÍ conviene competir, dictados por el top de la categoría
    padre y no por nuestro inventario.

    Se recorre el ranking de la raíz en orden (#1, #2, #3…), se agrupa por
    subcategoría —#5 y #6 son los dos Cargadores de Baterías, así que cuentan como
    UN nicho— y a cada uno se le pega cuántos SKUs tenemos ahí. La pregunta que
    contesta es "¿tenemos con qué pelear el #1?", y a veces la respuesta es no:
    en MLM1747 el #4 es aceite de motor (MLM187678) y en catálogo tenemos CERO.

    Los SKUs se cuentan sobre `categorias_ml`, el catálogo COMPLETO, no sobre los
    8 vigilados: el hallazgo suele ser que el nicho #1 tiene 15 productos nuestros
    y solo uno está bajo observación.

    La entrada de tipo ITEM del ranking queda sin nicho: /items de un ajeno es 403
    y no hay otra ruta para saber su categoría. Se reporta como hueco, no se omite.
    """
    top = raiz.get("top") or []

    # Los nichos NO dependen del raspado: posición y categoría son 100% API
    # (/highlights + /products/{id}/items). El ranking raspado solo aporta la FICHA
    # del líder (título, foto, precio), y si falta se usa lo que tenga la fila.
    #
    # Esto importa: las filas capturadas con el actor de Apify no traen `id_pagina`,
    # así que el join con /highlights no las alcanzaba y Hogar y Jardín salía con 0
    # nichos. Resolviendo por API el resultado ya no depende de con qué se raspó.
    if not top:
        return []

    # SOLO DATOS GUARDADOS. Esta función corre en cada carga de /vista, y una
    # versión anterior resolvía la subcategoría llamando a la API de ML aquí: con
    # 26 raíces eran cientos de llamadas por request y la página pasó de responder
    # en 2 s a agotar 150 s en producción. La resolución vive en la CAPTURA
    # (`_subcategoria_de_cada_fila`), que deja `item_categoria_id` en la tabla.
    nichos: dict[str, dict[str, Any]] = {}
    sin_categoria: list[dict[str, Any]] = []
    for fila in sorted(top, key=lambda x: x.get("posicion") or 99):
        cid = fila.get("item_categoria_id")
        if not cid:
            # Tipo ITEM (/items de un ajeno es 403) o ranking capturado antes de
            # que se guardara la subcategoría. Se cuenta, no se inventa.
            sin_categoria.append(fila)
            continue
        if cid not in nichos:
            nichos[cid] = {
                "categoria_id": cid,
                "categoria_nombre": fila.get("item_categoria_nombre") or cid,
                "posicion": fila.get("posicion"),
                "lider": fila,
                "otras_posiciones": [],
            }
        else:
            nichos[cid]["otras_posiciones"].append(fila.get("posicion"))

    orden = sorted(nichos.values(), key=lambda n: n["posicion"] or 99)[:tope]
    if not orden:
        return []

    # ¿Qué tenemos en catálogo en cada nicho? Una sola consulta para todos.
    cids = [n["categoria_id"] for n in orden]
    catalogo: dict[str, list[dict[str, Any]]] = {c: [] for c in cids}
    try:
        marcas = ",".join(["%s"] * len(cids))
        for f in db.fetch_all(
            f"SELECT sku, category_id FROM categorias_ml WHERE category_id IN ({marcas}) "
            f"ORDER BY sku", tuple(cids)):
            catalogo.setdefault(f["category_id"], []).append({"sku": f["sku"]})
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo contar el catálogo por categoría: %s", exc)

    # ¿Y cuáles de esos ya están bajo observación?
    vigilados: dict[str, list[str]] = {}
    for s in competencia_store.listar_skus(False):
        if s.get("categoria_id"):
            vigilados.setdefault(s["categoria_id"], []).append(s["sku"])

    for n in orden:
        cid = n["categoria_id"]
        mios = catalogo.get(cid) or []
        n["n_catalogo"] = len(mios)
        n["skus_catalogo"] = [m["sku"] for m in mios[:12]]
        n["skus_vigilados"] = vigilados.get(cid) or []
        n["tenemos"] = bool(mios)
        # El hueco que importa: hay producto en catálogo pero nadie lo mide.
        n["sin_vigilancia"] = bool(mios) and not n["skus_vigilados"]

    if sin_categoria:
        log.info("nichos_del_top: %s entradas del ranking sin subcategoría "
                 "(son de tipo ITEM, /items ajeno es 403)", len(sin_categoria))
    return orden


def _nombres_desde_woo(skus: list[str]) -> dict[str, str]:
    """
    Título desde WooCommerce para los SKUs que la tabla maestra `productos` no
    conoce. Hace falta de verdad: MUE-0163-TEL existe en Woo (wc_id 11154,
    'Lona Sombra Reforzada 4x6m…') y está categorizado en categorias_ml, pero NO
    está en `productos` — el caso de la lookup table huérfana. Sin este fallback
    se quedaría fuera del MVP por un hueco de la maestra, no por falta de datos.
    """
    if not skus:
        return {}
    try:
        from services import wp_db
        marcas = ",".join(["%s"] * len(skus))
        filas = wp_db._fetch_all(f"""
            SELECT pm.meta_value AS sku, p.post_title AS nombre
              FROM wp_postmeta pm
              JOIN wp_posts p ON p.ID = pm.post_id
             WHERE pm.meta_key = '_sku' AND pm.meta_value IN ({marcas})
               AND p.post_type IN ('product', 'product_variation')
               AND p.post_status NOT IN ('trash', 'auto-draft')
        """, tuple(skus))
        return {f["sku"]: f["nombre"] for f in filas if f.get("nombre")}
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer nombres de WooCommerce: %s", exc)
        return {}


def _fotos_desde_woo(skus: list[str]) -> dict[str, str]:
    """
    Foto del producto desde WooCommerce (la base de WordPress), no de ML.

    Es la foto del catálogo: la que el equipo cura en Woo, la misma que se ve en
    phpMyAdmin. Sale de `_thumbnail_id` → el attachment, vía `wp_db.imagenes`, que
    ya existía. La de ML sería la de la publicación, que puede diferir.

    El `wc_id` se busca en `productos` y, si falta, en WordPress directo: es el
    caso de MUE-0163-TEL (wc_id 11154), que la tabla maestra no conoce.
    """
    if not skus:
        return {}
    try:
        from services import wp_db
        marcas = ",".join(["%s"] * len(skus))
        wc = {r["sku"]: r["wc_id"] for r in db.fetch_all(
            f"SELECT sku, wc_id FROM productos WHERE sku IN ({marcas}) AND wc_id IS NOT NULL",
            tuple(skus)) if r.get("wc_id")}
        faltan = [s for s in skus if s not in wc]
        if faltan:
            mm = ",".join(["%s"] * len(faltan))
            for r in wp_db._fetch_all(
                f"SELECT pm.meta_value AS sku, p.ID AS wc_id FROM wp_postmeta pm "
                f"JOIN wp_posts p ON p.ID = pm.post_id "
                f"WHERE pm.meta_key = '_sku' AND pm.meta_value IN ({mm}) "
                f"AND p.post_type = 'product'", tuple(faltan)):
                wc[r["sku"]] = r["wc_id"]
        out = {}
        for sku, wid in wc.items():
            imgs = wp_db.imagenes(int(wid))
            if imgs:
                out[sku] = imgs[0]
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudieron leer las fotos de WooCommerce: %s", exc)
        return {}


def sembrar_skus(skus: list[str], con_ia: bool = True) -> dict[str, Any]:
    """
    Da de alta (o refresca) los SKUs vigilados.

    Fuentes, en cascada:
      nombre     → `productos`, y si no está, WooCommerce (lookup huérfana)
      categoría  → `categorias_ml`, con la ruta ya partida en cat1..cat4 (es lo
                   que permite agrupar la tabla por nivel de categoría)
      publicación→ `ml_progress` (se guarda la de BEKURA como referencia)
      término    → lo propone la IA; si ya fue corregido a mano, no se pisa
    """
    if not skus:
        return {"ok": False, "motivo": "Lista de SKUs vacía."}

    marcas = ",".join(["%s"] * len(skus))
    # La categoría se lee por SKU y NO se cuelga de `productos`: hay SKUs
    # categorizados que la maestra no tiene (MUE-0163-TEL).
    filas = db.fetch_all(f"""
        SELECT s.sku, p.nombre,
               c.category_id, c.category_name, c.ruta, c.cat1, c.cat2, c.cat3, c.cat4,
               MAX(CASE WHEN mp.cuenta = 'BEKURA' THEN mp.ml_item_id END) AS item_bekura,
               MAX(mp.ml_item_id) AS item_cualquiera,
               MAX(CASE WHEN mp.cuenta = 'BEKURA' THEN 'BEKURA' END) AS tiene_bekura
          FROM (SELECT %s AS sku {"".join(f" UNION ALL SELECT %s" for _ in skus[1:])}) s
          LEFT JOIN productos p     ON p.sku = s.sku
          LEFT JOIN categorias_ml c ON c.sku = s.sku
          LEFT JOIN ml_progress mp  ON mp.sku = s.sku
               AND mp.ml_item_id IS NOT NULL AND mp.ml_item_id <> ''
         GROUP BY s.sku, p.nombre, c.category_id, c.category_name, c.ruta,
                  c.cat1, c.cat2, c.cat3, c.cat4
    """, tuple(skus))

    # Fallback a Woo solo para los que no traen nombre de la maestra.
    sin_nombre = [f["sku"] for f in filas if not f.get("nombre")]
    de_woo = _nombres_desde_woo(sin_nombre)

    fotos = _fotos_desde_woo([f["sku"] for f in filas])
    # La RAÍZ del path por id: una llamada por categoría distinta, no por SKU
    # (los 3 de Tapetes comparten MLM162997).
    raices: dict[str, dict[str, str]] = {}
    for cid in {f.get("category_id") for f in filas if f.get("category_id")}:
        ruta = competencia_ml.ruta_categoria(cid)
        if ruta:
            raices[cid] = ruta[0]
    productos, sin_nombre_en_ningun_lado = [], []
    for f in filas:
        nombre = f.get("nombre") or de_woo.get(f["sku"])
        if not nombre:
            sin_nombre_en_ningun_lado.append(f["sku"])
            continue
        productos.append({
            "sku": f["sku"],
            "nombre": nombre,
            "origen_nombre": "productos" if f.get("nombre") else "woocommerce",
            "imagen": fotos.get(f["sku"]),
            "categoria_id": f.get("category_id"),
            "categoria_nombre": f.get("category_name"),
            "raiz_id": (raices.get(f.get("category_id")) or {}).get("id"),
            "raiz_nombre": (raices.get(f.get("category_id")) or {}).get("nombre"),
            "ruta": f.get("ruta"),
            "cat1": f.get("cat1"), "cat2": f.get("cat2"),
            "cat3": f.get("cat3"), "cat4": f.get("cat4"),
            "ml_item_id": f.get("item_bekura") or f.get("item_cualquiera"),
            "cuenta": "BEKURA" if f.get("tiene_bekura") else None,
        })

    terminos: dict[str, str] = {}
    if con_ia and productos:
        terminos = competencia_terminos.proponer(productos)
    for p in productos:
        p["termino_general"] = terminos.get(p["sku"])
        p["termino_origen"] = "ia"

    guardados = competencia_store.guardar_skus(productos)
    return {
        "ok": True,
        "guardados": guardados,
        "nombre_desde_woo": sorted(de_woo),
        "sin_nombre": sin_nombre_en_ningun_lado,
        # Sin publicación en ML se pueden medir las búsquedas y el top de la
        # categoría, pero "mi posición" siempre dirá "fuera": no hay publicación
        # nuestra que encontrar. Es información, no un error.
        "sin_publicacion_ml": [p["sku"] for p in productos if not p["ml_item_id"]],
        "sin_categoria": [p["sku"] for p in productos if not p["categoria_id"]],
        "sin_termino_general": [p["sku"] for p in productos if not p["termino_general"]],
        "sin_foto": [p["sku"] for p in productos if not p.get("imagen")],
    }


async def capturar_rankings_categorias(periodo: str | None = None) -> dict[str, Any]:
    """
    Top 10 de más vendidos de la categoría RAÍZ y de la ÚLTIMA categoría de cada
    SKU vigilado, raspado de `/mas-vendidos/{cat}`.

    Se agrupa POR CATEGORÍA y no por SKU: los 3 SKUs de Tapetes comparten
    MLM162997 y las 7 autopartes comparten la raíz MLM1747, así que 8 SKUs son
    solo 8 páginas (2 raíces + 6 hojas) en vez de 16.

    Las VISITAS y las RESEÑAS sí se traen: el `href` de cada tarjeta lleva el
    `#wid=` con el item_id REAL, así que basta una llamada por fila y no hay que
    resolver el id del highlight con /products/{id}/items. Y se guardan los
    TÉRMINOS más buscados de cada categoría (/trends), que son gratis y salen en
    la misma pasada.
    """
    periodo = periodo or competencia_store.periodo_actual()
    skus = competencia_store.listar_skus()
    if not skus:
        return {"ok": False, "motivo": "No hay SKUs vigilados."}

    raices = {s["raiz_id"] for s in skus if s.get("raiz_id")}
    hojas = {s["categoria_id"] for s in skus if s.get("categoria_id")}
    # Si la hoja ES la raíz no se pide dos veces.
    hojas -= raices
    nivel_de = {**{c: "raiz" for c in raices}, **{c: "hoja" for c in hojas}}
    if not nivel_de:
        return {"ok": False, "motivo": "Los SKUs vigilados no tienen categoría."}

    # NAVEGADOR LOCAL, no Apify: decisión de José (3-ago). El actor genérico
    # funcionaba pero se paga por cómputo, y además su normalización tira
    # `id_pagina` — el id del URL, que es la llave para unir la ficha raspada con
    # /highlights y con la subcategoría de cada fila. `leer` es bloqueante
    # (Selenium lo es), de ahí el to_thread.
    crudos = await asyncio.to_thread(
        competencia_mas_vendidos.leer, list(nivel_de), 20)
    nuestras = _nuestras_publicaciones()

    guardados, avisos, terminos = {}, [], {}
    for cat, filas in crudos.items():
        _marcar(filas, nuestras)
        await _enriquecer_ranking(cat, filas, nivel_de.get(cat, "hoja"))
        n = competencia_store.reemplazar_ranking(cat, nivel_de.get(cat, "hoja"),
                                                 periodo, filas)
        guardados[cat] = n

    # Términos más buscados: una llamada por categoría, gratis, sin navegador.
    for cat in nivel_de:
        t = await asyncio.to_thread(competencia_ml.tendencias, cat)
        if t:
            terminos[cat] = competencia_store.reemplazar_terminos(
                cat, periodo, [{"termino": x["keyword"], "url": x.get("url")} for x in t])
        else:
            # Mismo criterio que el ranking: ausencia de términos es un HECHO de
            # ML, no un fallo nuestro. La vista lo muestra como "sin datos".
            competencia_store.reemplazar_terminos(cat, periodo, [])
            avisos.append(f"{cat}: ML no publica términos de búsqueda de esta "
                          "categoría (/trends responde 404).")

    # Distinguir las DOS causas de un ranking vacío, porque llevan a acciones
    # opuestas. Verificado: Cartuchos de Turbo y Bujías fallan siempre y su
    # /highlights también devuelve 0 — ML simplemente no publica más vendidos de
    # esas categorías. Culpar al bloqueo mandaría a reintentar algo que no existe.
    for cat in nivel_de:
        if cat in crudos:
            continue
        hay_ranking = bool(competencia_ml.mas_vendidos_categoria(cat))
        if hay_ranking:
            avisos.append(f"{cat}: ML sí tiene ranking pero el raspado no lo trajo "
                          "(bloqueo intermitente). Vale reintentar.")
        else:
            avisos.append(f"{cat}: Mercado Libre NO publica más vendidos de esta "
                          "categoría (su /highlights también viene vacío). No es un "
                          "fallo del raspado y reintentar no cambia nada.")

    return {"ok": True, "periodo": periodo, "categorias": len(nivel_de),
            "con_datos": len(crudos), "guardados": guardados,
            "terminos": terminos, "avisos": avisos}


async def _enriquecer_ranking(categoria_id: str, filas: list[dict[str, Any]],
                              nivel: str = "hoja") -> None:
    """
    Le pega al ranking raspado lo que solo da la API: el TIPO de entrada y la
    posición oficial de /highlights, más visitas y reseñas del item real.

    El join es por `id_pagina` (el id que va en el URL), que es exactamente el que
    devuelve /highlights. El `externo_id` del raspador es el item REAL (viene del
    `#wid=` del href) y es el que sirve para /visits y /reviews — pedirle visitas
    al id de la página no funciona porque un MLMU no es una publicación.
    """
    hl = {e["id"]: e for e in
          (await asyncio.to_thread(competencia_ml.mas_vendidos_categoria, categoria_id))}
    for f in filas:
        # `mas_vendidos_categoria` ya normaliza a español (id/posicion/tipo); leer
        # 'type'/'position' aquí dejaba el tipo en NULL y con eso la subcategoría
        # de cada fila nunca se resolvía.
        e = hl.get(f.get("id_pagina") or f.get("externo_id") or "") or {}
        f["tipo"] = e.get("tipo")
        if e.get("posicion"):
            f["posicion"] = e["posicion"]

    objetivo = [f for f in filas if (f.get("externo_id") or "").startswith("MLM")
                and not (f.get("externo_id") or "").startswith("MLMU")]
    if not objetivo:
        return
    vis, rev = await asyncio.gather(
        asyncio.gather(*(asyncio.to_thread(competencia_ml.visitas_30d, f["externo_id"])
                         for f in objetivo), return_exceptions=True),
        asyncio.gather(*(asyncio.to_thread(competencia_ml.reviews, f["externo_id"])
                         for f in objetivo), return_exceptions=True))
    for f, v, r in zip(objetivo, vis, rev):
        if isinstance(v, int):
            f["visitas_30d"] = v
        if isinstance(r, dict):
            f["reviews"] = r.get("reviews")
            f["rating"] = f.get("rating") or r.get("rating")

    if nivel == "raiz":
        await _subcategoria_de_cada_fila(filas)


async def _subcategoria_de_cada_fila(filas: list[dict[str, Any]]) -> None:
    """
    A qué SUBCATEGORÍA pertenece cada fila del ranking de la raíz.

    Es lo que permite ordenar nuestros SKUs por oportunidad: si nuestra
    subcategoría es la del #1 del padre, ahí está la pelea que importa. Verificado
    en MLM1747: el #1 es Fundas para Vehículos (MLM92152), justo donde tenemos
    TEC-1539-AZL-XL.

    Las entradas de tipo ITEM quedan sin categoría: /items de un ajeno es 403 y no
    hay otra ruta. Se acepta el hueco en vez de inventarlo (7 de 8 en MLM1747).
    """
    pend = [f for f in filas if f.get("tipo") in ("PRODUCT", "USER_PRODUCT")
            and f.get("id_pagina")]
    if not pend:
        return
    res = await asyncio.gather(
        *(asyncio.to_thread(competencia_ml.competidores_de_producto, f["id_pagina"], 1)
          for f in pend), return_exceptions=True)
    cats: dict[str, str | None] = {}
    for f, r in zip(pend, res):
        if not isinstance(r, list) or not r:
            continue
        cid = r[0].get("categoria_id") or r[0].get("category_id")
        if not cid:
            continue
        f["item_categoria_id"] = cid
        if cid not in cats:
            ruta = await asyncio.to_thread(competencia_ml.ruta_categoria, cid)
            cats[cid] = ruta[-1]["nombre"] if ruta else None
        f["item_categoria_nombre"] = cats[cid]


# ── Las tres mediciones de un SKU ───────────────────────────────────────────

async def _medir_categoria(sku: dict[str, Any], periodo: str,
                           nuestras: dict[str, dict[str, str]]) -> tuple[int, int, list[str]]:
    """Ranking OFICIAL de la categoría, por API. Sin scraper y sin costo."""
    cat_id = sku.get("categoria_id")
    if not cat_id:
        return 0, 0, [f"{sku['sku']}: sin categoría de ML, no hay ranking que leer."]

    crudo = await asyncio.to_thread(competencia_ml.mas_vendidos_categoria, cat_id)
    if not crudo:
        return 0, 0, [f"{sku['sku']}: ML no publica ranking de más vendidos para "
                      f"«{sku.get('categoria_nombre') or cat_id}»."]

    filas = [await asyncio.to_thread(competencia_ml.resolver_highlight, e) for e in crudo]
    sin_ficha = sum(1 for f in filas if not f.get("titulo"))
    for f in filas:
        f["categoria_id"] = cat_id
        f["categoria_nombre"] = sku.get("categoria_nombre")
        f.pop("tipo_highlight", None)

    _marcar(filas, nuestras)
    ok = await _visitas(filas)
    n = competencia_store.reemplazar_resultados(sku["sku"], "categoria", periodo, filas)

    avisos = []
    if sin_ficha:
        avisos.append(f"{sku['sku']}: {sin_ficha} de {len(filas)} del ranking son de "
                      "tipo ITEM — la API de ML no permite leerlas (403) y quedan sin ficha.")
    return n, ok, avisos


async def _medir_busqueda(sku: dict[str, Any], tipo: str, termino: str, periodo: str,
                          nuestras: dict[str, dict[str, str]],
                          cache: dict[str, list[dict[str, Any]]]) -> tuple[int, int, list[str]]:
    """
    Una búsqueda ('general' o 'titulo') por scraper. El resultado se cachea por
    término: varios SKUs comparten término general y no hay razón para pagarlo
    dos veces.
    """
    clave = termino.strip().lower()
    if clave in cache:
        crudas = cache[clave]
    else:
        crudas = await competencia_scraper.buscar(
            termino, limite=settings.competencia_top,
            con_detalle=settings.competencia_con_detalle)
        cache[clave] = crudas

    if not crudas:
        return 0, 0, [f"{sku['sku']} ({tipo}): el scraper no devolvió resultados "
                      f"para «{termino}»."]

    # Copia por SKU: el marcado de es_nuestro y las visitas son por medición.
    filas = [dict(c) for c in crudas]
    for f in filas:
        f["categoria_id"] = sku.get("categoria_id")
        f["categoria_nombre"] = sku.get("categoria_nombre")
        f.pop("catalog_product_id", None)

    _marcar(filas, nuestras)
    ok = await _visitas(filas)
    n = competencia_store.reemplazar_resultados(sku["sku"], tipo, periodo, filas,
                                                termino=termino)
    return n, ok, []


async def medir_sku(sku: dict[str, Any], periodo: str,
                    nuestras: dict[str, dict[str, str]],
                    cache: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Las tres mediciones de un SKU."""
    total = visitas_ok = 0
    avisos: list[str] = []

    # 1. Búsqueda general (descubrimiento).
    if sku.get("termino_general"):
        n, ok, av = await _medir_busqueda(sku, "general", sku["termino_general"],
                                         periodo, nuestras, cache)
        total += n; visitas_ok += ok; avisos += av
    else:
        avisos.append(f"{sku['sku']}: sin término general, no se mide descubrimiento.")

    # 2. Título completo (competencia directa).
    if sku.get("nombre"):
        n, ok, av = await _medir_busqueda(sku, "titulo", sku["nombre"],
                                         periodo, nuestras, cache)
        total += n; visitas_ok += ok; avisos += av

    # 3. Ranking de la categoría (los mejores).
    n, ok, av = await _medir_categoria(sku, periodo, nuestras)
    total += n; visitas_ok += ok; avisos += av

    return {"sku": sku["sku"], "resultados": total, "visitas_ok": visitas_ok,
            "avisos": avisos}


# ── La corrida completa ─────────────────────────────────────────────────────

async def correr(periodo: str | None = None, origen: str = "manual",
                 skus: list[str] | None = None) -> dict[str, Any]:
    """
    Mide todos los SKUs vigilados (o los que se indiquen) y deja la foto del mes.

    Los SKUs se corren EN SERIE: cada uno lanza búsquedas de Apify y decenas de
    llamadas de visitas; en paralelo solo se gana rate-limit.
    """
    periodo = periodo or competencia_store.periodo_actual()
    vigilados = competencia_store.listar_skus()
    if skus:
        pedidos = set(skus)
        vigilados = [s for s in vigilados if s["sku"] in pedidos]
    if not vigilados:
        return {"ok": False, "motivo": "No hay SKUs vigilados. Corre la siembra "
                                       "primero (POST /api/competencia/sembrar).",
                "periodo": periodo}

    corrida_id = competencia_store.abrir_corrida(periodo, origen)
    nuestras = _nuestras_publicaciones()
    cache: dict[str, list[dict[str, Any]]] = {}
    detalle, avisos = [], []
    total = visitas_ok = 0

    try:
        for s in vigilados:
            r = await medir_sku(s, periodo, nuestras, cache)
            detalle.append(r)
            total += r["resultados"]
            visitas_ok += r["visitas_ok"]
            avisos += r["avisos"]

        costo = competencia_scraper.costo_estimado(
            len(cache), settings.competencia_top, settings.competencia_con_detalle)
        competencia_store.cerrar_corrida(corrida_id, len(vigilados), total,
                                         visitas_ok, costo, avisos=avisos)
        log.info("Corrida %s: %s SKUs, %s resultados, %s visitas, %s búsquedas (~$%s)",
                 periodo, len(vigilados), total, visitas_ok, len(cache), costo)
        return {"ok": True, "periodo": periodo, "corrida_id": corrida_id,
                "skus": len(vigilados), "resultados": total, "visitas_ok": visitas_ok,
                "busquedas": len(cache), "costo_apify_usd": costo,
                "avisos": avisos, "detalle": detalle}

    except Exception as exc:  # noqa: BLE001
        log.error("Corrida de competencia falló: %s", exc)
        competencia_store.cerrar_corrida(corrida_id, len(vigilados), total,
                                         visitas_ok, error=str(exc)[:500])
        return {"ok": False, "motivo": str(exc), "corrida_id": corrida_id}
