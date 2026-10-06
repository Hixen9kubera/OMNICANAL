"""
f_amazon.py — Lo que hay publicado en Amazon (San Corpe), EN VIVO por SP-API.

  1. Censo: `searchListingsItems` SIN identificadores, paginado. Es el catálogo que
     Amazon dice tener, no nuestra bitácora de publicaciones (que llegó a contar
     293 listados muertos). Trae SKU, ASIN, título, foto, precio, estado y stock.
  2. Comisiones: `getMyFeesEstimates` al precio publicado, en lotes de 20. Amazon
     devuelve el desglose (referral, cierre, FBA) — un cálculo suyo, no un supuesto.

`searchListingsItems` corta en ~1,000 resultados aunque `numberOfResults` diga más.
Por eso el censo va por VENTANAS de fecha de alta (`createdAfter`, orden ascendente):
cuando una pasada se queda sin página siguiente y aún faltan, la siguiente arranca
en la última fecha vista. Se deduplica por SKU.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from comun import Cfg, Red, ahora_iso, aviso, escribir_json, num

LOTE = 20
ESTADOS_A_LA_VENTA = {"BUYABLE"}


def _token(cfg: Cfg, red: Red) -> str:
    r = red.lectura_sin_get(cfg("AMAZON_LWA_TOKEN_URL", "https://api.amazon.com/auth/o2/token"), data={
        "grant_type": "refresh_token", "refresh_token": cfg("AMAZON_REFRESH_TOKEN"),
        "client_id": cfg("AMAZON_LWA_CLIENT_ID"),
        "client_secret": cfg("AMAZON_LWA_CLIENT_SECRET")})
    if r.status_code != 200:
        raise RuntimeError(f"Amazon LWA respondió HTTP {r.status_code}")
    return r.json()["access_token"]


def _fila(it: dict[str, Any]) -> dict[str, Any] | None:
    sku = (it.get("sku") or "").strip()
    if not sku:
        return None
    s = (it.get("summaries") or [{}])[0]
    estados = s.get("status") or []
    if isinstance(estados, str):
        estados = [estados]
    precio = None
    for of in it.get("offers") or []:
        if (of.get("offerType") or "B2C") == "B2C" and of.get("price"):
            precio = num(of["price"].get("amount"))
            break
    fba = fbm = None
    for fa in it.get("fulfillmentAvailability") or []:
        if (fa.get("fulfillmentChannelCode") or "").upper().startswith("AMAZON"):
            fba = fa.get("quantity")
        else:
            fbm = fa.get("quantity")
    asin = s.get("asin")
    return {
        "sku": sku, "id": asin, "titulo": s.get("itemName"),
        "imagen": (s.get("mainImage") or {}).get("link"),
        "precio": precio, "moneda": "MXN",
        "estado": "+".join(estados) if estados else "SIN_ESTADO",
        "a_la_venta": bool(ESTADOS_A_LA_VENTA & set(estados)),
        "tipo": s.get("productType"),
        "es_fba": fba is not None, "stock_fba": fba, "stock_propio": fbm,
        "link": f"https://www.amazon.com.mx/dp/{asin}" if asin else None,
        "creado": s.get("createdDate"), "editado": s.get("lastUpdatedDate"),
    }


def extraer(cfg: Cfg, salida: Path) -> dict[str, Any]:
    if not cfg.tiene("AMAZON_REFRESH_TOKEN", "AMAZON_LWA_CLIENT_ID", "AMAZON_SELLER_ID"):
        raise RuntimeError("Amazon sin configurar (.env.amazon)")
    red = Red(rps=2.0)
    inicio = ahora_iso()
    base = cfg("AMAZON_SP_API_ENDPOINT").rstrip("/")
    seller, mp = cfg("AMAZON_SELLER_ID"), cfg("AMAZON_MARKETPLACE_ID")
    cab = {"x-amz-access-token": _token(cfg, red)}

    # 1 · Censo por ventanas de fecha de alta
    filas: dict[str, dict[str, Any]] = {}
    declarado: int | None = None
    cursor: str | None = None
    avisos: list[str] = []
    for ventana in range(40):
        nuevos = 0
        token_pag: str | None = None
        ultima_fecha = cursor
        while True:
            params: dict[str, Any] = {
                "marketplaceIds": mp, "pageSize": LOTE,
                "includedData": "summaries,offers,fulfillmentAvailability",
                "sortBy": "createdDate", "sortOrder": "ASC"}
            if cursor:
                params["createdAfter"] = cursor
            if token_pag:
                params["pageToken"] = token_pag
            r = red.get(f"{base}/listings/2021-08-01/items/{seller}", params=params, headers=cab)
            if r.status_code != 200:
                avisos.append(f"censo: HTTP {r.status_code} en la ventana {ventana + 1} "
                              f"({r.text[:160]})")
                break
            j = r.json()
            if declarado is None:
                declarado = j.get("numberOfResults")
            for it in j.get("items") or []:
                f = _fila(it)
                if not f:
                    continue
                if f["sku"] not in filas:
                    nuevos += 1
                filas[f["sku"]] = f
                if f.get("creado") and (ultima_fecha is None or f["creado"] > ultima_fecha):
                    ultima_fecha = f["creado"]
            token_pag = (j.get("pagination") or {}).get("nextToken")
            if not token_pag:
                break
        aviso(f"amazon: ventana {ventana + 1} · {len(filas)} listados (Amazon declara {declarado})")
        if declarado is None or len(filas) >= declarado or nuevos == 0 or ultima_fecha == cursor:
            break
        cursor = ultima_fecha
    if declarado is not None and len(filas) < declarado:
        avisos.append(f"censo incompleto: {len(filas)} leídos de {declarado} que declara Amazon")

    # 2 · Comisiones al precio publicado (getMyFeesEstimates, 20 por llamada)
    con_precio = [f for f in filas.values() if f.get("precio")]
    estimadas = 0
    for i in range(0, len(con_precio), LOTE):
        tanda = con_precio[i:i + LOTE]
        cuerpo = [{
            "FeesEstimateRequest": {
                "MarketplaceId": mp, "IsAmazonFulfilled": bool(f["es_fba"]),
                "Identifier": f["sku"],
                "PriceToEstimateFees": {"ListingPrice": {"CurrencyCode": "MXN",
                                                         "Amount": f["precio"]}}},
            "IdType": "SellerSKU", "IdValue": f["sku"]} for f in tanda]
        r = red.lectura_sin_get(f"{base}/products/fees/v0/feesEstimate", json=cuerpo,
                                headers=cab, rps=0.45, reintentos=5)
        if r.status_code != 200:
            avisos.append(f"comisiones: HTTP {r.status_code} en el lote {i // LOTE + 1}")
            continue
        for res in r.json() or []:
            ident = ((res.get("FeesEstimateIdentifier") or {}).get("SellerInputIdentifier")
                     or (res.get("FeesEstimateIdentifier") or {}).get("IdValue"))
            f = filas.get(ident or "")
            if not f:
                continue
            if res.get("Status") != "Success":
                f["comision_error"] = ((res.get("Error") or {}).get("Message") or res.get("Status") or "")[:140]
                continue
            est = res.get("FeesEstimate") or {}
            detalle = []
            for d in est.get("FeeDetailList") or []:
                monto = num(((d.get("FinalFee") or {}).get("Amount")))
                if monto:
                    detalle.append({"concepto": d.get("FeeType"), "monto": round(monto, 2)})
            f["comision"] = {"total": num((est.get("TotalFeesEstimate") or {}).get("Amount")),
                             "detalle": detalle, "fuente": "Amazon · getMyFeesEstimates"}
            estimadas += 1
        if (i // LOTE) % 10 == 0:
            aviso(f"amazon: comisiones {min(i + LOTE, len(con_precio))}/{len(con_precio)}")

    lista = sorted(filas.values(), key=lambda f: f["sku"])
    doc = {
        "canal": "amazon", "cuenta": "San Corpe",
        "fuente": "Amazon SP-API · searchListingsItems + getMyFeesEstimates",
        "leido_desde": inicio, "leido_hasta": ahora_iso(),
        "declarado_por_el_canal": declarado, "publicaciones": len(lista),
        "a_la_venta": sum(1 for f in lista if f["a_la_venta"]),
        "con_comision": estimadas, "avisos": avisos, "filas": lista,
    }
    escribir_json(salida / "datos" / "amazon.json", doc)
    aviso(f"amazon: {len(lista)} listados, {doc['a_la_venta']} a la venta, {estimadas} con comisión")
    return {k: v for k, v in doc.items() if k != "filas"}
