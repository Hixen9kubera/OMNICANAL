"""
ordenes_venta.py — Endpoints de INVENTARIO · Órdenes de venta (las PROPIAS del
panel, folio OV-00001…), sobre el esquema de las migraciones 0064/0065. Las
rutas son las de frontend/components/ordenes/api.ts y la forma exacta de cada
respuesta está en frontend/components/ordenes/tipos.ts; el contrato con la base,
en docs/MIGRACION_0064_0065_GUIA_AGENTE.md.

  GET    /api/ordenes-venta/estado                   quién soy, banderas (sólo lectura), bodegas, si hay bucket
  GET    /api/ordenes-venta                          lista paginada + conteos de toda la tabla
  POST   /api/ordenes-venta                          alta en BORRADOR (idempotente por `clave`)
  GET    /api/ordenes-venta/skus                     buscador de productos, con su saldo por bodega de kubera
  GET    /api/ordenes-venta/marketplace/venta        una venta de marketplace, para prellenar con su precio
  GET    /api/ordenes-venta/marketplace/pendientes   ventas DROP recientes sin orden propia
  POST   /api/ordenes-venta/conciliar                revisa AHORA si el canal canceló alguna venta con orden
  GET    /api/ordenes-venta/{ref}                    el detalle; `ref` = id o folio
  PUT    /api/ordenes-venta/{id}                     GUARDA UN BORRADOR (lo que no se manda no se toca)
  POST   /api/ordenes-venta/{id}/confirmar           borrador → confirmada: APARTA todo o nada
  POST   /api/ordenes-venta/{id}/entregar            DELIVERED; con `lineas`, lo que salió de cada renglón
  POST   /api/ordenes-venta/{id}/cancelar            cancela (suelta el apartado)
  POST   /api/ordenes-venta/{id}/salio               Bodega contesta el «¿salió?» de una cancelada en camino
  POST   /api/ordenes-venta/{id}/salio-tarde         admin: una cancelada cuyo paquete sí había salido
  DELETE /api/ordenes-venta/{id}                     admin: borra (queda quién y por qué); {rev, motivo} en el cuerpo
  GET    /api/ordenes-venta/{id}/mensajes            el chat (long-poll con `esperar`)
  POST   /api/ordenes-venta/{id}/mensajes            manda un mensaje
  POST   /api/ordenes-venta/{id}/archivos            adjunta un PDF (multipart: `pdf` y `tipo`)
  GET    /api/ordenes-venta/{id}/archivos/{aid}      baja el PDF (operador o admin)
  DELETE /api/ordenes-venta/{id}/archivos/{aid}      admin: quita el PDF

LO QUE YA NO EXISTE, y no es un olvido: `/reservar` (apartar es todo o nada: no
hay reserva a medias que reintentar), `/regresar` (fuera de borrador el
contenido es inmutable: una confirmada mal capturada se cancela o se borra),
`/devolucion` (las devoluciones se reciben por su propio flujo, que aún no está)
y `/auto` (las banderas las enciende un acta, no la pantalla).

POR QUÉ ESTE ARCHIVO ES TAN DELGADO. Aquí no hay ni una regla de negocio: qué se
puede y quién puede lo decide `services/ordenes_venta.py` (depende del ESTADO de
la orden, que el RBAC por prefijo no ve) y, debajo de él, la base. El router
hace cuatro cosas:

  1. Dice QUIÉN pide (`_quien`): la identidad que dejó el middleware se vuelve
     el `Quien` que el servicio escribe en cada movimiento.
  2. Manda TODO a un hilo (regla 11). El servicio es psycopg2 y `requests` a
     Storage: llamado directo desde una corrutina detiene el backend entero.
     Hasta el nombre visible se resuelve dentro del hilo, porque lee la base
     cuando vence su caché.
  3. Traduce los errores: cada `ErrorOV` ya trae su código HTTP y su mensaje en
     español; lo inesperado es un 502 genérico (el detalle va al log, no afuera).
  4. Avisa al bus (`ov_bus`) después de cada escritura, para que el chat de los
     demás despierte sin esperar a que venza su petición.

Las rutas fijas (/estado, /skus, /marketplace/…, /conciliar) se declaran ANTES
de `/{ref}`: FastAPI casa en orden y `/{ref}` se las comería. Las de escritura
llevan `{orden_id:int}` para que un folio no entre donde va un id.

Permisos (core/rbac.py): GET `lectura`, POST/PUT `operador`, DELETE `admin`. Es
sólo el piso por verbo; lo fino lo contesta el servicio con un 403 que dice por
qué. Dos casos donde el piso NO alcanza y por eso `quien` viaja al servicio:
bajar un PDF es un GET pero pide `operador`, y «salió tarde» es un POST pero
pide `admin`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Any, Callable
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr
from starlette.datastructures import UploadFile

from config import settings
from services import ordenes_venta as ov
from services import ov_auto, ov_bus

router = APIRouter(prefix="/api/ordenes-venta", tags=["ordenes-venta"])
log = logging.getLogger("omnicanal.routers.ordenes_venta")

_MAX_PDF = ov.MAX_PDF            # 15 MB: el tope que tendrá el bucket
# Lo que el multipart le añade al archivo (separadores, cabeceras de cada parte
# y el campo `tipo`): la holgura para comparar el Content-Length del cuerpo
# contra el tope del PDF.
_MARGEN_MULTIPART = 64 * 1024
_MAX_ESPERA_S = 25               # lo más que se sostiene una petición del chat
# El long-poll no duerme los 25 s de un tirón: cada tramo comprueba si el
# navegador sigue ahí, para no sostener una espera que ya nadie mira.
_TRAMO_ESPERA_S = 3.0
_MSG_502 = "No se pudo completar la operación; intenta de nuevo."


# ── Los cuerpos ───────────────────────────────────────────────────────────────
# Sólo se acota lo que protege al servidor (tamaños). La validación de verdad
# —con su mensaje en español y su 400— vive en el servicio: aquí un tipo mal
# puesto saldría como un 422 genérico, sin decir qué renglón ni por qué.

# Un número TAL COMO LLEGUE (12, 12.5 o "12.50"): el servicio lo valida y dice qué
# renglón está mal. Tipado como `int`, un «2.5 piezas» sería un 422 sin explicación.
# Los cuatro van en modo ESTRICTO para que pydantic no convierta nada por su
# cuenta: con `int | float | str` a secas un `true` llegaba al servicio hecho un
# 1 —una pieza, un peso— y la regla del servicio («un booleano no es un número»)
# nunca lo veía. Así viaja como lo que es y se rechaza con palabras.
_Numero = StrictBool | StrictInt | StrictFloat | StrictStr

# La `rev` es el CANDADO optimista, y va igual de estricta: con `int` a secas
# pydantic convertía `true`, "1" y 1.0 en la rev 1, y un `{"rev": true}`
# confirmaba, cancelaba o entregaba cualquier documento que estuviera en su
# primera versión. El `_rev()` del servicio rechaza los booleanos, pero nunca
# llegaba a verlos.
_RevCuerpo = StrictInt


class _Linea(BaseModel):
    """LineaEntrada de tipos.ts. La BODEGA va por renglón (código de una de
    kubera que admita órdenes); en un borrador puede faltar."""
    sku: str = Field(max_length=200)
    cantidad: _Numero
    precio_unitario: _Numero | None = None
    titulo: str | None = Field(None, max_length=1000)
    imagen: str | None = Field(None, max_length=2000)
    almacen: str | None = Field(None, max_length=40)


class _Datos(BaseModel):
    """DatosOrden de tipos.ts. Todo opcional: lo que no se manda no se toca.
    Ya no hay «almacén» de encabezado: la bodega es de cada renglón."""
    cliente: str | None = None
    canal: str | None = None
    mp_canal: str | None = None
    mp_cuenta: str | None = None
    mp_orden: str | None = None
    descripcion: str | None = None
    guia: str | None = None
    paqueteria: str | None = None
    fecha_venta: str | None = None
    entrega_limite: str | None = None
    moneda: str | None = None
    total: _Numero | None = None          # null = que sea la suma de los renglones
    comision: _Numero | None = None
    precio_origen: str | None = None
    lineas: list[_Linea] | None = Field(None, max_length=ov.MAX_RENGLONES)


class _Alta(_Datos):
    # La genera el navegador UNA vez por formulario: el doble clic no crea dos órdenes.
    clave: str | None = Field(None, max_length=ov.MAX_CLAVE)
    # La pantalla no lo manda (aquí sólo nacen ventas). Viaja para que el
    # servicio conteste con palabras a quien pida un `full` por la API, en vez
    # de crearle una venta callando lo que pidió.
    tipo: str | None = Field(None, max_length=20)


class _Guardado(_Datos):
    rev: _RevCuerpo = Field(ge=1)


class _Rev(BaseModel):
    rev: _RevCuerpo = Field(ge=1)


class _RevMotivo(BaseModel):
    rev: _RevCuerpo = Field(ge=1)
    motivo: str = Field("", max_length=ov.MAX_MOTIVO)


class _Salida(BaseModel):
    """EntregaLinea de tipos.ts: qué renglón y cuántas piezas salieron de él."""
    id: _Numero
    n: _Numero


class _Entrega(BaseModel):
    rev: _RevCuerpo = Field(ge=1)
    # Sin `lineas` salen todos los renglones pendientes completos.
    lineas: list[_Salida] | None = Field(None, max_length=ov.MAX_RENGLONES)


class _Salio(BaseModel):
    rev: _RevCuerpo = Field(ge=1)
    # Obligatorio, y SIN convertir: «sí», 1 o "true" no son una respuesta. El
    # servicio exige un booleano de verdad —la respuesta mueve stock y no se
    # deshace— y lo dice en español.
    salio: Any


class _Mensaje(BaseModel):
    cuerpo: str = Field(min_length=1, max_length=ov.MAX_MENSAJE)
    # La genera el navegador, una por mensaje escrito: si la respuesta se pierde
    # y la persona vuelve a mandar, no queda dos veces.
    clave: str | None = Field(None, max_length=ov.MAX_CLAVE)


# ── Quién pide ────────────────────────────────────────────────────────────────

def _quien(request: Request) -> ov.Quien:
    """La identidad del middleware → el `Quien` del servicio (guía §4.4: una
    persona es `panel`, la llave de máquina es `api`, y nunca un actor vacío).

    ⚠️ BLOQUEA: `nombre_de` lee `core.usuarios` cuando vence su caché. Se llama
    SIEMPRE dentro del hilo (`_con_quien`), nunca desde la corrutina."""
    ident = getattr(request.state, "identidad", None)
    tipo = getattr(ident, "tipo", "anonimo")
    if tipo == "persona":
        correo = str(getattr(ident, "actor", "") or "")
        nombre = ov.nombre_de(correo) or correo.split("@")[0] or correo
        return ov.Quien(correo, nombre, "panel", str(getattr(ident, "rol", "") or ""))
    if tipo == "maquina":
        # Misma llave para los crons y para Claude: la cabecera dice cuál de los
        # dos. `X-Origen: claude` es la convención, pero ningún cliente la manda
        # si nadie se la enseñó; el User-Agent de las herramientas de Claude sí
        # lleva su nombre solo. Vale cualquiera de las dos, y SÓLO para la llave
        # de máquina: una persona con sesión no se vuelve «Claude» por un UA.
        claude = ((request.headers.get("x-origen") or "").strip().lower() == "claude"
                  or "claude" in (request.headers.get("user-agent") or "").lower())
        return ov.Quien("servicio", "Claude" if claude else "API",
                        "claude" if claude else "api", "admin")
    # Sin credencial. Con AUTH_ENFORCED apagado (local, sandbox) el sistema ya
    # está abierto y se trabaja como admin; encendido, aquí no entra nadie sin nombre.
    if settings.auth_enforced:
        raise HTTPException(401, "Falta la credencial. Inicia sesión o manda X-API-Key.")
    return ov.Quien("anonimo", "Sin sesión", "panel", "admin")


def _con_quien(request: Request, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Corre EN EL HILO: resuelve quién pide y llama a `fn(..., quien=…)`."""
    return fn(*args, quien=_quien(request), **kwargs)


