"""Economía por unidad en Mercado Libre FULL: funciones PURAS (sin red, sin base).

Fórmula de DISENO.md §4, a un precio P CON IVA:

    comision(P) = P × pct(categoria, tramo(P))      ← sobre el precio CON IVA
    envio(P)    = calc_fee_envio_ml(peso_facturable, P)
    iva(P)      = P − P/1.16
    utilidad(P) = P − comision − envio − iva − costo − costo_full
    margen(P)   = utilidad / P          roi = utilidad / costo

Por qué cada pieza es como es (todo medido el 28-sep-2026, informes de `mapa/`):

- **La comisión va sobre el precio CON IVA.** En 35,028 líneas FULL de 90 días,
  aplicando el % al precio con IVA el cargo fijo implícito sale ≈0 en todos los
  tramos; aplicado sin IVA (como hacen `costos.py:284` y `publicaciones_panel.py:797`)
  sobra un residuo de 5–14 MXN. El panel resta 13.8% menos comisión de la que ML
  cobra y el margen se ve 2–3 puntos mejor de lo que es.
- **El % depende del tramo de precio.** `listing_prices` gold_pro+fulfillment en
  718 categorías (28-sep): el % a $199 es igual al de $399 en las 718, y el de
  $599 igual al de $1,199 en 691. El escalón real está en $500 (19.5% → 16% en
  353 categorías, el patrón más común). Las otras 27 bajan DESPUÉS de $599 (19 de
  ellas son las que ML subió de 19.5% a 21.5% el 13–14-sep: 21.5% hasta $599 y
  16% a $1,199). Por eso hay dos juegos de tramos:

  * el MUESTREO de la API sigue en 4 precios (199/399/599/1199 →
    `cache/comisiones.json` con llaves 0/299/500/1000): si ML mueve un escalón,
    el caché lo trae sin tocar código, y la búsqueda en la tabla de la API usa
    SUS propias llaves (así esas 27 categorías conservan su 21.5% en $500–999);
  * el RESPALDO (categorías sin tabla de la API) usa los tramos de
    `parametros.json` (`tramos_comision_precio` = [0, 500] y
    `comision_respaldo_por_tramo` = 19.5% / 16%), que son los que la API midió.

- **El envío en FULL lo paga el vendedor también debajo de $299** (99% de esos
  pedidos con costo > 0). La tabla `_TARIFA_ML` cuadra con lo cobrado: razón
  real/estimado mediana 1.0 en todos los tramos. Lo que falla es el PESO, por eso
  aquí un peso ausente es ``None`` (``sin_peso``) y no el 0.5 kg de respaldo de
  `costos._peso_efectivo`, que abarata el envío y maquilla el margen.
- La tarifa y sus escalones NO se copian: se importan de `services.costos`
  (`calc_fee_envio_ml`, `_TRAMOS_PRECIO`). Si producción corrige la tabla, el
  laboratorio la hereda. Importar ese módulo arrastra `config`, por eso se hace
  perezoso y después de `_entorno.cargar()` (que solo lee el .env e instala los
  candados; ninguna función de aquí toca la red).

Un dato ausente es ``None`` y la razón va en ``faltan`` (DISENO §0.7): una
utilidad calculada con costo 0 o con peso inventado parece un número y no lo es.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal
from statistics import median
from typing import Any, Iterable, Mapping

IVA = 0.16
# Tramos del RESPALDO (parametros.json `mercado_libre.tramos_comision_precio`).
# Medido en las 718 categorías FULL con la API: el % no cambia en $299 y, en el
# patrón común, tampoco en $1,000; el escalón es $500. Las tablas de la API
# (`cache/comisiones.json`) conservan sus 4 llaves de muestreo y se consultan
# con ESAS llaves (ver `cortes_comision`), no con estas.
TRAMOS_COMISION = (0, 500)
# Respaldo de parametros.json (`mercado_libre.comision_respaldo_por_tramo`). Se
# repite aquí solo para que las funciones sean usables sin leer el archivo; quien
# tenga los parámetros los pasa en `respaldo={"por_tramo": ..., "tramos": ...}`.
# Es la MEDIANA de las 718 categorías de la API en cada tramo (19.5% abajo de
# $500, 16% desde $500) y a la vez el patrón más común (353 categorías). El
# "299": 0.18 que había antes no lo respaldaba la API: el 18% mediano de las
# líneas reales en $299–499 era mezcla de categorías, no un escalón.
COMISION_RESPALDO_POR_TRAMO = {"0": 0.195, "500": 0.16}
# Cortes ESTRICTOS: la llave aplica desde MÁS de ese precio, no desde el precio.
# Medido con `listing_prices` (gold_pro + fulfillment) el 28/29-sep: MLM437181
# cobra 21.5% a $999.99 y a $1,000.00 y 16% a $1,000.01; MLM189697 20% a $1,000 y
# 14.5% a $1,000.01. En $500 no: MLM1055 cobra 12.5% a $499.99 y 11% a $500.00.
# Re-medido en la fase 3 (29-sep 01:4x UTC, 3 GET): MLM437181 $1,000.00 → 21.5%
# ($215) y $1,000.01 → 16% ($160); MLM1055 $500.00 → 11% ($55): $500 inclusivo.
# (Auditoría de números, fase 2: 8 recomendados quedaban exactamente en $1,000
# con la comisión baja — $55 por pieza subestimados en MES-0045-MAD.)
CORTES_ESTRICTOS = frozenset({1000.0})


def _desde(corte: float) -> float:
    """Primer precio (al centavo) al que aplica la llave `corte`."""
    c = float(corte)
    return round(c + 0.01, 2) if c in CORTES_ESTRICTOS else c


def _cubre(corte: float, P: float) -> bool:
    """¿La llave `corte` aplica a `P`? (≥ corte; > corte en los estrictos)."""
    return float(P) >= _desde(corte) - 1e-9

_costos_mod = None


def _costos():
    """`services.costos`, importado una sola vez y después de cargar el entorno."""
    global _costos_mod
    if _costos_mod is None:
        from sandbox_precios import _entorno

        _entorno.cargar()  # idempotente; `services.costos` importa `config`
        from services import costos as _c

        _costos_mod = _c
    return _costos_mod


# ── Tramos y comisión ─────────────────────────────────────────────────────────
def tramo(P: float, tramos: Iterable[float] = TRAMOS_COMISION) -> str:
    """Llave del tramo de un precio dentro de `tramos` (por omisión "0" o "500").

    >>> [tramo(p) for p in (0, 159, 298.99, 299, 499.99, 500, 999, 1000, 5000)]
    ['0', '0', '0', '0', '0', '500', '500', '500', '500']
    >>> [tramo(p, (0, 299, 500, 1000)) for p in (298.99, 299, 499.99, 500, 999, 1000, 1000.01)]
    ['0', '299', '299', '500', '500', '500', '1000']
    """
    ts = sorted(float(t) for t in tramos)
    p = float(P or 0.0)
    return str(int(max((t for t in ts if _cubre(t, p)), default=ts[0])))


def _clave_en(tabla: Mapping[str, Any], P: float) -> str | None:
    """La llave de `tabla` (tramos como texto: "0", "500"…) que cubre a `P`:
    la mayor ≤ P (< P en los `CORTES_ESTRICTOS`: $1,000 cobra aún el % de abajo).
    ``None`` si la tabla está vacía o P queda debajo de todas."""
    mejor, valor = None, None
    p = float(P or 0.0)
    for k in tabla:
        try:
            x = float(k)
        except (TypeError, ValueError):
            continue
        if _cubre(x, p) and (valor is None or x > valor):
            mejor, valor = k, x
    return mejor


def _tramos_respaldo(respaldo: Mapping[str, Any] | None, tramos: Iterable[float]) -> list[float]:
    """Tramos con que se calcularon las tasas reales (`respaldo["tramos"]`) o los
    que se pasen; el respaldo genérico usa las llaves de su propio `por_tramo`."""
    ts = (respaldo or {}).get("tramos") or tramos
    return sorted(float(t) for t in ts)


def _tabla_categoria(comisiones_cache: Mapping[str, Any] | None, categoria: str | None) -> dict:
    """{tramo: pct} de una categoría. Acepta el formato de `cache/comisiones.json`
    ({cat: {"tramos": {...}, ...}}) o uno plano ({cat: {tramo: pct}})."""
    if not comisiones_cache or not categoria:
        return {}
    e = comisiones_cache.get(categoria)
    if not isinstance(e, Mapping):
        return {}
    t = e.get("tramos") if "tramos" in e else e
    return {str(k): float(v) for k, v in (t or {}).items()
            if v is not None and _es_pct(v)}


def _es_pct(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return 0.0 < x < 1.0


def pct_comision(categoria: str | None, P: float,
                 comisiones_cache: Mapping[str, Any] | None = None,
                 respaldo: Mapping[str, Any] | None = None,
                 tramos: Iterable[float] = TRAMOS_COMISION) -> tuple[float | None, str | None]:
    """% de comisión (decimal) de ML FULL gold_pro para `categoria` a precio `P`.

    Devuelve ``(pct, fuente)`` con fuente, en orden de preferencia:

    - ``"api"``: `listing_prices` de la categoría (`cache/comisiones.json`),
      buscado con las llaves de SU tabla (los 4 precios de muestreo).
    - ``"real_orders"``: mediana de lo que ML cobró de verdad en esa categoría y
      tramo (`respaldo["real_orders"]`, ver :func:`tasas_reales`), con los tramos
      de ``respaldo["tramos"]`` (o `tramos`).
    - ``"respaldo"``: % genérico por tramo (`respaldo["por_tramo"]`, o el de
      parametros.json si no se pasa nada); sus llaves son sus tramos.

    ``(None, None)`` solo si ni el respaldo cubre el precio. Un pct 0 nunca se
    acepta: en costos_finales hay 307 filas con pct=0 que el panel toma por
    válidas y le inflan el margen a 7 activas.

    >>> c4 = {"MLMX": {"tramos": {"0": 0.215, "299": 0.215, "500": 0.215, "1000": 0.16}}}
    >>> [pct_comision("MLMX", p, c4) for p in (159, 700, 1200)]
    [(0.215, 'api'), (0.215, 'api'), (0.16, 'api')]
    >>> pct_comision("MLMX", 600, {}, {"real_orders": {"MLMX": {"500": 0.155}}})
    (0.155, 'real_orders')
    >>> [pct_comision(None, p) for p in (350, 600)]
    [(0.195, 'respaldo'), (0.16, 'respaldo')]
    """
    api = _tabla_categoria(comisiones_cache, categoria)
    if api:
        k = _clave_en(api, P)
        if k is not None:
            return api[k], "api"
    resp = respaldo or {}
    reales = (resp.get("real_orders") or {}).get(categoria or "") or {}
    if reales:
        t = tramo(P, _tramos_respaldo(resp, tramos))
        if _es_pct(reales.get(t)):
            return float(reales[t]), "real_orders"
    generico = {str(k): v for k, v in (resp.get("por_tramo") or COMISION_RESPALDO_POR_TRAMO).items()
                if _es_pct(v)}
    k = _clave_en(generico, P)
    if k is not None:
        return float(generico[k]), "respaldo"
    return None, None


def cortes_comision(categoria: str | None, comisiones_cache: Mapping[str, Any] | None = None,
                    respaldo: Mapping[str, Any] | None = None,
                    tramos: Iterable[float] = TRAMOS_COMISION) -> list[float]:
    """Todos los precios donde el % de la categoría PUEDE cambiar: las llaves de su
    tabla de la API, los tramos de las tasas reales y los del respaldo genérico.
    Entre dos cortes consecutivos `pct_comision` es constante.

    >>> cortes_comision("MLMX", {"MLMX": {"tramos": {"0": 0.2, "299": 0.2, "500": 0.2, "1000": 0.16}}})
    [0.0, 299.0, 500.0, 1000.0]
    >>> cortes_comision(None)
    [0.0, 500.0]
    """
    resp = respaldo or {}
    cs = {0.0}
    cs.update(float(k) for k in _tabla_categoria(comisiones_cache, categoria))
    cs.update(_tramos_respaldo(resp, tramos))
    for k in (resp.get("por_tramo") or COMISION_RESPALDO_POR_TRAMO):
        try:
            cs.add(float(k))
        except (TypeError, ValueError):
            pass
    return sorted(cs)


def tabla_comision(categoria: str | None, comisiones_cache: Mapping[str, Any] | None = None,
                   respaldo: Mapping[str, Any] | None = None,
                   tramos: Iterable[float] = TRAMOS_COMISION) -> dict[str, tuple[float | None, str | None]]:
    """{corte: (pct, fuente)} de una categoría, para rejillas: se busca una vez y
    se reusa en los cientos de precios que evalúa el optimizador. Las llaves son
    los `cortes_comision` (no solo los tramos del respaldo): así una categoría
    con el escalón en $1,000 no se aplana. Se consulta con :func:`pct_de_tabla`."""
    return {str(int(c)): pct_comision(categoria, _desde(c), comisiones_cache, respaldo, tramos)
            for c in cortes_comision(categoria, comisiones_cache, respaldo, tramos)}


def pct_de_tabla(tabla: Mapping[str, tuple[float | None, str | None]], P: float
                 ) -> tuple[float | None, str | None]:
    """(pct, fuente) de `tabla_comision` para el precio `P`.

    >>> t = tabla_comision("MLMX", {"MLMX": {"tramos": {"0": 0.215, "299": 0.215, "500": 0.215, "1000": 0.16}}})
    >>> [pct_de_tabla(t, p)[0] for p in (99, 499.99, 999.99, 1000, 1000.01)]
    [0.215, 0.215, 0.215, 0.215, 0.16]
    """
    k = _clave_en(tabla, P)
    return tabla[k] if k is not None else (None, None)


# ── Envío FULL ────────────────────────────────────────────────────────────────
def envio_full(P: float, peso_facturable_kg: float | None) -> float | None:
    """Lo que ML le cobra al vendedor por el envío FULL de una pieza a precio `P`.

    ``None`` si no hay peso: el peso de respaldo de producción (0.5 kg) abarata
    el envío y el margen sale optimista.

    Casos medidos (`shipping_options/free`, 454 g facturables):
    >>> [envio_full(p, 0.454) for p in (99, 250, 299, 600)]
    [34.0, 38.0, 56.0, 70.0]
    >>> envio_full(99, None) is None
    True
    """
    if peso_facturable_kg is None:
        return None
    try:
        kg = float(peso_facturable_kg)
    except (TypeError, ValueError):
        return None
    if not kg > 0:
        return None
    return float(_costos().calc_fee_envio_ml(kg, float(P)))


def envio_ml(P: float, peso_facturable_kg: float | None, es_full: bool = True,
             umbral_envio_gratis: float = 299.0, ancla: float | None = None) -> float | None:
    """Envío a cargo del vendedor en ML. FULL: siempre. Fuera de FULL: solo desde
    `umbral_envio_gratis` (abajo lo paga el comprador).

    ``ancla`` = precio REGULAR de una promoción. Debajo de $299 con un regular de
    $299 o más, ML cobró la columna del regular en ~30% de las ventas de 30 días
    (auditoría de números, fase 2: 187 de 510 con lista ≥ $299 contra 10 de 157
    con lista < $299; MUE-0163-TEL vendido a $176.54 pagó $88, el modelo decía
    $53). No se sabe cuándo aplica (depende del tipo de promoción), así que el
    laboratorio toma el PEOR caso: con ancla, abajo de $299 se cobra la columna
    del regular.

    >>> envio_ml(250, 0.454), envio_ml(250, 0.454, ancla=369), envio_ml(350, 0.454, ancla=369)
    (38.0, 56.0, 56.0)

    Solo aplica con P < $299 Y regular ≥ $299: un regular de $289 no cambia de
    columna, a $299 o más manda la columna del propio P, y fuera de FULL abajo
    de $299 el envío lo paga el comprador aunque haya ancla.
    >>> envio_ml(250, 0.454, ancla=289), envio_ml(298.99, 0.454, ancla=299), envio_ml(299, 0.454, ancla=1200)
    (38.0, 56.0, 56.0)
    >>> envio_ml(250, 0.454, es_full=False, ancla=369), envio_ml(99, 0.454, ancla=150)
    (0.0, 34.0)
    """
    if not es_full and float(P) < umbral_envio_gratis:
        return 0.0
    if ancla is not None and float(ancla) >= umbral_envio_gratis and float(P) < umbral_envio_gratis:
        return envio_full(float(ancla), peso_facturable_kg)
    return envio_full(P, peso_facturable_kg)


def escalones_envio() -> list[float]:
    """Primer precio (al centavo) de cada columna de `_TARIFA_ML` después de la
    primera: 99, 199, 299, 499, 999 con la tabla de 2026-07. Se derivan de
    `_TRAMOS_PRECIO` para no copiar la tabla."""
    return [round(float(t) + 0.01, 2) for t in _costos()._TRAMOS_PRECIO]


# ── Utilidad a un precio ──────────────────────────────────────────────────────
def iva_de(P: float, iva: float = IVA) -> float:
    return float(P) - float(P) / (1.0 + iva)


def comision_de(P: float, pct: float) -> float:
    """Comisión por pieza como la cobra ML: P × pct redondeado al centavo PAR.

    Medido en 14,812 líneas FULL donde los tres redondeos difieren: el
    medio-a-par (bancario) acierta 12,702; medio-hacia-arriba 8,519; truncar
    4,183. Con `round()` de float, $159 × 19.5% = 31.005000000000003 → 31.01, y
    ML cobró 31.00. Por eso se hace en Decimal desde el texto del número.
    """
    return float((Decimal(str(float(P))) * Decimal(str(float(pct))))
                 .quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def utilidad(P: float, costo: float | None, categoria: str | None, peso: float | None,
             costo_full: float = 0.0, *,
             comisiones_cache: Mapping[str, Any] | None = None,
             respaldo: Mapping[str, Any] | None = None,
             pct: float | None = None, es_full: bool = True,
             iva: float = IVA, ancla_envio: float | None = None) -> dict[str, Any]:
    """Desglose de una pieza vendida a `P` (con IVA). `costo` SIN IVA.
    ``ancla_envio``: precio regular de la promoción (ver :func:`envio_ml`).

    ``pct`` fuerza la comisión (p. ej. la de `tabla_comision`); si no, se busca
    con :func:`pct_comision`. Devuelve siempre las mismas llaves; lo que no se
    puede calcular va en ``None`` y el motivo en ``faltan``
    (``sin_costo`` | ``sin_peso`` | ``sin_comision``).

    Caso medido: a $159 en una categoría de 19.5% ML cobró $31.00.
    >>> r = utilidad(159, 40.0, "MLMX", 0.454, comisiones_cache={"MLMX": {"0": 0.195}})
    >>> (r["comision"], r["envio"], r["iva"], r["utilidad"], r["margen"])
    (31.0, 34.0, 21.93, 32.07, 0.2017)
    >>> utilidad(159, None, "MLMX", 0.454)["faltan"]
    ['sin_costo']
    """
    P = float(P)
    fuente = "forzado" if pct is not None else None
    if pct is None:
        pct, fuente = pct_comision(categoria, P, comisiones_cache, respaldo)
    envio = envio_ml(P, peso, es_full, ancla=ancla_envio)
    iva_m = iva_de(P, iva)
    com = comision_de(P, pct) if pct is not None else None
    faltan = []
    if costo is None:
        faltan.append("sin_costo")
    if envio is None:
        faltan.append("sin_peso")
    if com is None:
        faltan.append("sin_comision")
    util = None
    if not faltan:
        util = P - com - envio - iva_m - float(costo) - float(costo_full or 0.0)
    return {
        "precio": round(P, 2),
        "pct": pct, "fuente_pct": fuente,
        "comision": None if com is None else round(com, 2),
        "envio": None if envio is None else round(envio, 2),
        "iva": round(iva_m, 2),
        "costo": None if costo is None else round(float(costo), 4),
        "costo_full": round(float(costo_full or 0.0), 2),
        "utilidad": None if util is None else round(util, 2),
        "margen": None if (util is None or P <= 0) else round(util / P, 4),
        "roi": None if (util is None or not costo) else round(util / float(costo), 4),
        "faltan": faltan,
    }


# ── Precios objetivo ──────────────────────────────────────────────────────────
def _segmentos(cortes: Iterable[float], tope: float) -> list[tuple[float, float]]:
    """Intervalos [a, b) del centavo donde la comisión y el envío son constantes.
    `cortes` = `cortes_comision` de la categoría (incluye los de su tabla de la
    API: una categoría con escalón en $1,000 no queda con el de $500)."""
    cs = {0.01, float(tope)}
    cs.update(_desde(t) for t in cortes if 0 < float(t) < tope)
    cs.update(e for e in escalones_envio() if 0 < e < tope)
    orden = sorted(cs)
    return list(zip(orden[:-1], orden[1:]))


def precio_para_margen(objetivo: float, costo: float | None, categoria: str | None,
                       peso: float | None, costo_full: float = 0.0, *,
                       comisiones_cache: Mapping[str, Any] | None = None,
                       respaldo: Mapping[str, Any] | None = None,
                       es_full: bool = True, iva: float = IVA,
                       tope: float = 100_000.0,
                       tramos_comision: Iterable[float] = TRAMOS_COMISION,
                       ancla_envio: float | None = None) -> float | None:
    """El MENOR precio (al centavo) con ``margen ≥ objetivo``. ``None`` si no existe
    debajo de `tope` o falta costo/peso. No depende de ninguna rejilla: puede caer
    muy abajo (o muy arriba) del precio actual.

    Respeta los escalones: el margen NO es monótono en P (el envío salta en $299
    y el margen cae; la comisión baja en $500 y el margen sube), así que una
    bisección simple sobre todo el rango puede saltarse la respuesta o caer en
    un precio que no cumple. Dentro de cada intervalo donde comisión y envío son
    constantes, ``margen(P) = 1 − pct − iva/(1+iva) − (envío+costo+full)/P`` sí
    es creciente; ahí la bisección se resuelve en forma cerrada:

        P* = (envío + costo + full) / (1 − pct − iva/(1+iva) − objetivo)

    y se recorren los intervalos de menor a mayor hasta el primero que cumple.

    >>> c = {"MLMX": {"0": 0.195, "299": 0.195, "500": 0.16, "1000": 0.16}}
    >>> p = precio_para_margen(0.0, 40.0, "MLMX", 0.454, comisiones_cache=c)
    >>> p, utilidad(p, 40.0, "MLMX", 0.454, comisiones_cache=c)["utilidad"] >= 0
    (110.94, True)
    >>> # A 20% el precio cae arriba de $299: allí el envío sube a $56.
    >>> precio_para_margen(0.20, 150.0, "MLMX", 0.454, comisiones_cache=c)
    441.05
    """
    if costo is None:
        return None
    base = float(costo) + float(costo_full or 0.0)
    fr_iva = iva / (1.0 + iva)
    obj = float(objetivo)
    cortes = cortes_comision(categoria, comisiones_cache, respaldo, tramos_comision)
    for a, b in _segmentos(cortes, tope):
        pct, _ = pct_comision(categoria, a, comisiones_cache, respaldo, tramos_comision)
        env = envio_ml(a, peso, es_full, ancla=ancla_envio)
        if pct is None or env is None:
            return None
        den = 1.0 - pct - fr_iva - obj
        if den <= 0:
            continue  # en este tramo ningún precio alcanza el objetivo
        p = max(a, math.ceil((env + base) / den * 100.0 - 1e-9) / 100.0)
        if p >= b:
            continue

        def _cumple(x: float) -> bool:
            # Mismo cálculo que `utilidad`, sin redondear la utilidad: la
            # comisión SÍ va redondeada al centavo par, como la cobra ML, y ese
            # redondeo puede hacer que un centavo abajo de P* ya cumpla.
            u = x - comision_de(x, pct) - env - iva_de(x, iva) - base
            return u >= obj * x - 1e-9

        # P* es exacto salvo por el redondeo de la comisión: se ajusta ±3 centavos.
        cands = [round(p + d / 100.0, 2) for d in range(-3, 4)]
        for x in cands:
            if a <= x < b and _cumple(x):
                return x
    return None


def precio_equilibrio(costo: float | None, categoria: str | None, peso: float | None,
                      costo_full: float = 0.0, **kw: Any) -> float | None:
    """El menor precio con utilidad ≥ 0 (margen 0)."""
    return precio_para_margen(0.0, costo, categoria, peso, costo_full, **kw)


# ── Respaldo con lo que ML cobró de verdad ────────────────────────────────────
def tasas_reales(lineas: Iterable[Mapping[str, Any]], *, min_lineas: int = 5,
                 solo_full: bool = True,
                 categoria_de_item: Mapping[str, str] | None = None,
                 tramos: Iterable[float] = TRAMOS_COMISION) -> dict[str, dict[str, float]]:
    """{categoria: {tramo: pct mediano real}} desde `channel.order_items`.

    pct real = comision_de_la_linea / (precio_unitario × cantidad). Solo líneas
    con comisión > 0 (1,290 del día en curso aún vienen en 0) y, por omisión,
    solo FULL. ``categoria_de_item`` completa las líneas sin ``category_id``
    (24% en el crudo de kubera: sus publicaciones no tienen fila en
    `channel.listings`); se llena con `ml_universo`. Los tramos deben ser los
    mismos que después se pasen en ``respaldo["tramos"]``.
    """
    ts = list(tramos)
    grupos: dict[tuple[str, str], list[float]] = {}
    for l in lineas:
        if solo_full and not l.get("es_fulfillment"):
            continue
        try:
            pu, q, c = float(l.get("precio_unitario") or 0), float(l.get("cantidad") or 0), float(l.get("comision") or 0)
        except (TypeError, ValueError):
            continue
        if pu <= 0 or q <= 0 or c <= 0:
            continue
        cat = l.get("category_id") or (categoria_de_item or {}).get(l.get("item_id") or "")
        if not cat:
            continue
        grupos.setdefault((cat, tramo(pu, ts)), []).append(c / (pu * q))
    out: dict[str, dict[str, float]] = {}
    for (cat, t), xs in grupos.items():
        if len(xs) >= min_lineas:
            out.setdefault(cat, {})[t] = round(median(xs), 4)
    return out


def respaldo_de(parametros_ml: Mapping[str, Any] | None, reales: Mapping[str, Any] | None = None,
                tramos_reales: Iterable[float] | None = None) -> dict[str, Any]:
    """El dict ``respaldo`` que esperan estas funciones, armado desde
    `parametros.json["mercado_libre"]` y `ultimo/comisiones_reales.json`.

    ``tramos_reales`` son los tramos con que se calcularon las tasas reales (el
    archivo los guarda en su llave ``tramos``); si no vienen, los de parámetros.
    """
    ml = parametros_ml or {}
    tramos = list(tramos_reales or ml.get("tramos_comision_precio") or TRAMOS_COMISION)
    return {"real_orders": dict(reales or {}),
            "por_tramo": ml.get("comision_respaldo_por_tramo") or dict(COMISION_RESPALDO_POR_TRAMO),
            "tramos": tramos}


def respaldo_desde_doc(parametros_ml: Mapping[str, Any] | None,
                       doc_reales: Mapping[str, Any] | None) -> dict[str, Any]:
    """:func:`respaldo_de` con el documento `ultimo/comisiones_reales.json` ya leído.
    Un archivo sin llave ``tramos`` es anterior al 28-sep y se calculó con los 4
    tramos de muestreo: se lee con esos, no con los del respaldo."""
    doc = doc_reales or {}
    cats = doc.get("categorias") or {}
    tr = doc.get("tramos") or ((0, 299, 500, 1000) if cats else None)
    return respaldo_de(parametros_ml, cats, tr)


# ── Autoprueba ────────────────────────────────────────────────────────────────
def _autoprueba() -> None:
    """Casos medidos en los informes del 28-sep; revienta si alguno no cuadra."""
    import doctest
    import sys

    fallas, _ = doctest.testmod(sys.modules[__name__], verbose=False)
    c = {"MLMX": {"tramos": {"0": 0.195, "299": 0.195, "500": 0.16, "1000": 0.16}}}
    # $159 en categoría de 19.5% → ML cobró $31.00 (sale_fee_amount medido).
    assert utilidad(159, 1.0, "MLMX", 0.454, comisiones_cache=c)["comision"] == 31.0
    # 454 g facturables: $99→34, $250→38, $299→56, $600→70 (shipping_options/free).
    assert [envio_full(p, 0.454) for p in (99, 250, 299, 600)] == [34.0, 38.0, 56.0, 70.0]
    # Escalón de envío en $299: 298.99 paga 38 y 299 paga 56.
    assert (envio_full(298.99, 0.454), envio_full(299, 0.454)) == (38.0, 56.0)
    # Escalón de comisión en $500: 499.99 al 19.5% y 500 al 16%.
    assert pct_comision("MLMX", 499.99, c)[0] == 0.195 and pct_comision("MLMX", 500, c)[0] == 0.16
    # Respaldo genérico con los tramos medidos: el escalón está en $500, no en $299.
    assert pct_comision(None, 350)[0] == 0.195 and pct_comision(None, 500)[0] == 0.16
    # Categoría de las que subieron a 21.5% (13–14-sep): el escalón está después de $599.
    c21 = {"MLM11041": {"tramos": {"0": 0.215, "299": 0.215, "500": 0.215, "1000": 0.16}}}
    assert [pct_comision("MLM11041", p, c21)[0] for p in (199, 599, 999.99, 1000, 1000.01)] ==         [0.215, 0.215, 0.215, 0.215, 0.16]
    # Escalón de $1,000 ESTRICTO (medido): $1,000.00 aún cobra el % de abajo.
    assert pct_comision("MLMX", 1000, c)[0] == 0.16 and pct_comision("MLM11041", 1000, c21)[0] == 0.215
    # Re-medido 29-sep (listing_prices): MLM437181 $1,000.00 → 21.5%, $1,000.01 → 16%;
    # MLM1055 $500.00 → 11% (12.5% a $499.99): $500 INCLUSIVO.
    c437 = {"MLM437181": {"tramos": {"0": 0.215, "299": 0.215, "500": 0.215, "1000": 0.16}}}
    assert [pct_comision("MLM437181", p, c437)[0] for p in (1000.0, 1000.01)] == [0.215, 0.16]
    c1055 = {"MLM1055": {"tramos": {"0": 0.125, "299": 0.125, "500": 0.11, "1000": 0.11}}}
    assert [pct_comision("MLM1055", p, c1055)[0] for p in (499.99, 500.0)] == [0.125, 0.11]
    assert tramo(1000.0, (0, 299, 500, 1000)) == "500" and tramo(1000.01, (0, 299, 500, 1000)) == "1000"
    assert tramo(500.0) == "500" and tramo(499.99) == "0"
    # El precio para margen cumple el margen y el centavo anterior no (en su tramo).
    for cache, cat in ((c, "MLMX"), (c21, "MLM11041"), ({}, "SIN_TABLA")):
        for obj, costo in ((0.0, 40.0), (0.12, 80.0), (0.20, 150.0), (0.20, 300.0), (0.30, 25.0), (0.12, 700.0)):
            p = precio_para_margen(obj, costo, cat, 0.454, comisiones_cache=cache)
            assert p is not None
            m = utilidad(p, costo, cat, 0.454, comisiones_cache=cache)["margen"]
            assert m >= obj - 1e-4, (cat, obj, costo, p, m)
            # Ningún precio más bajo cumple (barrido al centavo hasta p).
            # (utilidad sin redondear: la de `utilidad()` va al centavo y −0.0007 sale 0.0)
            q = 0.01
            while q < p - 0.005:
                pq = pct_comision(cat, q, cache)[0]
                u = q - comision_de(q, pq) - envio_full(q, 0.454) - iva_de(q) - costo
                assert u < obj * q - 1e-9, (cat, obj, costo, q, p)
                q = round(q + (0.01 if q > p - 2 else 0.37), 2)
    # La tabla por cortes da lo mismo que la búsqueda directa en todo el rango.
    for cache, cat in ((c, "MLMX"), (c21, "MLM11041"), ({}, None)):
        t = tabla_comision(cat, cache)
        for p in (1, 150, 298.99, 299, 499.99, 500, 599, 999.99, 1000, 1000.01, 5000):
            assert pct_de_tabla(t, p) == pct_comision(cat, p, cache), (cat, p)
    # Con ancla de promoción: abajo de $299 el envío es el de la columna del regular
    # y el precio para margen lo respeta (utilidad ≥ objetivo con ESE envío).
    for obj, costo in ((0.0, 40.0), (0.12, 80.0), (0.12, 20.0)):
        p = precio_para_margen(obj, costo, "MLMX", 0.454, comisiones_cache=c, ancla_envio=369.0)
        m = utilidad(p, costo, "MLMX", 0.454, comisiones_cache=c, ancla_envio=369.0)["margen"]
        assert m >= obj - 1e-4, (obj, costo, p, m)
        assert utilidad(round(p - 0.01, 2), costo, "MLMX", 0.454, comisiones_cache=c,
                        ancla_envio=369.0)["margen"] < obj + 1e-4 or p < 299
    # Sin peso → sin precio (no se inventa el envío).
    assert precio_equilibrio(40.0, "MLMX", None, comisiones_cache=c) is None
    if fallas:
        raise AssertionError(f"{fallas} doctests fallaron")
    print("economia: autoprueba OK")


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sandbox_precios import _entorno

    _entorno.cargar()
    _autoprueba()
