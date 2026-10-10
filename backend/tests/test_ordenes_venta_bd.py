"""Pruebas de INTEGRACIÓN de las órdenes de venta propias (services/ordenes_venta.py)
contra un Postgres LOCAL y DESECHABLE, con las migraciones 0064 y 0065 de Eduardo
aplicadas de verdad (dos veces cada una: la segunda vuelta ES la prueba de
idempotencia). Contrato: docs/MIGRACION_0064_0065_GUIA_AGENTE.md.

── POR QUÉ CONTRA UNA BASE DE VERDAD ───────────────────────────────────────────
La mitad de las reglas de este módulo YA NO viven en Python: las impone la base
(transiciones, inmutabilidad de lo confirmado, saldo = libro, apartado = Σ
reservado) y varias sólo truenan AL COMMIT (constraint triggers diferidos). Un
doble de la base no las tiene. Aquí cada función corre por su camino de
producción —`sdb.get_cursor()`, que CONFIRMA al salir— y después de CADA prueba
se revisan los invariantes de la base entera (ver `Base.tearDown`).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. El ciclo completo en ENSAYO: crear → guardar → confirmar (aparta) → entregar
     (el libro, `stock_mov` salida_ov), con sus mensajes del catálogo.
  2. Confirmar es TODO O NADA: si un SKU no alcanza no se aparta ninguno, el 409
     dice cuál, y queda el aviso `no_alcanzo` en el chat.
  3. La bodega va por renglón y sólo las de kubera con `admite_ov` (TEX3 apagada,
     REVISION y las de Odoo se rechazan con palabras).
  4. Entrega parcial y total; cancelar (suelta exacto; una confirmada la cancela
     cualquiera que escribe), cancelar con piezas ya afuera (entregada_cancelada
     + devolución pendiente), borrar (admin, motivo).
  5. Entregada y cancelada ya no cambian: 400/409 claros, nunca un 500.
  5b. EDITAR UNA CONFIRMADA (migración 0071, que el arranque aplica al final y dos
     veces): el encabezado; subir, bajar, quitar, agregar y cambiar de bodega con
     el apartado movido EXACTO por la diferencia; si no alcanza no cambia nada
     —ni la `rev` ni el chat— y dice cuánto falta; lo que ya salió no se toca;
     no puede quedar sin renglones por entregar; con el «¿salió?» pendiente, la
     bandera apagada o la base sin la 0071, no (y por la puerta se le pregunta
     al CATÁLOGO de la base, no al registro de migraciones; no haber podido
     preguntar no se dice como «falta»); el rastro `editada`, también del título
     y la imagen de un renglón; el reintento propio; dos ediciones a la vez, una
     edición contra la confirmación de OTRA orden por el mismo saldo, y el canal
     cancelando a media edición.
  6. `rev` vieja → 409. El alta es idempotente por clave, también a la vez.
  7. La liga con la venta (mp_*): todo o nada, formas, FULL rechazada, y la misma
     venta dos veces dice el folio de la que ya la tiene.
  8. El canal cancela (repetible, sin `rev` de quien llama): sin salir, en camino
     (la marca y el «¿salió?» sí/no), ya entregada; borrada o cancelada, nada. Y
     el tardío. Sobre una CONFIRMADA su guardia va por estado Y por la `rev` de
     su propia relectura (lo que suelta y lo que deja escrito salen de la misma
     foto), y no toca una orden que ya no es de la venta que el canal canceló.
  9. crear_auto, dentro de una transacción que termina en ROLLBACK (como el
     verificador): creada / ya_existia / no_alcanzo, con TODO su contenido.
 10. Permisos por rol, la bandera apagada (sólo borradores), tablas ausentes
     (lo dice, no truena), PDF sin bucket y con bucket (doble de Storage).
 11. CONCURRENCIA REAL: dos confirmaciones del mismo SKU con saldo para una (una
     gana, la otra `no_alcanzo`), doble clic, y la bodega ocupada (lock_timeout).
 12. Cada respuesta trae EXACTAMENTE las llaves de frontend/components/ordenes/tipos.ts.
 13. (al final del archivo) Lo que se apoya en el servicio, contra la misma base:
     el BARRIDO de cancelaciones del canal (services/ov_auto.py: sin salir, en
     camino → marcada, entregada → entregada_cancelada, borrador ligado, Temu
     por la bitácora, repetido, la cuenta, la bandera), el JOB del scheduler
     (la fila lo apaga y lo enciende sin reiniciar) y la API de punta a punta
     (routers/ordenes_venta.py: ciclo, «¿salió?», PDF con tipo y sin bucket,
     chat en vivo, modo prueba, sin migración, 404 y quién queda escrito).

── EL CANDADO ──────────────────────────────────────────────────────────────────
Sólo corre con OV_TEST_DSN, y sólo si apunta a 127.0.0.1 / localhost: si no, la
suite ABORTA al importar. El arranque TIRA Y RECREA la base del DSN (con una
conexión autocommit a `postgres`) —las tablas son de sólo agregar y sin
TRUNCATE: no hay otra forma de empezar limpio—, así que además exige que el
nombre empiece con `ov_` y que, si la base YA EXISTE, sea de esta suite: o es
`ov_b1` (la que tiene asignada), o lleva su marca en el comentario (la deja al
crearla), o quien corre dice que es suya con OV_TEST_RECREAR=1. Así no se lleva
por delante la base de otro (p. ej. una `ov_k` sembrada a mano) por un DSN mal
puesto. JAMÁS contra kubera ni contra el sandbox.
Datos: SKUs ZZPRUEBA-*, cuentas CUENTAPRUEBA, correos @prueba.test (repo público).

    cd backend && OV_TEST_DSN=postgresql://postgres@127.0.0.1:54341/ov_b1 \\
        python -m unittest tests.test_ordenes_venta_bd -v
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

import psycopg2
import psycopg2.extensions
import psycopg2.extras

DSN = os.environ.get("OV_TEST_DSN", "").strip()
_LOCALES = ("127.0.0.1", "localhost", "::1")
_MARCA = "desechable: la recrea tests.test_ordenes_venta_bd en cada corrida"
_BASE_PROPIA = "ov_b1"      # la base asignada a esta suite: se recrea sin preguntar


def _es_local(dsn: str) -> bool:
    """Sólo un host local EXPLÍCITO. Varios hosts, un `hostaddr` o ningún host no pasan."""
    try:
        p = psycopg2.extensions.parse_dsn(dsn)
    except Exception:  # noqa: BLE001
        return False
    return p.get("host") in _LOCALES and p.get("hostaddr", "127.0.0.1") in _LOCALES


if DSN and not _es_local(DSN):
    raise RuntimeError(
        "CANDADO: OV_TEST_DSN no apunta a 127.0.0.1 ni a localhost. Estas pruebas TIRAN y "
        "recrean la base; jamás se corren contra kubera ni contra el sandbox.")
if DSN:
    # ANTES de importar config: el pool del backend debe nacer apuntando aquí.
    os.environ["SUPABASE_DB_URL"] = DSN

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from config import settings  # noqa: E402
from services import ordenes_venta as ov  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

FIXTURE = BACKEND / "tests" / "ov_fixture.sql"
TIPOS = BACKEND.parent / "frontend" / "components" / "ordenes" / "tipos.ts"
MIGRACIONES = BACKEND.parent / "supabase" / "migrations"
M64 = MIGRACIONES / "0064_ops_ordenes_venta.sql"
M65 = MIGRACIONES / "0065_ops_inventario_kubera.sql"
# Fase 0 del reorden de esquemas (8-oct-2026): los esquemas vacíos `ventas` y
# `almacen`, y el retiro de ops.devoluciones_vs_canal_v. No tocan ops.ov_* ni el
# libro; van en la cadena para que la base de prueba sea la de producción.
M66 = MIGRACIONES / "0066_esquemas_ventas_almacen.sql"
M67 = MIGRACIONES / "0067_ops_quitar_devoluciones_vs_canal_v.sql"
M68 = MIGRACIONES / "0068_mudanza_ov_a_ventas_y_almacen.sql"
M71 = MIGRACIONES / "0071_ov_editar_confirmada.sql"


def _guardias(migracion: Path) -> dict[str, str]:
    """Las dos guardias de la orden —`ops.tg_ov_ordenes_guarda` y
    `ops.tg_ov_lineas_guarda`— tal como las crea esa migración: la 0068 SIN la
    puerta de la edición, la 0071 con ella. Sirve para quitar y reponer la puerta
    en una prueba sin correr la migración entera (que trae su propio COMMIT)."""
    texto = migracion.read_text(encoding="utf-8")
    bloques = {}
    for nombre in ("tg_ov_ordenes_guarda", "tg_ov_lineas_guarda"):
        m = re.search(r"CREATE OR REPLACE FUNCTION ops\." + nombre + r"\(\).*?\$function\$\s*;",
                      texto, flags=re.S)
        assert m, f"{migracion.name} ya no crea ops.{nombre}"
        bloques[nombre] = m.group(0)
    return bloques


ADMIN = ov.Quien("admin@prueba.test", "Ada Admin", "panel", "admin")
OPER = ov.Quien("oper@prueba.test", "Olga Operadora", "panel", "operador")
LECT = ov.Quien("lectura@prueba.test", "Leo Lectura", "panel", "lectura")
NADIE = ov.Quien("anonimo", "", "panel", "")
API = ov.Quien("servicio", "API", "api", "operador")

PDF = b"%PDF-1.4\n%comprobante de prueba\n" + b"0" * 2048
CUENTA = "CUENTAPRUEBA"


def L(sku: str, cantidad: int, precio: float = 10, almacen: str | None = "ENSAYO", **extra) -> dict:
    """Un renglón tal como se captura. Por omisión sale de ENSAYO."""
    return {"sku": sku, "cantidad": cantidad, "precio_unitario": precio, "almacen": almacen, **extra}


class StorageFalso:
    """El bucket en memoria: las mismas tres funciones que ov_storage, sin red."""

    def __init__(self) -> None:
        self.objetos: dict[str, bytes] = {}
        self.subidas = 0
        self.falla_subir = False
        self.falla_borrar = False

    def subir(self, ruta: str, datos: bytes) -> str:
        if self.falla_subir:
            raise ov.ov_storage.StorageError("HTTP 500 al subir (doble)")
        self.subidas += 1
        if ruta in self.objetos:
            return "existe"
        self.objetos[ruta] = bytes(datos)
        return "subido"

    def bajar(self, ruta: str) -> bytes:
        if ruta not in self.objetos:
            raise ov.ov_storage.StorageError(f"HTTP 404 al bajar {ruta} (doble)")
        return self.objetos[ruta]

    def borrar(self, ruta: str) -> None:
        if self.falla_borrar:
            raise ov.ov_storage.StorageError("HTTP 500 al borrar (doble)")
        self.objetos.pop(ruta, None)


_CN = None          # conexión directa de las pruebas (siembra y comprobaciones), autocommit
_PARCHE_DSN = None


def _recrear_base() -> None:
    """DROP + CREATE de la base del DSN, con una conexión autocommit a `postgres`.
    Dos candados más, además del host local: el nombre empieza con `ov_`, y una
    base que YA existe sólo se tira si es de esta suite (es `ov_b1`, lleva su
    marca, o lo dice OV_TEST_RECREAR=1): no vaya a ser la de otro."""
    p = psycopg2.extensions.parse_dsn(DSN)
    nombre = p.get("dbname") or ""
    if not re.fullmatch(r"ov_[a-z0-9_]{1,40}", nombre):
        raise RuntimeError(f"CANDADO: la base de pruebas se llama «{nombre}»; tiene que empezar "
                           "con ov_ (minúsculas, dígitos y guion bajo).")
    admin = psycopg2.connect(**{**p, "dbname": "postgres"})
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("select shobj_description(oid, 'pg_database') from pg_database "
                        "where datname = %s", (nombre,))
            fila = cur.fetchone()
            ajena = (fila is not None and fila[0] != _MARCA and nombre != _BASE_PROPIA
                     and os.environ.get("OV_TEST_RECREAR", "").strip() != "1")
            if ajena:
                raise RuntimeError(
                    f"CANDADO: la base «{nombre}» ya existe y no la creó esta suite (no lleva su "
                    "marca). Usa otro nombre en OV_TEST_DSN; o, si de verdad es tuya y se puede "
                    "tirar, corre con OV_TEST_RECREAR=1.")
            cur.execute(f'drop database if exists "{nombre}" with (force)')
            cur.execute(f'create database "{nombre}"')
            cur.execute(f'comment on database "{nombre}" is %s', (_MARCA,))
    finally:
        admin.close()


def setUpModule() -> None:
    """Una vez por corrida: la base se tira y se recrea (fixture → 0064 → 0065 →
    0066 → 0067, y otra vez las cuatro: idempotencia; después la 0068 y la 0071,
    cada una dos veces), y el pool del backend apunta a ella."""
    global _CN, _PARCHE_DSN
    if not DSN:
        return
    assert _es_local(DSN), "candado: el DSN de pruebas no es local"
    _recrear_base()
    _CN = psycopg2.connect(DSN)
    _CN.autocommit = True
    with _CN.cursor() as cur:
        # La cadena de producción: 0066 y 0067 después de la 0065, y la 0068 al final.
        # Todo dos veces (idempotencia), pero la 0068 va AL FINAL de las dos vueltas:
        # después de la mudanza la 0064 y la 0065 ya no se pueden repetir (en `ops`
        # quedan vistas con esos nombres, no tablas). El runner nunca las repite.
        # Y la 0071 (editar una confirmada) después de la 0068, también dos veces:
        # reescribe las dos guardias que dejó la 0068 y el CHECK del catálogo de
        # eventos (re-correr la 0068 DESPUÉS de la 0071 quitaría la puerta).
        for archivo in (FIXTURE, M64, M65, M66, M67, M64, M65, M66, M67, M68, M68, M71, M71):
            cur.execute(archivo.read_text(encoding="utf-8"))
        cur.execute("select migracion, count(*) from ops.migraciones group by 1 order by 1")
        assert cur.fetchall() == [("0064_ops_ordenes_venta", 2), ("0065_ops_inventario_kubera", 2),
                                  ("0066_esquemas_ventas_almacen", 2),
                                  ("0067_ops_quitar_devoluciones_vs_canal_v", 2),
                                  ("0068_mudanza_ov_a_ventas_y_almacen", 2),
                                  ("0071_ov_editar_confirmada", 2)]
        # La 0065 re-aplicada vuelve a crear la vista; la 0067 que va después la quita.
        cur.execute("select to_regclass('ops.devoluciones_vs_canal_v') is null")
        assert cur.fetchone() == (True,), "la 0067 no quitó ops.devoluciones_vs_canal_v"
    _PARCHE_DSN = mock.patch.object(settings, "supabase_db_url", DSN)
    _PARCHE_DSN.start()
    sdb._reiniciar_pool()               # el pool pudo nacer antes, apuntando a otro lado


def tearDownModule() -> None:
    global _CN, _PARCHE_DSN
    if _CN is not None:
        _CN.close()
        _CN = None
    if _PARCHE_DSN is not None:
        sdb._reiniciar_pool()
        _PARCHE_DSN.stop()
        _PARCHE_DSN = None


@unittest.skipUnless(DSN, "sin OV_TEST_DSN: las pruebas de integración necesitan el Postgres local")
class Base(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.assertTrue(_es_local(settings.supabase_db_url), "el pool NO apunta al Postgres local")
        self.t = uuid.uuid4().hex[:8].upper()       # cada prueba, sus SKUs y sus claves
        self.vigia_esperada: set[tuple[str, str]] = set()
        self.sql("delete from storage.buckets")     # sin bucket, como nace
        self.bandera(ov.BANDERA_MODULO, True)
        self.sql("delete from ops.automatizacion_flags where flag = %s", (ov.BANDERA_AUTO,))
        ov._olvidar_cache()
        ov._reiniciar_caida()
        self.storage = StorageFalso()
        for nombre in ("subir", "bajar", "borrar"):
            p = mock.patch.object(ov.ov_storage, nombre, getattr(self.storage, nombre))
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        # LOS INVARIANTES DE LA BASE, después de cada prueba y sobre TODO lo que hay
        # (cada prueba deja su historia: el libro y el chat son de sólo agregar).
        # 1) La vigía sólo trae filas cuando algo no cuadra.
        vigia = self.sql("select problema, sku::text as sku, almacen, ref, esperado, encontrado "
                         "from ops.stock_apartado_descuadre_v")
        self.assertEqual([f for f in vigia if (f["problema"], f["sku"]) not in self.vigia_esperada],
                         [], "la vista vigía reporta un descuadre")
        # (ops.devoluciones_vs_canal_v ya no existe: la quita la 0067.)
        # 2) Las tres verificaciones de la base, por cada orden y cada saldo.
        self.sql("select ops.verificar_ov(id) from ventas.ov_ordenes")
        self.sql("select ops.verificar_libro(sku, almacen), ops.verificar_apartado(sku, almacen) "
                 "from almacen.stock_almacen")
        # 3) El catálogo de bodegas no se toca desde el código (ni desde una prueba).
        self.assertEqual(self.sql("select count(*) as n from almacen.almacenes_hist")[0]["n"], 6)
        # 4) Nadie dejó una transacción abierta (un candado colgado tumba el pool).
        self.assertEqual(self.sql("select count(*) as n from pg_stat_activity "
                                  "where datname = current_database() "
                                  "and state like 'idle in transaction%%'")[0]["n"], 0)

    # ── ayudantes ────────────────────────────────────────────────────────────
    def sql(self, texto: str, params=None) -> list[dict]:
        with _CN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(texto, params)
            return [dict(f) for f in cur.fetchall()] if cur.description else []

    @contextmanager
    def conexion(self):
        """Una conexión PROPIA (no autocommit) que termina en ROLLBACK."""
        cn = psycopg2.connect(DSN, cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cn
        finally:
            cn.rollback()
            cn.close()

    def sku(self, sufijo: str) -> str:
        return f"ZZPRUEBA-{self.t}-{sufijo}"

    def clave(self, sufijo: str = "") -> str:
        return f"panel-{self.t}-{sufijo}-{uuid.uuid4().hex[:8]}"

    def bandera(self, nombre: str, valor: bool) -> None:
        self.sql("""insert into ops.automatizacion_flags (flag, valor, motivo, actualizado_por)
                    values (%s, %s, 'Prueba de integración', 'pruebas@prueba.test')
                    on conflict (flag) do update set valor = excluded.valor""", (nombre, valor))
        ov._olvidar_cache()

    _SIEMBRA = """
        with s as (
          insert into almacen.stock_almacen as sa (sku, almacen, fisico)
          values (%(sku)s, %(alm)s, %(n)s)
          on conflict (sku, almacen) do update set fisico = sa.fisico + excluded.fisico
          returning sa.sku, sa.almacen, sa.fisico
        ), m as (
          insert into almacen.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave, nota,
                                     quien, via)
          select s.sku, s.almacen, %(n)s, s.fisico, 'correccion', %(clave)s,
                 'Siembra de la prueba', 'pruebas@prueba.test', 'automatico'
            from s
          returning id
        )
        select ops.exigir((select count(*) from m) = 1, 'siembra_no_cuadra') as cuadra"""

    def siembra(self, sku: str, n: int, almacen: str = "ENSAYO", cur=None) -> None:
        """Saldo de prueba, en UNA sentencia: el saldo y su movimiento `correccion`
        (clave corr:<uuid>, nota ≥ 5), como el verificador. Sin `inventario_libro`
        no hay otra forma de meter stock a ENSAYO."""
        params = {"sku": sku, "alm": almacen, "n": n, "clave": f"corr:{uuid.uuid4()}"}
        if cur is not None:
            cur.execute(self._SIEMBRA, params)
        else:
            self.sql(self._SIEMBRA, params)

    def saldo(self, sku: str, almacen: str = "ENSAYO") -> tuple | None:
        f = self.sql("select fisico, apartado, libre from almacen.stock_almacen "
                     "where sku = %s and almacen = %s", (sku, almacen))
        return (f[0]["fisico"], f[0]["apartado"], f[0]["libre"]) if f else None

    def libro(self, sku: str, almacen: str = "ENSAYO") -> list[tuple]:
        return [(f["motivo"], f["delta"], f["saldo_despues"]) for f in self.sql(
            "select motivo, delta, saldo_despues from almacen.stock_mov "
            "where sku = %s and almacen = %s order by id", (sku, almacen))]

    def crear(self, lineas, quien=OPER, clave=None, **enc) -> dict:
        r = ov.crear_borrador({**enc, "lineas": lineas}, quien, clave or self.clave())
        self.assertTrue(r["ok"])
        return r["orden"]

    def confirmada(self, lineas, quien=OPER, **enc) -> dict:
        """Siembra lo justo (si no hay saldo) y deja la orden confirmada."""
        for l in lineas:
            if l.get("almacen") and self.saldo(l["sku"], l["almacen"]) is None:
                self.siembra(l["sku"], l["cantidad"], l["almacen"])
        o = self.crear(lineas, quien, **enc)
        return ov.confirmar(o["id"], o["rev"], quien)["orden"]

    def eventos(self, orden_id: int) -> list[str | None]:
        return [m["evento"] for m in ov.mensajes(orden_id)["mensajes"]]

    def folio_contador(self) -> int:
        return self.sql("select ultimo from ventas.ov_folio")[0]["ultimo"]

    def venta_canal(self, orden: str, items: list[tuple], canal: str = "tiktok",
                    cuenta: str = CUENTA, **cols) -> None:
        """Una venta en channel.orders. items: [(sku, cantidad, precio, es_fulfillment)]."""
        self.sql("""insert into channel.orders (external_order_id, canal, cuenta, estado_canal,
                           estado_wc, total, comision, creado_at)
                    values (%s, %s, %s, %s, %s, %s, %s, now())""",
                 (orden, canal, cuenta, cols.get("estado_canal"), cols.get("estado_wc", "processing"),
                  cols.get("total", 100), cols.get("comision", 10)))
        for n, (sku, cantidad, precio, full) in enumerate(items, 1):
            self.sql("""insert into channel.order_items (canal, cuenta, external_order_id, linea,
                               sku, titulo, cantidad, precio_unitario, es_fulfillment)
                        values (%s, %s, %s, %s, %s, 'Artículo de prueba', %s, %s, %s)""",
                     (canal, cuenta, orden, n, sku, cantidad, precio, full))

    def a_la_vez(self, *fns) -> list:
        """Corre las funciones en hilos que arrancan JUNTOS. Devuelve, en orden, el
        resultado o la excepción de cada una."""
        barrera = threading.Barrier(len(fns))
        salida: list = [None] * len(fns)

        def correr(i: int, fn) -> None:
            try:
                barrera.wait(10)
                salida[i] = fn()
            except Exception as exc:  # noqa: BLE001 — la prueba decide qué es un error
                salida[i] = exc

        hilos = [threading.Thread(target=correr, args=(i, fn)) for i, fn in enumerate(fns)]
        [h.start() for h in hilos]
        [h.join(60) for h in hilos]
        return salida

    @contextmanager
    def rechazos_del_canal(self):
        """Lo que la base le rechazó a `canal_cancelo` mientras dura el bloque, en
        orden: `[(clase, detalle)]`. No sustituye nada: es la `_transicion` de
        verdad, con un apuntador. Sirve para fijar POR DÓNDE salió una carrera."""
        real, vistos = ov._transicion, []

        def apunta(operacion, sql, params, cur=None):
            try:
                return real(operacion, sql, params, cur)
            except ov._Rechazo as r:
                if operacion == "canal_cancelo":
                    vistos.append((r.clase, r.detalle))
                raise

        with mock.patch.object(ov, "_transicion", apunta):
            yield vistos


# ══════════════════════════════════════════════════════════════════════════════
class Ciclo(Base):
    def test_crear_guardar_confirmar_y_entregar_en_ensayo(self):
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 10)
        self.siembra(b, 4)
        self.sql("insert into core.products (sku, name) values (%s, 'Audífonos de prueba')", (a,))
        antes = self.folio_contador()
        o = self.crear([L(a, 4, 100, titulo="Audífonos"), L(b, 3, 50.5, almacen=None)],
                       cliente=" directa ", canal="DIRECTA", comision=51.5, descripcion="  ")
        self.assertEqual((o["folio"], o["estado"], o["tipo"], o["rev"]),
                         (f"OV-{antes + 1:05d}", "borrador", "venta", 1))
        self.assertEqual((o["cliente"], o["canal"], o["descripcion"]), ("directa", "directa", None))
        self.assertEqual((o["total"], o["comision"], o["neto"]), (551.5, 51.5, 500.0))
        self.assertIsInstance(o["total"], float)
        self.assertEqual((o["moneda"], o["precio_origen"]), ("MXN", "manual"))
        self.assertEqual((o["renglones"], o["piezas"], o["piezas_apartadas"], o["piezas_entregadas"],
                          o["renglones_entregados"]), (2, 7, 0, 0, 0))
        self.assertEqual((o["skus"], o["bodegas"]), ([a, b], ["ENSAYO"]))
        self.assertEqual((o["n_mensajes"], o["n_archivos"]), (1, 0))
        self.assertEqual((o["creado_por"], o["creado_nombre"], o["creado_via"]),
                         ("oper@prueba.test", "Olga Operadora", "panel"))
        self.assertIsNotNone(datetime.fromisoformat(o["creado_at"]).tzinfo, "fecha ISO con zona")
        la, lb = o["lineas"]
        self.assertEqual((la["sku"], la["titulo"], la["cantidad"], la["precio_unitario"],
                          la["importe"], la["almacen"]), (a, "Audífonos", 4, 100.0, 400.0, "ENSAYO"))
        self.assertEqual((la["reservado"], la["entregado"], la["fisico"], la["apartado"],
                          la["libre"], la["conocido"]), (0, None, 10, 0, 10, True))
        # Sin bodega no se sabe el saldo: null, NO cero. Y un SKU fuera del catálogo se dice.
        self.assertEqual((lb["almacen"], lb["fisico"], lb["apartado"], lb["libre"], lb["conocido"]),
                         (None, None, None, None, False))
        p = o["permisos"]
        self.assertTrue(p["editar"] and p["confirmar"] and p["cancelar"] and p["mensajes"])
        self.assertFalse(p["borrar"] or p["entregar"] or p["subir_archivo"])
        self.assertIn("administrador", p["porque"]["borrar"])

        # Guardar: le pone bodega al segundo renglón y sube la cantidad del primero.
        g = ov.guardar(o["id"], 1, {"lineas": [L(a, 5, 100, titulo="Audífonos"), L(b, 3, 50.5)],
                                    "total": None, "descripcion": "Pedido de mostrador"}, OPER)
        self.assertEqual(g["mensaje"], "Cambios guardados.")
        o = g["orden"]
        self.assertEqual((o["rev"], o["estado"], o["total"], o["descripcion"], o["piezas"]),
                         (2, "borrador", 651.5, "Pedido de mostrador", 8))
        self.assertEqual([(l["linea"], l["sku"], l["cantidad"], l["almacen"]) for l in o["lineas"]],
                         [(1, a, 5, "ENSAYO"), (2, b, 3, "ENSAYO")])
        self.assertEqual(o["lineas"][0]["id"], la["id"], "el renglón que sigue conserva su id")
        self.assertEqual((self.saldo(a), self.saldo(b)), ((10, 0, 10), (4, 0, 4)),
                         "un borrador no aparta nada")

        c = ov.confirmar(o["id"], 2, OPER)
        self.assertEqual(c["mensaje"], "Confirmada: 2 renglones apartados (8 piezas).")
        o = c["orden"]
        self.assertEqual((o["estado"], o["rev"], o["piezas_apartadas"]), ("confirmada", 3, 8))
        self.assertEqual((o["confirmada_por"], o["confirmada_nombre"]),
                         ("oper@prueba.test", "Olga Operadora"))
        self.assertIsNotNone(o["confirmada_at"])
        self.assertEqual([(l["reservado"], l["fisico"], l["apartado"], l["libre"]) for l in o["lineas"]],
                         [(5, 10, 5, 5), (3, 4, 3, 1)])
        self.assertEqual((self.saldo(a), self.saldo(b)), ((10, 5, 5), (4, 3, 1)))
        p = o["permisos"]
        # Confirmada, quien escribe la entrega, la EDITA y la CANCELA (0071): ya no
        # hace falta un administrador. Lo que no vuelve es confirmarla.
        self.assertTrue(p["entregar"] and p["editar"] and p["cancelar"])
        self.assertFalse(p["confirmar"] or p["borrar"])
        self.assertEqual(p["porque"]["confirmar"], "Sólo un borrador se puede confirmar")

        e = ov.entregar(o["id"], 3, OPER)
        self.assertEqual(e["mensaje"], "Entregada a la paquetería.")
        o = e["orden"]
        self.assertEqual((o["estado"], o["rev"], o["piezas_apartadas"], o["piezas_entregadas"],
                          o["renglones_entregados"]), ("entregada", 4, 0, 8, 2))
        self.assertEqual((o["entregada_por"], o["entregada_nombre"]),
                         ("oper@prueba.test", "Olga Operadora"))
        # Entregada, ya no se edita ni se cancela a mano, y dice por qué.
        self.assertFalse(o["permisos"]["editar"] or o["permisos"]["cancelar"])
        self.assertIn("su contenido no cambia", o["permisos"]["porque"]["editar"])
        for l in o["lineas"]:
            self.assertEqual((l["reservado"], l["entregado"], l["entregado_por"]),
                             (0, l["cantidad"], "oper@prueba.test"))
            self.assertIsNotNone(l["entregado_at"])
        self.assertEqual((self.saldo(a), self.saldo(b)), ((5, 0, 5), (1, 0, 1)))
        # EL LIBRO: una salida_ov por renglón, con la clave del hecho y el folio de ref.
        salidas = self.sql("""select sku::text as sku, delta, saldo_despues, clave, ref, ov_linea_id,
                                     quien, quien_nombre, via
                                from almacen.stock_mov where motivo = 'salida_ov' and ref = %s
                               order by sku""", (o["folio"],))
        ids = {l["sku"]: l["id"] for l in o["lineas"]}
        self.assertEqual(salidas, [
            {"sku": a, "delta": -5, "saldo_despues": 5, "ref": o["folio"], "ov_linea_id": ids[a],
             "clave": f"ov:{o['id']}:linea:{ids[a]}:salida", "quien": "oper@prueba.test",
             "quien_nombre": "Olga Operadora", "via": "panel"},
            {"sku": b, "delta": -3, "saldo_despues": 1, "ref": o["folio"], "ov_linea_id": ids[b],
             "clave": f"ov:{o['id']}:linea:{ids[b]}:salida", "quien": "oper@prueba.test",
             "quien_nombre": "Olga Operadora", "via": "panel"}])
        self.assertEqual(self.libro(a), [("correccion", 10, 10), ("salida_ov", -5, 5)])

        chat = ov.mensajes(o["id"])
        self.assertEqual([m["evento"] for m in chat["mensajes"]],
                         ["creada", "borrador_guardado", "confirmada", "entregada"])
        self.assertEqual((chat["total"], chat["rev"], chat["estado"]), (4, 4, "entregada"))
        self.assertEqual(chat["ultimo_id"], chat["mensajes"][-1]["id"])
        for m in chat["mensajes"]:
            self.assertIn(m["evento"], ov.EVENTOS, "sólo eventos del catálogo de la 0064")
            self.assertEqual((m["tipo"], m["autor"], m["autor_nombre"], m["via"]),
                             ("sistema", "oper@prueba.test", "Olga Operadora", "panel"))
            self.assertNotIn("op", m["datos"] or {}, "la marca de idempotencia no sale por la API")
        guardada, conf, entr = chat["mensajes"][1:]
        self.assertEqual(guardada["datos"]["cambios"]["descripcion"], [None, "Pedido de mostrador"])
        self.assertEqual(guardada["datos"]["renglones"]["cambiados"],
                         [{"sku": a, "cantidad": [4, 5]}])
        self.assertEqual([(x["sku"], x["cantidad"], x["reservado"], x["almacen"])
                          for x in conf["datos"]["lineas"]],
                         [(a, 5, 5, "ENSAYO"), (b, 3, 3, "ENSAYO")])
        self.assertIn("DELIVERED", entr["cuerpo"])
        self.assertEqual([(x["sku"], x["entregado"]) for x in entr["datos"]["lineas"]],
                         [(a, 5), (b, 3)])

    def test_obtener_por_id_y_por_folio_sin_distinguir_mayusculas(self):
        o = self.crear([L(self.sku("A"), 1)])
        self.assertEqual(ov.obtener(o["id"], LECT)["folio"], o["folio"])
        self.assertEqual(ov.obtener(str(o["id"]), LECT)["id"], o["id"])
        self.assertEqual(ov.obtener(o["folio"].lower(), LECT)["id"], o["id"])
        self.assertFalse(ov.obtener(o["folio"], LECT)["permisos"]["cancelar"])
        for ref in ("OV-99999999", 99999999, "otra-cosa", "", True):
            with self.assertRaises(ov.NoExiste):
                ov.obtener(ref, ADMIN)

    def test_una_orden_puede_nacer_sin_renglones_pero_no_confirmarse(self):
        o = self.crear([])
        self.assertEqual((o["renglones"], o["piezas"], o["total"], o["lineas"], o["skus"],
                          o["bodegas"]), (0, 0, 0.0, [], [], []))
        self.assertFalse(o["permisos"]["confirmar"])
        self.assertEqual(o["permisos"]["porque"]["confirmar"], "La orden no tiene renglones")
        with self.assertRaises(ov.Invalido):
            ov.confirmar(o["id"], 1, OPER)

    def test_el_tipo_full_no_se_crea_desde_aqui(self):
        antes = self.folio_contador()
        with self.assertRaises(ov.Invalido) as e:
            ov.crear_borrador({"tipo": "full", "lineas": []}, OPER, self.clave())
        self.assertIn("Crear FULL", str(e.exception))
        with self.assertRaises(ov.Invalido):
            ov.crear_borrador({"tipo": "regalo", "lineas": []}, OPER, self.clave())
        self.assertEqual(self.folio_contador(), antes)

    def test_la_llave_de_api_queda_escrita_como_servicio_y_via_api(self):
        o = self.crear([L(self.sku("A"), 1)], quien=API)
        self.assertEqual((o["creado_por"], o["creado_nombre"], o["creado_via"]),
                         ("servicio", "API", "api"))
        m = ov.mensajes(o["id"])["mensajes"][0]
        self.assertEqual((m["autor"], m["via"]), ("servicio", "api"))
        # Una vía fuera del catálogo NO se disfraza de «panel»: se rechaza.
        raro = ov.Quien("script@prueba.test", "Script", "script", "operador")
        with self.assertRaises(ov.Invalido):
            ov.crear_borrador({"lineas": []}, raro, self.clave())


class TodoONada(Base):
    def test_si_un_sku_no_alcanza_no_se_aparta_ninguno_y_dice_cual(self):
        a, b, c = self.sku("A"), self.sku("B"), self.sku("C")
        self.siembra(a, 5)
        self.siembra(b, 1)
        o = self.crear([L(a, 2), L(b, 3), L(c, 4)])       # `c` ni siquiera tiene fila de saldo
        with self.assertRaises(ov.Conflicto) as e:
            ov.confirmar(o["id"], o["rev"], OPER)
        texto = str(e.exception)
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(texto, f"No alcanzó el stock para apartar: {b} pide 3 y hay 1 libre en "
                                f"ENSAYO; {c} pide 4 y no tiene existencias registradas en ENSAYO. "
                                "No se apartó nada.")
        self.assertNotIn("no_alcanzo", texto, "nunca el nombre técnico")
        # NADA quedó apartado, ni del SKU que sí alcanzaba, y la orden sigue en borrador.
        self.assertEqual((self.saldo(a), self.saldo(b), self.saldo(c)), ((5, 0, 5), (1, 0, 1), None))
        tras = ov.obtener(o["id"], OPER)
        self.assertEqual((tras["estado"], tras["rev"], tras["piezas_apartadas"]),
                         ("borrador", o["rev"], 0))
        # …y el aviso quedó en el chat (en OTRA transacción: el KB001 deshizo la de confirmar).
        chat = ov.mensajes(o["id"])["mensajes"]
        self.assertEqual([m["evento"] for m in chat], ["creada", "no_alcanzo"])
        self.assertEqual((chat[1]["cuerpo"], chat[1]["tipo"], chat[1]["autor"]),
                         (texto, "sistema", "oper@prueba.test"))
        self.assertEqual([(x["sku"], x["cantidad"], x["libre"]) for x in chat[1]["datos"]["lineas"]],
                         [(b, 3, 1), (c, 4, None)])
        # Llega el stock: ahora sí, con la MISMA rev (el aviso no la movió).
        self.siembra(b, 2)
        self.siembra(c, 4)
        ok = ov.confirmar(o["id"], o["rev"], OPER)["orden"]
        self.assertEqual((ok["estado"], ok["piezas_apartadas"]), ("confirmada", 9))
        self.assertEqual((self.saldo(a), self.saldo(b), self.saldo(c)),
                         ((5, 2, 3), (3, 3, 0), (4, 4, 0)))

    def test_el_mismo_sku_en_dos_ordenes_no_se_aparta_dos_veces(self):
        a = self.sku("A")
        self.siembra(a, 5)
        o1 = self.confirmada([L(a, 4)])
        o2 = self.crear([L(a, 2)])
        with self.assertRaises(ov.Conflicto) as e:
            ov.confirmar(o2["id"], o2["rev"], OPER)
        self.assertIn(f"{a} pide 2 y hay 1 libre en ENSAYO", str(e.exception))
        self.assertEqual(self.saldo(a), (5, 4, 1))
        ov.cancelar(o1["id"], o1["rev"], ADMIN, "Ya no se va a surtir")
        self.assertEqual(ov.confirmar(o2["id"], o2["rev"], OPER)["orden"]["estado"], "confirmada")
        self.assertEqual(self.saldo(a), (5, 2, 3))


class Bodegas(Base):
    def test_solo_bodegas_de_kubera_que_admiten_ov(self):
        a = self.sku("A")
        antes = self.folio_contador()
        for bodega, dice in (("TEX3", "no admite órdenes de venta"),
                             ("REVISION", "no admite órdenes de venta"),
                             ("TEXCO", "bodega de Odoo"), ("TEX2", "bodega de Odoo"),
                             ("NOEXISTE", "no existe la bodega")):
            with self.assertRaises(ov.Invalido, msg=bodega) as e:
                ov.crear_borrador({"lineas": [L(a, 1, almacen=bodega)]}, OPER, self.clave())
            self.assertIn(dice, str(e.exception))
            self.assertEqual(e.exception.status, 400)
        self.assertEqual(self.folio_contador(), antes, "un alta rechazada no consume folio")
        o = self.crear([L(a, 1, almacen="ensayo")])
        self.assertEqual(o["lineas"][0]["almacen"], "ENSAYO", "el código va en mayúsculas")
        with self.assertRaises(ov.Invalido):
            ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 1, almacen="TEX3")]}, OPER)
        self.assertEqual(ov.obtener(o["id"], OPER)["rev"], o["rev"])

    def test_un_renglon_sin_bodega_no_se_confirma(self):
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 3)
        o = self.crear([L(a, 1), L(b, 1, almacen=None)])
        with self.assertRaises(ov.Invalido) as e:
            ov.confirmar(o["id"], o["rev"], OPER)
        self.assertIn(f"El renglón de {b} no tiene bodega", str(e.exception))
        self.assertEqual(self.saldo(a), (3, 0, 3), "no se tocó la base")
        # El plan (de un proceso) tampoco puede mandar a una bodega que no admite OV…
        plan = [{"id": l["id"], "almacen": "TEX3"} for l in o["lineas"]]
        with self.assertRaises(ov.Invalido):
            ov.confirmar(o["id"], o["rev"], OPER, plan)
        # …y sí puede poner la bodega que al renglón le falta.
        self.siembra(b, 1)
        plan = [{"id": l["id"], "almacen": "ENSAYO"} for l in o["lineas"]]
        c = ov.confirmar(o["id"], o["rev"], OPER, plan)["orden"]
        self.assertEqual((c["estado"], [l["almacen"] for l in c["lineas"]]),
                         ("confirmada", ["ENSAYO", "ENSAYO"]))

    def test_si_python_no_lo_frena_la_base_si_y_sale_con_palabras(self):
        """La red de abajo: con la validación de Python apagada a propósito, la FK
        compuesta (bodega de Odoo) y la guarda dentro del candado (bodega sin
        admite_ov) rechazan, y lo que sale es un 400 en español, no un 500."""
        a = self.sku("A")
        with mock.patch.object(ov, "_porque_no_bodega", return_value=None):
            with self.assertRaises(ov.Invalido) as e:
                ov.crear_borrador({"lineas": [L(a, 1, almacen="TEXCO")]}, OPER, self.clave())
            self.assertIn("no es de kubera", str(e.exception))
            o = self.crear([L(a, 1, almacen="REVISION")])    # la FK sí la deja pasar en borrador
            with self.assertRaises(ov.Invalido) as e:
                ov.confirmar(o["id"], o["rev"], OPER)        # KB001 renglon_sin_plan_o_sin_saldo
        self.assertEqual(str(e.exception),
                         f"{a}: la bodega REVISION no admite órdenes de venta.")
        self.assertEqual(ov.obtener(o["id"], OPER)["estado"], "borrador")


class Guardar(Base):
    def test_agrupa_por_sku_y_bodega_renumera_y_no_escribe_si_nada_cambio(self):
        a, b = self.sku("A"), self.sku("B")
        o = self.crear([L(a, 1, 10), L(b, 2, 20)])
        # El mismo SKU en la misma bodega se SUMA; con otro precio se rechaza.
        g = ov.guardar(o["id"], 1, {"lineas": [L(b, 2, 20), L(a, 1, 10), L(a.lower(), 4, 10)]},
                       OPER)["orden"]
        self.assertEqual([(l["linea"], l["sku"], l["cantidad"]) for l in g["lineas"]],
                         [(1, b, 2), (2, a, 5)])
        self.assertEqual(g["rev"], 2)
        with self.assertRaises(ov.Invalido) as e:
            ov.guardar(o["id"], 2, {"lineas": [L(a, 1, 10), L(a, 1, 11)]}, OPER)
        self.assertIn("precios distintos", str(e.exception))
        # Lo mismo otra vez: «Sin cambios», y ni rev ni mensaje nuevo.
        igual = ov.guardar(o["id"], 2, {"lineas": [L(b, 2, 20), L(a, 5, 10)],
                                        "moneda": "MXN"}, OPER)
        self.assertEqual((igual["mensaje"], igual["orden"]["rev"], igual["orden"]["n_mensajes"]),
                         ("Sin cambios.", 2, 2))
        # Sólo el encabezado: los renglones ni se tocan.
        enc = ov.guardar(o["id"], 2, {"guia": "GUIA-PRUEBA-1", "paqueteria": "Paquetería de prueba",
                                      "fecha_venta": "2026-10-06T10:00:00-06:00"}, OPER)["orden"]
        self.assertEqual((enc["guia"], enc["paqueteria"], enc["rev"], enc["renglones"]),
                         ("GUIA-PRUEBA-1", "Paquetería de prueba", 3, 2))
        self.assertEqual(datetime.fromisoformat(enc["fecha_venta"]),
                         datetime.fromisoformat("2026-10-06T10:00:00-06:00"))
        self.assertEqual([l["id"] for l in enc["lineas"]], [l["id"] for l in g["lineas"]])
        # Quitar todos los renglones también es guardar.
        vacia = ov.guardar(o["id"], 3, {"lineas": [], "total": None}, OPER)["orden"]
        self.assertEqual((vacia["renglones"], vacia["total"], vacia["rev"]), (0, 0.0, 4))
        self.assertEqual(self.eventos(o["id"]), ["creada"] + ["borrador_guardado"] * 3)

    def test_el_mismo_sku_puede_salir_de_dos_bodegas(self):
        """`(orden, sku, bodega)` es la llave: dos bodegas, dos renglones. Para que
        ENSAYO no sea la única, la prueba lo demuestra con un renglón SIN bodega."""
        a = self.sku("A")
        o = self.crear([L(a, 1, 10), L(a, 2, 10, almacen=None)])
        self.assertEqual([(l["sku"], l["almacen"], l["cantidad"]) for l in o["lineas"]],
                         [(a, "ENSAYO", 1), (a, None, 2)])
        self.assertEqual((o["skus"], o["bodegas"], o["piezas"]), ([a], ["ENSAYO"], 3))


class Entrega(Base):
    def test_entrega_parcial_y_luego_total(self):
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 10)
        self.siembra(b, 10)
        o = self.confirmada([L(a, 3), L(b, 2)])
        la, lb = o["lineas"]
        p = ov.entregar(o["id"], o["rev"], OPER, [{"id": la["id"], "n": 3}])
        self.assertEqual(p["mensaje"], "Entrega parcial registrada: queda 1 renglón por salir.")
        o = p["orden"]
        self.assertEqual((o["estado"], o["renglones_entregados"], o["piezas_entregadas"],
                          o["piezas_apartadas"], o["entregada_at"]), ("confirmada", 1, 3, 2, None))
        self.assertEqual((self.saldo(a), self.saldo(b)), ((7, 0, 7), (10, 2, 8)))
        self.assertTrue(o["permisos"]["entregar"], "sigue confirmada: falta un renglón")
        # Un renglón se entrega UNA vez; y sólo los de esta orden, con piezas de 0 a cantidad.
        for malas in ([{"id": la["id"], "n": 1}], [{"id": 99999999, "n": 1}],
                      [{"id": lb["id"], "n": 3}], [{"id": lb["id"], "n": -1}],
                      [{"id": lb["id"], "n": 1}, {"id": lb["id"], "n": 1}], [], "todo"):
            with self.assertRaises(ov.Invalido, msg=str(malas)):
                ov.entregar(o["id"], o["rev"], OPER, malas)
        # Sale MENOS de lo pedido: lo que no salió se suelta, y con eso ya no queda nada.
        t = ov.entregar(o["id"], o["rev"], OPER, [{"id": lb["id"], "n": 1}])
        self.assertEqual(t["mensaje"], "Entregada a la paquetería.")
        o = t["orden"]
        self.assertEqual((o["estado"], o["piezas_entregadas"], o["piezas_apartadas"]),
                         ("entregada", 4, 0))
        self.assertEqual(self.saldo(b), (9, 0, 9))
        self.assertEqual(self.libro(b), [("correccion", 10, 10), ("salida_ov", -1, 9)])
        self.assertEqual(self.eventos(o["id"])[-2:], ["entregada_parcial", "entregada"])
        with self.assertRaises(ov.Invalido):
            ov.entregar(o["id"], o["rev"], OPER)             # ya está entregada

    def test_un_renglon_del_que_no_salio_nada_se_suelta_sin_movimiento(self):
        a, b = self.sku("A"), self.sku("B")
        o = self.confirmada([L(a, 2), L(b, 2)])
        la, lb = o["lineas"]
        o = ov.entregar(o["id"], o["rev"], OPER, [{"id": la["id"], "n": 0}])["orden"]
        self.assertEqual((o["estado"], o["piezas_entregadas"], o["piezas_apartadas"]),
                         ("confirmada", 0, 2))
        self.assertEqual(self.saldo(a), (2, 0, 2))
        self.assertEqual(self.libro(a), [("correccion", 2, 2)], "n = 0 no escribe salida")
        # …pero una orden no queda «entregada» sin que haya salido una sola pieza.
        with self.assertRaises(ov.Invalido) as e:
            ov.entregar(o["id"], o["rev"], OPER, [{"id": lb["id"], "n": 0}])
        self.assertIn("cancélala", str(e.exception))
        fin = ov.entregar(o["id"], o["rev"], OPER)["orden"]
        self.assertEqual((fin["estado"], fin["piezas_entregadas"]), ("entregada", 2))

    def test_si_un_conteo_dejo_menos_fisico_que_lo_apartado_se_dice(self):
        """Un conteo puede dejar libre < 0 (es la realidad; lo escribe
        inventario_libro). Entregar todo ya no cabe en el físico: la base lo
        rechaza (23514 stock_almacen_fisico_chk) y eso es de usuario, no un bug."""
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.confirmada([L(a, 4)])
        self.sql("""with x as (select sa.sku, sa.almacen, sa.fisico from almacen.stock_almacen sa
                                where sa.sku = %(sku)s and sa.almacen = 'ENSAYO' for update),
                         s as (update almacen.stock_almacen sa set fisico = 3 from x
                                where sa.sku = x.sku and sa.almacen = x.almacen
                               returning sa.sku, sa.almacen, sa.fisico, x.fisico as antes)
                    insert into almacen.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave,
                                               nota, quien, via)
                    select s.sku, s.almacen, s.fisico - s.antes, s.fisico, 'ajuste_conteo',
                           'conteo:' || %(sesion)s || ':' || s.sku || ':' || s.almacen,
                           'Conteo de la prueba', 'pruebas@prueba.test', 'automatico' from s""",
                 {"sku": a, "sesion": uuid.uuid4().hex})
        self.assertEqual(self.saldo(a), (3, 4, -1))
        with self.assertRaises(ov.Conflicto) as e:
            ov.entregar(o["id"], o["rev"], OPER)
        self.assertIn("No hay piezas físicas suficientes", str(e.exception))
        self.assertEqual((self.saldo(a), ov.obtener(o["id"], OPER)["estado"]),
                         ((3, 4, -1), "confirmada"))
        fin = ov.entregar(o["id"], o["rev"], OPER, [{"id": o["lineas"][0]["id"], "n": 3}])["orden"]
        self.assertEqual((fin["estado"], fin["piezas_entregadas"], self.saldo(a)),
                         ("entregada", 3, (0, 0, 0)))


class Cancelar(Base):
    def test_un_borrador_lo_cancela_quien_escribe_y_suelta_su_clave(self):
        a = self.sku("A")
        clave = self.clave("reusable")
        o = self.crear([L(a, 1)], clave=clave)
        r = ov.cancelar(o["id"], 1, OPER)
        c = r["orden"]
        self.assertEqual(r["mensaje"], "Orden cancelada.")
        self.assertEqual((c["estado"], c["rev"], c["cancelada_origen"], c["cancelada_motivo"],
                          c["cancelada_por"], c["cancelada_nombre"]),
                         ("cancelada", 2, "manual", None, "oper@prueba.test", "Olga Operadora"))
        self.assertEqual(self.eventos(o["id"]), ["creada", "cancelada"])
        self.assertFalse(c["permisos"]["cancelar"] or c["permisos"]["editar"])
        self.assertFalse(c["permisos"]["salio_tarde"])
        with self.assertRaises(ov.Invalido):
            ov.cancelar(o["id"], 2, ADMIN, "otra vez")
        # La clave de una cancelada se libera (el índice único es parcial).
        otra = self.crear([L(a, 1)], clave=clave)
        self.assertNotEqual(otra["id"], o["id"])

    def test_una_confirmada_la_cancela_quien_escribe_con_motivo_y_suelta_exacto(self):
        """Decisión de Brandon (9-oct-2026): cancelar una confirmada ya NO es sólo
        de administrador. Lo que se conserva: quien sólo lee no puede, y el motivo
        (5 caracteres o más) sigue siendo obligatorio."""
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 6)
        self.siembra(b, 2)
        o = self.confirmada([L(a, 4), L(b, 2)])
        self.assertEqual((self.saldo(a), self.saldo(b)), ((6, 4, 2), (2, 2, 0)))
        self.assertTrue(o["permisos"]["cancelar"], "el operador que la confirmó la puede cancelar")
        for quien in (LECT, NADIE):
            with self.assertRaises(ov.SinPermiso):
                ov.cancelar(o["id"], o["rev"], quien, "El cliente ya no la quiere")
        for quien in (OPER, ADMIN):
            for corto in ("", "no", "    x   "):
                with self.assertRaises(ov.Invalido) as e:
                    ov.cancelar(o["id"], o["rev"], quien, corto)
                self.assertIn("5 caracteres", str(e.exception))
        with self.assertRaises(ov.Invalido):
            ov.cancelar(o["id"], o["rev"], OPER, "Motivo válido", origen="otro")
        self.assertEqual((self.saldo(a), ov.obtener(o["id"], OPER)["rev"]), ((6, 4, 2), o["rev"]),
                         "ninguno de los rechazos movió nada")
        r = ov.cancelar(o["id"], o["rev"], OPER, "  El cliente ya no la quiere  ", origen="sistema")
        c = r["orden"]
        self.assertEqual(r["mensaje"], "Orden cancelada. Se soltaron 6 piezas.")
        self.assertEqual((c["estado"], c["cancelada_origen"], c["cancelada_motivo"],
                          c["piezas_apartadas"], c["devolucion_estado"]),
                         ("cancelada", "sistema", "El cliente ya no la quiere", 0, None))
        self.assertEqual((c["cancelada_por"], c["cancelada_nombre"]),
                         ("oper@prueba.test", "Olga Operadora"), "queda escrito quién fue")
        self.assertEqual((self.saldo(a), self.saldo(b)), ((6, 0, 6), (2, 0, 2)))
        self.assertEqual([l["reservado"] for l in c["lineas"]], [0, 0])
        m = ov.mensajes(o["id"])["mensajes"][-1]
        self.assertEqual(m["evento"], "cancelada")
        self.assertIn("se soltaron 6 piezas", m["cuerpo"])
        self.assertEqual([(x["sku"], x["reservado"]) for x in m["datos"]["lineas"]],
                         [(a, 4), (b, 2)])
        # «Salió tarde» —una cancelada que estuvo confirmada— sí sigue siendo de admin.
        self.assertFalse(c["permisos"]["salio_tarde"])
        self.assertTrue(ov.obtener(o["id"], ADMIN)["permisos"]["salio_tarde"])

    def test_cancelar_con_piezas_ya_entregadas_espera_devolucion(self):
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 5)
        self.siembra(b, 5)
        o = self.confirmada([L(a, 3), L(b, 2)])
        la = o["lineas"][0]
        o = ov.entregar(o["id"], o["rev"], OPER, [{"id": la["id"], "n": 2}])["orden"]
        self.assertEqual((self.saldo(a), self.saldo(b)), ((3, 0, 3), (5, 2, 3)))
        r = ov.cancelar(o["id"], o["rev"], ADMIN, "El cliente canceló el resto")
        c = r["orden"]
        self.assertIn("en espera de la devolución", r["mensaje"])
        self.assertEqual((c["estado"], c["devolucion_estado"], c["piezas_entregadas"],
                          c["piezas_apartadas"]), ("entregada_cancelada", "pendiente", 2, 0))
        # La entrega es de quien sacó las piezas y de cuándo salieron, no de quien cancela.
        self.assertEqual(c["entregada_por"], "oper@prueba.test")
        self.assertEqual(datetime.fromisoformat(c["entregada_at"]),
                         datetime.fromisoformat(c["lineas"][0]["entregado_at"]))
        self.assertEqual((c["cancelada_por"], c["cancelada_origen"]), ("admin@prueba.test", "manual"))
        self.assertEqual((self.saldo(a), self.saldo(b)), ((3, 0, 3), (5, 0, 5)),
                         "lo que no salió se soltó; lo que salió no vuelve solo")
        m = ov.mensajes(o["id"])["mensajes"][-1]
        self.assertEqual(m["evento"], "devolucion_esperada")
        self.assertIn("DELIVERED but CANCELLED", m["cuerpo"])
        self.assertEqual(ov.listar("por_devolver")["conteos"]["por_devolver"],
                         self.sql("select count(*) as n from ventas.ov_ordenes where borrada_at is null "
                                  "and devolucion_estado = 'pendiente'")[0]["n"])

    def test_una_entregada_no_se_cancela_a_mano(self):
        o = self.confirmada([L(self.sku("A"), 1)])
        o = ov.entregar(o["id"], o["rev"], OPER)["orden"]
        self.assertFalse(o["permisos"]["cancelar"])
        with self.assertRaises(ov.Invalido) as e:
            ov.cancelar(o["id"], o["rev"], ADMIN, "No debería poder")
        self.assertIn("entregada", str(e.exception))


class Borrar(Base):
    def test_solo_admin_con_motivo_y_suelta_en_la_misma_sentencia(self):
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.confirmada([L(a, 3)], mp_canal="tiktok", mp_cuenta=CUENTA,
                            mp_orden=f"V-{self.t}")
        self.assertEqual(self.saldo(a), (5, 3, 2))
        with self.assertRaises(ov.SinPermiso):
            ov.borrar(o["id"], o["rev"], OPER, "Se capturó con el SKU equivocado")
        for corto in ("", "mal", "123456789"):
            with self.assertRaises(ov.Invalido) as e:
                ov.borrar(o["id"], o["rev"], ADMIN, corto)
            self.assertIn("10 caracteres", str(e.exception))
        r = ov.borrar(o["id"], o["rev"], ADMIN, "Se capturó con el SKU equivocado")
        b = r["orden"]
        self.assertEqual(r["mensaje"], "Orden borrada.")
        self.assertEqual((b["estado"], b["borrada_por"], b["borrada_nombre"], b["borrada_motivo"],
                          b["piezas_apartadas"]),
                         ("confirmada", "admin@prueba.test", "Ada Admin",
                          "Se capturó con el SKU equivocado", 0))
        self.assertIsNotNone(b["borrada_at"])
        self.assertEqual(self.saldo(a), (5, 0, 5), "soltó en la misma sentencia")
        self.assertEqual(self.eventos(o["id"])[-1], "borrada_admin")
        # Borrada, ya no se mueve: todo falso, salvo bajar un PDF para el admin.
        p = b["permisos"]
        self.assertEqual([k for k, v in p.items() if v is True], ["bajar_archivo"])
        self.assertFalse(ov.obtener(o["id"], OPER)["permisos"]["bajar_archivo"])
        for fn in (lambda: ov.cancelar(o["id"], b["rev"], ADMIN, "Ya está borrada"),
                   lambda: ov.borrar(o["id"], b["rev"], ADMIN, "Borrarla otra vez"),
                   lambda: ov.entregar(o["id"], b["rev"], OPER),
                   lambda: ov.enviar_mensaje(o["id"], OPER, "hola")):
            with self.assertRaises(ov.Invalido) as e:
                fn()
            self.assertIn("borrada", str(e.exception))
        # Sale de la lista (está en «borradas») y suelta su venta: se puede volver a capturar.
        self.assertNotIn(o["id"], [x["id"] for x in ov.listar(q=o["folio"])["ordenes"]])
        self.assertIn(o["id"], [x["id"] for x in ov.listar("borradas", q=o["folio"])["ordenes"]])
        de_nuevo = self.crear([L(a, 3)], mp_canal="tiktok", mp_cuenta=CUENTA,
                              mp_orden=f"V-{self.t}")
        self.assertNotEqual(de_nuevo["id"], o["id"])

    def test_un_borrador_y_una_entregada_tambien_se_borran(self):
        d = self.crear([L(self.sku("A"), 1)])
        self.assertIsNotNone(ov.borrar(d["id"], d["rev"], ADMIN, "Borrador de prueba que sobra")
                             ["orden"]["borrada_at"])
        e = self.confirmada([L(self.sku("B"), 1)])
        e = ov.entregar(e["id"], e["rev"], OPER)["orden"]
        b = ov.borrar(e["id"], e["rev"], ADMIN, "Entregada capturada dos veces")["orden"]
        self.assertEqual((b["estado"], b["piezas_entregadas"]), ("entregada", 1))


class Inmutable(Base):
    def test_entregada_o_cancelada_guardar_es_un_400_claro_nunca_un_500(self):
        """Una CONFIRMADA sí se corrige (clase EditarConfirmada). Lo que ya no
        cambia es la entregada y la cancelada: ni el administrador, ni la guía."""
        a = self.sku("A")
        self.siembra(a, 9)
        entregada = self.confirmada([L(a, 2)])
        entregada = ov.entregar(entregada["id"], entregada["rev"], OPER)["orden"]
        cancelada = self.confirmada([L(a, 2)])
        cancelada = ov.cancelar(cancelada["id"], cancelada["rev"], OPER, "Ya no se va a surtir")["orden"]
        con_salida = self.confirmada([L(a, 2), L(self.sku("B"), 1)])
        con_salida = ov.entregar(con_salida["id"], con_salida["rev"], OPER,
                                 [{"id": con_salida["lineas"][0]["id"], "n": 2}])["orden"]
        con_salida = ov.cancelar(con_salida["id"], con_salida["rev"], OPER, "Canceló el resto")["orden"]
        for o, rotulo in ((entregada, "entregada"), (cancelada, "cancelada"),
                          (con_salida, "entregada y cancelada")):
            self.assertEqual(o["estado"], rotulo.replace(" y ", "_"))
            for quien in (OPER, ADMIN):
                for datos in ({"guia": "GUIA-TARDE"}, {"descripcion": "cambio", "lineas": [L(a, 9)]}):
                    with self.assertRaises(ov.Invalido) as e:
                        ov.guardar(o["id"], o["rev"], datos, quien)
                    self.assertEqual(e.exception.status, 400)
                    self.assertEqual(str(e.exception),
                                     f"La orden ya está {rotulo}: su contenido no cambia. Si se "
                                     "capturó mal, un administrador puede borrarla.")
            tras = ov.obtener(o["id"], OPER)
            self.assertEqual((tras["guia"], tras["rev"], tras["n_mensajes"]),
                             (None, o["rev"], o["n_mensajes"]))
            self.assertFalse(tras["permisos"]["editar"])
        self.assertEqual(self.saldo(a), (5, 0, 5), "9 − 2 − 2 que salieron; nada apartado")

    def test_si_python_no_la_frena_la_base_si_una_entregada_no_abre_la_puerta(self):
        """La red de abajo: la puerta de la 0071 sólo se abre para una orden que
        SIGUE confirmada. Con el permiso de Python apagado a propósito, la
        sentencia de editar no encuentra una entregada (su CAS pide
        `estado = 'confirmada'`) y la de guardar no encuentra nada que no sea un
        borrador: KB001, o sea «cambió, se recargó», y nada se movió."""
        a = self.sku("A")
        o = self.confirmada([L(a, 2)])
        e_ = ov.entregar(o["id"], o["rev"], OPER)["orden"]
        with mock.patch.object(ov, "_motivo", return_value=None):      # Python no la frena
            with self.assertRaises(ov.Conflicto) as e:
                ov.guardar(o["id"], e_["rev"], {"descripcion": "tarde"}, OPER)
            self.assertEqual((e.exception.status, str(e.exception)),
                             (409, "La orden cambió mientras tanto; se recargó."))
            # …y aunque alguien llamara a la sentencia de editar con esa entregada:
            with self.assertRaises(ov.Conflicto):
                ov._editar_confirmada(ov._leer_id(o["id"]), {"descripcion": "tarde"}, OPER,
                                      ov._firma(OPER))
        tras = ov.obtener(o["id"], OPER)
        self.assertEqual((tras["descripcion"], tras["rev"], tras["estado"]),
                         (None, e_["rev"], "entregada"))

    def test_si_la_orden_se_confirma_entre_la_lectura_y_el_guardado_es_un_409(self):
        """La guarda de verdad es el CAS de la sentencia. La carrera, tal cual: quien
        guarda leyó un BORRADOR (y por eso va por la sentencia de guardar), y antes
        de que su sentencia corra otro lo confirma. La base contesta KB001 y sale
        «cambió, se recargó»: lo que iba a guardar NO pisa a la confirmada."""
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.crear([L(a, 2)])
        real = ov._preparar
        carrera: list[dict] = []

        def leo_y_me_la_confirman(orden_id, rev, quien, accion, cur=None):
            leida = real(orden_id, rev, quien, accion, cur)
            if accion == "editar" and not carrera:
                carrera.append(ov.confirmar(orden_id, leida["rev"], ADMIN)["orden"])
            return leida

        with mock.patch.object(ov, "_preparar", leo_y_me_la_confirman):
            with self.assertRaises(ov.Conflicto) as e:
                ov.guardar(o["id"], o["rev"], {"descripcion": "tarde", "lineas": [L(a, 4)]}, OPER)
        self.assertEqual((e.exception.status, str(e.exception)),
                         (409, "La orden cambió mientras tanto; se recargó."))
        tras = ov.obtener(o["id"], OPER)
        self.assertEqual((tras["estado"], tras["descripcion"], tras["rev"], tras["piezas"]),
                         ("confirmada", None, carrera[0]["rev"], 2))
        self.assertEqual((self.saldo(a), self.eventos(o["id"])), ((5, 2, 3), ["creada", "confirmada"]))
        # Recargada, la MISMA corrección entra ya como edición de la confirmada.
        ok = ov.guardar(o["id"], tras["rev"], {"descripcion": "tarde", "lineas": [L(a, 4)]}, OPER)
        self.assertEqual((ok["orden"]["descripcion"], ok["orden"]["piezas_apartadas"], self.saldo(a)),
                         ("tarde", 4, (5, 4, 1)))

    def test_la_rev_vieja_es_un_409_en_toda_escritura(self):
        a = self.sku("A")
        o = self.crear([L(a, 1)])
        ov.guardar(o["id"], 1, {"descripcion": "otra pantalla la movió"}, ADMIN)
        for fn in (lambda: ov.guardar(o["id"], 1, {"descripcion": "pisar"}, OPER),
                   lambda: ov.confirmar(o["id"], 1, OPER),
                   lambda: ov.cancelar(o["id"], 1, OPER),
                   lambda: ov.borrar(o["id"], 1, ADMIN, "Motivo suficientemente largo")):
            with self.assertRaises(ov.Conflicto) as e:
                fn()
            self.assertEqual(e.exception.status, 409)
        for sin_rev in (None, 0, "x", True):
            with self.assertRaises(ov.Invalido):
                ov.guardar(o["id"], sin_rev, {"descripcion": "x"}, OPER)
        self.siembra(a, 1)
        c = ov.confirmar(o["id"], 2, OPER)["orden"]
        for fn in (lambda: ov.entregar(o["id"], 2, OPER),
                   lambda: ov.guardar(o["id"], 2, {"guia": "GUIA-PRUEBA-1"}, OPER),
                   lambda: ov.guardar(o["id"], 2, {"lineas": [L(a, 1, 99)]}, OPER),
                   lambda: ov.cancelar(o["id"], 2, OPER, "Motivo válido"),
                   lambda: ov.responder_salio(o["id"], 2, OPER, True)):
            with self.assertRaises(ov.Conflicto) as e:
                fn()
            self.assertEqual(str(e.exception), "La orden cambió mientras tanto; se recargó.")
        tras = ov.obtener(o["id"], OPER)
        self.assertEqual((tras["rev"], tras["guia"], tras["total"]), (c["rev"], None, 10.0))


class EditarConfirmada(Base):
    """Editar una orden CONFIRMADA (migración 0071) contra la base de verdad: las
    dos guardias con su puerta y, al COMMIT, `ov_coherente` y
    `stock_apartado_cuadra`. Después de CADA prueba `Base.tearDown` revisa que la
    vista vigía siga vacía y que cada orden, cada libro y cada apartado cuadren."""

    def ver(self, o: dict) -> list[tuple]:
        """(linea, sku, bodega, cantidad, reservado, entregado) de cada renglón."""
        return [(l["linea"], l["sku"], l["almacen"], l["cantidad"], l["reservado"], l["entregado"])
                for l in o["lineas"]]

    def editada(self, orden_id: int) -> dict:
        """El último mensaje `editada` de la orden."""
        return [m for m in ov.mensajes(orden_id)["mensajes"] if m["evento"] == "editada"][-1]

    # Lo que es DEL renglón (su saldo —fisico, apartado, libre— es de la bodega, y lo
    # mueven también las demás órdenes).
    _DEL_RENGLON = ("id", "linea", "sku", "titulo", "imagen", "cantidad", "precio_unitario",
                    "almacen", "reservado", "entregado", "entregado_at", "entregado_por")

    def intacta(self, o: dict, msg: str = "") -> dict:
        """La orden sigue EXACTAMENTE como estaba: ni `rev`, ni encabezado, ni
        renglones, ni un mensaje más en el chat."""
        tras = ov.obtener(o["id"], OPER)
        suyo = ("rev", "estado", "n_mensajes", "total", "comision", "cliente", "guia",
                "descripcion", "mp_orden", "piezas", "piezas_apartadas", "actualizado_at")
        self.assertEqual({k: tras[k] for k in suyo}, {k: o[k] for k in suyo}, msg)
        self.assertEqual([{k: l[k] for k in self._DEL_RENGLON} for l in tras["lineas"]],
                         [{k: l[k] for k in self._DEL_RENGLON} for l in o["lineas"]], msg)
        return tras

    # ── el encabezado ────────────────────────────────────────────────────────
    def test_solo_el_encabezado_no_toca_renglones_ni_apartado(self):
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.confirmada([L(a, 2, 100, titulo="Audífonos")], cliente="directa", canal="directa")
        self.assertTrue(o["permisos"]["editar"], "una confirmada ya se puede corregir")
        nuevo = {"cliente": " Cliente de mostrador ", "canal": "OTRO", "descripcion": "Corregida",
                 "guia": "GUIA-PRUEBA-1", "paqueteria": "Paquetería de prueba",
                 "fecha_venta": "2026-10-09T10:00:00-06:00",
                 "entrega_limite": "2026-10-12T18:00:00-06:00", "moneda": "usd", "total": 250,
                 "comision": "12.50", "precio_origen": "marketplace"}
        r = ov.guardar(o["id"], o["rev"], nuevo, OPER)
        self.assertEqual(r["mensaje"], "Cambios guardados.")
        e = r["orden"]
        self.assertEqual((e["estado"], e["rev"], e["cliente"], e["canal"], e["descripcion"], e["guia"],
                          e["paqueteria"], e["moneda"], e["total"], e["comision"], e["neto"],
                          e["precio_origen"]),
                         ("confirmada", o["rev"] + 1, "Cliente de mostrador", "otro", "Corregida",
                          "GUIA-PRUEBA-1", "Paquetería de prueba", "USD", 250.0, 12.5, 237.5,
                          "marketplace"))
        self.assertEqual((datetime.fromisoformat(e["fecha_venta"]),
                          datetime.fromisoformat(e["entrega_limite"])),
                         (datetime.fromisoformat("2026-10-09T10:00:00-06:00"),
                          datetime.fromisoformat("2026-10-12T18:00:00-06:00")))
        # Lo que NO es de quien corrige sigue fijo: el alta, la confirmación, el tipo.
        for fijo in ("id", "folio", "tipo", "creado_at", "creado_por", "creado_via", "confirmada_at",
                     "confirmada_por", "confirmada_nombre", "entregada_at", "cancelada_at",
                     "canal_cancelo_at", "devolucion_estado"):
            self.assertEqual(e[fijo], o[fijo], fijo)
        self.assertEqual((e["lineas"], e["piezas_apartadas"], self.saldo(a)),
                         (o["lineas"], 2, (5, 2, 3)), "ni un renglón ni una pieza se movieron")
        self.assertTrue(e["permisos"]["editar"] and e["permisos"]["entregar"])
        # El rastro: UN mensaje `editada`, con el antes y el después de cada campo.
        self.assertEqual(self.eventos(o["id"]), ["creada", "confirmada", "editada"])
        m = self.editada(o["id"])
        self.assertEqual((m["tipo"], m["autor"], m["autor_nombre"], m["via"]),
                         ("sistema", "oper@prueba.test", "Olga Operadora", "panel"))
        self.assertEqual(m["cuerpo"], "Orden editada · cliente, canal, descripción, guía, paquetería, "
                                      "fecha de venta, entrega límite, moneda, total, comisión, "
                                      "origen del precio")
        self.assertEqual(set(m["datos"]), {"cambios"}, "sin renglones: sólo el encabezado")
        cambios = m["datos"]["cambios"]
        self.assertEqual(set(cambios), set(nuevo))
        self.assertEqual((cambios["cliente"], cambios["canal"], cambios["guia"], cambios["moneda"],
                          cambios["total"], cambios["comision"], cambios["precio_origen"]),
                         (["directa", "Cliente de mostrador"], ["directa", "otro"],
                          [None, "GUIA-PRUEBA-1"], ["MXN", "USD"], [200.0, 250.0], [0.0, 12.5],
                          ["manual", "marketplace"]))
        self.assertEqual(datetime.fromisoformat(cambios["fecha_venta"][1]),
                         datetime.fromisoformat("2026-10-09T10:00:00-06:00"))
        # Lo mismo otra vez (la pantalla manda el documento entero): no es un cambio.
        igual = ov.guardar(o["id"], e["rev"], {**nuevo, "lineas": [L(a, 2, 100, titulo="Audífonos")]},
                           ADMIN)
        self.assertEqual((igual["mensaje"], igual["orden"]["rev"], igual["orden"]["n_mensajes"]),
                         ("Sin cambios.", e["rev"], e["n_mensajes"]))
        # Lo que no se manda no se toca; un campo se vacía mandándolo vacío; y el
        # administrador corrige igual que el operador.
        f = ov.guardar(o["id"], e["rev"], {"guia": " ", "total": None}, ADMIN)["orden"]
        self.assertEqual((f["guia"], f["total"], f["paqueteria"], f["cliente"], f["rev"]),
                         (None, 200.0, "Paquetería de prueba", "Cliente de mostrador", e["rev"] + 1))
        self.assertEqual(self.editada(o["id"])["datos"]["cambios"],
                         {"guia": ["GUIA-PRUEBA-1", None], "total": [250.0, 200.0]})
        self.assertEqual(self.editada(o["id"])["autor"], "admin@prueba.test")

    # ── los renglones y el apartado ──────────────────────────────────────────
    def test_subir_y_bajar_la_cantidad_mueve_el_apartado_exacto(self):
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 10)
        self.siembra(b, 6)
        o = self.confirmada([L(a, 3, 100, titulo="Audífonos"), L(b, 4, 50)])
        self.assertEqual((self.saldo(a), self.saldo(b)), ((10, 3, 7), (6, 4, 2)))
        ids = [l["id"] for l in o["lineas"]]
        e = ov.guardar(o["id"], o["rev"], {"total": None, "lineas": [
            L(a, 7, 100, titulo="Audífonos"), L(b, 1, 50)]}, OPER)["orden"]
        self.assertEqual(self.ver(e), [(1, a, "ENSAYO", 7, 7, None), (2, b, "ENSAYO", 1, 1, None)])
        self.assertEqual([l["id"] for l in e["lineas"]], ids, "el renglón que sigue conserva su id")
        self.assertEqual((e["estado"], e["rev"], e["piezas"], e["piezas_apartadas"], e["total"]),
                         ("confirmada", o["rev"] + 1, 8, 8, 750.0))
        # EL SALDO: subió 4 de `a` y soltó 3 de `b`. Ni una pieza de más ni de menos.
        self.assertEqual((self.saldo(a), self.saldo(b)), ((10, 7, 3), (6, 1, 5)))
        self.assertEqual([(l["fisico"], l["apartado"], l["libre"]) for l in e["lineas"]],
                         [(10, 7, 3), (6, 1, 5)])
        self.assertEqual((self.libro(a), self.libro(b)),
                         ([("correccion", 10, 10)], [("correccion", 6, 6)]),
                         "editar no saca ni mete piezas: el libro no se toca")
        m = self.editada(o["id"])
        self.assertEqual(m["cuerpo"], "Orden editada · total; renglones: 2 con cambios · se movió "
                                      "el apartado")
        self.assertEqual(m["datos"]["apartado"], [{"sku": a, "almacen": "ENSAYO", "delta": 4},
                                                  {"sku": b, "almacen": "ENSAYO", "delta": -3}])
        self.assertEqual(m["datos"]["renglones"],
                         {"agregados": [], "quitados": [],
                          "cambiados": [{"sku": a, "cantidad": [3, 7]}, {"sku": b, "cantidad": [4, 1]}]})
        self.assertEqual(m["datos"]["cambios"], {"total": [500.0, 750.0]})
        # Hasta el tope EXACTO de lo libre (quedaban 3) sí entra…
        tope = ov.guardar(o["id"], e["rev"], {"lineas": [L(a, 10, 100, titulo="Audífonos"),
                                                         L(b, 1, 50)]}, ADMIN)["orden"]
        self.assertEqual((self.saldo(a), tope["piezas_apartadas"]), ((10, 10, 0), 11))
        # …y bajarlo todo lo suelta todo menos lo que queda pedido.
        baja = ov.guardar(o["id"], tope["rev"], {"lineas": [L(a, 1, 100, titulo="Audífonos"),
                                                            L(b, 1, 50)]}, OPER)["orden"]
        self.assertEqual((self.saldo(a), self.saldo(b), baja["piezas_apartadas"]),
                         ((10, 1, 9), (6, 1, 5), 2))
        self.assertEqual(self.editada(o["id"])["datos"]["apartado"],
                         [{"sku": a, "almacen": "ENSAYO", "delta": -9}])
        # La orden editada se entrega como cualquiera: sale lo que quedó pedido.
        fin = ov.entregar(o["id"], baja["rev"], OPER)["orden"]
        self.assertEqual((fin["estado"], fin["piezas_entregadas"], self.saldo(a), self.saldo(b)),
                         ("entregada", 2, (9, 0, 9), (5, 0, 5)))

    def test_quitar_agregar_y_reordenar_renglones(self):
        a, b, c = self.sku("A"), self.sku("B"), self.sku("C")
        for sku in (a, b, c):
            self.siembra(sku, 5)
        o = self.confirmada([L(a, 2), L(b, 3)])
        de_a = o["lineas"][0]["id"]
        e = ov.guardar(o["id"], o["rev"], {"total": None, "lineas": [
            L(c, 4, 30, titulo="Cable"), L(a, 2)]}, OPER)["orden"]
        self.assertEqual(self.ver(e), [(1, c, "ENSAYO", 4, 4, None), (2, a, "ENSAYO", 2, 2, None)],
                         "en el orden recibido; el nuevo nace apartado completo")
        self.assertEqual((e["lineas"][1]["id"], e["lineas"][0]["titulo"], e["total"], e["renglones"]),
                         (de_a, "Cable", 140.0, 2))
        self.assertEqual((self.saldo(a), self.saldo(b), self.saldo(c)),
                         ((5, 2, 3), (5, 0, 5), (5, 4, 1)), "soltó `b` entero y apartó `c`")
        m = self.editada(o["id"])
        self.assertEqual(m["datos"]["renglones"], {"agregados": [c], "quitados": [b], "cambiados": []})
        self.assertEqual(m["datos"]["apartado"], [{"sku": c, "almacen": "ENSAYO", "delta": 4},
                                                  {"sku": b, "almacen": "ENSAYO", "delta": -3}])
        self.assertEqual([(x["sku"], x["cantidad"], x["reservado"], x["almacen"], x["precio_unitario"])
                          for x in m["datos"]["lineas"]],
                         [(c, 4, 4, "ENSAYO", 30.0), (a, 2, 2, "ENSAYO", 10.0)],
                         "los renglones por entregar como QUEDAN, apartados completos")
        self.assertEqual(m["cuerpo"], "Orden editada · total; renglones: 1 agregado(s), 1 quitado(s) "
                                      "· se movió el apartado")
        # Sólo reordenar o cambiar el precio NO mueve el apartado (ni pide stock: de
        # `c` no queda más que 1 libre y aun así entra).
        orden = ov.guardar(o["id"], e["rev"], {"lineas": [L(a, 2, 12.5), L(c.lower(), 4, 30,
                                                                         titulo="Cable")]},
                           OPER)["orden"]
        self.assertEqual(self.ver(orden), [(1, a, "ENSAYO", 2, 2, None), (2, c, "ENSAYO", 4, 4, None)])
        self.assertEqual((orden["lineas"][0]["precio_unitario"], orden["lineas"][0]["id"]),
                         (12.5, de_a))
        self.assertEqual((self.saldo(a), self.saldo(c)), ((5, 2, 3), (5, 4, 1)))
        m = self.editada(o["id"])
        self.assertEqual((m["datos"]["apartado"], m["datos"]["renglones"]["cambiados"], m["cuerpo"]),
                         ([], [{"sku": a, "precio_unitario": [10.0, 12.5]}],
                          "Orden editada · renglones: 1 con cambios"))
        # El mismo SKU repetido en la captura se SUMA, como en un borrador.
        suma = ov.guardar(o["id"], orden["rev"], {"lineas": [L(a, 1, 12.5), L(a, 2, 12.5)]},
                          OPER)["orden"]
        self.assertEqual((self.ver(suma), self.saldo(a), self.saldo(c)),
                         ([(1, a, "ENSAYO", 3, 3, None)], (5, 3, 2), (5, 0, 5)))

    def test_cambiar_el_titulo_o_la_imagen_de_un_renglon_deja_el_antes_y_el_despues(self):
        """El título y la imagen de un renglón también se guardan al editar (la
        sentencia los escribe). Su rastro decía «renglones: reordenados», con
        `cambiados` vacío: el cambio no quedaba en ningún lado y la imagen de antes
        no se recuperaba de ningún mensaje. Es el camino de la llave de máquina y de
        Claude, que pueden mandar los renglones sin título ni imagen (son
        opcionales) y con eso BORRARLOS."""
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 10)
        self.siembra(b, 10)
        foto, otra_foto = "https://img.prueba.test/a.jpg", "https://img.prueba.test/a2.jpg"
        o = self.confirmada([L(a, 3, 100, titulo="Audífonos", imagen=foto),
                             L(b, 2, 50, titulo="Cable")])
        saldos = (self.saldo(a), self.saldo(b))

        def editar(lineas: list[dict]) -> tuple[dict, str, dict, list]:
            nonlocal o
            o = ov.guardar(o["id"], o["rev"], {"lineas": lineas}, API)["orden"]
            m = self.editada(o["id"])
            self.assertEqual((self.saldo(a), self.saldo(b)), saldos, "ni pide ni suelta stock")
            return o, m["cuerpo"], m["datos"]["renglones"], m["datos"]["apartado"]

        def cambiados(*entradas: dict) -> dict:
            return {"agregados": [], "quitados": [], "cambiados": list(entradas)}

        # Sólo el título.
        e, cuerpo, renglones, apartado = editar([L(a, 3, 100, titulo="Audífonos BT", imagen=foto),
                                                 L(b, 2, 50, titulo="Cable")])
        self.assertEqual((e["lineas"][0]["titulo"], cuerpo, renglones, apartado),
                         ("Audífonos BT", "Orden editada · renglones: 1 con cambios",
                          cambiados({"sku": a, "titulo": ["Audífonos", "Audífonos BT"]}), []))
        # Sólo la imagen: queda la de antes, que no está en ningún otro mensaje.
        e, cuerpo, renglones, _ = editar([L(a, 3, 100, titulo="Audífonos BT", imagen=otra_foto),
                                          L(b, 2, 50, titulo="Cable")])
        self.assertEqual((e["lineas"][0]["imagen"], cuerpo, renglones),
                         (otra_foto, "Orden editada · renglones: 1 con cambios",
                          cambiados({"sku": a, "imagen": [foto, otra_foto]})))
        # Sin título ni imagen: los BORRA de los dos renglones, y lo que se va queda escrito.
        e, cuerpo, renglones, _ = editar([L(a, 3, 100), L(b, 2, 50)])
        self.assertEqual([(l["titulo"], l["imagen"]) for l in e["lineas"]], [(None, None)] * 2)
        self.assertEqual((cuerpo, renglones),
                         ("Orden editada · renglones: 2 con cambios",
                          cambiados({"sku": a, "titulo": ["Audífonos BT", None],
                                     "imagen": [otra_foto, None]},
                                    {"sku": b, "titulo": ["Cable", None]})))
        # «Reordenados» queda para cuando lo ÚNICO que cambió fue el orden…
        e, cuerpo, renglones, _ = editar([L(b, 2, 50), L(a, 3, 100)])
        self.assertEqual(([l["sku"] for l in e["lineas"]], cuerpo, renglones),
                         ([b, a], "Orden editada · renglones: reordenados", cambiados()))
        # …y junto con la cantidad va todo en la misma entrada (aquí sí se mueve el apartado).
        f = ov.guardar(o["id"], o["rev"], {"lineas": [L(b, 3, 50, titulo="Cable USB-C"),
                                                      L(a, 3, 100)]}, OPER)["orden"]
        m = self.editada(o["id"])
        self.assertEqual((m["cuerpo"], m["datos"]["renglones"], f["piezas_apartadas"]),
                         ("Orden editada · renglones: 1 con cambios · se movió el apartado",
                          cambiados({"sku": b, "cantidad": [2, 3],
                                     "titulo": [None, "Cable USB-C"]}), 6))
        # Un BORRADOR usa la misma diferencia: tampoco dice «reordenados» por un título.
        d = self.crear([L(a, 1, 100, titulo="Audífonos")])
        ov.guardar(d["id"], d["rev"], {"lineas": [L(a, 1, 100, titulo="Audífonos BT")]}, OPER)
        g = ov.mensajes(d["id"])["mensajes"][-1]
        self.assertEqual((g["evento"], g["cuerpo"], g["datos"]["renglones"]["cambiados"]),
                         ("borrador_guardado", "Borrador guardado · renglones: 1 con cambios",
                          [{"sku": a, "titulo": ["Audífonos", "Audífonos BT"]}]))

    def test_cambiar_un_renglon_de_bodega_y_lo_que_pasa_si_un_acta_la_apaga(self):
        """El fixture sólo trae ENSAYO con `admite_ov`. Para tener otra bodega que
        admita órdenes se enciende TEX3 DENTRO de la transacción de la prueba (un
        cambio a `almacen.almacenes`, con motivo y quién) y el ROLLBACK lo deshace,
        como hace el verificador y la clase CrearAuto: por eso va con `cur=`."""
        a = self.sku("A")

        def tex3(cur, admite: bool) -> None:
            cur.execute("""select set_config('app.usuario', 'pruebas@prueba.test', true);
                           update almacen.almacenes set admite_ov = %s, motivo = %s
                            where codigo = 'TEX3'""",
                        (admite, f"Prueba: TEX3 {'admite' if admite else 'ya no admite'} órdenes"))

        def saldo(cur, almacen: str) -> tuple | None:
            cur.execute("select fisico, apartado, libre from almacen.stock_almacen "
                        "where sku = %s and almacen = %s", (a, almacen))
            f = cur.fetchone()
            return (f["fisico"], f["apartado"], f["libre"]) if f else None

        with self.conexion() as cn:
            cur = cn.cursor()
            tex3(cur, True)
            self.siembra(a, 5, cur=cur)
            self.siembra(a, 3, "TEX3", cur=cur)
            o = ov.crear_borrador({"lineas": [L(a, 2)]}, OPER, self.clave(), cur=cur)["orden"]
            o = ov.confirmar(o["id"], o["rev"], OPER, cur=cur)["orden"]
            self.assertEqual((saldo(cur, "ENSAYO"), saldo(cur, "TEX3")), ((5, 2, 3), (3, 0, 3)))

            # De ENSAYO a TEX3: suelta en una y aparta en la otra, en la MISMA sentencia.
            e = ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 2, almacen="tex3")]}, OPER,
                           cur=cur)["orden"]
            self.assertEqual((self.ver(e), e["bodegas"]), ([(1, a, "TEX3", 2, 2, None)], ["TEX3"]))
            self.assertEqual((saldo(cur, "ENSAYO"), saldo(cur, "TEX3")), ((5, 0, 5), (3, 2, 1)))
            m = [x for x in ov.mensajes(o["id"], cur=cur)["mensajes"] if x["evento"] == "editada"][-1]
            self.assertEqual(m["datos"]["renglones"], {"agregados": [a], "quitados": [a],
                                                       "cambiados": []},
                             "cambiar la bodega de un renglón es quitarlo y ponerlo")
            self.assertEqual(m["datos"]["apartado"], [{"sku": a, "almacen": "TEX3", "delta": 2},
                                                      {"sku": a, "almacen": "ENSAYO", "delta": -2}])
            # El mismo SKU puede salir de DOS bodegas: dos renglones, cada uno con su apartado.
            e = ov.guardar(o["id"], e["rev"], {"lineas": [L(a, 2, almacen="TEX3"), L(a, 4)]}, OPER,
                           cur=cur)["orden"]
            self.assertEqual(self.ver(e), [(1, a, "TEX3", 2, 2, None), (2, a, "ENSAYO", 4, 4, None)])
            self.assertEqual((saldo(cur, "ENSAYO"), saldo(cur, "TEX3")), ((5, 4, 1), (3, 2, 1)))
            # Si no alcanza, dice en CUÁL bodega (lo libre es por bodega, no del SKU).
            with self.assertRaises(ov.Conflicto) as x:
                ov.guardar(o["id"], e["rev"], {"lineas": [L(a, 4, almacen="TEX3"), L(a, 4)]}, OPER,
                           cur=cur)
            self.assertEqual(str(x.exception), f"No alcanzó el stock para guardar el cambio: {a} "
                                               "necesita 2 más y hay 1 libre en TEX3. No se guardó nada.")
            self.assertEqual((saldo(cur, "ENSAYO"), saldo(cur, "TEX3")), ((5, 4, 1), (3, 2, 1)))

            # UN ACTA APAGA TEX3 con piezas apartadas ahí. Python decidió con el
            # catálogo de antes; la sentencia lo vuelve a exigir DENTRO del candado
            # (C11) y sale un 400 que dice cuál bodega, no un 500.
            de_antes = ov._bodegas_mapa(cur)
            tex3(cur, False)
            real, vistas = ov._bodegas_mapa, []

            def catalogo_de_antes(c=None):
                vistas.append(1)
                return de_antes if len(vistas) == 1 else real(c)

            with mock.patch.object(ov, "_bodegas_mapa", catalogo_de_antes):
                with self.assertRaises(ov.Invalido) as x:
                    ov.guardar(o["id"], e["rev"], {"lineas": [L(a, 3, almacen="TEX3"), L(a, 4)]},
                               OPER, cur=cur)
            self.assertEqual((x.exception.status, str(x.exception), len(vistas)),
                             (400, f"{a}: la bodega TEX3 no admite órdenes de venta (está apagada).",
                              2))
            # Con el catálogo de AHORA ni llega a la base: en TEX3 ya no se captura.
            with self.assertRaises(ov.Invalido) as x:
                ov.guardar(o["id"], e["rev"], {"lineas": [L(a, 1, almacen="TEX3"), L(a, 4)]}, OPER,
                           cur=cur)
            self.assertIn("la bodega TEX3 no admite órdenes de venta", str(x.exception))
            self.assertEqual((saldo(cur, "ENSAYO"), saldo(cur, "TEX3"),
                              ov.obtener(o["id"], OPER, cur=cur)["rev"]), ((5, 4, 1), (3, 2, 1), e["rev"]))
            # El encabezado sí se sigue corrigiendo, y SACAR el renglón de la bodega
            # apagada también: soltar siempre se puede.
            e = ov.guardar(o["id"], e["rev"], {"guia": "GUIA-PRUEBA-1"}, OPER, cur=cur)["orden"]
            e = ov.guardar(o["id"], e["rev"], {"lineas": [L(a, 5)]}, OPER, cur=cur)["orden"]
            self.assertEqual(self.ver(e), [(1, a, "ENSAYO", 5, 5, None)])
            self.assertEqual((saldo(cur, "ENSAYO"), saldo(cur, "TEX3")), ((5, 5, 0), (3, 0, 3)))
            cur.execute("set constraints all immediate")        # lo que revisaría el COMMIT
            cur.execute("select count(*) as n from ops.stock_apartado_descuadre_v")
            self.assertEqual(cur.fetchone()["n"], 0)
        self.assertEqual(self.saldo(a), None, "el ROLLBACK lo deshizo todo")
        self.assertEqual(self.sql("select admite_ov from almacen.almacenes where codigo = 'TEX3'"),
                         [{"admite_ov": False}])

    # ── todo o nada ──────────────────────────────────────────────────────────
    def test_si_no_alcanza_no_cambia_nada_ni_la_rev_ni_el_chat_y_dice_cuanto_falta(self):
        a, b, c = self.sku("A"), self.sku("B"), self.sku("C")
        self.siembra(a, 5)
        self.siembra(b, 2)
        o = self.confirmada([L(a, 2), L(b, 1)], guia="GUIA-PRUEBA-0")
        self.confirmada([L(a, 2)])                      # OTRA orden aparta 2 de `a`: queda 1 libre
        self.assertEqual((self.saldo(a), self.saldo(b), self.saldo(c)), ((5, 4, 1), (2, 1, 1), None))
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(o["id"], o["rev"], {"guia": "GUIA-NUEVA", "total": None, "lineas": [
                L(a, 5), L(b, 2), L(c, 1)]}, OPER)       # `b` SÍ alcanzaba; `c` ni tiene saldo
        texto = str(e.exception)
        self.assertEqual((e.exception.status, texto),
                         (409, f"No alcanzó el stock para guardar el cambio: {a} necesita 3 más y "
                               f"hay 1 libre en ENSAYO; {c} necesita 1 más y no tiene existencias "
                               "registradas en ENSAYO. No se guardó nada."))
        self.assertNotIn("no_alcanzo", texto, "nunca el nombre técnico")
        # NADA cambió: ni el renglón que sí alcanzaba, ni la guía, ni la rev (por eso la
        # pantalla sabe que el 409 no es «la orden cambió»), ni un mensaje en el chat.
        self.intacta(o)
        self.assertEqual((self.saldo(a), self.saldo(b), self.saldo(c)), ((5, 4, 1), (2, 1, 1), None))
        self.assertEqual(self.eventos(o["id"]), ["creada", "confirmada"],
                         "a diferencia de confirmar, aquí no queda aviso `no_alcanzo`")
        # Con la MISMA rev, lo que sí cabe entra: `a` sube justo la pieza libre.
        ok = ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 3), L(b, 2)]}, OPER)["orden"]
        self.assertEqual((ok["rev"], self.saldo(a), self.saldo(b)), (o["rev"] + 1, (5, 5, 0), (2, 2, 0)))
        # En el tope, una pieza más ya no entra: hay 0 libres (y se dice en plural).
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(o["id"], ok["rev"], {"lineas": [L(a, 4), L(b, 2)]}, ADMIN)
        self.assertEqual(str(e.exception), f"No alcanzó el stock para guardar el cambio: {a} "
                                           "necesita 1 más y hay 0 libres en ENSAYO. No se guardó nada.")
        self.intacta(ok)
        # Soltar de un SKU y apartar más de otro en la MISMA edición: una sola
        # sentencia, y cada saldo se mueve por su diferencia.
        self.siembra(b, 1)
        mixto = ov.guardar(o["id"], ok["rev"], {"lineas": [L(a, 1), L(b, 3)]}, OPER)["orden"]
        self.assertEqual((self.saldo(a), self.saldo(b), mixto["piezas_apartadas"]),
                         ((5, 3, 2), (3, 3, 0), 4))

    def test_un_conteo_que_dejo_libre_negativo_no_deja_subir_pero_si_soltar(self):
        """Un conteo puede dejar menos físico que lo apartado (libre < 0; lo escribe
        inventario_libro). Subir ahí no se puede —y se dice «hay 0 libres», no un
        número negativo—; bajar sí, y con eso el saldo vuelve a cuadrar."""
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.confirmada([L(a, 4)])
        self.sql("""with x as (select sa.sku, sa.almacen, sa.fisico from almacen.stock_almacen sa
                                where sa.sku = %(sku)s and sa.almacen = 'ENSAYO' for update),
                         s as (update almacen.stock_almacen sa set fisico = 3 from x
                                where sa.sku = x.sku and sa.almacen = x.almacen
                               returning sa.sku, sa.almacen, sa.fisico, x.fisico as antes)
                    insert into almacen.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave,
                                               nota, quien, via)
                    select s.sku, s.almacen, s.fisico - s.antes, s.fisico, 'ajuste_conteo',
                           'conteo:' || %(sesion)s || ':' || s.sku || ':' || s.almacen,
                           'Conteo de la prueba', 'pruebas@prueba.test', 'automatico' from s""",
                 {"sku": a, "sesion": uuid.uuid4().hex})
        self.assertEqual(self.saldo(a), (3, 4, -1))
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 5)]}, OPER)
        self.assertIn(f"{a} necesita 1 más y hay 0 libres en ENSAYO", str(e.exception))
        self.assertEqual(self.saldo(a), (3, 4, -1))
        baja = ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 3)]}, OPER)["orden"]
        self.assertEqual((self.saldo(a), baja["piezas_apartadas"]), ((3, 3, 0), 3),
                         "ya cuadra: al terminar, la vista vigía vuelve a estar vacía")

    # ── lo que ya salió ──────────────────────────────────────────────────────
    def test_tras_una_entrega_parcial_se_edita_lo_que_queda_y_lo_que_salio_no_se_toca(self):
        a, b, c = self.sku("A"), self.sku("B"), self.sku("C")
        for sku in (a, b, c):
            self.siembra(sku, 10)
        o = self.confirmada([L(a, 3, 100, titulo="Audífonos"), L(b, 2, 50)])
        o = ov.entregar(o["id"], o["rev"], OPER, [{"id": o["lineas"][0]["id"], "n": 2}])["orden"]
        salio = o["lineas"][0]
        self.assertEqual((self.ver(o), self.saldo(a)),
                         ([(1, a, "ENSAYO", 3, 0, 2), (2, b, "ENSAYO", 2, 2, None)], (8, 0, 8)))
        self.assertTrue(o["permisos"]["editar"], "sigue confirmada: queda un renglón por salir")
        # La pantalla manda el documento ENTERO: el eco del que salió, `b` con otra
        # cantidad y un renglón nuevo.
        e = ov.guardar(o["id"], o["rev"], {"total": None, "lineas": [
            L(a, 3, 100, titulo="Audífonos"), L(b, 5, 50), L(c, 1, 10)]}, OPER)["orden"]
        self.assertEqual(self.ver(e), [(1, a, "ENSAYO", 3, 0, 2), (2, b, "ENSAYO", 5, 5, None),
                                       (3, c, "ENSAYO", 1, 1, None)])
        self.assertEqual(e["lineas"][0], salio, "el renglón que ya salió quedó IDÉNTICO")
        self.assertEqual((e["total"], e["piezas_entregadas"], e["piezas_apartadas"]),
                         (560.0, 2, 6), "el total es de TODA la orden: 3×100 + 5×50 + 1×10")
        self.assertEqual((self.saldo(a), self.saldo(b), self.saldo(c)),
                         ((8, 0, 8), (10, 5, 5), (10, 1, 9)))
        m = self.editada(o["id"])
        self.assertEqual([(x["sku"], x["cantidad"], x["reservado"]) for x in m["datos"]["lineas"]],
                         [(b, 5, 5), (c, 1, 1)], "sólo los que siguen por entregar")
        self.assertEqual(m["datos"]["renglones"], {"agregados": [c], "quitados": [],
                                                   "cambiados": [{"sku": b, "cantidad": [2, 5]}]})
        # TOCAR el que ya salió: 400 con qué hacer. Ni otra cantidad, ni «sumarle» piezas.
        for malas in ([L(a, 2, 100), L(b, 5, 50), L(c, 1, 10)],
                      [L(a, 3, 100), L(a.lower(), 1, 100), L(b, 5, 50)]):
            with self.assertRaises(ov.Invalido) as x:
                ov.guardar(o["id"], e["rev"], {"lineas": malas}, ADMIN)
            self.assertEqual(str(x.exception), f"El renglón de {a} ya salió de ENSAYO: no se "
                                               "cambia. Si hace falta más, va en otra orden.")
        # QUITAR lo que queda dejaría la orden sin nada por entregar: 400 que dice qué hacer.
        for vacias in ([], [L(a, 3, 100)]):
            with self.assertRaises(ov.Invalido) as x:
                ov.guardar(o["id"], e["rev"], {"lineas": vacias}, OPER)
            self.assertEqual(str(x.exception), "Una orden confirmada necesita al menos un renglón "
                                               "por entregar. Si ya salió todo, márcala DELIVERED.")
        self.intacta(e)
        # No mandar el que salió NO lo borra; y si su eco trae otro precio, no lo cambia.
        f = ov.guardar(o["id"], e["rev"], {"lineas": [L(b, 4, 50)]}, OPER)["orden"]
        self.assertEqual(self.ver(f), [(1, a, "ENSAYO", 3, 0, 2), (2, b, "ENSAYO", 4, 4, None)])
        self.assertEqual((f["lineas"][0], self.saldo(b), self.saldo(c)), (salio, (10, 4, 6), (10, 0, 10)))
        g = ov.guardar(o["id"], f["rev"], {"guia": "GUIA-PRUEBA-1", "lineas": [
            L(a, 3, 999, titulo="Otro título"), L(b, 4, 50)]}, OPER)["orden"]
        self.assertEqual((g["lineas"][0], g["guia"], self.editada(o["id"])["datos"]),
                         (salio, "GUIA-PRUEBA-1", {"cambios": {"guia": [None, "GUIA-PRUEBA-1"]}}))
        # Lo que queda se entrega como siempre, y entregada ya no se edita.
        fin = ov.entregar(o["id"], g["rev"], OPER)["orden"]
        self.assertEqual((fin["estado"], fin["piezas_entregadas"], self.saldo(b)),
                         ("entregada", 6, (6, 0, 6)))
        self.assertEqual(self.libro(a), [("correccion", 10, 10), ("salida_ov", -2, 8)])
        with self.assertRaises(ov.Invalido):
            ov.guardar(o["id"], fin["rev"], {"guia": "GUIA-TARDE"}, ADMIN)

    def test_no_puede_quedar_sin_renglones_por_entregar_y_no_llega_a_la_base(self):
        a = self.sku("A")
        o = self.confirmada([L(a, 2)])
        with mock.patch.object(ov, "_transicion", side_effect=AssertionError("llegó a la base")):
            with self.assertRaises(ov.Invalido) as e:
                ov.guardar(o["id"], o["rev"], {"guia": "GUIA-PRUEBA-1", "lineas": []}, OPER)
            self.assertEqual((e.exception.status, str(e.exception)),
                             (400, "Una orden confirmada necesita al menos un renglón por entregar. "
                                   "Si ya no va, cancélala."))
            for datos, dice in (({"lineas": [L(a, 2, almacen=None)]}, "no tiene bodega"),
                                ({"lineas": [L(a, 2, almacen="TEX3")]}, "no admite órdenes de venta"),
                                ({"lineas": [L(a, 2, almacen="TEXCO")]}, "bodega de Odoo"),
                                ({"lineas": [L(a, 0)]}, "la cantidad debe ser un entero"),
                                ({"lineas": [L(a, 1, 10), L(a, 1, 11)]}, "precios distintos"),
                                ({"canal": "facebook"}, "El canal debe ser uno de")):
                with self.assertRaises(ov.Invalido, msg=str(datos)) as e:
                    ov.guardar(o["id"], o["rev"], datos, OPER)
                self.assertIn(dice, str(e.exception))
        self.intacta(o)
        self.assertEqual(self.saldo(a), (2, 2, 0))

    def test_si_python_no_lo_frenara_la_base_si_y_nada_queda_a_medias(self):
        """Las dos cosas que Python valida ANTES porque la base sólo las ve AL COMMIT.
        Con esa validación apagada a propósito: la sentencia corre, el COMMIT truena
        (constraint triggers y únicos diferidos) y TODO se deshace —saldo incluido—.
        Sale como lo que sería entonces: un bug nuestro (502, con su regla en el log)."""
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 5)
        self.siembra(b, 5)
        o = self.confirmada([L(a, 2), L(b, 2)])
        o = ov.entregar(o["id"], o["rev"], OPER, [{"id": o["lineas"][0]["id"], "n": 2}])["orden"]
        self.assertEqual((self.saldo(a), self.saldo(b)), ((3, 0, 3), (5, 2, 3)))
        repetido = {"linea": 2, "sku": a, "titulo": None, "imagen": None, "cantidad": 1,
                    "precio_unitario": Decimal("10.00"), "almacen": "ENSAYO"}
        for nuevos, regla in (([], "ov_coherente"),                    # sin nada por entregar
                              ([repetido], "ov_lineas_sku_alm_uq")):   # el (sku, bodega) que ya salió
            with mock.patch.object(ov, "_pendientes_nuevos", return_value=nuevos):
                with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
                    with self.assertRaises(ov.ErrorOV) as e:
                        ov.guardar(o["id"], o["rev"], {"lineas": [L(b, 2)]}, OPER)
            self.assertEqual((type(e.exception), e.exception.status), (ov.ErrorOV, 502), regla)
            self.assertIn(f"regla={regla}", "\n".join(logs.output))
            self.assertNotIn(regla, str(e.exception))
            self.intacta(o, regla)
            self.assertEqual((self.saldo(a), self.saldo(b)), ((3, 0, 3), (5, 2, 3)), regla)

    # ── quién, y cuándo no ───────────────────────────────────────────────────
    def test_el_operador_edita_y_cancela_una_confirmada_y_quien_solo_lee_no(self):
        a = self.sku("A")
        self.siembra(a, 6)
        o = self.confirmada([L(a, 2)], quien=ADMIN)          # la confirmó un administrador
        for quien in (LECT, NADIE):
            p = ov.obtener(o["id"], quien)["permisos"]
            self.assertFalse(p["editar"] or p["cancelar"])
            for fn in (lambda: ov.guardar(o["id"], o["rev"], {"guia": "GUIA-PRUEBA-1"}, quien),
                       lambda: ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 6)]}, quien),
                       lambda: ov.cancelar(o["id"], o["rev"], quien, "Motivo válido")):
                with self.assertRaises(ov.SinPermiso) as e:
                    fn()
                self.assertEqual(e.exception.status, 403)
        self.intacta(o)
        # El OPERADOR la corrige —y la llave de la API también: queda escrito por dónde—.
        e = ov.guardar(o["id"], o["rev"], {"guia": "GUIA-PRUEBA-1", "lineas": [L(a, 3)]}, OPER)["orden"]
        e = ov.guardar(o["id"], e["rev"], {"paqueteria": "Paquetería de prueba"}, API)["orden"]
        firmas = [(m["autor"], m["autor_nombre"], m["via"])
                  for m in ov.mensajes(o["id"])["mensajes"] if m["evento"] == "editada"]
        self.assertEqual(firmas, [("oper@prueba.test", "Olga Operadora", "panel"),
                                  ("servicio", "API", "api")])
        self.assertEqual((e["confirmada_por"], e["creado_por"], self.saldo(a)),
                         ("admin@prueba.test", "admin@prueba.test", (6, 3, 3)),
                         "quien confirmó sigue siendo quien confirmó")
        # …y el OPERADOR la cancela (ya no hace falta un administrador), con su motivo.
        self.assertTrue(ov.obtener(o["id"], OPER)["permisos"]["cancelar"])
        with self.assertRaises(ov.Invalido):
            ov.cancelar(o["id"], e["rev"], OPER, "no")
        c = ov.cancelar(o["id"], e["rev"], OPER, "El cliente ya no la quiso")["orden"]
        self.assertEqual((c["estado"], c["cancelada_por"], c["cancelada_origen"], self.saldo(a)),
                         ("cancelada", "oper@prueba.test", "manual", (6, 0, 6)),
                         "soltó lo que la EDICIÓN había dejado apartado (3), no lo de antes")
        self.assertEqual(self.eventos(o["id"]), ["creada", "confirmada", "editada", "editada",
                                                 "cancelada"])
        self.assertFalse(c["permisos"]["editar"])

    def test_esperando_el_salio_no_se_edita(self):
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.confirmada([L(a, 2)], mp_canal="tiktok", mp_cuenta=CUENTA,
                            mp_orden=f"V-{self.t}")
        m = ov.canal_cancelo(o["id"], "IN_TRANSIT", "", True)["orden"]
        self.assertEqual(m["estado"], "confirmada")
        for quien in (OPER, ADMIN):
            p = ov.obtener(o["id"], quien)["permisos"]
            self.assertTrue(p["responder_salio"])
            self.assertFalse(p["editar"] or p["cancelar"] or p["entregar"])
            self.assertEqual(p["porque"]["editar"], "El canal canceló esta venta con el paquete "
                                                    "en camino: primero hay que contestar si salió")
            for datos in ({"guia": "GUIA-PRUEBA-1"}, {"lineas": [L(a, 1)]}):
                with self.assertRaises(ov.Invalido) as e:
                    ov.guardar(o["id"], m["rev"], datos, quien)
                self.assertIn("contestar si salió", str(e.exception))
        # Y si Python no la frenara, la sentencia tampoco la encuentra (su CAS pide
        # que el canal NO la haya cancelado): 409, y nada se movió.
        with mock.patch.object(ov, "_motivo", return_value=None):
            with self.assertRaises(ov.Conflicto):
                ov.guardar(o["id"], m["rev"], {"lineas": [L(a, 1)]}, ADMIN)
        self.intacta(m)
        self.assertEqual(self.saldo(a), (5, 2, 3))
        # Contestada (no salió), queda cancelada: tampoco.
        c = ov.responder_salio(o["id"], m["rev"], OPER, False)["orden"]
        with self.assertRaises(ov.Invalido) as e:
            ov.guardar(o["id"], c["rev"], {"guia": "GUIA-PRUEBA-1"}, ADMIN)
        self.assertIn("cancelada", str(e.exception))

    def test_un_envio_a_full_confirmado_no_se_edita_desde_aqui(self):
        """Las órdenes `full` nacen en «Crear FULL» (este servicio las rechaza al
        crear), así que aquí se siembra una a mano, como la dejaría ese módulo: un
        borrador `full` con su número de envío. En borrador se guarda como
        cualquiera; confirmada, ni Python ni la sentencia (`v.tipo <> 'full'`) la
        dejan pasar por la puerta."""
        a = self.sku("A")
        self.siembra(a, 5)
        fila = self.sql("""
            with f as (update ventas.ov_folio set ultimo = ultimo + 1 where id = 1 returning ultimo)
            insert into ventas.ov_ordenes (folio, estado, tipo, canal, full_tienda, envio_ref,
                                           creado_por, creado_nombre, creado_via)
            select 'OV-' || lpad(f.ultimo::text, greatest(5, length(f.ultimo::text)), '0'),
                   'borrador', 'full', 'amazon', 'amazon', 'ENVIO-PRUEBA-1',
                   'pruebas@prueba.test', 'Pruebas', 'panel'
              from f
            returning id, rev""")[0]
        o = ov.guardar(fila["id"], fila["rev"], {"lineas": [L(a, 2)]}, OPER)["orden"]
        self.assertTrue(o["permisos"]["editar"], "en borrador se edita como cualquiera")
        o = ov.confirmar(o["id"], o["rev"], OPER)["orden"]
        self.assertEqual((o["tipo"], o["estado"], self.saldo(a)), ("full", "confirmada", (5, 2, 3)))
        p = o["permisos"]
        self.assertFalse(p["editar"])
        self.assertEqual(p["porque"]["editar"],
                         "Un envío a FULL ya confirmado no se edita desde esta pantalla")
        self.assertTrue(p["cancelar"] and p["entregar"], "lo demás sigue igual")
        for quien in (OPER, ADMIN):
            for datos in ({"guia": "GUIA-PRUEBA-1"}, {"lineas": [L(a, 5)]}):
                with self.assertRaises(ov.Invalido) as e:
                    ov.guardar(o["id"], o["rev"], datos, quien)
                self.assertIn("FULL", str(e.exception))
        with mock.patch.object(ov, "_motivo", return_value=None):      # Python no la frena
            with self.assertRaises(ov.Conflicto) as e:
                ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 5)]}, ADMIN)
        self.assertEqual(str(e.exception), "La orden cambió mientras tanto; se recargó.")
        self.intacta(o)
        self.assertEqual(self.saldo(a), (5, 2, 3))

    # El renglón que la 0071 deja en el registro de migraciones (de sólo agregar).
    _CORRIO_LA_0071 = ("select count(*) as n from ops.migraciones "
                       "where migracion = '0071_ov_editar_confirmada'")
    _FALTA_LA_0071 = ("Editar una orden confirmada todavía no está habilitado en esta base: falta "
                      "la migración 0071.")

    def _reponer_la_puerta(self) -> None:
        """Las dos guardias con el texto de la 0071, CONFIRMADO. (La red de las
        pruebas que quitan la puerta de verdad: se registra ANTES de quitarla.)"""
        self.sql("\n".join(_guardias(M71).values()))
        ov._olvidar_cache()

    def _texto_de_las_guardias(self) -> list[dict]:
        """La huella del texto de cada guardia, para comprobar que una prueba que
        les mete mano deja la base EXACTAMENTE como la encontró."""
        return self.sql("""select p.proname, md5(p.prosrc) as huella,
                                  position('app.ov_edicion' in p.prosrc) > 0 as con_puerta
                             from pg_proc p
                            where p.oid in ('ops.tg_ov_ordenes_guarda()'::regprocedure,
                                            'ops.tg_ov_lineas_guarda()'::regprocedure)
                            order by 1""")

    def test_por_la_puerta_se_le_pregunta_a_la_base_no_al_registro_de_migraciones(self):
        """`edicion_lista()` contra el catálogo de verdad, pieza por pieza. Cada caso
        corre en una transacción que termina en ROLLBACK (en Postgres el DDL es
        transaccional): se le quita UNA cosa de las que pone la 0071 y la respuesta
        tiene que ser «no». En todos, el renglón de la 0071 SIGUE en
        `ops.migraciones` —es de sólo agregar—: por eso ya no se le pregunta a él
        (decía que sí con la puerta quitada, y el permiso mentía para siempre)."""
        sin = _guardias(M68)                 # como las deja la 0068: sin la puerta
        con = _guardias(M71)
        self.assertNotIn("app.ov_edicion", "".join(sin.values()))
        self.assertTrue(all("app.ov_edicion" in texto for texto in con.values()))
        self.assertIs(ov.edicion_lista(refrescar=True), True, "la suite aplicó la 0071")
        como_estaban = self._texto_de_las_guardias()
        self.assertEqual([g["con_puerta"] for g in como_estaban], [True, True])
        casos = (
            ("la guardia del encabezado, sin la puerta", sin["tg_ov_ordenes_guarda"]),
            ("la guardia de los renglones, sin la puerta", sin["tg_ov_lineas_guarda"]),
            ("las dos: volver a correr la 0068, o la reversa", "\n".join(sin.values())),
            ("el catálogo de eventos sin `editada`",
             """alter table ventas.ov_mensajes drop constraint ov_mensajes_evento_chk;
                alter table ventas.ov_mensajes add constraint ov_mensajes_evento_chk
                  check (evento is null or evento in ('creada', 'confirmada', 'cancelada'))
                  not valid"""),
            # Las guardias se buscan por su nombre en `ops`: si un día se mudan o se
            # renombran, la respuesta es «no» —falla cerrado— hasta actualizar la consulta.
            ("una guardia que ya no se llama así",
             "alter function ops.tg_ov_lineas_guarda() rename to tg_ov_lineas_guarda_de_antes"))
        for nombre, ddl in casos:
            with self.subTest(nombre), self.conexion() as cn:
                cur = cn.cursor()
                self.assertIs(ov.edicion_lista(cur=cur), True)
                cur.execute(ddl)
                self.assertIs(ov.edicion_lista(cur=cur), False)
                cur.execute(self._CORRIO_LA_0071)
                self.assertEqual(cur.fetchone()["n"], 2, "el registro sigue diciendo que se corrió")
                # Y con todo en su lugar otra vez (como volver a correr la 0071), es «sí».
                if "sin la puerta" in nombre or "las dos" in nombre:
                    cur.execute("\n".join(con.values()))
                    self.assertIs(ov.edicion_lista(cur=cur), True)
        self.assertIs(ov.edicion_lista(refrescar=True), True, "cada ROLLBACK la dejó como estaba")
        self.assertEqual(self._texto_de_las_guardias(), como_estaban)
        self.assertEqual(ov._recordado("edicion"), (True, True),
                         "lo leído con un cursor prestado no se recuerda; esto sí")

    def test_sin_la_0071_no_se_ofrece_y_si_se_intenta_es_un_409_que_lo_dice(self):
        """Dos formas de «no tener» la 0071, sin buscar una base aparte:
        (1) LA BASE NO TRAE LA PUERTA, de verdad: dentro de una transacción que
            termina en ROLLBACK se reponen las dos guardias con el texto de la 0068
            (lo que hace volver a correrla, o la reversa). No se ofrece editar y se
            dice; y si Python no lo frenara, la base sí (42501). El renglón del
            registro de migraciones sigue ahí: no es a él a quien se le pregunta.
        (2) la base SÍ la trae, pero la sentencia no la abre —va sin la puerta, o
            con la de OTRA orden—: el mismo 42501 de antes de la migración. Y un
            catálogo sin `editada` (23514) es el tercer síntoma."""
        a, b, c = self.sku("A"), self.sku("B"), self.sku("C")
        self.siembra(a, 5)
        self.siembra(b, 5)
        self.siembra(c, 1)
        o = self.confirmada([L(a, 2), L(c, 1)])
        otra = self.confirmada([L(b, 1)])
        saldos = ((5, 2, 3), (5, 1, 4), (1, 1, 0))
        falta = (409, self._FALTA_LA_0071)
        self.assertTrue(ov.edicion_lista(refrescar=True), "la suite sí la aplicó")
        with self.conexion() as cn:
            cur = cn.cursor()
            cur.execute("\n".join(_guardias(M68).values()))         # ← se va la puerta
            cur.execute(self._CORRIO_LA_0071)
            self.assertEqual(cur.fetchone()["n"], 2, "el registro dice que la 0071 se corrió…")
            self.assertIs(ov.edicion_lista(cur=cur), False, "…y la base, que la puerta no está")
            p = ov.obtener(o["id"], OPER, cur=cur)["permisos"]
            self.assertFalse(p["editar"])
            self.assertEqual(p["porque"]["editar"], falta[1])
            self.assertTrue(p["cancelar"] and p["entregar"], "lo demás no depende de la 0071")
            for datos in ({"guia": "GUIA-PRUEBA-1"}, {"lineas": [L(a, 4), L(c, 1)]}):
                with self.assertRaises(ov.Conflicto) as e:
                    ov.guardar(o["id"], o["rev"], datos, ADMIN, cur=cur)
                self.assertEqual((e.exception.status, str(e.exception)), falta)
                # Y si Python no lo frenara (lo recordaba de antes), la base sí: la
                # guardia sin puerta rechaza la sentencia aunque traiga su `set local`.
                with mock.patch.object(ov, "edicion_lista", return_value=True), \
                        self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
                    with self.assertRaises(ov.Conflicto) as e:
                        ov.guardar(o["id"], o["rev"], datos, ADMIN, cur=cur)
                self.assertEqual((e.exception.status, str(e.exception)), falta)
                self.assertIn("pgcode=42501", logs.output[0])
            # Un BORRADOR se guarda igual: no pasa por la puerta.
            d = ov.crear_borrador({"lineas": [L(a, 1)]}, OPER, self.clave(), cur=cur)["orden"]
            self.assertEqual(ov.guardar(d["id"], d["rev"], {"guia": "GUIA-PRUEBA-1"}, OPER, cur=cur)
                             ["orden"]["guia"], "GUIA-PRUEBA-1")
            tras = ov.obtener(o["id"], OPER, cur=cur)
            self.assertEqual((tras["rev"], tras["guia"], tras["piezas_apartadas"]),
                             (o["rev"], None, 3), "nada se movió")
        ov._olvidar_cache()
        self.intacta(o)

        puerta = "set local app.ov_edicion = %(puerta)s;\n"
        self.assertIn(puerta, ov.SQL_EDITAR_CONFIRMADA)
        sin_puerta = ov.SQL_EDITAR_CONFIRMADA.replace(puerta, "")
        sin_evento = ov.SQL_EDITAR_CONFIRMADA.replace("'editada'", "'editada_que_no_esta'")
        real = ov._transicion

        def con_la_puerta_de_otra(operacion, sql, params, cur=None):
            return real(operacion, sql, {**params, "puerta": str(otra["id"])}, cur)

        # Cada caso tropieza con una guardia distinta: la del encabezado (UPDATE), y
        # la de los renglones al cambiar (UPDATE), agregar (INSERT) y quitar (DELETE).
        casos = (("sin puerta: el encabezado", {"guia": "GUIA-PRUEBA-1"}, sin_puerta, real,
                  "ov_ordenes_inmutable"),
                 ("sin puerta: cambiar un renglón", {"lineas": [L(a, 4), L(c, 1)]}, sin_puerta, real,
                  "ov_lineas_inmutable"),
                 ("sin puerta: agregar un renglón", {"lineas": [L(a, 2), L(c, 1), L(b, 1)]},
                  sin_puerta, real, "ov_lineas_inmutable"),
                 ("sin puerta: quitar un renglón", {"lineas": [L(a, 2)]}, sin_puerta, real,
                  "ov_lineas_inmutable"),
                 ("la puerta de OTRA orden", {"guia": "GUIA-PRUEBA-1", "lineas": [L(a, 4), L(c, 1)]},
                  ov.SQL_EDITAR_CONFIRMADA, con_la_puerta_de_otra, "ov_ordenes_inmutable"),
                 ("el catálogo sin `editada`", {"guia": "GUIA-PRUEBA-1"}, sin_evento, real,
                  "ov_mensajes_evento_chk"))
        for nombre, datos, sql, transicion, regla in casos:
            with self.subTest(nombre):
                self.assertTrue(ov.edicion_lista(), "la base SÍ trae la puerta: quien no la abre "
                                                    "es esta sentencia")
                with mock.patch.object(ov, "SQL_EDITAR_CONFIRMADA", sql), \
                        mock.patch.object(ov, "_transicion", transicion), \
                        self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
                    with self.assertRaises(ov.Conflicto) as e:
                        ov.guardar(o["id"], o["rev"], datos, OPER)
                self.assertEqual((e.exception.status, str(e.exception)), falta)
                self.assertEqual([r.levelname for r in logs.records], ["WARNING"])
                self.assertIn(f"regla={regla}", logs.output[0])
                self.assertEqual(ov._recordado("edicion"), (False, None),
                                 "lo recordado ya no vale: se vuelve a preguntar")
                self.intacta(o, nombre)
                self.assertEqual((self.saldo(a), self.saldo(b), self.saldo(c)), saldos, nombre)
        # Con la sentencia de verdad y SU puerta, la misma corrección entra.
        ok = ov.guardar(o["id"], o["rev"], {"guia": "GUIA-PRUEBA-1", "lineas": [L(a, 4), L(b, 1)]},
                        OPER)["orden"]
        self.assertEqual((ok["guia"], self.saldo(a), self.saldo(b), self.saldo(c)),
                         ("GUIA-PRUEBA-1", (5, 4, 1), (5, 2, 3), (1, 0, 1)))
        self.intacta(otra, "la otra orden ni se enteró")

    def test_si_la_base_pierde_la_puerta_el_primer_rechazo_deja_de_ofrecer_editar(self):
        """El caso entero, con el caché de verdad (por eso aquí el DDL SÍ se
        confirma; se repone pase lo que pase). Alguien vuelve a correr la 0068 —su
        encabezado dice «idempotente»— o hace la reversa de la 0071: las dos
        guardias quedan sin la puerta y el renglón de la 0071 sigue en el registro.
        Cuando se preguntaba por ese renglón el permiso seguía en True PARA SIEMPRE,
        cada guardado rebotaba con 409 y olvidar lo recordado no servía (la
        siguiente pregunta lo volvía a encontrar). Ahora lo recordado sobrevive, a
        lo más, a UN guardado: la relectura que hace la pantalla tras el 409 ya no
        ofrece editar, y dice por qué."""
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.confirmada([L(a, 2)])
        otra = self.confirmada([L(a, 1)])
        corregida = {"guia": "GUIA-PRUEBA-1", "lineas": [L(a, 4)]}
        como_estaban = self._texto_de_las_guardias()
        self.addCleanup(self._reponer_la_puerta)             # ANTES de quitarla
        self.assertIs(ov.edicion_lista(refrescar=True), True)                # recordado: 60 s
        self.sql("\n".join(_guardias(M68).values()))                         # ← se va la puerta
        self.assertEqual([g["con_puerta"] for g in self._texto_de_las_guardias()], [False, False])
        self.assertEqual(self.sql(self._CORRIO_LA_0071)[0]["n"], 2)
        # 1) Mientras dura lo recordado la pantalla todavía la ofrece (es la cortesía)…
        self.assertTrue(ov.obtener(o["id"], OPER)["permisos"]["editar"])
        # …y la guarda es la base: 42501 → 409 que lo dice, nada se mueve, y lo
        # recordado se olvida.
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
            with self.assertRaises(ov.Conflicto) as e:
                ov.guardar(o["id"], o["rev"], corregida, OPER)
        self.assertEqual((e.exception.status, str(e.exception)), (409, self._FALTA_LA_0071))
        self.assertIn("pgcode=42501 regla=ov_ordenes_inmutable", logs.output[0])
        self.assertEqual(ov._recordado("edicion"), (False, None))
        # 2) LA RELECTURA ya no la ofrece. (Esto es lo que antes no pasaba nunca.)
        p = ov.obtener(o["id"], OPER)["permisos"]
        self.assertEqual((p["editar"], p["porque"]["editar"]), (False, self._FALTA_LA_0071))
        self.assertTrue(p["cancelar"] and p["entregar"], "lo demás no depende de la puerta")
        # 3) Otro intento ya ni llega a la base: lo frena el permiso, con el mismo texto.
        with self.assertNoLogs("omnicanal.ordenes_venta", level="WARNING"):
            for quien in (OPER, ADMIN):
                with self.assertRaises(ov.Conflicto) as e:
                    ov.guardar(o["id"], o["rev"], corregida, quien)
                self.assertEqual(str(e.exception), self._FALTA_LA_0071)
        self.intacta(o)
        self.assertEqual(self.saldo(a), (5, 3, 2))
        # Sin la puerta todo lo demás sigue como antes de la 0071: un borrador se
        # guarda y otra confirmada se entrega.
        d = self.crear([L(a, 1)])
        self.assertEqual(ov.guardar(d["id"], d["rev"], {"guia": "GUIA-PRUEBA-2"}, OPER)
                         ["orden"]["guia"], "GUIA-PRUEBA-2")
        self.assertEqual(ov.entregar(otra["id"], otra["rev"], OPER)["orden"]["estado"], "entregada")
        # 4) Se vuelve a correr la 0071 (sus dos guardias). El «no» también se
        # recuerda un minuto; vencido, la misma corrección entra.
        self.sql("\n".join(_guardias(M71).values()))
        self.assertEqual(self._texto_de_las_guardias(), como_estaban,
                         "la base quedó EXACTAMENTE como la dejó la migración")
        self.assertFalse(ov.obtener(o["id"], OPER)["permisos"]["editar"], "todavía recordado")
        ov._olvidar("edicion")                               # (= pasó el minuto)
        self.assertTrue(ov.obtener(o["id"], OPER)["permisos"]["editar"])
        ok = ov.guardar(o["id"], o["rev"], corregida, OPER)["orden"]
        self.assertEqual((ok["guia"], ok["piezas_apartadas"], self.saldo(a)),
                         ("GUIA-PRUEBA-1", 4, (4, 4, 0)))
        self.assertEqual(self.sql(self._CORRIO_LA_0071)[0]["n"], 2, "el registro ni se enteró")

    def test_un_tropiezo_al_preguntar_por_la_puerta_no_se_dice_como_que_falta(self):
        """La pregunta de verdad, tronando de verdad en Postgres (pasa por `_fila` →
        `_tx` → el reintento del pool: el camino de producción). No saber no es
        «falta»: la orden no ofrece editar —la duda cierra— y un PUT en esa ventana
        es un 409, pero lo que se le dice a la persona es que no se pudo comprobar
        y que lo intente de nuevo, no que busque una migración que sí está."""
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.confirmada([L(a, 2)])
        borrador = self.crear([L(a, 1)])
        sin_saber = ("No se pudo comprobar si esta base ya permite editar una orden confirmada "
                     "(la migración 0071); intenta de nuevo en unos segundos.")
        self.assertTrue(ov.obtener(o["id"], OPER)["permisos"]["editar"])
        ov._olvidar("edicion")                               # venció lo recordado: toca preguntar
        truena = "select 1 / (select count(*) from pg_class where false) as lista"
        # (El tropiezo se recuerda `_TTL_FALLO`, unos segundos. Aquí se alarga para
        # que la prueba no dependa de lo rápido que corra la máquina.)
        with mock.patch.object(ov, "_SQL_HAY_EDICION", truena), \
                mock.patch.object(ov, "_TTL_FALLO", 600.0):
            with self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
                p = ov.obtener(o["id"], OPER)["permisos"]
            self.assertEqual(len(logs.output), 1)
            self.assertIn("DivisionByZero", logs.output[0])
            self.assertIsNone(ov.edicion_lista(), "ni sí ni no: no se sabe")
        self.assertEqual(ov._recordado("edicion"), (True, None))
        self.assertEqual((p["editar"], p["porque"]["editar"]), (False, sin_saber))
        self.assertTrue(p["cancelar"] and p["entregar"] and p["mensajes"], "lo demás no se entera")
        # La base ya contesta, pero el tropiezo se recuerda unos segundos: un PUT en
        # esa ventana es un 409 con ESE texto, y no cambia nada.
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(o["id"], o["rev"], {"guia": "GUIA-PRUEBA-1"}, OPER)
        self.assertEqual((e.exception.status, str(e.exception)), (409, sin_saber))
        self.intacta(o)
        # Un BORRADOR ni pregunta por la puerta: se edita igual.
        self.assertTrue(ov.obtener(borrador["id"], OPER)["permisos"]["editar"])
        guardado = ov.guardar(borrador["id"], borrador["rev"], {"guia": "GUIA-PRUEBA-2"}, OPER)
        self.assertEqual(guardado["orden"]["guia"], "GUIA-PRUEBA-2")
        # Vencido el recuerdo del tropiezo, la misma corrección entra.
        ov._olvidar("edicion")
        ok = ov.guardar(o["id"], o["rev"], {"guia": "GUIA-PRUEBA-1"}, OPER)["orden"]
        self.assertEqual((ok["guia"], ok["permisos"]["editar"]), ("GUIA-PRUEBA-1", True))

    def test_la_puerta_no_se_queda_abierta_en_la_conexion_del_pool(self):
        """Regla 13: `app.ov_edicion` va como SET LOCAL en el mismo envío y muere con
        la transacción. Si se quedara pegada en una conexión del pool, la SIGUIENTE
        sentencia sobre esa orden —venga de quien venga— podría tocar su contenido
        sin abrir la puerta."""
        a = self.sku("A")
        self.siembra(a, 9)
        o = self.confirmada([L(a, 2)])
        for n in (3, 4, 5):                                  # varias, para pasear por el pool
            o = ov.guardar(o["id"], o["rev"], {"lineas": [L(a, n)]}, OPER)["orden"]
        vistos = {ov._fila("select current_setting('app.ov_edicion', true) as puerta")["puerta"]
                  for _ in range(12)}
        self.assertLessEqual(vistos, {None, ""}, "ninguna conexión conserva la puerta")
        sin_puerta = ov.SQL_EDITAR_CONFIRMADA.replace("set local app.ov_edicion = %(puerta)s;\n", "")
        for _ in range(8):
            with mock.patch.object(ov, "SQL_EDITAR_CONFIRMADA", sin_puerta), \
                    self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
                with self.assertRaises(ov.Conflicto) as e:
                    ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 6)]}, OPER)
            self.assertIn("0071", str(e.exception))
        self.assertEqual((self.saldo(a), ov.obtener(o["id"], OPER)["rev"]), ((9, 5, 4), o["rev"]))

    # ── la liga con la venta y la orden automática ───────────────────────────
    def test_la_liga_con_la_venta_se_revalida_igual_que_en_un_borrador(self):
        a = self.sku("A")
        suya, ajena, full = f"V1-{self.t}", f"V2-{self.t}", f"VF-{self.t}"
        self.venta_canal(suya, [(a, 2, 10, False)])                       # en UNA sola cuenta
        self.venta_canal(full, [(a, 1, 10, True)], canal="mercado_libre")
        self.siembra(a, 9)
        o = self.confirmada([L(a, 2)])
        otra = self.confirmada([L(a, 1)], mp_canal="tiktok", mp_cuenta=CUENTA, mp_orden=ajena)
        # Todo o nada, y una venta FULL no lleva orden propia: tampoco una confirmada.
        with self.assertRaises(ov.Invalido) as e:
            ov.guardar(o["id"], o["rev"], {"mp_orden": suya}, OPER)
        self.assertIn("los tres o ninguno", str(e.exception))
        with self.assertRaises(ov.Invalido) as e:
            ov.guardar(o["id"], o["rev"], {"mp_canal": "mercado_libre", "mp_cuenta": CUENTA,
                                           "mp_orden": full}, OPER)
        self.assertIn("FULL", str(e.exception))
        # A una venta que YA tiene orden: 409 con su folio. También si la revisión
        # previa no la ve (dos capturas a la vez) y quien la frena es el índice único.
        mp_ajena = {"mp_canal": "tiktok", "mp_cuenta": CUENTA, "mp_orden": ajena}
        for parche in (mock.patch.object(ov, "_venta_ligada", wraps=ov._venta_ligada),
                       mock.patch.object(ov, "_venta_ligada", side_effect=[None, otra])):
            with parche, self.assertRaises(ov.Conflicto) as e:
                ov.guardar(o["id"], o["rev"], mp_ajena, OPER)
            self.assertEqual((e.exception.status, str(e.exception)),
                             (409, f"Esa venta ya tiene la orden {otra['folio']}."))
        self.intacta(o)
        # La suya: la cuenta se completa sola, y las formas (canal en minúsculas).
        e = ov.guardar(o["id"], o["rev"], {"mp_canal": " TikTok ", "mp_orden": suya}, OPER)["orden"]
        self.assertEqual((e["mp_canal"], e["mp_cuenta"], e["mp_orden"], e["estado"]),
                         ("tiktok", CUENTA, suya, "confirmada"))
        m = self.editada(o["id"])
        self.assertEqual((m["cuerpo"], m["datos"]["cambios"]),
                         ("Orden editada · canal de la venta, orden de marketplace, cuenta",
                          {"mp_canal": [None, "tiktok"], "mp_cuenta": [None, CUENTA],
                           "mp_orden": [None, suya]}))
        # Ligada, el barrido de cancelaciones ya la ve como la orden de esa venta…
        self.assertEqual(ov.venta_marketplace(suya)["ventas"][0]["ov"],
                         {"id": o["id"], "folio": o["folio"], "estado": "confirmada"})
        # …y se desliga mandando los tres en nulo.
        suelta = ov.guardar(o["id"], e["rev"], dict.fromkeys(mp_ajena), OPER)["orden"]
        self.assertEqual((suelta["mp_canal"], suelta["mp_cuenta"], suelta["mp_orden"]),
                         (None, None, None))
        self.assertEqual(self.saldo(a), (9, 3, 6), "la liga no mueve el apartado")

    def test_la_orden_que_nacio_sola_no_cambia_de_cliente_ni_de_canal_de_venta(self):
        """`crear_auto` aparta en una bodega que SURTE VENTAS, y ENSAYO no lo hace:
        se enciende dentro de la transacción de la prueba (ROLLBACK), como CrearAuto."""
        a = self.sku("A")
        venta = {"canal": "tiktok", "cuenta": CUENTA, "orden": f"5780{self.t}", "total": 400,
                 "comision": 40, "guia": "GUIA-PRUEBA-7"}
        with self.conexion() as cn:
            cur = cn.cursor()
            cur.execute("""select set_config('app.usuario', 'pruebas@prueba.test', true);
                           update almacen.almacenes
                              set surte_ventas = true, preferencia = 9,
                                  motivo = 'Prueba: ENSAYO surte ventas (editar una automática)'
                            where codigo = 'ENSAYO'""")
            cur.execute("""insert into ops.automatizacion_flags (flag, valor, motivo, actualizado_por)
                           values ('ov_generacion_auto', true, 'Prueba de editar una automática',
                                   'pruebas@prueba.test')""")
            self.siembra(a, 5, cur=cur)
            r = ov.crear_auto(venta, [{"sku": a, "cantidad": 2, "precio_unitario": 200}], "ENSAYO",
                              cur=cur)
            self.assertEqual((r["resultado"], r["orden"]["estado"], r["orden"]["creado_via"],
                              r["orden"]["cliente"]), ("creada", "confirmada", "automatico", "tiktok"))
            o = ov.obtener(r["orden"]["id"], OPER, cur=cur)
            self.assertTrue(o["permisos"]["editar"], "una automática también se corrige")
            for datos in ({"cliente": "Cliente de mostrador"}, {"cliente": None},
                          {"mp_canal": "temu"}, {"cliente": "temu", "mp_canal": "temu"}):
                with self.assertRaises(ov.Invalido, msg=str(datos)) as e:
                    ov.guardar(o["id"], o["rev"], datos, ADMIN, cur=cur)
                self.assertEqual(str(e.exception), "Esta orden se creó sola a partir de su venta: su "
                                                   "cliente es el canal de esa venta, y ninguno de "
                                                   "los dos se cambia.")
            # Lo demás sí: la guía, y los renglones con su apartado (el eco del cliente
            # y del canal, que la pantalla manda siempre, no es cambiarlos).
            e = ov.guardar(o["id"], o["rev"], {"cliente": "tiktok", "mp_canal": "tiktok",
                                               "guia": "GUIA-PRUEBA-8", "total": None,
                                               "lineas": [L(a, 3, 200)]}, OPER, cur=cur)["orden"]
            self.assertEqual((e["guia"], e["total"], e["piezas_apartadas"], e["cliente"],
                              e["creado_via"], e["confirmada_por"]),
                             ("GUIA-PRUEBA-8", 600.0, 3, "tiktok", "automatico", "automatico"))
            cur.execute("select fisico, apartado, libre from almacen.stock_almacen "
                        "where sku = %s and almacen = 'ENSAYO'", (a,))
            self.assertEqual(tuple(cur.fetchone().values()), (5, 3, 2))
            # Y `crear_auto` de la MISMA venta la sigue viendo: ya existía, sin apartar otra vez.
            r2 = ov.crear_auto(venta, [{"sku": a, "cantidad": 2, "precio_unitario": 200}], "ENSAYO",
                               cur=cur)
            self.assertEqual((r2["resultado"], r2["orden"]["id"], r2["orden"]["piezas_apartadas"]),
                             ("ya_existia", o["id"], 3))
            cur.execute("set constraints all immediate")        # lo que revisaría el COMMIT

    # ── reintentos y concurrencia ────────────────────────────────────────────
    def test_el_reintento_que_repite_una_edicion_que_si_entro_es_el_exito_que_fue(self):
        """El COMMIT entró y la conexión murió al contestar: el pool repite el cuerpo
        y el CAS falla… contra MI propia edición. La marca `op` del mensaje `editada`
        lo distingue de «la movió otro»: se contesta el éxito, y se aplicó UNA vez."""
        a = self.sku("A")
        self.siembra(a, 9)
        o = self.confirmada([L(a, 2)])
        with mock.patch.object(ov.sdb, "reintentar_transitorio", lambda fn: (fn(), fn())[1]):
            r = ov.guardar(o["id"], o["rev"], {"guia": "GUIA-PRUEBA-1", "lineas": [L(a, 5)]}, OPER)
        self.assertEqual((r["ok"], r["mensaje"], r["orden"]["rev"], r["orden"]["guia"]),
                         (True, "Cambios guardados.", o["rev"] + 1, "GUIA-PRUEBA-1"))
        self.assertEqual((self.saldo(a), self.eventos(o["id"])),
                         ((9, 5, 4), ["creada", "confirmada", "editada"]), "apartó +3 UNA sola vez")
        # La marca es fontanería: no sale por la API.
        self.assertNotIn("op", self.editada(o["id"])["datos"])
        self.assertEqual(self.sql("select count(*) as n from ventas.ov_mensajes where orden_id = %s "
                                  "and evento = 'editada' and datos ? 'op'", (o["id"],))[0]["n"], 1)

    def test_dos_ediciones_a_la_vez_de_la_misma_orden_gana_una(self):
        a = self.sku("A")
        self.siembra(a, 30)
        for vuelta in range(4):
            o = self.confirmada([L(a, 2)])
            antes = self.saldo(a)
            r = self.a_la_vez(lambda: ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 5)]}, OPER),
                              lambda: ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 1)],
                                                                     "guia": "GUIA-PRUEBA-1"}, ADMIN))
            ganan = [x for x in r if isinstance(x, dict)]
            pierden = [x for x in r if isinstance(x, Exception)]
            self.assertEqual((len(ganan), len(pierden)), (1, 1), f"vuelta {vuelta}: {r}")
            self.assertIsInstance(pierden[0], ov.Conflicto)
            self.assertEqual(str(pierden[0]), "La orden cambió mientras tanto; se recargó.")
            fin = ov.obtener(o["id"], OPER)
            pidio = fin["lineas"][0]["cantidad"]
            self.assertIn((pidio, fin["guia"]), ((5, None), (1, "GUIA-PRUEBA-1")),
                          "quedó UNA de las dos, entera: nunca una mezcla")
            self.assertEqual((fin["rev"], fin["piezas_apartadas"], self.eventos(o["id"]).count("editada")),
                             (o["rev"] + 1, pidio, 1))
            self.assertEqual(self.saldo(a), (antes[0], antes[1] - 2 + pidio, antes[2] + 2 - pidio))

    def test_una_edicion_y_la_confirmacion_de_otra_orden_pelean_el_mismo_saldo(self):
        """Hay 3 piezas libres y dos que las quieren A LA VEZ: una edición que sube 3
        y la confirmación de OTRA orden que pide 3. Entra una; la otra recibe su
        «no alcanzó» con palabras. Pase lo que pase, ni una pieza apartada de más."""
        ganaron = set()
        for vuelta in range(6):
            a = self.sku(f"A{vuelta}")
            self.siembra(a, 5)
            o1 = self.confirmada([L(a, 2)])
            o2 = self.crear([L(a, 3)])
            r = self.a_la_vez(lambda: ov.guardar(o1["id"], o1["rev"], {"lineas": [L(a, 5)]}, OPER),
                              lambda: ov.confirmar(o2["id"], o2["rev"], ADMIN))
            self.assertEqual([isinstance(x, dict) for x in r].count(True), 1, f"vuelta {vuelta}: {r}")
            self.assertEqual(self.saldo(a), (5, 5, 0), "ni una pieza de más")
            e1, e2 = ov.obtener(o1["id"], OPER), ov.obtener(o2["id"], OPER)
            if isinstance(r[0], dict):                   # ganó la edición
                ganaron.add("edicion")
                self.assertEqual((e1["piezas_apartadas"], e2["estado"]), (5, "borrador"))
                self.assertIsInstance(r[1], ov.Conflicto)
                self.assertEqual(str(r[1]), f"No alcanzó el stock para apartar: {a} pide 3 y hay 0 "
                                            "libres en ENSAYO. No se apartó nada.")
            else:                                        # ganó la confirmación
                ganaron.add("confirmacion")
                self.assertEqual((e1["piezas_apartadas"], e1["rev"], e2["estado"]),
                                 (2, o1["rev"], "confirmada"))
                self.assertIsInstance(r[0], ov.Conflicto)
                self.assertEqual(str(r[0]), f"No alcanzó el stock para guardar el cambio: {a} "
                                            "necesita 3 más y hay 0 libres en ENSAYO. No se guardó "
                                            "nada.")
                self.assertNotIn("editada", self.eventos(o1["id"]))
        self.assertTrue(ganaron, "alguna de las dos tuvo que ganar")

    def test_la_edicion_espera_el_candado_del_saldo_y_luego_no_alcanza(self):
        """La misma pelea, a cámara lenta y en los DOS sentidos: quien llega segundo
        se queda esperando la fila de saldo —se ve en pg_stat_activity— y, cuando el
        primero confirma (COMMIT), decide con el saldo de AHORA, no con su foto."""
        for primero in ("confirmacion", "edicion"):
            with self.subTest(primero=primero):
                a = self.sku(f"A-{primero[:4].upper()}")
                self.siembra(a, 5)
                o1 = self.confirmada([L(a, 2)])
                o2 = self.crear([L(a, 3)])
                salida: dict = {}

                def editar(cur=None):
                    return ov.guardar(o1["id"], o1["rev"], {"lineas": [L(a, 5)]}, OPER, cur=cur)

                def confirmar(cur=None):
                    return ov.confirmar(o2["id"], o2["rev"], ADMIN, cur=cur)

                # Quién espera se reconoce por el ARRANQUE de su sentencia: pg_stat_activity
                # recorta el texto a 1024 bytes, y en la de editar el saldo queda más
                # allá (antes van los catorce campos del encabezado). Las dos órdenes
                # son distintas, así que lo único que comparten —y por lo que una puede
                # esperar a la otra— es la fila de saldo.
                gana, espera_, la_que_espera = (
                    (confirmar, editar, "%%set local app.ov_edicion%%") if primero == "confirmacion"
                    else (editar, confirmar, "%%almacen.stock_almacen%%"))

                def segundo() -> None:
                    try:
                        salida["r"] = espera_()
                    except Exception as exc:  # noqa: BLE001
                        salida["r"] = exc

                with self.conexion() as cn:
                    cur = cn.cursor()
                    self.assertTrue(gana(cur)["ok"])
                    self.assertEqual(self.saldo(a), (5, 2, 3), "fuera de su transacción aún no se ve")
                    hilo = threading.Thread(target=segundo)
                    hilo.start()
                    espero = False
                    for _ in range(100):
                        time.sleep(0.05)
                        espero = self.sql(f"""select count(*) as n from pg_stat_activity
                                               where datname = current_database()
                                                 and wait_event_type = 'Lock'
                                                 and query like '{la_que_espera}'""")[0]["n"] >= 1
                        if espero:
                            break
                    cn.commit()                          # el primero gana
                    hilo.join(30)
                self.assertTrue(espero, "el segundo nunca esperó el candado del saldo")
                self.assertIsInstance(salida["r"], ov.Conflicto, repr(salida["r"]))
                self.assertIn("y hay 0 libres en ENSAYO", str(salida["r"]))
                self.assertEqual(self.saldo(a), (5, 5, 0))
                e1, e2 = ov.obtener(o1["id"], OPER), ov.obtener(o2["id"], OPER)
                self.assertEqual((e1["piezas_apartadas"], e2["estado"]),
                                 (2, "confirmada") if primero == "confirmacion" else (5, "borrador"))

    def test_el_canal_cancela_mientras_se_confirma_una_edicion(self):
        """La carrera que trajo la 0071, a cámara lenta: una persona edita y no ha
        hecho COMMIT; el barrido lee la orden (con los renglones y la `rev` de
        ANTES), decide cancelar y se queda esperando la fila. Al confirmar la
        edición la orden sigue `confirmada` y sin marca, pero ya no tiene la `rev`
        que el canal leyó: su guardia no encuentra la fila (KB001). Eso NO es un bug
        ni un 409: es «se movió», se relee y se cancela lo que HAY.

        (Cuando la guardia iba sólo por estado, la sentencia pasaba con los
        renglones de la foto vieja y quien la frenaba era la base —el apartado no
        cuadraría, o el renglón que la edición quitó ya no está—; y si la edición
        sólo tocaba el encabezado, ni eso. Aquí se fija que TODAS las variantes
        salen por la `rev`, sin llegar a esa red.)"""
        variantes = (("baja la cantidad", [("A", 5)], [("A", 2)]),
                     ("sube la cantidad", [("A", 2)], [("A", 5)]),
                     ("quita un renglón", [("A", 2), ("B", 2)], [("A", 2)]),
                     ("agrega un renglón", [("A", 2)], [("A", 2), ("B", 2)]),
                     ("cambia de SKU", [("A", 2)], [("C", 2)]),
                     ("sólo el encabezado", [("A", 2)], None))
        for otra_aparta in (False, True):
            for nombre, antes, despues in variantes:
                with self.subTest(nombre, otra_aparta=otra_aparta):
                    sku = {k: self.sku(f"{k}{uuid.uuid4().hex[:4]}") for k in "ABC"}
                    for s in sku.values():
                        self.siembra(s, 20)
                    if otra_aparta:                     # otra orden aparta los mismos SKU
                        self.confirmada([L(s, 3) for s in sku.values()])
                    base = 3 if otra_aparta else 0
                    o = self.confirmada([L(sku[k], n) for k, n in antes], mp_canal="tiktok",
                                        mp_cuenta=CUENTA, mp_orden=f"V-{uuid.uuid4().hex[:10]}")
                    cuerpo = ({"guia": "GUIA-PRUEBA-1"} if despues is None
                              else {"lineas": [L(sku[k], n) for k, n in despues]})
                    salida: dict = {}

                    def canal() -> None:
                        try:
                            salida["r"] = ov.canal_cancelo(o["id"], "CANCELLED",
                                                           "El comprador canceló en el canal", False)
                        except Exception as exc:  # noqa: BLE001
                            salida["r"] = exc

                    with self.assertNoLogs("omnicanal.ordenes_venta", level="ERROR"), \
                            self.rechazos_del_canal() as rechazos, self.conexion() as cn:
                        cur = cn.cursor()
                        e = ov.guardar(o["id"], o["rev"], cuerpo, OPER, cur=cur)["orden"]
                        self.assertEqual(e["rev"], o["rev"] + 1)
                        hilo = threading.Thread(target=canal)
                        hilo.start()
                        espero = False
                        for _ in range(100):
                            time.sleep(0.05)
                            espero = self.sql(
                                """select count(*) as n from pg_stat_activity
                                    where datname = current_database() and wait_event_type = 'Lock'
                                      and query like '%%update ventas.ov_ordenes%%'""")[0]["n"] >= 1
                            if espero:
                                break
                        cn.commit()                      # la edición gana
                        hilo.join(30)
                    self.assertTrue(espero, "el barrido nunca esperó la fila de la orden")
                    self.assertIsInstance(salida["r"], dict, f"salió {salida['r']!r}")
                    self.assertEqual(rechazos, [("negocio", "ov_no_cancelable_o_cambio_rev")],
                                     "UN rechazo, y por la `rev`: no por la foto vieja")
                    c = salida["r"]["orden"]
                    self.assertEqual((salida["r"]["resultado"], c["estado"], c["cancelada_origen"],
                                      c["piezas_apartadas"], c["rev"]),
                                     ("cancelada", "cancelada", "marketplace", 0, o["rev"] + 2))
                    self.assertEqual([self.saldo(s) for s in sku.values()],
                                     [(20, base, 20 - base)] * 3, "soltó EXACTO lo que había")
                    self.assertEqual(self.eventos(o["id"])[-2:], ["editada", "cancelada"])
                    # Y la bitácora dice lo que se soltó DESPUÉS de la edición, no antes.
                    quedo = ov.obtener(o["id"], OPER)["lineas"]
                    dicho = ov.mensajes(o["id"])["mensajes"][-1]
                    self.assertEqual([(l["sku"], l["cantidad"]) for l in dicho["datos"]["lineas"]],
                                     [(l["sku"], l["cantidad"]) for l in quedo])
                    self.assertEqual(sum(l["reservado"] for l in dicho["datos"]["lineas"]),
                                     e["piezas_apartadas"])


class Alta(Base):
    def test_un_total_automatico_que_no_cabe_es_un_400_no_un_502(self):
        """Dos renglones válidos cuya SUMA no cabe en numeric(14,2): antes era un
        22003 de la base, o sea un 502 «quedó registrado» y un log.error de bug."""
        a, b = self.sku("A"), self.sku("B")
        antes = self.folio_contador()
        absurdos = [L(a, 100000, 9999999.99), L(b, 100000, 9999999.99)]
        with self.assertNoLogs("omnicanal.ordenes_venta", level="ERROR"):
            with self.assertRaises(ov.Invalido) as e:
                ov.crear_borrador({"lineas": absurdos}, OPER, self.clave())
            self.assertIn("El total de los renglones pasa de 999,999,999,999.99", str(e.exception))
            o = self.crear([L(a, 1)])
            with self.assertRaises(ov.Invalido):
                ov.guardar(o["id"], o["rev"], {"total": None, "lineas": absurdos}, OPER)
        fin = ov.obtener(o["id"], OPER)
        self.assertEqual((fin["rev"], fin["total"], self.folio_contador()), (o["rev"], 10.0, antes + 1))
        # Con el total escrito a mano sí se guarda: el tope es del total, no de la captura.
        g = ov.guardar(o["id"], o["rev"], {"total": 500, "lineas": absurdos}, OPER)["orden"]
        self.assertEqual((g["total"], g["renglones"]), (500.0, 2))

    def test_es_idempotente_por_clave_y_no_deja_huecos_de_folio(self):
        a = self.sku("A")
        antes = self.folio_contador()
        clave = self.clave("doble")
        r1 = ov.crear_borrador({"lineas": [L(a, 1)]}, OPER, clave)
        r2 = ov.crear_borrador({"lineas": [L(a, 1)]}, OPER, clave)
        self.assertEqual(r2["orden"]["id"], r1["orden"]["id"])
        self.assertEqual(r2["mensaje"], f"La orden {r1['orden']['folio']} ya estaba creada.")
        self.assertEqual(self.folio_contador(), antes + 1)
        self.assertEqual(r2["orden"]["n_mensajes"], 1, "la segunda no escribió nada")
        # Sin clave no hay cómo saber que es la misma: son dos.
        s1 = ov.crear_borrador({"lineas": []}, OPER)["orden"]
        s2 = ov.crear_borrador({"lineas": []}, OPER)["orden"]
        self.assertEqual((s2["id"] != s1["id"], self.folio_contador()), (True, antes + 3))
        # Las claves de las órdenes automáticas están reservadas, y la clave tiene tope.
        for mala in ("mp:tiktok:CUENTAPRUEBA:1", "FULL:1:amazon:ENSAYO", "x" * 81):
            with self.assertRaises(ov.Invalido):
                ov.crear_borrador({"lineas": []}, OPER, mala)
        self.assertEqual(self.folio_contador(), antes + 3)

    def test_dos_altas_a_la_vez_con_la_misma_clave_son_una_orden(self):
        a = self.sku("A")
        antes = self.folio_contador()
        clave = self.clave("carrera")
        r = self.a_la_vez(*[lambda: ov.crear_borrador({"lineas": [L(a, 1)]}, OPER, clave)] * 4)
        self.assertEqual([x for x in r if isinstance(x, Exception)], [])
        self.assertEqual(len({x["orden"]["id"] for x in r}), 1)
        self.assertEqual(self.folio_contador(), antes + 1, "un solo folio")
        self.assertEqual(self.sql("select count(*) as n from ventas.ov_ordenes where clave = %s",
                                  (clave,))[0]["n"], 1)

    def test_un_alta_que_la_base_rechaza_no_consume_folio_y_no_sale_textual(self):
        """Un CHECK que Python debió validar es un bug nuestro: 502 genérico, el
        detalle (con su código y su regla) al log, y el folio intacto."""
        antes = self.folio_contador()
        with mock.patch.object(ov, "CANALES", (*ov.CANALES, "facebook")):
            with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
                with self.assertRaises(ov.ErrorOV) as e:
                    ov.crear_borrador({"canal": "facebook", "lineas": []}, OPER, self.clave())
        self.assertEqual((type(e.exception), e.exception.status), (ov.ErrorOV, 502))
        self.assertEqual(str(e.exception),
                         "No se pudo completar la operación; quedó registrado para revisarlo.")
        registro = "\n".join(logs.output)
        self.assertIn("pgcode=23514", registro)
        self.assertIn("regla=ov_ordenes_canal_chk", registro)
        self.assertEqual(self.folio_contador(), antes, "la sentencia entera se deshizo")

    def test_el_reintento_transitorio_que_repite_un_alta_devuelve_la_misma(self):
        """El COMMIT entró y la conexión murió al contestar: el pool vuelve a correr
        el cuerpo. La segunda vuelta choca con su propia clave → se relee."""
        a = self.sku("A")
        antes = self.folio_contador()
        with mock.patch.object(ov.sdb, "reintentar_transitorio", lambda fn: (fn(), fn())[1]):
            r = ov.crear_borrador({"lineas": [L(a, 2)]}, OPER, self.clave("eco"))
        self.assertTrue(r["ok"])
        self.assertEqual((self.folio_contador(), r["orden"]["n_mensajes"]), (antes + 1, 1))


class VentaDeMarketplace(Base):
    def test_todo_o_nada_y_sus_formas(self):
        a = self.sku("A")
        orden = f"V-{self.t}"
        for parcial in ({"mp_orden": orden}, {"mp_canal": "tiktok"}, {"mp_cuenta": CUENTA},
                        {"mp_canal": "tiktok", "mp_cuenta": CUENTA},
                        {"mp_cuenta": CUENTA, "mp_orden": orden}):
            with self.assertRaises(ov.Invalido, msg=str(parcial)) as e:
                ov.crear_borrador({**parcial, "lineas": [L(a, 1)]}, OPER, self.clave())
            self.assertIn("los tres o ninguno", str(e.exception))
        # Canal y orden, sin cuenta, de una venta que no está en channel.orders: falta la cuenta.
        with self.assertRaises(ov.Invalido) as e:
            ov.crear_borrador({"mp_canal": "tiktok", "mp_orden": orden, "lineas": []}, OPER,
                              self.clave())
        self.assertIn("Falta la cuenta", str(e.exception))
        # Formas: canal en minúsculas, cuenta en MAYÚSCULAS (lo exige la base).
        o = self.crear([L(a, 1)], mp_canal=" TikTok ", mp_cuenta="cuentaprueba", mp_orden=orden)
        self.assertEqual((o["mp_canal"], o["mp_cuenta"], o["mp_orden"]), ("tiktok", CUENTA, orden))
        self.assertIn(f"venta tiktok {orden}", ov.mensajes(o["id"])["mensajes"][0]["cuerpo"])

    def test_la_cuenta_se_completa_sola_si_la_venta_esta_en_una_sola(self):
        a = self.sku("A")
        una, dos = f"V1-{self.t}", f"V2-{self.t}"
        self.venta_canal(una, [(a, 1, 100, False)])
        self.venta_canal(dos, [(a, 1, 100, False)])
        self.venta_canal(dos, [(a, 1, 100, False)], cuenta="OTRACUENTAPRUEBA")
        o = self.crear([L(a, 1)], mp_canal="tiktok", mp_orden=una)
        self.assertEqual(o["mp_cuenta"], CUENTA)
        with self.assertRaises(ov.Invalido) as e:       # en dos cuentas no se adivina
            ov.crear_borrador({"mp_canal": "tiktok", "mp_orden": dos, "lineas": []}, OPER,
                              self.clave())
        self.assertIn("más de una cuenta", str(e.exception))

    def test_una_venta_full_no_lleva_orden_propia(self):
        a = self.sku("A")
        orden = f"VF-{self.t}"
        self.venta_canal(orden, [(a, 1, 100, False), (a, 1, 100, True)], canal="mercado_libre")
        antes = self.folio_contador()
        with self.assertRaises(ov.Invalido) as e:
            ov.crear_borrador({"mp_canal": "mercado_libre", "mp_cuenta": CUENTA, "mp_orden": orden,
                               "lineas": [L(a, 1)]}, OPER, self.clave())
        self.assertIn("FULL", str(e.exception))
        self.assertEqual(self.folio_contador(), antes)
        # Tampoco al ligar un borrador que ya existía.
        o = self.crear([L(a, 1)])
        with self.assertRaises(ov.Invalido):
            ov.guardar(o["id"], o["rev"], {"mp_canal": "mercado_libre", "mp_cuenta": CUENTA,
                                           "mp_orden": orden}, OPER)

    def test_la_misma_venta_dos_veces_dice_el_folio_de_la_que_ya_la_tiene(self):
        a = self.sku("A")
        orden = f"V-{self.t}"
        mp = {"mp_canal": "tiktok", "mp_cuenta": CUENTA, "mp_orden": orden}
        o1 = self.crear([L(a, 1)], **mp)
        antes = self.folio_contador()
        with self.assertRaises(ov.Conflicto) as e:
            ov.crear_borrador({**mp, "lineas": [L(a, 1)]}, OPER, self.clave("otra"))
        self.assertEqual((e.exception.status, str(e.exception)),
                         (409, f"Esa venta ya tiene la orden {o1['folio']}."))
        # Aunque la revisión previa no la vea (dos capturas a la vez), la base sí: 23505
        # de ov_ordenes_mp_uq, que con OTRA clave es la misma venta capturada dos veces.
        with mock.patch.object(ov, "_venta_ligada", side_effect=[None, o1]):
            with self.assertRaises(ov.Conflicto) as e:
                ov.crear_borrador({**mp, "lineas": [L(a, 1)]}, OPER, self.clave("carrera"))
        self.assertIn(o1["folio"], str(e.exception))
        self.assertEqual(self.folio_contador(), antes)
        # Ligar OTRO borrador a esa venta: lo mismo.
        o2 = self.crear([L(a, 1)])
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(o2["id"], o2["rev"], mp, OPER)
        self.assertIn(o1["folio"], str(e.exception))
        # Una CANCELADA suelta su venta: ya se puede.
        ov.cancelar(o1["id"], o1["rev"], OPER)
        ligada = ov.guardar(o2["id"], o2["rev"], mp, OPER)["orden"]
        self.assertEqual((ligada["mp_orden"], ligada["mp_cuenta"]), (orden, CUENTA))
        # …y se puede desligar mandando los tres en nulo.
        suelta = ov.guardar(o2["id"], ligada["rev"], dict.fromkeys(mp), OPER)["orden"]
        self.assertEqual((suelta["mp_canal"], suelta["mp_cuenta"], suelta["mp_orden"]),
                         (None, None, None))

    def test_la_venta_por_su_numero_y_las_pendientes(self):
        a, b = self.sku("A"), self.sku("B")
        viva, full, cancelada = f"VV-{self.t}", f"VF-{self.t}", f"VC-{self.t}"
        self.venta_canal(viva, [(a, 2, 150, False), (a, 1, 75, False), (None, 1, 5, False)],
                         total=375, comision=37.5)
        self.venta_canal(full, [(b, 1, 10, True)])
        self.venta_canal(cancelada, [(b, 1, 10, False)], estado_canal="CANCELLED")
        self.sql("""insert into ops.odoo_sale_orders (canal, cuenta, external_order_id, accion, guia,
                           paqueteria) values ('tiktok', %s, %s, 'creada', 'GUIA-PRUEBA-9',
                           'Paquetería de prueba')""", (CUENTA, viva))
        r = ov.venta_marketplace(viva)
        self.assertTrue(r["ok"])
        v = r["ventas"][0]
        self.assertEqual((v["canal"], v["cuenta"], v["orden"], v["total"], v["comision"], v["neto"]),
                         ("tiktok", CUENTA, viva, 375.0, 37.5, 337.5))
        self.assertEqual((v["guia"], v["paqueteria"], v["piezas"], v["renglones_sin_sku"], v["ov"],
                          v["cancelada"], v["es_fulfillment"]),
                         ("GUIA-PRUEBA-9", "Paquetería de prueba", 4, 1, None, False, False))
        # El mismo SKU a dos precios: un renglón con el promedio ponderado (el dinero se conserva).
        self.assertEqual([(x["sku"], x["cantidad"], x["precio_unitario"]) for x in v["lineas"]],
                         [(a, 3, 125.0)])
        self.assertTrue(ov.venta_marketplace(full)["ventas"][0]["es_fulfillment"])
        self.assertTrue(ov.venta_marketplace(cancelada)["ventas"][0]["cancelada"])
        self.assertEqual(ov.venta_marketplace(f"NO-{self.t}")["ventas"], [])
        self.assertFalse(ov.venta_marketplace("  ")["ok"])
        pend = {x["orden"] for x in ov.ventas_pendientes(dias=1)["ventas"]}
        self.assertTrue(viva in pend and full not in pend and cancelada not in pend)
        # Con orden propia ya no es pendiente, y la venta dice cuál la tiene.
        o = self.crear(v["lineas"], mp_canal="tiktok", mp_cuenta=CUENTA, mp_orden=viva)
        self.assertNotIn(viva, {x["orden"] for x in ov.ventas_pendientes(dias=1)["ventas"]})
        self.assertEqual(ov.venta_marketplace(viva, "tiktok")["ventas"][0]["ov"],
                         {"id": o["id"], "folio": o["folio"], "estado": "borrador"})


class ElCanalCancela(Base):
    def confirmada_de_venta(self, n: int = 2, fisico: int = 5) -> tuple[dict, str]:
        a = self.sku(f"A{uuid.uuid4().hex[:4]}")
        self.siembra(a, fisico)
        o = self.confirmada([L(a, n)], mp_canal="tiktok", mp_cuenta=CUENTA,
                            mp_orden=f"V-{uuid.uuid4().hex[:10]}")
        return o, a

    def test_confirmada_sin_salir_se_cancela_y_repetida_no_hace_nada(self):
        o, a = self.confirmada_de_venta()
        self.assertEqual(self.saldo(a), (5, 2, 3))
        r = ov.canal_cancelo(o["id"], "CANCELLED", "El comprador canceló en el canal", False)
        c = r["orden"]
        self.assertEqual(set(r), {"resultado", "orden"})
        self.assertEqual((r["resultado"], c["estado"], c["cancelada_origen"], c["cancelada_por"],
                          c["cancelada_nombre"], c["cancelada_motivo"], c["canal_cancelo_at"]),
                         ("cancelada", "cancelada", "marketplace", "automatico", "Automático",
                          "El comprador canceló en el canal", None))
        self.assertEqual(self.saldo(a), (5, 0, 5))
        m = ov.mensajes(o["id"])["mensajes"][-1]
        self.assertEqual((m["evento"], m["autor"], m["via"]), ("cancelada", "automatico", "automatico"))
        self.assertIn("Cancelada por el marketplace", m["cuerpo"])
        # El sondeo y el webhook REPITEN: ver la misma cancelación otra vez es éxito.
        r2 = ov.canal_cancelo(o["id"], "CANCELLED", "El comprador canceló en el canal", False)
        self.assertEqual((r2["resultado"], r2["orden"]["rev"], r2["orden"]["n_mensajes"]),
                         ("ya_cancelada", c["rev"], c["n_mensajes"]))
        self.assertEqual(self.saldo(a), (5, 0, 5))

    def test_en_camino_se_marca_y_bodega_contesta_que_si_salio(self):
        o, a = self.confirmada_de_venta()
        r = ov.canal_cancelo(o["id"], "IN_TRANSIT", "", True)
        m = r["orden"]
        self.assertEqual((r["resultado"], m["estado"], m["canal_cancelo_ref"], m["rev"]),
                         ("marcada", "confirmada", "IN_TRANSIT", o["rev"] + 1))
        self.assertIsNotNone(m["canal_cancelo_at"])
        self.assertEqual(self.saldo(a), (5, 2, 3), "la marca NO suelta el apartado")
        self.assertEqual(self.eventos(o["id"])[-1], "canal_cancelo")
        # Con la marca ya no se puede marcar DELIVERED: primero se contesta «¿salió?».
        p = ov.obtener(o["id"], OPER)["permisos"]
        self.assertTrue(p["responder_salio"])
        self.assertFalse(p["entregar"])
        self.assertIn("contestar si salió", p["porque"]["entregar"])
        with self.assertRaises(ov.Invalido):
            ov.entregar(o["id"], m["rev"], OPER)
        # La misma cancelación, vista otra vez (y aunque ahora no diga «en camino»): nada.
        for en_camino in (True, False):
            r2 = ov.canal_cancelo(o["id"], "IN_TRANSIT", "", en_camino)
            self.assertEqual((r2["resultado"], r2["orden"]["rev"], r2["orden"]["estado"]),
                             ("ya_marcada", m["rev"], "confirmada"))
        self.assertEqual(self.eventos(o["id"]).count("canal_cancelo"), 1)
        for mal in ("sí", 1, None):
            with self.assertRaises(ov.Invalido):
                ov.responder_salio(o["id"], m["rev"], OPER, mal)
        with self.assertRaises(ov.SinPermiso):
            ov.responder_salio(o["id"], m["rev"], LECT, True)

        s = ov.responder_salio(o["id"], m["rev"], OPER, True)
        f = s["orden"]
        self.assertIn("sí salió", s["mensaje"])
        self.assertEqual((f["estado"], f["devolucion_estado"], f["piezas_entregadas"],
                          f["piezas_apartadas"]), ("entregada_cancelada", "pendiente", 2, 0))
        self.assertEqual((f["entregada_por"], f["cancelada_por"], f["cancelada_origen"]),
                         ("oper@prueba.test", "automatico", "marketplace"))
        self.assertIn("IN_TRANSIT", f["cancelada_motivo"])
        self.assertEqual(self.saldo(a), (3, 0, 3), "salió: baja el físico y el apartado")
        linea = f["lineas"][0]
        self.assertEqual((linea["entregado"], linea["reservado"]), (2, 0))
        self.assertEqual(self.sql("select clave, delta, ref from almacen.stock_mov where ov_linea_id = %s",
                                  (linea["id"],)),
                         [{"clave": f"ov:{o['id']}:linea:{linea['id']}:salida", "delta": -2,
                           "ref": o["folio"]}], "la misma clave que entregar")
        self.assertEqual(self.eventos(o["id"])[-1], "devolucion_esperada")
        with self.assertRaises(ov.Invalido):
            ov.responder_salio(o["id"], f["rev"], OPER, True)        # ya se contestó
        self.assertEqual(ov.canal_cancelo(o["id"], "IN_TRANSIT", "", True)["resultado"],
                         "ya_cancelada")

    def test_en_camino_y_bodega_contesta_que_no_salio(self):
        o, a = self.confirmada_de_venta()
        m = ov.canal_cancelo(o["id"], "AWAITING_COLLECTION", "", True)["orden"]
        r = ov.responder_salio(o["id"], m["rev"], OPER, False)
        c = r["orden"]
        self.assertEqual((c["estado"], c["cancelada_origen"], c["cancelada_por"],
                          c["devolucion_estado"], c["canal_cancelo_ref"]),
                         ("cancelada", "marketplace", "oper@prueba.test", None,
                          "AWAITING_COLLECTION"))
        self.assertIn("NO salió", c["cancelada_motivo"])
        self.assertEqual(self.saldo(a), (5, 0, 5), "no salió: se suelta el apartado")
        self.assertEqual(self.libro(a), [("correccion", 5, 5)])
        self.assertEqual(self.eventos(o["id"])[-2:], ["canal_cancelo", "cancelada"])

    def test_ya_entregada_queda_entregada_y_cancelada_con_devolucion_pendiente(self):
        o, a = self.confirmada_de_venta()
        e = ov.entregar(o["id"], o["rev"], OPER)["orden"]
        r = ov.canal_cancelo(o["id"], "CANCELLED", "Reembolso en el canal", False)
        c = r["orden"]
        self.assertEqual((r["resultado"], c["estado"], c["devolucion_estado"], c["cancelada_origen"],
                          c["cancelada_por"], c["entregada_at"], c["entregada_por"]),
                         ("entregada_cancelada", "entregada_cancelada", "pendiente", "marketplace",
                          "automatico", e["entregada_at"], "oper@prueba.test"))
        self.assertEqual(self.saldo(a), (3, 0, 3), "no hay renglones que tocar")
        m = ov.mensajes(o["id"])["mensajes"][-1]
        self.assertEqual((m["evento"], m["autor"]), ("devolucion_esperada", "automatico"))
        r2 = ov.canal_cancelo(o["id"], "CANCELLED", "Reembolso en el canal", True)
        self.assertEqual((r2["resultado"], r2["orden"]["rev"]), ("ya_cancelada", c["rev"]))

    def test_confirmada_con_una_entrega_parcial_tambien_espera_devolucion(self):
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 3)
        self.siembra(b, 3)
        o = self.confirmada([L(a, 1), L(b, 2)])
        o = ov.entregar(o["id"], o["rev"], OPER, [{"id": o["lineas"][0]["id"], "n": 1}])["orden"]
        r = ov.canal_cancelo(o["id"], "CANCELLED", "", False)
        self.assertEqual((r["resultado"], r["orden"]["estado"], r["orden"]["devolucion_estado"]),
                         ("entregada_cancelada", "entregada_cancelada", "pendiente"))
        self.assertEqual(r["orden"]["cancelada_motivo"], "Cancelada en el canal (CANCELLED)")
        self.assertEqual((self.saldo(a), self.saldo(b)), ((2, 0, 2), (3, 0, 3)))

    def test_las_sentencias_aguantan_ver_la_misma_cancelacion_dos_veces(self):
        """La guarda de verdad está en el SQL (la de Python sólo ahorra el viaje):
        dos pasadas del sondeo pueden leer lo mismo a la vez. La segunda tiene que
        dar un KB001 CON NOMBRE —que se relee— y no un 42501 «la cancelación del
        canal ya está anotada», que se leería como bug en cada repetición.

        Desde que la guardia de una confirmada lleva también la `rev` de la lectura,
        la segunda pasada —que leyó la MISMA rev— ya no encuentra la fila por eso.
        Pero los filtros de estado siguen ahí y siguen bastando solos: cada caso se
        prueba con la rev que se leyó Y con la de ahora."""
        firma = ov._firma(ov.AUTOMATICO)
        o, a = self.confirmada_de_venta()
        marca = {**firma, "id": o["id"], "rev": o["rev"], "ref_canal": "IN_TRANSIT", "datos": "{}",
                 "cuerpo": "El canal canceló con el paquete en camino: ¿salió?"}
        cancela = {**firma, "id": o["id"], "rev": o["rev"], "origen": "marketplace", "datos": "{}",
                   "motivo": "Cancelada en el canal", "cuerpo": "Cancelada por el marketplace"}
        no_esta = ("negocio", "ov_no_cancelable_o_cambio_rev")

        def rechazo(sql: str, params: dict) -> tuple:
            with self.assertRaises(ov._Rechazo) as e:
                ov._transicion("canal_cancelo", sql, params)
            return e.exception.clase, e.exception.detalle

        ov._transicion("canal_cancelo", ov.SQL_CANAL_MARCA, marca)
        ahora = ov.obtener(o["id"], OPER)["rev"]
        self.assertEqual(ahora, o["rev"] + 1)
        for rev in (o["rev"], ahora):       # la otra pasada leyó lo mismo… o releyó sin mirar
            self.assertEqual(rechazo(ov.SQL_CANAL_MARCA, {**marca, "rev": rev}),
                             ("negocio", "canal_cancelo_no_aplica"))
            # Con la marca puesta el sondeo ya no cancela solo: decide Bodega.
            self.assertEqual(rechazo(ov.SQL_CANCELAR_CANAL, {**cancela, "rev": rev}), no_esta)
        self.assertEqual(self.saldo(a), (5, 2, 3))
        # Una entregada: la primera pasada la deja entregada_cancelada; la segunda, KB001.
        # (Ésta va sólo por estado: una entregada ya no cambia, no hay `rev` que mandar.)
        e, _ = self.confirmada_de_venta()
        ov.entregar(e["id"], e["rev"], OPER)
        entregada = {**firma, "id": e["id"], "motivo": "Reembolso en el canal", "datos": "{}",
                     "cuerpo": "El canal canceló una venta ya entregada"}
        ov._transicion("canal_cancelo", ov.SQL_CANAL_CANCELO_ENTREGADA, entregada)
        self.assertEqual(rechazo(ov.SQL_CANAL_CANCELO_ENTREGADA, entregada),
                         ("negocio", "canal_cancelo_entregada_no_aplica"))
        # Y una borrada no se toca (sin el filtro sería un 42501 de «está borrada»),
        # traiga la rev que traiga.
        b, _ = self.confirmada_de_venta()
        borrada = ov.borrar(b["id"], b["rev"], ADMIN, "Capturada por error")["orden"]
        for rev in (b["rev"], borrada["rev"]):
            for sql, params in ((ov.SQL_CANAL_MARCA, {**marca, "id": b["id"], "rev": rev}),
                                (ov.SQL_CANCELAR_CANAL, {**cancela, "id": b["id"], "rev": rev}),
                                (ov.SQL_CANAL_CANCELO_ENTREGADA, {**entregada, "id": b["id"]})):
                self.assertEqual(rechazo(sql, params)[0], "negocio")

    def test_la_rev_de_la_lectura_frena_al_canal_si_la_confirmada_cambio(self):
        """Las tres sentencias del canal sobre una CONFIRMADA, de una en una y contra
        la base: la orden sigue confirmada y sin marca —o sea que «por estado»
        pasaría—, pero ya no es la que se leyó (alguien la editó: desde la 0071 se
        puede). Con la rev de la lectura no encuentran la fila y no se mueve nada;
        con la de ahora, entran. Es la guardia que `canal_cancelo` usa para que
        cada reintento decida con lo que HAY."""
        firma = ov._firma(ov.AUTOMATICO)
        cuadra = {**firma, "datos": "{}", "origen": "marketplace", "ref_canal": "IN_TRANSIT",
                  "motivo": "Cancelada en el canal", "cuerpo": "Cancelada por el marketplace"}

        def rechazo(sql: str, orden: dict, rev: int) -> tuple:
            with self.assertRaises(ov._Rechazo) as e:
                ov._transicion("canal_cancelo", sql, {**cuadra, "id": orden["id"], "rev": rev})
            return e.exception.clase, e.exception.detalle

        def entra(sql: str, orden: dict, rev: int) -> dict:
            ov._transicion("canal_cancelo", sql, {**cuadra, "id": orden["id"], "rev": rev})
            return ov.obtener(orden["id"], OPER)

        def editada(orden: dict) -> dict:
            """La misma orden después de que una persona le corrige la guía."""
            e = ov.guardar(orden["id"], orden["rev"], {"guia": "GUIA-PRUEBA-1"}, OPER)["orden"]
            self.assertEqual((e["estado"], e["canal_cancelo_at"], e["rev"]),
                             ("confirmada", None, orden["rev"] + 1), "por estado, sigue igual")
            return e

        # 1) Cancelar sin salidas.
        leida, a = self.confirmada_de_venta()
        ya = editada(leida)
        self.assertEqual(rechazo(ov.SQL_CANCELAR_CANAL, leida, leida["rev"]),
                         ("negocio", "ov_no_cancelable_o_cambio_rev"))
        self.assertEqual((ov.obtener(leida["id"], OPER)["rev"], self.saldo(a)),
                         (ya["rev"], (5, 2, 3)), "ni la rev ni el apartado se movieron")
        c = entra(ov.SQL_CANCELAR_CANAL, leida, ya["rev"])
        self.assertEqual((c["estado"], self.saldo(a)), ("cancelada", (5, 0, 5)))
        # 2) La marca del «¿salió?».
        leida, a = self.confirmada_de_venta()
        ya = editada(leida)
        self.assertEqual(rechazo(ov.SQL_CANAL_MARCA, leida, leida["rev"]),
                         ("negocio", "canal_cancelo_no_aplica"))
        self.assertIsNone(ov.obtener(leida["id"], OPER)["canal_cancelo_at"])
        m = entra(ov.SQL_CANAL_MARCA, leida, ya["rev"])
        self.assertEqual((m["estado"], m["canal_cancelo_ref"], self.saldo(a)),
                         ("confirmada", "IN_TRANSIT", (5, 2, 3)))
        # 3) Cancelar con piezas ya afuera (tras una entrega parcial).
        a, b = self.sku(f"A{uuid.uuid4().hex[:4]}"), self.sku(f"B{uuid.uuid4().hex[:4]}")
        self.siembra(a, 5)
        self.siembra(b, 5)
        leida = self.confirmada([L(a, 2), L(b, 1)])
        leida = ov.entregar(leida["id"], leida["rev"], OPER,
                            [{"id": leida["lineas"][0]["id"], "n": 2}])["orden"]
        ya = editada(leida)
        self.assertEqual(rechazo(ov.SQL_CANCELAR_CON_SALIDA_CANAL, leida, leida["rev"]),
                         ("negocio", "ov_no_cancelable_o_cambio_rev"))
        self.assertEqual((ov.obtener(leida["id"], OPER)["estado"], self.saldo(b)),
                         ("confirmada", (5, 1, 4)))
        f = entra(ov.SQL_CANCELAR_CON_SALIDA_CANAL, leida, ya["rev"])
        self.assertEqual((f["estado"], f["devolucion_estado"], self.saldo(a), self.saldo(b)),
                         ("entregada_cancelada", "pendiente", (3, 0, 3), (5, 0, 5)))

    def test_el_mensaje_de_la_cancelacion_dice_lo_que_de_verdad_se_solto(self):
        """`canal_cancelo` lee la orden, arma con ESA lectura el cuerpo y los
        renglones del mensaje, y manda una sentencia que relee los renglones por su
        cuenta. Si una edición confirma en ese hueco (son milisegundos, sin espera
        de candado), con la guardia sólo por estado la sentencia pasaba, soltaba
        bien… y la bitácora decía otra cantidad y otros renglones: «se soltaron 5
        piezas» habiendo soltado 2. Con la `rev` de la lectura en la guardia esa
        sentencia no entra; entra la del reintento, armada con lo que hay."""
        casos = (("baja A de 5 a 2", lambda A, C: [L(A, 2)], "2 piezas"),
                 ("sube A a 9 y agrega C×4", lambda A, C: [L(A, 9), L(C, 4)], "13 piezas"),
                 ("cambia A por C×3", lambda A, C: [L(C, 3)], "3 piezas"))
        for nombre, despues, soltadas in casos:
            with self.subTest(nombre):
                A, C = self.sku(f"A{uuid.uuid4().hex[:4]}"), self.sku(f"C{uuid.uuid4().hex[:4]}")
                self.siembra(A, 20)
                self.siembra(C, 20)
                o = self.confirmada([L(A, 5)], mp_canal="tiktok", mp_cuenta=CUENTA,
                                    mp_orden=f"V-{uuid.uuid4().hex[:10]}")
                real = ov._params_cancelar
                ediciones: list[dict] = []

                def la_editan_justo_despues_de_la_lectura(*a, **k):
                    # `_params_cancelar` corre entre la lectura de `canal_cancelo` y su
                    # sentencia: aquí entra (y confirma) la edición de la persona.
                    if not ediciones:
                        ediciones.append(ov.guardar(o["id"], o["rev"],
                                                    {"lineas": despues(A, C)}, OPER)["orden"])
                    return real(*a, **k)

                with mock.patch.object(ov, "_params_cancelar",
                                       la_editan_justo_despues_de_la_lectura), \
                        self.assertNoLogs("omnicanal.ordenes_venta", level="ERROR"):
                    r = ov.canal_cancelo(o["id"], "CANCELLED", "El comprador canceló en el canal",
                                         False)
                e, = ediciones
                self.assertEqual((r["resultado"], r["orden"]["estado"], r["orden"]["rev"]),
                                 ("cancelada", "cancelada", o["rev"] + 2))
                self.assertEqual((self.saldo(A), self.saldo(C)), ((20, 0, 20), (20, 0, 20)))
                self.assertEqual(self.eventos(o["id"])[-2:], ["editada", "cancelada"])
                m = ov.mensajes(o["id"])["mensajes"][-1]
                self.assertEqual(m["cuerpo"], f"Cancelada por el marketplace · se soltaron {soltadas}"
                                              " · Motivo: El comprador canceló en el canal")
                self.assertEqual([(l["sku"], l["cantidad"], l["reservado"])
                                  for l in m["datos"]["lineas"]],
                                 [(l["sku"], l["cantidad"], l["reservado"]) for l in e["lineas"]],
                                 "los renglones que dejó la edición, no los de antes")
                self.assertEqual(sum(l["reservado"] for l in m["datos"]["lineas"]),
                                 e["piezas_apartadas"])

    def test_si_la_orden_ya_no_es_de_esa_venta_el_canal_no_la_toca(self):
        """`venta=` es la liga con la que quien llama decidió que el canal canceló.
        Si la orden ya no es de esa venta —alguien corrigió su liga (0071)—, no se
        cancela ni se marca: `nada`. También cuando la corrección entra en el hueco
        más angosto, ENTRE la relectura de `canal_cancelo` y su sentencia: ahí la
        frena la `rev` de la guardia (KB001), se relee, y ya no es de esa venta."""
        motivo = "Venta cancelada en TikTok"
        # Sin carrera: con SU liga se cancela; con la de otra venta, no.
        o, a = self.confirmada_de_venta()
        suya = (o["mp_canal"], o["mp_cuenta"], o["mp_orden"])
        for ajena in (("tiktok", CUENTA, "V-OTRA"), ("tiktok", "OTRACUENTAPRUEBA", o["mp_orden"]),
                      ("temu", CUENTA, o["mp_orden"]), (None, None, None)):
            for en_camino in (False, True):
                r = ov.canal_cancelo(o["id"], "CANCELLED", motivo, en_camino, venta=ajena)
                c = r["orden"]
                self.assertEqual((r["resultado"], c["estado"], c["rev"], c["canal_cancelo_at"]),
                                 ("nada", "confirmada", o["rev"], None))
        self.assertEqual((self.saldo(a), self.eventos(o["id"])),
                         ((5, 2, 3), ["creada", "confirmada"]))
        r = ov.canal_cancelo(o["id"], "CANCELLED", motivo, False, venta=suya)
        self.assertEqual((r["resultado"], self.saldo(a)), ("cancelada", (5, 0, 5)))
        # Con carrera: la persona corrige la liga cuando `canal_cancelo` YA releyó la
        # orden (y la vio suya) y va a mandar su sentencia.
        for en_camino, por_la_rev in ((False, "ov_no_cancelable_o_cambio_rev"),
                                      (True, "canal_cancelo_no_aplica")):
            with self.subTest(en_camino=en_camino):
                o, a = self.confirmada_de_venta()
                suya = (o["mp_canal"], o["mp_cuenta"], o["mp_orden"])
                otra = f"V-{uuid.uuid4().hex[:10]}"
                corregidas: list[dict] = []
                with self.assertNoLogs("omnicanal.ordenes_venta", level="ERROR"), \
                        self.rechazos_del_canal() as rechazos:
                    apuntada = ov._transicion

                    def la_corrigen_justo_antes_de_la_sentencia(operacion, sql, params, cur=None):
                        if operacion == "canal_cancelo" and not corregidas:
                            corregidas.append(ov.guardar(o["id"], o["rev"], {"mp_orden": otra},
                                                         OPER)["orden"])
                        return apuntada(operacion, sql, params, cur)

                    with mock.patch.object(ov, "_transicion",
                                           la_corrigen_justo_antes_de_la_sentencia):
                        r = ov.canal_cancelo(o["id"], "IN_TRANSIT" if en_camino else "CANCELLED",
                                             motivo, en_camino, venta=suya)
                e, = corregidas
                self.assertEqual(rechazos, [("negocio", por_la_rev)],
                                 "la sentencia armada con la lectura vieja no entró")
                c = r["orden"]
                self.assertEqual((r["resultado"], c["estado"], c["canal_cancelo_at"], c["rev"],
                                  c["mp_orden"], c["piezas_apartadas"]),
                                 ("nada", "confirmada", None, e["rev"], otra, 2))
                self.assertEqual((self.saldo(a), self.eventos(o["id"])[-1]), ((5, 2, 3), "editada"))
                p = ov.obtener(o["id"], OPER)["permisos"]
                self.assertTrue(p["editar"] and p["entregar"] and p["cancelar"],
                                "la orden de la otra venta sigue viva y en manos de la gente")

    def test_el_canal_cancela_mientras_se_confirma_una_entrega_parcial(self):
        """La carrera, a cámara lenta: una persona entrega UN renglón y no ha hecho
        COMMIT; el barrido lee la orden (aún sin salidas), elige cancelar «sin
        salidas» y se queda esperando la fila. Al confirmar la entrega la orden
        sigue `confirmada`, pero ya no tiene la `rev` que el canal leyó: su guardia
        no encuentra la fila (KB001). Eso NO es un bug ni un 409: es «se movió», se
        relee y sale por la sentencia que sí toca.

        (Con la guardia sólo por estado el CAS pasaba, con los renglones de la foto
        vieja, y quien lo frenaba era la base: 23514 del saldo si nadie más aparta
        ese SKU, 42501 del renglón si otra orden lo aparta. Las dos variantes
        siguen aquí; lo que se fija es que ya salen por la `rev`.)"""
        for otra_aparta in (False, True):
            with self.subTest(otra_aparta=otra_aparta):
                a, b = self.sku(f"A{uuid.uuid4().hex[:4]}"), self.sku(f"B{uuid.uuid4().hex[:4]}")
                self.siembra(a, 5)
                self.siembra(b, 5)
                o = self.confirmada([L(a, 2), L(b, 1)], mp_canal="tiktok", mp_cuenta=CUENTA,
                                    mp_orden=f"V-{uuid.uuid4().hex[:10]}")
                if otra_aparta:
                    self.confirmada([L(a, 2)])
                de_a = next(l for l in o["lineas"] if l["sku"] == a)
                salida: dict = {}

                def canal() -> None:
                    try:
                        salida["r"] = ov.canal_cancelo(o["id"], "CANCELLED",
                                                       "El comprador canceló en el canal", False)
                    except Exception as exc:  # noqa: BLE001
                        salida["r"] = exc

                with self.assertNoLogs("omnicanal.ordenes_venta", level="ERROR"), \
                        self.rechazos_del_canal() as rechazos, self.conexion() as cn:
                    cur = cn.cursor()
                    p = ov.entregar(o["id"], o["rev"], OPER, [{"id": de_a["id"], "n": 2}], cur=cur)
                    self.assertEqual(p["orden"]["estado"], "confirmada", "parcial: sigue confirmada")
                    hilo = threading.Thread(target=canal)
                    hilo.start()
                    espera = False
                    for _ in range(100):
                        time.sleep(0.05)
                        espera = self.sql("""select count(*) as n from pg_stat_activity
                                              where datname = current_database()
                                                and wait_event_type = 'Lock'
                                                and query like '%%update ventas.ov_ordenes%%'""")[0]["n"] >= 1
                        if espera:
                            break
                    cn.commit()                    # la entrega parcial gana
                    hilo.join(30)
                self.assertTrue(espera, "el barrido nunca esperó la fila de la orden")
                self.assertIsInstance(salida["r"], dict, f"salió {salida['r']!r}")
                self.assertEqual(rechazos, [("negocio", "ov_no_cancelable_o_cambio_rev")],
                                 "UN rechazo, y por la `rev`: no por la foto vieja")
                c = salida["r"]["orden"]
                self.assertEqual((salida["r"]["resultado"], c["estado"], c["cancelada_origen"],
                                  c["piezas_entregadas"], c["piezas_apartadas"],
                                  c["devolucion_estado"]),
                                 ("entregada_cancelada", "entregada_cancelada", "marketplace",
                                  2, 0, "pendiente"))
                self.assertEqual((self.saldo(a), self.saldo(b)),
                                 ((3, 2 if otra_aparta else 0, 1 if otra_aparta else 3), (5, 0, 5)))

    def test_borrada_cancelada_o_en_borrador_no_hace_nada(self):
        borrador = self.crear([L(self.sku("A"), 1)])
        o, a = self.confirmada_de_venta()
        cancelada = ov.cancelar(o["id"], o["rev"], ADMIN, "Cancelada a mano antes")["orden"]
        o2, b = self.confirmada_de_venta()
        borrada = ov.borrar(o2["id"], o2["rev"], ADMIN, "Capturada por error")["orden"]
        for orden, espera in ((borrador, "nada"), (cancelada, "ya_cancelada"), (borrada, "nada")):
            for en_camino in (False, True):
                r = ov.canal_cancelo(orden["id"], "CANCELLED", "El canal canceló", en_camino)
                self.assertEqual(r["resultado"], espera)
                self.assertEqual((r["orden"]["rev"], r["orden"]["estado"], r["orden"]["n_mensajes"],
                                  r["orden"]["canal_cancelo_at"]),
                                 (orden["rev"], orden["estado"], orden["n_mensajes"], None))
        self.assertEqual((self.saldo(a), self.saldo(b)), ((5, 0, 5), (5, 0, 5)))
        with self.assertRaises(ov.NoExiste):
            ov.canal_cancelo(99999999, "CANCELLED", "", False)


class SalioTarde(Base):
    def test_una_cancelada_que_si_habia_salido(self):
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.confirmada([L(a, 2)])
        c = ov.cancelar(o["id"], o["rev"], ADMIN, "El canal canceló en AWAITING_SHIPMENT")["orden"]
        self.assertEqual(self.saldo(a), (5, 0, 5))
        with self.assertRaises(ov.SinPermiso):
            ov.salio_tarde(o["id"], c["rev"], OPER)
        r = ov.salio_tarde(o["id"], c["rev"], ADMIN)
        f = r["orden"]
        self.assertIn("Salida registrada", r["mensaje"])
        self.assertEqual((f["estado"], f["devolucion_estado"], f["cancelada_motivo"],
                          f["entregada_por"], f["piezas_entregadas"]),
                         ("entregada_cancelada", "pendiente", "El canal canceló en AWAITING_SHIPMENT",
                          "admin@prueba.test", 2))
        self.assertEqual(self.saldo(a), (3, 0, 3))
        self.assertEqual(self.libro(a), [("correccion", 5, 5), ("salida_ov", -2, 3)])
        self.assertEqual(self.eventos(o["id"])[-1], "devolucion_esperada")
        with self.assertRaises(ov.Invalido):
            ov.salio_tarde(o["id"], f["rev"], ADMIN)

    def test_un_borrador_cancelado_nunca_salio(self):
        o = self.crear([L(self.sku("A"), 1)])
        c = ov.cancelar(o["id"], o["rev"], OPER)["orden"]
        with self.assertRaises(ov.Invalido) as e:
            ov.salio_tarde(o["id"], c["rev"], ADMIN)
        self.assertIn("nunca estuvo confirmada", str(e.exception))
        # Y si Python no lo frenara, la base sí (23514 ov_ordenes_conf_chk): mismo 400.
        self.siembra(self.sku("A"), 1)
        with mock.patch.object(ov, "_motivo", return_value=None):
            with self.assertRaises(ov.Invalido) as e:
                ov.salio_tarde(o["id"], c["rev"], ADMIN)
        self.assertIn("nunca estuvo confirmada", str(e.exception))
        self.assertEqual(self.saldo(self.sku("A")), (1, 0, 1))

    def test_si_la_venta_ya_tiene_otra_orden_viva_no_es_ya_existia(self):
        """Una cancelada SUELTA su venta; si después se capturó otra orden para esa
        venta, sacar la primera de `cancelada` choca (23505). Aquí ese 23505 NO es
        «ya existía»: la salida no se escribió. Se avisa y se resuelve a mano."""
        a = self.sku("A")
        self.siembra(a, 5)
        mp = {"mp_canal": "tiktok", "mp_cuenta": CUENTA, "mp_orden": f"V-{self.t}"}
        o1 = self.confirmada([L(a, 2)], **mp)
        c = ov.cancelar(o1["id"], o1["rev"], ADMIN, "Cancelada por una persona")["orden"]
        o2 = self.crear([L(a, 2)], **mp)
        with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
            with self.assertRaises(ov.Conflicto) as e:
                ov.salio_tarde(o1["id"], c["rev"], ADMIN)
        self.assertIn(o2["folio"], str(e.exception))
        self.assertIn("la salida NO se registró", str(e.exception))
        self.assertIn(o2["folio"], "\n".join(logs.output))
        self.assertEqual((self.saldo(a), ov.obtener(o1["id"], ADMIN)["estado"]),
                         ((5, 0, 5), "cancelada"))
        # Resuelto a mano (se cancela la nueva), ya entra.
        ov.cancelar(o2["id"], o2["rev"], OPER)
        self.assertEqual(ov.salio_tarde(o1["id"], c["rev"], ADMIN)["orden"]["estado"],
                         "entregada_cancelada")


class CrearAuto(Base):
    """`crear_auto` aparta en una bodega que SURTE VENTAS, y ENSAYO no lo hace.
    Encenderla es un cambio a `almacen.almacenes` (con motivo y quién), que NO se
    confirma: va DENTRO de la transacción de la prueba y el ROLLBACK lo deshace,
    como hace el verificador. Por eso esta clase usa `cur=` (guía §7.3)."""

    def venta(self, orden: str) -> dict:
        return {"canal": "TikTok", "cuenta": "cuentaprueba", "orden": orden, "total": 730,
                "comision": 73.5, "moneda": "mxn", "fecha": "2026-10-06T09:30:00-06:00",
                "descripcion": "Venta de prueba", "guia": "GUIA-PRUEBA-7",
                "paqueteria": "Paquetería de prueba",
                "entrega_limite": "2026-10-08T23:59:00-06:00"}

    def encender(self, cur) -> None:
        # El set_config va en el MISMO execute que el UPDATE (guía §4.4).
        cur.execute("""select set_config('app.usuario', 'pruebas@prueba.test', true);
                       update almacen.almacenes
                          set surte_ventas = true, preferencia = 9,
                              motivo = 'Prueba: ENSAYO surte ventas (crear_auto)'
                        where codigo = 'ENSAYO'""")
        cur.execute("""insert into ops.automatizacion_flags (flag, valor, motivo, actualizado_por)
                       values ('ov_generacion_auto', true, 'Prueba de crear_auto',
                               'pruebas@prueba.test')""")

    def test_creada_ya_existia_y_no_alcanzo_con_todo_su_contenido(self):
        a, b, c = self.sku("A"), self.sku("B"), self.sku("C")
        orden = f"5770{self.t}"
        lineas = [{"sku": a, "cantidad": 2, "precio_unitario": 200, "titulo": "Audífonos",
                   "imagen": "https://img.prueba.test/a.jpg"},
                  {"sku": b, "cantidad": 1, "precio_unitario": 130, "titulo": "Bocina"},
                  {"sku": a.lower(), "cantidad": 1, "precio_unitario": 100}]   # el mismo SKU, 2.ª pieza
        antes = self.folio_contador()
        with self.conexion() as cn:
            cur = cn.cursor()
            self.siembra(a, 5, cur=cur)
            self.siembra(b, 1, cur=cur)
            self.siembra(c, 1, cur=cur)
            # La bandera apagada (sin fila): ni lo intenta.
            with self.assertRaises(ov.Apagado) as e:
                ov.crear_auto(self.venta(orden), lineas, "ENSAYO", cur=cur)
            self.assertIn("ov_generacion_auto", str(e.exception))
            cur.execute("""insert into ops.automatizacion_flags (flag, valor, motivo)
                           values ('ov_generacion_auto', true, 'Prueba de crear_auto')""")
            # ENSAYO no surte ventas: se vuelve a comprobar DENTRO del candado (C11).
            r = ov.crear_auto(self.venta(orden), lineas, "ENSAYO", cur=cur)
            self.assertEqual((r["resultado"], r["orden"]), ("no_alcanzo", None))
            self.assertIn("no surte ventas", r["mensaje"])
            cur.execute("delete from ops.automatizacion_flags where flag = 'ov_generacion_auto'")
            self.encender(cur)

            r = ov.crear_auto(self.venta(orden), lineas, "ENSAYO", cur=cur)
            self.assertEqual(set(r), {"resultado", "orden", "mensaje"})
            o = r["orden"]
            self.assertEqual((r["resultado"], o["estado"], o["folio"]),
                             ("creada", "confirmada", f"OV-{antes + 1:05d}"))
            # TODO el contenido va en el INSERT: fuera de borrador ya no se puede anotar.
            self.assertEqual((o["cliente"], o["canal"], o["mp_canal"], o["mp_cuenta"], o["mp_orden"]),
                             ("tiktok", "tiktok", "tiktok", CUENTA, orden))
            self.assertEqual((o["total"], o["comision"], o["neto"], o["moneda"], o["precio_origen"]),
                             (730.0, 73.5, 656.5, "MXN", "marketplace"))
            self.assertEqual((o["descripcion"], o["guia"], o["paqueteria"]),
                             ("Venta de prueba", "GUIA-PRUEBA-7", "Paquetería de prueba"))
            self.assertEqual(datetime.fromisoformat(o["fecha_venta"]),
                             datetime.fromisoformat("2026-10-06T09:30:00-06:00"))
            self.assertIsNotNone(o["entrega_limite"])
            self.assertEqual((o["creado_por"], o["creado_nombre"], o["creado_via"],
                              o["confirmada_por"]),
                             ("automatico", "Automático", "automatico", "automatico"))
            # El SKU repetido quedó en UN renglón, con el precio promedio (500 / 3).
            self.assertEqual([(l["sku"], l["cantidad"], l["reservado"], l["precio_unitario"],
                               l["titulo"], l["imagen"], l["almacen"]) for l in o["lineas"]],
                             [(a, 3, 3, 166.67, "Audífonos", "https://img.prueba.test/a.jpg", "ENSAYO"),
                              (b, 1, 1, 130.0, "Bocina", None, "ENSAYO")])
            cur.execute("select clave from ventas.ov_ordenes where id = %s", (o["id"],))
            self.assertEqual(cur.fetchone()["clave"], f"mp:tiktok:{CUENTA}:{orden}")
            m = ov.mensajes(o["id"], cur=cur)["mensajes"]
            self.assertEqual([(x["evento"], x["autor"], x["via"]) for x in m],
                             [("creada", "automatico", "automatico")])
            self.assertEqual((self.saldo_en(cur, a), self.saldo_en(cur, b)), ((5, 3, 2), (1, 1, 0)))

            # La MISMA venta otra vez (el sondeo repite): ya existía, sin apartar de nuevo.
            r2 = ov.crear_auto(self.venta(orden), lineas, "ENSAYO", cur=cur)
            self.assertEqual((r2["resultado"], r2["orden"]["id"]), ("ya_existia", o["id"]))
            self.assertEqual((self.saldo_en(cur, a), self.saldo_en(cur, b)), ((5, 3, 2), (1, 1, 0)))

            # OTRA venta que no alcanza: no crea nada, no consume folio y dice qué SKU.
            otra = f"5771{self.t}"
            r3 = ov.crear_auto(self.venta(otra), [{"sku": c, "cantidad": 1, "precio_unitario": 5},
                                                  {"sku": b, "cantidad": 1, "precio_unitario": 5}],
                               "ENSAYO", cur=cur)
            self.assertEqual((r3["resultado"], r3["orden"]), ("no_alcanzo", None))
            self.assertIn(f"{b} pide 1 y hay 0 libres en ENSAYO", r3["mensaje"])
            self.assertEqual(self.saldo_en(cur, c), (1, 0, 1), "todo o nada")
            cur.execute("select ultimo from ventas.ov_folio")
            self.assertEqual(cur.fetchone()["ultimo"], antes + 1)
            # Lo que al COMMIT revisaría la base, revisado aquí: nada pendiente truena.
            cur.execute("set constraints all immediate")
        # El ROLLBACK lo deshizo todo: ni la orden, ni el saldo, ni el encendido de ENSAYO.
        self.assertEqual(self.folio_contador(), antes)
        self.assertEqual((self.saldo(a), self.saldo(b)), (None, None))
        self.assertEqual(self.sql("select surte_ventas, preferencia from almacen.almacenes "
                                  "where codigo = 'ENSAYO'"), [{"surte_ventas": False,
                                                                "preferencia": None}])

    def saldo_en(self, cur, sku: str) -> tuple | None:
        cur.execute("select fisico, apartado, libre from almacen.stock_almacen "
                    "where sku = %s and almacen = 'ENSAYO'", (sku,))
        f = cur.fetchone()
        return (f["fisico"], f["apartado"], f["libre"]) if f else None

    def test_un_borrador_de_esa_venta_no_es_ya_existia(self):
        """Una persona capturó la venta y NO la confirmó: para la base es una
        orden viva, pero no aparta nada. `ya_existia` es éxito —quien llama da la
        venta por cubierta y compra la guía—, así que el borrador sale con su
        propio resultado, y ni se toca ni se aparta."""
        a = self.sku("A")
        orden = f"5772{self.t}"
        lineas = [{"sku": a, "cantidad": 2, "precio_unitario": 200}]
        with self.conexion() as cn:
            cur = cn.cursor()
            self.encender(cur)
            self.siembra(a, 20, cur=cur)
            b = ov.crear_borrador({"mp_canal": "tiktok", "mp_cuenta": CUENTA, "mp_orden": orden,
                                   "lineas": [L(a, 2, 200)]}, OPER, self.clave(), cur=cur)["orden"]
            r = ov.crear_auto(self.venta(orden), lineas, "ENSAYO", cur=cur)
            self.assertEqual((r["resultado"], r["orden"]["id"], r["orden"]["estado"],
                              r["orden"]["piezas_apartadas"], r["orden"]["rev"]),
                             ("borrador_previo", b["id"], "borrador", 0, b["rev"]))
            self.assertEqual(r["mensaje"], f"Esa venta ya tiene el borrador {b['folio']}, sin "
                                           "confirmar: no hay nada apartado.")
            self.assertEqual(self.saldo_en(cur, a), (20, 0, 20))
            # Confirmado por la persona, ya es lo que `ya_existia` promete: apartada.
            ov.confirmar(b["id"], b["rev"], OPER, cur=cur)
            r2 = ov.crear_auto(self.venta(orden), lineas, "ENSAYO", cur=cur)
            self.assertEqual((r2["resultado"], r2["orden"]["id"], r2["orden"]["piezas_apartadas"]),
                             ("ya_existia", b["id"], 2))
            self.assertEqual(self.saldo_en(cur, a), (20, 2, 18))
            # Y el total automático de una venta absurda no llega a la base.
            with self.assertRaises(ov.Invalido) as e:
                ov.crear_auto({"canal": "tiktok", "cuenta": CUENTA, "orden": f"5773{self.t}"},
                              [{"sku": a, "cantidad": 100000, "precio_unitario": 9999999.99},
                               {"sku": self.sku("B"), "cantidad": 100000,
                                "precio_unitario": 9999999.99}], "ENSAYO", cur=cur)
            self.assertIn("El total de los renglones pasa de", str(e.exception))

    def test_lo_que_no_es_una_venta_completa_no_llega_a_la_base(self):
        with self.conexion() as cn:
            cur = cn.cursor()
            self.encender(cur)
            una = [{"sku": self.sku("A"), "cantidad": 1, "precio_unitario": 1}]
            for venta, lineas, bodega in (
                    ({"canal": "tiktok", "orden": "1"}, una, "ENSAYO"),          # sin cuenta
                    (self.venta("1"), [], "ENSAYO"),                              # sin renglones
                    (self.venta("1"), una, ""),                                   # sin bodega
                    (self.venta("1"), [{"sku": "", "cantidad": 1}], "ENSAYO"),
                    ("no es una venta", una, "ENSAYO")):
                with self.assertRaises(ov.Invalido):
                    ov.crear_auto(venta, lineas, bodega, cur=cur)


class Permisos(Base):
    def test_quien_no_escribe_no_mueve_nada(self):
        a = self.sku("A")
        o = self.confirmada([L(a, 1)])
        d = self.crear([L(a, 1)])
        antes = self.folio_contador()
        for quien in (LECT, NADIE):
            for fn in (lambda: ov.crear_borrador({"lineas": []}, quien, self.clave()),
                       lambda: ov.guardar(d["id"], d["rev"], {"descripcion": "x"}, quien),
                       lambda: ov.guardar(o["id"], o["rev"], {"guia": "GUIA-PRUEBA-1"}, quien),
                       lambda: ov.confirmar(d["id"], d["rev"], quien),
                       lambda: ov.cancelar(d["id"], d["rev"], quien),
                       lambda: ov.cancelar(o["id"], o["rev"], quien, "Motivo válido"),
                       lambda: ov.entregar(o["id"], o["rev"], quien),
                       lambda: ov.borrar(o["id"], o["rev"], quien, "Motivo suficientemente largo"),
                       lambda: ov.salio_tarde(o["id"], o["rev"], quien),
                       lambda: ov.enviar_mensaje(o["id"], quien, "hola"),
                       lambda: ov.subir_archivo(o["id"], quien, "comprobante", "c.pdf", PDF),
                       lambda: ov.bajar_archivo(o["id"], 1, quien),
                       lambda: ov.borrar_archivo(o["id"], 1, quien)):
                with self.assertRaises(ov.SinPermiso) as e:
                    fn()
                self.assertEqual(e.exception.status, 403)
        self.assertEqual(self.folio_contador(), antes)
        # El rol se dice AUNQUE la rev sea vieja (es lo que no cambia recargando).
        for fn in (lambda: ov.borrar(o["id"], 1, OPER, "Motivo suficientemente largo"),
                   lambda: ov.cancelar(o["id"], 1, LECT, "Motivo válido"),
                   lambda: ov.guardar(o["id"], 1, {"guia": "GUIA-PRUEBA-1"}, LECT)):
            with self.assertRaises(ov.SinPermiso):
                fn()
        tras = ov.obtener(o["id"], OPER)
        self.assertEqual((tras["estado"], tras["rev"], tras["guia"], self.saldo(a)),
                         ("confirmada", o["rev"], None, (1, 1, 0)), "nadie movió la confirmada")
        # Leer sí pueden, y ven por qué no pueden lo demás.
        p = ov.obtener(o["id"], LECT)["permisos"]
        self.assertEqual([k for k, v in p.items() if v is True], [])
        self.assertEqual(p["porque"]["entregar"], "Tu rol es de sólo lectura")
        self.assertEqual((p["porque"]["editar"], p["porque"]["cancelar"]),
                         ("Tu rol es de sólo lectura", "Tu rol es de sólo lectura"))
        self.assertEqual(ov.estado_modulo(LECT)["yo"],
                         {"actor": "lectura@prueba.test", "nombre": "Leo Lectura", "rol": "lectura",
                          "via": "panel", "admin": False, "escribe": False})

    def test_la_bandera_apagada_deja_solo_borradores(self):
        a = self.sku("A")
        self.siembra(a, 5)
        confirmada = self.confirmada([L(a, 2)])
        self.bandera(ov.BANDERA_MODULO, False)
        self.assertFalse(ov.habilitado())
        # Crear, guardar, chatear y cancelar un BORRADOR: sí.
        o = self.crear([L(a, 1)])
        o = ov.guardar(o["id"], o["rev"], {"descripcion": "en modo prueba"}, OPER)["orden"]
        ov.enviar_mensaje(o["id"], OPER, "¿ya se puede confirmar?")
        self.assertFalse(o["permisos"]["confirmar"])
        self.assertIn("Modo prueba", o["permisos"]["porque"]["confirmar"])
        # Confirmar, entregar y EDITAR UNA CONFIRMADA (que puede apartar más): no
        # (409 de modo prueba), y no se tocó el saldo.
        for fn in (lambda: ov.confirmar(o["id"], o["rev"], OPER),
                   lambda: ov.entregar(confirmada["id"], confirmada["rev"], OPER),
                   lambda: ov.guardar(confirmada["id"], confirmada["rev"],
                                      {"guia": "GUIA-PRUEBA-1"}, OPER),
                   lambda: ov.guardar(confirmada["id"], confirmada["rev"],
                                      {"lineas": [L(a, 4)]}, ADMIN)):
            with self.assertRaises(ov.Apagado) as e:
                fn()
            self.assertEqual(e.exception.status, 409)
            self.assertIn("modo prueba", str(e.exception))
        self.assertEqual(self.saldo(a), (5, 2, 3))
        p = ov.obtener(confirmada["id"], OPER)["permisos"]
        self.assertFalse(p["editar"] or p["entregar"])
        self.assertEqual(p["porque"]["editar"], "Modo prueba: editar una confirmada está apagado "
                                                "(bandera «ordenes_venta»)")
        self.assertTrue(p["cancelar"])
        # Soltar SIEMPRE se puede: es lo que hace seguro apagar el módulo.
        ov.cancelar(o["id"], o["rev"], OPER)
        ov.cancelar(confirmada["id"], confirmada["rev"], OPER, "Se apagó el módulo")
        self.assertEqual(self.saldo(a), (5, 0, 5))
        est = ov.estado_modulo(OPER)
        self.assertEqual((est["habilitado"], est["banderas"]["ordenes_venta"]["encendido"],
                          est["banderas"]["ordenes_venta"]["persistido"]), (False, False, True))

    def test_sin_fila_manda_el_respaldo_y_sin_poder_leer_esta_apagada(self):
        self.sql("delete from ops.automatizacion_flags where flag = %s", (ov.BANDERA_MODULO,))
        ov._olvidar_cache()
        self.assertFalse(ov.habilitado(), "sin fila y con la variable en false")
        self.assertEqual(ov.estado_bandera(ov.BANDERA_MODULO),
                         {"encendido": False, "persistido": False, "actualizado_por": None,
                          "motivo": None, "actualizado_at": None})
        with mock.patch.object(settings, "ordenes_venta_enabled", True):
            self.assertTrue(ov.habilitado(refrescar=True), "sin fila manda la variable de respaldo")
            # …pero si la fila no se puede LEER, apagada, diga lo que diga la variable.
            with mock.patch.object(ov, "_leer_bandera", side_effect=RuntimeError("sin tabla")):
                with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
                    self.assertFalse(ov.habilitado(refrescar=True))
        # La fila manda sobre la variable, y se lee tal cual quedó escrita.
        self.bandera(ov.BANDERA_MODULO, True)
        b = ov.estado_bandera(ov.BANDERA_MODULO)
        self.assertEqual((b["encendido"], b["persistido"], b["actualizado_por"], b["motivo"]),
                         (True, True, "pruebas@prueba.test", "Prueba de integración"))
        self.assertIsNotNone(datetime.fromisoformat(b["actualizado_at"]).tzinfo)
        # Un apagado tarda a lo más lo que dura el caché, y sin reiniciar nada.
        self.sql("update ops.automatizacion_flags set valor = false where flag = %s",
                 (ov.BANDERA_MODULO,))
        self.assertTrue(ov.habilitado(), "todavía en caché")
        self.assertFalse(ov.habilitado(refrescar=True))


class SinMigracion(Base):
    def test_sin_las_tablas_nada_truena_lo_dice(self):
        """No se busca una base sin las migraciones: se simula el `false` de
        to_regclass sustituyendo la función detrás del caché (guía §4.8)."""
        o = self.crear([L(self.sku("A"), 1)])
        antes = self.folio_contador()
        p = mock.patch.object(ov, "_leer_tablas", return_value=False)
        p.start()
        self.addCleanup(ov._olvidar_cache)
        self.addCleanup(p.stop)
        ov._olvidar_cache()
        self.assertFalse(ov.tablas_listas())
        lista = ov.listar()
        self.assertEqual((lista["ok"], lista["falta_migracion"], lista["ordenes"], lista["total"]),
                         (False, True, [], 0))
        self.assertIn("0064, 0065 y 0068", lista["motivo"])
        self.assertEqual(set(lista["conteos"]), set(ov.FILTROS))
        est = ov.estado_modulo(OPER)
        self.assertEqual((est["ok"], est["falta_migracion"], est["bodegas"]), (False, True, []))
        self.assertTrue(est["habilitado"], "la bandera se lee aparte de las tablas")
        for r in (ov.venta_marketplace("1"), ov.ventas_pendientes()):
            self.assertEqual((r["ok"], r["ventas"]), (False, []))
        for fn in (lambda: ov.crear_borrador({"lineas": []}, OPER, self.clave()),
                   lambda: ov.obtener(o["id"], OPER),
                   lambda: ov.guardar(o["id"], o["rev"], {"descripcion": "x"}, OPER),
                   lambda: ov.confirmar(o["id"], o["rev"], OPER),
                   lambda: ov.cancelar(o["id"], o["rev"], OPER),
                   lambda: ov.canal_cancelo(o["id"], "CANCELLED", "", False),
                   lambda: ov.mensajes(o["id"]),
                   lambda: ov.buscar_skus("zzprueba"),
                   lambda: ov.subir_archivo(o["id"], OPER, "comprobante", "c.pdf", PDF)):
            with self.assertRaises(ov.FaltaMigracion) as e:
                fn()
            self.assertEqual(e.exception.status, 409)
        self.assertEqual(self.folio_contador(), antes, "no se escribió nada")
        # No saber (kubera no contestó la pregunta) NO es lo mismo que «faltan».
        p.stop()
        with mock.patch.object(ov, "_leer_tablas", side_effect=ov.SinBase()):
            ov._olvidar_cache()
            self.assertFalse(ov.tablas_listas())
            with self.assertRaises(ov.SinBase):
                ov.listar()
            self.assertEqual(ov.estado_modulo(OPER)["falta_migracion"], False)
        p.start()


class Archivos(Base):
    def con_bucket(self) -> None:
        self.sql("insert into storage.buckets (id, name) values (%s, %s)", (ov.BUCKET, ov.BUCKET))
        ov._olvidar_cache()

    def test_sin_el_bucket_no_hay_donde_guardar_y_se_dice(self):
        o = self.crear([L(self.sku("A"), 1)])
        self.assertFalse(ov.hay_bucket())
        self.assertFalse(o["permisos"]["subir_archivo"])
        self.assertIn("falta crear el bucket", o["permisos"]["porque"]["subir_archivo"])
        with self.assertRaises(ov.Conflicto) as e:
            ov.subir_archivo(o["id"], OPER, "comprobante", "c.pdf", PDF)
        self.assertEqual((e.exception.status, str(e.exception)),
                         (409, "Todavía no se pueden adjuntar PDF: falta crear el bucket "
                               "«ordenes-venta» en Storage."))
        self.assertEqual((self.storage.subidas, ov.obtener(o["id"], OPER)["rev"]), (0, o["rev"]))
        self.assertEqual(ov.estado_modulo(OPER)["archivos"],
                         {"disponible": False, "motivo": str(e.exception)})

    def test_con_bucket_subir_duplicado_bajar_y_quitar(self):
        self.con_bucket()
        self.assertEqual(ov.estado_modulo(OPER)["archivos"], {"disponible": True, "motivo": None})
        o = self.confirmada([L(self.sku("A"), 1)])      # también fuera de borrador: sólo sube rev
        self.assertTrue(o["permisos"]["subir_archivo"])
        sha = hashlib.sha256(PDF).hexdigest()
        for tipo in (None, "", "guia", "etiqueta"):
            with self.assertRaises(ov.Invalido) as e:
                ov.subir_archivo(o["id"], OPER, tipo, "c.pdf", PDF)
            self.assertIn("comprobante, factura o envío a FULL", str(e.exception))
        r = ov.subir_archivo(o["id"], OPER, "Comprobante", "C:\\papeles\\Pago 1.PDF", PDF)
        a = r["orden"]["archivos"][0]
        self.assertEqual(r["mensaje"], "PDF adjunto: Pago 1.PDF.")
        self.assertEqual((a["tipo"], a["nombre"], a["bytes"], a["sha256"], a["subido_por"],
                          a["subido_nombre"], a["orden_id"]),
                         ("comprobante", "Pago 1.PDF", len(PDF), sha, "oper@prueba.test",
                          "Olga Operadora", o["id"]))
        self.assertNotIn("ruta", a)
        self.assertEqual((r["orden"]["n_archivos"], r["orden"]["rev"], r["orden"]["estado"]),
                         (1, o["rev"] + 1, "confirmada"),
                         "adjuntar sube la rev: así los demás releen el documento")
        self.assertEqual(list(self.storage.objetos), [f"{o['folio']}/{sha}.pdf"])
        # El aviso del chat es del sistema y SIN evento: «PDF adjunto» no está en el catálogo.
        m = ov.mensajes(o["id"])["mensajes"][-1]
        self.assertEqual((m["tipo"], m["evento"], m["datos"]["sha256"], m["datos"]["tipo"]),
                         ("sistema", None, sha, "comprobante"))
        self.assertIn("Pago 1.PDF", m["cuerpo"])

        otra_vez = ov.subir_archivo(o["id"], ADMIN, "factura", "copia.pdf", PDF)
        self.assertEqual((otra_vez["mensaje"], otra_vez["orden"]["n_archivos"],
                          otra_vez["orden"]["rev"]), ("Ese PDF ya estaba adjunto.", 1, o["rev"] + 1))
        self.assertEqual(self.storage.subidas, 1, "el duplicado ni siquiera se vuelve a subir")

        self.assertEqual(ov.bajar_archivo(o["id"], a["id"], OPER), ("Pago 1.PDF", PDF))
        with self.assertRaises(ov.NoExiste):
            ov.bajar_archivo(o["id"] + 1000000, a["id"], OPER)
        with self.assertRaises(ov.SinPermiso):
            ov.borrar_archivo(o["id"], a["id"], OPER)
        b = ov.borrar_archivo(o["id"], a["id"], ADMIN)
        self.assertEqual((b["orden"]["archivos"], b["orden"]["n_archivos"], b["orden"]["rev"]),
                         ([], 0, o["rev"] + 2))
        self.assertEqual(self.storage.objetos, {})
        fila = self.sql("select borrado_por, borrado_at from ventas.ov_archivos where id = %s",
                        (a["id"],))[0]
        self.assertEqual(fila["borrado_por"], "admin@prueba.test")
        self.assertIsNotNone(fila["borrado_at"])
        m = ov.mensajes(o["id"])["mensajes"][-1]
        self.assertEqual((m["evento"], m["cuerpo"]), (None, "PDF quitado: Pago 1.PDF"))
        for fn in (lambda: ov.bajar_archivo(o["id"], a["id"], OPER),
                   lambda: ov.borrar_archivo(o["id"], a["id"], ADMIN)):
            with self.assertRaises(ov.NoExiste):
                fn()
        # Quitado, el mismo PDF se puede volver a adjuntar (el único es entre los vivos).
        de_nuevo = ov.subir_archivo(o["id"], OPER, "envio_full", "envio.pdf", PDF)["orden"]
        self.assertEqual((de_nuevo["n_archivos"], de_nuevo["archivos"][0]["tipo"]), (1, "envio_full"))
        self.assertNotEqual(de_nuevo["archivos"][0]["id"], a["id"])

    def test_el_mismo_pdf_a_la_vez_queda_una_sola_vez(self):
        self.con_bucket()
        o = self.crear([L(self.sku("A"), 1)])
        r = self.a_la_vez(*[lambda: ov.subir_archivo(o["id"], OPER, "comprobante", "c.pdf", PDF)] * 5)
        self.assertEqual([x for x in r if isinstance(x, Exception)], [])
        self.assertEqual(self.sql("select count(*) as n from ventas.ov_archivos where orden_id = %s",
                                  (o["id"],))[0]["n"], 1)
        self.assertEqual(len(self.storage.objetos), 1)
        self.assertEqual(ov.obtener(o["id"], OPER)["rev"], o["rev"] + 1, "una sola subió la rev")

    def test_lo_que_no_es_pdf_o_pesa_de_mas_no_entra(self):
        self.con_bucket()
        o = self.crear([L(self.sku("A"), 1)])
        for datos in (b"", b"PK\x03\x04 un zip", b"<html>", "texto", None):
            with self.assertRaises(ov.Invalido):
                ov.subir_archivo(o["id"], OPER, "factura", "x.pdf", datos)
        with self.assertRaises(ov.Grande) as e:
            ov.subir_archivo(o["id"], OPER, "factura", "x.pdf", b"%PDF" + b"0" * ov.MAX_PDF)
        self.assertEqual(e.exception.status, 413)
        self.assertEqual((self.storage.subidas, ov.obtener(o["id"], OPER)["n_archivos"]), (0, 0))

    def test_si_storage_falla_el_orden_protege_al_indice(self):
        self.con_bucket()
        o = self.crear([L(self.sku("A"), 1)])
        # Subir: primero el objeto. Si Storage falla, no hay fila.
        self.storage.falla_subir = True
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            with self.assertRaises(ov.FallaStorage) as e:
                ov.subir_archivo(o["id"], OPER, "factura", "f.pdf", PDF)
        self.assertEqual((e.exception.status, str(e.exception)),
                         (502, "No se pudo guardar el PDF en Storage; intenta de nuevo."))
        self.assertEqual(self.sql("select count(*) as n from ventas.ov_archivos where orden_id = %s",
                                  (o["id"],))[0]["n"], 0)
        self.storage.falla_subir = False
        a = ov.subir_archivo(o["id"], OPER, "factura", "f.pdf", PDF)["orden"]["archivos"][0]
        ruta = f"{o['folio']}/{a['sha256']}.pdf"
        # Quitar: primero se MARCA la fila. Si Storage falla después, queda un objeto
        # huérfano (aceptado) y un aviso con su ruta; nunca una fila viva sin archivo.
        self.storage.falla_borrar = True
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
            q = ov.borrar_archivo(o["id"], a["id"], ADMIN)
        self.assertEqual(q["orden"]["n_archivos"], 0)
        self.assertIn(ruta, self.storage.objetos)
        self.assertIn("HUÉRFANO", "\n".join(logs.output))
        # Un objeto que no cuadra con su huella no se entrega.
        self.storage.falla_borrar = False
        b = ov.subir_archivo(o["id"], OPER, "factura", "f.pdf", PDF + b"otro")["orden"]["archivos"][0]
        self.storage.objetos[f"{o['folio']}/{b['sha256']}.pdf"] = b"%PDF otro contenido"
        with self.assertRaises(ov.FallaStorage):
            ov.bajar_archivo(o["id"], b["id"], OPER)

    def test_quitar_es_idempotente_si_la_marca_ya_entro(self):
        """El COMMIT de la marca entró y el pool repitió el cuerpo (o lo quitó otro
        a la vez): la segunda vuelta ya no encuentra el archivo vivo. El final es
        el que se pidió, así que el objeto se borra igual y se contesta el éxito."""
        self.con_bucket()
        o = self.crear([L(self.sku("A"), 1)])
        a = ov.subir_archivo(o["id"], OPER, "factura", "f.pdf", PDF)["orden"]["archivos"][0]
        with mock.patch.object(ov.sdb, "reintentar_transitorio", lambda fn: (fn(), fn())[1]):
            r = ov.borrar_archivo(o["id"], a["id"], ADMIN)
        self.assertEqual((r["mensaje"], r["orden"]["n_archivos"], r["orden"]["rev"],
                          self.storage.objetos), ("PDF quitado: f.pdf.", 0, o["rev"] + 2, {}))
        self.assertEqual([m["cuerpo"] for m in ov.mensajes(o["id"])["mensajes"]
                          if m["evento"] is None and m["tipo"] == "sistema"][-1], "PDF quitado: f.pdf")
        with self.assertRaises(ov.NoExiste):          # una SEGUNDA petición sí es un 404
            ov.borrar_archivo(o["id"], a["id"], ADMIN)

    def test_si_la_orden_se_borra_mientras_storage_recibe_no_queda_huerfano(self):
        self.con_bucket()
        o = self.crear([L(self.sku("A"), 1)])

        def subir_y_borrar(ruta: str, datos: bytes) -> str:
            resultado = StorageFalso.subir(self.storage, ruta, datos)
            ov.borrar(o["id"], o["rev"], ADMIN, "Se borró a media subida")
            return resultado

        with mock.patch.object(ov.ov_storage, "subir", subir_y_borrar):
            with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
                with self.assertRaises(ov.Conflicto) as e:
                    ov.subir_archivo(o["id"], OPER, "factura", "f.pdf", PDF)
        self.assertIn("se borró mientras tanto", str(e.exception))
        self.assertEqual(self.storage.objetos, {}, "el objeto recién subido se quitó")


class Chat(Base):
    def test_mensajes_con_desde_id_y_envio_idempotente(self):
        o = self.crear([L(self.sku("A"), 1)])
        r1 = ov.enviar_mensaje(o["id"], OPER, "  ¿ya salió?  ")
        self.assertEqual(len(r1["mensajes"]), 1, "sólo el mensaje nuevo")
        m1 = r1["mensajes"][0]
        self.assertEqual((m1["tipo"], m1["evento"], m1["cuerpo"], m1["autor"], m1["autor_nombre"],
                          m1["via"], m1["datos"], m1["orden_id"]),
                         ("usuario", None, "¿ya salió?", "oper@prueba.test", "Olga Operadora",
                          "panel", None, o["id"]))
        self.assertEqual((r1["total"], r1["ultimo_id"], r1["rev"], r1["estado"]),
                         (2, m1["id"], 1, "borrador"))
        r2 = ov.enviar_mensaje(o["id"], ADMIN, "mañana")
        nuevos = ov.mensajes(o["id"], desde_id=m1["id"])
        self.assertEqual([m["cuerpo"] for m in nuevos["mensajes"]], ["mañana"])
        self.assertEqual((nuevos["total"], nuevos["ultimo_id"]), (3, r2["mensajes"][0]["id"]))
        nada = ov.mensajes(o["id"], desde_id=nuevos["ultimo_id"])
        self.assertEqual((nada["mensajes"], nada["total"]), ([], 3))
        self.assertEqual(ov.obtener(o["id"], OPER)["rev"], 1, "el chat no mueve la rev")
        # El reenvío con la MISMA clave devuelve el que ya estaba; la de otro autor no lo tapa.
        clave = uuid.uuid4().hex
        e1 = ov.enviar_mensaje(o["id"], OPER, "con clave", clave)
        e2 = ov.enviar_mensaje(o["id"], OPER, "con clave", clave)
        e3 = ov.enviar_mensaje(o["id"], ADMIN, "con clave", clave)
        self.assertEqual(e2["mensajes"][0]["id"], e1["mensajes"][0]["id"])
        self.assertNotEqual(e3["mensajes"][0]["id"], e1["mensajes"][0]["id"])
        self.assertEqual(e3["total"], 5)
        self.assertIsNone(e1["mensajes"][0]["datos"], "la clave no sale por la API")
        # Una transición avisa por el mismo canal: rev y estado cambian.
        ov.cancelar(o["id"], 1, OPER)
        tras = ov.mensajes(o["id"], desde_id=e3["ultimo_id"])
        self.assertEqual(([m["evento"] for m in tras["mensajes"]], tras["rev"], tras["estado"]),
                         (["cancelada"], 2, "cancelada"))
        for cuerpo in ("", "   ", "x" * 4001):
            with self.assertRaises(ov.Invalido):
                ov.enviar_mensaje(o["id"], OPER, cuerpo)
        for fn in (lambda: ov.mensajes(99999999), lambda: ov.enviar_mensaje(99999999, OPER, "hola")):
            with self.assertRaises(ov.NoExiste):
                fn()


class Lista(Base):
    def test_filtros_busqueda_conteos_y_paginas(self):
        a, b = self.sku("A"), self.sku("B")
        self.siembra(a, 20)
        borrador = self.crear([L(a, 1)], descripcion=f"mostrador {self.t}", canal="directa")
        confirmada = self.confirmada([L(a, 1)], canal="tiktok", descripcion=f"lote {self.t}")
        entregada = self.confirmada([L(a, 1), L(b, 1)], descripcion=f"lote {self.t}")
        entregada = ov.entregar(entregada["id"], entregada["rev"], OPER)["orden"]
        cancelada = self.crear([L(b, 1)], descripcion=f"lote {self.t}")
        ov.cancelar(cancelada["id"], cancelada["rev"], OPER)
        borrada = self.crear([L(b, 1)], descripcion=f"lote {self.t}")
        ov.borrar(borrada["id"], borrada["rev"], ADMIN, "Sobra en la prueba de la lista")

        def ids(**filtros) -> list[int]:
            return [x["id"] for x in ov.listar(q=self.t, **filtros)["ordenes"]]

        self.assertEqual(ids(), [cancelada["id"], entregada["id"], confirmada["id"], borrador["id"]],
                         "más recientes primero, sin la borrada")
        self.assertEqual(ids(estado="borrador"), [borrador["id"]])
        self.assertEqual(ids(estado="confirmada"), [confirmada["id"]])
        self.assertEqual(ids(estado="entregada"), [entregada["id"]])
        self.assertEqual(ids(estado="cancelada"), [cancelada["id"]])
        self.assertEqual(ids(estado="borradas"), [borrada["id"]])
        self.assertEqual(ids(canal="TikTok"), [confirmada["id"]])
        self.assertEqual([x["id"] for x in ov.listar(q=b)["ordenes"]],
                         [cancelada["id"], entregada["id"]], "busca por SKU de sus renglones")
        self.assertEqual([x["id"] for x in ov.listar(q=confirmada["folio"].lower())["ordenes"]],
                         [confirmada["id"]])
        self.assertEqual(ov.listar(q="100%_nada")["ordenes"], [], "los comodines son letras")
        pag = ov.listar(q=self.t, por_pagina=3, pagina=2)
        self.assertEqual(([x["id"] for x in pag["ordenes"]], pag["total"], pag["paginas"],
                          pag["pagina"], pag["por_pagina"]), ([borrador["id"]], 4, 2, 2, 3))
        fila = ov.listar(q=str(entregada["folio"]))["ordenes"][0]
        self.assertEqual((fila["renglones"], fila["piezas"], fila["piezas_entregadas"],
                          fila["renglones_entregados"], fila["skus"], fila["bodegas"]),
                         (2, 2, 2, 2, [a, b], ["ENSAYO"]))
        self.assertNotIn("lineas", fila)
        # Los conteos son de TODA la tabla, no de la página ni de la búsqueda.
        conteos = ov.listar(q=self.t, estado="borrador")["conteos"]
        real = self.sql("""select count(*) filter (where borrada_at is null) as todas,
                                  count(*) filter (where borrada_at is not null) as borradas,
                                  count(*) filter (where borrada_at is null and estado = 'confirmada')
                                      as confirmada from ventas.ov_ordenes""")[0]
        self.assertEqual((conteos["todas"], conteos["borradas"], conteos["confirmada"]),
                         (real["todas"], real["borradas"], real["confirmada"]))
        with self.assertRaises(ov.Invalido):
            ov.listar(estado="inventado")

    def test_el_buscador_de_productos_trae_el_saldo_por_bodega(self):
        a, b = self.sku("A"), self.sku("B")
        self.sql("insert into core.products (sku, name) values (%s, %s), (%s, %s)",
                 (a, f"Lámpara {self.t}", b, f"Lupa {self.t}"))
        self.siembra(a, 7)
        self.confirmada([L(a, 2)])
        r = ov.buscar_skus(self.t)["opciones"]
        self.assertEqual(r, [
            {"sku": a, "nombre": f"Lámpara {self.t}",
             "existencias": [{"almacen": "ENSAYO", "fisico": 7, "apartado": 2, "libre": 5}]},
            {"sku": b, "nombre": f"Lupa {self.t}", "existencias": []}])
        self.assertEqual(ov.buscar_skus("z")["opciones"], [], "menos de dos letras no busca")
        self.assertEqual(ov.buscar_skus("100%_nada")["opciones"], [])

    def test_el_estado_del_modulo_trae_banderas_bodegas_y_quien_soy(self):
        e = ov.estado_modulo(ADMIN)
        self.assertEqual((e["ok"], e["falta_migracion"], e["habilitado"]), (True, False, True))
        self.assertEqual([b["codigo"] for b in e["bodegas"]],
                         ["ENSAYO", "TEX3", "REVISION", "TEXCO", "TEX2", "DROP"])
        self.assertEqual([b["codigo"] for b in e["bodegas"] if b["admite_ov"]], ["ENSAYO"],
                         "TEX3 nace apagada: sólo se practica en ENSAYO")
        self.assertEqual(e["banderas"]["ov_generacion_auto"],
                         {"encendido": False, "persistido": False, "actualizado_por": None,
                          "motivo": None, "actualizado_at": None})
        self.assertEqual(e["yo"], {"actor": "admin@prueba.test", "nombre": "Ada Admin",
                                   "rol": "admin", "via": "panel", "admin": True, "escribe": True})


class Concurrencia(Base):
    def test_dos_confirmaciones_del_mismo_sku_con_saldo_para_una(self):
        a = self.sku("A")
        self.siembra(a, 5)
        o1, o2 = self.crear([L(a, 5)]), self.crear([L(a, 5)])
        r = self.a_la_vez(lambda: ov.confirmar(o1["id"], o1["rev"], OPER),
                          lambda: ov.confirmar(o2["id"], o2["rev"], OPER))
        ganan = [x for x in r if isinstance(x, dict)]
        pierden = [x for x in r if isinstance(x, Exception)]
        self.assertEqual((len(ganan), len(pierden)), (1, 1), f"resultados: {r}")
        self.assertIsInstance(pierden[0], ov.Conflicto)
        self.assertEqual(str(pierden[0]), f"No alcanzó el stock para apartar: {a} pide 5 y hay 0 "
                                          "libres en ENSAYO. No se apartó nada.")
        self.assertEqual(self.saldo(a), (5, 5, 0), "ni una pieza de más")
        perdio = o2 if ganan[0]["orden"]["id"] == o1["id"] else o1
        self.assertEqual(ov.obtener(perdio["id"], OPER)["estado"], "borrador")
        self.assertEqual(self.eventos(perdio["id"]), ["creada", "no_alcanzo"])

    def test_la_segunda_espera_el_candado_del_saldo_y_luego_no_alcanza(self):
        """La carrera, a cámara lenta: A confirma y NO ha hecho COMMIT (su cursor
        prestado sigue abierto); B se queda esperando la fila de saldo —se ve en
        pg_stat_activity— y, cuando A confirma, B relee y recibe `no_alcanzo`."""
        a = self.sku("A")
        self.siembra(a, 5)
        o1, o2 = self.crear([L(a, 5)]), self.crear([L(a, 5)])
        salida: dict = {}

        def b() -> None:
            try:
                salida["b"] = ov.confirmar(o2["id"], o2["rev"], OPER)
            except Exception as exc:  # noqa: BLE001
                salida["b"] = exc

        with self.conexion() as cn_a:
            cur_a = cn_a.cursor()
            ra = ov.confirmar(o1["id"], o1["rev"], OPER, cur=cur_a)
            self.assertEqual(ra["orden"]["estado"], "confirmada")
            self.assertEqual(self.saldo(a), (5, 0, 5), "fuera de la transacción de A aún no se ve")
            hilo = threading.Thread(target=b)
            hilo.start()
            espera = False
            for _ in range(60):
                time.sleep(0.05)
                espera = self.sql("""select count(*) as n from pg_stat_activity
                                      where datname = current_database() and wait_event_type = 'Lock'
                                        and query like '%%almacen.stock_almacen%%'""")[0]["n"] >= 1
                if espera:
                    break
            cn_a.commit()                          # A gana
            hilo.join(30)
        self.assertTrue(espera, "B nunca esperó el candado de A")
        self.assertIsInstance(salida["b"], ov.Conflicto)
        self.assertIn("pide 5 y hay 0 libres", str(salida["b"]))
        self.assertEqual(self.saldo(a), (5, 5, 0))
        self.assertEqual(ov.obtener(o1["id"], OPER)["estado"], "confirmada")

    def test_el_doble_clic_de_la_misma_orden_confirma_una_vez(self):
        a = self.sku("A")
        self.siembra(a, 9)
        o = self.crear([L(a, 3)])
        r = self.a_la_vez(*[lambda: ov.confirmar(o["id"], o["rev"], OPER)] * 4)
        ganan = [x for x in r if isinstance(x, dict)]
        pierden = [x for x in r if isinstance(x, Exception)]
        self.assertEqual((len(ganan), len(pierden)), (1, 3), f"resultados: {r}")
        for p in pierden:
            self.assertIsInstance(p, ov.Conflicto)
            self.assertEqual(str(p), "La orden cambió mientras tanto; se recargó.")
        self.assertEqual(self.saldo(a), (9, 3, 6), "apartó una sola vez")
        self.assertEqual(self.eventos(o["id"]), ["creada", "confirmada"])

    def test_entregar_y_cancelar_a_la_vez_no_dejan_la_orden_a_medias(self):
        a = self.sku("A")
        self.siembra(a, 4)
        o = self.confirmada([L(a, 4)])
        r = self.a_la_vez(lambda: ov.entregar(o["id"], o["rev"], OPER),
                          lambda: ov.cancelar(o["id"], o["rev"], ADMIN, "Se canceló al mismo tiempo"))
        self.assertEqual(len([x for x in r if isinstance(x, dict)]), 1, f"resultados: {r}")
        self.assertIsInstance(next(x for x in r if isinstance(x, Exception)), ov.Conflicto)
        fin = ov.obtener(o["id"], OPER)
        self.assertIn((fin["estado"], self.saldo(a)),
                      (("entregada", (0, 0, 0)), ("cancelada", (4, 0, 4))))

    def test_si_la_bodega_sigue_ocupada_tras_el_reintento_se_dice(self):
        """Otro movimiento tiene bloqueada la fila de saldo. La sentencia espera lo
        que diga SU lock_timeout (va en el mismo envío), se reintenta UNA vez y,
        si sigue ocupada, sale «bodega ocupada» (no un 500, y sin colgar el pool).
        Para no esperar 4 s + 4 s la prueba acorta el timeout de la constante."""
        a = self.sku("A")
        self.siembra(a, 3)
        o = self.crear([L(a, 1)])
        self.assertIn("set local lock_timeout = '4s';", ov.SQL_CONFIRMAR)
        corta = ov.SQL_CONFIRMAR.replace("lock_timeout = '4s'", "lock_timeout = '250ms'")
        with self.conexion() as otro:
            c = otro.cursor()
            c.execute("select 1 from almacen.stock_almacen where sku = %s and almacen = 'ENSAYO' "
                      "for update", (a,))
            with mock.patch.object(ov, "SQL_CONFIRMAR", corta):
                with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
                    t0 = time.monotonic()
                    with self.assertRaises(ov.Conflicto) as e:
                        ov.confirmar(o["id"], o["rev"], OPER)
                    tardo = time.monotonic() - t0
        self.assertEqual(str(e.exception),
                         "La bodega está ocupada con otro movimiento; intenta de nuevo en unos "
                         "segundos.")
        self.assertGreaterEqual(tardo, 0.45, "dos intentos: el primero y UN reintento")
        self.assertLess(tardo, 3)
        self.assertEqual((self.saldo(a), ov.obtener(o["id"], OPER)["estado"]), ((3, 0, 3), "borrador"))
        # Suelto el candado, la misma petición entra.
        self.assertEqual(ov.confirmar(o["id"], o["rev"], OPER)["orden"]["estado"], "confirmada")

    def test_el_reintento_transitorio_de_una_transicion_que_si_entro_no_es_un_conflicto(self):
        """El COMMIT entró y la conexión murió al contestar: el pool repite el
        cuerpo y el CAS falla… contra MI propia escritura. La marca `op` del
        mensaje lo distingue de «la movió otro»: se contesta el éxito que fue."""
        a = self.sku("A")
        self.siembra(a, 6)
        o = self.crear([L(a, 2)])
        eco = mock.patch.object(ov.sdb, "reintentar_transitorio", lambda fn: (fn(), fn())[1])
        with eco:
            c = ov.confirmar(o["id"], o["rev"], OPER)
        self.assertEqual((c["orden"]["estado"], c["orden"]["rev"], self.saldo(a)),
                         ("confirmada", 2, (6, 2, 4)))
        with eco:
            e = ov.entregar(o["id"], 2, OPER)
        self.assertEqual((e["mensaje"], e["orden"]["estado"], self.saldo(a)),
                         ("Entregada a la paquetería.", "entregada", (4, 0, 4)))
        self.assertEqual(self.eventos(o["id"]), ["creada", "confirmada", "entregada"])
        self.assertEqual(self.libro(a), [("correccion", 6, 6), ("salida_ov", -2, 4)])


class AlCommit(Base):
    """Lo que la base revisa AL COMMIT (constraint triggers diferidos) no truena
    en el `execute`: truena al cerrar la transacción. Para verlo sin una carrera
    de verdad, la prueba hace que Python se EQUIVOQUE de sentencia —cancelar
    «sin salidas» una orden de la que ya salió una pieza— y deja que la base lo
    frene (23514 ov_coherente: una cancelada no dejó salir piezas)."""

    def con_una_pieza_afuera(self) -> tuple[dict, str]:
        a, b = self.sku(f"A{uuid.uuid4().hex[:4]}"), self.sku(f"B{uuid.uuid4().hex[:4]}")
        self.siembra(a, 3)
        self.siembra(b, 3)
        o = self.confirmada([L(a, 1), L(b, 2)])
        o = ov.entregar(o["id"], o["rev"], OPER, [{"id": o["lineas"][0]["id"], "n": 1}])["orden"]
        self.assertEqual(self.saldo(b), (3, 2, 1))
        return o, b

    def test_el_canal_relee_y_decide_de_nuevo(self):
        o, b = self.con_una_pieza_afuera()
        real = ov._params_cancelar
        vio: list[bool] = []

        def se_equivoca_la_primera(*args):
            con_salida, params, hecho = real(*args)
            vio.append(con_salida)
            return (False if len(vio) == 1 else con_salida), params, hecho

        with mock.patch.object(ov, "_params_cancelar", se_equivoca_la_primera):
            r = ov.canal_cancelo(o["id"], "CANCELLED", "", False)
        self.assertEqual((r["resultado"], vio), ("entregada_cancelada", [True, True]),
                         "el primer intento se deshizo ENTERO al COMMIT; el segundo releyó")
        self.assertEqual((r["orden"]["estado"], r["orden"]["rev"], self.saldo(b)),
                         ("entregada_cancelada", o["rev"] + 1, (3, 0, 3)))
        eventos = self.eventos(o["id"])
        self.assertEqual((eventos[-1], eventos.count("cancelada")), ("devolucion_esperada", 0),
                         "del intento fallido no quedó ni el mensaje")

    def test_a_una_persona_le_sale_un_502_y_no_queda_nada_a_medias(self):
        o, b = self.con_una_pieza_afuera()
        real = ov._params_cancelar
        with mock.patch.object(ov, "_params_cancelar", lambda *a: (False, *real(*a)[1:])):
            with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
                with self.assertRaises(ov.ErrorOV) as e:
                    ov.cancelar(o["id"], o["rev"], ADMIN, "Cancelar con la sentencia equivocada")
        self.assertEqual((type(e.exception), e.exception.status), (ov.ErrorOV, 502))
        self.assertIn("regla=ov_coherente", "\n".join(logs.output))
        self.assertNotIn("ov_coherente", str(e.exception))
        tras = ov.obtener(o["id"], OPER)
        self.assertEqual((tras["estado"], tras["rev"], tras["n_mensajes"], self.saldo(b)),
                         ("confirmada", o["rev"], o["n_mensajes"], (3, 2, 1)))
        # Con la sentencia correcta, la misma petición entra.
        bien = ov.cancelar(o["id"], o["rev"], ADMIN, "Ahora sí, con lo que ya salió")["orden"]
        self.assertEqual((bien["estado"], self.saldo(b)), ("entregada_cancelada", (3, 0, 3)))

    def test_los_set_local_no_se_quedan_pegados_en_la_conexion_del_pool(self):
        """Regla 13 y guía §4.7: los timeouts van como SET LOCAL en el mismo envío.
        Después de una transición, ninguna conexión del pool los conserva."""
        o = self.confirmada([L(self.sku("A"), 1)])
        ov.entregar(o["id"], o["rev"], OPER)
        vistos = {(ov._fila("show lock_timeout")["lock_timeout"],
                   ov._fila("show statement_timeout")["statement_timeout"]) for _ in range(12)}
        self.assertEqual(vistos, {("0", "0")})


class CursorPrestado(Base):
    def test_con_cur_todo_corre_en_la_transaccion_de_quien_lo_presta_y_no_se_confirma(self):
        """Guía §7.3: cada función acepta `cur`. El ciclo entero encadenado en una
        transacción que termina en ROLLBACK no deja NADA: ni folio, ni saldo, ni
        chat. Y un rechazo esperado no deja abortada la transacción prestada."""
        a = self.sku("A")
        antes = self.folio_contador()
        ordenes = self.sql("select count(*) as n from ventas.ov_ordenes")[0]["n"]
        with self.conexion() as cn:
            cur = cn.cursor()
            self.siembra(a, 3, cur=cur)
            o = ov.crear_borrador({"lineas": [L(a, 5)]}, OPER, self.clave(), cur=cur)["orden"]
            with self.assertRaises(ov.Conflicto) as e:          # KB001 dentro del savepoint
                ov.confirmar(o["id"], o["rev"], OPER, cur=cur)
            self.assertIn("pide 5 y hay 3 libres", str(e.exception))
            # La transacción sigue viva: se puede seguir usando el mismo cursor.
            o = ov.guardar(o["id"], o["rev"], {"lineas": [L(a, 3)], "total": None}, OPER,
                           cur=cur)["orden"]
            o = ov.confirmar(o["id"], o["rev"], OPER, cur=cur)["orden"]
            o = ov.entregar(o["id"], o["rev"], OPER, [{"id": o["lineas"][0]["id"], "n": 2}],
                            cur=cur)["orden"]
            self.assertEqual((o["estado"], o["piezas_entregadas"]), ("entregada", 2))
            self.assertEqual([m["evento"] for m in ov.mensajes(o["id"], cur=cur)["mensajes"]],
                             ["creada", "no_alcanzo", "borrador_guardado", "confirmada", "entregada"])
            self.assertEqual(ov.listar(q=o["folio"], cur=cur)["total"], 1)
            cur.execute("set constraints all immediate")        # lo que revisaría el COMMIT
            self.assertEqual(self.sql("select count(*) as n from ventas.ov_ordenes")[0]["n"], ordenes,
                             "desde afuera no se ve nada: no hubo COMMIT")
        self.assertEqual((self.folio_contador(), self.saldo(a)), (antes, None))
        self.assertEqual(self.sql("select count(*) as n from ventas.ov_ordenes")[0]["n"], ordenes)

    def test_tambien_con_un_cursor_de_tuplas(self):
        """El cursor prestado puede no ser RealDictCursor (el del verificador no lo es)."""
        cn = psycopg2.connect(DSN)
        try:
            cur = cn.cursor()
            o = ov.crear_borrador({"lineas": [L(self.sku("A"), 1)]}, OPER, self.clave(),
                                  cur=cur)["orden"]
            self.assertEqual((o["estado"], o["renglones"]), ("borrador", 1))
            self.assertTrue(ov.habilitado(cur=cur))
            self.assertEqual(len(ov.bodegas(cur=cur)), 6)
        finally:
            cn.rollback()
            cn.close()


# ══════════════════════════════════════════════════════════════════════════════
def llaves_ts(interfaz: str) -> tuple[set[str], set[str]]:
    """(obligatorias, opcionales) de una `export interface` de tipos.ts, sólo su primer nivel."""
    texto = TIPOS.read_text(encoding="utf-8")
    m = re.search(r"export interface " + interfaz + r"\b[^{]*\{", texto)
    assert m, f"tipos.ts ya no tiene la interfaz {interfaz}"
    nivel, cuerpo = 1, []
    for c in texto[m.end():]:
        nivel += (c == "{") - (c == "}")
        if nivel == 0:
            break
        if nivel == 1 and c != "}":
            cuerpo.append(c)
    limpio = re.sub(r"/\*.*?\*/", "", "".join(cuerpo), flags=re.S)
    limpio = re.sub(r"//[^\n]*", "", limpio)
    pares = re.findall(r"(?:^|;)\s*(\w+)(\??)\s*:", limpio, flags=re.M)
    return ({k for k, opcional in pares if not opcional}, {k for k, opcional in pares if opcional})


def union_ts(tipo: str) -> set[str]:
    """Los literales de un `export type X = "a" | "b"` de tipos.ts."""
    m = re.search(r"export type " + tipo + r"\s*=([^;]+);", TIPOS.read_text(encoding="utf-8"))
    assert m, f"tipos.ts ya no tiene el tipo {tipo}"
    return set(re.findall(r'"(\w+)"', re.sub(r"//[^\n]*", "", m.group(1))))


@unittest.skipUnless(TIPOS.exists(), "sin frontend/components/ordenes/tipos.ts")
class ContratoConTipos(Base):
    """Lo que contesta el servicio tiene EXACTAMENTE las llaves de tipos.ts: ni una
    de menos (la pantalla pintaría `undefined`) ni una de más sin declarar."""

    def cuadra(self, valor: dict, interfaz: str, *, hereda: str | None = None) -> None:
        obligatorias, opcionales = llaves_ts(interfaz)
        if hereda:
            a, b = llaves_ts(hereda)
            obligatorias, opcionales = obligatorias | a, opcionales | b
        self.assertEqual(obligatorias - set(valor), set(), f"{interfaz}: faltan llaves")
        self.assertEqual(set(valor) - obligatorias - opcionales, set(),
                         f"{interfaz}: llaves que tipos.ts no declara")

    def test_las_respuestas_tienen_las_llaves_de_tipos_ts(self):
        a = self.sku("A")
        orden = f"V-{self.t}"
        self.sql("insert into storage.buckets (id, name) values (%s, %s)", (ov.BUCKET, ov.BUCKET))
        ov._olvidar_cache()
        self.sql("insert into core.products (sku, name) values (%s, 'Producto de prueba')", (a,))
        self.venta_canal(orden, [(a, 2, 10, False)])
        c = self.confirmada([L(a, 2, 10)], mp_canal="tiktok", mp_cuenta=CUENTA, mp_orden=orden)
        # Editar la confirmada contesta lo mismo que guardar un borrador (RespOrden), y
        # su renglón `editada` es un Mensaje más (se revisa abajo, con todo el chat).
        editada = ov.guardar(c["id"], c["rev"], {"guia": "GUIA-PRUEBA-1", "lineas": [L(a, 2, 12)]},
                             OPER)
        self.cuadra(editada, "RespOrden")
        self.cuadra(editada["orden"]["permisos"], "Permisos")
        c = editada["orden"]
        r = ov.subir_archivo(c["id"], OPER, "comprobante", "pago.pdf", PDF)
        self.cuadra(r, "RespOrden")
        o = r["orden"]
        self.cuadra(o, "Orden", hereda="OrdenResumen")
        self.cuadra(o["lineas"][0], "LineaOrden")
        self.cuadra(o["archivos"][0], "Archivo")
        self.cuadra(o["permisos"], "Permisos")
        self.assertEqual(set(ov.ACCIONES) | {"porque"}, set(o["permisos"]))
        lista = ov.listar()
        self.cuadra(lista, "ListaOrdenes")
        self.cuadra(lista["ordenes"][0], "OrdenResumen")
        with mock.patch.object(ov, "_leer_tablas", return_value=False):
            ov._olvidar_cache()
            self.cuadra(ov.listar(), "ListaOrdenes")
            self.cuadra(ov.estado_modulo(OPER), "EstadoModulo")
        ov._olvidar_cache()
        self.assertEqual(set(lista["conteos"]), set(ov.FILTROS))
        self.assertEqual(union_ts("FiltroEstado") | set(ov.ESTADOS), set(ov.FILTROS),
                         "conteos trae cada FiltroEstado")
        chat = ov.mensajes(c["id"])
        self.cuadra(chat, "RespMensajes")
        for m in chat["mensajes"]:
            self.cuadra(m, "Mensaje")
        self.cuadra(ov.enviar_mensaje(c["id"], OPER, "hola"), "RespMensajes")
        est = ov.estado_modulo(OPER)
        self.cuadra(est, "EstadoModulo")
        self.assertEqual(set(est["banderas"]), {"ordenes_venta", "ov_generacion_auto"})
        for bandera in est["banderas"].values():
            self.cuadra(bandera, "Bandera")
        self.cuadra(est["bodegas"][0], "Bodega")
        self.assertEqual(set(est["archivos"]), {"disponible", "motivo"})
        self.assertEqual(set(est["yo"]), {"actor", "nombre", "rol", "via", "admin", "escribe"})
        opcion = ov.buscar_skus(a)["opciones"][0]
        self.cuadra(opcion, "SkuOpcion")
        self.cuadra(opcion["existencias"][0], "Existencia")
        ventas = ov.venta_marketplace(orden)
        self.cuadra(ventas, "RespVentas")
        self.cuadra(ventas["ventas"][0], "VentaMarketplace")
        self.cuadra(ventas["ventas"][0]["lineas"][0], "LineaEntrada")
        self.assertEqual(set(ventas["ventas"][0]["ov"]), {"id", "folio", "estado"})
        self.cuadra(ov.ventas_pendientes(), "RespVentas")
        # Los valores que la pantalla compara contra un tipo cerrado.
        self.assertEqual(union_ts("EstadoOrden"), set(ov.ESTADOS))
        self.assertEqual(union_ts("EventoOrden"), set(ov.EVENTOS))
        self.assertEqual(union_ts("TipoArchivo"), set(ov.TIPOS_ARCHIVO))
        self.assertEqual(union_ts("Via"), set(ov.VIAS))
        self.assertEqual(union_ts("TipoOrden"), {"venta", "full"})
        self.assertEqual(o["tipo"], "venta")
        # Y los CHECK de la base dicen lo mismo que las constantes del servicio.
        chk = {f["conname"]: f["def"] for f in self.sql(
            """select conname, pg_get_constraintdef(oid) as def from pg_constraint
                where conname in ('ov_mensajes_evento_chk', 'ov_archivos_tipo_chk',
                                  'ov_ordenes_canal_chk', 'ov_ordenes_via_chk',
                                  'ov_ordenes_estado_chk', 'ov_ordenes_cancelada_origen_chk')""")}
        for nombre, valores in (("ov_mensajes_evento_chk", ov.EVENTOS),
                                ("ov_archivos_tipo_chk", ov.TIPOS_ARCHIVO),
                                ("ov_ordenes_canal_chk", ov.CANALES), ("ov_ordenes_via_chk", ov.VIAS),
                                ("ov_ordenes_estado_chk", ov.ESTADOS),
                                ("ov_ordenes_cancelada_origen_chk", ov.ORIGENES_CANCELACION)):
            self.assertEqual(set(re.findall(r"'(\w+)'::text", chk[nombre])), set(valores), nombre)


# ══════════════════════════════════════════════════════════════════════════════
# LO QUE SE APOYA EN EL SERVICIO: el barrido de cancelaciones del canal
# (services/ov_auto.py), el job del scheduler y la API (routers/ordenes_venta.py),
# de punta a punta contra la misma base local. Sus pruebas sin base —con el
# servicio sustituido por dobles— están en tests/test_ordenes_venta_api.py.
#
# OJO, LA BASE ES COMPARTIDA: `ov_auto.revisar()` barre TODAS las órdenes vivas,
# también las que dejaron otras pruebas. Por eso cada prueba mira lo que el
# barrido hizo con SUS órdenes (`pasada`), y sólo compara la respuesta entera
# —vacía— en una SEGUNDA pasada, cuando la primera ya se llevó lo ajeno.
# ══════════════════════════════════════════════════════════════════════════════
import asyncio  # noqa: E402
import logging  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.identidad import Identidad  # noqa: E402
from routers import ordenes_venta as ruta  # noqa: E402
from services import ov_auto, ov_bus  # noqa: E402

RAIZ = "/api/ordenes-venta"
NADA_QUE_HACER = {"ok": True, "canceladas": [], "marcadas": []}

ID_OPER = Identidad(actor="oper@prueba.test", tipo="persona", rol="operador", id="u-oper")
ID_ADMIN = Identidad(actor="admin@prueba.test", tipo="persona", rol="admin", id="u-admin")
ID_LECT = Identidad(actor="lectura@prueba.test", tipo="persona", rol="lectura", id="u-lect")
ID_MAQUINA = Identidad(actor="servicio", tipo="maquina", rol="admin")


class VentasDelCanal(Base):
    """Ayudantes: una venta en channel.orders con su orden propia, y lo que el
    canal (o la bitácora de Automatización) dice después de esa venta."""

    def setUp(self) -> None:
        super().setUp()
        ov_auto._migracion_avisada = False
        ov_bus._reiniciar()
        self.addCleanup(ov_bus._reiniciar)
        # El barrido dice en WARNING cada orden que mueve (así se ve en Railway).
        # Aquí no es ruido que haya que leer: se recoge, para que la salida de la
        # suite siga siendo sus puntos. `assertLogs` lo sigue viendo igual.
        registro, nulo = logging.getLogger("omnicanal.ov_auto"), logging.NullHandler()
        registro.addHandler(nulo)
        self.addCleanup(registro.removeHandler, nulo)

    def orden_ligada(self, estado: str = "confirmada", canal: str = "tiktok", n: int = 2,
                     fisico: int = 5, cuenta_canal: str = CUENTA) -> tuple[dict, str, str]:
        """(orden, sku, id de la venta). La venta nace VIVA; la orden queda en
        `estado` (borrador | confirmada | entregada)."""
        a = self.sku(f"A{uuid.uuid4().hex[:4]}")
        venta = f"V-{uuid.uuid4().hex[:10]}"
        self.venta_canal(venta, [(a, n, 10, False)], canal=canal, cuenta=cuenta_canal,
                         estado_canal="AWAITING_SHIPMENT" if canal == "tiktok" else "2")
        self.siembra(a, fisico)
        o = self.crear([L(a, n)], mp_canal=canal, mp_cuenta=CUENTA, mp_orden=venta)
        if estado in ("confirmada", "entregada"):
            o = ov.confirmar(o["id"], o["rev"], OPER)["orden"]
        if estado == "entregada":
            o = ov.entregar(o["id"], o["rev"], OPER)["orden"]
        self.assertEqual(o["estado"], estado)
        return o, a, venta

    def el_canal_dice(self, venta: str, estado_canal: str | None, estado_wc: str = "cancelled",
                      cuenta: str = CUENTA) -> None:
        self.sql("""update channel.orders set estado_canal = %s, estado_wc = %s,
                           actualizado_at = now()
                     where external_order_id = %s and upper(cuenta) = %s""",
                 (estado_canal, estado_wc, venta, cuenta.upper()))

    def la_bitacora_dice(self, venta: str, accion: str, canal: str = "temu",
                         motivo: str | None = None) -> None:
        self.sql("""insert into ops.odoo_sale_orders (canal, cuenta, external_order_id, accion,
                           motivo) values (%s, %s, %s, %s, %s)
                    on conflict (canal, cuenta, external_order_id)
                    do update set accion = excluded.accion, motivo = excluded.motivo""",
                 (canal, CUENTA, venta, accion, motivo))

    def pasada(self, *ordenes: dict) -> tuple[list[dict], list[dict]]:
        """Corre el barrido y devuelve lo que hizo con MIS órdenes."""
        r = ov_auto.revisar()
        self.assertEqual(set(r), {"ok", "canceladas", "marcadas"}, r)
        self.assertTrue(r["ok"])
        ids = {o["id"] for o in ordenes}
        return ([c for c in r["canceladas"] if c["id"] in ids],
                [m for m in r["marcadas"] if m["id"] in ids])

    @staticmethod
    def quedo(o: dict, estado: str) -> dict:
        return {"id": o["id"], "folio": o["folio"], "estado": estado}


class BarridoDelCanal(VentasDelCanal):
    """`ov_auto.revisar()` contra la base: lee channel.orders y la bitácora, y lo
    que escribe lo escribe el servicio (con los invariantes revisados al final)."""

    def test_confirmada_sin_salir_se_cancela_y_repetido_no_hace_nada(self):
        o, a, venta = self.orden_ligada()
        self.assertEqual(self.pasada(o), ([], []), "la venta sigue viva: no se toca")
        self.assertEqual(self.saldo(a), (5, 2, 3))
        self.el_canal_dice(venta, "CANCELLED")
        self.assertEqual(self.pasada(o), ([self.quedo(o, "cancelada")], []))
        c = ov.obtener(o["id"], ADMIN)
        self.assertEqual((c["estado"], c["cancelada_origen"], c["cancelada_por"],
                          c["cancelada_nombre"], c["cancelada_motivo"], c["canal_cancelo_at"],
                          c["devolucion_estado"]),
                         ("cancelada", "marketplace", "automatico", "Automático",
                          f"Venta cancelada en TikTok ({venta})", None, None))
        self.assertEqual(self.saldo(a), (5, 0, 5), "soltó exactamente lo apartado")
        self.assertEqual(self.libro(a), [("correccion", 5, 5)], "nada salió: el libro no se movió")
        m = ov.mensajes(o["id"])["mensajes"][-1]
        self.assertEqual((m["tipo"], m["evento"], m["autor"], m["via"]),
                         ("sistema", "cancelada", "automatico", "automatico"))
        # REPETIDO: el sondeo vuelve a ver la misma cancelación y no hace nada.
        for _ in range(2):
            self.assertEqual(ov_auto.revisar(), NADA_QUE_HACER)
        tras = ov.obtener(o["id"], ADMIN)
        self.assertEqual((tras["rev"], tras["n_mensajes"]), (c["rev"], c["n_mensajes"]))

    def test_en_camino_se_marca_y_espera_a_bodega(self):
        o, a, venta = self.orden_ligada()
        self.el_canal_dice(venta, "IN_TRANSIT")
        self.assertEqual(self.pasada(o), ([], [{"id": o["id"], "folio": o["folio"]}]))
        m = ov.obtener(o["id"], OPER)
        self.assertEqual((m["estado"], m["canal_cancelo_ref"], m["rev"], m["piezas_apartadas"]),
                         ("confirmada", "IN_TRANSIT", o["rev"] + 1, 2))
        self.assertIsNotNone(m["canal_cancelo_at"])
        self.assertEqual(self.saldo(a), (5, 2, 3), "la marca NO suelta el apartado")
        self.assertTrue(m["permisos"]["responder_salio"])
        self.assertFalse(m["permisos"]["entregar"])
        aviso = ov.mensajes(o["id"])["mensajes"][-1]
        self.assertEqual((aviso["evento"], aviso["autor"], aviso["via"]),
                         ("canal_cancelo", "automatico", "automatico"))
        self.assertIn("IN_TRANSIT", aviso["cuerpo"])
        # REPETIDO: una orden que ya espera el «¿salió?» ni se vuelve a leer.
        for _ in range(2):
            self.assertEqual(ov_auto.revisar(), NADA_QUE_HACER)
        self.assertEqual(ov.obtener(o["id"], OPER)["rev"], m["rev"])
        self.assertEqual(self.eventos(o["id"]).count("canal_cancelo"), 1)
        self.assertNotIn(o["id"], [f["id"] for f in self.sql(ov_auto._SQL_CON_BITACORA,
                                                             {"dias": 45})])
        # Bodega contesta; de ahí en adelante el barrido no tiene nada que decirle.
        f = ov.responder_salio(o["id"], m["rev"], OPER, True)["orden"]
        self.assertEqual((f["estado"], f["devolucion_estado"]), ("entregada_cancelada", "pendiente"))
        self.assertEqual(self.pasada(o), ([], []))
        self.assertEqual(ov.obtener(o["id"], OPER)["rev"], f["rev"])

    def test_si_solo_el_pedido_de_woo_dice_cancelada_tambien_cuenta(self):
        """`estado_wc = cancelled` con el estado del canal vacío o atrasado: es la
        misma regla de la pantalla (`cancelada_en_canal`). Sin señal de envío no
        se pregunta nada: se cancela."""
        sin_estado, a1, v1 = self.orden_ligada()
        atrasado, a2, v2 = self.orden_ligada()
        entregada, _, v3 = self.orden_ligada("entregada")
        self.el_canal_dice(v1, None)
        self.el_canal_dice(v2, "AWAITING_SHIPMENT")
        self.el_canal_dice(v3, None)
        canceladas, marcadas = self.pasada(sin_estado, atrasado, entregada)
        self.assertEqual((canceladas, marcadas),
                         ([self.quedo(sin_estado, "cancelada"), self.quedo(atrasado, "cancelada"),
                           self.quedo(entregada, "entregada_cancelada")], []))
        self.assertEqual((self.saldo(a1), self.saldo(a2)), ((5, 0, 5), (5, 0, 5)))
        for o, venta in ((sin_estado, v1), (atrasado, v2), (entregada, v3)):
            self.assertEqual(ov.obtener(o["id"], OPER)["cancelada_motivo"],
                             f"Venta cancelada en TikTok ({venta})")
        # Sin estado del canal la referencia viaja vacía y el servicio pone la suya.
        aviso = ov.mensajes(entregada["id"])["mensajes"][-1]
        self.assertEqual((aviso["evento"], aviso["datos"]["ref_canal"]),
                         ("devolucion_esperada", "cancelada"))

    def test_cada_estado_de_envio_del_canal_cuenta_como_en_camino(self):
        ordenes = []
        for estado in ("AWAITING_COLLECTION", "SHIPPED", "shipped"):
            o, _, venta = self.orden_ligada()
            self.el_canal_dice(venta, estado)
            ordenes.append((o, estado))
        _, marcadas = self.pasada(*[o for o, _ in ordenes])
        self.assertEqual(marcadas, [{"id": o["id"], "folio": o["folio"]} for o, _ in ordenes])
        for o, estado in ordenes:
            self.assertEqual(ov.obtener(o["id"], OPER)["canal_cancelo_ref"], estado)

    def test_temu_avisa_por_la_bitacora_de_automatizacion(self):
        """Temu NO actualiza channel.orders al cancelar: la venta se queda en 2
        (pagada), 4 (enviada) o 5 (entregada) y quien avisa es la bitácora."""
        sin_salir, a1, v1 = self.orden_ligada(canal="temu")
        por_revisar, a2, v2 = self.orden_ligada(canal="temu")
        enviada, a3, v3 = self.orden_ligada(canal="temu")
        viva, a4, v4 = self.orden_ligada(canal="temu")
        self.sql("update channel.orders set estado_canal = '4' where external_order_id = any(%s)",
                 ([v3, v4],))
        self.la_bitacora_dice(v1, "cancelada", motivo="Temu canceló la venta")
        self.la_bitacora_dice(v2, "cancelada_revisar")
        self.la_bitacora_dice(v3, "cancelada_por_cancelar")
        self.la_bitacora_dice(v4, "creada")
        canceladas, marcadas = self.pasada(sin_salir, por_revisar, enviada, viva)
        self.assertEqual(canceladas, [self.quedo(sin_salir, "cancelada")])
        self.assertEqual(marcadas, [{"id": o["id"], "folio": o["folio"]}
                                    for o in (por_revisar, enviada)])
        self.assertEqual(ov.obtener(sin_salir["id"], OPER)["cancelada_motivo"],
                         f"Venta cancelada en Temu ({v1}) · Automatización: Temu canceló la venta")
        # La referencia es lo que dijo el canal; si la señal fue la bitácora, su acción.
        self.assertEqual(ov.obtener(por_revisar["id"], OPER)["canal_cancelo_ref"],
                         "cancelada_revisar")
        self.assertEqual(ov.obtener(enviada["id"], OPER)["canal_cancelo_ref"], "4")
        intacta = ov.obtener(viva["id"], OPER)
        self.assertEqual((intacta["estado"], intacta["canal_cancelo_at"], intacta["rev"]),
                         ("confirmada", None, viva["rev"]))
        self.assertEqual((self.saldo(a1), self.saldo(a2), self.saldo(a3), self.saldo(a4)),
                         ((5, 0, 5), (5, 2, 3), (5, 2, 3), (5, 2, 3)))
        self.assertEqual(ov_auto.revisar(), NADA_QUE_HACER)

    def test_entregada_queda_entregada_y_cancelada_con_devolucion_pendiente(self):
        o, a, venta = self.orden_ligada("entregada")
        self.assertEqual(self.saldo(a), (3, 0, 3))
        self.el_canal_dice(venta, "CANCELLED")
        self.assertEqual(self.pasada(o), ([self.quedo(o, "entregada_cancelada")], []))
        c = ov.obtener(o["id"], OPER)
        self.assertEqual((c["estado"], c["devolucion_estado"], c["cancelada_origen"],
                          c["cancelada_por"], c["entregada_at"], c["entregada_por"]),
                         ("entregada_cancelada", "pendiente", "marketplace", "automatico",
                          o["entregada_at"], "oper@prueba.test"))
        self.assertEqual(self.saldo(a), (3, 0, 3), "ya había salido: no hay saldo que tocar")
        self.assertEqual(self.eventos(o["id"])[-1], "devolucion_esperada")
        # Aunque el canal diga «en camino», una ENTREGADA no se «marca»: ya salió.
        o2, _, v2 = self.orden_ligada("entregada")
        self.el_canal_dice(v2, "IN_TRANSIT")
        self.assertEqual(self.pasada(o2), ([self.quedo(o2, "entregada_cancelada")], []))
        self.assertIsNone(ov.obtener(o2["id"], OPER)["canal_cancelo_at"])
        self.assertEqual(ov_auto.revisar(), NADA_QUE_HACER)
        self.assertEqual(ov.obtener(o["id"], OPER)["rev"], c["rev"])

    def test_con_una_entrega_parcial_queda_entregada_y_cancelada(self):
        a, b = self.sku("A"), self.sku("B")
        venta = f"V-{uuid.uuid4().hex[:10]}"
        self.venta_canal(venta, [(a, 1, 10, False), (b, 2, 10, False)])
        self.siembra(a, 3)
        self.siembra(b, 3)
        o = self.confirmada([L(a, 1), L(b, 2)], mp_canal="tiktok", mp_cuenta=CUENTA, mp_orden=venta)
        o = ov.entregar(o["id"], o["rev"], OPER, [{"id": o["lineas"][0]["id"], "n": 1}])["orden"]
        self.assertEqual(o["estado"], "confirmada")
        self.el_canal_dice(venta, "CANCELLED")
        self.assertEqual(self.pasada(o), ([self.quedo(o, "entregada_cancelada")], []))
        self.assertEqual(ov.obtener(o["id"], OPER)["devolucion_estado"], "pendiente")
        self.assertEqual((self.saldo(a), self.saldo(b)), ((2, 0, 2), (3, 0, 3)),
                         "lo que salió, salió; lo que no, se soltó")

    def test_un_borrador_ligado_a_la_venta_cancelada_se_cancela(self):
        o, a, venta = self.orden_ligada("borrador")
        self.el_canal_dice(venta, "CANCELLED")
        self.assertEqual(self.pasada(o), ([self.quedo(o, "cancelada")], []))
        c = ov.obtener(o["id"], ADMIN)
        self.assertEqual((c["estado"], c["cancelada_origen"], c["cancelada_por"], c["confirmada_at"],
                          c["cancelada_motivo"], c["rev"]),
                         ("cancelada", "marketplace", "automatico", None,
                          f"Venta cancelada en TikTok ({venta})", o["rev"] + 1))
        self.assertEqual(self.saldo(a), (5, 0, 5), "un borrador no apartaba nada")
        # Un borrador no tiene paquete: aunque el canal diga «en camino», se cancela.
        o2, _, v2 = self.orden_ligada("borrador")
        self.el_canal_dice(v2, "IN_TRANSIT")
        self.assertEqual(self.pasada(o2), ([self.quedo(o2, "cancelada")], []))
        self.assertEqual(ov_auto.revisar(), NADA_QUE_HACER)
        # Cancelada, soltó su venta: la misma venta puede volver a capturarse.
        self.assertIsNone(ov.venta_marketplace(venta)["ventas"][0]["ov"])

    def test_la_cuenta_siempre_se_compara(self):
        o, a, venta = self.orden_ligada()
        # La MISMA venta (mismo número) en OTRA cuenta y en OTRO canal, canceladas:
        # no son la mía. Cancelar por ellas soltaría un apartado que sí hace falta.
        self.venta_canal(venta, [(a, 2, 10, False)], cuenta="OTRACUENTAPRUEBA",
                         estado_canal="CANCELLED", estado_wc="cancelled")
        self.venta_canal(venta, [(a, 2, 10, False)], canal="temu", estado_canal="3")
        self.sql("""insert into ops.odoo_sale_orders (canal, cuenta, external_order_id, accion)
                    values ('tiktok', 'OTRACUENTAPRUEBA', %s, 'cancelada')""", (venta,))
        self.assertEqual(self.pasada(o), ([], []))
        tras = ov.obtener(o["id"], OPER)
        self.assertEqual((tras["estado"], tras["rev"], self.saldo(a)),
                         ("confirmada", o["rev"], (5, 2, 3)))
        # …y la cuenta del canal escrita en minúsculas SÍ es la mía.
        o2, a2, v2 = self.orden_ligada(cuenta_canal="cuentaprueba")
        self.el_canal_dice(v2, "CANCELLED")
        self.assertEqual(self.pasada(o, o2), ([self.quedo(o2, "cancelada")], []))
        self.assertEqual(self.saldo(a2), (5, 0, 5))

    def test_si_corrigen_la_liga_despues_de_la_lectura_no_se_toca_la_orden_de_otra_venta(self):
        """La lectura del barrido ENVEJECE: lee una vez todas las órdenes vivas con
        su venta y después avisa orden por orden. Desde la 0071 la liga de una
        confirmada se puede corregir, así que en ese hueco una persona puede haberla
        pasado a OTRA venta —viva— o haberla desligado, y su PUT ya contestó
        «Cambios guardados». El veredicto era de la venta de antes: cancelar la
        orden ahora, o dejarla esperando el «¿salió?» sin poder editarla, entregarla
        ni cancelarla, sería por una venta ajena. Y cancelada no se deshace.

        La pasada es la de verdad (`ov_auto.revisar`, nada sustituido de lo que
        decide o escribe); sólo se le mete la corrección justo DESPUÉS de su lectura."""
        leer = ov_auto._leer

        def pasada_tras(lo_que_hace_la_persona, *ordenes) -> tuple:
            def lee_y_luego_la_corrigen() -> list[dict]:
                filas = leer()
                lo_que_hace_la_persona()
                return filas

            with mock.patch.object(ov_auto, "_leer", lee_y_luego_la_corrigen):
                return self.pasada(*ordenes)

        casos = (("la ligan a otra venta, que está viva", "CANCELLED",
                  lambda viva: {"mp_orden": viva}),
                 ("la desligan", "CANCELLED", lambda viva: dict.fromkeys(ov._MP)),
                 ("iba en camino y la ligan a otra venta", "IN_TRANSIT",
                  lambda viva: {"mp_orden": viva}))
        for nombre, dice_el_canal, correccion in casos:
            with self.subTest(nombre):
                o, a, venta = self.orden_ligada()
                viva = f"V-{uuid.uuid4().hex[:10]}"
                self.venta_canal(viva, [(a, 2, 10, False)], estado_canal="AWAITING_SHIPMENT")
                self.el_canal_dice(venta, dice_el_canal)
                corregidas: list[dict] = []
                self.assertEqual(
                    pasada_tras(lambda: corregidas.append(
                        ov.guardar(o["id"], o["rev"], correccion(viva), OPER)["orden"]), o),
                    ([], []), "ni cancelada ni marcada")
                e, = corregidas
                tras = ov.obtener(o["id"], OPER)
                self.assertEqual((tras["estado"], tras["canal_cancelo_at"], tras["rev"],
                                  tras["mp_orden"], tras["piezas_apartadas"]),
                                 ("confirmada", None, e["rev"], e["mp_orden"], 2))
                self.assertEqual((self.saldo(a), self.eventos(o["id"])[-1]), ((5, 2, 3), "editada"),
                                 "su apartado sigue ahí, y lo último que le pasó fue la corrección")
                self.assertTrue(tras["permisos"]["editar"] and tras["permisos"]["entregar"]
                                and tras["permisos"]["cancelar"])
                # La SIGUIENTE pasada ya la lee con su liga de ahora: la venta viva (o
                # ninguna) no la mueve.
                self.assertEqual(self.pasada(o), ([], []))
                self.assertEqual(ov.obtener(o["id"], OPER)["rev"], e["rev"])
        # CONTROL: sin la corrección, esa misma pasada sí la cancela (o la marca).
        o, a, venta = self.orden_ligada()
        self.el_canal_dice(venta, "CANCELLED")
        self.assertEqual(pasada_tras(lambda: None, o), ([self.quedo(o, "cancelada")], []))
        # Y si además de corregirla la ENTREGAN: una entregada ya no cambia, así que
        # su sentencia va sólo por estado; lo que la protege es que se compara la liga.
        o, a, venta = self.orden_ligada()
        viva = f"V-{uuid.uuid4().hex[:10]}"
        self.venta_canal(viva, [(a, 2, 10, False)], estado_canal="AWAITING_SHIPMENT")
        self.el_canal_dice(venta, "CANCELLED")

        def la_corrigen_y_la_entregan() -> None:
            e = ov.guardar(o["id"], o["rev"], {"mp_orden": viva}, OPER)["orden"]
            ov.entregar(o["id"], e["rev"], OPER)

        self.assertEqual(pasada_tras(la_corrigen_y_la_entregan, o), ([], []))
        tras = ov.obtener(o["id"], OPER)
        self.assertEqual((tras["estado"], tras["devolucion_estado"], tras["mp_orden"],
                          self.eventos(o["id"])[-1]), ("entregada", None, viva, "entregada"))
        # Cuando el canal cancele ESA venta —la de ahora—, entonces sí.
        self.el_canal_dice(viva, "CANCELLED")
        self.assertEqual(self.pasada(o), ([self.quedo(o, "entregada_cancelada")], []))
        self.assertEqual(ov.obtener(o["id"], OPER)["cancelada_motivo"],
                         f"Venta cancelada en TikTok ({viva})")

    def test_una_entregada_vieja_ya_no_se_vigila(self):
        o, _, venta = self.orden_ligada("entregada")
        self.el_canal_dice(venta, "CANCELLED")
        with mock.patch.object(ov_auto, "_DIAS_ENTREGADA_VIVA", 0):
            self.assertEqual(self.pasada(o), ([], []), "fuera de la ventana no se cruza")
        self.assertEqual(ov.obtener(o["id"], OPER)["estado"], "entregada")
        self.assertEqual(self.pasada(o), ([self.quedo(o, "entregada_cancelada")], []))

    def test_con_la_bandera_apagada_o_sin_las_tablas_no_toca_nada(self):
        o, a, venta = self.orden_ligada()
        self.el_canal_dice(venta, "CANCELLED")
        self.bandera(ov.BANDERA_MODULO, False)
        with self.assertNoLogs("omnicanal.ov_auto", level="DEBUG"):
            for _ in range(2):
                r = ov_auto.revisar()
        self.assertEqual(r, {"ok": False, "canceladas": [], "marcadas": [],
                             "motivo": str(ov.Apagado())})
        # Sin fila manda el respaldo, que vale false: tampoco.
        self.sql("delete from ops.automatizacion_flags where flag = %s", (ov.BANDERA_MODULO,))
        ov._olvidar_cache()
        self.assertFalse(ov_auto.revisar()["ok"])
        self.assertEqual((ov.obtener(o["id"], OPER)["estado"], self.saldo(a)),
                         ("confirmada", (5, 2, 3)))
        # Encendida pero sin las migraciones: lo dice, una vez, y tampoco toca nada.
        self.bandera(ov.BANDERA_MODULO, True)
        with mock.patch.object(ov, "_leer_tablas", return_value=False):
            ov._olvidar_cache()
            with self.assertLogs("omnicanal.ov_auto", level="WARNING") as logs:
                r = ov_auto.revisar()
            self.assertEqual(r, {"ok": False, "canceladas": [], "marcadas": [],
                                 "motivo": str(ov.FaltaMigracion())})
            self.assertEqual(len(logs.output), 1)
            with self.assertNoLogs("omnicanal.ov_auto", level="INFO"):
                self.assertFalse(ov_auto.revisar()["ok"])
        ov._olvidar_cache()
        self.assertEqual((ov.obtener(o["id"], OPER)["rev"], self.saldo(a)), (o["rev"], (5, 2, 3)))
        # Con la bandera encendida y las tablas, la siguiente pasada sí la cancela.
        self.assertEqual(self.pasada(o), ([self.quedo(o, "cancelada")], []))

    def test_las_dos_lecturas_corren_contra_el_esquema_de_verdad(self):
        """La lectura sin bitácora sólo se usa donde `ops.odoo_sale_orders` no
        existe: si nadie la corre contra Postgres, un error suyo se descubre en
        producción. Y la caída a ella se prueba con un 42P01 DE VERDAD."""
        o, _, venta = self.orden_ligada()
        llaves = {"id", "folio", "estado", "rev", "mp_canal", "mp_cuenta", "mp_orden", "en_canal",
                  "estado_canal", "estado_wc", "accion", "motivo_bitacora"}
        for sql in (ov_auto._SQL_CON_BITACORA, ov_auto._SQL_SIN_BITACORA):
            mia = [f for f in self.sql(sql, {"dias": 45}) if f["id"] == o["id"]]
            self.assertEqual(len(mia), 1)
            self.assertEqual(set(mia[0]), llaves)
            self.assertEqual((mia[0]["mp_orden"], mia[0]["en_canal"], mia[0]["estado_canal"],
                              mia[0]["estado_wc"], mia[0]["accion"]),
                             (venta, True, "AWAITING_SHIPMENT", "processing", None))
        real = sdb.fetch_all
        self.addCleanup(setattr, ov_auto, "_bitacora_avisada", False)
        # Dos formas de «no tener» la bitácora, las dos con el error DE Postgres:
        # la tabla no existe (42P01), o existe con su forma vieja, sin `cuenta` (42703).
        for viejo, nuevo in (("ops.odoo_sale_orders", "ops.tabla_que_no_existe"),
                             ("upper(a.cuenta)", "upper(a.columna_que_no_existe)")):
            vistas: list[bool] = []

            def _sin_bitacora(sql, params=None, viejo=viejo, nuevo=nuevo, vistas=vistas):
                vistas.append("odoo_sale_orders" in sql)
                return real(sql.replace(viejo, nuevo), params)

            ov_auto._bitacora_avisada = False
            with mock.patch.object(ov_auto.sdb, "fetch_all", side_effect=_sin_bitacora), \
                    self.assertLogs("omnicanal.ov_auto", level="WARNING") as logs:
                filas = ov_auto._leer()
            self.assertEqual(vistas, [True, False], "primero con la bitácora; sin ella, la otra")
            self.assertIn(o["id"], [f["id"] for f in filas])
            self.assertIn("Temu", logs.output[0])
        # Y con ella de vuelta, la lectura completa trae su acción.
        self.la_bitacora_dice(venta, "creada", canal="tiktok")
        mia = [f for f in ov_auto._leer() if f["id"] == o["id"]]
        self.assertEqual([f["accion"] for f in mia], ["creada"])
        self.assertEqual(ov_auto._veredictos(mia), [])

    def test_el_barrido_y_el_webhook_a_la_vez_cancelan_una_sola_vez(self):
        """La misma cancelación llega por dos lados (el sondeo y el webhook del
        canal) y dos pasadas se enciman: se cancela UNA vez y nadie truena."""
        o, a, venta = self.orden_ligada()
        self.el_canal_dice(venta, "CANCELLED")
        r = self.a_la_vez(ov_auto.revisar, ov_auto.revisar,
                          lambda: ov.canal_cancelo(o["id"], "CANCELLED", "El comprador canceló",
                                                   False))
        for x in r:
            self.assertNotIsInstance(x, Exception, repr(x))
        del_barrido = [c for x in r[:2] for c in x["canceladas"] if c["id"] == o["id"]]
        self.assertEqual(len(del_barrido) + (r[2]["resultado"] == "cancelada"), 1)
        self.assertIn(r[2]["resultado"], ("cancelada", "ya_cancelada"))
        self.assertEqual(self.eventos(o["id"]).count("cancelada"), 1)
        self.assertEqual(self.saldo(a), (5, 0, 5))
        self.assertEqual(ov_auto.revisar(), NADA_QUE_HACER)


class ProgramadorDePrueba:
    """Apunta lo que `scheduler.iniciar()` registraría. JAMÁS arranca nada."""

    def __init__(self, **kw) -> None:
        self.jobs: dict[str, tuple] = {}

    def add_job(self, fn, trigger, **kw) -> None:
        self.jobs[kw.get("id")] = (fn, trigger, kw)

    def start(self) -> None:
        pass

    def shutdown(self, wait: bool = False) -> None:
        pass


class JobDelBarrido(VentasDelCanal):
    """El job del scheduler de verdad (la función que registra `iniciar()`),
    con la bandera de verdad: una FILA que se enciende y se apaga sin reiniciar."""

    def job(self):
        from services import scheduler
        self.assertIsNone(scheduler._scheduler, "ya había un scheduler vivo en este proceso")
        with mock.patch.object(scheduler, "AsyncIOScheduler", ProgramadorDePrueba):
            scheduler.iniciar()
            try:
                return scheduler._scheduler.jobs["ov_auto"][0]
            finally:
                scheduler.detener()

    def test_la_fila_de_la_bandera_lo_apaga_y_lo_enciende_sin_reiniciar(self):
        fn = self.job()
        o, a, venta = self.orden_ligada()
        self.el_canal_dice(venta, "CANCELLED")
        self.bandera(ov.BANDERA_MODULO, False)
        with self.assertNoLogs("omnicanal.scheduler", level="INFO"), \
                self.assertNoLogs("omnicanal.ov_auto", level="INFO"):
            asyncio.run(fn())
            asyncio.run(fn())
        self.assertEqual((ov.obtener(o["id"], OPER)["estado"], self.saldo(a), ov_bus.version(o["id"])),
                         ("confirmada", (5, 2, 3), 0), "apagada: ni toca ni avisa")
        # La MISMA función registrada, sin reiniciar nada: con la fila encendida corre.
        self.bandera(ov.BANDERA_MODULO, True)
        asyncio.run(fn())
        self.assertEqual((ov.obtener(o["id"], OPER)["estado"], self.saldo(a)),
                         ("cancelada", (5, 0, 5)))
        self.assertGreater(ov_bus.version(o["id"]), 0, "avisó al chat de la orden que movió")
        # …y la que queda esperando el «¿salió?» también avisa.
        o2, _, v2 = self.orden_ligada()
        self.el_canal_dice(v2, "IN_TRANSIT")
        asyncio.run(fn())
        self.assertIsNotNone(ov.obtener(o2["id"], OPER)["canal_cancelo_at"])
        self.assertGreater(ov_bus.version(o2["id"]), 0)


class ApiDePuntaAPunta(VentasDelCanal):
    """La API de verdad: el router, el servicio y la base local, sin un solo
    doble (salvo Storage, que es de la base `Base`). La identidad la pone un
    middleware de prueba, como en producción la pone core/middleware.py; el
    RBAC por prefijo NO está montado, así que cada 403 de aquí es del servicio."""

    def setUp(self) -> None:
        super().setUp()
        with ov._NOMBRES_CANDADO:
            ov._NOMBRES.clear()
        self.sql("""insert into core.usuarios (nombre, email, rol) values
                        ('Olga Operadora', 'oper@prueba.test', 'operador'),
                        ('Ada Admin', 'admin@prueba.test', 'admin')
                    on conflict (email) do nothing""")
        self.identidad: Identidad | None = ID_OPER
        app = FastAPI()
        app.include_router(ruta.router)

        @app.middleware("http")
        async def _identidad(request: Request, call_next):
            if self.identidad is not None:
                request.state.identidad = self.identidad
            return await call_next(request)

        # Con `with`: UN solo loop para toda la prueba (el long-poll lo necesita).
        self.c = TestClient(app)
        self.c.__enter__()
        self.addCleanup(self.c.__exit__, None, None, None)

    # ── ayudantes ────────────────────────────────────────────────────────────
    def ok(self, r, codigo: int = 200) -> dict:
        self.assertEqual(r.status_code, codigo, r.text)
        return r.json()

    def detalle(self, r, codigo: int) -> str:
        self.assertEqual(r.status_code, codigo, r.text)
        return r.json()["detail"]

    def alta(self, lineas: list[dict], **enc) -> dict:
        return self.ok(self.c.post(RAIZ, json={"clave": self.clave(), "lineas": lineas,
                                               **enc}))["orden"]

    def confirmada_http(self, sku: str, n: int, **enc) -> dict:
        o = self.alta([{"sku": sku, "cantidad": n, "precio_unitario": 10, "almacen": "ENSAYO"}],
                      **enc)
        return self.ok(self.c.post(f"{RAIZ}/{o['id']}/confirmar", json={"rev": o["rev"]}))["orden"]

    def hasta(self, condicion, mensaje: str, segundos: float = 5.0) -> None:
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            if condicion():
                return
            time.sleep(0.01)
        self.fail(mensaje)

    # ── el ciclo ─────────────────────────────────────────────────────────────
    def test_el_ciclo_completo_por_http(self):
        a, b = self.sku("A"), self.sku("B")
        est = self.ok(self.c.get(f"{RAIZ}/estado"))
        self.assertEqual((est["ok"], est["falta_migracion"], est["habilitado"]), (True, False, True))
        self.assertEqual(est["yo"], {"actor": "oper@prueba.test", "nombre": "Olga Operadora",
                                     "rol": "operador", "via": "panel", "admin": False,
                                     "escribe": True})
        self.assertEqual([x["codigo"] for x in est["bodegas"] if x["admite_ov"]], ["ENSAYO"])
        self.assertEqual(set(est["banderas"]), {"ordenes_venta", "ov_generacion_auto"})
        self.assertEqual((est["banderas"]["ordenes_venta"]["encendido"],
                          est["banderas"]["ov_generacion_auto"]["encendido"],
                          est["archivos"]["disponible"]), (True, False, False))
        self.assertNotIn("auto", est, "el interruptor movible ya no existe")

        cuerpo = {"clave": self.clave(), "cliente": "directa", "canal": "directa", "lineas": [
            {"sku": a, "cantidad": 3, "precio_unitario": 100, "almacen": "ENSAYO"},
            {"sku": b, "cantidad": 2, "precio_unitario": "50.50"}]}
        r = self.ok(self.c.post(RAIZ, json=cuerpo))
        o = r["orden"]
        self.assertEqual((o["estado"], o["tipo"], o["rev"], o["total"], o["creado_por"],
                          o["creado_nombre"], o["creado_via"]),
                         ("borrador", "venta", 1, 401.0, "oper@prueba.test", "Olga Operadora",
                          "panel"))
        self.assertEqual([l["almacen"] for l in o["lineas"]], ["ENSAYO", None])
        self.assertGreater(ov_bus.version(o["id"]), 0, "el alta avisó al bus")
        self.assertEqual(self.ok(self.c.post(RAIZ, json=cuerpo))["orden"]["id"], o["id"],
                         "el doble clic no crea otra orden")
        url = f"{RAIZ}/{o['id']}"

        # Un renglón sin bodega no se confirma: 400 que dice cuál.
        self.assertIn(f"El renglón de {b} no tiene bodega",
                      self.detalle(self.c.post(f"{url}/confirmar", json={"rev": 1}), 400))
        # PUT = guardar el borrador (lo que no se manda no se toca).
        g = self.ok(self.c.put(url, json={"rev": 1, "descripcion": "Pedido de mostrador", "lineas": [
            {"sku": a, "cantidad": 3, "precio_unitario": 100, "almacen": "ENSAYO"},
            {"sku": b, "cantidad": 2, "precio_unitario": 50.5, "almacen": "ENSAYO"}]}))["orden"]
        self.assertEqual((g["rev"], g["descripcion"], g["cliente"], g["total"],
                          [l["almacen"] for l in g["lineas"]]),
                         (2, "Pedido de mostrador", "directa", 401.0, ["ENSAYO", "ENSAYO"]))
        self.detalle(self.c.put(url, json={"rev": 1, "guia": "GUIA-PRUEBA-1"}), 409)   # rev vieja

        # Confirmar es TODO O NADA: sin saldo de `b`, 409 que dice cuál y nada apartado.
        self.siembra(a, 5)
        texto = self.detalle(self.c.post(f"{url}/confirmar", json={"rev": 2}), 409)
        self.assertEqual(texto, f"No alcanzó el stock para apartar: {b} pide 2 y no tiene "
                                "existencias registradas en ENSAYO. No se apartó nada.")
        self.assertEqual(self.saldo(a), (5, 0, 5))
        self.siembra(b, 2)
        c = self.ok(self.c.post(f"{url}/confirmar", json={"rev": 2}))["orden"]
        self.assertEqual((c["estado"], c["rev"], c["piezas_apartadas"], c["confirmada_nombre"]),
                         ("confirmada", 3, 5, "Olga Operadora"))
        self.assertEqual((self.saldo(a), self.saldo(b)), ((5, 3, 2), (2, 2, 0)))

        # (El PUT sobre la CONFIRMADA —editarla— tiene su prueba: test_editar_y_cancelar_
        # una_confirmada_por_http. Aquí la orden sigue su camino sin correcciones.)
        self.assertTrue(c["permisos"]["editar"] and c["permisos"]["cancelar"])

        # Entregar CON lineas: de `a` salieron 2 de 3 (la otra se suelta); `b` sigue pendiente.
        la, lb = c["lineas"]
        for malas in ([{"id": 99999999, "n": 1}], [{"id": la["id"], "n": 4}], [],
                      [{"id": la["id"], "n": True}], [{"id": la["id"], "n": 1.5}]):
            self.detalle(self.c.post(f"{url}/entregar", json={"rev": 3, "lineas": malas}), 400)
        self.assertEqual((self.ok(self.c.get(url))["rev"], self.saldo(a)), (3, (5, 3, 2)),
                         "ninguna de las malas movió nada")
        e = self.ok(self.c.post(f"{url}/entregar",
                                json={"rev": 3, "lineas": [{"id": la["id"], "n": 2}]}))
        self.assertIn("Entrega parcial", e["mensaje"])
        e = e["orden"]
        self.assertEqual((e["estado"], e["rev"], e["piezas_entregadas"], e["renglones_entregados"],
                          e["piezas_apartadas"]), ("confirmada", 4, 2, 1, 2))
        self.assertEqual((self.saldo(a), self.saldo(b)), ((3, 0, 3), (2, 2, 0)))
        # Entregar SIN lineas: sale completo todo lo que quedaba.
        f = self.ok(self.c.post(f"{url}/entregar", json={"rev": 4}))["orden"]
        self.assertEqual((f["estado"], f["rev"], f["piezas_entregadas"], f["renglones_entregados"],
                          f["entregada_por"]), ("entregada", 5, 4, 2, "oper@prueba.test"))
        self.assertEqual((self.saldo(a), self.saldo(b)), ((3, 0, 3), (0, 0, 0)))
        self.assertEqual(self.libro(a), [("correccion", 5, 5), ("salida_ov", -2, 3)])

        # ENTREGADA, el PUT ya no entra: 400 con su porqué, y nada cambia.
        texto = self.detalle(self.c.put(url, json={"rev": 5, "guia": "GUIA-PRUEBA-1"}), 400)
        self.assertIn("su contenido no cambia", texto)
        self.assertEqual((self.ok(self.c.get(url))["rev"], f["permisos"]["editar"]), (5, False))

        # El detalle por folio, la lista y la bitácora completa.
        self.assertEqual(self.ok(self.c.get(f"{RAIZ}/{o['folio'].lower()}"))["id"], o["id"])
        lista = self.ok(self.c.get(RAIZ, params={"q": o["folio"], "estado": "entregada"}))
        self.assertEqual([x["id"] for x in lista["ordenes"]], [o["id"]])
        self.assertEqual(set(lista["conteos"]), set(ov.FILTROS))
        chat = self.ok(self.c.get(f"{url}/mensajes"))
        self.assertEqual([m["evento"] for m in chat["mensajes"]],
                         ["creada", "borrador_guardado", "no_alcanzo", "confirmada",
                          "entregada_parcial", "entregada"])
        self.assertEqual((chat["rev"], chat["estado"]), (5, "entregada"))

    # ── editar una confirmada ────────────────────────────────────────────────
    def test_editar_y_cancelar_una_confirmada_por_http(self):
        """El mismo PUT del borrador, sobre una CONFIRMADA (contrato con la pantalla):
        200 con RespOrden; 409 con su `detail` cuando no alcanza —y la `rev` NO
        cambia: así la pantalla lo distingue de «la orden cambió»—; 400 en las
        validaciones; y el renglón `editada` se ve EN VIVO en el chat de los demás."""
        a = self.sku("A")
        self.siembra(a, 5)
        o = self.confirmada_http(a, 2)
        url = f"{RAIZ}/{o['id']}"
        self.assertTrue(o["permisos"]["editar"] and o["permisos"]["cancelar"],
                        "al operador que la confirmó se le ofrece corregirla y cancelarla")
        # 1) El encabezado. Otra persona tiene el chat abierto y esperando: despierta
        #    con el renglón `editada`, sin esperar a que venza su petición.
        chat = self.ok(self.c.get(f"{url}/mensajes"))
        salida: dict = {}
        hilo = threading.Thread(target=lambda: salida.update(r=self.c.get(
            f"{url}/mensajes", params={"desde_id": chat["ultimo_id"], "esperar": 20})))
        hilo.start()
        self.hasta(lambda: ov_bus.en_espera() == 1, "la petición nunca llegó a esperar")
        t0 = time.monotonic()
        g = self.ok(self.c.put(url, json={"rev": o["rev"], "guia": "GUIA-PRUEBA-1",
                                          "paqueteria": "Paquetería de prueba"}))
        self.assertEqual(set(g), {"ok", "orden", "mensaje"})
        self.assertEqual(g["mensaje"], "Cambios guardados.")
        g = g["orden"]
        self.assertEqual((g["estado"], g["rev"], g["guia"], g["paqueteria"], g["piezas_apartadas"]),
                         ("confirmada", o["rev"] + 1, "GUIA-PRUEBA-1", "Paquetería de prueba", 2))
        hilo.join(15)
        self.assertFalse(hilo.is_alive(), "la edición no despertó a quien esperaba")
        self.assertLess(time.monotonic() - t0, 10, "despertó por el aviso, no porque venciera")
        vivo = salida["r"].json()
        self.assertEqual(([m["evento"] for m in vivo["mensajes"]], vivo["rev"], vivo["estado"]),
                         (["editada"], g["rev"], "confirmada"))
        self.assertEqual(vivo["mensajes"][0]["datos"],
                         {"cambios": {"guia": [None, "GUIA-PRUEBA-1"],
                                      "paqueteria": [None, "Paquetería de prueba"]}})
        # 2) Los renglones: sube hasta lo que alcanza, y el apartado se mueve.
        linea = {"sku": a, "cantidad": 5, "precio_unitario": 10, "almacen": "ENSAYO"}
        h = self.ok(self.c.put(url, json={"rev": g["rev"], "total": None, "lineas": [linea]}))["orden"]
        self.assertEqual((h["rev"], h["piezas_apartadas"], h["total"], self.saldo(a)),
                         (g["rev"] + 1, 5, 50.0, (5, 5, 0)))
        # 3) No alcanza: 409 con el detalle, la rev NO se movió, el chat no tiene nada
        #    nuevo y —a diferencia de confirmar— no hay a quién avisar.
        bus = ov_bus.version(o["id"])
        texto = self.detalle(self.c.put(url, json={"rev": h["rev"], "guia": "GUIA-QUE-NO-ENTRA",
                                                   "lineas": [{**linea, "cantidad": 6}]}), 409)
        self.assertEqual(texto, f"No alcanzó el stock para guardar el cambio: {a} necesita 1 más y "
                                "hay 0 libres en ENSAYO. No se guardó nada.")
        tras = self.ok(self.c.get(url))
        self.assertEqual((tras["rev"], tras["guia"], tras["piezas"], tras["n_mensajes"],
                          self.saldo(a), ov_bus.version(o["id"])),
                         (h["rev"], "GUIA-PRUEBA-1", 5, h["n_mensajes"], (5, 5, 0), bus))
        # 4) La rev vieja SÍ es «la orden cambió»; y las validaciones son 400 con palabras.
        self.assertEqual(self.detalle(self.c.put(url, json={"rev": o["rev"], "guia": "x"}), 409),
                         "La orden cambió mientras tanto; se recargó.")
        for cuerpo, dice in (({"lineas": []}, "al menos un renglón por entregar"),
                             ({"lineas": [{**linea, "almacen": None}]}, "no tiene bodega"),
                             ({"lineas": [{**linea, "almacen": "TEX3"}]}, "no admite órdenes de venta"),
                             ({"lineas": [{**linea, "cantidad": 2.5}]}, "la cantidad debe ser un entero"),
                             ({"canal": "facebook"}, "El canal debe ser uno de")):
            self.assertIn(dice, self.detalle(self.c.put(url, json={"rev": h["rev"], **cuerpo}), 400))
        # 5) Quien sólo lee no corrige ni cancela: 403 del servicio, con su porqué.
        self.identidad = ID_LECT
        self.assertIn("sólo lectura", self.detalle(
            self.c.put(url, json={"rev": h["rev"], "guia": "x"}), 403))
        self.assertFalse(self.ok(self.c.get(url))["permisos"]["editar"])
        self.identidad = ID_ADMIN                       # el administrador, igual que el operador
        i = self.ok(self.c.put(url, json={"rev": h["rev"], "lineas": [{**linea, "cantidad": 3}]}))["orden"]
        self.assertEqual((i["piezas_apartadas"], self.saldo(a)), (3, (5, 3, 2)))
        # 6) La bitácora completa, con quién hizo cada cambio y sin la marca interna.
        eventos = self.ok(self.c.get(f"{url}/mensajes"))["mensajes"]
        self.assertEqual([m["evento"] for m in eventos],
                         ["creada", "confirmada", "editada", "editada", "editada"])
        self.assertEqual([(m["autor"], m["autor_nombre"], m["via"]) for m in eventos[2:]],
                         [("oper@prueba.test", "Olga Operadora", "panel")] * 2
                         + [("admin@prueba.test", "Ada Admin", "panel")])
        self.assertEqual((eventos[3]["datos"]["apartado"], eventos[4]["datos"]["apartado"]),
                         ([{"sku": a, "almacen": "ENSAYO", "delta": 3}],
                          [{"sku": a, "almacen": "ENSAYO", "delta": -2}]))
        for m in eventos:
            self.assertNotIn("op", m["datos"] or {})
        # 7) El OPERADOR la cancela (ya no hace falta un administrador), con su motivo;
        #    cancelada, el PUT ya no entra.
        self.identidad = ID_OPER
        c = self.ok(self.c.post(f"{url}/cancelar", json={
            "rev": i["rev"], "motivo": "El cliente ya no la quiso"}))["orden"]
        self.assertEqual((c["estado"], c["cancelada_por"], c["cancelada_nombre"], self.saldo(a)),
                         ("cancelada", "oper@prueba.test", "Olga Operadora", (5, 0, 5)))
        self.assertIn("su contenido no cambia", self.detalle(
            self.c.put(url, json={"rev": c["rev"], "guia": "GUIA-TARDE"}), 400))

    # ── el canal cancela ─────────────────────────────────────────────────────
    def test_el_canal_cancela_y_bodega_contesta_por_http(self):
        a = self.sku("A")
        venta = f"V-{uuid.uuid4().hex[:10]}"
        self.venta_canal(venta, [(a, 2, 10, False)], estado_canal="AWAITING_SHIPMENT")
        self.siembra(a, 5)
        o = self.confirmada_http(a, 2, mp_canal="tiktok", mp_orden=venta,
                                 precio_origen="marketplace")
        self.assertEqual(o["mp_cuenta"], CUENTA, "la cuenta se completó sola desde la venta")
        url = f"{RAIZ}/{o['id']}"
        r = self.ok(self.c.post(f"{RAIZ}/conciliar"))
        self.assertTrue(r["ok"])
        self.assertNotIn(o["id"], [x["id"] for x in r["canceladas"] + r["marcadas"]])

        self.el_canal_dice(venta, "IN_TRANSIT")
        self.identidad = ID_LECT
        self.detalle(self.c.post(f"{RAIZ}/conciliar"), 403)
        self.identidad = ID_OPER
        antes = ov_bus.version(o["id"])
        r = self.ok(self.c.post(f"{RAIZ}/conciliar"))
        self.assertEqual(set(r), {"ok", "canceladas", "marcadas"})
        self.assertEqual([x for x in r["marcadas"] if x["id"] == o["id"]],
                         [{"id": o["id"], "folio": o["folio"]}])
        self.assertGreater(ov_bus.version(o["id"]), antes, "conciliar avisó al chat de la marcada")
        m = self.ok(self.c.get(url))
        self.assertEqual((m["estado"], m["canal_cancelo_ref"], m["permisos"]["responder_salio"],
                          m["permisos"]["entregar"]), ("confirmada", "IN_TRANSIT", True, False))
        # Con la marca no se puede marcar DELIVERED: primero se contesta.
        self.assertIn("contestar si salió",
                      self.detalle(self.c.post(f"{url}/entregar", json={"rev": m["rev"]}), 400))
        # «sí», 1 y null no son una respuesta (llegan tal cual y el servicio los rechaza).
        for mal in ("sí", 1, None):
            self.assertIn("verdadero", self.detalle(
                self.c.post(f"{url}/salio", json={"rev": m["rev"], "salio": mal}), 400))
        self.identidad = ID_LECT
        self.detalle(self.c.post(f"{url}/salio", json={"rev": m["rev"], "salio": True}), 403)
        self.identidad = ID_OPER
        self.assertEqual(self.saldo(a), (5, 2, 3), "nada de lo anterior movió el saldo")
        f = self.ok(self.c.post(f"{url}/salio", json={"rev": m["rev"], "salio": True}))["orden"]
        self.assertEqual((f["estado"], f["devolucion_estado"], f["piezas_entregadas"],
                          f["entregada_por"], f["cancelada_por"], f["cancelada_origen"]),
                         ("entregada_cancelada", "pendiente", 2, "oper@prueba.test", "automatico",
                          "marketplace"))
        self.assertEqual(self.saldo(a), (3, 0, 3))
        self.detalle(self.c.post(f"{url}/salio", json={"rev": f["rev"], "salio": True}), 400)
        r = self.ok(self.c.post(f"{RAIZ}/conciliar"))
        self.assertNotIn(o["id"], [x["id"] for x in r["canceladas"] + r["marcadas"]])

    def test_no_salio_y_salio_tarde_por_http(self):
        a, b = self.sku("A"), self.sku("B")
        v1, v2 = (f"V-{uuid.uuid4().hex[:10]}" for _ in range(2))
        for sku, venta in ((a, v1), (b, v2)):
            self.venta_canal(venta, [(sku, 2, 10, False)], estado_canal="AWAITING_SHIPMENT")
            self.siembra(sku, 5)
        o1 = self.confirmada_http(a, 2, mp_canal="tiktok", mp_cuenta=CUENTA, mp_orden=v1)
        o2 = self.confirmada_http(b, 2, mp_canal="tiktok", mp_cuenta=CUENTA, mp_orden=v2)
        self.el_canal_dice(v1, "AWAITING_COLLECTION")
        self.el_canal_dice(v2, "CANCELLED")
        r = self.ok(self.c.post(f"{RAIZ}/conciliar"))
        self.assertEqual([x for x in r["marcadas"] if x["id"] == o1["id"]],
                         [{"id": o1["id"], "folio": o1["folio"]}])
        self.assertEqual([x for x in r["canceladas"] if x["id"] == o2["id"]],
                         [{"id": o2["id"], "folio": o2["folio"], "estado": "cancelada"}])

        # 1) Bodega contesta que NO salió: cancelada, y el apartado se suelta.
        m = self.ok(self.c.get(f"{RAIZ}/{o1['id']}"))
        c = self.ok(self.c.post(f"{RAIZ}/{o1['id']}/salio",
                                json={"rev": m["rev"], "salio": False}))["orden"]
        self.assertEqual((c["estado"], c["cancelada_origen"], c["cancelada_por"],
                          c["devolucion_estado"]),
                         ("cancelada", "marketplace", "oper@prueba.test", None))
        self.assertEqual(self.saldo(a), (5, 0, 5))

        # 2) La que el barrido canceló resulta que SÍ había salido: «salió tarde», de admin.
        x = self.ok(self.c.get(f"{RAIZ}/{o2['id']}"))
        self.assertEqual((x["estado"], x["cancelada_origen"], x["permisos"]["salio_tarde"]),
                         ("cancelada", "marketplace", False))
        self.assertEqual(self.saldo(b), (5, 0, 5))
        self.assertIn("administrador", self.detalle(
            self.c.post(f"{RAIZ}/{o2['id']}/salio-tarde", json={"rev": x["rev"]}), 403))
        self.identidad = ID_ADMIN
        self.detalle(self.c.post(f"{RAIZ}/{o2['id']}/salio-tarde", json={"rev": x["rev"] + 7}), 409)
        t = self.ok(self.c.post(f"{RAIZ}/{o2['id']}/salio-tarde", json={"rev": x["rev"]}))["orden"]
        self.assertEqual((t["estado"], t["devolucion_estado"], t["entregada_por"],
                          t["piezas_entregadas"]),
                         ("entregada_cancelada", "pendiente", "admin@prueba.test", 2))
        self.assertEqual(self.saldo(b), (3, 0, 3), "la salida tardía baja el físico")
        self.assertEqual(self.libro(b)[-1], ("salida_ov", -2, 3))
        # Un borrador cancelado nunca estuvo confirmado: no pudo salir.
        d = self.alta([])
        d = self.ok(self.c.post(f"{RAIZ}/{d['id']}/cancelar", json={"rev": d["rev"]}))["orden"]
        self.assertIn("nunca estuvo confirmada", self.detalle(
            self.c.post(f"{RAIZ}/{d['id']}/salio-tarde", json={"rev": d["rev"]}), 400))

    # ── cancelar y borrar ────────────────────────────────────────────────────
    def test_cancelar_y_borrar_por_http(self):
        a = self.sku("A")
        self.siembra(a, 6)
        o = self.confirmada_http(a, 2)
        url = f"{RAIZ}/{o['id']}"
        # Cancelar una CONFIRMADA ya no es de administrador: la cancela quien escribe
        # (el piso del RBAC para ese POST es `operador`). Quien sólo lee, no.
        self.assertTrue(o["permisos"]["cancelar"])
        self.identidad = ID_LECT
        self.assertIn("sólo lectura", self.detalle(
            self.c.post(f"{url}/cancelar", json={"rev": o["rev"], "motivo": "ya no la quiso"}), 403))
        self.identidad = ID_OPER
        for corto in ({}, {"motivo": ""}, {"motivo": "no"}):                 # falta el motivo
            self.assertIn("5 caracteres", self.detalle(
                self.c.post(f"{url}/cancelar", json={"rev": o["rev"], **corto}), 400))
        self.assertEqual(self.saldo(a), (6, 2, 4), "ningún rechazo soltó nada")
        c = self.ok(self.c.post(f"{url}/cancelar", json={
            "rev": o["rev"], "motivo": "El cliente ya no la quiso", "origen": "marketplace"}))["orden"]
        self.assertEqual((c["estado"], c["cancelada_origen"], c["cancelada_por"],
                          c["cancelada_nombre"], c["cancelada_motivo"]),
                         ("cancelada", "manual", "oper@prueba.test", "Olga Operadora",
                          "El cliente ya no la quiso"), "desde la API siempre es MANUAL")
        self.assertEqual(self.saldo(a), (6, 0, 6))

        o2 = self.confirmada_http(a, 3)
        url2 = f"{RAIZ}/{o2['id']}"
        self.identidad = ID_OPER
        self.assertIn("administrador", self.detalle(
            self.c.delete(url2, params={"rev": o2["rev"], "motivo": "Capturada por error"}), 403))
        self.identidad = ID_ADMIN
        self.assertIn("10 caracteres", self.detalle(
            self.c.delete(url2, params={"rev": o2["rev"], "motivo": "corto"}), 400))
        self.assertEqual(self.saldo(a), (6, 3, 3))
        # `rev` y `motivo` en el CUERPO (la dirección queda sólo por compatibilidad).
        b = self.ok(self.c.request("DELETE", url2, json={
            "rev": o2["rev"], "motivo": "Capturada por error"}))["orden"]
        self.assertEqual((b["borrada_por"], b["borrada_nombre"], b["borrada_motivo"],
                          b["piezas_apartadas"]),
                         ("admin@prueba.test", "Ada Admin", "Capturada por error", 0))
        self.assertIsNotNone(b["borrada_at"])
        self.assertEqual(self.saldo(a), (6, 0, 6), "borrar soltó el apartado en la misma sentencia")
        # No se elimina: sigue ahí, en su filtro, y ya no se mueve.
        self.assertIsNotNone(self.ok(self.c.get(url2))["borrada_at"])
        borradas = self.ok(self.c.get(RAIZ, params={"estado": "borradas", "q": b["folio"]}))
        self.assertEqual([x["id"] for x in borradas["ordenes"]], [o2["id"]])
        self.assertEqual(self.ok(self.c.get(RAIZ, params={"q": b["folio"]}))["ordenes"], [])
        self.detalle(self.c.post(f"{url2}/cancelar", json={"rev": b["rev"], "motivo": "otra vez"}), 400)
        self.detalle(self.c.delete(url2, params={"rev": b["rev"], "motivo": "Capturada por error"}),
                     400)

    # ── PDF ──────────────────────────────────────────────────────────────────
    def test_los_pdf_con_tipo_y_sin_bucket_por_http(self):
        o = self.alta([{"sku": self.sku("A"), "cantidad": 1, "almacen": "ENSAYO"}])
        url = f"{RAIZ}/{o['id']}/archivos"
        pdf = {"pdf": ("Factura ñ.pdf", PDF, "application/pdf")}
        # SIN el bucket no hay dónde guardar: 409 que lo dice, y nada sube.
        self.assertIn("falta crear el bucket",
                      self.detalle(self.c.post(url, data={"tipo": "factura"}, files=pdf), 409))
        self.assertEqual(self.storage.subidas, 0)
        self.sql("insert into storage.buckets (id, name) values (%s, %s)", (ov.BUCKET, ov.BUCKET))
        ov._olvidar_cache()
        self.assertEqual(self.ok(self.c.get(f"{RAIZ}/estado"))["archivos"],
                         {"disponible": True, "motivo": None})
        # El TIPO es obligatorio y sólo esos tres: una guía no se guarda aquí.
        for data in ({}, {"tipo": "guia"}, {"tipo": ""}):
            texto = self.detalle(self.c.post(url, data=data, files=pdf), 400)
            self.assertIn("comprobante, factura", texto)
            self.assertIn("NO se guardan", texto)
        self.assertEqual(self.storage.subidas, 0)
        s = self.ok(self.c.post(url, data={"tipo": "factura"}, files=pdf))
        archivo, = s["orden"]["archivos"]
        self.assertEqual((archivo["tipo"], archivo["nombre"], archivo["bytes"], archivo["subido_por"],
                          archivo["sha256"], s["orden"]["rev"]),
                         ("factura", "Factura ñ.pdf", len(PDF), "oper@prueba.test",
                          hashlib.sha256(PDF).hexdigest(), o["rev"] + 1))
        self.assertEqual(list(self.storage.objetos), [f"{o['folio']}/{archivo['sha256']}.pdf"])
        s2 = self.ok(self.c.post(url, data={"tipo": "factura"}, files=pdf))
        self.assertEqual((s2["mensaje"], len(s2["orden"]["archivos"])),
                         ("Ese PDF ya estaba adjunto.", 1))
        # Bajar: por el backend, con sesión de quien opera.
        d = self.c.get(f"{url}/{archivo['id']}")
        self.assertEqual((d.status_code, d.content, d.headers["content-type"]),
                         (200, PDF, "application/pdf"))
        self.assertIn("filename*=UTF-8''Factura%20%C3%B1.pdf", d.headers["content-disposition"])
        self.identidad = ID_LECT
        self.detalle(self.c.get(f"{url}/{archivo['id']}"), 403)
        # Quitar: sólo admin.
        self.identidad = ID_OPER
        self.assertIn("administrador", self.detalle(self.c.delete(f"{url}/{archivo['id']}"), 403))
        self.identidad = ID_ADMIN
        q = self.ok(self.c.delete(f"{url}/{archivo['id']}"))
        self.assertEqual((q["orden"]["archivos"], self.storage.objetos), ([], {}))
        self.detalle(self.c.get(f"{url}/{archivo['id']}"), 404)
        self.assertEqual([m["evento"] for m in self.ok(self.c.get(
            f"{RAIZ}/{o['id']}/mensajes"))["mensajes"]], ["creada", None, None],
            "el aviso de PDF es del sistema y SIN evento (no está en el catálogo cerrado)")

    # ── chat en vivo ─────────────────────────────────────────────────────────
    def test_el_chat_en_vivo_por_http(self):
        o = self.alta([])
        url = f"{RAIZ}/{o['id']}/mensajes"
        todo = self.ok(self.c.get(url))
        self.assertEqual([m["evento"] for m in todo["mensajes"]], ["creada"])

        def esperar_desde(ultimo: int) -> tuple[threading.Thread, dict]:
            salida: dict = {}

            def _pedir() -> None:
                t0 = time.monotonic()
                salida["r"] = self.c.get(url, params={"desde_id": ultimo, "esperar": 20})
                salida["t"] = time.monotonic() - t0

            hilo = threading.Thread(target=_pedir)
            hilo.start()
            self.hasta(lambda: ov_bus.en_espera() == 1, "la petición nunca llegó a esperar")
            return hilo, salida

        # 1) Otra persona escribe: quien esperaba despierta con ESE mensaje.
        hilo, salida = esperar_desde(todo["ultimo_id"])
        self.identidad = ID_ADMIN
        clave = f"k-{self.t}"
        enviado = self.ok(self.c.post(url, json={"cuerpo": "¿ya salió?", "clave": clave}))
        hilo.join(15)
        self.assertFalse(hilo.is_alive(), "el mensaje no despertó a quien esperaba")
        self.assertLess(salida["t"], 10, "despertó por el aviso, no porque venciera")
        nuevos = salida["r"].json()["mensajes"]
        self.assertEqual([(m["cuerpo"], m["tipo"], m["autor"], m["autor_nombre"]) for m in nuevos],
                         [("¿ya salió?", "usuario", "admin@prueba.test", "Ada Admin")])
        self.assertEqual(ov_bus.en_espera(), 0)
        # El reenvío con la misma clave no lo duplica.
        otra = self.ok(self.c.post(url, json={"cuerpo": "¿ya salió?", "clave": clave}))
        self.assertEqual(otra["mensajes"][0]["id"], enviado["mensajes"][0]["id"])
        self.assertEqual(otra["total"], 2)

        # 2) Una TRANSICIÓN también despierta, y trae la rev y el estado nuevos.
        hilo, salida = esperar_desde(otra["ultimo_id"])
        self.ok(self.c.post(f"{RAIZ}/{o['id']}/cancelar", json={"rev": o["rev"]}))
        hilo.join(15)
        self.assertFalse(hilo.is_alive())
        r = salida["r"].json()
        self.assertEqual(([m["evento"] for m in r["mensajes"]], r["rev"], r["estado"]),
                         (["cancelada"], o["rev"] + 1, "cancelada"))

        # 3) Sin nada nuevo vence, relee y contesta vacío.
        t0 = time.monotonic()
        vacio = self.ok(self.c.get(url, params={"desde_id": r["ultimo_id"], "esperar": 0.4}))
        self.assertGreaterEqual(time.monotonic() - t0, 0.35)
        self.assertEqual((vacio["mensajes"], vacio["total"]), ([], 3))
        # 4) Lo que no existe no se espera; y quien sólo lee no escribe.
        t0 = time.monotonic()
        self.detalle(self.c.get(f"{RAIZ}/99999999/mensajes", params={"esperar": 20}), 404)
        self.assertLess(time.monotonic() - t0, 5)
        self.identidad = ID_LECT
        self.detalle(self.c.post(url, json={"cuerpo": "hola"}), 403)
        self.assertEqual(self.ok(self.c.get(url))["total"], 3, "leer sí puede")

    # ── modo prueba y sin migración ──────────────────────────────────────────
    def test_modo_prueba_y_sin_migracion_por_http(self):
        a = self.sku("A")
        self.siembra(a, 3)
        self.bandera(ov.BANDERA_MODULO, False)
        est = self.ok(self.c.get(f"{RAIZ}/estado"))
        self.assertEqual((est["ok"], est["habilitado"],
                          est["banderas"]["ordenes_venta"]["persistido"]), (True, False, True))
        # Apagada = modo prueba: los BORRADORES sí; confirmar, no.
        o = self.alta([{"sku": a, "cantidad": 2, "precio_unitario": 10, "almacen": "ENSAYO"}])
        url = f"{RAIZ}/{o['id']}"
        g = self.ok(self.c.put(url, json={"rev": o["rev"], "guia": "GUIA-PRUEBA-2"}))["orden"]
        self.assertIn("modo prueba",
                      self.detalle(self.c.post(f"{url}/confirmar", json={"rev": g["rev"]}), 409))
        self.assertEqual(self.saldo(a), (3, 0, 3))
        self.assertFalse(self.ok(self.c.get(url))["permisos"]["confirmar"])
        r = self.ok(self.c.post(f"{RAIZ}/conciliar"))
        self.assertEqual((r["ok"], r["canceladas"], r["marcadas"]), (False, [], []))
        self.assertIn("modo prueba", r["motivo"])
        self.ok(self.c.post(f"{url}/cancelar", json={"rev": g["rev"]}))    # cancelar sí se puede

        # Sin las migraciones 0064/0065 nada truena: cada ruta lo dice.
        self.bandera(ov.BANDERA_MODULO, True)
        with mock.patch.object(ov, "_leer_tablas", return_value=False):
            ov._olvidar_cache()
            est = self.ok(self.c.get(f"{RAIZ}/estado"))
            self.assertEqual((est["ok"], est["falta_migracion"]), (False, True))
            self.assertIn("0064", est["motivo"])
            lista = self.ok(self.c.get(RAIZ))
            self.assertEqual((lista["ok"], lista["falta_migracion"], lista["ordenes"]),
                             (False, True, []))
            self.assertIn("0064", self.detalle(self.c.post(RAIZ, json={"lineas": []}), 409))
            self.assertIn("0064", self.detalle(self.c.get(url), 409))
            with self.assertLogs("omnicanal.ov_auto", level="WARNING"):
                r = self.ok(self.c.post(f"{RAIZ}/conciliar"))
            self.assertEqual(r["ok"], False)
            self.assertIn("0064", r["motivo"])
        ov._olvidar_cache()
        self.assertEqual(self.ok(self.c.get(url))["estado"], "cancelada")

    # ── lo que no existe, los buscadores y quién queda escrito ───────────────
    def test_lo_que_no_existe_es_404_en_toda_ruta(self):
        self.identidad = ID_ADMIN               # que el motivo no sea el permiso
        n = 99999999
        casos = (
            ("get", f"{RAIZ}/{n}", {}), ("get", f"{RAIZ}/OV-{n}", {}),
            ("get", f"{RAIZ}/no-es-un-folio", {}),
            ("put", f"{RAIZ}/{n}", {"json": {"rev": 1, "guia": "x"}}),
            ("post", f"{RAIZ}/{n}/confirmar", {"json": {"rev": 1}}),
            ("post", f"{RAIZ}/{n}/entregar", {"json": {"rev": 1}}),
            ("post", f"{RAIZ}/{n}/cancelar", {"json": {"rev": 1, "motivo": "no existe"}}),
            ("post", f"{RAIZ}/{n}/salio", {"json": {"rev": 1, "salio": True}}),
            ("post", f"{RAIZ}/{n}/salio-tarde", {"json": {"rev": 1}}),
            ("delete", f"{RAIZ}/{n}", {"params": {"rev": 1, "motivo": "Capturada por error"}}),
            ("get", f"{RAIZ}/{n}/mensajes", {}),
            ("post", f"{RAIZ}/{n}/mensajes", {"json": {"cuerpo": "hola"}}),
            ("post", f"{RAIZ}/{n}/archivos", {"data": {"tipo": "factura"},
                                             "files": {"pdf": ("x.pdf", PDF)}}),
            ("get", f"{RAIZ}/{n}/archivos/1", {}),
            ("delete", f"{RAIZ}/{n}/archivos/1", {}))
        for metodo, url, kw in casos:
            with self.subTest(metodo=metodo, url=url):
                self.detalle(getattr(self.c, metodo)(url, **kw), 404)

    def test_los_buscadores_y_la_lista_por_http(self):
        a = self.sku("A")
        self.sql("insert into core.products (sku, name) values (%s, 'Producto de prueba')", (a,))
        self.siembra(a, 7)
        self.assertEqual(self.ok(self.c.get(f"{RAIZ}/skus", params={"q": a}))["opciones"], [
            {"sku": a, "nombre": "Producto de prueba",
             "existencias": [{"almacen": "ENSAYO", "fisico": 7, "apartado": 0, "libre": 7}]}])
        self.assertEqual(self.ok(self.c.get(f"{RAIZ}/skus", params={"q": "z"}))["opciones"], [])
        venta = f"V-{uuid.uuid4().hex[:10]}"
        self.venta_canal(venta, [(a, 2, 150, False)], total=300, comision=30)
        v = self.ok(self.c.get(f"{RAIZ}/marketplace/venta", params={"orden": venta,
                                                                    "canal": "tiktok"}))
        self.assertEqual((v["ok"], [(x["cuenta"], x["total"], x["neto"], x["ov"])
                                    for x in v["ventas"]]), (True, [(CUENTA, 300.0, 270.0, None)]))
        pend = self.ok(self.c.get(f"{RAIZ}/marketplace/pendientes",
                                  params={"dias": 1, "canal": "tiktok"}))
        self.assertIn(venta, {x["orden"] for x in pend["ventas"]})
        o = self.alta(v["ventas"][0]["lineas"], mp_canal="tiktok", mp_orden=venta)
        v = self.ok(self.c.get(f"{RAIZ}/marketplace/venta", params={"orden": venta}))
        self.assertEqual(v["ventas"][0]["ov"],
                         {"id": o["id"], "folio": o["folio"], "estado": "borrador"})
        # La misma venta otra vez: 409 que dice el folio de la que ya la tiene.
        r = self.c.post(RAIZ, json={"clave": self.clave(), "mp_canal": "tiktok", "mp_orden": venta,
                                    "lineas": []})
        self.assertIn(o["folio"], self.detalle(r, 409))
        pagina = self.ok(self.c.get(RAIZ, params={"estado": "borrador", "por_pagina": 5}))
        self.assertEqual((pagina["ok"], pagina["por_pagina"], pagina["pagina"]), (True, 5, 1))
        self.assertLessEqual(len(pagina["ordenes"]), 5)
        self.detalle(self.c.get(RAIZ, params={"estado": "inventado"}), 400)

    def test_quien_queda_escrito_segun_por_donde_entra(self):
        """La `via` que arma el router pasa el CHECK de la base (panel | api |
        claude | automatico) y el actor nunca va vacío: queda escrito en la orden
        y en su bitácora."""
        casos = ((ID_OPER, {}, ("oper@prueba.test", "Olga Operadora", "panel")),
                 (ID_MAQUINA, {}, ("servicio", "API", "api")),
                 (ID_MAQUINA, {"X-Origen": "claude"}, ("servicio", "Claude", "claude")),
                 (ID_MAQUINA, {"User-Agent": "claude-cli/1.0 (external)"},
                  ("servicio", "Claude", "claude")),
                 # Una persona no se vuelve «Claude» por mandar la cabecera.
                 (ID_OPER, {"X-Origen": "claude"}, ("oper@prueba.test", "Olga Operadora", "panel")),
                 # Sin fila en core.usuarios: lo de antes de la arroba.
                 (Identidad(actor="nuevo@prueba.test", tipo="persona", rol="operador", id="u-n"),
                  {}, ("nuevo@prueba.test", "nuevo", "panel")))
        for identidad, cabeceras, esperado in casos:
            with self.subTest(esperado=esperado):
                self.identidad = identidad
                o = self.ok(self.c.post(RAIZ, json={"clave": self.clave(), "lineas": []},
                                        headers=cabeceras))["orden"]
                self.assertEqual((o["creado_por"], o["creado_nombre"], o["creado_via"]), esperado)
                m = self.ok(self.c.get(f"{RAIZ}/{o['id']}/mensajes"))["mensajes"][0]
                self.assertEqual((m["autor"], m["autor_nombre"], m["via"]), esperado)
        # Sin credencial: con el enforcement encendido no entra nadie sin nombre.
        for identidad in (Identidad(actor="anonimo", tipo="anonimo", rol=""), None):
            self.identidad = identidad
            antes = self.folio_contador()
            with mock.patch.object(ruta.settings, "auth_enforced", True):
                self.assertIn("credencial", self.detalle(self.c.post(RAIZ, json={"lineas": []}), 401))
            self.assertEqual(self.folio_contador(), antes)


if __name__ == "__main__":
    unittest.main()
