"""
pricing_bitacora.py — Los movimientos del panel pasan a la BITÁCORA del esquema `pricing`
(migración 0074, v0.671.0): quién movió qué precio u oferta, por qué, qué veía en pantalla,
cómo estaba antes y cómo quedó (v0.673.0, pedido de Brandon 20-oct-2026).

DE DÓNDE SALE
    Cada movimiento ya deja su fila técnica en `ops.process_log` (proceso `precio` u `ofertas`,
    origen `pricing`): los pasos y respuestas de ML. Esa fila es la fuente; de ella se escribe:

      pricing.accion              quién (actor, firmado por la sesión), cuándo, desde dónde, qué
                                  (tipo), por qué (motivo del catálogo + texto), con qué umbrales y
                                  foto veía la pantalla, y cómo terminó (estado). `process_log_id`
                                  apunta a la fila técnica y es ÚNICO: escribir dos veces la misma
                                  fila no duplica nada.
      pricing.accion_publicacion  la publicación: cómo estaba (antes), qué se pidió, qué recomendaba
                                  la pantalla, qué veía (contexto plano), cómo quedó y su resultado.
      pricing.precio_obs          el precio ANTES (si la última observación no lo tenía) y el
                                  DESPUÉS, con fuente `panel` y la acción que lo produjo.
      pricing.oferta (+promocion) el descuento individual que se quitó y el nuevo, y las campañas
                                  y relámpagos que se tomaron o quitaron, con la acción.

    `registrar()` se llama justo después de anotar en ops.process_log (en el mismo hilo); si kubera o
    una regla del esquema lo rechazan, el movimiento en ML ya está hecho y no se toca: queda en el
    log y `rellenar()` lo vuelve a intentar (al arrancar el backend y tras cada movimiento nuevo).
    Así también entró el cambio del 19-oct (ORG-0448-VER, el primero hecho desde el panel).

LAS REGLAS QUE MANDA EL ESQUEMA (triggers de la 0074)
    · Nada se borra; lo medido no se reescribe; el estado solo avanza
      (pedida → aplicada | parcial | error; aplicada | parcial → deshecha).
    · Un «deshacer» apunta a la acción que regresa (deshace_id) y, al quedar aplicado, la original
      pasa a «deshecha» y su publicación a «regresada».
    · El motivo es del catálogo `pricing.motivo`; «otro» exige texto.

POR QUÉ UNA SENTENCIA POR PASO
    El pool de kubera (SteadyDB) puede cambiar de conexión entre sentencias de una misma
    transacción sin avisar (memoria steadydb-reconecta-a-media-transaccion). La acción y su
    publicación nacen en UNA sentencia (CTE), así que o quedan las dos o ninguna; lo demás son
    sentencias sueltas que se pueden repetir sin duplicar.

PRECIO BASE = el tachado que muestra ML (`sale_price.regular_amount`), no `item.price`: con un
descuento activo ML deja `price` en el valor viejo (ORG-0448-VER: price $108, tachado $350).

Todo es BLOQUEANTE (kubera): se llama en un hilo (regla 11).
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("omnicanal.pricing.bitacora")

CDMX = timezone(timedelta(hours=-6))
CANAL = "mercado_libre"
PROCESOS = ("precio", "ofertas")
ORIGEN_LOG = "pricing"
TIPO = {("precio", "aplicar"): "editar_precio", ("precio", "deshacer"): "deshacer",
        ("ofertas", "tomar"): "entrar_campana", ("ofertas", "quitar"): "salir_campana"}
MOTIVO_CODIGO = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
CLAVE_RECO = re.compile(r"^[a-z_]{2,40}$")
TIPOS_OFERTA = {"PRICE_DISCOUNT", "SELLER_CAMPAIGN", "DEAL", "LIGHTNING", "DOD", "SMART", "PRICE_MATCHING",
                "UNHEALTHY_STOCK", "MARKETPLACE_CAMPAIGN", "PRE_NEGOTIATED"}


# ── PURO ─────────────────────────────────────────────────────────────────────

def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x >= 0 else None


def _fecha(s: Any) -> datetime | None:
    """Fechas de ML: con zona o sin ella (sin zona = hora de CDMX)."""
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=CDMX)
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=CDMX)


def base_de(estado: dict | None) -> float | None:
    """PURA. El precio normal que ve el comprador: el tachado de ML; si no hay, `item.price`."""
    if not estado:
        return None
    return _num(estado.get("tachado")) or _num(estado.get("normal")) or _num(estado.get("precio"))


def contexto_plano(ctx: Any) -> dict | None:
    """PURA. El esquema pide un objeto PLANO (sin objetos ni listas) de ≤ 16 KB: se queda con los escalares."""
    if not isinstance(ctx, dict):
        return None
    out: dict[str, Any] = {}
    for k, v in list(ctx.items())[:80]:
        if not isinstance(k, str) or len(k) > 60:
            continue
        if isinstance(v, bool) or v is None or isinstance(v, str):
            out[k] = v if not isinstance(v, str) else v[:500]
        elif isinstance(v, (int, float)):
            out[k] = v if math.isfinite(v) else None
    return out or None


def _promos_vivas(estado: dict | None, tipos: set[str] | None = None) -> list[dict]:
    return [p for p in (estado or {}).get("promos") or []
            if p.get("estado") in ("started", "pending") and (tipos is None or p.get("tipo") in tipos)]


def _llave(p: dict) -> tuple:
    return (p.get("tipo"), p.get("id"), (_fecha(p.get("inicio")) or datetime.min.replace(tzinfo=CDMX)).isoformat(),
            round(_num(p.get("precio")) or 0, 2))


def plan(fila: dict) -> dict | None:
    """
    PURA. De una fila de ops.process_log, lo que va a la bitácora. None = no es un movimiento de pricing.

    Devuelve: tipo, cuenta, item, estado (de la acción), resultado (de la publicación), error, antes y
    después (base y cobrado), lo pedido, el contexto, y las ofertas que empezaron o terminaron.
    """
    tipo = TIPO.get((fila.get("proceso"), fila.get("accion")))
    d = fila.get("detalle")
    if isinstance(d, str):
        d = json.loads(d)
    if not tipo or not isinstance(d, dict) or not d.get("item") or not d.get("cuenta"):
        return None
    b = d.get("bitacora") if isinstance(d.get("bitacora"), dict) else {}
    est_log = fila.get("estado")
    terminada = _fecha(fila.get("created_at")) or datetime.now(CDMX)
    iniciada = _fecha(b.get("iniciada")) or (terminada - timedelta(seconds=float(fila.get("duracion_s") or 0)))
    if iniciada > terminada:
        iniciada = terminada
    error = None
    pedido: dict[str, Any] = {}
    antes = despues = None
    empiezan: list[dict] = []
    terminan: list[dict] = []

    if tipo in ("editar_precio", "deshacer"):
        if tipo == "editar_precio":
            antes, despues = d.get("antes"), d.get("despues")
            pe = d.get("pedido") or {}
            pct = _num(pe.get("pct")) or 0
            base_p = _num(pe.get("precio"))
            pedido = {"base": base_p, "pct": pct / 100 if pct else None,
                      "cobrado": round(base_p * (1 - pct / 100), 2) if base_p else None,
                      "hasta": pe.get("hasta"), "salir": pe.get("salir")}
        else:
            antes, despues = d.get("ahora"), d.get("despues")
            obj = d.get("antes_del_cambio") or {}
            pedido = {"base": base_de(obj), "cobrado": _num(obj.get("cobra"))}
        if est_log == "ok":
            estado, resultado = "aplicada", "ok"
        elif est_log == "parcial":
            estado, resultado = "parcial", "parcial"
            error = "; ".join(d.get("avisos") or []) or "Quedó a medias."
        else:
            # error del editor: d["resultado"] lo dice desde v0.673.0; antes, sin «despues» nada quedó.
            r = d.get("resultado") or ("error" if not despues else "parcial")
            estado = "error" if r in ("error", "regresada") else "parcial"
            resultado = r
            error = str(d.get("error") or "Falló en Mercado Libre.")
        # Ofertas: el descuento individual que cambió y las campañas quitadas o re-metidas.
        if despues and estado in ("aplicada", "parcial"):
            va = {_llave(p): p for p in _promos_vivas(antes, {"PRICE_DISCOUNT"})}
            vd = {_llave(p): p for p in _promos_vivas(despues, {"PRICE_DISCOUNT"})}
            terminan += [p for k, p in va.items() if k not in vd]
            empiezan += [p for k, p in vd.items() if k not in va]
            if tipo == "editar_precio":
                terminan += [p for p in d.get("quitadas") or [] if p.get("id")]
            else:
                empiezan += [p for p in d.get("vueltas") or [] if p.get("id")]
    else:
        a, de = d.get("antes") or {}, d.get("despues") or {}
        antes = {"cobra": a.get("cobra"), "tachado": a.get("tachado"), "precio": a.get("normal"), "entrada": a.get("entrada")}
        despues = {"cobra": de.get("cobra"), "tachado": de.get("tachado"), "precio": de.get("normal"), "entrada": de.get("entrada")}
        cuerpo = d.get("cuerpo") or {}
        pedido = {"cobrado": _num(cuerpo.get("deal_price")), "promocion": d.get("promo_id"), "tipo_oferta": d.get("tipo"),
                  "stock": cuerpo.get("stock")}
        if est_log == "ok":
            estado, resultado = "aplicada", "ok"
        elif est_log == "sin_confirmar":
            estado, resultado = "parcial", "parcial"
            error = "ML aceptó, pero no se vio el cambio a tiempo."
        else:
            estado, resultado = "error", "error"
            error = str(d.get("error") or "Mercado Libre no aceptó.")
        if estado != "error":
            e = (de.get("entrada") if tipo == "entrar_campana" else a.get("entrada")) or {}
            oferta = {"tipo": d.get("tipo"), "id": d.get("promo_id"), "nombre": e.get("name"),
                      "precio": _num(e.get("price")) or pedido["cobrado"], "oferta_canal_id": e.get("ref_id"),
                      "inicio": de.get("inicio") or e.get("start_date"), "fin": de.get("fin") or e.get("finish_date")}
            (empiezan if tipo == "entrar_campana" else terminan).append(oferta)

    return {
        "tipo": tipo, "cuenta": d["cuenta"], "item": d["item"], "sku": fila.get("sku"),
        "process_log_id": fila.get("id"), "actor": fila.get("actor") or b.get("actor"),
        "deshace_log_id": d.get("deshace") if tipo == "deshacer" else None,
        "iniciada": iniciada, "terminada": terminada, "estado": estado, "resultado": resultado,
        "error": (error or "")[:1000] or None,
        "antes_base": base_de(antes), "antes_cobrado": _num((antes or {}).get("cobra")),
        "despues_base": base_de(despues) if despues else None,
        "despues_cobrado": _num((despues or {}).get("cobra")) if despues else None,
        "pedido": pedido, "antes": antes, "empiezan": empiezan, "terminan": terminan,
        "motivo": b.get("motivo") if MOTIVO_CODIGO.match(str(b.get("motivo") or "")) else None,
        "motivo_texto": (b.get("motivo_texto") or "")[:2000] or None,
        "recomendacion": b.get("recomendacion") if CLAVE_RECO.match(str(b.get("recomendacion") or "")) else None,
        "precio_recomendado": _num(b.get("precio_recomendado")),
        "umbrales": b.get("umbrales") if isinstance(b.get("umbrales"), dict) else None,
        "contexto": contexto_plano(b.get("contexto")),
        "foto": _fecha(b.get("foto")),
        "pantalla": (b.get("pantalla") or "pricing")[:80],
        "quitadas": [{"id": p.get("id"), "tipo": p.get("tipo"), "precio": _num(p.get("precio"))}
                     for p in (d.get("quitadas") or [])][:20] or None,
    }


# ── ESCRITURA ────────────────────────────────────────────────────────────────

_SQL_ACCION = """
with cta as (
    select a.id from core.accounts a where a.channel_id = %(canal)s and a.legacy_code = %(cuenta)s
), nueva as (
    insert into pricing.publicacion (account_id, listing_id)
    select cta.id, %(item)s from cta
    on conflict (account_id, listing_id) do nothing
    returning id
), pub as (
    select id from nueva
    union all
    select p.id from pricing.publicacion p join cta on cta.id = p.account_id where p.listing_id = %(item)s
    limit 1
), acc as (
    insert into pricing.accion (iniciada_at, terminada_at, foto_generado, deshace_id, process_log_id, actor, actor_rol,
                                registrada_por, origen_app, pantalla, tipo, estado, motivo, motivo_texto, error, umbrales)
    select %(iniciada)s, %(terminada)s, %(foto)s, %(deshace_id)s, %(process_log_id)s, %(actor)s, 'admin',
           coalesce(nullif(current_setting('app.usuario', true), ''), %(registra)s), 'panel', %(pantalla)s, %(tipo)s,
           %(estado)s, %(motivo)s, %(motivo_texto)s, %(error)s, %(umbrales)s::jsonb
      from pub
    on conflict (process_log_id) where process_log_id is not null do nothing
    returning id
)
insert into pricing.accion_publicacion (
    accion_id, publicacion_id, hasta_pedido, precio_base_antes, precio_cobrado_antes, precio_base_pedido,
    descuento_pct_pedido, precio_cobrado_pedido, precio_recomendado, precio_base_despues, precio_cobrado_despues,
    salir_campanas, recomendacion_clave, resultado, error, antes, pedido, promos_quitadas, contexto)
