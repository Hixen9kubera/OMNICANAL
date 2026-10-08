# -*- coding: utf-8 -*-
"""
recuperar_devoluciones_ml.py — Recupera en el SANDBOX lo que las 5 fallas de la
captura de devoluciones de ML dejaron fuera (5-oct-2026).

DRY-RUN POR DEFECTO. Sin `--aplicar` no escribe NADA, ni siquiera en el
sandbox: `guardar` es un colector y la fase local solo cuenta. Los cruces
(`channel.orders`, `core.accounts` y la búsqueda de duplicados) sí LEEN el
sandbox.

LAS TRES FASES (`--fases`, siempre en este orden)
─────────────────────────────────────────────────
  local        0 llamadas a ML. Por cada fila de `channel.returns` vuelve a
               derivar `estado` (ya con `expired` → 'rechazada', F4) y `destino`
               (`shipments[].destination.name`, F5) DESDE EL PAYLOAD GUARDADO, con
               las mismas `_estado` y `_destino` del flujo vivo. Solo escribe lo
               que cambia; nunca saca a una fila de 'reembolsada' (el dinero
               manda), nunca la mete ahí (eso necesita la fecha real del
               reembolso: lo hace el refresco) y nunca deja `destino` en NULL.
  abiertas     F3. `refrescar_abiertas(tope=None, horas=0)`: relee con ML todo
               lo no terminal (abierta, en tránsito, recibida y reembolsadas con
               la caja en camino) abierto hace a lo más 120 días. Va ANTES de
               las mediaciones: así no relee las que esa fase acaba de escribir
               (≈3 GET por fila sin ganar nada, que el dry-run no veía).
  mediaciones  F1. `claims/search?type=mediations` por tramos mensuales de los
               últimos `--dias`, y cada claim SIN FILA por `sincronizar(...,
               mediaciones=True)`: se guarda si trae devolución. Las que ya
               tienen fila no se releen (las cubrió la fase anterior): una
               corrida detenida a media fase se repite sin repetir lo hecho.

Así el conteo de llamadas del dry-run es una COTA de lo que gastará `--aplicar`
(en el dry-run la fase local no escribe, y el refresco relee también las filas
que `--aplicar` ya habría pasado a terminales).

CANDADOS (todos abortan ANTES de la primera llamada a ML)
──────────────────────────────────────────────────────────
1. DESTINO = SANDBOX. `SANDBOX_DB_URL` debe traer `yvootpbz` y no `tukwcvsi`;
   `--aplicar` lo vuelve a comprobar en cada escritura. NO HAY OPCIÓN para
   escribir en producción: hacerlo exige cambiar el código y su propia acta.
2. CONFIGURACIÓN AISLADA. `APP_ENV` no puede ser `staging` (cargaría
   `env.staging`, que trae MySQL y Woo de PRODUCCIÓN) y no debe existir `.env`
   ni `.env.amazon` en la raíz. `SUPABASE_DB_URL` se fuerza al sandbox antes de
   importar `config`, y después se exige que MySQL, Woo y WordPress estén vacíos
   y que ningún ajuste apunte a `tukwcvsi`.
3. CABLES TRAMPA. `services.db` (MySQL) y las funciones de token de `meli`
   (leer, renovar) LANZAN si alguien las llama: nada lee el MySQL ni renueva un
   token. Este script NUNCA renueva: la app del token debe ser la del entorno y
   renovar desde fuera tumba ML a las 6 h (regla 8).
4. TOKEN DE SOLO LECTURA. Una conexión a `PROD_DB_URL` (debe traer
   `tukwcvsi`): `set transaction read only` — por transacción, nunca la sesión
   (regla 13) —, `select` de `ops.ml_tokens`, ROLLBACK y cierre. Se descifra con
   Fernet en memoria. Si el token tiene más de `--max-edad-token-min` minutos,
   aborta: muere a las 6 h y la corrida completa dura ~35 min.
5. TRANSPORTE VIGILADO, el único por el que sale todo: solo GET y solo a
   api.mercadolibre.com; a lo más `--ritmo` GET/s; `--tope-llamadas`; un 401 o
   un 403 DETIENE la corrida (salvo el 401 interno ya medido de `/returns`,
   «client:shipments», que se cuenta). Detenido, rechaza toda petición
   siguiente y ya no se escribe nada; el script sale con 2 y dice por qué.

QUÉ IMPRIME: solo ids de claim y conteos. Nunca nombres, direcciones,
teléfonos, DSNs, llaves ni tokens. Eso vale también para el LOG: un filtro en
el handler quita las trazas y corta cada mensaje antes de `DETAIL`/`Failing
row` (el DETAIL de Postgres trae la fila entera, payload incluido); de una
falla solo sale su tipo y el claim.

USO (Git Bash; los secretos llegan por stdin como CLAVE=valor, nunca en la
línea de comandos ni en un archivo):

    { grep '^SUPABASE_DB_URL=' ../OMNICANAL/env.staging | sed 's/^SUPABASE_DB_URL=/SANDBOX_DB_URL=/'
      railway variable list -p <proyecto> -s BackendOmnicanal -e production --kv \\
        | grep -E '^(SUPABASE_DB_URL|DB_ENCRYPTION_KEY)=' | sed 's/^SUPABASE_DB_URL=/PROD_DB_URL=/'
    } | PYTHONIOENCODING=utf-8 backend/.venv/Scripts/python.exe \\
          backend/scripts/recuperar_devoluciones_ml.py --limite-claims 20

    ... --fases local                      # solo la fase local (no lee producción)
    ... --aplicar --fases local            # escribe en el SANDBOX, por fases
    Opciones: --dias 90 --cuenta BEKURA --tope-llamadas 7500 --ritmo 3
              --max-edad-token-min 300

Leer `PROD_DB_URL` es una lectura de producción: requiere la autorización de
Eduardo. Con `--fases local` no se pide ni se usa.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx

BACKEND = Path(__file__).resolve().parents[1]
RAIZ = BACKEND.parent
REF_SANDBOX = "yvootpbz"
REF_PROD = "tukwcvsi"
CUENTAS = ("BEKURA", "SANCORFASHION")
HOST_ML = "api.mercadolibre.com"
FASES = ("local", "abiertas", "mediaciones")
CANAL = "mercado_libre"
MAX_IDS = 20

# Lo que no puede traer valor en esta corrida: MySQL, Woo y WordPress.
_AJUSTES_PROHIBIDOS = ("db_host", "db_user", "db_password", "db_name",
                       "wc_url", "wc_consumer_key", "wc_consumer_secret",
                       "wp_user", "wp_app_password",
                       "wpdb_host", "wpdb_user", "wpdb_password", "wpdb_name")


class Candado(Exception):
    """Un candado del script no se cumple: se aborta antes de llamar a ML."""


class CorridaDetenida(RuntimeError):
    """El transporte se detuvo (401/403, tope, método o host prohibido).

    Es RuntimeError A PROPÓSITO, no `httpx.TransportError`: `motivo_de` atrapa
    los errores de transporte y seguiría hasta guardar; esto tiene que subir
    hasta el `except` de `sincronizar`, que no escribe."""


# ── El log, sin datos ────────────────────────────────────────────────────────
#
# Con `--aplicar`, si `_guardar` falla en el sandbox (CHECK, NOT NULL…),
# `sincronizar` hace `log.exception`, y el texto de psycopg2 trae `DETAIL:
# Failing row contains (...)` con la fila ENTERA: el payload con el claim, sus
# `players[].user_id`, la devolución y el detalle. `_limpiar_motivo` solo
# limpia el informe; esto limpia lo que sale por el log.

_CORTES = ("DETAIL", "Failing row")


class FiltroSinDatos(logging.Filter):
    """Deja la primera línea del mensaje, cortada antes de `DETAIL`/`Failing
    row`, y cambia la traza por el TIPO de la excepción."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — un formato roto no tumba la corrida
            msg = str(record.msg)
        msg = (msg.splitlines() or [""])[0]
        for corte in _CORTES:
            msg = msg.split(corte)[0]
        msg = msg.rstrip()
        exc = record.exc_info[0] if record.exc_info else None
        if exc is not None:
            msg += f" [{exc.__name__}]"
        record.msg, record.args = msg, None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def instalar_filtro_log() -> int:
    """El filtro en TODOS los handlers que existan (el de la raíz y los que
    algún módulo haya puesto al importarse). Devuelve cuántos cubrió."""
    filtro = FiltroSinDatos()
    loggers = [logging.getLogger(), *(lg for lg in logging.root.manager.loggerDict.values()
                                       if isinstance(lg, logging.Logger))]
    n = 0
    for lg in loggers:
        for h in lg.handlers:
            if not any(isinstance(f, FiltroSinDatos) for f in h.filters):
                h.addFilter(filtro)
            n += 1
    return n


