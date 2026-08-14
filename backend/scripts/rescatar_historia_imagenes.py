"""
rescatar_historia_imagenes.py — Lo que las dos tablas de imágenes tenían ANTES
de que existiera el espejo (PASO 4, docs/PLAN_31_TABLAS.md).

    ml_image_edit_backlog  →  ops.channel_submissions  (operacion='imagen')
    amazon_imagenes        →  enrich.product_media     (kind='amazon')

MISMO PATRÓN QUE LAS 44 BAJAS DE AMAZON
---------------------------------------
Las dos tablas ya se espejan en vivo desde el 24-jul. Lo que falta es sólo lo
ANTERIOR a esa fecha: nunca hubo quien lo copiara. El script no supone cuánto
es — lo calcula, lo enseña, y verifica que quede en cero.

`amazon_imagenes` NO es una caché regenerable, aunque lo parezca. Cada fila es
el resultado de un pipeline caro: descargar la imagen de WordPress, convertir
WebP→JPEG, escalar a ≥1000 px con Lanczos y, cuando no alcanza, pasar por
Real-ESRGAN. Perder una fila no es "se vuelve a consultar": es volver a
procesar la imagen y subir OTRA copia a WordPress, con otro `wp_media_id`.

LA LLAVE CAMBIA DE FORMA, Y ESO IMPORTA
---------------------------------------
En MySQL la PK es `src_hash` = SHA-1 de la URL, y es ÚNICA GLOBAL: la misma URL
procesada para dos SKUs es UNA fila. En kubera el índice único es
`(sku, kind, source_url)`, así que esa misma URL da DOS filas.

No es un error: el lector (`imagenes_amazon._cache_get`) pregunta "¿ya procesé
esta URL?" sin filtrar por SKU, así que cualquiera de las dos contesta lo mismo.
Queda escrito porque los conteos de las dos tablas **no tienen por qué cuadrar**
y alguien lo va a leer como una pérdida.

`created_at` se preserva del origen en los dos casos. Sellar historia de julio
con la fecha de la copia la pondría al frente de la fila — el error que ya
costó una corrección en `crear_logs`.

Uso:
  ...python backend/scripts/rescatar_historia_imagenes.py               # dry-run
  ...python backend/scripts/rescatar_historia_imagenes.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import re
import sys
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
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    ap.add_argument("--sandbox", action="store_true")
    args = ap.parse_args()

    E = cargar(".env")
    dsn = cargar("env.staging")["SUPABASE_DB_URL"] if args.sandbox else E["SUPABASE_DB_URL"]
    ref = (re.search(r"postgres\.([a-z0-9]+):", dsn) or [None, ""])[1]
    if args.real and args.acepto_destino != ref[:8]:
        sys.exit(f"ABORT: con --real hay que nombrar el destino "
                 f"(--acepto-destino {ref[:8]}).")
    print(f"[{'REAL' if args.real else 'DRY-RUN'}] destino: {ref[:8]}…", flush=True)

    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    pg = psycopg2.connect(dsn, connect_timeout=25)
    todo_ok = True

    # ══ 1) ml_image_edit_backlog → ops.channel_submissions ═════════════════
    print("\n── ml_image_edit_backlog → ops.channel_submissions ──")
    with my.cursor() as c:
        c.execute("""SELECT id, cuenta, sku, wp_media_id_new, action,
                            gemini_success, gemini_error, created_at
                       FROM ml_image_edit_backlog ORDER BY id""")
        edits = c.fetchall()
    with pg.cursor() as c:
        c.execute("select detail_ref from ops.channel_submissions "
                  "where detail_ref like 'mysql:ml_image_edit_backlog:%%'")
        ya = {r[0] for r in c.fetchall()}
    faltan_e = [f for f in edits if f"mysql:ml_image_edit_backlog:{f['id']}" not in ya]
    print(f"  MySQL {len(edits):,} · ya en la bitácora {len(ya):,} · FALTAN {len(faltan_e):,}")
    if faltan_e:
        fs = sorted(f["created_at"] for f in faltan_e if f["created_at"])
        print(f"  fechas: {fs[0]} → {fs[-1]}")

    # ══ 2) amazon_imagenes → enrich.product_media ══════════════════════════
    print("\n── amazon_imagenes → enrich.product_media ──")
    with my.cursor() as c:
        c.execute("SELECT sku, src_url, amz_url, created_at FROM amazon_imagenes")
        imgs = c.fetchall()
    with pg.cursor() as c:
        c.execute("select sku::text, source_url from enrich.product_media "
                  "where kind = 'amazon'")
        ya_img = {(r[0].lower(), r[1]) for r in c.fetchall()}
    faltan_i = [f for f in imgs
                if (str(f["sku"] or "").lower(), f["src_url"]) not in ya_img]
    print(f"  MySQL {len(imgs):,} · ya en product_media {len(ya_img):,} · "
          f"FALTAN {len(faltan_i):,}")
    if faltan_i:
        fs = sorted(f["created_at"] for f in faltan_i if f["created_at"])
        print(f"  fechas: {fs[0]} → {fs[-1]}")
    sin_sku = [f for f in faltan_i if not str(f["sku"] or "").strip()]
    if sin_sku:
        print(f"  ⚠ {len(sin_sku)} sin SKU: no se pueden colocar en product_media "
              f"(la llave lo incluye). Se OMITEN y se reportan.")
        faltan_i = [f for f in faltan_i if str(f["sku"] or "").strip()]

    if not args.real:
        print("\n== DRY-RUN: no se escribió nada ==")
        my.close(); pg.close()
        return

    # ══ Escribir ═══════════════════════════════════════════════════════════
    if faltan_e:
        with pg.cursor() as c:
            psycopg2.extras.execute_values(
                c,
                """insert into ops.channel_submissions
                     (canal, cuenta, sku, submission_id, operacion, status,
                      success, error_resumen, detail_ref, submitted_at, created_at)
                   values %s""",
                [("mercado_libre", f.get("cuenta") or "studio", f["sku"] or "",
                  str(f.get("wp_media_id_new") or "") or None, "imagen",
                  f.get("action"), bool(f.get("gemini_success")),
                  (f.get("gemini_error") or "")[:500] or None,
                  f"mysql:ml_image_edit_backlog:{f['id']}",
                  f.get("created_at"), f.get("created_at"))
                 for f in faltan_e],
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", page_size=500)
        pg.commit()
    if faltan_i:
        with pg.cursor() as c:
            psycopg2.extras.execute_values(
                c,
                """insert into enrich.product_media
                     (sku, kind, source_url, cdn_url, created_at) values %s
                   -- Por COLUMNAS y no `on constraint`: uq_product_media_sku_kind_url
                   -- es un índice único, no una constraint nombrada, y Postgres
                   -- no acepta `on constraint` para índices.
                   on conflict (sku, kind, source_url) do nothing""",
                [(f["sku"], "amazon", f["src_url"], f["amz_url"], f["created_at"])
                 for f in faltan_i],
                template="(%s,%s,%s,%s,%s)", page_size=500)
        pg.commit()

    # ══ Verificación ═══════════════════════════════════════════════════════
    print("\n── verificación ──")

    def check(etiqueta: str, ok: bool, detalle: str = "") -> None:
        nonlocal todo_ok
        todo_ok &= ok
        print(f"  [{'OK  ' if ok else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))

    with pg.cursor() as c:
        c.execute("select count(*) from ops.channel_submissions "
                  "where detail_ref like 'mysql:ml_image_edit_backlog:%%'")
        n_e = c.fetchone()[0]
        c.execute("select sku::text, source_url from enrich.product_media "
                  "where kind = 'amazon'")
        ahora_img = {(r[0].lower(), r[1]) for r in c.fetchall()}
    resto_i = [f for f in imgs if str(f["sku"] or "").strip()
               and (str(f["sku"]).lower(), f["src_url"]) not in ahora_img]
    check("ediciones de imagen en la bitácora", n_e >= len(edits),
          f"{n_e:,} de {len(edits):,}")
    check("imágenes de Amazon en product_media", not resto_i,
          f"{len(resto_i)} sin colocar")

    # Que la FECHA haya viajado: es el error que ya costó una corrección.
    with pg.cursor() as c:
        c.execute("""select count(*) from ops.channel_submissions
                      where detail_ref like 'mysql:ml_image_edit_backlog:%%'
                        and created_at::date <> submitted_at::date""")
        desfase = c.fetchone()[0]
    check("fechas del evento preservadas (created_at = submitted_at)",
          not desfase, f"{desfase} filas selladas con otra fecha")

    my.close(); pg.close()
    print(f"\nRESULTADO: {'rescatado y verificado' if todo_ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if todo_ok else 1)


if __name__ == "__main__":
    main()
