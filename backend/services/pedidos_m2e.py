"""
pedidos_m2e.py — Ventas de Temu/TikTok (vía M2E Cloud) → pedidos de WooCommerce.

M2E Cloud es el puente con los canales que aún no dan API directa (Temu,
TikTok). Su API pública NO publica listados (eso es su panel web), pero SÍ
entrega las ÓRDENES por canal:

    POST {base}/order/find/?channel=temu&account_token=<uuid>   body {}

Mismo patrón que pedidos_amazon: sondeo → normalizar → pedidos_ml.sincronizar
(candado, idempotencia, tabla con cuenta='TEMU'/'TIKTOK', creado = fecha de la
venta). Los canales M2E cumplen desde NUESTRA bodega → descuentan stock en Woo
(no hay "FULL" aquí).

Nota: al escribir esto Temu tiene 0 órdenes históricas — el mapeo de campos de
la orden se ajustará con la primera real; por eso se loggea el JSON crudo de
las primeras órdenes vistas y todo el parseo es defensivo.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from config import settings
from services import db, pedidos_ml

log = logging.getLogger("omnicanal.pedidos_m2e")

_BASE = "https://m2e.cloud/api/v1/api"
_CANAL_A_CUENTA = {"temu": "TEMU", "tiktok": "TIKTOK"}
_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}


def estado() -> dict[str, Any]:
    return _ultimo


def _num(v: Any) -> float:
    try:
        if isinstance(v, dict):  # {"amount": ..} / {"value": ..}
            v = v.get("amount") or v.get("value") or 0
        return float(v or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _normalizar(o: dict, canal: str) -> dict:
    """Orden de M2E → dict de pedidos_ml. Defensivo: el esquema real se
    confirmará con la primera orden (se loggea crudo)."""
    oid = str(o.get("id") or o.get("order_id") or o.get("channel_order_id")
              or o.get("number") or "")
    lineas = []
    for it in (o.get("items") or o.get("order_items") or o.get("lines") or []):
        qty = int(it.get("quantity") or it.get("qty") or 1)
        precio = _num(it.get("price") or it.get("unit_price") or it.get("item_price"))
        lineas.append({
            "item_id": str(it.get("id") or ""),
            "sku": (it.get("sku") or it.get("seller_sku") or "").strip(),
            "titulo": it.get("title") or it.get("name") or "",
            "variacion_id": None,
            "cantidad": qty,
            "precio_unitario": precio,
            "precio_lista": 0.0,
            "comision_ml": 0.0,
        })
    total = _num(o.get("total") or o.get("total_amount") or o.get("grand_total")) \
        or sum(l["precio_unitario"] * l["cantidad"] for l in lineas)
    fecha = (o.get("purchase_date") or o.get("create_date") or o.get("date")
             or o.get("created_at") or "")
    est = str(o.get("status") or "").lower()
    cancelada = "cancel" in est
    return {
        "id": oid,
        "cuenta": _CANAL_A_CUENTA.get(canal, canal.upper()),
        "estado": o.get("status"),
        "detalle": canal,
        "etiquetas": [],
        "fecha": fecha,
        "total": total,
        "pagado": total,
        "moneda": o.get("currency") or "MXN",
        "envio_costo": 0.0,
        "items": lineas,
        "envio": {"logistica": "mfn", "estado": ""},
        "es_full": False,  # Temu/TikTok siempre surten de NUESTRA bodega
        "pago_estado": o.get("status"),
        "pago_fecha": fecha,
        "comprador": {"id": None, "nick": "",
                      "nombre": str(o.get("buyer") or o.get("customer") or "Comprador")[:40],
                      "apellido": _CANAL_A_CUENTA.get(canal, canal)},
    }, cancelada


async def revisar() -> dict[str, Any]:
    """Una pasada: cuentas M2E válidas → órdenes por canal → pedidos."""
    tok = settings.m2e_api_token
    if not tok:
        _ultimo.update(estado="sin_token")
        return _ultimo
    creados = actualizados = errores = vistos = 0
    try:
        async with httpx.AsyncClient(timeout=30.0) as cli:
            h = {"access-token": tok, "Content-Type": "application/json"}
            r = await cli.get(f"{_BASE}/user/accounts/", headers=h)
            r.raise_for_status()
            cuentas = [a for a in r.json()
                       if a.get("channel") in _CANAL_A_CUENTA and a.get("is_valid")]
            previos = {f["ml_order_id"]: f["estado_wc"] for f in db.fetch_all(
                "SELECT ml_order_id, estado_wc FROM pedidos_ml "
                "WHERE cuenta IN ('TEMU','TIKTOK')")}
            for cta in cuentas:
                canal = cta["channel"]
                ro = await cli.post(
                    f"{_BASE}/order/find/?channel={canal}"
                    f"&account_token={cta['token']}", headers=h, json={})
                if ro.status_code != 200:
                    log.warning("m2e order/find %s: HTTP %s %s",
                                canal, ro.status_code, ro.text[:120])
                    continue
                lista = (ro.json() or {}).get("list") or []
                vistos += len(lista)
                for o in lista:
                    try:
                        orden, cancelada = _normalizar(o, canal)
                        if not orden["id"] or not orden["items"]:
                            # esquema aún desconocido: dejar el crudo en el log
                            log.info("m2e orden cruda (%s): %s", canal,
                                     json.dumps(o, ensure_ascii=False)[:800])
                            continue
                        destino = "cancelled" if cancelada else "processing"
                        if previos.get(orden["id"]) == destino:
                            continue
                        rp = await pedidos_ml.sincronizar(
                            orden["id"], forzar_estado=destino, orden=orden)
                        if rp.get("ok"):
                            creados += (rp.get("accion") == "creado")
                            actualizados += (rp.get("accion") == "actualizado")
                        else:
                            errores += 1
                            log.warning("pedido m2e %s falló: %s",
                                        orden["id"], rp.get("motivo"))
                        await asyncio.sleep(0.5)
                    except Exception as exc:  # noqa: BLE001
                        errores += 1
                        log.warning("orden m2e: %s", exc)
    except Exception as exc:  # noqa: BLE001
        _ultimo.update(estado=f"error: {exc}",
                       ts=datetime.now(timezone.utc).isoformat())
        return _ultimo
    _ultimo.update(estado="ok", ts=datetime.now(timezone.utc).isoformat(),
                   ordenes_vistas=vistos, creados=creados,
                   actualizados=actualizados, errores=errores)
    if creados or actualizados or errores:
        log.info("Pedidos M2E: %d creados, %d actualizados, %d err",
                 creados, actualizados, errores)
    return _ultimo