# ── Candados 1 y 2: destino y configuración ─────────────────────────────────

def verificar_destino(url: str) -> None:
    if not url:
        raise Candado("falta SANDBOX_DB_URL en stdin.")
    if REF_PROD in url:
        raise Candado("SANDBOX_DB_URL apunta a PRODUCCIÓN (tukwcvsi). "
                      "Este script solo escribe en el sandbox.")
    if REF_SANDBOX not in url:
        raise Candado("SANDBOX_DB_URL no es el sandbox (yvootpbz).")


def aislar_config(sandbox_url: str, *, env: dict | None = None,
                  raiz: Path = RAIZ) -> None:
    """Antes de `import config`: nada de env.staging ni .env de la raíz."""
    env = os.environ if env is None else env
    if (env.get("APP_ENV") or "").strip().lower() == "staging":
        raise Candado("APP_ENV=staging cargaría env.staging (MySQL y Woo de "
                      "PRODUCCIÓN). Corre sin APP_ENV.")
    for nombre in (".env", ".env.amazon"):
        if (raiz / nombre).exists():
            raise Candado(f"existe {nombre} en la raíz ({raiz.name}): config lo "
                          "cargaría. Corre desde un worktree sin él.")
    env["SUPABASE_DB_URL"] = sandbox_url


def verificar_config(settings: Any, sandbox_url: str) -> None:
    """Después de `import config`: el sandbox, y nada de MySQL/Woo/producción."""
    if settings.supabase_db_url != sandbox_url:
        raise Candado("settings.supabase_db_url no quedó en el sandbox.")
    llenos = [c for c in _AJUSTES_PROHIBIDOS
              if str(getattr(settings, c, "") or "").strip()]
    if llenos:
        raise Candado(f"ajustes de MySQL/Woo con valor (vienen del entorno): "
                      f"{', '.join(llenos)}. Corre en un shell limpio.")
    try:
        valores = settings.model_dump()
    except AttributeError:
        valores = {}
    hacia_prod = sorted(k for k, v in valores.items()
                        if k != "supabase_prod_ref" and isinstance(v, str)
                        and REF_PROD in v)
    if hacia_prod:
        raise Candado(f"ajustes que apuntan a producción: {', '.join(hacia_prod)}.")


