"""
cargar_requisitos_amazon.py — llena `channel.field_requirements` preguntándole
a Amazon qué exige cada productType.

QUÉ HACE
--------
Por cada tipo: pide su JSON Schema a SP-API Definitions, y convierte cada campo
en una fila — nombre nativo, nombre canónico si hay equivalencia, si es
obligatorio, y el sello `leido_at`.

POR QUÉ SE LEE DEL CANAL Y NO SE ESCRIBE A MANO
-----------------------------------------------
Es la regla que este trabajo lleva días aplicando: el esquema publicado de
Walmart dice 3.19 y producción corre 3.11; la doc decía `WALMART_MX` y el
sistema quería `WALMART_MEXICO`. Un requisito escrito de memoria es una mentira
esperando a que alguien la crea.

ALCANCE: LOS N TIPOS MÁS USADOS, NO LOS 558
--------------------------------------------
El catálogo tiene 558 productTypes distintos en `amazon_progress`, casi todos
con uno o dos productos. Bajar los 558 son 558 llamadas a una API que ya nos
cortó por exceso de peticiones en Walmart (19 de 24 productos caídos, ver
estado_walmart.py). Se empieza por los más usados —los 12 principales cubren
~290 publicaciones— y la cola larga entra después o bajo demanda.

Uso:
    # Ensayo (no escribe), destino sandbox:
    python -m scripts.cargar_requisitos_amazon

    # De verdad, contra el sandbox:
    python -m scripts.cargar_requisitos_amazon --aplicar --destino sandbox

    # Con las credenciales de Railway sin copiarlas a la máquina:
    railway run --service BackendOmnicanal python -m scripts.cargar_requisitos_amazon --aplicar --destino sandbox
"""
from __future__ import annotations

import argparse
import asyncio
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

# ── Mapeo NATIVO -> CANÓNICO ────────────────────────────────────────────────
# Solo lo que es inequívoco. Lo que no está aquí se guarda con
# `campo_canonico = NULL`: es un campo propio de Amazon que el panel no edita
# (`condition_type`, `supplier_declared_dg_hz_regulation`…), y eso está bien.
#
# NO se mapea a ciegas. `item_length_width_height` queda FUERA a propósito: un
# solo atributo de Amazon cubre tres canónicos nuestros (largo/ancho/alto) y el
# modelo guarda un canónico por fila. Inventar una correspondencia ahí sería
# exactamente el tipo de suposición que este cargador existe para evitar; el
# script lo reporta al final para que se decida con el dato enfrente.
CANONICO: dict[str, str] = {
    "item_name":                    "titulo",
    "product_description":          "descripcion",
    "bullet_point":                 "bullets",
    "fulfillment_availability":     "stock",
    "main_product_image_locator":   "imagenes",
    "externally_assigned_product_identifier": "sku",
    # Entró tras la primera corrida: el reporte lo listó como obligatorio sin
    # equivalente. Sale del producto (`_attr_from(atributos,"BRAND","Generic")`),
    # no es constante, y no tenía dónde editarse.
    "brand":                        "brand",
}

# Constantes que el publicador ya pone sin preguntar (verificadas en
# services/publicar.py:_amazon_attributes y _AMZ_DEFAULTS). Son las que evitan
# que el panel pinte en rojo ~7 campos que SIEMPRE se llenan solos.
DEFAULTS: dict[str, object] = {
    "condition_type": "new_new",
    "country_of_origin": "MX",
    "included_components": "1 x Producto",
    "warranty_description": "Garantía del vendedor",
    "supplier_declared_dg_hz_regulation": "not_applicable",
    "number_of_items": 1,
    "supplier_declared_has_product_identifier_exemption": True,
}


def _env_destino(destino: str) -> str:
    """DSN del destino. `env.staging` para sandbox, `.env` para producción."""
    archivo = ROOT / ("env.staging" if destino == "sandbox" else ".env")
    vals: dict[str, str] = {}
    if archivo.exists():
        for line in io.open(archivo, encoding="utf-8"):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    dsn = os.environ.get("SUPABASE_DB_URL") if destino == "prod" else ""
    dsn = dsn or vals.get("SUPABASE_DB_URL", "")
    if not dsn:
        sys.exit(f"ABORT: no hay SUPABASE_DB_URL para destino={destino}.")
    # Candado: el destino declarado tiene que coincidir con la ref real.
    if destino == "sandbox" and "yvootpbz" not in dsn:
        sys.exit("ABORT: --destino sandbox pero la DSN no es del sandbox.")
    if destino == "prod" and "tukwcvsi" not in dsn:
        sys.exit("ABORT: --destino prod pero la DSN no es de producción.")
    return dsn


