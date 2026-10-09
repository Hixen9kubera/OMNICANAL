"""Pruebas UNITARIAS de las órdenes de venta propias (services/ordenes_venta.py y
services/ov_storage.py). Sin base y sin red: lo que toca kubera —y todo lo que
la base impone por su cuenta— se prueba en tests/test_ordenes_venta_bd.py contra
un Postgres local con las migraciones 0064, 0065 y 0068.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. Quién es quién, qué código HTTP lleva cada error, y que la variable de
     respaldo de la bandera nace en `false`.
  2. `permisos()` es PURA y dice la matriz completa por rol y por estado, con el
     porqué de cada «no». El rol manda antes que el estado; la bandera apagada
     bloquea confirmar y entregar, nunca cancelar ni borrar; sin bucket no se
     adjunta; una borrada no se mueve.
  3. Qué error sale de cada «no»: rol → 403, `rev` vieja → 409, estado → 400,
     bandera apagada → 409 de modo prueba, sin bucket → 409.
  4. La validación: renglones (agrupados por SKU **y bodega**), la bodega por
     renglón, encabezado, lo que salió en una entrega, el plan de confirmar, los
     motivos con su mínimo, y la firma (autor nunca vacío, vía sólo del catálogo).
  5. EL CLASIFICADOR DE ERRORES de la guía (§4.6 / §4.9): por `pgcode` y
     `constraint_name`, nunca por el texto; KB001 por su motivo. El mismo 23505
     es «ya existía» en un alta e «inesperado» fuera de ella.
  6. Lo que sale HACIA LA PERSONA: siempre un texto en español; nunca el nombre
     técnico del motivo ni el texto de Postgres (que va al log, con su código).
  7. UNA transición = UN `execute`, con sus dos `set local` en el mismo envío;
     «ocupado» y el interbloqueo se reintentan UNA vez; el error diferido que
     sale al COMMIT se clasifica igual; con un cursor prestado no se confirma.
  8. Las sentencias (`SQL_*`), revisadas en frío: envoltura, `ops.exigir` y
     nunca `1/0`, eventos sólo del catálogo de la 0064, autor siempre parámetro.
  9. Las banderas son filas: sin fila manda el respaldo; sin poder leer, APAGADA.
     Sin las tablas nada truena: lo dice.
 10. El PDF: tipo obligatorio, lo que no es PDF o pesa de más no llega a
     Storage, sin bucket es un 409, y Storage caído es un 502 sin índice.
 11. Una venta de marketplace: cancelada por cualquiera de sus señales, FULL si
     ALGÚN renglón lo es, y sin comisión conocida no hay neto.
 12. El cliente de Storage: nunca upsert, «ya existe» es éxito, borrar lo que ya
     no estaba no es error, y que no conteste es un StorageError.
 13. Con kubera caída: una línea de log por minuto, y las lecturas que la
     pantalla repite solas contestan de inmediato sin tocar la base.

    cd backend && python -m unittest tests.test_ordenes_venta -v
"""
from __future__ import annotations

import hashlib
import re
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402
import psycopg2.errors  # noqa: E402
import requests  # noqa: E402

from config import Settings, settings  # noqa: E402
from services import ordenes_venta as ov  # noqa: E402
from services import ov_storage  # noqa: E402

ADMIN = ov.Quien("admin@prueba.test", "Ada Admin", "panel", "admin")
OPER = ov.Quien("oper@prueba.test", "Olga Operadora", "panel", "operador")
LECT = ov.Quien("lectura@prueba.test", "Leo Lectura", "panel", "lectura")
NADIE = ov.Quien("anonimo", "", "panel", "")

BODEGAS = {
    "ENSAYO": {"codigo": "ENSAYO", "nombre": "Bodega de ensayo", "fuente": "kubera",
               "admite_ov": True, "surte_ventas": False, "cuenta_para_woo": False},
    "TEX3": {"codigo": "TEX3", "nombre": "TEXCO III", "fuente": "kubera",
             "admite_ov": False, "surte_ventas": False, "cuenta_para_woo": False},
    "TEXCO": {"codigo": "TEXCO", "nombre": "TEXCO", "fuente": "odoo",
              "admite_ov": False, "surte_ventas": True, "cuenta_para_woo": True},
}


def orden(estado="borrador", renglones=2, borrada=False, **mas) -> dict:
    confirmada = estado not in ("borrador", "cancelada") or mas.pop("estuvo_confirmada", False)
    return {"id": 7, "folio": "OV-00007", "estado": estado, "tipo": "venta", "rev": 3,
            "renglones": renglones, "piezas": 5, "piezas_apartadas": 0, "piezas_entregadas": 0,
            "confirmada_at": datetime(2026, 10, 6, tzinfo=timezone.utc) if confirmada else None,
            "canal_cancelo_at": None,
            "borrada_at": datetime(2026, 10, 6, tzinfo=timezone.utc) if borrada else None,
            "lineas": [], "archivos": [], **mas}


class ErrorPg(Exception):
    """Un error de Postgres de mentira. El servicio sólo mira `pgcode` y `diag`
    (constraint_name, message_primary, column_name): jamás el texto."""

    def __init__(self, pgcode: str | None, regla: str | None = None, motivo: str | None = None,
                 columna: str | None = None, texto: str = "ERROR: texto de Postgres") -> None:
        super().__init__(texto)
        self.pgcode = pgcode
        self.diag = types.SimpleNamespace(constraint_name=regla, message_primary=motivo,
                                          column_name=columna)


def kb001(motivo: str) -> ErrorPg:
    return ErrorPg("KB001", motivo=motivo, texto=f"ERROR: {motivo}")


class CursorFalso:
    """Un cursor que sigue un guion: cada `execute` toma el siguiente paso, que es
    una excepción (se lanza) o la lista de filas que devuelve."""

    def __init__(self, guion: list) -> None:
        self.guion = guion
        self.sentencias: list[tuple[str, object]] = []
        self.description = None
        self._filas: list[dict] = []

    def execute(self, sql, params=None) -> None:
        self.sentencias.append((sql, params))
        paso = self.guion.pop(0) if self.guion else []
        if isinstance(paso, BaseException):
            raise paso
        self._filas = list(paso)
        self.description = [("columna",)]

    def fetchall(self) -> list[dict]:
        return self._filas


def base_con_guion(*pasos, al_commit: list | None = None):
    """Sustituto de `sdb.get_cursor`. `al_commit`: excepciones que salen AL CERRAR
    el `with` (como los constraint triggers diferidos), una por transacción."""
    guion = list(pasos)
    cursores: list[CursorFalso] = []
    pendientes = list(al_commit or [])

    @contextmanager
    def get_cursor():
        cur = CursorFalso(guion)
        cursores.append(cur)
        yield cur
        if pendientes:
            raise pendientes.pop(0)

    get_cursor.cursores = cursores
    return get_cursor


def base_que_truena(*errores: Exception):
    """Sustituto de `sdb.get_cursor`: lanza los errores AL CONECTAR, en orden, y
    al acabarse da un cursor mudo."""
    cola = list(errores)
    llamadas = []

    @contextmanager
    def get_cursor():
        llamadas.append(1)
        if cola:
            raise cola.pop(0)
        yield CursorFalso([[{"ok": 1}]])

    get_cursor.llamadas = llamadas
    return get_cursor


SIN_BASE = base_que_truena(*[AssertionError("esta prueba no debía tocar la base")] * 50)


class Limpia(unittest.TestCase):
    """Cada prueba arranca sin caché (tablas, banderas, bucket) y sin pausa de caída."""

    def setUp(self) -> None:
        ov._olvidar_cache()
        ov._reiniciar_caida()
        self.addCleanup(ov._olvidar_cache)
        self.addCleanup(ov._reiniciar_caida)

    def parche(self, nombre: str, valor) -> mock.MagicMock:
        p = mock.patch.object(ov, nombre, valor)
        doble = p.start()
        self.addCleanup(p.stop)
        return doble


# ══════════════════════════════════════════════════════════════════════════════
class QuienYErrores(unittest.TestCase):
    def test_quien(self):
        self.assertTrue(ADMIN.admin and ADMIN.escribe)
        self.assertTrue(OPER.escribe and not OPER.admin)
        self.assertFalse(LECT.escribe or LECT.admin or NADIE.escribe)
        self.assertEqual((ov.AUTOMATICO.actor, ov.AUTOMATICO.via, ov.AUTOMATICO.nombre),
                         ("automatico", "automatico", "Automático"))
        with self.assertRaises(Exception):
            OPER.rol = "admin"                                   # es inmutable

    def test_cada_error_sabe_su_codigo_y_su_mensaje(self):
        codigos = {ov.Invalido: 400, ov.SinPermiso: 403, ov.NoExiste: 404, ov.Conflicto: 409,
                   ov.FaltaMigracion: 409, ov.Apagado: 409, ov.Grande: 413,
                   ov.FallaStorage: 502, ov.SinBase: 502}
        for clase, status in codigos.items():
            self.assertTrue(issubclass(clase, ov.ErrorOV))
            self.assertEqual(clase().status, status, clase.__name__)
        self.assertEqual(str(ov.Conflicto()), "La orden cambió mientras tanto; se recargó.")
        self.assertIn("0064, 0065 y 0068", str(ov.FaltaMigracion()))
        self.assertIn("modo prueba", str(ov.Apagado()))
        self.assertEqual((ov.ErrorOV("x", status=502).status, str(ov.Invalido("mal"))), (502, "mal"))

    def test_la_variable_de_respaldo_nace_en_false(self):
        """Es el RESPALDO de la fila `ordenes_venta`: sólo se lee cuando la fila no
        existe, y por eso tiene que valer false."""
        self.assertIs(Settings.model_fields["ordenes_venta_enabled"].default, False)
        self.assertEqual(ov._RESPALDO, {"ordenes_venta": "ordenes_venta_enabled"})
        self.assertEqual((ov.BANDERA_MODULO, ov.BANDERA_AUTO), ("ordenes_venta", "ov_generacion_auto"))


