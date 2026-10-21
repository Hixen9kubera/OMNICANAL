"""
fanout.py — Monitoreo y simulación del fan-out de stock DROP.

  GET  /api/fanout/estado           → flags, cola, contadores y últimos eventos.
  GET  /api/fanout/recuperar        → el recuperador: configuración, última vuelta y
                                      qué reencolaría ahora mismo (solo lee).
  POST /api/fanout/recuperar        → corre una vuelta ya (respeta sus banderas).
  GET  /api/fanout/excedentes       → TikTok/Temu por ENCIMA de Woo: configuración,
                                      última vuelta y qué bajaría ahora (solo lee).
  POST /api/fanout/excedentes?canal= → corre una vuelta ya (respeta sus banderas).
  GET  /api/fanout/seguro           → seguro «stock 0 ⇒ fuera de la venta» (siempre
                                      encendido, pegado al fan-out): ensayo forzado,
                                      marcas, las APAGADAS QUE YA TIENEN STOCK, las
                                      soltadas que siguen fuera de la venta y lo que
                                      quedó a medias (solo lee).
  POST /api/fanout/seguro/soltar    → {sku, canal}: suelta la marca (no toca el canal;
                                      con el fan-out apagado no hace nada).
  GET  /api/fanout/vivo?desde_id=   → la página en vivo: veredicto, cadena y cambios.
  GET  /api/fanout/matriz           → SKUs × canales contra Woo (o el libro de kubera
                                      con FANOUT_FUENTE=kubera: `stock_rotulo`).
  GET  /api/fanout/rastro?sku=&fin= → un cambio salto por salto.
  GET  /api/fanout/historia?sku=&dias=&liga= → la línea de trazabilidad de un SKU (con sus devoluciones).
  GET  /api/fanout/full             → la pestaña FULL (stock, avisos, cuadre, salud).
  GET  /api/fanout/full/camino      → las salidas de Odoo a FULL (caché de envíos).
  GET  /api/fanout/bodegas          → la pestaña Bodegas: kubera (0064/0065) contra
                                      Odoo por bodega y contra lo que copia stock_watch.
  GET  /api/fanout/bodegas/sku?sku= → el cajón de un SKU: su fila, libro y OV.
  GET  /api/fanout/devoluciones?dias=&cuenta= → la pestaña Devoluciones: las cajas de ML
                                      que regresan a nuestra bodega y si ya entraron a
                                      Odoo, ligadas por la guía de la nota (solo lee).
  GET  /api/fanout/devoluciones/sku/{sku}?dias= → el cuadre de un SKU: cada subida de
                                      Odoo con las recepciones que la hicieron (solo lee).
  GET  /api/fanout/simular?sku=     → QUÉ haría con ese SKU ahora mismo, sin
                                      encolar ni escribir (seguro siempre).
  POST /api/fanout/encolar?sku=     → lo mete a la cola real (respeta dry-run).
"""
from __future__ import annotations

from fastapi import APIRouter, Path, Query, Request, Response
from pydantic import BaseModel

from config import settings

from services import fanout_stock

router = APIRouter(prefix="/api/fanout", tags=["fanout"])


@router.get("/estado")
def estado():
    """Estado del fan-out: flags, pendientes, contadores y bitácora reciente."""
    return fanout_stock.estado()


@router.get("/recuperar")
def recuperar_estado():
    """
    El recuperador de cambios sin repartir (services/fanout_recuperar.py): su
    configuración, su última vuelta y qué SKUs reencolaría AHORA MISMO con esa
    configuración. Solo lee: sirve para ver el hueco antes de encender la bandera.
    """
    from services import fanout_recuperar
    est = fanout_recuperar.estado()
    try:
        filas = fanout_recuperar.candidatos(est["horas"], est["gracia_min"], est["tope"])
        est["ahora"] = {"candidatos": len(filas), "muestra": [f["sku"] for f in filas[:20]]}
    except Exception as exc:  # noqa: BLE001 — la consulta es informativa
        est["ahora"] = {"error": str(exc)[:200]}
    return est


@router.post("/recuperar")
def recuperar():
    """Corre una vuelta del recuperador YA. Respeta FANOUT_RECUPERAR_ENABLED y
    FANOUT_ENABLED: con cualquiera apagada, contesta por qué no hizo nada."""
    from services import fanout_recuperar
    return fanout_recuperar.revisar()