select acc.id, pub.id, %(hasta)s, %(antes_base)s, %(antes_cobrado)s, %(pedido_base)s, %(pedido_pct)s, %(pedido_cobrado)s,
       %(precio_recomendado)s, %(despues_base)s, %(despues_cobrado)s, %(salir)s, %(recomendacion)s, %(resultado)s,
       %(error_pub)s, %(antes)s::jsonb, %(pedido)s::jsonb, %(quitadas)s::jsonb, %(contexto)s::jsonb
  from acc, pub
returning accion_id, publicacion_id
"""

_SQL_DESHECHA = """
with ap as (
    update pricing.accion_publicacion set resultado = 'regresada'
     where accion_id = %(orig)s and publicacion_id = %(pub)s and resultado in ('ok', 'parcial')
    returning 1
)
update pricing.accion set estado = 'deshecha'
 where id = %(orig)s and estado in ('aplicada', 'parcial')
   and exists (select 1 from pricing.accion x where x.deshace_id = %(orig)s and x.estado in ('aplicada', 'parcial'))
"""

# Una observación de precio solo si cambió contra la última anterior (la primera es el punto de partida).
_SQL_PRECIO = """
insert into pricing.precio_obs (publicacion_id, desde, desde_min, accion_id, precio_base, precio_cobrado, fuente, origen)
select %(pub)s, %(desde)s,
       case when %(fuente)s = 'panel' then null
            else (select max(o.desde) from pricing.precio_obs o where o.publicacion_id = %(pub)s and o.desde < %(desde)s) end,
       %(accion)s, %(base)s, %(cobrado)s, %(fuente)s, %(origen)s
 where not exists (
        select 1 from (select o.precio_base, o.precio_cobrado from pricing.precio_obs o
                        where o.publicacion_id = %(pub)s and o.desde <= %(desde)s order by o.desde desc limit 1) u
         where u.precio_base = %(base)s and u.precio_cobrado = %(cobrado)s)
