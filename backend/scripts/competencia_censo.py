"""
competencia_censo.py — El censo de Competencia se llena SOLO, desde channel.listings.

QUÉ PROBLEMA RESUELVE
---------------------
La pestaña Competencia no muestra el catálogo: muestra una LISTA, `enrich.
market_sku_config`. Un SKU que no está en esa lista no existe para el módulo,
aunque esté publicado y vendiendo. TEC-0407-MET fue el caso que lo destapó.

Esa lista se llenó UNA vez —`migrar_competencia_enrich.py`, 12-ago, 1,584 filas—
y se quedó ahí. El escritor que la alimentaba (`competencia_store.guardar_skus`)
se borró el 13-ago en el commit 9c601b7 ("fuera SQLite") y su gemela remota nunca
se escribió: `competencia_supabase` tiene reemplazar_ranking, reemplazar_terminos,
activar_raiz, actualizar_termino, proponer_termino, reemplazar_busqueda y
guardar_publicaciones — ninguna da de alta un SKU. Resultado medido el 18-ago:
1,117 SKUs con publicación viva en ML y CERO presencia en el módulo.

Este script es el escritor que faltaba, y no reconstruye el borrado: en vez de
partir de MySQL como aquél, parte de `channel.listings`, que ya es la verdad de
kubera sobre qué está publicado y la refresca `deltas-channel` a diario.

BAJO EL ESQUEMA DE KUBERA, SIN EXCEPCIONES
------------------------------------------
Todo sale de `core.*`, `channel.*` y `enrich.*` vía `supabase_db`. CERO MySQL:
no toca `ml_progress`, `productos` ni `categorias_ml`, y por eso no depende de
`SUPABASE_WRITE_CORE` ni de `SUPABASE_WRITE_CATEGORIAS` — corre igual con las
banderas prendidas o apagadas. Es la diferencia con `competencia_captura.py`,
que sí lee `ml_progress` de MySQL en cuatro puntos (65, 193, 424, 791) y por eso
todavía no se puede automatizar sin decidir antes esa sustitución.

QUÉ CUENTA COMO PUBLICACIÓN VIVA
--------------------------------
`lower(situacion) in ('active','paused')`, la misma definición que ya usa
`channel_read.vivas_ml()`. Una pausada sigue existiendo en ML y sigue
compitiendo; sólo no está comprando tráfico. Quedan fuera las 267 filas con
`status='error'` que nunca llegaron a publicarse, las 17 cerradas y las
under_review. Medido 18-ago: 4,955 filas de ML → 2,495 SKUs vivos distintos.

LO QUE ESTE SCRIPT **NO** HACE, A PROPÓSITO
-------------------------------------------
1. NO toca `activo` de las filas que ya existen. Ese flag lo mueve una persona
   desde el panel (`activar_raiz`): si el cron lo reescribiera cada noche,
   desharía la decisión humana en cada corrida. Es la misma trampa que en
   costing borra los precios manuales. Sólo las ALTAS nacen con activo=true.

2. NO pisa mediciones. `channel.listings` no tiene título, ni visitas, ni
   unidades — esos datos sólo salen de medir. El upsert del paso 2 lleva un
   `WHERE title IS NULL AND visits_30d IS NULL AND units_30d IS NULL`, así que
   sólo refresca las filas que creó este mismo script. Verificado: las 3,118
   filas migradas tienen las tres columnas pobladas al 100%, o sea que el
   guardián las cubre todas. Sin ese WHERE, un `sale_price` de channel.listings
   —que es precio de lista y se refresca cada 15 min— sobrescribiría el precio
   REAL que paga el comprador que trajo la medición (ver el docstring de
   `competencia_supabase.guardar_publicaciones`).

3. NO da de baja. Los SKUs que quedan en el censo sin publicación viva (206 al
   18-ago, herencia de la carga manual que no filtró por situación) se REPORTAN
   y nada más. Sacarlos es decisión de negocio, no de un cron.

LA CUENTA SALE DE core.accounts, NO DE store_name
-------------------------------------------------
`channel.listings.account_id` está poblado en las 4,955 filas; `store_name` sólo
en 4,044. Derivar la cuenta de `store_name` dejaría ~900 publicaciones sin
cuenta y por lo tanto fuera de `market_listing_metrics`, cuya PK la incluye.

USO
---
    python scripts/competencia_censo.py --dry-run   # dice qué haría, no escribe
    python scripts/competencia_censo.py             # escribe

Cron: `backend/railway.competencia-censo.json`, diario 07:30 UTC — después de
`deltas-channel` (06:45), que es quien refresca `channel.listings`.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from services import supabase_db  # noqa: E402

CANAL = "mercado_libre"

# La definición de "viva", igual que channel_read.vivas_ml(). Se escribe una vez
# y se interpola en las tres consultas para que no puedan divergir.
VIVA = ("l.canal = %(canal)s "
        "and lower(l.situacion) in ('active', 'paused') "
        "and nullif(l.listing_id, '') is not null "
        "and exists (select 1 from core.products p where p.sku = l.sku)")

# ── Paso 1: alta en el censo ────────────────────────────────────────────────
# El `exists` contra core.products no es decorativo: market_sku_config.sku tiene
# FK a esa tabla y sin el guardián la corrida entera revienta por un SKU huérfano.
SQL_CENSO = f"""
insert into enrich.market_sku_config (sku, canal, activo, updated_at)
select distinct l.sku, %(canal)s, true, now()
  from channel.listings l
 where {VIVA}