def tipos_mas_usados(limite: int) -> list[tuple[str, int]]:
    """
    Los productTypes con más publicaciones vivas, leídos de `channel.listings`.

    NO de `amazon_progress`: esa tabla de MySQL quedó CONGELADA al cerrarse la
    migración el 12-ago (ver CLAUDE.md). Sigue ahí con su último valor bueno,
    así que un SELECT contra ella devuelve el pasado sin decir que lo es.
    Se nota en los conteos: ARTIFICIAL_PLANT sale 43 en MySQL y 41 en
    `channel.listings`, que es la gemela viva.

    Se lee SIEMPRE de producción, aunque se escriba al sandbox: el catálogo real
    está allá, y el sandbox puede estar recién recreado y vacío.
    """
    import psycopg2
    dsn_prod = _env_destino("prod")
    cx = psycopg2.connect(dsn_prod, connect_timeout=20)
    try:
        cx.set_session(readonly=True, autocommit=True)
        with cx.cursor() as cur:
            cur.execute(
                """select product_type, count(*) n from channel.listings
                    where product_type is not null and product_type <> ''
                    group by 1 order by 2 desc limit %s""", (limite,))
            return [(t, n) for t, n in cur.fetchall()]
    finally:
        cx.close()


async def requisitos_de(tipo: str) -> tuple[list[dict], str | None]:
    """(filas, motivo_de_fallo). Una fila por campo del esquema de ese tipo."""
    from config import settings
    from services import amazon, publicar

    token = await amazon._access_token()  # noqa: SLF001
    if not token:
        return [], "sin token de SP-API"
    schema = await publicar._amazon_schema(token, tipo, settings.amazon_marketplace_id)  # noqa: SLF001
    if not schema:
        return [], "el esquema no se pudo bajar"

    props: dict = schema["properties"]
    requeridos = set(schema["required"])
    filas = []
    for campo, nodo in props.items():
        enum = nodo.get("enum") or (nodo.get("items") or {}).get("enum")
        filas.append({
            "campo": campo,
            "campo_canonico": CANONICO.get(campo),
            "obligatorio": campo in requeridos,
            "tipo": nodo.get("type"),
            "valores_permitidos": json.dumps(enum, ensure_ascii=False) if enum else None,
            "default_value": (json.dumps(DEFAULTS[campo], ensure_ascii=False)
                              if campo in DEFAULTS else None),
            "fuente": "codigo" if campo in DEFAULTS else "api",
        })
    return filas, None


def guardar(dsn: str, tipo: str, filas: list[dict]) -> int:
    """Upsert por (canal, categoria_id, campo). Re-correr no duplica."""
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
                [("amazon", tipo, f["campo"], f["campo_canonico"], f["obligatorio"],
                  f["tipo"], f["valores_permitidos"], f["default_value"], f["fuente"])
                 for f in filas],
                template="(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,now(),now())",
                page_size=500,
            )
        cx.commit()
    finally:
        cx.close()
    return len(filas)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="sin esto NO escribe")
    ap.add_argument("--destino", choices=["sandbox", "prod"], default="sandbox")
    ap.add_argument("--limite", type=int, default=12, help="cuántos tipos (default 12)")
    args = ap.parse_args()

    dsn = _env_destino(args.destino)
    print(f"DESTINO: {args.destino}   ({'ENSAYO' if not args.aplicar else 'ESCRIBIENDO'})\n")

    tipos = tipos_mas_usados(args.limite)
    print(f"Tipos a cargar: {len(tipos)}")
    for t, n in tipos:
        print(f"   {t:<32} {n} publicaciones")
    print()

    total, sin_canonico, fallidos = 0, set(), []
    for tipo, _n in tipos:
        filas, motivo = await requisitos_de(tipo)
        if motivo:
            fallidos.append((tipo, motivo))
            print(f"   {tipo:<32} FALLÓ: {motivo}", flush=True)
            continue
        obl = sum(1 for f in filas if f["obligatorio"])
        con_def = sum(1 for f in filas if f["default_value"] is not None)
        if args.aplicar:
            guardar(dsn, tipo, filas)
        total += len(filas)
        for f in filas:
            if f["obligatorio"] and not f["campo_canonico"]:
                sin_canonico.add(f["campo"])
        print(f"   {tipo:<32} {len(filas):>3} campos · {obl:>2} obligatorios · "
              f"{con_def} con respaldo", flush=True)
        await asyncio.sleep(1.0)   # Amazon corta si se le pega seguido

    print()
    print("=" * 70)
    print(f"Campos procesados: {total}" + ("" if args.aplicar else "  (ENSAYO — no se escribió)"))
    if fallidos:
        print(f"\nTipos que fallaron ({len(fallidos)}):")
        for t, m in fallidos:
            print(f"   {t}: {m}")
    if sin_canonico:
        # Lo más útil del reporte: obligatorios que el panel NO sabe llenar
        # porque no tienen equivalente en nuestro vocabulario.
        print(f"\nOBLIGATORIOS SIN EQUIVALENTE CANÓNICO ({len(sin_canonico)}):")
        print("   Son los que el panel no puede pedirle a nadie. Si alguno debería")
        print("   ser editable, hay que sumarlo a core.canonical_fields y a CANONICO.")
        for c in sorted(sin_canonico):
            marca = "  (lo pone el publicador)" if c in DEFAULTS else ""
            print(f"   · {c}{marca}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
