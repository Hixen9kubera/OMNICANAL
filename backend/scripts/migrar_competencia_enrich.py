"""
migrar_competencia_enrich.py — Paso 3 del PLAN_COMPETENCIA_v2: mueve el dato
de `propuestas.competencia_*` a `enrich.market_*` con insert…select.

TODO ocurre dentro de la misma base (BD kubera): no toca MySQL ni APIs.
`propuestas` NO se modifica — sigue siendo la fuente viva hasta el paso 5.

Mapeos (columnas que NO migran, decididas en el plan):
  rankings_categoria → market_bestsellers   (fuera: periodo, descuento)
  busquedas          → market_search_results (fuera: periodo, precio_lista,
                                              descuento, vendidos, visitas_30d)
  terminos_categoria → market_terms          (fuera: periodo, url)
  skus               → market_sku_config     (fuera: nombre/categoria_nombre/
                        ruta/raiz_*/imagen — derivables por JOIN; la categoría
                        medida va a categoria_id_real)
  publicacion_metricas → market_listing_metrics (fuera: fuente_unidades,
                        estado, precio_lista; account_id se resuelve por JOIN
                        con core.accounts.legacy_code)

Guardas:
  - `canal='mercado_libre'` explícito en las 4 tablas que no lo traían.
  - FK sku_nuestro → core.products: si el SKU no está en el maestro, la fila
    entra con sku_nuestro=NULL (se conserva el renglón del ranking; se pierde
    solo el vínculo de la corona). Se reporta cuántos.
  - FK sku (sku_config / listing_metrics): la fila NO puede entrar; se salta y
    se reporta cuáles.
  - PK de listing_metrics (sku, canal, cuenta, periodo): el origen puede traer
    DOS listings del mismo SKU en la misma cuenta (caso CAM-0030-IND). Se queda
    el de más visitas (desempate: listing_id); los descartados se reportan.
  - `on conflict do nothing` en todos: re-correr no duplica.

Uso:
  backend/.venv/Scripts/python.exe backend/scripts/migrar_competencia_enrich.py --destino prod
  backend/.venv/Scripts/python.exe backend/scripts/migrar_competencia_enrich.py --destino prod --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
FASE = "F3-migracion-enrich"
CANAL = "mercado_libre"
TIMEOUT_MIN = 10

socket.setdefaulttimeout(60)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def _armar_watchdog() -> None:
    def _matar():
        print(f"WATCHDOG: {TIMEOUT_MIN} min agotados — aborto.", flush=True)
        os._exit(2)
    t = threading.Timer(TIMEOUT_MIN * 60, _matar)
    t.daemon = True
    t.start()


def cargar_env(nombre: str) -> dict[str, str]:
    vals: dict[str, str] = dict(os.environ)
    p = ROOT / nombre
    if not p.exists():
        return vals
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


PROD = cargar_env(".env")
STAGING = cargar_env("env.staging")

# ── Los 5 movimientos. Cada uno: (nombre, sql_insert, conteo_esperado) ──────
MOVIMIENTOS = [
    ("market_bestsellers", f"""
        insert into enrich.market_bestsellers
              (canal, categoria_id, nivel, posicion, externo_id, id_pagina,
               tipo, titulo, precio, precio_lista, vendidos, rating, reviews,
               seller, imagen, url, visitas_30d, item_categoria_id,
               item_categoria_nombre, es_nuestro, sku_nuestro, capturado_en)
        select '{CANAL}', r.categoria_id, r.nivel, r.posicion, r.externo_id,
               r.id_pagina, r.tipo, r.titulo, r.precio, r.precio_lista,
               r.vendidos, r.rating, r.reviews, r.seller, r.imagen, r.url,
               r.visitas_30d, r.item_categoria_id, r.item_categoria_nombre,
               coalesce(r.es_nuestro, false),
               case when exists (select 1 from core.products p
                                  where p.sku = r.sku_nuestro::citext)
                    then r.sku_nuestro::citext end,
               r.capturado_en
          from propuestas.competencia_rankings_categoria r
        on conflict (canal, categoria_id, nivel, posicion) do nothing
    """, 3000),

    ("market_search_results", f"""
        insert into enrich.market_search_results
              (canal, termino, externo_id, posicion, titulo, precio, imagen,
               url, seller, rating, es_nuestro, sku_nuestro, capturado_en)
        select '{CANAL}', b.termino, b.externo_id, b.posicion, b.titulo,
               b.precio, b.imagen, b.url, b.seller, b.rating,
               coalesce(b.es_nuestro, false),
               case when exists (select 1 from core.products p
                                  where p.sku = b.sku_nuestro::citext)
                    then b.sku_nuestro::citext end,
               b.capturado_en
          from propuestas.competencia_busquedas b
        on conflict (canal, termino, externo_id) do nothing
    """, 1816),

    ("market_terms", f"""
        insert into enrich.market_terms
              (canal, categoria_id, posicion, termino, capturado_en)
        select '{CANAL}', t.categoria_id, t.posicion, t.termino, t.capturado_en
          from propuestas.competencia_terminos_categoria t
        on conflict (canal, categoria_id, posicion) do nothing
    """, 5789),

    ("market_sku_config", f"""
        insert into enrich.market_sku_config
              (sku, canal, termino_general, termino_origen, activo,
               categoria_id_real, updated_at)
        select s.sku::citext, '{CANAL}', s.termino_general, s.termino_origen,
               coalesce(s.activo, true), s.categoria_id,
               coalesce(s.actualizado_en, now())
          from propuestas.competencia_skus s
         where exists (select 1 from core.products p where p.sku = s.sku::citext)
        on conflict (sku, canal) do nothing
    """, 1584),

    # distinct on: si el mismo (sku, cuenta, periodo) trae DOS listings, gana
    # el de más visitas; desempate por listing_id para que sea determinista.
    ("market_listing_metrics", """
        insert into enrich.market_listing_metrics
              (sku, canal, cuenta, periodo, account_id, listing_id, title,
               sale_price, visits_30d, units_30d, metrics_updated_at)
        select distinct on (m.sku, m.canal, m.cuenta, m.periodo)
               m.sku::citext, m.canal, coalesce(m.cuenta, ''), m.periodo,
               a.id, m.listing_id, m.titulo, m.precio, m.visitas_30d,
               m.unidades_30d, coalesce(m.actualizado_en, now())
          from propuestas.competencia_publicacion_metricas m
          left join core.accounts a on a.legacy_code = m.cuenta
         where m.periodo is not null
           and exists (select 1 from core.products p where p.sku = m.sku::citext)
      order by m.sku, m.canal, m.cuenta, m.periodo,
               m.visitas_30d desc nulls last, m.listing_id
        on conflict (sku, canal, cuenta, periodo) do nothing
    """, 3118),
]

DIAGNOSTICOS = {
    "sku_nuestro_sin_maestro_rankings": """
        select count(*) from propuestas.competencia_rankings_categoria r
         where r.sku_nuestro is not null
           and not exists (select 1 from core.products p
                            where p.sku = r.sku_nuestro::citext)""",
    "sku_nuestro_sin_maestro_busquedas": """
        select count(*) from propuestas.competencia_busquedas b
         where b.sku_nuestro is not null
           and not exists (select 1 from core.products p
                            where p.sku = b.sku_nuestro::citext)""",
    "skus_config_fuera_del_maestro": """
        select count(*) from propuestas.competencia_skus s
         where not exists (select 1 from core.products p
                            where p.sku = s.sku::citext)""",
    "metricas_fuera_del_maestro": """
        select count(*) from propuestas.competencia_publicacion_metricas m
         where not exists (select 1 from core.products p
                            where p.sku = m.sku::citext)""",
    "metricas_sin_periodo": """
        select count(*) from propuestas.competencia_publicacion_metricas
         where periodo is null""",
    "metricas_duplicadas_por_pk_nueva": """
        select coalesce(sum(n - 1), 0) from (
            select count(*) n from propuestas.competencia_publicacion_metricas
             where periodo is not null
             group by sku, canal, cuenta, periodo having count(*) > 1) d""",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    ap.add_argument("--destino", choices=("sandbox", "prod"), default="sandbox")
    args = ap.parse_args()
    _armar_watchdog()

    env = PROD if args.destino == "prod" else STAGING
    m = re.search(r"postgres\.([a-z0-9]+):", env.get("SUPABASE_DB_URL", ""))
    if not m:
        sys.exit("ABORT: sin ref en el SUPABASE_DB_URL destino.")
    ref = m.group(1)
    modo = "REAL" if args.real else "DRY-RUN"
    if args.real and not ref.startswith(args.acepto_destino or "±"):
        sys.exit(f"ABORT: --real exige --acepto-destino ({ref[:8]}…).")
    print(f"[{modo}] destino: {ref[:8]}…", flush=True)

    pg = psycopg2.connect(env["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = False
    cur = pg.cursor()

    cur.execute("select 1 from information_schema.schemata where schema_name='propuestas'")
    if not cur.fetchone():
        pg.close()
        sys.exit("ABORT: 'propuestas' no existe en este destino (el origen "
                 "vive solo en la BD kubera).")

    # ── Diagnóstico (solo lectura) ───────────────────────────────────────────
    reporte: dict = {"diagnostico": {}}
    for nombre, sql in DIAGNOSTICOS.items():
        cur.execute(sql)
        reporte["diagnostico"][nombre] = int(cur.fetchone()[0])  # sum() da Decimal

    for tabla, _sql, esperado in MOVIMIENTOS:
        cur.execute(f"select count(*) from enrich.{tabla}")
        reporte.setdefault("ya_en_destino", {})[tabla] = cur.fetchone()[0]

    if not args.real:
        print("\n== DRY-RUN — nada escrito ==")
        print(json.dumps(reporte, ensure_ascii=False, indent=1))
        pg.close()
        return

    # ── Migración: una transacción por tabla ─────────────────────────────────
    resultado = "ok"
    for tabla, sql, esperado in MOVIMIENTOS:
        try:
            cur.execute(sql)
            insertadas = cur.rowcount
            cur.execute(f"select count(*) from enrich.{tabla}")
            final = cur.fetchone()[0]
            pg.commit()
            marca = "ok" if final >= esperado - reporte["diagnostico"].get(
                "metricas_duplicadas_por_pk_nueva", 0) else "REVISAR"
            reporte.setdefault("migrado", {})[tabla] = {
                "insertadas": insertadas, "final": final, "esperado": esperado}
            print(f"  {tabla:<26} insertadas={insertadas:>5}  final={final:>5}  "
                  f"(esperado {esperado})", flush=True)
        except Exception as exc:
            pg.rollback()
            resultado = "fallo"
            reporte.setdefault("errores", {})[tabla] = str(exc).splitlines()[0][:200]
            print(f"  {tabla:<26} FALLO: {exc}", flush=True)

    cur.execute("""insert into migration.reconciliation_runs
                   (dominio, descripcion, conteos, checksums, resultado)
                   values (%s, 'Paso 3: propuestas.competencia_* -> enrich.market_*
                   (insert...select, propuestas intacta)', %s, %s, %s)""",
                (FASE, json.dumps(reporte, ensure_ascii=False, default=str),
                 json.dumps({}), resultado))
    pg.commit()
    pg.close()

    print("\n== APLICADO ==")
    print(json.dumps(reporte, ensure_ascii=False, indent=1, default=str))
    if resultado != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
