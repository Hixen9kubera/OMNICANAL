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

ANUNCIOS: SE DESCARTAN
----------------------
Las primeras tarjetas del buscador son publicidad (`.poly-component__ads-promotions`
con el texto "Ad"). Medido en "disfraz de dinosaurio inflable": las 4 primeras lo
eran. No se devuelven —ni siquiera se construyen— por dos razones: contarlas como
posición orgánica diría que estás más abajo de lo que estás, y un anuncio no dice
quién gana el nicho, solo quién pagó hoy. La numeración orgánica avanza únicamente
con las tarjetas que no son anuncio.

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

import os

# MEDIDO el 4-ago: con 8 s de pausa y UNA sola sesión de Chrome, ML cortó a las
# ~50 consultas con el muro de login. Dos palancas contra eso, las dos por
# variable de entorno para poder ajustar sin tocar código:
#   COMPETENCIA_PAUSA=35   segundos entre consultas (más lento, menos sospechoso)
#   COMPETENCIA_LOTE=25    consultas por sesión de navegador; al agotarse se abre
#                          uno nuevo, que es lo que reinicia el contador de ML
_PAUSA_ENTRE = float(os.environ.get("COMPETENCIA_PAUSA", "8"))
_LOTE_NAVEGADOR = int(os.environ.get("COMPETENCIA_LOTE", "0"))  # 0 = sin reinicio
_MAX_INTENTOS = 3
_ESPERA_MAX = 25
# Tope duro de consultas por corrida. Es un freno, no una meta: sin él un bucle mal
# parametrizado puede lanzar miles de peticiones seguidas a ML, que es exactamente
# la conducta que hace que corten la cuenta. 0 = sin tope.
_TOPE_CONSULTAS = int(os.environ.get("COMPETENCIA_TOPE", "150"))

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

    En un resultado orgánico el href ES el permalink. El respaldo por atributos
    existe para la tarjeta rara cuya ancla no lo trae; descarta los ids de 12+
    dígitos, que son de campaña publicitaria y no de publicación.
    """
    m = _RE_PERMALINK.search(href or "")
    if m:
        return m.group(1) or f"{m.group(2)}{m.group(3)}"
    candidatos = [x for x in _RE_ID.findall(str(tarjeta))
                  if x.startswith("MLMU") or len(x) <= 13]
    propios = [x for x in candidatos if not x.startswith("MLMU") and len(x) - 3 <= 11]
    return (propios or candidatos or [None])[0]


def _fila(tarjeta, posicion_organica: int) -> dict[str, Any] | None:
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
    return {
        "externo_id": ident,
        # Posición ORGÁNICA: los anuncios no ocupan lugar en esta cuenta.
        "posicion": posicion_organica,
        "titulo": txt(".poly-component__title"),
        "precio": _entero(txt(".poly-price__current .andes-money-amount__fraction")),
        "precio_lista": _entero(txt(".poly-price__previous .andes-money-amount__fraction")),
        "descuento": txt(".poly-price__disc-label"),
        "vendidos": vendidos,
        "rating": score,
        "seller": txt(".poly-component__seller"),
        "imagen": (img.get("src") or img.get("data-src")) if img else None,
        # Nunca el enlace del redirector de clics: si viene vacío, la UI
        # reconstruye el permalink a partir del id.
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
    if _TOPE_CONSULTAS and len(consultas) > _TOPE_CONSULTAS:
        log.warning("Se pidieron %s consultas y el tope por corrida es %s: se "
                    "recortan. Sube COMPETENCIA_TOPE si de verdad hacen falta.",
                    len(consultas), _TOPE_CONSULTAS)
        consultas = consultas[:_TOPE_CONSULTAS]

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
            # Navegador nuevo cada `_LOTE_NAVEGADOR` consultas: es lo que reinicia
            # el contador que dispara el muro de login.
            if _LOTE_NAVEGADOR and n and n % _LOTE_NAVEGADOR == 0:
                log.info("consulta %s: reinicio el navegador", n)
                try:
                    d.quit()
                except Exception:  # noqa: BLE001
                    pass
                d = _navegador(visible)
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
                    # Insistir con la MISMA sesión no sirve: si ML ya pidió login,
                    # lo va a seguir pidiendo. Se abre una sesión nueva.
                    log.info("%r: bloqueado en el intento %s, reinicio el navegador",
                             q, intento)
                    try:
                        d.quit()
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(_PAUSA_ENTRE * 2)
                    d = _navegador(visible)
                    continue
                sopa = BeautifulSoup(html, "lxml")
                filas, organica, anuncios = [], 0, 0
                for tarjeta in sopa.select("div.poly-card"):
                    if _es_anuncio(tarjeta):
                        anuncios += 1
                        continue
                    organica += 1
                    f = _fila(tarjeta, organica)
                    if f:
                        filas.append(f)
                    if len(filas) >= limite:
                        break
                if filas:
                    out[q] = filas
                    log.info("%r: %s orgánicos (%s anuncios descartados)",
                             q, len(filas), anuncios)
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
