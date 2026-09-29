"""Candados del laboratorio: todo camino de ESCRITURA revienta antes de salir.

El laboratorio reutiliza módulos de producción (`services.*`). Varios de ellos
parecen de lectura y escriben en un camino lateral: renovar el token de ML ante
un 401 (rota el refresh_token de producción y puede parar las ventas), crear
tablas en MySQL, hacer UPDATE en `channel.listings`. En vez de confiar en que
nadie llame a la función equivocada, aquí se reemplazan esas puertas por una
que lanza `EscrituraProhibida`.

- kubera (`services.supabase_db`): el POOL queda envuelto, así que TODA conexión
  que salga de él (por `get_cursor`, `fetch_*` o quien la pida directo) entrega
  un cursor de LISTA BLANCA (`execute`, `fetch*`, `description`, `rowcount`,
  `close`, iterar; `cur.connection`, `callproc`, `copy_*`, `mogrify` revientan) y
  una conexión que solo sabe `cursor()`, `commit()`, `rollback()` y `close()`
  (nada de `set_session`, `autocommit` ni cursores con nombre).
  Cada `execute` pasa por `es_select()`, que lee el SQL como el lexer de
  Postgres (comentarios anidados, `E'…'`, `"…"`, dollar-quoting) y rechaza lo que
  no sea UNA sentencia SELECT/WITH sin verbos de escritura, sin `INTO`, sin
  bloqueo de filas, sin tocar la sesión (`SET`, `set_config(…, false)`) y sin
  funciones que escriban o ejecuten SQL de un texto (`pg_terminate_backend`,
  `lo_*`, `dblink`, `query_to_xml`…).
  GARANTÍA DEL SERVIDOR: cada sentencia viaja en el MISMO mensaje detrás de
  `SET TRANSACTION READ ONLY; SELECT set_config('statement_timeout', …, true);`.
  Postgres rechaza cualquier escritura aunque el filtro de texto tuviera un
  hueco, y como va en el mismo mensaje, si SteadyDB reconecta y repite la
  sentencia en otra conexión, la guardia viaja con ella.
  REGLA 13: las dos son POR TRANSACCIÓN — `SET TRANSACTION` (no `SET SESSION
  CHARACTERISTICS`) y `set_config(…, true)` (= `SET LOCAL`) mueren con el
  COMMIT/ROLLBACK que `get_cursor` hace siempre al salir. Nunca se toca la
  sesión de la conexión compartida del pooler 6543 (`default_transaction_read_only`
  sigue en `off`; se mide en `fase3/seguridad/probar_servidor.py`).
- `supabase_db._desinfectar` (hace `SET SESSION … READ WRITE` sobre conexiones del
  pool, fuera del cursor) queda en no-op: el laboratorio no escribe.
- MySQL (`services.db`): `execute` revienta y el cursor es el mismo de lista blanca
  (además `_entorno` fuerza `MYSQL_ENABLED=false`: el pool ni se crea).
- Odoo (`services.odoo`): solo `execute_kw` con métodos de lectura y con timeout
  de socket (también el `authenticate` de `_uid`); cualquier otro atributo del
  proxy (`execute`, `system.multicall`) revienta.
- Mercado Libre (`services.meli`, `services.tokens_read`): renovar o guardar
  tokens revienta. Leerlos, no.
- HTTP: httpx (síncrono y asíncrono), requests y `http.client` (urllib, xmlrpc)
  solo hacen GET/HEAD/OPTIONS, más POST a `api.anthropic.com` (la IA del último
  peldaño de packing100) y el XML-RPC de Odoo (`/xmlrpc/2/common|object`, que ya
  filtra por método arriba). Un PUT/POST/PATCH/DELETE a Woo, a un marketplace, a
  Slack o al Storage revienta ANTES de abrir la conexión.
- Escritores conocidos de producción (`costing_write`, `woocommerce`,
  `packing_*`, `precios_venta`, `visitas_ml`, `envio_real`, `costos`): sus
  funciones de escritura revientan. `alertas.*` queda mudo (no-op): un aviso no
  debe tumbar una lectura, pero tampoco llegar a Slack de producción.

FALLA CERRADO: si un módulo que se debe candar no importa, si un objetivo
CRÍTICO ya no existe (producción lo renombró: el candado apuntaría a la nada) o
si Odoo no se puede envolver, `instalar()` lanza la excepción y
`_entorno.cargar()` no se da por cargado. Los objetivos OPCIONALES (auxiliares
privados, ya cubiertos por los candados genéricos de BD y HTTP) solo avisan en el
log si desaparecen. Al final se VERIFICA que cada objetivo quedó reemplazado.

Se instala desde `_entorno.cargar()`; es idempotente.
"""
from __future__ import annotations