on conflict (sku, canal) do nothing
"""

# ── Paso 2: la publicación, para que deje de decir "sin publicar" ───────────
# El panel lee `market_publicaciones_v` (← market_listing_metrics), NO
# channel.listings. Por eso un SKU publicado pero nunca medido se ve como "sin
# publicar": no hay fila que mostrar. Esto la crea con lo que channel.listings
# SÍ sabe —item, estado, precio— y deja título/visitas/unidades en NULL para que
# la UI muestre "—" (no medido) en vez de un cero que se lee como "no vende".
#
# `distinct on` porque la PK es (sku, canal, cuenta, periodo) y un mismo par
# (sku, cuenta) podría traer dos publicaciones vivas: sin él, Postgres aborta con
# "ON CONFLICT DO UPDATE command cannot affect row a second time". Hoy son 0 los
# pares duplicados, pero la corrida no puede depender de que siga siendo cierto.
# Gana la activa sobre la pausada; desempate por listing_id para que sea
# determinista entre corridas.
SQL_PUBLICACIONES = f"""
insert into enrich.market_listing_metrics
      (sku, canal, cuenta, periodo, account_id, listing_id,
       estado, sale_price, list_price, metrics_updated_at)
select distinct on (l.sku, a.legacy_code)
       l.sku, %(canal)s, a.legacy_code,
       date_trunc('month', now())::date,
       l.account_id, l.listing_id,
       lower(l.situacion), l.price, l.price_base, now()
  from channel.listings l
  join core.accounts a on a.id = l.account_id
 where {VIVA}
 order by l.sku, a.legacy_code,
          (lower(l.situacion) = 'active') desc, l.listing_id
on conflict (sku, canal, cuenta, periodo) do update
   set listing_id = excluded.listing_id,
       account_id = excluded.account_id,
       estado     = excluded.estado,
       sale_price = excluded.sale_price,
       list_price = excluded.list_price,
       metrics_updated_at = now()
 where market_listing_metrics.title      is null
   and market_listing_metrics.visits_30d is null
   and market_listing_metrics.units_30d  is null
