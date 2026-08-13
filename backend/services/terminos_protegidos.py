"""
terminos_protegidos.py — Marcas registradas en el contenido: detectar y sustituir.

POR QUÉ NO SE LE DEJA A LA IA
-----------------------------
El prompt ya le dice a la IA que no use marcas de terceros. Eso no basta: un
modelo que redacta "acabado tipo Pandora" o "sonido estilo Bose" no está
desobedeciendo, está *describiendo* — y Amazon no contesta con un error, retira
el listado. Es el mismo patrón que costó `TEC-1812-NEG` (publicado en "Máquinas
de Coser" por fiarse de un detector automático): **la IA propone, el código
valida.**

Así que la lista vive aquí, es finita, es legible y se puede auditar. Nada de
"detección semántica".

LAS DOS COSAS QUE HACE, Y LA QUE NO HACE A PROPÓSITO
----------------------------------------------------
1. **Sustituye** la marca por un genérico en español que deja la frase
   entendible ("acabado tipo Pandora" → "acabado tipo charms estilo europeo").
2. **Reporta** cada sustitución con el texto exacto que pide Brandon:
   `"Se detectó 'Pandora' en la descripción — reemplazado por '…'"`.
3. **NO sustituye dentro de una frase de compatibilidad** ("compatible con
   iPhone", "apto para Nissan"). Ahí la marca es información legítima del
   producto y cambiarla convierte una funda de iPhone en una funda de
   "smartphone 15": corrupción silenciosa del contenido, que es justo el modo de
   falla que este proyecto lleva semanas pagando. Esas menciones se REPORTAN
   para que un humano decida, pero el texto se respeta.

`Ferrahome` y `Generic` son nuestras: nunca se tocan.
"""
from __future__ import annotations

import re

# ── La lista ────────────────────────────────────────────────────────────────
#
# El reemplazo se elige para que la frase siga leyéndose bien en el uso REAL que
# hace la IA, que es el name-drop comparativo ("estilo X", "calidad tipo X"), no
# la mención suelta. Por eso son sustantivos o descriptores, no marcas nuestras.
#
# Las variantes con acento van explícitas (`pokemon` y `pokémon`): comparar sin
# acentos obligaría a reconstruir el texto original y no vale la complejidad.
MARCAS: dict[str, str] = {
    # Joyería y moda
    "pandora": "charms estilo europeo",
    "swarovski": "cristales facetados",
    "tiffany": "joyería estilo clásico",
    "cartier": "joyería de lujo",
    "rolex": "reloj clásico",
    "gucci": "estilo de diseñador",
    "prada": "estilo de diseñador",
    "chanel": "estilo de diseñador",
    "versace": "estilo de diseñador",
    "louis vuitton": "estilo de diseñador",
    "dior": "estilo de diseñador",
    "zara": "estilo urbano",
    "levis": "mezclilla clásica",
    "levi's": "mezclilla clásica",
    "crocs": "sandalias tipo zueco",
    "ugg": "botas afelpadas",
    "nike": "estilo deportivo",
    "adidas": "estilo deportivo",
    "reebok": "estilo deportivo",
    "under armour": "estilo deportivo",
    "new balance": "estilo deportivo",
    "converse": "tenis de lona",
    # FUERA a propósito, aunque sean marcas: `puma` es un animal (estampados),
    # `vans` es un tipo de vehículo y `honda` es un adjetivo español ("olla
    # honda"). Sustituirlas produciría frases absurdas en categorías que Kubera
    # sí vende — el riesgo de un falso positivo es mayor que el de la mención.
    # Personajes y juguetes (los que más inventa la IA en decoración e infantil)
    "disney": "diseño coleccionable",
    "marvel": "diseño coleccionable",
    "star wars": "diseño coleccionable",
    "hello kitty": "personaje kawaii",
    "pokemon": "personaje coleccionable",
    "pokémon": "personaje coleccionable",
    "barbie": "muñeca de moda",
    "lego": "bloques de construcción",
    "hot wheels": "autos de colección",
    "funko": "figura coleccionable",
    # Electrónica y audio
    "iphone": "smartphone",
    "ipad": "tableta",
    "airpods": "audífonos inalámbricos",
    "macbook": "laptop",
    "kindle": "lector electrónico",
    "gopro": "cámara de acción",
    "bose": "audio de alta fidelidad",
    "jbl": "bocina portátil",
    "beats": "audífonos de alto rendimiento",
    "sony": "electrónica de alto rendimiento",
    "samsung": "electrónica de alto rendimiento",
    "xiaomi": "electrónica de alto rendimiento",
    "huawei": "electrónica de alto rendimiento",
    "nintendo": "consola de videojuegos",
    "playstation": "consola de videojuegos",
    "xbox": "consola de videojuegos",
    "alexa": "asistente de voz",
    "roku": "reproductor de streaming",
    "dji": "dron",
    # Herramientas
    "dewalt": "herramienta profesional",
    "makita": "herramienta profesional",
    "bosch": "herramienta profesional",
    "milwaukee": "herramienta profesional",
    "black+decker": "herramienta profesional",
    "black & decker": "herramienta profesional",
    "stanley": "herramienta profesional",
    "truper": "herramienta profesional",
    "husqvarna": "herramienta profesional",
    "stihl": "herramienta profesional",
    # Hogar y cocina
    "tupperware": "contenedor hermético",
    "thermomix": "robot de cocina",
    "nutribullet": "licuadora personal",
    "instant pot": "olla multifunción",
    "nespresso": "cafetera de cápsulas",
    "keurig": "cafetera de cápsulas",
    "oster": "electrodoméstico de cocina",
    "kitchenaid": "batidora de pedestal",
    "ikea": "estilo nórdico",
    # Marcas que se volvieron nombre común: sustituirlas NO es opcional, es lo
    # que Amazon exige y además es el término correcto en español.
    "velcro": "cierre de gancho y bucle",
    "kleenex": "pañuelos desechables",
    "jacuzzi": "tina de hidromasaje",
    "post-it": "notas adhesivas",
    "thermos": "termo",
    "diurex": "cinta adhesiva",
    # Automotriz (casi siempre llegan como compatibilidad → ver la guarda)
    "ferrari": "automóvil deportivo",
    "lamborghini": "automóvil deportivo",
    "bmw": "automóvil",
    "mercedes": "automóvil",
    "audi": "automóvil",
    "toyota": "automóvil",
    "nissan": "automóvil",
    "ford": "automóvil",
    "chevrolet": "automóvil",
    "volkswagen": "automóvil",
}

