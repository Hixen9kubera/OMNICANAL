"""
investigacion_temu.py — Candado, redactor y límite del proxy de INVESTIGACIÓN.

POR QUÉ EXISTE (Brandon, 24-sep-2026)
─────────────────────────────────────
La Open API de Temu sólo contesta desde la IP de Railway: desde la laptop da
`5000003 NOT_IN_IP_WHITE_LIST`. Para investigar cosas que nadie ha medido —hoy,
si Temu deja comprar una guía POR ALMACÉN (TEXCO / TEXCO II) en vez de la guía
combinada— hace falta llamar endpoints de LECTURA desde producción.

`routers/investigacion.py` expone ese paso. Todo lo que decide qué SÍ pasa vive
aquí, en funciones puras y sin red, para que las pruebas lo cubran sin tocar
Temu.

ESTO ES UN PROXY CON LAS CREDENCIALES DE LA TIENDA
──────────────────────────────────────────────────
Un hueco aquí compraría guías, confirmaría envíos o movería stock. Por eso el
candado FALLA CERRADO en tres capas, y cualquiera basta para rechazar:

  1. FORMA. El `type` tiene que ser exactamente `bg.algo.algo…` o
     `temu.algo.algo…`: minúsculas ASCII, dígitos y puntos. Nada de espacios,
     mayúsculas, guiones ni saltos de línea. No se "normaliza": lo que no está
     escrito en la forma canónica se rechaza tal cual.
  2. LA ÚLTIMA PARTE ES DE LECTURA. Convención de Temu: `…get` o `…query`.
  3. NINGÚN VERBO DE ESCRITURA EN NINGUNA PARTE. Aunque termine en `.get`:
     `bg.goods.stock.edit.get` o `bg.order.split.query` se rechazan. Y los
     tipos que terminan en lectura pero escriben (`bg.local.goods.spec.id.get`)
     se vetan por su NÚCLEO, así que sus sucesores `temu.…`/`….v2.…` caen con
     ellos.

Y aparte, los PARÁMETROS no pueden traer llaves del sobre (`type`,
`access_token`, `sign`…): `temu.llamar` mezcla los parámetros DESPUÉS de fijar
el `type`, así que un `{"type": "bg.logistics.shipment.create"}` en los
parámetros reemplazaría el tipo ya validado. Es el hueco más fácil de pasar por
alto y se cierra aquí.

LA RESPUESTA SALE REDACTADA
───────────────────────────
Por LISTA BLANCA para el texto: un valor de texto sale sólo si su llave dice
que es del negocio (ids, SKUs, packageSn, guía, almacén, paquetería, servicio,
estados, importes, medidas, avisos de Temu); todo lo demás se sustituye por
"[redactado]". Encima, una lista negra de datos y roles de persona tapa el
valor entero aunque sea un número o parezca id (`exteriorNumber`, `buyerId`).
Se prefiere redactar DE MÁS: perder una ciudad en una investigación cuesta
nada; filtrar una dirección, mucho. La llave se sigue viendo, así que quien
investiga sabe que el campo existe.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from typing import Any

# ── 1. El candado del tipo ───────────────────────────────────────────────────

# Forma canónica. `fullmatch` y no `match` con `$`: en Python `$` acepta un
# salto de línea al final, y "bg.order.list.v2.get\n" NO es un tipo válido.
_FORMA = re.compile(r"(bg|temu)(\.[a-z0-9]+){2,9}")
_LARGO_MAX = 100

# La última parte, y sólo ella, dice si es lectura.
FINALES_LECTURA = ("get", "query")

# Verbos que invalidan el tipo si aparecen DENTRO de cualquier segmento
# (subcadena). Son lo bastante largos para no chocar con lecturas reales:
# `shipment`, `shippinginfo`, `unshipped` o `accesstoken` NO contienen ninguno.
VERBOS_SUBCADENA: tuple[str, ...] = (
    "create", "update", "edit", "confirm", "cancel", "delete", "remove",
    "submit", "upload", "bind", "split", "merge", "modify", "change", "print",
    "apply", "save", "sync", "purchase", "generate", "assign", "accept",
    "agree", "reject", "approve", "decline", "refresh", "revoke", "subscribe",
    "publish", "release", "activate", "enable", "disable", "adjust",
    "increase", "decrease", "deduct", "commit", "declare", "reply", "resend",
    "retry", "negotiate", "restock", "replenish", "insert", "upsert", "import",
    "export", "transfer", "offline", "withdraw", "register", "notify",
    "dispatch", "authorize", "operate", "handle", "process", "execute",
    "trigger", "reset", "renew", "close",
)

# Verbos CORTOS: sólo cuentan como segmento completo. Como subcadena romperían
# lecturas legítimas (`ship` está en `shipment`, `add` en `address`, `set` en
# `asset`, `del` en `model`).
VERBOS_SEGMENTO: frozenset[str] = frozenset({
    "add", "del", "set", "ship", "send", "pay", "buy", "edit", "bind", "sync",
    "save", "put", "post", "patch", "push", "write", "move", "lock", "unlock",
    "mark", "sign", "book", "stop", "start", "pause", "resume", "cut", "copy",
    "run", "do", "make", "fix", "undo", "kill", "drop", "clear",
})

# Tipos que TERMINAN en `.get` y aun así escriben. Lista corta a propósito: la
# llena la experiencia, no la imaginación.
#   · `bg.local.goods.spec.id.get` GENERA un id de especificación nuevo en el
#     catálogo de Temu cuando la especificación no existe ("Search And Generate
#     Merchant-Customized Specifications").
TIPOS_PROHIBIDOS: frozenset[str] = frozenset({
    "bg.local.goods.spec.id.get",
})

# El veto NO se compara contra la cadena exacta, sino contra su NÚCLEO: sin el
# prefijo (`bg`/`temu`), sin los segmentos de versión (`v2`, `v3`…) y sin la
# parte final de lectura. Temu renombra familias enteras así —`bg.order.amount.
# query` → `temu.order.amount.v2.query`, `bg.local.goods.add` → `temu.local.
# goods.v3.add`—, y el sucesor de un tipo vetado tiene que caer con él:
# `temu.local.goods.spec.id.v2.get` o `bg.local.goods.spec.id.query` son el
# mismo generador con otro nombre.
_VERSION = re.compile(r"v\d+")


def _nucleo(tipo: str) -> str:
    """`temu.local.goods.spec.id.v2.get` → `local.goods.spec.id`."""
    return ".".join(s for s in tipo.split(".")[1:-1] if not _VERSION.fullmatch(s))


_NUCLEOS_PROHIBIDOS: frozenset[str] = frozenset(_nucleo(t) for t in TIPOS_PROHIBIDOS)

# Y por FRAGMENTO del núcleo compacto (sin puntos), para que tampoco pase con
# otra ruta o pegado: `bg.goods.spec.id.get`, `temu.local.goods.specid.get`,
# `bg.local.goods.spec.v2.id.get`. Rechazar de más aquí no cuesta nada: ninguna
# lectura documentada lleva `spec` seguido de `id` (la de especificaciones es
# `temu.local.goods.spec.info.get`, y pasa).
FRAGMENTOS_PROHIBIDOS: tuple[str, ...] = ("specid",)


class Rechazo(ValueError):
    """El candado dijo que no. El mensaje es para la persona que lo pidió."""


def validar_tipo(tipo: Any) -> str:
    """
    Devuelve el `type` tal cual si es una LECTURA permitida; si no, `Rechazo`.

    No normaliza nada —ni `strip`, ni minúsculas—: un tipo que necesita
    arreglarse para pasar es un tipo que no se escribió como Temu lo espera, y
    en un candado la duda se resuelve rechazando.
    """
    if not isinstance(tipo, str) or not tipo:
        raise Rechazo("El `type` es obligatorio y tiene que ser texto.")
    if len(tipo) > _LARGO_MAX:
        raise Rechazo(f"El `type` pasa de {_LARGO_MAX} caracteres.")
    if not _FORMA.fullmatch(tipo):
        raise Rechazo(
            "Forma inválida: el `type` va en minúsculas, sin espacios ni guiones, "
            "y empieza con `bg.` o `temu.` (ej. `bg.order.list.v2.get`).")
    nucleo = _nucleo(tipo)
    compacto = nucleo.replace(".", "")
    if (tipo in TIPOS_PROHIBIDOS or nucleo in _NUCLEOS_PROHIBIDOS
            or any(f in compacto for f in FRAGMENTOS_PROHIBIDOS)):
        raise Rechazo(f"`{tipo}` termina en lectura pero ESCRIBE en Temu: es "
                      f"{' / '.join(sorted(TIPOS_PROHIBIDOS))} o un sucesor suyo "
                      f"(mismo núcleo `{nucleo}`), y está vetado.")
    segmentos = tipo.split(".")
    if segmentos[-1] not in FINALES_LECTURA:
        raise Rechazo(
            f"Sólo se permiten lecturas: la última parte tiene que ser "
            f"{' o '.join(FINALES_LECTURA)}, y aquí es `{segmentos[-1]}`.")
    for seg in segmentos:
        if seg in VERBOS_SEGMENTO:
            raise Rechazo(f"`{seg}` es un verbo de escritura: rechazado aunque "
                          f"termine en {segmentos[-1]}.")
    for verbo in VERBOS_SUBCADENA:
        if verbo in tipo:
            raise Rechazo(f"El `type` contiene `{verbo}`, que es un verbo de "
                          f"escritura: rechazado aunque termine en {segmentos[-1]}.")
    return tipo


# ── 2. Los parámetros ────────────────────────────────────────────────────────

# Llaves del SOBRE de la llamada. `temu.llamar` hace `{type, app_key, …,
# **datos}`: una de estas en los parámetros PISARÍA la validada. Se comparan
# en minúsculas y sin separadores (`Access_Token` = `accesstoken`).
_LLAVES_RESERVADAS = frozenset({
    "type", "method", "api", "apiname", "apitype", "appkey", "appsecret",
    "accesstoken", "datatype", "timestamp", "sign", "signmethod", "version",
})
_PARAMS_BYTES_MAX = 16_000
_PARAMS_HONDO_MAX = 6
_LLAVE_PARAM = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")


def _pasa_de_hondo(v: Any, maximo: int) -> bool:
    """¿Anida más de `maximo` niveles? Iterativo: un JSON hondo no revienta la pila."""
    pila: list[tuple[Any, int]] = [(v, 0)]
    while pila:
        x, n = pila.pop()
        if n > maximo:
            return True
        if isinstance(x, dict):
            pila.extend((y, n + 1) for y in x.values())
        elif isinstance(x, list):
            pila.extend((y, n + 1) for y in x)
    return False


def _norm(llave: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(llave).lower())


def validar_params(params: Any) -> dict[str, Any]:
    """Los parámetros de la llamada, o `Rechazo`. `None` es `{}`."""
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise Rechazo("`params` tiene que ser un objeto JSON ({…}).")
    for llave in params:
        if not isinstance(llave, str) or not _LLAVE_PARAM.fullmatch(llave):
            raise Rechazo(f"Llave de parámetro inválida: {str(llave)[:40]!r}.")
        if _norm(llave) in _LLAVES_RESERVADAS:
            raise Rechazo(f"`{llave}` es parte del sobre de la llamada y no se "
                          f"puede mandar como parámetro.")
    if _pasa_de_hondo(params, _PARAMS_HONDO_MAX):
        raise Rechazo(f"`params` anida más de {_PARAMS_HONDO_MAX} niveles.")
    try:
        crudo = json.dumps(params, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise Rechazo(f"`params` no es JSON serializable: {exc}") from exc
    if len(crudo.encode("utf-8")) > _PARAMS_BYTES_MAX:
        raise Rechazo(f"`params` pasa de {_PARAMS_BYTES_MAX} bytes.")
    return params


# ── 3. El redactor ───────────────────────────────────────────────────────────
#
# CRITERIO: para el TEXTO, lista BLANCA. La primera versión tapaba por lista
# NEGRA de llaves y perdía contra cada variante que nadie había listado
# (`cityName`, `signedBy`, `personName`, `exteriorNumber`, `label`…): Temu
# nombra sus campos como quiere, y una respuesta que nadie ha visto —que es
# justo lo que se viene a investigar— trae llaves que nadie previó. Ahora:
#
#   1. DATO o ROL de persona (lista negra, por subcadena): se tapa el valor
#      ENTERO, sea texto, número u objeto. Es lo que tiene que caer aunque sea
#      un número (`exteriorNumber: 12`) o parezca id (`buyerId`, `personId`).
#      La subcadena vale dentro de UNA palabra o de palabras enteras, nunca a
#      caballo entre dos: `warehouseRegion` no es `user`, `packageOrder` no es
#      `geo`, `warehouseNo` no es `houseNo` y `shipTotal` no es `shipTo`.
#   2. Todo TEXTO sale tapado SALVO que su llave diga que es del negocio: ids y
#      sn, guía, almacén, paquetería, servicio, canal, producto/SKU/spec,
#      estados, tipos, fechas, importes, medidas, errores y avisos de Temu
#      (`failReasonText`, `warningMessage`) — lista blanca, por PALABRA de la
#      llave: `warehouseName` = warehouse+name.
#   3. TEXTO LIBRE (`…Text`, `…Desc`, `…Message`, `…Info`…) sólo sale si es
#      un aviso de Temu o vive dentro de un objeto del negocio: el `statusText`
#      de un rastreo lo escribe la paquetería, y en México es común que diga
#      "Entregado — Recibió: <nombre>".
#   4. Dentro de un objeto del NEGOCIO (`warehouseList[…]`, `onlineChannelDto
#      List[…]`, `goodsList[…]`…) el texto sale, salvo palabras de persona.
#      Ahí `name` es el de la bodega y `pickupRules` es de Temu.
#   5. Números, booleanos y null pasan (cantidades, estados, fechas, ids).
#
# Se prefiere redactar DE MÁS: la llave se sigue viendo con "[redactado]", así
# que quien investiga sabe que el campo existe; si hace falta su valor, se
# agrega a la lista blanca con su razón.

REDACTADO = "[redactado]"

# 1a. DATO personal o credencial: SIEMPRE se tapa, aunque la llave parezca id
# (`taxId` es el RFC de alguien, no un id del negocio). Subcadenas de la llave
# en minúsculas y sin separadores (ver `_contiene`).
_DATO: tuple[str, ...] = (
    "phone", "mobile", "telephone", "telno", "email", "street", "addr",
    "postcode", "postalcode", "zipcode", "zip", "taxcode", "taxid", "taxnumber",
    "taxno", "rfc", "curp", "passport", "idcard", "identity", "idnumber",
    "idno", "documentnumber", "docnumber", "nationalid", "personalid", "dni",
    "birth", "gender", "latitude", "longitude", "coordinate", "geo",
    "firstname", "lastname", "fullname", "middlename", "nickname", "realname",
    "surname", "username", "avatar", "housenumber", "houseno", "housenum",
    "doornumber", "doorno", "doornum", "exterior", "interior", "apartment",
    "colonia", "neighborhood", "neighbourhood", "password", "passwd", "secret",
    "token", "cookie", "authorization", "authcode", "credential", "appkey",
    "signature", "card", "account", "bankaccount", "clabe", "iban", "cvv",
    "openid", "unionid",
)
# La llave entera o su ÚLTIMA palabra (`loginIp`, `shopTel`).
_DATO_EXACTAS = frozenset({"sign", "ip", "tel", "lat", "lng", "lon", "cpf", "mail"})

# 1b. ROLES de persona: todo lo que cuelga de ellos se va, ids incluidos
# (`buyerId` identifica a una persona, no un paquete). Subcadenas.
_ROL: tuple[str, ...] = (
    "receipt", "consignee", "recipient", "receiver", "buyer", "customer",
    "contact", "sender", "user", "remark", "comment", "memo", "person",
    "client", "payer", "consumer", "holder", "owner", "signer", "signed",
    "deliveredto", "deliverto", "shipto", "billto", "soldto", "instruction",
    "customiz", "personaliz", "engrav",
)

# Palabras de persona que, además, vetan el texto aunque la llave traiga una
# palabra del negocio (`warehouseManagerName`, `carrierDriverName`).
_PERSONA: frozenset[str] = frozenset({
    "driver", "keeper", "manager", "operator", "staff", "employee", "agent",
    "rider", "messenger", "handler", "picker", "packer", "nick", "nickname",
})

# 2. LISTA BLANCA del texto, por PALABRA de la llave.
# Palabras del negocio: si la llave trae una, su texto sale.
_NEGOCIO: frozenset[str] = frozenset({
    "warehouse", "warehouses", "carrier", "carriers", "logistics", "company",
    "companies", "service", "services", "channel", "channels", "goods",
    "sku", "skus", "spec", "specs", "specification", "specifications",
    "product", "products", "category", "categories", "cat", "cats",
    "template", "templates", "freight", "brand", "brands", "property",
    "properties", "attribute", "attributes", "attr", "attrs", "country",
    "countries", "currency", "mall", "shop", "store", "site", "api", "apis",
    "scope", "scopes", "permission", "permissions", "event", "events",
})
# Avisos y fallos que redacta TEMU, no una persona: son la respuesta a "¿por
# qué este canal no está disponible para este almacén?". `reason` suelto NO:
# en posventa es lo que escribe el comprador.
_SISTEMA: frozenset[str] = frozenset({
    "fail", "failed", "failure", "warning", "warnings", "solution",
    "estimated", "unavailable", "abnormal",
})
# Última palabra de un TEXTO LIBRE (ver el punto 3 de arriba).
_TEXTO_LIBRE: frozenset[str] = frozenset({
    "text", "texts", "desc", "description", "content", "message", "messages",
    "msg", "detail", "details", "remark", "comment", "note", "notes", "info",
    "reason", "memo",
})
# Palabras de estado, tipo, tiempo, importe o medida.
_ENUM: frozenset[str] = frozenset({
    "status", "statuses", "type", "types", "unit",
    "units", "method", "mode", "time", "times", "date", "timezone", "version",
    "level", "flag", "flags", "scene", "quantity", "qty", "count", "total",
    "amount", "amounts", "price", "prices", "fee", "fees", "cost", "costs",
    "weight", "length", "width", "height", "volume", "size", "rate",
    "percent", "percentage", "page", "success", "result", "enable",
    "enabled", "default", "expire", "expired", "expiration", "duration",
    "sort",
})
# La guía, en sus nombres de siempre (`mailNo`/`waybill` son el vocabulario
# chino de la guía; `trackingNum` es el de `temu.track.trackinginfo.get`).
_GUIA: frozenset[str] = frozenset({"tracking", "waybill"})
_GUIA_NO: frozenset[str] = frozenset({
    "mail", "express", "ship", "shipping", "shipment", "logistics",
    "delivery", "order", "package", "parcel", "fulfill", "fulfillment",
})
_ID_FIN: frozenset[str] = frozenset({"id", "ids", "sn", "sns"})
_LISTA_FIN: frozenset[str] = frozenset({"list", "set", "array"})

# 4. Objetos del NEGOCIO: su última palabra (quitando `List`, `Info`, `Dto`…)
# es una de éstas. Dentro, el texto sale salvo palabras de persona. Más corta
# que `_NEGOCIO` a propósito: un `serviceInfo` o un `companyInfo` pueden ser
# de cualquiera, y ninguna respuesta documentada los necesita.
_PADRES_NEGOCIO: frozenset[str] = frozenset({
    "warehouse", "warehouses", "carrier", "carriers",
    "channel", "channels", "goods", "sku", "skus",
    "spec", "specs", "specification", "specifications", "product",
    "products", "category", "categories", "cat", "cats", "template",
    "templates", "freight", "brand", "brands", "property", "properties",
    "attribute", "attributes", "attr", "attrs",
})
_COLAS_PADRE: frozenset[str] = frozenset({
    "list", "lists", "info", "infos", "map", "detail", "details", "vo", "vos",
    "dto", "dtos", "data", "item", "items", "array", "set", "obj", "object",
})

_PALABRA = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_CORREO = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_HONDO_MAX = 40


def _palabras(llave: str) -> tuple[str, ...]:
    """`inventoryDeductionWarehouseName` → (inventory, deduction, warehouse, name).

    Parte camelCase, snake_case y siglas (`SKUId` → sku, id); los dígitos se
    van (`regionName1` → region, name).
    """
    return tuple(x.lower() for x in _PALABRA.findall(str(llave)) if not x.isdigit())


def _partes(llave: str) -> tuple[str, frozenset[int], str]:
    """(llave compacta en minúsculas, dónde empieza cada palabra, última palabra)."""
    trozos = [x.lower() for x in _PALABRA.findall(str(llave))]
    cortes, pos = {0}, 0
    for t in trozos:
        pos += len(t)
        cortes.add(pos)
    ultima = next((t for t in reversed(trozos) if not t.isdigit()), "")
    return "".join(trozos), frozenset(cortes), ultima


def _contiene(n: str, cortes: frozenset[int], m: str) -> bool:
    """
    ¿`m` está en `n` sin partir palabras? Vale dentro de una sola palabra
    (`contactless` ⊃ contact; en una llave toda en minúsculas, `deliverymobile`
    es UNA palabra y ⊃ mobile) o cubriendo palabras enteras (`postCode` =
    postcode, `shipTo` = shipto). No vale a caballo: `warehouseRegion` ⊅ user.
    """
    i = n.find(m)
    while i >= 0:
        j = i + len(m)
        if not any(i < c < j for c in cortes) or (i in cortes and j in cortes):
            return True
        i = n.find(m, i + 1)
    return False


def _es_dato(llave: str) -> bool:
    n, cortes, ultima = _partes(llave)
    if (n in _DATO_EXACTAS or ultima in _DATO_EXACTAS
            or ultima.endswith(("mail", "phone", "tel"))):
        return True
    return any(_contiene(n, cortes, m) for m in _DATO)


def _es_rol(llave: str) -> bool:
    n, cortes, ultima = _partes(llave)
    if ultima.endswith(("note", "notes")):
        return True
    return any(_contiene(n, cortes, m) for m in _ROL)


def _es_id(p: tuple[str, ...]) -> bool:
    if not p:
        return False
    if p[-1] in _ID_FIN:
        return True
    return len(p) >= 2 and p[-1] in _LISTA_FIN and p[-2] in _ID_FIN


def _es_guia(p: tuple[str, ...]) -> bool:
    """El NÚMERO de guía (`trackingNumber`, `mailNo`, `waybill`), no su texto."""
    if p[-1] in _TEXTO_LIBRE:
        return False
    if any(x in _GUIA for x in p):
        return True
    q = list(p)
    while q and q[-1] in _LISTA_FIN:         # `batchOrderNumberList`
        q.pop()
    return (len(q) >= 2 and q[-1] in ("no", "nos", "num", "number")
            and q[-2] in _GUIA_NO)


def _padre_negocio(padre: str) -> bool:
    p = list(_palabras(padre))
    while p and p[-1] in _COLAS_PADRE:
        p.pop()
    return bool(p) and p[-1] in _PADRES_NEGOCIO


def _texto_sale(llave: str, padre: str) -> bool:
    """¿El TEXTO bajo `llave` (dentro del objeto `padre`) es del negocio?"""
    p = _palabras(llave)
    if not p or any(x in _PERSONA for x in p):
        return False
    if p[0] in ("error", "err") or any(x in _SISTEMA for x in p):
        return True
    if p[-1] in _TEXTO_LIBRE:
        return _padre_negocio(padre)
    if (_es_id(p) or _es_guia(p) or "".join(p).endswith("extcode")
            or any(x in _NEGOCIO or x in _ENUM for x in p)):
        return True
    return _padre_negocio(padre)


def _json_anidado(s: str) -> Any:
    """El objeto o lista si `s` es JSON serializado; si no, None."""
    t = s.strip()
    if t[:1] not in ("{", "[") or len(t) < 2:
        return None
    try:
        anidado = json.loads(t)
    except ValueError:
        return None
    return anidado if isinstance(anidado, (dict, list)) else None


def _sin_correos(s: str, cuenta: list[int]) -> str:
    nuevo, n = _CORREO.subn(REDACTADO, s)
    cuenta[0] += n
    return nuevo


def _redactar(v: Any, llave: str, padre: str, cuenta: list[int], hondo: int) -> Any:
    """
    `llave`: la llave bajo la que vive `v` (los elementos de una lista heredan
    la de la lista: `packageSnList[0]` es un packageSn). `padre`: la llave del
    objeto que contiene a `v` (`warehouseList[0].name` → padre warehouseList).
    """
    if hondo > _HONDO_MAX:
        cuenta[0] += 1
        return "[demasiado hondo]"
    if isinstance(v, dict):
        salida: dict[str, Any] = {}
        for k, w in v.items():
            # Un booleano o un null no revelan a nadie (`isNoContactDelivery
            # Channel: true` es un dato del canal, no del comprador).
            if (_es_dato(k) or _es_rol(k)) and not (w is None or isinstance(w, bool)):
                salida[k] = REDACTADO
                cuenta[0] += 1
            else:
                salida[k] = _redactar(w, str(k), llave, cuenta, hondo + 1)
        return salida
    if isinstance(v, list):
        return [_redactar(w, llave, padre, cuenta, hondo + 1) for w in v]
    if isinstance(v, str):
        # JSON serializado dentro de un texto: se abre y decide cada llave suya.
        anidado = _json_anidado(v)
        if anidado is not None:
            limpio = _redactar(anidado, llave, padre, cuenta, hondo + 1)
            return json.dumps(limpio, ensure_ascii=False)
        if not v or _texto_sale(llave, padre):
            return _sin_correos(v, cuenta)
        cuenta[0] += 1
        return REDACTADO
    return v


def redactar(v: Any) -> tuple[Any, int]:
    """(copia redactada, cuántos campos se taparon). No modifica el original.

    La raíz no tiene llave: un texto suelto o una lista de textos en la raíz
    se tapan (no hay llave que diga que son del negocio).
    """
    cuenta = [0]
    return _redactar(v, "", "", cuenta, 0), cuenta[0]


def redactar_texto(s: str, largo: int = 600) -> str:
    """Un mensaje de error de Temu: sin correos, recortado y, si trae JSON, redactado."""
    t = str(s)[:largo]
    cuenta = [0]
    anidado = _json_anidado(t)
    if anidado is not None:
        return json.dumps(_redactar(anidado, "", "", cuenta, 0), ensure_ascii=False)
    return _sin_correos(t, cuenta)


# ── 4. El límite ─────────────────────────────────────────────────────────────

class Limitador:
    """
    Ventana deslizante en memoria: `maximo` llamadas cada `ventana_s`.

    Es GLOBAL, no por persona, a propósito: la cuota de Temu es de la APP, y es
    la misma que usan el sondeo de pedidos, el refresco de guías y el fan-out
    de stock. Una investigación que la agote frena la operación real.
    """

    def __init__(self, maximo: int = 30, ventana_s: float = 60.0) -> None:
        self.maximo = maximo
        self.ventana_s = ventana_s
        self._marcas: deque[float] = deque()
        self._candado = threading.Lock()

    def permite(self, ahora: float | None = None) -> bool:
        t = time.monotonic() if ahora is None else ahora
        with self._candado:
            while self._marcas and t - self._marcas[0] >= self.ventana_s:
                self._marcas.popleft()
            if len(self._marcas) >= self.maximo:
                return False
            self._marcas.append(t)
            return True

    def espera_s(self, ahora: float | None = None) -> int:
        """Cuántos segundos faltan para que se libere un lugar."""
        t = time.monotonic() if ahora is None else ahora
        with self._candado:
            if not self._marcas:
                return 0
            return max(1, int(self.ventana_s - (t - self._marcas[0])) + 1)


# ── 5. Qué significa cada código (para que un "falla" no entierre un camino) ──

# Mismo criterio que `/api/automatizacion/temu/probar`: SÓLO 3000003 dice "no
# existe"; los demás dicen "existe, pero…".
CODIGOS: dict[str, str] = {
    "3000003": "NO EXISTE ese tipo",
    "3000037": "EXISTE, pero hay una versión más nueva",
    "3000004": "EXISTÍA y se retiró (tiene sucesor)",
    "3000032": "EXISTE pero la tienda NO nos ha dado permiso para esa API",
    "3000000": "EXISTE — un parámetro no tiene la forma esperada (el mensaje suele decir cuál)",
    "5000003": "la IP no está en la lista blanca de Temu",
    "120012016": "EXISTE y responde — faltan parámetros u orden inválida",
    "180020003": "EXISTE y responde — faltan parámetros",
    "4000000": "EXISTE y responde — parámetro inválido",
    "7000000": "EXISTE y responde — le faltan datos de negocio",
    "10002": "EXISTE y responde — parámetros inválidos",
    "120011002": "EXISTE y responde — parámetros inválidos",
    "120018027": "EXISTE y responde — pide un packageSn válido",
    "170070010": "EXISTE y responde — pide packageSn",
    "120012038": "la etiqueta todavía no está lista",
}


# ── 6. Tipos de lectura sugeridos ────────────────────────────────────────────

# `verificado` = el panel ya lo llama en producción (o lo sondeó y contestó).
# `por verificar` = sale de la documentación de Temu; si contesta 3000003 es
# que no existe con ese nombre. La lista es una AYUDA: el candado no la usa —
# cualquier lectura que pase la regla se puede consultar.
TIPOS_SUGERIDOS: list[dict[str, Any]] = [
    # Órdenes
    {"type": "bg.order.list.v2.get", "estado": "verificado",
     "para": "Listado de órdenes (trae inventoryDeductionWarehouseId: de qué bodega descontó Temu)",
     "params": {"pageNumber": 1, "pageSize": 10}},
    {"type": "bg.order.detail.v2.get", "estado": "verificado",
     "para": "Detalle de una orden",
     "params": {"parentOrderSn": "PO-211-..."}},
    {"type": "bg.order.unshipped.package.get", "estado": "verificado",
     "para": "Paquetes con etiqueta comprada y envío sin confirmar (aquí se ve el combinado)",
     "params": {"parentOrderSnList": ["PO-211-..."], "pageNumber": 1, "pageSize": 20}},
    {"type": "bg.order.shippinginfo.v2.get", "estado": "verificado",
     "para": "Info de envío de la orden (la dirección sale REDACTADA)",
     "params": {"parentOrderSn": "PO-211-..."}},
    {"type": "bg.order.combinedshipment.list.get", "estado": "por verificar",
     "para": "Órdenes que Temu propone enviar juntas (envío combinado)",
     "params": {"pageNumber": 1, "pageSize": 10}},
    {"type": "bg.order.amount.query", "estado": "verificado",
     "para": "Importes de la orden (hoy 3000032: falta permiso de la tienda)",
     "params": {"parentOrderSnList": ["PO-211-..."]}},
    # Logística
    {"type": "bg.logistics.warehouse.list.get", "estado": "por verificar",
     "para": "Almacenes de envío registrados en Temu (¿están TEXCO y TEXCO II?)",
     "params": {}},
    {"type": "bg.logistics.companies.get", "estado": "por verificar",
     "para": "Paqueterías disponibles",
     "params": {}},
    {"type": "bg.logistics.shippingservices.get", "estado": "por verificar",
     "para": "Servicios de envío cotizables para un almacén y unas órdenes (la pregunta de la guía por almacén)",
     "params": {"warehouseId": "WH-...", "orderSnList": ["211-..."],
                "weight": "1", "weightUnit": "kg", "length": "10",
                "width": "10", "height": "10", "dimensionUnit": "cm"}},
    {"type": "bg.logistics.online.shippingservice.get", "estado": "verificado",
     "para": "Servicio de envío en línea (sondeado el 1-sep; parámetros por descubrir)",
     "params": {}},
    {"type": "bg.logistics.shipment.v2.get", "estado": "verificado",
     "para": "Envío confirmado de una orden: guía, paquetería, packageSn",
     "params": {"parentOrderSn": "PO-211-...", "orderSn": "211-..."}},
    {"type": "bg.logistics.shipment.result.get", "estado": "por verificar",
     "para": "Resultado de una compra de envío ya hecha (por packageSn)",
     "params": {"packageSnList": ["PK-..."]}},
    {"type": "bg.logistics.shipment.document.get", "estado": "verificado",
     "para": "Si ya hay etiqueta por packageSn (la URL sale REDACTADA: el PDF lleva la dirección)",
     "params": {"documentType": "SHIPPING_LABEL_PDF", "packageSnList": ["PK-..."]}},
    # Catálogo y stock
    {"type": "bg.local.goods.list.query", "estado": "verificado",
     "para": "Productos (quantity = stock que Temu ve). goodsSearchType ENTERO: 1, 4, 5 o 6",
     "params": {"goodsSearchType": 4, "pageNo": 1, "pageSize": 10}},
    {"type": "bg.local.goods.sku.list.price.query", "estado": "verificado",
     "para": "Precios por SKU",
     "params": {"goodsIdList": [0]}},
    {"type": "bg.freight.template.list.query", "estado": "verificado",
     "para": "Plantillas de envío (van atadas al almacén de salida)",
     "params": {"pageNo": 1, "pageSize": 20}},
    # Credencial
    {"type": "bg.open.accesstoken.info.get", "estado": "verificado",
     "para": "Qué APIs autorizó la tienda para este token (la respuesta tapa el token)",
     "params": {}},
]


def regla() -> dict[str, Any]:
    """La regla del candado, legible, para el GET /tipos y para la pantalla."""
    return {
        "forma": "bg.xxx.yyy… o temu.xxx.yyy… — minúsculas ASCII, dígitos y puntos; "
                 "de 3 a 10 partes; sin espacios, mayúsculas ni guiones; máx. "
                 f"{_LARGO_MAX} caracteres. No se normaliza: lo que no viene así, se rechaza.",
        "ultima_parte": list(FINALES_LECTURA),
        "verbos_prohibidos_en_cualquier_parte": list(VERBOS_SUBCADENA),
        "verbos_prohibidos_como_parte_completa": sorted(VERBOS_SEGMENTO),
        "tipos_vetados": sorted(TIPOS_PROHIBIDOS),
        "tipos_vetados_por_nucleo": (
            "se comparan sin prefijo bg/temu, sin segmentos vN y sin la parte final: "
            f"núcleos {sorted(_NUCLEOS_PROHIBIDOS)}; y ningún núcleo compacto puede "
            f"contener {list(FRAGMENTOS_PROHIBIDOS)}"),
        "params": ("objeto JSON; llaves [A-Za-z][A-Za-z0-9_]*; sin llaves del sobre "
                   f"({', '.join(sorted(_LLAVES_RESERVADAS))}); máx. "
                   f"{_PARAMS_BYTES_MAX} bytes y {_PARAMS_HONDO_MAX} niveles"),
        "falla": "cerrado: lo que no calza, 400 y NO sale a la red",
        "redaccion": ("texto: lista blanca por palabra de la llave (ids/sn, guía, "
                      "almacén, paquetería, canal, producto/SKU/spec, estados, "
                      "importes, medidas, avisos de Temu); texto libre (…Text, …Desc…) "
                      "sólo dentro de objetos del negocio; datos y roles de persona "
                      "se tapan enteros aunque sean números; lo demás, [redactado]"),
    }
