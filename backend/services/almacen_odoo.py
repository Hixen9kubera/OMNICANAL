"""
almacen_odoo.py — Lo que kubera COPIA de Odoo de un cedis que todavía se opera
allá: dónde está cada SKU (`almacen.locations`, 0069) y todo lo que se le movió
(`almacen.historial_movimientos`, 0070).

Pedido de Brandon (9-oct-2026): TEXCO II deja de operarse desde Odoo y pasa a
kubera. La ubicación de rack y el historial de movimientos sólo existían allá.
Hasta el día del corte, este módulo OBSERVA a Odoo cada 30 minutos y los deja
aquí, «para que lo tengamos como fuente de información».

QUÉ COPIA
  · LA FOTO de existencias (`stock.quant`, cantidad ≠ 0): un renglón por SKU y
    ubicación. Se concilia entera en cada pasada (se escribe sólo lo que cambió).
  · EL HISTORIAL (`stock.move.line` hechas): un renglón por movimiento. Cada
    pasada pide a Odoo sólo lo ESCRITO desde la última línea vista, con 15 min de
    traslape (el delta). La primera pasada de cada arranque y una vez al día se
    relee todo y se concilia.

LAS REGLAS (de Brandon)
  · `cedis` obligatorio en todo renglón.
  · El SKU sin rack entra como «SIN UBICAR».
  · Los negativos se traen: salen de movimientos hechos en Odoo.
  · Los productos ARCHIVADOS en Odoo NO se traen («archivados pueden ser
    error»): ni su existencia ni sus movimientos.
  · `piezas` de la foto es INFORMATIVA: el saldo oficial es `stock_almacen`.

LA PRUEBA DE QUE ESTÁ COMPLETO
  Σ delta del historial de un SKU = Σ piezas de su foto. Medido el 9-oct-2026
  contra Odoo: 1,414 de 1,414 productos activos y 1,626 de 1,626 pares producto +
  ubicación. `cuadre()` lo mide en cada pasada y lo deja en el log.

CUÁNDO SE DETIENE
  Cuando el catálogo de bodegas diga que el cedis ya no es de Odoo
  (`almacen.almacenes.fuente <> 'odoo'`): desde ese día Odoo dejó de ser su
  verdad y una copia pisaría lo que bodega capture aquí. La foto se detiene
  antes si alguien captura un renglón a mano en ese cedis (`origen = 'kubera'`).

LO QUE NO HACE
  No escribe en Odoo (sólo `search_read` y `read`). No toca Woo, el stock que se
  vende ni ningún canal: nadie decide nada con estas dos tablas todavía.

CÓMO ESCRIBE
  Cada sentencia es repetible y vale por sí sola (borrar lo que ya no está,
  escribir lo que cambió). `supabase_db.get_cursor` puede cambiar de conexión a
  media transacción sin avisar; así, lo peor que pasa es que una pasada quede a
  medias y la siguiente la termine.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager

from psycopg2.extras import execute_values

from services import odoo as od

log = logging.getLogger("omnicanal.almacen_odoo")

# Los cedis que se observan. Sólo cuentan mientras el catálogo de bodegas los
# tenga con `fuente = 'odoo'`: ése es el interruptor, no una variable.
CEDIS_OBSERVADOS: tuple[str, ...] = ("TEX2",)

SIN_UBICAR = "SIN UBICAR"
NAVE = "FERRAFORME"
TRASLAPE = timedelta(minutes=15)       # el delta relee este tramo: una validación larga de Odoo no se escapa
COMPLETA_CADA = timedelta(hours=24)
CAIDA_MAX_FOTO = 0.50                  # una foto que encoge más que esto es una lectura trunca, no la realidad
CAIDA_MAX_HISTORIAL = 0.20             # las líneas hechas no se borran en Odoo: el historial no encoge
TIMEOUT_ODOO = 90.0
_PAGINA = 2000

_SKU = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)+$")
_RACK = re.compile(r"^BLOQUE (\S+)-FILA (\S+)-(\S+)$")

Abrir = Callable[[], ContextManager[Any]]


class NoAplica(Exception):
    """El cedis no se copia (ya no es de Odoo, o faltan las tablas). No es una falla."""


# ─────────────────────────────────────────────────────────────────────────────
# Funciones puras: de lo que dice Odoo a los renglones de las tablas
# ─────────────────────────────────────────────────────────────────────────────

def ubicacion_de(completo: str) -> tuple[str, str | None, str | None, str | None]:
    """La ruta de Odoo → (ubicacion, bloque, fila, tarima).

    El rack se escribe como lo pinta el panel (`odoo._rack`), para que las tablas
    y el Catálogo Maestro digan lo mismo. La raíz de la nave no es una posición:
    es «SIN UBICAR». Lo que cuelga del almacén fuera de la nave («Zona de
    empaquetado», «Salida», «FERRAFORME Archivar») conserva su nombre: no es un
    rack, pero tampoco es lo mismo que no tener lugar.
    """
    partes = [p.strip() for p in (completo or "").split("/") if p.strip()]
    if len(partes) <= 1 or partes[1:] == [NAVE]:
        return SIN_UBICAR, None, None, None
    if len(partes) == 2:
        return partes[1], None, None, None
    rack = od._rack(completo, "")
    m = _RACK.match(rack)
    return (rack, m.group(1), m.group(2), m.group(3)) if m else (rack, None, None, None)


def sku_de(producto: dict[str, Any]) -> str | None:
    """El SKU de un producto de Odoo. Tres productos de TEXCO II no tienen
    `default_code` y su NOMBRE es el SKU: se usa el nombre sólo si tiene esa forma."""
    sku = (producto.get("default_code") or "").strip()
    if sku:
        return sku
    nombre = (producto.get("name") or "").strip()
    return nombre if _SKU.match(nombre) else None


def _fecha(texto: str) -> datetime:
    """Odoo entrega sus fechas en UTC y sin zona."""
    return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _id(campo: Any) -> int | None:
    return campo[0] if isinstance(campo, (list, tuple)) and campo else None


def _nombre(campo: Any) -> str:
    return str(campo[1]) if isinstance(campo, (list, tuple)) and len(campo) > 1 else ""


def filas_locations(cedis: str, quants: list[dict],
                    productos: dict[int, dict]) -> tuple[list[tuple], dict[str, int]]:
    """Los renglones de existencia de Odoo → filas de `almacen.locations`.

    Los productos archivados se saltan (regla de Brandon) y se cuentan. Revienta
    —no adivina— ante lo que haría una fila falsa: cantidad con decimales, un
    producto sin SKU cuyo nombre no es un SKU, o dos productos ACTIVOS del mismo
    SKU en la misma ubicación.
    """
    filas: list[tuple] = []
    stats: Counter = Counter()
    problemas: list[str] = []
    vistos: dict[tuple[str, str], int] = {}
    for q in quants:
        pid = q["product_id"][0]
        p = productos.get(pid)
        if p is None:
            problemas.append(f"quant {q['id']}: Odoo no devolvió el producto {pid}")
            continue
        if not p.get("active", True):
            stats["archivados_omitidos"] += 1
            continue
        sku = sku_de(p)
        if not sku:
            problemas.append(f"producto {pid} sin SKU y su nombre no es un SKU: {p.get('name')!r}")
            continue
        cantidad = q["quantity"]
        if cantidad != int(cantidad):
            problemas.append(f"{sku}: existencia con decimales ({cantidad})")
            continue
        completo = q["location_id"][1]
        ubicacion, bloque, fila, tarima = ubicacion_de(completo)
        clave = (sku.lower(), ubicacion)
        if clave in vistos:
            problemas.append(f"{sku}: dos productos activos ({vistos[clave]} y {pid}) "
                             f"en la misma ubicación {ubicacion!r}")
            continue
        vistos[clave] = pid
        filas.append((cedis, sku, ubicacion, bloque, fila, tarima, int(cantidad),
                      q["id"], pid, completo,
                      _fecha(q["in_date"]) if q.get("in_date") else None))
        stats["renglones"] += 1
        stats["piezas"] += int(cantidad)
        stats["negativos"] += cantidad < 0
        stats["sin_ubicar"] += ubicacion == SIN_UBICAR
        stats["en_rack"] += bloque is not None
        stats["sin_sku_en_odoo"] += not (p.get("default_code") or "").strip()
    if problemas:
        raise ValueError("la foto de Odoo no se puede copiar:\n  " + "\n  ".join(problemas[:30]))
    stats["skus"] = len({f[1].lower() for f in filas})
    return filas, dict(stats)


def filas_historial(cedis: str, lineas: list[dict], ubis: dict[int, dict], movs: dict[int, dict],
                    picks: dict[int, dict], productos: dict[int, dict],
                    cod_alm: dict[int, str]) -> tuple[list[tuple], dict[str, int]]:
    """Las líneas de movimiento hechas de Odoo → filas de `almacen.historial_movimientos`.

    El almacén de cada lado sale del `warehouse_id` de su ubicación, nunca del
    texto de la ruta. La causa es la del libro de bodega del panel (`odoo._causa`).
    `delta` es lo que el movimiento le hizo AL CEDIS: + si sólo el destino es
    suyo, − si sólo el origen, 0 si los dos.
    """
    filas: list[tuple] = []
    stats: Counter = Counter()
    problemas: list[str] = []
    for l in lineas:
        pid = l["product_id"][0]
        p = productos.get(pid)
        if p is None:
            problemas.append(f"línea {l['id']}: Odoo no devolvió el producto {pid}")
            continue
        if not p.get("active", True):
            stats["archivados_omitidos"] += 1
            continue
        sku = sku_de(p)
        if not sku:
            problemas.append(f"producto {pid} sin SKU y su nombre no es un SKU: {p.get('name')!r}")
            continue
        cantidad = l["quantity"]
        if cantidad != int(cantidad) or cantidad < 0:
            problemas.append(f"{sku}: línea {l['id']} con cantidad {cantidad}")
            continue
        o, d = ubis.get(l["location_id"][0]), ubis.get(l["location_dest_id"][0])
        if o is None or d is None:
            problemas.append(f"línea {l['id']}: Odoo no devolvió una de sus ubicaciones")
            continue
        u_org = (o.get("usage") or "", cod_alm.get(_id(o.get("warehouse_id")) or 0, ""))
        u_dst = (d.get("usage") or "", cod_alm.get(_id(d.get("warehouse_id")) or 0, ""))
        en_org, en_dst = u_org[1] == cedis, u_dst[1] == cedis
        if not en_org and not en_dst:
            # La consulta pide líneas que TOCAN el cedis; una que no lo toca por
            # almacén no es suya. Se cuenta: si aparece, hay que mirarla.
            stats["fuera_del_cedis"] += 1
            continue
        mv = movs.get(_id(l.get("move_id")) or 0, {})
        socio = _nombre(picks.get(_id(l.get("picking_id")) or 0, {}).get("partner_id"))
        causa = od._causa(mv, u_org, u_dst, od._vendible(u_dst), od._vendible(u_org), socio)
        piezas = int(cantidad)
        delta = (piezas if en_dst else 0) - (piezas if en_org else 0)
        filas.append((
            cedis, sku, _fecha(l["date"]), causa, piezas, delta,
            ubicacion_de(o["complete_name"])[0] if en_org else None,
            ubicacion_de(d["complete_name"])[0] if en_dst else None,
            l.get("reference") or None, mv.get("origin") or None, socio or None,
            _nombre(mv.get("picking_type_id")) or None, _nombre(l.get("create_uid")) or None,
            l["id"], pid, o["complete_name"], d["complete_name"], _fecha(l["write_date"]),
        ))
        stats["renglones"] += 1
        stats["entran"] += delta > 0
        stats["salen"] += delta < 0
        stats["adentro"] += delta == 0
    if problemas:
        raise ValueError("el historial de Odoo no se puede copiar:\n  " + "\n  ".join(problemas[:30]))
    stats["skus"] = len({f[1].lower() for f in filas})
    return filas, dict(stats)


def cuadre_filas(f_loc: list[tuple], f_his: list[tuple]) -> dict[str, int]:
    """¿El historial reproduce la foto? Por SKU, Σ delta contra Σ piezas. Sólo tiene
    sentido con el historial COMPLETO; es la prueba de carga del ensayo."""
    foto: dict[str, int] = defaultdict(int)
    libro: dict[str, int] = defaultdict(int)
    for f in f_loc:
        foto[f[1].lower()] += f[6]
    for f in f_his:
        libro[f[1].lower()] += f[5]
    skus = set(foto) | {s for s, v in libro.items() if v}
    return {"skus": len(skus), "descuadres": sum(1 for s in skus if foto.get(s, 0) != libro.get(s, 0))}


# ─────────────────────────────────────────────────────────────────────────────
# Odoo: sólo lectura, con tiempo límite, y lanza si algo falla
# ─────────────────────────────────────────────────────────────────────────────

def _kw(modelo: str, metodo: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
    return od._kw_flujo(modelo, metodo, args, kwargs, timeout=TIMEOUT_ODOO)


def _leer(modelo: str, ids: list[int], campos: list[str], lote: int = 800) -> dict[int, dict]:
    salida: dict[int, dict] = {}
    for i in range(0, len(ids), lote):
        for r in _kw(modelo, "read", [ids[i:i + lote]], {"fields": campos}):
            salida[r["id"]] = r
    return salida


def _productos(ids: list[int]) -> dict[int, dict]:
    """`active in [True, False]`: sin eso Odoo calla los archivados y no se podría
    saber que lo son para saltarlos."""
    salida: dict[int, dict] = {}
    for i in range(0, len(ids), 500):
        for p in _kw("product.product", "search_read",
                     [[["id", "in", ids[i:i + 500]], ["active", "in", [True, False]]]],
                     {"fields": ["default_code", "name", "active"]}):
            salida[p["id"]] = p
    return salida


def leer_almacenes() -> dict[str, dict[str, Any]]:
    """``{ código: {id, vista} }`` de todos los almacenes de Odoo, archivados incluidos."""
    filas = _kw("stock.warehouse", "search_read", [[["active", "in", [True, False]]]],
                {"fields": ["code", "view_location_id"]})
    if not filas:
        raise RuntimeError("Odoo no devolvió ningún almacén: se trata como falla")
    return {f["code"]: {"id": f["id"], "vista": _id(f["view_location_id"])} for f in filas}


def leer_foto(vista: int) -> tuple[list[dict], dict[int, dict]]:
    quants = _kw("stock.quant", "search_read",
                 [[["location_id", "child_of", vista], ["quantity", "!=", 0]]],
                 {"fields": ["product_id", "location_id", "quantity", "in_date"]})
    return quants, _productos(sorted({q["product_id"][0] for q in quants}))


def leer_movimientos(vista: int, desde: datetime | None = None) -> dict[str, Any]:
    """Las líneas HECHAS que tocan el almacén (origen o destino bajo su vista), con
    lo que hace falta para clasificarlas. Con `desde`, sólo las ESCRITAS a partir
    de ese momento: el delta."""
    dominio: list[Any] = [["state", "=", "done"], "|", ["location_id", "child_of", vista],
                          ["location_dest_id", "child_of", vista]]
    if desde is not None:
        dominio.append(["write_date", ">=", desde.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")])
    lineas: list[dict] = []
    while True:
        pagina = _kw("stock.move.line", "search_read", [dominio],
                     {"fields": ["date", "quantity", "product_id", "location_id", "location_dest_id",
                                 "reference", "picking_id", "move_id", "create_uid", "write_date"],
                      "order": "id", "limit": _PAGINA, "offset": len(lineas)})
        lineas += pagina
        if len(pagina) < _PAGINA:
            break
    if not lineas:
        return {"lineas": [], "ubis": {}, "movs": {}, "picks": {}, "productos": {}}
    return {
        "lineas": lineas,
        "ubis": _leer("stock.location",
                      sorted({l[c][0] for l in lineas for c in ("location_id", "location_dest_id")}),
                      ["complete_name", "usage", "warehouse_id"]),
        "movs": _leer("stock.move", sorted({l["move_id"][0] for l in lineas if l.get("move_id")}),
                      ["is_inventory", "scrapped", "origin", "picking_type_id"]),
        "picks": _leer("stock.picking", sorted({l["picking_id"][0] for l in lineas if l.get("picking_id")}),
                       ["partner_id"]),
        "productos": _productos(sorted({l["product_id"][0] for l in lineas})),
    }


# ─────────────────────────────────────────────────────────────────────────────
# kubera: guardas, escritura por diferencias y cuadre
# ─────────────────────────────────────────────────────────────────────────────

def guardas(cur: Any, cedis: str) -> dict[str, Any]:
    """Lo que tiene que ser cierto para copiar. Se revisa DOS veces: antes de leer
    Odoo y otra vez en la transacción que escribe, por si algo cambió entre tanto."""
    cur.execute("select to_regclass('almacen.locations') is not null as foto, "
                "to_regclass('almacen.historial_movimientos') is not null as historial, "
                "coalesce(to_regclass('almacen.almacenes'), to_regclass('ops.almacenes'))::text as catalogo")
    r = cur.fetchone()
    if not (r["foto"] and r["historial"] and r["catalogo"]):
        raise NoAplica("faltan almacen.locations, almacen.historial_movimientos o el catálogo "
                       "de bodegas (migraciones 0069 y 0070)")
    # `catalogo` viene de to_regclass: es un nombre de tabla que la base ya validó.
    cur.execute(f"select nombre, fuente, odoo_warehouse_id from {r['catalogo']} where codigo = %s",
                (cedis,))
    b = cur.fetchone()
    if not b:
        raise NoAplica(f"el catálogo de bodegas no tiene {cedis}")
    if b["fuente"] != "odoo":
        raise NoAplica(f"{b['nombre']} ya es de {b['fuente']}: Odoo dejó de ser su verdad")
    return {"nombre": b["nombre"], "odoo_warehouse_id": b["odoo_warehouse_id"]}


def marca_de_agua(cur: Any, cedis: str) -> datetime | None:
    cur.execute("select max(odoo_escrito_at) as marca from almacen.historial_movimientos "
                "where cedis = %s", (cedis,))
    return cur.fetchone()["marca"]


_L_COLS = ("cedis, sku, ubicacion, bloque, fila, tarima, piezas, origen, "
           "odoo_quant_id, odoo_product_id, odoo_ubicacion, entrada_at")
_L_DATOS = ("bloque", "fila", "tarima", "piezas", "odoo_quant_id", "odoo_product_id",
            "odoo_ubicacion", "entrada_at")
_L_PLANTILLA = "(%s,%s,%s,%s,%s,%s,%s,'odoo',%s,%s,%s,%s)"
# `where l.origen = 'odoo'`: un renglón capturado en kubera no se pisa nunca.
_L_UPSERT = (f"insert into almacen.locations as l ({_L_COLS}) values %s "
             "on conflict (cedis, sku, ubicacion) do update set "
             + ", ".join(f"{c} = excluded.{c}" for c in _L_DATOS)
             + " where l.origen = 'odoo'")


def escribir_locations(cur: Any, cedis: str, filas: list[tuple], *,
                       aplicar: bool, forzar: bool = False) -> dict[str, Any]:
    """Concilia la foto: borra lo que ya no está en Odoo y escribe sólo lo que cambió."""
    # El SKU se compara en minúsculas hechas AQUÍ, no con lower() de la base: con
    # una Ñ («MUE-0359-MUÑ-ROJ-BLN») la base y Python no siempre coinciden, y ese
    # renglón se borraría y se volvería a escribir en cada pasada.
    cur.execute("select id, sku::text as sku, ubicacion, origen, " + ", ".join(_L_DATOS)
                + " from almacen.locations where cedis = %s", (cedis,))
    actuales = cur.fetchall()
    a_mano = sum(1 for r in actuales if r["origen"] != "odoo")
    if a_mano:
        return {"omitida": f"{a_mano} renglón(es) capturados en kubera: la foto ya no se pisa",
                "total": len(actuales)}
    if not filas:
        raise RuntimeError("la foto de Odoo llegó vacía: se trata como falla, no como cero")
    if actuales and len(filas) < len(actuales) * (1 - CAIDA_MAX_FOTO) and not forzar:
        raise RuntimeError(f"la foto encogió de {len(actuales):,} a {len(filas):,} renglones: "
                           "parece una lectura trunca de Odoo. No se toca nada.")
    viejo = {(r["sku"].lower(), r["ubicacion"]): r for r in actuales}
    nuevo = {(f[1].lower(), f[2]): f for f in filas}
    borrar = [r["id"] for k, r in viejo.items() if k not in nuevo]
    escribir = [f for k, f in nuevo.items()
                if k not in viejo or tuple(viejo[k][c] for c in _L_DATOS) != f[3:]]
    nuevos = sum(1 for k in nuevo if k not in viejo)
    if aplicar:
        # Primero se borra: un quant que Odoo reemplazó por otro en la misma
        # ubicación chocaría con el único de `odoo_quant_id`.
        if borrar:
            cur.execute("delete from almacen.locations where id = any(%s) and origen = 'odoo'", (borrar,))
        if escribir:
            execute_values(cur, _L_UPSERT, escribir, template=_L_PLANTILLA, page_size=1000)
    return {"total": len(filas), "nuevos": nuevos, "cambiados": len(escribir) - nuevos,
            "borrados": len(borrar)}


_H_COLS = ("cedis, sku, fecha, causa, piezas, delta, ubicacion_origen, ubicacion_destino, "
           "documento, referencia, contraparte, tipo_operacion, quien, "
           "odoo_move_line_id, odoo_product_id, odoo_origen, odoo_destino, odoo_escrito_at")
_H_LISTA = tuple(c.strip() for c in _H_COLS.split(","))
_H_LINEA = _H_LISTA.index("odoo_move_line_id")
_H_DATOS = tuple(c for c in _H_LISTA if c not in ("cedis", "odoo_move_line_id"))
_H_PLANTILLA = "(" + ",".join(["%s"] * len(_H_LISTA)) + ")"
_H_UPSERT = (f"insert into almacen.historial_movimientos ({_H_COLS}) values %s "
             "on conflict (odoo_move_line_id, cedis) do update set "
             + ", ".join(f"{c} = excluded.{c}" for c in _H_DATOS))


def escribir_historial(cur: Any, cedis: str, filas: list[tuple], *, completo: bool,
                       aplicar: bool, forzar: bool = False) -> dict[str, Any]:
    """Escribe las líneas nuevas o cambiadas. En la pasada COMPLETA además borra lo
    que ya no viene de Odoo (un producto que se archivó después)."""
    ids = [f[_H_LINEA] for f in filas]
    if not completo and not filas:
        return {"leidas": 0, "nuevos": 0, "cambiados": 0, "borrados": 0}
    sql = "select id, " + ", ".join(_H_LISTA) + " from almacen.historial_movimientos where cedis = %s"
    if completo:
        cur.execute(sql, (cedis,))
    else:
        cur.execute(sql + " and odoo_move_line_id = any(%s)", (cedis, ids))
    viejo = {r["odoo_move_line_id"]: r for r in cur.fetchall()}
    if completo:
        if not filas:
            raise RuntimeError("el historial de Odoo llegó vacío: se trata como falla, no como cero")
        if viejo and len(filas) < len(viejo) * (1 - CAIDA_MAX_HISTORIAL) and not forzar:
            raise RuntimeError(f"el historial encogió de {len(viejo):,} a {len(filas):,} líneas: "
                               "parece una lectura trunca de Odoo. No se toca nada.")
    escribir = [f for f in filas
                if f[_H_LINEA] not in viejo
                or tuple(viejo[f[_H_LINEA]][c] for c in _H_LISTA) != f]
    nuevos = sum(1 for f in filas if f[_H_LINEA] not in viejo)
    frescos = set(ids)
    borrar = [r["id"] for k, r in viejo.items() if k not in frescos] if completo else []
    if aplicar:
        if borrar:
            cur.execute("delete from almacen.historial_movimientos where id = any(%s)", (borrar,))
        if escribir:
            execute_values(cur, _H_UPSERT, escribir, template=_H_PLANTILLA, page_size=1000)
    return {"leidas": len(filas), "nuevos": nuevos, "cambiados": len(escribir) - nuevos,
            "borrados": len(borrar)}


def cuadre(cur: Any, cedis: str) -> dict[str, int]:
    """Lo mismo que `cuadre_filas`, pero sobre lo que quedó escrito en las tablas."""
    cur.execute("""
        with h as (select lower(sku::text) as sku, sum(delta) as v
                     from almacen.historial_movimientos where cedis = %s group by 1),
             l as (select lower(sku::text) as sku, sum(piezas) as v
                     from almacen.locations where cedis = %s group by 1)
        select count(*) filter (where coalesce(h.v, 0) <> 0 or l.sku is not null) as skus,
               count(*) filter (where coalesce(h.v, 0) <> coalesce(l.v, 0)) as descuadres,
               (array_agg(sku order by sku) filter (where coalesce(h.v, 0) <> coalesce(l.v, 0)))[1:20] as cuales,
               (select count(*) from almacen.historial_movimientos where cedis = %s) as movimientos,
               (select count(*) from almacen.locations where cedis = %s) as ubicaciones
          from h full join l using (sku)""", (cedis, cedis, cedis, cedis))
    r = cur.fetchone()
    return {**{k: int(r[k]) for k in ("skus", "descuadres", "movimientos", "ubicaciones")},
            "cuales": list(r["cuales"] or [])}


# ─────────────────────────────────────────────────────────────────────────────
# La pasada
# ─────────────────────────────────────────────────────────────────────────────

def sincronizar(cedis: str, *, completo: bool, abrir: Abrir, aplicar: bool = True,
                forzar: bool = False) -> dict[str, Any]:
    """Una pasada de un cedis: foto + historial + cuadre. `abrir()` da un cursor de
    renglones-diccionario y CONFIRMA al salir (`supabase_db.get_cursor`, o la
    conexión propia del script). Lanza `NoAplica` si el cedis ya no se copia y
    cualquier otra excepción si algo falló: no deja nada a medias que la siguiente
    pasada no termine."""
    # 1) Guardas y marca de agua, en una lectura corta. La conexión se SUELTA antes
    #    de leer Odoo: esa lectura tarda segundos y no debe ocupar un lugar del pool.
    with abrir() as cur:
        bodega = guardas(cur, cedis)
        marca = None if completo else marca_de_agua(cur, cedis)
    completo = completo or marca is None

    # 2) Odoo, sin ninguna conexión a la base abierta.
    almacenes = leer_almacenes()
    alm = almacenes.get(cedis)
    if not alm or not alm["vista"]:
        raise RuntimeError(f"Odoo no tiene un almacén con código {cedis}")
    if alm["id"] != bodega["odoo_warehouse_id"]:
        raise RuntimeError(f"{cedis} es el almacén {alm['id']} en Odoo y "
                           f"{bodega['odoo_warehouse_id']} en el catálogo de bodegas de kubera")
    cod_alm = {v["id"]: k for k, v in almacenes.items()}
    desde = None if completo else marca - TRASLAPE
    m = leer_movimientos(alm["vista"], desde)
    f_his, s_his = filas_historial(cedis, m["lineas"], m["ubis"], m["movs"], m["picks"],
                                   m["productos"], cod_alm)
    quants, prods = leer_foto(alm["vista"])
    f_loc, s_loc = filas_locations(cedis, quants, prods)

    # 3) Una transacción corta: guardas otra vez, escribir y medir.
    with abrir() as cur:
        guardas(cur, cedis)
        r_loc = escribir_locations(cur, cedis, f_loc, aplicar=aplicar, forzar=forzar)
        r_his = escribir_historial(cur, cedis, f_his, completo=completo, aplicar=aplicar, forzar=forzar)
        if aplicar:
            medido = cuadre(cur, cedis)
        else:
            medido = cuadre_filas(f_loc, f_his) if completo else None
    return {"cedis": cedis, "nombre": bodega["nombre"], "completo": completo, "aplicado": aplicar,
            "desde": desde, "foto": {**s_loc, **r_loc}, "historial": {**s_his, **r_his},
            "cuadre": medido}


def resumen(r: dict[str, Any]) -> str:
    f, h, c = r["foto"], r["historial"], r["cuadre"]
    foto = (f"foto omitida ({f['omitida']})" if f.get("omitida") else
            f"foto {f['total']:,} renglones (+{f['nuevos']} ~{f['cambiados']} −{f['borrados']})")
    his = (f"historial {'completo' if r['completo'] else 'delta'}: {h['leidas']:,} leídas "
           f"(+{h['nuevos']} ~{h['cambiados']} −{h['borrados']})")
    cua = "" if c is None else (f" · cuadre {c['skus'] - c['descuadres']:,} de {c['skus']:,} SKUs"
                                + (f" · {c['movimientos']:,} movimientos" if "movimientos" in c else ""))
    return f"{r['nombre']}: {foto} · {his}{cua}"


# Estado del vigilante, por proceso: la primera pasada de cada arranque es completa.
_ultima_completa: dict[str, datetime] = {}
_callados: set[str] = set()
_sin_cuadrar: dict[str, set[str]] = {}   # los SKUs que no cuadraron en la pasada anterior


def vigilar() -> list[dict[str, Any]]:
    """La pasada del scheduler: cada cedis observado, uno por uno. NUNCA lanza: lo
    que falle se anota y la siguiente pasada lo reintenta. Bloquea (Odoo y la
    base): el scheduler la corre en un hilo (regla 11)."""
    from services import supabase_db as sdb

    hechas: list[dict[str, Any]] = []
    for cedis in CEDIS_OBSERVADOS:
        ahora = datetime.now(timezone.utc)
        ultima = _ultima_completa.get(cedis)
        completo = ultima is None or ahora - ultima >= COMPLETA_CADA
        try:
            r = sincronizar(cedis, completo=completo, abrir=sdb.get_cursor)
        except NoAplica as exc:
            if cedis not in _callados:      # se dice una vez por arranque, no cada 30 min
                log.info("Almacén · %s ya no se copia de Odoo: %s.", cedis, exc)
                _callados.add(cedis)
            continue
        except Exception as exc:  # noqa: BLE001 — una pasada fallida no tumba al scheduler
            log.warning("Almacén · %s: la copia de Odoo falló (%s: %s). Se reintenta en la "
                        "siguiente pasada.", cedis, type(exc).__name__, str(exc)[:300])
            continue
        _callados.discard(cedis)
        if r["completo"]:
            _ultima_completa[cedis] = ahora
        hubo = any(r[k].get(c) for k in ("foto", "historial") for c in ("nuevos", "cambiados", "borrados"))
        # El cuadre sólo dice algo mientras la foto se siga copiando de Odoo.
        c = {} if r["foto"].get("omitida") else (r["cuadre"] or {})
        ahora_mal = set(c.get("cuales") or [])
        repetidos = sorted(ahora_mal & _sin_cuadrar.get(cedis, set()))
        _sin_cuadrar[cedis] = ahora_mal
        if repetidos:
            # Un movimiento que cae entre la lectura del historial y la de la foto
            # descuadra UNA pasada y se corrige solo. Dos seguidas ya no es eso.
            log.warning("Almacén · %s · %d SKU(s) llevan dos pasadas sin cuadrar entre historial "
                        "y foto: %s", resumen(r), c["descuadres"], ", ".join(repetidos[:10]))
        elif hubo or r["completo"] or ahora_mal:
            log.info("Almacén · %s", resumen(r))
        hechas.append(r)
    return hechas
