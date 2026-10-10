"""Pruebas UNITARIAS de las órdenes de venta propias (services/ordenes_venta.py y
services/ov_storage.py). Sin base y sin red: lo que toca kubera —y todo lo que
la base impone por su cuenta— se prueba en tests/test_ordenes_venta_bd.py contra
un Postgres local con las migraciones 0064, 0065 y 0068.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. Quién es quién, qué código HTTP lleva cada error, y que la variable de
     respaldo de la bandera nace en `false`.
  2. `permisos()` es PURA y dice la matriz completa por rol y por estado, con el
     porqué de cada «no». El rol manda antes que el estado; la bandera apagada
     bloquea confirmar, entregar y EDITAR UNA CONFIRMADA, nunca cancelar ni
     borrar; sin bucket no se adjunta; una borrada no se mueve. Una confirmada la
     edita y la cancela cualquiera que escribe (0071); editarla pide además que
     la base traiga esa migración.
  3. Qué error sale de cada «no»: rol → 403, `rev` vieja → 409, estado → 400,
     bandera apagada → 409 de modo prueba, sin bucket → 409, sin la 0071 → 409
     (y si no se pudo COMPROBAR que está, otro 409 con su propio texto: no saber
     no se dice como «falta la migración»).
  3b. EDITAR UNA CONFIRMADA, sin base: lo que ya salió no se toca (su eco se
     descarta), no puede quedar sin renglones por entregar, cada uno lleva una
     bodega que admita órdenes, los números de `linea`, el total automático, lo
     que viaja a la sentencia (la puerta, el rastro `editada` —también del título
     y la imagen de un renglón—) y qué sale de cada
     rechazo: no alcanzó (dice cuánto falta y no deja mensaje), bodega apagada,
     la base sin la 0071, la venta que ya tiene orden y el reintento propio.
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
  8b. EL CANAL CANCELA: quien avisa no manda `rev`, pero la guardia de una
     CONFIRMADA lleva la de la relectura de `canal_cancelo` en ese intento (el
     mensaje y lo que se suelta salen de la misma foto), y con `venta=` no toca
     una orden que ya no es de la venta que el canal canceló.
  9. Las banderas son filas: sin fila manda el respaldo; sin poder leer, APAGADA.
     Sin las tablas nada truena: lo dice. Y por la puerta de la 0071 se le
     pregunta al CATÁLOGO de Postgres, no al registro de migraciones, con tres
     respuestas: sí, no, y no se pudo comprobar.
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
    def p(self, o, quien, encendido=True, bucket=True, edicion=True) -> dict:
        return ov.permisos(o, quien, encendido, bucket, edicion)

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
        """Desde la 0071 (pedido de Brandon, 9-oct-2026) una confirmada la EDITA y
        la CANCELA cualquiera que escribe: ya no hace falta un administrador."""
        oper = self.p(orden("confirmada"), OPER)
        self.assertEqual(self.si(oper), {"editar", "entregar", "cancelar", "mensajes",
                                         "subir_archivo", "bajar_archivo"})
        self.assertEqual(set(oper["porque"]), {"confirmar", "borrar", "responder_salio",
                                               "salio_tarde", "borrar_archivo"})
        admin = self.p(orden("confirmada"), ADMIN)
        self.assertEqual(self.si(admin), {"editar", "entregar", "cancelar", "borrar", "mensajes",
                                          "subir_archivo", "bajar_archivo", "borrar_archivo"})
        # Lo único que sigue siendo de administrador sobre una confirmada: borrarla.
        self.assertEqual(self.si(admin) - self.si(oper), {"borrar", "borrar_archivo"})

    def test_editar_una_confirmada_y_lo_que_la_frena(self):
        confirmada = orden("confirmada")
        # Sin la 0071 en la base: no se ofrece, y se dice por qué. Por omisión
        # (`edicion` sin pasar) falla cerrado, como el bucket.
        for p in (self.p(confirmada, OPER, edicion=False), ov.permisos(confirmada, OPER, True)):
            self.assertFalse(p["editar"])
            self.assertEqual(p["porque"]["editar"],
                             "Editar una orden confirmada todavía no está habilitado en esta "
                             "base: falta la migración 0071.")
            self.assertTrue(p["cancelar"] and p["entregar"], "sólo editar depende de la 0071")
        # No haber podido COMPROBARLO (None: kubera tropezó al preguntar) cierra
        # igual, pero no se dice igual: «falta la migración» mandaría a buscar una
        # que sí está.
        sin_saber = self.p(confirmada, OPER, edicion=None)
        self.assertFalse(sin_saber["editar"])
        self.assertEqual(sin_saber["porque"]["editar"],
                         "No se pudo comprobar si esta base ya permite editar una orden "
                         "confirmada (la migración 0071); intenta de nuevo en unos segundos.")
        self.assertTrue(sin_saber["cancelar"] and sin_saber["entregar"])
        # La bandera apagada: editar puede APARTAR más, así que va con ella.
        apagada = self.p(confirmada, ADMIN, encendido=False)
        self.assertFalse(apagada["editar"])
        self.assertEqual(apagada["porque"]["editar"],
                         "Modo prueba: editar una confirmada está apagado (bandera "
                         "«ordenes_venta»)")
        # Con el «¿salió?» pendiente primero se contesta; y un envío a FULL no se edita aquí.
        marcada = orden("confirmada", canal_cancelo_at="2026-10-06T10:00:00+00:00")
        self.assertIn("contestar si salió", self.p(marcada, ADMIN)["porque"]["editar"])
        full = orden("confirmada", tipo="full")
        self.assertIn("FULL", self.p(full, ADMIN)["porque"]["editar"])
        self.assertTrue(self.p(full, ADMIN)["cancelar"])
        # El porqué que se dice es el que NO cambia recargando: estado antes que bandera,
        # y la bandera antes que la migración.
        self.assertIn("FULL", self.p(orden("confirmada", tipo="full", canal_cancelo_at="x"),
                                     OPER, encendido=False, edicion=False)["porque"]["editar"])
        self.assertIn("contestar si salió",
                      self.p(marcada, OPER, encendido=False, edicion=False)["porque"]["editar"])
        self.assertIn("Modo prueba",
                      self.p(confirmada, OPER, encendido=False, edicion=False)["porque"]["editar"])
        # Un BORRADOR se edita como siempre: ni la bandera ni la 0071 le aplican.
        for tipo in ("venta", "full"):
            self.assertTrue(self.p(orden("borrador", tipo=tipo), OPER, encendido=False,
                                   edicion=False)["editar"])

    def test_con_la_marca_del_canal_se_contesta_antes_de_entregar(self):
        marcada = orden("confirmada", canal_cancelo_at="2026-10-06T10:00:00+00:00")
        p = self.p(marcada, OPER)
        self.assertTrue(p["responder_salio"])
        self.assertFalse(p["entregar"])
        self.assertIn("contestar si salió", p["porque"]["entregar"])
        # Ni se edita ni se cancela a mano mientras la pregunta sigue abierta —tampoco
        # un administrador—: sería una tercera salida que se la salta.
        for quien in (OPER, ADMIN):
            p = self.p(marcada, quien)
            self.assertFalse(p["editar"] or p["cancelar"])
            for accion in ("editar", "cancelar"):
                self.assertEqual(p["porque"][accion],
                                 "El canal canceló esta venta con el paquete en camino: "
                                 "primero hay que contestar si salió")
        self.assertFalse(self.p(orden("confirmada"), OPER)["responder_salio"])
        self.assertFalse(self.p(marcada, LECT)["responder_salio"])

    def test_entregada_y_canceladas(self):
        for estado in ("entregada", "entregada_cancelada"):
            self.assertEqual(self.si(self.p(orden(estado), OPER)),
                             {"mensajes", "subir_archivo", "bajar_archivo"}, estado)
            self.assertEqual(self.si(self.p(orden(estado), ADMIN)),
                             {"borrar", "mensajes", "subir_archivo", "bajar_archivo",
                              "borrar_archivo"}, estado)
        # Editar sigue siendo «no» fuera de borrador y de confirmada, con su porqué.
        for estado, rotulo in (("entregada", "entregada"), ("cancelada", "cancelada"),
                               ("entregada_cancelada", "entregada y cancelada")):
            for quien in (OPER, ADMIN):
                p = self.p(orden(estado), quien)
                self.assertFalse(p["editar"], estado)
                self.assertEqual(p["porque"]["editar"],
                                 f"La orden ya está {rotulo}: su contenido no cambia. Si se "
                                 "capturó mal, un administrador puede borrarla")
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
        for quien in (OPER, ADMIN):
            c = self.p(orden("confirmada"), quien, encendido=False)
            self.assertFalse(c["entregar"] or c["editar"], "ni entregar ni editar una confirmada")
            self.assertTrue(c["cancelar"], "soltar stock no depende de la bandera")
        self.assertTrue(c["borrar"])
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
        self.edicion = self.parche("edicion_lista", mock.MagicMock(return_value=True))

    def test_rol_es_403_aunque_la_rev_sea_vieja(self):
        with self.assertRaises(ov.SinPermiso) as e:
            ov._preparar(7, 1, OPER, "borrar")
        self.assertEqual((e.exception.status, str(e.exception)),
                         (403, "Sólo un administrador puede borrar una orden."))
        with self.assertRaises(ov.SinPermiso) as e:
            ov._preparar(7, 1, LECT, "cancelar")
        self.assertEqual(str(e.exception), "Tu rol es de sólo lectura.")
        # Cancelar una confirmada ya NO es de administrador: al operador lo único
        # que lo frena es la rev.
        with self.assertRaises(ov.Conflicto):
            ov._preparar(7, 1, OPER, "cancelar")
        self.assertIs(ov._preparar(7, 3, OPER, "cancelar"), self.o)

    def test_rev_vieja_es_409_antes_que_el_estado(self):
        self.o = orden("entregada")
        with self.assertRaises(ov.Conflicto):
            ov._preparar(7, 2, OPER, "editar")           # además ya no se edita: manda la rev
        for sin_rev in (None, 0, -1, "x", True):
            with self.assertRaises(ov.Invalido):
                ov._preparar(7, sin_rev, OPER, "entregar")

    def test_estado_es_400_y_apagado_es_409_de_modo_prueba(self):
        for estado in ("entregada", "cancelada", "entregada_cancelada"):
            self.o = orden(estado)
            with self.assertRaises(ov.Invalido) as e:
                ov._preparar(7, 3, OPER, "editar")
            self.assertEqual(e.exception.status, 400)
            self.assertIn("su contenido no cambia", str(e.exception))
        self.edicion.assert_not_called()                # sólo una confirmada pregunta por la 0071
        self.o = orden("confirmada")
        self.encendido.return_value = False
        for accion in ("entregar", "editar"):
            with self.assertRaises(ov.Apagado) as e:
                ov._preparar(7, 3, OPER, accion)
            self.assertEqual(e.exception.status, 409)
            self.assertIn("modo prueba", str(e.exception))
        self.assertIs(ov._preparar(7, 3, OPER, "cancelar"), self.o, "cancelar no mira la bandera")

    def test_editar_una_confirmada_pide_la_0071_y_lo_dice_con_un_409(self):
        self.assertIs(ov._preparar(7, 3, OPER, "editar"), self.o)
        self.edicion.assert_called_once_with(cur=None)
        self.edicion.return_value = False
        with self.assertRaises(ov.Conflicto) as e:
            ov._preparar(7, 3, OPER, "editar")
        self.assertEqual((e.exception.status, str(e.exception)),
                         (409, "Editar una orden confirmada todavía no está habilitado en esta "
                               "base: falta la migración 0071."))
        # Si no se pudo COMPROBAR (None) también es un 409 —la duda cierra—, pero
        # con su propio texto: no manda a buscar una migración que sí está.
        self.edicion.return_value = None
        with self.assertRaises(ov.Conflicto) as e:
            ov._preparar(7, 3, OPER, "editar")
        self.assertEqual((e.exception.status, str(e.exception)),
                         (409, "No se pudo comprobar si esta base ya permite editar una orden "
                               "confirmada (la migración 0071); intenta de nuevo en unos "
                               "segundos."))
        self.edicion.return_value = False
        # La rev vieja se dice antes (recargar sí la arregla), y el rol antes que todo.
        with self.assertRaises(ov.Conflicto) as e:
            ov._preparar(7, 2, OPER, "editar")
        self.assertEqual(str(e.exception), "La orden cambió mientras tanto; se recargó.")
        with self.assertRaises(ov.SinPermiso):
            ov._preparar(7, 2, LECT, "editar")
        # Las demás acciones no dependen de la 0071, ni la preguntan.
        self.edicion.reset_mock()
        for accion in ("cancelar", "entregar"):
            self.assertIs(ov._preparar(7, 3, OPER, accion), self.o)
        self.edicion.assert_not_called()
        # Con el «¿salió?» pendiente, o si es un envío a FULL, es un 400 de estado.
        self.edicion.return_value = True
        for mas, dice in (({"canal_cancelo_at": "2026-10-06T10:00:00+00:00"}, "contestar si salió"),
                          ({"tipo": "full"}, "FULL")):
            self.o = orden("confirmada", **mas)
            with self.assertRaises(ov.Invalido) as e:
                ov._preparar(7, 3, ADMIN, "editar")
            self.assertEqual(e.exception.status, 400)
            self.assertIn(dice, str(e.exception))

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
                                          "canal_cancelo_entregada_no_aplica",
                                          "bodega_no_admite_ov"})
        # Los de la sentencia que edita una confirmada, uno por uno: dos de negocio
        # (con sus palabras), el de la rev, y dos de invariante (bugs, que avisan).
        editar = set(re.findall(r"ops\.exigir\(.*?'(\w+)'\)", ov.SQL_EDITAR_CONFIRMADA, flags=re.S))
        self.assertEqual(editar, {"ov_no_esta_confirmada_o_cambio_rev", "bodega_no_admite_ov",
                                  "no_alcanzo", "renglones_no_cuadran",
                                  "editar_escrituras_no_cuadran"})
        self.assertEqual(ov.clasificar(kb001("editar_escrituras_no_cuadran"), "editar_confirmada"),
                         ("inesperado", "editar_escrituras_no_cuadran"))

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
        # El mismo texto, con otro título, es el de editar una confirmada.
        self.assertEqual(ov._cuerpo_guardada({"guia": 1, "paqueteria": 2},
                                             {"agregados": ["A"], "quitados": [], "cambiados": [1]},
                                             "Orden editada"),
                         "Orden editada · guía, paquetería; renglones: 1 agregado(s), 1 con cambios")
        self.assertEqual(ov._cuerpo_guardada({}, {"agregados": [], "quitados": [], "cambiados": []},
                                             "Orden editada"),
                         "Orden editada · renglones: reordenados")
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

    def test_la_diferencia_de_renglones_dice_tambien_el_titulo_y_la_imagen(self):
        """La huella (lo que decide si hay algo que escribir) cuenta título e
        imagen; la diferencia (lo que queda en la bitácora) tiene que contarlos
        también. Si no, un cambio que SÍ se guardó quedaba como «renglones:
        reordenados», sin el antes: el rastro mentía y la imagen no se recuperaba
        de ningún lado."""
        antes = [{"sku": "A", "almacen": "ENSAYO", "cantidad": 2, "precio_unitario": 10,
                  "titulo": "Audífonos", "imagen": "https://img.prueba.test/a.jpg"},
                 {"sku": "B", "almacen": "ENSAYO", "cantidad": 1, "precio_unitario": 5,
                  "titulo": "Cable", "imagen": None}]

        def con(**cambios) -> list[dict]:
            return [{**antes[0], **cambios}, dict(antes[1])]

        def dif(despues: list[dict]) -> tuple[list, str]:
            d = ov._dif_lineas(antes, despues)
            self.assertEqual((d["agregados"], d["quitados"]), ([], []))
            self.assertNotEqual(ov._huella_lineas(antes), ov._huella_lineas(despues),
                                "la huella lo ve: hay escritura")
            return d["cambiados"], ov._cuerpo_guardada({}, d, "Orden editada")

        un_cambio = "Orden editada · renglones: 1 con cambios"
        self.assertEqual(dif(con(titulo="Otra cosa")),
                         ([{"sku": "A", "titulo": ["Audífonos", "Otra cosa"]}], un_cambio))
        self.assertEqual(dif(con(imagen="https://img.prueba.test/b.jpg")),
                         ([{"sku": "A", "imagen": ["https://img.prueba.test/a.jpg",
                                                   "https://img.prueba.test/b.jpg"]}], un_cambio))
        # Mandar el renglón SIN título ni imagen (son opcionales) los BORRA: también
        # es un cambio, y lo que se va queda escrito.
        sin = [{"sku": "A", "almacen": "ENSAYO", "cantidad": 2, "precio_unitario": 10},
               {"sku": "B", "almacen": "ENSAYO", "cantidad": 1, "precio_unitario": 5}]
        self.assertEqual(dif(sin), (
            [{"sku": "A", "titulo": ["Audífonos", None],
              "imagen": ["https://img.prueba.test/a.jpg", None]},
             {"sku": "B", "titulo": ["Cable", None]}],
            "Orden editada · renglones: 2 con cambios"))
        # Junto con la cantidad y el precio, en la misma entrada.
        self.assertEqual(dif(con(cantidad=3, precio_unitario=12, titulo="Otra cosa"))[0],
                         [{"sku": "A", "cantidad": [2, 3], "precio_unitario": [10.0, 12.0],
                           "titulo": ["Audífonos", "Otra cosa"]}])
        # Vacío y nulo son lo mismo (como en la huella): eso NO es un cambio…
        igual = [{**antes[0]}, {**antes[1], "imagen": ""}]
        self.assertEqual(ov._huella_lineas(antes), ov._huella_lineas(igual))
        self.assertEqual(ov._dif_lineas(antes, igual)["cambiados"], [])
        # …y «reordenados» queda para cuando lo ÚNICO que cambió fue el orden.
        volteados = [dict(antes[1]), dict(antes[0])]
        self.assertEqual(dif(volteados), ([], "Orden editada · renglones: reordenados"))


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
            "SQL_CREAR", "SQL_GUARDAR", "SQL_EDITAR_CONFIRMADA", "SQL_CONFIRMAR", "SQL_ENTREGAR",
            "SQL_CANCELAR", "SQL_CANCELAR_CON_SALIDA", "SQL_CANCELAR_CANAL",
            "SQL_CANCELAR_CON_SALIDA_CANAL", "SQL_BORRAR", "SQL_CANAL_MARCA",
            "SQL_CANAL_CANCELO_ENTREGADA", "SQL_SALIO", "SQL_SALIO_TARDE", "SQL_CREAR_AUTO",
            "SQL_MENSAJE_SISTEMA", "SQL_MENSAJE_USUARIO", "SQL_ARCHIVO_ALTA", "SQL_ARCHIVO_BAJA"})

    # La puerta de la 0071: el ÚNICO `set local` que no es un timeout, y sólo en
    # la sentencia que edita una confirmada.
    PUERTA = "set local app.ov_edicion = %(puerta)s;\n"

    def test_cada_una_lleva_su_envoltura_y_es_una_sola_sentencia(self):
        for nombre, sql in self.sql.items():
            self.assertTrue(sql.startswith("set local lock_timeout = '4s';\n"
                                           "set local statement_timeout = '15s';\n"), nombre)
            cuerpo = sql[len(ov._ENVOLTURA):]
            sets = ["local", "local"]
            if nombre == "SQL_EDITAR_CONFIRMADA":
                # Su envío lleva un tercer `set local`, justo después de los dos de
                # la envoltura y ANTES de la sentencia. Quitado ése, se le exige
                # exactamente lo mismo que a las demás.
                self.assertTrue(cuerpo.startswith(self.PUERTA), "la puerta va en el mismo envío")
                cuerpo = cuerpo[len(self.PUERTA):]
                sets = ["local", "local", "local"]
            self.assertNotIn(";", cuerpo, f"{nombre}: una sola sentencia tras los set local")
            self.assertRegex(cuerpo, r"^(with|insert) ", nombre)
            # Regla 13: nada de estado de sesión en el pooler compartido. Los únicos
            # SET que son SENTENCIA (a inicio de línea; el SET de un UPDATE va
            # sangrado) son los `set local`, que mueren con la transacción.
            self.assertEqual(re.findall(r"(?im)^set\s+(\w+)", sql), sets, nombre)
            self.assertNotIn("set_config", sql, f"{nombre}: OV y libro no leen app.usuario")
            self.assertNotIn("set_session", sql)

    def test_solo_la_edicion_abre_la_puerta_y_solo_para_su_orden(self):
        """`app.ov_edicion` es lo que deja tocar el contenido de una confirmada. Si
        otra sentencia lo trajera, confirmar, entregar o cancelar podrían cambiar
        renglones sin rastro: ninguna lo nombra."""
        for nombre, sql in self.sql.items():
            if nombre != "SQL_EDITAR_CONFIRMADA":
                self.assertNotIn("ov_edicion", sql, nombre)
        sql = ov.SQL_EDITAR_CONFIRMADA
        self.assertEqual(sql.count("ov_edicion"), 1)
        self.assertTrue(sql.startswith(ov._ENVOLTURA + self.PUERTA + "with o as ("))
        # La puerta es un PARÁMETRO (el id de ESA orden), nunca un comodín ni un literal.
        self.assertNotRegex(sql, r"ov_edicion\s*=\s*'")
        # La guardia: CAS de rev, confirmada, viva, sin el «¿salió?» pendiente y nunca FULL.
        self.assertIn("where v.id = %(id)s and v.rev = %(rev)s and v.estado = 'confirmada' "
                      "and v.borrada_at is null\n     and v.canal_cancelo_at is null "
                      "and v.tipo <> 'full'", sql)
        # Los catorce campos del encabezado son los de guardar, cada uno con su bandera.
        for campo in ov._CAMPOS:
            for constante in (sql, ov.SQL_GUARDAR):
                self.assertRegex(constante, rf"{campo}\s+= case when %\(t_{campo}\)s\s+then "
                                            rf"%\({campo}\)s", campo)
        # Sólo toca renglones que NO han salido, y todos quedan apartados completos.
        self.assertIn("l.entregado_at is null", sql)
        self.assertEqual(sql.count("reservado = x.cantidad"), 1, "el UPDATE")
        self.assertRegex(sql, r"(?s)insert into ventas\.ov_lineas \(.*?almacen, reservado\)"
                              r".*?x\.almacen,\s+x\.cantidad\s+from o, x")
        # El saldo se mueve por la DIFERENCIA, y lo que sube sólo si alcanza.
        self.assertIn("set apartado = sa.apartado + b.delta", sql)
        self.assertIn("(b.delta < 0 or sa.libre >= b.delta)", sql)
        self.assertIn("(d.delta < 0 or alm.admite_ov)", sql, "soltar sí; apartar más, sólo si admite")
        self.assertNotIn("stock_mov", sql, "editar no saca ni mete piezas: el libro no se toca")
        self.assertNotIn("fisico", sql)

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
        self.assertEqual(usados, {"creada", "borrador_guardado", "confirmada", "editada",
                                  "entregada", "entregada_parcial", "cancelada", "borrada_admin",
                                  "canal_cancelo", "devolucion_esperada"})
        # `editada` VOLVIÓ al catálogo con la 0071 (9-oct-2026), y sólo lo escribe
        # la sentencia que edita una confirmada: es su rastro.
        self.assertIn("editada", ov.EVENTOS)
        self.assertEqual([n for n, sql in self.sql.items() if "'editada'" in sql],
                         ["SQL_EDITAR_CONFIRMADA"])
        # Los de la versión del 2-oct, que el CHECK ya no admite.
        for viejo in ("reserva", "regresada", "borrada", "archivo", "archivo_borrado",
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
        for nombre in ("SQL_GUARDAR", "SQL_EDITAR_CONFIRMADA", "SQL_CONFIRMAR", "SQL_ENTREGAR",
                       "SQL_CANCELAR", "SQL_CANCELAR_CON_SALIDA", "SQL_BORRAR", "SQL_SALIO",
                       "SQL_SALIO_TARDE"):
            sql = self.sql[nombre]
            self.assertIn("v.rev = %(rev)s", sql, f"{nombre}: las personas van con rev")
            self.assertIn("rev = v.rev + 1", sql.replace("rev            = v.rev + 1",
                                                         "rev = v.rev + 1"), nombre)
            self.assertIn("v.borrada_at is null", sql, nombre)
        # EL CANAL. Quien llama no manda `rev` (el proceso no trae una), pero las
        # tres sentencias que tocan una CONFIRMADA exigen —además del estado— la
        # `rev` de la lectura que `canal_cancelo` hizo en ese intento: desde la
        # 0071 una confirmada cambia entre la lectura y el CAS (sus renglones, y
        # hasta la venta a la que está ligada), y «sólo por estado» cancelaba la
        # orden de otra venta y dejaba la bitácora con piezas que ya no eran.
        # Los filtros de estado se quedan: son los que aguantan verse dos veces.
        guardias = {
            "SQL_CANCELAR_CANAL": "where v.id = %(id)s and v.rev = %(rev)s and v.estado = "
                                  "'confirmada' and v.canal_cancelo_at is null and "
                                  "v.borrada_at is null",
            "SQL_CANCELAR_CON_SALIDA_CANAL": "where v.id = %(id)s and v.rev = %(rev)s and "
                                             "v.canal_cancelo_at is null and v.estado = "
                                             "'confirmada' and v.borrada_at is null",
            "SQL_CANAL_MARCA": "where id = %(id)s and rev = %(rev)s and estado = 'confirmada' "
                               "and canal_cancelo_at is null\n     and borrada_at is null"}
        for nombre, guardia in guardias.items():
            self.assertIn(guardia, self.sql[nombre], nombre)
            self.assertEqual(self.sql[nombre].count("%(rev)s"), 1, f"{nombre}: sólo en la guardia")
        # Una ENTREGADA ya no cambia (ni su liga): ahí sí basta el estado.
        self.assertNotIn("%(rev)s", ov.SQL_CANAL_CANCELO_ENTREGADA)
        self.assertIn("where v.id = %(id)s and v.estado = 'entregada' and v.borrada_at is null",
                      ov.SQL_CANAL_CANCELO_ENTREGADA)
        self.assertIn("coalesce(v.devolucion_estado, 'pendiente')", ov.SQL_CANAL_CANCELO_ENTREGADA)
        # Con `rev` en las dos, siguen siendo OTRAS sentencias que las de una
        # persona: la del canal sólo toca una confirmada sin el «¿salió?» pendiente.
        self.assertNotEqual(ov.SQL_CANCELAR_CANAL, ov.SQL_CANCELAR)
        self.assertNotEqual(ov.SQL_CANCELAR_CON_SALIDA_CANAL, ov.SQL_CANCELAR_CON_SALIDA)

    def test_el_saldo_se_bloquea_en_orden_y_el_libro_va_en_la_misma_sentencia(self):
        for nombre in ("SQL_CONFIRMAR", "SQL_ENTREGAR", "SQL_CANCELAR", "SQL_CANCELAR_CON_SALIDA",
                       "SQL_BORRAR", "SQL_SALIO", "SQL_SALIO_TARDE", "SQL_CREAR_AUTO"):
            self.assertRegex(self.sql[nombre],
                             r"x as materialized \((?s:.*?)order by sa\.sku, sa\.almacen\s+"
                             r"for update of sa", nombre)
        # Editar una confirmada: el mismo candado, en su propia CTE (`x` ahí son
        # los renglones que llegan). Y en el orden de siempre: la orden → las
        # bodegas FOR SHARE → el saldo FOR UPDATE → las escrituras.
        editar = ov.SQL_EDITAR_CONFIRMADA
        self.assertRegex(editar, r"blq as materialized \((?s:.*?)order by sa\.sku, sa\.almacen\s+"
                                 r"for update of sa")
        self.assertRegex(editar, r"alm as \((?s:.*?)a\.fuente = 'kubera'(?s:.*?)for share")
        self.assertLess(editar.index("update ventas.ov_ordenes"), editar.index("for share"))
        self.assertLess(editar.index("for share"), editar.index("for update of sa"))
        self.assertLess(editar.index("for update of sa"), editar.index("update almacen.stock_almacen"))
        self.assertLess(editar.index("update almacen.stock_almacen"),
                        editar.index("delete from ventas.ov_lineas"))
        self.assertIn("exists (select 1 from o)", editar, "sin la orden no se bloquea ningún saldo")
        for nombre in ("SQL_ENTREGAR", "SQL_SALIO", "SQL_SALIO_TARDE"):
            sql = self.sql[nombre]
            self.assertIn("insert into almacen.stock_mov", sql, nombre)
            self.assertIn("'salida_ov'", sql)
            self.assertIn("'ov:' || %(id)s || ':linea:' || s.linea_id || ':salida'", sql,
                          f"{nombre}: la MISMA clave para entregar y para «¿salió?»")
        for nombre in ("SQL_CONFIRMAR", "SQL_CREAR_AUTO", "SQL_EDITAR_CONFIRMADA"):
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


# ══════════════════════════════════════════════════════════════════════════════
# Editar una CONFIRMADA (migración 0071), sin base
# ══════════════════════════════════════════════════════════════════════════════

def renglon(n: int, sku: str, cantidad: int, precio: float = 10.0, almacen: str = "ENSAYO",
            salio: int | None = None, **mas) -> dict:
    """Un renglón como lo trae el detalle de una confirmada: apartado completo, o
    —con `salio`— ya entregado (congelado: `entregado_at` puesto, sin apartado)."""
    return {"id": 10 + n, "linea": n, "sku": sku, "titulo": None, "imagen": None,
            "cantidad": cantidad, "precio_unitario": precio, "almacen": almacen,
            "reservado": 0 if salio is not None else cantidad, "entregado": salio,
            "entregado_at": "2026-10-09T10:00:00+00:00" if salio is not None else None,
            "entregado_por": "oper@prueba.test" if salio is not None else None, **mas}


def E(sku: str, cantidad: int, precio: float = 10.0, almacen: str | None = "ENSAYO", **mas) -> dict:
    """Un renglón como lo manda la pantalla."""
    return {"sku": sku, "cantidad": cantidad, "precio_unitario": precio, "almacen": almacen, **mas}


class EditarConfirmadaPuro(unittest.TestCase):
    """Lo que decide Python ANTES de la sentencia, sin tocar nada."""

    SALIO = renglon(1, "ZZPRUEBA-A", 3, salio=3)

    def nuevos(self, crudas, salidos=None) -> list[dict]:
        return ov._pendientes_nuevos(crudas, [self.SALIO] if salidos is None else salidos, BODEGAS)

    def test_el_eco_de_lo_que_ya_salio_se_descarta_y_lo_demas_se_numera(self):
        r = self.nuevos([E("zzprueba-a", 3), E("ZZPRUEBA-C", 1, 5), E("ZZPRUEBA-B", 2, 20)])
        self.assertEqual([(x["linea"], x["sku"], x["cantidad"], x["almacen"]) for x in r],
                         [(2, "ZZPRUEBA-C", 1, "ENSAYO"), (3, "ZZPRUEBA-B", 2, "ENSAYO")],
                         "el 1 es del que ya salió: los pendientes toman los que quedan libres")
        self.assertEqual(r[0]["precio_unitario"], Decimal("5.00"))
        # Sin nada entregado, 1..n en el orden recibido.
        self.assertEqual([x["linea"] for x in self.nuevos([E("B", 1), E("A", 1)], salidos=[])], [1, 2])
        # Los números de los que salieron se respetan estén donde estén.
        salidos = [renglon(2, "ZZPRUEBA-A", 3, salio=3), renglon(4, "ZZPRUEBA-D", 1, salio=0)]
        r = self.nuevos([E("X", 1), E("ZZPRUEBA-D", 1), E("Y", 1), E("ZZPRUEBA-A", 3), E("Z", 1)],
                        salidos=salidos)
        self.assertEqual([(x["linea"], x["sku"]) for x in r], [(1, "X"), (3, "Y"), (5, "Z")])
        # El mismo SKU en OTRA bodega no es el renglón que salió: es uno nuevo.
        otra = {**BODEGAS, "ENSAYO2": {**BODEGAS["ENSAYO"], "codigo": "ENSAYO2"}}
        r = ov._pendientes_nuevos([E("ZZPRUEBA-A", 3), E("ZZPRUEBA-A", 2, almacen="ENSAYO2")],
                                  [self.SALIO], otra)
        self.assertEqual([(x["linea"], x["sku"], x["almacen"]) for x in r],
                         [(2, "ZZPRUEBA-A", "ENSAYO2")])

    def test_lo_que_ya_salio_no_se_cambia(self):
        for crudas in ([E("ZZPRUEBA-A", 2), E("B", 1)],                       # otra cantidad
                       [E("ZZPRUEBA-A", 3), E("zzprueba-a", 1), E("B", 1)]):  # «sumarle» piezas
            with self.assertRaises(ov.Invalido) as e:
                self.nuevos(crudas)
            self.assertEqual((e.exception.status, str(e.exception)),
                             (400, "El renglón de ZZPRUEBA-A ya salió de ENSAYO: no se cambia. "
                                   "Si hace falta más, va en otra orden."))

    def test_no_puede_quedar_sin_renglones_por_entregar(self):
        for crudas in ([], [E("ZZPRUEBA-A", 3)]):
            with self.assertRaises(ov.Invalido) as e:
                self.nuevos(crudas)
            self.assertEqual(str(e.exception),
                             "Una orden confirmada necesita al menos un renglón por entregar. "
                             "Si ya salió todo, márcala DELIVERED.")
        with self.assertRaises(ov.Invalido) as e:
            self.nuevos([], salidos=[])
        self.assertEqual(str(e.exception),
                         "Una orden confirmada necesita al menos un renglón por entregar. "
                         "Si ya no va, cancélala.")
        for malas in ("texto", [1], [{"sku": "A", "cantidad": 0}],
                      [E("A", 1, 10), E("a", 1, 11)]):                         # precios distintos
            with self.assertRaises(ov.Invalido):
                self.nuevos(malas, salidos=[])

    def test_cada_renglon_por_entregar_sale_de_una_bodega_que_admite_ordenes(self):
        with self.assertRaises(ov.Invalido) as e:
            self.nuevos([E("ZZPRUEBA-B", 1, almacen=None)])
        self.assertEqual(str(e.exception), "El renglón de ZZPRUEBA-B no tiene bodega: elige de "
                                           "cuál sale antes de guardar.")
        for bodega, dice in (("TEX3", "no admite órdenes de venta"), ("TEXCO", "bodega de Odoo"),
                             ("NADA", "no existe la bodega")):
            with self.assertRaises(ov.Invalido) as e:
                self.nuevos([E("ZZPRUEBA-B", 1, almacen=bodega)])
            self.assertTrue(str(e.exception).startswith("ZZPRUEBA-B: "), str(e.exception))
            self.assertIn(dice, str(e.exception))
        # El eco de un renglón que YA SALIÓ de una bodega que un acta apagó después
        # no impide corregir los demás: la bodega se revisa sin los ecos.
        salio_de_tex3 = renglon(1, "ZZPRUEBA-A", 3, almacen="TEX3", salio=3)
        r = self.nuevos([E("ZZPRUEBA-A", 3, almacen="tex3"), E("ZZPRUEBA-B", 1)],
                        salidos=[salio_de_tex3])
        self.assertEqual([(x["linea"], x["sku"]) for x in r], [(2, "ZZPRUEBA-B")])

    def test_cuanto_se_mueve_el_apartado_por_sku_y_bodega(self):
        antes = [renglon(1, "ZZPRUEBA-A", 3), renglon(2, "ZZPRUEBA-B", 2), renglon(3, "ZZPRUEBA-C", 4)]
        despues = [E("zzprueba-a", 5), E("ZZPRUEBA-C", 4, 99), E("ZZPRUEBA-D", 1),
                   E("ZZPRUEBA-C", 2, almacen="ENSAYO2")]
        self.assertEqual(ov._deltas_apartado(antes, despues), [
            {"sku": "zzprueba-a", "almacen": "ENSAYO", "delta": 2},
            {"sku": "ZZPRUEBA-D", "almacen": "ENSAYO", "delta": 1},
            {"sku": "ZZPRUEBA-C", "almacen": "ENSAYO2", "delta": 2},
            {"sku": "ZZPRUEBA-B", "almacen": "ENSAYO", "delta": -2}],
            "sólo lo que cambia: C en ENSAYO sigue en 4 (cambió de precio, no de apartado)")
        self.assertEqual(ov._deltas_apartado(antes, [E(l["sku"], l["cantidad"]) for l in antes]), [])

    def test_no_alcanzo_al_editar_dice_cuanto_falta(self):
        t = ov._texto_no_alcanzo_edicion
        self.assertEqual(t([{"sku": "ZZPRUEBA-1", "falta": 2, "libre": 1, "almacen": "ENSAYO"}]),
                         "No alcanzó el stock para guardar el cambio: ZZPRUEBA-1 necesita 2 más y "
                         "hay 1 libre en ENSAYO. No se guardó nada.")
        self.assertIn("ZZPRUEBA-2 necesita 3 más y hay 0 libres en ENSAYO",
                      t([{"sku": "ZZPRUEBA-2", "falta": 3, "libre": -4, "almacen": "ENSAYO"}]))
        self.assertIn("ZZPRUEBA-3 necesita 1 más y no tiene existencias registradas en ENSAYO",
                      t([{"sku": "ZZPRUEBA-3", "falta": 1, "libre": None, "almacen": "ENSAYO"}]))
        muchos = [{"sku": f"S{i}", "falta": 1, "libre": 0, "almacen": "ENSAYO"} for i in range(5)]
        self.assertIn("; y otros 2 SKU. No se guardó nada.", t(muchos))
        self.assertIn("; y 1 SKU más. No se guardó nada.", t(muchos[:4]))


class EditarConfirmada(Limpia):
    """`guardar()` sobre una CONFIRMADA: va por `_editar_confirmada`, con la base
    sustituida. Lo que la base hace con esa sentencia —guardias, diferidos,
    concurrencia— está en tests/test_ordenes_venta_bd.py."""

    def setUp(self) -> None:
        super().setUp()
        self.o = orden("confirmada", cliente="directa", canal="directa", mp_canal=None,
                       mp_cuenta=None, mp_orden=None, descripcion=None, guia=None, paqueteria=None,
                       fecha_venta=None, entrega_limite=None, moneda="MXN",
                       total=Decimal("70.00"), comision=Decimal("0.00"), precio_origen="manual",
                       creado_via="panel",
                       lineas=[renglon(1, "ZZPRUEBA-A", 3, salio=3), renglon(2, "ZZPRUEBA-B", 2, 20.0)])
        self.parche("_tablas", mock.MagicMock(return_value=True))
        self.parche("_leer_id", mock.MagicMock(side_effect=lambda *a, **k: self.o))
        self.parche("habilitado", mock.MagicMock(return_value=True))
        self.parche("edicion_lista", mock.MagicMock(return_value=True))
        self.parche("hay_bucket", mock.MagicMock(return_value=False))
        self.parche("_bodegas_mapa", mock.MagicMock(return_value=BODEGAS))
        self.transicion = self.parche("_transicion", mock.MagicMock(return_value={"cuadra": 5}))
        # Ninguna de estas pruebas deja un mensaje fuera de la sentencia.
        self.aviso = self.parche("_mensaje_sistema", mock.MagicMock())

    def enviado(self) -> tuple[dict, dict]:
        """(parámetros, `datos` de la bitácora) de la ÚNICA sentencia que salió."""
        self.transicion.assert_called_once()
        operacion, sql, params = self.transicion.call_args.args[:3]
        self.assertEqual((operacion, sql), ("editar_confirmada", ov.SQL_EDITAR_CONFIRMADA))
        return params, ov.json.loads(params["datos"])

    def rechazo(self, exc: ErrorPg) -> None:
        self.transicion.side_effect = ov._Rechazo(*ov.clasificar(exc, "editar_confirmada"), exc)

    def test_solo_el_encabezado_abre_la_puerta_de_esa_orden_y_no_toca_renglones(self):
        r = ov.guardar(7, 3, {"guia": " G-1 ", "paqueteria": "Paquetería de prueba",
                              "cliente": "directa"}, OPER)
        self.assertEqual((r["ok"], r["mensaje"]), (True, "Cambios guardados."))
        params, datos = self.enviado()
        self.assertEqual((params["puerta"], params["id"], params["rev"]), ("7", 7, 3),
                         "la puerta es el id de ESA orden, como texto")
        self.assertEqual((params["con_lineas"], params["lineas"]), (False, "[]"))
        self.assertEqual({c for c in ov._CAMPOS if params[f"t_{c}"]}, {"guia", "paqueteria"},
                         "lo que no cambió (o no se mandó) no se toca")
        self.assertEqual((params["guia"], params["paqueteria"], params["cliente"]),
                         ("G-1", "Paquetería de prueba", None))
        self.assertEqual((params["q"], params["nombre"], params["via"]),
                         ("oper@prueba.test", "Olga Operadora", "panel"))
        self.assertEqual(params["cuerpo"], "Orden editada · guía, paquetería")
        self.assertEqual(set(datos), {"op", "cambios"}, "sin renglones no hay rastro de renglones")
        self.assertEqual(datos["cambios"], {"guia": [None, "G-1"],
                                            "paqueteria": [None, "Paquetería de prueba"]})
        # Todos los parámetros de la sentencia viajan (psycopg2 truena si falta uno).
        self.assertEqual(set(re.findall(r"%\((\w+)\)s", ov.SQL_EDITAR_CONFIRMADA)), set(params))
        self.aviso.assert_not_called()

    def test_el_documento_entero_sin_cambios_no_toca_la_base(self):
        r = ov.guardar(7, 3, {"cliente": "directa", "canal": "DIRECTA", "moneda": "mxn",
                              "total": 70, "comision": None, "guia": "  ",
                              "lineas": [E("zzprueba-a", 3), E("ZZPRUEBA-B", 2, 20)]}, OPER)
        self.assertEqual((r["ok"], r["mensaje"], r["orden"]["rev"]), (True, "Sin cambios.", 3))
        self.transicion.assert_not_called()

    def test_los_renglones_por_entregar_como_quedan_y_su_rastro(self):
        ov.guardar(7, 3, {"total": None, "lineas": [E("ZZPRUEBA-A", 3), E("ZZPRUEBA-C", 1, 5),
                                                    E("ZZPRUEBA-B", 4, 20)]}, ADMIN)
        params, datos = self.enviado()
        self.assertTrue(params["con_lineas"])
        self.assertEqual(ov.json.loads(params["lineas"]), [
            {"linea": 2, "sku": "ZZPRUEBA-C", "titulo": None, "imagen": None, "cantidad": 1,
             "precio_unitario": "5.00", "almacen": "ENSAYO"},
            {"linea": 3, "sku": "ZZPRUEBA-B", "titulo": None, "imagen": None, "cantidad": 4,
             "precio_unitario": "20.00", "almacen": "ENSAYO"}],
            "sólo los que NO han salido; el que salió conserva su 1")
        # El total automático es de TODA la orden: 3×10 que ya salieron + 1×5 + 4×20.
        self.assertEqual((params["t_total"], params["total"]), (True, Decimal("115.00")))
        self.assertEqual(datos["cambios"], {"total": [70.0, 115.0]})
        self.assertEqual(datos["renglones"], {"agregados": ["ZZPRUEBA-C"], "quitados": [],
                                              "cambiados": [{"sku": "ZZPRUEBA-B",
                                                             "cantidad": [2, 4]}]})
        self.assertEqual(datos["lineas"], [
            {"sku": "ZZPRUEBA-C", "titulo": None, "cantidad": 1, "precio_unitario": 5.0,
             "almacen": "ENSAYO", "reservado": 1},
            {"sku": "ZZPRUEBA-B", "titulo": None, "cantidad": 4, "precio_unitario": 20.0,
             "almacen": "ENSAYO", "reservado": 4}], "como quedan, y apartados completos")
        self.assertEqual(datos["apartado"], [{"sku": "ZZPRUEBA-C", "almacen": "ENSAYO", "delta": 1},
                                             {"sku": "ZZPRUEBA-B", "almacen": "ENSAYO", "delta": 2}])
        self.assertEqual(params["cuerpo"], "Orden editada · total; renglones: 1 agregado(s), "
                                           "1 con cambios · se movió el apartado")
        self.assertEqual(params["via"], "panel")

    def test_cambiar_solo_el_precio_no_mueve_el_apartado(self):
        ov.guardar(7, 3, {"lineas": [E("ZZPRUEBA-B", 2, 25)]}, OPER)
        params, datos = self.enviado()
        self.assertEqual(datos["apartado"], [])
        self.assertEqual(datos["renglones"]["cambiados"],
                         [{"sku": "ZZPRUEBA-B", "precio_unitario": [20.0, 25.0]}])
        self.assertEqual(params["cuerpo"], "Orden editada · renglones: 1 con cambios")
        self.assertFalse(params["t_total"], "el total no se mandó: no se toca")

    def test_cambiar_solo_el_titulo_o_la_imagen_deja_el_antes_y_el_despues(self):
        """El rastro `editada` de una confirmada es «el antes y el después». Con
        sólo el título o la imagen distintos la sentencia SÍ escribe (el renglón
        cambia en la base), y el mensaje decía «renglones: reordenados» con
        `cambiados` vacío: lo que cambió no quedaba en ningún lado."""
        foto = "https://img.prueba.test/b.jpg"
        self.o["lineas"][1].update(titulo="Cable", imagen=foto)
        casos = (
            (E("ZZPRUEBA-B", 2, 20, titulo="Cable USB-C", imagen=foto),
             {"titulo": ["Cable", "Cable USB-C"]}),
            (E("ZZPRUEBA-B", 2, 20, titulo="Cable", imagen="https://img.prueba.test/c.jpg"),
             {"imagen": [foto, "https://img.prueba.test/c.jpg"]}),
            # La llave de máquina (o Claude) manda el renglón sin título ni imagen:
            # los borra, y los valores que se van quedan en la bitácora.
            (E("ZZPRUEBA-B", 2, 20), {"titulo": ["Cable", None], "imagen": [foto, None]}))
        for entra, cambio in casos:
            self.transicion.reset_mock()
            ov.guardar(7, 3, {"lineas": [entra]}, OPER)
            params, datos = self.enviado()
            self.assertEqual(datos["renglones"], {"agregados": [], "quitados": [],
                                                  "cambiados": [{"sku": "ZZPRUEBA-B", **cambio}]})
            self.assertEqual(params["cuerpo"], "Orden editada · renglones: 1 con cambios",
                             "ya no dice «reordenados»")
            self.assertEqual(datos["apartado"], [], "no pide ni suelta stock")
        # Mandarlo IGUAL sigue sin ser un cambio: no toca la base.
        self.transicion.reset_mock()
        igual = E("ZZPRUEBA-B", 2, 20, titulo="Cable", imagen=foto)
        r = ov.guardar(7, 3, {"lineas": [igual]}, OPER)
        self.assertEqual(r["mensaje"], "Sin cambios.")
        self.transicion.assert_not_called()

    def test_lo_que_no_pasa_la_validacion_no_llega_a_la_base(self):
        casos = (
            ({"lineas": [E("ZZPRUEBA-A", 1), E("ZZPRUEBA-B", 2, 20)]}, "ya salió de ENSAYO"),
            ({"lineas": [E("ZZPRUEBA-A", 3)]}, "Si ya salió todo, márcala DELIVERED."),
            ({"lineas": []}, "al menos un renglón por entregar"),
            ({"lineas": [E("ZZPRUEBA-B", 2, 20, almacen=None)]}, "no tiene bodega"),
            ({"lineas": [E("ZZPRUEBA-B", 2, 20, almacen="TEX3")]}, "no admite órdenes de venta"),
            ({"lineas": [E("ZZPRUEBA-B", 2.5, 20)]}, "la cantidad debe ser un entero"),
            ({"canal": "facebook"}, "El canal debe ser uno de"),
            ({"total": -1}, "debe estar entre 0"),
            ({"mp_orden": "V-1"}, "los tres o ninguno"),
            ({"total": None, "lineas": [E("ZZPRUEBA-B", 100000, 9999999.99),
                                        E("ZZPRUEBA-C", 100000, 9999999.99)]},
             "El total de los renglones pasa de"))
        for datos, dice in casos:
            with self.assertRaises(ov.Invalido, msg=str(datos)[:60]) as e:
                ov.guardar(7, 3, datos, OPER)
            self.assertEqual(e.exception.status, 400)
            self.assertIn(dice, str(e.exception))
        for malo in ("texto", None, []):
            with self.assertRaises(ov.Invalido):
                ov.guardar(7, 3, malo, OPER)
        self.transicion.assert_not_called()

    def test_la_orden_que_se_creo_sola_no_cambia_de_cliente_ni_de_canal_de_venta(self):
        self.o.update(creado_via="automatico", cliente="tiktok", canal="tiktok", mp_canal="tiktok",
                      mp_cuenta="CUENTAPRUEBA", mp_orden="V-1")
        for datos in ({"cliente": "Otro cliente"}, {"mp_canal": "temu"}, {"cliente": None}):
            with self.assertRaises(ov.Invalido) as e:
                ov.guardar(7, 3, datos, ADMIN)
            self.assertIn("ninguno de los dos se cambia", str(e.exception))
        self.transicion.assert_not_called()
        # Mandarlos IGUALES (la pantalla manda todo) no es cambiarlos, y lo demás sí se corrige.
        ov.guardar(7, 3, {"cliente": " tiktok ", "mp_canal": "TIKTOK", "guia": "G-9"}, OPER)
        params, datos = self.enviado()
        self.assertEqual(datos["cambios"], {"guia": [None, "G-9"]})
        self.assertFalse(params["t_cliente"] or params["t_mp_canal"])

    def test_si_no_alcanza_dice_cuanto_falta_y_no_deja_mensaje_en_el_chat(self):
        self.rechazo(kb001("no_alcanzo"))
        saldo = self.parche("_filas", mock.MagicMock(return_value=[
            {"sku": "ZZPRUEBA-B", "almacen": "ENSAYO", "delta": 3, "con_saldo": True, "libre": 1},
            {"sku": "ZZPRUEBA-C", "almacen": "ENSAYO", "delta": 2, "con_saldo": False,
             "libre": None}]))
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(7, 3, {"lineas": [E("ZZPRUEBA-B", 5, 20), E("ZZPRUEBA-C", 2)]}, OPER)
        self.assertEqual((e.exception.status, str(e.exception)),
                         (409, "No alcanzó el stock para guardar el cambio: ZZPRUEBA-B necesita 3 "
                               "más y hay 1 libre en ENSAYO; ZZPRUEBA-C necesita 2 más y no tiene "
                               "existencias registradas en ENSAYO. No se guardó nada."))
        self.assertNotIn("no_alcanzo", str(e.exception))
        # Se releyó el saldo SÓLO de lo que tenía que subir…
        sql, params = saldo.call_args.args[:2]
        self.assertIs(sql, ov._SQL_DIAGNOSTICO_EDICION)
        self.assertEqual(ov.json.loads(params["deltas"]), [
            {"n": 0, "sku": "ZZPRUEBA-B", "almacen": "ENSAYO", "delta": 3},
            {"n": 1, "sku": "ZZPRUEBA-C", "almacen": "ENSAYO", "delta": 2}])
        # …y, a diferencia de confirmar, NO queda aviso en el chat: nada cambió.
        self.aviso.assert_not_called()
        self.transicion.assert_called_once()
        # Entre el rechazo y la relectura llegó stock: se dice, sin inventar un faltante.
        saldo.return_value = [{"sku": "ZZPRUEBA-B", "almacen": "ENSAYO", "delta": 3,
                               "con_saldo": True, "libre": 9}]
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(7, 3, {"lineas": [E("ZZPRUEBA-B", 5, 20)]}, OPER)
        self.assertIn("pero ya hay: vuelve a guardar", str(e.exception))
        # Sin poder releer el saldo sale el texto general (y un aviso en el log), no un 502.
        saldo.side_effect = psycopg2.OperationalError("timeout")
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            with self.assertRaises(ov.Conflicto) as e:
                ov.guardar(7, 3, {"lineas": [E("ZZPRUEBA-B", 5, 20)]}, OPER)
        self.assertEqual(str(e.exception),
                         "No alcanzó el stock para guardar el cambio. No se guardó nada.")

    def test_la_bodega_apagada_dentro_del_candado_es_un_400_que_dice_cual(self):
        """C11: Python la vio encendida, y un acta la apagó antes del candado."""
        self.rechazo(kb001("bodega_no_admite_ov"))
        mapas = [BODEGAS, {**BODEGAS, "ENSAYO": {**BODEGAS["ENSAYO"], "admite_ov": False}}]
        ov._bodegas_mapa.side_effect = lambda cur=None: mapas.pop(0)
        with self.assertRaises(ov.Invalido) as e:
            ov.guardar(7, 3, {"lineas": [E("ZZPRUEBA-B", 5, 20)]}, OPER)
        self.assertEqual((e.exception.status, str(e.exception)),
                         (400, "ZZPRUEBA-B: la bodega ENSAYO no admite órdenes de venta (está "
                               "apagada)."))
        # Si al releer ya volvió a admitir, sale el texto general; nunca el nombre técnico.
        ov._bodegas_mapa.side_effect = None
        with self.assertRaises(ov.Invalido) as e:
            ov.guardar(7, 3, {"lineas": [E("ZZPRUEBA-B", 5, 20)]}, OPER)
        self.assertEqual(str(e.exception), ov.TEXTO_MOTIVO["bodega_no_admite_ov"])
        self.aviso.assert_not_called()

    def test_si_la_base_no_trae_la_0071_es_un_409_que_lo_dice_y_se_olvida_lo_recordado(self):
        """`edicion_lista` dijo que sí (o lo recordaba), pero las guardias rechazan
        la edición: 42501 de cualquiera de las dos, o el CHECK del catálogo sin
        `editada`. No es un bug de la sentencia: falta la migración."""
        for exc in (ErrorPg("42501", "ov_ordenes_inmutable"), ErrorPg("42501", "ov_lineas_inmutable"),
                    ErrorPg("23514", "ov_mensajes_evento_chk")):
            self.rechazo(exc)
            ov._recordar("edicion", True, 60)
            ov._recordar("tablas", True, 60)
            with self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
                with self.assertRaises(ov.Conflicto) as e:
                    ov.guardar(7, 3, {"guia": "G-1", "lineas": [E("ZZPRUEBA-B", 5, 20)]}, OPER)
            self.assertEqual((e.exception.status, str(e.exception)),
                             (409, "Editar una orden confirmada todavía no está habilitado en "
                                   "esta base: falta la migración 0071."))
            self.assertEqual([r.levelname for r in logs.records], ["WARNING"],
                             "un aviso con su regla, no el ERROR de «bug nuestro»")
            self.assertIn("0071", logs.output[0])
            self.assertIn(exc.diag.constraint_name, logs.output[0])
            self.assertEqual(ov._recordado("edicion"), (False, None), "se vuelve a preguntar")
            self.assertEqual(ov._recordado("tablas"), (True, True), "lo demás no se olvida")
        # Otro 42501 (o el mismo nombre con otro código) NO es eso: sigue siendo un bug.
        self.rechazo(ErrorPg("42501", "ov_mensajes_solo_agregar"))
        with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
            with self.assertRaises(ov.ErrorOV) as e:
                ov.guardar(7, 3, {"guia": "G-1"}, OPER)
        self.assertEqual((type(e.exception), e.exception.status), (ov.ErrorOV, 502))
        self.assertIn("ordenes_venta.editar_confirmada", logs.output[0])

    def test_ligar_una_venta_que_ya_tiene_orden_es_un_409_con_su_folio(self):
        mp = {"mp_canal": "tiktok", "mp_cuenta": "cuentaprueba", "mp_orden": "V-1"}
        self.parche("_venta_en_canal", mock.MagicMock(return_value=[]))
        ligada = self.parche("_venta_ligada", mock.MagicMock(
            return_value={"id": 4, "folio": "OV-00004", "estado": "confirmada"}))
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(7, 3, mp, OPER)
        self.assertEqual(str(e.exception), "Esa venta ya tiene la orden OV-00004.")
        self.assertEqual(ligada.call_args.args, ("tiktok", "CUENTAPRUEBA", "V-1"))
        self.assertEqual(ligada.call_args.kwargs["excepto"], 7)
        self.transicion.assert_not_called()
        # Si la revisión previa no la ve (dos capturas a la vez), la base sí: 23505.
        ligada.side_effect = [None, {"id": 4, "folio": "OV-00004", "estado": "confirmada"}]
        self.rechazo(ErrorPg("23505", "ov_ordenes_mp_uq"))
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(7, 3, mp, OPER)
        self.assertEqual(str(e.exception), "Esa venta ya tiene la orden OV-00004.")
        params, _ = self.enviado()
        self.assertEqual((params["mp_canal"], params["mp_cuenta"], params["mp_orden"]),
                         ("tiktok", "CUENTAPRUEBA", "V-1"))
        # Y una venta FULL no lleva orden propia, tampoco al ligar una confirmada.
        ov._venta_en_canal.return_value = [{"cuenta": "CUENTAPRUEBA", "es_full": True}]
        with self.assertRaises(ov.Invalido) as e:
            ov.guardar(7, 3, mp, OPER)
        self.assertIn("FULL", str(e.exception))

    def test_la_rev_vieja_en_la_sentencia_es_un_409_salvo_que_sea_mi_propio_reintento(self):
        self.rechazo(kb001("ov_no_esta_confirmada_o_cambio_rev"))
        mio = self.parche("_lo_hice_yo", mock.MagicMock(return_value=False))
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(7, 3, {"guia": "G-1"}, OPER)
        self.assertEqual(str(e.exception), "La orden cambió mientras tanto; se recargó.")
        _, datos = self.enviado()
        self.assertEqual(mio.call_args.args[:2], (7, datos["op"]))
        # El COMMIT entró y el pool repitió el cuerpo: mi marca ya está → fue un éxito.
        mio.return_value = True
        r = ov.guardar(7, 3, {"guia": "G-1"}, OPER)
        self.assertEqual((r["ok"], r["mensaje"]), (True, "Cambios guardados."))

    def test_lo_que_la_base_rechaza_al_commit_sigue_siendo_un_502_con_su_regla_en_el_log(self):
        for exc in (ErrorPg("23514", "ov_coherente"), ErrorPg("23505", "ov_lineas_sku_alm_uq"),
                    ErrorPg("23514", "stock_apartado_cuadra"), kb001("renglones_no_cuadran"),
                    kb001("editar_escrituras_no_cuadran")):
            self.rechazo(exc)
            with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
                with self.assertRaises(ov.ErrorOV) as e:
                    ov.guardar(7, 3, {"lineas": [E("ZZPRUEBA-B", 5, 20)]}, OPER)
            self.assertEqual((type(e.exception), e.exception.status), (ov.ErrorOV, 502))
            self.assertIn("ordenes_venta.editar_confirmada", logs.output[0])

    def test_quien_no_puede_o_cuando_no_se_puede_ni_se_valida(self):
        with self.assertRaises(ov.SinPermiso):
            ov.guardar(7, 3, {"guia": "G-1"}, LECT)
        with self.assertRaises(ov.Conflicto):
            ov.guardar(7, 2, {"guia": "G-1"}, OPER)                 # rev vieja
        ov.habilitado.return_value = False
        with self.assertRaises(ov.Apagado):
            ov.guardar(7, 3, {"guia": "G-1"}, ADMIN)
        ov.habilitado.return_value = True
        ov.edicion_lista.return_value = False
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(7, 3, {"guia": "G-1"}, ADMIN)
        self.assertIn("falta la migración 0071", str(e.exception))
        ov.edicion_lista.return_value = True
        self.o["canal_cancelo_at"] = "2026-10-09T12:00:00+00:00"
        with self.assertRaises(ov.Invalido) as e:
            ov.guardar(7, 3, {"guia": "G-1"}, ADMIN)
        self.assertIn("contestar si salió", str(e.exception))
        self.transicion.assert_not_called()

    def test_un_borrador_sigue_yendo_por_la_sentencia_de_guardar(self):
        self.o.update(estado="borrador", confirmada_at=None,
                      lineas=[{**renglon(1, "ZZPRUEBA-B", 2, 20.0), "reservado": 0}])
        ov.guardar(7, 3, {"guia": "G-1", "lineas": [E("ZZPRUEBA-B", 2, 20, almacen=None)]}, OPER)
        operacion, sql, params = self.transicion.call_args.args[:3]
        self.assertEqual((operacion, sql), ("guardar", ov.SQL_GUARDAR))
        self.assertNotIn("puerta", params, "un borrador no abre ninguna puerta")
        self.assertEqual(ov.json.loads(params["datos"])["lineas"][0]["reservado"], 0)
        ov.edicion_lista.assert_not_called()


class ElCanalCancelaAMediaEdicion(Limpia):
    """Quien avisa que el canal canceló no manda `rev`, pero la sentencia sobre una
    CONFIRMADA sí lleva una: la de la lectura que `canal_cancelo` hizo en ese
    intento. Si una EDICIÓN (o una entrega parcial) confirma en medio, la guardia
    ya no encuentra la fila (KB001), se relee y se decide de nuevo con lo que hay;
    y si lo que cambió fue la LIGA con la venta, la orden ya no es de la venta que
    el canal canceló y no se toca (`venta=`).

    Debajo queda la red de antes, de cuando la guardia iba sólo por estado: la
    sentencia corría con los renglones de la foto vieja y la base la rechazaba.
    Eso es «la orden se movió» sólo si la `rev` de verdad cambió; con la misma rev
    es una sentencia mal hecha y tiene que seguir saliendo como el bug que sería.
    (Las carreras de verdad, variante por variante, están en test_ordenes_venta_bd.py.)"""

    RECHAZOS = (ErrorPg("23514", "stock_apartado_cuadra"),          # soltó de más o de menos
                kb001("cancelar_no_cuadra"),                        # el renglón que la edición quitó
                ErrorPg("23514", "stock_almacen_apartado_chk"),     # (los dos de la entrega parcial,
                ErrorPg("42501", "ov_lineas_inmutable"))            #  que ya estaban)
    S1 = ("tiktok", "CUENTAPRUEBA", "V-1")                          # la venta que el canal canceló

    def setUp(self) -> None:
        super().setUp()
        self.parche("_tablas", mock.MagicMock(return_value=True))
        self.parche("habilitado", mock.MagicMock(return_value=True))
        self.parche("hay_bucket", mock.MagicMock(return_value=False))
        self.parche("edicion_lista", mock.MagicMock(return_value=True))
        self.leer = self.parche("_leer_id", mock.MagicMock())
        self.transicion = self.parche("_transicion", mock.MagicMock())

    def rechazo(self, exc: ErrorPg) -> ov._Rechazo:
        return ov._Rechazo(*ov.clasificar(exc, "canal_cancelo"), exc)

    def ligada(self, estado: str = "confirmada", venta: tuple = S1, **mas) -> dict:
        """Una orden como la trae `_leer_id`, ligada a `venta`."""
        return orden(estado, **dict(zip(ov._MP, venta)), **mas)

    def enviados(self) -> list[tuple]:
        """(operación, sentencia, parámetros) de cada `execute` que salió."""
        return [c.args[:3] for c in self.transicion.call_args_list]

    def test_cada_intento_manda_la_rev_de_su_propia_lectura(self):
        """Las tres sentencias sobre una CONFIRMADA viajan con la `rev` que
        `canal_cancelo` acaba de leer —no con una de quien llama, que no trae—, y
        tras un KB001 (alguien la movió en medio) el reintento lleva la de la
        RELECTURA. «Sólo por estado» ya no alcanza: desde la 0071 el contenido de
        una confirmada cambia entre la lectura y el CAS."""
        cambio = "ov_no_cancelable_o_cambio_rev"
        casos = (("sin salir", {"piezas_apartadas": 5}, False, ov.SQL_CANCELAR_CANAL, cambio,
                  "cancelada"),
                 ("con piezas afuera", {"piezas_entregadas": 2, "piezas_apartadas": 1}, False,
                  ov.SQL_CANCELAR_CON_SALIDA_CANAL, cambio, "entregada_cancelada"),
                 ("en camino", {}, True, ov.SQL_CANAL_MARCA, "canal_cancelo_no_aplica", "marcada"))
        for nombre, mas, en_camino, sql, motivo, resultado in casos:
            with self.subTest(nombre):
                # 1.er intento: lee la 3 y la sentencia ya no encuentra la fila →
                # relee: es la 4 → entra → la respuesta relee cómo quedó.
                self.leer.side_effect = [self.ligada(rev=3, **mas), self.ligada(rev=4, **mas),
                                         self.ligada(rev=5, **mas)]
                self.transicion.reset_mock()
                self.transicion.side_effect = [self.rechazo(kb001(motivo)), {"cuadra": 2}]
                with self.assertNoLogs("omnicanal.ordenes_venta", level="ERROR"):
                    r = ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", en_camino,
                                         venta=self.S1)
                self.assertEqual(r["resultado"], resultado)
                self.assertEqual([(op, s) for op, s, _ in self.enviados()],
                                 [("canal_cancelo", sql)] * 2)
                self.assertEqual([p["rev"] for _, _, p in self.enviados()], [3, 4],
                                 "cada intento, la rev de SU lectura")
                for _, _, p in self.enviados():
                    self.assertEqual(set(re.findall(r"%\((\w+)\)s", sql)) - set(p), set(),
                                     "psycopg2 truena si falta un parámetro")
        # Sobre una ENTREGADA basta el estado (ya no cambia): esa sentencia no lleva rev.
        self.leer.side_effect = [self.ligada("entregada", rev=6),
                                 self.ligada("entregada_cancelada", rev=7)]
        self.transicion.reset_mock()
        self.transicion.side_effect = [{"cuadra": 2}]
        r = ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", False, venta=self.S1)
        (_, sql, params), = self.enviados()
        self.assertEqual((r["resultado"], sql), ("entregada_cancelada",
                                                 ov.SQL_CANAL_CANCELO_ENTREGADA))
        self.assertEqual(set(re.findall(r"%\((\w+)\)s", sql)), set(params))
        self.assertNotIn("rev", params)

    def test_el_mensaje_de_la_cancelacion_sale_de_la_misma_lectura_que_su_rev(self):
        """El cuerpo y los renglones del mensaje `cancelada` se arman en Python,
        con lo que se leyó; la sentencia relee los renglones por su cuenta. Con
        la guardia sólo por estado, una edición que confirmaba en medio dejaba la
        bitácora diciendo «se soltaron 5 piezas» cuando se habían soltado 2. Con
        la `rev` de la lectura en la guardia, la sentencia armada con la foto
        vieja NO entra; entra la del reintento, armada con la foto nueva."""
        antes = self.ligada(rev=3, piezas_apartadas=5, lineas=[renglon(1, "ZZPRUEBA-A", 5)])
        editada = self.ligada(rev=4, piezas_apartadas=2, lineas=[renglon(1, "ZZPRUEBA-A", 2)])
        self.leer.side_effect = [antes, editada, self.ligada("cancelada", rev=5)]
        self.transicion.side_effect = [self.rechazo(kb001("ov_no_cancelable_o_cambio_rev")),
                                       {"cuadra": 2}]
        r = ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", False, venta=self.S1)
        self.assertEqual(r["resultado"], "cancelada")
        (_, _, vieja), (_, _, nueva) = self.enviados()
        self.assertEqual((vieja["rev"], nueva["rev"]), (3, 4))
        self.assertIn("se soltaron 5 piezas", vieja["cuerpo"])      # ésta fue la rechazada
        self.assertIn("se soltaron 2 piezas", nueva["cuerpo"])      # y ésta la que entró
        de_la_vieja, de_la_nueva = (ov.json.loads(p["datos"]) for p in (vieja, nueva))
        self.assertEqual([(l["sku"], l["cantidad"], l["reservado"]) for l in de_la_nueva["lineas"]],
                         [("ZZPRUEBA-A", 2, 2)])
        self.assertNotEqual(de_la_vieja["op"], de_la_nueva["op"], "cada intento, su propia marca")

    def test_si_la_orden_ya_no_es_de_esa_venta_no_se_toca(self):
        """La liga de una confirmada se puede corregir (0071). Si entre la lectura
        del barrido y su aviso la orden dejó de ser de la venta que el canal
        canceló, cancelarla —o dejarla esperando el «¿salió?»— sería por una
        venta AJENA, y cancelada no se deshace: `nada`, sin una sola sentencia."""
        otras = (("ligada a otra venta", ("tiktok", "CUENTAPRUEBA", "V-2")),
                 ("la misma venta, en otra cuenta", ("tiktok", "OTRACUENTAPRUEBA", "V-1")),
                 ("desligada", (None, None, None)))
        for nombre, ahora in otras:
            for estado, en_camino in (("confirmada", False), ("confirmada", True),
                                      ("entregada", False)):
                with self.subTest(nombre, estado=estado, en_camino=en_camino):
                    self.leer.side_effect = [self.ligada(estado, ahora, rev=4)]
                    r = ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", en_camino,
                                         venta=self.S1)
                    self.assertEqual((r["resultado"], r["orden"]["estado"], r["orden"]["rev"]),
                                     ("nada", estado, 4))
                    self.transicion.assert_not_called()
        # La corrección también puede entrar DESPUÉS de la lectura de `canal_cancelo`
        # y antes de su sentencia: ahí la frena la `rev` de la guardia (KB001), se
        # relee… y la orden ya no es de esa venta.
        for en_camino, motivo in ((False, "ov_no_cancelable_o_cambio_rev"),
                                  (True, "canal_cancelo_no_aplica")):
            with self.subTest("entre la relectura y la sentencia", en_camino=en_camino):
                self.leer.side_effect = [self.ligada(rev=3), self.ligada(venta=otras[0][1], rev=4)]
                self.transicion.reset_mock()
                self.transicion.side_effect = [self.rechazo(kb001(motivo))]
                r = ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", en_camino,
                                     venta=self.S1)
                self.assertEqual((r["resultado"], r["orden"]["rev"], self.transicion.call_count),
                                 ("nada", 4, 1))
                self.assertEqual(self.enviados()[0][2]["rev"], 3)

    def test_con_la_misma_liga_o_sin_decirla_sigue_como_siempre(self):
        otra = ("tiktok", "CUENTAPRUEBA", "V-2")

        def cancela(leida: dict, **kw) -> str:
            self.leer.side_effect = [leida, self.ligada("cancelada", rev=leida["rev"] + 1)]
            self.transicion.reset_mock()
            self.transicion.side_effect = [{"cuadra": 2}]
            r = ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", False, **kw)
            return r["resultado"]

        # Con la MISMA liga procede (llegue como tupla o como lista)…
        for venta in (self.S1, list(self.S1)):
            self.assertEqual(cancela(self.ligada(rev=3), venta=venta), "cancelada")
        # …y sin `venta` no se compara nada: quien llama responde de que la orden
        # sigue siendo de esa venta (el contrato de antes, para quien no la manda).
        self.assertEqual(cancela(self.ligada(venta=otra, rev=3)), "cancelada")
        self.assertEqual(self.transicion.call_count, 1)
        # Lo que YA estaba resuelto se contesta como siempre, sea cual sea la liga:
        # la misma cancelación vista otra vez sigue siendo éxito, no un cambio.
        marca = "2026-10-09T12:00:00+00:00"
        for leida, resultado in ((self.ligada("cancelada", otra), "ya_cancelada"),
                                 (self.ligada("entregada_cancelada", otra), "ya_cancelada"),
                                 (self.ligada(venta=otra, canal_cancelo_at=marca), "ya_marcada"),
                                 (self.ligada("borrador", otra), "nada"),
                                 (self.ligada(venta=otra, borrada=True), "nada")):
            self.leer.side_effect = [leida]
            self.transicion.reset_mock()
            r = ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", False, venta=self.S1)
            self.assertEqual(r["resultado"], resultado)
            self.transicion.assert_not_called()

    def test_si_la_rev_cambio_se_relee_y_se_cancela_lo_que_hay(self):
        for exc in self.RECHAZOS:
            with self.subTest(regla=exc.diag.constraint_name or exc.diag.message_primary):
                antes, editada = orden("confirmada", rev=3), orden("confirmada", rev=4)
                # 1.er intento (lee la 3) → rechazo → relee (ya es la 4): se movió →
                # 2.º intento (lee la 4) → entra → la respuesta relee la cancelada.
                self.leer.side_effect = [antes, editada, editada, orden("cancelada", rev=5)]
                self.transicion.reset_mock()
                self.transicion.side_effect = [self.rechazo(exc), {"cuadra": 2}]
                with self.assertNoLogs("omnicanal.ordenes_venta", level="ERROR"):
                    r = ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", False)
                self.assertEqual((r["resultado"], r["orden"]["estado"], r["orden"]["rev"]),
                                 ("cancelada", "cancelada", 5))
                self.assertEqual(self.transicion.call_count, 2)
                self.assertEqual([c.args[1] for c in self.transicion.call_args_list],
                                 [ov.SQL_CANCELAR_CANAL] * 2)

    def test_con_la_misma_rev_sigue_siendo_un_bug_y_no_se_reintenta(self):
        for exc in self.RECHAZOS:
            with self.subTest(regla=exc.diag.constraint_name or exc.diag.message_primary):
                misma = orden("confirmada", rev=3)
                self.leer.side_effect = [misma, misma]
                self.transicion.reset_mock()
                self.transicion.side_effect = [self.rechazo(exc)]
                if exc.pgcode == "42501":
                    # Éste tiene su traducción de usuario (un guardado contra un
                    # borrador que se confirmó): sale como 409, pero SIN reintento.
                    with self.assertRaises(ov.Conflicto):
                        ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", False)
                else:
                    with self.assertLogs("omnicanal.ordenes_venta", level="ERROR") as logs:
                        with self.assertRaises(ov.ErrorOV) as e:
                            ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", False)
                    self.assertEqual((type(e.exception), e.exception.status), (ov.ErrorOV, 502))
                    self.assertIn("ordenes_venta.canal_cancelo", logs.output[0])
                self.assertEqual(self.transicion.call_count, 1)

    def test_si_no_deja_de_moverse_se_rinde_sin_pisar_nada(self):
        """Tope de intentos: una orden que se edita una y otra vez no deja al barrido
        en un ciclo. Sale un 409 y va en la siguiente pasada."""
        self.leer.side_effect = [orden("confirmada", rev=n) for n in (3, 4, 4, 5, 5, 6)]
        self.transicion.side_effect = [self.rechazo(kb001("cancelar_no_cuadra"))] * 3
        with self.assertRaises(ov.Conflicto) as e:
            ov.canal_cancelo(7, "CANCELLED", "El comprador canceló", False)
        self.assertIn("siguiente pasada", str(e.exception))
        self.assertEqual((self.transicion.call_count, ov._INTENTOS_CANAL), (3, 3))


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

    def test_la_pregunta_por_la_0071_es_por_la_puerta_y_no_por_el_registro(self):
        """`ops.migraciones` es de sólo agregar: dice que la 0071 se CORRIÓ, no que
        su puerta SIGA ahí. Volver a correr la 0068 (recrea las dos guardias sin
        la puerta, y sin error) o la reversa de la 0071 dejan el renglón: el
        permiso decía que sí y cada guardado rebotaba con 409, para siempre. Se
        pregunta por lo que de verdad abre, en UNA consulta al catálogo. (Contra
        la base, pieza por pieza: test_ordenes_venta_bd.py.)"""
        sql = ov._SQL_HAY_EDICION
        for trozo in ("to_regprocedure('ops.tg_ov_ordenes_guarda()')",
                      "to_regprocedure('ops.tg_ov_lineas_guarda()')",
                      "position('app.ov_edicion' in p.prosrc) > 0) = 2",      # las DOS guardias
                      "c.conrelid = to_regclass('ventas.ov_mensajes')",
                      "c.conname = 'ov_mensajes_evento_chk'",
                      "position('''editada''' in pg_get_constraintdef(c.oid)) > 0"):
            self.assertIn(trozo, sql)
        self.assertNotIn("migraciones", sql)
        self.assertFalse(hasattr(ov, "MIGRACION_EDICION"), "ya no hay renglón que buscar")
        # Una sola sentencia, que sólo lee, y sin un `%` que psycopg2 pueda tomar
        # por parámetro (va sin ellos).
        self.assertRegex(sql.strip(), r"(?s)^select .* as lista$")
        self.assertNotIn(";", sql)
        self.assertNotIn("%", sql)
        self.assertIsNone(re.search(r"\b(insert|update|delete|alter|create|drop|set)\b", sql))
        gc = base_con_guion([{"lista": True}], [{"lista": False}], [{"lista": None}], [])
        with mock.patch.object(ov.sdb, "get_cursor", gc):
            self.assertEqual([ov._leer_edicion() for _ in range(4)], [True, False, False, False])
        self.assertEqual([c.sentencias for c in gc.cursores], [[(sql, None)]] * 4)

    def test_la_0071_se_pregunta_una_vez_y_la_duda_cierra(self):
        """`edicion_lista()`: ¿la base trae la puerta para editar una confirmada?
        Con caché, y el «no» también se recuerda."""
        leer = self.parche("_leer_edicion", mock.MagicMock(return_value=True))
        self.assertTrue(ov.edicion_lista() and ov.edicion_lista())
        self.assertEqual(leer.call_count, 1)
        leer.return_value = False
        self.assertTrue(ov.edicion_lista(), "todavía en caché")
        self.assertFalse(ov.edicion_lista(refrescar=True))
        # Olvidar SÓLO esa llave (lo que hace la edición cuando la base la rechaza)
        # no olvida lo demás; olvidarlo todo, también la olvida.
        leer.return_value = True
        ov._recordar("bucket", True, 60)
        self.assertFalse(ov.edicion_lista(), "el «no» también se recuerda")
        ov._olvidar("edicion")
        self.assertTrue(ov.edicion_lista())
        self.assertEqual(ov._recordado("bucket"), (True, True))
        leer.return_value = False
        ov._olvidar_cache()
        self.assertFalse(ov.edicion_lista())

    def test_si_no_se_puede_preguntar_por_la_0071_no_se_ofrece_editar(self):
        """Tres respuestas, como `_tablas`: True, False, o None = no se pudo
        PREGUNTAR. None no es «falta»: para decidir vale lo mismo (la duda cierra,
        no se ofrece editar), pero a la persona se le dice otra cosa."""
        leer = self.parche("_leer_edicion", mock.MagicMock(side_effect=RuntimeError("sin base")))
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs:
            self.assertIsNone(ov.edicion_lista())
            self.assertIsNone(ov.edicion_lista(), "nunca lanza")
        self.assertEqual((leer.call_count, len(logs.output)), (1, 1), "el fallo se recuerda…")
        self.assertIn("0071", logs.output[0])
        self.assertEqual(ov._recordado("edicion"), (True, None),
                         "…como «no se sabe», no como «falta»…")
        hasta = ov._cache["edicion"][0] - ov.time.monotonic()
        self.assertLessEqual(hasta, ov._TTL_FALLO, "…y poco: al volver kubera no se espera 60 s")
        # Con kubera caída tampoco, y sin llenar el log (su línea ya la dejó `anotar_caida`).
        leer.side_effect = ov.SinBase()
        with self.assertNoLogs("omnicanal.ordenes_venta", level="WARNING"):
            self.assertIsNone(ov.edicion_lista(refrescar=True))
        # Cualquier otro tropiezo SÍ se dice, también un 42P01: esta lectura es del
        # catálogo de Postgres, no de una tabla nuestra que pueda faltar.
        for exc in (ov.FaltaMigracion(), psycopg2.OperationalError("statement timeout")):
            leer.side_effect = exc
            with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
                self.assertIsNone(ov.edicion_lista(refrescar=True))
        # En pausa (kubera no contesta) ni se intenta.
        ov._olvidar_cache()
        leer.reset_mock(side_effect=True)
        leer.return_value = True
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            ov.anotar_caida(psycopg2.OperationalError("timeout"))
        self.assertIsNone(ov.edicion_lista())
        leer.assert_not_called()
        self.assertEqual(ov._recordado("edicion"), (False, None), "la pausa no deja nada recordado")
        # Con un cursor prestado se lee ESE, y lo leído no se recuerda.
        ov._reiniciar_caida()
        cur = object()
        self.assertTrue(ov.edicion_lista(cur=cur))
        leer.assert_called_once_with(cur)
        self.assertEqual(ov._recordado("edicion"), (False, None))

    def test_un_tropiezo_al_preguntar_no_se_dice_como_que_falta_la_migracion(self):
        """Por el camino de verdad (`obtener` y `guardar`; sólo se sustituye la
        lectura): si kubera tropieza UNA vez al preguntar por la puerta, la orden
        confirmada no ofrece editar —falla cerrado— y un PUT en esa ventana es un
        409, pero la causa que se da es «no se pudo comprobar; intenta de nuevo»,
        no «falta la migración 0071» (que sí está). Y la falta REAL conserva su
        texto."""
        sin_saber = ("No se pudo comprobar si esta base ya permite editar una orden confirmada "
                     "(la migración 0071); intenta de nuevo en unos segundos.")
        falta = ("Editar una orden confirmada todavía no está habilitado en esta base: falta la "
                 "migración 0071.")

        def confirmada(*a, **k) -> dict:
            return orden("confirmada", guia=None, mp_canal=None, mp_cuenta=None, mp_orden=None,
                         creado_via="panel")

        self.parche("_tablas", mock.MagicMock(return_value=True))
        self.parche("_leer_id", mock.MagicMock(side_effect=confirmada))
        self.parche("habilitado", mock.MagicMock(return_value=True))
        self.parche("hay_bucket", mock.MagicMock(return_value=False))
        transicion = self.parche("_transicion", mock.MagicMock(return_value={"cuadra": 5}))
        leer = self.parche("_leer_edicion", mock.MagicMock(
            side_effect=psycopg2.OperationalError("canceling statement due to statement timeout")))
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            p = ov.obtener(7, OPER)["permisos"]
        self.assertFalse(p["editar"], "la duda cierra")
        self.assertEqual(p["porque"]["editar"], sin_saber)
        self.assertTrue(p["cancelar"] and p["entregar"], "lo demás no se entera")
        # Un PUT dentro de la ventana (el tropiezo se recuerda unos segundos): 409
        # con ESE texto, sin volver a preguntar y sin llegar a la sentencia.
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(7, 3, {"guia": "G-1"}, OPER)
        self.assertEqual((e.exception.status, str(e.exception)), (409, sin_saber))
        self.assertEqual(leer.call_count, 1)
        transicion.assert_not_called()
        # Un BORRADOR ni pregunta: el tropiezo no lo toca.
        ov._leer_id.side_effect = lambda *a, **k: orden("borrador")
        self.assertTrue(ov.obtener(7, OPER)["permisos"]["editar"])
        ov._leer_id.side_effect = confirmada
        # Vencido el recuerdo del tropiezo, la base ya contesta: se edita.
        leer.side_effect = None
        leer.return_value = True
        self.assertFalse(ov.obtener(7, OPER)["permisos"]["editar"], "todavía en la ventana")
        ov._olvidar("edicion")                       # (= pasaron los `_TTL_FALLO` segundos)
        self.assertTrue(ov.obtener(7, OPER)["permisos"]["editar"])
        self.assertEqual(ov.guardar(7, 3, {"guia": "G-1"}, OPER)["mensaje"], "Cambios guardados.")
        # Cuando de verdad FALTA (la base contestó, y dijo que no), se dice que falta.
        leer.return_value = False
        ov._olvidar("edicion")
        p = ov.obtener(7, OPER)["permisos"]
        self.assertEqual((p["editar"], p["porque"]["editar"]), (False, falta))
        with self.assertRaises(ov.Conflicto) as e:
            ov.guardar(7, 3, {"guia": "G-1"}, OPER)
        self.assertEqual(str(e.exception), falta)
        # Y con kubera en pausa ni se pregunta: tampoco se sabe.
        ov._olvidar_cache()
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING"):
            ov.anotar_caida(psycopg2.OperationalError("timeout"))
        self.assertEqual(ov.permisos(confirmada(), OPER, True, False,
                                     ov._edicion(confirmada()))["porque"]["editar"], sin_saber)
        self.assertEqual(leer.call_count, 3, "en pausa no se intentó")

    def test_solo_una_confirmada_pregunta_por_la_0071(self):
        lista = self.parche("edicion_lista", mock.MagicMock(return_value=True))
        for estado in ("borrador", "entregada", "cancelada", "entregada_cancelada"):
            self.assertIs(ov._edicion(orden(estado)), False)
        lista.assert_not_called()
        cur = object()
        self.assertIs(ov._edicion(orden("confirmada"), cur), True)
        lista.assert_called_once_with(cur=cur)
        lista.return_value = False
        self.assertIs(ov._edicion(orden("confirmada")), False)
        # «No se pudo comprobar» pasa TAL CUAL: no se aplana a False (sería «falta»).
        lista.return_value = None
        self.assertIsNone(ov._edicion(orden("confirmada")))

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
        self.parche("edicion_lista", mock.MagicMock(return_value=True))
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
