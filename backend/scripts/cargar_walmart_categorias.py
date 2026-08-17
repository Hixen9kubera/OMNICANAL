"""Las 76 categorias del esquema de Walmart -> channel.categories."""
import os, sys
sys.path.insert(0, r"C:\Users\diaz2\OneDrive\Escritorio\omnicanal\backend")
os.environ.setdefault("APP_ENV", "production")
from psycopg2.extras import execute_values
from services import supabase_db as sdb, walmart_panel

# La fuente es la MISMA que indexa los requisitos: si se sacaran de otro lado,
# el selector ofrecería categorías para las que no hay reglas.
cats = [r["categoria_id"] for r in sdb.fetch_all(
    "select distinct categoria_id from channel.field_requirements "
    "where canal='walmart' and categoria_id <> '*' order by 1")]
print(f"categorias del esquema: {len(cats)}")

# Cuáles están AUTORIZADAS para publicar (tienen folio de exención probado).
try:
    from scripts.publicar_walmart import CATEGORIAS_AUTORIZADAS, CATEGORIAS_POR_CONFIRMAR
    aut = {c["clave_visible"] for c in CATEGORIAS_AUTORIZADAS.values()}
    porc = {c.get("clave_visible") for c in CATEGORIAS_POR_CONFIRMAR.values()}
except Exception as e:
    aut, porc = set(), set()
    print("  (no se pudo leer la config del publicador:", e, ")")
print(f"  autorizadas para publicar: {len(aut)} -> {sorted(aut)}")
print(f"  por confirmar: {len(porc)}")

filas = []
for c in cats:
    disp = "AUTORIZADA" if c in aut else ("POR_CONFIRMAR" if c in porc else "SIN_EXENCION")
    filas.append(("walmart", c, c, c, None, c, c, True, disp))

if "--aplicar" in sys.argv:
    with sdb.get_cursor() as cur:
        execute_values(cur, """insert into channel.categories
             (channel_id, category_id, name, path, parent_id, root_id, root_name,
              is_leaf, disponibilidad)
           values %s
           on conflict (channel_id, category_id) do update set
             name=excluded.name, path=excluded.path, is_leaf=excluded.is_leaf,
             disponibilidad=excluded.disponibilidad""", filas, page_size=200)
    print(f"\nListo: {len(filas)} categorias de Walmart en channel.categories.")
else:
    print(f"\nDRY-RUN: se escribirian {len(filas)} categorias.")
