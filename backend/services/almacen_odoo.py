"""
almacen_odoo.py — El HISTORIAL de movimientos que kubera COPIA de Odoo de los
almacenes que todavía se operan allá (`almacen.historial_movimientos`, 0070).

Pedido de Brandon (9-oct-2026): TEXCO II deja de operarse desde Odoo y pasa a
kubera; el historial de movimientos sólo existía allá. El 6-nov pidió el
vigilante de cuarentena (`services/cuarentena.py`), que lee las llegadas a la
cuarentena de ESTE historial: desde entonces se copian los tres almacenes de
Odoo, cada hora.

QUÉ COPIA
  · EL HISTORIAL (`stock.move.line` hechas): un renglón por movimiento, visto
    desde el cedis. Cada pasada pide a Odoo sólo lo ESCRITO desde la última línea
    vista, con 15 min de traslape (el delta). La primera pasada de un cedis sin
    renglones lo trae todo; además, una vez al día (09:00 UTC) se relee todo y se
    concilia.
  · YA NO copia la foto de ubicaciones (`almacen.locations`): desde la 0076 la
    escribe la copia de Eduardo y su candado rechaza a cualquier otro. Las
    funciones puras de la foto se quedan para medir el cuadre a mano
    (`copiar_almacen_odoo.py --medir-cuadre`).

LAS REGLAS (de Brandon)
  · `cedis` obligatorio en todo renglón.
  · El SKU sin rack entra como «SIN UBICAR».
  · Los negativos se traen: salen de movimientos hechos en Odoo.
  · Los productos ARCHIVADOS en Odoo NO se traen («archivados pueden ser
    error»): ni su existencia ni sus movimientos.

LA PRUEBA DE QUE ESTÁ COMPLETO
  Σ delta del historial de un SKU = Σ piezas de su foto en Odoo. Medido el
  9-oct-2026 en TEXCO II: 1,414 de 1,414 productos activos. `cuadre_filas()` lo
  mide con el historial completo en la mano.

LA FIRMA DEL COPIADOR (0076)
  El historial y `almacen.copias_odoo` de una bodega de Odoo sólo los escribe una
  transacción que se firma como la copia: `app.almacen_escritor =
  'copia_odoo:<txid>'`, LOCAL a la transacción (muere con el commit; regla 13).

CUÁNDO SE DETIENE
  Cuando el catálogo de bodegas diga que el cedis ya no es de Odoo
  (`almacen.almacenes.fuente <> 'odoo'`): desde ese día Odoo dejó de ser su
  verdad y la base congela su historial.

LO QUE NO HACE
  No escribe en Odoo (sólo `search_read` y `read`). No toca Woo, el stock que se
  vende ni ningún canal.

CÓMO ESCRIBE
  Cada sentencia es repetible y vale por sí sola. `supabase_db.get_cursor` puede
  cambiar de conexión a media transacción sin avisar; si pasa, la firma se queda
  en la conexión vieja, la base rechaza lo que sigue y la siguiente pasada lo
  termina.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager

from psycopg2.extras import execute_values

from services import odoo as od

log = logging.getLogger("omnicanal.almacen_odoo")

# Los cedis que se observan. Sólo cuentan mientras el catálogo de bodegas los
# tenga con `fuente = 'odoo'`: ése es el interruptor, no una variable.
CEDIS_OBSERVADOS: tuple[str, ...] = ("TEX2", "TEXCO", "DROP")

SIN_UBICAR = "SIN UBICAR"
NAVE = "FERRAFORME"
TRASLAPE = timedelta(minutes=15)       # el delta relee este tramo: una validación larga de Odoo no se escapa
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
# kubera: guardas, firma del copiador y escritura por diferencias
# ─────────────────────────────────────────────────────────────────────────────

def guardas(cur: Any, cedis: str) -> dict[str, Any]:
    """Lo que tiene que ser cierto para copiar. Se revisa DOS veces: antes de leer
    Odoo y otra vez en la transacción que escribe, por si algo cambió entre tanto."""
    cur.execute("select to_regclass('almacen.historial_movimientos') is not null as historial, "
                "coalesce(to_regclass('almacen.almacenes'), to_regclass('ops.almacenes'))::text as catalogo")
    r = cur.fetchone()
    if not (r["historial"] and r["catalogo"]):
        raise NoAplica("faltan almacen.historial_movimientos o el catálogo de bodegas (migración 0070)")
    # `catalogo` viene de to_regclass: es un nombre de tabla que la base ya validó.
    cur.execute(f"select nombre, fuente, odoo_warehouse_id from {r['catalogo']} where codigo = %s",
                (cedis,))
    b = cur.fetchone()
    if not b:
        raise NoAplica(f"el catálogo de bodegas no tiene {cedis}")
    if b["fuente"] != "odoo":
        raise NoAplica(f"{b['nombre']} ya es de {b['fuente']}: Odoo dejó de ser su verdad")
    return {"nombre": b["nombre"], "odoo_warehouse_id": b["odoo_warehouse_id"]}


def firmar(cur: Any) -> None:
    """La firma que la 0076 exige para escribir la copia de una bodega de Odoo.
    `true` = LOCAL a la transacción: muere con el commit y no se queda pegada en
    la conexión compartida del pooler (regla 13)."""
    cur.execute("select set_config('app.almacen_escritor', 'copia_odoo:' || txid_current()::text, true)")


def registrar_copia(cur: Any, cedis: str, *, ok: bool, historial_hasta: datetime | None,
                    error: str | None) -> None:
    """La frescura en `almacen.copias_odoo`. `historial_hasta` es el instante en
    que se LEYÓ Odoo (así lo define la 0076); una falla no lo mueve. Requiere la
    firma en la misma transacción."""
    cur.execute("update almacen.copias_odoo set historial_hasta = coalesce(%s, historial_hasta), "
                "ultima_pasada_at = now(), ultima_pasada_ok = %s, ultimo_error = left(%s, 500) "
                "where almacen = %s", (historial_hasta, ok, error, cedis))


def redactar(exc: BaseException) -> str:
    """Lo que se guarda de una falla: la clase y, sólo si el mensaje es nuestro, el
    mensaje. Los errores de red o de XML-RPC pueden traer la URL de Odoo (0075, S12)."""
    if isinstance(exc, (NoAplica, ValueError)) or type(exc) is RuntimeError:
        return f"{type(exc).__name__}: {exc}"[:500]
    return type(exc).__name__


def marca_de_agua(cur: Any, cedis: str) -> datetime | None:
    cur.execute("select max(odoo_escrito_at) as marca from almacen.historial_movimientos "
                "where cedis = %s", (cedis,))
    return cur.fetchone()["marca"]


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


# ─────────────────────────────────────────────────────────────────────────────
# La pasada
# ─────────────────────────────────────────────────────────────────────────────

def sincronizar(cedis: str, *, completo: bool, abrir: Abrir, aplicar: bool = True,
                forzar: bool = False, medir_cuadre: bool = False) -> dict[str, Any]:
    """Una pasada del historial de un cedis. `abrir()` da un cursor de
    renglones-diccionario y CONFIRMA al salir (`supabase_db.get_cursor`, o la
    conexión propia del script). Lanza `NoAplica` si el cedis ya no se copia y
    cualquier otra excepción si algo falló: no deja nada a medias que la siguiente
    pasada no termine. Con `medir_cuadre` (y el historial completo) lee además la
    existencia de Odoo y mide Σ delta = Σ piezas por SKU; no la escribe."""
    # 1) Guardas y marca de agua, en una lectura corta. La conexión se SUELTA antes
    #    de leer Odoo: esa lectura tarda segundos y no debe ocupar un lugar del pool.
    with abrir() as cur:
        bodega = guardas(cur, cedis)
        marca = None if completo else marca_de_agua(cur, cedis)
    completo = completo or marca is None

    # 2) Odoo, sin ninguna conexión a la base abierta.
    leido_at = datetime.now(timezone.utc)
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
    medido = None
    if medir_cuadre and completo:
        quants, prods = leer_foto(alm["vista"])
        medido = cuadre_filas(filas_locations(cedis, quants, prods)[0], f_his)

    # 3) Una transacción corta: firma, guardas otra vez, escribir y anotar la frescura.
    with abrir() as cur:
        if aplicar:
            firmar(cur)
        guardas(cur, cedis)
        r_his = escribir_historial(cur, cedis, f_his, completo=completo, aplicar=aplicar, forzar=forzar)
        if aplicar:
            registrar_copia(cur, cedis, ok=True, historial_hasta=leido_at, error=None)
    return {"cedis": cedis, "nombre": bodega["nombre"], "completo": completo, "aplicado": aplicar,
            "desde": desde, "historial": {**s_his, **r_his}, "cuadre": medido}


def resumen(r: dict[str, Any]) -> str:
    h, c = r["historial"], r["cuadre"]
    his = (f"historial {'completo' if r['completo'] else 'delta'}: {h['leidas']:,} leídas "
           f"(+{h['nuevos']} ~{h['cambiados']} −{h['borrados']})")
    cua = "" if c is None else f" · cuadre {c['skus'] - c['descuadres']:,} de {c['skus']:,} SKUs"
    return f"{r['nombre']}: {his}{cua}"


# Estado del vigilante, por proceso.
_callados: set[str] = set()
_una_a_la_vez = threading.Lock()     # la pasada de cada hora y la completa del día no se enciman


def _anotar_falla(sdb: Any, cedis: str, exc: BaseException) -> None:
    """Deja la falla en `copias_odoo`. Si tampoco se puede, sólo el log: nunca lanza."""
    try:
        with sdb.get_cursor() as cur:
            firmar(cur)
            registrar_copia(cur, cedis, ok=False, historial_hasta=None, error=redactar(exc))
    except Exception as otra:  # noqa: BLE001
        log.warning("Almacén · %s: tampoco se pudo anotar la falla en copias_odoo (%s).",
                    cedis, type(otra).__name__)


def vigilar(completo: bool = False) -> list[dict[str, Any]]:
    """La pasada del scheduler: cada cedis observado, uno por uno. NUNCA lanza: lo
    que falle se anota y la siguiente pasada lo reintenta. Bloquea (Odoo y la
    base): el scheduler la corre en un hilo (regla 11). `completo` relee todo y
    concilia (la del día); si no, delta (o completa si el cedis aún no tiene nada)."""
    from services import supabase_db as sdb

    if not _una_a_la_vez.acquire(blocking=False):
        log.info("Almacén · ya hay una pasada de la copia de Odoo en curso: ésta se salta.")
        return []
    try:
        hechas: list[dict[str, Any]] = []
        for cedis in CEDIS_OBSERVADOS:
            try:
                r = sincronizar(cedis, completo=completo, abrir=sdb.get_cursor)
            except NoAplica as exc:
                if cedis not in _callados:      # se dice una vez por arranque, no cada hora
                    log.info("Almacén · %s ya no se copia de Odoo: %s.", cedis, exc)
                    _callados.add(cedis)
                continue
            except Exception as exc:  # noqa: BLE001 — una pasada fallida no tumba al scheduler
                log.warning("Almacén · %s: la copia de Odoo falló (%s). Se reintenta en la "
                            "siguiente pasada.", cedis, redactar(exc)[:300])
                _anotar_falla(sdb, cedis, exc)
                continue
            _callados.discard(cedis)
            h = r["historial"]
            if r["completo"] or any(h.get(c) for c in ("nuevos", "cambiados", "borrados")):
                log.info("Almacén · %s", resumen(r))
            hechas.append(r)
        return hechas
    finally:
        _una_a_la_vez.release()
