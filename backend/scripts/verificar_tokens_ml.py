"""
verificar_tokens_ml.py — ¿Es SEGURO migrar los tokens de ML a kubera?

SOLO LECTURA. **Nunca imprime un token ni un `client_secret`**, ni parte de
ellos: solo fechas, longitudes y una huella corta para saber si el valor CAMBIÓ.
Ese es el requisito para poder correrlo seguido sin dejar credenciales en
pantalla ni en logs.

LA PREGUNTA QUE CONTESTA
------------------------
El PASO 6 mueve `ml_tokens` y `ml_tokens_dashboard` a `ops.ml_tokens` +
`vault.secrets`. El riesgo no es copiar mal: es que **alguien más escriba esas
tablas** y al migrar le quitemos el piso sin enterarnos.

El esquema v4 lo dice literal: *"BLOQUEADA por P3: no converger sin acuerdo con
el dueño de `ml_tokens_dashboard` (sistema externo, refresca ~6 h)"*. Y
`meli.py` la llama *"fuente única de verdad — todos los proyectos de ML se
conectan ahí"*. Si eso sigue siendo cierto, migrar unilateralmente rompe a un
tercero. Si ya no lo es, el bloqueo P3 caducó.

**Este verificador existe para decidir eso con evidencia y no con memoria.**

EL INVARIANTE QUE DELATA AL ESCRITOR
------------------------------------
`meli.py` renueva y escribe LAS DOS tablas en la misma pasada, con segundos de
diferencia (`:486` ml_tokens, `:492` dashboard). Un escritor externo tocaría
**solo el dashboard**. Entonces:

    d = dashboard.updated_at − ml_tokens.updated_at

      |d| ≤ tolerancia  → escribió NUESTRO backend (lo normal)
      d  >  tolerancia  → **ESCRITOR EXTERNO VIVO** (el dashboard se movió solo)
      d  < −tolerancia  → nuestra sincronización al dashboard está FALLANDO
                          (ese UPDATE va en un try/except que solo avisa)

Ninguna de las tres se deduce de una sola corrida: la evidencia se ACUMULA. Por
eso cada observación se guarda en un registro local y se reporta la RACHA. Una
foto dice el estado; la racha dice quién escribe.

CÓMO SE USA PARA DECIDIR
------------------------
Correrlo a diario. Con **cero divergencias en varios días que incluyan al menos
una renovación real por cuenta**, el bloqueo P3 se puede dar por caducado y la
migración es unilateral y segura.

Una sola divergencia hacia el dashboard = hay dueño externo, y hay que hablar con
él ANTES de tocar nada.

⚠️ **Sin renovaciones no hay prueba.** Si en toda la ventana ningún token se
renovó, la racha de "cero divergencias" no significa nada — nadie escribió, ni
nosotros ni ellos. El script lo dice explícitamente en vez de dar un verde
vacío; es el mismo error que el "0 avisos" de un panel que nadie abrió.

Uso:
  ...python backend/scripts/verificar_tokens_ml.py
  ...python backend/scripts/verificar_tokens_ml.py --tolerancia-seg 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REGISTRO = ROOT / ".tokens_ml_observaciones.jsonl"   # local; va en .gitignore


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


def huella(valor) -> str:
    """8 hex del SHA-256. Sirve para ver si CAMBIÓ, nunca para reconstruirlo."""
    if valor is None:
        return "—"
    b = valor.encode() if isinstance(valor, str) else bytes(valor)
    return hashlib.sha256(b).hexdigest()[:8]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tolerancia-seg", type=int, default=10,
                    help="margen para considerar que las dos escrituras son la misma pasada")
    args = ap.parse_args()

    E = cargar(".env")
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    with my.cursor() as c:
        c.execute("SELECT cuenta, access_token, refresh_token, updated_at FROM ml_tokens")
        tok = {r["cuenta"]: r for r in c.fetchall()}
        c.execute("SELECT cuenta, app_id, access_token, refresh_token, client_secret, "
                  "updated_at FROM ml_tokens_dashboard")
        dash = {r["cuenta"]: r for r in c.fetchall()}
    my.close()

    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    print(f"VERIFICADOR DE TOKENS ML — {ahora:%Y-%m-%d %H:%M} UTC\n")

    obs = {"ts": ahora.isoformat(timespec="seconds"), "cuentas": {}}
    divergencias: list[str] = []
    todo_ok = True

    print("  cuenta          ml_tokens         dashboard         Δ        huella(refresh)")
    for cuenta in sorted(set(tok) | set(dash)):
        t, d = tok.get(cuenta), dash.get(cuenta)
        if not t or not d:
            falta = "ml_tokens" if not t else "ml_tokens_dashboard"
            print(f"  {cuenta:15s} ⚠ solo está en una tabla (falta en {falta})")
            divergencias.append(f"{cuenta}: ausente en {falta}")
            todo_ok = False
            continue
        delta = (d["updated_at"] - t["updated_at"]).total_seconds()
        marca = "ok" if abs(delta) <= args.tolerancia_seg else (
            "EXTERNO" if delta > 0 else "SYNC-FALLA")
        print(f"  {cuenta:15s} {t['updated_at']:%m-%d %H:%M:%S}  "
              f"{d['updated_at']:%m-%d %H:%M:%S}  {delta:+6.0f}s  "
              f"{huella(t['refresh_token'])} / {huella(d['refresh_token'])}  [{marca}]")
        obs["cuentas"][cuenta] = {
            "ml_tokens": t["updated_at"].isoformat(timespec="seconds"),
            "dashboard": d["updated_at"].isoformat(timespec="seconds"),
            "delta_seg": delta,
            "h_refresh_tok": huella(t["refresh_token"]),
            "h_refresh_dash": huella(d["refresh_token"]),
            "h_client_secret": huella(d["client_secret"]),
        }
        if marca == "EXTERNO":
            divergencias.append(
                f"{cuenta}: el dashboard se movió {delta:.0f}s DESPUÉS que ml_tokens "
                f"→ alguien más lo escribió")
            todo_ok = False
        elif marca == "SYNC-FALLA":
            divergencias.append(
                f"{cuenta}: ml_tokens quedó {-delta:.0f}s por delante del dashboard "
                f"→ nuestro UPDATE al dashboard falló (va en try/except)")
            todo_ok = False

        # SEGUNDO CHEQUEO, INDEPENDIENTE DEL RELOJ: el propio valor.
        # ML **rota el refresh_token en cada uso**, así que dos procesos que
        # renueven producen valores DISTINTOS. Nuestro `meli.py` escribe el mismo
        # valor en las dos tablas, así que sus huellas tienen que coincidir.
        # Huellas distintas = alguien renovó por su cuenta, aunque los relojes
        # se vean parecidos. Vale más que el delta: no depende de la hora del
        # servidor ni de que las dos escrituras hayan sido "casi simultáneas".
        if huella(t["refresh_token"]) != huella(d["refresh_token"]):
            divergencias.append(
                f"{cuenta}: el refresh_token DIFIERE entre las dos tablas "
                f"({huella(t['refresh_token'])} vs {huella(d['refresh_token'])}) "
                f"→ alguien renovó por su cuenta (ML rota ese token en cada uso)")
            todo_ok = False

    # ── La racha: una foto no prueba nada, la repetición sí ─────────────────
    previas = []
    if REGISTRO.exists():
        for linea in REGISTRO.read_text(encoding="utf-8").splitlines():
            if linea.strip():
                try:
                    previas.append(json.loads(linea))
                except json.JSONDecodeError:
                    pass
    with REGISTRO.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obs, ensure_ascii=False) + "\n")

    print(f"\n── evidencia acumulada ({len(previas) + 1} observaciones) ──")
    if previas:
        limpias = sum(1 for o in previas
                      if all(abs(c.get("delta_seg", 0)) <= args.tolerancia_seg
                             for c in o["cuentas"].values()))
        print(f"  observaciones sin divergencia: {limpias} de {len(previas)}")
        # ¿Hubo RENOVACIONES en la ventana? Sin ellas, "cero divergencias" no
        # prueba nada: nadie escribió, ni nosotros ni un tercero.
        renov = 0
        for cuenta in obs["cuentas"]:
            sellos = {o["cuentas"][cuenta]["ml_tokens"]
                      for o in previas if cuenta in o.get("cuentas", {})}
            sellos.add(obs["cuentas"][cuenta]["ml_tokens"])
            renov += len(sellos) - 1
        print(f"  renovaciones observadas en la ventana: {renov}")
        if renov == 0:
            print("  ⚠ SIN renovaciones: la racha limpia NO es evidencia todavía "
                  "— nadie escribió en la ventana")
    else:
        print("  primera observación: es la línea base, todavía no prueba nada")

    print("\n── veredicto ──")
    if divergencias:
        for d in divergencias:
            print(f"  [DIVERGENCIA] {d}")
        print("\n  ⇒ NO migrar todavía. Si la divergencia es 'EXTERNO', el bloqueo P3 "
              "sigue vigente\n     y hay que hablar con el dueño de ml_tokens_dashboard "
              "ANTES de tocar nada.")
    else:
        print("  [OK] las dos tablas se movieron juntas: el único escritor observado "
              "es nuestro backend")
        print("       (no es prueba hasta que la racha incluya renovaciones reales)")

    sys.exit(0 if todo_ok else 1)


if __name__ == "__main__":
    main()
