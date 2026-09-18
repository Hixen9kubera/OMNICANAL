"""
fulfillment_etapas.py — Las tres etapas del rail que SÍ se pueden llenar hoy:
RECIBIDO (observado), ACTIVO y 1ª VENTA, para cada envío a FULL o FBA.

LECTURA PURA de kubera (`SELECT`). Si kubera no está, el envío se queda con sus
etapas en `null` y la pestaña sigue funcionando: nunca se inventa una fecha.

═══════════════════════════════════════════════════════════════════════════════
DE DÓNDE SALE CADA ETAPA, Y POR QUÉ NO ES LO QUE DICE EL PANEL DE ML
═══════════════════════════════════════════════════════════════════════════════
Medido el 17-sep-2026 (590 GET contra la API de ML + la documentación oficial):
**Mercado Libre NO tiene API de envíos a Full**. No existe ningún recurso que
devuelva un envío por su número, ni su estado, ni las unidades declaradas. Lo
que sigue es lo que se puede reconstruir SIN ese recurso, y cada cosa se rotula
por lo que de verdad es.

· RECIBIDO → **llegada OBSERVADA**, no el aviso de ML. Sale de las subidas de
  `stock_full` en `channel.listing_history`, que es la foto que toma el sync
  cada 15 minutos. Llega con retraso de minutos u horas y viaja como `aprox`.
  No es "unidades recibidas" del panel: el panel cuenta al escanear en bodega,
  esto cuenta cuando las piezas ya se pueden vender. Las dos cifras no tienen
  por qué coincidir — de hecho el envío 76309173 decía 410/410 "procesamiento
  finalizado" cuando por API sólo habían bajado 170 piezas del CEDIS.

· ACTIVO → primera vez que la publicación se marca `is_fulfillment` o su
  `stock_full` sube desde 0 después de la salida. La hora es CUÁNDO SE OBSERVÓ
  (lote cada 15 min), así que también va como `aprox`.

· 1ª VENTA → `channel.sales_daily_completa` con `is_full`. Es por DÍA, no por
  hora: se manda a medianoche del día de la venta y se rotula como día.

El rail tenía dos etapas más —SOLICITADO y VALIDADO por Bodega— y se quitaron el
17-sep-2026: no las registra ningún sistema (la lista de Andy no se guarda y las
unidades declaradas sólo viven en el Seller Center de ML), así que eran dos
celdas rayadas en cada renglón. Vuelven cuando se capturen de verdad.

OJO CON LAS PIEZAS: las que entran a FULL en la ventana de un envío **pueden
incluir las de otro envío del mismo SKU** (los envíos a un mismo CEDIS se
mezclan y bajan por goteo). Por eso la cifra viaja rotulada como "entró a FULL
en la ventana", nunca como "recibidas de este envío", y en el rail sólo se
enseña la FECHA y la cobertura ("4 de 6 SKUs"), no una cantidad.

VENTANAS: la llegada se busca en los 30 días siguientes a la salida y la
activación en 45. Son cotas para no atribuirle a un envío lo que llegó meses
después; `channel.listing_history` además sólo existe desde el 17-jul-2026.
"""
from __future__ import annotations

import bisect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fulfillment_etapas")

# Cuánto después de la salida se le sigue atribuyendo una llegada / activación.
DIAS_LLEGADA = 30
DIAS_ACTIVACION = 45

# El histórico de cambios arranca aquí: antes de esta fecha no hay nada que ver
# y decir "sin dato" es lo correcto.
DESDE_HISTORIA = datetime(2026, 7, 17, tzinfo=timezone.utc)

_SQL_CUENTAS = """
    select id::text, legacy_code, channel_id from core.accounts where is_active
"""

# Subidas de stock_full: cada una es mercancía que ya se puede vender.
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
    """Las tres lecturas agregadas. BLOQUEANTE: va dentro del hilo del router."""
    if not sdb.disponible():
        return None
    cuentas = sdb.fetch_all(_SQL_CUENTAS)
    # legacy_code → id, y canal → legacy_code de la cuenta que guarda ese stock.
    por_codigo = {c["legacy_code"]: c["id"] for c in cuentas}
    llegadas = {(f["sku"], f["acc"]): (f["fechas"], f["deltas"]) for f in sdb.fetch_all(_SQL_LLEGADAS)}
    activaciones = {(f["sku"], f["acc"]): f["fechas"] for f in sdb.fetch_all(_SQL_ACTIVACIONES)}
    ventas = {(f["sku"], f["cuenta"]): f["dias"] for f in sdb.fetch_all(_SQL_VENTAS)}
    return {"cuentas": por_codigo, "llegadas": llegadas,
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


def aplicar(envios: list[dict[str, Any]], datos: dict[str, Any] | None) -> None:
    """Rellena etapas[2], [3] y [4] y agrega `cobertura` a cada envío. In place."""
    if not datos:
        return
    for e in envios:
        # SÓLO salidas ya validadas. Una salida que bodega no ha cerrado no puede
        # tener "llegada": lo que entrara a FULL en esos días sería de otro envío
        # del mismo SKU, y atribuírselo a éste sería inventar.
        base_iso = (e["etapas"][1] or {}).get("ts")
        codigo = _codigo(e["canal"], e.get("cuenta"))
        if not base_iso or not codigo:
            continue
        acc = datos["cuentas"].get(codigo)
        base = datetime.fromisoformat(base_iso)
        if not acc or base < DESDE_HISTORIA - timedelta(days=DIAS_LLEGADA):
            # Antes del 17-jul no hay historia: "sin dato" es la verdad.
            continue
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
                # Sólo las subidas dentro de la ventana, no toda la historia del SKU.
                piezas += sum(float(d) for f, d in zip(fechas, deltas) if base <= f <= tope_lleg)
                r["llegada"] = primera.isoformat()
                r["piezas_llegadas"] = round(sum(
                    float(d) for f, d in zip(fechas, deltas) if base <= f <= tope_lleg))
            a = _primera(datos["activaciones"].get((sku, acc), []), base, tope_act)
            if a:
                activo.append(a)
                r["activacion"] = a.isoformat()
            dias = datos["ventas"].get((sku, codigo), [])
            i = bisect.bisect_left(dias, base.date())
            if i < len(dias):
                vendio.append(dias[i])
                r["primera_venta"] = dias[i].isoformat()

        n = len(e["lineas"]) or 1
        if llego:
            # `aprox`: la hora es cuándo lo VIO el sync (cada 15 min), no cuándo ocurrió.
            e["etapas"][2] = {"ts": min(llego).isoformat(), "aprox": True}
        if activo:
            e["etapas"][3] = {"ts": min(activo).isoformat(), "aprox": True}
        if vendio:
            # La venta se fecha por DÍA en hora de CDMX (`sales_daily` hace
            # `creado_at AT TIME ZONE 'America/Mexico_City'`). Se manda al
            # mediodía de ese día con `dia: True` y el panel pinta sólo el día.
            # Antes iba a medianoche UTC: en CDMX eso son las 18:00 del día
            # ANTERIOR, y el rail decía "08 sep ~18:00" para una venta del 9.
            e["etapas"][4] = {"ts": f"{min(vendio).isoformat()}T12:00:00-06:00", "dia": True}
        e["cobertura"] = {
            "skus": n,
            "llegaron": len(llego), "piezas_llegadas": round(piezas),
            "activos": len(activo), "vendieron": len(vendio),
        }


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
    return {"kubera": True, "desde_historia": DESDE_HISTORIA.date().isoformat(),
            "ventana_llegada_dias": DIAS_LLEGADA, "ventana_activacion_dias": DIAS_ACTIVACION}
