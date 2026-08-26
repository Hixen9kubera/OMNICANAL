"""
probar_precio_al_abrir_sandbox.py — El refresco al abrir el cajon SELLA la
confirmacion, y no se convierte en un martillo.

POR QUE EXISTE
--------------
`services/precio_al_abrir.py` gasta llamadas a Mercado Libre para que el cajon
del producto muestre lo que la tienda cobra en ese momento. Toda esa inversion
se apoya en UNA condicion que el panel usa para creerle al numero:

    price_sale_at >= updated_at        (publicaciones_panel._oferta)

Y esa condicion depende de un trigger que no es nuestro: `trg_touch_listings`
sella `updated_at = now()` en CUALQUIER update. Si algun dia dejara de ser hora
de transaccion —o si el UPDATE de aqui cambiara de forma— el refresco seguiria
pidiendole precios a ML y **no confirmaria nada**: mismo costo, cero beneficio,
y sin ningun sintoma visible. Esta prueba pone eso en rojo antes que produccion.

Mide ademas las otras dos cosas que sostienen el diseno:

  · El PISO — que no se vuelva a preguntar por algo observado hace un momento.
  · La PUNTERIA — que el objetivo se arme con el SKU EXACTO. Una `q` que sea
    parte de un titulo o de otro SKU tiene que dar CERO publicaciones, no todas
    las que la busqueda encuentre. Es lo unico que separa "abrir un producto"
    de "recorrer una lista disparando cientos de llamadas a ML".

Escribe en el SANDBOX y lo deja como lo encontro (`price_sale` y `price_sale_at`
vuelven a su valor; `updated_at` no se puede devolver — el trigger es BEFORE
UPDATE — y por eso esto NO corre contra produccion: aborta si el DSN es el de
kubera).

La llamada a ML solo se ejercita si hay token disponible en el ambiente. Sin
token la prueba lo DICE y no finge haberlo probado; el sandbox no trae tokens.

Uso (desde la raiz del repo, con env.staging al lado):
  python backend/scripts/probar_precio_al_abrir_sandbox.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    ruta = ROOT / nombre
    if not ruta.exists():
        return d
    for l in ruta.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


env = cargar("env.staging")
dsn = env.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DB_URL", "")
if not dsn:
    print("No hay SUPABASE_DB_URL (env.staging en la raiz del repo). Nada que probar.")
    raise SystemExit(2)
if (env.get("SUPABASE_PROD_REF") or "tukwcvsi") in dsn:
    print("ABORTA: ese DSN es PRODUCCION. Esta prueba escribe; solo corre en sandbox.")
    raise SystemExit(2)

os.environ["SUPABASE_DB_URL"] = dsn
os.environ.setdefault("APP_ENV", "staging")
os.environ.setdefault("MYSQL_ENABLED", "false")
os.environ.setdefault("SYNC_ENABLED", "true")

from services import meli  # noqa: E402
from services import precio_al_abrir as pa  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

_FOTO = """
select l.sku::text as sku, l.listing_id, l.price_sale, l.price_sale_at,
       l.updated_at, (l.price_sale_at >= l.updated_at) as confirmada
  from channel.listings l
 where l.canal='mercado_libre' and l.listing_id=%(lid)s
 order by l.sku