"""

DIAGNOSTICO = {
    "skus_vivos_en_ml": f"""
        select count(distinct l.sku) from channel.listings l where {VIVA}""",
    "censo_total": """
        select count(*) from enrich.market_sku_config where canal = %(canal)s""",
    "censo_activos": """
        select count(*) from enrich.market_sku_config
         where canal = %(canal)s and activo""",
    "altas_pendientes": f"""
        select count(distinct l.sku) from channel.listings l
         where {VIVA}
           and not exists (select 1 from enrich.market_sku_config c
                            where c.sku = l.sku and c.canal = %(canal)s)""",
    "censo_sin_publicacion_viva": """
        select count(*) from enrich.market_sku_config c
         where c.canal = %(canal)s
           and not exists (select 1 from channel.listings l
                            where l.sku = c.sku and l.canal = %(canal)s
                              and lower(l.situacion) in ('active', 'paused'))""",
    "publicaciones_del_periodo": """
        select count(*) from enrich.market_listing_metrics
         where canal = %(canal)s and periodo = date_trunc('month', now())::date""",
    "sin_termino_general": """
        select count(*) from enrich.market_sku_config
         where canal = %(canal)s and termino_id is null""",
}


def _medir() -> dict[str, int]:
    return {k: supabase_db.fetch_scalar(q, {"canal": CANAL})
            for k, q in DIAGNOSTICO.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Mide y reporta, pero no escribe nada.")
    args = ap.parse_args()

    print("=== Competencia · censo automático desde channel.listings ===")
    if not supabase_db.disponible():
        print("ERROR: falta SUPABASE_DB_URL. Todo este script vive en kubera.")
        return 2

    antes = _medir()
    print("\n-- Antes --")
    print(f"  SKUs vivos en ML (active|paused) : {antes['skus_vivos_en_ml']}")
    print(f"  censo                            : {antes['censo_total']} "
          f"({antes['censo_activos']} activos)")
    print(f"  altas pendientes                 : {antes['altas_pendientes']}")
    print(f"  publicaciones del periodo        : {antes['publicaciones_del_periodo']}")

    if args.dry_run:
        print("\n--dry-run: no se escribió nada.")
        return 0

    print("\n-- Escribiendo --")
    altas = supabase_db.execute(SQL_CENSO, {"canal": CANAL})
    print(f"  altas en market_sku_config     : {altas}")
    pubs = supabase_db.execute(SQL_PUBLICACIONES, {"canal": CANAL})
    print(f"  filas de market_listing_metrics: {pubs}  "
          f"(altas + refrescos de filas NO medidas)")

    despues = _medir()
    print("\n-- Después --")
    print(f"  censo                     : {despues['censo_total']} "
          f"({despues['censo_activos']} activos)")
    print(f"  altas pendientes          : {despues['altas_pendientes']}  "
          f"{'OK' if despues['altas_pendientes'] == 0 else '<-- REVISAR'}")
    print(f"  publicaciones del periodo : {despues['publicaciones_del_periodo']}")

    # Lo que el cron NO decide, pero sí tiene que decir en voz alta.
    huerfanos = despues["censo_sin_publicacion_viva"]
    if huerfanos:
        print(f"\n  AVISO: {huerfanos} SKUs siguen en el censo sin publicación "
              f"viva (cerrados, pausados fuera de ML o nunca publicados). "
              f"Este script NO da de baja: sacarlos es decisión de negocio.")
    sin_termino = despues["sin_termino_general"]
    if sin_termino:
        print(f"  AVISO: {sin_termino} SKUs sin término general. Hasta que lo "
              f"tengan no se les puede medir posición orgánica:\n"
              f"         python scripts/competencia_proponer_terminos.py --raiz <RAIZ>")

    # El cron falla si quedó trabajo sin hacer: un exit 0 con altas pendientes
    # sería un verde que esconde el mismo problema que este script viene a cerrar.
    if despues["altas_pendientes"]:
        print("\nERROR: quedaron altas pendientes después de escribir.")
        return 1
    print("\nListo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