class Permisos(unittest.TestCase):
    def p(self, o, quien, encendido=True, bucket=True) -> dict:
        return ov.permisos(o, quien, encendido, bucket)

    def si(self, permisos: dict) -> set[str]:
        return {k for k, v in permisos.items() if v is True}

    def test_forma(self):
        p = self.p(orden(), OPER)
        self.assertEqual(set(p), set(ov.ACCIONES) | {"porque"})
        self.assertEqual(ov.ACCIONES, ("editar", "confirmar", "entregar", "cancelar", "borrar",
                                       "responder_salio", "salio_tarde", "mensajes",
                                       "subir_archivo", "bajar_archivo", "borrar_archivo"))
        self.assertEqual(set(p["porque"]), set(ov.ACCIONES) - self.si(p),
                         "cada «no» trae su porqué, y sólo los «no»")
        self.assertEqual(self.p(orden(), OPER), self.p(orden(), OPER), "es pura")

    def test_borrador(self):
        self.assertEqual(self.si(self.p(orden("borrador"), OPER)),
                         {"editar", "confirmar", "cancelar", "mensajes", "subir_archivo",
                          "bajar_archivo"})
        self.assertEqual(self.si(self.p(orden("borrador"), ADMIN)),
                         {"editar", "confirmar", "cancelar", "borrar", "mensajes", "subir_archivo",
                          "bajar_archivo", "borrar_archivo"})
        sin = self.p(orden("borrador", renglones=0), OPER)
        self.assertFalse(sin["confirmar"])
        self.assertEqual(sin["porque"]["confirmar"], "La orden no tiene renglones")

    def test_confirmada(self):
        oper = self.p(orden("confirmada"), OPER)
        self.assertEqual(self.si(oper), {"entregar", "mensajes", "subir_archivo", "bajar_archivo"})
        self.assertIn("su contenido no cambia", oper["porque"]["editar"])
        self.assertEqual(oper["porque"]["cancelar"],
                         "Sólo un administrador puede cancelar una orden confirmada")
        admin = self.p(orden("confirmada"), ADMIN)
        self.assertEqual(self.si(admin), {"entregar", "cancelar", "borrar", "mensajes",
                                          "subir_archivo", "bajar_archivo", "borrar_archivo"})
        self.assertFalse(admin["editar"], "ni el administrador edita una confirmada")

    def test_con_la_marca_del_canal_se_contesta_antes_de_entregar(self):
        marcada = orden("confirmada", canal_cancelo_at="2026-10-06T10:00:00+00:00")
        p = self.p(marcada, OPER)
        self.assertTrue(p["responder_salio"])
        self.assertFalse(p["entregar"])
        self.assertIn("contestar si salió", p["porque"]["entregar"])
        self.assertFalse(self.p(orden("confirmada"), OPER)["responder_salio"])
        self.assertFalse(self.p(marcada, LECT)["responder_salio"])

    def test_entregada_y_canceladas(self):
        for estado in ("entregada", "entregada_cancelada"):
            self.assertEqual(self.si(self.p(orden(estado), OPER)),
                             {"mensajes", "subir_archivo", "bajar_archivo"}, estado)
            self.assertEqual(self.si(self.p(orden(estado), ADMIN)),
                             {"borrar", "mensajes", "subir_archivo", "bajar_archivo",
                              "borrar_archivo"}, estado)
        # «Salió tarde»: una cancelada que ESTUVO confirmada, y sólo un administrador.
        estuvo = orden("cancelada", estuvo_confirmada=True)
        self.assertTrue(self.p(estuvo, ADMIN)["salio_tarde"])
        self.assertFalse(self.p(estuvo, OPER)["salio_tarde"])
        borrador_cancelado = self.p(orden("cancelada"), ADMIN)
        self.assertFalse(borrador_cancelado["salio_tarde"])
        self.assertIn("nunca estuvo confirmada", borrador_cancelado["porque"]["salio_tarde"])
        self.assertEqual(self.p(orden("entregada"), ADMIN)["porque"]["cancelar"],
                         "Una orden entregada no se cancela desde aquí: si el canal la cancela, "
                         "queda como entregada y cancelada")

    def test_lectura_y_sin_rol_no_escriben(self):
        for quien, texto in ((LECT, "Tu rol es de sólo lectura"),
                             (NADIE, "Tu usuario no tiene permiso para mover órdenes de venta")):
            for estado in ov.ESTADOS:
                p = self.p(orden(estado), quien)
                self.assertEqual(self.si(p), set(), f"{quien.rol} / {estado}")
                self.assertEqual(p["porque"]["mensajes"], texto)
                self.assertEqual(p["porque"]["bajar_archivo"], texto)

    def test_bandera_apagada(self):
        b = self.p(orden("borrador"), OPER, encendido=False)
        self.assertEqual(self.si(b), {"editar", "cancelar", "mensajes", "subir_archivo",
                                      "bajar_archivo"}, "sólo borradores: sin confirmar")
        self.assertIn("Modo prueba", b["porque"]["confirmar"])
        c = self.p(orden("confirmada"), ADMIN, encendido=False)
        self.assertFalse(c["entregar"])
        self.assertTrue(c["cancelar"] and c["borrar"], "soltar stock no depende de la bandera")
        marcada = orden("confirmada", canal_cancelo_at="2026-10-06T10:00:00+00:00")
        self.assertTrue(self.p(marcada, OPER, encendido=False)["responder_salio"])

    def test_sin_bucket_no_se_adjunta(self):
        p = ov.permisos(orden(), OPER, True)             # por omisión, sin bucket: falla cerrado
        self.assertFalse(p["subir_archivo"])
        self.assertIn("falta crear el bucket", p["porque"]["subir_archivo"])
        self.assertTrue(p["bajar_archivo"], "bajar no depende del bucket")

    def test_borrada_no_se_mueve(self):
        for estado in ov.ESTADOS:
            self.assertEqual(self.si(self.p(orden(estado, borrada=True), ADMIN)), {"bajar_archivo"},
                             "de una borrada, sólo bajar un PDF y sólo el administrador")
            self.assertEqual(self.si(self.p(orden(estado, borrada=True), OPER)), set())
        p = self.p(orden(borrada=True), ADMIN)
        self.assertEqual((p["porque"]["borrar"], p["porque"]["cancelar"]),
                         ("La orden ya está borrada", "La orden está borrada"))


class QueErrorSale(Limpia):
    def setUp(self) -> None:
        super().setUp()
        self.o = orden("confirmada")
        self.parche("_tablas", mock.MagicMock(return_value=True))
        self.parche("_leer_id", mock.MagicMock(side_effect=lambda *a, **k: self.o))
        self.encendido = self.parche("habilitado", mock.MagicMock(return_value=True))

    def test_rol_es_403_aunque_la_rev_sea_vieja(self):
        with self.assertRaises(ov.SinPermiso) as e:
            ov._preparar(7, 1, OPER, "cancelar")
        self.assertEqual((e.exception.status, str(e.exception)),
                         (403, "Sólo un administrador puede cancelar una orden confirmada."))

    def test_rev_vieja_es_409_antes_que_el_estado(self):
        with self.assertRaises(ov.Conflicto):
            ov._preparar(7, 2, OPER, "editar")           # además no es borrador: manda la rev
        for sin_rev in (None, 0, -1, "x", True):
            with self.assertRaises(ov.Invalido):
                ov._preparar(7, sin_rev, OPER, "entregar")

    def test_estado_es_400_y_apagado_es_409_de_modo_prueba(self):
        with self.assertRaises(ov.Invalido) as e:
            ov._preparar(7, 3, OPER, "editar")
        self.assertEqual(e.exception.status, 400)
        self.assertIn("su contenido no cambia", str(e.exception))
        self.encendido.return_value = False
        with self.assertRaises(ov.Apagado) as e:
            ov._preparar(7, 3, OPER, "entregar")
        self.assertEqual(e.exception.status, 409)
        self.assertIs(ov._preparar(7, 3, ADMIN, "cancelar"), self.o, "cancelar no mira la bandera")

    def test_sin_las_tablas_es_falta_migracion_y_sin_saber_es_sin_base(self):
        with mock.patch.object(ov, "_tablas", return_value=False):
            with self.assertRaises(ov.FaltaMigracion):
                ov._preparar(7, 3, ADMIN, "cancelar")
        with mock.patch.object(ov, "_tablas", return_value=None):
            with self.assertRaises(ov.SinBase):
                ov._preparar(7, 3, ADMIN, "cancelar")

    def test_crear_exige_rol_y_valida_antes_de_tocar_la_base(self):
        with mock.patch.object(ov.sdb, "get_cursor", SIN_BASE):
            for quien in (LECT, NADIE):
                with self.assertRaises(ov.SinPermiso):
                    ov.crear_borrador({"lineas": []}, quien)
            for malo in ("texto", None, []):
                with self.assertRaises(ov.Invalido):
                    ov.crear_borrador(malo, OPER)
            for datos in ({"tipo": "full"}, {"canal": "facebook"}, {"moneda": "pesos"},
                          {"total": -1}, {"precio_origen": "inventado"}):
                with self.assertRaises(ov.Invalido, msg=str(datos)):
                    ov.crear_borrador({**datos, "lineas": []}, OPER, "clave-1")
            for clave in ("mp:tiktok:CUENTAPRUEBA:1", "full:1:amazon:ENSAYO", "x" * 81):
                with self.assertRaises(ov.Invalido):
                    ov.crear_borrador({"lineas": []}, OPER, clave)


