"""
mercado_comun.py — Lo que comparten las dos etapas de precio de mercado (Mercado Libre y Amazon).

Qué se busca y cómo se agrupa
-----------------------------
No se hace una búsqueda por SKU: las variantes de un mismo modelo (colores, tallas)
compiten contra las mismas publicaciones. Se agrupa por MODELO —el SKU sin su último
tramo— más el término de búsqueda y las unidades por paquete, y cada grupo se busca
y se juzga UNA vez. Un renglón de packing list sin SKU es su propio grupo.

El juez
-------
Buscar «zapatero organizador» devuelve zapateros de todo tipo. Promediar todo eso
no dice cuánto vale EL NUESTRO. El juez (DeepSeek) lee nuestro título y los títulos
de los rivales y dice cuáles son el mismo producto. Sus reglas son las del juez de
Competencia de producción (`competencia_juez.py`, versión j3), con la respuesta
compactada para que cueste menos.

De ahí sale el precio de referencia
-----------------------------------
Solo cuentan los rivales `mismo`. El precio de cada uno se lleva a NUESTRA unidad:
si el rival vende un paquete de 2 y nosotros la pieza, su precio se parte entre 2
(y el resultado se marca `aprox`: el precio por pieza no es lineal, un paquete
suele salir más barato por pieza). El dato principal es la MEDIA; a su lado van la
mediana y el rango central P25–P75, porque dos publicaciones caras mueven el promedio.
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any

from comun import leer_json, sku_norm

VERSION_JUEZ = "j3c"
CLASES = {"m": "mismo", "g": "otra_gama", "r": "refaccion", "o": "otro_producto", "d": "dudoso"}

JUEZ = """Eres analista de precios de un vendedor de marketplaces en México.
Recibes UN producto NUESTRO y una lista numerada de publicaciones. Decides, para CADA una, si sirve para comparar PRECIO contra nuestro producto. Respondes SOLO un objeto JSON.

PASO 1 — Define para ti qué es EXACTAMENTE nuestro producto: su subtipo, su mecanismo o forma de uso, su tamaño o capacidad, para qué modelo o uso es y qué incluye. Ese subtipo es la vara con la que mides a cada rival.

PASO 2 — Para cada rival recorre estas preguntas EN ORDEN y quédate con la primera que se cumpla:
1. ¿Es otra cosa, que solo comparte palabras o categoría? -> "o".
2. ¿Es una refacción, repuesto, accesorio o consumible PARA un producto como el nuestro (cuchillas para licuadora, correa para reloj, funda para sillón)? -> "r". Excepción: si NUESTRO producto es en sí esa refacción o accesorio, los que venden lo mismo son "m".
3. ¿Comparte el sustantivo pero es OTRO subtipo: otro mecanismo o forma de uso, otro tamaño o capacidad claramente distintos, otra potencia, hecho para otro modelo o marca, o le falta (o le sobra) lo que define al nuestro? -> "g". Ejemplos: lámpara de escritorio contra lámpara de techo; mochila de viaje de 60 L contra mochila escolar; funda para un modelo de tableta contra funda para otro modelo; el producto suelto contra un sistema o kit completo que lo incluye.
4. ¿No se puede saber qué es ni con el título completo? -> "d".
5. En cualquier otro caso es "m": el mismo subtipo, con la misma función y una gama comparable; quien busca el nuestro lo compraría en su lugar.

REGLAS:
- Otra marca, color, diseño o material NO descalifican. Tampoco el precio.
- Sé estricto con el subtipo, pero no exijas que sea idéntico: pequeñas diferencias de medida o de acabado siguen siendo "m".
- Los títulos amontonan palabras clave («funda/carcasa/protector para celular»): decide por el producto más probable; eso no es "d".
- "unidades": cuántos productos COMPLETOS como el nuestro trae esa publicación (1 si no dice; «par» o «2 pzs» del mismo artículo = 2; «pack de 4» = 4). Las piezas de un juego que se usa completo (un juego de cubiertos de 24 piezas, un kit de bloques, un set de brocas) NO son unidades: eso es 1.
- No clasifiques por cantidad: si el producto es el mismo y solo cambia cuántas unidades trae, responde "m" y pon sus unidades. La cuenta la hace el sistema.
- Un veredicto por CADA número de la lista, sin saltarte ninguno ni inventar números.