@router.get("/excedentes")
def excedentes_estado():
    """
    Los excedentes de TikTok y Temu (services/fanout_excedentes.py): su
    configuración, su última vuelta y qué publicaciones están AHORA por encima de
    la foto de Woo. Solo lee: sirve para ver el hueco antes de encender la bandera.
    """
    from services import fanout_excedentes as fe
    est = fe.estado()
    try:
        est["ahora"] = {c: [{"sku": f["sku"], "canal": f["canal_stock"], "woo": f["stock_woo"]}
                            for f in fe.candidatos(c, fe._tope())] for c in fe.CANALES}
    except Exception as exc:  # noqa: BLE001 — la consulta es informativa
        est["ahora"] = {"error": str(exc)[:200]}
    return est


@router.post("/excedentes")
def excedentes(canal: str = Query(..., description="tiktok | temu")):
    """Corre una vuelta YA para un canal. Respeta FANOUT_EXCEDENTES_ENABLED,
    FANOUT_ENABLED y FANOUT_DRY_RUN: con cualquiera apagada, dice por qué no hizo nada."""
    from services import fanout_excedentes
    return fanout_excedentes.revisar(canal)


class SeguroSoltar(BaseModel):
    sku: str
    canal: str


@router.get("/seguro")
def seguro_estado():
    """
    El seguro «stock 0 ⇒ fuera de la venta» (services/fanout_seguro.py). No tiene
    interruptor: corre siempre que el fan-out esté encendido. Dice si algún canal
    está en ensayo FORZADO y por qué, las publicaciones que tiene apagadas (su
    marca), los intentos de hoy (cuenta LLAMADAS al canal, no éxitos; ya no hay
    tope), la última vuelta y qué apagaría ahora mismo.

    Las listas que no deben quedar en silencio: `con_stock` (apagadas por el
    seguro que YA tienen stock en Woo y siguen fuera de la venta), `soltadas` (las
    que el seguro dejó de considerar suyas y siguen fuera), `a_medias` por canal
    (intentos que nadie cerró: los mira el siguiente censo) y, en TikTok,
    `en_auditoria`. Solo lee: no llama a ningún canal.
    """
    from services import fanout_seguro
    return fanout_seguro.estado()


@router.post("/seguro/soltar")
def seguro_soltar(datos: SeguroSoltar):
    """Suelta la marca del seguro para un SKU en un canal: deja de considerarla
    suya (no la reactivará) y destraba el corte de «3 errores». No toca el canal:
    la publicación se queda como esté. Con el fan-out apagado no hace nada, y
    sólo anota donde hay algo que soltar (una marca vigente o un corte)."""
    from services import fanout_seguro
    return fanout_seguro.soltar(datos.sku, datos.canal)


@router.get("/vivo")
def vivo(desde_id: int = Query(0, ge=0, description="Solo los cambios con id mayor")):
    """
    Lo que pinta la página del fan-out: veredicto, cadena, canales, qué atender,
    la serie de 17 días y los cambios recientes. La página lo pide cada pocos
    segundos con el último `id` que ya tiene: los cambios nuevos llegan frescos y
    los agregados salen de una caché de 20 s. Solo lee.
    """
    from services import fanout_vivo
    return fanout_vivo.vivo(desde_id)


@router.get("/matriz")
def matriz():
    """SKUs × canales contra Woo: los que ofrecen de más, los rechazados, los
    distintos entre Odoo y Woo y los que se movieron hace poco. Solo lee."""
    from services import fanout_vivo
    return fanout_vivo.matriz()


@router.get("/rastro")
def rastro(sku: str = Query(..., description="SKU del cambio"),
           fin: str = Query(..., description="Marca de tiempo del cambio (ISO, la de /vivo)")):
    """Un cambio salto por salto: cuándo cambió Woo, cuánto esperó, cuánto tardó
    en escribirse y qué contestó cada canal. Solo lee."""
    from services import fanout_vivo
    return fanout_vivo.rastro(sku, fin)


@router.get("/historia")
def historia(sku: str = Query(..., min_length=2, description="SKU"),
             dias: int = Query(14, ge=1, le=60, description="Días hacia atrás"),
             liga: int | None = Query(None, ge=0, le=30,
                                      description="Días tras la llegada de una devolución en que se "
                                                  "busca su reingreso en Odoo (default FANOUT_DEVOL_LIGA_DIAS)")):
    """La línea de trazabilidad de un SKU: cada cambio de stock en Woo, cada reparto
    con lo que contestó cada canal, lo que cada canal reportó después y sus
    devoluciones, ligadas por fecha con la subida de Odoo que probablemente es su
    reingreso. Solo lee."""
    from services import fanout_vivo
    return fanout_vivo.historia(sku, dias, liga_dias=liga)


