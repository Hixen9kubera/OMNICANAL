"""
orders_read.py — Lecturas del dominio PEDIDOS desde la BD kubera (F5, flag
SUPABASE_READ_ORDERS).

Gemelas de las dos consultas MySQL de ventas_ml.py sobre `pedidos_ml`
(_pedidos_horario y _pedidos_rango): devuelven FILAS con exactamente las
mismas llaves que el fetch_all de MySQL, para que la agregación en Python
sea idéntica en ambas rutas. El llamador (ventas_ml) decide la fuente por
flag y hace fallback a MySQL ante cualquier error — apagar = revertir.

Notas de traducción (MySQL → Postgres/kubera):
  - `pedidos_ml` ≡ `channel.orders` (espejo v0.16.x, histórico completo).
    cuenta≡cuenta, estado_wc≡estado_wc, total≡total, es_full≡es_fulfillment.
  - `creado` en MySQL es DATETIME naive en UTC; `creado_at` es timestamptz.
    Se compara y bucketiza SIEMPRE sobre `creado_at at time zone 'utc'`
    (naive UTC) para que el corte de rango y la hora CDMX (UTC-6 fijo)
    coincidan con HOUR(DATE_SUB(creado, INTERVAL 6 HOUR)).
  - 0 filas es legítimo (rango sin ventas, p. ej. la semana previa al inicio
    del registro): aquí NO hay guardia de plausibilidad por conteo.
"""
from __future__ import annotations

from datetime import datetime

from services import supabase_db as sdb


def horario(cuentas: list[str], ini: datetime, fin: datetime) -> list[dict]:
    """Filas {h, cuenta, estado_wc, n, m} — gemela del SELECT de _pedidos_horario.
    ini/fin llegan naive en UTC (igual que a MySQL)."""
    return sdb.fetch_all(
        """select extract(hour from ((o.creado_at at time zone 'utc')
                                     - interval '6 hours'))::int as h,
                  o.cuenta, o.estado_wc, count(*) as n, sum(o.total) as m
             from channel.orders o
            where o.cuenta = any(%s)
              and (o.creado_at at time zone 'utc') >= %s
              and (o.creado_at at time zone 'utc') < %s
            group by 1, 2, 3""",
        (list(cuentas), ini, fin))


def rango(cuentas: list[str], ini: datetime, fin: datetime) -> list[dict]:
    """Filas {cuenta, estado_wc, n, m, f} — gemela del SELECT de _pedidos_rango."""
    return sdb.fetch_all(
        """select o.cuenta, o.estado_wc, count(*) as n, sum(o.total) as m,
                  sum((o.es_fulfillment)::int) as f
             from channel.orders o
            where o.cuenta = any(%s)
              and (o.creado_at at time zone 'utc') >= %s
              and (o.creado_at at time zone 'utc') < %s
            group by o.cuenta, o.estado_wc""",
        (list(cuentas), ini, fin))