on conflict (publicacion_id, desde) do nothing
"""

_SQL_PROMOCION = """
insert into pricing.promocion (promo_id, canal, tipo, nombre, account_id, inicio, fin)
select %(promo_id)s, %(canal)s, %(tipo)s, %(nombre)s,
       case when %(propia)s then (select a.id from core.accounts a where a.channel_id = %(canal)s and a.legacy_code = %(cuenta)s) end,
       %(inicio)s, %(fin)s
on conflict (canal, promo_id) do update set visto_ultimo_at = greatest(pricing.promocion.visto_ultimo_at, now())
returning id
"""

# Que empieza: una oferta nueva (si ya estaba con el mismo inicio, se le atribuye el alta si no la tenía).
_SQL_EMPIEZA = """
insert into pricing.oferta (publicacion_id, promocion_id, inicio, fin, accion_id, tipo, oferta_canal_id, inicio_fuente, origen)
values (%(pub)s, %(promocion)s, %(inicio)s, %(fin)s, %(accion)s, %(tipo)s, %(canal_id)s, %(inicio_fuente)s, 'panel')
on conflict (publicacion_id, tipo, promocion_id, inicio) do update
   set origen = 'panel', accion_id = excluded.accion_id
 where pricing.oferta.accion_id is null and pricing.oferta.origen = 'desconocido' and pricing.oferta.fin_accion_id is null