class Validacion(unittest.TestCase):
    def test_renglones_agrupados_por_sku_y_bodega(self):
        r = ov._lineas([
            {"sku": " ZZPRUEBA-1 ", "cantidad": "2", "precio_unitario": "10.555", "almacen": "ensayo",
             "titulo": "  Uno ", "imagen": "https://img.prueba.test/1.jpg"},
            {"sku": "zzprueba-1", "cantidad": 3.0, "precio_unitario": 10.56, "almacen": "ENSAYO"},
            {"sku": "ZZPRUEBA-1", "cantidad": 1, "precio_unitario": 10.56},       # sin bodega: OTRO
            {"sku": "ZZPRUEBA-2", "cantidad": 1}], BODEGAS)
        self.assertEqual(r, [
            {"linea": 1, "sku": "ZZPRUEBA-1", "titulo": "Uno", "cantidad": 5,
             "imagen": "https://img.prueba.test/1.jpg", "precio_unitario": Decimal("10.56"),
             "almacen": "ENSAYO"},
            {"linea": 2, "sku": "ZZPRUEBA-1", "titulo": None, "imagen": None, "cantidad": 1,
             "precio_unitario": Decimal("10.56"), "almacen": None},
            {"linea": 3, "sku": "ZZPRUEBA-2", "titulo": None, "imagen": None, "cantidad": 1,
             "precio_unitario": Decimal("0.00"), "almacen": None}])
        self.assertEqual(ov._lineas(None), [])
        self.assertEqual(ov._suma(r), Decimal("63.36"))

    def test_el_total_automatico_tambien_tiene_tope(self):
        """Cada renglón pasa su validación, pero la SUMA puede no caber en el
        numeric(14,2): la base contestaba 22003 y salía como 502 con log de bug."""
        renglon = {"precio_unitario": Decimal("9999999.99"), "cantidad": 100000}
        self.assertEqual(ov._suma([renglon]), Decimal("999999999000.00"))
        self.assertEqual(ov._total_que_cabe(ov.MAX_IMPORTE), ov.MAX_IMPORTE, "el tope sí cabe")
        with self.assertRaises(ov.Invalido) as e:
            ov._suma([renglon, renglon])
        self.assertEqual((e.exception.status, str(e.exception)),
                         (400, "El total de los renglones pasa de 999,999,999,999.99; revisa "
                               "cantidades y precios."))

    def test_un_sustituto_suelto_no_llega_a_la_base(self):
        """Media pareja UTF-16 (un emoji cortado por un `slice`) no se puede
        codificar: psycopg2 tronaba con UnicodeEncodeError y salía un 502."""
        self.assertEqual(ov._limpio(" a\ud800b\x00c\udfff "), "abc")
        self.assertEqual(ov._limpio("ok \U0001F600"), "ok \U0001F600", "la pareja completa se queda")
        self.assertEqual(ov._encabezado({"cliente": "\ud800", "guia": "G\udc00-1"}, parcial=True),
                         {"cliente": None, "guia": "G-1"})
        r = ov._lineas([{"sku": "zzprueba\ud83d-1", "cantidad": 1, "titulo": "Uno\ude00"}], BODEGAS)
        self.assertEqual((r[0]["sku"], r[0]["titulo"]), ("zzprueba-1", "Uno"))
        for texto in (r[0]["sku"], r[0]["titulo"]):
            texto.encode("utf-8")                       # lo que hará psycopg2 con el parámetro

    def test_el_mismo_sku_con_otro_precio_no_se_suma_a_escondidas(self):
        with self.assertRaises(ov.Invalido) as e:
            ov._lineas([{"sku": "A", "cantidad": 1, "precio_unitario": 100, "almacen": "ENSAYO"},
                        {"sku": "a", "cantidad": 1, "precio_unitario": 50, "almacen": "ENSAYO"}])
        self.assertIn("precios distintos", str(e.exception))
        self.assertIn("en ENSAYO", str(e.exception))

    def test_la_bodega_del_renglon(self):
        for bodega, dice in (("TEX3", "no admite órdenes de venta"), ("TEXCO", "bodega de Odoo"),
                             ("NADA", "no existe la bodega")):
            with self.assertRaises(ov.Invalido) as e:
                ov._lineas([{"sku": "ZZPRUEBA-1", "cantidad": 1, "almacen": bodega}], BODEGAS)
            self.assertIn(dice, str(e.exception))
            self.assertIn("Renglón 1 (ZZPRUEBA-1)", str(e.exception))
        self.assertIsNone(ov._porque_no_bodega("ENSAYO", BODEGAS))
        # Sin catálogo (las pruebas puras) la bodega sólo se normaliza.
        self.assertEqual(ov._lineas([{"sku": "A", "cantidad": 1, "almacen": " tex3 "}])[0]["almacen"],
                         "TEX3")

    def test_renglones_invalidos(self):
        for malo in ("texto", {"sku": "A"}, [1], [{"cantidad": 1}], [{"sku": "", "cantidad": 1}],
                     [{"sku": "x" * 81, "cantidad": 1}], [{"sku": "A", "cantidad": 0}],
                     [{"sku": "A", "cantidad": 2.5}], [{"sku": "A", "cantidad": True}],
                     [{"sku": "A", "cantidad": 100_001}],
                     [{"sku": "A", "cantidad": 1, "precio_unitario": -1}],
                     [{"sku": "A", "cantidad": 1, "precio_unitario": "caro"}],
                     [{"sku": "A", "cantidad": 60_000}, {"sku": "a", "cantidad": 60_000}],
                     [{"sku": f"S{i}", "cantidad": 1} for i in range(ov.MAX_RENGLONES + 1)]):
            with self.assertRaises(ov.Invalido, msg=str(malo)[:60]):
                ov._lineas(malo)
        self.assertIsNone(ov._lineas([{"sku": "A", "cantidad": 1, "imagen": "data:image/png;x"}])
                          [0]["imagen"], "un data-URI no se guarda")

    def test_encabezado_completo_con_sus_valores_por_omision(self):
        e = ov._encabezado({"cliente": " directa ", "canal": "TIKTOK", "mp_canal": "TikTok",
                            "mp_cuenta": "cuentaprueba", "descripcion": "  "}, parcial=False)
        self.assertEqual(set(e), set(ov._CAMPOS))
        self.assertNotIn("almacen", e, "la bodega ya no va en el encabezado: va por renglón")
        self.assertEqual((e["cliente"], e["canal"], e["mp_canal"], e["mp_cuenta"], e["descripcion"]),
                         ("directa", "tiktok", "tiktok", "CUENTAPRUEBA", None))
        self.assertEqual((e["moneda"], e["comision"], e["precio_origen"]),
                         ("MXN", Decimal("0.00"), "manual"))
        self.assertIs(e["total"], ov._TOTAL_AUTO, "total nulo = la suma de los renglones")

    def test_encabezado_parcial_solo_lo_que_se_manda(self):
        self.assertEqual(ov._encabezado({"guia": " G-1 "}, parcial=True), {"guia": "G-1"})
        self.assertEqual(ov._encabezado({}, parcial=True), {})
        self.assertEqual(ov._encabezado({"total": "10", "mp_orden": None}, parcial=True),
                         {"total": Decimal("10.00"), "mp_orden": None})

    def test_topes_y_formatos(self):
        for datos in ({"cliente": "x" * 121}, {"guia": "x" * 81}, {"descripcion": "x" * 501},
                      {"canal": "facebook"}, {"moneda": "PESOS"}, {"total": "mucho"},
                      {"total": 1e30}, {"comision": -0.01}, {"precio_origen": "otro"},
                      {"fecha_venta": "ayer"}, {"entrega_limite": "1999-01-01"}):
            with self.assertRaises(ov.Invalido, msg=str(datos)[:50]):
                ov._encabezado(datos, parcial=True)
        self.assertEqual(set(ov.CANALES), {"temu", "tiktok", "mercado_libre", "amazon", "walmart",
                                           "shein", "directa", "otro"})

    def test_fechas(self):
        sin_zona = ov._fecha("2026-10-06T15:30:00", "fecha_venta")
        self.assertEqual(sin_zona.utcoffset().total_seconds(), -6 * 3600, "sin zona = CDMX")
        self.assertEqual(ov._fecha("2026-10-06T21:30:00Z", "fecha_venta"), sin_zona)
        self.assertIsNone(ov._fecha("  ", "fecha_venta"))
        self.assertIsNone(ov._fecha(None, "fecha_venta"))

    def test_lo_que_salio_en_una_entrega(self):
        lineas = [{"id": 1, "sku": "A", "cantidad": 3, "entregado_at": None},
                  {"id": 2, "sku": "B", "cantidad": 2, "entregado_at": "2026-10-06T10:00:00+00:00"},
                  {"id": 3, "sku": "C", "cantidad": 1, "entregado_at": None}]
        self.assertEqual(ov._entrega(None, lineas), [{"id": 1, "n": 3}, {"id": 3, "n": 1}],
                         "sin detalle salen TODOS los pendientes, completos")
        self.assertEqual(ov._entrega([{"id": "1", "n": 0}, {"id": 3, "n": 1.0}], lineas),
                         [{"id": 1, "n": 0}, {"id": 3, "n": 1}])
        for malas, dice in (([], "Indica qué renglones"), ("todo", "Indica qué renglones"),
                            ([7], "«id»"), ([{"id": 9, "n": 1}], "no es de esta orden"),
                            ([{"id": 2, "n": 1}], "ya había salido"),
                            ([{"id": 1, "n": 4}], "de 0 a 3"), ([{"id": 1, "n": -1}], "de 0 a 3"),
                            ([{"id": 1, "n": 1.5}], "de 0 a 3"), ([{"id": 1, "n": True}], "de 0 a 3"),
                            ([{"id": 1, "n": 1}, {"id": 1, "n": 1}], "viene dos veces")):
            with self.assertRaises(ov.Invalido, msg=str(malas)) as e:
                ov._entrega(malas, lineas)
            self.assertIn(dice, str(e.exception))

    def test_el_plan_de_confirmar(self):
        lineas = [{"id": 1, "sku": "A", "almacen": "ENSAYO"}, {"id": 2, "sku": "B", "almacen": None}]
        with self.assertRaises(ov.Invalido) as e:
            ov._plan(lineas, None, BODEGAS)
        self.assertEqual(str(e.exception),
                         "El renglón de B no tiene bodega: elige de cuál sale antes de confirmar.")
        self.assertEqual(ov._plan(lineas, [{"id": 2, "almacen": "ensayo"}], BODEGAS),
                         [{"id": 1, "almacen": "ENSAYO"}, {"id": 2, "almacen": "ENSAYO"}])
        for plan in ([{"id": 2, "almacen": "TEX3"}], [{"id": 2, "almacen": "TEXCO"}]):
            with self.assertRaises(ov.Invalido):
                ov._plan(lineas, plan, BODEGAS)
        for plan in ("ENSAYO", [{"id": 2}], [{"almacen": "ENSAYO"}], ["ENSAYO"]):
            with self.assertRaises(ov.Invalido):
                ov._plan(lineas, plan, BODEGAS)

    def test_motivos_con_su_minimo(self):
        self.assertIsNone(ov._motivo_texto("   "))
        self.assertEqual(ov._motivo_texto("  ya no  ", 5), "ya no")
        for corto in ("", "no", " abc "):
            with self.assertRaises(ov.Invalido):
                ov._motivo_texto(corto, ov.MIN_MOTIVO_CANCELAR)
        with self.assertRaises(ov.Invalido) as e:
            ov._motivo_texto("123456789", ov.MIN_MOTIVO_BORRAR, "Escribe por qué (10 o más).")
        self.assertEqual(str(e.exception), "Escribe por qué (10 o más).")
        with self.assertRaises(ov.Invalido):
            ov._motivo_texto("x" * (ov.MAX_MOTIVO + 1))
        self.assertEqual((ov.MIN_MOTIVO_CANCELAR, ov.MIN_MOTIVO_BORRAR), (5, 10))

    def test_la_firma_nunca_va_vacia_y_la_via_es_del_catalogo(self):
        self.assertEqual(ov._firma(OPER),
                         {"q": "oper@prueba.test", "nombre": "Olga Operadora", "via": "panel"})
        self.assertEqual(ov._firma(ov.AUTOMATICO),
                         {"q": "automatico", "nombre": "Automático", "via": "automatico"})
        self.assertEqual(ov._firma(ov.Quien("servicio", "", "api", "operador")),
                         {"q": "servicio", "nombre": None, "via": "api"})
        # Un cron no tiene actor ('' en core.actor): se firma `automatico`, nunca '' ni None.
        self.assertEqual(ov._firma(ov.Quien("", "", "cron", "admin")),
                         {"q": "automatico", "nombre": None, "via": "automatico"})
        with self.assertRaises(ov.Invalido):          # «script» no tiene equivalente: no se disfraza
            ov._firma(ov.Quien("x@prueba.test", "X", "script", "admin"))
        with self.assertRaises(ov.SinPermiso):        # una persona sin identificar no escribe
            ov._firma(ov.Quien("  ", "X", "panel", "operador"))
        self.assertEqual(ov.VIAS, ("panel", "api", "claude", "automatico"))

    def test_ayudantes_de_texto(self):
        self.assertEqual(ov._nombre_pdf("C:\\papeles\\Pago 1.PDF"), "Pago 1.PDF")
        self.assertEqual(ov._nombre_pdf("../../etc/pago"), "pago.pdf")
        self.assertEqual(ov._nombre_pdf(""), "documento.pdf")
        self.assertEqual(ov._escapar_like("100%_x\\"), "100\\%\\_x\\\\")
        self.assertEqual((ov._piezas(1), ov._piezas(2), ov._renglones(1), ov._renglones(3)),
                         ("1 pieza", "2 piezas", "1 renglón", "3 renglones"))
        self.assertEqual((ov._quedan(1), ov._quedan(2)),
                         ("queda 1 renglón por salir", "quedan 2 renglones por salir"))
        self.assertEqual(ov._tres_skus(["A", "a", "B", None, "C", "D"]), ["A", "B", "C"])


class Clasificador(unittest.TestCase):
    """El patrón de la guía §4.9: por código y por nombre de regla, nunca por el texto."""

    def test_kb001_de_negocio_y_de_invariante(self):
        for motivo in ("ov_no_esta_en_borrador_o_cambio_rev", "no_alcanzo", "entrega_no_cuadra",
                       "renglon_sin_plan_o_sin_saldo", "canal_cancelo_no_aplica",
                       "salio_tarde_no_cuadra", "un_motivo_nuevo"):
            self.assertEqual(ov.clasificar(kb001(motivo), "confirmar"), ("negocio", motivo))
        # Los de invariante son BUGS: la sentencia no escribió lo que debía.
        for motivo in ("renglones_no_cuadran", "cancelar_no_cuadra", "devolucion_no_cuadra",
                       "regla_de_negocio", "entrega_escrituras_no_cuadran",
                       "salio_escrituras_no_cuadran", "alta_escrituras_no_cuadran"):
            self.assertEqual(ov.clasificar(kb001(motivo), "entregar"), ("inesperado", motivo))
        self.assertEqual(ov.clasificar(ErrorPg("KB001", motivo=""), "x"),
                         ("inesperado", "regla_de_negocio"), "un motivo vacío es el default de exigir")

    def test_el_mismo_23505_es_exito_en_un_alta_y_bug_fuera_de_ella(self):
        for regla in ("ov_ordenes_clave_uq", "ov_ordenes_mp_uq"):
            e = ErrorPg("23505", regla)
            for alta in ("crear_borrador", "crear_auto"):
                self.assertEqual(ov.clasificar(e, alta), ("ya_existia", regla))
            for otra in ("salio_tarde", "guardar", "cancelar"):
                self.assertEqual(ov.clasificar(e, otra), ("inesperado", regla), otra)
        for regla in ("ov_archivos_vivo_uq", "stock_mov_salida_ov_uq", "stock_mov_clave_uq",
                      "devoluciones_recepcion_uq"):
            self.assertEqual(ov.clasificar(ErrorPg("23505", regla), "entregar"), ("ya_existia", regla))
        # El folio repetido NUNCA es «ya existía»: alguien tocó el contador.
        self.assertEqual(ov.clasificar(ErrorPg("23505", "ov_ordenes_folio_uq"), "crear_borrador"),
                         ("inesperado", "ov_ordenes_folio_uq"))
        self.assertEqual(ov.clasificar(ErrorPg("23505", "ov_lineas_sku_alm_uq"), "guardar"),
                         ("inesperado", "ov_lineas_sku_alm_uq"))

    def test_ocupado_interbloqueo_y_lo_demas(self):
        self.assertEqual(ov.clasificar(ErrorPg("55P03"), "confirmar"), ("ocupado", None))
        self.assertEqual(ov.clasificar(ErrorPg("57014"), "confirmar"), ("ocupado", None))
        self.assertEqual(ov.clasificar(ErrorPg("40P01"), "confirmar"), ("deadlock", None))
        for codigo, regla in (("23514", "ov_coherente"), ("23514", "stock_libro_cuadra"),
                              ("23503", "ov_lineas_almacen_fk"), ("42501", "ov_ordenes_inmutable"),
                              ("23502", None), ("22012", None), ("42P01", None)):
            self.assertEqual(ov.clasificar(ErrorPg(codigo, regla), "confirmar"), ("inesperado", regla))

    def test_con_los_errores_de_verdad_de_psycopg2(self):
        """psycopg2 no deja fijar `pgcode` ni `diag`: sin código, lo que no se
        reconoce cae en «inesperado» en vez de adivinarse por el texto."""
        e = psycopg2.errors.UniqueViolation("duplicate key value violates «ov_ordenes_clave_uq»")
        self.assertEqual(ov.clasificar(e, "crear_borrador"), ("inesperado", None))
        e = psycopg2.OperationalError("no_alcanzo")
        self.assertEqual(ov.clasificar(e, "confirmar"), ("inesperado", None))


