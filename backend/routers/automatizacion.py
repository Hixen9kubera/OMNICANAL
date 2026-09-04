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
async def estado(canal: str | None = Query(None, description="acota los contadores "
                                                            "a esa pestaña")):
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
            "resumen": odoo_ventas_log.resumen(canal),
        }
    return await asyncio.to_thread(_leer)


@router.post("/interruptor")
async def interruptor(
    encendido: bool = Query(..., description="true enciende, false apaga"),
    motivo: str = Query("", description="por qué se apaga (queda en la bitácora)"),
    canal: str | None = Query(None, description="tiktok | temu. Sin canal, mueve "
                                                "el interruptor GENERAL"),
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
    if canal:
        return await asyncio.to_thread(
            odoo_ventas.fijar_canal, canal, bool(encendido), quien, motivo)
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


@router.post("/backfill")
async def backfill(
    dias: int = Query(7, ge=1, le=30),
    canal: str = Query("tiktok", description="tiktok | temu"),
):
    """
    Rellena la bitácora con lo que el automatismo HABRÍA hecho en los últimos
    N días. **No escribe una sola línea en Odoo.**

    PARA QUÉ SIRVE. Sin esto, la pestaña nace vacía y hay que esperar a que
    caigan ventas nuevas para ver algo — y con TikTok vendiendo ~6 al día, ver
    un ejemplo de cada caso (creada, parcial, sin producto, nació cancelada)
    puede tomar días. Esto reproduce la semana pasada de una vez.

    LAS FILAS QUEDAN MARCADAS COMO `simulado`, y eso no es un detalle: quien
    abra el tab tiene que poder distinguir de un golpe lo que de verdad pasó de
    lo que se reconstruyó. Una simulación que se ve idéntica a la realidad es
    peor que no tenerla.

    Aplica la MISMA decisión que el seam, para que lo que se ve aquí sea lo que
    va a pasar cuando se encienda — incluida la rama `nacio_cancelada`, que es
    la mitad del volumen.

    Idempotente: la llave es (canal, cuenta, external_order_id), así que
    correrlo dos veces no duplica.
    """
    from services import odoo_ventas_log, supabase_db as sdb

    def _correr() -> dict:
        ventas = sdb.fetch_all(f"""
            select o.external_order_id id, o.cuenta, o.estado_wc, o.creado_at,
                   (extract(epoch from (o.actualizado_at - o.creado_at)) < 10) nacio_asi,
                   json_agg(json_build_object(
                       'sku', i.sku::text, 'cantidad', i.cantidad,
                       'precio_unitario', i.precio_unitario, 'titulo', i.titulo)
                       order by i.linea) items
              from channel.orders o
              join channel.order_items i
                on i.canal = o.canal and i.external_order_id = o.external_order_id
             where o.canal = %(c)s
               and o.creado_at >= now() - interval '{int(dias)} days'
             group by 1, 2, 3, 4, 5
             order by o.creado_at desc""", {"c": canal})

        conteo: dict[str, int] = {}
        escritas = 0
        for v in ventas:
            if v["estado_wc"] == "cancelled" and v["nacio_asi"]:
                # Misma rama que el seam: la venta apareció ya muerta.
                r = {"ok": True, "accion": "nacio_cancelada", "canal": canal,
                     "order_id": v["id"],
                     "motivo": "la venta apareció ya cancelada: no se crea orden"}
            else:
                r = odoo_ventas.crear_orden(canal, v["id"],
                                            v["creado_at"].isoformat(),
                                            v["items"], dry_run=True)
            acc = r.get("accion") or "error"
            conteo[acc] = conteo.get(acc, 0) + 1
            if odoo_ventas_log.registrar(canal, v["cuenta"], v["id"], r, v["items"]):
                escritas += 1
        return {"ok": True, "ventas_revisadas": len(ventas), "filas_escritas": escritas,
                "por_accion": conteo, "dias": dias, "canal": canal,
                "nota": "simulación: NADA se escribió en Odoo"}

    return await asyncio.to_thread(_correr)


# Las llamadas del sondeo de Temu. La lista es FIJA y toda de lectura: el
# endpoint no acepta el nombre del método desde fuera, porque un proxy hacia
# "cualquier endpoint de Temu" sería una llave para escribirle al marketplace
# desde el panel. Aquí solo se puede preguntar lo que está en esta tabla.
_SONDEO_TEMU: list[tuple[str, str, dict]] = [
    # Los parámetros son los que usa `temu.listar_productos`, no una versión
    # inventada: `goodsSearchType` es OBLIGATORIO y va como ENTERO (de cadena
    # devuelve 3000000), y la página es `pageNo` — `page` se ignora EN SILENCIO.
    # La primera versión de esta sonda mandaba `page` y sin `goodsSearchType`,
    # fallaba por forma, y como el veredicto se apoyaba en ella, el panel dijo
    # "la IP no está en la lista blanca" cuando la IP estaba perfectamente bien
    # y Temu SÍ contestaba el listado de órdenes. Una sonda mal armada que
    # concluye algo falso es peor que no tener sonda.
    ("puerta", "bg.local.goods.list.query",
     {"goodsSearchType": 3, "pageNo": 1, "pageSize": 1}),
    # LO QUE DECIDE TODO: sin un listado de órdenes no hay forma de ENTERARSE
    # de una venta de Temu, y sin eso no hay nada que automatizar.
    ("ordenes", "bg.order.list.v2.get", {"pageNumber": 1, "pageSize": 10}),
    ("ordenes", "bg.order.list.get", {"pageNumber": 1, "pageSize": 10}),
    # Estaban bloqueados con 3000032 (API sensible, requiere permiso de Temu).
    ("importes", "bg.order.amount.query", {"parentOrderSnList": ["X"]}),
    ("importes", "temu.order.amount.v2.query", {"parentOrderSnList": ["X"]}),
    # LA GUÍA SÍ EXISTE, solo que no con estos nombres. El sondeo del 1-sep lo
    # destapó leyendo los CÓDIGOS en vez de quedarse en "falla":
    #   3000037 "interface upgrade requirements" → el endpoint existe y hay una
    #           versión más nueva que hay que usar.
    #   3000004 "type has been sunset"           → existía y se retiró: tiene
    #           sucesor.
    #   3000003 "type not exists"                → ese nombre nunca existió.
    # O sea que dos de los tres apuntan a un reemplazo. Se prueban las variantes
    # de versión para encontrarlo.
    ("guia", "bg.logistics.shipment.get", {}),
    ("guia", "bg.logistics.shipment.v2.get", {}),
    ("guia", "bg.order.shippinginfo.get", {}),
    ("guia", "bg.order.shippinginfo.v2.get", {}),
    ("guia", "bg.logistics.shipment.v2.query", {}),
    ("guia", "bg.order.logistics.v2.get", {}),
    ("guia", "bg.logistics.online.shippingservice.get", {}),
]


# ¿APARECE LA GUÍA? Se busca DENTRO de la orden, no solo en endpoints de
# envío: si Temu ya la manda en el propio pedido, no hace falta ninguna
# llamada extra. Se rastrean los nombres que usa esta familia de APIs —
# `trackingNumber` y `mailNo`/`waybill` (el vocabulario chino de paquetería)
# son los dos frentes, y buscar solo el inglés dejaría fuera la mitad.
_CLAVES_GUIA = ("tracking", "waybill", "mailno", "expressno", "shipno",
                "logisticsno", "deliveryno", "shipmentno", "expresscompany",
                "shippingcompany", "logisticscompany", "carrier")

def _buscar_guia(nodo, ruta="", hallazgos=None, hondo=0):
    """Recorre la respuesta buscando algo que se parezca a una guía."""
    if hallazgos is None:
        hallazgos = []
    if hondo > 6 or len(hallazgos) > 20:
        return hallazgos
    if isinstance(nodo, dict):
        for k, v in nodo.items():
            aqui = f"{ruta}.{k}" if ruta else k
            if any(c in k.lower() for c in _CLAVES_GUIA):
                hallazgos.append({"campo": aqui, "con_valor": bool(v),
                                  "valor": str(v)[:40] if v else None})
            _buscar_guia(v, aqui, hallazgos, hondo + 1)
    elif isinstance(nodo, list) and nodo:
        _buscar_guia(nodo[0], f"{ruta}[]", hallazgos, hondo + 1)
    return hallazgos


@router.get("/temu/sondeo")
async def temu_sondeo():
    """
    ¿Se puede ya automatizar Temu? Pregunta y contesta con evidencia.

    POR QUÉ ES UN ENDPOINT Y NO UN SCRIPT. La lista blanca de IPs de Temu solo
    trae la salida de Railway, así que desde una laptop TODA llamada devuelve
    `5000003 NOT_IN_IP_WHITE_LIST` y no se puede saber nada. Desde aquí sí.

    LA PREGUNTA QUE IMPORTA es la segunda fila: **si Temu nos deja LISTAR
    órdenes**. M2E está desinstalado y el webhook depende de cuatro trámites
    ajenos; si tampoco hay listado, no existe forma de enterarse de una venta de
    Temu y no hay nada que automatizar. Si el listado responde, el camino está
    abierto y el resto es código nuestro.

    Solo lectura, lista de llamadas fija, y ningún fallo se propaga: cada
    intento reporta su código de error, que es justamente el dato buscado
    (`3000032` = existe pero nos falta permiso; `5000003` = IP fuera).
    """
    from services import temu

    resultados = []
    for grupo, tipo, params in _SONDEO_TEMU:
        fila: dict = {"grupo": grupo, "endpoint": tipo}
        try:
            r = await asyncio.wait_for(temu.llamar(tipo, params), timeout=25)
            fila.update(ok=True,
                        llaves=sorted(r.keys())[:12] if isinstance(r, dict) else None)
            fila["_crudo"] = r if isinstance(r, dict) else None
        except asyncio.TimeoutError:
            fila.update(ok=False, error="timeout a los 25 s")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            fila.update(ok=False, error=msg[:600],
                        codigo=next((c for c in ("5000003", "3000032", "3000031",
                                                 "3000025", "3000012")
                                     if c in msg), None))
        resultados.append(fila)

    # Si el listado contestó, se extrae LO QUE IMPORTA: cuántas órdenes tiene
    # Temu de verdad. Ese número contra las 2 que vio nuestra tubería es el
    # tamaño real del hueco. Se sacan CONTEOS Y NOMBRES DE CAMPO, no el
    # contenido: una orden trae domicilio y nombre del comprador, y esto no es
    # el lugar para volcarlos.
    resumen_ordenes: dict | None = None
    exitosa = next((r for r in resultados
                    if r["grupo"] == "ordenes" and r.get("ok")), None)
    if exitosa and isinstance(exitosa.get("_crudo"), dict):
        d = exitosa["_crudo"]
        lista = next((v for v in d.values() if isinstance(v, list)), [])
        total = next((v for k, v in d.items()
                      if isinstance(v, int) and "total" in k.lower()), None)
        primera = lista[0] if lista and isinstance(lista[0], dict) else {}
        # Se baja UN NIVEL MÁS. El primer corte solo veía el sobre
        # (`orderList`, `parentOrderMap`) y eso no dice si la orden trae SKU,
        # cantidad o dinero — que es lo único que decide si se puede armar el
        # pedido sin una segunda llamada por venta.
        renglones = primera.get("orderList") or []
        detalle = {
            "campos_del_sobre": sorted(primera.keys())[:20],
            "campos_del_padre": sorted((primera.get("parentOrderMap") or {}).keys())[:25],
            "renglones_en_la_primera": len(renglones),
            "campos_del_renglon": (sorted(renglones[0].keys())[:30]
                                   if renglones and isinstance(renglones[0], dict) else []),
        }
        # DOS FUENTES DISTINTAS, y confundirlas fue un error real: el rótulo
        # decía "SÍ viene en la orden" cuando la guía venía de la llamada de
        # envío. `guias` es lo que trae el PEDIDO; `guias_envio`, lo que trae
        # el endpoint de logística.
        guias = _buscar_guia(d)
        guias_envio: list = []

        # LA GUÍA, SEGUNDO INTENTO: con una orden DE VERDAD.
        # El sondeo del 1-sep dejó claro que dos endpoints de envío existen y
        # solo rechazaban la llamada por falta de parámetros —`120012016 The
        # parentOrder or Order is invalid` y `180020003 Invalid param`—, muy
        # distinto de los `3000003 type not exists` de los otros tres. Así que
        # se toma el primer pedido del listado y se les pregunta en serio.
        padre = (primera.get("parentOrderMap") or {}).get("parentOrderSn")
        hijo = (renglones[0].get("orderSn") if renglones and isinstance(renglones[0], dict)
                else None)
        if padre or hijo:
            for tipo in ("bg.logistics.shipment.v2.get", "bg.order.shippinginfo.v2.get"):
                fila = {"grupo": "guia (con orden real)", "endpoint": tipo}
                try:
                    r2 = await asyncio.wait_for(
                        temu.llamar(tipo, {k: v for k, v in
                                           (("parentOrderSn", padre), ("orderSn", hijo))
                                           if v}),
                        timeout=25)
                    encontrados = _buscar_guia(r2) if isinstance(r2, dict) else []
                    fila.update(ok=True,
                                llaves=sorted(r2.keys())[:12] if isinstance(r2, dict) else None)
                    if encontrados:
                        guias_envio.extend(encontrados)
                except asyncio.TimeoutError:
                    fila.update(ok=False, error="timeout a los 25 s")
                except Exception as exc:  # noqa: BLE001
                    m = str(exc)
                    fila.update(ok=False, error=m[:600],
                                codigo=next((c for c in ("5000003", "3000032", "3000003",
                                                         "3000037", "3000004")
                                             if c in m), None))
                resultados.append(fila)
        resumen_ordenes = {
            "endpoint": exitosa["endpoint"],
            "total_declarado": total,
            "en_esta_pagina": len(lista),
            "campos_por_orden": sorted(primera.keys())[:30],
            "guia_en_la_orden": bool(guias),
            "guia_en_envio": bool(guias_envio),
            "guia_con_valor": any(g["con_valor"] for g in guias + guias_envio),
            "campos_de_guia": guias + guias_envio,
            **detalle,
        }
    for r in resultados:
        r.pop("_crudo", None)

    # EL VEREDICTO SE APOYA EN LO QUE DECIDE, que es el listado de órdenes — no
    # en la sonda de la puerta. Y la IP se juzga por el SÍNTOMA correcto: si
    # estuviera fuera de la lista blanca, TODAS fallarían con 5000003; que una
    # sola conteste ya prueba que la IP entra.
    ordenes = [r for r in resultados if r["grupo"] == "ordenes"]
    alguna_responde = any(r.get("ok") for r in resultados)
    # Basta con que UNA falle por IP y ninguna conteste: los endpoints que
    # devuelven otro código lo hacen por parámetros o por permisos, no por red.
    # Exigir que TODAS traigan 5000003 dejaba el diagnóstico mudo en cuanto una
    # sonda tuviera mal un parámetro.
    hay_ip_fuera = any(r.get("codigo") == "5000003" for r in resultados)
    if hay_ip_fuera and not alguna_responde:
        veredicto = ("la IP de este servidor NO está en la lista blanca de Temu: "
                     "ninguna llamada pasa")
    elif any(r.get("ok") for r in ordenes):
        veredicto = ("SE PUEDE: Temu deja LISTAR órdenes desde este servidor. "
                     "El camino para automatizar Temu está abierto.")
    elif alguna_responde:
        veredicto = ("La API responde, pero NO deja listar órdenes: sin listado "
                     "no hay forma de enterarse de una venta de Temu.")
    else:
        veredicto = "Ninguna llamada respondió; ver el error de cada fila."
    return {"veredicto": veredicto, "resultados": resultados,
            "ordenes": resumen_ordenes}


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


# ═════════════════════════════════════════════════════════════════════════════
# WALMART · PIEZA 6
# ═════════════════════════════════════════════════════════════════════════════
@router.get("/walmart/feed/{feed_id:path}")
async def walmart_feed(feed_id: str):
    """
    El VEREDICTO de un feed: qué SKU entró, cuál no y con qué error exacto.

    Es la otra mitad del botón de publicar. Walmart contesta el envío con un
    `feedId` y nada más; el resultado llega minutos después y solo se sabe
    preguntando. Dar por bueno el acuse fue lo que produjo los "9 feeds sin
    fallos" del 4-ago que en realidad fueron cero.

    `{feed_id:path}` y no `{feed_id}`: los ids de Walmart llevan `@` y a veces
    caracteres que un segmento normal no acepta.
    """
    from services import walmart
    if not walmart.disponible():
        return {"ok": False, "motivo": "Walmart no está configurado."}
    return await walmart.feed_estado(feed_id)


@router.get("/walmart/pedidos")
async def walmart_pedidos(dias: int = 30):
    """
    Las ventas de Walmart TAL CUAL las cuenta Walmart, sin crear nada.

    Existe porque este endpoint nunca se había llamado y la primera consulta
    destapó 8 ventas reales sin ingerir. Aquí se ve la verdad del canal antes de
    decidir si se enciende la ingesta.
    """
    from services import pedidos_walmart, walmart
    if not walmart.disponible():
        return {"ok": False, "motivo": "Walmart no está configurado."}
    from datetime import datetime, timedelta, timezone
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d")
    ordenes = await walmart.listar_pedidos(desde)
    filas = []
    for o in ordenes:
        n = pedidos_walmart._normalizar(o)  # noqa: SLF001
        filas.append({
            "purchase_order": n["id"], "fecha": n["fecha"], "total": n["total"],
            "estado_walmart": (n.get("_estados") or [None])[0],
            "estado_wc": pedidos_walmart._ESTADOS_WC.get(  # noqa: SLF001
                str((n.get("_estados") or [""])[0])),
            "wfs": n.get("_wfs"),
            "skus": [i["sku"] for i in n["items"]],
            "guia": n.get("guia"), "paqueteria": n.get("paqueteria"),
        })
    return {"ok": True, "desde": desde, "total": len(filas), "pedidos": filas,
            "ingesta": {
                "encendida": bool(getattr(settings, "pedidos_walmart_sondeo_enabled", False)),
                "solo_registro": bool(getattr(settings, "pedidos_walmart_solo_registro", True)),
            }}


@router.post("/walmart/pedidos/sondear")
async def walmart_sondear(dias: int = 7, solo_registro: bool = True):
    """
    Corre el sondeo A MANO.

    `solo_registro=True` por omisión y a propósito: el valor por omisión de un
    endpoint que crea pedidos y mueve stock tiene que ser el inocuo. Pasarlo en
    False es un acto deliberado, y aun así respeta `PEDIDOS_WALMART_SOLO_REGISTRO`
    cuando se manda `None`.
    """
    from services import pedidos_walmart
    return await pedidos_walmart.sondear(dias=dias, solo_registro=solo_registro)