@router.get("/simular")
def simular(sku: str = Query(..., description="SKU a simular")):
    """
    Plan de fan-out para un SKU: stock DROP leído, objetivo y qué pasaría con
    cada publicación (escribir / sin cambio / omitir, con el motivo).
    NO escribe ni encola: es seguro aunque el fan-out esté encendido.
    """
    return fanout_stock.plan(sku)


@router.get("/full")
def full_resumen():
    """La pestaña FULL: stock vendible por cuenta (sin las filas padre de las
    publicaciones con variantes), lo de hoy por tipo de aviso, los avisos de las
    últimas 24 h, el cuadre diario de 9 días contra la foto del sync y la salud.
    Todo de kubera, con caché de 20 s; las salidas de Odoo van en `/full/camino`.
    Solo lee."""
    from services import fanout_full
    return fanout_full.resumen()


@router.get("/full/camino")
async def full_camino():
    """Las salidas de Odoo a FULL por cuenta: abiertas, en camino y sin número de
    envío. Usa el MISMO caché que `/api/fulfillment/envios` (Odoo tarda ~15 s; el
    caché dura 120 s). Si Odoo no contesta, la pestaña sigue y este pedazo dice
    por qué falta. Solo lee."""
    import asyncio

    from services import cache_lectura, fanout_full, fulfillment_envios

    async def _producir():
        # XML-RPC bloquea: en un hilo (regla 11).
        return await asyncio.to_thread(fulfillment_envios.leer)

    try:
        datos, edad = await cache_lectura.con_cache("fulfillment_envios", {}, _producir)
    except Exception as exc:  # noqa: BLE001 — Odoo caído no tumba la pestaña
        return {"ok": False, "motivo": f"Odoo no contestó ({str(exc)[:160]})"}
    return {"ok": True, **fanout_full.camino(datos.get("envios") or []),
            "generado": datos.get("generado"), "edad_s": edad}


@router.get("/bodegas")
async def bodegas(request: Request):
    """La pestaña Bodegas: por SKU, Odoo por bodega (TEXCO, TEX2, DROP), kubera
    por bodega (`almacen.stock_almacen`), el «Woo esperado» tal como lo calcula
    hoy stock_watch y el Woo de su foto; más las bodegas de kubera, las banderas
    de la 0064/0065, la vigía y la última pasada de stock_watch.
    Odoo con caché de 10 min: si no contesta, la pestaña sigue con kubera y Woo.
    Sin las tablas de la 0064/0065 responde 200 y lo dice. Solo lee."""
    import asyncio

    from services import fanout_bodegas
    # psycopg2 y XML-RPC bloquean, y codificar ~0.9 MB de JSON también: todo en
    # un hilo (regla 11). Va ya codificado (y en gzip si el navegador lo acepta)
    # para que FastAPI no lo pase por `jsonable_encoder` dentro del loop.
    gz = "gzip" in (request.headers.get("accept-encoding") or "").lower()
    cuerpo, comprimido = await asyncio.to_thread(fanout_bodegas.resumen_bytes, gz)
    cabeceras = {"Vary": "Accept-Encoding", "Cache-Control": "no-store"}
    if comprimido:
        cabeceras["Content-Encoding"] = "gzip"
    return Response(content=cuerpo, media_type="application/json", headers=cabeceras)


@router.get("/bodegas/sku")
async def bodegas_sku(sku: str = Query(..., min_length=2, max_length=80, description="SKU")):
    """El cajón de un SKU (esté o no en la tabla): su fila, sus últimos
    movimientos en el libro (`almacen.stock_mov`) y sus órdenes de venta. Solo
    lee."""
    import asyncio

    from services import fanout_bodegas
    cuerpo = await asyncio.to_thread(lambda: fanout_bodegas.a_json(fanout_bodegas.detalle_sku(sku)))
    return Response(content=cuerpo, media_type="application/json", headers={"Cache-Control": "no-store"})


