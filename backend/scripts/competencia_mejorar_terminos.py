"""
competencia_mejorar_terminos.py — Busca un término mejor para los SKUs cuya
búsqueda no trae rivales de verdad, lo MIDE y lo deja como sugerencia. Solo
sandbox, en seco por defecto, con tope en dólares y en páginas.

POR QUÉ EXISTE
--------------
El juez limpia lo que la búsqueda trae; no puede inventar lo que no trae. Hay
SKUs cuyo término apunta a otra cosa y ahí no hay nada que filtrar: hay que
buscar distinto (`services/competencia_mejora.py` cuenta el caso completo).

El botón del panel lo hace para UN SKU. Este script lo hace por lotes, que es
además como sale barato: Apify cobra por tiempo de cómputo y el arranque del
navegador se paga por corrida, así que los candidatos se miden juntos, en tandas
de 20 páginas, y no de uno en uno.

LA COLA
-------
`competencia_mejora.elegibles()`: SKUs cuyo término ya se midió Y se juzgó
COMPLETO y aun así tienen menos de 3 comparables, agrupados por término (un
término lo comparten varias variantes y se pide UNA propuesta por grupo).

`elegibles` no garantiza un orden, y sin orden el plan en seco y la corrida real
podrían tomar grupos distintos con el mismo `--limite`. Aquí se ordenan: primero
los grupos con más SKUs (una propuesta arregla más variantes), luego los que
tienen menos comparables, y el término en orden alfabético como desempate.

Lo que queda FUERA se imprime con su motivo y su cuenta. El más común al
principio es «rivales sin juzgar: primero el juez»: esos SKUs no entran hasta
que corra `competencia_juez.py`, porque con rivales sin juzgar no se sabe si el
término está mal.

CANDADOS (viven en el CÓDIGO, no en un argumento)
-------------------------------------------------
Los mismos cinco de `competencia_juez.py`, y aquí pesan más porque este script sí
mide con Apify y pasa por el código que le pide datos a Mercado Libre:

1. **Solo el sandbox.** El destino sale de `SUPABASE_DB_URL` de `env.staging` (raíz
   del repo), con un parser propio; se aborta si la ref no es `yvootpbz` o si
   `tukwcvsi` aparece en cualquier parte del DSN. Ningún argumento lo cambia.
2. **El entorno se fuerza ANTES de importar `config`**: `APP_ENV=staging`, el DSN
   del archivo, MySQL apagado y sin host, Woo y Slack vacíos, los tres flags de
   tokens en falso. `env.staging` trae el MySQL y el Woo de PRODUCCIÓN, y una
   variable heredada de la terminal pisaría al archivo. Tras importar se
   comprueba que `settings` quedó así.
3. **Nunca se renueva un token de Mercado Libre.** `medir_busquedas` pide reseñas
   y visitas a la API de ML; con el MySQL de producción a la mano, un 401 haría
   renovar, y renovar rota el `refresh_token` VIVO: producción se quedaría con
   uno muerto y pararían los pedidos (regla 8). Con MySQL apagado no hay de
   dónde leer un token, y además `meli.refrescar_token` queda anulado.
4. **Las llaves, solo por stdin**, como `CLAVE=valor` por una tubería. Solo se
   toman cuatro nombres (`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`,
   `ANTHROPIC_API_KEY`, `APIFY_API_KEY`) y nunca se imprimen; las que ya
   estuvieran en el entorno se descartan.
5. **El registro va en WARNING**: `httpx` a INFO imprime cada URL que pide, y
   Apify manda su token en la URL.

GUARDIAS ANTES DE GASTAR
------------------------
Las tres primeras se revisan también en seco y salen con 2: el ensayo tiene que
fallar donde fallaría la corrida de verdad.

1. **El modelo tiene precio** (`ia_json.MODELOS`). Sin precio, el tope es ciego.
2. **Está la llave del proveedor de ese modelo.** Sin ella cada propuesta vuelve
   `ok: False` y la corrida terminaría sin haber hecho nada.
3. **Apify está disponible.** Sin `APIFY_API_KEY` el raspado vuelve vacío sin
   error, y un candidato que nunca se midió quedaría anotado como probado.
4. **El tope de IA alcanza para JUZGAR lo que se va a medir** (solo con
   `--aplicar`). Un candidato cuyo juicio queda INCOMPLETO no se pierde —queda
   `error`, reintentable— y si las propuestas se comen el tope `mejorar` ya no
   mide; pero todo eso es trabajo pagado que no dio respuesta y hay que repetir.
   Si el tope no cubre la estimación de propuestas + juez, no se empieza.
5. **El crédito de la cuenta de Apify** (solo con `--aplicar`). El tope mensual es
   de la CUENTA y se comparte con el raspado de Alibaba: con menos de $10 de
   margen no se empieza algo que puede morirse a la mitad.

EN SECO POR DEFECTO
-------------------
Sin `--aplicar` no llama a la IA ni a Apify y no escribe: lee del sandbox quién
es elegible, imprime cuántos grupos y SKUs, el desglose de los que quedaron
fuera y el costo. Para escribir hacen falta las DOS cosas: `--aplicar` y
`--acepto-destino yvootpbz`.

El costo son dos cuentas separadas y las dos son ESTIMACIONES:

  · IA: ~$0.001 por grupo (la propuesta), más el juez de los candidatos a
    ~$0.08 por 1,000 rivales. Con `deepseek-flash`; otro modelo se escala por su
    precio de entrada. `--tope` acota ESTO y solo esto.
  · Apify: HASTA 2 páginas por grupo a ~$0.035 reales por página. Ese número no
    es el de nuestra bitácora: `ops.process_log` lee el costo cuando la corrida
    termina y Apify sigue liquidando el proxy después, así que SUBCUENTA ~2.3
    veces (medido el 2-sep-2026, ver `competencia_barrido.py`). 0.035 es el
    ~0.015 de la bitácora por esa corrección. Apify se acota con
    `--max-paginas`, no con `--tope`: su costo real solo se sabe al liquidar.

NADA SE APLICA
--------------
`mejorar` deja SUGERENCIAS (intentos en estado `medido`). Ningún SKU cambia de
término aquí. Aceptar una sugerencia es un acto de un admin en la pantalla de
Competencia, porque cambiar el término mueve el «precio de mercado» que ven los
KAM en Publicaciones.

LO QUE ESTE SCRIPT NO VE (y por eso lo dice al correr)
------------------------------------------------------
Sin token de ML, lo medido aquí no trae reseñas ni visitas, y una publicación
NUESTRA que ML liste como producto de vendedor (`MLMU…`) no se resuelve a su
publicación real: puede quedar guardada como rival, el juez la dará por «mismo»
y contará como comparable del candidato. Antes de aceptar una sugerencia hay que
mirar sus rivales en la pantalla.

Un candidato con el juicio a medias (un rival sin contestar, el proveedor caído,
el tope agotado) NO cae en «sin mejora»: queda en `errores`, reintentable. Solo
en su segunda ronda, si el fallo es del propio candidato (cero filas, el muro de
ML, un rival que el modelo vuelve a dejar sin contestar), se da por respuesta.

LO QUE NO ALCANZÓ SE DICE
-------------------------
Al terminar: sugerencias, candidatos sin mejora, bloqueados, errores, páginas
pagadas, dólares de IA gastados del tope, SKUs sin cubrir y por qué se detuvo.

USO (Git Bash; las llaves NUNCA en la línea de comando ni en un archivo)
------------------------------------------------------------------------
Es UN solo comando: Railway entrega las variables, `grep` deja pasar las cuatro
llaves y el script las lee de la tubería.

    railway variables --kv -s BackendOmnicanal -e production -p <proyecto> \\
      | grep -E '^(DEEPSEEK_API_KEY|DEEPSEEK_BASE_URL|ANTHROPIC_API_KEY|APIFY_API_KEY)=' \\
      | python backend/scripts/competencia_mejorar_terminos.py --llaves-stdin

    ... | python backend/scripts/competencia_mejorar_terminos.py --llaves-stdin --limite 20
    ... | python backend/scripts/competencia_mejorar_terminos.py --llaves-stdin \\
              --sku ABC-0001-NEG --incluir-manuales --sin-enfriamiento
    ... | python backend/scripts/competencia_mejorar_terminos.py --llaves-stdin \\
              --aplicar --acepto-destino yvootpbz --limite 20 --tope 0.5 --max-paginas 40

Sale con 0 si terminó (haya o no sugerencias); con 1 si la corrida falló, el
proveedor no contestó ninguna propuesta, el tope de IA se agotó a media corrida
o a la cuenta de Apify no le queda margen; y con 2 si un candado o una guardia
no la dejó empezar.
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
# `competencia_juez.py` (el script): si se toca uno, se toca el otro.
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

TOPE_USD = 0.5          # IA de toda la corrida
MAX_PAGINAS = 40        # búsquedas NUEVAS en Apify: dos tandas de 20
MAX_DIAS = 45           # una medición más vieja toca re-medirla, no cambiarla

# Las cuatro son ESTIMACIONES de trabajo (ver el encabezado), no mediciones.
USD_IA_POR_GRUPO = 0.001
USD_POR_MIL_RIVALES = 0.08
USD_APIFY_POR_PAGINA = 0.035
RIVALES_POR_BUSQUEDA = 10       # los que guarda `medir_busquedas` por término
MODELO_DE_LA_ESTIMACION = "deepseek-flash"

RESERVA_APIFY = 10.0    # la misma de `competencia_barrido.py`


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


def _cargar_backend(dsn: str) -> tuple[Any, Any, Any, Any, Any]:
    """Importa `config` y los servicios YA con el entorno forzado, comprueba que
    quedó como se pidió y anula la renovación de tokens de ML.
    → (settings, ia_json, competencia_juez, competencia_mejora, competencia_scraper)"""
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

    from services import (  # noqa: E402
        competencia_juez, competencia_mejora, competencia_ml, competencia_scraper, ia_json,
    )

    # Sin token, CADA consulta a ML deja un «Sin token de ML para la cuenta…» en
    # WARNING: cientos de líneas iguales por corrida que tapan lo que importa.
    # Aquí es lo esperado (candado 3) y se dice una vez, en el plan.
    logging.getLogger(competencia_ml.log.name).setLevel(logging.ERROR)

    return settings, ia_json, competencia_juez, competencia_mejora, competencia_scraper


def _corto(exc: BaseException) -> str:
    """Un error en una línea. Los de psycopg2 no llevan la contraseña."""
    texto = (str(exc).strip().splitlines() or [""])[0]
    return f"{type(exc).__name__}: {texto[:160]}"


def _llave_del_proveedor(settings: Any, ia_json: Any, modelo: str) -> tuple[str, bool]:
    """(proveedor, ¿está su llave?). Nunca devuelve la llave."""
    proveedor = ia_json.proveedor_de(modelo) or ""
    llave = settings.deepseek_api_key if proveedor == "deepseek" else settings.anthropic_api_key
    return proveedor, bool(llave)


# ── La cola, la estimación y el crédito ──────────────────────────────────────

def ordenar(cm: Any, grupos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Orden FIJO de la cola (ver LA COLA): más SKUs primero, luego menos
    comparables, y el término como desempate."""
    return sorted(grupos, key=lambda g: (
        -len(g["skus"]),
        min(s["resumen"]["comparables"] for s in g["skus"]),
        cm.llave(g["termino"]), g["termino_id"]))


