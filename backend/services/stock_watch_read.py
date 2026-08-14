"""
stock_watch_read.py — La foto del vigilante de inventario, en la BD kubera.

Gemela de las tres consultas que `stock_watch.py` y `channel_mirror.py` hacen
contra `stock_watch_foto` (MySQL). Traducción de nombres:

    stock_watch_foto  →  ops.stock_watch_photo

Cada función devuelve EXACTAMENTE la misma forma que su gemela MySQL, para que
el llamador no cambie más que la línea de la consulta.

TRES COSAS QUE NO SON COSMÉTICAS
--------------------------------

1. **`foto_leer()` NO se traga los errores.** Su gemela MySQL tenía
   `except Exception: return {}`, y ahí ese `{}` no significaba "no hay foto":
   significaba "no sé". El vigilante lo leía como "primera pasada" y la primera
   pasada ABSORBE en la foto todo lo pendiente sin aplicarlo — o sea que un
   parpadeo de la base tiraba a la basura, en silencio, los deltas de Odoo y los
   cambios de Woo de esa vuelta. Es la misma familia del `None` de los 964
   pedidos fantasma. Aquí el error PROPAGA y la pasada se aborta: no hacer nada
   es correcto, hacerlo con datos inventados no.

   La foto genuinamente vacía (primera corrida de todas) sigue devolviendo `{}`.
   Vacío y roto son cosas distintas y ahora se distinguen.

2. **Todo aquí es BLOQUEANTE** (psycopg2). Ninguna de estas funciones se llama
   desde una corrutina sin `asyncio.to_thread` — regla 11 de la casa, nacida del
   apagón del 13-ago. `stock_watch.revisar` ya envuelve sus llamadas; al
   repuntarlo hay que conservar el envoltorio.

3. **`actualizado` se reescribe en CADA pasada**, toque o no el número. Es la
   señal de vida del vigilante, no un "cuándo cambió". Se conserva idéntico a
   MySQL a propósito: ahorrar esas escrituras con un `where ... is distinct
   from` convertiría la columna en otra cosa, y esa confusión exacta
   (`channel.listings.updated_at`) ya produjo tres diagnósticos equivocados
   seguidos.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services import supabase_db as sdb


def foto_leer() -> dict[str, dict[str, int | None]]:
    """{ sku: {"woo": n, "odoo": n} } — gemela de `stock_watch._foto`.

    Devuelve `{}` SOLO si la tabla está vacía de verdad. Si la base falla, la
    excepción sube (ver punto 1 del encabezado).
    """
    return {r["sku"]: {"woo": r["stock_woo"], "odoo": r["stock_odoo"]}
            for r in sdb.fetch_all(
                "select sku, stock_woo, stock_odoo from ops.stock_watch_photo")}


def foto_guardar(filas: list[tuple[str, int | None, int | None]],
                 ahora: datetime | None = None) -> int:
    """(sku, stock_woo, stock_odoo) → upsert de la foto completa.

    SIN `coalesce`: aquí un NULL es informativo ("Woo no gestiona stock de este
    SKU", "Odoo no lo conoce") y tiene que poder PISAR a un número anterior. Es
    lo contrario de las cachés del paso 1, donde el NULL era "no me contestaron"
    y no debía borrar un valor bueno.

    `ahora` lo pasa el llamador para que los DOS lados de la mudanza sellen la
    misma pasada con el MISMO instante. Si cada lado pusiera su propio reloj
    (`now()` de Postgres contra el `datetime.now()` de Python), el arnés de
    comparación vería una diferencia en cada fila de cada pasada y habría que
    inventarle una tolerancia — o sea, un arnés que ya no compara de verdad.
    """
    if not filas:
        return 0
    from psycopg2.extras import execute_values

    ts = ahora or datetime.now(timezone.utc)
    # El upsert va sin guardia de cambio: `actualizado` avanza en toda la tabla
    # cada pasada, igual que en MySQL (punto 3 del encabezado).
    with sdb.get_cursor() as cur:
        for i in range(0, len(filas), 1000):
            execute_values(
                cur,
                """insert into ops.stock_watch_photo
                     (sku, stock_woo, stock_odoo, actualizado) values %s
                   on conflict (sku) do update set
                     stock_woo   = excluded.stock_woo,
                     stock_odoo  = excluded.stock_odoo,
                     actualizado = excluded.actualizado""",
                [(s, w, o, ts) for s, w, o in filas[i:i + 1000]],
                template="(%s, %s, %s, %s)", page_size=1000)
    return len(filas)


def drop_leer() -> list[dict[str, Any]]:
    """[{sku, stock_woo}] con stock — gemela del SELECT de `sincronizar_drop`.

    El filtro `is not null` es el mismo de allá y por la misma razón: un SKU
    cuyo stock Woo no gestiona no se publica como 0 inventado.
    """
    return sdb.fetch_all(
        "select sku::text as sku, stock_woo from ops.stock_watch_photo "
        "where stock_woo is not null")


def senal_de_vida() -> dict[str, Any]:
    """Cuántas filas y de cuándo. Para el vigilante de congelación y el arnés."""
    return sdb.fetch_one(
        "select count(*) as filas, max(actualizado) as ultima, "
        "count(*) filter (where stock_woo is not null) as con_woo "
        "from ops.stock_watch_photo") or {}
