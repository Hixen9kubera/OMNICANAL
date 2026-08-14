"""
publicacion_seam.py — El acta de nacimiento de una publicación llega a kubera en
el momento, no dentro de 15 minutos.

EL HUECO QUE CIERRA (medido el 14-ago-2026)
-------------------------------------------
Hoy la cadena es ésta:

    publicar  →  ml_progress / amazon_progress  (MySQL)
                            ↓  sync de 15 min (inventario.py)
                     channel.listings  (kubera)

El publicador **no tiene seam a `channel.listings`**: escribe MySQL y nada más.
Kubera se entera hasta el siguiente sync, y ese sync lee de `ml_progress`.

Por eso `ml_progress` NO es "la bitácora del publicador" que decía el plan: es el
**acta de nacimiento** de la publicación, y durante hasta 15 minutos es lo ÚNICO
que sabe que ese listing existe. Ésa es la razón por la que `presencia.py`
consulta `ml_progress` después de `channel.listings` — no es redundancia, es la
red de lo recién publicado.

Mientras ese hueco exista, repuntar cualquiera de los 25 lectores del grupo 4
cambia "publicado hace 30 segundos" por "sin publicar". Este módulo es el
requisito previo, igual que el seam Crear → `core.products` lo fue para el corte
de `core`.

NACE APAGADO (`SUPABASE_SEAM_PUBLICAR=false`)
---------------------------------------------
Encenderlo mete una escritura en el flujo de PUBLICAR, que es negocio vivo:
regla 3, va con el dale de Brandon. Apagado, este módulo es un no-op y el
publicador se comporta exactamente como hoy.

QUÉ ESCRIBE, Y QUÉ **NO** ESCRIBE A PROPÓSITO
---------------------------------------------
Solo lo que el publicador SABE en ese instante:

  · ML     → `listing_id` (el MLM) y `url`.
  · Amazon → `status` y `product_type`. **No `listing_id`**: al publicar, el ASIN
             todavía no existe (verificado: los 1,791 registros de
             `amazon_progress` que nacieron así tienen `asin` NULL).

Y sobre todo, lo que NO toca:

  · **`is_fulfillment` jamás.** El upsert del sync lo escribe SIN `coalesce`
    (`is_fulfillment = excluded.is_fulfillment`), así que pasar un `false` por
    "no sé" apagaría el FULL de una publicación que sí lo es. Al republicar un
    SKU reciclado —el playbook de v0.15.0— eso sería un dato falso sobre dónde
    está la mercancía.
  · Precio, stock, situación y logística: los observa el sync contra el
    marketplace. El publicador solo sabe que la publicación nació.

Todo lo demás viaja NULL y el `coalesce` del upsert conserva lo que hubiera.

BEST-EFFORT **MIENTRAS SEA ESPEJO**
-----------------------------------
Un fallo aquí NO tumba la publicación: MySQL sigue siendo la fuente y el sync
alcanza en 15 min, así que perder este aviso degrada la frescura, no la verdad.
Por eso el `except` de abajo es legítimo — y por eso queda anotado en
`ops.migration_issues` en vez de tragarse en silencio.

**El día que se repunten los lectores esto se invierte** y este `except` tiene
que morir: cuando `channel.listings` sea lo único que conteste "¿está
publicado?", un fallo silencioso aquí es una publicación invisible. Es la misma
trampa de los candados del paso 0, y está escrita aquí para que no se descubra
después.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings
from services import channel_mirror
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.publicacion_seam")


def activo() -> bool:
    return bool(getattr(settings, "supabase_seam_publicar", False)) and sdb.disponible()


def _escribir(canal: str, cuenta: str, sku: str, listing_id: str | None,
              url: str | None, status: str | None, product_type: str | None) -> None:
    """BLOQUEANTE. Solo se llama desde un hilo (ver `registrar`)."""
    cuenta_id = channel_mirror._cuenta_uuid(canal, cuenta)
    if not cuenta_id:
        channel_mirror._registrar_issue(
            sku, f"seam publicar: cuenta sin uuid canal={canal} cuenta={cuenta}")
        return
    with sdb.get_cursor() as cur:
        # La vía identifica QUIÉN escribió para el trigger de historia. 'publicar'
        # y no 'sync': si mañana una publicación aparece con datos raros, la
        # historia tiene que decir que la trajo el publicador y no el barrido.
        cur.execute("select set_config('app.via', 'publicar', true)")
        # Identidad primero, igual que el resto de los escritores (regla 4): un
        # SKU que el maestro no conozca se registra solo en vez de perderse.
        cur.execute(
            """insert into core.products (sku, status, source)
               values (%s, 'draft', 'publicador')
               on conflict (sku) do nothing""", (sku,))
        cur.execute(
            """insert into channel.listings
                 (sku, account_id, canal, listing_id, url, status, product_type)
               values (%s, %s, %s, %s, %s, %s, %s)
               on conflict (sku, account_id, canal) do update set
                 listing_id   = coalesce(excluded.listing_id, listings.listing_id),
                 url          = coalesce(excluded.url, listings.url),
                 status       = coalesce(excluded.status, listings.status),
                 product_type = coalesce(excluded.product_type, listings.product_type)
               where (listings.listing_id, listings.url, listings.status,
                      listings.product_type)
                 is distinct from
                     (coalesce(excluded.listing_id, listings.listing_id),
                      coalesce(excluded.url, listings.url),
                      coalesce(excluded.status, listings.status),
                      coalesce(excluded.product_type, listings.product_type))""",
            (sku, cuenta_id, canal, listing_id or None, url or None,
             status or None, product_type or None))


def registrar(canal: str, cuenta: str, sku: str, *, listing_id: str | None = None,
              url: str | None = None, status: str | None = None,
              product_type: str | None = None) -> None:
    """Avisa a kubera que este SKU acaba de publicarse. No-op si el flag está off.

    Se despacha a un hilo: el publicador es `async` y psycopg2 bloquea el loop
    entero, no solo a quien llama (regla 11 — el apagón del 13-ago). Fuera de un
    loop corriendo (scripts) se ejecuta en línea.
    """
    if not activo() or not (sku or "").strip():
        return

    def _trabajo() -> None:
        try:
            _escribir(canal, cuenta, str(sku).strip(), listing_id, url,
                      status, product_type)
            log.info("seam publicar → kubera: %s %s/%s item=%s",
                     sku, canal, cuenta or "-", listing_id or "-")
        except Exception as exc:  # noqa: BLE001 — ver "BEST-EFFORT" arriba
            log.warning("seam publicar falló (%s %s): %s", sku, canal, exc)
            channel_mirror._registrar_issue(sku, f"seam publicar fallo: {exc}")

    try:
        asyncio.get_running_loop().run_in_executor(None, _trabajo)
    except RuntimeError:
        _trabajo()


def estado() -> dict[str, Any]:
    """Para /migracion y el arnés: ¿está encendido y qué ha traído?"""
    d: dict[str, Any] = {"activo": activo()}
    if not sdb.disponible():
        return d
    try:
        d.update(sdb.fetch_one(
            """select count(*) as cambios_via_publicador,
                      count(distinct sku) as skus,
                      max(changed_at) as ultimo
                 from channel.listing_history
                where detectado_via = 'publicar'""") or {})
    except Exception as exc:  # noqa: BLE001
        d["error"] = str(exc)[:200]
    return d
