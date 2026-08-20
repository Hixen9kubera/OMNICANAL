"""
probar_bloque1_sandbox.py — ¿Sobreviven al retiro de MySQL las seis lecturas
del BLOQUE 1 del PASO 3?

CORRE CONTRA EL SANDBOX, y esa es la gracia
-------------------------------------------
El sandbox arranca con `MYSQL_ENABLED=false` (decisión del 15-jul: staging no
tiene MySQL de pruebas). O sea que **el sandbox ya es el mundo de después del
retiro del esquema.** No hay que simular nada: la pregunta que este repunte
tiene que contestar —"¿estos lectores siguen sirviendo sin MySQL?"— es
exactamente la que el ambiente contesta solo.

LO QUE MIDE, Y POR QUÉ ASÍ
--------------------------
No compara paridad de datos. Eso ya lo hace `comparar_publicaciones_bloque1.py`
contra PRODUCCIÓN, que es donde la paridad significa algo; aquí los datos son un
clon y una diferencia solo diría "el clon tiene otra edad".

Lo que mide es el MECANISMO, con las dos corridas una al lado de la otra:

    bandera APAGADA  + sin MySQL  ->  lo que pasaría hoy si se retirara el esquema
    bandera ENCENDIDA + sin MySQL ->  lo que pasa con el repunte

Y el resultado de la primera columna es el argumento entero del paso 3: los seis
sitios envuelven su consulta en `try/except -> vacío`, así que sin MySQL **no
fallan, contestan "no está publicado"**. Una fuente que no está disponible
diciendo "no" en vez de "no sé" es el defecto que costó los 964 pedidos
fantasma, y aquí se puede ver en una tabla.

Uso:
  ...python backend/scripts/probar_bloque1_sandbox.py
  ...python backend/scripts/probar_bloque1_sandbox.py --skus SKU1,SKU2
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    if not p.exists():
        return d
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


# ── El ambiente se arma ANTES de importar nada del backend ───────────────────
# `settings` se congela al importarse, así que esto tiene que pasar primero.
_ST = cargar("env.staging")
if not _ST.get("SUPABASE_DB_URL"):
    sys.exit("ABORT: env.staging sin SUPABASE_DB_URL (va en la RAIZ y sin punto).")
os.environ["SUPABASE_DB_URL"] = _ST["SUPABASE_DB_URL"]
os.environ["MYSQL_ENABLED"] = "false"      # el mundo de después del retiro
os.environ["SUPABASE_READ_CHANNEL"] = "true"
os.environ["APP_ENV"] = "staging"

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION. El sandbox es otro proyecto.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido; la prueba pierde el sentido.")

from services import presencia, publicar, studio  # noqa: E402

_ok = True


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def con_bandera(valor: bool, fn, *a, **k):
    """Corre `fn` con la bandera en `valor`. Devuelve (resultado, excepción)."""
    previo = settings.supabase_read_publicaciones
    settings.supabase_read_publicaciones = valor
    try:
        return fn(*a, **k), None
    except Exception as exc:  # noqa: BLE001
        return None, exc
    finally:
        settings.supabase_read_publicaciones = previo


def skus_de_prueba(n: int) -> list[str]:
    """SKUs que el sandbox conoce publicados: la prueba necesita casos con SI."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """select l.sku::text as sku from channel.listings l
            where l.canal = 'mercado_libre'
              and nullif(l.listing_id,'') is not null
            group by 1 order by 1 limit %s""", (n,))
    return [f["sku"] for f in filas]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skus", default="")
    args = ap.parse_args()

    print(f"SANDBOX {_ref[:8]}... · MYSQL_ENABLED={settings.mysql_enabled}\n")
    skus = ([s.strip() for s in args.skus.split(",") if s.strip()]
            or skus_de_prueba(6))
    if not skus:
        sys.exit("ABORT: el sandbox no tiene publicaciones. Re-sembrar con "
                 "clonar_a_sandbox.py (ver regla 2 de CLAUDE.md).")
    print(f"SKUs de prueba ({len(skus)}): {', '.join(skus[:6])}\n")
    sku = skus[0]

    # ── 1. studio.estado_publicacion ────────────────────────────────────────
    print("── 1. studio.estado_publicacion (sitios :108 y :124) ──")
    viejo, e_v = con_bandera(False, studio.estado_publicacion, sku)
    nuevo, e_n = con_bandera(True, studio.estado_publicacion, sku)
    print(f"     bandera APAGADA -> {viejo if e_v is None else f'EXCEPCION {e_v}'}")
    print(f"     bandera PRENDIDA-> {nuevo if e_n is None else f'EXCEPCION {e_n}'}")
    check("sin MySQL y apagada, MIENTE: dice que no hay publicaciones",
          e_v is None and viejo == {"ml": [], "amazon": {"publicado": False,
                                                         "asin": None, "status": None}},
          "es el estado de hoy — el try/except convierte «no sé» en «no»")
    check("prendida: contesta desde kubera", e_n is None and bool(nuevo["ml"]),
          f"{len(nuevo['ml']) if nuevo else 0} cuenta(s) de ML" if e_n is None else str(e_n))
    check("y respeta la forma del contrato",
          bool(nuevo) and set(nuevo) == {"ml", "amazon"}
          and set(nuevo["amazon"]) == {"publicado", "asin", "status"}
          and all(set(x) == {"cuenta", "item_id"} for x in nuevo["ml"]),
          str(nuevo)[:90])

    # ── 2. publicar._ml_publicaciones ───────────────────────────────────────
    print("\n── 2. publicar._ml_publicaciones (sitio :154) ──")
    viejo, e_v = con_bandera(False, publicar._ml_publicaciones, sku)
    nuevo, e_n = con_bandera(True, publicar._ml_publicaciones, sku)
    print(f"     apagada -> {viejo}    prendida -> {nuevo}")
    check("apagada sin MySQL: lista vacia (a ese SKU no se le actualizaria nada)",
          e_v is None and viejo == [])
    check("prendida: devuelve las cuentas", e_n is None and bool(nuevo),
          str(e_n) if e_n else f"{len(nuevo)} cuenta(s)")
    check("con las llaves que espera quien la llama",
          bool(nuevo) and all(set(x) == {"cuenta", "item_id"} for x in nuevo))

    # ── 3. publicar._product_type_amazon ────────────────────────────────────
    print("\n── 3. publicar._product_type_amazon (sitio :262) ──")
    from services import supabase_db as sdb
    fila = sdb.fetch_all(
        """select sku::text as sku, product_type from channel.listings
            where canal='amazon' and product_type is not null limit 1""")
    if fila:
        s_amz, pt = fila[0]["sku"], fila[0]["product_type"]
        viejo, e_v = con_bandera(False, publicar._product_type_amazon, s_amz)
        nuevo, e_n = con_bandera(True, publicar._product_type_amazon, s_amz)
        print(f"     {s_amz}: apagada -> {viejo}    prendida -> {nuevo}")
        check("apagada sin MySQL: None -> el SKU cae a deteccion por titulo",
              e_v is None and viejo is None,
              "es el escalon de en medio de la regla 2 desapareciendo")
        check("prendida: devuelve el product_type del historico",
              e_n is None and nuevo == pt, f"esperado {pt}, obtuvo {nuevo}")
    else:
        check("hay un SKU de Amazon con product_type en el sandbox", False,
              "sin datos no se puede probar este sitio")

    # ── 4. presencia — la red ───────────────────────────────────────────────
    print("\n── 4. presencia_por_sku (sitios :101 y :119, la RED) ──")
    viejo, e_v = con_bandera(False, presencia.presencia_por_sku, skus)
    nuevo, e_n = con_bandera(True, presencia.presencia_por_sku, skus)
    check("apagada: no truena (el primario ya lee kubera)", e_v is None, str(e_v))
    check("prendida: no truena", e_n is None, str(e_n))
    if e_v is None and e_n is None:
        # LO QUE MAS IMPORTA DE ESTE SITIO: la red SUMA, no reemplaza. Si la
        # guardia se perdiera, un SKU contaria de mas por una sola publicacion
        # — es el defecto de los 1,387 que se arreglo el 14-ago.
        #
        # OJO con el chequeo ingenuo: `n > 1` NO es doble conteo. Un SKU
        # publicado en BEKURA y en SANCORFASHION tiene n=2 en mercado_libre con
        # toda razon. La primera version de esta prueba reprobaba por eso, y
        # reprobaba IGUAL con la bandera apagada — la senal de que el defecto
        # estaba en la prueba y no en lo probado.
        #
        # Lo que si delata la perdida de la guardia es COMPARAR las dos
        # corridas: sin MySQL la red vieja esta muerta, asi que la columna
        # "apagada" es el primario puro. La red nueva puede AGREGAR canales que
        # el primario no vio (para eso existe), pero no puede INFLAR uno que el
        # primario ya conto.
        def por_canal(res):
            return {(s, c.get("canal")): c.get("n", 1)
                    for s, canales in res.items() for c in canales}
        pv, pn = por_canal(viejo), por_canal(nuevo)
        inflados = [(k, pv[k], pn[k]) for k in pv if k in pn and pn[k] > pv[k]]
        perdidos = [k for k in pv if k not in pn]
        agregados = [k for k in pn if k not in pv]
        print(f"     apagada: {len(pv)} canales · prendida: {len(pn)} canales "
              f"(+{len(agregados)} que el primario no veia)")
        check("la red NO infla un canal que el primario ya conto", not inflados,
              f"{inflados[:3]}" if inflados else "todos los n coinciden")
        check("y no pierde ningun canal por el camino", not perdidos,
              f"{perdidos[:3]}" if perdidos else "")

    # ── 5. que NADIE haya tocado MySQL ──────────────────────────────────────
    print("\n── 5. ¿alguien intento abrir MySQL? ──")
    from services import db
    check("el pool de MySQL nunca se creo", db._pool is None,
          "si existiera, alguna de las rutas de arriba lo abrio a escondidas")

    print(f"\nRESULTADO: {'el bloque 1 sobrevive sin MySQL' if _ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
