"""
cargar_locations_tex2.py — La foto de las ubicaciones de TEXCO II, de Odoo a
`almacen.locations` (migración 0069).

Pedido de Brandon (9-oct-2026): TEXCO II deja de operarse desde Odoo y pasa a
kubera. La ubicación de rack sólo existe en Odoo; aquí se copia, completa.

QUÉ TRAE
  Todos los renglones de existencia (`stock.quant`, cantidad ≠ 0) que cuelgan
  del almacén TEX2 de Odoo. Un renglón de Odoo = un renglón de la tabla.

  · cedis      siempre TEX2 («TEXCO II» en el catálogo de bodegas).
  · ubicacion  como la pinta el panel (`services.odoo._rack`):
                 TEX2/FERRAFORME/BLOQUE D/FILA 1/T6 → BLOQUE D-FILA 1-T6
                 TEX2/FERRAFORME/REQUERIMENTOS      → REQUERIMENTOS
                 TEX2/FERRAFORME                    → SIN UBICAR
                 TEX2/Zona de empaquetado           → Zona de empaquetado
  · piezas     la cantidad de Odoo, con su signo. INFORMATIVA (ver la 0069).

LOS NEGATIVOS SE TRAEN (medido el 9-oct: 21 renglones, 20 SKUs)
  Los 21 salen de movimientos HECHOS en Odoo, no de capturas sueltas:
    · 11 en «FERRAFORME Archivar»: surtidos (TEX2/PICK) del 23 al 30-jul que
      sacaron de una ubicación que nunca tuvo mercancía, mientras las piezas
      siguen contadas en su rack.
    ·  7 en la raíz por la «Adecuación para alta de producto [revertido]» del
      18-jul: repartió a los racks más de lo que había.
    ·  3 por surtir o traspasar de más (TEC-2185-BLN, ACC-0574-LIL, ACC-0886-NEG).
  El negativo es lo que hace que la suma del SKU cuadre con Odoo. Quitarlo
  dejaría a la vista piezas que ya no están.

LO RARO QUE TAMBIÉN SE TRAE, MARCADO
  · Productos ARCHIVADOS en Odoo con existencia (24): `archivado_odoo = true`.
    21 de ellos son el gemelo viejo de un SKU que tiene otro producto activo: el
    archivado conserva el rack y el activo lleva la cuenta. Sumarlos duplica.
  · Productos SIN SKU cuyo nombre ES el SKU (3): se usa el nombre.

CANDADOS
  · Sin `--aplicar` no escribe (la transacción se abre de sólo lectura).
  · No deja una transacción abierta mientras lee Odoo: revisa, suelta la
    conexión, lee Odoo, y escribe en una transacción corta que revisa otra vez.
  · No corre si TEXCO II ya es de kubera (`almacenes.fuente <> 'odoo'`) ni si
    alguien ya capturó renglones a mano en ese cedis: desde ese día Odoo deja de
    ser la verdad y esta foto pisaría el trabajo de bodega.
  · En Odoo sólo `search_read`.

Uso:
  python backend/scripts/cargar_locations_tex2.py                       # ensayo en el sandbox
  python backend/scripts/cargar_locations_tex2.py --aplicar             # escribe en el sandbox
  python backend/scripts/cargar_locations_tex2.py --destino prod --aplicar
"""
from __future__ import annotations

import argparse
import io
import os
import re
import socket
import sys
import threading
import xmlrpc.client
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

CEDIS = "TEX2"
SIN_UBICAR = "SIN UBICAR"
NAVE = "FERRAFORME"
WATCHDOG_S = 600
MINIMO = 1_000       # el 9-oct-2026 son 1,663 renglones; menos de mil es una lectura trunca
CAIDA_MAX = 0.20

_SKU = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)+$")
_RACK = re.compile(r"^BLOQUE (\S+)-FILA (\S+)-(\S+)$")

_COLUMNAS = ("cedis, sku, ubicacion, bloque, fila, tarima, piezas, origen, archivado_odoo, "
             "odoo_quant_id, odoo_product_id, odoo_ubicacion, entrada_at")
_INSERT = f"insert into almacen.locations ({_COLUMNAS}) values %s"
_PLANTILLA = "(%s,%s,%s,%s,%s,%s,%s,'odoo',%s,%s,%s,%s,%s)"


