"""
fanout.py — Monitoreo y simulación del fan-out de stock DROP.

  GET  /api/fanout/estado           → flags, cola, contadores y últimos eventos.
  GET  /api/fanout/simular?sku=     → QUÉ haría con ese SKU ahora mismo, sin
                                      encolar ni escribir (seguro siempre).
  POST /api/fanout/encolar?sku=     → lo mete a la cola real (respeta dry-run).
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from config import settings

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
    if settings.supabase_read_fanout_log:
        from services import fanout_read
        filas = fanout_read.movimientos_full(horas)
    else:
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


@router.get("/inventario/estado")
def inventario_estado():
    """Vigilante de inventario (Odoo →delta→ Woo →cambio→ canales)."""
    from services import stock_watch
    return stock_watch.estado()


@router.post("/inventario/revisar")
async def inventario_revisar(
    forzar: bool = Query(False, description="Corre aunque esté apagado / pase el tope")
):
    """
    Dispara una pasada del vigilante de inventario a mano.

    `forzar=true` sirve para dos cosas: correrlo estando apagado (una pasada en
    solo-registro es inocua) y saltarse el cortacircuitos cuando el volumen de
    cambios es REAL y ya se revisó.
    """
    from services import stock_watch
    return await stock_watch.revisar(forzar=forzar)


@router.get("/inventario/pendientes")
def inventario_pendientes(limite: int = Query(50, ge=1, le=500)):
    """Lo que el vigilante de inventario propuso/aplicó, más reciente primero."""
    from services import db
    if settings.supabase_read_fanout_log:
        from services import fanout_read
        filas = fanout_read.pendientes_inventario(limite)
    else:
        filas = db.fetch_all(
            """SELECT ts, sku, accion, motivo, resultado, dry_run FROM fanout_log
               WHERE accion IN ('odoo_delta','odoo_delta_registro','woo_cambio',
                                'woo_cambio_registro','stock_watch_freno')
               ORDER BY id DESC LIMIT %s""", (limite,))
    return {"eventos": filas, "total": len(filas)}


@router.post("/alinear")
def alinear(canal: str = Query(..., description="tiktok | mercado_libre | temu"),
            confirmar: bool = Query(False, description="Sin true solo cuenta, no encola"),
            limite: int = Query(0, ge=0, le=5000, description="0 = sin tope")):
    """
    Alineación inicial de un canal: encola en el fan-out TODOS los SKUs con
    publicación viva en ese canal, para que la primera sincronización no
    espere a que cada SKU se venda o se mueva (el fan-out no tiene barrido
    propio: es 100% por evento).

    No escribe nada por sí mismo: ENCOLA, y cada SKU pasa por plan() con todas
    sus guardas (dry-run, FANOUT_CANALES, pausas, DESCONOCIDO≠0…). Con
    `confirmar=false` solo devuelve cuántos SKUs encolaría.

    Nació para la corrida inicial de TikTok tras revivir el token (18-ago):
    285 ACTIVATE, de los cuales solo ~24 divergían de Woo.
    """
    from services import supabase_db as sdb
    canal = (canal or "").strip().lower()
    filtros = {
        # TikTok: `status` es quien dice si está a la venta (ACTIVATE).
        "tiktok": "canal='tiktok' and status='ACTIVATE'",
        # ML: activas y pausadas DROP (las FULL las descarta el propio fan-out,
        # pero se filtran aquí para no encolar de más).
        "mercado_libre": ("canal='mercado_libre' and situacion in ('active','paused') "
                          "and coalesce(is_fulfillment,false)=false"),
        # Temu: DROP-only por decisión (18-ago); vivo = tiene goodsId. Los
        # estados crudos no distinguen activo/inactivo y aquí no bloquean —
        # la rama de temu en _destinos aplica la política fina.
        "temu": "canal='temu' and coalesce(listing_id,'') <> ''",
    }
    if canal not in filtros:
        return {"ok": False, "motivo": f"canal '{canal}' sin alineación definida",
                "canales": sorted(filtros)}
    filas = sdb.fetch_all(
        f"select distinct sku from channel.listings where {filtros[canal]}"
        + (f" limit {int(limite)}" if limite else ""))
    skus = [str(f["sku"]) for f in filas]
    if confirmar and skus:
        fanout_stock.encolar_varios(skus, motivo=f"alineacion inicial {canal}")
    return {"ok": True, "canal": canal, "skus": len(skus),
            "encolados": len(skus) if confirmar else 0,
            "confirmar": confirmar,
            "habilitado": fanout_stock.habilitado(),
            "dry_run": fanout_stock.dry_run(),
            "nota": ("encolados; ver /api/fanout/estado" if confirmar
                     else "conteo solamente — repetir con confirmar=true")}


@router.get("/odoo/monitor")
def odoo_monitor(horas: int = Query(24, ge=1, le=168),
                 limite: int = Query(80, ge=10, le=400)):
    """
    Vigilancia EN VIVO de la cadena Odoo → Woo → canales DROP.

    Existe porque el inventario depende de Odoo al 100%: cualquier movimiento
    del master tiene que verse, quedar auditado y poder revisarse después. Lee
    de `fanout_log` (bitácora del fan-out) y de `stock_watch_foto` (la foto que
    compara Woo contra Odoo en cada pasada); no llama a Odoo, así que es barato
    y se puede refrescar cada pocos segundos desde el panel.
    """
    from services import db, stock_watch

    filas = db.fetch_all(
        """SELECT ts, sku, accion, motivo, resultado, canal, stock_drop, objetivo
           FROM fanout_log
           WHERE ts >= UTC_TIMESTAMP() - INTERVAL %s HOUR
             AND accion IN ('odoo_delta','odoo_delta_registro','woo_cambio',
                            'woo_cambio_registro','odoo_master','stock_watch_freno')
           ORDER BY id DESC LIMIT %s""", (horas, limite))

    resumen = {r["accion"]: r["n"] for r in db.fetch_all(
        """SELECT accion, COUNT(*) n FROM fanout_log
           WHERE ts >= UTC_TIMESTAMP() - INTERVAL %s HOUR
             AND accion IN ('odoo_delta','woo_cambio','odoo_master')
           GROUP BY accion""", (horas,))}
    canales = db.fetch_all(
        """SELECT canal, accion, COUNT(*) n FROM fanout_log
           WHERE ts >= UTC_TIMESTAMP() - INTERVAL %s HOUR AND canal IS NOT NULL
             AND accion IN ('escribir','sin_cambio','omitir')
           GROUP BY canal, accion""", (horas,))
    errores = db.fetch_all(
        """SELECT canal, sku, LEFT(resultado, 150) resultado, ts FROM fanout_log
           WHERE ts >= UTC_TIMESTAMP() - INTERVAL %s HOUR
             AND resultado LIKE 'ERROR%%' ORDER BY id DESC LIMIT 20""", (horas,))

    # La foto de stock_watch ya trae Woo y Odoo lado a lado: de ahí salen las
    # discrepancias SIN volver a preguntarle a Odoo.
    try:
        foto = db.fetch_one(
            """SELECT COUNT(*) skus,
                      SUM(stock_woo <> stock_odoo) discrepan,
                      SUM(stock_odoo < 0) odoo_negativo,
                      SUM(stock_woo < 0) woo_negativo,
                      MAX(actualizado) ultima_foto
               FROM stock_watch_foto
               WHERE stock_woo IS NOT NULL AND stock_odoo IS NOT NULL""") or {}
        desalineados = db.fetch_all(
            """SELECT sku, stock_odoo, stock_woo, (stock_woo - stock_odoo) brecha,
                      actualizado
               FROM stock_watch_foto
               WHERE stock_woo IS NOT NULL AND stock_odoo IS NOT NULL
                 AND stock_woo <> stock_odoo
               ORDER BY ABS(stock_woo - stock_odoo) DESC LIMIT 25""")
    except Exception:  # noqa: BLE001 — la foto es opcional
        foto, desalineados = {}, []

    return {
        "vigilante": stock_watch.estado(),
        "horas": horas,
        "movimientos": filas,
        "resumen": resumen,
        "por_canal": canales,
        "errores": errores,
        "foto": foto,
        "desalineados": desalineados,
    }
