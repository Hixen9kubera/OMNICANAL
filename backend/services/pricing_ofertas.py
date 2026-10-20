"""
pricing_ofertas.py — Las OFERTAS de Mercado Libre que se pueden tomar desde PRICING:
verlas, tomarlas y quitarlas (v0.672.0, pedido de Brandon 20-oct-2026).

LOS TRES TIPOS (medidos en vivo el 20-oct: una publicación de cada uno se tomó,
se quitó y quedó igual que antes)
    LIGHTNING        Oferta relámpago. ML invita a la publicación con un precio
                     sugerido, un piso y un rango de piezas. Se toma con
                     `{deal_price, stock, promotion_type}` SIN promotion_id: el
                     horario (12 h) lo escoge ML — a ORG-0852-NEG le dio el 22-oct
                     de 00:00 a 11:59, ~33 h después. Se quita con solo
                     `promotion_type`, y SOLO mientras no ha empezado: activa, ML no
                     la deja quitar (lo único que la para es pausar la publicación).
    DEAL             Campañas de ML: Buen Fin, «Activa tus descuentos», clearance
                     de Full. `{promotion_id, promotion_type, deal_price}` dentro
                     del rango que da ML (min–max, con sugerido). Acepta aunque la
                     publicación tenga descuento individual. Se quita con
                     promotion_id.
    SELLER_CAMPAIGN  Campaña propia (ALWAYS ON OCT, Octubre). Igual que DEAL, pero
                     ML NO deja entrar a una publicación con DESCUENTO INDIVIDUAL
                     activo: contesta «No candidates found for item». La señal es
                     que en `/seller-promotions/items/{id}` su entrada de la campaña
                     viene SIN rango de precio. Por eso, para la campaña propia, se
                     lee cada candidata una por una.

QUÉ LEE (solo GET a ML y SELECT a kubera)
    · `/seller-promotions/users/{uid}` → las ofertas vivas de la cuenta.
    · `/seller-promotions/promotions/{id}/items` → candidatas e inscritas, con su rango.
    · `/items?ids=` → título, foto, stock y estado.
    · `/seller-promotions/items/{id}` → solo las candidatas a campaña propia.
    · kubera `channel.listings` (SKU y lo que se cobra hoy) y `channel.order_items`
      (piezas vendidas en 30 días).
    Se recuerda 10 min por cuenta; «Volver a leer» la pide de nuevo.

QUÉ ESCRIBE: un POST (tomar) o un DELETE (quitar) por clic, en ML. Antes se relee
la publicación en ML; después se comprueba que quedó y se anota en
`ops.process_log` (proceso `ofertas`, origen `pricing`, `detail_ref`
`ofertas:{id}`) con quién lo hizo. Solo admin (ver `routers/pricing.py`). El token
es el del backend (no se renueva aquí). Red con httpx asíncrono; kubera en un hilo
(regla 11).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from services import pricing_precio as pp

log = logging.getLogger("omnicanal.pricing.ofertas")

CDMX = timezone(timedelta(hours=-6))
PROCESO, ORIGEN = "ofertas", "pricing"
TIPOS = {"LIGHTNING": "Relámpago", "DEAL": "Campaña de ML", "SELLER_CAMPAIGN": "Campaña propia"}
PROMO_ID = re.compile(r"^[A-Z]{1,4}-MLM\d{1,14}$")
TTL_S = 600                 # la lista se recuerda 10 min por cuenta
MAX_PAGINAS = 60            # 50 por página: 3,000 publicaciones por oferta
ESPERA_COMPROBAR_S = 30     # cuánto se espera a ver el cambio en ML
_NO_CANDIDATA = "no candidates found"

ErrorOferta = pp.ErrorPrecio   # el mismo «algo que la persona tiene que leer» del editor de precio

_cache: dict[str, tuple[float, dict]] = {}
_leyendo: dict[str, asyncio.Lock] = {}


# ── PURO ─────────────────────────────────────────────────────────────────────

def _f(v: Any) -> float | None:
    return pp._f(v)


def _hora(s: str | None) -> str | None:
    """«2026-10-22T00:00:00» (hora local de ML, sin zona) o con zona → «2026-10-22 00:00» en CDMX."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:16]
    if d.tzinfo:
        d = d.astimezone(CDMX)
    return d.strftime("%Y-%m-%d %H:%M")


