"""
fanout_bodegas.py — La pestaña «Bodegas» de Operaciones: ¿cuadra el inventario
propio de kubera (0064/0065, mudado a `almacen.*` y `ventas.*` por la 0068) con
Odoo y con lo que stock_watch copia a Woo?

SOLO LEE. No escribe en kubera, ni en Odoo, ni en Woo, ni en ningún canal.

DE DÓNDE SALE CADA COLUMNA
  · Odoo por bodega (TEXCO 135, TEX2 150, DROP 142): `stock.quant` agrupado por
    producto bajo la ubicación raíz de cada almacén, sólo ubicaciones `internal`.
    físico = Σ quantity, reservado = Σ reserved_quantity, libre = físico − reservado
    (la identidad `free_qty = qty_available − reservado` está medida exacta en
    26,999 productos; ver `odoo.py`, nota 3 sobre `free_qty`). Caché de 10 min y,
    si Odoo no contesta, la última lectura buena con su edad.
  · Odoo hoy: el `free_qty` SIN contexto (todas las bodegas) del producto que
    copia stock_watch (el activo de id más alto), en la MISMA lectura que las tres
    bodegas. «Fuera de las 3» = ese total − Σ libre de las tres: lo que Odoo tiene
    en otras bodegas (SCRAP y CUARENTENA no: Odoo ya las deja fuera del total).
  · Odoo de la foto: `ops.stock_watch_photo.stock_odoo`, lo que leyó (y absorbió)
    la última pasada de stock_watch, ya con max(0).
  · kubera por bodega: `almacen.stock_almacen` (físico, apartado, libre).
  · Woo hoy: `ops.stock_watch_photo.stock_woo` (la misma pasada).
  · Ya no hay «puerta» ni formatos de Bodega: eran de la mudanza de TEX2 a TEX3,
    que no existirá (Eduardo, 8-oct-2026; la limpieza de la Fase 1 los borra).
    Por UNA versión la respuesta sigue llevando `formatos` en ceros,
    `que_falta: []`, una `puerta` neutra por fila y `renglones: []` en el cajón,
    para que una pestaña abierta con el frontend anterior no truene; se quitan en
    la versión siguiente, con los dos servicios ya desplegados y 24 h después.

«WOO ESPERADO» — EXACTAMENTE lo que hoy calcula `stock_watch._deltas_odoo`
  · Modo ABSOLUTO (`STOCK_WATCH_ABSOLUTO`): `max(0, stock_odoo − pend)` si
    `stock_watch.resta_pendientes()`, si no `stock_odoo`. `pend` sale de la MISMA
    función que usa stock_watch (`stock_watch._pendientes`, centinela incluido) y
    se busca con el código EXACTO, como `pend.get(sku)` allá: si no se puede
    medir, stock_watch no copia nada y aquí no hay esperado.
  · Modo DELTA: no existe un esperado absoluto (Woo conserva su base). Se dice.
  · `stock_odoo` NULL: Odoo no lo conoce (o está archivado) y stock_watch no lo toca.
  · kubera: la guía §5.2.7 fija `max(0, max(0, Odoo) + libre_kubera − pend)`,
    donde `libre_kubera` = Σ libre ≥ 0 de las bodegas kubera con
    `cuenta_para_woo` (comment de `stock_watch_photo.stock_kubera`). Se aplica
    SÓLO si stock_watch de verdad suma kubera: cuando exista
    `stock_watch.lee_kubera()` (la misma lectura que decide), esta pestaña la
    usa; mientras no exista, kubera NO suma aunque la fila de la bandera diga
    encendida, y la pestaña lo avisa. Con kubera sumando, la mitad kubera sale de
    la foto (`stock_kubera`, lo que sumó ESA pasada) y sólo sin ella del saldo de
    hoy. Los componentes `tras/entro/cub` del plan v3 §4 no están en el repo: esa
    fórmula con kubera es PROVISIONAL.

«COINCIDE» — y por qué NO basta comparar la foto consigo misma
  Tras una pasada normal, la foto guarda `stock_woo = max(0, stock_odoo − pend)`:
  comparar esperado (de la foto) contra Woo hoy (de la foto) da «Coincide» por
  construcción. Y en los tres casos que importan stock_watch conserva valores
  VIEJOS y consistentes entre sí: escritura fallida (guarda el `stock_odoo` viejo
  para reintentar), cortacircuitos (no guarda foto) y solo registro (conserva
  odoo y woo viejos). Lo que sí los delata es ODOO HOY: si el `free_qty` de hoy
  no es el de la foto, el estado es `por_copiar` («la próxima pasada copia N»), y
  si sigue así después de la próxima pasada, es una escritura fallida, un freno o
  el modo solo registro. Para que eso valga, la lectura de Odoo tiene que ser
  POSTERIOR a la pasada: si la pasada es más nueva que la caché, se relee.
  Lo único que la foto sola delata (`mas`/`menos`) es una pasada «ciega», que
  guarda el Odoo de hoy sin haberlo copiado.
  Y al revés, lo que movió Woo sin que la foto se entere: una venta en espera de
  guía (o una orden que nace en Odoo) entre dos pasadas cambia `pend` de HOY
  pero no la foto. Esos SKUs (venta u orden con `creado_at`/`actualizado_at`
  posterior a la pasada, o que salió de la ventana) también quedan en
  `por_copiar` en lugar de un falso «de más / de menos».

TOLERANCIAS
  · Sin las tablas de la 0064/0065 en su casa de la 0068 (`almacen.*`, `ventas.*`;
    `to_regclass`, guía §4.8): responde 200 con `tablas.ok = false` y en `faltan`
    el nombre calificado real, y la tabla igual se llena con Odoo y la foto. Nada
    aquí nombra los puentes `ops.<tabla>` de la 0068 (lo fija una prueba).
  · Odoo caído: `odoo.ok = false` (o la última lectura buena con `viejo`); kubera
    y Woo se siguen mostrando. Nunca un 500 por Odoo. Tras una falla no se vuelve
    a Odoo antes de un minuto, tampoco desde el cajón.
  · Las banderas: sin fila manda la variable de respaldo; si la lectura falla,
    apagadas (SEG-05).

ODOO EN SOLO LECTURA: todas las llamadas pasan por `_kw_solo_lectura`, con lista
blanca `search_read` / `read_group`. Cinco llamadas por lectura completa (TEX2,
productos de TEX2, sus hermanos por código más los SKUs de kubera, TEXCO y DROP),
más la de almacenes una vez al día. Todo esto BLOQUEA (XML-RPC y psycopg2): la
ruta lo corre entero en `asyncio.to_thread` (regla 11), y también codifica ahí
el JSON.
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fanout_bodegas")

ZONA = "America/Mexico_City"
_CDMX = timezone(timedelta(hours=-6))       # sin horario de verano desde 2022

# Las bodegas de Odoo que se leen, en el orden de la tabla. DROP entra aunque el
# planeador no la use: su libre SÍ está en el `free_qty` total que copia stock_watch.
ODOO_BODEGAS: list[tuple[str, int, str]] = [("TEXCO", 135, "TEXCO"), ("TEX2", 150, "TEXCO II"),
                                            ("DROP", 142, "DROP OFF")]
ODOO_TTL = 600.0            # la lectura por bodega: 10 min
ODOO_REINTENTO = 60.0       # tras una falla, no se vuelve a Odoo antes (tabla y cajón)
ODOO_TIMEOUT = 45.0         # segundos por llamada XML-RPC (la tabla)
ODOO_TIMEOUT_CAJON = 15.0   # el cajón de un SKU: alguien está esperando con la vista encima
_VISTAS_TTL = 86400.0       # almacén → ubicación raíz: cambia nunca
_RESUMEN_TTL = 20.0         # la página pregunta cada minuto y puede haber varias abiertas
_TABLAS_TTL = 60.0          # guía §4.8
_LENTO_TTL = 300.0          # conteos del libro y vigía: crecen con el libro, sin tope
_UNO_MAX = 256              # SKUs sueltos del cajón en caché

METODOS_ODOO = frozenset({"search_read", "read_group"})
CAMPOS_PRODUCTO = ["default_code", "name", "active", "free_qty"]

# Las cuatro banderas de la 0064/0065 (guía §4.8). Sin fila manda la variable de
# respaldo (si la hay); si la lectura falla, apagada.
BANDERAS: list[dict[str, Any]] = [
    {"flag": "ordenes_venta", "variable": "ORDENES_VENTA_ENABLED",
     "que": "Pantallas y rutas de órdenes de venta"},
    {"flag": "stock_watch_lee_kubera", "variable": "STOCK_WATCH_LEE_KUBERA",
     "que": "stock_watch suma a Woo el libre de kubera"},
    {"flag": "ov_generacion_auto", "variable": None,
     "que": "OV automáticas (crear_auto); el planeador todavía no las genera"},
    {"flag": "inventario_libro", "variable": None,
     "que": "Libro de kubera: entradas, conteos y correcciones (es del corte)"},
]

# Compatibilidad por UNA versión (ver el encabezado): lo que el frontend anterior
# lee sin guardas. Se quitan en la versión siguiente.
FORMATOS_COMPAT = {"por_confirmar": 0, "confirmados": 0, "esperando": 0, "abiertas_hoy": 0, "abiertas": 0,
                   "renglones": 0}
PUERTA_COMPAT = {"estado": "sin_formato", "texto": "Sin formato"}

# ── SQL ──────────────────────────────────────────────────────────────────────
# Cada sentencia lleva su marca `/* bodegas:… */`: las pruebas la usan para
# contestar sin base. Arreglos de SKU como `any(%(s)s::citext[])` (guía §4.7.6),
# nunca `sku::text = any(…)`, que anula el índice. TODAS son `select`: lo
# comprueba `tests/test_fanout_bodegas.py::SqlSoloLectura`.

# El núcleo de la pestaña, con su nombre calificado real (la 0068 los mudó de
# `ops`; la vigía se quedó en `ops`). `_tablas` lo usa para decir qué falta.
NUCLEO = {"almacenes": "almacen.almacenes", "stock_almacen": "almacen.stock_almacen",
          "stock_mov": "almacen.stock_mov", "ov_ordenes": "ventas.ov_ordenes", "ov_lineas": "ventas.ov_lineas"}

_SQL_TABLAS = """/* bodegas:tablas */
select to_regclass('almacen.almacenes') is not null as almacenes,
       to_regclass('almacen.stock_almacen') is not null as stock_almacen,
       to_regclass('almacen.stock_mov') is not null as stock_mov,
       to_regclass('ventas.ov_ordenes') is not null as ov_ordenes,
       to_regclass('ventas.ov_lineas') is not null as ov_lineas,
       to_regclass('ops.stock_apartado_descuadre_v') is not null as vigia,
       exists (select 1 from pg_catalog.pg_attribute a
                where a.attrelid = to_regclass('ops.stock_watch_photo')
                  and a.attname = 'stock_kubera' and not a.attisdropped) as stock_kubera"""

# `ultima_ts` crudo (timestamptz): con él se buscan las ventas posteriores a la pasada.
_SQL_PASADA = """/* bodegas:pasada */
select to_char(now() at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as ahora,
       to_char(max(actualizado) at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as ultima,
       max(actualizado) as ultima_ts,
       extract(epoch from now() - max(actualizado))::int as edad_s,
       count(*) as filas
  from ops.stock_watch_photo"""

_SQL_ALMACENES = """/* bodegas:almacenes */
select codigo, nombre, fuente, odoo_warehouse_id, preferencia, surte_ventas, admite_ov,
       cuenta_para_woo, motivo,
       to_char(actualizado_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as actualizado
  from almacen.almacenes
 order by (fuente = 'kubera'), preferencia nulls last, codigo"""

_SQL_BANDERAS = """/* bodegas:banderas */
select flag, valor, motivo, actualizado_por,
       to_char(actualizado_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as actualizado
  from ops.automatizacion_flags
 where flag = any(%(f)s)"""

# Toda fila de `stock_almacen` entra (un SKU que se vendió hasta 0 en una bodega
# de kubera sigue siendo de kubera); sólo se dejan fuera las filas en 0 de ENSAYO,
# que son ruido.
_SQL_SALDOS = """/* bodegas:saldos */
select sku::text as sku, almacen, fisico, apartado, libre, ubicacion
  from almacen.stock_almacen
 where almacen <> 'ENSAYO' or fisico <> 0 or apartado <> 0"""

_SQL_SALDOS_SKU = """/* bodegas:saldos_sku */
select sku::text as sku, almacen, fisico, apartado, libre, ubicacion,
       to_char(actualizado_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as actualizado
  from almacen.stock_almacen
 where sku = %(s)s::citext"""

# Conteos del encabezado. Recorren tablas que sólo crecen (el libro), así que van
# con su propia caché de 5 min (`_lentos`).
_SQL_CONTEOS = """/* bodegas:conteos */
select (select count(*) from almacen.stock_mov) as movimientos,
       (select count(*) from ventas.ov_ordenes
         where borrada_at is null and estado in ('borrador', 'confirmada')) as ov_abiertas"""

_SQL_VIGIA = """/* bodegas:vigia */
select problema, sku::text as sku, almacen, ref, esperado, encontrado, detalle
  from ops.stock_apartado_descuadre_v
 limit 50"""

_SQL_FOTO = """/* bodegas:foto */
select sku::text as sku, stock_woo, stock_odoo, {kub} as stock_kubera,
       to_char(actualizado at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora
  from ops.stock_watch_photo
 where sku = any(%(s)s::citext[])"""

# Los SKUs cuyo `pend` pudo moverse DESPUÉS de la pasada: una venta (o su orden)
# creada o tocada desde entonces, o una que salió de la ventana de días. Mismo
# cruce que `odoo_ventas_log.piezas_sin_orden`; el rango de `creado_at` va primero
# para que use su índice.
_SQL_RECIENTES = """/* bodegas:recientes */
select distinct i.sku::text as sku
  from ops.odoo_sale_orders o
  join ops.odoo_sale_order_items i
    on i.canal = o.canal and i.cuenta = o.cuenta
   and i.external_order_id = o.external_order_id
 where o.creado_at > %(u)s::timestamptz - make_interval(days => %(d)s)
   and (o.creado_at > %(u)s::timestamptz or o.actualizado_at > %(u)s::timestamptz
        or o.creado_at <= now() - make_interval(days => %(d)s))"""

_SQL_LIBRO = """/* bodegas:libro */
select id, almacen, delta, saldo_despues, motivo, ref, nota, coalesce(quien_nombre, quien) as quien, via,
       to_char(creado_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora,
       count(*) over () as total
  from almacen.stock_mov
 where sku = %(s)s::citext
 order by id desc
 limit %(n)s"""

# Sin `cliente` ni nada del comprador: el repo es público y la pantalla no lo necesita.
_SQL_OV_SKU = """/* bodegas:ov_sku */
select o.folio, o.estado, o.tipo, o.canal, o.full_tienda, (o.borrada_at is not null) as borrada,
       l.linea, l.cantidad, l.almacen, l.reservado, l.entregado,
       to_char(o.creado_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as creada,
       to_char(o.confirmada_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as confirmada,
       to_char(o.entregada_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as entregada,
       to_char(o.cancelada_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as cancelada
  from ventas.ov_lineas l
  join ventas.ov_ordenes o on o.id = l.orden_id
 where l.sku = %(s)s::citext
 order by o.id desc, l.linea
 limit 50"""


# ── Utilidades ───────────────────────────────────────────────────────────────

def _llave(sku: Any) -> str:
    """La llave de cruce de la TABLA: citext en kubera, `default_code` en Odoo.
    Mayúsculas. Para restar pendientes NO sirve: ahí manda el código exacto."""
    return str(sku or "").strip().upper()


def _ent(v: Any) -> int:
    try:
        return int(round(float(v or 0)))
    except (TypeError, ValueError):
        return 0


def _free(p: dict[str, Any]) -> int | None:
    """`free_qty` como lo toma stock_watch (`int(float(…))`, sin redondear). None
    si Odoo no lo devolvió: entonces no se compara contra la foto."""
    v = p.get("free_qty")
    if v is None or v is False:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _id_de(campo: Any) -> int | None:
    """Odoo devuelve los many2one como [id, nombre]; a veces False."""
    return int(campo[0]) if isinstance(campo, (list, tuple)) and campo else None


def _local_ahora() -> str:
    return datetime.now(_CDMX).strftime("%Y-%m-%d %H:%M:%S")


def _n(v: Any) -> str:
    return "—" if v is None else f"{int(v):,}"


def _escapar_like(s: str) -> str:
    """Para `=ilike` (igual sin distinguir mayúsculas): `%` y `_` son literales."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def a_json(v: Any) -> bytes:
    """El cuerpo de la respuesta. ⚠️ Con ~1,300 filas son ~0.9 MB y
    `jsonable_encoder` tarda ~60 ms: se codifica en el hilo, no en el loop."""
    return json.dumps(v, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")


# ── Funciones PURAS (las prueban tests/test_fanout_bodegas.py) ────────────────

def banderas(filas: list[dict[str, Any]] | None,
             variable: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    """Las cuatro banderas. `filas` None = la lectura falló → todas apagadas.

    Sin fila manda la variable de respaldo (si la bandera tiene; si no, apagada).
    `variable` recibe el nombre en minúsculas y dice su valor; por omisión,
    `settings`.
    """
    variable = variable or (lambda nombre: bool(getattr(settings, nombre, False)))
    por = {str(f.get("flag")): f for f in (filas or [])}
    salida = []
    for b in BANDERAS:
        f = por.get(b["flag"])
        if filas is None:
            enc, fuente = False, "error"
        elif f is not None:
            enc, fuente = bool(f.get("valor")), "fila"
        else:
            enc = bool(variable(b["variable"].lower())) if b["variable"] else False
            fuente = "variable"
        salida.append({
            "flag": b["flag"], "que": b["que"], "encendida": enc, "fuente": fuente,
            "variable": b["variable"],
            "motivo": (f or {}).get("motivo"), "por": (f or {}).get("actualizado_por"),
            "actualizado": (f or {}).get("actualizado"),
        })
    return salida


def libre_kubera(saldos: Iterable[dict[str, Any]], almacenes: dict[str, dict[str, Any]]) -> int:
    """Σ libre ≥ 0 de las bodegas kubera con `cuenta_para_woo` (la definición del
    comment de `stock_watch_photo.stock_kubera`). Un libre negativo (tras un
    conteo) cuenta 0, no resta."""
    total = 0
    for s in saldos:
        a = almacenes.get(str(s.get("almacen")))
        if a and a.get("fuente") == "kubera" and a.get("cuenta_para_woo"):
            total += max(0, int(s.get("libre") or 0))
    return total


def woo_esperado(stock_odoo: int | None, pend: int, *, absoluto: bool, resta: bool,
                 ciega: bool = False, lee_kubera: bool = False,
                 libre_kub: int = 0) -> tuple[int | None, str, str]:
    """(valor, motivo, desglose). Mismo cálculo que `stock_watch._deltas_odoo`.

    `motivo`: `calculado` | `delta` | `ciega` | `sin_odoo`. Con `valor` None la
    pestaña no compara (stock_watch no tiene un número que copiar).
    """
    if not absoluto:
        return None, "delta", ("Modo delta: Woo conserva su propia base y Odoo sólo aporta su variación; "
                               "no hay un número absoluto que esperar.")
    if ciega:
        return None, "ciega", ("No se pudo medir lo vendido sin orden: stock_watch no copia nada de Odoo a Woo "
                               "mientras tanto.")
    p = int(pend or 0) if resta else 0
    if lee_kubera and (stock_odoo is not None or libre_kub > 0):
        od = max(0, int(stock_odoo or 0))
        valor = max(0, od + int(libre_kub) - p)
        txt = f"max(0, Odoo {_n(od)} + kubera {_n(libre_kub)}" + (f" − {_n(p)} vendidas sin orden" if p else "") \
            + f") = {_n(valor)}"
        return valor, "calculado", txt
    if stock_odoo is None:
        return None, "sin_odoo", ("Odoo no lo conoce (o está archivado): stock_watch no lo toca"
                                  + (" y kubera no suma." if libre_kub else "."))
    if not resta:
        return int(stock_odoo), "calculado", f"Copia de Odoo: {_n(stock_odoo)} (sin restar vendidas sin orden)"
    valor = max(0, int(stock_odoo) - p)
    txt = (f"max(0, Odoo {_n(stock_odoo)} − {_n(p)} vendidas sin orden) = {_n(valor)}" if p
           else f"Odoo {_n(stock_odoo)} (sin vendidas sin orden)")
    return valor, "calculado", txt


def coincide(esperado: int | None, woo: int | None, motivo: str) -> tuple[str, int | None, str]:
    """(k, diferencia Woo − esperado, texto). `k`: igual | mas | menos | no_toca.

    `mas` es Woo ofreciendo de más (lo peligroso: sobreventa). `armar_fila` puede
    cambiarlo después a `por_copiar` (algo se movió desde la foto)."""
    if esperado is None:
        return "no_toca", None, {"delta": "Modo delta", "ciega": "Pendientes sin medir",
                                 "sin_odoo": "stock_watch no lo toca"}.get(motivo, "Sin esperado")
    if woo is None:
        if esperado == 0:
            return "no_toca", None, "Woo sin número y nada que ofrecer: stock_watch no lo prende"
        return "no_toca", None, (f"Woo sin número (o no está en Woo): si existe, stock_watch le prendería el "
                                 f"inventario con {_n(esperado)}, salvo que sea un padre con variantes")
    dif = int(woo) - int(esperado)
    if dif == 0:
        return "igual", 0, "Coincide"
    return ("mas", dif, "Woo ofrece de más") if dif > 0 else ("menos", dif, "Woo ofrece de menos")


def odoo_por_sku(productos: list[dict[str, Any]],
                 grupos: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Cruza `product.product` con los `read_group` por bodega → {SKU MAYÚS: …}.

    Con dos productos ACTIVOS del mismo código se queda con el de id más alto
    —igual que stock_watch, cuyo dict sobreescribe en orden de id— y lo marca. Un
    archivado no lo cuenta stock_watch: si sólo hay archivados, se muestran sus
    números marcados; si además hay activo, se cuentan aparte sus piezas. `free`
    es el `free_qty` del elegido si es activo (lo que copiaría stock_watch hoy).
    """
    por_id: dict[int, dict[str, dict[str, int]]] = defaultdict(dict)
    for codigo, filas in grupos.items():
        for g in filas or []:
            pid = _id_de(g.get("product_id"))
            if not pid:
                continue
            q, r = float(g.get("quantity") or 0), float(g.get("reserved_quantity") or 0)
            por_id[pid][codigo] = {"fisico": _ent(q), "reservado": _ent(r), "libre": _ent(q - r)}
    por_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in productos:
        k = _llave(p.get("default_code"))
        if k:
            por_sku[k].append(p)
    salida: dict[str, dict[str, Any]] = {}
    for k, ps in por_sku.items():
        ps = list({int(p["id"]): p for p in ps}.values())      # un id repetido no es un duplicado
        activos = sorted((p for p in ps if p.get("active")), key=lambda p: int(p["id"]))
        archivados = [p for p in ps if not p.get("active")]
        elegido = activos[-1] if activos else max(ps, key=lambda p: int(p["id"]))
        piezas_arch = sum(b["fisico"] for p in archivados if p is not elegido
                          for b in por_id.get(int(p["id"]), {}).values())
        salida[k] = {
            "sku": (elegido.get("default_code") or "").strip(),
            "nombre": elegido.get("name") or "",
            "activo": bool(activos),
            "duplicado": len(activos) > 1,
            "archivados": len(archivados),
            "piezas_archivadas": piezas_arch,
            "bodegas": por_id.get(int(elegido["id"]), {}),
            "free": _free(elegido) if activos else None,
            "en_tex2": any("TEX2" in por_id.get(int(p["id"]), {}) for p in ps),
        }
    return salida


def _cuenta_kubera(s: dict[str, Any]) -> bool:
    """¿Esta fila de `stock_almacen` mete al SKU en la tabla? Todas, menos las
    filas en 0 de ENSAYO (la misma regla que `_SQL_SALDOS`)."""
    return (str(s.get("almacen")) != "ENSAYO" or bool(int(s.get("fisico") or 0))
            or bool(int(s.get("apartado") or 0)))


def universo(odoo: dict[str, dict[str, Any]] | None, saldos: Iterable[dict[str, Any]],
             buscado: str | None = None) -> list[str]:
    """SKUs (mayúsculas) de la tabla: algo en TEX2 en Odoo, o fila en
    `almacen.stock_almacen` (las filas en 0 de ENSAYO no entran); más el que se
    busque."""
    u = {k for k, o in (odoo or {}).items() if o.get("en_tex2")}
    u |= {_llave(s.get("sku")) for s in saldos if _cuenta_kubera(s)}
    if buscado:
        u.add(_llave(buscado))
    u.discard("")
    return sorted(u)


def armar_fila(k: str, *, od: dict[str, Any] | None, odoo_ok: bool, foto: dict[str, Any] | None,
               saldos: list[dict[str, Any]], almacenes: dict[str, dict[str, Any]],
               pend: int, modo: dict[str, Any],
               odoo_tras_pasada: bool = False, reciente: bool = False) -> dict[str, Any]:
    """Una fila de la tabla. `modo`: absoluto, resta, ciega, lee_kubera.

    `pend` es el del código EXACTO que usa stock_watch. `odoo_tras_pasada`: la
    lectura de Odoo es posterior a la última pasada (sólo así se compara contra
    la foto). `reciente`: hubo una venta u orden de este SKU después de la pasada.
    """
    f = foto or {}
    sku = f.get("sku") or (saldos[0]["sku"] if saldos else None) or (od or {}).get("sku") or k
    kub = {str(s["almacen"]): {"fisico": int(s.get("fisico") or 0), "apartado": int(s.get("apartado") or 0),
                               "libre": int(s.get("libre") or 0)} for s in saldos}
    lk = libre_kubera(saldos, almacenes)
    lee = bool(modo.get("lee_kubera", False))
    stock_odoo = f.get("stock_odoo")
    k_foto = f.get("stock_kubera")
    # Con kubera sumando, cada fuente contra SU foto: la mitad kubera que sumó esa
    # pasada (comment de la 0065). Sin ella —pasada anterior a la columna—, la de hoy.
    lk_base = int(k_foto) if (lee and k_foto is not None) else lk
    kw_esp = {"absoluto": modo["absoluto"], "resta": modo["resta"], "ciega": modo.get("ciega", False),
              "lee_kubera": lee}
    esperado, motivo, desglose = woo_esperado(stock_odoo, pend, libre_kub=lk_base, **kw_esp)
    woo = f.get("stock_woo")
    k_c, dif, texto_c = coincide(esperado, woo, motivo)
    odoo_base: int | None = stock_odoo
    kubera_base: int | None = lk_base if lee else None

    # Odoo HOY, de la misma lectura que las tres bodegas, y sólo del producto que
    # copia stock_watch (el activo de id más alto).
    free = od.get("free") if (odoo_ok and od and od.get("activo")) else None
    odoo_hoy = max(0, int(free)) if free is not None else None
    # `odoo` None = Odoo no contestó; {} = contestó y el código no existe allá.
    odoo: dict[str, Any] | None = None
    otras = None
    if odoo_ok:
        cero = {"fisico": 0, "reservado": 0, "libre": 0}
        odoo = {c: (od["bodegas"].get(c) or cero) for c, _w, _n in ODOO_BODEGAS} if od else {}
        if od and free is not None:
            # Las dos cifras de la MISMA lectura: lo que Odoo tiene fuera de las tres.
            otras = int(free) - sum(b["libre"] for b in od["bodegas"].values())

    # ¿Se movió algo desde la foto? Entonces la foto no puede decir si Woo está bien:
    # lo dirá la próxima pasada. Ver «COINCIDE» en el encabezado del módulo.
    cambio: tuple[str, str] | None = None
    if k_c in ("igual", "mas", "menos"):
        if (odoo_tras_pasada and odoo_hoy is not None and stock_odoo is not None
                and odoo_hoy != int(stock_odoo)):
            cambio = ("odoo", f"Odoo cambió desde la pasada: la foto tenía {_n(stock_odoo)} y hoy hay "
                              f"{_n(odoo_hoy)}")
        elif lee and k_foto is not None and lk != int(k_foto):
            cambio = ("kubera", f"kubera cambió desde la pasada: la foto sumó {_n(k_foto)} y hoy hay {_n(lk)}")
        elif k_c != "igual" and reciente:
            cambio = ("venta", "Venta u orden nueva desde la pasada")
    if cambio:
        ya_igual = False
        if cambio[0] == "venta":
            texto_c = (f"{cambio[1]}: la foto difiere por {_n(abs(dif or 0))} y la próxima pasada reajusta Woo")
        else:
            base_od = odoo_hoy if odoo_hoy is not None else stock_odoo
            prox, _m, prox_d = woo_esperado(base_od, pend, libre_kub=lk, **kw_esp)
            esperado, desglose = prox, f"{prox_d}. {cambio[1]}."
            odoo_base, kubera_base = base_od, (lk if lee else None)
            # Si lo que copiaría la próxima pasada ya es lo que tiene Woo, Woo no va a
            # cambiar: no hay nada «por copiar» y la fila coincide (6-oct, verificación
            # en vivo: con la foto del 2-oct, 0 → 0 salía como pendiente).
            ya_igual = prox is not None and woo is not None and int(prox) == int(woo)
            if ya_igual:
                texto_c = (f"{cambio[1]}, pero lo que copia la próxima pasada ({_n(prox)}) ya es lo que "
                           "tiene Woo")
            else:
                texto_c = (f"{cambio[1]}: la próxima pasada copia {_n(prox)}. Si sigue así después de la "
                           "próxima pasada: escritura fallida, freno o modo solo registro")
        k_c, dif = ("igual", 0) if ya_igual else ("por_copiar", None)

    avisos = []
    if odoo_ok and od is None:
        avisos.append("No existe en Odoo")
    if od is not None and odoo_ok:
        if not od.get("activo"):
            avisos.append("Archivado en Odoo: stock_watch no lo cuenta")
        elif od.get("piezas_archivadas"):
            avisos.append(f"Un código archivado con {_n(od['piezas_archivadas'])} pzs que stock_watch no cuenta")
        if od.get("duplicado"):
            avisos.append("Dos productos activos con este código: stock_watch usa el de id más alto")
    if foto is None:
        avisos.append("No está en la foto de stock_watch (ni Odoo activo ni Woo lo listan)")

    tags = []
    if kub:
        tags.append("kubera")
    if k_c in ("mas", "menos"):
        tags.append("no_coincide")
    if k_c == "por_copiar":
        tags.append("por_copiar")
    rango = {"mas": 0, "menos": 1, "por_copiar": 2}.get(k_c, 3 if kub else 4)
    return {
        "sku": str(sku), "nombre": (od or {}).get("nombre") or "",
        "odoo": odoo, "odoo_existe": bool(od), "odoo_total": stock_odoo, "odoo_hoy": odoo_hoy,
        "otras": otras, "avisos": avisos,
        "kubera": kub, "libre_kubera": lk, "pend": int(pend or 0),
        "esperado": esperado, "esperado_motivo": motivo, "esperado_d": desglose,
        "odoo_base": odoo_base, "kubera_base": kubera_base,
        "woo": woo, "woo_de": f.get("hora"),
        "stock_kubera_foto": k_foto,
        "coincide": k_c, "dif": dif, "coincide_t": texto_c,
        "puerta": dict(PUERTA_COMPAT),       # compat: el frontend anterior la pinta; se quita después
        "tags": tags, "peso": [rango, -abs(dif or 0)],
    }


def ordenar(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los peores primero: Woo de más, de menos, por copiar, con saldo en kubera,
    el resto."""
    return sorted(filas, key=lambda f: (f["peso"][0], f["peso"][1], f["sku"]))


# ── Lectura de kubera (BLOQUEA) ──────────────────────────────────────────────

_tablas_cache: dict[str, Any] = {"t": 0.0, "v": None}
_lento_cache: dict[str, Any] = {"t": 0.0, "v": None}


def _tablas() -> dict[str, Any]:
    """¿Existen las tablas de la 0064/0065 (en `almacen.*` y `ventas.*` desde la
    0068)? Caché de 60 s (guía §4.8). `faltan` lleva el nombre calificado real.
    Las pruebas simulan su ausencia reemplazando esta función."""
    ahora = time.monotonic()
    if _tablas_cache["v"] is not None and ahora - _tablas_cache["t"] < _TABLAS_TTL:
        return _tablas_cache["v"]
    fila = sdb.fetch_one(_SQL_TABLAS) or {}
    faltan = [nombre for clave, nombre in NUCLEO.items() if not fila.get(clave)]
    v = {"ok": not faltan, "faltan": faltan, "vigia": bool(fila.get("vigia")),
         "stock_kubera": bool(fila.get("stock_kubera"))}
    if faltan:
        log.warning("fanout_bodegas: faltan las tablas de la 0064/0065 (%s)", ", ".join(faltan))
    _tablas_cache.update(t=ahora, v=v)
    return v


def _lentos(tablas: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Conteos del libro y de las OV abiertas, y la vigía, con caché de 5 min:
    recorren el libro, que sólo crece. Corre bajo `_cache_lock` (lo llama
    `_resumen`)."""
    ahora = time.monotonic()
    if _lento_cache["v"] is not None and ahora - _lento_cache["t"] < _LENTO_TTL:
        return _lento_cache["v"]
    conteos = sdb.fetch_one(_SQL_CONTEOS) or {}
    vigia = sdb.fetch_all(_SQL_VIGIA) if tablas["vigia"] else []
    _lento_cache.update(t=ahora, v=(conteos, vigia))
    return conteos, vigia


def _config_stock_watch() -> dict[str, Any]:
    """Cómo decide HOY stock_watch, desde sus propias funciones."""
    from services import stock_watch as sw
    est = sw.estado()
    return {
        "habilitado": bool(est.get("habilitado")), "solo_registro": bool(est.get("solo_registro")),
        "tope": est.get("tope"), "modo": est.get("modo"),
        "absoluto": bool(getattr(settings, "stock_watch_absoluto", False)),
        "resta": bool(sw.resta_pendientes()), "dias": est.get("resta_pendientes_dias"),
        "estado_memoria": est.get("estado") if est.get("ts") else None,
    }


def _suma_kubera() -> bool | None:
    """¿stock_watch suma kubera? None: esta versión de stock_watch no sabe (no
    existe `stock_watch.lee_kubera`). Si existe, es la misma lectura que decide;
    si falla, apagada (SEG-05)."""
    from services import stock_watch as sw
    f = getattr(sw, "lee_kubera", None)
    if not callable(f):
        return None
    try:
        return bool(f())
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_bodegas: stock_watch.lee_kubera falló (%s); se toma apagada", exc)
        return False


def _pendientes(resta: bool) -> tuple[dict[str, int], dict[str, Any]]:
    """Lo vendido sin orden, con la MISMA función que stock_watch (centinela
    incluido). Si no se puede medir, `ciega`: stock_watch no copiaría nada.

    El dict conserva el SKU EXACTO de la venta: stock_watch resta
    `pend.get(sku)` con el `default_code` tal cual, y `odoo_sale_order_items.sku`
    es text (distingue mayúsculas). Juntar variantes restaría lo que allá no."""
    if not resta:
        return {}, {"aplica": False, "ciega": False, "ventas": 0}
    from services import stock_watch as sw
    try:
        info = sw._pendientes()
    except Exception as exc:  # noqa: BLE001
        return {}, {"aplica": True, "ciega": True, "ventas": None, "motivo": str(exc)[:160]}
    pend = {str(s): int(v or 0) for s, v in (info.get("pend") or {}).items()}
    return pend, {"aplica": True, "ciega": False, "ventas": int(info.get("ventas") or 0),
                  "mas_vieja_h": info.get("mas_vieja_h")}


def _ventana_dias() -> int:
    from services import stock_watch as sw
    try:
        return int(sw._ventana_pendientes())
    except Exception:  # noqa: BLE001
        return 14


def _recientes(pasada: dict[str, Any], aplica: bool) -> tuple[set[str], str | None]:
    """SKUs (exactos) con una venta u orden posterior a la pasada. Si la consulta
    falla, conjunto vacío y el motivo: la tabla vuelve a comparar contra la foto
    sin ese descuento (y lo dice el encabezado)."""
    if not aplica or not pasada.get("ultima_ts"):
        return set(), None
    try:
        filas = sdb.fetch_all(_SQL_RECIENTES, {"u": pasada["ultima_ts"], "d": _ventana_dias()})
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_bodegas: no se pudieron leer las ventas posteriores a la pasada (%s)", exc)
        return set(), str(exc)[:160]
    return {str(f["sku"]) for f in filas if f.get("sku")}, None


def _leer_banderas() -> list[dict[str, Any]]:
    try:
        filas = sdb.fetch_all(_SQL_BANDERAS, {"z": ZONA, "f": [b["flag"] for b in BANDERAS]})
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_bodegas: no se pudieron leer las banderas (%s); se toman apagadas", exc)
        filas = None
    return banderas(filas)


def _leer_foto(llaves: list[str], con_kubera: bool) -> dict[str, dict[str, Any]]:
    if not llaves:
        return {}
    sql = _SQL_FOTO.format(kub="stock_kubera" if con_kubera else "null::integer")
    return {_llave(f["sku"]): f for f in sdb.fetch_all(sql, {"z": ZONA, "s": llaves})}


# ── Lectura de Odoo (BLOQUEA; SOLO LECTURA) ──────────────────────────────────

def _kw_solo_lectura(modelo: str, metodo: str, args: list[Any],
                     kwargs: dict[str, Any] | None = None, *, timeout: float = ODOO_TIMEOUT) -> Any:
    """La ÚNICA puerta a Odoo de este módulo: lista blanca de métodos de lectura."""
    if metodo not in METODOS_ODOO:
        raise PermissionError(f"fanout_bodegas sólo lee Odoo: «{metodo}» no está permitido")
    from services import odoo
    return odoo._kw_flujo(modelo, metodo, args, kwargs or {}, timeout=timeout)


_vistas_cache: dict[str, Any] = {"t": 0.0, "v": None}


def _vistas(kw: Callable[..., Any]) -> dict[str, int]:
    """{codigo: ubicación raíz} de las tres bodegas de Odoo. Caché de 24 h."""
    if _vistas_cache["v"] and time.monotonic() - _vistas_cache["t"] < _VISTAS_TTL:
        return _vistas_cache["v"]
    ids = {w: c for c, w, _n in ODOO_BODEGAS}
    filas = kw("stock.warehouse", "search_read", [[["id", "in", list(ids)]]],
               {"fields": ["code", "view_location_id"]})
    v = {ids[int(f["id"])]: _id_de(f.get("view_location_id")) for f in filas or [] if int(f["id"]) in ids}
    v = {c: r for c, r in v.items() if r}
    if "TEX2" not in v:
        raise RuntimeError("Odoo no devolvió el almacén TEXCO II (150)")
    _vistas_cache.update(t=time.monotonic(), v=v)
    return v


def _grupos(kw: Callable[..., Any], raiz: int, ids: list[int] | None) -> list[dict[str, Any]]:
    """`stock.quant` bajo la raíz de un almacén, sólo `internal`, agrupado por
    producto. Entra el quant con cantidad O reservado (un reservado sin físico es
    justo lo que hay que ver)."""
    dominio: list[Any] = [["location_id", "child_of", raiz], ["location_id.usage", "=", "internal"],
                          "|", ["quantity", "!=", 0], ["reserved_quantity", "!=", 0]]
    if ids is not None:
        dominio = [["product_id", "in", ids]] + dominio
    return kw("stock.quant", "read_group",
              [dominio, ["quantity:sum", "reserved_quantity:sum"], ["product_id"]], {"lazy": False}) or []


def leer_odoo_crudo(skus: list[str], kw: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Almacenes (caché), TEX2 (fija el universo), los productos de TEX2, sus
    HERMANOS por código más los SKUs de kubera, y TEXCO y DROP restringidos a
    todos ellos.

    Los hermanos importan: el universo sale por id de producto, y si el que tiene
    piezas en TEX2 es un archivado (o el duplicado de id menor), el activo que
    copia stock_watch no estaría en la lista y la fila mostraría otro producto."""
    kw = kw or _kw_solo_lectura
    vistas = _vistas(kw)
    g_tex2 = _grupos(kw, vistas["TEX2"], None)
    ids_tex2 = sorted({pid for g in g_tex2 if (pid := _id_de(g.get("product_id")))})
    opciones = {"fields": CAMPOS_PRODUCTO, "context": {"active_test": False}}
    productos: list[dict[str, Any]] = []
    if ids_tex2:
        productos = kw("product.product", "search_read", [[["id", "in", ids_tex2]]], dict(opciones)) or []
    vistos = sorted({int(p["id"]) for p in productos})
    # `default_code in` distingue mayúsculas: van los códigos tal cual y sin espacios.
    codigos = {str(p["default_code"]) for p in productos if p.get("default_code")}
    codigos |= {c.strip() for c in codigos} | {str(s) for s in skus}
    codigos.discard("")
    if codigos:
        dominio: list[Any] = [["default_code", "in", sorted(codigos)]]
        if vistos:
            dominio.append(["id", "not in", vistos])
        productos = productos + (kw("product.product", "search_read", [dominio], dict(opciones)) or [])
    ids = sorted({int(p["id"]) for p in productos})
    grupos = {"TEX2": g_tex2}
    for c in ("TEXCO", "DROP"):
        grupos[c] = _grupos(kw, vistas[c], ids) if ids and vistas.get(c) else []
    return {"por_sku": odoo_por_sku(productos, grupos), "skus_tex2": len(ids_tex2)}


# Dos candados: `_odoo_lock` sólo protege el dict (nunca se sostiene durante la
# red); `_odoo_leyendo` hace que haya UNA relectura a la vez. Así el cajón lee la
# caché sin esperar las cinco llamadas de una relectura en curso.
_odoo_cache: dict[str, Any] = {"t": 0.0, "llaves": frozenset(), "v": None, "leido": None,
                               "fallo_t": 0.0, "fallo": None}
_odoo_uno: dict[str, tuple[float, dict[str, Any] | None]] = {}
_odoo_lock = threading.Lock()
_odoo_leyendo = threading.Lock()


def _tras(edad: float, pasada_edad_s: int | None) -> bool:
    """¿La lectura de Odoo (de `edad` segundos) es posterior a la última pasada?"""
    return pasada_edad_s is None or edad <= pasada_edad_s


def _servir(c: dict[str, Any], ahora: float, pasada_edad_s: int | None, **extra: Any) -> dict[str, Any]:
    edad = ahora - c["t"]
    return {"ok": True, **c["v"], "edad_s": int(edad), "leido": c["leido"],
            "tras_pasada": _tras(edad, pasada_edad_s), **extra}


def _vigente(c: dict[str, Any], ahora: float, llaves: frozenset, pasada_edad_s: int | None) -> bool:
    """La caché sirve si es de hace < 10 min, cubre a los SKUs de kubera y es
    POSTERIOR a la última pasada (si no, no se puede comparar contra la foto)."""
    return (c["v"] is not None and ahora - c["t"] < ODOO_TTL and llaves <= c["llaves"]
            and _tras(ahora - c["t"], pasada_edad_s))


def _en_freno(c: dict[str, Any], ahora: float, pasada_edad_s: int | None) -> dict[str, Any] | None:
    """Tras una falla, un minuto sin volver a Odoo: la última buena o `ok: False`."""
    if c["fallo"] and ahora - c["fallo_t"] < ODOO_REINTENTO:
        if c["v"] is not None:
            return _servir(c, ahora, pasada_edad_s, viejo=True, motivo=c["fallo"])
        return {"ok": False, "motivo": c["fallo"]}
    return None


def leer_odoo(skus_kubera: Iterable[str], pasada_edad_s: int | None = None) -> dict[str, Any]:
    """Odoo por bodega con caché de 10 min. Nunca lanza.

    Relee si la caché no cubre algún SKU de kubera nuevo o si la última pasada de
    stock_watch es más nueva que ella. Si Odoo falla, sirve la última lectura
    buena (`viejo`) con su edad; sin ninguna, `ok: False`, y no se reintenta
    antes de un minuto (la página pregunta cada minuto)."""
    originales = {str(s).strip() for s in skus_kubera if str(s or "").strip()}
    llaves = frozenset(_llave(s) for s in originales)
    c = _odoo_cache
    with _odoo_lock:
        ahora = time.monotonic()
        if _vigente(c, ahora, llaves, pasada_edad_s):
            return _servir(c, ahora, pasada_edad_s)
        r = _en_freno(c, ahora, pasada_edad_s)
        if r is not None:
            return r
    with _odoo_leyendo:
        with _odoo_lock:                         # otro hilo pudo releer mientras se esperaba
            ahora = time.monotonic()
            if _vigente(c, ahora, llaves, pasada_edad_s):
                return _servir(c, ahora, pasada_edad_s)
            r = _en_freno(c, ahora, pasada_edad_s)
            if r is not None:
                return r
        inicio = time.monotonic()                # la edad cuenta desde que EMPIEZA la lectura
        try:
            # `default_code in` distingue mayúsculas: van el texto de kubera y su versión en mayúsculas.
            v = leer_odoo_crudo(sorted(originales | llaves))
        except Exception as exc:  # noqa: BLE001 — Odoo caído no tumba la pestaña
            motivo = f"Odoo no contestó ({str(exc)[:160]})"
            log.warning("fanout_bodegas: %s", motivo)
            with _odoo_lock:
                ahora = time.monotonic()
                c.update(fallo=motivo, fallo_t=ahora)
                if c["v"] is not None:
                    return _servir(c, ahora, pasada_edad_s, viejo=True, motivo=motivo)
            return {"ok": False, "motivo": motivo}
        with _odoo_lock:
            c.update(t=inicio, llaves=llaves, v=v, leido=_local_ahora(), fallo=None, fallo_t=0.0)
            return _servir(c, time.monotonic(), pasada_edad_s)


def _odoo_de_un_sku(sku: str, pasada_edad_s: int | None = None) -> dict[str, Any]:
    """Odoo para UN SKU (el cajón). Nunca lanza.

    Primero la caché de la tabla y la de SKUs sueltos (sin esperar a una
    relectura en curso); respeta el minuto sin Odoo tras una falla y deja anotada
    la suya. Busca el código sin distinguir mayúsculas (la página manda el SKU en
    mayúsculas) y con timeout corto: alguien está esperando."""
    s = (sku or "").strip()
    k = _llave(s)
    c = _odoo_cache
    with _odoo_lock:
        ahora = time.monotonic()
        freno = bool(c["fallo"]) and ahora - c["fallo_t"] < ODOO_REINTENTO
        if c["v"] is not None and k in c["v"]["por_sku"]:
            edad = ahora - c["t"]
            if freno or (edad < ODOO_TTL and _tras(edad, pasada_edad_s)):
                return {"ok": True, "od": c["v"]["por_sku"][k], "edad_s": int(edad),
                        "viejo": edad >= ODOO_TTL, "tras_pasada": _tras(edad, pasada_edad_s)}
        uno = _odoo_uno.get(k)
        if uno is not None:
            edad = ahora - uno[0]
            if freno or (edad < ODOO_TTL and _tras(edad, pasada_edad_s)):
                return {"ok": True, "od": uno[1], "edad_s": int(edad), "viejo": edad >= ODOO_TTL,
                        "tras_pasada": _tras(edad, pasada_edad_s)}
        if freno:
            return {"ok": False, "motivo": c["fallo"]}
    inicio = time.monotonic()
    try:
        def kw(modelo: str, metodo: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
            return _kw_solo_lectura(modelo, metodo, args, kwargs, timeout=ODOO_TIMEOUT_CAJON)
        vistas = _vistas(kw)
        productos = kw("product.product", "search_read", [[["default_code", "=ilike", _escapar_like(s)]]],
                       {"fields": CAMPOS_PRODUCTO, "context": {"active_test": False}}) or []
        ids = sorted({int(p["id"]) for p in productos})
        grupos = {c_: (_grupos(kw, vistas[c_], ids) if ids and vistas.get(c_) else []) for c_, _w, _n in ODOO_BODEGAS}
        od = odoo_por_sku(productos, grupos).get(k)
    except Exception as exc:  # noqa: BLE001
        motivo = f"Odoo no contestó ({str(exc)[:160]})"
        log.warning("fanout_bodegas: cajón de %s: %s", s, motivo)
        with _odoo_lock:
            c.update(fallo=motivo, fallo_t=time.monotonic())
        return {"ok": False, "motivo": motivo}
    with _odoo_lock:
        if len(_odoo_uno) >= _UNO_MAX:
            _odoo_uno.clear()
        _odoo_uno[k] = (inicio, od)
    return {"ok": True, "od": od, "edad_s": 0, "viejo": False, "tras_pasada": True}


# ── Lo que pinta la página ───────────────────────────────────────────────────

def _contexto(tablas: dict[str, Any]) -> dict[str, Any]:
    """Lo común a la tabla y al cajón: hora, bodegas, banderas y cómo decide stock_watch."""
    pasada = sdb.fetch_one(_SQL_PASADA, {"z": ZONA}) or {}
    almacenes = ({a["codigo"]: a for a in sdb.fetch_all(_SQL_ALMACENES, {"z": ZONA})}
                 if tablas["ok"] else {})
    flags = _leer_banderas()
    cfg = _config_stock_watch()
    suma = _suma_kubera()
    bandera_kub = next(b["encendida"] for b in flags if b["flag"] == "stock_watch_lee_kubera")
    pend, pend_info = _pendientes(cfg["absoluto"] and cfg["resta"])
    modo = {"absoluto": cfg["absoluto"], "resta": cfg["resta"], "ciega": bool(pend_info.get("ciega")),
            "lee_kubera": bool(suma) and tablas["ok"]}
    recientes, rec_error = _recientes(pasada, bool(pend_info.get("aplica")) and not modo["ciega"])
    pend_info = {**pend_info, "recientes": len(recientes), "recientes_error": rec_error}
    return {"pasada": pasada, "almacenes": almacenes, "banderas": flags, "cfg": cfg, "suma": suma,
            "bandera_kubera": bandera_kub, "pend": pend, "pend_info": pend_info, "modo": modo,
            "recientes": recientes}


def _fila(k: str, ctx: dict[str, Any], *, od: dict[str, Any] | None, odoo_ok: bool,
          odoo_tras_pasada: bool, foto: dict[str, Any] | None, saldos: list[dict[str, Any]]) -> dict[str, Any]:
    # El SKU EXACTO con que stock_watch resta: el de la foto (es el código de Odoo
    # y de Woo a la vez cuando hay `stock_odoo`), si no el de Odoo o el de kubera.
    clave = str((foto or {}).get("sku") or (od or {}).get("sku") or (saldos[0]["sku"] if saldos else k))
    return armar_fila(k, od=od, odoo_ok=odoo_ok, foto=foto, saldos=saldos, almacenes=ctx["almacenes"],
                      pend=ctx["pend"].get(clave, 0), modo=ctx["modo"],
                      odoo_tras_pasada=odoo_tras_pasada, reciente=clave in ctx["recientes"])


def _formula(ctx: dict[str, Any]) -> dict[str, Any]:
    """El aviso de cómo se calcula «Woo esperado» hoy (lo pinta el encabezado)."""
    m, cfg = ctx["modo"], ctx["cfg"]
    if not m["absoluto"]:
        texto = ("stock_watch está en modo DELTA: Woo conserva su base y Odoo sólo aporta su variación, así que "
                 "no hay un «Woo esperado» absoluto con qué comparar.")
    elif m["lee_kubera"]:
        texto = ("kubera SÍ suma a Woo: Woo esperado = max(0, max(0, Odoo) + libre de kubera"
                 + (" − vendidas sin orden" if m["resta"] else "") + "). Fórmula de la guía §5.2.7, provisional "
                 "hasta tener el plan v3 §4.")
    else:
        if ctx["suma"] is None and ctx["bandera_kubera"]:
            por_que = ("la bandera stock_watch_lee_kubera está encendida, pero stock_watch de esta versión "
                       "todavía no la lee")
        elif ctx["suma"] is None:
            por_que = ("la bandera stock_watch_lee_kubera está apagada (y stock_watch de esta versión todavía no "
                       "sabe sumar kubera)")
        else:
            por_que = "la bandera stock_watch_lee_kubera está apagada"
        texto = (f"kubera NO suma a Woo hoy: {por_que}. Woo esperado = "
                 + ("max(0, libre de Odoo − vendidas sin orden)" if m["resta"] else "libre de Odoo")
                 + ", lo mismo que copia stock_watch.")
    return {"texto": texto, "suma_kubera": m["lee_kubera"], "absoluto": m["absoluto"], "resta": m["resta"],
            "solo_registro": cfg["solo_registro"], "habilitado": cfg["habilitado"]}


def _resumen() -> dict[str, Any]:
    tablas = _tablas()
    ctx = _contexto(tablas)
    almacenes = ctx["almacenes"]
    pasada = ctx["pasada"]
    saldos: list[dict[str, Any]] = []
    conteos: dict[str, Any] = {}
    vigia: list[dict[str, Any]] = []
    if tablas["ok"]:
        saldos = sdb.fetch_all(_SQL_SALDOS)
        conteos, vigia = _lentos(tablas)
    skus_kubera = {s["sku"] for s in saldos if _cuenta_kubera(s)}

    odoo = leer_odoo(skus_kubera, pasada.get("edad_s"))
    por_sku = odoo.get("por_sku") if odoo.get("ok") else None
    llaves = universo(por_sku, saldos)
    foto = _leer_foto(llaves, tablas["stock_kubera"])
    saldos_por: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in saldos:
        saldos_por[_llave(s["sku"])].append(s)

    filas = ordenar([_fila(k, ctx, od=(por_sku or {}).get(k), odoo_ok=por_sku is not None,
                           odoo_tras_pasada=bool(odoo.get("tras_pasada")), foto=foto.get(k),
                           saldos=saldos_por.get(k, []))
                     for k in llaves])
    for f in filas:
        f.pop("peso", None)

    # Columnas de kubera: sólo las bodegas de fuente kubera con saldo ≠ 0.
    con_saldo = {str(s["almacen"]) for s in saldos if int(s.get("fisico") or 0) or int(s.get("apartado") or 0)}
    col_kub = [{"codigo": c, "nombre": a["nombre"], "cuenta_para_woo": bool(a.get("cuenta_para_woo"))}
               for c, a in almacenes.items() if a["fuente"] == "kubera" and c in con_saldo]
    conteos = {k: int(v or 0) for k, v in conteos.items()}

    arch = [o for o in (por_sku or {}).values() if not o.get("activo") and o.get("en_tex2")]
    return {
        "ok": True,
        "ahora": (pasada.get("ahora") or _local_ahora())[:16],
        "hoy": (pasada.get("ahora") or _local_ahora())[:10],
        "tablas": tablas,
        "almacenes": [{k: a.get(k) for k in ("codigo", "nombre", "fuente", "odoo_warehouse_id", "preferencia",
                                             "surte_ventas", "admite_ov", "cuenta_para_woo", "motivo",
                                             "actualizado")} for a in almacenes.values()],
        "banderas": ctx["banderas"],
        "stock_watch": {**ctx["cfg"], "ultima": pasada.get("ultima"), "edad_s": pasada.get("edad_s"),
                        "filas_foto": int(pasada.get("filas") or 0), "suma_kubera": ctx["suma"],
                        "pendientes": ctx["pend_info"]},
        "formula": _formula(ctx),
        "conteos": conteos,
        "vigia": vigia,
        # compat (una versión): el frontend anterior lee estos dos sin guardas.
        "formatos": {**FORMATOS_COMPAT, **conteos},
        "que_falta": [],
        "odoo": {"ok": bool(odoo.get("ok")), "motivo": odoo.get("motivo"), "viejo": bool(odoo.get("viejo")),
                 "edad_s": odoo.get("edad_s"), "leido": odoo.get("leido"),
                 "tras_pasada": bool(odoo.get("tras_pasada")),
                 "skus_tex2": odoo.get("skus_tex2"),
                 "archivados_tex2": len(arch),
                 "piezas_archivadas_tex2": sum(sum(b["fisico"] for b in o["bodegas"].values()) for o in arch),
                 "duplicados": sum(1 for o in (por_sku or {}).values() if o.get("duplicado"))},
        "columnas_odoo": [{"codigo": c, "nombre": n, "warehouse_id": w} for c, w, n in ODOO_BODEGAS],
        "columnas_kubera": col_kub,
        # Sin Odoo (y sin lectura vieja) no se sabe qué hay en TEX2: sólo entra lo de kubera.
        "universo_parcial": por_sku is None,
        "conteo": {"filas": len(filas),
                   "no_coincide": sum(1 for f in filas if "no_coincide" in f["tags"]),
                   "de_mas": sum(1 for f in filas if f["coincide"] == "mas"),
                   "por_copiar": sum(1 for f in filas if f["coincide"] == "por_copiar"),
                   "kubera": sum(1 for f in filas if "kubera" in f["tags"])},
        "filas": filas,
    }


# La caché guarda el dict y, la primera vez que se pide, sus bytes (y su gzip):
# cada pestaña abierta pregunta cada minuto y recodificar ~0.9 MB cuesta.
_cache: dict[str, Any] = {"t": 0.0, "v": None, "b": None, "gz": None}
_cache_lock = threading.Lock()


def resumen() -> dict[str, Any]:
    """Lo que pinta la pestaña. ⚠️ BLOQUEA: la ruta lo llama en `asyncio.to_thread`.
    Caché de 20 s (Odoo lleva la suya, de 10 min). Si kubera no contesta, `ok: False`."""
    with _cache_lock:
        if _cache["v"] is not None and time.time() - _cache["t"] < _RESUMEN_TTL:
            return _cache["v"]
        try:
            v = _resumen()
        except Exception as exc:  # noqa: BLE001 — se dice, no se truena
            log.exception("fanout_bodegas: no se pudo armar la pestaña")
            return {"ok": False, "motivo": f"kubera no contestó ({str(exc)[:160]})"}
        _cache.update(t=time.time(), v=v, b=None, gz=None)
        return v


def resumen_bytes(gz: bool) -> tuple[bytes, bool]:
    """`resumen()` ya codificado (y comprimido si el navegador acepta gzip).
    ⚠️ BLOQUEA: corre en el hilo de la ruta. → (cuerpo, va_comprimido)."""
    v = resumen()
    with _cache_lock:
        mio = _cache["v"] is v
        b, z = (_cache["b"], _cache["gz"]) if mio else (None, None)
    if b is None:
        b = a_json(v)
    if gz and z is None:
        z = gzip.compress(b, compresslevel=5)
    if mio:
        with _cache_lock:
            if _cache["v"] is v:
                _cache.update(b=b, gz=z if gz else _cache["gz"])
    return (z, True) if gz else (b, False)


def detalle_sku(sku: str) -> dict[str, Any]:
    """El cajón de UN SKU: su fila, su libro (`almacen.stock_mov`) y sus OV.
    ⚠️ BLOQUEA (la ruta lo llama en un hilo). Solo lee."""
    s = (sku or "").strip()
    k = _llave(s)
    try:
        tablas = _tablas()
        ctx = _contexto(tablas)
        saldos, libro, ovs = [], [], []
        if tablas["ok"]:
            saldos = sdb.fetch_all(_SQL_SALDOS_SKU, {"z": ZONA, "s": s})
            libro = sdb.fetch_all(_SQL_LIBRO, {"z": ZONA, "s": s, "n": 200})
            ovs = sdb.fetch_all(_SQL_OV_SKU, {"z": ZONA, "s": s})
        foto = _leer_foto([k], tablas["stock_kubera"]).get(k)
    except Exception as exc:  # noqa: BLE001
        log.exception("fanout_bodegas: detalle de %s", s)
        return {"ok": False, "sku": s, "motivo": f"kubera no contestó ({str(exc)[:160]})"}

    pasada = ctx["pasada"]
    o = _odoo_de_un_sku(s, pasada.get("edad_s"))
    fila = _fila(k, ctx, od=o.get("od"), odoo_ok=bool(o.get("ok")), odoo_tras_pasada=bool(o.get("tras_pasada")),
                 foto=foto, saldos=saldos)
    fila.pop("peso", None)
    total = int(libro[0]["total"]) if libro else 0
    for m in libro:
        m.pop("total", None)
    existe = bool(foto or saldos or ovs or libro or o.get("od"))
    return {
        "ok": True, "sku": fila["sku"] if existe else s, "existe": existe,
        "hoy": (pasada.get("ahora") or _local_ahora())[:10],
        "tablas": tablas, "fila": fila,
        "odoo": {"ok": bool(o.get("ok")), "motivo": o.get("motivo"), "edad_s": o.get("edad_s"),
                 "viejo": bool(o.get("viejo"))},
        "columnas_odoo": [{"codigo": c, "nombre": n, "warehouse_id": w} for c, w, n in ODOO_BODEGAS],
        "libro": libro, "libro_total": total, "ov": ovs,
        "renglones": [],                     # compat: el frontend anterior lo lee sin guarda; se quita después
        "formula": _formula(ctx),
    }