class LoQueSaleHaciaLaPersona(unittest.TestCase):
    def rechazo(self, exc: ErrorPg, operacion: str = "confirmar") -> ov._Rechazo:
        return ov._Rechazo(*ov.clasificar(exc, operacion), exc)

    def test_cada_motivo_de_negocio_tiene_sus_palabras(self):
        self.assertEqual(ov.texto_de_motivo("ov_no_esta_en_borrador_o_cambio_rev"),
                         "La orden cambió mientras tanto; se recargó.")
        for motivo, texto in ov.TEXTO_MOTIVO.items():
            self.assertNotIn(motivo, texto)
            self.assertNotRegex(texto, r"[a-z]+_[a-z]+", f"{motivo}: sin nombres técnicos")
            self.assertTrue(texto[0].isupper() and texto.endswith("."), motivo)
            e = ov._error_de_rechazo(self.rechazo(kb001(motivo)), "confirmar")
            self.assertEqual((type(e), e.status, str(e)), (ov.Conflicto, 409, texto))
        # Un motivo que nadie tradujo tampoco sale a secas.
        e = ov._error_de_rechazo(self.rechazo(kb001("motivo_que_nadie_tradujo")), "confirmar")
        self.assertEqual((type(e), str(e)),
                         (ov.Conflicto, "La operación ya no aplica al estado actual de la orden; "
                                        "se recargó."))
        self.assertNotIn("motivo_que_nadie_tradujo", ov.texto_de_motivo("motivo_que_nadie_tradujo"))
        self.assertEqual(ov.texto_de_motivo(None), ov.texto_de_motivo("otro"))

    def test_los_motivos_de_la_guia_que_usa_este_modulo_estan_traducidos(self):
        usados = set()
        for nombre in dir(ov):
            if nombre.startswith("SQL_"):
                usados |= set(re.findall(r"ops\.exigir\(.*?'(\w+)'\)", getattr(ov, nombre), flags=re.S))
        negocio = {m for m in usados
                   if m not in ov.KB001_QUE_AVISAN and not m.endswith("_escrituras_no_cuadran")}
        self.assertEqual(negocio - set(ov.TEXTO_MOTIVO), set(),
                         "un motivo de negocio de una sentencia sin su texto en español")
        self.assertGreaterEqual(negocio, {"ov_no_esta_en_borrador_o_cambio_rev", "no_alcanzo",
                                          "renglon_sin_plan_o_sin_saldo", "entrega_no_cuadra",
                                          "ov_no_esta_confirmada_o_cambio_rev",
                                          "ov_no_cancelable_o_cambio_rev", "salio_no_cuadra",
                                          "salio_tarde_no_cuadra", "canal_cancelo_no_aplica",
                                          "canal_cancelo_entregada_no_aplica"})

    def test_no_alcanzo_dice_que_sku_cuanto_pide_y_cuanto_hay(self):
        t = ov._texto_no_alcanzo
        self.assertEqual(t([{"sku": "ZZPRUEBA-1", "pide": 3, "libre": 1, "almacen": "ENSAYO"}]),
                         "No alcanzó el stock para apartar: ZZPRUEBA-1 pide 3 y hay 1 libre en "
                         "ENSAYO. No se apartó nada.")
        self.assertIn("ZZPRUEBA-2 pide 2 y hay 0 libres en ENSAYO",
                      t([{"sku": "ZZPRUEBA-2", "pide": 2, "libre": -4, "almacen": "ENSAYO"}]),
                      "un libre negativo (tras un conteo) se dice como cero")
        # Sin fila de saldo NO es un cero: «no lo sabemos» se dice distinto.
        self.assertIn("ZZPRUEBA-3 pide 1 y no tiene existencias registradas en ENSAYO",
                      t([{"sku": "ZZPRUEBA-3", "pide": 1, "libre": None, "almacen": "ENSAYO"}]))
        muchos = [{"sku": f"S{i}", "pide": 1, "libre": 0, "almacen": "ENSAYO"} for i in range(5)]
        self.assertIn("; y otros 2 SKU.", t(muchos))
        self.assertIn("; y 1 SKU más.", t(muchos[:4]))

    def test_lo_inesperado_es_un_502_sin_detalle_y_el_detalle_va_al_log(self):
        exc = ErrorPg("23514", "stock_libro_cuadra",
                      texto="libro descuadrado ZZPRUEBA-9/ENSAYO en db.host-secreto.supabase.co")
        with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
            e = ov._error_de_rechazo(self.rechazo(exc, "entregar"), "entregar")
        self.assertEqual((type(e), e.status, str(e)),
                         (ov.ErrorOV, 502,
                          "No se pudo completar la operación; quedó registrado para revisarlo."))
        registro = "\n".join(logs.output)
        for esperado in ("ordenes_venta.entregar", "pgcode=23514", "regla=stock_libro_cuadra",
                         "host-secreto"):
            self.assertIn(esperado, registro)
        # Un KB001 de invariante (la sentencia no escribió lo que debía) avisa igual.
        with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
            e = ov._error_de_rechazo(self.rechazo(kb001("renglones_no_cuadran")), "confirmar")
        self.assertEqual((e.status, "renglones_no_cuadran" in str(e)), (502, False))
        self.assertIn("regla=renglones_no_cuadran", "\n".join(logs.output))
        # NOT NULL de un autor (el actor no se resolvió): se dice la columna en el log.
        with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
            ov._error_de_rechazo(self.rechazo(ErrorPg("23502", columna="creado_por")), "crear_borrador")
        self.assertIn("columna=creado_por", "\n".join(logs.output))

    def test_los_rechazos_que_si_son_de_usuario(self):
        casos = (("23503", "ov_lineas_almacen_fk", ov.Invalido, "no es de kubera"),
                 ("42501", "ov_lineas_inmutable", ov.Conflicto, "se confirmó mientras tanto"),
                 ("23514", "ov_ordenes_canal_cancelo_chk", ov.Conflicto, "contestar si salió"),
                 ("23514", "stock_almacen_fisico_chk", ov.Conflicto, "piezas físicas"),
                 ("23514", "stock_mov_saldo_chk", ov.Conflicto, "piezas físicas"),
                 ("23514", "ov_ordenes_conf_chk", ov.Invalido, "nunca estuvo confirmada"),
                 ("23503", "ov_mensajes_orden_fk", ov.Invalido, "ya no existe"))
        for codigo, regla, clase, dice in casos:
            e = ov._error_de_rechazo(self.rechazo(ErrorPg(codigo, regla)), "entregar")
            self.assertIs(type(e), clase, regla)
            self.assertIn(dice, str(e))
            self.assertNotIn(regla, str(e))
        # «Ya estaba» sin que la operación lo trate aparte: se relee, no es un 500.
        e = ov._error_de_rechazo(self.rechazo(ErrorPg("23505", "stock_mov_salida_ov_uq"), "entregar"),
                                 "entregar")
        self.assertEqual((type(e), str(e)), (ov.Conflicto, "La orden cambió mientras tanto; se recargó."))

    def test_los_textos_de_la_bitacora(self):
        self.assertEqual(ov._cuerpo_creada([], {}), "Orden creada en borrador · sin renglones")
        self.assertEqual(ov._cuerpo_creada([{"cantidad": 2}, {"cantidad": 1}],
                                           {"mp_canal": "tiktok", "mp_orden": "577"}),
                         "Orden creada en borrador · 2 renglones, 3 piezas · venta tiktok 577")
        self.assertEqual(ov._cuerpo_guardada({"guia": 1, "total": 2}, None),
                         "Borrador guardado · guía, total")
        self.assertEqual(ov._cuerpo_guardada({}, {"agregados": ["A"], "quitados": [], "cambiados": [1]}),
                         "Borrador guardado · renglones: 1 agregado(s), 1 con cambios")
        self.assertEqual(ov._cuerpo_cancelada("manual", None, 0), "Cancelada")
        self.assertEqual(ov._cuerpo_cancelada("marketplace", "El comprador canceló", 2),
                         "Cancelada por el marketplace · se soltaron 2 piezas · Motivo: El comprador "
                         "canceló")
        self.assertEqual(ov._cuerpo_cancelada("sistema", None, 1),
                         "Cancelada por el sistema · se soltaron 1 pieza")
        ec = ov._cuerpo_cancelada_con_salida("manual", "ya no", 1, 2)
        self.assertIn("con 1 pieza ya entregada a la paquetería (DELIVERED but CANCELLED)", ec)
        self.assertIn("se espera la devolución", ec)
        self.assertIn("se soltaron 2 piezas que no habían salido · Motivo: ya no", ec)

    def test_la_diferencia_de_renglones_casa_por_sku_y_bodega(self):
        antes = [{"sku": "A", "almacen": "ENSAYO", "cantidad": 1, "precio_unitario": 10},
                 {"sku": "B", "almacen": None, "cantidad": 2, "precio_unitario": 5}]
        despues = [{"sku": "a", "almacen": "ENSAYO", "cantidad": 3, "precio_unitario": 10},
                   {"sku": "B", "almacen": "ENSAYO", "cantidad": 2, "precio_unitario": 5}]
        self.assertEqual(ov._dif_lineas(antes, despues),
                         {"agregados": ["B"], "quitados": ["B"],
                          "cambiados": [{"sku": "a", "cantidad": [1, 3]}]},
                         "cambiar la bodega de un renglón es quitarlo y ponerlo")
        self.assertNotEqual(ov._huella_lineas(antes), ov._huella_lineas(despues))


