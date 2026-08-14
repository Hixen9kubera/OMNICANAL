"""
cargar_temu_requisitos.py — Qué campos exige Temu, categoría por categoría.

Pieza 3 de las seis que abren un canal. Es lo que alimenta el semáforo del panel
("a este producto le faltan 3 obligatorios") y lo que el generador de contenido
le da a la IA como listas CERRADAS de dónde elegir.

DE DÓNDE SALE: `bg.local.goods.template.get` con `language=es`, que devuelve
atributos **y valores ya en español** — eso ahorra la capa de traducción que en
TikTok sí hubo que hacer. Solo responde en HOJAS ("The catId not a leaf
category"), y las 144 categorías en uso lo son porque son las de productos ya
publicados.

LAS DOS COSAS QUE HAY QUE ENTENDER DE TEMU
------------------------------------------
1. **"Obligatorio" en la doc NO significa que se valide.** El sondeo del 13-ago
   quitó un campo a la vez en 12 altas de prueba: las 12 publicaron. De todo el
   payload, el validador solo hace respetar CINCO cosas (se cargan como filas
   globales, `categoria_id='*'`). Lo demás no truena al publicar — pero un
   producto sin sus atributos cae en "Incompleto" o "Borrador" y no se vende,
   que es donde están hoy 90 de nuestras 160 publicaciones.

2. **DURO vs CONDICIONAL es la distinción que ahorra trabajo.** `showType=0` es
   un atributo que siempre se ve; `showType=1` solo aparece si su padre tomó
   cierto valor. De los "obligatorios" de una hoja, la mayoría son
   condicionales: los duros son 0 a 4, y casi siempre los mismos tres conceptos
   (Material, Fuente de alimentación, Características de la batería). Las tres
   listas traen salida de emergencia ("Sin electricidad", "Sin batería"), así
   que **un producto no eléctrico se cierra con Material + dos "no"** — y el
   catálogo Kubera es casi todo no eléctrico.

   Eso se guarda en `tipo`: `duro` o `condicional`. El generador pregunta los
   duros en la primera vuelta y los condicionales solo si se activaron.

Uso:
    python -m scripts.cargar_temu_requisitos            # dry-run
    python -m scripts.cargar_temu_requisitos --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import supabase_db as sdb, temu  # noqa: E402

CANAL = "temu"

# Los CINCO que el validador de Temu hace respetar de verdad (medidos el 13-ago
# quitando un campo a la vez en 12 altas). El resto de "Required=True" de la doc
# publica igual sin ellos.
GLOBALES = [
    ("goodsBasic.goodsName",        "titulo",   "string", "el título del anuncio"),
    ("goodsBasic.externalGoodsId",  "sku",      "string", "nuestro SKU (outGoodsSn)"),
    ("skuList[].externalSkuId",     "sku",      "string", "el SKU de la variante"),
    ("skuList[].images",            "imagenes", "[]string", "al menos una imagen"),
    ("skuList[].price.basePrice",   "precio_regular", "number",
     "DECIMAL en pesos; v3 ignora en silencio lo que no conoce"),
]


async def _plantilla(cat_id: str, sem: asyncio.Semaphore) -> tuple[str, list[dict[str, Any]]]:
    async with sem:
        try:
            tpl = await temu.plantilla_categoria(cat_id)
        except Exception as exc:  # noqa: BLE001
            print(f"    categoría {cat_id}: {str(exc)[:90]}")
            return cat_id, []
    return cat_id, ((tpl.get("templateInfo") or {}).get("goodsProperties") or [])


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    if not temu.disponible():
        print("Temu no está configurado (faltan las TEMU_* en el entorno).")
        return

    cats = [r["category_id"] for r in sdb.fetch_all(
        """select distinct category_id from channel.listings
            where canal=%(c)s and category_id is not null""", {"c": CANAL})]
    print(f"Categorías en uso: {len(cats)}")
    if not cats:
        print("No hay publicaciones de Temu cargadas — corre antes cargar_temu.")
        return

    sem = asyncio.Semaphore(4)
    resultados = await asyncio.gather(*[_plantilla(c, sem) for c in cats])

    filas: list[tuple] = []
    duros = condicionales = 0
    for cat_id, props in resultados:
        for p in props:
            nombre = (p.get("name") or "").strip()
            if not nombre:
                continue
            es_duro = str(p.get("showType")) == "0"
            obligatorio = bool(p.get("required"))
            if obligatorio:
                duros += 1 if es_duro else 0
                condicionales += 0 if es_duro else 1
            # La lista CERRADA de valores, con su vid: es lo que el validador
            # coteja para que la IA no pueda inventar uno (10 inventados en 89
            # productos durante la prueba del 13-ago).
            valores = [{"vid": v.get("vid"), "valor": v.get("value")}
                       for v in (p.get("values") or []) if v.get("vid")]
            filas.append((
                CANAL, str(cat_id), nombre, None, obligatorio,
                "duro" if es_duro else "condicional",
                json.dumps({"pid": p.get("templatePid"), "valores": valores},
                           ensure_ascii=False) if valores or p.get("templatePid") else None,
                "api",
            ))

    print(f"  atributos leídos: {len(filas)}")
    print(f"  obligatorios DUROS: {duros} · obligatorios CONDICIONALES: {condicionales}")
    print(f"  + {len(GLOBALES)} filas globales (categoria_id='*')")

    if not args.aplicar:
        print("\nDRY-RUN: nada se escribió. Corre con --aplicar.")
        return

    for campo, canonico, tipo, nota in GLOBALES:
        sdb.execute(
            """insert into channel.field_requirements
                 (canal, categoria_id, campo, campo_canonico, obligatorio, tipo,
                  valores_permitidos, fuente, leido_at, updated_at)
               values (%s, '*', %s, %s, true, %s, %s::jsonb, 'api', now(), now())
               on conflict (canal, categoria_id, campo) do update set
                 obligatorio = excluded.obligatorio, tipo = excluded.tipo,
                 valores_permitidos = excluded.valores_permitidos,
                 updated_at = now()""",
            (CANAL, campo, canonico, tipo, json.dumps(nota, ensure_ascii=False)))

    for f in filas:
        sdb.execute(
            """insert into channel.field_requirements
                 (canal, categoria_id, campo, campo_canonico, obligatorio, tipo,
                  valores_permitidos, fuente, leido_at, updated_at)
               values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, now(), now())
               on conflict (canal, categoria_id, campo) do update set
                 obligatorio = excluded.obligatorio, tipo = excluded.tipo,
                 valores_permitidos = excluded.valores_permitidos,
                 updated_at = now()""", f)

    print(f"\nListo: {len(filas) + len(GLOBALES)} reglas de campo para Temu.")


if __name__ == "__main__":
    asyncio.run(main())
