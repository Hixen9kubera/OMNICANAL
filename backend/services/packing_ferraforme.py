"""
packing_ferraforme.py — La referencia de Ferraforme: en qué renglón del packing
list ORIGINAL está cada SKU.

QUÉ ES FERRAFORME
La carpeta de Drive "Ferraforme packing list" guarda una copia
homologada de cada packing list: el MISMO archivo del proveedor con columnas
agregadas al frente —la que importa trae el SKU de Odoo ("SKU ODOO" y variantes)— y las
filas en su lugar. Medido el 23-sep-2026 en KOCU4642556: 1,053 de 1,053
renglones traen el mismo nombre (chino e inglés) en la misma fila que el
original.

Decisión de Eduardo (23-sep): es SOLO una referencia para ubicar el SKU. Los
números salen del original, nunca de aquí. Y con razón: Alma descombina las
celdas (27 → 1 en ese mismo archivo), que es justo como el parser detecta las
cajas compartidas.

LO QUE HACE ESTE MÓDULO
1. :func:`leer_tabla` — encabezado y filas de un xlsx en modo SOLO LECTURA: sin
   fotos, así que un Ferraforme de 50 MB se lee en segundos y sin la RAM que se
   come el índice completo.
2. :func:`ubicar` — los renglones con SKU de un Ferraforme, cotejados contra el
   original: por MISMA FILA cuando el archivo va fila por fila, y si no, por
   TEXTO que aparece una sola vez en el original. Solo se comparan columnas de
   texto que Alma no retocó; las numéricas no, porque al descombinar ella copia
   el valor del ancla y un CTNS difiere sin que la fila sea otra (2 de 1,053 en
   el ejemplo). Medido el 23-sep sobre los 74 del sandbox: 24 archivos van fila
   por fila y 25 se ubican por texto (4,731 SKUs, 9,751 renglones). De los 25
   que no dan nada, 14 son ambiguos de verdad —contenedores de zapatos donde
   mil renglones dicen "鞋子" y solo cambia la talla: esos los sigue resolviendo
   la foto—, 8 no tienen su original en el bucket y 3 no traen columna de SKU.
3. :func:`ubicaciones_de` — la consulta del backend sobre
   ``costing.packing_ubicaciones`` (0057), que llena
   ``scripts/indexar_ferraforme.py``.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

import openpyxl

from services import packing_parser

# "SKU ODOO" (casi todos) o "SKU ODDO" (con la errata). La columna "SKU" a secas es el
# código del PROVEEDOR (REBI57779), no el nuestro.
_ENC_SKU = re.compile(r"^sku\s*od+o+$")
_ENC_CODIGO = re.compile(r"^sku$")
# El SKU de Kubera arranca con subcategoría y número (OFI-0152-...); lo demás es
# libre: hay '/', comillas y tallas (OFI-0152-MAD/CAF, TEC-1614-NEG-TV7").
SKU_KUBERA = re.compile(r"^[A-Z]{2,6}-\d{3,5}\S*$")
# Una columna es "de texto" si al menos la mitad de sus valores lo son.
_MIN_TEXTO = 0.5
# "[ELEC-0143-DJ6-NEG] Mezclador…": el nombre para mostrar de Odoo, pegado en
# alguna celda. A veces es el ÚNICO lugar con el SKU bueno: en el contenedor 80
# la columna de SKU sigue con el genérico ELEC-0116-EST y el corregido por la
# orden P03487 solo vive entre corchetes.
_CORCHETE = re.compile(r"\[\s*([A-Z]{2,6}-\d{3,5}[^\]\s]*)\s*\]")


def _norm(v: Any) -> str:
    """Valor comparable: texto sin espacios de más y en minúsculas; número
    redondeado (1 y 1.0 son lo mismo)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return repr(round(float(v), 6))
    return re.sub(r"\s+", " ", str(v)).strip().lower()


@dataclass
class Tabla:
    encabezado: int                                    # fila 1-based
    columnas: list[str]                                # encabezados normalizados
    filas: dict[int, tuple] = field(default_factory=dict)   # fila 1-based → valores


def leer_tabla(datos: bytes) -> Tabla:
    wb = openpyxl.load_workbook(io.BytesIO(datos), read_only=True, data_only=True)
    try:
        # `wb.active`, como el parser: si fueran hojas distintas se cotejaría
        # una hoja contra otra.
        filas = [tuple(r) for r in wb.active.iter_rows(values_only=True)]
    finally:
        wb.close()
    if not filas:
        return Tabla(1, [])
    i = packing_parser.encontrar_encabezado(filas)
    return Tabla(i + 1, [_norm(v) for v in filas[i]],
                 {n + 1: f for n, f in enumerate(filas) if n > i})


