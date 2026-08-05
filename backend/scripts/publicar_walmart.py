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

# EL ESQUEMA OFICIAL ES PÚBLICO — ya no hay que adivinar
# ------------------------------------------------------
#   https://developer.walmart.com/file/mp/mx/MX_MP_ITEM_INTL_SPEC.json
#   3.9 MB, HTTP 200 SIN credenciales.
#
# Trae el enum de los 75 `subCategory`, los 75 grupos `Visible` con su etiqueta
# en español y la lista `required` de cada uno. Antes de sondear con feeds de
# prueba (que queman cuota), leer ese archivo. De ahí salieron los `required`
# de abajo y la confirmación de que `mart`, `locale` y `sellingChannel` son
# enums de UN SOLO valor — por eso "WALMART_MX" y "es_MX" nunca iban a funcionar.
#
# OJO: el esquema publicado hoy es la versión 3.19 y nosotros mandamos 3.11, que
# funciona. No migrar a ciegas: el Orderable de 3.19 exige 4 campos que este
# payload no manda (condition, sellerWarranty, sellerWarrantyCondition,
# sellerWarrantyPeriod).

# Categorías con EXENCIÓN DE UPC concedida. Walmart la otorga por categoría, no
# por cuenta: publicar en una que no esté aquí responde "You are not authorized
# to set up 'CUSTOM' Product IDs for UPC exemptions".
#
#   clave_visible : etiqueta EN ESPAÑOL del grupo Visible (la del esquema, con
#                   acentos exactos). Si se equivoca, Walmart cae en un spec
#                   genérico y pide atributos absurdos — ese es el síntoma.
#   clave_sat     : c_ClaveProdServ del SAT. Walmart solo valida el FORMATO
#                   (entero ≤ 8 dígitos), no lo cruza contra el SAT, pero la
#                   factura sí, así que vale ponerla bien.
#   pide_genero   : Disfraces exige `gender`; "Cocina, Decoración y Otros" no.
CATEGORIAS_AUTORIZADAS: dict[str, dict] = {
    "costumes": {
        "clave_visible": "Disfraces",
        "folio_exencion": "15728342",     # 4-ago-2026
        # 60141401 = "Disfraces o accesorios". La anterior (53102700) era
        # "Uniformes", no ropa genérica: verificado contra el catálogo oficial
        # del SAT (catCFDI_V_4, 52,513 claves).
        "clave_sat": 60141401,
        "pide_genero": True,
        "patron_categoria": "isfra|osplay",
        "patron_titulo": "isfra|osplay|allowee",
    },
    "home_other": {
        "clave_visible": "Cocina, Decoración y Otros",
        "folio_exencion": "15751007",     # 5-ago-2026
        "clave_sat": 52151600,            # "Utensilios de cocina domésticos"
        # OJO — el esquema PUBLICADO (3.19) dice que esta categoría NO pide
        # `gender` ni `size`: su `required` son 7 campos. PRODUCCIÓN DICE OTRA
        # COSA. Con version=3.11 (la que mandamos y funciona), los tres SKUs de
        # cocina del 5-ago fueron rechazados con:
        #     "`Talla` is a required attribute, but no value was provided"
        #     "`Género` is a required attribute, but no value was provided"
        # El esquema público es de la 3.19; los `required` NO son los de la 3.11.
        # Lección: el JSON oficial sirve para descubrir nombres, claves y listas
        # cerradas — pero la obligatoriedad hay que verificarla contra la versión
        # que de verdad se manda.
        "pide_genero": True,
        # El patrón por título jalaba 38 SKUs, casi todos TEC-* (reflectores,
        # tiras LED, lámparas de trabajo): "iluminación" aparece en el título de
        # mucha electrónica que NO es de hogar. El prefijo del SKU es la
        # taxonomía real de Kubera (ver services/categorias.py), así que manda él.
        "prefijos_sku": ("COC", "DEC", "ILUM", "LUZ"),
        "patron_categoria": "ocina|ecoraci|dorno|luminaci",
        "patron_titulo": "ocina|ecoraci|dorno|luminaci",
    },
}

# ⚠️ NO todo el catálogo de hogar cae en "Cocina, Decoración y Otros". El
# esquema tiene grupos SEPARADOS que esta exención NO cubre:
#     storage              -> "Almacenamiento"        (cajas, organizadores)
#     furniture_other      -> "Muebles"               (mesas, sillas, estantes)
#     decorations_and_favors -> "Adornos y Decoraciones"
# Para publicar ahí hay que pedir la exención de cada una por separado en
# sellerhelp.mx.walmart.com.

