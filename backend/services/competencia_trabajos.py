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
import contextvars
import logging
import threading
import time
import uuid
from typing import Any

from config import settings
from services import competencia_captura, competencia_juez

log = logging.getLogger("omnicanal.competencia.trabajos")

_TTL = 60 * 30          # media hora: un raspado tarda ~3 min, no 3 h
_MAX = 40               # un trabajo son cuatro campos; caben muchos
# Un trabajo VIVO más viejo que esto ya no va a terminar: es un zombi. Tres
# medias horas cubren la cola de Apify (2 corridas a la vez, hasta 20 min cada
# una) y el reclamo de 30 min de la mejora, con holgura.
_ZOMBI = 3 * _TTL
_TERMINADOS = ("listo", "error")

_trabajos: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

PASOS = {
    "encolado": "En cola…",
    "raspando": "Buscando en Mercado Libre…",
    "mejorando": "Buscando un mejor término…",
    "listo": "Listo",
    "error": "Error",
}

# Trabajo VIVO por clave («busq:<término>», «mejora:<sku>»). Dos clics sobre lo
# mismo —dos personas, dos pestañas, o el botón que se reactiva cuando el sondeo
# se rinde— devolvían dos trabajos: dos raspados pagados. Con esto el segundo
# POST recibe el `jid` del que ya corre.
_por_clave: dict[str, str] = {}

# Hasta cuándo el juez colgado de una captura EMPIEZA SKUs. El raspado ya midió
# 178 s y el panel deja de preguntar a los 6 min: el juez corre DESPUÉS de marcar
# `listo`, pero igual no debe retener el hilo. Lo que no alcance queda pendiente
# para el script por lotes.
#
# No es lo que TARDA: el plazo se revisa antes de cada SKU, no durante. El que
# arranca en el segundo 59 hace su trozo entero (las 10 filas de una página caben
# en uno), y ese trozo espera turno hasta `_JUEZ_TIMEOUT_S` y llama hasta otro
# tanto, dos veces si el lote sale sospechoso: 60 + 2 × (30 + 30) = 180 s en el
# peor caso. Tras `listo` el panel pregunta 210 s (`JUEZ_VUELTAS` en page.tsx) y
# `JuezTrasLaCaptura` amarra las dos cifras: subir cualquiera de estas dos pide
# subir esas vueltas.
_JUEZ_PLAZO_S = 60.0
_JUEZ_TIMEOUT_S = 30.0


def _purgar() -> None:
    """Tira lo caducado y lo que sobre del tope. Se llama CON el lock tomado.

    Lo que sigue CORRIENDO no se tira ni por viejo ni por el tope: tirarlo suelta
    su clave, y entonces el segundo POST ya no recibe su `jid` sino que arranca
    otro hilo y vuelve a pagar. Pasaba con un trabajo que hacía cola tras Apify
    más de media hora, o con 41 arranques mientras uno seguía vivo. Solo un
    zombi (más de `_ZOMBI`) se tira estando vivo."""
    ahora = time.time()
    fuera = [k for k, v in _trabajos.items()
             if ahora - v.get("creado", 0) > (_TTL if v.get("paso") in _TERMINADOS else _ZOMBI)]
    sobran = len(_trabajos) - len(fuera) - _MAX
    if sobran > 0:
        # El recorte por tope elige SOLO entre los terminados, del más viejo al
        # más nuevo. Si no alcanzan, el almacén crece un rato: son cuatro campos.
        terminados = sorted(((k, v) for k, v in _trabajos.items()
                             if k not in fuera and v.get("paso") in _TERMINADOS),
                            key=lambda kv: kv[1].get("creado", 0))
        fuera += [k for k, _ in terminados[:sobran]]
    for k in fuera:
        _trabajos.pop(k, None)
    for clave, jid in list(_por_clave.items()):
        if jid not in _trabajos:
            _por_clave.pop(clave, None)


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
        return
    # El juez va DESPUÉS de `listo`, a propósito: el contrato con el panel es «se
    # guarda antes de marcar listo», y un juez lento antes de esa marca haría que
    # el sondeo se rindiera con la medición ya pagada. Solo juzga si hubo filas
    # NUEVAS: un término bloqueado conserva las viejas y no hay que rejuzgarlas
    # como si fueran una captura de hoy.
    if n > 0:
        _juzgar_tras_captura(jid, termino)


