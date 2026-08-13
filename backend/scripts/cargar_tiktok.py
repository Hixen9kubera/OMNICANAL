"""
cargar_tiktok.py — Abre el canal TikTok en el panel: cuenta, censo, árbol de
categorías y requisitos.

QUÉ HACE
--------
Toma la entrega del chat de TikTok (cuatro CSV medidos en vivo el 13-ago-2026) y
la deja en las tablas que el panel YA lee para Mercado Libre y Amazon:

    core.accounts              la cuenta KUBERA (sin ella, listings no tiene FK)
    core.channels.is_active    el flag rancio que decía false con la tienda viva
    channel.categories         2,168 categorías
    channel.field_requirements 1,779 requisitos
    channel.listings           900 publicaciones

POR QUÉ CSV Y NO LA API
-----------------------
El publicador de TikTok vive en otro hilo y tiene las credenciales, la firma
HMAC y la allowlist de IPs resueltas. Duplicar aquí ese cliente para releer lo
mismo sería una segunda verdad que se desincroniza. La entrega trae el sello de
cuándo se leyó (`leido_at`), que es lo que importa para el semáforo.

⚠️ NO ESCRIBE `canal_inventario` (MySQL), A PROPÓSITO. Desde el 13-ago los
espejos inversos están apagados y kubera es la fuente. Sembrar un canal nuevo en
una tabla congelada sería crear una foto que nadie va a actualizar — el error
que ya costó 964 pedidos fantasma.

Uso:
    # Ensayo (no escribe), contra producción:
    python -m scripts.cargar_tiktok

    # De verdad:
    python -m scripts.cargar_tiktok --aplicar --destino prod
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parent.parent.parent

ENTREGA = Path(r"C:\Users\diaz2\OneDrive\Escritorio\respaldo_tiktok_20260813")

CANAL = "tiktok"
CUENTA = "KUBERA"


def _dsn(destino: str) -> str:
    archivo = ROOT / ("env.staging" if destino == "sandbox" else ".env")
    vals: dict[str, str] = {}
    if archivo.exists():
        for line in io.open(archivo, encoding="utf-8"):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    dsn = (os.environ.get("SUPABASE_DB_URL") if destino == "prod" else "") or vals.get("SUPABASE_DB_URL", "")
    if not dsn:
        sys.exit(f"ABORT: no hay SUPABASE_DB_URL para destino={destino}.")
    # Guardia triple, como los ETLs: el destino declarado tiene que coincidir.
    if destino == "sandbox" and "yvootpbz" not in dsn:
        sys.exit("ABORT: --destino sandbox pero la DSN no es del sandbox.")
    if destino == "prod" and "tukwcvsi" not in dsn:
        sys.exit("ABORT: --destino prod pero la DSN no es de producción.")
    return dsn


def _leer(nombre: str) -> list[dict[str, str]]:
    ruta = ENTREGA / nombre
    if not ruta.exists():
        sys.exit(f"ABORT: falta {ruta}")
    # utf-8-sig: los CSV vienen con BOM y sin esto la PRIMERA columna se llama
    # '\ufeffcanal' y no la encuentra nadie.
    with io.open(ruta, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("true", "1", "sí", "si", "yes")


def _num(v: str):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _int(v: str):
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


# El diccionario canónico (`core.canonical_fields`) tiene FK desde
# `field_requirements.campo_canonico`, así que un nombre que no exista ahí TUMBA
# la carga entera. La entrega trae dos que no están, y cada uno se resuelve
# distinto:
#
#   precio      → `precio_regular`. Es el mismo concepto con otro nombre, y el
#                 diccionario se sembró desde el código (`CamposPublicar`), que
#                 dice `precio_regular`. Manda el código.
#   marca       → `brand`. Igual: el diccionario lo tiene en inglés porque así
#                 se llama el campo en el panel y en el payload de Amazon.
#   dimensiones → NULL. `package_dimension` de TikTok cubre TRES canónicos
#                 (largo/ancho/alto) y el modelo guarda uno por fila. Es
#                 exactamente el caso que el cargador de Amazon dejó sin mapear
#                 a propósito con `item_length_width_height`: inventar la
#                 correspondencia sería la suposición que estos cargadores
#                 existen para evitar. El requisito se guarda igual, sin
#                 canónico — y "sin canónico" ya significa "nadie puede
#                 llenarlo" en el semáforo, que es la verdad.
#
# Se contrastaron los ONCE valores distintos de la entrega contra el diccionario
# de una vez, en vez de ir descubriéndolos por FK una a una: tres no existían.
_CANONICO = {"precio": "precio_regular", "marca": "brand", "dimensiones": None}


def _valores(texto: str):
    """
    `valores_permitidos` es jsonb y la entrega trae TEXTO legible
    ("Sí | No", "1-300 caracteres"). Se guarda como array cuando es una lista y
    como cadena cuando es una descripción.

    ⚠️ Son los NOMBRES, no los IDs. TikTok exige ID de atributo Y de valor al
    publicar, y ese mapeo NO está en esta tabla: lo lee en vivo
    `tiktok_atributos.build_prompt`. Aquí sirve para que un humano revise, no
    para armar el payload.
    """
    t = (texto or "").strip()
    if not t:
        return None
    if " | " in t:
        return json.dumps([x.strip() for x in t.split("|") if x.strip()], ensure_ascii=False)
    return json.dumps(t, ensure_ascii=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="escribe (por omisión: ensayo)")
    ap.add_argument("--destino", choices=("prod", "sandbox"), default="prod")
    args = ap.parse_args()

    censo = _leer("TIKTOK_CENSO_LISTINGS.csv")
    cats = _leer("TIKTOK_CATEGORIAS.csv")
    reqs = _leer("TIKTOK_FIELD_REQUIREMENTS.csv")

    print(f"Entrega: {len(censo)} publicaciones · {len(cats)} categorías · "
          f"{len(reqs)} requisitos")

    # La columna del permiso NO se puede verificar desde otra máquina (los CSV
    # viven en el escritorio de quien hizo la entrega), así que se comprueba en
    # tiempo de corrida en vez de suponerla. Sin esto, un `.get()` devolvería
    # None y `disponibilidad` quedaría en NULL para las 2,168 filas SIN QUE
    # NADIE SE ENTERE — que es justo el modo de fallo que estas columnas
    # existen para evitar: una categoría INVITE_ONLY que nadie marca acepta el
    # producto y lo deja en PENDING para siempre.
    if cats and "permission_status" not in cats[0]:
        sys.exit(
            "ABORT: TIKTOK_CATEGORIAS.csv no trae la columna `permission_status`.\n"
            f"       Columnas que sí trae: {', '.join(cats[0].keys())}\n"
            "       Ajusta el nombre en este script — NO se puede seguir: "
            "`disponibilidad` quedaría en NULL y el semáforo no bloquearía las "
            "categorías restringidas."
        )
    if not args.aplicar:
        print("ENSAYO — no se escribe nada. Agrega --aplicar para hacerlo.")

    import psycopg2
    from psycopg2.extras import execute_values

    cx = psycopg2.connect(_dsn(args.destino), connect_timeout=10)
    cur = cx.cursor()

    # ── 1) La cuenta y el canal ──────────────────────────────────────────────
    shop_id = next((r["shop_id"] for r in censo if r.get("shop_id")), None)
    cur.execute("select id from core.accounts where channel_id=%s and legacy_code=%s",
                (CANAL, CUENTA))
    fila = cur.fetchone()
    if fila:
        cuenta_id = fila[0]
        print(f"  cuenta {CUENTA}: ya existía ({cuenta_id})")
    elif args.aplicar:
        cur.execute(
            """insert into core.accounts (channel_id, legacy_code, external_id, label, is_active)
               values (%s,%s,%s,%s,true) returning id""",
            (CANAL, CUENTA, shop_id, CUENTA))
        cuenta_id = cur.fetchone()[0]
        print(f"  cuenta {CUENTA}: creada ({cuenta_id}) · shop_id {shop_id}")
    else:
        cuenta_id = None
        print(f"  cuenta {CUENTA}: se crearía (shop_id {shop_id})")

    if args.aplicar:
        # El flag estaba en false con la tienda publicando desde julio: es el
        # dato rancio que hacía que el panel dijera "pendiente de credenciales".
        cur.execute("update core.channels set is_active=true where id=%s", (CANAL,))
        print(f"  core.channels.{CANAL}.is_active → true")

    # ── 2) Árbol de categorías ───────────────────────────────────────────────
    # root_id/root_name se CALCULAN subiendo por parent_id hasta nivel 1: son
    # columnas de la tabla y ML las usa; sin ellas la ruta habría que parsearla,
    # y `path` mezcla dos separadores distintos (› y >) — no se parsea.
    por_id = {c["categoria_id"]: c for c in cats}

    def raiz(cid: str) -> tuple[str | None, str | None]:
        visto = set()
        actual = por_id.get(cid)
        while actual and actual.get("parent_id") not in ("0", "", None):
            if actual["categoria_id"] in visto:      # ciclo: no debería, pero
                break                                 # un bucle aquí cuelga todo
            visto.add(actual["categoria_id"])
            siguiente = por_id.get(actual["parent_id"])
            if not siguiente:
                break
            actual = siguiente
        return (actual["categoria_id"], actual["nombre"]) if actual else (None, None)

    filas_cat = []
    for c in cats:
        rid, rnom = raiz(c["categoria_id"])
        filas_cat.append((CANAL, c["categoria_id"], c["nombre"], c["ruta"],
                          (c["parent_id"] if c["parent_id"] != "0" else None), rid, rnom,
                          # `is_leaf` y `disponibilidad` entran desde la 0019.
                          # `permission_status` se guarda VERBATIM: es el valor
                          # nativo de TikTok y aplastarlo a un boolean perdería
                          # el porqué — hay categorías bloqueadas por no ser hoja
                          # y otras por ser INVITE_ONLY, y son problemas
                          # distintos. `publicable` NO se guarda: se deriva con
                          # `is_leaf and disponibilidad = 'AVAILABLE'`.
                          _bool(c["is_leaf"]),
                          (c.get("permission_status") or None)))
    hojas = sum(1 for c in cats if _bool(c["is_leaf"]))
    publicables = sum(1 for c in cats if _bool(c["publicable"]))
    restringidas = sum(1 for c in cats
                       if (c.get("permission_status") or "") == "INVITE_ONLY")
    print(f"  categorías: {len(filas_cat)} · {hojas} hojas · {publicables} publicables "
          f"· {restringidas} restringidas (INVITE_ONLY)")
    if args.aplicar:
        execute_values(cur, """
            insert into channel.categories
                (channel_id, category_id, name, path, parent_id, root_id, root_name,
                 is_leaf, disponibilidad)
            values %s
            on conflict (channel_id, category_id) do update set
                name=excluded.name, path=excluded.path, parent_id=excluded.parent_id,
                root_id=excluded.root_id, root_name=excluded.root_name,
                is_leaf=excluded.is_leaf, disponibilidad=excluded.disponibilidad""",
            filas_cat, page_size=500)

    # ── 3) Requisitos ────────────────────────────────────────────────────────
    filas_req = [(r["canal"], r["categoria_id"], r["campo"],
                  _CANONICO.get(r["campo_canonico"], r["campo_canonico"]) or None,
                  _bool(r["obligatorio"]), r["tipo"] or None,
                  _valores(r["valores_permitidos"]),
                  json.dumps(r["default_value"], ensure_ascii=False) if r["default_value"] else None,
                  r["fuente"] or "api", r["leido_at"] or None)
                 for r in reqs]
    oblig = sum(1 for r in reqs if _bool(r["obligatorio"]))
    print(f"  requisitos: {len(filas_req)} ({oblig} obligatorios) en "
          f"{len({r['categoria_id'] for r in reqs})} categorías")
    if args.aplicar:
        execute_values(cur, """
            insert into channel.field_requirements
                (canal, categoria_id, campo, campo_canonico, obligatorio, tipo,
                 valores_permitidos, default_value, fuente, leido_at)
            values %s
            on conflict (canal, categoria_id, campo) do update set
                campo_canonico=excluded.campo_canonico, obligatorio=excluded.obligatorio,
                tipo=excluded.tipo, valores_permitidos=excluded.valores_permitidos,
                default_value=excluded.default_value, fuente=excluded.fuente,
                leido_at=excluded.leido_at, updated_at=now()""",
            filas_req, page_size=500)

    # ── 4) El censo → channel.listings ───────────────────────────────────────
    # `situacion` lleva el estado de AUDITORÍA y `status` el del producto: son
    # dos cosas distintas en TikTok (un ACTIVATE con audit FAILED existe) y
    # aplastarlas en una columna perdería justo la que explica por qué algo no
    # se vende.
    #
    # is_fulfillment=false: el stock sale de NUESTRA bodega. El warehouse_id de
    # TikTok es dónde lo recogen, no quién lo guarda.
    filas_lst = []
    for r in censo:
        filas_lst.append((
            r["sku"], cuenta_id, CANAL, r["product_id"], r["url"] or None,
            r["status"] or None, r["audit_status"] or None,
            _num(r["precio"]), _int(r["stock"]), False,
            r["category_id"] or None, r["moneda"] or "MXN", CUENTA,
        ))
    print(f"  publicaciones: {len(filas_lst)} · "
          f"{sum(1 for r in censo if r['status']=='ACTIVATE')} ACTIVATE · "
          f"{sum(1 for r in censo if r['status']=='DRAFT')} DRAFT")
    if args.aplicar and cuenta_id:
        execute_values(cur, """
            insert into channel.listings
                (sku, account_id, canal, listing_id, url, status, situacion,
                 price, stock_own, is_fulfillment, category_id, currency,
                 store_name, updated_at)
            values %s
            on conflict (sku, account_id, canal) do update set
                listing_id=excluded.listing_id, url=excluded.url,
                status=excluded.status, situacion=excluded.situacion,
                price=excluded.price, stock_own=excluded.stock_own,
                category_id=excluded.category_id, currency=excluded.currency,
                store_name=excluded.store_name, updated_at=now()""",
            filas_lst, page_size=300,
            template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())")

    if args.aplicar:
        cx.commit()
        print("\nAPLICADO.")
    else:
        cx.rollback()
        print("\nEnsayo terminado (rollback).")
    cx.close()


if __name__ == "__main__":
    main()
