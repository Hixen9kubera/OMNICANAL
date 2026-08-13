"""
clonar_a_sandbox.py — Copia datos de la kubera de PRODUCCIÓN al SANDBOX.

POR QUÉ EXISTE. La regla del sandbox cambió el 12-ago (Eduardo): dejó de ser
"vacío a propósito" y ahora lleva clones de producción, porque sin datos que se
parezcan a los de verdad cualquier cambio de UI o de SQL se valida a ciegas. El
mismo día, un sandbox vacío dejó sin poder verificar dos tarjetas nuevas de la
tabla de Análisis.

POR QUÉ NO SE USA sembrar_sandbox.py. Ese script lee de MySQL producción —que
salió de la arquitectura— y solo cubre tres tablas (core.products,
costing.costos_validados, costing.costos_finales). Le faltan channel.listings y
channel.orders, que son las que alimentan la tabla de Análisis. Este va de
kubera a sandbox, Postgres a Postgres.

REGLA DE ORO: sobre producción SOLO `SELECT`. Ni un INSERT, ni un UPDATE, ni un
TRUNCATE. Las escrituras van todas al sandbox. Dos candados lo respaldan:
  · el ORIGEN tiene que ser la ref de producción (si no, aborta)
  · el DESTINO no puede serlo (si no, aborta)

QUÉ NO SE COPIA, a propósito:
  · ops.ml_tokens   — credenciales de Mercado Libre
  · core.usuarios   — cuentas de acceso al panel
Un sandbox es para probar lecturas, no para tener una segunda copia de los
secretos.

channel.sales_daily y channel.sales_daily_completa son VISTAS sobre orders +
order_items: se reconstruyen solas al copiar esas dos, no se listan aquí.

USO
  # ver qué haría, sin escribir (por defecto)
  backend/.venv/Scripts/python.exe backend/scripts/clonar_a_sandbox.py

  # clonar de verdad
  backend/.venv/Scripts/python.exe backend/scripts/clonar_a_sandbox.py --real

  # limitar las tablas de historia, que son las grandes
  backend/scripts/clonar_a_sandbox.py --real --tope-historia 20000

El DSN de producción se lee de --origen, de la variable KUBERA_PROD_DSN, o del
archivo que se le indique con --origen-archivo (recomendado: así no queda en el
historial de la terminal).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REF_PROD = "tukwcvsi"

# Orden que respeta las llaves foráneas: cada tabla va después de aquellas de
# las que depende. Sale del grafo real de pg_constraint, no de memoria.
#   channels ← accounts ← listings/orders/costos_finales
#   products ← listings/product_category/costos_*
#   orders   ← order_items
TABLAS: list[tuple[str, str | None]] = [
    # (tabla, columna de fecha para topar historia; None = se copia entera)
    ("core.channels", None),
    ("core.canonical_fields", None),
    ("core.accounts", None),
    ("core.products", None),
    ("channel.categories", None),
    ("channel.product_category", None),
    ("channel.field_requirements", None),
    ("channel.listings", None),
    ("channel.orders", "creado_at"),
    ("channel.order_items", None),
    ("costing.pricing_params", None),
    ("costing.fx_rates", None),
    ("costing.costos_validados", None),
    ("costing.costos_finales", None),
    ("analytics.sales_daily_hist", "date"),
    ("analytics.stock_hist", None),
    ("analytics.temporadas", None),
    # `changed_at`, NO `captured_at`: el nombre se verificó contra
    # information_schema. Suponerlo costó una corrida completa de 16 tablas.
    ("channel.listing_history", "changed_at"),
    ("costing.cost_history", "created_at"),
    ("ops.process_log", "created_at"),
    ("ops.migration_issues", "created_at"),
    ("migration.reconciliation_runs", "created_at"),
]

# Nunca. Ver el encabezado.
PROHIBIDAS = {"ops.ml_tokens", "core.usuarios"}


def a_modo_sesion(dsn: str) -> str:
    """
    Pasa el DSN del pooler de TRANSACCIÓN (6543) al de SESIÓN (5432).

    El .env apunta al 6543 porque es lo que le conviene a la app: muchas
    conexiones cortas. Pero ese modo NO sostiene cursores con nombre ni
    transacciones largas —reparte cada sentencia entre conexiones distintas—,
    y este script necesita las dos cosas: el cursor del servidor para no
    tragarse 66,890 filas de un fetch, y una transacción REPEATABLE READ para
    que las 21 tablas vengan del mismo instante.

    Con el 6543 la conexión se cayó dos veces, en core.products y en
    channel.field_requirements. El 5432 del mismo host es modo sesión y las
    aguanta.
    """
    return re.sub(r"(pooler\.supabase\.com):6543\b", r"\1:5432", dsn)


def ref_de(dsn: str) -> str:
    m = re.search(r"postgres\.([a-z0-9]+):", dsn or "") or re.search(r"db\.([a-z0-9]{20})\.", dsn or "")
    return m.group(1) if m else ""


def dsn_origen(args) -> str:
    if args.origen_archivo:
        return Path(args.origen_archivo).read_text(encoding="utf-8").strip()
    return (args.origen or os.environ.get("KUBERA_PROD_DSN") or "").strip()


def columnas(cur, tabla: str) -> list[str]:
    esq, nom = tabla.split(".")
    cur.execute("""select column_name from information_schema.columns
                    where table_schema=%s and table_name=%s
                      and is_generated='NEVER' order by ordinal_position""", (esq, nom))
    return [r[0] for r in cur.fetchall()]


def tiene_identidad_always(cur, tabla: str) -> bool:
    """
    ¿La tabla tiene una columna de identidad GENERATED ALWAYS?

    `is_generated` NO las delata —eso marca las columnas calculadas— y por eso
    se colaron en el filtro de columnas(): el id de channel.listing_history pasó
    el filtro y el destino lo rechazó. Se detectan con `is_identity`.

    Importa conservar el id original en vez de dejar que se regenere: si algo
    apunta a esas filas, un id nuevo rompería la referencia y el clon dejaría de
    ser fiel.
    """
    esq, nom = tabla.split(".")
    cur.execute("""select 1 from information_schema.columns
                    where table_schema=%s and table_name=%s
                      and is_identity='YES' and identity_generation='ALWAYS'
                    limit 1""", (esq, nom))
    return cur.fetchone() is not None


def main() -> None:
    ap = argparse.ArgumentParser(description="Clona kubera producción → sandbox (lectura en prod).")
    ap.add_argument("--real", action="store_true", help="escribe en el sandbox (default: dry-run)")
    ap.add_argument("--origen", default="", help="DSN de producción (o usa --origen-archivo)")
    ap.add_argument("--origen-archivo", default="", help="archivo con el DSN de producción en una línea")
    ap.add_argument("--destino", default="", help="DSN del sandbox (default: SUPABASE_DB_URL del .env)")
    ap.add_argument("--tope-historia", type=int, default=50000,
                    help="máximo de filas por tabla de historia, las más recientes (default 50000)")
    ap.add_argument("--solo", default="", help="clonar solo estas tablas, separadas por coma")
    ap.add_argument("--lote", type=int, default=5000,
                    help="filas por lote al leer y escribir (default 5000; bajarlo si el pooler corta)")
    ap.add_argument("--sin-modo-sesion", action="store_true",
                    help="no reescribir el puerto 6543 a 5432 (ver a_modo_sesion)")
    args = ap.parse_args()

    origen = dsn_origen(args)
    if not origen:
        sys.exit("ABORT: falta el DSN de produccion (--origen, --origen-archivo o KUBERA_PROD_DSN).")

    destino = args.destino
    if not destino:
        from config import settings  # noqa: E402 — solo si hace falta
        destino = settings.supabase_db_url or ""
    if not destino:
        sys.exit("ABORT: falta el DSN del sandbox.")

    r_ori, r_des = ref_de(origen), ref_de(destino)
    if not r_ori.startswith(REF_PROD):
        sys.exit(f"ABORT: el ORIGEN no es la kubera de produccion (ref {r_ori[:8] or '?'}). "
                 "Clonar de otra cosa no tiene sentido.")
    if r_des.startswith(REF_PROD):
        sys.exit("ABORT: el DESTINO es PRODUCCION. Este script jamas escribe ahi.")
    if r_ori == r_des:
        sys.exit("ABORT: origen y destino son la misma base.")

    # Se valida la DECLARACIÓN completa, no la lista ya filtrada por --solo: lo
    # que hay que impedir es que alguien agregue una tabla prohibida a TABLAS,
    # y ese error no lo atrapa revisar el subconjunto que se pidió esta vez.
    malas = {t for t, _ in TABLAS} & PROHIBIDAS
    if malas:
        sys.exit(f"ABORT: {sorted(malas)} está en TABLAS y no se clona nunca "
                 "(credenciales / cuentas de acceso). Quítala de la lista.")

    pedidas = {s.strip() for s in args.solo.split(",")} if args.solo else set()
    if pedidas & PROHIBIDAS:
        sys.exit(f"ABORT: {sorted(pedidas & PROHIBIDAS)} no se clona nunca.")
    desconocidas = pedidas - {t for t, _ in TABLAS}
    if desconocidas:
        sys.exit(f"ABORT: --solo nombra tablas que no están en TABLAS: {sorted(desconocidas)}")

    tablas = [t for t in TABLAS if not pedidas or t[0] in pedidas]

    if not args.sin_modo_sesion:
        origen, destino = a_modo_sesion(origen), a_modo_sesion(destino)

    puerto = lambda d: (re.search(r":(\d+)/", d) or [None, "?"])[1]  # noqa: E731
    print(f"origen  (SOLO LECTURA): {r_ori[:8]}...  puerto {puerto(origen)}")
    print(f"destino (escritura)   : {r_des[:8]}...  puerto {puerto(destino)}")
    print(f"modo: {'CLONANDO' if args.real else 'DRY-RUN (no escribe nada)'}\n")

    ori = psycopg2.connect(origen)
    # readonly a nivel de SESIÓN: el candado contra escribir en producción no
    # depende solo de que las consultas de aquí sean SELECT. Sin autocommit,
    # porque los cursores con nombre (los que evitan el fetch gigante) exigen
    # estar dentro de una transacción.
    # UNA SOLA FOTO. Producción está viva: en la primera corrida se creó una
    # cuenta ENTRE que el clon leyó core.accounts (4 filas) y leyó
    # channel.listings, y el sandbox rechazó las publicaciones de esa 5ª cuenta
    # por llave foránea. Leer todo en una transacción REPEATABLE READ garantiza
    # que las 21 tablas vengan del mismo instante y el clon sea consistente.
    ori.set_session(readonly=True, autocommit=False,
                    isolation_level=psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ)
    # JSON SIN DESERIALIZAR. Por omisión psycopg2 convierte json/jsonb a objetos
    # de Python al leer, y al reinsertarlos se adaptan como texto plano: el valor
    # `"Generic"` volvía como el str Generic —sin comillas— y Postgres lo
    # rechazaba por no ser JSON válido. Leyéndolos como texto crudo, el JSON
    # viaja intacto y el destino lo vuelve a parsear.
    psycopg2.extras.register_default_json(conn_or_curs=ori, loads=lambda s: s)
    psycopg2.extras.register_default_jsonb(conn_or_curs=ori, loads=lambda s: s)
    des = psycopg2.connect(destino)

    # PRE-VUELO. Los dos esquemas NO son idénticos: analytics.temporadas existe
    # en el sandbox y no en producción. Detectarlo de golpe y saltarlo es mejor
    # que reventar en la tabla 17 dejando el clon a medias.
    def existe(cur, tabla: str) -> bool:
        esq, nom = tabla.split(".")
        cur.execute("""select 1 from information_schema.tables
                        where table_schema=%s and table_name=%s""", (esq, nom))
        return cur.fetchone() is not None

    with ori.cursor() as co, des.cursor() as cd:
        faltan_ori = [t for t, _ in tablas if not existe(co, t)]
        faltan_des = [t for t, _ in tablas if not existe(cd, t)]
    if faltan_ori:
        print(f"  se saltan, no existen en PRODUCCION: {faltan_ori}")
    if faltan_des:
        print(f"  se saltan, no existen en el SANDBOX : {faltan_des}")
    if faltan_ori or faltan_des:
        print()
    saltar = set(faltan_ori) | set(faltan_des)
    tablas = [t for t in tablas if t[0] not in saltar]

    total = 0
    print("  %-34s %10s %10s" % ("tabla", "en prod", "copiadas"))
    print("  " + "-" * 58)
    try:
        with ori.cursor() as co, des.cursor() as cd:
            for tabla, col_fecha in tablas:
                co.execute(f"select count(*) from {tabla}")
                n_prod = co.fetchone()[0]

                cols = columnas(co, tabla)
                lista = ", ".join(f'"{c}"' for c in cols)
                sql = f"select {lista} from {tabla}"
                if col_fecha and n_prod > args.tope_historia:
                    sql += f" order by {col_fecha} desc limit {args.tope_historia}"

                if not args.real:
                    copiadas = min(n_prod, args.tope_historia) if col_fecha else n_prod
                    print("  %-34s %10d %10d" % (tabla, n_prod, copiadas))
                    total += copiadas
                    continue

                # POR LOTES, con cursor del SERVIDOR. Traer la tabla entera con
                # fetchall() tumbó la conexión en core.products (22,284 filas):
                # el pooler de Supabase corta el SSL en fetches grandes. Un
                # cursor con nombre deja los datos en el servidor y los va
                # entregando de a `--lote`, así que nunca hay un fetch enorme.
                cd.execute(f"truncate {tabla} cascade")
                overriding = " overriding system value" if tiene_identidad_always(co, tabla) else ""
                copiadas = 0
                with ori.cursor(name=f"clon_{tabla.replace('.', '_')}") as stream:
                    stream.itersize = args.lote
                    stream.execute(sql)
                    while True:
                        filas = stream.fetchmany(args.lote)
                        if not filas:
                            break
                        psycopg2.extras.execute_values(
                            cd, f"insert into {tabla} ({lista}){overriding} values %s",
                            filas, page_size=1000)
                        copiadas += len(filas)
                        # El avance con \r solo si hay terminal: al redirigir la
                        # salida, el retorno de carro no corta línea y se come
                        # el renglón final de cada tabla.
                        if sys.stdout.isatty():
                            print("    %-32s %10d ..." % (tabla, copiadas), end="\r", flush=True)
                # SIN commit por tabla: si algo falla a media lista, un commit
                # parcial deja el sandbox con unas tablas nuevas y otras viejas,
                # que es peor que dejarlo como estaba. Se confirma todo al final.
                print("  %-34s %10d %10d" % (tabla, n_prod, copiadas))
                total += copiadas
            # SECUENCIAS AL DÍA. Al copiar ids explícitos, el contador de cada
            # columna de identidad se queda donde estaba y el siguiente INSERT
            # del sandbox chocaría con una llave ya usada. Se adelanta al máximo
            # copiado. Sin esto el clon se ve bien y falla en la primera
            # escritura, que es la peor forma de fallar.
            if args.real:
                for tabla, _ in tablas:
                    cd.execute("""select column_name from information_schema.columns
                                   where table_schema=%s and table_name=%s
                                     and is_identity='YES'""", tuple(tabla.split(".")))
                    for (col,) in cd.fetchall():
                        cd.execute(
                            f"select setval(pg_get_serial_sequence('{tabla}', '{col}'), "
                            f"coalesce((select max(\"{col}\") from {tabla}), 1))")
                        print("  secuencia al día: %s.%s" % (tabla, col))

        if args.real:
            des.commit()
    except Exception as exc:  # noqa: BLE001
        des.rollback()
        print(f"\nFALLO (sandbox intacto, se deshizo todo): {exc}")
        raise
    finally:
        ori.close()
        des.close()

    print("  " + "-" * 58)
    print("  %-34s %21d" % ("TOTAL", total))
    if not args.real:
        print("\n  DRY-RUN: no se escribio nada. Repetir con --real para clonar.")


if __name__ == "__main__":
    main()