def promo_publica(p: dict, conteo: dict[str, int]) -> dict:
    """PURA. Una oferta de la cuenta, como la ve la pantalla."""
    return {"id": p.get("id"), "tipo": p.get("type"), "nombre": (p.get("name") or "").strip() or TIPOS.get(p.get("type"), ""),
            "estado": p.get("status"), "inicio": _hora(p.get("start_date")), "fin": _hora(p.get("finish_date")),
            "limite": _hora(p.get("deadline_date")), "conteo": conteo}


def fila_de(cuenta: str, promo: dict, x: dict) -> dict:
    """PURA. Una publicación dentro de una oferta (candidata, programada o activa), del listado de la oferta."""
    tipo = promo.get("type")
    st = x.get("status")
    stock = x.get("stock") or {}
    inscrita = st in ("pending", "started")
    # En la relámpago, el `price` de una candidata ES el sugerido de ML (doc de ofertas relámpago).
    sugerido = _f(x.get("suggested_discounted_price")) or (_f(x.get("price")) if tipo == "LIGHTNING" and not inscrita else None)
    return {
        "item": x.get("id"), "cuenta": cuenta, "tipo": tipo, "promo_id": promo.get("id"),
        "promo": (promo.get("name") or "").strip() or TIPOS.get(tipo, ""), "estado": st,
        "precio": _f(x.get("price")) if inscrita else None, "sugerido": sugerido,
        "min": _f(x.get("min_discounted_price")), "max": _f(x.get("max_discounted_price")),
        "top": _f(x.get("max_top_discounted_price")), "original": _f(x.get("original_price")),
        "stock_min": stock.get("min"), "stock_max": stock.get("max"), "restante": stock.get("remaining_stock"),
        "inicio": _hora(x.get("start_date")), "fin": _hora(x.get("finish_date") or x.get("end_date")),
        "ref": x.get("ref_id") or x.get("offer_id"), "puede": None, "motivo": None,
    }


def bloqueo(fila: dict, info: dict | None, promos_item: list[dict] | None) -> tuple[bool | None, str | None]:
    """
    PURA. ¿Puede entrar esta candidata? (True/False, y el porqué en palabras); None = no se sabe (con o sin aviso).

    `promos_item` es `/seller-promotions/items/{id}` (solo se lee para la campaña propia).
    """
    if fila["estado"] != "candidate":
        return None, None
    if info and info.get("status") and info["status"] != "active":
        return False, f"La publicación está {'pausada' if info['status'] == 'paused' else info['status']}."
    if fila["tipo"] == "LIGHTNING":
        disp, minimo = (info or {}).get("stock"), fila.get("stock_min")
        if disp is not None and minimo and disp < minimo:
            return False, f"La relámpago pide al menos {minimo} piezas y tiene {disp}."
        return True, None
    if fila["tipo"] == "SELLER_CAMPAIGN":
        if promos_item is None:
            return None, None
        suya = next((p for p in promos_item if p.get("id") == fila["promo_id"]), None)
        if suya and _f(suya.get("max_discounted_price")):
            return True, None
        pd = next((p for p in promos_item if p.get("type") == "PRICE_DISCOUNT" and p.get("status") in ("started", "pending")), None)
        if pd:
            precio = _f(pd.get("price"))
            return False, ("Tiene descuento individual" + (f" a ${precio:,.2f}" if precio else "") +
                           ": ML no deja meterla a la campaña. Quítalo con «Cambiar precio» (Desc. 0 %) y vuelve en unos minutos.")
        # Sin rango y SIN descuento individual: no se ha probado (las dos rechazadas del 20-oct tenían descuento
        # individual). Se deja intentar con el aviso; si ML no la acepta, el error lo explica.
        return None, "ML no le da rango de precio: puede que no la acepte."
    if not fila.get("min") and not fila.get("max"):
        return None, None
    return True, None


