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
  FANOUT_VERIFICAR_RANCIO   default False — un «sin cambio» de TikTok/Temu tras
                   una escritura nuestra reciente con otro número se verifica en
                   vivo (`_rancios`); ventana en FANOUT_VERIFICAR_RANCIO_H (2 h; 0 =
                   no verifica). FANOUT_VERIFICAR_RANCIO_ENSAYO sólo anota lo que
                   verificaría, sin llamar al canal.
  FANOUT_PRIORIDAD / FANOUT_TEMU_VERIFICAR_DIFERIDO / FANOUT_WORKERS
                   la cola rápida (ver «Cola con debounce»): las tres apagadas = la
                   cola de siempre.
"""
from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.fanout")
_ARRANQUE = time.time()     # cuándo arrancó este proceso (el recuperador lo usa al arrancar)

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

_pendientes: dict[str, dict[str, Any]] = {}   # sku → {listo_en, motivo, desde}
_lock = threading.Lock()
_worker_iniciado = False
# Lo que la cola ya sacó de `_pendientes` y todavía no termina. Antes el lote vivía en
# una lista LOCAL del worker y nadie lo veía: el 12-oct el panel decía «Cola 57» con
# ~50 SKUs más en el lote, la matriz los rotulaba «cambio perdido» y el recuperador
# volvió a encolar 5 que seguían ahí. Sólo contabilidad: el orden no cambia.
_lote: list[tuple[str, str, float]] = []      # (sku, motivo, desde), en el orden en que se procesarán
_en_proceso: dict[str, dict[str, Any]] = {}   # sku → {motivo, desde, inicio, hilo}
_duraciones: deque[float] = deque(maxlen=40)  # segundos reales por SKU (para la ETA)
_eventos: deque[dict[str, Any]] = deque(maxlen=_EVENTOS_MAX)
_contadores: dict[str, int] = {
    "encolados": 0, "procesados": 0, "escrituras": 0, "simuladas": 0,
    "sin_cambio": 0, "errores": 0, "omitidos_full": 0, "omitidos_pausados": 0,
    "verificados": 0,
}


# ── Configuración ────────────────────────────────────────────────────────────

def habilitado() -> bool:
    return bool(getattr(settings, "fanout_enabled", False))


def dry_run() -> bool:
    """True = NO escribe en los canales, solo registra lo que haría."""
    return bool(getattr(settings, "fanout_dry_run", True))


def seguro_encendido() -> bool:
    """
    ¿Corre el seguro «stock 0 ⇒ fuera de la venta» (`services/fanout_seguro.py`)?

    SIEMPRE que el fan-out esté encendido. No tiene interruptor propio (decisión de
    Brandon, 9-oct-2026: «quitas la variable para inactivar o reactivar
    automáticamente; éste deberá de estar siempre encendido»): la variable
    FANOUT_CERO_ENABLED ya no existe. Es la ÚNICA pregunta que hacen `_destinos`,
    `_seguro`, el worker, los censos de Temu y TikTok, y `pedidos_ml`; quien pruebe
    otra cosa y no quiera al seguro en medio, suplanta esta función.
    """
    return habilitado()


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


_WORKERS_MAX = 3


def _prioridad() -> bool:
    """FANOUT_PRIORIDAD: las bajadas a la venta primero (ver `_clasificar`)."""
    return bool(getattr(settings, "fanout_prioridad", False))


def _temu_diferido() -> bool:
    """FANOUT_TEMU_VERIFICAR_DIFERIDO: la cola no espera la relectura de Temu."""
    return bool(getattr(settings, "fanout_temu_verificar_diferido", False))


def _num_workers() -> int:
    """FANOUT_WORKERS entre 1 y 3. Ilegible = 1, la cola de siempre."""
    try:
        return max(1, min(_WORKERS_MAX, int(getattr(settings, "fanout_workers", 1) or 1)))
    except (TypeError, ValueError):
        return 1


def _usa_picker() -> bool:
    """¿El worker toma de a un SKU (`_tomar`) en vez de vaciar el lote de golpe?"""
    return _prioridad() or _num_workers() > 1


def cola_rapida() -> bool:
    """¿Está encendida alguna pieza de la cola rápida? Con las tres apagadas todo es
    como siempre (también el recuperador, que sólo entonces mira la cola completa)."""
    return _usa_picker() or _temu_diferido()


# ── Lectura de la VERDAD (WooCommerce vía MySQL directo) ─────────────────────

def _stock_drop(sku: str) -> int | None:
    """
    Stock DROP del SKU: el número que el fan-out reparte. Lo elige `FANOUT_FUENTE`:

    · `woo` (por omisión) — `_stock` en WooCommerce. Se lee por MySQL directo
      (wp_db) a propósito: la REST de Woo devuelve 403 intermitente por el CDN de
      Hostinger (pendiente #1) y un fallo de lectura NUNCA debe convertirse en un
      stock inventado.
    · `kubera` — el libro de kubera (`services/stock_libro.py`): libre vendible de
      las bodegas del libro menos las ventas DROP sin OV; un SKU que el libro no
      trae da 0 (D4). Si el libro no se puede leer LANZA `LibroNoDisponible`
      —igual que una caída de WPDB—: nunca cae a Woo ni devuelve un 0 inventado.
      Una errata en la variable también lanza.

    `None` = «sin número» (sólo con Woo): el fan-out no toca ese SKU.
    """
    from services import stock_libro
    if stock_libro.fuente_fanout() == "kubera":
        return stock_libro.numero(sku)
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
            # TikTok no cabe en `_SITUACIONES_VIVAS`: su `situacion` es el
            # veredicto de la AUDITORÍA (APPROVED/FAILED/…) y quien dice si está
            # a la venta es `status`. Se mira `estado_canal`, que es el campo
            # que manda.
            #
            # DESDE EL 19-ago SE SINCRONIZA TODO, no solo lo ACTIVATE (pedido de
            # Brandon: 100% de trackeo de stock). Un borrador con stock rancio es
            # una sobreventa esperando el día que alguien lo publique —
            # `tk_activar.py` activa ~300/día desde el escritorio. Canario en
            # vivo antes de cambiarlo (TEC-2187-MET, 52→0): el producto siguió
            # en DRAFT, escribir inventario NO lo activa. Es la misma decisión
            # que con las pausadas de ML, y por la misma razón.
            #
            # DELETED queda fuera: el producto ya no existe y escribirle solo
            # produce errores.
            est = str(f.get("estado_canal") or "").upper()
            if est == "DELETED":
                motivo = "status=DELETED — el producto ya no existe en TikTok"
        elif canal == "temu":
            # Temu es DROP-only (decisión 18-ago) y sus estados vienen CRUDOS
            # (`4/7`, `2/8`…). Hasta el 19-ago solo se escribía a lo publicado;
            # AHORA SE SINCRONIZA TODO (mismo pedido de 100% de trackeo que en
            # TikTok, y mismo canario: TEC-2187-MET 52→0 en estado Incompleto,
            # sin que Temu lo publicara). Un Incompleto con stock viejo es una
            # sobreventa dormida: el día que alguien lo complete, sale a vender
            # con la cifra de hace semanas.
            #
            # Ya no hay falla cerrada por estado desconocido: el peor caso de
            # escribirle a un estado raro es un error registrado, y el peor caso
            # de NO escribirle es vender lo que no existe.
            #
            # EXCEPCIÓN medida el 19-ago: los BORRADORES (`5/None`) NO aceptan
            # `stock.edit` — Temu contesta «Goods commit have not submitted».
            # No es una precaución nuestra: es una negativa del canal, así que
            # se omiten con motivo en vez de acumular errores en cada venta.
            # (Los Incompletos `2/8`/`3/2`/`3/3` SÍ aceptan: canario TEC-2187-MET.)
            from services import temu as _temu
            cod = str(f.get("estado_canal") or "")
            if _temu.ESTADOS.get(cod) == "Borrador":
                motivo = (f"Temu {cod} = Borrador — sin enviar, el canal rechaza "
                          "editarle stock")
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
        if seguro_encendido():
            # Para el seguro «stock 0 ⇒ fuera de la venta» (`fanout_seguro`): el
            # estado que vio el censo (su prefiltro sin red) y si el descarte es
            # de ESTE nivel (FULL, borrada, borrador), que es lo único que el
            # seguro no mira. `plan()` reusa `omitido_por` para sus propias
            # guardas, así que sin esta bandera no se distinguen. Con el fan-out
            # apagado (y con él el seguro) el destino sale como siempre: ni una
            # llave de más en `/simular`, en `/estado` ni en la respuesta de `bajar`.
            salida[-1].update(estado_canal=f.get("estado_canal"), fuera=bool(motivo))
    return salida


# ── Escritores por canal (solo se usan FUERA de dry-run) ─────────────────────

# Lo que contestan los escritores con `solo_bajar` (excedentes) cuando el canal YA
# tiene lo mismo o menos que el objetivo: no escribieron nada, a propósito.
NO_BAJA = "no se baja"
# Lo que contestan cuando el canal YA tenía el objetivo en vivo y no escribieron
# nada (Temu siempre relee; TikTok, sólo con `verificar`).
YA_TENIA = "ok (ya tenía"

# Freno por canal: una cubeta de fichas por canal (FANOUT_RITMO_* llamadas por MINUTO,
# más una ráfaga de `_RITMO_RAFAGA`: las llamadas seguidas de UN SKU, p. ej. GET+PUT+GET a
# una pausada de ML en las dos cuentas), compartida por TODOS los hilos que le hablan al canal
# desde aquí: los de la cola, el verificador de Temu, el seguro y los excedentes (los
# censos y los sondeos de pedidos NO: gastan la misma cuota por su lado). Por minuto y no
# «una llamada cada X s» porque así se midió lo único que sabemos de los límites (60 días,
# 0 errores de límite) y porque un intervalo fijo frenaba hasta al hilo único: en la
# simulación de S41906, las 3 llamadas seguidas de cada escritura a una pausada de ML
# costaban 2 min. Con la cola rápida el freno se SOSTIENE toda la ráfaga: con los valores
# por omisión (Temu 29, TikTok 27) el freno más su ráfaga no pasa en 10 min de lo visto
# sostenido sin error (Temu 29.6, TikTok 28.4 llamadas/min, contadas con las llamadas
# reales por escritura); ver config.py.
# Sólo frena cuando la cola va más rápido que hoy —Temu diferido o varios hilos—: con las
# banderas apagadas, o sólo con la prioridad (que cambia el orden, no el paso), el ritmo es
# el de siempre y no se le agrega ni una espera.
_RITMO_AJUSTE = {"temu": "fanout_ritmo_temu", "tiktok": "fanout_ritmo_tiktok",
                 "mercado_libre": "fanout_ritmo_ml"}
_RITMO_RAFAGA = 6.0
_ritmo_lock = threading.Lock()
_ritmo_cubeta: dict[str, tuple[float, float]] = {}   # canal → (fichas, epoch de la última cuenta)


def _canal_de(etiqueta: str) -> str | None:
    e = (etiqueta or "").lower()
    return "temu" if "temu" in e else "tiktok" if "tiktok" in e else None


def _ritmo(canal: str | None) -> None:
    """Toma una ficha del canal antes de llamarlo (con Temu diferido o varios hilos). Sin
    fichas, espera la suya: las fichas negativas son turnos ya apartados por otros hilos."""
    if not canal or not (_temu_diferido() or _num_workers() > 1):
        return
    try:
        por_s = float(getattr(settings, _RITMO_AJUSTE.get(canal, ""), 0) or 0) / 60.0
    except (TypeError, ValueError):
        por_s = 0.0
    if por_s <= 0:
        return
    with _ritmo_lock:
        ahora = time.time()
        fichas, cuenta = _ritmo_cubeta.get(canal, (_RITMO_RAFAGA, ahora))
        fichas = min(_RITMO_RAFAGA, fichas + max(0.0, ahora - cuenta) * por_s) - 1.0
        _ritmo_cubeta[canal] = (fichas, ahora)
    if fichas < 0:
        time.sleep(-fichas / por_s)


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
        _ritmo("mercado_libre")
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
        _ritmo("mercado_libre")
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
            _ritmo("mercado_libre")
            v = httpx.get(f"https://api.mercadolibre.com/items/{item_id}",
                          headers=cab, params={"attributes": "status"}, timeout=30.0)
            if v.status_code == 200 and (v.json() or {}).get("status") != "paused":
                _ritmo("mercado_libre")
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
    _ritmo(_canal_de(etiqueta))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(corutina_factory())
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(corutina_factory())).result(timeout=timeout)


def _escribir_tiktok(cuenta: str, item_id: str, cantidad: int,
                     solo_bajar: bool = False, verificar: bool = False) -> tuple[bool, str]:
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

    Con `solo_bajar` (excedentes) se relee ese almacén en vivo y no se escribe si
    TikTok ya tiene lo mismo o menos: subir liberaría lo que el canal apartó.

    Con `verificar` («sin cambio» rancio, `_rancios`) se compara la cantidad viva de
    ese almacén —viene en el mismo GET, sin llamada extra— y no se escribe si ya es
    el objetivo; con los dos (la verificación cuando lo último que le dejamos era
    MÁS), manda `solo_bajar`. Sin ellos, la escritura es absoluta y a ciegas, como
    siempre.
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
        vivo = None
        if solo_bajar or verificar:
            vivo = next((i.get("quantity") for i in (skus[0].get("inventory") or [])
                         if str(i.get("warehouse_id") or "") == _ALMACEN_VENTAS_TIKTOK), None)
            if vivo is None:
                return False, ("stock vivo de TikTok ilegible (no se baja a ciegas)" if solo_bajar
                               else "stock vivo de TikTok ilegible (no se verifica a ciegas)")
            if solo_bajar and int(vivo) <= int(cantidad):
                return True, f"{NO_BAJA}: TikTok tiene {int(vivo)} en vivo (objetivo {int(cantidad)})"
            if verificar and int(vivo) == int(cantidad):
                return True, f"{YA_TENIA} {int(cantidad)} en vivo)"
        cuerpo = {"skus": [{"id": sku_id,
                            "inventory": [{"warehouse_id": _ALMACEN_VENTAS_TIKTOK,
                                           "quantity": int(cantidad)}]}]}
        _en_hilo(
            lambda: tk.llamar(f"/product/202309/products/{item_id}/inventory/update",
                              token, {"shop_cipher": cipher}, cuerpo, "POST"),
            "inventario tiktok")
        if verificar:
            return True, f"ok ({int(vivo)}→{int(cantidad)})"
        return True, f"ok ({cantidad})"
    except Exception as exc:  # noqa: BLE001
        # `tiktok.llamar` ya traduce el `code` del cuerpo a excepción: TikTok
        # responde HTTP 200 aunque haya fallado, y confundirlos es el error
        # clásico con esta API.
        #
        # 12052901 = «Operation Not Allowed» sobre el producto. Se resume a una
        # línea legible: el texto original ocupa 300 caracteres y el `resultado`
        # de fanout_log corta en 255, así que el motivo real quedaba fuera.
        # Medido el 19-ago en 3 productos que el GET reporta en DRAFT y que aun
        # así el canal se niega a editar — no es un fallo nuestro, es una
        # negativa suya, y solo se resuelve tocándolos en el Seller Center.
        txt = str(exc)
        if "12052901" in txt:
            return False, ("TikTok NO permite editarle stock a este producto "
                           "(12052901) — hay que tocarlo en el Seller Center")
        return False, f"{type(exc).__name__}: {exc}"


# El de VENTAS. El otro almacén de la tienda es el de DEVOLUCIONES: escribirle
# stock no da error y no vende nada.
_ALMACEN_VENTAS_TIKTOK = "7647893424175580935"

# Convergencia de Temu: su lectura va con retraso, así que cada escritura se
# verifica y se corrige. 8 s cubre con margen los ~5 s medidos; 3 intentos
# bastan para un desfase y su corrección sin volverse un bucle.
_TEMU_ESPERA_S = 8.0
_TEMU_INTENTOS = 3
# Cuánto dura la desconfianza en la lectura DESPUÉS de que nosotros escribimos
# ese mismo producto. Fuera de esa ventana la lectura es fiable y un "no hay
# nada que hacer" se resuelve al instante — sin esto, una alineación de 352
# SKUs esperaría 8 s por cada uno (≈1 h) y dejaría la cola tapada para las
# ventas reales. Si el desfase viene de una venta en Temu (no de nosotros), el
# pedido correspondiente dispara otra pasada y se corrige solo.
_TEMU_VENTANA_RANCIA_S = 90.0
_temu_escrito_en: dict[int, float] = {}   # goodsId → epoch de nuestra última escritura


def _escribir_temu_sincrono(cuenta: str, item_id: str, cantidad: int,
                            solo_bajar: bool = False, verificar: bool = False) -> tuple[bool, str]:
    """
    Stock a UN producto de Temu (canal DROP-only, decisión 18-ago). Es el escritor de
    siempre; `_escribir_temu` (abajo) decide si va éste o la verificación diferida.

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
    5. Con `solo_bajar` (excedentes) nunca sube: si en vivo ya tiene lo mismo o
       menos no escribe, y si tras bajar la relectura queda por debajo, se detiene.
    6. `verificar` («sin cambio» rancio) no le cambia nada: este escritor SIEMPRE
       decide con la lectura viva y, si ya tenía el objetivo, contesta `YA_TENIA`.
       Existe para que los dos escritores se llamen igual. La verificación lo
       llama con `solo_bajar` cuando lo último que le dejamos era MÁS que el
       objetivo: así no suelta lo que Temu apartó después (descuenta al APARTAR).
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
        if solo_bajar and int(actual) <= objetivo:
            return True, f"{NO_BAJA}: Temu tiene {int(actual)} en vivo (objetivo {objetivo})"

        # Atajo seguro: si NOSOTROS no tocamos este goods hace poco, la lectura
        # es de fiar y un objetivo ya cumplido no necesita verificación.
        if (int(actual) == objetivo
                and (_t.time() - _temu_escrito_en.get(goods_id, 0.0)) > _TEMU_VENTANA_RANCIA_S):
            return True, f"{YA_TENIA} {objetivo} en vivo)"

        for intento in range(_TEMU_INTENTOS):
            diff = objetivo - int(actual)
            if solo_bajar and diff > 0:
                # Quedó por DEBAJO (una venta, o la relectura rancia): este modo no sube.
                return True, f"ok ({inicial}→{int(actual)}; no se sube a {objetivo})"
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
                _temu_escrito_en[goods_id] = _t.time()
            # Verificación SIEMPRE, incluso si el diff dio 0: la lectura que lo
            # calculó pudo venir rancia.
            _t.sleep(_TEMU_ESPERA_S)
            actual, _ids, err = _leer()
            if err:
                return False, f"escrito, pero no se pudo verificar: {err}"
            if actual is not None and int(actual) == objetivo:
                if escrituras == 0:
                    return True, f"{YA_TENIA} {objetivo} en vivo)"
                return True, (f"ok ({inicial}→{objetivo}"
                              + (f", {escrituras} ajustes)" if escrituras > 1 else ")"))
        return False, (f"no convergió tras {_TEMU_INTENTOS} intentos: "
                       f"quedó en {actual}, objetivo {objetivo}")
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# ── Temu: la verificación DIFERIDA (FANOUT_TEMU_VERIFICAR_DIFERIDO) ────────────
#
# EL COSTO (medido el 12-oct-2026). El escritor síncrono duerme `_TEMU_ESPERA_S` y relee
# después de TODA escritura: 9.1 s por SKU, de los que 8.0 s son el sueño. En la ráfaga
# S41906 fueron 15.3 de los 22 min que trabajó la cola, y mientras tanto los SKUs de
# atrás seguían a la venta en TikTok y Temu con las piezas que Odoo ya había apartado.
# Los reintentos no pesan (11 de 2,916 eventos con Temu esperaron 2 veces o más): pesa
# la espera fija.
#
# EL ARREGLO. Desde la cola (`_aplicar`) Temu lee, escribe el diff y REGRESA; registra
# una CONVERGENCIA pendiente para ese goods, y el hilo `fanout-temu-verif` la relee a
# los 8 s y corrige hasta `_TEMU_INTENTOS` escrituras, como el escritor síncrono. Lo
# que se conserva, a propósito:
#   · Un goods con una convergencia pendiente NO recibe otro diff: su lectura todavía
#     puede venir rancia (lo que dejó escrito el canario). El evento nuevo sólo le
#     cambia el objetivo y el verificador lo alcanza.
#   · Un candado por goods que toman la cola, el verificador y lo síncrono (excedentes,
#     rancios, reactivación del seguro): lo síncrono espera a que termine la
#     convergencia (`_temu_tomar_sincrono`). Eso cierra, además, la carrera que hoy
#     existe entre los excedentes y la cola sobre el mismo goods (3 traslapes en 60 días).
#     La espera tiene tope (32 s) y se vuelve a mirar YA con el candado tomado: si en el
#     hueco se registró otra convergencia, se suelta y se espera de nuevo. Si al vencer
#     sigue una pendiente, lo síncrono escribe igual (relee y converge solo), queda en el
#     log y, si dejó el goods en SU objetivo, la convergencia vieja se descarta: si no, el
#     verificador «corregiría» la escritura más nueva de vuelta al objetivo viejo
#     (revisión del 12-oct: la reactivación del seguro la ponía en 3 y el verificador la
#     regresaba a 0).
#   · La bitácora: la fila de la cola queda «ok (verificación diferida)». El verificador
#     sólo escribe cuando CORRIGE o FALLA, con `accion='verif_temu'` (fuera de los
#     eventos del fan-out a propósito: si fuera `escribir`, la matriz la tomaría por el
#     último reparto del SKU sin las filas de los demás canales). La matriz, las barras
#     y `_rancios` la leen como el último valor conocido; si falló, vale lo que se leyó.
#     Por eso el verificador no cierra un goods mientras el evento que lo registró siga
#     en proceso: esa fila de la cola se escribe al FINAL del evento (después de TikTok y
#     ML) y, con un id y un `ts` mayores, taparía la corrección o el ERROR.
#   · Lo que exige el stock VERIFICADO antes de seguir no se difiere: la reactivación
#     del seguro (objetivo > 0 con marca), los excedentes (`solo_bajar`) y la
#     verificación de rancios (`verificar`).
#   · Las convergencias viven en RAM: un reinicio a media verificación la pierde. Al
#     arrancar —con la bandera encendida O APAGADA, y aunque el recuperador esté
#     apagado: la reversa de esta bandera es justo un reinicio— se vuelven a encolar los
#     SKUs con una escritura diferida desde 2 min antes del arranque
#     (`fanout_recuperar.reencolar_diferidas`).

ACC_VERIF_TEMU = "verif_temu"
_TEMU_DIFERIDA = "verificación diferida"
_TEMU_VERIF_TICK_S = 1.0
_TEMU_ESPERA_PASO_S = 0.5     # cada cuánto mira lo síncrono si el verificador ya soltó el goods
_temu_lock = threading.Lock()
_temu_candados: dict[int, threading.Lock] = {}
_temu_conv: dict[int, dict[str, Any]] = {}    # goodsId → convergencia pendiente
_verificador_iniciado = False


def _temu_candado(goods_id: int) -> threading.Lock:
    with _temu_lock:
        return _temu_candados.setdefault(goods_id, threading.Lock())


def _temu_leer(goods_id: int) -> tuple[int | None, list, str | None]:
    """La misma lectura que el escritor síncrono: (quantity, skuIdList, error)."""
    from services import temu as tm
    res = _en_hilo(
        lambda: tm.llamar("bg.local.goods.list.query",
                          {"pageNo": 1, "pageSize": 10, "goodsIdList": [goods_id]}),
        "lectura temu")
    fila = next((g for g in (res.get("goodsList") or [])
                 if str(g.get("goodsId")) == str(goods_id)), None)
    if fila is None:
        return None, [], "el goods no aparece en el listado (¿eliminado?)"
    return fila.get("quantity"), (fila.get("skuIdList") or []), None


def _temu_editar(goods_id: int, sku_id: int, diff: int) -> tuple[bool, str]:
    """`stock.edit` por diferencia, exigiendo los DOS veredictos (como el síncrono)."""
    from services import temu as tm
    r = _en_hilo(
        lambda: tm.llamar("bg.local.goods.stock.edit", {
            "goodsId": goods_id,
            "skuStockChangeList": [{"skuId": sku_id, "stockDiff": int(diff)}]}),
        "stock temu")
    por_sku = (r.get("skuStockEditStatusInfoList") or [{}])[0]
    if not (r.get("operateResult") and por_sku.get("stockEditStatus")):
        return False, (f"Temu no aplicó: operateResult={r.get('operateResult')} "
                       f"skuStatus={por_sku.get('stockEditStatus')} "
                       f"{por_sku.get('errorMsg') or ''}")
    _temu_escrito_en[goods_id] = time.time()
    return True, "ok"


def _temu_esperar_convergencia(goods_id: int, fin: float) -> None:
    """Lo síncrono espera, hasta `fin`, a que el verificador suelte este goods."""
    while time.time() < fin:
        with _temu_lock:
            if goods_id not in _temu_conv:
                return
        time.sleep(_TEMU_ESPERA_PASO_S)


def _temu_tomar_sincrono(goods_id: int) -> tuple[threading.Lock, dict[str, Any] | None]:
    """El candado del goods para una escritura SÍNCRONA, sin convergencia pendiente.
    Esperar y tomar el candado no son un solo paso: si en el hueco la cola registró
    otra convergencia, se suelta el candado (el verificador lo necesita) y se espera
    de nuevo. Con tope de 32 s en total: al vencer se queda con el candado y devuelve
    la convergencia que sigue pendiente (None si no hay)."""
    fin = time.time() + _TEMU_ESPERA_S * (_TEMU_INTENTOS + 1)
    candado = _temu_candado(goods_id)
    while True:
        _temu_esperar_convergencia(goods_id, fin)
        candado.acquire()
        with _temu_lock:
            conv = _temu_conv.get(goods_id)
            pendiente = None if conv is None else dict(conv)
        if pendiente is None or time.time() >= fin:
            return candado, pendiente
        candado.release()


def _evento_en_proceso(sku: str) -> float | None:
    """`inicio` del evento de la cola que está repartiendo `sku` ahora (None si no)."""
    with _lock:
        return (_en_proceso.get(sku) or {}).get("inicio")


def _temu_diferir(cuenta: str, goods_id: int, objetivo: int, sku: str,
                  motivo: str) -> tuple[bool, str]:
    """Lee, escribe el diff y deja la verificación al hilo `fanout-temu-verif`."""
    evento = _evento_en_proceso(sku)
    with _temu_candado(goods_id):
        with _temu_lock:
            conv = _temu_conv.get(goods_id)
            if conv is not None:
                # Sin otra escritura: la lectura de este goods todavía no es de fiar.
                if conv["objetivo"] != objetivo:
                    conv.update(objetivo=objetivo, escrituras=0)
                conv.update(sku=sku, motivo=motivo, cuenta=cuenta, evento=evento)
                return True, f"ok ({_TEMU_DIFERIDA}: se suma a la que está en curso)"
        actual, ids, err = _temu_leer(goods_id)
        if err:
            return False, err
        if actual is None or not ids:
            return False, "listado sin quantity o sin skuIdList (no se escribe a ciegas)"
        if len(ids) > 1:
            return False, f"{len(ids)} variantes — repartir stock no está definido"
        sku_id, actual = int(ids[0]), int(actual)
        reciente = (time.time() - _temu_escrito_en.get(goods_id, 0.0)) <= _TEMU_VENTANA_RANCIA_S
        if actual == objetivo and not reciente:
            return True, f"{YA_TENIA} {objetivo} en vivo)"     # el mismo atajo de siempre
        escrituras = 0
        if actual != objetivo:
            ok, det = _temu_editar(goods_id, sku_id, objetivo - actual)
            if not ok:
                return False, det
            escrituras = 1
        with _temu_lock:
            _temu_conv[goods_id] = {
                "sku": sku, "cuenta": cuenta, "motivo": motivo, "sku_id": sku_id,
                "objetivo": objetivo, "inicial": actual, "escrituras": escrituras,
                "corregido_de": None, "desde": time.time(), "evento": evento,
                "listo_en": time.time() + _TEMU_ESPERA_S}
    _asegurar_verificador()
    if escrituras:
        return True, f"ok ({actual}→{objetivo}, {_TEMU_DIFERIDA})"
    return True, f"{YA_TENIA} {objetivo} en vivo; {_TEMU_DIFERIDA})"


def _temu_cerrar(goods_id: int, c: dict[str, Any], leido: int | None,
                 resultado: str | None) -> None:
    """Saca la convergencia y, si hubo corrección o falla, lo deja en la bitácora."""
    with _temu_lock:
        _temu_conv.pop(goods_id, None)
    if resultado is None:
        return
    ok = resultado.startswith("ok")
    _contadores["escrituras" if ok else "errores"] += 1
    ahora = datetime.now(timezone.utc)
    evento = {
        "ts": ahora.isoformat(timespec="seconds"), "ts_dt": ahora.replace(tzinfo=None),
        "sku": c["sku"], "motivo": c["motivo"], "dry_run": False, "stock_drop": None,
        "objetivo": c["objetivo"], "ok": ok, "detalle": None,
        "acciones": [{"canal": "temu", "cuenta": c["cuenta"], "item_id": str(goods_id),
                      "accion": ACC_VERIF_TEMU, "stock_actual_canal": leido,
                      "objetivo": c["objetivo"], "resultado": resultado}],
        "ms": round((time.time() - c["desde"]) * 1000, 1),
    }
    _eventos.appendleft({k: v for k, v in evento.items() if k != "ts_dt"})
    _persistir(evento)
    (log.info if ok else log.warning)("fan-out %s temu/%s: %s", c["sku"], goods_id, resultado)


def _verificar_temu(goods_id: int) -> None:
    """Una vuelta de la convergencia de un goods (con su candado ya tomado)."""
    with _temu_lock:
        c = dict(_temu_conv.get(goods_id) or {})
    if not c or c["listo_en"] > time.time():
        return
    objetivo = int(c["objetivo"])
    try:
        actual, _ids, err = _temu_leer(goods_id)
    except Exception as exc:  # noqa: BLE001
        actual, err = None, f"{type(exc).__name__}: {exc}"
    if err or actual is None:
        _temu_cerrar(goods_id, c, None, f"ERROR ({_TEMU_DIFERIDA}): escrito, pero no se pudo "
                                         f"verificar: {err or 'sin quantity'}")
        return
    actual = int(actual)
    if actual == objetivo:
        de = c.get("corregido_de")
        _temu_cerrar(goods_id, c, actual,
                     None if de is None else f"ok ({_TEMU_DIFERIDA}: corregido {de}→{objetivo})")
        return
    if int(c["escrituras"]) >= _TEMU_INTENTOS:
        _temu_cerrar(goods_id, c, actual, (f"ERROR ({_TEMU_DIFERIDA}): no convergió tras "
                                           f"{_TEMU_INTENTOS} intentos: quedó en {actual}, "
                                           f"objetivo {objetivo}"))
        return
    try:
        ok, det = _temu_editar(goods_id, int(c["sku_id"]), objetivo - actual)
    except Exception as exc:  # noqa: BLE001
        ok, det = False, f"{type(exc).__name__}: {exc}"
    if not ok:
        _temu_cerrar(goods_id, c, actual, f"ERROR ({_TEMU_DIFERIDA}): {det}")
        return
    with _temu_lock:
        vivo = _temu_conv.get(goods_id)
        if vivo is not None:
            vivo["escrituras"] = int(vivo["escrituras"]) + 1
            if vivo.get("corregido_de") is None:
                vivo["corregido_de"] = actual
            vivo["listo_en"] = time.time() + _TEMU_ESPERA_S


def _verificar_temu_listos() -> None:
    """Las convergencias que ya cumplieron su espera. Un goods ocupado (lo síncrono, o
    la cola sumándole un objetivo) se intenta en el siguiente tic: nunca se espera
    con un candado tomado. Tampoco se toma uno cuyo evento de la cola sigue en proceso:
    su fila «ok (verificación diferida)» todavía no está en la bitácora y, escrita
    después, taparía la `verif_temu` (las consultas toman la fila más nueva)."""
    ahora = time.time()
    with _lock:
        en_proceso = {s: i.get("inicio") for s, i in _en_proceso.items()}
    with _temu_lock:
        listos = [g for _t, g in sorted((c["listo_en"], g) for g, c in _temu_conv.items()
                                        if c["listo_en"] <= ahora
                                        and (c.get("evento") is None
                                             or en_proceso.get(c.get("sku")) != c.get("evento")))]
    for g in listos:
        candado = _temu_candado(g)
        if not candado.acquire(blocking=False):
            continue
        try:
            _verificar_temu(g)
        except Exception as exc:  # noqa: BLE001 — nunca en bucle sobre el mismo goods
            with _temu_lock:
                c = dict(_temu_conv.get(g) or {})
            if c:
                _temu_cerrar(g, c, None, f"ERROR ({_TEMU_DIFERIDA}): {type(exc).__name__}: {exc}")
        finally:
            candado.release()


def _verificador() -> None:
    """Hilo `fanout-temu-verif`: relee lo que la cola le escribió a Temu."""
    while True:
        try:
            time.sleep(_TEMU_VERIF_TICK_S)
            _verificar_temu_listos()
        except Exception as exc:  # noqa: BLE001 — el verificador NUNCA muere
            log.warning("verificador de Temu: %s", exc)


def _asegurar_verificador() -> None:
    global _verificador_iniciado
    if not _verificador_iniciado:
        with _temu_lock:
            if not _verificador_iniciado:
                threading.Thread(target=_verificador, daemon=True,
                                 name="fanout-temu-verif").start()
                _verificador_iniciado = True


def _escribir_temu(cuenta: str, item_id: str, cantidad: int,
                   solo_bajar: bool = False, verificar: bool = False,
                   diferir: bool = False, sku: str = "", motivo: str = "") -> tuple[bool, str]:
    """
    Stock a UN producto de Temu. Con FANOUT_TEMU_VERIFICAR_DIFERIDO apagada es
    `_escribir_temu_sincrono`, sin una línea de diferencia. Encendida: la cola pide
    `diferir` y no espera la relectura; lo demás (excedentes, rancios, reactivación)
    sigue síncrono, pero con el candado del goods y sin cruzarse con el verificador.
    """
    if not (diferir or _temu_diferido()):
        return _escribir_temu_sincrono(cuenta, item_id, cantidad, solo_bajar, verificar)
    from services import temu as tm
    try:
        goods_id = int(str(item_id))
    except (TypeError, ValueError):
        return _escribir_temu_sincrono(cuenta, item_id, cantidad, solo_bajar, verificar)
    if not tm.disponible():
        return False, "Temu no configurado (faltan TEMU_*)"
    if diferir and not (solo_bajar or verificar):
        try:
            return _temu_diferir(cuenta, goods_id, int(cantidad), sku, motivo)
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
    candado, pendiente = _temu_tomar_sincrono(goods_id)
    try:
        if pendiente is not None:
            log.warning("fan-out temu/%s: la verificación diferida (objetivo %s, de %s) sigue "
                        "pendiente tras %.0f s; la escritura síncrona (objetivo %s) no espera más",
                        goods_id, pendiente.get("objetivo"), pendiente.get("sku"),
                        _TEMU_ESPERA_S * (_TEMU_INTENTOS + 1), cantidad)
        ok, det = _escribir_temu_sincrono(cuenta, item_id, cantidad, solo_bajar, verificar)
        if ok and not det.startswith(NO_BAJA):
            # Lo síncrono releyó y convergió SOLO: una convergencia que siga pendiente
            # trae un objetivo más viejo y el verificador lo volvería a escribir encima.
            with _temu_lock:
                viejo = _temu_conv.pop(goods_id, None)
            if viejo is not None:
                log.warning("fan-out temu/%s: se descarta la verificación diferida (objetivo %s, "
                            "de %s): la escritura síncrona (objetivo %s) ya convergió: %s",
                            goods_id, viejo.get("objetivo"), viejo.get("sku"), cantidad, det)
        return ok, det
    finally:
        candado.release()


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

def _bloqueo_canal(canal: str | None) -> str | None:
    """
    Por qué el fan-out NO le escribe a este canal aunque haga falta; None = sí le
    escribe. Lo preguntan `plan` y la verificación de los «sin cambio» rancios
    (`_rancios`): un candado nuevo aquí vale para los dos.
    """
    c = (canal or "").lower()
    canales_ok = _canales_activos()
    if canales_ok is not None and c not in canales_ok:
        return f"canal '{canal}' no habilitado en FANOUT_CANALES"
    if c == "tiktok" and not settings.fanout_tiktok:
        # Candado propio, además de FANOUT_CANALES. El escritor ESTÁ hecho y
        # probado; lo que falta es la decisión de encenderlo, y encender
        # escrituras a un marketplace vivo no puede ser efecto secundario de
        # un deploy.
        return "FANOUT_TIKTOK apagado — el escritor está listo, falta encenderlo"
    if c == "temu" and not settings.fanout_temu:
        # Mismo candado que TikTok. El escritor existe desde el sondeo
        # canario del 18-ago (`_escribir_temu`); encenderlo es un acto
        # explícito, nunca efecto secundario de un deploy.
        return "FANOUT_TEMU apagado — el escritor está listo, falta encenderlo"
    if c not in _ESCRITORES:
        return f"sin escritor implementado para '{canal}'"
    return None


def plan(sku: str) -> dict[str, Any]:
    """
    Qué haría el fan-out con este SKU AHORA MISMO. No escribe ni encola nada:
    es lo que consume el dry-run, el endpoint de simulación y el worker.
    """
    stock = _stock_drop(sku)
    if stock is None:
        from services import stock_libro
        donde = "el libro de kubera" if stock_libro.lee_libro() else "WooCommerce"
        return {"sku": sku, "ok": False, "motivo": f"sin stock legible en {donde}",
                "acciones": []}
    objetivo = max(0, stock - _reserva())
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
            # OJO en TikTok y Temu: `stock_actual_canal` es lo que vio el ÚLTIMO
            # censo (cada 60 min), no lo que les escribimos después. Ver `_rancios`.
            accion["accion"] = "sin_cambio"
            accion["omitido_por"] = f"el canal ya tiene {objetivo}"
        elif bloqueo := _bloqueo_canal(d["canal"]):
            accion["accion"] = "omitir"
            accion["omitido_por"] = bloqueo
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
        # ESPEJO PRIMERO, Y APARTE. La leccion de `imagenes_amazon._cache_put`
        # (20-ago): si el espejo va DENTRO del try del INSERT a MySQL y DESPUES
        # de el, el dia del corte MySQL revienta y el espejo nunca corre — el
        # evento se pierde sin que nadie se entere.
        # Por el MISMO ayudante que los otros tres escritores. Tener aqui una
        # copia propia fue lo que dejo `stock_full` sin espejar: cuatro sitios
        # escriben esta bitacora y solo uno se habia enterado.
        _CAMPOS = ("ts", "sku", "motivo", "dry_run", "stock_drop", "objetivo",
                   "canal", "cuenta", "item_id", "accion", "stock_canal",
                   "resultado", "ms")
        from services import fanout_read
        for f in filas:
            d = dict(zip(_CAMPOS, f))
            d["dry_run"] = bool(d["dry_run"])
            fanout_read.espejar(**d)

        with db.get_cursor() as cur:
            cur.executemany(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                    item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", filas)
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_log: no se pudo persistir %s: %s", evento.get("sku"), exc)


def _seguro(paso: str, *args):
    """
    El seguro «stock 0 ⇒ fuera de la venta» (`services/fanout_seguro.py`): con el
    objetivo en 0 saca la publicación de la venta en Temu/TikTok ANTES de
    escribirle el 0, y la regresa cuando el stock vuelve, si la apagó él.

    No tiene interruptor propio: corre siempre que el fan-out esté encendido
    (`seguro_encendido`). Con el fan-out apagado esto no hace NADA: ni importa el
    módulo (tampoco lo importan los censos ni `pedidos_ml`, que hacen la misma
    pregunta). Y cada paso va en su propio `try`: una excepción del seguro nunca
    impide escribir el stock ni llegar a `_persistir`.
    """
    if not seguro_encendido():
        return None
    if paso != "abrir" and (not args or args[0] is None):
        return None
    try:
        from services import fanout_seguro
        return getattr(fanout_seguro, paso)(*args)
    except Exception as exc:  # noqa: BLE001
        log.warning("seguro stock 0 (%s): %s", paso, exc)
        return None


# ── «Sin cambio» rancios de TikTok y Temu ────────────────────────────────────
#
# EL DEFECTO (medido el 9-oct-2026 en producción). `plan` decide «el canal ya tiene
# N» contra `channel.listings.stock_own`, y nadie actualiza esa fila después de que
# escribimos: en ML la corrige el webhook `items` en 0.7 s, pero en TikTok y Temu
# sólo el censo (cada 60 min; mediana 20 min, p90 43 min). Si Woo regresa al número
# viejo dentro de esa ventana —una venta y stock_watch que la devuelve antes de que
# Odoo tenga la orden, o el rebote de la foto de Odoo—, el plan ve la cifra vieja,
# dice «sin cambio» y el canal se queda con lo último que le escribimos. 60 días:
# 163 casos (8.8 % de sus «sin cambio»; 0.3 % en ML), 139 con el canal ABAJO que
# nadie cerraba, porque el corrector de excedentes sólo baja. Y el seguro de stock 0
# no reactivaba lo que había apagado: para él no hubo escritura.
#
# EL ARREGLO, acotado a propósito. No se toca `plan` (así no cambian el seguro, la
# matriz, /simular, el recuperador ni los excedentes): en `_aplicar`, un «sin cambio»
# de TikTok/Temu cuya última cifra CONOCIDA de esa publicación —lo último que le
# escribimos, o lo que una verificación encontró— en las últimas
# FANOUT_VERIFICAR_RANCIO_H horas es OTRA se le pasa al escritor, que lee el canal en
# vivo y decide. La memoria de «qué le dejamos» es `ops.fanout_log`, que sobrevive a
# los deploys (la de Temu en RAM, no). Cuatro candados, medidos sobre los 60 días:
#
#   1. Sin escritura nuestra reciente NO se verifica: lo que el canal tiene por debajo
#      sin que lo hayamos tocado suele ser un pedido sin pagar que Temu apartó
#      (ACC-0768-EST y otras 6 el 9-oct), y el censo ya lo vio.
#   2. Si el censo RELEYÓ la publicación después de esa escritura, la libreta no está
#      rancia: la diferencia la hizo el canal solo (un apartado, una venta propia) y
#      eso no se toca. Lo dice `channel.listings.updated_at`, que el censo pone a TODAS
#      las filas del canal en cada corrida (lo demás que la toca es el seguro al
#      reflejar un estado: 13 veces en 60 días). Con `_CENSO_MARGEN_S` de margen,
#      porque el censo LEE el canal antes de escribir la libreta.
#   3. RESPETA LA DIRECCIÓN. Si lo último que le dejamos es MÁS que el objetivo, lo
#      rancio sería tenerlo ARRIBA: el escritor va con `solo_bajar`, que nunca sube (si
#      en vivo tiene lo mismo o menos —un apartado nuevo de Temu, que descuenta al
#      APARTAR— no lo toca). Sólo se sube cuando lo último que le dejamos es MENOS.
#   4. No se sube tras una VENTA: si lo último lo escribió una «venta …» y el objetivo
#      sube, casi siempre es stock_watch regresando la pieza antes de que Odoo tenga la
#      orden (106 de los 163 casos): subirlo re-ofrecería una pieza vendida. Si la
#      venta se canceló de verdad, el siguiente censo ve el canal abajo y el plan lo
#      escribe en el siguiente movimiento, como siempre.

_CANALES_VERIFICAR = ("tiktok", "temu")
_ESCRITORES_VERIFICAR = {"tiktok": _escribir_tiktok, "temu": _escribir_temu}
# Va en el `resultado` de toda fila que sale de una verificación: así se distinguen
# en la bitácora, en la trazabilidad (`fanout_vivo._en_vivo`) y en la matriz, que
# las toma como el último valor conocido del canal (`fanout_vivo._VERIFICADO`).
VERIFICADO = "verificado en vivo"
# …y en las que la decidieron pero NO llamaron al canal: dry-run, o
# FANOUT_VERIFICAR_RANCIO_ENSAYO con la bandera apagada.
VERIFICARIA = "verificaría en vivo"
_RE_DE_VIVO = re.compile(r"^ok \((-?\d+)→(-?\d+)")
_RE_TIENE = re.compile(r"tiene (-?\d+) en vivo")
# Lo que puede tardar un censo entre LEER una publicación y escribir la libreta: si la
# escribió antes de esto, pudo haberla leído antes de nuestra escritura.
_CENSO_MARGEN_S = 300

# La última cifra que SABEMOS que le dejamos a cada publicación: una escritura «ok»
# (del fan-out o de los excedentes) o una verificación que la encontró sin escribirle,
# y si el censo la releyó después. Usa los índices de la 0028: en producción
# (9-oct-2026) `ix_fanout_log_ts`, 116 filas en 2 h y 1-4 ms, también para el SKU con
# más filas de la bitácora; `channel.listings` va por su llave (sku, …).
# Una `verif_temu` (la verificación diferida de Temu) también cuenta: si corrigió, el
# canal quedó en el objetivo; si falló, en lo que leyó (desmiente el «ok» diferido).
_SQL_ULTIMA_CONOCIDA = """
    with u as (
      select distinct on (canal, item_id) canal, item_id,
             case when accion = 'verif_temu' and resultado not ilike 'ok%%' then stock_canal
                  else objetivo end as objetivo, ts, motivo
        from ops.fanout_log
       where sku = %(s)s and canal = any(%(c)s) and not dry_run
         and ts > now() - %(h)s * interval '1 hour'
         and ((accion = 'escribir' and resultado ilike 'ok%%')
              or (accion = 'sin_cambio' and resultado like %(v)s)
              or (accion = 'verif_temu' and (resultado ilike 'ok%%' or stock_canal is not null)))
       order by canal, item_id, id desc)
    select u.canal, u.item_id, u.objetivo, u.ts, u.motivo,
           exists (select 1 from channel.listings l
                    where l.sku = %(s)s and l.canal = u.canal and l.listing_id = u.item_id
                      and l.updated_at > u.ts + %(m)s * interval '1 second') as releida
      from u"""


def _rancio_activo() -> bool:
    """¿Se buscan «sin cambio» rancios? Con la bandera, o con su ensayo (que sólo anota)."""
    return bool(getattr(settings, "fanout_verificar_rancio", False)
                or getattr(settings, "fanout_verificar_rancio_ensayo", False))


def _rancios(sku: str, acciones: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """
    Los «sin cambio» de TikTok/Temu que pudieron decidirse contra una cifra vieja:
    {(canal, item_id): la última fila conocida de esa publicación + `modo`}. `modo` es
    «bajar» si lo último que le dejamos es MÁS que el objetivo (se verifica con
    `solo_bajar`) o «subir» si es menos. Vacío si la bandera y su ensayo están
    apagados, si la ventana es 0, si la bitácora de kubera no se escribe (no hay con
    qué comparar) o si no se pudo leer: en todos esos casos todo queda como siempre.
    """
    if not _rancio_activo():
        return {}
    if not getattr(settings, "supabase_write_fanout_log", False):
        return {}
    try:
        horas = float(getattr(settings, "fanout_verificar_rancio_h", 2.0))
    except (TypeError, ValueError):
        horas = 0.0
    if horas <= 0:          # ventana 0 (o ilegible) = no se verifica nada
        return {}
    # Sólo lo que el plan dejó «sin cambio» (su `stock_actual_canal` es conocido e igual
    # al objetivo): lo omitido —stock DESCONOCIDO, borradores, FULL— no se toca.
    dudosos = {((a.get("canal") or "").lower(), str(a["item_id"])): a for a in acciones
               if a.get("accion") == "sin_cambio" and a.get("item_id")
               and (a.get("canal") or "").lower() in _CANALES_VERIFICAR
               and _bloqueo_canal(a.get("canal")) is None}
    if not dudosos:
        return {}
    from services import supabase_db as sdb
    try:
        filas = sdb.fetch_all(_SQL_ULTIMA_CONOCIDA, {
            "s": sku, "c": sorted({c for c, _ in dudosos}), "h": horas,
            "v": VERIFICADO + "%", "m": _CENSO_MARGEN_S})
    except Exception as exc:  # noqa: BLE001 — sin bitácora, el «sin cambio» queda como hoy
        log.warning("fan-out %s: no pude leer la última escritura en kubera (%s); "
                    "los «sin cambio» de TikTok/Temu no se verifican", sku, exc)
        return {}
    salida: dict[tuple[str, str], dict[str, Any]] = {}
    for f in filas:
        clave = (str(f.get("canal") or "").lower(), str(f.get("item_id") or ""))
        a = dudosos.get(clave)
        if a is None or f.get("objetivo") is None:
            continue
        ultima, objetivo = int(f["objetivo"]), int(a["stock_actual_canal"])
        if ultima == objetivo:
            continue        # lo último que le dejamos ES lo que dice la libreta
        if f.get("releida"):
            continue        # el censo la releyó después: la libreta está al día (candado 2)
        modo = "bajar" if ultima > objetivo else "subir"
        if modo == "subir" and str(f.get("motivo") or "").lower().startswith("venta "):
            continue        # tras una venta, subir re-ofrecería la pieza vendida (candado 4)
        salida[clave] = {**f, "modo": modo}
    return salida


def _verificaria(ultima: dict[str, Any], simulacion: bool) -> str:
    """El `resultado` de un «sin cambio» rancio que NO se lleva al canal (dry-run o ensayo)."""
    return (f"{'DRY-RUN' if simulacion else 'ENSAYO'} ({VERIFICARIA}"
            f"{', sólo bajar' if ultima.get('modo') == 'bajar' else ''}: "
            f"lo último que le escribimos fue {ultima.get('objetivo')})")


def _verificar(sku: str, a: dict[str, Any],
               ultima: dict[str, Any]) -> tuple[dict[str, Any], bool | None]:
    """
    Le pasa al escritor un «sin cambio» rancio para que decida con el canal en vivo
    (con `solo_bajar` si `modo` es «bajar»). Devuelve la fila para la bitácora y si el
    canal quedó con el objetivo —True sí, None no (a propósito), False error—, que es
    lo que `_seguro("despues")` necesita para reactivar. Nunca levanta.

    `stock_actual_canal` (→ `stock_canal`) es lo que se LEYÓ en vivo, como en las
    filas de Amazon y del seguro; la libreta queda en el texto («el censo decía L»).

      · ya tenía el objetivo  → sigue `sin_cambio`: «verificado en vivo: el canal ya tiene N …»;
      · tenía menos y no se
        le sube (modo bajar)  → sigue `sin_cambio`: «verificado en vivo: el canal tiene K,
                                no se le sube a N …», con K como `stock_canal`;
      · tenía otra cifra      → `escribir` «ok (verificado en vivo: X→N; el censo decía L)»;
      · falló                 → `escribir` «ERROR (verificado en vivo): …», con lo último
                                que le escribimos como `stock_canal` (nada lo desmiente).
    """
    canal = (a.get("canal") or "").lower()
    objetivo, censo, previa = int(a["objetivo"]), a.get("stock_actual_canal"), ultima.get("objetivo")
    bajar = ultima.get("modo") == "bajar"
    try:
        ok, det = _ESCRITORES_VERIFICAR[canal](a["cuenta"], a["item_id"], objetivo,
                                               solo_bajar=bajar, verificar=True)
    except Exception as exc:  # noqa: BLE001 — los escritores ya atrapan; esto es red
        ok, det = False, f"{type(exc).__name__}: {exc}"
    _contadores["verificados"] += 1
    nota = f"lo último que le escribimos fue {previa}"
    tiene = None
    if ok and det.startswith(NO_BAJA):
        m = _RE_TIENE.search(det)
        tiene = int(m.group(1)) if m else None
    alcanzado: bool | None
    if ok and (det.startswith(YA_TENIA) or (det.startswith(NO_BAJA) and tiene == objetivo)):
        _contadores["sin_cambio"] += 1
        alcanzado = True
        fila = {**a, "resultado": f"{VERIFICADO}: el canal ya tiene {objetivo} ({nota})"}
    elif ok and det.startswith(NO_BAJA):
        # Tiene MENOS que el objetivo y lo último que le dejamos era más: Temu apartó o
        # vendió después de nuestra escritura. Subirlo soltaría ese apartado.
        _contadores["sin_cambio"] += 1
        alcanzado = None
        fila = {**a, "stock_actual_canal": tiene,
                "resultado": (f"{VERIFICADO}: el canal tiene {tiene if tiene is not None else '?'}, "
                              f"no se le sube a {objetivo} ({nota})")}
    elif ok:
        _contadores["escrituras"] += 1
        m = _RE_DE_VIVO.match(det)
        vivo = int(m.group(1)) if m else censo
        if m and "no se sube a" in det:
            # Temu con `solo_bajar`: bajó, pero la relectura quedó por debajo del objetivo.
            alcanzado = None
            fila = {**a, "accion": "escribir", "omitido_por": None, "stock_actual_canal": vivo,
                    "resultado": (f"ok ({VERIFICADO}: {vivo}→{m.group(2)}, no se sube a {objetivo}; "
                                  f"el censo decía {censo})")}
        else:
            alcanzado = True
            fila = {**a, "accion": "escribir", "omitido_por": None, "stock_actual_canal": vivo,
                    "resultado": f"ok ({VERIFICADO}: {vivo}→{objetivo}; el censo decía {censo})"}
    else:
        _contadores["errores"] += 1
        alcanzado = False
        fila = {**a, "accion": "escribir", "omitido_por": None, "stock_actual_canal": previa,
                "resultado": f"ERROR ({VERIFICADO}): {det}"}
    log.info("fan-out %s %s/%s: «sin cambio» verificado en vivo (%s; último escrito %s, objetivo %s) → %s",
             sku, canal, a.get("item_id"), ultima.get("modo"), previa, objetivo, fila["resultado"])
    return fila, alcanzado


_pub_lock = threading.Lock()
_pub_candados: dict[tuple[str, str], threading.Lock] = {}


def _candado_pub(canal: str, item_id: Any):
    """Con varios hilos (FANOUT_WORKERS > 1), dos SKUs que comparten publicación (31 de
    ML el 12-oct: parejas padre/variante) no la tocan a la vez: ni la escritura, ni la
    verificación de rancios, ni los pasos del seguro sobre ella. Con uno, nada.
    LO QUE NO CUBRE (por eso FANOUT_WORKERS se queda en 1): el tope por hora del seguro
    se revisa y se cuenta en dos pasos (`fanout_seguro._sueltos_en_la_hora` /
    `_contar_suelto`) y con N hilos se puede pasar hasta por N−1, y `_contadores` no es
    atómico (sólo estadística)."""
    if _num_workers() <= 1 or not item_id:
        return contextlib.nullcontext()
    with _pub_lock:
        return _pub_candados.setdefault((canal, str(item_id)), threading.Lock())


def _temu_exige_verificado(a: dict[str, Any]) -> bool:
    """¿Esta escritura a Temu tiene que quedar VERIFICADA antes de seguir? Sí cuando el
    objetivo es mayor que 0 y la publicación lleva la marca del seguro: el seguro la
    regresa a la venta sólo con el stock ya puesto (`despues` con la escritura «ok»).
    Con el objetivo en 0 el seguro ya la sacó de la venta (síncrono) antes de escribir.
    Si no se pueden leer las marcas, se verifica (lo de siempre)."""
    if int(a.get("objetivo") or 0) <= 0 or not seguro_encendido():
        return False
    try:
        from services import fanout_seguro
        return ("temu", str(a.get("item_id"))) in fanout_seguro._marcas_por_item()
    except Exception:  # noqa: BLE001
        return True


def _accion(sku: str, motivo: str, a: dict[str, Any], seg: Any, rancios: dict,
            sin_canal: bool, simulacion: bool, resultados: list[dict[str, Any]]) -> None:
    """Un destino del plan dentro de `_aplicar` (antes, el cuerpo de su `for`)."""
    # Objetivo 0 ⇒ primero fuera de la venta, después el 0 (que se escribe
    # SIEMPRE, falle o no el seguro). No depende de `accion`: también vale
    # para `sin_cambio`.
    _seguro("antes", seg, a)
    ultima = rancios.get(((a.get("canal") or "").lower(), str(a.get("item_id") or "")))
    if ultima is not None and sin_canal:
        _contadores["sin_cambio"] += 1
        resultados.append({**a, "resultado": _verificaria(ultima, simulacion)})
        _seguro("despues", seg, a, None)
        return
    if ultima is not None:
        # «Sin cambio» contra una cifra que pudo quedar vieja: decide el canal en
        # vivo. Si quedó con el objetivo, el seguro puede reactivar lo que apagó
        # (sin esto esperaba al censo + el reencolado diario).
        fila, alcanzado = _verificar(sku, a, ultima)
        resultados.append(fila)
        _seguro("despues", seg, fila, alcanzado)
        return
    if a["accion"] != "escribir":
        _contadores["sin_cambio" if a["accion"] == "sin_cambio" else (
            "omitidos_full" if "FULL" in (a.get("omitido_por") or "")
            else "omitidos_pausados")] += 1
        resultados.append(a)
        _seguro("despues", seg, a, None)
        return
    if simulacion:
        _contadores["simuladas"] += 1
        resultados.append({**a, "resultado": "DRY-RUN (no se escribió)"})
        _seguro("despues", seg, a, None)
        return
    canal = (a["canal"] or "").lower()
    escritor = _ESCRITORES[canal]
    if canal == "temu" and _temu_diferido() and not _temu_exige_verificado(a):
        ok, det = escritor(a["cuenta"], a["item_id"], a["objetivo"],
                           diferir=True, sku=sku, motivo=motivo)
    else:
        ok, det = escritor(a["cuenta"], a["item_id"], a["objetivo"])
    _contadores["escrituras" if ok else "errores"] += 1
    diferida = ok and _TEMU_DIFERIDA in str(det)
    resultados.append({**a, "resultado": ((f"ok ({_TEMU_DIFERIDA})" if diferida else "ok")
                                          if ok else f"ERROR: {det}")})
    # Stock de vuelta ⇒ PRIMERO el stock (ya quedó escrito), luego la venta.
    _seguro("despues", seg, a, ok)


def _aplicar(sku: str, motivo: str) -> None:
    """Calcula el plan y (si no es dry-run) lo ejecuta. Registra siempre."""
    inicio = time.time()
    p = plan(sku)
    simulacion = dry_run()
    seg = _seguro("abrir", sku, motivo, p)
    rancios = _rancios(sku, p.get("acciones", []))
    # En dry-run, o con sólo el ensayo encendido, se anota lo que verificaría y no se
    # llama al canal.
    sin_canal = simulacion or not getattr(settings, "fanout_verificar_rancio", False)
    resultados: list[dict[str, Any]] = []
    for a in p.get("acciones", []):
        # Con varios hilos, la publicación ENTERA —el seguro antes, la verificación o la
        # escritura, el seguro después— bajo su candado: dos SKUs que la comparten no se
        # cruzan. Con uno (lo de siempre) el candado no existe.
        with _candado_pub((a.get("canal") or "").lower(), a.get("item_id")):
            _accion(sku, motivo, a, seg, rancios, sin_canal, simulacion, resultados)

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
    # Las filas del seguro se sellan aparte y verificadas, con el MISMO `ts`.
    _seguro("cerrar", seg, evento["ts_dt"], evento["ms"])
    escrituras = sum(1 for r in resultados if r.get("accion") == "escribir")
    log.info("fan-out %s%s: stock=%s objetivo=%s → %d destino(s) a escribir",
             sku, " [DRY-RUN]" if simulacion else "", p.get("stock_drop"),
             p.get("objetivo"), escrituras)


_ESCRITORES_SOLO_BAJAR = {"tiktok": _escribir_tiktok, "temu": _escribir_temu}


def bajar(sku: str, canal: str, motivo: str) -> dict[str, Any]:
    """
    Le escribe el objetivo SÓLO a las publicaciones de `canal` que tienen MÁS que él
    (`fanout_excedentes`: el canal subió su número por su cuenta). Nunca sube nada:
    lo que está por debajo suele ser un pedido sin pagar que el canal apartó, y
    escribirle el número de Woo liberaría esa pieza. Mismo plan, candados y bitácora
    que `_aplicar`; el escritor relee el canal en vivo y se niega a subir.

    `resultado`: bajado · simulado (dry-run) · sin_excedente · sin_cambio (en vivo ya
    no estaba arriba) · omitido (el plan no le escribe a ese destino) · error.
    """
    canal = (canal or "").lower()
    escritor = _ESCRITORES_SOLO_BAJAR.get(canal)
    if escritor is None:
        return {"sku": sku, "resultado": "omitido", "detalle": f"'{canal}' no baja excedentes"}
    inicio = time.time()
    p = plan(sku)
    if not p.get("ok"):
        return {"sku": sku, "resultado": "omitido", "detalle": p.get("motivo")}
    arriba = [a for a in p["acciones"] if (a.get("canal") or "").lower() == canal
              and a.get("stock_actual_canal") is not None and a["stock_actual_canal"] > a["objetivo"]]
    if not arriba:
        return {"sku": sku, "resultado": "sin_excedente", "objetivo": p.get("objetivo")}
    simulacion = dry_run()
    seg = _seguro("abrir", sku, motivo, p)
    resultados: list[dict[str, Any]] = []
    salida = "omitido"
    for a in arriba:
        # El canal ofrece piezas con Woo en 0: primero fuera de la venta
        # (`fanout_seguro`), después se baja.
        _seguro("antes", seg, a)
        if a["accion"] != "escribir":
            resultados.append(a)
            _seguro("despues", seg, a, None)
            continue
        if simulacion:
            _contadores["simuladas"] += 1
            resultados.append({**a, "resultado": "DRY-RUN (no se escribió)"})
            salida = "simulado"
            _seguro("despues", seg, a, None)
            continue
        ok, det = escritor(a["cuenta"], a["item_id"], a["objetivo"], solo_bajar=True)
        if ok and det.startswith(NO_BAJA):
            _contadores["sin_cambio"] += 1
            resultados.append({**a, "accion": "sin_cambio", "omitido_por": det})
            if salida == "omitido":
                salida = "sin_cambio"
            _seguro("despues", seg, a, None)
        else:
            _contadores["escrituras" if ok else "errores"] += 1
            resultados.append({**a, "resultado": "ok" if ok else f"ERROR: {det}"})
            salida = "bajado" if ok else "error"
            _seguro("despues", seg, a, ok)
    if salida in ("bajado", "simulado", "error"):
        ahora = datetime.now(timezone.utc)
        evento = {
            "ts": ahora.isoformat(timespec="seconds"), "ts_dt": ahora.replace(tzinfo=None),
            "sku": sku, "motivo": motivo, "dry_run": simulacion,
            "stock_drop": p.get("stock_drop"), "objetivo": p.get("objetivo"),
            "ok": True, "detalle": None, "acciones": resultados,
            "ms": round((time.time() - inicio) * 1000, 1),
        }
        _eventos.appendleft({k: v for k, v in evento.items() if k != "ts_dt"})
        _persistir(evento)
        _seguro("cerrar", seg, evento["ts_dt"], evento["ms"])
        log.info("fan-out %s: %s ofrecía %s, objetivo %s → %s", sku, canal,
                 arriba[0]["stock_actual_canal"], p.get("objetivo"), salida)
    else:
        _seguro("cerrar", seg)      # sin evento del fan-out: sus filas llevan su propia hora
    return {"sku": sku, "resultado": salida, "antes": arriba[0]["stock_actual_canal"],
            "objetivo": p.get("objetivo"), "acciones": resultados}


# ── Cola con debounce ────────────────────────────────────────────────────────
#
# LA COLA RÁPIDA (12-oct-2026: la venta S41906 de Odoo apartó 183 SKUs en una pasada).
# Un solo hilo sacaba de golpe TODO lo que ya había reposado a un lote y lo procesaba
# en serie, en el orden de llegada (stock_watch encola en orden ALFABÉTICO): 24 min para
# vaciarlo, las bajadas que tocaban publicaciones a la venta esperando su letra, lo que
# llegaba durante el lote esperando a que terminara (57 SKUs a las 13:57) y el lote
# invisible para el panel, la matriz y el recuperador. Tres banderas, las tres apagadas
# (= la cola de siempre, sin una espera ni una consulta de más):
#
#   FANOUT_PRIORIDAD   el worker toma de a UN SKU entre los que ya reposaron, por clase
#                      (`_clasificar`): 0 = baja algo A LA VENTA, 1 = otra bajada, 2 =
#                      subida. Lo que pasa de FANOUT_PRIORIDAD_ENVEJECER_MIN esperando toma
#                      uno de cada dos turnos (`_orden_picker`). Lo nuevo ya no espera a que
#                      termine un lote.
#   FANOUT_WORKERS     N hilos con el mismo picker (en orden de llegada si la prioridad
#                      está apagada). Nunca dos eventos del mismo SKU a la vez: un SKU
#                      en proceso no se toma hasta que su evento pasó por `_persistir` y
#                      `_seguro("cerrar")` (de esa bitácora leen el siguiente).
#   FANOUT_TEMU_VERIFICAR_DIFERIDO   la cola no espera la relectura de Temu (`_temu_diferir`).
#
# Lo que no cambia con ninguna: una entrada por SKU en `_pendientes` (re-encolar sólo
# reinicia el debounce y el motivo; ahora conserva `desde`, la hora del primer
# encolado), nadie toma un SKU antes de `listo_en`, y `_aplicar` es el mismo.

_CLASE_VIGENCIA_S = 120.0     # una clase más vieja se recalcula (el stock pudo moverse)
_CLASIFICAR_MAX = 500         # SKUs por consulta de clasificación
# Segundos por SKU mientras la cola no haya medido los suyos (`_duraciones`): la mediana
# del 12-oct con Temu síncrono, y la de los eventos sin la espera de Temu.
_SEG_POR_SKU = {False: 11.0, True: 3.5}
_TEMU_A_LA_VENTA = "2/8"      # = fanout_vivo._TEMU_A_LA_VENTA
_clasif_lock = threading.Lock()


def _procesar(sku: str, motivo: str) -> None:
    """Un SKU de punta a punta. Quien lo toma ya lo puso en `_en_proceso`; sale de ahí
    cuando `_aplicar` terminó (con su bitácora y el cierre del seguro)."""
    inicio = time.time()
    try:
        _aplicar(sku, motivo)
    except Exception as exc:  # noqa: BLE001
        _contadores["errores"] += 1
        log.warning("fan-out %s falló: %s", sku, exc)
    finally:
        with _lock:
            _en_proceso.pop(sku, None)
            _duraciones.append(time.time() - inicio)


def _drenar_lote() -> None:
    """La cola de siempre: todo lo que ya reposó, en el orden en que llegó y en serie.
    El lote vive en `_lote` (antes, una lista local que nadie más veía)."""
    ahora = time.time()
    with _lock:
        for sku, info in list(_pendientes.items()):
            if info["listo_en"] <= ahora:
                _lote.append((sku, info["motivo"], info.get("desde", ahora)))
                _pendientes.pop(sku, None)
    while True:
        with _lock:
            if not _lote:
                return
            sku, motivo, desde = _lote.pop(0)
            _en_proceso[sku] = {"motivo": motivo, "desde": desde, "inicio": time.time(), "hilo": 0}
        _procesar(sku, motivo)


def _stock_drop_varios(skus: list[str]) -> dict[str, int | None]:
    """`_stock_drop` de una tanda en UNA consulta (para la prioridad): {SKU en
    mayúsculas: stock}. Lo que no aparece no está en Woo; None = ilegible. Con
    `FANOUT_FUENTE=kubera`, del libro (dos consultas; lanza si no se puede leer)."""
    if not skus:
        return {}
    from services import stock_libro
    if stock_libro.fuente_fanout() == "kubera":
        return {s: n for s, n in stock_libro.numeros(skus).items() if n is not None}
    from services import wp_db
    P = wp_db._prefix()
    marcas = ", ".join(["%s"] * len(skus))
    rows = wp_db._fetch_all(
        f"""SELECT s.meta_value AS sku, st.meta_value AS stock
            FROM {P}postmeta s
            JOIN {P}posts p ON p.ID = s.post_id AND p.post_status <> 'trash'
            LEFT JOIN {P}postmeta st ON st.post_id = p.ID AND st.meta_key = '_stock'
            WHERE s.meta_key = '_sku' AND s.meta_value IN ({marcas})""",
        tuple(skus),
    )
    salida: dict[str, int | None] = {}
    for r in rows:
        clave = str(r.get("sku") or "").upper()
        if clave in salida:
            continue            # el primero, como el LIMIT 1 de `_stock_drop`
        v = r.get("stock")
        try:
            salida[clave] = None if v in (None, "") else int(float(v))
        except (TypeError, ValueError):
            salida[clave] = None
    return salida


# Las publicaciones de una tanda de SKUs con su valor más reciente: lo más nuevo entre
# el censo (`channel.listings`) y nuestra última escritura «ok» o verificación (la misma
# regla que `fanout_vivo._SQL_VIVAS`). Sin eso, TikTok y Temu se clasificarían con un
# censo de hasta 60 min. `sku` es citext: la comparación no distingue mayúsculas.
_SQL_CLASES = """
    with w as (
      select distinct on (upper(sku::text), canal, item_id) upper(sku::text) as sku, canal,
             item_id, ts,
             case when accion = 'sin_cambio' then coalesce(stock_canal, objetivo)
                  when accion = 'verif_temu' and resultado not ilike 'ok%%' then stock_canal
                  else objetivo end as valor
        from ops.fanout_log
       where sku = any(%(s)s::citext[]) and not dry_run and ts > now() - interval '3 days'
         and ((accion = 'escribir' and resultado ilike 'ok%%')
              or (accion = 'sin_cambio' and resultado like 'verificado en vivo%%')
              or (accion = 'verif_temu' and (resultado ilike 'ok%%' or stock_canal is not null)))
       order by upper(sku::text), canal, item_id, ts desc, id desc)
    select upper(l.sku::text) as sku, l.canal, l.status, l.situacion,
           (coalesce(l.is_fulfillment, false)
            or coalesce(l.logistic_type, '') = 'fulfillment') as es_full,
           case when w.ts is not null and w.ts > l.updated_at then w.valor
                else l.stock_own end as valor
      from channel.listings l
      left join w on w.sku = upper(l.sku::text) and w.canal = l.canal and w.item_id = l.listing_id
     where l.sku = any(%(s)s::citext[]) and l.canal = any(%(c)s)"""


def _vende(f: dict[str, Any]) -> bool:
    """¿Está A LA VENTA? TikTok ACTIVATE, Temu 2/8, ML active (el FULL se descarta antes)."""
    canal = f.get("canal")
    if canal == "tiktok":
        return str(f.get("status") or "").upper() == "ACTIVATE"
    if canal == "temu":
        return str(f.get("status") or "") == _TEMU_A_LA_VENTA
    return str(f.get("situacion") or "").lower() == "active"


def _clasificar(skus: list[str]) -> dict[str, int]:
    """La clase de cada SKU para FANOUT_PRIORIDAD, con UNA lectura de Woo y UNA de kubera:

      0 · baja algo A LA VENTA (el valor conocido es mayor que el objetivo), o el
          objetivo es 0 y tiene algo a la venta (el seguro lo tiene que sacar);
      1 · otra bajada, Woo ilegible o el valor de una publicación a la venta desconocido;
      2 · subida o sin cambio.

    Es sólo el ORDEN: lo que se escribe lo decide `plan`, como siempre."""
    from services import supabase_db as sdb
    woo = _stock_drop_varios(skus)
    canales = [c for c in ("mercado_libre", "tiktok", "temu") if _bloqueo_canal(c) is None]
    por_sku: dict[str, list[dict[str, Any]]] = {}
    if canales:
        for f in sdb.fetch_all(_SQL_CLASES, {"s": sorted({s.upper() for s in skus}), "c": canales}):
            por_sku.setdefault(str(f.get("sku") or ""), []).append(f)
    reserva = _reserva()
    salida: dict[str, int] = {}
    for s in skus:
        w = woo.get(s.upper())
        if w is None:
            salida[s] = 1
            continue
        objetivo, clase = max(0, w - reserva), 2
        for f in por_sku.get(s.upper(), []):
            if f.get("es_full"):
                continue
            valor = None if f.get("valor") is None else int(f["valor"])
            vende = _vende(f)
            if vende and (objetivo == 0 or (valor is not None and valor > objetivo)):
                clase = 0
                break
            if (vende and valor is None) or (valor is not None and valor > objetivo):
                clase = 1
        salida[s] = clase
    return salida


def _clasificar_seguro(skus: list[str]) -> dict[str, int]:
    try:
        return _clasificar(skus)
    except Exception as exc:  # noqa: BLE001 — sin clase, todo va parejo y la cola avanza
        log.warning("fan-out: no pude clasificar %d SKU(s) (%s); van en orden de llegada",
                    len(skus), exc)
        return {s: 1 for s in skus}


def _clasificar_listos() -> None:
    """Clasifica lo listo que no tiene clase (o la tiene vieja), fuera de `_lock`: lee
    Woo y kubera. Dos hilos no clasifican la misma tanda."""
    with _clasif_lock:
        ahora = time.time()
        with _lock:
            faltan = [s for s, i in _pendientes.items() if i["listo_en"] <= ahora
                      and (i.get("clase") is None
                           or ahora - i.get("clase_en", 0.0) > _CLASE_VIGENCIA_S)]
        faltan = faltan[:_CLASIFICAR_MAX]
        if not faltan:
            return
        clases = _clasificar_seguro(faltan)
        t = time.time()
        with _lock:
            for s in faltan:
                info = _pendientes.get(s)
                if info is not None:
                    info["clase"], info["clase_en"] = clases.get(s, 1), t


def _clase(info: dict[str, Any]) -> int:
    """La clase de una entrada; sin clase (aún sin clasificar, o falló) = 1, neutral."""
    return 1 if info.get("clase") is None else int(info["clase"])


def _envejecer_s() -> float:
    """FANOUT_PRIORIDAD_ENVEJECER_MIN en segundos (piso: 1 min)."""
    try:
        return max(60.0, float(getattr(settings, "fanout_prioridad_envejecer_min", 15.0)) * 60)
    except (TypeError, ValueError):
        return 900.0


def _es_vieja(info: dict[str, Any], ahora: float) -> bool:
    """¿Una bajada que no toca nada a la venta, o una subida, que ya esperó demasiado?"""
    return _clase(info) > 0 and ahora - float(info.get("desde", ahora)) >= _envejecer_s()


# A quién le toca el siguiente turno con FANOUT_PRIORIDAD: True = a la entrada VIEJA más
# antigua (`_es_vieja`), False = a la primera por clase. Se alterna en cada toma.
_alterna: dict[str, bool] = {"vieja": False}


def _orden_picker(listos: list[tuple[str, dict[str, Any]]], ahora: float,
                  toca_vieja: bool | None = None) -> list[tuple[str, dict[str, Any]]]:
    """En qué orden los tomaría el picker. Sin prioridad: por llegada (el orden del dict,
    que re-encolar no cambia). Con prioridad, DOS filas que se alternan turno a turno:

      · por clase (`_clasificar`) y, dentro de la clase, por llegada: primero las bajadas
        A LA VENTA, luego otras bajadas, luego subidas;
      · las VIEJAS: clase 1 o 2 con más de FANOUT_PRIORIDAD_ENVEJECER_MIN esperando, por
        llegada. Mientras haya, toman UNO DE CADA DOS turnos.

    Así una bajada a la venta pasa siempre delante de lo que no vende, por larga que sea la
    ráfaga o por tarde que llegue, y nada espera para siempre: lo que pasó del umbral se
    reparte al menos a la mitad del paso de la cola. (Dos ideas anteriores, descartadas:
    restar un nivel de clase por cada 10 min de espera emparejaba a todos a media ráfaga y
    devolvía el orden alfabético —la última bajada a la venta a los 37.6 min en la
    simulación—; y «llegada + 10 min por nivel» dejaba una venta que llega a media ráfaga
    detrás de TODAS las bajadas viejas: en S41906, las 13 publicaciones del segundo lote
    sólo bajaban su exposición un 8 %.)"""
    if not _prioridad():
        return list(listos)
    if toca_vieja is None:
        toca_vieja = _alterna["vieja"]
    viejas = sorted((x for x in listos if _es_vieja(x[1], ahora)),
                    key=lambda x: float(x[1].get("desde", ahora)))
    nombres = {x[0] for x in viejas}
    resto = sorted((x for x in listos if x[0] not in nombres),
                   key=lambda x: (_clase(x[1]), float(x[1].get("desde", ahora))))
    salida: list[tuple[str, dict[str, Any]]] = []
    i = j = 0
    while i < len(resto) or j < len(viejas):
        if j < len(viejas) and (toca_vieja or i >= len(resto)):
            salida.append(viejas[j])
            j += 1
            toca_vieja = False
        else:
            salida.append(resto[i])
            i += 1
            toca_vieja = True
    return salida


def _tomar(hilo: int) -> tuple[str, str] | None:
    """El siguiente SKU para este hilo, o None si no hay nada listo (o lo listo está en
    proceso en otro hilo). Sale de `_pendientes` y entra a `_en_proceso` bajo el MISMO
    candado: nunca queda en ninguno de los dos, y nunca en los dos a la vez."""
    if _prioridad():
        _clasificar_listos()
    ahora = time.time()
    with _lock:
        ocupados = {s.upper() for s in _en_proceso}
        listos = [(s, i) for s, i in _pendientes.items()
                  if i["listo_en"] <= ahora and s.upper() not in ocupados]
        if not listos:
            return None
        sku, info = _orden_picker(listos, ahora)[0]
        if _prioridad():
            _alterna["vieja"] = not _es_vieja(info, ahora)
        _pendientes.pop(sku, None)
        _en_proceso[sku] = {"motivo": info["motivo"], "desde": info.get("desde", ahora),
                            "inicio": ahora, "hilo": hilo, "clase": info.get("clase")}
    return sku, info["motivo"]


def _drenar_esperas() -> None:
    """Seguro stock 0: las esperas anti-parpadeo que ya vencieron vuelven a la cola y
    `_aplicar` reevalúa todo."""
    if seguro_encendido():
        from services import fanout_seguro
        for sku in fanout_seguro.esperas_vencidas():
            encolar(sku, fanout_seguro.MOTIVO_ESPERA)


def _drenar_picker(hilo: int) -> None:
    """De a uno mientras haya algo listo. El hilo 0 sigue devolviendo a la cola las
    esperas vencidas del seguro aunque la ráfaga dure (con el lote esperaban a que
    terminara)."""
    siguiente = time.time() + _TICK_S
    while True:
        tomado = _tomar(hilo)
        if tomado is None:
            return
        _procesar(*tomado)
        if hilo == 0 and time.time() >= siguiente:
            _drenar_esperas()
            siguiente = time.time() + _TICK_S


def _worker(hilo: int = 0) -> None:
    """Drena los SKUs que ya 'reposaron' su ventana de debounce."""
    while True:
        try:
            time.sleep(_TICK_S)
            if _usa_picker():
                _drenar_picker(hilo)
            elif hilo == 0:
                _drenar_lote()
            if hilo == 0:
                _drenar_esperas()
        except Exception as exc:  # noqa: BLE001 — el worker NUNCA muere
            log.warning("worker de fan-out: %s", exc)


def _asegurar_worker() -> None:
    global _worker_iniciado
    if not _worker_iniciado:
        with _lock:
            if not _worker_iniciado:
                for i in range(_num_workers()):
                    threading.Thread(target=_worker, args=(i,), daemon=True,
                                     name="fanout-stock" if i == 0 else f"fanout-stock-{i + 1}").start()
                _worker_iniciado = True


def encolar(sku: str, motivo: str = "venta") -> None:
    """
    Pide replicar el stock DROP de `sku` a los canales. Fire-and-forget: solo
    encola y regresa — el camino crítico de la venta NUNCA se bloquea ni falla
    por esto. Re-encolar el mismo SKU dentro de la ventana solo REINICIA el
    debounce (las ráfagas se colapsan en una escritura); `desde` (la hora del
    primer encolado) se conserva: con ella envejece la prioridad y se calcula la ETA.
    """
    try:
        if not habilitado() or not (sku or "").strip():
            return
        _asegurar_worker()
        with _lock:
            clave, ahora = sku.strip(), time.time()
            previo = _pendientes.get(clave)
            _pendientes[clave] = {"listo_en": ahora + DEBOUNCE_S, "motivo": motivo,
                                  "desde": previo.get("desde", ahora) if previo else ahora}
            _contadores["encolados"] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("fan-out encolar(%s): %s", sku, exc)


def encolar_varios(skus: list[str], motivo: str = "venta") -> None:
    for s in skus or []:
        encolar(s, motivo)


def seg_por_sku() -> float:
    """Lo que tarda un SKU en la cola: la mediana de los últimos 40 (o la medida el 12-oct)."""
    with _lock:
        durs = sorted(_duraciones)
    return durs[len(durs) // 2] if durs else _SEG_POR_SKU[_temu_diferido()]


def cola() -> list[dict[str, Any]]:
    """
    La cola REAL, en el orden en que se va a repartir: lo que está en proceso, el lote
    (la cola de siempre) y lo pendiente (en el orden del picker). Cada entrada lleva
    `sku`, `motivo`, `desde` (epoch del primer encolado), `estado` (proceso | lote |
    pendiente), `posicion` (0 = en proceso), `eta_s` (cuánto falta, aprox.) y `clase`
    (con prioridad). Vive en la memoria de ESTE proceso (hoy es uno solo): la matriz
    («en cola, posición N, ~M min») y el recuperador la leen de aquí. Sin red ni disco.
    """
    ahora = time.time()
    with _lock:
        proc = sorted(((s, dict(i)) for s, i in _en_proceso.items()),
                      key=lambda x: x[1].get("inicio", 0.0))
        lote = list(_lote)
        pend = [(s, dict(i)) for s, i in _pendientes.items()]
    seg = seg_por_sku()
    hilos = _num_workers() if _usa_picker() else 1
    if _usa_picker():
        pend = (_orden_picker([x for x in pend if x[1]["listo_en"] <= ahora], ahora)
                + _orden_picker([x for x in pend if x[1]["listo_en"] > ahora], ahora))
    salida = [{"sku": s, "motivo": i.get("motivo"), "desde": i.get("desde"), "estado": "proceso",
               "posicion": 0, "eta_s": 0, "clase": i.get("clase")} for s, i in proc]
    n = 0
    for s, motivo, desde in lote:
        n += 1
        salida.append({"sku": s, "motivo": motivo, "desde": desde, "estado": "lote",
                       "posicion": n, "eta_s": round(n * seg / hilos), "clase": None})
    for s, i in pend:
        n += 1
        salida.append({"sku": s, "motivo": i.get("motivo"), "desde": i.get("desde"),
                       "estado": "pendiente", "posicion": n, "eta_s": round(n * seg / hilos),
                       "clase": i.get("clase")})
    return salida


def en_cola() -> set[str]:
    """Los SKUs (en mayúsculas) que la cola va a repartir o está repartiendo."""
    with _lock:
        return ({s.upper() for s in _pendientes} | {s.upper() for s, _m, _d in _lote}
                | {s.upper() for s in _en_proceso})


# ── Monitoreo ────────────────────────────────────────────────────────────────

def historial(limite: int = 100, solo_errores: bool = False) -> list[dict[str, Any]]:
    """Bitácora PERSISTIDA (sobrevive deploys). Es lo que pinta el dashboard."""
    if settings.supabase_read_fanout_log:
        from services import fanout_read
        return fanout_read.historial(limite, solo_errores)
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
    if settings.supabase_read_fanout_log:
        from services import fanout_read
        return fanout_read.resumen()
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
    from services import fanout_recuperar
    with _lock:
        pendientes = sorted(_pendientes.keys())
    foto = cola()
    with _temu_lock:
        verificando = len(_temu_conv)
    return {
        "recuperador": fanout_recuperar.estado(),
        "habilitado": habilitado(),
        "dry_run": dry_run(),
        "canales_habilitados": sorted(_canales_activos()) if _canales_activos() else "todos",
        "escritores_implementados": sorted(_ESCRITORES.keys()),
        "reserva": _reserva(),
        "debounce_s": DEBOUNCE_S,
        "pendientes": pendientes,
        # La cola REAL (`cola()`): lo pendiente, el lote y lo que está en proceso.
        "cola": {"total": len({str(e["sku"]).upper() for e in foto}),     # SKUs, no entradas
                 "en_proceso": [e["sku"] for e in foto if e["estado"] == "proceso"],
                 "lote": sum(1 for e in foto if e["estado"] == "lote"),
                 "primeros": foto[:30]},
        "cola_rapida": {"prioridad": _prioridad(), "temu_verificar_diferido": _temu_diferido(),
                        "workers": _num_workers(), "seg_por_sku": round(seg_por_sku(), 1),
                        "verificaciones_temu_pendientes": verificando},
        "contadores": dict(_contadores),
        "eventos": list(_eventos)[:50],
        "resumen": resumen(),           # acumulado persistido (sobrevive deploys)
        "historial": historial(60),     # bitácora para el dashboard
    }
