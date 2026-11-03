"""
probar_catalogo_familia_sandbox.py — La 0080 (familia, preparación y baja del catálogo, PLAN MAESTRO 3.2, diseño de
Eduardo del 2-nov) contra el SANDBOX. Lo de la familia y los estados se LEE; lo del trigger y los CHECK se prueba en UNA
transacción que termina en ROLLBACK. Nunca toca producción (guardia de ref). De `env.staging` solo lee
SUPABASE_DB_URL y SUPABASE_PROD_REF.

  C1. Criterio de F0.3: toda variación (wc_parent_id) tiene parent_sku y es el sku de su padre; has_variations
      en los padres.
  C2. estado_ciclo lleno en todas las filas y SOLO preparación (draft | procesado | listo).
  C3. «Publicado» se calcula y cuenta por familia: una variación cuyo padre tiene una publicación abierta sale
      publicada; sin familia, no.
  C4. Mercado Libre: la función da EXACTAMENTE lo que la pestaña Catálogo (`channel_read.sql_publicado_ml`).
  C5. Trigger: status trash → archivado (por woo, papelera_woo) sin tocar estado_ciclo; volver a publish no lo
      desarchiva.
  C6. Un alta nueva nace draft y sin archivar.
  C7. Los CHECK frenan: un estado que no existe, una baja sin motivo, recodificado sin destino, destino sin
      recodificado y un recodificado hacia sí mismo. Una baja de persona con recodificado y destino entra, y la vista
      la ve.
  C8. `core.publicacion_abierta` canal por canal contra una tabla de casos, incluidos los que confunden la columna
      (status contra situación) en TikTok y Amazon.
  C9. service_role lee la vista (es invoker: ejecuta las funciones; M1).

Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_catalogo_familia_sandbox.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REF_KUBERA_PROD = "tukwcvsi"


def _env_staging() -> dict[str, str]:
    for p in (ROOT / "env.staging", ROOT.parent / "OMNICANAL" / "env.staging"):
        if p.exists():
            d = {}
            for s in p.read_text(encoding="utf-8").splitlines():
                k, _, v = s.partition("=")
                if k.strip() in ("SUPABASE_DB_URL", "SUPABASE_PROD_REF"):
                    d[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
            return d
    sys.exit("ABORT: no encontré env.staging (ni en la raíz ni en ../OMNICANAL).")


S = _env_staging()
m = re.search(r"postgres\.([a-z0-9]+):", S.get("SUPABASE_DB_URL", ""))
ref = m.group(1) if m else ""
if not ref or ref.startswith(REF_KUBERA_PROD) or ref == S.get("SUPABASE_PROD_REF", "").strip():
    sys.exit("ABORT: el destino no es el sandbox. Aborto.")

from services.channel_read import sql_publicado_ml  # noqa: E402  (solo arma texto SQL; no conecta)

resultados: list[bool] = []


def check(nombre: str, paso: bool, detalle: str = "") -> None:
    resultados.append(paso)
    print(f"  [{'PASA' if paso else 'FALLA'}] {nombre}" + (f" — {detalle}" if detalle else ""), flush=True)


def frena(c, sql: str, args=()) -> bool:
    c.execute("savepoint s")
    try:
        c.execute(sql, args)
    except psycopg2.errors.CheckViolation:
        c.execute("rollback to savepoint s")
        return True
    c.execute("rollback to savepoint s")
    return False


ABIERTA = "core.publicacion_abierta(l.canal, l.status, l.situacion, l.listing_id)"
cn = psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=30)
c = cn.cursor()
try:
    c.execute("set local statement_timeout = '300s'")
    c.execute("""select count(wc_parent_id), count(parent_sku),
                        count(*) filter (where wc_parent_id is not null and parent_sku is distinct from
                            (select p.sku from core.products p where p.wc_id = o.wc_parent_id)),
                        count(*) filter (where has_variations),
                        count(*) filter (where wc_id is not null and not has_variations
                            and exists (select 1 from core.products h where h.wc_parent_id = o.wc_id))
                   from core.products o""")
    var, con_ps, difs, padres, padres_sin_marca = c.fetchone()
    check("C1 cada variación con su parent_sku, 0 diferencias contra wc_parent_id",
          var == con_ps and difs == 0, f"{con_ps}/{var}, diferencias {difs}")
    check("C1 has_variations en todos los padres", padres > 0 and padres_sin_marca == 0,
          f"padres {padres}, sin marca {padres_sin_marca}")
    c.execute("""select count(*) filter (where estado_ciclo is null),
                        count(*) filter (where estado_ciclo not in ('draft', 'procesado', 'listo'))
                   from core.products""")
    nulos, fuera = c.fetchone()
    check("C2 estado_ciclo lleno y solo preparación", nulos == 0 and fuera == 0, f"nulos {nulos}, fuera {fuera}")
    c.execute(f"""select count(*) filter (where not core.sku_publicado(v.sku)),
                         count(*) filter (where core.sku_publicado(v.sku, false)
                                            <> exists (select 1 from channel.listings l where l.sku = v.sku and {ABIERTA})),
                         count(*)
                    from core.products v
                   where v.parent_sku is not null
                     and exists (select 1 from channel.listings l where l.sku = v.parent_sku and {ABIERTA})""")
    huecos, sin_familia_mal, n = c.fetchone()
    check("C3 la publicación del padre cuenta para sus variaciones", n > 0 and huecos == 0,
          f"{n} variaciones con padre publicado, sin publicar: {huecos}")
    check("C3 sin familia solo cuentan las suyas", sin_familia_mal == 0, f"distintas: {sin_familia_mal}")
    c.execute(f"""select count(*) filter (where
                         exists (select 1 from channel.listings l where l.sku = p.sku and l.canal = 'mercado_libre'
                                  and {ABIERTA}) is distinct from {sql_publicado_ml('p.sku')}),
                         count(*) filter (where {sql_publicado_ml('p.sku')})
                    from core.products p""")
    distintos, en_ml = c.fetchone()
    check("C4 Mercado Libre = la regla de Catálogo", en_ml > 0 and distintos == 0,
          f"{en_ml} SKUs publicados en ML, distintos {distintos}")
    CASOS = [
        ("mercado_libre", "published", "active", "MLM1", True), ("mercado_libre", None, "paused", "MLM1", True),
        ("mercado_libre", "published", "under_review", "MLM1", False), ("mercado_libre", "published", "active", "", False),
        ("mercado_libre", "active", None, "MLM1", False),
        ("amazon", "PUBLISHED", "BUYABLE", "B0", True), ("amazon", None, "DISCOVERABLE", "B0", True),
        ("amazon", "DELETED", "BUYABLE", "B0", False), ("amazon", "INVALID", None, "B0", False),
        ("amazon", "PUBLISHED", "closed", "B0", False), ("amazon", "BUYABLE", None, "B0", False),
        ("tiktok", "ACTIVATE", None, "1", True), ("tiktok", "SELLER_DEACTIVATED", "APPROVED", "1", True),
        ("tiktok", "PLATFORM_DEACTIVATED", None, "1", True), ("tiktok", "FREEZE", None, "1", True),
        ("tiktok", "DRAFT", "NONE", "1", False), ("tiktok", "DELETED", None, "1", False),
        ("tiktok", "PENDING", "PRE_APPROVED", "1", False), ("tiktok", None, "ACTIVATE", "1", False),
        ("temu", "2/8", None, "1", True), ("temu", "3/2", None, "1", True), ("temu", "3/3", None, "1", True),
        ("temu", "4/7", None, "1", False), ("temu", "5/None", None, "1", False), ("temu", "9/9", None, "1", False),
        ("walmart", "PUBLISHED", None, "1", True), ("walmart", "UNPUBLISHED", None, "1", False),
        ("general", "publish", "active", "1", False), ("shein", "ACTIVE", "active", "1", False),
    ]
    malos = []
    for canal, st, sit, lid, esperado in CASOS:
        c.execute("select core.publicacion_abierta(%s, %s, %s, %s)", (canal, st, sit, lid))
        if c.fetchone()[0] is not esperado:
            malos.append((canal, st, sit, lid))
    check("C8 la regla canal por canal contra la función real", not malos, f"{len(CASOS)} casos, malos: {malos}")
    cn.rollback()
    try:
        c.execute("set local role service_role")
        c.execute("select count(*) from core.products_ciclo_v where archivado_at is not null")
        check("C9 service_role lee la vista (ejecuta las funciones)", True)
    except psycopg2.Error as exc:
        check("C9 service_role lee la vista (ejecuta las funciones)", False, f"{type(exc).__name__}: {exc}".strip())
    cn.rollback()

    c.execute("set local statement_timeout = '60s'")
    c.execute("select sku, estado_ciclo from core.products where status = 'publish' and archivado_at is null limit 1")
    sku, estado = c.fetchone()
    c.execute("update core.products set status = 'trash' where sku = %s "
              "returning archivado_at is not null, archivado_por, archivado_motivo, estado_ciclo", (sku,))
    r = c.fetchone()
    check("C5 status trash → archivado por woo, papelera_woo, sin tocar la preparación",
          r == (True, "woo", "papelera_woo", estado), str(r))
    c.execute("update core.products set status = 'publish' where sku = %s returning archivado_at is not null", (sku,))
    check("C5 volver a publish no lo desarchiva", c.fetchone()[0] is True)
    c.execute("insert into core.products (sku, status) values ('ZZZ-CATALOGO-1', 'draft') "
              "returning estado_ciclo, archivado_at")
    check("C6 un alta nueva nace draft y sin archivar", c.fetchone() == ("draft", None))
    check("C7 el CHECK frena un estado que no existe",
          frena(c, "update core.products set estado_ciclo = 'publicado' where sku = 'ZZZ-CATALOGO-1'"))
    check("C7 una baja sin motivo no entra",
          frena(c, "update core.products set archivado_at = now(), archivado_por = 'x@kubera.mx' "
                   "where sku = 'ZZZ-CATALOGO-1'"))
    check("C7 recodificado sin destino no entra",
          frena(c, "update core.products set archivado_at = now(), archivado_por = 'x@kubera.mx', "
                   "archivado_motivo = 'recodificado' where sku = 'ZZZ-CATALOGO-1'"))
    check("C7 destino sin recodificado no entra",
          frena(c, "update core.products set archivado_at = now(), archivado_por = 'x@kubera.mx', "
                   "archivado_motivo = 'duplicado', archivado_sku_destino = %s where sku = 'ZZZ-CATALOGO-1'", (sku,)))
    check("C7 un recodificado hacia sí mismo no entra",
          frena(c, "update core.products set archivado_at = now(), archivado_por = 'x@kubera.mx', "
                   "archivado_motivo = 'recodificado', archivado_sku_destino = 'ZZZ-CATALOGO-1' "
                   "where sku = 'ZZZ-CATALOGO-1'"))
    c.execute("update core.products set archivado_at = now(), archivado_por = 'x@kubera.mx', "
              "archivado_motivo = 'recodificado', archivado_sku_destino = %s where sku = 'ZZZ-CATALOGO-1'", (sku,))
    c.execute("select situacion from core.products_ciclo_v where sku = 'ZZZ-CATALOGO-1'")
    check("C7 una baja de persona (recodificado con destino) entra y la vista la ve", c.fetchone()[0] == "baja_limpia")
finally:
    cn.rollback()
    cn.close()

with psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=15) as cn2, cn2.cursor() as c2:
    c2.execute("set transaction read only")
    c2.execute("select count(*) from core.products where sku = 'ZZZ-CATALOGO-1'")
    quedan = c2.fetchone()[0]
    c2.execute("select count(*) from core.products where archivado_at is not null")
    archivados = c2.fetchone()[0]
    cn2.rollback()
check("tras el ROLLBACK no queda la fila de prueba ni archivados", quedan == 0 and archivados == 0,
      f"prueba {quedan}, archivados {archivados}")
print(f"\nRESULTADO: {sum(resultados)}/{len(resultados)} PASAN")
sys.exit(0 if all(resultados) else 1)
