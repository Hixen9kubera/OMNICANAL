"""
fanout_stock.py — Fan-out del stock DROP hacia los canales.

PROBLEMA QUE RESUELVE: hoy una venta no-FULL descuenta en WooCommerce (30→29)
pero NADIE le avisa a los demás canales — SANCORFASHION y Amazon siguen
ofreciendo 30. Verificado 2026-07-24: `sync_woo.py` solo empuja Odoo→Woo y el
sync de 15 min solo LEE los canales. Riesgo real de sobreventa.

QUÉ HACE: cuando el stock DROP de un SKU cambia, lo replica a todas las
publicaciones ACTIVAS y NO-FULL de ese SKU en todos los canales.

    venta en BEKURA (no-FULL) → Woo 30→29 → [fan-out] → SANCOR 29 · Amazon 29

DECISIONES DE DISEÑO (cada una nace de un incidente real o de una regla viva):

  1. SE ENCOLA EL SKU, NUNCA UN DELTA. Al procesarlo se LEE el stock actual de
     Woo. Así el flujo es idempotente por naturaleza (un mensaje repetido da el
     mismo resultado) y auto-sanable (si un evento se pierde, el siguiente
     corrige). Con deltas ("resta 1") un duplicado descuadra el inventario para
     siempre — y ML manda webhooks EN RÁFAGA (regla 6 de CLAUDE.md).

  2. LA PAUSA SE RESPETA SIEMPRE. Escribir SOLO stock a una publicación PAUSADA
     la REACTIVA (ML avisa: "se reactivaron porque hiciste cambios en su stock o
     estado"; pasado real con CAM-0030 el 2026-07-24), y Brandon pidió que todas
     se queden pausadas. Durante meses eso las dejó fuera del fan-out.
     Desde el 28-jul SÍ se sincronizan las PAUSADAS de ML DROP: mandando
     `status` junto al stock en la misma petición, ML respeta el estado y solo
     cambia la cantidad (probado en ambas cuentas). El estado se LEE antes de
     escribir — mandar `paused` a ciegas pausaría una activa. `under_review`,
     `closed` e `inactive` siguen fuera: ahí manda ML, no nosotros.

  3. SOLO ítems NO-FULL. Las piezas de FULL/FBA viven en la bodega del
     marketplace, no son del almacén compartido, y ML no deja fijarles cantidad.

  4. COMPARAR ANTES DE ESCRIBIR. Si el canal ya tiene el valor, no se escribe:
     ahorra rate-limit y MATA EL ECO (al escribir en ML llega de vuelta un
     webhook `items` que volvería a encolar el SKU; como el valor ya coincide,
     el ciclo muere solo).

  5. DEBOUNCE por SKU: las ráfagas de la misma venta se colapsan en UNA sola
     escritura por canal.

  6. NUNCA rompe la venta: se invoca fire-and-forget después de que el pedido ya
     quedó guardado; cualquier excepción muere aquí dentro.

FLAGS (Railway, apagable sin deploy):
  FANOUT_ENABLED   default False — nace apagado.
  FANOUT_DRY_RUN   default True  — calcula y REGISTRA lo que haría, sin escribir.
  FANOUT_CANALES   CSV de canales habilitados para escribir (encendido gradual).
  FANOUT_RESERVA   piezas de colchón que NO se publican (cubre la ventana de
                   latencia entre la venta y la escritura).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.fanout")

# Ventana de coalescing: las ráfagas del mismo SKU se funden en una escritura.
DEBOUNCE_S = 5.0
_TICK_S = 1.0          # cada cuánto revisa el worker si algo ya "reposó"
_EVENTOS_MAX = 300     # ring buffer para la pantalla de monitoreo

# Cada canal nombra distinto "está vendiendo": ML usa `active` (paused/closed/
# under_review/inactive NO venden), Amazon usa `PUBLISHED` (ACCEPTED aún no
# publica) y WooCommerce `publish`. Sin esta normalización el fan-out ignoraba
# las 1,616 publicaciones vivas de Amazon — que son el destino DROP más grande.
_SITUACIONES_VIVAS = {"active", "published", "publish"}

# ML PAUSADO que SÍ se sincroniza (solo DROP; el FULL nunca se toca). Una pausada
# no vende, así que no hay riesgo de sobreventa — pero sí lo hay el día que
# Brandon la reactive con el stock rancio. Se puede escribir SIN despertarla
# mandando `status` junto a `available_quantity` (probado 28-jul en ambas
# cuentas: HTTP 200, `paused_by_seller` intacto y el stock actualizado).
# `under_review`, `closed` e `inactive` quedan FUERA a propósito: ahí ML decide,
# no nosotros, y escribirles puede alterar la revisión.
_SITUACIONES_PAUSADAS = {"paused"}

_pendientes: dict[str, dict[str, Any]] = {}   # sku → {listo_en, motivo, encolado}
_lock = threading.Lock()
_worker_iniciado = False
_eventos: deque[dict[str, Any]] = deque(maxlen=_EVENTOS_MAX)
_contadores: dict[str, int] = {
    "encolados": 0, "procesados": 0, "escrituras": 0, "simuladas": 0,
    "sin_cambio": 0, "errores": 0, "omitidos_full": 0, "omitidos_pausados": 0,
}


# ── Configuración ────────────────────────────────────────────────────────────

def habilitado() -> bool:
    return bool(getattr(settings, "fanout_enabled", False))


def dry_run() -> bool:
    """True = NO escribe en los canales, solo registra lo que haría."""
    return bool(getattr(settings, "fanout_dry_run", True))


def _canales_activos() -> set[str] | None:
    """Canales con escritura habilitada. None = todos (cuando el CSV va vacío)."""
    csv = (getattr(settings, "fanout_canales", "") or "").strip()
    if not csv:
        return None
    return {c.strip().lower() for c in csv.split(",") if c.strip()}


def _reserva() -> int:
    try:
        return max(0, int(getattr(settings, "fanout_reserva", 0) or 0))
    except (TypeError, ValueError):
        return 0


# ── Lectura de la VERDAD (WooCommerce vía MySQL directo) ─────────────────────

def _stock_drop(sku: str) -> int | None:
    """
    Stock DROP del SKU = `_stock` en WooCommerce (que ya trae el de Odoo).

    Se lee por MySQL directo (wp_db) a propósito: la REST de Woo devuelve 403
    intermitente por el CDN de Hostinger (pendiente #1) y un fallo de lectura
    NUNCA debe convertirse en un stock inventado.
    """
    from services import wp_db
    P = wp_db._prefix()
    rows = wp_db._fetch_all(
        f"""SELECT st.meta_value AS stock
            FROM {P}postmeta s
            JOIN {P}posts p ON p.ID = s.post_id AND p.post_status <> 'trash'
            LEFT JOIN {P}postmeta st ON st.post_id = p.ID AND st.meta_key = '_stock'
            WHERE s.meta_key = '_sku' AND s.meta_value = %s
            LIMIT 1""",
        (sku,),
    )
    if not rows:
        return None
    v = rows[0].get("stock")
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _token_amazon() -> str | None:
    """
    Token LWA de Amazon desde contexto SÍNCRONO.

    OJO (auditoría 27-jul): `asyncio.run()` revienta con RuntimeError si YA hay
    un event loop corriendo, y el `loop.run_until_complete` del except tampoco
    servía (no se puede correr un loop nuevo dentro del hilo de otro que está
    activo). El vigilante de FBA vive en un AsyncIOScheduler → moría en CADA
    ejecución y nunca llegó a correr. Se resuelve empujando la corrutina a un
    HILO aparte con su propio loop cuando detectamos uno activo.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from services import amazon
    try:
        asyncio.get_running_loop()
    except RuntimeError:                      # no hay loop: camino simple
        try:
            return asyncio.run(amazon._access_token())
        except Exception as exc:  # noqa: BLE001
            log.warning("token Amazon: %s", exc)
            return None
    try:                                       # hay loop: hilo con loop propio
        with ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(amazon._access_token())).result(timeout=40)
    except Exception as exc:  # noqa: BLE001
        log.warning("token Amazon (hilo aparte): %s", exc)
        return None


