"""
channel_read.py — Lecturas del dominio CHANNEL desde la BD kubera (F5, flag
SUPABASE_READ_CHANNEL). Gemelas de las lecturas de panel sobre canal_inventario:

  leer_inventario(skus)   ≡ inventario.leer_inventario  (dict sku → canal|cuenta → fila)
  presencia(skus)         ≡ query de presencia.py       (filas sku/canal/cuenta/item_id/situacion)
  resumen_por_canal()     ≡ GET /api/sync/estado        (agregados por canal+cuenta)

Traducción canal_inventario → channel.listings:
  item_id→listing_id · precio→price · stock_real→stock_own · es_full→is_fulfillment
  logistica→logistic_type · moneda→currency (columnas añadidas en la migración 0004)
  cuenta ↔ core.accounts.legacy_code, con la convención MySQL de cuenta vacía
  para amazon/general (AMAZON/GENERAL → '').

ALCANCE: canales 'mercado_libre' y 'amazon'. El canal 'general' NO viaja por
estas gemelas: en kubera listings 'general' es el catálogo Woo completo del
ETL de fusión (~13k filas) mientras canal_inventario solo tiene ~20 filas
legadas — no son equivalentes; 'general' se unifica en el corte (F6).
"""
from __future__ import annotations

from datetime import timezone
from typing import Any

from services import supabase_db as sdb

CANALES = ("mercado_libre", "amazon")
_SEL = """
    select l.sku, l.canal,
           case when a.legacy_code in ('AMAZON','GENERAL') then '' else a.legacy_code end as cuenta,
           l.listing_id as item_id, l.price as precio, l.stock_own as stock_real,
           -- convención MySQL: amazon reporta en stock_fba y deja stock_full NULL
           -- (el espejo históricamente fusionó fba→stock_full; se normaliza aquí)
           case when l.canal = 'amazon' then null else l.stock_full end as stock_full,
           l.stock_fba,
           (case when l.is_fulfillment then 1 else 0 end) as es_full,
           l.logistic_type as logistica, l.situacion, l.currency as moneda,
           l.updated_at
      from channel.listings l
      join core.accounts a on a.id = l.account_id
"""


def hay_datos() -> bool:
    """
    ¿El dominio tiene algo en kubera? Sirve para distinguir las dos razones por
    las que una lectura puede volver vacía:
      · el lote consultado no tiene publicaciones (NORMAL: ~91% de los productos
        no viven en ML/Amazon, y una ficha de producto pide un solo SKU), y
      · kubera perdió la tabla (lo que la guardia de plausibilidad busca).
    """
    return bool(sdb.fetch_all(
        "select 1 from channel.listings where canal = any(%s) limit 1",
        (list(CANALES),)))


def leer_inventario(skus: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    if not skus:
        return {}
    # se excluyen los "fantasmas" del ETL de fusión (filas-identidad con TODO
    # en NULL, sin equivalente en canal_inventario); las filas reales sin
    # item_id (nunca publicadas) SÍ viajan, igual que en MySQL
    rows = sdb.fetch_all(
        _SEL + """ where l.canal = any(%s) and l.sku = any(%s::citext[])
                   and not (nullif(l.listing_id,'') is null and l.situacion is null
                            and l.price is null and l.stock_own is null
                            and l.logistic_type is null)""",
        (list(CANALES), list(skus)))
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        clave = f"{r['canal']}|{r.get('cuenta') or ''}"
        out.setdefault(str(r["sku"]), {})[clave] = {**r, "sku": str(r["sku"])}
    return out


def presencia(skus: list[str]) -> list[dict[str, Any]]:
    if not skus:
        return []
    rows = sdb.fetch_all(
        _SEL + """ where l.canal = any(%s) and l.sku = any(%s::citext[])
                   and l.listing_id is not null and l.listing_id <> ''""",
        (list(CANALES), list(skus)))
    return [{"sku": str(r["sku"]), "canal": r["canal"], "cuenta": r["cuenta"],
             "item_id": r["item_id"], "situacion": r["situacion"]} for r in rows]


def resumen_por_canal() -> list[dict[str, Any]]:
    return sdb.fetch_all("""
        select l.canal,
               case when a.legacy_code in ('AMAZON','GENERAL') then '' else a.legacy_code end as cuenta,
               count(*) as skus,
               max(l.updated_at) as ultima_actualizacion,
               sum(l.stock_full) as total_full, sum(l.stock_fba) as total_fba,
               sum(l.stock_own) as total_real
          from channel.listings l
          join core.accounts a on a.id = l.account_id
         where l.canal = any(%s)
           and not (nullif(l.listing_id,'') is null and l.situacion is null
                    and l.price is null and l.stock_own is null
                    and l.logistic_type is null)
         group by 1, 2""", (list(CANALES),))


# ── Gemelas del SYNC de ML (repunte del paso 0, 12-ago-2026) ──────────────────
# `_lote_desde_ml` decidía la rotación y el barrido de cierre leyendo
# `canal_inventario`. Con el espejo congelado el barrido NUNCA se auto-termina:
# las filas siguen 'active' en MySQL y vuelven a colarse en cada ronda. Estas
# dos leen de donde el sync ESCRIBE hoy (channel.listings).
#
# Las fechas vuelven NAIVE en UTC a propósito: el llamador ordena comparando
# contra datetime(1970,1,1), y mezclar aware con naive revienta el sort.

def vistos_ml(cuenta: str) -> dict[str, Any]:
    """{ item_id: updated_at (naive UTC) } de las publicaciones ML de la cuenta."""
    filas = sdb.fetch_all(
        """select l.listing_id as item_id, l.updated_at
             from channel.listings l
             join core.accounts a on a.id = l.account_id
            where l.canal = 'mercado_libre' and a.legacy_code = %(c)s
              and nullif(l.listing_id, '') is not null""",
        {"c": cuenta})
    out: dict[str, Any] = {}
    for f in filas:
        ts = f.get("updated_at")
        out[str(f["item_id"])] = (
            ts.astimezone(timezone.utc).replace(tzinfo=None) if ts else None)
    return out


def vivas_ml(cuenta: str) -> list[str]:
    """item_id de las publicaciones ML que el registro cree VIVAS (active/paused).

    Es el insumo del barrido de cierre: lo que está aquí y ya no está en el
    catálogo de ML necesita que se le lea su estado final.
    """
    return [str(f["item_id"]) for f in sdb.fetch_all(
        """select l.listing_id as item_id
             from channel.listings l
             join core.accounts a on a.id = l.account_id
            where l.canal = 'mercado_libre' and a.legacy_code = %(c)s
              and lower(l.situacion) in ('active', 'paused')
              and nullif(l.listing_id, '') is not null""",
        {"c": cuenta})]
