"""
fulfillment_etapas.py — Las tres etapas del rail que SÍ se pueden llenar hoy:
RECIBIDO, ACTIVO y 1ª VENTA, para cada envío a FULL o FBA.

LECTURA PURA de kubera (`SELECT`). Si kubera no está, el envío se queda con sus
etapas en `null` y la pestaña sigue funcionando: nunca se inventa una fecha.

═══════════════════════════════════════════════════════════════════════════════
MERCADO LIBRE: LA LLEGADA SALE DE LOS AVISOS DE FULL (desde v0.545.0)
═══════════════════════════════════════════════════════════════════════════════
ML no tiene API de envíos a Full (medido el 17-sep-2026), pero SÍ avisa por
webhook cada movimiento de su bodega (`fbm_stock_operations`, de las DOS
cuentas). El backend resuelve cada aviso —tipo, piezas y SKU— y lo anota en
`ops.fanout_log` (`services/stock_full.py`). Cuando la mercancía de un envío se
vuelve vendible llega como `TRANSFER_DELIVERY` (vía CEDIS, el camino normal) o
`INBOUND_RECEPTION` (directo a bodega), en tandas a lo largo de 1 a 4 días.

Medido el 18-sep contra 19 salidas: la suma de esos avisos por SKU da EXACTO lo
que salió de Odoo (S37750: DEC-0182-BLN 150 de 150 en 8 avisos). Antes se leía
de `channel.listing_history`, y eso fallaba de dos formas:
  · DEC-0182-BLN vive en una publicación (MLM6015038652) que `channel.listings`
    no tiene: sin fila no hay historia, y el panel decía "no llegó";
  · el sync registró un salto falso 0 → 610 → 0 en DEC-0182-NEG el 12-sep, y el
    rail pintaba esa hora como llegada y "entraron 760".

LAS REGLAS QUE NO SE AFLOJAN
  1. La ventana empieza 4 días ANTES de que bodega valide (nunca antes de
     crearse la orden): en S35628 ML recibió el 23-ago y Odoo validó el 26, y
     con la ventana en la validación 21 SKUs salían "no llegó" habiendo llegado.
     Medido en 173 casos limpios: lo más temprano de verdad fue 3.2 días antes;
     lo de 4 a 28 días antes era de OTRO envío (arrancar al crear la orden le
     colgaba a S37015 las últimas tandas del envío anterior del mismo SKU).
  2. Termina en la siguiente orden del mismo SKU en la misma cuenta (lo que
     llegue después es de ésa) o a los 30 días.
  3. RECHAZO = lo enviado que no llegó cuando el envío ya CERRÓ (10 días después
     de la salida). Antes de eso es "en proceso": las tandas tardan días.
  4. Cada operación de ML cuenta UNA vez: ML reenvía avisos y la bitácora los
     repetía (7% de las piezas, v0.545.2). Llegar MÁS de lo enviado, ya sin
     duplicados, no es sobrante: ML mueve piezas entre sus bodegas con el mismo
     tipo de aviso. Se tope a lo enviado y se dice.
  5. Un aviso cuyo SKU no se pudo leer (publicación con VARIANTES: el SKU vive en
     cada variante) queda como '?'. Si en la ventana de un envío con faltantes
     hubo de ésos, se avisa: "pueden ser de este envío" (S36996 llegó así).

· ACTIVO → cuándo quedó activo el ÚLTIMO SKU que llegó (su primera pieza
  vendible en FULL). · 1ª VENTA → primer día con venta FULL desde que ese SKU
  llegó (`channel.sales_daily_completa`, por DÍA en hora de CDMX).

═══════════════════════════════════════════════════════════════════════════════
AMAZON FBA: sigue con lo que ve el sync (`channel.listing_history`), como `aprox`.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import bisect
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fulfillment_etapas")

# Cuánto después del inicio se le sigue atribuyendo una llegada / activación.
DIAS_LLEGADA = 30
DIAS_ACTIVACION = 45
# Días después de la salida en que el envío se da por cerrado: lo que no llegó
# para entonces, ML no lo recibió.
DIAS_CIERRE = 10
# Cuánto ANTES de la validación en Odoo puede llegar la mercancía a ML (bodega
# valida tarde): medido, a lo más 3.2 días en 173 casos.
DIAS_ADELANTO = 4

# El histórico de cambios del sync arranca aquí (Amazon).
DESDE_HISTORIA = datetime(2026, 7, 17, tzinfo=timezone.utc)
# Los avisos de llegada a FULL resueltos por SKU arrancan aquí: antes no hay con
# qué medir y decir "sin dato" es lo correcto (no "rechazado").
DESDE_AVISOS = datetime(2026, 8, 12, tzinfo=timezone.utc)

_SQL_CUENTAS = """
    select id::text, legacy_code, channel_id from core.accounts where is_active
