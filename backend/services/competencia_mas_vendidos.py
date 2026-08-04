"""
competencia_mas_vendidos.py — Lee `/mas-vendidos/{categoria}` con Selenium + BeautifulSoup.

POR QUÉ UN NAVEGADOR Y NO `requests` + BS4
------------------------------------------
BeautifulSoup sola NO sirve para esta página. Medido: `requests` recibe 23 KB del
interstitial `suspicious-traffic-frontend` en lugar del contenido, porque ML corre
`security.js` + `snoopy-matt` y las tarjetas las pinta JavaScript. Ni con el proxy
residencial mexicano cambia — el problema no es la IP, es que nadie ejecuta el JS.

Selenium con el Chrome local SÍ pasa: probado contra MLM1747 y MLM162997, ambas
`bloqueado=False`, 15 y 18 tarjetas, todos los campos. Y sale gratis, sin actores
de terceros y sin el bloqueo intermitente que tenía la vía Apify (donde cada
página gastaba ~6 reintentos).

BeautifulSoup se sigue usando, pero para lo que sirve: parsear el HTML **ya
renderizado** que entrega el navegador.

DÓNDE CORRE
-----------
Necesita un Chrome instalado. En local está; en Railway NO viene por defecto, así
que si esto se despliega hay que agregarlo al build o dejar el módulo para
correrlo desde una máquina con navegador (como el cron mensual desde local).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

log = logging.getLogger("omnicanal.competencia.mas_vendidos")

URL_BASE = "https://www.mercadolibre.com.mx/mas-vendidos/"

# Se ESPERA a que aparezcan las tarjetas en vez de dormir un rato fijo: con
# `sleep(6)` una de dos categorías salía vacía porque la página aún no había
# pintado. El sleep fijo apuesta, la espera explícita observa.
_ESPERA_MAX = 25        # segundos máximos esperando `div.poly-card`
_MAX_INTENTOS = 3       # por categoría

# MEDIDO: 2 categorías seguidas pasan; 8 seguidas solo dejaron pasar 2. No es
# azar, es RATE LIMITING por IP. La cura no es reintentar más rápido sino ir más
# despacio, así que hay una pausa entre categorías y un castigo mayor tras un
# bloqueo. Un ranking mensual de ~200 categorías con 8s de pausa son ~27 min:
# perfectamente aceptable para un cron que corre una vez al mes.
_PAUSA_ENTRE = 8.0      # segundos entre categorías
_PAUSA_BLOQUEO = 25.0   # espera tras topar con el interstitial

# "+50mil vendidos" (sin espacio), "+1 mil vendidos", "+500 vendidos"
_RE_VENDIDOS = re.compile(r"([\d.,]+)\s*(mil)?", re.IGNORECASE)
_RE_BADGE = re.compile(r"(\d+)")            # "1º MÁS VENDIDO" → 1
_RE_ITEM = re.compile(r"[?&#]wid=(MLM\d+)")
# Tres formas de URL, una por tipo de entrada de /highlights: /up/ (USER_PRODUCT),
# /p/ (PRODUCT) y articulo…/MLM-1234…-_JM (ITEM). La tercera faltaba y las tarjetas
# de tipo ITEM se caían en silencio: MLM1747 devolvía 7 de 8 filas sin avisar.
_RE_PAGINA = re.compile(r"/(?:up|p)/(MLMU?\d+)|/(MLM)-(\d{9,12})-")
# El slug del permalink ES el título, en kebab-case y sin acentos. Es el respaldo
# cuando la tarjeta no trae el título en el DOM.
_RE_SLUG = re.compile(r"\.com\.mx/([a-z0-9-]{10,})/(?:up|p)/|/MLM-\d{9,12}-([a-z0-9-]{10,})-_JM")


def disponible() -> bool:
    """¿Están selenium y bs4 instalados?"""
    try:
        import bs4  # noqa: F401
        import selenium  # noqa: F401
        return True
    except ImportError:
        return False


# ── Parsers de los formatos reales de la tarjeta ────────────────────────────

def _entero(txt: Any) -> int | None:
    """'$1,777' → 1777."""
    if txt in (None, ""):
        return None
    d = re.sub(r"[^0-9]", "", str(txt))
    return int(d) if d else None


def _vendidos(txt: str | None) -> int | None:
    """
    '+50mil vendidos' → 50000 · '+1 mil' → 1000 · '+500' → 500.
    Son COTAS INFERIORES: ML redondea, no publica la cifra exacta.
    """
    if not txt:
        return None
    m = _RE_VENDIDOS.search(str(txt).lower().replace("+", "").replace("|", "").strip())
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return int(n * 1000) if m.group(2) else int(n)


def _score(txt: str | None) -> float | None:
    """'4.8' → 4.8. Descarta lo que no caiga en 0-5 para no colar un precio."""
    if not txt:
        return None
    m = re.search(r"(\d+[.,]\d+|\d+)", str(txt))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return v if 0 < v <= 5 else None


def _badge(txt: str | None) -> int | None:
    """'1º MÁS VENDIDO' → 1. Es el ranking OFICIAL que publica ML en la tarjeta."""
    if not txt:
        return None
    m = _RE_BADGE.search(str(txt))
    return int(m.group(1)) if m else None


def _score_y_vendidos(tarjeta) -> tuple[float | None, int | None]:
    """
    Score y unidades vendidas de una tarjeta.

    Primero el texto de accesibilidad ('Calificación 4.8 de 5 estrellas. Más de
    50mil productos vendidos'), que los trae juntos y etiquetados. Con el Chrome
    local ese nodo suele venir vacío, así que el camino real son las etiquetas
    visibles: `['4.8', '| +50mil vendidos']`. Se distinguen por CONTENIDO —
    cuál dice "vendidos" y cuál parece calificación — y no por posición, que es
    frágil ante cualquier cambio de maquetado.
    """
    acc = tarjeta.select_one(".poly-component__review-compacted .andes-visually-hidden")
    if acc:
        t = acc.get_text(" ", strip=True)
        calif = re.search(r"calificaci[oó]n\s+([\d.,]+)", t, re.IGNORECASE)
        vend = re.search(r"([\d.,]+\s*mil|[\d.,]+)\s*producto", t, re.IGNORECASE)
        s = _score(calif.group(1)) if calif else None
        v = _vendidos(vend.group(1)) if vend else None
        if s is not None or v is not None:
            return s, v

    score = vendidos = None
    for et in tarjeta.select(".polylabel-label"):
        txt = et.get_text(strip=True)
        if "vendido" in txt.lower():
            vendidos = _vendidos(txt)
        elif score is None:
            score = _score(txt)
    return score, vendidos


def _fila(tarjeta, posicion_dom: int) -> dict[str, Any] | None:
    """Una tarjeta de la página → una fila del ranking."""
    a = (tarjeta.select_one("a.poly-component__title")
         or tarjeta.select_one("a[href*='mercadolibre.com.mx']"))
    if not a:
        return None
    href = a.get("href") or ""
    limpio = href.split("#")[0]

    def txt(sel: str) -> str | None:
        n = tarjeta.select_one(sel)
        return n.get_text(strip=True) if n else None

    mi = _RE_ITEM.search(href)
    mp = _RE_PAGINA.search(limpio)
    # `id_pagina` es el id que aparece en el URL, y es EXACTAMENTE el que devuelve
    # /highlights: por ahí se unen la ficha raspada y la posición oficial.
    id_pagina = (mp.group(1) or f"{mp.group(2)}{mp.group(3)}") if mp else None
    # El `wid` del fragmento es el item_id REAL de la publicación mostrada; con él
    # se pueden pedir las visitas por API. El id de la página (`/up/MLMU…`) es del
    # producto del vendedor y no sirve para /visits: hay que resolverlo con
    # /products/{id}/items (ver competencia_ml.resolver_highlight).
    externo = mi.group(1) if mi else id_pagina
    if not externo:
        return None

    score, vendidos = _score_y_vendidos(tarjeta)
    img = tarjeta.select_one("img")
    # El título del DOM manda; el slug es el respaldo (viene sin acentos ni
    # mayúsculas, pero es el título real que ML publicó, no una aproximación).
    titulo = txt(".poly-component__title")
    if not titulo:
        ms = _RE_SLUG.search(limpio)
        if ms:
            titulo = (ms.group(1) or ms.group(2)).replace("-", " ").capitalize()
    return {
        "externo_id": externo,
        "id_pagina": id_pagina,
        # El badge oficial manda; el orden del DOM es respaldo.
        "posicion": _badge(txt(".poly-component__highlight")) or posicion_dom,
        "titulo": titulo,
        "precio": _entero(txt(".poly-price__current .andes-money-amount__fraction")),
        "precio_lista": _entero(txt(".poly-price__previous .andes-money-amount__fraction")),
        "descuento": txt(".poly-price__disc-label"),
        "vendidos": vendidos,
        "rating": score,
        "seller": txt(".poly-component__seller"),
        "imagen": (img.get("src") or img.get("data-src")) if img else None,
        "url": limpio,
        "visitas_30d": None,   # se deja vacía: sería otra llamada por publicación
    }


# ── El navegador ─────────────────────────────────────────────────────────────

def _navegador(visible: bool = False):
    """
    Chrome con las banderas mínimas para no delatarse. Sin ellas ML detecta la
    automatización de inmediato: `navigator.webdriver` y el switch
    `enable-automation` son las delaciones más comunes.

    NO CORRE HEADLESS (medido el 4-ago)
    -----------------------------------
    ML detecta `--headless=new` y le sirve un 404 a TODO, incluida su propia home:
    2,581 bytes, sin <title>, con `ui-empty-state not-found-page`. La MISMA máquina,
    la MISMA red y el MISMO minuto, con ventana visible, devuelven 499 KB y las 20
    tarjetas. No era la IP, ni el rate limiting, ni la categoría, ni la sesión: se
    probó también con el perfil de Chrome de una cuenta con sesión iniciada y no
    hacía falta — basta con no ser headless.

    Por eso la ventana es VISIBLE por defecto. `COMPETENCIA_HEADLESS=1` la vuelve a
    ocultar, para cuando ML afloje o para correr en un servidor con Xvfb.
    """
    import os

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    op = Options()
    if not visible and os.environ.get("COMPETENCIA_HEADLESS") == "1":
        op.add_argument("--headless=new")
    op.add_argument("--window-size=1440,2400")
    op.add_argument("--lang=es-MX")
    op.add_argument("--disable-blink-features=AutomationControlled")
    op.add_experimental_option("excludeSwitches", ["enable-automation"])
    op.add_experimental_option("useAutomationExtension", False)
    d = webdriver.Chrome(options=op)
    d.set_page_load_timeout(60)
    d.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return d


def _bloqueado(html: str, url: str = "") -> bool:
    """
    ¿ML nos frenó? Son CUATRO formas distintas, y hay que reconocer las cuatro o el
    módulo miente:

      1. `suspicious-traffic-frontend` — el interstitial que sirve a IPs de datacenter.
      2. `account-verification`        — el que pide verificar cuenta.
      3. **`/captcha/wall`**          — MURO DE CAPTCHA. Es el que aparece de verdad
         con Selenium tras 1-2 páginas: redirige a
         `mercadolibre.com.mx/captcha/wall?go_url=…`, título "Seguridad — Mercado
         Libre". No trae `suspicious-traffic` ni `account-verification`, así que
         antes se colaba como "página sin tarjetas" y el módulo lo reportaba como
         "ML no publica ranking de esta categoría" — un falso negativo.

      4. **PÁGINA 404 de ML** (`ui-empty-state not-found-page`, ~2.5 KB, sin
         `<title>`). Es una NEGACIÓN disfrazada: cuando ML marca al cliente le
         sirve un 404 a TODO, incluida la home — comprobado el 4-ago, con la home
         devolviendo exactamente el mismo cuerpo de 2,581 bytes. Reconocerlo
         importa porque una categoría que de verdad no tiene ranking devuelve una
         página NORMAL con 0 tarjetas (así se comportan Bujías y Cartuchos de
         Turbo). Confundirlas hace que el módulo diga "ML no publica ranking de
         esta categoría" cuando la verdad es "a nosotros no nos deja entrar", y
         son acciones opuestas: una es no reintentar, la otra es cambiar de red.
    """
    if "/captcha/wall" in (url or ""):
        return True
    return ("suspicious-traffic" in html
            or "account-verification" in html
            or "captcha/wall" in html
            or "not-found-page" in html)


def _esperar_tarjetas(d) -> str:
    """
    Espera a que la página pinte las tarjetas y devuelve el HTML.

    Sale antes si aparece el interstitial: no tiene sentido esperar 25 segundos
    tarjetas que nunca van a llegar. Y si la página carga bien pero sin tarjetas
    (categorías sin ranking, como Bujías), devuelve el HTML igual para que el
    llamador distinga "vacío" de "bloqueado".
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        WebDriverWait(d, _ESPERA_MAX).until(
            lambda drv: drv.find_elements(By.CSS_SELECTOR, "div.poly-card")
            or _bloqueado(drv.page_source, drv.current_url)
        )
    except Exception:  # noqa: BLE001
        pass   # ni tarjetas ni bloqueo: el llamador lo resuelve con el HTML
    return d.page_source


def leer(categorias: list[str], limite: int = 10,
         visible: bool = False) -> dict[str, list[dict[str, Any]]]:
    """
    Top de más vendidos de cada categoría. → { categoria_id: [filas] }

    Reusa UNA sola instancia de Chrome para todas las categorías: abrirlo cuesta
    varios segundos y no hay razón para pagarlo por página.

    Es BLOQUEANTE (Selenium lo es). Desde código async, llamar con
    `asyncio.to_thread`.
    """
    cats = [c for c in dict.fromkeys(categorias) if c]
    if not cats:
        return {}
    if not disponible():
        log.error("Falta selenium o beautifulsoup4: pip install selenium beautifulsoup4 lxml")
        return {}

    from bs4 import BeautifulSoup

    out: dict[str, list[dict[str, Any]]] = {}
    sin_ranking: list[str] = []
    d = None
    try:
        d = _navegador(visible)
        for n_cat, cat in enumerate(cats):
            if n_cat:
                time.sleep(_PAUSA_ENTRE)   # ritmo: es rate limiting, no azar
            for intento in range(1, _MAX_INTENTOS + 1):
                try:
                    d.get(f"{URL_BASE}{cat}")
                    html = _esperar_tarjetas(d)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s: error de carga (intento %s): %s", cat, intento, exc)
                    continue
                if _bloqueado(html, d.current_url):
                    log.info("%s: interstitial en el intento %s; espero %ss",
                             cat, intento, _PAUSA_BLOQUEO)
                    time.sleep(_PAUSA_BLOQUEO)
                    continue

                sopa = BeautifulSoup(html, "lxml")
                filas = []
                for i, tarjeta in enumerate(sopa.select("div.poly-card"), start=1):
                    f = _fila(tarjeta, i)
                    if f:
                        filas.append(f)
                    if len(filas) >= limite:
                        break
                if filas:
                    out[cat] = filas
                    log.info("%s: %s filas", cat, len(filas))
                else:
                    # Sin tarjetas y SIN interstitial = ML no publica ranking de
                    # esa categoría (pasa con Bujías y Cartuchos de Turbo, cuyo
                    # /highlights también viene vacío). Reintentar no cambia nada,
                    # y distinguirlo del bloqueo importa: son acciones opuestas.
                    sin_ranking.append(cat)
                    log.info("%s: la página cargó SIN tarjetas — ML no publica más "
                             "vendidos de esta categoría", cat)
                break
    except Exception as exc:  # noqa: BLE001
        log.error("El navegador falló: %s", exc)
    finally:
        if d is not None:
            try:
                d.quit()
            except Exception:  # noqa: BLE001
                pass
    faltan = [c for c in cats if c not in out and c not in sin_ranking]
    if faltan:
        log.warning("Bloqueadas tras %s intentos: %s (subir _PAUSA_ENTRE)",
                    _MAX_INTENTOS, faltan)
    if sin_ranking:
        log.info("Sin ranking publicado por ML: %s", sin_ranking)
    return out
