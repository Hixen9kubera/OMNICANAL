"""
costing_mirror.py — Espejo del dominio de COSTOS hacia Supabase (dual-write, F3).

Cada escritura de costos.py a MySQL (fuente de verdad) se replica aquí a
`costing.*` / `ops.process_log` cuando SUPABASE_DUAL_WRITE=true. Reglas del
patrón (las mismas del piloto de webhooks):

  1. MySQL manda. Un fallo del espejo JAMÁS rompe la operación: se loguea y se
     anota en ops.migration_issues (best-effort).
  2. Nunca en el event loop: psycopg2 bloquea — los llamadores usan en_hilo()
     (run_in_executor), el patrón que ya usa crear_producto._persistir_log.
  3. Upserts solo-si-cambió (IS DISTINCT FROM): los no-cambios no ensucian
     costing.cost_history.
  4. Atribución del historial: set_config('app.accion'/'app.usuario'/
     'app.formula_ver', ..., true) EN LA MISMA transacción del upsert — es lo
     que el trigger de cost_history lee. set_config es LOCAL a la transacción,
     compatible con el pooler transaccional 6543 (nada de SET de sesión).
  5. Revertir = SUPABASE_DUAL_WRITE=false.

NOTA: mientras el ETL de F2 (full-refresh) siga corriéndose, pisa lo espejado
aquí — son etapas: el ETL puebla, el espejo mantiene. No correr el ETL una vez
que el periodo de comparación (job de deltas) esté en marcha.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.costing_mirror")

# Versión de la fórmula de pricing vigente en costos.py (margen 0.48, IVA 0.16,
# fee por tabla _TARIFA_ML). Actualizar la etiqueta si la fórmula cambia: es lo
# que hace reproducible un cálculo histórico.
FORMULA_VER = "costos.py/v1-margen48-iva16-tarifaML202607"


def activo() -> bool:
    return settings.supabase_dual_write and sdb.disponible()


def en_hilo(fn: Callable, *args) -> None:
    """Ejecuta el espejo fuera del event loop (y fuera del camino crítico)."""
    if not activo():
        return
    try:
        asyncio.get_running_loop().run_in_executor(None, fn, *args)
    except RuntimeError:  # sin loop (contexto síncrono puro): directo
        fn(*args)


def _registrar_issue(tabla: str, sku: str, motivo: str) -> None:
    try:
        sdb.execute(
            "insert into ops.migration_issues (fase, tabla_origen, sku, motivo) "
            "values ('F3-dualwrite-costos', %s, %s, %s)",
            (tabla, sku, motivo[:500]),
        )
    except Exception:  # noqa: BLE001
        pass  # ya quedó en logs


def _atribuir(cur, accion: str, origen: str) -> None:
    """Deja la atribución del cambio donde el trigger de cost_history la lee."""
    cur.execute(
        "select set_config('app.accion', %s, true), "
        "       set_config('app.usuario', %s, true), "
        "       set_config('app.formula_ver', %s, true)",
        (accion or "auto", origen or "backend", FORMULA_VER),
    )


def _asegurar_identidad(cur, sku: str) -> None:
    """Identidad primero: si el SKU aún no existe en el maestro (producto recién
    creado), el espejo lo registra — es el primer paso del diseño final en el
    que el backend escribe la identidad al crear. No pisa filas existentes."""
    cur.execute(
        """insert into core.products (sku, status, source)
           values (%s, 'draft', 'backend-dualwrite')
           on conflict (sku) do nothing""",
        (sku,),
    )


def espejar_validados(sku: str, fila: dict[str, Any], accion: str = "auto",
                      origen: str = "backend") -> None:
    """Espeja el upsert de costos_validados (solo las columnas que costos.py toca;
    contenedor/cajas/etc. de la fila existente se conservan, igual que en MySQL)."""
    if not activo():
        return
    try:
        with sdb.get_cursor() as cur:
            _atribuir(cur, accion, origen)
            _asegurar_identidad(cur, sku)
            cur.execute(
                """insert into costing.costos_validados
                     (sku, largo, alto, ancho, peso, costo_producto, costo_cbm, costo_total)
                   values (%(sku)s, %(largo)s, %(alto)s, %(ancho)s, %(peso)s,
                           %(costo_producto)s, %(costo_cbm)s, %(costo_total)s)
                   on conflict (sku) do update set
                     largo = excluded.largo, alto = excluded.alto, ancho = excluded.ancho,
                     peso = excluded.peso, costo_producto = excluded.costo_producto,
                     costo_cbm = excluded.costo_cbm, costo_total = excluded.costo_total
                   where (costos_validados.largo, costos_validados.alto,
                          costos_validados.ancho, costos_validados.peso,
                          costos_validados.costo_producto, costos_validados.costo_cbm,
                          costos_validados.costo_total)
                     is distinct from
                         (excluded.largo, excluded.alto, excluded.ancho, excluded.peso,
                          excluded.costo_producto, excluded.costo_cbm, excluded.costo_total)""",
                {**fila, "sku": sku, "costo_total": fila.get("costo_total")},
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("espejo costos_validados(%s) falló (la operación continúa): %s", sku, exc)
        _registrar_issue("costos_validados", sku, f"espejo fallo: {exc}")


def espejar_finales(sku: str, fila: dict[str, Any], accion: str = "auto",
                    origen: str = "backend") -> None:
    """Espeja el upsert de costos_finales. Las dimensiones NO viajan (en el
    modelo v4 viven solo en costos_validados); se agrega formula_ver."""
    if not activo():
        return
    try:
        with sdb.get_cursor() as cur:
            _atribuir(cur, accion, origen)
            _asegurar_identidad(cur, sku)
            cur.execute(
                """insert into costing.costos_finales
                     (sku, costo_producto, costo_cbm, costo_unitario, costo_comision,
                      costo_fee_envio, precio_sugerido, precio_base, ml_cat_id,
                      pct_comision, peso_origen, formula_ver, comision_consultada_at)
                   values (%(sku)s, %(costo_producto)s, %(costo_cbm)s, %(costo_unitario)s,
                           %(costo_comision)s, %(costo_fee_envio)s, %(precio_sugerido)s,
                           %(precio_base)s, %(ml_cat_id)s, %(pct_comision)s,
                           %(peso_origen)s, %(formula_ver)s, now())
                   on conflict (sku) do update set
                     costo_producto = excluded.costo_producto, costo_cbm = excluded.costo_cbm,
                     costo_unitario = excluded.costo_unitario,
                     costo_comision = excluded.costo_comision,
                     costo_fee_envio = excluded.costo_fee_envio,
                     precio_sugerido = excluded.precio_sugerido,
                     precio_base = excluded.precio_base, ml_cat_id = excluded.ml_cat_id,
                     pct_comision = excluded.pct_comision, peso_origen = excluded.peso_origen,
                     formula_ver = excluded.formula_ver,
                     comision_consultada_at = excluded.comision_consultada_at
                   where (costos_finales.costo_producto, costos_finales.costo_cbm,
                          costos_finales.costo_unitario, costos_finales.costo_comision,
                          costos_finales.costo_fee_envio, costos_finales.precio_sugerido,
                          costos_finales.precio_base, costos_finales.ml_cat_id,
                          costos_finales.pct_comision, costos_finales.peso_origen)
                     is distinct from
                         (excluded.costo_producto, excluded.costo_cbm, excluded.costo_unitario,
                          excluded.costo_comision, excluded.costo_fee_envio,
                          excluded.precio_sugerido, excluded.precio_base, excluded.ml_cat_id,
                          excluded.pct_comision, excluded.peso_origen)""",
                {"sku": sku, "formula_ver": FORMULA_VER,
                 **{k: fila.get(k) for k in (
                     "costo_producto", "costo_cbm", "costo_unitario", "costo_comision",
                     "costo_fee_envio", "precio_sugerido", "precio_base", "ml_cat_id",
                     "pct_comision", "peso_origen")}},
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("espejo costos_finales(%s) falló (la operación continúa): %s", sku, exc)
        _registrar_issue("costos_finales", sku, f"espejo fallo: {exc}")


def espejar_log(sku: str, accion: str, origen: str, detalle: dict[str, Any]) -> None:
    """Espeja costos_logs → ops.process_log (detalle truncado a 4 KB, la misma
    salvaguarda de crear_logs)."""
    if not activo():
        return
    try:
        detalle_json = json.dumps(detalle, ensure_ascii=False, default=str)
        if len(detalle_json) > 4000:
            # el recorte invalida el JSON: se re-empaca como valor string válido
            detalle_json = json.dumps({"truncado": True, "detalle": detalle_json[:4000]},
                                      ensure_ascii=False)
        sdb.execute(
            """insert into ops.process_log (proceso, origen, sku, accion, estado, detalle)
               values ('costos', %s, %s, %s, 'ok', %s::jsonb)""",
            (origen or "backend", sku, accion, detalle_json),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("espejo process_log(%s) falló (la operación continúa): %s", sku, exc)
        _registrar_issue("costos_logs", sku, f"espejo fallo: {exc}")
