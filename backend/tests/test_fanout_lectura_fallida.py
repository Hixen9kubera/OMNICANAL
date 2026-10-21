"""Pruebas de la LECTURA FALLIDA del fan-out (`fanout_stock._procesar`).

── EL DEFECTO ───────────────────────────────────────────
Con `FANOUT_FUENTE=kubera`, si el libro no se puede leer `stock_libro` lanza
`LibroNoDisponible` (falla cerrada: nunca un 0 inventado). `_procesar` la atrapaba
como cualquier error: un WARNING y el SKU SOLTADO. El cambio no llegaba a ningún canal,
la bitácora no decía nada y el recuperador no tenía de dónde saberlo.

── QUÉ FIJAN ────────────────────────────────────────────
  1. LA EXCEPCIÓN LLEGA: de `stock_libro.desglose` a `plan` sin que nadie la vuelva None.
  2. SE ANOTA: `lectura_fallida` en la bitácora (canal '*', el error recortado y sin
     secretos en `resultado`, el motivo original en `motivo`), a lo más una por SKU
     cada 10 min.
  3. VUELVE A LA COLA: espera creciente 60 s → 15 min tope, UNA entrada por SKU (si
     llegó otra vez mientras se procesaba, se queda una con la espera más larga), con
     su `desde` original; una lectura que sale borra los reintentos.
  4. AVISA: tras 3 fallas seguidas (de cualquier SKU) un aviso por cambio de estado, y
     otro cuando una lectura vuelve a salir; un éxito en medio corta la racha.
  5. LO DEMÁS NO CAMBIA: otro error se cuenta y se suelta, como siempre.
  6. CON WOO TAMBIÉN: cualquier falla de `plan` (la MySQL de WordPress que parpadea,
     kubera que no contesta al buscar destinos) es una lectura fallida —nada se
     escribió—: sube como `LecturaFallida`, se anota «lectura del número fallida» y se
     reintenta. Lo que falla DESPUÉS de leer se suelta como siempre y la lectura cuenta
     como buena. (`ConWoo`)

── NO SE TOCA NADA REAL ─────────────────────────────────
La base, Woo y la red revientan si se tocan (las trampas de `test_fanout_seguro`);
la bitácora y las alertas son falsas. Los SKUs son INVENTADOS: el repo es público.

    cd backend && python -m unittest tests.test_fanout_lectura_fallida -v
"""
from __future__ import annotations

import inspect
import sys
import time
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import alertas  # noqa: E402
from services import fanout_stock as F  # noqa: E402
from services import stock_libro  # noqa: E402
from services.stock_libro import LibroNoDisponible  # noqa: E402
from tests import test_fanout_seguro as _TS  # noqa: E402

setUpModule = _TS.setUpModule
tearDownModule = _TS.tearDownModule

SKU = "PRB-0401-NEG"
MOTIVO = "cambio de vendible en kubera (PRB) 5 -> 4"


class Base(unittest.TestCase):
    def setUp(self):
        self.eventos: list[dict] = []
        self.avisos: list[tuple] = []
        self.parches = [
            mock.patch.dict(F._pendientes, {}, clear=True),
            mock.patch.dict(F._en_proceso, {}, clear=True),
            mock.patch.object(F, "_duraciones", deque(maxlen=40)),
            mock.patch.dict(F._contadores, {k: 0 for k in F._contadores}),
            mock.patch.dict(F._lectura_intentos, {}, clear=True),
            mock.patch.dict(F._lectura_anotada, {}, clear=True),
            mock.patch.dict(F._lectura, {"racha": 0, "avisado": False, "ultima": None}),
            mock.patch.object(F, "habilitado", return_value=True),
            mock.patch.object(F, "_persistir", side_effect=self.eventos.append),
            mock.patch.object(alertas, "avisar_estado",
                              side_effect=lambda *a, **k: self.avisos.append((a, k)) or True),
        ]
        for p in self.parches:
            p.start()
            self.addCleanup(p.stop)

    def procesar(self, sku: str = SKU, exc: Exception | None = None, motivo: str = MOTIVO,
                 desde: float | None = None) -> None:
        """Lo que hace el worker: lo pone en proceso y llama `_procesar`."""
        F._en_proceso[sku] = {"motivo": motivo, "desde": desde or time.time(), "inicio": time.time(), "hilo": 0}
        efecto = exc if exc is not None else None
        with mock.patch.object(F, "_aplicar", side_effect=efecto) as aplicar:
            F._procesar(sku, motivo)
        aplicar.assert_called_once_with(sku, motivo)

    def falla(self, sku: str = SKU, msg: str = "no se pudo leer el libro: la base no contesta", **kw):
        self.procesar(sku, LibroNoDisponible(msg), **kw)

    def espera(self, sku: str = SKU) -> float:
        return F._pendientes[sku]["listo_en"] - time.time()


