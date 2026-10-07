"""
empate_ia.py — Lo que ni la foto ni la cantidad pudieron empatar, se lo pregunta a la IA. Por contenedor.

En los contenedores que bodega no ha validado quedan dos listas sueltas:
  · renglones del packing list sin SKU (nombre del proveedor en inglés o chino, y piezas);
  · SKUs que Odoo o la base de costos mandan a ESE contenedor y no tienen renglón.

Dentro de un mismo contenedor son pocas decenas contra pocos cientos, y casi siempre
el SKU es uno de esos renglones con el nombre traducido. Se le dan las dos listas a
DeepSeek y se le pide que empareje SOLO lo que esté seguro. Lo recibido en Odoo va
como pista (si coincide con las piezas del renglón, mejor).

Solo se acepta la confianza «alta». Queda marcado como `ia`: es una inferencia por
nombre, sin foto que la respalde, y la página lo dice.

Salida: `datos/empate_ia.json` — lo lee `inventario_pl.py` en su siguiente corrida.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from comun import Cfg, ahora_iso, aviso, escribir_json, leer_json, sku_norm
from ia_titulos import DeepSeek

RE_NUM = re.compile(r"cont(?:enedor)?\.?\s*#?\s*(\d{1,3})\b", re.I)
MAX_RENGLONES = 700

SISTEMA = """Eres experto en comercio exterior e inventarios de un importador mexicano.
Recibes, de UN contenedor:
  RENGLONES: líneas del packing list del proveedor que nadie ha identificado. Formato: número | nombre (inglés o chino) | piezas.
  PRODUCTOS: artículos de nuestro catálogo que llegaron en ese contenedor y no tienen renglón. Formato: SKU | nombre en español | piezas recibidas (si se sabe).

Empareja cada PRODUCTO con el RENGLÓN que es ESE MISMO artículo físico.

REGLAS:
- Empareja solo cuando el nombre del renglón describe el mismo artículo que el producto. Los nombres chinos son más precisos que los ingleses.
- Las piezas son una pista fuerte: si lo recibido coincide con las piezas del renglón (o es un múltiplo evidente), sube la confianza; si son muy distintas, bájala.
- Varios SKUs pueden apuntar al MISMO renglón solo cuando son variantes (color, talla) de un artículo y el renglón no distingue la variante.
- Si un producto no tiene un renglón claro, NO lo incluyas. Es mejor dejarlo sin empate que inventar uno.
- "confianza": "alta" si nombre y cantidad coinciden o el nombre es inequívoco; "media" si el nombre coincide pero hay otro renglón parecido; "baja" en cualquier otro caso.

Respondes SOLO JSON: {"pares": [{"sku": "...", "renglon": <número>, "confianza": "alta|media|baja"}]}"""


def emparejar(cfg: Cfg, salida: Path, limite: int = 0) -> dict[str, Any]:
    d = salida / "datos"
    inv = leer_json(d / "inventario_pl.json")
    odoo = leer_json(d / "odoo.json") or {"filas": []}
    mov = leer_json(d / "movimientos.json") or {"skus": {}, "compras": {}}
    titulos = (leer_json(d / "titulos.json") or {}).get("skus") or {}
    if not inv:
        raise RuntimeError("falta datos/inventario_pl.json: corre antes la etapa `inventario`")
    nombre = {sku_norm(f["sku"]): f["nombre"] for f in odoo["filas"]}
    cont_odoo: dict[str, int] = {}
    for f in odoo["filas"]:
        m = RE_NUM.search(f.get("contenedor") or "")
        if m:
            cont_odoo[sku_norm(f["sku"])] = int(m.group(1))
    recibido = {s: sum(x["recibido"] for x in lin) for s, lin in (mov.get("compras") or {}).items()}

    sueltos: dict[str, list[str]] = defaultdict(list)          # contenedor → SKUs sin renglón
    for sku, x in inv["skus"].items():
        if "pl" in x:
            continue
        n = x.get("est_c") or cont_odoo.get(sku)
        if n:
            sueltos[str(n)].append(sku)
    renglones: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in inv["renglones_sin_sku"]:
        if r.get("o") == "o":
            renglones[r["c"]].append(r)
    tareas = [(c, sueltos[c], renglones[c]) for c in sorted(sueltos) if renglones.get(c)]
    if limite:
        tareas = tareas[:limite]
    aviso(f"empate IA: {len(tareas)} contenedores con SKUs y renglones sueltos · "
          f"{sum(len(t[1]) for t in tareas)} SKUs · {sum(len(t[2]) for t in tareas)} renglones")
    ia = DeepSeek(cfg)
    resultado: dict[str, list[dict[str, Any]]] = {}

    def uno(tarea: tuple[str, list[str], list[dict[str, Any]]]) -> None:
        c, skus, filas = tarea
        filas = sorted(filas, key=lambda r: -r["pz"])[:MAX_RENGLONES]
        lineas = "\n".join(f"{i + 1} | {r['t'][:70]} | {r['pz']}" for i, r in enumerate(filas))
        pares: list[dict[str, Any]] = []
        for k in range(0, len(skus), 60):
            trozo = skus[k:k + 60]
            productos = "\n".join(
                f"{s} | {(titulos.get(s) or {}).get('t') or nombre.get(s, '')} ({nombre.get(s, '')[:50]}) | "
                f"{int(recibido[s]) if recibido.get(s) else '?'}" for s in trozo)
            usuario = f"CONTENEDOR {c}\n\nRENGLONES ({len(filas)})\n{lineas}\n\nPRODUCTOS ({len(trozo)})\n{productos}"
            try:
                res = ia.json(SISTEMA, usuario, max_tokens=200 + 40 * len(trozo), temperatura=0)
            except Exception as exc:  # noqa: BLE001
                aviso(f"empate IA: contenedor {c} falló ({str(exc)[:80]})")
                continue
            for p in res.get("pares") or []:
                try:
                    sku, i = sku_norm(p.get("sku")), int(p.get("renglon")) - 1
                except (TypeError, ValueError):
                    continue
                if sku in trozo and 0 <= i < len(filas):
                    pares.append({"sku": sku, "a": filas[i]["a"], "f": filas[i]["f"], "pz": filas[i]["pz"],
                                  "t": filas[i]["t"][:70], "conf": str(p.get("confianza") or "").lower()})
        resultado[c] = pares

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(uno, tareas))
    doc = {"generado": ahora_iso(), "modelo": "deepseek-flash", "uso": ia.uso, "costo_usd": ia.costo_usd(),
           "contenedores": resultado}
    escribir_json(d / "empate_ia.json", doc)
    conf: dict[str, int] = defaultdict(int)
    for pares in resultado.values():
        for p in pares:
            conf[p["conf"]] += 1
    aviso(f"empate IA: {dict(conf)} · ≈ ${ia.costo_usd():.3f} USD")
    return {"contenedores": len(resultado), "pares": dict(conf), "costo_usd": ia.costo_usd(),
            "muestra": [json.dumps(p, ensure_ascii=False) for pares in list(resultado.values())[:3] for p in pares[:3]]}
