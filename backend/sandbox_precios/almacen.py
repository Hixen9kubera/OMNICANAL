"""Archivos del laboratorio: dónde se guarda cada cosa y cómo se escribe sin romperla.

Estructura bajo `LAB_DATOS_DIR` (volumen en Railway, carpeta local ignorada por git):

    crudo/<AAAA-MM-DD>/<fuente>.json[l]   extracciones del día (kubera, ML, Odoo)
    cache/                                cachés con vigencia (comisiones por categoría, packing lists)
    ultimo/<nombre>.json                  lo que sirve la API (último cálculo completo)
    snapshots/<AAAA-MM-DD>/resumen.json   foto diaria COMPACTA → historia de precios
                                          (+ metricas.json y estado.json; ~1.3 MB/día)
    estado.json                           bitácora de la última corrida del pipeline

Nada de esto va a Supabase: la regla es no crear ni modificar tablas.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sandbox_precios._entorno import datos_dir

CDMX = ZoneInfo("America/Mexico_City")


def hoy_cdmx() -> dt.date:
    return dt.datetime.now(CDMX).date()


def ahora_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def ruta(*partes: str) -> Path:
    p = datos_dir().joinpath(*partes)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def crudo(nombre: str, dia: dt.date | None = None) -> Path:
    return ruta("crudo", (dia or hoy_cdmx()).isoformat(), nombre)


def ultimo(nombre: str) -> Path:
    return ruta("ultimo", nombre)


def _json_default(o: Any) -> Any:
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    try:
        from decimal import Decimal

        if isinstance(o, Decimal):
            return float(o)
    except Exception:  # noqa: BLE001
        pass
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


def escribir_json(destino: Path, datos: Any) -> Path:
    """Escritura atómica: la API nunca lee un archivo a medias."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=destino.parent, prefix=".tmp_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False, default=_json_default, separators=(",", ":"))
    os.replace(tmp, destino)
    return destino


