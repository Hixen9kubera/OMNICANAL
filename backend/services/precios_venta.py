"""
precios_venta.py — El BARRIDO que confirma el precio de las activas de ML.

Estuvo DORMIDO del 21 al 26-ago-2026. Se revive con otro filtro y otra cadencia,
y las dos cosas cambiaron por medición, no por gusto. Léase antes de tocarlo.

═══════════════════════════════════════════════════════════════════════════════
QUÉ HACE
═══════════════════════════════════════════════════════════════════════════════
Le pregunta a ML `/items/{id}/sale_price?context=channel_marketplace` por cada
publicación ACTIVA de Mercado Libre y guarda el `amount` —lo que el comprador
PAGA— en `channel.listings.price_sale`, sellando `price_sale_at`.

Con eso la oferta queda CONFIRMADA para `publicaciones_panel._oferta`, que es
quien decide el margen del panel:

    confirmada  ⇔  price_sale_at >= listings.updated_at

Ese `>=` se cumple porque `_SQL_GUARDAR` sella `price_sale_at = now()` y el
trigger `trg_touch_listings` sella `updated_at = now()` en el MISMO UPDATE:
`now()` es la hora de la transacción, así que los dos salen idénticos. No es
casualidad y no se puede cambiar a `clock_timestamp()` sin romper el barrido
entero (`updated_at` quedaría un microsegundo DESPUÉS y nada se confirmaría).

═══════════════════════════════════════════════════════════════════════════════
POR QUÉ EXISTE, SI YA HAY WEBHOOKS DE PRECIO
═══════════════════════════════════════════════════════════════════════════════
Porque los avisos dan LATENCIA y el barrido da COBERTURA. Son cosas distintas y
hacen falta las dos.

Los dos topics de precio (`items_prices` y `public_offers`, v0.261.0 y v0.262.0)
funcionan y son el camino rápido: cuando ML avisa, la publicación se confirma en
segundos vía `inventario.refrescar_ml_item_id(con_precio_venta=True)`. Pero un
aviso solo llega cuando algo CAMBIA, y hay dos agujeros que ningún aviso tapa.

**Agujero 1 — hay activas que nunca reciben aviso.** Medido el 26-ago-2026 sobre
la ventana completa que retiene `ops.webhook_events` (3 días, del 23 al 26):

    745 publicaciones ACTIVAS de ML
    404 recibieron algún aviso de precio  (54%)
    341 NO recibieron ninguno            (46%)  ← de éstas, 0 confirmadas

El porcentaje depende de la ventana y la ventana es corta a la fuerza:
`ops.webhook_events` retiene 3 días. Sobre los dos días COMPLETOS de la muestra
el hueco sube a 381 (51%) y sobre el día de más tráfico a 456 (61%). En ningún
corte baja del 45%.

Ojo con el "130 activas (17%)" que dice la entrada v0.261.0 del README:
contesta OTRA pregunta. Aquél contaba las que no reciben aviso de NINGÚN tipo
(hoy 173, 23%), pero `orders_v2`, `shipments`, `items` y los demás **no
confirman la oferta** — solo `items_prices` y `public_offers` disparan
`con_precio_venta=True` en `routers/webhooks.py`. La pregunta que importa es
cuántas no reciben aviso de PRECIO.

Las 341 no es que estén atrasadas: es que **ninguna está confirmada, y por ese
camino ninguna lo va a estar nunca**. El panel les calcula el margen contra un
precio que ML no cobra. Muestra viva de ese grupo, GET a ML el 26-ago:

    MLM2674612829   guardado $1,600.00   ML cobra   $608.00   (−62%)
    MLM5306160256   guardado   $248.54   ML cobra    $91.00   (−63%)
    MLM2926602405   guardado   $445.61   ML cobra   $435.53

**Agujero 2 — la confirmación se cae sola.** `updated_at` se mueve con cualquier
cambio de la fila (precio, stock, situación…), y entonces la oferta guardada
vuelve a quedar sin confirmar aunque nadie la haya tocado. Medido el 26-ago:

    de las 745 activas, 0 tienen updated_at con más de 48 h
    315 cambiaron en las últimas 24 h · 439 en 48 h

O sea: **la confirmación del catálogo entero caduca en menos de dos días.** Una
pasada única —la del 20-ago, que dejó 665 ofertas rancias aplicándose al margen
cuatro días— no arregla nada; hay que repetirla. Es la moraleja que este archivo
ya traía escrita y que ahora está medida: *una foto de precios sin quien la
repita es una mentira con fecha de caducidad.*

═══════════════════════════════════════════════════════════════════════════════
UNA SOLA LLAMADA TRAE LOS DOS PRECIOS
═══════════════════════════════════════════════════════════════════════════════
`/items/{id}/sale_price` contesta `amount` Y `regular_amount` en el mismo cuerpo:

    {"price_id":"27","amount":435.53,"regular_amount":533.69,"currency_id":"MXN",
     "metadata":{"campaign_id":"P-MLM17863008","promotion_type":"marketplace_campaign"}}

`regular_amount` es el precio de LISTA —el mismo número que `item.original_price`
y que `channel.listings.price_base`— y viene NULL cuando no hay promoción.
Verificado el 26-ago contra 40 publicaciones vivas: 40/40 en 200, 33 traían
`regular_amount` y **33/33 coincidían al centavo con el `price_base` guardado**;
las 7 con `regular_amount` nulo tenían `amount == price` (sin promoción).

Por eso NO hace falta pedir además `/items/{id}` — que es lo que hace el camino
del webhook, y por lo que ese camino cuesta DOS llamadas por aviso. Aquí una
llamada alcanza, y el par (oferta, lista) sale del mismo cuerpo y del mismo
instante, así que el descuento que pinta el panel no puede quedar cruzado.

Lo que NO se hace es ESCRIBIR `price_base` con ese `regular_amount`. Se usa solo
para VIGILAR: si ML dice una lista distinta a la guardada, se cuenta y se
registra (`denominador_movido`). Dos escritores para una columna que nadie
audita es cómo se pierden datos en silencio; el sync de 15 min ya la escribe
desde `item.original_price` y sigue siendo su único dueño.

═══════════════════════════════════════════════════════════════════════════════
EL FILTRO — por qué ya no es `stock_full > 0`
═══════════════════════════════════════════════════════════════════════════════
El filtro viejo nació para VALUAR el inventario en FULL, no para mantener
precios, y no es el mismo conjunto. Medido el 26-ago:

    stock_full > 0 y no cerrada .... 680
    activas ........................ 745
      activas SIN stock en FULL .....  68   ← el filtro viejo no las miraba
      con stock FULL y NO activas ....  3   ← el filtro viejo gastaba en ellas

El universo correcto es el que el panel pinta: **activa**, y con el MISMO
criterio que usa el panel — no una segunda definición de "activa". Por eso el
WHERE sale de `publicaciones_panel.filtro_sql_activas('mercado_libre')` y no de
un `situacion <> 'closed'` escrito aquí: cambiar el mapa de estados mueve este
barrido en el mismo commit.

El COSTO no decide quién entra, decide quién va PRIMERO. De las 745 activas,
456 tienen costo en `costing.costos_finales` y 289 no. Sin costo no hay margen
que corregir hoy, pero el costo puede cargarse mañana y entonces conviene que el
precio ya esté confirmado — y son 289 llamadas sobre un presupuesto barato. Van
al final de la cola, no fuera de ella.

═══════════════════════════════════════════════════════════════════════════════
LA CADENCIA, Y POR QUÉ NO ES `ML_PRECIO_VENTA` OTRA VEZ
═══════════════════════════════════════════════════════════════════════════════
El proyecto ya rechazó encender `ML_PRECIO_VENTA` en el sync: ~11,500 llamadas
diarias, y además ENSUCIABA el corte de valor (volvía a sellar fecha sobre
publicaciones que el refresco acababa de leer). Este barrido no es eso.

    ML_PRECIO_VENTA en el sync ............ ~11,500 llamadas/día
    los webhooks de precio, HOY ............ ~3,444 llamadas/día
        (1,722 avisos el 25-ago × 2 llamadas: item + sale_price)
    este barrido, 80 por hora .............. ~1,920 llamadas/día

Sí: **el barrido completo cuesta poco más de la MITAD de lo que ya se gasta en
los avisos**, porque una llamada por publicación en vez de dos, y porque no
repite la misma publicación 32 veces al día (`price` cambió 2,962 veces en 24 h
repartidas entre 91 SKUs; los avisos siguen cada uno de esos rebotes).

Se eligió **goteo por hora** en vez de pasada completa cada N horas. A igual
presupuesto las dos dan la misma edad máxima —80/h son 745/80 = **9.3 h de
ciclo**, igual que una pasada completa cada 9.3 h— pero el goteo:

  · no choca con el sync de 15 min (80 filas contra 745 de golpe),
  · degrada suave: si ML se pone lento o el backend reinicia, se atrasa minutos,
  · reparte la carga en vez de mandarle a ML 745 peticiones en 18 segundos
    (medido: 42.7 req/s con 8 en paralelo — la pasada completa cabe en 18 s, y
    ese pico es justo lo que el archivo viejo ya se cuidaba de no hacer).

Y el orden es **la más rancia primero**, que es lo que hace converger el goteo
sin coordinarse con nadie: la que un webhook acaba de confirmar se va al final
de la cola sola. El barrido no compite con los avisos, los complementa.

9.3 h de ciclo contra 48 h de caducidad deja margen de sobra. Si se quiere más
barato, `PRECIOS_VENTA_POR_HORA=40` da ciclo de 18.6 h y sigue por debajo de la
caducidad; por debajo de ~32/h (ciclo 23 h) empieza a no alcanzar para las que
cambian a diario.

**El barrido de ARRANQUE** es aparte: una pasada completa 3 min después del
boot, 745 llamadas de una vez, para no esperar 9 h a que el goteo drene el
atraso acumulado (600 publicaciones sin confirmar el 26-ago).

═══════════════════════════════════════════════════════════════════════════════
LO QUE NO SE PISA A CIEGAS
═══════════════════════════════════════════════════════════════════════════════
`channel.listing_history` NO audita `price_sale` — `channel.fn_listing_history`
solo registra price, stock_own, stock_full, is_fulfillment, status y situacion.
Lo que este barrido sobreescriba **no se puede reconstruir**. De ahí tres reglas:

  1. `None` NUNCA se escribe. Un fallo de red no borra una observación buena
     (el contrato de la 0025: NULL = "no observado", no "sin descuento").
  2. Solo se escribe la fila cuyo valor CAMBIÓ. Reobservar lo mismo igual tiene
     que resellar la fecha —observar es el hecho, no solo cambiar— así que el
     UPDATE va igual, pero el cambio de VALOR se registra en la bitácora con el
     antes y el después, que hoy es el único rastro que queda de él.
  3. `app.via` se marca `barrido_precios`, para que el día que alguien sume
     `price_sale` al trigger de historia esas filas no salgan rotuladas 'sync'.

Regla 11 de la casa: todo HTTP por httpx ASYNC; el guardado (psycopg2,
bloqueante) sale a un hilo con asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from config import settings
from services import meli
from services import publicaciones_panel as pp
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.precios")

_ML_API = "https://api.mercadolibre.com"
# Cuántas publicaciones se piden a la vez. ML aguanta más (medido: 42.7 req/s
# con 8 en paralelo, mediana 100 ms), pero el objetivo no es exprimirlo: es
# terminar sin que un pico de concurrencia se lea como abuso desde su lado.
_EN_PARALELO = 8
_TIMEOUT_S = 20.0
_TANDA = 80


def _sql_objetivo() -> tuple[str, dict[str, Any]]:
    """
    Las activas de ML, la más rancia primero, con las que tienen costo delante.

    El criterio de "activa" NO se escribe aquí: sale de `publicaciones_panel`,
    que es el que decide qué es activa en el panel. Dos definiciones de la misma
    palabra en dos módulos es exactamente el bug que el handoff de omni-frontend
    del 24-ago levantó sobre `solo_publicados`.

    `limite = None` se renderiza como `LIMIT NULL`, que en Postgres es "sin
    tope": el mismo SQL sirve para el goteo y para la pasada completa.
    """
    activas = pp.filtro_sql_activas("mercado_libre", alias="l")
    if activas is None:  # pragma: no cover — ML sí decide por `situacion`
        raise RuntimeError("publicaciones_panel no sabe qué es una activa de ML")
    donde, params = activas
    sql = f"""
    select l.listing_id,
           a.legacy_code                       as cuenta,
           max(l.price_base)                   as price_base,
           max(l.price_sale)                   as price_sale,
           bool_or(cf.sku is not null)         as con_costo
      from channel.listings l
      join core.accounts a on a.id = l.account_id
      left join costing.costos_finales cf
             on cf.sku = l.sku and cf.canal = 'mercado_libre'
     where l.canal = 'mercado_libre'
       and nullif(l.listing_id, '') is not null
       and {donde}
       and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
     group by 1, 2
     order by bool_or(cf.sku is not null) desc,
              max(l.price_sale_at) asc nulls first,
              max(l.updated_at) asc,
              -- Desempate DETERMINISTA. Sin él el goteo no es un prefijo de la
              -- pasada completa: hay cientos de filas empatadas en
              -- `price_sale_at is null`, y Postgres devuelve un orden distinto
              -- en cada corrida. Convergería igual (observar una publicación la
              -- manda al final de la cola), pero dos corridas seguidas pedirían
              -- muestras distintas y ninguna medición sería reproducible.
              l.listing_id
     limit %(limite)s
    """
    return sql, params


# `price_sale_at` se sella SIEMPRE que hubo observación, aunque el precio no
# haya cambiado: la pregunta que contesta es "¿cuándo se miró?", no "¿cuándo
# cambió?". Sin eso no se distingue "hoy no hay promoción" de "nadie preguntó",
# y peor: una promoción que sigue viva quedaría sin confirmar para siempre.
#
# `now()` (hora de la TRANSACCIÓN) es lo que hace que esto confirme: el trigger
# trg_touch_listings pone `updated_at = now()` en este mismo UPDATE, así que los
# dos quedan iguales y `price_sale_at >= updated_at` se cumple.
_SQL_GUARDAR = """
update channel.listings
   set price_sale = %(precio)s, price_sale_at = now()
 where canal = 'mercado_libre' and listing_id = %(listing_id)s