def ubicacion_de(completo: str) -> tuple[str, str | None, str | None, str | None]:
    """La ruta de Odoo → (ubicacion, bloque, fila, tarima).

    El rack se escribe como lo pinta el panel (`services.odoo._rack`), para que
    la tabla y el Catálogo Maestro digan lo mismo. La raíz de la nave no es una
    posición: es «SIN UBICAR». Lo que cuelga del almacén fuera de la nave
    («Zona de empaquetado», «FERRAFORME Archivar») conserva su nombre: no es un
    rack, pero tampoco es lo mismo que no tener lugar.
    """
    from services.odoo import _rack

    partes = [p.strip() for p in (completo or "").split("/") if p.strip()]
    if len(partes) <= 1 or partes[1:] == [NAVE]:
        return SIN_UBICAR, None, None, None
    if len(partes) == 2:
        return partes[1], None, None, None
    rack = _rack(completo, "")
    m = _RACK.match(rack)
    return (rack, m.group(1), m.group(2), m.group(3)) if m else (rack, None, None, None)


def filas_desde_odoo(quants: list[dict], productos: dict[int, dict]) -> tuple[list[tuple], dict]:
    """Los renglones de Odoo → filas de la tabla. Función pura (la prueba la usa).

    Revienta —no adivina— ante lo que haría una fila falsa: cantidad con
    decimales, un producto sin SKU cuyo nombre no es un SKU, o dos productos
    ACTIVOS del mismo SKU en la misma ubicación.
    """
    filas: list[tuple] = []
    stats: Counter = Counter()
    problemas: list[str] = []
    vistos: dict[tuple[str, str], int] = {}
    for q in quants:
        pid = q["product_id"][0]
        p = productos.get(pid)
        if p is None:
            problemas.append(f"quant {q['id']}: Odoo no devolvió el producto {pid}")
            continue
        sku = (p.get("default_code") or "").strip()
        if not sku:
            sku = (p.get("name") or "").strip()
            if not _SKU.match(sku):
                problemas.append(f"producto {pid} sin SKU y su nombre no es un SKU: {sku!r}")
                continue
            stats["sin_sku_en_odoo"] += 1
        cantidad = q["quantity"]
        if cantidad != int(cantidad):
            problemas.append(f"{sku}: cantidad con decimales ({cantidad})")
            continue
        completo = q["location_id"][1]
        ubicacion, bloque, fila, tarima = ubicacion_de(completo)
        archivado = not p.get("active", True)
        if not archivado:
            clave = (sku.lower(), ubicacion)
            if clave in vistos:
                problemas.append(f"{sku}: dos productos activos ({vistos[clave]} y {pid}) "
                                 f"en la misma ubicación {ubicacion!r}")
                continue
            vistos[clave] = pid
        entrada = None
        if q.get("in_date"):
            entrada = datetime.strptime(q["in_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        filas.append((CEDIS, sku, ubicacion, bloque, fila, tarima, int(cantidad), archivado,
                      q["id"], pid, completo, entrada))
        stats["renglones"] += 1
        stats["piezas"] += int(cantidad)
        stats["archivados"] += archivado
        stats["negativos"] += cantidad < 0
        stats["sin_ubicar"] += ubicacion == SIN_UBICAR
        stats["en_rack"] += bloque is not None
    if problemas:
        raise ValueError("no se carga nada:\n  " + "\n  ".join(problemas[:30]))
    stats["skus"] = len({f[1].lower() for f in filas})
    return filas, dict(stats)


