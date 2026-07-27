"""
fanout.py — Monitoreo y simulación del fan-out de stock DROP.

  GET  /api/fanout/estado           → flags, cola, contadores y últimos eventos.
  GET  /api/fanout/simular?sku=     → QUÉ haría con ese SKU ahora mismo, sin
                                      encolar ni escribir (seguro siempre).
  POST /api/fanout/encolar?sku=     → lo mete a la cola real (respeta dry-run).
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from services import fanout_stock

router = APIRouter(prefix="/api/fanout", tags=["fanout"])


@router.get("/estado")
def estado():
    """Estado del fan-out: flags, pendientes, contadores y bitácora reciente."""
    return fanout_stock.estado()


@router.get("/simular")
def simular(sku: str = Query(..., description="SKU a simular")):
    """
    Plan de fan-out para un SKU: stock DROP leído, objetivo y qué pasaría con
    cada publicación (escribir / sin cambio / omitir, con el motivo).
    NO escribe ni encola: es seguro aunque el fan-out esté encendido.
    """
    return fanout_stock.plan(sku)


@router.get("/full/observacion")
def full_observacion(horas: int = Query(24, ge=1, le=168)):
    """
    Qué está viendo el vigilante de FULL/FBA: catálogo de tipos de operación con
    tráfico REAL y qué haría cada uno con el stock de Woo.

    Es la pantalla de análisis del modo solo-registro: antes de dejar que mueva
    inventario hay que confirmar aquí que no aparecen tipos desconocidos y que
    los conocidos se comportan como dice la tabla de decisión.
    """
    from services import db, stock_full
    filas = db.fetch_all(
        """SELECT accion, resultado, sku, cuenta, stock_drop, objetivo, ts
           FROM fanout_log
           WHERE (accion LIKE 'full_%%' OR accion LIKE 'fba_%%')
             AND ts >= UTC_TIMESTAMP() - INTERVAL %s HOUR
           ORDER BY id DESC""", (horas,))
    # El tipo de ML viene al inicio del resultado ("TRANSFER_DELIVERY x2: …").
    tipos: dict[str, dict] = {}
    for f in filas:
        tipo = str(f.get("resultado") or "").split(" ")[0].split(":")[0] or "?"
        t = tipos.setdefault(tipo, {"tipo": tipo, "n": 0, "efecto_declarado":
                                    stock_full.EFECTO_EN_WOO.get(tipo, "DESCONOCIDO"),
                                    "acciones": {}, "ejemplo": None})
        t["n"] += 1
        t["acciones"][f["accion"]] = t["acciones"].get(f["accion"], 0) + 1
        if t["ejemplo"] is None:
            t["ejemplo"] = {"sku": f["sku"], "cuenta": f["cuenta"],
                            "woo": f"{f['stock_drop']}→{f['objetivo']}",
                            "detalle": f["resultado"], "ts": str(f["ts"])}
    desconocidos = [t for t in tipos.values() if t["efecto_declarado"] == "DESCONOCIDO"]
    return {
        "modo_solo_registro": stock_full.solo_registro(),
        "vigilante_encendido": stock_full.habilitado(),
        "horas": horas,
        "eventos": len(filas),
        "tipos_vistos": sorted(tipos.values(), key=lambda x: -x["n"]),
        "TIPOS_DESCONOCIDOS": desconocidos,   # ← si esto no está vacío, NO encender
        "tabla_de_decision": stock_full.EFECTO_EN_WOO,
    }


@router.post("/encolar")
def encolar(sku: str = Query(..., description="SKU a encolar"),
            motivo: str = Query("manual", description="Origen del encolado")):
    """Encola el SKU en el fan-out real (respeta FANOUT_ENABLED y DRY_RUN)."""
    fanout_stock.encolar(sku, motivo)
    return {"ok": True, "sku": sku,
            "habilitado": fanout_stock.habilitado(),
            "dry_run": fanout_stock.dry_run()}
