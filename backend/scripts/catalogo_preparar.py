"""
catalogo_preparar.py — Arma la pestaña CATÁLOGO del panel a partir del artifact del catálogo vivo (inventario contado
desde los packing lists): `backend/data/catalogo/catalogo.json.gz` y las hojas de fotos `backend/data/catalogo/img/`.

    python backend/scripts/catalogo_preparar.py bajar --cache CACHE --env ARCHIVO_ENV
    python backend/scripts/catalogo_preparar.py armar CATALOGO_VIVO --cache CACHE [--salida backend/data/catalogo]

  bajar          Baja los packing lists ORIGINALES de Drive (carpeta pública, solo GET) a CACHE. El índice sale de
                 `costing.packing_archivos` en kubera con un SELECT (`SUPABASE_DB_URL` o `KUBERA_DB_URL` de
                 ARCHIVO_ENV). Los `X-…` no son originales y se saltan. Lo que ya está en CACHE no se vuelve a bajar.
                 Son ~3.3 GB: CACHE va fuera de OneDrive.
  armar          CATALOGO_VIVO es `reportes/catalogo_vivo`: lee `datos_pl.json` (los datos del artifact),
                 `datos/pl.json` (la huella de la foto de cada renglón del packing list) y las hojas del artifact
                 `img/g*.jpg` (fotos de Odoo) e `img/q*.jpg` (fotos de renglón). NO MODIFICA NADA DE ESA CARPETA:
                 el artifact es el respaldo de los datos (Brandon, 29-oct-2026).

LA FOTO DE CADA FILA (pedido de Brandon, 29-oct-2026: «cambiarás las imágenes de odoo a las de packing list»)
    1. La foto ORIGINAL de su renglón en el packing list: el catálogo guarda su sha1 (`pl.json`) y aquí se busca
       ese mismo archivo dentro de los `.xlsx` bajados. Si la fila tiene varios renglones con foto, la más grande.
    2. Si no se encontró el original: la foto de su renglón que ya trae el artifact (hojas `q`, 256 px).
    3. Si la fila no tiene renglón con foto: la de Odoo del artifact (hojas `g`, 256 px).
    Cada fila dice de dónde salió la suya (`fo`: `pl` u `odoo`). Las fotos iguales se guardan una sola vez.

QUÉ LLEVA Y QUÉ NO
    El repo es PÚBLICO. Se quitan el costo por pieza (`co`), el precio del proveedor en dólares (`usd`), el múltiplo
    con que se estima un precio a partir del costo (con el precio se despejaría el costo) y las notas de v3 que lo
    citan. Lo demás es lo que muestra el artifact: piezas, precios de mercado, categorías, contenedores y empates.
    `comprobar_sin_costos` revisa la salida antes de escribirla y truena si queda algo.

SOLO LEE: archivos locales, Drive público y un SELECT a kubera. No escribe en kubera, Woo, ML, Amazon ni Odoo.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent

RE_COPIA = re.compile(r"^X+-", re.I)
MIME_HOJA = "application/vnd.google-apps.spreadsheet"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

LADO = 192                  # px de cada foto en las hojas
COLS = 10                   # fotos por renglón de hoja (10 × 10 = 100 por hoja)
CALIDAD = 80                # WebP

# Lo que NO puede viajar en el repo (el artifact sí lo trae).
LLAVES_COSTO = {"co", "usd"}
_RE_COSTO = re.compile(r"costo|fob|usd|compra", re.I)
# Nombres que mencionan el costo pero son una REGLA o un CONTEO, no un costo: el tope «10 × FOB» y cuántas filas de
# v3 quedaron con su estimado por costo.
LLAVES_PERMITIDAS = {"tope_fob", "estimado_costo"}


# ── bajar ────────────────────────────────────────────────────────────────────

def _env(ruta: Path) -> dict[str, str]:
    from dotenv import dotenv_values
    return {k: v for k, v in dotenv_values(ruta).items() if v}


def _indice(dsn: str) -> list[dict[str, Any]]:
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(dsn)
    try:
        cn.autocommit = True        # un SELECT y se cierra; la sesión NO se marca de solo lectura (regla 13)
        with cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("select id, tipo, nombre, miembro, drive_file_id, drive_mime "
                        "from costing.packing_archivos order by tipo, id")
            return [dict(f) for f in cur.fetchall()]
    finally:
        cn.close()


def _bajar_uno(f: dict[str, Any], destino: Path) -> str:
    import httpx

    if destino.exists() and destino.stat().st_size > 0:
        return "ya estaba"
    fid, mime = f["drive_file_id"], f.get("drive_mime") or ""
    with httpx.Client(timeout=900.0, follow_redirects=True, headers=_UA) as cli:
        if mime == MIME_HOJA:
            r = cli.get(f"https://docs.google.com/spreadsheets/d/{fid}/export?format=xlsx")
        else:
            r = cli.get("https://drive.usercontent.google.com/download",
                        params={"id": fid, "export": "download", "confirm": "t"})
        datos = r.content
    if r.status_code != 200 or datos[:2] != b"PK":
        return f"falló: HTTP {r.status_code}, {len(datos)} bytes"
    if f.get("miembro"):
        # El de Drive es un .zip con varios packing lists: se saca el suyo.
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            nombre = next((n for n in z.namelist() if n == f["miembro"] or n.endswith(f["miembro"].split("/")[-1])), None)
            if not nombre:
                return f"falló: el zip no trae «{f['miembro']}»"
            datos = z.read(nombre)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".parte")
    tmp.write_bytes(datos)
    tmp.replace(destino)
    return f"{len(datos) / 1e6:.1f} MB"


def bajar(cache: Path, archivo_env: Path) -> None:
    env = _env(archivo_env)
    dsn = env.get("SUPABASE_DB_URL") or env.get("KUBERA_DB_URL")
    if not dsn:
        sys.exit(f"{archivo_env} no trae SUPABASE_DB_URL ni KUBERA_DB_URL")
    todos = _indice(dsn)
    lista = [f for f in todos if f.get("drive_file_id") and not RE_COPIA.match((f.get("nombre") or "").strip())]
    print(f"{len(todos)} archivos en el índice; se bajan {len(lista)} (sin copias X- ni los que no tienen Drive)")

    def uno(f: dict[str, Any]) -> tuple[dict[str, Any], str]:
        try:
            return f, _bajar_uno(f, cache / str(f["tipo"]) / f"{f['id']}.xlsx")
        except Exception as exc:  # noqa: BLE001 — un archivo roto no detiene a los demás
            return f, f"falló: {type(exc).__name__}: {str(exc)[:120]}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        for k, (f, res) in enumerate(pool.map(uno, lista), 1):
            if res.startswith("falló") or k % 20 == 0:
                print(f"  {k}/{len(lista)} · {f['tipo']}/{f['id']} {f.get('nombre')}: {res}")
    print("listo")


# ── armar ────────────────────────────────────────────────────────────────────

def _foto_de(f: dict[str, Any]) -> dict[str, Any] | None:
    x = f.get("foto")
    if isinstance(x, str):
        import ast
        x = ast.literal_eval(x)
    return x or None


def retorno(r: dict[str, Any]) -> float | None:
    """El valor de lo que queda a precio de ML (`_tm` de la pantalla, `logica.ts::preparar`): piezas que quedan × la
    media de ML si es medida (o viene del archivo de Eduardo), si no × su estimado."""
    if r.get("pc") is None:
        return None
    ml, em = r.get("ml") or {}, r.get("em")
    medido = ml.get("o") == "e" or (not r.get("sin") and (ml.get("n") or 0) >= 2 and not ml.get("x") and ml.get("o") != "c")
    precio = ml.get("m") if medido and ml else (em[0] if em else None)
    return None if precio is None else (r.get("pq") or 0) * precio


def huellas_por_fila(d: dict[str, Any], pl: dict[str, Any]) -> list[list[str]]:
    """El sha1 de las fotos de los renglones de cada fila, en orden y sin repetir. `fo` es la fila de EXCEL (+1)."""
    filas = {(a["id"], f["fila"]): f for a in pl["originales"] for f in a.get("filas") or []}
    out = []
    for r in d["R"]:
        vistos: list[str] = []
        for rn in r.get("rn") or []:
            for fo in rn.get("fo") or []:
                f = filas.get((rn.get("ao"), fo - 1))
                ft = _foto_de(f) if f else None
                if ft and ft["s"] not in vistos:
                    vistos.append(ft["s"])
        out.append(vistos)
    return out


def indice_medios(cache: Path, quiero: set[str]) -> dict[str, tuple[Path, str]]:
    """sha1 → (xlsx, miembro) de las fotos que se buscan, recorriendo todos los packing lists bajados."""
    out: dict[str, tuple[Path, str]] = {}
    archivos = sorted(cache.rglob("*.xlsx"))
    for k, x in enumerate(archivos, 1):
        try:
            with zipfile.ZipFile(x) as z:
                for n in z.namelist():
                    if n.startswith("xl/media/"):
                        h = hashlib.sha1(z.read(n)).hexdigest()
                        if h in quiero and h not in out:
                            out[h] = (x, n)
        except zipfile.BadZipFile:
            print(f"  ⚠ {x.name} no es un .xlsx legible")
        if k % 25 == 0:
            print(f"  {k}/{len(archivos)} archivos · {len(out)} fotos encontradas")
    return out


def _cuadro(im, lado: int):
    """La foto entera, centrada en un cuadro blanco de `lado` px (no se recorta)."""
    from PIL import Image

    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        fondo = Image.new("RGB", im.size, "white")
        fondo.paste(im, mask=im.split()[-1])
        im = fondo
    else:
        im = im.convert("RGB")
    im.thumbnail((lado, lado), Image.LANCZOS)
    c = Image.new("RGB", (lado, lado), "white")
    c.paste(im, ((lado - im.width) // 2, (lado - im.height) // 2))
    return c


class Hojas:
    """Recorta las fotos de las hojas del artifact (`g`/`q`: 256 px, 8 columnas, 64 por hoja)."""

    def __init__(self, carpeta: Path, sprite: dict[str, Any], prefijo: str):
        self.carpeta, self.s, self.pre, self._abiertas = carpeta, sprite, prefijo, {}

    def foto(self, i: int):
        from PIL import Image

        h, c = divmod(i, self.s["por_hoja"])
        if h not in self._abiertas:
            self._abiertas.clear()
            self._abiertas[h] = Image.open(self.carpeta / f"{self.pre}{h:03d}.jpg").convert("RGB")
        lado, cols = self.s["lado"], self.s["cols"]
        x, y = (c % cols) * lado, (c // cols) * lado
        return self._abiertas[h].crop((x, y, x + lado, y + lado))


def limpiar_fila(r: dict[str, Any]) -> dict[str, Any]:
    """La fila del artifact sin costo: fuera `co` y `usd`, y el múltiplo de los estimados POR COSTO."""
    x = {k: v for k, v in r.items() if k not in LLAVES_COSTO}
    for k in ("em", "ea"):
        e = x.get(k)
        if e and len(e) > 2 and e[1] == "c":
            x[k] = [e[0], e[1], None, e[3] if len(e) > 3 else None, e[4] if len(e) > 4 else 0]
    for k in ("ml",):
        if isinstance(x.get(k), dict) and isinstance(x[k].get("v3"), dict):
            x[k] = dict(x[k])
            x[k]["v3"] = _v3_sin_costo(x[k]["v3"])
    if isinstance(x.get("v3"), dict):
        x["v3"] = _v3_sin_costo(x["v3"])
    return x


def _v3_sin_costo(v: dict[str, Any]) -> dict[str, Any]:
    v = dict(v)
    if v.get("nt"):
        v["nt"] = [n for n in v["nt"] if not _RE_COSTO.search(str(n))]
        if not v["nt"]:
            v.pop("nt")
    return v


def comprobar_sin_costos(doc: dict[str, Any]) -> None:
    """Truena si en la salida queda una llave de costo o un múltiplo de costo."""
    malos: list[str] = []

    def ver(o: Any, ruta: str) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if (k in LLAVES_COSTO or _RE_COSTO.search(k)) and k not in LLAVES_PERMITIDAS:
                    malos.append(f"{ruta}.{k}")
                ver(v, f"{ruta}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                ver(v, f"{ruta}[{i}]")

    ver(doc, "$")
    for r in doc["R"]:
        for k in ("em", "ea"):
            e = r.get(k)
            if e and e[1] == "c" and e[2] is not None:
                malos.append(f"{r['s']}.{k} con múltiplo de costo")
    if malos:
        raise SystemExit("Quedó información de costo en la salida:\n  " + "\n  ".join(malos[:20]))


def armar(cat: Path, cache: Path, salida: Path, lado: int = LADO, cols: int = COLS) -> None:
    from PIL import Image

    d = json.loads((cat / "datos_pl.json").read_text(encoding="utf-8"))
    pl = json.loads((cat / "datos" / "pl.json").read_text(encoding="utf-8"))
    huellas = huellas_por_fila(d, pl)
    quiero = {h for hs in huellas for h in hs}
    print(f"{len(d['R'])} filas · {len(quiero)} fotos distintas de packing list por buscar")
    medios = indice_medios(cache, quiero)
    print(f"{len(medios)} de {len(quiero)} encontradas en los packing lists bajados")

    # La foto de cada fila: la original más grande de sus renglones → la de renglón del artifact → la de Odoo.
    tam: dict[str, tuple[int, int]] = {}
    zips: dict[Path, zipfile.ZipFile] = {}

    def bytes_de(h: str) -> bytes:
        x, n = medios[h]
        if x not in zips:
            zips[x] = zipfile.ZipFile(x)
        return zips[x].read(n)

    def medida(h: str) -> tuple[int, int]:
        if h not in tam:
            try:
                tam[h] = Image.open(io.BytesIO(bytes_de(h))).size
            except Exception:  # noqa: BLE001 — una imagen que PIL no abre no compite
                tam[h] = (0, 0)
        return tam[h]

    llaves: dict[tuple[str, Any], int] = {}
    orden: list[tuple[str, Any]] = []
    por_fila: dict[int, tuple[int | None, str | None]] = {}
    cuenta = {"pl_original": 0, "pl_artifact": 0, "odoo": 0, "sin": 0}
    # Las fotos se acomodan en las hojas en el ORDEN EN QUE ABRE LA TABLA (mayor RETORNO primero —piezas que quedan ×
    # precio de ML—, como `ordenar` de la pantalla desde v0.705.0): la primera página pide una hoja, no cuarenta.
    R = d["R"]
    vista = sorted(range(len(R)), key=lambda i: (-(retorno(R[i]) or -1), -(R[i].get("pq") or 0), R[i]["s"]))
    for idx in vista:
        r, hs = R[idx], huellas[idx]
        disponibles = [h for h in hs if h in medios and min(medida(h)) > 0]
        if disponibles:
            llave, fo = ("sha", max(disponibles, key=lambda h: (min(medida(h)), max(medida(h))))), "pl"
            cuenta["pl_original"] += 1
        elif r.get("j") is not None:
            llave, fo = ("q", r["j"]), "pl"
            cuenta["pl_artifact"] += 1
        elif r.get("i") is not None:
            llave, fo = ("g", r["i"]), "odoo"
            cuenta["odoo"] += 1
        else:
            por_fila[idx] = (None, None)
            cuenta["sin"] += 1
            continue
        if llave not in llaves:
            llaves[llave] = len(orden)
            orden.append(llave)
        por_fila[idx] = (llaves[llave], fo)
    print(f"fotos por fila: {cuenta} · {len(orden)} fotos distintas en las hojas")

    # Las hojas.
    carpeta = salida / "img"
    carpeta.mkdir(parents=True, exist_ok=True)
    for viejo in carpeta.glob("f*.webp"):
        viejo.unlink()
    g = Hojas(cat / "img", d["spriteG"], "g")
    q = Hojas(cat / "img", d["spriteQ"], "q")
    por_hoja = cols * cols
    total_bytes = 0
    for h0 in range(0, len(orden), por_hoja):
        hoja = Image.new("RGB", (cols * lado, cols * lado), "white")
        for k, (tipo, v) in enumerate(orden[h0:h0 + por_hoja]):
            try:
                im = (Image.open(io.BytesIO(bytes_de(v))) if tipo == "sha" else q.foto(v) if tipo == "q" else g.foto(v))
                hoja.paste(_cuadro(im, lado), ((k % cols) * lado, (k // cols) * lado))
            except Exception as exc:  # noqa: BLE001 — una foto rota deja su cuadro en blanco
                print(f"  ⚠ foto {tipo}:{str(v)[:12]} no se pudo pintar: {exc}")
        destino = carpeta / f"f{h0 // por_hoja}.webp"
        hoja.save(destino, "WEBP", quality=CALIDAD, method=6)
        total_bytes += destino.stat().st_size
    for z in zips.values():
        z.close()
    print(f"{(len(orden) + por_hoja - 1) // por_hoja} hojas · {total_bytes / 1e6:.1f} MB")

    # Los datos, sin costo.
    R = []
    for k, r in enumerate(d["R"]):
        fi, fo = por_fila[k]
        x = limpiar_fila(r)
        x.pop("i", None)
        x.pop("j", None)
        if fi is not None:
            x["f"], x["fo"] = fi, fo
        R.append(x)
    estimacion = json.loads(json.dumps(d.get("estimacion") or {}))
    (estimacion.get("ml") or {}).pop("multiplo", None)
    doc = {
        "meta": {"generado": d.get("generado"), "preparado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "fotos": {"lado": lado, "cols": cols, "por_hoja": por_hoja, "total": len(orden),
                           "hojas": (len(orden) + por_hoja - 1) // por_hoja, **cuenta}},
        "fuentes": d.get("fuentes"), "resumen": d.get("resumen"), "rot": d.get("rot"), "estimacion": estimacion,
        "K": d["K"], "A": d["A"], "C": d["C"], "mercado": d.get("mercado"), "v3": d.get("v3"), "R": R,
    }
    comprobar_sin_costos(doc)
    salida.mkdir(parents=True, exist_ok=True)
    crudo = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (salida / "catalogo.json.gz").write_bytes(gzip.compress(crudo, 9, mtime=0))
    print(f"catalogo.json.gz · {len(crudo) / 1e6:.1f} MB sin comprimir → "
          f"{(salida / 'catalogo.json.gz').stat().st_size / 1e6:.1f} MB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bajar")
    b.add_argument("--cache", required=True, type=Path)
    b.add_argument("--env", required=True, type=Path)
    a = sub.add_parser("armar")
    a.add_argument("catalogo", type=Path)
    a.add_argument("--cache", required=True, type=Path)
    a.add_argument("--salida", type=Path, default=_BACKEND / "data" / "catalogo")
    a.add_argument("--lado", type=int, default=LADO)
    args = ap.parse_args()
    if args.cmd == "bajar":
        bajar(args.cache, args.env)
    else:
        armar(args.catalogo, args.cache, args.salida, lado=args.lado)


if __name__ == "__main__":
    main()
