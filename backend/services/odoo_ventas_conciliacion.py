"""
odoo_ventas_conciliacion.py — Órdenes CONFIRMADAS en Odoo cuya venta está CANCELADA en el canal.

POR QUÉ EXISTE (14-sep-2026)
----------------------------
Brandon: "en Automatización debe de mostrarme las órdenes confirmadas que fueron
canceladas". Medido ese día en TikTok: ~81 órdenes en `sale` con la venta
CANCELLED en channel.orders. Casi todas son capturas A MANO (Gabriela), sin
`client_order_ref`, así que `odoo_ventas.cancelar_orden` —que busca por ref— no
las ve y nadie las cancela.

Una orden viva por una venta muerta es una de dos cosas, y piden gente distinta:
  · la entrega sigue PENDIENTE → la orden reserva inventario de una venta que ya
    no existe: `free_qty` no lo ofrece y stock_watch lo quita de los canales.
    Hay que cancelarla en Odoo. ES LO PRIMERO DE LA LISTA: se resuelve hoy, con
    un clic, y cada hora que pasa el stock sigue escondido.
  · la ENTREGA SALIÓ y no hay devolución → la mercancía se fue con una venta que
    no se cobró. El almacén tiene que revisar si regresó (y registrarla).

SÓLO LECTURA
------------
Lee channel.orders (kubera), Odoo (`sale.order`, `stock.picking`) y, en TikTok,
el estado vivo de las ventas abiertas (`GET /order/202309/orders`). No cancela,
no escribe, no crea devoluciones: eso lo decide una persona con la orden
abierta. Por eso puede nacer encendido (la regla 3 es para lo que escribe).

QUÉ ES "CANCELADA", POR CANAL — MEDIDO, NO SUPUESTO
--------------------------------------------------
SELECT del 14-sep a `channel.orders`:
  · TikTok: `CANCELLED` (164) y `cancelled` (1, del 7-ago, entrada vieja por
    M2E). Se compara en mayúsculas.
  · Temu:   `2` (35), `pending` (2, M2E), `4` (1). NINGUNO es cancelación: el
    enum de Temu no se publica y el código de cancelada todavía no se ha visto.
    Inventarlo pintaría como canceladas ventas vivas. Queda VACÍO.
Además, en los dos canales, `estado_wc = 'cancelled'`: es la traducción de la
propia tubería (`_ESTADOS_WC` de cada canal), no un código ajeno. En TikTok
coincide 1:1 con CANCELLED.

TEMU NO ES DETECTABLE HOY — Y NO SE VA A ENCENDER SOLO
------------------------------------------------------
Una versión anterior de este encabezado prometía que el día que alguien mapeara
el código de cancelada de Temu "esta lista se enciende sola". Es FALSO, por dos
razones que se suman:
  1. `pedidos_temu_sondeo` pregunta si la venta ya está registrada y, si lo
     está, la SALTA sin leer su estado. channel.orders se queda con el estado
     con que nació la venta: medido el 14-sep, 0 de 38 filas de Temu se
     actualizaron más de un día después de nacer.
  2. Mapear la cancelada sólo alcanzaría a las ventas que NACEN canceladas, y
     ésas no generan orden en Odoo: no hay nada que conciliar.
Antes de encender la detección hay que actualizar el estado de lo YA registrado.
Mientras tanto el canal sale con `detectable=False` y su razón, y la pantalla
dice "no disponible" en vez de "no hay nada que revisar": decir que no hay
nada, cuando lo cierto es que no se puede saber, es peor que no decir nada.

EL ESTADO GUARDADO ENVEJECE (TikTok)
------------------------------------
channel.orders sólo cambia cuando llega el aviso de TikTok. Medido el 14-sep: el
último `actualizado_at` de TikTok es del 4-sep, y hay una AWAITING_SHIPMENT del
14-ago y una IN_TRANSIT del 31-ago sin tocar. Si TikTok las canceló después,
con sólo lo guardado sus órdenes no saldrían aquí. Por eso:
  · la respuesta dice DE CUÁNDO es lo guardado (`estado_al` = el último
    `actualizado_at` del canal en la ventana), y
  · las ventas cuyo estado guardado NO es terminal se le preguntan a TikTok en
    vivo (lotes de 50, tope `TOPE_VIVO`, `tiktok_diagnostico.estados_vivos`,
    que pasa cada orden por la lista blanca `_orden_sin_pii`). Las que TikTok
    contesta CANCELLED entran al cruce con Odoo marcadas `estado_vivo`.
Desde la laptop TikTok rechaza la IP: ese fallo queda en `vivo.error` y la
lista sale con lo guardado, diciendo que no pudo confirmarlo.

CÓMO SE AMARRA UNA ORDEN DE ODOO A SU VENTA
-------------------------------------------
Las mismas dos huellas que `tiktok_diagnostico._marcar_odoo`:
  · `client_order_ref` (cortado en `#`: un surtido dividido lleva `<id>#1`,
    `<id>#2` y es la misma venta) → origen "automatica".
  · el nombre del PDF de la guía, `<id>.pdf` en `meli_etiqueta_filename` → la
    huella de la captura a mano → origen "manual".
Y en DOS pasadas, por la misma razón que allá: la primera acota por el partner
del canal; la segunda, sin partner, busca por id lo que no apareció. Medido el
14-sep: 5 de 119 ventas entregadas de TikTok estaban capturadas con OTRO
cliente. Sin la segunda pasada esas órdenes no existirían para esta pantalla.

SIN DATOS DEL COMPRADOR
-----------------------
De Odoo se pide sólo: nombre S…, estado, ref, nombre del PDF (nunca el binario),
fecha y pickings. No se pide `partner_id` —en una captura con otro cliente ES
el comprador— ni direcciones. De channel.orders: id, estados y fechas. De
TikTok: sólo lo que deja pasar `_orden_sin_pii`. La fila de salida se arma
campo por campo (lista blanca).

REGLA 11, Y UN SOLO BARRIDO A LA VEZ
------------------------------------
`supabase_db` (psycopg2) y `odoo_ventas._kw` (XML-RPC) bloquean: todo va por
`asyncio.to_thread`, con techo `wait_for`. Pero `wait_for` NO mata el hilo, y
el XML-RPC de Odoo no tiene timeout (odoo.py usa `ServerProxy` a secas): si
Odoo se cuelga sin cerrar la conexión, cada barrido deja un hilo del ejecutor
compartido ocupado. Sin freno, cada admin que abre la página o pica Actualizar
ocuparía otro — el mismo ejecutor de todo el trabajo de la regla 11.
Por eso: caché corta por (canal, días) — `_CACHE_OK_S`, y `_CACHE_ERROR_S`
para un resultado con error, que es justo cuando no conviene insistir — y un
`asyncio.Lock` que deja UN barrido en curso; quien llega mientras tanto espera
y se lleva lo que calculó el primero. No se toca el transporte de Odoo: es
código compartido.

Nunca lanza: un fallo queda descrito en el resultado (`error`,
`cruce_incompleto`, `vivo.error`), no como traza.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("omnicanal.odoo_ventas_conciliacion")

CANALES = ("tiktok", "temu")

_CUENTA = {"tiktok": "TIKTOK", "temu": "TEMU"}

# Ver el encabezado: medido con un SELECT el 14-sep-2026. Se compara en MAYÚSCULAS.
_CANCELADA_ESTADO_CANAL: dict[str, frozenset[str]] = {
    "tiktok": frozenset({"CANCELLED"}),
    "temu": frozenset(),
}

# Canales cuya cancelación HOY no se puede ver, con la razón que lee la pantalla.
# Ver el encabezado ("TEMU NO ES DETECTABLE HOY"). Sacar a Temu de aquí exige
# antes que su sondeo actualice el estado de lo ya registrado.
_NO_DETECTABLE: dict[str, str] = {
    "temu": ("el sondeo de Temu salta las ventas que ya registró sin volver a leer su "
             "estado, así que channel.orders se queda con el estado con que nació cada "
             "venta y nunca ve una cancelación"),
}

# Estados TERMINALES por canal, para decidir a quién preguntarle el estado vivo.
# Sólo los canales listados se consultan en vivo (TikTok llega desde Railway).
_TERMINALES: dict[str, frozenset[str]] = {
    "tiktok": frozenset({"CANCELLED", "DELIVERED", "COMPLETED"}),
}
# Cuántas ventas abiertas se le preguntan a TikTok por barrido (4 lotes de 50),
# las más recientes primero. Las demás se cuentan en `vivo.omitidas`.
TOPE_VIVO = 200

# Techos por paso. `wait_for` sobre un `to_thread` no mata el hilo, pero devuelve
# el control: la pantalla contesta aunque Odoo se quede colgado.
_T_BD = 60
_T_ODOO = 180
_T_VIVO = 60

# Caché y candado (ver el encabezado, "UN SOLO BARRIDO A LA VEZ").
_CACHE_OK_S = 90
_CACHE_ERROR_S = 20
_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_candado_loop: dict[str, Any] = {"loop": None, "lock": None}
_reloj = time.monotonic

# Un id de TikTok es numérico y largo; se busca dentro de un nombre de PDF que no
# sea exactamente `<id>.pdf` (p. ej. `<id> (1).pdf`, bajado dos veces).
_DIGITOS = re.compile(r"\d{15,}")

# Cuántos ids por consulta en la pasada sin partner (cada id suma dos términos
# `=like`/`ilike` al dominio).
_TROZO = 50

# Qué hacer con cada orden, en orden de urgencia (así se ordena la lista).
# `cancelar_en_odoo` va PRIMERO: es lo que se resuelve hoy y lo que la pantalla
# pinta en rojo. Detrás de 84 ámbar, con una muestra de 5 filas, no se veía.
QUE_HACER = ("cancelar_en_odoo", "revisar_regreso", "validar_devolucion", "devuelta")


# ── utilidades sin red ───────────────────────────────────────────────────────

def _err(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "tiempo agotado"
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def _iso(v: Any) -> str | None:
    """datetime (psycopg2) o texto de Odoo 'YYYY-MM-DD HH:MM:SS' (UTC) → ISO Z."""
    if not v:
        return None
    try:
        if isinstance(v, datetime):
            d = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        else:
            d = datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def criterio(canal: str) -> str:
    """Qué cuenta como cancelada en ese canal, dicho para quien lee la pantalla."""
    estados = sorted(_CANCELADA_ESTADO_CANAL.get(canal, frozenset()))
    if estados:
        return (f"estado en el canal {', '.join(estados)} (o el pedido de Woo quedó "
                "cancelado)")
    return ("todavía no se conoce el código de cancelada de este canal; sólo cuenta "
            "si el pedido de Woo quedó cancelado")


def detectable(canal: str) -> tuple[bool, str | None]:
    """
    (¿se pueden ver hoy sus cancelaciones?, razón si no). Un canal sin estados de
    cancelada conocidos tampoco lo es: una lista vacía ahí no significa "nada que
    revisar", significa "no sé".
    """
    if canal in _NO_DETECTABLE:
        return False, _NO_DETECTABLE[canal]
    if not _CANCELADA_ESTADO_CANAL.get(canal):
        return False, "todavía no se conoce el código de cancelada de este canal"
    return True, None


def venta_de_orden(fila: dict[str, Any], buscadas: set[str] | frozenset[str]
                   ) -> tuple[str | None, str | None]:
    """
    (venta, origen) de una orden de Odoo, o (None, None) si no es de las buscadas.

    El ref manda: si lleva `client_order_ref` de una venta buscada es
    "automatica". Si no, el nombre del PDF de la guía ("manual"). Mismas reglas
    que `tiktok_diagnostico._marcar_odoo`, pero por ORDEN y no por venta: aquí
    cada orden es un renglón (un surtido dividido son dos).
    """
    ref = str(fila.get("client_order_ref") or "").strip()
    if ref:
        venta = ref.split("#", 1)[0].strip()
        if venta in buscadas:
            return venta, "automatica"
    nombre = str(fila.get("meli_etiqueta_filename") or "").strip()
    if nombre:
        tallo = nombre[:-4].strip() if nombre.lower().endswith(".pdf") else nombre
        if tallo in buscadas:
            return tallo, "manual"
        for c in _DIGITOS.findall(tallo):
            if c in buscadas:
                return c, "manual"
    return None, None


def _o(terminos: list[list[Any]]) -> list[Any]:
    """OR de n términos en la notación polaca de los dominios de Odoo."""
    return ["|"] * (len(terminos) - 1) + terminos


def clasificar(pickings: list[dict[str, Any]], salida_ids: set[int] | None = None
               ) -> dict[str, Any]:
    """
    Estado de la entrega de salida y de la devolución de UNA orden.

    `pickings` = los de la orden más las devoluciones halladas por `return_id`.
    SÓLO ENTREGAS DE SALIDA para "entrega": en ruta de dos pasos cuelgan PICK y
    PACK (internos), y un PICK hecho no significa que la mercancía salió.
      · hecha      — alguna salida en `done`: la mercancía se fue del almacén.
      · pendiente  — hay salidas vivas y ninguna hecha.
      · sin_entrega — sin salidas, o todas canceladas.
    Devolución: una ENTRADA que cuelga de la orden, o un picking cuyo
    `return_id` apunta a una de sus salidas. Las canceladas no cuentan.
    """
    salidas = [p for p in pickings if p.get("picking_type_code") == "outgoing"]
    vivas = [p for p in salidas if p.get("state") != "cancel"]
    ids_salida = {p.get("id") for p in salidas} | set(salida_ids or ())
    if any(p.get("state") == "done" for p in vivas):
        entrega = "hecha"
    elif vivas:
        entrega = "pendiente"
    else:
        entrega = "sin_entrega"

    def _es_devolucion(p: dict[str, Any]) -> bool:
        if p.get("state") == "cancel":
            return False
        origen = p.get("return_id")
        origen_id = origen[0] if isinstance(origen, (list, tuple)) and origen else origen
        return bool(origen_id and origen_id in ids_salida) or (
            p.get("picking_type_code") == "incoming")

    devs = {p.get("id"): p for p in pickings if _es_devolucion(p)}
    registrada = bool(devs)
    hecha = any(p.get("state") == "done" for p in devs.values())
    if entrega == "hecha":
        que = ("devuelta" if hecha else "validar_devolucion" if registrada
               else "revisar_regreso")
    else:
        que = "cancelar_en_odoo"
    return {"entrega": entrega, "devolucion_registrada": registrada,
            "devolucion_hecha": hecha, "que_hacer": que}


# ── lecturas (BLOQUEAN: sólo desde un hilo) ──────────────────────────────────

def _ventas_canceladas(canal: str, dias: int) -> dict[str, Any]:
    """
    ⚠️ BLOQUEA. Las ventas canceladas del canal en la ventana, qué estados hay,
    de cuándo es lo guardado y —en los canales con estado vivo— las ventas
    ABIERTAS (estado no terminal), las más recientes primero, hasta `TOPE_VIVO`.
    """
    from services import supabase_db as sdb
    desde = datetime.now(timezone.utc) - timedelta(days=int(dias))
    base = {"canal": canal, "cuenta": _CUENTA[canal], "desde": desde}
    filas = sdb.fetch_all(
        """select external_order_id, estado_canal, estado_wc, creado_at
             from channel.orders
            where (canal = %(canal)s or cuenta = %(cuenta)s)
              and creado_at >= %(desde)s
              and (upper(coalesce(estado_canal, '')) = any(%(estados)s::text[])
                   or estado_wc = 'cancelled')""",
        {**base, "estados": sorted(_CANCELADA_ESTADO_CANAL[canal])})
    vistos = sdb.fetch_all(
        """select coalesce(estado_canal, '') estado, count(*) n,
                  max(actualizado_at) ultimo
             from channel.orders
            where (canal = %(canal)s or cuenta = %(cuenta)s)
              and creado_at >= %(desde)s
            group by 1 order by 2 desc""",
        base)
    ventas: dict[str, dict[str, Any]] = {}
    for f in filas:
        vid = str(f.get("external_order_id") or "").strip()
        if vid:
            ventas[vid] = {"estado_canal": f.get("estado_canal"),
                           "venta_at": _iso(f.get("creado_at")),
                           "_creado": f.get("creado_at")}
    ultimos = [v.get("ultimo") for v in vistos if isinstance(v.get("ultimo"), datetime)]
    abiertas: dict[str, dict[str, Any]] = {}
    abiertas_total = 0
    if canal in _TERMINALES:
        filas_ab = sdb.fetch_all(
            """select external_order_id, estado_canal, creado_at,
                      count(*) over () total
                 from channel.orders
                where (canal = %(canal)s or cuenta = %(cuenta)s)
                  and creado_at >= %(desde)s
                  and upper(coalesce(estado_canal, '')) <> all(%(terminales)s::text[])
                  and coalesce(estado_wc, '') <> 'cancelled'
                order by creado_at desc
                limit %(tope)s""",
            {**base, "terminales": sorted(_TERMINALES[canal]), "tope": TOPE_VIVO})
        for f in filas_ab:
            abiertas_total = max(abiertas_total, int(f.get("total") or 0))
            vid = str(f.get("external_order_id") or "").strip()
            if vid and vid not in ventas:
                abiertas[vid] = {"estado_canal": f.get("estado_canal"),
                                 "venta_at": _iso(f.get("creado_at")),
                                 "_creado": f.get("creado_at")}
    return {"ventas": ventas,
            "estados_vistos": {str(v["estado"]): int(v["n"]) for v in vistos},
            "estado_al": _iso(max(ultimos)) if ultimos else None,
            "abiertas": abiertas, "abiertas_total": abiertas_total}


def _ordenes_confirmadas(canal: str, ventas: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """
    ⚠️ BLOQUEA (XML-RPC). Las órdenes CONFIRMADAS de Odoo de esas ventas, con el
    estado de su entrega y de su devolución. Sin datos del comprador.
    """
    from services import odoo_ventas as ov

    if not ventas:
        return []
    buscadas = set(ventas)
    confirmadas = ["state", "in", list(ov._CONFIRMADAS)]  # noqa: SLF001
    campos = ["name", "state", "client_order_ref", "meli_etiqueta_filename",
              "date_order", "picking_ids"]
    por_id: dict[int, tuple[dict[str, Any], str, str]] = {}

    def _tomar(filas: list[dict[str, Any]] | None) -> None:
        for f in filas or []:
            if f.get("state") not in ov._CONFIRMADAS:  # noqa: SLF001
                continue
            venta, origen = venta_de_orden(f, buscadas)
            if venta:
                por_id.setdefault(f["id"], (f, venta, origen))

    # 1 · Por partner del canal, desde una semana antes de la venta más vieja: una
    #     orden no se captura antes de que exista la venta.
    fechas = [v["_creado"] for v in ventas.values() if isinstance(v.get("_creado"), datetime)]
    dominio: list[Any] = [["partner_id", "=", ov._PARTNER[canal]], confirmadas]  # noqa: SLF001
    if fechas:
        corte = min(fechas) - timedelta(days=7)
        dominio.append(["create_date", ">=", corte.astimezone(timezone.utc)
                        .strftime("%Y-%m-%d %H:%M:%S")])
    _tomar(ov._kw("sale.order", "search_read", [dominio],  # noqa: SLF001
                  {"fields": campos, "limit": 5000}))

    # 2 · Por id, SIN partner ni fecha, para lo que no apareció (capturas con otro
    #     cliente). `ilike` sobre el nombre del PDF: encuentra `<id> (1).pdf`.
    halladas = {v for _x, v, _y in por_id.values()}
    faltan = sorted(buscadas - halladas)
    for i in range(0, len(faltan), _TROZO):
        trozo = faltan[i:i + _TROZO]
        terminos: list[list[Any]] = [["client_order_ref", "in", trozo]]
        for x in trozo:
            terminos.append(["client_order_ref", "=like", f"{x}#%"])
            terminos.append(["meli_etiqueta_filename", "ilike", x])
        _tomar(ov._kw("sale.order", "search_read", [[confirmadas, *_o(terminos)]],  # noqa: SLF001
                      {"fields": campos, "limit": 1000}))
    if not por_id:
        return []

    # 3 · Pickings: los de cada orden, y las devoluciones que apunten a sus salidas
    #     aunque no cuelguen de la orden (una devolución hecha desde la entrega).
    todos = sorted({p for f, _x, _y in por_id.values() for p in (f.get("picking_ids") or [])})
    campos_pk = ["picking_type_code", "state", "return_id"]
    pickings: dict[int, dict[str, Any]] = {}
    if todos:
        for p in ov._kw("stock.picking", "read", [todos, campos_pk]):  # noqa: SLF001
            pickings[p["id"]] = p
    salidas = sorted(i for i, p in pickings.items() if p.get("picking_type_code") == "outgoing")
    de_retorno: dict[int, list[dict[str, Any]]] = {}
    if salidas:
        for p in ov._kw("stock.picking", "search_read",  # noqa: SLF001
                        [[["return_id", "in", salidas]]], {"fields": campos_pk}):
            origen = p.get("return_id")
            oid = origen[0] if isinstance(origen, (list, tuple)) and origen else origen
            if oid:
                de_retorno.setdefault(oid, []).append(p)

    filas_out: list[dict[str, Any]] = []
    for f, venta, origen in por_id.values():
        propios = [pickings[i] for i in (f.get("picking_ids") or []) if i in pickings]
        ids_salida = {p["id"] for p in propios if p.get("picking_type_code") == "outgoing"}
        extra = [p for s in ids_salida for p in de_retorno.get(s, [])
                 if p.get("id") not in {q["id"] for q in propios}]
        c = clasificar(propios + extra, ids_salida)
        v = ventas[venta]
        # LISTA BLANCA: la fila se arma campo por campo.
        filas_out.append({
            "canal": canal,
            "odoo_order_id": f["id"],
            "odoo_name": f.get("name"),
            "odoo_estado": f.get("state"),
            "venta": venta,
            "estado_canal": v.get("estado_canal"),
            # True = channel.orders la tenía abierta y TikTok contestó CANCELLED.
            "estado_vivo": bool(v.get("estado_vivo")),
            "origen": origen,
            "fecha_orden": _iso(f.get("date_order")),
            "venta_at": v.get("venta_at"),
            **c,
        })
    return filas_out


def _orden_lista(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lo urgente primero; dentro, lo más reciente primero. `sorted` es estable:
    se ordena del criterio menor al mayor."""
    rango = {q: i for i, q in enumerate(QUE_HACER)}
    filas = sorted(filas, key=lambda r: str(r.get("odoo_name") or ""))
    filas = sorted(filas, key=lambda r: r.get("fecha_orden") or r.get("venta_at") or "",
                   reverse=True)
    return sorted(filas, key=lambda r: rango.get(r.get("que_hacer"), 99))


