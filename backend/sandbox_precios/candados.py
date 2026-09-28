"""Candados del laboratorio: todo camino de ESCRITURA revienta antes de salir.

El laboratorio reutiliza módulos de producción (`services.*`). Varios de ellos
parecen de lectura y escriben en un camino lateral: renovar el token de ML ante
un 401 (rota el refresh_token de producción y puede parar las ventas), crear
tablas en MySQL, hacer UPDATE en `channel.listings`. En vez de confiar en que
nadie llame a la función equivocada, aquí se reemplazan esas puertas por una
que lanza `EscrituraProhibida`.

- kubera (`services.supabase_db`): solo `fetch_*`. `execute*` revientan, y
  `solo_select()` rechaza cualquier SQL que no empiece por SELECT/WITH.
- MySQL (`services.db`): `execute` revienta.
- Odoo (`services.odoo`): `execute_kw` solo con métodos de lectura.
- Mercado Libre (`services.meli`): renovar token revienta. Leerlo, no.
- Escritores conocidos (`costing_write`, `precios_venta`, `visitas_ml`,
  `envio_real`): sus funciones públicas de escritura revientan.

Se instala desde `_entorno.cargar()`; es idempotente.
"""
from __future__ import annotations

import importlib
import logging
import re

log = logging.getLogger("laboratorio.candados")


class EscrituraProhibida(RuntimeError):
    """Alguien intentó escribir desde el laboratorio. Es un bug, no un caso a manejar."""


_instalado = False
_SQL_LECTURA = re.compile(r"^\s*(\(|--[^\n]*\n\s*)*\s*(select|with)\b", re.IGNORECASE)
_SQL_ESCRITURA = re.compile(
    r"\b(insert|update|delete|merge|upsert|truncate|alter|create|drop|grant|revoke|"
    r"comment\s+on|refresh\s+materialized|vacuum|copy|call|do|lock|set\s+session|"
    r"set\s+transaction|nextval|setval|pg_advisory)\b",
    re.IGNORECASE,
)
_ODOO_LECTURA = {"search_read", "read", "search", "search_count", "fields_get",
                 "name_get", "name_search", "read_group", "default_get"}


def es_select(sql: str) -> bool:
    """True solo para SELECT/WITH sin verbos de escritura (ni en CTEs)."""
    return bool(_SQL_LECTURA.match(sql or "")) and not _SQL_ESCRITURA.search(sql or "")


def solo_select(sql: str) -> str:
    if not es_select(sql):
        raise EscrituraProhibida(f"laboratorio: SQL no permitido (solo SELECT): {sql[:120]!r}")
    return sql


def _prohibido(nombre: str):
    def _f(*_a, **_k):
        raise EscrituraProhibida(f"laboratorio: '{nombre}' escribe y está prohibido aquí")
    _f.__name__ = f"prohibido_{nombre.replace('.', '_')}"
    return _f


class _CursorLectura:
    """Envoltura de un cursor DB-API que solo deja pasar SELECT/WITH."""

    def __init__(self, real, origen: str):
        self._real = real
        self._origen = origen

    def execute(self, sql, params=None):
        texto = sql if isinstance(sql, str) else str(sql)
        if not es_select(texto):
            raise EscrituraProhibida(
                f"laboratorio: {self._origen} rechazó una sentencia que no es SELECT: {texto[:120]!r}")
        return self._real.execute(sql, params)

    def executemany(self, *_a, **_k):
        raise EscrituraProhibida(f"laboratorio: {self._origen} executemany prohibido")

    def __iter__(self):
        return iter(self._real)

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)


def _cursor_de_lectura(original, origen: str):
    from contextlib import contextmanager

    @contextmanager
    def get_cursor(*a, **k):
        with original(*a, **k) as cur:
            yield _CursorLectura(cur, origen)

    return get_cursor


def _parchar(modulo: str, funciones: list[str]) -> None:
    try:
        mod = importlib.import_module(modulo)
    except Exception as exc:  # noqa: BLE001 — si el módulo no importa, nadie lo puede llamar
        log.debug("candados: %s no importó (%s)", modulo, exc)
        return
    for f in funciones:
        if hasattr(mod, f):
            setattr(mod, f, _prohibido(f"{modulo}.{f}"))