def leer_odoo(odoo_warehouse_id: int) -> tuple[list[dict], dict[int, dict]]:
    """Sólo `search_read`. El almacén se busca por su CÓDIGO y se cruza con el
    id que guarda kubera: si no coinciden, el catálogo de bodegas apunta a otra."""
    from config import settings

    comun = xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/common")
    uid = comun.authenticate(settings.odoo_db, settings.odoo_user, settings.odoo_password, {})
    if not uid:
        sys.exit("ABORT: Odoo rechazó la autenticación.")
    modelos = xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/object")

    def leer(modelo: str, dominio: list, campos: list[str]) -> list[dict]:
        return modelos.execute_kw(settings.odoo_db, uid, settings.odoo_password, modelo,
                                  "search_read", [dominio], {"fields": campos})

    almacenes = leer("stock.warehouse", [["code", "=", CEDIS]], ["id", "name", "view_location_id"])
    if len(almacenes) != 1:
        sys.exit(f"ABORT: Odoo tiene {len(almacenes)} almacenes con código {CEDIS}.")
    if almacenes[0]["id"] != odoo_warehouse_id:
        sys.exit(f"ABORT: {CEDIS} es el almacén {almacenes[0]['id']} en Odoo y "
                 f"{odoo_warehouse_id} en el catálogo de bodegas de kubera.")
    vista = almacenes[0]["view_location_id"][0]
    quants = leer("stock.quant", [["location_id", "child_of", vista], ["quantity", "!=", 0]],
                  ["product_id", "location_id", "quantity", "in_date"])
    ids = sorted({q["product_id"][0] for q in quants})
    productos: dict[int, dict] = {}
    for i in range(0, len(ids), 500):
        # `active in [True, False]`: sin eso Odoo calla los archivados y sus
        # renglones se quedarían sin SKU.
        for p in leer("product.product", [["id", "in", ids[i:i + 500]],
                                          ["active", "in", [True, False]]],
                      ["default_code", "name", "active"]):
            productos[p["id"]] = p
    return quants, productos


def _env_destino(destino: str) -> str:
    """DSN del destino: `SUPABASE_DB_URL` del proceso o, si no está, env.staging /
    .env de la raíz. Venga de donde venga, se valida el proyecto."""
    archivo = ROOT / ("env.staging" if destino == "sandbox" else ".env")
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn and archivo.exists():
        for line in io.open(archivo, encoding="utf-8"):
            s = line.strip()
            if s.startswith("SUPABASE_DB_URL="):
                dsn = s.partition("=")[2].split("#")[0].strip().strip('"').strip("'")
    if not dsn:
        sys.exit(f"ABORT: no hay SUPABASE_DB_URL para destino={destino} "
                 f"(ni en el entorno ni en {archivo.name}).")
    if destino == "sandbox" and "yvootpbz" not in dsn:
        sys.exit("ABORT: --destino sandbox pero la DSN no es del sandbox.")
    if destino == "prod" and "tukwcvsi" not in dsn:
        sys.exit("ABORT: --destino prod pero la DSN no es de producción.")
    if destino == "local" and not re.search(r"@(127\.0\.0\.1|localhost)[:/]", dsn):
        sys.exit("ABORT: --destino local pero la DSN no es de esta máquina.")
    return dsn


class _Abortar(Exception):
    """Algo que tiene que ser cierto para cargar, no lo es."""


