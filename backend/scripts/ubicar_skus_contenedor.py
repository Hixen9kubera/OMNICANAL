"""
ubicar_skus_contenedor.py — Carga `costing.sku_contenedor` (migración 0060):
en qué contenedor(es) de Kubera llegó cada SKU, con nivel, fuentes y evidencia.

Escritor ÚNICO de esa tabla. Lo que decide vive en
`services/ubicar_contenedores.py` (puro, con pruebas); aquí solo se leen las
fuentes, se corre esa lógica y se escriben los resultados.

FUENTES (todas EN SOLO LECTURA)
  · kubera: costing.packing_ubicaciones + packing_archivos (Ferraforme),
    costing.costos_validados y caja_compartida (linaje de Brandon),
    core.products (catálogo) y los padres de Woo (otro producto los tiene como
    wc_parent_id). Una sola transacción `repeatable read, read only` (todas las
    consultas ven la MISMA foto, como clonar_a_sandbox: producción está viva y
    el indexador de packing puede correr a la vez) y rollback. NUNCA
    `set_session(readonly=True)`: el pooler 6543 comparte la conexión y el
    read-only se le pega a producción (regla 13 del CLAUDE.md).
  · Odoo: product.template.container_numbers, product.product (SKU ↔
    plantilla), purchase.order y purchase.order.line. El cliente SOLO deja
    search_read / read / fields_get / search_count: cualquier otro método
    truena antes de salir a la red.
  · Drive NO se lee: el Drive de Brandon es el origen de costos_validados
    (un solo linaje) y en el análisis aportaba ~7 SKUs nuevos. OJO, cuesta
    más que eso: también escondía ~34 conflictos que solo existían por una N
    que daba el Drive (ACC-0768-EST: el Drive dice 98, costos 101, Odoo 84).
    Leerlo como familia B, o listarlos para Brandon, es decisión de Eduardo.

SALIDAS (en --salida)
  asignaciones.csv   una fila por (sku, N) cargable — lo que iría a la tabla
  excluidos.csv      SKUs que NO se cargan y por qué (conflicto, refutado, …)
  por_sku.csv        una fila por SKU del universo (para comparar corridas)
  numeros.csv        por contenedor: SKUs cargados y documentos que lo cubren
  ocs.csv            cada OC de Odoo con la N que se le dedujo y cómo
  resumen.json       conteos por nivel, multi, motivo, fuente, vías
  --excel RUTA       la lista para Brandon (services/ubicar_contenedores_xlsx.py),
                     de esta MISMA corrida
  faltan_en_destino.csv  (solo con --aplicar) SKUs que el destino no tiene

CANDADO DE ESCRITURA
  Sin --aplicar no se escribe NADA en ninguna BD. Con --aplicar el destino es
  el SUPABASE_DB_URL de env.staging y SOLO se acepta el sandbox (yvootpbz):
  producción (tukwcvsi) está bloqueada en el código
  (`ubicar_contenedores.validar_destino`); habilitarla es otro cambio, con el
  doble candado. En una transacción se borran las filas de SU `origen` y se
  insertan las nuevas; las de otro origen no se tocan (si chocan por
  (sku, numero), gana la existente y se reporta). Solo se cargan SKUs que
  existan en core.products del DESTINO.
  · `--origen` solo acepta «carga_…» (`ubicar_contenedores.validar_origen`):
    «manual» y los demás son de personas y el DELETE nunca los alcanza.
  · `--aplicar` no se deja combinar con `--sin-odoo` (sin Odoo desaparecen la
    familia O y las OC: sería reemplazar la carga por una degradada).
  · Tope: si las filas nuevas bajan del 90 % de las que ya tiene su origen,
    aborta sin tocar nada, salvo con `--permitir-caida`.
  · OJO: `clonar_a_sandbox.py` hace `truncate core.products cascade` y eso
    VACÍA esta tabla en el sandbox (la FK la alcanza). Después de re-clonar,
    volver a correr este script con --aplicar.

Nunca imprime DSNs ni credenciales: solo la ref del proyecto.

Uso (desde la raíz del worktree; credenciales por stdin, sin tocar disco):
  railway variable list -p 66831425-3b47-4fda-8a8b-4b2b5f3df3e2 -s BackendOmnicanal -e production --kv 2>/dev/null \\
    | grep -E '^(KUBERA_DB_URL|ODOO_(URL|DB|USER|PASSWORD))=' \\
    | backend/.venv/Scripts/python.exe backend/scripts/ubicar_skus_contenedor.py --credenciales-stdin --salida <carpeta>
  … mismo comando + --aplicar --acepto-destino yvootpbz     # escribe en el sandbox
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime
import json
import os
import socket
import sys
import time
import xmlrpc.client
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from services import ubicar_contenedores as U  # noqa: E402
from services import ubicar_contenedores_xlsx as X  # noqa: E402

socket.setdefaulttimeout(180)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

REFS_FUENTE = (U.REF_PRODUCCION, U.REF_SANDBOX)
TOPE_CAIDA = 0.9          # filas nuevas / filas que ya tiene su origen


def ref_de(dsn: str) -> str:
    """La ref del usuario o del host (`ubicar_contenedores.ref_de`); «?» si no."""
    return U.ref_de(dsn) or "?"


# ── Credenciales ──────────────────────────────────────────────────────────────

def leer_credenciales(desde_stdin: bool) -> dict[str, str]:
    """KEY=VALUE por stdin (la salida de `railway variable list --kv`), o las
    variables de entorno. Nada de esto se imprime."""
    claves = ("KUBERA_DB_URL", "ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD")
    cred = {k: os.environ.get(k, "") for k in claves}
    if desde_stdin:
        for linea in sys.stdin.read().splitlines():
            if "=" in linea:
                k, v = linea.split("=", 1)
                if k.strip() in claves:
                    cred[k.strip()] = v.strip().strip('"').strip("'")
    return cred


def leer_env(ruta: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    if not ruta.exists():
        return vals
    for linea in ruta.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


# ── kubera (solo lectura) ─────────────────────────────────────────────────────

CONSULTAS_KUBERA = {
    "ubicaciones": """select u.sku::text as sku, u.ferraforme_sha256, u.ferraforme_fila, u.alineado,
                             u.original_sha256
                        from costing.packing_ubicaciones u order by u.id""",
    "archivos": """select id, tipo, sha256, nombre, miembro
                     from costing.packing_archivos order by id""",
    # Costo y medidas solo para la hoja de provisionales del Excel (su única pista).
    "costos": """select sku::text as sku, contenedor, costo_producto, largo, ancho, alto, peso,
                        piezas_por_caja, cajas
                   from costing.costos_validados""",
    "caja": """select sku::text as sku, contenedor, contenedor_base, archivo
                 from costing.caja_compartida""",
    "productos": "select sku::text as sku, name, status from core.products",
    # Padre de Woo = otro producto lo tiene como wc_parent_id.
    "padres": """select distinct p.sku::text as sku
                   from core.products p
                   join core.products h on h.wc_parent_id = p.wc_id""",
}


def leer_kubera(dsn: str) -> dict[str, list[dict]]:
    import psycopg2
    import psycopg2.extras

    ref = ref_de(dsn)
    # La ref completa es de 20 caracteres; «tukwcvsi»/«yvootpbz» es su prefijo.
    if not ref.startswith(REFS_FUENTE):
        sys.exit(f"ABORT: la fuente no es kubera ni el sandbox (ref {ref[:8]}…).")
    salida: dict[str, list[dict]] = {}
    conn = psycopg2.connect(dsn, connect_timeout=30)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Por TRANSACCIÓN (muere con el rollback), nunca por sesión; y una sola
        # foto para las siete consultas (REPEATABLE READ, como clonar_a_sandbox).
        cur.execute("set transaction isolation level repeatable read, read only")
        cur.execute("set local statement_timeout = '120s'")
        for nombre, sql in CONSULTAS_KUBERA.items():
            cur.execute(sql)
            salida[nombre] = [dict(r) for r in cur.fetchall()]
    finally:
        conn.rollback()
        conn.close()
    return salida


# ── Odoo (solo lectura) ───────────────────────────────────────────────────────

class OdooSoloLectura:
    """Cliente XML-RPC que SOLO deja leer. El método se revisa ANTES de salir a
    la red; la contraseña no se expone en repr ni en errores."""

    PERMITIDOS = frozenset({"search_read", "read", "fields_get", "search_count"})

    def __init__(self, url: str, db: str, usuario: str, password: str, proxy=None):
        url = (url or "").rstrip("/")
        if not (url and db and usuario and password):
            raise ValueError("faltan credenciales de Odoo (ODOO_URL/DB/USER/PASSWORD)")
        self._db, self.__pwd = db, password
        if proxy is None:
            uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True).authenticate(
                db, usuario, password, {})
            if not uid:
                raise PermissionError("Odoo rechazó la autenticación")
            self._uid = uid
            self._obj = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
        else:                                   # pruebas: un proxy falso, sin red
            self._uid, self._obj = 1, proxy

    def __repr__(self) -> str:
        return "OdooSoloLectura(<oculto>)"

    def llamar(self, modelo: str, metodo: str, *args, **kw):
        if metodo not in self.PERMITIDOS:
            raise PermissionError(f"método no permitido en Odoo (solo lectura): {metodo}")
        return self._obj.execute_kw(self._db, self._uid, self.__pwd, modelo, metodo, list(args), kw)

    def todo(self, modelo: str, dominio: list, campos: list[str], lote: int = 4000) -> list[dict]:
        salida, offset = [], 0
        while True:
            r = self.llamar(modelo, "search_read", dominio, fields=campos, limit=lote, offset=offset,
                            order="id asc", context={"active_test": False})
            salida += r
            if len(r) < lote:
                return salida
            offset += lote


def leer_odoo(cred: dict[str, str]) -> dict[str, list[dict]]:
    o = OdooSoloLectura(cred.get("ODOO_URL"), cred.get("ODOO_DB"), cred.get("ODOO_USER"),
                        cred.get("ODOO_PASSWORD"))
    return {
        "plantillas": o.todo("product.template", [("container_numbers", "!=", False)],
                             ["container_numbers"]),
        "productos": o.todo("product.product", [], ["default_code", "product_tmpl_id", "active", "name"]),
        "ordenes": o.todo("purchase.order", [], ["name", "partner_ref", "origin", "notes", "state"]),
        "lineas": o.todo("purchase.order.line", [], ["order_id", "product_id", "product_qty", "qty_received"]),
    }


# ── Salidas ───────────────────────────────────────────────────────────────────

def _csv(ruta: Path, columnas: list[str], filas: list[list]) -> None:
    with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(columnas)
        w.writerows(filas)


def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


def patrones_conflicto(excluidos: list[dict]) -> list[dict]:
    """Conflictos agrupados por (N con documento, N sin documento, quién la dice)."""
    c: collections.Counter = collections.Counter()
    ej: dict[tuple, list[str]] = collections.defaultdict(list)
    for e in excluidos:
        if e["motivo"] != "conflicto":
            continue
        dis = sorted(e.get("sin_documento") or [])
        quien = sorted({f for f, m in e["evidencia"].items() for n in dis if str(n) in m})
        prim = sorted(set(e["ns"]) - set(dis))
        k = (tuple(prim), tuple(dis), tuple(quien))
        c[k] += 1
        ej[k].append(e["sku"])
    return [{"con_documento": list(k[0]), "sin_documento": list(k[1]), "lo_dice": list(k[2]),
             "skus": v, "ejemplos": ej[k][:5]} for k, v in c.most_common(40)]


def odoo_nombre_distinto(asignaciones: list[dict], nombres: dict[str, str]) -> list[list]:
    """Filas cargadas SOLO por el campo de Odoo cuyo producto en Odoo no
    comparte ni una palabra con el del catálogo: posible colisión de SKU en
    Odoo (el contenedor sería el de OTRO producto). Solo se reporta; decidir si
    se excluyen es de Eduardo (las traducciones dan falsos positivos)."""
    filas = []
    for a in asignaciones:
        if a["evidencia"].get("familias") != ["O"]:
            continue
        refs = a["evidencia"].get("odoo_campo") or []
        n_odoo = next((r.get("nombre_odoo") for r in refs if r.get("nombre_odoo")), "")
        n_cat = nombres.get(U.clave(a["sku"]), "")
        if n_odoo and n_cat and U.palabras_en_comun(n_odoo, n_cat) == 0:
            filas.append([a["sku"], a["numero"], n_cat, n_odoo, refs[0].get("texto", ""),
                          max((r.get("productos_con_este_sku") or 1) for r in refs)])
    return filas


def por_numero(evidencias: list[dict], asignaciones: list[dict], ns_con_ferraforme: set[int],
               codigo_por_n: dict[int, str]) -> list[list]:
    fuentes: dict[int, dict[str, set]] = collections.defaultdict(lambda: collections.defaultdict(set))
    for e in evidencias:
        fuentes[e["numero"]][e["fuente"]].add(U.clave(e["sku"]))
    carg: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for a in asignaciones:
        carg[a["numero"]][a["nivel"]] += 1
        carg[a["numero"]]["multi"] += a["multi"]
    filas = []
    for n in sorted(set(fuentes) | set(carg)):
        f = fuentes.get(n, {})
        ferra = len(f.get(U.FERRA_ALINEADO, set()) | f.get(U.FERRA_NO_ALINEADO, set()))
        filas.append([n, codigo_por_n.get(n, ""), carg[n]["A"] + carg[n]["B"], carg[n]["A"], carg[n]["B"],
                      carg[n]["multi"], ferra, n in ns_con_ferraforme,
                      len(f.get(U.COSTOS, set()) | f.get(U.CAJA, set())), len(f.get(U.ODOO_CAMPO, set())),
                      len(f.get(U.ODOO_PELON, set())), len(f.get(U.OC_RECIBIDA, set()))])
    return filas


def escribir_salidas(carpeta: Path, res: dict, evidencias: list[dict], ctx: dict,
                     nombres: dict[str, str], extra: dict) -> dict:
    carpeta.mkdir(parents=True, exist_ok=True)
    _csv(carpeta / "asignaciones.csv",
         ["sku", "numero", "codigo", "nivel", "fuentes", "multi", "origen", "evidencia"],
         [[a["sku"], a["numero"], a["codigo"] or "", a["nivel"], "|".join(a["fuentes"]), a["multi"],
           a["origen"], _j(a["evidencia"])] for a in res["asignaciones"]])
    _csv(carpeta / "excluidos.csv",
         ["sku", "nombre", "motivo", "n_candidatas", "n_por_fuente", "sin_documento", "refuta",
          "oc_no_documenta", "evidencia"],
         [[e["sku"], nombres.get(U.clave(e["sku"]), ""), e["motivo"], "|".join(map(str, e["ns"])),
           e["n_por_fuente"], "|".join(map(str, e.get("sin_documento") or [])), e.get("refuta", ""),
           _j(e["oc_no_documenta"]) if e.get("oc_no_documenta") else "",
           _j(e["evidencia"]) if e["evidencia"] else ""] for e in res["excluidos"]])
    distintos = odoo_nombre_distinto(res["asignaciones"], nombres)
    _csv(carpeta / "odoo_nombre_distinto.csv",
         ["sku", "numero", "nombre_catalogo", "nombre_odoo", "texto_odoo", "productos_con_este_sku"], distintos)
    _csv(carpeta / "por_sku.csv",
         ["sku", "estado", "motivo", "nivel_sku", "numeros", "multi", "n_por_fuente"],
         [[r["sku"], r["estado"], r["motivo"],
           ("A" if any(a["nivel"] == "A" for a in r["asignaciones"]) else "B") if r["estado"] == "cargar" else "",
           "|".join(str(a["numero"]) for a in r["asignaciones"]) if r["estado"] == "cargar"
           else "|".join(map(str, r["ns"])),
           r["multi"], r["n_por_fuente"]] for r in res["por_sku"]])
    _csv(carpeta / "numeros.csv",
         ["numero", "codigo", "skus_cargados", "nivel_A", "nivel_B", "multi", "ferraforme_skus",
          "ferraforme_es_documento", "costos_skus", "odoo_campo_skus", "odoo_pelon_skus", "oc_recibida_skus"],
         por_numero(evidencias, res["asignaciones"], ctx["ns_con_ferraforme"], ctx["codigo_por_numero"]))
    _csv(carpeta / "ocs.csv", ["oc", "estado", "numero", "via", "skus", "duplicada_de"],
         [[x["po"], x["estado"], x["n"] if x["n"] is not None else "", x["via"], x["skus"],
           x.get("duplicada_de", "")] for _, x in sorted(ctx["numeros_oc"].items(), key=lambda kv: str(kv[1]["po"]))])
    resumen = dict(res["resumen"])
    resumen.update({
        "evidencia_filas": ctx["conteos"],
        "sin_numero": {k: len(v) for k, v in ctx["sin_numero"].items()},
        "sin_numero_ejemplos": {k: v[:15] for k, v in ctx["sin_numero"].items()},
        "vias_odoo_campo": ctx.get("vias_odoo", {}),
        "oc_renglones": ctx.get("oc_renglones", {}),
        "oc_por_via": dict(collections.Counter((x["via"] or "sin_N").split(" ")[0].split(":")[0]
                                               for x in ctx["numeros_oc"].values())),
        "ns_con_ferraforme": sorted(ctx["ns_con_ferraforme"]),
        "odoo_solo_nombre_distinto": len(distintos),
        "patrones_conflicto": patrones_conflicto(res["excluidos"]),
        **extra,
    })
    (carpeta / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1, default=str),
                                          encoding="utf-8")
    return resumen


# ── Aplicar (solo sandbox) ────────────────────────────────────────────────────

def aplicar(asignaciones: list[dict], dsn: str, origen: str, carpeta: Path,
            permitir_caida: bool = False) -> dict:
    import psycopg2
    import psycopg2.extras

    U.validar_destino(dsn)
    U.validar_origen(origen)                   # nunca «manual» ni otro origen humano
    conn = psycopg2.connect(dsn, connect_timeout=30)
    try:
        cur = conn.cursor()
        cur.execute("select to_regclass('costing.sku_contenedor')")
        if cur.fetchone()[0] is None:
            sys.exit("ABORT: el destino no tiene costing.sku_contenedor (aplicar la 0060 primero).")
        skus = sorted({a["sku"] for a in asignaciones}, key=U.clave)
        cur.execute("select sku::text from core.products where sku = any(%s::citext[])", (skus,))
        existen = {U.clave(r[0]) for r in cur.fetchall()}
        faltan = [a for a in asignaciones if U.clave(a["sku"]) not in existen]
        filas = [U.fila_bd(a) for a in asignaciones if U.clave(a["sku"]) in existen]
        _csv(carpeta / "faltan_en_destino.csv", ["sku", "numero", "nivel"],
             [[a["sku"], a["numero"], a["nivel"]] for a in faltan])
        # Tope: una corrida degradada (sin Odoo, otra fuente, un error) no
        # sustituye en silencio la carga buena de su mismo origen.
        cur.execute("select count(*) from costing.sku_contenedor where origen = %s", (origen,))
        previas = int(cur.fetchone()[0] or 0)
        if previas and len(filas) < TOPE_CAIDA * previas and not permitir_caida:
            raise SystemExit(f"ABORT: {len(filas):,} filas nuevas contra {previas:,} de «{origen}» "
                             f"(< {TOPE_CAIDA:.0%}); no se tocó nada. Si es a propósito: --permitir-caida.")
        U.validar_destino(dsn)                 # otra vez, justo antes de escribir
        cur.execute("delete from costing.sku_contenedor where origen = %s", (origen,))
        borradas = cur.rowcount
        insertadas = psycopg2.extras.execute_values(
            cur,
            """insert into costing.sku_contenedor
                 (sku, numero, codigo, nivel, fuentes, multi, evidencia, origen)
               values %s
               on conflict (sku, numero) do nothing
               returning sku""",
            filas, template="(%s, %s, %s, %s, %s::text[], %s, %s::jsonb, %s)", page_size=1000, fetch=True)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"destino": U.REF_SANDBOX, "origen": origen, "previas_de_su_origen": previas,
            "borradas_de_su_origen": borradas,
            "insertadas": len(insertadas), "chocan_con_otro_origen": len(filas) - len(insertadas),
            "faltan_en_destino_filas": len(faltan),
            "faltan_en_destino_skus": len({U.clave(a["sku"]) for a in faltan})}


# ── Principal ─────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Carga costing.sku_contenedor (dry run por default).")
    ap.add_argument("--salida", required=True, help="carpeta para los CSV y el resumen")
    ap.add_argument("--credenciales-stdin", action="store_true",
                    help="leer KUBERA_DB_URL y ODOO_* como KEY=VALUE por stdin")
    ap.add_argument("--sin-odoo", action="store_true", help="no leer Odoo (solo kubera)")
    ap.add_argument("--origen", default=U.ORIGEN_CARGA_INICIAL,
                    help="solo «carga_…»: el cargador nunca borra orígenes humanos («manual»)")
    ap.add_argument("--aplicar", action="store_true", help="escribir en el SANDBOX")
    ap.add_argument("--permitir-caida", action="store_true",
                    help=f"con --aplicar: aceptar menos del {TOPE_CAIDA * 100:.0f} %% de las filas "
                         "que ya tiene su origen")
    ap.add_argument("--acepto-destino", default="", help="con --aplicar: la ref del sandbox, yvootpbz")
    ap.add_argument("--destino-env", default=str(ROOT / "env.staging"),
                    help="archivo con SUPABASE_DB_URL del destino (default: env.staging)")
    ap.add_argument("--excel", default="", help="ruta del .xlsx para Brandon (opcional)")
    args = ap.parse_args()

    try:
        U.validar_origen(args.origen)
    except ValueError as e:
        sys.exit(f"ABORT: {e}.")
    destino_dsn = ""
    if args.aplicar:
        if args.sin_odoo:
            sys.exit("ABORT: --aplicar con --sin-odoo reemplazaría la carga por una sin la familia O ni las OC.")
        if args.acepto_destino != U.REF_SANDBOX:
            sys.exit(f"ABORT: --aplicar exige --acepto-destino {U.REF_SANDBOX} (solo sandbox).")
        destino_dsn = leer_env(Path(args.destino_env)).get("SUPABASE_DB_URL", "")
        try:
            U.validar_destino(destino_dsn)
        except PermissionError as e:
            sys.exit(f"ABORT: {e}.")

    cred = leer_credenciales(args.credenciales_stdin)
    fuente_dsn = cred.get("KUBERA_DB_URL") or ""
    if not fuente_dsn:
        sys.exit("ABORT: falta KUBERA_DB_URL (por stdin con --credenciales-stdin o en el entorno).")
    t0 = time.time()
    print(f"[{'APLICAR→sandbox' if args.aplicar else 'DRY-RUN'}] fuente kubera {ref_de(fuente_dsn)[:8]}… "
          f"(solo lectura){'' if args.sin_odoo else ' + Odoo (solo lectura)'}", flush=True)
    kub = leer_kubera(fuente_dsn)
    del fuente_dsn
    print("  kubera:", {k: len(v) for k, v in kub.items()}, f"{time.time() - t0:.1f}s", flush=True)
    odoo = None
    if not args.sin_odoo:
        odoo = leer_odoo(cred)
        print("  odoo:", {k: len(v) for k, v in odoo.items()}, f"{time.time() - t0:.1f}s", flush=True)
    cred.clear()

    evidencias, ctx = U.armar_evidencia(ubicaciones=kub["ubicaciones"], archivos=kub["archivos"],
                                        costos=kub["costos"], caja=kub["caja"], odoo=odoo)
    catalogo = {U.clave(p["sku"]): p["sku"] for p in kub["productos"]}
    nombres = {U.clave(p["sku"]): p.get("name") or "" for p in kub["productos"]}
    padres = {U.clave(p["sku"]) for p in kub["padres"]}
    res = U.ubicar(evidencias, catalogo, padres, ctx["ns_con_ferraforme"], ctx["codigo_por_numero"],
                   origen=args.origen)
    carpeta = Path(args.salida)
    extra = {"fuente": "kubera+odoo" if odoo else "kubera", "odoo_leido": bool(odoo),
             "drive_leido": False, "origen": args.origen,
             "generado": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "catalogo_skus": len(catalogo), "padres_woo": len(padres)}
    resumen = escribir_salidas(carpeta, res, evidencias, ctx, nombres, extra)
    print(json.dumps({k: resumen[k] for k in ("skus_cargables", "filas", "filas_por_nivel", "skus_por_nivel",
                                              "skus_nivel_A_estricto", "skus_multi", "filas_multi",
                                              "excluidos_por_motivo")}, ensure_ascii=False, indent=1))
    if args.excel:
        conteos = X.escribir_libro(
            args.excel, excluidos=res["excluidos"], asignaciones=res["asignaciones"], evidencias=evidencias,
            ns_con_ferraforme=ctx["ns_con_ferraforme"], codigo_por_numero=ctx["codigo_por_numero"],
            archivos=kub["archivos"], shas_indexados={u.get("ferraforme_sha256") or "" for u in kub["ubicaciones"]},
            mapa_codigos=ctx["mapa_codigos"],
            catalogo=catalogo, nombres=nombres, costos=kub["costos"],
            generado=X.fecha_larga(datetime.datetime.now()), resumen=resumen)
        resumen["excel"] = {"ruta": str(args.excel), "renglones_por_hoja": conteos}
        (carpeta / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1, default=str),
                                              encoding="utf-8")
        print("excel:", conteos)
    if args.aplicar:
        r = aplicar(res["asignaciones"], destino_dsn, args.origen, carpeta, permitir_caida=args.permitir_caida)
        resumen["aplicado"] = r
        (carpeta / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1, default=str),
                                              encoding="utf-8")
        print("aplicado:", r)
    print(f"listo en {time.time() - t0:.1f}s → {carpeta}")


if __name__ == "__main__":
    main()
