"""
precio_al_abrir.py — El precio de ML se confirma AL ABRIR el cajón del producto.

QUÉ HACE. Cuando el panel abre el cajón de un SKU, le pregunta a Mercado Libre
el precio que el comprador PAGA por **esas** publicaciones (1 o 2 llamadas: no
hay un solo SKU del catálogo con más de dos publicaciones de ML) y lo guarda
ANTES de contestar. Lo que se ve en pantalla es entonces lo que la tienda cobra
en ese momento, no una foto de hace horas.

POR QUÉ NO ALCANZABA LO QUE YA HABÍA. Tres caminos escriben `price_sale` y
ninguno cubre "el producto que estoy mirando":

  · Los webhooks `items_prices` / `public_offers` refrescan la publicación que
    ML dice que cambió. Cubren el 72% de las activas, y las otras 130 no se
    confirman solas. Medido: 292 publicaciones observadas en las últimas 24 h,
    de 4,726 vivas (26-ago-2026).
  · El barrido completo (`precios_venta.py`) tarda ~9.3 h por vuelta: cuando
    abres un producto puede llevar horas sin refrescar.
  · El sync de 15 min escribe `price`, que NO es lo que se cobra.

El agujero, medido en vivo el 26-ago-2026 sobre `MLM5473713768`
(ACC-0562-NEG-MATTE, BEKURA): el panel mostraba `precio_vigente` **$219.00**
—marcado "sin confirmar"— y ML cobraba **$99.00** en ese mismo instante. No es
matiz: es 2.2x. Con este refresco el cajón abre diciendo $99.00 confirmado.

EL PISO, que es lo que impide que esto sea un martillo. Si la publicación ya se
observó hace menos de `PRECIO_AL_ABRIR_PISO_MIN` minutos, NO se vuelve a
preguntar: se devuelve lo guardado, que ya está confirmado. Alguien recorriendo
100 productos gasta ~180 llamadas (1.82 publicaciones por SKU de mediana), y
volver sobre los mismos productos en la misma sesión no gasta nada.

Hay un segundo piso, DURO, de segundos: pase lo que pase con la confirmación,
una publicación observada hace menos de `PRECIO_AL_ABRIR_PISO_DURO_S` no se
vuelve a preguntar. Existe porque la condición "sin confirmar" puede reaparecer
sola —el sync toca `updated_at` y la confirmación se cae— y sin ese tope un
cajón reabierto en bucle preguntaría cada vez.

QUÉ NO HACE, A PROPÓSITO
------------------------
1. **No pisa `price_sale` a ciegas.** `channel.listing_history` NO audita esa
   columna (handoff abierto a omni-datos, 26-ago): lo que se sobreescribe no se
   reconstruye. Por eso cada cambio de valor deja una línea de log — hoy es el
   único rastro— y por eso un `None` de ML jamás se escribe: `None` significa
   "no observado", no "sin promoción".
2. **No renueva el token de ML.** Ante un 401 se rinde y el cajón abre con lo
   guardado. El camino del webhook y el barrido sí se auto-sanan; éste no,
   porque está debajo de una pantalla que alguien está esperando y un refresh
   de token cuesta más de lo que este camino puede gastar.
3. **No pide `/items/{id}`.** `GET /items/{id}/sale_price` trae `amount` Y
   `regular_amount` en el mismo cuerpo, así que es UNA llamada por publicación.
   El webhook gasta dos porque además refresca stock y situación; aquí no hace
   falta. Verificado el 26-ago-2026 sobre publicaciones vivas de las dos
   cuentas: las cinco contestaron 200 con las dos claves presentes.
4. **No escribe `price_base`** aunque `regular_amount` lo traiga servido. Esa
   columna tampoco se audita y no está fallando: en la muestra del encargo
   (33 con `regular_amount`) coincidió al centavo con lo guardado. Cuando NO
   coincide se deja una línea de log y ahí se queda — para enterarnos si algún
   día empieza a derivar, sin estrenar un escritor que nadie pidió.

DÓNDE SE SELLA LA CONFIRMACIÓN. El panel considera confirmado lo que cumple
`price_sale_at >= updated_at` (`publicaciones_panel._oferta`). El UPDATE de aquí
pone `price_sale_at = now()` y el trigger `trg_touch_listings` (BEFORE UPDATE,
incondicional) pone `updated_at = now()`: `now()` es la hora de la TRANSACCIÓN,
así que los dos salen idénticos. **Verificado, no supuesto** — probado contra el
sandbox el 26-ago-2026: delta de 0.000000 s, `confirmada = true` en las dos
filas del par, y `listing_history` sin una sola fila nueva. Si esto no se
cumpliera, cada apertura gastaría llamadas a ML sin confirmar nada.

Regla 11 de la casa: todo HTTP por httpx ASYNC; el guardado (psycopg2,
bloqueante) sale a un hilo con `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import settings
from services import meli
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.precio_al_abrir")

_ML_API = "https://api.mercadolibre.com"

# Vocabulario CERRADO del campo `estado`. El frontend indexa por él.
OK = "ok"                          # se le preguntó a ML
PISO = "piso"                      # no se preguntó: observado hace muy poco
SIN_PUBLICACIONES = "sin_publicaciones"
APAGADO = "apagado"                # SYNC_ENABLED o PRECIO_AL_ABRIR en false
NO_APLICA = "no_aplica"            # no vino un SKU exacto que consultar
SIN_TOKEN = "sin_token"
FALLO = "fallo"
TIMEOUT = "timeout"

# Las publicaciones de ML de UN SKU. Agrupado por `listing_id` porque 89
# publicaciones cuelgan de DOS filas —la del SKU padre y la de la variante— con
# el mismo `listing_id` (handoff a omni-datos del 25-ago). Se pregunta UNA vez y
# el UPDATE, que va por `listing_id`, escribe en las dos.
#
# `min(price_sale_at)` y `max(updated_at)` son la lectura CONSERVADORA del par:
# si cualquiera de las dos filas está rancia o sin confirmar, se refresca. Como
# el UPDATE sella las dos a la vez, converge en una sola pasada.
_SQL_OBJETIVO = """
select l.listing_id                as listing_id,
       max(a.legacy_code)          as cuenta,
       count(*)                    as filas,
       count(l.price_sale_at)      as observadas,
       min(l.price_sale_at)        as observada_at,
       max(l.updated_at)           as cambiada_at,
       max(l.price_sale)           as price_sale,
       max(l.price_base)           as price_base
  from channel.listings l
  join core.accounts a on a.id = l.account_id
 where l.canal = 'mercado_libre'
   and l.sku = %(sku)s
   and nullif(l.listing_id, '') is not null
   and lower(coalesce(l.situacion, '')) <> 'closed'
 group by l.listing_id
