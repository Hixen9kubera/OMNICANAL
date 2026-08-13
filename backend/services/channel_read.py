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

# `tiktok` entra el 13-ago con las 900 publicaciones del censo. Con esto sus
# puntos aparecen en la vista General (`presencia`) y su precio/stock enriquece
# el listado, igual que ML y Amazon.
#
# Para el FAN-OUT no cambia nada aunque sus filas ahora se vean: `_ESCRITORES`
# no tiene escritor de TikTok y `FANOUT_CANALES` no lo incluye, así que sus
# destinos se OMITEN con motivo escrito ("sin escritor implementado") en vez de
# recibir stock. Verificado antes de agregarlo aquí.
CANALES = ("mercado_libre", "amazon", "tiktok")
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


# ── Categoría ML curada (paso 0, 12-ago-2026) ───────────────────────────────

def categoria_curada(sku: str) -> dict[str, str] | None:
    """
    Gemela de `crear_producto._categoria_curada`, que leía `categorias_ml`.

    El mapa vive en `channel.product_category` y el NOMBRE en
    `channel.categories` (2,116 de 2,116 ids en uso tienen nombre). Se busca
    por SKU exacto y, si no, por PREFIJO PADRE, igual que el original.

    `fuente` traduce `source`: 'panel' es la elección HUMANA y por regla de la
    casa manda sobre cualquier detector. Ese matiz no es cosmético — kubera y
    el viejo `categorias_ml` discrepan en 2,270 SKUs y en todos los muestreados
    MySQL traía `predictor` contra el `panel` de kubera. Publicar desde MySQL
    era publicar en la categoría que ADIVINÓ el detector, ignorando la que un
    humano ya había corregido.
    """
    if not sku:
        return None
    base = "-".join(sku.split("-")[:2])
    row = sdb.fetch_one(
        """select pc.category_id, ct.name as category_name, pc.source
             from channel.product_category pc
             left join channel.categories ct
                    on ct.category_id = pc.category_id
                   and ct.channel_id = pc.channel_id
            where pc.channel_id = 'mercado_libre'
              and nullif(pc.category_id, '') is not null
              and (pc.sku = %(s)s::citext or pc.sku::text ilike %(p)s)
            order by (pc.sku = %(s)s::citext) desc,
                     (pc.source = 'panel') desc
            limit 1""",
        {"s": sku, "p": base + "%"})
    if not (row and row.get("category_id")):
        return None
    return {"category_id": str(row["category_id"]),
            "category_name": str(row.get("category_name") or ""),
            "fuente": str(row.get("source") or "")}


def skus_de_categoria(category_id: str) -> list[str]:
    """SKUs mapeados a una categoría ML, ordenados (gemela del SELECT sobre
    `categorias_ml` de competencia_captura)."""
    return [str(r["sku"]) for r in sdb.fetch_all(
        """select sku::text as sku from channel.product_category
            where channel_id = 'mercado_libre' and category_id = %s
            order by sku""", (category_id,))]


def skus_por_categorias(ids: list[str]) -> dict[str, list[str]]:
    """{ category_id: [sku, …] } para varios nichos de una sola consulta."""
    salida: dict[str, list[str]] = {c: [] for c in ids}
    if not ids:
        return salida
    for r in sdb.fetch_all(
        """select category_id, sku::text as sku from channel.product_category
            where channel_id = 'mercado_libre' and category_id = any(%s)
            order by sku""", (list(ids),)):
        salida.setdefault(r["category_id"], []).append(str(r["sku"]))
    return salida


def categorias_de(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    { sku: {category_id, category_name, ruta, cat1..cat4} } — gemela de las
    columnas de `categorias_ml` que consume competencia_captura.

    `ruta` es el `path` de `channel.categories` y cat1..cat4 son sus tramos: la
    tabla vieja guardaba el camino ya partido, aquí se parte al leer para no
    duplicar el dato.
    """
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        idx = {s.lower(): s for s in chunk}
        for r in sdb.fetch_all(
            """select pc.sku::text as sku, pc.category_id,
                      ct.name as category_name, ct.path
                 from channel.product_category pc
                 left join channel.categories ct
                        on ct.category_id = pc.category_id
                       and ct.channel_id = pc.channel_id
                where pc.channel_id = 'mercado_libre'
                  and pc.sku = any(%s::citext[])
                  and nullif(pc.category_id, '') is not null""", (chunk,)):
            tramos = [t.strip() for t in str(r.get("path") or "").split("›") if t.strip()]
            salida[idx.get(r["sku"].lower(), r["sku"])] = {
                "category_id": r["category_id"],
                "category_name": r.get("category_name") or "",
                "ruta": r.get("path") or "",
                "cat1": tramos[0] if len(tramos) > 0 else None,
                "cat2": tramos[1] if len(tramos) > 1 else None,
                "cat3": tramos[2] if len(tramos) > 2 else None,
                "cat4": tramos[3] if len(tramos) > 3 else None,
            }
    return salida


def stock_fba_amazon() -> dict[str, int]:
    """
    { sku: stock_fba } de Amazon — la SEMILLA del vigilante de FBA.

    No es un dato de adorno: es la referencia contra la que se compara el
    inventario que devuelve Amazon. Una semilla vieja no da un error, da una
    ALERTA FANTASMA ("FBA bajó de 40 a 12") sobre un movimiento que nunca pasó.
    Medido antes de repuntar: 1,790 SKUs aquí contra 1,680 en el espejo, cero
    con valor distinto.
    """
    return {str(r["sku"]): int(r["fba"] or 0) for r in sdb.fetch_all(
        """select l.sku::text as sku, coalesce(l.stock_fba, 0) as fba
             from channel.listings l
            where l.canal = 'amazon'""")}


def no_full(limite: int) -> list[dict[str, Any]]:
    """
    Publicaciones NO-FULL (las que sí salen de nuestra bodega), para el plan de
    sincronización. Gemela del SELECT sobre `canal_inventario`.

    Deja fuera el canal 'general' A PROPÓSITO, aunque el SELECT viejo lo
    nombrara: en kubera 'general' es el catálogo Woo COMPLETO (13,092 filas)
    mientras `canal_inventario` solo tenía 21 legadas. No son la misma cosa, y
    además Woo es la FUENTE del stock, no un destino al que empujarlo — ese
    camino es `sync_woo.py`. Incluirlo multiplicaría el plan por cuatro con
    filas que nadie pidió sincronizar.
    """
    return sdb.fetch_all(
        f"""select l.sku::text as sku, l.canal,
                   case when a.legacy_code in ('AMAZON','GENERAL')
                        then '' else a.legacy_code end as cuenta,
                   l.listing_id as item_id, l.stock_own as stock_real,
                   0 as es_full
              from channel.listings l
              join core.accounts a on a.id = l.account_id
             where coalesce(l.is_fulfillment, false) = false
               and l.canal in ('mercado_libre', 'amazon')
             limit %s""", (limite,))


def vistos_amazon() -> dict[str, Any]:
    """
    { sku: updated_at (naive UTC) } de las publicaciones Amazon — gemela de
    `vistos_ml` para el otro canal.

    Su uso NO es informativo: es el turno del barrido progresivo. Ver la nota
    en `inventario._lote_amazon`.
    """
    out: dict[str, Any] = {}
    for f in sdb.fetch_all(
        """select l.sku::text as sku, l.updated_at
             from channel.listings l
            where l.canal = 'amazon'"""):
        ts = f.get("updated_at")
        out[str(f["sku"])] = (
            ts.astimezone(timezone.utc).replace(tzinfo=None) if ts else None)
    return out
