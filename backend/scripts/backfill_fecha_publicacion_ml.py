"""
backfill_fecha_publicacion_ml.py — Rellena `channel.listings.date_published`
con la fecha REAL de publicación de cada listing de Mercado Libre (`date_created`
de la API), para las filas que ya existían antes de la migración 0031.

POR QUÉ. `channel.listings` nunca guardó la fecha de publicación real — solo
`updated_at` (última vez que el sync la tocó, que cambia con cada precio/stock
y no sirve para el KPI "publicaciones activadas por semana" del tab Métricas).
Desde la 0031, `services/inventario.py` captura `date_created` hacia ADELANTE
en cada sync, pero su UPDATE es "solo si algo cambió ese ciclo": una fila cuyo
precio/stock no se mueven nunca dispara el UPDATE, así que lo que ya estaba en
la tabla se queda con `date_published` NULL para siempre si no se rellena aquí
una vez.

Nunca pisa un valor existente: filtra `date_published is null` al leer y usa
`coalesce(date_published, %s)` al escribir.

Uso:
  cd backend && python -m scripts.backfill_fecha_publicacion_ml                          # dry-run
  cd backend && python -m scripts.backfill_fecha_publicacion_ml --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import httpx
import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_API = "https://api.mercadolibre.com"
_LOTE = 20  # multiget de ML: mismo tamaño que sincronizar_ml_huerfanas.py


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    args = ap.parse_args()

    E = cargar(".env")
    ref = (re.search(r"postgres\.([a-z0-9]+):", E["SUPABASE_DB_URL"]) or [None, ""])[1]
    if args.real and args.acepto_destino != ref[:8]:
        sys.exit(f"ABORT: con --real hay que nombrar el destino "
                 f"(--acepto-destino {ref[:8]}).")
    print(f"[{'REAL' if args.real else 'DRY-RUN'}] destino: {ref[:8]}…\n", flush=True)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/
    from services import meli  # noqa: E402  (token por cuenta, MySQL ml_tokens*)

    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)
    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute("""select l.sku, l.account_id::text as account_id, l.listing_id,
                            a.legacy_code as cuenta
                       from channel.listings l
                       join core.accounts a on a.id = l.account_id
                      where l.canal = 'mercado_libre'
                        and l.listing_id is not null
                        and l.date_published is null""")
        huecos = c.fetchall()

    print(f"  listings de ML sin date_published: {len(huecos)}\n", flush=True)
    if not huecos:
        print("== nada que hacer ==")
        pg.close()
        return

    por_cuenta: dict[str, list[dict]] = {}
    for h in huecos:
        por_cuenta.setdefault(h["cuenta"], []).append(h)

    resueltos: list[tuple[str, str, str]] = []  # (sku, account_id, date_created)
    fallidos = 0
    with httpx.Client(base_url=_API, timeout=40.0) as cli:
        for cuenta, filas in por_cuenta.items():
            token = meli._access_token(cuenta)
            if not token:
                print(f"  ({cuenta}: sin token, se omite — {len(filas)} listings)")
                fallidos += len(filas)
                continue
            headers = {"Authorization": f"Bearer {token}"}
            for i in range(0, len(filas), _LOTE):
                lote = filas[i:i + _LOTE]
                ids = ",".join(f["listing_id"] for f in lote)
                try:
                    r = cli.get("/items", headers=headers,
                                params={"ids": ids, "attributes": "id,date_created"})
                    if r.status_code != 200:
                        fallidos += len(lote)
                        continue
                    por_id = {}
                    for e in r.json():
                        b = e.get("body") or {}
                        if b.get("id"):
                            por_id[b["id"]] = b.get("date_created")
                    for f in lote:
                        dc = por_id.get(f["listing_id"])
                        if dc:
                            resueltos.append((f["sku"], f["account_id"], dc))
                        else:
                            fallidos += 1
                except Exception as exc:  # noqa: BLE001
                    fallidos += len(lote)
                    print(f"    lote {cuenta} falló: {str(exc)[:70]}")
                time.sleep(0.15)  # mismo ritmo que sincronizar_ml_huerfanas.py
            print(f"  {cuenta}: {len(filas)} listings procesados")

    print(f"\n  resueltos: {len(resueltos)}   sin resolver: {fallidos}")
    for sku, _, dc in resueltos[:10]:
        print(f"    {sku:24s} {dc}")

    if not args.real:
        print("\n== DRY-RUN: no se escribió nada ==")
        pg.close()
        return

    with pg.cursor() as c:
        for sku, account_id, dc in resueltos:
            c.execute("""update channel.listings
                            set date_published = coalesce(date_published, %s)
                          where sku = %s and account_id = %s and canal = 'mercado_libre'""",
                      (dc, sku, account_id))
    pg.commit()
    print(f"\n== APLICADO: {len(resueltos)} fecha(s) de publicación ==")
    pg.close()


if __name__ == "__main__":
    main()
