"""
alistar_corte.py — ¿Qué pasa exactamente si apago MySQL ahora mismo?

SOLO LECTURA, contra PRODUCCION. No cambia una sola bandera.

QUE CONTESTA, Y POR QUE ASI
----------------------------
`probar_corte_total.py` prueba que el CODIGO vive sin MySQL, en el sandbox. Esto
es la otra mitad: que los DATOS ya esten del otro lado, en produccion, hoy.

La pregunta concreta es una sola, repetida por tabla: **si MySQL recibio una
escritura hace 3 minutos y su destino en kubera trae 3 dias de atraso, apagarlo
pierde esos 3 dias.** Eso no lo puede ver una prueba en el sandbox.

COMO MIDE LA FRESCURA
---------------------
No adivina el nombre de la columna de fecha: la BUSCA en el esquema. Esta sesion
tropezo tres veces con columnas que se llamaban distinto de lo esperado
(`actualizado` y no `updated_at`, `source_url` y no `url`, una categoria que no
vivia donde parecia). Preguntar por el esquema cuesta una consulta y no falla.

LOS TRES VEREDICTOS POR TABLA
-----------------------------
  AL DIA      kubera esta igual de fresco o mas    -> apagar no pierde nada
  ATRASADO    kubera trae retraso frente a MySQL   -> apagar PIERDE ese hueco
  SIN MEDIR   falta columna de fecha de un lado    -> no se puede afirmar nada

SIN MEDIR **no es verde**. Es justo el error que persigue toda esta migracion:
confundir "no pude preguntar" con "no hay problema".

Uso:
  ...python backend/scripts/alistar_corte.py
  ...python backend/scripts/alistar_corte.py --minutos 30
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_PROYECTO = "66831425-3b47-4fda-8a8b-4b2b5f3df3e2"

# Tabla de MySQL -> destino en kubera. Solo las que SIGUEN VIVAS: una tabla
# muerta no puede perder nada al apagarse.
_PAREJAS: list[tuple[str, str]] = [
    # OJO: la bitacora va a `ops.fanout_log`. `ops.fulfillment_operations` es
    # el CANDADO --17 filas contra 32,506-- y solo recibe una fila cuando una
    # operacion SE APLICA. Comparar sus fechas daba 31 dias de "atraso" que no
    # existian: eran dos tablas que miden cosas distintas.
    ("fanout_log", "ops.fanout_log"),
    ("ml_tokens", "ops.ml_tokens"),
    ("ml_tokens_dashboard", "ops.ml_tokens"),
    ("tiktok_tokens", "ops.tiktok_tokens"),
    ("stock_watch_foto", "ops.stock_watch_photo"),
    ("productos", "core.products"),
    ("crear_logs", "ops.process_log"),
    ("amazon_imagenes", "enrich.product_media"),
    ("ml_backlog", "ops.channel_submissions"),
    ("amazon_backlog", "ops.channel_submissions"),
    ("ml_image_edit_backlog", "ops.channel_submissions"),
    ("ml_progress", "channel.listings"),
    ("amazon_progress", "channel.listings"),
    ("webhook_eventos", "ops.webhook_events"),
]

# Lo que se pierde el dia del corte, a sabiendas. No es una lista de pendientes:
# es lo que ya se decidio sacrificar, escrito para que nadie lo descubra despues.
_SE_PIERDE: list[tuple[str, str]] = [
    ("El candado anti-spam de las alertas (NO las alertas)",
     "Corregido el 28-ago: las alertas salen por SLACK, no por MySQL, asi que "
     "apagarlo NO las calla. En MySQL vive solo `alertas_estado`, el candado de "
     "enfriamiento, y el propio codigo degrada al candado en memoria cuando no "
     "esta. Consecuencia real: tras cada despliegue puede colarse un aviso "
     "repetido. Ruido, no silencio. `espejo_kubera_log` si se pierde, pero es "
     "el registro para REPROCESAR errores del espejo, que es andamiaje de la "
     "migracion y se retira en F8 de todas formas."),
    ("Los 13 scripts de mantenimiento",
     "Leen MySQL directo. Los cuatro peligrosos ya estan trancados con "
     "`_candado_congelado`; el resto simplemente deja de funcionar."),
    ("El buscador manual del Resolver",
     "`packing_comparador.buscar_sku` no tiene gemela. La pantalla de empate a "
     "mano se ve vacia. Es visible y no mueve mercancia."),
    ("(RESUELTO 28-ago) La campana",
     "Ya no se pierde. `_leer_de_supabase()` cambia SOLA cuando MySQL se apaga "
     "--no hace falta la bandera--, `odoo_watch` ahora escribe directo a kubera "
     "(antes se descartaba en el espejo: 845 avisos en MySQL contra 0 en "
     "kubera) y la campana filtra a los canales que una persona lee, para no "
     "ahogarse con los 11,621 eventos diarios de ML."),
]

def cargar() -> dict[str, str]:
    d: dict[str, str] = {}
    for linea in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def _ultima(cur, sql_max, cols: list[str]) -> tuple[str | None, object]:
    """De todas las columnas de fecha, la que trae el dato MAS NUEVO.

    Elegir por NOMBRE no funciona y costo un falso rojo: en
    `ops.webhook_events` la primera columna que termina en `_at` es
    `next_retry_at`, que esta vacia en las 71,018 filas. La sonda reporto
    "kubera VACIO" sobre una tabla mas fresca que MySQL.

    Preguntarle a los DATOS no se puede enganar con un nombre. Si todas las
    columnas de fecha estan vacias, devuelve (None, None) y eso es SIN MEDIR
    de verdad, no un cero disfrazado.
    """
    mejor_col, mejor_val = None, None
    for c in cols:
        try:
            cur.execute(sql_max(c))
            v = cur.fetchone()
            v = v[0] if isinstance(v, (list, tuple)) else list(v.values())[0]
        except Exception:  # noqa: BLE001
            continue
        if v is None:
            continue
        vn = v.replace(tzinfo=None) if getattr(v, "tzinfo", None) else v
        # Una fecha en el FUTURO no mide frescura: es un vencimiento. En
        # `tiktok_tokens`, `refresh_expira` cae en 2125 y ganaba siempre, asi
        # que la comparacion dejaba de mirar `updated_at` --lo unico que dice
        # si el espejo sigue vivo-- sin avisar.
        if vn > datetime.now(timezone.utc).replace(tzinfo=None):
            continue
        if mejor_val is None or vn > mejor_val:
            mejor_col, mejor_val = c, vn
    return mejor_col, mejor_val


def banderas_produccion() -> dict[str, str] | None:
    """Las banderas VIVAS, leidas de Railway. None si no se pudo preguntar."""
    # En Windows el CLI es `railway.cmd` y subprocess NO lo resuelve con el
    # nombre pelon: falla como si no estuviera instalado. Se busca de verdad.
    exe = shutil.which("railway") or shutil.which("railway.cmd")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "variables", "-p", _PROYECTO, "-e", "production",
             "-s", "BackendOmnicanal", "--kv"],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return None
        out: dict[str, str] = {}
        for linea in r.stdout.splitlines():
            if "=" in linea:
                k, _, v = linea.partition("=")
                if k.startswith(("SUPABASE_READ_", "SUPABASE_WRITE_", "MYSQL_")):
                    out[k.strip()] = v.strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


def _frescura(my, pg, dbname: str, tabla: str, destino: str, tolerancia: float):
    """(estado, detalle) — AL DIA / ATRASADO / SIN MEDIR."""
    esq, _, nom = destino.partition(".")
    with my.cursor() as c:
        c.execute("""SELECT COLUMN_NAME FROM information_schema.columns
                      WHERE table_schema=%s AND table_name=%s
                        AND DATA_TYPE IN ('datetime','timestamp','date')""",
                  (dbname, tabla))
        cols_m = [r["COLUMN_NAME"] for r in c.fetchall()]
        cm, um = _ultima(c, lambda x: f"SELECT MAX(`{x}`) u FROM `{tabla}`", cols_m)
    with pg.cursor() as c:
        c.execute("""select column_name from information_schema.columns
                      where table_schema=%s and table_name=%s
                        and data_type like any(array['timestamp%%','date'])""",
                  (esq, nom))
        cols_k = [r[0] for r in c.fetchall()]
        ck, uk = _ultima(c, lambda x: f'select max("{x}") from {esq}."{nom}"', cols_k)
    if not cols_m or not cols_k:
        falta = "MySQL" if not cols_m else "kubera"
        return "SIN MEDIR", f"sin ninguna columna de fecha en {falta}"
    if um is None:
        return "SIN MEDIR", "MySQL no tiene ninguna fila con fecha"
    if uk is None:
        return "ATRASADO", f"kubera VACIO · MySQL {um} ({cm})"
    um_n, uk_n = um, uk
    atraso = (um_n - uk_n).total_seconds() / 60
    if atraso > tolerancia:
        return "ATRASADO", f"kubera {atraso:,.0f} min atras · MySQL {um_n} ({cm})"
    return "AL DIA", f"desfase {max(atraso, 0):.0f} min · {cm} -> {ck}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=90.0,
                    help="cuanto atraso se tolera antes de marcar ATRASADO")
    args = ap.parse_args()

    E = cargar()
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    print("ALISTAMIENTO DEL CORTE — produccion, solo lectura")
    print(f"  {ahora:%Y-%m-%d %H:%M:%S} UTC · tolerancia {args.minutos:.0f} min\n")

    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)

    # -- 1. Las banderas ----------------------------------------------------
    print("-- 1. banderas en produccion --")
    fl = banderas_produccion()
    apagadas: list[str] = []
    if fl is None:
        print("  [SIN MEDIR] no se pudo leer Railway (CLI o sesion).")
        print("              Sin esto NO se puede afirmar que el corte sea seguro.")
    else:
        lect = {k: v for k, v in fl.items() if k.startswith("SUPABASE_READ_")}
        escr = {k: v for k, v in fl.items() if k.startswith("SUPABASE_WRITE_")}
        apagadas = [k for k, v in lect.items() if v.lower() != "true"]
        n_e = sum(1 for v in escr.values() if v.lower() == "true")
        n_l = sum(1 for v in lect.values() if v.lower() == "true")
        print(f"  escrituras encendidas: {n_e} de {len(escr)}")
        print(f"  lecturas   encendidas: {n_l} de {len(lect)}")
        print(f"  MYSQL_ENABLED = {fl.get('MYSQL_ENABLED', '(sin definir = encendido)')}")
        try:
            texto = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
            conocidas = {n.upper() for n in re.findall(r"supabase_read_[a-z_]+", texto)}
            for n in sorted(conocidas - set(lect)):
                apagadas.append(f"{n} (sin definir)")
        except Exception:  # noqa: BLE001
            pass
        if apagadas:
            print(f"\n  LECTURAS APAGADAS ({len(apagadas)}) — el dia del corte estas")
            print("  se quedan sin fuente:")
            for a in apagadas:
                print(f"     · {a}")

    # -- 2. Frescura --------------------------------------------------------
    print("\n-- 2. frescura: MySQL contra su destino en kubera --")
    al_dia, atrasadas, sin_medir = [], [], []
    for tabla, destino in _PAREJAS:
        try:
            estado, detalle = _frescura(my, pg, E["DB_NAME"], tabla, destino,
                                        args.minutos)
        except Exception as exc:  # noqa: BLE001
            pg.rollback()
            estado, detalle = "SIN MEDIR", f"{type(exc).__name__}: {str(exc)[:60]}"
        fila = (tabla, destino, detalle)
        {"AL DIA": al_dia, "ATRASADO": atrasadas, "SIN MEDIR": sin_medir}[estado].append(fila)

    for t, d, det in atrasadas:
        print(f"  [ATRASADO ] {t:22s} -> {d:32s} {det}")
    for t, d, det in sin_medir:
        print(f"  [SIN MEDIR] {t:22s} -> {d:32s} {det}")
    for t, d, det in al_dia:
        print(f"  [AL DIA   ] {t:22s} -> {d:32s} {det}")

    # -- 3. Lo que se pierde a sabiendas ------------------------------------
    print("\n-- 3. lo que se pierde el dia del corte, a sabiendas --")
    for titulo, razon in _SE_PIERDE:
        print(f"  · {titulo}")
        print(f"    {razon}")

    my.close()
    pg.close()

    # -- 4. Veredicto -------------------------------------------------------
    print("\n" + "=" * 74)
    print(f"  AL DIA {len(al_dia)}  ·  ATRASADO {len(atrasadas)}  ·  "
          f"SIN MEDIR {len(sin_medir)}  ·  lecturas apagadas {len(apagadas)}")
    listo = not atrasadas and not sin_medir and not apagadas and fl is not None
    print("\n  VEREDICTO:")
    if listo:
        print("  Los datos ya estan del otro lado y no queda ninguna lectura sin")
        print("  fuente. Apagar MySQL no pierde nada que no este en la lista 3.")
    else:
        falta = []
        if fl is None:
            falta.append("no se pudieron leer las banderas")
        if apagadas:
            falta.append(f"{len(apagadas)} lectura(s) sin fuente")
        if atrasadas:
            falta.append(f"{len(atrasadas)} destino(s) atrasado(s)")
        if sin_medir:
            falta.append(f"{len(sin_medir)} sin medir")
        print(f"  NO todavia: {'; '.join(falta)}.")
        print("  Cada renglon de arriba dice cual y por que.")
    sys.exit(0 if listo else 1)


if __name__ == "__main__":
    main()
