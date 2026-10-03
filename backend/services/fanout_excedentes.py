"""
fanout_excedentes.py — Cuando TikTok o Temu ofrecen MÁS de lo que hay, se baja al
número de Woo en cuanto el censo lo ve. Nunca se sube nada.

POR QUÉ EXISTE (medido el 2-oct-2026 con 14 días de producción). El fan-out sólo
escribe cuando cambia Woo, pero TikTok y Temu también mueven su número solos: un
pedido sin pagar aparta piezas y, si se cancela, el canal las DEVUELVE encima de lo
que el fan-out ya había escrito. Nada lo corregía hasta el siguiente movimiento del
SKU:
  · Temu quedó por encima de Woo 51 veces (353 pzs de más, 37 h en promedio), 8 con
    Woo en 0; TikTok, 4 veces.
  · DEC-0078-PLA: Woo en 0 desde el 19-sep; Temu subió solo a 33 y a 39 y el 24-sep
    alguien compró 39. Se surtió hasta que llegó mercancía (salida del 1-oct).
  · MASC-0057-ROJ: Temu volvió a «a la venta» con 1 y Woo en 0; se vendió el 29-sep.
Los censos sí lo veían («cambió el canal» en la matriz), pero sólo lo anotaban.

QUÉ HACE. Al terminar cada censo de TikTok o Temu toma las publicaciones de ese canal
cuyo stock recién leído supera la foto de Woo de stock_watch —las de poco stock
primero— y a cada una le aplica `fanout_stock.bajar`: recalcula el plan con Woo EN
VIVO y, si el canal sigue arriba del objetivo, le escribe el objetivo SÓLO a ese
canal. El escritor vuelve a leer el canal en vivo y se niega a subir.

POR QUÉ SÓLO BAJAR. Un canal por DEBAJO de Woo suele ser un pedido sin pagar que el
canal apartó: escribirle el número de Woo liberaría esa pieza. Por ENCIMA, bajar
siempre es seguro: lo peor es ofrecer de menos hasta el siguiente movimiento.

Mismas reglas que el fan-out (destinos, borradores de Temu, FANOUT_CANALES,
FANOUT_TIKTOK / FANOUT_TEMU, FANOUT_RESERVA, FANOUT_DRY_RUN) y la misma bitácora
(`ops.fanout_log`, motivo `excedente:<canal>`).

BANDERA: FANOUT_EXCEDENTES_ENABLED (nace apagada, regla 3). FANOUT_EXCEDENTES_TOPE
publicaciones por censo. Qué tan seguido se mira lo da el censo de cada canal
(TIKTOK_CENSO_MIN, TEMU_CENSO_MIN).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.fanout_excedentes")

CANALES = ("tiktok", "temu")
_ultimo: dict[str, dict[str, Any]] = {}


def habilitado() -> bool:
    return bool(getattr(settings, "fanout_excedentes_enabled", False))


def _tope() -> int:
    return max(1, int(getattr(settings, "fanout_excedentes_tope", 100) or 100))


def candidatos(canal: str, limite: int) -> list[dict[str, Any]]:
    """Publicaciones de `canal` cuyo stock (el que leyó el censo) supera la foto de
    Woo. Primero las de poco stock (Woo ≤ 2), después las de mayor excedente."""
    from services import supabase_db as sdb
    return sdb.fetch_all(
        """select l.sku::text as sku, l.stock_own::int as canal_stock, p.stock_woo
             from channel.listings l
             join ops.stock_watch_photo p on p.sku = l.sku
            where l.canal = %(c)s and not coalesce(l.is_fulfillment, false)
              and l.stock_own is not null and p.stock_woo is not null
              and l.stock_own > greatest(p.stock_woo, 0)
            order by (p.stock_woo <= 2) desc, l.stock_own - greatest(p.stock_woo, 0) desc, l.sku
            limit %(n)s""",
        {"c": canal, "n": int(limite)})


def revisar(canal: str) -> dict[str, Any]:
    """Una vuelta para `canal`. SÍNCRONA a propósito: el censo la corre con
    `asyncio.to_thread` (regla 11: lee la base y Woo, y escribe en el canal)."""
    from services import fanout_stock
    canal = (canal or "").lower()
    if canal not in CANALES:
        return {"ok": False, "motivo": f"'{canal}' no es TikTok ni Temu"}
    if not habilitado():
        return {"ok": False, "motivo": "FANOUT_EXCEDENTES_ENABLED apagado"}
    if not fanout_stock.habilitado():
        return {"ok": False, "motivo": "el fan-out está apagado"}
    t0 = time.time()
    filas = candidatos(canal, _tope())
    cuenta: dict[str, int] = {}
    muestra: list[str] = []
    for f in filas:
        try:
            r = fanout_stock.bajar(f["sku"], canal, motivo=f"excedente:{canal}")
        except Exception as exc:  # noqa: BLE001 — una publicación no tumba la vuelta
            r = {"resultado": "error", "detalle": str(exc)[:160]}
        cuenta[r["resultado"]] = cuenta.get(r["resultado"], 0) + 1
        if r["resultado"] in ("bajado", "simulado", "error") and len(muestra) < 15:
            muestra.append(f"{f['sku']} {r.get('antes', f['canal_stock'])}→{r.get('objetivo', '?')}"
                           + (f" ({r['resultado']})" if r["resultado"] != "bajado" else ""))
    salida = {"ok": True, "canal": canal, "candidatos": len(filas), **cuenta, "muestra": muestra,
              "segundos": round(time.time() - t0, 1), "ts": time.time()}
    _ultimo[canal] = salida
    if cuenta.get("bajado") or cuenta.get("error"):
        log.warning("Excedentes de %s: %s", canal, salida)
    return salida


def estado() -> dict[str, Any]:
    return {"habilitado": habilitado(), "tope": _tope(),
            "cada_min": {"tiktok": int(getattr(settings, "tiktok_censo_min", 120)),
                         "temu": int(getattr(settings, "temu_censo_min", 240))},
            "ultima_vuelta": dict(_ultimo) or None}
