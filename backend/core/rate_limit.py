"""
rate_limit.py — Freno de ráfagas para los webhooks, en memoria.

POR QUÉ EXISTE
--------------
Auditoría 2026-08-27: un POST a `/api/webhooks/ml` no pide credencial (ML no
puede mandar nuestro token) y NADA cuenta cuántos llegan. Una prueba en vivo
mandó 20 golpes seguidos y ninguno se frenó. Un flood real —miles por segundo—
puede saturar el backend, llenar la base de basura y quemar llamadas salientes
a ML con nuestro token.

LA TENSIÓN QUE HAY QUE RESPETAR
------------------------------
El webhook de ML tiene una GUARDA ABSOLUTA: si le devolvemos algo que no sea
200, ML reintenta 1 h y después DESHABILITA el topic, y dejan de entrar ventas
SIN error visible. Así que un límite mal calibrado no es "molesto": corta las
ventas.

Por eso el diseño se apoya en dos hechos MEDIDOS (72 h de producción):
  · tráfico normal:  15 webhooks/min
  · pico real de ML: 111 webhooks/min  (ráfaga de hasta 20/seg)

DOS CARRILES, según quién llama:
  · CARRIL ML (1200/min):  para webhooks que traen un `user_id` de nuestros
    vendedores conocidos. Es ML legítimo — se le da muchísimo aire (11× su
    pico) para que jamás lo roce, ni en el día más movido.
  · CARRIL GENERAL (150/min): para todo lo demás. Un desconocido que floodea
    se topa con esto rápido. Es la mitad del carril de ML por diseño: apretar
    al que no reconocemos, dar espacio al que sí.

Ambos carriles son POR IP: el flood de un atacante llena SU cubo, y ML (otra IP,
otro carril) sigue pasando.

EL MATIZ HONESTO DEL user_id
----------------------------
El `user_id` viaja en el cuerpo del POST, y nuestros IDs de vendedor están en el
código (repo público). Un atacante que los lea puede ponerlos y colarse al
carril de ML (1200 en vez de 150). No es un agujero: 1200 SIGUE siendo un
límite —un flood se frena igual, solo un poco más arriba— y el carril general
frena al 99% de los bots que no se molestan en leer el repo. La identificación
FUERTE del remitente es el paso 2 (verificar contra la propia API de ML); este
freno no la sustituye, solo pone el primer muro.

LA IP, Y EL ALCANCE HONESTO DE ESTE FRENO
-----------------------------------------
Detrás del proxy de Railway, la IP directa es la del proxy, no la del cliente.
Se lee `X-Forwarded-For` (primer valor = cliente original que ve el edge).

Esto frena bien un flood desde una IP (o pocas): esa IP llena su cubo y se
auto-bloquea, mientras ML (otra IP) sigue pasando. Lo que NO frena es un flood
que falsifica el header y rota miles de IPs: cada IP falsa estrena cubo. No se
resuelve aquí a propósito. Se consideró un cubo GLOBAL de respaldo y se
DESCARTÓ: un global que se agota frenaría también a ML, y cortar ventas para
defenderse de un flood sofisticado es un mal negocio. Ese flood lo atrapan las
capas correctas: la validación de remitente (paso 2, otro commit, que descarta
lo que no venga de nuestras cuentas) y la restricción de red de Supabase. Este
módulo hace una cosa —frenar la ráfaga simple— y no finge hacer más.

NUNCA REVIENTA
--------------
Si algo falla aquí dentro, `permite()` devuelve True (deja pasar). Un bug en el
limitador no puede convertirse en una caída — la misma regla que el middleware.
"""
from __future__ import annotations

import threading
import time

# ── Parámetros (medidos, ver encabezado) ────────────────────────────────────
# Carril ML: para vendedores conocidos. 11× su pico (111/min) — inalcanzable.
_ML_POR_MINUTO = 1200
_ML_BURST = 300
# Carril general: para desconocidos. Aprieta rápido.
_GEN_POR_MINUTO = 150
_GEN_BURST = 60

# Tope de IPs recordadas. Un atacante que rota IPv6 podría inflar el dict; al
# pasar el tope se purgan las más viejas. No es exacto, es un techo de memoria.
_MAX_IPS = 20_000

_lock = threading.Lock()
# Un cubo por (ip, carril): así una IP que manda ML y basura no mezcla cuentas.
_cubos: dict[tuple[str, bool], list[float]] = {}   # (ip, es_ml) -> [fichas, ts]


def _ahora() -> float:
    return time.monotonic()


def _toma_ficha(estado: list[float], burst: float, recarga: float, t: float) -> bool:
    """Token bucket: recarga por tiempo transcurrido, consume una ficha."""
    fichas, ultimo = estado
    if ultimo == 0.0:
        fichas, ultimo = burst, t
    fichas = min(burst, fichas + (t - ultimo) * recarga)
    if fichas >= 1.0:
        estado[0] = fichas - 1.0
        estado[1] = t
        return True
    estado[0] = fichas
    estado[1] = t
    return False


def ip_de(request) -> str:
    """La IP del cliente. Detrás del proxy de Railway viene en X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:45]     # cabe una IPv6
    cli = getattr(request, "client", None)
    return (getattr(cli, "host", "") or "desconocida")[:45]


def permite(ip: str, es_ml: bool = False) -> bool:
    """
    ¿Se le deja pasar esta petición? True = sí, False = frenar (429).

    `es_ml=True` usa el carril generoso (vendedor conocido); False, el general.
    Jamás lanza: ante cualquier error, deja pasar.
    """
    burst = _ML_BURST if es_ml else _GEN_BURST
    recarga = (_ML_POR_MINUTO if es_ml else _GEN_POR_MINUTO) / 60.0
    try:
        t = _ahora()
        with _lock:
            clave = (ip, es_ml)
            estado = _cubos.get(clave)
            if estado is None:
                if len(_cubos) >= _MAX_IPS:
                    _purgar(t)
                estado = [burst, 0.0]
                _cubos[clave] = estado
            return _toma_ficha(estado, burst, recarga, t)
    except Exception:   # noqa: BLE001 — nunca tumba el sitio
        return True


def _purgar(t: float) -> None:
    """Quita cubos sin uso reciente (>120s). Llamado con el lock tomado."""
    muertos = [k for k, (_fichas, ultimo) in _cubos.items() if (t - ultimo) > 120]
    for k in muertos:
        _cubos.pop(k, None)
    # Si aún así está lleno (todos activos), tira la mitad más vieja.
    if len(_cubos) >= _MAX_IPS:
        viejos = sorted(_cubos.items(), key=lambda kv: kv[1][1])[: _MAX_IPS // 2]
        for k, _ in viejos:
            _cubos.pop(k, None)


def _reset_para_pruebas() -> None:
    """Solo para el laboratorio: vacía los cubos."""
    with _lock:
        _cubos.clear()
