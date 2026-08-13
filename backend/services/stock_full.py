"""
stock_full.py — Sincroniza los movimientos de las bodegas FULL / FBA con Woo.

EL HUECO QUE CIERRA (incidente 2026-07-27): cuando se manda mercancía a FULL,
esas piezas SALEN del almacén propio. Odoo lo registra (a mano) pero **Woo no**,
así que Woo seguía ofreciéndolas en los canales DROP → sobreventa. Se detectaron
26 SKUs con 1,526 piezas exactamente en ese estado.

CÓMO SE DETECTA AHORA (sin sondear):
Mercado Libre YA nos manda el aviso por webhook y lo estábamos ignorando:

    [fbm_stock_operations] /stock/fulfillment/operations/3963089046712909973
    [stock-locations]      /user-products/MLMU3866432728/stock

Resolviendo esa operación se sabe QUÉ pasó, CUÁNTAS piezas y el total resultante:

    {"type": "TRANSFER_DELIVERY", "detail": {"available_quantity": 5}, ...}

TABLA DE DECISIÓN (qué toca Woo y qué no):

  TRANSFER_DELIVERY      (+) llegó mercancía a FULL  → RESTA de Woo (salió de bodega)
  TRANSFER_RESERVATION   (−) reserva del envío       → NO toca (el real es DELIVERY)
  WITHDRAWAL_RESERVATION (−) el vendedor PIDE retirar→ NO toca (ni siquiera salió)
  WITHDRAWAL_DELIVERY    (−) ML entrega el retiro    → NO toca: va EN TRÁNSITO;
                             cuando llegue lo captura Odoo (el restock SIEMPRE
                             entra por Odoo) y sumarlo aquí lo contaría DOS VECES
  SALE_CONFIRMATION      (−) venta desde FULL        → NO toca (sale del almacén de ML)
  SALE_CANCELATION       (+) cancelación             → NO toca (regresa al de ML)
  QUARANTINE_*           (±) cuarentena interna      → NO toca
  ADJUSTMENT             (±) ajuste de ML            → NO toca, pero se AVISA

INCIDENTE 2026-07-27 (30 min): la v0.19.0 trataba WITHDRAWAL_DELIVERY como
"regresó a bodega → SUMA", y ML entrega los retiros DE A UNO: TEC-1804-BLN subió
de 0 a 14 en 8 minutos (19 pzas fantasma en 3 SKUs, revertidas). Nada salió a los
marketplaces: el fan-out omitió esos SKUs por no estar BUYABLE.

MODO SOLO-REGISTRO (`FULL_WATCH_SOLO_REGISTRO`, default True): clasifica y anota
en el Dashboard lo que HARÍA, sin escribir en Woo. Nació de ese incidente — los
tipos de operación se descubrieron por muestreo y el muestreo NO vio
TRANSFER_RESERVATION ni WITHDRAWAL_RESERVATION (aparecieron al encender). Antes
de mover inventario hay que ver el catálogo COMPLETO con tráfico real.

DEFENSAS CONTRA FALSOS POSITIVOS (el riesgo real de automatizar esto):
  1. IDEMPOTENCIA por `operation_id`: ML manda ráfagas del mismo evento (se vio
     el mismo shipment 3 veces en 2 s). Una operación ya aplicada se ignora.
  2. Solo los tipos de la tabla de arriba mueven stock; lo demás se registra.
  3. VERIFICACIÓN CRUZADA: antes de restar se confirma contra
     `/user-products/{id}/stock` que la bodega de ML efectivamente tiene esas
     piezas. Si no cuadra, no se toca Woo y queda el aviso.
  4. TOPE DE CORDURA: nunca deja Woo en negativo ni aplica cantidades absurdas
     (mayores al stock disponible) — se registra para revisión humana.
  5. Todo queda en la bitácora `fanout_log` (la que ya pinta el Dashboard).

Flag: FULL_WATCH_ENABLED (default False). Nace APAGADO: encenderlo mueve
inventario real (regla 3 de CLAUDE.md).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.stock_full")

# Qué hace cada tipo de operación con el stock de NUESTRA bodega (Woo).
#   "resta"  → las piezas salieron de nuestro almacén hacia la bodega del canal
#   "suma"   → las piezas regresaron a nuestro almacén
#   None     → movimiento interno del marketplace: Woo no se entera
EFECTO_EN_WOO: dict[str, str | None] = {
    # ÚNICO tipo que mete mercancía NUEVA a la bodega de ML (auditoría 27-jul:
    # verificado en producción, 129 pzas en SANCORFASHION y 17 de CUNA-0018 con
    # su `inbound_id`). Esas piezas SÍ salieron del almacén propio → resta.
    "INBOUND_RECEPTION":     "resta",
    # TRANSFER_* NO es "llegó mercancía": es un barajeo INTERNO de ML entre sus
    # propios buckets (RESERVATION saca de `available` al bucket `transfer`,
    # DELIVERY las regresa). Medido: 102 TRANSFER_DELIVERY en 60 inventarios y el
    # `total` de la bodega NO cambió ni una sola vez. Mapearlo a "resta" habría
    # desinflado Woo de forma monótona (−329 pzas solo en la muestra).
    "TRANSFER_DELIVERY":     None,
    # RETIROS: NO suman a Woo. La mercancía sale de la bodega de ML pero va EN
    # TRÁNSITO hacia el almacén; todavía no está disponible. Cuando llegue, el
    # almacén la captura en Odoo (el restock SIEMPRE entra por Odoo) y de ahí
    # baja a Woo — sumarla aquí la contaría DOS VECES.
    # Error real: el 27-jul se sumaron 10 piezas fantasma antes de detectarlo
    # (TEC-1804-BLN pasó de 0 a 8 en 3 minutos por 8 avisos de x1).
    "WITHDRAWAL_DELIVERY":   None,
    "WITHDRAWAL_RESERVATION": None,   # solo RESERVA el retiro, ni siquiera salió
    "TRANSFER_RESERVATION":  None,    # reserva de envío a FULL; el real es DELIVERY
    "SALE_CONFIRMATION":     None,
    "SALE_CANCELATION":      None,
    "QUARANTINE_RESERVATION": None,
    "QUARANTINE_RESTOCK":    None,
    "ADJUSTMENT":            None,   # se avisa: puede esconder un envío no declarado
}

# Tipos que MERECEN aviso aunque no toquen Woo: son movimientos reales de
# mercancía que el almacén debe conciliar (el retiro llegará por Odoo).
AVISAR_SIN_TOCAR = {"WITHDRAWAL_DELIVERY", "WITHDRAWAL_RESERVATION", "ADJUSTMENT"}


def habilitado() -> bool:
    return bool(getattr(settings, "full_watch_enabled", False))


def solo_registro() -> bool:
    """
    True = clasifica y ANOTA lo que haría, sin tocar Woo.

    Es el modo de observación: deja ver el catálogo real de tipos de operación y
    validar la tabla de decisión con tráfico de producción antes de mover una
    sola pieza. Default True a propósito — encender el vigilante nunca debe
    escribir por accidente.
    """
    return bool(getattr(settings, "full_watch_solo_registro", True))


# ── Bitácora / idempotencia (reutiliza fanout_log: NO se crea tabla nueva) ────

# Acciones que significan "el movimiento YA SE APLICÓ a Woo". Solo éstas sellan
# la operación: si se registró un fallo (full_sospechoso, full_sin_sku, error de
# Woo), la operación debe poder REINTENTARSE cuando ML la vuelva a avisar.
# Antes bastaba cualquier fila `full_%`, así que un 502 del WAF de Hostinger
# (pendiente #1) sellaba el movimiento y se perdía para siempre (auditoría 27-jul).
_ACCIONES_APLICADAS = ("full_ingreso", "full_retiro", "fba_ingreso")


def _ya_procesada(operacion_id: str) -> bool:
    from services import db, fanout_stock
    fanout_stock._asegurar_schema()
    ph = ",".join(["%s"] * len(_ACCIONES_APLICADAS))
    try:
        r = db.fetch_one(
            f"""SELECT id FROM fanout_log
                WHERE item_id=%s AND accion IN ({ph})
                  AND (resultado IS NULL OR resultado NOT LIKE 'ERROR%%') LIMIT 1""",
            (str(operacion_id)[:64], *_ACCIONES_APLICADAS))
        return bool(r)
    except Exception as exc:  # noqa: BLE001
        log.warning("stock_full idempotencia: %s", exc)
        return False


def _registrar(sku: str, operacion_id: str, cuenta: str, accion: str,
               antes: int | None, despues: int | None, resultado: str) -> None:
    from services import db, fanout_stock
    fanout_stock._asegurar_schema()
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                    item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (datetime.now(timezone.utc).replace(tzinfo=None), sku or "?",
                 "movimiento FULL/FBA", 0, antes, despues, "mercado_libre",
                 cuenta, str(operacion_id)[:64], accion, antes,
                 resultado[:255], 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("stock_full bitácora: %s", exc)


# ── Escritura en Woo ─────────────────────────────────────────────────────────

async def _ajustar_woo(sku: str, delta: int) -> tuple[bool, str, int | None, int | None]:
    """
    Suma/resta `delta` al stock de Woo del SKU. Nunca deja negativo.
    Devuelve (ok, detalle, antes, despues).
    """
    from services import woocommerce, wp_db
    P = wp_db._prefix()
    filas = wp_db._fetch_all(
        f"""SELECT p.ID wc_id, p.post_type, p.post_parent, st.meta_value stock,
                   ms.meta_value gest
            FROM {P}postmeta s
            JOIN {P}posts p ON p.ID = s.post_id AND p.post_status <> 'trash'
            LEFT JOIN {P}postmeta st ON st.post_id = p.ID AND st.meta_key = '_stock'
            LEFT JOIN {P}postmeta ms ON ms.post_id = p.ID AND ms.meta_key = '_manage_stock'
            WHERE s.meta_key = '_sku' AND s.meta_value = %s LIMIT 1""", (sku,))
    if not filas:
        return False, "SKU no existe en WooCommerce", None, None
    f = filas[0]
    if (f.get("gest") or "").lower() != "yes":
        return False, "Woo no gestiona stock de este SKU", None, None
    try:
        antes = int(float(f["stock"])) if f["stock"] not in (None, "") else 0
    except (TypeError, ValueError):
        antes = 0
    despues = max(0, antes + delta)          # TOPE: nunca negativo
    if despues == antes:
        return False, f"sin cambio (Woo ya está en {antes})", antes, antes
    async with woocommerce._client() as cli:
        if f["post_type"] == "product_variation":
            r = await cli.put(f"/products/{f['post_parent']}/variations/{f['wc_id']}",
                              json={"stock_quantity": despues}, timeout=60.0)
        else:
            r = await cli.put(f"/products/{f['wc_id']}",
                              json={"stock_quantity": despues}, timeout=60.0)
    if r.status_code not in (200, 201):
        return False, f"WooCommerce HTTP {r.status_code}", antes, None
    return True, "ok", antes, despues


# ── Núcleo: procesar una operación de FULL avisada por webhook ───────────────

async def procesar_operacion(operacion_id: str, cuenta: str) -> dict[str, Any]:
    """
    Resuelve la operación en ML y aplica su efecto al stock de Woo.

    Se invoca desde el webhook `fbm_stock_operations`. Idempotente por
    `operacion_id`: las ráfagas de ML no aplican el movimiento dos veces.
    """
    import httpx

    from services import fanout_stock, meli
    if not habilitado():
        return {"ok": False, "motivo": "FULL_WATCH_ENABLED apagado"}
    if _ya_procesada(operacion_id):
        return {"ok": True, "accion": "duplicado", "motivo": "operación ya aplicada"}

    token = meli._access_token(cuenta)
    if not token:
        return {"ok": False, "motivo": f"sin token de {cuenta}"}
    try:
        r = httpx.get(f"https://api.mercadolibre.com/stock/fulfillment/operations/{operacion_id}",
                      headers={"Authorization": f"Bearer {token}"}, timeout=25.0)
        if r.status_code != 200:
            return {"ok": False, "motivo": f"operación ilegible: HTTP {r.status_code}"}
        op = r.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"{type(exc).__name__}: {exc}"}

    tipo = str(op.get("type") or "").upper()
    cantidad = int((op.get("detail") or {}).get("available_quantity") or 0)
    inventory_id = op.get("inventory_id")
    efecto = EFECTO_EN_WOO.get(tipo)

    # SKU del inventario afectado
    sku = None
    try:
        rs = httpx.get(f"https://api.mercadolibre.com/inventories/{inventory_id}/stock/fulfillment",
                       headers={"Authorization": f"Bearer {token}"}, timeout=25.0)
        if rs.status_code == 200:
            refs = (rs.json().get("external_references") or [])
            item = next((x.get("id") for x in refs if x.get("type") == "item"), None)
            if item:
                ri = httpx.get(f"https://api.mercadolibre.com/items/{item}",
                               headers={"Authorization": f"Bearer {token}"}, timeout=25.0)
                if ri.status_code == 200:
                    d = ri.json()
                    sku = next((a.get("value_name") for a in d.get("attributes", [])
                                if a.get("id") == "SELLER_SKU"), None) or d.get("seller_custom_field")
    except Exception as exc:  # noqa: BLE001
        log.warning("stock_full: no se pudo resolver el SKU de %s: %s", inventory_id, exc)

    if not sku:
        _registrar("?", operacion_id, cuenta, "full_sin_sku", None, None,
                   f"{tipo} x{cantidad}: no se pudo resolver el SKU")
        return {"ok": False, "motivo": "SKU no resuelto"}

    # Movimiento interno del marketplace: se registra y no se toca Woo.
    if efecto is None:
        if tipo in AVISAR_SIN_TOCAR:
            aviso = ("RETIRO en tránsito: llegará por Odoo, NO se suma aquí"
                     if tipo.startswith("WITHDRAWAL")
                     else "AVISO: revisar (puede esconder un envío)")
            accion_reg = "full_aviso"
        else:
            aviso = "sin efecto en Woo"
            accion_reg = "full_ignorado"
        _registrar(sku, operacion_id, cuenta, accion_reg, None, None,
                   f"{tipo} x{cantidad}: {aviso}")
        return {"ok": True, "accion": accion_reg, "tipo": tipo, "sku": sku}

    # VERIFICACIÓN CRUZADA — reescrita tras la auditoría del 27-jul.
    # La versión vieja comparaba un NIVEL de stock (40-120 pzas) contra un DELTA
    # (1-6): tautología que no bloqueó ninguna de 55 operaciones simuladas, y
    # encima hacía un GET extra que devolvía el total ACTUAL, no el del momento.
    # Ahora se usa el `result` que la PROPIA operación ya trae (foto exacta del
    # instante) y se exige que el movimiento sea COHERENTE: un ingreso tiene que
    # haber dejado el total al menos tan grande como las piezas que entraron.
    resultado_op = op.get("result") or {}
    total_tras = resultado_op.get("total")
    if efecto == "resta":
        if total_tras is None:
            _registrar(sku, operacion_id, cuenta, "full_sospechoso", None, None,
                       f"{tipo} x{cantidad}: la operación no trae `result.total` — NO se tocó Woo")
            return {"ok": False, "accion": "sospechoso", "sku": sku}
        if int(total_tras) < abs(cantidad):
            _registrar(sku, operacion_id, cuenta, "full_sospechoso", None, None,
                       f"{tipo} x{cantidad}: incoherente (total tras la operación = "
                       f"{total_tras}) — NO se tocó Woo")
            return {"ok": False, "accion": "sospechoso", "sku": sku}

    delta = -abs(cantidad) if efecto == "resta" else abs(cantidad)
    accion = "full_ingreso" if efecto == "resta" else "full_retiro"

    # MODO OBSERVACIÓN: se anota lo que haría y se termina. Nada toca Woo.
    if solo_registro():
        actual = fanout_stock._stock_drop(sku)
        propuesto = max(0, (actual or 0) + delta) if actual is not None else None
        _registrar(sku, operacion_id, cuenta, f"{accion}_sim", actual, propuesto,
                   f"{tipo} x{cantidad}: SOLO-REGISTRO — restaría a Woo "
                   f"{actual}→{propuesto} (no se escribió)")
        log.info("FULL [solo-registro] %s %s: %s x%s → Woo %s→%s",
                 cuenta, sku, tipo, cantidad, actual, propuesto)
        return {"ok": True, "accion": f"{accion}_sim", "sku": sku, "tipo": tipo,
                "cantidad": cantidad, "antes": actual, "despues": propuesto,
                "solo_registro": True}

    ok, det, antes, despues = await _ajustar_woo(sku, delta)
    _registrar(sku, operacion_id, cuenta, accion, antes, despues,
               f"{tipo} x{cantidad} → Woo {antes}→{despues} ({det})" if ok
               else f"{tipo} x{cantidad}: {det}")
    if ok:
        log.info("FULL %s %s: %s x%s → Woo %s→%s", cuenta, sku, tipo, cantidad, antes, despues)
        # El stock propio cambió: los canales DROP tienen que enterarse.
        fanout_stock.encolar(sku, motivo=f"movimiento FULL {tipo} {cuenta}")
    return {"ok": ok, "accion": accion, "sku": sku, "tipo": tipo,
            "cantidad": cantidad, "antes": antes, "despues": despues, "detalle": det}


# ── Amazon FBA: sin webhook disponible → comparación de fotos ────────────────

async def revisar_fba() -> dict[str, Any]:
    """
    Detecta ingresos a FBA comparando el inventario actual contra la última foto
    guardada en `canal_inventario.stock_fba`.

    Amazon SÍ tiene notificaciones de inventario (FBA_INVENTORY_AVAILABILITY_
    CHANGES) pero hoy devuelven 403: requieren un rol extra en la app de SP-API
    y una cola SQS/EventBridge. Mientras tanto, la foto basta: son pocos SKUs en
    FBA (7 al 2026-07-27), así que consultar es barato.
    """
    import httpx

    from services import channel_read, db, fanout_stock
    if not habilitado():
        return {"ok": False, "motivo": "FULL_WATCH_ENABLED apagado"}
    token = fanout_stock._token_amazon()
    if not token:
        return {"ok": False, "motivo": "sin token de Amazon"}

    # FOTO PROPIA, no la de canal_inventario (auditoría 27-jul): esa columna la
    # refresca el sync cada 15 min, así que tras descontar un ingreso el sync la
    # actualizaba y en la vuelta siguiente el MISMO ingreso volvía a verse como
    # nuevo → descuento repetido. La referencia es lo que ESTE vigilante vio la
    # última vez, guardado en su propia bitácora (`fanout_log`, sin tabla nueva).
    previos: dict[str, int] = {}
    try:
        for r in db.fetch_all(
            """SELECT f.sku, f.resultado FROM fanout_log f
               JOIN (SELECT sku, MAX(id) mx FROM fanout_log
                     WHERE accion LIKE 'fba_%%' GROUP BY sku) u ON u.mx = f.id"""):
            # el resultado guarda "FBA subió A→B (+N)…": la referencia es B
            import re as _re
            m = _re.search(r"→\s*(\d+)", str(r["resultado"] or ""))
            if m:
                previos[r["sku"]] = int(m.group(1))
    except Exception as exc:  # noqa: BLE001
        log.warning("stock_full: sin foto previa de FBA (%s)", exc)
    # Semilla para los SKUs que este vigilante nunca ha visto: el valor del sync.
    # PASO 0 (12-ago-2026): la semilla sale de kubera. Con `canal_inventario`
    # congelada, un SKU visto por primera vez se compararía contra su foto del
    # 11-ago: no un error, una ALERTA FANTASMA de un movimiento que no pasó.
    if settings.supabase_read_channel:
        for sku, fba in channel_read.stock_fba_amazon().items():
            previos.setdefault(sku, fba)
    else:
        for r in db.fetch_all("SELECT sku, stock_fba FROM canal_inventario WHERE canal='amazon'"):
            previos.setdefault(r["sku"], int(r["stock_fba"] or 0))
    actuales: dict[str, int] = {}
    token_pag = None
    try:
        with httpx.Client(timeout=30.0) as cli:
            while True:
                params = {"granularityType": "Marketplace",
                          "granularityId": settings.amazon_marketplace_id,
                          "marketplaceIds": settings.amazon_marketplace_id,
                          "details": "false"}
                if token_pag:
                    params["nextToken"] = token_pag
                r = cli.get(f"{settings.amazon_sp_api_endpoint}/fba/inventory/v1/summaries",
                            params=params, headers={"x-amz-access-token": token})
                if r.status_code != 200:
                    return {"ok": False, "motivo": f"HTTP {r.status_code}"}
                d = r.json().get("payload") or r.json()
                for s in (d.get("inventorySummaries") or []):
                    sku = s.get("sellerSku")
                    if sku:
                        actuales[sku] = int((s.get("totalQuantity") or 0))
                token_pag = ((r.json().get("pagination") or {}).get("nextToken"))
                if not token_pag:
                    break
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"{type(exc).__name__}: {exc}"}

    aplicados = []
    for sku, ahora in actuales.items():
        antes_fba = previos.get(sku)
        if antes_fba is None or ahora <= antes_fba:
            continue                      # sin ingreso (o SKU nuevo: se toma como base)
        subio = ahora - antes_fba
        if solo_registro():
            actual = fanout_stock._stock_drop(sku)
            propuesto = max(0, (actual or 0) - subio) if actual is not None else None
            _registrar(sku, f"fba:{sku}:{ahora}", "AMAZON", "fba_ingreso_sim",
                       actual, propuesto,
                       f"FBA subió {antes_fba}→{ahora} (+{subio}): SOLO-REGISTRO — "
                       f"restaría a Woo {actual}→{propuesto} (no se escribió)")
            aplicados.append({"sku": sku, "piezas": subio, "woo": f"{actual}→{propuesto}",
                              "solo_registro": True})
            continue
        ok, det, antes, despues = await _ajustar_woo(sku, -subio)
        _registrar(sku, f"fba:{sku}:{ahora}", "AMAZON",
                   "fba_ingreso" if ok else "fba_error", antes, despues,
                   f"FBA subió {antes_fba}→{ahora} (+{subio}) → Woo {antes}→{despues} ({det})")
        if ok:
            aplicados.append({"sku": sku, "piezas": subio, "woo": f"{antes}→{despues}"})
            fanout_stock.encolar(sku, motivo="ingreso a FBA")
    if aplicados:
        log.info("FBA: %d ingreso(s) descontados de Woo", len(aplicados))
    return {"ok": True, "revisados": len(actuales), "aplicados": aplicados}
