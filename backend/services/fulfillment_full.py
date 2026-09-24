"""
fulfillment_full.py — CREAR FULL: qué mandar a FULL de Mercado Libre, por
cuenta, y el borrador de la orden en Odoo.

Es la PRIMERA acción de la pestaña FULLFILMENT (Brandon, 24-sep-2026: "como las
aplicaciones de banco: primero la transacción, después los envíos y después el
análisis"). Sale de la prueba del 18-sep —borrador S38925—, que recorrió a mano
la cadena completa: planeación → borrador en Odoo.

═══════════════════════════════════════════════════════════════════════════════
LA PROPUESTA — el navegador la calcula con estos insumos (mover la cobertura
no cuesta otra lectura; ver `frontend/components/fulfillment/proponer.ts`)
═══════════════════════════════════════════════════════════════════════════════
    venta diaria  = ventas FULL de los últimos 30 días COMPLETOS (CDMX) / 30
    objetivo      = venta diaria × días de cobertura
    necesidad     = objetivo − en FULL hoy − en camino − en borradores
    sugerido      = necesidad topada por lo libre en Odoo (TEXCO + TEXCO II),
                    repartido entre las dos cuentas cuando las dos lo piden

«En camino» sale de la MISMA lectura de la pestaña Envíos (`en_camino`):
  · salida sin validar, creada hace menos de 21 días → lo pedido;
  · salida validada con el envío sin cerrar (10 días) → lo enviado que ML
    todavía no avisa (avisos de FULL);
  · envío cerrado → nada: lo que falta ahí es lo que ML no recibió.
Una salida abierta de hace más de 21 días NO es mercancía en camino: es una
orden que nadie cerró (S26441, S25795). Se enseña aparte y no se resta.

«En borradores» son las cotizaciones FULL que ya existen en Odoo (21 días), de
la KAM o del panel: no se propone dos veces lo mismo.

═══════════════════════════════════════════════════════════════════════════════
LA ESCRITURA — APAGADA hasta que Brandon la encienda
═══════════════════════════════════════════════════════════════════════════════
Crear en Odoo es un flujo de negocio vivo (regla 3 del CLAUDE.md): vive detrás
del interruptor `fulfillment_crear_full` de `ops.automatizacion_flags` —el mismo
molde que las órdenes de TikTok/Temu— y APAGADO es el valor por omisión. Con él
apagado, «Crear» contesta la vista previa exacta de lo que se crearía.

Encendido, lo ÚNICO que escribe:
  · res.partner.create → el socio fijo de la cuenta («FULL KUBERA» / «FULL SAN
    CORPE»), una sola vez. Es lo que dice de qué cuenta es la orden: la API de
    Odoo crea como José Enrique y la regla por creador (Thalia/Cinthya) daría
    «sin cuenta» (`fulfillment_envios.asignar_cuenta` ya lo lee).
  · sale.order.create → la cotización en BORRADOR, UNA POR ALMACÉN (como las
    arman las KAM). Precio 0 y sin impuestos: es mercancía nuestra que cambia de
    bodega, no una venta.
NUNCA confirma —confirmar reserva stock y crea el picking de bodega—: eso lo
sigue haciendo la KAM en Odoo. Idempotente por `origin` (lleva la clave de la
solicitud) y a Odoo se le RELEE después de crear.

TODO ESTO BLOQUEA (XML-RPC y psycopg2): el router lo corre en un hilo (regla 11).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings
from services import fulfillment_envios as fenv
from services import odoo_ventas
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fulfillment_full")

# Cuenta legible → código con el que kubera guarda ventas y publicaciones.
CUENTAS: dict[str, str] = {"Kubera": "BEKURA", "San Corpe": "SANCORFASHION"}
# Cuenta legible → el socio fijo que la identifica en Odoo.
SOCIO: dict[str, str] = {v: k for k, v in fenv.SOCIOS_FULL.items()}

# De qué almacenes se surte FULL, en orden de preferencia. Los mismos que la
# venta de TikTok/Temu (DROP OFF fuera: no es mercancía nuestra).
ALMACENES: list[tuple[int, str]] = list(odoo_ventas._ALMACENES)

# Los supuestos de negocio que la pantalla deja mover. Ninguno tiene dueño
# todavía (18-sep): se enseñan como supuestos, no como reglas.
PARAMETROS: dict[str, int] = {
    "cobertura_dias": 30,     # cuántos días de venta debe aguantar FULL
    "min_piezas": 5,          # menos que esto no justifica un renglón
    "min_ventas_30": 3,       # con menos ventas en 30 días el ritmo es ruido
    "dejar_en_bodega": 0,     # colchón por SKU para DROP (TikTok, Temu, Walmart)
}
TRANSITO_DIAS = 21     # una salida abierta más vieja que esto es una orden olvidada
BORRADOR_DIAS = 21     # cotizaciones FULL que todavía cuentan como "ya pedido"

_CDMX = timezone(timedelta(hours=-6))
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# Crear sin avisos: ni seguidores, ni correo, ni bitácora de campos. Es el mismo
# contexto con el que se creó S38925 sin efectos secundarios.
_SIN_AVISOS = {"tracking_disable": True, "mail_create_nosubscribe": True,
               "mail_create_nolog": True, "mail_notrack": True}


def _ts(etapa: dict | None) -> datetime | None:
    return datetime.fromisoformat(etapa["ts"]) if etapa and etapa.get("ts") else None


def _dia_txt(dt: datetime) -> str:
    local = dt.astimezone(_CDMX)
    return f"{local.day} {_MESES[local.month - 1]}"


# ── El interruptor ──────────────────────────────────────────────────────────
# Mismo molde que `odoo_ventas.habilitado`: la fila de `ops.automatizacion_flags`
# manda y la variable de entorno es el valor por omisión. Caché de 30 s: apagar
# tarda a lo más medio minuto en surtir efecto.
_FLAG = "fulfillment_crear_full"
_TTL = 30.0
_cache: dict[str, Any] = {"valor": None, "ts": 0.0}


def habilitado(refrescar: bool = False) -> bool:
    """¿Puede «Crear FULL» escribir en Odoo? ⚠️ BLOQUEA al vencer el caché."""
    ahora = time.time()
    if refrescar or _cache["valor"] is None or (ahora - _cache["ts"]) > _TTL:
        fila = None
        try:
            fila = sdb.fetch_one(
                "select valor, motivo, actualizado_por, actualizado_at "
                "from ops.automatizacion_flags where flag = %(f)s", {"f": _FLAG})
        except Exception as exc:  # noqa: BLE001 — sin kubera manda la variable
            log.debug("crear FULL: interruptor no legible (%s)", exc)
        _cache.update(
            valor=bool(fila["valor"]) if fila else bool(getattr(settings, "fulfillment_crear_full", False)),
            ts=ahora, persistido=bool(fila),
            por=(fila or {}).get("actualizado_por"), motivo=(fila or {}).get("motivo"),
            cuando=(fila["actualizado_at"].isoformat() if fila and fila.get("actualizado_at") else None))
    return bool(_cache["valor"])


def estado_interruptor() -> dict[str, Any]:
    habilitado(refrescar=True)
    return {"encendido": bool(_cache["valor"]), "persistido": bool(_cache.get("persistido")),
            "actualizado_por": _cache.get("por"), "motivo": _cache.get("motivo"),
            "actualizado_at": _cache.get("cuando")}


def fijar_interruptor(encendido: bool, quien: str = "", motivo: str = "") -> dict[str, Any]:
    """Enciende o apaga la escritura en Odoo. Queda QUIÉN y POR QUÉ. Nunca lanza."""
    try:
        sdb.execute(
            """insert into ops.automatizacion_flags
                   (flag, valor, motivo, actualizado_at, actualizado_por)
               values (%(f)s, %(v)s, %(m)s, now(), %(q)s)
               on conflict (flag) do update set
                   valor = excluded.valor, motivo = excluded.motivo,
                   actualizado_at = now(), actualizado_por = excluded.actualizado_por""",
            {"f": _FLAG, "v": bool(encendido), "m": (motivo or "")[:300] or None,
             "q": (quien or "")[:120] or None})
        log.warning("Crear FULL en Odoo: %s por %s%s", "ENCENDIDO" if encendido else "APAGADO",
                    quien or "?", f" — {motivo}" if motivo else "")
        return {"ok": True, **estado_interruptor()}
    except Exception as exc:  # noqa: BLE001
        log.exception("no se pudo mover el interruptor de Crear FULL")
        return {**estado_interruptor(), "ok": False, "motivo": str(exc)[:300]}


# ── Lecturas ────────────────────────────────────────────────────────────────

# Ventas FULL por SKU y cuenta: 30 y 7 días COMPLETOS (el día de hoy va a medias
# y se excluye). La vista ya une el histórico con lo vivo.
_SQL_VENTAS = """
    select cuenta, sku::text sku,
           coalesce(sum(units_sold), 0)::int v30,
           coalesce(sum(units_sold) filter (where date >= %(d7)s), 0)::int v7,
           max(date) ultima
      from channel.sales_daily_completa
     where canal = 'mercado_libre' and is_full
       and cuenta in ('BEKURA', 'SANCORFASHION')
       and date >= %(d30)s and date < %(hoy)s
     group by 1, 2
