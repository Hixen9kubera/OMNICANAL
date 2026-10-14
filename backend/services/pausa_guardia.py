"""
pausa_guardia.py — La GUARDIA de la pausa: con un canal en pausa, lo que vuelva a la
venta se apaga en minutos, no en horas.

POR QUÉ EXISTE. Brandon, 14-oct-2026: «¿puedes volver a checar si tenemos algún
producto encendido? Si es así apágalo y genera una seguridad para que no se vuelvan a
prender».

LO MEDIDO ESE DÍA. De 10:25 a 10:33 (CDMX) se apagaron las 81 publicaciones de TikTok
desde el Publicador y a las 10:34 se puso la pausa. Entre 10:40 y 11:00 TikTok mandó
335 avisos de auditoría (tipo 37: AUDITING → APPROVED) de 162 productos: alguien los
ACTIVÓ en bloque, y en TikTok activar pasa por auditoría. No fue omnicanal: cero envíos
en `ops.channel_submissions`, cero llamadas del seguro y ninguna activación en los
registros. Volvieron a la venta 218 publicaciones, y el vigilante del censo —una vez
por hora y con tope de 60— tardó tres censos en apagarlas.

LO QUE NO SE PUEDE: impedir que una persona active desde el Seller Center. Eso no pasa
por nuestro backend. Lo que sí: enterarse pronto y apagar.

LAS CAPAS (todas actúan SÓLO con el canal en pausa; sin pausa no hacen nada):
  1. AVISO DE TIKTOK (`aviso_tiktok`, lo llama el webhook): cada aviso de auditoría de
     producto programa un barrido de TikTok `_ESPERA_AVISO_S` después del ÚLTIMO aviso,
     así una ráfaga de cientos de avisos es UN barrido. Lo que alguien active se apaga
     en un par de minutos.
  2. BARRIDO EN VIVO cada `CADA_MIN` minutos (`barrer_todo`, job del scheduler): le
     pregunta AL CANAL qué tiene —no al censo, que puede traer una hora de atraso— y
     apaga lo que esté a la venta, sin tope. Es lo que cubre a Temu, que no avisa, y a
     TikTok si un aviso se pierde. TikTok: el catálogo completo por `products/search`
     (sin filtro, ver `_vivos_tiktok`); Temu: la cubeta 1.
  3. El vigilante del censo (`publicaciones_lote.vigilar`), cada hora, ahora sin tope.
  Y del lado propio, un candado: con el canal en pausa omnicanal no publica, no edita
  y no prende nada ahí (`publicar.confirmar`, `fanout_seguro._prender`).

QUÉ CUENTA COMO «POR APAGAR» (lo mismo que la pantalla):
  TikTok  ACTIVATE.
  Temu    2/8 (a la venta), 3/1 (agotada: vuelve sola en cuanto sube el contador), 2/4,
          y 3/3 (retenida por Temu) sólo si nunca se le mandó la orden: Temu la acepta y
          no cambia de estado, así que repetírsela cada 10 minutos no enseña nada.

Apaga con `publicaciones_lote.desactivar_publicaciones`: lee en vivo, apaga, relee,
deja el estado en `channel.listings` firmado `panel_lote` y lo anota en
`ops.process_log` firmado «pausa del canal». Avisa igual que el vigilante (amarillo: lo
que había vuelto y se apagó; rojo: lo que no se pudo).

REGLA 11. Todo aquí es SÍNCRONO (el canal por `fanout_stock._en_hilo`, kubera por
psycopg2). El scheduler y el webhook lo llaman con `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from services import publicaciones_lote as L

log = logging.getLogger("omnicanal.pausa_guardia")

CADA_MIN = 10                 # el barrido en vivo
_ESPERA_AVISO_S = 90.0        # tras el ÚLTIMO aviso de TikTok (agrupa la ráfaga)
_REINTENTOS_OCUPADO = 5       # si al tocar el barrido ya hay otro corriendo
_PAGE_SIZE = 100
_MAX_PAGINAS = 60             # cortacircuitos: 6,000 productos
# Avisos de TikTok que disparan un barrido: el 37 trae `data.audit.status`
# (AUDITING / APPROVED) y `product_id`. Activar un producto apagado pasa por ahí.
_TIPOS_TIKTOK = {37}


# ── Lo que el canal tiene a la venta AHORA ───────────────────────────────────

def _vivos_tiktok() -> list[dict[str, Any]]:
    """TODO el catálogo de TikTok, con el estado de cada producto; `_por_apagar` se
    queda con los que venden.

    SIN EL FILTRO `status` DE LA BÚSQUEDA, a propósito (v0.647.0). La primera versión
    pedía `{"status": "ACTIVATE"}` y el primer barrido en producción contestó «0 vistos»
    — correcto ese día (no había nada a la venta), pero indistinguible de un filtro que
    TikTok entendiera de otra forma y devolviera siempre vacío: la guardia quedaría
    ciega justo para lo que existe. Sin filtro es la MISMA lectura que el censo, probada
    en producción desde agosto, y «vistos» dice cuántos productos se revisaron. Cuesta
    unas 13 páginas por barrido."""
    from services import fanout_stock
    from services import tiktok as tk
    token, ciph = tk.access_token(), tk.cipher()
    if not (token and ciph):
        raise RuntimeError("TikTok sin token o sin shop_cipher")
    vistos: dict[str, dict[str, Any]] = {}
    page_token = ""
    for _ in range(_MAX_PAGINAS):
        params: dict[str, Any] = {"shop_cipher": ciph, "page_size": _PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        data = fanout_stock._en_hilo(
            lambda p=params: tk.llamar("/product/202309/products/search", token, p, {}, "POST"),
            "guardia: lectura tiktok") or {}
        lote = data.get("products") or []
        for p in lote:
            pid = str(p.get("id") or "")
            if pid:
                vistos.setdefault(pid, p)
        page_token = str(data.get("next_page_token") or "")
        if not page_token or not lote:
            break
    salida = []
    for pid, p in vistos.items():
        skus = p.get("skus") or []
        sku = str((skus[0] if skus else {}).get("seller_sku") or "").strip()
        salida.append({"sku": sku, "listing_id": pid, "estado": str(p.get("status") or "").upper()})
    return salida


def _vivos_temu() -> list[dict[str, Any]]:
    """Lo que Temu tiene en la cubeta 1, que es donde vive todo lo que vende o puede
    volver a vender sola (2/8, 3/1, 3/2, 3/3, 2/4). Con la cubeta, el listado trae el
    estado; sin ella, no (medido el 9-oct)."""
    from services import fanout_stock
    from services import temu as tm
    if not tm.disponible():
        raise RuntimeError("Temu no configurado (faltan TEMU_*)")
    goods = fanout_stock._en_hilo(lambda: tm.listar_productos((1,)), "guardia: lectura temu",
                                  timeout=180) or []
    salida = []
    for g in goods:
        if g.get("status4VO") is None or g.get("goodsId") in (None, ""):
            continue
        salida.append({"sku": str(g.get("outGoodsSn") or "").strip(),
                       "listing_id": str(g.get("goodsId")),
                       "estado": f"{g.get('status4VO')}/{g.get('subStatus4VO')}"})
    return salida


_VIVOS = {"tiktok": _vivos_tiktok, "temu": _vivos_temu}


def _por_apagar(canal: str, vivos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De lo leído en vivo, lo que hay que apagar; primero lo que SÍ vende."""
    panel = L._del_panel(canal)
    salida = []
    for v in vivos:
        c = L.clasificar(canal, v.get("estado"))
        if not c["desactivable"]:
            continue
        ult = panel.get(str(v.get("listing_id")))
        if (c["estado"] in L._NO_CAMBIAN.get(canal, set()) and ult
                and ult.get("accion") == "desactivar"
                and ult.get("estado") in (L.ORDEN_ENVIADA, L.SIN_CONFIRMAR)
                and ult.get("despues") == c["estado"]):
            continue          # retenida que ya recibió la orden
        salida.append({"sku": v.get("sku") or "", "listing_id": str(v["listing_id"]),
                       "grupo": c["grupo"]})
    salida.sort(key=lambda x: x["grupo"] != "a_la_venta")
    return salida


