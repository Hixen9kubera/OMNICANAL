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
import urllib.parse
import re
from typing import Any

import httpx

from config import settings

log = logging.getLogger("omnicanal.competencia.scraper")

_APIFY = "https://api.apify.com/v2"
_ESPERA = 5
# MEDIDO el 4-ago: una tanda de 20 URLs del navegador genérico tarda ~12 min y con
# el tope viejo de 7 min el sondeo se rendía ANTES de que la corrida terminara —
# leía el dataset a medias y reportaba "sin resultados" habiendo pagado el cómputo.
# 240 sondeos son 20 minutos.
_MAX_SONDEOS = 240
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
                        limite_lectura: int = 200,
                        respaldo: str | None = None) -> list[dict[str, Any]]:
    """
    Corre un actor y devuelve las filas de su dataset. Devuelve [] ante cualquier
    fallo: un SKU que no raspa no debe tumbar la corrida mensual completa.

    `respaldo` es otro actor con el MISMO contrato de entrada. Se usa cuando el
    primero no trae nada, que no siempre es un error visible: el 13-ago dos
    términos de Herramientas terminaron en corridas `SUCCEEDED` con "Crawled 0/2
    pages" — el actor reporta éxito y el dataset viene vacío. Un fallo así no se
    distingue de "la página no tiene resultados" sin intentar por otro lado.
    """
    if not disponible():
        log.warning("APIFY_API_KEY no configurada; el scraping de competencia no corre")
        return []
    token = {"token": settings.apify_api_key}
    filas: list[dict[str, Any]] = []
    async with _sem:
        try:
            async with httpx.AsyncClient(timeout=120.0) as cli:
                r = await cli.post(f"{_APIFY}/acts/{actor}/runs",
                                   params={**token, "memory": 2048}, json=payload)
                if r.status_code >= 300:
                    log.warning("Apify %s no arrancó: %s %s", actor,
                                r.status_code, r.text[:200])
                    raise RuntimeError(f"no arrancó: {r.status_code}")
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
                    raise RuntimeError(f"terminó en {datos.get('status')}")

                rd = await cli.get(
                    f"{_APIFY}/datasets/{datos['defaultDatasetId']}/items",
                    params={**token, "limit": limite_lectura},
                )
                leidas = rd.json()
                filas = leidas if isinstance(leidas, list) else []
        except Exception as exc:  # noqa: BLE001
            log.warning("Apify %s falló: %s", actor, exc or type(exc).__name__)
            filas = []

    return filas


async def _con_respaldo(payload: dict[str, Any], limite_lectura: int,
                        util) -> list[dict[str, Any]]:
    """
    Corre el actor principal y, si lo que trajo NO SIRVE, reintenta con el otro.

    `util(filas) -> bool` lo decide quien llama, porque "vacío" no es lo mismo
    que "inútil": una corrida puede terminar SUCCEEDED y devolver una entrada por
    URL con `items: []` — el dataset no está vacío pero no hay nada que guardar.
    Pasó con dos términos de Herramientas, dos veces seguidas.
    """
    filas = await _correr_actor(settings.apify_navegador_actor, payload, limite_lectura)
    if util(filas):
        return filas
    respaldo = settings.apify_navegador_respaldo
    if not respaldo or respaldo == settings.apify_navegador_actor:
        return filas
    log.warning("El actor principal no trajo nada aprovechable; "
                "reintento con el respaldo %s", respaldo)
    otras = await _correr_actor(respaldo, payload, limite_lectura)
    return otras if util(otras) else filas


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
        # ANUNCIO. El actor lo marca con `isPromoted` y su permalink va por el
        # redirector click1. No se cuenta como posición orgánica.
        "es_anuncio": bool(it.get("isPromoted")),
    }