def factor_de_precio(ia_json: Any, modelo: str) -> float:
    """Cuánto más caro es `modelo` que el de la estimación, por precio de entrada."""
    base = (ia_json.MODELOS.get(MODELO_DE_LA_ESTIMACION) or {}).get("entrada")
    return float(ia_json.MODELOS[modelo]["entrada"]) / float(base) if base else 1.0


def estimar(cm: Any, ia_json: Any, modelo: str, plan: list[dict[str, Any]],
            max_paginas: int) -> dict[str, float]:
    """El techo de lo que puede costar el plan. Todo es «hasta»: un grupo puede
    salir con el término bien, sin candidatos o con uno ya medido que se reusa.

    → {paginas, usd_apify, usd_propuestas, parejas, usd_juez, usd_ia}"""
    factor = factor_de_precio(ia_json, modelo)
    candidatos = len(plan) * cm.MAX_POR_GRUPO
    paginas = min(candidatos, max_paginas)
    # Cada candidato se juzga contra CADA SKU de su grupo.
    parejas = sum(len(g["skus"]) for g in plan) * cm.MAX_POR_GRUPO * RIVALES_POR_BUSQUEDA
    propuestas = len(plan) * USD_IA_POR_GRUPO * factor
    juez = parejas / 1000 * USD_POR_MIL_RIVALES * factor
    return {"paginas": paginas, "usd_apify": paginas * USD_APIFY_POR_PAGINA,
            "usd_propuestas": propuestas, "parejas": parejas, "usd_juez": juez,
            "usd_ia": propuestas + juez}