import functools
import importlib
import logging
import os
import re
from urllib.parse import urlsplit

log = logging.getLogger("laboratorio.candados")


class EscrituraProhibida(RuntimeError):
    """Alguien intentó escribir desde el laboratorio. Es un bug, no un caso a manejar."""


_instalado = False

# ── SQL ───────────────────────────────────────────────────────────────────────
_SQL_LECTURA = re.compile(r"^[\s(]*(select|with)\b", re.IGNORECASE)
_SQL_ESCRITURA = re.compile(
    r"\b(insert|update|delete|merge|upsert|truncate|alter|create|drop|grant|revoke|"
    r"comment\s+on|refresh\s+materialized|vacuum|copy|call|do|lock|set|reset|discard|"
    r"notify|listen|unlisten|prepare|execute|deallocate|analyze|analyse|cluster|reindex|"
    r"security\s+label|checkpoint|load|import\s+foreign|into|nextval|setval|"
    r"pg_advisory\w*|pg_try_advisory\w*|"
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|pg_notify|"
    r"pg_switch_wal|pg_create\w*|pg_drop\w*|pg_promote|pg_file_\w+|pg_read_\w*file|pg_ls_\w+|"
    r"pg_stat_file|pg_stat_reset\w*|pg_stat_\w*_reset\w*|pg_logical_\w+|pg_replication_\w+|pg_import_\w+|pg_log_\w+|"
    r"pg_sleep\w*|lo_\w+|dblink\w*|txid_current|pg_current_xact_id|"
    # funciones que EJECUTAN el SQL de un texto (el filtro no ve adentro del literal)
    r"query_to_xml\w*|cursor_to_xml\w*|table_to_xml\w*|schema_to_xml\w*|database_to_xml\w*|"
    r"ts_stat)\b",
    re.IGNORECASE,
)
# Bloqueo de filas: `for update / for share / for no key update / for key share`.
_SQL_BLOQUEO = re.compile(r"\bfor\s+(no\s+key\s+update|key\s+share|update|share)\b", re.IGNORECASE)
# `set_config` solo LOCAL a la transacción (tercer argumento `true`) y solo para
# estos nombres; con `false` se queda pegado en la conexión compartida del
# pooler (regla 13). La palabra `set` suelta sí se rechaza (arriba):
# `set_config` no la dispara porque `_` es carácter de palabra.
_SET_CONFIG = re.compile(r"\bset_config\b", re.IGNORECASE)
_SET_CONFIG_LOCAL = re.compile(
    r"\bset_config\s*\(\s*'(\d+)'\s*,\s*(?:%s|%\(\w+\)s|'\d+')\s*,\s*true\s*\)", re.IGNORECASE)
_SET_CONFIG_NOMBRES = frozenset({"statement_timeout", "lock_timeout", "app.usuario", "app.origen"})
# Un `%s` DENTRO de un literal lo sustituye psycopg2 igual y rompe las comillas.
_PARAM_EN_LITERAL = re.compile(r"(?<!%)(?:%%)*%[s(]")
_DOLLAR = re.compile(r"\$(?:[A-Za-z_\x80-￿][\w\x80-￿]*)?\$")

_ODOO_LECTURA = {"search_read", "read", "search", "search_count", "fields_get",
                 "name_get", "name_search", "read_group", "default_get"}