def _amazon_en_vivo(sku: str) -> dict[str, Any]:
    """
    Cantidad y estado REALES de un listing de Amazon (SP-API).

    IMPRESCINDIBLE: `canal_inventario.stock_real` viene NULL en el 100% de las
    filas de Amazon — el batch lo escribe así a propósito
    (`inventario.py`: "FBM se lee en refresco individual"). Sin esta lectura en
    vivo, el fan-out creía que Amazon tenía 0 en TODO y el dry-run reportaba
    "0 → N" para 1,614 SKUs (falso: hay listings con 540, 150 y hasta 1,999 pzas).

    También devuelve el estado real: Amazon usa BUYABLE (vendible) vs
    DISCOVERABLE (existe pero DORMIDO). El `situacion='PUBLISHED'` que guarda el
    panel viene de nuestra propia bitácora `amazon_progress`, no de Amazon: el
    76% de lo que decimos "PUBLISHED" está en realidad dormido, y escribirle
    stock lo DESPERTARÍA.
    """
    import httpx
    if "/" in sku:   # rompe la URL de la Listings API
        return {"ok": False, "motivo": "SKU con '/' no direccionable en la API"}
    token = _token_amazon()
    if not token:
        return {"ok": False, "motivo": "sin token de Amazon"}
    try:
        r = httpx.get(
            f"{settings.amazon_sp_api_endpoint}/listings/2021-08-01/items/"
            f"{settings.amazon_seller_id}/{sku}",
            params={"marketplaceIds": settings.amazon_marketplace_id,
                    "includedData": "summaries,fulfillmentAvailability,attributes"},
            headers={"x-amz-access-token": token}, timeout=25.0,
        )
        if r.status_code != 200:
            return {"ok": False, "motivo": f"HTTP {r.status_code}"}
        d = r.json()
        # DOS vistas de la cantidad y NO son la misma:
        #   attributes.fulfillment_availability = lo que NOSOTROS fijamos (se
        #     actualiza al instante con el PATCH) → es la autoritativa para
        #     decidir si hay que escribir.
        #   fulfillmentAvailability = lo que Amazon SIRVE hoy (vista derivada,
        #     tarda en reflejar el cambio).
        # Comparar contra la servida provocaría reescrituras en bucle tras cada
        # PATCH (verificado 2026-07-27: attribute=0 mientras servida=1000).
        servida = (d.get("fulfillmentAvailability") or [])
        attr = ((d.get("attributes") or {}).get("fulfillment_availability") or [])
        cant_attr = attr[0].get("quantity") if attr else None
        cant_serv = servida[0].get("quantity") if servida else None
        estados = (d.get("summaries") or [{}])[0].get("status") or []
        if isinstance(estados, str):
            estados = [estados]
        return {
            "ok": True,
            "cantidad": cant_attr if cant_attr is not None else cant_serv,
            "cantidad_servida": cant_serv,
            "estados": [str(e).upper() for e in estados],
            "vendible": any(str(e).upper() == "BUYABLE" for e in estados),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"{type(exc).__name__}: {exc}"}