# ── estado vivo (red, async) ─────────────────────────────────────────────────

async def _estado_vivo(canal: str, abiertas: dict[str, dict[str, Any]], total: int
                       ) -> tuple[dict[str, str], dict[str, Any]]:
    """
    Pregunta al canal el estado ACTUAL de las ventas abiertas. Hoy sólo TikTok.
    → ({venta: estado vivo cancelado}, resumen para la pantalla). Nunca lanza.
    """
    ids = [x for x in abiertas if x.isdigit()][:TOPE_VIVO]
    resumen: dict[str, Any] = {"abiertas": max(total, len(abiertas)), "consultadas": len(ids),
                               "omitidas": max(0, max(total, len(abiertas)) - len(ids)),
                               "respondidas": 0, "canceladas": 0, "al": None, "error": None}
    if not ids:
        return {}, resumen
    try:
        from services import tiktok_diagnostico as td
        r = await asyncio.wait_for(td.estados_vivos(ids), _T_VIVO)
    except Exception as exc:  # noqa: BLE001
        resumen["error"] = _err(exc)
        return {}, resumen
    vistas = r.get("vistas") or {}
    canceladas = {vid: str(f.get("status") or "") for vid, f in vistas.items()
                  if vid in abiertas
                  and str(f.get("status") or "").upper() in _CANCELADA_ESTADO_CANAL[canal]}
    errores = r.get("errores") or []
    resumen.update(respondidas=len(vistas), canceladas=len(canceladas),
                   al=_ahora_iso() if (vistas or not errores) else None,
                   error="; ".join(errores) or None)
    return canceladas, resumen