"""


def _foto(lid: str) -> list[dict]:
    return sdb.fetch_all(_FOTO, {"lid": lid})


# ── 1. EL PISO — funcion pura, sin red ni base ────────────────────────────────
print("\n1. El piso decide bien cuando preguntarle a ML")
AHORA = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)


def _hace(**kw):
    return AHORA - timedelta(**kw)


CASOS = [
    ("nunca observada", None, _hace(hours=1), False, True),
    ("el par a medias: una fila sin observar", _hace(minutes=1), _hace(hours=1), True, True),
    ("recien observada y confirmada", _hace(seconds=30), _hace(hours=1), False, False),
    ("recien observada y SIN confirmar: manda el piso duro", _hace(seconds=30), _hace(seconds=10), False, False),
    ("observada hace 2 min, confirmada", _hace(minutes=2), _hace(hours=3), False, False),
    ("observada hace 2 min, SIN confirmar", _hace(minutes=2), _hace(minutes=1), False, True),
    ("observada hace 6 min: pasa el piso", _hace(minutes=6), _hace(hours=9), False, True),
    ("observada hace dias", _hace(days=5), _hace(days=6), False, True),
    ("fecha sin zona horaria", (AHORA - timedelta(minutes=2)).replace(tzinfo=None), _hace(hours=3), False, False),
]
for nombre, obs, cam, sin_obs, esperado in CASOS:
    got = pa.hay_que_preguntar(observada_at=obs, cambiada_at=cam, sin_observar=sin_obs,
                               ahora=AHORA, piso_min=5, piso_duro_s=60)
    check(nombre, got == esperado, f"pregunta={got}, esperado={esperado}")

# ── 2. LA PUNTERIA — el objetivo sale del SKU EXACTO ──────────────────────────
print("\n2. El objetivo se arma con el SKU exacto, no con la busqueda")
par = sdb.fetch_all("""
    select l.sku::text as sku, count(distinct l.listing_id) as pubs
      from channel.listings l
     where l.canal='mercado_libre' and nullif(l.listing_id,'') is not null
       and lower(coalesce(l.situacion,'')) <> 'closed'
     group by 1 having count(distinct l.listing_id) >= 1
     order by pubs desc, 1 limit 1""")
if not par:
    print("  El sandbox no tiene publicaciones de ML. Re-siembra con clonar_a_sandbox.py.")
    raise SystemExit(2)
SKU = par[0]["sku"]
objetivo = pa._objetivo(SKU)
check(f"{SKU} devuelve sus publicaciones", len(objetivo) >= 1, f"{len(objetivo)} publicacion(es)")
check("minusculas dan el MISMO objetivo (sku es citext)",
      len(pa._objetivo(SKU.lower())) == len(objetivo))
trozo = SKU[:6]
anchas = sdb.fetch_scalar(
    """select count(*) from channel.listings l
        where l.canal='mercado_libre' and l.sku::text ilike %(q)s""", {"q": f"%{trozo}%"})
check(f"'{trozo}' como subcadena NO refresca nada", len(pa._objetivo(trozo)) == 0,
      f"la busqueda ancha tocaria {anchas} filas; el refresco toca 0")
check("un SKU inexistente da objetivo vacio", pa._objetivo("NO-EXISTE-9999") == [])

# ── 3. EL SELLO — price_sale_at >= updated_at despues del UPDATE ──────────────
print("\n3. El UPDATE deja la observacion CONFIRMADA (es de lo que vive el panel)")
# Se prefiere una publicacion que cuelgue de DOS filas (SKU padre + variante):
# es el caso que puede salir mal, porque el UPDATE va por `listing_id` y tiene
# que dejar coherentes las dos. Si el sandbox no tiene ninguna, se usa la del
# SKU de arriba y se dice que ese caso no se ejercito.
_par = sdb.fetch_all("""
    select listing_id, count(*) as filas from channel.listings
     where canal='mercado_libre' and nullif(listing_id,'') is not null
     group by 1 having count(*) > 1 order by filas desc, 1 limit 1""")
lid = str(_par[0]["listing_id"]) if _par else str(objetivo[0]["listing_id"])
if not _par:
    print("  (el sandbox no tiene ningun listing_id con dos filas: ese caso no se ejercita aqui)")
antes = _foto(lid)
hist_antes = sdb.fetch_scalar("select count(*) from channel.listing_history")
try:
    n = pa._guardar([(lid, 123.45)])
    despues = _foto(lid)
    check("el UPDATE toca todas las filas de ese listing_id", len(despues) == len(antes),
          f"{len(despues)} fila(s) del mismo listing_id"
          + (" — el caso padre+variante (89 en produccion)" if len(despues) > 1 else ""))
    check("price_sale_at >= updated_at en TODAS", all(f["confirmada"] for f in despues))
    deltas = {(f["price_sale_at"] - f["updated_at"]).total_seconds() for f in despues}
    check("las dos fechas salen del MISMO now() de transaccion", deltas == {0.0}, f"delta {deltas}")
    check("price_sale quedo con el valor pedido",
          all(float(f["price_sale"]) == 123.45 for f in despues))
    hist = sdb.fetch_scalar("select count(*) from channel.listing_history") - hist_antes
    check("no ensucia listing_history", hist == 0,
          f"{hist} filas nuevas — fn_listing_history no audita price_sale (handoff a omni-datos)")
    check("el guardado reporta lo que escribio", n == 1)
finally:
    for f in antes:
        sdb.execute("""update channel.listings set price_sale=%s, price_sale_at=%s
                        where canal='mercado_libre' and listing_id=%s and sku=%s""",
                    (f["price_sale"], f["price_sale_at"], f["listing_id"], f["sku"]))
    vuelta = _foto(lid)
    check("el sandbox queda como estaba",
          all(a["price_sale"] == d["price_sale"] and a["price_sale_at"] == d["price_sale_at"]
              for a, d in zip(antes, vuelta)))

# ── 4. NO ROMPE LA PANTALLA ───────────────────────────────────────────────────
print("\n4. Ningun camino de error levanta una excepcion")
for etiqueta, arg in (("sin SKU", ""), ("SKU inexistente", "NO-EXISTE-9999"),
                      ("None", None)):
    inf = asyncio.run(pa.refrescar_sku(arg))
    check(f"{etiqueta} devuelve informe, no excepcion", isinstance(inf, dict) and "estado" in inf,
          str(inf.get("estado")))

# ── 5. LA LLAMADA A ML, solo si hay token ─────────────────────────────────────
print("\n5. La llamada a Mercado Libre")
cuenta = str(objetivo[0]["cuenta"])
if meli._access_token(cuenta):
    inf = asyncio.run(pa.refrescar_sku(SKU))
    print("   informe:", inf)
    check("contesto ML y se confirmo", inf["estado"] == pa.OK and inf["confirmadas"] > 0)
    inf2 = asyncio.run(pa.refrescar_sku(SKU))
    check("la reapertura inmediata NO vuelve a preguntar", inf2["estado"] == pa.PISO,
          f"omitidas por piso: {inf2['omitidas_piso']}")
else:
    print(f"   SIN TOKEN de {cuenta} en este ambiente: la llamada a ML NO se probo aqui.")
    print("   (el sandbox no trae tokens; se probo a mano contra ML real el 26-ago-2026)")

print("\n", "TODO OK" if _ok else "HAY FALLAS")
raise SystemExit(0 if _ok else 1)
