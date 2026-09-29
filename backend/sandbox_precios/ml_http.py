"""Cliente HTTP de Mercado Libre para el laboratorio: SOLO GET, con freno y sin renovar tokens.

Por qué no se reutiliza `competencia_ml._get` ni `precios_venta._precio`: ante un
401 llaman a `meli.refrescar_token`, que ROTA el refresh_token de producción (ML lo
invalida al usarlo) — si el laboratorio lo hiciera, el backend se quedaría con un
refresh_token muerto y pararían las ventas de ML. Aquí, ante un 401 se relee el
token de kubera UNA vez (por si producción ya renovó) y, si sigue en 401, se
aborta con `TokenInvalido`.

El cupo de la API es por APLICACIÓN y lo compartimos con producción (webhooks,
barrido de precios, cron de visitas de las 12:00 UTC). Por eso:
- freno global de `LAB_ML_RPS` peticiones por segundo (default 4, TOPE 5) entre todos
  los hilos;
- ante un 429 se frena a TODOS los hilos (no solo al que lo recibió): espera
  exponencial que respeta Retry-After;
- presupuesto diario de GET (`LAB_ML_PRESUPUESTO_DIA`, default 15,000; una
  extracción completa son ~8,500) persistido en `cache/ml_presupuesto.json`:
  al agotarse, `get` lanza `PresupuestoAgotado` en vez de seguir;
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

_RPS_TOPE = 5.0


def _rps() -> float:
    try:
        v = float(os.environ.get("LAB_ML_RPS") or 4)
    except ValueError:
        v = 4.0
    return max(0.1, min(v, _RPS_TOPE))


_RPS = _rps()
_PRESUPUESTO_DEFECTO = 15_000
_ESPERAS_429 = (0.5, 1, 2, 4, 8, 16)
_VENTANA_PAUSA_UTC = ((11, 50), (12, 30))


class TokenInvalido(RuntimeError):
    """El token de la cuenta dio 401 incluso releído. El laboratorio NO lo renueva."""


class PresupuestoAgotado(RuntimeError):
    """Se acabó el presupuesto diario de GET a ML del laboratorio."""


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

    def pausar(self, segundos: float) -> None:
        """Un 429 frena a TODOS los hilos: el siguiente turno de cualquiera es
        después de `segundos` (antes solo esperaba el hilo que lo recibió y los
        otros cinco seguían al ritmo normal)."""
        with self._lock:
            self._siguiente = max(self._siguiente, time.monotonic() + max(0.0, segundos))


class _Presupuesto:
    """GET por día (UTC) a ML, persistido en `cache/ml_presupuesto.json` cada
    `_CADA` llamadas: sobrevive a reinicios y a corridas repetidas de /recalcular."""

    _CADA = 50

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dia: str | None = None
        self._n = 0
        self._sin_guardar = 0

    @staticmethod
    def limite() -> int:
        try:
            return max(0, int(os.environ.get("LAB_ML_PRESUPUESTO_DIA") or _PRESUPUESTO_DEFECTO))
        except ValueError:
            return _PRESUPUESTO_DEFECTO

    @staticmethod
    def _ruta():
        from sandbox_precios import almacen

        return almacen.ruta("cache", "ml_presupuesto.json")

    def _cargar(self, dia: str) -> None:
        from sandbox_precios import almacen

        try:
            d = almacen.leer_json(self._ruta(), {}) or {}
        except (OSError, ValueError):
            d = {}
        self._dia, self._n = dia, int(d.get("get") or 0) if d.get("dia") == dia else 0

    def _guardar(self) -> None:
        from sandbox_precios import almacen

        try:
            almacen.escribir_json(self._ruta(), {"dia": self._dia, "get": self._n,
                                                 "limite": self.limite(), "actualizado_at": almacen.ahora_iso()})
            self._sin_guardar = 0
        except OSError as exc:  # el presupuesto nunca tumba una lectura por un disco lleno
            log.warning("ML: no se pudo guardar el presupuesto (%s)", exc)

    def consumir(self) -> None:
        dia = dt.datetime.now(dt.timezone.utc).date().isoformat()
        with self._lock:
            if self._dia != dia:
                self._cargar(dia)
            if self._n >= self.limite():
                self._guardar()
                raise PresupuestoAgotado(f"presupuesto diario de GET a ML agotado ({self._n}/{self.limite()}, "
                                         "LAB_ML_PRESUPUESTO_DIA)")
            self._n += 1
            self._sin_guardar += 1
            if self._sin_guardar >= self._CADA:
                self._guardar()

    def cerrar(self) -> None:
        with self._lock:
            if self._dia and self._sin_guardar:
                self._guardar()

    def usado(self) -> dict[str, Any]:
        with self._lock:
            return {"dia": self._dia, "get": self._n, "limite": self.limite()}


_freno = _Freno(_RPS)
_presupuesto = _Presupuesto()
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

    Lanza `TokenInvalido` si la cuenta da 401 después de releer el token y
    `PresupuestoAgotado` si ya se gastó el presupuesto diario de GET.
    Errores de red: reintenta 2 veces y devuelve (0, str(error)).
    """
    cuenta = (cuenta or "BEKURA").upper()
    releido = False
    intento_429 = 0
    intento_red = 0
    while True:
        _pausa_ventana()
        _presupuesto.consumir()
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
            _freno.pausar(min(espera, 30))  # frena a todos los hilos; `esperar()` duerme este
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


def presupuesto() -> dict[str, Any]:
    """{dia, get, limite} del presupuesto diario (y lo deja guardado en disco)."""
    _presupuesto.cerrar()
    return _presupuesto.usado()
