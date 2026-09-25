"""
sku_provisional.py — El identificador provisional del app viejo, en UN solo lugar.

QUÉ ES
------
Antes de que existiera el SKU de Kubera (``SUBCAT-####-ATRIBUTO``), el app
viejo costeaba con un identificador armado con los últimos 4 del contenedor y
un consecutivo: ``5279-0001``. A veces con la variante pegada al final:
``0759-0057-PURPLE``, ``2791-0015-L``, ``1330-0083-WHITE-A+A``.

No existen en WooCommerce (VERIFICADO: ni los pelones ni los de sufijo; sin el
sufijo opcional en el patrón se colaban 16 filas): no tienen nombre, ni foto,
ni publicación. MEDIDO en producción: 6,237 de las
15,429 filas de ``costos_validados`` en MySQL, 6,252 en kubera (25-sep), y es
todo-o-nada por contenedor — los cuatro más grandes (MRKU3085279, FFAU4457148,
MRKU2054020, EITU9309801 = 5,213 filas) están 100% provisionales.

POR QUÉ VIVE APARTE
-------------------
El 25-sep Eduardo decidió BORRARLOS de ``core.products`` y
``costing.costos_validados`` (lo hace un script aparte, con su propio candado).
Borrar no basta si la app los vuelve a crear: ``costing_mirror`` da de alta la
identidad y el costo de cualquier SKU que le llegue. Por eso la regla la tienen
que compartir el que analiza (``packing_comparador``), el que ubica contenedores
(``ubicar_contenedores``) y el que escribe (``costing_mirror``) — con un patrón
por módulo, tarde o temprano uno se mueve y los otros no.

El ancla ``^\\d`` no toca SKUs de Kubera: empiezan con letras (``BAÑ-0486-EST``,
``ROP-AZL-GRICLA-GRIOBS-S``) o, si empiezan con dígito, no son dígitos-guion-
dígitos (``1CALZ-0108-BLN-ROJ-NEG-44``, ``802G``). Un número suelto (``25``)
tampoco: le falta el guion.
"""
from __future__ import annotations

import re
from typing import Any

# 3 a 5 dígitos por lado y un sufijo opcional («0031-0001», «4814-0001-A»).
RE_PROVISIONAL = re.compile(r"^\d{3,5}-\d{3,5}(?:-.+)?$")

# Lo que ve la persona en la pantalla de Costos, por SKU.
MENSAJE = "identificador provisional: ya no se guarda"


def es_provisional(sku: Any) -> bool:
    """``True`` si el "SKU" es en realidad un identificador provisional del app
    viejo (``5279-0001``), no un SKU de Kubera. Tolera ``None`` y espacios."""
    return bool(RE_PROVISIONAL.match(str(sku or "").strip()))


class SkuProvisional(ValueError):
    """Se intentó ESCRIBIR un identificador provisional.

    No es una falla de la base ni de la red: reintentarlo da lo mismo. Quien la
    atrape NO debe tratarla como caída de kubera — nada de encolarla en
    ``espejo_kubera_log`` ni de mandarla a MySQL como respaldo. Se loguea y se
    le dice a quien pidió la escritura.
    """

    def __init__(self, sku: Any) -> None:
        self.sku = str(sku or "").strip()
        super().__init__(
            f"{self.sku}: {MENSAJE} — es un identificador del app viejo "
            f"(contenedor + consecutivo), no un SKU de Kubera; no se da de alta "
            f"en core.products ni en costing.costos_validados/costos_finales")


def exigir_sku_real(sku: Any) -> None:
    """Lanza :class:`SkuProvisional` si ``sku`` es provisional. Va ANTES de
    cualquier SQL: la invariante no se delega."""
    if es_provisional(sku):
        raise SkuProvisional(sku)
