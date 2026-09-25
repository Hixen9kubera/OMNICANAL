"""
fulfillment_full.py — CREAR FULL: la PLANEACIÓN SEMANAL de reabasto a los
almacenes de los marketplaces, POR TIENDA, y su orden en Odoo.

Es la PRIMERA acción de la pestaña FULLFILMENT (Brandon, 24-sep-2026: "como las
aplicaciones de banco: primero la transacción"). Desde v0.566.0 sigue el PROMPT
ESTÁNDAR de planeación semanal que le pasaron a Brandon (el texto completo vive
en `fulfillment_ia.PROMPT_ESTANDAR`): mismo cálculo, mismos estados, ganadores
agotados con reemplazo y las mismas alertas. Los números los calcula el código
—así no hay cifras inventadas—; la IA revisa y sugiere encima (`fulfillment_ia`).

═══════════════════════════════════════════════════════════════════════════════
LAS TIENDAS — cada una con SUS publicaciones (Brandon: "cada cuenta tiene unos
SKUs y la otra otros; casi el 80% son los mismos")
═══════════════════════════════════════════════════════════════════════════════
    meli:Kubera     FULL de Mercado Libre, cuenta BEKURA          destino meli_bekura
    meli:San Corpe  FULL de Mercado Libre, cuenta SANCORFASHION   destino meli_sank
    amazon          FBA de Amazon (San Corpe)                     destino fba
    walmart         WFS de Walmart                                destino wfs
Temu y TikTok NO entran: son únicamente DROP (Brandon, 24-sep). Lo que se queda en
bodega para ellos es el «colchón para DROP».

De dónde sale cada dato — EN VIVO siempre que se puede:
  · publicaciones: `channel.listings` (el sync de 15 min) y, para Mercado Libre,
    VERIFICADAS EN VIVO contra la API de ML (estado, stock en FULL y título) al
    armar la propuesta, al buscar y al revisar antes de crear. Walmart se lee en
    vivo de su API: su copia en kubera es del 17-ago.
  · ventas: `channel.sales_daily_completa` (las órdenes de cada cuenta; es lo
    mismo que los pedidos de Woo con `_ml_cuenta`, y kubera es la fuente desde el
    12-ago). El prompt pedía MySQL `canal_inventario` y `ml_progress`: están
    CONGELADOS desde el 13-ago y ya no dicen la verdad.
  · stock libre: Odoo `free_qty` por almacén (TEXCO, TEXCO II), releído en vivo
    antes de crear. Odoo es el MASTER.
  · medidas y piezas por caja: `costing.costos_validados`.

═══════════════════════════════════════════════════════════════════════════════
EL CÁLCULO (del prompt) — lo hace el navegador, `components/fulfillment/proponer.ts`
═══════════════════════════════════════════════════════════════════════════════
    velocidad_dia = ventas en la ventana / días de la ventana
    objetivo      = ⌈velocidad × cobertura⌉
    faltante      = objetivo − en el almacén del marketplace − en camino − en borradores
    enviar        = min(libre en Odoo, faltante)
UNA diferencia deliberada con el prompt: también se resta lo que YA va en camino y
los borradores. El prompt resta sólo el stock del almacén, y un envío que salió el
lunes y ML todavía no recibe se volvería a mandar el martes.

═══════════════════════════════════════════════════════════════════════════════
LA ESCRITURA — detrás del interruptor, APAGADA por omisión
═══════════════════════════════════════════════════════════════════════════════
Crear en Odoo es un flujo vivo (regla 3): interruptor `fulfillment_crear_full` de
`ops.automatizacion_flags`. Apagado, «Crear» contesta la vista previa exacta.
Encendido escribe SÓLO:
  · res.partner.create → el socio fijo de la tienda, una vez («FULL KUBERA»,
    «FULL SAN CORPE», «AMAZON FBA», «WFS WALMART»): dice de qué cuenta y
    marketplace es la orden, porque la API crea como José Enrique.
  · sale.order.create → la cotización en BORRADOR, una por tienda y almacén,
    precio 0 y sin impuestos. En MODO PRUEBA lleva «PRUEBA · NO CONFIRMAR NI
    SURTIR» en la referencia y la nota, y no se resta de la siguiente propuesta.
  · sale.order.write → SÓLO en órdenes creadas por el panel: el número del envío
    del marketplace (`client_order_ref`) y la guía en PDF («Subir guía»,
    `meli_etiqueta_file`, la convención de la casa).
NUNCA confirma: eso reserva stock y crea el picking; lo hace la KAM en Odoo.

TODO ESTO BLOQUEA (XML-RPC, psycopg2, httpx síncrono): el router lo corre en un
hilo (regla 11).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import settings
from services import fulfillment_envios as fenv
from services import odoo_ventas
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fulfillment_full")

# ── Las tiendas ─────────────────────────────────────────────────────────────
TIENDAS: dict[str, dict[str, Any]] = {
    "meli:Kubera": {"nombre": "ML Kubera", "canal": "meli", "cuenta": "Kubera", "codigo": "BEKURA",
                    "almacen": "FULL", "destino": "meli_bekura", "socio": "FULL KUBERA"},
    "meli:San Corpe": {"nombre": "ML San Corpe", "canal": "meli", "cuenta": "San Corpe",
                       "codigo": "SANCORFASHION", "almacen": "FULL", "destino": "meli_sank",
                       "socio": "FULL SAN CORPE"},
    "amazon": {"nombre": "Amazon FBA", "canal": "amazon", "cuenta": "San Corpe", "codigo": "AMAZON",
               "almacen": "FBA", "destino": "fba", "socio": "AMAZON FBA"},
    "walmart": {"nombre": "Walmart WFS", "canal": "walmart", "cuenta": None, "codigo": "WALMART",
                "almacen": "WFS", "destino": "wfs", "socio": "WFS WALMART"},
}
# Compatibilidad: la cuenta legible de ML → su tienda.
CUENTAS: dict[str, str] = {"Kubera": "BEKURA", "San Corpe": "SANCORFASHION"}
SOCIO: dict[str, str] = {t: d["socio"] for t, d in TIENDAS.items()}

ALMACENES: list[tuple[int, str]] = list(odoo_ventas._ALMACENES)

# Los supuestos de la corrida. El prompt los llama "PARÁMETROS DE ESTA CORRIDA".
PARAMETROS: dict[str, int] = {
    "cobertura_dias": 30,     # cuántos días de venta debe aguantar el almacén del marketplace
    "ventana_dias": 30,       # ventana de ventas para la velocidad
    "min_piezas": 5,          # menos que esto no justifica un renglón
    "min_ventas": 3,          # con menos ventas en la ventana el ritmo es ruido; y "ganador" = vendió ≥ esto
    "dejar_en_bodega": 0,     # colchón por SKU para los canales DROP (TikTok, Temu, Walmart S2H, web)
}
VENTANAS = (7, 14, 30, 60, 90)
TRANSITO_DIAS = 21
BORRADOR_DIAS = 21          # los borradores que se RESTAN de la planeación
BORRADOR_VISTA_DIAS = 90    # los que se ENSEÑAN en «órdenes sin completar» (Análisis)
PRUEBA_REF = "PRUEBA · NO CONFIRMAR NI SURTIR"

_CDMX = timezone(timedelta(hours=-6))
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_SIN_AVISOS = {"tracking_disable": True, "mail_create_nosubscribe": True,
               "mail_create_nolog": True, "mail_notrack": True}
_ML = "https://api.mercadolibre.com"


def _ts(etapa: dict | None) -> datetime | None:
    return datetime.fromisoformat(etapa["ts"]) if etapa and etapa.get("ts") else None


def _dia_txt(dt: datetime) -> str:
    local = dt.astimezone(_CDMX)
    return f"{local.day} {_MESES[local.month - 1]}"


def tienda_de_envio(e: dict[str, Any]) -> str | None:
    """La tienda de una salida de Odoo (la lectura de Envíos). None = no se sabe."""
    if e.get("canal") == "meli":
        return f"meli:{e['cuenta']}" if e.get("cuenta") in CUENTAS else None
    if e.get("canal") in ("amazon", "walmart"):
        return e["canal"]
    return None


def modelo_base(sku: str) -> str:
    """«TEC-0393-ROS» → «TEC-0393»: el MISMO MODELO en otro color o talla (prompt)."""
    partes = (sku or "").split("-")
    return "-".join(partes[:2]) if len(partes) >= 2 else (sku or "")


_PALABRAS_VACIAS = {"de", "la", "el", "los", "las", "con", "para", "por", "y", "en", "del", "un", "una",
                    "al", "a", "o", "sin", "tipo", "set", "kit", "pzs", "pz", "piezas", "pieza"}


def _palabras(texto: str | None) -> set[str]:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 2 and w not in _PALABRAS_VACIAS}


def _raiz(w: str) -> str:
    return w[:5] if len(w) > 5 else w


def parecido(a: str | None, b: str | None) -> float | None:
    """
    Palabras en común entre dos títulos (0 a 1), por RAÍZ: «flores»/«flor»,
    «masajeador»/«masaje» y «ramas»/«rama» cuentan como la misma. Medido el 24-sep
    contra Odoo: con palabras exactas 120 títulos «no coincidían» y casi todos eran
    el mismo producto dicho distinto; con raíces, 72. None si alguno no dice nada.
    """
    ra, rb = {_raiz(w) for w in _palabras(a)}, {_raiz(w) for w in _palabras(b)}
    if len(ra) < 2 or len(rb) < 2:
        return None
    comunes = sum(1 for x in ra if any(x == y or (len(x) >= 4 and len(y) >= 4 and (x.startswith(y) or y.startswith(x)))
                                       for y in rb))
    return comunes / min(len(ra), len(rb))


def _foto_ml(url: str | None) -> str | None:
    """La foto de la publicación: `thumbnail` viene en http y chica (-I); se pide en https y completa (-O)."""
    if not url:
        return None
    return re.sub(r"-I\.(jpe?g|png|webp)$", r"-O.\1", url.replace("http://", "https://"))


def medidas_sospechosas(m: dict[str, Any] | None) -> bool:
    """El prompt: caja master con peso ≤ 0.5 kg y medidas ~60×41×41."""
    if not m:
        return False
    try:
        dims = sorted([float(m.get("largo") or 0), float(m.get("ancho") or 0), float(m.get("alto") or 0)],
                      reverse=True)
        peso = float(m.get("peso")) if m.get("peso") is not None else None
    except (TypeError, ValueError):
        return False
    if peso is None or peso > 0.5:
        return False
    return abs(dims[0] - 60) <= 3 and abs(dims[1] - 41) <= 3 and abs(dims[2] - 41) <= 3


# ── El interruptor ──────────────────────────────────────────────────────────
_FLAG = "fulfillment_crear_full"
_TTL = 30.0
_cache: dict[str, Any] = {"valor": None, "ts": 0.0}


def habilitado(refrescar: bool = False) -> bool:
    """¿Puede «Crear FULL» escribir en Odoo? ⚠️ BLOQUEA al vencer el caché."""
    ahora = time.time()
    if refrescar or _cache["valor"] is None or (ahora - _cache["ts"]) > _TTL:
        fila = None
        try:
            fila = sdb.fetch_one(
                "select valor, motivo, actualizado_por, actualizado_at "
                "from ops.automatizacion_flags where flag = %(f)s", {"f": _FLAG})
        except Exception as exc:  # noqa: BLE001 — sin kubera manda la variable
            log.debug("crear FULL: interruptor no legible (%s)", exc)
        _cache.update(
            valor=bool(fila["valor"]) if fila else bool(getattr(settings, "fulfillment_crear_full", False)),
            ts=ahora, persistido=bool(fila),
            por=(fila or {}).get("actualizado_por"), motivo=(fila or {}).get("motivo"),
            cuando=(fila["actualizado_at"].isoformat() if fila and fila.get("actualizado_at") else None))
    return bool(_cache["valor"])


def estado_interruptor() -> dict[str, Any]:
    habilitado(refrescar=True)
    return {"encendido": bool(_cache["valor"]), "persistido": bool(_cache.get("persistido")),
            "actualizado_por": _cache.get("por"), "motivo": _cache.get("motivo"),
            "actualizado_at": _cache.get("cuando")}


def fijar_interruptor(encendido: bool, quien: str = "", motivo: str = "") -> dict[str, Any]:
    """Enciende o apaga la escritura en Odoo. Queda QUIÉN y POR QUÉ. Nunca lanza."""
    try:
        sdb.execute(
            """insert into ops.automatizacion_flags
                   (flag, valor, motivo, actualizado_at, actualizado_por)
               values (%(f)s, %(v)s, %(m)s, now(), %(q)s)
               on conflict (flag) do update set
                   valor = excluded.valor, motivo = excluded.motivo,
                   actualizado_at = now(), actualizado_por = excluded.actualizado_por""",
            {"f": _FLAG, "v": bool(encendido), "m": (motivo or "")[:300] or None,
             "q": (quien or "")[:120] or None})
        log.warning("Crear FULL en Odoo: %s por %s%s", "ENCENDIDO" if encendido else "APAGADO",
                    quien or "?", f" — {motivo}" if motivo else "")
        return {"ok": True, **estado_interruptor()}
    except Exception as exc:  # noqa: BLE001
        log.exception("no se pudo mover el interruptor de Crear FULL")
        return {**estado_interruptor(), "ok": False, "motivo": str(exc)[:300]}


# ── Lecturas de kubera ──────────────────────────────────────────────────────

# Ventas por tienda en la ventana COMPLETA (el día de hoy va a medias y se
# excluye). El prompt usa todas las ventas de la cuenta, FULL o no.
_SQL_VENTAS = """
    select canal, cuenta, sku::text sku,
           coalesce(sum(units_sold), 0)::int vv,
           coalesce(sum(units_sold) filter (where date >= %(d7)s), 0)::int v7,
           max(date) ultima
      from channel.sales_daily_completa
     where ((canal = 'mercado_libre' and cuenta in ('BEKURA', 'SANCORFASHION'))
            or (canal = 'amazon' and cuenta = 'AMAZON') or canal = 'walmart')
       and date >= %(desde)s and date < %(hoy)s
     group by 1, 2, 3