def leer_json(origen: Path, defecto: Any = None) -> Any:
    try:
        with open(origen, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return defecto


def escribir_jsonl(destino: Path, filas: Iterable[dict], anexar: bool = False) -> int:
    destino.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(destino, "a" if anexar else "w", encoding="utf-8") as fh:
        for f in filas:
            fh.write(json.dumps(f, ensure_ascii=False, default=_json_default) + "\n")
            n += 1
    return n


def leer_jsonl(origen: Path) -> list[dict]:
    try:
        with open(origen, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]
    except FileNotFoundError:
        return []


def ultimo_crudo(nombre: str, max_dias: int = 7) -> Path | None:
    """El `crudo/<día>/<nombre>` más reciente dentro de `max_dias` (None si no hay)."""
    base = datos_dir() / "crudo"
    if not base.exists():
        return None
    dias = sorted((d for d in base.iterdir() if d.is_dir()), reverse=True)
    limite = hoy_cdmx() - dt.timedelta(days=max_dias)
    for d in dias:
        try:
            if dt.date.fromisoformat(d.name) < limite:
                break
        except ValueError:
            continue
        p = d / nombre
        if p.exists():
            return p
    return None


def a_https(url: Any) -> str | None:
    """URL de imagen/enlace servible desde una página https: `http://` → `https://`
    y `//host/…` → `https://host/…`. ML todavía manda miniaturas `http://` y la CSP
    del laboratorio (`img-src … https:`) las bloquearía. Vacío → ``None``.

    Solo esquemas http(s): cualquier otro (`javascript:`, `data:`, `vbscript:`,
    rutas relativas) → ``None``. La web pinta estas URL en `<a href>` y React 18
    no bloquea `javascript:`; con `'unsafe-inline'` en la CSP sería un XSS
    almacenado si un dato de marketplace lo trajera.

    >>> [a_https(x) for x in ("http://a/b.jpg", "//a/b.jpg", "https://a", "", None)]
    ['https://a/b.jpg', 'https://a/b.jpg', 'https://a', None, None]
    >>> [a_https(x) for x in ("javascript:alert(1)", " JaVaScRiPt:x", "data:text/html,x", "/relativa", "https://")]
    [None, None, None, None, None]
    """
    s = str(url or "").strip()
    if not s or any(c in s for c in "\x00\r\n\t"):
        return None
    if s[:7].lower() == "http://":
        s = "https://" + s[7:]
    elif s.startswith("//"):
        s = "https:" + s
    elif s[:8].lower() != "https://":
        return None
    return s if len(s) > 8 and s[8] not in "/\\" else None


# ── Snapshots diarios (compactos) ─────────────────────────────────────────────
# Antes cada día copiaba `ultimo/` entero (~28 MB/día ≈ 10 GB/año). Lo que la
# serie de precios necesita cabe en ~1 MB: por publicación, lo cobrado, lo
# recomendado, el precio de lista, unidades y visitas de 30 d, stock FULL y
# estado. Se guardan para siempre (no hay retención: 1 MB/día ≈ 0.4 GB/año).
SNAPSHOT_RESUMEN = "resumen.json"
SNAPSHOT_EXTRAS = ("metricas.json", "estado.json")      # se copian tal cual (≈0.5 MB)
SNAPSHOT_FORMATO = "compacto-1"
# llave corta → campo de la fila de `publicaciones.json` (pr sale de `precios.json`)
SNAPSHOT_CLAVES = {"pc": "precio_cobrado", "pr": "precio_recomendado", "pl": "precio_lista",
                   "u30": "unidades_30d", "v30": "visitas_30d", "sf": "stock_full", "e": "estado"}
_SNAPSHOT_VIEJOS = ("publicaciones.json", "precios.json")  # formato anterior (copia de ultimo/)


def _filas_de(doc: Any) -> list[dict]:
    filas = doc.get("filas") if isinstance(doc, dict) else doc
    return [f for f in (filas or []) if isinstance(f, dict) and f.get("id")]


def _num(v: Any) -> float | int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def compactar(publicaciones: Any, precios: Any) -> dict[str, dict[str, Any]]:
    """{id: {pc, pr, pl, u30, v30, sf, e}} sin llaves nulas, desde los documentos
    `publicaciones.json` y `precios.json` (ya leídos).

    >>> compactar({"filas": [{"id": "m:B:1", "precio_cobrado": 99.0, "precio_lista": None,
    ...                       "unidades_30d": 3, "visitas_30d": 50, "stock_full": 0, "estado": "activa"}]},
    ...           {"filas": [{"id": "m:B:1", "precio_recomendado": 94.0}]})
    {'m:B:1': {'pc': 99.0, 'u30': 3, 'v30': 50, 'sf': 0, 'e': 'activa', 'pr': 94.0}}
    """
    out: dict[str, dict[str, Any]] = {}
    for f in _filas_de(publicaciones):
        d: dict[str, Any] = {}
        for corta, campo in SNAPSHOT_CLAVES.items():
            if corta == "pr":
                continue
            v = f.get(campo)
            v = v if corta == "e" and isinstance(v, str) else _num(v)
            if v is not None:
                d[corta] = v
        out[str(f["id"])] = d
    for f in _filas_de(publicaciones):   # respaldo: el recomendado que publicaciones trae unido
        pr = _num(f.get("precio_recomendado"))
        if pr is not None:
            out[str(f["id"])]["pr"] = pr
    for f in _filas_de(precios):          # manda precios.json: es lo que se recomendó
        pr = _num(f.get("precio_recomendado"))
        if pr is not None:
            out.setdefault(str(f["id"]), {})["pr"] = pr
    return out


def _escribir_copia(origen: Path, destino: Path) -> None:
    """Copia atómica (tmp + os.replace): la API nunca lee una copia a medias."""
    fd, tmp = tempfile.mkstemp(dir=destino.parent, prefix=".tmp_", suffix=destino.suffix)
    os.close(fd)
    try:
        shutil.copyfile(origen, tmp)
        os.replace(tmp, destino)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _limpiar_snapshot(destino: Path) -> list[str]:
    """Borra de un día todo lo que no es el formato compacto (copias viejas de ultimo/)."""
    quedan = {SNAPSHOT_RESUMEN, *SNAPSHOT_EXTRAS}
    borrados = []
    for p in destino.iterdir():
        if p.is_file() and p.name not in quedan:
            p.unlink()
            borrados.append(p.name)
        elif p.is_dir():
            shutil.rmtree(p)
            borrados.append(p.name + "/")
    return borrados


def _doc_resumen(filas: dict[str, dict], dia: str, pub_gen: Any, pre_gen: Any, origen: str) -> dict:
    return {"_meta": {"formato": SNAPSHOT_FORMATO, "dia": dia, "generado_at": ahora_iso(),
                      "origen": origen, "publicaciones_generado_at": pub_gen,
                      "precios_generado_at": pre_gen, "claves": SNAPSHOT_CLAVES, "filas": len(filas)},
            **filas}


def fotografiar_ultimo(dia: dt.date | None = None) -> Path:
    """Foto COMPACTA de `ultimo/` en `snapshots/<día>/`: `resumen.json` (ver
    `SNAPSHOT_CLAVES`) + `metricas.json` + `estado.json`. Es lo que vuelve serie
    la historia de precios; ~1 MB por día en vez de ~28 MB."""
    u = datos_dir() / "ultimo"
    d = (dia or hoy_cdmx()).isoformat()
    destino = datos_dir() / "snapshots" / d
    destino.mkdir(parents=True, exist_ok=True)
    pub = leer_json(u / "publicaciones.json", {}) or {}
    pre = leer_json(u / "precios.json", {}) or {}
    filas = compactar(pub, pre)
    escribir_json(destino / SNAPSHOT_RESUMEN,
                  _doc_resumen(filas, d, pub.get("generado_at") if isinstance(pub, dict) else None,
                               pre.get("generado_at") if isinstance(pre, dict) else None, "ultimo"))
    for nombre in SNAPSHOT_EXTRAS:
        if (u / nombre).exists():
            _escribir_copia(u / nombre, destino / nombre)
    _limpiar_snapshot(destino)
    return destino


def compactar_snapshot(dia: str) -> dict[str, Any]:
    """Migra un día en formato viejo (copia entera de `ultimo/`) al compacto.
    Idempotente: si ya tiene `resumen.json` solo limpia lo que sobre."""
    destino = datos_dir() / "snapshots" / dia
    if not destino.is_dir():
        return {"dia": dia, "ok": False, "motivo": "no existe"}
    antes = sum(p.stat().st_size for p in destino.rglob("*") if p.is_file())
    ya = (destino / SNAPSHOT_RESUMEN).exists()
    if not ya:
        pub = leer_json(destino / "publicaciones.json", {}) or {}
        pre = leer_json(destino / "precios.json", {}) or {}
        if not _filas_de(pub) and not _filas_de(pre):
            return {"dia": dia, "ok": False, "motivo": "sin publicaciones.json ni precios.json legibles"}
        filas = compactar(pub, pre)
        escribir_json(destino / SNAPSHOT_RESUMEN,
                      _doc_resumen(filas, dia, pub.get("generado_at") if isinstance(pub, dict) else None,
                                   pre.get("generado_at") if isinstance(pre, dict) else None,
                                   "migrado_de_formato_viejo"))
    borrados = _limpiar_snapshot(destino)
    despues = sum(p.stat().st_size for p in destino.rglob("*") if p.is_file())
    return {"dia": dia, "ok": True, "ya_compacto": ya, "bytes_antes": antes, "bytes_despues": despues,
            "borrados": borrados}


def migrar_snapshots() -> list[dict[str, Any]]:
    """Compacta todos los días que sigan en el formato viejo."""
    return [compactar_snapshot(d) for d in dias_con_snapshot()
            if any((datos_dir() / "snapshots" / d / n).exists() for n in _SNAPSHOT_VIEJOS)]


def firma_snapshot(dia: str) -> tuple:
    """(mtime_ns, tamaño) de lo que se lee de un día: `resumen.json` o, en el
    formato viejo, sus `publicaciones.json` y `precios.json`. Para cachés."""
    base = datos_dir() / "snapshots" / dia
    nombres = (SNAPSHOT_RESUMEN,) if (base / SNAPSHOT_RESUMEN).exists() else _SNAPSHOT_VIEJOS
    firma: list[Any] = []
    for n in nombres:
        try:
            st = (base / n).stat()
            firma.append((n, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            firma.append((n, None))
    return tuple(firma)


def leer_snapshot(dia: str) -> dict[str, dict[str, Any]]:
    """{id: {pc, pr, pl, u30, v30, sf, e}} de un día, en cualquiera de los dos
    formatos (el compacto `resumen.json` o el viejo, que se compacta al vuelo).
    Un archivo ilegible cuenta como ausente (se registra con `ValueError` hacia arriba
    solo si NINGUNA fuente se pudo leer)."""
    base = datos_dir() / "snapshots" / dia
    r = base / SNAPSHOT_RESUMEN
    if r.exists():
        doc = leer_json(r, {}) or {}
        return {k: v for k, v in doc.items() if not k.startswith("_") and isinstance(v, dict)}
    docs, errores = [], []
    for n in _SNAPSHOT_VIEJOS:
        try:
            docs.append(leer_json(base / n, {}) or {})
        except ValueError as exc:
            errores.append(f"{n}: {exc}")
            docs.append({})
    if errores and not any(_filas_de(x) for x in docs):
        raise ValueError(f"snapshot {dia} ilegible: {'; '.join(errores)}")
    return compactar(docs[0], docs[1])


def dias_con_snapshot() -> list[str]:
    """Días (AAAA-MM-DD) con carpeta en `snapshots/`; ignora temporales y basura."""
    base = datos_dir() / "snapshots"
    if not base.exists():
        return []
    out = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        try:
            dt.date.fromisoformat(d.name)
        except ValueError:
            continue
        out.append(d.name)
    return sorted(out)
