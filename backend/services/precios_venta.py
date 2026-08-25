"""
DORMIDO — no lo llama nadie hoy. Léase antes de borrarlo o de revivirlo.

QUÉ HACE. Lee de ML el precio que el comprador PAGA
(`/items/{id}/sale_price?context=channel_marketplace`) para las publicaciones
con stock en FULL, y lo guarda en `channel.listings.price_sale`. En segundo
plano, por tandas, ~790 publicaciones en unos dos minutos. Probado en
producción el 20-ago-2026: 794 de 794, todas 200, sin un solo 429.

POR QUÉ EXISTE. Porque `channel.listings.price` NO es lo que la gente paga.
`/items/{id}.price` se queda en el precio de LISTA cuando la promoción la monta
una CAMPAÑA de ML. Medido contra 265 SKUs con venta real en
`channel.order_items`: la mediana de `price` está en **1.71x lo transado**, la
de `sale_price` en 1.03x. No es una diferencia de matiz.

POR QUÉ ESTÁ DORMIDO. Se construyó para un reporte de valor del inventario en
FULL que se DESARMÓ el 21-ago-2026 (decisión de Eduardo — ver la entrada
v0.249.0 del README). Al desarmarlo, `price_sale` se quedó sin lectores.

⚠️ ESO YA NO ES CIERTO (v0.261.0, 25-ago-2026). `price_sale` SÍ tiene lector: el
margen de la pestaña Omnicanal (`publicaciones_panel._oferta`). Y su única
corrida —la del 20/21-ago, desde este archivo— es exactamente la que dejó **665
ofertas rancias** aplicándose al margen durante cuatro días, porque nadie
volvió a correrlo y ningún lector preguntaba por `price_sale_at`. Ahora el panel
pregunta: lo observado antes del último cambio de la publicación se marca **sin
confirmar** y NO se aplica.

Moraleja de este archivo, y por eso queda escrita aquí: **una foto de precios
sin quien la repita es una mentira con fecha de caducidad.** Correrlo una vez
fue peor que no correrlo nunca.

CUÁNDO REVIVIRLO. El refresco al día ya NO depende de este barrido: lo hace el
webhook del topic `items_prices` (~413 avisos/día), que pide el precio de oferta
solo de la publicación que ML dice que cambió —
`inventario.refrescar_ml_item_id(con_precio_venta=True)`.

Lo que este archivo todavía puede aportar es un **barrido de arranque**: las
publicaciones que llevan días sin aviso no se confirman solas, y una pasada
completa las pondría al día de golpe. Eso sigue necesitando un disparador
(endpoint o botón) y ACTA — escribe a producción y a la API de ML.

Falta también el margen del panel de Análisis: `fulfillment._BASE` y
`_SQL_MARGEN_REAL_TOP` siguen calculando contra `price`, o sea contra un precio
que nadie cobra. Ese arreglo llegó a estar escrito —`coalesce(price_sale,
price)` en los cuatro lectores— y se revirtió (v0.244.0). Cuando se retome:
copiar la regla de `_oferta`, NO el coalesce pelón — es justo el coalesce sin
fecha el que causó esto.

El flag `ML_PRECIO_VENTA` del sync sigue existiendo y sigue apagado a propósito
— ver la nota en `config.py`.

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