def _guardas(cur) -> tuple[str, int, int]:
    """Lo que tiene que ser cierto para cargar → (nombre, id del almacén en Odoo,
    renglones de Odoo que hay hoy). Se revisa DOS veces: antes de leer Odoo y otra
    vez dentro de la transacción que escribe, por si algo cambió entre tanto."""
    cur.execute("select to_regclass('almacen.locations') is not null, "
                "coalesce(to_regclass('almacen.almacenes'), to_regclass('ops.almacenes'))::text")
    hay_tabla, catalogo = cur.fetchone()
    if not hay_tabla or not catalogo:
        raise _Abortar("no existe almacen.locations o el catálogo de bodegas "
                       "(falta aplicar la migración 0069).")
    cur.execute(f"select nombre, fuente, odoo_warehouse_id from {catalogo} where codigo = %s",
                (CEDIS,))
    bodega = cur.fetchone()
    if not bodega:
        raise _Abortar(f"el catálogo de bodegas no tiene {CEDIS}.")
    nombre, fuente, odoo_wh = bodega
    if fuente != "odoo":
        raise _Abortar(f"{nombre} ya es de {fuente}: Odoo dejó de ser su verdad y esta foto "
                       "pisaría lo que bodega capturó aquí.")
    cur.execute("select count(*) filter (where origen = 'odoo'), "
                "count(*) filter (where origen <> 'odoo') "
                "from almacen.locations where cedis = %s", (CEDIS,))
    actual, a_mano = cur.fetchone()
    if a_mano:
        raise _Abortar(f"{nombre} ya tiene {a_mano} renglón(es) capturados en kubera; "
                       "no se recarga encima.")
    return nombre, odoo_wh, actual


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="sin esto NO escribe")
    ap.add_argument("--destino", choices=["sandbox", "prod", "local"], default="sandbox")
    args = ap.parse_args()

    perro = threading.Timer(WATCHDOG_S, lambda: os._exit(3))
    perro.daemon = True  # no retiene el proceso si main() termina o revienta
    perro.start()
    socket.setdefaulttimeout(240)

    dsn = _env_destino(args.destino)

    # 1) Las guardas, en una lectura corta. La conexión se SUELTA antes de leer
    #    Odoo: esa lectura tarda decenas de segundos y una transacción abierta todo
    #    ese rato ocupa un lugar del pool y estorba a quien quiera migrar la tabla.
    cx = psycopg2.connect(dsn, connect_timeout=20)
    try:
        with cx.cursor() as cur:
            cur.execute("set transaction read only")
            nombre, odoo_wh, _ = _guardas(cur)
        cx.rollback()
    except _Abortar as exc:
        print(f"ABORT ({args.destino}): {exc}")
        return 2
    finally:
        cx.close()

    # 2) Odoo, sin ninguna conexión a la base abierta.
    quants, productos = leer_odoo(odoo_wh)
    try:
        filas, s = filas_desde_odoo(quants, productos)
    except ValueError as exc:
        print(f"ABORT: {exc}")
        return 2
    print(f"Odoo · {nombre}: {s['renglones']:,} renglones · {s['skus']:,} SKUs · "
          f"{s['piezas']:,} piezas")
    print(f"  en rack {s.get('en_rack', 0):,} · SIN UBICAR {s.get('sin_ubicar', 0):,} · "
          f"negativos {s.get('negativos', 0)} · de producto archivado "
          f"{s.get('archivados', 0)} · sin SKU en Odoo {s.get('sin_sku_en_odoo', 0)}")

    # 3) Una transacción corta: se vuelven a revisar las guardas y se escribe.
    cx = psycopg2.connect(dsn, connect_timeout=20)
    try:
        with cx.cursor() as cur:
            if not args.aplicar:
                cur.execute("set transaction read only")
            cur.execute("set local statement_timeout = '120s'")
            cur.execute("set local lock_timeout = '5s'")
            try:
                nombre, _, actual = _guardas(cur)
            except _Abortar as exc:
                cx.rollback()
                print(f"ABORT ({args.destino}): {exc}")
                return 2
            if len(filas) < MINIMO or (actual and len(filas) < actual * (1 - CAIDA_MAX)):
                cx.rollback()
                print(f"ABORT: llegan {len(filas):,} renglones (hoy hay {actual:,}); parece una "
                      "lectura trunca de Odoo. No se toca nada.")
                return 2
            print(f"destino={args.destino}: hoy {actual:,} renglones de Odoo · se reemplazan "
                  f"por {len(filas):,}")
            if not args.aplicar:
                cx.rollback()
                print("ENSAYO: no se escribió nada (usa --aplicar).")
                return 0

            # Es una FOTO: lo de Odoo se reemplaza entero, en la misma transacción.
            cur.execute("delete from almacen.locations where cedis = %s and origen = 'odoo'", (CEDIS,))
            psycopg2.extras.execute_values(cur, _INSERT, filas, template=_PLANTILLA, page_size=1000)
            cur.execute("select count(*), coalesce(sum(piezas), 0) from almacen.locations "
                        "where cedis = %s", (CEDIS,))
            total, piezas = cur.fetchone()
            if total != len(filas) or piezas != s["piezas"]:
                cx.rollback()
                print(f"ABORT: quedaron {total:,} renglones / {piezas:,} piezas y se esperaban "
                      f"{len(filas):,} / {s['piezas']:,}. Se deshizo todo.")
                return 2
        cx.commit()
        print(f"LISTO: {total:,} renglones y {piezas:,} piezas en almacen.locations ({nombre}).")
        return 0
    finally:
        cx.close()


if __name__ == "__main__":
    sys.exit(main())
