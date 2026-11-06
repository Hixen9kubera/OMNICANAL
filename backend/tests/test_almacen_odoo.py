"""La copia de Odoo a kubera de un cedis (services/almacen_odoo.py): las reglas, sin red.

Qué fijan:
  1. Cómo se escribe una ubicación (rack partido, SIN UBICAR, zonas con su nombre).
  2. La foto: trae negativos con su cedis, NO trae productos archivados, usa el
     nombre como SKU sólo si lo es, y revienta antes que adivinar.
  3. El historial: el signo de `delta` sale de qué lado del movimiento está en el
     cedis; la causa es la del panel; tampoco trae archivados.
  4. El cuadre: Σ delta del historial = Σ piezas de la foto, por SKU.
  5. La escritura del historial va por DIFERENCIAS y no acepta una lectura trunca.
  6. Las guardas: el cedis que ya no es de Odoo no se copia.
  7. La pasada: firma como copiador (0076), no toca `almacen.locations` y anota la
     frescura en `almacen.copias_odoo`.
  8. El vigilante: los tres almacenes, nunca lanza y anota la falla; el scheduler
     lo corre cada hora y una completa a las 09:00 UTC, en un hilo (regla 11).
"""
import asyncio
import os
import sys
import threading
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import almacen_odoo as ao  # noqa: E402

UTC = timezone.utc


def _q(qid, pid, ruta, cantidad, entro="2026-06-10 19:22:53"):
    return {"id": qid, "product_id": [pid, "x"], "location_id": [1, ruta],
            "quantity": cantidad, "in_date": entro}


ACTIVO = {"default_code": "ACC-0886-NEG", "name": "x", "active": True}
ARCHIVADO = {"default_code": "ACC-0886-NEG", "name": "x", "active": False}

# Ubicaciones de Odoo: id → lo que devuelve stock.location
UBIS = {
    1: {"complete_name": "Physical Locations/Traslado entre almacenes", "usage": "transit", "warehouse_id": False},
    2: {"complete_name": "TEX2/FERRAFORME", "usage": "internal", "warehouse_id": [150, "TEXCO II"]},
    3: {"complete_name": "TEX2/FERRAFORME/BLOQUE D/FILA 1/T6", "usage": "internal", "warehouse_id": [150, "TEXCO II"]},
    4: {"complete_name": "TEX2/Salida", "usage": "internal", "warehouse_id": [150, "TEXCO II"]},
    5: {"complete_name": "Partners/Customers", "usage": "customer", "warehouse_id": False},
    6: {"complete_name": "TEXCO/FERRAFORME/STAGE", "usage": "internal", "warehouse_id": [135, "TEXCO  I"]},
}
ALM = {150: "TEX2", 135: "TEXCO"}


def _l(lid, pid, org, dst, cantidad, mov=None, pick=None, fecha="2026-10-06 21:24:50"):
    return {"id": lid, "product_id": [pid, "x"], "location_id": [org, "x"],
            "location_dest_id": [dst, "x"], "quantity": cantidad, "date": fecha,
            "write_date": fecha, "reference": f"DOC/{lid}", "create_uid": [7, "Calidad TEX 1"],
            "move_id": [mov, "x"] if mov else False, "picking_id": [pick, "x"] if pick else False}


class Ubicacion(unittest.TestCase):
    def test_rack_completo_se_parte(self):
        self.assertEqual(ao.ubicacion_de("TEX2/FERRAFORME/BLOQUE S1/FILA 2/T17"),
                         ("BLOQUE S1-FILA 2-T17", "S1", "2", "T17"))

    def test_la_raiz_de_la_nave_es_sin_ubicar(self):
        self.assertEqual(ao.ubicacion_de("TEX2/FERRAFORME"), ("SIN UBICAR", None, None, None))

    def test_zona_que_no_es_rack_conserva_su_nombre(self):
        self.assertEqual(ao.ubicacion_de("TEX2/FERRAFORME/REQUERIMENTOS")[0], "REQUERIMENTOS")
        self.assertEqual(ao.ubicacion_de("TEX2/Zona de empaquetado")[0], "Zona de empaquetado")
        # No es la raíz de la nave: si cayera en SIN UBICAR chocaría con ella.
        self.assertEqual(ao.ubicacion_de("TEX2/FERRAFORME Archivar"),
                         ("FERRAFORME Archivar", None, None, None))