FORMATO (JSON compacto, sin explicaciones):
{"v": [[<número>, "<m|g|r|o|d>", <unidades>], ...]}"""


def modelo_de(sku: str) -> str:
    """El SKU sin su último tramo (`MUE-0374-NEG` → `MUE-0374`): las variantes de un modelo."""
    partes = sku.split("-")
    return "-".join(partes[:2]) if len(partes) >= 3 else sku


def _clave(*partes: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in partes).encode()).hexdigest()[:12]


def grupos_de_busqueda(salida: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """({clave: {q, t, u, skus}}, {sku: clave}) a partir de `datos/titulos.json`."""
    doc = leer_json(salida / "datos" / "titulos.json") or {}
    grupos: dict[str, dict[str, Any]] = {}
    de_sku: dict[str, str] = {}
    for sku, x in (doc.get("skus") or {}).items():
        q = re.sub(r"\s+", " ", (x.get("q") or "")).strip().lower()
        if not q or not x.get("t"):
            continue
        u = int(x.get("u") or 1)
        base = modelo_de(sku_norm(sku)) if not sku.startswith("PL:") else sku
        clave = _clave(base, q, u)
        g = grupos.setdefault(clave, {"q": q, "t": x["t"], "u": u, "skus": []})
        g["skus"].append(sku)
        # el título que representa al grupo: el más largo (el que más dice)
        if len(x["t"]) > len(g["t"]):
            g["t"] = x["t"]
        de_sku[sku] = clave
    return grupos, de_sku


def pesos(salida: Path) -> dict[str, float]:
    """Cuántas piezas hay detrás de cada llave (SKU o renglón sin SKU): para buscar primero
    lo que más pesa y que una corrida cortada a la mitad ya cubra casi todo el inventario."""
    from ia_titulos import clave_renglon

    inv = leer_json(salida / "datos" / "inventario_pl.json") or {}
    w: dict[str, float] = {}
    for sku, x in (inv.get("skus") or {}).items():
        w[sku] = max(x.get("q") or 0, x.get("lib") or 0)
    for r in inv.get("renglones_sin_sku") or []:
        k = clave_renglon(r.get("t") or "")
        if k:
            w[k] = w.get(k, 0) + (r.get("pz") or 0)
    return w


def peso_grupo(g: dict[str, Any], w: dict[str, float]) -> float:
    return sum(w.get(s, 0) for s in g["skus"])


def estadistica(precios: list[float]) -> dict[str, float]:
    p = sorted(x for x in precios if x and x > 0)
    if not p:
        return {}

    def cuantil(f: float) -> float:
        if len(p) == 1:
            return p[0]
        i = f * (len(p) - 1)
        a, b = int(i), min(int(i) + 1, len(p) - 1)
        return p[a] + (p[b] - p[a]) * (i - a)

    return {"n": len(p), "media": round(statistics.fmean(p), 2), "mediana": round(statistics.median(p), 2),
            "p25": round(cuantil(0.25), 2), "p75": round(cuantil(0.75), 2),
            "min": round(p[0], 2), "max": round(p[-1], 2)}


def juzgar(ia: Any, titulo: str, unidades: int, rivales: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """[(clase, unidades)] en el orden de `rivales`. Lo que el juez se salte queda `d`."""
    if not rivales:
        return []
    lista = "\n".join(f"{i + 1}. {str(r['t'])[:150]}" for i, r in enumerate(rivales))
    usuario = (f"NUESTRO PRODUCTO\ntitulo: {titulo}\nunidades_nuestras (dato fijo): {unidades}\n\n"
               f"RIVALES ({len(rivales)})\n{lista}\n\nResponde el JSON con un veredicto por cada número.")
    try:
        res = ia.json(JUEZ, usuario, max_tokens=120 + 14 * len(rivales), temperatura=0)
    except Exception:  # noqa: BLE001 — sin juez no hay precio: se reintenta en la siguiente corrida
        return []
    por_i: dict[int, tuple[str, int]] = {}
    for v in res.get("v") or []:
        try:
            i, clase = int(v[0]), str(v[1]).strip().lower()[:1]
            u = max(1, int(float(v[2]))) if len(v) > 2 and v[2] else 1
        except (TypeError, ValueError, IndexError):
            continue
        if clase in CLASES:
            por_i[i] = (clase, u)
    return [por_i.get(i + 1, ("d", 1)) for i in range(len(rivales))]


def resumir(unidades: int, rivales: list[dict[str, Any]], veredictos: list[tuple[str, int]]) -> dict[str, Any]:
    """El precio de referencia del grupo, en NUESTRA unidad."""
    exactos, aprox, ejemplos = [], [], []
    cuenta = {"m": 0, "g": 0, "r": 0, "o": 0, "d": 0}
    for r, (clase, u) in zip(rivales, veredictos):
        cuenta[clase] += 1
        if clase != "m" or not r.get("p"):
            continue
        if u == unidades:
            exactos.append(r["p"])
            ejemplos.append({"t": str(r["t"])[:90], "p": r["p"], "id": r.get("id")})
        else:
            precio = round(r["p"] * unidades / u, 2)
            aprox.append(precio)
            ejemplos.append({"t": str(r["t"])[:90], "p": precio, "id": r.get("id"), "u": u})
    usados = exactos if len(exactos) >= 2 or not aprox else exactos + aprox
    fila: dict[str, Any] = {"vistos": len(rivales), "clases": {k: v for k, v in cuenta.items() if v}}
    fila.update(estadistica(usados))
    if usados and usados is not exactos:
        fila["aprox"] = 1                       # entraron rivales con otro tamaño de paquete, llevados a la pieza
    fila["ej"] = sorted(ejemplos, key=lambda e: e["p"])[:4]
    return fila


def cargar(ruta: Path) -> dict[str, Any]:
    return leer_json(ruta) or {"grupos": {}}


def firma(g: dict[str, Any]) -> str:
    return _clave(VERSION_JUEZ, g["q"], g["t"], g["u"])


def volcar(texto: Any) -> str:
    return json.dumps(texto, ensure_ascii=False)
