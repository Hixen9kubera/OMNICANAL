"""
walmart_panel.py — Lo que el PANEL necesita saber de Walmart: qué hay publicado.

Mismo papel que `tiktok_panel.py` y `temu_panel.py`, misma fuente
(`channel.listings` en kubera). Hasta el 17-ago la pestaña Walmart mostraba
datos de EJEMPLO encima de una cuenta con 235 artículos reales.

LO BUENO DE ESTE CANAL: el estado es una PALABRA
────────────────────────────────────────────────
`publishedStatus` viene como PUBLISHED / UNPUBLISHED / SYSTEM_PROBLEM. No hay
que decodificar nada — en Temu hicieron falta los totales del Seller Center para
adivinar qué significaban siete números. Aquí el panel puede afirmar qué está
publicado y qué no.

⚠️ LAS DOS TAXONOMÍAS DE WALMART — la trampa de este canal
──────────────────────────────────────────────────────────
Walmart usa DOS clasificaciones distintas y no coinciden en un solo valor
(medido: 100 `productType` contra 76 categorías del esquema, **0 en común**):

  · `productType` — lo que Walmart ASIGNÓ al artículo después de publicarlo.
    Es la hoja fina: "Licuadoras", "Flores Artificiales", "Abanicos de Mano".
    Es lo que se guarda en `listings.category_id` porque es lo que el canal
    dice del producto.

  · La categoría del ESQUEMA — "Electrónicos", "Juguetes", "Cocina, Decoración
    y Otros". Es la que decide **qué campos exige** y la que indexa
    `channel.field_requirements`.

Cruzar una con la otra devuelve CERO filas sin dar ningún error — el mismo
defecto que en Amazon (`product_type` vs `category_id`) y que ya costó una
mañana. Por eso `categoria_esquema()` no adivina: resuelve con la MISMA
configuración que usa el publicador (`CATEGORIAS_AUTORIZADAS`), para que el
semáforo y el alta no puedan contradecirse.

Y un dato que el censo destapó: **105 de 235 artículos están en «Por Defecto»**,
que no es una categoría sino el hueco — Walmart no supo dónde ponerlos.
"""
from __future__ import annotations

import logging
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.walmart_panel")

CANAL = "walmart"
ESTADO_VIVO = "PUBLISHED"
SIN_CATEGORIA = "Por Defecto"


def filtro_sql_publicado(alias: str = "l", clave: str = "wm_vivo") -> tuple[str, dict]:
    """`(expresión, params)` de «está publicado» en Walmart, compartido entre la
    rejilla y la fuente `canales` del flujo del SKU: un predicado escrito dos
    veces se desincroniza sin que nada falle."""
    return f"upper({alias}.status) = %({clave})s", {clave: ESTADO_VIVO}

_SEL = """
    select l.sku::text as sku, p.wc_id, p.name as nombre,
           l.price, l.stock_own, l.status, l.situacion, l.listing_id, l.url,
           l.category_id
      from channel.listings l
      join core.products p on p.sku = l.sku
     where l.canal = %(canal)s
"""

_ORDEN = {
    "reciente": "l.updated_at desc nulls last",
    "precio_desc": "l.price desc nulls last",
    "precio_asc": "l.price asc nulls last",
    "stock_desc": "l.stock_own desc nulls last",
    "stock_asc": "l.stock_own asc nulls last",
}


def _normalizar(r: dict[str, Any]) -> dict[str, Any]:
    estado = (r.get("status") or "").upper()
    cat = r.get("category_id")
    return {
        "sku": r["sku"],
        "wc_id": r.get("wc_id"),
        "nombre": r.get("nombre") or r["sku"],
        "precio": float(r["price"]) if r.get("price") is not None else None,
        "precio_base": float(r["price"]) if r.get("price") is not None else None,
        # Walmart no devuelve stock en /v3/items: NULL es "no lo sabemos", que
        # es distinto de 0 ("agotado"). No se inventa.
        "stock": r.get("stock_own"),
        "estado": estado or "sin publicar",
        "situacion": estado or None,
        "categoria_id": cat,
        "categoria_path": ([{"id": cat, "nombre": cat}] if cat else []),
        "publicado": estado == ESTADO_VIVO,
        "item_id": r.get("listing_id"),      # wpid
        "url": r.get("url"),
        "full": None,
        "full_label": None,
        "origen": "db",
    }


