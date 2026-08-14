"""
temu.py — Cliente de la API abierta de Temu (mallId 635517742093915, regionId 128).

Nace SOLO LECTURA a propósito. Publicar en Temu quema el SKU para siempre si algo
falla después de que el alta se acepta (`150010090`: borrar NO libera el
`externalSkuId`), así que el escritor se agrega aparte y con su propio dale.

CÓMO SE FIRMA (lo que costó reconstruir). Todo va en el CUERPO de un POST al
mismo `router`: no hay rutas por recurso, el endpoint se elige con `type`. La
firma es MD5 en MAYÚSCULAS de:

    app_secret + concat(clave+valor de TODOS los params, ordenados) + app_secret

sin separadores — ni `=` ni `&` — y con los objetos y listas serializados a JSON
compacto. Es el mismo esquema de Pinduoduo. El `sign` no se incluye a sí mismo.

⚠️ La misma trampa del webhook: el código de ejemplo de la propia doc de Temu
firma con formato `clave=valor&`, que NO reproduce sus propios ejemplos. Ver
`docs/TEMU_MANUAL.md` §1. Aquí se firma concatenando.

TRAMPAS DEL LISTADO, medidas el 12-ago (dos errores SILENCIOSOS costaron el censo):

  · `goodsSearchType` es ENTERO nativo y obligatorio. Como cadena da `3000000`;
    ausente da `7000000`.
  · La paginación es `pageNo`, NO `page`. `page` se acepta **en silencio** y
    devuelve siempre la página 1 — así el censo se quedó en 100 de 124 sin que
    ningún error lo dijera.
  · `pageSize` tope 100 (200 → `1000000`).
  · Las cubetas son CUATRO, no dos. `docs/TEMU_MANUAL.md` documentó 1 y 4 (era
    lo que había el 12-ago); sondeando `goodsSearchType` de 0 a 7 el 14-ago
    aparecieron también la **5** (2 productos) y la **6** (1). Se descubrió
    porque dos productos que la doc daba por publicados —`ILUM-0089-PLA` y
    `HERR-0374-MUL`— no salían en el censo. Ninguna cubeta da error: las vacías
    contestan lista vacía, así que **una cubeta que no se pregunta no se
    extraña**. Si aparecen productos que el panel no ve, sondear otra vez.

Credenciales: `TEMU_APP_KEY`, `TEMU_APP_SECRET`, `TEMU_ACCESS_TOKEN`,
`TEMU_API_BASE`. ⚠️ Al 14-ago viven SOLO en el `.env` local: **no están en
Railway**, así que en producción este módulo se reporta como no configurado.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx

from config import settings

log = logging.getLogger("omnicanal.temu")

# Cubetas del listado. Son DISJUNTAS: para el censo completo hay que unirlas.
# Sondeadas una por una el 14-ago (0 a 7): las vivas son estas cuatro.
CUBETAS = (1, 4, 5, 6)
PAGE_SIZE = 100

# Qué significan los códigos de estado, hasta donde está VERIFICADO. Temu no
# documenta esta tabla; lo de aquí salió de cruzar productos cuyo estado real se
# conocía por el Seller Center. Lo que no está aquí, no se inventa.
ESTADOS = {
    "2/8": "Incompleto",   # los 4 publicados el 13-ago que el panel de Temu marca así
    "5/None": "Borrador",  # ILUM-0089-PLA y HERR-0374-MUL, ambos con precio 0.00
}


def _cfg(nombre: str) -> str:
    return str(getattr(settings, nombre, "") or "").strip()


def disponible() -> bool:
    """Hay credenciales para hablar con Temu."""
    return bool(_cfg("temu_app_key") and _cfg("temu_app_secret")
                and _cfg("temu_access_token") and _cfg("temu_api_base"))


def _firmar(params: dict[str, Any]) -> str:
    """MD5(secret + clave+valor ordenados y CONCATENADOS + secret), en mayúsculas."""
    secreto = _cfg("temu_app_secret")
    partes: list[str] = []
    for clave in sorted(params):
        valor = params[clave]
        if isinstance(valor, (dict, list)):
            # Compacto y sin escapar acentos: la firma se calcula sobre el MISMO
            # texto que viaja en el cuerpo, así que ambos tienen que coincidir.
            valor = json.dumps(valor, separators=(",", ":"), ensure_ascii=False)
        elif isinstance(valor, bool):
            valor = "true" if valor else "false"
        partes.append(f"{clave}{valor}")
    crudo = f"{secreto}{''.join(partes)}{secreto}"
    return hashlib.md5(crudo.encode("utf-8")).hexdigest().upper()


async def llamar(tipo: str, datos: dict[str, Any] | None = None,
                 timeout: float = 40.0) -> dict[str, Any]:
    """
    Una llamada a la API. `tipo` es el endpoint (`bg.local.goods.list.query`…).

    Devuelve el `result` ya desenvuelto. Si Temu responde con error, se levanta
    RuntimeError con su código: los códigos son la mejor pista que da esta API
    (`3000000` incluso dice la ruta completa del campo que no le cuadró).
    """
    if not disponible():
        raise RuntimeError("Temu no está configurado (faltan TEMU_APP_KEY / "
                           "TEMU_APP_SECRET / TEMU_ACCESS_TOKEN / TEMU_API_BASE).")
    cuerpo: dict[str, Any] = {
        "type": tipo,
        "app_key": _cfg("temu_app_key"),
        "access_token": _cfg("temu_access_token"),
        "data_type": "JSON",
        "timestamp": str(int(time.time())),
        **(datos or {}),
    }
    cuerpo["sign"] = _firmar(cuerpo)
    async with httpx.AsyncClient(timeout=timeout) as cli:
        r = await cli.post(_cfg("temu_api_base"), json=cuerpo)
    r.raise_for_status()
    d = r.json()
    if not d.get("success", False):
        raise RuntimeError(f"Temu {tipo}: errorCode={d.get('errorCode')} "
                           f"{d.get('errorMsg') or ''}".strip())
    res = d.get("result") or {}
    # ⚠️ `success: true` NO siempre significa éxito. Hay endpoints —`goods.delete`
    # es el caso medido— que contestan bien por fuera y traen el veredicto REAL
    # anidado en `result.success`, con el motivo en `result.errorMsg` ("data
    # under review cannot be deleted"). Quien solo mire el de arriba dará por
    # hecho un cambio que no ocurrió. Solo se levanta si viene explícitamente en
    # falso: la mayoría de los endpoints no traen esa llave.
    if isinstance(res, dict) and res.get("success") is False:
        raise RuntimeError(
            f"Temu {tipo}: la llamada respondió OK pero la operación FALLÓ "
            f"(result.success=false): {res.get('errorMsg') or res.get('errorCode') or ''}")
    return res


async def listar_productos(cubetas: tuple[int, ...] = CUBETAS) -> list[dict[str, Any]]:
    """
    Censo COMPLETO del catálogo en Temu, recorriendo todas las cubetas y páginas.

    Se deduplica por `goodsId`: las cubetas deberían ser disjuntas, pero confiar
    en eso sin comprobarlo es cómo se cuentan productos de más.
    """
    vistos: dict[str, dict[str, Any]] = {}
    for cubeta in cubetas:
        pagina = 1
        while True:
            res = await llamar("bg.local.goods.list.query", {
                "goodsSearchType": int(cubeta),   # ENTERO: como cadena da 3000000
                "pageNo": pagina,                 # `page` se ignora EN SILENCIO
                "pageSize": PAGE_SIZE,
            })
            lote = (res.get("goodsList") or res.get("data")
                    or res.get("list") or [])
            for g in lote:
                gid = str(g.get("goodsId") or g.get("goods_id") or "")
                if gid:
                    g["_cubeta"] = cubeta
                    vistos.setdefault(gid, g)
            if len(lote) < PAGE_SIZE:
                break
            pagina += 1
            if pagina > 50:                       # cortacircuitos
                log.warning("listar_productos: cubeta %s pasó de 50 páginas", cubeta)
                break
    return list(vistos.values())


async def plantilla_categoria(cat_id: str | int) -> dict[str, Any]:
    """
    Atributos y valores de una hoja de categoría, EN ESPAÑOL.

    Solo responde en hojas ("The catId not a leaf category"). `language=es`
    devuelve categorías, atributos y valores ya traducidos — eso ahorra una capa
    entera de traducción que en TikTok sí hubo que hacer.
    """
    return await llamar("bg.local.goods.template.get",
                        {"catId": str(cat_id), "language": "es"})


async def skus_ya_publicados(skus: list[str]) -> dict[str, str]:
    """
    Cuáles de estos SKUs ya existen en Temu → {sku: goodsId}.

    Es el oráculo de idempotencia del alta (`isDuplicate` + `duplicateGoodsId`).
    Se consulta en lotes de 50 y se corre ANTES de cada tanda: un alta repetida
    quema el SKU para siempre.
    """
    salida: dict[str, str] = {}
    for i in range(0, len(skus), 50):
        lote = [s for s in skus[i:i + 50] if s]
        if not lote:
            continue
        res = await llamar("bg.local.goods.out.sn.check", {"outGoodsSnList": lote})
        for fila in (res.get("outGoodsSnCheckResultList")
                     or res.get("resultList") or []):
            sn = str(fila.get("outGoodsSn") or "")
            if sn and fila.get("isDuplicate"):
                salida[sn] = str(fila.get("duplicateGoodsId") or "")
    return salida
