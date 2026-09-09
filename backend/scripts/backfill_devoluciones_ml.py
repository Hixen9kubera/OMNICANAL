# -*- coding: utf-8 -*-
"""
backfill_devoluciones_ml.py — trae la historia de devoluciones de ML a kubera.

DRY-RUN POR DEFAULT. Sin `--aplicar` no escribe nada: solo dice qué escribiría.

QUÉ HACE
────────
Recorre `claims/search` mes por mes, y de cada devolución hace las tres
llamadas y el cruce contra `channel.orders`. **No reimplanta nada**: usa las
mismas funciones que el webhook (`services/devoluciones_ml.py`), así que lo que
se ve en el dry-run es exactamente lo que va a escribir el flujo vivo.

POR QUÉ MES POR MES
───────────────────
`claims/search` pagina con `offset`, pero la ventana completa (dic-2025 → hoy)
son ~771 devoluciones por cuenta y el `paging.total` se vuelve poco fiable en
rangos largos. Cortar por mes mantiene cada página chica y hace el progreso
visible: si algo se cae a la mitad, se sabe DÓNDE.

LA TRAMPA QUE ESTE SCRIPT EXISTE PARA NO PISAR
──────────────────────────────────────────────
El trigger `tg_returns_touch` sella `abierta_at` con `now()` si llega en NULL.
Un backfill que no mande la fecha real colapsaría siete meses de historia en el
día de hoy — `returns_daily` mostraría 771 devoluciones hoy y cero antes.
`services/devoluciones_ml.armar()` manda SIEMPRE `claim.date_created`, y el
dry-run imprime el rango de fechas resultante justo para poder verificarlo
ANTES de escribir.

USO
───
    python backend/scripts/backfill_devoluciones_ml.py                  # dry-run, todo
    python backend/scripts/backfill_devoluciones_ml.py --desde 2026-08-01
    python backend/scripts/backfill_devoluciones_ml.py --cuenta BEKURA
    python backend/scripts/backfill_devoluciones_ml.py --aplicar        # escribe
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import httpx  # noqa: E402

from services import devoluciones_ml as dml  # noqa: E402
from services import meli  # noqa: E402

# DÓNDE ARRANCA, Y POR QUÉ NO ES DONDE PARECE.
#
# La tentación es empezar donde empiezan las ventas. Pero lo que hace falta
# para valorar una devolución no son las ventas: es el PEDIDO en
# `channel.orders`, que es de donde salen el SKU, el precio congelado y si era
# FULL. Y esa tubería arrancó el 17-jul-2026 (medido el 9-sep: feb 1 pedido,
# may 4, jun 181, jul 7,603, ago 19,598).
#
# El barrido completo del 9-sep lo demostró: de 771 devoluciones históricas,
# **423 no tienen su pedido en kubera**. Existen en ML —las comprobé una por
# una contra `/orders/{id}`: 200, de febrero, ya canceladas— pero nunca
# entraron. Escribirlas produciría 423 filas sin SKU, valiendo $0, y con
# `es_fulfillment = false` por ser la columna NOT NULL: o sea AFIRMANDO que
# eran DROP cuando no se sabe. El desglose por tipo diría FULL=334/DROP=437 con
# 423 "DROP" que en realidad son "no sé".
#
# Decisión de Brandon (9-sep-2026): se captura desde julio. Las 368 anteriores
# no se pierden —siguen en ML y se pueden traer el día que esos pedidos
# entren—, y la pantalla ya sabe decir "de este período no hay datos" en vez de
# pintar un 0% tranquilizador.
ARRANQUE = "2026-07-01"


def _meses(desde: str, hasta: str) -> list[tuple[str, str]]:
    """Parte el rango en tramos mensuales [(desde, hasta), …]."""
    d0 = date.fromisoformat(desde)
    d1 = date.fromisoformat(hasta)
    tramos = []
    cur = d0
    while cur <= d1:
        fin_mes = (cur.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        fin = min(fin_mes, d1)
        tramos.append((cur.isoformat(), fin.isoformat()))
        cur = fin + timedelta(days=1)
    return tramos


async def recolectar(cuenta: str, desde: str, hasta: str,
                     concurrencia: int) -> tuple[list, list, list[str]]:
    """Devuelve (cabeceras, líneas, avisos) de una cuenta, sin escribir."""
    tok = await asyncio.to_thread(meli._access_token, cuenta)
    if not tok:
        print(f"  ✗ {cuenta}: sin token vigente (regla 8)")
        return [], [], []
    cab_http = {"Authorization": f"Bearer {tok}"}
    cuentas_kb = await asyncio.to_thread(dml._cuentas_kubera)
    account_id = cuentas_kb.get((dml.CANAL, cuenta))

    cabeceras, lineas, avisos = [], [], []
    sem = asyncio.Semaphore(concurrencia)

    async with httpx.AsyncClient(follow_redirects=True, timeout=40) as cli:
        for m0, m1 in _meses(desde, hasta):
            claims = await dml._buscar(cli, cab_http, m0, m1)
            if not claims:
                print(f"    {m0[:7]}  —")
                continue

            async def _uno(cid: str):
                async with sem:
                    return await dml._traer(cli, cab_http, cid)

            crudos = await asyncio.gather(*(_uno(str(c["id"])) for c in claims),
                                          return_exceptions=True)
            # Un claim que no se pudo TRAER no es un claim que no existe: hay
            # que decirlo y volver por él, no dejarlo caer en silencio.
            caidos = [c for c in crudos if isinstance(c, BaseException)]
            buenos = [c for c in crudos
                      if isinstance(c, dict) and (c["claim"].get("type") == "returns")]
            # Un solo viaje a la base por tramo, no uno por devolución.
            oids = sorted({str(c["claim"].get("resource_id")) for c in buenos})
            pedidos = await asyncio.to_thread(dml._lineas_de_pedidos, oids)

            for c in buenos:
                oid = str(c["claim"].get("resource_id"))
                cb, ln, av = dml.armar(c, cuenta,
                                       lineas_pedido=pedidos.get(oid, []),
                                       account_id=account_id,
                                       detectado_via="backfill")
                cabeceras.append(cb)
                lineas.extend(ln)
                avisos.extend(av)
            if caidos:
                avisos.append(f"{m0[:7]}: {len(caidos)} claims no se pudieron traer "
                              f"({type(caidos[0]).__name__}: {str(caidos[0])[:70]})")
            print(f"    {m0[:7]}  {len(claims):>4} claims · {len(buenos):>4} devoluciones"
                  + (f" · ⚠ {len(caidos)} NO SE PUDIERON TRAER" if caidos else ""))
    return cabeceras, lineas, avisos


def resumir(cabeceras: list[dict], lineas: list[dict], avisos: list[str]) -> None:
    print("\n" + "═" * 72)
    print(f"  DEVOLUCIONES A ESCRIBIR: {len(cabeceras)}   ·   LÍNEAS: {len(lineas)}")
    print("═" * 72)
    if not cabeceras:
        return

    por_ln = {}
    for l in lineas:
        por_ln.setdefault(l["external_return_id"], []).append(l)

    fechas = sorted(c["abierta_at"][:10] for c in cabeceras if c.get("abierta_at"))
    print(f"  rango de fechas   : {fechas[0]} → {fechas[-1]}"
          f"   ({len(set(fechas))} días distintos)")
    if len(set(fechas)) <= 1 and len(cabeceras) > 5:
        print("  ⚠️  TODAS EN UN SOLO DÍA — abierta_at no se está mandando. NO APLICAR.")

    print(f"  por cuenta        : {dict(Counter(c['cuenta'] for c in cabeceras))}")
    print(f"  estado            : {dict(Counter(c['estado'] for c in cabeceras))}")
    print(f"  estado del dinero : {dict(Counter(c['estado_dinero'] for c in cabeceras))}")
    print(f"  FULL / DROP       : FULL={sum(1 for c in cabeceras if c['es_fulfillment'])}"
          f"  DROP={sum(1 for c in cabeceras if not c['es_fulfillment'])}")
    print(f"  venta_contaba     : {dict(Counter(c['venta_contaba'] for c in cabeceras))}")
    print(f"  con motivo legible: {sum(1 for c in cabeceras if c['motivo_texto'])}/{len(cabeceras)}")
    print(f"  multi-línea       : {sum(1 for v in por_ln.values() if len(v) > 1)}")

    con_sku = [l for l in lineas if l["sku"]]
    print(f"  líneas con SKU    : {len(con_sku)}/{len(lineas)}")

    valor = sum(float(l["monto_unitario"]) * l["cantidad"]
                for l in lineas if l["monto_unitario"])
    contaba = {c["external_return_id"] for c in cabeceras if c["venta_contaba"]}
    restable = sum(float(l["monto_unitario"]) * l["cantidad"] for l in lineas
                   if l["monto_unitario"] and l["external_return_id"] in contaba)
    piezas = sum(l["cantidad"] for l in lineas)
    print(f"\n  PIEZAS devueltas  : {piezas:,}")
    print(f"  VALOR devuelto    : ${valor:,.2f}")
    print(f"    restable de ventas          : ${restable:,.2f}")
    print(f"    ya descontado (cancelados)  : ${valor - restable:,.2f}")

    if avisos:
        print(f"\n  avisos ({len(avisos)}) — los 12 primeros:")
        for a in avisos[:12]:
            print(f"    · {a}")
        tipos = Counter(a.split("→")[-1].strip()[:45] for a in avisos)
        if len(avisos) > 12:
            print(f"    … y {len(avisos)-12} más. Por tipo: {dict(tipos)}")


async def main() -> int:
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default=ARRANQUE)
    ap.add_argument("--hasta", default=hoy)
    ap.add_argument("--cuenta", choices=list(dml.CUENTAS))
    ap.add_argument("--concurrencia", type=int, default=3,
                    help="ML corta con 429 arriba de 3-4 en paralelo")
    ap.add_argument("--aplicar", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()

    cuentas = [args.cuenta] if args.cuenta else list(dml.CUENTAS)
    print(f"Ventana: {args.desde} → {args.hasta}   cuentas: {', '.join(cuentas)}")

    cabeceras, lineas, avisos = [], [], []
    for cuenta in cuentas:
        print(f"\n■ {cuenta}")
        cb, ln, av = await recolectar(cuenta, args.desde, args.hasta, args.concurrencia)
        cabeceras += cb
        lineas += ln
        avisos += av

    resumir(cabeceras, lineas, avisos)

    if not args.aplicar:
        print("\nDRY-RUN: no se escribió nada. Repite con --aplicar.")
        return 0

    print("\nEscribiendo…")
    por_ln: dict[str, list] = {}
    for l in lineas:
        por_ln.setdefault(l["external_return_id"], []).append(l)
    escritas = 0
    for cb in cabeceras:
        await asyncio.to_thread(dml._guardar, cb, por_ln.get(cb["external_return_id"], []))
        escritas += 1
        if escritas % 50 == 0:
            print(f"   {escritas}/{len(cabeceras)}")
    print(f"\nOK — {escritas} devoluciones escritas en channel.returns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