def _timeout_sql() -> str:
    """`LAB_STATEMENT_TIMEOUT` validado (va DENTRO del SQL de la guardia)."""
    v = (os.environ.get("LAB_STATEMENT_TIMEOUT") or "120s").strip().lower()
    return v if re.fullmatch(r"\d{1,7}(ms|s|min)?", v) else "120s"


def _timeout_odoo() -> float:
    try:
        return max(5.0, min(float(os.environ.get("LAB_ODOO_TIMEOUT_S") or 180), 1800.0))
    except ValueError:
        return 180.0


_TIMEOUT_SQL = _timeout_sql()
_TIMEOUT_ODOO_S = _timeout_odoo()
# Viaja en el MISMO mensaje que cada sentencia del laboratorio (ver docstring).
_GUARDIA_PG = f"SET TRANSACTION READ ONLY; SELECT set_config('statement_timeout', '{_TIMEOUT_SQL}', true); "


def _es_ident(ch: str) -> bool:
    return ch.isalnum() or ch in "_$"


def _lexico(sql: str, barra_en_estandar: bool) -> tuple[str, list[str]] | None:
    """El SQL como lo parte el lexer de Postgres: devuelve (código, literales).

    En el código, cada literal queda como `'<índice>'`, cada identificador entre
    comillas como su nombre desnudo (así `"set_config"(…)` o `"update"` se ven) y
    cada comentario como un espacio. `None` = no se puede leer sin ambigüedad
    (literal o comentario sin cerrar, dollar-quoting, `U&'…'`).

    `barra_en_estandar`: si `'…'` trata `\\` como escape (standard_conforming_strings
    = off). `es_select` lee con las DOS interpretaciones y exige que ambas pasen:
    así no depende de un ajuste de sesión que otro cliente del pooler pudo cambiar.
    """
    out: list[str] = []
    lits: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        sig = sql[i + 1] if i + 1 < n else ""
        if c == "-" and sig == "-":
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
            continue
        if c == "/" and sig == "*":
            prof, i = 1, i + 2
            while i < n and prof:
                if sql.startswith("/*", i):
                    prof, i = prof + 1, i + 2
                elif sql.startswith("*/", i):
                    prof, i = prof - 1, i + 2
                else:
                    i += 1
            if prof:
                return None
            out.append(" ")
            continue
        if c in "'\"":
            prev = sql[i - 1] if i else ""
            prev2 = sql[i - 2] if i >= 2 else ""
            if prev == "&" and prev2 and prev2 in "uU":
                return None  # U&'…' / U&"…": escapes unicode que el filtro no decodifica
            e_str = c == "'" and bool(prev) and prev in "eE" and not (prev2 and _es_ident(prev2))
            barra = c == "'" and (e_str or barra_en_estandar)
            j, cuerpo = i + 1, []
            while True:
                if j >= n:
                    return None
                ch = sql[j]
                if barra and ch == "\\":
                    cuerpo.append(sql[j:j + 2])
                    j += 2
                    continue
                if ch == c:
                    if j + 1 < n and sql[j + 1] == c:
                        cuerpo.append(c)
                        j += 2
                        continue
                    break
                cuerpo.append(ch)
                j += 1
            texto = "".join(cuerpo)
            if c == "'":
                lits.append(texto)
                out.append(f"'{len(lits) - 1}'")
            else:
                out.append(" " + (re.sub(r"\W", "_", texto) or "_") + " ")
            i = j + 1
            continue
        out.append(c)
        i += 1
    codigo = "".join(out)
    if _DOLLAR.search(codigo):
        return None  # $$…$$ / $tag$…$tag$: literal que este lector no parte
    return codigo, lits


