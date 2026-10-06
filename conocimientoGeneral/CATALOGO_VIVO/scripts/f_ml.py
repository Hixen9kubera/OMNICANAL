"""
f_ml.py — Lo que hay publicado en Mercado Libre (BEKURA y SANCORFASHION), EN VIVO.

Todo son GET a `api.mercadolibre.com`:

  1. CENSO   `/users/{id}/items/search` con `search_type=scan`, por estado. Es el
             catálogo que ML dice tener — no nuestra bitácora de publicaciones,
             que solo conoce lo que publicó el panel (le faltaban 754).
  2. DETALLE `/items?ids=` de 20 en 20: título, foto, precio, estado, logística,
             categoría y el SKU (seller_custom_field → SELLER_SKU → variaciones).
  3. ECONOMÍA, solo de las ACTIVAS (las pausadas no venden), tres lecturas:
       · `/items/{id}/sale_price`           el precio que de verdad se cobra hoy
       · `/sites/MLM/listing_prices`        la comisión de ML a ESE precio
       · `/users/{id}/shipping_options/free` el envío que paga el vendedor
                                             (solo si la publicación lo ofrece gratis)

EL CUPO ES COMPARTIDO. La API de ML limita por aplicación, y es la misma app de los
webhooks de ventas. Por eso todo pasa por un freno de 4 peticiones por segundo y
ante un 429 se frenan TODOS los hilos.

EL TOKEN NO SE RENUEVA (ver `tokens_kubera.py`). Ante un 401 se relee una vez de
kubera; si sigue en 401, esa cuenta se reporta como NO LEÍDA.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import tokens_kubera
from comun import Cfg, Red, ahora_iso, aviso, escribir_json, num

API = "https://api.mercadolibre.com"
CUENTAS = {"BEKURA": "Kubera", "SANCORFASHION": "San Corpe"}
ESTADOS = ("active", "paused", "under_review")
CAMPOS = ("id,title,thumbnail,permalink,price,base_price,original_price,available_quantity,"
          "sold_quantity,status,sub_status,shipping,listing_type_id,catalog_listing,"
          "category_id,seller_custom_field,attributes,variations,date_created,last_updated")
HILOS = 5


class TokenMuerto(RuntimeError):
    pass


class _Cuenta:
    """Una cuenta de ML con su token, y la relectura única ante un 401."""

    def __init__(self, cfg: Cfg, red: Red, codigo: str, acceso: str):
        self.cfg, self.red, self.codigo = cfg, red, codigo
        self._acceso = acceso
        self._releido = False
        self._lock = threading.Lock()
        self.uid: str | None = None

    def get(self, ruta: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        for _ in range(3):
            usado = self._acceso
            r = self.red.get(API + ruta, params=params,
                             headers={"Authorization": f"Bearer {usado}"})
            if r.status_code != 401:
                try:
                    return r.status_code, r.json()
                except ValueError:
                    return r.status_code, r.text
            with self._lock:
                if self._acceso != usado:
                    continue                      # otro hilo ya lo releyó
                if self._releido:
                    raise TokenMuerto(f"{self.codigo}: 401 con el token releído (no se renueva)")
                self._releido = True
                nuevo = (tokens_kubera.mercado_libre(self.cfg).get(self.codigo) or {}).get("acceso")
                if not nuevo or nuevo == usado:
                    raise TokenMuerto(f"{self.codigo}: token caducado y producción no lo ha renovado")
                self._acceso = nuevo
        raise TokenMuerto(f"{self.codigo}: 401 persistente")


def _limpio(v: Any) -> str | None:
    s = str(v or "").strip()
    return s if s and len(s) <= 100 and not any(ch.isspace() for ch in s) else None


def _skus(item: dict[str, Any]) -> list[str]:
    """Todos los SKUs que nombra una publicación, el principal primero."""
    vistos: list[str] = []

    def _suma(v: Any) -> None:
        s = _limpio(v)
        if s and s not in vistos:
            vistos.append(s)

    _suma(item.get("seller_custom_field"))
    for a in item.get("attributes") or []:
        if a.get("id") == "SELLER_SKU":
            _suma(a.get("value_name"))
    for var in item.get("variations") or []:
        _suma(var.get("seller_custom_field"))
        for a in var.get("attributes") or []:
            if a.get("id") == "SELLER_SKU":
                _suma(a.get("value_name"))
    return vistos


def _fila(b: dict[str, Any], cuenta: str) -> dict[str, Any]:
    envio = b.get("shipping") or {}
    logistica = envio.get("logistic_type")
    skus = _skus(b)
    foto = b.get("thumbnail") or ""
    if foto.startswith("http://"):
        foto = "https://" + foto[len("http://"):]
    return {
        "sku": skus[0] if skus else None, "skus": skus, "id": b.get("id"),
        "cuenta": cuenta, "titulo": b.get("title"), "imagen": foto or None,
        "precio": num(b.get("price")), "precio_original": num(b.get("original_price")),
        "moneda": "MXN", "estado": b.get("status"), "subestado": b.get("sub_status") or [],
        "a_la_venta": b.get("status") == "active",
        "logistica": logistica, "es_full": logistica == "fulfillment",
        "envio_gratis": bool(envio.get("free_shipping")),
        "tipo_publicacion": b.get("listing_type_id"), "categoria": b.get("category_id"),
        "catalogo": bool(b.get("catalog_listing")),
        "stock_canal": b.get("available_quantity"), "vendidas": b.get("sold_quantity"),
        "variaciones": len(b.get("variations") or []),
        "link": b.get("permalink"), "creado": b.get("date_created"),
        "editado": b.get("last_updated"),
    }


def _censar(c: _Cuenta, avisos: list[str]) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    st, yo = c.get("/users/me")
    if st != 200:
        raise RuntimeError(f"{c.codigo}: /users/me respondió HTTP {st}")
    c.uid = str(yo.get("id"))
    ids: list[tuple[str, str]] = []
    totales: dict[str, Any] = {}
    for estado in ESTADOS:
        desplaza, vistos = None, []
        total = None
        while True:
            params: dict[str, Any] = {"search_type": "scan", "limit": 100, "status": estado}
            if desplaza:
                params["scroll_id"] = desplaza
            st, j = c.get(f"/users/{c.uid}/items/search", params)
            if st != 200 or not isinstance(j, dict):
                avisos.append(f"{c.codigo}/{estado}: el censo se cortó con HTTP {st} tras {len(vistos)}")
                break
            total = (j.get("paging") or {}).get("total", total)
            lote = j.get("results") or []
            vistos.extend(lote)
            desplaza = j.get("scroll_id")
            if not lote or not desplaza:
                break
        unicos = list(dict.fromkeys(vistos))
        if total is not None and len(unicos) != total:
            avisos.append(f"{c.codigo}/{estado}: {len(unicos)} leídas de {total} que declara ML")
        totales[estado] = {"declarado": total, "leido": len(unicos)}
        ids.extend((i, estado) for i in unicos)
        aviso(f"ml {c.codigo}: {estado} {len(unicos)} (ML declara {total})")
    return ids, totales


def _economia(c: _Cuenta, f: dict[str, Any], cache: dict[tuple, dict], lock: threading.Lock) -> None:
    iid = f["id"]
    st, sp = c.get(f"/items/{iid}/sale_price", {"context": "channel_marketplace"})
    if st == 200 and isinstance(sp, dict) and sp.get("amount") is not None:
        f["precio_cobrado"] = num(sp.get("amount"))
        f["precio_regular"] = num(sp.get("regular_amount"))
        meta = sp.get("metadata") or {}
        if meta.get("promotion_type") or meta.get("campaign_id"):
            f["promocion"] = meta.get("promotion_type") or "campaña"
    precio = f.get("precio_cobrado") or f.get("precio")
    if not precio or not f.get("categoria"):
        return
    llave = (round(precio, 2), f["categoria"], f.get("tipo_publicacion"), f.get("logistica"))
    with lock:
        com = cache.get(llave)
    if com is None:
        params = {"price": precio, "category_id": f["categoria"],
                  "listing_type_id": f.get("tipo_publicacion") or "gold_pro"}
        if f.get("logistica"):
            params["logistic_type"] = f["logistica"]
        st, j = c.get("/sites/MLM/listing_prices", params)
        if isinstance(j, list):
            j = next((x for x in j if x.get("listing_type_id") == params["listing_type_id"]), None)
        com = {}
        if st == 200 and isinstance(j, dict) and j.get("sale_fee_amount") is not None:
            det = j.get("sale_fee_details") or {}
            com = {"total": num(j.get("sale_fee_amount")),
                   "porcentaje": num(det.get("percentage_fee")),
                   "fijo": num(det.get("fixed_fee")),
                   "fuente": "ML · listing_prices al precio cobrado"}
        with lock:
            cache[llave] = com
    if com:
        f["comision"] = com
    if f.get("envio_gratis"):
        st, j = c.get(f"/users/{c.uid}/shipping_options/free",
                      {"item_id": iid, "item_price": precio})
        cob = ((j or {}).get("coverage") or {}).get("all_country") or {} if isinstance(j, dict) else {}
        if st == 200 and cob.get("list_cost") is not None:
            f["envio"] = {"costo": num(cob.get("list_cost")),
                          "peso_facturable_g": cob.get("billable_weight"),
                          "fuente": "ML · shipping_options/free"}


def extraer(cfg: Cfg, salida: Path, con_economia: bool = True) -> dict[str, Any]:
    tokens = tokens_kubera.mercado_libre(cfg)
    if not tokens:
        raise RuntimeError("no hay token legible de Mercado Libre en kubera")
    red = Red(rps=4.0, timeout=30.0)
    inicio = ahora_iso()
    avisos: list[str] = []
    filas: list[dict[str, Any]] = []
    resumen_cuentas: dict[str, Any] = {}

    for codigo in CUENTAS:
        if codigo not in tokens:
            avisos.append(f"{codigo}: sin token en kubera — cuenta NO leída")
            resumen_cuentas[codigo] = {"leida": False, "motivo": "sin token"}
            continue
        c = _Cuenta(cfg, red, codigo, tokens[codigo]["acceso"])
        try:
            ids, totales = _censar(c, avisos)
            lotes = [ids[i:i + 20] for i in range(0, len(ids), 20)]

            def _detalle(lote: list[tuple[str, str]], _c: _Cuenta = c) -> list[dict[str, Any]]:
                st, j = _c.get("/items", {"ids": ",".join(i for i, _ in lote), "attributes": CAMPOS})
                if st != 200 or not isinstance(j, list):
                    return [{"id": i, "cuenta": _c.codigo, "error": f"detalle HTTP {st}"}
                            for i, _ in lote]
                listos, salida_ = set(), []
                for e in j:
                    b = e.get("body") or {}
                    if e.get("code") == 200 and b.get("id"):
                        salida_.append(_fila(b, _c.codigo))
                        listos.add(b["id"])
                # nada desaparece en silencio: lo que no vino queda con su error
                salida_.extend({"id": i, "cuenta": _c.codigo, "error": "detalle sin cuerpo"}
                               for i, _ in lote if i not in listos)
                return salida_

            propias: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=HILOS) as pool:
                for n, lote in enumerate(pool.map(_detalle, lotes), 1):
                    propias.extend(lote)
                    if n % 40 == 0 or n == len(lotes):
                        aviso(f"ml {codigo}: detalle {n}/{len(lotes)} lotes")
            propias = list({f["id"]: f for f in propias}.values())

            if con_economia:
                activas = [f for f in propias if f.get("a_la_venta")]
                cache: dict[tuple, dict] = {}
                lock = threading.Lock()
                hechas = [0]

                def _una(f: dict[str, Any], _c: _Cuenta = c) -> None:
                    try:
                        _economia(_c, f, cache, lock)
                    except TokenMuerto:
                        raise
                    except Exception as exc:  # noqa: BLE001 — una publicación no tumba la cuenta
                        f["economia_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                    with lock:
                        hechas[0] += 1
                        if hechas[0] % 250 == 0 or hechas[0] == len(activas):
                            aviso(f"ml {_c.codigo}: economía {hechas[0]}/{len(activas)} activas")

                with ThreadPoolExecutor(max_workers=HILOS) as pool:
                    list(pool.map(_una, activas))
            filas.extend(propias)
            resumen_cuentas[codigo] = {
                "leida": True, "usuario": c.uid, "estados": totales,
                "publicaciones": len(propias),
                "a_la_venta": sum(1 for f in propias if f.get("a_la_venta")),
                "sin_sku": sum(1 for f in propias if not f.get("error") and not f.get("sku")),
                "con_error": sum(1 for f in propias if f.get("error")),
                "token_de": tokens[codigo]["actualizado"]}
        except TokenMuerto as exc:
            avisos.append(str(exc))
            resumen_cuentas[codigo] = {"leida": False, "motivo": str(exc)}
        except Exception as exc:  # noqa: BLE001 — la otra cuenta sigue
            avisos.append(f"{codigo}: {type(exc).__name__}: {str(exc)[:200]}")
            resumen_cuentas[codigo] = {"leida": False, "motivo": f"{type(exc).__name__}: {str(exc)[:200]}"}

    doc = {
        "canal": "mercado_libre",
        "fuente": "Mercado Libre API · items/search (scan) + items + sale_price + listing_prices + shipping_options",
        "leido_desde": inicio, "leido_hasta": ahora_iso(),
        "cuentas": resumen_cuentas, "publicaciones": len(filas),
        "a_la_venta": sum(1 for f in filas if f.get("a_la_venta")),
        "peticiones": dict(red.cuenta), "avisos": avisos, "filas": filas,
    }
    escribir_json(salida / "datos" / "ml.json", doc)
    aviso(f"ml: {len(filas)} publicaciones, {doc['a_la_venta']} activas, peticiones {dict(red.cuenta)}")
    return {k: v for k, v in doc.items() if k != "filas"}