"""

# Lo PUBLICADO por tienda: Mercado Libre activa, pausada (FULL sin stock se pausa
# sola) o en revisión; Amazon todo lo que no esté borrado, inválido ni cerrado.
_SQL_PUBLICACIONES = """
    select a.legacy_code codigo, l.sku::text sku, l.listing_id, l.url, l.status, l.situacion,
           coalesce(l.is_fulfillment, false) en_almacen, l.logistic_type, l.category_id, l.product_type,
           coalesce(l.stock_full, 0)::int stock_full, coalesce(l.stock_fba, 0)::int stock_fba, l.price
      from channel.listings l join core.accounts a on a.id = l.account_id
     where (a.legacy_code in ('BEKURA', 'SANCORFASHION') and l.canal = 'mercado_libre'
            and l.situacion in ('active', 'paused', 'under_review'))
        or (a.legacy_code = 'AMAZON' and l.canal = 'amazon'
            and coalesce(l.status, '') not in ('DELETED', 'INVALID')
            and coalesce(l.situacion, '') <> 'closed')
"""

_SQL_NOMBRES = "select sku::text sku, name from core.products where sku::text = any(%(skus)s)"

_SQL_MEDIDAS = """
    select sku::text sku, largo, ancho, alto, peso, piezas_por_caja
      from costing.costos_validados where sku::text = any(%(skus)s)
