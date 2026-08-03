"""
competencia_scraper.py — La ficha de la competencia, que la API de ML no da.

Por qué existe (sondeado contra la API con el token real de BEKURA):

  • `GET /items/{id}` de una publicación ajena → **403**. Ni título, ni precio,
    ni imagen, ni permalink. El multiget `GET /items?ids=` también, uno por uno.
  • `GET /sites/MLM/search` → **403** en las dos apps. O sea que la posición
    orgánica de búsqueda tampoco existe por API.
  • `sold_quantity` de un competidor: no hay endpoint.

Y raspar a pelo tampoco: ML sirve `suspicious-traffic-frontend` a IPs de
datacenter, y **ni el proxy residencial MX lo evita** — probado, la home carga
(515 KB) pero las páginas de artículo y de más-vendidos caen igual. ML corre
`security.js` + `snoopy-matt`, que exige ejecutar JS. Por eso esto va con actores
de Apify (navegador real), no con `requests`.

DOS ACTORES, DOS TRABAJOS
------------------------
1. `apify_ml_actor` (especializado en ML) → BÚSQUEDAS por término. Solo con
   `searchQueries`: `categoryUrls` y `startUrls` los acepta y los IGNORA en
   silencio, devolviendo su consulta por defecto. Le pasé la URL de
   `mas-vendidos/MLM162997` (tapetes) por las dos vías y terminó SUCCEEDED
   devolviendo **iPhone 15**. No falla, MIENTE.
2. `apify_navegador_actor` (Playwright genérico) → la página `/mas-vendidos/{cat}`.
   Los actores de ML no la parsean (uno FAILED, el otro 0 items) y este sí, porque
   ejecuta el `security.js`. Cobra por CÓMPUTO (~$0.007/página) y no por item:
   ~93× más barato que el de listings con detalle.

COSTO — el detalle no es gratis
-------------------------------
`includeProductDetail=false` → $0.003/item: título, precio, imagen, url, seller,
posición. `=true` → $0.025/item (8×) y agrega **descripcion** (la corta, derivada
de atributos) y **vendidos** (`soldQuantity`), que es la mejor señal de demanda
disponible de un competidor. Por eso el detalle se pide solo del top N.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from config import settings

log = logging.getLogger("omnicanal.competencia.scraper")

_APIFY = "https://api.apify.com/v2"
_ESPERA = 5
_MAX_SONDEOS = 84          # ~7 min
_sem = asyncio.Semaphore(2)
_URL_MAS_VENDIDOS = "https://www.mercadolibre.com.mx/mas-vendidos/"

# Tarifas del actor (PAY_PER_EVENT), para poder reportar el gasto de una corrida.
COSTO_ITEM = 0.003
COSTO_ITEM_DETALLE = 0.025


def disponible() -> bool:
    return bool(settings.apify_api_key)


def _proxy() -> dict[str, Any]:
    return {
        "useApifyProxy": True,
        "apifyProxyGroups": ["RESIDENTIAL"],
        "apifyProxyCountry": settings.apify_proxy_pais,
    }


async def _correr_actor(actor: str, payload: dict[str, Any],
                        limite_lectura: int = 200) -> list[dict[str, Any]]:
    """
    Corre un actor y devuelve las filas de su dataset. Devuelve [] ante cualquier
    fallo: un SKU que no raspa no debe tumbar la corrida mensual completa.
    """
    if not disponible():
        log.warning("APIFY_API_KEY no configurada; el scraping de competencia no corre")
        return []
    token = {"token": settings.apify_api_key}
    async with _sem:
        try:
            async with httpx.AsyncClient(timeout=120.0) as cli:
                r = await cli.post(f"{_APIFY}/acts/{actor}/runs",
                                   params={**token, "memory": 2048}, json=payload)
                if r.status_code >= 300:
                    log.warning("Apify %s no arrancó: %s %s", actor,
                                r.status_code, r.text[:200])
                    return []
                run_id = r.json()["data"]["id"]

                datos: dict[str, Any] = {}
                for _ in range(_MAX_SONDEOS):
                    await asyncio.sleep(_ESPERA)
                    rs = await cli.get(f"{_APIFY}/actor-runs/{run_id}", params=token)
                    datos = rs.json().get("data", {})
                    if datos.get("status") not in ("RUNNING", "READY"):
                        break

                if datos.get("status") != "SUCCEEDED":
                    log.warning("Apify %s terminó en %s", actor, datos.get("status"))
                    return []

                rd = await cli.get(
                    f"{_APIFY}/datasets/{datos['defaultDatasetId']}/items",
                    params={**token, "limit": limite_lectura},
                )
                filas = rd.json()
                return filas if isinstance(filas, list) else []
        except Exception as exc:  # noqa: BLE001
            log.warning("Apify %s falló: %s", actor, exc)
            return []


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _marca(it: dict[str, Any]) -> str | None:
    attrs = it.get("attributes") or {}
    if isinstance(attrs, dict):
        m = attrs.get("brand") or attrs.get("Marca")
        if m:
            # El actor a veces trae el campo Marca lleno de keyword-stuffing del
            # vendedor ("Malla Sombra Black/malla sombra/sombras para jardin/…").
            return str(m).split("/")[0].strip()[:120]
    return None


def _normalizar(it: dict[str, Any], posicion: int) -> dict[str, Any]:
    """Fila cruda del actor → fila de `competencia.resultados`."""
    vendedor = it.get("seller") or {}
    imgs = it.get("images") or []
    return {
        "externo_id": it.get("id") or it.get("itemId"),
        "posicion": posicion,
        "titulo": it.get("title"),
        # Descripción CORTA derivada de atributos ("Largo: 4 m | Ancho: 6 m").
        # ML no expone la descripción larga de publicaciones ajenas.
        "descripcion": it.get("description"),
        "precio": _num(it.get("price")),
        "moneda": it.get("currency") or "MXN",
        "imagen": it.get("thumbnailUrl") or (imgs[0] if imgs else None),
        "url": it.get("permalink") or it.get("url"),
        "seller": vendedor.get("nickname") or vendedor.get("storeName"),
        "marca": _marca(it),
        # El actor NO devuelve categoría (categoryId viene null): la pone el
        # orquestador desde nuestra taxonomía.
        "categoria_id": None,
        "categoria_nombre": None,
        "vendidos": it.get("soldQuantity"),
        "reviews": it.get("reviewCount"),
        "rating": _num(it.get("ratingAverage")),
        "envio_gratis": it.get("freeShipping"),
        "es_full": it.get("fullShipping"),
        "catalog_product_id": it.get("catalogProductId"),
    }


async def buscar(termino: str, limite: int = 30,
                 con_detalle: bool = True) -> list[dict[str, Any]]:
    """
    Resultados de búsqueda de ML para un término, **en orden**: el índice ES la
    posición orgánica, el único lugar de donde se puede sacar ese dato.

    `con_detalle=True` agrega descripción y vendidos, a 8× el costo por item.
    """
    filas = await _correr_actor(settings.apify_ml_actor, {
        "siteId": settings.ml_site_id,
        "searchQueries": [termino],
        "maxItems": limite,
        "maxPagesPerQuery": max(1, limite // 50 + 1),
        "sort": "relevance",
        "includeProductDetail": con_detalle,
        "proxyConfiguration": _proxy(),
    }, limite_lectura=limite)

    out = []
    for i, it in enumerate(filas[:limite], start=1):
        fila = _normalizar(it, i)
        if fila["externo_id"]:
            out.append(fila)
    log.info("buscar(%r, detalle=%s) → %s resultados", termino, con_detalle, len(out))
    return out


# ── Más vendidos por categoría (página /mas-vendidos/{cat}) ─────────────────
#
# HALLAZGO que cambió el diseño: los actores ESPECIALIZADOS en ML no sirven para
# esta página (uno terminó FAILED, el otro devolvió 0 items, y el de listings
# ignora `categoryUrls` y responde con su consulta por defecto). Un NAVEGADOR
# genérico sí pasa, porque ejecuta el `security.js` de ML.
#
# Y sale ~93× más barato: `apify/playwright-scraper` cobra por CÓMPUTO
# (~$0.007/página) y no por item como los de ML ($0.625 por búsqueda con detalle).
#
# El bloqueo es INTERMITENTE: la misma URL pasa con una IP residencial y cae en el
# interstitial con otra. Por eso la pageFunction LANZA al detectarlo — así Apify
# reintenta la request con otra sesión de proxy, que es el único remedio real.

_PAGE_FUNCTION_MAS_VENDIDOS = r"""
async function pageFunction(context) {
  const { page, request } = context;
  await page.waitForTimeout(4000);
  let html = await page.content();
  const malo = (h) => h.includes('suspicious-traffic') || h.includes('account-verification');
  if (malo(html)) {
    await page.waitForTimeout(2500);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(4000);
    html = await page.content();
  }
  if (malo(html)) { throw new Error('BLOQUEADO: interstitial de trafico sospechoso'); }
  await page.waitForSelector('div.poly-card', { timeout: 15000 });
  const items = await page.evaluate(() => {
    const vistos = new Set();
    const out = [];
    document.querySelectorAll('div.poly-card').forEach((el) => {
      const a = el.querySelector('a.poly-component__title, a[href*="/up/"], a[href*="/p/"]');
      if (!a) return;
      const limpio = (a.href || '').split('#')[0];
      if (vistos.has(limpio)) return;
      vistos.add(limpio);
      const t = (s) => { const n = el.querySelector(s); return n ? n.textContent.trim() : null; };
      const frac = (s) => {
        const n = el.querySelector(s + ' .andes-money-amount__fraction');
        return n ? n.textContent.replace(/[^0-9]/g, '') : null;
      };
      out.push({
        url: limpio,
        wid: ((a.href || '').match(/[?&#]wid=(MLM\d+)/) || [])[1] || null,
        id_pagina: (limpio.match(/\/(?:up|p)\/(MLMU?\d+)/) || [])[1] || null,
        titulo: t('.poly-component__title'),
        badge: t('.poly-component__highlight'),
        precio: frac('.poly-price__current'),
        precio_lista: frac('.poly-price__previous'),
        descuento: t('.poly-price__disc-label'),
        seller: t('.poly-component__seller'),
        accesible: t('.poly-component__review-compacted .andes-visually-hidden')
                   || t('.andes-visually-hidden'),
        etiquetas: [...el.querySelectorAll('.polylabel-label')].map((n) => n.textContent.trim()),
        imagen: el.querySelector('img') ? el.querySelector('img').src : null,
      });
    });
    return out;
  });
  return { url: request.url, n: items.length, items };
}
"""

# "+50mil vendidos" (sin espacio), "+1 mil vendidos", "+500 vendidos".
_RE_VENDIDOS = re.compile(r"([\d.,]+)\s*(mil)?", re.IGNORECASE)
_RE_BADGE = re.compile(r"(\d+)")   # "1º MÁS VENDIDO" → 1


def _entero(txt: Any) -> int | None:
    """'1234' o '$1,234' → 1234."""
    if txt in (None, ""):
        return None
    d = re.sub(r"[^0-9]", "", str(txt))
    return int(d) if d else None


def _vendidos(txt: str | None) -> int | None:
    """
    '+500 vendidos' → 500 · '+1 mil vendidos' → 1000 · '2.5 mil' → 2500.
    ML redondea: son cotas inferiores, no cifras exactas.
    """
    if not txt:
        return None
    t = str(txt).lower().replace("+", "").strip()
    m = _RE_VENDIDOS.search(t)
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if m.group(2):
        n *= 1000
    return int(n)


def _score(txt: str | None) -> float | None:
    """'4.8' o 'Calificación 4.8 de 5 estrellas…' → 4.8. Descarta lo que no sea 0-5."""
    if not txt:
        return None
    m = re.search(r"([\d]+[.,][\d]+|[\d]+)", str(txt))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return v if 0 < v <= 5 else None


def _de_accesible(txt: str | None) -> tuple[float | None, int | None]:
    """
    'Calificación 4.8 de 5 estrellas. Más de 50mil productos vendidos' → (4.8, 50000).

    Es la fuente más limpia de la tarjeta: trae score y vendidos juntos y sin
    ambigüedad. Las etiquetas visibles son '4.8' y '| +50mil vendidos' por
    separado, y distinguir cuál es cuál por posición es frágil.
    """
    if not txt:
        return None, None
    t = str(txt)
    calif = re.search(r"calificaci[oó]n\s+([\d.,]+)", t, re.IGNORECASE)
    score = _score(calif.group(1)) if calif else None
    vend = re.search(r"([\d.,]+\s*mil|[\d.,]+)\s*producto", t, re.IGNORECASE)
    return score, (_vendidos(vend.group(1)) if vend else None)


def _del_badge(txt: str | None) -> int | None:
    """'1º MÁS VENDIDO' → 1. Es el ranking OFICIAL que publica ML en la tarjeta."""
    if not txt:
        return None
    m = _RE_BADGE.search(str(txt))
    return int(m.group(1)) if m else None


async def mas_vendidos_categorias(categorias: list[str],
                                  limite: int = 10) -> dict[str, list[dict[str, Any]]]:
    """
    Top de más vendidos de cada categoría, raspado de `/mas-vendidos/{cat}`.
    → { categoria_id: [filas] }

    Trae ranking, título, score, vendidos, precio base y precio con descuento.
    Las VISITAS quedan en None a propósito: son otra llamada por publicación a la
    API y este ranking es informativo, no la comparación uno-a-uno.
    """
    cats = [c for c in dict.fromkeys(categorias) if c]
    if not cats or not disponible():
        return {}
    filas = await _correr_actor(settings.apify_navegador_actor, {
        "startUrls": [{"url": f"{_URL_MAS_VENDIDOS}{c}"} for c in cats],
        "pageFunction": _PAGE_FUNCTION_MAS_VENDIDOS,
        "proxyConfiguration": _proxy(),
        "maxRequestsPerCrawl": len(cats),
        # MEDIDO: las 4 categorías de la prueba salieron 4/4 pero TODAS gastaron
        # los 6 reintentos, o sea que la tasa por intento ronda el 14%. Con 6 se
        # quedaba al filo: 12 da margen y casi no encarece, porque solo se paga el
        # cómputo de los intentos que ocurren y los bloqueados abortan en segundos.
        "maxRequestRetries": 12,
        "maxConcurrency": 2,
        "headless": True,
        "launcher": "chromium",
    }, limite_lectura=len(cats))

    out: dict[str, list[dict[str, Any]]] = {}
    for pagina in filas:
        cat = str(pagina.get("url", "")).rsplit("/", 1)[-1]
        normalizadas = []
        for i, it in enumerate((pagina.get("items") or [])[:limite], start=1):
            ident = it.get("wid") or it.get("id_pagina")
            if not ident:
                continue
            score, vendidos = _de_accesible(it.get("accesible"))
            # Respaldo si cambia el texto accesible: las etiquetas visibles.
            for et in it.get("etiquetas") or []:
                if "vendido" in et.lower():
                    vendidos = vendidos if vendidos is not None else _vendidos(et.replace("|", ""))
                elif score is None:
                    score = _score(et)
            normalizadas.append({
                "externo_id": ident,
                # El badge oficial manda; el orden del DOM es el respaldo.
                "posicion": _del_badge(it.get("badge")) or i,
                "titulo": it.get("titulo"),
                "precio": _entero(it.get("precio")),
                "precio_lista": _entero(it.get("precio_lista")),
                "descuento": it.get("descuento"),
                "vendidos": vendidos,
                "rating": score,
                "seller": it.get("seller"),
                "imagen": it.get("imagen"),
                "url": it.get("url"),
                "visitas_30d": None,   # se deja vacía: es otra llamada por item
            })
        if normalizadas:
            out[cat] = normalizadas
    faltan = [c for c in cats if c not in out]
    if faltan:
        log.warning("mas_vendidos_categorias: sin datos para %s (bloqueo intermitente)",
                    faltan)
    return out


def costo_estimado(busquedas: int, items_por_busqueda: int,
                   con_detalle: bool = True) -> float:
    """Gasto estimado de Apify en USD, para reportarlo antes y después de correr."""
    por_item = COSTO_ITEM_DETALLE if con_detalle else COSTO_ITEM
    return round(busquedas * items_por_busqueda * por_item, 3)