class UnaTransicionUnExecute(Limpia):
    def test_va_en_un_solo_envio_con_sus_dos_set_local(self):
        gc = base_con_guion([{"cuadra": 4}])
        with mock.patch.object(ov.sdb, "get_cursor", gc):
            fila = ov._transicion("confirmar", ov.SQL_CONFIRMAR, {"id": 7})
        self.assertEqual(fila, {"cuadra": 4})
        self.assertEqual(len(gc.cursores), 1, "una transacción")
        (sql, params), = gc.cursores[0].sentencias
        self.assertTrue(sql.startswith("set local lock_timeout = '4s';\n"
                                       "set local statement_timeout = '15s';\nwith o as ("))
        self.assertEqual(params, {"id": 7})

    def test_ocupado_se_reintenta_una_vez_y_luego_se_dice(self):
        for codigo in ("55P03", "57014"):
            gc = base_con_guion(ErrorPg(codigo), [{"cuadra": 1}])
            with mock.patch.object(ov.sdb, "get_cursor", gc):
                self.assertEqual(ov._transicion("confirmar", "select 1", {}), {"cuadra": 1})
            self.assertEqual(len(gc.cursores), 2, "el reintento va en OTRA transacción")
            gc = base_con_guion(ErrorPg(codigo), ErrorPg(codigo), [{"cuadra": 1}])
            with mock.patch.object(ov.sdb, "get_cursor", gc), \
                    self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
                with self.assertRaises(ov.Conflicto) as e:
                    ov._transicion("confirmar", "select 1", {})
            self.assertEqual(len(gc.cursores), 2, "UN reintento como máximo")
            self.assertEqual(str(e.exception), "La bodega está ocupada con otro movimiento; intenta "
                                               "de nuevo en unos segundos.")

    def test_el_interbloqueo_se_reintenta_una_vez_y_avisa(self):
        gc = base_con_guion(ErrorPg("40P01"), [{"cuadra": 1}])
        with mock.patch.object(ov.sdb, "get_cursor", gc), \
                self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
            self.assertEqual(ov._transicion("entregar", "select 1", {}), {"cuadra": 1})
        self.assertIn("INTERBLOQUEO", logs.output[0])
        gc = base_con_guion(ErrorPg("40P01"), ErrorPg("40P01"))
        with mock.patch.object(ov.sdb, "get_cursor", gc), \
                self.assertLogs("omnicanal.ordenes_venta", level="ERROR"):
            with self.assertRaises(ov.ErrorOV) as e:
                ov._transicion("entregar", "select 1", {})
        self.assertEqual((type(e.exception), e.exception.status), (ov.ErrorOV, 502))

    def test_un_rechazo_no_se_reintenta_y_llega_clasificado(self):
        for exc, clase, detalle in ((kb001("no_alcanzo"), "negocio", "no_alcanzo"),
                                    (ErrorPg("23505", "ov_archivos_vivo_uq"), "ya_existia",
                                     "ov_archivos_vivo_uq"),
                                    (ErrorPg("23514", "ov_coherente"), "inesperado", "ov_coherente")):
            gc = base_con_guion(exc, [{"no": "debe llegar aquí"}])
            with mock.patch.object(ov.sdb, "get_cursor", gc):
                with self.assertRaises(ov._Rechazo) as e:
                    ov._transicion("confirmar", "select 1", {})
            self.assertEqual((e.exception.clase, e.exception.detalle, len(gc.cursores)),
                             (clase, detalle, 1))
            self.assertIs(e.exception.exc, exc)
        self.assertTrue(e.exception.es("23514", "ov_coherente", "otra"))
        self.assertFalse(e.exception.es("23514", "otra"))

    def test_el_error_diferido_sale_al_commit_y_se_clasifica_igual(self):
        """Los constraint triggers diferidos no truenan en el `execute`: truenan al
        cerrar el `with` (el COMMIT). La fila del SELECT ya «llegó», pero la
        transición NO ocurrió, y tiene que salir como rechazo."""
        diferido = ErrorPg("23514", "ov_coherente")
        gc = base_con_guion([{"cuadra": 2}], al_commit=[diferido])
        with mock.patch.object(ov.sdb, "get_cursor", gc):
            with self.assertRaises(ov._Rechazo) as e:
                ov._transicion("cancelar", "select 1", {})
        self.assertEqual((e.exception.clase, e.exception.detalle), ("inesperado", "ov_coherente"))

    def test_sin_tabla_es_falta_migracion_y_sin_conexion_es_sin_base(self):
        with mock.patch.object(ov.sdb, "get_cursor", base_con_guion(ErrorPg("42P01"))):
            with self.assertRaises(ov.FaltaMigracion):
                ov._transicion("confirmar", "select 1", {})
        self.assertTrue(ov._sin_tabla(ErrorPg("42703")))
        self.assertFalse(ov._sin_tabla(ErrorPg("3B001", texto='savepoint "x" does not exist')),
                         "manda el código, no el texto")
        caida = psycopg2.OperationalError("could not connect to server")
        with mock.patch.object(ov.sdb, "get_cursor", base_que_truena(caida)), \
                mock.patch.object(ov.sdb, "reintentar_transitorio", side_effect=lambda f: f()), \
                self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            with self.assertRaises(ov.SinBase):
                ov._transicion("confirmar", "select 1", {})
        with mock.patch.object(ov.sdb, "get_cursor", base_que_truena(RuntimeError("otra cosa"))):
            with self.assertRaises(RuntimeError):
                ov._transicion("confirmar", "select 1", {})

    def test_con_un_cursor_prestado_no_se_abre_ni_se_confirma_nada(self):
        cur = CursorFalso([[], [{"cuadra": 1}], []])
        with mock.patch.object(ov.sdb, "get_cursor", SIN_BASE):
            self.assertEqual(ov._transicion("confirmar", "select 1", {}, cur), {"cuadra": 1})
        self.assertEqual([s for s, _ in cur.sentencias],
                         ["savepoint ov_paso", "select 1", "release savepoint ov_paso"])
        # Un rechazo deja VIVA la transacción de quien prestó el cursor.
        cur = CursorFalso([[], kb001("no_alcanzo"), [], []])
        with self.assertRaises(ov._Rechazo):
            ov._transicion("confirmar", "select 1", {}, cur)
        self.assertEqual([s for s, _ in cur.sentencias],
                         ["savepoint ov_paso", "select 1", "rollback to savepoint ov_paso",
                          "release savepoint ov_paso"])
        # …y un cursor de tuplas sirve igual que el RealDictCursor del pool.
        tuplas = mock.MagicMock()
        tuplas.description = [("id",), ("folio",)]
        tuplas.fetchall.return_value = [(7, "OV-00007")]
        self.assertEqual(ov._fila("select 1", None, tuplas), {"id": 7, "folio": "OV-00007"})


class Sentencias(unittest.TestCase):
    """Las constantes `SQL_*`, revisadas en frío."""

    def setUp(self) -> None:
        self.sql = {n: getattr(ov, n) for n in dir(ov) if n.startswith("SQL_")}

    def test_estan_todas_las_transiciones_del_contrato(self):
        self.assertEqual(set(self.sql), {
            "SQL_CREAR", "SQL_GUARDAR", "SQL_CONFIRMAR", "SQL_ENTREGAR", "SQL_CANCELAR",
            "SQL_CANCELAR_CON_SALIDA", "SQL_CANCELAR_CANAL", "SQL_CANCELAR_CON_SALIDA_CANAL",
            "SQL_BORRAR", "SQL_CANAL_MARCA", "SQL_CANAL_CANCELO_ENTREGADA", "SQL_SALIO",
            "SQL_SALIO_TARDE", "SQL_CREAR_AUTO", "SQL_MENSAJE_SISTEMA", "SQL_MENSAJE_USUARIO",
            "SQL_ARCHIVO_ALTA", "SQL_ARCHIVO_BAJA"})

    def test_cada_una_lleva_su_envoltura_y_es_una_sola_sentencia(self):
        for nombre, sql in self.sql.items():
            self.assertTrue(sql.startswith("set local lock_timeout = '4s';\n"
                                           "set local statement_timeout = '15s';\n"), nombre)
            cuerpo = sql[len(ov._ENVOLTURA):]
            self.assertNotIn(";", cuerpo, f"{nombre}: una sola sentencia tras los set local")
            self.assertRegex(cuerpo, r"^(with|insert) ", nombre)
            # Regla 13: nada de estado de sesión en el pooler compartido. Los únicos
            # SET que son SENTENCIA (a inicio de línea; el SET de un UPDATE va
            # sangrado) son los dos `set local`, que mueren con la transacción.
            self.assertEqual(re.findall(r"(?im)^set\s+(\w+)", sql), ["local", "local"], nombre)
            self.assertNotIn("set_config", sql, f"{nombre}: OV y libro no leen app.usuario")
            self.assertNotIn("set_session", sql)

    def test_terminan_en_exigir_y_nunca_dividen_entre_cero(self):
        for nombre, sql in self.sql.items():
            if nombre in ("SQL_MENSAJE_SISTEMA", "SQL_MENSAJE_USUARIO"):
                continue                               # un INSERT…SELECT: cero filas no es un error
            self.assertIn("ops.exigir(", sql, nombre)
            self.assertNotRegex(sql, r"1\s*/\s*\(", f"{nombre}: el 1/0 es de la versión vieja")
            for motivo in re.findall(r"ops\.exigir\(.*?'(\w*)'\)", sql, flags=re.S):
                self.assertRegex(motivo, r"^[a-z]+(_[a-z]+)+$", f"{nombre}: un motivo con nombre")

    def test_los_mensajes_del_sistema_solo_usan_el_catalogo_de_eventos(self):
        usados = set()
        for nombre, sql in self.sql.items():
            for evento in re.findall(r"'sistema',\s*'(\w+)'", sql):
                self.assertIn(evento, ov.EVENTOS, nombre)
                usados.add(evento)
            for evento in re.findall(r"then '(\w+)' else '(\w+)' end,\s*case", sql):
                usados |= set(evento)
        usados -= {"MXN"}
        self.assertLessEqual(usados, set(ov.EVENTOS))
        self.assertEqual(usados, {"creada", "borrador_guardado", "confirmada", "entregada",
                                  "entregada_parcial", "cancelada", "borrada_admin",
                                  "canal_cancelo", "devolucion_esperada"})
        # Los de la versión del 2-oct, que el CHECK ya no admite.
        for viejo in ("editada", "reserva", "regresada", "borrada", "archivo", "archivo_borrado",
                      "devolucion"):
            self.assertNotIn(viejo, ov.EVENTOS)
            for nombre, sql in self.sql.items():
                self.assertNotIn(f"'{viejo}'", sql, nombre)
        # «PDF adjunto / quitado» va como aviso del sistema SIN evento.
        for nombre in ("SQL_ARCHIVO_ALTA", "SQL_ARCHIVO_BAJA"):
            self.assertRegex(self.sql[nombre], r"'sistema', null,", nombre)

    def test_quien_y_cuando(self):
        for nombre, sql in self.sql.items():
            # Los `_at` van con now(), nunca con una hora que mande el cliente.
            for columna in re.findall(r"(\w+_at)\s*=\s*([^,\n]+)", sql):
                self.assertNotIn("%(", columna[1].split("case")[0] if "case" in columna[1]
                                 else columna[1], f"{nombre}: {columna[0]}")
            # Los autores van SIEMPRE como parámetro; nunca un literal ni vacío.
            for columna, valor in re.findall(r"(\w+_por|quien)\s*=\s*([^,\n]+)", sql):
                self.assertRegex(valor.strip(), r"^(%\(q(_canal)?\)s|e\.entregado_por|case )",
                                 f"{nombre}: {columna}")
            self.assertNotIn("''", sql.replace("'{}'", ""), f"{nombre}: nada de cadenas vacías")
            self.assertNotIn("'prueba'", sql, f"{nombre}: la vía de las pruebas del verificador")
        self.assertIn("confirmada_at = now()", ov.SQL_CONFIRMAR,
                      "sin eso, el UPDATE de los renglones al confirmar da 42501")

    def test_las_de_estado_llevan_su_cas(self):
        for nombre in ("SQL_GUARDAR", "SQL_CONFIRMAR", "SQL_ENTREGAR", "SQL_CANCELAR",
                       "SQL_CANCELAR_CON_SALIDA", "SQL_BORRAR", "SQL_SALIO", "SQL_SALIO_TARDE"):
            sql = self.sql[nombre]
            self.assertIn("v.rev = %(rev)s", sql, f"{nombre}: las personas van con rev")
            self.assertIn("rev = v.rev + 1", sql.replace("rev            = v.rev + 1",
                                                         "rev = v.rev + 1"), nombre)
            self.assertIn("v.borrada_at is null", sql, nombre)
        # El canal: compare-and-set por ESTADO, sin rev, y aguanta verse dos veces.
        for nombre in ("SQL_CANCELAR_CANAL", "SQL_CANCELAR_CON_SALIDA_CANAL", "SQL_CANAL_MARCA",
                       "SQL_CANAL_CANCELO_ENTREGADA"):
            self.assertNotIn("%(rev)s", self.sql[nombre], nombre)
            self.assertIn("borrada_at is null", self.sql[nombre], nombre)
        self.assertIn("canal_cancelo_at is null", ov.SQL_CANAL_MARCA)
        self.assertIn("coalesce(v.devolucion_estado, 'pendiente')", ov.SQL_CANAL_CANCELO_ENTREGADA)

    def test_el_saldo_se_bloquea_en_orden_y_el_libro_va_en_la_misma_sentencia(self):
        for nombre in ("SQL_CONFIRMAR", "SQL_ENTREGAR", "SQL_CANCELAR", "SQL_CANCELAR_CON_SALIDA",
                       "SQL_BORRAR", "SQL_SALIO", "SQL_SALIO_TARDE", "SQL_CREAR_AUTO"):
            self.assertRegex(self.sql[nombre],
                             r"x as materialized \((?s:.*?)order by sa\.sku, sa\.almacen\s+"
                             r"for update of sa", nombre)
        for nombre in ("SQL_ENTREGAR", "SQL_SALIO", "SQL_SALIO_TARDE"):
            sql = self.sql[nombre]
            self.assertIn("insert into almacen.stock_mov", sql, nombre)
            self.assertIn("'salida_ov'", sql)
            self.assertIn("'ov:' || %(id)s || ':linea:' || s.linea_id || ':salida'", sql,
                          f"{nombre}: la MISMA clave para entregar y para «¿salió?»")
        for nombre in ("SQL_CONFIRMAR", "SQL_CREAR_AUTO"):
            self.assertIn("for share", self.sql[nombre], nombre)
            self.assertIn("a.admite_ov", self.sql[nombre], nombre)
        self.assertIn("a.surte_ventas", ov.SQL_CREAR_AUTO, "C11: se vuelve a exigir en el candado")
        self.assertIn("sa.libre >= x.n", ov.SQL_CONFIRMAR)
        self.assertNotIn("ops.stock_", ov.SQL_CREAR + ov.SQL_GUARDAR, "un borrador no toca saldo")
        # Nada de la versión del 2-oct.
        for nombre, sql in self.sql.items():
            for viejo in ("ov_stock", "ov_stock_base_v", "otorgado", "o.almacen"):
                self.assertNotIn(viejo, sql, nombre)
        self.assertFalse(hasattr(ov, "reservar") or hasattr(ov, "regresar")
                         or hasattr(ov, "devolucion") or hasattr(ov, "editar"))

    def test_crear_auto_lleva_todo_el_contenido_en_el_insert(self):
        sql = ov.SQL_CREAR_AUTO
        for columna in ("descripcion", "guia", "paqueteria", "fecha_venta", "entrega_limite",
                        "moneda", "total", "comision", "precio_origen", "titulo", "imagen",
                        "precio_unitario", "confirmada_nombre"):
            self.assertIn(columna, sql, columna)
        self.assertIn("'confirmada', 'venta', %(mc)s,", sql, "cliente = mp_canal (SEG-06)")
        self.assertIn("'marketplace'", sql)
        self.assertRegex(sql, r"(?s)fo as \(\s*update ventas.ov_folio.*not exists \(select 1 from previa\)"
                              r".*\(select count\(\*\) from s\) = \(select count\(\*\) from r\)",
                         "el folio sube AL FINAL, sólo si no existía y todo alcanzó")
        self.assertLess(sql.index("for update of sa"), sql.index("update ventas.ov_folio"),
                        "la excepción documentada al orden de candados")