def _pasa(lectura: tuple[str, list[str]] | None) -> bool:
    if lectura is None:
        return False
    codigo, lits = lectura
    if not _SQL_LECTURA.match(codigo):
        return False
    if ";" in codigo.strip().rstrip(";").rstrip():
        return False  # más de una sentencia
    if _SQL_ESCRITURA.search(codigo) or _SQL_BLOQUEO.search(codigo):
        return False
    if any(_PARAM_EN_LITERAL.search(x) for x in lits):
        return False
    usos = len(_SET_CONFIG.findall(codigo))
    if usos:
        locales = _SET_CONFIG_LOCAL.findall(codigo)
        if len(locales) != usos or any(lits[int(k)].strip().lower() not in _SET_CONFIG_NOMBRES
                                       for k in locales):
            return False  # algún set_config no es LOCAL o no es de un nombre permitido
    return True


def es_select(sql: str) -> bool:
    """True solo para UNA sentencia SELECT/WITH sin verbos de escritura (ni en CTEs).

    >>> es_select("select 1"), es_select("with x as (select 1) select * from x;")
    (True, True)
    >>> [es_select(s) for s in ("select 1; set search_path = public",
    ...     "select set_config('default_transaction_read_only','on', false)",
    ...     "select * into public.t from core.products", "select pg_terminate_backend(1)",
    ...     "select * from ops.ml_tokens for share", "select lo_import('/tmp/x')",
    ...     "select 1; discard all", "select 1; prepare p as select 1", "select 1; analyze t",
    ...     "select dblink_exec('x', 'y')", "update t set a = 1",
    ...     "select pg_stat_statements_reset()")]
    [False, False, False, False, False, False, False, False, False, False, False, False]
    >>> es_select("select set_config('statement_timeout', '120s', true)")
    True
    >>> es_select("select set_config('role', 'postgres', true)")
    False
    >>> es_select("select * from t where nombre = 'insert; drop' and offset_x > 0")
    True
    >>> [es_select(s) for s in ("select E'\\\\''; set role x; --'",   # E-string
    ...     "select $$ ' $$; set role x; --'",                             # dollar-quoting
    ...     "select \\"'\\" ; set role x; --'",                            # identificador
    ...     "select 1 -- '\\n; set role x; --'",                           # comentario
    ...     "select 'a\\\\'; set role x; --'",                             # scs=off
    ...     "select \\"set_config\\"('role', 'x', false)",
    ...     "select * from query_to_xml('select 1', true, true, '')",
    ...     "select * from t where a = '%s'", "select /* sin cerrar")]
    [False, False, False, False, False, False, False, False, False]
    >>> es_select("select * from t where sku ~ '^[A-Z]{3}-\\\\d{4}' and x like 'a%%'")
    True
    """
    texto = sql or ""
    return _pasa(_lexico(texto, False)) and _pasa(_lexico(texto, True))


def solo_select(sql: str) -> str:
    if not isinstance(sql, str) or not es_select(sql):
        raise EscrituraProhibida(f"laboratorio: SQL no permitido (solo SELECT): {str(sql)[:120]!r}")
    return sql


def _marcar(f):
    f._candado_lab = True  # type: ignore[attr-defined]
    return f


def _prohibido(nombre: str):
    def _f(*_a, **_k):
        raise EscrituraProhibida(f"laboratorio: '{nombre}' escribe y está prohibido aquí")
    _f.__name__ = f"prohibido_{nombre.replace('.', '_')}"
    return _marcar(_f)


def _mudo(nombre: str):
    def _f(*_a, **_k):
        log.info("laboratorio: '%s' silenciado (no se avisa a producción desde aquí)", nombre)
        return False
    _f.__name__ = f"mudo_{nombre.replace('.', '_')}"
    return _marcar(_f)