def _juzgar_tras_captura(jid: str, termino: str) -> None:
    """Juzga los rivales recién guardados. NUNCA cambia el paso del trabajo: un
    fallo aquí no puede hacer creer que la medición —ya pagada— falló.

    Deja en `juez` cómo terminó: `corriendo` → `listo` (todo juzgado) | `parcial`
    | `tope` | `fallo`. `parcial` son dos casos: se DETUVO por plazo, tope o
    fallos seguidos (el motivo va en `juez_detenido`), o llegó al final dejando
    `juez_pendientes` sin juzgar (por qué, en `juez_motivo`). El panel sigue
    preguntando después de `listo` mientras lea `corriendo`."""
    try:
        if not settings.competencia_juez_enabled or not competencia_juez.tablas_listas():
            return
        # El tope descuenta TODO el gasto del juez en 24 h —lote, botón y mejora
        # incluidos—, no solo el de este camino. Es una sola bolsa a propósito.
        tope = settings.competencia_juez_tope_diario_usd
        gastado = competencia_juez.gastado_24h()
        restante = tope - gastado
        if restante <= 0:
            # Sin esta línea el corte es mudo: los rivales salen «sin juzgar» y se
            # lee como gancho roto cuando lo que pasó es que el lote ya gastó el día.
            log.info("juez tras captura de %r: tope diario alcanzado (%.2f de %.2f USD "
                     "en 24 h, lote, botón y mejora incluidos)", termino, gastado, tope)
            _marcar_campo(jid, juez="tope")
            return
        _marcar_campo(jid, juez="corriendo")
        r = competencia_juez.juzgar_termino(
            termino, presupuesto=competencia_juez.Presupuesto(restante), hilos=2,
            plazo_s=_JUEZ_PLAZO_S, timeout=_JUEZ_TIMEOUT_S, intentos=1)
        # Un juicio que se detuvo NO es `listo`: los SKUs que no alcanzó ni
        # siquiera se intentaron, así que `sin_juzgar` no los cuenta y
        # `juez_pendientes` se queda corto. `parcial` + el motivo lo dicen.
        #
        # Tampoco lo es uno que llegó al final con rivales sin juzgar: la IA que
        # falla de 1 a 4 veces (a la 5ª se detiene), la respuesta inválida o
        # incompleta, o 1 o 2 SKUs que la base no guardó. El panel lo pintaba «ya
        # juzgados», en verde, junto al chip que decía «10 sin juzgar».
        detenido = r.get("detenido")
        sin_juzgar = r["sin_juzgar"]
        _marcar_campo(jid, juez="parcial" if detenido or sin_juzgar else "listo",
                      juez_detenido=detenido,
                      juez_motivo=_motivo_corto(r.get("ultimo_motivo")) if sin_juzgar else None,
                      juez_veredictos=r["veredictos"], juez_pendientes=sin_juzgar)
    except Exception as exc:                                  # noqa: BLE001
        log.warning("el juez de %r falló (la medición ya está guardada): %s", termino, exc)
        _marcar_campo(jid, juez="fallo")


def _motivo_corto(motivo: str | None) -> str | None:
    """El motivo sin el detalle del proveedor entre paréntesis: el trabajo lo lee
    cualquiera que mida, no solo un admin, y ese detalle puede traer el cuerpo de
    un error HTTP. «DeepSeek no respondió (HTTP 401: …).» → «DeepSeek no respondió»."""
    if not motivo:
        return None
    return motivo.split(" (", 1)[0].rstrip(".") or None


def _marcar_campo(jid: str, **extra: Any) -> None:
    """Como `_marcar`, pero sin tocar el paso."""
    with _lock:
        t = _trabajos.get(jid)
        if t:
            t.update({"actualizado": time.time(), **extra})


def _lanzar(clave: str, base: dict[str, Any], fn: Any, *args: Any) -> dict[str, Any]:
    """Registra el trabajo y arranca su hilo; si ya hay uno VIVO con esa clave,
    devuelve ese. El hilo hereda el contexto de quien lo pide (el actor de la
    sesión), que de otro modo se pierde al cruzar de hilo."""
    ahora = time.time()
    with _lock:
        _purgar()
        vivo = _trabajos.get(_por_clave.get(clave, ""))
        if vivo and vivo["paso"] not in _TERMINADOS:
            return dict(vivo)
        jid = uuid.uuid4().hex[:12]
        _trabajos[jid] = {"id": jid, "paso": "encolado", "paso_label": PASOS["encolado"],
                          "creado": ahora, "actualizado": ahora, "error": None, **base}
        _por_clave[clave] = jid
        copia = dict(_trabajos[jid])
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(fn, jid, *args),
                     name=f"comp-{jid}", daemon=True).start()
    return copia


def _correr_mejora(jid: str, sku: str) -> None:
    from services import competencia_mejora      # import tardío: evita el ciclo

    _marcar(jid, "mejorando")
    try:
        r = competencia_mejora.mejorar_sku(
            sku, presupuesto=competencia_juez.Presupuesto(0.05))
        _marcar(jid, "listo", resultado={k: r.get(k) for k in (
            "ok", "motivo", "sugerencias", "termino_ok", "sin_mejora", "bloqueados",
            "errores", "en_espera", "paginas", "detenido")})
    except Exception as exc:                                  # noqa: BLE001
        log.warning("la mejora de término de %s falló: %s", sku, exc)
        _marcar(jid, "error", error=str(exc)[:200])


def arrancar_mejora(sku: str) -> dict[str, Any]:
    """Lanza «buscar mejor término» para un SKU y devuelve el `jid`."""
    return _lanzar(f"mejora:{sku}", {"sku": sku, "resultado": None}, _correr_mejora, sku)


def arrancar(termino: str) -> dict[str, Any]:
    """Lanza el raspado y devuelve el `jid` de inmediato."""
    return _lanzar(f"busq:{termino}",
                   {"termino": termino, "filas": None, "vacio": None, "bloqueado": None,
                    "juez": None},
                   _correr, termino)
