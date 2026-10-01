"""
fanout_recuperar.py — Vuelve a encolar los cambios de stock que nunca se repartieron.

POR QUÉ EXISTE
--------------
La cola del fan-out (`fanout_stock._pendientes`) vive en MEMORIA. Un reinicio del
backend a media cola —un despliegue, o cambiar una variable en Railway (regla 12)—
la tira, y como el fan-out solo actúa cuando Woo cambia, el SKU se queda con el
número viejo en los canales hasta que su stock se vuelva a mover.

Pasó el 29-sep-2026: la pasada de stock_watch de las 12:04 (CDMX) escribió 72
cambios en Woo, el fan-out alcanzó a repartir 3 y el despliegue de v0.596.0
reemplazó el contenedor. Los otros 69 no llegaron a ningún canal; dos días
después MUE-0293-AZL seguía a la venta en Temu con 37 piezas y Woo en 0.

CÓMO LO RESUELVE
----------------
Sin guardar la cola en otra parte: `ops.fanout_log` YA es el registro durable de
lo que stock_watch escribió (`odoo_delta` / `woo_cambio`) y de lo que el fan-out
repartió. Al arrancar y luego cada `fanout_recuperar_min`, se buscan los SKUs
cuyo ÚLTIMO cambio no tiene después ningún evento del fan-out, y se vuelven a
encolar. Es idempotente: el fan-out relee Woo en vivo y, si el canal ya coincide,
el plan sale `sin_cambio`.

LÍMITES (a propósito)
---------------------
- Solo cambios con más de `gracia_min` de antigüedad: lo reciente puede seguir en
  la cola. Al arrancar la cola está recién vacía, así que la gracia baja a 3 min.
- Solo cambios de las últimas `horas`: lo muy viejo pide una alineación
  consciente (`POST /api/fanout/alinear`), no un reenvío automático.
- Tope de SKUs por vuelta, y se salta lo que ya está en la cola.
- Un mismo cambio (sku, ts) se reencola UNA vez por proceso: si su evento no llega
  a la bitácora (kubera caída), no entra en bucle.
- Necesita que la bitácora se escriba en kubera (`supabase_write_fanout_log`):
  sin ella, todo cambio parecería sin repartir.
- Cubre lo que pasa por stock_watch. Una venta que se pierde en la cola la vuelve
  a ver stock_watch en su siguiente pasada (como `woo_cambio`), y si ESE cambio
  también se pierde, lo recupera esto.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.fanout_recuperar")

# Lo que cuenta como «el fan-out ya lo procesó»: cualquier fila de un evento suyo.
_ACC_FANOUT = ["escribir", "omitir", "sin_cambio", "sin_destinos"]
MOTIVO = "recuperado: cambio sin repartir"
GRACIA_ARRANQUE_MIN = 3

_lock = threading.Lock()
_ya: dict[tuple[str, str], float] = {}   # (sku, ts del cambio) → cuándo se reencoló
_ultimo: dict[str, Any] = {}


def habilitado() -> bool:
    return bool(getattr(settings, "fanout_recuperar_enabled", False))


def candidatos(horas: int, gracia_min: int, limite: int) -> list[dict[str, Any]]:
    """SKUs cuyo último cambio de stock_watch no tiene evento del fan-out después.

    El cambio que falló al escribirse en Woo («ESCRITURA FALLÓ») no cuenta:
    stock_watch no lo encola y lo reintenta en su siguiente pasada.
    """
    from services import supabase_db as sdb
    return sdb.fetch_all(
        """with c as (
             select distinct on (sku) sku::text as sku, ts from ops.fanout_log
              where canal = 'woocommerce' and accion in ('odoo_delta', 'woo_cambio')
                and resultado not like '%%FALLÓ%%'
                and ts > now() - make_interval(hours => %(h)s)
              order by sku, ts desc, id desc),
           e as (
             select sku::text as sku, max(ts) as t from ops.fanout_log
              where accion = any(%(a)s) and coalesce(canal, '') <> 'woocommerce'
                and ts > now() - make_interval(hours => %(h)s + 1)
              group by sku)
           select c.sku, c.ts from c left join e using (sku)
            where c.ts < now() - make_interval(mins => %(g)s)
              and (e.t is null or e.t < c.ts)
            order by c.ts
            limit %(n)s""",
        {"h": int(horas), "g": int(gracia_min), "a": _ACC_FANOUT, "n": int(limite)})


def revisar(arranque: bool = False) -> dict[str, Any]:
    """Una vuelta. SÍNCRONA a propósito: el scheduler la corre con
    `asyncio.to_thread` (regla 11: lee la base)."""
    from services import fanout_stock
    if not habilitado():
        return {"ok": False, "motivo": "FANOUT_RECUPERAR_ENABLED apagado"}
    if not fanout_stock.habilitado():
        return {"ok": False, "motivo": "el fan-out está apagado"}
    if not getattr(settings, "supabase_write_fanout_log", False):
        return {"ok": False, "motivo": "la bitácora del fan-out no se escribe en kubera: no hay con qué comparar"}
    horas = max(1, int(settings.fanout_recuperar_horas))
    gracia = max(1, int(settings.fanout_recuperar_gracia_min))
    if arranque:
        gracia = min(gracia, GRACIA_ARRANQUE_MIN)
    tope = max(1, int(settings.fanout_recuperar_tope))

    t0 = time.time()
    filas = candidatos(horas, gracia, tope)
    with fanout_stock._lock:
        en_cola = set(fanout_stock._pendientes)
    nuevos: list[str] = []
    with _lock:
        vencido = time.time() - 2 * horas * 3600
        for clave in [k for k, cuando in _ya.items() if cuando < vencido]:
            _ya.pop(clave, None)
        for f in filas:
            clave = (str(f["sku"]), f["ts"].isoformat())
            if clave in _ya or clave[0] in en_cola:
                continue
            _ya[clave] = time.time()
            nuevos.append(clave[0])
    if nuevos:
        fanout_stock.encolar_varios(nuevos, motivo=MOTIVO)
        log.warning("Fan-out: %d cambio(s) que nunca se repartieron, de vuelta a la cola: %s%s",
                    len(nuevos), ", ".join(nuevos[:10]), " …" if len(nuevos) > 10 else "")
    _ultimo.clear()
    _ultimo.update(ts=time.time(), arranque=arranque, candidatos=len(filas), encolados=len(nuevos),
                   muestra=nuevos[:15], segundos=round(time.time() - t0, 2), horas=horas,
                   gracia_min=gracia)
    return {"ok": True, **_ultimo}


def estado() -> dict[str, Any]:
    return {
        "habilitado": habilitado(),
        "cada_min": max(5, int(getattr(settings, "fanout_recuperar_min", 10))),
        "horas": int(getattr(settings, "fanout_recuperar_horas", 6)),
        "gracia_min": int(getattr(settings, "fanout_recuperar_gracia_min", 15)),
        "tope": int(getattr(settings, "fanout_recuperar_tope", 300)),
        "ultima_vuelta": dict(_ultimo) or None,
    }
