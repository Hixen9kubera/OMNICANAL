"""
investigacion.py — Llamadas de INVESTIGACIÓN a marketplaces, desde producción.

  GET  /api/investigacion/temu/tipos   → la regla del candado + tipos sugeridos
  POST /api/investigacion/temu         → {type, params} → respuesta REDACTADA

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
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core import identidad as core_identidad
from services import investigacion_temu as inv
from services import temu

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
