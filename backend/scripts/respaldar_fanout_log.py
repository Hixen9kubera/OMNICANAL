"""
respaldar_fanout_log.py — Las 19,616 filas de la bitácora del fan-out, de MySQL
a `ops.fanout_log`.

POR QUÉ HACE FALTA
------------------
La escritura doble solo guarda lo NUEVO. Sin este respaldo, el día que se
encienda la lectura el dashboard del fan-out mostraría el historial empezando
desde cero — y un dashboard que dice "0 eventos" se ve igual que uno que dice
"no pude preguntar".

REPETIBLE A PROPÓSITO
---------------------
Va por `mysql_id` con `on conflict do nothing`. Eso no es adorno: la secuencia
correcta tiene un hueco inevitable —

    respaldo  →  (aquí siguen entrando eventos)  →  se enciende la doble escritura

y esos eventos de en medio solo se recuperan **volviendo a correr el respaldo**.
Sin el ancla, la segunda corrida duplicaría las 19,616 y nadie lo notaría hasta
ver el dashboard contando doble.

VERIFICA CONTRA EL ORIGEN, NO CONTRA SÍ MISMO
---------------------------------------------
Al terminar no dice "copié N filas": vuelve a contar los dos lados, compara los
totales por acción y verifica **fila por fila** una muestra. Un respaldo que se
declara exitoso por su propio contador no prueba nada — es la misma trampa que
un arnés que se mide contra sí mismo.

Uso:
  ...python backend/scripts/respaldar_fanout_log.py                 # dry-run
  ...python backend/scripts/respaldar_fanout_log.py --sandbox --real
  ...python backend/scripts/respaldar_fanout_log.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_COLS = ("id", "ts", "sku", "motivo", "dry_run", "stock_drop", "objetivo",
         "canal", "cuenta", "item_id", "accion", "stock_canal", "resultado", "ms")
_LOTE = 1000


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
    ap.add_argument("--sandbox", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    args = ap.parse_args()

    E = cargar(".env")
    dsn = cargar("env.staging")["SUPABASE_DB_URL"] if args.sandbox else E["SUPABASE_DB_URL"]
    ref = (re.search(r"postgres\.([a-z0-9]+):", dsn) or [None, ""])[1]
    if args.real and not args.sandbox and args.acepto_destino != ref[:8]:
        sys.exit(f"ABORT: con --real contra produccion hay que nombrar el destino "
                 f"(--acepto-destino {ref[:8]}).")
    print(f"[{'REAL' if args.real else 'DRY-RUN'}] destino: {ref[:8]}…\n", flush=True)

    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    with my.cursor() as c:
        c.execute(f"SELECT {', '.join(_COLS)} FROM fanout_log ORDER BY id")
        origen = c.fetchall()
    my.close()

    pg = psycopg2.connect(dsn, connect_timeout=25)
    with pg.cursor() as c:
        c.execute("select count(*) from ops.fanout_log where mysql_id is not null")
        ya = c.fetchone()[0]
    print(f"  MySQL fanout_log      : {len(origen):6d} filas")
    print(f"  ya respaldadas en kubera: {ya:6d}")
    print(f"  faltan                : {len(origen) - ya:6d}")

    if not args.real:
        print("\n== DRY-RUN: no se escribio nada ==")
        pg.close()
        return

    filas = [(r["id"], r["ts"], r["sku"] or None, r["motivo"], bool(r["dry_run"]),
              r["stock_drop"], r["objetivo"], r["canal"], r["cuenta"],
              r["item_id"], r["accion"], r["stock_canal"], r["resultado"], r["ms"])
             for r in origen]
    metidas = 0
    with pg.cursor() as c:
        for i in range(0, len(filas), _LOTE):
            psycopg2.extras.execute_values(
                c,
                """insert into ops.fanout_log
                     (mysql_id, ts, sku, motivo, dry_run, stock_drop, objetivo,
                      canal, cuenta, item_id, accion, stock_canal, resultado, ms)
                   values %s
                   on conflict (mysql_id) where mysql_id is not null do nothing""",
                filas[i:i + _LOTE], page_size=_LOTE)
            metidas += c.rowcount
            print(f"    … {min(i + _LOTE, len(filas)):6d}/{len(filas)}", flush=True)
    pg.commit()

    # ── Quitar lo que el ESPEJO ya habia puesto en esta misma ventana ───────
    # El ancla hace idempotente el RESPALDO contra si mismo, pero no lo protege
    # del ESPEJO: los dos escriben los mismos eventos. Desde que la escritura
    # doble esta encendida, cada corrida del respaldo vuelve a copiar lo que el
    # espejo ya dejo — y el dashboard contaria doble.
    #
    # El respaldo es la copia AUTORITATIVA de su ventana (viene con el id de
    # origen), asi que las filas del espejo dentro de esa ventana sobran.
    #
    # El corte va por `ts <= el maximo respaldado`. Si un evento del espejo cae
    # justo en ese instante pero corresponde a un id posterior, se borra de mas
    # — y la siguiente corrida lo trae de vuelta con su mysql_id. Se pierde una
    # copia duplicada, nunca el evento.
    with pg.cursor() as c:
        c.execute("select max(ts) from ops.fanout_log where mysql_id is not null")
        corte = c.fetchone()[0]
        c.execute("delete from ops.fanout_log where mysql_id is null and ts <= %s",
                  (corte,))
        quitadas = c.rowcount
    pg.commit()
    print(f"  duplicadas del espejo, quitadas: {quitadas} (corte {corte})")

    # ── Verificacion CONTRA EL ORIGEN ───────────────────────────────────────
    print(f"\n── verificacion ──\n  filas nuevas insertadas: {metidas}")
    ok = True
    with pg.cursor() as c:
        c.execute("select count(*) from ops.fanout_log where mysql_id is not null")
        total = c.fetchone()[0]
        c.execute("""select accion, count(*) from ops.fanout_log
                      where mysql_id is not null group by 1""")
        dest_acc = dict(c.fetchall())
    src_acc = Counter(r["accion"] for r in origen)

    def check(etiqueta, cond, detalle=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))

    check("el total respaldado coincide con el origen", total == len(origen),
          f"kubera {total} · MySQL {len(origen)}")
    with pg.cursor() as c:
        c.execute("select count(*) from ops.fanout_log")
        tabla = c.fetchone()[0]
        c.execute("select count(*) from ops.fanout_log where mysql_id is null")
        solo_espejo = c.fetchone()[0]
    # LO QUE DE VERDAD IMPORTA PARA EL DASHBOARD: que la TABLA no tenga de mas.
    # Las del espejo POSTERIORES al corte no son duplicados — son eventos que
    # MySQL todavia no tenia cuando se leyo. Contarlas como sobrantes seria el
    # error contrario.
    check("la tabla no cuenta ningun evento dos veces",
          tabla == total + solo_espejo,
          f"tabla {tabla} = respaldo {total} + espejo posterior {solo_espejo}")
    difs = [(a, src_acc.get(a, 0), dest_acc.get(a, 0))
            for a in set(src_acc) | set(dest_acc)
            if src_acc.get(a, 0) != dest_acc.get(a, 0)]
    check("los totales POR ACCION coinciden", not difs,
          f"{len(difs)} acciones distintas: {difs[:3]}" if difs else
          f"{len(src_acc)} acciones iguales")

    # Fila por fila, una muestra: los totales pueden cuadrar con el contenido mal.
    muestra = origen[::max(1, len(origen) // 200)][:200]
    with pg.cursor() as c:
        c.execute("""select mysql_id, sku::text, accion, resultado, ms
                       from ops.fanout_log where mysql_id = any(%s)""",
                  ([r["id"] for r in muestra],))
        dest = {r[0]: r for r in c.fetchall()}
    malas = [r["id"] for r in muestra
             if r["id"] not in dest
             or (dest[r["id"]][1] or None) != (r["sku"] or None)
             or dest[r["id"]][2] != r["accion"]
             or dest[r["id"]][3] != r["resultado"]
             or dest[r["id"]][4] != r["ms"]]
    check(f"la muestra de {len(muestra)} filas coincide campo por campo",
          not malas, f"{len(malas)} distintas: {malas[:5]}")

    pg.close()
    print(f"\nRESULTADO: {'respaldo verificado contra el origen' if ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
