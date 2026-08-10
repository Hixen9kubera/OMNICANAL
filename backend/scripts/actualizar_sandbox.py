"""
actualizar_sandbox.py — Refresca el SANDBOX con los datos de la BD kubera de
PRODUCCIÓN, para que las pantallas se prueben contra cifras que se parecen a las
de verdad.

DIRECCIÓN ÚNICA: producción se lee en transacciones READ ONLY y el sandbox es el
único que recibe escrituras. Los dos candados del repo siguen vigentes: el
destino no puede ser la ref de la BD kubera ni el `SUPABASE_PROD_REF` de
env.staging.

TRAMPA DEL POOLER — leer esto antes de "mejorar" el candado de lectura. Las dos
DSN entran por el pooler de Supabase en modo TRANSACCIÓN (puerto 6543): varios
clientes se turnan la MISMA conexión del servidor. Ahí, cualquier ajuste de
SESIÓN se queda pegado y lo hereda el siguiente cliente. Abrir producción con
`options=-c default_transaction_read_only=on` o con `set_session(readonly=True)`
—que parece lo prudente— deja conexiones del pool en read-only para QUIEN SEA
que las tome después, el backend de Railway incluido: la protección se convierte
en una caída de escrituras ajena. Verificado el 10-ago: así se dejó el pool del
sandbox en read-only y la carga de migraciones falló con "cannot execute CREATE
EXTENSION in a read-only transaction" sin que nadie hubiera cambiado nada.
Por eso el candado va POR TRANSACCIÓN (`set transaction read only`), que muere
con ella y no puede contaminar a nadie.

QUÉ HACE Y QUÉ NO. Copia por UPSERT (`on conflict do update`) tabla por tabla en
orden de llaves foráneas: padres antes que hijos. NO borra nada — lo que exista
en el sandbox y no exista en producción (cobayas de las pruebas de corte, filas
de `suite_caos_sandbox`) sobrevive intacto, y al final se declara cuántas son.
Por lo mismo, esto NO propaga BAJAS: un producto borrado en producción sigue en
el sandbox. Si se quiere una copia exacta, el camino es recrear el sandbox
(`aplicar_migraciones.py --recrear`) y volver a correr esto.

LOS TRIGGERS SE APAGAN DURANTE LA COPIA, y esto NO es un atajo. Las tablas
destino llevan triggers `touch` (ponen `updated_at = now()`) e `hist` (escriben
`channel.listing_history` y `costing.cost_history`). Copiando con ellos activos,
el sandbox no queda igual a producción: queda con TODAS las fechas de
modificación puestas en el momento de la copia, y con una historia de cambios
inventada — un "cambio" por cada campo de cada fila copiada. Es exactamente lo
que ya pasó antes en este sandbox: `costing.cost_history` tiene 19,767 filas
contra 52 en producción, todas fabricadas por cargas anteriores. Se apagan por
tabla, dentro de la MISMA transacción de la copia (si el proceso muere, el
rollback los devuelve encendidos) y al final se verifica que quedaron activos.

QUEDAN FUERA A PROPÓSITO:
  · `core.usuarios`      — son credenciales; el sandbox no las necesita.
  · `costing.cost_history` y `costing.pricing_params` — historia local del
    sandbox; se deja como está para no borrar trabajo ajeno sin pedirlo.
Ninguna de las tablas copiadas lleva datos personales: `channel.orders` guarda
ids, importes y estados — no nombre, correo ni dirección del comprador.

ANTES DE CORRER: el esquema del sandbox debe estar al día
(`aplicar_migraciones.py`), o las columnas nuevas de producción no se copian.
El script compara columnas y avisa cuáles se está saltando.

Uso:
  backend/.venv/Scripts/python.exe backend/scripts/actualizar_sandbox.py            # ensayo
  backend/.venv/Scripts/python.exe backend/scripts/actualizar_sandbox.py --aplicar
  ... --aplicar --tabla channel.orders --tabla channel.order_items
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parent.parent.parent
REF_KUBERA_PROD = "tukwcvsi"
socket.setdefaulttimeout(120)

# Orden de llaves foráneas: cada tabla se copia después de aquellas a las que
# apunta. Cambiar el orden rompe la carga con violación de FK.
#   conflicto: 'actualizar' pisa las columnas no-PK; 'nada' deja la fila como
#   está (para las tablas de historia, que son inmutables por definición).
TABLAS: list[dict] = [
    {"tabla": "core.channels", "pk": ["id"], "conflicto": "actualizar"},
    {"tabla": "core.accounts", "pk": ["id"], "conflicto": "actualizar"},
    {"tabla": "core.products", "pk": ["sku"], "conflicto": "actualizar"},
    {"tabla": "channel.categories", "pk": ["channel_id", "category_id"],
     "conflicto": "actualizar"},
    {"tabla": "channel.product_category", "pk": ["sku", "channel_id"],
     "conflicto": "actualizar"},
    {"tabla": "channel.listings", "pk": ["sku", "account_id", "canal"],
     "conflicto": "actualizar"},
    # La historia no se reescribe: una fila ya escrita es un hecho pasado.
    # `id` es columna de identidad, así que se copia con OVERRIDING SYSTEM VALUE
    # y al final se reacomoda la secuencia (si no, el siguiente insert del
    # sandbox chocaría con un id ya usado).
    {"tabla": "channel.listing_history", "pk": ["id"], "conflicto": "nada",
     "identidad": "id"},
    {"tabla": "channel.orders", "pk": ["canal", "cuenta", "external_order_id"],
     "conflicto": "actualizar"},
    {"tabla": "channel.order_items",
     "pk": ["canal", "cuenta", "external_order_id", "linea"],
     "conflicto": "actualizar"},
    {"tabla": "costing.costos_validados", "pk": ["sku"], "conflicto": "actualizar"},
    {"tabla": "costing.costos_finales", "pk": ["sku", "canal"], "conflicto": "actualizar"},
]

LOTE = 2000


def _watchdog(minutos: int = 30) -> None:
    def _matar() -> None:
        print(f"WATCHDOG: {minutos} min — aborto.", flush=True)
        os._exit(2)
    t = threading.Timer(minutos * 60, _matar)
    t.daemon = True
    t.start()


def cargar_env(nombre: str) -> dict[str, str]:
    vals: dict[str, str] = dict(os.environ)
    p = ROOT / nombre
    if not p.exists():
        sys.exit(f"ABORT: no encuentro {p}")
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


def ref_de(url: str) -> str:
    m = re.search(r"postgres\.([a-z0-9]+):", url)
    return m.group(1) if m else ""


def columnas(cur, tabla: str) -> list[str]:
    esq, tab = tabla.split(".")
    cur.execute("""select column_name from information_schema.columns
                    where table_schema=%s and table_name=%s order by ordinal_position""",
                (esq, tab))
    return [r[0] for r in cur.fetchall()]


def contar(cur, tabla: str) -> int:
    cur.execute(f"select count(*) from {tabla}")
    return int(cur.fetchone()[0])


def _sincronizar_secuencias(cur) -> None:
    """Pone cada secuencia de identidad por encima del id más alto que ya existe.

    Una carga masiva escribe los ids tal cual vienen del origen y la secuencia
    se queda donde estaba: el siguiente insert normal del sandbox pide un id ya
    usado y choca contra la PK. Se hace para TODAS las secuencias de los
    esquemas propios, no solo la de la tabla en curso — el problema es de la
    técnica de carga, no de una tabla."""
    cur.execute("""
        select quote_ident(n.nspname)||'.'||quote_ident(c.relname),
               quote_ident(n.nspname)||'.'||quote_ident(s.relname),
               quote_ident(a.attname)
          from pg_class s
          join pg_depend d on d.objid = s.oid and d.classid = 'pg_class'::regclass
          join pg_class c on c.oid = d.refobjid
          join pg_attribute a on a.attrelid = c.oid and a.attnum = d.refobjsubid
          join pg_namespace n on n.oid = c.relnamespace
         where s.relkind = 'S' and n.nspname in ('core', 'channel', 'costing')""")
    for tabla, secuencia, col in cur.fetchall():
        cur.execute(f"select setval('{secuencia}', "
                    f"coalesce((select max({col}) from {tabla}), 0) + 1, false)")


def copiar(origen, destino, spec: dict, aplicar: bool) -> dict:
    """Copia una tabla. La transacción de LECTURA de producción se abre y se
    cierra aquí dentro (`rollback` al salir): así el `set transaction read only`
    del inicio es siempre la primera sentencia de una transacción nueva, y la
    conexión regresa al pool sin estado."""
    try:
        return _copiar(origen, destino, spec, aplicar)
    finally:
        origen.rollback()


def _copiar(origen, destino, spec: dict, aplicar: bool) -> dict:
    tabla, pk = spec["tabla"], spec["pk"]
    cur_o, cur_d = origen.cursor(), destino.cursor()
    # Candado de lectura con vida de UNA transacción: si algo intentara escribir
    # en producción, el servidor lo rechaza; y al terminar, la conexión vuelve
    # limpia al pool. Va primero, antes de cualquier otra sentencia.
    cur_o.execute("set transaction read only")
    cols_prod, cols_sand = columnas(cur_o, tabla), columnas(cur_d, tabla)
    if not cols_sand:
        return {"tabla": tabla, "estado": "no existe en el sandbox — ¿faltan migraciones?"}
    # Solo lo que existe en AMBAS: si al sandbox le falta una columna nueva de
    # producción, se dice en el reporte en vez de reventar a media carga.
    cols = [c for c in cols_prod if c in cols_sand]
    saltadas = [c for c in cols_prod if c not in cols_sand]

    antes_prod, antes_sand = contar(cur_o, tabla), contar(cur_d, tabla)
    res = {"tabla": tabla, "prod": antes_prod, "sandbox_antes": antes_sand,
           "columnas": len(cols)}
    if saltadas:
        res["columnas_saltadas"] = saltadas
    if not aplicar:
        res["estado"] = "ensayo (no se escribió)"
        cur_o.close(); cur_d.close()
        return res

    lista = ", ".join(f'"{c}"' for c in cols)
    if spec["conflicto"] == "nada":
        accion = "do nothing"
    else:
        sets = [f'"{c}" = excluded."{c}"' for c in cols if c not in pk]
        accion = "do update set " + ", ".join(sets) if sets else "do nothing"
    override = " overriding system value" if spec.get("identidad") else ""
    sql = (f'insert into {tabla} ({lista}){override} values %s '
           f'on conflict ({", ".join(pk)}) {accion}')

    # Triggers fuera mientras dure la copia (ver cabecera). Todo va en la misma
    # transacción: o se escribe la tabla Y se reactivan, o no pasa nada.
    cur_d.execute(f"alter table {tabla} disable trigger user")
    # Secuencias al día ANTES de escribir: el sandbox llegó con la de
    # `listing_history` en 19,220 teniendo ids hasta 42,551 (carga vieja que no
    # la reacomodó), y cualquier inserción chocaba con un id existente.
    _sincronizar_secuencias(cur_d)
    try:
        # Cursor con nombre = lectura del lado del servidor: no se trae 82 mil
        # filas a la memoria del proceso para luego escribirlas.
        lector = origen.cursor(name=f"lee_{tabla.replace('.', '_')}")
        lector.itersize = LOTE
        lector.execute(f"select {lista} from {tabla}")
        escritas, t0 = 0, time.time()
        while True:
            filas = lector.fetchmany(LOTE)
            if not filas:
                break
            psycopg2.extras.execute_values(cur_d, sql, filas, page_size=LOTE)
            escritas += len(filas)
            print(f"  {tabla}: {escritas}/{antes_prod}", flush=True)
        lector.close()
        if spec.get("identidad"):
            _sincronizar_secuencias(cur_d)
    finally:
        cur_d.execute(f"alter table {tabla} enable trigger user")
    destino.commit()

    res["escritas"] = escritas
    res["sandbox_despues"] = contar(cur_d, tabla)
    res["segundos"] = round(time.time() - t0, 1)
    # Lo que el sandbox tiene de más: no se borra, se declara.
    res["sobrantes_en_sandbox"] = max(0, res["sandbox_despues"] - antes_prod)
    cur_o.close(); cur_d.close()
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="sin esto solo cuenta y reporta, no escribe")
    ap.add_argument("--tabla", action="append",
                    help="limitar a una tabla (repetible); por defecto, todas")
    args = ap.parse_args()
    _watchdog()

    prod_url = cargar_env(".env").get("SUPABASE_DB_URL", "")
    sand_env = cargar_env("env.staging")
    sand_url = sand_env.get("SUPABASE_DB_URL", "")
    ref_prod, ref_sand = ref_de(prod_url), ref_de(sand_url)

    # ── Los tres candados ────────────────────────────────────────────────────
    if not ref_prod.startswith(REF_KUBERA_PROD):
        sys.exit(f"ABORT: el ORIGEN ({ref_prod[:8]}…) no es la BD kubera de producción.")
    if not ref_sand:
        sys.exit("ABORT: env.staging no tiene SUPABASE_DB_URL válida.")
    if ref_sand.startswith(REF_KUBERA_PROD) or ref_sand == sand_env.get("SUPABASE_PROD_REF", "").strip():
        sys.exit(f"ABORT: el DESTINO ({ref_sand[:8]}…) es PRODUCCIÓN. Este script solo escribe en el sandbox.")
    if ref_sand == ref_prod:
        sys.exit("ABORT: origen y destino son la misma BD.")

    print(f"Origen  PROD    {ref_prod[:8]}…  (solo lectura)")
    print(f"Destino SANDBOX {ref_sand[:8]}…  {'ESCRIBIENDO' if args.aplicar else '(ensayo)'}\n",
          flush=True)

    # Producción: SIN ajustes de sesión (ver "trampa del pooler" arriba). El
    # read-only se declara en cada transacción, dentro de `copiar()`.
    origen = psycopg2.connect(prod_url, connect_timeout=30)
    origen.autocommit = False
    destino = psycopg2.connect(sand_url, connect_timeout=30)
    destino.autocommit = False

    pendientes = [t for t in TABLAS if not args.tabla or t["tabla"] in args.tabla]
    if args.tabla and len(pendientes) != len(args.tabla):
        sys.exit(f"ABORT: tabla desconocida en --tabla; válidas: {[t['tabla'] for t in TABLAS]}")

    reporte = []
    for spec in pendientes:
        print(f"-> {spec['tabla']}", flush=True)
        try:
            reporte.append(copiar(origen, destino, spec, args.aplicar))
        except Exception as exc:  # noqa: BLE001
            destino.rollback()
            reporte.append({"tabla": spec["tabla"], "error": str(exc)[:300]})
            print(f"  FALLÓ: {exc}", flush=True)
            break
    # Ningún trigger puede quedarse apagado: si uno se queda así, el sandbox
    # deja de comportarse como producción y las pruebas de corte pasarían por
    # razones falsas. Se comprueba SIEMPRE, aunque la copia haya fallado.
    cur = destino.cursor()
    cur.execute("""select n.nspname||'.'||c.relname||'.'||t.tgname
                     from pg_trigger t join pg_class c on c.oid = t.tgrelid
                     join pg_namespace n on n.oid = c.relnamespace
                    where not t.tgisinternal and t.tgenabled = 'D'
                      and n.nspname in ('core','channel','costing')""")
    apagados = [r[0] for r in cur.fetchall()]
    destino.commit()
    origen.close(); destino.close()
    print("\n" + json.dumps(reporte, ensure_ascii=False, indent=1, default=str))
    if apagados:
        print("\nATENCION: quedaron triggers APAGADOS en el sandbox: "
              + ", ".join(apagados)
              + "\nReactivar con: alter table <tabla> enable trigger user")
        sys.exit(1)
    print("\nTriggers del sandbox: todos activos.")


if __name__ == "__main__":
    main()
