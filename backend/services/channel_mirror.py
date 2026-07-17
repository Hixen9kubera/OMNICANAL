"""
channel_mirror.py — Espejo del dominio CHANNEL hacia Supabase (dual-write, F3).

Cada tanda del sync de inventario (canal_inventario en MySQL) se replica a
`channel.listings`; el TRIGGER de la base (channel.fn_listing_history) captura
automáticamente los cambios de precio/stock/FULL/status en
`channel.listing_history` — la base del monitoreo de precios por plataforma.

Mismas reglas que costing_mirror (el patrón probado):
  1. MySQL manda; un fallo del espejo JAMÁS rompe el sync (log + migration_issues).
  2. Nunca en el event loop: los llamadores usan en_hilo().
  3. Upsert solo-si-cambió: los no-cambios no disparan el trigger ni tocan updated_at.
  4. Identidad primero: SKUs que el maestro no conoce se registran solos.
  5. Flag propio del dominio: SUPABASE_DUAL_WRITE_CHANNEL (revertir = apagarlo),
     independiente del de costos para poder apagar uno sin el otro.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.channel_mirror")

# cuenta legacy -> uuid de core.accounts (cache de proceso; 4 filas, estable)
_cuentas: dict[str, str] | None = None

# misma regla que el ETL: las tablas viejas usan cuenta='' en canales mono-cuenta
_CUENTA_DEFAULT = {"mercado_libre": "BEKURA", "amazon": "AMAZON", "general": "GENERAL"}


def activo() -> bool:
    return settings.supabase_dual_write_channel and sdb.disponible()


def en_hilo(fn: Callable, *args) -> None:
    if not activo():
        return
    try:
        asyncio.get_running_loop().run_in_executor(None, fn, *args)
    except RuntimeError:
        fn(*args)


def _cuenta_uuid(canal: str, cuenta: str) -> str | None:
    global _cuentas
    if _cuentas is None:
        _cuentas = {r["legacy_code"]: str(r["id"])
                    for r in sdb.fetch_all("select legacy_code, id from core.accounts")}
    legacy = (cuenta or "").strip() or _CUENTA_DEFAULT.get(canal, "")
    return _cuentas.get(legacy)


def _registrar_issue(sku, motivo: str) -> None:
    try:
        sdb.execute(
            "insert into ops.migration_issues (fase, tabla_origen, sku, motivo) "
            "values ('F3-dualwrite-channel', 'canal_inventario', %s, %s)",
            (sku, motivo[:500]),
        )
    except Exception:  # noqa: BLE001
        pass


def espejar_inventario(rows: list[dict[str, Any]]) -> None:
    """Espeja una tanda del sync (las mismas filas que fueron a canal_inventario).

    Todo en UNA transacción: set_config de la vía (para el trigger de historia),
    identidad de SKUs nuevos, y upserts solo-si-cambió por fila.
    """
    if not activo() or not rows:
        return
    try:
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('app.via', 'sync', true)")
            for r in rows:
                sku = str(r.get("sku") or "").strip()
                if not sku or len(sku) > 100 or any(ch.isspace() for ch in sku):
                    continue  # inválidos conocidos: ya inventariados en el Excel
                canal = r.get("canal") or ""
                cuenta_id = _cuenta_uuid(canal, r.get("cuenta") or "")
                if not cuenta_id:
                    _registrar_issue(sku, f"cuenta sin uuid: canal={canal} cuenta={r.get('cuenta')}")
                    continue
                stock_full = r.get("stock_full") if canal == "mercado_libre" else r.get("stock_fba")
                cur.execute(
                    """insert into core.products (sku, status, source)
                       values (%s, 'draft', 'backend-dualwrite')
                       on conflict (sku) do nothing""", (sku,))
                cur.execute(
                    """insert into channel.listings
                         (sku, account_id, canal, listing_id, price, stock_own,
                          stock_full, is_fulfillment, situacion)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       on conflict (sku, account_id, canal) do update set
                         listing_id = coalesce(excluded.listing_id, listings.listing_id),
                         price = excluded.price, stock_own = excluded.stock_own,
                         stock_full = excluded.stock_full,
                         is_fulfillment = excluded.is_fulfillment,
                         situacion = excluded.situacion
                       where (listings.price, listings.stock_own, listings.stock_full,
                              listings.is_fulfillment, listings.situacion)
                         is distinct from
                             (excluded.price, excluded.stock_own, excluded.stock_full,
                              excluded.is_fulfillment, excluded.situacion)""",
                    (sku, cuenta_id, canal, r.get("item_id"), r.get("precio"),
                     r.get("stock_real"), stock_full, bool(r.get("es_full")),
                     r.get("situacion")),
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("espejo channel falló (el sync continúa): %s", exc)
        _registrar_issue(None, f"espejo tanda fallo: {exc}")
