"""
comparar_channel.py — El job de deltas del dominio CHANNEL (F3).

Auditor del dual-write: lee canal_inventario en MySQL (fuente de verdad) y
channel.listings en Supabase (espejo), compara en 3 niveles — conteos, sumas
implícitas por campo, fila por fila — y deja acta en
migration.reconciliation_runs con la lista exacta de divergencias.

Reglas propias del dominio (distintas a costos):
  1. FOTO MOVIDA — canal_inventario se reescribe cada 15 min; una fila tocada
     hace <20 min puede estar a segundos del espejo (que corre en hilo aparte).
     Esas filas "calientes" se excluyen de la pasada y se cuentan aparte.
  2. RECONFIRMACIÓN — los deltas de la primera pasada se releen tras una
     espera; si ya coinciden (o la fila se volvió caliente), era el parpadeo
     del sync, no un error. Solo lo persistente va al acta como delta.
  3. NULL = NO OBSERVADO — el espejo conserva el valor anterior cuando el
     lector no trajo el dato (Amazon por lote no trae stock FBM). Un NULL en
     MySQL no se compara: no es divergencia que Supabase recuerde el último
     valor real.
  4. SOLO_EN_SUPABASE ES INFORMATIVO — channel.listings nació de la fusión de
     3 tablas (ml_progress + amazon_progress + canal_inventario); es normal y
     correcto que tenga listings que canal_inventario aún no conoce. No cuenta
     como delta. Lo que SÍ es delta es solo_en_mysql (el espejo perdió filas).

SOLO LECTURA sobre las tablas comparadas (su única escritura es el acta).
Criterio de salida de la fase: 14 días consecutivos con delta = 0.

Uso:  python backend/scripts/comparar_channel.py
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
VENTANA_CALIENTE_MIN = 20  # filas de MySQL tocadas hace menos de esto no se comparan
ESPERA_RECONFIRMA_S = 75   # pausa antes de releer los deltas de la primera pasada

# misma regla que el espejo: las tablas viejas usan cuenta='' en canales mono-cuenta
CUENTA_DEFAULT = {"mercado_libre": "BEKURA", "amazon": "AMAZON", "general": "GENERAL"}

# columnas comparadas: (nombre lógico, mysql, supabase)
CAMPOS = ("precio", "stock_own", "stock_full", "es_full", "situacion", "listing_id")


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


def sku_valido(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or len(s) > 100 or re.search(r"\s", s):
        return None  # inválidos conocidos: el espejo los salta (inventariados en el Excel)
    return s


def _num(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v.normalize()
    return Decimal(str(v)).normalize()


def _fila_mysql(r: dict) -> tuple:
    """Valores comparables de una fila de canal_inventario (None = no observado)."""
    canal = r["canal"]
    stock_full = r["stock_full"] if canal == "mercado_libre" else r["stock_fba"]
    situacion = str(r["situacion"]).strip() if r["situacion"] is not None else None
    listing_id = str(r["item_id"]).strip() if r["item_id"] not in (None, "") else None
    return (_num(r["precio"]), _num(r["stock_real"]), _num(stock_full),
            bool(r["es_full"]), situacion, listing_id)


def _fila_pg(r: tuple) -> tuple:
    """(price, stock_own, stock_full, is_fulfillment, situacion, listing_id) ya en orden."""
    price, stock_own, stock_full, is_full, situacion, listing_id = r
    return (_num(price), _num(stock_own), _num(stock_full), bool(is_full),
            str(situacion).strip() if situacion is not None else None,
            str(listing_id).strip() if listing_id not in (None, "") else None)


def _texto(v) -> str:
    # Decimal.normalize() imprime 50 como '5E+1'; para el acta, notación fija.
    return format(v, "f") if isinstance(v, Decimal) else str(v)


def _difiere(vm: tuple, vp: tuple) -> dict:
    """Campos divergentes bajo la regla NULL-en-MySQL = no observado (se salta)."""
    difs = {}
    for i, campo in enumerate(CAMPOS):
        if vm[i] is None:
            continue  # regla 3: el lector no lo trajo; el espejo recuerda el último real
        if vm[i] != vp[i]:
            difs[campo] = {"mysql": _texto(vm[i]), "supabase": _texto(vp[i])}
    return difs


def leer_mysql(my_cur) -> tuple[dict, int, int]:
    """{(sku_lower, canal, cuenta_legacy): (sku, valores)} de las filas frías."""
    my_cur.execute(
        """SELECT sku, canal, cuenta, item_id, precio, stock_real, stock_full,
                  stock_fba, es_full, situacion,
                  (updated_at >= NOW() - INTERVAL %s MINUTE) AS caliente
           FROM canal_inventario""",
        (VENTANA_CALIENTE_MIN,),
    )
    rows, calientes, invalidos = {}, 0, 0
    for r in my_cur.fetchall():
        s = sku_valido(r["sku"])
        if s is None:
            invalidos += 1
            continue
        if r["caliente"]:
            calientes += 1
            continue  # regla 1: recién reescrita por el sync; mañana será fría
        legacy = (r["cuenta"] or "").strip() or CUENTA_DEFAULT.get(r["canal"], "")
        rows[(s.lower(), r["canal"], legacy)] = (s, _fila_mysql(r))
    return rows, calientes, invalidos


def leer_pg(pg_cur) -> dict:
    pg_cur.execute(
        """select l.sku, l.canal, a.legacy_code, l.price, l.stock_own,
                  l.stock_full, l.is_fulfillment, l.situacion, l.listing_id
           from channel.listings l join core.accounts a on a.id = l.account_id"""
    )
    return {(str(r[0]).lower(), r[1], r[2]): (str(r[0]), _fila_pg(r[3:]))
            for r in pg_cur.fetchall()}


def releer_clave(my_cur, pg_cur, clave: tuple) -> tuple[tuple | None, tuple | None, bool]:
    """Segunda lectura de UNA clave en ambos lados (para la reconfirmación)."""
    sku_l, canal, legacy = clave
    cuenta_my = "" if legacy == CUENTA_DEFAULT.get(canal) and canal != "mercado_libre" else legacy
    my_cur.execute(
        """SELECT sku, canal, cuenta, item_id, precio, stock_real, stock_full,
                  stock_fba, es_full, situacion,
                  (updated_at >= NOW() - INTERVAL %s MINUTE) AS caliente
           FROM canal_inventario WHERE LOWER(sku)=%s AND canal=%s AND cuenta=%s""",
        (VENTANA_CALIENTE_MIN, sku_l, canal, cuenta_my),
    )
    rm = my_cur.fetchone()
    pg_cur.execute(
        """select l.price, l.stock_own, l.stock_full, l.is_fulfillment,
                  l.situacion, l.listing_id
           from channel.listings l join core.accounts a on a.id = l.account_id
           where lower(l.sku::text)=%s and l.canal=%s and a.legacy_code=%s""",
        (sku_l, canal, legacy),
    )
    rp = pg_cur.fetchone()
    if rm is None:
        return None, (rp and _fila_pg(rp)), False
    return _fila_mysql(rm), (rp and _fila_pg(rp)), bool(rm["caliente"])


def main() -> None:
    prod = cargar_env(".env")
    dest = cargar_env("env.staging")  # el proyecto donde escribe el espejo hoy (DEV)
    m = re.search(r"postgres\.([a-z0-9]+):", dest["SUPABASE_DB_URL"])
    print(f"Comparando MySQL prod (canal_inventario) ↔ Supabase channel.listings "
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

    # ── Pasada 1: todo contra todo (filas frías) ────────────────────────────
    my_rows, calientes, invalidos = leer_mysql(my_cur)
    pg_rows = leer_pg(pg_cur)

    solo_my = sorted(set(my_rows) - set(pg_rows))
    solo_pg_n = len(set(pg_rows) - set(my_rows))  # informativo (regla 4: fusión ETL)
    sospechosos = []
    for k in my_rows.keys() & pg_rows.keys():
        difs = _difiere(my_rows[k][1], pg_rows[k][1])
        if difs:
            sospechosos.append((k, difs))

    print(f"Pasada 1: mysql_frías={len(my_rows)} (+{calientes} calientes, "
          f"+{invalidos} sku inválido) supabase={len(pg_rows)} | "
          f"solo_mysql={len(solo_my)} solo_supabase={solo_pg_n} (informativo) | "
          f"sospechosos={len(sospechosos)}")

    # ── Pasada 2: reconfirmación de sospechosos (regla 2) ───────────────────
    divergentes, parpadeos = [], 0
    if sospechosos:
        print(f"Esperando {ESPERA_RECONFIRMA_S}s para reconfirmar "
              f"{len(sospechosos)} sospechosos…")
        time.sleep(ESPERA_RECONFIRMA_S)
        for clave, difs_v1 in sospechosos[: MAX_DETALLE * 2]:
            vm, vp, caliente = releer_clave(my_cur, pg_cur, clave)
            if caliente or vm is None or vp is None:
                parpadeos += 1  # el sync la tocó en medio: foto movida
                continue
            difs = _difiere(vm, vp)
            if not difs:
                parpadeos += 1
            else:
                divergentes.append({"sku": my_rows[clave][0], "canal": clave[1],
                                    "cuenta": clave[2], "columnas": difs})

    limpio = not solo_my and not divergentes
    resumen = {
        "mysql_frias": len(my_rows), "mysql_calientes_excluidas": calientes,
        "excluidos_sku_invalido": invalidos, "supabase": len(pg_rows),
        "solo_en_mysql": len(solo_my), "solo_en_supabase_informativo": solo_pg_n,
        "sospechosos_pasada1": len(sospechosos), "parpadeos_descartados": parpadeos,
        "divergentes_confirmados": len(divergentes),
    }
    detalle = {
        "solo_en_mysql": [f"{my_rows[k][0]} ({k[1]}/{k[2]})" for k in solo_my[:MAX_DETALLE]],
        "divergentes": divergentes[:MAX_DETALLE],
    }
    print(f"Pasada 2: parpadeos={parpadeos} divergentes_confirmados={len(divergentes)} "
          f"-> {'DELTA = 0' if limpio else 'CON DELTAS'}")
    for d in divergentes[:5]:
        print(f"    {d['sku']} ({d['canal']}/{d['cuenta']}): " + "; ".join(
            f"{c} {v['mysql']} vs {v['supabase']}" for c, v in d["columnas"].items()))

    my_cur.close()
    my.close()

    resultado = "ok" if limpio else "con_deltas"
    pg_cur.execute("set session characteristics as transaction read write; "
                   "set default_transaction_read_only = off;")
    pg_cur.execute(
        """insert into migration.reconciliation_runs (dominio, descripcion, conteos, checksums, resultado)
           values ('channel-deltas', 'job de deltas del dual-write de channel', %s, %s, %s)""",
        (json.dumps({"canal_inventario_vs_listings": resumen}),
         json.dumps(detalle, default=str)[:100000], resultado),
    )
    pg_cur.close()
    pg.close()
    print(f"\nActa registrada en migration.reconciliation_runs → resultado: {resultado}")
    sys.exit(0 if limpio else 2)


if __name__ == "__main__":
    main()
