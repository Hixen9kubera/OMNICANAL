"""
lecturas_fuente.py — Contadores en memoria de QUIÉN respondió cada lectura F5:
kubera o el fallback a MySQL. El testigo del tablero: /migracion los pinta y
la evidencia "N días al 100% kubera" es la que autoriza el corte del dominio.
Mismo espíritu que el ring buffer del espejo: memoria del proceso, sin BD.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_datos: dict[str, dict] = {}


def anotar(dominio: str, fuente: str, error: str | None = None) -> None:
    """fuente: 'kubera' | 'fallback'. Nunca lanza (es instrumentación)."""
    try:
        with _lock:
            d = _datos.setdefault(dominio, {
                "kubera": 0, "fallback": 0,
                "ultimo_fallback": None, "ultimo_error": None,
            })
            d[fuente] = int(d.get(fuente, 0)) + 1
            if fuente == "fallback":
                d["ultimo_fallback"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                d["ultimo_error"] = (error or "")[:200]
    except Exception:  # noqa: BLE001 — jamás frenar la lectura por contar
        pass


def estado() -> dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _datos.items()}
