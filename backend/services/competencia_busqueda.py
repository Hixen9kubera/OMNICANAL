"""Raspa el BUSCADOR de Mercado Libre: qué sale cuando alguien busca algo.

POR QUÉ RASPANDO Y NO POR API
-----------------------------
`GET /sites/MLM/search` responde **403** con token válido en las dos apps. No hay
posición orgánica por API: es la única medición del módulo que obliga a raspar.

LAS DOS BÚSQUEDAS
-----------------
  • TÉRMINO GENERAL  ("disfraz de dinosaurio") → con quién compites por
    DESCUBRIMIENTO: quien no sabe qué marca quiere y escribe la categoría.
  • TÍTULO COMPLETO → tu competencia DIRECTA: el mismo producto.

ANUNCIOS: SE MARCAN, NO SE MEZCLAN
----------------------------------
Las primeras tarjetas del buscador son publicidad (`.poly-component__ads-promotions`
con el texto "Ad") y su enlace va por `click1.mercadolibre.com.mx/mclics/…`, que
esconde el id. Medido en "disfraz de dinosaurio inflable": las 4 primeras eran
anuncios. Contarlas como posición orgánica falsearía el ranking —dirían que estás
más abajo de lo que estás—, así que la posición orgánica se numera aparte y el
anuncio queda etiquetado.

CORRE CON VENTANA VISIBLE
-------------------------
Igual que el ranking: ML detecta `--headless=new` y devuelve un 404 a todo. Ver
`competencia_mas_vendidos._navegador`.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
from typing import Any

from services.competencia_mas_vendidos import (
    _badge, _bloqueado, _entero, _navegador, _score_y_vendidos,
)

log = logging.getLogger("omnicanal.competencia.busqueda")

URL_BASE = "https://listado.mercadolibre.com.mx/"

_PAUSA_ENTRE = 8.0
_MAX_INTENTOS = 3
_ESPERA_MAX = 25

_RE_ID = re.compile(r"MLMU?\d{9,12}")
# El id de la publicación tiene 9-12 dígitos tras MLM; los de campaña publicitaria
# vienen con 12 y conviven con el real en la misma tarjeta, así que no basta con
# tomar el primero que aparezca.
_RE_PERMALINK = re.compile(r"/(?:up|p)/(MLMU?\d+)|/(MLM)-(\d{9,12})-")


def disponible() -> bool:
    from services import competencia_mas_vendidos as mv
    return mv.disponible()


def _es_anuncio(tarjeta) -> bool:
    n = tarjeta.select_one(".poly-component__ads-promotions")
    return bool(n and "ad" in n.get_text(strip=True).lower())


def _id_de(tarjeta, href: str) -> str | None:
    """
    El id de la publicación.

    En los ORGÁNICOS el href ES el permalink y se saca de ahí. En los ANUNCIOS el
    href va por el redirector de clics y no lo trae, así que se busca en los
    atributos de la tarjeta descartando los de 12 dígitos, que son de campaña.
    """
    m = _RE_PERMALINK.search(href or "")
    if m:
        return m.group(1) or f"{m.group(2)}{m.group(3)}"
    candidatos = [x for x in _RE_ID.findall(str(tarjeta))
                  if x.startswith("MLMU") or len(x) <= 13]
    propios = [x for x in candidatos if not x.startswith("MLMU") and len(x) - 3 <= 11]
    return (propios or candidatos or [None])[0]


def _fila(tarjeta, posicion_organica: int | None) -> dict[str, Any] | None:
    a = (tarjeta.select_one("a.poly-component__title")
         or tarjeta.select_one("a[href*='mercadolibre']"))
    href = (a.get("href") if a else "") or ""

    def txt(sel: str) -> str | None:
        n = tarjeta.select_one(sel)
        return n.get_text(strip=True) if n else None

    ident = _id_de(tarjeta, href)
    if not ident:
        return None
    score, vendidos = _score_y_vendidos(tarjeta)
    img = tarjeta.select_one("img")
    anuncio = _es_anuncio(tarjeta)
    return {
        "externo_id": ident,
        # Solo los orgánicos llevan posición. Un anuncio no está "en el puesto 1".
        "posicion": posicion_organica,
        "es_anuncio": anuncio,
        "titulo": txt(".poly-component__title"),
        "precio": _entero(txt(".poly-price__current .andes-money-amount__fraction")),
        "precio_lista": _entero(txt(".poly-price__previous .andes-money-amount__fraction")),
        "descuento": txt(".poly-price__disc-label"),
        "vendidos": vendidos,
        "rating": score,
        "seller": txt(".poly-component__seller"),
        "imagen": (img.get("src") or img.get("data-src")) if img else None,
        # El enlace del anuncio pasa por el redirector; se reconstruye del id para
        # que el clic lleve a la publicación y no a un contador de clics.
        "url": href if (href and "click1.mercadolibre" not in href) else None,
    }


def buscar(terminos: list[str], limite: int = 5,
           visible: bool = True) -> dict[str, list[dict[str, Any]]]:
    """
    Top `limite` ORGÁNICOS de cada término. → { termino: [filas] }

    Bloqueante (Selenium). Desde código async, `asyncio.to_thread`.
    """
    consultas = [t.strip() for t in dict.fromkeys(terminos) if t and t.strip()]
    if not consultas or not disponible():
        return {}

    from bs4 import BeautifulSoup
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    out: dict[str, list[dict[str, Any]]] = {}
    d = None
    try:
        d = _navegador(visible)
        for n, q in enumerate(consultas):
            if n:
                time.sleep(_PAUSA_ENTRE)
            url = URL_BASE + urllib.parse.quote(q.replace(" ", "-"))
            for intento in range(1, _MAX_INTENTOS + 1):
                try:
                    d.get(url)
                    WebDriverWait(d, _ESPERA_MAX).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div.poly-card")))
                except Exception as exc:  # noqa: BLE001
                    log.warning("%r: intento %s sin tarjetas (%s)", q, intento, exc)
                html = d.page_source
                if _bloqueado(html, d.current_url):
                    log.info("%r: bloqueado en el intento %s", q, intento)
                    time.sleep(20)
                    continue
                sopa = BeautifulSoup(html, "lxml")
                filas, organica = [], 0
                for tarjeta in sopa.select("div.poly-card"):
                    es_ad = _es_anuncio(tarjeta)
                    if not es_ad:
                        organica += 1
                    f = _fila(tarjeta, None if es_ad else organica)
                    if f and not f["es_anuncio"]:
                        filas.append(f)
                    if len(filas) >= limite:
                        break
                if filas:
                    out[q] = filas
                    log.info("%r: %s orgánicos", q, len(filas))
                break
    except Exception as exc:  # noqa: BLE001
        log.error("El navegador falló: %s", exc)
    finally:
        if d is not None:
            try:
                d.quit()
            except Exception:  # noqa: BLE001
                pass
    faltan = [q for q in consultas if q not in out]
    if faltan:
        log.warning("Sin resultados para %s", faltan)
    return out
