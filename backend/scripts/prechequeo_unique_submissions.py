"""
prechequeo_unique_submissions.py — SOLO LECTURA. Mide si `ops.channel_submissions`
aguanta un índice único sobre `detail_ref` antes de crearlo (migración 0016).

POR QUÉ
-------
`kubera_mirror._up_channel_submissions` (línea 379) declara "idempotencia por
detail_ref", pero la implementa con SELECT-luego-INSERT en Python, sin índice
único detrás. El espejo corre con pool 6 + 2 workers: dos workers pueden pasar
el SELECT a la vez e insertar la misma fila. Este script cuenta si eso ya pasó.

NO es el candado que sugirió el consejo. Un unique sobre
(canal, submission_id, sku) ROMPERÍA producción: para ML `submission_id` es el
`ml_item_id` (kubera_mirror:851) y se reusa entre los eventos 'alta',
'actualizacion', 'imagen' y 'pausa' del MISMO sku. Verificado antes de escribir
esto.

Uso:
    backend/.venv/Scripts/python.exe backend/scripts/prechequeo_unique_submissions.py
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

DSN = os.getenv("SUPABASE_DB_URL") or os.getenv("KUBERA_DB_URL") or ""
if not DSN:
    sys.exit("FALTA SUPABASE_DB_URL (o KUBERA_DB_URL) en el entorno.")

DESTINO = "produccion (tukwcvsi)" if "tukwcvsi" in DSN else \
          "sandbox (yvootpbz)" if "yvootpbz" in DSN else "DESCONOCIDO"

CONSULTAS = [
    ("total de filas",
     "select count(*) from ops.channel_submissions"),

    ("filas con detail_ref nulo (el índice parcial las ignora)",
     "select count(*) from ops.channel_submissions where detail_ref is null"),

    ("detail_ref DISTINTOS no nulos",
     "select count(distinct detail_ref) from ops.channel_submissions "
     "where detail_ref is not null"),

    ("*** COLISIONES: detail_ref repetidos (debe dar 0) ***",
     "select count(*) from (select detail_ref from ops.channel_submissions "
     "where detail_ref is not null group by detail_ref having count(*) > 1) x"),

    ("filas de más que habría que resolver antes de crear el índice",
     "select coalesce(sum(n - 1), 0) from (select count(*) n "
     "from ops.channel_submissions where detail_ref is not null "
     "group by detail_ref having count(*) > 1) x"),

    ("reparto por canal",
     "select canal, count(*) from ops.channel_submissions "
     "group by canal order by 2 desc"),

    ("¿ya hay filas de walmart o tiktok?",
     "select canal, count(*) from ops.channel_submissions "
     "where canal in ('walmart','tiktok') group by canal"),

    ("prueba de que submission_id SE REUSA (por qué el otro unique rompería)",
     "select count(*) from (select canal, submission_id, sku "
     "from ops.channel_submissions where submission_id is not null "
     "group by 1,2,3 having count(*) > 1) x"),
]


def main() -> int:
    print(f"DESTINO: {DESTINO}\n")
    colisiones = None
    con = psycopg2.connect(DSN)
    # CANDADO POR TRANSACCIÓN, NO POR SESIÓN. `set_session(readonly=True)` deja
    # el ajuste pegado a la conexión, y estas DSN entran por el pooler de
    # Supabase en modo TRANSACCIÓN: varios clientes se turnan la MISMA conexión
    # del servidor, así que el candado lo hereda quien la tome después. El
    # 18-ago-2026 tumbó dos escrituras de negocio en producción — el registro de
    # una venta y una tanda de 75 publicaciones — con
    # `ReadOnlySqlTransaction: cannot execute INSERT in a read-only transaction`.
    # `set transaction read only` muere con la transacción y no contamina.
    con.autocommit = False
    try:
        with con.cursor() as cur:
            cur.execute("set transaction read only")
            for titulo, sql in CONSULTAS:
                cur.execute(sql)
                filas = cur.fetchall()
                print(f"── {titulo}")
                for f in filas:
                    print("   " + " · ".join(str(c) for c in f))
                if not filas:
                    print("   (sin filas)")
                if "COLISIONES" in titulo:
                    colisiones = filas[0][0]
                print()
    finally:
        con.rollback()   # cierra la transacción de solo-lectura sin escribir
        con.close()

    print("=" * 70)
    if colisiones == 0:
        print("VEREDICTO: se puede crear el índice único sobre detail_ref.")
        return 0
    print(f"VEREDICTO: NO crear el índice todavía — hay {colisiones} detail_ref "
          f"repetidos.\n           Resolverlos primero (conservar el id más "
          f"chico de cada grupo).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
