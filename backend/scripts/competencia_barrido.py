"""
competencia_barrido.py — El único proceso que GASTA. Mensual, con tope en dólares.

Raspa los más vendidos de las categorías que lo ameritan, en orden de dinero, y
**se detiene cuando llega al tope**. No se detiene por número de categorías: se
detiene por gasto, que es lo que de verdad importa.

POR QUÉ EL TOPE VA EN DÓLARES Y NO EN CATEGORÍAS
------------------------------------------------
Apify **no cobra por página: cobra por tiempo de cómputo**, más el proxy
residencial aparte. Medido el 1-sep-2026 sobre la facturación real: $36.07 en 176
corridas, y una corrida de 235 s costó $0.0993. Una categoría puede costar el
doble que otra según lo que tarde en cargar.

Un tope de "N categorías" no acota el gasto. Uno de "$9" sí, y además sobrevive a
que el catálogo crezca: si mañana hay 2,000 categorías, este barrido no gasta el
doble — gasta lo mismo y cubre menos, empezando por lo que más vende.

LA COLA
-------
De `enrich.market_categoria_prioridad_v`, ordenada por pesos de 30 días:

  · vende algo                    (si no vende, la competencia ahí no urge)
  · `tiene_ranking_ml`            (si ML no publica lista, raspar no trae nada;
                                   son 208 de 1,129 categorías, medido)
  · sin captura o con más de N días

DOS GUARDIAS ANTES DE GASTAR
----------------------------
1. **El crédito de la cuenta.** El tope de Apify es de la CUENTA y se comparte
   con el scraper de Alibaba que dispara `crear_producto`. Si queda poco, este
   barrido no corre: prefiere no empezar a morirse a la mitad. Sin este chequeo,
   `_correr_actor` devuelve `[]` ante cualquier fallo y el diagnóstico dice
   "bloqueo intermitente, vale reintentar" — el consejo contrario al correcto
   cuando lo que pasó es que se acabó el crédito.

2. **El gasto de ESTA corrida**, releído entre tanda y tanda **de la API de
   Apify**, no de nuestra bitácora.

   Ese cambio importa: `ops.process_log` **subcuenta 2.3x**. Lee `usageTotalUsd`
   cuando la corrida termina, y Apify sigue liquidando el proxy residencial y la
   transferencia después. Medido el 2-sep-2026 cruzando por `run_id` las 25
   corridas del día anterior: nuestra bitácora decía **$1.3966**, Apify había
   cobrado **$3.2140**. Con el tope en $9 eso habría dejado gastar ~$20 creyendo
   que iba en $9.

LO QUE NO ALCANZÓ SE DICE
-------------------------
Si el tope se agota con la mitad de la cola, la otra mitad se REPORTA con su
dinero. Un barrido que se calla lo que dejó fuera se lee como "ya cubrimos todo".

USO
---
    python scripts/competencia_barrido.py                # DRY-RUN: la cola y el plan
    python scripts/competencia_barrido.py --real         # raspa, tope $9
    python scripts/competencia_barrido.py --real --tope 3
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from config import settings  # noqa: E402
from services import competencia_captura, competencia_scraper, supabase_db  # noqa: E402

# TOPE POR CORRIDA, no por mes — y desde que el barrido es QUINCENAL (2-sep-2026,
# Eduardo) la diferencia importa: dos corridas al mes ponen el techo del ciclo en
# $18, no en $9. El gasto REAL de una corrida es otra cosa: el barrido del
# 1-sep-2026 cubrió 482 categorías y Apify cobró $3.21, así que el tope no se
# alcanza y sirve de freno de emergencia, no de presupuesto.
TOPE_USD = 9.0

# Debajo de esto la captura todavía sirve.
#
# ⚠️ NO SUBIR DE 12 MIENTRAS EL CRON SEA QUINCENAL (días 1 y 16). El hueco más
# CORTO entre corridas no son 15 días sino 13 — del 16 de febrero al 1 de marzo—,
# así que con el 14 de antes la corrida del 1 de marzo no habría encontrado NADA
# vencido y se habría ido en blanco sin fallar. Un barrido que no raspa nada y
# termina en verde es peor que uno que truena.
DIAS_VIEJO = 12
TANDA = 20            # igual que _TANDA_RANKING: una corrida de Apify
RESERVA_CUENTA = 10.0 # si a la cuenta le quedan menos de esto, no se empieza


def cola(dias: int = DIAS_VIEJO) -> list[dict[str, Any]]:
    """Lo que amerita rasparse, lo más caro primero."""
    return supabase_db.fetch_all(
        """select categoria_id, categoria_nombre, pesos_30d, unidades_30d,
                  dias_sin_captura
             from enrich.market_categoria_prioridad_v
            where unidades_30d > 0
              and tiene_ranking_ml
              and (dias_sin_captura is null or dias_sin_captura > %s)
            order by pesos_30d desc nulls last""", (dias,))


def raices(dias: int = DIAS_VIEJO) -> list[dict[str, Any]]:
    """
    Las categorías RAÍZ que ameritan refrescarse.

    ── POR QUÉ VAN APARTE DE `cola()` ──────────────────────────────────────────
    `enrich.market_categoria_prioridad_v` es **por SUBCATEGORÍA**: nunca devuelve
    una raíz. Como este barrido arma su lote desde ahí y se la pasa a
    `capturar_rankings_categorias(solo=…)`, **jamás pidió una raíz** — y la raíz
    es justo lo PRIMERO que se ve al abrir el tab ("MÁS VENDIDOS DE HOGAR,
    MUEBLES Y JARDÍN").

    Medido el 1-sep-2026: las 24 raíces estaban capturadas entre el 5 y el 18 de
    agosto, hasta 27 días de atraso, mientras 287 hojas se habían refrescado ese
    mismo día. No era una decisión de costo: era un hueco. Refrescarlas TODAS
    cuesta **$0.08** al costo medido.

    El nivel se resuelve solo: `capturar_rankings_categorias` ya sabe cuáles ids
    son raíz —los deriva de los SKUs vigilados— así que basta con nombrarlas.

    Sin `pesos_30d`: una raíz agrega a todas sus hojas y sumarlo aquí duplicaría
    el dinero que ya cuenta la cola. Van primero por ser pocas y baratas, no por
    dinero.
    """
    return supabase_db.fetch_all(
        """with r as (
             select distinct raiz_id from enrich.market_skus_v where raiz_id is not null
           )
           select r.raiz_id                                   as categoria_id,
                  coalesce(max(c.name), r.raiz_id)            as categoria_nombre,
                  0::numeric                                  as pesos_30d,
                  0                                           as unidades_30d,
                  (current_date - max(b.capturado_en)::date)  as dias_sin_captura
             from r
             left join channel.categories c
                    on c.category_id = r.raiz_id and c.channel_id = 'mercado_libre'
             left join enrich.market_bestsellers b
                    on b.categoria_id = r.raiz_id and b.canal = 'mercado_libre'
                   and b.nivel = 'raiz'
            group by r.raiz_id
           having max(b.capturado_en) is null
               or (current_date - max(b.capturado_en)::date) > %s
            order by 5 desc nulls first""", (dias,))


def credito_disponible() -> float | None:
    """Cuánto le queda a la CUENTA este ciclo. None si no se pudo saber."""
    try:
        r = requests.get("https://api.apify.com/v2/users/me/limits",
                         headers={"Authorization": f"Bearer {settings.apify_api_key}"},
                         timeout=20)
        if r.status_code != 200:
            return None
        d = r.json().get("data", {})
        tope = (d.get("limits") or {}).get("maxMonthlyUsageUsd")
        usado = (d.get("current") or {}).get("monthlyUsageUsd")
        if tope is None or usado is None:
            return None
        return float(tope) - float(usado)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("no se pudo leer el crédito: %s", exc)
        return None


def gasto_del_ciclo() -> float | None:
    """
    Lo que Apify lleva cobrado EN ESTE CICLO. None si no se pudo leer.

    ── POR QUÉ SE LE PREGUNTA A APIFY Y NO A NUESTRA BITÁCORA ─────────────────
    Porque nuestra bitácora **subcuenta 2.3×**, y con un tope eso es grave.

    `_registrar_gasto` lee `usageTotalUsd` en el instante en que la corrida
    TERMINA, y Apify sigue liquidando después — el proxy residencial y la
    transferencia se contabilizan al cerrar. Cruzado el 2-sep-2026 por `run_id`
    sobre las 25 corridas del día anterior: nosotros registramos **$1.3966** y
    Apify había cobrado **$3.2140**. Corrida por corrida el factor va de 1.0x a
    5.1x, así que ni siquiera se puede corregir con una constante.

    Con el tope en $9 y subcontando así, el barrido habría gastado ~$20 creyendo
    que iba en $9. El tope tiene que medirse contra lo que Apify cobra.

    `ops.process_log` se sigue escribiendo: sirve para el desglose por proceso y
    por categoría, que la API de la cuenta no da. Sólo deja de ser la fuente del
    TOPE.
    """
    try:
        r = requests.get("https://api.apify.com/v2/users/me/usage/monthly",
                         headers={"Authorization": f"Bearer {settings.apify_api_key}"},
                         timeout=20)
        if r.status_code != 200:
            return None
        d = r.json().get("data") or {}
        v = (d.get("totalUsageCreditsUsdBeforeVolumeDiscount")
             or d.get("totalUsageCreditsUsd"))
        return float(v) if v is not None else None
    except Exception as exc:  # noqa: BLE001
        print(f"  (no pude leer el gasto del ciclo: {exc})")
        return None


def costo_real_por_categoria(dias: int = 30) -> float | None:
    """
    Lo que CUESTA de verdad raspar una categoría, según lo que Apify ya liquidó.

    ── POR QUÉ NO SIRVE `competencia_scraper.costo_medido_por_pagina()` ────────
    Porque promedia `ops.process_log`, que **subcuenta 2.3x** (ver
    `gasto_del_ciclo`). Con ese número el barrido anunciaba "$0.0029 por
    categoría, alcanza para ~3,103" cuando el costo real es ~$0.0068 y alcanzaba
    para menos de la mitad. Una estimación de capacidad al doble no es un detalle
    cosmético: es lo que alguien lee para decidir el tope.

    ── CÓMO SE MIDE ───────────────────────────────────────────────────────────
    Una sola llamada trae las últimas corridas con su `usageTotalUsd` YA
    liquidado. Se cruzan por `run_id` contra nuestra bitácora —que es la única
    que sabe cuántas URLs llevaba cada corrida— y se divide.

    Devuelve None si no se puede medir; quien llame decide qué hacer con eso, en
    vez de recibir un número inventado.
    """
    try:
        r = requests.get("https://api.apify.com/v2/actor-runs",
                         params={"limit": 1000, "desc": "true"},
                         headers={"Authorization": f"Bearer {settings.apify_api_key}"},
                         timeout=30)
        if r.status_code != 200:
            return None
        liquidado = {x.get("id"): float(x.get("usageTotalUsd") or 0)
                     for x in ((r.json().get("data") or {}).get("items") or [])}
    except Exception:  # noqa: BLE001
        return None
    if not liquidado:
        return None

    filas = supabase_db.fetch_all(
        "select detalle->>'run_id' as run_id, (detalle->>'urls')::int as urls "
        "  from ops.process_log "
        " where proceso = 'competencia' and accion = 'raspado' "
        "   and created_at > now() - make_interval(days => %s) "
        "   and detalle->>'run_id' is not null", (dias,))
    usd = cats = 0.0
    for f in filas:
        u = liquidado.get(f["run_id"])
        if u and f.get("urls"):
            usd += u
            cats += f["urls"]
    return (usd / cats) if cats else None


def gastado_local(inicio: str) -> float:
    """
    El respaldo: lo que NUESTRA bitácora registró desde que empezó la corrida.

    ⚠️ SUBCUENTA ~2.3x (ver `gasto_del_ciclo`). Sólo se usa cuando Apify no
    contesta, y entonces el tope se aplica sobre un tercio de tope para
    compensar — más vale cubrir de menos que gastar de más sin saberlo.
    """
    v = supabase_db.fetch_scalar(
        "select coalesce(sum((detalle->>'usd')::numeric), 0) "
        "  from ops.process_log "
        " where proceso = 'competencia' and accion = 'raspado' "
        "   and created_at >= %s", (inicio,))
    return float(v or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="Raspa. Sin esto, sólo el plan.")
    ap.add_argument("--tope", type=float, default=TOPE_USD, help="Tope en USD.")
    ap.add_argument("--dias", type=int, default=DIAS_VIEJO)
    args = ap.parse_args()

    print("═══ Competencia · barrido quincenal ═══")
    if not supabase_db.disponible():
        print("ERROR: sin SUPABASE_DB_URL.")
        return 2
    if not competencia_scraper.disponible():
        print("ERROR: sin APIFY_API_KEY. El barrido es lo único que necesita a Apify.")
        return 2

    # Las RAÍCES van primero: son ~24, cuestan centavos y son lo primero que se
    # ve al abrir el tab. Iban quedando fuera porque la vista de prioridad es por
    # subcategoría — ver `raices()`.
    pendientes = raices(args.dias) + cola(args.dias)
    n_raices = sum(1 for f in pendientes if f["pesos_30d"] == 0)
    if not pendientes:
        print(f"Nada que raspar: ninguna categoría vende y lleva más de {args.dias} "
              "días sin captura. Es una buena noticia.")
        return 0

    # El costo REAL (liquidado por Apify), no el de nuestra bitácora — que
    # subcuenta 2.3x y anunciaba el doble de capacidad de la que hay.
    por_cat = costo_real_por_categoria()
    fuente = "liquidado por Apify"
    if por_cat is None:
        por_cat = competencia_scraper.costo_medido_por_pagina()
        fuente = "nuestra bitácora — SUBCUENTA ~2.3x"
    caben = int(args.tope / por_cat) if por_cat else 0
    print(f"  en la cola      : {len(pendientes)} categorías "
          f"({n_raices} raíces + {len(pendientes) - n_raices} subcategorías) · "
          f"${sum(f['pesos_30d'] or 0 for f in pendientes):,.0f} de venta detrás")
    print(f"  costo medido    : ${por_cat:.4f} por categoría  ({fuente})")
    print(f"  tope            : ${args.tope:.2f}  →  alcanza para ~{caben}")
    print(f"\n  las 8 primeras (por dinero):")
    for f in pendientes[:8]:
        d = f["dias_sin_captura"]
        print(f"    {str(f['categoria_nombre'])[:32]:<33} ${f['pesos_30d']:>9,}  "
              f"{'nunca' if d is None else str(d)+' d'}")

    if not args.real:
        print("\n--dry-run: no se raspó nada. Corre con --real.")
        return 0

    queda = credito_disponible()
    if queda is not None and queda < RESERVA_CUENTA:
        print(f"\nABORTA: a la cuenta de Apify le quedan ${queda:.2f} este ciclo, "
              f"menos de la reserva de ${RESERVA_CUENTA:.0f}. El tope es COMPARTIDO "
              "con el scraper de crear_producto. No se empieza algo que no puede "
              "terminar.")
        return 1
    print(f"\n  crédito de la cuenta: "
          f"{'${:.2f}'.format(queda) if queda is not None else 'no se pudo leer'}")

    inicio = supabase_db.fetch_scalar("select now()")
    # La línea base: lo que el ciclo llevaba ANTES de esta corrida. El gasto de
    # la corrida es la diferencia contra esto — así el tope no se confunde con lo
    # que ya se había gastado el mes.
    base = gasto_del_ciclo()
    if base is None:
        print("  ⚠ Apify no contestó el gasto del ciclo: se mide con la bitácora,")
        print("    que SUBCUENTA ~2.3x, así que el tope se aplica a un tercio.")
    else:
        print(f"  gasto del ciclo antes de empezar: ${base:.2f}")

    def gastado_ahora() -> float:
        """Lo de ESTA corrida. Contra Apify si contesta; si no, la bitácora."""
        if base is not None:
            ahora = gasto_del_ciclo()
            if ahora is not None:
                return max(0.0, ahora - base)
        # Sin Apify, el respaldo subcuenta: se compara contra un tercio del tope
        # para no pasarse sin enterarse.
        return gastado_local(inicio) * 3

    hechas, gastado = [], 0.0
    t0 = time.time()
    for i in range(0, len(pendientes), TANDA):
        gastado = gastado_ahora()
        if gastado >= args.tope:
            break
        lote = [f["categoria_id"] for f in pendientes[i:i + TANDA]]
        print(f"\n  tanda {i//TANDA + 1}: {len(lote)} categorías "
              f"(gastado ${gastado:.3f} de ${args.tope:.2f})")
        try:
            r = asyncio.run(competencia_captura.capturar_rankings_categorias(solo=lote))
            print(f"    guardadas: {r.get('con_datos')} · avisos: {len(r.get('avisos') or [])}")
            hechas.extend(lote)
        except Exception as exc:  # noqa: BLE001
            print(f"    la tanda falló: {exc}")

    gastado = gastado_ahora()
    faltaron = [f for f in pendientes if f["categoria_id"] not in set(hechas)]
    print(f"\n── Resultado ──")
    print(f"  capturadas : {len(hechas)} categorías en {(time.time()-t0)/60:.1f} min")
    print(f"  gastado    : ${gastado:.3f} de ${args.tope:.2f}")
    if faltaron:
        # Lo que no alcanzó se DICE, con su dinero. Un barrido que se calla lo
        # que dejó fuera se lee como "ya cubrimos todo".
        print(f"  SIN CUBRIR : {len(faltaron)} categorías, "
              f"${sum(f['pesos_30d'] or 0 for f in faltaron):,.0f} de venta detrás")
        for f in faltaron[:5]:
            print(f"     {str(f['categoria_nombre'])[:34]:<35} ${f['pesos_30d']:>9,}")
    print("\nListo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
