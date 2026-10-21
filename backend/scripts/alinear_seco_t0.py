#!/usr/bin/env python
"""
alinear_seco_t0.py — La alineación de T0 EN SECO: qué le escribiría el fan-out a
cada publicación DROP cuando reparta el número del libro de kubera
(`FANOUT_FUENTE=kubera`), sin escribir nada en ningún lado.

POR QUÉ EXISTE
--------------
En T0 (ruta directa del plan de salida de Woo, Eduardo, 20-oct) el fan-out deja
de repartir el `_stock` de Woo y reparte el vendible del libro
(`services/stock_libro.py`). La primera pasada de `stock_watch` en modo libro
encola cada SKU en que el libro no coincide con el último número de Woo, y el
tope (`STOCK_WATCH_TOPE`, 300) la frena si son muchos: forzarla es un acto del
acta de T0. Antes de eso hay que saber, publicación por publicación, qué va a
pasar: cuántas bajan, cuántas suben, cuántas piezas se mueven y cuáles quedan
en una situación que el fan-out solo no resuelve.
`scripts/comparar_libro_vs_woo.py` compara NÚMEROS por SKU; éste mira las
PUBLICACIONES, que es donde el número llega a un canal.

QUÉ CALCULA (con la lógica real, no con una copia)
--------------------------------------------------
Por cada publicación de `channel.listings` en los canales del fan-out (ML en sus
dos cuentas, TikTok y Temu), en proceso y con `FANOUT_FUENTE=kubera`:

  · VIVA O LATENTE = lo que `fanout_stock._destinos` no descarta: ML activa o
    pausada (no FULL), TikTok salvo DELETED, Temu salvo Borrador. Lo descartado
    (FULL, cerrada, borrada, borrador) sólo se cuenta, por motivo.
  · OBJETIVO = el de `fanout_stock.plan()` (stock del libro − `FANOUT_RESERVA`,
    nunca negativo; 0 si el libro no trae el SKU, D4). `plan()` no escribe ni
    encola; aquí corre tal cual, con la lectura del libro (`stock_libro.desglose`)
    y la de las publicaciones (`channel_read.leer_inventario`) hechas UNA vez para
    todo el catálogo y servidas desde memoria SKU por SKU (sin eso serían ~3
    consultas por SKU).
  · VIVA = a la venta hoy (ML activa, TikTok ACTIVATE, Temu 2/8); LATENTE = el
    fan-out le escribe pero hoy no vende (ML pausada, TikTok fuera de ACTIVATE,
    Temu fuera de 2/8). `seguro_apagaria` = con objetivo 0, el seguro de stock 0
    (`fanout_seguro.apagable`: Temu 2/8 y 3/1, TikTok ACTIVATE) la sacaría de la
    venta; es una cota: el seguro además respeta padres, variantes hermanas con
    stock y FANOUT_CERO_EXCLUIR. ML con 0 deja de vender sin el seguro.
  · LA PRIMERA PASADA de `stock_watch`: `stock_watch._movidos_libro` contra la
    foto (`ops.stock_watch_photo`), el mismo conteo que su tope, MÁS los ceros de
    D4 que esa función no ve (ver abajo).

CATEGORÍAS (una por publicación viva o latente)
-----------------------------------------------
  a_cero_desconocido  el libro no trae el SKU: `plan()` da objetivo 0 (D4) y el
                      fan-out lo lleva a 0 en cuanto su SKU pasa por la cola. La
                      lista aprobada no cambia esto: entra AL LIBRO y ahí tiene
                      número; un aprobado que cae aquí es «aprobado pero sin
                      número en el libro» y también iría a 0.
  canal_desconocido   el libro lo conoce, pero no se sabe qué tiene el canal: el
                      fan-out no escribe a ciegas.
  sin_numero_en_woo   el libro lo conoce y Woo no tenía número (sin «gestionar
                      inventario» o sin el SKU): hoy el fan-out no lo gobierna y
                      desde T0 sí. `sentido` dice si baja, sube o queda igual.
  igual / baja / sube el objetivo contra lo que tiene el canal.
  sin_plan            `plan()` no dio objetivo (con el libro no debería pasar:
                      `_stock_drop` ya no devuelve None en modo libro).
  `cambia` = el objetivo es distinto de lo que tiene el canal (y los dos se saben).

PRIMERA PASADA Y LOS CEROS DE D4
--------------------------------
`_movidos_libro` cuenta los SKUs del libro cuyo vendible no coincide con su
último número (`stock_kubera`, o `stock_woo` si el libro nunca le dio número) y
los que SALEN del libro (tenían `stock_kubera`). Un SKU que el libro nunca trajo
no tiene `stock_kubera`: esa función no lo ve, aunque D4 lo lleve a 0. Aquí se
suman al conteo contra el tope como `cero_d4`: los que el libro no trae con un
último número distinto de 0, o sin número y con publicación viva o latente. La
columna `primera_pasada` dice `libro` (lo cuenta `_movidos_libro`), `cero_d4` o
nada.

APROBADOS (`--aprobados`)
-------------------------
La lista aprobada ENTRA AL LIBRO y tiene número (D4): el script no fija
objetivos, VERIFICA. Cada SKU aprobado debe estar en `stock_libro.todos()`; los
que falten salen como «aprobado pero sin número en el libro: iría a 0».

Amazon y Walmart quedan FUERA por D2 (el fan-out no les escribe): se miden aparte,
contra su `stock_own`, sin consultar al canal (Amazon en vivo NUNCA se llama).

SÓLO LECTURA
------------
Una sola conexión propia y UNA transacción `BEGIN; SET TRANSACTION READ ONLY; …;
ROLLBACK` (cada consulta en su savepoint, para que un error no envenene las
demás). Nunca `set_session(readonly=True)` (regla 13: se pega en el pooler).
`supabase_db` se desvía a esa conexión y sus escrituras, MySQL y la base de
WordPress quedan anuladas: si algo intentara escribir o leer Woo, revienta.
No llama a Woo, Odoo ni a ningún marketplace. El DSN no se imprime.

ENTORNOS
--------
  --entorno sandbox (por omisión)  DSN de `SUPABASE_DB_URL` (con APP_ENV=staging,
                                   el de env.staging). Se niega si es producción.
  --entorno prod                   DSN por stdin (`--dsn-stdin`) o SUPABASE_DB_URL.
                                   Se niega si NO es producción.
  Los dos exigen `SUPABASE_PROD_REF` para saber cuál es cuál (falla cerrada).

SALIDA
------
  CSV (una fila por publicación; por omisión en la carpeta temporal: el repo es
  público) + resumen en pantalla con el cotejo contra lo medido por MAESTRO.
  0  medido.
  2  el libro no pasa el piso (`LibroNoDisponible`) o la foto no se puede leer.
  3  candado de entorno, DSN o argumentos.

USO
---
  cd backend
  APP_ENV=staging STOCK_LIBRO_DESDE=2026-10-15T16:48:47Z \\
    python scripts/alinear_seco_t0.py [--aprobados aprobados.csv] [--salida x.csv]
  railway variable list --kv -s <backend> | python scripts/alinear_seco_t0.py --entorno prod --dsn-stdin
  `--aprobados`: CSV con encabezado `sku` y, opcional, `motivo` (una columna
  `objetivo` se ignora: el número de un aprobado sale del libro).
  `--desde` / `--reserva` / `--tope` sustituyen STOCK_LIBRO_DESDE / FANOUT_RESERVA /
  STOCK_WATCH_TOPE sólo en este proceso.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CANALES_FANOUT = ("mercado_libre", "tiktok", "temu")
CANALES_D2 = ("amazon", "walmart")

# Lo que midió MAESTRO (20-oct) para cotejar. `None` en la columna de aquí = no se
# puede medir con lo que este script lee (lo dice el resumen).
MAESTRO = {
    "huerfanos": (24, 2437),            # SKUs, piezas
    "recodificaciones": (18, 2154),     # SKUs, +piezas
    "ml_sin_numero_woo": (566, 189720),  # publicaciones DROP, piezas
    "tiktok_sin_numero_woo": (7, 902),
    "temu_sin_numero_woo": (1, 28),
}

CAMPOS_CSV = ["alcance", "canal", "cuenta", "item_id", "sku", "estado_canal", "vida", "categoria",
              "sentido", "cambia", "canal_actual", "objetivo", "delta", "libro", "woo",
              "seguro_apagaria", "primera_pasada", "plan_accion", "plan_motivo", "aprobado_motivo"]

_SQL_SKUS = """
    select distinct upper(sku::text) as sku
      from channel.listings
     where canal = any(%(c)s) and sku is not null
       and not (nullif(listing_id, '') is null and situacion is null and price is null
                and stock_own is null and logistic_type is null)"""

_SQL_D2 = """
    select upper(l.sku::text) as sku, l.canal, a.legacy_code as cuenta,
           l.listing_id as item_id, l.stock_own, l.situacion, l.status as estado_canal
      from channel.listings l
      join core.accounts a on a.id = l.account_id
     where l.canal = any(%(c)s) and not coalesce(l.is_fulfillment, false)
       and l.sku is not null
       and not (nullif(l.listing_id, '') is null and l.situacion is null and l.price is null
                and l.stock_own is null and l.logistic_type is null)"""


class Abortar(RuntimeError):
    """Termina el script con `codigo` y un mensaje sin secretos."""

    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo


# ── Candado de entorno ───────────────────────────────────────────────────────

_RE_URI = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
_RE_CLAVE = re.compile(r"(password|passwd|pwd)\s*=\s*\S+", re.I)


def sin_secretos(texto: Any) -> str:
    """Un mensaje de error sin DSN ni contraseñas (psycopg2 puede citar la cadena)."""
    return _RE_CLAVE.sub(r"\1=***", _RE_URI.sub("<dsn>", str(texto)))


def ref_de_dsn(dsn: str) -> str | None:
    """El proyecto de Supabase de un DSN: el usuario del pooler (`postgres.<ref>`) o
    el host directo (`db.<ref>.supabase.co`)."""
    m = (re.search(r"//postgres\.([a-z0-9]+)[:@]", dsn or "")
         or re.search(r"@db\.([a-z0-9]+)\.supabase\.co", dsn or ""))
    return m.group(1) if m else None


def candado(entorno: str, dsn: str, prod_ref: str) -> str:
    """El ref del DSN si cuadra con el entorno pedido; si no, `Abortar(3)`."""
    if not dsn:
        raise Abortar(3, "falta el DSN: SUPABASE_DB_URL (sandbox: corre con APP_ENV=staging y "
                         "env.staging en la raíz) o --dsn-stdin")
    ref = ref_de_dsn(dsn)
    if not ref:
        raise Abortar(3, "no reconozco el proyecto de Supabase en el DSN: me niego a adivinar")
    prod_ref = (prod_ref or "").strip()
    if not prod_ref:
        raise Abortar(3, "falta SUPABASE_PROD_REF: sin ella no sé si el DSN es producción")
    if entorno == "sandbox" and ref == prod_ref:
        raise Abortar(3, "pediste --entorno sandbox y el DSN apunta a PRODUCCIÓN")
    if entorno == "prod" and ref != prod_ref:
        raise Abortar(3, f"pediste --entorno prod y el DSN apunta a otro proyecto ({ref[:8]}…)")
    return ref


# ── Sólo lectura: una transacción, savepoint por consulta, ROLLBACK ──────────

class SoloLectura:
    """Una conexión propia con UNA transacción `read only` que termina en ROLLBACK.

    `fetch_all`/`fetch_one`/`fetch_scalar` tienen la firma de `supabase_db`; cada
    consulta va en su savepoint: si truena, se regresa a él y la transacción sigue
    sirviendo (y la excepción sube a quien preguntó, igual que con el pool)."""

    def __init__(self, conn: Any):
        self.conn = conn
        self._cur = conn.cursor()
        self._cur.execute("set transaction read only")

    def fetch_all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        self._cur.execute("savepoint alinear_seco")
        try:
            self._cur.execute(sql, params)
            filas = [dict(r) for r in self._cur.fetchall()]
        except Exception:
            self._cur.execute("rollback to savepoint alinear_seco")
            raise
        self._cur.execute("release savepoint alinear_seco")
        return filas

    def fetch_one(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        filas = self.fetch_all(sql, params)
        return filas[0] if filas else None

    def fetch_scalar(self, sql: str, params: Any = None) -> Any:
        fila = self.fetch_one(sql, params)
        return next(iter(fila.values())) if fila else None

    def cerrar(self) -> None:
        try:
            self.conn.rollback()
        finally:
            self.conn.close()


def _prohibido(nombre: str) -> Callable[..., Any]:
    def _no(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(f"alinear_seco_t0 es de SÓLO LECTURA: nada llama a {nombre}")
    return _no


@contextlib.contextmanager
def desviar(lector: SoloLectura):
    """`supabase_db` lee por `lector`; sus escrituras, MySQL y WordPress revientan."""
    from services import db, wp_db
    from services import supabase_db as sdb
    with contextlib.ExitStack() as pila:
        for nombre in ("fetch_all", "fetch_one", "fetch_scalar"):
            pila.enter_context(mock.patch.object(sdb, nombre, getattr(lector, nombre)))
        pila.enter_context(mock.patch.object(sdb, "disponible", lambda: True))
        for nombre in ("execute", "execute_returning", "get_cursor"):
            pila.enter_context(mock.patch.object(sdb, nombre, _prohibido(f"supabase_db.{nombre}")))
        for nombre in ("fetch_all", "fetch_one", "fetch_scalar", "execute", "get_cursor"):
            pila.enter_context(mock.patch.object(db, nombre, _prohibido(f"MySQL ({nombre})")))
        for nombre in ("_fetch_all", "_cursor"):
            pila.enter_context(mock.patch.object(wp_db, nombre, _prohibido("WordPress")))
        yield


# ── Entradas ─────────────────────────────────────────────────────────────────

def leer_aprobados(filas) -> dict[str, str]:
    """{SKU: motivo} del CSV `--aprobados`. Sin columna `sku` → `Abortar(3)`. Una
    columna `objetivo` se ignora (D4: el aprobado entra al libro con su número)."""
    campos = getattr(filas, "fieldnames", None)
    if campos is not None and "sku" not in [str(c).strip().lower() for c in campos]:
        raise Abortar(3, "--aprobados: falta la columna `sku`")
    salida: dict[str, str] = {}
    for r in filas:
        r = {str(k).strip().lower(): v for k, v in r.items() if k is not None}
        sku = (r.get("sku") or "").strip().upper()
        if sku:
            salida[sku] = (r.get("motivo") or "").strip() or "aprobado"
    return salida


# ── Clasificación ────────────────────────────────────────────────────────────

def sentido(objetivo: int | None, actual: int | None) -> str:
    if objetivo is None:
        return "no_se_toca"
    if actual is None:
        return "desconocido"
    if objetivo == actual:
        return "igual"
    return "baja" if objetivo < actual else "sube"


def clasificar(libro: int | None, objetivo: int | None, woo: int | None,
               actual: int | None) -> dict[str, Any]:
    """La categoría, el objetivo y el sentido de UNA publicación viva o latente.

    `libro` = el vendible del libro (None = `todos()` no trae el SKU); `objetivo` =
    el de `plan()` (con el libro, 0 para lo que no trae: D4; None = sin plan);
    `woo` = el último número de Woo (None = sin número); `actual` = lo que tiene
    el canal (None = desconocido)."""
    if objetivo is None:
        cat = "sin_plan"
    elif libro is None:
        cat = "a_cero_desconocido"
    elif actual is None:
        cat = "canal_desconocido"
    elif woo is None:
        cat = "sin_numero_en_woo"
    else:
        cat = sentido(objetivo, actual)
    s = sentido(objetivo, actual)
    return {"categoria": cat, "objetivo": objetivo, "sentido": s, "cambia": s in ("baja", "sube"),
            "delta": (objetivo - actual) if s in ("baja", "sube") else 0}


def vida(canal: str, situacion: Any, estado: Any) -> str:
    """«viva» (a la venta hoy) o «latente» (el fan-out le escribe, hoy no vende)."""
    from services import fanout_seguro, fanout_stock
    canal = (canal or "").lower()
    if canal == "mercado_libre":
        return "viva" if str(situacion or "").lower() in fanout_stock._SITUACIONES_VIVAS else "latente"
    return "viva" if fanout_seguro.a_la_venta(canal, estado) else "latente"


def seguro_apagaria(canal: str, estado: Any, objetivo: int | None) -> bool:
    """¿El seguro de stock 0 la sacaría de la venta? Cota: no mira padres, variantes
    hermanas con stock ni FANOUT_CERO_EXCLUIR."""
    from services import fanout_seguro
    canal = (canal or "").lower()
    return objetivo == 0 and canal in ("temu", "tiktok") and fanout_seguro.apagable(canal, estado)


def ceros_d4(libro: dict[str, int], foto: dict[str, dict[str, Any]], ya: set[str],
             con_pub: set[str]) -> list[tuple[str, int | None, int]]:
    """[(sku, antes, 0)]: lo que D4 lleva a 0 y `_movidos_libro` no cuenta (`ya`).

    El libro no lo trae y su último número (`stock_kubera`, si no `stock_woo`) no
    era 0, o no tenía número y tiene publicación viva o latente (`con_pub`)."""
    por_mayus = {str(s).upper(): v for s, v in foto.items()}
    salida: list[tuple[str, int | None, int]] = []
    for s in sorted(set(por_mayus) | con_pub):
        if s in libro or s in ya:
            continue
        f = por_mayus.get(s) or {}
        ant = f.get("kubera") if f.get("kubera") is not None else f.get("woo")
        if (ant is None and s in con_pub) or (ant is not None and ant != 0):
            salida.append((s, ant, 0))
    return salida


# ── La medición ──────────────────────────────────────────────────────────────

def _memo_desglose(real: Callable[..., dict]) -> Callable[..., dict]:
    """`stock_libro.desglose` leído UNA vez (todo el libro); las tandas posteriores
    se sirven de ahí. Es lo mismo que pedir la tanda: el filtro por SKU sólo recorta."""
    cache: dict[str, dict[str, Any]] = {}

    def desglose(skus: list[str] | None = None) -> dict[str, dict[str, int | None]]:
        if "todo" not in cache:
            cache["todo"] = real(None)
        todo = cache["todo"]
        if skus is None:
            return dict(todo)
        pedidos = {str(s).upper() for s in skus}
        return {s: d for s, d in todo.items() if s in pedidos}
    return desglose


def _memo_inventario(filas: dict[str, dict[str, dict[str, Any]]], canales: tuple[str, ...]
                     ) -> Callable[[list[str]], dict]:
    """`channel_read.leer_inventario` servido de una lectura completa, sólo con los
    canales del fan-out (Amazon fuera: `_destinos` lo consultaría en vivo)."""
    por_sku: dict[str, dict[str, dict[str, Any]]] = {}
    for sku, por_canal in filas.items():
        for clave, f in por_canal.items():
            if (f.get("canal") or "").lower() in canales:
                por_sku.setdefault(str(sku).upper(), {})[clave] = f

    def leer_inventario(skus: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
        return {s: por_sku[s] for s in {str(x).upper() for x in skus} if s in por_sku}
    return leer_inventario


def medir(aprobados: dict[str, str], reserva: int | None = None,
          tope: int | None = None) -> dict[str, Any]:
    """Toda la medición. Requiere `supabase_db` ya desviado (ver `desviar`).
    LANZA `stock_libro.LibroNoDisponible` si el libro no se puede leer y
    `Abortar(2)` si la foto de Woo tampoco."""
    from config import settings
    from services import channel_read, fanout_stock, stock_libro, stock_watch, stock_watch_read
    from services import supabase_db as sdb

    with contextlib.ExitStack() as pila:
        pila.enter_context(mock.patch.object(settings, "fanout_fuente", "kubera"))
        if reserva is not None:
            pila.enter_context(mock.patch.object(settings, "fanout_reserva", reserva))
        if tope is not None:
            pila.enter_context(mock.patch.object(settings, "stock_watch_tope", tope))
        pila.enter_context(mock.patch.object(fanout_stock, "_amazon_en_vivo",
                                             _prohibido("Amazon en vivo")))
        memo = _memo_desglose(stock_libro.desglose)
        pila.enter_context(mock.patch.object(stock_libro, "desglose", memo))

        desglose = memo(None)                      # LibroNoDisponible sube desde aquí
        libro = stock_libro.todos()                # {SKU: vendible} con la fórmula real
        try:
            foto = stock_watch_read.foto_leer(True)
        except Exception as exc:  # noqa: BLE001 — sin foto no hay «último Woo» ni primera pasada
            raise Abortar(2, f"no se pudo leer la foto de Woo (ops.stock_watch_photo): "
                             f"{sin_secretos(exc)}") from exc
        woo = {str(s).upper(): v.get("woo") for s, v in foto.items()}
        foto_act = max((v["act"] for v in foto.values() if v.get("act") is not None), default=None)

        skus = sorted(r["sku"] for r in sdb.fetch_all(_SQL_SKUS, {"c": list(CANALES_FANOUT)}))
        inventario = channel_read.leer_inventario(skus) if skus else {}
        memo_inv = _memo_inventario(inventario, CANALES_FANOUT)
        pila.enter_context(mock.patch.object(channel_read, "leer_inventario", memo_inv))

        cambios, salen = stock_watch._movidos_libro(libro, foto)
        en_primera = {s.upper() for s, _a, _b in cambios}
        vistos = stock_watch._vistos_con_piezas(foto)

        filas: list[dict[str, Any]] = []
        fuera: dict[str, int] = {}
        drop_sin_woo_todas: dict[str, int] = {}     # DROP no FULL sin número en Woo, vivas o no
        for sku in skus:
            # Las filas crudas en el MISMO orden en que `_destinos` las recorre (una por
            # destino): de ahí salen `situacion` y `estado_canal` para viva/latente.
            crudas = [f for por in memo_inv([sku]).values() for f in por.values()]
            destinos = fanout_stock._destinos(sku)
            plan = fanout_stock.plan(sku)
            ok = bool(plan.get("ok"))
            acciones = (plan.get("acciones") or []) if ok else []
            n_libro = libro.get(sku)
            for i, d in enumerate(destinos):
                canal = (d.get("canal") or "").lower()
                motivo = d.get("omitido_por")
                if motivo:
                    clave = f"{canal}: {motivo.split(' — ')[0].split(' (')[0]}"
                    fuera[clave] = fuera.get(clave, 0) + 1
                    if not motivo.startswith("FULL") and woo.get(sku) is None:
                        drop_sin_woo_todas[canal] = drop_sin_woo_todas.get(canal, 0) + 1
                    continue
                cruda = crudas[i] if i < len(crudas) and (crudas[i].get("canal") or "").lower() == canal \
                    else {}
                estado = cruda.get("estado_canal") or d.get("estado_canal")
                a = acciones[i] if i < len(acciones) else {}
                actual = d.get("stock_actual_canal")
                c = clasificar(n_libro, plan.get("objetivo") if ok else None, woo.get(sku), actual)
                if woo.get(sku) is None:
                    drop_sin_woo_todas[canal] = drop_sin_woo_todas.get(canal, 0) + 1
                filas.append({
                    "alcance": "fanout", "canal": canal, "cuenta": d.get("cuenta") or "",
                    "item_id": d.get("item_id"), "sku": sku, "estado_canal": estado,
                    "vida": vida(canal, cruda.get("situacion"), estado),
                    **c, "canal_actual": actual, "libro": n_libro, "woo": woo.get(sku),
                    "seguro_apagaria": seguro_apagaria(canal, estado, c["objetivo"]),
                    "primera_pasada": "libro" if sku in en_primera else "",
                    "plan_accion": a.get("accion") or ("" if ok else "sin_plan"),
                    "plan_motivo": a.get("omitido_por") or ("" if ok else plan.get("motivo")),
                    "aprobado_motivo": aprobados.get(sku, ""),
                })

        # D4: lo que el libro no trae va a 0 y la primera pasada lo tiene que contar
        # aunque `_movidos_libro` no lo vea (sin `stock_kubera` en la foto).
        ceros = ceros_d4(libro, foto, en_primera, {f["sku"] for f in filas})
        en_ceros = {s for s, _a, _b in ceros}
        for f in filas:
            if f["sku"] in en_ceros:
                f["primera_pasada"] = "cero_d4"

        d2 = []
        for r in sdb.fetch_all(_SQL_D2, {"c": list(CANALES_D2)}):
            sku = r["sku"]
            n_libro = libro.get(sku)
            obj = max(0, (n_libro or 0) - fanout_stock._reserva())     # D4: sin libro, 0
            c = clasificar(n_libro, obj, woo.get(sku), r.get("stock_own"))
            d2.append({"alcance": "fuera_d2", "canal": r["canal"], "cuenta": r.get("cuenta") or "",
                       "item_id": r.get("item_id"), "sku": sku,
                       "estado_canal": r.get("estado_canal") or r.get("situacion"), "vida": "", **c,
                       "canal_actual": r.get("stock_own"), "libro": n_libro, "woo": woo.get(sku),
                       "seguro_apagaria": False,
                       "primera_pasada": ("libro" if sku in en_primera
                                          else "cero_d4" if sku in en_ceros else ""),
                       "plan_accion": "fuera_d2",
                       "plan_motivo": "Amazon/Walmart fuera del fan-out por D2",
                       "aprobado_motivo": aprobados.get(sku, "")})

        return {
            "libro_skus": len(libro), "libro_con_piezas": sum(1 for v in libro.values() if v > 0),
            "libro_pendientes": sum(1 for d in desglose.values() if d.get("pendientes")),
            "desde": str(getattr(settings, "stock_libro_desde", "")),
            "reserva": fanout_stock._reserva(), "tope": stock_watch.tope(),
            "foto_skus": len(foto), "foto_con_woo": sum(1 for v in woo.values() if v is not None),
            "foto_act": foto_act,
            "primera": {"cambios": cambios, "salen": salen, "ceros_d4": ceros,
                        "vistos_kubera": len(vistos)},
            "filas": filas, "d2": d2, "fuera": fuera, "skus_universo": len(skus),
            "drop_sin_woo_todas": drop_sin_woo_todas,
            "aprobados": verificar_aprobados(aprobados, libro, filas, woo),
        }


def verificar_aprobados(aprobados: dict[str, str], libro: dict[str, int],
                        filas: list[dict[str, Any]], woo: dict[str, int | None]) -> dict[str, Any]:
    """La lista aprobada ENTRA al libro (D4): cada SKU debe estar en `todos()`. Los
    que falten van a 0 como cualquier desconocido."""
    por_sku: dict[str, list[dict[str, Any]]] = {}
    for f in filas:
        por_sku.setdefault(f["sku"], []).append(f)
    con_numero = [s for s in aprobados if s in libro]
    sin_numero = []
    for s in sorted(set(aprobados) - set(con_numero)):
        pubs = por_sku.get(s, [])
        sin_numero.append({
            "sku": s, "motivo": aprobados[s], "woo": woo.get(s),
            "vivas": sum(1 for f in pubs if f["vida"] == "viva"),
            "latentes": sum(1 for f in pubs if f["vida"] == "latente"),
            "pzs_canal": sum(int(f["canal_actual"] or 0) for f in pubs)})
    return {"total": len(aprobados), "con_numero": len(con_numero),
            "en_cero": sum(1 for s in con_numero if libro[s] == 0), "sin_numero": sin_numero}


# ── El resumen ───────────────────────────────────────────────────────────────

def _n(x: int | None) -> str:
    return "—" if x is None else f"{x:,}"


def _agrupar(filas: list[dict[str, Any]], llave: Callable[[dict], Any]) -> dict[Any, dict[str, int]]:
    g: dict[Any, dict[str, Any]] = {}
    for f in filas:
        k = llave(f)
        a = g.setdefault(k, {"pubs": 0, "skus": set(), "cambian": 0, "pzs_canal": 0,
                             "pzs_objetivo": 0, "baja": 0, "sube": 0})
        a["pubs"] += 1
        a["skus"].add(f["sku"])
        a["pzs_canal"] += int(f["canal_actual"] or 0)
        a["pzs_objetivo"] += int(f["objetivo"] or 0) if f["objetivo"] is not None \
            else int(f["canal_actual"] or 0)
        if f["cambia"]:
            a["cambian"] += 1
            a["baja" if f["delta"] < 0 else "sube"] += f["delta"]
    return {k: {**v, "skus": len(v["skus"])} for k, v in g.items()}


def cotejo(m: dict[str, Any]) -> list[tuple[str, tuple[int, int], tuple[int | None, int | None], str]]:
    """(qué, lo de MAESTRO, lo medido aquí, nota) para cada número de MAESTRO."""
    filas = m["filas"]
    huerf = {f["sku"]: f["woo"] for f in filas
             if f["libro"] is None and (f["woo"] or 0) > 0}
    salida = [("huérfanos (el libro no conoce, Woo > 0)", MAESTRO["huerfanos"],
               (len(huerf), sum(huerf.values())),
               "SKUs con publicación viva o latente; piezas = las de Woo")]
    salida.append(("recodificaciones", MAESTRO["recodificaciones"], (None, None),
                   "no se distinguen aquí: el código viejo sale a_cero_desconocido (a 0 por "
                   "D4) y el nuevo sin_numero_en_woo/sube; la lista aprobada se verifica "
                   "con --aprobados"))
    for canal, clave in (("mercado_libre", "ml_sin_numero_woo"), ("tiktok", "tiktok_sin_numero_woo"),
                         ("temu", "temu_sin_numero_woo")):
        sel = [f for f in filas if f["canal"] == canal and f["woo"] is None]
        todas = m["drop_sin_woo_todas"].get(canal, 0)
        salida.append((f"{canal} DROP sin número en Woo", MAESTRO[clave],
                       (len(sel), sum(int(f["canal_actual"] or 0) for f in sel)),
                       f"vivas o latentes; con las DROP fuera de la venta: {todas:,}"))
    return salida


def _resumen_d4(filas: list[dict[str, Any]]) -> list[str]:
    """Lo que D4 lleva a 0, vivas contra latentes: el seguro puede sacarlas de la venta."""
    d4 = [f for f in filas if f["categoria"] == "a_cero_desconocido"]
    if not d4:
        return []
    L = ["", f"D4 · EL LIBRO NO LOS TRAE → 0 (objetivo 0 en plan()): {len(d4):,} publicaciones de "
             f"{len({f['sku'] for f in d4}):,} SKUs · bajan {-sum(f['delta'] for f in d4):,} pzs"]
    for v in ("viva", "latente"):
        sel = [f for f in d4 if f["vida"] == v]
        con = [f for f in sel if f["cambia"]]
        L.append(f"  {v + 's':<9}{len(sel):>7,} pubs · {len(con):,} con piezas en el canal "
                 f"({-sum(f['delta'] for f in con):,} pzs) · el seguro sacaría de la venta "
                 f"{sum(1 for f in sel if f['seguro_apagaria']):,}")
    for canal in sorted({f["canal"] for f in d4}):
        sel = [f for f in d4 if f["canal"] == canal]
        partes = []
        for v in ("viva", "latente"):
            s = [f for f in sel if f["vida"] == v]
            partes.append(f"{len(s):,} {v}s ({sum(1 for f in s if f['cambia']):,} con piezas, "
                          f"seguro {sum(1 for f in s if f['seguro_apagaria']):,})")
        L.append(f"    {canal:<14}" + " · ".join(partes))
    L.append("  (seguro = Temu 2/8 y 3/1, TikTok ACTIVATE; es cota: respeta padres, variantes "
             "hermanas con stock y FANOUT_CERO_EXCLUIR. ML con 0 deja de vender sin el seguro)")
    return L


def _resumen_aprobados(ap: dict[str, Any], n: int = 40) -> list[str]:
    if not ap["total"]:
        return []
    sin = ap["sin_numero"]
    L = ["", f"LISTA APROBADA (--aprobados, {ap['total']:,} SKUs; entra al libro y tiene número): "
             f"{ap['con_numero']:,} en el libro ({ap['en_cero']:,} con 0) · {len(sin):,} aprobado "
             f"pero sin número en el libro: iría a 0"]
    for a in sin[:n]:
        L.append(f"    {a['sku']:<22} woo {a['woo']!s:>6} · {a['vivas']:,} vivas / {a['latentes']:,} "
                 f"latentes · {a['pzs_canal']:,} pzs en el canal · {a['motivo']}")
    if len(sin) > n:
        L.append(f"    … y {len(sin) - n:,} más")
    return L


def resumen(m: dict[str, Any], entorno: str) -> list[str]:
    L: list[str] = []
    ahora = datetime.now(timezone.utc)
    L.append(f"ALINEACIÓN EN SECO DE T0 · {entorno} · {ahora:%Y-%m-%d %H:%M} UTC · NADA SE ESCRIBIÓ")
    L.append(f"Libro: {m['libro_skus']:,} SKUs con número ({m['libro_con_piezas']:,} con piezas, "
             f"{m['libro_pendientes']:,} con ventas DROP sin OV) · STOCK_LIBRO_DESDE={m['desde']}"
             f" · FANOUT_RESERVA={m['reserva']}")
    act = m["foto_act"]
    L.append(f"Woo (foto ops.stock_watch_photo): {m['foto_con_woo']:,} SKUs con número de "
             f"{m['foto_skus']:,} · última pasada {act:%Y-%m-%d %H:%M} UTC" if act else
             f"Woo (foto ops.stock_watch_photo): {m['foto_con_woo']:,} SKUs con número (sin fecha)")

    p = m["primera"]
    tope = m["tope"]
    del_libro, ceros = p["cambios"], p["ceros_d4"]
    salen = sum(1 for s, _a, _b in del_libro if s.upper() in p["salen"])
    todos_ = list(del_libro) + list(ceros)
    total = len(todos_)
    netas = sum(b - (a or 0) for _s, a, b in todos_)
    bajan = sum(b - (a or 0) for _s, a, b in todos_ if b < (a or 0))
    suben = sum(b - (a or 0) for _s, a, b in todos_ if b > (a or 0))
    con_pub = {f["sku"] for f in m["filas"] if f["primera_pasada"]}
    escribe = [f for f in m["filas"] if f["primera_pasada"] and f["cambia"]]
    L.append("")
    L.append(f"PRIMERA PASADA de stock_watch (libro contra el último número): {total:,} cambios "
             f"contra el tope de {tope:,} → "
             + ("FRENARÍA: va forzada en el acta de T0" if total > tope else "pasa sin forzar"))
    L.append(f"  {len(del_libro):,} los cuenta `_movidos_libro`"
             + (f" ({salen:,} que el libro no trae, a 0)" if salen else "")
             + f" + {len(ceros):,} a 0 por D4 (el libro no los trae: "
             f"{sum(1 for _s, a, _b in ceros if a is not None):,} con número ≠ 0, "
             f"{sum(1 for _s, a, _b in ceros if a is None):,} sin número y con publicación)")
    if ceros:
        L.append(f"  OJO: fuera de la foto o sin ningún número, `_movidos_libro` no encola esos {len(ceros):,} "
                 f"(a lo sumo siembra su 0): bajan a 0 sólo cuando su SKU pase por la cola "
                 f"(venta, POST /api/fanout/alinear). Sin ellos contaría {len(del_libro):,} → "
                 + ("frenaría" if len(del_libro) > tope else "pasaría sin forzar"))
    L.append(f"  piezas: neto {netas:+,} (bajan {bajan:,} · suben {suben:+,}) · "
             f"{len(con_pub):,} de esos SKUs tienen publicación viva o latente → "
             f"{len(escribe):,} publicaciones cambiarían")
    if p["vistos_kubera"]:
        L.append(f"  piso: la pasada anterior vio {p['vistos_kubera']:,} SKUs del libro con piezas")

    filas = m["filas"]
    L.append("")
    L.append(f"PUBLICACIONES VIVAS O LATENTES del fan-out: {len(filas):,} "
             f"({m['skus_universo']:,} SKUs con publicación en {', '.join(CANALES_FANOUT)})")
    orden = ["igual", "baja", "sube", "sin_numero_en_woo", "a_cero_desconocido",
             "canal_desconocido", "sin_plan"]
    por_cat = _agrupar(filas, lambda f: f["categoria"])
    L.append(f"  {'categoría':<20}{'pubs':>8}{'SKUs':>7}{'cambian':>9}{'pzs canal':>11}"
             f"{'pzs objetivo':>14}{'bajan':>10}{'suben':>9}")
    for cat in orden + sorted(set(por_cat) - set(orden)):
        a = por_cat.get(cat)
        if not a:
            continue
        L.append(f"  {cat:<20}{a['pubs']:>8,}{a['skus']:>7,}{a['cambian']:>9,}{a['pzs_canal']:>11,}"
                 f"{a['pzs_objetivo']:>14,}{a['baja']:>10,}{a['sube']:>+9,}")
    cambian = [f for f in filas if f["cambia"]]
    L.append(f"  ALINEACIÓN COMPLETA: {len(cambian):,} publicaciones cambian "
             f"({len({f['sku'] for f in cambian}):,} SKUs), "
             f"{sum(f['delta'] for f in cambian if f['delta'] < 0):,} / "
             f"{sum(f['delta'] for f in cambian if f['delta'] > 0):+,} piezas")
    L.append("  por canal:")
    for (canal, cuenta), a in sorted(_agrupar(filas, lambda f: (f["canal"], f["cuenta"])).items()):
        L.append(f"    {canal:<14}{cuenta or '-':<15}{a['pubs']:>7,} pubs · {a['cambian']:,} cambian"
                 f" · {a['baja']:,} / {a['sube']:+,} pzs")
    if m["fuera"]:
        L.append("  fuera de la venta (no se tocan, sólo se cuentan):")
        for k, v in sorted(m["fuera"].items(), key=lambda x: -x[1])[:12]:
            L.append(f"    {v:>6,}  {k}")

    L.extend(_resumen_d4(filas))
    L.extend(_resumen_aprobados(m["aprobados"]))

    L.append("")
    L.append(f"FUERA POR D2 (Amazon y Walmart, no se escriben): {len(m['d2']):,} publicaciones DROP")
    for (canal, cat), a in sorted(_agrupar(m["d2"], lambda f: (f["canal"], f["categoria"])).items()):
        L.append(f"    {canal:<9}{cat:<20}{a['pubs']:>7,} pubs · {a['cambian']:,} cambiarían")

    L.append("")
    L.append("COTEJO CON MAESTRO (MAESTRO → aquí)")
    for que, suyo, mio, nota in cotejo(m):
        L.append(f"  {que:<44} {suyo[0]:>5,} / {suyo[1]:>8,} pzs  →  {_n(mio[0]):>5} / "
                 f"{_n(mio[1]):>8} pzs   ({nota})")
    return L


def muestra(m: dict[str, Any], n: int) -> list[str]:
    top = sorted((f for f in m["filas"] if f["cambia"]), key=lambda f: -abs(f["delta"]))[:n]
    return [f"  {f['sku']:<22} {f['canal']:<14} {f['cuenta'] or '-':<14} {f['categoria']:<19}"
            f" canal {f['canal_actual']!s:>6} → {f['objetivo']!s:>6}  (libro {f['libro']!s}, "
            f"woo {f['woo']!s})" for f in top]


def escribir_csv(filas: list[dict[str, Any]], ruta: Path) -> None:
    with ruta.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS_CSV, extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)


# ── main ─────────────────────────────────────────────────────────────────────

def de_stdin(texto: str) -> tuple[str, str]:
    """(DSN, SUPABASE_PROD_REF) de lo que llegó por stdin: una línea con el DSN a
    secas, o la salida de `railway variable list --kv` (KEY=valor por línea)."""
    dsn = prod_ref = ""
    for linea in (texto or "").splitlines():
        s = linea.strip()
        if s.startswith("SUPABASE_DB_URL="):
            dsn = dsn or s.split("=", 1)[1].strip().strip('"').strip("'")
        elif s.startswith("SUPABASE_PROD_REF="):
            prod_ref = prod_ref or s.split("=", 1)[1].strip().strip('"').strip("'")
        elif re.match(r"^postgres(ql)?://", s):
            dsn = dsn or s
    return dsn, prod_ref


def _conectar(dsn: str) -> Any:
    import psycopg2
    import psycopg2.extras
    try:
        return psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor,
                                application_name="alinear_seco_t0", connect_timeout=20)
    except Exception as exc:  # noqa: BLE001
        raise Abortar(3, f"no pude conectar: {type(exc).__name__}: {sin_secretos(exc)[:200]}") from exc


def main(argv: list[str] | None = None, conectar: Callable[[str], Any] = _conectar,
         stdin: Any = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--entorno", choices=("sandbox", "prod"), default="sandbox")
    ap.add_argument("--dsn-stdin", action="store_true",
                    help="lee el DSN de stdin: la cadena sola o la salida de "
                         "`railway variable list --kv` (no se imprime)")
    ap.add_argument("--aprobados", type=Path)
    ap.add_argument("--salida", type=Path)
    ap.add_argument("--desde", help="sustituye STOCK_LIBRO_DESDE (ISO con zona) en este proceso")
    ap.add_argument("--reserva", type=int, help="sustituye FANOUT_RESERVA en este proceso")
    ap.add_argument("--tope", type=int, help="sustituye STOCK_WATCH_TOPE en este proceso")
    ap.add_argument("--muestra", type=int, default=15)
    a = ap.parse_args(argv)

    from config import settings
    from services import stock_libro

    lector: SoloLectura | None = None
    try:
        aprobados: dict[str, str] = {}
        ignora_objetivo = False
        if a.aprobados:
            with a.aprobados.open(encoding="utf-8") as fh:
                lector_csv = csv.DictReader(fh)
                aprobados = leer_aprobados(lector_csv)
                ignora_objetivo = "objetivo" in [str(c).strip().lower()
                                                 for c in (lector_csv.fieldnames or [])]
        prod_ref = str(getattr(settings, "supabase_prod_ref", "") or "")
        if a.dsn_stdin:
            dsn, ref_stdin = de_stdin((stdin or sys.stdin).read())
            prod_ref = prod_ref or ref_stdin
        else:
            dsn = str(getattr(settings, "supabase_db_url", "") or "")
        ref = candado(a.entorno, dsn, prod_ref)
        print(f"Candado de entorno OK: {a.entorno}, Supabase ref={ref[:8]}… "
              f"(una transacción read only, ROLLBACK al final)")
        if ignora_objetivo:
            print("--aprobados: la columna `objetivo` se ignora (D4: un aprobado entra al libro "
                  "y su número sale de ahí; aquí sólo se verifica que esté)")
        lector = SoloLectura(conectar(dsn))
        with contextlib.ExitStack() as pila:
            if a.desde:
                pila.enter_context(mock.patch.object(settings, "stock_libro_desde", a.desde))
            pila.enter_context(desviar(lector))
            try:
                m = medir(aprobados, reserva=a.reserva, tope=a.tope)
            except stock_libro.LibroNoDisponible as exc:
                raise Abortar(2, f"EL LIBRO NO PASA EL PISO (LibroNoDisponible): "
                                 f"{sin_secretos(exc)}") from exc
    except Abortar as exc:
        print(str(exc), file=sys.stderr)
        return exc.codigo
    finally:
        if lector is not None:
            lector.cerrar()

    for linea in resumen(m, a.entorno):
        print(linea)
    if a.muestra:
        print(f"\nLos {a.muestra} cambios más grandes:")
        for linea in muestra(m, a.muestra):
            print(linea)
    ruta = a.salida or Path(tempfile.gettempdir()) / (
        f"alinear_seco_t0_{a.entorno}_{datetime.now(timezone.utc):%Y%m%d_%H%M}.csv")
    escribir_csv(m["filas"] + m["d2"], ruta)
    print(f"\nCSV: {ruta} ({len(m['filas']) + len(m['d2']):,} filas)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