"""

_estado: dict[str, Any] = {
    "fase": "inactivo", "detalle": None, "motivo": None, "inicio": None,
    "fin": None, "vistas": 0, "objetivo": 0, "cuenta": None,
    "cambios": 0, "sin_respuesta": 0, "denominador_movido": 0,
}
_lock = asyncio.Lock()


def estado() -> dict[str, Any]:
    return dict(_estado)


def _marcar(fase: str, detalle: str | None = None, **extra: Any) -> None:
    _estado["fase"] = fase
    _estado["detalle"] = detalle
    _estado.update(extra)
    if fase in ("listo", "error"):
        _estado["fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log.info("precios de venta: %s%s", fase, f" — {detalle}" if detalle else "")


def _objetivo(cuenta: str | None, limite: int | None) -> list[dict[str, Any]]:
    """BLOQUEANTE — va en to_thread."""
    sql, params = _sql_objetivo()
    return sdb.fetch_all(sql, {**params, "cuenta": cuenta, "limite": limite})


def _guardar(pares: list[tuple[str, float]]) -> int:
    """BLOQUEANTE — va en to_thread. Una transacción para toda la tanda.

    `app.via` se marca aunque hoy no genere ni una fila de historia: el trigger
    `channel.fn_listing_history` no audita `price_sale`. El día que lo audite
    (hay handoff abierto a omni-datos pidiéndolo), estas filas ya nacen
    rotuladas con quién las escribió en vez de heredar el 'sync' por default.
    """
    if not pares:
        return 0
    with sdb.get_cursor() as cur:
        cur.execute("select set_config('app.via', 'barrido_precios', true)")
        for listing_id, precio in pares:
            cur.execute(_SQL_GUARDAR, {"precio": precio, "listing_id": listing_id})
    return len(pares)


async def _precio(cli: httpx.AsyncClient, listing_id: str, cuenta: str,
                  tokens: dict[str, str], sem: asyncio.Semaphore
                  ) -> tuple[float, float | None] | None:
    """
    `(lo que se PAGA, el precio de LISTA)` según ML, del mismo cuerpo y el mismo
    instante. `None` ante cualquier fallo.

    El segundo elemento es `regular_amount`, y viene NULL cuando no hay
    promoción — en ese caso la lista ES lo que se paga. Aquí se devuelve tal
    cual (None) y quien lo consume decide; no se inventa un número.

    `None` como resultado entero se DESCARTA en vez de escribirse: el contrato
    de la columna es que NULL significa "no observado", así que un fallo de red
    nunca debe borrar una observación buena de ayer.
    """
    async with sem:
        par = {"context": "channel_marketplace"}
        ruta = f"/items/{listing_id}/sale_price"
        try:
            r = await cli.get(ruta, params=par,
                              headers={"Authorization": f"Bearer {tokens[cuenta]}"})
            if r.status_code == 401:
                nuevo = await asyncio.to_thread(meli.refrescar_token, cuenta)
                if not nuevo:
                    return None
                tokens[cuenta] = nuevo
                r = await cli.get(ruta, params=par,
                                  headers={"Authorization": f"Bearer {nuevo}"})
            if r.status_code != 200:
                return None
            cuerpo = r.json() or {}
            v = cuerpo.get("amount")
            if v in (None, ""):
                return None
            lista = cuerpo.get("regular_amount")
            return float(v), (float(lista) if lista not in (None, "") else None)
        except Exception:  # noqa: BLE001
            return None


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def _refrescar(cuenta: str | None, limite: int | None, motivo: str) -> None:
    objetivo = await asyncio.to_thread(_objetivo, cuenta, limite)
    if not objetivo:
        _marcar("listo", "no hay publicaciones activas de ML", vistas=0, objetivo=0,
                motivo=motivo)
        return

    cuentas = {str(f["cuenta"]) for f in objetivo}
    tokens: dict[str, str] = {}
    for c in cuentas:
        t = await asyncio.to_thread(meli._access_token, c)
        if t:
            tokens[c] = t
    faltan = cuentas - set(tokens)
    if faltan:
        # Sin token no se puede preguntar. Seguir con las demás dejaría un
        # barrido a medias que se ve completo, y en el panel eso significa
        # media cuenta con margen confirmado y media sin — peor que no correr.
        _marcar("error", f"sin token de ML para {', '.join(sorted(faltan))}",
                motivo=motivo)
        return

    _marcar("consultando", f"{len(objetivo)} publicaciones ({motivo})", vistas=0,
            objetivo=len(objetivo), cuenta=cuenta, motivo=motivo,
            cambios=0, sin_respuesta=0, denominador_movido=0)
    sem = asyncio.Semaphore(_EN_PARALELO)
    vistas = cambios = movido = 0
    try:
        async with httpx.AsyncClient(base_url=_ML_API, timeout=_TIMEOUT_S) as cli:
            # Por tandas para poder ir guardando y mostrando avance: una sola
            # espera de 745 peticiones no deja ver nada hasta el final, y si
            # truena a la mitad se pierde todo lo ya consultado.
            for i in range(0, len(objetivo), _TANDA):
                trozo = objetivo[i:i + _TANDA]
                res = await asyncio.gather(*[
                    _precio(cli, str(f["listing_id"]), str(f["cuenta"]), tokens, sem)
                    for f in trozo
                ])
                pares: list[tuple[str, float]] = []
                for fila, r in zip(trozo, res):
                    if r is None:
                        continue
                    amount, lista_ml = r
                    lid = str(fila["listing_id"])
                    pares.append((lid, amount))
                    # Bitácora del sobreescrito: es el ÚNICO rastro que queda de
                    # lo que había antes, porque listing_history no audita esta
                    # columna. Ver el encabezado.
                    antes = _num(fila.get("price_sale"))
                    if antes is not None and abs(antes - amount) >= 0.01:
                        cambios += 1
                        log.info("precio de venta %s: $%.2f → $%.2f", lid, antes, amount)
                    elif antes is None:
                        cambios += 1
                        log.info("precio de venta %s: primera observación $%.2f",
                                 lid, amount)
                    # Vigilancia del DENOMINADOR. `regular_amount` es la lista
                    # según ML en este mismo instante; `price_base` es la que el
                    # sync guardó. Si no coinciden, el descuento que pinta el
                    # panel está cruzado — y como aquí NO se escribe price_base,
                    # lo único correcto es contarlo y decirlo.
                    lista_db = _num(fila.get("price_base"))
                    lista_hoy = lista_ml if lista_ml is not None else amount
                    if lista_db is not None and abs(lista_db - lista_hoy) >= 0.01:
                        movido += 1
                        log.info("precio de LISTA de %s: kubera $%.2f · ML $%.2f "
                                 "(no se pisa: lo escribe el sync)",
                                 lid, lista_db, lista_hoy)
                vistas += await asyncio.to_thread(_guardar, pares)
                _marcar("consultando", f"{vistas} de {len(objetivo)}", vistas=vistas,
                        cambios=cambios, denominador_movido=movido)
        sin_dato = len(objetivo) - vistas
        _marcar("listo",
                f"{vistas} de {len(objetivo)} publicaciones · {cambios} con precio nuevo"
                + (f" · {movido} con la lista movida" if movido else "")
                + (f" · {sin_dato} sin respuesta de ML" if sin_dato else ""),
                vistas=vistas, cambios=cambios, sin_respuesta=sin_dato,
                denominador_movido=movido, motivo=motivo)
    except Exception as exc:  # noqa: BLE001
        _marcar("error", f"{type(exc).__name__}: {exc}", vistas=vistas, motivo=motivo)


async def refrescar_en_fondo(cuenta: str | None = None, limite: int | None = None,
                             motivo: str = "manual") -> dict[str, Any]:
    """Dispara el barrido si no hay uno corriendo. Contesta de inmediato.

    Quien lo llame NO espera: son cientos de llamadas a ML y una petición HTTP
    que las aguarde se pasa del timeout del proxy (ya pasó con el backfill de
    channel.orders). El avance se lee en `estado()`.

    Si ya hay uno corriendo devuelve su estado y no encola otro: dos barridos a
    la vez se pisarían la cola de "más rancias" y gastarían el doble en las
    mismas publicaciones.
    """
    if _lock.locked():
        return estado()

    async def _con_candado() -> None:
        async with _lock:
            await _refrescar(cuenta, limite, motivo)

    _estado.update({"fase": "arrancando", "detalle": None, "motivo": motivo,
                    "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "fin": None,
                    "vistas": 0, "objetivo": 0, "cuenta": cuenta,
                    "cambios": 0, "sin_respuesta": 0, "denominador_movido": 0})
    asyncio.create_task(_con_candado())
    return estado()


# ── Los dos disparadores del scheduler ───────────────────────────────────────
#
# Los dos comparten candado con el disparo manual, y los dos se apagan con la
# MISMA variable. `SYNC_ENABLED` manda por encima: hablar con ML para refrescar
# datos de catálogo es justo lo que ese interruptor apaga (modo puros pedidos),
# igual que hace el webhook de items en routers/webhooks.py.


def _encendido() -> bool:
    return bool(settings.sync_enabled and settings.precios_venta_barrido)


async def barrido_arranque() -> None:
    """Pasada COMPLETA, una sola vez, poco después del boot.

    Existe para no esperar un ciclo entero del goteo (9.3 h con el default) a
    que se drene el atraso: el 26-ago eran 600 de 745 activas sin confirmar.
    """
    if not (_encendido() and settings.precios_venta_arranque):
        return
    await refrescar_en_fondo(limite=None, motivo="arranque")


async def barrido_periodico() -> None:
    """El goteo: las N más rancias, cada hora. Ver la sección CADENCIA."""
    if not _encendido():
        return
    await refrescar_en_fondo(limite=settings.precios_venta_por_hora,
                             motivo="goteo")