@router.get("/devoluciones")
async def devoluciones(dias: int = Query(60, ge=1, le=90, description="Días hacia atrás"),
                       cuenta: str | None = Query(None, description="BEKURA | SANCORFASHION (sin ella, las dos)")):
    """La pestaña Devoluciones: las devoluciones de ML que van a NUESTRA bodega y se
    abrieron o llegaron en el periodo, en grupos (buscar en Bodega, otro SKU, Odoo ya
    la tiene, en Odoo, a SCRAP, en camino, no regresan), cada una ligada con su
    recepción de Odoo por la guía que Bodega anota en la nota. Odoo con caché de 10
    min: si no contesta, la pestaña sigue con el estado de ML y lo dice
    (`odoo_ok=false`). Solo lee."""
    import asyncio

    from fastapi import HTTPException

    from services import fanout_devoluciones
    cuenta = (cuenta or "").strip().upper() or None
    if cuenta and cuenta not in fanout_devoluciones.CUENTAS:
        raise HTTPException(status_code=400, detail=f"cuenta desconocida: {cuenta}")
    # psycopg2 y XML-RPC bloquean: en un hilo (regla 11).
    return await asyncio.to_thread(fanout_devoluciones.bandeja, dias, cuenta)


@router.get("/devoluciones/sku/{sku}")
async def devoluciones_sku(sku: str = Path(..., min_length=2, max_length=80, description="SKU"),
                           dias: int = Query(14, ge=1, le=60, description="Días hacia atrás")):
    """El cuadre de un SKU: cada subida de Odoo que stock_watch copió a Woo, con las
    recepciones que la hicieron (devolución, compra, traslado o ajuste), la devolución
    de kubera que trae cada una por su guía y lo que hizo el reparto; más las
    devoluciones que llegaron y todavía no entran. Solo lee."""
    import asyncio

    from services import fanout_devoluciones
    return await asyncio.to_thread(fanout_devoluciones.detalle_sku, sku, dias)


@router.get("/full/observacion")
def full_observacion(horas: int = Query(24, ge=1, le=168)):
    """
    Qué está viendo el vigilante de FULL/FBA: catálogo de tipos de operación con
    tráfico REAL y qué haría cada uno con el stock de Woo.

    Es la pantalla de análisis del modo solo-registro: antes de dejar que mueva
    inventario hay que confirmar aquí que no aparecen tipos desconocidos y que
    los conocidos se comportan como dice la tabla de decisión.
    """
    from services import db, stock_full
    if settings.supabase_read_fanout_log:
        from services import fanout_read
        filas = fanout_read.movimientos_full(horas)
    else:
        filas = db.fetch_all(
            """SELECT accion, resultado, sku, cuenta, stock_drop, objetivo, ts
               FROM fanout_log
               WHERE (accion LIKE 'full_%%' OR accion LIKE 'fba_%%')
                 AND ts >= UTC_TIMESTAMP() - INTERVAL %s HOUR
               ORDER BY id DESC""", (horas,))
    # El tipo de ML viene al inicio del resultado ("TRANSFER_DELIVERY x2: …").
    tipos: dict[str, dict] = {}
    for f in filas:
        tipo = str(f.get("resultado") or "").split(" ")[0].split(":")[0] or "?"
        t = tipos.setdefault(tipo, {"tipo": tipo, "n": 0, "efecto_declarado":
                                    stock_full.EFECTO_EN_WOO.get(tipo, "DESCONOCIDO"),
                                    "acciones": {}, "ejemplo": None})
        t["n"] += 1
        t["acciones"][f["accion"]] = t["acciones"].get(f["accion"], 0) + 1
        if t["ejemplo"] is None:
            t["ejemplo"] = {"sku": f["sku"], "cuenta": f["cuenta"],
                            "woo": f"{f['stock_drop']}→{f['objetivo']}",
                            "detalle": f["resultado"], "ts": str(f["ts"])}
    desconocidos = [t for t in tipos.values() if t["efecto_declarado"] == "DESCONOCIDO"]
    return {
        "modo_solo_registro": stock_full.solo_registro(),
        "vigilante_encendido": stock_full.habilitado(),
        "horas": horas,
        "eventos": len(filas),
        "tipos_vistos": sorted(tipos.values(), key=lambda x: -x["n"]),
        "TIPOS_DESCONOCIDOS": desconocidos,   # ← si esto no está vacío, NO encender
        "tabla_de_decision": stock_full.EFECTO_EN_WOO,
    }


