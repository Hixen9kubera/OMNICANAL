"""
f_temu.py — Lo que hay publicado en Temu, EN VIVO: `bg.local.goods.list.query`.

LA TRAMPA DE TEMU ES LA IP. Su Open API solo contesta desde la IP de Railway; desde
una laptop responde `5000003 NOT_IN_IP_WHITE_LIST`. Por eso hay DOS caminos:

  A. DIRECTO — cuando esto corre desde una IP autorizada. Recorre las cubetas del
     listado (son las pestañas del Seller Center) y sus páginas.

  B. POR `/investigacion` — la página del panel de producción que hace la lectura
     desde Railway (solo admin con sesión, solo tipos `…get`/`…query`, respuesta
     redactada). Una persona con sesión corre ahí el mismo tipo, cubeta por cubeta, y
     las respuestas se guardan juntas en `datos/temu_crudo.json`:

         {"leido": "<ISO>", "goods": [ {…goodsList de cada respuesta…} ]}

     Si ese archivo existe, se usa y la fuente lo dice.

Si ninguno de los dos está disponible, Temu se reporta como NO LEÍDO, con el motivo.

LOS ESTADOS DE TEMU NO ESTÁN DOCUMENTADOS: producción los dedujo de los pedidos
(v0.616.0, 2-oct-2026 — de 388 pedidos de 30 días, 363 se hicieron con la publicación
en 2/8 y ninguno en 4/7). Lo verificado es:

    2/8 = a la venta · 3/1 = agotada · 5/None = borrador

Cualquier otro código se muestra CRUDO («Temu 3/3») y cuenta como no vendiendo:
inventarle un nombre sería atar decisiones a una suposición.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from comun import Cfg, Red, ahora_iso, aviso, escribir_json, leer_json, num

CUBETAS = (1, 4, 5, 6)
PAGINA = 100
A_LA_VENTA = {"2/8"}
ESTADOS = {"2/8": "A la venta", "3/1": "Agotada", "5/None": "Borrador"}


def _firmar(secreto: str, params: dict[str, Any]) -> str:
    partes: list[str] = []
    for clave in sorted(params):
        valor = params[clave]
        if isinstance(valor, (dict, list)):
            valor = json.dumps(valor, separators=(",", ":"), ensure_ascii=False)
        elif isinstance(valor, bool):
            valor = "true" if valor else "false"
        partes.append(f"{clave}{valor}")
    return hashlib.md5(f"{secreto}{''.join(partes)}{secreto}".encode("utf-8")).hexdigest().upper()


def _directo(cfg: Cfg) -> list[dict[str, Any]]:
    if not cfg.tiene("TEMU_APP_KEY", "TEMU_APP_SECRET", "TEMU_ACCESS_TOKEN", "TEMU_API_BASE"):
        raise RuntimeError("Temu sin configurar (TEMU_*)")
    red = Red(rps=2.0)
    vistos: dict[str, dict[str, Any]] = {}
    for cubeta in CUBETAS:
        pagina = 1
        while pagina <= 50:
            cuerpo: dict[str, Any] = {
                "type": "bg.local.goods.list.query", "app_key": cfg("TEMU_APP_KEY"),
                "access_token": cfg("TEMU_ACCESS_TOKEN"), "data_type": "JSON",
                "timestamp": str(int(time.time())),
                "goodsSearchType": int(cubeta), "pageNo": pagina, "pageSize": PAGINA}
            cuerpo["sign"] = _firmar(cfg("TEMU_APP_SECRET"), cuerpo)
            j = red.lectura_sin_get(cfg("TEMU_API_BASE"), json=cuerpo).json()
            if not j.get("success", False):
                raise RuntimeError(f"Temu errorCode={j.get('errorCode')} {j.get('errorMsg') or ''}".strip())
            lote = (j.get("result") or {}).get("goodsList") or []
            for g in lote:
                gid = str(g.get("goodsId") or "")
                if gid:
                    g["_cubeta"] = cubeta
                    vistos.setdefault(gid, g)
            if len(lote) < PAGINA:
                break
            pagina += 1
    return list(vistos.values())


def _fila(g: dict[str, Any]) -> dict[str, Any] | None:
    sku = str(g.get("outGoodsSn") or "").strip()
    gid = str(g.get("goodsId") or "")
    if not (sku and gid):
        return None
    estado = f"{g.get('status4VO')}/{g.get('subStatus4VO')}"
    rotulo = ESTADOS.get(estado) or f"Temu {estado}"
    return {
        "sku": sku, "id": gid, "titulo": g.get("goodsName"),
        "imagen": g.get("thumbUrl") if str(g.get("thumbUrl") or "").startswith("https://") else None,
        "precio": num(g.get("price")), "moneda": g.get("currency") or "MXN",
        "estado": rotulo, "a_la_venta": estado in A_LA_VENTA,
        "stock_canal": g.get("quantity"),
        "link": f"https://www.temu.com/goods.html?goods_id={gid}",
    }


def extraer(cfg: Cfg, salida: Path) -> dict[str, Any]:
    inicio = ahora_iso()
    crudo = leer_json(salida / "datos" / "temu_crudo.json")
    if crudo and crudo.get("goods"):
        bienes, via = crudo["goods"], "Temu Open API · bg.local.goods.list.query, vía /investigacion (producción)"
        inicio = crudo.get("leido") or inicio
    else:
        bienes, via = _directo(cfg), "Temu Open API · bg.local.goods.list.query"
    unicos: dict[str, dict[str, Any]] = {}
    for g in bienes:
        gid = str(g.get("goodsId") or "")
        if gid:
            unicos.setdefault(gid, g)
    filas, ilegibles = [], 0
    for g in unicos.values():
        f = _fila(g)
        if f:
            filas.append(f)
        else:
            ilegibles += 1
    doc = {
        "canal": "temu", "cuenta": "Temu", "fuente": via,
        "leido_desde": inicio, "leido_hasta": crudo.get("leido") if crudo else ahora_iso(),
        "publicaciones": len(filas), "a_la_venta": sum(1 for f in filas if f["a_la_venta"]),
        "avisos": ([f"{ilegibles} productos sin SKU o sin id"] if ilegibles else []), "filas": filas,
    }
    escribir_json(salida / "datos" / "temu.json", doc)
    aviso(f"temu: {len(filas)} productos, {doc['a_la_venta']} a la venta")
    return {k: v for k, v in doc.items() if k != "filas"}
