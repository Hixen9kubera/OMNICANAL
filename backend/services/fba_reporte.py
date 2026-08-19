"""
El reporte "Manage FBA Inventory" — parseo, guardado y refresco automático.

Fase 2 de la pestaña /analisis/fba (Eduardo, 18-ago): el mismo reporte que se
sube a mano se puede PEDIR a Amazon por la Reports API de SP-API
(`GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA` — verificado: trae exactamente las
columnas del export de Seller Central que se usó para construir la pestaña).

El parseo y el guardado viven AQUÍ y no en el router a propósito: la subida
manual y el refresco automático tienen que pasar por el MISMO código, o el día
que uno se corrija y el otro no, la pestaña dirá cosas distintas según cómo
entró el dato.

El refresco corre en SEGUNDO PLANO (asyncio.create_task): Amazon tarda de uno
a varios minutos en generar el reporte y ninguna petición HTTP debe quedarse
esperándolo. El endpoint dispara y contesta; la página va leyendo `estado()`.

Regla 11 de la casa: todo HTTP por httpx ASYNC; el guardado (psycopg2,
bloqueante) sale a un hilo con asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import io
import logging
import time
from typing import Any

import httpx

from config import settings
from services import supabase_db as sdb
from services.amazon import _access_token

log = logging.getLogger("omnicanal.fba")

_REPORT_TYPE = "GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA"
_RUTA_REPORTS = "/reports/2021-06-30/reports"
_RUTA_DOCS = "/reports/2021-06-30/documents"
# Amazon tarda 1-10 min en generar; más de 15 se da por perdido.
_ESPERA_S = 15
_TIMEOUT_TOTAL_S = 15 * 60

# Columnas del export que se leen. Si Amazon cambia el reporte, el error debe
# decir QUÉ columna falta, no reventar con un KeyError.
COLS = {
    "sku", "fnsku", "asin", "product-name", "your-price",
    "afn-total-quantity",
    "afn-fulfillable-quantity", "afn-reserved-quantity",
    "afn-unsellable-quantity", "afn-warehouse-quantity",
    "afn-inbound-working-quantity", "afn-inbound-shipped-quantity",
    "afn-inbound-receiving-quantity", "per-unit-volume",
}


def _n(v: Any) -> int:
    try:
        return int(str(v).strip() or 0)
    except (TypeError, ValueError):
        return 0


def _f(v: Any) -> float | None:
    try:
        s = str(v).strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def parsear(texto: str, nombre: str) -> list[tuple]:
    """CSV/TSV del reporte → filas para `guardar`. ValueError si no es él.

    LAS OCHO CANTIDADES Y CÓMO SE RELACIONAN (verificado SKU por SKU en los
    1,258 del reporte del 18-ago, sin una sola excepción):

        fulfillable + reserved + unsellable = afn-warehouse-quantity
        warehouse   + inbound(3)            = afn-total-quantity

    `warehouse` es lo que HOY está físicamente en la bodega y paga almacenaje;
    `total` es todo lo comprometido con FBA, incluido lo que va en camino.
    `total_quantity` se guarda aunque sea derivable: si algún día una de las
    dos identidades deja de cumplirse, tener el número original de Amazon es
    lo único que permite notarlo (Eduardo, 18-ago).
    """
    sep = "\t" if "\t" in texto.splitlines()[0] else ","
    lector = csv.DictReader(io.StringIO(texto), delimiter=sep)
    faltan = COLS - set(lector.fieldnames or [])
    if faltan:
        raise ValueError("el archivo no es el reporte 'Manage FBA Inventory': "
                         f"le faltan columnas {sorted(faltan)}")
    filas: list[tuple] = []
    vistos: set[str] = set()
    for r in lector:
        sku = (r.get("sku") or "").strip()
        if not sku or sku in vistos:   # un duplicado rompería la PK
            continue
        vistos.add(sku)
        filas.append((
            sku, (r.get("fnsku") or "").strip() or None,
            (r.get("asin") or "").strip() or None,
            (r.get("product-name") or "").strip() or None,
            _f(r.get("your-price")),
            _n(r.get("afn-fulfillable-quantity")),
            _n(r.get("afn-reserved-quantity")),
            _n(r.get("afn-unsellable-quantity")),
            _n(r.get("afn-warehouse-quantity")),
            _n(r.get("afn-inbound-working-quantity")),
            _n(r.get("afn-inbound-shipped-quantity")),
            _n(r.get("afn-inbound-receiving-quantity")),
            _f(r.get("per-unit-volume")),
            _n(r.get("afn-total-quantity")),
            nombre[:200],
        ))
    if not filas:
        raise ValueError("el reporte no trae ni un SKU")
    return filas


def guardar(filas: list[tuple]) -> int:
    """DELETE + INSERT en UNA transacción: la foto nueva reemplaza a la vieja
    sin ventana en la que la tabla se vea vacía. BLOQUEANTE — va en to_thread."""
    with sdb.get_cursor() as cur:
        cur.execute("delete from ops.fba_snapshot")
        args = b",".join(
            cur.mogrify("(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", f)
            for f in filas
        )
        cur.execute(
            b"insert into ops.fba_snapshot (sku, fnsku, asin, product_name,"
            b" price, fulfillable, reserved, unsellable, warehouse,"
            b" inbound_working, inbound_shipped, inbound_receiving,"
            b" per_unit_volume, total_quantity, report_name) values " + args
        )
        return cur.rowcount


def decodificar(crudo: bytes) -> str:
    """utf-8 primero; el export real de Seller Central viene en cp1252."""
    try:
        return crudo.decode("utf-8")
    except UnicodeDecodeError:
        return crudo.decode("cp1252", errors="replace")


# ── Refresco automático por la Reports API ──────────────────────────────────

# Estado visible para la página. Un solo refresco a la vez: dos reportes
# simultáneos gastarían la cuota de la API para llegar al mismo snapshot.
_estado: dict[str, Any] = {"fase": "inactivo", "detalle": None,
                           "inicio": None, "fin": None}
_lock = asyncio.Lock()


def estado() -> dict[str, Any]:
    return dict(_estado)


def _marcar(fase: str, detalle: str | None = None) -> None:
    _estado["fase"] = fase
    _estado["detalle"] = detalle
    if fase in ("listo", "error"):
        _estado["fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log.info("FBA refresco: %s%s", fase, f" — {detalle}" if detalle else "")


async def _refrescar() -> None:
    tok = await _access_token()
    if not tok:
        _marcar("error", "sin credenciales de Amazon en este ambiente")
        return
    base = settings.amazon_sp_api_endpoint
    cab = {"x-amz-access-token": tok}
    try:
        async with httpx.AsyncClient(timeout=60.0) as cli:
            # 1. Pedir el reporte
            _marcar("solicitando")
            r = await cli.post(f"{base}{_RUTA_REPORTS}", headers=cab, json={
                "reportType": _REPORT_TYPE,
                "marketplaceIds": [settings.amazon_marketplace_id],
            })
            r.raise_for_status()
            report_id = r.json()["reportId"]

            # 2. Esperar a que Amazon lo genere
            _marcar("esperando", f"reporte {report_id}")
            doc_id = None
            limite = time.monotonic() + _TIMEOUT_TOTAL_S
            while time.monotonic() < limite:
                await asyncio.sleep(_ESPERA_S)
                r = await cli.get(f"{base}{_RUTA_REPORTS}/{report_id}", headers=cab)
                r.raise_for_status()
                d = r.json()
                st = d.get("processingStatus")
                if st == "DONE":
                    doc_id = d["reportDocumentId"]
                    break
                if st in ("CANCELLED", "FATAL"):
                    _marcar("error", f"Amazon devolvió {st}")
                    return
            if not doc_id:
                _marcar("error", "Amazon no terminó el reporte en 15 minutos")
                return

            # 3. Descargar el documento (URL prefirmada, sin cabecera de auth)
            _marcar("descargando")
            r = await cli.get(f"{base}{_RUTA_DOCS}/{doc_id}", headers=cab)
            r.raise_for_status()
            doc = r.json()
            r = await cli.get(doc["url"])
            r.raise_for_status()
            crudo = r.content
            if doc.get("compressionAlgorithm") == "GZIP":
                crudo = gzip.decompress(crudo)

        # 4. El MISMO parser y el MISMO guardado que la subida manual
        filas = parsear(decodificar(crudo), f"SP-API {report_id}")
        n = await asyncio.to_thread(guardar, filas)
        _marcar("listo", f"{n} SKUs")
    except Exception as exc:  # noqa: BLE001
        _marcar("error", f"{type(exc).__name__}: {exc}")


async def refrescar_programado() -> None:
    """Entrada del CRON diario (scheduler). A diferencia del botón, si hay un
    refresco corriendo ESPERA el candado en vez de saltarse — el job diario no
    debe perderse por coincidir con un clic."""
    async with _lock:
        await _refrescar()


async def refrescar_en_fondo() -> dict[str, Any]:
    """Dispara el refresco si no hay uno corriendo. Contesta de inmediato."""
    if _lock.locked():
        return estado()
    async def _con_candado() -> None:
        async with _lock:
            await _refrescar()
    _estado.update({"fase": "arrancando", "detalle": None,
                    "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "fin": None})
    asyncio.create_task(_con_candado())
    return estado()
