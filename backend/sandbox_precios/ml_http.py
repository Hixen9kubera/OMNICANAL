"""Cliente HTTP de Mercado Libre para el laboratorio: SOLO GET, con freno y sin renovar tokens.

Por qué no se reutiliza `competencia_ml._get` ni `precios_venta._precio`: ante un
401 llaman a `meli.refrescar_token`, que ROTA el refresh_token de producción (ML lo
invalida al usarlo) — si el laboratorio lo hiciera, el backend se quedaría con un
refresh_token muerto y pararían las ventas de ML. Aquí, ante un 401 se relee el
token de kubera UNA vez (por si producción ya renovó) y, si sigue en 401, se
aborta con `TokenInvalido`.

El cupo de la API es por APLICACIÓN y lo compartimos con producción (webhooks,
barrido de precios, cron de visitas de las 12:00 UTC). Por eso:
- freno global de `LAB_ML_RPS` peticiones por segundo (default 4) entre todos los hilos;
- espera exponencial ante 429 (respeta Retry-After);
- pausa automática entre 11:50 y 12:30 UTC, cuando corre el cron de visitas.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from typing import Any

import httpx

log = logging.getLogger("laboratorio.ml_http")

API = "https://api.mercadolibre.com"
CUENTAS = ("BEKURA", "SANCORFASHION")
UID = {"BEKURA": "3072519654", "SANCORFASHION": "3064478475"}

_RPS = float(os.environ.get("LAB_ML_RPS", "4"))
_ESPERAS_429 = (0.5, 1, 2, 4, 8, 16)
_VENTANA_PAUSA_UTC = ((11, 50), (12, 30))


class TokenInvalido(RuntimeError):
    """El token de la cuenta dio 401 incluso releído. El laboratorio NO lo renueva."""


class _Freno:
    def __init__(self, rps: float):
        self.intervalo = 1.0 / max(rps, 0.1)
        self._lock = threading.Lock()
        self._siguiente = 0.0

    def esperar(self) -> None:
        with self._lock:
            ahora = time.monotonic()
            turno = max(ahora, self._siguiente)
            self._siguiente = turno + self.intervalo
        if turno > ahora:
            time.sleep(turno - ahora)


_freno = _Freno(_RPS)
_cliente: httpx.Client | None = None
_cliente_lock = threading.Lock()
_contador = {"get": 0, "429": 0, "401": 0, "error": 0}


def _http() -> httpx.Client:
    global _cliente
    with _cliente_lock:
        if _cliente is None:
            _cliente = httpx.Client(base_url=API, timeout=httpx.Timeout(25.0, connect=10.0),
                                    headers={"Accept": "application/json"})
        return _cliente


def _pausa_ventana() -> None:
    if os.environ.get("LAB_ML_SIN_PAUSA") == "1":
        return
    ahora = dt.datetime.now(dt.timezone.utc)
    (h1, m1), (h2, m2) = _VENTANA_PAUSA_UTC
    ini = ahora.replace(hour=h1, minute=m1, second=0, microsecond=0)
    fin = ahora.replace(hour=h2, minute=m2, second=0, microsecond=0)
    if ini <= ahora < fin:
        espera = (fin - ahora).total_seconds()
        log.info("ML: pausa %.0fs para no competir con el cron de visitas de producción", espera)
        time.sleep(espera)


def token(cuenta: str, releer: bool = False) -> str:
    from services import meli

    tok = meli.releer_token(cuenta) if releer else meli._access_token(cuenta)
    if not tok:
        raise TokenInvalido(f"sin token legible para {cuenta}")
    return tok


def get(ruta: str, params: dict[str, Any] | None = None, cuenta: str = "BEKURA",
        autenticado: bool = True) -> tuple[int, Any]:
    """GET a la API de ML. Devuelve (status, json|texto). Nunca renueva tokens.

    Lanza `TokenInvalido` si la cuenta da 401 después de releer el token.
    Errores de red: reintenta 2 veces y devuelve (0, str(error)).
    """
    cuenta = (cuenta or "BEKURA").upper()
    releido = False
    intento_429 = 0
    intento_red = 0
    while True:
        _pausa_ventana()
        _freno.esperar()
        headers = {"Authorization": f"Bearer {token(cuenta, releer=releido)}"} if autenticado else {}
        try:
            r = _http().get(ruta, params=params, headers=headers)
        except httpx.HTTPError as exc:
            intento_red += 1
            _contador["error"] += 1
            if intento_red > 2:
                return 0, str(exc)
            time.sleep(1.5 * intento_red)
            continue
        _contador["get"] += 1
        if r.status_code == 429:
            _contador["429"] += 1
            if intento_429 >= len(_ESPERAS_429):
                return 429, r.text
            ra = r.headers.get("Retry-After")
            espera = float(ra) if ra and ra.replace(".", "", 1).isdigit() else _ESPERAS_429[intento_429]
            intento_429 += 1
            time.sleep(min(espera, 30))
            continue
        if r.status_code == 401 and autenticado:
            _contador["401"] += 1
            if releido:
                raise TokenInvalido(f"{cuenta}: 401 en {ruta} con el token releído (no se renueva)")
            releido = True
            continue
        if r.status_code >= 500 and intento_red < 2:
            intento_red += 1
            time.sleep(1.0 * intento_red)
            continue
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text


def contadores() -> dict[str, int]:
    return dict(_contador)
