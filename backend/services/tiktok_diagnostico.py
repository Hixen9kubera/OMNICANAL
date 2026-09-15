"""
tiktok_diagnostico.py — ¿TikTok sigue avisándonos de las ventas, y se perdió alguna?

POR QUÉ EXISTE (14-sep-2026)
----------------------------
El aviso de PEDIDO de TikTok (`ORDER_STATUS_CHANGE`) estuvo vivo hasta el 4-sep.
Desde el 10-sep a `ops.webhook_events` sólo llegan avisos de PRODUCTO (tipos
15/16/37/68). Eso admite dos lecturas muy distintas —"no hubo ventas" o "la
suscripción de pedidos se cayó y las ventas no entran"— y NO se pueden separar
mirando nuestra base: un silencio se ve igual en los dos casos.

Las dos preguntas sólo las contesta TikTok, y TikTok rechaza la IP de la laptop
(`36009033 IP not in allow list`): la allowlist sólo trae la salida de Railway.
Por eso esto vive en el backend y no en un script de escritorio.

  1. `suscripciones()` → ¿la tienda sigue suscrita a ORDER_STATUS_CHANGE, y
     apuntando a NUESTRA URL?
  2. `ventas(dias)`    → ¿qué ventas tiene TikTok en la ventana, y cuáles no
     aparecen en NINGUNO de nuestros tres registros?
  3. `estado_ids(ids)` → para ventas NOMBRADAS: su estado VIVO en TikTok y en
     qué registro está cada una. Existe porque channel.orders puede guardar un
     estado viejo (una AWAITING_SHIPMENT del 14-ago que nadie actualizó) y la
     búsqueda por fecha no enseña lo que quedó fuera de la ventana.

LOS TRES REGISTROS, Y POR QUÉ HACEN FALTA LOS TRES
--------------------------------------------------
  · `channel.orders` (cuenta TIKTOK) — el candado de idempotencia: lo que entró
    por la tubería (`pedidos_ml.sincronizar`).
  · `ops.odoo_sale_orders` (canal tiktok) — la bitácora del seam de Odoo.
  · Odoo mismo. Aquí está la trampa: las ~214 órdenes que Gabriela capturó A
    MANO no llevan `client_order_ref`, así que la idempotencia de
    `odoo_ventas.crear_orden` NO las ve. Lo único que las amarra a la venta es
    el PDF de la guía, nombrado `<order_id>.pdf` (`meli_etiqueta_filename`).
    Una venta capturada a mano NO está perdida, y "recuperarla" crearía una
    orden DUPLICADA en Odoo — el mismo accidente que obligó a pedir ids
    explícitos en la recuperación de Temu.
    Y no es la única huella que esa idempotencia no ve: `buscar_por_ref`
    compara el ref EXACTO y con el partner de TikTok, así que tampoco halla un
    surtido dividido (`<id>#1`) ni una orden capturada con otro cliente. Por
    eso la recuperación omite TODO lo que dejó huella fuera de channel.orders.

SÓLO LECTURA
------------
El diagnóstico no escribe en TikTok (ni un PUT a webhooks: suscribir es un
efecto real, ver docs/TIKTOK_MANUAL.md), ni en Odoo, ni en kubera, ni en Woo.
La única escritura es `recuperar(..., aplicar=True)`, con ids nombrados.

SIN DATOS DEL COMPRADOR
-----------------------
Una orden de TikTok trae domicilio, nombre, teléfono y correo del comprador.
De cada orden se extrae SÓLO: id, status, create_time, update_time, total y
moneda, si trae guía, shipping_type y número de líneas — en `_orden_sin_pii`, el
ÚNICO sitio que toca la orden cruda. Todo lo demás del módulo trabaja sobre esa
lista blanca, así que un campo nuevo de TikTok no puede colarse a un log.

NUNCA LANZA
-----------
Corre como job de arranque y detrás de un endpoint de diagnóstico: un fallo
tiene que quedar DESCRITO en el resultado, no convertirse en una traza que
nadie lee. Y un registro que no se pudo leer se reporta como tal
(`cruce_incompleto`): "no sé si está" no es "no está".

REGLA 11
--------
`tk.access_token()`/`tk.cipher()` (lectura de BD), `supabase_db.*` (psycopg2) y
`odoo_ventas._kw` (XML-RPC) bloquean: todos van por `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.tiktok_diagnostico")

# El evento de la VENTA. Nombre verificado contra el listado real el 12-ago
# (tk_webhooks_alta.py lo dio de alta y lo leyó de vuelta con este GET).
EVENTO_PEDIDOS = "ORDER_STATUS_CHANGE"
_RUTA_WEBHOOKS = "/event/202309/webhooks"
_RUTA_BUSCAR = "/order/202309/orders/search"
_RUTA_DETALLE = "/order/202309/orders"

# 100 es el máximo que admite `orders/search`. Con ~6-30 ventas al día, 45 días
# caben en 1-14 páginas; 50 es un techo para una paginación que no termina
# (token que no avanza), no un límite que se espere tocar. Si se toca, el
# resultado lo dice (`cortado_por_tope`) en vez de dar un total falso.
_PAGE_SIZE = 100
_MAX_PAGINAS = 50

# Mismo tope que la recuperación de Temu: el peor caso de un error de dedo es
# acotado y está escrito en la petición.
TOPE_RECUPERAR = 25

# `estado_ids` (sólo lectura). `GET /order/202309/orders` acepta hasta 50 ids por
# llamada ("Max count: 50" en la doc, separados por coma). 200 = 4 llamadas: un
# techo contra un pegado accidental, no un límite que se espere tocar. Los que
# pasan del tope NO se consultan y el resultado dice cuántos (`omitidos`).
LOTE_DETALLE = 50
TOPE_ESTADO_IDS = 200

# Techos por paso. `wait_for` sobre un `to_thread` no mata el hilo, pero SÍ
# devuelve el control: el diagnóstico contesta aunque Odoo se quede colgado.
_T_TIKTOK = 90
_T_BD = 60
_T_ODOO = 180

# México es UTC-6 FIJO desde 2022 (sin horario de verano). El reparto por día
# se lee en hora de México porque así cuenta las ventas quien las revisa; con
# UTC, una venta de las 19:00 caería "mañana".
_MX = timezone(timedelta(hours=-6))

# Un id de orden de TikTok es numérico y largo (18 dígitos hoy). Se usa para
# encontrarlo dentro de un nombre de PDF que no sea exactamente `<id>.pdf`
# (p. ej. `577….pdf` descargado dos veces → `577… (1).pdf`).
_DIGITOS = re.compile(r"\d{15,}")


# ── utilidades sin red ───────────────────────────────────────────────────────

def _err(exc: BaseException) -> str:
    """Descripción corta de un fallo. `TimeoutError` llega con mensaje vacío."""
    if isinstance(exc, asyncio.TimeoutError):
        return "tiempo agotado"
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def _epoch(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _iso(epoch: Any) -> str | None:
    e = _epoch(epoch)
    if e is None:
        return None
    try:
        return datetime.fromtimestamp(e, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/").lower()


def direccion_esperada() -> str:
    """La URL a la que TikTok debe mandar los avisos: la misma que enseña
    `/api/webhooks/activos`, derivada del redirect del OAuth (mismo host)."""
    base = (settings.tiktok_redirect_uri or "").split("/api/")[0].rstrip("/")
    return f"{base}/api/webhooks/tiktok"


def _orden_sin_pii(o: dict[str, Any]) -> dict[str, Any]:
    """
    LA LISTA BLANCA. Es el único sitio del módulo que lee una orden cruda.

    Se construye un dict NUEVO campo por campo, en vez de copiar la orden y
    borrar lo sensible: con una lista negra, el día que TikTok agregue un campo
    con datos del comprador se colaría solo. Aquí sólo sale lo que está escrito.
    """
    pago = o.get("payment") if isinstance(o.get("payment"), dict) else {}
    lineas = o.get("line_items") if isinstance(o.get("line_items"), list) else []
    # La guía viene en el encabezado y en cada línea (medido el 28-ago). Sólo
    # interesa SI existe: el número no aporta al diagnóstico.
    guia = bool(o.get("tracking_number")) or any(
        isinstance(l, dict) and l.get("tracking_number") for l in lineas)
    return {
        "id": str(o.get("id") or "").strip(),
        "status": str(o.get("status") or "").strip().upper(),
        "create_time": _epoch(o.get("create_time")),
        "update_time": _epoch(o.get("update_time")),
        "total": _float(pago.get("total_amount")),
        "moneda": str(pago.get("currency") or "") or None,
        "tiene_guia": guia,
        "shipping_type": str(o.get("shipping_type") or "") or None,
        "n_lineas": len(lineas),
    }


def _publica(f: dict[str, Any]) -> dict[str, Any]:
    """La fila tal como sale al resultado: tiempos legibles."""
    return {**f, "create_time": _iso(f.get("create_time")),
            "update_time": _iso(f.get("update_time"))}


def _estados_wc() -> dict[str, str]:
    # Se importa la tabla del flujo REAL, no una copia: si mañana se agrega
    # ON_HOLD a lo que genera pedido, el diagnóstico lo sigue sin tocarse.
    from services.pedidos_tiktok import _ESTADOS_WC  # noqa: PLC0415
    return _ESTADOS_WC


def _genera_pedido(status: str) -> bool:
    destino = _estados_wc().get((status or "").upper())
    return bool(destino) and destino != "cancelled"


async def _credenciales() -> tuple[str | None, str | None]:
    """Token y cipher. Las dos son lecturas de BD SÍNCRONAS: en hilo (regla 11)."""
    from services import tiktok as tk

    def _leer() -> tuple[str | None, str | None]:
        return tk.access_token(), tk.cipher()

    return await asyncio.wait_for(asyncio.to_thread(_leer), _T_BD)


# ── 1. SUSCRIPCIONES ─────────────────────────────────────────────────────────

async def suscripciones() -> dict[str, Any]:
    """
    ¿La tienda sigue suscrita al aviso de PEDIDOS, y a nuestra URL?

    SOLO LECTURA: `GET /event/202309/webhooks`. No se re-suscribe nada aunque
    falte — eso lo decide una persona (regla 3).

    `falta_pedidos` es None cuando no se pudo leer: "no sé" no es "falta".
    `pedidos_a_otra_direccion` separa el caso en que el evento SÍ existe pero
    apunta a otro host (un redeploy con otra URL, otra app): TikTok avisa, pero
    no a nosotros, y el síntoma es idéntico a no estar suscrito.
    """
    from services import tiktok as tk

    esperada = direccion_esperada()
    fuera: dict[str, Any] = {"ok": False, "eventos": [], "falta_pedidos": None,
                             "pedidos_a_otra_direccion": None,
                             "direccion_esperada": esperada,
                             "direcciones_distintas": [], "error": None}
    try:
        token, ciph = await _credenciales()
        if not (token and ciph):
            fuera["error"] = "TikTok sin token o sin shop_cipher"
            return fuera
        data = await asyncio.wait_for(
            tk.llamar(_RUTA_WEBHOOKS, token, {"shop_cipher": ciph}), _T_TIKTOK)
        lista = (data or {}).get("webhooks") or []
        eventos = [{"event_type": str(w.get("event_type") or ""),
                    "address": str(w.get("address") or ""),
                    "update_time": _iso(w.get("update_time"))}
                   for w in lista if isinstance(w, dict)]
        norm = _norm_url(esperada)
        pedidos = [e for e in eventos if e["event_type"].upper() == EVENTO_PEDIDOS]
        fuera.update(
            ok=True,
            eventos=eventos,
            falta_pedidos=not pedidos,
            pedidos_a_otra_direccion=(any(_norm_url(e["address"]) != norm for e in pedidos)
                                      if pedidos else None),
            direcciones_distintas=sorted({e["address"] for e in eventos
                                          if _norm_url(e["address"]) != norm}),
        )
    except Exception as exc:  # noqa: BLE001 — nunca lanza
        fuera["error"] = _err(exc)
    return fuera


# ── 2. VENTAS ────────────────────────────────────────────────────────────────

async def _buscar_ordenes(token: str, ciph: str, desde: int) -> dict[str, Any]:
    """
    Pagina `orders/search` por `create_time_ge` hasta agotar `next_page_token`.

    `page_size`/`page_token` van en la QUERY y los filtros en el CUERPO (así lo
    separan la doc 202309 y los clientes que la implementan). El cuerpo entra
    en la firma: `tk.llamar` ya lo hace.

    Deduplica por id: si entra una venta nueva mientras se pagina, el corrimiento
    puede repetir una orden entre páginas y no debe contarse dos veces.
    """
    from services import tiktok as tk

    vistas: dict[str, dict[str, Any]] = {}
    page_token = ""
    tokens_vistos: set[str] = set()
    paginas, cortado, total_declarado, error = 0, False, None, None
    while True:
        if paginas >= _MAX_PAGINAS:
            cortado = True
            log.warning("tiktok_diagnostico: orders/search se cortó en el tope de "
                        "%s páginas con %s órdenes leídas; el total NO es completo.",
                        _MAX_PAGINAS, len(vistas))
            break
        params: dict[str, Any] = {"shop_cipher": ciph, "page_size": _PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        try:
            data = await asyncio.wait_for(
                tk.llamar(_RUTA_BUSCAR, token, params, {"create_time_ge": desde}, "POST"),
                _T_TIKTOK)
        except Exception as exc:  # noqa: BLE001
            error = f"orders/search página {paginas + 1}: {_err(exc)}"
            break
        data = data or {}
        paginas += 1
        if total_declarado is None:
            total_declarado = _epoch(data.get("total_count"))
        lote = data.get("orders") or []
        for o in lote:
            if isinstance(o, dict):
                f = _orden_sin_pii(o)
                if f["id"]:
                    vistas[f["id"]] = f
        page_token = str(data.get("next_page_token") or "")
        if not page_token or not lote:
            break
        if page_token in tokens_vistos:
            # Un token que se repite es un bucle: seguir sería pedir lo mismo
            # hasta el tope. Se corta y se dice.
            error = "orders/search repitió el next_page_token; paginación detenida"
            break
        tokens_vistos.add(page_token)
    return {"ordenes": vistas, "paginas": paginas, "cortado_por_tope": cortado,
            "total_declarado": total_declarado, "error": error}


def _en_channel_orders(ids: list[str]) -> set[str]:
    """⚠️ BLOQUEA. Las ventas que ya entraron por la tubería."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """select external_order_id
             from channel.orders
            where (cuenta = 'TIKTOK' or canal = 'tiktok')
              and external_order_id = any(%(ids)s)""", {"ids": list(ids)})
    return {str(f["external_order_id"]) for f in filas}


def _en_bitacora(ids: list[str]) -> set[str]:
    """⚠️ BLOQUEA. Las ventas que pasaron por el seam de Odoo."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """select external_order_id
             from ops.odoo_sale_orders
            where canal = 'tiktok'
              and external_order_id = any(%(ids)s)""", {"ids": list(ids)})
    return {str(f["external_order_id"]) for f in filas}


def _marcar_odoo(filas: list[dict[str, Any]] | None, buscados: set[str],
                 hallados: dict[str, str]) -> None:
    """
    Amarra órdenes de Odoo a ventas: por `client_order_ref` ("ref") o por el
    nombre del PDF de la guía ("pdf", la huella de la captura a mano).

    El ref se corta en `#`: un surtido dividido lleva `<id>#1`/`<id>#2` y es la
    misma venta. Si una venta aparece por los dos caminos, gana "ref": significa
    que la creó el automatismo, y ésa es idempotente.
    """
    for f in filas or []:
        ref = f.get("client_order_ref")
        if ref:
            venta = str(ref).split("#", 1)[0].strip()
            if venta in buscados:
                hallados[venta] = "ref"
        nombre = f.get("meli_etiqueta_filename")
        if nombre:
            tallo = str(nombre).strip()
            if tallo.lower().endswith(".pdf"):
                tallo = tallo[:-4].strip()
            for c in {tallo, *_DIGITOS.findall(tallo)}:
                if c in buscados:
                    hallados.setdefault(c, "pdf")


def _o(terminos: list[list[Any]]) -> list[Any]:
    """OR de n términos en la notación polaca de los dominios de Odoo."""
    return ["|"] * (len(terminos) - 1) + terminos


def _en_odoo(ids: list[str], desde: int | None = None,
             ya_registrados: set[str] | frozenset[str] = frozenset()) -> dict[str, str]:
    """
    ⚠️ BLOQUEA (XML-RPC). {venta: "ref" | "pdf"} para las ventas que Odoo tiene.

    Dos pasadas, de barata a cara:
      1. Con ventana (`desde`): las órdenes del partner TikTok creadas desde dos
         días antes de la venta más vieja. Una orden no puede capturarse antes
         de que exista la venta, así que la ventana no deja fuera ninguna. Es
         UNA consulta acotada por partner y fecha, y lee los nombres de PDF
         completos: encuentra el id aunque el archivo no se llame exactamente
         `<id>.pdf`.
      2. Por id, SIN partner ni fecha, sólo para lo que siga sin aparecer: por
         si alguien capturó la venta con otro cliente o la fecha no cuadra.
         Sin partner a propósito — aquí equivocarse hacia "ya existe" es lo
         seguro: una venta no marcada como faltante no se recupera, y una
         recuperación de más duplica la orden. Es la cara (`ilike` sin índice
         sobre todo `sale.order`, que carga también las ventas de ML), así que
         se salta `ya_registrados`: lo que la tubería ya conoce no puede ser
         faltante ni captura a mano, y preguntarlo sólo cuesta.

    Nunca pide `meli_etiqueta_file` (el binario): sólo su nombre.
    """
    from services import odoo_ventas as ov

    buscados = {str(i) for i in ids}
    hallados: dict[str, str] = {}
    campos = ["client_order_ref", "meli_etiqueta_filename"]
    if desde is not None:
        corte = datetime.fromtimestamp(int(desde) - 2 * 86400, timezone.utc)
        filas = ov._kw("sale.order", "search_read",  # noqa: SLF001
                       [[["partner_id", "=", ov._PARTNER["tiktok"]],  # noqa: SLF001
                         ["create_date", ">=", corte.strftime("%Y-%m-%d %H:%M:%S")]]],
                       {"fields": campos, "limit": 5000})
        _marcar_odoo(filas, buscados, hallados)
    faltan = sorted(buscados - set(hallados) - set(ya_registrados))
    for i in range(0, len(faltan), 50):
        trozo = faltan[i:i + 50]
        terminos: list[list[Any]] = [["client_order_ref", "in", trozo]]
        for x in trozo:
            terminos.append(["client_order_ref", "=like", f"{x}#%"])
            terminos.append(["meli_etiqueta_filename", "ilike", x])
        filas = ov._kw("sale.order", "search_read", [_o(terminos)],  # noqa: SLF001
                       {"fields": campos, "limit": 1000})
        _marcar_odoo(filas, buscados, hallados)
    return hallados


async def _registros(ids: list[str], desde: int | None = None,
                     acotar_odoo: bool = False) -> dict[str, Any]:
    """
    Los tres registros para esos ids. Cada fallo queda en `errores`, aparte.

    `acotar_odoo` (el diagnóstico) salta la búsqueda cara en Odoo para lo que ya
    está en channel.orders o en la bitácora. La recuperación NO lo usa: son a lo
    más 25 ids y su vista en seco enseña lo que Odoo tiene de cada uno.
    """
    canal: set[str] = set()
    bitacora: set[str] = set()
    odoo: dict[str, str] = {}
    errores: list[str] = []
    if ids:
        try:
            canal = await asyncio.wait_for(asyncio.to_thread(_en_channel_orders, ids), _T_BD)
        except Exception as exc:  # noqa: BLE001
            errores.append(f"channel.orders: {_err(exc)}")
        try:
            bitacora = await asyncio.wait_for(asyncio.to_thread(_en_bitacora, ids), _T_BD)
        except Exception as exc:  # noqa: BLE001
            errores.append(f"ops.odoo_sale_orders: {_err(exc)}")
        try:
            ya = (canal | bitacora) if acotar_odoo else frozenset()
            odoo = await asyncio.wait_for(
                asyncio.to_thread(_en_odoo, ids, desde, ya), _T_ODOO)
        except Exception as exc:  # noqa: BLE001
            errores.append(f"odoo: {_err(exc)}")
    return {"channel_orders": canal, "bitacora": bitacora, "odoo": odoo,
            "errores": errores}


async def ventas(dias: int = 14) -> dict[str, Any]:
    """
    Las ventas de TikTok de los últimos `dias`, cruzadas contra los tres
    registros. SIN datos del comprador.

    `faltantes` = no está en NINGUNO. `faltantes_vendibles` = de ésas, las que
    `pedidos_tiktok._ESTADOS_WC` sí convierte en pedido y no son cancelación:
    las que de verdad hay que recuperar. `capturadas_a_mano` = Odoo las tiene
    sólo por el PDF y la tubería no: NO se recuperan (duplicaría la orden).
    """
    try:
        dias = max(1, min(int(dias), 45))
    except (TypeError, ValueError):
        dias = 14
    desde = int(time.time()) - dias * 86400
    fuera: dict[str, Any] = {
        "ok": False, "dias": dias, "desde": _iso(desde),
        "total": 0, "total_declarado": None, "paginas": 0, "cortado_por_tope": False,
        "por_estado": {}, "por_dia": {},
        "cruce": {"channel_orders": 0, "bitacora": 0, "odoo_ref": 0, "odoo_pdf": 0},
        "cruce_incompleto": False,
        "faltantes": [], "faltantes_vendibles": [], "capturadas_a_mano": [],
        "para_recuperar": "", "error": None,
    }
    try:
        token, ciph = await _credenciales()
        if not (token and ciph):
            fuera["error"] = "TikTok sin token o sin shop_cipher"
            return fuera
        b = await _buscar_ordenes(token, ciph, desde)
        vistas: dict[str, dict[str, Any]] = b["ordenes"]
        errores = [b["error"]] if b["error"] else []
        fuera.update(total=len(vistas), total_declarado=b["total_declarado"],
                     paginas=b["paginas"], cortado_por_tope=b["cortado_por_tope"])

        por_estado: dict[str, int] = {}
        por_dia: dict[str, int] = {}
        for f in vistas.values():
            por_estado[f["status"] or "?"] = por_estado.get(f["status"] or "?", 0) + 1
            dia = (datetime.fromtimestamp(f["create_time"], _MX).strftime("%Y-%m-%d")
                   if f["create_time"] is not None else "sin fecha")
            por_dia[dia] = por_dia.get(dia, 0) + 1
        fuera["por_estado"] = dict(sorted(por_estado.items()))
        fuera["por_dia"] = dict(sorted(por_dia.items(), reverse=True))

        ids = list(vistas)
        viejo = min((f["create_time"] for f in vistas.values()
                     if f["create_time"] is not None), default=desde)
        reg = await _registros(ids, min(viejo, desde), acotar_odoo=True)
        errores.extend(reg["errores"])
        canal, bitacora, odoo = reg["channel_orders"], reg["bitacora"], reg["odoo"]
        fuera["cruce"] = {
            "channel_orders": len(canal & set(ids)),
            "bitacora": len(bitacora & set(ids)),
            "odoo_ref": sum(1 for v in odoo.values() if v == "ref"),
            "odoo_pdf": sum(1 for v in odoo.values() if v == "pdf"),
        }
        fuera["cruce_incompleto"] = bool(reg["errores"])

        ordenadas = sorted(vistas.values(), key=lambda f: f["create_time"] or 0,
                           reverse=True)
        faltantes = [f for f in ordenadas
                     if f["id"] not in canal and f["id"] not in bitacora
                     and f["id"] not in odoo]
        vendibles = [f for f in faltantes if _genera_pedido(f["status"])]
        fuera["faltantes"] = [_publica(f) for f in faltantes]
        fuera["faltantes_vendibles"] = [_publica(f) for f in vendibles]
        fuera["capturadas_a_mano"] = [f["id"] for f in ordenadas
                                      if odoo.get(f["id"]) == "pdf"
                                      and f["id"] not in canal]
        # Listo para pegar en POST /tiktok/recuperar. Con el tope aplicado: si
        # hay más, se recuperan por tandas y el resto sale en la siguiente
        # corrida del diagnóstico.
        fuera["para_recuperar"] = ",".join(f["id"] for f in vendibles[:TOPE_RECUPERAR])
        fuera["ok"] = not errores
        fuera["error"] = "; ".join(errores) or None
    except Exception as exc:  # noqa: BLE001 — nunca lanza
        fuera["error"] = _err(exc)
    return fuera


# ── 3. EL DIAGNÓSTICO COMPLETO ───────────────────────────────────────────────

async def diagnosticar(dias: int = 14) -> dict[str, Any]:
    """
    Suscripciones + ventas. Nunca lanza.

    En serie y no con `gather`: las dos leen el token, y con un token vencido
    las dos dispararían el refresh a la vez. El candado anti-estampida de
    `tiktok.refrescar_y_guardar` lo aguanta, pero no hay prisa que lo justifique.
    """
    generado = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        s = await suscripciones()
    except Exception as exc:  # noqa: BLE001 — cinturón: suscripciones() no lanza
        s = {"ok": False, "eventos": [], "falta_pedidos": None, "error": _err(exc)}
    try:
        v = await ventas(dias)
    except Exception as exc:  # noqa: BLE001
        v = {"ok": False, "faltantes": [], "faltantes_vendibles": [], "error": _err(exc)}
    return {"ok": bool(s.get("ok") and v.get("ok")), "generado": generado,
            "suscripciones": s, "ventas": v}


def resumen_linea(d: dict[str, Any]) -> str:
    """
    El diagnóstico en UNA línea de log: conteos, veredicto de la suscripción,
    direcciones y los ids faltantes con su estado. Sin datos del comprador
    (sólo lee campos de la lista blanca). Nunca lanza.
    """
    try:
        s = d.get("suscripciones") or {}
        v = d.get("ventas") or {}
        partes = ["TIKTOK diagnostico"]
        if s.get("ok"):
            tipos = ",".join(sorted(str(e.get("event_type")) for e in s.get("eventos") or []))
            partes.append(
                f"webhooks={len(s.get('eventos') or [])}[{tipos}] "
                f"falta_pedidos={s.get('falta_pedidos')} "
                f"pedidos_otra_dir={s.get('pedidos_a_otra_direccion')} "
                f"esperada={s.get('direccion_esperada')} "
                f"distintas={s.get('direcciones_distintas') or []}")
        else:
            partes.append(f"webhooks=ERROR({s.get('error')})")
        faltan = v.get("faltantes") or []
        vendibles = v.get("faltantes_vendibles") or []
        estados = ",".join(f"{k}:{n}" for k, n in (v.get("por_estado") or {}).items())
        cruce = v.get("cruce") or {}
        partes.append(
            f"ventas{v.get('dias', '?')}d={v.get('total', 0)} "
            f"declarado={v.get('total_declarado')} paginas={v.get('paginas', 0)}"
            f"{' CORTADO' if v.get('cortado_por_tope') else ''} [{estados}] "
            f"cruce=co:{cruce.get('channel_orders', 0)}/bit:{cruce.get('bitacora', 0)}"
            f"/odoo_ref:{cruce.get('odoo_ref', 0)}/odoo_pdf:{cruce.get('odoo_pdf', 0)}"
            f"{' INCOMPLETO' if v.get('cruce_incompleto') else ''} "
            f"faltantes={len(faltan)} vendibles={len(vendibles)} "
            f"a_mano={len(v.get('capturadas_a_mano') or [])}")
        if faltan:
            muestra = ",".join(f"{f.get('id')}:{f.get('status')}" for f in faltan[:40])
            partes.append(f"ids={muestra}{f' +{len(faltan) - 40}' if len(faltan) > 40 else ''}")
        if v.get("error"):
            partes.append(f"error_ventas={str(v.get('error'))[:300]}")
        return " · ".join(partes)
    except Exception as exc:  # noqa: BLE001
        return f"TIKTOK diagnostico: no se pudo resumir ({type(exc).__name__})"


# ── 4. RECUPERACIÓN CON IDS EXPLÍCITOS ───────────────────────────────────────

def _parsear_ids(ids: str | list[str] | None) -> list[str]:
    if isinstance(ids, str):
        crudos = re.split(r"[,\s]+", ids)
    else:
        crudos = [str(x) for x in (ids or [])]
    vistos: list[str] = []
    for x in crudos:
        x = x.strip()
        if x and x not in vistos:
            vistos.append(x)
    return vistos


async def _estado_en_tiktok(token: str, ciph: str, oid: str) -> dict[str, Any]:
    """Estado actual de UNA orden (lectura). Uno por uno: no está verificado
    cómo acepta `ids` varias órdenes en la query, y son a lo más 25."""
    from services import tiktok as tk
    try:
        data = await asyncio.wait_for(
            tk.llamar(_RUTA_DETALLE, token, {"shop_cipher": ciph, "ids": oid}), _T_TIKTOK)
        ordenes = (data or {}).get("orders") or []
        if not ordenes or not isinstance(ordenes[0], dict):
            return {"status": None, "error": "TikTok no devolvió la orden"}
        f = _orden_sin_pii(ordenes[0])
        return {"status": f["status"], "genera_pedido": _genera_pedido(f["status"]),
                "total": f["total"], "tiene_guia": f["tiene_guia"],
                "create_time": _iso(f["create_time"])}
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "error": _err(exc)}


async def recuperar(ids: str | list[str] | None, aplicar: bool = False) -> dict[str, Any]:
    """
    Mete a la tubería ventas de TikTok que no entraron, NOMBRADAS una por una.

    Mismo molde que `POST /temu/recuperar`, por la misma razón: un barrido por
    fechas que se pasara de la raya recrearía ventas capturadas a mano, y en
    Odoo eso es una orden duplicada reservando inventario.

    EN SECO por omisión: por cada id dice en qué registro está, su estado en
    TikTok y qué haría. Sólo lee.

    CON `aplicar=True` cada id pasa por `pedidos_tiktok.procesar` —el MISMO
    camino del webhook: orden de TikTok → pedido de Woo → seam de Odoo—, con
    DOS candados:
      · una venta que la tubería NO conoce (no está en `channel.orders`) pero
        que YA dejó huella en otro registro se OMITE (`_registro_previo`).
        `sincronizar` sólo pregunta a channel.orders: no halla pedido previo,
        crea otro en Woo, y el seam llama a `crear_orden`, cuyo candado
        (`buscar_por_ref`) sólo ve el ref EXACTO con el partner de TikTok.
      · si algún registro no se pudo leer, NO se aplica nada (falla cerrado):
        sin esa lectura el primer candado no puede decidir.
    Las ventas que YA están en `channel.orders` sí pasan: `sincronizar` es
    idempotente y el seam sólo crea orden cuando no había pedido previo.

    Nunca lanza.
    """
    try:
        lista = _parsear_ids(ids)
        if not lista:
            return {"ok": False, "motivo": "sin ids"}
        if len(lista) > TOPE_RECUPERAR:
            return {"ok": False, "recibidos": len(lista),
                    "motivo": f"{len(lista)} ids: el tope es {TOPE_RECUPERAR} por llamada"}
        invalidos = [x[:40] for x in lista if not x.isdigit()]
        if invalidos:
            return {"ok": False, "invalidos": invalidos,
                    "motivo": "los ids de orden de TikTok son numéricos"}

        reg = await _registros(lista)
        canal, bitacora, odoo = reg["channel_orders"], reg["bitacora"], reg["odoo"]

        def _donde(oid: str) -> dict[str, Any]:
            return {"channel_orders": oid in canal, "bitacora": oid in bitacora,
                    "odoo": odoo.get(oid)}

        def _registro_previo(oid: str) -> str | None:
            """
            ¿La venta ya está FUERA de la tubería? El nombre de la huella, o None.

            Con la venta en channel.orders no hay riesgo (None): `sincronizar`
            halla el pedido y el seam no crea orden. Sin ella, cualquier otra
            huella es un duplicado esperando a ocurrir:
              · odoo_pdf  — captura a mano, sin ref: `buscar_por_ref` no la ve.
              · odoo_ref  — `<id>#n` (surtido dividido) o capturada con otro
                cliente (la búsqueda sin ventana no filtra partner, a
                propósito): `buscar_por_ref` tampoco la ve. Con ref exacto la
                orden de Odoo no se duplicaría, pero el pedido de Woo sí: si
                el seam corrió, el pedido ya existía.
              · bitacora  — la bitácora sólo se escribe desde una venta que
                estuvo en channel.orders (seam o backfill): el pedido de Woo
                existió y lo que se perdió es su registro, no el pedido.
            """
            if oid in canal:
                return None
            huellas = (["bitacora"] if oid in bitacora else []) + (
                [f"odoo_{odoo[oid]}"] if odoo.get(oid) else [])
            return "+".join(huellas) or None

        def _motivo_omision(huella: str) -> str:
            if "odoo_pdf" in huella:
                que = "Odoo la tiene capturada a mano (PDF de la guía, sin referencia)"
            elif "odoo_ref" in huella:
                que = ("Odoo la tiene con una referencia que su idempotencia no "
                       "ve (<id>#n u otro cliente)")
            else:
                que = "la bitácora de Odoo la registró y channel.orders la perdió"
            return (f"{que}: procesarla duplicaría el pedido de Woo y/o la orden "
                    f"de Odoo [{huella}]")

        if not aplicar:
            # Sin token el seco sigue sirviendo: lo que dicen los registros no
            # depende de TikTok, y es lo que decide si una venta se omite.
            try:
                token, ciph = await _credenciales()
            except Exception as exc:  # noqa: BLE001
                log.warning("tiktok_diagnostico: sin credenciales para el seco: %s", _err(exc))
                token, ciph = None, None
            filas = []
            for oid in lista:
                tt = (await _estado_en_tiktok(token, ciph, oid) if token and ciph
                      else {"status": None, "error": "TikTok sin token o sin shop_cipher"})
                huella = _registro_previo(oid)
                if huella:
                    haria = f"SE OMITIRÍA: {_motivo_omision(huella)}"
                elif oid in canal:
                    haria = "re-sincroniza (ya está en channel.orders; idempotente, no crea orden en Odoo)"
                elif tt.get("status") and tt["status"] not in _estados_wc():
                    haria = f"nada: el estado '{tt.get('status')}' no genera pedido"
                elif tt.get("status") and not tt.get("genera_pedido"):
                    haria = ("registra la venta como CANCELADA en Woo; sin orden "
                             "en Odoo (nació cancelada)")
                else:
                    haria = "pedido en Woo + orden de venta en Odoo (según el escalón de odoo_ventas)"
                filas.append({"id": oid, "registros": _donde(oid), "tiktok": tt,
                              "que_haria": haria})
            return {"ok": True, "modo": "EN SECO — no se tocó nada",
                    "recibidos": len(lista), "ids": filas,
                    "cruce_incompleto": bool(reg["errores"]),
                    "errores": reg["errores"],
                    "pedidos_tiktok_enabled": bool(settings.pedidos_tiktok_enabled),
                    "para_aplicar": "repetir con aplicar=true"}

        if reg["errores"]:
            return {"ok": False, "modo": "NO APLICADO",
                    "motivo": "no se pudo verificar contra los registros; sin esa "
                              "lectura no se distingue una captura a mano (falla cerrado)",
                    "errores": reg["errores"]}

        from services import pedidos_tiktok
        resultados = []
        for oid in lista:
            huella = _registro_previo(oid)
            if huella:
                fila = {"id": oid, "ok": False, "accion": "omitida_ya_registrada",
                        "registro": huella, "motivo": _motivo_omision(huella),
                        "pedido_wc": None, "estado_wc": None}
            else:
                try:
                    r = await pedidos_tiktok.procesar(oid)
                except Exception as exc:  # noqa: BLE001 — una mala no tumba las demás
                    r = {"ok": False, "accion": "error", "motivo": _err(exc)}
                r = r if isinstance(r, dict) else {}
                fila = {"id": oid, "ok": bool(r.get("ok")), "accion": r.get("accion"),
                        "registro": None,
                        "motivo": str(r["motivo"])[:200] if r.get("motivo") else None,
                        "pedido_wc": r.get("wc_order_id"), "estado_wc": r.get("estado_wc")}
            log.warning("TIKTOK recuperar %s → ok=%s accion=%s registro=%s motivo=%s "
                        "pedido_wc=%s", oid, fila["ok"], fila["accion"], fila["registro"],
                        fila["motivo"], fila["pedido_wc"])
            resultados.append(fila)
        hechas = sum(1 for x in resultados if x["ok"])
        return {"ok": True, "modo": "APLICADO", "pedidos": len(lista),
                "con_exito": hechas, "sin_exito": len(lista) - hechas,
                "resultados": resultados}
    except Exception as exc:  # noqa: BLE001 — nunca lanza
        return {"ok": False, "motivo": _err(exc)}


# ── 5. ESTADO VIVO DE IDS NOMBRADOS (SÓLO LECTURA) ───────────────────────────

async def estados_vivos(ids: list[str]) -> dict[str, Any]:
    """
    SÓLO la pregunta a TikTok: el estado ACTUAL de ids ya limpios (numéricos,
    sin repetir, acotados por quien llama), en lotes de `LOTE_DETALLE`.

    Es la mitad de red de `estado_ids`, separada para que la reuse la
    conciliación de Automatización (odoo_ventas_conciliacion) sin arrastrar el
    cruce contra los tres registros, que allá no hace falta.

    Cada orden pasa por `_orden_sin_pii`; de la respuesta cruda no sale nada
    más. Token y cipher en hilo (regla 11). Un lote que falla no tumba a los
    demás. Nunca lanza.

    → {"vistas": {id: fila sin PII}, "fallo_por_id": {id: error},
       "lotes": n, "sin_credenciales": bool, "errores": [..]}
    """
    fuera: dict[str, Any] = {"vistas": {}, "fallo_por_id": {}, "lotes": 0,
                             "sin_credenciales": True, "errores": []}
    errores: list[str] = fuera["errores"]
    try:
        from services import tiktok as tk

        try:
            token, ciph = await _credenciales()
        except Exception as exc:  # noqa: BLE001
            token, ciph = None, None
            errores.append(f"credenciales: {_err(exc)}")
        fuera["sin_credenciales"] = not (token and ciph)
        if not (token and ciph):
            if not errores:
                errores.append("TikTok sin token o sin shop_cipher")
            return fuera
        for n, i in enumerate(range(0, len(ids), LOTE_DETALLE), start=1):
            lote = list(ids[i:i + LOTE_DETALLE])
            fuera["lotes"] += 1
            try:
                data = await asyncio.wait_for(
                    tk.llamar(_RUTA_DETALLE, token,
                              {"shop_cipher": ciph, "ids": ",".join(lote)}),
                    _T_TIKTOK)
            except Exception as exc:  # noqa: BLE001 — un lote malo no tumba a los demás
                errores.append(f"orders lote {n}: {_err(exc)}")
                for x in lote:
                    fuera["fallo_por_id"][x] = _err(exc)
                continue
            pedidos = set(lote)
            for o in (data or {}).get("orders") or []:
                if isinstance(o, dict):
                    f = _orden_sin_pii(o)
                    if f["id"] in pedidos:
                        fuera["vistas"][f["id"]] = f
    except Exception as exc:  # noqa: BLE001 — nunca lanza
        errores.append(_err(exc))
    return fuera


async def estado_ids(ids: str | list[str] | None) -> dict[str, Any]:
    """
    Estado ACTUAL en TikTok de órdenes nombradas, y en qué registro está cada una.

    SÓLO LECTURA: `GET /order/202309/orders` en lotes de `LOTE_DETALLE` (50, el
    máximo de la doc) + los tres registros (`_registros`). No procesa nada: para
    meter una venta a la tubería está `recuperar`, con sus candados.

    Cada orden pasa por `_orden_sin_pii` y NADA MÁS sale de la respuesta cruda.

    Un lote que falla no tumba a los demás: sus ids quedan con `error_tiktok` y
    el resto se consulta igual. "TikTok no la devolvió" se dice como tal —puede
    ser un id de otra tienda o mal copiado— y no se confunde con un error.

    Nunca lanza.
    """
    fuera: dict[str, Any] = {"ok": False, "recibidos": 0, "consultados": 0, "omitidos": 0,
                             "invalidos": [], "lotes": 0, "devueltas": 0, "ids": [],
                             "cruce_incompleto": False, "errores": [], "error": None}
    try:
        lista = _parsear_ids(ids)
        fuera["recibidos"] = len(lista)
        fuera["invalidos"] = [x[:40] for x in lista if not x.isdigit()]
        validos = [x for x in lista if x.isdigit()]
        if len(validos) > TOPE_ESTADO_IDS:
            fuera["omitidos"] = len(validos) - TOPE_ESTADO_IDS
            validos = validos[:TOPE_ESTADO_IDS]
        fuera["consultados"] = len(validos)
        if not validos:
            fuera["error"] = ("sin ids" if not lista
                              else "ningún id válido: los ids de orden de TikTok son numéricos")
            return fuera

        viva = await estados_vivos(validos)
        fuera["lotes"] = viva["lotes"]
        errores: list[str] = list(viva["errores"])
        vistas: dict[str, dict[str, Any]] = viva["vistas"]
        fallo_por_id: dict[str, str] = viva["fallo_por_id"]
        sin_credenciales = viva["sin_credenciales"]

        reg = await _registros(validos)
        errores.extend(reg["errores"])
        canal, bitacora, odoo = reg["channel_orders"], reg["bitacora"], reg["odoo"]

        filas = []
        for oid in validos:
            f = vistas.get(oid)
            if f:
                fallo = None
            elif sin_credenciales:
                fallo = "TikTok sin token o sin shop_cipher"
            else:
                fallo = fallo_por_id.get(oid) or "TikTok no devolvió la orden"
            filas.append({
                "id": oid,
                "tiktok": _publica(f) if f else None,
                "genera_pedido": _genera_pedido(f["status"]) if f else None,
                "error_tiktok": fallo,
                "registros": {"channel_orders": oid in canal, "bitacora": oid in bitacora,
                              "odoo": odoo.get(oid)},
            })
        fuera.update(ok=not errores, devueltas=len(vistas), ids=filas,
                     cruce_incompleto=bool(reg["errores"]), errores=errores,
                     error="; ".join(errores) or None)
    except Exception as exc:  # noqa: BLE001 — nunca lanza
        fuera["error"] = _err(exc)
    return fuera


def resumen_ids(r: dict[str, Any]) -> str:
    """
    `estado_ids` en UNA línea de log ("TIKTOK ids …"): por id, su estado vivo,
    si trae guía, la fecha de su última actualización y en qué registro está.
    Sólo campos de la lista blanca. Nunca lanza.
    """
    try:
        partes = [f"TIKTOK ids consultados={r.get('consultados', 0)} "
                  f"devueltas={r.get('devueltas', 0)} lotes={r.get('lotes', 0)}"
                  f"{' INCOMPLETO' if r.get('cruce_incompleto') else ''}"
                  f"{' omitidos=' + str(r.get('omitidos')) if r.get('omitidos') else ''}"]
        for fila in (r.get("ids") or [])[:60]:
            tt = fila.get("tiktok") or {}
            reg = fila.get("registros") or {}
            if tt:
                vivo = (f"{tt.get('status')} guia={int(bool(tt.get('tiene_guia')))} "
                        f"upd={str(tt.get('update_time') or '?')[:10]}")
            else:
                vivo = f"SIN_TIKTOK({str(fila.get('error_tiktok') or '?')[:60]})"
            partes.append(f"{fila.get('id')}:{vivo} co={int(bool(reg.get('channel_orders')))} "
                          f"bit={int(bool(reg.get('bitacora')))} odoo={reg.get('odoo') or '-'}")
        if len(r.get("ids") or []) > 60:
            partes.append(f"+{len(r['ids']) - 60}")
        if r.get("invalidos"):
            partes.append(f"invalidos={','.join(r['invalidos'][:10])}")
        if r.get("error"):
            partes.append(f"error={str(r.get('error'))[:300]}")
        return " · ".join(partes)
    except Exception as exc:  # noqa: BLE001
        return f"TIKTOK ids: no se pudo resumir ({type(exc).__name__})"