def _col(tabla: Tabla, patron: re.Pattern) -> int | None:
    return next((j for j, c in enumerate(tabla.columnas) if patron.match(c)), None)


def _col_sku(tabla: Tabla) -> int | None:
    """
    La columna con el SKU de Kubera, elegida por su CONTENIDO: la que más valores
    con forma de SKU trae (al menos 2 y la mitad de lo no vacío). El encabezado
    no sirve: en los 74 del sandbox aparece como "SKU ODOO", "SKU ODDO",
    "SKU DOO", "Sku" o "SKU" a secas — y "SKU" a secas, en el formato de
    siempre, es el código del PROVEEDOR (REBI57779), que no tiene esa forma.

    A empate de valores gana la de más valores DISTINTOS: junto a "SKU 0DDO
    PDRE" (el padre) viene "SKU ODOO" (la variante), y la variante es más fina.
    Y si la variante es una fórmula sin valor guardado (llega vacía en modo
    solo lectura), queda el padre — que no estorba: la escalera busca variantes.
    """
    mejor, puntos = None, (0, 0, False)
    for j, enc in enumerate(tabla.columnas):
        vals = [str(v[j]).strip().upper() for v in tabla.filas.values()
                if j < len(v) and v[j] not in (None, "")]
        skus = [x for x in vals if SKU_KUBERA.match(x)]
        if len(skus) < 2 or len(skus) < 0.5 * len(vals):
            continue
        p = (len(skus), len(set(skus)), bool(_ENC_SKU.match(enc)))
        if p > puntos:
            mejor, puntos = j, p
    return mejor


def _valor(fila: tuple, j: int | None) -> Any:
    return fila[j] if j is not None and j < len(fila) else None


def skus_del_renglon(vals: tuple, c_sku: int | None) -> list[tuple[str, str]]:
    """``[(sku, fuente)]``: el de la columna de SKU (``columna``) y los que
    vengan entre corchetes en cualquier celda (``corchete``), sin repetir el de
    la columna. Se guardan los dos porque no se sabe de antemano cuál acierta:
    eso lo mide la prueba de fiabilidad, no una suposición."""
    col = str(_valor(vals, c_sku) or "").strip().upper()
    salida = [(col, "columna")] if SKU_KUBERA.match(col) else []
    for v in vals:
        if isinstance(v, str) and "[" in v:
            for s in _CORCHETE.findall(v.upper()):
                if s != col and (s, "corchete") not in salida:
                    salida.append((s, "corchete"))
    return salida


