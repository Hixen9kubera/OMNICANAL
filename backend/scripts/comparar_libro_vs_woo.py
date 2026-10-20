#!/usr/bin/env python
"""
comparar_libro_vs_woo.py — La comparación de T0 de la ruta directa: el número
que el fan-out repartiría con `FANOUT_FUENTE=kubera` (el vendible del libro,
`services/stock_libro.py`) contra el que reparte hoy (el `_stock` de Woo).

POR QUÉ EXISTE
--------------
En T0 Woo sale del camino del stock en un solo acto. Antes de voltear la
bandera, cada SKU en que los dos números no coinciden tiene que tener una
razón: una venta DROP que el libro ya apartó, una bodega que no cuenta, un
ajuste del conteo. Una diferencia sin razón es un número que el fan-out va a
publicar en los canales sin que nadie lo haya visto.

SOLO LECTURA
------------
Cada consulta a Supabase corre en su propia transacción `set transaction read
only` que termina en ROLLBACK, por una conexión propia (no el pool de la app;
nunca `set_session(readonly=True)`: se pega en el pooler). Woo se lee sólo con
`--woo vivo`, con el mismo SELECT de `stock_watch` (`_leer_woo(estricto=True)`).

CATEGORÍAS
----------
  igual        los dos números coinciden.
  distinto     los dos tienen número y no coinciden.
  solo_libro   el libro da número y Woo no (sin «gestionar inventario» o sin el
               SKU): desde T0 el fan-out empieza a repartir donde hoy no toca.
  solo_woo     Woo tiene piezas y el libro no conoce el SKU: desde T0 el fan-out
               lo pone en 0 (D4: lo que el libro no trae vale 0, salvo lista
               aprobada; lo demás con piezas entra en `por_verificar`).
  (Woo en 0 y el libro sin el SKU no es diferencia: ninguno ofrece nada.)

Con `--woo foto` justo después del desprendimiento, «distinto» + «solo_libro» +
«solo_woo» son EXACTAMENTE los cambios de la primera pasada de `stock_watch` (la que
mide el libro contra el último `stock_woo`), y su conteo es lo que el acta compara
con el tope de 300.

SALIDA
------
  0  sin diferencias que cuenten, o todas en `--explicados`.
  1  hay diferencias sin explicar (se listan las mayores).
  2  el libro no se puede leer (`LibroNoDisponible`) o falta la lectura de Woo.
Por omisión sólo CUENTAN las de SKUs con alguna publicación DROP (no FULL) en
`channel.listings` de los canales del fan-out (`--canales`, por omisión
mercado_libre, tiktok y temu): es ahí donde el número llega a un canal. `--todos` cuenta
todas. `--salida` escribe TODAS las diferencias a un CSV (fuera del repo: el
repo es público).

USO
---
  SUPABASE_DB_URL=... FANOUT_FUENTE=kubera STOCK_LIBRO_DESDE=2026-10-22T15:00:00Z \\
    python scripts/comparar_libro_vs_woo.py --woo foto
      [--explicados explicados.csv] [--salida diferencias.csv] [--todos]
  `--explicados`: CSV con encabezado `sku,motivo` y, opcional, `woo_menos_libro`.
  Con esa columna la diferencia sólo cuenta como explicada si es EXACTAMENTE ese
  número (Woo − libro): una razón de +14 no tapa una diferencia de +20. Sin ella,
  basta el SKU. «Explicada» no quiere decir «vendible»: sólo que tiene razón.
  `--woo foto` compara contra `ops.stock_watch_photo.stock_woo` y dice de cuándo
  es: sólo sirve si la pasada de Woo de `stock_watch` está al día.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from config import settings  # noqa: E402
from services import stock_libro  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

_SQL_FOTO_WOO = """
    select upper(sku::text) as sku, stock_woo, actualizado from ops.stock_watch_photo"""

# Los canales que escribe el fan-out. `channel.listings` también trae `general` (el
# espejo del DROP de Woo, una fila por SKU) y Amazon, que el fan-out no toca: con ellos
# todo SKU parecería «publicado».
CANALES_FANOUT = ["mercado_libre", "tiktok", "temu"]

_SQL_PUBLICADAS = """
    select upper(sku::text) as sku, count(*) as n, max(stock_own) as canal
      from channel.listings
     where canal = any(%(c)s) and not coalesce(is_fulfillment, false)
       and listing_id is not null and sku is not null
     group by 1"""


def _solo_lectura(dsn: str) -> None:
    """Desvía `supabase_db.fetch_all/fetch_one` (los que usa `stock_libro`) a una
    conexión propia, cada consulta en `set transaction read only` + ROLLBACK."""
    conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def fetch_all(sql: str, params: Any = None) -> list[dict[str, Any]]:
        try:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.rollback()

    def fetch_one(sql: str, params: Any = None) -> dict[str, Any] | None:
        filas = fetch_all(sql, params)
        return filas[0] if filas else None

    sdb.fetch_all = fetch_all  # type: ignore[assignment]
    sdb.fetch_one = fetch_one  # type: ignore[assignment]


def _woo(modo: str) -> tuple[dict[str, int | None], str]:
    if modo == "vivo":
        from services import stock_watch
        return ({s.upper(): w["stock"] for s, w in stock_watch._leer_woo(estricto=True).items()},
                f"Woo en vivo ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)")
    filas = sdb.fetch_all(_SQL_FOTO_WOO)
    ult = max((r["actualizado"] for r in filas if r["actualizado"]), default=None)
    return ({r["sku"]: r["stock_woo"] for r in filas},
            f"foto de Woo (ops.stock_watch_photo; `actualizado` = última pasada de stock_watch "
            f"de CUALQUIER fuente: {ult:%Y-%m-%d %H:%M} UTC)"
            if ult else "foto de Woo (vacía)")


def clasificar(libro: dict[str, dict[str, int | None]], woo: dict[str, int | None]
               ) -> list[dict[str, Any]]:
    """Una fila por SKU con diferencia (todas las categorías menos `igual`)."""
    filas: list[dict[str, Any]] = []
    for sku in sorted(set(libro) | set(woo)):
        d = libro.get(sku) or {"libre": None, "pendientes": 0}
        n = None if d["libre"] is None else max(0, d["libre"] - d["pendientes"])
        w = woo.get(sku)
        if n is not None and w is not None:
            cat = "igual" if n == w else "distinto"
        elif n is not None:
            cat = "solo_libro"
        elif w is not None and w > 0:
            cat = "solo_woo"
        else:
            continue
        if cat != "igual":
            filas.append({"sku": sku, "categoria": cat, "woo": w, "libro": n,
                          "libre": d["libre"], "pendientes": d["pendientes"],
                          "delta": (n or 0) - (w or 0)})
    return filas


def leer_explicados(filas) -> dict[str, dict[str, Any]]:
    """{SKU: {"motivo": str, "woo_menos_libro": int | None}} del CSV `--explicados`."""
    salida: dict[str, dict[str, Any]] = {}
    for r in filas:
        sku = (r.get("sku") or "").strip().upper()
        if not sku:
            continue
        n = (r.get("woo_menos_libro") or "").strip()
        salida[sku] = {"motivo": (r.get("motivo") or "").strip() or "explicado",
                       "woo_menos_libro": int(n) if n else None}
    return salida


def explicacion(dif: dict[str, Any], exp: dict[str, Any] | None) -> str:
    """El motivo si la diferencia está explicada; «» si no. Con `woo_menos_libro`,
    sólo si la diferencia observada es justo esa."""
    if not exp:
        return ""
    esperado = exp.get("woo_menos_libro")
    if esperado is not None and (dif["woo"] or 0) - (dif["libro"] or 0) != esperado:
        return ""
    return exp["motivo"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--woo", choices=("foto", "vivo"), default="foto")
    ap.add_argument("--explicados", type=Path)
    ap.add_argument("--salida", type=Path)
    ap.add_argument("--todos", action="store_true")
    ap.add_argument("--muestra", type=int, default=20)
    ap.add_argument("--canales", default=",".join(CANALES_FANOUT))
    a = ap.parse_args(argv)

    if not settings.supabase_db_url:
        print("falta SUPABASE_DB_URL", file=sys.stderr)
        return 2
    _solo_lectura(settings.supabase_db_url)
    try:
        libro = stock_libro.desglose()
    except stock_libro.LibroNoDisponible as exc:
        print(f"EL LIBRO NO SE PUEDE LEER: {exc}", file=sys.stderr)
        return 2
    try:
        woo, rotulo = _woo(a.woo)
    except Exception as exc:  # noqa: BLE001
        print(f"NO SE PUDO LEER WOO ({a.woo}): {exc}", file=sys.stderr)
        return 2
    canales = [c.strip().lower() for c in a.canales.split(",") if c.strip()]
    pub = {r["sku"]: r for r in sdb.fetch_all(_SQL_PUBLICADAS, {"c": canales})}
    explicados: dict[str, dict[str, Any]] = {}
    if a.explicados:
        with a.explicados.open(encoding="utf-8") as fh:
            explicados = leer_explicados(csv.DictReader(fh))

    difs = clasificar(libro, woo)
    for f in difs:
        p = pub.get(f["sku"]) or {}
        f["drop_publicaciones"] = int(p.get("n") or 0)
        f["canal_max"] = p.get("canal")
        f["explicado"] = explicacion(f, explicados.get(f["sku"]))
    cuentan = [f for f in difs if a.todos or f["drop_publicaciones"]]
    sin_explicar = [f for f in cuentan if not f["explicado"]]

    con_libro = sum(1 for d in libro.values() if d["libre"] is not None)
    print(f"Libro: {con_libro:,} SKUs con número (STOCK_LIBRO_DESDE={settings.stock_libro_desde}, "
          f"vista {settings.stock_libro_vista}) · {sum(1 for d in libro.values() if d['pendientes']):,} "
          f"con ventas DROP sin OV")
    print(f"Contra: {rotulo} · {sum(1 for v in woo.values() if v is not None):,} SKUs con número")
    por_cat: dict[str, int] = {}
    for f in difs:
        por_cat[f["categoria"]] = por_cat.get(f["categoria"], 0) + 1
    print("Diferencias:", ", ".join(f"{k} {v:,}" for k, v in sorted(por_cat.items())) or "ninguna",
          f"· cuentan {len(cuentan):,} ({'todas' if a.todos else 'con publicación DROP'})"
          f" · explicadas {len(cuentan) - len(sin_explicar):,} · SIN EXPLICAR {len(sin_explicar):,}")
    for f in sorted(sin_explicar, key=lambda x: -abs(x["delta"]))[:a.muestra]:
        print(f"  {f['sku']:<24} {f['categoria']:<10} woo {f['woo']!s:>5} → libro {f['libro']!s:>5}"
              f"  (libre {f['libre']!s}, pend {f['pendientes']}, canal {f['canal_max']!s},"
              f" {f['drop_publicaciones']} pub.)")
    if a.salida:
        campos = ["sku", "categoria", "woo", "libro", "libre", "pendientes", "delta",
                  "drop_publicaciones", "canal_max", "explicado"]
        with a.salida.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=campos)
            w.writeheader()
            w.writerows(difs)
        print(f"CSV: {a.salida} ({len(difs):,} filas)")
    return 1 if sin_explicar else 0


if __name__ == "__main__":
    sys.exit(main())