def validar_toma(fila: dict, precio: float | None, stock: int | None, disponible: int | None) -> None:
    """PURA. Lo que se revisa antes del POST, con la entrada de ML leída en vivo. Lanza ErrorOferta."""
    if not precio:
        raise ErrorOferta("Escribe el precio de la oferta.")
    mn, mx = fila.get("min"), fila.get("max")
    if mn and precio < mn - 0.005:
        raise ErrorOferta(f"ML no acepta menos de ${mn:,.2f} en esta oferta.")
    if mx and precio > mx + 0.005:
        raise ErrorOferta(f"ML no acepta más de ${mx:,.2f} en esta oferta.")
    if fila["tipo"] == "LIGHTNING":
        smin, smax = fila.get("stock_min"), fila.get("stock_max")
        if not stock or stock < 1:
            raise ErrorOferta("La relámpago aparta piezas: escribe cuántas.")
        if smin and stock < smin:
            raise ErrorOferta(f"La relámpago pide al menos {smin} piezas.")
        if smax and stock > smax:
            raise ErrorOferta(f"La relámpago acepta hasta {smax} piezas.")
        if disponible is not None and stock > disponible:
            raise ErrorOferta(f"Solo hay {disponible} piezas disponibles.")


def cuerpo_toma(tipo: str, promo_id: str, precio: float, stock: int | None) -> dict:
    """PURA. El cuerpo del POST según la doc de ML de cada tipo."""
    if tipo == "LIGHTNING":
        return {"promotion_type": "LIGHTNING", "deal_price": round(precio, 2), "stock": int(stock or 0)}
    return {"promotion_id": promo_id, "promotion_type": tipo, "deal_price": round(precio, 2)}


def traducir(tipo: str, r: httpx.Response) -> str:
    """PURA. El rechazo de ML en palabras que se entienden."""
    t = pp._txt(r)
    if _NO_CANDIDATA in t.lower():
        if tipo == "SELLER_CAMPAIGN":
            return ("ML no la deja entrar a la campaña. Casi siempre es porque tiene un descuento individual activo; "
                    "si se lo acabas de quitar, ML tarda de 1 a 4 minutos en volver a ofrecerla.")
        return "ML ya no la ofrece para esta oferta (o tarda unos minutos tras un cambio de precio)."
    if "credib" in t.lower():
        return "ML dice que ese precio no es creíble para esta oferta: usa uno dentro del rango que da."
    return f"Mercado Libre no aceptó ({r.status_code}): {t}"


# ── ML ───────────────────────────────────────────────────────────────────────

async def _get(cli: httpx.AsyncClient, ruta: str, **params) -> tuple[int, Any]:
    for intento in range(4):
        try:
            r = await cli.get(ruta, params=params)
        except httpx.HTTPError:
            await asyncio.sleep(1 + intento)
            continue
        if r.status_code == 429:
            await asyncio.sleep(2 + 3 * intento)
            continue
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
    return 0, None


async def _items_de(cli: httpx.AsyncClient, pid: str, tipo: str, **extra) -> list[dict]:
    out, sa = [], None
    for _ in range(MAX_PAGINAS):
        params = {"promotion_type": tipo, "app_version": "v2", "limit": 50, **extra}
        if sa:
            params["search_after"] = sa
        s, j = await _get(cli, f"/seller-promotions/promotions/{pid}/items", **params)
        if s != 200 or not isinstance(j, dict):
            break
        res = j.get("results") or []
        out.extend(res)
        pg = j.get("paging") or {}
        sa = pg.get("searchAfter") or pg.get("search_after")
        if not res or not sa:
            break
    return out


async def _info_items(cli: httpx.AsyncClient, ids: list[str]) -> dict[str, dict]:
    """Título, foto, stock y estado de ML, de 20 en 20."""
    info: dict[str, dict] = {}
    sem = asyncio.Semaphore(6)

    async def lote(xs: list[str]) -> None:
        async with sem:
            s, j = await _get(cli, "/items", ids=",".join(xs),
                              attributes="id,title,thumbnail,permalink,price,available_quantity,status")
        for x in j if isinstance(j, list) else []:
            b = x.get("body") or {}
            if x.get("code") == 200 and b.get("id"):
                info[b["id"]] = {"titulo": b.get("title"), "foto": (b.get("thumbnail") or "").replace("http://", "https://"),
                                 "url": b.get("permalink"), "normal": _f(b.get("price")), "stock": b.get("available_quantity"),
                                 "status": b.get("status")}

    await asyncio.gather(*(lote(ids[i:i + 20]) for i in range(0, len(ids), 20)))
    return info


