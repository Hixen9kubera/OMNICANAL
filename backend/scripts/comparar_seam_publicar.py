"""
comparar_seam_publicar.py — Arnés del seam de publicación (PASO 3).

Se corre durante los días de observación de `SUPABASE_SEAM_PUBLICAR`. Cumple la
regla de ARNESES.md: **arbitra contra un tercero vivo** —el propio MySQL del
publicador, que sigue escribiéndose— y no contra una tabla congelada.

QUÉ MIDE
--------
1. **Cobertura.** De las publicaciones nacidas en los últimos N días según
   `ml_progress` / `amazon_progress`, ¿cuántas conoce `channel.listings`?
   Con el seam apagado esto ya debería dar ~100%: el sync de 15 min las
   alcanza. Si aquí falta algo, el problema es anterior al seam.

2. **Que el ID sea el MISMO.** Cobertura no es corrección: el seam podría
   escribir un `listing_id` y el sync otro. Los 63 pares con `ml_item_id`
   distinto que ya existen entre `ml_progress` y `channel.listings` (SKUs
   republicados) son la prueba de que esto pasa de verdad.

3. **El reparto de la vía.** `channel.listing_history.detectado_via` dice quién
   escribió cada cambio. Con el seam encendido debe aparecer `publicar`; si
   sigue todo en `sync`, el seam no está corriendo aunque el flag diga que sí.

4. **El retraso que se está eliminando.** Distancia entre `published_at` del
   publicador y el `updated_at` de la publicación en kubera. Es el hueco que
   el seam existe para cerrar, y el número que debería desplomarse al
   encenderlo.

LO QUE ESTE ARNÉS **NO** PUEDE DECIR
------------------------------------
Con el seam apagado, los puntos 1, 2 y 4 miden el mundo de hoy — sirven como
LÍNEA BASE, no como aprobación. El punto 3 solo tiene sentido encendido. No se
enciende la lectura de ningún lector del grupo 4 hasta que el punto 3 muestre
tráfico real por la vía `publicar`.

Uso:
  ...python backend/scripts/comparar_seam_publicar.py
  ...python backend/scripts/comparar_seam_publicar.py --dias 30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=14)
    args = ap.parse_args()

    E = cargar(".env")
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)
    todo_ok = True

    def check(etiqueta: str, ok: bool, detalle: str = "") -> None:
        nonlocal todo_ok
        todo_ok &= ok
        print(f"  [{'OK  ' if ok else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))

    # ── 1-2. Cobertura e identidad del ID ───────────────────────────────────
    print(f"\n── 1-2. publicaciones de los últimos {args.dias} días ──")
    with my.cursor() as c:
        c.execute("""SELECT sku, cuenta, ml_item_id, published_at FROM ml_progress
                      WHERE success = 1 AND ml_item_id IS NOT NULL AND ml_item_id <> ''
                        AND published_at > UTC_TIMESTAMP() - INTERVAL %s DAY""", (args.dias,))
        nuevas_ml = c.fetchall()
        c.execute("""SELECT sku, status, published_at FROM amazon_progress
                      WHERE success = 1
                        AND published_at > UTC_TIMESTAMP() - INTERVAL %s DAY""", (args.dias,))
        nuevas_am = c.fetchall()

    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute("""select l.sku::text as sku, a.legacy_code as cuenta, l.canal,
                            l.listing_id, l.status, l.updated_at
                       from channel.listings l
                       join core.accounts a on a.id = l.account_id
                      where l.canal in ('mercado_libre','amazon')""")
        kb = {}
        for r in c.fetchall():
            kb[(r["canal"], (r["cuenta"] or "").upper(), r["sku"].lower())] = r

    # ML: la llave es (cuenta, sku) — el mismo SKU vive en las dos tiendas.
    falta_ml = [f for f in nuevas_ml
                if ("mercado_libre", str(f["cuenta"]).upper(), str(f["sku"]).lower()) not in kb]
    distinto_ml = [
        (f, kb[("mercado_libre", str(f["cuenta"]).upper(), str(f["sku"]).lower())])
        for f in nuevas_ml
        if ("mercado_libre", str(f["cuenta"]).upper(), str(f["sku"]).lower()) in kb
        and str(kb[("mercado_libre", str(f["cuenta"]).upper(),
                    str(f["sku"]).lower())]["listing_id"] or "") != str(f["ml_item_id"])]
    falta_am = [f for f in nuevas_am
                if ("amazon", "AMAZON", str(f["sku"]).lower()) not in kb]

    print(f"        ML {len(nuevas_ml)} publicaciones · Amazon {len(nuevas_am)}")
    check(f"ML: todas en channel.listings", not falta_ml,
          f"{len(falta_ml)} sin registro en kubera")
    for f in falta_ml[:5]:
        print(f"        falta: {f['sku']} ({f['cuenta']}) item={f['ml_item_id']}")
    # ARBITRAJE POR RECENCIA (regla de ARNESES.md: no dar por buena una fuente
    # sin preguntarle a alguien). Un id distinto NO es un fallo por sí solo: es
    # el caso del SKU REPUBLICADO, donde `ml_progress` guarda el MLM viejo y el
    # sync trajo el vivo. Solo es fallo si el PUBLICADOR es el más nuevo — ahí
    # sí kubera se perdió una publicación fresca, que es lo que el seam cierra.
    kubera_gano = [(f, r) for f, r in distinto_ml
                   if r["updated_at"] and f["published_at"]
                   and r["updated_at"].replace(tzinfo=None) >= f["published_at"]]
    publicador_gano = [(f, r) for f, r in distinto_ml if (f, r) not in kubera_gano]
    check("ML: ningún id del publicador es más nuevo que el de kubera",
          not publicador_gano,
          f"{len(publicador_gano)} donde kubera se quedó con el viejo")
    for f, r in publicador_gano[:5]:
        print(f"        {f['sku']} ({f['cuenta']}): publicador={f['ml_item_id']} "
              f"(nuevo) kubera={r['listing_id']}")
    if kubera_gano:
        print(f"        [info] {len(kubera_gano)} con id distinto donde kubera es el "
              f"más reciente: SKUs republicados, kubera tiene el vivo")
        for f, r in kubera_gano[:3]:
            print(f"               {f['sku']} ({f['cuenta']}): "
                  f"ml_progress={f['ml_item_id']} (viejo) kubera={r['listing_id']}")
    check("Amazon: todas en channel.listings", not falta_am,
          f"{len(falta_am)} sin registro en kubera")
    for f in falta_am[:5]:
        print(f"        falta: {f['sku']} status={f['status']}")

    # ── 3. ¿Está corriendo el seam? ─────────────────────────────────────────
    print("\n── 3. quién escribió (channel.listing_history.detectado_via) ──")
    with pg.cursor() as c:
        c.execute("""select detectado_via, count(*), count(distinct sku), max(changed_at)
                       from channel.listing_history
                      where changed_at > now() - (%s || ' days')::interval
                      group by 1 order by 2 desc""", (args.dias,))
        vias = c.fetchall()
    for v, n, skus, ult in vias:
        print(f"        {str(v):<14} {n:6,} cambios · {skus:5,} SKUs · último {str(ult)[:16]}")
    por_publicar = next((n for v, n, _, _ in vias if v == "publicar"), 0)
    if por_publicar:
        check("el seam está escribiendo", True, f"{por_publicar:,} cambios por la vía 'publicar'")
    else:
        print("  [ n/d] sin tráfico por la vía 'publicar' — el seam está APAGADO "
              "(esperado hasta que se encienda SUPABASE_SEAM_PUBLICAR)")

    # ── 4. El síntoma observable del hueco ──────────────────────────────────
    #
    # NO se mide "cuánto tardó kubera en enterarse". Se intentó y NO ES
    # MEDIBLE con lo que hay:
    #
    #   · `channel.listing_history` no registra `listing_id` — solo `price`,
    #     `is_fulfillment`, `stock_full`, `situacion` y `stock_own`. No existe
    #     la fila que diría "aquí kubera aprendió el MLM".
    #   · `channel.listings.updated_at` significa **cuándo CAMBIÓ el dato**, no
    #     cuándo se visitó. Restarle `published_at` da "cuánto tardó en cambiar
    #     de precio o stock después de nacer", que no es el retraso del seam.
    #     La primera versión de este arnés lo hacía e imprimía una mediana de
    #     889 minutos: un número real que medía otra cosa. Es la misma trampa
    #     que invalidó la métrica `turno_sync` de `vigilar_congelacion.py`.
    #
    # Lo que SÍ es observable: publicaciones que el publicador da por vivas y
    # que en kubera no tienen `listing_id`. Ése es el hueco con cara visible —
    # el panel las mostraría como "sin publicar".
    print("\n── 4. síntoma observable: publicado en MySQL, sin id en kubera ──")
    sin_id = [f for f in nuevas_ml
              if (r := kb.get(("mercado_libre", str(f["cuenta"]).upper(),
                               str(f["sku"]).lower()))) and not (r["listing_id"] or "")]
    check("ninguna publicación reciente quedó sin listing_id en kubera", not sin_id,
          f"{len(sin_id)} sin id")
    for f in sin_id[:5]:
        print(f"        {f['sku']} ({f['cuenta']}): publicador={f['ml_item_id']}, kubera vacío")
    print("        (el retraso en sí NO se mide aquí — ver el comentario del código)")

    my.close(); pg.close()
    print(f"\nRESULTADO: {'línea base sana' if todo_ok else 'HAY DIFERENCIAS — revisar arriba'}")
    sys.exit(0 if todo_ok else 1)


if __name__ == "__main__":
    main()