def _columnas_de_texto(original: Tabla, comunes: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """De las columnas en común, las que en el ORIGINAL son mayormente texto."""
    elegidas = []
    for jf, jo in comunes:
        vals = [_valor(f, jo) for f in original.filas.values()]
        vals = [v for v in vals if v not in (None, "")]
        if vals and sum(isinstance(v, str) for v in vals) / len(vals) >= _MIN_TEXTO:
            elegidas.append((jf, jo))
    return elegidas


def _estables(original: Tabla, cols: list[tuple[int, int]],
              filas_f: list[tuple]) -> list[tuple[int, int]]:
    """Las columnas que Alma NO tocó: al menos 80% de lo que dice Ferraforme en
    ella aparece tal cual en esa columna del original. Una sola columna
    retocada (FFAU5807425: ella llenó la guía donde el original traía "/")
    bastaba para que ninguna llave cuadrara."""
    buenas = []
    for jf, jo in cols:
        del_original = {_norm(_valor(v, jo)) for v in original.filas.values()} - {""}
        vf = [x for x in (_norm(_valor(v, jf)) for v in filas_f) if x]
        if vf and sum(x in del_original for x in vf) >= 0.8 * len(vf):
            buenas.append((jf, jo))
    return buenas


def _juegos(ferra: Tabla, original: Tabla, c_sku: int,
            filas_f: list[tuple]) -> list[list[tuple[int, int]]]:
    """Las columnas con las que se puede cotejar, de la más fiel a la más laxa.

    1. Encabezado IDÉNTICO, mayormente texto y sin retocar (:func:`_estables`):
       el caso de manual, Ferraforme = el original con columnas agregadas.
    2. El nombre CHINO según :func:`packing_parser.mapear_columnas`, para cuando
       los encabezados no coinciden: varios "originales" del bucket son copias
       normalizadas (``nombre_chino``, ``num_cajas``…). Solo el chino: Alma
       escribe su propia descripción en español y la inglesa se confunde con
       ella; el chino es el del proveedor y nadie lo toca.
    """
    pos = {c: j for j, c in enumerate(original.columnas) if c}
    exactas = _estables(original, _columnas_de_texto(
        original, [(j, pos[c]) for j, c in enumerate(ferra.columnas)
                   if c and c in pos and j != c_sku]), filas_f)
    mf = packing_parser.mapear_columnas(ferra.columnas)
    mo = packing_parser.mapear_columnas(original.columnas)
    chino = ([(mf["producto_chn"], mo["producto_chn"])]
             if "producto_chn" in mf and "producto_chn" in mo and mf["producto_chn"] != c_sku
             else [])
    return [j for j in (exactas, chino) if j]


def _llave(vals: tuple, cols: list[tuple[int, int]], lado: int) -> tuple[str, ...]:
    return tuple(_norm(_valor(vals, par[lado])) for par in cols)


def ubicar(ferra: Tabla, original: Tabla | None) -> list[dict[str, Any]]:
    """
    Un dict por renglón de Ferraforme con SKU, y a qué fila del original
    corresponde — solo si se puede probar. Dos cotejos, en este orden:

    * ``fila`` — la misma fila (con el desfase del encabezado) dice lo mismo. Se
      acepta solo si así cuadra ≥90% del archivo: en un archivo que NO va fila
      por fila, que dos renglones "Pajama set" coincidan en la fila 4 es
      casualidad, no evidencia (Alma parte un renglón por talla y todo se corre).
    * ``texto`` — el renglón de Ferraforme dice algo que aparece UNA sola vez en
      el original. Ahí no importa el orden. Lo que se repite no se asigna.

    Sin original, o sin columnas que cotejar, nada queda alineado.
    """
    c_sku = _col_sku(ferra)
    c_cod = _col(ferra, _ENC_CODIGO)
    if c_cod == c_sku:
        c_cod = None
    skus = {fila: s for fila, vals in ferra.filas.items() if (s := skus_del_renglon(vals, c_sku))}
    con_sku = {fila: ferra.filas[fila] for fila in skus}
    if not con_sku:
        return []

    mapa: dict[int, int] = {}          # fila de Ferraforme → fila del original
    cotejo, cols = None, []
    if original is not None and con_sku:
        juegos = _juegos(ferra, original, c_sku, list(con_sku.values()))
        desfase = ferra.encabezado - original.encabezado
        for js in juegos:                                    # 1) misma fila
            m = {}
            for fila, vals in con_sku.items():
                ov = original.filas.get(fila - desfase)
                k = _llave(vals, js, 0)
                if ov is not None and any(k) and k == _llave(ov, js, 1):
                    m[fila] = fila - desfase
            if len(m) >= 0.9 * len(con_sku):
                mapa, cotejo, cols = m, "fila", js
                break
        if cotejo is None:                                   # 2) texto único
            for js in juegos:
                donde: dict[tuple, list[int]] = {}
                for fo, ov in original.filas.items():
                    k = _llave(ov, js, 1)
                    if any(k):
                        donde.setdefault(k, []).append(fo)
                m = {fila: donde[k][0] for fila, vals in con_sku.items()
                     if len(donde.get(k := _llave(vals, js, 0), ())) == 1}
                if len(m) > len(mapa):
                    mapa, cotejo, cols = m, "texto", js

    salida = []
    for fila, vals in con_sku.items():
        fo = mapa.get(fila)
        desc = []
        if fo is not None:
            ov = original.filas[fo]
            desc = [str(_valor(ov, jo)).strip() for _jf, jo in cols
                    if _valor(ov, jo) not in (None, "")][:2]
        for sku, fuente in skus[fila]:
            salida.append({
                "sku": sku, "fuente_sku": fuente, "ferraforme_fila": fila,
                "original_fila": fo, "alineado": fo is not None,
                "cotejo": cotejo if fo is not None else None,
                "codigo_proveedor": (str(_valor(vals, c_cod)).strip()
                                     if _valor(vals, c_cod) not in (None, "") else None),
                "texto": " · ".join(desc)[:300] or None,
            })
    return salida


def ubicaciones_de(skus: list[str]) -> dict[str, list[dict[str, Any]]]:
    """``{sku: [ubicación ALINEADA, ...]}``: el archivo original (file_id y
    huella de la versión cotejada) y su renglón. Ordenadas por archivo y fila."""
    if not skus:
        return {}
    from services import supabase_db as sdb
    salida: dict[str, list[dict[str, Any]]] = {}
    for f in sdb.fetch_all(
            """select u.sku::text as sku, u.original_file_id, u.original_sha256,
                      u.original_fila, u.contenedor_base, u.texto, u.fuente_sku,
                      o.nombre as original_nombre
                 from costing.packing_ubicaciones u
                 join costing.packing_archivos o
                   on o.tipo = 'original' and o.sha256 = u.original_sha256
                  and o.drive_file_id = u.original_file_id
                where u.alineado and u.sku = any(%s::citext[])
                order by u.sku, u.original_file_id, u.original_fila""", (list(skus),)):
        salida.setdefault(f["sku"].upper(), []).append(f)
    return salida