class Foto(unittest.TestCase):
    def test_trae_el_negativo_con_su_signo_y_el_cedis(self):
        filas, s = ao.filas_locations(
            "TEX2", [_q(1, 10, "TEX2/FERRAFORME/BLOQUE D/FILA 1/T6", 150.0),
                     _q(2, 10, "TEX2/FERRAFORME", -2.0)], {10: ACTIVO})
        self.assertEqual([(f[0], f[1], f[2], f[6]) for f in filas],
                         [("TEX2", "ACC-0886-NEG", "BLOQUE D-FILA 1-T6", 150),
                          ("TEX2", "ACC-0886-NEG", "SIN UBICAR", -2)])
        self.assertEqual((s["piezas"], s["negativos"], s["sin_ubicar"], s["skus"]), (148, 1, 1, 1))

    def test_los_archivados_no_se_traen(self):
        # El gemelo archivado conserva el rack; el activo lleva la cuenta. Sólo entra el activo.
        filas, s = ao.filas_locations(
            "TEX2", [_q(1, 10, "TEX2/FERRAFORME", 648.0),
                     _q(2, 11, "TEX2/FERRAFORME/BLOQUE G/FILA 1/T3", 360.0),
                     _q(3, 11, "TEX2/FERRAFORME", 6720.0)],
            {10: ACTIVO, 11: ARCHIVADO})
        self.assertEqual([(f[2], f[6], f[8]) for f in filas], [("SIN UBICAR", 648, 10)])
        self.assertEqual((s["archivados_omitidos"], s["piezas"]), (2, 648))

    def test_sin_sku_usa_el_nombre_solo_si_es_un_sku(self):
        filas, s = ao.filas_locations(
            "TEX2", [_q(1, 10, "TEX2/FERRAFORME Archivar", -54.0)],
            {10: {"default_code": False, "name": "DEPO-0001-AZL", "active": True}})
        self.assertEqual(filas[0][1], "DEPO-0001-AZL")
        self.assertEqual(s["sin_sku_en_odoo"], 1)
        with self.assertRaises(ValueError):
            ao.filas_locations("TEX2", [_q(1, 10, "TEX2/FERRAFORME", 5.0)],
                               {10: {"default_code": False, "name": "Flor artificial", "active": True}})

    def test_no_adivina(self):
        with self.assertRaises(ValueError):   # decimales
            ao.filas_locations("TEX2", [_q(1, 10, "TEX2/FERRAFORME", 1.5)], {10: ACTIVO})
        with self.assertRaises(ValueError):   # dos productos activos, mismo SKU y ubicación
            ao.filas_locations("TEX2", [_q(1, 10, "TEX2/FERRAFORME", 1.0), _q(2, 11, "TEX2/FERRAFORME", 1.0)],
                               {10: ACTIVO, 11: dict(ACTIVO)})
        with self.assertRaises(ValueError):   # Odoo no devolvió el producto
            ao.filas_locations("TEX2", [_q(1, 99, "TEX2/FERRAFORME", 1.0)], {})