# Artículos por feed. `MPItem` es un array y Walmart admite 10,000 artículos /
# 10 MB por feed. Se manda con margen: 200 artículos pesan ~400 KB y el detalle
# del feed sigue siendo cómodo de leer.
TAM_LOTE = 200
LIMITE_BYTES_FEED = 9 * 1024 * 1024      # 10 MB reales, con margen

# Segundos entre feeds cuando hay más de un lote.
PAUSA_ENTRE_LOTES = 20

# Segundos entre subir las imágenes a WordPress y mandar el feed.
#
# LA CAUSA RAÍZ DEL LOTE DEL 4-AGO. El script subía las imágenes y publicaba en
# el mismo aliento: las de MASC-0033 se subieron a las 16:25:26 UTC y el feed
# salió a las 16:25:28 — dos segundos después. Walmart intentó descargarlas de
# inmediato, el CDN de Hostinger (`server=hcdn`) todavía no las servía, y
# respondió "We couldn't download the image, because the URL isn't in the correct
# format" (8 veces) + "Main image URL setup failed" (8 veces). El mensaje habla
# de formato, pero las URLs son ASCII puro, .jpg y HTTPS: el problema era el
# tiempo. Un día después, las MISMAS urls responden 200 con ocho User-Agents.
#
# Por eso ahora hay dos fases: se preparan TODAS las imágenes, se espera, se
# revalida cada URL contra el servidor público, y recién entonces se publica.
ESPERA_PROPAGACION = 120

# SKUs que NO se mandan y por qué. Documentarlo aquí evita volver a gastarles
# cuota y volver a descubrir el mismo motivo.
EXCLUIDOS = {
    "JUGU-0241-ROJ": "solo 1 imagen utilizable; Walmart exige mínimo 1 foto adicional",
    "JUGU-1177": "solo 1 imagen utilizable; Walmart exige mínimo 1 foto adicional",
    "ACC-0162-NEG": "en revisión de cumplimiento desde el 4-ago; Walmart rechaza "
                    "reenvíos hasta que termine (hasta 48 h)",
}

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


def candidatos(cfg: dict | None = None) -> list[str]:
    """SKUs de esa categoría que YA están vivos en Mercado Libre o Amazon."""
    from services import db, wp_db

    cfg = cfg or CATEGORIAS_AUTORIZADAS["costumes"]
    PATRON_CATEGORIA = cfg["patron_categoria"]
    PATRON_TITULO = cfg["patron_titulo"]

    # El prefijo del SKU es una vía ALTERNA al texto, no un filtro encima. Un
    # sartén puede estar en la categoría "Sartenes" y titularse "Sartén
    # antiadherente 24 cm": ni la categoría ni el título dicen "cocina", pero el
    # prefijo COC sí. Al revés, media electrónica dice "iluminación" en el
    # título sin ser de hogar — por eso el prefijo también acota.
    prefijos = cfg.get("prefijos_sku") or ()
    patron_sku = ("^(" + "|".join(prefijos) + ")-") if prefijos else None

    P = wp_db._prefix()
    cond = "(t.name REGEXP %s OR p.post_title REGEXP %s"
    params: list = [PATRON_CATEGORIA, PATRON_TITULO]
    if patron_sku:
        cond += " OR m2.meta_value REGEXP %s"
        params.append(patron_sku)
    cond += ")"

    filas = wp_db._fetch_all(f"""
        SELECT MAX(m2.meta_value) AS sku
        FROM {P}posts p
        JOIN {P}postmeta m2 ON m2.post_id = p.ID AND m2.meta_key = '_sku'
                           AND m2.meta_value <> ''
        LEFT JOIN {P}term_relationships tr ON tr.object_id = p.ID
        LEFT JOIN {P}term_taxonomy tt ON tt.term_taxonomy_id = tr.term_taxonomy_id
                                     AND tt.taxonomy = 'product_cat'
        LEFT JOIN {P}terms t ON t.term_id = tt.term_id
        WHERE p.post_type = 'product' AND p.post_status = 'publish'
          AND {cond}
        GROUP BY p.ID
        HAVING sku IS NOT NULL""", tuple(params))
    skus = sorted({f["sku"] for f in filas if f["sku"]})
    # Si hay prefijos declarados, ninguno de otra familia entra aunque el texto
    # haya coincidido.
    if prefijos:
        skus = [s for s in skus if s.split("-")[0].upper() in prefijos]
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


