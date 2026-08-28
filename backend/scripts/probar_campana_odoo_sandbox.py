"""
probar_campana_odoo_sandbox.py — Los avisos de Odoo llegan a kubera, y la
campana no se convierte en manguera.

LOS DOS DEFECTOS QUE CIERRA
---------------------------
1. `odoo_watch._avisar_campana` mandaba su evento por `kubera_mirror.espejar`,
   que arranca preguntando `activo("webhook_eventos")` — y esa tabla no esta en
   `KUBERA_MIRROR_TABLAS`. Se descartaba EN SILENCIO. Medido el 28-ago: MySQL
   con 845 avisos de canal 'odoo', kubera con **cero**. Y son los unicos que
   alguien lee: los de Mercado Libre en MySQL estan congelados desde el 6-jul.

2. La campana leia `ops.webhook_events` SIN filtrar. Ahi viven 11,621 eventos
   de ML al dia (shipments, payments, invoices). Al pasar la campana a kubera,
   los avisos que importan quedaban sepultados en el primer segundo.

QUE SE PRUEBA, CON MYSQL APAGADO
--------------------------------
El sandbox corre con `MYSQL_ENABLED=false`: es el mundo de despues del corte.

  1. el aviso de Odoo aparece en kubera        (antes: cero, siempre)
  2. la campana lo muestra                     (es lo que se lee)
  3. la campana NO muestra el trafico de ML    (el filtro sirve)
  4. el contador lleva el MISMO filtro         (o el globito diria 11,621)
  5. el mismo evento repetido en UNA pasada no se duplica

Deja el sandbox como lo encontro.

Uso:
  ...python backend/scripts/probar_campana_odoo_sandbox.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_SKU = "SONDA-CAMPANA-ODOO"
_ok = True


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for linea in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def main() -> None:
    S = cargar("env.staging")
    os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["MYSQL_ENABLED"] = "false"
    os.environ["APP_ENV"] = "staging"
    from config import settings                              # noqa: E402
    from routers import webhooks                             # noqa: E402
    from services import odoo_watch                          # noqa: E402
    if settings.mysql_enabled:
        sys.exit("ABORT: MYSQL_ENABLED quedo encendido; la prueba no valdria.")
    print(f"sandbox · MYSQL_ENABLED={settings.mysql_enabled} · "
          f"env={settings.app_env}\n")

    pg = psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=25)
    pg.autocommit = True
    ml_id = None
    try:
        with pg.cursor() as c:
            c.execute("delete from ops.webhook_events where sku=%s", (_SKU,))

        # -- 1. el aviso de Odoo llega a kubera -----------------------------
        print("-- 1. el aviso de Odoo llega a kubera (con MySQL apagado) --")
        odoo_watch._avisar_campana([(_SKU, 8, 3)])
        with pg.cursor() as c:
            c.execute("""select canal, topic, resultado from ops.webhook_events
                          where sku=%s""", (_SKU,))
            fila = c.fetchone()
        check("quedo la fila en ops.webhook_events", fila is not None,
              f"{fila}" if fila else "no se escribio NADA — el defecto de origen")
        if fila:
            check("con canal 'odoo'", fila[0] == "odoo", f"canal={fila[0]!r}")
            check("y el texto del cambio de stock", "3" in (fila[2] or "")
                  and "8" in (fila[2] or ""), f"{fila[2]!r}")

        # -- 2 y 3. la campana muestra Odoo y esconde el ruido de ML --------
        print("\n-- 2/3. la campana muestra lo que se lee, no el trafico --")
        with pg.cursor() as c:
            c.execute("""insert into ops.webhook_events
                           (env, canal, topic, external_id, delivery_id, sku,
                            payload, procesado, resultado, recibido_at)
                         values (%s,'mercado_libre','shipments','SONDA-ML',
                                 'SONDA','SONDA-CAMPANA-ODOO','{}'::jsonb,
                                 true,'ruido de maquina', now())
                         returning id""", (settings.app_env,))
            ml_id = c.fetchone()[0]
        datos = asyncio.run(webhooks.notificaciones(limite=50))
        canales = {e["canal"] for e in datos["eventos"]}
        check("la campana trae el aviso de Odoo",
              any(e.get("sku") == _SKU and e["canal"] == "odoo"
                  for e in datos["eventos"]))
        check("y NO trae el trafico de Mercado Libre",
              "mercado_libre" not in canales,
              f"canales visibles: {sorted(canales)}")

        # -- 4. el contador lleva el mismo filtro ---------------------------
        print("\n-- 4. el contador cuenta lo mismo que la lista --")
        with pg.cursor() as c:
            c.execute("""select count(*) from ops.webhook_events
                          where recibido_at >= current_date""")
            todo_hoy = c.fetchone()[0]
        check("total_hoy NO cuenta el trafico de ML",
              datos["total_hoy"] < todo_hoy or todo_hoy == datos["total_hoy"] == 0,
              f"campana dice {datos['total_hoy']}, la tabla entera tiene {todo_hoy}")

        # -- 5. idempotencia DENTRO DE UNA PASADA ---------------------------
        #
        # La granularidad correcta es la PASADA, no el dia. `delivery_id`
        # lleva el segundo de la corrida, asi que dos pasadas distintas que
        # reportan el mismo SKU son DOS eventos --y esta bien: el stock pudo
        # ir 3->8, volver, y cambiar otra vez--. Si la llave fuera el dia
        # (como en `alertas._campana`, donde si corresponde) se perderian.
        #
        # Lo que SI tiene que colapsar es el mismo evento repetido DENTRO de
        # la misma pasada, que es el caso de un reintento. Eso se prueba aqui.
        print()
        print("-- 5. el mismo evento, dos veces en la misma pasada --")
        with pg.cursor() as c:
            c.execute("delete from ops.webhook_events where sku=%s", (_SKU,))
        odoo_watch._avisar_campana([(_SKU, 8, 3), (_SKU, 8, 3)])
        with pg.cursor() as c:
            c.execute("select count(*) from ops.webhook_events where sku=%s and canal='odoo'",
                      (_SKU,))
            n = c.fetchone()[0]
        check("se escribe una sola fila, no dos", n == 1,
              f"hay {n}; si son 2 la UNIQUE no esta protegiendo")
    finally:
        with pg.cursor() as c:
            c.execute("delete from ops.webhook_events where sku=%s", (_SKU,))
            if ml_id:
                c.execute("delete from ops.webhook_events where id=%s", (ml_id,))
        pg.close()
        print("\n(sandbox devuelto a como estaba)")

    print(f"\nRESULTADO: {'todo en verde' if _ok else 'HAY FALLAS'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