"""

# Una publicación FULL por SKU y cuenta (medido el 24-sep: 1,041 filas = 1,041
# SKUs en Kubera; 892 = 892 en San Corpe).
_SQL_PUBLICACIONES = """
    select a.legacy_code cuenta, l.sku::text sku, l.listing_id, l.url, l.situacion,
           coalesce(l.stock_full, 0)::int stock_full
      from channel.listings l join core.accounts a on a.id = l.account_id
     where a.legacy_code in ('BEKURA', 'SANCORFASHION')
       and l.canal = 'mercado_libre' and l.is_fulfillment
"""


def _borradores(ahora: datetime) -> list[dict[str, Any]]:
    """Las cotizaciones FULL que YA existen en Odoo (21 días): de la KAM o del panel."""
    desde = (ahora - timedelta(days=BORRADOR_DIAS)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ordenes = odoo_ventas._kw(
        "sale.order", "search_read",
        [[["state", "=", "draft"], ["create_date", ">=", desde],
          "|", ["partner_id.name", "=ilike", "full%"], ["partner_id.name", "=ilike", "mercado libre"]]],
        {"fields": ["name", "create_uid", "create_date", "partner_id", "client_order_ref", "origin",
                    "warehouse_id", "order_line"],
         "order": "create_date desc", "limit": 100})
    ids = [i for o in ordenes for i in (o.get("order_line") or [])]
    lineas = ({l["id"]: l for l in odoo_ventas._kw("sale.order.line", "read",
                                                   [ids, ["product_id", "product_uom_qty"]])}
              if ids else {})
    salida = []
    for o in ordenes:
        socio = fenv._nombre(o.get("partner_id"))
        cuenta, regla = fenv.asignar_cuenta("meli", socio, fenv._id(o.get("create_uid")),
                                            fenv._nombre(o.get("create_uid")))
        renglones = []
        for lid in o.get("order_line") or []:
            ln = lineas.get(lid)
            if not ln or not ln.get("product_id"):
                continue            # secciones y notas de la cotización
            completo = fenv._nombre(ln["product_id"])
            m = fenv._RE_SKU.match(completo)
            renglones.append({"sku": m.group(1) if m else completo,
                              "cantidad": int(ln.get("product_uom_qty") or 0)})
        salida.append({
            "id": o["id"], "orden": o["name"], "cuenta": cuenta, "cuenta_regla": regla,
            "socio": socio, "kam": fenv.quien_armo(o), "creada": fenv._iso(o.get("create_date")),
            "referencia": o.get("client_order_ref") or None, "origen": o.get("origin") or None,
            "almacen": fenv._nombre(o.get("warehouse_id")) or None,
            "panel": str(o.get("origin") or "").startswith(fenv.ORIGEN_PANEL),
            "piezas": sum(r["cantidad"] for r in renglones), "skus": len(renglones),
            "lineas": renglones,
            "url": odoo_ventas.url_orden_publica().format(id=o["id"]),
        })
    return salida


# ── Funciones puras (se prueban sin Odoo ni kubera) ─────────────────────────

def en_camino(envios: list[dict[str, Any]],
              ahora: datetime) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """
    Lo que ya va hacia FULL, por (cuenta, SKU), con el porqué de cada pieza; y las
    salidas abiertas OLVIDADAS (más de 21 días), que se enseñan y no se restan.
    """
    camino: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"piezas": 0, "detalle": []})
    zombis: list[dict[str, Any]] = []
    for e in envios:
        if e.get("canal") != "meli" or e.get("cuenta") not in CUENTAS:
            continue
        cuenta = e["cuenta"]
        creada, salida = _ts(e["etapas"][0]), _ts(e["etapas"][1])
        lineas = e.get("lineas") or []
        if e.get("estado_odoo") != "done":
            if creada and creada < ahora - timedelta(days=TRANSITO_DIAS):
                zombis.append({"orden": e.get("orden"), "salida": e.get("salida"), "cuenta": cuenta,
                               "creada": creada.isoformat(), "piezas": int(e.get("pedidas") or 0)})
                continue
            for r in lineas:
                n = int(r.get("pedidas") or 0)
                if n > 0:
                    k = (cuenta, r["sku"])
                    camino[k]["piezas"] += n
                    camino[k]["detalle"].append(f"{e.get('orden')} por validar en Odoo ({n})")
            continue
        c = e.get("cobertura") or {}
        if c.get("fuente") == "avisos":
            if c.get("cerrado"):
                continue            # lo que falta en un envío cerrado es rechazo, no camino
            for r in lineas:
                falta = int(r.get("enviadas") or 0) - int(r.get("llegadas") or 0)
                if falta > 0:
                    k = (cuenta, r["sku"])
                    camino[k]["piezas"] += falta
                    camino[k]["detalle"].append(
                        f"{e.get('orden')} salió el {_dia_txt(salida)}: faltan {falta} por llegar"
                        if salida else f"{e.get('orden')}: faltan {falta} por llegar")
        elif salida and salida >= ahora - timedelta(days=TRANSITO_DIAS):
            # Validada sin avisos que la midan: se toma lo enviado entero.
            for r in lineas:
                n = int(r.get("enviadas") or 0)
                if n > 0:
                    k = (cuenta, r["sku"])
                    camino[k]["piezas"] += n
                    camino[k]["detalle"].append(f"{e.get('orden')} salió el {_dia_txt(salida)} ({n}, sin avisos)")
    return dict(camino), zombis


def armar_propuesta(ventas: list[dict], publicaciones: list[dict], envios: list[dict],
                    borradores: list[dict], productos: dict[str, dict], libres: dict[int, dict[int, float]],
                    ahora: datetime) -> dict[str, Any]:
    """Los insumos de la propuesta, por cuenta y SKU. NO decide cantidades: eso lo
    hace el navegador con los parámetros que la persona mueva."""
    camino, zombis = en_camino(envios, ahora)
    borr: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"piezas": 0, "detalle": []})
    for b in borradores:
        if b.get("cuenta") not in CUENTAS:
            continue
        for r in b["lineas"]:
            if r["cantidad"] > 0:
                k = (b["cuenta"], r["sku"])
                borr[k]["piezas"] += r["cantidad"]
                borr[k]["detalle"].append(f"{b['orden']} ({r['cantidad']})")
    pub = {(p["cuenta"], p["sku"]): p for p in publicaciones}

    cuentas: dict[str, list[dict[str, Any]]] = {}
    for legible, codigo in CUENTAS.items():
        filas = []
        for v in ventas:
            if v["cuenta"] != codigo:
                continue
            sku = v["sku"]
            p, l = productos.get(sku), pub.get((codigo, sku))
            c, b = camino.get((legible, sku)), borr.get((legible, sku))
            lib = libres.get(p["id"], {}) if p else {}
            filas.append({
                "sku": sku, "nombre": (p or {}).get("name"), "product_id": (p or {}).get("id"),
                "listing_id": l["listing_id"] if l else None, "url": l["url"] if l else None,
                "situacion": l["situacion"] if l else None,
                "v30": int(v["v30"] or 0), "v7": int(v["v7"] or 0),
                "ultima_venta": v["ultima"].isoformat() if v.get("ultima") else None,
                # null = la publicación no está en channel.listings: no se sabe
                # cuánto hay en FULL (no es un cero).
                "stock_full": int(l["stock_full"]) if l else None,
                "en_camino": c["piezas"] if c else 0, "camino": c["detalle"] if c else [],
                "borrador": b["piezas"] if b else 0, "borradores": b["detalle"] if b else [],
                # Lo libre que Odoo puede prometer en cada almacén (negativo = ya
                # comprometido de más: para planear es cero).
                "libre": ({nombre: max(0, int(lib.get(wid, 0) or 0)) for wid, nombre in ALMACENES}
                          if p else None),
            })
        filas.sort(key=lambda f: (-f["v30"], f["sku"]))
        cuentas[legible] = filas

    local = ahora.astimezone(_CDMX)
    iso = local.isocalendar()
    lunes = (local - timedelta(days=local.weekday())).date()
    return {
        "generado": ahora.isoformat(),
        "semana": {"semana": f"S{iso[1]}", "anio": iso[0], "lunes": lunes.isoformat(),
                   "domingo": (lunes + timedelta(days=6)).isoformat()},
        "parametros": dict(PARAMETROS),
        "almacenes": [n for _, n in ALMACENES],
        "cuentas": cuentas,
        "borradores": borradores,
        "zombis": zombis,
        # TODO lo que va hacia FULL por cuenta, no sólo lo de los SKUs con venta:
        # es el «saldo en camino» que abre la pantalla.
        "en_camino": {legible: {"piezas": sum(v["piezas"] for (c, _), v in camino.items() if c == legible),
                                "skus": sum(1 for (c, _) in camino if c == legible)}
                      for legible in CUENTAS},
        "fuente": ("ventas FULL de 30 días (channel.sales_daily_completa) · stock en FULL "
                   "(channel.listings) · en camino: la lectura de Envíos (Odoo + avisos de FULL) · "
                   "borradores FULL y libre por almacén: Odoo en vivo"),
    }


def repartir_almacenes(lineas: list[dict[str, Any]],
                       libres: dict[int, dict[int, float]]) -> dict[str, Any]:
    """
    De qué almacén sale cada renglón. UNA ORDEN POR ALMACÉN, como las arman las
    KAM (Cinthya el 18-sep: S38878 TEXCO + S38879 TEXCO II).

      0. Nada que no exista: cada renglón se topa a lo libre de los dos almacenes
         juntos, y el recorte SE DICE. (Una venta se registra aunque no haya; un
         envío a FULL pidiendo lo que no hay sólo le hace perder el tiempo a
         bodega.)
      1. Si un almacén SOLO cubre todo, una sola orden ahí (TEXCO primero).
      2. Si no, cada renglón va COMPLETO al primer almacén que lo cubra: no se
         parte un SKU por gusto.
      3. Sólo si ninguno lo cubre solo, se parte (TEXCO primero).
    """
    def lib(ln: dict, wid: int) -> int:
        return max(0, int(libres.get(ln["product_id"], {}).get(wid, 0) or 0))

    recortes: list[dict[str, Any]] = []
    van: list[dict[str, Any]] = []
    for ln in lineas:
        total = sum(lib(ln, wid) for wid, _ in ALMACENES)
        pide = int(ln["cantidad"])
        n = min(pide, total)
        if n < pide:
            recortes.append({"sku": ln["sku"], "pedidas": pide, "van": n,
                             "porque": ("Odoo no tiene libre" if total == 0
                                        else f"sólo hay {total} libre{'s' if total != 1 else ''} en Odoo")})
        if n > 0:
            van.append({**ln, "cantidad": n})

    for wid, nombre in ALMACENES:
        if van and all(lib(ln, wid) >= ln["cantidad"] for ln in van):
            return {"partes": [{"almacen_id": wid, "almacen": nombre, "lineas": van}], "recortes": recortes}

    por: dict[int, list[dict[str, Any]]] = {wid: [] for wid, _ in ALMACENES}
    for ln in van:
        entero = next((wid for wid, _ in ALMACENES if lib(ln, wid) >= ln["cantidad"]), None)
        if entero is not None:
            por[entero].append(ln)
            continue
        falta = ln["cantidad"]
        for wid, _ in ALMACENES:
            toma = min(lib(ln, wid), falta)
            if toma > 0:
                por[wid].append({**ln, "cantidad": toma})
                falta -= toma
    partes = [{"almacen_id": wid, "almacen": nombre, "lineas": por[wid]}
              for wid, nombre in ALMACENES if por[wid]]
    return {"partes": partes, "recortes": recortes}


def limpiar_lineas(lineas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """La solicitud tal como llega del navegador → un renglón por SKU, piezas > 0."""
    por_sku: dict[str, dict[str, Any]] = {}
    for ln in lineas or []:
        sku = str(ln.get("sku") or "").strip()
        try:
            n = int(ln.get("cantidad") or 0)
        except (TypeError, ValueError):
            n = 0
        if not sku or n <= 0:
            continue
        r = por_sku.setdefault(sku, {"sku": sku, "cantidad": 0, "sugerido": None})
        r["cantidad"] += n
        if ln.get("sugerido") is not None:
            r["sugerido"] = int(ln["sugerido"])
    return list(por_sku.values())


# ── Lo que llama el router (BLOQUEA) ────────────────────────────────────────

def leer_propuesta(envios: list[dict[str, Any]], ahora: datetime | None = None) -> dict[str, Any]:
    ahora = ahora or datetime.now(timezone.utc)
    hoy = ahora.astimezone(_CDMX).date()
    ventas = sdb.fetch_all(_SQL_VENTAS, {"d30": hoy - timedelta(days=30),
                                         "d7": hoy - timedelta(days=7), "hoy": hoy})
    publicaciones = sdb.fetch_all(_SQL_PUBLICACIONES)
    borradores = _borradores(ahora)
    skus = sorted({v["sku"] for v in ventas})
    productos = odoo_ventas.productos_por_sku(skus)
    libres = odoo_ventas.libre_por_almacen([p["id"] for p in productos.values()])
    datos = armar_propuesta(ventas, publicaciones, envios, borradores, productos, libres, ahora)
    log.info("crear FULL: propuesta con %s",
             {c: len(f) for c, f in datos["cuentas"].items()})
    return datos


def vista_previa(cuenta: str, lineas: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Lo que se crearía en Odoo, con el stock libre RELEÍDO en este momento (la
    propuesta pudo envejecer minutos). No escribe nada, con o sin interruptor.
    """
    if cuenta not in CUENTAS:
        return {"ok": False, "motivo": f"cuenta '{cuenta}' no es Kubera ni San Corpe"}
    pedidas = limpiar_lineas(lineas)
    if not pedidas:
        return {"ok": False, "motivo": "la solicitud no trae piezas"}
    productos = odoo_ventas.productos_por_sku([l["sku"] for l in pedidas])
    no_en_odoo = [l["sku"] for l in pedidas if l["sku"] not in productos]
    con_producto = [{**l, "product_id": productos[l["sku"]]["id"], "nombre": productos[l["sku"]]["name"]}
                    for l in pedidas if l["sku"] in productos]
    libres = odoo_ventas.libre_por_almacen([l["product_id"] for l in con_producto])
    plan = repartir_almacenes(con_producto, libres)
    partes = [{"almacen_id": p["almacen_id"], "almacen": p["almacen"],
               "piezas": sum(l["cantidad"] for l in p["lineas"]),
               "lineas": [{"sku": l["sku"], "nombre": l["nombre"], "cantidad": l["cantidad"],
                           "product_id": l["product_id"]} for l in p["lineas"]]}
              for p in plan["partes"]]
    return {"ok": bool(partes), "cuenta": cuenta, "socio": SOCIO[cuenta],
            "motivo": None if partes else "nada de lo pedido tiene stock libre en Odoo",
            "partes": partes, "recortes": plan["recortes"], "no_en_odoo": no_en_odoo,
            "piezas_pedidas": sum(l["cantidad"] for l in pedidas),
            "piezas": sum(p["piezas"] for p in partes),
            "generado": datetime.now(timezone.utc).isoformat()}