class Banderas(Limpia):
    APAGADA = {"encendido": False, "persistido": False, "actualizado_por": None, "motivo": None,
               "actualizado_at": None}

    def test_con_fila_manda_la_fila(self):
        fila = {"valor": True, "motivo": "Acta de la fase A", "actualizado_por": "admin@prueba.test",
                "actualizado_at": datetime(2026, 10, 6, 12, tzinfo=timezone.utc)}
        leer = self.parche("_leer_bandera", mock.MagicMock(return_value=fila))
        with mock.patch.object(settings, "ordenes_venta_enabled", False):
            self.assertTrue(ov.habilitado())
            self.assertEqual(ov.estado_bandera("ordenes_venta"),
                             {"encendido": True, "persistido": True, "motivo": "Acta de la fase A",
                              "actualizado_por": "admin@prueba.test",
                              "actualizado_at": "2026-10-06T12:00:00+00:00"})
        self.assertEqual(leer.call_count, 1, "con caché: no se pregunta en cada orden")
        leer.return_value = {**fila, "valor": False}
        self.assertTrue(ov.habilitado(), "todavía en caché")
        self.assertFalse(ov.habilitado(refrescar=True))
        with mock.patch.object(settings, "ordenes_venta_enabled", True):
            self.assertFalse(ov.habilitado(refrescar=True), "la fila apagada manda sobre la variable")

    def test_sin_fila_manda_el_respaldo_que_vale_false(self):
        self.parche("_leer_bandera", mock.MagicMock(return_value=None))
        self.assertEqual(ov.estado_bandera("ordenes_venta"), self.APAGADA)
        with mock.patch.object(settings, "ordenes_venta_enabled", True):
            self.assertTrue(ov.habilitado(refrescar=True))
            # La de la generación automática NO tiene variable: sin fila, apagada.
            self.assertFalse(ov.generacion_auto(refrescar=True))
            self.assertEqual(ov.estado_bandera("ov_generacion_auto"), self.APAGADA)

    def test_si_no_se_puede_leer_esta_apagada_diga_lo_que_diga_la_variable(self):
        leer = self.parche("_leer_bandera", mock.MagicMock(side_effect=ErrorPg("42P01")))
        with mock.patch.object(settings, "ordenes_venta_enabled", True), \
                self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            self.assertFalse(ov.habilitado())
            self.assertEqual(ov.estado_bandera("ordenes_venta"), self.APAGADA)
        # El fallo se recuerda POCO: cuando kubera vuelve no hay que esperar medio minuto.
        self.assertLess(ov._TTL_FALLO, ov._TTL_BANDERA)
        leer.side_effect = None
        leer.return_value = {"valor": True, "motivo": None, "actualizado_por": None,
                             "actualizado_at": None}
        ov._olvidar_cache()
        self.assertTrue(ov.habilitado())
        # Con kubera en pausa ni se intenta, y se toma apagada.
        ov._olvidar_cache()
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            ov.anotar_caida(psycopg2.OperationalError("timeout"))
        antes = leer.call_count
        self.assertFalse(ov.habilitado())
        self.assertEqual(leer.call_count, antes)

    def test_con_un_cursor_prestado_se_lee_ese_y_no_se_guarda_en_cache(self):
        leer = self.parche("_leer_bandera", mock.MagicMock(return_value={"valor": True}))
        cur = object()
        self.assertTrue(ov.habilitado(cur=cur))
        self.assertTrue(ov.habilitado(cur=cur))
        self.assertEqual([c.args for c in leer.call_args_list],
                         [("ordenes_venta", cur), ("ordenes_venta", cur)])
        leer.return_value = None
        self.assertFalse(ov.habilitado(), "lo leído dentro de una transacción ajena no se recuerda")


class SinMigracionYSinBase(Limpia):
    def test_la_pregunta_se_hace_una_vez_y_se_recuerda(self):
        leer = self.parche("_leer_tablas", mock.MagicMock(return_value=True))
        self.assertTrue(ov.tablas_listas() and ov.tablas_listas())
        self.assertEqual(leer.call_count, 1)
        self.assertIn("to_regclass('ventas.ov_ordenes')", ov._SQL_HAY_TABLAS)
        self.assertIn("to_regclass('almacen.almacenes')", ov._SQL_HAY_TABLAS)
        self.assertIn("to_regclass('almacen.stock_almacen')", ov._SQL_HAY_TABLAS)
        leer.return_value = False
        self.assertTrue(ov.tablas_listas())
        self.assertFalse(ov.tablas_listas(refrescar=True))

    def test_sin_las_tablas_listar_y_el_estado_no_truenan_lo_dicen(self):
        self.parche("_leer_tablas", mock.MagicMock(return_value=False))
        self.parche("_leer_bandera", mock.MagicMock(return_value=None))
        with mock.patch.object(ov.sdb, "get_cursor", SIN_BASE):
            r = ov.listar(por_pagina=25)
            self.assertEqual(r, {"ok": False, "falta_migracion": True, "ordenes": [], "total": 0,
                                 "pagina": 1, "por_pagina": 25, "paginas": 1,
                                 "motivo": str(ov.FaltaMigracion()),
                                 "conteos": dict.fromkeys(ov.FILTROS, 0)})
            e = ov.estado_modulo(OPER)
            self.assertEqual((e["ok"], e["falta_migracion"], e["habilitado"], e["bodegas"]),
                             (False, True, False, []))
            self.assertEqual(e["archivos"]["disponible"], False)
            self.assertEqual(e["yo"]["escribe"], True)
            for r in (ov.venta_marketplace("123"), ov.ventas_pendientes()):
                self.assertEqual((r["ok"], r["ventas"]), (False, []))
            for fn in (lambda: ov.obtener(7, OPER), lambda: ov.mensajes(7),
                       lambda: ov.crear_borrador({"lineas": []}, OPER, "clave"),
                       lambda: ov.canal_cancelo(7, "CANCELLED", "", False),
                       lambda: ov.buscar_skus("zz"), lambda: ov.bajar_archivo(7, 1, OPER)):
                with self.assertRaises(ov.FaltaMigracion):
                    fn()

    def test_no_saber_no_es_lo_mismo_que_faltan(self):
        self.parche("_leer_tablas", mock.MagicMock(side_effect=ov.SinBase()))
        self.parche("_leer_bandera", mock.MagicMock(return_value=None))
        self.assertIsNone(ov._tablas())
        self.assertFalse(ov.tablas_listas(), "nunca truena")
        with self.assertRaises(ov.SinBase):
            ov.listar()
        e = ov.estado_modulo(OPER)
        self.assertEqual((e["ok"], e["falta_migracion"]), (False, False))

    def test_estado_del_modulo_nunca_lanza_ni_enseña_el_error(self):
        self.parche("_leer_bandera", mock.MagicMock(return_value=None))
        self.parche("_tablas", mock.MagicMock(return_value=True))
        self.parche("bodegas", mock.MagicMock(side_effect=psycopg2.OperationalError(
            'connection to server at "db.host-secreto.supabase.co" failed')))
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            e = ov.estado_modulo(ADMIN)
        self.assertEqual((e["ok"], e["motivo"]), (False, "No se pudo leer kubera; intenta de nuevo."))
        self.assertNotIn("host-secreto", str(e))

    def test_nombre_de_nunca_lanza(self):
        ov._NOMBRES.clear()
        self.addCleanup(ov._NOMBRES.clear)
        with mock.patch.object(ov.sdb, "fetch_one", side_effect=RuntimeError("sin base")):
            self.assertIsNone(ov.nombre_de("oper@prueba.test"))
        self.assertIsNone(ov.nombre_de("servicio"))
        self.assertIsNone(ov.nombre_de(""))
        with mock.patch.object(ov.sdb, "fetch_one", return_value={"nombre": " Olga "}) as f:
            self.assertEqual(ov.nombre_de("Oper@Prueba.test"), "Olga")
            self.assertEqual(ov.nombre_de("oper@prueba.test"), "Olga")
        self.assertEqual(f.call_count, 1, "caché de 5 minutos")