# ── El barrido ───────────────────────────────────────────────────────────────

def barrer(canal: str, motivo: str = f"cada {CADA_MIN} min") -> dict[str, Any]:
    """
    Si `canal` está en pausa: lee en vivo lo que tiene a la venta y lo apaga todo.
    Sin pausa no le pregunta nada al canal. Si no se puede leer la pausa, tampoco
    apaga: un «no sé» no apaga un canal (el siguiente barrido vuelve a preguntar).
    Un barrido por canal a la vez (lo comparte con el vigilante del censo).
    """
    canal = L._canal(canal)
    p = L.pausa(canal, refrescar=True)
    if p.get("en_pausa") is not True:
        return ({"ok": True, "pausa": False} if p.get("legible")
                else {"ok": False, "pausa": None, "motivo": "no pude leer la pausa del canal"})
    with L._turno(canal) as mio:
        if not mio:
            return {"ok": True, "pausa": True, "ocupado": True,
                    "motivo": "ya hay un barrido de este canal en curso"}
        t0 = time.time()
        try:
            vivos = _VIVOS[canal]()
        except Exception as exc:  # noqa: BLE001 — el siguiente barrido vuelve a intentar
            log.warning("guardia de la pausa %s: no pude leer el canal en vivo: %s", canal, exc)
            return {"ok": False, "pausa": True,
                    "motivo": f"no pude leer {L.NOMBRE[canal]} en vivo: {type(exc).__name__}: {exc}"[:300]}
        pendientes = _por_apagar(canal, vivos)
        hecho = L.apagar_pendientes(canal, pendientes)
        salida = {"ok": True, "pausa": True, "motivo": motivo, "vistos": len(vivos),
                  "por_apagar": len(pendientes), **hecho, "segundos": round(time.time() - t0, 1)}
        if pendientes:
            log.warning("GUARDIA DE LA PAUSA %s (%s): %s", canal, motivo, salida)
        return salida


