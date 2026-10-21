"""
ml_revision_categoria.py — La DOBLE VALIDACIÓN de la categoría antes de dar de alta en
Mercado Libre.

POR QUÉ EXISTE. Brandon, 21-oct-2026: «varias publicaciones de mercado libre tienen la
categoría incorrecta y nos lo acepta mercado libre… que antes de que se mande a
publicarse se haga un check visual de la categoría y de cuál podría pertenecer, así
como dándome opción de cambiar la categoría antes de publicar». ML NO rechaza una
categoría equivocada: si los atributos obligatorios se llenan, la acepta. JUGU-0085-PLA
se publicó en «Comidas Preparadas» (v0.675.0) y nada lo frenó.

QUÉ HACE
  · `revisar(categoria_id, titulo)`: la categoría que va a salir (ruta completa y si ML
    deja publicar ahí) contra las que el PREDICTOR de ML (`domain_discovery`) propone
    para ese título, con un veredicto:
        coincide        la elegida está entre las propuestas
        parecida        comparte el padre con alguna propuesta (categorías hermanas)
        distinta        ML pondría el producto en otra rama
        sin_sugerencia  el predictor no contestó o no propuso nada
        no_publicable   la elegida es una RAMA: ML la rechazaría
        sin_categoria   el producto no tiene categoría de ML (se enseñan las propuestas)
    El veredicto INFORMA; quien decide es la persona.
  · `categoria_de_alta(prod)`: la categoría con la que el pipeline vendorizado daría de
    alta ese `prod` — la regla de `publisher_core.build_payload` (meta, salvo que el
    mapeo de la categoría de Woo la pise), sin tocar `vendor/`.
  · `motivo_sin_confirmar(confirmada, real)`: motivo para NO crear, o None.

EL CANDADO. El modal de Publicar manda `categoria_confirmada` —la categoría que la
persona vio y marcó como revisada— y `publicar_ready.crear_ml` no da de alta si no es
la que va a salir. Así el check no depende de la pantalla: una petición sin
confirmación, o una categoría que cambió entre la vista previa y el envío, no publica.

No usa token: `/categories` y `domain_discovery` son públicos. Si ML llegara a pedir
autenticación, el predictor se queda sin propuestas (`sin_sugerencia`) y el check
sigue: la persona ve la categoría y confirma.

REGLA 11: `revisar` es `async` con httpx; `categoria_de_alta` es síncrona (ver su nota).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from config import settings

log = logging.getLogger("omnicanal.ml_revision_categoria")

API = "https://api.mercadolibre.com"
SUGERENCIAS = 3                     # cuántas propuestas del predictor se enseñan
_TTL_CAT_S = 6 * 3600               # el árbol de ML casi no cambia
_cache_cat: dict[str, tuple[float, dict[str, Any] | None]] = {}

ORIGENES = {
    "panel": "Elegida en el panel (Estudio o Costos)",
    "crear": "Propuesta automática al crear el producto",
    "woo": "Por la categoría de WooCommerce",
    "ninguna": "Sin categoría asignada",
}

MSG_SIN_CATEGORIA = (
    "El producto no tiene categoría de Mercado Libre: elígela en la vista previa "
    "o en el Estudio antes de publicar."
)
MSG_SIN_CONFIRMAR = (
    "Falta confirmar la categoría de Mercado Libre: abre otra vez la vista previa, "
    "revisa la categoría y marca «Revisé la categoría»."
)


async def _get(cli: httpx.AsyncClient, ruta: str, params: dict | None = None) -> Any:
    try:
        r = await cli.get(ruta, params=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("ML %s falló: %s", ruta, exc)
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return None


async def _categoria(cli: httpx.AsyncClient, cid: str) -> dict[str, Any] | None:
    """{category_id, name, ruta, niveles:[{id,name}], publicable} o None si ML no la conoce."""
    hit = _cache_cat.get(cid)
    if hit and time.monotonic() - hit[0] < _TTL_CAT_S:
        return hit[1]
    d = await _get(cli, f"/categories/{cid}")
    info = None
    if isinstance(d, dict) and d.get("id"):
        # ML trae nombres con espacios de sobra ("Deportes y Fitness ").
        niveles = [{"id": p.get("id", ""), "name": (p.get("name") or "").strip()}
                   for p in (d.get("path_from_root") or [])]
        info = {
            "category_id": cid,
            "name": (d.get("name") or "").strip() or (niveles[-1]["name"] if niveles else cid),
            "ruta": " > ".join(n["name"] for n in niveles),
            "niveles": niveles,
            "publicable": bool((d.get("settings") or {}).get("listing_allowed", True)),
        }
    if info is not None:  # un tropiezo de red no se recuerda: se vuelve a preguntar
        _cache_cat[cid] = (time.monotonic(), info)
    return info


def veredicto(actual: dict[str, Any] | None, sugeridas: list[dict[str, Any]]) -> str:
    """Pura (sin red): cómo se compara la categoría elegida con las propuestas de ML."""
    if actual is None:
        return "sin_categoria"
    if not actual.get("publicable", True):
        return "no_publicable"
    if not sugeridas:
        return "sin_sugerencia"
    ids = [s.get("category_id") for s in sugeridas]
    if actual["category_id"] in ids:
        return "coincide"
    padre = [n.get("id") for n in (actual.get("niveles") or [])][:-1]
    for s in sugeridas:
        otro = [n.get("id") for n in (s.get("niveles") or [])][:-1]
        if padre and otro == padre:
            return "parecida"
    return "distinta"


async def revisar(categoria_id: str | None, titulo: str | None) -> dict[str, Any]:
    """La revisión que pinta el modal. Nunca lanza: lo que no se pudo leer sale vacío."""
    cid = (categoria_id or "").strip()
    q = (titulo or "").strip()
    async with httpx.AsyncClient(base_url=API, timeout=12.0) as cli:
        actual_t = _categoria(cli, cid) if cid else asyncio.sleep(0, result=None)
        pred_t = (_get(cli, f"/sites/{settings.ml_site_id}/domain_discovery/search",
                       {"q": q[:120], "limit": SUGERENCIAS})
                  if q else asyncio.sleep(0, result=None))
        actual, pred = await asyncio.gather(actual_t, pred_t)
        propuestas: list[dict[str, Any]] = []
        vistos: set[str] = set()
        for p in (pred if isinstance(pred, list) else [])[:SUGERENCIAS]:
            pid = p.get("category_id")
            if pid and pid not in vistos:
                vistos.add(pid)
                propuestas.append(p)
        detalles = await asyncio.gather(*[_categoria(cli, p["category_id"]) for p in propuestas])
    sugeridas = []
    for p, det in zip(propuestas, detalles):
        det = det or {}
        sugeridas.append({
            "category_id": p["category_id"],
            "name": det.get("name") or p.get("category_name") or p["category_id"],
            "ruta": det.get("ruta") or p.get("category_name") or "",
            "niveles": det.get("niveles") or [],
            "dominio": p.get("domain_name") or "",
            "publicable": det.get("publicable", True),
        })
    if cid and actual is None:
        actual = {"category_id": cid, "name": cid, "ruta": "", "niveles": [],
                  "publicable": True, "ilegible": True}
    if (actual or {}).get("ilegible"):
        # Se sabe el ID pero no su ruta: no se puede comparar, solo verla a ojo.
        return {"actual": actual, "sugeridas": sugeridas, "veredicto": "sin_sugerencia"}
    return {"actual": actual, "sugeridas": sugeridas, "veredicto": veredicto(actual, sugeridas)}


def categoria_de_alta(prod: dict[str, Any]) -> str:
    """
    La categoría con la que `publisher_core.build_payload` daría de alta este `prod`.

    Es SU regla (publisher_core.py, «Resolver categoria ML usando la categoria WC»): la
    meta `ml_category_id`, salvo que el mapeo de la categoría de Woo diga `override`.
    `construir_prod` deja `wc_categories` vacío cuando hay categoría en la meta, así que
    el mapeo solo decide cuando no hay ninguna. Se llama a la MISMA función del vendor
    para no tener dos copias de la regla.

    SÍNCRONA: sin categoría en la meta, el vendor baja el mapeo de Woo por REST con
    `requests`. Desde una corrutina va en `asyncio.to_thread` (regla 11).
    """
    meta_id = str(prod.get("ml_category_id") or "").strip()
    try:
        from vendor.ml_ready.wc_category_mapping import resolve_ml_category_from_wc
        nuevo, motivo = resolve_ml_category_from_wc(prod.get("wc_categories") or [], meta_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("mapeo de categoría WC no disponible: %s", exc)
        nuevo, motivo = None, "error"
    if nuevo and motivo == "override":
        return str(nuevo)
    return meta_id


def origen(prod: dict[str, Any], categoria_id: str) -> str:
    """De dónde sale la categoría: panel (elección humana), crear (predictor), woo o ninguna."""
    if not categoria_id:
        return "ninguna"
    meta = prod.get("meta") or {}
    if categoria_id and str(meta.get("ml_categoria_id") or "").strip() == categoria_id:
        return "panel"
    if categoria_id and str(meta.get("ml_category_id") or "").strip() == categoria_id:
        return "crear"
    return "woo"


def motivo_sin_confirmar(confirmada: str | None, real: str | None) -> str | None:
    """Motivo para NO dar de alta, o None. Pura."""
    confirmada = (confirmada or "").strip().upper()
    real = (real or "").strip().upper()
    if not real:
        return MSG_SIN_CATEGORIA
    if not confirmada:
        return MSG_SIN_CONFIRMAR
    if confirmada != real:
        return (f"La categoría cambió desde la vista previa (revisaste {confirmada} y "
                f"saldría en {real}): abre otra vez la vista previa y "
                "confírmala.")
    return None
