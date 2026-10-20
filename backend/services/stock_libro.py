"""
El número vendible desde el LIBRO de kubera (`FANOUT_FUENTE=kubera`).

── POR QUÉ ────────────────────────────────────────────────────────────────────
Odoo salió de toda la operación el 15-oct-2026 y, desde T0, el físico de las
bodegas lo lleva el libro de kubera (`almacen.*`, sembrado de la copia de Odoo).
La ruta directa del plan de salida de Woo (Eduardo, 20-oct) saca a Woo del
camino del stock en ese mismo acto: el fan-out deja de leer el `_stock` de Woo
y lee este número.

── EL NÚMERO ───────────────────────────────────────────────────────────────────
    vendible(SKU) = max(0, Σ libre_vendible − pendientes)

  · `Σ libre_vendible`: de la vista del libro (`STOCK_LIBRO_VISTA`, por omisión
    `almacen.existencias_bodega_v`, contrato de la migración A de MAESTRO), SOLO
    de las bodegas con `fuente = 'kubera'` y `cuenta_para_woo`. La vista da una
    fila por (sku, almacen, fuente, grupo) —bodega, cuarentena, scrap, tránsito—
    y fuera de «bodega» `libre_vendible` es 0 o NULL: sumarlas da bien. Nunca
    `fisico`, `libre` ni `stock_almacen.libre`: incluyen lo no vendible.
  · `pendientes`: piezas de ventas DROP (renglón no FULL de `channel.order_items`)
    creadas desde `STOCK_LIBRO_DESDE`, no canceladas (`estado_wc`: medido el 20-oct,
    ML, TikTok y Amazon lo marcan; Temu se queda en `processing`), cuya orden del marketplace
    todavía no tiene una OV confirmada o entregada (`ventas.ov_ordenes.mp_canal`
    + `mp_cuenta` + `mp_orden`, la misma liga que `ordenes_venta._venta_en_canal`;
    un id de ML puede existir en dos cuentas). El diario de arranque de T0 asienta
    cada venta posterior al corte como OV espejo CON esa liga (MAESTRO, 20-oct):
    por eso no se resta dos veces. Una OV confirmada ya apartó en el libro: restarla otra vez
    escondería la pieza dos veces. El encabezado de `channel.orders` no sirve
    para FULL (`es_fulfillment` quedó en false desde el 14-ago): manda el renglón.

  · `STOCK_LIBRO_DESDE` NO es T0: es el instante hasta el que el libro YA refleja
    las ventas. Si T0 siembra el saldo de la copia de Odoo del corte (15-oct
    16:48:47 UTC) sin asentar lo vendido después, es el corte; si T0 asienta esas
    ventas (con su OV o como salida), es T0. Lo fija quien hace el
    desprendimiento (MAESTRO). Equivocarse hacia T0 vuelve a ofrecer lo vendido
    entre el corte y T0; hacia el corte, lo esconde dos veces.
  · Sin ventana de caducidad: una venta sin OV sigue restando mientras no tenga
    OV, porque el libro no la descontará de otra forma.

  · SKU que el libro no trae → **0** (D4, Eduardo, 21-oct: lo que el libro no
    conoce sale en 0 salvo lista aprobada; lo que tiene piezas y no está en la
    lista entra al libro en `por_verificar`). Con el libro disponible, «no lo conozco» nunca deja en el
    canal el número viejo: eso era sobreventa (un SKU vendido hasta 0 desaparecía
    de la vista antes de la B′). `todos()` sí devuelve sólo los que conoce: la
    pasada de `stock_watch` trata al que SALE del libro como un cambio a 0.

── FALLA CERRADA ───────────────────────────────────────────────────────────────
`LibroNoDisponible` (y nada de caer a Woo) si: la fuente tiene una errata, falta
`STOCK_LIBRO_DESDE`, la vista no existe, ninguna bodega del libro cuenta para la
venta (T0 todavía no ocurre), alguna de esas bodegas no trae NINGUNA fila en la
vista, el libro trae menos de `STOCK_LIBRO_MIN_SKUS` SKUs con piezas (vacío o a
medio cargar: con D4 un libro así pondría en 0 todo lo que no alcanzó a cargar) o
la base falla. El piso por bodega atrapa lo que el global no ve: dos bodegas
completas pasan solas los 4,000 y la tercera, ausente, mandaría sus SKUs a 0. Con
la B′ una bodega cargada siempre trae filas (también las de 0), así que una bodega
que cuenta para la venta sin existencias todavía no se enciende. El fan-out que la recibe no
reparte ese SKU: «no sé» nunca se vuelve un 0 ni un número viejo.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from config import settings

FUENTES = ("woo", "kubera")
_VISTA_OK = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


class LibroNoDisponible(RuntimeError):
    """El libro no se puede leer con confianza: el fan-out no reparte con él."""


def fuente_fanout() -> str:
    """`woo` | `kubera` (`FANOUT_FUENTE`). Vacía → `woo`. Una errata LANZA."""
    f = str(getattr(settings, "fanout_fuente", "woo") or "").strip().lower() or "woo"
    if f not in FUENTES:
        raise LibroNoDisponible(f"FANOUT_FUENTE={f!r} no existe (woo | kubera): el fan-out no lee")
    return f


def lee_libro() -> bool:
    """¿El fan-out reparte el número del libro? Con una errata también es «sí»
    —para que el lector se NIEGUE en vez de leer Woo sin que nadie lo pidiera—."""
    try:
        return fuente_fanout() == "kubera"
    except LibroNoDisponible:
        return True


def desde() -> datetime:
    """`STOCK_LIBRO_DESDE` (ISO 8601 con zona): hasta dónde el libro ya refleja las
    ventas (el corte o T0; ver el encabezado)."""
    crudo = str(getattr(settings, "stock_libro_desde", "") or "").strip()
    if not crudo:
        raise LibroNoDisponible("falta STOCK_LIBRO_DESDE (hasta dónde el libro ya refleja las "
                                "ventas): sin él no se sabe qué ventas DROP faltan por apartar")
    try:
        t = datetime.fromisoformat(crudo.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LibroNoDisponible(f"STOCK_LIBRO_DESDE={crudo!r} no es una fecha ISO: {exc}") from exc
    if t.tzinfo is None:
        raise LibroNoDisponible(f"STOCK_LIBRO_DESDE={crudo!r} no trae zona horaria")
    return t


def _vista() -> str:
    v = str(getattr(settings, "stock_libro_vista", "") or "almacen.existencias_bodega_v").strip()
    if not _VISTA_OK.match(v):
        raise LibroNoDisponible(f"STOCK_LIBRO_VISTA={v!r} no es esquema.vista")
    return v


_SQL_BODEGAS = """
    select codigo from almacen.almacenes where fuente = 'kubera' and cuenta_para_woo"""

_SQL_LIBRE = """
    select upper(b.sku::text) as sku, sum(b.libre_vendible)::bigint as libre
      from {vista} b
      join almacen.almacenes a on a.codigo = b.almacen
     where a.fuente = 'kubera' and a.cuenta_para_woo and b.fuente = 'kubera'
       {filtro}
     group by 1"""

_SQL_PENDIENTES = """
    select upper(i.sku::text) as sku, sum(i.cantidad)::bigint as pzs
      from channel.order_items i
      join channel.orders o
        on o.canal = i.canal and o.cuenta = i.cuenta and o.external_order_id = i.external_order_id
     where o.creado_at >= %(desde)s
       and not coalesce(i.es_fulfillment, false)
       and coalesce(o.estado_wc, '') <> 'cancelled'
       and i.sku is not null and coalesce(i.cantidad, 0) > 0
       {filtro}
       and not exists (
             select 1 from ventas.ov_ordenes v
              where v.mp_canal = o.canal and v.mp_orden = o.external_order_id
                and v.mp_cuenta = upper(btrim(o.cuenta))
                and v.estado in ('confirmada', 'entregada'))
     group by 1"""


def desglose(skus: list[str] | None = None) -> dict[str, dict[str, int | None]]:
    """{SKU en mayúsculas: {"libre": Σ libre_vendible (None si el libro no lo
    conoce), "pendientes": piezas DROP sin OV}}: las dos mitades del número, para
    la comparación de T0. Trae también los SKUs vendidos que el libro no conoce.
    LANZA `LibroNoDisponible` si no se le puede creer (ver el encabezado)."""
    from services import supabase_db as sdb
    t0 = desde()
    vista = _vista()
    try:
        if not sdb.fetch_one("select to_regclass(%s) as r", (vista,)).get("r"):
            raise LibroNoDisponible(f"la vista del libro {vista} no existe (¿falta la migración A?)")
        if not sdb.fetch_all(_SQL_BODEGAS):
            raise LibroNoDisponible("ninguna bodega del libro (fuente kubera) cuenta para la venta: "
                                    "T0 todavía no ocurre")
        params: dict[str, Any] = {"desde": t0}
        filtro_l = filtro_p = ""
        if skus is not None:
            params["s"] = sorted({str(s).upper() for s in skus})
            filtro_l = "and upper(b.sku::text) = any(%(s)s)"
            filtro_p = "and upper(i.sku::text) = any(%(s)s)"
        libre = {r["sku"]: int(r["libre"] or 0)
                 for r in sdb.fetch_all(_SQL_LIBRE.format(vista=vista, filtro=filtro_l), params)}
        _revisar_cobertura(sdb, vista, libre if skus is None else None)
        pend = {r["sku"]: int(r["pzs"] or 0)
                for r in sdb.fetch_all(_SQL_PENDIENTES.format(filtro=filtro_p), params)}
    except LibroNoDisponible:
        raise
    except Exception as exc:  # noqa: BLE001 — la base falló: «no sé», no 0
        raise LibroNoDisponible(f"no se pudo leer el libro: {exc}") from exc
    return {s: {"libre": libre.get(s), "pendientes": pend.get(s, 0)} for s in set(libre) | set(pend)}


_SQL_COBERTURA = """
    select count(*) filter (where libre > 0) as con_piezas from (
      select upper(b.sku::text) as sku, sum(b.libre_vendible) as libre
        from {vista} b
        join almacen.almacenes a on a.codigo = b.almacen
       where a.fuente = 'kubera' and a.cuenta_para_woo and b.fuente = 'kubera'
       group by 1) t"""

# Filas de la vista por bodega que cuenta para la venta: una en 0 = no cargada.
_SQL_FILAS_BODEGA = """
    select a.codigo, count(b.sku) as filas
      from almacen.almacenes a
      left join {vista} b on b.almacen = a.codigo and b.fuente = 'kubera'
     where a.fuente = 'kubera' and a.cuenta_para_woo
     group by 1"""

# La cobertura se mide a lo más una vez cada `_COBERTURA_S` por proceso: el fan-out lee
# SKU por SKU (una venta, una vuelta del seguro) y no puede contar el libro en cada uno.
_COBERTURA_S = 120
_cobertura: dict[str, Any] = {}


def _minimo() -> int:
    return int(getattr(settings, "stock_libro_min_skus", 4000) or 0)


def _revisar_cobertura(sdb: Any, vista: str, libre: dict[str, int] | None) -> None:
    """LANZA si alguna bodega que cuenta para la venta no trae filas, o si el libro
    trae menos SKUs con piezas que `STOCK_LIBRO_MIN_SKUS`. `libre` = la lectura
    completa (los SKUs con piezas se cuentan de ahí, sin otra consulta)."""
    minimo = _minimo()
    ahora = time.time()
    fresca = _cobertura.get("vista") == vista and ahora - _cobertura.get("t", 0) < _COBERTURA_S
    if fresca and libre is None:
        n, vacias = _cobertura["n"], _cobertura["vacias"]
    else:
        vacias = sorted(r["codigo"] for r in sdb.fetch_all(_SQL_FILAS_BODEGA.format(vista=vista))
                        if not int(r["filas"] or 0))
        if libre is not None:
            n = sum(1 for v in libre.values() if v > 0)
        elif minimo > 0:
            n = int((sdb.fetch_one(_SQL_COBERTURA.format(vista=vista)) or {}).get("con_piezas") or 0)
        else:
            n = 0
        _cobertura.update(vista=vista, t=ahora, n=n, vacias=vacias)
    if vacias:
        raise LibroNoDisponible(f"{', '.join(vacias)} cuenta(n) para la venta y no trae(n) ninguna fila en "
                                f"{vista}: libro a medio cargar (con D4 sus SKUs irían a 0)")
    if minimo > 0 and n < minimo:
        raise LibroNoDisponible(f"el libro trae {n:,} SKUs con piezas y el mínimo es {minimo:,} "
                                f"(STOCK_LIBRO_MIN_SKUS): vacío o a medio cargar")


def _leer(skus: list[str] | None) -> dict[str, int]:
    """{SKU en mayúsculas: vendible} de los SKUs que el libro conoce."""
    return {s: max(0, d["libre"] - d["pendientes"])
            for s, d in desglose(skus).items() if d["libre"] is not None}


def numero(sku: str) -> int:
    """El vendible de UN SKU; 0 si el libro no lo trae (D4). LANZA si el libro no
    se puede leer: «no sé» nunca es 0."""
    return _leer([sku]).get(str(sku).upper(), 0)


def numeros(skus: list[str]) -> dict[str, int]:
    """{SKU en mayúsculas: vendible} de una tanda, en dos consultas; 0 si el libro
    no lo trae (D4)."""
    if not skus:
        return {}
    leidos = _leer(list(skus))
    return {str(s).upper(): leidos.get(str(s).upper(), 0) for s in skus}


def todos() -> dict[str, int]:
    """Todo el libro: {SKU en mayúsculas: vendible}, SÓLO los SKUs que el libro trae
    (lo que no está vale 0: ver `numero`). Para `stock_watch` (modo libro) y la
    comparación de T0."""
    return _leer(None)