# ── la función pública ───────────────────────────────────────────────────────

async def _un_canal(canal: str, dias: int) -> dict[str, Any]:
    det, razon = detectable(canal)
    fuera: dict[str, Any] = {"ok": False, "criterio": criterio(canal),
                             "detectable": det, "razon_no_detectable": razon,
                             "ventas_canceladas": 0, "estados_vistos": {},
                             "estado_al": None, "vivo": None, "leido_at": _ahora_iso(),
                             "ordenes": [], "por_que_hacer": {}, "total": 0,
                             "cruce_incompleto": False, "error": None}
    try:
        leido = await asyncio.wait_for(
            asyncio.to_thread(_ventas_canceladas, canal, dias), _T_BD)
    except Exception as exc:  # noqa: BLE001
        fuera.update(error=f"channel.orders: {_err(exc)}", cruce_incompleto=True)
        return fuera
    ventas = leido["ventas"]
    fuera.update(estados_vistos=leido["estados_vistos"], estado_al=leido.get("estado_al"))
    if canal in _TERMINALES:
        vivas, resumen = await _estado_vivo(canal, leido.get("abiertas") or {},
                                            int(leido.get("abiertas_total") or 0))
        for vid, st in vivas.items():
            ventas.setdefault(vid, {**leido["abiertas"][vid], "estado_canal": st,
                                    "estado_vivo": True})
        fuera["vivo"] = resumen
    fuera["ventas_canceladas"] = len(ventas)
    try:
        filas = await asyncio.wait_for(
            asyncio.to_thread(_ordenes_confirmadas, canal, ventas), _T_ODOO)
    except Exception as exc:  # noqa: BLE001
        fuera.update(error=f"odoo: {_err(exc)}", cruce_incompleto=True)
        return fuera
    filas = _orden_lista(filas)
    conteo: dict[str, int] = {}
    for r in filas:
        conteo[r["que_hacer"]] = conteo.get(r["que_hacer"], 0) + 1
    fuera.update(ok=True, ordenes=filas, total=len(filas), por_que_hacer=conteo)
    return fuera


