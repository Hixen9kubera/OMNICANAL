"""
ml_multiget.py — El multiget de publicaciones de Mercado Libre: de `/items?ids=`
a `/items/bulk?ids=` sin que ningún llamador cambie su forma de leer.

POR QUÉ EXISTE. ML deprecó el multiget `GET /items?ids=` (y `/users?ids=`, que
aquí nadie usa): hay que pasar a `GET /items/bulk?ids=` antes del 25-oct-2026;
hasta entonces conviven los dos. El reemplazo NO es solo otra ruta: contesta
con OTRA FORMA. Medido el 6-oct-2026 con el token de BEKURA y los mismos 5 ids
(uno propio, dos de la otra cuenta, uno inexistente y uno ajeno borrado):

  /items?ids=                     [{"code": 200|403|404, "body": {...}}]
                                  un fallo trae body = {id, message, error, status, cause}
  /items/bulk?ids=                [{"id", "status_code": 200, "body": {...}}]
                                  [{"id", "status_code": 403|404, "error": {"message"}}]
  /items/bulk + attributes=body.x [{"body": {...}}] por cada 200 y {} VACÍO por cada
                                  fallo: no se sabe cuál falló ni por qué.
  /items/bulk + attributes=x      [{"id"}] para TODOS y sin error: el filtro se
                                  aplica al SOBRE y el item se pierde entero.

O sea: cambiar solo la ruta rompe EN SILENCIO a 8 de los 9 llamadores (todos
piden `attributes` y casi todos filtran `code == 200`, que en bulk no existe):
el Checklist da todo «no viva», Crear FULL todo «sin verificar», la ficha de
ML guarda TODAS las fichas vacías y pisa los pesos medidos una semana. Ninguna
de las dos rutas respeta el orden del pedido (cambió entre dos llamadas
iguales): todos los llamadores indexan por `body.id`.

QUÉ HACE. Nada de red: cada llamador conserva su cliente HTTP, su token y su
manejo del 401 (son distintos a propósito: el Checklist nunca renueva, la
ficha renueva con candado async…). Aquí solo se decide
  · `ruta()`       → "/items/bulk", o "/items" con `ML_ITEMS_BULK=false`;
  · `params()`     → en bulk NUNCA manda `attributes`: pide el item completo
                     y el recorte se hace del lado nuestro. En el modo legado,
                     `attributes` como siempre;
  · `normalizar()` → cualquiera de las tres formas, de vuelta a la LEGADA
                     [{"code", "body"}] que todos los llamadores ya saben leer.
                     Un id que ML no devolvió NO se inventa: qué hacer con lo
                     que falta lo sigue decidiendo cada llamador, como antes.

Tope: 20 ids por llamada en las dos rutas.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from config import settings

log = logging.getLogger("omnicanal.ml_multiget")

TOPE = 20                    # ids por llamada: el mismo en /items y en /items/bulk
RUTA_BULK = "/items/bulk"
RUTA_LEGADA = "/items"

# El `error` que el legado ponía en el body de un fallo. Bulk solo trae el
# `message`; para los dos códigos medidos el 6-oct se repone el mismo texto que
# daba el legado. Otro código va sin él (nadie decide con este campo hoy).
_ERROR_POR_CODIGO = {403: "access_denied", 404: "not_found"}


def usa_bulk() -> bool:
    """`ML_ITEMS_BULK` (default encendido). Apagado = la ruta vieja, tal cual."""
    return bool(getattr(settings, "ml_items_bulk", True))


def ruta() -> str:
    return RUTA_BULK if usa_bulk() else RUTA_LEGADA


def params(ids: Iterable[str] | str, campos: Iterable[str] | str | None = None
           ) -> dict[str, str]:
    """Los query params del multiget. `ids` va como viene (lista o ya unida por
    comas); el llamador sigue partiendo en lotes de `TOPE`. `campos` solo viaja
    en el modo legado: en bulk, con o sin el prefijo `body.`, ML pierde en
    silencio el item o el motivo del fallo (ver el encabezado)."""
    par = {"ids": ids if isinstance(ids, str) else ",".join(str(i) for i in ids)}
    if campos and not usa_bulk():
        par["attributes"] = campos if isinstance(campos, str) else ",".join(campos)
    return par


def _campos(campos: Iterable[str] | str | None) -> tuple[str, ...]:
    """Los campos de PRIMER nivel a conservar. Acepta la forma legada
    ("id,status") y la de bulk ("body.id,body.status"); de un campo anidado
    ("shipping.logistic_type") se conserva el objeto entero."""
    if not campos:
        return ()
    partes = campos.split(",") if isinstance(campos, str) else list(campos)
    salida: list[str] = []
    for p in partes:
        p = str(p).strip()
        if p.startswith("body."):
            p = p[len("body."):]
        p = p.split(".", 1)[0]
        if p and p not in salida:
            salida.append(p)
    return tuple(salida)


def _recortar(body: dict[str, Any], quedan: tuple[str, ...]) -> dict[str, Any]:
    """El body con solo lo pedido (y siempre el `id`), como lo recortaba ML en
    el legado. Sin campos pedidos, el item completo."""
    if not quedan:
        return dict(body)
    return {k: v for k, v in body.items() if k == "id" or k in quedan}


def _sobre(x: Any, quedan: tuple[str, ...]) -> dict[str, Any] | None:
    """Un elemento de la respuesta, en la forma legada. None = sin forma: no se
    puede saber de qué id es ni qué pasó."""
    if not isinstance(x, dict) or not x:
        return None
    body = x.get("body") if isinstance(x.get("body"), dict) else None
    if "code" in x:
        # Legado (o el interruptor apagado): tal cual, el 200 recortado. El
        # body de un fallo no se toca: ahí viven `id` y `status` (403/404).
        if x.get("code") == 200 and body is not None:
            return {"code": 200, "body": _recortar(body, quedan)}
        return {"code": x.get("code"), "body": dict(body or {})}
    if "status_code" in x:
        # Bulk sin attributes: el código y el id vienen en el sobre.
        codigo = x.get("status_code")
        iid = x.get("id") or (body or {}).get("id")
        if not iid:
            return None
        if codigo == 200:
            if not body:
                # Un 200 sin item (sin body o con `{}`) no es dato: no se inventa.
                # Leído como item vacío, la ficha de ML guardaría la ficha en
                # blanco y pisaría el peso medido una semana.
                return None
            b = dict(body)
            b.setdefault("id", iid)
            return {"code": 200, "body": _recortar(b, quedan)}
        err = x.get("error")
        err = err if isinstance(err, dict) else ({"error": err} if err else {})
        # El body de error como el del legado: hay llamadores que se fían, sin
        # saberlo, de que trae `id` y un `status` numérico (alinear_ml_drop
        # descarta así a los 403/404; la ficha de ML los guarda vacíos para no
        # volver a preguntar en una semana).
        return {"code": codigo, "body": {
            "id": iid, "message": err.get("message"),
            "error": err.get("error") or _ERROR_POR_CODIGO.get(codigo),
            "status": codigo}}
    if body is not None and body.get("id"):
        # Bulk con attributes=body.*: solo los 200 traen body.
        return {"code": 200, "body": _recortar(body, quedan)}
    # {} (un fallo con body.*), [{"id"}] (attributes sin `body.`) o algo nuevo.
    return None


def normalizar(respuesta: Any, campos: Iterable[str] | str | None = None
               ) -> list[dict[str, Any]]:
    """La respuesta del multiget —legada o bulk, con o sin attributes— como
    [{"code", "body"}], en el orden en que llegó.

    · el 200 trae el item recortado a `campos` (+ `id`) si se piden;
    · cada fallo conserva su código y su id: body = {id, message, error, status};
    · lo que no se puede atribuir a un id se descarta (y se avisa en el log);
    · lo que ML no devolvió no aparece: aquí no se inventa ningún 404.
    Una respuesta que no es lista da [], y avisa en el log salvo con None (es
    lo que devuelven en cada error los clientes que ya lo registraron, como
    `competencia_ml._get`). Quien antes se caía con una forma desconocida y
    quiere seguir cayéndose (el Checklist) revisa la lista ANTES de llamar."""
    if not isinstance(respuesta, list):
        if respuesta is not None:
            # El cambio de forma más silencioso: de arriba, no de un sobre.
            log.warning("multiget de ML: respuesta sin forma conocida (%s); se ignora",
                        type(respuesta).__name__)
        return []
    quedan = _campos(campos)
    salida: list[dict[str, Any]] = []
    sin_forma = 0
    for x in respuesta:
        sobre = _sobre(x, quedan)
        if sobre is None:
            sin_forma += 1
        else:
            salida.append(sobre)
    if sin_forma:
        # WARNING y no DEBUG: es justo la rotura que no hace ruido (¿alguien
        # mandó `attributes` a /items/bulk? ¿ML cambió la forma otra vez?).
        log.warning("multiget de ML: %d de %d sobres sin forma conocida; se ignoran",
                    sin_forma, len(respuesta))
    return salida
