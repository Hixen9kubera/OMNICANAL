"""
sku_contenedor.py — Lector compartido de `costing.sku_contenedor` (0060): en qué
contenedor(es) de Kubera llegó cada SKU.

QUÉ ES
------
La tabla es la fuente PREFERIDA del contenedor de un SKU (spec del 25-sep,
«Sí, conéctala en sandbox»). Una fila = (sku, número de contenedor) con nivel de
confianza (A/B), las fuentes que coinciden y si el SKU llegó en varios
(`multi`). Lo de antes —el campo de Odoo, `costos_validados.contenedor`, los
packing lists— queda de RESPALDO para los SKUs que la tabla no tiene
(conflictos, refutados, sin evidencia): esos van a la lista de Brandon y aquí
simplemente no aparecen.

Lo usan Inventario (fila, ficha y alertas), el cotejo de cajas, Crear y el
filtro de Costos. Cada pantalla decide su precedencia; aquí solo se LEE.

REGLAS
------
- Solo lectura. Nada del panel escribe en la tabla (su escritor único es
  scripts/ubicar_skus_contenedor.py).
- Sin el flag `LEER_SKU_CONTENEDOR` no se consulta nada: `{}` y cada pantalla se
  queda EXACTAMENTE como en v0.575.
- Nunca tumba una pantalla: si la tabla no existe (42P01, producción antes de la
  0060) o la lectura falla, avisa UNA vez por proceso en el log y `por_sku`
  devuelve `None`: «no se pudo leer; usa el respaldo y no afirmes nada de la
  tabla». `{}` queda SOLO para «se leyó y no tiene a esos SKUs». Inventario
  necesita distinguir los dos casos: con `{}` diría de CADA SKU que «la tabla no
  lo tiene» cuando en realidad no la pudo leer. Con la tabla ausente, además,
  no se vuelve a preguntar durante 5 minutos: sin eso cada carga de la pestaña
  pagaría una consulta que ya se sabe que falla.
- Todo es síncrono (psycopg2): desde una corrutina va en `asyncio.to_thread`
  (regla 11 de la casa).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.sku_contenedor")

# Lotes de 800, como las gemelas de costing_read: el mismo límite práctico.
_LOTE = 800
# La lista de números (para los filtros de Costos) se guarda 30 s, como la
# agrupación de embarques con la que se combina.
_TTL_NUMEROS_S = 30.0
# Con la tabla AUSENTE no se reintenta antes de esto.
_TTL_AUSENTE_S = 300.0

_candado = threading.Lock()
_avisados: set[str] = set()
_ausente_hasta = 0.0
_cache_numeros: tuple[float, dict[int, str | None]] | None = None


def activo() -> bool:
    """¿Está encendido `LEER_SKU_CONTENEDOR`?"""
    return bool(getattr(settings, "leer_sku_contenedor", False))


def _es_tabla_ausente(exc: BaseException) -> bool:
    return (getattr(exc, "pgcode", None) == "42P01"
            or type(exc).__name__ == "UndefinedTable")


def _fallo(exc: BaseException) -> None:
    """Un aviso por proceso y por tipo de falla; lo demás, a debug."""
    global _ausente_hasta
    ausente = _es_tabla_ausente(exc)
    tipo = "tabla_ausente" if ausente else type(exc).__name__
    with _candado:
        if ausente:
            _ausente_hasta = time.monotonic() + _TTL_AUSENTE_S
        primera = tipo not in _avisados
        _avisados.add(tipo)
    if primera:
        if ausente:
            log.warning("sku_contenedor: costing.sku_contenedor no existe en esta base "
                        "(¿falta la 0060?); se usa el respaldo de siempre")
        else:
            log.warning("sku_contenedor: no se pudo leer costing.sku_contenedor (%s); "
                        "se usa el respaldo de siempre", exc)
    else:
        log.debug("sku_contenedor: lectura fallida (%s)", exc)


def _ausente() -> bool:
    with _candado:
        return time.monotonic() < _ausente_hasta


def por_sku(skus: Iterable[str]) -> dict[str, list[dict[str, Any]]] | None:
    """
    ``{ sku en minúsculas: [{numero, codigo, nivel, fuentes, multi}, …] }``,
    cada lista ordenada por `numero` descendente (el más nuevo primero).

    Una consulta por lote de 800 (`sku = any(%s::citext[])`). La llave va en
    minúsculas porque la columna es citext: `mic-0001-gri` y `MIC-0001-GRI` son
    el mismo SKU. Un SKU ausente del dict = la tabla SE LEYÓ y no sabe nada de él.

    Sin flag: `{}` sin consultar. Con la tabla ausente (o dentro de sus 5
    minutos de espera) o ante cualquier error: `None` = NO SE LEYÓ. Todo o nada:
    medio resultado haría que una página mezclara precedencias.
    """
    if not activo():
        return {}
    if _ausente():
        return None
    pedidos: list[str] = []
    vistos: set[str] = set()
    for s in skus or ():
        t = str(s or "").strip()
        if t and t.lower() not in vistos:
            vistos.add(t.lower())
            pedidos.append(t)
    if not pedidos:
        return {}
    salida: dict[str, list[dict[str, Any]]] = {}
    try:
        for i in range(0, len(pedidos), _LOTE):
            chunk = pedidos[i:i + _LOTE]
            for r in sdb.fetch_all(
                    """select sku::text as sku, numero, codigo, nivel, fuentes, multi
                         from costing.sku_contenedor
                        where sku = any(%s::citext[])
                        order by sku, numero desc""", (chunk,)):
                salida.setdefault(str(r["sku"]).lower(), []).append({
                    "numero": int(r["numero"]),
                    "codigo": (r.get("codigo") or "").strip() or None,
                    "nivel": r.get("nivel"),
                    "fuentes": list(r.get("fuentes") or []),
                    "multi": bool(r.get("multi")),
                })
    except Exception as exc:  # noqa: BLE001 — respaldo, nunca tumbar la pantalla
        _fallo(exc)
        return None
    for lista in salida.values():
        lista.sort(key=lambda c: -c["numero"])
    return salida


def de(mapa: dict[str, list[dict[str, Any]]] | None,
       sku: str | None) -> list[dict[str, Any]]:
    """Lo que `por_sku` sabe de un SKU (sin importar mayúsculas), o `[]`
    (también con `mapa` None: la tabla no se pudo leer)."""
    return mapa.get(str(sku or "").strip().lower(), []) if mapa else []


def numeros(forzar: bool = False) -> dict[int, str | None]:
    """
    ``{ numero: código principal }`` de TODOS los contenedores de la tabla.

    Lo necesita Costos para saber qué `n:N` puede filtrar por la tabla y para
    crear la opción de una N que ningún packing list ni costos nombra. 30 s en
    el proceso; `forzar` la rehace. Sin flag o con la tabla caída: `{}`.
    """
    global _cache_numeros
    if not activo() or _ausente():
        return {}
    with _candado:
        ahora = time.monotonic()
        if (not forzar and _cache_numeros is not None
                and ahora - _cache_numeros[0] < _TTL_NUMEROS_S):
            return _cache_numeros[1]
    try:
        filas = sdb.fetch_all(
            """select numero,
                      mode() within group (order by nullif(codigo, '')) as codigo
                 from costing.sku_contenedor
                group by numero""")
    except Exception as exc:  # noqa: BLE001
        _fallo(exc)
        return {}
    salida = {int(f["numero"]): ((f.get("codigo") or "").strip() or None) for f in filas}
    with _candado:
        _cache_numeros = (time.monotonic(), salida)
    return salida


def etiqueta(c: dict[str, Any]) -> str:
    """«CODIGO - N», el formato que ya usan costos y Crear. Sin código: «Contenedor N»."""
    codigo = (c.get("codigo") or "").strip()
    return f"{codigo} - {c['numero']}" if codigo else f"Contenedor {c['numero']}"


def etiquetas(lista: Iterable[dict[str, Any]]) -> str:
    """Varias N en un texto, en el orden recibido. Con lo que da `por_sku`, la N
    mayor primero: «B - 88 / A - 80» (el mismo orden de Inventario y Costos)."""
    return " / ".join(etiqueta(c) for c in lista or ())


def con_etiqueta(lista: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copia de la lista con `etiqueta` en cada contenedor (para el JSON)."""
    return [{**c, "etiqueta": etiqueta(c)} for c in lista or ()]


def limpiar_cache() -> None:
    """Para las pruebas: olvida la caché, los avisos y la marca de tabla ausente."""
    global _cache_numeros, _ausente_hasta
    with _candado:
        _cache_numeros = None
        _ausente_hasta = 0.0
        _avisados.clear()