def limpiar_cache() -> None:
    """Olvida lo leído (pruebas, o para forzar un barrido nuevo)."""
    _cache.clear()


def _candado() -> asyncio.Lock:
    """El candado del barrido, uno por event loop (un Lock no se comparte entre loops)."""
    loop = asyncio.get_running_loop()
    if _candado_loop["loop"] is not loop:
        _candado_loop.update(loop=loop, lock=asyncio.Lock())
    return _candado_loop["lock"]


def _de_cache(clave: tuple[str, int]) -> dict[str, Any] | None:
    guardado = _cache.get(clave)
    if not guardado:
        return None
    t, r = guardado
    edad = _reloj() - t
    con_error = bool(r.get("error")) or bool((r.get("vivo") or {}).get("error"))
    if edad < 0 or edad >= (_CACHE_ERROR_S if con_error else _CACHE_OK_S):
        return None
    return {**r, "cache_edad_s": int(edad)}


async def _un_canal_cacheado(canal: str, dias: int) -> dict[str, Any]:
    clave = (canal, dias)
    r = _de_cache(clave)
    if r is not None:
        return r
    async with _candado():
        # Quien esperaba el candado encuentra lo que acaba de calcular el primero.
        r = _de_cache(clave)
        if r is not None:
            return r
        r = await _un_canal(canal, dias)
        ahora = _reloj()
        for k in [k for k, (t, _v) in _cache.items() if ahora - t >= max(_CACHE_OK_S, _CACHE_ERROR_S)]:
            _cache.pop(k, None)
        _cache[clave] = (ahora, r)
        return {**r, "cache_edad_s": 0}


