"""
conexion.py — Por dónde entra el MCP a las dos fuentes, y el candado.

DOS FUENTES, y no es un capricho de diseño:

  · **Supabase kubera** (`tukwcvsi…`) tiene visitas y ventas. Se llega por
    conexión DIRECTA a Postgres (`SUPABASE_DB_URL`), NO por la API REST —
    `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` del `.env` apuntan al OTRO
    proyecto (analytics, `xaxbkijc…`), que no tiene nada de esto.
  · **Odoo** tiene el stock libre y el factor de caja. No están en Supabase,
    así que no hay forma de contestar esas dos preguntas sin salir a Odoo.

REGLA CERO — SOLO LECTURA, Y SIN MARCAR LA SESIÓN
-------------------------------------------------
Este servidor no escribe en ningún lado: no hay una sola herramienta de
escritura, y `consultar()` rechaza cualquier SQL que no empiece en SELECT/WITH.

Lo que NO se hace, y es la parte que cuesta dinero cuando se olvida: **no se
marca la sesión de solo-lectura**. El DSN va al pooler en modo transacción
(6543), donde las conexiones del SERVIDOR se comparten entre clientes; un
`set_session(readonly=True)` o un `SET SESSION CHARACTERISTICS AS TRANSACTION
READ ONLY` se queda pegado en la conexión y lo hereda el siguiente que la tome
— que puede ser el backend registrando una venta. Reventó dos veces (12-ago y
17-19-ago de 2026). El script termina y el daño sigue vivo.

Si algún día alguien quiere la garantía dura, las tres salidas legítimas son:
marcarlo POR TRANSACCIÓN (BEGIN / SET TRANSACTION READ ONLY / ROLLBACK, que
muere con el commit), conectarse al **5432** donde la conexión es propia, o —lo
que hace este módulo— simplemente no marcar nada porque sólo se hace SELECT.

POR QUÉ SE IMPORTA `services/` EN VEZ DE REESCRIBIRLO
-----------------------------------------------------
`services.supabase_db` trae el pool, el reintento por conexión muerta y la
desinfección del candado heredado; `services.odoo.detalle_por_sku` trae el
`free_qty`, el factor de caja, los archivados y los 5 SKUs que están DOS veces
en Odoo. Todo eso es conocimiento medido que no se puede volver a deducir.

Lo que NO se importa es `main.py`: al arrancar, el backend levanta su scheduler
y ese sí escribe en producción. Aquí se importan módulos sueltos de `services/`,
que son inertes al importarse (`services/__init__.py` y `core/__init__.py`
están vacíos, verificado el 17-sep-2026).

REGLA 11 DE LA CASA: en una corrutina, nada síncrono. `psycopg2` y el `xmlrpc`
de Odoo BLOQUEAN el event loop entero mientras responden, no sólo a quien
llamó. Por eso aquí todo sale por `asyncio.to_thread` y las herramientas del
servidor nunca tocan `sdb.*` ni `odoo.*` directo.
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# `backend/` al FINAL del sys.path, no al principio. Este paquete se llama
# `mcp_research` y no `mcp` justamente para no tapar el SDK cuando `backend/`
# entra a la ruta; añadir al final es el cinturón sobre los tirantes.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.append(str(_BACKEND))

from config import settings                      # noqa: E402
from services import odoo                        # noqa: E402
from services import supabase_db as sdb          # noqa: E402

# Topes. El catálogo tiene ~13,200 SKUs y las ventas 29 mil renglones: una
# respuesta sin tope se come el contexto de quien pregunta y no sirve para nada.
LIMITE_DEFECTO = 50
LIMITE_MAXIMO = 500
MAX_SKUS = 500          # Odoo se pide en lotes de 200 por dentro


class SoloLectura(RuntimeError):
    """Se intentó correr algo que no es un SELECT."""


_ARRANQUE = re.compile(r"^\s*(select|with)\b", re.I)

# Verbos que escriben o cambian estado. Se buscan en TODA la sentencia, no sólo
# al principio, y esa es la lección de la prueba del 17-sep-2026: exigir que
# empiece en SELECT/WITH **no alcanza**, porque un CTE puede escribir —
#
#     with x as (update core.products set sku='x' returning 1) select * from x
#
# arranca con WITH, pasa cualquier guarda que sólo mire el primer token, y es
# un UPDATE a la tabla entera. En la prueba llegó a la base y sólo rebotó de
# suerte, contra una restricción de unicidad. Nada se escribió, pero el candado
# no tuvo nada que ver con eso, y un candado que depende de la suerte no es un
# candado.
#
# Una lista negra sobre el texto completo sería frágil en un motor de consultas
# genérico; aquí NO lo es, porque todo el SQL de este servidor es un literal
# escrito en este paquete y los valores viajan por `%s` de psycopg2. Ninguna de
# las consultas propias contiene estas palabras como palabra suelta: `\b` deja
# pasar `updated_at`, `metrics_updated_at` y `offset` sin falsos positivos.
_PROHIBIDOS = (
    "insert", "update", "delete", "merge", "truncate", "drop", "alter",
    "create", "grant", "revoke", "copy", "vacuum", "analyze", "refresh",
    "call", "do", "execute", "prepare", "discard", "reindex", "cluster",
    "comment", "lock", "listen", "notify", "unlisten", "reset", "set",
    "begin", "commit", "rollback", "savepoint", "import", "security",
)
_RE_PROHIBIDOS = re.compile(r"\b(" + "|".join(_PROHIBIDOS) + r")\b", re.I)


def _desnudar(sql: str) -> str:
    """
    Quita comentarios y literales de texto.

    Los literales se van para que el candado mire la ESTRUCTURA y no los datos:
    sin esto, un `where titulo = 'set de copas'` se leería como un `SET`.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:''|[^'])*'", " '' ", sql)          # 'texto'
    sql = re.sub(r"\$([A-Za-z_]*)\$.*?\$\1\$", " '' ", sql, flags=re.S)   # $$texto$$
    return sql


def _verificar_lectura(sql: str) -> None:
    """
    Candado de código, no de sesión (ver el encabezado del módulo).

    Tres reglas, y las tres hacen falta: que empiece en SELECT/WITH, que no
    encadene sentencias con `;`, y que no aparezca ningún verbo de escritura en
    ninguna parte —incluido dentro de un CTE, que es por donde se coló la
    primera versión—.
    """
    limpio = _desnudar(sql)
    if not _ARRANQUE.match(limpio):
        raise SoloLectura(
            "Este MCP es de SOLO LECTURA: la consulta debe empezar en SELECT o WITH."
        )
    if ";" in limpio.strip().rstrip(";"):
        raise SoloLectura(
            "Este MCP es de SOLO LECTURA: no se permite encadenar sentencias."
        )
    hallado = _RE_PROHIBIDOS.search(limpio)
    if hallado:
        raise SoloLectura(
            f"Este MCP es de SOLO LECTURA: la consulta contiene "
            f"'{hallado.group(1).upper()}'. Ni siquiera dentro de un CTE."
        )


async def consultar(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    """Un SELECT a kubera, fuera del event loop (regla 11)."""
    _verificar_lectura(sql)
    return await asyncio.to_thread(sdb.fetch_all, sql, params)


async def odoo_detalle(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    `{sku: {libre, fisico, reservado, piezas_por_caja, cbm_caja, …}}` en vivo.

    Se reusa `services.odoo.detalle_por_sku` a propósito: pide `free_qty`
    explícito (NO `qty_available`), ve los archivados, y resuelve los 5 SKUs
    que tienen dos productos en Odoo quedándose con el de más existencia y
    MARCÁNDOLO (`duplicado`). Reescribirlo aquí sería perder las tres cosas.
    """
    return await asyncio.to_thread(odoo.detalle_por_sku, skus)


def limitar(limite: int | None) -> int:
    if not limite or limite < 1:
        return LIMITE_DEFECTO
    return min(int(limite), LIMITE_MAXIMO)


def normalizar_skus(skus: list[str] | str | None) -> list[str]:
    """Acepta lista o cadena separada por comas/espacios; quita repetidos y topa."""
    if skus is None:
        return []
    if isinstance(skus, str):
        skus = [t for t in re.split(r"[,\s]+", skus) if t]
    vistos: list[str] = []
    for s in skus:
        s = (s or "").strip()
        if s and s not in vistos:
            vistos.append(s)
    return vistos[:MAX_SKUS]


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso(valor: Any) -> Any:
    """Fechas a texto ISO; lo demás tal cual. Decimal lo maneja el serializador."""
    if isinstance(valor, datetime):
        return valor.isoformat(timespec="seconds")
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return valor


# ── Traducir cuentas ────────────────────────────────────────────────────────
# `core.accounts` mapea `legacy_code → label`. Pero las ventas traen cuentas
# que NO están en esa tabla: medido el 17-sep-2026, `sales_daily_completa` usa
# `TIKTOK` y `accounts` registra ese canal como `KUBERA`. Un join a secas
# dejaría a TikTok sin nombre, así que hay un segundo intento POR CANAL para
# los canales de una sola cuenta. Si ninguno acierta se devuelve `None` y se
# muestra el código crudo: inventar el nombre sería peor que no tenerlo.
_CUENTAS: dict[str, Any] | None = None


async def cuentas() -> dict[str, Any]:
    global _CUENTAS
    if _CUENTAS is None:
        filas = await consultar(
            "select legacy_code, label, channel_id from core.accounts"
        )
        por_codigo = {f["legacy_code"]: f["label"] for f in filas if f["legacy_code"]}
        por_canal: dict[str, str] = {}
        for f in filas:
            canal = f["channel_id"]
            # Canal con más de una cuenta (mercado_libre) → no se puede adivinar.
            por_canal[canal] = "" if canal in por_canal else (f["label"] or "")
        _CUENTAS = {"por_codigo": por_codigo,
                    "por_canal": {k: v for k, v in por_canal.items() if v}}
    return _CUENTAS


async def nombre_cuenta(codigo: str | None, canal: str | None = None) -> str | None:
    if not codigo:
        return None
    m = await cuentas()
    return m["por_codigo"].get(codigo) or (m["por_canal"].get(canal) if canal else None)


def configurado() -> dict[str, bool]:
    """Qué credenciales hay. Se usa al arrancar para fallar temprano y claro."""
    return {"supabase": bool(settings.supabase_db_url),
            "odoo": bool(settings.odoo_url and settings.odoo_db and settings.odoo_user)}
