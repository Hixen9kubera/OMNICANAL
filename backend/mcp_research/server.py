"""
server.py — El servidor MCP de research de Omnicanal. SOLO LECTURA.

Cuatro herramientas, una por pregunta, sueltas a propósito (decisión de
Brandon, 17-sep-2026): cada una devuelve SU dato y quien pregunta arma el
cruce según la necesidad —resurtido, precios, qué publicar, qué descontinuar—.
No hay una quinta que "ya entregue el cruce armado", porque el cruce cambia con
la pregunta y una herramienta que decide por su cuenta cuál hacer se equivoca
en silencio.

    visitas         enrich.listing_visits + enrich.market_listing_metrics
    ventas          channel.sales_daily_completa
    stock_libre     Odoo free_qty (NO qty_available, NO Woo)
    cajas           Odoo units_per_master_box

NINGUNA ESCRIBE. No hay una herramienta de escritura "por si acaso", y el
candado de `conexion.consultar` rechaza cualquier SQL que no empiece en
SELECT/WITH. Ver el encabezado de `conexion.py` para por qué tampoco se marca
la sesión de solo-lectura (el pooler 6543 comparte conexiones; ya reventó dos
veces la escritura de producción).

DOS TRANSPORTES
---------------
  · `stdio` — para probar en local desde Claude Desktop / Claude Code.
  · `http`  — Streamable HTTP para desplegarlo central y compartir una llave.
    Sin `MCP_AUTH_TOKEN` definido NO arranca en modo http: un servidor abierto
    a internet con el DSN de producción adentro no es una opción por omisión.

La llave es UNA, compartida (decisión de Brandon). Vale la pena saber lo que
eso implica: no se puede revocar por persona, así que cortarle el acceso a
alguien obliga a rotarla para todos. `_TOKENS` acepta varias separadas por
coma justamente para que el día que eso estorbe sea añadir una línea al
entorno, no rehacer el servidor.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from decimal import Decimal
from datetime import date, datetime
from typing import Any

# Cuando se corre como `python mcp_research/server.py`, el paquete padre no está
# en la ruta y los imports relativos fallan. Con `python -m mcp_research.server`
# sí funciona; esto hace que las dos formas den lo mismo.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "mcp_research"

from mcp.server.mcpserver import MCPServer          # noqa: E402
from mcp.types import ToolAnnotations               # noqa: E402

from . import bodega, conexion as cx, ventas as v_ventas, visitas as v_visitas  # noqa: E402

log = logging.getLogger("omnicanal.mcp")

SOLO_LECTURA = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                               idempotent_hint=True, open_world_hint=True)

INSTRUCCIONES = """
Datos de Kubera (Omnicanal) para research. TODO es de solo lectura.

Cuatro preguntas, cuatro herramientas; el cruce lo armas tú:
  · `visitas`      — tráfico de las publicaciones. NO hay serie diaria: son
                     fotos. Siempre vienen con `ventana_dias`, `dias_con_datos`
                     y su fecha de medición. Sin dato ≠ 0.
  · `ventas`       — piezas, bruto y neto por canal/cuenta/SKU/día. Antes de
                     jul-2026 sólo hay Mercado Libre; Walmart NO está ingerido
                     aunque sí vende. Lee `cobertura` antes de concluir.
  · `stock_libre`  — free_qty de Odoo: el físico MENOS lo reservado. No es el
                     stock de WooCommerce (que miente cuando el campo va vacío).
  · `cajas`        — piezas por caja de Odoo. Las cajas son un DERIVADO y salen
                     `null` cuando el factor falta o vale 1.

