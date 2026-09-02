"""
competencia_visitas.py — Refresca las VISITAS de 30 días de NUESTRAS publicaciones.

GRATIS. Una llamada a la API de Mercado Libre por publicación, sin Apify.

POR QUÉ EXISTE Y NO SE USA `refrescar_visitas_propias`
------------------------------------------------------
El endpoint `POST /api/competencia/visitas-propias` hace lo mismo pero no sirve
para un cron, por tres razones medidas el 31-ago-2026:

1. **Pide TRES llamadas por publicación**, no una: `items_por_sku` para
   descubrir los item_id, más `detalle_item` y `visitas_30d`. Son ~14,000
   llamadas. Aquí el `listing_id` ya lo sabemos —está en la fila que vamos a
   actualizar—, así que basta **una** llamada: 3,118 en total.
2. **No acota la concurrencia.** Sus `asyncio.gather` lanzan miles de
   `to_thread` de golpe contra el ThreadPoolExecutor por defecto, que es el
   MISMO que usa todo el backend para hablarle a la base. Encolar miles de
   tareas ahí es cómo se congela el panel entero. Aquí hay `Semaphore`.
3. **Es HTTP.** Una corrida de minutos muere en el timeout del proxy y el cron
   se marca fallido aunque las escrituras parciales sí hayan entrado.

ALCANCE: SOLO LAS FILAS QUE YA EXISTEN
--------------------------------------
Se refrescan las publicaciones que YA tienen fila en
`enrich.market_listing_metrics` y siguen vivas en `channel.listings`.

**No se crean filas nuevas, y es a propósito.** `market_publicaciones_v` sirve
las publicaciones sin medición desde su rama espejo, que toma el precio de
`channel.listings` directo. Si les creáramos fila de medición pasarían a la rama
medida, cuyo join es `l.store_name = mm.cuenta` — y `store_name` viene NULL en
1,052 listings. Medido: de las 1,685 del espejo, **1,040 se quedarían sin
precio**. Ampliar la cobertura exige antes cambiar ese join a `account_id`, que
es otra migración y otra decisión.

CADENCIA
--------
**Una vez al día y punto.** `/visits/time_window` devuelve una ventana MÓVIL de
30 días: correrlo dos veces el mismo día agrega un día y quita otro, o sea casi
nada. Temprano, para que el panel amanezca al día.

USO
---
    python scripts/competencia_visitas.py              # DRY-RUN: mide y no escribe
    python scripts/competencia_visitas.py --real       # escribe
    python scripts/competencia_visitas.py --real --limite 50   # una probadita

Sale con código != 0 si no logra medir al menos la mitad, para que el cron se
marque fallido en vez de fingir que todo bien.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from services import competencia_ml, competencia_store, supabase_db  # noqa: E402
from services.competencia_captura import CUENTAS  # noqa: E402  (la pareja token↔etiqueta)

# Ocho en paralelo: medido, 0.56 s por llamada, así que 3,118 salen en ~4 min.
# Subirlo tienta a que ML empiece a responder 429; bajarlo alarga el cron.
CONCURRENCIA = 8


def objetivo(limite: int | None = None) -> list[dict[str, Any]]:
    """Las publicaciones medidas que siguen vivas, con su item_id."""
    # ⚠ `distinct on (sku, cuenta)`: `market_listing_metrics` guarda UNA FILA POR
    # MES (su PK lleva `periodo`). Sin esto, desde el día 1 cada publicación sale
    # DOS veces y se mide dos veces.
    #
    # Pasó el 1-sep-2026 y tumbó el cron: pidió 5,732 filas para 2,935
    # publicaciones reales, el doble de llamadas hizo que ML empezara a limitar
    # por tasa, 4,397 volvieron vacías y la guarda de salud abortó la corrida —
    # y con ella el sondeo de /highlights, que va encadenado.
    #
    # Es el MISMO error que la 0039 arregló en la vista. Éste no estaba cubierto
    # porque el script consulta la TABLA, no la vista.
    sql = """
        select distinct on (m.sku, m.cuenta)
               m.sku::text as sku, m.canal, m.cuenta, m.periodo,
               coalesce(m.listing_id, l.listing_id) as ml_item_id,
               m.visits_30d as visitas_previas,
               m.metrics_updated_at::date as medido_el
          from enrich.market_listing_metrics m
          join core.accounts a on a.legacy_code = m.cuenta
          join channel.listings l
            on l.sku = m.sku and l.canal = m.canal and l.account_id = a.id
           and lower(l.situacion) in ('active', 'paused')
           and nullif(l.listing_id, '') is not null
         where m.canal = 'mercado_libre'
         order by m.sku, m.cuenta, m.periodo desc
    """
    if limite:
        sql += f" limit {int(limite)}"
    return supabase_db.fetch_all(sql)


async def medir(filas: list[dict[str, Any]]) -> tuple[int, int]:
    """Llena `visitas_30d` en cada fila. Devuelve (con dato, sin dato)."""
    tok_de = {etiqueta: tok for tok, etiqueta in CUENTAS}
    sem = asyncio.Semaphore(CONCURRENCIA)

    async def una(f: dict[str, Any]) -> None:
        tok = tok_de.get(f["cuenta"])
        if not tok or not f.get("ml_item_id"):
            f["visitas_30d"] = None
            return
        async with sem:
            try:
                f["visitas_30d"] = await asyncio.to_thread(
                    competencia_ml.visitas_30d, f["ml_item_id"], tok)
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "visitas de %s/%s fallaron: %s", f["sku"], f["cuenta"], exc)
                f["visitas_30d"] = None

    await asyncio.gather(*(una(f) for f in filas))
    ok = sum(1 for f in filas if f.get("visitas_30d") is not None)
    return ok, len(filas) - ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="Escribe. Sin esto, sólo mide.")
    ap.add_argument("--limite", type=int, default=0, help="Acota el número de publicaciones.")
    args = ap.parse_args()

    print("═══ Competencia · visitas de 30 días ═══")
    if not supabase_db.disponible():
        print("ERROR: sin SUPABASE_DB_URL. Las visitas se guardan en kubera y no hay a dónde.")
        return 2

    filas = objetivo(args.limite or None)
    if not filas:
        print("No hay publicaciones medidas con listing vivo. Nada que hacer.")
        return 0

    viejas = sum(1 for f in filas if f.get("visitas_previas") is not None)
    print(f"  publicaciones a medir : {len(filas)}")
    print(f"  con visitas previas   : {viejas}")
    print(f"  medidas por última vez: {min((f['medido_el'] for f in filas if f['medido_el']), default='—')}")
    print(f"  concurrencia          : {CONCURRENCIA}")

    t0 = time.time()
    ok, sin = asyncio.run(medir(filas))
    dt = time.time() - t0
    print(f"\n  medidas en {dt/60:.1f} min · {ok} con dato · {sin} sin dato")

    cambian = [f for f in filas
               if f.get("visitas_30d") is not None
               and f["visitas_30d"] != f.get("visitas_previas")]
    subieron = sum(1 for f in cambian if (f.get("visitas_previas") or 0) < f["visitas_30d"])
    print(f"  cambian {len(cambian)} filas ({subieron} suben, {len(cambian)-subieron} bajan)")
    for f in sorted(cambian, key=lambda x: -(x["visitas_30d"] or 0))[:5]:
        print(f"    {f['sku']:<22} {f['cuenta']:<14} {f.get('visitas_previas')} → {f['visitas_30d']}")

    if not args.real:
        print("\n--dry-run: no se escribió nada. Corre con --real para guardar.")
        return 0

    # Sólo las que TIENEN dato: una fila sin medición no debe tocar la tabla,
    # ni siquiera para mover su marca de tiempo (ver guardar_publicaciones).
    payload = [{"sku": f["sku"], "canal": f["canal"], "cuenta": f["cuenta"],
                "ml_item_id": f["ml_item_id"], "visitas_30d": f["visitas_30d"]}
               for f in filas if f.get("visitas_30d") is not None]
    guardadas = competencia_store.guardar_publicaciones(payload)
    print(f"\n  guardadas: {guardadas} filas")

    if ok < len(filas) / 2:
        print(f"\nERROR: sólo {ok} de {len(filas)} trajeron visitas; no es una "
              "corrida sana.")
        print("  Antes de sospechar del token, busca 429 en los logs: la API de ML limita")
        print("  por aplicación y el backend comparte ese cupo. `competencia_ml._get` ya")
        print("  reintenta con espera; si aun así se abandonan llamadas, baja CONCURRENCIA.")
        return 1
    print("\nListo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