class Historial(unittest.TestCase):
    def _filas(self, lineas, movs=None, picks=None, productos=None):
        return ao.filas_historial("TEX2", lineas, UBIS, movs or {}, picks or {},
                                  productos or {10: ACTIVO}, ALM)

    def test_el_signo_sale_de_que_lado_esta_en_el_cedis(self):
        filas, s = self._filas([
            _l(1, 10, 1, 2, 300.0),    # llega de fuera            → +300
            _l(2, 10, 2, 3, 150.0),    # raíz → rack, adentro      →    0
            _l(3, 10, 4, 5, 2.0),      # Salida → cliente          →   −2
            _l(4, 10, 6, 2, 20.0),     # de TEXCO I a TEXCO II     →  +20
        ])
        self.assertEqual([(f[4], f[5], f[6], f[7]) for f in filas], [
            (300, 300, None, "SIN UBICAR"),
            (150, 0, "SIN UBICAR", "BLOQUE D-FILA 1-T6"),
            (2, -2, "Salida", None),
            (20, 20, None, "SIN UBICAR"),
        ])
        self.assertEqual((s["entran"], s["salen"], s["adentro"]), (2, 1, 1))
        # La ruta completa de Odoo de los dos lados se conserva siempre.
        self.assertEqual((filas[2][15], filas[2][16]), ("TEX2/Salida", "Partners/Customers"))

    def test_la_causa_es_la_del_panel(self):
        movs = {50: {"is_inventory": True, "scrapped": False, "origin": False, "picking_type_id": False},
                51: {"is_inventory": False, "scrapped": False, "origin": "S40478",
                     "picking_type_id": [9, "TEXCO II: Órdenes de entrega"]},
                52: {"is_inventory": False, "scrapped": False, "origin": "S30635",
                     "picking_type_id": [9, "TEXCO II: Órdenes de entrega"]}}
        picks = {70: {"partner_id": [1, "PÚBLICO EN GENERAL"]}, 71: {"partner_id": [2, "FULL"]}}
        filas, _ = self._filas([
            _l(1, 10, 1, 2, 300.0),                    # de tránsito a bodega
            _l(2, 10, 2, 3, 150.0, mov=50),            # ajuste de inventario
            _l(3, 10, 2, 3, 150.0),                    # reacomodo dentro del mismo almacén
            _l(4, 10, 4, 5, 2.0, mov=51, pick=70),     # a un cliente
            _l(5, 10, 4, 5, 60.0, mov=52, pick=71),    # a la bodega de un canal
            _l(6, 10, 6, 2, 20.0),                     # entre almacenes
        ], movs=movs, picks=picks)
        self.assertEqual([f[3] for f in filas],
                         ["entrada", "ajuste", "preparacion", "venta", "envio_full", "traspaso"])
        # documento, referencia, contraparte, tipo de operación, quién
        self.assertEqual(filas[3][8:13], ("DOC/4", "S40478", "PÚBLICO EN GENERAL",
                                          "TEXCO II: Órdenes de entrega", "Calidad TEX 1"))
        self.assertEqual(filas[0][8:12], ("DOC/1", None, None, None))

    def test_los_archivados_no_se_traen(self):
        filas, s = self._filas([_l(1, 10, 1, 2, 120.0), _l(2, 11, 1, 2, 120.0)],
                               productos={10: ACTIVO, 11: ARCHIVADO})
        self.assertEqual([f[13] for f in filas], [1])
        self.assertEqual(s["archivados_omitidos"], 1)

    def test_fechas_en_utc_y_no_adivina(self):
        filas, _ = self._filas([_l(1, 10, 1, 2, 5.0, fecha="2026-10-06 21:24:50")])
        self.assertEqual(filas[0][2], datetime(2026, 10, 6, 21, 24, 50, tzinfo=UTC))
        self.assertEqual(filas[0][17], datetime(2026, 10, 6, 21, 24, 50, tzinfo=UTC))
        with self.assertRaises(ValueError):   # decimales
            self._filas([_l(1, 10, 1, 2, 1.5)])
        with self.assertRaises(ValueError):   # Odoo no devolvió el producto
            self._filas([_l(1, 99, 1, 2, 1.0)])

    def test_una_linea_que_no_toca_el_cedis_no_es_suya(self):
        filas, s = self._filas([_l(1, 10, 6, 5, 3.0)])     # TEXCO I → cliente
        self.assertEqual((filas, s["fuera_del_cedis"]), ([], 1))


class Cuadre(unittest.TestCase):
    def test_el_historial_reproduce_la_foto(self):
        f_loc, _ = ao.filas_locations(
            "TEX2", [_q(1, 10, "TEX2/FERRAFORME/BLOQUE D/FILA 1/T6", 300.0),
                     _q(2, 10, "TEX2/FERRAFORME", -2.0)], {10: ACTIVO})
        f_his, _ = ao.filas_historial(
            "TEX2", [_l(1, 10, 1, 2, 300.0), _l(2, 10, 2, 3, 300.0), _l(3, 10, 4, 5, 2.0)],
            UBIS, {}, {}, {10: ACTIVO}, ALM)
        self.assertEqual(ao.cuadre_filas(f_loc, f_his), {"skus": 1, "descuadres": 0})
        self.assertEqual(ao.cuadre_filas(f_loc, f_his[:2]), {"skus": 1, "descuadres": 1})


class Cursor:
    """La base, de mentira: contesta cada SELECT con lo que se le sembró (por un trozo
    de su texto) y anota lo demás."""

    def __init__(self, respuestas):
        self.respuestas = respuestas      # [(trozo del SQL, filas)]
        self.hechas: list[tuple[str, tuple]] = []
        self._filas: list[dict] = []

    def execute(self, sql, params=None):
        self.hechas.append((" ".join(sql.split()), params))
        for trozo, filas in self.respuestas:
            if trozo in sql:
                self._filas = list(filas)
                return
        self._filas = []

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def escrituras(self):
        return [s for s, _ in self.hechas if not s.lower().startswith("select")]