# ── Candado 3: cables trampa ────────────────────────────────────────────────

_TRAMPA_DB = ("fetch_one", "fetch_all", "fetch_scalar", "execute", "get_cursor",
              "_get_pool")
_TRAMPA_MELI = ("_access_token", "access_token_async", "_access_token_leer",
                "_leer_token_y_fecha", "_renovar_con_candado", "refrescar_token",
                "_refrescar_token_sin_cache", "_refrescar_solo_kubera",
                "releer_token")


def poner_cables_trampa(db: Any, meli: Any) -> list[str]:
    """MySQL y los tokens de `meli` LANZAN si alguien los toca."""
    def _trampa(nombre: str) -> Callable[..., Any]:
        def _f(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError(f"CABLE TRAMPA: {nombre} no se puede llamar "
                               "desde la recuperación")
        return _f
    puestos = []
    for mod, nombres, pref in ((db, _TRAMPA_DB, "db"), (meli, _TRAMPA_MELI, "meli")):
        for n in nombres:
            if hasattr(mod, n):
                setattr(mod, n, _trampa(f"{pref}.{n}"))
                puestos.append(f"{pref}.{n}")
    return puestos


# ── Candado 4: el token, de solo lectura ────────────────────────────────────

def _leer_filas_tokens(prod_url: str) -> list[tuple[str, str, Any]]:
    import psycopg2
    try:
        cn = psycopg2.connect(prod_url, connect_timeout=20)
    except Exception as exc:  # noqa: BLE001 — el mensaje podría traer host/usuario
        raise Candado(f"no pude conectar a producción para leer los tokens "
                      f"({type(exc).__name__}).") from None
    try:
        cur = cn.cursor()
        # POR TRANSACCIÓN, nunca de sesión (regla 13): muere con el rollback.
        cur.execute("set transaction read only")
        cur.execute(
            """select distinct on (cuenta) cuenta, access_token, updated_at
                 from ops.ml_tokens
                where cuenta = any(%s)
                order by cuenta, updated_at desc nulls last""",
            (list(CUENTAS),))
        return [(str(c), t, u) for c, t, u in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        raise Candado(f"no pude leer ops.ml_tokens ({type(exc).__name__}).") from None
    finally:
        try:
            cn.rollback()
        finally:
            cn.close()


def descifrar_tokens(filas: Iterable[tuple[str, str, Any]], clave: str, *,
                     cuentas: Iterable[str], max_edad_min: float,
                     ahora: datetime | None = None) -> dict[str, str]:
    """Descifra en memoria y aborta si falta alguno o si alguno es viejo."""
    from cryptography.fernet import Fernet, InvalidToken
    try:
        fernet = Fernet(clave.encode())
    except Exception:  # noqa: BLE001 — jamás se imprime la llave
        raise Candado("DB_ENCRYPTION_KEY no es una llave Fernet válida.") from None
    ahora = ahora or datetime.now(timezone.utc)
    por_cuenta = {c: (t, u) for c, t, u in filas}
    tokens: dict[str, str] = {}
    for cuenta in cuentas:
        raw, upd = por_cuenta.get(cuenta, (None, None))
        if not raw:
            raise Candado(f"{cuenta}: no hay token en ops.ml_tokens.")
        if upd is None:
            raise Candado(f"{cuenta}: el token no tiene updated_at; no se sabe su edad.")
        if upd.tzinfo is None:
            upd = upd.replace(tzinfo=timezone.utc)
        edad = (ahora - upd).total_seconds() / 60
        if edad > max_edad_min:
            raise Candado(f"{cuenta}: el token tiene {edad:.0f} min (> {max_edad_min:.0f}). "
                          "Muere a las 6 h y este script NO renueva: espera a que "
                          "producción lo renueve y vuelve a correr.")
        raw = str(raw)
        if raw.startswith("gAAAAA"):
            try:
                tokens[cuenta] = fernet.decrypt(raw.encode()).decode()
            except InvalidToken:
                raise Candado(f"{cuenta}: el token no se pudo descifrar con esa llave.") from None
        else:
            tokens[cuenta] = raw
    return tokens


# ── Candado 5: el transporte vigilado ───────────────────────────────────────

def normalizar_ruta(path: str) -> str:
    p = re.sub(r"/claims/reasons/[^/]+", "/claims/reasons/{id}", path)
    return re.sub(r"/\d+", "/{id}", p)


class TransporteVigilado(httpx.AsyncBaseTransport):
    """Todo lo que sale a ML pasa por aquí. Ver el candado 5 del encabezado."""

    def __init__(self, interno: httpx.AsyncBaseTransport | None = None, *,
                 ritmo: float = 3.0, tope: int = 7500,
                 reloj: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], Any] = asyncio.sleep) -> None:
        self._interno = interno or httpx.AsyncHTTPTransport()
        self._intervalo = 1.0 / ritmo if ritmo and ritmo > 0 else 0.0
        self._tope = tope
        self._reloj, self._dormir = reloj, dormir
        self._candado: asyncio.Lock | None = None
        self._ultimo: float | None = None
        self.llamadas = 0
        self.por_ruta: Counter = Counter()
        self.internos_401 = 0
        self.detenido: str | None = None

    def _detener(self, por_que: str) -> CorridaDetenida:
        if not self.detenido:
            self.detenido = por_que
        return CorridaDetenida(self.detenido)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.detenido:
            raise CorridaDetenida(self.detenido)
        if request.method != "GET":
            raise self._detener(f"método {request.method} prohibido")
        if request.url.host != HOST_ML:
            raise self._detener(f"host {request.url.host} prohibido")
        if self.llamadas >= self._tope:
            raise self._detener(f"tope de {self._tope} llamadas alcanzado")
        if self._candado is None:
            self._candado = asyncio.Lock()
        async with self._candado:
            if self._ultimo is not None and self._intervalo:
                espera = self._ultimo + self._intervalo - self._reloj()
                if espera > 0:
                    await self._dormir(espera)
            self._ultimo = self._reloj()
            self.llamadas += 1
        resp = await self._interno.handle_async_request(request)
        ruta = normalizar_ruta(request.url.path)
        self.por_ruta[(ruta, resp.status_code)] += 1
        if resp.status_code in (401, 403):
            cuerpo = await resp.aread()
            if (resp.status_code == 401 and request.url.path.endswith("/returns")
                    and b"client:shipments" in cuerpo):
                # El 401 INTERNO de ML ya medido (claim 5573786449): su servicio
                # de devoluciones no pudo hablar con el de envíos. No es nuestro
                # token: un token vencido da 401 en /claims/{id} antes.
                self.internos_401 += 1
            else:
                self._detener(f"{resp.status_code} en {ruta}")
        return resp

    async def aclose(self) -> None:
        await self._interno.aclose()


