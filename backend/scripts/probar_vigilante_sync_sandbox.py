"""
probar_vigilante_sync_sandbox.py — El vigilante de cobertura del sync (v0.599.0)
contra Postgres DE VERDAD: el SQL de `cobertura()`, el insert de
`anotar_ronda()` y la poda de `revisar()`.

Simula 26 h de vueltas (una cada 15 min) de cinco syncs de mentira en
`ops.process_log` y comprueba que el estado de cada uno sea el que toca:

  RELOJ     tajadas contiguas de 80 sobre 2,000 → recorre todo → ok
  ATORADO   las MISMAS 80 en cada vuelta (el defecto del 29-sep) → baja
  NUEVO     su primera vuelta fue hace 3 h → calentando (no avisa)
  CALLADO   dejó de dar vueltas hace 2 h → sin_vueltas
  MUDO      visita pero el canal no contesta nada → sin_respuesta

Todo en UNA transacción que termina en ROLLBACK. Slack NO recibe nada (el
aviso va simulado). Aborta si el DSN es el de producción.

Uso (desde la raíz del repo, con env.staging al lado):
  python backend/scripts/probar_vigilante_sync_sandbox.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    ruta = ROOT / nombre
    if ruta.exists():
        for l in ruta.read_text(encoding="utf-8").splitlines():
            s = l.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


env = cargar("env.staging")
dsn = env.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DB_URL", "")
if not dsn:
    sys.exit("No hay SUPABASE_DB_URL (env.staging en la raíz del repo).")
if (env.get("SUPABASE_PROD_REF") or "tukwcvsi") in dsn:
    sys.exit("ABORTA: ese DSN es PRODUCCIÓN. Esta prueba escribe; solo corre en sandbox.")

import psycopg2  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

from config import settings  # noqa: E402
from services import alertas  # noqa: E402
from services import vigilante_sync as vs  # noqa: E402

_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def main() -> None:
    cn = psycopg2.connect(dsn, connect_timeout=15, application_name="probar_vigilante_sync")
    cur = cn.cursor(cursor_factory=RealDictCursor)
    antes = settings.vigilante_sync_enabled
    try:
        cur.execute("set local statement_timeout = '120s'")
        cur.execute("select count(*) n from ops.process_log where proceso = 'sync_cobertura'")
        previas = cur.fetchone()["n"]
        cur.execute("select now() n")
        ahora = cur.fetchone()["n"]

        def vuelta(clave, hace_min, n, leidos, respondidos):
            cur.execute(
                """insert into ops.process_log (proceso, origen, accion, estado, detalle, created_at)
                   values ('sync_cobertura', 'prueba', %s, 'ronda', %s::jsonb, %s)""",
                (clave, json.dumps({"n": n, "leidos": leidos, "respondidos": respondidos}),
                 ahora - timedelta(minutes=hace_min)))

        universo = [f"ZZ{k:05d}" for k in range(2000)]
        for r in range(104):                               # 26 h, una vuelta cada 15 min
            hace = (103 - r) * 15 + 1
            tajada = [universo[(r * 80 + k) % 2000] for k in range(80)]
            vuelta("zzprueba|RELOJ", hace, 2000, tajada, 78)
            vuelta("zzprueba|ATORADO", hace, 2000, universo[:80], 80)
            vuelta("zzprueba|MUDO", hace, 2000, tajada, 0)
            if hace > 120:
                vuelta("zzprueba|CALLADO", hace, 2000, tajada, 80)
            if hace < 180:
                vuelta("zzprueba|NUEVO", hace, 2000, tajada, 80)
        vuelta("zzprueba|VIEJO", 8 * 24 * 60, 10, ["X"], 1)   # de hace 8 días: se poda

        def en_cursor(sql, params=None):
            cur.execute(sql, params)
            return cur.fetchall()

        def ejecutar(sql, params=None):
            cur.execute(sql, params)

        settings.vigilante_sync_enabled = True
        with mock.patch.object(vs.sdb, "fetch_all", en_cursor), \
             mock.patch.object(vs.sdb, "execute", ejecutar), \
             mock.patch.object(alertas, "avisar_estado") as aviso:
            # El insert real de una vuelta, con su fecha por omisión (now()).
            vs.anotar_ronda("zzprueba", "NUEVO", 2000, ["ZZ00001", "ZZ00000"], 2)
            print(f"\nSandbox · {previas} vueltas reales previas del vigilante (debe ser 0)")
            filas = {f["clave"]: f for f in vs.cobertura(24)}
            for c in ("RELOJ", "ATORADO", "NUEVO", "CALLADO", "MUDO"):
                f = filas.get(f"zzprueba|{c}", {})
                print(f"  {c:8} {f.get('estado'):14} {f.get('distintos')}/{f.get('universo')} "
                      f"en {f.get('vueltas')} vueltas")
            print()
            check("el turno por reloj recorre todo → ok",
                  filas["zzprueba|RELOJ"]["estado"] == "ok"
                  and filas["zzprueba|RELOJ"]["distintos"] == 2000)
            check("las mismas 80 en cada vuelta → baja (el defecto del 29-sep)",
                  filas["zzprueba|ATORADO"]["estado"] == "baja"
                  and filas["zzprueba|ATORADO"]["distintos"] == 80)
            check("recién encendido → calentando", filas["zzprueba|NUEVO"]["estado"] == "calentando")
            check("sin vueltas hace 2 h → sin_vueltas", filas["zzprueba|CALLADO"]["estado"] == "sin_vueltas")
            check("visita pero nadie contesta → sin_respuesta",
                  filas["zzprueba|MUDO"]["estado"] == "sin_respuesta")
            check("el insert real de anotar_ronda entra a la ventana",
                  filas["zzprueba|NUEVO"]["vueltas"] == 13, f"{filas['zzprueba|NUEVO']['vueltas']} vueltas")

            vs.revisar()
            avisos = {c.args[0]: c.args[1] for c in aviso.call_args_list}
            check("revisar avisa a los malos y al sano (para su recuperación), no al que calienta",
                  avisos.get("sync_cobertura:zzprueba|ATORADO") == "baja"
                  and avisos.get("sync_cobertura:zzprueba|RELOJ") == "ok"
                  and "sync_cobertura:zzprueba|NUEVO" not in avisos, str(avisos))
            cur.execute("select count(*) n from ops.process_log where proceso='sync_cobertura' "
                        "and accion = 'zzprueba|VIEJO'")
            check("la poda borra lo de más de 7 días", cur.fetchone()["n"] == 0)
    finally:
        cn.rollback()
        cn.close()
        settings.vigilante_sync_enabled = antes
    print("\nRESULTADO:", "TODO OK" if _ok else "HAY FALLAS", "(transacción revertida: el sandbox quedó igual)")
    raise SystemExit(0 if _ok else 1)


if __name__ == "__main__":
    main()
