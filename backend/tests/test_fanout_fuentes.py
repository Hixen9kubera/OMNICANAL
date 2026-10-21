"""Pruebas de la VALIDACIÓN DE FUENTES (`services/fanout_fuentes.py`) y su candado en
el arranque (`scheduler.iniciar`).

── EL DEFECTO ───────────────────────────────────────────
`STOCK_WATCH_FUENTE` y `FANOUT_FUENTE` tienen cada una su candado contra erratas, pero
nadie miraba si JUNTAS tienen sentido. Con FANOUT_FUENTE=kubera y stock_watch todavía
en Odoo, el fan-out reparte el libro y ningún cambio del libro llega a los canales
(salvo con una venta), mientras stock_watch movería Woo con deltas de un Odoo muerto.

── QUÉ FIJAN ────────────────────────────────────────────
  1. COHERENTES: (odoo, woo) hoy, (libro, woo) sombra antes de T0, (libro, kubera) T0;
     vacío = el valor por omisión de cada lector; mayúsculas y espacios no importan.
  2. INCOHERENTES: (odoo, kubera), STOCK_WATCH_FUENTE=woo (el modo puente quedó
     descartado) y cualquier errata, con un motivo que dice qué corregir.
  3. AL ARRANCAR con una incoherente y stock_watch encendido: CRITICAL en el log y un
     aviso por Slack (en un hilo: regla 11). Su job NO se registra si stock_watch conoce
     su fuente (odoo, libro): su pasada correría. Con una STOCK_WATCH_FUENTE que no
     conoce (`woo`, una errata) SÍ se registra: su pasada se niega sola (contrato de WOO
     SALIDA en `test_stock_watch_fuente`). El fan-out sigue (el recuperador se registra
     igual). Apagado: sólo WARNING, sin aviso.
  4. EL ESTADO queda consultable para el Dashboard.
  5. T0 SIN ENCOLAR: (libro, kubera) con `STOCK_WATCH_SOLO_REGISTRO` encendida (así nace)
     o stock_watch apagado NO es coherente: el libro no llega a los canales. Momento
     `t0_sin_encolar`, CRITICAL y aviso (aunque stock_watch esté apagado); stock_watch
     se registra igual (sólo registra: no estorba).

── NO SE TOCA NADA REAL ─────────────────────────────────
El planificador es de mentira y las alertas también. Nada de base ni de red.

    cd backend && python -m unittest tests.test_fanout_fuentes -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import alertas  # noqa: E402
from services import fanout_fuentes as FF  # noqa: E402
from tests import test_fanout_seguro as _TS  # noqa: E402

setUpModule = _TS.setUpModule
tearDownModule = _TS.tearDownModule


class Validar(unittest.TestCase):
    def test_las_tres_coherentes(self):
        for sw, ff, momento in (("odoo", "woo", "hoy"), ("libro", "woo", "sombra"), ("libro", "kubera", "t0")):
            ok, motivo = FF.validar(sw, ff)
            self.assertTrue(ok, motivo)
            self.assertEqual(FF.momento(sw, ff), momento)
            self.assertIn(f"STOCK_WATCH_FUENTE={sw} + FANOUT_FUENTE={ff}", motivo)

    def test_vacio_es_el_valor_por_omision_de_cada_lector(self):
        for sw, ff in (("", ""), (None, None), ("  ", " "), ("odoo", ""), ("", "woo")):
            self.assertEqual(FF.validar(sw, ff)[0], True, (sw, ff))
            self.assertEqual(FF.momento(sw, ff), "hoy")
        self.assertFalse(FF.validar("", "kubera")[0], "vacío es odoo: con el libro no casa")

    def test_mayusculas_y_espacios_no_importan(self):
        self.assertTrue(FF.validar(" Libro ", "KUBERA")[0])

    def test_odoo_con_el_libro_no(self):
        ok, motivo = FF.validar("odoo", "kubera")
        self.assertFalse(ok)
        self.assertIn("STOCK_WATCH_FUENTE=libro", motivo, "dice qué corregir")
        self.assertIsNone(FF.momento("odoo", "kubera"))

    def test_el_modo_puente_quedo_descartado(self):
        for ff in ("woo", "kubera"):
            ok, motivo = FF.validar("woo", ff)
            self.assertFalse(ok, ff)
            self.assertIn("modo puente", motivo)

    def test_las_erratas(self):
        for sw, ff, pista in (("wooo", "woo", "STOCK_WATCH_FUENTE='wooo' no existe"),
                              ("libro", "kubra", "FANOUT_FUENTE='kubra' no existe"),
                              ("kubera", "woo", "STOCK_WATCH_FUENTE='kubera' no existe"),
                              ("libro", "libro", "FANOUT_FUENTE='libro' no existe")):
            ok, motivo = FF.validar(sw, ff)
            self.assertFalse(ok, (sw, ff))
            self.assertIn(pista, motivo)

    def test_registrable(self):
        for sw, ff, esperado in (("odoo", "woo", True), ("libro", "kubera", True),
                                 ("odoo", "kubera", False), ("libro", "kubra", False),
                                 ("", "kubera", False), ("woo", "woo", True), ("x", "y", True)):
            self.assertEqual(FF.registrable(sw, ff), esperado, (sw, ff))

    def test_t0_sin_encolar(self):
        for kw, pista in (({"solo_registro": True}, "STOCK_WATCH_SOLO_REGISTRO está encendida"),
                          ({"stock_watch_encendido": False}, "STOCK_WATCH_ENABLED está apagada")):
            ok, motivo = FF.validar("libro", "kubera", **kw)
            self.assertFalse(ok, kw)
            self.assertIn(pista, motivo)
            self.assertIn("STOCK_WATCH_SOLO_REGISTRO=false", motivo, "dice qué corregir")
            self.assertEqual(FF.momento("libro", "kubera", **kw), FF.T0_SIN_ENCOLAR)
            self.assertTrue(FF.registrable("libro", "kubera"), "registrarlo no estorba")
        # Antes de T0 sólo registrar es lo esperado: la sombra y hoy no cambian.
        for sw, ff, m in (("libro", "woo", "sombra"), ("odoo", "woo", "hoy")):
            self.assertTrue(FF.validar(sw, ff, solo_registro=True, stock_watch_encendido=False)[0])
            self.assertEqual(FF.momento(sw, ff, solo_registro=True), m)

    def test_dos_erratas_se_dicen_las_dos(self):
        ok, motivo = FF.validar("x", "y")
        self.assertFalse(ok)
        self.assertIn("STOCK_WATCH_FUENTE='x'", motivo)
        self.assertIn("FANOUT_FUENTE='y'", motivo)


class Arranque(unittest.TestCase):
    """`scheduler.iniciar()` de verdad con el planificador suplantado."""

    def setUp(self):
        p = mock.patch.dict(FF._estado, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def _jobs(self, slack: bool = False, **cfg) -> dict:
        from services import scheduler
        base = {"stock_watch_enabled": True, "mysql_enabled": True, "stock_watch_fuente": "odoo",
                "fanout_fuente": "woo", "fanout_enabled": True, "fanout_recuperar_enabled": True,
                "fanout_recuperar_min": 10, "stock_watch_solo_registro": False}
        falso = mock.MagicMock()
        with mock.patch.object(scheduler, "AsyncIOScheduler", return_value=falso), \
                mock.patch.object(scheduler, "_scheduler", None), \
                mock.patch.object(alertas, "disponible", return_value=slack), \
                mock.patch.multiple(scheduler.settings, **{**base, **cfg}):
            scheduler.iniciar()
        return {c.kwargs.get("id"): c for c in falso.add_job.call_args_list}

    def test_coherente_registra_stock_watch(self):
        for sw, ff in (("odoo", "woo"), ("libro", "woo"), ("libro", "kubera")):
            jobs = self._jobs(stock_watch_fuente=sw, fanout_fuente=ff)
            self.assertIn("stock_watch", jobs, (sw, ff))
            self.assertEqual(FF.estado()["stock_watch_job"], "registrado")

    def test_t0_que_solo_registra_se_registra_y_lo_grita(self):
        with self.assertLogs("omnicanal.fanout_fuentes", level="CRITICAL") as logs:
            jobs = self._jobs(slack=True, stock_watch_fuente="libro", fanout_fuente="kubera",
                              stock_watch_solo_registro=True)
        self.assertIn("stock_watch", jobs)
        self.assertIn("fanout_fuentes_aviso", jobs)
        self.assertIn("T0 SIN ENCOLAR", logs.output[0])
        est = FF.estado()
        self.assertEqual((est["ok"], est["momento"], est["avisa"]), (False, FF.T0_SIN_ENCOLAR, True))
        self.assertEqual(est["stock_watch_job"], "registrado: sólo registra, no encola (STOCK_WATCH_SOLO_REGISTRO)")

    def test_t0_con_stock_watch_apagado_tambien_avisa(self):
        with self.assertLogs("omnicanal.fanout_fuentes", level="CRITICAL"):
            jobs = self._jobs(slack=True, stock_watch_enabled=False, stock_watch_fuente="libro",
                              fanout_fuente="kubera")
        self.assertNotIn("stock_watch", jobs)
        self.assertIn("fanout_fuentes_aviso", jobs, "justo ése es el problema")
        est = FF.estado()
        self.assertEqual((est["ok"], est["momento"], est["stock_watch_job"]),
                         (False, FF.T0_SIN_ENCOLAR, "apagado"))

    def test_incoherente_no_registra_stock_watch_y_lo_grita(self):
        with self.assertLogs("omnicanal.fanout_fuentes", level="CRITICAL") as logs:
            jobs = self._jobs(stock_watch_fuente="odoo", fanout_fuente="kubera")
        self.assertNotIn("stock_watch", jobs)
        self.assertIn("FUENTES DEL STOCK INCOHERENTES", logs.output[0])
        self.assertIn("FANOUT_FUENTE=kubera", logs.output[0])
        est = FF.estado()
        self.assertFalse(est["ok"])
        self.assertEqual(est["stock_watch_job"], "NO registrado: fuentes incoherentes")
        self.assertEqual((est["stock_watch_fuente"], est["fanout_fuente"]), ("odoo", "kubera"))
        self.assertIsNotNone(est["validado_en"])

    def test_el_fanout_por_ventas_sigue(self):
        jobs = self._jobs(stock_watch_fuente="odoo", fanout_fuente="kubera")
        self.assertNotIn("stock_watch", jobs)
        self.assertIn("fanout_recuperar", jobs, "el fan-out y su recuperador no dependen de stock_watch")

    def test_una_fuente_que_stock_watch_no_conoce_si_se_registra_y_lo_grita(self):
        # Su pasada se niega sola (`stock_watch._fuente_sin_construir`): frenarla aquí
        # escondería esa negativa. Es el contrato de WOO SALIDA (`woo` incluido).
        for sw, ff in (("woo", "woo"), ("woo", "kubera"), ("libr", "woo")):
            with self.assertLogs("omnicanal.fanout_fuentes", level="CRITICAL") as logs:
                jobs = self._jobs(stock_watch_fuente=sw, fanout_fuente=ff)
            self.assertIn("stock_watch", jobs, (sw, ff))
            self.assertIn("se NIEGA", logs.output[0])
            est = FF.estado()
            self.assertFalse(est["ok"])
            self.assertTrue(est["registra"])
            self.assertEqual(est["stock_watch_job"],
                             "registrado: su pasada se niega (STOCK_WATCH_FUENTE desconocida)")

    def test_una_errata_en_fanout_fuente_con_una_fuente_conocida_no_se_registra(self):
        for sw in ("odoo", "libro"):
            with self.assertLogs("omnicanal.fanout_fuentes", level="CRITICAL"):
                jobs = self._jobs(stock_watch_fuente=sw, fanout_fuente="kubra")
            self.assertNotIn("stock_watch", jobs, sw)

    def test_avisa_por_slack_en_un_hilo(self):
        jobs = self._jobs(slack=True, stock_watch_fuente="odoo", fanout_fuente="kubera")
        c = jobs["fanout_fuentes_aviso"]
        self.assertEqual(c.args[1], "date")
        llamado = mock.AsyncMock()
        from services import scheduler
        with mock.patch.object(scheduler.asyncio, "to_thread", llamado):
            asyncio.run(c.args[0]())
        llamado.assert_awaited_once()
        self.assertIs(llamado.await_args.args[0], FF.avisar, "regla 11: el candado de alertas lee la base")

    def test_sin_slack_no_hay_job_de_aviso(self):
        jobs = self._jobs(slack=False, stock_watch_fuente="odoo", fanout_fuente="kubera")
        self.assertNotIn("fanout_fuentes_aviso", jobs)

    def test_con_stock_watch_apagado_solo_advierte(self):
        with self.assertLogs("omnicanal.fanout_fuentes", level="WARNING") as logs:
            jobs = self._jobs(slack=True, stock_watch_enabled=False, stock_watch_fuente="odoo",
                              fanout_fuente="kubera")
        self.assertFalse(any("CRITICAL" in x for x in logs.output))
        self.assertNotIn("fanout_fuentes_aviso", jobs)
        self.assertEqual(FF.estado()["stock_watch_job"], "apagado")
        self.assertFalse(FF.estado()["ok"], "el Dashboard lo ve igual")


class Avisar(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(FF._estado, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.avisos: list[tuple] = []
        q = mock.patch.object(alertas, "avisar_estado",
                              side_effect=lambda *a, **k: self.avisos.append((a, k)) or True)
        q.start()
        self.addCleanup(q.stop)

    def _arrancar(self, sw, ff, quiere=True, solo_registro=False):
        with mock.patch.multiple(FF.settings, stock_watch_fuente=sw, fanout_fuente=ff,
                                 stock_watch_solo_registro=solo_registro, stock_watch_enabled=quiere):
            return FF.al_arrancar(quiere)

    def test_incoherente_avisa_por_cambio_de_estado(self):
        self.assertFalse(self._arrancar("odoo", "kubera"))
        self.assertTrue(FF.avisar())
        (tipo, estado, texto), _ = self.avisos[0]
        self.assertEqual((tipo, estado), ("fanout_fuentes", "incoherente"))
        self.assertIn("stock_watch NO se registró", texto)
        self.assertLessEqual(len(estado), 30, "cabe en alertas_estado.estado VARCHAR(30)")

    def test_coherente_anuncia_la_recuperacion(self):
        self.assertTrue(self._arrancar("libro", "kubera"))
        FF.avisar()
        (tipo, estado, _texto), k = self.avisos[0]
        self.assertEqual((tipo, estado), ("fanout_fuentes", "ok"))
        self.assertIn("coherentes", k["texto_ok"])

    def test_con_una_fuente_desconocida_dice_que_se_registro_y_se_niega(self):
        self.assertTrue(self._arrancar("woo", "woo"))
        self.assertTrue(FF.avisar())
        (tipo, estado, texto), _ = self.avisos[0]
        self.assertEqual((tipo, estado), ("fanout_fuentes", "incoherente"))
        self.assertIn("se registró, pero su pasada se niega", texto)
        self.assertIn("modo puente", texto)

    def test_t0_sin_encolar_avisa(self):
        self.assertTrue(self._arrancar("libro", "kubera", solo_registro=True))
        self.assertTrue(FF.avisar())
        (tipo, estado, texto), _ = self.avisos[0]
        self.assertEqual((tipo, estado), ("fanout_fuentes", "incoherente"))
        self.assertIn("stock_watch no encola sus cambios", texto)
        self.assertIn("T0 sin encolar", texto)

    def test_sin_stock_watch_no_avisa(self):
        self._arrancar("odoo", "kubera", quiere=False)
        self.assertFalse(FF.avisar())
        self.assertEqual(self.avisos, [])

    def test_sin_arranque_no_avisa(self):
        self.assertFalse(FF.avisar())


class Estado(unittest.TestCase):
    def test_sin_arranque_valida_la_configuracion_actual(self):
        with mock.patch.dict(FF._estado, {}, clear=True), \
                mock.patch.multiple(FF.settings, stock_watch_fuente="libro", fanout_fuente="kubera",
                                    stock_watch_solo_registro=False, stock_watch_enabled=True):
            est = FF.estado()
        self.assertTrue(est["ok"])
        self.assertEqual(est["momento"], "t0")
        self.assertIsNone(est["validado_en"])
        self.assertIsNone(est["stock_watch_job"])

    def test_sin_arranque_ve_el_t0_que_solo_registra(self):
        # `STOCK_WATCH_SOLO_REGISTRO` nace encendida: un T0 que la olvida no es T0.
        with mock.patch.dict(FF._estado, {}, clear=True), \
                mock.patch.multiple(FF.settings, stock_watch_fuente="libro", fanout_fuente="kubera",
                                    stock_watch_solo_registro=True, stock_watch_enabled=True):
            est = FF.estado()
        self.assertEqual((est["ok"], est["momento"]), (False, FF.T0_SIN_ENCOLAR))

    def test_no_lee_la_base_ni_la_red(self):
        # Las trampas de `test_fanout_seguro` revientan si validar/estado tocaran algo.
        with mock.patch.dict(FF._estado, {}, clear=True):
            FF.estado()
            FF.validar("odoo", "kubera")
            FF.al_arrancar(True)


if __name__ == "__main__":
    unittest.main()