class EscribirHistorial(unittest.TestCase):
    def _fila(self, linea, delta=5):
        ahora = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)
        return ("TEX2", "ACC-1", ahora, "entrada", 5, delta, None if delta > 0 else "SIN UBICAR",
                "SIN UBICAR" if delta >= 0 else None, "DOC", None, None, None, None,
                linea, 10, "a", "b", ahora)

    def _en_base(self, id_, fila):
        return {"id": id_, **dict(zip(ao._H_LISTA, fila))}

    def _correr(self, actuales, filas, **kw):
        cur = Cursor([("from almacen.historial_movimientos", actuales)])
        with mock.patch.object(ao, "execute_values") as ev:
            r = ao.escribir_historial(cur, "TEX2", filas, **kw)
        return cur, ev, r

    def test_el_delta_solo_agrega_y_corrige_nunca_borra(self):
        f1, f2 = self._fila(1), self._fila(2)
        cambiada = self._en_base(501, self._fila(1, delta=-5))
        cur, ev, r = self._correr([cambiada], [f1, f2], completo=False, aplicar=True)
        self.assertEqual(r, {"leidas": 2, "nuevos": 1, "cambiados": 1, "borrados": 0})
        self.assertFalse([s for s in cur.escrituras() if s.startswith("delete")])
        self.assertEqual(ev.call_args.args[2], [f1, f2])
        # y sólo pregunta por las líneas que trajo, no por todo el cedis
        self.assertIn("odoo_move_line_id = any", cur.hechas[0][0])

    def test_sin_lineas_nuevas_el_delta_no_toca_la_base(self):
        cur, ev, r = self._correr([], [], completo=False, aplicar=True)
        self.assertEqual((r["leidas"], cur.hechas), (0, []))
        ev.assert_not_called()

    def test_la_pasada_completa_borra_lo_que_ya_no_viene(self):
        f1 = self._fila(1)
        actuales = [self._en_base(501, f1), self._en_base(502, self._fila(2))]   # la 2: producto archivado después
        cur, ev, r = self._correr(actuales, [f1], completo=True, aplicar=True, forzar=True)
        self.assertEqual(r, {"leidas": 1, "nuevos": 0, "cambiados": 0, "borrados": 1})
        self.assertEqual([p for s, p in cur.hechas if s.startswith("delete")], [([502],)])
        ev.assert_not_called()                                 # nada cambió: nada se escribe

    def test_un_historial_que_encoge_no_se_acepta(self):
        actuales = [self._en_base(500 + i, self._fila(i)) for i in range(10)]
        with self.assertRaises(RuntimeError):
            self._correr(actuales, [self._fila(i) for i in range(7)], completo=True, aplicar=True)
        with self.assertRaises(RuntimeError):
            self._correr(actuales, [], completo=True, aplicar=True)


class Guardas(unittest.TestCase):
    def _cur(self, fuente="odoo", tablas=True):
        return Cursor([
            ("to_regclass", [{"historial": tablas, "catalogo": "almacen.almacenes"}]),
            ("from almacen.almacenes", [{"nombre": "TEXCO II", "fuente": fuente, "odoo_warehouse_id": 150}]),
        ])

    def test_el_cedis_de_odoo_se_copia(self):
        self.assertEqual(ao.guardas(self._cur(), "TEX2"), {"nombre": "TEXCO II", "odoo_warehouse_id": 150})

    def test_el_que_ya_es_de_kubera_no(self):
        with self.assertRaises(ao.NoAplica):
            ao.guardas(self._cur(fuente="kubera"), "TEX2")

    def test_sin_las_tablas_no_aplica_y_no_truena(self):
        with self.assertRaises(ao.NoAplica):
            ao.guardas(self._cur(tablas=False), "TEX2")