class _CursorLectura:
    """Envoltura de un cursor DB-API de LISTA BLANCA: solo SELECT/WITH por `execute`
    y lectura de resultados. `connection`, `callproc`, `copy_*`, `mogrify`… no
    existen para quien la use. Con `guardia` (Postgres), cada sentencia viaja
    detrás de `_GUARDIA_PG` en el mismo mensaje."""

    _PERMITIDOS = frozenset({"fetchone", "fetchall", "fetchmany", "description", "rowcount",
                             "close", "closed", "arraysize", "rownumber", "statusmessage"})

    def __init__(self, real, origen: str, guardia: str = ""):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_origen", origen)
        object.__setattr__(self, "_guardia", guardia)

    def execute(self, sql, params=None):
        if not isinstance(sql, str) or not es_select(sql):
            raise EscrituraProhibida(
                f"laboratorio: {self._origen} rechazó una sentencia que no es SELECT: {str(sql)[:120]!r}")
        return self._real.execute(self._guardia + sql if self._guardia else sql, params)

    def executemany(self, *_a, **_k):
        raise EscrituraProhibida(f"laboratorio: {self._origen} executemany prohibido")

    def __iter__(self):
        return iter(self._real)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, nombre):
        if nombre in _CursorLectura._PERMITIDOS:
            return getattr(self._real, nombre)
        raise EscrituraProhibida(f"laboratorio: {self._origen} cursor.{nombre} fuera de la lista blanca")

    def __setattr__(self, nombre, valor):
        if nombre == "arraysize":
            setattr(self._real, nombre, valor)
            return
        raise EscrituraProhibida(f"laboratorio: {self._origen} no se puede asignar cursor.{nombre}")


class _ConexionLectura:
    """Conexión del pool de kubera de LISTA BLANCA: `cursor()` (sin nombre: un
    cursor `WITH HOLD` sobreviviría a la transacción en la conexión compartida),
    `commit`, `rollback`, `close`, `begin`. Nada de `set_session`, `autocommit`,
    `set_isolation_level` ni el objeto psycopg2 de adentro."""

    _PERMITIDOS = frozenset({"closed", "status", "encoding"})

    def __init__(self, real, origen: str):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_origen", origen)

    def cursor(self, *a, **k):
        if a or set(k) - {"cursor_factory"}:
            raise EscrituraProhibida(f"laboratorio: {self._origen} conn.cursor{a or k} fuera de la lista blanca")
        return _CursorLectura(self._real.cursor(**k), self._origen, _GUARDIA_PG)

    def commit(self):
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def close(self):
        return self._real.close()

    def begin(self, *a, **k):
        return self._real.begin(*a, **k)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __getattr__(self, nombre):
        if nombre in _ConexionLectura._PERMITIDOS:
            return getattr(self._real, nombre)
        raise EscrituraProhibida(f"laboratorio: {self._origen} conn.{nombre} fuera de la lista blanca")

    def __setattr__(self, nombre, valor):
        raise EscrituraProhibida(f"laboratorio: {self._origen} no se puede asignar conn.{nombre}")


class _PoolLectura:
    def __init__(self, real, origen: str):
        self._real, self._origen = real, origen

    def connection(self, *a, **k):
        return _ConexionLectura(self._real.connection(*a, **k), self._origen)

    def close(self):
        return self._real.close()


def _pool_de_lectura(original, origen: str):
    """`_get_pool` envuelto: toda conexión del pool sale de lista blanca."""
    def _get_pool(*a, **k):
        return _PoolLectura(original(*a, **k), origen)

    return _marcar(_get_pool)


def _cursor_de_lectura(original, origen: str, solo_lectura_servidor: bool = False):
    """`get_cursor` envuelto (MySQL y pruebas). Con `solo_lectura_servidor`
    (Postgres) cada sentencia lleva `_GUARDIA_PG` delante. Para kubera se envuelve
    el POOL (`_pool_de_lectura`), que cubre también a quien no use `get_cursor`."""
    from contextlib import contextmanager

    @contextmanager
    def get_cursor(*a, **k):
        with original(*a, **k) as cur:
            yield _CursorLectura(cur, origen, _GUARDIA_PG if solo_lectura_servidor else "")

    return _marcar(get_cursor)


# ── HTTP: solo lectura (más la IA y el XML-RPC de Odoo) ───────────────────────
_POST_PERMITIDOS = frozenset({"api.anthropic.com"})
_METODOS_HTTP_LECTURA = frozenset({"GET", "HEAD", "OPTIONS"})
_RUTAS_ODOO = ("/xmlrpc/2/common", "/xmlrpc/2/object")


