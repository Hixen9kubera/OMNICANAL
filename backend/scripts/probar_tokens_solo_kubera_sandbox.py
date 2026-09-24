"""
probar_tokens_solo_kubera_sandbox.py — TOKENS_SOLO_KUBERA contra una base de
verdad: el sandbox, sin MySQL (`MYSQL_ENABLED=false`).

LO QUE LAS PRUEBAS UNITARIAS NO PUEDEN DECIR
--------------------------------------------
Que el candado de Postgres de verdad deja pasar a UNO. Con dobles, el candado es
lo que uno quiera que sea. Aquí tres PROCESOS distintos piden renovar la misma
cuenta al mismo tiempo, y Mercado Libre (simulado, tarda 3 s en contestar) debe
recibir UNA sola llamada; los otros dos tienen que salir con el token del que
ganó. Es el caso del 24-sep, cuando SANCORFASHION se renovó dos veces en el
mismo segundo.

NADA DE ESTO TOCA MERCADO LIBRE NI UN TOKEN REAL
------------------------------------------------
  · La llamada a /oauth/token está SIMULADA en todos los procesos.
  · Los tokens son cadenas inventadas, cifradas con una llave Fernet que nace y
    muere con esta corrida (el sandbox no tiene `DB_ENCRYPTION_KEY`).
  · Escribe UNA fila, cuenta `PRUEBA_TOKENS`, y la borra al final.
  · Aborta si el DSN es el de producción o si MySQL quedó encendido.

Uso (desde la raíz, con `env.staging` ahí):
  python backend/scripts/probar_tokens_solo_kubera_sandbox.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HIJO = len(sys.argv) > 2 and sys.argv[1] == "--hijo"
_CUENTA = "PRUEBA_TOKENS"
_APP = "7777"

if not _HIJO:
    # El ambiente de la prueba se arma UNA vez aquí y los hijos lo heredan.
    from cryptography.fernet import Fernet
    _llave = Fernet.generate_key()
    os.environ.update({
        "APP_ENV": "staging",
        "MYSQL_ENABLED": "false",
        "TOKENS_SOLO_KUBERA": "true",
        "DB_ENCRYPTION_KEY": _llave.decode(),
        # Nunca las de verdad, aunque env.staging las trajera.
        "MELI_APP_ID": "9999",
        "MELI_CLIENT_SECRET": "secreto-global-de-prueba",
        f"MELI_APP_ID_{_CUENTA}": _APP,
        # CIFRADA, como se copia de `ml_tokens_dashboard` a Railway.
        f"MELI_CLIENT_SECRET_{_CUENTA}": Fernet(_llave).encrypt(b"secreto-de-prueba").decode(),
    })

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if not settings.supabase_db_url:
    sys.exit("ABORT: env.staging sin SUPABASE_DB_URL.")
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION y este script ESCRIBE.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")
if not settings.tokens_solo_kubera:
    sys.exit("ABORT: TOKENS_SOLO_KUBERA no quedo encendido.")

from services import alertas, meli, tokens_read  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

_avisos: list[str] = []
mock.patch.object(alertas, "avisar",
                  side_effect=lambda tipo, texto, *a, **k: _avisos.append(texto)).start()


class _ML:
    """/oauth/token simulado: apunta lo que recibió y contesta lo que se le diga."""

    def __init__(self, token: str, rt: str, status: int = 200, tarda: float = 0.0):
        self.llamadas: list[dict] = []
        self.token, self.rt, self.status, self.tarda = token, rt, status, tarda

    def __call__(self, url, data=None, timeout=None):
        assert url.endswith("/oauth/token"), url
        self.llamadas.append(dict(data or {}))
        time.sleep(self.tarda)
        if self.status != 200:
            return mock.Mock(status_code=self.status, text='{"error":"invalid_grant"}')
        return mock.Mock(status_code=200,
                         json=lambda: {"access_token": self.token, "refresh_token": self.rt})


def hijo(inicio: float, n: str) -> None:
    ml = _ML(f"APP_USR-{_APP}-092418-hijo{n}-1", f"rt-hijo{n}", tarda=3.0)
    with mock.patch.object(meli.httpx, "post", ml):
        time.sleep(max(0.0, inicio - time.time()))
        t0 = time.time() - inicio
        tok = meli.refrescar_token(_CUENTA)
        t1 = time.time() - inicio
    print(json.dumps({"hijo": n, "llamo_ml": bool(ml.llamadas),
                      "huella": tokens_read.huella(tok),
                      "t0": round(t0, 2), "t1": round(t1, 2)}), flush=True)


if _HIJO:
    hijo(float(sys.argv[2]), sys.argv[3])
    sys.exit(0)


# ═════════════════════════════════════════════════════════════════════════════
_ok = True


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f" — {detalle}" if detalle else ""))


f = meli._fernet()


def sembrar(access: str, refresh: str, horas: float) -> None:
    tokens_read.guardar(_CUENTA, meli._enc(f, access), meli._enc(f, refresh),
                        datetime.now(timezone.utc) - timedelta(hours=horas))


def fila() -> dict:
    r = sdb.fetch_one("select access_token, refresh_token, extract(epoch from "
                      "(clock_timestamp() - updated_at))::float as edad_s "
                      "from ops.ml_tokens where cuenta = %s", (_CUENTA,))
    return {"access": meli._dec(f, r["access_token"]), "refresh": meli._dec(f, r["refresh_token"]),
            "edad_s": r["edad_s"]}


print(f"Sandbox {_ref[:8]}… · MySQL apagado · TOKENS_SOLO_KUBERA encendido\n")
try:
    sdb.execute("delete from ops.ml_tokens where cuenta = %s", (_CUENTA,))
    semilla = f"APP_USR-{_APP}-092412-semilla-1"
    sembrar(semilla, "rt-semilla", horas=7)

    print("1. Leer")
    lecturas_mysql = mock.patch.object(meli.db, "fetch_one",
                                       side_effect=AssertionError("leyó MySQL")).start()
    check("el token sale de kubera, pedido en minúsculas",
          meli._access_token(_CUENTA.lower()) == semilla)
    check("sin una sola consulta a MySQL", not lecturas_mysql.called)

    print("\n2. Renovar (un proceso)")
    ml = _ML(f"APP_USR-{_APP}-092418-nuevo-1", "rt-nuevo")
    with mock.patch.object(meli.httpx, "post", ml):
        tok = meli.refrescar_token(_CUENTA)
    enviado = ml.llamadas[0] if ml.llamadas else {}
    check("ML recibe la app de la cuenta, su clave DESCIFRADA y el refresh_token de kubera",
          (enviado.get("client_id"), enviado.get("client_secret"), enviado.get("refresh_token"))
          == (_APP, "secreto-de-prueba", "rt-semilla"))
    ahora = fila()
    check("el par nuevo quedó en kubera, cifrado y junto",
          (ahora["access"], ahora["refresh"]) == (tok, "rt-nuevo"))
    check("con la hora de este momento", ahora["edad_s"] < 10, f"{ahora['edad_s']:.1f} s")

    print("\n3. Otra petición enseguida")
    ml2 = _ML("no-debe-llegar", "no-debe-llegar")
    with mock.patch.object(meli.httpx, "post", ml2):
        tok2 = meli.refrescar_token(_CUENTA)
    check("reutiliza el recién renovado sin llamar a ML", tok2 == tok and not ml2.llamadas)

    print("\n4. App equivocada")
    sembrar(f"APP_USR-{_APP}-092412-otra-1", "rt-otra", horas=7)
    os.environ[f"MELI_APP_ID_{_CUENTA}"] = "8888"
    n_avisos = len(_avisos)
    ml3 = _ML("no-debe-llegar", "no-debe-llegar")
    with mock.patch.object(meli.httpx, "post", ml3):
        tok3 = meli.refrescar_token(_CUENTA)
    os.environ[f"MELI_APP_ID_{_CUENTA}"] = _APP
    check("no llama a ML y no toca la fila",
          tok3 is None and not ml3.llamadas and fila()["refresh"] == "rt-otra")
    check("y avisa diciendo las dos apps", len(_avisos) > n_avisos
          and "7777" in _avisos[-1] and "8888" in _avisos[-1])

    print("\n5. ML rechaza (invalid_grant)")
    ml4 = _ML("", "", status=400)
    with mock.patch.object(meli.httpx, "post", ml4):
        tok4 = meli.refrescar_token(_CUENTA)
    check("no guarda nada: el par viejo sigue intacto",
          tok4 is None and fila()["refresh"] == "rt-otra")
    check("y avisa con el motivo", "invalid_grant" in _avisos[-1])

    print("\n6. Tres PROCESOS piden renovar al mismo tiempo (ML tarda 3 s)")
    sembrar(f"APP_USR-{_APP}-092412-carrera-1", "rt-carrera", horas=7)
    inicio = time.time() + 6
    hijos = [subprocess.Popen([sys.executable, __file__, "--hijo", str(inicio), str(i)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                              encoding="utf-8")
             for i in range(3)]
    res = []
    for p in hijos:
        salida, _ = p.communicate(timeout=120)
        lineas = [l for l in salida.splitlines() if l.startswith("{")]
        res.append(json.loads(lineas[-1]) if lineas else {"llamo_ml": None, "huella": None})
    llamaron = [r["hijo"] for r in res if r["llamo_ml"]]
    huellas = {r["huella"] for r in res}
    print(f"     {res}")
    arranques = [r.get("t0") for r in res if r.get("t0") is not None]
    check("los tres pidieron a la vez (antes de que ML contestara)",
          len(arranques) == 3 and max(arranques) - min(arranques) < 1.5,
          f"arranques {arranques} s")
    check("ML recibe UNA sola renovación", len(llamaron) == 1, f"llamaron: {llamaron}")
    check("los tres salen con el MISMO token", len(huellas) == 1 and None not in huellas)
    final = fila()
    check("y es el que quedó en kubera",
          tokens_read.huella(final["access"]) in huellas
          and final["refresh"] == f"rt-hijo{llamaron[0] if llamaron else '?'}")

    print("\n7. Vigilante de tokens rancios")
    sembrar(f"APP_USR-{_APP}-092412-vieja-1", "rt-vieja", horas=13)
    n_avisos = len(_avisos)
    alertas._revisar_tokens_rancios()
    nuevos = _avisos[n_avisos:]
    check("avisa por la cuenta que lleva 13 h",
          any(f"{_CUENTA} hace 13 h" in t for t in nuevos), nuevos[-1][:90] if nuevos else "sin aviso")

    print("\n8. Revisión previa al encendido (revisar_apps_kubera)")
    sembrar(f"APP_USR-{_APP}-092412-previa-1", "rt-previa", horas=1)
    mia = [r for r in meli.revisar_apps_kubera() if r["cuenta"] == _CUENTA]
    check("con la app de la cuenta en el entorno: coincide",
          bool(mia) and mia[0]["coincide"] and mia[0]["fuente"] == "cuenta", str(mia))
    guardadas = {k: os.environ.pop(k) for k in (f"MELI_APP_ID_{_CUENTA}",
                                                f"MELI_CLIENT_SECRET_{_CUENTA}")}
    mia = [r for r in meli.revisar_apps_kubera() if r["cuenta"] == _CUENTA]
    os.environ.update(guardadas)
    check("solo con la global (otra app): NO coincide y lo dice",
          bool(mia) and not mia[0]["coincide"] and mia[0]["fuente"] == "global"
          and mia[0]["app_entorno"] == "9999", str(mia))

    print("\n9. Tiempos del servidor (el candado espera mientras ML contesta)")
    with sdb.get_cursor() as cur:
        cur.execute("select current_setting('statement_timeout') st, "
                    "current_setting('idle_in_transaction_session_timeout') it")
        t = cur.fetchone()
    print(f"     statement_timeout={t['st']} · idle_in_transaction={t['it']}")
finally:
    sdb.execute("delete from ops.ml_tokens where cuenta = %s", (_CUENTA,))
    queda = sdb.fetch_one("select count(*) n from ops.ml_tokens where cuenta = %s", (_CUENTA,))
    print(f"\nLimpieza: filas de {_CUENTA} que quedan = {queda['n']}")

print("\nRESULTADO:", "TODO PASA" if _ok else "HAY FALLAS")
sys.exit(0 if _ok else 1)