async def _promos_items(cli: httpx.AsyncClient, ids: list[str]) -> dict[str, list[dict]]:
    sem = asyncio.Semaphore(8)
    out: dict[str, list[dict]] = {}

    async def uno(i: str) -> None:
        async with sem:
            s, j = await _get(cli, f"/seller-promotions/items/{i}", app_version="v2")
        if s == 200 and isinstance(j, list):
            out[i] = j

    await asyncio.gather(*(uno(i) for i in ids))
    return out


def _kubera(ids: list[str]) -> dict[str, dict]:
    """BLOQUEANTE (kubera, SELECT). SKU, lo que se cobra hoy y piezas vendidas en 30 días."""
    from services import supabase_db as sdb

    if not ids:
        return {}
    out: dict[str, dict] = {}
    for f in sdb.fetch_all(
        """
        select distinct on (l.listing_id) l.listing_id, l.sku::text as sku, l.price_sale, l.price, l.logistic_type
          from channel.listings l
         where l.canal = 'mercado_libre' and l.listing_id = any(%(ids)s)
         order by l.listing_id, l.updated_at desc nulls last
        """, {"ids": ids}):
        out[f["listing_id"]] = {"sku": f["sku"], "cobra": _f(f["price_sale"]) or _f(f["price"]), "logistica": f["logistic_type"]}
    for f in sdb.fetch_all(
        """
        select oi.item_id, sum(oi.cantidad)::int as u30
          from channel.order_items oi
          join channel.orders o on o.canal = oi.canal and o.cuenta = oi.cuenta and o.external_order_id = oi.external_order_id
         where oi.canal = 'mercado_libre' and oi.item_id = any(%(ids)s) and o.creado_at >= now() - interval '30 days'
           and lower(coalesce(o.estado_canal, '')) <> 'cancelled'
         group by oi.item_id
        """, {"ids": ids}):
        out.setdefault(f["item_id"], {})["u30"] = f["u30"] or 0
    return out


async def _leer_cuenta(cuenta: str) -> dict:
    async with await pp._cliente(cuenta) as cli:
        s, yo = await _get(cli, "/users/me")
        if s != 200 or not isinstance(yo, dict) or not yo.get("id"):
            raise ErrorOferta(f"Mercado Libre no contestó por la cuenta {cuenta} ({s}).")
        s, lista = await _get(cli, f"/seller-promotions/users/{yo['id']}", app_version="v2")
        promos = [p for p in ((lista or {}).get("results") or [])
                  if p.get("type") in TIPOS and p.get("status") in ("pending", "started") and p.get("id")]
        listas = await asyncio.gather(*(_items_de(cli, p["id"], p["type"]) for p in promos))
        filas: list[dict] = []
        publicas = []
        for p, xs in zip(promos, listas):
            conteo: dict[str, int] = {}
            for x in xs:
                if x.get("status") not in ("candidate", "pending", "started"):
                    continue
                conteo[x["status"]] = conteo.get(x["status"], 0) + 1
                filas.append(fila_de(cuenta, p, x))
            publicas.append(promo_publica(p, conteo))
        ids = sorted({f["item"] for f in filas if f["item"]})
        info = await _info_items(cli, ids)
        a_revisar = sorted({f["item"] for f in filas if f["tipo"] == "SELLER_CAMPAIGN" and f["estado"] == "candidate"})
        por_item = await _promos_items(cli, a_revisar)
    try:
        k = await asyncio.to_thread(_kubera, ids)
    except Exception as exc:  # noqa: BLE001 — sin kubera la lista sirve igual (sin SKU ni ventas)
        log.warning("ofertas: kubera no contestó: %s", exc)
        k = {}
    for f in filas:
        i = info.get(f["item"]) or {}
        kk = k.get(f["item"]) or {}
        f.update(sku=kk.get("sku"), titulo=i.get("titulo"), foto=i.get("foto"), url=i.get("url"), stock=i.get("stock"),
                 normal=i.get("normal"), status=i.get("status"), cobra=kk.get("cobra"), u30=kk.get("u30", 0),
                 logistica=kk.get("logistica"))
        f["puede"], f["motivo"] = bloqueo(f, i, por_item.get(f["item"]) if f["item"] in por_item else None)
    return {"cuenta": cuenta, "leido": datetime.now(CDMX).isoformat(timespec="seconds"), "promos": publicas, "filas": filas,
            "kubera": bool(k)}