class Propaga(unittest.TestCase):
    def test_de_desglose_a_plan_sin_volverse_none(self):
        with mock.patch.object(F.settings, "fanout_fuente", "kubera"), \
                mock.patch.object(stock_libro, "desglose", side_effect=LibroNoDisponible("vista ausente")):
            with self.assertRaises(LibroNoDisponible):
                F.plan(SKU)

    def test_una_errata_en_la_fuente_tambien_llega(self):
        with mock.patch.object(F.settings, "fanout_fuente", "kubra"):
            with self.assertRaises(LibroNoDisponible):
                F.plan(SKU)


class Anota(Base):
    def test_anota_lectura_fallida_y_no_la_cuenta_como_error_de_canal(self):
        self.falla()
        self.assertEqual(len(self.eventos), 1)
        ev = self.eventos[0]
        self.assertEqual(ev["motivo"], MOTIVO, "el motivo original: la matriz arma el origen con él")
        self.assertEqual(len(ev["acciones"]), 1)
        a = ev["acciones"][0]
        self.assertEqual((a["canal"], a["accion"]), ("*", "lectura_fallida"))
        self.assertIn("la base no contesta", a["resultado"])
        self.assertIn("reintento en 60 s", a["resultado"])
        self.assertLessEqual(len(a["resultado"]), 255)
        self.assertEqual((F._contadores["lecturas_fallidas"], F._contadores["errores"]), (1, 0))
        self.assertNotIn(SKU, F._en_proceso)

    def test_una_por_sku_cada_10_min(self):
        self.falla()
        self.falla()
        self.assertEqual(len(self.eventos), 1, "la segunda falla en 10 min no anota otra fila")
        self.falla("PRB-0402-ROJ")
        self.assertEqual(len(self.eventos), 2, "otro SKU sí anota la suya")
        F._lectura_anotada[SKU] -= F._LECTURA_ANOTA_S + 1
        self.falla()
        self.assertEqual(len(self.eventos), 3, "pasados 10 min, otra")

    def test_el_error_va_recortado_y_sin_secretos(self):
        msg = ("no se pudo leer el libro: connection to postgresql://postgres.abc:SuperClave@db.x:6543/postgres "
               "failed password=OtraClave " + "x" * 500)
        self.falla(msg=msg)
        r = self.eventos[0]["acciones"][0]["resultado"]
        self.assertNotIn("SuperClave", r)
        self.assertNotIn("OtraClave", r)
        self.assertIn("<url>", r)
        self.assertLessEqual(len(r), 255)

    def test_si_la_bitacora_revienta_el_worker_sigue(self):
        with mock.patch.object(F, "_persistir", side_effect=RuntimeError("kubera caída")):
            self.falla()
        self.assertIn(SKU, F._pendientes, "igual vuelve a la cola")