async def _hilo(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """`fn` en un hilo (regla 11), con los errores ya traducidos a HTTP."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except HTTPException:
        raise
    except ov.ErrorOV as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — el detalle va al log, no a quien pide
        # Con `_con_quien` de por medio, lo que interesa es a QUIÉN llamaba.
        real = args[1] if fn is _con_quien and len(args) > 1 else fn
        raise _inesperado(exc, str(getattr(real, "__name__", real))) from exc


def _inesperado(exc: Exception, donde: str) -> HTTPException:
    """Lo que no es un `ErrorOV`: un 502 genérico. Se llama DENTRO del `except`
    (el traceback sale de ahí). Si lo que pasó es que kubera no contesta, no hay
    traceback: una línea, una vez por minuto (`ov.anotar_caida`) — con la base
    caída la pantalla sondea sola y cada sondeo dejaba una traza entera."""
    if ov.es_caida(exc):
        ov.anotar_caida(exc, donde)
        return HTTPException(502, str(ov.SinBase()))
    log.exception("órdenes de venta: falló %s", donde)
    return HTTPException(502, _MSG_502)


def _avisar(resp: dict[str, Any]) -> dict[str, Any]:
    """Después de una escritura: despierta al chat de esa orden. Corre en el loop."""
    orden = resp.get("orden") if isinstance(resp, dict) else None
    if isinstance(orden, dict) and orden.get("id") is not None:
        ov_bus.avisar(orden["id"])
    return resp


# ── El módulo ─────────────────────────────────────────────────────────────────

def _conciliar(quien: ov.Quien) -> dict[str, Any]:
    """El barrido a mano: el mismo `ov_auto.revisar` del job. Las compuertas
    (la bandera, las tablas) viven en `revisar`, no aquí, para que el job y el
    botón hagan exactamente lo mismo; aquí sólo se exige que quien lo pide
    pueda mover órdenes (el piso del RBAC ya dejó fuera a `lectura`)."""
    if not quien.escribe:
        raise ov.SinPermiso("Tu rol no permite revisar las cancelaciones del canal.")
    return ov_auto.revisar()


@router.get("/estado")
async def estado(request: Request) -> dict[str, Any]:
    """EstadoModulo: quién soy para este módulo, las banderas `ordenes_venta` y
    `ov_generacion_auto` (de SÓLO LECTURA: las enciende un acta), el catálogo
    de bodegas y si ya se pueden adjuntar PDF (si existe el bucket). No truena
    sin las migraciones ni con kubera caída: lo dice en `motivo`."""
    return await _hilo(_con_quien, request, ov.estado_modulo)


@router.get("")
async def listar(estado: str | None = Query(None, max_length=40),
                 q: str | None = Query(None, max_length=200),
                 canal: str | None = Query(None, max_length=40),
                 pagina: int = Query(1, ge=1, le=1_000_000),
                 por_pagina: int = Query(40, ge=1, le=200)) -> dict[str, Any]:
    """La lista (más recientes primero) y los conteos de TODA la tabla por filtro.
    Sin las migraciones 0064/0065 no truena: contesta `falta_migracion`."""
    return await _hilo(ov.listar, estado, q, canal, pagina, por_pagina)


@router.post("")
async def crear(body: _Alta, request: Request) -> dict[str, Any]:
    """Alta en BORRADOR. No depende de la bandera: un borrador no aparta nada."""
    datos = body.model_dump(exclude_unset=True)
    clave = datos.pop("clave", None)
    return _avisar(await _hilo(_con_quien, request, ov.crear_borrador, datos, clave=clave))


@router.get("/skus")
async def skus(q: str = Query("", max_length=200)) -> dict[str, Any]:
    """El buscador de productos del documento: el catálogo y el saldo de cada
    SKU en las bodegas de kubera (físico, apartado y libre)."""
    return await _hilo(ov.buscar_skus, q)


@router.get("/marketplace/venta")
async def venta(orden: str = Query("", max_length=200),
                canal: str | None = Query(None, max_length=40)) -> dict[str, Any]:
    """Una venta por su id en el marketplace, con sus renglones y su precio."""
    return await _hilo(ov.venta_marketplace, orden, canal)


@router.get("/marketplace/pendientes")
async def pendientes(dias: int = Query(7, ge=1, le=90),
                     canal: str | None = Query(None, max_length=40)) -> dict[str, Any]:
    """Ventas DROP recientes que todavía no tienen orden propia."""
    return await _hilo(ov.ventas_pendientes, dias, canal)


@router.post("/conciliar")
async def conciliar(request: Request) -> dict[str, Any]:
    """Revisa AHORA si el canal canceló alguna venta que tiene orden propia (el
    mismo barrido del job). Contesta RespConciliar: las `canceladas` y las
    `marcadas` (canceladas con el paquete en camino: esperan el «¿salió?»).
    Con la bandera apagada no hace nada y lo dice (`ok: false` y su motivo)."""
    r = await _hilo(_con_quien, request, _conciliar)
    for o in [*(r.get("canceladas") or []), *(r.get("marcadas") or [])]:
        ov_bus.avisar(o["id"])
    return r


# ── Una orden ─────────────────────────────────────────────────────────────────

@router.get("/{ref}")
async def detalle(ref: str, request: Request) -> dict[str, Any]:
    """El detalle con sus renglones, sus PDF y lo que PUEDE hacer quien pregunta.
    `ref` es el id o el folio («OV-00012», sin distinguir mayúsculas)."""
    return await _hilo(_con_quien, request, ov.obtener, ref)


@router.put("/{orden_id:int}")
async def guardar(orden_id: int, body: _Guardado, request: Request) -> dict[str, Any]:
    """Guarda un BORRADOR (encabezado y renglones). SÓLO viaja al servicio lo
    que el cliente mandó: lo demás no se toca. Fuera de borrador la orden ya no
    cambia, y el servicio lo contesta con un 400 que dice qué hacer."""
    datos = body.model_dump(exclude_unset=True)
    rev = datos.pop("rev")
    return _avisar(await _hilo(_con_quien, request, ov.guardar, orden_id, rev, datos))


@router.post("/{orden_id:int}/confirmar")
async def confirmar(orden_id: int, body: _Rev, request: Request) -> dict[str, Any]:
    """Borrador → confirmada, APARTANDO cada renglón en su bodega. Todo o nada:
    si un renglón no alcanza es un 409 que dice cuál, y no se aparta ninguno.

    El 409 TAMBIÉN avisa al bus. Cuando no alcanza, el servicio deja el renglón
    `no_alcanzo` en el chat (en otra transacción) y después lanza: la `rev` no
    cambia y `_avisar` no corre, así que ese mensaje no aparecía hasta que
    vencía el long-poll (hasta 25 s después del error rojo). Avisar de más sólo
    provoca una relectura del chat."""
    try:
        return _avisar(await _hilo(_con_quien, request, ov.confirmar, orden_id, body.rev))
    except HTTPException as exc:
        if exc.status_code == 409:
            ov_bus.avisar(orden_id)
        raise


@router.post("/{orden_id:int}/entregar")
async def entregar(orden_id: int, body: _Entrega, request: Request) -> dict[str, Any]:
    """DELIVERED: almacén la entregó a la paquetería. Sin `lineas` salen todos
    los renglones pendientes completos; con ellas, las piezas que salieron de
    cada uno (entrega parcial: lo que no salió se suelta)."""
    lineas = None if body.lineas is None else [l.model_dump() for l in body.lineas]
    return _avisar(await _hilo(_con_quien, request, ov.entregar, orden_id, body.rev,
                               lineas=lineas))


@router.post("/{orden_id:int}/cancelar")
async def cancelar(orden_id: int, body: _RevMotivo, request: Request) -> dict[str, Any]:
    """Cancela. De aquí siempre sale como cancelación MANUAL: la del marketplace
    sólo la escribe el barrido (el `origen` no se acepta del cuerpo)."""
    return _avisar(await _hilo(_con_quien, request, ov.cancelar, orden_id, body.rev,
                               motivo=body.motivo))


@router.post("/{orden_id:int}/salio")
async def salio(orden_id: int, body: _Salio, request: Request) -> dict[str, Any]:
    """La respuesta de Bodega cuando el canal canceló con el paquete en camino:
    `salio = true` → DELIVERED but CANCELLED (se espera la devolución);
    `salio = false` → cancelada, y el apartado se suelta."""
    return _avisar(await _hilo(_con_quien, request, ov.responder_salio, orden_id, body.rev,
                               salio=body.salio))


@router.post("/{orden_id:int}/salio-tarde")
async def salio_tarde(orden_id: int, body: _Rev, request: Request) -> dict[str, Any]:
    """Admin: una orden ya CANCELADA cuyo paquete sí había salido. El piso del
    RBAC para este POST es `operador`; que sea de admin lo exige el servicio."""
    return _avisar(await _hilo(_con_quien, request, ov.salio_tarde, orden_id, body.rev))


@router.delete("/{orden_id:int}")
async def borrar(orden_id: int, request: Request, body: _RevMotivo | None = None,
                 rev: int | None = Query(None, ge=1),
                 motivo: str = Query("", max_length=ov.MAX_MOTIVO)) -> dict[str, Any]:
    """Admin: borra la orden. No se elimina: queda quién, cuándo y por qué
    (motivo de 10 caracteres o más; el mínimo lo dice el servicio).

    `rev` y `motivo` van en el CUERPO (`{rev, motivo}`), como en cancelar. El
    motivo es texto libre —«la pidió duplicada fulano, tel…»— y en la dirección
    quedaba copiado en el log de accesos de uvicorn, fuera de la base y con otra
    retención y otro público que la orden. Los parámetros de la dirección se
    conservan SÓLO para quien todavía no manda cuerpo (compatibilidad): si viene
    cuerpo, manda el cuerpo y la dirección ni se mira."""
    if body is not None:
        rev, motivo = body.rev, body.motivo
    elif rev is None:
        raise HTTPException(422, "Falta la rev: va en el cuerpo, como {rev, motivo}.")
    return _avisar(await _hilo(_con_quien, request, ov.borrar, orden_id, rev, motivo=motivo))


# ── Chat ──────────────────────────────────────────────────────────────────────

def _actor(request: Request) -> str:
    """De quién es la espera, para el tope por identidad del bus."""
    ident = getattr(request.state, "identidad", None)
    return str(getattr(ident, "actor", "") or "anonimo")


async def _oir_cierre(request: Request) -> bool:
    """Termina (True) cuando el cliente cierra la conexión.

    Por `request.receive()` y no por `request.is_disconnected()`: detrás de un
    `app.middleware("http")` —el de identidad de main.py lo es— `is_disconnected`
    contesta SIEMPRE False (su CancelScope ya cancelado revienta dentro del
    task group del middleware y el mensaje nunca llega; medido con Starlette
    0.41.3). `receive()` sí entrega el `http.disconnect`."""
    while True:
        if (await request.receive()).get("type") == "http.disconnect":
            return True


def _cerro(cierre: "asyncio.Future[bool]") -> bool:
    return (cierre.done() and not cierre.cancelled() and cierre.exception() is None
            and cierre.result() is True)


# Los oyentes de cierre que siguen vivos tras contestar (ver el `finally` de
# `_esperar_o_irse`). La referencia fuerte evita que el recolector se lleve una
# tarea pendiente; cada una se saca sola al terminar.
_OYENTES: set["asyncio.Task[bool]"] = set()


def _soltar_oyente(tarea: "asyncio.Task[bool]") -> None:
    _OYENTES.discard(tarea)
    if not tarea.cancelled():
        tarea.exception()        # recogida: sin «Task exception was never retrieved»


async def _esperar_o_irse(request: Request, orden_id: int, vista: int, segundos: float,
                          actor: str) -> bool:
    """Sostiene la espera del long-poll. True = hay que releer (alguien escribió,
    o venció); False = el navegador se fue y ya nadie escucha.

    POR QUÉ NO ES UN SIMPLE `await ov_bus.esperar(...)`. Ni uvicorn ni Starlette
    cancelan el handler cuando el cliente cierra: cada pestaña que se ocultaba o
    cada documento que se cerraba dejaba su espera viva hasta 25 s (un lugar del
    tope del bus) y, al vencer, una lectura a la base para nadie. Aquí la espera
    corre en carrera contra la desconexión (`_oir_cierre`) y, además, despierta
    en tramos cortos para comprobarlo (`is_disconnected`, que sí sirve cuando no
    hay un middleware de por medio). Al salir se CANCELA la espera: el `finally`
    de `ov_bus.esperar` suelta su lugar."""
    espera = asyncio.ensure_future(ov_bus.esperar(orden_id, vista, segundos, actor))
    cierre = asyncio.ensure_future(_oir_cierre(request))
    vigilar = {espera, cierre}
    try:
        while True:
            await asyncio.wait(vigilar, timeout=_TRAMO_ESPERA_S,
                               return_when=asyncio.FIRST_COMPLETED)
            if espera.done():
                espera.result()                 # si la espera tronó, que se vea
                return True
            if cierre.done():
                if _cerro(cierre):
                    return False
                vigilar = {espera}              # el oyente falló: se sigue sin él
            try:
                if await request.is_disconnected():
                    return False
            except Exception:  # noqa: BLE001 — sin saber, se sigue esperando
                pass
    finally:
        espera.cancel()
        cierre.cancel()
        # El oyente NO se espera. Si la cancelación le llega justo cuando el
        # middleware sale de su task group (anyio), éste se la traga y el oyente
        # sigue en `receive()` aguardando un mensaje que sólo llega cuando ESTA
        # respuesta termina: esperarlo aquí era colgarse (medido en las pruebas).
        # Sin esperarlo termina solo en cuanto la respuesta sale: `receive()`
        # contesta `http.disconnect` (uvicorn y el middleware lo hacen así).
        _OYENTES.add(cierre)
        cierre.add_done_callback(_soltar_oyente)
        await asyncio.gather(espera, return_exceptions=True)


@router.get("/{orden_id:int}/mensajes")
async def mensajes(orden_id: int, request: Request, desde_id: int = Query(0, ge=0),
                   esperar: float = Query(0, ge=0, le=_MAX_ESPERA_S)) -> dict[str, Any]:
    """El chat y la bitácora de la orden, a partir de `desde_id`.

    Con `esperar` es un LONG-POLL: si no hay nada nuevo, la petición se sostiene
    hasta que alguien escriba sobre la orden (o venza). Mientras espera no ocupa
    hilo ni conexión de base: sólo esta corrutina dormida en `ov_bus`. Cada
    identidad sostiene a lo más `ov_bus.TOPE_POR_ACTOR` esperas a la vez; pasada
    de ahí —o lleno el proceso— contesta de inmediato y ese chat sondea."""
    actor = _actor(request)
    # La versión se toma ANTES de leer: si alguien escribe entre la lectura y la
    # espera, la versión ya avanzó y `esperar` regresa de inmediato.
    vista = ov_bus.version(orden_id)
    r = await _hilo(ov.mensajes, orden_id, desde_id)      # una orden que no existe: 404 aquí
    if r["mensajes"] or esperar <= 0 or ov_bus.lleno(actor):
        return r                                          # lleno: degrada a sondeo
    if not await _esperar_o_irse(request, orden_id, vista, esperar, actor):
        return r                                          # se fue: ni se relee
    # Se relee aunque haya vencido: la base es la verdad, y otro contenedor
    # (durante el relevo de un deploy) pudo escribir sin avisar a éste.
    return await _hilo(ov.mensajes, orden_id, desde_id)


@router.post("/{orden_id:int}/mensajes")
async def enviar_mensaje(orden_id: int, body: _Mensaje, request: Request) -> dict[str, Any]:
    """Un mensaje de una persona. Contesta SÓLO el mensaje nuevo. Con `clave`
    (opcional) es idempotente: reenviar el mismo mensaje no lo duplica."""
    r = await _hilo(_con_quien, request, ov.enviar_mensaje, orden_id, cuerpo=body.cuerpo,
                    clave=body.clave)
    ov_bus.avisar(orden_id)
    return r


# ── PDF ───────────────────────────────────────────────────────────────────────

@router.post("/{orden_id:int}/archivos")
async def subir_archivo(orden_id: int, request: Request) -> dict[str, Any]:
    """Adjunta un PDF: multipart con el archivo en `pdf` y qué es en `tipo`
    (comprobante | factura | envio_full; obligatorio, lo valida el servicio).
    15 MB y sólo PDF; el mismo dos veces no duplica. Las guías con la dirección
    del comprador NO se guardan aquí. Mientras no exista el bucket
    `ordenes-venta` el servicio contesta un 409 que lo dice.

    EL TOPE SE MIRA ANTES DE LEER EL CUERPO. Con `pdf: UploadFile = File(...)` en
    la firma, FastAPI recibía y volcaba a disco el multipart ENTERO antes de
    entrar aquí: un archivo de 120 MB se escribía completo al disco efímero del
    contenedor —el mismo del backend que ingiere ventas— y sólo entonces salía
    el 413. Por eso el formulario se lee a mano, después de mirar el
    Content-Length.

    Y EL TOPE NO DEPENDE DE LA BUENA FE DEL CLIENTE. Antes, sin Content-Length
    (cuerpo por trozos) no había con qué adelantarse y el cuerpo ENTERO llegaba
    al disco antes del 413 —medido: 120 MB enviados, 120 MB escritos—; y con
    otro tipo de cuerpo (urlencoded) `request.form()` lo juntaba en MEMORIA.
    Por eso se exigen las dos cosas antes de abrir el formulario: que sea
    multipart (415) y que diga cuánto pesa (411). El navegador y `fetch` con
    FormData mandan las dos siempre; el servidor corta el cuerpo en lo que la
    cabecera declaró, así que lo declarado es un tope de verdad."""
    if not (request.headers.get("content-type") or "").strip().lower().startswith(
            "multipart/form-data"):
        raise HTTPException(415, "El PDF va como multipart/form-data, en el campo «pdf».")
    declarado = (request.headers.get("content-length") or "").strip()
    if not declarado.isdigit():
        raise HTTPException(411, "Falta el Content-Length: el PDF se manda completo, "
                                 "no por trozos.")
    if int(declarado) > _MAX_PDF + _MARGEN_MULTIPART:
        raise HTTPException(413, f"El PDF pesa más de {_MAX_PDF // 1048576} MB.")
    # `async with`: al salir se cierran (y se borran) los temporales del multipart.
    # Un archivo y, a lo más, dos campos de texto (hoy sólo viaja `tipo`).
    async with request.form(max_files=1, max_fields=2) as formulario:
        pdf = formulario.get("pdf")
        if not isinstance(pdf, UploadFile):
            raise HTTPException(422, "Falta el archivo: va como multipart, en el campo «pdf».")
        nombre = pdf.filename or ""
        # Si falta (o no es texto) viaja vacío: el servicio dice cuáles son los tipos.
        tipo = formulario.get("tipo")
        tipo = tipo[:40] if isinstance(tipo, str) else ""
        # La medición REAL: un byte más del tope y ya se sabe que no cabe.
        datos = await pdf.read(_MAX_PDF + 1)
    if len(datos) > _MAX_PDF:
        raise HTTPException(413, f"El PDF pesa más de {_MAX_PDF // 1048576} MB.")
    if not datos:
        raise HTTPException(400, "El archivo está vacío.")
    if not datos.startswith(b"%PDF"):
        raise HTTPException(400, "El archivo no es un PDF.")
    return _avisar(await _hilo(_con_quien, request, ov.subir_archivo, orden_id, tipo=tipo,
                               nombre=nombre, datos=datos))


def _ascii(nombre: str) -> str:
    """El nombre para el `filename="…"` plano: sin acentos ni nada que rompa la
    cabecera. Un nombre sin una sola letra latina («発送.pdf») queda «documento.pdf»."""
    plano = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode("ascii")
    plano = re.sub(r'[^A-Za-z0-9 ._()\-]', "_", plano)
    if plano.lower().endswith(".pdf"):
        plano = plano[:-4]
    return (plano.strip(" .") or "documento") + ".pdf"


@router.get("/{orden_id:int}/archivos/{archivo_id:int}")
async def bajar_archivo(orden_id: int, archivo_id: int, request: Request) -> Response:
    """Baja el PDF por el backend, con sesión: jamás sale una URL pública ni
    firmada. Y no basta con tener sesión: el servicio exige `operador` o `admin`
    (de una orden borrada, admin), aunque el piso del RBAC para un GET sea
    `lectura`."""
    nombre, datos = await _hilo(_con_quien, request, ov.bajar_archivo, orden_id, archivo_id)
    return Response(content=datos, media_type="application/pdf", headers={
        # `filename*` lleva el nombre con sus acentos; `filename`, el respaldo en ASCII.
        "Content-Disposition": (f'attachment; filename="{_ascii(nombre)}"; '
                                f"filename*=UTF-8''{quote(nombre, safe='')}"),
        # Sin esto el navegador (otro origen) no deja leer Content-Disposition.
        "Access-Control-Expose-Headers": "Content-Disposition",
        "Cache-Control": "no-store"})


@router.delete("/{orden_id:int}/archivos/{archivo_id:int}")
async def borrar_archivo(orden_id: int, archivo_id: int, request: Request) -> dict[str, Any]:
    """Admin: quita un PDF (primero se marca en el índice, después se borra de Storage)."""
    return _avisar(await _hilo(_con_quien, request, ov.borrar_archivo, orden_id, archivo_id))