def _destinos(sku: str) -> list[dict[str, Any]]:
    """
    Publicaciones que DEBEN recibir el stock DROP: activas y no-FULL.

    Fuente: `channel.listings` (BD kubera), vía la gemela `channel_read`, que
    traduce a los nombres de columna de siempre (item_id, stock_real, es_full…).

    POR QUÉ NO `canal_inventario` (repunte 12-ago-2026). Esta función decide a
    qué publicaciones se les ESCRIBE stock en el marketplace, y lo decidía
    leyendo el espejo MySQL. El 12-ago se congeló ese espejo unas horas y el
    riesgo quedó a la vista: con una foto detenida, el fan-out le escribiría a
    publicaciones cerradas creyéndolas vivas y no vería las nuevas. Es la misma
    causa que dejó 964 pedidos fantasma ese día — leer de una tabla que ya no se
    escribe. Se lee de donde se escribe.

    Sin respaldo a MySQL a propósito: si kubera no responde, esto REVIENTA y el
    SKU no se sincroniza. Fallar es barato (el stock se propaga en la siguiente
    vuelta); escribirle stock equivocado a un marketplace, no.

    Devuelve también los descartes, para que la pantalla explique POR QUÉ un
    canal no recibió nada.
    """
    from services import channel_read
    # OJO: NO se filtra por item_id. En Mercado Libre el identificador es el
    # `item_id` (MLM…), pero en AMAZON es el PROPIO SKU (la Listings Items API
    # direcciona /items/{sellerId}/{sku}) y sus filas tienen item_id NULL.
    # Filtrar por item_id dejaba fuera el canal DROP más grande.
    # Se aplana el dict {sku: {canal|cuenta: fila}}: pedimos UN sku, así que
    # todo lo que vuelve es de él (y así el match no depende de mayúsculas —
    # `sku` es citext en kubera).
    filas = [f for por_sku in channel_read.leer_inventario([sku]).values()
             for f in por_sku.values()]
    salida: list[dict[str, Any]] = []
    for f in filas:
        situacion = (f.get("situacion") or "").lower()
        # `es_full` es la bandera CONFIABLE (logistic_type=fulfillment / canal AFN):
        # está poblada en los 3 canales. El stock en bodega es solo red de
        # seguridad — hay 319 publicaciones FULL con 0 piezas que el heurístico
        # por stock clasificaría mal (y les escribiríamos stock que ML no acepta).
        es_full = bool(f.get("es_full")) or int(f.get("stock_full") or 0) > 0 \
            or int(f.get("stock_fba") or 0) > 0
        canal = (f.get("canal") or "").lower()
        # Identificador de escritura: ML → item_id (MLM…); Amazon → el SKU.
        identificador = f.get("item_id") or (sku if canal == "amazon" else None)
        crudo = f.get("stock_real")
        stock_canal: int | None = None if crudo is None else int(crudo)
        motivo = None
        if es_full:
            motivo = "FULL/FBA (bodega del marketplace, no se toca)"
        elif canal == "mercado_libre" and situacion in _SITUACIONES_PAUSADAS:
            # DROP pausado: SÍ se sincroniza. Mandar `status` junto al stock
            # conserva la pausa (ver `_escribir_ml`). Sin ese blindaje ML la
            # reactivaría, que es lo que bloqueaba estas 2,278 publicaciones.
            pass
        elif canal == "tiktok":
            # TikTok no cabe en `_SITUACIONES_VIVAS`, y forzarlo habría sido un
            # error silencioso: su `situacion` es el veredicto de la AUDITORÍA
            # (APPROVED/FAILED/NONE/PRE_APPROVED) y quien dice si está a la venta
            # es `status` (ACTIVATE). Hoy APPROVED coincide con ACTIVATE en las
            # 900 publicaciones, pero es coincidencia del dato, no una regla:
            # meter "approved" en la lista de vivas ataba el fan-out a esa
            # casualidad. Se mira `estado_canal`, que es el campo que manda.
            if str(f.get("estado_canal") or "").upper() != "ACTIVATE":
                motivo = (f"status={f.get('estado_canal') or 'desconocido'} — "
                          "no está a la venta en TikTok")
        elif canal == "temu":
            # Temu es DROP-only (decisión 18-ago) y sus estados vienen CRUDOS
            # (`4/7`, `2/8`…): la tabla temu.ESTADOS solo distingue con certeza
            # Incompleto y Borrador (no publicados). La política acordada: a lo
            # PUBLICADO se le escribe stock aunque no se sepa si está activo o
            # inactivo — es bodega nuestra y no hay riesgo de sobreventa por
            # escribir de más. Un código NUNCA visto se omite (falla cerrada):
            # si Temu estrena estados, primero se decodifican.
            from services import temu as _temu
            cod = str(f.get("estado_canal") or "")
            etiqueta = _temu.ESTADOS.get(cod)
            if etiqueta in ("Incompleto", "Borrador"):
                motivo = f"Temu {cod} = {etiqueta} — no publicado"
            elif etiqueta is None:
                motivo = f"Temu status '{cod or '?'}' desconocido — no se escribe a ciegas"
        elif situacion not in _SITUACIONES_VIVAS:
            motivo = f"situacion={situacion or 'desconocida'} (escribirle la REACTIVARÍA)"
        elif not identificador:
            motivo = "sin identificador de publicación en el canal"
        elif canal == "amazon":
            # El caché NO sirve para Amazon (stock_real siempre NULL) y su
            # `situacion` viene de nuestra bitácora, no de Amazon. Se consulta
            # el listing EN VIVO: da la cantidad real y si está BUYABLE.
            vivo = _amazon_en_vivo(sku)
            if not vivo.get("ok"):
                motivo = f"Amazon ilegible: {vivo.get('motivo')} (no se escribe a ciegas)"
            elif not vivo.get("vendible"):
                motivo = (f"Amazon {'/'.join(vivo.get('estados') or ['sin estado'])}"
                          " — dormido, escribirle lo DESPERTARÍA")
            elif vivo.get("cantidad") is None:
                motivo = "Amazon sin fulfillmentAvailability legible (no se escribe a ciegas)"
            else:
                stock_canal = int(vivo["cantidad"])
        salida.append({
            "canal": f.get("canal"), "cuenta": f.get("cuenta"),
            "item_id": identificador,
            "stock_actual_canal": stock_canal,       # None = DESCONOCIDO (≠ 0)
            "omitido_por": motivo,
        })
    return salida


