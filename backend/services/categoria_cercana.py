"""
categoria_cercana.py — ¿En qué subcategoría de ML cae un producto DENTRO de su categoría general? (v0.729.0)

POR QUÉ EXISTE
La pestaña CATÁLOGO agrupa y filtra por la categoría del catálogo (`K`), que muchas veces es la predicha y está mal
(el spray para cabello HIG-0146-EST salía en Hogar › Cocina). Desde la v0.728.0 cada SKU trae su categoría GENERAL
decidida con la foto del packing list (`channel.categoria_general`). Eduardo (5-nov-2026): «que en la búsqueda por
categoría se asignen a su categoría correcta en caso de que tengan más acercamiento». Este módulo decide:

  · en qué SUBCATEGORÍA de esa categoría general cae el producto, buscando con sus palabras (título, nombre de Odoo,
    texto del packing list) en el árbol de ML (`channel.ml_category_tree`, 0059), con la misma normalización que el
    buscador del Estudio (`categorias_arbol.normalizar` y su raíz de plurales);
  · si se REUBICA: confianza revisada o alta → siempre (los controles dieron 110/111); media o baja → solo si su mejor
    coincidencia dentro de la general le gana por un margen claro (MARGEN) a su mejor coincidencia dentro de la
    categoría del catálogo. Así una palabra suelta («auto», «repuesto») no mueve un producto que estaba bien.

CÓMO PUNTÚA (así se le explica a una persona)
Cada palabra del producto vale más mientras más al principio del título está (ahí va el tipo de producto) y mientras
MENOS común sea en esa categoría (una palabra que aparece en cientos de hojas no distingue nada). Una palabra que está
en el NOMBRE de la hoja vale el doble que una que solo está en su ruta. La hoja tiene que compartir al menos una
palabra en su propio nombre; si ninguna lo hace, se reubica sin subcategoría.

Funciones puras: no leen base ni disco (las alimenta `services/catalogo.py`).
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable

from services.categorias_arbol import _VACIAS, _raiz, normalizar

# La general le tiene que ganar al catálogo por este factor para reubicar una de confianza media o baja.
MARGEN = 1.25
# Confianzas que se reubican siempre (aplicadas).
SIEMPRE = frozenset({"revisado", "alta"})
# El árbol de ML dice «Mascotas»; el panel, «Animales y Mascotas».
_RAIZ_PANEL = {"Mascotas": "Animales y Mascotas"}
_OTRAS = frozenset({"otro", "otra"})


def raiz_panel(nombre: str | None) -> str | None:
    return _RAIZ_PANEL.get(nombre, nombre) if nombre else None


def _palabras(texto: str | None) -> list[str]:
    """Palabras útiles en raíz, en orden y sin repetir."""
    salida: list[str] = []
    for t in normalizar(texto).split():
        if t in _VACIAS or len(t) <= 2 or t.isdigit():
            continue
        r = _raiz(t)
        if r not in _OTRAS and r not in salida:
            salida.append(r)
    return salida


def pesos(titulo: str | None, otros: Iterable[str | None] = ()) -> dict[str, float]:
    """Palabra → peso. Las del título pesan 1, 0.74, 0.59… según su lugar; las de los otros textos, 0.4."""
    w: dict[str, float] = {}
    for i, t in enumerate(_palabras(titulo)):
        w[t] = 1.0 / (1 + 0.35 * i)
    for texto in otros:
        for t in _palabras(texto):
            w.setdefault(t, 0.4)
    return w


class Indice:
    """Las hojas publicables del árbol de ML, agrupadas por categoría general, con lo común que es cada palabra."""

    def __init__(self, hojas: Iterable[tuple[str, list[str]]]):
        self.por_raiz: dict[str, list[tuple[str, list[str], frozenset[str], frozenset[str]]]] = {}
        for category_id, ruta in hojas:
            if not ruta:
                continue
            rz = raiz_panel(ruta[0])
            nombre = frozenset(_palabras(ruta[-1]))
            medio = frozenset(_palabras(" ".join(ruta[1:-1])))
            self.por_raiz.setdefault(rz, []).append((category_id, ruta, nombre, medio))
        self._df: dict[str, tuple[int, Counter]] = {}
        # Índice invertido: palabra del NOMBRE de la hoja → hojas que la llevan (solo ésas pueden ganar).
        self._por_palabra: dict[str, dict[str, list[int]]] = {}
        for rz, lista in self.por_raiz.items():
            c: Counter = Counter()
            inv: dict[str, list[int]] = {}
            for i, (_, _, nombre, medio) in enumerate(lista):
                c.update(nombre | medio)
                for t in nombre:
                    inv.setdefault(t, []).append(i)
            self._df[rz] = (len(lista), c)
            self._por_palabra[rz] = inv

    def _idf(self, rz: str, t: str) -> float:
        n, c = self._df.get(rz, (1, Counter()))
        return math.log((n + 1) / (c.get(t, 0) + 1)) + 0.1

    def mejor(self, w: dict[str, float], raiz: str | None) -> tuple[list[str] | None, float]:
        """(ruta de la hoja más cercana dentro de `raiz`, puntaje). Sin coincidencia → (None, 0)."""
        if not raiz or raiz not in self.por_raiz or not w:
            return None, 0.0
        mejor_ruta, mejor_p = None, 0.0
        palabras = set(w)
        inv = self._por_palabra[raiz]
        candidatas = sorted({i for t in palabras for i in inv.get(t, ())})
        lista = self.por_raiz[raiz]
        for i in candidatas:
            _, ruta, nombre, medio = lista[i]
            p = sum(w[t] * self._idf(raiz, t) * (2 if t in nombre else 1)
                    for t in palabras if t in nombre or t in medio)
            if p > mejor_p:
                mejor_ruta, mejor_p = ruta, p
        return mejor_ruta, round(mejor_p, 3)


def ubicar(indice: Indice, general: str, confianza: str, raiz_catalogo: str | None,
           w: dict[str, float]) -> list[str] | None:
    """¿Dónde se agrupa en la pestaña? None = se queda en la categoría del catálogo; [sub, hoja] = en la general, con
    su subcategoría y hoja más cercanas ("" si no hubo ninguna)."""
    rc = raiz_panel(raiz_catalogo)
    if rc == general:
        return None                                    # misma categoría: manda la subcategoría del catálogo
    ruta, p_general = indice.mejor(w, general)
    if confianza not in SIEMPRE and rc:
        _, p_catalogo = indice.mejor(w, rc)
        if not ruta or p_general < MARGEN * max(p_catalogo, 0.01):
            return None
    if not ruta:
        return ["", ""]
    return [ruta[1] if len(ruta) > 1 else "", ruta[-1] if len(ruta) > 2 else ""]


def textos(fila: dict[str, Any]) -> dict[str, float]:
    """Pesos de una fila del catálogo (`catalogo.json.gz`): título + nombre de Odoo + texto del packing list."""
    return pesos(fila.get("t"), [fila.get("n"), " ".join(fila.get("pd") or []), fila.get("on")])
