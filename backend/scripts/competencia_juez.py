"""
competencia_juez.py — Juzga por lotes los rivales que quedaron SIN VEREDICTO.
Solo sandbox, en seco por defecto y con tope en dólares.

POR QUÉ EXISTE
--------------
El juez (`services/competencia_juez.py`) corre enganchado a cada captura, pero ahí
tiene 60 segundos de reloj y un tope diario: lo que no alcanza «queda pendiente
para el script por lotes». Este es ese script. También es el que hace la primera
pasada sobre todo lo que ya estaba capturado, y el que vuelve a juzgar cuando
cambia un título o se sube `VERSION_PROMPT`.

Sin él, un SKU con 4 rivales juzgados de 10 se queda así para siempre, y quien
lo lee tiene que contestar «no sé» en vez de «tiene N comparables»: la mejora de
términos ni siquiera lo considera («rivales sin juzgar: primero el juez»).

LA COLA
-------
`competencia_juez.skus_con_pendientes()`: SKUs con al menos un rival AJENO y con
precio, en su término asignado, que nunca se juzgó o cuyo veredicto ya no vale
(cambió el título del rival, el nuestro o la versión del prompt). Va en orden
alfabético de SKU, y el orden no decide nada: la corrida es reanudable. Lo
juzgado deja de estar pendiente y la siguiente pasada sigue donde quedó esta.

Se juzga en tandas de 50 SKUs con UN solo `Presupuesto` para toda la corrida. Un
tope que se estrena en cada tanda nunca se alcanza.

CANDADOS (viven en el CÓDIGO, no en un argumento)
-------------------------------------------------
1. **Solo el sandbox.** El destino sale de `SUPABASE_DB_URL` de `env.staging` (raíz
   del repo), leído con un parser propio, y se aborta si la ref del DSN no es la
   del sandbox (`yvootpbz`) o si `tukwcvsi` —producción— aparece en cualquier
   parte. No hay argumento que lo cambie: un `--acepto-destino <ref>` genérico
   dejaría correr esto contra producción con solo teclear la ref, saltándose el
   doble candado. Se abrirá a producción en otro cambio, con su acta.

2. **El entorno se fuerza ANTES de importar `config`.** En pydantic-settings las
   variables del proceso pisan al archivo: una `SUPABASE_DB_URL` exportada en la
   terminal ganaría a `env.staging` sin que nadie lo viera. Y `env.staging` trae
   el MySQL y el Woo de PRODUCCIÓN. Por eso aquí se fijan `APP_ENV=staging`, el
   DSN del archivo, MySQL apagado y sin host, Woo y Slack vacíos, y los tres
   flags de tokens en falso. Después de importar se comprueba que `settings`
   quedó así; si no, se aborta.

3. **Nunca se renueva un token de Mercado Libre.** `meli.refrescar_token` se
   anula. El juez no llama a ML, pero importa medio backend, y un 401 con el
   MySQL de producción a la mano quemaría el `refresh_token` VIVO: ML lo rota en
   cada uso y producción se quedaría con uno muerto (regla 8: paran los pedidos).

4. **Las llaves, solo por stdin.** `env.staging` no trae llaves de IA y no debe
   traerlas: ese archivo se copia entre worktrees. Tecleadas en la terminal
   quedan en el historial y en la lista de procesos. Llegan como `CLAVE=valor`
   por una tubería, se ponen en el entorno de ESTE proceso y nunca se imprimen.
   De stdin solo se toman cuatro nombres (`DEEPSEEK_API_KEY`,
   `DEEPSEEK_BASE_URL`, `ANTHROPIC_API_KEY`, `APIFY_API_KEY`): si alguien manda
   la lista entera de variables de Railway, el DSN de producción que viene ahí
   NO entra. Y las que ya estuvieran en el entorno se descartan: «solo por
   stdin» es literal.

5. **El registro va en WARNING.** `httpx` a INFO imprime cada URL que pide, y
   Apify manda su token en la URL.

DOS GUARDIAS ANTES DE GASTAR
----------------------------
1. **El modelo tiene que tener precio** (`ia_json.MODELOS`). El nombre llega de
   una variable de entorno —texto libre— y un modelo sin precio dejaría ciego al
   tope: sumaría cero mientras la cuenta corre.
2. **Tiene que estar la llave del proveedor de ESE modelo.** `ia_json` no cambia
   de proveedor a escondidas: sin llave cada llamada vuelve `ok: False` y la
   corrida terminaría «bien» sin haber juzgado nada.

Las dos se revisan también en seco, y cualquiera de las dos sale con 2: el
ensayo tiene que fallar donde fallaría la corrida de verdad. Un plan que no se
puede ejecutar no es un plan.

EN SECO POR DEFECTO
-------------------
Sin `--aplicar` no llama a la IA ni escribe: cuenta lo pendiente leyendo el
sandbox (`competencia_juez.filas`, por tandas) e imprime el plan y su costo.

El costo es una ESTIMACIÓN, no una medición: ~0.08 USD por cada 1,000 rivales
con `deepseek-flash`, escalado por el precio de ENTRADA de `ia_json.MODELOS` si
el modelo es otro. Con los modelos de Claude se queda corta (la salida pesa más
y piensan antes de contestar). La corrida real dice el número; la factura manda.

Para escribir hacen falta las DOS cosas: `--aplicar` y `--acepto-destino
yvootpbz`. La segunda no elige el destino —eso lo decide el candado 1—: es la
firma de que quien lo corre sabe a dónde va.

LO QUE NO ALCANZÓ SE DICE
-------------------------
Al terminar imprime veredictos guardados, rivales que el modelo dejó sin juzgar,
llamadas, dólares gastados del tope y por qué se detuvo. Y vuelve a contar lo
pendiente en los SKUs de la corrida: una pasada que se calla lo que dejó fuera
se lee como «ya está todo juzgado».

Los rivales de un SKU SIN título nuestro no se pueden juzgar (no hay contra qué
compararlos) y se reportan aparte, en seco y al final.

USO (Git Bash; las llaves NUNCA en la línea de comando ni en un archivo)
------------------------------------------------------------------------
Es UN solo comando: Railway entrega las variables, `grep` deja pasar las cuatro
llaves y el script las lee de la tubería.

    railway variables --kv -s BackendOmnicanal -e production -p <proyecto> \\
      | grep -E '^(DEEPSEEK_API_KEY|DEEPSEEK_BASE_URL|ANTHROPIC_API_KEY|APIFY_API_KEY)=' \\
      | python backend/scripts/competencia_juez.py --llaves-stdin

    ... | python backend/scripts/competencia_juez.py --llaves-stdin --limite 40
    ... | python backend/scripts/competencia_juez.py --llaves-stdin --sku ABC-0001-NEG
    ... | python backend/scripts/competencia_juez.py --llaves-stdin \\
              --aplicar --acepto-destino yvootpbz --tope 2

Sale con 0 si juzgó (o si no había nada que juzgar), con 1 si la corrida no
guardó ningún veredicto o se detuvo por fallos del proveedor, y con 2 si un
candado o una guardia no la dejó empezar.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
ENV_STAGING = ROOT / "env.staging"

REF_SANDBOX = "yvootpbz"
REF_PRODUCCION = "tukwcvsi"

# Lo ÚNICO que se toma de stdin. Es una lista de permitidos, no de prohibidos.
LLAVES = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "ANTHROPIC_API_KEY", "APIFY_API_KEY")

# Lo que se fuerza en el entorno antes de importar `config` (candado 2). El DSN
# se suma al vuelo. Este bloque y sus funciones son GEMELOS de los de
# `competencia_mejorar_terminos.py`: si se toca uno, se toca el otro.
FORZADAS = {
    "APP_ENV": "staging",
    "DB_HOST": "127.0.0.1",             # env.staging trae el MySQL de producción:
    "DB_PORT": "1",                     # sin host y apagado, aquí no se toca
    "MYSQL_ENABLED": "false",
    "WPDB_HOST": "127.0.0.1",
    "WC_URL": "",
    "SLACK_WEBHOOK_URL": "",
    "SUPABASE_READ_TOKENS": "false",
    "SUPABASE_WRITE_TOKENS": "false",
    "TOKENS_SOLO_KUBERA": "false",
}

TOPE_USD = 2.0
TANDA = 50              # SKUs por llamada a `juzgar_skus`
TANDA_CONTEO = 200      # SKUs por consulta al contar (unas 2,000 filas por vuelta)
HILOS = 4

# ESTIMACIÓN de trabajo, no medición: una llamada por SKU con ~10 rivales, a
# precio de lista. Se escala por el precio de entrada si el modelo es otro.
USD_POR_MIL_RIVALES = 0.08
MODELO_DE_LA_ESTIMACION = "deepseek-flash"


# ── Candados: nada de esto importa `config` ──────────────────────────────────

def _abortar(mensaje: str) -> NoReturn:
    """Un candado o una guardia no dejó empezar. Siempre sale con 2."""
    sys.stdout.flush()      # que el ABORT salga DESPUÉS de lo ya impreso
    print(f"ABORT: {mensaje}", file=sys.stderr)
    raise SystemExit(2)


def _leer_archivo_env(ruta: Path) -> dict[str, str]:
    """`CLAVE=valor` de un archivo de entorno. Parser PROPIO a propósito: el
    destino se decide ANTES de importar `config`, que es quien lee este archivo
    con pydantic. Gana la última aparición de cada clave, igual que allá."""
    vals: dict[str, str] = {}
    if not ruta.is_file():
        return vals
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        s = linea.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.removeprefix("export ").partition("=")
        v = v.strip()
        if v[:1] in ("'", '"'):
            fin = v.find(v[0], 1)
            v = v[1:fin] if fin > 0 else v[1:]
        else:
            # Un «#» solo abre comentario tras un espacio: pegado al valor puede
            # ser parte de una contraseña.
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        vals[k.strip()] = v
    return vals


def _ref(dsn: str) -> str:
    """La ref del proyecto según el USUARIO («postgres.<ref>», el del pooler) o el
    HOST directo («db.<ref>.supabase.co»); '' si no la trae. No se busca en
    cualquier parte del DSN: una contraseña podría contenerla sin que el destino
    sea ese proyecto."""
    try:
        partes = urlsplit((dsn or "").strip())
        usuario, host = (partes.username or "").lower(), (partes.hostname or "").lower()
    except ValueError:
        return ""
    m = (re.fullmatch(r"postgres\.([a-z0-9]+)", usuario)
         or re.fullmatch(r"db\.([a-z0-9]+)\.supabase\.co", host))
    return m.group(1) if m else ""


def _destino() -> str:
    """El DSN del sandbox, verificado. Nunca se imprime."""
    dsn = _leer_archivo_env(ENV_STAGING).get("SUPABASE_DB_URL", "")
    if not dsn:
        _abortar("env.staging (en la raíz del repo) no existe o no trae SUPABASE_DB_URL.")
    if REF_PRODUCCION in dsn.lower():
        _abortar("el SUPABASE_DB_URL de env.staging es la base kubera de PRODUCCIÓN "
                 f"({REF_PRODUCCION}). Este script solo corre contra el sandbox.")
    if not _ref(dsn).startswith(REF_SANDBOX):
        _abortar(f"el SUPABASE_DB_URL de env.staging no es el sandbox ({REF_SANDBOX}). "
                 "Producción y cualquier otra base están bloqueadas en el código.")
    return dsn


def _llaves_de_stdin() -> dict[str, str]:
    """Las llaves que llegaron por la tubería. Solo los nombres de `LLAVES`."""
    if sys.stdin is None or sys.stdin.isatty():
        _abortar("--llaves-stdin espera líneas CLAVE=valor por una tubería, no tecleadas. "
                 "Ver USO en el encabezado.")
    vals: dict[str, str] = {}
    for linea in sys.stdin.read().splitlines():
        k, igual, v = linea.strip().partition("=")
        if igual and k.strip() in LLAVES:
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _preparar_entorno(dsn: str, llaves: dict[str, str]) -> None:
    """Deja el entorno del proceso como lo necesita el sandbox. ANTES de `config`."""
    os.environ.update(FORZADAS)
    os.environ["SUPABASE_DB_URL"] = dsn
    for k in LLAVES:
        if k.endswith("_API_KEY"):
            # Vacía si no llegó por stdin: una llave heredada de la terminal (o
            # pegada en env.staging) no cuenta.
            os.environ[k] = llaves.get(k, "")
        elif llaves.get(k):
            os.environ[k] = llaves[k]
        else:
            # La URL base no es un secreto, pero decide A DÓNDE viaja la llave:
            # sin stdin vale la del archivo o la de fábrica, nunca la del shell.
            os.environ.pop(k, None)
    # Lo mismo del lado de Claude, y aquí no pasa por `config`: el SDK de
    # Anthropic lee estas dos DIRECTO del entorno. Una terminal que las traiga
    # (la de un asistente de código, un proxy) mandaría la llave de Railway a
    # esa otra dirección.
    for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(k, None)


def _cargar_backend(dsn: str) -> tuple[Any, Any, Any]:
    """Importa `config` y los servicios YA con el entorno forzado, comprueba que
    quedó como se pidió y anula la renovación de tokens de ML.
    → (settings, ia_json, competencia_juez)"""
    sys.path.insert(0, str(BACKEND))
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    from config import settings  # noqa: E402
    from services import supabase_db  # noqa: E402

    esperado = {"supabase_db_url": dsn, "app_env": "staging", "mysql_enabled": False,
                "db_host": "127.0.0.1", "wc_url": "", "slack_webhook_url": "",
                "supabase_read_tokens": False, "supabase_write_tokens": False,
                "tokens_solo_kubera": False}
    torcidas = sorted(k for k, v in esperado.items() if getattr(settings, k, None) != v)
    if torcidas or supabase_db.settings.supabase_db_url != dsn:
        # Pasa si `config` ya estaba importado con otro entorno. Solo se dicen
        # los NOMBRES: el valor de una de ellas es el DSN.
        _abortar("la configuración no quedó apuntando al sandbox "
                 f"({', '.join(torcidas) or 'supabase_db'}). No se hace nada.")

    from services import meli  # noqa: E402

    meli.refrescar_token = lambda *a, **k: None     # candado 3

    from services import competencia_juez, ia_json  # noqa: E402

    return settings, ia_json, competencia_juez


def _corto(exc: BaseException) -> str:
    """Un error en una línea. Los de psycopg2 no llevan la contraseña."""
    texto = (str(exc).strip().splitlines() or [""])[0]
    return f"{type(exc).__name__}: {texto[:160]}"


def _llave_del_proveedor(settings: Any, ia_json: Any, modelo: str) -> tuple[str, bool]:
    """(proveedor, ¿está su llave?). Nunca devuelve la llave."""
    proveedor = ia_json.proveedor_de(modelo) or ""
    llave = settings.deepseek_api_key if proveedor == "deepseek" else settings.anthropic_api_key
    return proveedor, bool(llave)


# ── El conteo y la estimación ────────────────────────────────────────────────

def contar(cj: Any, skus: list[str]) -> dict[str, int]:
    """Lo pendiente de esos SKUs, con la MISMA regla que usa `juzgar_skus`
    (`pendiente` y con título nuestro), leído por tandas.

    → {skus, rivales, llamadas, skus_sin_titulo, rivales_sin_titulo}"""
    n = {"skus": 0, "rivales": 0, "llamadas": 0, "skus_sin_titulo": 0, "rivales_sin_titulo": 0}
    for i in range(0, len(skus), TANDA_CONTEO):
        juzgables: dict[str, int] = {}
        sin_titulo: dict[str, int] = {}
        for f in cj.filas(skus[i:i + TANDA_CONTEO]):
            if f["pendiente"]:
                d = juzgables if f["titulo_nuestro"] else sin_titulo
                d[f["sku"]] = d.get(f["sku"], 0) + 1
        n["skus"] += len(juzgables)
        n["rivales"] += sum(juzgables.values())
        # Una llamada por SKU, y otra por cada `TROZO` rivales que pase.
        n["llamadas"] += sum(-(-c // cj.TROZO) for c in juzgables.values())
        n["skus_sin_titulo"] += len(sin_titulo)
        n["rivales_sin_titulo"] += sum(sin_titulo.values())
    return n


def factor_de_precio(ia_json: Any, modelo: str) -> float:
    """Cuánto más caro es `modelo` que el de la estimación, por precio de entrada."""
    base = (ia_json.MODELOS.get(MODELO_DE_LA_ESTIMACION) or {}).get("entrada")
    return float(ia_json.MODELOS[modelo]["entrada"]) / float(base) if base else 1.0


def estimar_usd(ia_json: Any, modelo: str, rivales: int) -> float:
    return rivales / 1000 * USD_POR_MIL_RIVALES * factor_de_precio(ia_json, modelo)


def _imprimir_pendiente(n: dict[str, int], titulo: str) -> None:
    print(f"  {titulo:<19}: {n['rivales']:,} rivales en {n['skus']:,} SKUs")
    if n["rivales_sin_titulo"]:
        print(f"  {'sin título nuestro':<19}: {n['rivales_sin_titulo']:,} rivales en "
              f"{n['skus_sin_titulo']:,} SKUs — NO se pueden juzgar: no hay contra qué")


# ── Principal ────────────────────────────────────────────────────────────────

def main() -> int:
    # En Windows, con stdout a una tubería, Python escribe en cp1252 y los
    # acentos y las rayas tumban el script a media corrida. Va ANTES de leer los
    # argumentos para que también `--help` salga legible.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    ap.add_argument("--tope", type=float, default=TOPE_USD,
                    help=f"Tope de gasto de IA de la corrida, en USD (default {TOPE_USD}).")
    ap.add_argument("--limite", type=int, default=0,
                    help="Cuántos SKUs a lo más (0 = todos los que tengan pendientes).")
    ap.add_argument("--sku", action="append", default=[],
                    help="Solo este SKU. Se puede repetir.")
    ap.add_argument("--hilos", type=int, default=HILOS,
                    help=f"SKUs en paralelo (default {HILOS}). El módulo topa en 4 las "
                         "llamadas simultáneas al LLM, cuente quien cuente.")
    ap.add_argument("--modelo", default="",
                    help="Una llave de ia_json.MODELOS (default: competencia_juez_modelo).")
    ap.add_argument("--llaves-stdin", action="store_true",
                    help="Leer las llaves de IA por stdin (CLAVE=valor). Es la única vía.")
    ap.add_argument("--aplicar", action="store_true",
                    help="Llama a la IA y guarda los veredictos. Sin esto, solo el plan.")
    ap.add_argument("--acepto-destino", default="",
                    help=f"Con --aplicar: la ref del sandbox, {REF_SANDBOX}.")
    args = ap.parse_args()
    if args.tope <= 0:
        ap.error("--tope tiene que ser mayor que 0.")
    if args.limite < 0 or args.hilos < 1:
        ap.error("--limite no puede ser negativo y --hilos tiene que ser al menos 1.")

    print("═══ Competencia · juez de rivales por lotes (SANDBOX) ═══")

    # ── 1. El destino y la firma, antes de leer una sola llave ───────────────
    dsn = _destino()
    if args.acepto_destino and args.acepto_destino != REF_SANDBOX:
        _abortar(f"--acepto-destino solo admite {REF_SANDBOX}: este script no conoce "
                 "otro destino.")
    if args.aplicar and args.acepto_destino != REF_SANDBOX:
        _abortar(f"--aplicar exige además --acepto-destino {REF_SANDBOX} (los dos).")
    print(f"  destino            : sandbox {REF_SANDBOX}… (de env.staging)")

    # ── 2. Llaves y entorno, y recién entonces el backend ────────────────────
    llaves = _llaves_de_stdin() if args.llaves_stdin else {}
    _preparar_entorno(dsn, llaves)
    llaves.clear()
    settings, ia_json, cj = _cargar_backend(dsn)

    # ── 3. Las dos guardias ─────────────────────────────────────────────────
    modelo = (args.modelo or settings.competencia_juez_modelo or "").strip()
    if modelo not in ia_json.MODELOS:
        _abortar(f"el modelo {modelo!r} no tiene precio en ia_json.MODELOS y no se puede "
                 f"aplicar el tope. Valen: {', '.join(sorted(ia_json.MODELOS))}.")
    proveedor, hay_llave = _llave_del_proveedor(settings, ia_json, modelo)
    if not hay_llave:
        nombre = "DEEPSEEK_API_KEY" if proveedor == "deepseek" else "ANTHROPIC_API_KEY"
        _abortar(f"falta {nombre}, la llave del proveedor de {modelo}. Llega solo por "
                 "stdin con --llaves-stdin (ver USO en el encabezado).")
    print(f"  modelo             : {modelo} ({proveedor}) · llave presente")

    if not cj.tablas_listas():
        _abortar("el sandbox no tiene las tablas del juez (o no se pudo conectar). Aplica "
                 "primero supabase/migrations/0063_enrich_market_rival_juicio.sql.")

    # ── 4. La cola y el plan ─────────────────────────────────────────────────
    try:
        if args.sku:
            skus = list(dict.fromkeys(s.strip() for s in args.sku if s.strip()))
            origen = f"los {len(skus)} pedidos con --sku"
            if args.limite:
                skus = skus[:args.limite]
        else:
            skus = cj.skus_con_pendientes(args.limite or None)
            origen = "la cola completa" if not args.limite else f"los primeros {args.limite}"
        antes = contar(cj, skus)
    except Exception as exc:                                        # noqa: BLE001
        print(f"ERROR: no se pudo leer lo pendiente del sandbox ({_corto(exc)}).",
              file=sys.stderr)
        return 1

    print(f"  SKUs revisados     : {len(skus):,} ({origen})")
    _imprimir_pendiente(antes, "por juzgar")
    if not antes["rivales"]:
        print("\nNada que juzgar: ningún rival pendiente en esos SKUs.")
        return 0

    estimado = estimar_usd(ia_json, modelo, antes["rivales"])
    factor = factor_de_precio(ia_json, modelo)
    escala = ("" if modelo == MODELO_DE_LA_ESTIMACION
              else f", ×{factor:.1f} por el precio de entrada de {modelo}")
    print(f"  llamadas           : ~{antes['llamadas']:,} (una por SKU, más una por cada "
          f"{cj.TROZO} rivales que pase)")
    print(f"  costo ESTIMADO     : ~${estimado:.3f}  (≈ ${USD_POR_MIL_RIVALES:.2f} por 1,000 "
          f"rivales con {MODELO_DE_LA_ESTIMACION}{escala})")
    if estimado <= args.tope:
        print(f"  tope               : ${args.tope:.2f} → alcanza, según la estimación")
    else:
        caben = int(antes["rivales"] * args.tope / estimado)
        print(f"  tope               : ${args.tope:.2f} → NO alcanza: cubriría ~{caben:,} "
              f"rivales; ~{antes['rivales'] - caben:,} quedarían pendientes")

    if not args.aplicar:
        print("\nEn seco: no se llamó a la IA ni se escribió nada. Para juzgar: "
              f"--aplicar --acepto-destino {REF_SANDBOX}.")
        return 0

    # ── 5. Juzgar, con UN presupuesto para toda la corrida ───────────────────
    presupuesto = cj.Presupuesto(args.tope)
    total = {"veredictos": 0, "sin_juzgar": 0, "llamadas": 0, "juzgados_skus": 0, "fallidos": 0}
    detenido: str | None = None
    ultimo_motivo: str | None = None
    fallo: str | None = None
    tandas = -(-len(skus) // TANDA)
    t0 = time.time()
    print()
    for i in range(0, len(skus), TANDA):
        if not presupuesto.puede():
            detenido = "tope de gasto"
            break
        try:
            r = cj.juzgar_skus(skus[i:i + TANDA], presupuesto=presupuesto, modelo=modelo,
                               hilos=args.hilos)
        except Exception as exc:                                    # noqa: BLE001
            fallo = _corto(exc)
            break
        for k in total:
            total[k] += int(r.get(k) or 0)
        print(f"  tanda {i // TANDA + 1}/{tandas}: {r.get('veredictos', 0)} veredictos · "
              f"{r.get('sin_juzgar', 0)} sin juzgar · va ${presupuesto.gastado:.4f} "
              f"de ${args.tope:.2f}", flush=True)
        if r.get("detenido"):
            detenido, ultimo_motivo = r["detenido"], r.get("ultimo_motivo")
            break

    # ── 6. Lo que se hizo y lo que NO ────────────────────────────────────────
    print("\n── Resultado ──")
    print(f"  veredictos         : {total['veredictos']:,} guardados en {total['juzgados_skus']:,} "
          f"SKUs, en {(time.time() - t0) / 60:.1f} min")
    print(f"  sin juzgar         : {total['sin_juzgar']:,} rivales que el modelo no contestó o "
          f"contestó mal ({total['fallidos']:,} SKUs sin ningún veredicto)")
    print(f"  llamadas           : {total['llamadas']:,}")
    print(f"  gastado            : ${presupuesto.gastado:.4f} de ${args.tope:.2f}  (a precio de "
          "lista; la factura manda)")
    if detenido:
        print(f"  SE DETUVO          : {detenido}"
              + (f" — {ultimo_motivo}" if ultimo_motivo else ""))
    if fallo:
        print(f"  FALLÓ              : {fallo}")
    try:
        # Se vuelve a contar en la base: `sin_juzgar` no incluye los SKUs a los
        # que el tope o el fallo ni siquiera dejaron llegar.
        _imprimir_pendiente(contar(cj, skus), "QUEDA PENDIENTE")
    except Exception as exc:                                        # noqa: BLE001
        print(f"  (no se pudo recontar lo pendiente: {_corto(exc)})")

    if fallo or not total["veredictos"] or (detenido and detenido != "tope de gasto"):
        print("\nTerminó CON PROBLEMAS: ver arriba.")
        return 1
    print("\nListo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
