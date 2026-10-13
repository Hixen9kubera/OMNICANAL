"""
publicaciones_lote.py — Sacar de la venta, EN LOTE, las publicaciones de un canal.

POR QUÉ EXISTE. Brandon, 13-oct-2026: «tenemos problemas de stock directamente con
almacén, por lo que el stock actual puede que no sea el correcto … necesito por el
momento apagar todo Temu», y pidió que el Publicador deje escoger un canal, ver sus
SKUs publicados, seleccionar un lote o TODOS y desactivarlos. Hasta hoy la única forma
era publicación por publicación en el Seller Center.

QUÉ HACE
  · `listar(canal)`: las publicaciones del canal como las vio el último censo
    (`channel.listings`), con su estado en palabras, lo que ofrecen, el stock de Woo,
    y quién las tiene apagadas (el seguro de stock 0 o esta misma pantalla).
  · `desactivar(canal, skus)`: para cada SKU lee la publicación EN VIVO, le pide al
    canal que la saque de la venta, la RELEE para confirmar, deja el estado nuevo en
    `channel.listings` y lo anota en `ops.process_log` con quién lo pidió.

INACTIVA, NO BORRA. Son las mismas dos llamadas que el seguro de stock 0 probó en
vivo el 9-oct-2026 (`fanout_seguro._apagar`): `bg.local.goods.sale.status.set
{goodsId, onsale: 0}` en Temu (`2/8` y `3/1` → `3/2`) y
`/product/202309/products/deactivate` en TikTok (`ACTIVATE → SELLER_DEACTIVATED`).
La publicación sigue en el canal, apagada, igual que si se apagara a mano en el
Seller Center. Aquí no hay ninguna llamada de borrado.

LO QUE SE APAGA DESDE AQUÍ SE QUEDA APAGADO. Es una decisión de una persona:
  · no lleva la marca del seguro, así que el seguro no la regresa sola;
  · si el seguro YA la tenía apagada (su marca), se le SUELTA la marca: si no, al
    volver el stock —el mismo stock del que hoy se desconfía— la regresaría a la venta;
  · cada cambio queda firmado `panel_lote` en `channel.listing_history`, que es donde
    el seguro mira si alguien pausó el canal en bloque (`apagado_en_bloque`).

LO QUE NO HACE
  · No reactiva. Volver a prender es otra decisión; lo que se apagó desde aquí queda
    en `ops.process_log` (proceso `publicaciones_lote`) con el estado que tenía antes,
    que es lo que hará falta para regresarlo.
  · No impide que alguien publique o prenda algo después: no es una «pausa del canal».
  · Sólo Temu y TikTok, que es donde hay una llamada de «sacar de la venta» probada.
    Mercado Libre ya está pausado entero y su PUT está bloqueado; Amazon y Walmart no
    tienen un «apagar» equivalente.

REGLA 11. Todo aquí es SÍNCRONO (psycopg2 y las llamadas al canal por
`fanout_stock._en_hilo`): las rutas que lo usan son `def`, no `async def`, y corren
en el threadpool de FastAPI.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

log = logging.getLogger("omnicanal.publicaciones_lote")

CANALES = ("temu", "tiktok")
NOMBRE = {"temu": "Temu", "tiktok": "TikTok"}
# Publicaciones por llamada. Cada una son dos o tres llamadas al canal más la espera
# para releer: 10 caben holgadas en una petición; la pantalla manda de 5 en 5.
TOPE_POR_LLAMADA = 10
PROCESO = "publicaciones_lote"
VIA = "panel_lote"                 # la firma en `channel.listing_history.detectado_via`

# En qué GRUPO cae cada estado y cómo se le dice. Lo que no esté aquí se enseña crudo
# y no se toca: un estado que nadie conoce no se adivina.
#   a_la_venta    se puede comprar ahora
#   publicada     no vende en este momento, pero el canal la regresa SOLA a la venta
#   apagada       fuera de la venta por decisión del vendedor (lo que deja esta pantalla)
#   apagada_canal fuera de la venta por decisión del canal
#   en_revision   el canal la está revisando; no deja apagarla hasta que termine
#   sin_publicar  borrador, incompleta o rechazada: nunca ha estado a la venta así
#   borrada       ya no existe en el canal
_ESTADOS: dict[str, dict[str, tuple[str, str]]] = {
    "temu": {
        "2/8": ("a_la_venta", "A la venta"),
        "3/1": ("publicada", "Agotada · Temu la regresa sola a la venta"),
        "3/3": ("publicada", "Fuera de la venta por Temu · va y viene sola"),
        "2/4": ("publicada", "Activa sin vender (2/4)"),
        "3/2": ("apagada", "Apagada"),
        "4/7": ("sin_publicar", "Incompleta"),
        "5/None": ("sin_publicar", "Borrador"),
        "6/None": ("sin_publicar", "Sin publicar (6)"),
    },
    "tiktok": {
        "ACTIVATE": ("a_la_venta", "A la venta"),
        "PENDING": ("en_revision", "En revisión de TikTok"),
        "SELLER_DEACTIVATED": ("apagada", "Apagada"),
        "PLATFORM_DEACTIVATED": ("apagada_canal", "Apagada por TikTok"),
        "FREEZE": ("apagada_canal", "Congelada por TikTok"),
        "FAILED": ("sin_publicar", "Revisión fallida"),
        "DRAFT": ("sin_publicar", "Borrador"),
        "DELETED": ("borrada", "Borrada"),
    },
}
# Desde qué estados se le PIDE al canal sacarla de la venta. En Temu, además de las
# dos probadas (`2/8`, `3/1`), las dos desde las que vuelve sola a la venta: `3/3` y
# `2/4`. Dejarlas fuera de «apagar todo» sería dejar la puerta abierta. En TikTok
# `deactivate` sólo aplica a `ACTIVATE`.
_DESACTIVABLES = {"temu": {"2/8", "3/1", "3/3", "2/4"}, "tiktok": {"ACTIVATE"}}
_GRUPOS = ("a_la_venta", "publicada", "en_revision", "apagada", "apagada_canal",
           "sin_publicar", "borrada", "desconocido")

# Resultados por publicación (vocabulario cerrado: la pantalla los pinta por nombre).
APAGADA = "apagada"                 # se le pidió, se releyó y quedó fuera de la venta
YA_APAGADA = "ya_apagada"           # en vivo ya estaba apagada: no se llamó
NO_APLICA = "no_aplica"             # borrador, en revisión, borrada…: no hay nada que apagar
SIN_PUBLICACION = "sin_publicacion"  # ese SKU no tiene publicación en el canal
SIN_CONFIRMAR = "sin_confirmar"     # el canal aceptó y todavía no enseña el cambio
ERROR = "error"                     # no se pudo leer, o el canal rechazó la llamada


def _canal(canal: Any) -> str:
    c = str(canal or "").strip().lower()
    if c not in CANALES:
        raise ValueError(f"canal: {' | '.join(CANALES)} (llegó {canal!r})")
    return c


def clasificar(canal: str, estado: Any) -> dict[str, Any]:
    """Grupo, etiqueta y si se le puede pedir al canal que la apague. PURA."""
    crudo = str(estado if estado is not None else "").strip()
    clave = crudo.upper() if canal == "tiktok" else crudo
    grupo, etiqueta = _ESTADOS.get(canal, {}).get(clave, ("desconocido", f"{NOMBRE.get(canal, canal)} {crudo or '?'}"))
    return {"estado": clave, "grupo": grupo, "etiqueta": etiqueta,
            "desactivable": clave in _DESACTIVABLES.get(canal, set())}


# ── Leer ─────────────────────────────────────────────────────────────────────

_SQL_LISTA = """
    select l.sku::text as sku, l.listing_id, l.status, l.stock_own, l.price, l.url,
           a.legacy_code as cuenta, p.name as titulo, w.stock_woo,
           to_char(l.updated_at at time zone 'America/Mexico_City', 'YYYY-MM-DD HH24:MI') as visto
      from channel.listings l
      join core.accounts a on a.id = l.account_id
      left join core.products p on p.sku = l.sku
      left join ops.stock_watch_photo w on w.sku = l.sku
     where l.canal = %(c)s and nullif(l.listing_id, '') is not null
           {filtro}
     order by l.sku, l.listing_id"""

# Lo último que esta pantalla le hizo a cada publicación (para decir «apagada desde el
# panel» y, el día que haga falta regresarlas, saber cuáles y en qué estado estaban).
_SQL_PANEL = """
    select distinct on (detalle->>'listing_id')
           detalle->>'listing_id' as listing_id, accion, estado,
           detalle->>'antes' as antes, detalle->>'despues' as despues,
           coalesce(detalle->>'actor', actor) as actor,
           to_char(created_at at time zone 'America/Mexico_City', 'YYYY-MM-DD HH24:MI') as cuando
      from ops.process_log
     where proceso = %(p)s and detalle->>'canal' = %(c)s and estado = 'ok'
     order by detalle->>'listing_id', created_at desc, id desc"""


def _publicaciones(canal: str, skus: list[str] | None = None) -> list[dict[str, Any]]:
    from services import supabase_db as sdb
    if skus is None:
        return sdb.fetch_all(_SQL_LISTA.format(filtro=""), {"c": canal})
    return sdb.fetch_all(_SQL_LISTA.format(filtro="and upper(l.sku::text) = any(%(s)s)"),
                         {"c": canal, "s": [s.upper() for s in skus]})


def _del_seguro(canal: str) -> dict[str, dict[str, Any]]:
    """{listing_id: marca} de lo que el seguro de stock 0 tiene apagado. Vacío si no
    se pudo leer: es un dato de la lista, no una decisión."""
    try:
        from services import fanout_seguro
        return {str(m["item_id"]): m for m in fanout_seguro.marcas_vigentes(canal)}
    except Exception as exc:  # noqa: BLE001
        log.warning("lote: no pude leer las marcas del seguro de %s: %s", canal, exc)
        return {}


def _del_panel(canal: str) -> dict[str, dict[str, Any]]:
    try:
        from services import supabase_db as sdb
        return {str(f["listing_id"]): f
                for f in sdb.fetch_all(_SQL_PANEL, {"p": PROCESO, "c": canal}) if f.get("listing_id")}
    except Exception as exc:  # noqa: BLE001
        log.warning("lote: no pude leer la bitácora del panel de %s: %s", canal, exc)
        return {}


def listar(canal: str) -> dict[str, Any]:
    """
    Las publicaciones del canal, como las vio el ÚLTIMO CENSO (pasa cada hora). Sólo
    lee kubera: no le pregunta nada al canal. El estado vivo se lee al desactivar.
    """
    canal = _canal(canal)
    filas = _publicaciones(canal)
    seguro, panel = _del_seguro(canal), _del_panel(canal)
    items: list[dict[str, Any]] = []
    resumen = {g: 0 for g in _GRUPOS}
    for f in filas:
        c = clasificar(canal, f.get("status"))
        item = str(f["listing_id"])
        apagada_por = None
        if c["grupo"] in ("apagada", "apagada_canal") or not c["desactivable"]:
            # Sólo se atribuye si SIGUE apagada: una que alguien volvió a prender no
            # es «apagada desde el panel» por más que la bitácora lo recuerde.
            ult = panel.get(item)
            if c["grupo"] == "apagada" and ult and ult.get("accion") == "desactivar":
                apagada_por = {"quien": "panel", "cuando": ult.get("cuando"),
                               "actor": ult.get("actor"), "antes": ult.get("antes")}
            elif c["grupo"] == "apagada" and item in seguro:
                apagada_por = {"quien": "seguro", "cuando": None, "actor": None,
                               "antes": None}
        resumen[c["grupo"]] += 1
        items.append({
            "sku": f["sku"], "listing_id": item, "titulo": f.get("titulo"),
            "cuenta": f.get("cuenta"), "url": f.get("url"),
            "estado": c["estado"], "grupo": c["grupo"], "etiqueta": c["etiqueta"],
            "desactivable": c["desactivable"],
            "stock_canal": f.get("stock_own"), "stock_woo": f.get("stock_woo"),
            "precio": float(f["price"]) if f.get("price") is not None else None,
            "visto": f.get("visto"), "apagada_por": apagada_por,
        })
    return {
        "canal": canal, "nombre": NOMBRE[canal], "total": len(items),
        "desactivables": sum(1 for i in items if i["desactivable"]),
        "censo": max((i["visto"] for i in items if i["visto"]), default=None),
        "resumen": resumen, "tope_por_llamada": TOPE_POR_LLAMADA, "items": items,
    }


def canales() -> list[dict[str, Any]]:
    return [{"id": c, "nombre": NOMBRE[c]} for c in CANALES]


# ── Desactivar ───────────────────────────────────────────────────────────────

def _limpiar_skus(skus: Any) -> list[str]:
    if not isinstance(skus, (list, tuple)):
        raise ValueError("skus: una lista de SKUs")
    vistos: dict[str, str] = {}
    for s in skus:
        t = str(s or "").strip()
        if t:
            vistos.setdefault(t.upper(), t)
    if not vistos:
        raise ValueError("skus: la lista viene vacía")
    if len(vistos) > TOPE_POR_LLAMADA:
        raise ValueError(f"skus: a lo mucho {TOPE_POR_LLAMADA} por llamada (llegaron {len(vistos)})")
    return list(vistos.values())


def _anotar(canal: str, r: dict[str, Any], lote: str, actor: str, segundos: float) -> None:
    """La fila de la bitácora. Mejor esfuerzo: que kubera no la guarde no deshace lo
    que el canal ya hizo, y el estado nuevo queda de todos modos en `channel.listings`."""
    try:
        from services import supabase_db as sdb
        detalle = {"canal": canal, "listing_id": r.get("listing_id"), "antes": r.get("antes"),
                   "despues": r.get("despues"), "resultado": r.get("resultado"),
                   "detalle": r.get("detalle"), "lote": lote, "actor": actor or None}
        sdb.execute(
            "insert into ops.process_log (proceso, origen, sku, accion, estado, detalle, duracion_s) "
            "values (%s, 'panel', %s, 'desactivar', %s, %s::jsonb, %s)",
            (PROCESO, r.get("sku"), "ok" if r.get("resultado") == APAGADA else str(r.get("resultado")),
             json.dumps(detalle, ensure_ascii=False), round(float(segundos), 2)))
    except Exception as exc:  # noqa: BLE001
        log.warning("lote: no pude anotar %s/%s en ops.process_log: %s", canal, r.get("sku"), exc)


def _reflejar(canal: str, sku: str, item: str, estado: str) -> None:
    """Deja el estado RELEÍDO en `channel.listings`, firmado `panel_lote`: la lista
    deja de contarla a la venta sin esperar al censo y `listing_history` guarda que
    fue una persona, que es lo que el seguro mira para no ir contra una pausa."""
    try:
        from services import supabase_db as sdb
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('app.via', %s, true)", (VIA,))
            cur.execute(
                """update channel.listings set status = %(e)s, updated_at = now()
                    where canal = %(c)s and sku = %(s)s and listing_id = %(i)s
                      and status is distinct from %(e)s""",
                {"e": estado, "c": canal, "s": sku, "i": str(item)})
    except Exception as exc:  # noqa: BLE001 — el censo siguiente lo corrige
        log.warning("lote: no pude reflejar %s/%s=%s en channel.listings: %s", canal, sku, estado, exc)


def _soltar_marca(canal: str, sku: str, item: str) -> bool:
    """Si el seguro de stock 0 tenía ESTA publicación apagada, se le suelta la marca
    para que no la regrese sola. True si había marca y se soltó."""
    try:
        from services import fanout_seguro
        if not fanout_seguro.marca(canal, item):
            return False
        return bool(fanout_seguro.soltar(sku, canal).get("ok"))
    except Exception as exc:  # noqa: BLE001
        log.warning("lote: no pude soltar la marca del seguro de %s/%s: %s", canal, sku, exc)
        return False


def desactivar(canal: str, skus: list[str], actor: str = "") -> dict[str, Any]:
    """
    Saca de la venta las publicaciones de `skus` en `canal` (a lo mucho
    TOPE_POR_LLAMADA). Por publicación: leer EN VIVO → pedirle al canal que la apague
    → esperar → RELEER → reflejar y anotar. Devuelve un resultado por publicación.

    Se decide con el estado VIVO, no con el del censo, que puede traer una hora de
    atraso: la que en vivo ya está apagada no se vuelve a llamar.
    """
    from services import fanout_seguro as S
    canal = _canal(canal)
    skus = _limpiar_skus(skus)
    t0 = time.time()
    lote = uuid.uuid4().hex[:12]
    por_sku: dict[str, list[dict[str, Any]]] = {}
    for f in _publicaciones(canal, skus):
        por_sku.setdefault(str(f["sku"]).upper(), []).append(f)

    resultados: list[dict[str, Any]] = []
    por_confirmar: list[dict[str, Any]] = []
    for sku in skus:
        pubs = por_sku.get(sku.upper())
        if not pubs:
            resultados.append({"sku": sku, "listing_id": None, "antes": None, "despues": None,
                               "resultado": SIN_PUBLICACION,
                               "detalle": f"sin publicación en {NOMBRE[canal]}"})
            continue
        for f in pubs:
            item = str(f["listing_id"])
            r: dict[str, Any] = {"sku": str(f["sku"]), "listing_id": item, "antes": None,
                                 "despues": None, "resultado": ERROR, "detalle": None}
            resultados.append(r)
            with S._candado(canal, item):
                vivo = S._leer_vivo(canal, item)
                if not vivo.get("ok"):
                    r["detalle"] = f"no se pudo leer en vivo: {vivo.get('motivo')}"
                    continue
                c = clasificar(canal, vivo.get("estado"))
                r["antes"] = r["despues"] = c["estado"]
                if c["grupo"] == "apagada":
                    r["resultado"] = YA_APAGADA
                    r["detalle"] = "ya estaba apagada"
                    continue
                if not c["desactivable"]:
                    r["resultado"] = NO_APLICA
                    r["detalle"] = f"{c['etiqueta']}: no hay nada que apagar"
                    continue
                try:
                    S._apagar(canal, item)
                except Exception as exc:  # noqa: BLE001
                    r["detalle"] = f"{NOMBRE[canal]} rechazó la llamada: {type(exc).__name__}: {exc}"[:400]
                    continue
                r["resultado"] = SIN_CONFIRMAR
                por_confirmar.append(r)

    # Releer. La lectura del canal va unos segundos atrás de lo que se le pide, así
    # que se espera UNA vez por tanda (no una por publicación) y se insiste con las
    # que todavía no enseñan el cambio.
    for _ in range(S._relecturas()):
        pendientes = [r for r in por_confirmar if r["resultado"] == SIN_CONFIRMAR]
        if not pendientes:
            break
        S._dormir(S._espera_s())
        for r in pendientes:
            vivo = S._leer_vivo(canal, r["listing_id"])
            if not vivo.get("ok"):
                continue
            nuevo = clasificar(canal, vivo.get("estado"))
            r["despues"] = nuevo["estado"]
            if nuevo["estado"] != r["antes"] and not S.a_la_venta(canal, nuevo["estado"]):
                r["resultado"] = APAGADA
                r["detalle"] = (None if nuevo["grupo"] == "apagada"
                                else f"quedó fuera de la venta como «{nuevo['etiqueta']}»")

    resumen: dict[str, int] = {}
    for r in resultados:
        if r["resultado"] == SIN_CONFIRMAR:
            r["detalle"] = (f"{NOMBRE[canal]} aceptó la llamada y todavía la enseña en {r['despues']}; "
                            "vuelve a consultar en un minuto")
        if r["resultado"] in (APAGADA, YA_APAGADA):
            # También la que EN VIVO ya estaba apagada (el censo iba atrás): si no,
            # la lista la seguiría enseñando «a la venta» hasta el siguiente censo.
            # `_reflejar` sólo escribe si el estado cambió.
            _reflejar(canal, r["sku"], r["listing_id"], r["despues"])
        if r["resultado"] in (APAGADA, YA_APAGADA) and r.get("listing_id"):
            # Lo que una persona manda apagar se queda apagado: el seguro la suelta.
            r["seguro_soltado"] = _soltar_marca(canal, r["sku"], r["listing_id"])
        if r["resultado"] != SIN_PUBLICACION:
            _anotar(canal, r, lote, actor, time.time() - t0)
        resumen[r["resultado"]] = resumen.get(r["resultado"], 0) + 1
    apagadas = resumen.get(APAGADA, 0)
    if apagadas or resumen.get(ERROR) or resumen.get(SIN_CONFIRMAR):
        log.warning("LOTE %s · %s · %s pidió desactivar %d SKU(s): %s", lote, canal,
                    actor or "?", len(skus), resumen)
    return {"canal": canal, "nombre": NOMBRE[canal], "lote": lote, "pedidos": len(skus),
            "resumen": resumen, "resultados": resultados,
            "segundos": round(time.time() - t0, 1)}