"""

# Llegadas a FULL avisadas por ML y resueltas por el backend (stock_full.py).
# El `%` va DOBLE: con parámetros, psycopg2 lee `%` como un hueco.
#
# UNA VEZ POR OPERACIÓN (`distinct on (item_id)`, que aquí es el id de la
# operación de ML). ML reenvía el mismo aviso —mediana 4.5 min después, hasta
# media hora— y la bitácora lo anota otra vez: medido el 18-sep, 396 operaciones
# duplicadas y ~930 piezas contadas dos veces (7%). S38279 salía "26 de 25, +1"
# por la operación 6355804565657261632 anotada a las 18:11 y a las 18:15. Y un
# duplicado también puede TAPAR un faltante: 29 reales + 1 repetida = "completo".
_SQL_AVISOS = r"""
    select cuenta, sku, array_agg(ts order by ts) fechas, array_agg(n order by ts) piezas
      from (select distinct on (item_id) cuenta, sku::text sku, ts,
                   (regexp_match(resultado, '^(?:TRANSFER_DELIVERY|INBOUND_RECEPTION) x(\d+)'))[1]::int n
              from ops.fanout_log
             where motivo = 'movimiento FULL/FBA'
               and cuenta in ('BEKURA', 'SANCORFASHION')
               and ts >= %s
               and (resultado like 'TRANSFER_DELIVERY%%' or resultado like 'INBOUND_RECEPTION%%')
             order by item_id, ts) t
     where n is not null
     group by 1, 2
"""

# Subidas de stock_full vistas por el sync (sólo Amazon desde v0.545.0).
_SQL_LLEGADAS = """
    select sku::text sku, account_id::text acc,
           array_agg(changed_at order by changed_at) fechas,
           array_agg((coalesce(nullif(valor_nuevo,''),'0')::numeric
                      - coalesce(nullif(valor_anterior,''),'0')::numeric)
                     order by changed_at) deltas
      from channel.listing_history
     where campo = 'stock_full'
       and coalesce(nullif(valor_nuevo,''),'0')::numeric
           > coalesce(nullif(valor_anterior,''),'0')::numeric
     group by 1, 2
"""

# La publicación se prende en FULL: o se marca is_fulfillment, o su stock sube desde 0.
_SQL_ACTIVACIONES = """
    select sku::text sku, account_id::text acc,
           array_agg(changed_at order by changed_at) fechas
      from channel.listing_history
     where (campo = 'is_fulfillment' and valor_nuevo in ('true','t','1'))
        or (campo = 'stock_full'
            and coalesce(nullif(valor_anterior,''),'0')::numeric = 0
            and coalesce(nullif(valor_nuevo,''),'0')::numeric > 0)
     group by 1, 2
"""

# Primera venta FULL por SKU y cuenta (la vista ya une el histórico con lo vivo).
_SQL_VENTAS = """
    select sku::text sku, cuenta,
           array_agg(date order by date) dias
      from channel.sales_daily_completa
     where is_full and units_sold > 0
     group by 1, 2
