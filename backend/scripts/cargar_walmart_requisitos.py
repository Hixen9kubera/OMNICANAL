"""
cargar_walmart_requisitos.py — Los 3,326 campos de Walmart MX a la base.

Cierra el pendiente #7 de `docs/WALMART_ENTREGA_A_OMNICANAL.md`: pasar el CSV
que produce `walmart_field_requirements.py` a `channel.field_requirements`, que
es de donde el panel pinta el semáforo y de donde el generador saca las listas
cerradas para la IA. Mismo papel que `cargar_temu_requisitos.py`.

LO QUE HACE DISTINTO A WALMART
──────────────────────────────
1. **NO HAY API DE ATRIBUTOS.** En TikTok se piden con
   `GET /categories/{id}/attributes`; en Walmart MX ese endpoint no existe
   (`POST /v3/items/spec` da 404 con credenciales MX y en Global está marcado
   "US only"). La fuente es el ESQUEMA PÚBLICO `MX_MP_ITEM_INTL_SPEC.json`
   (3.9 MB, se descarga sin credenciales). Por eso `fuente='manual'`: no es una
   API que se pueda reconsultar en caliente, es un archivo con versión — y
   llamarlo 'api' invitaría a creer que se refresca solo.

2. **EL ARCHIVO MIENTE EN LOS DOS SENTIDOS.** El esquema publicado es la 3.19 y
   producción corre la 3.11: hay campos que el archivo marca obligatorios y
   producción no exige, y campos que producción RECHAZA aunque el archivo los
   liste. Cada corrección costó un lote. Esa medición viaja en
   `veredicto_produccion` / `evidencia_produccion` y se conserva aquí dentro de
   `valores_permitidos`, porque es lo que distingue este catálogo de una copia
   de la documentación.

3. **`(todas)` se traduce a `'*'`**, que es la convención que ya usan Amazon,
   TikTok y Temu para "aplica a cualquier categoría". Sin esa traducción el
   semáforo no encontraría los 24 campos comunes.

Uso:
    python -m scripts.cargar_walmart_requisitos --csv <ruta>            # dry-run
    python -m scripts.cargar_walmart_requisitos --csv <ruta> --aplicar
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psycopg2.extras import execute_values  # noqa: E402

from services import supabase_db as sdb  # noqa: E402

CANAL = "walmart"

# Campo de Walmart → campo canónico del panel. Solo los que existen en
# `core.canonical_fields` (hay una llave foránea): lo demás va en NULL, que es
# lo correcto para un atributo propio del canal.
CANONICO = {
    "productName": "titulo",
    "shortDescription": "descripcion",
    "longDescription": "descripcion",
    "brand": "brand",
    "sku": "sku",
    "price": "precio_regular",
    "mainImageUrl": "imagenes",
    "productSecondaryImageURL": "imagenes",
    "keyFeatures": "bullets",
    "shippingWeight": "peso",
    "productLength": "largo",
    "productWidth": "ancho",
    "productHeight": "alto",
    "category": "categoria_id",
}


def _bool(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "1", "si", "sí", "yes", "x")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    with open(args.csv, encoding="utf-8-sig", newline="") as fh:
        filas = list(csv.DictReader(fh))
    print(f"CSV: {len(filas)} filas")

    vistos: set[tuple[str, str]] = set()
    salida: list[tuple] = []
    obligatorios = rechazados = corregidos = 0
    for f in filas:
        cat = (f.get("categoria") or "").strip()
        cat_id = "*" if cat in ("(todas)", "", "todas") else cat
        campo = (f.get("campo") or "").strip()
        if not campo:
            continue
        clave = (cat_id, campo)
        if clave in vistos:      # la PK es (canal, categoria_id, campo)
            continue
        vistos.add(clave)

        obligatorio = _bool(f.get("obligatorio"))
        veredicto = (f.get("veredicto_produccion") or "").strip()
        if obligatorio:
            obligatorios += 1
        if veredicto == "RECHAZADO":
            rechazados += 1
        if veredicto and veredicto != "OK":
            corregidos += 1

        # TODO lo que no cabe en columnas propias viaja aquí: es lo que la IA
        # necesita para elegir de una lista cerrada y lo que un humano necesita
        # para entender por qué un campo obligatorio del archivo no se manda.
        extra = {
            "etiqueta_es": f.get("etiqueta_es") or None,
            "bloque": f.get("bloque") or None,
            "lista_cerrada": f.get("lista_cerrada") or None,
            "n_valores": f.get("n_valores") or None,
            "min": f.get("min") or None,
            "max": f.get("max") or None,
            "ejemplos": f.get("ejemplos") or None,
            "descripcion": f.get("descripcion") or None,
            "obligatorio_segun_archivo": _bool(f.get("obligatorio_segun_archivo")),
            "veredicto_produccion": veredicto or None,
            "evidencia_produccion": f.get("evidencia_produccion") or None,
            "exencion_upc": f.get("exencion_upc") or None,
        }
        salida.append((
            CANAL, cat_id, campo, CANONICO.get(campo), obligatorio,
            (f.get("tipo") or "").strip() or None,
            json.dumps({k: v for k, v in extra.items() if v not in (None, "")},
                       ensure_ascii=False),
            "manual",
        ))

    # ── LAS CORRECCIONES QUE EL CSV NO ALCANZA A LLEVAR ─────────────────────
    # `CORRECCIONES_MEDIDAS` tiene 9 renglones y solo 4 salen en el CSV: los
    # otros 5 son campos que el esquema 3.19 NO lista para esa categoría, así
    # que no hay fila a la que colgarles el veredicto. Y son justo los más
    # caros: cuatro son RECHAZADO —"85 de 85 muertos", "83 de 83 muertos"— o
    # sea, campos que MATAN el lote entero si se mandan.
    #
    # Dejarlos fuera sería perder lo único que no se puede deducir del archivo.
    # Se emiten como filas propias, con `obligatorio=false` cuando el veredicto
    # es RECHAZADO: para el semáforo no falta nada, y quien lea la fila ve por
    # qué ese campo no se manda.
    from scripts.walmart_field_requirements import CORRECCIONES_MEDIDAS
    rescatadas = 0
    for (cat, campo), (veredicto, evidencia) in CORRECCIONES_MEDIDAS.items():
        if (cat, campo) in vistos:
            continue
        vistos.add((cat, campo))
        rescatadas += 1
        if veredicto == "OBLIGATORIO":
            obligatorios += 1
        if veredicto == "RECHAZADO":
            rechazados += 1
        salida.append((
            CANAL, cat, campo, CANONICO.get(campo),
            veredicto == "OBLIGATORIO", None,
            json.dumps({"veredicto_produccion": veredicto,
                        "evidencia_produccion": evidencia,
                        "solo_medicion": True,
                        "nota": ("El esquema publicado NO lista este campo para "
                                 "esta categoría; la fila existe porque "
                                 "producción lo midió.")},
                       ensure_ascii=False),
            "manual"))
    if rescatadas:
        print(f"  + {rescatadas} correcciones medidas que el esquema no lista "
              f"(y que el CSV por eso no traía)")

    print(f"  a escribir: {len(salida)} (sin duplicados de la PK)")
    print(f"  obligatorios: {obligatorios}")
    print(f"  con veredicto de producción distinto del archivo: {corregidos}"
          f" · de esos, RECHAZADOS: {rechazados}")
    cats = {s[1] for s in salida}
    print(f"  categorías: {len(cats)} (incluye '*' para las comunes)")

    if not args.aplicar:
        print("\nDRY-RUN: nada se escribió.")
        return

    with sdb.get_cursor() as cur:
        execute_values(
            cur,
            """insert into channel.field_requirements
                 (canal, categoria_id, campo, campo_canonico, obligatorio, tipo,
                  valores_permitidos, fuente, leido_at, updated_at)
               values %s
               on conflict (canal, categoria_id, campo) do update set
                 campo_canonico = excluded.campo_canonico,
                 obligatorio = excluded.obligatorio,
                 tipo = excluded.tipo,
                 valores_permitidos = excluded.valores_permitidos,
                 fuente = excluded.fuente,
                 updated_at = now()""",
            salida,
            template="(%s, %s, %s, %s, %s, %s, %s::jsonb, %s, now(), now())",
            page_size=500)
    print(f"\nListo: {len(salida)} reglas de campo para Walmart.")


if __name__ == "__main__":
    main()