class Reencola(Base):
    def test_vuelve_a_la_cola_con_su_motivo_y_su_desde(self):
        desde = time.time() - 300
        self.falla(desde=desde)
        info = F._pendientes[SKU]
        self.assertEqual(info["motivo"], MOTIVO)
        self.assertAlmostEqual(info["desde"], desde, delta=0.01)
        self.assertAlmostEqual(self.espera(), 60, delta=2)

    def test_espera_creciente_con_tope_de_15_min(self):
        vistas = []
        for _ in range(7):
            F._pendientes.pop(SKU, None)          # el worker lo toma otra vez
            self.falla()
            vistas.append(round(self.espera() / 10) * 10)
        self.assertEqual(vistas, [60, 120, 240, 480, 900, 900, 900])

    def test_una_lectura_que_sale_borra_los_reintentos(self):
        self.falla()
        self.falla()
        F._pendientes.pop(SKU, None)
        self.procesar()                           # sale bien
        self.assertNotIn(SKU, F._lectura_intentos)
        self.falla()
        self.assertAlmostEqual(self.espera(), 60, delta=2, msg="empieza otra vez en 60 s")

    def test_si_llego_otra_vez_mientras_se_procesaba_queda_una_entrada(self):
        F._en_proceso[SKU] = {"motivo": MOTIVO, "desde": time.time() - 50, "inicio": time.time(), "hilo": 0}
        # Una venta lo vuelve a encolar mientras está en proceso (debounce de 5 s).
        F._pendientes[SKU] = {"listo_en": time.time() + 5, "motivo": "venta ML", "desde": time.time()}
        with mock.patch.object(F, "_aplicar", side_effect=LibroNoDisponible("caída")):
            F._procesar(SKU, MOTIVO)
        self.assertEqual(list(F._pendientes), [SKU])
        info = F._pendientes[SKU]
        self.assertEqual(info["motivo"], "venta ML", "el motivo más nuevo se queda")
        self.assertAlmostEqual(info["listo_en"] - time.time(), 60, delta=2, msg="la espera más larga")
        self.assertLess(info["desde"], time.time() - 40, "el desde más viejo")

    def test_nunca_queda_fuera_de_la_cola(self):
        # Sale de `_en_proceso` y entra a `_pendientes` en el MISMO bloque bajo `_lock`.
        fuente = inspect.getsource(F._procesar)
        bloque = fuente[fuente.index("with _lock:"):]
        self.assertLess(bloque.index("_en_proceso.pop"), bloque.index("_reencolar_lectura"))
        self.assertNotIn("\n    if fallo", bloque[:bloque.index("_reencolar_lectura")])

    def test_con_el_fanout_apagado_no_reencola(self):
        with mock.patch.object(F, "habilitado", return_value=False):
            self.falla()
        self.assertNotIn(SKU, F._pendientes)

    def test_otro_error_se_suelta_como_siempre(self):
        self.procesar(exc=RuntimeError("Temu 500"))
        self.assertNotIn(SKU, F._pendientes)
        self.assertEqual((F._contadores["errores"], F._contadores["lecturas_fallidas"]), (1, 0))
        self.assertEqual(self.eventos, [])


