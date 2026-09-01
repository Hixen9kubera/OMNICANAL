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

import logging
from datetime import timezone
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.channel_read")

# `tiktok` entra el 13-ago con las 900 publicaciones del censo. Con esto sus
# puntos aparecen en la vista General (`presencia`) y su precio/stock enriquece
# el listado, igual que ML y Amazon.
#
# Para el FAN-OUT no cambia nada aunque sus filas ahora se vean: `_ESCRITORES`
# no tiene escritor de TikTok y `FANOUT_CANALES` no lo incluye, así que sus
# destinos se OMITEN con motivo escrito ("sin escritor implementado") en vez de
# recibir stock. Verificado antes de agregarlo aquí.
# temu entró el 18-ago (decisión: canal DROP-only). Verificado ANTES de
# agregarlo, igual que con tiktok: sin escritor en _ESCRITORES y con
# FANOUT_TEMU apagado, sus 352 filas solo producen "omitir" con motivo escrito
# — visibles en dry-run/simulación, cero escrituras.
CANALES = ("mercado_libre", "amazon", "tiktok", "temu")
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
           -- `estado_canal` es `status`, que NO es lo mismo que `situacion`. En
           -- TikTok `status` dice si el producto está a la venta (ACTIVATE) y
           -- `situacion` dice cómo salió de la auditoría (APPROVED/FAILED): el
           -- fan-out necesita el primero, y sin esta columna leía el segundo y
           -- descartaba todo el canal por "situación desconocida".
           l.status as estado_canal,
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


def skus_publicados_por_categorias(ids: list[str]) -> dict[str, list[str]]:
    """
    `{ category_id: [sku, …] }` pero SOLO los que tienen publicación VIVA en ML.

    ── POR QUÉ NO ALCANZA `skus_por_categorias` ────────────────────────────────
    Ésa devuelve el mapa de catálogo entero, y en Competencia eso engaña: un SKU
    que no está publicado NO compite en ese nicho, así que contarlo dice que
    estamos mejor posicionados de lo que estamos. Medido el 1-sep-2026: de los
    13,788 SKUs mapeados a alguna categoría de ML, **sólo 2,502 (18%) tienen
    publicación viva**. En Mochilas el panel decía "20 SKUs en catálogo" y el
    publicado era **uno**; en Tenis decía 363 y compiten 127.

    ── EL FILTRO ES `('active','paused')`, NO "distinta de closed" ─────────────
    Son cosas distintas y la diferencia no es teórica: hoy hay 208 publicaciones
    en `under_review`, 2 en `inactive` y 267 sin estado. "No cerrada" las metería
    a todas. Se usa el mismo filtro canónico que `competencia_visitas.objetivo()`
    y la vista de prioridad, para que las tres cuenten lo mismo.
    """
    salida: dict[str, list[str]] = {c: [] for c in ids}
    if not ids:
        return salida
    for r in sdb.fetch_all(
        """select distinct pc.category_id, pc.sku::text as sku
             from channel.product_category pc
             join channel.listings l
               on l.sku = pc.sku and l.canal = 'mercado_libre'
              and lower(l.situacion) in ('active', 'paused')
              and nullif(l.listing_id, '') is not null
            where pc.channel_id = 'mercado_libre' and pc.category_id = any(%s)
            order by pc.category_id, sku""", (list(ids),)):
        salida.setdefault(r["category_id"], []).append(str(r["sku"]))
    return salida


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


