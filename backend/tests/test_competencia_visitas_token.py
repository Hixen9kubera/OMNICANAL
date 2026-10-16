"""Pruebas de la segunda pasada del cron de visitas (`scripts/competencia_visitas.py`, v0.658.0).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **El 401 de la renovación ya no deja a una cuenta sin visitas.** El cron
   corría a las 12:00 UTC y el token de SANCORFASHION se renueva hacia las
   12:05: desde ahí cada llamada de esa cuenta daba 401 (15-oct: 1,358 sin dato;
   16-oct: 834). Si una cuenta pierde ≥ 20 %, se espera su token nuevo —leído
   de la base SIN caché, nunca renovado por el cron— y se repiten las fallidas.
2. Fallas dispersas (429) no esperan token: se repiten de una vez.
3. Sin fallas no hay segunda pasada.
4. `esperar_tokens_nuevos` deja el token nuevo en las DOS cachés y se rinde a
   tiempo si el backend no renueva.

Sin red.

    cd backend && python -m unittest tests.test_competencia_visitas_token -v
"""
from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from scripts import competencia_visitas as cv


def _filas(cuenta: str, n: int, sin: int) -> list[dict]:
    return [{"sku": f"{cuenta}-{i}", "cuenta": cuenta, "ml_item_id": f"MLM{cuenta[:1]}{i}",
             "visitas_30d": None if i < sin else 10} for i in range(n)]


class CuentasConHueco(unittest.TestCase):
    def test_una_cuenta_que_pierde_un_quinto(self):
        filas = _filas("SANCORFASHION", 10, 3) + _filas("BEKURA", 10, 1)
        self.assertEqual(cv.cuentas_con_hueco(filas), {"SANCORFASHION"})

    def test_sin_item_id_no_cuenta(self):
        filas = [{"sku": "X", "cuenta": "BEKURA", "ml_item_id": None, "visitas_30d": None}]
        self.assertEqual(cv.cuentas_con_hueco(filas), set())


class EsperarTokensNuevos(unittest.TestCase):
    def setUp(self):
        self.dormir = mock.patch.object(cv.time, "sleep").start()
        self.recordar = mock.patch.object(cv.meli, "_recordar_token").start()
        self.olvidar = mock.patch.object(cv.competencia_ml, "_olvidar_token").start()
        self.addCleanup(mock.patch.stopall)

    def test_espera_hasta_que_el_backend_renueva(self):
        with mock.patch.object(cv.meli, "_access_token_leer", side_effect=["viejo", "viejo", "nuevo"]):
            self.assertEqual(cv.esperar_tokens_nuevos({"SANCORFASHION": "viejo"}, {"SANCORFASHION"}),
                             {"SANCORFASHION"})
        self.assertEqual(self.dormir.call_count, 2)
        self.recordar.assert_called_once_with("SANCORFASHION", "nuevo")
        self.olvidar.assert_called_with("SANCORFASHION")

    def test_si_ya_habia_cambiado_no_espera(self):
        with mock.patch.object(cv.meli, "_access_token_leer", return_value="nuevo"):
            self.assertEqual(cv.esperar_tokens_nuevos({"BEKURA": "viejo"}, {"BEKURA"}), {"BEKURA"})
        self.dormir.assert_not_called()

    def test_se_rinde_si_no_renueva(self):
        with mock.patch.object(cv, "ESPERA_TOKEN_MAX_S", 0), \
             mock.patch.object(cv.meli, "_access_token_leer", return_value="viejo"):
            self.assertEqual(cv.esperar_tokens_nuevos({"BEKURA": "viejo"}, {"BEKURA"}), set())
        self.recordar.assert_not_called()

    def test_sin_token_no_cuenta_como_nuevo(self):
        with mock.patch.object(cv, "ESPERA_TOKEN_MAX_S", 0), \
             mock.patch.object(cv.meli, "_access_token_leer", return_value=None):
            self.assertEqual(cv.esperar_tokens_nuevos({"BEKURA": "viejo"}, {"BEKURA"}), set())


class SegundaPasada(unittest.TestCase):
    def setUp(self):
        self.olvidar = mock.patch.object(cv.competencia_ml, "_olvidar_token").start()
        self.addCleanup(mock.patch.stopall)

    def _correr(self, filas, esperar_devuelve=frozenset()):
        llamadas = []

        async def medir(fs, concurrencia=cv.CONCURRENCIA):
            llamadas.append(([f["sku"] for f in fs], concurrencia))
            for f in fs:
                f["visitas_30d"] = 7
            return len(fs), 0

        esperar = mock.Mock(return_value=set(esperar_devuelve))
        with mock.patch.object(cv, "medir", medir), \
             mock.patch.object(cv, "esperar_tokens_nuevos", esperar), \
             contextlib.redirect_stdout(io.StringIO()):
            recuperadas = cv.segunda_pasada(filas, {"BEKURA": "b", "SANCORFASHION": "s"})
        return recuperadas, llamadas, esperar

    def test_hueco_de_token_espera_y_repite_solo_las_fallidas(self):
        filas = _filas("SANCORFASHION", 10, 4) + _filas("BEKURA", 10, 0)
        recuperadas, llamadas, esperar = self._correr(filas, {"SANCORFASHION"})
        self.assertEqual(recuperadas, 4)
        esperar.assert_called_once()
        self.assertEqual(esperar.call_args[0][1], {"SANCORFASHION"})
        self.assertEqual(len(llamadas), 1)
        self.assertEqual(sorted(llamadas[0][0]), [f"SANCORFASHION-{i}" for i in range(4)])
        self.assertEqual(llamadas[0][1], cv.CONCURRENCIA_REINTENTO)
        self.assertTrue(all(f["visitas_30d"] == 7 for f in filas[:4]))

    def test_fallas_dispersas_se_repiten_sin_esperar(self):
        filas = _filas("SANCORFASHION", 20, 1) + _filas("BEKURA", 20, 1)
        recuperadas, llamadas, esperar = self._correr(filas)
        self.assertEqual(recuperadas, 2)
        esperar.assert_not_called()
        self.assertEqual(len(llamadas[0][0]), 2)

    def test_sin_fallas_no_hay_segunda_pasada(self):
        filas = _filas("BEKURA", 5, 0)
        recuperadas, llamadas, esperar = self._correr(filas)
        self.assertEqual((recuperadas, llamadas), (0, []))
        esperar.assert_not_called()


class Horario(unittest.TestCase):
    def test_el_cron_corre_despues_de_la_renovacion(self):
        import json
        from pathlib import Path
        cfg = json.loads((Path(cv.__file__).resolve().parents[1] / "railway.competencia-visitas.json").read_text())
        self.assertEqual(cfg["deploy"]["cronSchedule"], "30 12 * * *")


if __name__ == "__main__":
    unittest.main()
