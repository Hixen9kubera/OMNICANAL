"""
probar_categoria_studio_sandbox.py — El Estudio saca la categoria de kubera.

QUE SE ARREGLO
--------------
`studio._categoria_mysql` era el ULTIMO lector de `categorias_ml` sin repuntar.
Los otros cinco pasaron a kubera el 12-ago; escritores no tiene ninguno desde el
22-jul.

Y no era solo el corte: las dos tablas discrepan en 2,270 SKUs, y en los
muestreados MySQL traia la categoria del PREDICTOR contra la que un humano
corrigio en el PANEL. De este lector salen el selector que se prellena en el
Estudio y el `ml_cat_id` que se manda al publicar.

QUE SE PRUEBA
-------------
  1. Con MySQL apagado contesta igual                 (el mundo tras el corte)
  2. Devuelve las MISMAS tres llaves que el original  (el front las consume)
  3. Los niveles terminan en la hoja, sin repetirla   (el armado compartido)
  4. Con la bandera apagada y MySQL apagado: None     (o sea, era un hueco real)

Uso:
  ...python backend/scripts/probar_categoria_studio_sandbox.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ok = True


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
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
    os.environ["SUPABASE_WRITE_CATEGORIAS"] = "true"
    os.environ["APP_ENV"] = "staging"
    from config import settings                       # noqa: E402
    from services import studio, supabase_db as sdb   # noqa: E402
    if settings.mysql_enabled:
        sys.exit("ABORT: MYSQL_ENABLED quedo encendido; la prueba no valdria.")
    print(f"sandbox · MYSQL_ENABLED={settings.mysql_enabled} · "
          f"WRITE_CATEGORIAS={settings.supabase_write_categorias}\n")

    # Un SKU que SI tenga categoria: preguntar en seco no prueba nada.
    filas = sdb.fetch_all(
        """select pc.sku::text as sku from channel.product_category pc
             join channel.categories ct on ct.category_id = pc.category_id
                                       and ct.channel_id = pc.channel_id
            where pc.channel_id='mercado_libre'
              and nullif(pc.category_id,'') is not null
              and nullif(ct.path,'') is not null
            limit 1""")
    if not filas:
        sys.exit("ABORT: el sandbox no tiene ningun SKU con categoria y ruta.")
    sku = filas[0]["sku"]
    print(f"── SKU de prueba: {sku} ──")

    got = studio._categoria_mysql(sku)
    check("contesta con MySQL apagado", got is not None,
          f"devolvio {got}")
    if got:
        check("trae las tres llaves que consume el front",
              set(got) == {"category_id", "ruta", "niveles"},
              f"llaves: {sorted(got)}")
        check("category_id no viene vacio", bool(got["category_id"]),
              str(got["category_id"]))
        check("hay niveles", bool(got["niveles"]), f"{got['niveles']}")
        # El armado compartido: la hoja se agrega solo si no estaba ya.
        check("los niveles no repiten la hoja al final",
              len(got["niveles"]) == len(set(got["niveles"]))
              or got["niveles"][-1] != got["niveles"][-2],
              f"{got['niveles']}")

    # ── 4. Sin la bandera y sin MySQL: el hueco que habia ───────────────────
    print("\n── el hueco, para que se vea que era real ──")
    settings.supabase_write_categorias = False
    sin = studio._categoria_mysql(sku)
    settings.supabase_write_categorias = True
    check("con la bandera apagada y MySQL apagado devuelve None", sin is None,
          f"devolvio {sin}; si trae datos es que quedo otro camino a MySQL")

    print(f"\nRESULTADO: {'todo en verde' if _ok else 'HAY FALLAS'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
