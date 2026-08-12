"""
cargar_requisitos_ml.py — llena `channel.field_requirements` para Mercado Libre.

LA DIFERENCIA CON AMAZON, QUE CAMBIA LA FORMA
----------------------------------------------
El esquema de Amazon (`/definitions/productTypes/{tipo}`) trae el payload
COMPLETO: `item_name`, `product_description`, `purchasable_offer`… todo junto,
por tipo.

ML no. `/categories/{id}/attributes` devuelve **solo la ficha técnica** — 57
atributos para MLM1071, de los cuales **uno** es obligatorio (`BRAND`). El
título, el precio, el stock y las imágenes NO están ahí: ML los exige para
TODAS las categorías, no por categoría.

Por eso este cargador escribe en dos niveles:

  categoria_id = '*'      los comunes (titulo, precio, stock, imagenes…),
                          levantados del publicador vendorizado
  categoria_id = MLM…     los atributos obligatorios de esa categoría, leídos
                          de la API

Es justo para lo que existe el centinela `'*'`: sin él habría que repetir los
comunes en las 1,065 categorías que usa el catálogo.

CÓMO SE COMPRUEBAN LOS ATRIBUTOS
---------------------------------
Los obligatorios de ML por categoría (`BRAND`, `MODEL`…) NO son campos de
primer nivel: viven dentro de la llave `atributos` del contenido, como
`{"nombre": "BRAND", "valor": "..."}`.

Por eso se guardan con `campo_canonico = 'atributos'`: esa columna dice DÓNDE
buscar y `campo` dice QUÉ buscar. El semáforo mira dentro de la lista por el
`nombre` de cada entrada (y exige que el valor no venga vacío).

La primera versión los dejaba sin canónico para no dar falso verde, pero el
panel los etiquetaba "no editable desde el panel" — que es falso, los atributos
sí se editan en el Estudio. La comprobación dentro de la lista resuelve las dos
cosas.

Uso:
    python -m scripts.cargar_requisitos_ml                       # ensayo
    python -m scripts.cargar_requisitos_ml --aplicar --destino sandbox
    python -m scripts.cargar_requisitos_ml --aplicar --destino prod --limite 1100
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parent.parent.parent

CANAL = "mercado_libre"

# ── LOS COMUNES: lo que ML pide en TODA categoría ───────────────────────────
# Levantados de `vendor/ml_ready/publisher_core.py::build_payload`, que es el
# código que publicó 1,200+ productos — no de la documentación de ML.
# (campo_nativo, canonico, obligatorio, default_value)
COMUNES: list[tuple[str, str | None, bool, object]] = [
    ("title",              "titulo",         True,  None),
    ("available_quantity", "stock",          True,  None),
    ("price",              "precio_regular", True,  None),
    ("pictures",           "imagenes",       True,  None),
    ("category_id",        "categoria_id",   True,  None),
    ("seller_custom_field", "sku",           True,  None),
    # La descripción va en llamada APARTE (`/items/{id}/description`), no en el
    # item. Sigue siendo obligatoria para publicar.
    ("description",        "descripcion",    True,  None),
    # Constantes del publicador: el panel no las pide y no deben salir en rojo.
    ("listing_type_id",    None,             True,  "gold_pro"),
    ("condition",          None,             True,  "new"),
    ("shipping.mode",      None,             True,  "me2"),
    ("sale_terms",         None,             True,  "garantía del vendedor 30 días"),
    # BRAND es atributo de ficha, pero el publicador lo fija para TODO el
    # catálogo (DEFAULT_BRAND en publisher_core.py:50). Con respaldo no sale
    # en rojo. OJO: Amazon cae a "Generic" y ML pone "Ferrahome" — la
    # divergencia entre canales sigue abierta y es decisión de negocio.
    ("BRAND",              "brand",          True,  "Ferrahome"),
]


def _env_destino(destino: str) -> str:
    archivo = ROOT / ("env.staging" if destino == "sandbox" else ".env")
    vals: dict[str, str] = {}
    if archivo.exists():
        for line in io.open(archivo, encoding="utf-8"):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    dsn = vals.get("SUPABASE_DB_URL", "")
    if not dsn:
        sys.exit(f"ABORT: no hay SUPABASE_DB_URL para destino={destino}.")
    if destino == "sandbox" and "yvootpbz" not in dsn:
        sys.exit("ABORT: --destino sandbox pero la DSN no es del sandbox.")
    if destino == "prod" and "tukwcvsi" not in dsn:
        sys.exit("ABORT: --destino prod pero la DSN no es de producción.")
    return dsn


def categorias_mas_usadas(limite: int) -> list[tuple[str, int]]:
    """
    Las categorías de ML con más publicaciones vivas.

    El candado de lectura va POR TRANSACCIÓN (`set transaction read only`) y NO
    por sesión: las DSN entran por el pooler de Supabase en modo transacción y
    un ajuste de sesión se queda pegado en la conexión del servidor, que hereda
    quien la tome después — incluido el backend de Railway. Con
    `set_session(readonly=True)` la carga de Amazon murió a la mitad, envenenada
    por su propia lectura (v0.115.2).
    """
    import psycopg2
    cx = psycopg2.connect(_env_destino("prod"), connect_timeout=20)
    try:
        with cx.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute(
                """select category_id, count(*) n from channel.listings
                    where canal = %s and category_id is not null and category_id <> ''
                    group by 1 order by 2 desc limit %s""", (CANAL, limite))
            return [(c, n) for c, n in cur.fetchall()]
    finally:
        cx.rollback()
        cx.close()


def guardar(dsn: str, categoria: str, filas: list[dict]) -> None:
    import psycopg2
    import psycopg2.extras
    cx = psycopg2.connect(dsn, connect_timeout=20)
    try:
        with cx.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """insert into channel.field_requirements
                     (canal, categoria_id, campo, campo_canonico, obligatorio,
                      tipo, valores_permitidos, default_value, fuente,
                      leido_at, updated_at)
                   values %s
                   on conflict (canal, categoria_id, campo) do update set
                     campo_canonico = excluded.campo_canonico,
                     obligatorio    = excluded.obligatorio,
                     tipo           = excluded.tipo,
                     valores_permitidos = excluded.valores_permitidos,
                     default_value  = excluded.default_value,
                     fuente         = excluded.fuente,
                     leido_at       = excluded.leido_at,
                     updated_at     = now()""",
                [(CANAL, categoria, f["campo"], f["campo_canonico"], f["obligatorio"],
                  f["tipo"], f["valores_permitidos"], f["default_value"], f["fuente"])
                 for f in filas],
                template="(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,now(),now())",
                page_size=500,
            )
        cx.commit()
    finally:
        cx.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="sin esto NO escribe")
    ap.add_argument("--destino", choices=["sandbox", "prod"], default="sandbox")
    ap.add_argument("--limite", type=int, default=12)
    ap.add_argument("--saltar-cargadas", action="store_true")
    args = ap.parse_args()

    dsn = _env_destino(args.destino)
    print(f"DESTINO: {args.destino}   ({'ENSAYO' if not args.aplicar else 'ESCRIBIENDO'})\n")

    # ── 1. Los comunes, como fila '*' ──────────────────────────────────────
    comunes = [{"campo": c, "campo_canonico": k, "obligatorio": o, "tipo": None,
                "valores_permitidos": None,
                "default_value": json.dumps(d, ensure_ascii=False) if d is not None else None,
                "fuente": "codigo"} for c, k, o, d in COMUNES]
    print(f"COMUNES (categoria_id='*'): {len(comunes)} campos")
    for f in comunes:
        et = f"respaldo={f['default_value']}" if f["default_value"] else (f"canon={f['campo_canonico']}" if f["campo_canonico"] else "")
        print(f"   {f['campo']:<22} {et}")
    if args.aplicar:
        guardar(dsn, "*", comunes)
    print()

    # ── 2. Los atributos obligatorios, por categoría ───────────────────────
    from services import publicar_ready
    publicar_ready.configurar()
    from vendor.ml_ready import ml_api

    token = ml_api.get_token("BEKURA")
    if not token:
        print("SIN TOKEN de ML — solo se cargaron los comunes.")
        return 1

    cats = categorias_mas_usadas(args.limite)
    if args.saltar_cargadas:
        import psycopg2
        cx = psycopg2.connect(dsn, connect_timeout=20)
        try:
            with cx.cursor() as cur:
                cur.execute("select distinct categoria_id from channel.field_requirements "
                            "where canal = %s and categoria_id <> '*'", (CANAL,))
                ya = {r[0] for r in cur.fetchall()}
        finally:
            cx.close()
        antes = len(cats)
        cats = [(c, n) for c, n in cats if c not in ya]
        print(f"Ya cargadas: {antes - len(cats)}  ·  pendientes: {len(cats)}")

    print(f"Categorías a cargar: {len(cats)}\n")
    total, sin_obligatorios, fallidas = 0, 0, []
    for cat, n in cats:
        try:
            attrs = ml_api.get_category_attributes(cat, token)
        except Exception as exc:  # noqa: BLE001
            fallidas.append((cat, str(exc)[:80]))
            time.sleep(5.0)
            continue
        if not attrs:
            fallidas.append((cat, "sin respuesta"))
            time.sleep(5.0)
            continue

        req = [a for a in attrs if (a.get("tags") or {}).get("required")]
        if not req:
            sin_obligatorios += 1
        filas = []
        for a in req:
            vals = [v.get("name") for v in (a.get("values") or []) if v.get("name")]
            filas.append({
                "campo": a["id"],
                # `atributos` dice DÓNDE buscar; `campo` (BRAND, MODEL…) dice
                # QUÉ buscar. El semáforo mira dentro de la lista por el
                # `nombre` de cada entrada, no la mera presencia de la llave —
                # si no, se pondría verde con cualquier atributo aunque faltara
                # el obligatorio. Ver channel_content._faltantes_sync.
                "campo_canonico": "atributos",
                "obligatorio": True,
                "tipo": a.get("value_type"),
                "valores_permitidos": json.dumps(vals[:200], ensure_ascii=False) if vals else None,
                # BRAND lo fija el publicador para todo el catálogo.
                "default_value": '"Ferrahome"' if a["id"] == "BRAND" else None,
                "fuente": "api",
            })
        if filas and args.aplicar:
            guardar(dsn, cat, filas)
        total += len(filas)
        print(f"   {cat:<14} {len(attrs):>3} atributos · {len(req)} obligatorios "
              f"({n} publicaciones)", flush=True)
        time.sleep(0.6)

    print()
    print("=" * 70)
    print(f"Atributos obligatorios cargados: {total}"
          + ("" if args.aplicar else "  (ENSAYO — no se escribió)"))
    print(f"Categorías SIN atributos obligatorios: {sin_obligatorios} de {len(cats)}")
    if fallidas:
        print(f"\nCategorías que fallaron ({len(fallidas)}):")
        for c, m in fallidas[:10]:
            print(f"   {c}: {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