# ══════════════════════════════════════════════════════════════════════════════
# PASO 3 — Gemelas de "¿está publicado y con qué id?"
#
# Reemplazan las seis lecturas de `ml_progress` / `amazon_progress` del BLOQUE 1
# (`studio`, `presencia`, `publicar`). Ver docs/PLAN_31_TABLAS.md.
#
# SE PUEDEN REPUNTAR PORQUE EL SEAM YA FUNCIONA: medido el 16-ago, 22
# publicaciones llegaron a `channel.listings` con mediana de 2 s. Antes de eso,
# `ml_progress` era lo único que conocía una publicación recién nacida durante
# hasta 15 min, y repuntar habría convertido "publicado hace 30 s" en "sin
# publicar". El orden importaba.
#
# DOS EQUIVALENCIAS MEDIDAS, no supuestas
# ---------------------------------------
# 1. **Amazon "publicado" NO se puede leer del `listing_id`.** 268 de sus 1,606
#    publicaciones PUBLISHED no tienen ASIN: Amazon no lo asigna al publicar.
#    `channel_read.presencia()` filtra por `listing_id`, así que se las come —
#    por eso estas gemelas miran `status`.
#
#    Y hace falta MIRAR DOS COLUMNAS, no una. Medido contra los 1,791 registros
#    de `amazon_progress`:
#
#      status IN (PUBLISHED, ACCEPTED, ACTIVE)      →  50 discrepancias
#      situacion IN (BUYABLE, DISCOVERABLE, …)      → 322
#      tiene listing_id                             → 278
#      **status OR situacion**                      →   4
#
#    Los 50 del primer intento son publicaciones del 28-jul al 13-ago que en
#    kubera tienen ASIN y `situacion` BUYABLE/DISCOVERABLE pero `status` NULL:
#    las dos columnas las llenan caminos distintos —`situacion` la trae el sync
#    desde la API de Amazon, `status` venía de la bitácora del publicador— y
#    ninguna sola cubre todo. Elegir una era elegir mal.
#
#    Y las 4 que quedan NO son error: en 3 de ellas MySQL dice PUBLISHED del
#    28-jul y kubera dice `closed` del 3-ago. La bitácora congeló el EVENTO de
#    publicación; kubera vio que la publicación se cerró después. **Kubera tiene
#    razón**, igual que con los MLM republicados.
#
# 2. **ML "publicado" es tener `listing_id`**, igual que el original
#    (`ml_item_id IS NOT NULL AND <> ''`). `situacion='closed'` NO se filtra
#    aquí a propósito: el original tampoco lo hacía, y una publicación cerrada
#    SÍ existió — quien quiera distinguirlo tiene `situacion` en la respuesta.
_AMZ_PUBLICADO = ("PUBLISHED", "ACCEPTED", "ACTIVE")        # columna `status`
_AMZ_VIVA = ("BUYABLE", "DISCOVERABLE", "PUBLISHED")        # columna `situacion`


def publicaciones_ml(skus: list[str]) -> dict[str, list[dict[str, Any]]]:
    """{ sku: [{cuenta, item_id, url, situacion}] } — solo las que tienen id.

    Gemela de las tres lecturas de `ml_progress` que preguntan "¿en qué cuentas
    está publicado y con qué MLM?" (`studio.py:108`, `presencia.py:101`,
    `publicar.py:154`).
    """
    if not skus:
        return {}
    filas = sdb.fetch_all(
        """select l.sku::text as sku, a.legacy_code as cuenta,
                  l.listing_id as item_id, l.url, l.situacion
             from channel.listings l
             join core.accounts a on a.id = l.account_id
            where l.canal = 'mercado_libre'
              and l.sku = any(%s::citext[])
              and nullif(l.listing_id, '') is not null
            order by a.legacy_code""",
        ([str(s) for s in skus],))
    res: dict[str, list[dict[str, Any]]] = {}
    for f in filas:
        res.setdefault(f["sku"], []).append(
            {"cuenta": f["cuenta"], "item_id": f["item_id"],
             "url": f["url"], "situacion": f["situacion"]})
    return res


