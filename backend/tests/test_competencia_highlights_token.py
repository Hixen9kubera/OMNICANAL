"""Pruebas de la espera por token nuevo del cron de /highlights
(`scripts/competencia_highlights.py`).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Un hueco del token no tumba la corrida.** El renovador externo cambia el
   token de ML ya vencido y el cron no puede renovar solo. El 24-sep las 1,238
   categorías dieron 401 a las 12:22 y el token nuevo llegó a las 12:23: la
   corrida abortó por un minuto.
2. `esperar_token_nuevo` devuelve True en cuanto el token ya NO es el que falló
   (aunque ya hubiera cambiado antes de empezar a esperar) y False si se acaba
   el tiempo. Sin token no cuenta como token nuevo.
3. `main` vuelve a sondear UNA vez tras el token nuevo y entonces sí guarda.

Sin red.

    cd backend && python -m unittest tests.test_competencia_highlights_token -v
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from unittest import mock

from scripts import competencia_highlights as ch


class EsperarTokenNuevo(unittest.TestCase):
    def setUp(self):
        self.olvidar = mock.patch.object(ch.competencia_ml, "_olvidar_token").start()
        self.dormir = mock.patch.object(ch.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def test_espera_hasta_que_cambia(self):
        with mock.patch.object(ch.meli, "_access_token", side_effect=["viejo", "viejo", "nuevo"]):
            self.assertTrue(ch.esperar_token_nuevo("viejo"))
        self.assertEqual(self.dormir.call_count, 2)

    def test_si_ya_cambio_no_espera(self):
        with mock.patch.object(ch.meli, "_access_token", return_value="nuevo"):
            self.assertTrue(ch.esperar_token_nuevo("viejo"))
        self.dormir.assert_not_called()

    def test_si_nunca_cambia_se_rinde(self):
        with mock.patch.object(ch, "ESPERA_TOKEN_MAX_S", 0), \
             mock.patch.object(ch.meli, "_access_token", return_value="viejo"):
            self.assertFalse(ch.esperar_token_nuevo("viejo"))

    def test_sin_token_no_cuenta_como_nuevo(self):
        with mock.patch.object(ch, "ESPERA_TOKEN_MAX_S", 0), \
             mock.patch.object(ch.meli, "_access_token", return_value=None):
            self.assertFalse(ch.esperar_token_nuevo("viejo"))


class MainReintenta(unittest.TestCase):
    def test_vuelve_a_sondear_y_guarda(self):
        vacio, lleno = {"MLM1": []}, {"MLM1": [{"p": 1, "id": "MLM9", "t": "i"}]}
        sondeos = mock.AsyncMock(side_effect=[vacio, lleno])
        guardar = mock.Mock(return_value=(1, 0))
        with mock.patch.object(ch.supabase_db, "disponible", return_value=True), \
             mock.patch.object(ch, "objetivo", return_value=["MLM1"]), \
             mock.patch.object(ch, "sondear", sondeos), \
             mock.patch.object(ch.meli, "_access_token", return_value="viejo"), \
             mock.patch.object(ch, "esperar_token_nuevo", return_value=True) as espera, \
             mock.patch.object(ch, "sano", return_value=(True, "")), \
             mock.patch.object(ch, "guardar", guardar), \
             mock.patch.object(ch, "refrescar_terminos",
                               mock.AsyncMock(return_value={"guardados": 1, "sin_terminos": 0, "no_se_pudo": 0})), \
             mock.patch.object(sys, "argv", ["competencia_highlights.py", "--real"]), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ch.main(), 0)
        self.assertEqual(sondeos.await_count, 2)
        espera.assert_called_once_with("viejo")
        guardar.assert_called_once_with(lleno)


if __name__ == "__main__":
    unittest.main()