@functools.lru_cache(maxsize=1)
def _host_odoo() -> str:
    try:
        from config import settings

        return (urlsplit(settings.odoo_url or "").hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _revisar_http(metodo: str, url, host: str | None = None) -> None:
    m = (metodo or "").upper()
    if m in _METODOS_HTTP_LECTURA:
        return
    partes = urlsplit(str(url))
    host = (host if host is not None else partes.hostname or "").lower()
    if m == "POST" and host in _POST_PERMITIDOS:
        return
    if m == "POST" and host and host == _host_odoo() and partes.path.rstrip("/") in _RUTAS_ODOO:
        return  # XML-RPC de Odoo: el método ya lo filtra `_ProxyLectura`
    raise EscrituraProhibida(f"laboratorio: HTTP {m} a {host or '?'} prohibido (solo GET/HEAD)")


def _candar_http() -> None:
    import http.client

    import httpx
    import requests

    if not getattr(httpx.Client.send, "_candado_lab", False):
        _send = httpx.Client.send

        def send(self, request, *a, **k):
            _revisar_http(request.method, request.url)
            return _send(self, request, *a, **k)

        httpx.Client.send = _marcar(send)  # type: ignore[method-assign]

    if not getattr(httpx.AsyncClient.send, "_candado_lab", False):
        _asend = httpx.AsyncClient.send

        async def asend(self, request, *a, **k):
            _revisar_http(request.method, request.url)
            return await _asend(self, request, *a, **k)

        httpx.AsyncClient.send = _marcar(asend)  # type: ignore[method-assign]

    if not getattr(requests.Session.send, "_candado_lab", False):
        _rsend = requests.Session.send

        def rsend(self, request, **k):
            _revisar_http(request.method, request.url)
            return _rsend(self, request, **k)

        requests.Session.send = _marcar(rsend)  # type: ignore[method-assign]

    # La capa de abajo de urllib, xmlrpc (Odoo) y urllib3 (requests): quien no
    # pase por httpx ni por requests.Session también queda revisado.
    if not getattr(http.client.HTTPConnection.putrequest, "_candado_lab", False):
        _put = http.client.HTTPConnection.putrequest

        def putrequest(self, method, url, *a, **k):
            _revisar_http(method, url, host=str(getattr(self, "host", "") or ""))
            return _put(self, method, url, *a, **k)

        http.client.HTTPConnection.putrequest = _marcar(putrequest)  # type: ignore[method-assign]


# ── parches por nombre ────────────────────────────────────────────────────────
_OBJETIVOS: list[tuple[str, str]] = []


def _parchar(modulo: str, criticos: list[str], reemplazo=_prohibido,
             opcionales: tuple[str, ...] | list[str] = ()) -> None:
    """Reemplaza funciones de `modulo`. Falla CERRADO:

    - si el módulo NO importa, sale la excepción (un módulo que no importa hoy
      podría importar mañana sin candado);
    - si un nombre CRÍTICO no existe, `EscrituraProhibida`: producción lo renombró
      y el candado ya no cubre esa puerta — hay que revisarlo, no arrancar;
    - un nombre OPCIONAL (auxiliar privado, ya cubierto por los candados de BD y
      HTTP) que no exista solo se avisa en el log.
    """
    mod = importlib.import_module(modulo)
    faltan = [f for f in criticos if not callable(getattr(mod, f, None))]
    if faltan:
        raise EscrituraProhibida(
            f"laboratorio: candado crítico sin objetivo: {modulo}.{faltan} ya no existe "
            "(producción cambió; revisa candados.py antes de arrancar)")
    for f in (*criticos, *opcionales):
        if callable(getattr(mod, f, None)):
            setattr(mod, f, reemplazo(f"{modulo}.{f}"))
            _OBJETIVOS.append((modulo, f))
        else:
            log.warning("candados: %s.%s (opcional) ya no existe; lo cubren los candados de BD/HTTP",
                        modulo, f)


def _verificar() -> None:
    """Revienta si algún objetivo no quedó reemplazado (p. ej. un reimport)."""
    import http.client

    import httpx
    import requests

    from services import db as mysql
    from services import odoo
    from services import supabase_db as sdb

    faltan = [f"{m}.{f}" for m, f in _OBJETIVOS
              if not getattr(getattr(importlib.import_module(m), f, None), "_candado_lab", False)]
    for nombre in ("_get_pool", "execute", "execute_returning", "_desinfectar", "fetch_all", "fetch_one"):
        if not getattr(getattr(sdb, nombre, None), "_candado_lab", False):
            faltan.append(f"services.supabase_db.{nombre}")
    for obj, nombre in ((httpx.Client.send, "httpx.Client.send"),
                        (httpx.AsyncClient.send, "httpx.AsyncClient.send"),
                        (requests.Session.send, "requests.Session.send"),
                        (http.client.HTTPConnection.putrequest, "http.client.HTTPConnection.putrequest"),
                        (mysql.get_cursor, "services.db.get_cursor"),
                        (odoo._models, "services.odoo._models"),
                        (odoo._uid, "services.odoo._uid")):
        if not getattr(obj, "_candado_lab", False):
            faltan.append(nombre)
    if faltan:
        raise EscrituraProhibida(f"laboratorio: candados incompletos: {faltan}")


def instalar() -> None:
    global _instalado
    if _instalado:
        return

    # ── HTTP ──────────────────────────────────────────────────────────────────
    _candar_http()

    # ── kubera ────────────────────────────────────────────────────────────────
    from services import supabase_db as sdb

    _fetch_all, _fetch_one = sdb.fetch_all, sdb.fetch_one

    def fetch_all(sql, params=None):
        return _fetch_all(solo_select(sql), params)

    def fetch_one(sql, params=None):
        return _fetch_one(solo_select(sql), params)

    sdb.fetch_all, sdb.fetch_one = _marcar(fetch_all), _marcar(fetch_one)
    sdb.execute = _prohibido("supabase_db.execute")
    sdb.execute_returning = _prohibido("supabase_db.execute_returning")
    # El POOL: por `get_cursor` escriben `costos.marcar_validado`, `tokens_read` y
    # `meli`, y cualquiera puede pedir `_get_pool().connection()` directo. Toda
    # conexión que salga de aquí es de lista blanca y toda sentencia lleva la
    # guardia de transacción READ ONLY (ver docstring). `get_cursor` y `fetch_*`
    # la toman al buscar `_get_pool` en el módulo en cada llamada.
    sdb._get_pool = _pool_de_lectura(sdb._get_pool, "kubera")
    # `_desinfectar` hace SET SESSION … READ WRITE en conexiones del pool, fuera
    # del cursor: el laboratorio nunca lo necesita.
    sdb._desinfectar = _marcar(lambda: 0)

    # ── MySQL ─────────────────────────────────────────────────────────────────
    _parchar("services.db", ["execute"])
    from services import db as mysql

    mysql.get_cursor = _cursor_de_lectura(mysql.get_cursor, "mysql")

    # ── Odoo: solo execute_kw de lectura, con timeout ─────────────────────────
    from services import odoo

    _pct = getattr(odoo, "_proxy_con_tiempo", None)
    if _pct is None:
        # Sin el transporte con tiempo, una red partida colgaría el pipeline y el
        # `authenticate` no tendría timeout: se falla cerrado.
        raise EscrituraProhibida("laboratorio: services.odoo._proxy_con_tiempo no existe; revisa candados.py")

    class _ProxyLectura:
        def __init__(self, real):
            object.__setattr__(self, "_real", real)

        def execute_kw(self, db, uid, pwd, modelo, metodo, *args, **kwargs):
            if metodo not in _ODOO_LECTURA:
                raise EscrituraProhibida(f"laboratorio: Odoo {modelo}.{metodo} no es de lectura")
            return self._real.execute_kw(db, uid, pwd, modelo, metodo, *args, **kwargs)

        def __getattr__(self, nombre):
            # Solo execute_kw: `execute`, `system.multicall`, etc. no pasan.
            raise EscrituraProhibida(f"laboratorio: Odoo proxy.{nombre} fuera de la lista blanca")

    def _models_lectura():
        # Con timeout de socket: `_models()` no lo configura y una red partida
        # colgaría el pipeline.
        return _ProxyLectura(_pct("object", _TIMEOUT_ODOO_S))

    odoo._models = _marcar(_models_lectura)

    def _proxy_con_tiempo(ruta, timeout):
        real = _pct(ruta, timeout)
        return _ProxyLectura(real) if "object" in str(ruta) else real

    odoo._proxy_con_tiempo = _marcar(_proxy_con_tiempo)

    @functools.lru_cache
    def _uid_con_tiempo():
        # Igual que `odoo._uid` (authenticate es de lectura), pero con timeout.
        from config import settings

        try:
            uid = _pct("common", _TIMEOUT_ODOO_S).authenticate(
                settings.odoo_db, settings.odoo_user, settings.odoo_password, {})
            return uid or None
        except Exception as exc:  # noqa: BLE001
            log.warning("Odoo auth falló: %s", type(exc).__name__)
            return None

    odoo._uid = _marcar(_uid_con_tiempo)

    # ── Mercado Libre: leer token sí, renovarlo o guardarlo NUNCA ─────────────
    _parchar("services.meli", ["refrescar_token", "_refrescar_token_sin_cache", "_refrescar_solo_kubera"],
             opcionales=["_renovar_con_candado", "_espejar_mysql"])
    _parchar("services.tokens_read", ["guardar", "candado_renovacion"], opcionales=["tiktok_guardar"])

    # ── Escritores conocidos de producción ────────────────────────────────────
    _parchar("services.costing_write", ["guardar_validados", "guardar_finales", "marcar_revisado"],
             opcionales=["guardar_caja_compartida", "registrar_log", "_escribir", "_encolar_kubera"])
    _parchar("services.costing_mirror", [], opcionales=["upsert_validados", "upsert_finales"])
    _parchar("services.precios_venta", [], opcionales=["refrescar_en_fondo", "_guardar"])
    _parchar("services.visitas_ml", [], opcionales=["completar", "_asegurar_tabla"])
    _parchar("services.envio_real", [], opcionales=["completar", "_completar", "_asegurar_tabla"])
    _parchar("services.costos", ["marcar_validado", "liberar_validado", "recalcular"],
             opcionales=["_guardar_finales", "_guardar_validados", "asegurar_finales", "_log_costo"])
    _parchar("services.packing_publicados", ["guardar"],
             opcionales=["iniciar", "agregar_archivo", "corregir_fila", "_registrar_procedencia"])
    _parchar("services.packing_resolver", [], opcionales=["guardar", "iniciar", "capturar", "actualizar_empate"])
    _parchar("services.packing_comparador", [], opcionales=["guardar"])
    _parchar("services.packing_storage", ["subir"], opcionales=["_subir_tus"])
    _parchar("services.woocommerce", ["crear_borradores", "subir_imagen_wp", "asignar_imagenes",
                                      "reemplazar_imagenes_galeria", "eliminar_imagen_galeria",
                                      "agregar_imagenes_galeria", "guardar_meta", "guardar_contenido_wc"],
             opcionales=["_limpiar_fantasma_lookup"])
    # Avisos: mudos (no revientan: un aviso en un camino de lectura no debe tumbar
    # la corrida, pero tampoco llegar al Slack de producción ni sellar su estado).
    _parchar("services.alertas", ["avisar", "avisar_estado", "_post_slack"], reemplazo=_mudo,
             opcionales=["avisar_si_racha", "_sellar", "_contar_suprimida", "_guardar_estado",
                         "_campana", "_guardar_foto"])

    _verificar()
    _instalado = True
    log.info("laboratorio: candados de solo lectura instalados (%d funciones)", len(_OBJETIVOS))