# ── Lo que imprime: formas del payload (solo enumeraciones y conteos) ───────

class Formas:
    """Lo que la lectura de producción negada no pudo contestar (SPEC §0.2)."""

    def __init__(self) -> None:
        self._por_claim: dict[str, dict[str, Any]] = {}

    def registrar(self, crudo: dict[str, Any] | None) -> None:
        if not crudo:
            return
        claim = crudo.get("claim") or {}
        devol = crudo.get("returns") or {}
        envios = [s for s in (devol.get("shipments") or []) if isinstance(s, dict)]
        rel = claim.get("related_entities") or []
        self._por_claim[str(claim.get("id"))] = {
            "tipo": claim.get("type") or "?",
            "related": tuple(sorted(str(e.get("type") if isinstance(e, dict) else e)
                                    for e in rel)),
            "status": devol.get("status"),
            "money": devol.get("status_money"),
            "n_envios": len(envios),
            "envios": tuple(f"{s.get('type') or '?'}→"
                            f"{(s.get('destination') or {}).get('name') or '?'}"
                            for s in envios),
            "returns_error": crudo.get("returns_error"),
        }

    def imprimir(self) -> None:
        f = list(self._por_claim.values())
        if not f:
            return
        print(f"\n  FORMA DEL PAYLOAD ({len(f)} claims leídos)")
        print(f"    tipos de claim        : {dict(Counter(x['tipo'] for x in f))}")
        print(f"    related_entities      : {dict(Counter(x['related'] for x in f))}")
        print(f"    status × status_money : "
              f"{dict(Counter((x['status'], x['money']) for x in f))}")
        print(f"    envíos por devolución : {dict(Counter(x['n_envios'] for x in f))}")
        print(f"    tramos (tipo→destino) : {dict(Counter(x['envios'] for x in f if x['envios']))}")
        print(f"    status_money de expired: "
              f"{dict(Counter(x['money'] for x in f if x['status'] == 'expired'))}")
        print(f"    /returns con error    : {dict(Counter(x['returns_error'] for x in f if x['returns_error']))}")


def espiar_traer(dml: Any, formas: Formas) -> None:
    """Envuelve `dml._traer` para anotar la forma de cada claim leído."""
    original = dml._traer

    async def _traer(cli, cab, claim_id):  # noqa: ANN001
        crudo = await original(cli, cab, claim_id)
        formas.registrar(crudo)
        return crudo

    dml._traer = _traer


# ── Guardar: colector (dry-run) o sandbox (--aplicar) ───────────────────────

class Colector:
    """El `guardar` del dry-run: junta lo que se escribiría, sin escribir.

    `retirar` es el otro lado de la escritura (`sincronizar` lo busca en el
    `guardar` inyectado): la fila de un claim duplicado que se borraría. Aquí
    solo se anota, y solo si esa fila existe (con `dml`, se LEE el sandbox)."""

    def __init__(self, transporte: TransporteVigilado | None = None,
                 dml: Any = None) -> None:
        self.cabeceras: list[dict[str, Any]] = []
        self.lineas: list[dict[str, Any]] = []
        self.retiros: list[tuple[str, str, str]] = []    # (cuenta, perdedor, ganador)
        self._transporte = transporte
        self._dml = dml

    def _vivo(self) -> None:
        if self._transporte is not None and self._transporte.detenido:
            raise CorridaDetenida(self._transporte.detenido)

    def __call__(self, cab: dict[str, Any], lineas: list[dict[str, Any]]) -> None:
        self._vivo()
        self.cabeceras.append(cab)
        self.lineas.extend(lineas)

    def retirar(self, cuenta: str, perdedor: str, ganador: str, rid: Any) -> int:
        """Dry-run: 0 filas borradas; se anota para el informe."""
        self._vivo()
        if self._dml is None or str(perdedor) in self._dml._ya_guardados(cuenta, [perdedor]):
            self.retiros.append((cuenta, str(perdedor), str(ganador)))
        return 0