# ══════════════════════════════════════════════════════════════════════════════
# "PUBLICADO EN MERCADO LIBRE" — LA DEFINICIÓN ÚNICA (v0.281.0)
# ══════════════════════════════════════════════════════════════════════════════
# Vive aquí y SOLO aquí. La consumen los cuatro lugares que tienen que contestar
# lo mismo: la insignia de la tabla de Costos, el chip "Solo publicados en ML",
# el arranque del validador y el candado de antes de escribir. Hasta el 26-ago
# había tres redacciones distintas —una por sitio— y el panel decía "no
# publicado" de 76 SKUs que el botón sí dejaba validar y escribirles costo bajo
# el candado. Una insignia que miente sobre lo que el botón hace es peor que no
# tener insignia.
#
# Decide `situacion`, NO `status`, y esto NO es un detalle de gusto:
#
#   · `situacion` es el ciclo de vida que reporta ML (active/paused/closed/
#     under_review/inactive) y lo refresca el sync de 15 min;
#   · `status` es la bitácora de NUESTRO publicador (published/error/null) y
#     MIENTE para esta pregunta: 267 filas con `situacion is null` traen
#     `status='error'` — 138 SKUs que nunca llegaron a existir en ML—, y filtrar
#     por `status='published'` dejaría fuera 1,590 filas VIVAS cuyo `status` es
#     nulo (publicaciones que el sync descubrió, no que este backend creó).
#
# `paused` entra a propósito: una pausada conserva su listing_id y se reactiva
# con un PUT — es justo el producto al que le urge el costo bien ANTES de
# reactivarlo.
#
# `under_review` queda FUERA, y ya no hay flag para meterlo. Es el estado de una
# publicación que ML todavía está juzgando: puede terminar activa o puede no
# existir nunca. Este flujo ESCRIBE —deja el costo bajo el candado de COSTO
# VALIDADO, que después nadie mueve sin liberarlo a mano—, así que ante la duda
# se elige lo conservador; y sobre todo, es lo que el panel LLAMA publicado.
# Poder marcar una casilla y validar 76 SKUs que la insignia de al lado declara
# no publicados es justo el defecto que esto cierra. Si algún día hay que
# incluirlos, se cambia esta tupla y cambian los cuatro sitios a la vez.
#
# Y ojo: esto NO es lo mismo que `publicaciones_ml`, que sigue arriba SIN filtrar
# situación y NO se toca. Aquella contesta "¿en qué cuentas existe/existió este
# SKU y con qué MLM?" —una pregunta de identidad, donde una cerrada sigue
# contando— y devuelve `situacion` para que el llamador distinga. Ésta contesta
# "¿puedo escribirle el costo hoy?". Son dos preguntas distintas y por eso dan
# números distintos: 2,603 contra 2,524, medido el 26-ago-2026.
#
# Universo medido ese día: active 692 · active+paused 2,524 · +under_review
# 2,600, y 76 SKUs cuyo ÚNICO listing es `under_review`. `closed` (14) y
# `situacion is null` quedan siempre fuera.
_SIT_PUBLICADO_ML = ("active", "paused")


def sql_publicado_ml(col_sku: str = "p.sku") -> str:
    """
    La MISMA definición, escrita como ``exists (…)`` para quien no puede llamar
    a :func:`publicados_ml` porque necesita filtrar y pintar dentro de un solo
    SELECT paginado (la tabla de Costos: `costing_read.listado`).

    Es una SUBCONSULTA y no un join a propósito: hay SKUs con dos publicaciones
    de ML (3,742 filas `paused` sobre 2,296 SKUs) y la tabla tiene que seguir
    dando una fila por SKU.

    Las situaciones se interpolan como literales porque salen de una constante
    NUESTRA, no del cliente; `col_sku` es el nombre de columna del llamador y por
    eso también es código, no dato.
    """
    sits = ", ".join(f"'{s}'" for s in _SIT_PUBLICADO_ML)
    return f"""exists (
    select 1 from channel.listings l
     where l.sku = {col_sku}
       and l.canal = 'mercado_libre'
       and nullif(l.listing_id, '') is not null
       and lower(coalesce(l.situacion, '')) in ({sits}))"""


def publicados_ml(skus: list[str]) -> dict[str, list[dict[str, Any]]]:
    """
    ``{ sku: [{cuenta, item_id, url, situacion}] }`` de los SKUs VIVOS en ML.

    Se diferencia de :func:`publicaciones_ml` en que aquí "publicado" quiere
    decir *vivo hoy*, no *tiene id*: es el filtro que decide qué SKUs puede tocar
    el validador de costos, y por eso excluye `closed`, `under_review` y las
    filas fantasma.

    Un SKU que no aparezca en el resultado NO está publicado en ML — y esa es la
    regla que el endpoint usa para rechazarlo. La llave del diccionario respeta
    la que mandó el llamador (kubera usa citext; el cruce de vuelta, no).
    """
    if not skus:
        return {}
    sits = list(_SIT_PUBLICADO_ML)
    res: dict[str, list[dict[str, Any]]] = {}
    limpios = [str(s).strip() for s in skus if str(s or "").strip()]
    for i in range(0, len(limpios), 800):      # mismo lote que el resto del módulo
        chunk = limpios[i:i + 800]
        idx = {s.lower(): s for s in chunk}
        filas = sdb.fetch_all(
            """select l.sku::text as sku, a.legacy_code as cuenta,
                      l.listing_id as item_id, l.url, l.situacion
                 from channel.listings l
                 join core.accounts a on a.id = l.account_id
                where l.canal = 'mercado_libre'
                  and l.sku = any(%s::citext[])
                  and nullif(l.listing_id, '') is not null
                  and lower(coalesce(l.situacion, '')) = any(%s)
                order by a.legacy_code""",
            (chunk, sits))
        for f in filas:
            llave = idx.get(f["sku"].lower(), f["sku"])
            res.setdefault(llave, []).append(
                {"cuenta": f["cuenta"], "item_id": f["item_id"],
                 "url": f["url"], "situacion": f["situacion"]})
    return res


