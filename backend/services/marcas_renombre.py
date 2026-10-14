"""
marcas_renombre.py — ¿Este rival es una MARCA DE RENOMBRE?

POR QUÉ EXISTE (Eduardo, 12-oct-2026): vendemos genéricos importados, y las
búsquedas y el ranking de más vendidos de Mercado Libre traen productos de marca
(Vans, adidas, Puma, New Balance…) que cobran 3 a 5 veces lo que un genérico. En
Tenis (MLM6585) los genéricos del top cuestan $190–$300 y los de marca
$619–$1,420: la mediana salía en $398 cuando lo razonable para nuestros tenis
ronda $200. Una marca de renombre NO entra a ninguna mediana, promedio ni
referencia de precio de mercado. Se sigue MOSTRANDO, marcada.

La lista vive en `marcas_renombre.json` ({"marcas": [{marca, alias,
solo_vendedor}]}) y se va a reemplazar por una más completa con el mismo
formato: nada aquí depende de su contenido.

  alias          cuenta en el TÍTULO y en el vendedor ("air max" → Nike).
  solo_vendedor  cuenta SOLO en el vendedor: "on", "fila", "cat" son palabras
                 comunes en un título ("Pon on la mesa") y solo delatan a la
                 marca cuando la tienda es la marca.

El TÍTULO no delata marca (revisión v0.639.0) si la nombra para decir a qué es
compatible o a qué se parece: negación en las 3 palabras previas («para»,
«tipo», «inspirado en»…), un título que se declara «genérico/réplica/clon» en
cualquier posición, o un ACCESORIO como sustantivo del producto («Funda Uso Rudo
iPhone 15»). El VENDEDOR se lee sin el prefijo de la tarjeta de ML («Por Nike»,
«Vendido por FILA»).

Funciones PURAS (salvo `es_activo`, que lee el entorno): sin red ni base.

La vista de Competencia hace ~18,000 detecciones por carga, así que el título se
recorre UNA vez por sus n-gramas (1 a N palabras, N = el alias más largo) contra
un diccionario, en vez de probar cada alias con `in`; y el resultado se cachea
por (título, vendedor).

REVERSA: `COMPETENCIA_SIN_MARCAS=false` → las medianas vuelven a contar todo. La
marca se sigue detectando y viajando para que la pantalla la pinte.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("omnicanal.competencia.marcas")

_ARCHIVO = Path(__file__).with_name("marcas_renombre.json")

# Una aparición en el título NO cuenta si alguna de estas está entre las 3
# palabras anteriores: «Funda para iPhone 15», «Correa compatible con Apple
# Watch», «Tenis tipo Nike» venden un genérico que NOMBRA a la marca.
NEGADORES = frozenset({
    "para", "compatible", "compatibles", "tipo", "estilo", "similar", "replica",
    "imitacion", "generico", "generica", "alternativo", "repuesto", "for",
    # Perfumes «dupe» (revisión v0.639.0): «Perfume Inspirado En Carolina
    # Herrera 212» es un genérico que nombra a la marca para decir a qué huele.
    "inspirado", "inspirada", "inspirados", "inspiradas", "inspired",
    "equivalente", "equivalencia", "alternativa", "alternativos", "alternativas",
    "aroma",
})
VENTANA_NEGACION = 3

# Estas palabras niegan EN CUALQUIER POSICIÓN, no solo antes de la marca: un
# título que se declara genérico, réplica o clon no es la marca aunque la nombre
# primero («Control Inalambrico Ps4 Playstation Generico»).
_SE_DECLARA_GENERICO = frozenset({
    "generico", "generica", "genericos", "genericas", "generic",
    "replica", "replicas", "imitacion", "clon", "clones",
    # Listas de compatibilidad (v0.639.0, con títulos reales de los más vendidos):
    # «Cable Tipo C 60W … Compatible Samsung S24, Redmi», «Kit Varillas Suspension
    # Whirlpool Lavadora», «Pastilla De Deshielo Sirve Mabe Acros». La marca
    # aparece a más de 3 palabras de «compatible», así que la ventana no basta.
    # Casi todo lo que vende Kubera son esos accesorios: si se tomaran por marca,
    # su propia mediana se quedaría sin genéricos.
    "compatible", "compatibles", "universal", "universales", "sirve",
    "repuesto", "repuestos", "refaccion", "refacciones", "compatibilidad",
})

# ACCESORIOS (revisión v0.639.0): una funda, mica, cable o correa genérica se
# titula casi siempre nombrando el dispositivo SIN «para»: «Funda Uso Rudo
# iPhone 15», «Mica Cristal Templado Samsung Galaxy A54», «Correa Mi Band 8».
# Si la PRIMERA palabra del título (el sustantivo del producto, saltando
# cantidades y «kit/paquete/set») es un accesorio, el título no delata marca:
# solo el VENDEDOR puede (la tienda oficial de Apple sigue saliendo como Apple).
# Se queda fuera a propósito lo ambiguo: «base» (de maquillaje), «pantalla»
# (televisor), «bateria» (herramienta), «audifonos» (dispositivo).
ACCESORIOS = frozenset({
    "funda", "fundas", "mica", "micas", "hidrogel", "protector", "protectores",
    "cristal", "cristales", "vidrio", "case", "carcasa", "carcasas", "cubierta",
    "bumper", "skin", "skins", "estuche", "estuches", "cable", "cables",
    "cargador", "cargadores", "adaptador", "adaptadores", "correa", "correas",
    "pulso", "pulsos", "extensible", "extensibles", "control", "controles",
    "soporte", "soportes", "dock", "hub", "repuesto", "repuestos", "refaccion",
    "refacciones", "tapa", "tapas", "display",
    # Auditoría de las 1,048 detecciones del sandbox (12-oct): filtros y cartuchos de reemplazo nombran la marca
    # del aparato sin «para» («Filtro Agua Refrigerador Samsung Da29», «Filtro Aspiradora Koblenz»).
    "filtro", "filtros", "cartucho", "cartuchos",
})
# Lo que puede ir ANTES del sustantivo sin serlo: «2 Micas…», «Kit De Fundas…».
_RELLENO_CABEZA = frozenset({
    "kit", "paquete", "pack", "set", "combo", "lote", "x", "de", "pz", "pza",
    "pzs", "pzas", "pieza", "piezas", "pcs", "par", "pares", "w",
})
# Cantidades con unidad («45 W», «65w», «2m», «500ml») tampoco son el sustantivo: sin esto «65w Cargador…» tenía
# por cabeza «65w» y la regla de accesorios no actuaba.
_CANTIDAD = re.compile(r"^\d+(?:[.,]\d+)?(?:x|pz|pza|pzs|pzas|pcs|pack|w|v|a|mah|gb|tb|l|ml|oz|cm|mm|m|pulg|in)?$")

# «para» y «for» seguidos de a QUIÉN va dirigido no niegan: «Tenis Para Hombre
# Nike Revolution» es la forma más común de titular un tenis de marca en ML, y
# sin esta salvedad la marca se escapaba justo en la categoría que motivó todo
# esto. «Agujetas para tenis Nike» sí sigue negado («tenis» no es un público).
_PARA = frozenset({"para", "for"})
_PUBLICO = frozenset({
    "hombre", "hombres", "mujer", "mujeres", "dama", "damas", "caballero",
    "caballeros", "nino", "ninos", "nina", "ninas", "bebe", "bebes", "unisex",
    "adulto", "adultos", "joven", "jovenes", "men", "women", "kids",
    # …o PARA QUÉ uso, que es como se titulan los tenis de marca: «Tenis Para Correr Hombre Nike Revolution».
    "correr", "caminar", "running", "gym", "entrenar", "entrenamiento", "deporte", "deportivo", "deportivos",
    "futbol", "basquetbol", "yoga", "diario", "casual",
})

_NO_ALFANUM = re.compile(r"[^a-z0-9]+")
_ENV = "COMPETENCIA_SIN_MARCAS"


def normalizar(t: object) -> str:
    """NFKD → ascii, minúsculas, todo lo que no sea [a-z0-9] → espacio, espacios
    colapsados. «Cámara» → «camara», «Levi's» → «levi s», «Black+Decker» →
    «black decker»."""
    if t is None:
        return ""
    s = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode("ascii")
    return " ".join(_NO_ALFANUM.sub(" ", s.lower()).split())


@lru_cache(maxsize=1)
def _indice() -> tuple[dict[str, str], dict[str, str], int]:
    """(alias del título → marca, nombre del vendedor → marca, palabras del alias
    más largo). Si dos marcas comparten alias, gana la primera de la lista."""
    try:
        datos = json.loads(_ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Sin lista no hay exclusión: la mediana sale como antes, no revienta.
        log.error("marcas de renombre: no se pudo leer %s (%s)", _ARCHIVO.name, exc)
        return {}, {}, 1
    titulo: dict[str, str] = {}
    vendedor: dict[str, str] = {}
    for m in datos.get("marcas") or []:
        nombre = str(m.get("marca") or "").strip()
        if not nombre:
            continue
        alias = [a for a in (normalizar(x) for x in m.get("alias") or []) if a]
        solo = [a for a in (normalizar(x) for x in m.get("solo_vendedor") or []) if a]
        for a in alias:
            titulo.setdefault(a, nombre)
        for a in [normalizar(nombre), *alias, *solo]:
            if a:
                vendedor.setdefault(a, nombre)
    largo = max((len(a.split()) for a in titulo), default=1)
    return titulo, vendedor, largo


def _negado(palabras: list[str], i: int) -> bool:
    """¿Alguna de las VENTANA_NEGACION palabras antes de la posición `i` niega
    la aparición? (con la salvedad de «para <público>»)."""
    for j in range(max(0, i - VENTANA_NEGACION), i):
        p = palabras[j]
        if p not in NEGADORES:
            continue
        # «para» seguido SOLO de público o de uso hasta la marca no niega («Para Correr Hombre Nike»).
        if p in _PARA and j + 1 < i and all(x in _PUBLICO for x in palabras[j + 1:i]):
            continue
        return True
    return False


# El scraper web guarda el texto de `.poly-component__seller` tal cual, y en las
# tarjetas de ML ese texto trae el prefijo: «Por Nike», «Vendido por FILA»,
# «Tienda oficial Adidas». Sin quitarlo, «por nike» no está en el índice y las
# marcas que solo se delatan por el vendedor (Fila, Charly, On, LG…) nunca salen.
_PREFIJO_VENDEDOR = re.compile(r"^(?:(?:vendido|vendida) por |por |tienda oficial (?:de )?)+")
_SUFIJO_VENDEDOR = re.compile(r" (?:tienda )?oficial$")


def _por_vendedor(seller: str) -> str | None:
    s = _SUFIJO_VENDEDOR.sub("", _PREFIJO_VENDEDOR.sub("", normalizar(seller))).strip()
    if not s:
        return None
    # Tiendas oficiales de ML: «ADIDAS por Look Sport», «REEBOK por Look Trendy».
    if " por " in s:
        s = s.split(" por ", 1)[0].strip()
    return _indice()[1].get(s)


def _cabeza(palabras: list[str]) -> str | None:
    """El sustantivo del producto: la primera palabra que no es cantidad ni
    «kit/paquete/set/de». «2 Micas Cristal…» → «micas»."""
    for p in palabras:
        if p in _RELLENO_CABEZA or _CANTIDAD.match(p):
            continue
        return p
    return None


def _por_titulo(titulo: str) -> str | None:
    alias, _, largo = _indice()
    if not alias:
        return None
    palabras = normalizar(titulo).split()
    if _SE_DECLARA_GENERICO.intersection(palabras) or _cabeza(palabras) in ACCESORIOS:
        return None
    negadas: set[str] = set()
    for i in range(len(palabras)):
        for n in range(1, largo + 1):
            if i + n > len(palabras):
                break
            marca = alias.get(" ".join(palabras[i:i + n]))
            if not marca or marca in negadas:
                continue
            # Si una aparición de la marca está negada, las demás de la MISMA marca en ese título tampoco cuentan:
            # «Vaso Para Licuadora T-fal Infinity Force Vidrio T Fal» es un vaso genérico que repite el nombre.
            if _negado(palabras, i):
                negadas.add(marca)
                continue
            return marca
    return None


@lru_cache(maxsize=65536)
def _detectar(titulo: str, seller: str) -> str | None:
    return _por_vendedor(seller) or _por_titulo(titulo)


def detectar(titulo: object, seller: object = None) -> str | None:
    """El nombre de la marca de renombre (como viene en la lista) o None.

    Primero el VENDEDOR (la tienda es la marca, o «MARCA por X»); luego el
    TÍTULO, por palabras completas y sin negación en las 3 anteriores."""
    return _detectar(str(titulo or ""), str(seller or ""))


def es_activo() -> bool:
    """¿Se excluyen las marcas de las medianas? `COMPETENCIA_SIN_MARCAS`, por
    omisión encendido; "false"/"0"/"no" lo apaga. Apagado, las medianas salen
    como antes y la marca se sigue detectando para la pantalla."""
    return (os.getenv(_ENV) or "true").strip().lower() not in ("false", "0", "no")


def etiquetar(filas: list[dict], *, campo_titulo: str = "titulo",
              campo_seller: str = "seller") -> list[dict]:
    """Pone `marca_renombre` (str | None) en cada fila, en su lugar. Devuelve
    las mismas filas para encadenar.

    Una publicación NUESTRA (`sku_nuestro` o `es_nuestro`) nunca es marca de
    renombre: vendemos genéricos, y si su título nombra a una marca («Proyector
    Galaxy…») no debe salir de la mediana del top donde aparece."""
    for f in filas:
        if f.get("sku_nuestro") or f.get("es_nuestro"):
            f["marca_renombre"] = None
            continue
        f["marca_renombre"] = detectar(f.get(campo_titulo), f.get(campo_seller))
    return filas


def recargar() -> None:
    """Olvida la lista y las detecciones cacheadas (pruebas, o tras cambiar el
    JSON sin reiniciar)."""
    _indice.cache_clear()
    _detectar.cache_clear()