class KuberaCaida(Limpia):
    """Con la base muda, cada sondeo de la pantalla esperaba 10 s y dejaba un
    traceback. Ahora: una línea por minuto, y las lecturas que la pantalla
    repite solas contestan de inmediato mientras dura la pausa."""

    def test_que_cuenta_como_caida(self):
        self.assertTrue(ov.es_caida(psycopg2.OperationalError("could not connect to server")))
        self.assertTrue(ov.es_caida(psycopg2.InterfaceError("connection already closed")))
        self.assertFalse(ov.es_caida(psycopg2.errors.DeadlockDetected("deadlock")),
                         "un interbloqueo es de la sentencia, no de la conexión")
        self.assertFalse(ov.es_caida(psycopg2.errors.QueryCanceled("statement timeout")))
        self.assertFalse(ov.es_caida(RuntimeError("otra cosa")))
        self.assertFalse(ov.es_caida(ErrorPg(None)), "sólo errores de psycopg2")

    def test_una_linea_por_minuto_y_la_pausa_corta_las_lecturas(self):
        caida = psycopg2.OperationalError(
            'connection to server at "pooler.supabase.com", port 6543 failed: timeout\notra línea')
        gc = base_que_truena(caida, caida)
        with mock.patch.object(ov.sdb, "get_cursor", gc), \
                mock.patch.object(ov.sdb, "reintentar_transitorio", side_effect=lambda f: f()), \
                mock.patch.object(ov, "_tablas", return_value=True):
            with self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
                with self.assertRaises(ov.SinBase) as e:
                    ov.listar()
                # La SEGUNDA lectura, dentro de la pausa, ni toca la base.
                with self.assertRaises(ov.SinBase):
                    ov.listar()
                with self.assertRaises(ov.SinBase):
                    ov.mensajes(7)
                est = ov.estado_modulo(OPER)
                self.assertFalse(ov.habilitado(), "en pausa la bandera se toma apagada")
                with self.assertRaises(ov.SinBase):
                    ov._fila("select 1")      # una lectura o escritura cualquiera SÍ lo intenta
        self.assertEqual(len(gc.llamadas), 2, "sólo intentaron la primera lista y el _fila")
        self.assertEqual((e.exception.status, str(e.exception)),
                         (502, "kubera no contesta; intenta de nuevo en un momento."))
        self.assertEqual((est["ok"], est["falta_migracion"], est["motivo"]),
                         (False, False, "kubera no contesta; intenta de nuevo en un momento."))
        self.assertEqual(len(logs.output), 1, "una línea, no una por petición")
        self.assertNotIn("Traceback", logs.output[0])
        self.assertNotIn("otra línea", logs.output[0], "sólo la primera línea del error")
        self.assertTrue(ov.en_pausa())

    def test_cuando_kubera_contesta_la_pausa_se_levanta(self):
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            ov.anotar_caida(psycopg2.OperationalError("x"))
        self.assertTrue(ov.en_pausa())
        with mock.patch.object(ov.sdb, "get_cursor", base_que_truena()):
            self.assertEqual(ov._fila("select 1"), {"ok": 1})
        self.assertFalse(ov.en_pausa(), "la primera consulta que contesta levanta la pausa")

    def test_el_aviso_vuelve_a_salir_pasado_el_minuto(self):
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
            ov.anotar_caida(psycopg2.OperationalError("a"))
            ov.anotar_caida(psycopg2.OperationalError("b"))
            ov._caida["aviso"] = ov._caida["aviso"] - ov._AVISO_CAIDA_S - 1
            ov.anotar_caida(psycopg2.OperationalError("c"))
        self.assertEqual(len(logs.output), 2)

    def test_el_nombre_visible_no_insiste_y_usa_el_ultimo_que_supo(self):
        ov._NOMBRES.clear()
        self.addCleanup(ov._NOMBRES.clear)
        with mock.patch.object(ov.sdb, "fetch_one", return_value={"nombre": "Olga"}):
            self.assertEqual(ov.nombre_de("oper@prueba.test"), "Olga")
        ov._NOMBRES["oper@prueba.test"] = (0.0, "Olga")             # ya vencido
        with mock.patch.object(ov.sdb, "fetch_one",
                               side_effect=psycopg2.OperationalError("timeout")) as f, \
                self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            self.assertIsNone(ov.nombre_de("otro@prueba.test"))     # arranca la pausa
            self.assertEqual(ov.nombre_de("oper@prueba.test"), "Olga", "el último que se supo")
            self.assertIsNone(ov.nombre_de("nuevo@prueba.test"))
        self.assertEqual(f.call_count, 1, "en pausa no se vuelve a preguntar")


class Pdf(Limpia):
    PDF = b"%PDF-1.7 comprobante de prueba"

    def setUp(self) -> None:
        super().setUp()
        self.o = orden("confirmada")
        self.parche("_tablas", mock.MagicMock(return_value=True))
        self.parche("_leer_id", mock.MagicMock(side_effect=lambda *a, **k: self.o))
        self.parche("habilitado", mock.MagicMock(return_value=True))
        self.bucket = self.parche("hay_bucket", mock.MagicMock(return_value=True))
        self.transicion = self.parche("_transicion", mock.MagicMock(return_value={"id": 1}))
        self.subir = mock.MagicMock(return_value="subido")
        p = mock.patch.object(ov.ov_storage, "subir", self.subir)
        p.start()
        self.addCleanup(p.stop)

    def test_se_sube_por_su_huella_y_despues_se_indexa(self):
        r = ov.subir_archivo(7, OPER, "comprobante", "pago.pdf", self.PDF)
        sha = hashlib.sha256(self.PDF).hexdigest()
        self.subir.assert_called_once_with(f"OV-00007/{sha}.pdf", self.PDF)
        operacion, sql, params = self.transicion.call_args.args[:3]
        self.assertEqual((operacion, sql), ("subir_archivo", ov.SQL_ARCHIVO_ALTA))
        self.assertEqual((params["ruta"], params["sha"], params["bytes"], params["archivo"],
                          params["tipo"], params["q"], params["nombre"], params["via"]),
                         (f"OV-00007/{sha}.pdf", sha, len(self.PDF), "pago.pdf", "comprobante",
                          "oper@prueba.test", "Olga Operadora", "panel"))
        self.assertEqual((r["ok"], r["mensaje"]), (True, "PDF adjunto: pago.pdf."))

    def test_el_tipo_es_obligatorio_y_las_guias_no_se_guardan(self):
        for tipo in (None, "", "guia", "etiqueta", "otro"):
            with self.assertRaises(ov.Invalido) as e:
                ov.subir_archivo(7, OPER, tipo, "x.pdf", self.PDF)
            self.assertIn("Las guías con la dirección del comprador NO se guardan", str(e.exception))
        for tipo in ("comprobante", " Factura ", "ENVIO_FULL"):
            ov.subir_archivo(7, OPER, tipo, "x.pdf", self.PDF + tipo.encode())
        self.assertEqual(self.subir.call_count, 3)
        self.assertEqual(ov.TIPOS_ARCHIVO, ("comprobante", "factura", "envio_full"))

    def test_sin_bucket_es_un_409_y_no_llega_a_storage(self):
        self.bucket.return_value = False
        with self.assertRaises(ov.Conflicto) as e:
            ov.subir_archivo(7, OPER, "factura", "x.pdf", self.PDF)
        self.assertEqual(e.exception.status, 409)
        self.assertIn("falta crear el bucket «ordenes-venta»", str(e.exception))
        self.subir.assert_not_called()
        self.transicion.assert_not_called()

    def test_lo_que_no_pasa_no_llega_a_storage(self):
        for datos, clase in ((b"", ov.Invalido), (b"GIF89a", ov.Invalido), ("texto", ov.Invalido),
                             (None, ov.Invalido), (b"%PDF" + b"0" * ov.MAX_PDF, ov.Grande)):
            with self.assertRaises(clase):
                ov.subir_archivo(7, OPER, "factura", "x.pdf", datos)
        with self.assertRaises(ov.SinPermiso):
            ov.subir_archivo(7, LECT, "factura", "x.pdf", self.PDF)
        self.o["borrada_at"] = "2026-10-06"
        with self.assertRaises(ov.Invalido):
            ov.subir_archivo(7, OPER, "factura", "x.pdf", self.PDF)
        self.subir.assert_not_called()
        self.transicion.assert_not_called()

    def test_el_tope_exacto_si_entra(self):
        ov.subir_archivo(7, OPER, "factura", "x.pdf", b"%PDF" + b"0" * (ov.MAX_PDF - 4))
        self.subir.assert_called_once()
        self.assertEqual(ov.MAX_PDF, 15 * 1024 * 1024)

    def test_storage_caido_es_502_y_no_deja_indice(self):
        self.subir.side_effect = ov_storage.StorageError(
            "HTTP 503 al subir OV-00007/abc.pdf: https://ref.supabase.co/storage/v1/…")
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
            with self.assertRaises(ov.FallaStorage) as e:
                ov.subir_archivo(7, OPER, "factura", "x.pdf", self.PDF)
        self.assertEqual(e.exception.status, 502)
        # Ni el host, ni el bucket, ni la ruta salen hacia afuera…
        self.assertEqual(str(e.exception), "No se pudo guardar el PDF en Storage; intenta de nuevo.")
        self.assertIn("HTTP 503", "\n".join(logs.output), "…el detalle queda en el log")
        self.transicion.assert_not_called()

    def test_bajar_exige_operador_antes_de_tocar_la_base(self):
        with mock.patch.object(ov.sdb, "get_cursor", SIN_BASE):
            for quien in (LECT, NADIE):
                with self.assertRaises(ov.SinPermiso) as e:
                    ov.bajar_archivo(7, 1, quien)
                self.assertEqual(e.exception.status, 403)

    def test_el_mismo_pdf_ya_adjunto_ni_se_sube(self):
        self.o["archivos"] = [{"id": 9, "sha256": hashlib.sha256(self.PDF).hexdigest()}]
        r = ov.subir_archivo(7, OPER, "factura", "otra-vez.pdf", self.PDF)
        self.assertEqual(r["mensaje"], "Ese PDF ya estaba adjunto.")
        self.subir.assert_not_called()
        self.transicion.assert_not_called()


