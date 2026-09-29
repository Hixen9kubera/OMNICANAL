"""Extracción de la API de Mercado Libre para el laboratorio: SOLO GET, sin renovar tokens.

Todo pasa por `ml_http.get` (freno global de `LAB_ML_RPS`=4/s entre hilos, espera
ante 429, pausa 11:50–12:30 UTC por el cron de visitas de producción, y ante un
401 relee el token de kubera UNA vez y aborta con `TokenInvalido`). El cupo de
la API es por aplicación y lo compartimos con producción: por eso 6 hilos pero
el ritmo lo pone el freno, no los hilos.

Etapas (`ETAPAS`) y lo que producen en `crudo/<día>/`:

| etapa | llamadas | archivo |
|---|---|---|
| universo | scan `/users/{uid}/items/search` active+paused + multiget `/items?ids=` de 20 | `ml_universo.jsonl` |
| visitas | `/items/{id}/visits/time_window` de TODAS las FULL (activas+pausadas) | `ml_visitas.jsonl`, `cache/visitas/<id>.json`, `cache/visitas_serie.json` |
| visitas_cuenta | `/users/{uid}/items_visits/time_window?last=150` | `ml_visitas_cuenta.jsonl` |
| precios | `/items/{id}/prices` (activas) + `/sale_price` (activas + pausadas FULL que vendieron en 150 d) | `ml_precios.jsonl` |
| promos | `/seller-promotions/items/{id}?app_version=v2` (activas FULL) | `ml_promos.jsonl` |
| envio | `/users/{uid}/shipping_options/free?item_id=&item_price=<cobrado>` (activas FULL) | `ml_envio.jsonl` |
| comisiones | `/sites/MLM/listing_prices` gold_pro+fulfillment a los precios muestra, por categoría con FULL | `cache/comisiones.json` (vigencia 7 d) |
| sugerencias | `/suggestions/user/{uid}/items` + `/suggestions/items/{id}/details` | `ml_sugerencias.jsonl` |
| price_to_win | `/items/{id}/price_to_win?version=v2` de las activas de catálogo | `ml_price_to_win.jsonl` |

El universo son LISTING_IDS, no SKUs: `channel.listings` guarda una fila por
(sku, cuenta) y esconde las publicaciones duplicadas (medido el 28-sep: 10
activas sin fila, 11 ítems ocultos con $132,677 vendidos en 30 d). Aquí cada
publicación viva es una línea con su SKU leído del propio ítem
(`inventario._sku_de_item`: seller_custom_field → SELLER_SKU → variaciones).

Trampas que este módulo esquiva (medidas en los informes de `scratchpad/mapa/`):
- Visitas: la API OMITE los días en cero y los devuelve desordenados; el último
  punto (`date_to`) es HOY y está incompleto. Se ordena, se rellena con 0 y se
  descarta hoy. No hay multiget de visitas (400 con 2 ids). `last` máximo 150.
  El relleno de ceros empieza en `date_created` si el ítem es más nuevo que la
  ventana: antes de existir no había "cero visitas", no había publicación.
- `item.price` NO es lo que se cobra (MLM5741078908: price 244.5, se cobra 159).
  El precio cobrado sale de `/sale_price`; `/prices` da la vigencia de la promo
  y la que queda cuando termina.
- `shipping_options/free` sin `item_price` calcula con `item.price` ($38 cuando
  lo real era $34): se le pasa el precio cobrado.
- La comisión depende de la categoría Y del tramo de precio Y de la logística
  (FULL ≥$500 cobra 3.5 puntos menos). Se consulta con `logistic_type=fulfillment`
  a los precios muestra de `parametros.json`, y se guarda por tramo.

Un `TokenInvalido` aborta SOLO la cuenta afectada (sus tareas pendientes se
saltan) y queda anotado en el resumen; la otra cuenta sigue.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable

if __package__ in (None, ""):  # corrido como script: backend/ al path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sandbox_precios import _entorno  # noqa: E402

_entorno.cargar()  # idempotente; SIEMPRE antes de tocar services.* (instala candados)
from sandbox_precios import almacen, ml_http  # noqa: E402
from sandbox_precios.ml_http import TokenInvalido  # noqa: E402

log = logging.getLogger("laboratorio.extraer_ml")

ETAPAS = ("universo", "visitas_cuenta", "sugerencias", "precios", "envio", "promos",
          "price_to_win", "visitas", "comisiones")
MAX_WORKERS = 6
DIAS_VISITAS = 150            # máximo que acepta la API (con 180 responde 400)
INCREMENTAL_LAST = 8          # con serie de ≤7 días de atraso basta pedir 8 días
VIGENCIA_COMISIONES_DIAS = 7
DIAS_VENDIDAS = 150

ATRIBUTOS_MULTIGET = ("id,title,thumbnail,permalink,price,base_price,original_price,"
                      "available_quantity,sold_quantity,status,sub_status,shipping,"
                      "listing_type_id,tags,catalog_listing,catalog_product_id,category_id,"
                      "user_product_id,seller_custom_field,attributes,date_created,last_updated")
# Atributos de ficha que se conservan (la lista completa son ~30 por ítem y no
# sirven al precio). UNITS_PER_PACK sirve para comparar precio por pieza.
ATRIBUTOS_GUARDAR = {"SELLER_SKU", "BRAND", "MODEL", "GTIN", "UNITS_PER_PACK", "ITEM_CONDITION",
                     "PACKAGE_WEIGHT", "PACKAGE_LENGTH", "PACKAGE_WIDTH", "PACKAGE_HEIGHT"}


def _parametros() -> dict:
    try:
        return json.loads(Path(__file__).with_name("parametros.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _sku_de_item(item: dict) -> str | None:
    from services.inventario import _sku_de_item as f  # función pura del sync de producción
    return f(item)


def _hoy_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _fecha(s: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


class _Corrida:
    """Estado compartido de una extracción: cuentas abortadas, avisos y medición por etapa."""

    def __init__(self, dia: dt.date, incremental: bool):
        self.dia = dia
        self.incremental = incremental
        self.muertas: dict[str, str] = {}
        self.avisos: list[str] = []
        self.etapas: dict[str, dict] = {}
        self.sin_presupuesto = False
        self._lock = threading.Lock()

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def get(self, ruta: str, params: dict | None, cuenta: str) -> tuple[int | None, Any]:
        """GET con la cuenta; (None, motivo) si la cuenta ya fue abortada."""
        if cuenta in self.muertas:
            return None, "cuenta_abortada"
        if self.sin_presupuesto:
            return None, "presupuesto_agotado"
        try:
            return ml_http.get(ruta, params, cuenta)
        except ml_http.PresupuestoAgotado as exc:
            with self._lock:
                if not self.sin_presupuesto:
                    self.sin_presupuesto = True
                    self.avisos.append(str(exc))
                    log.error("ML: %s — se detienen las llamadas de esta corrida", exc)
            return None, "presupuesto_agotado"
        except TokenInvalido as exc:
            with self._lock:
                if cuenta not in self.muertas:
                    self.muertas[cuenta] = str(exc)
                    log.error("ML: %s abortada (token inválido, no se renueva): %s", cuenta, exc)
            return None, "token_invalido"

    def cuenta_viva(self, preferida: str = "BEKURA") -> str | None:
        for c in (preferida, *ml_http.CUENTAS):
            if c not in self.muertas:
                return c
        return None

    def aviso(self, texto: str) -> None:
        with self._lock:
            self.avisos.append(texto)
        log.warning("ML: %s", texto)

    def paralelo(self, tareas: list, fn: Callable[[Any], Any]) -> list:
        """Corre `fn` sobre las tareas con 6 hilos; una excepción queda como {"error": …}."""
        def _seguro(t):
            try:
                return fn(t)
            except Exception as exc:  # noqa: BLE001 — una tarea rota no tumba la etapa
                log.exception("ML: tarea falló")
                return {"error": f"{type(exc).__name__}: {exc}"[:300], "tarea": str(t)[:200]}
        if not tareas:
            return []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            return list(ex.map(_seguro, tareas))


# ── Universo ──────────────────────────────────────────────────────────────────
def _scan(c: _Corrida, cuenta: str) -> dict:
    """IDs active+paused de la cuenta con search_type=scan (el scroll es secuencial)."""
    uid = ml_http.UID[cuenta]
    salida: dict[str, Any] = {"cuenta": cuenta, "ids": {}, "totales": {}}
    for status in ("active", "paused"):
        ids: list[str] = []
        scroll = None
        total = None
        while True:
            params: dict[str, Any] = {"search_type": "scan", "limit": 100, "status": status}
            if scroll:
                params["scroll_id"] = scroll
            st, j = c.get(f"/users/{uid}/items/search", params, cuenta)
            if st != 200 or not isinstance(j, dict):
                if st is not None:
                    c.aviso(f"scan {cuenta}/{status}: HTTP {st} tras {len(ids)} ids")
                break
            total = (j.get("paging") or {}).get("total", total)
            res = j.get("results") or []
            ids.extend(res)
            scroll = j.get("scroll_id")
            if not res or not scroll:
                break
        unicos = list(dict.fromkeys(ids))
        if total is not None and len(unicos) != total:
            c.aviso(f"scan {cuenta}/{status}: {len(unicos)} ids distintos vs paging.total {total}")
        salida["ids"][status] = unicos
        salida["totales"][status] = total
    return salida


def _fila_universo(body: dict, cuenta: str, status_scan: str, leido_at: str) -> dict:
    ship = body.get("shipping") or {}
    lt = ship.get("logistic_type")
    attrs = {a.get("id"): a.get("value_name") for a in (body.get("attributes") or [])
             if a.get("id") in ATRIBUTOS_GUARDAR}
    return {
        "id": body.get("id"), "cuenta": cuenta, "sku": _sku_de_item(body),
        "titulo": body.get("title"), "thumbnail": body.get("thumbnail"), "permalink": body.get("permalink"),
        "price": body.get("price"), "base_price": body.get("base_price"),
        "original_price": body.get("original_price"),
        "available_quantity": body.get("available_quantity"), "sold_quantity": body.get("sold_quantity"),
        "status": body.get("status"), "sub_status": body.get("sub_status") or [],
        "logistic_type": lt, "es_full": lt == "fulfillment",
        "shipping_mode": ship.get("mode"), "free_shipping": ship.get("free_shipping"),
        "shipping_tags": ship.get("tags") or [],
        "listing_type_id": body.get("listing_type_id"), "tags": body.get("tags") or [],
        "catalog_listing": body.get("catalog_listing"), "catalog_product_id": body.get("catalog_product_id"),
        "category_id": body.get("category_id"), "user_product_id": body.get("user_product_id"),
        "seller_custom_field": body.get("seller_custom_field"), "atributos": attrs,
        "date_created": body.get("date_created"), "last_updated": body.get("last_updated"),
        "status_scan": status_scan, "leido_at": leido_at,
    }


def _universo(c: _Corrida) -> list[dict]:
    t0 = time.monotonic()
    g0 = ml_http.contadores()["get"]
    scans = c.paralelo(list(ml_http.CUENTAS), lambda cu: _scan(c, cu))
    tareas: list[tuple[str, str, list[str]]] = []
    totales: dict[str, Any] = {}
    for s in scans:
        if "error" in s:
            c.aviso(f"scan falló: {s['error']}")
            continue
        totales[s["cuenta"]] = s["totales"]
        for status, ids in s["ids"].items():
            for i in range(0, len(ids), 20):
                tareas.append((s["cuenta"], status, ids[i:i + 20]))

    def _multiget(t):
        cuenta, status, ids = t
        st, j = c.get("/items", {"ids": ",".join(ids), "attributes": ATRIBUTOS_MULTIGET}, cuenta)
        ahora = almacen.ahora_iso()
        if st != 200 or not isinstance(j, list):
            return [{"id": i, "cuenta": cuenta, "status_scan": status, "error": f"multiget HTTP {st}",
                     "leido_at": ahora} for i in ids]
        filas = []
        vistos = set()
        for e in j:
            body = e.get("body") or {}
            if e.get("code") == 200 and body.get("id"):
                filas.append(_fila_universo(body, cuenta, status, ahora))
                vistos.add(body["id"])
        for i in ids:  # nada desaparece en silencio: el que no vino queda con error
            if i not in vistos:
                filas.append({"id": i, "cuenta": cuenta, "status_scan": status,
                              "error": "multiget sin cuerpo", "leido_at": ahora})
        return filas

    lotes = c.paralelo(tareas, _multiget)
    filas: list[dict] = []
    for lote in lotes:
        if isinstance(lote, list):
            filas.extend(lote)
        else:
            c.aviso(f"multiget falló: {lote.get('error')}")
    # Un ítem puede salir dos veces si cambió de estado a media paginación.
    unicas = list({(f["cuenta"], f["id"]): f for f in filas}.values())
    n = almacen.escribir_jsonl(almacen.crudo("ml_universo.jsonl", c.dia), unicas)
    c.etapas["universo"] = {"ok": not any(cu in c.muertas for cu in ml_http.CUENTAS),
                            "filas": n, "llamadas": ml_http.contadores()["get"] - g0,
                            "duracion_s": round(time.monotonic() - t0, 1),
                            "paging_total": totales,
                            "con_error": sum(1 for f in unicas if f.get("error")),
                            "sin_sku": sum(1 for f in unicas if not f.get("error") and not f.get("sku"))}
    return unicas


def _cargar_universo(c: _Corrida) -> list[dict]:
    p = almacen.crudo("ml_universo.jsonl", c.dia)
    if not p.exists():
        p = almacen.ultimo_crudo("ml_universo.jsonl", max_dias=2)
    if not p:
        raise RuntimeError("no hay ml_universo.jsonl reciente: correr la etapa 'universo'")
    c.aviso(f"universo leído de {p.parent.name}/{p.name} (no se re-escaneó)")
    return almacen.leer_jsonl(p)


# ── Visitas ───────────────────────────────────────────────────────────────────
def _serie_desde_api(j: dict, desde_min: dt.date | None) -> tuple[list[list], list[list], dt.date | None]:
    """(serie rellena sin hoy, puntos crudos ordenados, fecha de hoy según la API)."""
    puntos = {}
    for r in j.get("results") or []:
        f = _fecha(r.get("date"))
        if f:
            puntos[f] = puntos.get(f, 0) + int(r.get("total") or 0)
    ini, hoy = _fecha(j.get("date_from")), _fecha(j.get("date_to"))
    crudos = [[f.isoformat(), v] for f, v in sorted(puntos.items())]
    if not ini or not hoy:
        return [], crudos, hoy
    d = max(ini, desde_min) if desde_min else ini
    serie = []
    while d < hoy:  # `date_to` es hoy a las 00:00Z: su punto es un día a medias
        serie.append([d.isoformat(), puntos.get(d, 0)])
        d += dt.timedelta(days=1)
    return serie, crudos, hoy


def _visitas(c: _Corrida, universo: list[dict]) -> None:
    t0 = time.monotonic()
    g0 = ml_http.contadores()["get"]
    full = [u for u in universo if u.get("es_full") and not u.get("error")]
    ayer = _hoy_utc() - dt.timedelta(days=1)

    def _una(u: dict) -> dict:
        iid, cuenta = u["id"], u["cuenta"]
        ruta_cache = almacen.ruta("cache", "visitas", f"{iid}.json")
        # La caché se lee SIEMPRE (también sin incremental) para no perder los días
        # que ya salieron de la ventana de 150 d de la API: ML no los vuelve a dar.
        previa = almacen.leer_json(ruta_cache, None)
        ultimo = _fecha((previa or {}).get("ultimo_dia"))
        last = INCREMENTAL_LAST if (c.incremental and previa and ultimo
                                    and (ayer - ultimo).days <= INCREMENTAL_LAST - 1) else DIAS_VISITAS
        st, j = c.get(f"/items/{iid}/visits/time_window", {"last": last, "unit": "day"}, cuenta)
        if st != 200 or not isinstance(j, dict):
            return {"id": iid, "cuenta": cuenta, "sku": u.get("sku"), "last": last,
                    "status": st, "error": str(j)[:200]}
        creado = _fecha(u.get("date_created"))
        serie, crudos, hoy = _serie_desde_api(j, creado)
        consolidada = {f: v for f, v in ((previa or {}).get("serie") or [])}
        consolidada.update({f: v for f, v in serie})  # lo recién leído manda sobre la caché
        ordenada = [[f, consolidada[f]] for f in sorted(consolidada)]
        almacen.escribir_json(ruta_cache, {"id": iid, "cuenta": cuenta, "sku": u.get("sku"),
                                           "actualizado_at": almacen.ahora_iso(),
                                           "ultimo_dia": ordenada[-1][0] if ordenada else None,
                                           "serie": ordenada})
        return {"id": iid, "cuenta": cuenta, "sku": u.get("sku"), "status": st, "last": last,
                "date_from": j.get("date_from"), "date_to": j.get("date_to"),
                "total_visits": j.get("total_visits"), "n_puntos_api": len(crudos),
                "dias_serie": len(ordenada), "puntos": crudos}

    filas = c.paralelo(full, _una)
    n = almacen.escribir_jsonl(almacen.crudo("ml_visitas.jsonl", c.dia), filas)
    # Serie consolidada de TODO lo que hay en caché (incluye ítems de corridas previas).
    serie_total: dict[str, list] = {}
    base = almacen.ruta("cache", "visitas", "x").parent
    for p in base.glob("*.json"):
        d = almacen.leer_json(p, None) or {}
        if d.get("id") and d.get("serie"):
            serie_total[d["id"]] = d["serie"]
    almacen.escribir_json(almacen.ruta("cache", "visitas_serie.json"), serie_total)
    ok = [f for f in filas if f.get("status") == 200]
    c.etapas["visitas"] = {"ok": len(ok) == len(filas), "filas": n, "llamadas": ml_http.contadores()["get"] - g0,
                           "duracion_s": round(time.monotonic() - t0, 1),
                           "full_pedidas": len(full), "con_serie": len(ok),
                           "incrementales": sum(1 for f in ok if f.get("last") == INCREMENTAL_LAST),
                           "series_en_cache": len(serie_total),
                           "fallidas": len(filas) - len(ok)}


def _visitas_cuenta(c: _Corrida) -> None:
    t0 = time.monotonic()
    filas = []
    for cuenta in ml_http.CUENTAS:
        uid = ml_http.UID[cuenta]
        st, j = c.get(f"/users/{uid}/items_visits/time_window", {"last": DIAS_VISITAS, "unit": "day"}, cuenta)
        if st != 200 or not isinstance(j, dict):
            filas.append({"cuenta": cuenta, "status": st, "error": str(j)[:200]})
            continue
        serie, crudos, _ = _serie_desde_api(j, None)
        filas.append({"cuenta": cuenta, "uid": uid, "status": st, "date_from": j.get("date_from"),
                      "date_to": j.get("date_to"), "total_visits": j.get("total_visits"),
                      "n_puntos_api": len(crudos), "serie": serie})
    n = almacen.escribir_jsonl(almacen.crudo("ml_visitas_cuenta.jsonl", c.dia), filas)
    c.etapas["visitas_cuenta"] = {"ok": all(f.get("status") == 200 for f in filas), "filas": n,
                                  "duracion_s": round(time.monotonic() - t0, 1)}


# ── Precios ───────────────────────────────────────────────────────────────────
def _vendidas_150d() -> set[str]:
    """item_id de ML con alguna venta en 150 d: del crudo de kubera del día o un SELECT."""
    p = almacen.ultimo_crudo("kubera_ventas_dia.json", max_dias=2)
    if p:
        d = almacen.leer_json(p, {}) or {}
        return {f["item_id"] for f in d.get("ml_dia") or [] if f.get("item_id") and (f.get("units_sold") or 0) > 0}
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        "select distinct item_id from channel.sales_daily_completa "
        "where canal = 'mercado_libre' and units_sold > 0 and item_id is not null "
        "and date >= (now() at time zone 'America/Mexico_City')::date - %s", (DIAS_VENDIDAS,))
    return {f["item_id"] for f in filas}


def _promo_vigente(prices: Any, sale: Any) -> dict | None:
    """La promoción que explica el precio cobrado hoy, con su vigencia (de `/prices`)."""
    if not isinstance(sale, dict):
        return None
    meta = sale.get("metadata") or {}
    monto = sale.get("amount")
    promo = {"tipo": meta.get("promotion_type"), "campaign_id": meta.get("campaign_id"),
             "promotion_id": meta.get("promotion_id"), "monto": monto,
             "regular": sale.get("regular_amount"), "inicio": None, "fin": None}
    lista = (prices or {}).get("prices") if isinstance(prices, dict) else None
    for p in lista or []:
        if p.get("type") == "promotion" and monto is not None and p.get("amount") == monto:
            cond = p.get("conditions") or {}
            promo["inicio"], promo["fin"] = cond.get("start_time"), cond.get("end_time")
            break
    return promo if (promo["tipo"] or promo["regular"]) else None


def _precios(c: _Corrida, universo: list[dict]) -> dict[str, dict]:
    t0 = time.monotonic()
    g0 = ml_http.contadores()["get"]
    vivas = [u for u in universo if not u.get("error")]
    activas = [u for u in vivas if u.get("status") == "active"]
    try:
        vendidas = _vendidas_150d()
    except Exception as exc:  # noqa: BLE001
        vendidas = set()
        c.aviso(f"no se pudo leer ventas 150 d para las pausadas FULL: {exc}")
    pausadas_full = [u for u in vivas if u.get("status") != "active" and u.get("es_full")
                     and u["id"] in vendidas]

    def _una(t: tuple[dict, bool]) -> dict:
        u, con_prices = t
        iid, cuenta = u["id"], u["cuenta"]
        fila: dict[str, Any] = {"id": iid, "cuenta": cuenta, "sku": u.get("sku"),
                                "status_item": u.get("status"), "es_full": u.get("es_full"),
                                "price_item": u.get("price"), "leido_at": almacen.ahora_iso()}
        prices = None
        if con_prices:
            st, prices = c.get(f"/items/{iid}/prices", None, cuenta)
            fila["prices_status"] = st
            fila["prices"] = prices if st == 200 else None
        st, sale = c.get(f"/items/{iid}/sale_price", {"context": "channel_marketplace"}, cuenta)
        fila["sale_price_status"] = st
        fila["sale_price"] = sale if st == 200 else None
        if st == 200 and isinstance(sale, dict):
            fila["precio_cobrado"] = sale.get("amount")
            fila["precio_regular"] = sale.get("regular_amount")
        else:
            fila["precio_cobrado"] = None
            fila["precio_regular"] = None
        fila["promo"] = _promo_vigente(fila.get("prices"), fila.get("sale_price"))
        return fila

    tareas = [(u, True) for u in activas] + [(u, False) for u in pausadas_full]
    filas = c.paralelo(tareas, _una)
    n = almacen.escribir_jsonl(almacen.crudo("ml_precios.jsonl", c.dia), filas)
    c.etapas["precios"] = {"ok": all("error" not in f for f in filas), "filas": n,
                           "llamadas": ml_http.contadores()["get"] - g0,
                           "duracion_s": round(time.monotonic() - t0, 1),
                           "activas": len(activas), "pausadas_full_vendidas_150d": len(pausadas_full),
                           "con_precio_cobrado": sum(1 for f in filas if f.get("precio_cobrado") is not None),
                           "con_promo": sum(1 for f in filas if f.get("promo"))}
    return {f["id"]: f for f in filas if f.get("id")}


def _cargar_precios(c: _Corrida) -> dict[str, dict]:
    p = almacen.crudo("ml_precios.jsonl", c.dia)
    return {f["id"]: f for f in almacen.leer_jsonl(p) if f.get("id")} if p.exists() else {}


# ── Promociones ───────────────────────────────────────────────────────────────
def _resumen_promos(lista: Any) -> dict:
    """Lo que el optimizador usa de las promociones: sugerido de ML y co-financiamiento."""
    if not isinstance(lista, list):
        return {}
    pd = [p for p in lista if p.get("type") == "PRICE_DISCOUNT"]
    sug = [p.get("suggested_discounted_price") for p in pd if p.get("suggested_discounted_price")]
    cofin = [p for p in lista if (p.get("meli_percentage") or 0) > 0]
    return {"tipos": sorted({f"{p.get('type')}:{p.get('status')}" for p in lista}),
            "price_discount_sugerido": min(sug) if sug else None,
            "price_discount_min": min((p.get("min_discounted_price") for p in pd
                                       if p.get("min_discounted_price")), default=None),
            "price_discount_max": max((p.get("max_discounted_price") for p in pd
                                       if p.get("max_discounted_price")), default=None),
            "cofinanciadas": [{"tipo": p.get("type"), "id": p.get("id"), "precio": p.get("price"),
                               "meli_pct": p.get("meli_percentage"), "seller_pct": p.get("seller_percentage")}
                              for p in cofin],
            "unhealthy_stock": any(p.get("type") == "UNHEALTHY_STOCK" for p in lista),
            "iniciadas": [{"tipo": p.get("type"), "id": p.get("id"), "nombre": p.get("name"),
                           "fin": p.get("finish_date")} for p in lista if p.get("status") == "started"]}


def _promos(c: _Corrida, universo: list[dict]) -> None:
    t0 = time.monotonic()
    g0 = ml_http.contadores()["get"]
    objetivo = [u for u in universo if not u.get("error") and u.get("status") == "active" and u.get("es_full")]

    def _una(u: dict) -> dict:
        st, j = c.get(f"/seller-promotions/items/{u['id']}", {"app_version": "v2"}, u["cuenta"])
        return {"id": u["id"], "cuenta": u["cuenta"], "sku": u.get("sku"), "status": st,
                "promociones": j if st == 200 else None, "error": None if st == 200 else str(j)[:200],
                "resumen": _resumen_promos(j) if st == 200 else {}}

    filas = c.paralelo(objetivo, _una)
    n = almacen.escribir_jsonl(almacen.crudo("ml_promos.jsonl", c.dia), filas)
    ok = [f for f in filas if f.get("status") == 200]
    c.etapas["promos"] = {"ok": len(ok) == len(filas), "filas": n, "llamadas": ml_http.contadores()["get"] - g0,
                          "duracion_s": round(time.monotonic() - t0, 1),
                          "con_sugerido": sum(1 for f in ok if f["resumen"].get("price_discount_sugerido")),
                          "con_cofinanciamiento": sum(1 for f in ok if f["resumen"].get("cofinanciadas")),
                          "unhealthy_stock": sum(1 for f in ok if f["resumen"].get("unhealthy_stock"))}


# ── Envío ─────────────────────────────────────────────────────────────────────
def _envio(c: _Corrida, universo: list[dict], precios: dict[str, dict]) -> None:
    t0 = time.monotonic()
    g0 = ml_http.contadores()["get"]
    objetivo = [u for u in universo if not u.get("error") and u.get("status") == "active" and u.get("es_full")]

    def _una(u: dict) -> dict:
        cobrado = (precios.get(u["id"]) or {}).get("precio_cobrado")
        precio, fuente = (cobrado, "sale_price") if cobrado else (u.get("price"), "item_price")
        params = {"item_id": u["id"]}
        if precio:
            params["item_price"] = precio
        st, j = c.get(f"/users/{ml_http.UID[u['cuenta']]}/shipping_options/free", params, u["cuenta"])
        cov = ((j or {}).get("coverage") or {}).get("all_country") or {} if isinstance(j, dict) else {}
        return {"id": u["id"], "cuenta": u["cuenta"], "sku": u.get("sku"), "status": st,
                "item_price": precio, "fuente_precio": fuente,
                "list_cost": cov.get("list_cost"), "billable_weight": cov.get("billable_weight"),
                "coverage": (j or {}).get("coverage") if isinstance(j, dict) else None,
                "error": None if st == 200 else str(j)[:200]}

    filas = c.paralelo(objetivo, _una)
    n = almacen.escribir_jsonl(almacen.crudo("ml_envio.jsonl", c.dia), filas)
    c.etapas["envio"] = {"ok": all(f.get("status") == 200 for f in filas), "filas": n,
                         "llamadas": ml_http.contadores()["get"] - g0,
                         "duracion_s": round(time.monotonic() - t0, 1),
                         "con_billable_weight": sum(1 for f in filas if f.get("billable_weight")),
                         "con_precio_cobrado": sum(1 for f in filas if f.get("fuente_precio") == "sale_price")}


# ── Comisiones por categoría y tramo (caché de 7 días) ────────────────────────
def _tramo(precio: float, tramos: list[float]) -> str:
    return str(int(max(t for t in tramos if precio >= t)))


def _comisiones(c: _Corrida, universo: list[dict]) -> None:
    t0 = time.monotonic()
    g0 = ml_http.contadores()["get"]
    par = _parametros().get("mercado_libre", {})
    muestras = [float(p) for p in par.get("precios_muestra_comision") or [199, 399, 599, 1199]]
    # Llaves de MUESTREO (una por precio de muestra), NO `tramos_comision_precio`:
    # esos son los tramos del respaldo ([0, 500] desde el 28-sep) y con ellos dos
    # muestras caerían en la misma llave → `len(tramos) != len(muestras)` y TODAS
    # las categorías se marcarían incompletas.
    tramos = [float(t) for t in par.get("tramos_muestreo_comision") or [0, 299, 500, 1000]]
    if len({_tramo(p, tramos) for p in muestras}) != len(muestras):
        raise ValueError("tramos_muestreo_comision debe dar una llave distinta a cada precio de muestra")
    ruta = almacen.ruta("cache", "comisiones.json")
    cache: dict[str, dict] = almacen.leer_json(ruta, {}) or {}
    cats = sorted({u["category_id"] for u in universo
                   if not u.get("error") and u.get("es_full") and u.get("category_id")})
    limite = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=VIGENCIA_COMISIONES_DIAS)

    def _vigente(cat: str) -> bool:
        e = cache.get(cat) or {}
        try:
            return c.incremental and bool(e.get("tramos")) and \
                dt.datetime.fromisoformat(e["consultado_at"]) >= limite
        except (KeyError, TypeError, ValueError):
            return False

    pendientes = [cat for cat in cats if not _vigente(cat)]
    tareas = [(cat, p) for cat in pendientes for p in muestras]

    def _una(t: tuple[str, float]) -> dict:
        cat, precio = t
        cuenta = c.cuenta_viva()
        if not cuenta:
            return {"cat": cat, "precio": precio, "status": None}
        st, j = c.get("/sites/MLM/listing_prices",
                      {"price": int(precio) if precio == int(precio) else precio,
                       "listing_type_id": "gold_pro", "category_id": cat,
                       "logistic_type": "fulfillment"}, cuenta)
        if isinstance(j, list):  # sin listing_type_id la API responde lista; por si acaso
            j = next((x for x in j if x.get("listing_type_id") == "gold_pro"), None)
        if st != 200 or not isinstance(j, dict):
            return {"cat": cat, "precio": precio, "status": st, "error": str(j)[:160]}
        det = j.get("sale_fee_details") or {}
        pct = det.get("percentage_fee")
        pct = float(pct) / 100 if pct is not None else (
            float(j["sale_fee_amount"]) / precio if j.get("sale_fee_amount") is not None else None)
        return {"cat": cat, "precio": precio, "status": st, "pct": round(pct, 5) if pct is not None else None,
                "fijo": det.get("fixed_fee"), "monto": j.get("sale_fee_amount")}

    resultados = c.paralelo(tareas, _una)
    ahora = almacen.ahora_iso()
    nuevas: dict[str, dict] = {}
    for r in resultados:
        if "cat" not in r:
            continue
        e = nuevas.setdefault(r["cat"], {"consultado_at": ahora, "listing_type_id": "gold_pro",
                                         "logistic_type": "fulfillment", "muestras": [], "tramos": {}})
        e["muestras"].append({k: r.get(k) for k in ("precio", "status", "pct", "fijo", "monto", "error")})
        if r.get("pct") is not None:
            e["tramos"][_tramo(r["precio"], tramos)] = r["pct"]
    fallidas = 0
    for cat, e in nuevas.items():
        if len(e["tramos"]) == len(muestras):
            cache[cat] = e
        else:  # a medias no pisa una entrada buena anterior; queda para la próxima corrida
            fallidas += 1
            if not cache.get(cat, {}).get("tramos"):
                cache[cat] = {**e, "consultado_at": None}
    almacen.escribir_json(ruta, cache)
    completas = sum(1 for cat in cats if len((cache.get(cat) or {}).get("tramos") or {}) == len(muestras))
    c.etapas["comisiones"] = {"ok": fallidas == 0, "filas": len(nuevas),
                              "llamadas": ml_http.contadores()["get"] - g0,
                              "duracion_s": round(time.monotonic() - t0, 1),
                              "categorias_full": len(cats), "consultadas_hoy": len(pendientes),
                              "de_cache": len(cats) - len(pendientes), "con_todos_los_tramos": completas,
                              "incompletas": fallidas}


# ── Sugerencias y price_to_win ────────────────────────────────────────────────
def _sugerencias(c: _Corrida) -> None:
    t0 = time.monotonic()
    g0 = ml_http.contadores()["get"]
    filas: list[dict] = []
    pares: list[tuple[str, str]] = []
    for cuenta in ml_http.CUENTAS:
        uid = ml_http.UID[cuenta]
        ids: list[str] = []
        offset = 0
        st, j = None, None
        while True:
            st, j = c.get(f"/suggestions/user/{uid}/items", {"limit": 100, "offset": offset}, cuenta)
            if st != 200 or not isinstance(j, dict):
                break
            nuevos = [i for i in (j.get("items") or []) if i not in ids]
            ids.extend(nuevos)
            total = j.get("total") or 0
            if not nuevos or len(ids) >= total:
                break
            offset = len(ids)
        if st == 200:
            filas.append({"tipo": "lista", "cuenta": cuenta, "status": st, "total": len(ids), "items": ids})
        else:
            filas.append({"tipo": "lista", "cuenta": cuenta, "status": st, "error": str(j)[:200],
                          "total": len(ids), "items": ids})
        pares.extend((cuenta, i) for i in ids)

    def _det(t: tuple[str, str]) -> dict:
        cuenta, iid = t
        st, j = c.get(f"/suggestions/items/{iid}/details", None, cuenta)
        return {"tipo": "detalle", "id": iid, "cuenta": cuenta, "status": st,
                "detalle": j if st == 200 else None, "error": None if st == 200 else str(j)[:200]}

    filas.extend(c.paralelo(pares, _det))
    n = almacen.escribir_jsonl(almacen.crudo("ml_sugerencias.jsonl", c.dia), filas)
    det = [f for f in filas if f.get("tipo") == "detalle"]
    c.etapas["sugerencias"] = {"ok": True, "filas": n, "llamadas": ml_http.contadores()["get"] - g0,
                               "duracion_s": round(time.monotonic() - t0, 1),
                               "items": len(pares), "detalles_ok": sum(1 for f in det if f.get("status") == 200)}


def _price_to_win(c: _Corrida, universo: list[dict]) -> None:
    t0 = time.monotonic()
    g0 = ml_http.contadores()["get"]
    objetivo = [u for u in universo if not u.get("error") and u.get("status") == "active"
                and u.get("catalog_listing")]

    def _una(u: dict) -> dict:
        st, j = c.get(f"/items/{u['id']}/price_to_win", {"version": "v2"}, u["cuenta"])
        return {"id": u["id"], "cuenta": u["cuenta"], "sku": u.get("sku"), "status": st,
                "catalog_product_id": u.get("catalog_product_id"),
                "price_to_win": j if st == 200 else None, "error": None if st == 200 else str(j)[:200]}

    filas = c.paralelo(objetivo, _una)
    n = almacen.escribir_jsonl(almacen.crudo("ml_price_to_win.jsonl", c.dia), filas)
    c.etapas["price_to_win"] = {"ok": all(f.get("status") == 200 for f in filas), "filas": n,
                                "llamadas": ml_http.contadores()["get"] - g0,
                                "duracion_s": round(time.monotonic() - t0, 1),
                                "catalogo_activas": len(objetivo),
                                "ganando": sum(1 for f in filas
                                               if (f.get("price_to_win") or {}).get("status") == "winning")}


# ── Conteos: API contra kubera ────────────────────────────────────────────────
def _conteos(universo: list[dict], dia: dt.date) -> dict:
    api: dict[str, dict[str, int]] = {}
    for u in universo:
        if u.get("error"):
            continue
        k = f"{u.get('status')}:{'full' if u.get('es_full') else (u.get('logistic_type') or '?')}"
        api.setdefault(u["cuenta"], {})
        api[u["cuenta"]][k] = api[u["cuenta"]].get(k, 0) + 1
    salida: dict[str, Any] = {"api": api}
    p = almacen.crudo("kubera_publicaciones.json", dia)
    if not p.exists():
        p = almacen.ultimo_crudo("kubera_publicaciones.json", max_dias=2)
    if p:
        filas = [f for f in (almacen.leer_json(p, {}) or {}).get("filas") or [] if f.get("canal") == "mercado_libre"]
        kub: dict[str, dict[str, int]] = {}
        for f in filas:
            lt = f.get("logistic_type")
            k = f"{(f.get('situacion') or '?').lower()}:{'full' if lt == 'fulfillment' else (lt or '?')}"
            kub.setdefault(f["cuenta"], {})
            kub[f["cuenta"]][k] = kub[f["cuenta"]].get(k, 0) + 1
        ids_api = {(u["cuenta"], u["id"]) for u in universo if not u.get("error")}
        ids_kub_vivos = {(f["cuenta"], f["listing_id"]) for f in filas
                         if f.get("listing_id") and (f.get("situacion") or "").lower() in ("active", "paused")}
        ids_kub = {(f["cuenta"], f["listing_id"]) for f in filas if f.get("listing_id")}
        activas_sin_fila = [u for u in universo if not u.get("error") and u.get("status") == "active"
                            and (u["cuenta"], u["id"]) not in ids_kub]
        salida.update({
            "kubera": kub,
            "api_sin_fila_kubera": len(ids_api - ids_kub),
            "api_activas_sin_fila_kubera": len(activas_sin_fila),
            "ejemplos_activas_sin_fila": [(u["cuenta"], u["id"], u.get("sku")) for u in activas_sin_fila[:10]],
            "kubera_vivas_fuera_de_api": len(ids_kub_vivos - ids_api),
        })
    return salida


# ── Orquestación ──────────────────────────────────────────────────────────────
def extraer(dia: dt.date | None = None, incremental: bool = True,
            solo: Iterable[str] | None = None) -> dict:
    """Corre las etapas (todas, o las de `solo`) y devuelve el resumen.

    `incremental`: visitas con `last=8` si la serie en caché tiene ≤7 días de
    atraso, y comisiones solo para categorías sin consulta en 7 días. Con False
    se pide todo de nuevo (150 días de visitas, todas las comisiones).
    """
    dia = dia or almacen.hoy_cdmx()
    etapas = [e for e in ETAPAS if not solo or e in set(solo)]
    desconocidas = sorted(set(solo or []) - set(ETAPAS))
    c = _Corrida(dia, incremental)
    if desconocidas:
        c.aviso(f"etapas desconocidas ignoradas: {desconocidas}")
    t0 = time.monotonic()
    cont0 = ml_http.contadores()
    universo: list[dict] = []
    precios: dict[str, dict] = {}
    try:
        universo = _universo(c) if "universo" in etapas else _cargar_universo(c)
    except Exception as exc:  # noqa: BLE001
        c.aviso(f"universo no disponible: {type(exc).__name__}: {exc}")
        etapas = [e for e in etapas if e in ("visitas_cuenta", "sugerencias")]
    pasos: dict[str, Callable[[], Any]] = {
        "visitas_cuenta": lambda: _visitas_cuenta(c),
        "sugerencias": lambda: _sugerencias(c),
        "precios": lambda: precios.update(_precios(c, universo)),
        "envio": lambda: _envio(c, universo, precios or _cargar_precios(c)),
        "promos": lambda: _promos(c, universo),
        "price_to_win": lambda: _price_to_win(c, universo),
        "visitas": lambda: _visitas(c, universo),
        "comisiones": lambda: _comisiones(c, universo),
    }
    for e in etapas:
        if e == "universo":
            continue
        if len(c.muertas) == len(ml_http.CUENTAS):
            c.aviso(f"etapa {e} saltada: las dos cuentas abortadas")
            continue
        try:
            log.info("ML: etapa %s", e)
            pasos[e]()
        except Exception as exc:  # noqa: BLE001 — una etapa rota no tumba las demás
            log.exception("ML: etapa %s falló", e)
            c.etapas[e] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    cont1 = ml_http.contadores()
    resumen = {
        "etapa": "extraer_ml", "dia": dia.isoformat(), "generado_at": almacen.ahora_iso(),
        "incremental": incremental, "etapas_pedidas": etapas,
        "ok": not c.muertas and not c.sin_presupuesto and all(v.get("ok") for v in c.etapas.values()),
        "duracion_s": round(time.monotonic() - t0, 1),
        "etapas": c.etapas, "cuentas_abortadas": c.muertas, "avisos": c.avisos,
        "contadores_ml_api": {k: cont1[k] - cont0.get(k, 0) for k in cont1},
        "presupuesto_ml": ml_http.presupuesto(),
        "conteos": _conteos(universo, dia) if universo else {},
        "frescura": {"ml_listings": almacen.ahora_iso() if "universo" in etapas else None,
                     "visitas_api": almacen.ahora_iso() if "visitas" in c.etapas else None},
    }
    if solo:  # corrida parcial: se conservan las etapas previas del día en el resumen
        previo = almacen.leer_json(almacen.crudo("extraer_ml_resumen.json", dia), {}) or {}
        resumen["etapas"] = {**(previo.get("etapas") or {}), **c.etapas}
    almacen.escribir_json(almacen.crudo("extraer_ml_resumen.json", dia), resumen)
    return resumen


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description="Extracción de la API de ML (solo GET) a crudo/<día>/")
    ap.add_argument("--solo", nargs="*", choices=ETAPAS, help="etapas a correr (default: todas)")
    ap.add_argument("--completo", action="store_true",
                    help="no incremental: 150 d de visitas y todas las comisiones de nuevo")
    args = ap.parse_args()
    r = extraer(incremental=not args.completo, solo=args.solo)
    print(json.dumps({k: r[k] for k in ("ok", "duracion_s", "etapas", "cuentas_abortadas",
                                        "contadores_ml_api", "conteos", "avisos")},
                     ensure_ascii=False, indent=1, default=str))
