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

3. **¿ESCRIBIÓ EL SEAM?** — el desfase entre la hora de publicación y el
   `updated_at` de esa fila en `channel.listings`. Segundos = lo escribió el
   seam; minutos = lo alcanzó el sync de 15 min.

4. **El síntoma del hueco**: publicaciones que el publicador da por vivas y que
   en kubera no tienen `listing_id`.

⚠️ EL BLOQUE 3 ESTUVO ROTO, Y VALE LA PENA SABERLO
--------------------------------------------------
Su primera versión buscaba el rastro en `channel.listing_history.detectado_via
= 'publicar'`. **Ese trigger solo registra seis columnas** —`price`,
`stock_own`, `stock_full`, `situacion`, `is_fulfillment` y `status`— y el seam,
para Mercado Libre, escribe `listing_id` y `url`: ninguna está en la lista.

Resultado: el 16-ago reportaba *"el seam está APAGADO"* con el flag encendido y
14 publicaciones llegando a kubera **en 1-3 segundos**. Un detector ciego para el
caso exacto que venía a detectar.

Es el cuarto caso del mismo defecto en este proyecto (`turno_sync`, `padron`, la
métrica de retraso que se tiró el 14-ago, y éste): **medir una señal que la cosa
medida no puede producir.** Los tres primeros se detectaron midiendo; éste, por
una pregunta de Eduardo.

LO QUE ESTE ARNÉS **NO** PUEDE DECIR
------------------------------------
- Sin publicaciones en la ventana del bloque 3, **no opina** — y lo dice. Un
  "sin datos" no es un verde.
- El bloque 3 solo JUZGA lo publicado dentro de `--ventana-seam-h` (48 h por
  defecto). Lo anterior al seam es historia: incluirlo hacía reprobar por
  publicaciones que obviamente alcanzó el barrido, y un rojo mal calculado es
  tan inútil como un verde vacío.

Uso:
  ...python backend/scripts/comparar_seam_publicar.py
  ...python backend/scripts/comparar_seam_publicar.py --dias 30 --ventana-seam-h 24
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
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
    ap.add_argument("--ventana-seam-h", type=int, default=48,
                    help="solo se JUZGAN las publicaciones de esta ventana; las anteriores al seam son historia")
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

    # ── 3. ¿ESCRIBIÓ EL SEAM? — medido por el RELOJ, no por la bitácora ─────
    #
    # ⚠️ LA PRIMERA VERSIÓN DE ESTE BLOQUE ERA UN DETECTOR CIEGO: reportaba
    # "el seam está APAGADO" con el flag ENCENDIDO y funcionando (16-ago).
    #
    # Buscaba el rastro en `channel.listing_history.detectado_via = 'publicar'`.
    # Pero ese trigger solo registra SEIS columnas —`price`, `stock_own`,
    # `stock_full`, `situacion`, `is_fulfillment` y `status`— y el seam, para
    # Mercado Libre, escribe **`listing_id` y `url`**: ninguna está en esa lista.
    # O sea que el seam podía funcionar perfecto sin dejar una sola fila donde
    # este arnés lo buscaba. Medido el 16-ago: 14 publicaciones llegaron a kubera
    # en 1-3 s y el bloque seguía diciendo "apagado".
    #
    # Cuarto caso del mismo defecto en este proyecto (`turno_sync`, `padron`, la
    # métrica de retraso que se tiró el 14-ago, y éste): **medir una señal que la
    # cosa medida NO PUEDE producir.**
    #
    # LO QUE SE MIDE AHORA: el desfase entre la hora en que el publicador
    # registró la publicación y el `updated_at` de su fila en `channel.listings`.
    #
    #   ≤ UMBRAL_SEAM_SEG → escribió el SEAM (medido: 1-3 s)
    #   >  UMBRAL         → lo alcanzó el SYNC de 15 min; el seam no actuó
    #
    # Vale porque una publicación NUEVA trae un `listing_id` que la fila no
    # tenía, así que el upsert sí la toca. En una REPUBLICACIÓN con el mismo id
    # no cambia nada y la fila no se mueve — por eso solo cuentan las que traen
    # el id del publicador, que son las que sí debieron tocarse.
    # SOLO SE JUZGA LO PUBLICADO DENTRO DE LA VENTANA, y esto tampoco es un
    # detalle: la primera versión de este cálculo juzgó los 14 días completos y
    # reprobó con "82 por el sync" — publicaciones ANTERIORES a que el seam
    # existiera, que obviamente las alcanzó el barrido. Medir bien y juzgar mal
    # da un rojo tan inútil como un verde vacío.
    UMBRAL_SEAM_SEG = 60
    print(f"\n── 3. ¿escribió el SEAM? (desfase publicación → channel.listings, "
          f"últimas {args.ventana_seam_h} h) ──")
    corte = datetime.utcnow() - timedelta(hours=args.ventana_seam_h)
    medibles, por_seam, por_sync, viejas = [], [], [], 0
    for f in nuevas_ml:
        r = kb.get(("mercado_libre", str(f["cuenta"]).upper(), str(f["sku"]).lower()))
        if not r or not f["published_at"] or not r["updated_at"]:
            continue
        if str(r["listing_id"] or "") != str(f["ml_item_id"]):
            continue                      # republicación: la fila no debía moverse
        d = (r["updated_at"].replace(tzinfo=None) - f["published_at"]).total_seconds()
        if d < 0:
            continue
        if f["published_at"] < corte:
            viejas += 1
            continue                      # anterior al seam: es historia, no juicio
        medibles.append(d)
        (por_seam if d <= UMBRAL_SEAM_SEG else por_sync).append((f["sku"], d))

    if not medibles:
        print(f"  [ n/d] ninguna publicación en las últimas {args.ventana_seam_h} h "
              f"— el arnés NO puede opinar")
        print(f"         (no es un verde: es ausencia de evidencia. Publicar un "
              f"producto lo desbloquea)")
        if viejas:
            print(f"         [info] {viejas} publicaciones más viejas quedaron fuera "
                  f"del juicio, a propósito")
    else:
        medibles.sort()
        print(f"        {len(medibles)} publicaciones en la ventana · mediana "
              f"{medibles[len(medibles) // 2]:.0f}s · peor {max(medibles):.0f}s"
              + (f" · {viejas} más viejas ignoradas" if viejas else ""))
        for sku, d in por_sync[:4]:
            print(f"        tardó {d:.0f}s: {sku} — la alcanzó el sync, no el seam")
        check("el seam está escribiendo (desfase de segundos, no de minutos)",
              bool(por_seam) and not por_sync,
              f"{len(por_seam)} por el seam · {len(por_sync)} por el sync")

    # La bitácora se sigue mostrando, pero INFORMATIVA y con su límite dicho:
    # sirve para ver las otras vías vivas, no para juzgar el seam de ML.
    with pg.cursor() as c:
        c.execute("""select detectado_via, count(*), max(changed_at)
                       from channel.listing_history
                      where changed_at > now() - (%s || ' days')::interval
                      group by 1 order by 2 desc""", (args.dias,))
        vias = c.fetchall()
    print("        [info] vías en listing_history: "
          + " · ".join(f"{v or '(vacía)'} {n:,}" for v, n, _ in vias))
    print("        (NO registra `listing_id` ni `url`, así que no puede ver el "
          "seam de ML — ver el comentario del código)")

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
