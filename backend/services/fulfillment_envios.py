"""
fulfillment_envios.py — Las salidas de Odoo a los almacenes de los marketplaces
(ML FULL, Amazon FBA, Walmart WFS), clasificadas por canal y cuenta.

LECTURA PURA de Odoo por XML-RPC (`search_read`/`read`). No escribe nada en
ninguna parte. Alimenta la pestaña FULLFILMENT (`/api/fulfillment/envios`).

═══════════════════════════════════════════════════════════════════════════════
LAS REGLAS — medidas, no supuestas (sondeo del 14-sep-2026)
═══════════════════════════════════════════════════════════════════════════════
Evidencia completa, orden por orden: docs/FULLFILMENT_SONDEO_DESTINOS.md y
docs/FULLFILMENT_EVIDENCIA_ORDENES.md.

1. En Odoo NO hay ubicación, tipo de operación, almacén ni campo que diga canal
   o cuenta: todas las salidas terminan en `Customers`. Lo único que distingue
   es el NOMBRE del socio, quién creó la orden de venta y la referencia.
   Y el socio es un contacto NUEVO por orden (219 "FULL" distintos): se filtra
   por `partner_id.name`, jamás por `partner_id`.

2. Canal, por el nombre del socio (mayúsculas, espacios colapsados):
     FULL…            → ML FULL
     MERCADO LIBRE    → ML FULL (exacto; 2 órdenes de Nancy, llegaron a BEKURA)
     AMAZON… ≥40 pzs  → Amazon FBA. Con 1–7 piezas son VENTAS MFN capturadas a
                        mano, no envíos: se excluyen.
     WFS…             → Walmart WFS. Los "WALMART #…" son ventas S2H: fuera.
   Lo que filtraba `odoo._causa` por subcadena marcaba 1,065 salidas como envío
   a fulfillment; lo eran 229.

3. Cuenta de ML = quién CREÓ LA ORDEN DE VENTA (el picking lo crea OdooBot):
     Thalia (152)  → SANCORFASHION · San Corpe
     Cinthya (153) → BEKURA · Kubera
   Probado contra la API de ML: 27 de 27 números de envío de Thalia llegaron a
   SANCORFASHION y 21 de 21 de Cinthya a BEKURA, cero cruzados. Cualquier otro
   creador → cuenta None ("sin asignar"): NO se adivina. La regla depende de
   personas; se rompe el día que una KAM cubra a la otra, y por eso cada envío
   lleva `cuenta_regla` diciendo de dónde salió.

4. `date_done` de la salida NO es la hora del camión: en 16 de 43 envíos
   verificados ML ya había recibido piezas ANTES de que bodega validara. Se
   rotula "salida validada en Odoo", nunca "salió" ni "en tránsito".

5. Nada de lo que no se mide se inventa: solicitadas, validadas por Bodega,
   recibidas, activación y primera venta van en None hasta que tengan fuente.

6. Los movimientos CANCELADOS de una salida ya validada SÍ cuentan como pedidos:
   es lo que bodega no tuvo (Odoo cancela el renglón al validar sin pendiente y
   la orden queda con Entregado = 0). En una salida abierta, un cancelado es un
   renglón que la KAM quitó y no se pide.
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings
from services import fulfillment_etapas, odoo

log = logging.getLogger("omnicanal.fulfillment_envios")

# Usuarios de Odoo → cuenta de ML. Por ID, no por nombre: el nombre se edita.
_KAM_CUENTA: dict[int, tuple[str, str]] = {
    152: ("SANCORFASHION", "San Corpe"),   # Thalia
    153: ("BEKURA", "Kubera"),             # Cinthya
}

# Umbral que separa un envío a FBA de una venta MFN con el mismo socio AMAZON.
# Medido: envíos de 47 a 1,664 piezas; ventas de 1 a 7. Sin traslape.
UMBRAL_FBA_PIEZAS = 40

_CDMX = timezone(timedelta(hours=-6))   # México no cambia de horario desde 2022

_RE_ML = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_RE_WFS = re.compile(r"(\d{7}GDM)", re.I)
_RE_FBA = re.compile(r"(FBA[0-9A-Z]{8,10})", re.I)
_RE_SKU = re.compile(r"^\[([^\]]+)\]\s*(.*)$")


def _norm(texto: str | None) -> str:
    return re.sub(r"\s+", " ", (texto or "").strip()).upper()


def _nombre(campo: Any) -> str:
    return campo[1] if isinstance(campo, (list, tuple)) and len(campo) > 1 else ""


def _id(campo: Any) -> int | None:
    return campo[0] if isinstance(campo, (list, tuple)) and campo else None


def _iso(odoo_utc: str | bool | None) -> str | None:
    """Odoo entrega UTC SIN zona. Se le pone la zona; el panel lo pinta en CDMX."""
    if not odoo_utc:
        return None
    return datetime.strptime(str(odoo_utc)[:19], "%Y-%m-%d %H:%M:%S") \
        .replace(tzinfo=timezone.utc).isoformat()


def _dia_cdmx(iso: str | None) -> int | None:
    """0 = lunes … 6 = domingo, en hora de CDMX (un martes 19 h UTC-6 es martes)."""
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone(_CDMX).weekday()


def clasificar_canal(socio: str, piezas: float) -> str | None:
    """'meli' | 'amazon' | 'walmart' | None (no es un envío a fulfillment)."""
    s = _norm(socio)
    if re.match(r"^FULL\b", s) or s == "MERCADO LIBRE":
        return "meli"
    if re.match(r"^AMAZON\b", s):
        return "amazon" if piezas >= UMBRAL_FBA_PIEZAS else None
    if re.match(r"^WFS\b", s):
        return "walmart"
    return None


def asignar_cuenta(canal: str, socio: str, creador_id: int | None,
                   creador: str) -> tuple[str | None, str]:
    """(cuenta legible | None, de dónde salió). Nunca adivina."""
    if canal == "amazon":
        return "San Corpe", "Amazon tiene una sola cuenta: San Corpe"
    if canal == "walmart":
        return None, "Walmart MX tiene una sola cuenta; no se separa"
    if _norm(socio) == "MERCADO LIBRE":
        return "Kubera", "socio «MERCADO LIBRE»: las 2 órdenes medidas llegaron a BEKURA"
    if creador_id in _KAM_CUENTA:
        _, legible = _KAM_CUENTA[creador_id]
        return legible, f"creó la orden {creador} → {legible}"
    return None, f"creó la orden {creador or 'alguien'}: sin regla de cuenta, no se asigna"


def numero_envio(canal: str, referencia: str | None, socio: str) -> tuple[str | None, str | None]:
    """(número normalizado, de dónde salió: 'referencia' | 'socio')."""
    ref = referencia or ""
    if canal == "meli":
        m = _RE_ML.search(ref)
        if m:
            return m.group(1), "referencia"
        m = _RE_ML.search(socio or "")
        return (m.group(1), "socio") if m else (None, None)
    if canal == "walmart":
        m = _RE_WFS.search(socio or "") or _RE_WFS.search(ref)
        return (m.group(1).upper(), "socio") if m else (None, None)
    if canal == "amazon":
        m = _RE_FBA.search(ref)
        return (m.group(1).upper(), "referencia") if m else (None, None)
    return None, None


def _estado(canal: str, hecha: bool, numero: str | None) -> str:
    if canal == "meli" and not numero:
        return "sinEnlazar"
    if not hecha:
        return "abierta"
    if canal == "amazon":
        return "fbaSinLectura"
    if canal == "walmart":
        return "wfsSinLectura"
    return "salio"


def _leer_odoo() -> tuple[list[dict], dict[int, dict], list[dict]]:
    """Las tres lecturas. BLOQUEANTE (XML-RPC): llamar con asyncio.to_thread."""
    uid = odoo._uid()
    if not uid:
        raise RuntimeError("Odoo no autenticó")
    m = odoo._models()

    def sr(modelo: str, dominio: list, campos: list[str], **kw: Any) -> list[dict]:
        return m.execute_kw(settings.odoo_db, uid, settings.odoo_password,
                            modelo, "search_read", [dominio], {"fields": campos, **kw})

    pickings = sr(
        "stock.picking",
        [["picking_type_id.code", "=", "outgoing"], ["state", "!=", "cancel"],
         "|", "|", "|",
         ["partner_id.name", "=ilike", "full%"],
         ["partner_id.name", "=ilike", "amazon%"],
         ["partner_id.name", "=ilike", "wfs%"],
         ["partner_id.name", "=ilike", "mercado libre"]],
        ["name", "state", "origin", "partner_id", "sale_id", "picking_type_id",
         "create_date", "date_done", "scheduled_date"],
        order="create_date desc", limit=3000,
    )
    so_ids = sorted({_id(p["sale_id"]) for p in pickings if _id(p["sale_id"])})
    ordenes = {o["id"]: o for o in m.execute_kw(
        settings.odoo_db, uid, settings.odoo_password, "sale.order", "read", [so_ids],
        {"fields": ["name", "create_uid", "user_id", "client_order_ref", "create_date", "state"]},
    )} if so_ids else {}
    # CON los movimientos cancelados. Cuando bodega valida una salida sin tener
    # un SKU, Odoo CANCELA ese renglón (demanda 10, hecho 0) y la orden de venta
    # queda con Entregado = 0. Filtrarlos escondía justo lo que Odoo no surtió:
    # en S38407 desaparecía TEC-1661-NEG-5C (10 pedidas, 0 entregadas).
    movs = sr(
        "stock.move",
        [["picking_id", "in", [p["id"] for p in pickings]]],
        ["picking_id", "product_id", "product_qty", "quantity", "state"],
    ) if pickings else []
    return pickings, ordenes, movs


def armar(pickings: list[dict], ordenes: dict[int, dict], movs: list[dict]) -> dict[str, Any]:
    """Clasifica y arma la respuesta. Función pura: se prueba sin Odoo."""
    hechas = {p["id"] for p in pickings if p["state"] == "done"}
    lineas: dict[int, dict[str, dict[str, Any]]] = {}
    for mv in movs:
        pid = _id(mv["picking_id"])
        # Un renglón cancelado en una salida ABIERTA es uno que la KAM quitó o
        # redujo: ya no se pide. En una salida VALIDADA es lo que bodega no tuvo
        # —Odoo lo cancela al validar sin pendiente— y sí se tiene que ver.
        if mv.get("state") == "cancel" and pid not in hechas:
            continue
        completo = _nombre(mv["product_id"])
        mm = _RE_SKU.match(completo)
        sku, nombre = (mm.group(1), mm.group(2)) if mm else (completo, "")
        ren = lineas.setdefault(pid, {}).setdefault(
            sku, {"sku": sku, "nombre": nombre, "pedidas": 0.0, "enviadas": 0.0})
        ren["pedidas"] += float(mv.get("product_qty") or 0)
        if mv.get("state") == "done":
            ren["enviadas"] += float(mv.get("quantity") or 0)

    envios: list[dict[str, Any]] = []
    # Lo que el filtro de Odoo trajo pero NO es envío: se cuenta, no se esconde.
    excluidas = {"venta_amazon_mfn": 0, "otro": 0}
    for p in pickings:
        socio = _nombre(p["partner_id"])
        # Los renglones en 0 (la KAM los dejó en la orden sin cantidad) al final.
        ren = sorted(lineas.get(p["id"], {}).values(),
                     key=lambda r: (r["pedidas"] <= 0 and r["enviadas"] <= 0, -r["pedidas"]))
        pedidas = sum(r["pedidas"] for r in ren)
        hecha = p["state"] == "done"
        enviadas = sum(r["enviadas"] for r in ren) if hecha else None
        canal = clasificar_canal(socio, max(pedidas, enviadas or 0))
        if canal is None:
            clave = "venta_amazon_mfn" if _norm(socio).startswith("AMAZON") else "otro"
            excluidas[clave] += 1
            continue
        o = ordenes.get(_id(p["sale_id"]), {})
        creador_id, creador = _id(o.get("create_uid")), _nombre(o.get("create_uid"))
        cuenta, regla = asignar_cuenta(canal, socio, creador_id, creador)
        numero, origen_num = numero_envio(canal, o.get("client_order_ref"), socio)
        orden_creada = _iso(o.get("create_date"))
        validada = _iso(p.get("date_done")) if hecha else None
        envios.append({
            "id": p["id"],
            "orden": o.get("name") or p.get("origin") or None,
            "salida": p["name"],
            "almacen": _nombre(p["picking_type_id"]).split(":")[0] or None,
            "estado_odoo": p["state"],
            "canal": canal,
            "cuenta": cuenta,
            "cuenta_regla": regla,
            "kam": creador or None,
            "socio": socio,
            "referencia": o.get("client_order_ref") or None,
            "envio": numero,
            "envio_origen": origen_num,
            "piezas": round(enviadas) if enviadas is not None else None,
            "pedidas": round(pedidas),
            # Odoo no registra cajas; el factor por SKU aún no se cruza aquí.
            "cajas": None,
            # Orden de venta · Salida validada · Recibido · Activo · 1ª venta.
            # Las dos primeras salen de Odoo; las otras tres las llena
            # `fulfillment_etapas` desde kubera si hay con qué.
            #
            # Aquí VIVÍAN dos etapas más, "Solicitado" y "Validado por Bodega"
            # (la lista de Andy y su recorte). Se quitaron el 17-sep-2026
            # (Brandon: "si no sirven de nada bórralos"): ningún sistema las
            # registra, así que eran dos celdas rayadas en cada renglón. El
            # concepto no se perdió — vive en Planeación semanal, que es donde
            # se capturarían. Vuelven al rail el día que se guarden de verdad.
            "etapas": [{"ts": orden_creada} if orden_creada else None,
                       {"ts": validada} if validada else None,
                       None, None, None],
            "estado": _estado(canal, hecha, numero),
            # Lo que Odoo NO surtió, sólo cuando la salida ya se validó: antes de
            # eso un 0 entregado no es un faltante, es que todavía no sale.
            "faltante_odoo": (round(sum(max(0.0, r["pedidas"] - r["enviadas"]) for r in ren))
                              if hecha else None),
            "lineas": [{"sku": r["sku"], "nombre": r["nombre"],
                        "pedidas": round(r["pedidas"]),
                        "enviadas": round(r["enviadas"]) if hecha else None,
                        "faltante_odoo": (round(max(0.0, r["pedidas"] - r["enviadas"]))
                                          if hecha else None)}
                       for r in ren],
        })

    return {"envios": envios, "resumen": resumir(envios), "excluidas": excluidas}


def resumir(envios: list[dict[str, Any]]) -> dict[str, Any]:
    """Totales por canal y cuenta, días de la semana y tiempos orden → validación."""
    grupos: dict[str, dict[str, Any]] = {}

    def grupo(clave: str) -> dict[str, Any]:
        return grupos.setdefault(clave, {
            "salidas": 0, "hechas": 0, "abiertas": 0, "piezas_enviadas": 0,
            # Lo que pedían las salidas YA hechas: contra esto se mide cuánto salió.
            "piezas_pedidas_hechas": 0, "piezas_abiertas": 0, "sin_numero": 0, "desde": None, "hasta": None,
            "dias_orden": [0] * 7, "dias_validacion": [0] * 7, "_tramos": []})

    for e in envios:
        claves = [e["canal"]]
        if e["canal"] == "meli":
            claves.append(f"meli:{e['cuenta'] or 'sin_asignar'}")
        for c in claves:
            g = grupo(c)
            g["salidas"] += 1
            creada = e["etapas"][0]["ts"] if e["etapas"][0] else None
            validada = e["etapas"][1]["ts"] if e["etapas"][1] else None
            if e["estado_odoo"] == "done":
                g["hechas"] += 1
                g["piezas_enviadas"] += e["piezas"] or 0
                g["piezas_pedidas_hechas"] += e["pedidas"] or 0
            else:
                g["abiertas"] += 1
                g["piezas_abiertas"] += e["pedidas"] or 0
            if not e["envio"]:
                g["sin_numero"] += 1
            if creada:
                g["desde"] = min(g["desde"] or creada, creada)
                g["hasta"] = max(g["hasta"] or creada, creada)
                g["dias_orden"][_dia_cdmx(creada)] += 1
            if validada:
                g["dias_validacion"][_dia_cdmx(validada)] += 1
            if creada and validada:
                d = (datetime.fromisoformat(validada) - datetime.fromisoformat(creada))
                g["_tramos"].append(d.total_seconds() / 86400)

    for g in grupos.values():
        t = sorted(g.pop("_tramos"))
        g["orden_a_validacion_dias"] = {
            "n": len(t),
            "mediana": round(statistics.median(t), 1) if t else None,
            "p90": round(t[min(len(t) - 1, int(len(t) * 0.9))], 1) if t else None,
        }
    return grupos


def leer() -> dict[str, Any]:
    """BLOQUEANTE. El router la corre en un hilo."""
    pickings, ordenes, movs = _leer_odoo()
    datos = armar(pickings, ordenes, movs)
    # Las tres etapas que kubera sí puede llenar (recibido observado, activo y
    # 1ª venta). Si kubera no contesta, quedan en `null` y la pestaña lo dice.
    datos["etapas_kubera"] = fulfillment_etapas.enriquecer(datos["envios"])
    datos["generado"] = datetime.now(timezone.utc).isoformat()
    datos["fuente"] = ("Odoo: stock.picking de salida (socio FULL/AMAZON/WFS/MERCADO LIBRE) "
                       "+ sale.order (quién la creó y su referencia) + stock.move (con los "
                       "cancelados de una salida validada: lo que Odoo no surtió); llegada a FULL: "
                       "avisos fbm_stock_operations de ML resueltos por SKU (ops.fanout_log); "
                       "1ª venta: ventas FULL en kubera")
    log.info("fulfillment_envios: %d salidas (%s)", len(datos["envios"]),
             {k: v["salidas"] for k, v in datos["resumen"].items()})
    return datos