def uso_apify(llave: str) -> tuple[float, float] | None:
    """(usado, tope) en USD del ciclo de la CUENTA de Apify; `None` si no se pudo
    leer. La llave va en la cabecera, no en la URL."""
    import httpx

    try:
        r = httpx.get("https://api.apify.com/v2/users/me/limits",
                      headers={"Authorization": f"Bearer {llave}"}, timeout=20)
        if r.status_code != 200:
            return None
        d = r.json().get("data") or {}
        tope = (d.get("limits") or {}).get("maxMonthlyUsageUsd")
        usado = (d.get("current") or {}).get("monthlyUsageUsd")
        if tope is None or usado is None:
            return None
        return float(usado), float(tope)
    except Exception:                                               # noqa: BLE001
        return None


def _imprimir_cola(e: dict[str, Any], grupos: list[dict[str, Any]], plan: list[dict[str, Any]],
                   pedidos: int) -> None:
    fuera = e.get("fuera") or {}
    n_skus = sum(len(g["skus"]) for g in grupos)
    print(f"  elegibles          : {len(grupos):,} grupos (términos) · {n_skus:,} SKUs")
    if pedidos and pedidos != n_skus + sum(fuera.values()):
        print(f"  pedidos con --sku  : {pedidos} — de ellos {n_skus + sum(fuera.values())} "
              "están vigilados en Competencia; el resto no tiene término asignado")
    if len(plan) < len(grupos):
        print(f"  en esta corrida    : {len(plan):,} grupos · "
              f"{sum(len(g['skus']) for g in plan):,} SKUs (por --limite)")
    print(f"  fuera              : {sum(fuera.values()):,} SKUs")
    for motivo, n in sorted(fuera.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {n:>6,}  {motivo}")
    if plan:
        print("\n  los primeros de la cola:")
    for g in plan[:8]:
        comp = [s["resumen"]["comparables"] for s in g["skus"]]
        rango = str(min(comp)) if min(comp) == max(comp) else f"{min(comp)}-{max(comp)}"
        print(f"    «{str(g['termino'])[:44]}»  {len(g['skus'])} SKUs · {rango} comparables")


def _imprimir_costo(est: dict[str, float], plan: list[dict[str, Any]], args: Any, cm: Any,
                    modelo: str, factor: float) -> None:
    escala = ("" if modelo == MODELO_DE_LA_ESTIMACION
              else f"; ×{factor:.1f} por el precio de entrada de {modelo}")
    print("\n  costo ESTIMADO (son techos: «hasta»)")
    print(f"    IA    : ~${est['usd_ia']:.3f}  = propuestas ~${est['usd_propuestas']:.3f} "
          f"(${USD_IA_POR_GRUPO:.3f} por grupo) + juez ~${est['usd_juez']:.3f} "
          f"({int(est['parejas']):,} parejas){escala}")
    print(f"    Apify : ~${est['usd_apify']:.2f}  = hasta {int(est['paginas'])} páginas "
          f"({cm.MAX_POR_GRUPO} por grupo, tope --max-paginas {args.max_paginas}) a "
          f"~${USD_APIFY_POR_PAGINA:.3f} reales cada una")
    print("            (nuestra bitácora subcuenta ~2.3x: ese número ya va corregido)")
    alcanza = "alcanza" if args.tope >= est["usd_ia"] else "NO ALCANZA"
    print(f"    tope de IA ${args.tope:.3f} → {alcanza} para proponer y juzgar este plan")
    caben = args.max_paginas // cm.MAX_POR_GRUPO
    if len(plan) > caben:
        # Llenas las páginas, `mejorar` deja de proponer: los grupos que sigan en
        # la cola quedan «sin cubrir» (sin pagar nada) hasta la próxima corrida.
        print(f"    OJO: las páginas alcanzan para ~{caben} grupos; los otros "
              f"{len(plan) - caben} quedarían sin cubrir en esta corrida. Conviene "
              f"--limite {caben}.")


def informe_final(cm: Any, out: dict[str, Any], *, gastado: float, tope: float,
                  agotado: bool) -> int:
    """El cierre del resultado: el gasto de IA, lo que no alcanzó, las
    sugerencias y el código de salida. No pide nada afuera. → 0 o 1"""
    # `usd_ia` = propuestas + juez de candidatos. En ops.process_log van
    # separadas: la fila 'terminos' lleva solo las propuestas y el juez la suya.
    print(f"  IA                : ${gastado:.4f} de ${tope:.3f}  "
          f"(propuestas ${out['usd_propuestas']:.4f} + juez de candidatos "
          f"${out['usd_ia'] - out['usd_propuestas']:.4f}; a precio de lista)")
    if out["sin_cubrir"]:
        print(f"  SIN CUBRIR        : {out['sin_cubrir']:,} SKUs a los que no les alcanzó "
              "el tope o las páginas")
    if out["detenido"]:
        print(f"  SE DETUVO         : {out['detenido']}")
    if agotado:
        print("\n  OJO: el tope de IA se AGOTÓ. Lo que ya no se pudo juzgar quedó en «errores» "
              "(reintentable),\n  y lo que ya no se alcanzó a medir, sin pagar y «sin "
              "cubrir». Nada de eso quedó\n  como «sin mejora»: se retoma en la siguiente "
              "corrida.")
    elif out["errores"]:
        print("\n  Nota: «errores» son intentos sin respuesta (la medición no volvió o el "
              f"juicio quedó a\n  medias). No cuentan como probados: se reintentan pasados "
              f"{cm.REINTENTO_DIAS} días, y en la\n  ronda {cm.RONDAS_MAX} un fallo del "
              "propio candidato ya se da por respuesta.")

    print(f"\n── Sugerencias ({len(out['detalle'])}) ──")
    for d in sorted(out["detalle"], key=lambda x: str(x["sku"])):
        print(f"  {str(d['sku']):<22} «{d['termino']}»   {d['antes']} → {d['despues']} "
              "comparables")
    if not out["detalle"]:
        print("  (ninguna)")
    print("\nNADA se aplicó: ningún SKU cambió de término. Aceptar una sugerencia es un "
          "acto de un\nadmin en la pantalla de Competencia (mueve el precio de mercado que "
          "ven los KAM en\nPublicaciones), y antes conviene mirar sus rivales.")

    # Cualquier rastro de una propuesta CONTESTADA. Un cero o un muro recientes
    # del catálogo se cierran sin pagar página, y un candidato que ya tenía otro
    # proceso no se mide: también son respuestas del modelo.
    hizo_algo = (out["paginas"] or out["reusados"] or out["termino_ok"]
                 or out["sin_candidato"] or out["en_espera"] or out["sugerencias"]
                 or out["sin_mejora"] or out["bloqueados"] or out["ocupados"])
    if out["errores"] and not hizo_algo:
        print("\nTerminó CON PROBLEMAS: el proveedor no contestó ninguna propuesta.")
        return 1
    if agotado:
        print("\nTerminó CON PROBLEMAS: el tope de IA se agotó a media corrida (ver el OJO).")
        return 1
    print("\nListo.")
    return 0


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
                    help=f"Tope de gasto de IA de la corrida, en USD (default {TOPE_USD}). "
                         "No acota Apify: eso es --max-paginas.")
    ap.add_argument("--max-paginas", type=int, default=MAX_PAGINAS,
                    help=f"Búsquedas NUEVAS en Apify a lo más (default {MAX_PAGINAS}).")
    ap.add_argument("--limite", type=int, default=0,
                    help="Cuántos grupos (términos) a lo más (0 = todos los elegibles).")
    ap.add_argument("--sku", action="append", default=[],
                    help="Solo este SKU. Se puede repetir.")
    ap.add_argument("--incluir-inactivos", action="store_true",
                    help="También SKUs sin publicación activa en Mercado Libre.")
    ap.add_argument("--incluir-manuales", action="store_true",
                    help="También SKUs cuyo término corrigió una persona.")
    ap.add_argument("--max-dias", type=int, default=MAX_DIAS,
                    help=f"Antigüedad máxima de la medición, en días (default {MAX_DIAS}; "
                         "0 = sin límite).")
    ap.add_argument("--sin-enfriamiento", action="store_true",
                    help="No respetar el descanso de un SKU tras su último intento.")
    ap.add_argument("--modelo", default="",
                    help="Una llave de ia_json.MODELOS (default: competencia_juez_modelo).")
    ap.add_argument("--llaves-stdin", action="store_true",
                    help="Leer las llaves de IA y Apify por stdin (CLAVE=valor). Única vía.")
    ap.add_argument("--aplicar", action="store_true",
                    help="Propone, mide y juzga, y deja SUGERENCIAS. Sin esto, solo el plan.")
    ap.add_argument("--acepto-destino", default="",
                    help=f"Con --aplicar: la ref del sandbox, {REF_SANDBOX}.")
    args = ap.parse_args()
    if args.tope <= 0 or args.max_paginas < 1:
        ap.error("--tope tiene que ser mayor que 0 y --max-paginas al menos 1.")
    if args.limite < 0 or args.max_dias < 0:
        ap.error("--limite y --max-dias no pueden ser negativos.")

    print("═══ Competencia · mejora de términos por lotes (SANDBOX) ═══")

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
    settings, ia_json, cj, cm, scraper = _cargar_backend(dsn)

    # ── 3. Las guardias que valen también en seco ────────────────────────────
    modelo = (args.modelo or settings.competencia_juez_modelo or "").strip()
    if modelo not in ia_json.MODELOS:
        _abortar(f"el modelo {modelo!r} no tiene precio en ia_json.MODELOS y no se puede "
                 f"aplicar el tope. Valen: {', '.join(sorted(ia_json.MODELOS))}.")
    proveedor, hay_llave = _llave_del_proveedor(settings, ia_json, modelo)
    if not hay_llave:
        nombre = "DEEPSEEK_API_KEY" if proveedor == "deepseek" else "ANTHROPIC_API_KEY"
        _abortar(f"falta {nombre}, la llave del proveedor de {modelo}. Llega solo por "
                 "stdin con --llaves-stdin (ver USO en el encabezado).")
    if not scraper.disponible():
        _abortar("falta APIFY_API_KEY. Sin Apify el raspado vuelve vacío SIN error y cada "
                 "candidato quedaría anotado como probado sin haberse medido nunca.")
    print(f"  modelo             : {modelo} ({proveedor}) · llave presente · Apify presente")

    if not cj.tablas_listas():
        _abortar("el sandbox no tiene las tablas del juez (o no se pudo conectar). Aplica "
                 "primero supabase/migrations/0063_enrich_market_rival_juicio.sql.")

    # ── 4. La cola y el plan ─────────────────────────────────────────────────
    pedidos = list(dict.fromkeys(s.strip() for s in args.sku if s.strip()))
    try:
        e = cm.elegibles(pedidos or None, solo_activos=not args.incluir_inactivos,
                         incluir_manuales=args.incluir_manuales,
                         max_dias=args.max_dias or None,
                         respetar_enfriamiento=not args.sin_enfriamiento)
    except Exception as exc:                                        # noqa: BLE001
        print(f"ERROR: no se pudo leer del sandbox quién es elegible ({_corto(exc)}).",
              file=sys.stderr)
        return 1
    grupos = ordenar(cm, e["grupos"])
    plan = grupos[:args.limite] if args.limite else grupos
    _imprimir_cola(e, grupos, plan, len(pedidos))
    if (e.get("fuera") or {}).get("rivales sin juzgar: primero el juez"):
        print("\n  Hay SKUs con rivales sin juzgar: no entran hasta que corra "
              "backend/scripts/competencia_juez.py.")
    if not plan:
        print("\nNada que mejorar: ningún SKU elegible con esos filtros.")
        return 0

    est = estimar(cm, ia_json, modelo, plan, args.max_paginas)
    _imprimir_costo(est, plan, args, cm, modelo, factor_de_precio(ia_json, modelo))
    print("\n  Mercado Libre: sin token en el sandbox (a propósito). Lo medido no trae "
          "reseñas ni visitas,\n  y una publicación NUESTRA listada como MLMU puede contar "
          "como rival del candidato.")

    if not args.aplicar:
        print("\nEn seco: no se llamó a la IA ni a Apify y no se escribió nada. Para "
              f"correrlo: --aplicar --acepto-destino {REF_SANDBOX}.")
        return 0

    # ── 5. Las dos guardias que solo importan al gastar ──────────────────────
    if args.tope < est["usd_ia"]:
        _abortar(f"el tope de IA (${args.tope:.3f}) no cubre la estimación de este plan "
                 f"(~${est['usd_ia']:.3f}). Si se agota a media corrida, los candidatos ya "
                 "pagados en Apify se quedan sin juzgar y hay que repetirlos. Sube --tope o "
                 "baja --limite.")
    antes = uso_apify(settings.apify_api_key)
    if antes is None:
        print("\n  crédito de Apify   : no se pudo leer (se sigue, como en el barrido)")
    else:
        queda = antes[1] - antes[0]
        if queda < RESERVA_APIFY:
            print(f"\nABORTA: a la cuenta de Apify le quedan ${queda:.2f} este ciclo, menos "
                  f"de la reserva de ${RESERVA_APIFY:.0f}. El tope es COMPARTIDO con el "
                  "raspado de Alibaba. No se empieza algo que no puede terminar.")
            return 1
        print(f"\n  crédito de Apify   : ${queda:.2f} disponibles en el ciclo")

    # ── 6. Proponer, medir, juzgar ───────────────────────────────────────────
    presupuesto = cj.Presupuesto(args.tope)
    t0 = time.time()
    try:
        out = cm.mejorar(plan, presupuesto=presupuesto, max_paginas=args.max_paginas,
                         modelo=modelo)
    except Exception as exc:                                        # noqa: BLE001
        # Lo reclamado queda `propuesto` y se puede volver a reclamar en 30 min;
        # lo ya medido en Apify está guardado en el catálogo y se reusa.
        print(f"\nERROR: la corrida se cayó a la mitad ({_corto(exc)}). Gastado en IA "
              f"hasta ahí: ${presupuesto.gastado:.4f}. Revisa ops.process_log y los "
              "intentos en estado «propuesto».", file=sys.stderr)
        return 1

    # ── 7. Lo que se hizo y lo que NO ────────────────────────────────────────
    print(f"\n── Resultado ({(time.time() - t0) / 60:.1f} min) ──")
    print(f"  grupos / SKUs     : {out['grupos']:,} / {out['skus']:,}")
    print(f"  sugerencias       : {out['sugerencias']:,} SKUs con un término mejor MEDIDO")
    print(f"  sin mejora        : {out['sin_mejora']:,} intentos (SKU × candidato)")
    print(f"  término correcto  : {out['termino_ok']:,} SKUs — el problema no es el término")
    print(f"  sin candidato     : {out['sin_candidato']:,} SKUs — la IA no propuso nada nuevo")
    if out["en_espera"]:
        print(f"  en espera         : {out['en_espera']:,} SKUs — la IA solo repitió candidatos "
              "que esperan su\n                      reintento; no se anotó nada y se "
              f"vuelven a proponer a más tardar en {cm.REINTENTO_DIAS} días")
    print(f"  bloqueados        : {out['bloqueados']:,} candidatos que ML no dejó ver "
          "(reintentables hasta su 2ª ronda)")
    print(f"  errores           : {out['errores']:,} (propuesta fallida, medición que no "
          "volvió o juicio a medias; reintentables)")
    if out["ocupados"]:
        print(f"  ocupados          : {out['ocupados']:,} intentos que ya tenía otro proceso "
              f"(o una corrida que murió): se liberan en {cm.RECLAMO_MIN} min")
    print(f"  Apify             : {out['paginas']:,} páginas nuevas ≈ "
          f"${out['paginas'] * USD_APIFY_POR_PAGINA:.2f} ESTIMADO · "
          f"{out['reusados']:,} candidatos reusados sin pagar")
    despues = uso_apify(settings.apify_api_key) if antes is not None else None
    if antes is not None and despues is not None:
        print(f"                      uso del ciclo según Apify: ${antes[0]:.2f} → "
              f"${despues[0]:.2f} (sigue liquidando: el número final es mayor)")
    return informe_final(cm, out, gastado=presupuesto.gastado, tope=args.tope,
                         agotado=not presupuesto.puede())


if __name__ == "__main__":
    raise SystemExit(main())