"""


def _leer() -> dict[str, Any] | None:
    """Las lecturas agregadas. BLOQUEANTE: va dentro del hilo del router."""
    if not sdb.disponible():
        return None
    cuentas = sdb.fetch_all(_SQL_CUENTAS)
    por_codigo = {c["legacy_code"]: c["id"] for c in cuentas}
    avisos = {(f["sku"], f["cuenta"]): (f["fechas"], f["piezas"])
              for f in sdb.fetch_all(_SQL_AVISOS, (DESDE_AVISOS,))}
    llegadas = {(f["sku"], f["acc"]): (f["fechas"], f["deltas"]) for f in sdb.fetch_all(_SQL_LLEGADAS)}
    activaciones = {(f["sku"], f["acc"]): f["fechas"] for f in sdb.fetch_all(_SQL_ACTIVACIONES)}
    ventas = {(f["sku"], f["cuenta"]): f["dias"] for f in sdb.fetch_all(_SQL_VENTAS)}
    return {"cuentas": por_codigo, "avisos": avisos, "llegadas": llegadas,
            "activaciones": activaciones, "ventas": ventas}


# La cuenta del envío (como la ve el panel) → el código con el que kubera guarda
# su stock y sus ventas. FBA vive bajo la cuenta AMAZON.
def _codigo(canal: str, cuenta: str | None) -> str | None:
    if canal == "amazon":
        return "AMAZON"
    if canal == "meli":
        return {"Kubera": "BEKURA", "San Corpe": "SANCORFASHION"}.get(cuenta or "")
    return None


def _primera(fechas: list[datetime], desde: datetime, hasta: datetime) -> datetime | None:
    i = bisect.bisect_left(fechas, desde)
    return fechas[i] if i < len(fechas) and fechas[i] <= hasta else None


def _ts(etapa: dict | None) -> datetime | None:
    return datetime.fromisoformat(etapa["ts"]) if etapa and etapa.get("ts") else None


def _inicio(e: dict[str, Any]) -> datetime | None:
    """Desde cuándo una llegada puede ser de este envío (regla 1 del encabezado)."""
    creada, salida = _ts(e["etapas"][0]), _ts(e["etapas"][1])
    if salida:
        tope = salida - timedelta(days=DIAS_ADELANTO)
        return max(creada, tope) if creada else tope
    return creada


def _venta_desde(dias: list[Any], desde: datetime) -> Any | None:
    i = bisect.bisect_left(dias, desde.astimezone(timezone(timedelta(hours=-6))).date())
    return dias[i] if i < len(dias) else None


def _dia(d: Any) -> dict[str, Any]:
    # La venta se fecha por DÍA en hora de CDMX (`sales_daily` hace
    # `creado_at AT TIME ZONE 'America/Mexico_City'`): mediodía de ese día con
    # `dia: True`, y el panel pinta sólo el día. A medianoche UTC caía a las
    # 18:00 del día ANTERIOR en CDMX.
    return {"ts": f"{d.isoformat()}T12:00:00-06:00", "dia": True}


# ── Mercado Libre: avisos de FULL ───────────────────────────────────────────

def _aplicar_meli(envios: list[dict[str, Any]], datos: dict[str, Any], ahora: datetime) -> None:
    # Dónde empieza cada orden por (cuenta, SKU): la ventana de un envío se
    # corta en la siguiente orden del mismo SKU en la misma cuenta.
    inicios: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    for e in envios:
        codigo = _codigo(e["canal"], e.get("cuenta"))
        ini = _inicio(e)
        if e["canal"] == "meli" and codigo and ini:
            for r in e["lineas"]:
                inicios[(codigo, r["sku"])].append(ini)
    for v in inicios.values():
        v.sort()

    sin_sku = {c: datos["avisos"].get(("?", c), ([], [])) for c in ("BEKURA", "SANCORFASHION")}

    for e in envios:
        codigo = _codigo(e["canal"], e.get("cuenta"))
        ini = _inicio(e)
        if e["canal"] != "meli" or not codigo or not ini or ini < DESDE_AVISOS:
            # Sin cuenta no se sabe a qué bodega mirar; antes del 12-ago no hay
            # avisos resueltos. En los dos casos, "sin dato" y no "rechazado".
            continue
        salida = _ts(e["etapas"][1])
        hecha = e.get("estado_odoo") == "done"
        cerrado = bool(hecha and salida and ahora >= salida + timedelta(days=DIAS_CIERRE))

        primeras: list[datetime] = []
        vendio: list[Any] = []
        for r in e["lineas"]:
            sig = [x for x in inicios[(codigo, r["sku"])] if x > ini]
            fin = min(sig + [ini + timedelta(days=DIAS_LLEGADA)])
            fechas, piezas = datos["avisos"].get((r["sku"], codigo), ([], []))
            i0, i1 = bisect.bisect_left(fechas, ini), bisect.bisect_left(fechas, fin)
            llegadas = int(sum(piezas[i0:i1]))
            r["llegadas"] = llegadas
            if i1 > i0:
                r["llegada"] = fechas[i0].isoformat()
                r["llegada_ultima"] = fechas[i1 - 1].isoformat()
                primeras.append(fechas[i0])
                d = _venta_desde(datos["ventas"].get((r["sku"], codigo), []), fechas[i0])
                if d:
                    vendio.append(d)
                    r["primera_venta"] = d.isoformat()

            enviadas = r.get("enviadas")
            if not hecha:
                # Sin salida validada no se juzga nada (y ML a veces recibe antes).
                r["estado_llegada"] = "llegando" if llegadas else None
            elif not enviadas:
                r["estado_llegada"] = None       # Odoo no la surtió: no se esperaba nada
            elif llegadas >= enviadas:
                r["estado_llegada"] = "completo"
                if llegadas > enviadas:
                    r["llegadas_extra"] = llegadas - enviadas
            elif cerrado:
                r["estado_llegada"] = "rechazo_total" if not llegadas else "rechazo_parcial"
                r["rechazadas"] = enviadas - llegadas
            else:
                r["estado_llegada"] = "en_proceso"

        esperados = [r for r in e["lineas"] if (r.get("enviadas") if hecha else r["pedidas"]) or 0]
        piezas_env = sum(int(r.get("enviadas") or 0) for r in esperados) if hecha else None
        piezas_lleg = sum(min(int(r.get("llegadas") or 0), int((r.get("enviadas") if hecha else r["pedidas"]) or 0))
                          for r in esperados)
        faltan = (piezas_env - piezas_lleg) if hecha else None

        if primeras:
            # La hora es la del aviso de ML (segundos), no la de un sync: sin `aprox`.
            e["etapas"][2] = {"ts": min(primeras).isoformat()}
            e["etapas"][3] = {"ts": max(primeras).isoformat()}
        if vendio:
            e["etapas"][4] = _dia(min(vendio))
        cob: dict[str, Any] = {
            "fuente": "avisos",
            "skus": len(esperados) or len(e["lineas"]),
            "llegaron": sum(1 for r in esperados if r.get("llegadas")),
            "completos": sum(1 for r in esperados if r.get("estado_llegada") == "completo"),
            "activos": len(primeras), "vendieron": len(vendio),
            "piezas_enviadas": piezas_env, "piezas_llegadas": piezas_lleg,
            "cerrado": cerrado,
            "rechazadas": (sum(int(r.get("rechazadas") or 0) for r in e["lineas"]) if cerrado else None),
            "cierre": ((salida + timedelta(days=DIAS_CIERRE)).isoformat() if hecha and salida else None),
        }
        if faltan:
            # Sólo hasta el CIERRE del envío: lo que llegó sin SKU después ya no
            # puede explicar un faltante de éste (S35628 no tiene que cargar con
            # el sillón de variantes que llegó dos días después de su cierre).
            hasta = salida + timedelta(days=DIAS_CIERRE) if salida else ini + timedelta(days=DIAS_LLEGADA)
            f_sin, p_sin = sin_sku.get(codigo, ([], []))
            j0 = bisect.bisect_left(f_sin, ini)
            j1 = bisect.bisect_left(f_sin, min(hasta, ahora))
            if j1 > j0:
                cob["sin_sku"] = {"piezas": int(sum(p_sin[j0:j1])), "avisos": j1 - j0}
        e["cobertura"] = cob


# ── Amazon FBA: lo que ve el sync ────────────────────────────────────────────

def _aplicar_sync(e: dict[str, Any], datos: dict[str, Any], codigo: str) -> None:
    # SÓLO salidas ya validadas: lo que entrara antes sería de otro envío.
    base_iso = (e["etapas"][1] or {}).get("ts")
    if not base_iso:
        return
    acc = datos["cuentas"].get(codigo)
    base = datetime.fromisoformat(base_iso)
    if not acc or base < DESDE_HISTORIA - timedelta(days=DIAS_LLEGADA):
        return
    tope_lleg = base + timedelta(days=DIAS_LLEGADA)
    tope_act = base + timedelta(days=DIAS_ACTIVACION)
    llego: list[datetime] = []
    piezas = 0.0
    activo: list[datetime] = []
    vendio: list[Any] = []
    for r in e["lineas"]:
        sku = r["sku"]
        fechas, deltas = datos["llegadas"].get((sku, acc), ([], []))
        primera = _primera(fechas, base, tope_lleg)
        if primera:
            llego.append(primera)
            dentro = sum(float(d) for f, d in zip(fechas, deltas) if base <= f <= tope_lleg)
            piezas += dentro
            r["llegada"] = primera.isoformat()
            r["piezas_llegadas"] = round(dentro)
        a = _primera(datos["activaciones"].get((sku, acc), []), base, tope_act)
        if a:
            activo.append(a)
            r["activacion"] = a.isoformat()
        d = _venta_desde(datos["ventas"].get((sku, codigo), []), base)
        if d:
            vendio.append(d)
            r["primera_venta"] = d.isoformat()
    if llego:
        # `aprox`: la hora es cuándo lo VIO el sync (cada 15 min), no cuándo ocurrió.
        e["etapas"][2] = {"ts": min(llego).isoformat(), "aprox": True}
    if activo:
        e["etapas"][3] = {"ts": min(activo).isoformat(), "aprox": True}
    if vendio:
        e["etapas"][4] = _dia(min(vendio))
    e["cobertura"] = {"fuente": "sync", "skus": len(e["lineas"]) or 1,
                      "llegaron": len(llego), "piezas_llegadas": round(piezas),
                      "activos": len(activo), "vendieron": len(vendio)}


def aplicar(envios: list[dict[str, Any]], datos: dict[str, Any] | None,
            ahora: datetime | None = None) -> None:
    """Rellena etapas[2], [3] y [4] y agrega `cobertura` a cada envío. In place."""
    if not datos:
        return
    ahora = ahora or datetime.now(timezone.utc)
    _aplicar_meli(envios, datos, ahora)
    for e in envios:
        if e["canal"] == "amazon":
            _aplicar_sync(e, datos, "AMAZON")


def enriquecer(envios: list[dict[str, Any]]) -> dict[str, Any]:
    """Lee kubera y aplica las etapas. Nunca revienta la lectura de Odoo."""
    try:
        datos = _leer()
    except Exception as exc:  # noqa: BLE001
        log.warning("etapas de fulfillment: kubera no contestó (%s); se dejan en sin dato", exc)
        return {"kubera": False, "motivo": str(exc)[:200]}
    if not datos:
        return {"kubera": False, "motivo": "kubera no configurada en este ambiente"}
    aplicar(envios, datos)
    return {"kubera": True, "desde_avisos": DESDE_AVISOS.date().isoformat(),
            "desde_historia": DESDE_HISTORIA.date().isoformat(),
            "ventana_llegada_dias": DIAS_LLEGADA, "cierre_dias": DIAS_CIERRE}
