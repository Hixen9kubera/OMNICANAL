"""
publicar_walmart.py — Publica en Walmart México los productos YA publicados en
otros canales, dentro de las categorías que tienen exención de UPC.

POR QUÉ EXISTE
--------------
Walmart MX exige un identificador de producto (GTIN/UPC/EAN) y Kubera no tiene:
solo 4 SKUs de 7,151 traen código, y de origen no confirmado. La salida es la
EXENCIÓN, que Walmart otorga **por categoría** — no para toda la cuenta.

El 4-ago-2026, folio 15728342, autorizaron la carga sin UPC para **Disfraces**.
Con eso se publica mandando `productIdType=GTIN` y `productId=CUSTOM`. En una
categoría sin exención, Walmart responde:

    "You are not authorized to set up 'CUSTOM' Product IDs for UPC exemptions"

Por eso este script FILTRA por categoría autorizada. Cuando lleguen más
exenciones, se agregan a CATEGORIAS_AUTORIZADAS y ya.

LAS DOS TRAMPAS QUE COSTARON DESCUBRIR
--------------------------------------
1. **Las imágenes.** El catálogo es mayormente WEBP (viene del scraping de
   Alibaba) y las editadas con IA son PNG con extensión `.jpg`. Walmart lee el
   CONTENIDO, no el nombre, y las rechaza. La solución ya existía:
   `imagenes_amazon.preparar_para_amazon` convierte a JPEG conservando la
   resolución, sube a WordPress y cachea por hash. Se reusa tal cual.

2. **Walmart valida POR ETAPAS.** Corriges un error y aparecen otros que estaban
   escondidos detrás. Que cambien los mensajes NO significa que lo anterior se
   resolvió — solo que avanzaste un escalón. Por eso el resumen final distingue
   "publicado" de "rechazado" y muestra el motivo textual.

Uso (desde backend/):
    python -m scripts.publicar_walmart                  # lista, no manda nada
    python -m scripts.publicar_walmart --limite 3 --aplicar
    python -m scripts.publicar_walmart --aplicar        # todos
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import uuid

logging.disable(logging.WARNING)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

HOST = "https://marketplace.walmartapis.com"

# categoría de la API -> etiqueta del grupo Visible (la que usa el spec en español)
CATEGORIAS_AUTORIZADAS = {
    "costumes": "Disfraces",
}

# Cómo reconocer un disfraz en el catálogo de Woo.
PATRON_CATEGORIA = "isfra|osplay"
PATRON_TITULO = "isfra|osplay|allowee"

# Clave del SAT para ropa y accesorios de vestir.
CLAVE_SAT = 53102700

# Segundos de espera entre productos. Sin esto, Walmart corta con
# REQUEST_THRESHOLD_VIOLATED (pasó con 4 del primer lote).
PAUSA_ENTRE_ITEMS = 8

# `Género` es lista cerrada: [Hombre, Niño, Mujer, Unisex, Niña]. Woo guarda
# valores libres ("Adulto", "Dama", "Caballero"...) que Walmart rechaza. Lo que
# NO se puede deducir queda en Unisex — es el default honesto, no una invención
# sobre el producto.
GENERO = {
    "hombre": "Hombre", "caballero": "Hombre", "masculino": "Hombre",
    "mujer": "Mujer", "dama": "Mujer", "femenino": "Mujer",
    "niño": "Niño", "nino": "Niño", "niños": "Niño",
    "niña": "Niña", "nina": "Niña", "niñas": "Niña",
    "unisex": "Unisex",
}

# "Adulto" habla de EDAD, no de género. Si el atributo dice eso, no aporta y hay
# que mirar el título: "Disfraz de pirata MUJER adulto" sí lo dice.
NO_SON_GENERO = {"adulto", "adultos", "adulta", "adultas", "unitalla", "n/a", "-"}


# El catálogo tiene los MISMOS atributos con dos nombres: en MAYÚSCULA/inglés
# (COLOR, SIZE, GENDER) y en español (Color, Talla, Género). Leer solo los
# primeros hacía que 15 de 24 productos se publicaran con "Multicolor" y
# "Unitalla" TENIENDO el dato real. No era un hueco, era un dato FALSO.
ALIAS = {
    "color": ("COLOR", "Color", "COLOUR"),
    "talla": ("SIZE", "Talla", "TALLA"),
    "genero": ("GENDER", "Género", "Genero", "GENERO"),
    "material": ("MAIN_MATERIAL", "Material", "MATERIAL", "MATERIALS",
                 "COMPOSITION", "Composición"),
    "marca": ("BRAND", "Marca", "MARCA"),
    "modelo": ("MODEL", "Modelo", "MODELO"),
    "personaje": ("CHARACTER", "Personaje"),
}


def _attr(atrs: dict, familia: str) -> str | None:
    """Primer valor no vacío entre todos los nombres que usa esa familia."""
    for nombre in ALIAS.get(familia, ()):
        v = atrs.get(nombre)
        if v and str(v).strip():
            return str(v).strip()
    return None


# `colorCategory` es lista CERRADA de 36 valores, con acentos literales. Woo
# guarda texto libre ("Azul marino", "Rojo/Negro"), y cualquier valor fuera de
# la lista tumba el artículo.
COLORES = ("Cedro", "Aqua", "Rojo", "Anaranjado", "Bambú", "Transparente",
           "Morado", "Encino", "Rosa", "Madera", "Amarillo", "Gris", "Beige",
           "Negro", "Café", "Plateado", "Tabaco", "Fucsia", "Shedron",
           "Acero Inox", "Chocolate", "Multicolor", "Roble", "Bronce",
           "Turquesa", "Camello", "Nogal", "Verde", "Azul", "Rosa Dorado",
           "Silver", "Fresno", "Lila", "Blanco", "Vino", "Dorado")

# Sinónimos frecuentes del catálogo → valor de la lista. No adivina tonos:
# "Azul marino" es Azul, pero un color desconocido cae en Multicolor, que es
# lo honesto cuando no se sabe.
COLOR_SINONIMOS = {
    "dorada": "Dorado", "oro": "Dorado", "gold": "Dorado",
    "plata": "Plateado", "plateada": "Plateado",
    "cafe": "Café", "marron": "Café", "marrón": "Café",
    "naranja": "Anaranjado", "morada": "Morado", "purpura": "Morado",
    "violeta": "Lila", "celeste": "Azul", "turqueza": "Turquesa",
    "blanca": "Blanco", "negra": "Negro", "roja": "Rojo", "verde militar": "Verde",
}


def _color(valor: str | None) -> str:
    """Normaliza contra la lista cerrada de Walmart. Sin match → Multicolor."""
    v = (valor or "").strip()
    if not v:
        return "Multicolor"
    bajo = v.lower()
    for c in COLORES:                      # coincidencia exacta
        if bajo == c.lower():
            return c
    if bajo in COLOR_SINONIMOS:
        return COLOR_SINONIMOS[bajo]
    for c in COLORES:                      # "Azul marino" -> Azul
        if bajo.startswith(c.lower() + " ") or f" {c.lower()}" in bajo:
            return c
    for clave, destino in COLOR_SINONIMOS.items():
        if clave in bajo:
            return destino
    return "Multicolor"


def _genero(valor: str | None, titulo: str = "") -> str:
    """Traduce el género de Woo a la lista cerrada de Walmart."""
    v = (valor or "").strip().lower()
    if v in GENERO and v not in NO_SON_GENERO:
        return GENERO[v]
    # Si el atributo no sirve, el TÍTULO suele decirlo con claridad
    # ("Disfraz de Cleopatra para MUJER"). Solo se acepta si es inequívoco.
    t = (titulo or "").lower()
    for clave, destino in (("niña", "Niña"), ("nina", "Niña"), ("niño", "Niño"),
                           ("nino", "Niño"), ("mujer", "Mujer"), ("dama", "Mujer"),
                           ("hombre", "Hombre"), ("caballero", "Hombre")):
        if clave in t:
            return destino
    return "Unisex"


def _h(tk: str | None = None) -> dict:
    d = {"WM_SVC.NAME": "Walmart Marketplace",
         "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
         "WM_MARKET": "mx", "Accept": "application/json"}
    if tk:
        d["WM_SEC.ACCESS_TOKEN"] = tk
    return d


_token_cache: dict = {"valor": "", "vence": 0.0}


async def _token(cx) -> str:
    """
    Token vigente, renovándolo solo cuando toca.

    El token de Walmart dura 900 s. En el primer lote se pidió UNA vez al
    arrancar y venció a media corrida: 7 de 24 productos murieron con
    "UNAUTHORIZED - Invalid token" sin que hubiera nada malo en sus datos.
    Se renueva 120 s antes del vencimiento para no quedarse corto.
    """
    import time
    if _token_cache["valor"] and time.time() < _token_cache["vence"]:
        return _token_cache["valor"]
    cid, sec = os.environ["WM_CLIENT_ID"], os.environ["WM_CLIENT_SECRET"]
    h = _h()
    h["Authorization"] = "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()
    h["Content-Type"] = "application/x-www-form-urlencoded"
    r = await cx.post(f"{HOST}/v3/token", headers=h,
                      data={"grant_type": "client_credentials"})
    r.raise_for_status()
    j = r.json()
    _token_cache["valor"] = j["access_token"]
    _token_cache["vence"] = time.time() + int(j.get("expires_in", 900)) - 120
    return _token_cache["valor"]


def candidatos() -> list[str]:
    """SKUs de disfraz que YA están vivos en Mercado Libre o Amazon."""
    from services import db, wp_db

    P = wp_db._prefix()
    filas = wp_db._fetch_all(f"""
        SELECT MAX(CASE WHEN m.meta_key='_sku' THEN m.meta_value END) AS sku
        FROM {P}posts p
        JOIN {P}postmeta m ON m.post_id = p.ID
        LEFT JOIN {P}term_relationships tr ON tr.object_id = p.ID
        LEFT JOIN {P}term_taxonomy tt ON tt.term_taxonomy_id = tr.term_taxonomy_id
                                     AND tt.taxonomy = 'product_cat'
        LEFT JOIN {P}terms t ON t.term_id = tt.term_id
        WHERE p.post_type = 'product' AND p.post_status = 'publish'
          AND (t.name REGEXP %s OR p.post_title REGEXP %s)
        GROUP BY p.ID
        HAVING sku IS NOT NULL""", (PATRON_CATEGORIA, PATRON_TITULO))
    skus = sorted({f["sku"] for f in filas if f["sku"]})
    if not skus:
        return []
    ph = ",".join(["%s"] * len(skus))
    vivos = db.fetch_all(f"""
        SELECT DISTINCT sku FROM canal_inventario
        WHERE sku IN ({ph}) AND item_id IS NOT NULL AND situacion <> 'closed'""",
        tuple(skus))
    return sorted({r["sku"] for r in vivos})


async def ficha(cx, sku: str) -> dict | None:
    """
    Producto EN VIVO desde WooCommerce (con cache-bust, regla de la casa).

    Resuelve además el PRECIO DE LISTA real. En un producto `variable` el padre
    trae `regular_price` VACÍO, así que caer a `price` publicaba el precio con
    DESCUENTO y además el MÁS BAJO de todas las variantes — o sea, en Walmart
    saldría más barato que tu precio de lista. Afectaba a 14 de los 24.
    """
    from config import settings
    base = f"{settings.wc_url.rstrip('/')}/wp-json/wc/v3"
    auth = (settings.wc_consumer_key, settings.wc_consumer_secret)
    r = await cx.get(f"{base}/products", auth=auth, timeout=60.0,
                     params={"sku": sku, "_cb": "wmpub"})
    if r.status_code != 200:
        return None
    prods = r.json()
    if not prods:
        return None
    p = prods[0]

    def _f(v):
        try:
            x = float(v)
            return x if x > 0 else None
        except (TypeError, ValueError):
            return None

    precio = _f(p.get("regular_price"))
    if precio is None and p.get("type") == "variable":
        rv = await cx.get(f"{base}/products/{p['id']}/variations", auth=auth,
                          timeout=60.0,
                          params={"per_page": 100, "_cb": "wmpub",
                                  "_fields": "id,sku,regular_price,price"})
        if rv.status_code == 200:
            # El precio de LISTA del padre es el MAYOR de sus variantes: es el
            # que se anuncia como "desde" y el que no está descontado.
            precios = [x for x in
                       (_f(v.get("regular_price")) or _f(v.get("price"))
                        for v in rv.json()) if x]
            if precios:
                precio = max(precios)
    if precio is None:
        precio = _f(p.get("price"))
    # Se marca en la ficha para que _armar() no tenga que repetir la lógica.
    p["_precio_lista"] = precio
    return p


def _armar(p: dict, imgs: list[str], categoria: str, clave: str) -> dict:
    """Payload MP_ITEM_INTL a partir de lo que Woo ya tiene."""
    atrs = {a.get("name"): (a.get("options") or [None])[0]
            for a in (p.get("attributes") or [])}
    dims = p.get("dimensions") or {}

    def num(v, x=10.0) -> float:
        """
        Walmart rechaza más de 2 decimales:
            "The value for `ShippingWeight` cannot exceed `2` decimal points"
        Woo guarda el peso como '0.300' y las dimensiones como '10.31', así que
        hay que redondear ANTES de mandar. Fue la causa de 6 de los rechazos del
        primer lote.
        """
        try:
            f = float(v)
            return round(f, 2) if f > 0 else round(x, 2)
        except (TypeError, ValueError):
            return round(x, 2)

    desc = (p.get("short_description") or p.get("description") or "")
    import re
    desc = re.sub(r"<[^>]+>", " ", desc)
    desc = re.sub(r"\s+", " ", desc).strip()[:3900] or p.get("name")

    return {
        "MPItemFeedHeader": {
            "subCategory": categoria, "sellingChannel": "marketplace",
            "processMode": "REPLACE", "mart": "WALMART_MEXICO",
            "locale": "es", "version": "3.11", "subset": "EXTERNAL",
        },
        "MPItem": [{
            "Orderable": {
                "sku": p.get("sku"),
                # LA EXENCIÓN — folio 15728342, categoría Disfraces
                "productIdentifiers": {"productIdType": "GTIN", "productId": "CUSTOM"},
                "productName": (p.get("name") or "")[:200],
                "brand": atrs.get("BRAND") or "Ferrahome",
                "manufacturer": atrs.get("BRAND") or "Ferrahome",
                # Precio de LISTA, ya resuelto en ficha() (los variables traen
                # el padre vacío y caían al precio con descuento).
                "price": num(p.get("_precio_lista"), 1.0),
                "ProductTaxCode": CLAVE_SAT,
                "msiEligible": "No",
                "shortDescription": desc,
                "keyFeatures": [k for k in [
                    p.get("name"),
                    f"Material: {atrs['MAIN_MATERIAL']}" if atrs.get("MAIN_MATERIAL") else None,
                    f"Personaje: {atrs['CHARACTER']}" if atrs.get("CHARACTER") else None,
                    "Ideal para Halloween y fiestas temáticas",
                ] if k][:5],
                "mainImageUrl": imgs[0],
                "productSecondaryImageURL": imgs[1:5],
                "ShippingWeight": {"measure": num(p.get("weight"), 0.3), "unit": "kg"},
                "ShippingDimensionsWidth": {"measure": num(dims.get("width")), "unit": "cm"},
                "ShippingDimensionsHeight": {"measure": num(dims.get("height")), "unit": "cm"},
                "ShippingDimensionsDepth": {"measure": num(dims.get("length")), "unit": "cm"},
                "countryOfOriginAssembly": ["China"],
                "hazardousMaterialsInd": "No",
                "hasNomCertification": "No",
                "shippingDiscount": 0,
                "itemsIncluded": (p.get("name") or "")[:200],
            },
            "Visible": {
                clave: {
                    "countPerPack": 1,
                    "material": _attr(atrs, "material") or "Poliéster",
                    "colorCategory": [_color(_attr(atrs, "color"))],
                    "modelNumber": atrs.get("MODEL") or p.get("sku"),
                    "size": _attr(atrs, "talla") or "Unitalla",
                    "gender": _genero(_attr(atrs, "genero"), p.get("name")),
                    "assembledProductLength": {"measure": num(dims.get("length")), "unit": "cm"},
                    "assembledProductWidth": {"measure": num(dims.get("width")), "unit": "cm"},
                    "assembledProductHeight": {"measure": num(dims.get("height")), "unit": "cm"},
                    "assembledProductWeight": {"measure": num(p.get("weight"), 0.3), "unit": "kg"},
                }
            },
        }],
    }


async def _solo_jpeg(cx, urls: list[str]) -> list[str]:
    """
    Deja pasar SOLO las imágenes que Walmart de verdad acepta.

    `imagenes_amazon.preparar_para_amazon` está hecho para Amazon, y eso trae
    dos problemas aquí:
      · Su lista de formatos válidos incluye PNG. Amazon las acepta; Walmart NO.
        Una PNG de ≥1000 px se devuelve SIN CONVERTIR.
      · Ante cualquier fallo devuelve la URL ORIGINAL, y regresa siempre tantas
        URLs como recibió. Desde fuera no se distingue una imagen convertida de
        una WEBP que no se pudo convertir.

    Por eso no se confía en lo que devuelve: se descarga cada una y se mira su
    contenido real. Es la única forma de saber qué va a aceptar Walmart.
    """
    from io import BytesIO

    from PIL import Image

    buenas: list[str] = []
    for u in urls:
        try:
            r = await cx.get(u, timeout=45.0)
            if r.status_code != 200:
                continue
            im = Image.open(BytesIO(r.content))
            if im.format == "JPEG" and min(im.width, im.height) >= 500:
                buenas.append(u)
        except Exception:  # noqa: BLE001 — una imagen ilegible simplemente no entra
            continue
    return buenas


async def publicar(cx, tk: str, payload: dict) -> tuple[str, list[str]]:
    crudo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r = await cx.post(f"{HOST}/v3/feeds", params={"feedType": "MP_ITEM_INTL"},
                      headers=_h(tk), timeout=120.0,
                      files={"file": ("i.json", crudo, "application/json")})
    if r.status_code != 200:
        return "ENVIO_FALLIDO", [r.text[:180]]
    fid = r.json()["feedId"]
    for _ in range(8):
        await asyncio.sleep(14)
        s = (await cx.get(f"{HOST}/v3/feeds/{fid}", headers=_h(tk),
                          params={"includeDetails": "true"}, timeout=60.0)).json()
        d = ((s.get("itemDetails") or {}).get("itemIngestionStatus") or [{}])[0]
        st = d.get("ingestionStatus") or ""
        if st and st != "INPROGRESS":
            errs = [e.get("description", "")[:170]
                    for e in (d.get("ingestionErrors") or {}).get("ingestionError", [])]
            return st, errs
        if s.get("feedStatus") == "PROCESSED":
            return st or "PROCESSED", []
    return "INPROGRESS", []


async def main() -> int:
    aplicar = "--aplicar" in sys.argv
    limite = 0
    if "--limite" in sys.argv:
        limite = int(sys.argv[sys.argv.index("--limite") + 1])

    import httpx

    from services import imagenes_amazon

    skus = candidatos()
    if limite:
        skus = skus[:limite]

    print("=" * 78)
    print(f"DISFRACES YA PUBLICADOS EN OTRO CANAL: {len(skus)}")
    print(f"Categorías con exención de UPC: {', '.join(CATEGORIAS_AUTORIZADAS)}")
    print("=" * 78)
    for s in skus:
        print(f"   {s}")

    if not aplicar:
        print("\n(simulación — agrega --aplicar para publicar de verdad)")
        return 0

    categoria, clave = next(iter(CATEGORIAS_AUTORIZADAS.items()))
    resultados: list[tuple[str, str, list[str]]] = []

    async with httpx.AsyncClient(timeout=120.0) as cx:
        for i, sku in enumerate(skus, 1):
            print(f"\n{'─' * 78}\n[{i}/{len(skus)}] {sku}", flush=True)
            if i > 1:
                await asyncio.sleep(PAUSA_ENTRE_ITEMS)   # evita el corte por ritmo
            p = await ficha(cx, sku)
            if not p:
                resultados.append((sku, "SIN_FICHA", ["no se encontró en WooCommerce"]))
                print("   sin ficha en Woo", flush=True)
                continue
            urls = [im.get("src") for im in (p.get("images") or [])][:5]
            if not urls:
                resultados.append((sku, "SIN_IMAGEN", ["el producto no tiene imágenes"]))
                print("   sin imágenes", flush=True)
                continue
            print(f"   {str(p.get('name'))[:62]}", flush=True)
            print(f"   convirtiendo {len(urls)} imágenes…", flush=True)
            listas, _ = await imagenes_amazon.preparar_para_amazon(sku, urls)
            listas = await _solo_jpeg(cx, listas)
            if not listas:
                resultados.append((sku, "IMAGEN_FALLIDA",
                                   ["ninguna imagen quedó en JPEG utilizable"]))
                print("   ninguna imagen sirve para Walmart", flush=True)
                continue
            if len(listas) < 2:
                # Walmart exige al menos una 'Foto adicional'. No se inventa
                # duplicando la principal: se reporta para que se suba otra.
                resultados.append((sku, "FALTA_2A_FOTO",
                                   [f"solo 1 imagen utilizable de {len(urls)}; "
                                    f"Walmart exige mínimo 1 foto adicional"]))
                print("   solo 1 imagen utilizable — se necesita una segunda",
                      flush=True)
                continue
            # El token se renueva solo si está por vencer (dura 900 s).
            tk = await _token(cx)
            estado, errs = await publicar(cx, tk, _armar(p, listas, categoria, clave))
            resultados.append((sku, estado, errs))
            print(f"   -> {estado}", flush=True)
            for e in errs[:3]:
                print(f"      · {e}", flush=True)

    print("\n" + "=" * 78)
    print("RESUMEN")
    print("=" * 78)
    ok = [r for r in resultados if r[1] in ("INPROGRESS", "SUCCESS", "PROCESSED")]
    mal = [r for r in resultados if r not in ok]
    print(f"   Aceptados por Walmart : {len(ok)}")
    print(f"   Rechazados            : {len(mal)}")
    for sku, estado, errs in mal:
        print(f"\n   {sku}  [{estado}]")
        for e in errs[:4]:
            print(f"      · {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
