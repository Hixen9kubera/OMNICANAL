"""
probar_catalogo_familia_sandbox.py — La 0080 (familia y ciclo del catálogo, PLAN MAESTRO 3.2) contra el SANDBOX.
Lo de la familia y los estados se LEE; lo del trigger y el CHECK se prueba en UNA transacción que termina en
ROLLBACK. Nunca toca producción (guardia de ref). De `env.staging` solo lee SUPABASE_DB_URL y SUPABASE_PROD_REF.

  C1. Criterio de F0.3: toda variación (wc_parent_id) tiene parent_sku y es el sku de su padre; has_variations
      en los padres.
  C2. estado_ciclo lleno en todas las filas y dentro del CHECK.
  C3. La publicación cuenta por familia: ninguna variación queda `draft` si su padre está `publicado` por un
      artículo abierto del padre.
  C4. Trigger: status trash → archivado; volver a publish no lo saca de archivado.
  C5. Un alta nueva nace `draft`.
  C6. El CHECK frena un estado que no existe.

Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_catalogo_familia_sandbox.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
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

resultados: list[bool] = []


def check(nombre: str, paso: bool, detalle: str = "") -> None:
    resultados.append(paso)
    print(f"  [{'PASA' if paso else 'FALLA'}] {nombre}" + (f" — {detalle}" if detalle else ""), flush=True)


cn = psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=30)
c = cn.cursor()
try:
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
                        count(*) filter (where estado_ciclo not in ('draft','procesado','listo','publicado','archivado'))
                   from core.products""")
    nulos, fuera = c.fetchone()
    check("C2 estado_ciclo lleno y dentro del CHECK", nulos == 0 and fuera == 0, f"nulos {nulos}, fuera {fuera}")
    c.execute("""select count(*) from core.products v join core.products p on p.sku = v.parent_sku
                  where v.estado_ciclo = 'draft' and p.estado_ciclo = 'publicado'
                    and v.status not in ('trash', 'deleted')
                    and exists (select 1 from channel.listings l where l.sku = p.sku
                                 and core.publicacion_abierta(l.canal, l.status, l.situacion))""")
    huecos = c.fetchone()[0]
    check("C3 la publicación del padre cuenta para sus variaciones", huecos == 0, f"variaciones draft: {huecos}")
    cn.rollback()

    c.execute("select sku from core.products where status = 'publish' and estado_ciclo <> 'archivado' limit 1")
    sku = c.fetchone()[0]
    c.execute("update core.products set status = 'trash' where sku = %s returning estado_ciclo", (sku,))
    check("C4 status trash → archivado", c.fetchone()[0] == "archivado")
    c.execute("update core.products set status = 'publish' where sku = %s returning estado_ciclo", (sku,))
    check("C4 volver a publish no saca de archivado", c.fetchone()[0] == "archivado")
    c.execute("insert into core.products (sku, status) values ('ZZZ-CATALOGO-1', 'draft') returning estado_ciclo")
    check("C5 un alta nueva nace draft", c.fetchone()[0] == "draft")
    c.execute("savepoint s")
    try:
        c.execute("update core.products set estado_ciclo = 'inventado' where sku = %s", (sku,))
        check("C6 el CHECK frena un estado que no existe", False)
    except psycopg2.errors.CheckViolation:
        c.execute("rollback to savepoint s")
        check("C6 el CHECK frena un estado que no existe", True)
finally:
    cn.rollback()
    cn.close()

with psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=15) as cn2, cn2.cursor() as c2:
    c2.execute("set transaction read only")
    c2.execute("select count(*) from core.products where sku = 'ZZZ-CATALOGO-1'")
    quedan = c2.fetchone()[0]
    cn2.rollback()
check("tras el ROLLBACK no queda la fila de prueba", quedan == 0)
print(f"\nRESULTADO: {sum(resultados)}/{len(resultados)} PASAN")
sys.exit(0 if all(resultados) else 1)