async def listar(cuenta: str, fresco: bool = False) -> dict:
    """Las ofertas de la cuenta y sus publicaciones. Solo lee. Se recuerda 10 min por cuenta."""
    cuenta = (cuenta or "").strip().upper()
    if cuenta not in pp.CUENTAS:
        raise ErrorOferta("Cuenta no válida.")
    candado = _leyendo.setdefault(cuenta, asyncio.Lock())
    async with candado:
        c = _cache.get(cuenta)
        if c and not fresco and time.monotonic() - c[0] < TTL_S:
            return c[1]
        datos = await _leer_cuenta(cuenta)
        _cache[cuenta] = (time.monotonic(), datos)
        return datos


def _recordar(cuenta: str, item_id: str, tipo: str, promo_id: str, cambio: dict) -> None:
    """Pone en la lista recordada lo que acaba de pasar, sin volver a leer toda la cuenta."""
    c = _cache.get(cuenta)
    if not c:
        return
    for f in c[1]["filas"]:
        if f["item"] == item_id and f["tipo"] == tipo and f["promo_id"] == promo_id and (
                tipo != "LIGHTNING" or f["estado"] == cambio.get("de")):
            f.update({k: v for k, v in cambio.items() if k != "de"})
            break


# ── TOMAR Y QUITAR ───────────────────────────────────────────────────────────

def _entrada(promos_item: list[dict], tipo: str, promo_id: str, estados: tuple[str, ...]) -> list[dict]:
    return [p for p in promos_item if p.get("type") == tipo and p.get("status") in estados
            and (tipo == "LIGHTNING" or p.get("id") == promo_id)]


async def _leer_item(cli: httpx.AsyncClient, item_id: str) -> tuple[dict, list[dict], float | None]:
    (s1, it), (s2, pr), (s3, sp) = await asyncio.gather(
        _get(cli, f"/items/{item_id}", attributes="id,title,price,available_quantity,status"),
        _get(cli, f"/seller-promotions/items/{item_id}", app_version="v2"),
        _get(cli, f"/items/{item_id}/sale_price", context="channel_marketplace"))
    if s1 != 200 or not isinstance(it, dict):
        raise ErrorOferta(f"Mercado Libre no devolvió la publicación ({s1}).")
    if s2 != 200 or not isinstance(pr, list):
        raise ErrorOferta(f"Mercado Libre no devolvió las promociones de la publicación ({s2}).")
    return it, pr, _f((sp or {}).get("amount")) if s3 == 200 and isinstance(sp, dict) else None


def _validar(item_id: str, cuenta: str, tipo: str, promo_id: str) -> tuple[str, str]:
    cuenta = pp._validar(item_id, cuenta)
    tipo = (tipo or "").strip().upper()
    if tipo not in TIPOS:
        raise ErrorOferta("Tipo de oferta no válido.")
    if not PROMO_ID.match(promo_id or ""):
        raise ErrorOferta("Oferta no válida.")
    return cuenta, tipo


def _anotar(sku: str | None, accion: str, estado: str, detalle: dict, quien: str) -> None:
    """BLOQUEANTE (kubera). La bitácora nunca tumba lo ya hecho en ML."""
    from services import supabase_db as sdb

    try:
        sdb.execute(
            "INSERT INTO ops.process_log (proceso, origen, sku, accion, estado, detalle, detail_ref, actor) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
            (PROCESO, ORIGEN, sku, accion, estado, json.dumps(detalle, ensure_ascii=False, default=str),
             f"ofertas:{detalle.get('item')}", quien or None))
    except Exception as exc:  # noqa: BLE001
        log.warning("ofertas: no se pudo anotar %s de %s: %s", accion, detalle.get("item"), exc)