# ── Escritores por canal (solo se usan FUERA de dry-run) ─────────────────────

def _escribir_ml(cuenta: str, item_id: str, cantidad: int) -> tuple[bool, str]:
    """
    PUT del stock a una publicación de Mercado Libre.

    BLINDAJE DE LA PAUSA (28-jul). Escribir solo `available_quantity` a una
    publicación PAUSADA la REACTIVA — ML lo avisa ("se reactivaron porque hiciste
    cambios en su stock o estado"; pasó de verdad con CAM-0030 el 24-jul). Como
    Brandon pidió que TODAS se queden pausadas, eso dejaba 2,278 publicaciones
    DROP sin sincronizar.

    La salida: mandar `status` JUNTO con el stock en la MISMA petición. ML
    respeta el estado explícito y solo cambia la cantidad. Probado en las dos
    cuentas (28-jul): HTTP 200, `sub_status` sigue en `paused_by_seller` y el
    stock quedó actualizado.

    El estado se LEE antes de escribir, nunca se asume: mandar `paused` a ciegas
    PAUSARÍA una publicación activa, que sería el desastre opuesto. Si no se
    puede leer, se escribe sin `status` solo cuando ya sabíamos que estaba activa.
    """
    import httpx
    from services import meli
    token = meli._access_token(cuenta)
    if not token:
        return False, f"sin token de {cuenta}"
    cab = {"Authorization": f"Bearer {token}"}
    payload: dict[str, Any] = {"available_quantity": int(cantidad)}
    estado_previo = None
    try:
        g = httpx.get(f"https://api.mercadolibre.com/items/{item_id}",
                      headers=cab, params={"attributes": "status,sub_status"},
                      timeout=30.0)
        if g.status_code == 200:
            estado_previo = (g.json() or {}).get("status")
    except Exception:  # noqa: BLE001 — sin lectura se escribe sin `status`
        pass
    if estado_previo == "paused":
        payload["status"] = "paused"     # sin esto, ML la despierta
    try:
        r = httpx.put(
            f"https://api.mercadolibre.com/items/{item_id}",
            headers=cab, json=payload, timeout=30.0,
        )
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:120]}"
        if estado_previo != "paused":
            return True, "ok"
        # Verificación: si a pesar del blindaje despertó, se re-pausa YA.
        try:
            v = httpx.get(f"https://api.mercadolibre.com/items/{item_id}",
                          headers=cab, params={"attributes": "status"}, timeout=30.0)
            if v.status_code == 200 and (v.json() or {}).get("status") != "paused":
                httpx.put(f"https://api.mercadolibre.com/items/{item_id}",
                          headers=cab, json={"status": "paused"}, timeout=30.0)
                return True, "ok (se reactivó y se volvió a pausar)"
        except Exception:  # noqa: BLE001
            pass
        return True, "ok (pausa conservada)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _escribir_amazon(cuenta: str, sku: str, cantidad: int) -> tuple[bool, str]:
    """
    PATCH del stock a un listing de Amazon (MFN/DROP).

    En Amazon el identificador NO es un item_id: la Listings Items API direcciona
    /items/{sellerId}/{sku}. El stock vive en `fulfillment_availability` con el
    canal DEFAULT (= MFN, nuestro almacén). Los FBA no se tocan (los filtra
    `_destinos`): esa bodega la administra Amazon.
    """
    import httpx

    # Mismo helper que la lectura: maneja el caso "ya hay event loop corriendo"
    # (el `asyncio.run` directo reventaba ahí — auditoría 27-jul).
    token = _token_amazon()
    if not token:
        return False, "sin token de Amazon"
    # El productType REAL del listing: la Listings API lo exige y un valor
    # genérico ("PRODUCT") puede rechazarse o alterar la ficha. Se lee del
    # propio listing antes de escribir.
    tipo = "PRODUCT"
    try:
        rg = httpx.get(
            f"{settings.amazon_sp_api_endpoint}/listings/2021-08-01/items/"
            f"{settings.amazon_seller_id}/{sku}",
            params={"marketplaceIds": settings.amazon_marketplace_id,
                    "includedData": "summaries"},
            headers={"x-amz-access-token": token}, timeout=25.0)
        if rg.status_code == 200:
            tipo = ((rg.json().get("summaries") or [{}])[0].get("productType") or "PRODUCT")
    except Exception:  # noqa: BLE001
        pass
    try:
        r = httpx.patch(
            f"{settings.amazon_sp_api_endpoint}/listings/2021-08-01/items/"
            f"{settings.amazon_seller_id}/{sku}",
            params={"marketplaceIds": settings.amazon_marketplace_id},
            headers={"x-amz-access-token": token},
            json={"productType": tipo, "patches": [{
                "op": "replace",
                "path": "/attributes/fulfillment_availability",
                "value": [{"fulfillment_channel_code": "DEFAULT",
                           "quantity": int(cantidad)}],
            }]},
            timeout=30.0,
        )
        if r.status_code in (200, 202):
            estado = (r.json() or {}).get("status", "")
            if str(estado).upper() == "INVALID":
                return False, f"Amazon rechazó: {str(r.json().get('issues'))[:120]}"
            return True, f"ok ({estado or r.status_code})"
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _en_hilo(corutina_factory, etiqueta: str, timeout: int = 60):
    """
    Corre una corrutina desde contexto SÍNCRONO, haya o no un loop activo.

    Es el mismo puente que `_token_amazon` documentó tras la auditoría del
    27-jul: `asyncio.run()` revienta si YA hay un event loop corriendo, y el
    fan-out corre dentro del AsyncIOScheduler. Extraído para no tenerlo escrito
    dos veces con la mitad del comentario.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(corutina_factory())
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(corutina_factory())).result(timeout=timeout)


def _escribir_tiktok(cuenta: str, item_id: str, cantidad: int) -> tuple[bool, str]:
    """
    Actualiza el stock de UN producto en TikTok Shop.

    DOS COSAS QUE TIKTOK EXIGE Y QUE NO SE PUEDEN ADIVINAR:

    1. **El identificador de la variante (`sku_id`), no el `seller_sku`.** El
       endpoint es `/product/202309/products/{product_id}/inventory/update` y
       dentro pide `skus[].id`, que es un id propio de TikTok. `channel.listings`
       no tiene columna para guardarlo (el censo sí lo traía), así que se lee del
       propio producto antes de escribir — una llamada más, pero siempre
       correcta: si TikTok recreó la variante, el id nuevo se toma solo.
    2. **El `warehouse_id` de VENTAS.** Hay dos almacenes y el otro es el de
       devoluciones; escribirle stock a ese no da error y no vende nada.
    """
    from services import tiktok as tk

    if not item_id:
        return False, "sin product_id de TikTok"
    token, cipher = tk.access_token(), tk.cipher()
    if not (token and cipher):
        return False, "TikTok sin token o sin shop_cipher"
    try:
        detalle = _en_hilo(
            lambda: tk.llamar(f"/product/202309/products/{item_id}", token,
                              {"shop_cipher": cipher}),
            "detalle tiktok")
        skus = detalle.get("skus") or []
        if not skus:
            return False, "el producto no tiene variantes en TikTok"
        sku_id = str(skus[0].get("id") or "")
        if not sku_id:
            return False, "la variante de TikTok no trae id"
        cuerpo = {"skus": [{"id": sku_id,
                            "inventory": [{"warehouse_id": _ALMACEN_VENTAS_TIKTOK,
                                           "quantity": int(cantidad)}]}]}
        _en_hilo(
            lambda: tk.llamar(f"/product/202309/products/{item_id}/inventory/update",
                              token, {"shop_cipher": cipher}, cuerpo, "POST"),
            "inventario tiktok")
        return True, f"ok ({cantidad})"
    except Exception as exc:  # noqa: BLE001
        # `tiktok.llamar` ya traduce el `code` del cuerpo a excepción: TikTok
        # responde HTTP 200 aunque haya fallado, y confundirlos es el error
        # clásico con esta API.
        return False, f"{type(exc).__name__}: {exc}"


# El de VENTAS. El otro almacén de la tienda es el de DEVOLUCIONES: escribirle
# stock no da error y no vende nada.
_ALMACEN_VENTAS_TIKTOK = "7647893424175580935"

# Convergencia de Temu: su lectura va con retraso, así que cada escritura se
# verifica y se corrige. 8 s cubre con margen los ~5 s medidos; 3 intentos
# bastan para un desfase y su corrección sin volverse un bucle.
_TEMU_ESPERA_S = 8.0
_TEMU_INTENTOS = 3


def _escribir_temu(cuenta: str, item_id: str, cantidad: int) -> tuple[bool, str]:
    """
    Stock a UN producto de Temu (canal DROP-only, decisión 18-ago).

    LO QUE EL SONDEO CANARIO DEJÓ ESCRITO (18-ago, ACC-0017-MUL — la primera
    escritura de stock a Temu en la historia del proyecto):

    1. `bg.local.goods.stock.edit` edita por DIFERENCIA (`stockDiff`), NO por
       valor absoluto — lo contrario del contrato del fan-out. Se vuelve
       absoluto LEYENDO el stock vivo justo antes: `bg.local.goods.list.query`
       con `goodsIdList` filtra a UN goods SIN `goodsSearchType` (pasarlo con
       la cubeta equivocada devuelve vacío en silencio). diff = objetivo − vivo.
    2. La respuesta trae DOS veredictos y se exigen AMBOS: `operateResult`
       (global) y `skuStockEditStatusInfoList[].stockEditStatus` (por SKU).
    3. **La lectura es EVENTUALMENTE CONSISTENTE, en los dos sentidos.** Medido
       en el canario: tras bajar 100→99 la lectura siguió contestando 100
       varios segundos, y tras subir tardó ~5 s en mostrarlo. Un diff calculado
       sobre una lectura rancia escribe un valor equivocado — y si la lectura
       rancia coincide con el objetivo, ni siquiera escribe (pasó: el canario
       se quedó en 99 creyendo que tenía 100). Por eso cada operación CONVERGE:
       se escribe, se espera, se relee y se corrige el resto, hasta
       `_TEMU_INTENTOS` veces. Es lento a propósito; el stock mal escrito sale
       más caro que unos segundos.
    4. La carrera venta-entre-lectura-y-escritura se autocorrige: esa venta
       regresa por pedidos (M2E) y dispara otra pasada con el Woo ya nuevo.
    """
    import time as _t

    from services import temu as tm

    if not item_id:
        return False, "sin goodsId de Temu"
    if not tm.disponible():
        return False, "Temu no configurado (faltan TEMU_*)"
    try:
        goods_id = int(str(item_id))
    except (TypeError, ValueError):
        return False, f"goodsId ilegible: {item_id!r}"

    def _leer() -> tuple[int | None, list, str | None]:
        res = _en_hilo(
            lambda: tm.llamar("bg.local.goods.list.query",
                              {"pageNo": 1, "pageSize": 10,
                               "goodsIdList": [goods_id]}),
            "lectura temu")
        lote = res.get("goodsList") or []
        fila = next((g for g in lote
                     if str(g.get("goodsId")) == str(goods_id)), None)
        if fila is None:
            return None, [], "el goods no aparece en el listado (¿eliminado?)"
        return fila.get("quantity"), (fila.get("skuIdList") or []), None

    objetivo = int(cantidad)
    try:
        actual, ids, err = _leer()
        if err:
            return False, err
        if actual is None or not ids:
            return False, "listado sin quantity o sin skuIdList (no se escribe a ciegas)"
        if len(ids) > 1:
            # Multi-variante: repartir el stock DROP entre variantes no está
            # definido (hoy 151/152 del catálogo tienen 1 sola). Falla cerrada.
            return False, f"{len(ids)} variantes — repartir stock no está definido"
        sku_id, inicial, escrituras = int(ids[0]), int(actual), 0

        for intento in range(_TEMU_INTENTOS):
            diff = objetivo - int(actual)
            if diff != 0:
                r = _en_hilo(
                    lambda d=diff: tm.llamar("bg.local.goods.stock.edit", {
                        "goodsId": goods_id,
                        "skuStockChangeList": [{"skuId": sku_id, "stockDiff": d}]}),
                    "stock temu")
                por_sku = (r.get("skuStockEditStatusInfoList") or [{}])[0]
                if not (r.get("operateResult") and por_sku.get("stockEditStatus")):
                    return False, (f"Temu no aplicó: operateResult={r.get('operateResult')} "
                                   f"skuStatus={por_sku.get('stockEditStatus')} "
                                   f"{por_sku.get('errorMsg') or ''}")
                escrituras += 1
            # Verificación SIEMPRE, incluso si el diff dio 0: la lectura que lo
            # calculó pudo venir rancia.
            _t.sleep(_TEMU_ESPERA_S)
            actual, _ids, err = _leer()
            if err:
                return False, f"escrito, pero no se pudo verificar: {err}"
            if actual is not None and int(actual) == objetivo:
                if escrituras == 0:
                    return True, f"ok (ya tenía {objetivo} en vivo)"
                return True, (f"ok ({inicial}→{objetivo}"
                              + (f", {escrituras} ajustes)" if escrituras > 1 else ")"))
        return False, (f"no convergió tras {_TEMU_INTENTOS} intentos: "
                       f"quedó en {actual}, objetivo {objetivo}")
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

# ARQUITECTURA DE CANALES (decisión de Brandon, 18-ago-2026):
#   · ML, Amazon y Walmart se manejan con su fulfillment (FULL / FBA / WFS) —
#     esas bodegas las administra cada plataforma y este fan-out no las toca.
#     AMAZON está FUERA de FANOUT_CANALES a propósito: no se le alimenta stock
#     (su escritor queda solo para el one-shot de limpieza
#     scripts/apagar_amazon_fantasma.py). Las DROP pausadas de ML sí siguen
#     recibiendo stock (higiene para el día que alguna reactive).
#   · TikTok y Temu (después SHEIN) son ÚNICAMENTE DROP: el destino real.
_ESCRITORES = {"mercado_libre": _escribir_ml, "amazon": _escribir_amazon,
               "tiktok": _escribir_tiktok, "temu": _escribir_temu}


# ── Núcleo: calcular el plan de un SKU ───────────────────────────────────────

def plan(sku: str) -> dict[str, Any]:
    """
    Qué haría el fan-out con este SKU AHORA MISMO. No escribe ni encola nada:
    es lo que consume el dry-run, el endpoint de simulación y el worker.
    """
    stock = _stock_drop(sku)
    if stock is None:
        return {"sku": sku, "ok": False, "motivo": "sin stock legible en WooCommerce",
                "acciones": []}
    objetivo = max(0, stock - _reserva())
    canales_ok = _canales_activos()
    acciones: list[dict[str, Any]] = []
    for d in _destinos(sku):
        accion = dict(d)
        accion["objetivo"] = objetivo
        if d["omitido_por"]:
            accion["accion"] = "omitir"
        elif d["stock_actual_canal"] is None:
            # DESCONOCIDO ≠ 0. Escribir sin saber qué tiene el canal fue el bug
            # que hacía que el dry-run reportara "Amazon = 0" en 1,614 SKUs.
            accion["accion"] = "omitir"
            accion["omitido_por"] = "stock del canal DESCONOCIDO (no se escribe a ciegas)"
        elif d["stock_actual_canal"] == objetivo:
            accion["accion"] = "sin_cambio"
            accion["omitido_por"] = f"el canal ya tiene {objetivo}"
        elif canales_ok is not None and (d["canal"] or "").lower() not in canales_ok:
            accion["accion"] = "omitir"
            accion["omitido_por"] = f"canal '{d['canal']}' no habilitado en FANOUT_CANALES"
        elif (d["canal"] or "").lower() == "tiktok" and not settings.fanout_tiktok:
            # Candado propio, además de FANOUT_CANALES. El escritor ESTÁ hecho y
            # probado; lo que falta es la decisión de encenderlo, y encender
            # escrituras a un marketplace vivo no puede ser efecto secundario de
            # un deploy.
            accion["accion"] = "omitir"
            accion["omitido_por"] = ("FANOUT_TIKTOK apagado — el escritor está "
                                     "listo, falta encenderlo")
        elif (d["canal"] or "").lower() == "temu" and not settings.fanout_temu:
            # Mismo candado que TikTok. El escritor existe desde el sondeo
            # canario del 18-ago (`_escribir_temu`); encenderlo es un acto
            # explícito, nunca efecto secundario de un deploy.
            accion["accion"] = "omitir"
            accion["omitido_por"] = ("FANOUT_TEMU apagado — el escritor está "
                                     "listo, falta encenderlo")
        elif (d["canal"] or "").lower() not in _ESCRITORES:
            accion["accion"] = "omitir"
            accion["omitido_por"] = f"sin escritor implementado para '{d['canal']}'"
        else:
            accion["accion"] = "escribir"
        acciones.append(accion)
    return {"sku": sku, "ok": True, "stock_drop": stock, "reserva": _reserva(),
            "objetivo": objetivo, "acciones": acciones}


_schema_listo = False


def _asegurar_schema() -> None:
    """
    Tabla LOCAL de bitácora del fan-out (MySQL kubera_ml).

    Local A PROPÓSITO, igual que `espejo_kubera_log`: es operación del panel, NO
    entra en la migración a la BD centralizada. Sin esto, cada deploy de Railway
    reinicia el proceso y se pierde todo el historial del dry-run.
    """
    global _schema_listo
    if _schema_listo:
        return
    from services import db
    try:
        with db.get_cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fanout_log (
                    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
                    ts          DATETIME NOT NULL,
                    sku         VARCHAR(100) NOT NULL,
                    motivo      VARCHAR(160),
                    dry_run     TINYINT(1) NOT NULL DEFAULT 1,
                    stock_drop  INT,
                    objetivo    INT,
                    canal       VARCHAR(40),
                    cuenta      VARCHAR(40),
                    item_id     VARCHAR(64),
                    accion      VARCHAR(20),
                    stock_canal INT,
                    resultado   VARCHAR(255),
                    ms          DECIMAL(10,1),
                    INDEX idx_fanout_ts (ts),
                    INDEX idx_fanout_sku (sku),
                    INDEX idx_fanout_accion (accion, ts)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        _schema_listo = True
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_log: no se pudo asegurar el schema: %s", exc)


def _persistir(evento: dict[str, Any]) -> None:
    """Guarda una fila por ACCIÓN (así el dashboard filtra por canal/resultado)."""
    from services import db
    _asegurar_schema()
    try:
        filas = [
            (evento["ts_dt"], evento["sku"], (evento.get("motivo") or "")[:160],
             1 if evento["dry_run"] else 0, evento.get("stock_drop"),
             evento.get("objetivo"), a.get("canal"), a.get("cuenta"),
             str(a.get("item_id") or "")[:64], a.get("accion"),
             a.get("stock_actual_canal"),
             str(a.get("resultado") or a.get("omitido_por") or "")[:255],
             evento.get("ms"))
            for a in (evento.get("acciones") or [])
        ] or [(evento["ts_dt"], evento["sku"], (evento.get("motivo") or "")[:160],
               1 if evento["dry_run"] else 0, evento.get("stock_drop"),
               evento.get("objetivo"), None, None, "", "sin_destinos", None,
               str(evento.get("detalle") or "sin publicaciones vivas")[:255],
               evento.get("ms"))]
        with db.get_cursor() as cur:
            cur.executemany(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                    item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", filas)
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_log: no se pudo persistir %s: %s", evento.get("sku"), exc)


def _aplicar(sku: str, motivo: str) -> None:
    """Calcula el plan y (si no es dry-run) lo ejecuta. Registra siempre."""
    inicio = time.time()
    p = plan(sku)
    simulacion = dry_run()
    resultados: list[dict[str, Any]] = []
    for a in p.get("acciones", []):
        if a["accion"] != "escribir":
            _contadores["sin_cambio" if a["accion"] == "sin_cambio" else (
                "omitidos_full" if "FULL" in (a.get("omitido_por") or "")
                else "omitidos_pausados")] += 1
            resultados.append(a)
            continue
        if simulacion:
            _contadores["simuladas"] += 1
            resultados.append({**a, "resultado": "DRY-RUN (no se escribió)"})
            continue
        escritor = _ESCRITORES[(a["canal"] or "").lower()]
        ok, det = escritor(a["cuenta"], a["item_id"], a["objetivo"])
        _contadores["escrituras" if ok else "errores"] += 1
        resultados.append({**a, "resultado": ("ok" if ok else f"ERROR: {det}")})

    _contadores["procesados"] += 1
    ahora = datetime.now(timezone.utc)
    evento = {
        "ts": ahora.isoformat(timespec="seconds"),
        "ts_dt": ahora.replace(tzinfo=None),
        "sku": sku, "motivo": motivo, "dry_run": simulacion,
        "stock_drop": p.get("stock_drop"), "objetivo": p.get("objetivo"),
        "ok": p.get("ok"), "detalle": p.get("motivo"),
        "acciones": resultados, "ms": round((time.time() - inicio) * 1000, 1),
    }
    _eventos.appendleft({k: v for k, v in evento.items() if k != "ts_dt"})
    _persistir(evento)   # sobrevive a los deploys (el ring buffer no)
    escrituras = sum(1 for r in resultados if r.get("accion") == "escribir")
    log.info("fan-out %s%s: stock=%s objetivo=%s → %d destino(s) a escribir",
             sku, " [DRY-RUN]" if simulacion else "", p.get("stock_drop"),
             p.get("objetivo"), escrituras)


# ── Cola con debounce ────────────────────────────────────────────────────────

def _worker() -> None:
    """Drena los SKUs que ya 'reposaron' su ventana de debounce."""
    while True:
        try:
            time.sleep(_TICK_S)
            ahora = time.time()
            listos: list[tuple[str, str]] = []
            with _lock:
                for sku, info in list(_pendientes.items()):
                    if info["listo_en"] <= ahora:
                        listos.append((sku, info["motivo"]))
                        _pendientes.pop(sku, None)
            for sku, motivo in listos:
                try:
                    _aplicar(sku, motivo)
                except Exception as exc:  # noqa: BLE001
                    _contadores["errores"] += 1
                    log.warning("fan-out %s falló: %s", sku, exc)
        except Exception as exc:  # noqa: BLE001 — el worker NUNCA muere
            log.warning("worker de fan-out: %s", exc)


def _asegurar_worker() -> None:
    global _worker_iniciado
    if not _worker_iniciado:
        with _lock:
            if not _worker_iniciado:
                threading.Thread(target=_worker, daemon=True,
                                 name="fanout-stock").start()
                _worker_iniciado = True


def encolar(sku: str, motivo: str = "venta") -> None:
    """
    Pide replicar el stock DROP de `sku` a los canales. Fire-and-forget: solo
    encola y regresa — el camino crítico de la venta NUNCA se bloquea ni falla
    por esto. Re-encolar el mismo SKU dentro de la ventana solo REINICIA el
    debounce (las ráfagas se colapsan en una escritura).
    """
    try:
        if not habilitado() or not (sku or "").strip():
            return
        _asegurar_worker()
        with _lock:
            _pendientes[sku.strip()] = {"listo_en": time.time() + DEBOUNCE_S,
                                        "motivo": motivo}
            _contadores["encolados"] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("fan-out encolar(%s): %s", sku, exc)


def encolar_varios(skus: list[str], motivo: str = "venta") -> None:
    for s in skus or []:
        encolar(s, motivo)


# ── Monitoreo ────────────────────────────────────────────────────────────────

def historial(limite: int = 100, solo_errores: bool = False) -> list[dict[str, Any]]:
    """Bitácora PERSISTIDA (sobrevive deploys). Es lo que pinta el dashboard."""
    from services import db
    _asegurar_schema()
    where = "WHERE resultado LIKE 'ERROR%%'" if solo_errores else ""
    try:
        return db.fetch_all(
            f"""SELECT ts, sku, motivo, dry_run, stock_drop, objetivo, canal,
                       cuenta, item_id, accion, stock_canal, resultado, ms
                FROM fanout_log {where} ORDER BY id DESC LIMIT %s""",
            (int(limite),))
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_log historial: %s", exc)
        return []


def resumen() -> dict[str, Any]:
    """Totales acumulados desde la tabla (no desde memoria)."""
    from services import db
    _asegurar_schema()
    try:
        por_accion = db.fetch_all(
            "SELECT accion, COUNT(*) n FROM fanout_log GROUP BY accion")
        por_canal = db.fetch_all(
            """SELECT canal, accion, COUNT(*) n FROM fanout_log
               WHERE canal IS NOT NULL GROUP BY canal, accion""")
        tot = db.fetch_one(
            """SELECT COUNT(*) eventos, COUNT(DISTINCT sku) skus,
                      MIN(ts) desde, MAX(ts) hasta,
                      SUM(resultado LIKE 'ERROR%%') errores FROM fanout_log""") or {}
        return {"por_accion": {r["accion"]: r["n"] for r in por_accion},
                "por_canal": por_canal, **tot}
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_log resumen: %s", exc)
        return {}


def estado() -> dict[str, Any]:
    with _lock:
        pendientes = sorted(_pendientes.keys())
    return {
        "habilitado": habilitado(),
        "dry_run": dry_run(),
        "canales_habilitados": sorted(_canales_activos()) if _canales_activos() else "todos",
        "escritores_implementados": sorted(_ESCRITORES.keys()),
        "reserva": _reserva(),
        "debounce_s": DEBOUNCE_S,
        "pendientes": pendientes,
        "contadores": dict(_contadores),
        "eventos": list(_eventos)[:50],
        "resumen": resumen(),           # acumulado persistido (sobrevive deploys)
        "historial": historial(60),     # bitácora para el dashboard
    }
