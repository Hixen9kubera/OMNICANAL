"""
comparar_stock_watch_foto.py — El arnés de los días de observación del PASO 2.

Se corre CADA MAÑANA mientras `SUPABASE_WRITE_STOCK_WATCH=true` y
`SUPABASE_READ_STOCK_WATCH=false`, o sea mientras la foto se escribe en los dos
lados pero MySQL sigue mandando. Solo se enciende la lectura cuando este script
sale limpio varios días seguidos.

QUÉ COMPARA, Y POR QUÉ ESTAS COSAS
----------------------------------
No basta con "los datos coinciden". Lo que se está migrando es la MEMORIA de un
proceso que escribe stock en Woo, así que se mide lo que de verdad importa:

1. **Las dos fotos están VIVAS.** Una foto detenida que coincide con otra
   detenida "coincide" perfectamente y no vale nada. Se exige que las dos hayan
   avanzado en la última hora (3 pasadas de 20 min).

2. **Mismo censo.** Un SKU de más o de menos en un lado es un delta que un lado
   vería y el otro no.

3. **Mismos valores, incluidos los NULL.** Un NULL contra un 0 no es un detalle
   de tipo: significa "Woo no gestiona este SKU" contra "Woo dice que hay cero",
   y el espejo del DROP los trata distinto.

4. **EL DELTA QUE SE APLICARÍA.** Es la prueba que de verdad decide. Para cada
   SKU se calcula lo que el vigilante haría con cada foto y se comparan los DOS
   resultados. Que los datos coincidan es la hipótesis; que la DECISIÓN coincida
   es la conclusión. En el incidente de los 964 pedidos, los datos coincidían.

5. **El canal `general` de `channel.listings`** contra la foto de kubera: el
   otro lector. Se avisa si divergen más de lo que explica el desfase de una
   pasada (el espejo del DROP corre en su propio job, así que un puñado de SKUs
   recién movidos es normal y no es falla).

Uso:
  ...python backend/scripts/comparar_stock_watch_foto.py
  ...python backend/scripts/comparar_stock_watch_foto.py --max-atraso-min 90
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    if not p.exists():
        return d
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-atraso-min", type=int, default=60)
    args = ap.parse_args()

    E = cargar(".env")
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    todo_ok = True

    def check(etiqueta: str, ok: bool, detalle: str = "") -> None:
        nonlocal todo_ok
        todo_ok &= ok
        print(f"  [{'OK  ' if ok else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))

    # ── 1) ¿Las dos fotos están vivas? ──────────────────────────────────────
    print("\n── 1. señal de vida ──")
    with my.cursor() as c:
        c.execute("SELECT COUNT(*) n, MAX(actualizado) ult FROM stock_watch_foto")
        f_my = c.fetchone()
    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute("select count(*) n, max(actualizado) ult from ops.stock_watch_photo")
        f_kb = c.fetchone()
    ult_kb = f_kb["ult"].replace(tzinfo=None) if f_kb["ult"] else None
    for nombre, ult in (("MySQL ", f_my["ult"]), ("kubera", ult_kb)):
        atr = (ahora - ult) if ult else timedelta(days=999)
        check(f"{nombre} refrescada hace {atr.total_seconds() / 60:.0f} min",
              atr <= timedelta(minutes=args.max_atraso_min), f"última {ult} UTC")

    # ── 2) Mismo censo y 3) mismos valores ──────────────────────────────────
    print("\n── 2-3. censo y valores ──")
    with my.cursor() as c:
        c.execute("SELECT sku, stock_woo, stock_odoo FROM stock_watch_foto")
        A = {str(r["sku"]).lower(): (r["stock_woo"], r["stock_odoo"]) for r in c.fetchall()}
    with pg.cursor() as c:
        c.execute("select sku::text, stock_woo, stock_odoo from ops.stock_watch_photo")
        B = {str(s).lower(): (w, o) for s, w, o in c.fetchall()}

    solo_my, solo_kb = set(A) - set(B), set(B) - set(A)
    difs = sorted(k for k in set(A) & set(B) if A[k] != B[k])
    check(f"MySQL {len(A):,} filas · kubera {len(B):,}", not solo_my and not solo_kb,
          f"solo MySQL {len(solo_my)} · solo kubera {len(solo_kb)}")
    check(f"valores idénticos en los {len(set(A) & set(B)):,} comunes", not difs,
          f"{len(difs)} distintos")
    for k in difs[:8]:
        print(f"        {k}: mysql (woo,odoo)={A[k]} kubera={B[k]}")
    for k in list(solo_my)[:5]:
        print(f"        solo MySQL: {k} = {A[k]}")
    for k in list(solo_kb)[:5]:
        print(f"        solo kubera: {k} = {B[k]}")

    # ── 4) La DECISIÓN: ¿el delta que se aplicaría es el mismo? ─────────────
    # No se llama a Odoo ni a Woo (este script es de solo lectura y no debe
    # depender de dos APIs para correr todas las mañanas). Se compara la parte
    # de la cuenta que vive en la foto: `odoo_en_la_foto`, que es el único
    # término que la migración cambia. Si ese término es idéntico para todos los
    # SKUs, el delta resultante también lo es, venga Odoo con lo que venga.
    print("\n── 4. la decisión (el término de la foto en delta = odoo_ahora − odoo_foto) ──")
    dif_odoo = sorted(k for k in set(A) & set(B) if A[k][1] != B[k][1])
    huerfanos = sorted(solo_my | solo_kb)
    check("el término `odoo_foto` es idéntico en los dos lados", not dif_odoo,
          f"{len(dif_odoo)} SKUs darían un delta distinto")
    for k in dif_odoo[:8]:
        d = (B[k][1] or 0) - (A[k][1] or 0)
        print(f"        {k}: odoo_foto mysql={A[k][1]} kubera={B[k][1]} "
              f"→ el delta cambiaría en {d:+d} pzas")
    check("ningún SKU existe en una foto y no en la otra", not huerfanos,
          f"{len(huerfanos)} verían un delta en un lado y no en el otro")

    # ── 5) El otro lector: canal `general` de channel.listings ──────────────
    print("\n── 5. el otro lector (channel.listings canal='general') ──")
    with pg.cursor() as c:
        c.execute("""select l.sku::text, l.stock_own, p.stock_woo
                       from channel.listings l
                       join ops.stock_watch_photo p on p.sku = l.sku
                      where l.canal = 'general' and p.stock_woo is not null
                        and l.stock_own is distinct from p.stock_woo""")
        desfase = c.fetchall()
    # Un puñado es NORMAL: el espejo del DROP corre en su propio job y va una
    # pasada atrás de los SKUs recién movidos. Lo que no es normal es que crezca.
    print(f"        {len(desfase)} publicaciones 'general' con stock distinto a la foto")
    for s, own, woo in desfase[:8]:
        print(f"        {s}: listings={own} foto={woo}")
    check("el desfase del espejo del DROP es de una pasada, no estructural",
          len(desfase) <= 100, f"{len(desfase)} desfasadas (>100 = revisar)")

    my.close(); pg.close()
    print(f"\nRESULTADO: {'las dos fotos coinciden y deciden igual' if todo_ok else 'HAY DIFERENCIAS — no encender la lectura'}")
    sys.exit(0 if todo_ok else 1)


if __name__ == "__main__":
    main()