def barrer_todo() -> dict[str, Any]:
    """Un barrido de cada canal en pausa. Lo corre el scheduler, EN UN HILO."""
    salida: dict[str, Any] = {}
    for canal in L.CANALES:
        try:
            r = barrer(canal)
        except Exception as exc:  # noqa: BLE001 — un canal no tumba al otro
            log.warning("guardia de la pausa %s: %s", canal, exc)
            r = {"ok": False, "motivo": f"{type(exc).__name__}: {exc}"[:300]}
        if r.get("pausa") is not False:
            salida[canal] = r
    return salida


async def job() -> None:
    """El job del scheduler (cada `CADA_MIN` min)."""
    r = await asyncio.to_thread(barrer_todo)
    if r:
        log.info("guardia de la pausa: %s", r)


# ── El aviso de TikTok ───────────────────────────────────────────────────────

_aviso_hasta: dict[str, float] = {}
_aviso_tarea: dict[str, asyncio.Task] = {}


def es_aviso_de_producto(payload: Any) -> bool:
    """¿Este aviso de TikTok es de los que pueden significar «alguien lo prendió»? PURA."""
    if not isinstance(payload, dict):
        return False
    try:
        return int(payload.get("type") or 0) in _TIPOS_TIKTOK
    except (TypeError, ValueError):
        return False


async def aviso_tiktok(payload: Any) -> None:
    """
    Lo llama el webhook de TikTok (en segundo plano) con cada aviso. Si es de auditoría
    de producto y TikTok está en pausa, programa un barrido `_ESPERA_AVISO_S` después
    del último aviso. Jamás lanza: corre después de contestarle a TikTok.
    """
    try:
        if not es_aviso_de_producto(payload):
            return
        if await asyncio.to_thread(L.en_pausa, "tiktok") is not True:
            return            # sin pausa (o «no sé»): lo cubre el barrido de cada 10 min
        programar("tiktok")
    except Exception as exc:  # noqa: BLE001
        log.warning("guardia de la pausa: aviso de TikTok: %s", exc)


def programar(canal: str, espera_s: float = _ESPERA_AVISO_S) -> None:
    """Corre (o recorre) el barrido de `canal` a `espera_s` de ahora. Debe llamarse
    desde el event loop."""
    _aviso_hasta[canal] = time.monotonic() + espera_s
    t = _aviso_tarea.get(canal)
    if t is None or t.done():
        _aviso_tarea[canal] = asyncio.create_task(_barrer_tras_avisos(canal))


async def _barrer_tras_avisos(canal: str) -> None:
    for _ in range(_REINTENTOS_OCUPADO + 1):
        while True:
            falta = _aviso_hasta.get(canal, 0.0) - time.monotonic()
            if falta <= 0:
                break
            await asyncio.sleep(min(falta, 15.0))
        try:
            r = await asyncio.to_thread(barrer, canal, f"aviso de {L.NOMBRE[canal]}")
        except Exception as exc:  # noqa: BLE001
            log.warning("guardia de la pausa %s tras aviso: %s", canal, exc)
            return
        if not r.get("ocupado"):
            log.info("guardia de la pausa %s tras aviso: %s", canal, r)
            return
        # Otro barrido iba a la mitad y pudo no ver lo recién activado: otra vuelta.
        _aviso_hasta[canal] = time.monotonic() + 60.0
