"""
vectorizar_arbol_ml.py — llena `channel.ml_category_vec` (migración 0078): la HUELLA de
cada hoja publicable del árbol de ML (`channel.ml_category_tree`, 0059) en los dos
modelos de la revisión de categoría (`services/categoria_vectores.py`).

QUÉ VECTORIZA
La ruta completa de cada hoja con `listing_allowed` («Accesorios para Vehículos > … >
Compresores»), tal cual, sin instrucción (qwen3 solo la lleva en la consulta). Se guardan
las primeras 512 dimensiones normalizadas en halfvec.

SOLO LO QUE FALTA
`texto_hash` = sha1 de la ruta: se vectoriza lo que no tiene huella o cuya ruta cambió, y
se borran las huellas de categorías que ya no son hojas publicables. Re-correrlo tras
`cargar_arbol_ml` cuesta centavos (o nada).

COSTO (medido el 26/27-oct-2026): el árbol completo ≈ US$0.003 en qwen3 y ≈ US$0.034 en
voyage. `--tope-usd` (0.06 por omisión) frena ANTES de una llamada que lo pasaría,
estimando 1 token por cada 3 caracteres; lo cobrado sale de `usage.cost` de OpenRouter.

Sin `--aplicar` es un ENSAYO: cuenta lo que vectorizaría y cuánto costaría, sin llamar a
OpenRouter ni escribir.

EN EL CRON DE LAS 06:15 (`railway.etl-core.json`, después de `cargar_arbol_ml`) corre con
`--cron`: si todavía no existe la tabla (0078 sin aplicar), el servicio no tiene
OPENROUTER_API_KEY o no se pudo leer la lista de precios, lo dice y sale en 0. Así el paso no tumba el cron, sea cual sea el orden
en que se apliquen la migración, el push y la variable. Un día normal no vectoriza nada (US$0).

Uso (desde backend/), con OPENROUTER_API_KEY en el entorno (nunca se imprime):
    python -m scripts.vectorizar_arbol_ml                           # ensayo, sandbox
    python -m scripts.vectorizar_arbol_ml --aplicar --destino sandbox
    python -m scripts.vectorizar_arbol_ml --aplicar --destino prod   # con acta

La DSN sale como en `cargar_arbol_ml` (SUPABASE_DB_URL o env.staging / .env) y se valida
contra el --destino.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from scripts.cargar_arbol_ml import _env_destino  # noqa: E402
from services.categoria_vectores import (API, DIMS, MODELO_FOTO, MODELO_TEXTO,  # noqa: E402
                                         literal, normalizar)

LOTE = 96
CHARS_POR_TOKEN = 3.0


def hash_texto(t: str) -> str:
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


def precios() -> dict[str, float]:
    """USD por token de entrada, del listado público de OpenRouter."""
    with urllib.request.urlopen(API + "/embeddings/models", timeout=30) as r:
        datos = json.loads(r.read())["data"]
    return {m["id"]: float((m.get("pricing") or {}).get("prompt") or 0) for m in datos}


def entrada(modelo: str, texto: str):
    # voyage multimodal pide objetos; qwen3, texto plano.
    return {"content": [{"type": "text", "text": texto}]} if modelo == MODELO_FOTO else texto


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--destino", choices=["sandbox", "prod"], default="sandbox")
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--tope-usd", type=float, default=0.06)
    ap.add_argument("--modelo", choices=[MODELO_TEXTO, MODELO_FOTO], action="append",
                    help="por omisión, los dos")
    ap.add_argument("--cron", action="store_true",
                    help="sin la tabla de la 0078 o sin OPENROUTER_API_KEY, avisa y sale en 0")
    args = ap.parse_args()
    modelos = args.modelo or [MODELO_TEXTO, MODELO_FOTO]
    dsn = _env_destino(args.destino)

    conn = psycopg2.connect(dsn, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("select to_regclass('channel.ml_category_vec') is not null")
            existe = cur.fetchone()[0]
        conn.rollback()
        if not existe:
            print(f"{'AVISO' if args.cron else 'ABORT'}: {args.destino} no tiene channel.ml_category_vec "
                  "(¿falta la migración 0078?).")
            return 0 if args.cron else 2
        with conn.cursor() as cur:
            cur.execute("select category_id, path_names from channel.ml_category_tree "
                        " where is_leaf and listing_allowed")
            hojas = {cid: " > ".join(p) for cid, p in cur.fetchall()}
            cur.execute("select category_id, modelo, texto_hash from channel.ml_category_vec")
            hay = {(c, m): h for c, m, h in cur.fetchall()}
        conn.rollback()
        if len(hojas) < 5_000:
            print(f"ABORT: el árbol de {args.destino} trae {len(hojas):,} hojas publicables; "
                  "¿corrió cargar_arbol_ml?")
            return 2

        plan = {}
        for m in modelos:
            faltan = [(cid, t) for cid, t in hojas.items() if hay.get((cid, m)) != hash_texto(t)]
            sobran = [c for (c, mm) in hay if mm == m and c not in hojas]
            plan[m] = (faltan, sobran)
        if args.aplicar and not any(f or s for f, s in plan.values()):
            print(f"{args.destino}: {len(hojas):,} hojas, todas al día: nada que vectorizar ni borrar (US$0).")
            return 0
        llave = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if args.aplicar and not llave:
            print(f"{'AVISO' if args.cron else 'ABORT'}: falta OPENROUTER_API_KEY en el entorno.")
            return 0 if args.cron else 2
        try:
            p = precios()
        except Exception as exc:  # noqa: BLE001
            if not args.cron:
                raise
            print(f"AVISO: no se pudo leer la lista de precios de OpenRouter ({type(exc).__name__}); "
                  "se intenta mañana.")
            return 0
        for m in modelos:
            faltan, sobran = plan[m]
            est = sum(len(t) for _, t in faltan) / CHARS_POR_TOKEN * p.get(m, 0)
            print(f"{args.destino} · {m}: {len(hojas):,} hojas · con huella al día "
                  f"{len(hojas) - len(faltan):,} · a vectorizar {len(faltan):,} (≈ US${est:.4f}) · "
                  f"a borrar {len(sobran):,}")
        if not args.aplicar:
            print("ENSAYO: no se llamó a OpenRouter ni se escribió nada (usa --aplicar).")
            return 0
        gastado = 0.0
        with httpx.Client(base_url=API, timeout=120.0,
                          headers={"Authorization": f"Bearer {llave}"}) as cli:
            for m in modelos:
                faltan, sobran = plan[m]
                if sobran:
                    with conn.cursor() as cur:
                        cur.execute("delete from channel.ml_category_vec "
                                    " where modelo = %s and category_id = any(%s)", (m, sobran))
                    conn.commit()
                for i in range(0, len(faltan), LOTE):
                    bloque = faltan[i:i + LOTE]
                    est = sum(len(t) for _, t in bloque) / CHARS_POR_TOKEN * p.get(m, 0)
                    if gastado + est > args.tope_usd:
                        print(f"TOPE: US${gastado:.5f} + estimado US${est:.5f} > US${args.tope_usd}; "
                              f"me detengo en {i:,}/{len(faltan):,} de {m}. Re-correr sigue donde quedó.")
                        return 3
                    for intento in range(4):
                        r = cli.post("/embeddings", json={"model": m,
                                                          "input": [entrada(m, t) for _, t in bloque]})
                        if r.status_code in (429, 500, 502, 503, 504) and intento < 3:
                            time.sleep(3 * (intento + 1))
                            continue
                        r.raise_for_status()
                        break
                    d = r.json()
                    gastado += float((d.get("usage") or {}).get("cost") or 0)
                    datos = sorted(d["data"], key=lambda x: x["index"])
                    filas = [(cid, m, hash_texto(t), literal(normalizar(x["embedding"], DIMS)))
                             for (cid, t), x in zip(bloque, datos)]
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(
                            cur,
                            "insert into channel.ml_category_vec (category_id, modelo, texto_hash, vec) "
                            "values %s on conflict (category_id, modelo) do update set "
                            "texto_hash = excluded.texto_hash, vec = excluded.vec, creado_at = now()",
                            filas, template="(%s, %s, %s, %s::extensions.halfvec)")
                    conn.commit()
                    if (i // LOTE) % 20 == 0:
                        print(f"  {m}: {i + len(bloque):,}/{len(faltan):,} · US${gastado:.5f}", flush=True)
        print(f"listo · gastado US${gastado:.6f}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
