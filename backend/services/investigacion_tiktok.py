"""
investigacion_tiktok.py — Catálogo, candados y redactor de la INVESTIGACIÓN de TikTok.

POR QUÉ EXISTE (Brandon, 7-oct-2026)
────────────────────────────────────
Antes de automatizar el agendado de envíos de TikTok Shop hay que sondear EN
VIVO qué permite su API: qué bodegas ve, qué franjas ofrece, si deja dividir,
con qué peso calcula la guía. Eso sólo se puede preguntar desde producción: la
IP de Railway está en la lista permitida de TikTok (la laptop entra y sale) y
ahí vive la llave de la tienda. `routers/investigacion.py` expone el paso; todo
lo que decide QUÉ pasa vive aquí, en funciones puras y sin red, para que las
pruebas lo cubran sin tocar TikTok.

POR QUÉ NO SE COPIÓ EL CANDADO DE TEMU
──────────────────────────────────────
En Temu el NOMBRE del tipo dice si es lectura (`…get` / `…query`). En TikTok
no: `POST /fulfillment/202309/packages/search` lee y `POST /fulfillment/202309/
packages` compra una etiqueta; `POST …/cancellations/search` busca y `POST
…/cancellations` cancela la venta. Una regla sobre el texto de la ruta no
alcanza. Por eso aquí:

  1. NO HAY CAMPO DE RUTA. Quien investiga elige una CONSULTA CON NOMBRE de un
     catálogo cerrado (`CATALOGO`); el método, la ruta con su versión y los
     parámetros permitidos los pone el servidor. Los ids que van dentro de la
     ruta sólo pueden ser dígitos ASCII (`[0-9]{6,24}`, `fullmatch`): ni `..`,
     ni `?`, ni un host.
  2. LISTA NEGRA POR PATRÓN (`vetar`), independiente del catálogo: aunque
     alguien meta por error una ruta que agenda, envía, divide, combina,
     cancela, actualiza, crea, borra o sube —o cualquiera de productos,
     precios, inventario o promociones—, el módulo NO IMPORTA
     (`validar_catalogo` corre al cargar) y el backend no arranca con el
     catálogo roto. La misma regla se vuelve a pasar sobre la ruta ya armada.
  3. PARÁMETROS DECLARADOS. Cada consulta lista los suyos con su tipo; lo que
     no está declarado se rechaza. `shop_cipher`, `app_key`, `sign`,
     `timestamp` y los ids de comprador no se pueden mandar nunca.

LA RESPUESTA SALE REDACTADA POR LISTA BLANCA
────────────────────────────────────────────
Un valor sale sólo si su llave dice que es del negocio (ids de pedido, paquete
y SKU; estados; fechas y plazos; bodega; paquetería; guía; tipo de entrega;
pesos y medidas; importes; motivos de cancelación) y además TIENE LA FORMA de
ese dato (un importe tiene que ser un número, un plazo un entero). Todo lo
demás sale "[redactado]" con la llave a la vista. Encima, una lista negra tapa
el valor ENTERO —objeto incluido— de `recipient_address`, `buyer_*`, notas,
teléfonos, correos, CPF/RFC/CURP, cifrados y llaves. Ninguna liga sale nunca:
la `doc_url` de la etiqueta es una URL firmada que entrega la dirección del
comprador a quien la abra.

Una sola excepción, escrita: en la consulta de NUESTRAS bodegas se muestran
nombre, ciudad/municipio, estado y código postal (son datos nuestros y hacen
falta para distinguir TEXCO de TEXCO II). Calle, teléfono y contacto, no.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping
from urllib.parse import quote

# El mismo error y la misma ventana deslizante que la investigación de Temu:
# son puros y no saben nada de ningún marketplace.
from services.investigacion_temu import Limitador, Rechazo

__all__ = [
    "AVISO", "CATALOGO", "CODIGOS", "Campo", "CatalogoInvalido", "Consulta",
    "IDS_MAX", "Limitador", "PAGINA_MAX", "PROHIBIDAS", "Pedido", "REDACTADO",
    "Rechazo", "TOKEN_VENCIDO", "leer_abiertas", "leer_error", "llave_vetada",
    "para_pantalla", "preparar", "redactar", "regla", "sin_llaves", "sin_secretos",
    "validar_catalogo", "vetar",
]


class CatalogoInvalido(RuntimeError):
    """El catálogo trae algo que no es una lectura. Se levanta AL IMPORTAR."""


# ═════════════════════════════════════════════════════════════════════════════
# 1. LA LISTA NEGRA POR PATRÓN
# ═════════════════════════════════════════════════════════════════════════════
#
# No consulta el catálogo: juzga (método, ruta) por lo que dicen. Es la segunda
# capa — la primera es que la ruta no la escribe nadie — y existe para el día
# en que alguien agregue una consulta al catálogo sin fijarse en lo que hace.

METODOS_LECTURA: tuple[str, ...] = ("GET", "POST")

# `/dominio/AAAAMM/segmento/…`: minúsculas ASCII, dígitos y guion bajo; los
# huecos van como `{nombre}`. `fullmatch`, no `match` con `$` (que acepta un
# salto de línea final). Sin `//`, sin `.`, sin `?`, sin `%`, sin host.
_FORMA = re.compile(r"/[a-z_]+/20[0-9]{4}(?:/(?:[a-z0-9_]+|\{[a-z_]+\}))+")
_LARGO_MAX = 120

# Los únicos dominios de la Open API donde vive algo que esta página lee.
DOMINIOS_LECTURA: frozenset[str] = frozenset({
    "authorization", "event", "order", "fulfillment", "logistics",
    "return_refund", "finance",
})
# Dominios vetados ENTEROS, lecturas incluidas. Decisión, no descuido: en
# `/product/` un POST a `…/products` crea y un POST a `…/products/search` lee, y
# es el dominio donde producción SÍ escribe (stock y publicaciones). Esta
# página es para envíos.
PALABRAS_DOMINIO_VETADO: dict[str, str] = {
    "product": "productos, precios e inventario",
    "promotion": "promociones",
    "affiliate": "afiliados y creadores",
    "customer": "conversaciones con compradores (dato personal puro)",
    "supply": "cadena de suministro (sincroniza paquetes)",
    "seller": "ajustes del vendedor",
    "data": "conciliaciones",
    "analytics": "analítica",
}

# Un POST sólo entra si es una BÚSQUEDA: su último segmento es uno de éstos.
# Así caen los POST sin verbo, que son los peligrosos: `POST …/packages` (crea
# el paquete = compra la etiqueta), `POST …/cancellations` (cancela la venta),
# `POST …/orders/{id}/packages` (marca como enviado).
POST_FINALES_LECTURA: tuple[str, ...] = ("search", "query")

# Verbos de escritura. Vetan como SEGMENTO completo y como PALABRA dentro de un
# segmento compuesto (`partial_edit`, `batch_ship`).
VERBOS: frozenset[str] = frozenset({
    "ship", "split", "combine", "uncombine", "merge", "cancel", "update",
    "create", "delete", "remove", "edit", "modify", "change", "replace",
    "deliver", "upload", "activate", "deactivate", "recover", "restore",
    "approve", "reject", "accept", "decline", "sync", "calculate", "confirm",
    "submit", "add", "set", "put", "post", "patch", "apply", "refresh",
    "revoke", "authorize", "deauthorize", "mark", "bind", "unbind", "publish",
    "unpublish", "withdraw", "pay", "transfer", "generate", "print",
    "purchase", "buy", "send", "reply", "save", "import", "subscribe",
    "unsubscribe", "register", "assign", "schedule", "reschedule", "arrange",
    "book", "adjust", "close", "open", "start", "stop", "pause", "resume",
    "lock", "unlock", "restock", "replenish", "commit", "execute", "trigger",
    "retry", "resend", "notify", "dispatch", "pack", "unpack", "fulfill",
    "renew", "reset", "clear", "move", "copy", "insert", "upsert", "partial",
    "external", "batch", "bulk",
})
# La única excepción, con nombre y apellido: `split_attributes` PREGUNTA si un
# pedido se puede dividir; no divide. `split` solo, `…/orders/{id}/split` y
# cualquier otro compuesto con `split` siguen vetados.
SEGMENTOS_QUE_SOLO_PREGUNTAN: frozenset[str] = frozenset({"split_attributes"})

# Sustantivos que vetan aunque el dominio sea de lectura.
SUSTANTIVOS_VETADOS: dict[str, str] = {
    "product": "productos", "products": "productos",
    "price": "precios", "prices": "precios",
    "inventory": "inventario", "inventories": "inventario",
    "promotion": "promociones", "promotions": "promociones",
    "coupon": "promociones", "coupons": "promociones",
    "activity": "promociones", "activities": "promociones",
    "affiliate": "afiliados", "brand": "marcas", "brands": "marcas",
    "category": "categorías", "categories": "categorías",
    "compliance": "cumplimiento",
    "image": "subida de archivos", "images": "subida de archivos",
    "file": "subida de archivos", "files": "subida de archivos",
    "token": "llaves", "tokens": "llaves",
    "credential": "llaves", "credentials": "llaves",
    "conversation": "conversaciones", "conversations": "conversaciones",
    "message": "conversaciones", "messages": "conversaciones",
    "customer": "compradores",
    # Finanzas: sólo las transacciones DE UN PEDIDO. Estados de cuenta, pagos y
    # retiros traen datos bancarios.
    "statements": "estados de cuenta (datos bancarios)",
    "payments": "pagos (datos bancarios)",
    "withdrawals": "retiros (datos bancarios)",
    "bank": "datos bancarios", "invoice": "facturas", "invoices": "facturas",
}


def vetar(metodo: Any, ruta: Any) -> None:
    """
    `Rechazo` si (método, ruta) no es una LECTURA de las que caben aquí.

    Sirve igual para la plantilla del catálogo (`…/packages/{package_id}`) que
    para la ruta ya armada (`…/packages/1212558614775432729`). No normaliza
    nada: una ruta que necesita arreglarse para pasar, no pasa.
    """
    if not isinstance(metodo, str) or metodo not in METODOS_LECTURA:
        raise Rechazo("Sólo GET y POST de búsqueda: PUT, DELETE y PATCH escriben "
                      "(suscribir un aviso es PUT /event/…/webhooks).")
    if not isinstance(ruta, str) or not ruta or len(ruta) > _LARGO_MAX:
        raise Rechazo("La ruta tiene que ser texto de hasta "
                      f"{_LARGO_MAX} caracteres.")
    if not _FORMA.fullmatch(ruta):
        raise Rechazo("Forma de ruta inválida: /dominio/AAAAMM/segmento/… en "
                      "minúsculas, sin `..`, `?`, `#`, `%`, `//` ni host.")
    segmentos = ruta.split("/")[1:]
    dominio, resto = segmentos[0], segmentos[2:]
    for palabra in dominio.split("_"):
        if palabra in PALABRAS_DOMINIO_VETADO:
            raise Rechazo(f"El dominio `/{dominio}/` está vetado entero: "
                          f"{PALABRAS_DOMINIO_VETADO[palabra]}.")
    if dominio not in DOMINIOS_LECTURA:
        raise Rechazo(f"El dominio `/{dominio}/` no es de los que esta página lee "
                      f"({', '.join(sorted(DOMINIOS_LECTURA))}).")
    literales = [s for s in resto if not s.startswith("{")]
    for seg in literales:
        if seg in SEGMENTOS_QUE_SOLO_PREGUNTAN:
            continue
        for palabra in (seg, *seg.split("_")):
            if palabra in VERBOS:
                raise Rechazo(f"`{palabra}` es un verbo de escritura (en `{seg}`): "
                              "esa ruta agenda, envía, divide, combina, cancela, "
                              "actualiza, crea, borra o sube.")
            if palabra in SUSTANTIVOS_VETADOS:
                raise Rechazo(f"`{seg}` toca {SUSTANTIVOS_VETADOS[palabra]}: "
                              "fuera del alcance de esta página.")
    if metodo == "POST":
        if not literales or resto[-1] not in POST_FINALES_LECTURA:
            raise Rechazo("Un POST sólo entra si es una búsqueda: su último tramo "
                          f"tiene que ser {' o '.join(POST_FINALES_LECTURA)}. Un POST "
                          "a un recurso «a secas» lo CREA (paquete = etiqueta "
                          "comprada; cancelación = venta cancelada).")
        if dominio == "finance":
            raise Rechazo("En finanzas sólo se lee con GET.")


# Rutas que se sabe que ESCRIBEN (o que quedan fuera a propósito). No son el
# candado —el candado es `vetar`, que las rechaza todas por patrón, y hay una
# prueba que lo exige—: dan el mensaje humano en la pantalla y son un segundo
# cerrojo en `validar_catalogo`.
PROHIBIDAS: tuple[tuple[str, str, str], ...] = (
    ("POST", "/fulfillment/202309/packages/{package_id}/ship",
     "AGENDA la recolección o declara la guía del paquete"),
    ("POST", "/fulfillment/202309/packages/ship", "agenda varios paquetes de un golpe"),
    ("POST", "/fulfillment/202309/orders/{order_id}/split", "divide el pedido en paquetes"),
    ("POST", "/fulfillment/202309/packages/combine", "combina pedidos en un paquete"),
    ("POST", "/fulfillment/202309/packages/{package_id}/uncombine", "deshace un combinado"),
    ("POST", "/fulfillment/202309/orders/{order_id}/packages", "marca el pedido como enviado"),
    ("POST", "/fulfillment/202309/packages", "crea el paquete (compra la etiqueta)"),
    ("POST", "/fulfillment/202512/packages", "crea paquetes (compra etiquetas)"),
    ("POST", "/fulfillment/202309/packages/{package_id}/shipping_info/update",
     "cambia guía o paquetería del paquete"),
    ("POST", "/fulfillment/202309/orders/{order_id}/shipping_info/update",
     "cambia guía o paquetería del pedido"),
    ("POST", "/fulfillment/202309/packages/deliver", "declara paquetes como entregados"),
    ("POST", "/fulfillment/202309/images/upload", "sube una imagen de entrega"),
    ("POST", "/fulfillment/202309/files/upload", "sube un archivo de entrega"),
    ("POST", "/supply_chain/202309/packages/sync", "sincroniza paquetes de la cadena de suministro"),
    ("POST", "/return_refund/202309/cancellations", "CANCELA la venta"),
    ("POST", "/return_refund/202602/cancellations", "CANCELA la venta"),
    ("POST", "/return_refund/202309/cancellations/{cancel_id}/approve", "aprueba una cancelación"),
    ("POST", "/return_refund/202309/cancellations/{cancel_id}/reject", "rechaza una cancelación"),
    ("POST", "/return_refund/202309/returns", "crea una devolución"),
    ("POST", "/return_refund/202602/returns", "crea una devolución"),
    ("POST", "/return_refund/202309/returns/{return_id}/approve", "aprueba una devolución"),
    ("POST", "/return_refund/202309/returns/{return_id}/reject", "rechaza una devolución"),
    ("POST", "/return_refund/202309/refunds/calculate", "calcula (y prepara) un reembolso"),
    ("POST", "/order/202406/orders/external_orders", "registra pedidos externos"),
    ("PUT", "/event/202309/webhooks", "suscribe o cambia un aviso"),
    ("DELETE", "/event/202309/webhooks", "borra un aviso"),
    ("POST", "/product/202309/products", "crea un producto"),
    ("POST", "/product/202309/products/{product_id}", "edita un producto (lo que hoy manda el publicador)"),
    ("PUT", "/product/202309/products/{product_id}", "edita un producto"),
    ("DELETE", "/product/202309/products", "borra productos"),
    ("POST", "/product/202309/products/{product_id}/partial_edit", "edita parte de un producto"),
    ("POST", "/product/202309/products/{product_id}/inventory/update", "ESCRIBE stock"),
    ("POST", "/product/202309/products/{product_id}/prices/update", "ESCRIBE precios"),
    ("POST", "/product/202309/products/activate", "activa productos"),
    ("POST", "/product/202309/products/deactivate", "desactiva productos"),
    ("POST", "/product/202309/images/upload", "sube imágenes de producto"),
    ("POST", "/product/202309/files/upload", "sube archivos de producto"),
    ("POST", "/product/202309/brands", "crea una marca"),
    ("GET", "/product/202309/products/{product_id}", "lee un producto: dominio vetado entero"),
    ("POST", "/product/202309/products/search", "busca productos: dominio vetado entero"),
    ("POST", "/product/202309/inventory/search", "lee inventario: dominio vetado entero"),
    ("POST", "/product/202602/packages/recommend", "peso recomendado: dominio vetado entero"),
    ("POST", "/promotion/202309/activities", "crea una promoción"),
    ("POST", "/promotion/202309/activities/search", "busca promociones: dominio vetado entero"),
    ("GET", "/customer_service/202309/conversations", "conversaciones con compradores"),
    ("GET", "/finance/202309/statements", "estados de cuenta (datos bancarios)"),
    ("GET", "/finance/202309/payments", "pagos (datos bancarios)"),
    ("GET", "/finance/202309/withdrawals", "retiros (datos bancarios)"),
)
_PROHIBIDAS: frozenset[tuple[str, str]] = frozenset((m, r) for m, r, _ in PROHIBIDAS)


# ═════════════════════════════════════════════════════════════════════════════
# 2. EL CATÁLOGO
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Campo:
    """Un parámetro que la consulta acepta. Lo que no está aquí, no pasa."""
    nombre: str                       # la llave que manda la pantalla
    donde: str                        # "ruta" | "query" | "cuerpo"
    tipo: str                         # ver _VALIDADORES
    etiqueta: str                     # en español llano, para la pantalla
    requerido: bool = False
    opciones: tuple[str, ...] = ()
    minimo: int | None = None
    maximo: int | None = None
    destino: tuple[str, ...] = ()     # cuerpo anidado: ("weight", "value")
    ejemplo: Any = None               # con qué se llena el campo al elegir
    ayuda: str = ""


@dataclass(frozen=True)
class Consulta:
    """Una lectura con nombre. El método y la ruta NO vienen del usuario."""
    nombre: str
    grupo: str
    metodo: str
    plantilla: str                    # la ruta, con su versión y sus {huecos}
    titulo: str                       # en español llano
    pregunta: str                     # qué pregunta contesta
    campos: tuple[Campo, ...] = ()
    con_tienda: bool = True           # el servidor agrega `shop_cipher`
    estado: str = "documentada"       # "en producción" | "documentada" | "dudosa"
    nota: str = ""
    bodega_propia: bool = False       # la única excepción de dirección (ver §4)
    acuna_liga: bool = False          # cada llamada genera una liga firmada
    # NACE CERRADA. La cuarentena vive AQUÍ y no en una variable: una variable
    # que LISTA lo cerrado se abre sola con un error de dedo (basta escribirla
    # mal, o usarla para cerrar otra cosa). Sólo la abre su nombre exacto en
    # INVESTIGACION_TIKTOK_ABIERTAS (ver `leer_abiertas`).
    en_cuarentena: bool = False
    # Parámetros que van TODOS o NINGUNO (largo, ancho, alto y su unidad).
    juntos: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Pedido:
    """Una consulta ya validada y armada, lista para `tiktok.llamar`."""
    consulta: Consulta
    ruta: str
    query: dict[str, str]
    cuerpo: dict[str, Any] | None
    params: dict[str, Any]            # lo que se validó, para el eco y el log

    def para_log(self) -> dict[str, Any]:
        """Los parámetros para la auditoría. El cursor de página es opaco y
        largo: va sólo su tamaño."""
        return {k: (f"<{len(v)} caracteres>" if k == "page_token" else v)
                for k, v in self.params.items()}

    def ids_propios(self) -> frozenset[str]:
        """Los ids que mandó quien pregunta. Son lo ÚNICO numérico y largo que
        un mensaje de error de TikTok puede repetir sin que se tape."""
        propios: set[str] = set()
        for v in self.params.values():
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, str) and _ID.fullmatch(x):
                    propios.add(x)
        return frozenset(propios)


PAGINA_MAX = 20          # tope de `page_size` si quien llama no dice otro
IDS_MAX = 10             # ids por llamada, en cualquier lista de ids
_PARAMS_MAX = 24
_CUERPO_BYTES_MAX = 4_000

_NOMBRE_CONSULTA = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+")
_NOMBRE_CAMPO = re.compile(r"[a-z][a-z0-9_]{0,40}")
_HUECO = re.compile(r"\{([a-z_]+)\}")
# La ruta YA ARMADA: sin huecos. Los ids son dígitos ASCII, así que no puede
# traer más que minúsculas, dígitos y guion bajo.
_RUTA_ARMADA = re.compile(r"/[a-z_]+/20[0-9]{4}(?:/[a-z0-9_]+)+")

# Llaves que NUNCA se aceptan como parámetro (ni declaradas en el catálogo).
# `tiktok.llamar` escribe `app_key`, `timestamp` y `sign` después de copiar los
# parámetros, así que no se podrían pisar; se vetan igual, y con ellas el
# `shop_cipher` (lo pone el servidor), la versión y los ids de comprador
# (`orders/search` deja filtrar por `buyer_user_id`: aquí no).
RESERVADAS: frozenset[str] = frozenset({
    "appkey", "appsecret", "sign", "signature", "timestamp", "accesstoken",
    "xttsaccesstoken", "refreshtoken", "authcode", "token", "secret", "cipher",
    "shopcipher", "shopid", "version", "buyeruserid", "buyeruserids", "userid",
    "ruta", "metodo", "path", "url", "host", "method", "plantilla", "consulta",
})


def _norm(llave: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(llave).lower())


# ── Validadores de valor. Ninguno normaliza y ninguno repite el valor en el
#    mensaje: lo que se rechaza no se copia a un log ni a una respuesta. ──────

_ID = re.compile(r"[0-9]{6,24}")                 # `[0-9]`, no `\d` ni isdigit():
#                                                  "１２３".isdigit() es True
# El cursor de página es OPACO. Las muestras de la documentación son base64,
# pero nada obliga a TikTok México a entregarlo así: se aceptan además
# `. : ~ % , | * !` (un JWT lleva puntos; un cursor ya codificado, `%`). Nunca
# espacios, comillas, `&`, `?`, `#` ni `@`. Es un VALOR de la query: httpx lo
# codifica y TikTok lo decodifica antes de firmar, así que no puede tocar la
# ruta. La MISMA forma decide qué `next_page_token` se deja ver en la respuesta
# (`_EXENTAS`): lo que la pantalla muestra es justo lo que la entrada acepta.
_CURSOR = re.compile(r"[A-Za-z0-9+/=_\-.:~%,|*!]{1,600}")
_REGION = re.compile(r"[A-Z]{2}")
_DECIMAL = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,3})?")
_EPOCH_MIN, _EPOCH_MAX = 1_577_836_800, 4_102_444_800     # 2020 … 2100


def _v_id(c: Campo, v: Any, _pm: int) -> str:
    if not isinstance(v, str) or not _ID.fullmatch(v):
        raise Rechazo(f"`{c.nombre}` tiene que ser un id: texto de 6 a 24 dígitos.")
    return v


def _v_ids(c: Campo, v: Any, _pm: int) -> list[str]:
    if not isinstance(v, list) or not v or len(v) > IDS_MAX:
        raise Rechazo(f"`{c.nombre}` tiene que ser una lista de 1 a {IDS_MAX} ids.")
    for x in v:
        if not isinstance(x, str) or not _ID.fullmatch(x):
            raise Rechazo(f"`{c.nombre}`: cada id es texto de 6 a 24 dígitos.")
    if len(set(v)) != len(v):
        raise Rechazo(f"`{c.nombre}` trae ids repetidos.")
    return list(v)


def _v_entero(c: Campo, v: Any, pagina_max: int) -> int:
    lo = 1 if c.minimo is None else c.minimo
    hi = c.maximo if c.maximo is not None else 1_000_000
    if c.nombre == "page_size":
        hi = min(hi, pagina_max)
    if type(v) is not int or not lo <= v <= hi:        # `True` no es un entero
        raise Rechazo(f"`{c.nombre}` tiene que ser un entero entre {lo} y {hi}.")
    return v


def _v_epoch(c: Campo, v: Any, _pm: int) -> int:
    if type(v) is not int or not _EPOCH_MIN <= v <= _EPOCH_MAX:
        raise Rechazo(f"`{c.nombre}` es una fecha en segundos Unix (entero).")
    return v


def _v_opcion(c: Campo, v: Any, _pm: int) -> str:
    if not isinstance(v, str) or v not in c.opciones:
        raise Rechazo(f"`{c.nombre}` tiene que ser uno de: {', '.join(c.opciones)}.")
    return v


def _v_opciones(c: Campo, v: Any, _pm: int) -> list[str]:
    if (not isinstance(v, list) or not v or len(set(map(str, v))) != len(v)
            or any(not isinstance(x, str) or x not in c.opciones for x in v)):
        raise Rechazo(f"`{c.nombre}` es una lista sin repetir de: {', '.join(c.opciones)}.")
    return list(v)


def _v_cursor(c: Campo, v: Any, _pm: int) -> str:
    if not isinstance(v, str) or not _CURSOR.fullmatch(v):
        raise Rechazo(f"`{c.nombre}` es el `next_page_token` de la respuesta anterior, tal cual.")
    return v


def _v_region(c: Campo, v: Any, _pm: int) -> str:
    if not isinstance(v, str) or not _REGION.fullmatch(v):
        raise Rechazo(f"`{c.nombre}` es un país en dos letras mayúsculas (MX).")
    return v


def _v_decimal(c: Campo, v: Any, _pm: int) -> str:
    if not isinstance(v, str) or not _DECIMAL.fullmatch(v):
        raise Rechazo(f"`{c.nombre}` es un número como texto (ej. 1.2).")
    return v


def _v_booleano(c: Campo, v: Any, _pm: int) -> bool:
    if type(v) is not bool:
        raise Rechazo(f"`{c.nombre}` tiene que ser true o false.")
    return v


_VALIDADORES = {
    "id": _v_id, "ids": _v_ids, "entero": _v_entero, "epoch": _v_epoch,
    "opcion": _v_opcion, "opciones": _v_opciones, "cursor": _v_cursor,
    "region": _v_region, "decimal": _v_decimal, "booleano": _v_booleano,
}
# Qué tipos caben en la QUERY: `tiktok._firmar` concatena `f"{k}{v}"`, así que
# un booleano ("True") o una lista firmarían una cosa y viajarían otra. Las
# listas de ids van unidas por coma; lo demás, como texto.
_TIPOS_EN_QUERY = frozenset({"ids", "entero", "epoch", "opcion", "cursor", "region"})


def validar_catalogo(catalogo: Mapping[str, Consulta]) -> None:
    """
    `CatalogoInvalido` si alguna entrada no es una lectura bien declarada.

    Corre al importar el módulo: una ruta de escritura en el catálogo no llega
    a producción "apagada" — no llega, porque el backend no arranca.
    """
    vistas: set[tuple[str, str]] = set()
    for nombre, c in catalogo.items():
        donde = f"consulta «{nombre}»"
        if nombre != c.nombre or not _NOMBRE_CONSULTA.fullmatch(str(nombre)):
            raise CatalogoInvalido(f"{donde}: nombre inválido o distinto de su llave.")
        try:
            vetar(c.metodo, c.plantilla)
        except Rechazo as exc:
            raise CatalogoInvalido(f"{donde} ({c.metodo} {c.plantilla}) NO ES UNA "
                                   f"LECTURA PERMITIDA: {exc}") from None
        if (c.metodo, c.plantilla) in _PROHIBIDAS:
            raise CatalogoInvalido(f"{donde}: {c.metodo} {c.plantilla} está en la "
                                   "lista de rutas prohibidas.")
        if (c.metodo, c.plantilla) in vistas:
            raise CatalogoInvalido(f"{donde}: ruta repetida en el catálogo.")
        vistas.add((c.metodo, c.plantilla))
        if not (c.titulo.strip() and c.pregunta.strip()):
            raise CatalogoInvalido(f"{donde}: le falta título o la pregunta que contesta.")
        nombres = [f.nombre for f in c.campos]
        if len(set(nombres)) != len(nombres):
            raise CatalogoInvalido(f"{donde}: parámetros repetidos.")
        if c.metodo == "POST" and not any(f.donde == "cuerpo" for f in c.campos):
            raise CatalogoInvalido(f"{donde}: un POST de búsqueda declara al menos "
                                   "un filtro de cuerpo.")
        huecos = _HUECO.findall(c.plantilla)
        de_ruta = [f.nombre for f in c.campos if f.donde == "ruta"]
        if sorted(huecos) != sorted(de_ruta) or len(set(huecos)) != len(huecos):
            raise CatalogoInvalido(f"{donde}: los huecos de la ruta {huecos} no son "
                                   f"sus parámetros de ruta {de_ruta}.")
        for f in c.campos:
            cual = f"{donde}, parámetro `{f.nombre}`"
            if not _NOMBRE_CAMPO.fullmatch(f.nombre) or _norm(f.nombre) in RESERVADAS:
                raise CatalogoInvalido(f"{cual}: nombre inválido o reservado.")
            if f.tipo not in _VALIDADORES or not f.etiqueta.strip():
                raise CatalogoInvalido(f"{cual}: tipo desconocido o sin etiqueta.")
            if f.donde == "ruta":
                if f.tipo != "id" or not f.requerido:
                    raise CatalogoInvalido(f"{cual}: en la ruta sólo van ids obligatorios.")
            elif f.donde == "query":
                if f.tipo not in _TIPOS_EN_QUERY:
                    raise CatalogoInvalido(f"{cual}: ese tipo no puede ir en la query "
                                           "(rompería la firma).")
            elif f.donde == "cuerpo":
                if c.metodo != "POST":
                    raise CatalogoInvalido(f"{cual}: un GET no lleva cuerpo.")
            else:
                raise CatalogoInvalido(f"{cual}: `donde` tiene que ser ruta, query o cuerpo.")
            if f.tipo in ("opcion", "opciones") and not f.opciones:
                raise CatalogoInvalido(f"{cual}: una opción necesita su lista cerrada.")
            if f.destino and (f.donde != "cuerpo" or not all(
                    _NOMBRE_CAMPO.fullmatch(p) and _norm(p) not in RESERVADAS
                    for p in f.destino)):
                raise CatalogoInvalido(f"{cual}: `destino` inválido.")
        for grupo in c.juntos:
            if (len(grupo) < 2 or len(set(grupo)) != len(grupo)
                    or any(g not in nombres for g in grupo)):
                raise CatalogoInvalido(f"{donde}: `juntos` es un grupo de dos o más "
                                       "parámetros DECLARADOS en la consulta.")


# ── Campos que se repiten ────────────────────────────────────────────────────

def _id_ruta(nombre: str, etiqueta: str) -> Campo:
    return Campo(nombre, "ruta", "id", etiqueta, requerido=True)


def _pagina(maximo: int) -> Campo:
    return Campo("page_size", "query", "entero", "Resultados por página",
                 requerido=True, minimo=1, maximo=maximo, ejemplo=20,
                 ayuda="El servidor lo topa aunque TikTok acepte más.")


_CURSOR_PAGINA = Campo(
    "page_token", "query", "cursor", "Página siguiente",
    ayuda="Pega aquí el `next_page_token` de la respuesta anterior. Vacío = primera página.")
_ORDEN = Campo("sort_order", "query", "opcion", "Orden", opciones=("DESC", "ASC"),
               ejemplo="DESC")


def _fechas(prefijo: str, que: str) -> tuple[Campo, Campo]:
    return (Campo(f"{prefijo}_time_ge", "cuerpo", "epoch", f"{que} desde"),
            Campo(f"{prefijo}_time_lt", "cuerpo", "epoch", f"{que} antes de"))


_ESTADOS_PEDIDO = ("AWAITING_SHIPMENT", "AWAITING_COLLECTION", "PARTIALLY_SHIPPING",
                   "IN_TRANSIT", "DELIVERED", "COMPLETED", "CANCELLED", "ON_HOLD", "UNPAID")

_CONSULTAS: tuple[Consulta, ...] = (
    # ── Tienda ───────────────────────────────────────────────────────────────
    Consulta(
        "tienda.autorizadas", "Tienda", "GET", "/authorization/202309/shops",
        "Tienda autorizada",
        "¿Funcionan la firma, la llave y la IP? ¿KUBERA es vendedor local o global "
        "(seller_type)? De eso depende cómo deja dividir TikTok. Es la prueba de humo: "
        "empieza por aquí.",
        con_tienda=False, estado="en producción",
        nota="El cifrado de la tienda (cipher) sale tapado."),
    Consulta(
        "tienda.avisos", "Tienda", "GET", "/event/202309/webhooks",
        "Avisos a los que está suscrita la tienda",
        "¿De qué temas nos avisa TikTok hoy (pedidos, cancelaciones, devoluciones) y "
        "cuáles faltan (paquete dividido o combinado, cambio de dirección)?",
        estado="en producción",
        nota="Sólo lista: suscribir o borrar un aviso es PUT/DELETE y está vetado."),
    # ── Bodegas y entrega ────────────────────────────────────────────────────
    Consulta(
        "bodegas.lista", "Bodegas y entrega", "GET", "/logistics/202309/warehouses",
        "Bodegas dadas de alta en TikTok",
        "¿Cuántas bodegas de venta ve TikTok? ¿Existe TEXCO II o cree que todo sale de "
        "una sola? Nombre, ciudad, estado y código postal de cada una, y cuál es la "
        "predeterminada.",
        bodega_propia=True,
        nota="De la dirección sólo salen ciudad/municipio, estado y código postal; "
             "calle, teléfono y contacto van tapados."),
    Consulta(
        "bodegas.opciones_entrega", "Bodegas y entrega", "GET",
        "/logistics/202309/warehouses/{warehouse_id}/delivery_options",
        "Opciones de entrega de una bodega",
        "¿Qué opciones de entrega tiene la bodega y cuáles son sus límites REALES de "
        "peso y medidas? (hoy fijos en config: 30 kg, 100 cm por lado, 160 cm de suma)",
        campos=(
            _id_ruta("warehouse_id", "Id de la bodega (sale de «Bodegas dadas de alta»)"),
            Campo("scope", "query", "opcion", "Alcance", opciones=("WAREHOUSE", "PRODUCT"),
                  ejemplo="WAREHOUSE",
                  ayuda="WAREHOUSE = las opciones activas de la bodega, con sus límites."),
        )),
    Consulta(
        "entrega.paqueterias", "Bodegas y entrega", "GET",
        "/logistics/202309/delivery_options/{delivery_option_id}/shipping_providers",
        "Paqueterías de una opción de entrega",
        "¿Estafeta y J&T cuelgan de la misma opción de entrega? ¿Se puede elegir "
        "paquetería o la asigna TikTok?",
        campos=(
            _id_ruta("delivery_option_id",
                     "Id de la opción de entrega (sale de «Opciones de entrega»)"),
            Campo("warehouse_region", "query", "region", "País de la bodega", ejemplo="MX"),
            Campo("buyer_region", "query", "region", "País del comprador", ejemplo="MX"),
        )),
    # ── Pedidos ──────────────────────────────────────────────────────────────
    Consulta(
        "pedidos.buscar", "Pedidos", "POST", "/order/202309/orders/search",
        "Buscar pedidos por estado",
        "¿Cuál es la cola por agendar en este momento (AWAITING_SHIPMENT) y cada venta "
        "ya trae su paquete? También sirve para ver lo agendado sin recoger.",
        campos=(
            _pagina(100), _CURSOR_PAGINA,
            Campo("sort_field", "query", "opcion", "Ordenar por",
                  opciones=("update_time", "create_time"), ejemplo="update_time"),
            _ORDEN,
            Campo("order_status", "cuerpo", "opcion", "Estado del pedido",
                  opciones=_ESTADOS_PEDIDO, ejemplo="AWAITING_SHIPMENT",
                  ayuda="AWAITING_SHIPMENT = pagado y por agendar."),
            *_fechas("create", "Creados"), *_fechas("update", "Actualizados"),
            Campo("shipping_type", "cuerpo", "opcion", "Quién envía",
                  opciones=("TIKTOK", "SELLER")),
            Campo("is_buyer_request_cancel", "cuerpo", "booleano",
                  "Con solicitud de cancelación del comprador"),
            Campo("warehouse_ids", "cuerpo", "ids", "Ids de bodega",
                  ayuda="Sólo sirve si la tienda tiene varias bodegas activas."),
        ),
        estado="en producción",
        nota="No se puede filtrar por comprador: ese parámetro está vetado."),
    Consulta(
        "pedidos.detalle", "Pedidos", "GET", "/order/202309/orders",
        "Detalle de pedidos",
        "¿Qué fecha límite REAL trae la venta (shipping_due_time, collection_due_time)? "
        "¿Qué bodega le asignó TikTok, es muestra, qué paquete tiene? Con varios ids: "
        "cómo se ve una dividida, una combinada y una surtida desde TEXCO II.",
        campos=(Campo("ids", "query", "ids", "Ids de pedido", requerido=True),),
        estado="en producción",
        nota="Es la versión 202309, la que usa producción. La dirección y todo dato "
             "del comprador salen tapados."),
    Consulta(
        "pedidos.detalle_202507", "Pedidos", "GET", "/order/202507/orders",
        "Detalle de pedidos (versión 202507)",
        "¿La versión nueva del detalle trae algo que la 202309 no? La documentación "
        "dice que no (los mismos 211 campos): esto lo confirma con una venta real.",
        campos=(Campo("ids", "query", "ids", "Ids de pedido", requerido=True),)),
    Consulta(
        "pedidos.atributos_division", "Pedidos", "GET",
        "/fulfillment/202309/orders/split_attributes",
        "¿Se puede dividir el pedido?",
        "¿TikTok deja dividirlo (can_split)? ¿Obliga a dividirlo (must_split) y por "
        "qué? Es la base para dividir por bodega. Sólo pregunta: no divide nada.",
        campos=(Campo("order_ids", "query", "ids", "Ids de pedido", requerido=True),)),
    Consulta(
        "pedidos.rastreo", "Pedidos", "GET", "/fulfillment/202309/orders/{order_id}/tracking",
        "Rastreo de un pedido",
        "¿A qué hora real lo recogió la paquetería? Queda la hora y el código de cada "
        "evento; el texto lo escribe la paquetería y sale tapado.",
        campos=(_id_ruta("order_id", "Id del pedido"),),
        nota="El código de cada evento (action_code) NO tiene leyenda pública: se "
             "interpreta por el ORDEN de los eventos. La leyenda se arma a mano con "
             "dos o tres ventas cuya historia ya se conoce (recogida, en tránsito, "
             "entregada)."),
    Consulta(
        "pedidos.finanzas", "Pedidos", "GET",
        "/finance/202501/orders/{order_id}/statement_transactions",
        "Finanzas de un pedido entregado",
        "¿Cuánto cobró TikTok de comisión y cuánto costó de verdad el envío en esa "
        "venta? (hoy la comisión se guarda en 0 y el envío no se guarda)",
        campos=(_id_ruta("order_id", "Id del pedido (ya entregado)"),),
        nota="Sólo las transacciones de UN pedido. Estados de cuenta, pagos y retiros "
             "están vetados."),
    Consulta(
        "pedidos.servicios_envio", "Pedidos", "POST",
        "/fulfillment/202309/orders/{order_id}/shipping_services/query",
        "Cotizar servicios de envío de un pedido",
        "¿México deja cotizar el servicio de envío? La documentación dice que sólo "
        "EE. UU. y Japón: se espera un código de «no soportado», y ese código es el "
        "dato. Sólo cotiza: no compra ni agenda.",
        campos=(
            _id_ruta("order_id", "Id del pedido"),
            Campo("weight_value", "cuerpo", "decimal", "Peso", destino=("weight", "value"),
                  requerido=True, ejemplo="500"),
            Campo("weight_unit", "cuerpo", "opcion", "Unidad de peso",
                  opciones=("GRAM", "POUND"), destino=("weight", "unit"),
                  requerido=True, ejemplo="GRAM"),
            Campo("dimension_length", "cuerpo", "decimal", "Largo",
                  destino=("dimension", "length")),
            Campo("dimension_width", "cuerpo", "decimal", "Ancho",
                  destino=("dimension", "width")),
            Campo("dimension_height", "cuerpo", "decimal", "Alto",
                  destino=("dimension", "height")),
            Campo("dimension_unit", "cuerpo", "opcion", "Unidad de medida",
                  opciones=("CM", "INCH"), destino=("dimension", "unit")),
            Campo("order_line_item_ids", "cuerpo", "ids", "Ids de renglón del pedido"),
        ),
        # TikTok marca los cuatro como obligatorios DENTRO de `dimension`: con
        # medidas a medias contestaría «parámetros inválidos» en vez del «no
        # soportado en México» que se busca, y ese código se leería como hallazgo.
        juntos=(("dimension_length", "dimension_width", "dimension_height",
                 "dimension_unit"),),
        nota="Largo, ancho, alto y su unidad van los cuatro juntos o ninguno."),
    # ── Paquetes ─────────────────────────────────────────────────────────────
    Consulta(
        "paquetes.buscar", "Paquetes", "POST", "/fulfillment/202309/packages/search",
        "Buscar paquetes",
        "¿Cuántos paquetes están agendados sin recoger (PROCESSING) y desde cuándo? "
        "Ojo: no deja filtrar los pendientes de agendar; para eso es «Buscar pedidos "
        "por estado».",
        campos=(
            _pagina(50), _CURSOR_PAGINA,
            Campo("sort_field", "query", "opcion", "Ordenar por",
                  opciones=("update_time", "create_time", "order_pay_time"),
                  ejemplo="update_time"),
            _ORDEN,
            Campo("package_status", "cuerpo", "opcion", "Estado del paquete",
                  opciones=("PROCESSING", "FULFILLING", "COMPLETED", "CANCELLED"),
                  ejemplo="PROCESSING",
                  ayuda="PROCESSING = agendado, esperando a la paquetería."),
            *_fechas("create", "Creados"), *_fechas("update", "Actualizados"),
        )),
    Consulta(
        "paquetes.detalle", "Paquetes", "GET", "/fulfillment/202309/packages/{package_id}",
        "Detalle de un paquete",
        "Antes de agendar: ¿ya tiene paquetería? ¿Qué peso y caja declara TikTok? Ya "
        "agendado: ¿recolección o entrega en punto (handover_method)? ¿Qué franja y "
        "qué guía?",
        campos=(_id_ruta("package_id", "Id del paquete (sale del detalle del pedido)"),),
        nota="La dirección del destinatario y la del remitente salen tapadas. DE QUÉ "
             "BODEGA sale no se ve aquí (en México el paquete no trae warehouse_id y "
             "el remitente va tapado): está en «Detalle de pedidos» (warehouse_id) y "
             "se traduce con «Bodegas dadas de alta»."),
    Consulta(
        "paquetes.franjas", "Paquetes", "GET",
        "/fulfillment/202309/packages/{package_id}/handover_time_slots",
        "Franjas de entrega del paquete",
        "¿Hay recolección, entrega en punto o ambas (can_pickup, can_drop_off)? ¿Qué "
        "días y horas ofrece TikTok y con cuánta anticipación? ¿Sigue ofreciendo "
        "franjas ya agendado (reagendar)?",
        campos=(_id_ruta("package_id", "Id del paquete"),),
        nota="Sólo las lista: elegir una franja es agendar, y agendar está vetado."),
    Consulta(
        "paquetes.documento", "Paquetes", "GET",
        "/fulfillment/202309/packages/{package_id}/shipping_documents",
        "¿Ya existe la etiqueta del paquete?",
        "¿Se puede pedir la etiqueta antes de agendar? (se espera el código 21023035 o "
        "21042104). Ya agendado: ¿está lista y con qué guía?",
        campos=(
            _id_ruta("package_id", "Id del paquete"),
            Campo("document_type", "query", "opcion", "Documento", requerido=True,
                  opciones=("SHIPPING_LABEL", "PACKING_SLIP"), ejemplo="SHIPPING_LABEL"),
            Campo("document_size", "query", "opcion", "Tamaño", opciones=("A6", "A5"),
                  ejemplo="A6"),
            Campo("document_format", "query", "opcion", "Formato", opciones=("PDF",),
                  ejemplo="PDF"),
        ),
        estado="en producción", acuna_liga=True,
        nota="NO se descarga nada y la liga del PDF sale tapada: sólo contesta si "
             "existe y con qué guía. OJO, es la única consulta abierta que puede no "
             "ser inocua: cada llamada hace que TikTok GENERE una liga firmada de 24 h "
             "y puede contar como «etiqueta impresa» (un paquete con la etiqueta "
             "impresa ya no se puede descombinar; la documentación no dice si este "
             "GET lo activa). Producción ya la pide cada 20 min para lo agendado. "
             "Para salir de la duda: «Detalle de un paquete» antes y después, y "
             "comparar. Tiene su propio tope por minuto."),
    Consulta(
        "paquetes.combinables", "Paquetes", "GET",
        "/fulfillment/202309/combinable_packages/search",
        "Pedidos que TikTok propone enviar juntos",
        "¿Qué pedidos se podrían combinar en un solo paquete (mismo comprador y "
        "dirección, antes de agendar)? Sólo los lista: combinar está vetado.",
        campos=(_pagina(50), _CURSOR_PAGINA),
        estado="dudosa", en_cuarentena=True,
        nota="EN CUARENTENA: la documentación llama «pre-generados» a los ids de "
             "paquete que devuelve. No combina nada, pero hasta confirmar que tampoco "
             "deja un borrador vivo, se abre sólo escribiendo su nombre EXACTO en "
             "INVESTIGACION_TIKTOK_ABIERTAS."),
    # ── Cancelaciones ────────────────────────────────────────────────────────
    Consulta(
        "cancelaciones.buscar", "Cancelaciones", "POST",
        "/return_refund/202602/cancellations/search",
        "Buscar cancelaciones",
        "¿Quién cancela y por qué? ¿Hay solicitudes esperando NUESTRA respuesta, con "
        "fecha límite, que bloqueen agendar?",
        campos=(
            _pagina(50), _CURSOR_PAGINA,
            Campo("sort_field", "query", "opcion", "Ordenar por",
                  opciones=("update_time", "create_time"), ejemplo="update_time"),
            _ORDEN,
            Campo("order_ids", "cuerpo", "ids", "Ids de pedido"),
            Campo("cancel_ids", "cuerpo", "ids", "Ids de cancelación"),
            Campo("cancel_status", "cuerpo", "opciones", "Estado de la cancelación",
                  opciones=("CANCELLATION_REQUEST_PENDING", "CANCELLATION_REQUEST_SUCCESS",
                            "CANCELLATION_REQUEST_CANCEL", "CANCELLATION_REQUEST_COMPLETE"),
                  # Con qué nace marcada: sin un filtro la búsqueda no sale (y
                  # el primer clic dejaba un «RECHAZADA» falso en la auditoría).
                  ejemplo=("CANCELLATION_REQUEST_PENDING",),
                  ayuda="PENDING = esperando respuesta del vendedor."),
            Campo("cancel_types", "cuerpo", "opciones", "Quién canceló",
                  opciones=("CANCEL", "BUYER_CANCEL"),
                  ayuda="CANCEL = vendedor o sistema; BUYER_CANCEL = comprador."),
            *_fechas("create", "Creadas"), *_fechas("update", "Actualizadas"),
            Campo("locale", "cuerpo", "opcion", "Idioma de los textos",
                  opciones=("es-MX", "en-US")),
        ),
        nota="Sólo busca: aprobar, rechazar o crear una cancelación está vetado."),
)

CATALOGO: dict[str, Consulta] = {c.nombre: c for c in _CONSULTAS}
if len(CATALOGO) != len(_CONSULTAS):
    raise CatalogoInvalido("Hay nombres de consulta repetidos en el catálogo.")
validar_catalogo(CATALOGO)          # AL IMPORTAR: con el catálogo roto no se arranca


# ═════════════════════════════════════════════════════════════════════════════
# 3. DE LA PETICIÓN A LA LLAMADA
# ═════════════════════════════════════════════════════════════════════════════

def leer_abiertas(valor: Any, catalogo: Mapping[str, Consulta] | None = None,
                  ) -> tuple[frozenset[str], tuple[str, ...]]:
    """
    Qué consultas en cuarentena ABRE `INVESTIGACION_TIKTOK_ABIERTAS`
    → (las que abre, lo que se descartó).

    FALLA CERRADO. Los nombres van EXACTOS (minúsculas, con su punto) y
    separados por COMA. Si UNO solo no está en el catálogo —mal escrito, entre
    comillas, con mayúsculas, con `;` de separador—, NO SE ABRE NINGUNA: quien
    escribió la variable creía otra cosa, y lo que queda cerrado no hace daño.
    Un nombre del catálogo que no está en cuarentena no hace nada.

    De lo descartado sólo se devuelve lo que tiene forma de nombre de consulta
    (va a un log; una variable mal puesta puede traer cualquier cosa).
    """
    cat = CATALOGO if catalogo is None else catalogo
    pedidas = [x.strip() for x in str("" if valor is None else valor).split(",")
               if x.strip()]
    malas = [x for x in pedidas if x not in cat]
    if malas:
        return frozenset(), tuple(
            x if len(x) <= 60 and _NOMBRE_CONSULTA.fullmatch(x)
            else "(no tiene forma de nombre de consulta)" for x in malas[:10])
    return frozenset(x for x in pedidas if cat[x].en_cuarentena), ()


def preparar(nombre: Any, params: Any = None, *,
             catalogo: Mapping[str, Consulta] | None = None,
             pagina_max: int = PAGINA_MAX,
             abiertas: Iterable[str] = ()) -> Pedido:
    """
    La consulta validada y armada, o `Rechazo` (que es un 400 y NO sale a la red).

    Orden, todo falla cerrado: 1) el nombre está en el catálogo, exacto; 2) no
    está en cuarentena (la que el catálogo marca, salvo que su nombre venga en
    `abiertas`); 3) cada parámetro está DECLARADO en esa consulta y tiene la
    forma de su tipo; 4) no falta ninguno obligatorio ni queda un grupo a
    medias; 5) la ruta armada vuelve a pasar la lista negra.
    """
    cat = CATALOGO if catalogo is None else catalogo
    consulta = cat.get(nombre) if isinstance(nombre, str) else None
    if consulta is None:
        raise Rechazo("Esa consulta no está en el catálogo. Aquí no se escribe una "
                      "ruta: se elige una consulta de la lista.")
    if consulta.en_cuarentena and consulta.nombre not in frozenset(abiertas):
        raise Rechazo(f"«{consulta.nombre}» está en cuarentena: {consulta.nota}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise Rechazo("`params` tiene que ser un objeto JSON ({…}).")
    if len(params) > _PARAMS_MAX:
        raise Rechazo(f"`params` trae más de {_PARAMS_MAX} llaves.")

    declarados = {c.nombre: c for c in consulta.campos}
    valores: dict[str, Any] = {}
    for llave, valor in params.items():
        if not isinstance(llave, str) or not _NOMBRE_CAMPO.fullmatch(llave):
            raise Rechazo("Llave de parámetro inválida: van en minúsculas, con "
                          "dígitos y guion bajo.")
        if _norm(llave) in RESERVADAS:
            raise Rechazo(f"`{llave}` no se puede mandar: lo pone el servidor o "
                          "está vetado (llaves, firma, tienda, comprador, ruta).")
        campo = declarados.get(llave)
        if campo is None:
            permitidos = ", ".join(declarados) or "ninguno"
            raise Rechazo(f"`{llave}` no es un parámetro de «{consulta.nombre}». "
                          f"Permitidos: {permitidos}.")
        valores[llave] = _VALIDADORES[campo.tipo](campo, valor, pagina_max)
    faltan = [c.nombre for c in consulta.campos if c.requerido and c.nombre not in valores]
    if faltan:
        raise Rechazo(f"Falta: {', '.join(faltan)}.")
    for grupo in consulta.juntos:
        ausentes = [g for g in grupo if g not in valores]
        if ausentes and len(ausentes) < len(grupo):
            raise Rechazo(f"{', '.join(grupo)} van los {len(grupo)} juntos o ninguno. "
                          f"Falta: {', '.join(ausentes)}.")

    ruta = consulta.plantilla.format(
        **{c.nombre: valores[c.nombre] for c in consulta.campos if c.donde == "ruta"})
    if not _RUTA_ARMADA.fullmatch(ruta):
        raise Rechazo("La ruta armada no tiene la forma esperada.")
    vetar(consulta.metodo, consulta.plantilla)
    vetar(consulta.metodo, ruta)

    query: dict[str, str] = {}
    cuerpo: dict[str, Any] = {}
    for c in consulta.campos:
        if c.nombre not in valores or c.donde == "ruta":
            continue
        v = valores[c.nombre]
        if c.donde == "query":
            query[c.nombre] = ",".join(v) if isinstance(v, list) else str(v)
        else:
            *padres, hoja = c.destino or (c.nombre,)
            nodo = cuerpo
            for p in padres:
                nodo = nodo.setdefault(p, {})
            nodo[hoja] = v
    if consulta.metodo == "POST":
        # `tiktok.llamar` manda un cuerpo vacío como cadena vacía, no como `{}`;
        # y una búsqueda sin un solo filtro es justo la que no hay que hacer.
        if not cuerpo:
            raise Rechazo(f"«{consulta.nombre}» es una búsqueda: pide al menos un "
                          "filtro (estado, fechas o ids).")
        if len(json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")) > _CUERPO_BYTES_MAX:
            raise Rechazo(f"El cuerpo pasa de {_CUERPO_BYTES_MAX} bytes.")
    return Pedido(consulta=consulta, ruta=ruta, query=query,
                  cuerpo=cuerpo or None, params=valores)


# ═════════════════════════════════════════════════════════════════════════════
# 4. EL REDACTOR
# ═════════════════════════════════════════════════════════════════════════════
#
# Las llaves de TikTok son snake_case y están documentadas, así que la lista
# blanca va por la ÚLTIMA palabra de la llave (`…_id`, `…_time`, `…_status`),
# por alguna palabra de importe o medida, o por la llave exacta. Dos capas:
#
#   A. LISTA NEGRA — se tapa el valor ENTERO (objeto o lista incluidos), y gana
#      siempre a la blanca:
#        · DATO: contacto, domicilio, identidad, texto escrito por una persona,
#          llaves y cifrados, y cualquier liga.
#        · ROL de persona (recipient, buyer, customer, user…). Única salvedad:
#          un IMPORTE numérico (`buyer_service_fee`, `customer_paid_shipping_
#          fee_amount`) — es dinero, no una persona, y el segundo es justo el
#          dato que se viene a buscar en finanzas. La salvedad sólo abre si la
#          llave ACABA en palabra de importe: `buyer_vat_number` o
#          `customer_tax_rate_id` llevan `vat`/`tax` en medio y son un
#          identificador fiscal, no dinero.
#   B. LISTA BLANCA — un texto o un número sale sólo si su llave es del negocio
#      Y el valor tiene la forma de ese dato. Lo demás, "[redactado]".
#
# RESIDUO ACEPTADO (lo que la forma no puede distinguir en un campo que TikTok
# AGREGUE mañana; con los 383 campos documentados hoy no pasa): un `…_id`
# numérico que no sea de persona (`account_id`, `member_id`) sale aunque
# guarde un teléfono; un `…_type` / `…_status` deja ver UNA palabra sin
# espacios; y una llave con forma de identificador se muestra aunque sea el
# dato (`{"zenaida": {…}}`). `llaves_redactadas` y la respuesta dejan verlo.
#
# `true`, `false` y `null` pasan siempre: no dicen quién es nadie
# (`has_updated_recipient_address: false` es un hecho del pedido).

REDACTADO = "[redactado]"

# A1. DATO: por PALABRA de la llave.
_DATO_PALABRAS: frozenset[str] = frozenset({
    # contacto y domicilio
    "address", "addresses", "phone", "phones", "mobile", "tel", "telephone",
    "email", "emails", "mail", "whatsapp", "street", "postal", "zip", "zipcode",
    "postcode",
    "geolocation", "latitude", "longitude", "lat", "lng", "lon", "geo",
    "coordinate", "coordinates",
    # identidad
    "cpf", "cnpj", "rfc", "curp", "passport", "identity", "registry", "national",
    "nickname", "avatar", "birth", "birthday", "gender", "consultation",
    # texto que escribe una persona
    "note", "notes", "message", "messages", "remark", "remarks", "comment",
    "comments", "memo", "instruction", "instructions", "preference", "preferences",
    # llaves
    "cipher", "token", "tokens", "secret", "sign", "password", "passwd",
    "credential", "credentials", "cookie", "auth",
    # medios de pago
    "iban", "clabe", "cvv",
    # ligas: ninguna sale (la `doc_url` de la etiqueta es una URL firmada)
    "url", "urls", "uri", "link", "links",
})
# A1'. DATO: por trozo de la llave compacta (sin separadores).
_DATO_COMPACTO: tuple[str, ...] = (
    "firstname", "lastname", "fullname", "realname", "username", "surname",
    "localscript", "taxid", "taxnumber", "taxcode", "taxno", "taxpayer",
    "vatid", "vatnumber", "vatcode", "vatno",
    "idcard", "idnumber", "nationalid", "openid", "unionid", "appkey", "apikey",
    "accesskey", "secretkey", "paymentmethod", "cardtype", "cardnumber", "cardno",
    "cardholder", "bankaccount", "bankcard", "accountnumber", "accountno",
)
# A2. ROL de persona.
_ROL_PALABRAS: frozenset[str] = frozenset({
    "recipient", "recipients", "buyer", "buyers", "customer", "customers",
    "consumer", "user", "users", "receiver", "consignee", "contact", "contacts",
    "person", "sender", "holder", "payer", "creator", "creators",
})
# Llaves exactas que la lista negra NO tapa, con la forma que deben tener: el
# cursor de paginación (contiene `token` y sin él no se puede pedir la página
# siguiente) y quién canceló (contiene `user` y es un rol: BUYER/SELLER/SYSTEM).
_EXENTAS: dict[str, re.Pattern[str]] = {
    "next_page_token": _CURSOR,            # la misma forma que acepta la entrada
    "cancel_user": re.compile(r"[A-Z_]{1,40}"),
}

# B. LISTA BLANCA.
_FIN_ID = frozenset({"id", "ids"})
_FIN_TIEMPO = frozenset({"time", "millis", "deadline", "date"})
_FIN_ENUM = frozenset({"status", "type", "types", "tag", "tags", "method", "level",
                       "program", "platform", "initiator", "role"})
_PAL_IMPORTE = frozenset({
    "amount", "fee", "fees", "tax", "total", "subtotal", "discount", "price",
    "cost", "subsidy", "commission", "reimbursement", "withheld", "deposit",
    "incentive", "vat", "duty", "rate",
})
# Con qué palabra tiene que ACABAR una llave con rol de persona para que su
# número salga como dinero. Los 8 campos documentados que lo necesitan acaban
# en `amount`, `fee` o `tax`. Nunca en `id`, `number`, `code` ni `no`.
_FIN_IMPORTE = frozenset({
    "amount", "fee", "fees", "tax", "total", "subtotal", "discount", "price",
    "cost", "subsidy", "commission", "reimbursement",
})
_PAL_MEDIDA = frozenset({"count", "quantity", "qty", "days", "weight", "length",
                         "width", "height"})
# Texto del negocio, por llave EXACTA.
_TEXTO_CORTO = frozenset({"currency", "unit", "region"})
_TEXTO_GUIA = frozenset({"tracking_number", "last_mile_tracking_number"})
_TEXTO_SKU = frozenset({"seller_sku"})
_TEXTO_NEGOCIO = frozenset({
    "shipping_provider", "shipping_provider_name", "delivery_option_name",
    "sku_name", "product_name",
    "cancel_reason", "cancel_reason_text", "ship_exception_reason",
})
_NUMERO_EXACTO = frozenset({"action_code"})
# Llaves genéricas que sólo salen según DE QUIÉN cuelgan (la llave del objeto o
# de la lista que las contiene). `name` suelto es el del destinatario en
# `recipient_address` y el de la bodega en `warehouses`.
_SEGUN_PADRE: dict[str, frozenset[str]] = {
    "name": frozenset({"warehouses", "delivery_options", "shipping_providers",
                       "shipping_services", "shops", "skus"}),
    "code": frozenset({"shops"}),
    # NUNCA en el rastreo: ahí lo escribe la paquetería ("Recibió: <nombre>").
    "description": frozenset({"delivery_options"}),
    "reason": frozenset({"split_attributes"}),
    "action": frozenset({"seller_next_action_response"}),
    "value": frozenset({"weight"}),
}
# LA EXCEPCIÓN de la bodega propia: qué sale de `warehouses[].address`.
_BODEGA_DIRECCION = frozenset({"city", "state", "postal_code", "region",
                               "region_code", "district", "distict", "town"})

_F_ID = re.compile(r"[0-9]{1,40}")            # los ids de TikTok son numéricos
# Sin espacios: ningún enum de TikTok los lleva, y con espacio pasaba un nombre
# y apellido bajo cualquier `…_type` o `…_status` que TikTok agregue.
_F_ENUM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{0,63}")
_F_NUMERO = re.compile(r"-?[0-9]{1,15}(?:\.[0-9]{1,6})?")
_F_ENTERO = re.compile(r"[0-9]{1,16}")
_F_CORTO = re.compile(r"[A-Za-z0-9_\- ]{1,24}")
_F_GUIA = re.compile(r"[A-Za-z0-9\-]{4,40}")
_F_CODIGO = re.compile(r"[A-Za-z0-9_\-]{1,40}")
_F_SKU = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/ ]{0,63}")
_LLAVE_OK = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

_PALABRA = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
# El `(?<!…)` no cambia qué coincide (la coincidencia más a la izquierda
# siempre empieza donde empieza la corrida): hace que se intente UNA vez por
# corrida y no una por carácter. Sin él, un texto largo sin espacios ni `@`
# cuesta el cuadrado de su largo.
_CORREO = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# También un `dominio.tld/ruta` SIN esquema: una liga firmada escrita así sigue
# siendo una liga (`x.tiktokcdn.com/l.pdf?x-signature=…`). El `(?<!…)` hace que
# cada corrida con puntos se intente una sola vez (ver `_CORREO`).
_LIGA = re.compile(r"(?i)(?:https?:|ftp:|www\.|//|(?<![a-z0-9.\-])(?:[a-z0-9\-]+\.)+[a-z]{2,24}/)")
# Para el cursor de página: en base64 `//` es un par de caracteres como
# cualquier otro, así que ahí sólo delata una liga su esquema.
_LIGA_ESQUEMA = re.compile(r"(?i)(?:https?:|ftp:|www\.)")
_TELEFONO = re.compile(r"(?<![0-9A-Za-z])\+?(?:[0-9][ ().\-]{0,2}){9,14}[0-9](?![0-9A-Za-z])")
_TEXTO_MAX = 300
# Ningún valor de la lista blanca mide más que esto (el más largo es el cursor
# de página): lo que lo pasa no sale, y así ninguna expresión corre sobre un
# texto sin tope.
_VALOR_MAX = 600
_HONDO_MAX = 40
_LLAVES_MAX = 80


@lru_cache(maxsize=4096)
def _palabras(llave: str) -> tuple[str, ...]:
    """`customer_paid_shipping_fee_amount` → (customer, paid, shipping, fee, amount).
    Parte snake_case y, por si acaso, camelCase; los dígitos se van."""
    return tuple(x.lower() for x in _PALABRA.findall(llave) if not x.isdigit())


@lru_cache(maxsize=4096)
def _es_dato(llave: str) -> bool:
    p = _palabras(llave)
    if any(x in _DATO_PALABRAS for x in p):
        return True
    compacta = "".join(p)
    return any(t in compacta for t in _DATO_COMPACTO)


@lru_cache(maxsize=4096)
def _es_rol(llave: str) -> bool:
    return any(x in _ROL_PALABRAS for x in _palabras(llave))


@lru_cache(maxsize=4096)
def _acaba_en_importe(llave: str) -> bool:
    p = _palabras(llave)
    return bool(p) and p[-1] in _FIN_IMPORTE


def _es_numero(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    return isinstance(v, str) and bool(_F_NUMERO.fullmatch(v))


def llave_vetada(llave: str, valor: Any = "") -> bool:
    """¿La lista negra tapa ENTERO lo que cuelga de esta llave?"""
    if llave in _EXENTAS:
        return False
    if _es_dato(llave):
        return True
    if _es_rol(llave):
        return not (_acaba_en_importe(llave) and _es_numero(valor))
    return False


def _texto_libre(v: str) -> str | None:
    """Un texto del negocio, o None si no puede salir. Los correos y lo que
    parezca teléfono se tapan dentro del texto."""
    if len(v) > _TEXTO_MAX:
        return None
    return _TELEFONO.sub(REDACTADO, _CORREO.sub(REDACTADO, v))


def _escalar(llave: str, padre: str, v: Any) -> tuple[bool, Any]:
    """(sale, valor) para un texto o un número bajo `llave`, dentro de `padre`."""
    if isinstance(v, str) and len(v) > _VALOR_MAX:
        return False, None
    exenta = _EXENTAS.get(llave)
    if exenta is not None:
        sale = (isinstance(v, str) and bool(exenta.fullmatch(v))
                and not _LIGA_ESQUEMA.search(v))
        return (True, v) if sale else (False, None)
    p = _palabras(llave)
    if not p:                                    # la raíz no tiene llave
        return False, None
    es_texto = isinstance(v, str)
    # NINGUNA liga sale, la llave que sea: una URL bajo `sku_name` o bajo
    # `tracking_number` sigue siendo una URL (y la de la etiqueta es firmada).
    if es_texto and _LIGA.search(v):
        return False, None
    ultima = p[-1]
    if ultima in _FIN_ID:
        ok = (es_texto and bool(_F_ID.fullmatch(v))) or (type(v) is int and v >= 0)
        return ok, v
    if ultima in _FIN_TIEMPO:
        ok = (es_texto and bool(_F_ENTERO.fullmatch(v))) or (type(v) is int and v >= 0)
        return ok, v
    if ultima in _FIN_ENUM:
        ok = (es_texto and bool(_F_ENUM.fullmatch(v))) or type(v) is int
        return ok, v
    if llave in _NUMERO_EXACTO:
        return _es_numero(v), v
    if llave in _TEXTO_CORTO:
        return es_texto and bool(_F_CORTO.fullmatch(v)), v
    if llave in _TEXTO_GUIA:
        return es_texto and bool(_F_GUIA.fullmatch(v)), v
    if llave in _TEXTO_SKU:
        return es_texto and bool(_F_SKU.fullmatch(v)), v
    if llave in _TEXTO_NEGOCIO:
        limpio = _texto_libre(v) if es_texto else None
        return limpio is not None, limpio
    padres = _SEGUN_PADRE.get(llave)
    if padres is not None:
        if padre not in padres:
            return False, None
        if llave == "value":
            return _es_numero(v), v
        if llave == "code":
            return es_texto and bool(_F_CODIGO.fullmatch(v)), v
        limpio = _texto_libre(v) if es_texto else None
        return limpio is not None, limpio
    if any(x in _PAL_IMPORTE or x in _PAL_MEDIDA for x in p):
        return _es_numero(v), v
    return False, None


def _json_anidado(s: str) -> Any:
    """El objeto o la lista si `s` es JSON serializado; si no, None."""
    t = s.strip()
    if t[:1] not in ("{", "[") or len(t) < 2:
        return None
    try:
        anidado = json.loads(t)
    except (ValueError, RecursionError):     # roto, o tan hondo que no se abre
        # (un texto así de hondo mide miles de caracteres: `_escalar` lo tapa
        #  por largo, y ya no se cae la respuesta entera por un solo campo)
        return None
    return anidado if isinstance(anidado, (dict, list)) else None


class _Cuenta:
    """Cuántos campos se taparon y bajo qué llaves (los nombres, no los valores)."""

    def __init__(self, bodega_propia: bool) -> None:
        self.bodega_propia = bodega_propia
        self.n = 0
        self.llaves: set[str] = set()

    def tapar(self, llave: str) -> str:
        self.n += 1
        if len(self.llaves) < _LLAVES_MAX:
            self.llaves.add(llave if _LLAVE_OK.fullmatch(llave) else "(llave no estándar)")
        return REDACTADO


def _direccion_bodega(d: dict[Any, Any], cuenta: _Cuenta) -> dict[str, Any]:
    """`warehouses[].address` de NUESTRA bodega: ciudad/municipio, estado, CP y
    país. Calle, contacto, teléfono y coordenadas siguen tapados."""
    salida: dict[str, Any] = {}
    for k, w in d.items():
        if not isinstance(k, str) or not _LLAVE_OK.fullmatch(k):
            salida[f"(llave no estándar {len(salida)})"] = cuenta.tapar("")
        elif w is None or isinstance(w, bool):
            salida[k] = w
        elif (k in _BODEGA_DIRECCION and isinstance(w, (str, int))
              and len(str(w)) <= 80 and not _LIGA.search(str(w))):
            salida[k] = _CORREO.sub(REDACTADO, w) if isinstance(w, str) else w
        else:
            salida[k] = cuenta.tapar(k)
    return salida


def _redactar(v: Any, llave: str, padre: str, cuenta: _Cuenta, hondo: int) -> Any:
    """
    `llave`: bajo qué llave vive `v` (los elementos de una lista heredan la de
    la lista). `padre`: la llave del objeto que contiene a `v`
    (`warehouses[0].name` → llave `name`, padre `warehouses`).
    """
    if hondo > _HONDO_MAX:
        return cuenta.tapar(llave)
    if isinstance(v, dict):
        salida: dict[str, Any] = {}
        for k, w in v.items():
            if not isinstance(k, str) or not _LLAVE_OK.fullmatch(k):
                # Una llave que no parece de esquema puede SER el dato.
                salida[f"(llave no estándar {len(salida)})"] = cuenta.tapar("")
            elif w is None or isinstance(w, bool):
                salida[k] = w
            elif (cuenta.bodega_propia and k == "address" and llave == "warehouses"
                  and isinstance(w, dict)):
                salida[k] = _direccion_bodega(w, cuenta)
            elif llave_vetada(k, w):
                salida[k] = cuenta.tapar(k)
            else:
                salida[k] = _redactar(w, k, llave, cuenta, hondo + 1)
        return salida
    if isinstance(v, list):
        return [_redactar(w, llave, padre, cuenta, hondo + 1) for w in v]
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str):
        if not v:
            return v
        # JSON serializado dentro de un texto: se abre y decide cada llave suya.
        anidado = _json_anidado(v)
        if anidado is not None:
            return json.dumps(_redactar(anidado, llave, padre, cuenta, hondo + 1),
                              ensure_ascii=False)
    if not isinstance(v, (str, int, float)):
        return cuenta.tapar(llave)
    sale, valor = _escalar(llave, padre, v)
    return valor if sale else cuenta.tapar(llave)


def redactar(v: Any, consulta: str = "") -> tuple[Any, int, list[str]]:
    """
    (copia redactada, cuántos campos se taparon, bajo qué llaves). No modifica
    el original. `consulta` es el nombre del catálogo: sólo la que tiene
    `bodega_propia` abre la excepción de dirección.
    """
    entrada = CATALOGO.get(consulta)
    cuenta = _Cuenta(bool(entrada and entrada.bodega_propia))
    limpio = _redactar(v, "", "", cuenta, 0)
    return limpio, cuenta.n, sorted(cuenta.llaves)


# ═════════════════════════════════════════════════════════════════════════════
# 5. LAS LLAVES NO SALEN: ni en un error, ni en un log, ni por descuido
# ═════════════════════════════════════════════════════════════════════════════

TOKEN_VENCIDO = "token vencido: lo renueva producción; reintenta en unos minutos"
NO_JSON = "TikTok no contestó JSON (HTTP {http}): el cuerpo no se muestra"
SIN_LEER = "TikTok contestó algo que no se pudo leer: no se muestra"

_PAR_SECRETO = re.compile(
    r"(?i)(x-tts-access-token|access[_-]?token|refresh[_-]?token|shop[_-]?cipher|"
    r"app[_-]?key|app[_-]?secret|auth[_-]?code|authorization|cipher|sign|token|secret)"
    # `=` y `:`, y también percent-codificados (`sign%3D…`): así repite una
    # pasarela la query que recibió.
    r"(\"?\s*(?:[=:]|%3[DA])\s*)[\"']?(?:bearer\s+)?[^&\s\"',;)}\]]+")
_URL_ENTERA = re.compile(r"(?i)\b(?:https?(?:://|%3A%2F%2F)|www\.)\S+")
# Toda corrida hexadecimal de 32 o más (un HMAC-SHA256 son 64). SIN condiciones
# alrededor: en `sign%3D<firma>` la `D` de `%3D` es hexadecimal, y exigir que no
# hubiera un hexadecimal pegado dejaba salir la firma entera.
_FIRMA_HEX = re.compile(r"(?i)[0-9a-f]{32,}")
# Un número LARGO: 7 dígitos o más, seguidos o separados por espacio, punto,
# guion o paréntesis (así se escribe un teléfono, con lada o sin ella). Sólo
# para el mensaje de error: en un texto del negocio «modelo 2024-2025» es un
# modelo, y ahí decide `_TELEFONO`.
_NUMERO_LARGO = re.compile(r"(?<![0-9A-Za-z])\+?(?:[0-9][ ().\-]{0,2}){6,}[0-9](?![0-9A-Za-z])")
_PCT = re.compile(r"%[0-9A-F]{2}")
_ERROR_MAX = 300
# Cuánto texto se les da a las expresiones de arriba. El `message` de TikTok no
# tiene tope, y limpiar corre DENTRO de la corrutina (regla 11): con decenas de
# KB sin espacios el backend entero se quedaba esperando. Con este tope el
# costo tiene techo, y sigue siendo «limpiar antes de recortar» a 300.
_ENTRADA_MAX = 4_000
_JSON_INTENTOS = 12
_ERROR_NO_JSON = re.compile(r"TikTok devolvió algo que no es JSON \(HTTP ([0-9]{3})\)")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_\-]{1,48}")
_CODIGO_TIKTOK = re.compile(r"[0-9]{1,12}")


def _formas(secretos: Iterable[Any]) -> list[str]:
    """
    Cada llave como puede aparecer ESCRITA: tal cual y percent-codificada (así
    viaja en la URL, y así la repite una pasarela en su página de error), con
    los `%XX` en mayúsculas y en minúsculas. Las más largas primero, para que
    una no deje a medias a otra.
    """
    formas: set[str] = set()
    for s in secretos:
        s = "" if s is None else str(s)
        if len(s) < 4:
            continue
        for cod in (quote(s, safe=""), quote(s)):
            formas.update((cod, _PCT.sub(lambda m: m.group(0).lower(), cod)))
        formas.add(s)
    return sorted(formas, key=len, reverse=True)


def _acotado(texto: Any, secretos: Iterable[Any]) -> str:
    """
    El texto sin los valores literales de las llaves y con TOPE de largo.

    Los literales se quitan sobre el texto ENTERO (es un reemplazo lineal): si
    se recortara primero, media llave ya no coincidiría con nada. Después se
    recorta, y del recorte se tira el último tramo sin espacios — lo único que
    pudo quedar partido a la mitad.
    """
    t = str(texto)
    for s in _formas(secretos):
        t = t.replace(s, "[llave]")
    if len(t) > _ENTRADA_MAX:
        t = t[:_ENTRADA_MAX]
        t = t[:max(t.rfind(" "), t.rfind("\n"), t.rfind("\t")) + 1]
    return t


def _sin_formas(t: str) -> str:
    """Fuera lo que tiene FORMA de llave: ligas, pares `sign=…`, hexadecimal, correos."""
    t = _URL_ENTERA.sub("[liga]", t)
    t = _PAR_SECRETO.sub(lambda m: f"{m.group(1)}{m.group(2)}[llave]", t)
    t = _FIRMA_HEX.sub("[firma]", t)
    return _CORREO.sub(REDACTADO, t)


def sin_llaves(texto: Any, secretos: Iterable[Any] = ()) -> str:
    """
    Un texto de error sin llaves: fuera los valores literales de `secretos`
    (token, cifrado de la tienda, app key, app secret; tal cual y
    percent-codificados), cualquier `sign=…` / `access_token=…`, toda liga, toda
    firma en hexadecimal y los correos.

    Se limpia ANTES de recortar: recortar primero partiría una llave a la mitad
    y la mitad que queda ya no coincide con nada.
    """
    t = _acotado(texto, secretos)
    return _sin_formas(t)[:_ERROR_MAX]


def _firma_o_numero(m: re.Match[str]) -> str:
    return m.group(0) if m.group(0).isdigit() else "[firma]"


def sin_secretos(v: Any, secretos: Iterable[Any] = ()) -> Any:
    """Última red sobre la respuesta YA redactada: si un valor literal de las
    llaves (tal cual o percent-codificado) apareciera en cualquier texto o
    llave, se sustituye; y con él, todo lo que tenga forma de firma."""
    vivos = _formas(secretos)

    def limpiar(x: Any) -> Any:
        if isinstance(x, str):
            for s in vivos:
                if s in x:
                    x = x.replace(s, "[llave]")
            # La FIRMA de la petición no se conoce aquí (la calcula
            # `tiktok.llamar`), pero su forma sí: 32 o más hexadecimales con
            # alguna letra. Repetida bajo `status` o `tracking_number` pasaría
            # la lista blanca. Un id decimal largo no se toca.
            return _FIRMA_HEX.sub(_firma_o_numero, x) if len(x) >= 32 else x
        if isinstance(x, dict):
            return {limpiar(k): limpiar(w) for k, w in x.items()}
        if isinstance(x, list):
            return [limpiar(w) for w in x]
        return x

    return limpiar(v)


def _abrir_json(t: str) -> str:
    """
    Si el mensaje trae un objeto o una lista JSON incrustados, se sustituyen
    por su versión REDACTADA (la misma lista blanca de las respuestas). Un JSON
    roto, o más corchetes de los que vale la pena mirar, se corta ahí mismo.
    """
    decodificador = json.JSONDecoder()
    trozos: list[str] = []
    i = intentos = 0
    while True:
        j = min((k for k in (t.find("{", i), t.find("[", i)) if k >= 0), default=-1)
        if j < 0:
            return "".join(trozos) + t[i:]
        if intentos >= _JSON_INTENTOS:
            return "".join(trozos) + t[i:j] + "[…]"
        intentos += 1
        try:
            obj, fin = decodificador.raw_decode(t, j)
        except (ValueError, RecursionError):
            if t[j] == "{" or t[j + 1:j + 2] in ("{", "[", '"'):
                return "".join(trozos) + t[i:j] + "[…]"     # JSON roto: no se adivina
            trozos.append(t[i:j + 1])                       # «[ids]»: un corchete de prosa
            i = j + 1
            continue
        if isinstance(obj, (dict, list)) and obj:
            trozos.append(t[i:j] + json.dumps(redactar(obj)[0], ensure_ascii=False))
        else:
            trozos.append(t[i:fin])
        i = fin


def _sin_datos(t: str, propios: frozenset[str]) -> str:
    """Correos y todo número de 7 o más dígitos (un teléfono, con formato o sin
    él) — salvo los ids que mandó quien pregunta, que son lo único largo y
    numérico que ya conoce."""
    def tapar(trozo: str) -> str:
        return _NUMERO_LARGO.sub("[número]", _CORREO.sub(REDACTADO, trozo))

    ids = sorted((p for p in propios if isinstance(p, str) and _ID.fullmatch(p)),
                 key=len, reverse=True)
    if not ids:
        return tapar(t)
    partes = re.split("(?<![0-9])(" + "|".join(ids) + ")(?![0-9])", t)
    return "".join(p if i % 2 else tapar(p) for i, p in enumerate(partes))


# ── El mensaje de error sale por LISTA BLANCA DE PALABRAS ───────────────────
#
# El `message` de TikTok es prosa: no trae llaves que digan qué es cada cosa,
# así que la lista blanca del redactor no le alcanza. Y tapar por FORMA
# (correos, teléfonos, números largos) es lista negra: un nombre o una calle no
# tienen forma que los delate. Por eso la última capa es por VOCABULARIO: una
# palabra sale sólo si está en la lista — el inglés de los mensajes de error
# que documenta la referencia de TikTok (469 distintos; los 29 de las consultas
# de este catálogo salen intactos), el inglés técnico de cualquier API, y las
# palabras del propio catálogo (rutas, parámetros y sus opciones). Lo demás
# sale «[…]». «Zenaida», «Insurgentes», un apellido o un RFC no están en
# ninguna lista.
#
# RESIDUO, dicho como es: una palabra SUELTA que sea a la vez nombre y
# vocabulario («Will», «May», «Real») pasaría, sin su apellido ni su calle; y
# un número de menos de 7 dígitos pasa (un número exterior sin su calle, un
# código postal). Si un mensaje legítimo sale con huecos, la palabra que falta
# se agrega aquí: es redacción de más, que es el lado seguro.
_VOCABULARIO_BASE: frozenset[str] = frozenset("""
a abnormal about above absent accept accepted accepting accepts access accessed according
account across action actions activate activated activation active actual add added adding
addition additional address addresses admin advance affected after again against ago ahead
alert algorithm algorithms all allow allowable allowed allowing allowlist allowlisted along
alphabetical alphabetically alphanumeric already also although always among amount amounts
an and another any anymore anything api app appeal appear appears append appended applicable
application applications applied applies apply approval approve approved are area argument
arguments around arrange arranged array arrival arrive arrived as ascii ask asked aspect
assign assigned assigning assistance associated at attempt attempted attempting attempts
attr attribute attributes auction auth authenticate authenticated authentication
authorization authorize authorized authorizing auto automatic automatically availability
available avalible average avoid awaiting aware away awb back backend backorder bad balance
base base64 based basic batch be became because become becomes been before begin beginning
behalf behavior being belong belongs below best better between beyond bidding binding blank
blind block blocked boarder body bool boolean border both bound boundary box br brand brands
break broken buffer bug build built bulk bundle bundles business busy buyer by bytes cache
calculate calculated calculation call called caller calling calls can cancel cancelation
cancellation cancellations cancelled cancelling cancels cannot capability capacity card
carrier carriers case cases categories category categoryid cause caused causes center
centimeter centimeters certain certificate chain change changed channel char character
characters charge chargeable charged chart check checked checking checks chinese choice
choose chosen cipher circumference clear cleared client clock close closed cm cod code codes
coefficient collect collected collecting collection column combinable combination combine
combined combining combo come comes comma command comment commission commit common
communication company compare compatible complete completed completely completion compliance
compliant component concatenate concatenated concurrent condition conditions config
configured confirm confirmation confirmed confirming conflict conflicting connect connected
connection consecutive consider considered consist consistent console constraint constraints
consumed contact contain contained container contains content context continue continued
contract control conversion convert converted cookie copy correct correctly corresponding
cost costs could couldn count counted counter country courier coverage create created
creating creation creator credential credentials criteria critical cross crossborder
currency current currently cursor custom customer customers customize cycle daily damaged
dashboard data database date dates datetime day days de deactivated deadline deal debug
decimal decimals decision declare declared decode decoded decrease dedicated deduct deducted
deemed default defined definition delay delayed delete deleted deletion deliver delivered
deliveries delivering delivery demand denied depend dependent depending depends deposit
deprecated depth desc describe described description design designated desired destination
detail details detected determine determined dev developer developers development device did
didn differ difference different differs digest digit digital digits dimension dimensional
dimensions direct direction directly disable disabled disallowed disconnect discount
discounts dispatch dispatched display displayed distinct distribution division do doc docs
document documentation documents does doesn domain don done double down download downstream
draft drop dropoff dropped due duplicate duplicated duplicates duration during dynamic e
each earlier earliest early edit edited editing effect effective either element elements
eligibility eligible else elsewhere email emergency emojis empty enable enabled enabling
encode encoded encoding encounter encountered encrypt encrypted encryption end ended ending
endpoint ends energy enforce enforced engine english enough ensure enter entered entirely
enum environment epoch equal equals equivalent error errors escape especially essential
estimate estimated etc eu evaluate even event events ever every everything exact exactly
example examples exceed exceeded exceeding exceeds exception exceptions exchange exclude
excluded excluding exclusive execute executed execution exempt exist existed existence
existing exists exit expect expected expects experience expiration expire expired expires
expiry explicit explicitly express expression extend extended extension external extra
extract factor fail failed failing fails failure failures fall fallback falls false far fast
fatal fault fbt feature fee feedback fees fetch fetched fetching few fewer field fields file
filled filter filtering filters final finalize finalized find finish finished first fit fix
fixed flag flow follow following follows for forbidden force forced form format formats
formatted formatting forward found four fr fraction frame free freeze frequency frequent
frequently from frozen fulfil fulfill fulfilled fulfilling fulfillment fulfilment full
function functionality further future futures g gallery gateway gb general generate
generated generating generation get gift given gives global go gone good got gram grams
grant granted granting greater group grouped groups guarantee guide half halt hand handle
handled handler handling handover happen happened happens hard has hash have haven having
head header headers heavy height held help hence here hex hidden high higher highest hint
history hit hmac hold holding home host hour hours however html http https hub huge human
hundred id idempotent identical identification identified identifier identifiers identify
identity idle ids if ignore ignored illegal image images img immediately impact imperial
implement implemented implicit important impossible improper in inaccessible inactivated
inactive inbound inch inches include included includes including incoming incompatible
incomplete inconsistent incorrect increase increased increment independent index indicate
indicated indicates indicator individual infinite info inform information informed initial
initialize initialized initiate initiated inner input insert inside inspect install instance
instant instead instruction instructions insufficient integer integers integration integrity
intended interaction interception interface intermediate internal international internet
interrupted interval into introduced invalid invalidated inventory invoice invoke invoked
involved ip is isn isolated issue issued issues it item items iteration its japan job join
joined jp json just keep kept key keys kg kilogram kilograms kind kindly know known label
labels lack lacking lacks landing language large larger largest last lasts late latency
later latest latter launch launched layer lb lbs lead learn least leave leaves left legacy
legal length lengths less let letter letters level library life lifetime light like likely
limit limitation limitations limited limits line lines link linked links list listed listing
listings lists literal little live load loaded loading local locale locally located location
lock locked log logged logic login logistic logistics logout logsitics long longer longest
look lookup loop lose loss lost lot lottery low lower lowercase lowest machine made mail
main maintain maintenance major make makes making malformed manage managed management
manager mandatory manifest manner manual manually manufacturer manufacturers many map mapped
mapping marked market marketplace markets mass master match matched matching material max
maximum may maybe md5 mean meaning means meant measure measurement mechanism media medium
meet member memory mention mentioned merchant merchants merge merged message messages met
meta metadata meter meters method metric mexico middle might migrate migrated migration
millis millisecond milliseconds min minimum minor minute minutes mismatch mismatched miss
missed missing mistake mixed mm mode model modes modified modify moment money monitor month
monthly months more moreover most move moved much multi multipart multiple must mutually mx
my name names namespace native near nearly necessary need needed needs negative neither nest
network never new newer newest next nil nine no node non nonce none nor normal normally not
note nothing notice notification notified notify now null nullable num number numbers
numeric oauth object objects observe obsolete obtain obtained occur occurred occurrence
occurs of off offer offered official offline often ok okay old older oldest omit omitted on
onboarding once one ones ongoing online only onto opaque open openapi opened operate
operating operation operational operations operator option optional options or order ordered
ordering orders organization origin original originally other others otherwise ought ounce
ounces our out outbound outdated outer output outside over overall overdue overflow overlap
overlapping overridden override oversize oversized overweight own owned owner ownership
package packages packaging packed packing page pagenumber pagesize pagination paid pair
pairs panel paper parallel param parameter parameters params parcel parent parse parsed
parser parsing part partial partially participate particular partner parts party pass passed
passing password past patch path pattern pause paused pay payload payment payments pdf peak
penalty pending per percent percentage perform performed performing perhaps period periods
permanent permanently permission permissions permitted persists person ph phase phone phones
physical pick pickup piece pixels pkg place placed placement plain plan planned platform
please pleaser plus point pointer points policy poll pool poor pop port portal portion
position positive possible possibly post postal posting postpone potential pound pounds
power practice pre precision precondition predefined prefer preferred prefix preorder
prepaid prepare prepared preparing presale presence present preserved prevent prevented
previous previously price prices primary principal print printed printing prior priority
private privilege privileges probably problem problems procedure proceed process processed
processing produce produced product production products profile program progress prohibited
project promise promotion proof proper properly properties property proportion protect
protected protection protocol proven provide provided provider providing proxy public
publish published pull purchase purchased purpose purposes push put qps qualification
quality quantities quantity queried queries query queue queued quick quota quote quoted
raise raised random range rate rates rather ratio raw re reach reached reaches reaching read
readable reading ready real realtime reason reasons reauthorize recalculate receipt receive
received receiver receives receiving recent recently recipient recipients recognize
recognized recommend recommended record recorded records recover recovery redirect reduce
reduced refer reference referenced references refresh refreshed refund refunded refunds
refuse refused regarding regardless region regions register registered registration regular
reject rejected rejection related relation relationship relative release released relevant
reliable remain remaining remains remote removal remove removed rename renew renewal renewed
repeat repeated replace replaced replacement replicas replicate replicated reply report
reported represent represents reproduce request requested requester requesting requests
require required requirement requirements requires requiring reschedule rescheduled reserved
reset resolution resolve resolved resource resources respect respective respond responded
response responses responsibility responsible rest restart restore restrict restricted
restriction restrictions resubmit result results resume retried retries retrieval retrieve
retrieved retrieving retry retrying return returned returning returns reverse reversed
revert review reviewed revision revoke revoked right risk role roles rollback root round
route routing row rows rule rules run running runtime s safe safety said sale sales same
sample samples sandbox satisfied satisfy save saved say says scale scan scanned scenario
scene scenes schedule scheduled scheduling schema scope scopes script sea search second
seconds secret section secure security see seem seems seen segment select selected selecting
selection self sell seller sellers send sender sending sent separate separated separately
separator sequence serial series serve server service services session set sets setting
settings settle settled settlement setup seven several sg sha sha256 shall shape share
shared shares sheet ship shipment shipments shipped shipper shipping shop shopping shops
short shorten shortly should show shown shows shut side sign signature signatures signed
signing similar simple simply simultaneous simultaneously since single site six size
sizechart sized skew skip skipped sku skus sla slip slot slots slow small smaller smallest
snapshot so soft sold solely some somehow someone something sometimes soon sorry sort sorted
sorting source sources space spec special specific specifically specification specified
specify split splits splitting stable stage staging stale stamp standalone standard start
started starting starts state stated statement statements states static statistics status
statuses stay step steps still stock stop stopped storage store stored stores strategy
stream strict strictly string strings strong structure structured style sub subject
submission submit submitted subscribe subscribed subscription subsequent subset substitute
succeed succeeded success successful successfully such sufficient suffix suggest suggested
suitable suite sum summary supplementary supplied supplier supply support supported
supporting supports suppose sure suspend suspended suspension switch switched symbol symbols
sync synchronization synchronize synchronized synchronous syntax system t table tablets tag
take taken takes taking target targeted task tasks tax team technical template temporarily
temporary ten tenant term terminal terminate terminated terms test tested testing text th
than that the them there therefore these they this though thousand thread three threshold
throttle throttled throttling through throw thrown thus ticket tier tiktok till time timed
timeframe timeout times timeslot timeslots timestamp timestamps timezone tip title to today
together token tokens tomorrow too took tool top topic topics total touch toward towards
trace track tracked tracking trade traffic transaction transactions transfer transferred
transform transit transition translate translation transport treat treated tried trigger
triggered true truncated trust trusted try trying tts turn turned twice two type typed types
typical typically typo uk unable unaccepted unallowed unassigned unauthenticated
unauthorized unavailability unavailable unchanged uncombine undefined under underlying
understand undo unexpected unexpectedly unfinished unfortunately unhandled unified uniform
uninitialized unique uniquely unit united units unix unknown unless unlike unlimited unlink
unlock unlocked unpaid unprocessable unpublished unread unrecognized unregistered unrelated
unresolved unsafe unset unshipped unspecified unsuccessful unsupported until untrusted
unused unusual up upcoming update updated updates updating upgrade upload uploaded upon
upper uppercase upstream urgent uri url us usable usage use used useful user username users
using usual usually utc utf utf8 uuid v2 valid validate validated validation validity value
values variable variables varialbes variant variants variation various vary ve vendor
verification verified verify verifying version versions very via video view viewed violate
violated violation virtual visibility visible visit vn void volume volumetric wait waiting
waived wallet want warehouse warehouses warn warning was watch way waybill ways we web
webhook webhooks week weekly weeks weigh weight weights well went were what whatever when
whenever where whereas whether which while whitelist whitelisted who whole whom whose why
wide width will window windows wish with withdraw withdrawal withdrawn within without won
word words work worked worker workflow working works world worth would wrap wrapped write
writing written wrong x year years yes yesterday yet you your yourself zero zone
""".split())

OCULTA = "[…]"
# Lo que ponen las capas anteriores (y el redactor) se deja pasar tal cual.
_MARCAS: tuple[str, ...] = ("[llave]", "[liga]", "[firma]", REDACTADO, "[número]", OCULTA)
_ALFANUM = re.compile(r"[^\W_]+")
_TROZO = re.compile(
    "(?P<marca>" + "|".join(re.escape(m) for m in _MARCAS)
    + r"|\(llave no estándar [0-9]{1,3}\))|(?P<palabra>[^\W_]+)|(?P<otro>[\W_])")
# Puntuación y espacios. Un símbolo que no esté aquí sale como espacio: hay
# «letras» que no cuentan como letra (las encerradas en círculo) y un nombre
# escrito con ellas no se parte en palabras.
_SIGNOS: frozenset[str] = frozenset(" \t\n!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~…—–«»“”‘’·→¿¡°")
_CON_UNIDAD = re.compile(r"[0-9]{1,4}(?:kg|g|lbs?|oz|cm|mm|m|in|h|s|ms|d|st|nd|rd|th|x)")
_OCULTAS_SEGUIDAS = re.compile(r"\[…\](?:[ ,;]{1,2}\[…\])+")


def _vocabulario() -> frozenset[str]:
    """La base más las palabras del catálogo: lo que TikTok puede citar de la
    propia petición (la ruta, los parámetros y sus opciones)."""
    propias: set[str] = set()
    for c in CATALOGO.values():
        textos = [c.plantilla]
        for f in c.campos:
            textos += [f.nombre, *f.destino, *f.opciones]
        for texto in textos:
            propias.update(p.lower() for p in _ALFANUM.findall(texto))
    return _VOCABULARIO_BASE | propias


VOCABULARIO: frozenset[str] = _vocabulario()


def _palabra_permitida(trozo: str, propios: frozenset[str]) -> bool:
    """¿Esta corrida de letras y dígitos puede salir en un mensaje de error?"""
    if not trozo.isascii():              # «Peña»; y los dígitos que no son ASCII
        return False
    if trozo.isdigit():                  # corto, o un id que mandó quien pregunta
        return len(trozo) < 7 or trozo in propios
    baja = trozo.lower()
    if baja in VOCABULARIO or _CON_UNIDAD.fullmatch(baja):
        return True
    if trozo.isalpha():                  # camelCase: `pageSize` → page, size
        partes = _PALABRA.findall(trozo)
        return len(partes) > 1 and all(p.lower() in VOCABULARIO for p in partes)
    return False


def _solo_vocabulario(t: str, propios: frozenset[str] = frozenset()) -> str:
    """
    El texto, palabra por palabra: sale la que está en `VOCABULARIO` (o es un
    número corto, un id de `propios` o una marca de las capas anteriores); la
    que no, sale «[…]». Varias tapadas seguidas se juntan en una.
    """
    salida: list[str] = []
    for m in _TROZO.finditer(t):
        trozo = m.group()
        if m.lastgroup == "marca":
            salida.append(trozo)
        elif m.lastgroup == "otro":
            salida.append(trozo if trozo in _SIGNOS else " ")
        elif _palabra_permitida(trozo, propios):
            salida.append(trozo)
        else:
            salida.append(OCULTA)
    return _OCULTAS_SEGUIDAS.sub(OCULTA, "".join(salida))


def _rid_limpio(rid: str, secretos: Iterable[Any]) -> bool:
    """El `request_id` se pega al mensaje FUERA de las capas de limpieza (se separa
    antes para que la lista blanca de palabras no lo borre). Por eso lleva su
    propio candado: forma estricta y NINGÚN trozo de 8 caracteres de una llave
    (ni la llave entera si es más corta). Si TikTok devolviera ahí el token, el
    cifrado o el secreto, el request_id simplemente no sale."""
    if not _REQUEST_ID.fullmatch(rid or ""):
        return False
    for s in secretos:
        s = str(s or "")
        if not s:
            continue
        if len(s) < 8:
            if s in rid:
                return False
            continue
        if any(s[i:i + 8] in rid for i in range(len(s) - 7)):
            return False
    return True


def leer_error(texto: Any, secretos: Iterable[Any] = (),
               propios: Iterable[str] = ()) -> tuple[str, str]:
    """
    (código, mensaje) del `RuntimeError` de `tiktok.llamar`
    ("TikTok <ruta> → code=N mensaje (request_id=…)").

    Token vencido: se dice eso y NADA más — ni el mensaje de TikTok, ni un
    intento de renovar (eso lo hace producción, no una página de lectura).

    Si TikTok no contestó JSON, del cuerpo no sale NADA: `tiktok.llamar` lo
    recorta a 200 caracteres, y media llave percent-codificada ya no coincide
    con ninguna regla. Si el texto no tiene la forma de `llamar`, tampoco sale.

    El `message` de TikTok es texto libre, y es el único sitio donde TikTok
    podría citar un dato del comprador. Pasa por cuatro capas, en este orden:
    sin llaves (y con tope de largo); un JSON incrustado, por el redactor;
    correos y todo número de 7 o más dígitos que no sea un id de `propios`,
    tapados; y al final LISTA BLANCA DE PALABRAS (`_solo_vocabulario`): lo que
    no es inglés de un mensaje de API sale «[…]». Un nombre, una calle o un
    RFC escritos en prosa no tienen forma que los delate, pero tampoco están
    en el vocabulario. Los 29 mensajes distintos que la referencia documenta
    para estas consultas salen intactos (hay una prueba que lo exige).
    """
    t = str(texto)
    # Tupla: se recorre dos veces (mensaje y request_id); un generador se agotaría.
    secretos = tuple(secretos)
    m = _ERROR_NO_JSON.match(t)
    if m:
        return f"http_{m.group(1)}", NO_JSON.format(http=m.group(1))
    cabeza, flecha, resto = t.partition(" → code=")
    if not flecha or not cabeza.startswith("TikTok /"):
        return "sin_codigo", SIN_LEER
    ruta = cabeza[len("TikTok "):]
    codigo, _, resto = resto.partition(" ")
    if not _CODIGO_TIKTOK.fullmatch(codigo):
        codigo = "sin_codigo"
    if codigo == "105002":
        return codigo, TOKEN_VENCIDO
    mensaje, cola, rid = resto.rpartition(" (request_id=")
    if cola and rid.endswith(")"):
        rid = rid[:-1]
    else:
        mensaje, rid = resto, ""
    suyos = frozenset(propios)
    mensaje = _sin_datos(_sin_formas(_abrir_json(_acotado(mensaje, secretos))), suyos)
    mensaje = _solo_vocabulario(mensaje, suyos)[:_ERROR_MAX].strip()
    partes = ["TikTok"]
    if len(ruta) <= _LARGO_MAX and _RUTA_ARMADA.fullmatch(ruta):
        partes.append(ruta)
    partes.append("→")
    if codigo != "sin_codigo":
        partes.append(f"code={codigo}")
    partes.append(mensaje)
    if _rid_limpio(rid, secretos):
        partes.append(f"(request_id={rid})")
    return codigo, " ".join(p for p in partes if p)

# ═════════════════════════════════════════════════════════════════════════════
# 6. QUÉ SIGNIFICA CADA CÓDIGO, Y LO QUE VE LA PANTALLA
# ═════════════════════════════════════════════════════════════════════════════

# En una investigación el código ES el hallazgo: "sin permiso" dice qué permiso
# le falta a la app; "sin agendar" confirma que la etiqueta no existe todavía.
CODIGOS: dict[str, str] = {
    "0": "contestó bien",
    "105002": TOKEN_VENCIDO,
    "105005": "EXISTE, pero a la app le falta ese permiso (el mensaje dice cuál)",
    "36009002": "saturación: TikTok pide bajar el ritmo",
    "http_429": "saturación: TikTok pide bajar el ritmo",
    "36009033": "la IP no está en la lista permitida de TikTok",
    "36009009": "NO EXISTE esa ruta",
    "36009010": "la ruta existe, pero no con ese método",
    "36009004": "credencial, firma o reloj inválidos (el mensaje dice cuál)",
    "106001": "firma inválida",
    "106013": "falta el cifrado de la tienda",
    "36009003": "error interno de TikTok: reintentar",
    "36009007": "TikTok tardó demasiado: reintentar",
    "21023035": "el paquete todavía NO está agendado: la etiqueta aún no existe",
    "21042104": "el paquete todavía NO está agendado: la etiqueta aún no existe",
    "11034037": "la etiqueta se está generando: reintentar en un momento",
    "21042102": "el paquete ya fue recogido: la etiqueta ya no se entrega",
    "21011040": "el paquete ya fue enviado",
    "21001028": "en proceso: reintentar",
    "11021009": "sin servicio de envío disponible",
    "21004017": "fuera del horario de recolección",
    "21008042": "la etiqueta de ese paquete ya consta como IMPRESA en TikTok",
    "timeout": "TikTok no contestó a tiempo",
    "sin_codigo": "TikTok contestó algo que no se pudo leer",
}

AVISO = (
    "Sólo lectura. Aquí no se escribe ninguna ruta: se elige una consulta de una "
    "lista cerrada que vive en el servidor. Las rutas que agendan, envían, dividen, "
    "combinan, cancelan o editan están vetadas y no llegan a TikTok. Los datos del "
    "comprador salen como [redactado]; la etiqueta no se descarga y su liga sale "
    "tapada. Esta página no renueva la llave de la tienda. Una salvedad: preguntar "
    "por la etiqueta de un paquete hace que TikTok genere su liga y puede contar "
    "como «etiqueta impresa»."
)


def para_pantalla(*, pagina_max: int = PAGINA_MAX,
                  abiertas: Iterable[str] = ()) -> list[dict[str, Any]]:
    """El catálogo como lo pinta la pantalla: nombre, descripción y campos."""
    sueltas = frozenset(abiertas)
    salida: list[dict[str, Any]] = []
    for c in CATALOGO.values():
        campos = []
        for f in c.campos:
            maximo, ejemplo = f.maximo, f.ejemplo
            if f.nombre == "page_size":
                maximo = min(f.maximo or pagina_max, pagina_max)
                ejemplo = min(int(f.ejemplo or maximo), maximo)
            elif f.tipo in ("ids", "opciones") and maximo is None:
                maximo = IDS_MAX if f.tipo == "ids" else len(f.opciones)
            if isinstance(ejemplo, tuple):
                ejemplo = list(ejemplo)
            campos.append({
                "nombre": f.nombre, "donde": f.donde, "tipo": f.tipo,
                "etiqueta": f.etiqueta, "requerido": f.requerido,
                "opciones": list(f.opciones), "minimo": f.minimo, "maximo": maximo,
                "ejemplo": ejemplo, "ayuda": f.ayuda,
            })
        salida.append({
            "nombre": c.nombre, "grupo": c.grupo, "titulo": c.titulo,
            "pregunta": c.pregunta, "metodo": c.metodo, "ruta": c.plantilla,
            "estado": c.estado, "nota": c.nota,
            "en_cuarentena": c.en_cuarentena and c.nombre not in sueltas,
            "juntos": [list(g) for g in c.juntos], "campos": campos,
        })
    return salida


def regla() -> dict[str, Any]:
    """Los candados, legibles, para el GET del catálogo y para la pantalla."""
    return {
        "ruta": "no se escribe: se elige una consulta del catálogo y el servidor "
                "pone método, ruta y versión. Los ids de la ruta son de 6 a 24 "
                "dígitos ASCII.",
        "lista_negra": {
            "metodos": list(METODOS_LECTURA),
            "post_solo_si_termina_en": list(POST_FINALES_LECTURA),
            "dominios_que_se_leen": sorted(DOMINIOS_LECTURA),
            "dominios_vetados_enteros": dict(PALABRAS_DOMINIO_VETADO),
            "verbos_vetados": sorted(VERBOS),
            "unica_excepcion": sorted(SEGMENTOS_QUE_SOLO_PREGUNTAN),
            "sustantivos_vetados": sorted(SUSTANTIVOS_VETADOS),
        },
        "params": ("sólo los declarados en cada consulta, con su tipo; nunca "
                   f"{', '.join(sorted(RESERVADAS))}; hasta {IDS_MAX} ids por "
                   "llamada; un POST de búsqueda pide al menos un filtro; los "
                   "que van en grupo, todos o ninguno"),
        "cuarentena": ("nace cerrada en el catálogo; sólo la abre su nombre EXACTO en "
                       "INVESTIGACION_TIKTOK_ABIERTAS, y un nombre que no exista no "
                       "abre ninguna"),
        "redaccion": ("lista blanca: ids, fechas y plazos, estados y tipos, guía, "
                      "paquetería, opción de entrega, SKU y producto, importes, pesos "
                      "y medidas, motivos de cancelación — y sólo si el valor tiene "
                      "la forma de ese dato. recipient_address, buyer_*, notas, "
                      "teléfonos, correos, CPF/RFC, cifrados y toda liga se tapan "
                      "enteros. De NUESTRAS bodegas salen nombre, ciudad/municipio, "
                      "estado y código postal."),
        "llaves": ("la respuesta nunca trae token, app secret, firma ni cifrado de "
                   "la tienda; token vencido se informa y no se renueva; si TikTok no "
                   "contesta JSON, de su cuerpo no sale nada"),
        "errores": ("el mensaje de error de TikTok sale por lista blanca de PALABRAS: "
                    "la que no está en el vocabulario (el inglés de sus mensajes "
                    "documentados, el técnico de una API y las palabras del catálogo) "
                    "sale «[…]». Antes se le quitan llaves, ligas, correos y todo "
                    "número de 7 o más dígitos que no sea un id de la consulta; un "
                    "JSON incrustado pasa por el redactor"),
        "falla": "cerrado: lo que no calza, 400 y NO sale a la red",
    }