class Pasada(unittest.TestCase):
    """`sincronizar` contra una base y un Odoo de mentira."""

    def _correr(self, aplicar=True):
        cur = Cursor([
            ("to_regclass", [{"historial": True, "catalogo": "almacen.almacenes"}]),
            ("from almacen.almacenes", [{"nombre": "TEXCO", "fuente": "odoo", "odoo_warehouse_id": 135}]),
            ("max(odoo_escrito_at)", [{"marca": None}]),
        ])

        class Abrir:
            def __enter__(self_):
                return cur

            def __exit__(self_, *a):
                return False

        lineas = {"lineas": [_l(1, 10, 6, 5, 3.0)], "ubis": UBIS, "movs": {}, "picks": {},
                  "productos": {10: ACTIVO}}
        with mock.patch.object(ao, "leer_almacenes", return_value={"TEXCO": {"id": 135, "vista": 9}}), \
                mock.patch.object(ao, "leer_movimientos", return_value=lineas), \
                mock.patch.object(ao, "leer_foto") as foto, \
                mock.patch.object(ao, "execute_values") as ev:
            r = ao.sincronizar("TEXCO", completo=False, abrir=Abrir, aplicar=aplicar)
        return cur, ev, foto, r

    def test_observa_los_tres_almacenes(self):
        self.assertEqual(ao.CEDIS_OBSERVADOS, ("TEX2", "TEXCO", "DROP"))

    def test_firma_escribe_el_historial_y_anota_la_frescura(self):
        cur, ev, foto, r = self._correr()
        self.assertTrue(r["completo"])                       # sin renglones: carga completa
        self.assertEqual(r["historial"]["nuevos"], 1)
        textos = [t for t, _ in cur.hechas]
        firma = next(i for i, t in enumerate(textos) if "app.almacen_escritor" in t)
        self.assertIn("'copia_odoo:' || txid_current()", textos[firma])
        self.assertIn(", true)", textos[firma])              # local a la transacción (regla 13)
        anota = next(i for i, (t, _) in enumerate(cur.hechas) if t.startswith("update almacen.copias_odoo"))
        self.assertGreater(anota, firma)
        self.assertEqual(cur.hechas[anota][1][1:], (True, None, "TEXCO"))
        ev.assert_called_once()

    def test_no_toca_la_foto_de_ubicaciones(self):
        cur, ev, foto, r = self._correr()
        self.assertFalse([t for t, _ in cur.hechas if "almacen.locations" in t])
        foto.assert_not_called()                             # ni la lee de Odoo
        self.assertNotIn("foto", r)

    def test_el_ensayo_no_firma_ni_escribe(self):
        cur, ev, foto, r = self._correr(aplicar=False)
        self.assertEqual(cur.escrituras(), [])
        self.assertFalse([t for t, _ in cur.hechas if "almacen_escritor" in t])
        ev.assert_not_called()

    def test_la_falla_se_redacta(self):
        self.assertEqual(ao.redactar(OSError("https://odoo.ejemplo/xmlrpc 401")), "OSError")
        self.assertIn("encogió", ao.redactar(RuntimeError("el historial encogió")))


class Vigilante(unittest.TestCase):
    def setUp(self):
        ao._callados.clear()

    def _resultado(self, cedis, completo):
        return {"cedis": cedis, "nombre": cedis, "completo": completo, "aplicado": True, "desde": None,
                "historial": {"leidas": 0, "nuevos": 0, "cambiados": 0, "borrados": 0}, "cuadre": None}

    def test_pasa_por_los_tres_y_respeta_completo(self):
        pedidas = []

        def falso(cedis, *, completo, abrir):
            pedidas.append((cedis, completo))
            return self._resultado(cedis, completo)

        with mock.patch.object(ao, "sincronizar", falso), mock.patch.dict(sys.modules, {
                "services.supabase_db": mock.MagicMock()}):
            ao.vigilar()
            ao.vigilar(completo=True)
        self.assertEqual(pedidas, [("TEX2", False), ("TEXCO", False), ("DROP", False),
                                   ("TEX2", True), ("TEXCO", True), ("DROP", True)])

    def test_nunca_lanza_y_anota_la_falla_del_que_fallo(self):
        def a_veces(cedis, *, completo, abrir):
            if cedis == "TEXCO":
                raise RuntimeError("Odoo no contesta")
            return self._resultado(cedis, completo)

        sdb = mock.MagicMock()      # en el paquete y en sys.modules: otra prueba pudo importar el de verdad
        with mock.patch.object(ao, "sincronizar", a_veces), mock.patch.object(ao, "registrar_copia") as reg, \
                mock.patch.dict(sys.modules, {"services.supabase_db": sdb}), \
                mock.patch("services.supabase_db", sdb, create=True):
            hechas = ao.vigilar()
        self.assertEqual([r["cedis"] for r in hechas], ["TEX2", "DROP"])
        reg.assert_called_once()
        self.assertEqual(reg.call_args.args[1], "TEXCO")
        self.assertEqual((reg.call_args.kwargs["ok"], reg.call_args.kwargs["error"]),
                         (False, "RuntimeError: Odoo no contesta"))

    def test_no_se_enciman_dos_pasadas(self):
        with ao._una_a_la_vez, mock.patch.object(ao, "sincronizar") as s, mock.patch.dict(sys.modules, {
                "services.supabase_db": mock.MagicMock()}):
            self.assertEqual(ao.vigilar(), [])
        s.assert_not_called()

    def test_el_cedis_que_ya_no_aplica_se_dice_una_sola_vez(self):
        def no_aplica(cedis, *, completo, abrir):
            raise ao.NoAplica(f"{cedis} ya es de kubera")

        with mock.patch.object(ao, "sincronizar", no_aplica), mock.patch.dict(sys.modules, {
                "services.supabase_db": mock.MagicMock()}), self.assertLogs(ao.log, "INFO") as bitacora:
            ao.vigilar()
            ao.vigilar()
        self.assertEqual(sum("ya no se copia" in m for m in bitacora.output), 3)   # una por cedis


