"""
probar_rescate_cola_llena.py — Que un descarte por COLA LLENA deje rastro
reprocesable, en vez de morirse en la memoria.

NO TOCA NINGUNA BASE: `db.execute` se stubea y se captura lo que habría
escrito. Por eso corre en cualquier lado, sin guardia de ambiente.

QUÉ PRUEBA, Y POR QUÉ EXISTE
----------------------------
`kubera_mirror.espejar()` encola con `put_nowait`. Si la cola está llena
(`_COLA_MAX = 500` por worker) el evento **se descarta**. Hasta hoy eso solo se
anotaba en un ring de 500 eventos EN MEMORIA: moría con el siguiente reinicio y
nunca llegaba a `espejo_kubera_log`, que es la tabla que sobrevive y la que
alimenta el reproceso de /migracion.

O sea: el ÚNICO camino del espejo que pierde datos era también el único que no
se podía ni ver ni reintentar.

Se descubrió midiendo el PASO 4 — faltaban 156 filas de `amazon_imagenes` del 4
al 13-ago, con el espejo encendido y la tabla en la lista, y el log de errores
vacío. No hay prueba de que esas 156 se fueran por aquí (el ring ya se había
perdido); lo que sí quedó probado es que el canal de pérdida silenciosa existía.

  T1  con la cola llena, el evento se persiste con su payload → reprocesable
  T2  el rescate está ACOTADO: no genera un hilo por descarte sin límite
  T3  `espejar` sigue sin lanzar y sin bloquear al que la llama
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services import kubera_mirror as km   # noqa: E402

_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


# ── Stubs: ninguna base real se toca ───────────────────────────────────────
capturado: list[tuple] = []
_lock = threading.Lock()


class _DBFalsa:
    @staticmethod
    def execute(sql, params=None):
        with _lock:
            capturado.append((sql, params))
        return 1


import services.db as _db_real  # noqa: E402
_db_real.execute = _DBFalsa.execute
km._asegurar_tabla_log = lambda: True          # la tabla "existe"
km.activo = lambda tabla=None: True            # el espejo está encendido
km._MAX_PAYLOAD_JSON = 10_000

# Colas llenas de verdad: se llenan hasta que `put_nowait` truene.
colas = km._asegurar_workers()
for q in colas:
    try:
        while True:
            q.put_nowait(("x", "y", "z", "w", "v", {}, None))
    except Exception:  # noqa: BLE001 — queue.Full
        pass
print(f"colas llenas: {[q.qsize() for q in colas]}\n")

# ── T1 · el descarte se persiste ───────────────────────────────────────────
print("T1 · con la cola llena, ¿queda rastro reprocesable?")
capturado.clear()
km.espejar("services/imagenes_amazon.py", "_cache_put", "amazon_imagenes",
           "enrich.product_media", "UPSERT",
           {"sku": "PRUEBA-001", "kind": "amazon", "source_url": "http://x/y.jpg",
            "cdn_url": "http://z/y.jpg"}, clave="PRUEBA-001")
for _ in range(50):                 # el rescate va en un hilo
    if capturado:
        break
    time.sleep(0.1)

# Se BUSCA la fila del descarte en vez de tomar la primera: las colas se
# llenaron con tuplas basura y los workers reales las procesan y persisten SUS
# propios errores. Tomar `capturado[0]` medía el error de mi relleno, no el
# descarte — la prueba reprobaba por su propio montaje.
mias = [(s, p) for s, p in capturado
        if any(isinstance(x, str) and "ColaLlenaError" in x for x in (p or ()))]
check("el descarte llegó a espejo_kubera_log", bool(mias),
      "0 escrituras: se habría perdido en silencio" if not mias else
      f"{len(mias)} de {len(capturado)} escrituras son del descarte")
if mias:
    sql, params = mias[0]
    check("va a la tabla que SOBREVIVE al reinicio", "espejo_kubera_log" in sql)
    check("lleva el tipo de error correcto", "ColaLlenaError" in params,
          str([p for p in params if isinstance(p, str) and "Error" in p]))
    payload = params[-1]
    check("lleva el PAYLOAD, que es lo que permite reprocesar",
          "PRUEBA-001" in str(payload) and "source_url" in str(payload),
          str(payload)[:80])

# ── T2 · el rescate está acotado ───────────────────────────────────────────
print("\nT2 · ráfaga: ¿el rescate está acotado?")
check("hay tope de rescates concurrentes", km._RESCATE_MAX <= 8,
      f"_RESCATE_MAX = {km._RESCATE_MAX}")
antes = threading.active_count()
for i in range(60):
    km.espejar("services/x.py", "f", "amazon_imagenes", "enrich.product_media",
               "UPSERT", {"sku": f"R-{i}"}, clave=f"R-{i}")
pico = threading.active_count() - antes
check("60 descartes no generan 60 hilos", pico <= km._RESCATE_MAX + 2,
      f"hilos extra en el pico: {pico}")

# ── T3 · espejar sigue siendo inofensiva ───────────────────────────────────
print("\nT3 · `espejar` no lanza y no bloquea")
t0 = time.perf_counter()
try:
    km.espejar("services/x.py", "f", "amazon_imagenes", "enrich.product_media",
               "UPSERT", {"sku": "T3"}, clave="T3")
    lanzo = False
except Exception:  # noqa: BLE001
    lanzo = True
ms = (time.perf_counter() - t0) * 1000
check("no lanza", not lanzo)
check("no bloquea al llamador", ms < 50, f"{ms:.1f} ms")

print(f"\nRESULTADO: {'el descarte ya deja rastro' if _ok else 'HAY FALLAS'}")
sys.exit(0 if _ok else 1)
