"""
Refresco A LA MEDIDA del corte de valor: los precios de venta, todos de golpe.

POR QUÉ NO LO HACE EL SYNC (Eduardo, 20-ago-2026).

El sync progresivo toma 60 publicaciones por cuenta cada 15 min y las ordena
"primero lo que nunca se ha visto, luego lo más viejo". Ese orden es correcto
para mantener el catálogo al día, pero no sirve para valuar. Medido en su
primera corrida real:
de las 133 publicaciones que alcanzó, 12 estaban activas y 12 tenían stock en
FULL; las otras 121 eran pausadas con cero piezas. De las 796 publicaciones con
stock —las únicas que entran al valor— llevaba 12. Y ninguno de los cinco casos
con la brecha de precio más grande había sido tocado.

O sea: el barrido paga el costo de las 4,977 del catálogo para entregar tarde
las 796 que el reporte necesita, y aun entonces el resultado sería un mosaico
—unos precios de las 15:00 y otros de las 06:00— cuando lo que se firma es una
FOTO. Las ofertas de ML traen cuenta regresiva: un precio de hace diez horas
puede ser de una promoción que ya terminó.

Este módulo refresca SOLO las publicaciones que el reporte va a valuar, en una
pasada. Después, `price_sale_at` deja de ser una curiosidad y pasa a ser la
prueba: todos los precios del Excel se leyeron en la misma ventana de minutos.

ESTE ES EL ÚNICO CAMINO desde el 20-ago-2026 (decisión de Eduardo). El flag
`ML_PRECIO_VENTA` del sync quedó apagado: `price_sale` tiene un solo lector —el
reporte de valor— así que observarlo cada 15 minutos era pagar ~11,500 llamadas
diarias por un dato que nadie mira entre corte y corte.

Y había un costo peor que el volumen. Con los dos escribiendo, el barrido volvía
a tocar publicaciones que este refresco ya había leído y les sellaba una fecha
nueva: medido, 5 de 788 bastaban para estirar la ventana de 1 minuto a 4.7
horas. El dato de esas 5 era MÁS fresco, pero el corte dejaba de ser simultáneo
— y para un valor, la simultaneidad es lo que lo hace comparable consigo mismo.

Lo ya observado por el sync no se pierde: sigue en la columna y el `coalesce`
lo usa como respaldo mientras nadie pida un refresco.

Regla 11 de la casa: todo HTTP por httpx ASYNC; el guardado (psycopg2,
bloqueante) sale a un hilo con asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from services import meli
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.precios")

_ML_API = "https://api.mercadolibre.com"
# Cuántas publicaciones se piden a la vez. ML aguanta más, pero el objetivo no
# es exprimirlo: es terminar las ~800 en un par de minutos sin que un pico de
# concurrencia se lea como abuso desde su lado.
_EN_PARALELO = 8
_TIMEOUT_S = 20.0

# Qué publicaciones entran: las que tienen stock en FULL, que son exactamente
# las que el corte de valor va a valuar. Una publicación puede aparecer bajo el
# SKU padre y bajo el hijo con el MISMO listing_id (ver la nota de `pub` en
# fulfillment._SQL_INV_BASE): se pide UNA vez y se escribe en las dos filas.
_SQL_OBJETIVO = """
select l.listing_id, a.legacy_code as cuenta
  from channel.listings l
  join core.accounts a on a.id = l.account_id
 where l.canal = 'mercado_libre'
   and coalesce(l.stock_full, 0) > 0
   and l.listing_id is not null
   and lower(coalesce(l.situacion, '')) <> 'closed'
   and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
 group by 1, 2
"""

# `price_sale_at` se sella SIEMPRE que hubo observación, aunque el precio no
# haya cambiado: la pregunta que contesta es "¿cuándo se miró?", no "¿cuándo
# cambió?". Sin eso no se distingue "hoy no hay promoción" de "nadie preguntó".
_SQL_GUARDAR = """
update channel.listings
   set price_sale = %(precio)s, price_sale_at = now()
 where canal = 'mercado_libre' and listing_id = %(listing_id)s