@router.post("/encolar")
def encolar(sku: str = Query(..., description="SKU a encolar"),
            motivo: str = Query("manual", description="Origen del encolado")):
    """Encola el SKU en el fan-out real (respeta FANOUT_ENABLED y DRY_RUN)."""
    fanout_stock.encolar(sku, motivo)
    return {"ok": True, "sku": sku,
            "habilitado": fanout_stock.habilitado(),
            "dry_run": fanout_stock.dry_run()}


@router.get("/inventario/estado")
def inventario_estado():
    """Vigilante de inventario (Odoo →delta→ Woo →cambio→ canales). Con los rótulos del
    contrato con el panel (`fanout_vivo._rotulos`): `odoo_rotulo` FIJO «Odoo (congelado
    al 15-oct)» —el de stock_watch decía «Odoo» a secas con FUENTE=odoo— y
    `stock_rotulo`. Sólo memoria y configuración."""
    from services import fanout_vivo, stock_watch
    return {**stock_watch.estado(), **fanout_vivo._rotulos()}


@router.post("/inventario/revisar")
async def inventario_revisar(
    forzar: bool = Query(False, description="Deja pasar el tope y el piso de lectura. "
                                            "NO lo enciende: apagado contesta 409.")
):
    """
    Dispara una pasada del vigilante de inventario a mano.

    **Apagado (`STOCK_WATCH_ENABLED=false`) contesta 409 y no corre, venga o no
    `forzar`.** Antes `forzar=true` también lo corría estando apagado, con la idea
    de que «una pasada en solo-registro es inocua»; pero `forzar` nunca miró
    `STOCK_WATCH_SOLO_REGISTRO`: con el vigilante apagado y el registro en
    `false`, un clic encolaba al fan-out justo lo que el apagado debía detener.
    Apagarlo es una decisión (la de T0, la de un incidente) y un botón no la
    deshace: se enciende con la variable. Retirado el 20-oct-2026 (1.9-a del plan
    de salida de Odoo/Woo); `stock_watch.revisar` tiene su propio candado.

    `forzar=true` sólo sirve, ya encendido, para pasar el cortacircuitos
    (`STOCK_WATCH_TOPE`) y el piso de lectura cuando el volumen de cambios es REAL
    y ya se revisó: la excepción del acta de T0 (la primera pasada del libro mide
    contra el último número de Woo y puede pasar del tope).
    """
    from fastapi import HTTPException

    from services import stock_watch
    if not stock_watch.habilitado():
        raise HTTPException(status_code=409, detail={
            "estado": "apagado", "forzar": forzar,
            "motivo": ("El vigilante de inventario está apagado (STOCK_WATCH_ENABLED=false) "
                       "y no se corre a mano, ni con forzar: forzar sólo deja pasar el tope "
                       "con el vigilante encendido. Para correrlo, enciéndelo con la variable."),
        })
    return await stock_watch.revisar(forzar=forzar)


@router.get("/inventario/pendientes")
def inventario_pendientes(limite: int = Query(50, ge=1, le=500)):
    """Lo que el vigilante de inventario propuso/aplicó, más reciente primero.

    Las dos ramas filtran con LA MISMA lista (`fanout_read._ACCIONES_INVENTARIO`, que ya
    trae `vendible_cambio`/`vendible_registro` y las `vendible_sin_numero` viejas), como
    parámetro y no como literal. La de MySQL sólo verá lo viejo: la pasada del libro anota
    únicamente en kubera (`stock_watch._anotar(mysql=False)`)."""
    from services import db, fanout_read
    if settings.supabase_read_fanout_log:
        filas = fanout_read.pendientes_inventario(limite)
    else:
        filas = db.fetch_all(
            """SELECT ts, sku, accion, motivo, resultado, dry_run FROM fanout_log
               WHERE accion IN %s
               ORDER BY id DESC LIMIT %s""", (tuple(fanout_read._ACCIONES_INVENTARIO), limite))
    return {"eventos": filas, "total": len(filas)}