async def canceladas_confirmadas(canal: str | None = None, dias: int = 90) -> dict[str, Any]:
    """
    Órdenes CONFIRMADAS en Odoo (state sale/done) cuya venta está CANCELADA en el
    canal. Una fila por orden de Odoo. Sólo lectura, sin datos del comprador,
    nunca lanza.

    `canal` = tiktok | temu | None (los dos). `dias` acota por la fecha en que
    la venta entró a channel.orders (1..365). Cada canal sale de la caché si es
    reciente (`cache_edad_s`), y nunca hay dos barridos a la vez.
    """
    generado = _ahora_iso()
    try:
        dias = max(1, min(int(dias), 365))
    except (TypeError, ValueError):
        dias = 90
    # `isinstance` y no `or ""`: llamada directa a la función del router, el
    # valor por omisión es el `Query(...)` de FastAPI, no None.
    elegido = canal.strip().lower() if isinstance(canal, str) else ""
    if elegido and elegido not in CANALES:
        return {"ok": False, "generado": generado, "dias": dias, "canales": {},
                "ordenes": [], "error": f"canal '{elegido[:20]}' no soportado (tiktok | temu)"}
    canales: dict[str, dict[str, Any]] = {}
    # En serie: son lecturas a Odoo y a kubera, y no hay prisa que justifique
    # duplicar la carga sobre el XML-RPC.
    for c in ([elegido] if elegido else list(CANALES)):
        try:
            canales[c] = await _un_canal_cacheado(c, dias)
        except Exception as exc:  # noqa: BLE001 — cinturón: _un_canal no lanza
            det, razon = detectable(c)
            canales[c] = {"ok": False, "ordenes": [], "total": 0, "error": _err(exc),
                          "cruce_incompleto": True, "criterio": criterio(c),
                          "detectable": det, "razon_no_detectable": razon}
    ordenes = [r for c in canales.values() for r in c.get("ordenes") or []]
    errores = [f"{c}: {d['error']}" for c, d in canales.items() if d.get("error")]
    if errores:
        log.warning("canceladas-confirmadas incompleto: %s", "; ".join(errores))
    return {"ok": not errores, "generado": generado, "dias": dias,
            "canales": canales, "ordenes": ordenes,
            "error": "; ".join(errores) or None}
