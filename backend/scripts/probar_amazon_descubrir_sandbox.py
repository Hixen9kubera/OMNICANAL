"""
probar_amazon_descubrir_sandbox.py — El descubrimiento de Amazon y la rotación
por reloj contra el SANDBOX, leyendo el Amazon de verdad.

QUÉ TOCA
--------
  · Amazon: SOLO LECTURA (listar la cuenta y leer publicaciones). Necesita las
    variables AMAZON_* en el entorno del proceso; el script no las guarda.
  · Base: SOLO el sandbox. Aborta si el DSN es el de producción. MySQL apagado.
    Lo que escribe son las filas de Amazon del catálogo del sandbox con los datos
    reales de hoy: no se borran al final porque no son cobayas, son la verdad.

`PASOS=3,4` salta las dos pasadas completas: desde una laptop tardan ~5 min
(cada escritura cruza a la base); en Railway, junto a la base, son segundos.

Uso (desde la raíz, con env.staging ahí y AMAZON_* en el entorno):
  python backend/scripts/probar_amazon_descubrir_sandbox.py
"""
from __future__ import annotations

import asyncio
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.update({"APP_ENV": "staging", "MYSQL_ENABLED": "false",
                   "SUPABASE_WRITE_CHANNEL": "true", "SUPABASE_READ_CHANNEL": "true",
                   "SUPABASE_READ_PUBLICACIONES": "true"})

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if not settings.supabase_db_url:
    sys.exit("ABORT: env.staging sin SUPABASE_DB_URL.")
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION y este script ESCRIBE.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")
if not (settings.amazon_refresh_token and settings.amazon_seller_id):
    sys.exit("ABORT: faltan las credenciales AMAZON_* en el entorno.")

from services import amazon, channel_read, inventario  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

PASOS = set(os.environ.get("PASOS", "1,2,3,4").split(","))
_ok = True


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f" — {detalle}" if detalle else ""))


def conteo() -> int:
    return sdb.fetch_one("select count(*) n from channel.listings where canal='amazon'")["n"]


def fila(sku: str) -> dict | None:
    return sdb.fetch_one("select listing_id, situacion, price, stock_own, updated_at "
                         "from channel.listings where canal='amazon' and sku=%s", (sku,))


print(f"Sandbox {_ref[:8]}… · Amazon real (solo lectura) · MySQL apagado · pasos {sorted(PASOS)}\n")
antes = conteo()

if "1" in PASOS:
    print("1. En seco: cuánto hay en Amazon y cuánto falta en el panel")
    r0 = asyncio.run(inventario.descubrir_amazon(aplicar=False))
    print(f"     {({k: v for k, v in r0.items() if not k.endswith('_muestra')})}")
    print(f"     nuevas (muestra): {r0['nuevas_muestra'][:10]}")
    print(f"     fuera del catálogo (muestra): {r0['fuera_muestra'][:5]}")
    check("lee la cuenta COMPLETA pese al tope de 1,000",
          r0["ok"] and r0["completo"] and (r0["total_amazon"] or 0) > 1000,
          f"{r0['leidas']} de {r0['total_amazon']}")
    check("en seco no escribe", r0["escritas"] == 0 and conteo() == antes)

if "2" in PASOS:
    print("\n2. Aplicando")
    r1 = asyncio.run(inventario.descubrir_amazon(aplicar=True))
    check("escribe todas las del catálogo y ninguna ajena",
          r1["ok"] and r1["escritas"] == r1["del_catalogo"],
          f"escritas {r1['escritas']} · del catálogo {r1['del_catalogo']} · ajenas {r1['fuera_de_catalogo']}")
    check("no borra nada", conteo() >= antes, f"{antes} → {conteo()} filas")

if "3" in PASOS:
    print("\n3. Sin cambios no toca la fecha (upsert solo-si-cambió), 50 publicaciones")
    m = fila("CAM-0030-MAT")
    check("CAM-0030-MAT tiene su ASIN y es comprable",
          bool(m) and m["listing_id"] == "B0HJ463BQB" and m["situacion"] == "BUYABLE", str(m)[:110])
    muestra = [x["sku"] for x in sdb.fetch_all(
        "select sku::text sku from channel.listings where canal='amazon' and listing_id is not null "
        "order by sku limit 50")]
    fechas = {s: fila(s)["updated_at"] for s in muestra}
    vivo = asyncio.run(amazon.datos_por_sku(muestra))
    filas = [inventario._fila_amazon({"sku": s, **vivo[s]}) for s in muestra if s in vivo]
    asyncio.run(inventario._upsert_async(filas))
    movidas = [s for s in muestra if fila(s)["updated_at"] != fechas[s]]
    check("releer y volver a escribir lo mismo no mueve la fecha",
          len(filas) >= 40 and len(movidas) <= 3,
          f"releídas {len(filas)} · con fecha nueva {len(movidas)} {movidas[:3]}")

if "4" in PASOS:
    print("\n4. La rotación por reloj sobre el universo del sandbox")
    universo = channel_read.universo_amazon()
    n = len(universo)
    vueltas = math.ceil(n / settings.sync_batch)
    vistos: set[str] = set()
    for ronda in range(10_000, 10_000 + vueltas):
        vistos |= {x["sku"] for x in inventario._tajada_reloj(
            universo, settings.sync_batch, ahora=ronda * settings.sync_interval_min * 60)}
    check("en una vuelta completa visita TODAS", len(vistos) == len({x["sku"] for x in universo}),
          f"{len(vistos)} de {n} en {vueltas} rondas (~{vueltas * settings.sync_interval_min / 60:.1f} h)")

print("\nRESULTADO:", "TODO PASA" if _ok else "HAY FALLAS")
sys.exit(0 if _ok else 1)