"""

# `price_sale_at = now()` en el MISMO UPDATE que el precio: la pregunta que
# contesta es "¿cuándo se miró?", no "¿cuándo cambió?". Se sella aunque el
# número venga igual — que ML confirme el mismo precio ES la respuesta.
_SQL_GUARDAR = """
update channel.listings
   set price_sale = %(precio)s, price_sale_at = now()
 where canal = 'mercado_libre' and listing_id = %(listing_id)s
"""


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _utc(ts: Any) -> datetime | None:
    if not isinstance(ts, datetime):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def hay_que_preguntar(*, observada_at: Any, cambiada_at: Any, sin_observar: bool,
                      ahora: datetime, piso_min: int, piso_duro_s: int) -> bool:
    """
    ¿Se le pregunta a ML por esta publicación? Función PURA — se prueba sola.

    Se pregunta cuando:
      · nunca se observó (o alguna fila del par no se observó), o
      · la observación es más vieja que el piso, o
      · la observación quedó SIN CONFIRMAR (`observada_at < cambiada_at`): la
        publicación cambió después de mirarla, así que lo guardado ya no vale
        para esta pantalla. Este es el caso que más se da hoy —202 confirmadas
        de 4,726 vivas el 26-ago— y es justo el que el cajón viene a cerrar.

    Y NO se pregunta, pase lo que pase, si la observación tiene menos de
    `piso_duro_s` segundos. Sin ese tope, reabrir el mismo cajón en bucle
    volvería a preguntar cada vez que el sync tocara la fila.
    """
    obs = _utc(observada_at)
    if obs is None or sin_observar:
        return True
    if (ahora - obs) < timedelta(seconds=piso_duro_s):
        return False
    if (ahora - obs) >= timedelta(minutes=piso_min):
        return True
    cambio = _utc(cambiada_at)
    return bool(cambio and obs < cambio)


def _objetivo(sku: str) -> list[dict[str, Any]]:
    """BLOQUEANTE — va en to_thread."""
    return [dict(f) for f in sdb.fetch_all(_SQL_OBJETIVO, {"sku": sku})]


def _guardar(pares: list[tuple[str, float]]) -> int:
    """BLOQUEANTE — va en to_thread. Una transacción para todo el producto."""
    if not pares:
        return 0
    with sdb.get_cursor() as cur:
        # Rótulo para `listing_history.detectado_via`. HOY ES INERTE:
        # `fn_listing_history` no audita `price_sale`, así que este UPDATE no
        # genera ninguna fila de historial (comprobado en sandbox: delta 0).
        # Se marca igual para que el día que omni-datos sume la columna, estas
        # escrituras salgan rotuladas desde el primer minuto y no como 'sync'.
        cur.execute("select set_config('app.via', %s, true)", ("refresco_al_abrir",))
        for listing_id, precio in pares:
            cur.execute(_SQL_GUARDAR, {"precio": precio, "listing_id": listing_id})
    return len(pares)


async def _preguntar(cli: httpx.AsyncClient, listing_id: str, token: str,
                     sem: asyncio.Semaphore) -> dict[str, Any] | None:
    """
    El cuerpo de `/items/{id}/sale_price`. `None` ante CUALQUIER fallo.

    `None` se descarta en vez de escribirse: el contrato de la columna es que
    NULL significa "no observado", así que un fallo de red nunca puede borrar
    una observación buena de ayer. Y ante un 401 NO se renueva el token — ver
    el punto 2 del encabezado.
    """
    async with sem:
        try:
            r = await cli.get(f"/items/{listing_id}/sale_price",
                              params={"context": "channel_marketplace"},
                              headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                log.info("precio al abrir · %s: ML contestó %s", listing_id,
                         r.status_code)
                return None
            return r.json() or {}
        except Exception as exc:  # noqa: BLE001
            log.info("precio al abrir · %s: %s", listing_id, type(exc).__name__)
            return None


def _informe(estado: str, **extra: Any) -> dict[str, Any]:
    """El bloque `refresco` de la respuesta. Contrato con el frontend."""
    base = {"estado": estado, "publicaciones": 0, "preguntadas": 0,
            "confirmadas": 0, "cambiaron": 0, "sin_respuesta": 0,
            "omitidas_piso": 0, "omitidas_tope": 0, "ms": 0, "detalle": None}
    base.update(extra)
    # `al_dia` es lo único que el frontend NECESITA mirar: "todo lo que se
    # muestra de ML para este SKU está confirmado contra ML ahora mismo".
    base["al_dia"] = (estado in (OK, PISO, SIN_PUBLICACIONES)
                      and not base["sin_respuesta"] and not base["omitidas_tope"])
    return base


async def _refrescar(sku: str) -> dict[str, Any]:
    t0 = time.monotonic()
    objetivo = await asyncio.to_thread(_objetivo, sku)
    if not objetivo:
        # Ni error ni hueco: este SKU no tiene publicaciones vivas de ML. El
        # cajón está al día porque no hay nada de ML que confirmar.
        return _informe(SIN_PUBLICACIONES)

    ahora = datetime.now(timezone.utc)
    pendientes = [o for o in objetivo
                  if hay_que_preguntar(observada_at=o.get("observada_at"),
                                       cambiada_at=o.get("cambiada_at"),
                                       sin_observar=(o.get("observadas") or 0)
                                       < (o.get("filas") or 0),
                                       ahora=ahora,
                                       piso_min=settings.precio_al_abrir_piso_min,
                                       piso_duro_s=settings.precio_al_abrir_piso_duro_s)]
    omitidas_piso = len(objetivo) - len(pendientes)
    if not pendientes:
        return _informe(PISO, publicaciones=len(objetivo),
                        omitidas_piso=omitidas_piso,
                        ms=int((time.monotonic() - t0) * 1000))

    # Tope por producto. Hoy no muerde —ningún SKU del catálogo tiene más de 2
    # publicaciones de ML (474 con una, 2,126 con dos, 26-ago-2026)— pero un
    # SKU con veinte no puede convertir una apertura de cajón en veinte
    # llamadas. Se atienden primero las más rancias: lo nunca observado antes.
    tope = settings.precio_al_abrir_max
    pendientes.sort(key=lambda o: (_utc(o.get("observada_at"))
                                   or datetime(1970, 1, 1, tzinfo=timezone.utc)))
    omitidas_tope = max(0, len(pendientes) - tope)
    pendientes = pendientes[:tope]

    cuentas = {str(o["cuenta"]) for o in pendientes}
    tokens: dict[str, str] = {}
    for c in cuentas:
        t = await asyncio.to_thread(meli._access_token, c)
        if t:
            tokens[c] = t
    # Sin token de UNA cuenta no se cae todo: se refresca lo que sí se puede y
    # lo demás cuenta como no refrescado. Si esto se sumara en silencio, el
    # informe diría `al_dia` con una publicación sin confirmar detrás — que es
    # justo la mentira que este camino existe para no contar.
    con_token = [o for o in pendientes if str(o["cuenta"]) in tokens]
    huerfanas = len(pendientes) - len(con_token)
    pendientes = con_token
    if not pendientes:
        return _informe(SIN_TOKEN, publicaciones=len(objetivo),
                        sin_respuesta=huerfanas, omitidas_piso=omitidas_piso,
                        omitidas_tope=omitidas_tope,
                        detalle=f"sin token de ML para {', '.join(sorted(cuentas - set(tokens)))}",
                        ms=int((time.monotonic() - t0) * 1000))

    sem = asyncio.Semaphore(settings.precio_al_abrir_en_paralelo)
    async with httpx.AsyncClient(
            base_url=_ML_API, timeout=settings.precio_al_abrir_timeout_s) as cli:
        cuerpos = await asyncio.gather(*[
            _preguntar(cli, str(o["listing_id"]), tokens[str(o["cuenta"])], sem)
            for o in pendientes])

    pares: list[tuple[str, float]] = []
    cambiaron = 0
    sin_respuesta = huerfanas  # las que se quedaron sin token arrastran hasta aquí
    for o, cuerpo in zip(pendientes, cuerpos):
        lid = str(o["listing_id"])
        precio = _num((cuerpo or {}).get("amount"))
        if precio is None:
            sin_respuesta += 1
            continue
        antes = _num(o.get("price_sale"))
        if antes is None or abs(antes - precio) >= 0.005:
            cambiaron += 1
            # ÚNICO rastro del valor anterior mientras `price_sale` no se
            # audite. Si algún día se sobreescribe algo por error, esto es lo
            # que queda para reconstruirlo.
            log.info("precio al abrir · %s %s (%s): price_sale %s → %s",
                     sku, lid, o.get("cuenta"), antes, precio)
        lista = _num((cuerpo or {}).get("regular_amount"))
        guardado = _num(o.get("price_base"))
        if lista is not None and guardado is not None and abs(lista - guardado) >= 0.005:
            # No se escribe (punto 4 del encabezado); se deja dicho.
            log.info("precio al abrir · %s %s: lista de ML %s ≠ price_base %s",
                     sku, lid, lista, guardado)
        pares.append((lid, precio))

    confirmadas = await asyncio.to_thread(_guardar, pares)
    return _informe(OK, publicaciones=len(objetivo), preguntadas=len(pendientes),
                    confirmadas=confirmadas, cambiaron=cambiaron,
                    sin_respuesta=sin_respuesta, omitidas_piso=omitidas_piso,
                    omitidas_tope=omitidas_tope,
                    ms=int((time.monotonic() - t0) * 1000))


async def refrescar_sku(sku: str | None) -> dict[str, Any]:
    """
    Confirma contra ML el precio de las publicaciones de UN SKU. No levanta.

    Un cajón que no abre es peor que un cajón con un dato de hace una hora: si
    algo aquí falla o se pasa del presupuesto, se devuelve el informe diciendo
    qué pasó y la lectura sigue su curso con lo guardado. Por eso NINGUNA
    excepción sale de esta función.
    """
    if not settings.precio_al_abrir or not settings.sync_enabled:
        # `SYNC_ENABLED` apagado = modo puros pedidos: no se habla con ML para
        # sincronizar datos. Es el mismo interruptor que respeta el webhook.
        return _informe(APAGADO,
                        detalle=("PRECIO_AL_ABRIR" if not settings.precio_al_abrir
                                 else "SYNC_ENABLED"))
    sku = (sku or "").strip()
    if not sku:
        return _informe(NO_APLICA)
    try:
        return await asyncio.wait_for(
            _refrescar(sku), timeout=settings.precio_al_abrir_presupuesto_s)
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("precio al abrir · %s: se pasó del presupuesto de %ss",
                    sku, settings.precio_al_abrir_presupuesto_s)
        return _informe(TIMEOUT,
                        detalle=f"ML tardó más de {settings.precio_al_abrir_presupuesto_s}s")
    except Exception as exc:  # noqa: BLE001
        log.warning("precio al abrir · %s: %s: %s", sku, type(exc).__name__, exc)
        return _informe(FALLO, detalle=f"{type(exc).__name__}: {exc}")