class CrearAutoPuro(unittest.TestCase):
    def test_la_venta_trae_su_llave_y_todo_su_contenido(self):
        v = ov._venta_auto({"canal": "TikTok", "cuenta": "cuentaprueba", "orden": " 577 ",
                            "total": "730", "comision": 73.5, "fecha": "2026-10-06T09:30:00-06:00",
                            "guia": "G-1"})
        self.assertEqual((v["mc"], v["mu"], v["mo"], v["canal"]),
                         ("tiktok", "CUENTAPRUEBA", "577", "tiktok"))
        self.assertEqual((v["total"], v["comision"], v["moneda"], v["guia"], v["descripcion"]),
                         (Decimal("730.00"), Decimal("73.50"), "MXN", "G-1", None))
        self.assertEqual(v["fecha_venta"].isoformat(), "2026-10-06T09:30:00-06:00")
        # Las llaves de la tabla también valen, y un canal fuera del catálogo va como «otro».
        v = ov._venta_auto({"mp_canal": "liverpool", "mp_cuenta": "X", "mp_orden": "1"})
        self.assertEqual((v["mc"], v["canal"], v["total"]), ("liverpool", "otro", None))
        for mala in ("texto", {}, {"canal": "tiktok", "orden": "1"}, {"canal": "tiktok", "cuenta": "X"},
                     {"canal": "tiktok", "cuenta": "X", "orden": "1", "moneda": "pesos"},
                     {"canal": "tiktok", "cuenta": "X", "orden": "1", "total": -5}):
            with self.assertRaises(ov.Invalido, msg=str(mala)):
                ov._venta_auto(mala)

    def test_los_renglones_se_agrupan_por_sku_antes_de_la_sentencia(self):
        r = ov._lineas_auto([
            {"sku": "ZZPRUEBA-1", "cantidad": 2, "precio_unitario": 200, "titulo": "Uno",
             "imagen": "https://img.prueba.test/1.jpg"},
            {"sku": "ZZPRUEBA-2", "n": 1, "precio_unitario": 130},
            {"sku": "zzprueba-1", "cantidad": 1, "precio_unitario": 100}])
        self.assertEqual(r, [
            {"linea": 1, "sku": "ZZPRUEBA-1", "n": 3, "titulo": "Uno",
             "imagen": "https://img.prueba.test/1.jpg", "precio_unitario": Decimal("166.67")},
            {"linea": 2, "sku": "ZZPRUEBA-2", "n": 1, "titulo": None, "imagen": None,
             "precio_unitario": Decimal("130.00")}])
        for malas in (None, [], "x", [1], [{"sku": "", "cantidad": 1}], [{"sku": "A", "cantidad": 0}]):
            with self.assertRaises(ov.Invalido):
                ov._lineas_auto(malas)


class Ventas(unittest.TestCase):
    def test_cancelada_por_cualquiera_de_sus_senales(self):
        c = ov.cancelada_en_canal
        self.assertTrue(c("tiktok", "CANCELLED", "processing"))
        self.assertTrue(c("mercado_libre", "canceled", None))
        self.assertTrue(c("walmart", "Created", "cancelled"))
        self.assertTrue(c("temu", "3", "processing"))
        self.assertTrue(c("temu", "2", "processing", "cancelada_por_cancelar"))
        self.assertTrue(c("tiktok", None, None, "nacio_cancelada"))
        self.assertFalse(c("temu", "2", "processing", "creada"))
        self.assertFalse(c("tiktok", "3", "processing"), "el 3 sólo es cancelada en Temu")
        self.assertFalse(c("temu", None, None))

    def test_venta_desde_channel_orders(self):
        c = {"canal": "temu", "cuenta": "CUENTAPRUEBA", "orden": "T-1", "total": Decimal("500.00"),
             "comision": Decimal("50.00"), "es_fulfillment": False, "estado_canal": "2",
             "estado_wc": "processing", "fecha": datetime(2026, 10, 2, 18, tzinfo=timezone.utc),
             "items": [{"sku": "A", "titulo": "Uno", "cantidad": 2, "precio_unitario": 200,
                        "es_fulfillment": False},
                       {"sku": "a", "titulo": "Uno", "cantidad": 1, "precio_unitario": 200,
                        "es_fulfillment": False},
                       {"sku": None, "titulo": "sin mapear", "cantidad": 4, "precio_unitario": 1,
                        "es_fulfillment": False},
                       {"sku": "B", "titulo": None, "cantidad": 1, "precio_unitario": None,
                        "es_fulfillment": True}]}
        a = {"canal": "temu", "cuenta": "CUENTAPRUEBA", "orden": "T-1", "accion": "creada",
             "guia": "G-1", "paqueteria": "Paquetería de prueba", "total": Decimal("1"),
             "items": [{"sku": "b", "titulo": "Dos", "imagen": "https://img.prueba.test/b.jpg",
                        "cantidad": 1, "precio_unitario": 99.5}]}
        ligadas = [{"id": 4, "folio": "OV-00004", "estado": "confirmada", "mp_canal": "temu",
                    "mp_cuenta": "CUENTAPRUEBA", "mp_orden": "T-1"}]
        v = ov._venta(c, a, ligadas)
        self.assertEqual((v["total"], v["comision"], v["neto"]), (500.0, 50.0, 450.0))
        self.assertEqual((v["piezas"], v["renglones_sin_sku"]), (8, 1))
        self.assertEqual(v["lineas"], [
            {"sku": "A", "cantidad": 3, "precio_unitario": 200.0, "titulo": "Uno", "imagen": None},
            {"sku": "B", "cantidad": 1, "precio_unitario": 99.5, "titulo": "Dos",
             "imagen": "https://img.prueba.test/b.jpg"}])
        self.assertTrue(v["es_fulfillment"], "basta UN renglón FULL")
        self.assertEqual((v["guia"], v["paqueteria"], v["entrega_limite"]),
                         ("G-1", "Paquetería de prueba", None))
        self.assertEqual(v["ov"], {"id": 4, "folio": "OV-00004", "estado": "confirmada"})
        self.assertEqual(v["fecha"], "2026-10-02T18:00:00+00:00")
        self.assertFalse(v["cancelada"])
        self.assertEqual(set(v), {"canal", "cuenta", "orden", "fecha", "estado_canal", "estado_wc",
                                  "cancelada", "es_fulfillment", "total", "comision", "neto",
                                  "guia", "paqueteria", "entrega_limite", "piezas", "lineas",
                                  "renglones_sin_sku", "ov"},
                         "ya no trae «almacen»: la bodega de Odoo no es la de la orden")

    def test_el_mismo_sku_a_dos_precios_se_promedia_y_conserva_el_dinero(self):
        lineas, piezas, sin_sku = ov._lineas_venta(
            [{"sku": "A", "cantidad": 2, "precio_unitario": 150},
             {"sku": "a", "cantidad": 1, "precio_unitario": 75}], [])
        self.assertEqual((lineas, piezas, sin_sku),
                         ([{"sku": "A", "cantidad": 3, "precio_unitario": 125.0, "titulo": None,
                            "imagen": None}], 3, 0))

    def test_sin_comision_conocida_no_hay_neto(self):
        c = {"canal": "amazon", "cuenta": "CUENTAPRUEBA", "orden": "A-1", "total": Decimal("10"),
             "comision": None, "es_fulfillment": True, "estado_canal": None, "estado_wc": None,
             "fecha": None, "items": []}
        v = ov._venta(c, None, [])
        self.assertEqual((v["total"], v["comision"], v["neto"]), (10.0, None, None))
        self.assertTrue(v["es_fulfillment"], "sin renglones manda el encabezado")
        otra_cuenta = [{"id": 1, "folio": "OV-00001", "estado": "borrador", "mp_canal": "amazon",
                        "mp_cuenta": "OTRACUENTAPRUEBA", "mp_orden": "A-1"}]
        self.assertIsNone(ov._venta(c, None, otra_cuenta)["ov"])

    def test_lo_que_sale_por_la_api_no_lleva_decimal_ni_datetime(self):
        fila = {"id": 1, "total": Decimal("10.50"), "creado_at": datetime(2026, 10, 6, tzinfo=timezone.utc),
                "renglones": Decimal("2"), "skus": ["A", "a", "B"], "bodegas": ["ENSAYO", None],
                "lineas": [1], "archivos": [2]}
        o = ov._resumen(fila)
        self.assertEqual((o["total"], o["creado_at"], o["renglones"], o["skus"], o["bodegas"]),
                         (10.5, "2026-10-06T00:00:00+00:00", 2, ["A", "B"], ["ENSAYO"]))
        self.assertNotIn("lineas", o)
        self.assertNotIn("reserva", o, "el estado del apartado lo deriva la pantalla")
        for k in ("piezas", "piezas_apartadas", "piezas_entregadas", "renglones_entregados",
                  "n_archivos", "n_mensajes"):
            self.assertEqual(o[k], 0)


class Storage(unittest.TestCase):
    def setUp(self) -> None:
        for campo, valor in (("supabase_url", "https://abc.supabase.co/"),
                             ("supabase_service_role_key", "llave-de-prueba")):
            p = mock.patch.object(settings, campo, valor)
            p.start()
            self.addCleanup(p.stop)

    def resp(self, status: int, texto: str = "", contenido: bytes = b"") -> mock.MagicMock:
        r = mock.MagicMock()
        r.status_code, r.ok, r.text, r.content = status, 200 <= status < 300, texto, contenido
        return r

    def pedir(self, respuesta):
        return mock.patch.object(ov_storage.requests, "request", return_value=respuesta)

    def test_subir_nunca_pisa(self):
        with self.pedir(self.resp(200)) as req:
            self.assertEqual(ov_storage.subir("OV-00001/abc.pdf", b"%PDF"), "subido")
        (metodo, url), kw = req.call_args.args, req.call_args.kwargs
        self.assertEqual((metodo, url), (
            "POST", "https://abc.supabase.co/storage/v1/object/ordenes-venta/OV-00001/abc.pdf"))
        self.assertEqual(kw["headers"], {"Authorization": "Bearer llave-de-prueba",
                                         "apikey": "llave-de-prueba",
                                         "Content-Type": "application/pdf", "x-upsert": "false"})
        self.assertEqual((kw["data"], kw["timeout"]), (b"%PDF", 30))
        for ya in (self.resp(409, "Duplicate"), self.resp(400, '{"error":"Duplicate"}'),
                   self.resp(400, "The resource already exists")):
            with self.pedir(ya):
                self.assertEqual(ov_storage.subir("r.pdf", b"%PDF"), "existe")
        for mal in (self.resp(500, "boom"), self.resp(400, "bad"), self.resp(413, "too large")):
            with self.pedir(mal), self.assertRaises(ov_storage.StorageError):
                ov_storage.subir("r.pdf", b"%PDF")

    def test_bajar(self):
        with self.pedir(self.resp(200, contenido=b"%PDF x")) as req:
            self.assertEqual(ov_storage.bajar("OV-00001/abc.pdf"), b"%PDF x")
        self.assertEqual(req.call_args.args, (
            "GET",
            "https://abc.supabase.co/storage/v1/object/authenticated/ordenes-venta/OV-00001/abc.pdf"))
        with self.pedir(self.resp(404, "not found")), self.assertRaises(ov_storage.StorageError):
            ov_storage.bajar("no-esta.pdf")

    def test_borrar_lo_que_ya_no_estaba_no_es_error(self):
        with self.pedir(self.resp(200)) as req:
            self.assertIsNone(ov_storage.borrar("OV-00001/abc.pdf"))
        self.assertEqual(req.call_args.args, (
            "DELETE", "https://abc.supabase.co/storage/v1/object/ordenes-venta/OV-00001/abc.pdf"))
        for ya_no in (self.resp(404, "Not Found"),
                      self.resp(400, '{"statusCode":"404","error":"not_found"}'),
                      self.resp(400, '{"message":"Object not found"}')):
            with self.pedir(ya_no):
                self.assertIsNone(ov_storage.borrar("r.pdf"))
        for mal in (self.resp(500, "boom"), self.resp(403, "denied"), self.resp(400, "otra cosa")):
            with self.pedir(mal), self.assertRaises(ov_storage.StorageError):
                ov_storage.borrar("r.pdf")

    def test_sin_respuesta_o_sin_credenciales_es_storage_error(self):
        with mock.patch.object(ov_storage.requests, "request",
                               side_effect=requests.ConnectionError("sin red")):
            for fn in (lambda: ov_storage.subir("r.pdf", b"%PDF"), lambda: ov_storage.bajar("r.pdf"),
                       lambda: ov_storage.borrar("r.pdf")):
                with self.assertRaises(ov_storage.StorageError) as e:
                    fn()
                self.assertIn("sin red", str(e.exception))
        with mock.patch.object(settings, "supabase_service_role_key", ""), \
             mock.patch.object(ov_storage.requests, "request") as req:
            with self.assertRaises(ov_storage.StorageError):
                ov_storage.subir("r.pdf", b"%PDF")
            req.assert_not_called()
        self.assertEqual((ov_storage.BUCKET, ov.BUCKET), ("ordenes-venta", "ordenes-venta"))


if __name__ == "__main__":
    unittest.main()
