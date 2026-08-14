"""
corregir_fechas_bitacora.py — 21,816 eventos de `ops.channel_submissions` están
fechados el día en que se COPIARON, no el día en que pasaron.

QUÉ PASÓ
--------
El 24-jul-2026 un `TRUNCATE CASCADE` de `etl_core_products` vació la bitácora, y
se reconstruyó con `kubera_mirror.backfill_channel_submissions`. Ese backfill
llenaba `submitted_at` con la fecha real del evento, pero **no pasaba nada para
`created_at`**, así que la columna tomó su `default now()`: el 24-jul.

Resultado medido hoy:

    ml_image_edit_backlog  11,978 filas selladas el 24-jul
    ml_backlog              5,556
    amazon_backlog          4,282
                           ──────
                           21,816   desfase promedio 47 días, el peor 128

Es el MISMO defecto que se corrigió en `crear_logs` (60 filas, hasta 17.6 h),
pero 360 veces más grande — porque allá lo produjo un reproceso ocasional y aquí
una restauración completa.

POR QUÉ SE ARREGLA AHORA Y NO "CUANDO MOLESTE"
----------------------------------------------
Hoy no rompe nada: ningún flujo vivo lee esta tabla (verificado — solo la tocan
scripts, y con `exists`, sin ordenar). Pero la bitácora existe justamente para
ser el registro cuando MySQL se retire, y entonces la pregunta "¿cuándo
intentamos publicar este SKU?" se contestaría con la fecha de una restauración.
Un archivo histórico con las fechas mal no es un archivo histórico.

En `crear_logs` este mismo defecto SÍ hizo daño: el historial busca el último
evento por SKU, y 60 filas mal fechadas se colaban al frente e invertían el
estado de 50 SKUs. Aquí todavía no hay quien las ordene. Se arregla antes de que
lo haya.

QUÉ HACE, EXACTAMENTE
---------------------
    created_at := submitted_at    donde las FECHAS (no las horas) difieren

`submitted_at` es la hora real del evento en MySQL y viajó bien en los tres
casos. Solo se tocan filas con desfase de día completo: las del espejo en vivo
—que escriben con décimas de diferencia— no entran.

La causa raíz ya está tapada: `_up_channel_submissions` acepta `creado` y los
tres payloads del backfill lo pasan (v0.172.0). Este script es para lo ya
escrito; sin ese arreglo, el próximo backfill volvería a sellar mal.

Uso:
  ...python backend/scripts/corregir_fechas_bitacora.py               # dry-run
  ...python backend/scripts/corregir_fechas_bitacora.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_DONDE = ("where submitted_at is not null "
          "and created_at::date <> submitted_at::date")


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
    print(f"[{'REAL' if args.real else 'DRY-RUN'}] destino: {ref[:8]}…\n", flush=True)

    pg = psycopg2.connect(dsn, connect_timeout=25)
    with pg.cursor() as c:
        c.execute(f"""select split_part(detail_ref, ':', 2) as origen, count(*),
                             min(submitted_at)::date, max(submitted_at)::date
                        from ops.channel_submissions {_DONDE}
                       group by 1 order by 2 desc""")
        antes = c.fetchall()
    total = sum(r[1] for r in antes)
    for origen, n, a, b in antes:
        print(f"  {str(origen):<24} {n:6,} filas · eventos reales del {a} al {b}")
    print(f"  {'TOTAL':<24} {total:6,}\n")

    if not total:
        print("== Nada que corregir ==")
        pg.close()
        return
    if not args.real:
        with pg.cursor() as c:
            c.execute(f"""select sku, submitted_at, created_at
                            from ops.channel_submissions {_DONDE}
                           order by submitted_at limit 3""")
            for sku, s, cr in c.fetchall():
                print(f"    {str(sku):<20} pasó {str(s)[:16]} · figura como {str(cr)[:16]}")
        print("\n== DRY-RUN: no se escribió nada ==")
        pg.close()
        return

    with pg.cursor() as c:
        c.execute(f"update ops.channel_submissions set created_at = submitted_at {_DONDE}")
        tocadas = c.rowcount
    pg.commit()

    print("── verificación ──")
    with pg.cursor() as c:
        c.execute(f"select count(*) from ops.channel_submissions {_DONDE}")
        resto = c.fetchone()[0]
        # Que no se haya movido nada MÁS de la cuenta: el total de la tabla
        # tiene que ser el mismo (esto es un UPDATE, no un borrado).
        c.execute("select count(*) from ops.channel_submissions")
        n_tabla = c.fetchone()[0]
        c.execute("""select min(created_at)::date, max(created_at)::date
                       from ops.channel_submissions where submitted_at is not null""")
        rango = c.fetchone()
    ok = resto == 0 and tocadas == total
    print(f"  [{'OK  ' if tocadas == total else 'FALLA'}] filas corregidas: {tocadas:,} de {total:,}")
    print(f"  [{'OK  ' if not resto else 'FALLA'}] filas que siguen mal fechadas: {resto}")
    print(f"  [info] la bitácora tiene {n_tabla:,} filas y ahora abarca "
          f"del {rango[0]} al {rango[1]}")
    pg.close()
    print(f"\nRESULTADO: {'fechas corregidas' if ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