class ProgramadorFalso:
    """Apunta lo que `scheduler.iniciar()` registraría. JAMÁS arranca nada: con el de
    verdad, la prueba echaría a andar el sync de inventario y los sondeos."""

    def __init__(self, **kw) -> None:
        self.jobs: dict[str, tuple] = {}

    def add_job(self, fn, trigger, **kw) -> None:
        self.jobs[kw.get("id")] = (fn, trigger, kw)

    def start(self) -> None:
        pass

    def shutdown(self, wait: bool = False) -> None:
        pass


class Programador(unittest.TestCase):
    DSN_FALSO = "postgresql://nadie@127.0.0.1:1/no_se_usa"      # nunca se abre

    def registrar(self, encendido: bool, dsn: str = DSN_FALSO) -> dict[str, tuple]:
        try:
            from services import scheduler
        except ImportError as exc:            # sin APScheduler no hay scheduler que probar
            self.skipTest(f"no se puede importar el scheduler: {exc}")
        self.assertIsNone(scheduler._scheduler, "ya había un scheduler vivo en este proceso")
        with mock.patch.object(scheduler, "AsyncIOScheduler", ProgramadorFalso),                 mock.patch.object(scheduler.settings, "almacen_odoo_enabled", encendido),                 mock.patch.object(scheduler.settings, "supabase_db_url", dsn):
            scheduler.iniciar()
            try:
                return dict(scheduler._scheduler.jobs)
            finally:
                scheduler.detener()

    def test_se_registra_cada_hora_y_sin_encimarse(self):
        fn, trigger, kw = self.registrar(True)["almacen_odoo"]
        self.assertEqual((trigger, kw["minutes"], kw["max_instances"], kw["coalesce"]),
                         ("interval", 60, 1, True))
        self.assertIsNotNone(kw["next_run_time"].tzinfo)        # con zona: no depende de la del servidor

    def test_la_completa_es_a_las_9_utc(self):
        fn, trigger, kw = self.registrar(True)["almacen_odoo_completa"]
        self.assertEqual(kw["kwargs"], {"completo": True})
        campos = {f.name: str(f) for f in trigger.fields}
        self.assertEqual((campos["hour"], campos["minute"], str(trigger.timezone)), ("9", "0", "UTC"))

    def test_el_freno_de_emergencia_y_sin_kubera_no_lo_registran(self):
        for jobs in (self.registrar(False), self.registrar(True, dsn="")):
            self.assertNotIn("almacen_odoo", jobs)
            self.assertNotIn("almacen_odoo_completa", jobs)

    def test_corre_en_un_hilo_y_una_falla_no_sube_al_scheduler(self):
        fn = self.registrar(True)["almacen_odoo"][0]
        visto: dict = {}

        def _vigilar(completo=False):
            visto["hilo"] = threading.get_ident()
            raise RuntimeError("Odoo no contesta")

        async def caso() -> None:
            visto["loop"] = threading.get_ident()
            await fn()                                           # no debe lanzar

        with mock.patch.object(ao, "vigilar", side_effect=_vigilar):
            asyncio.run(caso())
        self.assertIn("hilo", visto)
        self.assertNotEqual(visto["hilo"], visto["loop"])        # regla 11: fuera del loop


if __name__ == "__main__":
    unittest.main()
