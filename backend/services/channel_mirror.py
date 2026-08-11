"""
channel_mirror.py — Espejo del dominio CHANNEL hacia Supabase (dual-write, F3).

Cada tanda del sync de inventario (canal_inventario en MySQL) se replica a
`channel.listings`; el TRIGGER de la base (channel.fn_listing_history) captura
automáticamente los cambios de precio/stock/FULL/status en
`channel.listing_history` — la base del monitoreo de precios por plataforma.

Mismas reglas que costing_mirror (el patrón probado):
  1. MySQL manda; un fallo del espejo JAMÁS rompe el sync (log + migration_issues).
  2. Nunca en el event loop: los llamadores usan en_hilo().
  3. Upsert solo-si-cambió: los no-cambios no disparan el trigger ni tocan updated_at.
  4. Identidad primero: SKUs que el maestro no conoce se registran solos.
  5. Flag propio del dominio: SUPABASE_DUAL_WRITE_CHANNEL (revertir = apagarlo),
     independiente del de costos para poder apagar uno sin el otro.

F2 (30-jul) — el canal `general` (DROP, bodega propia) NO viene de
canal_inventario: su fuente murió el 14-jul y quedaron 20 filas fósiles. Hoy la
verdad de la bodega propia es `stock_watch_foto` (MySQL), que el vigilante de
Brandon refresca cada 20 min contra Woo. `sincronizar_drop()` la espeja a
channel.listings canal='general'. UNA fuente por campo: el sync de canales
sigue SIN tocar `general`, y el acta de channel dejó de auditarlo (regla 5 de
comparar_channel.py) porque MySQL ya no lo observa.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.channel_mirror")

# cuenta legacy -> uuid de core.accounts (cache de proceso; 4 filas, estable)
_cuentas: dict[str, str] | None = None

# misma regla que el ETL: las tablas viejas usan cuenta='' en canales mono-cuenta
_CUENTA_DEFAULT = {"mercado_libre": "BEKURA", "amazon": "AMAZON", "general": "GENERAL"}


def activo() -> bool:
    return settings.supabase_dual_write_channel and sdb.disponible()


def corte_activo() -> bool:
    """CORTE F6 (opción A): channel.listings primaria, canal_inventario espejo."""
    return settings.supabase_write_channel and sdb.disponible()


def en_hilo(fn: Callable, *args) -> None:
    if not activo():
        return
    try:
        asyncio.get_running_loop().run_in_executor(None, fn, *args)
    except RuntimeError:
        fn(*args)


def _cuenta_uuid(canal: str, cuenta: str) -> str | None:
    global _cuentas
    if _cuentas is None:
        _cuentas = {r["legacy_code"]: str(r["id"])
                    for r in sdb.fetch_all("select legacy_code, id from core.accounts")}
    legacy = (cuenta or "").strip() or _CUENTA_DEFAULT.get(canal, "")
    return _cuentas.get(legacy)


def _registrar_issue(sku, motivo: str) -> None:
    try:
        sdb.execute(
            "insert into ops.migration_issues (fase, tabla_origen, sku, motivo) "
            "values ('F3-dualwrite-channel', 'canal_inventario', %s, %s)",
            (sku, motivo[:500]),
        )
    except Exception:  # noqa: BLE001
        pass


def escribir_tanda(cur, rows: list[dict[str, Any]]) -> None:
    """Upserts de una tanda a nivel cursor (identidad + solo-si-cambió por
    fila). Lo comparten el espejo F3 y la primaria del CORTE F6 — es el mismo
    SQL que validó la racha del acta. El set_config de la vía lo pone el
    llamador (define QUIÉN escribió para el trigger de historia)."""
    for r in rows:
                sku = str(r.get("sku") or "").strip()
                if not sku or len(sku) > 100 or any(ch.isspace() for ch in sku):
                    continue  # inválidos conocidos: ya inventariados en el Excel
                canal = r.get("canal") or ""
                cuenta_id = _cuenta_uuid(canal, r.get("cuenta") or "")
                if not cuenta_id:
                    _registrar_issue(sku, f"cuenta sin uuid: canal={canal} cuenta={r.get('cuenta')}")
                    continue
                stock_full = r.get("stock_full") if canal == "mercado_libre" else r.get("stock_fba")
                cur.execute(
                    """insert into core.products (sku, status, source)
                       values (%s, 'draft', 'backend-dualwrite')
                       on conflict (sku) do nothing""", (sku,))
                # NULL en precio/stock/situación = "el lector no lo observó en esta
                # pasada" (p. ej. Amazon por lote no trae stock FBM) — se conserva
                # el valor anterior en vez de grabar un falso cambio a NULL.
                cur.execute(
                    """insert into channel.listings
                         (sku, account_id, canal, listing_id, price, price_base,
                          stock_own, stock_full, is_fulfillment, situacion,
                          logistic_type, stock_fba, currency)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       on conflict (sku, account_id, canal) do update set
                         listing_id = coalesce(excluded.listing_id, listings.listing_id),
                         price = coalesce(excluded.price, listings.price),
                         price_base = coalesce(excluded.price_base, listings.price_base),
                         stock_own = coalesce(excluded.stock_own, listings.stock_own),
                         stock_full = coalesce(excluded.stock_full, listings.stock_full),
                         is_fulfillment = excluded.is_fulfillment,
                         situacion = coalesce(excluded.situacion, listings.situacion),
                         logistic_type = coalesce(excluded.logistic_type, listings.logistic_type),
                         stock_fba = coalesce(excluded.stock_fba, listings.stock_fba),
                         currency = coalesce(excluded.currency, listings.currency)
                       where (listings.listing_id,
                              listings.price, listings.price_base,
                              listings.stock_own, listings.stock_full,
                              listings.is_fulfillment, listings.situacion,
                              listings.logistic_type, listings.stock_fba, listings.currency)
                         is distinct from
                             (coalesce(excluded.listing_id, listings.listing_id),
                              coalesce(excluded.price, listings.price),
                              coalesce(excluded.price_base, listings.price_base),
                              coalesce(excluded.stock_own, listings.stock_own),
                              coalesce(excluded.stock_full, listings.stock_full),
                              excluded.is_fulfillment,
                              coalesce(excluded.situacion, listings.situacion),
                              coalesce(excluded.logistic_type, listings.logistic_type),
                              coalesce(excluded.stock_fba, listings.stock_fba),
                              coalesce(excluded.currency, listings.currency))""",
                    (sku, cuenta_id, canal, r.get("item_id"), r.get("precio"),
                     # precio de lista (el tachado de ML): solo lo trae el lector
                     # de mercado_libre; en los demás canales viaja NULL y el
                     # coalesce conserva lo que hubiera
                     r.get("precio_base"),
                     r.get("stock_real"), stock_full, bool(r.get("es_full")),
                     r.get("situacion"),
                     r.get("logistica"), r.get("stock_fba"), r.get("moneda")),
                )


def espejar_inventario(rows: list[dict[str, Any]]) -> None:
    """Espeja una tanda del sync (las mismas filas que fueron a canal_inventario).

    Todo en UNA transacción: set_config de la vía (para el trigger de historia),
    identidad de SKUs nuevos, y upserts solo-si-cambió por fila. Con el CORTE
    encendido también sirve (lo usa backfill_situacion) — la vía sigue siendo
    'sync'."""
    if not (activo() or corte_activo()) or not rows:
        return
    try:
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('app.via', 'sync', true)")
            escribir_tanda(cur, rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("espejo channel falló (el sync continúa): %s", exc)
        _registrar_issue(None, f"espejo tanda fallo: {exc}")


def escribir_primario(rows: list[dict[str, Any]],
                      escribir_mysql: Callable[[], None]) -> None:
    """CORTE F6 (opción A): la tanda va PRIMERO a channel.listings (síncrona);
    canal_inventario MySQL queda de espejo inverso en hilo, best-effort.

    Con kubera caída el sync NO truena: se escribe MySQL como en el mundo
    viejo y el SIGUIENTE ciclo (15 min, full-refresh por tanda) auto-sana
    kubera — este dominio no necesita cola. Un fallo del espejo inverso deja
    MySQL desfasado ese ciclo (log + issue + Slack) y también se auto-sana."""
    if not rows:
        return
    try:
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('app.via', 'corte_channel', true)")
            escribir_tanda(cur, rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("primaria kubera channel falló — MySQL aguanta y el "
                    "siguiente ciclo sana: %s", exc)
        try:
            from services import alertas
            alertas.avisar(
                "escritura_fallback:channel",
                f"⚠️ Escritura de CHANNEL cayó a MySQL (tanda de {len(rows)}): "
                f"{type(exc).__name__}: {str(exc)[:140]}. El siguiente ciclo "
                f"del sync auto-sana kubera.")
        except Exception:  # noqa: BLE001
            pass
        escribir_mysql()  # si esto también truena, el error sube al llamador
        return

    # Desmantelamiento (paso 1): sin espejo inverso, canal_inventario queda
    # congelado a propósito. kubera ya guardó; no hay nada más que hacer.
    if not settings.channel_espejo_inverso:
        return

    def _inverso() -> None:
        try:
            escribir_mysql()
        except Exception as exc:  # noqa: BLE001
            log.warning("espejo inverso MySQL canal_inventario falló (el sync "
                        "continúa): %s", exc)
            _registrar_issue(None, f"espejo inverso MySQL fallo: {exc}")
            try:
                from services import alertas
                alertas.avisar(
                    "espejo_inverso:channel",
                    f"*Espejo inverso de CHANNEL a MySQL falló*: "
                    f"{type(exc).__name__}: {str(exc)[:140]}. kubera SÍ guardó; "
                    f"canal_inventario se sana en el siguiente ciclo.")
            except Exception:  # noqa: BLE001
                pass

    try:
        asyncio.get_running_loop().run_in_executor(None, _inverso)
    except RuntimeError:
        _inverso()


def backfill_situacion(situacion: str = "closed", canal: str | None = None,
                       max_items: int = 5000) -> dict[str, Any]:
    """Re-espeja la `situacion` que HOY tiene canal_inventario a channel.listings.

    Existe porque la `situacion` puede cambiar FUERA del barrido y este espejo
    solo se dispara desde `inventario._upsert()`: lo que no pasa por ahí nunca
    llega a Supabase. Caso real (29-jul-2026):
    `scripts/marcar_amazon_muertas.py` marcó 289 listados de Amazon como
    `closed` con un UPDATE directo a MySQL; el espejo no se enteró y el acta de
    Channel salió con_deltas el 30-jul rompiendo una racha de 9 días.

    Y NO se cura sola: para esos SKUs Amazon responde 404, así que el barrido
    manda `situacion=NULL` y el `coalesce` del upsert —correcto, significa "no
    lo observé en esta pasada"— conserva el valor viejo (`PUBLISHED`). Cada
    barrido reconfirma la divergencia en vez de corregirla.

    Idempotente: el `where ... is distinct from` del upsert no toca las filas
    que ya coinciden, así que tampoco ensucia `channel.listing_history`.
    """
    if not (activo() or corte_activo()):
        return {"ok": False,
                "motivo": "SUPABASE_DUAL_WRITE_CHANNEL apagado o sin DSN."}
    from services import db

    cond = ["LOWER(COALESCE(situacion, '')) = LOWER(%s)"]
    params: list[Any] = [situacion]
    if canal:
        cond.append("canal = %s")
        params.append(canal)
    params.append(int(max_items))
    filas = db.fetch_all(
        f"""SELECT sku, canal, cuenta, item_id, precio, stock_real, stock_full,
                   stock_fba, es_full, logistica, situacion, moneda
              FROM canal_inventario
             WHERE {' AND '.join(cond)}
             LIMIT %s""",
        tuple(params),
    )
    if not filas:
        return {"ok": True, "leidas": 0, "situacion": situacion,
                "canal": canal or "todos"}
    # Mismo escritor que el sync (no un UPDATE aparte): conserva el set_config
    # de la vía para el trigger de historia y la resolución de cuenta→uuid.
    espejar_inventario([dict(f) for f in filas])
    return {"ok": True, "leidas": len(filas), "situacion": situacion,
            "canal": canal or "todos"}


def sincronizar_drop(limite: int = 0) -> dict[str, Any]:
    """F2 — Espeja la bodega PROPIA (DROP) a channel.listings canal='general'.

    Fuente: `stock_watch_foto` en MySQL (sku, stock_woo), que el vigilante de
    Brandon reescribe cada 20 min leyendo Woo. Es la ÚNICA verdad del stock
    propio desde el 17-jul (Woo es fuente de verdad de inventario); el canal
    `general` de canal_inventario murió el 14-jul y no se toca.

    Solo viaja `stock_own`: precio, situación y FULL son de los marketplaces y
    aquí van NULL para que el `coalesce` del upsert conserve lo que hubiera.
    Los SKUs con `stock_woo` NULL se saltan — Woo no gestiona su stock, y un 0
    inventado sería peor que no decir nada.

    En bloque (execute_values): 13k SKUs fila por fila serían 13k viajes cada
    20 min. El `where ... is distinct from` del upsert hace que los no-cambios
    no toquen updated_at ni disparen el trigger de historia.
    """
    if not (activo() or corte_activo()):
        return {"ok": False, "motivo": "SUPABASE_DUAL_WRITE_CHANNEL apagado o sin DSN."}
    from psycopg2.extras import execute_values

    from services import db

    cuenta_id = _cuenta_uuid("general", "")
    if not cuenta_id:
        return {"ok": False, "motivo": "core.accounts no tiene la cuenta GENERAL."}

    sql = "SELECT sku, stock_woo FROM stock_watch_foto WHERE stock_woo IS NOT NULL"
    if limite:
        sql += f" LIMIT {int(limite)}"
    with db.get_cursor() as cur:
        cur.execute(sql)
        crudas = cur.fetchall()

    filas = []
    for r in crudas:
        sku = str(r["sku"] or "").strip()
        if not sku or len(sku) > 100 or any(ch.isspace() for ch in sku):
            continue  # mismos inválidos que descarta el espejo del sync
        filas.append((sku, cuenta_id, "general", int(r["stock_woo"])))
    if not filas:
        return {"ok": True, "leidas": len(crudas), "escritas": 0, "cambiadas": 0}

    cambiadas = 0
    try:
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('app.via', 'drop_watch', true)")
            for i in range(0, len(filas), 1000):
                lote = filas[i:i + 1000]
                # Identidad primero (regla 4): un SKU de Woo que el maestro no
                # conoce se registra solo, igual que en el espejo del sync.
                execute_values(
                    cur,
                    "insert into core.products (sku, status, source) values %s "
                    "on conflict (sku) do nothing",
                    [(f[0], "draft", "drop-watch") for f in lote])
                execute_values(
                    cur,
                    """insert into channel.listings
                         (sku, account_id, canal, stock_own) values %s
                       on conflict (sku, account_id, canal) do update set
                         stock_own = excluded.stock_own
                       where listings.stock_own is distinct from excluded.stock_own""",
                    lote)
                cambiadas += cur.rowcount
    except Exception as exc:  # noqa: BLE001
        log.warning("espejo DROP falló: %s", exc)
        _registrar_issue(None, f"espejo drop fallo: {exc}")
        return {"ok": False, "motivo": str(exc)[:300]}
    log.info("espejo DROP: %d SKUs leídos, %d con cambio real", len(filas), cambiadas)
    return {"ok": True, "leidas": len(crudas), "escritas": len(filas),
            "cambiadas": cambiadas}