_candado = threading.Lock()


def _socio(cuenta: str) -> int:
    """El id del socio fijo de la cuenta; lo crea la primera vez."""
    nombre = SOCIO[cuenta]
    filas = odoo_ventas._kw("res.partner", "search_read", [[["name", "=ilike", nombre]]],
                            {"fields": ["id"], "limit": 1, "order": "id asc"})
    if filas:
        return filas[0]["id"]
    pid = odoo_ventas._kw(
        "res.partner", "create",
        [{"name": nombre, "type": "contact",
          "comment": (f"<p>Socio fijo de FULL de Mercado Libre, cuenta {cuenta}. Lo usa «Crear FULL» del "
                      "panel Omnicanal para que la orden diga de qué cuenta es (no quién la capturó).</p>")}],
        {"context": _SIN_AVISOS})
    log.warning("crear FULL: socio «%s» creado en Odoo (id %s)", nombre, pid)
    return pid


def _guardar_solicitud(clave: str, cuenta: str, quien: str, parametros: dict[str, Any],
                       pedidas: list[dict[str, Any]], previa: dict[str, Any],
                       ordenes: list[dict[str, Any]]) -> bool:
    """
    La solicitud ORIGINAL (lo sugerido y lo que la persona pidió) antes de que
    bodega recorte: es lo único que hace posible la etapa «Solicitado» del rail y
    la tasa de validado. Necesita la migración 0054; sin ella NO se detiene la
    creación (la orden de Odoo es lo que importa), se avisa.
    """
    van: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in previa["partes"]:
        for l in p["lineas"]:
            van[l["sku"]].append({"almacen": p["almacen"], "cantidad": l["cantidad"]})
    renglones = [{"sku": l["sku"], "sugerido": l.get("sugerido"), "solicitado": l["cantidad"],
                  "van": sum(x["cantidad"] for x in van.get(l["sku"], [])), "almacenes": van.get(l["sku"], [])}
                 for l in pedidas]
    try:
        sdb.execute(
            """insert into ops.fulfillment_solicitudes
                   (clave, cuenta, quien, parametros, lineas, recortes, ordenes)
               values (%(clave)s, %(cuenta)s, %(quien)s, %(par)s::jsonb, %(lin)s::jsonb,
                       %(rec)s::jsonb, %(ord)s::jsonb)
               on conflict (clave) do nothing""",
            {"clave": clave, "cuenta": cuenta, "quien": (quien or "")[:120] or None,
             "par": json.dumps(parametros or {}), "lin": json.dumps(renglones),
             "rec": json.dumps(previa.get("recortes") or []), "ord": json.dumps(ordenes)})
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("crear FULL: la solicitud %s no se guardó (¿falta la migración 0054?): %s", clave, exc)
        return False