class ConWoo(Base):
    """`_aplicar` de verdad, con `plan` suplantado."""

    def procesar_real(self, sku: str = SKU, **parches) -> None:
        F._en_proceso[sku] = {"motivo": MOTIVO, "desde": time.time(), "inicio": time.time(), "hilo": 0}
        with mock.patch.multiple(F, **parches):
            F._procesar(sku, MOTIVO)

    def test_una_falla_de_plan_sube_como_lectura_fallida(self):
        with mock.patch.object(F, "plan", side_effect=RuntimeError("MySQL de WordPress: Lost connection")):
            with self.assertRaises(F.LecturaFallida) as cm:
                F._aplicar(SKU, MOTIVO)
        self.assertIn("RuntimeError: MySQL de WordPress", str(cm.exception))
        with mock.patch.object(F, "plan", side_effect=LibroNoDisponible("caída")):
            with self.assertRaises(LibroNoDisponible):
                F._aplicar(SKU, MOTIVO)

    def test_se_anota_y_vuelve_a_la_cola(self):
        self.procesar_real(plan=mock.Mock(side_effect=RuntimeError("Lost connection")))
        self.assertIn(SKU, F._pendientes, "una bajada por una venta no se suelta")
        self.assertAlmostEqual(self.espera(), 60, delta=2)
        (ev,) = self.eventos
        self.assertEqual(ev["acciones"][0]["accion"], "lectura_fallida")
        self.assertTrue(ev["acciones"][0]["resultado"].startswith("lectura del número fallida (reintento en 60 s)"))
        self.assertEqual((F._contadores["lecturas_fallidas"], F._contadores["errores"]), (1, 0))

    def test_la_del_libro_se_sigue_llamando_del_libro(self):
        self.falla()
        self.assertTrue(self.eventos[0]["acciones"][0]["resultado"].startswith("lectura del libro fallida"))

    def test_lo_que_falla_despues_de_leer_se_suelta_y_la_lectura_cuenta_como_buena(self):
        self.falla()
        F._pendientes.pop(SKU, None)
        plan = mock.Mock(return_value={"sku": SKU, "ok": True, "acciones": [], "stock_drop": 3, "objetivo": 3})
        self.procesar_real(plan=plan, _seguro=mock.Mock(side_effect=RuntimeError("seguro roto")))
        self.assertNotIn(SKU, F._pendientes)
        self.assertEqual(F._contadores["errores"], 1)
        self.assertNotIn(SKU, F._lectura_intentos, "no queda «esperando reintento»")
        self.assertEqual(F.lectura_estado()["racha"], 0)


class Avisa(Base):
    def _tipos(self) -> list[tuple[str, str]]:
        return [(a[0], a[1]) for a, _k in self.avisos]

    def test_tras_tres_fallas_seguidas_y_una_sola_vez(self):
        self.falla("PRB-0001-AZL")
        self.falla("PRB-0002-AZL")
        self.assertEqual(self.avisos, [], "dos pueden ser un parpadeo")
        self.falla("PRB-0003-AZL")
        self.assertEqual(self._tipos(), [("fanout_lectura_fallida", "fallando")])
        self.assertIn("PRB-0003-AZL", self.avisos[0][0][2])
        self.falla("PRB-0004-AZL")
        self.assertEqual(len(self.avisos), 1, "mientras siga cayendo no se repite")

    def test_avisa_cuando_vuelve(self):
        for i in range(3):
            self.falla(f"PRB-000{i}-AZL")
        self.procesar("PRB-0009-AZL")
        self.assertEqual(self._tipos(), [("fanout_lectura_fallida", "fallando"), ("fanout_lectura_fallida", "ok")])
        self.procesar("PRB-0010-AZL")
        self.assertEqual(len(self.avisos), 2, "un éxito sin aviso previo no toca alertas")

    def test_un_exito_en_medio_corta_la_racha(self):
        self.falla("PRB-0001-AZL")
        self.falla("PRB-0002-AZL")
        self.procesar("PRB-0003-AZL")
        self.falla("PRB-0004-AZL")
        self.falla("PRB-0005-AZL")
        self.assertEqual(self.avisos, [])
        self.assertEqual(F.lectura_estado()["racha"], 2)

    def test_el_estado_lo_dice(self):
        self.falla()
        est = F.lectura_estado()
        self.assertEqual((est["racha"], est["en_reintento"]), (1, 1))
        self.assertEqual(est["ultima"]["sku"], SKU)

    def test_todo_es_sincrono_en_el_hilo_del_worker(self):
        # Regla 11: nada de esto corre en el loop (lo llaman los hilos del worker).
        for f in (F._procesar, F._lectura_fallida, F._lectura_ok, F._reencolar_lectura):
            self.assertFalse(inspect.iscoroutinefunction(f), f.__name__)


if __name__ == "__main__":
    unittest.main()