Toda respuesta trae `fuente`, `cobertura` y `medido_en`. Un número sin eso no
sirve para research: úsalos al citar. Mercado Libre son DOS cuentas (BEKURA =
Kubera, SANCORFASHION = San Corpe) y sumarlas esconde la mitad de la historia.
""".strip()

servidor = MCPServer(name="omnicanal-research", version="0.1.0",
                     instructions=INSTRUCCIONES)


def _serializable(v: Any) -> Any:
    """
    Decimal y fechas a algo que el protocolo pueda mandar.

    `sum(revenue)` en Postgres vuelve como `Decimal` y las fechas como objetos
    de `datetime`: los dos truenan al serializar a JSON. Se convierte aquí, en
    la frontera, y no en cada consulta, para que ninguna herramienta nueva se
    pueda olvidar.
    """
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _serializable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_serializable(x) for x in v]
    return v


@servidor.tool(
    title="Visitas de las publicaciones",
    annotations=SOLO_LECTURA,
    description=(
        "Visitas guardadas en Supabase, por PUBLICACIÓN y sumadas por SKU, más "
        "la foto mensual por SKU. No existe serie diaria: son fotos que se "
        "sobrescriben, así que cada cifra viene con su ventana (`ventana_dias`: "
        "7/30/60/90), cuántos días trae de verdad (`dias_con_datos`) y cuándo se "
        "midió. Un SKU sin datos sale en `sin_dato`, nunca como 0. Ojo: 93 "
        "publicaciones de ML sirven a DOS SKUs y sus visitas no se pueden "
        "repartir — se marcan como `compartida`."),
)
async def visitas(skus: list[str] | None = None,
                  listing_id: str | None = None,
                  canal: str | None = None,
                  cuenta: str | None = None,
                  ventana_dias: int | None = None,
                  periodo: str | None = None,
                  limite: int = 50) -> dict[str, Any]:
    """
    Args:
        skus: SKUs a consultar. Vacío = las publicaciones más vistas.
        listing_id: Una publicación concreta, ej. 'MLM2992981761'.
        canal: 'mercado_libre', 'amazon', 'temu', 'tiktok', 'walmart', 'general'.
        cuenta: Código de cuenta: BEKURA (Kubera) o SANCORFASHION (San Corpe).
        ventana_dias: Ventana de la foto: 7, 30, 60 o 90. Fíjala para poder comparar.
        periodo: Mes de la foto mensual, ej. '2026-09-01'.
        limite: Tope de renglones (máx. 500).
    """
    return _serializable(await v_visitas.consultar_visitas(
        skus=skus, listing_id=listing_id, canal=canal, cuenta=cuenta,
        ventana_dias=ventana_dias, periodo=periodo, limite=limite))


@servidor.tool(
    title="Ventas por canal",
    annotations=SOLO_LECTURA,
    description=(
        "Ventas guardadas en Supabase: piezas, bruto (revenue), comisión "
        "(sale_fee) y neto, agrupadas como pidas. La respuesta SIEMPRE trae "
        "`cobertura` por canal y `canales_sin_datos`, porque la ventana importa: "
        "antes de jul-2026 sólo hay Mercado Libre, y Walmart MX no tiene una "
        "sola fila aunque sí vende (falta la ingesta, no las ventas). Nunca "
        "interpretes una ausencia como cero sin mirar `cobertura`."),
)
async def ventas(skus: list[str] | None = None,
                 canal: str | None = None,
                 cuenta: str | None = None,
                 desde: str | None = None,
                 hasta: str | None = None,
                 agrupar_por: str = "canal",
                 detalle: bool = False,
                 diagnostico: bool = False,
                 limite: int = 50) -> dict[str, Any]:
    """
    Args:
        skus: SKUs a consultar. Vacío = todo el catálogo dentro de la ventana.
        canal: 'mercado_libre', 'amazon', 'temu', 'tiktok'.
        cuenta: BEKURA (Kubera), SANCORFASHION (San Corpe), AMAZON, TEMU, TIKTOK.
        desde: Fecha inicial ISO, ej. '2026-08-01'. Es la fecha de la VENTA.
        hasta: Fecha final ISO, ej. '2026-09-17'.
        agrupar_por: canal | cuenta | sku | sku_canal | dia | canal_dia | cuenta_dia.
        detalle: Añade el renglón por día/publicación además del agregado.
        diagnostico: Mide qué renglones tiene channel.sales_daily que la vista no expone.
        limite: Tope de renglones (máx. 500).
    """
    return _serializable(await v_ventas.consultar_ventas(
        skus=skus, canal=canal, cuenta=cuenta, desde=desde, hasta=hasta,
        agrupar_por=agrupar_por, detalle=detalle, diagnostico=diagnostico,
        limite=limite))


@servidor.tool(
    title="Stock libre en bodega",
    annotations=SOLO_LECTURA,
    description=(
        "Stock DISPONIBLE en bodega leyendo Odoo en vivo: `free_qty`, o sea el "
        "físico MENOS lo reservado en órdenes y borradores. No es el stock de "
        "WooCommerce ni el de channel.listings — ésos son copias y se han "
        "medido vacíos con piezas reales en bodega. Devuelve también `fisico`, "
        "`reservado` y, con `por_almacen`, el libre en TEXCO / TEXCO II / DROP "
        "OFF, que el free_qty global no distingue. `todos=true` barre el "
        "catálogo entero: ~24 s la primera vez (luego 10 min de caché), y con "
        "`orden='libre_asc'` lo primero que sale es stock NEGATIVO, que es "
        "descuadre de captura en Odoo, no escasez."),
)
async def stock_libre(skus: list[str] | None = None,
                      por_almacen: bool = False,
                      todos: bool = False,
                      orden: str = "libre_desc",
                      limite: int = 50) -> dict[str, Any]:
    """
    Args:
        skus: SKUs a consultar. Obligatorio salvo que uses `todos`.
        por_almacen: Desglosa el libre por almacén de Odoo (una vuelta más por almacén).
        todos: Barre el catálogo completo (~13,200 productos, cacheado 10 min).
        orden: Con `todos`: 'libre_desc' (más stock) o 'libre_asc' (a punto de agotarse).
        limite: Tope de productos (máx. 500).
    """
    return _serializable(await bodega.consultar_stock(
        skus=skus, por_almacen=por_almacen, todos=todos, orden=orden,
        limite=limite))


@servidor.tool(
    title="Cajas y piezas por caja",
    annotations=SOLO_LECTURA,
    description=(
        "Piezas por caja master de Odoo (`units_per_master_box`) y el volumen de "
        "esa caja (`cbm_master_box`), más las cajas ESTIMADAS que salen de las "
        "piezas libres y físicas. Odoo no cuenta cajas: son un derivado. El "
        "factor está en el 75.3% del catálogo y 644 productos lo tienen en 1, "
        "que no describe una caja sino la falta del dato — ésos salen `null`, "
        "no 1 caja por pieza."),
)
async def cajas(skus: list[str] | None = None, limite: int = 50) -> dict[str, Any]:
    """
    Args:
        skus: SKUs a consultar.
        limite: Tope de productos (máx. 500).
    """
    return _serializable(await bodega.consultar_cajas(skus=skus, limite=limite))


# ── Transporte HTTP: la llave ───────────────────────────────────────────────
def _tokens() -> list[str]:
    crudo = os.environ.get("MCP_AUTH_TOKEN", "")
    return [t.strip() for t in crudo.split(",") if t.strip()]


def _app_http(host: str):
    """Streamable HTTP con la llave al frente."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    validas = _tokens()

    @servidor.custom_route("/salud", methods=["GET"])
    async def salud(_req):                                   # noqa: ANN001
        return JSONResponse({"ok": True, "servidor": "omnicanal-research",
                             "solo_lectura": True,
                             "configurado": cx.configurado()})

    app = servidor.streamable_http_app(host=host)

    class Llave(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):        # noqa: ANN001
            if request.url.path == "/salud":
                return await call_next(request)
            cab = request.headers.get("authorization", "")
            dado = cab[7:].strip() if cab.lower().startswith("bearer ") else ""
            # Comparación normal y no `hmac.compare_digest` sería el descuido
            # típico: el tiempo de `==` filtra cuántos caracteres acertaste.
            import hmac
            if not any(hmac.compare_digest(dado, t) for t in validas):
                return JSONResponse({"error": "Llave inválida o ausente."},
                                    status_code=401)
            return await call_next(request)

    app.add_middleware(Llave)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="MCP de research de Omnicanal (solo lectura)")
    ap.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    args = ap.parse_args()

    falta = [k for k, v in cx.configurado().items() if not v]
    if falta:
        # Fallar aquí y no en la primera pregunta: un MCP que arranca y luego
        # contesta "no pude" en cada herramienta se diagnostica mucho peor.
        raise SystemExit(
            f"Faltan credenciales para: {falta}. Revisa SUPABASE_DB_URL y "
            f"ODOO_URL/ODOO_DB/ODOO_USER/ODOO_PASSWORD.")

    if args.transport == "stdio":
        # En stdio NO se pide llave: el transporte es el proceso hijo de quien
        # lo lanzó, no hay red de por medio.
        servidor.run(transport="stdio")
        return

    if not _tokens():
        raise SystemExit(
            "MCP_AUTH_TOKEN no está definida. En modo http este servidor no "
            "arranca sin llave: lleva el DSN de producción adentro.")

    import uvicorn
    log.info("MCP de research escuchando en %s:%s/mcp (solo lectura, %d llave(s))",
             args.host, args.port, len(_tokens()))
    uvicorn.run(_app_http(args.host), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