"""


def _precio(valor: Any) -> float | None:
    """El precio de venta de la publicación en pesos. Walmart lo manda como
    `{"amount": …, "currency": "MXN"}`; ML y la copia del sync, como número."""
    if isinstance(valor, dict):
        valor = valor.get("amount")
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return round(n, 2) if n > 0 else None


def _por_sku(filas: list[dict], clave: str = "sku") -> dict[str, dict]:
    return {f[clave]: f for f in filas}


def _publicaciones() -> dict[str, dict[str, dict[str, Any]]]:
    """{tienda: {sku: publicación}}. Walmart viene de su API EN VIVO."""
    salida: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in TIENDAS}
    tienda_de = {d["codigo"]: t for t, d in TIENDAS.items()}
    for f in sdb.fetch_all(_SQL_PUBLICACIONES):
        t = tienda_de.get(f["codigo"])
        if not t:
            continue
        f["stock"] = f["stock_fba"] if t == "amazon" else f["stock_full"]
        f["categoria"] = f.get("category_id") or f.get("product_type")
        f["precio"] = _precio(f.get("price"))
        # Una publicación por SKU y cuenta (medido); si hubiera dos, gana la que
        # ya está en el almacén del marketplace.
        previa = salida[t].get(f["sku"])
        if not previa or (f["en_almacen"] and not previa["en_almacen"]):
            salida[t][f["sku"]] = f
    salida["walmart"] = _walmart_en_vivo()
    return salida


def _walmart_en_vivo() -> dict[str, dict[str, Any]]:
    """El catálogo de Walmart EN VIVO (su copia en kubera es del 17-ago). El stock
    de WFS no se puede leer (la API de inventario WFS contesta 401): va en null."""
    try:
        from services import walmart
        if not walmart.disponible():
            return {}
        items = asyncio.run(walmart.listar_items())
    except Exception as exc:  # noqa: BLE001 — sin Walmart la planeación sigue
        log.warning("planeación: Walmart no contestó (%s)", exc)
        return {}
    salida = {}
    for it in items:
        if str(it.get("publishedStatus") or "").upper() != "PUBLISHED":
            continue
        sku = str(it.get("sku") or "").strip()
        if sku:
            salida[sku] = {"sku": sku, "listing_id": it.get("wpid"), "url": None,
                           "situacion": str(it.get("lifecycleStatus") or "").lower() or "published",
                           "en_almacen": None, "categoria": it.get("productType"), "stock": None,
                           "titulo": it.get("productName"), "precio": _precio(it.get("price"))}
    return salida


# ── Mercado Libre EN VIVO ───────────────────────────────────────────────────

def _token_ml(codigo: str, renovar: bool = False) -> str | None:
    from services import meli
    return meli.refrescar_token(codigo) if renovar else meli._access_token(codigo)


def verificar_ml(codigo: str, listing_ids: list[str]) -> dict[str, dict[str, Any]]:
    """
    Estado, stock y título de cada publicación, preguntándole a Mercado Libre
    AHORA (`/items?ids=`, de 20 en 20). Sólo GET. Si ML no contesta, lo que no se
    pudo verificar simplemente no viene: el que llama lo marca «sin verificar».
    """
    ids = sorted({i for i in listing_ids if i})
    if not ids:
        return {}
    token = _token_ml(codigo)
    if not token:
        return {}
    campos = "id,status,sub_status,available_quantity,shipping,title,category_id,price,thumbnail"
    lotes = [ids[i:i + 20] for i in range(0, len(ids), 20)]

    def lote(grupo: list[str]) -> list[dict]:
        nonlocal token
        for intento in range(2):
            r = httpx.get(f"{_ML}/items", params={"ids": ",".join(grupo), "attributes": campos},
                          headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if r.status_code == 401 and intento == 0:
                token = _token_ml(codigo, renovar=True) or token
                continue
            r.raise_for_status()
            return r.json()
        return []

    salida: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for res in pool.map(lambda g: _seguro(lote, g), lotes):
            for x in res or []:
                if x.get("code") != 200:
                    continue
                b = x.get("body") or {}
                salida[b.get("id")] = {
                    "estado": b.get("status"), "sub_estado": b.get("sub_status") or [],
                    "stock": b.get("available_quantity"),
                    "logistica": (b.get("shipping") or {}).get("logistic_type"),
                    "titulo": b.get("title"), "categoria": b.get("category_id"),
                    "precio": b.get("price"),
                    "imagen": _foto_ml(b.get("thumbnail")),
                }
    return salida


def _seguro(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        log.warning("planeación: ML no contestó un lote (%s)", exc)
        return []


def buscar_ml_por_sku(codigo: str, sku: str) -> list[str]:
    """Las publicaciones de la cuenta con ese SKU, EN VIVO (`seller_sku`)."""
    from services import meli
    token = meli._access_token(codigo)
    usuario = {"BEKURA": "3072519654", "SANCORFASHION": "3064478475"}.get(codigo)
    if not token or not usuario:
        return []
    try:
        r = httpx.get(f"{_ML}/users/{usuario}/items/search", params={"seller_sku": sku},
                      headers={"Authorization": f"Bearer {token}"}, timeout=20)
        return list(r.json().get("results") or []) if r.status_code == 200 else []
    except Exception as exc:  # noqa: BLE001
        log.warning("planeación: búsqueda en vivo de %s falló (%s)", sku, exc)
        return []


# ── Odoo: borradores ────────────────────────────────────────────────────────

def _borradores(ahora: datetime) -> list[dict[str, Any]]:
    """Las cotizaciones a FULL/FBA/WFS que YA existen en Odoo (21 días)."""
    desde = (ahora - timedelta(days=BORRADOR_VISTA_DIAS)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ordenes = odoo_ventas._kw(
        "sale.order", "search_read",
        [[["state", "=", "draft"], ["create_date", ">=", desde],
          "|", "|", "|", ["partner_id.name", "=ilike", "full%"], ["partner_id.name", "=ilike", "mercado libre"],
          ["partner_id.name", "=ilike", "amazon%"], ["partner_id.name", "=ilike", "wfs%"]]],
        {"fields": ["name", "create_uid", "create_date", "partner_id", "client_order_ref", "origin",
                    "warehouse_id", "order_line"],
         "order": "create_date desc", "limit": 150})
    ids = [i for o in ordenes for i in (o.get("order_line") or [])]
    lineas = ({l["id"]: l for l in odoo_ventas._kw("sale.order.line", "read",
                                                   [ids, ["product_id", "product_uom_qty"]])}
              if ids else {})
    salida = []
    for o in ordenes:
        socio = fenv._nombre(o.get("partner_id"))
        renglones = []
        for lid in o.get("order_line") or []:
            ln = lineas.get(lid)
            if not ln or not ln.get("product_id"):
                continue
            completo = fenv._nombre(ln["product_id"])
            m = fenv._RE_SKU.match(completo)
            renglones.append({"sku": m.group(1) if m else completo, "cantidad": int(ln.get("product_uom_qty") or 0)})
        piezas = sum(r["cantidad"] for r in renglones)
        canal = fenv.clasificar_canal(socio, piezas)
        if canal is None:
            continue       # AMAZON con menos de 40 piezas: una venta MFN capturada a mano
        cuenta, regla = fenv.asignar_cuenta(canal, socio, fenv._id(o.get("create_uid")),
                                            fenv._nombre(o.get("create_uid")))
        tienda = tienda_de_envio({"canal": canal, "cuenta": cuenta})
        origen = str(o.get("origin") or "")
        creada = fenv._iso(o.get("create_date"))
        dias = _dias_desde(creada, ahora)
        salida.append({
            "id": o["id"], "orden": o["name"], "tienda": tienda, "cuenta": cuenta, "cuenta_regla": regla,
            "socio": socio, "kam": fenv.quien_armo(o), "creada": creada, "dias": dias,
            "referencia": o.get("client_order_ref") or None, "origen": origen or None,
            "almacen": fenv._nombre(o.get("warehouse_id")) or None,
            "panel": origen.startswith(fenv.ORIGEN_PANEL),
            "prueba": "PRUEBA" in origen.upper() or "PRUEBA" in str(o.get("client_order_ref") or "").upper(),
            "piezas": piezas, "skus": len(renglones), "lineas": renglones,
            "url": odoo_ventas.url_orden_publica().format(id=o["id"]),
        })
    return salida


# ── Funciones puras (se prueban sin Odoo, kubera ni ML) ─────────────────────

def _dias_desde(iso: str | datetime | None, ahora: datetime) -> int | None:
    """Días completos desde una fecha (con zona). None si no hay fecha."""
    if not iso:
        return None
    dt = iso if isinstance(iso, datetime) else datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (ahora - dt).days)


def se_resta(borrador: dict[str, Any]) -> bool:
    """Un borrador se resta de la planeación si es de una tienda, no es PRUEBA y tiene
    21 días o menos. Los más viejos se ENSEÑAN (Análisis) pero ya no se restan."""
    return (bool(borrador.get("tienda")) and not borrador.get("prueba")
            and (borrador.get("dias") or 0) <= BORRADOR_DIAS)


def salidas_abiertas(envios: list[dict[str, Any]], ahora: datetime) -> list[dict[str, Any]]:
    """
    Cada salida a FULL/FBA/WFS que existe en Odoo y bodega no ha validado, con
    CUÁNTO LLEVA sin completarse (Brandon, 24-sep). Las de más de 21 días son
    «olvidadas»: no cuentan como en camino y siguen reservando stock en Odoo.
    La más vieja primero.
    """
    salida = []
    for e in envios:
        tienda = tienda_de_envio(e)
        if not tienda or e.get("estado_odoo") == "done":
            continue
        creada = _ts(e["etapas"][0])
        dias = _dias_desde(creada, ahora)
        salida.append({"orden": e.get("orden"), "salida": e.get("salida"), "tienda": tienda,
                       "cuenta": e.get("cuenta"), "kam": e.get("kam"),
                       "creada": creada.isoformat() if creada else None, "dias": dias,
                       "piezas": int(e.get("pedidas") or 0),
                       "olvidada": dias is not None and dias > TRANSITO_DIAS})
    return sorted(salida, key=lambda x: -(x["dias"] or 0))


def en_camino(envios: list[dict[str, Any]],
              ahora: datetime) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """
    Lo que ya va hacia el almacén de cada tienda, por (tienda, SKU), con el porqué;
    y las salidas abiertas OLVIDADAS (más de 21 días), que se enseñan y no se restan.
    """
    camino: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"piezas": 0, "detalle": []})
    zombis: list[dict[str, Any]] = []
    for e in envios:
        tienda = tienda_de_envio(e)
        if not tienda:
            continue
        creada, salida = _ts(e["etapas"][0]), _ts(e["etapas"][1])
        lineas = e.get("lineas") or []
        if e.get("estado_odoo") != "done":
            if creada and creada < ahora - timedelta(days=TRANSITO_DIAS):
                zombis.append({"orden": e.get("orden"), "salida": e.get("salida"), "tienda": tienda,
                               "cuenta": e.get("cuenta"), "creada": creada.isoformat(),
                               "piezas": int(e.get("pedidas") or 0)})
                continue
            for r in lineas:
                n = int(r.get("pedidas") or 0)
                if n > 0:
                    k = (tienda, r["sku"])
                    camino[k]["piezas"] += n
                    camino[k]["detalle"].append(f"{e.get('orden')} por validar en Odoo ({n})")
            continue
        c = e.get("cobertura") or {}
        if c.get("fuente") == "avisos":
            if c.get("cerrado"):
                continue            # lo que falta en un envío cerrado es rechazo, no camino
            for r in lineas:
                falta = int(r.get("enviadas") or 0) - int(r.get("llegadas") or 0)
                if falta > 0:
                    k = (tienda, r["sku"])
                    camino[k]["piezas"] += falta
                    camino[k]["detalle"].append(
                        f"{e.get('orden')} salió el {_dia_txt(salida)}: faltan {falta} por llegar"
                        if salida else f"{e.get('orden')}: faltan {falta} por llegar")
        elif salida and salida >= ahora - timedelta(days=TRANSITO_DIAS):
            # Sin avisos que la midan (FBA, WFS): lo enviado entero, 21 días.
            for r in lineas:
                n = int(r.get("enviadas") or 0)
                if n > 0:
                    k = (tienda, r["sku"])
                    camino[k]["piezas"] += n
                    camino[k]["detalle"].append(f"{e.get('orden')} salió el {_dia_txt(salida)} ({n}, sin avisos)")
    return dict(camino), zombis


def esta_semana(envios: list[dict[str, Any]], ahora: datetime) -> dict[str, dict[str, Any]]:
    """
    Lo que SE ESTÁ MANDANDO ESTA SEMANA por tienda (Brandon: "cuántas piezas se
    están mandando de cuántos SKUs de esta semana"): las salidas validadas en la
    semana ISO actual (CDMX) más las que están por validar (sin las olvidadas).
    """
    semana = ahora.astimezone(_CDMX).isocalendar()[:2]
    salida: dict[str, dict[str, Any]] = {}
    skus: dict[str, set[str]] = defaultdict(set)
    for e in envios:
        tienda = tienda_de_envio(e)
        if not tienda:
            continue
        creada, validada = _ts(e["etapas"][0]), _ts(e["etapas"][1])
        g = salida.setdefault(tienda, {"envios": 0, "piezas": 0, "skus": 0,
                                       "salieron": {"envios": 0, "piezas": 0},
                                       "por_validar": {"envios": 0, "piezas": 0}})
        if e.get("estado_odoo") == "done":
            if not validada or validada.astimezone(_CDMX).isocalendar()[:2] != semana:
                continue
            n = int(e.get("piezas") or 0)
            g["salieron"]["envios"] += 1
            g["salieron"]["piezas"] += n
            lineas = [r for r in e.get("lineas") or [] if (r.get("enviadas") or 0) > 0]
        else:
            if creada and creada < ahora - timedelta(days=TRANSITO_DIAS):
                continue
            n = int(e.get("pedidas") or 0)
            g["por_validar"]["envios"] += 1
            g["por_validar"]["piezas"] += n
            lineas = [r for r in e.get("lineas") or [] if (r.get("pedidas") or 0) > 0]
        g["envios"] += 1
        g["piezas"] += n
        skus[tienda].update(r["sku"] for r in lineas)
    for t, g in salida.items():
        g["skus"] = len(skus[t])
    return salida


def reemplazos_para(sku: str, tienda: str, pubs: dict[str, dict[str, Any]],
                    libre_total: dict[str, int], nombres: dict[str, str],
                    vendidas: dict[str, int] | None = None, tope: int = 3) -> list[dict[str, Any]]:
    """
    GANADOR SIN EXISTENCIA → su REEMPLAZO. El ganador no se puede surtir (0 libre en
    Odoo y 0 en el almacén); el reemplazo es el SIGUIENTE que SÍ VENDE en esa tienda
    y SÍ tiene libre en Odoo (Brandon, 24-sep: "su reemplazo debe ser el que sigue con
    ventas"). Orden del prompt: (1) el MISMO MODELO (otro color o talla), (2) la
    MISMA CATEGORÍA del marketplace; dentro de cada uno, el que más vende primero.
    Sólo lo YA PUBLICADO en esa tienda.
    """
    vendidas = vendidas or {}
    base = modelo_base(sku)
    categoria = (pubs.get(sku) or {}).get("categoria")

    def sirve(s: str) -> bool:
        return s != sku and libre_total.get(s, 0) > 0 and vendidas.get(s, 0) > 0

    def orden(s: str) -> tuple[int, int]:
        return (-vendidas.get(s, 0), -libre_total.get(s, 0))

    mismos = sorted((s for s in pubs if sirve(s) and modelo_base(s) == base), key=orden)
    misma_cat = sorted((s for s, p in pubs.items() if sirve(s) and s not in mismos and categoria
                        and p.get("categoria") == categoria), key=orden)
    salida = []
    for s, tipo in [(s, "mismo modelo") for s in mismos] + [(s, "misma categoría") for s in misma_cat]:
        salida.append({"sku": s, "nombre": nombres.get(s), "tipo": tipo, "libre": libre_total[s],
                       "vendio": vendidas[s], "precio": _precio(pubs[s].get("precio"))})
        if len(salida) >= tope:
            break
    return salida


def _fila(tienda: str, sku: str, venta: dict | None, pub: dict | None, vivo: dict | None,
          producto: dict | None, lib: dict | None, cam: dict | None, bor: dict | None,
          nombre: str | None, medidas: dict | None) -> dict[str, Any]:
    """Un renglón de la planeación: los INSUMOS. La cantidad la decide proponer.ts."""
    stock = pub.get("stock") if pub else None
    if vivo and vivo.get("logistica") == "fulfillment" and vivo.get("stock") is not None:
        # ML en vivo manda sobre la copia del sync: el `available_quantity` de una
        # publicación FULL es lo que hay en FULL ahora. De una que NO es FULL es el
        # stock propio del vendedor, así que ahí se queda lo del sync (0 en FULL).
        stock = int(vivo["stock"])
    titulo = (vivo or {}).get("titulo") or (pub or {}).get("titulo")
    precio = _precio((vivo or {}).get("precio")) or _precio((pub or {}).get("precio"))
    alertas = []
    if pub and not (pub.get("categoria") or (vivo or {}).get("categoria")):
        alertas.append("sin_categoria")
    # El título del marketplace contra el nombre de ODOO (Brandon, 24-sep); sin
    # producto en Odoo, contra el de Omnicanal.
    nombre_odoo = (producto or {}).get("name")
    p = parecido(titulo, nombre_odoo or nombre)
    if p is not None and p < 0.15:
        alertas.append("reciclado")
    if medidas_sospechosas(medidas):
        alertas.append("medidas")
    estado_vivo = (vivo or {}).get("estado")
    if estado_vivo in ("closed", "inactive"):
        alertas.append("cerrada_en_ml")
    return {
        "tienda": tienda, "sku": sku, "nombre": nombre or (producto or {}).get("name"),
        "product_id": (producto or {}).get("id"),
        "publicada": pub is not None, "listing_id": (pub or {}).get("listing_id"), "url": (pub or {}).get("url"),
        "situacion": estado_vivo or (pub or {}).get("situacion"),
        "en_almacen": bool((vivo or {}).get("logistica") == "fulfillment") if vivo else (pub or {}).get("en_almacen"),
        "verificada": vivo is not None, "titulo_mkt": titulo, "nombre_odoo": nombre_odoo,
        # Las fotos sólo viajan donde hacen falta: en los que el título no coincide.
        "imagen_mkt": (vivo or {}).get("imagen") if "reciclado" in alertas else None,
        "parecido": round(p, 2) if p is not None and "reciclado" in alertas else None,
        # Si el título del marketplace TAMPOCO se parece al nombre del catálogo, es lo
        # primero que hay que revisar; si sí se parece, casi siempre es redacción (Odoo
        # trae nombres del proveedor, a veces en inglés), pero se enseña igual con foto.
        "titulo_urgente": ("reciclado" in alertas
                           and ((parecido(titulo, nombre) or 0) < 0.5) if nombre_odoo else None),
        "categoria": (vivo or {}).get("categoria") or (pub or {}).get("categoria"),
        "precio": precio,
        "vv": int((venta or {}).get("vv") or 0), "v7": int((venta or {}).get("v7") or 0),
        "ultima_venta": (venta or {}).get("ultima").isoformat() if (venta or {}).get("ultima") else None,
        "stock": stock,
        "en_camino": cam["piezas"] if cam else 0, "camino": cam["detalle"] if cam else [],
        "borrador": bor["piezas"] if bor else 0, "borradores": bor["detalle"] if bor else [],
        "libre": ({n: max(0, int((lib or {}).get(w, 0) or 0)) for w, n in ALMACENES} if producto else None),
        # Piezas por caja del packing list: menos de 1 no es una caja, es un hueco.
        "caja": (int(round(float(medidas["piezas_por_caja"])))
                 if medidas and medidas.get("piezas_por_caja") is not None
                 and float(medidas["piezas_por_caja"]) >= 1 else None),
        "alertas": alertas,
        "reemplazos": [],
    }


def armar_propuesta(tiendas: list[str], ventas: list[dict], pubs: dict[str, dict[str, dict]],
                    vivos: dict[str, dict[str, dict]], envios: list[dict], borradores: list[dict],
                    productos: dict[str, dict], libres: dict[int, dict[int, float]],
                    nombres: dict[str, str], medidas: dict[str, dict], ahora: datetime,
                    ventana: int) -> dict[str, Any]:
    """Los insumos de la planeación, por tienda y SKU. Función pura."""
    camino, zombis = en_camino(envios, ahora)
    borr: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"piezas": 0, "detalle": []})
    for b in borradores:
        # Las PRUEBAS no se restan (nadie las va a surtir) y los de más de 21 días tampoco.
        if not se_resta(b):
            continue
        for r in b["lineas"]:
            if r["cantidad"] > 0:
                k = (b["tienda"], r["sku"])
                borr[k]["piezas"] += r["cantidad"]
                borr[k]["detalle"].append(f"{b['orden']} ({r['cantidad']})")
    tienda_de = {("mercado_libre", "BEKURA"): "meli:Kubera", ("mercado_libre", "SANCORFASHION"): "meli:San Corpe",
                 ("amazon", "AMAZON"): "amazon"}
    ventas_por: dict[str, dict[str, dict]] = defaultdict(dict)
    for v in ventas:
        t = tienda_de.get((v["canal"], v["cuenta"])) or ("walmart" if v["canal"] == "walmart" else None)
        if t:
            ventas_por[t][v["sku"]] = v

    def libre_de(sku: str) -> int:
        p = productos.get(sku)
        return sum(max(0, int(libres.get(p["id"], {}).get(w, 0) or 0)) for w, _ in ALMACENES) if p else 0

    libre_total = {s: libre_de(s) for s in productos}
    filas: dict[str, list[dict[str, Any]]] = {}
    for t in tiendas:
        skus = set(ventas_por[t]) | {s for (tt, s) in camino if tt == t} | {s for (tt, s) in borr if tt == t}
        lista = []
        for sku in sorted(skus):
            pub = pubs.get(t, {}).get(sku)
            vivo = vivos.get(t, {}).get((pub or {}).get("listing_id")) if pub else None
            p = productos.get(sku)
            f = _fila(t, sku, ventas_por[t].get(sku), pub, vivo, p, libres.get(p["id"]) if p else None,
                      camino.get((t, sku)), borr.get((t, sku)), nombres.get(sku), medidas.get(sku))
            # Ganador agotado (el umbral de ventas lo aplica la pantalla): sin stock
            # libre y sin stock en el almacén del marketplace.
            if f["vv"] > 0 and p and libre_total.get(sku, 0) == 0 and (f["stock"] or 0) == 0:
                f["reemplazos"] = reemplazos_para(sku, t, pubs.get(t, {}), libre_total, nombres,
                                                  {s: int(v.get("vv") or 0) for s, v in ventas_por[t].items()})
            lista.append(f)
        lista.sort(key=lambda f: (-f["vv"], f["sku"]))
        filas[t] = lista

    local = ahora.astimezone(_CDMX)
    iso = local.isocalendar()
    lunes = (local - timedelta(days=local.weekday())).date()
    hoy = local.date()
    return {
        "generado": ahora.isoformat(),
        "semana": {"semana": f"S{iso[1]}", "anio": iso[0], "lunes": lunes.isoformat(),
                   "domingo": (lunes + timedelta(days=6)).isoformat()},
        "ventana": {"dias": ventana, "desde": (hoy - timedelta(days=ventana)).isoformat(),
                    "hasta": (hoy - timedelta(days=1)).isoformat()},
        "parametros": {**PARAMETROS, "ventana_dias": ventana},
        "almacenes": [n for _, n in ALMACENES],
        "tiendas": {t: {**{k: v for k, v in TIENDAS[t].items() if k != "codigo"},
                        "publicadas": len(pubs.get(t, {})),
                        "verificadas": len(vivos.get(t, {})),
                        "filas": filas.get(t, [])} for t in tiendas},
        "borradores": borradores,
        "zombis": zombis,
        "abiertas": salidas_abiertas(envios, ahora),
        "esta_semana": esta_semana(envios, ahora),
        "en_camino": {t: {"piezas": sum(v["piezas"] for (tt, _), v in camino.items() if tt == t),
                          "skus": sum(1 for (tt, _) in camino if tt == t)} for t in TIENDAS},
    }


def repartir_almacenes(lineas: list[dict[str, Any]],
                       libres: dict[int, dict[int, float]]) -> dict[str, Any]:
    """
    De qué almacén sale cada renglón. UNA ORDEN POR ALMACÉN, como las arman las KAM.
      0. Nada que no exista: cada renglón se topa a lo libre de los dos almacenes
         juntos, y el recorte SE DICE.
      1. Si un almacén SOLO cubre todo, una sola orden ahí (TEXCO primero).
      2. Si no, cada renglón va COMPLETO al primer almacén que lo cubra.
      3. Sólo si ninguno lo cubre solo, se parte (TEXCO primero).
    """
    def lib(ln: dict, wid: int) -> int:
        return max(0, int(libres.get(ln["product_id"], {}).get(wid, 0) or 0))

    recortes: list[dict[str, Any]] = []
    van: list[dict[str, Any]] = []
    for ln in lineas:
        total = sum(lib(ln, wid) for wid, _ in ALMACENES)
        pide = int(ln["cantidad"])
        n = min(pide, total)
        if n < pide:
            recortes.append({"sku": ln["sku"], "pedidas": pide, "van": n,
                             "porque": ("Odoo no tiene libre" if total == 0
                                        else f"sólo hay {total} libre{'s' if total != 1 else ''} en Odoo")})
        if n > 0:
            van.append({**ln, "cantidad": n})

    for wid, nombre in ALMACENES:
        if van and all(lib(ln, wid) >= ln["cantidad"] for ln in van):
            return {"partes": [{"almacen_id": wid, "almacen": nombre, "lineas": van}], "recortes": recortes}

    por: dict[int, list[dict[str, Any]]] = {wid: [] for wid, _ in ALMACENES}
    for ln in van:
        entero = next((wid for wid, _ in ALMACENES if lib(ln, wid) >= ln["cantidad"]), None)
        if entero is not None:
            por[entero].append(ln)
            continue
        falta = ln["cantidad"]
        for wid, _ in ALMACENES:
            toma = min(lib(ln, wid), falta)
            if toma > 0:
                por[wid].append({**ln, "cantidad": toma})
                falta -= toma
    partes = [{"almacen_id": wid, "almacen": nombre, "lineas": por[wid]}
              for wid, nombre in ALMACENES if por[wid]]
    return {"partes": partes, "recortes": recortes}


def repartir_entre_tiendas(pedidos: dict[str, list[dict[str, Any]]],
                           libres: dict[int, dict[int, float]]) -> tuple[dict[str, list[dict]], list[dict]]:
    """
    Si varias tiendas piden el MISMO producto y lo libre (releído ahora) no alcanza
    para todas, se reparte en proporción a lo que pidió cada una. Devuelve lo que
    le toca a cada tienda y los recortes por reparto (se dicen, no se esconden).
    """
    por_producto: dict[int, list[tuple[str, dict]]] = defaultdict(list)
    for t, lineas in pedidos.items():
        for ln in lineas:
            por_producto[ln["product_id"]].append((t, ln))
    ajustados: dict[str, list[dict]] = {t: [] for t in pedidos}
    recortes: list[dict] = []
    for pid, lista in por_producto.items():
        libre = sum(max(0, int(libres.get(pid, {}).get(w, 0) or 0)) for w, _ in ALMACENES)
        total = sum(int(ln["cantidad"]) for _, ln in lista)
        if len(lista) < 2 or total <= libre:
            for t, ln in lista:
                ajustados[t].append(ln)
            continue
        resto = libre
        for i, (t, ln) in enumerate(lista):
            parte = resto if i == len(lista) - 1 else (libre * int(ln["cantidad"])) // total
            resto -= parte
            if parte < int(ln["cantidad"]):
                recortes.append({"tienda": t, "sku": ln["sku"], "pedidas": int(ln["cantidad"]), "van": parte,
                                 "porque": f"las {len(lista)} tiendas lo piden y Odoo tiene {libre} libres"})
            ajustados[t].append({**ln, "cantidad": parte})
    return ajustados, recortes


def limpiar_lineas(lineas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """La solicitud tal como llega del navegador → un renglón por SKU, piezas > 0."""
    por_sku: dict[str, dict[str, Any]] = {}
    for ln in lineas or []:
        sku = str(ln.get("sku") or "").strip()
        try:
            n = int(ln.get("cantidad") or 0)
        except (TypeError, ValueError):
            n = 0
        if not sku or n <= 0:
            continue
        r = por_sku.setdefault(sku, {"sku": sku, "cantidad": 0, "sugerido": None})
        r["cantidad"] += n
        if ln.get("sugerido") is not None:
            r["sugerido"] = int(ln["sugerido"])
    return list(por_sku.values())


def limpiar_tiendas(tiendas: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """[{tienda, lineas}] → {tienda: lineas limpias}, sólo tiendas conocidas y con piezas."""
    salida: dict[str, list[dict[str, Any]]] = {}
    for t in tiendas or []:
        nombre = str(t.get("tienda") or "")
        if nombre not in TIENDAS:
            continue
        lineas = limpiar_lineas(t.get("lineas") or [])
        if lineas:
            salida.setdefault(nombre, []).extend(lineas)
    return {t: limpiar_lineas(ls) for t, ls in salida.items()}


# ── Lo que llama el router (BLOQUEA) ────────────────────────────────────────

def leer_propuesta(envios: list[dict[str, Any]], ventana: int = 30, ahora: datetime | None = None) -> dict[str, Any]:
    """Los insumos de las CUATRO tiendas (la pantalla elige cuáles están activas)."""
    ahora = ahora or datetime.now(timezone.utc)
    ventana = ventana if ventana in VENTANAS else PARAMETROS["ventana_dias"]
    hoy = ahora.astimezone(_CDMX).date()
    tiendas = list(TIENDAS)
    ventas = sdb.fetch_all(_SQL_VENTAS, {"desde": hoy - timedelta(days=ventana),
                                         "d7": hoy - timedelta(days=7), "hoy": hoy})
    pubs = _publicaciones()
    borradores = _borradores(ahora)
    camino, _ = en_camino(envios, ahora)

    # Los SKUs que entran a la planeación de cada tienda.
    tienda_de = {("mercado_libre", "BEKURA"): "meli:Kubera", ("mercado_libre", "SANCORFASHION"): "meli:San Corpe",
                 ("amazon", "AMAZON"): "amazon"}
    candidatos: dict[str, set[str]] = defaultdict(set)
    for v in ventas:
        t = tienda_de.get((v["canal"], v["cuenta"])) or ("walmart" if v["canal"] == "walmart" else None)
        if t:
            candidatos[t].add(v["sku"])
    for (t, s) in camino:
        candidatos[t].add(s)
    for b in borradores:
        if se_resta(b):
            candidatos[b["tienda"]].update(r["sku"] for r in b["lineas"])

    # Mercado Libre EN VIVO para las publicaciones que entran.
    vivos: dict[str, dict[str, dict]] = {}
    for t in ("meli:Kubera", "meli:San Corpe"):
        ids = [pubs[t][s]["listing_id"] for s in candidatos[t] if s in pubs[t]]
        vivos[t] = verificar_ml(TIENDAS[t]["codigo"], ids)

    # Odoo: lo libre de los candidatos. Los REEMPLAZOS ya están entre ellos: un
    # reemplazo tiene que vender en esa tienda, y todo lo que vende es candidato.
    todos = sorted(set().union(*candidatos.values()))
    productos = odoo_ventas.productos_por_sku(todos)
    libres = odoo_ventas.libre_por_almacen([p["id"] for p in productos.values()])

    skus_todos = sorted(set(productos) | set(todos))
    nombres = {f["sku"]: f["name"] for f in sdb.fetch_all(_SQL_NOMBRES, {"skus": skus_todos})}
    medidas = _por_sku(sdb.fetch_all(_SQL_MEDIDAS, {"skus": todos}))
    datos = armar_propuesta(tiendas, ventas, pubs, vivos, envios, borradores, productos, libres,
                            nombres, medidas, ahora, ventana)
    datos["fuente"] = ("ventas: channel.sales_daily_completa · publicaciones: channel.listings, verificadas en "
                       "vivo con la API de Mercado Libre; Walmart en vivo de su API · en camino: la lectura de "
                       "Envíos (Odoo + avisos de FULL) · borradores y libre por almacén: Odoo en vivo · medidas y "
                       "piezas por caja: costing.costos_validados")
    log.info("planeación: %s", {t: len(d["filas"]) for t, d in datos["tiendas"].items()})
    return datos


def buscar(tienda: str, texto: str, envios: list[dict[str, Any]], ventana: int = 30,
           ahora: datetime | None = None) -> dict[str, Any]:
    """
    Buscar SKUs PUBLICADOS en esa tienda para agregarlos a la planeación (por SKU,
    por nombre o varios SKUs pegados). Mercado Libre se verifica EN VIVO: si el SKU
    no está en la copia, se le pregunta a ML por `seller_sku`.
    """
    ahora = ahora or datetime.now(timezone.utc)
    if tienda not in TIENDAS:
        return {"ok": False, "motivo": f"tienda '{tienda}' no existe", "filas": []}
    texto = (texto or "").strip()
    if len(texto) < 2:
        return {"ok": True, "filas": []}
    trozos = [t.strip().upper() for t in re.split(r"[\s,;]+", texto) if t.strip()]
    varios = len(trozos) > 1
    pubs = _publicaciones() if tienda == "walmart" else {tienda: {}}
    if tienda != "walmart":
        filas_pub = sdb.fetch_all(_SQL_PUBLICACIONES)
        codigo = TIENDAS[tienda]["codigo"]
        for f in filas_pub:
            if f["codigo"] == codigo:
                f["stock"] = f["stock_fba"] if tienda == "amazon" else f["stock_full"]
                f["categoria"] = f.get("category_id") or f.get("product_type")
                f["precio"] = _precio(f.get("price"))
                pubs[tienda][f["sku"]] = f
    universo = pubs.get(tienda, {})
    nombres_todos: dict[str, str] = {}
    if varios:
        # Cada trozo es un SKU exacto o la BASE de sus variantes («JUGU-0100» →
        # JUGU-0100-ROJ…): sin eso, pegar la base lo daba por «no publicado».
        encontrados, faltan = [], []
        for t in trozos:
            hallados = [t] if t in universo else sorted(s for s in universo if s.upper().startswith(f"{t}-"))[:10]
            if not hallados:
                faltan.append(t)
            encontrados.extend(h for h in hallados if h not in encontrados)
    else:
        q = trozos[0]
        por_sku = [s for s in universo if q in s.upper()]
        coinc = sdb.fetch_all("select sku::text sku, name from core.products where name ilike %(q)s limit 60",
                              {"q": f"%{texto}%"})
        nombres_todos = {f["sku"]: f["name"] for f in coinc}
        por_nombre = [s for s in nombres_todos if s in universo]
        encontrados = sorted(set(por_sku) | set(por_nombre))[:25]
        faltan = [q] if not encontrados and re.fullmatch(r"[A-Z0-9-]+", q) else []

    # Mercado Libre EN VIVO: lo encontrado se verifica; lo que falta se busca.
    vivos: dict[str, dict] = {}
    no_publicados = []
    if tienda.startswith("meli"):
        codigo = TIENDAS[tienda]["codigo"]
        for s in faltan[:10]:
            ids = buscar_ml_por_sku(codigo, s)
            if ids:
                universo[s] = {"sku": s, "listing_id": ids[0], "url": None, "situacion": None,
                               "en_almacen": None, "categoria": None, "stock": None}
                encontrados.append(s)
            else:
                no_publicados.append(s)
        vivos = verificar_ml(codigo, [universo[s]["listing_id"] for s in encontrados if universo.get(s)])
    else:
        no_publicados = faltan

    hoy = ahora.astimezone(_CDMX).date()
    enc = set(encontrados)
    ventas = [v for v in sdb.fetch_all(_SQL_VENTAS, {"desde": hoy - timedelta(days=ventana),
                                                     "d7": hoy - timedelta(days=7), "hoy": hoy})
              if v["sku"] in enc]
    productos = odoo_ventas.productos_por_sku(encontrados)
    libres = odoo_ventas.libre_por_almacen([p["id"] for p in productos.values()])
    nombres = {f["sku"]: f["name"] for f in sdb.fetch_all(_SQL_NOMBRES, {"skus": encontrados})}
    medidas = _por_sku(sdb.fetch_all(_SQL_MEDIDAS, {"skus": encontrados}))
    camino, _ = en_camino(envios, ahora)
    tienda_de = {("mercado_libre", "BEKURA"): "meli:Kubera", ("mercado_libre", "SANCORFASHION"): "meli:San Corpe",
                 ("amazon", "AMAZON"): "amazon"}
    venta_de = {v["sku"]: v for v in ventas
                if (tienda_de.get((v["canal"], v["cuenta"])) or ("walmart" if v["canal"] == "walmart" else None)) == tienda}
    filas = []
    for s in encontrados:
        pub = universo.get(s)
        vivo = vivos.get((pub or {}).get("listing_id"))
        p = productos.get(s)
        filas.append(_fila(tienda, s, venta_de.get(s), pub, vivo, p, libres.get(p["id"]) if p else None,
                           camino.get((tienda, s)), None, nombres.get(s), medidas.get(s)))
    return {"ok": True, "tienda": tienda, "filas": filas, "no_publicados": no_publicados,
            "generado": ahora.isoformat()}


def _tipo_imagen(b64: str) -> str:
    return ("image/png" if b64.startswith("iVBOR") else "image/webp" if b64.startswith("UklGR")
            else "image/gif" if b64.startswith("R0lGOD") else "image/jpeg")


def imagenes_odoo(skus: list[str]) -> dict[str, str | None]:
    """La foto de cada producto en Odoo (`image_128`) como data URI, para compararla
    con la de la publicación. None = el producto no tiene foto o no está en Odoo."""
    limpios = sorted({(s or "").strip() for s in skus if (s or "").strip()})[:60]
    productos = odoo_ventas.productos_por_sku(limpios)
    salida: dict[str, str | None] = {s: None for s in limpios}
    if not productos:
        return salida
    sku_de = {p["id"]: s for s, p in productos.items()}
    for f in odoo_ventas._kw("product.product", "read", [list(sku_de), ["image_128"]]):
        b64 = f.get("image_128")
        if b64 and sku_de.get(f["id"]):
            salida[sku_de[f["id"]]] = f"data:{_tipo_imagen(b64)};base64,{b64}"
    return salida


def vista_previa(tiendas: list[dict[str, Any]], prueba: bool = True) -> dict[str, Any]:
    """
    Lo que se crearía en Odoo POR TIENDA, con el stock libre RELEÍDO ahora y las
    publicaciones de ML verificadas en vivo. No escribe nada, con o sin interruptor.
    """
    pedidos = limpiar_tiendas(tiendas)
    if not pedidos:
        return {"ok": False, "motivo": "la planeación no trae piezas", "tiendas": []}
    todos = sorted({l["sku"] for ls in pedidos.values() for l in ls})
    productos = odoo_ventas.productos_por_sku(todos)
    libres = odoo_ventas.libre_por_almacen([p["id"] for p in productos.values()])
    no_en_odoo = sorted(s for s in todos if s not in productos)
    con_producto = {t: [{**l, "product_id": productos[l["sku"]]["id"], "nombre": productos[l["sku"]]["name"]}
                        for l in ls if l["sku"] in productos] for t, ls in pedidos.items()}
    por_tienda, recortes_reparto = repartir_entre_tiendas(con_producto, libres)

    # Mercado Libre en vivo: ¿la publicación sigue viva? (sólo avisa; decide la persona)
    avisos: list[dict[str, Any]] = []
    for t in (x for x in por_tienda if x.startswith("meli")):
        codigo = TIENDAS[t]["codigo"]
        filas = sdb.fetch_all(
            "select l.sku::text sku, l.listing_id from channel.listings l join core.accounts a on a.id = l.account_id "
            "where a.legacy_code = %(c)s and l.canal = 'mercado_libre' and l.sku::text = any(%(s)s)",
            {"c": codigo, "s": [l["sku"] for l in por_tienda[t]]})
        ids = {f["sku"]: f["listing_id"] for f in filas}
        vivos = verificar_ml(codigo, list(ids.values()))
        for l in por_tienda[t]:
            v = vivos.get(ids.get(l["sku"]))
            if not ids.get(l["sku"]):
                avisos.append({"tienda": t, "sku": l["sku"], "aviso": "no está publicado en esta cuenta"})
            elif v is None:
                avisos.append({"tienda": t, "sku": l["sku"], "aviso": "Mercado Libre no contestó: sin verificar"})
            elif v.get("estado") in ("closed", "inactive"):
                avisos.append({"tienda": t, "sku": l["sku"], "aviso": f"la publicación está {v['estado']} en ML"})

    # Los almacenes se asignan tienda por tienda DESCONTANDO lo ya asignado: el
    # reparto de arriba cuida el total, esto cuida que TEXCO no se prometa dos veces.
    restante = {pid: dict(v) for pid, v in libres.items()}
    salida = []
    for t in sorted(por_tienda, key=lambda x: list(TIENDAS).index(x)):
        lineas = por_tienda[t]
        plan = repartir_almacenes([l for l in lineas if l["cantidad"] > 0], restante)
        for p in plan["partes"]:
            for l in p["lineas"]:
                restante.setdefault(l["product_id"], {})[p["almacen_id"]] = (
                    restante.get(l["product_id"], {}).get(p["almacen_id"], 0) - l["cantidad"])
        partes = [{"almacen_id": p["almacen_id"], "almacen": p["almacen"],
                   "piezas": sum(l["cantidad"] for l in p["lineas"]),
                   "lineas": [{"sku": l["sku"], "nombre": l["nombre"], "cantidad": l["cantidad"],
                               "product_id": l["product_id"]} for l in p["lineas"]]}
                  for p in plan["partes"]]
        rec = [r for r in recortes_reparto if r["tienda"] == t] + [{**r, "tienda": t} for r in plan["recortes"]]
        salida.append({"tienda": t, "nombre": TIENDAS[t]["nombre"], "socio": SOCIO[t], "partes": partes,
                       "recortes": rec, "piezas_pedidas": sum(l["cantidad"] for l in pedidos[t]),
                       "piezas": sum(p["piezas"] for p in partes)})
    salida.sort(key=lambda x: list(TIENDAS).index(x["tienda"]))
    total = sum(x["piezas"] for x in salida)
    return {"ok": total > 0, "prueba": bool(prueba), "tiendas": salida, "no_en_odoo": no_en_odoo,
            "avisos": avisos, "piezas": total,
            "piezas_pedidas": sum(x["piezas_pedidas"] for x in salida),
            "motivo": None if total > 0 else "nada de lo pedido tiene stock libre en Odoo",
            "generado": datetime.now(timezone.utc).isoformat()}


_candado = threading.Lock()


def _socio(tienda: str) -> int:
    """El id del socio fijo de la tienda; lo crea la primera vez."""
    nombre = SOCIO[tienda]
    filas = odoo_ventas._kw("res.partner", "search_read", [[["name", "=ilike", nombre]]],
                            {"fields": ["id"], "limit": 1, "order": "id asc"})
    if filas:
        return filas[0]["id"]
    d = TIENDAS[tienda]
    pid = odoo_ventas._kw(
        "res.partner", "create",
        [{"name": nombre, "type": "contact",
          "comment": (f"<p>Socio fijo de {d['nombre']} ({d['almacen']}). Lo usa «Crear FULL» del panel Omnicanal "
                      "para que la orden diga de qué cuenta y marketplace es (no quién la capturó).</p>")}],
        {"context": _SIN_AVISOS})
    log.warning("crear FULL: socio «%s» creado en Odoo (id %s)", nombre, pid)
    return pid


# La solicitud vive en la bitácora de acciones de personas (`ops.process_log`, la
# misma de publicar, precio, stock y costos; ver services/bitacora.py): una fila
# por SKU y tienda. No tiene tabla propia: la 0054 se retiró sin aplicarse
# (v0.567.0). Medido antes de decidirlo: la tabla no restringe `proceso`, ya tiene
# índices (proceso, created_at) y (sku), no la purga ningún cron, y quienes la leen
# sin filtrar por proceso sólo MUESTRAN (/flujo, «último paso» de Inventario y
# ops.rastro_autoria); ninguno decide con estas filas.
PROCESO_BITACORA = "fulfillment"


def filas_solicitud(clave: str, tienda: str, prueba: bool, parametros: dict[str, Any],
                    pedidas: list[dict[str, Any]], previa: dict[str, Any],
                    ordenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Las filas de bitácora de una solicitud, una por SKU pedido. Función pura."""
    d = TIENDAS[tienda]
    orden_de = {o["almacen"]: o for o in ordenes}
    van: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in previa["partes"]:
        o = orden_de.get(p["almacen"]) or {}
        for l in p["lineas"]:
            van[l["sku"]].append({"almacen": p["almacen"], "cantidad": l["cantidad"],
                                  "orden": o.get("orden"), "orden_id": o.get("id")})
    # Un SKU puede traer dos recortes: el del reparto entre tiendas y el de lo libre.
    recortes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in previa.get("recortes") or []:
        recortes[r["sku"]].append({"pedidas": r.get("pedidas"), "van": r.get("van"), "porque": r.get("porque")})
    filas = []
    for l in pedidas:
        almacenes = van.get(l["sku"], [])
        filas.append({
            "sku": l["sku"],
            "ref": f"{PROCESO_BITACORA}:{clave}:{tienda}:{l['sku']}",
            "detalle": {
                # canal y cuenta como los anota bitacora.py («mercado_libre», «BEKURA»).
                "tienda": tienda, "canal": {"meli": "mercado_libre"}.get(d["canal"], d["canal"]),
                "cuenta": d["codigo"], "clave": clave, "prueba": bool(prueba),
                "sugerido": l.get("sugerido"), "solicitado": l["cantidad"],
                "van": sum(x["cantidad"] for x in almacenes), "almacenes": almacenes,
                "recortes": recortes.get(l["sku"], []), "parametros": parametros or {},
            },
        })
    return filas


def _guardar_solicitud(clave: str, tienda: str, prueba: bool, quien: str, parametros: dict[str, Any],
                       pedidas: list[dict[str, Any]], previa: dict[str, Any],
                       ordenes: list[dict[str, Any]]) -> bool:
    """
    La solicitud ORIGINAL (lo sugerido y lo pedido) antes de que Odoo o bodega
    recorten: lo único que hace posible la etapa «Solicitado» y la tasa de
    validado. Va a `ops.process_log` (proceso 'fulfillment', accion 'solicitud').
    Idempotente por `detail_ref`: reintentar con la misma clave no duplica. Si
    falla NO se detiene la creación: la respuesta lo avisa.
    """
    filas = filas_solicitud(clave, tienda, prueba, parametros, pedidas, previa, ordenes)
    if not filas:
        return True
    try:
        nuevas = sdb.execute(
            """insert into ops.process_log (proceso, origen, sku, accion, estado, detalle, detail_ref, actor)
               select %(proceso)s, 'panel', r.sku::citext, 'solicitud', 'ok', r.detalle, r.ref, %(quien)s
                 from jsonb_to_recordset(%(filas)s::jsonb) as r(sku text, ref text, detalle jsonb)
                where not exists (select 1 from ops.process_log p
                                   where p.proceso = %(proceso)s and p.detail_ref = r.ref)""",
            {"proceso": PROCESO_BITACORA, "quien": (quien or "")[:120] or None, "filas": json.dumps(filas)})
        log.info("crear FULL: solicitud %s/%s en la bitácora (%s filas nuevas de %s)",
                 clave, tienda, nuevas, len(filas))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("crear FULL: la solicitud %s/%s no se guardó en la bitácora: %s", clave, tienda, exc)
        return False


def crear(tiendas: list[dict[str, Any]], quien: str, clave: str, parametros: dict[str, Any] | None = None,
          prueba: bool = True) -> dict[str, Any]:
    """
    Crea en Odoo las cotizaciones en BORRADOR: una por TIENDA y ALMACÉN. Con el
    interruptor apagado contesta la vista previa (`accion: "apagado"`) y no escribe.
    Idempotente: la misma `clave` nunca crea dos veces la misma parte.
    """
    clave = re.sub(r"[^0-9A-Za-z]", "", clave or "")[:12]
    if len(clave) < 8:
        return {"ok": False, "accion": "sin_clave", "motivo": "falta la clave de la solicitud"}
    previa = vista_previa(tiendas, prueba)
    if not previa.get("ok"):
        return {**previa, "accion": "nada_que_crear"}
    if not habilitado(refrescar=True):
        return {**previa, "ok": True, "accion": "apagado",
                "motivo": ("La creación en Odoo está APAGADA: esto es exactamente lo que se crearía. "
                           "Se enciende con el interruptor de Crear FULL (sólo admin).")}

    pedidos = limpiar_tiendas(tiendas)
    corto = (quien or "panel").split("@")[0][:40] or "panel"
    hoy = datetime.now(_CDMX)
    with _candado:
        existentes = odoo_ventas._kw("sale.order", "search_read",
                                     [[["origin", "ilike", f"· {clave} ·"]]],
                                     {"fields": ["name", "state", "warehouse_id", "partner_id", "origin"]})
        for t in previa["tiendas"]:
            t["ordenes"] = []
            if not t["partes"]:
                continue
            socio_id = _socio(t["tienda"])
            for parte in t["partes"]:
                ya = next((o for o in existentes if fenv._id(o.get("partner_id")) == socio_id
                           and fenv._id(o.get("warehouse_id")) == parte["almacen_id"]), None)
                if ya:
                    t["ordenes"].append({"id": ya["id"], "orden": ya["name"], "estado": ya["state"],
                                         "almacen": parte["almacen"], "piezas": parte["piezas"], "ya_existia": True})
                    continue
                nombre = TIENDAS[t["tienda"]]["nombre"]
                payload = {
                    "partner_id": socio_id,
                    "warehouse_id": parte["almacen_id"],
                    "origin": (f"{fenv.ORIGEN_PANEL} · {nombre} · {corto} · {clave} · {parte['almacen']}"
                               + (" · PRUEBA" if prueba else "")),
                    "note": ((f"<p><b>{PRUEBA_REF}.</b> " if prueba else "<p>")
                             + f"{nombre}: {TIENDAS[t['tienda']]['almacen']}. Creada en BORRADOR desde la "
                             f"planeación semanal de la pestaña FULLFILMENT del panel Omnicanal por {corto} el "
                             f"{hoy.day} {_MESES[hoy.month - 1]} {hoy.year}. "
                             + ("Es una PRUEBA: no se confirma ni se surte.</p>" if prueba else
                                "La confirma la KAM en Odoo. Falta el número del envío del marketplace y su "
                                "guía: se adjuntan desde el panel.</p>")),
                    "order_line": [(0, 0, {"product_id": l["product_id"], "product_uom_qty": l["cantidad"],
                                           "price_unit": 0.0, "tax_id": [(6, 0, [])],
                                           "name": f"[{l['sku']}] {l['nombre'] or ''}".strip()[:400]})
                                   for l in parte["lineas"]],
                }
                if prueba:
                    payload["client_order_ref"] = PRUEBA_REF
                oid = odoo_ventas._kw("sale.order", "create", [payload], {"context": _SIN_AVISOS})
                o = odoo_ventas._kw("sale.order", "read",
                                    [[oid], ["name", "state", "picking_ids", "order_line"]])[0]
                t["ordenes"].append({"id": oid, "orden": o["name"], "estado": o["state"],
                                     "almacen": parte["almacen"], "piezas": parte["piezas"],
                                     "renglones": len(o.get("order_line") or []),
                                     "con_picking": bool(o.get("picking_ids")), "ya_existia": False})
                log.warning("crear FULL: %s creada en BORRADOR (%s, %s, %s pzs%s) por %s", o["name"], nombre,
                            parte["almacen"], parte["piezas"], ", PRUEBA" if prueba else "", corto)

    raras = []
    for t in previa["tiendas"]:
        for c in t.get("ordenes", []):
            c["url"] = odoo_ventas.url_orden_publica().format(id=c["id"])
            if not c["ya_existia"] and (c["estado"] != "draft" or c.get("con_picking")):
                raras.append(c)
        t["solicitud_guardada"] = _guardar_solicitud(
            clave, t["tienda"], prueba, quien, parametros or {}, pedidos.get(t["tienda"], []), t,
            [{"id": c["id"], "orden": c["orden"], "almacen": c["almacen"]} for c in t.get("ordenes", [])])
    todas = [c for t in previa["tiendas"] for c in t.get("ordenes", [])]
    accion = "ya_existia" if todas and all(c["ya_existia"] for c in todas) else "creada"
    return {**previa, "ok": not raras, "accion": accion,
            "solicitud_guardada": all(t.get("solicitud_guardada") for t in previa["tiendas"] if t.get("ordenes")),
            "motivo": (f"Odoo dejó {', '.join(c['orden'] for c in raras)} fuera de borrador: revisar a mano."
                       if raras else None)}


def adjuntar_guia(orden_id: int, numero: str | None, pdf: bytes | None, nombre_pdf: str | None,
                  quien: str) -> dict[str, Any]:
    """
    Después de crear: el número del envío del marketplace y su GUÍA en PDF, en la
    orden de Odoo («Subir guía», `meli_etiqueta_file`, como las de SHEIN). Sólo en
    órdenes que creó el panel y que siguen en borrador o cotización: una orden de
    otra persona o ya confirmada no se toca. Se RELEE para saber que quedó.
    """
    numero = re.sub(r"\s+", " ", (numero or "").strip())[:60]
    if not numero and not pdf:
        return {"ok": False, "accion": "nada", "motivo": "falta el número del envío o el PDF de la guía"}
    if pdf is not None and not pdf.startswith(b"%PDF"):
        return {"ok": False, "accion": "sin_pdf", "motivo": "el archivo no es un PDF"}
    if not habilitado(refrescar=True):
        return {"ok": False, "accion": "apagado",
                "motivo": "La escritura en Odoo está APAGADA (interruptor de Crear FULL)."}
    o = odoo_ventas._kw("sale.order", "read", [[int(orden_id)], ["name", "state", "origin", "client_order_ref"]])
    if not o:
        return {"ok": False, "accion": "no_existe", "motivo": f"la orden {orden_id} no existe en Odoo"}
    o = o[0]
    if not str(o.get("origin") or "").startswith(fenv.ORIGEN_PANEL):
        return {"ok": False, "accion": "ajena", "motivo": f"{o['name']} no la creó el panel: no se toca"}
    if o.get("state") not in ("draft", "sent"):
        return {"ok": False, "accion": "confirmada",
                "motivo": f"{o['name']} ya está en «{o.get('state')}»: la guía se sube en Odoo"}
    valores: dict[str, Any] = {}
    if numero:
        prueba = "PRUEBA" in str(o.get("client_order_ref") or "").upper() or "PRUEBA" in str(o.get("origin") or "")
        valores["client_order_ref"] = f"PRUEBA · {numero}" if prueba else numero
    if pdf:
        nombre = re.sub(r"[^\w.\- ]", "", nombre_pdf or "") or f"guia_{o['name']}.pdf"
        if not nombre.lower().endswith(".pdf"):
            nombre += ".pdf"
        valores["meli_etiqueta_file"] = base64.b64encode(pdf).decode("ascii")
        valores["meli_etiqueta_filename"] = nombre
    odoo_ventas._kw("sale.order", "write", [[int(orden_id)], valores], {"context": _SIN_AVISOS})
    leida = odoo_ventas._kw("sale.order", "read",
                            [[int(orden_id)], ["name", "client_order_ref", "meli_etiqueta_file",
                                               "meli_etiqueta_filename"]], {"context": {"bin_size": True}})[0]
    ok = ((not numero or leida.get("client_order_ref") == valores.get("client_order_ref"))
          and (not pdf or (leida.get("meli_etiqueta_file") and leida.get("meli_etiqueta_filename") == valores.get("meli_etiqueta_filename"))))
    log.warning("crear FULL: guía de %s %s por %s (número %s, PDF %s)", leida["name"],
                "ADJUNTADA" if ok else "NO VERIFICADA", (quien or "?").split("@")[0], numero or "—",
                leida.get("meli_etiqueta_filename") or "—")
    return {"ok": bool(ok), "accion": "adjuntada" if ok else "no_verificada", "orden": leida["name"],
            "referencia": leida.get("client_order_ref"), "pdf": leida.get("meli_etiqueta_filename") if pdf else None,
            "tamano_pdf": leida.get("meli_etiqueta_file") if pdf else None}
