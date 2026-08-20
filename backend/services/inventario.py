"""
inventario.py — Núcleo del sistema de sincronización de inventario omnicanal.

Modelo (según la regla de negocio acordada):

    STOCK TOTAL = stock_real + stock_full(ML) + stock_fba(Amazon)

  - stock_real : unidades en TU almacén (vendidas por ti / Flex / FBM).
                 ↑ ES LO ÚNICO QUE SE SINCRONIZA entre Woo + ML(no-FULL) + Amazon(FBM).
  - stock_full : unidades en bodega de Mercado Libre (FULL).  Solo se muestran.
  - stock_fba  : unidades en bodega de Amazon (FBA).           Solo se muestran.

Fuente de verdad del stock_real: Odoo (qty_available).

Este servicio:
  • LEE en vivo de cada canal (precio, stock real/FULL/FBA, situación) y lo
    guarda en la tabla cache `canal_inventario` (para que la UI sea rápida).
  • Calcula un PLAN de sincronización en modo simulación (dry-run): qué stock_real
    habría que escribir en cada canal para igualarlo al maestro (Odoo).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from config import settings
from services import (alertas, amazon, channel_read, db, lecturas_fuente, meli,
                      odoo, woocommerce)

log = logging.getLogger("omnicanal.inventario")

_EPOCA = datetime(1970, 1, 1)


def _turno(visto: datetime | None):
    """Clave de orden del barrido progresivo: primero lo que NUNCA se ha visto,
    luego lo más viejo. Réplica exacta del viejo
    `ORDER BY (ci.sku IS NULL) DESC, ci.updated_at ASC`."""
    return (visto is not None, visto or _EPOCA)


def _precio_lista(item: dict[str, Any]) -> float | None:
    """
    Precio de LISTA de una publicación de ML: el tachado cuando hay promoción,
    o el precio normal cuando no la hay. Con esto, `precio < precio_base`
    significa "hay descuento vivo" y la resta da cuánto.

    Ojo con los nombres de ML, que engañan:
      · `price`          — lo que cobra hoy (el real)
      · `original_price` — el tachado; NULL si no hay campaña  ← el que sirve
      · `base_price`     — NO es el precio de lista: viene igual a `price`
                           (medido sobre 20 publicaciones activas: 0 difieren)

    Se devuelve `price` en vez de NULL cuando no hay promoción a propósito: el
    espejo trata NULL como "no observado" y conserva el valor anterior, así que
    una promo terminada se quedaría pegada para siempre.
    """
    v = item.get("original_price")
    if v in (None, ""):
        v = item.get("price")
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None

_ML_API = "https://api.mercadolibre.com"

# ── Esquema ───────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS canal_inventario (
    sku        VARCHAR(60)  NOT NULL,
    canal      VARCHAR(20)  NOT NULL,
    cuenta     VARCHAR(50)  NOT NULL DEFAULT '',
    item_id    VARCHAR(60),
    precio     DECIMAL(12,2),
    stock_real INT,
    stock_full INT,
    stock_fba  INT,
    es_full    TINYINT(1)   NOT NULL DEFAULT 0,
    logistica  VARCHAR(30),
    situacion  VARCHAR(30),
    moneda     VARCHAR(5)   NOT NULL DEFAULT 'MXN',
    updated_at DATETIME     NOT NULL,
    PRIMARY KEY (sku, canal, cuenta)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_schema_ok = False


def asegurar_schema() -> None:
    global _schema_ok
    if _schema_ok:
        return
    try:
        with db.get_cursor() as cur:
            cur.execute(_DDL)
        _schema_ok = True
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo crear canal_inventario: %s", exc)


def _upsert(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    asegurar_schema()
    sql = """
        INSERT INTO canal_inventario
          (sku, canal, cuenta, item_id, precio, stock_real, stock_full, stock_fba,
           es_full, logistica, situacion, moneda, updated_at)
        VALUES
          (%(sku)s, %(canal)s, %(cuenta)s, %(item_id)s, %(precio)s, %(stock_real)s,
           %(stock_full)s, %(stock_fba)s, %(es_full)s, %(logistica)s, %(situacion)s,
           %(moneda)s, NOW())
        ON DUPLICATE KEY UPDATE
          -- COALESCE en los tres campos que el barrido masivo de Amazon manda
          -- en NULL (auditoría 29-jul): escribe item_id=amazon_progress.asin,
          -- que es NULL en las 1,770 filas, y stock_real=None a propósito
          -- ("FBM se lee en el refresco individual"). Sin COALESCE, cada vuelta
          -- del ciclo BORRABA el ASIN, el stock FBM y el precio que el refresco
          -- individual sí había guardado bien. Mismo patrón ya sancionado en
          -- pedidos_ml para la comisión 0→valor.
          item_id=COALESCE(VALUES(item_id), item_id),
          precio=COALESCE(VALUES(precio), precio),
          stock_real=COALESCE(VALUES(stock_real), stock_real),
          -- stock_full / stock_fba NO llevan COALESCE: ahí un 0 y un NULL SÍ
          -- son informativos (bodega vacía vs sin dato).
          stock_full=VALUES(stock_full), stock_fba=VALUES(stock_fba), es_full=VALUES(es_full),
          logistica=VALUES(logistica),
          -- `situacion` TAMBIÉN con COALESCE: el barrido de Amazon manda NULL
          -- cuando Amazon no responde ese SKU, y sin esto se perdía el marcado
          -- `closed` de las publicaciones muertas (293 → 15 en una tarde).
          situacion=COALESCE(VALUES(situacion), situacion),
          updated_at=NOW()
    """
    def _mysql() -> None:
        with db.get_cursor() as cur:
            cur.executemany(sql, rows)

    from services import channel_mirror
    # F6 (corte, opción A): la tanda va PRIMERO a channel.listings (kubera);
    # canal_inventario queda de espejo inverso en hilo. Con kubera caída, MySQL
    # aguanta y el siguiente ciclo auto-sana (full-refresh por tanda).
    if channel_mirror.corte_activo():
        channel_mirror.escribir_primario([dict(r) for r in rows], _mysql)
        return len(rows)
    _mysql()
    # Dual-write F3 (flag SUPABASE_DUAL_WRITE_CHANNEL): espejo de la tanda a
    # channel.listings en hilo aparte; el trigger de la base registra los
    # cambios de precio/stock/FULL en channel.listing_history. Nunca rompe el sync.
    channel_mirror.en_hilo(channel_mirror.espejar_inventario, [dict(r) for r in rows])
    return len(rows)


async def _upsert_async(rows: list[dict[str, Any]]) -> int:
    """`_upsert` EN UN HILO — la única forma correcta de llamarlo desde async.

    `_upsert` escribe primero en kubera (`channel_mirror.escribir_primario`) con
    psycopg2, que es BLOQUEANTE. Llamado dentro de una corrutina paraba el
    backend entero mientras Postgres contestaba: el vigilante del event loop lo
    cachó parado justo ahí, en el sync de inventario y en el webhook de ML
    (13-ago). Todos los llamadores de `_upsert` son async, así que van por aquí.
    """
    return await asyncio.to_thread(_upsert, rows)


# ── LECTOR: Mercado Libre ───────────────────────────────────────────────────────

async def _leer_ml_item(
    cli: httpx.AsyncClient, item_id: str, token: str, cuenta: str | None = None
) -> dict | None:
    try:
        r = await cli.get(f"/items/{item_id}", headers={"Authorization": f"Bearer {token}"})
        # Token expirado (401): intentar renovarlo una vez y reintentar.
        if r.status_code == 401 and cuenta:
            nuevo = meli.refrescar_token(cuenta)
            if nuevo:
                r = await cli.get(f"/items/{item_id}", headers={"Authorization": f"Bearer {nuevo}"})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def _sku_de_item(item: dict[str, Any]) -> str | None:
    """
    SKU de una publicación de ML leyéndolo del PROPIO item, en orden:
    seller_custom_field → atributo SELLER_SKU → variaciones. Las publicaciones
    creadas fuera del panel guardan el SKU en el atributo, no en el campo
    (medido 4-ago: 185 de 186 huérfanas activas lo traen ahí). Mismas reglas
    de higiene que el espejo: sin espacios y ≤100 caracteres.
    """
    def _limpio(v: Any) -> str | None:
        s = str(v or "").strip()
        return s if s and len(s) <= 100 and not any(ch.isspace() for ch in s) else None

    sku = _limpio(item.get("seller_custom_field"))
    if sku:
        return sku
    for a in (item.get("attributes") or []):
        if (a.get("id") or "") == "SELLER_SKU":
            sku = _limpio(a.get("value_name"))
            if sku:
                return sku
    for var in (item.get("variations") or []):
        sku = _limpio(var.get("seller_custom_field"))
        if sku:
            return sku
    return None


# Universo vivo por cuenta: (timestamp, ids). Listarlo son ~22 GETs por cuenta
# (paginación scan de 100), así que se cachea media hora — el detalle por item
# sigue siendo fresco en cada ronda; esto solo decide QUÉ ids existen.
_UNIVERSO_TTL_S = 1800
# tope de cierres por ronda: no infla el lote y aun asi drena backlogs en horas
_CIERRES_POR_RONDA = 15
_universo_cache: dict[str, tuple[float, list[str]]] = {}
_user_id_cache: dict[str, str] = {}


async def _universo_ml(cli: httpx.AsyncClient, cuenta: str, token: str) -> list[str]:
    """IDs de TODAS las publicaciones vivas (active+paused) de la cuenta, del
    catálogo real de ML — no de ml_progress. Cache de 30 min por cuenta."""
    import time
    en_cache = _universo_cache.get(cuenta)
    if en_cache and time.monotonic() - en_cache[0] < _UNIVERSO_TTL_S:
        return en_cache[1]
    h = {"Authorization": f"Bearer {token}"}
    uid = _user_id_cache.get(cuenta)
    if not uid:
        r = await cli.get("/users/me", headers=h)
        if r.status_code != 200:
            return en_cache[1] if en_cache else []
        uid = str(r.json().get("id") or "")
        _user_id_cache[cuenta] = uid
    ids: list[str] = []
    for status in ("active", "paused"):
        scroll = None
        while True:
            params: dict[str, Any] = {"search_type": "scan", "limit": 100, "status": status}
            if scroll:
                params["scroll_id"] = scroll
            r = await cli.get(f"/users/{uid}/items/search", headers=h, params=params)
            if r.status_code != 200:
                break
            j = r.json()
            res = j.get("results") or []
            ids.extend(res)
            scroll = j.get("scroll_id")
            if not res or not scroll:
                break
    if ids:  # una página fallida a media lista no borra el cache bueno anterior
        _universo_cache[cuenta] = (time.monotonic(), ids)
        return ids
    return en_cache[1] if en_cache else []


async def _lote_desde_ml(cli: httpx.AsyncClient, cuenta: str, token: str,
                         limite: int) -> list[dict[str, Any]]:
    """
    Lote de la ronda cuando SYNC_DESDE_ML está encendido: el universo sale del
    catálogo vivo de ML y la rotación es la MISMA de siempre (lo nunca visto
    primero, luego lo más rancio, por item_id). Efecto lateral deliberado: las
    publicaciones que ML ya borró no aparecen en el universo → dejan de
    refrescarse y de pisar la fila de su SKU (253 muertas medidas el 4-ago).
    El sku viaja None: se resuelve del propio item al leer el detalle.
    """
    ids = await _universo_ml(cli, cuenta, token)
    if not ids:
        return []
    # PASO 0 del desmantelamiento (12-ago-2026): la rotación se ordena con
    # `channel.listings`, que es donde este mismo sync escribe. Leerlo del
    # espejo MySQL funcionaba solo mientras el espejo estuviera fresco.
    vistos: dict[str, Any] = {}
    try:
        from services import channel_read
        vistos = channel_read.vistos_ml(cuenta)
    except Exception:  # noqa: BLE001 — sin cache de vistos el orden degrada, no rompe
        pass
    from datetime import datetime
    epoca = datetime(1970, 1, 1)
    orden = sorted(ids, key=lambda i: (i in vistos, vistos.get(i) or epoca))
    lote = [{"sku": None, "ml_item_id": i} for i in orden[:limite]]

    # BARRIDO DE CIERRE: filas que el panel cree vivas (active/paused) cuyo
    # item YA NO aparece en el catálogo vivo — ML lo borró o lo cerró y, como
    # el universo solo consulta active+paused, nadie volvería a preguntarle:
    # la fila quedaría CONGELADA en su último estado para siempre. Se leen una
    # única vez (el detalle sí responde con su estado final, p. ej.
    # inactive/deleted como el caso CUNA-0011-AZL) y al escribirse dejan de
    # cumplir el filtro → el barrido se auto-termina. Acotado por ronda para
    # no inflar el lote.
    # Insumo desde `channel.listings` (paso 0): con el espejo MySQL congelado
    # este barrido NO se auto-terminaba — las filas seguían 'active' ahí para
    # siempre y volvían a colarse en cada ronda, desplazando trabajo real.
    try:
        from services import channel_read
        universo = set(ids)
        congeladas = [i for i in channel_read.vivas_ml(cuenta) if i not in universo]
        if congeladas:
            log.info("barrido de cierre %s: %d fila(s) viva(s) sin publicación "
                     "en el catálogo — se les lee el estado final", cuenta,
                     len(congeladas))
            lote.extend({"sku": None, "ml_item_id": i}
                        for i in congeladas[:_CIERRES_POR_RONDA])
    except Exception:  # noqa: BLE001 — el barrido nunca frena la ronda normal
        pass
    return lote


async def sincronizar_ml(cuenta: str, limite: int = 60) -> dict[str, Any]:
    """Lee en vivo los items de una cuenta ML y los guarda en canal_inventario."""
    token = meli._access_token(cuenta)
    if not token:
        return {"canal": "mercado_libre", "cuenta": cuenta, "ok": False, "motivo": "sin token"}

    rows: list[dict[str, Any]] = []
    sin_sku = 0
    async with httpx.AsyncClient(base_url=_ML_API, timeout=20.0) as cli:
        if settings.sync_desde_ml:
            # F. UNIVERSO: el catálogo real de ML decide qué existe.
            listings = await _lote_desde_ml(cli, cuenta, token, limite)
            # Respaldo de identidad para items sin SKU legible en ML.
            # PASO 3 · BLOQUE 4 (19-ago).
            respaldo = (channel_read.respaldo_identidad_ml(cuenta)
                        if settings.supabase_read_publicaciones else {
                str(r["ml_item_id"]): r["sku"] for r in db.fetch_all(
                    "SELECT sku, ml_item_id FROM ml_progress "
                    "WHERE cuenta=%s AND ml_item_id IS NOT NULL", (cuenta,))
            })
        else:
            # Camino histórico: la bitácora del publicador (ml_progress).
            # Progresivo: primero los SKUs que aún NO están en el cache, luego
            # los más viejos, para cubrir todo el catálogo corrida a corrida.
            #
            # PASO 0 (12-ago-2026): el TURNO se decide con `channel.listings`,
            # no con `canal_inventario`. Ese JOIN no traía datos, ORDENABA — y
            # una fecha congelada congela el orden: el barrido se quedaría
            # rifando los mismos SKUs cada 15 min y el resto del catálogo no se
            # volvería a observar NUNCA. Sin un error en los logs: la fuente de
            # verdad simplemente dejaría de refrescarse. `ml_progress` sí sigue
            # en MySQL (bitácora del publicador, viva), así que el orden se
            # arma aquí — son ~1,900 filas por cuenta.
            # PASO 3 · BLOQUE 4: el universo sale de channel.listings, que ademas
            # ya excluye las cerradas (ver la nota en channel_read).
            listings = channel_read.universo_ml(cuenta) if settings.supabase_read_publicaciones else db.fetch_all(
                """SELECT mp.sku, mp.ml_item_id
                   FROM ml_progress mp
                   WHERE mp.cuenta=%s AND mp.success=1 AND mp.ml_item_id IS NOT NULL""",
                (cuenta,),
            ) if settings.supabase_read_channel else db.fetch_all(
                """SELECT mp.sku, mp.ml_item_id
                   FROM ml_progress mp
                   LEFT JOIN canal_inventario ci
                          ON ci.sku = mp.sku AND ci.canal='mercado_libre' AND ci.cuenta = mp.cuenta
                   WHERE mp.cuenta=%s AND mp.success=1 AND mp.ml_item_id IS NOT NULL
                   ORDER BY (ci.sku IS NULL) DESC, ci.updated_at ASC
                   LIMIT %s""",
                (cuenta, limite),
            )
            if settings.supabase_read_channel:
                vistos = channel_read.vistos_ml(cuenta)
                listings.sort(key=lambda r: _turno(vistos.get(str(r["ml_item_id"]))))
                listings = listings[:limite]
                lecturas_fuente.anotar("channel", "kubera")
            respaldo = {}
        for lst in listings:
            item = await _leer_ml_item(cli, lst["ml_item_id"], token, cuenta)
            if not item:
                continue
            # Identidad: en modo universo, del propio item (con ml_progress de
            # respaldo); en modo histórico viene de la bitácora, como siempre.
            sku = lst["sku"] or _sku_de_item(item) or respaldo.get(str(lst["ml_item_id"]))
            if not sku:
                sin_sku += 1
                log.info("sync ML %s: %s sin SKU legible — no se escribe",
                         cuenta, lst["ml_item_id"])
                continue
            logistic = (item.get("shipping") or {}).get("logistic_type")
            es_full = logistic == "fulfillment"
            qty = item.get("available_quantity")
            rows.append({
                "sku": sku, "canal": "mercado_libre", "cuenta": cuenta,
                "item_id": lst["ml_item_id"], "precio": item.get("price"), "precio_base": _precio_lista(item),
                "stock_real": 0 if es_full else qty,
                "stock_full": qty if es_full else 0,
                "stock_fba": None,
                "es_full": 1 if es_full else 0,
                "logistica": logistic, "situacion": item.get("status"), "moneda": "MXN",
            })
    n = await _upsert_async(rows)
    salida = {"canal": "mercado_libre", "cuenta": cuenta, "ok": True, "actualizados": n}
    if sin_sku:
        salida["sin_sku"] = sin_sku
    return salida


# ── LECTOR: Amazon (FBA bulk) ───────────────────────────────────────────────────

async def sincronizar_amazon(limite: int = 100) -> dict[str, Any]:
    """Lee el inventario FBA (fulfillableQuantity) por SKU y lo guarda."""
    token = await amazon._access_token()
    if not token:
        return {"canal": "amazon", "ok": False, "motivo": "sin token LWA"}

    from config import settings
    mp = settings.amazon_marketplace_id
    fba: dict[str, int] = {}
    try:
        async with httpx.AsyncClient(base_url=settings.amazon_sp_api_endpoint, timeout=30.0) as cli:
            params = {"granularityType": "Marketplace", "granularityId": mp,
                      "marketplaceIds": mp, "details": "true"}
            next_token = None
            paginas = 0
            while paginas < 10:  # tope de seguridad
                if next_token:
                    params["nextToken"] = next_token
                r = await cli.get("/fba/inventory/v1/summaries", params=params,
                                  headers={"x-amz-access-token": token})
                if r.status_code != 200:
                    break
                payload = r.json().get("payload", {})
                for s in payload.get("inventorySummaries", []):
                    det = s.get("inventoryDetails", {}) or {}
                    fba[s.get("sellerSku")] = det.get("fulfillableQuantity", s.get("totalQuantity", 0))
                next_token = (r.json().get("pagination") or {}).get("nextToken")
                paginas += 1
                if not next_token:
                    break
    except Exception as exc:  # noqa: BLE001
        log.warning("Amazon FBA sync falló: %s", exc)

    # Cruzar con amazon_progress (sku, asin, status); progresivo: primero los que
    # faltan en el cache, luego los más viejos.
    # PASO 0 (12-ago-2026): mismo caso que el turno de ML — el JOIN ordenaba,
    # no traía datos. Ver la nota larga en `_lote_ml`.
    if settings.supabase_read_channel:
        # PASO 3 · BLOQUE 4.
        pubs = (channel_read.universo_amazon()
                if settings.supabase_read_publicaciones else db.fetch_all(
                    """SELECT ap.sku, ap.asin, ap.status
                       FROM amazon_progress ap WHERE ap.success=1"""))
        vistos = channel_read.vistos_amazon()
        pubs.sort(key=lambda r: _turno(vistos.get(str(r["sku"]))))
        pubs = pubs[:limite]
        lecturas_fuente.anotar("channel", "kubera")
    else:
        pubs = db.fetch_all(
            """SELECT ap.sku, ap.asin, ap.status
               FROM amazon_progress ap
               LEFT JOIN canal_inventario ci
                      ON ci.sku = ap.sku AND ci.canal='amazon'
               WHERE ap.success=1
               ORDER BY (ci.sku IS NULL) DESC, ci.updated_at ASC
               LIMIT %s""",
            (limite,),
        )
    # Se le pregunta A AMAZON, no a nuestra bitácora (auditoría 29-jul).
    # Una llamada por lote de 20 devuelve precio + ASIN + estado REAL + stock FBM.
    # Antes: el precio venía de Pricing API v0 (cubría 40%), el ASIN y el estado se
    # copiaban de `amazon_progress` (ASIN NULL en el 100%, y el estado era el de
    # publicación: 293 listados dados de baja seguían diciendo PUBLISHED) y
    # `stock_real` se mandaba NULL a propósito — por eso la tarjeta de Amazon
    # mostraba "—" en stock aunque Amazon sí devuelve la cantidad.
    skus_pub = [p["sku"] for p in pubs if p.get("sku")]
    vivo = await amazon.datos_por_sku(skus_pub)

    rows: list[dict[str, Any]] = []
    for p in pubs:
        sku = p["sku"]
        v = vivo.get(sku) or {}
        fba_qty = v.get("stock_fba")
        if fba_qty is None:
            fba_qty = fba.get(sku, 0)          # respaldo: summaries de FBA
        es_fba = bool(fba_qty)
        rows.append({
            "sku": sku, "canal": "amazon", "cuenta": "",
            # COALESCE en _upsert protege el valor viejo si Amazon no respondió
            # este SKU en esta vuelta (no se pisa con NULL).
            "item_id": v.get("asin") or p.get("asin"),
            "precio": v.get("precio"),
            "stock_real": v.get("stock_real"),
            "stock_full": None,
            "stock_fba": fba_qty,
            "es_full": 1 if es_fba else 0,
            "logistica": "FBA" if es_fba else "FBM",
            # Estado REAL del listado. Si Amazon NO devolvió este SKU se manda
            # None y el COALESCE del _upsert CONSERVA el valor que ya había.
            #
            # OJO, esto es delicado (bug detectado y corregido el 29-jul, mismo
            # día): la primera versión hacía `v.get("estado") or p.get("status")`,
            # o sea que ante silencio de Amazon reescribía el estado con el de
            # `amazon_progress` — siempre "PUBLISHED". Efecto medido: las 293
            # publicaciones muertas que habíamos marcado `closed` volvían a
            # PUBLISHED en cuanto el barrido las visitaba (bajaron a 15 en una
            # tarde). Justo el problema que este cambio venía a resolver.
            #
            # Un SKU ausente en la respuesta puede ser un listado dado de baja O
            # un lote que falló: por eso NO se cierra aquí. Cerrar es tarea de
            # `scripts/marcar_amazon_muertas.py`, que confirma el 404 uno por uno
            # y distingue un 404 real de un 429/5xx.
            "situacion": v.get("estado"),
            "moneda": "MXN",
        })
    n = await _upsert_async(rows)
    return {"canal": "amazon", "ok": True, "actualizados": n,
            "skus_fba": len(fba), "skus_leidos_en_vivo": len(vivo),
            "skus_con_precio": sum(1 for v in vivo.values() if v.get("precio") is not None),
            "skus_con_stock": sum(1 for v in vivo.values() if v.get("stock_real") is not None)}


# ── LECTOR: WooCommerce ─────────────────────────────────────────────────────────

async def sincronizar_woo(skus: list[str]) -> dict[str, Any]:
    """Guarda el stock real (stock_quantity) y precio de WooCommerce por SKU."""
    rows: list[dict[str, Any]] = []
    for sku in skus[:60]:
        p = await woocommerce.obtener_producto_por_sku(sku)
        if not p:
            continue
        rows.append({
            "sku": sku, "canal": "general", "cuenta": "",
            "item_id": str(p.get("wc_id") or ""), "precio": p.get("precio"),
            "stock_real": p.get("stock"), "stock_full": None, "stock_fba": None,
            "es_full": 0, "logistica": "propia", "situacion": p.get("estado"), "moneda": "MXN",
        })
    n = await _upsert_async(rows)
    return {"canal": "general", "ok": True, "actualizados": n}


# ── SYNC de un solo SKU (en vivo, al abrir el detalle) ──────────────────────────

async def _sync_ml_sku(sku: str) -> list[dict[str, Any]]:
    """Lee el SKU en ambas cuentas de ML. Tolerante a fallos."""
    # PASO 3 · BLOQUE 4.
    if settings.supabase_read_publicaciones:
        ml = [{"cuenta": p["cuenta"], "ml_item_id": p["item_id"]}
              for p in channel_read.publicaciones_ml([sku]).get(sku, [])]
    else:
        try:
            ml = db.fetch_all(
                """SELECT cuenta, ml_item_id FROM ml_progress
                   WHERE sku=%s AND ml_item_id IS NOT NULL""",
                (sku,),
            )
        except Exception:  # noqa: BLE001
            return []
    out: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(base_url=_ML_API, timeout=20.0) as cli:
            vistos: set[str] = set()
            for r in ml:
                cta = r["cuenta"]
                if cta in vistos:
                    continue
                vistos.add(cta)
                token = meli._access_token(cta)
                if not token:
                    continue
                item = await _leer_ml_item(cli, r["ml_item_id"], token, cta)
                if not item:
                    continue
                logistic = (item.get("shipping") or {}).get("logistic_type")
                es_full = logistic == "fulfillment"
                qty = item.get("available_quantity")
                out.append({
                    "sku": sku, "canal": "mercado_libre", "cuenta": cta,
                    "item_id": r["ml_item_id"], "precio": item.get("price"), "precio_base": _precio_lista(item),
                    "stock_real": 0 if es_full else qty,
                    "stock_full": qty if es_full else 0,
                    "stock_fba": None, "es_full": 1 if es_full else 0,
                    "logistica": logistic, "situacion": item.get("status"), "moneda": "MXN",
                })
    except Exception as exc:  # noqa: BLE001
        log.warning("sync ML sku %s: %s", sku, exc)
    return out


async def _sync_amazon_sku(sku: str) -> list[dict[str, Any]]:
    try:
        existe = (channel_read.existe_en_amazon(sku)
                  if settings.supabase_read_publicaciones
                  else bool(db.fetch_one(
                      "SELECT 1 FROM amazon_progress WHERE sku=%s LIMIT 1", (sku,))))
        if not existe:
            return []
        a = await amazon.detalle_sku(sku)
        if not a:
            return []
        return [{
            "sku": sku, "canal": "amazon", "cuenta": "",
            "item_id": a.get("asin"), "precio": a.get("precio"),
            "stock_real": a.get("stock_real"), "stock_full": None,
            "stock_fba": a.get("stock_fba"), "es_full": 1 if a.get("es_fba") else 0,
            "logistica": "FBA" if a.get("es_fba") else "FBM",
            "situacion": a.get("estado"), "moneda": "MXN",
        }]
    except Exception as exc:  # noqa: BLE001
        log.warning("sync Amazon sku %s: %s", sku, exc)
        return []


async def _sync_woo_sku(sku: str) -> list[dict[str, Any]]:
    try:
        p = await woocommerce.obtener_producto_por_sku(sku)
        if not p:
            return []
        return [{
            "sku": sku, "canal": "general", "cuenta": "",
            "item_id": str(p.get("wc_id") or ""), "precio": p.get("precio"),
            "stock_real": p.get("stock"), "stock_full": None, "stock_fba": None,
            "es_full": 0, "logistica": "propia", "situacion": p.get("estado"), "moneda": "MXN",
        }]
    except Exception as exc:  # noqa: BLE001
        log.warning("sync Woo sku %s: %s", sku, exc)
        return []


async def refrescar_ml_item_id(item_id: str) -> dict[str, Any]:
    """
    Refresca UN ítem de Mercado Libre por su id (lo usa el webhook cuando ML avisa
    que un item cambió). Busca la cuenta/SKU en ml_progress y actualiza el cache.
    """
    # PASO 3 · BLOQUE 4. Lo llama el webhook de ML: si aqui no se resuelve el
    # dueño, el aviso se descarta y esa publicacion no se refresca.
    if settings.supabase_read_publicaciones:
        row = channel_read.dueno_de_item_ml(item_id)
    else:
        try:
            row = db.fetch_one(
                "SELECT sku, cuenta FROM ml_progress WHERE ml_item_id=%s LIMIT 1",
                (item_id,),
            )
        except Exception:  # noqa: BLE001
            row = None
    if not row:
        return {"ok": False, "motivo": "item_id no está en el catálogo"}
    sku, cuenta = row["sku"], row["cuenta"]
    token = meli._access_token(cuenta)
    if not token:
        return {"ok": False, "motivo": "sin token"}
    try:
        async with httpx.AsyncClient(base_url=_ML_API, timeout=20.0) as cli:
            item = await _leer_ml_item(cli, item_id, token, cuenta)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": str(exc)}
    if not item:
        return {"ok": False, "motivo": "no se pudo leer el item"}
    logistic = (item.get("shipping") or {}).get("logistic_type")
    es_full = logistic == "fulfillment"
    qty = item.get("available_quantity")
    await _upsert_async([{
        "sku": sku, "canal": "mercado_libre", "cuenta": cuenta,
        "item_id": item_id, "precio": item.get("price"), "precio_base": _precio_lista(item),
        "stock_real": 0 if es_full else qty, "stock_full": qty if es_full else 0,
        "stock_fba": None, "es_full": 1 if es_full else 0,
        "logistica": logistic, "situacion": item.get("status"), "moneda": "MXN",
    }])
    return {"ok": True, "sku": sku, "cuenta": cuenta, "item_id": item_id}


async def sincronizar_sku(sku: str) -> dict[str, Any]:
    """
    Lee en vivo TODOS los canales para un SKU concreto (en paralelo) y actualiza
    el cache. Se usa al abrir el detalle 360° para que nunca aparezca incompleto.
    Tolerante a fallos: si un canal falla, los demás se guardan igual.
    """
    import asyncio
    partes = await asyncio.gather(
        _sync_ml_sku(sku), _sync_amazon_sku(sku), _sync_woo_sku(sku),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    for p in partes:
        if isinstance(p, list):
            rows.extend(p)
    try:
        n = await _upsert_async(rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("upsert sku %s: %s", sku, exc)
        n = 0
    return {"sku": sku, "ok": True, "actualizados": n}


# ── LECTURA para la UI ──────────────────────────────────────────────────────────

def leer_inventario(skus: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """
    Devuelve { sku: { 'mercado_libre|BEKURA': {...}, 'amazon|': {...}, ... } }
    con lo cacheado en canal_inventario para un lote de SKUs.
    """
    if not skus:
        return {}
    # PASO 3 del desmantelamiento (12-ago-2026): `canal_inventario` quedó
    # CONGELADA el 11-ago, así que caer ahí ya no es una red de seguridad —
    # es servir datos viejos sin avisar. Medido el mismo día: de 354 filas, 0
    # solo en MySQL y 0 solo en kubera, pero 12 campos distintos, todos con
    # kubera al día (ORG-0451: MySQL decía cross_docking/inactive cuando la
    # publicación ya estaba en fulfillment/active). Si kubera falla, la lectura
    # falla: es preferible un error visible a un inventario mentiroso.
    if settings.supabase_read_channel:
        out_kb = channel_read.leer_inventario(list(skus))
        lecturas_fuente.anotar("channel", "kubera")
        return out_kb
    asegurar_schema()
    ph = ",".join(["%s"] * len(skus))
    try:
        rows = db.fetch_all(
            f"SELECT * FROM canal_inventario WHERE sku IN ({ph})", tuple(skus)
        )
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        clave = f"{r['canal']}|{r.get('cuenta') or ''}"
        out.setdefault(r["sku"], {})[clave] = r
    return out


# ── PLAN de sincronización (dry-run) ────────────────────────────────────────────

def plan_dry_run(limite: int = 200) -> dict[str, Any]:
    """
    Calcula qué stock_real habría que escribir en cada canal para igualarlo al
    maestro (Odoo). NO escribe nada. Devuelve un reporte de diferencias.
    """
    asegurar_schema()
    # SKUs con inventario cacheado (no-FULL/FBA) para comparar
    if settings.supabase_read_channel:
        rows = channel_read.no_full(limite)
        lecturas_fuente.anotar("channel", "kubera")
    else:
        rows = db.fetch_all(
            """SELECT sku, canal, cuenta, item_id, stock_real, es_full
               FROM canal_inventario
               WHERE es_full = 0 AND canal IN ('mercado_libre','amazon','general')
               LIMIT %s""",
            (limite,),
        )
    skus = sorted({r["sku"] for r in rows})
    maestro = odoo.stock_por_sku(skus)  # { sku: qty_available }

    cambios: list[dict[str, Any]] = []
    for r in rows:
        objetivo = maestro.get(r["sku"])
        if objetivo is None:
            continue
        actual = r.get("stock_real")
        if actual is None or int(actual) != int(objetivo):
            cambios.append({
                "sku": r["sku"], "canal": r["canal"], "cuenta": r.get("cuenta") or "",
                "item_id": r.get("item_id"),
                "stock_actual": actual, "stock_objetivo": int(objetivo),
                "delta": (int(objetivo) - int(actual)) if actual is not None else None,
            })
    return {
        "modo": "dry_run",
        "maestro": "odoo",
        "skus_evaluados": len(skus),
        "cambios_propuestos": len(cambios),
        "detalle": cambios[:200],
    }
