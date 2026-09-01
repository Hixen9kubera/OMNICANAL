"""
competencia_highlights.py — Sondea GRATIS qué categorías tienen ranking en ML.

Una llamada a `GET /highlights/MLM/category/{id}` por categoría activa. Sin
Apify, sin costo. Medido: **1,129 categorías en 2.5 minutos** con 8 en paralelo.

QUÉ RESUELVE
------------
Mercado Libre **no publica lista de más vendidos de todas las categorías**, y
raspar una que no la tiene cuesta lo mismo que raspar una que sí y devuelve nada.
Este sondeo lo dice antes de pagar. Medido el 1-sep-2026 sobre las 60 de más
venta: 52 con ranking y **8 sin él** — entre ellas Bombas de Agua y Pistolas para
Limpieza Textil, dos de las que más venden y que nunca se capturaron. No era
descuido: no hay qué capturar.

Y de paso deja el DETECTOR DE CAMBIO: la huella del top-10. Cuando cambia, el
ranking se movió, y eso es la señal para recapturar por evento en vez de por
calendario — averiguado sin gastar un peso.

DOS DISTINCIONES QUE NO SE PUEDEN PERDER
----------------------------------------
1. `capturado_en` es el último INTENTO y siempre avanza; `cambio_en` sólo se
   mueve cuando la huella cambió de verdad. Confundirlas es cómo se construye un
   medidor que miente en verde.

2. **Si la llamada FALLA, no se escribe la fila.** `n = 0` significa "ML dice que
   no hay ranking", nunca "no pude preguntar". Un cero que en realidad es un
   error se lee como "no insistas" y manda a no capturar justo donde hay dinero.

CADENCIA
--------
Diaria, junto al refresco de visitas (servicio `competencia-visitas`, 12:00 UTC).
El ranking de ML no se mueve más rápido que eso.

USO
---
    python scripts/competencia_highlights.py            # DRY-RUN: sondea y no escribe
    python scripts/competencia_highlights.py --real     # escribe
    python scripts/competencia_highlights.py --real --limite 50
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from services import competencia_ml, supabase_db  # noqa: E402

CANAL = "mercado_libre"
CONCURRENCIA = 8      # medido: 134 ms por categoría, 2.5 min las 1,129
TOPE_HUELLA = 10      # la huella mira el top 10: abajo el orden es ruido


def _huella(entradas: list[dict[str, Any]]) -> str | None:
    """sha1 corto de los ids del top 10, en orden. None si no hay ranking."""
    if not entradas:
        return None
    ids = [e["id"] for e in sorted(entradas, key=lambda x: x.get("p") or 99)[:TOPE_HUELLA]]
    return hashlib.sha1("|".join(ids).encode()).hexdigest()[:16]


def objetivo(limite: int | None = None) -> list[str]:
    """
    Las categorías ACTIVAS, las de más dinero primero — y las RAÍCES antes que
    todas.

    ⚠️ LAS RAÍCES VAN APARTE. `market_categoria_prioridad_v` es por
    SUBCATEGORÍA: nunca devuelve una raíz. Este sondeo leía sólo de ahí, así que
    **jamás sondeó una raíz** — y la raíz es lo primero que se ve al abrir el
    tab. Medido el 1-sep-2026: 1,133 subcategorías sondeadas y las 27 raíces en
    blanco, o sea que de ellas no se podía saber ni si ML publica ranking ni si
    el top se había movido.

    Es el mismo punto ciego que tenía `competencia_barrido.py`, y por la misma
    razón: los dos arman su lista desde esa vista. Aquí ni siquiera había excusa
    de costo — `/highlights` es gratis.

    Las raíces van primero por ser pocas y por ser la portada, no por dinero: una
    raíz agrega a todas sus hojas y ordenarla por pesos la pondría siempre arriba
    sin que eso signifique nada.
    """
    sql = ("""
        with raices as (
          select distinct raiz_id as categoria_id
            from enrich.market_skus_v where raiz_id is not null
        ),
        hojas as (
          select categoria_id, pesos_30d, unidades_30d
            from enrich.market_categoria_prioridad_v
           where categoria_id not in (select categoria_id from raices)
        )
        select categoria_id from (
          select categoria_id, 0 as orden, null::numeric as pesos, null::int as unid
            from raices
          union all
          select categoria_id, 1, pesos_30d, unidades_30d from hojas
        ) x
        order by orden, pesos desc nulls last, unid desc nulls last
    """)
    if limite:
        sql += f" limit {int(limite)}"
    return [f["categoria_id"] for f in supabase_db.fetch_all(sql)]


async def sondear(cats: list[str]) -> dict[str, list[dict[str, Any]] | None]:
    """{categoria_id: entradas} — None cuando la llamada falló (≠ sin ranking)."""
    sem = asyncio.Semaphore(CONCURRENCIA)
    out: dict[str, list[dict[str, Any]] | None] = {}

    async def una(cid: str) -> None:
        async with sem:
            try:
                top = await asyncio.to_thread(competencia_ml.mas_vendidos_categoria, cid)
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning("/highlights de %s falló: %s", cid, exc)
                out[cid] = None
                return
        out[cid] = [{"p": e.get("posicion"), "id": e.get("id"),
                     "t": (e.get("tipo") or "")[:1]}
                    for e in (top or []) if e.get("id")]

    await asyncio.gather(*(una(c) for c in cats))
    return out


def guardar(res: dict[str, list[dict[str, Any]] | None]) -> tuple[int, int]:
    """Upsert. Devuelve (filas escritas, cuántas cambiaron de huella)."""
    escritas = cambios = 0
    with supabase_db.get_cursor() as cur:
        for cid, entradas in res.items():
            if entradas is None:      # la llamada falló: NO se escribe (ver docstring)
                continue
            h = _huella(entradas)
            cur.execute(
                """insert into enrich.market_highlights
                       (canal, categoria_id, entradas, n, huella, capturado_en, cambio_en)
                   values (%s, %s, %s::jsonb, %s, %s, now(), null)
                   on conflict (canal, categoria_id) do update set
                       entradas     = excluded.entradas,
                       n            = excluded.n,
                       huella       = excluded.huella,
                       capturado_en = now(),
                       cambio_en    = case
                           when enrich.market_highlights.huella
                                is distinct from excluded.huella then now()
                           else enrich.market_highlights.cambio_en end
                   returning (xmax <> 0) as actualizada,
                             (cambio_en >= now() - interval '1 minute') as cambio""",
                (CANAL, cid, json.dumps(entradas, separators=(",", ":")),
                 len(entradas), h))
            fila = cur.fetchone()
            escritas += 1
            if fila and fila.get("cambio"):
                cambios += 1
    return escritas, cambios


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="Escribe. Sin esto, sólo sondea.")
    ap.add_argument("--limite", type=int, default=0)
    args = ap.parse_args()

    print("═══ Competencia · sondeo de /highlights ═══")
    if not supabase_db.disponible():
        print("ERROR: sin SUPABASE_DB_URL.")
        return 2

    cats = objetivo(args.limite or None)
    if not cats:
        print("No hay categorías activas. ¿Corrió la vista de prioridad?")
        return 0
    print(f"  categorías a sondear : {len(cats)}")
    print(f"  concurrencia         : {CONCURRENCIA}")

    t0 = time.time()
    res = asyncio.run(sondear(cats))
    dt = time.time() - t0

    con = sum(1 for v in res.values() if v)
    sin = sum(1 for v in res.values() if v == [])
    err = sum(1 for v in res.values() if v is None)
    print(f"\n  sondeadas en {dt/60:.1f} min")
    print(f"    con ranking : {con}")
    print(f"    SIN ranking : {sin}   (ML no publica: raspar ahí es tirar dinero)")
    print(f"    error       : {err}   (no se escriben: 'no sé' ≠ 'no hay')")

    if not args.real:
        print("\n--dry-run: no se escribió nada. Corre con --real para guardar.")
        return 0

    escritas, cambios = guardar(res)
    print(f"\n  escritas: {escritas} · con el top movido desde la vez pasada: {cambios}")

    if err > len(cats) / 4:
        print(f"\nERROR: {err} de {len(cats)} fallaron. No es una corrida sana.")
        return 1
    print("\nListo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
