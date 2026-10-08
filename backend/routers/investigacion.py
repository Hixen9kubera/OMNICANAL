"""
investigacion.py — Llamadas de INVESTIGACIÓN a marketplaces, desde producción.

  GET  /api/investigacion/temu/tipos       → la regla del candado + tipos sugeridos
  POST /api/investigacion/temu             → {type, params} → respuesta REDACTADA
  GET  /api/investigacion/tiktok/consultas → el catálogo CERRADO de lecturas de TikTok
  POST /api/investigacion/tiktok           → {consulta, params} → respuesta REDACTADA

(TikTok, 7-oct-2026: el candado es otro —un catálogo de consultas con nombre,
porque en TikTok el nombre de la ruta no dice si escribe—; ver la sección de
abajo y `services/investigacion_tiktok.py`. Lo de Temu sigue igual.)

POR QUÉ (Brandon, 24-sep-2026): "si Temu no te deja acceder, entra a omnicanal
para hacer las investigaciones en producción". La Open API de Temu sólo acepta
la IP de Railway; desde la laptop contesta `5000003 NOT_IN_IP_WHITE_LIST`. La
pregunta que lo detonó: ¿se puede comprar una guía por ALMACÉN (TEXCO / TEXCO
II) en vez de la guía combinada? Contestarla exige leer almacenes, paquetes sin
enviar y servicios de envío — desde aquí.

LO QUE LO HACE SEGURO, en el orden en que se evalúa:

  1. SÓLO ADMIN CON SESIÓN DEL PANEL. No depende de `AUTH_ENFORCED` ni de
     `RBAC_ENFORCED`: el middleware falla ABIERTO por diseño (una caída de la
     autenticación no puede tumbar el sitio), pero este endpoint es un proxy con
     las credenciales de la tienda y aquí se falla CERRADO. La llave de máquina
     (`X-API-Key`) tampoco pasa: esto lo usa una persona desde el navegador.
  2. EL CANDADO DE ESCRITURA (`services/investigacion_temu.py`): sólo lecturas
     (`…get` / `…query`), ningún verbo de escritura en ninguna parte, y los
     parámetros no pueden pisar el `type` del sobre. Lo que no calza: 400 y NO
     sale a la red.
  3. UN LÍMITE GLOBAL (30/min) y 3 a la vez: la cuota de Temu es de la app, la
     misma del sondeo de pedidos, el refresco de guías y el fan-out de stock.
  4. LA RESPUESTA SALE REDACTADA: nada del comprador ni del destinatario.

Nada del payload se escribe a disco ni a logs. En el log va sólo quién llamó,
el `type` y el código con que contestó Temu.

La firma y el sobre NO se reimplementan: se llama `services.temu.llamar`, el
mismo que usan los pedidos y las guías.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from config import settings
from core import identidad as core_identidad
from services import investigacion_temu as inv
from services import investigacion_tiktok as inv_tk
from services import temu
from services import tiktok as tk

log = logging.getLogger("omnicanal.investigacion")
router = APIRouter(prefix="/api/investigacion", tags=["investigacion"])

TIMEOUT_TEMU_S = 20.0
_limitador = inv.Limitador(maximo=30, ventana_s=60.0)
_simultaneas = asyncio.Semaphore(3)


async def solo_admin(request: Request):
    """
    Persona con sesión válida y rol admin. Si no, 401/403 — SIEMPRE.

    Se lee la identidad que dejó el middleware; si no la dejó (falló abierto,
    o la ruta se montó sin él), se resuelve aquí mismo. Nunca se asume.
    """
    quien = getattr(request.state, "identidad", None)
    if quien is None:
        quien = await core_identidad.resolver(request)
    if not getattr(quien, "autenticado", False):
        log.warning("INVESTIGACIÓN 401: %s %s sin sesión", request.method,
                    request.url.path)
        raise HTTPException(status_code=401,
                            detail="Inicia sesión en el panel para usar las investigaciones.")
    if getattr(quien, "tipo", "") != "persona" or getattr(quien, "rol", "") != "admin":
        log.warning("INVESTIGACIÓN 403: %s (%s, %s) intentó %s", quien.actor,
                    getattr(quien, "tipo", ""), getattr(quien, "rol", ""),
                    request.url.path)
        raise HTTPException(status_code=403,
                            detail="Las investigaciones son sólo para administradores "
                                   "con sesión en el panel.")
    return quien


class ConsultaTemu(BaseModel):
    # `Any` a propósito: la validación la hace el candado, que contesta 400 con
    # una razón legible (un 422 de pydantic no le dice nada a quien investiga).
    type: Any = None
    params: Any = None


@router.get("/temu/tipos")
async def temu_tipos(quien=Depends(solo_admin)) -> dict[str, Any]:
    """La regla exacta del candado y una lista de lecturas conocidas."""
    return {
        "temu_configurado": temu.disponible(),
        "regla": inv.regla(),
        "limite": {"llamadas": _limitador.maximo, "por_segundos": int(_limitador.ventana_s),
                   "alcance": "global (la cuota de Temu es de la app, compartida con producción)"},
        "timeout_s": TIMEOUT_TEMU_S,
        "codigos": inv.CODIGOS,
        "sugeridos": inv.TIPOS_SUGERIDOS,
    }


@router.post("/temu")
async def temu_consultar(consulta: ConsultaTemu, quien=Depends(solo_admin)) -> dict[str, Any]:
    """
    Una LECTURA a la Open API de Temu, con la respuesta redactada.

    Un error de Temu NO es un error de este endpoint: contesta 200 con
    `ok: false`, el `codigo` y su lectura — en una investigación el código ES
    el hallazgo (3000003 = no existe; 3000032 = existe y falta permiso…).
    """
    actor = getattr(quien, "actor", "?")

    # 1. El candado. Antes que nada que toque la red o gaste cuota.
    try:
        tipo = inv.validar_tipo(consulta.type)
        params = inv.validar_params(consulta.params)
    except inv.Rechazo as exc:
        log.warning("INVESTIGACIÓN temu RECHAZADA: %s pidió %r — %s", actor,
                    str(consulta.type)[:120], exc)
        raise HTTPException(status_code=400, detail=str(exc)) from None

    if not temu.disponible():
        raise HTTPException(status_code=503,
                            detail="Temu no está configurado en este ambiente (faltan TEMU_*).")

    # 2. El límite: sólo cuentan las llamadas que sí salen.
    if not _limitador.permite():
        espera = _limitador.espera_s()
        raise HTTPException(status_code=429,
                            detail=f"Límite de {_limitador.maximo} consultas por minuto "
                                   f"(la cuota de Temu es compartida con producción). "
                                   f"Intenta en {espera} s.",
                            headers={"Retry-After": str(espera)})

    # 3. La llamada: async de verdad (httpx), con techo doble de tiempo.
    t0 = time.monotonic()
    ok, codigo, resultado, error = False, None, None, None
    async with _simultaneas:
        try:
            resultado = await asyncio.wait_for(
                temu.llamar(tipo, params, timeout=TIMEOUT_TEMU_S),
                timeout=TIMEOUT_TEMU_S + 5)
            ok, codigo = True, "ok"
        except asyncio.TimeoutError:
            codigo, error = "timeout", f"Temu no contestó en {int(TIMEOUT_TEMU_S)} s."
        except Exception as exc:  # noqa: BLE001 — el error de Temu es el dato
            texto = str(exc)
            m = re.search(r"errorCode=(\w+)", texto)
            codigo = m.group(1) if m else type(exc).__name__
            error = inv.redactar_texto(texto)
    ms = int((time.monotonic() - t0) * 1000)

    # Sólo quién, qué tipo y cómo contestó. NADA del payload.
    log.info("INVESTIGACIÓN temu: %s → %s → %s (%d ms)", actor, tipo, codigo, ms)

    if not ok:
        return {"ok": False, "type": tipo, "ms": ms, "codigo": codigo,
                "lectura": inv.CODIGOS.get(str(codigo)), "error": error}
    limpio, tapados = inv.redactar(resultado)
    return {"ok": True, "type": tipo, "ms": ms, "codigo": codigo,
            "campos_redactados": tapados, "result": limpio}


# ═════════════════════════════════════════════════════════════════════════════
# TIKTOK SHOP (7-oct-2026)
# ═════════════════════════════════════════════════════════════════════════════
#
# PARA QUÉ: sondear EN VIVO qué permite la API de TikTok antes de automatizar el
# agendado de envíos (bodegas, franjas, división, pesos, plazos). Sólo se puede
# desde aquí: la IP de Railway está en la lista permitida de TikTok y aquí vive
# la llave de la tienda.
#
# LOS CANDADOS, en el orden en que se evalúan:
#
#   1. SÓLO ADMIN CON SESIÓN (`solo_admin`, el mismo de Temu): falla CERRADO
#      aunque AUTH_ENFORCED / RBAC_ENFORCED estén apagados; la X-API-Key no pasa.
#   2. SÓLO LECTURA POR CONSTRUCCIÓN: el cuerpo trae `consulta` (un NOMBRE del
#      catálogo) y `params`. No existe campo de ruta ni de método: los pone el
#      servidor. Cualquier otra llave en el cuerpo, 400. El catálogo se valida
#      contra la lista negra AL IMPORTAR (`inv_tk.validar_catalogo`).
#   3. PARÁMETROS DECLARADOS y con forma; `shop_cipher` lo agrega el servidor.
#      Una consulta en CUARENTENA (lo marca el catálogo) no sale: sólo la abre
#      su nombre exacto en INVESTIGACION_TIKTOK_ABIERTAS, y un nombre que no
#      exista no abre ninguna.
#   4. LÍMITE GLOBAL por minuto (configurable) y 2 a la vez: TikTok no publica
#      una cuota fija por app y tienda, y la que haya es la misma del aviso de
#      pedidos, las guías cada 20 min y el fan-out de stock.
#   5. NO RENUEVA LA LLAVE: se llama `tk.llamar(…, _reintento=True)`, que es la
#      rama que NO pasa por `refrescar_y_guardar`. Un `105002` se devuelve como
#      «token vencido: lo renueva producción» y nada más. Una página de lectura
#      no escribe en la tabla de tokens.
#   6. LA RESPUESTA SALE REDACTADA por lista blanca, y sin una sola liga: la
#      etiqueta NO se descarga y su `doc_url` sale tapada.
#   7. SIN LLAVES A LA VISTA: ni token, ni app secret, ni firma, ni cifrado de
#      la tienda salen en la respuesta, en un error o en el log. De una
#      excepción que no sea de TikTok sólo se muestra el NOMBRE de su clase (el
#      texto de httpx trae la URL entera, con `sign` y `shop_cipher`).
#
# AUDITORÍA: cada consulta deja UN renglón en el log con quién, cuál consulta,
# sus parámetros (ya validados: ids, estados, fechas, página), el código de
# TikTok, los milisegundos y cuántos campos se taparon. Nunca el cuerpo de la
# respuesta. No hay tabla nueva.
#
# La firma y el sobre NO se reimplementan: se llama `services.tiktok.llamar`,
# el mismo que usan los pedidos y las guías. No depende de TIKTOK_ENABLED:
# producción opera con esa bandera apagada.


def _acotar(valor: Any, minimo: float, maximo: float, omision: float) -> float:
    """El número, o `omision` si no es un número o se sale del rango.

    Las tres variables numéricas llegan como TEXTO a propósito (config.py): un
    `INVESTIGACION_TIKTOK_POR_MINUTO=abc` declarado como entero no caía a la
    omisión — tiraba el arranque del backend entero.
    """
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return omision
    return v if minimo <= v <= maximo else omision      # `nan` e `inf` → omisión


TIMEOUT_TIKTOK_S = _acotar(settings.investigacion_tiktok_timeout_s, 3.0, 30.0, 15.0)
_PAGINA_MAX_TK = int(_acotar(settings.investigacion_tiktok_pagina_max, 1, 50, 20))
# LA CUARENTENA VIVE EN EL CATÁLOGO (`Consulta.en_cuarentena`); la variable sólo
# ABRE, por nombre exacto. Antes era al revés —la variable LISTABA lo cerrado— y
# fallaba abierta: un nombre mal escrito, otro separador, unas comillas o usarla
# para cerrar otra consulta dejaban abierta `paquetes.combinables` sin aviso.
_ABIERTAS_TK, _ABIERTAS_MALAS_TK = inv_tk.leer_abiertas(
    settings.investigacion_tiktok_abiertas)
if _ABIERTAS_MALAS_TK:
    # Sin excepción: una variable mal escrita no tira el arranque. Queda todo
    # cerrado, que es el lado seguro, y se dice.
    log.error("INVESTIGACIÓN tiktok: INVESTIGACION_TIKTOK_ABIERTAS trae nombres que "
              "NO están en el catálogo (%s). No se abre NINGUNA consulta en "
              "cuarentena: los nombres van exactos, en minúsculas y separados por "
              "coma.", ", ".join(_ABIERTAS_MALAS_TK))
_limitador_tk = inv_tk.Limitador(
    maximo=int(_acotar(settings.investigacion_tiktok_por_minuto, 1, 120, 30)),
    ventana_s=60.0)
# Cada llamada al documento hace que TikTok genere una liga firmada de 24 h con
# la etiqueta. Aquí no se muestra, pero no hay por qué fabricarlas de a montón.
_limitador_tk_liga = inv_tk.Limitador(maximo=6, ventana_s=60.0)
_simultaneas_tk = asyncio.Semaphore(2)


def _llaves_tiktok() -> tuple[str | None, str | None]:
    """Token y cifrado de la tienda, de donde los lee todo el backend.

    SÍNCRONO (psycopg2 / pymysql): se llama con `asyncio.to_thread` (regla 11).
    Sólo LEE: aquí no se renueva nada.
    """
    return tk.access_token(), tk.cipher()


def _tiktok_configurado() -> bool:
    return bool(settings.tiktok_app_key and settings.tiktok_app_secret)


def _auditar(actor: str, consulta: str, params: Any, codigo: Any, ms: int,
             tapados: int) -> None:
    """EL renglón de auditoría. Sin cuerpo, sin llaves, sin cifrado."""
    log.info("INVESTIGACIÓN tiktok: quien=%s consulta=%s params=%s codigo=%s "
             "ms=%d redactados=%d", actor, consulta,
             json.dumps(params, ensure_ascii=False, sort_keys=True), codigo, ms, tapados)


@router.get("/tiktok/consultas")
async def tiktok_consultas(quien=Depends(solo_admin)) -> dict[str, Any]:
    """El catálogo cerrado de lecturas, con sus campos, y los candados."""
    return {
        "tiktok_configurado": _tiktok_configurado(),
        "aviso": inv_tk.AVISO,
        "limite": {"llamadas": _limitador_tk.maximo,
                   "por_segundos": int(_limitador_tk.ventana_s),
                   "alcance": "global (la cuota de TikTok es de la app y la tienda, "
                              "compartida con el aviso de pedidos, las guías y el stock)"},
        "pagina_max": _PAGINA_MAX_TK,
        "ids_max": inv_tk.IDS_MAX,
        "timeout_s": TIMEOUT_TIKTOK_S,
        "codigos": inv_tk.CODIGOS,
        "consultas": inv_tk.para_pantalla(pagina_max=_PAGINA_MAX_TK,
                                          abiertas=_ABIERTAS_TK),
        # La cuarentena EFECTIVA, para que nadie tenga que deducirla.
        "cuarentena": sorted(n for n, c in inv_tk.CATALOGO.items()
                             if c.en_cuarentena and n not in _ABIERTAS_TK),
        "abiertas": sorted(_ABIERTAS_TK),
        "abiertas_descartadas": len(_ABIERTAS_MALAS_TK),
        "vetadas": [{"metodo": m, "ruta": r, "que_hace": q}
                    for m, r, q in inv_tk.PROHIBIDAS],
        "regla": inv_tk.regla(),
    }


@router.post("/tiktok")
async def tiktok_consultar(peticion: Any = Body(default=None),
                           quien=Depends(solo_admin)) -> dict[str, Any]:
    """
    Una LECTURA del catálogo a la Open API de TikTok, con la respuesta redactada.

    Un error de TikTok NO es un error de este endpoint: contesta 200 con
    `ok: false`, el `codigo` y su lectura — en una investigación el código ES
    el hallazgo ("sin permiso" dice qué permiso le falta a la app).
    """
    actor = getattr(quien, "actor", "?")

    # 1. El candado. Antes que nada que toque la red, la BD o la cuota.
    try:
        if not isinstance(peticion, dict):
            raise inv_tk.Rechazo('El cuerpo es {"consulta": "…", "params": {…}}.')
        if set(peticion) - {"consulta", "params"}:
            raise inv_tk.Rechazo("Sólo se aceptan `consulta` y `params`: la ruta y el "
                                 "método los pone el servidor, no quien pregunta.")
        pedido = inv_tk.preparar(peticion.get("consulta"), peticion.get("params"),
                                 pagina_max=_PAGINA_MAX_TK, abiertas=_ABIERTAS_TK)
    except inv_tk.Rechazo as exc:
        pedida = peticion.get("consulta") if isinstance(peticion, dict) else None
        nombre = pedida if isinstance(pedida, str) and pedida in inv_tk.CATALOGO \
            else "(fuera del catálogo)"
        log.warning("INVESTIGACIÓN tiktok RECHAZADA: quien=%s consulta=%s motivo=%s",
                    actor, nombre, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from None

    consulta = pedido.consulta
    if not _tiktok_configurado():
        raise HTTPException(status_code=503,
                            detail="TikTok no está configurado en este ambiente "
                                   "(faltan TIKTOK_APP_KEY / TIKTOK_APP_SECRET).")

    # 2. El límite: sólo cuentan las consultas que sí van a salir.
    for limitador, que in ((_limitador_tk_liga if consulta.acuna_liga else None,
                            "consultas de etiqueta"),
                           (_limitador_tk, "consultas")):
        if limitador is not None and not limitador.permite():
            espera = limitador.espera_s()
            _auditar(actor, consulta.nombre, pedido.para_log(), "limite", 0, 0)
            raise HTTPException(status_code=429,
                                detail=f"Límite de {limitador.maximo} {que} por minuto "
                                       f"(la cuota de TikTok es compartida con "
                                       f"producción). Intenta en {espera} s.",
                                headers={"Retry-After": str(espera)})

    # 3. Las llaves: lectura síncrona, en un hilo. De un fallo aquí sólo sale
    #    el nombre de la clase (el texto puede traer el DSN).
    try:
        token, cifrado = await asyncio.to_thread(_llaves_tiktok)
    except Exception as exc:  # noqa: BLE001
        _auditar(actor, consulta.nombre, pedido.para_log(), "sin_llaves", 0, 0)
        raise HTTPException(status_code=503,
                            detail="No se pudo leer la llave de TikTok "
                                   f"({type(exc).__name__}).") from None
    if not token or (consulta.con_tienda and not cifrado):
        _auditar(actor, consulta.nombre, pedido.para_log(), "sin_llaves", 0, 0)
        raise HTTPException(status_code=503,
                            detail="TikTok sin token o sin tienda autorizada en este "
                                   "ambiente: no hay con qué preguntar.")
    secretos = (token, cifrado, settings.tiktok_app_key, settings.tiktok_app_secret)
    query = dict(pedido.query)
    if consulta.con_tienda:
        query["shop_cipher"] = cifrado

    # 4. La llamada: async de verdad (httpx), con techo de tiempo corto, y por
    #    la rama de `llamar` que NO renueva el token.
    t0 = time.monotonic()
    ok, codigo, resultado, error = False, None, None, None
    async with _simultaneas_tk:
        try:
            resultado = await asyncio.wait_for(
                tk.llamar(pedido.ruta, token, query, pedido.cuerpo, consulta.metodo,
                          _reintento=True),
                timeout=TIMEOUT_TIKTOK_S)
            ok, codigo = True, "0"
        except asyncio.TimeoutError:
            codigo, error = "timeout", f"TikTok no contestó en {int(TIMEOUT_TIKTOK_S)} s."
        except RuntimeError as exc:           # el error de TikTok es el dato
            try:
                codigo, error = inv_tk.leer_error(str(exc), secretos, pedido.ids_propios())
            except Exception:  # noqa: BLE001 — si la limpieza fallara, no sale el texto crudo
                codigo, error = "sin_codigo", inv_tk.SIN_LEER
        except Exception as exc:  # noqa: BLE001 — su texto puede traer la URL firmada
            codigo = type(exc).__name__
            error = f"No hubo respuesta de TikTok ({type(exc).__name__})."
    ms = int((time.monotonic() - t0) * 1000)

    base = {"canal": "tiktok", "consulta": consulta.nombre, "metodo": consulta.metodo,
            "ruta": pedido.ruta, "params": pedido.params, "ms": ms}
    tapados, llaves, limpio = 0, [], None
    if ok:
        # 5. La redacción. Si el redactor mismo fallara, NO sale la respuesta
        #    cruda: se contesta que no se pudo redactar.
        try:
            limpio, tapados, llaves = inv_tk.redactar(resultado, consulta.nombre)
            limpio = inv_tk.sin_secretos(limpio, secretos)
            # Los NOMBRES de las llaves tapadas también salen en la respuesta:
            # una llave con forma de identificador puede ser el cifrado mismo.
            llaves = inv_tk.sin_secretos(llaves, secretos)
        except Exception as exc:  # noqa: BLE001
            ok, codigo = False, "redactor"
            error = f"No se pudo redactar la respuesta ({type(exc).__name__}): no se muestra."
    if not ok:
        _auditar(actor, consulta.nombre, pedido.para_log(), codigo, ms, 0)
        return {"ok": False, **base, "codigo": codigo,
                "lectura": inv_tk.CODIGOS.get(str(codigo)), "error": error}
    _auditar(actor, consulta.nombre, pedido.para_log(), codigo, ms, tapados)
    return {"ok": True, **base, "codigo": codigo,
            "lectura": inv_tk.CODIGOS.get(str(codigo)),
            "campos_redactados": tapados, "llaves_redactadas": llaves, "result": limpio}
