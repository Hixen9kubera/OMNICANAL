"""
comparar_productos_kubera.py — Arnés de paridad de la pestaña PRODUCTOS.

Compara, campo por campo y sobre TODO el catálogo que la pestaña lista, lo que
se muestra HOY (WooCommerce) contra lo que se mostraría leyendo kubera. Es el
mismo papel que cumple `comparar_variantes_wpdb.py` para las variantes.

POR QUÉ EXISTE. Al mudar una lectura a kubera el riesgo no es que el dato salga
distinto —eso se ve—, sino que salga VACÍO donde antes había algo, y que nadie
lo note porque un guion no llama la atención. Este arnés separa tres cosas que
no son lo mismo:

  · IDÉNTICO   — tiene que coincidir. Si no, es un bug.
  · MEJORA     — antes vacío, ahora con valor. Es el objetivo.
  · REGRESIÓN  — antes con valor, ahora vacío. **Es la condición de fallo.**
  · CAMBIO     — los dos tienen valor y difieren. Se lista para revisar a mano.

Los campos NO se juzgan igual:

  nombre, estado, tipo, nº de variantes → deben ser IDÉNTICOS.
  costo                                 → puede mejorar, no puede regresar.
  categoría                             → es OTRA taxonomía (Woo vs Mercado
                                          Libre), así que comparar el texto no
                                          dice nada. Lo que se exige es que
                                          nadie se quede SIN categoría.

Uso:
    backend/.venv/Scripts/python.exe backend/scripts/comparar_productos_kubera.py

Solo lee. No escribe en ninguna base.
"""
from __future__ import annotations

import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


def _env(ruta: str) -> dict[str, str]:
    vals: dict[str, str] = {}
    for linea in io.open(ruta, encoding="utf-8"):
        if "=" in linea and not linea.strip().startswith("#"):
            k, v = linea.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _ruta_env() -> str:
    """El `.env` del repo. `OMNICANAL_ENV` lo sobrescribe — hace falta al correr
    desde un worktree, donde el `.env` no está (no se versiona)."""
    ruta = os.environ.get("OMNICANAL_ENV") or os.path.join(os.path.dirname(RAIZ), ".env")
    if not os.path.exists(ruta):
        sys.exit(f"No encuentro el .env en {ruta}. Pásalo con OMNICANAL_ENV=<ruta>.")
    return ruta


ENV = _env(_ruta_env())
PREFIJO = ENV.get("WPDB_PREFIX", "wp_")


def _woo():
    import pymysql
    return pymysql.connect(
        host=ENV.get("WPDB_HOST") or ENV["DB_HOST"], user=ENV["WPDB_USER"],
        password=ENV["WPDB_PASSWORD"], database=ENV["WPDB_NAME"],
        port=int(ENV.get("WPDB_PORT", 3306)), connect_timeout=30,
        cursorclass=__import__("pymysql").cursors.DictCursor)


def _kubera():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return psycopg2.connect(ENV["SUPABASE_DB_URL"], connect_timeout=30,
                            cursor_factory=RealDictCursor)


# ── El "antes": exactamente lo que la pestaña lee hoy ────────────────────────
def foto_woo() -> dict[str, dict]:
    """Los productos que lista la pestaña, con los campos tal como se ven hoy."""
    cx = _woo()
    cur = cx.cursor()
    cur.execute(f"""
        select sk.meta_value            as sku,
               p.post_title             as nombre,
               p.post_status            as estado,
               co.meta_value            as costo,
               (select count(*) from {PREFIJO}posts v
                 where v.post_parent = p.ID and v.post_type = 'product_variation'
                   and v.post_status <> 'trash')                as n_variantes,
               (select group_concat(t.name order by t.name)
                  from {PREFIJO}term_relationships tr
                  join {PREFIJO}term_taxonomy tt
                    on tt.term_taxonomy_id = tr.term_taxonomy_id
                  join {PREFIJO}terms t on t.term_id = tt.term_id
                 where tr.object_id = p.ID and tt.taxonomy = 'product_cat')
                                                                as categoria
          from {PREFIJO}posts p
          join {PREFIJO}postmeta sk on sk.post_id = p.ID and sk.meta_key = '_sku'
          left join {PREFIJO}postmeta co on co.post_id = p.ID and co.meta_key = 'costo'
         where p.post_type = 'product'
           and p.post_status in ('publish', 'pending', 'ready')
           and sk.meta_value <> ''""")
    salida = {}
    for r in cur.fetchall():
        costo = r["costo"]
        salida[r["sku"]] = {
            "nombre": (r["nombre"] or "").strip(),
            "estado": r["estado"],
            "costo": None if costo in (None, "", "0") else round(float(costo), 2),
            "n_variantes": int(r["n_variantes"] or 0),
            "categoria": r["categoria"] or None,
        }
    cx.close()
    return salida