async def _horario(cli: httpx.AsyncClient, item_id: str) -> tuple[str | None, str | None]:
    """El horario que ML le dio a una relámpago recién tomada (la lista de la oferta lo trae; la del ítem no)."""
    xs = await _items_de(cli, "LGH-MLM1000", "LIGHTNING", item_id=item_id)
    xs = [x for x in xs if x.get("id") == item_id and x.get("status") == "pending"]
    xs.sort(key=lambda x: x.get("start_date") or "", reverse=True)
    return (_hora(xs[0].get("start_date")), _hora(xs[0].get("finish_date"))) if xs else (None, None)


async def tomar(item_id: str, cuenta: str, tipo: str, promo_id: str, precio: float, stock: int | None = None,
                sku: str | None = None, quien: str = "") -> dict:
    """ESCRIBE EN ML: inscribe la publicación en la oferta. Relee antes, comprueba después y lo anota."""
    cuenta, tipo = _validar(item_id, cuenta, tipo, promo_id)
    precio = _f(precio)
    candado = pp._candados.setdefault(item_id, asyncio.Lock())
    if candado.locked():
        raise ErrorOferta("Ya se está cambiando esta publicación. Espera a que termine.")
    async with candado, await pp._cliente(cuenta) as cli:
        it, pr, cobra_antes = await _leer_item(cli, item_id)
        cand = _entrada(pr, tipo, promo_id, ("candidate",))
        if not cand:
            ya = _entrada(pr, tipo, promo_id, ("pending", "started"))
            raise ErrorOferta("Ya está inscrita en esta oferta." if ya and tipo != "LIGHTNING"
                              else "ML ya no la tiene como candidata a esta oferta. Vuelve a leer la lista.")
        e = cand[0]
        fila = {"tipo": tipo, "min": _f(e.get("min_discounted_price")), "max": _f(e.get("max_discounted_price")),
                "stock_min": (e.get("stock") or {}).get("min"), "stock_max": (e.get("stock") or {}).get("max")}
        if tipo == "SELLER_CAMPAIGN":
            puede, motivo = bloqueo({"estado": "candidate", "tipo": tipo, "promo_id": promo_id}, None, pr)
            if puede is False:   # con descuento individual activo: ML la rechaza seguro (medido el 20-oct)
                raise ErrorOferta(motivo or "ML no la deja entrar a la campaña.")
        validar_toma(fila, precio, stock, it.get("available_quantity"))
        cuerpo = cuerpo_toma(tipo, promo_id, precio, stock)
        detalle = {"item": item_id, "cuenta": cuenta, "tipo": tipo, "promo_id": promo_id, "cuerpo": cuerpo,
                   "antes": {"cobra": cobra_antes, "entrada": e}}
        r = await cli.post(f"/seller-promotions/items/{item_id}", params={"app_version": "v2"}, json=cuerpo)
        detalle["http"] = r.status_code
        if r.status_code not in (200, 201):
            detalle["error"] = pp._txt(r)
            await asyncio.to_thread(_anotar, sku, "tomar", "error", detalle, quien)
            raise ErrorOferta(traducir(tipo, r))
        try:
            detalle["respuesta"] = r.json()
        except ValueError:
            detalle["respuesta"] = r.text[:300]
        # Comprobar en ML que quedó.
        quedo, cobra_despues, limite = None, None, time.monotonic() + ESPERA_COMPROBAR_S
        while time.monotonic() < limite:
            await asyncio.sleep(3)
            try:
                _, pr2, cobra_despues = await _leer_item(cli, item_id)
            except ErrorOferta:
                continue
            ins = _entrada(pr2, tipo, promo_id, ("pending", "started"))
            if ins:
                quedo = ins[0]
                break
        inicio, fin = (None, None)
        if tipo == "LIGHTNING" and quedo:
            inicio, fin = await _horario(cli, item_id)
    estado = (quedo or {}).get("status")
    detalle.update(despues={"cobra": cobra_despues, "entrada": quedo, "inicio": inicio, "fin": fin})
    await asyncio.to_thread(_anotar, sku, "tomar", "ok" if quedo else "sin_confirmar", detalle, quien)
    if quedo:
        _recordar(cuenta, item_id, tipo, promo_id, {"de": "candidate", "estado": estado, "precio": _f(quedo.get("price")) or precio,
                                                    "inicio": inicio, "fin": fin, "puede": None, "motivo": None,
                                                    "cobra": cobra_despues})
    avisos = []
    if not quedo:
        avisos.append("ML aceptó, pero todavía no se ve inscrita. Vuelve a leer la lista en un minuto.")
    if tipo == "LIGHTNING":
        avisos.append("La relámpago ya no se puede quitar cuando empieza: solo mientras está programada.")
    return {"estado": estado, "precio": _f((quedo or {}).get("price")) or precio, "inicio": inicio, "fin": fin,
            "cobra_antes": cobra_antes, "cobra_despues": cobra_despues, "avisos": avisos}


