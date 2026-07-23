"""
comparar_orders.py — El job de deltas del dominio ORDERS (pedidos).

Auditor del dual-write de ventas: lee pedidos_ml en MySQL (fuente de verdad)
y channel.orders en Supabase (espejo v0.16.0 + backfill v0.16.2/3), compara
conteos y fila por fila, y deja acta en migration.reconciliation_runs con la
lista exacta de divergencias. Mismo patrón que comparar_channel.py.

Reglas propias del dominio (distintas a channel):
  1. PEDIDO ≈ INMUTABLE — total/comisión/es_full/creado quedan congelados al
     primer registro (regla de negocio del pedido histórico); solo wc_order_id
     y los estados se mueven. La comparación cubre ambos grupos.
  2. FILAS CALIENTES — un pedido tocado hace <20 min puede estar aún en la
     cola del espejo (2 workers en serie): se excluye de la pasada y se cuenta
     aparte. Reconfirmación a los 75 s para descartar parpadeos.
  3. SKUS: SUBCONJUNTO, NO IGUALDAD — el CSV de MySQL trunca a 255 chars; el
     espejo en vivo manda el array COMPLETO. Delta solo si a Supabase le falta
     un SKU que MySQL sí tiene (mysql ⊆ supabase es lo esperado).
  4. SOLO_EN_SUPABASE ES DELTA — a diferencia de channel (fusión ETL), aquí
     channel.orders nace exclusivamente de pedidos_ml: una fila que MySQL no
     conoce es una anomalía real (los calientes excluidos no cuentan).
  5. creado_at NO se compara (naive vs timestamptz = falsos positivos; está
     congelado por el ON CONFLICT, riesgo nulo).
  6. COMISIÓN 0 EN MYSQL = NO OBSERVADA — el webhook puede llegar antes de que
     ML calcule la comisión; MySQL congela ese 0 para siempre (por diseño),
     pero si el espejo nació de un re-sync posterior congeló el valor YA
     CORREGIDO. Hallazgo real de la primera corrida (23-jul): 7 pedidos con
     comisión 0 en MySQL y comisión real en Supabase — el espejo es MÁS
     correcto que la fuente en ese campo. No es delta.

SOLO LECTURA sobre las tablas comparadas (su única escritura es el acta).
Criterio de salida de la fase: 14 días consecutivos con delta = 0.

Uso:  python backend/scripts/comparar_orders.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

import psycopg2
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
MAX_DETALLE = 200          # divergencias que se guardan con detalle en el acta
VENTANA_CALIENTE_MIN = 20  # pedidos tocados hace menos de esto no se comparan
ESPERA_RECONFIRMA_S = 75   # pausa antes de releer los deltas de la primera pasada

# mismo mapeo que _ESPEJO_ORIGEN (pedidos_ml.py) y el backfill (kubera_mirror)
CANAL_DE_CUENTA = {
    "BEKURA": "mercado_libre", "SANCORFASHION": "mercado_libre",
    "AMAZON": "amazon", "TEMU": "temu", "TIKTOK": "tiktok",
}

CAMPOS = ("wc_order_id", "estado_canal", "estado_wc", "total", "comision",
          "es_fulfillment", "skus")


def cargar_env(nombre: str) -> dict[str, str]:
    """Valores del archivo local; si no existe (Railway cron), variables de
    entorno del proceso. El archivo gana sobre el entorno en local."""
    import os
    vals: dict[str, str] = dict(os.environ)
    p = ROOT / nombre
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


def _num(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v.normalize()
    return Decimal(str(v)).normalize()


def _skus_set(v) -> frozenset[str]:
    """CSV MySQL o array Postgres → set comparable (case-insensitive)."""
    if v is None:
        return frozenset()
    partes = v if isinstance(v, (list, tuple)) else str(v).split(",")
    return frozenset(s.strip().lower() for s in partes if s and s.strip())


def _fila_mysql(r: dict) -> tuple:
    estado_ml = str(r["estado_ml"]).strip() if r["estado_ml"] is not None else None
    estado_wc = str(r["estado_wc"]).strip() if r["estado_wc"] is not None else None
    return (r["wc_order_id"], estado_ml, estado_wc, _num(r["total"]),
            _num(r["comision"]), bool(r["es_full"]), _skus_set(r["skus"]))


def _fila_pg(r: tuple) -> tuple:
    wc_order_id, estado_canal, estado_wc, total, comision, es_full, skus = r
    return (wc_order_id,
            str(estado_canal).strip() if estado_canal is not None else None,
            str(estado_wc).strip() if estado_wc is not None else None,
            _num(total), _num(comision), bool(es_full), _skus_set(skus))


def _texto(v) -> str:
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, frozenset):
        return ",".join(sorted(v))
    return str(v)


def _difiere(vm: tuple, vp: tuple) -> dict:
    """Campos divergentes. NULL en MySQL = no observado (se salta); los SKUs
    se comparan por subconjunto (regla 3)."""
    difs = {}
    for i, campo in enumerate(CAMPOS):
        if vm[i] is None:
            continue
        if campo == "comision" and vm[i] == 0:
            continue  # regla 6: 0 en MySQL = aún no calculada al congelarse
        if campo == "skus":
            faltan = vm[i] - vp[i]
            if faltan:
                difs[campo] = {"mysql": _texto(vm[i]),
                               "supabase": _texto(vp[i]),
                               "faltan_en_supabase": _texto(faltan)}
            continue
        if vm[i] != vp[i]:
            difs[campo] = {"mysql": _texto(vm[i]), "supabase": _texto(vp[i])}
    return difs


def leer_mysql(my_cur) -> tuple[dict, set]:
    """{(canal, cuenta, order_id): valores} de los pedidos fríos + claves calientes."""
    my_cur.execute(
        """SELECT ml_order_id, cuenta, wc_order_id, estado_ml, estado_wc,
                  total, comision, es_full, skus,
                  (COALESCE(actualizado, creado) >= NOW() - INTERVAL %s MINUTE) AS caliente
           FROM pedidos_ml""",
        (VENTANA_CALIENTE_MIN,),
    )
    rows, calientes = {}, set()
    for r in my_cur.fetchall():
        cuenta = (r["cuenta"] or "").strip()
        canal = CANAL_DE_CUENTA.get(cuenta, cuenta.lower() or "desconocido")
        clave = (canal, cuenta, str(r["ml_order_id"]))
        if r["caliente"]:
            calientes.add(clave)  # regla 2: puede seguir en la cola del espejo
            continue
        rows[clave] = _fila_mysql(r)
    return rows, calientes


def leer_pg(pg_cur) -> dict:
    # skus::text[] — psycopg2 no adapta citext[] (llegaría como cadena "{...}")
    pg_cur.execute(
        """select canal, cuenta, external_order_id, wc_order_id, estado_canal,
                  estado_wc, total, comision, es_fulfillment, skus::text[]
           from channel.orders"""
    )
    return {(r[0], r[1], str(r[2])): _fila_pg(r[3:]) for r in pg_cur.fetchall()}


def releer_clave(my_cur, pg_cur, clave: tuple):
    """Segunda lectura de UN pedido en ambos lados (reconfirmación)."""
    canal, cuenta, order_id = clave
    my_cur.execute(
        """SELECT ml_order_id, cuenta, wc_order_id, estado_ml, estado_wc,
                  total, comision, es_full, skus,
                  (COALESCE(actualizado, creado) >= NOW() - INTERVAL %s MINUTE) AS caliente
           FROM pedidos_ml WHERE ml_order_id=%s AND cuenta=%s""",
        (VENTANA_CALIENTE_MIN, order_id, cuenta),
    )
    rm = my_cur.fetchone()
    pg_cur.execute(
        """select wc_order_id, estado_canal, estado_wc, total, comision,
                  es_fulfillment, skus::text[]
           from channel.orders
           where canal=%s and cuenta=%s and external_order_id=%s""",
        (canal, cuenta, order_id),
    )
    rp = pg_cur.fetchone()
    if rm is None:
        return None, (rp and _fila_pg(rp)), False
    return _fila_mysql(rm), (rp and _fila_pg(rp)), bool(rm["caliente"])


def main() -> None:
    # Consolas Windows (cp1252) truenan con ↔/…; en Railway es no-op.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    prod = cargar_env(".env")
    dest = cargar_env("env.staging")  # el proyecto donde escribe el espejo hoy (DEV)
    m = re.search(r"postgres\.([a-z0-9]+):", dest.get("SUPABASE_DB_URL", ""))
    print(f"Comparando MySQL prod (pedidos_ml) ↔ Supabase channel.orders "
          f"({(m.group(1) if m else '?')[:8]}…)")

    my = pymysql.connect(
        host=prod["DB_HOST"], port=int(prod.get("DB_PORT", 3306)), user=prod["DB_USER"],
        password=prod["DB_PASSWORD"], database=prod["DB_NAME"], charset="utf8mb4",
        connect_timeout=15, read_timeout=120, cursorclass=pymysql.cursors.DictCursor,
    )
    my_cur = my.cursor()
    pg = psycopg2.connect(dest["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = True
    pg_cur = pg.cursor()

    # ── Pasada 1: todo contra todo (pedidos fríos) ──────────────────────────
    my_rows, calientes = leer_mysql(my_cur)
    pg_rows = leer_pg(pg_cur)

    solo_my = sorted(set(my_rows) - set(pg_rows))
    # regla 4: aquí SÍ es delta — pero un pedido caliente excluido de MySQL
    # naturalmente "sobra" en Supabase: no cuenta.
    solo_pg = sorted(set(pg_rows) - set(my_rows) - calientes)
    sospechosos = []
    for k in my_rows.keys() & pg_rows.keys():
        difs = _difiere(my_rows[k], pg_rows[k])
        if difs:
            sospechosos.append((k, difs))

    print(f"Pasada 1: mysql_fríos={len(my_rows)} (+{len(calientes)} calientes) "
          f"supabase={len(pg_rows)} | solo_mysql={len(solo_my)} "
          f"solo_supabase={len(solo_pg)} | sospechosos={len(sospechosos)}")

    # ── Pasada 2: reconfirmación de sospechosos (regla 2) ───────────────────
    divergentes, parpadeos = [], 0
    if sospechosos:
        print(f"Esperando {ESPERA_RECONFIRMA_S}s para reconfirmar "
              f"{len(sospechosos)} sospechosos…")
        time.sleep(ESPERA_RECONFIRMA_S)
        for clave, _difs_v1 in sospechosos[: MAX_DETALLE * 2]:
            vm, vp, caliente = releer_clave(my_cur, pg_cur, clave)
            if caliente or vm is None or vp is None:
                parpadeos += 1
                continue
            difs = _difiere(vm, vp)
            if not difs:
                parpadeos += 1
            else:
                divergentes.append({"pedido": clave[2], "canal": clave[0],
                                    "cuenta": clave[1], "columnas": difs})

    limpio = not solo_my and not solo_pg and not divergentes
    resumen = {
        "mysql_frios": len(my_rows), "mysql_calientes_excluidos": len(calientes),
        "supabase": len(pg_rows), "solo_en_mysql": len(solo_my),
        "solo_en_supabase": len(solo_pg),
        "sospechosos_pasada1": len(sospechosos),
        "parpadeos_descartados": parpadeos,
        "divergentes_confirmados": len(divergentes),
    }
    detalle = {
        "solo_en_mysql": [f"{k[2]} ({k[0]}/{k[1]})" for k in solo_my[:MAX_DETALLE]],
        "solo_en_supabase": [f"{k[2]} ({k[0]}/{k[1]})" for k in solo_pg[:MAX_DETALLE]],
        "divergentes": divergentes[:MAX_DETALLE],
    }
    print(f"Pasada 2: parpadeos={parpadeos} divergentes_confirmados={len(divergentes)} "
          f"-> {'DELTA = 0' if limpio else 'CON DELTAS'}")
    for d in divergentes[:5]:
        print(f"    {d['pedido']} ({d['canal']}/{d['cuenta']}): " + "; ".join(
            f"{c} {v['mysql']} vs {v['supabase']}" for c, v in d["columnas"].items()))

    my_cur.close()
    my.close()

    resultado = "ok" if limpio else "con_deltas"
    pg_cur.execute("set session characteristics as transaction read write; "
                   "set default_transaction_read_only = off;")
    pg_cur.execute(
        """insert into migration.reconciliation_runs (dominio, descripcion, conteos, checksums, resultado)
           values ('orders-deltas', 'job de deltas del dual-write de pedidos', %s, %s, %s)""",
        (json.dumps({"pedidos_ml_vs_channel_orders": resumen}),
         json.dumps(detalle, default=str)[:100000], resultado),
    )
    pg_cur.close()
    pg.close()
    print(f"\nActa registrada en migration.reconciliation_runs → resultado: {resultado}")
    sys.exit(0 if limpio else 2)


if __name__ == "__main__":
    main()
