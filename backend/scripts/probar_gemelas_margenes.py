"""
probar_gemelas_margenes.py — Que las tres gemelas de Márgenes devuelvan lo MISMO
que devolvía MySQL, incluido el TIPO de la fecha.

SOLO LECTURA sobre producción. Corre en cualquier lado.

POR QUÉ EXISTE
--------------
El 14-ago, el día que se encendió la lectura del PASO 1, la columna
«Visitas · CR%» de Análisis se vació entera. En los logs, una vez por minuto:

    [WARNING] visitas no disponibles en la tabla:
              can't compare offset-naive and offset-aware datetimes

`enrich.listing_visits.consultado_at` es `timestamptz`, así que psycopg2 lo
devuelve CON zona; MySQL lo daba sin. Los tres consumidores calculan su TTL
contra `datetime.utcnow()` —naive— y comparar aware con naive lanza `TypeError`.
El `try` del llamador se tragaba la excepción: no rompía nada a la vista, solo
desaparecía el dato.

La paridad del PASO 1 se verificó celda por celda —13,735 + 971 + 1,485, cero
diferencias— y **aun así el arreglo se rompió**. La lección es la prueba: un
arnés que compara VALORES no ve un cambio de TIPO. Este mide lo que aquél no.

  T1  el tipo: las tres devuelven `consultado_at` SIN zona
  T2  la operación real: el cálculo de TTL de los tres consumidores no lanza
  T3  el valor no se movió: la fecha sigue siendo el mismo instante
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services import margenes_read as mr        # noqa: E402
from services import supabase_db as sdb         # noqa: E402

_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


# Muestras reales de producción
ids_v = [str(r["listing_id"]) for r in sdb.fetch_all(
    "select listing_id from enrich.listing_visits limit 30")]
dias = (sdb.fetch_one("select dias from enrich.listing_visits limit 1") or {}).get("dias", 30)
ids_f = [str(r["listing_id"]) for r in sdb.fetch_all(
    "select listing_id from enrich.listing_weight limit 30")]
pares_e = [(r["cuenta"], str(r["external_order_id"])) for r in sdb.fetch_all(
    "select cuenta, external_order_id from enrich.order_shipping_cost limit 30")]

visitas = mr.visitas_leer(ids_v, dias)
fichas = mr.ficha_leer(ids_f)
envios = mr.envio_leer(pares_e)
print(f"muestras: visitas {len(visitas)} · fichas {len(fichas)} · envíos {len(envios)}\n")

# ── T1 · el TIPO ───────────────────────────────────────────────────────────
print("T1 · `consultado_at` sin zona, como lo daba MySQL")
for nombre, cache in (("visitas", visitas), ("fichas", fichas), ("envíos", envios)):
    con_zona = [f for f in cache.values()
                if f.get("consultado_at") is not None
                and getattr(f["consultado_at"], "tzinfo", None) is not None]
    check(f"{nombre}: ninguna fecha trae zona", not con_zona,
          f"{len(con_zona)} de {len(cache)} con tzinfo")

# ── T2 · LA OPERACIÓN QUE FALLABA ──────────────────────────────────────────
# No se comprueba el tipo y ya: se REPITE la cuenta exacta de cada consumidor.
# Un arnés que solo mirara `tzinfo` pasaría aunque el llamador comparara de otra
# forma. Esto es lo que hace la página, tal cual.
print("\nT2 · el cálculo de TTL de los tres consumidores")
vence = datetime.utcnow() - timedelta(hours=24)
for nombre, cache, campo in (("visitas_ml", visitas, None),
                             ("ficha_ml", fichas, None),
                             ("envio_real", envios, "costo_vendedor")):
    try:
        n = sum(1 for f in cache.values()
                if f.get("consultado_at") is not None
                and f["consultado_at"] < vence)
        check(f"{nombre}: la comparación NO lanza", True, f"{n} vencidas de {len(cache)}")
    except TypeError as exc:
        check(f"{nombre}: la comparación NO lanza", False, str(exc))

# El caso que hoy se salva de casualidad: `envio_real` solo compara cuando el
# costo es NULL, y ahora mismo no hay ninguno. Se fuerza el camino a mano para
# que la prueba no dependa de ese accidente.
forzado = [dict(f, costo_vendedor=None) for f in envios.values()][:5]
try:
    n = sum(1 for f in forzado
            if f["costo_vendedor"] is None and f["consultado_at"] < vence)
    check("envio_real con costo NULL forzado (el camino que hoy no se ejecuta)",
          True, f"{n} de {len(forzado)} reintentarían")
except TypeError as exc:
    check("envio_real con costo NULL forzado", False, str(exc))

# ── T3 · el VALOR no se movió ──────────────────────────────────────────────
print("\nT3 · normalizar no corrió la fecha")
crudo = sdb.fetch_all(
    "select listing_id, consultado_at from enrich.listing_visits "
    "where dias = %s and listing_id = any(%s)", (int(dias), ids_v))
movidas = []
for r in crudo:
    a = r["consultado_at"]
    b = (visitas.get(str(r["listing_id"])) or {}).get("consultado_at")
    if a is None or b is None:
        continue
    if a.astimezone(timezone.utc).replace(tzinfo=None) != b:
        movidas.append((r["listing_id"], a, b))
check("la fecha sigue siendo el mismo instante en UTC", not movidas,
      f"{len(movidas)} corridas" if movidas else f"{len(crudo)} comparadas")
for m in movidas[:3]:
    print(f"        {m[0]}: origen={m[1]} normalizada={m[2]}")

print(f"\nRESULTADO: {'las gemelas cumplen su contrato' if _ok else 'HAY FALLAS'}")
sys.exit(0 if _ok else 1)