def crear(cuenta: str, lineas: list[dict[str, Any]], quien: str, clave: str,
          parametros: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Crea en Odoo la cotización FULL en BORRADOR, una por almacén. Con el
    interruptor apagado contesta la vista previa (`accion: "apagado"`) y no
    escribe. Idempotente: la misma `clave` nunca crea dos veces.
    """
    clave = re.sub(r"[^0-9A-Za-z]", "", clave or "")[:12]
    if len(clave) < 8:
        return {"ok": False, "accion": "sin_clave", "motivo": "falta la clave de la solicitud"}
    previa = vista_previa(cuenta, lineas)
    if not previa.get("ok"):
        return {**previa, "accion": "nada_que_crear"}
    if not habilitado(refrescar=True):
        return {**previa, "ok": True, "accion": "apagado",
                "motivo": ("La creación en Odoo está APAGADA: esto es exactamente lo que se crearía. "
                           "Se enciende con el interruptor de Crear FULL (sólo admin).")}

    pedidas = limpiar_lineas(lineas)
    corto = (quien or "panel").split("@")[0][:40] or "panel"
    with _candado:
        # Idempotencia DENTRO del candado: un doble clic entra aquí dos veces y
        # la segunda encuentra lo que creó la primera.
        existentes = odoo_ventas._kw("sale.order", "search_read",
                                     [[["origin", "ilike", f"· {clave} ·"]]],
                                     {"fields": ["name", "state", "warehouse_id", "picking_ids"]})
        por_almacen = {fenv._id(o.get("warehouse_id")): o for o in existentes}
        socio_id = _socio(cuenta)
        hoy = datetime.now(_CDMX)
        creadas: list[dict[str, Any]] = []
        for parte in previa["partes"]:
            ya = por_almacen.get(parte["almacen_id"])
            if ya:
                creadas.append({"id": ya["id"], "orden": ya["name"], "estado": ya["state"],
                                "almacen": parte["almacen"], "piezas": parte["piezas"],
                                "ya_existia": True})
                continue
            payload = {
                "partner_id": socio_id,
                "warehouse_id": parte["almacen_id"],
                "origin": f"{fenv.ORIGEN_PANEL} · FULL {cuenta} · {corto} · {clave} · {parte['almacen']}",
                "note": (f"<p>FULL de Mercado Libre, cuenta <b>{cuenta}</b>. Creada en BORRADOR desde la "
                         f"pestaña FULLFILMENT del panel Omnicanal por {corto} el "
                         f"{hoy.day} {_MESES[hoy.month - 1]} {hoy.year}. La confirma la KAM en Odoo. "
                         f"Falta el número del envío de ML: se teclea en la referencia del cliente.</p>"),
                "order_line": [(0, 0, {"product_id": l["product_id"], "product_uom_qty": l["cantidad"],
                                       "price_unit": 0.0, "tax_id": [(6, 0, [])],
                                       "name": f"[{l['sku']}] {l['nombre'] or ''}".strip()[:400]})
                               for l in parte["lineas"]],
            }
            oid = odoo_ventas._kw("sale.order", "create", [payload], {"context": _SIN_AVISOS})
            # A Odoo se le RELEE: no se da por hecho nada de lo que se pidió.
            o = odoo_ventas._kw("sale.order", "read",
                                [[oid], ["name", "state", "picking_ids", "order_line"]])[0]
            creadas.append({"id": oid, "orden": o["name"], "estado": o["state"],
                            "almacen": parte["almacen"], "piezas": parte["piezas"],
                            "renglones": len(o.get("order_line") or []),
                            "con_picking": bool(o.get("picking_ids")), "ya_existia": False})
            log.warning("crear FULL: %s creada en BORRADOR (%s, %s, %s pzs) por %s",
                        o["name"], cuenta, parte["almacen"], parte["piezas"], corto)

    for c in creadas:
        c["url"] = odoo_ventas.url_orden_publica().format(id=c["id"])
    raras = [c for c in creadas if not c["ya_existia"] and (c["estado"] != "draft" or c.get("con_picking"))]
    guardada = _guardar_solicitud(clave, cuenta, quien, parametros or {}, pedidas, previa,
                                  [{"id": c["id"], "orden": c["orden"], "almacen": c["almacen"]} for c in creadas])
    accion = "ya_existia" if all(c["ya_existia"] for c in creadas) else "creada"
    return {**previa, "ok": not raras, "accion": accion, "ordenes": creadas,
            "solicitud_guardada": guardada,
            "motivo": (f"Odoo dejó {', '.join(c['orden'] for c in raras)} fuera de borrador: revisar a mano."
                       if raras else None)}