def listar(page: int = 1, per_page: int = 40, search: str | None = None,
           solo_publicados: bool = False, orden: str = "reciente",
           estados: list[str] | None = None,
           skus_filtro: list[str] | None = None,
           solo_activas: bool = False,
           estricto: bool = False) -> tuple[list[dict[str, Any]], int]:
    """
    Publicaciones de Walmart con los filtros de la pantalla. (items, total).

    `solo_activas` usa el criterio de `publicaciones_panel`, que aquí coincide
    con `ESTADO_VIVO` (`PUBLISHED`, 207 de 235). Se pide en vez de re-escribirse
    para que los dos filtros no puedan separarse.

    `estricto`: con una lista resuelta por el sistema, un `[], 0` diría
    «ninguna» cuando lo cierto es «kubera no contestó». Ahí se lanza.
    """
    where, params = [], {"canal": CANAL}
    if search:
        where.append("(l.sku::text ilike %(like)s or p.name ilike %(like)s)")
        params["like"] = f"%{search}%"
    if skus_filtro:
        # `lower()` de los dos lados, igual que en TikTok y Temu.
        where.append("lower(l.sku::text) = any(%(skus)s)")
        params["skus"] = sorted({str(s).lower() for s in skus_filtro})
    if solo_activas:
        from services import publicaciones_panel
        frag = publicaciones_panel.filtro_sql_activas(CANAL)
        if frag:
            where.append(frag[0])
            params.update(frag[1])
    elif solo_publicados or (estados and "publicado" in estados and "inactivo" not in estados):
        expr, p = filtro_sql_publicado("l", "vivo")
        where.append(expr)
        params.update(p)
    elif estados and "inactivo" in estados and "publicado" not in estados:
        where.append("upper(l.status) is distinct from %(vivo)s")
        params["vivo"] = ESTADO_VIVO

    filtro = (" and " + " and ".join(where)) if where else ""
    params["limit"] = per_page
    params["offset"] = max(0, (page - 1) * per_page)
    try:
        filas = sdb.fetch_all(
            f"{_SEL}{filtro} order by {_ORDEN.get(orden, _ORDEN['reciente'])} "
            f"limit %(limit)s offset %(offset)s", params)
        total = sdb.fetch_all(
            f"""select count(*) as n from channel.listings l
                  join core.products p on p.sku = l.sku
                 where l.canal = %(canal)s{filtro}""", params)
        return [_normalizar(f) for f in filas], int((total or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.listar falló: %s", exc)
        if estricto:
            raise
        return [], 0


def contar_publicados() -> int:
    """TODAS las publicaciones del canal — mismo criterio que TikTok y Temu:
    un artículo despublicado es trabajo por destrabar, no algo que esconder."""
    try:
        r = sdb.fetch_all("select count(*) as n from channel.listings where canal=%(c)s",
                          {"c": CANAL})
        return int((r or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.contar_publicados falló: %s", exc)
        return 0


def resumen_estados() -> list[dict[str, Any]]:
    try:
        return [{"estado": f["status"], "publicaciones": int(f["n"])}
                for f in sdb.fetch_all(
                    """select status, count(*) n from channel.listings
                        where canal=%(c)s group by status order by n desc""",
                    {"c": CANAL})]
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.resumen_estados falló: %s", exc)
        return []


def datos_de(sku: str) -> dict[str, Any] | None:
    """La publicación de Walmart de UN SKU, ya normalizada, o None."""
    try:
        filas = sdb.fetch_all(f"{_SEL} and l.sku = %(sku)s::citext limit 1",
                              {"canal": CANAL, "sku": sku})
        return _normalizar(filas[0]) if filas else None
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.datos_de(%s) falló: %s", sku, exc)
        return None


# ── La categoría del ESQUEMA (la que decide qué campos se exigen) ────────────

def categoria_esquema(nombre: str | None, categoria_woo: str | None = None,
                      sku: str | None = None) -> str | None:
    """
    Qué categoría del esquema le toca a un producto — la que indexa
    `channel.field_requirements`.

    NO se deduce del `productType` (son taxonomías distintas, ver el encabezado):
    se resuelve con los MISMOS patrones que usa el publicador, importados de
    `scripts.publicar_walmart`. Si el panel y el alta usaran reglas distintas,
    el semáforo diría verde sobre unos campos y se publicarían otros.

    Devuelve None cuando ninguna categoría autorizada aplica: ese producto no se
    puede publicar todavía, y decirlo es más útil que asignarle una al azar.
    """
    # Delegado en `publicar_walmart.clasificar`, que es LA regla. Esta función
    # tenía su propia copia y se había quedado atrás: clasificaba solo por
    # texto, sin mirar el prefijo del SKU, así que decía "Cocina" de productos
    # que el publicador manda a "Electrónicos". Dos reglas para una decisión
    # es una regla que se desincroniza.
    if not f"{nombre or ''} {categoria_woo or ''}".strip() and not sku:
        return None
    # Ahora con la elección del panel primero, igual que el botón de publicar:
    # `resolver_categoria` es la precedencia completa, `clasificar` solo la
    # mitad automática.
    try:
        from services.publicar_walmart import resolver_categoria
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel: no se pudo leer la config del publicador: %s", exc)
        return None
    _clave, cfg, _motivo, _origen = resolver_categoria(
        sku or "", nombre or "", categoria_woo or "")
    return cfg.get("clave_visible") if cfg else None


# ═════════════════════════════════════════════════════════════════════════════
# EL SELECTOR DE CATEGORÍA — el que Walmart no tenía (28-sep-2026)
# ═════════════════════════════════════════════════════════════════════════════
# Amazon, TikTok y Temu tenían su selector en el Estudio. Walmart decidía la
# categoría A CIEGAS al publicar, con patrones de título y prefijos de SKU: lo
# que no casaba con ninguna regla se rechazaba con "ninguna categoría con
# exención aplica", aunque la hubiera. Con 16 categorías autorizadas eso deja
# de escalar — y las reglas ya fallaban donde el prefijo miente (ORG-0245-MUL
# es un inflable de Halloween).
#
# MISMO CONTRATO QUE TEMU Y TIKTOK, con una diferencia de fondo:
#   · Allá el CANAL propone candidatas (`category.recommend`) y la IA elige
#     entre ellas. **Walmart MX no tiene recomendador**: la taxonomía y el spec
#     por API son "US only". Pero sus categorías de feed son solo 75, así que
#     la IA elige entre TODAS — descritas por los campos que cada una pide
#     ("Firmeza del colchón", "Tipo de equipaje"), que es lo que dice qué se
#     vende ahí. Con el nombre a secas, "Accesorios para bebés" parecía
#     «Portadores y Accesorios»… que es de maletas.
#   · La IA NO sabe cuáles tienen exención, a propósito: tiene que decir qué ES
#     el producto, no dónde hay permiso. Si lo que es no tiene exención, el
#     panel lo dice y no publica — en vez de meterlo en otra categoría.
#   · La sugerencia NO se guarda sola (regla 2): se propone, y una persona la
#     acepta. Recién entonces se escribe con `source='panel'`, y desde ahí
#     manda sobre las reglas en el publicador, la IA y el semáforo.

import time as _time  # noqa: E402

_CACHE: dict[str, Any] = {}
_TTL = 3600   # el catálogo solo cambia cuando corre el cargador del esquema


def _estado_exencion() -> dict[str, dict[str, Any]]:
    """Por etiqueta: si se puede publicar, con qué evidencia y folio. Sale del
    CÓDIGO (`CATEGORIAS_AUTORIZADAS`), no de `channel.categories.disponibilidad`,
    que se escribió una sola vez y se queda vieja en cuanto llega un ticket."""
    from scripts.publicar_walmart import (CATEGORIAS_AUTORIZADAS,
                                          CATEGORIAS_POR_CONFIRMAR)
    out: dict[str, dict[str, Any]] = {}
    for clave, c in CATEGORIAS_POR_CONFIRMAR.items():
        out[c["clave_visible"]] = {
            "autorizada": False, "sub_category": clave,
            "estado": ("negada" if str(c.get("evidencia", "")).startswith("❌")
                       else "sin_exencion"),
            "nota": c.get("que_falta")}
    for clave, c in CATEGORIAS_AUTORIZADAS.items():
        out[c["clave_visible"]] = {
            "autorizada": True, "sub_category": clave,
            "estado": "probada" if c.get("prueba") == "feed" else "por_ticket",
            "folio": c.get("folio_exencion"), "nota": None}
    return out


def _filas_esquema() -> list[dict[str, Any]]:
    """Las filas del bloque `Visible` de las 75 categorías (cacheadas)."""
    ahora = _time.time()
    if _CACHE.get("filas") and ahora - _CACHE.get("filas_t", 0) < _TTL:
        return _CACHE["filas"]
    filas = sdb.fetch_all(
        """select categoria_id as cat, campo,
                  coalesce(valores_permitidos->>'etiqueta_es', '') as et
             from channel.field_requirements
            where canal = %(c)s and categoria_id <> '*'
              and coalesce(valores_permitidos->>'bloque', 'Visible') = 'Visible'""",
        {"c": CANAL})
    _CACHE["filas"], _CACHE["filas_t"] = filas, ahora
    return filas


def catalogo_categorias() -> list[dict[str, Any]]:
    """
    Las categorías del feed de Walmart, cada una con su estado de exención y
    los campos PROPIOS que la describen. Misma fuente que el semáforo
    (`channel.field_requirements`): si el selector ofreciera una categoría sin
    requisitos cargados, el semáforo no tendría con qué medirla.
    """
    from collections import Counter, defaultdict
    try:
        filas = _filas_esquema()
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.catalogo_categorias falló: %s", exc)
        return []
    por: dict[str, list[tuple[str, str]]] = defaultdict(list)
    frec: Counter = Counter()
    for f in filas:
        por[f["cat"]].append((f["campo"], (f["et"] or "").strip()))
        frec[f["campo"]] += 1
    # "Propio" = aparece en pocas categorías. `color` o `countPerPack` están en
    # casi todas y no dicen nada; "Firmeza del colchón" dice exactamente qué es.
    umbral = max(3, int(len(por) * 0.12))
    estado = _estado_exencion()
    out = []
    for cat in sorted(por):
        propios = sorted(((c, e) for c, e in por[cat] if e and frec[c] <= umbral),
                         key=lambda x: frec[x[0]])
        info = estado.get(cat, {"autorizada": False, "estado": "sin_exencion"})
        out.append({"category_id": cat, "name": cat, "path": cat,
                    "describe": ", ".join(e for _, e in propios[:7]),
                    **info})
    return out


def buscar_categorias(q: str, limite: int = 25) -> list[dict[str, Any]]:
    """Por nombre o por lo que vende, sin acentos. Las autorizadas primero."""
    import unicodedata

    def plano(t: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                       if unicodedata.category(c) != "Mn")
    q = plano((q or "").strip())
    if len(q) < 2:
        return []
    hits = [c for c in catalogo_categorias()
            if q in plano(c["name"]) or q in plano(c.get("describe") or "")]
    hits.sort(key=lambda c: (not c.get("autorizada"),
                             q not in plano(c["name"]), c["name"]))
    return hits[:limite]


def categoria_elegida(sku: str) -> str | None:
    """La categoría que eligió una PERSONA en el panel para ese SKU, o None."""
    try:
        filas = sdb.fetch_all(
            """select category_id from channel.product_category
                where channel_id = %s and sku = %s::citext""", (CANAL, sku))
        elegida = (filas or [{}])[0].get("category_id")
        return str(elegida) if elegida else None
    except Exception as exc:  # noqa: BLE001 — sin elección legible, deciden las reglas
        log.warning("walmart_panel.categoria_elegida(%s): %s", sku, exc)
        return None


def guardar_categoria(sku: str, etiqueta: str) -> dict[str, Any]:
    """
    La categoría de Walmart ELEGIDA EN EL PANEL. Manda sobre las reglas.

    Se acepta aunque no tenga exención: la persona está diciendo qué ES el
    producto, y eso es cierto con o sin ticket. El publicador se niega a
    mandarla y dice qué falta — pero no la cambia por otra.
    """
    etiqueta = (etiqueta or "").strip()
    cats = {c["category_id"]: c for c in catalogo_categorias()}
    if etiqueta not in cats:
        return {"ok": False,
                "motivo": f"«{etiqueta}» no es una categoría del feed de Walmart. "
                          f"Ojo con los acentos: la etiqueta tiene que ser literal."}
    try:
        sdb.execute(
            """insert into channel.product_category
                 (sku, channel_id, category_id, source, updated_at)
               values (%s::citext, %s, %s, 'panel', now())
               on conflict (sku, channel_id) do update set
                 category_id = excluded.category_id, source = 'panel',
                 updated_at = now()""",
            (sku, CANAL, etiqueta))
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.guardar_categoria(%s): %s", sku, exc)
        return {"ok": False, "motivo": str(exc)}
    c = cats[etiqueta]
    return {"ok": True, "sku": sku, "categoria_id": etiqueta, "nombre": etiqueta,
            "path": etiqueta, "autorizada": c.get("autorizada", False),
            "aviso": (None if c.get("autorizada") else
                      f"Guardada, pero «{etiqueta}» NO tiene exención de UPC: el "
                      f"publicador no la va a mandar hasta que llegue el ticket.")}


def categoria_actual(sku: str, nombre: str = "", categorias_woo: str = ""
                     ) -> dict[str, Any]:
    """
    Qué categoría usaría HOY el publicador para este SKU, y de dónde sale:
    `panel` (alguien la eligió), `reglas` (patrones / prefijo) o ninguna — y en
    ese caso, por qué. Es la MISMA función que usa el botón de publicar, así
    que el Estudio no puede prometer una categoría y el feed salir con otra.
    """
    from services.publicar_walmart import resolver_categoria
    elegida = categoria_elegida(sku)
    _clave, cfg, motivo, origen = resolver_categoria(sku, nombre, categorias_woo)
    etiqueta = cfg["clave_visible"] if cfg else elegida
    info = _estado_exencion().get(etiqueta or "", {})
    return {"origen": origen or None, "category_id": etiqueta, "name": etiqueta,
            "path": etiqueta, "autorizada": bool(cfg),
            "estado": info.get("estado"), "folio": info.get("folio"),
            "motivo": motivo}


_PROMPT_CAT = """Eres catalogador de productos para Walmart México.

Clasifica el producto en UNA de las categorías del feed de Walmart. Junto a
cada categoría van los atributos que Walmart pide SOLO en ella: eso dice qué se
vende ahí. Úsalos para decidir, no solo el nombre.

PRODUCTO
  Título: {titulo}
  Categorías en la tienda propia: {cats}
  Categoría en Mercado Libre: {ml}
  Descripción: {desc}

CATEGORÍAS (nombre exacto — lo que la distingue)
{lista}

REGLAS
1. Decide por QUÉ ES el producto, no por palabras sueltas del título. Una
   refacción no va en la categoría del aparato completo. Un disfraz "de bebé"
   es un disfraz. Una colchoneta de ejercicio no es un colchón.
2. Copia el nombre EXACTO de la lista, con sus acentos y mayúsculas.
3. Si ninguna encaja de verdad, devuelve categoria vacía: publicar en la
   categoría equivocada no da error, el producto queda donde nadie lo busca.

SALIDA — solo JSON:
{{"categoria": "<nombre exacto o vacío>", "alternativa": "<segunda opción o vacío>",
  "confianza": 0.0, "motivo": "<una frase>"}}"""


def _match_etiqueta(texto: str, cats: dict[str, dict[str, Any]]) -> str | None:
    """El nombre que devolvió la IA → una categoría REAL, o None.

    LA GARANTÍA NO ES EL PROMPT: un nombre inventado o mal copiado no da error
    en Walmart — publica en el spec genérico. Se acepta el exacto y, si no, el
    mismo sin acentos ni mayúsculas; nada más difuso que eso."""
    import unicodedata

    def plano(t: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFD", (t or "").strip().lower())
                       if unicodedata.category(c) != "Mn")
    texto = (texto or "").strip()
    if not texto:
        return None
    if texto in cats:
        return texto
    por_plano = {plano(k): k for k in cats}
    return por_plano.get(plano(texto))


async def sugerir_categoria(sku: str, titulo: str, categorias_woo: str = "",
                            descripcion: str = "") -> dict[str, Any]:
    """
    Una categoría RECOMENDADA para el SKU — sugerencia, no se guarda.

    Devuelve la forma de Temu (`ok`, `sugerida`, `razon`, `ninguna`) para que el
    Estudio pinte las tres igual, más si la sugerida tiene exención.
    """
    import asyncio

    from services import ia_generadores

    titulo = (titulo or "").strip()
    if not titulo:
        return {"ok": False, "motivo": "Sin título con el cual clasificar."}
    catalogo = await asyncio.to_thread(catalogo_categorias)
    if not catalogo:
        return {"ok": False, "motivo": "No se pudo leer el catálogo de categorías "
                                       "de Walmart (channel.field_requirements)."}
    cats = {c["category_id"]: c for c in catalogo}

    # La categoría de ML es una ELECCIÓN HUMANA de la mayoría del catálogo
    # (5,536 con `source='panel'`): es la mejor pista que hay sobre qué es el
    # producto, y no cuesta una llamada.
    ml = ""
    try:
        from services import studio
        cm = await asyncio.to_thread(studio._categoria_mysql, sku)  # noqa: SLF001
        if cm:
            # `_armar_categoria` da `ruta` (a veces vacía) y `niveles`.
            ml = str(cm.get("ruta") or " > ".join(cm.get("niveles") or []) or "")
    except Exception as exc:  # noqa: BLE001
        log.info("walmart_panel.sugerir_categoria(%s): sin categoría de ML (%s)", sku, exc)

    lista = "\n".join(f"- {c['name']}" + (f" — {c['describe']}" if c.get("describe") else "")
                      for c in catalogo)
    prompt = _PROMPT_CAT.format(
        titulo=titulo[:200], cats=(categorias_woo or "—")[:200], ml=ml or "—",
        desc=(descripcion or "—")[:400], lista=lista)
    try:
        r = await asyncio.to_thread(
            ia_generadores._completar,  # noqa: SLF001
            "Devuelve SOLO JSON válido.", prompt, 500)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"La IA no contestó: {exc}"}
    if not r.get("ok"):
        return {"ok": False, "motivo": r.get("motivo") or "La IA no contestó."}
    d = ia_generadores._parse_json(r.get("texto", "")) or {}  # noqa: SLF001

    elegida = _match_etiqueta(str(d.get("categoria") or ""), cats)
    alterna = _match_etiqueta(str(d.get("alternativa") or ""), cats)
    if alterna == elegida:
        alterna = None
    try:
        confianza = float(d.get("confianza")) if d.get("confianza") is not None else None
    except (TypeError, ValueError):
        confianza = None

    def ficha(et: str | None) -> dict[str, Any] | None:
        if not et:
            return None
        c = cats[et]
        return {"category_id": et, "name": et, "path": et,
                "autorizada": bool(c.get("autorizada")), "estado": c.get("estado"),
                "folio": c.get("folio"), "describe": c.get("describe")}

    return {"ok": True, "sku": sku,
            "sugerida": ficha(elegida), "alternativa": ficha(alterna),
            "razon": d.get("motivo"), "confianza": confianza,
            "ninguna": elegida is None,
            "origen": f"IA ({r.get('proveedor') or r.get('modelo') or 'modelo'}) "
                      f"sobre las {len(cats)} categorías del feed",
            # Si la IA contestó un nombre que no existe, se dice — es la señal
            # de que el prompt o el catálogo necesitan atención.
            "descartada": (str(d.get("categoria") or "") if (d.get("categoria") and not elegida)
                           else None)}
