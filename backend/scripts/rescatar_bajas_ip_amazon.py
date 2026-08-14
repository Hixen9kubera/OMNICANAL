"""
rescatar_bajas_ip_amazon.py — Los 44 SKUs que Amazon tumbó por marca/IP y cuyo
MOTIVO solo vive en MySQL (primer sub-paso del PASO 3, docs/PLAN_31_TABLAS.md).

    amazon_progress (success=0)  →  ops.channel_submissions (operacion='baja_ip')

QUÉ SE RESCATA Y POR QUÉ IMPORTA
--------------------------------
`channel.listings` YA dice `DELETED` para los 44: la pregunta "¿está publicado?"
está contestada en kubera y no se pierde nada. Lo que NO está en ningún lado de
kubera es **por qué**:

    bulk_deleted_ip_brand      30 SKUs
    amz_infringement_deleted   14 SKUs

Eso no es "el intento de publicación falló". Es **Amazon dando de baja el
listing por infracción de marca o propiedad intelectual**. La diferencia es
operativa y cuesta dinero: un SKU que dice `DELETED` a secas se ve como
candidato a re-publicar, y re-publicarlo lo vuelve a tumbar — con el historial
de infracciones de la cuenta de por medio.

Es exactamente lo que el consejo advirtió del grupo 4: `channel.listings` es
superset **solo de los éxitos**; el motivo del fracaso no cabe ahí.

POR QUÉ SON EXACTAMENTE ESTOS 44 Y NO LOS 126
---------------------------------------------
`amazon_progress` tiene 126 SKUs con `success=0`. De ésos, 82 ya tienen su
motivo en la bitácora porque el espejo los capturó desde `amazon_backlog`. Los
44 restantes son de **30-jun a 6-jul**, todos ANTERIORES al arranque del espejo
(24-jul): nunca hubo quien los copiara. Verificado: cero de los 44 es posterior
a esa fecha.

(De paso: los "269 fallidos" de ML del plan eran FILAS, no SKUs — `ml_progress`
tiene llave `cuenta:sku`. Son 138 SKUs y los 138 ya están en la bitácora. Ese
lado no necesita rescate.)

DECISIONES QUE NO SON OBVIAS
----------------------------
1. **`created_at` = `updated_at` del origen, no `now()`.** La bitácora se lee
   por orden cronológico; sellar un evento de junio con la fecha de la copia lo
   pondría al frente de la fila y contaría una historia falsa. Es el mismo error
   que hubo que corregir en `crear_logs` (60 filas con hasta 17.6 h de desfase
   invirtieron el estado de 50 SKUs).

2. **`operacion='baja_ip'` y no `'alta'`.** No fue un alta que salió mal: fue
   una baja impuesta desde afuera. Meterla como alta fallida haría creer que
   alguien intentó publicar el 30-jun, y no es lo que pasó — `published_at` de
   los 44 es del 23-jun y salió BIEN.

3. **`submitted_at` = `last_submitted`** = cuándo Amazon aplicó la baja.
   **`published_at` se preserva**: son publicaciones que sí vivieron una semana.

4. **Idempotente por `detail_ref`**, no por conteo: re-ejecutar no duplica. Y
   solo toca SKUs SIN ningún evento de fallo, así que jamás pisa lo que el
   espejo ya trajo.

Uso:
  ...python backend/scripts/rescatar_bajas_ip_amazon.py               # dry-run
  ...python backend/scripts/rescatar_bajas_ip_amazon.py --real --acepto-destino tukwcvsi
  ...python backend/scripts/rescatar_bajas_ip_amazon.py --real --acepto-destino yvootpbz --sandbox
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
    ap.add_argument("--real", action="store_true", help="escribe (default: dry-run)")
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

    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    pg = psycopg2.connect(dsn, connect_timeout=25)

    # ── 1) El hueco, calculado y no supuesto ────────────────────────────────
    with my.cursor() as c:
        c.execute("SELECT sku, status, error_label, issue_count, last_submitted, "
                  "published_at, updated_at FROM amazon_progress WHERE success = 0")
        fallidos = c.fetchall()
    with pg.cursor() as c:
        c.execute("select distinct sku::text from ops.channel_submissions "
                  "where canal = 'amazon' and success is false")
        con_motivo = {r[0].lower() for r in c.fetchall()}
        c.execute("select detail_ref from ops.channel_submissions "
                  "where detail_ref like 'mysql:amazon_progress:%%'")
        ya_rescatados = {r[0] for r in c.fetchall()}

    huecos = [f for f in fallidos if str(f["sku"]).lower() not in con_motivo]
    nuevos = [f for f in huecos
              if f"mysql:amazon_progress:{f['sku']}" not in ya_rescatados]

    print(f"  amazon_progress con success=0 : {len(fallidos):3d} SKUs")
    print(f"  ya tienen motivo en bitácora  : {len(fallidos) - len(huecos):3d}")
    print(f"  HUECO                         : {len(huecos):3d}")
    print(f"  a insertar (no rescatados aún): {len(nuevos):3d}\n")
    if huecos:
        print("  motivos:", dict(Counter(str(f["error_label"]) for f in huecos)))
        fechas = sorted(f["updated_at"] for f in huecos if f["updated_at"])
        print(f"  fechas de la baja: {fechas[0]} → {fechas[-1]}")
        posteriores = sum(1 for d in fechas if d.strftime("%Y-%m-%d") >= "2026-07-24")
        print(f"  posteriores al arranque del espejo (24-jul): {posteriores} "
              f"{'← REVISAR: el espejo debió traerlos' if posteriores else '(ninguno, como se esperaba)'}\n")

    if not nuevos:
        print("== Nada que rescatar ==")
        my.close(); pg.close()
        return
    if not args.real:
        for f in nuevos[:5]:
            print(f"    {f['sku']:<18} {f['error_label']:<26} "
                  f"baja {str(f['last_submitted'])[:16]} · publicada {str(f['published_at'])[:10]}")
        print("\n== DRY-RUN: no se escribió nada ==")
        my.close(); pg.close()
        return

    # ── 2) Rescatar ─────────────────────────────────────────────────────────
    # `created_at` = `updated_at` del origen: la bitácora se lee en orden y un
    # evento de junio sellado con la fecha de hoy contaría una historia falsa.
    with pg.cursor() as c:
        psycopg2.extras.execute_values(
            c,
            """insert into ops.channel_submissions
                 (canal, cuenta, sku, submission_id, operacion, status, success,
                  error_resumen, detail_ref, submitted_at, published_at, created_at)
               values %s""",
            [("amazon", "AMAZON", f["sku"], None, "baja_ip", f["status"], False,
              f["error_label"], f"mysql:amazon_progress:{f['sku']}",
              f["last_submitted"], f["published_at"], f["updated_at"])
             for f in nuevos],
            template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", page_size=200)
    pg.commit()

    # ── 3) Verificación: el hueco tiene que quedar en CERO ──────────────────
    print("── verificación ──")
    with pg.cursor() as c:
        c.execute("select distinct sku::text from ops.channel_submissions "
                  "where canal = 'amazon' and success is false")
        ahora = {r[0].lower() for r in c.fetchall()}
    resto = [f["sku"] for f in fallidos if str(f["sku"]).lower() not in ahora]
    ok = not resto
    print(f"  [{'OK  ' if ok else 'FALLA'}] SKUs de amazon_progress sin motivo en "
          f"bitácora: {len(resto)} (antes {len(huecos)})")

    # Que la FECHA haya viajado, no solo la fila: es el error que ya nos costó
    # una corrección en crear_logs.
    with pg.cursor() as c:
        c.execute("""select count(*) from ops.channel_submissions
                      where operacion = 'baja_ip'
                        and created_at > timestamptz '2026-07-24'""")
        tarde = c.fetchone()[0]
        c.execute("""select min(created_at), max(created_at), count(*)
                       from ops.channel_submissions where operacion = 'baja_ip'""")
        mn, mx, n = c.fetchone()
    print(f"  [{'OK  ' if not tarde else 'FALLA'}] fechas del evento preservadas: "
          f"{n} filas entre {str(mn)[:10]} y {str(mx)[:10]} "
          f"({tarde} selladas con fecha de copia)")
    ok &= not tarde

    my.close(); pg.close()
    print(f"\nRESULTADO: {'rescatado y verificado' if ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