async def buscar_terminos(terminos: list[str], limite: int = 10,
                          ) -> dict[str, list[dict[str, Any]]]:
    """
    Búsqueda de varios términos con el navegador GENÉRICO. → { termino: [filas] }

    ES EL CAMINO BUENO, y sustituye al actor de ML para búsquedas:

      • COSTO: cobra por CÓMPUTO (~$0.007/página) en vez de $0.09 por corrida. Los
        230 términos de las dos categorías pasan de ~$24 a ~$1.61.
      • ATRIBUCIÓN: cada página ES una consulta, así que se sabe de qué término
        vino cada resultado. El actor de ML no lo dice — medido con 5 consultas,
        devuelve todo intercalado y no hay forma de repartirlo.
      • VOLUMEN: la página trae ~48 orgánicos por término, no 5.

    Y corre desde la infraestructura de Apify con proxy residencial, así que el
    muro de login que ML levanta contra nuestra IP no aplica.
    """
    consultas = [t.strip() for t in dict.fromkeys(terminos) if t and t.strip()]
    if not consultas:
        return {}
    # El término va en la URL, y de ahí se recupera para atribuir el resultado.
    urls, de_url = [], {}
    for q in consultas:
        slug = urllib.parse.quote(q.replace(" ", "-"))
        u = f"https://listado.mercadolibre.com.mx/{slug}"
        urls.append({"url": u})
        de_url[u.rstrip("/")] = q

    paginas = await _con_respaldo({
        "startUrls": urls,
        "pageFunction": _PAGE_FUNCTION_BUSCADOR,
        "proxyConfiguration": _proxy(),
        "maxRequestsPerCrawl": len(urls),
        # Igual que en el ranking: ML bloquea de forma intermitente y cada intento
        # fallido aborta en segundos, así que reintentar sale casi gratis.
        "maxRequestRetries": 8,
        "maxConcurrency": 2,
        "headless": True,
        "launcher": "chromium",
    }, limite_lectura=len(urls),
        util=lambda fs: any((p.get("items") or []) for p in fs))

    out: dict[str, list[dict[str, Any]]] = {}
    for pag in paginas:
        q = de_url.get((pag.get("url") or "").rstrip("/"))
        if not q:
            # Respaldo: reconstruir el término desde el slug del URL.
            q = urllib.parse.unquote(
                (pag.get("url") or "").rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")
        filas = []
        for it in (pag.get("items") or [])[:limite]:
            f = _de_tarjeta(it)
            if f["externo_id"]:
                filas.append(f)
        if filas:
            out[q] = filas
    faltan = [q for q in consultas if q not in out]
    if faltan:
        log.warning("buscar_terminos: sin resultados para %s: %s",
                    len(faltan), faltan[:5])
    log.info("buscar_terminos: %s consultas → %s con resultados",
             len(consultas), len(out))
    return out


def _de_tarjeta(it: dict[str, Any]) -> dict[str, Any]:
    """Item de la pageFunction del buscador → fila de `busquedas`."""
    url = (it.get("url") or "").split("#")[0]
    m = re.search(r"/(?:up|p)/(MLMU?\d+)|/(MLM)-(\d{9,12})-", url)
    ident = (m.group(1) or f"{m.group(2)}{m.group(3)}") if m else None
    # `vendidos` y el rating viven en las etiquetas visibles de la tarjeta.
    vendidos = rating = None
    for et in it.get("etiquetas") or []:
        if "vendido" in et.lower():
            vendidos = _vendidos(et)
        elif rating is None:
            rating = _score(et)
    envio = (it.get("envio") or "").lower()
    return {
        "externo_id": ident,
        "posicion": it.get("posicion"),
        "titulo": it.get("titulo"),
        "precio": _entero(it.get("precio")),
        "precio_lista": _entero(it.get("precio_lista")),
        "descuento": it.get("descuento"),
        "vendidos": vendidos,
        "rating": rating,
        "seller": it.get("seller"),
        "imagen": it.get("imagen"),
        "url": url or None,
        "envio_gratis": 1 if "gratis" in envio else 0,
        "es_full": 1 if "full" in envio else None,
        "catalog_id": None,
    }


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

# Buscador. Se raspa con el navegador GENÉRICO y no con el actor de ML porque el
# de ML cobra $0.09 por CORRIDA y no etiqueta de qué consulta viene cada item
# (medido: con 5 consultas devuelve todo intercalado, así que agrupar no sirve).
# El genérico cobra por CÓMPUTO, ~$0.007 por página, y cada página ES una consulta,
# con lo que la atribución es trivial: una URL, un término.
_PAGE_FUNCTION_BUSCADOR = r"""
async function pageFunction(context) {
  const { page, request } = context;
  // `page.waitForTimeout` solo existe en Playwright. Con esto la MISMA
  // pageFunction corre en el actor de respaldo (Puppeteer) sin tocarla.
  const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
  await dormir(3500);
  let html = await page.content();
  const malo = (h) => h.includes('suspicious-traffic') || h.includes('account-verification')
                   || h.includes('not-found-page') || h.includes('Para continuar, ingresa a tu cuenta');
  if (malo(html)) {
    await dormir(2500);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await dormir(3500);
    html = await page.content();
  }
  if (malo(html)) { throw new Error('BLOQUEADO'); }
  await page.waitForSelector('div.poly-card', { timeout: 15000 });
  const items = await page.evaluate(() => {
    const out = [];
    let organica = 0;
    document.querySelectorAll('div.poly-card').forEach((el) => {
      // ANUNCIO: no ocupa posición orgánica y se descarta.
      const ad = el.querySelector('.poly-component__ads-promotions');
      if (ad && /ad/i.test(ad.textContent || '')) return;
      const a = el.querySelector('a.poly-component__title, a[href*="mercadolibre"]');
      if (!a) return;
      organica += 1;
      const t = (s) => { const n = el.querySelector(s); return n ? n.textContent.trim() : null; };
      const frac = (s) => {
        const n = el.querySelector(s + ' .andes-money-amount__fraction');
        return n ? n.textContent.replace(/[^0-9]/g, '') : null;
      };
      const img = el.querySelector('img');
      out.push({
        posicion: organica,
        url: (a.href || '').split('#')[0],
        titulo: t('.poly-component__title'),
        precio: frac('.poly-price__current'),
        precio_lista: frac('.poly-price__previous'),
        descuento: t('.poly-price__disc-label'),
        seller: t('.poly-component__seller'),
        imagen: img ? (img.src || img.getAttribute('data-src')) : null,
        etiquetas: Array.from(el.querySelectorAll('.polylabel-label')).map(x => x.textContent.trim()),
        envio: t('.poly-component__shipping'),
      });
    });
    return out;
  });
  return { url: request.url, items };
}
"""

_PAGE_FUNCTION_MAS_VENDIDOS = r"""
async function pageFunction(context) {
  const { page, request } = context;
  // `page.waitForTimeout` solo existe en Playwright. Con esto la MISMA
  // pageFunction corre en el actor de respaldo (Puppeteer) sin tocarla.
  const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
  await dormir(4000);
  let html = await page.content();
  const malo = (h) => h.includes('suspicious-traffic') || h.includes('account-verification');
  if (malo(html)) {
    await dormir(2500);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await dormir(4000);
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


# El id de la RUTA del URL, que no es el mismo que el `wid`. El `wid` es el item
# real (sirve para /visits); éste es el que `/products/{id}/items` necesita para
# resolver a qué SUBCATEGORÍA pertenece la fila, o sea los NICHOS. Sin él, un
# ranking de raíz capturado por Apify se queda sin nichos — que es exactamente el
# hueco medido en producción (37 de 3,000 filas con item_categoria_id, y eran
# justo las que se habían capturado con el navegador local).
_RE_PAGINA_RANK = re.compile(r"/(?:up|p)/(MLMU?\d+)|/(MLM)-(\d{9,12})-")


def _pagina_y_tipo(url: str | None) -> tuple[str | None, str | None]:
    """`(id_pagina, tipo)` a partir del href de la tarjeta."""
    u = url or ""
    m = _RE_PAGINA_RANK.search(u)
    if not m:
        return None, None
    if m.group(1):
        ident = m.group(1)
        return ident, "USER_PRODUCT" if ident.startswith("MLMU") else "PRODUCT"
    return f"{m.group(2)}{m.group(3)}", "ITEM"


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
    filas = await _con_respaldo({
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
    }, limite_lectura=len(cats),
        util=lambda fs: any((p.get('items') or []) for p in fs))

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
            id_pagina, tipo = _pagina_y_tipo(it.get("url"))
            normalizadas.append({
                "externo_id": ident,
                # El id de la RUTA y el tipo: los necesita
                # `_subcategoria_de_cada_fila` para resolver los nichos. Antes
                # quedaban en NULL y por eso se creía que Apify no servía para
                # capturar una categoría RAÍZ.
                "id_pagina": id_pagina,
                "tipo": tipo,
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
