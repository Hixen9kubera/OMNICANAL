"""
probar_ml_sync_sandbox.py — Los tres arreglos del sync de ML (v0.597.0) contra
Postgres DE VERDAD: el CASE del rango, el `on conflict ... where` y el trigger
de historia no se pueden probar con un cursor falso.

QUÉ PRUEBA (cada caso primero con el flag APAGADO, para ver el defecto, y
luego ENCENDIDO):

  A. CHANNEL_GEMELAS_SITUACION — dos SKUs reclaman el mismo item de ML; el sync
     escribe la fila del que declara el item y la otra se queda `active` con su
     stock FULL. Encendido: la gemela pasa a la situación observada, SIN tocar
     su stock, y la historia lo registra. En Amazon (ASIN compartido) no se copia.
  B. CHANNEL_DUENO_ESTABLE — dos items declaran el mismo SKU (uno FULL, otro
     xd_drop_off) y se turnan la fila: `is_fulfillment` cambia en cada vuelta.
     Encendido: el hermano en estado igual o peor NO entra; uno estrictamente
     mejor sí; el mismo item siempre escribe; un SKU nuevo se inserta.
  D. El aviso de ML escribe en la fila DUEÑA (el SKU que declara ML) entre
     las que apuntan al item; la otra recibe solo la situación.
  C. SYNC_ML_ROTACION_RELOJ — sobre los listings REALES del sandbox (clon de
     producción): el orden de siempre da el mismo lote ronda tras ronda y la
     primera activa queda a miles de lugares; la tajada por reloj recorre todo.

Todo corre en UNA transacción que termina en ROLLBACK: filas de prueba,
historia y `updated_at` desaparecen y el sandbox queda como estaba. Aun así
escribe, así que aborta si el DSN es el de producción.

Uso (desde la raíz del repo, con env.staging al lado):
  python backend/scripts/probar_ml_sync_sandbox.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
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
    print("No hay SUPABASE_DB_URL (env.staging en la raíz del repo). Nada que probar.")
    raise SystemExit(2)
if (env.get("SUPABASE_PROD_REF") or "tukwcvsi") in dsn:
    print("ABORTA: ese DSN es PRODUCCIÓN. Esta prueba escribe; solo corre en sandbox.")
    raise SystemExit(2)

import psycopg2  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

from config import settings  # noqa: E402
from services import channel_mirror as cm  # noqa: E402
from services import inventario as inv  # noqa: E402

P = "ZZPRUEBA-MLSYNC-"          # prefijo de todo lo sintético


def main() -> None:
    cn = psycopg2.connect(dsn, connect_timeout=15, application_name="probar_ml_sync_sandbox")
    cur = cn.cursor(cursor_factory=RealDictCursor)
    flags = ("channel_gemelas_situacion", "channel_dueno_estable", "sync_ml_rotacion_reloj")
    antes = {f: getattr(settings, f) for f in flags}
    try:
        cur.execute("set local statement_timeout = '120s'")
        cur.execute("select set_config('app.via', 'prueba_ml_sync', true)")
        cur.execute("select legacy_code, id from core.accounts")
        cm._cuentas = {r["legacy_code"]: str(r["id"]) for r in cur.fetchall()}
        bek = cm._cuentas["BEKURA"]

        def poner(sku, lid, sit, *, full, stock_full=0, stock_own=0, canal="mercado_libre",
                  cuenta_id=bek):
            cur.execute("insert into core.products (sku, status, source) values (%s,'draft','prueba')"
                        " on conflict (sku) do nothing", (sku,))
            cur.execute(
                """insert into channel.listings (sku, account_id, canal, listing_id, situacion,
                        is_fulfillment, stock_full, stock_own, logistic_type, price)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,100)""",
                (sku, cuenta_id, canal, lid, sit, full, stock_full, stock_own,
                 "fulfillment" if full else "xd_drop_off"))

        def leer(sku, canal="mercado_libre"):
            cur.execute("select listing_id, situacion, is_fulfillment, stock_full, stock_own"
                        " from channel.listings where sku=%s and canal=%s", (sku, canal))
            return cur.fetchone()

        def hist(sku, campo):
            cur.execute("select count(*) n from channel.listing_history where sku=%s and campo=%s"
                        " and detectado_via='prueba_ml_sync'", (sku, campo))
            return cur.fetchone()["n"]

        def sync(sku, lid, sit, *, full, qty=0, canal="mercado_libre", cuenta="BEKURA"):
            """Lo que manda `sincronizar_ml` por un item leído de ML."""
            cm.escribir_tanda(cur, [{
                "sku": sku, "canal": canal, "cuenta": cuenta, "item_id": lid,
                "precio": 100, "precio_base": 100, "precio_venta": None,
                "stock_real": 0 if full else qty, "stock_full": qty if full else 0,
                "stock_fba": None, "es_full": 1 if full else 0,
                "logistica": "fulfillment" if full else "xd_drop_off",
                "situacion": sit, "moneda": "MXN", "fecha_publicacion": None}])

        # ── A. gemelas ────────────────────────────────────────────────────────
        print("\nA. CHANNEL_GEMELAS_SITUACION (padre y variante reclaman el mismo item)")
        for fase, flag in (("apagado", False), ("encendido", True)):
            settings.channel_gemelas_situacion = flag
            padre, var, lid = f"{P}PADRE-{fase}", f"{P}VAR-{fase}", f"MLMZZ1{int(flag)}"
            poner(padre, lid, "active", full=True, stock_full=54)   # la huérfana
            poner(var, lid, "active", full=True, stock_full=54)     # la que declara el item
            sync(var, lid, "paused", full=True, qty=0)               # ML: pausado, 0 piezas
            h = leer(padre)
            if not flag:
                check("apagado: la huérfana SIGUE active con 54 (el defecto)",
                      h["situacion"] == "active" and h["stock_full"] == 54, str(dict(h)))
            else:
                check("encendido: la huérfana pasa a paused", h["situacion"] == "paused", str(dict(h)))
                check("encendido: su stock NO se toca (54)", h["stock_full"] == 54)
                check("encendido: la historia registra el cambio", hist(padre, "situacion") == 1)
                sync(var, lid, "paused", full=True, qty=0)           # misma observación
                check("idempotente: reobservar no agrega historia", hist(padre, "situacion") == 1)
        # Amazon: el ASIN se comparte legítimamente entre SKUs → no se copia
        amz = cm._cuentas.get("AMAZON")
        if amz:
            poner(f"{P}AMZ-A", "B0ZZPRUEBA", "BUYABLE", full=False, canal="amazon", cuenta_id=amz)
            poner(f"{P}AMZ-B", "B0ZZPRUEBA", "BUYABLE", full=False, canal="amazon", cuenta_id=amz)
            sync(f"{P}AMZ-A", "B0ZZPRUEBA", "INACTIVE", full=False, canal="amazon", cuenta="")
            check("amazon: la otra fila del mismo ASIN no se toca",
                  leer(f"{P}AMZ-B", "amazon")["situacion"] == "BUYABLE")
        settings.channel_gemelas_situacion = False

        # ── B. hermanos ───────────────────────────────────────────────────────
        print("\nB. CHANNEL_DUENO_ESTABLE (dos items declaran el mismo SKU)")
        settings.channel_dueno_estable = False
        s = f"{P}HERM-apagado"
        poner(s, "MLMZZ20", "paused", full=True)
        for _ in range(3):                                           # tres rondas
            sync(s, "MLMZZ21", "paused", full=False)                 # el hermano
            sync(s, "MLMZZ20", "paused", full=True)                  # el guardado
        check("apagado: is_fulfillment aletea en cada vuelta (el defecto)",
              hist(s, "is_fulfillment") == 6, f"{hist(s, 'is_fulfillment')} cambios en 3 rondas")

        settings.channel_dueno_estable = True
        s = f"{P}HERM-encendido"
        poner(s, "MLMZZ30", "paused", full=True)
        for _ in range(3):
            sync(s, "MLMZZ31", "paused", full=False)
        h = leer(s)
        check("encendido: el hermano en empate NO entra",
              h["listing_id"] == "MLMZZ30" and h["is_fulfillment"] is True
              and hist(s, "is_fulfillment") == 0, str(dict(h)))
        sync(s, "MLMZZ30", "paused", full=True, qty=3)
        check("encendido: el item guardado sí escribe", leer(s)["stock_full"] == 3)
        sync(s, "MLMZZ31", "active", full=False, qty=8)
        h = leer(s)
        check("encendido: un hermano ACTIVO le gana a uno pausado",
              h["listing_id"] == "MLMZZ31" and h["situacion"] == "active"
              and h["stock_own"] == 8, str(dict(h)))
        sync(s, "MLMZZ30", "paused", full=True)
        check("encendido: el pausado ya no la recupera", leer(s)["listing_id"] == "MLMZZ31")
        sync(s, "MLMZZ31", "paused", full=False, qty=8)
        check("encendido: el dueño que se pausa se actualiza (mismo item)",
              leer(s)["situacion"] == "paused")
        sync(f"{P}NUEVO", "MLMZZ40", "active", full=False, qty=1)
        check("encendido: un SKU nuevo se inserta", (leer(f"{P}NUEVO") or {}).get("listing_id") == "MLMZZ40")
        poner(f"{P}SINID", None, "paused", full=False)
        sync(f"{P}SINID", "MLMZZ50", "paused", full=False)
        check("encendido: una fila sin listing_id lo adopta", leer(f"{P}SINID")["listing_id"] == "MLMZZ50")
        settings.channel_dueno_estable = False

        # ── C. rotación con los listings reales del sandbox ─────────────────
        print("\nC. SYNC_ML_ROTACION_RELOJ (listings reales del sandbox, cuenta BEKURA)")

        def universo_y_vistos():
            cur.execute("""select l.listing_id, l.updated_at, lower(l.situacion) sit
                             from channel.listings l join core.accounts a on a.id = l.account_id
                            where l.canal='mercado_libre' and a.legacy_code='BEKURA'
                              and lower(l.situacion) in ('active','paused')
                              and l.sku::text not like %s""", (P + "%",))
            fs = cur.fetchall()
            return (sorted({f["listing_id"] for f in fs}),
                    {f["listing_id"]: f["updated_at"].astimezone(timezone.utc).replace(tzinfo=None)
                     for f in fs},
                    {f["listing_id"] for f in fs if f["sit"] == "active"})

        epoca = datetime(1970, 1, 1)
        ids, vistos, activas = universo_y_vistos()
        lote1 = sorted(ids, key=lambda i: (i in vistos, vistos.get(i) or epoca))[:80]
        orden = sorted(ids, key=lambda i: (i in vistos, vistos.get(i) or epoca))
        primera = next(k for k, i in enumerate(orden) if i in activas)
        check("de siempre: el lote de 80 no tiene ninguna activa",
              not set(lote1) & activas, f"la primera activa está en el lugar {primera} de {len(ids)}")
        # La ronda "lee" esas 80 y ML contesta lo mismo que ya estaba guardado:
        # el upsert real (solo-si-cambió) no toca nada y `updated_at` no se mueve.
        cur.execute("""select l.sku::text sku, l.listing_id, l.price, l.price_base, l.stock_own,
                              l.stock_full, l.is_fulfillment, l.situacion, l.logistic_type, l.currency
                         from channel.listings l join core.accounts a on a.id = l.account_id
                        where a.legacy_code='BEKURA' and l.canal='mercado_libre'
                          and l.listing_id = any(%s)""", (lote1,))
        cm.escribir_tanda(cur, [{
            "sku": f["sku"], "canal": "mercado_libre", "cuenta": "BEKURA",
            "item_id": f["listing_id"], "precio": f["price"], "precio_base": f["price_base"],
            "precio_venta": None, "stock_real": f["stock_own"], "stock_full": f["stock_full"],
            "stock_fba": None, "es_full": f["is_fulfillment"], "logistica": f["logistic_type"],
            "situacion": f["situacion"], "moneda": f["currency"], "fecha_publicacion": None,
        } for f in cur.fetchall()])
        ids, vistos, activas = universo_y_vistos()
        lote2 = sorted(ids, key=lambda i: (i in vistos, vistos.get(i) or epoca))[:80]
        check("de siempre: releídas sin cambios, la ronda siguiente elige las MISMAS 80",
              lote1 == lote2)
        inv._intentados.clear()
        periodo = settings.sync_interval_min * 60
        rondas = -(-len(ids) // 80)
        leidos: set[str] = set()
        for r in range(rondas):
            leidos.update(inv._tajada_por_reloj("BEKURA", ids, vistos, 80,
                                                ahora=(2_000_000 + r) * periodo))
        check(f"reloj: {rondas} rondas ({rondas * settings.sync_interval_min / 60:.1f} h) leen el universo entero",
              leidos == set(ids), f"{len(leidos)}/{len(ids)}, activas {len(leidos & activas)}/{len(activas)}")

        # ── D. el aviso escribe en la fila dueña (v0.597.0) ──────────────────
        # El camino real de `refrescar_ml_item_id` con su SQL real (las lecturas
        # de channel_read corren en ESTE cursor, dentro de la transacción). Solo
        # el item de ML es simulado: aquí no se llama a ML ni se usa un token.
        print("\nD. El aviso de ML escribe en la fila DUEÑA (CHANNEL_GEMELAS_SITUACION)")
        import asyncio
        from unittest import mock

        from services import channel_read as cr

        def en_cursor(sql, params=None):
            cur.execute(sql, params)
            return cur.fetchall()

        async def _token(cuenta):
            return "tok-simulado"

        async def _upsert(rows):
            cm.escribir_tanda(cur, rows)
            return len(rows)

        for fase, flag in (("apagado", False), ("encendido", True)):
            settings.channel_gemelas_situacion = flag
            padre, lid = f"{P}AV-{fase}", f"MLMZZ6{int(flag)}"
            hijo = f"{padre}-AZL"
            poner(padre, lid, "active", full=True, stock_full=54)
            poner(hijo, lid, "active", full=True, stock_full=54)
            item = {"id": lid, "seller_custom_field": hijo, "status": "paused",
                    "available_quantity": 0, "price": 259,
                    "shipping": {"logistic_type": "fulfillment"}}

            async def _leer(cli, item_id, token, cuenta, _i=item):
                return _i

            # `dueno_de_item_ml` (limit 1 sin orden) forzado a dar el PADRE: el
            # caso que dejaba atrasada a la dueña.
            with mock.patch.object(cr.sdb, "fetch_all", en_cursor), \
                 mock.patch.object(cr, "dueno_de_item_ml",
                                   return_value={"sku": padre, "cuenta": "BEKURA"}), \
                 mock.patch.object(settings, "supabase_read_publicaciones", True), \
                 mock.patch.object(inv.meli, "access_token_async", _token), \
                 mock.patch.object(inv, "_leer_ml_item", _leer), \
                 mock.patch.object(inv, "_upsert_async", _upsert):
                r = asyncio.run(inv.refrescar_ml_item_id(lid))
            h, p = leer(hijo), leer(padre)
            if not flag:
                check("apagado: el aviso cae en el padre y la dueña sigue active con 54 (el defecto)",
                      r.get("sku") == padre and h["situacion"] == "active" and h["stock_full"] == 54,
                      f"dueña {dict(h)}")
            else:
                check("encendido: el aviso escribe en la dueña (paused, 0 piezas)",
                      r.get("sku") == hijo and h["situacion"] == "paused" and h["stock_full"] == 0,
                      f"dueña {dict(h)}")
                check("encendido: el padre recibe solo la situación y conserva su stock",
                      p["situacion"] == "paused" and p["stock_full"] == 54, f"padre {dict(p)}")
        settings.channel_gemelas_situacion = False
    finally:
        cn.rollback()                      # nada de lo de arriba queda
        cn.close()
        for f, v in antes.items():
            setattr(settings, f, v)

    print("\nRESULTADO:", "TODO OK" if _ok else "HAY FALLAS", "(transacción revertida: el sandbox quedó igual)")
    raise SystemExit(0 if _ok else 1)


if __name__ == "__main__":
    main()
