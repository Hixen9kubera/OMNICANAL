"""
visitas_ml.py — Visitas de nuestras publicaciones y la conversión real.

De dónde sale: `GET /items/{id}/visits/time_window?last=N&unit=day`, el mismo
endpoint que ya usa el módulo de Competencia (`competencia_ml.visitas_serie`).
Es de los pocos que ML deja consultar sin peros, y devuelve la serie diaria más
el total de la ventana — que es lo que hace falta aquí, porque la conversión
tiene que comparar visitas y ventas del MISMO período.

LA RESTRICCIÓN QUE MANDA EN EL DISEÑO: ML acepta **un item por llamada**
(`/visits/items` con dos ids responde HTTP 400). No hay multiget, así que cada
publicación cuesta una llamada — por eso Competencia se limita a 25 por corrida.
Aquí eso se resuelve con caché: la medición vive en MySQL (tabla NUESTRA,
`ml_visitas`, mismo terreno que `ml_envio_real`) y solo se vuelve a pedir cuando
pasó el TTL. Las visitas son un acumulado diario, no un dato de segundo a
segundo: refrescarlas cada 6 h es de sobra y hace la página instantánea.

QUÉ NO ES: no cubre Amazon (no hay equivalente por esta vía), así que la
conversión que sale de aquí es de Mercado Libre y la UI debe decirlo. Y una
publicación PAUSADA conserva las visitas que juntó cuando estaba viva: su
conversión habla del pasado, no de hoy.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services import db, meli

log = logging.getLogger("omnicanal.visitas_ml")

_API = "https://api.mercadolibre.com"

# Cuántas horas vale una medición antes de volver a preguntar. Las visitas se
# acumulan por día: pedirlas más seguido gasta llamadas sin cambiar el número.
TTL_HORAS = 6

_DDL = """
CREATE TABLE IF NOT EXISTS ml_visitas (
  listing_id    VARCHAR(40)  NOT NULL,
  dias          SMALLINT     NOT NULL,
  cuenta        VARCHAR(32)  NULL,
  visitas       INT          NULL,
  dias_datos    SMALLINT     NULL,
  consultado_at DATETIME     NOT NULL,
  PRIMARY KEY (listing_id, dias)
) CHARACTER SET utf8mb4
"""
_tabla_lista = False


def _asegurar_tabla() -> None:
    global _tabla_lista
    if not _tabla_lista:
        db.execute(_DDL)
        _tabla_lista = True


def leer(listing_ids: list[str], dias: int) -> dict[str, dict[str, Any]]:
    """Mediciones cacheadas para esas publicaciones. Solo lectura."""
    ids = [str(i) for i in listing_ids if i]
    if not ids:
        return {}
    _asegurar_tabla()
    marcas = ",".join(["%s"] * len(ids))
    filas = db.fetch_all(
        f"SELECT listing_id, visitas, dias_datos, consultado_at FROM ml_visitas "
        f"WHERE dias=%s AND listing_id IN ({marcas})", (dias, *ids))
    return {str(f["listing_id"]): f for f in filas}


async def completar(pares: list[tuple[str, str]], dias: int,
                    presupuesto: int = 80) -> int:
    """
    Mide las publicaciones cuya medición falta o ya venció el TTL.
    `pares` = [(cuenta, listing_id), …]; devuelve cuántas consultó.
    """
    from datetime import datetime, timedelta

    _asegurar_tabla()
    cache = leer([i for _, i in pares], dias)
    vence = datetime.utcnow() - timedelta(hours=TTL_HORAS)
    faltan = [
        (c, str(i)) for (c, i) in pares
        if str(i) not in cache or cache[str(i)]["consultado_at"] < vence
    ]
    lote = faltan[: max(0, presupuesto)]
    if not lote:
        return 0

    import httpx

    tokens: dict[str, str | None] = {}
    sem = asyncio.Semaphore(8)
    resultados: list[tuple[str, str, int | None, int | None]] = []

    async with httpx.AsyncClient(base_url=_API, timeout=20.0) as cli:

        async def una(cuenta: str, iid: str) -> None:
            if cuenta not in tokens:
                tokens[cuenta] = meli._access_token(cuenta)
            tk = tokens.get(cuenta)
            if not tk:
                return
            async with sem:
                cab = {"Authorization": f"Bearer {tk}"}
                ruta = f"/items/{iid}/visits/time_window"
                par = {"last": dias, "unit": "day"}
                r = await cli.get(ruta, params=par, headers=cab)
                if r.status_code == 401:
                    nuevo = await meli._renovar_con_candado(cuenta)
                    if not nuevo:
                        return
                    tokens[cuenta] = nuevo
                    r = await cli.get(ruta, params=par,
                                      headers={"Authorization": f"Bearer {nuevo}"})
                if r.status_code != 200:
                    return  # sin fila: se reintenta en la siguiente carga
                d = r.json()
                # `results` trae un punto por día CON DATOS, que no siempre son
                # los `dias` pedidos: se guarda cuántos vinieron para que la UI
                # no presuma una ventana completa que ML no dio.
                resultados.append((cuenta, iid,
                                   d.get("total_visits"),
                                   len(d.get("results") or [])))

        await asyncio.gather(*(una(c, i) for c, i in lote))

    def _guardar() -> None:
        # UN solo INSERT con todas las filas. Escribirlas de a una costaba un
        # viaje de red por medición al MySQL de Hostinger: 20 publicaciones
        # tardaban ~11 s aunque las llamadas a ML sumaran menos de uno.
        vals = ", ".join(["(%s, %s, %s, %s, %s, UTC_TIMESTAMP())"] * len(resultados))
        params: list[Any] = []
        for cuenta, iid, visitas, dd in resultados:
            params += [iid, dias, cuenta, visitas, dd]
        db.execute(
            "INSERT INTO ml_visitas (listing_id, dias, cuenta, visitas,"
            f" dias_datos, consultado_at) VALUES {vals}"
            " ON DUPLICATE KEY UPDATE cuenta=VALUES(cuenta),"
            " visitas=VALUES(visitas), dias_datos=VALUES(dias_datos),"
            " consultado_at=UTC_TIMESTAMP()",
            tuple(params))

    if resultados:
        await asyncio.to_thread(_guardar)
    log.info("visitas_ml: %d publicaciones medidas (%d pendientes, ventana %dd)",
             len(resultados), len(faltan) - len(lote), dias)
    return len(resultados)