async def quitar(item_id: str, cuenta: str, tipo: str, promo_id: str, sku: str | None = None, quien: str = "") -> dict:
    """ESCRIBE EN ML: saca la publicación de la oferta. Relee antes, comprueba después y lo anota."""
    cuenta, tipo = _validar(item_id, cuenta, tipo, promo_id)
    candado = pp._candados.setdefault(item_id, asyncio.Lock())
    if candado.locked():
        raise ErrorOferta("Ya se está cambiando esta publicación. Espera a que termine.")
    async with candado, await pp._cliente(cuenta) as cli:
        _, pr, cobra_antes = await _leer_item(cli, item_id)
        ins = _entrada(pr, tipo, promo_id, ("pending", "started"))
        if not ins:
            raise ErrorOferta("No está inscrita en esta oferta (ya se quitó o terminó). Vuelve a leer la lista.")
        if tipo == "LIGHTNING":
            # ML quita la relámpago solo con el tipo: con una activa, o con dos programadas, no se sabe cuál quitaría.
            if any(p.get("status") == "started" for p in ins):
                raise ErrorOferta("La relámpago ya empezó: ML no deja quitarla. Lo único que la detiene es pausar la publicación.")
            if len(ins) > 1:
                raise ErrorOferta(f"Tiene {len(ins)} relámpagos programadas y ML no deja escoger cuál quitar: quítala en Mercado Libre.")
        params = {"promotion_type": tipo, "app_version": "v2"}
        if tipo != "LIGHTNING":
            params["promotion_id"] = promo_id
        detalle = {"item": item_id, "cuenta": cuenta, "tipo": tipo, "promo_id": promo_id, "params": params,
                   "antes": {"cobra": cobra_antes, "entrada": ins[0]}}
        r = await cli.delete(f"/seller-promotions/items/{item_id}", params=params)
        detalle["http"] = r.status_code
        if r.status_code not in (200, 204):
            detalle["error"] = pp._txt(r)
            await asyncio.to_thread(_anotar, sku, "quitar", "error", detalle, quien)
            raise ErrorOferta(f"Mercado Libre no la quitó ({r.status_code}): {pp._txt(r)}")
        salio, cobra_despues, limite = False, None, time.monotonic() + ESPERA_COMPROBAR_S
        while time.monotonic() < limite:
            await asyncio.sleep(3)
            try:
                _, pr2, cobra_despues = await _leer_item(cli, item_id)
            except ErrorOferta:
                continue
            if not _entrada(pr2, tipo, promo_id, ("pending", "started")):
                salio = True
                break
    detalle.update(despues={"cobra": cobra_despues, "salio": salio})
    await asyncio.to_thread(_anotar, sku, "quitar", "ok" if salio else "sin_confirmar", detalle, quien)
    if salio:
        _recordar(cuenta, item_id, tipo, promo_id, {"de": ins[0].get("status"), "estado": "candidate", "precio": None,
                                                    "inicio": None, "fin": None, "puede": None, "motivo": None,
                                                    "cobra": cobra_despues})
    return {"estado": "candidate" if salio else ins[0].get("status"), "cobra_antes": cobra_antes, "cobra_despues": cobra_despues,
            "avisos": [] if salio else ["ML aceptó, pero todavía se ve inscrita. Vuelve a leer la lista en un minuto."]}


def olvidar() -> None:
    _cache.clear()
