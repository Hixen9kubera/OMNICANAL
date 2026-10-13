"""Pruebas de la COLA RÁPIDA del fan-out (`fanout_stock`, «Cola con debounce»):
FANOUT_PRIORIDAD, FANOUT_TEMU_VERIFICAR_DIFERIDO y FANOUT_WORKERS, y la matriz que
distingue «en cola» de «cambio perdido» mirando la cola real.

── EL DEFECTO (medido el 12-oct-2026 en producción) ─────────────
La venta S41906 de Odoo apartó 183 SKUs en una pasada de stock_watch. Un solo hilo
los repartió en orden alfabético a 5.5 SKUs/min (Temu: 8 s de sueño + relectura en
cada escritura), con las bajadas a la venta esperando su letra (1,444 piezas×min
ofrecidas de más), el lote invisible para el panel y la matriz rotulando «cambio
perdido» a 50 de 133 SKUs que sólo esperaban su turno.

── QUÉ FIJAN ────────────────────────────────────────────
  1. LAS TRES APAGADAS = HOY: el lote en orden de llegada y en serie, Temu duerme y
     relee, el escritor se llama igual, el recuperador sólo mira lo pendiente, el
     freno no frena y no se clasifica nada. Lo único nuevo es que el lote SE VE.
  2. VARIOS HILOS: nunca dos eventos del mismo SKU a la vez, y en orden; una
     publicación compartida no se escribe desde dos hilos a la vez; el freno por canal
     espacia las llamadas; un error de un SKU no tumba la cola.
  3. PRIORIDAD: bajadas a la venta antes que otras bajadas antes que subidas; lo que
     pasa del umbral de espera toma uno de cada dos turnos (nunca espera para siempre,
     y una venta que llega a media ráfaga no queda detrás de todo lo viejo); nada se
     toma antes de reposar; si la clasificación revienta, todo va parejo y la cola
     avanza; `_clasificar` decide con el valor más nuevo y descarta FULL.
  4. TEMU DIFERIDO: la cola no duerme; el verificador confirma sin anotar, corrige y
     anota `verif_temu`, y tras 3 escrituras anota ERROR; un segundo evento del mismo
     goods no escribe otro diff; con marca del seguro y objetivo > 0 sigue síncrono;
     los excedentes y los rancios siguen síncronos y esperan la convergencia —también
     si se registra una entre la espera y el candado—, y si la espera vence, la
     escritura síncrona descarta el objetivo viejo (el verificador no la deshace); el
     verificador no cierra mientras el evento que la registró no está en la bitácora;
     al reiniciar se recuperan con la bandera apagada y sin recuperador; con hilos
     REALES, cola + verificador + excedentes sobre los mismos goods terminan en su
     objetivo.
  5. LA MATRIZ: «repartiendo ahora», «en cola (posición N, ~M min)», «en camino» y
     «cambio perdido» según la cola REAL; «en camino» por la pasada de stock_watch
     sólo para lo que anotó ESA pasada; los perdidos no incluyen lo que está en cola;
     «Cola N» cuenta SKUs (el lote y lo que está en proceso, una vez cada uno); los SQL
     toman la `verif_temu`.

── NO SE TOCA NADA REAL ─────────────────────────────────
La base, Woo y la red revientan si se tocan (las trampas de `test_fanout_seguro`);
los canales y la bitácora son falsos. Los SKUs son INVENTADOS: el repo es público.

    cd backend && python -m unittest tests.test_fanout_cola -v
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import threading
import time
import unittest
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import fanout_recuperar as R  # noqa: E402
from services import fanout_seguro as S  # noqa: E402
from services import fanout_stock as F  # noqa: E402
from services import fanout_vivo as V  # noqa: E402
from services import stock_watch as SW  # noqa: E402
from services import supabase_db as sdb  # noqa: E402
from services import temu as TM  # noqa: E402
from tests import test_fanout_seguro as _TS  # noqa: E402

setUpModule = _TS.setUpModule
tearDownModule = _TS.tearDownModule

G = "900077"          # goodsId inventado
ML = "MLM0000077"
APAGADAS = dict(fanout_prioridad=False, fanout_workers=1, fanout_temu_verificar_diferido=False,
                fanout_prioridad_envejecer_min=15.0, fanout_ritmo_temu=45.0,
                fanout_ritmo_tiktok=33.0, fanout_ritmo_ml=67.0)


class Cola(unittest.TestCase):
    """La cola aislada: `_pendientes`, `_lote`, `_en_proceso` y `_duraciones` propios."""

    AJUSTES: dict = {}

    def setUp(self):
        self.parches = [
            mock.patch.multiple(F.settings, **{**APAGADAS, **self.AJUSTES}),
            mock.patch.dict(F._pendientes, {}, clear=True),
            mock.patch.dict(F._en_proceso, {}, clear=True),
            mock.patch.object(F, "_lote", []),
            mock.patch.object(F, "_duraciones", deque(maxlen=40)),
            mock.patch.dict(F._contadores, {k: 0 for k in F._contadores}),
            mock.patch.dict(F._alterna, {"vieja": False}),
            mock.patch.object(F, "_asegurar_worker"),
            mock.patch.object(F, "habilitado", return_value=True),
            mock.patch.object(F, "seguro_encendido", return_value=False),
        ]
        for p in self.parches:
            p.start()
            self.addCleanup(p.stop)

    def pendiente(self, sku: str, motivo: str = "venta", hace_s: float = 10.0, listo: bool = True):
        ahora = time.time()
        F._pendientes[sku] = {"listo_en": ahora - 1 if listo else ahora + 60,
                              "motivo": motivo, "desde": ahora - hace_s}

    def _marcas(self) -> list[str]:
        """Lo que `fanout_seguro._reencolar_marcas` devuelve a la cola con el SKU en el lote."""
        F._lote.append(("ZZZ-0090-NEG", "venta", time.time()))
        marcas = [{"sku": "ZZZ-0090-NEG", "item_id": "77", "stock_woo": 3, "huerfana": False}]
        with mock.patch.dict(S._esperas, {}, clear=True), mock.patch.dict(S._reencoladas, {}, clear=True), \
                mock.patch.object(F, "encolar"):
            return S._reencolar_marcas("tiktok", marcas)


# ══════════════════════════════════════════════════════════════════════════════

class Apagadas(Cola):
    """1 · Con las tres banderas en su valor por omisión, la cola es la de siempre."""

    def test_el_lote_en_orden_de_llegada_y_en_serie_y_ahora_se_ve(self):
        for s in ("ZZZ-0003-NEG", "ZZZ-0001-NEG", "ZZZ-0002-NEG"):
            self.pendiente(s, motivo=f"m {s}")
        vistos, fotos = [], []

        def aplicar(sku, motivo):
            vistos.append((sku, motivo))
            fotos.append([(e["sku"], e["estado"], e["posicion"]) for e in F.cola()])

        with mock.patch.object(F, "_aplicar", side_effect=aplicar), \
                mock.patch.object(F, "_clasificar", side_effect=AssertionError("apagada no clasifica")), \
                mock.patch.object(F.time, "sleep", side_effect=[None, SystemExit]):
            with self.assertRaises(SystemExit):
                F._worker()
        self.assertEqual(vistos, [("ZZZ-0003-NEG", "m ZZZ-0003-NEG"), ("ZZZ-0001-NEG", "m ZZZ-0001-NEG"),
                                  ("ZZZ-0002-NEG", "m ZZZ-0002-NEG")])
        # Antes el lote vivía en una lista local: ahora lo ven la matriz y el recuperador.
        self.assertEqual(fotos[0], [("ZZZ-0003-NEG", "proceso", 0), ("ZZZ-0001-NEG", "lote", 1),
                                    ("ZZZ-0002-NEG", "lote", 2)])
        self.assertEqual(F.en_cola(), set())
        self.assertEqual((F._lote, F._en_proceso, len(F._duraciones)), ([], {}, 3))

    def test_lo_que_llega_durante_el_lote_espera_como_siempre(self):
        self.pendiente("ZZZ-0001-NEG")
        self.pendiente("ZZZ-0002-NEG")
        vistos = []

        def aplicar(sku, motivo):
            vistos.append(sku)
            if sku == "ZZZ-0001-NEG":
                self.pendiente("ZZZ-0000-VTA", motivo="venta")    # llega una venta a media ráfaga
        with mock.patch.object(F, "_aplicar", side_effect=aplicar), \
                mock.patch.object(F.time, "sleep", side_effect=[None, None, SystemExit]):
            with self.assertRaises(SystemExit):
                F._worker()
        self.assertEqual(vistos, ["ZZZ-0001-NEG", "ZZZ-0002-NEG", "ZZZ-0000-VTA"])

    def test_aplicar_llama_a_los_escritores_como_siempre(self):
        llamadas = []

        def escritor(canal):
            def f(*a, **k):
                llamadas.append((canal, a, k))
                return True, "ok (3→1)"
            return f
        p = {"sku": "ZZZ-0004-NEG", "ok": True, "stock_drop": 1, "reserva": 0, "objetivo": 1,
             "acciones": [{"canal": c, "cuenta": "X", "item_id": i, "stock_actual_canal": 3,
                           "objetivo": 1, "accion": "escribir", "omitido_por": None}
                          for c, i in (("temu", G), ("tiktok", "77"), ("mercado_libre", ML))]}
        guardados = []
        with mock.patch.object(F, "plan", return_value=p), \
                mock.patch.object(F, "dry_run", return_value=False), \
                mock.patch.object(F, "_persistir", side_effect=guardados.append), \
                mock.patch.dict(F._ESCRITORES, {c: escritor(c) for c in ("temu", "tiktok", "mercado_libre")}):
            F._aplicar("ZZZ-0004-NEG", "venta")
        self.assertEqual(llamadas, [("temu", ("X", G, 1), {}), ("tiktok", ("X", "77", 1), {}),
                                    ("mercado_libre", ("X", ML, 1), {})])
        self.assertEqual([a["resultado"] for a in guardados[0]["acciones"]], ["ok", "ok", "ok"])

    def test_temu_sigue_durmiendo_y_releyendo(self):
        vivo = {"q": 5}

        async def llamar(tipo, datos):
            if tipo == "bg.local.goods.list.query":
                return {"goodsList": [{"goodsId": int(G), "quantity": vivo["q"], "skuIdList": [1]}]}
            vivo["q"] += datos["skuStockChangeList"][0]["stockDiff"]
            return {"operateResult": True, "skuStockEditStatusInfoList": [{"stockEditStatus": True}]}
        with mock.patch.object(TM, "llamar", side_effect=llamar), \
                mock.patch.object(TM, "disponible", return_value=True), \
                mock.patch.dict(F._temu_escrito_en, {}, clear=True), \
                mock.patch.object(F, "_temu_diferir", side_effect=AssertionError("apagada no difiere")), \
                mock.patch("time.sleep") as dormir:
            self.assertEqual(F._escribir_temu("TEMU", G, 3), (True, "ok (5→3)"))
        dormir.assert_called_once_with(F._TEMU_ESPERA_S)
        self.assertEqual(F._temu_conv, {})

    def test_encolar_conserva_desde_y_reinicia_el_debounce(self):
        with mock.patch.object(F.time, "time", return_value=1000.0):
            F.encolar("ZZZ-0005-NEG", "venta A")
        with mock.patch.object(F.time, "time", return_value=1003.0):
            F.encolar("ZZZ-0005-NEG", "venta B")
        self.assertEqual(F._pendientes["ZZZ-0005-NEG"],
                         {"listo_en": 1003.0 + F.DEBOUNCE_S, "motivo": "venta B", "desde": 1000.0})

    def test_el_freno_no_frena(self):
        with mock.patch.object(F.time, "sleep") as dormir:
            for _ in range(5):
                F._ritmo("temu")
                F._ritmo("mercado_libre")
        dormir.assert_not_called()
        self.assertFalse(F.cola_rapida())

    def test_el_recuperador_solo_mira_lo_pendiente(self):
        # Hoy: lo que está en el lote se reencola (el 12-oct duplicó 5). Apagadas, igual.
        F._lote.append(("ZZZ-0006-NEG", "delta de Odoo aplicado", time.time()))
        encolados = self._recuperar(["ZZZ-0006-NEG"])
        self.assertEqual(encolados, [["ZZZ-0006-NEG"]])

    def _recuperar(self, skus, diferidas=None):
        encolados = []

        def encolar_varios(s, motivo):
            encolados.append(list(s) if motivo == R.MOTIVO else (list(s), motivo))
        filas = [{"sku": s, "ts": datetime.now(timezone.utc) - timedelta(minutes=40)} for s in skus]
        with mock.patch.multiple(R.settings, fanout_recuperar_enabled=True, fanout_recuperar_horas=6,
                                 fanout_recuperar_gracia_min=15, fanout_recuperar_tope=300,
                                 supabase_write_fanout_log=True), \
                mock.patch.object(R, "candidatos", return_value=filas), \
                mock.patch.object(R, "diferidas_recientes", return_value=diferidas or []) as dif, \
                mock.patch.object(F, "encolar_varios", side_effect=encolar_varios), \
                mock.patch.dict(R._ya, {}, clear=True):
            R.revisar(arranque=True)
        dif.assert_called_once()
        return encolados

    def test_al_reiniciar_con_la_bandera_apagada_recupera_las_diferidas(self):
        # La reversa de FANOUT_TEMU_VERIFICAR_DIFERIDO es ponerla en false: eso REINICIA el
        # contenedor y las verificaciones a medias vivían en su memoria. Antes sólo se
        # recuperaban con la bandera encendida al arrancar (revisión del 12-oct).
        self.assertFalse(F._temu_diferido())
        self.assertEqual(self._recuperar([], diferidas=["ZZZ-0077-NEG"]),
                         [(["ZZZ-0077-NEG"], R.MOTIVO_DIFERIDA)])

    def test_sin_recuperador_el_scheduler_las_recupera_igual(self):
        from services import scheduler as sch
        from tests.test_fanout_recuperar import _SchedFalso
        viejo = sch._scheduler
        sch._scheduler = None
        try:
            with mock.patch.object(sch, "AsyncIOScheduler", _SchedFalso), \
                    mock.patch.multiple(sch.settings, fanout_recuperar_enabled=False, fanout_enabled=True):
                sch.iniciar()
                jobs = {k.get("id"): (a, k) for a, k in sch._scheduler.jobs}
        finally:
            sch._scheduler = viejo
        self.assertNotIn("fanout_recuperar_arranque", jobs)
        a, _k = jobs["fanout_diferidas_temu_arranque"]
        self.assertEqual(a[1], "date")
        hilo = mock.AsyncMock()
        with mock.patch.object(sch.asyncio, "to_thread", hilo):
            asyncio.run(a[0]())
        self.assertIs(hilo.await_args.args[0], R.reencolar_diferidas, "regla 11: en un hilo")
        # …y la función no mira la bandera del recuperador, y se salta lo que ya va en la cola.
        encolados = []
        F._lote.append(("ZZZ-0078-NEG", "venta", time.time()))
        with mock.patch.multiple(R.settings, fanout_recuperar_enabled=False, supabase_write_fanout_log=True), \
                mock.patch.object(R, "diferidas_recientes", return_value=["ZZZ-0077-NEG", "ZZZ-0078-NEG"]), \
                mock.patch.object(F, "encolar_varios",
                                  side_effect=lambda s, motivo: encolados.append((list(s), motivo))):
            self.assertEqual(R.reencolar_diferidas(), ["ZZZ-0077-NEG"])
        self.assertEqual(encolados, [(["ZZZ-0077-NEG"], R.MOTIVO_DIFERIDA)])

    def test_el_seguro_reencola_como_siempre(self):
        # Apagadas, `_reencolar_marcas` sólo mira lo pendiente (como el recuperador).
        self.assertEqual(self._marcas(), ["ZZZ-0090-NEG"])


# ══════════════════════════════════════════════════════════════════════════════

class Hilos(Cola):
    """2 · FANOUT_WORKERS > 1: paralelo entre SKUs, nunca dentro del mismo."""

    AJUSTES = {"fanout_workers": 3}

    def _correr(self, hilos: int = 3) -> None:
        ts = [threading.Thread(target=F._drenar_picker, args=(i,)) for i in range(hilos)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=20)
        self.assertFalse(any(t.is_alive() for t in ts), "un hilo se quedó colgado")

    def test_nunca_dos_eventos_del_mismo_sku_y_en_orden(self):
        skus = [f"ZZZ-01{i:02d}-NEG" for i in range(9)]
        for s in skus:
            self.pendiente(s, motivo="m1")
        activos: dict[str, int] = {}
        traslapes, maximo, vistos = [], [0], []
        candado = threading.Lock()

        def aplicar(sku, motivo):
            with candado:
                activos[sku] = activos.get(sku, 0) + 1
                if activos[sku] > 1:
                    traslapes.append(sku)
                maximo[0] = max(maximo[0], sum(1 for v in activos.values() if v))
                vistos.append((sku, motivo))
            if motivo == "m1" and sku in skus[:3]:
                # El mismo SKU vuelve a la cola MIENTRAS se reparte (una venta en ráfaga).
                F.encolar(sku, "m2")
            time.sleep(0.05)
            with candado:
                activos[sku] -= 1
        with mock.patch.object(F, "DEBOUNCE_S", 0.0), \
                mock.patch.object(F, "_aplicar", side_effect=aplicar):
            self._correr()
        self.assertEqual(traslapes, [])
        self.assertGreaterEqual(maximo[0], 2, "con 3 hilos, SKUs distintos van en paralelo")
        self.assertEqual(len(vistos), 12)
        for s in skus[:3]:
            self.assertEqual([m for x, m in vistos if x == s], ["m1", "m2"], "en orden")
        self.assertEqual((F._pendientes, F._en_proceso), ({}, {}))

    def test_un_sku_en_proceso_no_se_toma_aunque_este_listo(self):
        F._en_proceso["ZZZ-0200-NEG"] = {"motivo": "m1", "desde": 0, "inicio": 0, "hilo": 1}
        self.pendiente("zzz-0200-neg", motivo="m2")     # mismo SKU, otra caja
        self.assertIsNone(F._tomar(0))
        F._en_proceso.clear()
        self.assertEqual(F._tomar(0), ("zzz-0200-neg", "m2"))

    def test_un_error_no_tumba_la_cola(self):
        for s in ("ZZZ-0300-NEG", "ZZZ-0301-NEG", "ZZZ-0302-NEG"):
            self.pendiente(s)
        vistos = []

        def aplicar(sku, motivo):
            vistos.append(sku)
            if sku == "ZZZ-0301-NEG":
                raise RuntimeError("Temu se cayó (prueba)")
        with mock.patch.object(F, "_aplicar", side_effect=aplicar):
            self._correr(1)
        self.assertEqual(sorted(vistos), ["ZZZ-0300-NEG", "ZZZ-0301-NEG", "ZZZ-0302-NEG"])
        self.assertEqual((F._en_proceso, F._contadores["errores"]), ({}, 1))

    def test_una_publicacion_compartida_no_se_escribe_a_la_vez(self):
        # 31 publicaciones de ML las comparten dos SKUs (padre/variante).
        a_la_vez, maximo = [0], [0]
        candado = threading.Lock()

        def escribir_ml(cuenta, item, cantidad):
            with candado:
                a_la_vez[0] += 1
                maximo[0] = max(maximo[0], a_la_vez[0])
            time.sleep(0.05)
            with candado:
                a_la_vez[0] -= 1
            return True, "ok"

        def plan(sku):
            return {"sku": sku, "ok": True, "stock_drop": 2, "reserva": 0, "objetivo": 2,
                    "acciones": [{"canal": "mercado_libre", "cuenta": "BEKURA", "item_id": ML,
                                  "stock_actual_canal": 5, "objetivo": 2, "accion": "escribir",
                                  "omitido_por": None}]}
        for s in ("ZZZ-0400-NEG", "ZZZ-0400-NEG-CH", "ZZZ-0401-NEG"):
            self.pendiente(s)
        with mock.patch.object(F, "plan", side_effect=plan), \
                mock.patch.object(F, "dry_run", return_value=False), \
                mock.patch.object(F, "_persistir"), \
                mock.patch.dict(F._ESCRITORES, {"mercado_libre": escribir_ml}):
            self._correr()
        self.assertEqual(maximo[0], 1)

    def test_la_verificacion_de_rancios_y_el_seguro_tambien_van_bajo_el_candado(self):
        # Antes el candado sólo rodeaba a la escritura: `_verificar` y los pasos del seguro
        # de dos SKUs que comparten la publicación podían cruzarse (revisión del 12-oct).
        a_la_vez, maximo = [0], [0]
        candado = threading.Lock()

        def ocupa(*_a, **_k):
            with candado:
                a_la_vez[0] += 1
                maximo[0] = max(maximo[0], a_la_vez[0])
            time.sleep(0.03)
            with candado:
                a_la_vez[0] -= 1

        def verificar(sku, a, ultima):
            ocupa()
            return {**a, "accion": "sin_cambio", "resultado": "verificado en vivo (prueba)"}, True

        def plan(sku):
            return {"sku": sku, "ok": True, "stock_drop": 2, "reserva": 0, "objetivo": 2,
                    "acciones": [{"canal": "tiktok", "cuenta": "K", "item_id": "77",
                                  "stock_actual_canal": 2, "objetivo": 2, "accion": "sin_cambio",
                                  "omitido_por": None}]}
        for s in ("ZZZ-0410-NEG", "ZZZ-0410-NEG-CH", "ZZZ-0411-NEG"):
            self.pendiente(s)
        with mock.patch.object(F, "plan", side_effect=plan), \
                mock.patch.object(F, "dry_run", return_value=False), \
                mock.patch.multiple(F.settings, fanout_verificar_rancio=True), \
                mock.patch.object(F, "_rancios", return_value={("tiktok", "77"): {"objetivo": 5}}), \
                mock.patch.object(F, "_verificar", side_effect=verificar), \
                mock.patch.object(F, "_seguro", side_effect=lambda paso, *a: ocupa() if paso in ("antes", "despues") else None), \
                mock.patch.object(F, "_persistir"):
            self._correr()
        self.assertEqual(maximo[0], 1)

    def test_el_freno_deja_pasar_la_rafaga_y_luego_cobra_por_minuto(self):
        reloj = [5000.0]
        dormido = []

        def dormir(s):
            dormido.append(round(s, 3))
        with mock.patch.dict(F._ritmo_cubeta, {}, clear=True), \
                mock.patch.object(F.time, "time", side_effect=lambda: reloj[0]), \
                mock.patch.object(F.time, "sleep", side_effect=dormir):
            for _ in range(12):
                F._ritmo("temu")            # 45/min: pasan las 6 de la ráfaga y luego 1 cada 60/45 s
            F._ritmo(None)                  # una etiqueta sin canal no frena
            self.assertEqual(dormido, [round(k * 60 / 45, 3) for k in range(1, 7)])
            reloj[0] += 60.0                # un minuto después la cubeta volvió a llenarse (tope 6)
            F._ritmo("temu")
            self.assertEqual(len(dormido), 6)
            self.assertAlmostEqual(F._ritmo_cubeta["temu"][0], F._RITMO_RAFAGA - 1)
        with mock.patch.dict(F._ritmo_cubeta, {}, clear=True), \
                mock.patch.multiple(F.settings, fanout_ritmo_tiktok=0.0), \
                mock.patch.object(F.time, "sleep") as nada:
            for _ in range(30):
                F._ritmo("tiktok")          # 0 = sin freno
        nada.assert_not_called()

    def test_el_canal_sale_de_la_etiqueta_de_en_hilo(self):
        self.assertEqual([F._canal_de(e) for e in ("lectura temu", "stock temu", "seguro: apagar temu",
                                                    "detalle tiktok", "inventario tiktok", "otra")],
                         ["temu", "temu", "temu", "tiktok", "tiktok", None])


# ══════════════════════════════════════════════════════════════════════════════

class Prioridad(Cola):
    """3 · FANOUT_PRIORIDAD: primero lo que ofrece de más A LA VENTA."""

    AJUSTES = {"fanout_prioridad": True}

    def orden(self, clases: dict[str, int]) -> list[str]:
        salida = []
        F._alterna["vieja"] = False          # cada tanda empieza por la fila por clase
        with mock.patch.object(F, "_clasificar", side_effect=lambda skus: {s: clases[s] for s in skus}):
            while (t := F._tomar(0)) is not None:
                salida.append(t[0])
                F._en_proceso.pop(t[0], None)
        return salida

    def test_bajadas_a_la_venta_luego_otras_bajadas_luego_subidas(self):
        # stock_watch encola en orden alfabético: hoy saldrían A, B, C, D, E.
        clases = {"ZZZ-A": 2, "ZZZ-B": 1, "ZZZ-C": 0, "ZZZ-D": 2, "ZZZ-E": 0}
        for i, s in enumerate(clases):
            self.pendiente(s, hace_s=60 - i)
        self.assertEqual(self.orden(clases), ["ZZZ-C", "ZZZ-E", "ZZZ-B", "ZZZ-A", "ZZZ-D"])

    def test_una_subida_vieja_toma_uno_de_cada_dos_turnos(self):
        clases = {"ZZZ-SUBE": 2, "ZZZ-BAJA": 1, "ZZZ-VENTA-1": 0, "ZZZ-VENTA-2": 0, "ZZZ-VENTA-3": 0}
        self.pendiente("ZZZ-SUBE", hace_s=14 * 60)
        for s in ("ZZZ-BAJA", "ZZZ-VENTA-1", "ZZZ-VENTA-2", "ZZZ-VENTA-3"):
            self.pendiente(s, hace_s=5)
        self.assertEqual(self.orden(clases), ["ZZZ-VENTA-1", "ZZZ-VENTA-2", "ZZZ-VENTA-3", "ZZZ-BAJA",
                                              "ZZZ-SUBE"], "a los 14 min sigue atrás")
        self.pendiente("ZZZ-SUBE", hace_s=16 * 60)
        for s in ("ZZZ-BAJA", "ZZZ-VENTA-1", "ZZZ-VENTA-2", "ZZZ-VENTA-3"):
            self.pendiente(s, hace_s=5)
        self.assertEqual(self.orden(clases), ["ZZZ-VENTA-1", "ZZZ-SUBE", "ZZZ-VENTA-2", "ZZZ-VENTA-3",
                                              "ZZZ-BAJA"],
                         "a los 16 min ya no cede: toma el turno siguiente aunque sigan llegando ventas")

    def test_lo_viejo_se_alterna_y_nunca_espera_para_siempre(self):
        # Una corriente de bajadas a la venta que no se acaba: lo viejo sale a la mitad del paso.
        viejas = [f"ZZZ-V{i}" for i in range(3)]
        ventas = [f"ZZZ-N{i}" for i in range(6)]
        for i, s in enumerate(viejas):
            self.pendiente(s, hace_s=40 * 60 - i)
        for s in ventas:
            self.pendiente(s, hace_s=1)
        clases = {**{s: 2 for s in viejas}, **{s: 0 for s in ventas}}
        self.assertEqual(self.orden(clases), ["ZZZ-N0", "ZZZ-V0", "ZZZ-N1", "ZZZ-V1", "ZZZ-N2", "ZZZ-V2",
                                              "ZZZ-N3", "ZZZ-N4", "ZZZ-N5"])

    def test_la_venta_que_llega_a_media_rafaga_no_espera_detras_de_lo_viejo(self):
        # S41906: 50 SKUs entraron 16.2 min tarde. Con «llegada + 10 min por nivel» una bajada
        # a la venta del segundo lote quedaba detrás de TODAS las bajadas sin venta del primero
        # (su exposición bajaba sólo 8 %, revisión del 12-oct).
        lote1 = [f"ZZZ-1-{i}" for i in range(4)]
        lote2 = ["ZZZ-2-A", "ZZZ-2-B"]
        for i, s in enumerate(lote1):
            self.pendiente(s, hace_s=16.2 * 60 - i)
        for s in lote2:
            self.pendiente(s, hace_s=1)
        clases = {**{s: 1 for s in lote1}, **{s: 0 for s in lote2}}
        self.assertEqual(self.orden(clases), ["ZZZ-2-A", "ZZZ-1-0", "ZZZ-2-B", "ZZZ-1-1", "ZZZ-1-2", "ZZZ-1-3"])
        # Antes del umbral no hay ni eso: la venta va primero, de punta a punta.
        for i, s in enumerate(lote1):
            self.pendiente(s, hace_s=8 * 60 - i)
        for s in lote2:
            self.pendiente(s, hace_s=1)
        self.assertEqual(self.orden(clases)[:2], lote2)

    def test_una_rafaga_que_llega_junta_sale_por_clase(self):
        # Mientras nadie pase del umbral, las clases no se mezclan (con «restar un nivel por cada
        # 10 min» volvía el orden alfabético a media ráfaga).
        clases = {f"ZZZ-{i:03d}": (0 if i % 3 == 0 else 1) for i in range(30)}
        for s in clases:
            self.pendiente(s, hace_s=10 * 60)
        orden = self.orden(clases)
        self.assertEqual(orden[:10], [s for s in clases if clases[s] == 0])

    def test_la_alternancia_se_ve_en_la_cola(self):
        self.pendiente("ZZZ-VIEJA", hace_s=20 * 60)
        self.pendiente("ZZZ-VENTA-1", hace_s=5)
        self.pendiente("ZZZ-VENTA-2", hace_s=4)
        for s, c in (("ZZZ-VIEJA", 2), ("ZZZ-VENTA-1", 0), ("ZZZ-VENTA-2", 0)):
            F._pendientes[s].update(clase=c, clase_en=time.time())
        self.assertEqual([e["sku"] for e in F.cola()], ["ZZZ-VENTA-1", "ZZZ-VIEJA", "ZZZ-VENTA-2"])
        F._alterna["vieja"] = True          # el último turno fue de la fila por clase
        self.assertEqual([e["sku"] for e in F.cola()], ["ZZZ-VIEJA", "ZZZ-VENTA-1", "ZZZ-VENTA-2"])

    def test_nada_se_toma_antes_de_reposar(self):
        self.pendiente("ZZZ-NUEVO", listo=False)
        self.pendiente("ZZZ-LISTO")
        self.assertEqual(self.orden({"ZZZ-NUEVO": 0, "ZZZ-LISTO": 2}), ["ZZZ-LISTO"])
        self.assertIn("ZZZ-NUEVO", F._pendientes)

    def test_si_la_clasificacion_revienta_va_en_orden_de_llegada(self):
        for i, s in enumerate(("ZZZ-3", "ZZZ-1", "ZZZ-2")):
            self.pendiente(s, hace_s=30 - i)
        salida = []
        with mock.patch.object(F, "_clasificar", side_effect=RuntimeError("kubera no contesta (prueba)")):
            while (t := F._tomar(0)) is not None:
                salida.append(t[0])
                F._en_proceso.pop(t[0], None)
        self.assertEqual(salida, ["ZZZ-3", "ZZZ-1", "ZZZ-2"])

    def test_el_seguro_con_la_cola_rapida_no_reencola_lo_que_va_en_el_lote(self):
        self.assertEqual(self._marcas(), [])

    def test_la_prioridad_sola_no_frena(self):
        # Cambia el orden, no el paso: el ritmo de los canales es el de hoy.
        with mock.patch.dict(F._ritmo_cubeta, {}, clear=True), mock.patch.object(F.time, "sleep") as dormir:
            for _ in range(30):
                F._ritmo("mercado_libre")
        dormir.assert_not_called()

    def test_se_clasifica_una_vez_por_tanda(self):
        for s in ("ZZZ-1", "ZZZ-2", "ZZZ-3"):
            self.pendiente(s)
        with mock.patch.object(F, "_clasificar", side_effect=lambda skus: {s: 1 for s in skus}) as cl:
            while (t := F._tomar(0)) is not None:
                F._en_proceso.pop(t[0], None)
        self.assertEqual(cl.call_count, 1)
        self.assertEqual(sorted(cl.call_args.args[0]), ["ZZZ-1", "ZZZ-2", "ZZZ-3"])

    def test_lo_nuevo_se_adelanta_al_resto(self):
        # Con el lote, una venta que llega a media ráfaga esperaba a que terminara.
        for s in ("ZZZ-1", "ZZZ-2", "ZZZ-3"):
            self.pendiente(s, hace_s=120)
        vistos = []

        def aplicar(sku, motivo):
            vistos.append(sku)
            if sku == "ZZZ-1":
                self.pendiente("ZZZ-VENTA", hace_s=0)
        with mock.patch.object(F, "_clasificar", side_effect=lambda skus: {s: 0 if s == "ZZZ-VENTA" else 1 for s in skus}), \
                mock.patch.object(F, "_aplicar", side_effect=aplicar):
            F._drenar_picker(0)
        self.assertEqual(vistos, ["ZZZ-1", "ZZZ-VENTA", "ZZZ-2", "ZZZ-3"])

    def test_la_cola_se_ve_en_el_orden_del_picker(self):
        for s, h in (("ZZZ-A", 50), ("ZZZ-B", 40), ("ZZZ-C", 30)):
            self.pendiente(s, hace_s=h)
        for s, c in (("ZZZ-A", 2), ("ZZZ-B", 1), ("ZZZ-C", 0)):
            F._pendientes[s].update(clase=c, clase_en=time.time())
        F._duraciones.extend([30.0] * 5)
        foto = F.cola()
        self.assertEqual([(e["sku"], e["posicion"], e["eta_s"]) for e in foto],
                         [("ZZZ-C", 1, 30), ("ZZZ-B", 2, 60), ("ZZZ-A", 3, 90)])

    def test_clasificar_con_el_valor_mas_nuevo(self):
        filas = [
            # A: TikTok a la venta con 5 y Woo 3 → baja algo a la venta.
            {"sku": "ZZZ-A", "canal": "tiktok", "status": "ACTIVATE", "situacion": None, "es_full": False, "valor": 5},
            # B: ML pausada con 5 → otra bajada (no está a la venta).
            {"sku": "ZZZ-B", "canal": "mercado_libre", "status": None, "situacion": "paused", "es_full": False, "valor": 5},
            # C: Temu a la venta con 2 y Woo 3 → subida.
            {"sku": "ZZZ-C", "canal": "temu", "status": "2/8", "situacion": None, "es_full": False, "valor": 2},
            # D: lo único que ofrece de más es FULL → no cuenta.
            {"sku": "ZZZ-D", "canal": "mercado_libre", "status": None, "situacion": "active", "es_full": True, "valor": 9},
            # E: Woo en 0 y Temu a la venta (aunque diga 0): el seguro la tiene que sacar.
            {"sku": "ZZZ-E", "canal": "temu", "status": "2/8", "situacion": None, "es_full": False, "valor": 0},
            # F: a la venta con valor desconocido → 1.
            {"sku": "ZZZ-F", "canal": "tiktok", "status": "ACTIVATE", "situacion": None, "es_full": False, "valor": None},
        ]
        woo = {"ZZZ-A": 3, "ZZZ-B": 3, "ZZZ-C": 3, "ZZZ-D": 3, "ZZZ-E": 0, "ZZZ-F": 3, "ZZZ-G": None}
        consultas = []

        def fetch_all(sql, params=None):
            consultas.append((sql, params))
            return [dict(f) for f in filas]
        with mock.patch.multiple(F.settings, fanout_canales="", fanout_tiktok=True, fanout_temu=True,
                                 fanout_reserva=0), \
                mock.patch.object(F, "_stock_drop_varios", return_value={k: v for k, v in woo.items() if v is not None}), \
                mock.patch.object(sdb, "fetch_all", side_effect=fetch_all):
            clases = F._clasificar(["zzz-a", "ZZZ-B", "ZZZ-C", "ZZZ-D", "ZZZ-E", "ZZZ-F", "ZZZ-G"])
        self.assertEqual(clases, {"zzz-a": 0, "ZZZ-B": 1, "ZZZ-C": 2, "ZZZ-D": 2, "ZZZ-E": 0,
                                  "ZZZ-F": 1, "ZZZ-G": 1})
        self.assertEqual(len(consultas), 1)
        self.assertIs(consultas[0][0], F._SQL_CLASES)
        self.assertEqual(consultas[0][1]["c"], ["mercado_libre", "tiktok", "temu"])

    def test_el_sql_de_clases(self):
        sql = " ".join(F._SQL_CLASES.split())
        for pedazo in ("from ops.fanout_log", "not dry_run", "(accion = 'escribir' and resultado ilike 'ok%%')",
                       "(accion = 'sin_cambio' and resultado like 'verificado en vivo%%')",
                       "(accion = 'verif_temu' and (resultado ilike 'ok%%' or stock_canal is not null))",
                       "w.ts > l.updated_at then w.valor else l.stock_own",
                       "from channel.listings l", "l.sku = any(%(s)s::citext[])", "l.canal = any(%(c)s)"):
            self.assertIn(pedazo, sql)


# ══════════════════════════════════════════════════════════════════════════════

class TemuDiferido(Cola):
    """4 · FANOUT_TEMU_VERIFICAR_DIFERIDO: la cola no duerme; el verificador converge."""

    AJUSTES = {"fanout_temu_verificar_diferido": True, "fanout_ritmo_temu": 0.0,
               "fanout_ritmo_tiktok": 0.0, "fanout_ritmo_ml": 0.0}

    def setUp(self):
        super().setUp()
        self.t = [10_000.0]
        self.vivo = 5
        self.come = 0           # piezas que Temu «se come» en cada edición (venta/apartado)
        self.api: list = []
        self.guardados: list[dict] = []
        self.seguro: list = []
        self.dormido: list = []

        async def llamar(tipo, datos):
            self.api.append(tipo)
            if tipo == "bg.local.goods.list.query":
                return {"goodsList": [{"goodsId": int(G), "quantity": self.vivo, "skuIdList": [222]}]}
            self.vivo += datos["skuStockChangeList"][0]["stockDiff"] - self.come
            return {"operateResult": True, "skuStockEditStatusInfoList": [{"stockEditStatus": True}]}

        def dormir(s):
            self.dormido.append(s)
            self.t[0] += s

        def seguro(paso, *args):
            self.seguro.append(paso if paso != "despues" else ("despues", args[-1]))
            return "ctx" if paso == "abrir" else None

        for p in (mock.patch.object(TM, "llamar", side_effect=llamar),
                  mock.patch.object(TM, "disponible", return_value=True),
                  mock.patch.object(F, "_en_hilo", side_effect=lambda fab, et, timeout=60: asyncio.run(fab())),
                  mock.patch("time.time", side_effect=lambda: self.t[0]),
                  mock.patch("time.sleep", side_effect=dormir),
                  mock.patch.dict(F._temu_escrito_en, {}, clear=True),
                  mock.patch.dict(F._temu_conv, {}, clear=True),
                  mock.patch.object(F, "_asegurar_verificador"),
                  mock.patch.object(F, "_persistir", side_effect=self.guardados.append),
                  mock.patch.object(F, "dry_run", return_value=False),
                  mock.patch.object(F, "_seguro", side_effect=seguro)):
            p.start()
            self.addCleanup(p.stop)

    def evento(self, objetivo: int, censo: int = 5, motivo: str = "delta de Odoo aplicado") -> dict:
        p = {"sku": "ZZZ-0077-NEG", "ok": True, "stock_drop": objetivo, "reserva": 0, "objetivo": objetivo,
             "acciones": [{"canal": "temu", "cuenta": "TEMU", "item_id": G, "stock_actual_canal": censo,
                           "objetivo": objetivo, "accion": "escribir", "omitido_por": None}]}
        with mock.patch.object(F, "plan", return_value=p):
            F._aplicar("ZZZ-0077-NEG", motivo)
        return self.guardados[-1]["acciones"][0]

    def verificar(self, tras_s: float = F._TEMU_ESPERA_S) -> None:
        self.t[0] += tras_s
        F._verificar_temu_listos()

    def test_la_cola_no_duerme(self):
        fila = self.evento(3)
        self.assertEqual(self.dormido, [])
        self.assertEqual(fila["resultado"], f"ok ({F._TEMU_DIFERIDA})")
        self.assertEqual(self.vivo, 3)
        self.assertEqual(self.api, ["bg.local.goods.list.query", "bg.local.goods.stock.edit"])
        self.assertEqual(F._temu_conv[int(G)]["listo_en"], self.t[0] + F._TEMU_ESPERA_S)
        self.assertEqual(self.seguro, ["abrir", "antes", ("despues", True), "cerrar"])

    def test_el_verificador_confirma_sin_anotar(self):
        self.evento(3)
        self.verificar(F._TEMU_ESPERA_S - 1)
        self.assertIn(int(G), F._temu_conv, "antes de los 8 s no relee")
        self.verificar(1)
        self.assertEqual(F._temu_conv, {})
        self.assertEqual(len(self.guardados), 1, "si quedó bien, no anota nada")

    def test_el_verificador_detecta_y_corrige(self):
        self.come = 1                    # Temu dejó 2 en vez de 3
        self.evento(3)
        self.assertEqual(self.vivo, 2)
        self.come = 0
        self.verificar()
        self.assertEqual(self.vivo, 3, "escribió +1")
        self.assertIn(int(G), F._temu_conv, "y vuelve a verificar")
        self.verificar()
        self.assertEqual(F._temu_conv, {})
        fila = self.guardados[-1]["acciones"][0]
        self.assertEqual((fila["accion"], fila["resultado"], fila["stock_actual_canal"], fila["objetivo"]),
                         (F.ACC_VERIF_TEMU, f"ok ({F._TEMU_DIFERIDA}: corregido 2→3)", 3, 3))
        self.assertEqual(self.guardados[-1]["motivo"], "delta de Odoo aplicado")

    def test_tras_tres_escrituras_anota_error(self):
        self.come = 1                    # Temu se come una pieza en cada edición
        self.evento(3)
        for _ in range(F._TEMU_INTENTOS):
            self.verificar()
        self.assertEqual(F._temu_conv, {})
        fila = self.guardados[-1]["acciones"][0]
        self.assertEqual(fila["accion"], F.ACC_VERIF_TEMU)
        self.assertTrue(fila["resultado"].startswith(f"ERROR ({F._TEMU_DIFERIDA}): no convergió tras 3"),
                        fila["resultado"])
        self.assertEqual(fila["stock_actual_canal"], 2, "lo que leyó: desmiente el «ok» diferido")
        self.assertEqual(self.api.count("bg.local.goods.stock.edit"), 3)

    def test_un_segundo_evento_no_escribe_otro_diff(self):
        self.evento(3)
        self.api.clear()
        fila = self.evento(1)            # llega otro cambio antes de verificar
        self.assertEqual(self.api, [], "ni lee ni escribe: la lectura no es de fiar")
        self.assertEqual(fila["resultado"], f"ok ({F._TEMU_DIFERIDA})")
        self.assertEqual(F._temu_conv[int(G)]["objetivo"], 1)
        self.verificar()
        self.assertEqual(self.vivo, 1)
        self.verificar()
        self.assertEqual(F._temu_conv, {})
        self.assertEqual(self.guardados[-1]["acciones"][0]["resultado"],
                         f"ok ({F._TEMU_DIFERIDA}: corregido 3→1)")

    def test_ya_tenia_sin_escritura_reciente_no_verifica(self):
        self.vivo = 3
        fila = self.evento(3, censo=5)
        self.assertEqual(fila["resultado"], "ok")
        self.assertEqual(F._temu_conv, {})

    def test_con_marca_del_seguro_y_stock_sigue_sincrono(self):
        with mock.patch.object(F, "seguro_encendido", return_value=True), \
                mock.patch.object(S, "_marcas_por_item", return_value={("temu", G): {"sku": "ZZZ-0077-NEG"}}):
            fila = self.evento(3)
        self.assertEqual(fila["resultado"], "ok")
        self.assertIn(F._TEMU_ESPERA_S, self.dormido, "durmió y releyó: la reactivación exige el stock verificado")
        self.assertEqual(F._temu_conv, {})

    def test_con_objetivo_cero_se_difiere_aunque_haya_marca(self):
        with mock.patch.object(F, "seguro_encendido", return_value=True), \
                mock.patch.object(S, "_marcas_por_item", return_value={("temu", G): {"sku": "ZZZ-0077-NEG"}}):
            fila = self.evento(0)
        self.assertEqual(fila["resultado"], f"ok ({F._TEMU_DIFERIDA})")
        self.assertEqual(self.seguro[:2], ["abrir", "antes"], "el seguro la saca de la venta ANTES")

    def test_sin_leer_las_marcas_se_verifica(self):
        with mock.patch.object(F, "seguro_encendido", return_value=True), \
                mock.patch.object(S, "_marcas_por_item", side_effect=RuntimeError("kubera (prueba)")):
            self.assertTrue(F._temu_exige_verificado({"objetivo": 3, "item_id": G}))

    def test_excedentes_y_rancios_siguen_sincronos_y_esperan_la_convergencia(self):
        self.evento(3)
        self.vivo = 3
        soltar = {"n": 0}

        def dormir(s):
            self.dormido.append(s)
            self.t[0] += s
            if s == 0.5:                 # mientras espera, el verificador termina
                soltar["n"] += 1
                F._temu_conv.clear()
        with mock.patch("time.sleep", side_effect=dormir):
            ok, det = F._escribir_temu("TEMU", G, 2, solo_bajar=True)
        self.assertEqual(soltar["n"], 1, "esperó a que el verificador soltara el goods")
        self.assertEqual((ok, det), (True, "ok (3→2)"))
        self.assertEqual(self.dormido[-1], F._TEMU_ESPERA_S, "y siguió síncrono")
        self.dormido.clear()
        ok, det = F._escribir_temu("TEMU", G, 2, verificar=True)
        self.assertEqual((ok, det), (True, "ok (ya tenía 2 en vivo)"))
        self.assertEqual(self.dormido, [F._TEMU_ESPERA_S], "dentro de 90 s de la escritura relee, como siempre")

    def test_si_la_espera_vence_lo_sincrono_descarta_el_objetivo_viejo(self):
        # [A] de la revisión del 12-oct: el evento 1 (objetivo 0) queda diferido y el
        # verificador no alcanza a soltar el goods en 32 s. Antes, la escritura síncrona (la
        # reactivación del seguro, objetivo 3) escribía y luego el verificador la regresaba a 0.
        self.evento(0)
        self.assertEqual((self.vivo, F._temu_conv[int(G)]["objetivo"]), (0, 0))
        with self.assertLogs("omnicanal.fanout", "WARNING") as avisos:
            ok, det = F._escribir_temu("TEMU", G, 3)
        self.assertEqual((ok, det, self.vivo), (True, "ok (0→3)", 3))
        self.assertGreaterEqual(self.dormido.count(F._TEMU_ESPERA_PASO_S),
                                int(F._TEMU_ESPERA_S * (F._TEMU_INTENTOS + 1) / F._TEMU_ESPERA_PASO_S) - 1,
                                "esperó los 32 s")
        texto = " ".join(avisos.output)
        self.assertIn("sigue pendiente", texto, "al vencer, queda en el log")
        self.assertIn("se descarta la verificación diferida (objetivo 0", texto)
        self.assertEqual(F._temu_conv, {})
        self.verificar()
        self.verificar()
        self.assertEqual(self.vivo, 3, "el verificador ya no la regresa a 0")
        self.assertEqual(len(self.guardados), 1, "ni anota una «corrección»")

    def test_si_lo_sincrono_no_baja_la_convergencia_sigue(self):
        self.evento(1)                    # 5 → 1, pendiente de verificar
        with self.assertLogs("omnicanal.fanout", "WARNING"):
            ok, det = F._escribir_temu("TEMU", G, 4, solo_bajar=True)    # excedentes: 1 ≤ 4
        self.assertTrue(ok and det.startswith(F.NO_BAJA), det)
        self.assertEqual(F._temu_conv[int(G)]["objetivo"], 1, "no escribió: la verificación sigue siendo la suya")

    def test_una_convergencia_que_entra_entre_la_espera_y_el_candado_se_espera_otra_vez(self):
        # [B] de la revisión: lo síncrono pasa la espera (no hay convergencia) y, antes de que
        # tome el candado, la cola difiere el objetivo 4. Antes escribía encima con una lectura
        # rancia y luego el verificador lo volvía a subir a 4.
        real = F._temu_esperar_convergencia
        esperas = []

        def espera(goods_id, fin):
            esperas.append(self.t[0])
            real(goods_id, fin)
            if len(esperas) == 1:          # aquí entra la cola, desde otro hilo
                F._temu_diferir("TEMU", int(G), 4, "ZZZ-0077-NEG", "venta")

        def dormir(s):
            self.dormido.append(s)
            self.t[0] += s
            if s == F._TEMU_ESPERA_PASO_S:
                F._verificar_temu_listos()     # el verificador corre mientras lo síncrono espera
        with mock.patch.object(F, "_temu_esperar_convergencia", side_effect=espera), \
                mock.patch("time.sleep", side_effect=dormir):
            ok, det = F._escribir_temu("TEMU", G, 2, solo_bajar=True)
        self.assertEqual(len(esperas), 2, "soltó el candado y volvió a esperar")
        self.assertEqual((ok, det, self.vivo), (True, "ok (4→2)", 2))
        self.assertEqual(F._temu_conv, {})
        self.verificar()
        self.assertEqual(self.vivo, 2, "nadie la vuelve a subir a 4")

    def test_el_verificador_espera_a_que_el_evento_se_guarde(self):
        # La fila de la cola se escribe al FINAL del evento; si el verificador anotaba antes,
        # esa fila (id y ts mayores) tapaba la corrección o el ERROR en la matriz.
        F._en_proceso["ZZZ-0077-NEG"] = {"motivo": "venta", "desde": 0, "inicio": 123.0, "hilo": 0}
        self.come = 1
        self.evento(3)                    # `_aplicar` directo: el evento 123 sigue «en proceso»
        self.come = 0
        self.assertEqual(F._temu_conv[int(G)]["evento"], 123.0)
        self.verificar()
        self.assertEqual((self.vivo, len(self.guardados)), (2, 1), "no relee ni anota todavía")
        F._en_proceso["ZZZ-0077-NEG"]["inicio"] = 456.0     # otro evento del mismo SKU: ya no espera
        self.verificar(1)
        self.assertEqual(self.vivo, 3)
        F._en_proceso.clear()
        self.verificar()
        self.assertEqual([g["acciones"][0]["accion"] for g in self.guardados], ["escribir", F.ACC_VERIF_TEMU],
                         "la verificación queda DESPUÉS de la fila de la cola")

    def test_el_verificador_no_toma_un_goods_ocupado(self):
        self.evento(3)
        self.t[0] += F._TEMU_ESPERA_S
        with F._temu_candado(int(G)):
            F._verificar_temu_listos()
            self.assertIn(int(G), F._temu_conv)
        F._verificar_temu_listos()
        self.assertEqual(F._temu_conv, {})

    def test_un_error_del_canal_en_el_verificador_se_anota_y_no_se_repite(self):
        self.evento(3)

        async def caido(tipo, datos):
            raise RuntimeError("Temu 500 (prueba)")
        with mock.patch.object(TM, "llamar", side_effect=caido):
            self.verificar()
        self.assertEqual(F._temu_conv, {})
        fila = self.guardados[-1]["acciones"][0]
        self.assertTrue(fila["resultado"].startswith(f"ERROR ({F._TEMU_DIFERIDA}): escrito, pero no se pudo verificar"))
        self.assertIsNone(fila["stock_actual_canal"])

    def test_el_recuperador_al_arrancar_reencola_las_diferidas(self):
        encolados = []
        with mock.patch.multiple(R.settings, fanout_recuperar_enabled=True, fanout_recuperar_horas=6,
                                 fanout_recuperar_gracia_min=15, fanout_recuperar_tope=300,
                                 supabase_write_fanout_log=True), \
                mock.patch.object(R, "candidatos", return_value=[]), \
                mock.patch.object(R, "diferidas_recientes", return_value=["ZZZ-0077-NEG"]), \
                mock.patch.object(F, "encolar_varios", side_effect=lambda s, motivo: encolados.append((list(s), motivo))):
            R.revisar(arranque=True)
            R.revisar(arranque=False)
        self.assertEqual(encolados, [(["ZZZ-0077-NEG"], R.MOTIVO_DIFERIDA)])

    def test_el_sql_de_las_diferidas(self):
        consultas = []
        with mock.patch.object(sdb, "fetch_all", side_effect=lambda sql, p: consultas.append((sql, p)) or []):
            R.diferidas_recientes()
        sql, p = consultas[0]
        self.assertIn("canal = 'temu' and accion = 'escribir' and not dry_run", " ".join(sql.split()))
        self.assertEqual(p["r"], f"ok ({F._TEMU_DIFERIDA}%")
        self.assertEqual(p["a"], F._ARRANQUE - R.VENTANA_DIFERIDA_S)
        self.assertEqual(p["b"], self.t[0], "hasta ahora: en un despliegue el proceso viejo sigue un rato")


# ══════════════════════════════════════════════════════════════════════════════

class HilosReales(unittest.TestCase):
    """4b · Con hilos y reloj REALES (las demás pruebas de Temu corren en un solo hilo con
    reloj falso): 2 hilos de la cola + el verificador + los excedentes, todos sobre los
    mismos goods, con la lectura de Temu rancia unos milisegundos. Al final cada goods
    tiene que quedar en el Woo de su SKU, sin errores ni dos eventos del mismo SKU a la vez."""

    N = 8
    LAG_S = 0.03            # la lectura de Temu devuelve lo de hace 30 ms

    def test_cola_verificador_y_excedentes_sobre_los_mismos_goods(self):
        import random
        azar = random.Random(7)
        goods = {f"ZZZ-{i:04d}-NEG": 800_000 + i for i in range(self.N)}
        woo = {s: 5 for s in goods}
        temu = {g: 5 for g in goods.values()}
        historia = {g: deque([(0.0, 5)], maxlen=200) for g in goods.values()}
        api = threading.Lock()
        activos: dict[str, int] = {}
        traslapes: list[str] = []

        async def llamar(tipo, datos):
            g = int(datos["goodsId"]) if "goodsId" in datos else int(datos["goodsIdList"][0])
            with api:
                if tipo == "bg.local.goods.list.query":
                    t = time.time() - self.LAG_S
                    q = [v for ts, v in historia[g] if ts <= t][-1]
                    return {"goodsList": [{"goodsId": g, "quantity": q, "skuIdList": [1]}]}
                temu[g] += int(datos["skuStockChangeList"][0]["stockDiff"])
                historia[g].append((time.time(), temu[g]))
            return {"operateResult": True, "skuStockEditStatusInfoList": [{"stockEditStatus": True}]}

        def plan(sku):
            with api:
                activos[sku] = activos.get(sku, 0) + 1
                if activos[sku] > 1:
                    traslapes.append(sku)
                o = woo[sku]
            return {"sku": sku, "ok": True, "stock_drop": o, "reserva": 0, "objetivo": o,
                    "acciones": [{"canal": "temu", "cuenta": "T", "item_id": str(goods[sku]),
                                  "stock_actual_canal": -1, "objetivo": o, "accion": "escribir",
                                  "omitido_por": None}]}

        def persistir(evento):
            if any(a.get("accion") == F.ACC_VERIF_TEMU for a in evento["acciones"]):
                return
            with api:
                activos[evento["sku"]] -= 1

        parches = [
            mock.patch.multiple(F.settings, fanout_prioridad=True, fanout_workers=2,
                                fanout_temu_verificar_diferido=True, fanout_ritmo_temu=0.0,
                                fanout_ritmo_tiktok=0.0, fanout_ritmo_ml=0.0, fanout_reserva=0,
                                fanout_prioridad_envejecer_min=15.0),
            mock.patch.object(F, "DEBOUNCE_S", 0.02), mock.patch.object(F, "_TICK_S", 0.01),
            mock.patch.object(F, "_TEMU_ESPERA_S", 0.06), mock.patch.object(F, "_TEMU_VERIF_TICK_S", 0.01),
            mock.patch.object(F, "_TEMU_ESPERA_PASO_S", 0.01), mock.patch.object(F, "_TEMU_VENTANA_RANCIA_S", 0.2),
            mock.patch.dict(F._pendientes, {}, clear=True), mock.patch.dict(F._en_proceso, {}, clear=True),
            mock.patch.object(F, "_lote", []), mock.patch.object(F, "_duraciones", deque(maxlen=40)),
            mock.patch.dict(F._contadores, {k: 0 for k in F._contadores}),
            mock.patch.dict(F._alterna, {"vieja": False}),
            mock.patch.dict(F._temu_conv, {}, clear=True), mock.patch.dict(F._temu_escrito_en, {}, clear=True),
            mock.patch.object(F, "_asegurar_worker"), mock.patch.object(F, "_asegurar_verificador"),
            mock.patch.object(F, "_en_hilo", side_effect=lambda fab, et, timeout=60: asyncio.run(fab())),
            mock.patch.object(TM, "llamar", side_effect=llamar), mock.patch.object(TM, "disponible", return_value=True),
            mock.patch.object(F, "plan", side_effect=plan), mock.patch.object(F, "dry_run", return_value=False),
            mock.patch.object(F, "_persistir", side_effect=persistir),
            mock.patch.object(F, "habilitado", return_value=True),
            mock.patch.object(F, "seguro_encendido", return_value=False),
            mock.patch.object(F, "_rancios", return_value={}),
            mock.patch.object(F, "_clasificar", side_effect=lambda skus: {s: sum(map(ord, s)) % 3 for s in skus}),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        parar = threading.Event()

        def cola(i):
            while not parar.is_set():
                time.sleep(F._TICK_S)
                F._drenar_picker(i)

        def verificador():
            while not parar.is_set():
                time.sleep(F._TEMU_VERIF_TICK_S)
                F._verificar_temu_listos()

        fin_excedentes = time.time() + 1.5
        bajadas = []

        def excedentes():
            r = random.Random(11)
            while time.time() < fin_excedentes:
                s = r.choice(list(goods))
                bajadas.append(F._escribir_temu("T", str(goods[s]), woo[s], solo_bajar=True))
                time.sleep(0.02)
        hilos = [threading.Thread(target=cola, args=(i,), daemon=True) for i in range(2)]
        hilos += [threading.Thread(target=verificador, daemon=True), threading.Thread(target=excedentes, daemon=True)]
        for h in hilos:
            h.start()
        self.addCleanup(parar.set)
        while time.time() < fin_excedentes:
            s = azar.choice(list(goods))
            woo[s] = azar.randint(0, 6)
            F.encolar(s, "venta")
            time.sleep(0.015)
        hilos[-1].join(5)
        for s in goods:                   # el último cambio de cada SKU, ya sin excedentes
            F.encolar(s, "venta")
        limite = time.time() + 15
        while time.time() < limite:
            with F._lock:
                quedan = bool(F._pendientes or F._en_proceso)
            with F._temu_lock:
                quedan = quedan or bool(F._temu_conv)
            if not quedan:
                break
            time.sleep(0.05)
        parar.set()
        for h in hilos[:-1]:
            h.join(5)
        self.assertFalse(quedan, "la cola o el verificador no terminaron")
        self.assertEqual(traslapes, [])
        self.assertEqual(F._contadores["errores"], 0)
        self.assertGreater(len(bajadas), 10, "los excedentes sí corrieron a la par")
        self.assertEqual({s: temu[g] for s, g in goods.items()}, woo)


# ══════════════════════════════════════════════════════════════════════════════

class Matriz(Cola):
    """5 · La matriz contesta con la cola REAL, no con un plazo de 15 min."""

    AHORA = datetime(2026, 10, 12, 19, 50, tzinfo=timezone.utc)      # 13:50 CDMX

    def causa(self, edad_min: float, pasada_hace_min: float | None = None) -> dict:
        """La causa de una celda cuyo Woo cambió hace `edad_min` sin reparto después; con
        `pasada_hace_min`, stock_watch lleva esos minutos en una pasada."""
        cambio = self.AHORA - timedelta(minutes=edad_min)
        hora = (cambio - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")

        def fetch_all(sql, params=None):
            if "accion in ('odoo_delta', 'woo_cambio')" in sql:
                return [{"sku": "ZZZ-0500-NEG", "ts": cambio, "resultado": "Woo 4 -> 1", "hora": hora}]
            return []                    # sin evento del fan-out después

        def fetch_one(sql, params=None):
            return {"n": self.AHORA} if "now()" in sql else {}
        celdas = [{"sku": "ZZZ-0500-NEG", "canal": "tiktok", "cuenta": "KUBERA",
                   "listing": {"canal": "tiktok", "status": "ACTIVATE"}}]
        candado = mock.Mock()
        candado.locked.return_value = pasada_hace_min is not None
        desde = None if pasada_hace_min is None else (self.AHORA - timedelta(minutes=pasada_hace_min)).timestamp()
        with mock.patch.object(sdb, "fetch_all", side_effect=fetch_all), \
                mock.patch.object(sdb, "fetch_one", side_effect=fetch_one), \
                mock.patch.object(SW, "_lock", candado), mock.patch.object(SW, "_pasada_desde", desde):
            return V._causas(celdas, "2026-10-12")[("ZZZ-0500-NEG", "tiktok", "KUBERA")]

    def test_en_cola_con_su_posicion(self):
        F._duraciones.extend([30.0] * 5)
        for s in ("ZZZ-0498-NEG", "ZZZ-0499-NEG", "ZZZ-0500-NEG"):
            F._lote.append((s, "delta de Odoo aplicado", self.AHORA.timestamp() - 17 * 60))
        c = self.causa(17)
        self.assertEqual((c["c"], c["t"]), ("cola", "en cola (posición 3, ~2 min)"))
        self.assertIn("de 4 a 1 hoy a las 13:33", c["d"])
        self.assertIn("espera su turno en la cola del fan-out desde las 13:33", c["d"])

    def test_en_cola_un_cambio_de_otro_dia_lleva_la_fecha(self):
        # Lo devolvió a la cola el seguro o el recuperador: el cambio de Woo es de antier.
        self.pendiente("ZZZ-0500-NEG", motivo=R.MOTIVO)
        self.assertIn("de 4 a 1 el 10-oct a las 13:50", self.causa(2 * 24 * 60)["d"])

    def test_en_cola_lo_que_reencolo_el_recuperador(self):
        self.pendiente("zzz-0500-neg", motivo=R.MOTIVO)
        c = self.causa(40)
        self.assertEqual(c["c"], "cola")
        self.assertIn("lo devolvió a la cola el recuperador", c["d"])

    def test_repartiendo_ahora(self):
        F._en_proceso["ZZZ-0500-NEG"] = {"motivo": "venta", "desde": 0, "inicio": 0, "hilo": 0}
        c = self.causa(25)
        self.assertEqual((c["c"], c["t"]), ("proceso", "repartiendo ahora"))

    def test_fuera_de_la_cola_y_viejo_es_perdido(self):
        self.assertEqual(self.causa(20)["c"], "perdido")
        self.assertEqual(self.causa(4)["c"], "perdido", "con la cola a la vista, 4 min sin estar en ella ya es perdido")

    def test_fuera_de_la_cola_y_reciente_es_en_camino(self):
        self.assertEqual(self.causa(2)["c"], "camino")

    def test_lo_que_anoto_la_pasada_en_curso_es_en_camino(self):
        # Una pasada larga (5 min) anotó el cambio hace 4: todavía no lo encola.
        c = self.causa(4, pasada_hace_min=5)
        self.assertEqual(c["c"], "camino")
        self.assertIn("stock_watch todavía no termina su pasada", c["d"])
        self.assertEqual(self.causa(4)["c"], "perdido", "sin la pasada, 4 min fuera de la cola es perdido")

    def test_un_cambio_viejo_sigue_perdido_aunque_haya_pasada(self):
        # Antes, con stock_watch a media pasada TODO cambio perdido salía «en camino», aunque
        # fuera de hace días (y `_skus_perdidos` sí lo contaba: la matriz se contradecía).
        self.assertEqual(self.causa(3 * 24 * 60, pasada_hace_min=1)["c"], "perdido")
        self.assertEqual(self.causa(20, pasada_hace_min=1)["c"], "perdido")

    def test_en_proceso_y_otra_vez_en_cola_es_repartiendo_ahora_y_cuenta_una_vez(self):
        F._en_proceso["ZZZ-0500-NEG"] = {"motivo": "venta", "desde": 0, "inicio": 0, "hilo": 0}
        self.pendiente("ZZZ-0500-NEG", motivo="venta")      # volvió a la cola mientras se reparte
        self.pendiente("ZZZ-0501-NEG")
        self.assertEqual(self.causa(25)["c"], "proceso")
        e = V._fanout_estado()
        self.assertEqual((e["cola"], e["en_proceso"]), (2, 1))

    def test_si_la_cola_no_se_puede_leer_contesta_como_antes(self):
        with mock.patch.object(F, "cola", side_effect=RuntimeError("prueba")):
            self.assertEqual(V._cola_viva(), ({}, None, False))

    def test_si_el_fan_out_no_corre_aqui_el_plazo_de_siempre(self):
        # El backend del sandbox no corre el fan-out: su cola vacía no dice nada.
        with mock.patch.object(F, "habilitado", return_value=False):
            self.assertEqual(self.causa(10)["c"], "camino")
            self.assertEqual(self.causa(16)["c"], "perdido")

    def test_los_perdidos_no_incluyen_lo_que_esta_en_la_cola(self):
        self.pendiente("ZZZ-0601-NEG")
        F._en_proceso["ZZZ-0602-NEG"] = {"motivo": "venta", "desde": 0, "inicio": 0, "hilo": 0}
        filas = [{"sku": s} for s in ("ZZZ-0600-NEG", "zzz-0601-neg", "ZZZ-0602-NEG")]
        with mock.patch.object(sdb, "fetch_all", return_value=filas):
            self.assertEqual(V._skus_perdidos(), ["ZZZ-0600-NEG"])

    def test_la_cola_del_panel_cuenta_el_lote_y_lo_que_esta_en_proceso(self):
        self.pendiente("ZZZ-1")
        F._lote.extend([("ZZZ-2", "m", 0.0), ("ZZZ-3", "m", 0.0)])
        F._en_proceso["ZZZ-4"] = {"motivo": "m", "desde": 0, "inicio": 0, "hilo": 0}
        e = V._fanout_estado()
        self.assertEqual((e["cola"], e["en_proceso"]), (4, 1))
        self.assertEqual(F.en_cola(), {"ZZZ-1", "ZZZ-2", "ZZZ-3", "ZZZ-4"})

    def test_el_recuperador_con_la_cola_rapida_mira_la_cola_completa(self):
        F._lote.append(("ZZZ-0700-NEG", "m", 0.0))
        F._en_proceso["ZZZ-0701-NEG"] = {"motivo": "m", "desde": 0, "inicio": 0, "hilo": 0}
        encolados = []
        filas = [{"sku": s, "ts": datetime.now(timezone.utc) - timedelta(minutes=40)}
                 for s in ("ZZZ-0700-NEG", "zzz-0701-neg", "ZZZ-0702-NEG")]
        with mock.patch.multiple(F.settings, fanout_prioridad=True), \
                mock.patch.multiple(R.settings, fanout_recuperar_enabled=True, fanout_recuperar_horas=6,
                                    fanout_recuperar_gracia_min=15, fanout_recuperar_tope=300,
                                    supabase_write_fanout_log=True), \
                mock.patch.object(R, "candidatos", return_value=filas), \
                mock.patch.object(F, "encolar_varios", side_effect=lambda s, motivo: encolados.append(list(s))), \
                mock.patch.dict(R._ya, {}, clear=True):
            R.revisar()
        self.assertEqual(encolados, [["ZZZ-0702-NEG"]])

    def test_la_barra_agrupa_con_la_etiqueta_generica(self):
        self.assertEqual((V._CAUSA_TXT["cola"], V._CAUSA_TXT["proceso"]), ("en cola", "repartiendo ahora"))
        self.assertIn('_CAUSA_TXT.get(ca["c"], ca["t"])', inspect.getsource(V.matriz))

    def test_los_sql_toman_la_verificacion_diferida(self):
        filtro = "(accion = 'verif_temu' and (resultado ilike 'ok%%' or stock_canal is not null))"
        self.assertIn(filtro, " ".join(V._SQL_VIVAS.split()))
        self.assertIn(filtro, " ".join(F._SQL_ULTIMA_CONOCIDA.split()))
        self.assertIn("when accion = 'verif_temu' and resultado not ilike 'ok%%' then stock_canal",
                      " ".join(F._SQL_ULTIMA_CONOCIDA.split()))
        self.assertIn("accion = 'escribir' or accion = 'verif_temu'", " ".join(inspect.getsource(V.matriz).split()))
        self.assertIn('("escribir", "verif_temu")', inspect.getsource(V.historia))
        self.assertEqual(F.ACC_VERIF_TEMU, "verif_temu")
        self.assertNotIn(F.ACC_VERIF_TEMU, V._ACC_FANOUT, "no es un evento del fan-out")
        self.assertNotIn(F.ACC_VERIF_TEMU, R._ACC_FANOUT, "el recuperador no la cuenta como reparto")

    def test_la_celda_toma_la_verificacion(self):
        L = {"canal": "temu", "status": "2/8", "stock_own": 5, "updated_at": self.AHORA - timedelta(hours=1),
             "act": "2026-10-12 12:50:00"}
        ok = {"ts": self.AHORA, "hora": "2026-10-12 13:50:00", "accion": "verif_temu", "objetivo": 3,
              "stock_canal": 3, "resultado": f"ok ({F._TEMU_DIFERIDA}: corregido 2→3)"}
        self.assertEqual(V._celda_matriz(L, ok, 3, "2026-10-12")["k"], "igual")
        mal = dict(ok, stock_canal=2, resultado=f"ERROR ({F._TEMU_DIFERIDA}): no convergió")
        self.assertEqual(V._celda_matriz(L, mal, 3, "2026-10-12")["k"], "rech")


if __name__ == "__main__":
    unittest.main()