class GuardarEnSandbox(Colector):
    """El `guardar` de `--aplicar`: vuelve a comprobar el destino en cada
    escritura (y en cada retiro de un duplicado) y no escribe nada si el
    transporte se detuvo."""

    def __init__(self, dml: Any, settings: Any,
                 transporte: TransporteVigilado | None = None) -> None:
        super().__init__(transporte, dml)
        self._settings = settings

    def __call__(self, cab: dict[str, Any], lineas: list[dict[str, Any]]) -> None:
        self._vivo()
        verificar_destino(self._settings.supabase_db_url)
        self._dml._guardar(cab, lineas)
        self.cabeceras.append(cab)
        self.lineas.extend(lineas)

    def retirar(self, cuenta: str, perdedor: str, ganador: str, rid: Any) -> int:
        self._vivo()
        verificar_destino(self._settings.supabase_db_url)
        n = self._dml._retirar_duplicada(cuenta, perdedor, ganador, rid)
        if n:
            self.retiros.append((cuenta, str(perdedor), str(ganador)))
        return n


# ── Fase local (F4 y F5 sobre lo existente, 0 llamadas a ML) ────────────────

def cambios_locales(filas: Iterable[dict[str, Any]], dml: Any) -> list[dict[str, Any]]:
    """De las filas guardadas (con su payload) saca lo que hay que cambiar.

    · `estado`: se vuelve a derivar con `_estado`. Nunca se toca una fila
      'reembolsada' (el dinero manda) y nunca se mete a una ahí (eso necesita
      `reembolsada_at` real; lo hace el refresco con ML).
    · `destino`: con `_destino`. Solo de NULL/otro valor a un valor; nunca a NULL.
    """
    out: list[dict[str, Any]] = []
    for f in filas:
        p = f.get("payload") or {}
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except ValueError:
                p = {}
        claim = p.get("claim") or {}
        devol = p.get("returns") or {}
        cambio: dict[str, Any] = {}
        actual = f.get("estado")
        if actual != "reembolsada":
            nuevo = dml._estado(devol.get("status"), devol.get("status_money"),
                                claim.get("status"))
            if nuevo != actual and nuevo != "reembolsada":
                cambio["estado"] = (actual, nuevo)
        dest = dml._destino(devol)
        if dest and dest != f.get("destino"):
            cambio["destino"] = (f.get("destino"), dest)
        if cambio:
            out.append({"cuenta": f["cuenta"],
                        "external_return_id": str(f["external_return_id"]),
                        "estado_canal": f.get("estado_canal"),
                        "es_fulfillment": f.get("es_fulfillment"), **cambio})
    return out


def leer_filas_locales(sdb: Any, cuentas: Iterable[str]) -> list[dict[str, Any]]:
    return sdb.fetch_all(
        """select cuenta, external_return_id, estado, estado_canal, destino,
                  es_fulfillment, payload
             from channel.returns
            where canal = %s and cuenta = any(%s)""",
        (CANAL, list(cuentas)))


def sql_local(c: dict[str, Any]) -> tuple[str, tuple]:
    """El UPDATE de un cambio local. El estado se cambia solo si la fila SIGUE
    en el estado que se leyó (si algo la movió entre tanto, no se pisa; y como
    el leído nunca es 'reembolsada', jamás se saca a una de ahí). `destino`
    con coalesce: jamás a NULL. Los triggers de la 0049 sellan
    `actualizado_at` y escriben la transición en `return_history`."""
    sets, vals = [], []
    if "estado" in c:
        sets.append("estado = %s")
        vals.append(c["estado"][1])
    if "destino" in c:
        sets.append("destino = coalesce(%s, destino)")
        vals.append(c["destino"][1])
    where = "canal = %s and cuenta = %s and external_return_id = %s"
    params = [*vals, CANAL, c["cuenta"], c["external_return_id"]]
    if "estado" in c:
        where += " and estado = %s and estado <> 'reembolsada'"
        params.append(c["estado"][0])
    return f"update channel.returns set {', '.join(sets)} where {where}", tuple(params)


def aplicar_locales(sdb: Any, settings: Any, cambios: list[dict[str, Any]]) -> int:
    """Una sola transacción, en el sandbox comprobado."""
    verificar_destino(settings.supabase_db_url)
    n = 0
    with sdb.get_cursor() as cur:
        for c in cambios:
            cur.execute(*sql_local(c))
            n += cur.rowcount
    return n


def imprimir_locales(cambios: list[dict[str, Any]]) -> None:
    est = Counter((c["estado"][0], c["estado"][1], c.get("estado_canal"))
                  for c in cambios if "estado" in c)
    dst = Counter((c["destino"][0], c["destino"][1], bool(c.get("es_fulfillment")))
                  for c in cambios if "destino" in c)
    print(f"  filas con cambio: {len(cambios)}")
    print(f"    estado (antes → después · estado_canal): "
          f"{ {f'{a}→{b} · {k}': n for (a, b, k), n in est.items()} }")
    print(f"    destino (antes → después · FULL): "
          f"{ {f'{a}→{b} · FULL={k}': n for (a, b, k), n in dst.items()} }")
    raros = [c["external_return_id"] for c in cambios
             if "estado" in c and c["estado"][1] != "rechazada"][:MAX_IDS]
    if raros:
        print(f"    cambios de estado que NO son a 'rechazada' (revisar): {raros}")


# ── Fases que hablan con ML ─────────────────────────────────────────────────

