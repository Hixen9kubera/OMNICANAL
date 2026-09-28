"""Archivos del laboratorio: dónde se guarda cada cosa y cómo se escribe sin romperla.

Estructura bajo `LAB_DATOS_DIR` (volumen en Railway, carpeta local ignorada por git):

    crudo/<AAAA-MM-DD>/<fuente>.json[l]   extracciones del día (kubera, ML, Odoo)
    cache/                                cachés con vigencia (comisiones por categoría, packing lists)
    ultimo/<nombre>.json                  lo que sirve la API (último cálculo completo)
    snapshots/<AAAA-MM-DD>/<nombre>.json  copia diaria de `ultimo/` → historia de precios
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


def fotografiar_ultimo(dia: dt.date | None = None) -> Path:
    """Copia `ultimo/` a `snapshots/<día>/` (lo que vuelve serie la historia de precios)."""
    origen = datos_dir() / "ultimo"
    destino = datos_dir() / "snapshots" / (dia or hoy_cdmx()).isoformat()
    if destino.exists():
        shutil.rmtree(destino)
    shutil.copytree(origen, destino, ignore=shutil.ignore_patterns("curvas*", ".tmp_*"))
    return destino


def dias_con_snapshot() -> list[str]:
    base = datos_dir() / "snapshots"
    return sorted(d.name for d in base.iterdir() if d.is_dir()) if base.exists() else []
