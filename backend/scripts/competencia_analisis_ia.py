"""Lee el análisis de oportunidad y le pide a DeepSeek el juicio que falta.

QUÉ APORTA LA IA Y QUÉ NO
-------------------------
Los números salen de `competencia_analisis.py` y se pueden auditar sin LLM. Lo
que la IA agrega es lo que un humano tendría que leer producto por producto:

  1. **¿El término general describe NUESTRO producto?** Es la pregunta más
     importante y no se puede contestar con SQL. Medido: el promedio de "estamos
     127% arriba del precio de nuestro término" está CONTAMINADO por términos mal
     asignados. `TEC-0264-NAR` es un trolley MANUAL de $42,447 y su término dice
     "polipasto eléctrico", que trae polipastos de $1,700: no estamos caros, nos
     estamos comparando contra otro producto. `TEC-1841-ROS` es una máquina de
     garra arcade y su término trae PELUCHES de $39. Recomendar "baja el precio"
     con esa base sería un error caro.

  2. **Qué palabras del mercado le faltan al título.** Se le dan los títulos de
     los más vendidos de su subcategoría y las keywords que ML publica de esa
     categoría; el hueco entre eso y nuestro título es lo accionable.

  3. **Qué hacer**, en una de cuatro acciones concretas y excluyentes, para que
     el reporte se pueda ejecutar y no solo leer.

La IA NO decide precios ni reactiva nada: propone, y queda por escrito quién lo
propuso.

USO
---
    backend/.venv/bin/python backend/scripts/competencia_analisis_ia.py \\
        --entrada analisis.json --salida reporte.md [--limite 30]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import ia_generadores, supabase_db as sdb  # noqa: E402

LOTE = 6

SYSTEM = """Eres analista de Mercado Libre México. Recibes productos de un
catálogo y su contexto de mercado REAL (líder de la subcategoría, mediana de
precio, keywords que ML publica, resultados de la búsqueda del término).

Para CADA producto devuelve un objeto con:
  sku
  termino_ok      true|false  — ¿el término describe ESTE producto? Si el término
                  trae otra clase de producto (accesorio vs máquina, manual vs
                  eléctrico, juguete vs profesional), es false.
  termino_mejor   string|null — término general corregido, 2 a 4 palabras, como
                  lo escribiría un comprador. null si termino_ok es true.
  keywords_falta  [string]    — hasta 4 palabras del mercado que NO están en
                  nuestro título y sí en el de los más vendidos o en las keywords
                  de la categoría. Vacío si no falta ninguna.
  accion          una de: "reactivar" | "corregir_termino" | "bajar_precio" |
                  "mejorar_titulo" | "ninguna"
  por_que         una frase corta, concreta, citando el número que la justifica.

REGLAS DURAS:
- Si el producto está pausado y tiene visitas o ventas, la acción es
  "reactivar": ningún cambio de precio o título sirve si nadie lo puede comprar.
- Si termino_ok es false, la acción es "corregir_termino" y NO recomiendes bajar
  precio: la comparación de precio contra ese término no vale.
- Solo recomienda "bajar_precio" si el término es correcto Y estamos arriba de la
  mediana del mercado.
- No inventes datos. Si algo no se puede saber con lo dado, dilo en por_que.

Responde SOLO un JSON: {"productos": [ ... ]}"""


def contexto(sdb_, r: dict) -> str:
    cid = r.get("categoria_id")
    tops = sdb_.fetch_all(
        "select posicion, titulo, precio, vendidos, visitas_30d "
        "  from enrich.market_bestsellers where categoria_id=%s "
        " order by posicion limit 5", (cid,))
    kws = sdb_.fetch_one(
        "select terminos from enrich.market_terms where categoria_id=%s", (cid,))
    serp = sdb_.fetch_all(
        "select r.posicion, r.titulo, r.precio from enrich.market_search_results r "
        "  join enrich.market_search_term st on st.id=r.termino_id "
        " where st.termino=%s order by r.posicion limit 4",
        (r.get("termino_general"),)) if r.get("termino_general") else []

    p = [f"SKU {r['sku']} · {r.get('nombre') or ''}",
         f"  título publicado: {r.get('title') or '—'}",
         f"  estado: {r.get('estado') or '—'} · precio ${r.get('precio') or 0:.0f}"
         f" · visitas 30d {r.get('visitas') or 0} · unidades 30d {r.get('unidades') or 0}",
         f"  subcategoría: {r.get('categoria_nombre')} · mediana del top "
         f"${r.get('mediana') or 0:.0f}",
         f"  término actual: {r.get('termino_general') or '(sin término)'}"]
    if tops:
        p.append("  MÁS VENDIDOS de la subcategoría:")
        for t in tops:
            p.append(f"    #{t['posicion']} ${t['precio'] or 0:.0f} "
                     f"({t['vendidos'] or 0} vend, {t['visitas_30d'] or 0} vis) "
                     f"{(t['titulo'] or '')[:70]}")
    if kws and kws.get("terminos"):
        p.append("  keywords que ML publica: " + ", ".join(list(kws["terminos"])[:10]))
    if serp:
        p.append(f"  lo que trae su término «{r.get('termino_general')}»:")
        for s in serp:
            p.append(f"    #{s['posicion']} ${s['precio'] or 0:.0f} {(s['titulo'] or '')[:60]}")
    return "\n".join(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--limite", type=int, default=30)
    args = ap.parse_args()

    datos = json.loads(Path(args.entrada).read_text())
    top = datos["top"][:args.limite]
    print(f"Analizando {len(top)} productos con DeepSeek…", flush=True)

    juicios: dict[str, dict] = {}
    for i in range(0, len(top), LOTE):
        trozo = top[i:i + LOTE]
        user = ("Analiza estos productos:\n\n"
                + "\n\n".join(contexto(sdb, r) for r in trozo))
        try:
            # `_completar` devuelve {ok, texto} con el texto CRUDO del modelo, no
            # el JSON ya parseado; `_parse_json` es el que quita el ```json y lo
            # convierte. Saltarse ese paso hace que todo se lea como vacío sin un
            # solo error, que es exactamente lo que pasó la primera vez.
            r = ia_generadores._completar(SYSTEM, user, max_tokens=2200)
            if not r.get("ok"):
                print(f"  ! lote {i//LOTE+1}: {r.get('motivo')}", flush=True)
                continue
            data = ia_generadores._parse_json(r.get("texto") or "")
            for p in (data.get("productos") or []):
                if p.get("sku"):
                    juicios[p["sku"]] = p
        except Exception as exc:  # noqa: BLE001
            print(f"  ! lote {i//LOTE+1}: {type(exc).__name__}: {exc}", flush=True)
            continue
        print(f"  lote {i//LOTE+1}/{-(-len(top)//LOTE)}: {len(juicios)} juicios",
              flush=True)

    Path(args.salida).write_text(json.dumps(juicios, ensure_ascii=False, indent=1))
    print(f"\n{len(juicios)} juicios → {args.salida}")

    from collections import Counter
    print("\nacciones propuestas:", dict(Counter(
        j.get("accion") for j in juicios.values())))
    malos = [s for s, j in juicios.items() if j.get("termino_ok") is False]
    print(f"términos MAL asignados: {len(malos)} de {len(juicios)}")
    for s in malos[:8]:
        print(f"   {s:<20} → propone: {juicios[s].get('termino_mejor')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