def meses(desde: str, hasta: str) -> list[tuple[str, str]]:
    """Parte el rango en tramos mensuales [(desde, hasta), …]."""
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    tramos, cur = [], d0
    while cur <= d1:
        fin_mes = (cur.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        fin = min(fin_mes, d1)
        tramos.append((cur.isoformat(), fin.isoformat()))
        cur = fin + timedelta(days=1)
    return tramos


async def fase_mediaciones(dml: Any, cli: httpx.AsyncClient, tokens: dict[str, str],
                           guardar: Callable[[dict, list[dict]], None], *,
                           cuentas: Iterable[str], dias: int, limite: int | None,
                           hoy: date | None = None,
                           transporte: TransporteVigilado | None = None
                           ) -> list[dict[str, Any]]:
    """F1: las mediaciones de los últimos `dias` SIN FILA, una por una
    (concurrencia 1: el ritmo lo pone el transporte). Las que ya tienen fila
    las cubrió la fase `abiertas` (o son terminales): no se releen."""
    hoy = hoy or datetime.now(timezone.utc).date()
    desde, hasta = (hoy - timedelta(days=dias)).isoformat(), hoy.isoformat()
    resultados: list[dict[str, Any]] = []
    for cuenta in cuentas:
        cab = {"Authorization": f"Bearer {tokens[cuenta]}"}
        ids: list[str] = []
        for m0, m1 in meses(desde, hasta):
            if transporte is not None and transporte.detenido:
                return resultados
            try:
                encontrados = await dml._buscar(cli, cab, m0, m1, tipo="mediations")
            except CorridaDetenida:
                return resultados
            for c in encontrados:
                cid = str(c.get("id"))
                if cid not in ids:
                    ids.append(cid)
        ya = await asyncio.to_thread(dml._ya_guardados, cuenta, ids)
        print(f"    {cuenta}: {len(ids)} mediaciones de {desde} a {hasta}"
              f" · {len(ya)} ya tienen fila (no se releen)", flush=True)
        ids = [cid for cid in ids if cid not in ya]
        for i, cid in enumerate(ids, 1):
            if limite and len(resultados) >= limite:
                return resultados
            if transporte is not None and transporte.detenido:
                return resultados
            r = await dml.sincronizar(cid, cuenta, detectado_via="backfill", cli=cli,
                                      tokens=tokens, guardar=guardar, mediaciones=True)
            resultados.append({**r, "claim": cid, "cuenta": cuenta})
            if i % 100 == 0:
                print(f"      … {i}/{len(ids)}", flush=True)
    return resultados


# ── Informe (solo ids y conteos) ────────────────────────────────────────────

def _limpiar_motivo(m: Any) -> str:
    """El motivo de una falla, sin el DETAIL de Postgres (podría traer la fila
    entera, payload incluido)."""
    s = str(m or "?").splitlines()[0]
    for corte in ("DETAIL", "Failing row"):
        s = s.split(corte)[0]
    return re.sub(r"\d{6,}", "N", s.strip())[:90]


def _rid(cab: dict[str, Any]) -> Any:
    try:
        return (json.loads(cab.get("payload") or "{}").get("returns") or {}).get("id")
    except ValueError:
        return None


def ordenes_en_transicion(filas_db: Iterable[dict[str, Any]], cabs: Iterable[dict[str, Any]],
                          retiros: Iterable[tuple[str, str, str]] = ()) -> list[str]:
    """Claims SIN `returns.id` que comparten orden con otra devolución CON id.

    Es el hueco que el candado de duplicados no ve: una mediación guardada antes
    de que ML le diera id, y otro claim que después trajo esa devolución. No
    toda coincidencia es un duplicado (una orden puede tener dos reclamos
    legítimos), así que se cuenta para revisar, no se corrige. Junta lo
    guardado (`filas_db`) con lo que escribiría la corrida (`cabs`, que pisa al
    mismo claim) y quita lo retirado. Devuelve los claims sin id."""
    por_orden: dict[tuple[str, str], dict[str, Any]] = {}
    for f in filas_db:
        por_orden.setdefault((str(f["cuenta"]), str(f["external_order_id"])), {})[
            str(f["external_return_id"])] = f.get("rid")
    for c in cabs:
        por_orden.setdefault((str(c["cuenta"]), str(c["external_order_id"])), {})[
            str(c["external_return_id"])] = _rid(c)
    fuera = {(str(cu), str(p)) for cu, p, _g in retiros}
    sospechosos: list[str] = []
    for (cuenta, _oid), claims in por_orden.items():
        vivos = {cid: r for cid, r in claims.items() if (cuenta, cid) not in fuera}
        sin = [cid for cid, r in vivos.items() if r in (None, "")]
        con = [cid for cid, r in vivos.items() if r not in (None, "", 0, "0")]
        if sin and con:
            sospechosos.extend(sorted(sin))
    return sospechosos


def leer_ids_por_orden(sdb: Any, cuentas: Iterable[str]) -> list[dict[str, Any]]:
    return sdb.fetch_all(
        """select cuenta, external_order_id, external_return_id,
                  payload->'returns'->>'id' as rid
             from channel.returns
            where canal = %s and cuenta = any(%s)""",
        (CANAL, list(cuentas)))


def imprimir_fase(nombre: str, resultados: list[dict[str, Any]], col: Colector,
                  desde: int = 0, lineas_desde: int = 0, retiros_desde: int = 0) -> None:
    cabs = col.cabeceras[desde:]
    lineas = col.lineas[lineas_desde:]
    retiros = col.retiros[retiros_desde:]
    print(f"\n  ── {nombre} ──")
    if retiros:
        print(f"    filas duplicadas a retirar (se queda el claim de menor id): "
              f"{len(retiros)} {[p for _c, p, _g in retiros][:MAX_IDS]}")
    if resultados:
        dec = Counter(r.get("decision") or r.get("accion") or
                      ("fallo" if not r.get("ok") else "?") for r in resultados)
        print(f"    decisiones: {dict(dec)}")
        fallos = Counter(_limpiar_motivo(r.get("motivo"))
                         for r in resultados if not r.get("ok"))
        if fallos:
            print(f"    fallos por motivo: {dict(fallos.most_common(8))}")
        for cat in ("esperar", "duplicada", "conservado"):
            ids = [r["claim"] for r in resultados
                   if (r.get("decision") == cat or r.get("accion") == cat)][:MAX_IDS]
            if ids:
                print(f"    ids {cat}: {ids}")
        ids_f = [r["claim"] for r in resultados if not r.get("ok")][:MAX_IDS]
        if ids_f:
            print(f"    ids con fallo: {ids_f}")
        avisos = Counter(re.sub(r"\d+", "N", a.split(": ", 1)[-1])[:70]
                         for r in resultados for a in (r.get("avisos") or []))
        if avisos:
            print(f"    avisos: {dict(avisos.most_common(8))}")
    print(f"    devoluciones a escribir: {len(cabs)} · líneas: {len(lineas)}")
    if not cabs:
        return
    print(f"    estado × estado_canal: "
          f"{dict(Counter((c['estado'], c['estado_canal']) for c in cabs))}")
    print(f"    destino × FULL: "
          f"{dict(Counter((c['destino'], c['es_fulfillment']) for c in cabs))}")
    print(f"    venta_contaba: {dict(Counter(c['venta_contaba'] for c in cabs))}")
    sin_orden = [c["external_return_id"] for c in cabs if c["venta_contaba"] is None]
    print(f"    sin orden en channel.orders: {len(sin_orden)} "
          f"(caen como DROP por omisión) {sin_orden[:MAX_IDS]}")
    fechas = sorted(str(c["abierta_at"])[:10] for c in cabs if c.get("abierta_at"))
    if fechas:
        print(f"    abierta_at: {fechas[0]} → {fechas[-1]} ({len(set(fechas))} días distintos)")
    valor = sum(float(l["monto_unitario"]) * l["cantidad"]
                for l in lineas if l.get("monto_unitario"))
    contaba = {c["external_return_id"] for c in cabs if c["venta_contaba"]}
    restable = sum(float(l["monto_unitario"]) * l["cantidad"] for l in lineas
                   if l.get("monto_unitario") and l["external_return_id"] in contaba)
    print(f"    piezas: {sum(l['cantidad'] for l in lineas):,} · valor a precio "
          f"congelado: ${valor:,.2f} · restable: ${restable:,.2f}")
    rids = Counter((c["cuenta"], _rid(c)) for c in cabs if _rid(c) not in (None, 0, "0"))
    dups = {k: n for k, n in rids.items() if n > 1}
    print(f"    misma devolución en dos claims (dentro de la corrida): {len(dups)}"
          + (f" {list(dups)[:MAX_IDS]}" if dups else ""))


def imprimir_llamadas(t: TransporteVigilado) -> None:
    print(f"\n  LLAMADAS A ML: {t.llamadas}"
          + (f" · 401 internos de /returns: {t.internos_401}" if t.internos_401 else ""))
    for (ruta, st), n in sorted(t.por_ruta.items()):
        print(f"    {st}  {ruta:<46} {n}")
    if t.detenido:
        print(f"  ⚠ CORRIDA DETENIDA: {t.detenido}")


# ── main ─────────────────────────────────────────────────────────────────────

def _leer_stdin() -> dict[str, str]:
    if sys.stdin is None or sys.stdin.isatty():
        raise Candado("los secretos llegan por stdin (CLAVE=valor): SANDBOX_DB_URL "
                      "y, para las fases con ML, PROD_DB_URL y DB_ENCRYPTION_KEY.")
    vals: dict[str, str] = {}
    for linea in sys.stdin.read().splitlines():
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


async def _correr(args: argparse.Namespace, fases: list[str], cuentas: list[str],
                  dml: Any, sdb: Any, settings: Any, tokens: dict[str, str]) -> int:
    transporte = TransporteVigilado(ritmo=args.ritmo, tope=args.tope_llamadas)
    if args.aplicar:
        guardar: Colector = GuardarEnSandbox(dml, settings, transporte)
    else:
        guardar = Colector(transporte, dml)
    formas = Formas()
    espiar_traer(dml, formas)
    limite = args.limite_claims or None
    salida = 0

    if "local" in fases:
        filas = await asyncio.to_thread(leer_filas_locales, sdb, cuentas)
        cambios = cambios_locales(filas, dml)
        if limite:
            cambios = cambios[:limite]
        print(f"\n  ── local ── ({len(filas)} filas en channel.returns del sandbox)")
        imprimir_locales(cambios)
        if args.aplicar and cambios:
            n = await asyncio.to_thread(aplicar_locales, sdb, settings, cambios)
            print(f"    escritas en el sandbox: {n}")

    def _marca() -> tuple[int, int, int]:
        return len(guardar.cabeceras), len(guardar.lineas), len(guardar.retiros)

    async with httpx.AsyncClient(transport=transporte, follow_redirects=True,
                                 timeout=40) as cli:
        # `abiertas` ANTES que `mediaciones`: no relee lo que esa fase escribe.
        if "abiertas" in fases:
            m = _marca()
            try:
                # El ritmo lo pone el transporte vigilado (cli inyectado).
                r = await dml.refrescar_abiertas(tope=limite, horas=0, max_dias=120,
                                                 cuentas=cuentas, cli=cli,
                                                 tokens=tokens, guardar=guardar)
                print(f"\n    refresco: { {k: v for k, v in r.items() if k != 'motivos'} }")
                if r.get("motivos"):
                    print(f"    fallos: {[_limpiar_motivo(x) for x in r['motivos']]}")
            except CorridaDetenida:
                pass
            imprimir_fase("abiertas", [], guardar, *m)
        if "mediaciones" in fases and not transporte.detenido:
            m = _marca()
            try:
                res = await fase_mediaciones(dml, cli, tokens, guardar, cuentas=cuentas,
                                             dias=args.dias, limite=limite,
                                             transporte=transporte)
            except CorridaDetenida:
                res = []
            imprimir_fase("mediaciones", res, guardar, *m)

    filas_db = await asyncio.to_thread(leer_ids_por_orden, sdb, cuentas)
    sospechosos = ordenes_en_transicion(filas_db, guardar.cabeceras, guardar.retiros)
    print(f"\n  DEVOLUCIÓN SIN returns.id JUNTO A OTRA CON id EN LA MISMA ORDEN "
          f"(posible duplicado por transición, revisar): {len(sospechosos)}"
          + (f" {sospechosos[:MAX_IDS]}" if sospechosos else ""))

    formas.imprimir()
    imprimir_llamadas(transporte)
    if transporte.detenido:
        salida = 2
    if not args.aplicar:
        print("\nDRY-RUN: no se escribió nada. Repite con --aplicar (solo sandbox).")
    return salida


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dias", type=int, default=90)
    ap.add_argument("--cuenta", choices=CUENTAS)
    ap.add_argument("--fases", default=",".join(FASES),
                    help="local,abiertas,mediaciones (siempre en ese orden)")
    ap.add_argument("--limite-claims", type=int, default=0,
                    help="corta cada fase a N claims (humo)")
    ap.add_argument("--tope-llamadas", type=int, default=7500)
    ap.add_argument("--ritmo", type=float, default=3.0, help="GET por segundo")
    ap.add_argument("--max-edad-token-min", type=float, default=300.0)
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe en el SANDBOX (default: dry-run)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    fases = [f.strip() for f in args.fases.split(",") if f.strip()]
    if not fases or any(f not in FASES for f in fases):
        print(f"ABORT: --fases admite {', '.join(FASES)}.")
        return 2
    fases = [f for f in FASES if f in fases]
    cuentas = [args.cuenta] if args.cuenta else list(CUENTAS)
    con_ml = any(f in fases for f in ("mediaciones", "abiertas"))
    print(f"═══ Recuperar devoluciones ML · {'APLICAR (sandbox)' if args.aplicar else 'DRY-RUN'}"
          f" · fases {','.join(fases)} · {','.join(cuentas)} ═══")

    try:
        secretos = _leer_stdin()
        sand = secretos.get("SANDBOX_DB_URL", "")
        verificar_destino(sand)
        prod, clave = secretos.get("PROD_DB_URL", ""), secretos.get("DB_ENCRYPTION_KEY", "")
        if con_ml:
            if REF_PROD not in prod:
                raise Candado("PROD_DB_URL no llegó o no es la BD kubera de producción "
                              "(solo se usa para LEER ops.ml_tokens).")
            if not clave:
                raise Candado("falta DB_ENCRYPTION_KEY en stdin.")
        del secretos

        aislar_config(sand)
        sys.path.insert(0, str(BACKEND))
        logging.basicConfig(level=logging.WARNING,
                            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        instalar_filtro_log()
        from config import settings  # noqa: E402
        verificar_config(settings, sand)
        from services import db, meli  # noqa: E402
        from services import devoluciones_ml as dml  # noqa: E402
        from services import supabase_db as sdb  # noqa: E402
        puestos = poner_cables_trampa(db, meli)
        if sdb.settings.supabase_db_url != sand:
            raise Candado("supabase_db no quedó apuntando al sandbox.")
        # Otra vez, ya importado todo: por si algún módulo puso su handler.
        filtrados = instalar_filtro_log()
        print(f"  configuración aislada en el sandbox · cables trampa: {len(puestos)}"
              f" · log sin datos: {filtrados} handler(s)")

        try:
            existe = sdb.fetch_scalar("select to_regclass('channel.returns') is not null")
        except Exception as exc:  # noqa: BLE001
            raise Candado(f"no pude leer el sandbox ({type(exc).__name__}).") from None
        if not existe:
            raise Candado("el sandbox no tiene channel.returns: aplica antes "
                          "supabase/migrations/0049_channel_devoluciones.sql (SPEC §6, paso 0).")

        tokens: dict[str, str] = {}
        if con_ml:
            filas = _leer_filas_tokens(prod)
            tokens = descifrar_tokens(filas, clave, cuentas=cuentas,
                                      max_edad_min=args.max_edad_token_min)
            print(f"  tokens leídos (solo lectura): {', '.join(sorted(tokens))}")
        del prod, clave
    except Candado as exc:
        print(f"ABORT: {exc}")
        return 2

    t0 = time.time()
    try:
        salida = asyncio.run(_correr(args, fases, cuentas, dml, sdb, settings, tokens))
    finally:
        tokens.clear()
    print(f"\n  {time.time() - t0:.0f} s.")
    return salida


if __name__ == "__main__":
    raise SystemExit(main())
