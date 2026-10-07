"""
pl_bajar.py — Los packing lists de todos los contenedores, a una carpeta local.

De dónde salen
--------------
`costing.packing_archivos` (kubera) es el índice de las dos carpetas de Drive:

  · tipo `original`    el packing list del PROVEEDOR, uno por contenedor;
  · tipo `ferraforme`  el mismo archivo ya VALIDADO por bodega, con la columna
                       «SKU ODOO» y lo que se contó en físico.

El índice guarda el `drive_file_id` de cada uno. Los archivos se bajan de Drive,
que es público («cualquier persona con el enlace»): GET y nada más. El bucket de
Storage donde producción guarda la copia pide una llave de servicio que esta
carpeta no tiene ni debe tener.

Los `X-…`, `XX-…` no son originales (vienen sin renglones) y se saltan, igual que
en producción.

Pesan: son ~3.3 GB, casi todo fotos incrustadas. Por eso van a una carpeta de
CACHÉ aparte (`--cache-pl`), no a la de salida: la de salida suele vivir en una
carpeta sincronizada con la nube.

La bajada copia la de producción (`packing_drive.descargar`): hoja nativa de
Google → `export?format=xlsx`; binario → `uc?export=download`, y si Drive
contesta el aviso de antivirus de los archivos grandes, el mismo archivo por
`drive.usercontent` con `confirm=t`.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from comun import Cfg, Red, ahora_iso, aviso, escribir_json

RE_COPIA = re.compile(r"^X+-", re.I)
MIME_HOJA = "application/vnd.google-apps.spreadsheet"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_CONSULTA = """
    select id, tipo, sha256, bytes, nombre, miembro, contenedor_base,
           drive_file_id, drive_mime, drive_modified_at::text as drive_modified_at
      from costing.packing_archivos
     order by tipo, id
"""


def _indice(cfg: Cfg) -> list[dict[str, Any]]:
    import psycopg2
    import psycopg2.extras

    dsn = cfg("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("falta SUPABASE_DB_URL (la base kubera)")
    cn = psycopg2.connect(dsn, connect_timeout=25)
    try:
        cn.autocommit = True                 # una lectura y se cierra; sin marcar la sesión
        with cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_CONSULTA)
            return [dict(f) for f in cur.fetchall()]
    finally:
        cn.close()


def _es_zip(datos: bytes) -> bool:
    return datos[:2] == b"PK"


def _bajar(red: Red, fid: str, mime: str) -> bytes:
    if mime == MIME_HOJA:
        r = red.get(f"https://docs.google.com/spreadsheets/d/{fid}/export?format=xlsx", headers=_UA)
        datos = r.content
    else:
        r = red.get(f"https://drive.google.com/uc?export=download&id={fid}", headers=_UA)
        datos = r.content
        if not _es_zip(datos):
            cabeza = datos[:200_000]
            if b"Virus scan warning" in cabeza or b'name="uuid"' in cabeza or b"confirm=" in cabeza:
                r = red.get("https://drive.usercontent.google.com/download"
                            f"?id={fid}&export=download&confirm=t", headers=_UA)
                datos = r.content
    if r.status_code != 200 or not _es_zip(datos):
        pista = " (Drive mandó una página: el archivo no es público)" if b"<html" in datos[:2000].lower() else ""
        raise RuntimeError(f"HTTP {r.status_code}, {len(datos)} bytes{pista}")
    return datos


def bajar(cfg: Cfg, salida: Path, cache: Path) -> dict[str, Any]:
    indice = _indice(cfg)
    todos = len(indice)
    indice = [f for f in indice if not RE_COPIA.match((f["nombre"] or "").strip())]
    cache.mkdir(parents=True, exist_ok=True)
    red = Red(rps=3.0, timeout=900.0)
    estado: dict[int, dict[str, Any]] = {}

    def uno(f: dict[str, Any]) -> None:
        destino = cache / f["tipo"] / f"{f['id']}.xlsx"
        destino.parent.mkdir(parents=True, exist_ok=True)
        info: dict[str, Any] = {"ok": True}
        if destino.exists() and destino.stat().st_size > 0:
            info["de_cache"] = True
        else:
            try:
                datos = _bajar(red, f["drive_file_id"], f["drive_mime"] or "")
                if f.get("miembro"):
                    # El de Drive es un .zip con varios packing lists: se saca el suyo.
                    with zipfile.ZipFile(io.BytesIO(datos)) as z:
                        nombres = z.namelist()
                        elegido = next((n for n in nombres if n == f["miembro"]), None) or next(
                            (n for n in nombres if n.endswith(f["miembro"].split("/")[-1])), None)
                        if not elegido:
                            raise RuntimeError(f"el zip no trae «{f['miembro']}»")
                        datos = z.read(elegido)
                tmp = destino.with_suffix(".parte")
                tmp.write_bytes(datos)
                tmp.replace(destino)
                info["igual_al_copiado"] = hashlib.sha256(datos).hexdigest() == f.get("sha256")
            except Exception as exc:  # noqa: BLE001 — un archivo roto no detiene a los demás
                info = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        if info["ok"]:
            info["bytes"] = destino.stat().st_size
        estado[f["id"]] = info
        hechos = len(estado)
        if hechos % 10 == 0 or hechos == len(indice):
            listos = sum(1 for e in estado.values() if e["ok"])
            aviso(f"packing lists: {hechos}/{len(indice)} · {listos} en disco · "
                  f"{sum(e.get('bytes', 0) for e in estado.values()) / 1e6:,.0f} MB")

    # Primero los chicos: si algo está mal se nota en segundos, no tras 100 MB.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(uno, sorted(indice, key=lambda f: f["bytes"] or 0)))

    for f in indice:
        f["local"] = estado[f["id"]]
    fallidos = [f for f in indice if not f["local"]["ok"]]
    doc = {
        "fuente": "costing.packing_archivos (índice) + Google Drive (archivos, GET público)",
        "leido": ahora_iso(), "cache": str(cache), "en_indice": todos,
        "copias_descartadas": todos - len(indice), "archivos": indice,
    }
    escribir_json(salida / "datos" / "pl_indice.json", doc)
    for f in fallidos:
        aviso(f"   NO se bajó {f['tipo']} #{f['id']} «{(f['nombre'] or '')[:50]}»: {f['local']['error']}")
    return {"archivos": len(indice), "bajados": len(indice) - len(fallidos), "fallidos": len(fallidos),
            "mb": round(sum(f["local"].get("bytes", 0) for f in indice) / 1e6),
            "por_tipo": {t: sum(1 for f in indice if f["tipo"] == t and f["local"]["ok"])
                         for t in sorted({f["tipo"] for f in indice})}}