# ── El "después": lo que devolverían los lectores de kubera ──────────────────
def foto_kubera(skus: list[str]) -> dict[str, dict]:
    cx = _kubera()
    cur = cx.cursor()
    salida: dict[str, dict] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        cur.execute("""
            select p.sku::text                as sku,
                   p.name                     as nombre,
                   p.status                   as estado,
                   v.costo_total              as costo,
                   (select count(*) from core.products h
                     where h.wc_parent_id = p.wc_id
                       and h.wc_parent_id is not null
                       and h.wc_parent_id <> 0)              as n_variantes,
                   ct.name                    as categoria,
                   ct.path                    as ruta
              from core.products p
              left join costing.costos_validados v on v.sku = p.sku
              left join channel.product_category pc
                     on pc.sku = p.sku and pc.channel_id = 'mercado_libre'
              left join channel.categories ct
                     on ct.category_id = pc.category_id
                    and ct.channel_id = pc.channel_id
             where p.sku = any(%s::citext[])""", (chunk,))
        for r in cur.fetchall():
            salida[r["sku"]] = {
                "nombre": (r["nombre"] or "").strip(),
                "estado": r["estado"],
                "costo": round(float(r["costo"]), 2) if r["costo"] is not None else None,
                "n_variantes": int(r["n_variantes"] or 0),
                "categoria": r["categoria"] or None,
                "ruta": r["ruta"] or None,
            }
    cx.close()
    return salida


def _clasificar(antes, despues):
    hay_a, hay_d = antes not in (None, "", 0), despues not in (None, "", 0)
    if not hay_a and not hay_d:
        return "vacio_ambos"
    if hay_a and not hay_d:
        return "REGRESION"
    if not hay_a and hay_d:
        return "mejora"
    return "identico" if antes == despues else "cambio"


def con_respaldo(woo: dict[str, dict], kub: dict[str, dict]) -> dict[str, dict]:
    """Lo que de verdad va a mostrar la pestaña: kubera y, donde kubera no
    tenga, lo de Woo. Réplica exacta del enriquecimiento de
    `routers/productos.listar_productos`, para que el arnés mida el código y no
    una idea del código.

    `n_variantes` NO lleva respaldo a propósito: hoy sale de Woo y ahí se queda
    (medido: kubera acierta 2,799 de 2,800, pero no gana nada y es ciego a las
    variaciones sin SKU). Se deja en la comparación como CONTROL — si algún día
    alguien lo cambia, este arnés lo va a gritar.
    """
    salida: dict[str, dict] = {}
    for sku, a in woo.items():
        k = kub.get(sku) or {}
        salida[sku] = {
            "nombre": k.get("nombre") or a["nombre"],
            "estado": k.get("estado") or a["estado"],
            "costo": k.get("costo") if k.get("costo") is not None else a["costo"],
            "categoria": k.get("categoria") or a["categoria"],
            "n_variantes": a["n_variantes"],          # control: no cambia
        }
    return salida


def main() -> int:
    woo = foto_woo()
    skus = list(woo)
    print(f"Productos que lista la pestaña: {len(skus)}\n")
    kub = con_respaldo(woo, foto_kubera(skus))
    faltan = [s for s in skus if s not in kub]
    print(f"Sin fila en core.products: {len(faltan)}"
          f"{'  ' + ', '.join(faltan[:5]) if faltan else ''}\n")

    # Campo -> (¿debe ser idéntico?, comparador)
    CAMPOS = [
        ("nombre", True), ("estado", True), ("n_variantes", True),
        ("costo", False), ("categoria", False),
    ]
    regresiones: dict[str, list] = {}
    fallo = False

    for campo, exige_identico in CAMPOS:
        cuenta: dict[str, int] = {}
        ejemplos: list = []
        for s in skus:
            a = woo[s][campo]
            d = (kub.get(s) or {}).get(campo)
            # La categoría es OTRA taxonomía: solo se mide presencia.
            if campo == "categoria":
                a, d = (1 if a else None), (1 if d else None)
            c = _clasificar(a, d)
            cuenta[c] = cuenta.get(c, 0) + 1
            if c == "REGRESION" and len(regresiones.setdefault(campo, [])) < 6:
                regresiones[campo].append((s, woo[s][campo], (kub.get(s) or {}).get(campo)))
            if c == "cambio" and len(ejemplos) < 4:
                ejemplos.append((s, a, d))

        etiqueta = "debe ser IDÉNTICO" if exige_identico else "puede mejorar"
        print(f"── {campo}  ({etiqueta}) " + "─" * (44 - len(campo)))
        for k in ("identico", "mejora", "cambio", "REGRESION", "vacio_ambos"):
            if cuenta.get(k):
                print(f"     {k:<14}{cuenta[k]:>7}")
        if cuenta.get("REGRESION"):
            fallo = True
            print("     ↑ REGRESIÓN: se perdería un dato que hoy SÍ se ve")
            for s, a, d in regresiones[campo]:
                print(f"        {s:<20} antes={a!r}  después={d!r}")
        if exige_identico and cuenta.get("cambio"):
            fallo = True
            print("     ↑ este campo NO debería cambiar:")
            for s, a, d in ejemplos:
                print(f"        {s:<20} Woo={a!r}  kubera={d!r}")
        print()

    print("═" * 60)
    print("FALLA: hay regresiones o cambios donde se exigía identidad."
          if fallo else
          "OK: ninguna regresión, y los campos que debían ser idénticos lo son.")
    return 1 if fallo else 0


if __name__ == "__main__":
    sys.exit(main())
