"""Pruebas del RECUPERADOR del fan-out (`fanout_recuperar.revisar` y su job).

── QUÉ FIJAN ────────────────────────────────────────────
La cola del fan-out vive en memoria; el 29-sep un despliegue a media cola tiró 69
de 72 cambios y nadie los volvió a repartir. El recuperador busca en la bitácora
los cambios sin reparto y los reencola. Tiene que ser aburrido:

  1. EXISTE SOLO CON SUS DOS BANDERAS (la suya y la del fan-out), nace apagado y
     su trabajo va en `asyncio.to_thread` (regla 11: lee la base).
  2. NO HACE NADA si el fan-out está apagado o si la bitácora no se escribe en
     kubera (sin ella, todo cambio parecería sin repartir).
  3. REENCOLA UNA SOLA VEZ cada cambio (sku, ts): si su evento nunca llega a la
     bitácora, no entra en bucle. Un cambio NUEVO del mismo SKU sí vuelve a entrar.
  4. SE SALTA lo que ya está en la cola.
  5. AL ARRANCAR la gracia baja a 3 min (la cola está recién vacía).

── NO SE TOCA LA BASE ───────────────────────────────────
`candidatos` va suplantado y `encolar_varios` también. Los SKUs son INVENTADOS:
el repo es público.

    cd backend && python -m unittest tests.test_fanout_recuperar -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import fanout_recuperar as R  # noqa: E402
from services import fanout_stock as F  # noqa: E402

AJUSTES = {"fanout_recuperar_enabled": True, "fanout_recuperar_min": 10, "fanout_recuperar_horas": 6,
           "fanout_recuperar_gracia_min": 15, "fanout_recuperar_tope": 300,
           "supabase_write_fanout_log": True}
HACE = datetime.now(timezone.utc) - timedelta(minutes=40)


def _fila(sku: str, ts: datetime = HACE) -> dict:
    return {"sku": sku, "ts": ts}


class Vuelta(unittest.TestCase):
    def setUp(self):
        R._ya.clear()
        R._ultimo.clear()
        self.encolados: list[tuple[list[str], str]] = []
        self.parches = [
            mock.patch.multiple(R.settings, **AJUSTES),
            mock.patch.object(F, "habilitado", return_value=True),
            mock.patch.object(F, "encolar_varios", side_effect=lambda s, motivo: self.encolados.append((list(s), motivo))),
            mock.patch.dict(F._pendientes, {}, clear=True),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in reversed(self.parches):
            p.stop()

    def _correr(self, filas, **kw):
        with mock.patch.object(R, "candidatos", return_value=filas) as cand:
            r = R.revisar(**kw)
        return r, cand

    def test_reencola_lo_que_nunca_se_repartio_con_su_motivo(self):
        r, _ = self._correr([_fila("SKU-UNO"), _fila("SKU-DOS")])
        self.assertTrue(r["ok"])
        self.assertEqual(self.encolados, [(["SKU-UNO", "SKU-DOS"], R.MOTIVO)])
        self.assertEqual((r["candidatos"], r["encolados"]), (2, 2))

    def test_el_mismo_cambio_no_se_reencola_dos_veces(self):
        self._correr([_fila("SKU-UNO")])
        r, _ = self._correr([_fila("SKU-UNO")])
        self.assertEqual(r["encolados"], 0, "si su evento no llegó a la bitácora, no entra en bucle")
        self.assertEqual(len(self.encolados), 1)

    def test_un_cambio_nuevo_del_mismo_sku_si_vuelve_a_entrar(self):
        self._correr([_fila("SKU-UNO")])
        r, _ = self._correr([_fila("SKU-UNO", HACE + timedelta(minutes=20))])
        self.assertEqual(r["encolados"], 1)

    def test_se_salta_lo_que_ya_esta_en_la_cola(self):
        F._pendientes["SKU-UNO"] = {"listo_en": 0, "motivo": "venta"}
        r, _ = self._correr([_fila("SKU-UNO"), _fila("SKU-DOS")])
        self.assertEqual(self.encolados, [(["SKU-DOS"], R.MOTIVO)])
        self.assertEqual(r["encolados"], 1)

    def test_sin_candidatos_no_encola_nada(self):
        r, _ = self._correr([])
        self.assertTrue(r["ok"])
        self.assertEqual(self.encolados, [])

    def test_gracia_normal_y_de_arranque(self):
        _, cand = self._correr([])
        self.assertEqual(cand.call_args.args, (6, 15, 300))
        _, cand = self._correr([], arranque=True)
        self.assertEqual(cand.call_args.args, (6, R.GRACIA_ARRANQUE_MIN, 300),
                         "al arrancar la cola está vacía: recupera hasta lo de hace 3 min")

    def test_con_la_bandera_apagada_no_hace_nada(self):
        with mock.patch.object(R.settings, "fanout_recuperar_enabled", False):
            r, cand = self._correr([_fila("SKU-UNO")])
        self.assertFalse(r["ok"])
        cand.assert_not_called()
        self.assertEqual(self.encolados, [])

    def test_con_el_fanout_apagado_no_hace_nada(self):
        with mock.patch.object(F, "habilitado", return_value=False):
            r, cand = self._correr([_fila("SKU-UNO")])
        self.assertFalse(r["ok"])
        cand.assert_not_called()

    def test_sin_bitacora_en_kubera_no_hace_nada(self):
        with mock.patch.object(R.settings, "supabase_write_fanout_log", False):
            r, cand = self._correr([_fila("SKU-UNO")])
        self.assertFalse(r["ok"])
        self.assertIn("bitácora", r["motivo"])
        cand.assert_not_called()

    def test_estado_dice_la_ultima_vuelta(self):
        self._correr([_fila("SKU-UNO")])
        est = R.estado()
        self.assertTrue(est["habilitado"])
        self.assertEqual(est["ultima_vuelta"]["encolados"], 1)
        self.assertEqual(est["ultima_vuelta"]["muestra"], ["SKU-UNO"])


class _SchedFalso:
    """AsyncIOScheduler de mentira: apunta los add_job y no arranca nada."""

    def __init__(self, *a, **k):
        self.jobs: list[tuple[tuple, dict]] = []

    def add_job(self, *a, **k):
        self.jobs.append((a, k))

    def start(self):
        pass


class JobDelScheduler(unittest.TestCase):
    def _jobs(self, recuperar: bool, fanout: bool = True, **ajustes):
        from services import scheduler as sch
        ajustes = {"fanout_recuperar_enabled": recuperar, "fanout_enabled": fanout,
                   "fanout_recuperar_min": 10, **ajustes}
        viejo = sch._scheduler
        sch._scheduler = None
        try:
            with mock.patch.object(sch, "AsyncIOScheduler", _SchedFalso), \
                    mock.patch.multiple(sch.settings, **ajustes):
                sch.iniciar()
                return {k.get("id"): (a, k) for a, k in sch._scheduler.jobs}
        finally:
            sch._scheduler = viejo

    def test_solo_con_sus_dos_banderas(self):
        self.assertNotIn("fanout_recuperar", self._jobs(False))
        self.assertNotIn("fanout_recuperar", self._jobs(True, fanout=False))
        jobs = self._jobs(True)
        a, k = jobs["fanout_recuperar"]
        self.assertEqual(a[1], "interval")
        self.assertEqual(k["minutes"], 10)
        self.assertEqual((k["max_instances"], k["coalesce"]), (1, True))
        self.assertIsNotNone(k["next_run_time"].tzinfo, "con zona: el scheduler va en UTC")
        a, k = jobs["fanout_recuperar_arranque"]
        self.assertEqual(a[1], "date")
        self.assertEqual(k["kwargs"], {"arranque": True})
        gracia = (k["run_date"] - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(100 < gracia <= 120, gracia)

    def test_el_intervalo_tiene_piso(self):
        _, k = self._jobs(True, fanout_recuperar_min=0)["fanout_recuperar"]
        self.assertEqual(k["minutes"], 5, "un 0 no vuelve el barrido un bucle")

    def test_la_bandera_nace_apagada(self):
        campos = type(R.settings).model_fields
        self.assertIs(campos["fanout_recuperar_enabled"].default, False)
        self.assertEqual(campos["fanout_recuperar_horas"].default, 6)
        self.assertEqual(campos["fanout_recuperar_gracia_min"].default, 15)

    def test_el_trabajo_va_en_un_hilo(self):
        a, _ = self._jobs(True)["fanout_recuperar"]
        job = a[0]
        from services import scheduler as sch
        llamado = mock.AsyncMock()
        with mock.patch.object(sch.asyncio, "to_thread", llamado):
            asyncio.run(job(arranque=True))
        llamado.assert_awaited_once()
        self.assertIs(llamado.await_args.args[0], R.revisar)
        self.assertEqual(llamado.await_args.args[1], True)


if __name__ == "__main__":
    unittest.main()