"""

_estado: dict[str, Any] = {"fase": "inactivo", "detalle": None, "inicio": None,
                           "fin": None, "vistas": 0, "objetivo": 0, "cuenta": None}
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


def _objetivo(cuenta: str | None) -> list[tuple[str, str]]:
    """BLOQUEANTE — va en to_thread."""
    filas = sdb.fetch_all(_SQL_OBJETIVO, {"cuenta": cuenta})
    return [(str(f["listing_id"]), str(f["cuenta"])) for f in filas]


def _guardar(pares: list[tuple[str, float]]) -> int:
    """BLOQUEANTE — va en to_thread. Una transacción para toda la tanda."""
    if not pares:
        return 0
    with sdb.get_cursor() as cur:
        for listing_id, precio in pares:
            cur.execute(_SQL_GUARDAR, {"precio": precio, "listing_id": listing_id})
    return len(pares)


async def _precio(cli: httpx.AsyncClient, listing_id: str, cuenta: str,
                  tokens: dict[str, str], sem: asyncio.Semaphore) -> float | None:
    """El precio que el comprador PAGA. None ante cualquier fallo.

    None se descarta en vez de escribirse: el contrato de la columna es que
    NULL significa "no observado", así que un fallo de red nunca debe borrar
    una observación buena de ayer.
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
            v = (r.json() or {}).get("amount")
            return float(v) if v not in (None, "") else None
        except Exception:  # noqa: BLE001
            return None


async def _refrescar(cuenta: str | None) -> None:
    objetivo = await asyncio.to_thread(_objetivo, cuenta)
    if not objetivo:
        _marcar("listo", "no hay publicaciones con stock en FULL", vistas=0, objetivo=0)
        return

    cuentas = {c for _, c in objetivo}
    tokens: dict[str, str] = {}
    for c in cuentas:
        t = await asyncio.to_thread(meli._access_token, c)
        if t:
            tokens[c] = t
    faltan = cuentas - set(tokens)
    if faltan:
        # Sin token no se puede preguntar, y seguir con las demás dejaría un
        # corte a medias que se ve completo. Mejor decirlo y no valuar mal.
        _marcar("error", f"sin token de ML para {', '.join(sorted(faltan))}")
        return

    _marcar("consultando", f"{len(objetivo)} publicaciones", vistas=0,
            objetivo=len(objetivo), cuenta=cuenta)
    sem = asyncio.Semaphore(_EN_PARALELO)
    vistas = 0
    try:
        async with httpx.AsyncClient(base_url=_ML_API, timeout=_TIMEOUT_S) as cli:
            # Por tandas para poder ir guardando y mostrando avance: una sola
            # espera de 800 peticiones no deja ver nada hasta el final, y si
            # truena a la mitad se pierde todo lo ya consultado.
            TANDA = 80
            for i in range(0, len(objetivo), TANDA):
                trozo = objetivo[i:i + TANDA]
                precios = await asyncio.gather(*[
                    _precio(cli, lid, cta, tokens, sem) for lid, cta in trozo
                ])
                pares = [(lid, p) for (lid, _), p in zip(trozo, precios) if p is not None]
                vistas += await asyncio.to_thread(_guardar, pares)
                _marcar("consultando", f"{vistas} de {len(objetivo)}", vistas=vistas)
        sin_dato = len(objetivo) - vistas
        _marcar("listo",
                f"{vistas} de {len(objetivo)} publicaciones"
                + (f" · {sin_dato} sin respuesta de ML" if sin_dato else ""),
                vistas=vistas)
    except Exception as exc:  # noqa: BLE001
        _marcar("error", f"{type(exc).__name__}: {exc}", vistas=vistas)


async def refrescar_en_fondo(cuenta: str | None = None) -> dict[str, Any]:
    """Dispara el refresco si no hay uno corriendo. Contesta de inmediato.

    El endpoint NO espera: son ~800 llamadas a ML y una petición HTTP que las
    aguarde se pasa del timeout del proxy (ya pasó con el backfill de
    channel.orders). La página lee el avance en `estado()`.
    """
    if _lock.locked():
        return estado()

    async def _con_candado() -> None:
        async with _lock:
            await _refrescar(cuenta)

    _estado.update({"fase": "arrancando", "detalle": None,
                    "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "fin": None,
                    "vistas": 0, "objetivo": 0, "cuenta": cuenta})
    asyncio.create_task(_con_candado())
    return estado()