def frescura_listings_ml() -> str | None:
    """
    ``max(updated_at)`` de las filas de ML, en ISO, o ``None``.

    La alimenta el sync de 15 min (`SYNC_ENABLED`). Si ese flag se apaga —ya
    pasó del 17 al 20-jul y congeló la observación 3 días— el filtro de
    publicados empieza a contestar con una foto vieja, y una foto detenida
    contesta con seguridad lo que ya no sabe. Por eso el pronóstico la enseña.
    """
    fila = sdb.fetch_one(
        "select max(updated_at) as ultimo from channel.listings "
        " where canal = 'mercado_libre'")
    ultimo = (fila or {}).get("ultimo")
    return ultimo.isoformat() if ultimo else None


def estado_amazon(skus: list[str]) -> dict[str, dict[str, Any]]:
    """{ sku: {publicado, asin, status, product_type} } — gemela de las tres
    lecturas de `amazon_progress` (`studio.py:124`, `presencia.py:119`,
    `publicar.py:259`).

    `publicado` sale del STATUS y no del ASIN: ver la nota de arriba, 268
    publicaciones vivas no tienen ASIN.
    """
    if not skus:
        return {}
    return {
        f["sku"]: {
            # LAS DOS COLUMNAS: ninguna sola cubre el catálogo (ver la nota
            # de arriba — mirar solo `status` dejaba 50 publicaciones vivas
            # marcadas como no publicadas).
            "publicado": (str(f["status"] or "").upper() in _AMZ_PUBLICADO
                          or str(f["situacion"] or "").upper() in _AMZ_VIVA),
            "situacion": f["situacion"],
            "asin": f["item_id"] or None,
            "status": f["status"],
            "product_type": f["product_type"],
        }
        for f in sdb.fetch_all(
            """select l.sku::text as sku, l.listing_id as item_id,
                      l.status, l.situacion, l.product_type
                 from channel.listings l
                where l.canal = 'amazon' and l.sku = any(%s::citext[])""",
            ([str(s) for s in skus],))
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PASO 3 · BLOQUE 2 — las REJILLAS de Mercado Libre y Amazon
# ═══════════════════════════════════════════════════════════════════════════
# Gemelas de `meli.listar` / `meli.contar_publicados` y sus dos equivalentes en
# `amazon.py`: los 7 sitios del bloque 2. A diferencia del bloque 1 —consultas
# puntuales— aquí lo que se repunta es la TABLA PAGINADA del panel, con su
# búsqueda, sus filtros, su orden y su conteo.
#
# DEVUELVEN FILAS CON LAS LLAVES DE MySQL, a propósito. Así `_normalizar()` de
# cada servicio se reusa SIN TOCAR y el contrato con el frontend no se mueve:
# lo único que cambia es de dónde salieron los datos.
#
# LO QUE MEJORA, MEDIDO (19-ago)
# ------------------------------
# El `LEFT JOIN productos` de las consultas viejas ya no cubría la rejilla:
#
#     nombre desde `productos` (MySQL)    ML  65%   ·  Amazon  69%
#     nombre desde `core.products`        ML  99%   ·  Amazon 100%
#     SKUs que SOLO conoce MySQL           0        ·  0
#
# O sea que un tercio de las filas salía hoy sin nombre, mostrando el SKU pelón.
# El repunte no es neutral: tapa ese hueco.
#
# LO QUE CAMBIA DE SIGNIFICADO, Y HAY QUE SABERLO
# -----------------------------------------------
# La columna `stock` de la rejilla venía de `productos.stock_odoo`, la foto del
# vigilante de Odoo. **Esa columna no tiene casa en kubera** (es la decisión
# pendiente de `odoo_watch`), así que aquí sale de `channel.listings.stock_own`.
#
# No es un parche: es más correcto. La rejilla lista PUBLICACIONES de un canal,
# y el stock que importa ahí es el del canal —lo que se está ofreciendo—, no el
# del almacén de un sistema en retiro que además solo cubría 1,251 de los 1,798
# SKUs de Amazon. Pero es un cambio VISIBLE y por eso está escrito aquí.
#
# LO QUE SE CAE SOLO
# ------------------
# `p.categorias` se seleccionaba y **nadie lo usaba** (`_normalizar` lo tira).
# Y el `LEFT JOIN costos_finales` era vestigial desde el paso 0: el precio y la
# categoría se pisan después con `_con_precio_kubera`, que ya lee kubera. Aquí
# se leen de una vez, en la misma consulta.

_PUB_ML = ("nullif(l.listing_id,'') is not null "
           "and lower(coalesce(l.situacion,'')) <> 'closed'")
_PUB_AMZ = ("upper(coalesce(l.status,'')) = any(%(pub)s) "
            "or upper(coalesce(l.situacion,'')) = any(%(viva)s)")

_REJILLA_ML = """
    select l.sku::text                       as sku,
           p.wc_id                           as wc_id,
           p.odoo_id                         as odoo_id,
           p.name                            as nombre,
           cf.precio_sugerido                as precio,
           cf.precio_base                    as precio_base,
           cf.ml_cat_id                      as ml_cat_id,
           l.stock_own                       as stock_odoo,
           a.legacy_code                     as cuenta,
           l.listing_id                      as ml_item_id,
           l.url                             as ml_url,
           (nullif(l.listing_id,'') is not null
            and lower(coalesce(l.situacion,'')) <> 'closed') as publicado
      from channel.listings l
      join core.accounts a on a.id = l.account_id
      left join core.products p on p.sku = l.sku
      left join costing.costos_finales cf
             on cf.sku = l.sku and cf.canal = 'mercado_libre'
     where l.canal = 'mercado_libre'
"""

_REJILLA_AMZ = """
    select l.sku::text                       as sku,
           p.wc_id                           as wc_id,
           p.odoo_id                         as odoo_id,
           p.name                            as nombre,
           cf.precio_sugerido                as precio,
           l.stock_own                       as stock_odoo,
           l.listing_id                      as asin,
           l.product_type                    as product_type,
           l.status                          as status,
           (upper(coalesce(l.status,'')) = any(%(pub)s)
            or upper(coalesce(l.situacion,'')) = any(%(viva)s)) as publicado,
           l.updated_at                      as published_at
      from channel.listings l
      left join core.products p on p.sku = l.sku
      left join costing.costos_finales cf
             on cf.sku = l.sku and cf.canal = 'mercado_libre'
     where l.canal = 'amazon'
"""

# Mismo orden que `_ORDEN_ML` / `_ORDEN_AMZ`, traducido. `stock` ahora ordena
# por el stock del canal (ver la nota de arriba).
_ORDEN = {
    "stock_desc": "stock_own desc nulls last",
    "stock_asc": "stock_own asc nulls last",
    "precio_desc": "precio desc nulls last",
    "precio_asc": "precio asc nulls last",
}


def _filtros(base, *, search, solo_publicados, cuenta, estados, skus_filtro,
             pub_expr, activas=None):
    """
    Arma el WHERE compartido por las dos rejillas. Devuelve (sql, params).

    `activas` es el `(expr, params)` de `publicaciones_panel.filtro_sql_activas`
    y cuando viene MANDA sobre `solo_publicados` y sobre `estados`: son dos
    respuestas a la misma pregunta y sólo una es la del canal. `pub_expr`
    contesta "¿existe en el marketplace?" (una pausada de ML y una DISCOVERABLE
    de Amazon cuentan); `activas` contesta "¿se puede comprar HOY?". Sumarlas
    con AND mezclaría las dos definiciones en un número que no es ninguna.
    """
    sql, params = base, {}
    if search:
        sql += " and (p.name ilike %(like)s or l.sku::text ilike %(like)s)"
        params["like"] = f"%{search}%"
    if cuenta:
        sql += " and a.legacy_code = %(cuenta)s"
        params["cuenta"] = cuenta
    if activas:
        expr_act, params_act = activas
        sql += f" and ({expr_act})"
        params.update(params_act)
    elif solo_publicados:
        sql += f" and ({pub_expr})"
    if estados and not activas:
        if "publicado" in estados and "inactivo" not in estados:
            sql += f" and ({pub_expr})"
        elif "inactivo" in estados and "publicado" not in estados:
            sql += f" and not ({pub_expr})"
    terminos = [t.strip() for t in (skus_filtro or []) if t.strip()]
    if terminos:
        piezas = []
        for n, t in enumerate(terminos):
            piezas.append(f"(p.name ilike %(sku_{n})s or l.sku::text ilike %(sku_{n})s)")
            params[f"sku_{n}"] = f"%{t}%"
        sql += " and (" + " or ".join(piezas) + ")"
    return sql, params


def _pagina(sql, params, orden, per_page, page, orden_default):
    """Conteo + página. El ORDER BY va por ALIAS, no por la expresión: los
    alias existen en el select externo y así el orden no depende de que la
    columna siga llamándose igual adentro."""
    total = sdb.fetch_all(f"select count(*) as n from ({sql}) t", params)[0]["n"]
    orden_sql = _ORDEN.get(orden, orden_default)
    filas = sdb.fetch_all(
        f"select * from ({sql}) t order by {orden_sql} "
        f"limit %(limit)s offset %(offset)s",
        {**params, "limit": per_page, "offset": (page - 1) * per_page})
    return [dict(f) for f in filas], int(total)


def _activas(canal, solo_activas):
    """El WHERE de "se puede comprar HOY", derivado del normalizador de
    `publicaciones_panel`. Import perezoso: ese módulo lee `costos` y subirlo
    al encabezado haría ciclo."""
    if not solo_activas:
        return None
    from services import publicaciones_panel
    return publicaciones_panel.filtro_sql_activas(canal)


def rejilla_ml(*, page, per_page, search=None, solo_publicados=False,
               cuenta=None, orden="reciente", estados=None, skus_filtro=None,
               solo_activas=False):
    """Gemela de `meli.listar`. Filas con las llaves que espera `_normalizar`."""
    sql, params = _filtros(_REJILLA_ML, search=search,
                           solo_publicados=solo_publicados, cuenta=cuenta,
                           estados=estados, skus_filtro=skus_filtro,
                           pub_expr=_PUB_ML,
                           activas=_activas("mercado_libre", solo_activas))
    return _pagina(sql, params, orden, per_page, page,
                   "publicado desc, sku")


def rejilla_amazon(*, page, per_page, search=None, solo_publicados=False,
                   orden="reciente", estados=None, skus_filtro=None,
                   solo_activas=False):
    """Gemela de `amazon.listar`."""
    sql, params = _filtros(_REJILLA_AMZ, search=search,
                           solo_publicados=solo_publicados, cuenta=None,
                           estados=estados, skus_filtro=skus_filtro,
                           pub_expr=_PUB_AMZ,
                           activas=_activas("amazon", solo_activas))
    params.update({"pub": list(_AMZ_PUBLICADO), "viva": list(_AMZ_VIVA)})
    return _pagina(sql, params, orden, per_page, page,
                   "publicado desc, published_at desc nulls last")


def contar_publicados_ml(cuenta=None):
    """Gemela de `meli.contar_publicados` (sus DOS ramas: con y sin cuenta)."""
    sql = f"""select count(*) as n from channel.listings l
                join core.accounts a on a.id = l.account_id
               where l.canal = 'mercado_libre' and ({_PUB_ML})"""
    params = {}
    if cuenta:
        sql += " and a.legacy_code = %(cuenta)s"
        params["cuenta"] = cuenta
    return int(sdb.fetch_all(sql, params)[0]["n"])


def contar_publicados_amazon():
    """Gemela de `amazon.contar_publicados`."""
    return int(sdb.fetch_all(
        f"""select count(*) as n from channel.listings l
             where l.canal = 'amazon' and ({_PUB_AMZ})""",
        {"pub": list(_AMZ_PUBLICADO), "viva": list(_AMZ_VIVA)})[0]["n"])


# ═══════════════════════════════════════════════════════════════════════════
#  PASO 3 · BLOQUE 3 — Competencia
# ═══════════════════════════════════════════════════════════════════════════
# Los 4 sitios de `competencia_captura.py` preguntan la misma cosa que el bloque
# 1 —"¿qué publicaciones de ML tiene este SKU?"— salvo uno, que la pregunta AL
# REVÉS: dado un item_id que vimos en los resultados de competencia, ¿es
# nuestro? Ese necesita el índice invertido y sobre TODO el catálogo, no sobre
# una lista de SKUs; los otros tres reusan `publicaciones_ml`.

def publicaciones_ml_por_item() -> dict[str, dict[str, str]]:
    """
    { item_id: {sku, cuenta} } de TODAS las publicaciones de ML.

    Gemela de `competencia_captura._nuestras_publicaciones`. Sirve para marcar
    "esta publicación de la competencia en realidad es nuestra".

    NO filtra por `situacion`: una publicación pausada o cerrada **sigue siendo
    nuestra**, y de eso se trata la pregunta. Filtrarla haría que apareciéramos
    como competencia de nosotros mismos.
    """
    filas = sdb.fetch_all(
        """select l.listing_id as item_id, l.sku::text as sku,
                  a.legacy_code as cuenta
             from channel.listings l
             join core.accounts a on a.id = l.account_id
            where l.canal = 'mercado_libre'
              and nullif(l.listing_id, '') is not null""")
    return {f["item_id"]: {"sku": f["sku"], "cuenta": f["cuenta"] or ""}
            for f in filas}


def publicaciones_ml_vivas(skus: list[str]) -> dict[str, list[dict[str, Any]]]:
    """
    Como `publicaciones_ml`, pero solo las que siguen VIVAS en el canal.

    Existe para el sitio que en MySQL filtraba `success = 1`. Ojo con la
    traducción: `success` es "la publicación se creó bien" —un hecho del pasado,
    congelado— mientras que aquí se pregunta por el estado de hoy. Una
    publicación que nació bien y después se cerró tiene `success = 1` en la
    bitácora y `situacion = 'closed'` en kubera. **Gana kubera**, que es el
    mismo arbitraje por recencia de todo este paso.
    """
    return {sku: vivas for sku, pubs in publicaciones_ml(skus).items()
            if (vivas := [p for p in pubs
                          if str(p.get("situacion") or "").lower() != "closed"])}


# ═══════════════════════════════════════════════════════════════════════════
#  PASO 3 · BLOQUE 4 — Inventario (el último, y el que mueve stock)
# ═══════════════════════════════════════════════════════════════════════════
# Los 8 sitios de `inventario.py`. Va al final del paso 3 a propósito: es el
# único bloque cuyas lecturas deciden A QUÉ PUBLICACIONES se les va a escribir
# stock, así que un hueco aquí no se ve en una pantalla — se ve en la bodega.
#
# Los 8 son cuatro preguntas repetidas:
#   · el UNIVERSO a recorrer, por cuenta (ML) y global (Amazon)
#   · el respaldo de identidad {item_id: sku} cuando ML no trae SKU legible
#   · "¿este SKU existe en el canal?" antes de pedirle el detalle
#   · "¿de quién es este item_id?" cuando ML avisa por webhook
#
# UNA TRADUCCIÓN QUE NO ES OBVIA: en MySQL el universo salía de `success = 1`,
# que es "la publicación se creó bien" — un hecho del pasado, congelado. Aquí se
# usa el estado de HOY y se excluye `closed`. Es la diferencia entre "nació bien"
# y "sigue viva", y para decidir a quién visitar la buena es la segunda: recorrer
# publicaciones cerradas gasta llamadas a la API y vuelve a abrir filas que el
# barrido de cierre ya había apagado.

def universo_ml(cuenta: str) -> list[dict[str, Any]]:
    """[{sku, ml_item_id}] de las publicaciones VIVAS de una cuenta de ML."""
    return [dict(f) for f in sdb.fetch_all(
        """select l.sku::text as sku, l.listing_id as ml_item_id
             from channel.listings l
             join core.accounts a on a.id = l.account_id
            where l.canal = 'mercado_libre' and a.legacy_code = %(c)s
              and nullif(l.listing_id, '') is not null
              and lower(coalesce(l.situacion, '')) <> 'closed'""",
        {"c": cuenta})]


def universo_amazon() -> list[dict[str, Any]]:
    """[{sku, asin, status}] de las publicaciones vivas de Amazon."""
    return [dict(f) for f in sdb.fetch_all(
        f"""select l.sku::text as sku, l.listing_id as asin, l.status
              from channel.listings l
             where l.canal = 'amazon' and ({_PUB_AMZ})""",
        {"pub": list(_AMZ_PUBLICADO), "viva": list(_AMZ_VIVA)})]


def respaldo_identidad_ml(cuenta: str) -> dict[str, str]:
    """{ item_id: sku } de una cuenta — el respaldo cuando ML no trae SKU.

    Aquí NO se filtra `closed`: si ML nos está hablando de un item, queremos
    saber de quién es aunque ya esté cerrado. Es identidad, no elegibilidad.

    ⚠️ HAY COLISIONES REALES, y este diccionario las aplasta. Medido en
    producción el 19-ago: **81 `listing_id` de ML apuntan a dos SKUs distintos**,
    y el patrón es siempre el mismo — el SKU base y su variante reclaman la misma
    publicación (`TEC-0199` y `TEC-0199-NEG-VER`, `CUNA-0011` y `CUNA-0011-GRI`).

    La versión de MySQL tenía exactamente la misma forma, así que esto **no lo
    introduce el repunte**: lo destapa. Pero importa más aquí que allá, porque de
    este respaldo sale la identidad con la que `inventario` decide **a qué SKU
    escribirle stock**, y si gana el equivocado el stock aterriza en el producto
    que no es.

    Lo que sí cambia: antes ganaba el último que saliera de la consulta —o sea,
    el azar—. Ahora **gana el SKU más corto**, que en todos los casos medidos es
    el padre, y las colisiones se anotan en el log con nombre y apellido para que
    alguien pueda arreglarlas en vez de que se pierdan en silencio.
    """
    filas = sdb.fetch_all(
        """select l.listing_id as item_id, l.sku::text as sku
             from channel.listings l
             join core.accounts a on a.id = l.account_id
            where l.canal = 'mercado_libre' and a.legacy_code = %(c)s
              and nullif(l.listing_id, '') is not null
            order by length(l.sku::text), l.sku::text""",
        {"c": cuenta})
    res: dict[str, str] = {}
    choques: list[str] = []
    for f in filas:
        if f["item_id"] in res:
            choques.append(f"{f['item_id']}={res[f['item_id']]}|{f['sku']}")
            continue                      # el primero gana: el SKU más corto
        res[f["item_id"]] = f["sku"]
    if choques:
        log.warning("respaldo_identidad_ml(%s): %d item_id con mas de un SKU; "
                    "gana el mas corto. %s", cuenta, len(choques),
                    ", ".join(choques[:5]))
    return res


def dueno_de_item_ml(item_id: str) -> dict[str, str] | None:
    """{sku, cuenta} del item, o None. Lo usa el webhook de ML.

    Sin filtrar por situación, por lo mismo que arriba: ML avisa de items
    cerrados y pausados, y hay que poder atenderlos.
    """
    filas = sdb.fetch_all(
        """select l.sku::text as sku, a.legacy_code as cuenta
             from channel.listings l
             join core.accounts a on a.id = l.account_id
            where l.canal = 'mercado_libre' and l.listing_id = %(i)s
            limit 1""", {"i": str(item_id)})
    return {"sku": filas[0]["sku"], "cuenta": filas[0]["cuenta"]} if filas else None


def existe_en_amazon(sku: str) -> bool:
    """¿Este SKU tiene fila de Amazon? Gemela del `SELECT 1 FROM amazon_progress`.

    Deliberadamente NO exige que esté publicado: la pregunta del llamador es
    "¿vale la pena pedirle el detalle a Amazon?", y un SKU con fila pero sin
    estado (los cascarones) sí vale — es justo el caso que hay que resolver
    preguntándole al canal.
    """
    return bool(sdb.fetch_all(
        """select 1 from channel.listings
            where canal = 'amazon' and sku = %(s)s::citext limit 1""",
        {"s": str(sku)}))
