"""
copiar_packing_lists.py — Copia los packing lists de Google Drive al bucket
privado ``packing-lists`` de Supabase y los registra en
``costing.packing_archivos`` (migración 0055).

Dos carpetas, dos papeles:
  · ``original``   — la carpeta packing_lists: el papel del proveedor, de donde
                     sale el costo.
  · ``ferraforme`` — la copia homologada: el mismo packing list con columnas de
                     SKU agregadas al frente y las filas 1:1 con el original.
                     Solo sirve para ubicar el SKU en su renglón.

ES IDEMPOTENTE, así que volver a correrlo solo trae lo que cambió:
  · si ya hay una versión con ese mismo modifiedTime de Drive, ni se descarga;
  · el objeto se nombra por su sha256: subir lo mismo otra vez no hace nada;
  · la fila es única por (archivo de Drive, miembro del zip, sha256).
Un packing list nuevo se añade igual: en el manifiesto, o con ``--local``.

EL MANIFIESTO NO VA EN EL REPO. Es el listado de Drive en JSON
(``[{"tipo", "id", "nombre", "mime", "modificado"}, ...]``) y trae los IDs de
los archivos de Drive: se guarda fuera del repo.

SE SALTAN las copias vaciadas ``X-``/``XX-``/``XXX-`` (solo encabezados). Los
zip se abren y se guarda cada hoja de cálculo de adentro.

CANDADO: se niega a escribir en producción salvo con ``--produccion``.

Uso (desde la raíz del worktree, que tiene env.staging):
  python backend/scripts/copiar_packing_lists.py --manifiesto m.json --solo 5
  python backend/scripts/copiar_packing_lists.py --manifiesto m.json --reporte r.json
  python backend/scripts/copiar_packing_lists.py --local archivo.xlsx --tipo original
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from services import packing_drive, packing_storage as st  # noqa: E402

REF_KUBERA_PROD = "tukwcvsi"
COPIA_VACIA = re.compile(r"^X+-")
# El MISMO patrón que packing_publicados._RE_CONTENEDOR, que es el que llena
# caja_compartida.contenedor: así las dos tablas se juntan por contenedor.
# SZLS va primero y se toma el match MÁS LARGO (ver _contenedor_de): la forma
# genérica se come el último dígito de SZLS50213900.
CODIGO = re.compile(r"(?:SZLS\d{6,9}|[A-Z]{4}[A-Z0-9]{8,14}|[A-Z]{4}\d{6,7}|\d{9,12})")
ACTOR = "script:copiar_packing_lists"


def cargar_env(archivo: Path) -> dict[str, str]:
    vals = dict(os.environ)
    if archivo.exists():
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            s = linea.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def destino(env: dict[str, str], produccion: bool) -> tuple[str, str, str, str]:
    """(url, llave, db_url, ref). Aborta si apunta a producción sin permiso."""
    url = env.get("SUPABASE_URL", "")
    llave = env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    db = env.get("SUPABASE_DB_URL", "")
    if not (url and llave and db):
        sys.exit("Faltan SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY o SUPABASE_DB_URL.")
    ref = re.sub(r"^https?://", "", url).split(".")[0]
    m = re.search(r"postgres\.([a-z0-9]+)", db) or re.search(r"db\.([a-z0-9]+)\.supabase", db)
    ref_db = m[1] if m else ""
    if ref != ref_db:
        # Subir los archivos a un proyecto y registrarlos en otro dejaría filas
        # que apuntan a objetos que no existen.
        sys.exit(f"CANDADO: SUPABASE_URL ({ref[:8]}…) y SUPABASE_DB_URL ({ref_db[:8]}…) "
                 "apuntan a proyectos distintos.")
    es_prod = ref.startswith(REF_KUBERA_PROD) or ref == env.get("SUPABASE_PROD_REF", "").strip()
    if es_prod and not produccion:
        sys.exit("CANDADO: el destino es PRODUCCIÓN. Solo con --produccion.")
    # 5432 (sesión) y no 6543: el pooler de transacciones comparte conexiones.
    return url, llave, db.replace(":6543/", ":5432/"), ref


def codigo(nombre: str) -> str | None:
    cands = [m[0] for m in CODIGO.finditer((nombre or "").upper())]
    return max(cands, key=len) if cands else None


def desempacar(datos: bytes) -> list[tuple[bytes, str | None, str]]:
    """``(bytes, miembro, ext)`` por hoja de cálculo. Un xlsx TAMBIÉN es un zip:
    se distingue porque trae ``xl/workbook.xml``."""
    if datos[:4] == b"\xd0\xcf\x11\xe0":          # .xls viejo (OLE)
        return [(datos, None, "xls")]
    try:
        z = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile:
        raise ValueError("no es xlsx ni zip (¿Drive devolvió una página?)")
    nombres = z.namelist()
    if "xl/workbook.xml" in nombres:
        return [(datos, None, "xlsx")]
    miembros = [n for n in nombres
                if n.lower().endswith((".xlsx", ".xls")) and not n.startswith("__MACOSX/")
                and not Path(n).name.startswith("~$")]
    if not miembros:
        return [(datos, None, "zip")]
    return [(z.read(n), n, n.rsplit(".", 1)[1].lower()) for n in miembros]


def sin_cambios(pg, e: dict) -> bool:
    """¿Ya hay una versión con este modifiedTime? Entonces ni se descarga."""
    if not (e.get("id") and e.get("modificado")):
        return False
    with pg, pg.cursor() as cur:
        cur.execute("""select 1 from costing.packing_archivos
                        where drive_file_id = %s and drive_modified_at >= %s::timestamptz
                        limit 1""", (e["id"], e["modificado"]))
        return cur.fetchone() is not None


def registrar(pg, e: dict, piezas, url: str, llave: str) -> list[str]:
    estados = []
    for datos, miembro, ext in piezas:
        sha = st.huella(datos)
        with pg, pg.cursor() as cur:
            cur.execute("""select id from costing.packing_archivos
                            where coalesce(drive_file_id, '') = coalesce(%s, '')
                              and coalesce(miembro, '') = coalesce(%s, '') and sha256 = %s""",
                        (e.get("id"), miembro, sha))
            fila = cur.fetchone()
            if fila:
                # Misma versión: Drive movió el modifiedTime (un renombre, p. ej.)
                # sin cambiar el contenido. Solo se actualiza lo descriptivo.
                cur.execute("""update costing.packing_archivos
                                  set nombre = %s,
                                      drive_modified_at = greatest(drive_modified_at, %s::timestamptz)
                                where id = %s""", (e["nombre"], e.get("modificado"), fila[0]))
                estados.append("misma_version")
                continue
        # El objeto se sube ANTES de la fila: una fila sin objeto apuntaría a la
        # nada; un objeto sin fila lo recoge la siguiente corrida.
        ruta = st.ruta_de(e["tipo"], sha, ext)
        subida = st.subir(ruta, datos, ext, url=url, llave=llave)
        with pg, pg.cursor() as cur:
            cur.execute("select set_config('app.usuario', %s, true)", (ACTOR,))
            cur.execute(
                """insert into costing.packing_archivos
                       (tipo, sha256, ruta, bytes, nombre, miembro, contenedor_base,
                        drive_file_id, drive_mime, drive_modified_at, origen)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::timestamptz, %s)
                   on conflict ((coalesce(drive_file_id, '')), (coalesce(miembro, '')), sha256)
                   do nothing""",
                (e["tipo"], sha, ruta, len(datos), e["nombre"], miembro,
                 codigo(miembro or "") or codigo(e["nombre"]), e.get("id"), e.get("mime"),
                 e.get("modificado"), "local" if e.get("local") else "drive"))
        estados.append("nueva" if subida == "subido" else "nueva_fila")
    return estados


def leer(e: dict) -> bytes:
    if e.get("local"):
        return Path(e["local"]).read_bytes()
    datos, _ = packing_drive.descargar_id(e["id"])
    return datos


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--manifiesto", type=Path, help="JSON con el listado de Drive")
    ap.add_argument("--local", type=Path, help="un archivo del disco, en vez de Drive")
    ap.add_argument("--tipo", choices=["original", "ferraforme"],
                    help="con --local es obligatorio; con --manifiesto, filtra")
    ap.add_argument("--solo", type=int, help="procesar solo los primeros N")
    ap.add_argument("--filtro", help="solo los nombres que contengan este texto")
    ap.add_argument("--forzar", action="store_true",
                    help="descargar aunque el modifiedTime no haya cambiado")
    ap.add_argument("--dry-run", action="store_true", help="no descarga ni escribe")
    ap.add_argument("--reporte", type=Path, help="deja el resultado por archivo en JSON")
    ap.add_argument("--env", type=Path, default=ROOT / "env.staging")
    ap.add_argument("--produccion", action="store_true")
    a = ap.parse_args()

    if bool(a.manifiesto) == bool(a.local):
        sys.exit("Usa --manifiesto o --local (uno de los dos).")
    if a.local:
        if not a.tipo:
            sys.exit("--local necesita --tipo.")
        entradas = [{"tipo": a.tipo, "nombre": a.local.name, "local": str(a.local)}]
    else:
        entradas = json.loads(a.manifiesto.read_text(encoding="utf-8"))
        if a.tipo:
            entradas = [e for e in entradas if e["tipo"] == a.tipo]
    if a.filtro:
        entradas = [e for e in entradas if a.filtro.lower() in e["nombre"].lower()]
    if a.solo:
        entradas = entradas[:a.solo]

    url, llave, db, ref = destino(cargar_env(a.env), a.produccion)
    print(f"Destino: {ref[:8]}… · {len(entradas)} archivos"
          f"{' · DRY-RUN' if a.dry_run else ''}", flush=True)
    pg = psycopg2.connect(db, connect_timeout=20)

    total, resultado = len(entradas), []
    for i, e in enumerate(entradas, 1):
        t0, mb, error = time.monotonic(), 0.0, None
        if COPIA_VACIA.match(e["nombre"]):
            estados = ["copia_vacia"]
        elif not a.forzar and sin_cambios(pg, e):
            estados = ["sin_cambios"]
        elif a.dry_run:
            estados = ["por_copiar"]
        else:
            try:
                datos = leer(e)
                mb = len(datos) / 1e6
                estados = registrar(pg, e, desempacar(datos), url, llave)
            except Exception as exc:  # noqa: BLE001 — un archivo malo no para la copia
                estados, error = ["fallo"], f"{type(exc).__name__}: {exc}"[:300]
                if pg.closed:
                    pg = psycopg2.connect(db, connect_timeout=20)
        seg = time.monotonic() - t0
        print(f"[{i}/{total}] {e['tipo']:<10} {','.join(estados):<24} {mb:7.1f} MB "
              f"{seg:6.1f} s  {e['nombre']}" + (f"\n        ↳ {error}" if error else ""),
              flush=True)
        resultado.append({"tipo": e["tipo"], "nombre": e["nombre"], "estados": estados,
                          "mb": round(mb, 2), "segundos": round(seg, 1), "error": error})
    pg.close()

    cuenta: dict[str, int] = {}
    for r in resultado:
        for s in r["estados"]:
            cuenta[s] = cuenta.get(s, 0) + 1
    print("Resumen:", ", ".join(f"{k} {v}" for k, v in sorted(cuenta.items())),
          f"· {sum(r['mb'] for r in resultado):.0f} MB descargados", flush=True)
    if a.reporte:
        a.reporte.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
