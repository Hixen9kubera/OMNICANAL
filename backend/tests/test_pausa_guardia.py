"""Pruebas de la GUARDIA DE LA PAUSA (`services/pausa_guardia.py`) y sus candados.

── QUÉ FIJAN ────────────────────────────────────────────
Brandon, 14-oct-2026: «¿puedes volver a checar si tenemos algún producto encendido? Si
es así apágalo y genera una seguridad para que no se vuelvan a prender». Media hora
después de apagar TikTok y ponerle la pausa, alguien activó 162 productos desde fuera de
omnicanal y el vigilante del censo (cada hora, tope de 60) tardó tres horas en apagarlos.

  1. SÓLO CON PAUSA: sin pausa la guardia no le pregunta nada al canal; con un «no sé»
     tampoco apaga.
  2. LEE EL CANAL EN VIVO, no el censo: TikTok por la búsqueda con `status: ACTIVATE`,
     Temu por la cubeta 1. Lo que no vende no se toca; a la retenida de Temu que ya
     recibió la orden no se le repite.
  3. SIN TOPE, de 10 en 10, primero lo que vende; firmado «pausa del canal».
  4. UN BARRIDO POR CANAL A LA VEZ (lo comparte con el vigilante del censo).
  5. EL AVISO DE TIKTOK (tipo 37) programa UN barrido tras la ráfaga, sólo con pausa.
  6. CANDADOS PROPIOS: con el canal en pausa omnicanal publica APAGADO (TikTok como
     borrador; Temu se apaga al nacer; desde el 20-oct) y no prende
     (`fanout_seguro._prender`); con un «no sé», no publica.
  7. Está colgada del scheduler cada 10 min y del webhook de TikTok.

── NO SE TOCA NADA REAL ─────────────────────────────────
La base y la red revientan si se tocan. SKUs INVENTADOS.

    cd backend && python -m unittest tests.test_pausa_guardia -v
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import fanout_seguro as S  # noqa: E402
from services import pausa_guardia as G  # noqa: E402
from services import publicaciones_lote as L  # noqa: E402

_TRAMPAS: list = []


def setUpModule():
    import httpx

    from services import supabase_db as sdb

    def trampa(nombre):
        def f(*a, **k):
            raise AssertionError(f"la prueba tocó {nombre}: falta un doble")
        return f

    for mod, nombres in ((sdb, ("fetch_all", "fetch_one", "execute", "get_cursor")),
                         (httpx, ("AsyncClient", "get", "post"))):
        for n in nombres:
            p = mock.patch.object(mod, n, side_effect=trampa(f"{mod.__name__}.{n}"))
            p.start()
            _TRAMPAS.append(p)


def tearDownModule():
    for p in _TRAMPAS:
        p.stop()


EN_PAUSA = {"en_pausa": True, "por": "jefe@kubera.mx", "desde": "2026-10-14 10:34", "motivo": None,
            "legible": True}
SIN_PAUSA = {"en_pausa": False, "por": None, "desde": None, "motivo": None, "legible": True}
NO_SE = {"en_pausa": None, "por": None, "desde": None, "motivo": None, "legible": False}


class Arnes(unittest.TestCase):
    """Un canal falso EN VIVO. `self.vivos[canal]` es lo que el canal enseña ahora."""

    def setUp(self):
        self.pausas = {"temu": dict(SIN_PAUSA), "tiktok": dict(SIN_PAUSA)}
        self.vivos: dict[str, list[dict]] = {"temu": [], "tiktok": []}
        self.estado: dict[str, str] = {}          # listing_id → estado vivo
        self.lecturas: list[str] = []             # qué canal se leyó en vivo
        self.apagadas: list[tuple] = []
        self.anotadas: list[dict] = []
        self.panel: dict[str, dict] = {}
        self.avisos: list[tuple] = []
        self.lee_mal: set[str] = set()

        def vivos(canal):
            def f():
                self.lecturas.append(canal)
                if canal in self.lee_mal:
                    raise RuntimeError("ConnectError")
                return [dict(v, estado=self.estado[v["listing_id"]]) for v in self.vivos[canal]]
            return f

        def leer(canal, item):
            return {"ok": True, "estado": self.estado[item], "stock": 5, "skus": []}

        def apagar(canal, item, sku_ids=None):
            self.apagadas.append((canal, item))
            self.estado[item] = S._APAGADO_A_MANO[canal]
            return {"success": True}

        from services import alertas
        parches = [
            mock.patch.dict(G._VIVOS, {"temu": vivos("temu"), "tiktok": vivos("tiktok")}),
            mock.patch.object(L, "pausa", side_effect=lambda c, refrescar=False: dict(self.pausas[c])),
            mock.patch.object(L, "_del_panel", side_effect=lambda c: dict(self.panel)),
            mock.patch.object(L, "_publicaciones", side_effect=AssertionError(
                "la guardia no busca en channel.listings: apaga lo que vio en vivo")),
            mock.patch.object(L, "_reflejar", side_effect=lambda *a: None),
            mock.patch.object(L, "_soltar_marca", side_effect=lambda *a: False),
            mock.patch.object(L, "_anotar", side_effect=lambda canal, r, lote, actor, seg: self.anotadas.append(
                dict(r, canal=canal, actor=actor))),
            mock.patch.object(S, "_leer_vivo", side_effect=leer),
            mock.patch.object(S, "_apagar", side_effect=apagar),
            mock.patch.object(S, "_prender", side_effect=AssertionError("la guardia NUNCA prende")),
            mock.patch.object(S, "_dormir", side_effect=lambda s: None),
            mock.patch.multiple(S.settings, fanout_cero_espera_s=1.0, fanout_cero_relecturas=2),
            mock.patch.object(alertas, "avisar",
                              side_effect=lambda tipo, texto, nivel="🔴": self.avisos.append((tipo, texto, nivel)) or True),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        S._candados.clear()

    def vivo(self, canal, sku, item, estado):
        self.vivos[canal].append({"sku": sku, "listing_id": item})
        self.estado[item] = estado


class Barrer(Arnes):

    def test_sin_pausa_no_le_pregunta_nada_al_canal(self):
        self.vivo("tiktok", "ZZZ-0001-NEG", "1701", "ACTIVATE")
        self.assertEqual(G.barrer("tiktok"), {"ok": True, "pausa": False})
        self.assertEqual((self.lecturas, self.apagadas), ([], []))

    def test_un_no_se_no_apaga(self):
        self.pausas["tiktok"] = dict(NO_SE)
        self.vivo("tiktok", "ZZZ-0001-NEG", "1701", "ACTIVATE")
        r = G.barrer("tiktok")
        self.assertEqual((r["ok"], r["pausa"]), (False, None))
        self.assertEqual((self.lecturas, self.apagadas), ([], []))

    def test_tiktok_en_pausa_apaga_lo_que_vende_y_avisa(self):
        self.pausas["tiktok"] = dict(EN_PAUSA)
        self.vivo("tiktok", "ZZZ-0001-NEG", "1701", "ACTIVATE")
        self.vivo("tiktok", "ZZZ-0002-NEG", "1702", "PENDING")       # en revisión: no se puede
        self.vivo("tiktok", "ZZZ-0003-NEG", "1703", "SELLER_DEACTIVATED")
        r = G.barrer("tiktok")
        self.assertEqual(self.apagadas, [("tiktok", "1701")])
        self.assertEqual((r["vistos"], r["por_apagar"], r["resumen"], r["reaparecidas"]),
                         (3, 1, {L.APAGADA: 1}, ["ZZZ-0001-NEG"]))
        self.assertEqual(self.anotadas[0]["actor"], L.ACTOR_PAUSA)
        ((tipo, _texto, nivel),) = self.avisos
        self.assertEqual((tipo, nivel), ("canal_pausa_reaparecio:tiktok", "🟡"))

    def test_temu_apaga_lo_que_vende_o_vuelve_solo_y_respeta_la_orden_ya_enviada(self):
        self.pausas["temu"] = dict(EN_PAUSA)
        self.vivo("temu", "AAA-0001-AZL", "9001", "3/1")      # agotada: vuelve sola
        self.vivo("temu", "ZZZ-0002-AZL", "9002", "2/8")      # a la venta
        self.vivo("temu", "ZZZ-0003-AZL", "9003", "3/3")      # retenida, ya recibió la orden
        self.vivo("temu", "ZZZ-0004-AZL", "9004", "3/3")      # retenida, nunca la recibió
        self.vivo("temu", "ZZZ-0005-AZL", "9005", "3/2")      # apagada
        self.panel = {"9003": {"accion": "desactivar", "estado": "orden_enviada", "despues": "3/3"}}
        G.barrer("temu")
        self.assertEqual([i for _, i in self.apagadas], ["9002", "9001", "9004"],
                         "primero lo que vende; a la que ya tiene su orden no se le repite")

    def test_sin_tope_y_de_diez_en_diez(self):
        self.pausas["tiktok"] = dict(EN_PAUSA)
        for i in range(218):
            self.vivo("tiktok", f"ZZZ-{i:04d}-NEG", f"17{i:04d}", "ACTIVATE")
        tandas = []
        real = L.desactivar_publicaciones

        def contar(canal, pubs, actor=""):
            tandas.append(len(pubs))
            return real(canal, pubs, actor=actor)

        with mock.patch.object(L, "desactivar_publicaciones", side_effect=contar):
            r = G.barrer("tiktok")
        self.assertEqual(len(self.apagadas), 218, "las 218 del 14-oct, en un solo barrido")
        self.assertEqual(tandas, [10] * 21 + [8])
        self.assertEqual((r["por_apagar"], r["atendidas"]), (218, 218))

    def test_apaga_aunque_el_censo_no_la_conozca(self):
        # Un SKU que no está en channel.listings (o publicado sin catálogo) también se apaga:
        # `_publicaciones` revienta en el arnés si se consulta.
        self.pausas["tiktok"] = dict(EN_PAUSA)
        self.vivo("tiktok", "", "1799", "ACTIVATE")
        G.barrer("tiktok")
        self.assertEqual(self.apagadas, [("tiktok", "1799")])
        self.assertEqual(self.anotadas[0]["sku"], "(sin SKU · 1799)")

    def test_si_no_puede_leer_el_canal_no_hace_nada_y_lo_dice(self):
        self.pausas["temu"] = dict(EN_PAUSA)
        self.lee_mal.add("temu")
        r = G.barrer("temu")
        self.assertEqual((r["ok"], r["pausa"]), (False, True))
        self.assertIn("en vivo", r["motivo"])
        self.assertEqual(self.apagadas, [])

    def test_un_barrido_por_canal_a_la_vez(self):
        self.pausas["tiktok"] = dict(EN_PAUSA)
        self.vivo("tiktok", "ZZZ-0001-NEG", "1701", "ACTIVATE")
        with L._turno("tiktok") as mio:
            self.assertTrue(mio)
            r = G.barrer("tiktok")
            v = L.vigilar("tiktok")
        self.assertEqual((r.get("ocupado"), v.get("ocupado")), (True, True))
        self.assertEqual((self.lecturas, self.apagadas), ([], []))
        with L._turno("temu") as mio:
            self.assertTrue(mio, "el turno es POR canal")
            G.barrer("tiktok")
        self.assertEqual(self.apagadas, [("tiktok", "1701")])

    def test_barrer_todo_solo_reporta_los_canales_en_pausa_y_uno_no_tumba_al_otro(self):
        self.pausas["tiktok"] = dict(EN_PAUSA)
        self.vivo("tiktok", "ZZZ-0001-NEG", "1701", "ACTIVATE")
        self.assertEqual(set(G.barrer_todo()), {"tiktok"})
        self.pausas["temu"] = dict(EN_PAUSA)
        real = G.barrer
        with mock.patch.object(G, "barrer", side_effect=lambda c, *a: (_ for _ in ()).throw(RuntimeError("x"))
                               if c == "temu" else real(c, *a)):
            r = G.barrer_todo()
        self.assertEqual((r["temu"]["ok"], r["tiktok"]["ok"]), (False, True))


class LecturasVivas(unittest.TestCase):
    """Las dos lecturas EN VIVO, contra un canal falso."""

    def test_tiktok_lee_todo_el_catalogo_sin_filtro_y_pagina(self):
        # Sin `status` en la búsqueda (v0.647.0): un filtro que contestara «0» siempre
        # dejaría ciega a la guardia, y ese 0 no se distingue de «no hay nada a la venta».
        from services import tiktok as tk
        llamadas = []
        paginas = {"": {"products": [{"id": "1", "status": "ACTIVATE", "skus": [{"seller_sku": "ZZZ-1"}]},
                                     {"id": "2", "status": "SELLER_DEACTIVATED", "skus": [{"seller_sku": "ZZZ-2"}]}],
                        "next_page_token": "t2"},
                   "t2": {"products": [{"id": "2", "status": "SELLER_DEACTIVATED", "skus": [{"seller_sku": "ZZZ-2"}]},
                                       {"id": "3", "status": "activate", "skus": []}],
                          "next_page_token": ""}}

        async def llamar(ruta, token, params=None, cuerpo=None, metodo="GET"):
            llamadas.append((ruta, dict(params), cuerpo, metodo))
            return paginas[params.get("page_token", "")]

        with mock.patch.object(tk, "access_token", return_value="tok"), \
                mock.patch.object(tk, "cipher", return_value="ciph"), \
                mock.patch.object(tk, "llamar", side_effect=llamar):
            vivos = G._vivos_tiktok()
        self.assertEqual(vivos, [{"sku": "ZZZ-1", "listing_id": "1", "estado": "ACTIVATE"},
                                 {"sku": "ZZZ-2", "listing_id": "2", "estado": "SELLER_DEACTIVATED"},
                                 {"sku": "", "listing_id": "3", "estado": "ACTIVATE"}])
        self.assertEqual([(r, c, m) for r, _, c, m in llamadas],
                         [("/product/202309/products/search", {}, "POST")] * 2,
                         "la misma lectura que el censo: sin filtro de estado")
        self.assertEqual(llamadas[1][1]["page_token"], "t2")

    def test_de_todo_el_catalogo_solo_se_apaga_lo_que_vende(self):
        with mock.patch.object(L, "_del_panel", return_value={}):
            pendientes = G._por_apagar("tiktok", [
                {"sku": "ZZZ-1", "listing_id": "1", "estado": "ACTIVATE"},
                {"sku": "ZZZ-2", "listing_id": "2", "estado": "SELLER_DEACTIVATED"},
                {"sku": "ZZZ-3", "listing_id": "3", "estado": "DRAFT"},
                {"sku": "ZZZ-4", "listing_id": "4", "estado": "PENDING"}])
        self.assertEqual([p["listing_id"] for p in pendientes], ["1"])

    def test_tiktok_sin_token_no_inventa_una_lista_vacia(self):
        from services import tiktok as tk
        with mock.patch.object(tk, "access_token", return_value=None), \
                mock.patch.object(tk, "cipher", return_value=None):
            with self.assertRaises(RuntimeError):
                G._vivos_tiktok()

    def test_temu_lee_la_cubeta_uno(self):
        from services import temu as tm
        pedidas = []

        async def listar(cubetas=tm.CUBETAS):
            pedidas.append(cubetas)
            return [{"goodsId": 601, "outGoodsSn": "ZZZ-1", "status4VO": 2, "subStatus4VO": 8},
                    {"goodsId": 602, "outGoodsSn": "ZZZ-2", "status4VO": None, "subStatus4VO": None}]

        with mock.patch.object(tm, "disponible", return_value=True), \
                mock.patch.object(tm, "listar_productos", side_effect=listar):
            vivos = G._vivos_temu()
        self.assertEqual(pedidas, [(1,)])
        self.assertEqual(vivos, [{"sku": "ZZZ-1", "listing_id": "601", "estado": "2/8"}],
                         "sin estado legible no se decide nada")


class AvisoDeTikTok(unittest.TestCase):

    def setUp(self):
        G._aviso_hasta.clear()
        G._aviso_tarea.clear()
        self.addCleanup(G._aviso_hasta.clear)
        self.addCleanup(G._aviso_tarea.clear)

    def test_que_avisos_cuentan(self):
        self.assertTrue(G.es_aviso_de_producto({"type": 37, "data": {"audit": {"status": "APPROVED"}}}))
        for p in ({"type": 1}, {"type": 68}, {"type": "x"}, {}, None, [37]):
            self.assertFalse(G.es_aviso_de_producto(p), p)

    def test_con_pausa_programa_un_barrido(self):
        async def correr():
            with mock.patch.object(L, "en_pausa", return_value=True), \
                    mock.patch.object(G, "programar") as prog:
                await G.aviso_tiktok({"type": 37})
                await G.aviso_tiktok({"type": 1})
            return prog
        prog = asyncio.run(correr())
        prog.assert_called_once_with("tiktok")

    def test_sin_pausa_o_con_no_se_no_programa_nada(self):
        for valor in (False, None):
            async def correr():
                with mock.patch.object(L, "en_pausa", return_value=valor), \
                        mock.patch.object(G, "programar") as prog:
                    await G.aviso_tiktok({"type": 37})
                return prog
            asyncio.run(correr()).assert_not_called()

    def test_jamas_lanza(self):
        async def correr():
            with mock.patch.object(L, "en_pausa", side_effect=RuntimeError("kubera caída")):
                await G.aviso_tiktok({"type": 37})
        asyncio.run(correr())

    def test_una_rafaga_es_un_solo_barrido(self):
        barridos = []

        def barrer(canal, motivo=""):
            barridos.append((canal, motivo))
            return {"ok": True, "pausa": True}

        async def correr():
            with mock.patch.object(G, "barrer", side_effect=barrer), \
                    mock.patch.object(L, "en_pausa", return_value=True):
                for _ in range(300):
                    await G.aviso_tiktok({"type": 37})
                    G._aviso_hasta["tiktok"] = G.time.monotonic() + 0.05
                await G._aviso_tarea["tiktok"]
        asyncio.run(correr())
        self.assertEqual(barridos, [("tiktok", "aviso de TikTok")])

    def test_si_otro_barrido_iba_a_la_mitad_vuelve_a_intentar(self):
        respuestas = [{"ok": True, "pausa": True, "ocupado": True}, {"ok": True, "pausa": True}]
        barridos = []

        def barrer(canal, motivo=""):
            barridos.append(canal)
            return respuestas.pop(0)

        async def correr():
            with mock.patch.object(G, "barrer", side_effect=barrer), \
                    mock.patch.object(G.asyncio, "sleep", side_effect=lambda s: asyncio.sleep(0)), \
                    mock.patch.object(G.time, "monotonic", side_effect=[0.0, 1000.0, 1000.0, 2000.0, 2000.0,
                                                                        3000.0, 3000.0, 4000.0]):
                G._aviso_hasta["tiktok"] = 0.0
                await G._barrer_tras_avisos("tiktok")
        asyncio.run(correr())
        self.assertEqual(barridos, ["tiktok", "tiktok"])


class Colgado(unittest.TestCase):
    """Que la guardia de verdad corra: scheduler, webhook y regla 11."""

    def test_el_scheduler_la_corre_cada_diez_minutos_sin_variable(self):
        from services import scheduler
        fuente = inspect.getsource(scheduler)
        i = fuente.index("pausa_guardia.job")
        bloque = fuente[fuente.rfind("\n    from services import pausa_guardia", 0, i):i + 400]
        self.assertIn("minutes=pausa_guardia.CADA_MIN", bloque)
        self.assertIn('id="pausa_guardia"', bloque)
        self.assertNotIn("settings.", bloque.split("_scheduler.add_job")[0],
                         "sin interruptor de entorno: el interruptor es la pausa")
        self.assertEqual(G.CADA_MIN, 10)

    def test_el_job_y_el_aviso_no_bloquean_el_loop(self):
        # Regla 11: lo síncrono (kubera, el canal) va en hilo.
        self.assertTrue(inspect.iscoroutinefunction(G.job))
        self.assertIn("asyncio.to_thread(barrer_todo)", inspect.getsource(G.job))
        self.assertIn("asyncio.to_thread(L.en_pausa", inspect.getsource(G.aviso_tiktok))
        self.assertIn("asyncio.to_thread(barrer", inspect.getsource(G._barrer_tras_avisos))
        self.assertFalse(inspect.iscoroutinefunction(G.barrer))

    def test_el_webhook_de_tiktok_la_dispara_en_segundo_plano(self):
        from routers import webhooks as W

        class Peticion:
            def __init__(self, cuerpo):
                self._c = cuerpo
                self.headers = {"content-type": "application/json"}

            async def body(self):
                return self._c

        def correr(cuerpo, firma):
            from fastapi import BackgroundTasks
            bg = BackgroundTasks()
            with mock.patch.object(W, "_firma_tiktok_ok", return_value=firma), \
                    mock.patch.object(W, "_persistir_evento", return_value=None), \
                    mock.patch.object(W.settings, "pedidos_tiktok_enabled", False, create=True):
                r = asyncio.run(W.recibir_tiktok(Peticion(cuerpo), bg))
            self.assertEqual(r, {"code": 0, "message": "success"})
            return [t.func for t in bg.tasks]

        aviso = b'{"type": 37, "data": {"audit": {"status": "APPROVED"}, "product_id": 1}}'
        self.assertEqual(correr(aviso, True), [G.aviso_tiktok])
        self.assertEqual(correr(aviso, None), [G.aviso_tiktok], "firma no evaluable: sólo dispara una LECTURA")
        self.assertEqual(correr(aviso, False), [], "con firma inválida, nada")
        self.assertEqual(correr(b'{"type": 1, "data": {"order_id": "9"}}', True), [])


class PublicarApagado(unittest.TestCase):
    """Con TikTok/Temu en pausa el Publicador PUBLICA, pero apagado (20-oct-2026). Antes
    (v0.646) la pausa lo bloqueaba entero."""

    def _confirmar(self, canal, estado, **extra):
        from services import publicar as P
        siguio = []

        async def rellenar(req):
            siguio.append(dict(req))
            raise RuntimeError("siguió de largo")

        with mock.patch.object(L, "pausa", side_effect=lambda c, refrescar=False: dict(estado)), \
                mock.patch.object(P, "_rellenar_desde_guardado", side_effect=rellenar):
            try:
                r = asyncio.run(P.confirmar({"canal": canal, "sku": "ZZZ-0001-NEG", **extra}))
            except RuntimeError:
                r = None
        return r, siguio

    def test_en_pausa_sigue_y_marca_apagado(self):
        for canal in ("tiktok", "temu"):
            r, siguio = self._confirmar(canal, EN_PAUSA)
            self.assertIsNone(r, canal)
            self.assertEqual(len(siguio), 1, canal)
            self.assertIs(siguio[0]["_publicar_apagado"], True, canal)
        self.assertEqual(self._confirmar("tiktok", EN_PAUSA)[1][0]["save_mode"], "AS_DRAFT")
        self.assertNotIn("save_mode", self._confirmar("temu", EN_PAUSA)[1][0])

    def test_la_pantalla_no_puede_pedir_que_salga_a_la_venta(self):
        _, siguio = self._confirmar("tiktok", EN_PAUSA, save_mode="LISTING", _publicar_apagado=False)
        self.assertEqual((siguio[0]["save_mode"], siguio[0]["_publicar_apagado"]), ("AS_DRAFT", True),
                         "lo decide el servidor, no la petición")

    def test_con_un_no_se_no_publica(self):
        r, siguio = self._confirmar("tiktok", NO_SE)
        self.assertEqual((siguio, r["ok"]), ([], False))
        self.assertIn("No se pudo confirmar", r["motivo"])

    def test_sin_pausa_publica_como_siempre(self):
        _, siguio = self._confirmar("tiktok", SIN_PAUSA, save_mode="LISTING")
        self.assertEqual((siguio[0]["_publicar_apagado"], siguio[0]["save_mode"]), (False, "LISTING"))

    def test_los_demas_canales_ni_preguntan(self):
        from services import publicar as P
        with mock.patch.object(P, "_pausa_publicar", side_effect=AssertionError("no aplica")), \
                mock.patch.object(P, "_rellenar_desde_guardado", side_effect=RuntimeError("siguió")):
            for canal in ("mercado_libre", "amazon", "walmart"):
                with self.assertRaises(RuntimeError):
                    asyncio.run(P.confirmar({"canal": canal, "sku": "ZZZ-0001-NEG"}))

    def test_la_vista_previa_dice_que_nace_apagado(self):
        from services import publicar as P
        vistos = []

        async def canal_prev(canal, req):
            vistos.append(dict(req))
            return {"ok": True, "canal": canal, "avisos": ["otro"]}

        async def nada(req):
            return None

        async def sin_avisos(req):
            return []

        with mock.patch.object(L, "pausa", side_effect=lambda c, refrescar=False: dict(EN_PAUSA)), \
                mock.patch.object(P, "_rellenar_desde_guardado", side_effect=nada), \
                mock.patch.object(P, "_asegurar_wc_id", side_effect=nada), \
                mock.patch.object(P, "_candado_padre", side_effect=sin_avisos), \
                mock.patch.object(P, "_aviso_imagenes", side_effect=nada), \
                mock.patch.object(P, "_aviso_titulo", return_value=None), \
                mock.patch.object(P, "_preview_canal", side_effect=canal_prev):
            r = asyncio.run(P.preview({"canal": "tiktok", "sku": "ZZZ-0001-NEG"}))
        self.assertEqual(vistos[0]["save_mode"], "AS_DRAFT", "la vista previa enseña el borrador")
        self.assertTrue(r["publicar_apagado"])
        self.assertIn("BORRADOR", r["avisos"][0])


class TikTokEnPausa(unittest.TestCase):
    """`publicar_tiktok.confirmar` con el canal en pausa."""

    def _correr(self, item_id=None, vivo=None):
        from services import fanout_seguro
        from services import publicar_tiktok as PT
        from services import tiktok as tk
        llamadas = []

        async def armar(req, token, cipher, subir_imagenes=True):
            return {"payload": {"save_mode": req.get("save_mode") or "LISTING", "skus": []},
                    "categoria_id": "1", "imagenes": 0}

        async def llamar(ruta, token, params=None, cuerpo=None, metodo="GET"):
            llamadas.append((ruta, metodo, dict(cuerpo or {})))
            return {"product_id": item_id or "1799"}

        with mock.patch.object(tk, "access_token", return_value="tok"), \
                mock.patch.object(tk, "cipher", return_value="ciph"), \
                mock.patch.object(tk, "llamar", side_effect=llamar), \
                mock.patch.object(PT, "_armar", side_effect=armar), \
                mock.patch.object(PT, "_listing_id", return_value=item_id), \
                mock.patch.object(PT, "_registrar", side_effect=lambda *a, **k: None), \
                mock.patch.object(PT, "_reflejar", side_effect=lambda *a, **k: None), \
                mock.patch.object(fanout_seguro, "_leer_vivo", return_value=vivo or {"ok": False}):
            r = asyncio.run(PT.confirmar({"sku": "ZZZ-0001-NEG", "_publicar_apagado": True,
                                          "save_mode": "LISTING"}))
        return r, llamadas

    def test_lo_nuevo_nace_borrador(self):
        r, llamadas = self._correr()
        self.assertEqual(len(llamadas), 1)
        ruta, metodo, cuerpo = llamadas[0]
        self.assertEqual((ruta, metodo, cuerpo["save_mode"]), ("/product/202309/products", "POST", "AS_DRAFT"))
        self.assertTrue(r["ok"] and r["apagado"])

    def test_uno_que_estuvo_a_la_venta_no_se_toca(self):
        for estado in ("ACTIVATE", "SELLER_DEACTIVATED", "PLATFORM_DEACTIVATED"):
            r, llamadas = self._correr("1701", {"ok": True, "estado": estado})
            self.assertEqual((r["ok"], llamadas), (False, []), estado)
            self.assertIn("EN PAUSA", r["motivo"])
        r, llamadas = self._correr("1701", {"ok": False, "motivo": "x"})
        self.assertEqual((r["ok"], llamadas), (False, []), "sin poder leerlo, tampoco")

    def test_un_borrador_si_se_edita_como_borrador(self):
        r, llamadas = self._correr("1701", {"ok": True, "estado": "DRAFT"})
        self.assertEqual(len(llamadas), 1)
        ruta, metodo, cuerpo = llamadas[0]
        self.assertEqual((ruta, metodo, cuerpo["save_mode"]), ("/product/202309/products/1701", "PUT", "AS_DRAFT"))
        self.assertTrue(r["ok"])


class TemuEnPausa(unittest.TestCase):

    def test_el_alta_se_apaga_al_nacer(self):
        from services import fanout_seguro
        from services import publicar_temu as PM
        with mock.patch.object(fanout_seguro, "_apagar", return_value={"success": True}) as ap:
            r = asyncio.run(PM._apagar_recien_creado("ZZZ-0001-NEG", "601"))
        ap.assert_called_once_with("temu", "601")
        self.assertEqual((r["pedido"], r["ok"]), (True, True))

    def test_si_temu_no_la_acepta_todavia_no_truena(self):
        from services import fanout_seguro
        from services import publicar_temu as PM
        with mock.patch.object(fanout_seguro, "_apagar", side_effect=RuntimeError("en revisión")):
            r = asyncio.run(PM._apagar_recien_creado("ZZZ-0001-NEG", "601"))
        self.assertEqual((r["pedido"], r["ok"]), (True, False))
        self.assertIn("guardia", r["detalle"])

    def test_confirmar_la_apaga_solo_en_pausa(self):
        from services import publicar_temu as PM
        fuente = inspect.getsource(PM.confirmar)
        self.assertIn('await _apagar_recien_creado(sku, goods_id) if req.get("_publicar_apagado") else None',
                      fuente)


class CandadoDePrender(unittest.TestCase):

    def test_en_pausa_o_con_no_se_no_prende(self):
        from services import fanout_stock
        with mock.patch.object(S, "_canal_en_pausa", return_value=True), \
                mock.patch.object(fanout_stock, "_en_hilo", side_effect=AssertionError("llamó al canal")):
            for canal in ("temu", "tiktok"):
                with self.assertRaises(RuntimeError):
                    S._prender(canal, "1701")

    def test_sin_pausa_si_llega_al_canal(self):
        from services import fanout_stock
        with mock.patch.object(S, "_canal_en_pausa", return_value=False), \
                mock.patch.object(fanout_stock, "_en_hilo", return_value={"ok": True}) as hilo:
            S._prender("temu", "601")
        self.assertEqual(hilo.call_count, 1)

    def test_el_candado_pregunta_por_la_pausa_y_el_no_se_es_pausa(self):
        # `_canal_en_pausa` es el mismo que ya protege `_reactivar`: «no sé» = pausa.
        with mock.patch.object(L, "en_pausa", return_value=None):
            self.assertTrue(S._canal_en_pausa("tiktok"))
        with mock.patch.object(L, "en_pausa", side_effect=RuntimeError("kubera caída")):
            self.assertTrue(S._canal_en_pausa("tiktok"))


if __name__ == "__main__":
    unittest.main()