# Nuestras. Nunca se tocan, ni aunque alguien las meta a MARCAS por error.
PROPIAS = {"ferrahome", "generic", "kubera"}

# La guarda de compatibilidad. Cerrada a propósito: son 6 formas, no un
# "detector de intención". Si la marca viene detrás de una de éstas, la mención
# es información del producto y NO se sustituye.
_COMPATIBILIDAD = re.compile(
    r"(compatible[s]?\s+(?:con|para)|apto[s]?\s+para|para\s+uso\s+con|"
    r"funciona\s+con|dise[ñn]ado\s+para|reemplazo\s+(?:de|para))\s*$",
    re.IGNORECASE,
)

# Se ordena por longitud descendente para que "louis vuitton" gane a "vuitton"
# y "black & decker" no se parta en "black".
_PATRON = re.compile(
    r"(?<![\w-])(" + "|".join(
        re.escape(m) for m in sorted(MARCAS, key=len, reverse=True)
    ) + r")(?![\w-])",
    re.IGNORECASE,
)


def revisar(texto: str, campo: str = "texto") -> tuple[str, list[str]]:
    """
    (texto_limpio, mensajes). No lanza nunca; texto vacío entra y sale igual.

    `campo` es solo para el mensaje ("…en la descripción — reemplazado por…").
    """
    if not texto:
        return texto, []
    mensajes: list[str] = []
    fuera: list[str] = []
    ultimo = 0
    for m in _PATRON.finditer(texto):
        marca = m.group(0)
        clave = marca.lower()
        if clave in PROPIAS:
            continue
        reemplazo = MARCAS.get(clave)
        if reemplazo is None:              # variante de acento no listada
            continue
        # ¿Viene detrás de "compatible con"? Entonces es un dato del producto.
        previo = texto[max(0, m.start() - 40):m.start()]
        if _COMPATIBILIDAD.search(previo):
            mensajes.append(
                f"Se detectó '{marca}' en {campo} como mención de compatibilidad "
                f"— NO se reemplazó (revísalo: Amazon la permite solo si el "
                f"producto realmente es compatible)")
            continue
        fuera.append(texto[ultimo:m.start()])
        fuera.append(reemplazo)
        ultimo = m.end()
        mensajes.append(
            f"Se detectó '{marca}' en {campo} — reemplazado por '{reemplazo}'")
    if not fuera:
        return texto, mensajes
    fuera.append(texto[ultimo:])
    return "".join(fuera), mensajes


# Nombre legible de cada campo, para que el mensaje se entienda sin abrir código.
_ETIQUETAS = {
    "titulo": "el título",
    "highlights": "los highlights",
    "descripcion": "la descripción",
    "backend_search_terms": "los términos de búsqueda",
}


def revisar_campos(campos: dict) -> tuple[dict, list[str]]:
    """
    Pasa el documento de contenido entero por el detector.

    Cubre los cuatro campos de texto, los bullets uno por uno (para poder decir
    en cuál) y el VALOR de cada atributo — que es donde la IA mete la marca
    cuando el prompt le prohíbe ponerla en el título.
    """
    salida = dict(campos or {})
    mensajes: list[str] = []

    for k, etiqueta in _ETIQUETAS.items():
        if isinstance(salida.get(k), str):
            salida[k], msg = revisar(salida[k], etiqueta)
            mensajes += msg

    bullets = salida.get("bullets")
    if isinstance(bullets, list):
        nuevos = []
        for i, b in enumerate(bullets, 1):
            if isinstance(b, str):
                b, msg = revisar(b, f"el bullet {i}")
                mensajes += msg
            nuevos.append(b)
        salida["bullets"] = nuevos

    atributos = salida.get("atributos")
    if isinstance(atributos, list):
        nuevos_a = []
        for a in atributos:
            if isinstance(a, dict) and isinstance(a.get("valor"), str):
                a = dict(a)
                a["valor"], msg = revisar(a["valor"], f"el atributo {a.get('nombre')}")
                mensajes += msg
            nuevos_a.append(a)
        salida["atributos"] = nuevos_a

    return salida, mensajes
