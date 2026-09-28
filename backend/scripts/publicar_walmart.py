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
    python -m scripts.publicar_walmart --aplicar --sin-bitacora   # no registrar

BITÁCORA (desde v0.106.0): con --aplicar, cada artículo enviado queda en
`ops.channel_submissions` (canal='walmart', submission_id=feedId) y su veredicto
se escribe encima cuando Walmart contesta. Así "¿qué pasó con este SKU?" deja de
costar una llamada a la API — que es lo que tumbó 19 de 24 productos por
REQUEST_THRESHOLD_VIOLATED. Ver la clase _Bitacora.
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

# `backend/` en la ruta, igual que en `publicar_temu.py`. Sin esto el script solo
# corre si alguien puso el PYTHONPATH a mano: lanzándolo por su ruta, `sys.path[0]`
# es `scripts/`, y ni `core` ni `services` se encuentran. Se descubrió al añadirle
# el candado del actor, que fue el primer import que se hizo temprano.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
#   campos_visible: BLANCA de campos del bloque `Visible`. Ver LA PODA abajo.
#   prueba        : "feed" = un artículo de esa categoría ya llegó a SUCCESS;
#                   "ticket" = Walmart lo autorizó POR ESCRITO pero ningún feed
#                   lo ha confirmado. Son dos certezas distintas: el ticket dice
#                   que la puerta está abierta, no qué campos exige producción.
#                   El panel avisa en la vista previa cuando es "ticket".
#   sat_por_patron: (opcional) pares (regex del título, clave SAT) que afinan
#                   la clave dentro de una categoría que mezcla productos: en
#                   «Blancos» un colchón y una sábana no llevan la misma.
#   excluir       : (opcional) regex sobre el TÍTULO. Si casa, esa categoría NO
#                   aplica aunque sus patrones sí — el título dice qué ES el
#                   producto, y las categorías de Woo (heredadas de ML) mienten:
#                   "Pañales" también agrupa pañales para perro. La aplica
#                   `clasificar()`, y por él las DOS rutas: el botón del panel y
#                   la tanda (`candidatos()` decide con la misma función).
#
# ═════════════════════════════════════════════════════════════════════════════
# LA PODA — por qué cada categoría lleva su propia lista de campos
# ═════════════════════════════════════════════════════════════════════════════
# Mandar un campo que la categoría NO define no es un aviso: tumba el artículo
# entero, y con él el lote. Tres feeds completos murieron así el 7-ago, cada uno
# por UN campo de más:
#
#     'modelNumber' is not a valid field   ->  85/85 muertos (Electrónicos)
#     'gender' is not a valid field        ->  83/83 muertos (Almacenamiento)
#     'countPerPack' is not a valid field  ->  33/33 muertos (Juguetes)
#
# El esquema PUBLICADO (3.19) no sirve para decidir esto: dice que
# `modelNumber` es válido en electrónica y producción (3.11) lo rechaza. Vale lo
# MEDIDO. Por eso `campos_visible` es una lista blanca explícita por categoría y
# no una derivación automática del JSON oficial.
#
# ═════════════════════════════════════════════════════════════════════════════
# EXENCIÓN DE UPC — la regla para leer la evidencia
# ═════════════════════════════════════════════════════════════════════════════
# Walmart valida POR ETAPAS y solo reporta la PRIMERA que falla. Por eso hay
# exactamente dos clases de prueba válida:
#
#   ✅ POSITIVA  un SKU de esa categoría llegó a SUCCESS. Para lograrlo tuvo que
#                pasar el filtro de UPC. La exención existe.
#   ❌ NEGATIVA  Walmart contestó "not authorized to set up 'CUSTOM' Product
#                IDs". La exención NO existe.
#
# **Que no aparezca el error de UPC NO prueba nada**: el artículo pudo morir
# antes de esa etapa. Un lote entero se envió sobre esa suposición y se cayó.
# Las categorías sin prueba positiva viven en CATEGORIAS_POR_CONFIRMAR, abajo.
CATEGORIAS_AUTORIZADAS: dict[str, dict] = {
    "costumes": {
        "clave_visible": "Disfraces",
        "folio_exencion": "15728342",     # 4-ago-2026
        # 60141401 = "Disfraces o accesorios". La anterior (53102700) era
        # "Uniformes", no ropa genérica: verificado contra el catálogo oficial
        # del SAT (catCFDI_V_4, 52,513 claves).
        "clave_sat": 60141401,
        "prueba": "feed",                 # SUCCESS desde el 4-ago
        "pide_genero": True,
        "patron_categoria": "isfra|osplay",
        "patron_titulo": "isfra|osplay|allowee",
        "campos_visible": (
            "countPerPack", "material", "colorCategory", "modelNumber",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight",
            "size", "gender"),
    },
    "home_other": {
        "clave_visible": "Cocina, Decoración y Otros",
        "folio_exencion": "15751007",     # 5-ago-2026
        "clave_sat": 52151600,            # "Utensilios de cocina domésticos"
        "prueba": "feed",                 # SUCCESS desde el 5-ago
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
        "campos_visible": (
            "countPerPack", "material", "colorCategory", "modelNumber",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight",
            "size", "gender"),
    },
    # ── LA PUERTA GRANDE (folio 15777537, "Electrónicos") ────────────────
    # OJO CON EL NOMBRE. La exención de electrónica NO vive en "Accesorios
    # Electrónicos" (`electronics_accessories`): vive en "Electrónicos", que en
    # el esquema es `health_and_beauty_electronics`. El nombre engaña y la
    # taxonomía tiene 15 puertas de electrónica.
    #
    # Se probó mandando EL MISMO SKU (TEC-0018-NEG) a cinco puertas el 7-ago:
    #     Eléctricas        (electrical)          -> ❌ "not authorized"
    #     Cables            (electronics_cables)  -> ❌ "not authorized"
    #     Electrodomésticos (large_appliances)    -> ❌ "not authorized"
    #     Otros Electrónicos(electronics_other)   -> ❓ murió antes (Watts)
    #     Electrónicos      (health_and_beauty_…) -> ✅ pasó el filtro de UPC
    # Y la prueba definitiva: 182 artículos publicaron por esta puerta ese
    # mismo día. `electronics_accessories` NUNCA se probó — sus 5 feeds de 85
    # murieron todos en `modelNumber`, antes de llegar a la etapa de UPC.
    "health_and_beauty_electronics": {
        "clave_visible": "Electrónicos",
        "folio_exencion": "15777537",     # 7-ago-2026
        # 52161500 = "Equipos audiovisuales" (verificado en catCFDI_V_4).
        # ⚠ Es la clave con la que publicaron los 182, pero le queda CHICA al
        # catálogo real: por esta puerta entran herramientas, autopartes y
        # artículos deportivos (lo dice el `shelf` que Walmart les asignó). No
        # frena la publicación —Walmart solo valida el formato— pero el CFDI
        # sale mal. Afinarla por familia de SKU es trabajo de facturación.
        "clave_sat": 52161500,
        "prueba": "feed",                 # 182 artículos publicaron el 7-ago
        # `gender` es OPCIONAL aquí y así se mandó en los 182 que publicaron.
        "pide_genero": True,
        "prefijos_sku": ("TEC", "VEH", "VAR", "CORR", "ELEC"),
        "patron_categoria": "lectr|udio|celular|comput",
        "patron_titulo": "lectr|udio|celular|comput",
        # Bloque MUY corto: ni material, ni size, ni countPerPack, ni
        # modelNumber. Es exactamente lo que publicó el 7-ago.
        "campos_visible": (
            "colorCategory", "gender",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight"),
    },
    # ── JUGUETES (ticket del 2-sep-2026) ─────────────────────────────
    # Walmart confirmó por ticket la carga sin UPC para esta categoría. Es la
    # PRIMERA que entra aquí con la exención probada por escrito y no por un
    # feed que pasó — lo cual cierra la mitad del riesgo, no las dos.
    #
    # ⚠️ LA OTRA MITAD SIGUE ABIERTA. Los 33 del 7-ago no murieron por el UPC:
    # murieron por `countPerPack` (que NO existe en el bloque "Juguetes" del
    # esquema — verificado) y por dos obligatorios que el esquema PUBLICADO no
    # declara pero producción sí exige: `activity` y `productLine`. Su
    # `required` oficial son 7 campos y ninguno de los dos aparece; es el mismo
    # desfase de versión que ya mordió en "Cocina, Decoración y Otros".
    # Esa corrección está DEDUCIDA del mensaje de error, no comprobada: el
    # primer envío de esta categoría sigue siendo un piloto de 1 SKU.
    "toys_other": {
        "clave_visible": "Juguetes",
        # El número de folio vive en el ticket (2-sep-2026) y falta capturarlo.
        # Es SOLO para la bitácora: no viaja en el feed — la exención se ejerce
        # con `productIdType: GTIN / productId: CUSTOM`, igual que las otras tres.
        "folio_exencion": "ticket 2-sep-2026 (folio por capturar)",
        # 60141000 = "Juguetes", clase 6014 del segmento 60 del c_ClaveProdServ
        # (el mismo segmento que 60141401 "Disfraces o accesorios").
        "clave_sat": 60141000,
        # FEED, no solo ticket: los pilotos del 4-sep (JUGU-0264-ROS, -0201-MUL,
        # -0035-MUL, -0200-VER) están PUBLISHED en Walmart (medido por
        # /v3/items el 28-sep). `ops.channel_submissions` los sigue diciendo
        # "ENVIADO" porque los veredictos de feed no se escriben de vuelta.
        "prueba": "feed",
        "pide_genero": True,
        # `gender` SÍ es obligatorio aquí — está en el `required` oficial— y es
        # lista CERRADA: Unisex / Niño / Niña / Mujer / Hombre. `_genero()` ya
        # devuelve solo de esa lista.
        "prefijos_sku": ("JUGU", "JUG"),
        "patron_categoria": "uguete|eluche|didactic|didáctic",
        "patron_titulo": "uguete|eluche|didactic|didáctic",
        # SIN `countPerPack`: no existe en este bloque y tumbó los 33 completos.
        "campos_visible": (
            "material", "colorCategory", "modelNumber",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight",
            "size", "gender", "activity", "productLine"),
    },
    # ── PIJAMAS (ticket del 2-sep-2026) ─────────────────────────────
    # ⚠️ "Pijamas" NO EXISTE en el esquema. Se revisaron las 75 categorías: no
    # está ni con ese nombre ni con ninguno equivalente. La puerta más cercana
    # es "Ropa" (`clothing_other`), y por eso este renglón es un PILOTO: la
    # exención dice "Pijamas" y Walmart pudo habérsela dado a un anaquel más
    # chico que la categoría entera.
    #
    # POR ESO SE FILTRA POR PATRÓN Y **NO** POR PREFIJO DE SKU. `ROP` son 214
    # pijamas... dentro de un catálogo de ropa mucho mayor. Con `prefijos_sku`
    # entraría TODA la ropa por una exención de pijamas — justo el salto que ya
    # costó un lote ("que no aparezca el error de UPC NO prueba nada").
    #
    # Y OJO CON LOS CAMPOS: el bloque "Ropa" **no tiene** `assembledProduct*`
    # ni `size` — verificado contra el esquema. Mandarlos sería repetir
    # exactamente lo de `countPerPack` en Juguetes, que tumbó 33 de 33. Sus
    # obligatorios son solo cuatro: countPerPack, material, colorCategory y
    # gender. La talla de ropa vive en campos por prenda (pantSize, shoeSize…),
    # ninguno obligatorio y ninguno aplicable a un conjunto de pijama.
    "clothing_other": {
        "clave_visible": "Ropa",
        "folio_exencion": "ticket 2-sep-2026 (folio por capturar)",
        # 53102600 = "Ropa de dormir", clase del segmento 53. Es la clase de
        # 53102601 (pijamas de niño) y 53102602 (de hombre).
        "clave_sat": 53102600,
        # FEED: la pijama ROP-0417-ROS está PUBLISHED (productType "Pijamas",
        # /v3/items, 28-sep). Ojo con la razón que se dio el 4-sep —"no trajo
        # el error de UPC sino uno de atributo"—: un error de atributo sale
        # ANTES de la etapa de UPC, así que eso no probaba nada. Lo que prueba
        # es que hoy esté publicada.
        "prueba": "feed",
        "pide_genero": True,
        "patron_categoria": "ijama|amis[oó]n|ropa de dormir",
        "patron_titulo": "ijama|amis[oó]n|ropa de dormir",
        # `activity` va porque el PILOTO del 4-sep lo exigió, no porque el
        # esquema lo diga: su `required` son cuatro y este no aparece. Mismo
        # desfase que ya mordió en Juguetes y en Cocina.
        # `productLine` NO se manda: no existe en el bloque "Ropa" (sí en
        # "Juguetes"), y un campo de más tumba el artículo.
        "campos_visible": (
            "countPerPack", "material", "colorCategory", "modelNumber",
            "gender", "activity"),
    },

    # ═════════════════════════════════════════════════════════════════════
    # LAS DE SEPTIEMBRE — tickets de cinthya garcia, resueltos 16 al 18-sep
    # ═════════════════════════════════════════════════════════════════════
    # Cómo se armó la LISTA BLANCA de cada una, y por qué es la MÍNIMA:
    #
    #   obligatorios del esquema  +  `size`/`gender` cuando el bloque los tiene
    #   +  lo MEDIDO en producción  −  lo que producción RECHAZA.  Nada opcional.
    #
    # `size` y `gender` van aunque el 3.19 no los pida porque producción ya los
    # exigió en TRES categorías que el archivo no marcaba (Cocina el 5-ago,
    # Muebles y Blancos el 19-ago). Y NADA opcional porque un opcional de más no
    # gana nada y puede tumbar el lote: `modelNumber` existe en el esquema de
    # casi todas y producción lo rechazó en dos (85/85 muertos el 7-ago).
    #
    # Ninguna de estas tiene todavía un feed con SUCCESS (`prueba: "ticket"`):
    # el primer envío de cada una es un piloto, y la vista previa lo dice.
    #
    # ⚠️ DOS TICKETS QUE NO SE MAPEAN POR SU NOMBRE — misma trampa que colchones:
    #   · "ACCESORIOS PARA BEBÉS" (16296043) NO es «Portadores y Accesorios»:
    #     ese bloque es de EQUIPAJE (luggageType, bagStyle, isWheeled,
    #     zipperMaterial). Lo que Walmart autorizó por escrito son las SEIS
    #     categorías de bebé que nombra el aviso del 17-sep. Esas entran.
    #   · "COLCHONES" (15822204) no existe como categoría del feed: vive en
    #     «Blancos». Probado con SUCCESS, ver abajo.

    # ── BLANCOS / COLCHONES (folio 15822204) — PROBADA ───────────────────
    # Cinthya pidió "COLCHONES" y Walmart contestó "activado para la categoría _
    # y subcategoría _": los dos campos EN BLANCO, porque "Colchones" es un
    # departamento de la TIENDA y no una `subCategory` del feed. Sonda del
    # 19-ago, un SKU por puerta: «Muebles» -> "not authorized" en 3 minutos;
    # «Blancos» -> SUCCESS. CAM-0030-MAT y CAM-0030-QUE publicaron y Walmart los
    # estanteó en "Colchones y Blancos > Colchones > Matrimoniales / Queen
    # Size". Este renglón se escribió ese día y se perdió en una sincronización:
    # no había llegado a `main` hasta ahora.
    "bedding": {
        "clave_visible": "Blancos",
        "folio_exencion": "15822204",     # 11-ago-2026, pedido: COLCHONES
        # 52121500 = "Ropa de cama" (catCFDI_V_4). OJO: en agosto se anotó como
        # "Colchones y somieres" y es falso — por eso `sat_por_patron`.
        "clave_sat": 52121500,
        "sat_por_patron": (
            # 56101508 = "Colchones o sets para dormir".
            (r"colch[oó]n", 56101508),
        ),
        "prueba": "feed",
        "pide_genero": True,
        "material_default": "Poliéster",
        # "colchoneta" (de ejercicio) NO es colchón: `colch[oó]n` tiene que ir
        # seguido de espacio, plural, coma o fin — la colchoneta cae en Deportes.
        # "colcha" va entre espacios: suelta casaba dentro de "a-colcha-da" y
        # se llevaba sillas de campamento y fundas de laptop "acolchadas".
        "patron_categoria": ("colch[oó]n( |es|,|$)|almohada|s[aá]bana|edred[oó]n|"
                             "cobija|(^| )colchas?( |,|$)|cubrecama"),
        "patron_titulo": ("colch[oó]n( |es|,|$)|almohada|s[aá]bana|edred[oó]n|"
                          "cobija|(^| )colchas?( |,|$)|cubrecama"),
        # "Bolsas al vacío para ropa y edredones" es almacenamiento; la
        # colchoneta "para acampar" es de campismo; el juguete "palma calmante"
        # que Woo archiva en "Almohadas para Bebés" es un juguete.
        "excluir": ("al vac[ií]o|organizador|almacenamiento|juguete|acampar|camping|"
                    "perro|gato|mascota"),
        # Exactamente lo que llevaba CAM-0030-QUE, que publicó. `size` y `gender`
        # los exige producción aunque el 3.19 no los marque: sin ellos la primera
        # sonda murió en "`Talla` y `Género` son obligatorios".
        "campos_visible": (
            "countPerPack", "material", "colorCategory", "modelNumber",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight",
            "size", "gender"),
    },

    # ── BEBÉ — las SEIS del aviso del 17-sep ─────────────────────────────
    # Autorizadas por escrito en un solo aviso ("ya cuenta con la autorización
    # para realizar cargas sin UPC para las categorías: …"). El ticket que las
    # pidió fue "ACCESORIOS PARA BEBÉS" (16296043).
    #
    # Los patrones de TÍTULO son estrechos a propósito: "bebé" solo no dice
    # nada ("Disfraz de bebé dinosaurio" es un disfraz), y una categoría de bebé
    # mal elegida publica sin dar error. Lo que los patrones no alcancen lo
    # resuelve el selector del Estudio, que sugiere con IA y guarda cuando una
    # persona acepta.
    "child_car_seats": {
        "clave_visible": "Transporte del bebé",
        "folio_exencion": "aviso 17-sep-2026 (ticket 16296043)",
        "clave_sat": 56101800,            # "Accesorios y muebles de bebé y niño"
        "prueba": "ticket",
        "pide_genero": True,
        "patron_categoria": "carriola|carreola|portabeb[eé]",
        # Sin "andadera": su bloque es de carriolas, portabebés y autoasientos
        # (Estilo del Transporte, sistema LATCH), y una andadera no es nada de
        # eso. Si alguien la quiere aquí, la elige en el selector.
        "patron_titulo": ("carriola|carreola|cochecito|portabeb[eé]|autoasiento|"
                          "asiento (de|para) (auto|carro|coche) (para|de) beb|"
                          "arn[eé]s para (caminar|beb|ni[ñn])"),
        "excluir": ("triciclo|bicicleta|montable|patineta|scooter|"
                    "mu[ñn]ec[oa]|perro|gato|mascota"),
        "campos_visible": (
            "countPerPack", "material", "colorCategory",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight",
            "size", "gender"),
    },
    "baby_furniture": {
        "clave_visible": "Muebles del bebé",
        "folio_exencion": "aviso 17-sep-2026 (ticket 16296043)",
        "clave_sat": 56101800,            # "Accesorios y muebles de bebé y niño"
        "prueba": "ticket",
        "pide_genero": True,
        "patron_categoria": "muebles? (para|de) beb",
        "patron_titulo": ("(^| )cunas?( |,|$)|corral (para|de) beb|cambiador de pa[ñn]al|"
                          "mois[eé]s|periquera|silla alta (para|de) beb|"
                          "mecedora (para|de) beb"),
        "excluir": "mu[ñn]ec[oa]|perro|gato|mascota",
        "campos_visible": (
            "countPerPack", "material", "colorCategory",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight",
            "size", "gender"),
    },
    "baby_toys": {
        "clave_visible": "Juguetes de bebé",
        "folio_exencion": "aviso 17-sep-2026 (ticket 16296043)",
        "clave_sat": 60141000,            # "Juguetes"
        "prueba": "ticket",
        "pide_genero": True,
        # `educationalFocus` es OBLIGATORIO en este bloque (y SOLO en este):
        # texto libre, lista de al menos uno. Sin él cada artículo rebotaría con
        # "`Enfoque Educativo` is a required attribute". El valor sale del
        # ejemplo del propio esquema y la IA lo afina al generar el contenido.
        "educational_focus_default": "Habilidades motoras",
        "patron_categoria": "juguetes? (para|de) beb",
        "patron_titulo": ("mordedera|mordedor|sonaja|gimnasio (para|de) beb|"
                          "m[oó]vil (para|de) cuna|juguetes? (para|de) beb"),
        # "Set 7 juguetes para perro cuerda mordedor": el mordedor es de perro.
        "excluir": "perro|gato|mascota",
        "campos_visible": (
            "countPerPack", "material", "colorCategory",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight",
            "size", "gender", "educationalFocus"),
    },
    "baby_clothing": {
        "clave_visible": "Ropa de Bebé",     # "Bebé" con MAYÚSCULA: así la escribe el esquema
        "folio_exencion": "aviso 17-sep-2026 (ticket 16296043)",
        # 53101605 = "Camisas o blusas para bebé". El SAT no tiene una clase de
        # "ropa de bebé": reparte por prenda, y esta es la más general.
        "clave_sat": 53101605,
        "prueba": "ticket",
        "pide_genero": True,
        "patron_categoria": "ropa (para|de) beb",
        "patron_titulo": ("mameluco|pa[ñn]alero|body (para|de) beb|ropa (para|de) beb|"
                          "conjunto (para|de) beb|pelele"),
        "excluir": "mu[ñn]ec[oa]|perro|gato|mascota",
        # El bloque es CORTO: no tiene medidas, ni `size`, ni `material`. Sus
        # obligatorios son tres. (La talla de bebé vive en `babyClothingSize`,
        # opcional y con su propia lista.)
        "campos_visible": ("countPerPack", "colorCategory", "gender"),
    },
    "baby_other": {
        "clave_visible": "Pañale cuidado del bebé y otro",   # literal del esquema, errata incluida
        "folio_exencion": "aviso 17-sep-2026 (ticket 16296043)",
        "clave_sat": 53102305,            # "Pañales para bebé"
        "prueba": "ticket",
        "pide_genero": True,
        "patron_categoria": "pa[ñn]al|cuidado del beb",
        "patron_titulo": ("pa[ñn]al(es)? (desechable|de tela|para beb)|monitor (para|de) beb|"
                          "toallitas h[uú]medas|cambiador port[aá]til|aspirador nasal|"
                          "term[oó]metro (para|de) beb|cortau[ñn]as (para|de) beb|"
                          "ba[ñn]era (para|de) beb|tina (para|de) beb"),
        # Woo agrupa en "Pañales" también los pañales para perro.
        "excluir": "mu[ñn]ec[oa]|perro|gato|mascota",
        "campos_visible": (
            "countPerPack", "material", "colorCategory", "size", "gender"),
    },
    # Es COMIDA de bebé (sabor, porciones, calorías, nutrientes), no accesorios
    # para alimentar. Queda autorizada porque lo está, pero SIN patrones: el
    # catálogo de Kubera no vende alimentos, y un biberón clasificado aquí
    # saldría con la ficha de un alimento. Solo entra si una persona la elige.
    "baby_food": {
        "clave_visible": "Alimentacion del bebé",   # sin acento en "Alimentacion": literal del esquema
        "folio_exencion": "aviso 17-sep-2026 (ticket 16296043)",
        "clave_sat": 50193000,            # "Bebidas y Comidas Infantiles"
        "prueba": "ticket",
        "pide_genero": False,
        # Su único obligatorio. Sin `size`: aquí "Unitalla" sería un disparate
        # publicado en la ficha de un alimento.
        "campos_visible": ("countPerPack",),
    },

    # ── ALMACENAMIENTO (ticket 16292474, 16-sep) ─────────────────────────
    # Los 83 del 7-ago murieron en `'gender' is not a valid field` — el bloque
    # no lo tiene y producción lo rechaza. Nunca llegaron a la etapa de UPC, así
    # que hasta este ticket la exención no estaba ni probada ni negada.
    "storage": {
        "clave_visible": "Almacenamiento",
        "folio_exencion": "16292474",     # 16-sep-2026
        "clave_sat": 24112400,            # "Cofres, armarios y baúles de almacenaje"
        "prueba": "ticket",
        "pide_genero": True,              # arma `size`; `gender` lo poda la lista
        # Por PATRÓN y no por prefijo `ORG`: dentro de ORG hay SKUs reciclados
        # (ORG-0245-MUL es un inflable de Halloween). Lo que no case con estas
        # palabras lo resuelve el selector del Estudio.
        "patron_categoria": "rganizaci[oó]n|lmacenamiento|rganizador",
        "patron_titulo": ("organizador|cajas? organizadora|cajas? de almacenamiento|"
                          "contenedor(es)? de almacenamiento|zapatera|bolsas? al vac[ií]o|"
                          "canastas? organizadora|cesto organizador"),
        # Su bloque SÍ es de estantes y cajones —"Estilo de estantería",
        # "Número de Anaqueles", "Número de cajones"—, así que un estante
        # organizador cabe aquí. Lo que es mueble de sala, cama o asiento es
        # «Muebles» (sin exención), y el organizador de coche es de autos.
        #
        # La categoría de Woo "Organizadores de …" la pone ML a los ACCESORIOS
        # de cada cosa, no a lo que organiza: medido el 28-sep, por ahí entraban
        # un fregadero ("Organizadores de Fregaderos"), una bandeja para volante,
        # un protector de cable, una carpeta de argollas y un estuche de
        # estetoscopio. El título los saca.
        "excluir": ("para (tv|televisi[oó]n)|aparador|vajillero|librero|(^| )camas?( |,|$)|"
                    "cabecera|sof[aá]|sill[oó]n|silla|taburete|"
                    "carro|coche|cajuela|volante|autom[oó]vil|(^| )auto( |,|$)|"
                    "^fregadero|tarja|vaso|carpeta|cable|cargador|estetoscopio|"
                    "funda para|bolsa[^,]{0,25}herramientas|perro|gato|mascota"),
        "campos_visible": (
            "countPerPack", "material", "colorCategory",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight", "size"),
    },

    # ── ELECTRODOMÉSTICOS (ticket 16295669, 16-sep) ───────────────────────
    # El 7-ago esta puerta dijo "not authorized". Pero esa misma sonda dejó un
    # dato útil: LLEGÓ a la etapa de UPC, o sea que su juego de campos
    # (material, colorCategory, medidas, size) ya pasó la validación de campos.
    # Es el mismo juego que va aquí, sin `modelNumber`.
    #
    # Va DESPUÉS de «Electrónicos» a propósito: lo `TEC-*` sigue saliendo por la
    # puerta probada (182 publicados). Esta cubre lo que no es TEC.
    "large_appliances": {
        "clave_visible": "Electrodomésticos",
        "folio_exencion": "16295669",     # 16-sep-2026
        "clave_sat": 52141800,            # "Otros electrodomésticos"
        "sat_por_patron": (
            (r"refrigerador|congelador|frigobar|minibar|lavavajillas|lavaplatos|"
             r"estufa|horno|microondas|campana", 52141500),   # "Electrodomésticos para cocina"
        ),
        "prueba": "ticket",
        "pide_genero": True,              # arma `size`; no hay `gender` en el bloque
        "patron_categoria": "lectrodom[eé]stico|l[ií]nea blanca",
        # El aparato tiene que ABRIR el título. Con el patrón suelto, 7 de 8
        # aciertos eran accesorios: "Base rodante para lavadora", "Manguera
        # para hidrolavadora", "Imán para refrigerador", "Lonchera … apta para
        # microondas". El bloque pide BTU, carga y etiqueta energética: es de
        # aparatos, no de lo que se les pone.
        "patron_titulo": ("^(mini |nuev[oa] |port[aá]til )?(refrigerador|lavadora|"
                          "secadora de ropa|centro de lavado|estufa|horno de microondas|"
                          "microondas|lavavajillas|lavaplatos|congelador|frigobar|"
                          "minibar|campana extractora|calentador de agua|boiler|"
                          "aire acondicionado|minisplit|calefactor|deshumidificador|"
                          "purificador de aire)"),
        "excluir": ("para (lavadora|refrigerador|microondas|estufa)|hidrolavadora|"
                    "im[aá]n|funda|cubierta|refacci[oó]n|repuesto|filtro|manguera|"
                    "juguete|camping|acampar"),
        "campos_visible": (
            "material", "colorCategory",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight", "size"),
    },

    # ── ACCESORIOS ELECTRÓNICOS (ticket 16296156, 16-sep) ─────────────────
    # La puerta que PARECÍA la buena en agosto y nunca se probó: sus 5 feeds de
    # 85 murieron en `modelNumber` antes de la etapa de UPC. `modelNumber` sigue
    # FUERA (rechazo medido). No hay `countPerPack` ni `gender` en el bloque.
    #
    # Igual que Electrodomésticos: lo `TEC-*` sigue yendo a «Electrónicos» por
    # prefijo. Esta toma los accesorios que NO son TEC, y los TEC que alguien
    # mande aquí a mano desde el selector.
    "electronics_accessories": {
        "clave_visible": "Accesorios Electrónicos",
        "folio_exencion": "16296156",     # 16-sep-2026
        "clave_sat": 52161500,            # "Equipos audiovisuales"
        "prueba": "ticket",
        "pide_genero": True,              # arma `size`; `gender` lo poda la lista
        "patron_categoria": "ccesorios (para )?(celular|electr)|porta ?celular",
        "patron_titulo": ("funda para (celular|tel[eé]fono|tablet|laptop)|"
                          "soporte para (celular|tel[eé]fono|tablet)|protector de pantalla|"
                          "(vidrio|cristal) templado (para|de) (celular|tel[eé]fono|pantalla|"
                          "iphone|samsung|tablet)|mica (para|de) (celular|tel[eé]fono)|"
                          "cargador (inal[aá]mbrico|usb|para celular)|"
                          "protector(es)? de cable|organizador(es)? de cables?|"
                          "funda[^,]{0,30}(para|de) (laptop|tablet)|"
                          "cable (usb|tipo c|lightning)|power ?bank|bater[ií]a port[aá]til|"
                          "hub usb|adaptador usb"),
        "campos_visible": (
            "material", "colorCategory",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight", "size"),
    },

    # ── OTROS DEPORTES Y RECREACIÓN (aviso 17-sep, Paola) ────────────────
    # La única de septiembre que Walmart nombró literal en la respuesta.
    "sport_and_recreation_other": {
        "clave_visible": "Otros Deportes y Recreación",
        "folio_exencion": "aviso 17-sep-2026 (Seller Support)",
        "clave_sat": 49221500,            # "Accesorios para deporte"
        "prueba": "ticket",
        "pide_genero": True,
        "patron_categoria": "eportes?|itness|jercicio|ampismo|camping",
        "patron_titulo": ("yoga|pilates|mancuerna|pesa rusa|kettlebell|"
                          "(liga|banda)s? de resistencia|cuerda para saltar|colchoneta|"
                          "tapete (de yoga|de ejercicio)|guantes de box|costal de box|"
                          "tienda de campa[ñn]a|casa de campa[ñn]a|bolsa de dormir|"
                          "hamaca|ca[ñn]a de pescar|barra de dominadas|ejercitador|"
                          "acampar|camping"),
        # La ropa y el calzado "de yoga" son ropa (su bloque pide talla de
        # prenda); el masajeador y la faja lumbar, salud. Woo los archiva en
        # "Ejercitadores" y "Fajas … Abdominales", y el patrón los alcanzaba.
        "excluir": ("chamarra|sudadera|playera|camiseta|pantal[oó]n|leggings?|"
                    "calcetas?|calcetines|brasier|sost[eé]n|zapatos?|zapatillas|"
                    "masajeador|faja|lumbar|beb[eé]|perro|gato|mascota"),
        "campos_visible": (
            "countPerPack", "material", "colorCategory",
            "assembledProductLength", "assembledProductWidth",
            "assembledProductHeight", "assembledProductWeight",
            "size", "gender"),
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# CATEGORÍAS **NO** AUTORIZADAS TODAVÍA — no mover de aquí sin prueba positiva
# ─────────────────────────────────────────────────────────────────────────────
# Esta tabla existe para que nadie vuelva a leer "no salió el error de UPC"
# como "sí tenemos la exención". Cada renglón dice qué evidencia hay y cuál
# falta. Un piloto de UN SKU la resuelve; el lote completo NO es un piloto.
#
# El folio 15822204 (COLCHONES) quedó resuelto con una sonda: abre «Blancos», no
# «Muebles». El 15776196 sigue sin categoría conocida. Lo de abajo se confirma
# en Seller Center o con un piloto de un SKU, nunca suponiendo.
CATEGORIAS_POR_CONFIRMAR: dict[str, dict] = {
    "furniture_other": {
        "clave_visible": "Muebles",
        "evidencia": "❌ NEGATIVA — 39 SKUs con 'not authorized' el 7-ago, y "
                     "RECONFIRMADA el 19-ago (sonda de colchones: rebotó en 3 min)",
        "que_falta": "ticket de exención propio para 'Muebles'. El 15822204 NO la "
                     "cubre: ese abrió «Blancos». Los muebles de BEBÉ sí tienen "
                     "la suya («Muebles del bebé»)",
        "skus_esperando": 96,
    },
    # «Almacenamiento» y «Accesorios Electrónicos» SALIERON de aquí el 28-sep:
    # tickets 16292474 y 16296156. Ver CATEGORIAS_AUTORIZADAS.
    #
    # NO confundir con el ticket "ACCESORIOS PARA BEBÉS" (16296043): esta es la
    # de EQUIPAJE y bolsos (luggageType, bagStyle, isWheeled). Emparejarla por
    # el nombre habría publicado maletas con la exención de las de bebé.
    "carriers_and_accessories_other": {
        "clave_visible": "Portadores y Accesorios",
        "evidencia": "❓ NINGUNA — ningún ticket la nombra",
        "que_falta": "ticket de exención propio si se van a publicar maletas o bolsos",
        "skus_esperando": 0,
    },
    "office_other": {
        "clave_visible": "Papelería",
        "evidencia": "❓ NINGUNA — nunca se mandó nada",
        "que_falta": "piloto de 1 SKU",
        "skus_esperando": 27,
    },
    "tools": {
        "clave_visible": "Herramientas",
        "evidencia": "❓ NINGUNA — nunca se mandó nada por esta puerta",
        "que_falta": "piloto de 1 SKU",
        "skus_esperando": 7,
    },
}

# ⚠️ NO todo el catálogo de hogar cae en "Cocina, Decoración y Otros". El
# esquema tiene grupos SEPARADOS que esta exención NO cubre:
#     storage              -> "Almacenamiento"        (cajas, organizadores)
#     furniture_other      -> "Muebles"               (mesas, sillas, estantes)
#     decorations_and_favors -> "Adornos y Decoraciones"
# Para publicar ahí hay que pedir la exención de cada una por separado en
# sellerhelp.mx.walmart.com.

# Artículos por feed. La doc dice 10,000; la realidad es otra cosa y hay tres
# fuentes independientes que apuntan al mismo número:
#   · lo MEDIDO: 343 artículos -> SYSTEM_ERROR.GMP_GATEWAY_API; 85 pasan. El
#     gateway revienta por CONTEO, no por peso (937 KB contra un tope de 25 MB).
#   · lo único que Walmart publica cerca de la realidad: "Max SKUs per call: 50".
#   · el paginado del veredicto: `GET /v3/feeds/{id}?includeDetails=true` topa
#     en 50 entidades. Con lotes más grandes el resumen por SKU sale INCOMPLETO
#     y en silencio — la misma clase de falso positivo que produjo los "9 feeds
#     sin fallos" del 4-ago que en realidad fueron 0.
# Con 50 las tres cosas se alinean y el veredicto es completo.
TAM_LOTE = 50
LIMITE_BYTES_FEED = 9 * 1024 * 1024      # 10 MB reales, con margen

# Segundos entre feeds cuando hay más de un lote.
#
# El presupuesto de MP_ITEM_INTL es de 10 feeds POR HORA. Con 20 s de pausa, 10
# feeds seguidos queman la hora entera en 3.3 minutos y el resto de la corrida
# muere en REQUEST_THRESHOLD_VIOLATED — que es exactamente lo que tumbó 19 de 24
# productos sin que hubiera nada malo en sus datos. 360 s = 10 feeds/hora justos.
PAUSA_ENTRE_LOTES = 360

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
    sin_reglas = not (cfg.get("patron_categoria") or cfg.get("patron_titulo")
                      or cfg.get("prefijos_sku"))
    if sin_reglas:
        # «Alimentacion del bebé» no tiene patrones A PROPÓSITO: solo entra lo
        # que una persona eligió en el selector del Estudio. La tanda no adivina.
        print(f"«{cfg['clave_visible']}» no tiene reglas: solo entran los SKUs "
              f"elegidos en el selector del Estudio (o los que pases con --skus).")
    # Un patrón vacío casaría con TODO en REGEXP; el que falte se neutraliza.
    PATRON_CATEGORIA = cfg.get("patron_categoria") or "a^"
    PATRON_TITULO = cfg.get("patron_titulo") or "a^"

    # El prefijo del SKU es una vía ALTERNA al texto, no un filtro encima. Un
    # sartén puede estar en la categoría "Sartenes" y titularse "Sartén
    # antiadherente 24 cm": ni la categoría ni el título dicen "cocina", pero el
    # prefijo COC sí. Al revés, media electrónica dice "iluminación" en el
    # título sin ser de hogar — por eso el prefijo también acota.
    prefijos = cfg.get("prefijos_sku") or ()
    patron_sku = ("^(" + "|".join(prefijos) + ")-") if prefijos else None

    P = wp_db._prefix()
    # ⚠️ EL SQL NO DECIDE: trae un SUPERCONJUNTO (cualquier patrón de la
    # categoría, en el título o en cualquiera de sus categorías de Woo, o el
    # prefijo) y decide `resolver_categoria()` — la MISMA función del botón del
    # panel: elección del panel > reglas, con el ORDEN de la tabla y `excluir`.
    # Medido el 28-sep: decidiendo por SQL, la tanda de «Accesorios
    # Electrónicos» se llevaba 25 `TEC-*` que el botón manda a «Electrónicos»;
    # la de Deportes, 33 (pelotas `JUGU-*`, linternas `TEC-*`).
    cond = "(t.name REGEXP %s OR t.name REGEXP %s OR p.post_title REGEXP %s"
    params: list = [PATRON_CATEGORIA, PATRON_TITULO, PATRON_TITULO]
    if patron_sku:
        cond += " OR m2.meta_value REGEXP %s"
        params.append(patron_sku)
    cond += ")"

    filas = [] if sin_reglas else wp_db._fetch_all(f"""
        SELECT MAX(m2.meta_value) AS sku, MAX(p.post_title) AS nombre,
               (SELECT GROUP_CONCAT(t2.name SEPARATOR ' ')
                  FROM {P}term_relationships tr2
                  JOIN {P}term_taxonomy tt2 ON tt2.term_taxonomy_id = tr2.term_taxonomy_id
                                           AND tt2.taxonomy = 'product_cat'
                  JOIN {P}terms t2 ON t2.term_id = tt2.term_id
                 WHERE tr2.object_id = p.ID) AS cats
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
    # Las elecciones del panel, TODAS en una consulta (no una por SKU). Si
    # kubera no contesta se decide solo por reglas — igual que el botón — y se
    # dice, porque una elección ignorada es justo lo que la regla 2 prohíbe.
    from services.publicar_walmart import resolver_categoria
    elecciones: dict[str, str] = {}
    try:
        from services import supabase_db as sdb
        elecciones = {str(r["sku"]): str(r["category_id"]) for r in sdb.fetch_all(
            """select sku::text as sku, category_id from channel.product_category
                where channel_id = 'walmart'""")}
    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠ sin las elecciones del panel ({exc}): decido solo por reglas")
    etiqueta = cfg["clave_visible"]
    # El prefijo ya no se filtra aquí: lo aplica `clasificar()` en su orden, y
    # un SKU que una persona mandó a esta categoría desde el selector entra
    # aunque su prefijo sea de otra familia (regla 2 de la casa).
    skus = sorted(
        {f["sku"] for f in filas if f["sku"] and (resolver_categoria(
            f["sku"], f["nombre"] or "", f["cats"] or "",
            elegida=elecciones.get(f["sku"]))[1] or {}).get("clave_visible") == etiqueta}
        | {sku for sku, cat in elecciones.items() if cat == etiqueta})
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


def _clave_sat(cfg: dict, nombre: str) -> int:
    """
    La clave SAT de ESTE producto: la de la categoría, salvo que un patrón de
    `sat_por_patron` la afine. Walmart solo valida el formato, pero la factura
    la usa tal cual — en «Blancos» un colchón no es "Ropa de cama".
    """
    import re
    for patron, clave in cfg.get("sat_por_patron") or ():
        if re.search(patron, nombre or "", re.I):
            return clave
    return cfg["clave_sat"]


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

    # Los dos campos que el esquema NO declara obligatorios y producción SÍ
    # exige en "Juguetes". Se arman solo si la categoría los pide: fuera de
    # ella son campos de más, y un campo de más tumba el artículo.
    #
    # ⚠️ Sin esto, declararlos en `campos_visible` no sirve de nada: la lista
    # blanca de abajo PODA, no crea. Los 33 juguetes del 7-ago se iban a volver
    # a caer exactamente igual.
    blanca_cfg = cfg.get("campos_visible") or ()
    if "activity" in blanca_cfg:
        # array de strings, minItems 1 (esquema oficial).
        visible["activity"] = [(_attr(atrs, "actividad")
                                or cfg.get("activity_default", "Juego"))[:600]]
    if "productLine" in blanca_cfg:
        # string libre. La marca es el único valor real que tenemos.
        visible["productLine"] = (atrs.get("BRAND")
                                  or cfg.get("product_line_default")
                                  or "Ferrahome")[:400]
    if "educationalFocus" in blanca_cfg:
        # «Juguetes de bebé» lo exige (array de texto, minItems 1). Mismo caso
        # que `activity` en Juguetes: sin armarlo aquí, la lista blanca lo
        # declara y el feed sale sin él — y cada artículo rebota.
        visible["educationalFocus"] = [(cfg.get("educational_focus_default")
                                        or "Habilidades motoras")[:600]]

    # LA PODA. Un solo campo de más tumba el artículo y arrastra el lote (85/85
    # por `modelNumber`, 83/83 por `gender`, 33/33 por `countPerPack`). Si la
    # categoría declara su lista blanca, aquí se recorta a ella; si no la
    # declara, se manda todo como siempre — así ninguna categoría vieja cambia
    # de comportamiento por este cambio.
    blanca = cfg.get("campos_visible")
    if blanca:
        visible = {k: v for k, v in visible.items() if k in blanca}

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
                "ProductTaxCode": _clave_sat(cfg, p.get("name") or ""),
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
            # 1000 px, no 500. El esquema oficial recomienda 1000×1000 para
            # `mainImageUrl` y la guía de Walmart pide 1500×1500 para el zoom.
            # Con el umbral en 500 pasaban imágenes que Walmart iba a rechazar
            # de todos modos. (Revisado el 6-ago: ninguna de las 37 enviadas
            # caía ahí, así que este NO fue el motivo de esos rechazos — pero
            # habría mordido al escalar a 500 SKUs.)
            if im.format == "JPEG" and min(im.width, im.height) >= 1000:
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
    # `limit` va EXPLÍCITO: el default de Walmart es 20 y el máximo 50. Sin él,
    # un feed de 50 artículos devolvía el detalle de 20 y los otros 30 no
    # aparecían en `por_sku` — el resumen los daba por buenos sin haberlos
    # mirado. Y se pagina con `offset`, porque `nextCursor` no llega en MX.
    por_sku: dict[str, tuple[str, list[str]]] = {}
    estado, off = "?", 0
    while off < TAM_LOTE + 50:
        r = await cx.get(f"{HOST}/v3/feeds/{fid}", headers=_h(tk),
                         params={"includeDetails": "true", "limit": 50,
                                 "offset": off}, timeout=90.0)
        if r.status_code != 200:
            return ("CONSULTA_FALLIDA", por_sku) if por_sku else ("CONSULTA_FALLIDA", {})
        s = r.json()
        estado = s.get("feedStatus") or "?"
        trozo = (s.get("itemDetails") or {}).get("itemIngestionStatus", [])
        for d in trozo:
            sku = d.get("sku") or "?"
            errs = [e.get("description", "")[:180]
                    for e in (d.get("ingestionErrors") or {}).get("ingestionError", [])]
            por_sku[sku] = (d.get("ingestionStatus") or "INPROGRESS", errs)
        if len(trozo) < 50:
            break
        off += 50
    return estado, por_sku


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


# ═════════════════════════════════════════════════════════════════════════════
# BITÁCORA — qué se mandó y qué contestó Walmart, en ops.channel_submissions
#
# POR QUÉ EXISTE: hasta hoy el `feedId` moría en una variable local. Para saber
# qué pasó con un artículo había que volver a preguntarle a Walmart, y
# preguntarle CUESTA: el corte por REQUEST_THRESHOLD_VIOLATED tumbó 19 de 24
# productos del segundo lote sin que hubiera nada malo en sus datos (ver el
# encabezado de estado_walmart.py). Con esto la pregunta se contesta en local.
#
# IDEMPOTENTE SIN DDL: cada INSERT trae su propio `where not exists` sobre
# (canal, submission_id, sku), y el veredicto es UPDATE, no INSERT — por eso
# las 6 rondas de consulta de FASE 4 no duplican nada.
#
# NO se apoya en un índice único porque `ops.channel_submissions` no tiene
# ninguno, y hoy no se le puede crear: las 369 filas de tiktok del 11-ago
# comparten un solo `detail_ref` de lote (`tiktok:lote:20260811`, 252 SKUs
# distintos). Medido con scripts/prechequeo_unique_submissions.py. Aquí el
# `detail_ref` se escribe POR FILA, que es como debió hacerse allá.
#
# NUNCA rompe la publicación: si la BD no contesta, se anota y el script sigue
# mandando. Publicar es su trabajo; registrar es el extra.
# ═════════════════════════════════════════════════════════════════════════════
class _Bitacora:
    """Escribe a ops.channel_submissions. Callada ante fallos, ruidosa al final."""

    def __init__(self, activa: bool) -> None:
        self.activa = activa
        self.corrida = uuid.uuid4().hex[:12]
        self.con = None
        self.escritas = 0
        self.actualizadas = 0
        self.sin_maestro: list[str] = []
        self.fallos: list[str] = []
        if not activa:
            return
        dsn = os.getenv("SUPABASE_DB_URL") or os.getenv("KUBERA_DB_URL") or ""
        if not dsn:
            self.activa = False
            self.fallos.append("no hay SUPABASE_DB_URL en el entorno")
            return
        try:
            import psycopg2
            self.con = psycopg2.connect(dsn, connect_timeout=20)
            # autocommit: cada fila es su propia transacción, así un SKU que no
            # está en core.products no envenena al resto del lote.
            self.con.autocommit = True
        except Exception as e:  # noqa: BLE001
            self.activa = False
            self.fallos.append(f"conexión: {str(e)[:120]}")

    def _ejecutar(self, sql: str, args: tuple) -> int | None:
        """rowcount, o -1 si el SKU no está en el maestro, o None si falló."""
        if not self.activa or self.con is None:
            return None
        try:
            with self.con.cursor() as cur:
                cur.execute(sql, args)
                return cur.rowcount
        except Exception as e:  # noqa: BLE001
            texto = str(e).lower()
            if "foreign key" in texto or "core.products" in texto or "fkey" in texto:
                return -1
            self.fallos.append(str(e)[:140])
            return None

    def _anotar(self, sku: str, n: int | None) -> None:
        if n == -1:
            self.sin_maestro.append(sku)
        elif n:
            self.escritas += n

    def enviado(self, fid: str, skus: list[str]) -> None:
        """Una fila por SKU al mandar el feed. Veredicto todavía pendiente."""
        for sku in skus:
            self._anotar(sku, self._ejecutar(
                """insert into ops.channel_submissions
                     (canal, cuenta, sku, submission_id, operacion, status,
                      detail_ref, submitted_at)
                   select 'walmart', '', %s, %s, 'alta', 'ENVIADO', %s, now()
                    where not exists (
                      select 1 from ops.channel_submissions
                       where canal = 'walmart' and submission_id = %s
                         and sku = %s)""",
                (sku, fid, f"walmart:feed:{fid}:{sku}", fid, sku)))

    def envio_fallido(self, skus: list[str], motivo: str) -> None:
        """Walmart ni aceptó el feed: no hay feedId. Se marca por corrida."""
        for sku in skus:
            ref = f"walmart:envio_fallido:{self.corrida}:{sku}"
            self._anotar(sku, self._ejecutar(
                """insert into ops.channel_submissions
                     (canal, cuenta, sku, operacion, status, success,
                      error_resumen, detail_ref, submitted_at)
                   select 'walmart', '', %s, 'alta', 'ENVIO_FALLIDO', false,
                          %s, %s, now()
                    where not exists (
                      select 1 from ops.channel_submissions
                       where detail_ref = %s)""",
                (sku, motivo[:500], ref, ref)))

    def veredicto(self, fid: str, sku: str, estado: str,
                  errores: list[str]) -> None:
        """UPDATE, no INSERT: por eso re-consultar el feed no duplica filas."""
        gano = estado in ("SUCCESS", "PROCESSED")
        n = self._ejecutar(
            """update ops.channel_submissions
                  set status = %s, success = %s, error_resumen = %s,
                      published_at = case when %s then now()
                                          else published_at end
                where canal = 'walmart' and submission_id = %s and sku = %s""",
            (estado, gano, (" · ".join(errores))[:500] or None, gano, fid, sku))
        if n and n > 0:
            self.actualizadas += n

    def resumen(self) -> str:
        if not self.activa and not self.fallos:
            return "   Bitácora                 : apagada"
        out = [f"   Bitácora                 : {self.escritas} filas nuevas, "
               f"{self.actualizadas} veredictos"]
        if self.sin_maestro:
            faltan = sorted(set(self.sin_maestro))
            out.append(f"      ⚠ {len(faltan)} SKU sin fila en core.products, "
                       f"NO registrados: {', '.join(faltan[:8])}"
                       + (" …" if len(faltan) > 8 else ""))
            out.append("        (los agrega el cron etl-core-products 06:15 UTC)")
        if self.fallos:
            out.append(f"      ⚠ {len(self.fallos)} error(es) de BD; "
                       f"el primero: {self.fallos[0]}")
        return "\n".join(out)

    def cerrar(self) -> None:
        if self.con is not None:
            try:
                self.con.close()
            except Exception:  # noqa: BLE001
                pass


async def main() -> int:
    # QUIÉN corre esto. Las 127 altas que este script dejó en
    # `ops.channel_submissions` no tienen dueño: un script no pasa por el
    # middleware, así que nada firmaba sus filas. Se exige SIEMPRE, incluso sin
    # `--aplicar` — una excepción ("salvo en dry-run") es la costura donde se
    # forma la costumbre de saltárselo.
    from core import actor
    _como = None
    if "--como" in sys.argv:
        i = sys.argv.index("--como") + 1
        _como = sys.argv[i] if i < len(sys.argv) else None
    actor.fijar_desde_cli(_como)   # aborta si no se declaró

    aplicar = "--aplicar" in sys.argv
    # Sin --aplicar no se manda nada, así que no hay nada que registrar.
    bitacora = _Bitacora(aplicar and "--sin-bitacora" not in sys.argv)
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
    refrescar = "--refrescar-imagenes" in sys.argv
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
        if refrescar and skus:
            # Walmart parece CACHEAR el fallo por URL: las tres imágenes que
            # rechazó el 4-ago volvieron a ser rechazadas el 5-ago con el mismo
            # mensaje, estando perfectas (JPEG, 1600×1600, 197 KB, descargables
            # desde fetchers externos de datacenter). Borrar el caché de
            # `amazon_imagenes` fuerza a regenerarlas: WordPress les pone un
            # nombre nuevo y Walmart las ve como URLs que nunca ha visto.
            from services import db as _db
            ph = ",".join(["%s"] * len(skus))
            try:
                _db.execute(f"DELETE FROM amazon_imagenes WHERE sku IN ({ph})",
                            tuple(skus))
                print(f"   caché de imágenes borrado para {len(skus)} SKUs "
                      f"(se regeneran con URL nueva)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"   no se pudo borrar el caché: {e}", flush=True)

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
                bitacora.enviado(fid, lote)
            else:
                for s in lote:
                    resultados.append((s, "ENVIO_FALLIDO", [estado]))
                bitacora.envio_fallido(lote, estado)

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
                        bitacora.veredicto(fid, sku, st, errs)
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
    print(bitacora.resumen())
    bitacora.cerrar()
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