"""

# Que termina: se cierra la abierta de esa publicación y promoción; si no estaba (nació antes de la
# bitácora), se da de alta ya cerrada para que quede quién la terminó. Una programada que se quita
# antes de empezar (relámpago, Buen Fin) se cierra en su propio inicio: duró cero.
_SQL_TERMINA_ABIERTA = """
update pricing.oferta set fin = greatest(%(fin)s, inicio), fin_origen = 'panel', fin_accion_id = %(accion)s
 where id = (select o.id from pricing.oferta o
              where o.publicacion_id = %(pub)s and o.tipo = %(tipo)s and o.promocion_id is not distinct from %(promocion)s
                and o.fin_accion_id is null and (o.fin is null or o.fin > %(fin)s)
                and (%(inicio)s::timestamptz is null or o.inicio is not distinct from %(inicio)s::timestamptz)
              order by o.inicio desc nulls last limit 1)
"""
_SQL_TERMINA_NUEVA = """
insert into pricing.oferta (publicacion_id, promocion_id, inicio, fin, fin_accion_id, tipo, oferta_canal_id, inicio_fuente,
                            origen, fin_origen)
values (%(pub)s, %(promocion)s, %(inicio)s, %(fin)s, %(accion)s, %(tipo)s, %(canal_id)s, %(inicio_fuente)s, 'desconocido', 'panel')
on conflict (publicacion_id, tipo, promocion_id, inicio) do nothing
"""


def _j(v: Any) -> str | None:
    return json.dumps(v, ensure_ascii=False, default=str) if v is not None else None


def _antes_corto(antes: dict | None) -> dict | None:
    """Lo que deshacer necesita de «antes», sin lo que no aporta, para quedar holgado bajo los 16 KB."""
    if not isinstance(antes, dict):
        return None
    out = {k: antes.get(k) for k in ("precio", "tachado", "cobra", "stock", "status", "por", "entrada") if antes.get(k) is not None}
    promos = [{k: p.get(k) for k in ("tipo", "id", "nombre", "estado", "precio", "inicio", "fin")} for p in antes.get("promos") or []
              if p.get("estado") in ("started", "pending")]
    if promos:
        out["promos"] = promos[:30]
    return out or None


def _accion_de_log(sdb, process_log_id: Any) -> dict | None:
    if not process_log_id:
        return None
    return sdb.fetch_one("select id, estado from pricing.accion where process_log_id = %s", (process_log_id,))


def registrar(fila: dict, registra: str = "automatico") -> int | None:
    """
    BLOQUEANTE (kubera). Pasa UNA fila de ops.process_log a la bitácora. Devuelve el id de la acción
    (la que ya existía si esa fila ya se había pasado). None si no es un movimiento de pricing.
    `registra` = quién lo anota cuando no hay sesión (relleno al arrancar).
    """
    from services import supabase_db as sdb

    p = plan(fila)
    if not p:
        return None
    ya = _accion_de_log(sdb, p["process_log_id"])
    if ya:
        return ya["id"]
    deshace_id = None
    if p["tipo"] == "deshacer":
        orig = _accion_de_log(sdb, p["deshace_log_id"])
        if orig is None and p["deshace_log_id"]:
            o = sdb.fetch_one("select * from ops.process_log where id = %s", (p["deshace_log_id"],))
            if o:
                registrar(dict(o), registra)
                orig = _accion_de_log(sdb, p["deshace_log_id"])
        if orig and orig["estado"] in ("aplicada", "parcial"):
            deshace_id = orig["id"]
        else:
            # Sin una acción deshacible (ya deshecha o no registrada): se guarda como corrección, no se pierde.
            p["tipo"] = "otro"
            p["motivo"] = p["motivo"] or "correccion"
            p["motivo_texto"] = p["motivo_texto"] or f"Deshacer desde PRICING (fila {p['deshace_log_id']} de ops.process_log)."
    if p["motivo"] == "otro" and not p["motivo_texto"]:
        p["motivo_texto"] = "Sin texto."
    pe = p["pedido"]
    fila_ap = sdb.execute_returning(_SQL_ACCION, {
        "canal": CANAL, "cuenta": p["cuenta"], "item": p["item"],
        "iniciada": p["iniciada"], "terminada": p["terminada"], "foto": p["foto"], "deshace_id": deshace_id,
        "process_log_id": p["process_log_id"], "actor": p["actor"] or "automatico", "registra": registra,
        "pantalla": p["pantalla"], "tipo": p["tipo"], "estado": p["estado"], "motivo": p["motivo"],
        "motivo_texto": p["motivo_texto"], "error": p["error"] if p["estado"] in ("error", "parcial") else None,
        "umbrales": _j(p["umbrales"]),
        "hasta": pe.get("hasta"), "antes_base": p["antes_base"], "antes_cobrado": p["antes_cobrado"],
        "pedido_base": pe.get("base"), "pedido_pct": pe.get("pct"), "pedido_cobrado": pe.get("cobrado"),
        "precio_recomendado": p["precio_recomendado"], "despues_base": p["despues_base"],
        "despues_cobrado": p["despues_cobrado"], "salir": pe.get("salir"), "recomendacion": p["recomendacion"],
        "resultado": p["resultado"], "error_pub": p["error"] if p["resultado"] != "ok" else None,
        "antes": _j(_antes_corto(p["antes"])),
        "pedido": _j({k: v for k, v in pe.items() if k in ("promocion", "tipo_oferta", "stock") and v is not None} or None),
        "quitadas": _j(p["quitadas"]), "contexto": _j(p["contexto"]),
    })
    if not fila_ap:
        ya = _accion_de_log(sdb, p["process_log_id"])
        return ya["id"] if ya else None
    acc, pub = fila_ap["accion_id"], fila_ap["publicacion_id"]
    if deshace_id:
        _paso(sdb.execute, "deshecha", _SQL_DESHECHA, {"orig": deshace_id, "pub": pub})
    if p["estado"] in ("aplicada", "parcial"):
        _precios(sdb, p, acc, pub)
        _ofertas(sdb, p, acc, pub)
    return acc


def _paso(fn, nombre: str, sql: str, params: dict):
    """Un paso que no es la acción misma: si falla, se avisa y la acción ya registrada se queda."""
    try:
        return fn(sql, params)
    except Exception as exc:  # noqa: BLE001
        log.warning("bitácora pricing: el paso «%s» falló (%s): %s", nombre, params.get("pub"), exc)
        return None


def _precios(sdb, p: dict, acc: int, pub: int) -> None:
    if p["antes_base"] is not None and p["antes_cobrado"] is not None:
        # Lo leído antes de actuar; siempre antes del «después» (en el relleno las dos horas pueden coincidir).
        desde = min(p["iniciada"], p["terminada"] - timedelta(seconds=1))
        _paso(sdb.execute, "precio antes", _SQL_PRECIO, {
            "pub": pub, "desde": desde, "accion": None, "base": p["antes_base"], "cobrado": p["antes_cobrado"],
            "fuente": "monitor", "origen": "desconocido"})
    if p["despues_base"] is not None and p["despues_cobrado"] is not None:
        _paso(sdb.execute, "precio después", _SQL_PRECIO, {
            "pub": pub, "desde": p["terminada"], "accion": acc, "base": p["despues_base"], "cobrado": p["despues_cobrado"],
            "fuente": "panel", "origen": "panel"})


def _promocion(sdb, p: dict, o: dict) -> int | None:
    if not o.get("id"):
        return None
    fila = _paso(sdb.execute_returning, "promoción", _SQL_PROMOCION, {
        "promo_id": str(o["id"])[:80], "canal": CANAL, "tipo": o.get("tipo"), "nombre": (o.get("nombre") or None),
        "propia": str(o["id"]).startswith("C-"), "cuenta": p["cuenta"],
        "inicio": None, "fin": None})
    return fila["id"] if fila else None


def _ofertas(sdb, p: dict, acc: int, pub: int) -> None:
    for o in p["terminan"]:
        if o.get("tipo") not in TIPOS_OFERTA:
            continue
        promo = _promocion(sdb, p, o)
        datos = {"pub": pub, "promocion": promo, "tipo": o["tipo"], "fin": p["terminada"], "accion": acc,
                 "inicio": _fecha(o.get("inicio")), "canal_id": (o.get("oferta_canal_id") or None),
                 "inicio_fuente": "canal" if o.get("inicio") else "lectura"}
        # Con campaña, la abierta se reconoce por la campaña: el inicio que da ML es el de la CAMPAÑA, no el de
        # esta publicación. Solo el descuento individual (sin id) se distingue por su inicio.
        n = _paso(sdb.execute, "oferta termina", _SQL_TERMINA_ABIERTA,
                  {**datos, "inicio": datos["inicio"] if o["tipo"] == "PRICE_DISCOUNT" else None})
        if not n:
            if datos["inicio"] is None:
                datos["inicio"] = p["iniciada"]
            if datos["inicio"] > p["terminada"]:
                datos["inicio"] = p["terminada"]   # programada que nunca empezó: dura cero
            _paso(sdb.execute, "oferta termina (alta)", _SQL_TERMINA_NUEVA, datos)
    for o in p["empiezan"]:
        if o.get("tipo") not in TIPOS_OFERTA:
            continue
        promo = _promocion(sdb, p, o)
        ini = _fecha(o.get("inicio"))
        # Campaña propia: ML da el inicio de la CAMPAÑA, no el de esta publicación; vale la hora del movimiento.
        if ini is None or (o.get("tipo") == "SELLER_CAMPAIGN" and ini < p["terminada"]):
            ini, fuente = p["terminada"], "lectura"
        else:
            fuente = "canal"
        fin = _fecha(o.get("fin"))
        if fin is not None and fin < ini:
            fin = None
        _paso(sdb.execute, "oferta empieza", _SQL_EMPIEZA, {
            "pub": pub, "promocion": promo, "inicio": ini, "fin": fin, "accion": acc, "tipo": o["tipo"],
            "canal_id": (o.get("oferta_canal_id") or None), "inicio_fuente": fuente})


def rellenar(limite: int = 200, registra: str = "relleno-bitacora") -> dict:
    """
    BLOQUEANTE (kubera). Pasa a la bitácora las filas de ops.process_log de PRICING que todavía no
    están (las de antes de v0.673.0 y las que fallaron al momento). En orden, para que un deshacer
    encuentre su original. Idempotente.
    """
    from services import supabase_db as sdb

    filas = sdb.fetch_all(
        """
        select l.* from ops.process_log l
         where l.origen = %s and l.proceso = any(%s)
           and l.accion in ('aplicar', 'deshacer', 'tomar', 'quitar')
           and not exists (select 1 from pricing.accion a where a.process_log_id = l.id)
         order by l.created_at, l.id
         limit %s
        """, (ORIGEN_LOG, list(PROCESOS), limite))
    hechas, fallas = 0, 0
    for f in filas:
        try:
            if registrar(dict(f), registra):
                hechas += 1
        except Exception as exc:  # noqa: BLE001 — una fila mala no detiene a las demás
            fallas += 1
            log.warning("bitácora pricing: no se pudo pasar la fila %s de ops.process_log: %s", f.get("id"), exc)
    if filas:
        log.info("bitácora pricing: relleno %s de %s (fallas %s)", hechas, len(filas), fallas)
    return {"pendientes": len(filas), "hechas": hechas, "fallas": fallas}


def motivos() -> list[dict]:
    """BLOQUEANTE (kubera, SELECT). El catálogo de motivos activos."""
    from services import supabase_db as sdb

    return [dict(r) for r in sdb.fetch_all(
        "select codigo, nombre, descripcion from pricing.motivo where activo order by (codigo = 'otro'), nombre")]


def tras_anotar(fila: dict | None) -> None:
    """
    BLOQUEANTE. Lo llaman los escritores de ops.process_log de PRICING (editor de precio y Ofertas) justo
    después de anotar: pasa ESA fila a la bitácora y, de paso, cualquiera que se haya quedado atrás.
    Nunca lanza: el movimiento en ML ya está hecho y su fila técnica ya quedó.
    """
    try:
        if fila:
            registrar(dict(fila))
    except Exception as exc:  # noqa: BLE001
        log.warning("bitácora pricing: la fila %s se queda para el relleno: %s", (fila or {}).get("id"), exc)
    try:
        rellenar(limite=20)
    except Exception as exc:  # noqa: BLE001
        log.warning("bitácora pricing: el relleno no corrió: %s", exc)


# ── MOTIVOS (lo que pide la pantalla) ────────────────────────────────────────

_motivos_cache: tuple[float, list[dict]] | None = None


def motivos_vigentes() -> list[dict]:
    """BLOQUEANTE. El catálogo, recordado 10 min (crece sin migración: un INSERT en pricing.motivo)."""
    import time

    global _motivos_cache
    if _motivos_cache and time.monotonic() - _motivos_cache[0] < 600:
        return _motivos_cache[1]
    xs = motivos()
    _motivos_cache = (time.monotonic(), xs)
    return xs


def validar_motivo(motivo: str | None, texto: str | None, codigos: set[str] | None) -> tuple[str, str | None]:
    """
    PURA. El motivo es obligatorio al mover un precio u oferta (es lo único que contesta «¿por qué se movió?»
    meses después). «otro» exige texto. `codigos` = el catálogo; None = no se pudo leer (se valida la forma).
    """
    from services.pricing_precio import ErrorPrecio

    m = (motivo or "").strip()
    t = (texto or "").strip() or None
    if not m:
        raise ErrorPrecio("Escoge el motivo del cambio.")
    if not MOTIVO_CODIGO.match(m) or (codigos is not None and m not in codigos):
        raise ErrorPrecio("Ese motivo no está en el catálogo.")
    if m == "otro" and not t:
        raise ErrorPrecio("Con «Otro», escribe el motivo.")
    if t and len(t) > 2000:
        raise ErrorPrecio("El texto del motivo es muy largo (máximo 2,000 caracteres).")
    return m, t
