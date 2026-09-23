"""
calidad_ml_sandbox.py — Siembra `enrich.listing_health` (y su serie diaria
`enrich.listing_health_hist`) del SANDBOX con la calidad y la experiencia de
compra REALES que contesta Mercado Libre.

Mide las DOS cosas con el mismo `calidad_ml.medir` que usa el job y las guarda
con el mismo `calidad_ml.guardar` (una fila por publicación y métrica, más el
día de México en la historia), y resume las dos: niveles de calidad y
distribución de la experiencia por color, sin datos, cuántas calculó ML con la
categoría y cuántas tiene pausadas.

POR QUÉ UN SCRIPT Y NO EL JOB. En el sandbox el job no tiene token de ML:
`clonar_a_sandbox.py` excluye `ops.ml_tokens` a propósito y env.staging no trae
la llave Fernet. Y darle al job acceso a los tokens de producción sería peor: un
401 lo haría RENOVAR, y renovar rota el refresh_token real de producción (ML lo
cambia en cada uso) — el backend de producción se quedaría con uno muerto y los
pedidos pararían (regla 8). Así que aquí:

  · el objetivo sale del `channel.listings` del SANDBOX
    (`calidad_ml.objetivo`, con `supabase_db` apuntado al sandbox);
  · los tokens se LEEN de producción (`ops.ml_tokens`, BEKURA y SANCORFASHION)
    dentro de UNA transacción `set transaction read only` — jamás de sesión:
    el pooler 6543 comparte conexiones y un read-only de sesión lo hereda el
    backend de producción (regla 13) —, se descifran con Fernet en memoria y
    NUNCA se imprimen (ni el token ni el DSN);
  · se mide con `calidad_ml.medir(..., renovar=None)`: ante un 401 NO renueva
    nunca; la publicación cuenta como `sin_token` y no escribe fila.

CANDADOS. Aborta si el SUPABASE_DB_URL de env.staging es la BD kubera de
producción (ref tukwcvsi o SUPABASE_PROD_REF). Exige que el DSN que llega por
stdin SÍ sea el de producción (solo se usa para leer los tokens). Fuerza
`SUPABASE_DB_URL` = el del sandbox y apaga MySQL (`DB_HOST=127.0.0.1`,
`DB_PORT=1`) ANTES de importar `config`: env.staging trae el MySQL de
producción y una variable heredada del shell pisaría el archivo.

EN SECO POR DEFECTO: mide e imprime conteos, no escribe. `--real` escribe en el
sandbox. Las tablas tienen que existir ahí (aplicar
`supabase/migrations/0053_enrich_listing_health.sql` primero).

OJO con `clonar_a_sandbox.py`: hace `truncate core.accounts cascade`, que vacía
también las dos tablas de la salud del sandbox. Tras clonar, re-sembrar aquí.

USO (Git Bash; los secretos NUNCA en la línea de comando ni en un archivo):

    railway variable list --kv -s BackendOmnicanal -e production -p <proyecto> \\
      | sed -n 's/^SUPABASE_DB_URL=/PROD_SUPABASE_DB_URL=/p; /^DB_ENCRYPTION_KEY=/p' \\
      | backend/.venv/Scripts/python.exe backend/scripts/calidad_ml_sandbox.py --limite 20

    ... | python backend/scripts/calidad_ml_sandbox.py --real          # escribe
    ... | python backend/scripts/calidad_ml_sandbox.py --real --cuenta BEKURA

Stdin: líneas `CLAVE=valor` con PROD_SUPABASE_DB_URL y DB_ENCRYPTION_KEY.
Sale con 1 si menos de la mitad obtuvo respuesta de ML (medida o no calculada).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
REF_KUBERA_PROD = "tukwcvsi"
CUENTAS = ("BEKURA", "SANCORFASHION")
TANDA = 100

socket.setdefaulttimeout(120)


def _leer_archivo_env(ruta: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    if not ruta.exists():
        return vals
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


def _leer_stdin() -> dict[str, str]:
    if sys.stdin is None or sys.stdin.isatty():
        sys.exit("ABORT: los secretos llegan por stdin (CLAVE=valor): "
                 "PROD_SUPABASE_DB_URL y DB_ENCRYPTION_KEY. Ver el encabezado.")
    vals: dict[str, str] = {}
    for linea in sys.stdin.read().splitlines():
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _ref(url: str) -> str | None:
    m = re.search(r"postgres\.([a-z0-9]+):", url or "")
    return m.group(1) if m else None


def _leer_tokens(prod_url: str, clave: str) -> dict[str, str]:
    """Tokens de ML de PRODUCCIÓN, descifrados en memoria. Solo lectura, en UNA
    transacción marcada `set transaction read only` y cerrada con rollback."""
    import psycopg2
    from cryptography.fernet import Fernet, InvalidToken

    try:
        fernet = Fernet(clave.encode())
    except Exception:  # noqa: BLE001 — jamás se imprime la llave
        sys.exit("ABORT: DB_ENCRYPTION_KEY no es una llave Fernet válida.")
    try:
        cn = psycopg2.connect(prod_url, connect_timeout=20)
    except Exception as exc:  # noqa: BLE001 — el mensaje podría traer el host/usuario
        sys.exit(f"ABORT: no pude conectar a producción para leer los tokens "
                 f"({type(exc).__name__}).")
    try:
        cur = cn.cursor()
        # POR TRANSACCIÓN, nunca de sesión (regla 13): muere con el rollback.
        cur.execute("set transaction read only")
        cur.execute(
            """select distinct on (cuenta) cuenta, access_token
                 from ops.ml_tokens
                where cuenta = any(%s)
                order by cuenta, updated_at desc nulls last""",
            (list(CUENTAS),))
        filas = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"ABORT: no pude leer ops.ml_tokens ({type(exc).__name__}).")
    finally:
        try:
            cn.rollback()
        finally:
            cn.close()

    tokens: dict[str, str] = {}
    for cuenta, raw in filas:
        if not raw:
            continue
        raw = str(raw)
        if raw.startswith("gAAAAA"):
            try:
                tokens[str(cuenta)] = fernet.decrypt(raw.encode()).decode()
            except InvalidToken:
                print(f"  {cuenta}: el token no se pudo descifrar con esa llave.")
        else:
            tokens[str(cuenta)] = raw
    return tokens


async def _medir_todo(calidad_ml, pares: list[tuple[str, str]],
                      tokens: dict[str, str]) -> tuple[list[dict], dict]:
    filas: list[dict] = []
    total: dict[str, int] = {}
    for i in range(0, len(pares), TANDA):
        trozo = pares[i:i + TANDA]
        # renovar=None: ante un 401 NO se renueva jamás (rotaría el
        # refresh_token de producción). Ver el encabezado.
        f, n = await calidad_ml.medir(trozo, tokens, renovar=None)
        filas.extend(f)
        for k, v in n.items():
            total[k] = total.get(k, 0) + int(v)
        print(f"  … {min(i + TANDA, len(pares))} de {len(pares)}", flush=True)
    return filas, total


def _resumir_experiencia(calidad_ml, filas: list[dict], n: dict) -> None:
    """Distribución de la experiencia de compra, tal como la contó ML: por
    color (con su texto y sus valores), sin datos, cuántas calculó con la
    categoría (heurística sobre el texto) y cuántas tiene pausadas."""
    con_exp = [f for f in filas if f.get("exp_estado")]
    print(f"\n  experiencia de compra: {n.get('exp_ok', 0)} medidas · "
          f"{n.get('exp_sin_datos', 0)} sin datos · {n.get('exp_error', 0)} con error · "
          f"{n.get('exp_rechazada', 0)} rechazadas por ML "
          f"(de {len(filas)} filas)")
    medidas = [f for f in con_exp if f["exp_estado"] == calidad_ml.EXP_MEDIDA]
    colores = Counter((f.get("exp_color"), f.get("exp_texto")) for f in medidas)
    valores: dict[tuple, Counter] = {}
    for f in medidas:
        valores.setdefault((f.get("exp_color"), f.get("exp_texto")),
                           Counter())[f.get("exp_valor")] += 1
    for (color, texto), k in sorted(
            colores.items(),
            key=lambda ck: (min((v for v in valores[ck[0]] if v is not None),
                                default=0), str(ck[0][0]))):
        vals = ", ".join(f"{v}×{c}" for v, c in sorted(valores[(color, texto)].items()))
        print(f"    {color!s:<8} {texto!s:<8} {k:>4}   valores {vals}")
    sin_datos = sum(1 for f in con_exp if f["exp_estado"] == calidad_ml.EXP_SIN_DATOS)
    print(f"    sin datos (gris: ML aún no tiene ventas): {sin_datos}")
    por_cat = sum(1 for f in medidas if f.get("exp_por_categoria"))
    print(f"    calculadas con la categoría (heurística): {por_cat} de {len(medidas)}")
    status = Counter(f.get("exp_status_ml") for f in con_exp if f.get("exp_status_ml"))
    for sid, k in status.most_common():
        textos = Counter(f.get("exp_status_texto") for f in con_exp
                         if f.get("exp_status_ml") == sid and f.get("exp_status_texto"))
        extra = f" — «{textos.most_common(1)[0][0]}»" if textos else ""
        print(f"    status de ML {sid!s:<8}: {k}{extra}")
    ia = sum(1 for f in con_exp if f.get("exp_ia"))
    print(f"    con recomendación escrita por la IA de ML: {ia}")


def _resumir_historia(supabase_db) -> None:
    """Lo que quedó HOY (día de México) en la historia del sandbox: cuántas
    filas por métrica y cuántas guardaron el jsonb (su huella cambió respecto
    al día guardado anterior; el primer día de cada serie siempre)."""
    try:
        filas = supabase_db.fetch_all(
            """select metrica, count(*)::int as n,
                      count(*) filter (where detalle is not null)::int as con_jsonb
                 from enrich.listing_health_hist
                where dia = (now() at time zone 'America/Mexico_City')::date
                group by 1 order by 1""")
    except Exception as exc:  # noqa: BLE001 — el resumen no tumba la siembra
        print(f"  (no se pudo resumir la historia: {type(exc).__name__})")
        return
    for f in filas:
        print(f"  historia de hoy · {f['metrica']:<12}: {f['n']} filas, "
              f"{f['con_jsonb']} con jsonb (cambió la huella)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--real", action="store_true",
                    help="Escribe en el sandbox. Sin esto solo mide.")
    ap.add_argument("--limite", type=int, default=0,
                    help="Acota el número de publicaciones (0 = todas).")
    ap.add_argument("--cuenta", choices=CUENTAS, default=None,
                    help="Solo una cuenta de ML.")
    ap.add_argument("--forzar", action="store_true",
                    help="Mide todas las activas aunque ya tengan captura de hoy.")
    args = ap.parse_args()

    # En Windows, con stdout a una tubería, Python escribe en cp1252 y los
    # acentos/rayas tumban el script a media corrida.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    print("═══ Calidad ML · siembra del SANDBOX ═══")

    # ── 1. El destino: el sandbox, verificado ────────────────────────────────
    staging = _leer_archivo_env(ROOT / "env.staging")
    sand_url = staging.get("SUPABASE_DB_URL", "")
    sand_ref = _ref(sand_url)
    if not sand_ref:
        sys.exit("ABORT: env.staging (en la raíz del repo) no tiene un "
                 "SUPABASE_DB_URL válido.")
    prod_ref_env = staging.get("SUPABASE_PROD_REF", "").strip()
    if sand_ref.startswith(REF_KUBERA_PROD) or (prod_ref_env and sand_ref == prod_ref_env):
        sys.exit(f"ABORT: el destino ({sand_ref[:8]}…) es la BD kubera de "
                 "PRODUCCIÓN. Este script solo escribe en el SANDBOX.")
    print(f"  sandbox verificado    : {sand_ref[:8]}…")

    # ── 2. Los secretos de producción, por stdin ─────────────────────────────
    secretos = _leer_stdin()
    prod_url = secretos.get("PROD_SUPABASE_DB_URL", "")
    clave = secretos.get("DB_ENCRYPTION_KEY", "")
    prod_ref = _ref(prod_url)
    if not prod_ref or not prod_ref.startswith(REF_KUBERA_PROD):
        sys.exit("ABORT: PROD_SUPABASE_DB_URL no llegó o no es la BD kubera de "
                 "producción (solo se usa para LEER ops.ml_tokens).")
    if not clave:
        sys.exit("ABORT: falta DB_ENCRYPTION_KEY en stdin.")

    # ── 3. supabase_db apuntado al SANDBOX antes de importar config ─────────
    # Las variables de entorno pisan al archivo en pydantic-settings: por eso
    # se fuerza SUPABASE_DB_URL aquí, aunque el shell traiga otra.
    os.environ["APP_ENV"] = "staging"
    os.environ["SUPABASE_DB_URL"] = sand_url
    os.environ["DB_HOST"] = "127.0.0.1"          # env.staging trae el MySQL de
    os.environ["DB_PORT"] = "1"                  # producción: aquí no se toca
    os.environ["SUPABASE_READ_TOKENS"] = "false"
    os.environ["SUPABASE_WRITE_TOKENS"] = "false"
    sys.path.insert(0, str(BACKEND))

    import logging
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    from config import settings  # noqa: E402
    from services import calidad_ml, supabase_db  # noqa: E402

    if settings.supabase_db_url != sand_url or _ref(supabase_db.settings.supabase_db_url) != sand_ref:
        sys.exit("ABORT: supabase_db no quedó apuntando al sandbox.")

    # ── 4. El objetivo, del channel.listings del SANDBOX ────────────────────
    try:
        objetivo = calidad_ml.objetivo(args.limite or None, cuenta=args.cuenta,
                                       forzar=args.forzar)
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "pgcode", None) == "42P01":
            sys.exit("ABORT: el sandbox no tiene enrich.listing_health. Aplica "
                     "supabase/migrations/0053_enrich_listing_health.sql primero.")
        if getattr(exc, "pgcode", None) == "42703":
            sys.exit("ABORT: a enrich.listing_health del sandbox le faltan "
                     "columnas: no es la de supabase/migrations/"
                     "0053_enrich_listing_health.sql.")
        sys.exit(f"ABORT: no pude leer el objetivo del sandbox ({type(exc).__name__}: {exc}).")
    por_cuenta = Counter(f["cuenta"] for f in objetivo)
    print(f"  activas sin captura de hoy: {len(objetivo)} "
          f"({', '.join(f'{c} {n}' for c, n in sorted(por_cuenta.items())) or '—'})")
    if not objetivo:
        print("Nada que medir: todas las activas del sandbox ya tienen captura de hoy.")
        return 0

    # ── 5. Tokens de producción (solo lectura) ───────────────────────────────
    tokens = _leer_tokens(prod_url, clave)
    del clave, secretos
    for c in CUENTAS:
        print(f"  token {c:<14}: {'leído' if tokens.get(c) else 'NO HAY'}")

    # ── 6. Medir ─────────────────────────────────────────────────────────────
    pares = [(f["item_id"], f["cuenta"]) for f in objetivo]
    t0 = time.time()
    filas, n = asyncio.run(_medir_todo(calidad_ml, pares, tokens))
    tokens.clear()
    print(f"\n  medidas en {time.time() - t0:.0f} s: " +
          " · ".join(f"{k} {v}" for k, v in n.items()))

    medidas = [f for f in filas if f["estado"] == calidad_ml.ESTADO_MEDIDA]
    niveles = Counter((f["level"], f["level_wording"]) for f in medidas)
    for (lvl, w), k in niveles.most_common():
        print(f"    nivel {lvl!s:<10} {w!s:<14} {k}")
    if medidas:
        scores = [f["score"] for f in medidas]
        print(f"    score min {min(scores)} · promedio {sum(scores) / len(scores):.1f} "
              f"· max {max(scores)} · pendientes promedio "
              f"{sum(f['n_pendientes'] for f in medidas) / len(medidas):.1f}")
        acciones = Counter(p.get("accion") or p.get("titulo")
                           for f in medidas for b in f["buckets"]
                           for p in b["pendientes"])
        for accion, k in acciones.most_common(5):
            print(f"    ML pide «{accion}»: {k}")
    motivos = Counter(f["motivo"] for f in filas
                      if f["estado"] == calidad_ml.ESTADO_NO_CALCULADA)
    for motivo, k in motivos.most_common(3):
        print(f"    no calculada ({k}): {motivo}")
    _resumir_experiencia(calidad_ml, filas, n)
    if n.get("sin_token"):
        print("\n  AVISO: hubo 401 o faltó token. Este script NUNCA renueva (rotaría "
              "el refresh_token de producción): espera a que el renovador de "
              "producción corra y vuelve a intentarlo.")

    # ── 7. Escribir (solo con --real) ────────────────────────────────────────
    if not args.real:
        print(f"\nEn seco: no se escribió nada ({len(filas)} filas listas). "
              "Corre con --real para guardarlas en el sandbox.")
    else:
        # Sin mapa de cuentas: `guardar` lo lee del sandbox UNA vez (una sola
        # llamada con todas las filas = una transacción).
        guardadas = calidad_ml.guardar(filas)
        print(f"\n  guardadas en el sandbox: {guardadas} publicaciones")
        _resumir_historia(supabase_db)

    respondidas = n.get("ok", 0) + n.get("no_calculada", 0)
    if respondidas < len(pares) / 2:
        print(f"\nERROR: solo {respondidas} de {len(pares)} obtuvieron respuesta de ML.")
        return 1
    print("\nListo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
