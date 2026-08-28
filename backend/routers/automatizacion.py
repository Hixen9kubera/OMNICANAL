"""
automatizacion.py — Lo que el panel automatiza sin que nadie lo empuje.

  GET  /api/automatizacion/estado           → banderas y contadores
  GET  /api/automatizacion/ordenes-odoo     → la bitácora que pinta el tab
  GET  /api/automatizacion/simular?venta=   → QUÉ orden armaría esa venta, sin
                                              escribir nada en Odoo

Hoy solo cubre las órdenes de venta en Odoo (TikTok/Temu). El nombre es de la
pestaña, no del módulo: aquí van cayendo los demás automatismos conforme se
construyan.

`/simular` es la herramienta de la fase de observación: contesta "¿qué habría
hecho?" sin depender de que haya una venta nueva ni de las banderas — es
seguro aunque todo esté encendido, porque nunca llama a `create`.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request

from config import settings
from services import odoo_ventas, odoo_ventas_log

router = APIRouter(prefix="/api/automatizacion", tags=["automatizacion"])


@router.get("/estado")
async def estado():
    """
    En qué escalón va el encendido, y qué ha pasado en 30 días.

    Las cuatro banderas son una ESCALERA (ver config.py): observar → crear en
    borrador → dejar de descontar en Woo → confirmar. Que salgan juntas en la
    pantalla es para que nadie tenga que adivinar en cuál está.

    En hilo: lee el interruptor y la bitácora de kubera, y las dos bloquean.
    """
    def _leer():
        interruptor = odoo_ventas.estado_interruptor()
        encendido = interruptor["encendido"]
        return {
            "odoo_ventas": {
                **interruptor,
                "habilitado": encendido,
                "solo_registro": bool(settings.odoo_ventas_solo_registro),
                "confirmar": bool(settings.odoo_ventas_confirmar),
                "canales": sorted(odoo_ventas.canales()),
                "escalon": (
                    "apagado" if not encendido
                    else "1 · observando (no escribe en Odoo)" if settings.odoo_ventas_solo_registro
                    else "2 · creando en borrador" if not settings.odoo_ventas_confirmar
                    else "4 · creando y confirmando (Odoo reserva)"
                ),
            },
            "resumen": odoo_ventas_log.resumen(),
        }
    return await asyncio.to_thread(_leer)


@router.post("/interruptor")
async def interruptor(
    encendido: bool = Query(..., description="true enciende, false apaga"),
    motivo: str = Query("", description="por qué se apaga (queda en la bitácora)"),
    request: Request = None,  # noqa: B008
):
    """
    Enciende o apaga la generación de órdenes de venta en Odoo.

    APAGAR ES INMEDIATO Y SEGURO: lo que ya se creó en Odoo se queda (son
    órdenes reales de un pedido real), pero no nace ninguna nueva. El apagado
    tarda a lo más 30 segundos en surtir efecto en todos los procesos — es el
    vencimiento del caché del interruptor, no una cola.

    ENCENDER es lo que hay que pensar dos veces, no apagar. Por eso no hay
    confirmación aquí: la advertencia vive en la pantalla, donde está la persona.

    Queda registrado QUIÉN lo movió: un flujo que toca inventario y contabilidad
    tiene que poder contestar eso.
    """
    # La identidad la deja el middleware en `request.state.identidad`, y el
    # nombre para la bitácora es su `.actor` (correo, etiqueta de la llave o
    # "anonimo") — ver core/identidad.py::Identidad. La primera versión buscaba
    # `state.usuario`/`state.email`, que NO existen: `actualizado_por` quedaba
    # siempre en NULL y la tabla no podía contestar quién encendió el flujo,
    # que era justo la razón de tener esa columna.
    quien = ""
    try:
        ident = getattr(request.state, "identidad", None)
        quien = str(getattr(ident, "actor", "") or "")
    except Exception:  # noqa: BLE001
        pass
    return await asyncio.to_thread(
        odoo_ventas.fijar_interruptor, bool(encendido), quien, motivo)


@router.get("/ordenes-odoo")
def ordenes_odoo(
    limite: int = Query(100, ge=1, le=500),
    canal: str | None = Query(None, description="tiktok | temu"),
    solo_problemas: bool = Query(False,
                                 description="solo lo que alguien tiene que mirar: "
                                             "sin crear, o con cobertura parcial"),
):
    """
    Una fila por venta: la orden de Odoo, su guía, sus SKUs con imagen y
    unidades, y el STOCK QUE HABÍA EN EL INSTANTE DE LA VENTA.

    Ese último dato no se puede pedir en vivo — `free_qty` ya cambió. Sale de la
    foto congelada en `ops.odoo_sale_order_items`.
    """
    return {"ordenes": odoo_ventas_log.historial(limite, canal, solo_problemas)}


@router.get("/simular")
async def simular(
    venta: str = Query(..., description="external_order_id de la venta"),
    canal: str = Query("tiktok", description="tiktok | temu"),
):
    """
    Qué orden de venta armaría esa venta AHORA: almacén elegido, cobertura y la
    foto de stock. **No escribe en Odoo** pase lo que pase con las banderas.

    En un hilo: XML-RPC bloquea, y dentro de la corrutina esto congelaría el
    backend entero mientras Odoo contesta (regla 11).
    """
    from services import supabase_db as sdb

    filas = await asyncio.to_thread(sdb.fetch_all, """
        select i.sku::text sku, i.cantidad, i.precio_unitario, i.titulo
          from channel.order_items i
         where i.canal = %(c)s and i.external_order_id = %(o)s
         order by i.linea""", {"c": canal, "o": str(venta)})
    if not filas:
        return {"ok": False, "motivo": "esa venta no tiene líneas registradas"}

    items = [{"sku": f["sku"], "cantidad": f["cantidad"],
              "precio_unitario": float(f["precio_unitario"] or 0),
              "titulo": f["titulo"] or ""} for f in filas]
    # `dry_run=True` es lo que hace verdadera la promesa de arriba: sin él,
    # este endpoint dejaría de simular en cuanto se apagara SOLO_REGISTRO.
    return await asyncio.to_thread(
        odoo_ventas.crear_orden, canal, str(venta), None, items, False, True)
