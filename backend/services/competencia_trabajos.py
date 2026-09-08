"""
competencia_trabajos.py — el raspado de UNA búsqueda, en segundo plano.

── POR QUÉ EXISTE ─────────────────────────────────────────────────────────────
Porque `POST /api/competencia/busqueda` hacía el raspado EN LÍNEA y ese raspado
tarda de verdad. Medido contra producción el 8-sep-2026: **178 segundos** para
«casco integral moto». El backend terminaba bien —`{ok: true}`— pero la petición
del navegador ya se había caído en el camino, así que el panel mostraba «No se
pudo medir.» mientras el trabajo se completaba y se guardaba.

Un botón que hace el trabajo y pierde la respuesta es peor que uno que falla: el
usuario vuelve a apretarlo y vuelve a pagar.

── EL MISMO MOLDE QUE LOS RESOLVEDORES DE COSTOS ──────────────────────────────
`packing_publicados` y `packing_resolver` ya resolvieron esto: el POST arranca un
hilo y devuelve un `jid`, y la UI pregunta `GET /{jid}` hasta que el paso sea
`listo` o `error`. Se copia esa forma —incluido el vocabulario de pasos— para que
el frontend pueda reusar `useTrabajoJob`.

Aquí el almacén es mucho más chico que el de allá: un trabajo son cuatro campos,
no filas con fotos en base64. Por eso el tope es más alto y el TTL más corto.

── SIN PERSISTENCIA, A PROPÓSITO ──────────────────────────────────────────────
Si el backend reinicia, el trabajo se pierde — pero **el raspado ya se guardó en
la base**: `medir_busquedas` escribe antes de que el trabajo se marque listo. Lo
que se pierde es el aviso, no el dato ni el dinero. El panel lo nota igual porque
`busqueda_medida_en` cambia.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any

from services import competencia_captura

log = logging.getLogger("omnicanal.competencia.trabajos")

_TTL = 60 * 30          # media hora: un raspado tarda ~3 min, no 3 h
_MAX = 40               # un trabajo son cuatro campos; caben muchos

_trabajos: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

PASOS = {
    "encolado": "En cola…",
    "raspando": "Buscando en Mercado Libre…",
    "listo": "Listo",
    "error": "Error",
}


def _purgar() -> None:
    """Tira lo caducado y lo que sobre del tope. Se llama CON el lock tomado."""
    ahora = time.time()
    fuera = [k for k, v in _trabajos.items() if ahora - v.get("creado", 0) > _TTL]
    if len(_trabajos) - len(fuera) > _MAX:
        vivos = sorted(((k, v) for k, v in _trabajos.items() if k not in fuera),
                       key=lambda kv: kv[1].get("creado", 0))
        fuera += [k for k, _ in vivos[: len(vivos) - _MAX]]
    for k in fuera:
        _trabajos.pop(k, None)


def _marcar(jid: str, paso: str, **extra: Any) -> None:
    with _lock:
        t = _trabajos.get(jid)
        if not t:       # purgado a media corrida: no se resucita
            return
        t.update({"paso": paso, "paso_label": PASOS.get(paso, paso),
                  "actualizado": time.time(), **extra})


def estado(jid: str) -> dict[str, Any] | None:
    """El estado del trabajo. `None` si caducó o el backend reinició."""
    with _lock:
        t = _trabajos.get(jid)
        return dict(t) if t else None


def _correr(jid: str, termino: str) -> None:
    _marcar(jid, "raspando")
    try:
        # `medir_busquedas` es una corrutina y este es un hilo aparte, así que
        # necesita su propio loop. No se puede colgar del loop del backend: eso
        # es justo lo que la regla 11 prohíbe (tres minutos de red bloquearían
        # el servidor entero).
        murados: set[str] = set()
        guardadas = asyncio.run(
            competencia_captura.medir_busquedas([termino], bloqueados=murados))
        n = guardadas.get(termino, 0)
        # Cero filas NO es error, pero hay DOS ceros y no significan lo mismo:
        # «ML no tiene nada» es un hecho del mercado; «ML no nos dejó ver» es un
        # problema nuestro. Los dos quedan medidos —ya se pagaron— y el panel
        # dice cuál de los dos fue.
        _marcar(jid, "listo", filas=n, vacio=n == 0,
                bloqueado=termino in murados)
    except Exception as exc:                                  # noqa: BLE001
        log.warning("la búsqueda de %r falló: %s", termino, exc)
        _marcar(jid, "error", error=str(exc)[:200])


def arrancar(termino: str) -> dict[str, Any]:
    """Lanza el raspado y devuelve el `jid` de inmediato."""
    jid = uuid.uuid4().hex[:12]
    ahora = time.time()
    with _lock:
        _purgar()
        _trabajos[jid] = {
            "id": jid, "termino": termino, "paso": "encolado",
            "paso_label": PASOS["encolado"], "creado": ahora, "actualizado": ahora,
            "filas": None, "vacio": None, "bloqueado": None, "error": None,
        }
    threading.Thread(target=_correr, args=(jid, termino),
                     name=f"busq-{jid}", daemon=True).start()
    return {"id": jid, "paso": "encolado", "paso_label": PASOS["encolado"],
            "termino": termino}