@router.post("/alinear")
def alinear(canal: str = Query(..., description="tiktok | mercado_libre | temu"),
            confirmar: bool = Query(False, description="Sin true solo cuenta, no encola"),
            limite: int = Query(0, ge=0, le=5000, description="0 = sin tope")):
    """
    Alineación inicial de un canal: encola en el fan-out TODOS los SKUs con
    publicación viva en ese canal, para que la primera sincronización no
    espere a que cada SKU se venda o se mueva (el fan-out no tiene barrido
    propio: es 100% por evento).

    No escribe nada por sí mismo: ENCOLA, y cada SKU pasa por plan() con todas
    sus guardas (dry-run, FANOUT_CANALES, pausas, DESCONOCIDO≠0…). Con
    `confirmar=false` solo devuelve cuántos SKUs encolaría.

    Nació para la corrida inicial de TikTok tras revivir el token (18-ago):
    285 ACTIVATE, de los cuales solo ~24 divergían de Woo.
    """
    from services import supabase_db as sdb
    canal = (canal or "").strip().lower()
    filtros = {
        # TikTok: `status` es quien dice si está a la venta (ACTIVATE).
        "tiktok": "canal='tiktok' and status='ACTIVATE'",
        # ML: activas y pausadas DROP (las FULL las descarta el propio fan-out,
        # pero se filtran aquí para no encolar de más).
        "mercado_libre": ("canal='mercado_libre' and situacion in ('active','paused') "
                          "and coalesce(is_fulfillment,false)=false"),
        # Temu: DROP-only por decisión (18-ago); vivo = tiene goodsId. Los
        # estados crudos no distinguen activo/inactivo y aquí no bloquean —
        # la rama de temu en _destinos aplica la política fina.
        "temu": "canal='temu' and coalesce(listing_id,'') <> ''",
    }
    if canal not in filtros:
        return {"ok": False, "motivo": f"canal '{canal}' sin alineación definida",
                "canales": sorted(filtros)}
    filas = sdb.fetch_all(
        f"select distinct sku from channel.listings where {filtros[canal]}"
        + (f" limit {int(limite)}" if limite else ""))
    skus = [str(f["sku"]) for f in filas]
    if confirmar and skus:
        fanout_stock.encolar_varios(skus, motivo=f"alineacion inicial {canal}")
    return {"ok": True, "canal": canal, "skus": len(skus),
            "encolados": len(skus) if confirmar else 0,
            "confirmar": confirmar,
            "habilitado": fanout_stock.habilitado(),
            "dry_run": fanout_stock.dry_run(),
            "nota": ("encolados; ver /api/fanout/estado" if confirmar
                     else "conteo solamente — repetir con confirmar=true")}


# `GET /api/fanout/odoo/monitor` se retiró el 20-oct-2026: leía MySQL (`fanout_log` y
# `stock_watch_foto`, sin escritor desde la salida de Odoo del 15-oct) con la lista
# vieja de acciones, y ninguna pantalla la llamaba. La bitácora del vigilante vive en
# `/inventario/pendientes` y la cadena en vivo en `/vivo` y `/matriz`.


@router.get("/diagnostico/ip")
async def diagnostico_ip():
    """
    Con qué IP SALE este backend a internet, preguntado a tres servicios.

    Existe porque las listas blancas por IP (Temu, TikTok) son un punto único de
    falla y el egress de Railway YA se movió dos veces: primero de
    162.220.232.251 a 152.55.177.181, y el 27-ago los rechazos `5000003
    NOT_IN_IP_WHITE_LIST` de Temu volvieron a aparecer con las tres IPs
    estáticas dadas de alta. Adivinar cuál es la IP de salida cuesta horas;
    preguntarla cuesta una llamada.

    Se consultan TRES ecos: si contestan distinto, es que la salida rota entre
    varias IPs (Railway las declara `Shared`) y la lista blanca necesita todas.
    """
    import asyncio

    import httpx

    ECOS = {"ipify": "https://api.ipify.org?format=json",
            "aws": "https://checkip.amazonaws.com",
            "ifconfig": "https://ifconfig.me/ip"}

    async def eco(nombre: str, url: str) -> tuple[str, str]:
        try:
            async with httpx.AsyncClient(timeout=12.0) as cli:
                r = await cli.get(url)
                t = r.text.strip()
                if t.startswith("{"):
                    t = (r.json() or {}).get("ip", t)
                return nombre, t
        except Exception as exc:  # noqa: BLE001
            return nombre, f"error: {type(exc).__name__}"

    res = dict(await asyncio.gather(*(eco(n, u) for n, u in ECOS.items())))
    vistas = {v for v in res.values() if not v.startswith("error")}
    return {
        "ip_de_salida": res,
        "coinciden": len(vistas) == 1,
        "ips_distintas_vistas": sorted(vistas),
        "nota": ("Todas las IPs que aparezcan aquí deben estar en la lista "
                 "blanca de Temu y de TikTok. Railway las marca 'Shared': "
                 "puede rotar entre ellas."),
    }
