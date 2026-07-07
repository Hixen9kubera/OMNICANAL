#!/usr/bin/env python
"""
cli_agrupar.py — Agrupa drafts simples de Woo en productos VARIABLES (CLI).

Corre en tu terminal en paralelo mientras seguimos trabajando:

    cd /Users/je/dev/kubera/omnicanal/backend
    .venv/bin/python cli_agrupar.py                # todos los grupos
    .venv/bin/python cli_agrupar.py --limite 5     # solo 5 (prueba)
    .venv/bin/python cli_agrupar.py --pausa 2      # más lento (anti-bot)

Idempotente: salta los que ya son variables y completa (attach) los que
quedaron a medias por un corte. Si el anti-bot lo interrumpe, vuelve a correrlo.
"""
import argparse
import asyncio
import json
import logging
import sys

sys.path.insert(0, ".")
from services import variables  # noqa: E402


async def main(limite: int | None, pausa: float) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    cont = {"n": 0, "ok": 0, "var": 0, "err": 0}
    orig = variables.convertir_grupo

    async def wrap(cli, base, ids):
        r = await orig(cli, base, ids)
        cont["n"] += 1
        if r.get("ok") and not r.get("saltado"):
            cont["ok"] += 1
        cont["var"] += r.get("variaciones", 0) + r.get("variaciones_agregadas", 0)
        if not r.get("ok"):
            cont["err"] += 1
            print(f"  ERROR {base}: {r.get('motivo')}", flush=True)
        if cont["n"] % 10 == 0:
            print(f"AVANCE: {cont['n']} grupos · {cont['ok']} convertidos · "
                  f"{cont['var']} variaciones · {cont['err']} errores", flush=True)
        return r

    variables.convertir_grupo = wrap
    r = await variables.agrupar(limite=limite, pausa=pausa)
    print("\nRESUMEN FINAL:",
          json.dumps({k: v for k, v in r.items() if k != "detalle"}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=None, help="máx. de grupos (prueba)")
    ap.add_argument("--pausa", type=float, default=1.0, help="segundos entre grupos")
    a = ap.parse_args()
    asyncio.run(main(a.limite, a.pausa))