def _sobre(categoria: str, items: list[dict]) -> dict:
    """
    El envoltorio del feed, con TODOS los artículos adentro.

    `MPItem` es un ARRAY y admite hasta 10,000 artículos / 10 MB por feed. Se
    mandaba UNO por feed, y ahí estaba el cuello de botella: la cuota de Walmart
    (`REQUEST_THRESHOLD_VIOLATED`) cuenta LLAMADAS, no artículos. Con 1×feed, 40
    productos son 40 llamadas y la cuota muere a la mitad — pasó el 4-ago (11
    disfraces sin salir) y volvió a pasar el 5-ago (6 más). En lote, esos mismos
    40 son UNA llamada.

    Un artículo con datos malos NO tumba a los demás: Walmart valida y reporta
    artículo por artículo en `GET /v3/feeds/{feedId}?includeDetails=true`.
    """
    return {
        "MPItemFeedHeader": {
            "subCategory": categoria, "sellingChannel": "marketplace",
            "processMode": "REPLACE", "mart": "WALMART_MEXICO",
            "locale": "es", "version": "3.11", "subset": "EXTERNAL",
        },
        "MPItem": items,
    }


def _item(p: dict, imgs: list[str], categoria: str, cfg: dict) -> dict:
    """Una entrada de `MPItem` a partir de lo que Woo ya tiene."""
    clave = cfg["clave_visible"]
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

    # Los 7 obligatorios que TODA categoría comparte, según el `required` del
    # esquema oficial. Disfraces añade el octavo (`gender`); "Cocina, Decoración
    # y Otros" no lo pide — mandarlo de más ahí sería inventar un dato.
    visible = {
        "countPerPack": 1,
        "material": _attr(atrs, "material") or cfg.get("material_default", "Plástico"),
        "colorCategory": [_color(_attr(atrs, "color"))],
        "modelNumber": atrs.get("MODEL") or p.get("sku"),
        "assembledProductLength": {"measure": num(dims.get("length")), "unit": "cm"},
        "assembledProductWidth": {"measure": num(dims.get("width")), "unit": "cm"},
        "assembledProductHeight": {"measure": num(dims.get("height")), "unit": "cm"},
        "assembledProductWeight": {"measure": num(p.get("weight"), 0.3), "unit": "kg"},
    }
    if cfg.get("pide_genero"):
        visible["size"] = _attr(atrs, "talla") or "Unitalla"
        visible["gender"] = _genero(_attr(atrs, "genero"), p.get("name"))

    return {
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
                "ProductTaxCode": cfg["clave_sat"],
                "msiEligible": "No",
                "shortDescription": desc,
                "keyFeatures": [k for k in [
                    p.get("name"),
                    f"Material: {atrs['MAIN_MATERIAL']}" if atrs.get("MAIN_MATERIAL") else None,
                    f"Personaje: {atrs['CHARACTER']}" if atrs.get("CHARACTER") else None,
                    cfg.get("frase_extra"),
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
            "Visible": {clave: visible},
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


async def consultar_feed(cx, tk: str, fid: str) -> tuple[str, dict[str, tuple[str, list[str]]]]:
    """
    Veredicto de UN feed, artículo por artículo.

    Devuelve (feedStatus, {sku: (estado, [errores])}). Walmart valida cada
    artículo por separado aunque vayan cientos en el mismo feed, así que un dato
    malo en uno NO tumba a los demás: aquí se ve exactamente cuál pasó y cuál no.
    """
    r = await cx.get(f"{HOST}/v3/feeds/{fid}", headers=_h(tk),
                     params={"includeDetails": "true"}, timeout=90.0)
    if r.status_code != 200:
        return "CONSULTA_FALLIDA", {}
    s = r.json()
    por_sku: dict[str, tuple[str, list[str]]] = {}
    for d in (s.get("itemDetails") or {}).get("itemIngestionStatus", []):
        sku = d.get("sku") or "?"
        errs = [e.get("description", "")[:180]
                for e in (d.get("ingestionErrors") or {}).get("ingestionError", [])]
        por_sku[sku] = (d.get("ingestionStatus") or "INPROGRESS", errs)
    return s.get("feedStatus") or "?", por_sku


async def publicar_lote(cx, tk: str, payload: dict) -> tuple[str, str]:
    """
    Manda UN feed con TODOS los artículos del lote. Devuelve (estado, feedId).

    NO espera el veredicto aquí: sondear gastaba cuota y, peor, si a los 112 s
    Walmart seguía procesando el script devolvía "INPROGRESS" y el resumen lo
    contaba como ACEPTADO. Así nacieron los "9 feeds sin fallos" del 4-ago que en
    realidad fueron 0. El veredicto se consulta después, por SKU.
    """
    crudo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(crudo) > LIMITE_BYTES_FEED:
        return f"LOTE_MUY_GRANDE ({len(crudo) // 1024} KB)", ""
    r = await cx.post(f"{HOST}/v3/feeds", params={"feedType": "MP_ITEM_INTL"},
                      headers=_h(tk), timeout=300.0,
                      files={"file": ("lote.json", crudo, "application/json")})
    if r.status_code != 200:
        return f"ENVIO_FALLIDO: {r.text[:200]}", ""
    return "ENVIADO", r.json().get("feedId", "")


async def main() -> int:
    aplicar = "--aplicar" in sys.argv
    limite = 0
    if "--limite" in sys.argv:
        limite = int(sys.argv[sys.argv.index("--limite") + 1])
    solo: list[str] = []
    if "--skus" in sys.argv:
        solo = [s.strip() for s in sys.argv[sys.argv.index("--skus") + 1].split(",")
                if s.strip()]
    espera = ESPERA_PROPAGACION
    if "--espera" in sys.argv:
        espera = int(sys.argv[sys.argv.index("--espera") + 1])
    tam_lote = TAM_LOTE
    if "--lote" in sys.argv:
        tam_lote = max(1, int(sys.argv[sys.argv.index("--lote") + 1]))
    rondas, espera_ronda = 6, 60
    if "--rondas" in sys.argv:
        rondas = int(sys.argv[sys.argv.index("--rondas") + 1])
    categoria = "costumes"
    if "--categoria" in sys.argv:
        categoria = sys.argv[sys.argv.index("--categoria") + 1]
    if categoria not in CATEGORIAS_AUTORIZADAS:
        print(f"categoría '{categoria}' sin exención de UPC. "
              f"Disponibles: {', '.join(CATEGORIAS_AUTORIZADAS)}")
        return 1
    cfg = CATEGORIAS_AUTORIZADAS[categoria]

    import httpx

    from services import imagenes_amazon

    skus = solo or [s for s in candidatos(cfg) if s not in EXCLUIDOS]
    if limite:
        skus = skus[:limite]

    print("=" * 78)
    print(f"A PUBLICAR EN WALMART MX: {len(skus)}")
    print(f"Categoría: {categoria} → Visible[{cfg['clave_visible']!r}]  "
          f"exención folio {cfg['folio_exencion']}  SAT {cfg['clave_sat']}")
    print("=" * 78)
    for s in skus:
        print(f"   {s}")
    if not solo and EXCLUIDOS:
        print("\n   FUERA a propósito:")
        for s, motivo in EXCLUIDOS.items():
            print(f"      {s:<20} {motivo}")

    if not aplicar:
        print("\n(simulación — agrega --aplicar para publicar de verdad)")
        return 0

    resultados: list[tuple[str, str, list[str]]] = []
    feeds: list[tuple[str, list[str]]] = []     # (feedId, [skus del lote])

    async with httpx.AsyncClient(timeout=120.0) as cx:
        # ── FASE 1: dejar TODAS las imágenes servidas y publicadas ────────────
        print("\n" + "=" * 78)
        print("FASE 1 — preparar imágenes (nada se manda a Walmart todavía)")
        print("=" * 78, flush=True)
        fichas: dict[str, dict] = {}
        imgs: dict[str, list[str]] = {}
        for i, sku in enumerate(skus, 1):
            p = await ficha(cx, sku)
            if not p:
                resultados.append((sku, "SIN_FICHA", ["no se encontró en WooCommerce"]))
                print(f"[{i}/{len(skus)}] {sku:<22} sin ficha en Woo", flush=True)
                continue
            urls = [im.get("src") for im in (p.get("images") or [])][:5]
            if not urls:
                resultados.append((sku, "SIN_IMAGEN", ["el producto no tiene imágenes"]))
                print(f"[{i}/{len(skus)}] {sku:<22} sin imágenes", flush=True)
                continue
            listas, _ = await imagenes_amazon.preparar_para_amazon(sku, urls)
            fichas[sku], imgs[sku] = p, listas
            print(f"[{i}/{len(skus)}] {sku:<22} {len(listas)} imágenes preparadas",
                  flush=True)

        # ── Propagación: el CDN necesita tiempo antes de que Walmart entre ────
        if fichas and espera:
            print(f"\nesperando {espera}s a que el CDN sirva las imágenes nuevas…",
                  flush=True)
            await asyncio.sleep(espera)

        # ── FASE 2: revalidar contra el servidor público ──────────────────────
        print("\n" + "=" * 78)
        print("FASE 2 — revalidar que las imágenes se descargan de verdad")
        print("=" * 78, flush=True)
        listos: list[str] = []
        for sku in list(fichas):
            buenas = await _solo_jpeg(cx, imgs[sku])
            imgs[sku] = buenas
            if not buenas:
                resultados.append((sku, "IMAGEN_FALLIDA",
                                   ["ninguna imagen quedó en JPEG utilizable"]))
                print(f"   {sku:<22} ✗ ninguna descargable", flush=True)
            elif len(buenas) < 2:
                # Walmart exige al menos una 'Foto adicional'. No se inventa
                # duplicando la principal: se reporta para que se suba otra.
                resultados.append((sku, "FALTA_2A_FOTO",
                                   ["solo 1 imagen utilizable; Walmart exige "
                                    "mínimo 1 foto adicional"]))
                print(f"   {sku:<22} ✗ solo 1 imagen utilizable", flush=True)
            else:
                listos.append(sku)
                print(f"   {sku:<22} ✓ {len(buenas)} imágenes vivas", flush=True)

        # ── FASE 3: publicar POR LOTE ─────────────────────────────────────────
        lotes = [listos[i:i + tam_lote] for i in range(0, len(listos), tam_lote)]
        print("\n" + "=" * 78)
        print(f"FASE 3 — publicar {len(listos)} artículos en {len(lotes)} "
              f"feed(s) de hasta {tam_lote}")
        print("=" * 78, flush=True)
        for n, lote in enumerate(lotes, 1):
            if n > 1:
                await asyncio.sleep(PAUSA_ENTRE_LOTES)
            tk = await _token(cx)      # se renueva solo (el token dura 900 s)
            items = [_item(fichas[s], imgs[s], categoria, cfg) for s in lote]
            estado, fid = await publicar_lote(cx, tk, _sobre(categoria, items))
            peso = len(json.dumps(_sobre(categoria, items),
                                  ensure_ascii=False).encode()) // 1024
            print(f"   lote {n}/{len(lotes)}: {len(lote)} artículos, {peso} KB "
                  f"-> {estado} {fid}", flush=True)
            if fid:
                feeds.append((fid, lote))
            else:
                for s in lote:
                    resultados.append((s, "ENVIO_FALLIDO", [estado]))

        # ── FASE 4: el veredicto REAL, artículo por artículo ──────────────────
        if feeds:
            print("\n" + "=" * 78)
            print(f"FASE 4 — veredicto por SKU ({rondas} rondas de {espera_ronda}s)")
            print("=" * 78, flush=True)
            pendientes = {s: fid for fid, lote in feeds for s in lote}
            for ronda in range(1, rondas + 1):
                if not pendientes:
                    break
                await asyncio.sleep(espera_ronda)
                tk = await _token(cx)
                for fid, lote in feeds:
                    if not any(s in pendientes for s in lote):
                        continue
                    fstat, por_sku = await consultar_feed(cx, tk, fid)
                    for sku, (st, errs) in por_sku.items():
                        if st == "INPROGRESS" or sku not in pendientes:
                            continue
                        resultados.append((sku, st, errs))
                        pendientes.pop(sku, None)
                    await asyncio.sleep(1.5)
                print(f"   ronda {ronda}: resueltos "
                      f"{len(listos) - len(pendientes)}/{len(listos)}, "
                      f"faltan {len(pendientes)}", flush=True)
            for sku in pendientes:
                resultados.append((sku, "INPROGRESS", []))

            print("\n   FEEDS DE ESTA CORRIDA (para volver a consultarlos):")
            for fid, lote in feeds:
                print(f"      {fid}   {len(lote)} artículos")

    print("\n" + "=" * 78)
    print("RESUMEN")
    print("=" * 78)
    # INPROGRESS NO es éxito: es "Walmart todavía no decide". Contarlo como
    # aceptado fue lo que produjo los "9 feeds sin fallos" del 4-ago que en
    # realidad fueron 0.
    ok = [r for r in resultados if r[1] in ("SUCCESS", "PROCESSED")]
    pendientes = [r for r in resultados if r[1] == "INPROGRESS"]
    mal = [r for r in resultados if r not in ok and r not in pendientes]
    print(f"   Publicados (SUCCESS)     : {len(ok)}")
    print(f"   Sin veredicto todavía    : {len(pendientes)}")
    print(f"   Rechazados               : {len(mal)}")
    for sku, _e, _errs in ok:
        print(f"      ✓ {sku}")
    for sku, estado, errs in pendientes:
        print(f"      … {sku}  [{estado}]  vuelve a correr estado_walmart en un rato")
    for sku, estado, errs in mal:
        print(f"\n   ✗ {sku}  [{estado}]")
        for e in errs[:4]:
            print(f"      · {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