def instalar() -> None:
    global _instalado
    if _instalado:
        return

    # ── kubera ────────────────────────────────────────────────────────────────
    from services import supabase_db as sdb

    _fetch_all, _fetch_one = sdb.fetch_all, sdb.fetch_one

    def fetch_all(sql, params=None):
        return _fetch_all(solo_select(sql), params)

    def fetch_one(sql, params=None):
        return _fetch_one(solo_select(sql), params)

    sdb.fetch_all, sdb.fetch_one = fetch_all, fetch_one
    sdb.execute = _prohibido("supabase_db.execute")
    sdb.execute_returning = _prohibido("supabase_db.execute_returning")
    # El cursor crudo también: por `get_cursor` escriben `costos.marcar_validado`,
    # `tokens_read` y `meli` (hallazgo de la auditoría del servidor). Parchar por
    # nombre a los escritores conocidos no alcanza para uno nuevo; aquí se revisa
    # cada sentencia que pase por el cursor, venga de quien venga.
    sdb.get_cursor = _cursor_de_lectura(sdb.get_cursor, "kubera")

    # ── MySQL ─────────────────────────────────────────────────────────────────
    _parchar("services.db", ["execute"])
    try:
        from services import db as mysql

        mysql.get_cursor = _cursor_de_lectura(mysql.get_cursor, "mysql")
    except Exception as exc:  # noqa: BLE001
        log.debug("candados: services.db no importó (%s)", exc)

    # ── Odoo: solo métodos de lectura ─────────────────────────────────────────
    try:
        from services import odoo

        _models = odoo._models

        class _ProxyLectura:
            def __init__(self, real):
                self._real = real

            def execute_kw(self, db, uid, pwd, modelo, metodo, *args, **kwargs):
                if metodo not in _ODOO_LECTURA:
                    raise EscrituraProhibida(f"laboratorio: Odoo {modelo}.{metodo} no es de lectura")
                return self._real.execute_kw(db, uid, pwd, modelo, metodo, *args, **kwargs)

            def __getattr__(self, nombre):
                if nombre == "execute":
                    raise EscrituraProhibida("laboratorio: Odoo execute() sin lista blanca")
                return getattr(self._real, nombre)

        odoo._models = lambda: _ProxyLectura(_models())
        if hasattr(odoo, "_proxy_con_tiempo"):
            _pct = odoo._proxy_con_tiempo

            def _proxy_con_tiempo(ruta, timeout):
                real = _pct(ruta, timeout)
                return _ProxyLectura(real) if "object" in str(ruta) else real

            odoo._proxy_con_tiempo = _proxy_con_tiempo
    except Exception as exc:  # noqa: BLE001
        log.warning("candados: no se pudo envolver Odoo (%s)", exc)

    # ── Mercado Libre: leer token sí, renovarlo NUNCA ─────────────────────────
    _parchar("services.meli", ["refrescar_token", "_refrescar_token_sin_cache",
                               "_renovar_con_candado", "_refrescar_solo_kubera"])

    # ── Escritores conocidos de producción ────────────────────────────────────
    _parchar("services.costing_write", ["guardar_validados", "guardar_finales",
                                        "upsert_validados", "upsert_finales", "guardar"])
    _parchar("services.costing_mirror", ["upsert_validados", "upsert_finales"])
    _parchar("services.precios_venta", ["refrescar_en_fondo", "_guardar"])
    _parchar("services.visitas_ml", ["completar", "_asegurar_tabla"])
    _parchar("services.envio_real", ["completar"])
    _parchar("services.costos", ["_guardar_finales", "_guardar_validados", "marcar_validado",
                                 "liberar_validado", "asegurar_finales", "recalcular"])
    _parchar("services.packing_publicados", ["guardar", "iniciar"])
    _parchar("services.packing_resolver", ["guardar"])
    _parchar("services.packing_comparador", ["guardar"])
    _parchar("services.packing_storage", ["subir"])

    _instalado = True
    log.info("laboratorio: candados de solo lectura instalados")
