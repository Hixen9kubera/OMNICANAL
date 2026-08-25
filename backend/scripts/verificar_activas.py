# -*- coding: utf-8 -*-
"""
verificar_activas.py — que "activa" siga siendo UNA sola definición.

QUÉ PROTEGE
-----------
`solo_activas` de `GET /api/productos` filtra en SQL (donde se pagina) y
`/api/publicaciones` normaliza en Python (fila por fila). Son dos motores para
la MISMA regla, y la casa ya pagó una vez por tener dos escrituras de la misma
palabra: `channel_read._PUB_ML` / `_AMZ_VIVA` contestan "¿existe en el canal?"
y se leían como "¿está activa?".

Por eso el WHERE no se escribe a mano: `publicaciones_panel.filtro_sql_activas`
lo DERIVA de la misma tabla que usa `normalizar_estado`. Este arnés comprueba
que la derivación siga siendo cierta contra el vocabulario REAL de la base:

  1. para cada par `(situacion, status)` que existe hoy en `channel.listings`,
     el WHERE selecciona la fila si y solo si `normalizar_estado` la deja en
     `ESTADOS_VIVOS`;
  2. el total de cada listador coincide con el conteo SQL del mismo criterio,
     medido en la MISMA corrida (la tabla está viva: el sync de 15 min la
     reescribe, así que contra una constante daría falsos rojos);
  3. un canal que no puede contestar (`general`, `shein`) devuelve `None`, no
     una lista vacía — o sea "no sé", no "no hay".

CÓMO SE CORRE
-------------
    python backend/scripts/verificar_activas.py            # lee SUPABASE_DB_URL
    python backend/scripts/verificar_activas.py --dsn ...  # p. ej. el sandbox

SOLO LECTURA. Únicamente hace `SELECT` y **no** marca la sesión como read-only
(CONTRATO §4 / regla 13 de la casa: contra el pooler 6543 eso se pega a una
conexión compartida). Si el DSN trae el 6543 se cambia al 5432 solo.

NO está en `docs/ARNESES.md` a propósito: es de mano, no del chequeo diario.
Sale 0 si todo cuadra, 1 si no.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_AQUI))          # backend/

fallos: list[str] = []


def ok(cond: bool, etiqueta: str, extra: str = "") -> None:
    print(("  OK   " if cond else "  FALLA") + f" {etiqueta} {extra}")
    if not cond:
        fallos.append(etiqueta)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None,
                    help="DSN de Postgres; por defecto SUPABASE_DB_URL")
    args = ap.parse_args()

    from config import settings
    dsn = args.dsn or settings.supabase_db_url
    if not dsn:
        print("Sin DSN: define SUPABASE_DB_URL o pasa --dsn.")
        return 1
    # El pooler en modo transacción no sostiene nada largo; el 5432 sí.
    settings.supabase_db_url = re.sub(r":6543/", ":5432/", dsn)
    settings.supabase_read_publicaciones = True

    from services import amazon, meli, publicaciones_panel as pp
    from services import supabase_db as sdb
    from services import temu_panel, tiktok_panel, walmart_panel

    def escalar(sql, params=None):
        return int(sdb.fetch_all(sql, params or {})[0]["n"])

    # ── 1. El WHERE y el normalizador dicen lo mismo, valor por valor ────────
    print("== el WHERE en SQL y `normalizar_estado` coinciden ==")
    pares = sdb.fetch_all(
        """select canal, situacion, status, count(*) as n
             from channel.listings group by 1,2,3""")
    revisados = 0
    for f in pares:
        canal = f["canal"]
        columna, pliegue = pp._DECIDE.get(canal, (None, None))  # noqa: SLF001
        if not columna:
            continue
        crudo = f["situacion"] if columna == "situacion" else f["status"]
        vivo_py = pp.normalizar_estado(
            canal, f["situacion"], f["status"]) in pp.ESTADOS_VIVOS
        # Se reproduce el WHERE tal cual lo arma `filtro_sql_activas`.
        v = crudo or ""
        v = (v.lower() if pliegue == "lower"
             else v.upper() if pliegue == "upper" else v)
        vivo_sql = v in (pp.valores_activos(canal) or [])
        if vivo_py != vivo_sql:
            fallos.append(f"{canal}/{crudo}")
            print(f"  FALLA {canal} crudo={crudo!r} python={vivo_py} sql={vivo_sql}")
        revisados += 1
    ok(True, f"{revisados} pares (canal, situacion, status) revisados",
       f"-> {sum(f['n'] for f in pares)} filas de channel.listings")

    # ── 2. El total del listador == el conteo SQL, medido a la par ───────────
    print("\n== el total de cada listador == el conteo SQL de la misma corrida ==")
    ref = {
        "mercado_libre": """select count(*) as n from channel.listings l
                              join core.accounts a on a.id = l.account_id
                             where l.canal='mercado_libre'
                               and lower(coalesce(l.situacion,''))='active'""",
        "amazon": """select count(*) as n from channel.listings l
                      where l.canal='amazon'
                        and upper(coalesce(l.situacion,'')) in ('BUYABLE','PUBLISHED')""",
        "tiktok": """select count(*) as n from channel.listings l
                       join core.products p on p.sku=l.sku
                      where l.canal='tiktok' and upper(coalesce(l.status,''))='ACTIVATE'""",
        "temu": """select count(*) as n from channel.listings l
                     join core.products p on p.sku=l.sku
                    where l.canal='temu' and coalesce(l.status,'')='4/7'""",
        "walmart": """select count(*) as n from channel.listings l
                        join core.products p on p.sku=l.sku
                       where l.canal='walmart' and upper(coalesce(l.status,''))='PUBLISHED'""",
    }
    listadores = {
        "mercado_libre": lambda: meli.listar(1, 1, None, False, None, solo_activas=True),
        "amazon": lambda: amazon.listar(1, 1, None, False, solo_activas=True),
        "tiktok": lambda: tiktok_panel.listar(1, 1, solo_activas=True),
        "temu": lambda: temu_panel.listar(1, 1, solo_activas=True),
        "walmart": lambda: walmart_panel.listar(1, 1, solo_activas=True),
    }
    for canal, sql in ref.items():
        # Antes y después: `channel.listings` está viva y puede moverse EN MEDIO.
        # Si no se movió, el intervalo es un solo número y esto es igualdad.
        antes = escalar(sql)
        _, total = listadores[canal]()
        despues = escalar(sql)
        lo, hi = min(antes, despues), max(antes, despues)
        movio = "" if antes == despues else f"  (se movió {antes}->{despues} en la prueba)"
        ok(lo <= total <= hi, f"{canal}: listador vs SQL",
           f"-> {total} en [{lo},{hi}]{movio}")

    # ── 3. Quien no puede contestar dice "no sé", no "no hay" ────────────────
    print("\n== el canal que no puede contestar no se filtra a cero ==")
    ok(pp.valores_activos("general") is None, "general -> None (no `[]`)")
    ok(pp.filtro_sql_activas("shein") is None, "shein -> None")
    ok(meli.puede_filtrar_activas() == bool(settings.supabase_read_publicaciones)
       and amazon.puede_filtrar_activas() == bool(settings.supabase_read_publicaciones),
       "ML y Amazon reportan si su camino de lectura puede evaluarlo")

    print()
    if fallos:
        print("FALLOS:", len(fallos), fallos)
        return 1
    print("Sin fallos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
