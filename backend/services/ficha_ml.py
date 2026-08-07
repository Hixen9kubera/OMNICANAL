"""
ficha_ml.py — El peso que la BODEGA DE ML midió de cada publicación.

PARA QUÉ. Un SKU publicado en las dos cuentas debería ser el mismo objeto. Si
ML pesó 40 g en una y 60 g en la otra, no lo es: son dos productos distintos
compartiendo una clave — y entonces comparten también un costo, un inventario
y un margen que no le corresponden a uno de los dos. Casos reales (censo del
6-ago): TEC-0393-ROS 40 g / 60 g (audífonos BT 5.3 contra V5.2),
CUNA-0011-GRI 580 g / 1,140 g, MASC-0044-NEG 1,040 g / 1,820 g.

POR QUÉ EL PESO Y NO EL TÍTULO. El título separa mal: de 67 SKUs con títulos
distintos entre cuentas, la mayoría resultaron ser el mismo producto descrito
de dos formas ("Lona Sombra" y "Malla de Tela"). El peso es medición, no
redacción.

SOLO CUENTA LO QUE ML MIDIÓ (`PACKAGE_WEIGHT`), nunca lo que declaramos
nosotros (`SELLER_PACKAGE_WEIGHT`). Comparar una báscula contra una captura
detecta capturas malas, que ya sabemos que abundan, y no dice nada sobre si
son dos productos: al mezclar ambas fuentes el censo pasó de 26 hallazgos
sólidos a 462 en su mayoría falsos.

Barato: el multiget acepta 20 publicaciones por llamada, así que el catálogo
entero cuesta ~205 llamadas. Se cachea en MySQL con TTL largo porque el peso
de un producto no cambia de un día para otro.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from services import db, meli

log = logging.getLogger("omnicanal.ficha_ml")

_API = "https://api.mercadolibre.com"

# El peso de un producto no cambia; solo cambia si RECICLAN la publicación,
# que es justamente lo que queremos detectar. Una semana es suficiente.
TTL_HORAS = 168

_DDL = """
CREATE TABLE IF NOT EXISTS ml_ficha (
  listing_id    VARCHAR(40)  NOT NULL,
  cuenta        VARCHAR(32)  NULL,
  titulo        VARCHAR(255) NULL,
  peso_g        DECIMAL(10,2) NULL,
  medido        TINYINT(1)   NOT NULL DEFAULT 0,
  consultado_at DATETIME     NOT NULL,
  PRIMARY KEY (listing_id)
) CHARACTER SET utf8mb4
"""
_tabla_lista = False
_RE_NUM = re.compile(r"([\d.]+)")


def _asegurar_tabla() -> None:
    global _tabla_lista
    if not _tabla_lista:
        db.execute(_DDL)
        _tabla_lista = True


def _gramos(txt: str | None) -> float | None:
    m = _RE_NUM.search(txt or "")
    if not m:
        return None
    val = float(m.group(1))
    return val * 1000 if "kg" in (txt or "").lower() else val


def leer(listing_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = [str(i) for i in listing_ids if i]
    if not ids:
        return {}
    _asegurar_tabla()
    marcas = ",".join(["%s"] * len(ids))
    filas = db.fetch_all(
        f"SELECT listing_id, cuenta, titulo, peso_g, medido, consultado_at "
        f"FROM ml_ficha WHERE listing_id IN ({marcas})", tuple(ids))
    return {str(f["listing_id"]): f for f in filas}


async def completar(pares: list[tuple[str, str]], presupuesto: int = 400) -> int:
    """Consulta las publicaciones sin ficha (o vencida). `pares` = [(cuenta, id)]."""
    from datetime import datetime, timedelta

    _asegurar_tabla()
    cache = leer([i for _, i in pares])
    vence = datetime.utcnow() - timedelta(hours=TTL_HORAS)
    faltan = [(c, str(i)) for (c, i) in pares
              if str(i) not in cache or cache[str(i)]["consultado_at"] < vence]
    lote = faltan[: max(0, presupuesto)]
    if not lote:
        return 0

    import httpx

    por_cuenta: dict[str, list[str]] = {}
    for cuenta, iid in lote:
        por_cuenta.setdefault(cuenta, []).append(iid)

    tokens: dict[str, str | None] = {}
    resultados: list[tuple[str, str, str | None, float | None, bool]] = []
    sem = asyncio.Semaphore(6)

    async with httpx.AsyncClient(base_url=_API, timeout=30.0) as cli:

        async def bloque(cuenta: str, ids: list[str]) -> None:
            if cuenta not in tokens:
                tokens[cuenta] = meli._access_token(cuenta)
            tk = tokens.get(cuenta)
            if not tk:
                return
            async with sem:
                par = {"ids": ",".join(ids), "attributes": "id,title,attributes"}
                r = await cli.get("/items", params=par,
                                  headers={"Authorization": f"Bearer {tk}"})
                if r.status_code == 401:
                    nuevo = await meli._renovar_con_candado(cuenta)
                    if not nuevo:
                        return
                    tokens[cuenta] = nuevo
                    r = await cli.get("/items", params=par,
                                      headers={"Authorization": f"Bearer {nuevo}"})
                if r.status_code != 200:
                    return
                for fila in r.json():
                    b = fila.get("body") or {}
                    iid = b.get("id")
                    if not iid:
                        continue
                    att = {a["id"]: a.get("value_name") for a in b.get("attributes", [])}
                    pesado = att.get("PACKAGE_WEIGHT")   # lo que midió su bodega
                    resultados.append((
                        cuenta, iid, (b.get("title") or "")[:255],
                        _gramos(pesado or att.get("SELLER_PACKAGE_WEIGHT")),
                        pesado is not None))

        tareas = []
        for cuenta, ids in por_cuenta.items():
            for i in range(0, len(ids), 20):     # tope del multiget de ML
                tareas.append(bloque(cuenta, ids[i:i + 20]))
        await asyncio.gather(*tareas)

    def _guardar() -> None:
        vals = ", ".join(["(%s, %s, %s, %s, %s, UTC_TIMESTAMP())"] * len(resultados))
        params: list[Any] = []
        for cuenta, iid, titulo, peso, medido in resultados:
            params += [iid, cuenta, titulo, peso, 1 if medido else 0]
        db.execute(
            "INSERT INTO ml_ficha (listing_id, cuenta, titulo, peso_g, medido,"
            f" consultado_at) VALUES {vals}"
            " ON DUPLICATE KEY UPDATE cuenta=VALUES(cuenta), titulo=VALUES(titulo),"
            " peso_g=VALUES(peso_g), medido=VALUES(medido),"
            " consultado_at=UTC_TIMESTAMP()",
            tuple(params))

    if resultados:
        await asyncio.to_thread(_guardar)
    log.info("ficha_ml: %d publicaciones consultadas (%d pendientes)",
             len(resultados), len(faltan) - len(lote))
    return len(resultados)


# Arriba de esta proporción entre el peso de una cuenta y el de la otra, ya no
# es variación de empaque. 1.25 = 25%%; con menos entran diferencias de caja.
FACTOR_DIVERGENCIA = 1.25


def divergencia(fichas: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    ¿Las publicaciones de este SKU describen objetos de pesos distintos?

    Solo comparan las que ML PESÓ, y solo si son de cuentas distintas: dos
    publicaciones de la misma cuenta pueden ser dos presentaciones del mismo
    producto, y ahí el dato no significa lo mismo.
    """
    medidas = [f for f in fichas
               if f and f.get("medido") and (f.get("peso_g") or 0) > 0]
    if len({f.get("cuenta") for f in medidas}) < 2:
        return None
    pesos = [float(f["peso_g"]) for f in medidas]
    lo, hi = min(pesos), max(pesos)
    if lo <= 0 or hi / lo < FACTOR_DIVERGENCIA:
        return None
    return {
        "ratio": round(hi / lo, 2),
        "min_g": round(lo),
        "max_g": round(hi),
        "detalle": [{"cuenta": f.get("cuenta"), "peso_g": round(float(f["peso_g"])),
                     "titulo": f.get("titulo")} for f in medidas],
    }
