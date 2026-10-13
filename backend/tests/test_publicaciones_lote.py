"""Pruebas de DESACTIVAR EN LOTE (`services/publicaciones_lote.py` y sus dos rutas).

── QUÉ FIJAN ────────────────────────────────────────────
Brandon, 13-oct-2026: «necesito por el momento apagar todo Temu» (el stock de almacén
no es de fiar) y poder hacerlo desde el Publicador: escoger un canal, ver sus SKUs
publicados, seleccionar un lote o TODOS y desactivarlos.

  1. INACTIVA, NO BORRA: lo único que mueve algo es `fanout_seguro._apagar` (las dos
     llamadas probadas en vivo el 9-oct); nunca prende ni borra.
  2. SE DECIDE CON EL ESTADO VIVO: la que en vivo ya está apagada, o no está
     publicada, no se llama; la que no se puede leer, tampoco.
  3. SE CONFIRMA RELEYENDO: «apagada» sólo si el canal la enseña fuera de la venta.
     Si no, «sin confirmar», y no se da por hecho. Lo que el canal tiene RETENIDO
     (Temu `3/3`) acepta la orden y no cambia de estado: es «orden enviada».
  4. UNA ESPERA POR TANDA, no una por publicación.
  5. LO QUE UNA PERSONA APAGA SE QUEDA APAGADO: se suelta la marca del seguro y el
     cambio queda firmado `panel_lote`.
  6. QUEDA ANOTADO quién apagó qué y en qué estado estaba (para poder regresarlo).
  7. SÓLO ADMIN, a lo mucho 10 SKUs por llamada, y sólo Temu y TikTok.

── NO SE TOCA NADA REAL ─────────────────────────────────
El canal, la base, la bitácora y el reloj van suplantados. SKUs INVENTADOS.

    cd backend && python -m unittest tests.test_publicaciones_lote -v
"""
from __future__ import annotations

import asyncio
import inspect
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException  # noqa: E402

from routers import publicaciones as RUTAS  # noqa: E402
from services import fanout_seguro as S  # noqa: E402
from services import publicaciones_lote as L  # noqa: E402

_TRAMPAS: list = []


def setUpModule():
    """Mientras corre este módulo, tocar la base o la red revienta."""
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


def fila(sku, item, status, canal="temu", stock=5, woo=5):
    return {"sku": sku, "listing_id": item, "status": status, "stock_own": stock, "price": 199.0,
            "url": None, "cuenta": "TEMU" if canal == "temu" else "KUBERA",
            "titulo": f"Producto {sku}", "stock_woo": woo, "visto": "2026-10-13 11:44"}


class Arnes(unittest.TestCase):
    """Un canal falso. Todo lo que pasa queda en `self.orden`, EN ORDEN."""

    def setUp(self):
        self.orden: list[tuple] = []
        self.vivo: dict[str, str] = {}             # item → estado vivo
        self.censo: list[dict] = []                # lo que diría channel.listings
        self.ilegibles: set[str] = set()
        self.rechaza: set[str] = set()             # items cuya llamada revienta
        self.no_obedece: set[str] = set()          # el canal dice ok y no cambia nada
        self.al_apagar: dict[str, str] = {}        # item → estado en que lo deja (omisión: el de «apagada»)
        self.marcas: set[str] = set()              # items con marca vigente del seguro
        self.reflejos: list[tuple] = []
        self.anotadas: list[dict] = []
        self.dormidas: list[float] = []
        self.sueltas: list[tuple] = []
        self.canal = "temu"

        def leer(canal, item):
            self.orden.append(("leer", item))
            if item in self.ilegibles:
                return {"ok": False, "motivo": "ConnectError"}
            return {"ok": True, "estado": self.vivo[item], "stock": 5, "skus": []}

        def apagar(canal, item, sku_ids=None):
            self.orden.append(("apagar", item))
            if item in self.rechaza:
                raise RuntimeError("Temu bg.local.goods.sale.status.set: errorCode=3000032")
            if item not in self.no_obedece:
                self.vivo[item] = self.al_apagar.get(item, S._APAGADO_A_MANO[canal])
            return {"success": True}

        def dormir(s):
            self.orden.append(("dormir", s))
            self.dormidas.append(s)

        def soltar(sku, canal):
            self.sueltas.append((sku, canal))
            return {"ok": True}

        def publicaciones(canal, skus=None):
            if skus is None:
                return [dict(f) for f in self.censo]
            quiero = {s.upper() for s in skus}
            return [dict(f) for f in self.censo if f["sku"].upper() in quiero]

        parches = [
            mock.patch.object(S, "_leer_vivo", side_effect=leer),
            mock.patch.object(S, "_apagar", side_effect=apagar),
            mock.patch.object(S, "_prender", side_effect=AssertionError("desactivar NUNCA prende")),
            mock.patch.object(S, "_dormir", side_effect=dormir),
            mock.patch.object(S, "marca", side_effect=lambda c, i: ({"item_id": i} if i in self.marcas else None)),
            mock.patch.object(S, "soltar", side_effect=soltar),
            mock.patch.object(S, "marcas_vigentes",
                              side_effect=lambda c=None: [{"item_id": i} for i in sorted(self.marcas)]),
            mock.patch.multiple(S.settings, fanout_cero_espera_s=8.0, fanout_cero_relecturas=3),
            mock.patch.object(L, "_publicaciones", side_effect=publicaciones),
            mock.patch.object(L, "_reflejar", side_effect=lambda *a: self.reflejos.append(a)),
            mock.patch.object(L, "_anotar",
                              side_effect=lambda canal, r, lote, actor, seg: self.anotadas.append(
                                  dict(r, canal=canal, lote=lote, actor=actor))),
            mock.patch.object(L, "_del_panel", side_effect=lambda c: dict(self.panel)),
        ]
        self.panel: dict[str, dict] = {}
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        S._candados.clear()

    def poner(self, sku, item, status, vivo=None, canal="temu"):
        self.censo.append(fila(sku, item, status, canal))
        self.vivo[item] = status if vivo is None else vivo

    def llamadas(self):
        return [o for o in self.orden if o[0] == "apagar"]


# ══════════════════════════════════════════════════════════════════════════════

class Clasificar(unittest.TestCase):
    def test_temu(self):
        casos = {"2/8": ("a_la_venta", True), "3/1": ("publicada", True), "3/3": ("publicada", True),
                 "2/4": ("publicada", True), "3/2": ("apagada", False), "4/7": ("sin_publicar", False),
                 "5/None": ("sin_publicar", False)}
        for estado, (grupo, desactivable) in casos.items():
            c = L.clasificar("temu", estado)
            self.assertEqual((c["grupo"], c["desactivable"]), (grupo, desactivable), estado)

    def test_tiktok_sin_distinguir_mayusculas(self):
        casos = {"ACTIVATE": ("a_la_venta", True), "activate": ("a_la_venta", True),
                 "PENDING": ("en_revision", False), "SELLER_DEACTIVATED": ("apagada", False),
                 "PLATFORM_DEACTIVATED": ("apagada_canal", False), "FAILED": ("sin_publicar", False),
                 "DRAFT": ("sin_publicar", False), "DELETED": ("borrada", False)}
        for estado, (grupo, desactivable) in casos.items():
            c = L.clasificar("tiktok", estado)
            self.assertEqual((c["grupo"], c["desactivable"]), (grupo, desactivable), estado)

    def test_un_estado_que_nadie_conoce_no_se_toca(self):
        for canal, estado in (("temu", "9/9"), ("tiktok", "NUEVO"), ("temu", None), ("temu", "")):
            c = L.clasificar(canal, estado)
            self.assertEqual((c["grupo"], c["desactivable"]), ("desconocido", False))
        self.assertEqual(L.clasificar("temu", "9/9")["etiqueta"], "Temu 9/9")

    def test_el_estado_apagada_es_el_mismo_del_seguro(self):
        for canal, estado in S._APAGADO_A_MANO.items():
            self.assertEqual(L.clasificar(canal, estado)["grupo"], "apagada")
        self.assertEqual(L.CANALES, S.CANALES)

    def test_todo_grupo_declarado_se_cuenta(self):
        usados = {g for mapa in L._ESTADOS.values() for g, _ in mapa.values()} | {"desconocido"}
        self.assertEqual(usados, set(L._GRUPOS))


class Listar(Arnes):
    def test_la_lista_del_canal_con_su_resumen(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        self.poner("ZZZ-0002-AZL", "9002", "3/1")
        self.poner("ZZZ-0003-AZL", "9003", "3/2")
        self.poner("ZZZ-0004-AZL", "9004", "5/None")
        self.poner("ZZZ-0005-AZL", "9005", "9/9")
        r = L.listar("TEMU")
        self.assertEqual((r["canal"], r["nombre"], r["total"], r["desactivables"]), ("temu", "Temu", 5, 2))
        self.assertEqual({g: n for g, n in r["resumen"].items() if n},
                         {"a_la_venta": 1, "publicada": 1, "apagada": 1, "sin_publicar": 1, "desconocido": 1})
        primero = r["items"][0]
        self.assertEqual((primero["sku"], primero["listing_id"], primero["etiqueta"], primero["desactivable"],
                          primero["stock_canal"], primero["stock_woo"], primero["precio"]),
                         ("ZZZ-0001-AZL", "9001", "A la venta", True, 5, 5, 199.0))
        self.assertEqual((r["censo"], r["tope_por_llamada"]), ("2026-10-13 11:44", L.TOPE_POR_LLAMADA))
        self.assertEqual(self.orden, [], "listar no le pregunta nada al canal")

    def test_dice_quien_la_tiene_apagada(self):
        self.poner("ZZZ-0001-AZL", "9001", "3/2")      # la apagó el seguro
        self.poner("ZZZ-0002-AZL", "9002", "3/2")      # se apagó desde el panel
        self.poner("ZZZ-0003-AZL", "9003", "3/2")      # a mano en el Seller Center
        self.poner("ZZZ-0004-AZL", "9004", "2/8")      # el panel la apagó y alguien la volvió a prender
        self.marcas = {"9001"}
        ult = {"accion": "desactivar", "estado": "ok", "cuando": "2026-10-13 12:30",
               "actor": "alguien@kubera.mx", "antes": "2/8", "despues": "3/2"}
        self.panel = {"9002": ult, "9004": ult}
        por = {i["sku"]: i["apagada_por"] for i in L.listar("temu")["items"]}
        self.assertEqual(por["ZZZ-0001-AZL"]["quien"], "seguro")
        self.assertEqual((por["ZZZ-0002-AZL"]["quien"], por["ZZZ-0002-AZL"]["antes"],
                          por["ZZZ-0002-AZL"]["actor"]), ("panel", "2/8", "alguien@kubera.mx"))
        self.assertIsNone(por["ZZZ-0003-AZL"])
        self.assertIsNone(por["ZZZ-0004-AZL"], "ya no está apagada: no se le atribuye a nadie")

    def test_recuerda_a_cual_retenida_ya_se_le_mando_la_orden(self):
        # Medido el 13-oct-2026: Temu acepta `onsale: 0` en `3/3` y la sigue enseñando
        # en `3/3`. La lista no la puede dar por apagada, pero sí decir que ya se pidió.
        for i in range(4):
            self.poner(f"ZZZ-040{i}-AZL", f"940{i}", "3/3")
        self.poner("ZZZ-0404-AZL", "9404", "2/8")
        enviada = {"accion": "desactivar", "estado": "orden_enviada", "cuando": "2026-10-13 12:55",
                   "actor": "alguien@kubera.mx", "antes": "3/3", "despues": "3/3"}
        self.panel = {"9400": enviada,
                      "9401": dict(enviada, estado="sin_confirmar"),      # las de antes de esta versión
                      "9402": dict(enviada, despues="2/8"),               # se le pidió estando en otro estado
                      "9404": dict(enviada, estado="ok", despues="3/2")}  # apagada… y alguien la prendió
        r = L.listar("temu")
        orden = {i["sku"]: i["orden"] for i in r["items"]}
        self.assertEqual(orden["ZZZ-0400-AZL"], {"cuando": "2026-10-13 12:55", "actor": "alguien@kubera.mx"})
        self.assertIsNotNone(orden["ZZZ-0401-AZL"])
        self.assertIsNone(orden["ZZZ-0402-AZL"])
        self.assertIsNone(orden["ZZZ-0403-AZL"], "a ésta nadie le ha pedido nada")
        self.assertIsNone(orden["ZZZ-0404-AZL"], "está A LA VENTA: no hay orden que valga")
        self.assertEqual((r["desactivables"], r["por_apagar"]), (5, 3))
        self.assertTrue(all(i["desactivable"] for i in r["items"]), "se le puede volver a pedir")

    def test_si_la_bitacora_no_contesta_la_lista_sale_igual(self):
        self.poner("ZZZ-0001-AZL", "9001", "3/2")
        with mock.patch.object(S, "marcas_vigentes", side_effect=RuntimeError("kubera caída")), \
                mock.patch.object(L, "_del_panel", return_value={}):
            r = L.listar("temu")
        self.assertEqual(r["total"], 1)

    def test_canal_desconocido(self):
        for canal in ("mercado_libre", "amazon", "", None):
            with self.assertRaises(ValueError):
                L.listar(canal)
        self.assertEqual(L.canales(), [{"id": "temu", "nombre": "Temu"}, {"id": "tiktok", "nombre": "TikTok"}])


class Desactivar(Arnes):
    def test_la_saca_de_la_venta_y_lo_confirma_releyendo(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        r = L.desactivar("temu", ["ZZZ-0001-AZL"], actor="alguien@kubera.mx")
        self.assertEqual(self.orden, [("leer", "9001"), ("apagar", "9001"), ("dormir", 8.0), ("leer", "9001")])
        (uno,) = r["resultados"]
        self.assertEqual((uno["resultado"], uno["antes"], uno["despues"], uno["detalle"]),
                         (L.APAGADA, "2/8", "3/2", None))
        self.assertEqual(r["resumen"], {L.APAGADA: 1})
        self.assertEqual((r["canal"], r["pedidos"]), ("temu", 1))

    def test_queda_reflejado_y_anotado_con_quien_lo_pidio(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        r = L.desactivar("temu", ["ZZZ-0001-AZL"], actor="alguien@kubera.mx")
        self.assertEqual(self.reflejos, [("temu", "ZZZ-0001-AZL", "9001", "3/2")])
        (a,) = self.anotadas
        self.assertEqual((a["sku"], a["listing_id"], a["antes"], a["despues"], a["resultado"], a["actor"],
                          a["canal"], a["lote"]),
                         ("ZZZ-0001-AZL", "9001", "2/8", "3/2", L.APAGADA, "alguien@kubera.mx", "temu", r["lote"]))
        self.assertRegex(r["lote"], r"^[0-9a-f]{12}$")

    def test_tiktok(self):
        self.poner("ZZZ-0001-AZL", "7701", "ACTIVATE", canal="tiktok")
        r = L.desactivar("tiktok", ["ZZZ-0001-AZL"])
        (uno,) = r["resultados"]
        self.assertEqual((uno["resultado"], uno["antes"], uno["despues"]),
                         (L.APAGADA, "ACTIVATE", "SELLER_DEACTIVATED"))

    def test_en_temu_tambien_las_que_vuelven_solas_a_la_venta(self):
        for i, estado in enumerate(("3/1", "3/3", "2/4")):
            self.poner(f"ZZZ-010{i}-AZL", f"910{i}", estado)
        r = L.desactivar("temu", [f"ZZZ-010{i}-AZL" for i in range(3)])
        self.assertEqual(len(self.llamadas()), 3)
        self.assertEqual([(x["antes"], x["despues"], x["resultado"]) for x in r["resultados"]],
                         [("3/1", "3/2", L.APAGADA), ("3/3", "3/2", L.APAGADA), ("2/4", "3/2", L.APAGADA)])

    def test_se_decide_con_el_estado_vivo_no_con_el_censo(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8", vivo="3/2")      # el censo va una hora atrás
        self.poner("ZZZ-0002-AZL", "9002", "3/2", vivo="2/8")      # alguien la prendió después del censo
        r = L.desactivar("temu", ["ZZZ-0001-AZL", "ZZZ-0002-AZL"])
        self.assertEqual(self.llamadas(), [("apagar", "9002")])
        self.assertEqual([x["resultado"] for x in r["resultados"]], [L.YA_APAGADA, L.APAGADA])
        # Las dos se reflejan: la que ya estaba apagada en vivo dejaría de verse «a la
        # venta» hasta el siguiente censo si no.
        self.assertEqual(self.reflejos, [("temu", "ZZZ-0001-AZL", "9001", "3/2"),
                                         ("temu", "ZZZ-0002-AZL", "9002", "3/2")])

    def test_lo_que_no_esta_publicado_no_se_llama(self):
        self.poner("ZZZ-0001-AZL", "9001", "5/None")
        self.poner("ZZZ-0002-AZL", "9002", "4/7")
        self.poner("ZZZ-0003-AZL", "7703", "PENDING", canal="tiktok")
        r = L.desactivar("temu", ["ZZZ-0001-AZL", "ZZZ-0002-AZL", "ZZZ-0003-AZL"])
        self.assertEqual(self.llamadas(), [])
        self.assertEqual([x["resultado"] for x in r["resultados"]], [L.NO_APLICA] * 3)
        self.assertIn("Borrador", r["resultados"][0]["detalle"])
        self.assertEqual(self.dormidas, [], "sin nada que confirmar no se espera")

    def test_en_revision_de_tiktok_no_se_puede_apagar(self):
        self.poner("ZZZ-0001-AZL", "7701", "PENDING", canal="tiktok")
        r = L.desactivar("tiktok", ["ZZZ-0001-AZL"])
        self.assertEqual((self.llamadas(), r["resultados"][0]["resultado"]), ([], L.NO_APLICA))

    def test_un_sku_sin_publicacion_en_el_canal(self):
        r = L.desactivar("temu", ["ZZZ-9999-AZL"])
        (uno,) = r["resultados"]
        self.assertEqual((uno["resultado"], uno["listing_id"]), (L.SIN_PUBLICACION, None))
        self.assertEqual((self.orden, self.anotadas), ([], []), "ni se lee ni se anota")

    def test_si_no_se_puede_leer_en_vivo_no_se_llama(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        self.ilegibles.add("9001")
        r = L.desactivar("temu", ["ZZZ-0001-AZL"])
        (uno,) = r["resultados"]
        self.assertEqual((uno["resultado"], self.llamadas()), (L.ERROR, []))
        self.assertIn("no se pudo leer en vivo", uno["detalle"])
        self.assertEqual(self.reflejos, [])

    def test_un_rechazo_del_canal_no_detiene_a_las_demas(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        self.poner("ZZZ-0002-AZL", "9002", "2/8")
        self.rechaza.add("9001")
        r = L.desactivar("temu", ["ZZZ-0001-AZL", "ZZZ-0002-AZL"])
        self.assertEqual([x["resultado"] for x in r["resultados"]], [L.ERROR, L.APAGADA])
        self.assertIn("3000032", r["resultados"][0]["detalle"])
        self.assertEqual(r["resumen"], {L.ERROR: 1, L.APAGADA: 1})
        self.assertEqual(self.reflejos, [("temu", "ZZZ-0002-AZL", "9002", "3/2")])
        self.assertEqual(len(self.anotadas), 2, "el error también queda anotado")

    def test_si_el_canal_dice_ok_y_no_cambia_no_se_da_por_hecho(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        self.no_obedece.add("9001")
        r = L.desactivar("temu", ["ZZZ-0001-AZL"])
        (uno,) = r["resultados"]
        self.assertEqual((uno["resultado"], uno["antes"], uno["despues"]), (L.SIN_CONFIRMAR, "2/8", "2/8"))
        self.assertIn("vuelve a consultar", uno["detalle"])
        self.assertEqual(self.dormidas, [8.0, 8.0, 8.0], "insiste las relecturas configuradas y ya")
        self.assertEqual(self.reflejos, [], "sin confirmar no se toca channel.listings")
        self.assertEqual(self.sueltas, [])

    def test_lo_que_temu_tiene_retenido_acepta_la_orden_y_no_cambia(self):
        # Las 67 de Temu en `3/3` del 13-oct-2026: «ok» a la llamada y el estado igual.
        self.poner("ZZZ-0001-AZL", "9001", "3/3")
        self.poner("ZZZ-0002-AZL", "9002", "3/3")
        self.no_obedece |= {"9001", "9002"}
        self.marcas = {"9002"}
        r = L.desactivar("temu", ["ZZZ-0001-AZL", "ZZZ-0002-AZL"], actor="alguien@kubera.mx")
        self.assertEqual([x["resultado"] for x in r["resultados"]], [L.ORDEN_ENVIADA] * 2)
        self.assertEqual(r["resumen"], {L.ORDEN_ENVIADA: 2})
        self.assertIn("aceptó la orden", r["resultados"][0]["detalle"])
        self.assertEqual(len(self.llamadas()), 2, "la orden SÍ se manda")
        self.assertEqual(self.dormidas, [8.0], "y no se sigue esperando un cambio que no llega")
        self.assertEqual(self.reflejos, [], "no se vio apagada: channel.listings no se toca")
        self.assertEqual([(a["resultado"], a["antes"], a["despues"]) for a in self.anotadas],
                         [(L.ORDEN_ENVIADA, "3/3", "3/3")] * 2)
        self.assertEqual(self.sueltas, [("ZZZ-0002-AZL", "temu")], "y el seguro la suelta igual")

    def test_una_retenida_que_si_cambia_es_apagada(self):
        self.poner("ZZZ-0001-AZL", "9001", "3/3")
        r = L.desactivar("temu", ["ZZZ-0001-AZL"])
        self.assertEqual((r["resultados"][0]["resultado"], r["resultados"][0]["despues"]), (L.APAGADA, "3/2"))

    def test_la_agotada_que_no_cambia_sigue_siendo_sin_confirmar(self):
        # `3/1` sí cambia (medido el 9-oct: `3/1→3/2` en ~9 s): si no lo hace, es raro y se dice.
        self.poner("ZZZ-0001-AZL", "9001", "3/1")
        self.no_obedece.add("9001")
        r = L.desactivar("temu", ["ZZZ-0001-AZL"])
        self.assertEqual(r["resultados"][0]["resultado"], L.SIN_CONFIRMAR)
        self.assertEqual(self.dormidas, [8.0, 8.0, 8.0])

    def test_una_espera_por_tanda_no_una_por_publicacion(self):
        for i in range(5):
            self.poner(f"ZZZ-020{i}-AZL", f"920{i}", "2/8")
        r = L.desactivar("temu", [f"ZZZ-020{i}-AZL" for i in range(5)])
        self.assertEqual(self.dormidas, [8.0])
        self.assertEqual(r["resumen"], {L.APAGADA: 5})
        tipos = [o[0] for o in self.orden]
        self.assertEqual(tipos, ["leer", "apagar"] * 5 + ["dormir"] + ["leer"] * 5,
                         "primero todas las llamadas, luego UNA espera, luego las relecturas")

    def test_la_que_tarda_se_vuelve_a_leer_sin_frenar_a_las_demas(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        self.poner("ZZZ-0002-AZL", "9002", "2/8")
        lecturas = {"n": 0}
        real = S._leer_vivo.side_effect

        def lenta(canal, item):
            if item == "9002":
                lecturas["n"] += 1
                if lecturas["n"] == 2:                 # la primera relectura va rancia
                    self.orden.append(("leer", item))
                    return {"ok": True, "estado": "2/8", "stock": 5, "skus": []}
            return real(canal, item)

        with mock.patch.object(S, "_leer_vivo", side_effect=lenta):
            r = L.desactivar("temu", ["ZZZ-0001-AZL", "ZZZ-0002-AZL"])
        self.assertEqual([x["resultado"] for x in r["resultados"]], [L.APAGADA, L.APAGADA])
        self.assertEqual(self.dormidas, [8.0, 8.0])
        self.assertEqual([o for o in self.orden if o[0] == "leer"].count(("leer", "9001")), 2,
                         "la que ya quedó no se vuelve a leer")

    def test_si_queda_fuera_de_la_venta_en_otro_estado_lo_dice(self):
        # Medido el 9-oct con ROP-0694-MUL: TikTok la dejó en FAILED, no en SELLER_DEACTIVATED.
        self.poner("ZZZ-0001-AZL", "7701", "ACTIVATE", canal="tiktok")
        self.al_apagar["7701"] = "FAILED"
        r = L.desactivar("tiktok", ["ZZZ-0001-AZL"])
        (uno,) = r["resultados"]
        self.assertEqual((uno["resultado"], uno["despues"]), (L.APAGADA, "FAILED"))
        self.assertIn("Revisión fallida", uno["detalle"])

    def test_un_sku_con_dos_publicaciones_apaga_las_dos(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        self.poner("ZZZ-0001-AZL", "9002", "2/8")
        r = L.desactivar("temu", ["zzz-0001-azl"])
        self.assertEqual(self.llamadas(), [("apagar", "9001"), ("apagar", "9002")])
        self.assertEqual((r["pedidos"], len(r["resultados"])), (1, 2))

    def test_nunca_prende(self):
        # `_prender` revienta en el arnés: si alguna ruta lo llamara, todas las demás fallarían.
        self.poner("ZZZ-0001-AZL", "9001", "3/2")
        L.desactivar("temu", ["ZZZ-0001-AZL"])
        self.assertEqual(self.llamadas(), [])

    def test_usa_el_candado_de_la_publicacion(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        tomados = []
        real = S._candado

        def candado(canal, item):
            tomados.append((canal, item))
            return real(canal, item)

        with mock.patch.object(S, "_candado", side_effect=candado):
            L.desactivar("temu", ["ZZZ-0001-AZL"])
        self.assertEqual(tomados, [("temu", "9001")], "el mismo candado del seguro: no se pisan")


class SeQuedaApagado(Arnes):
    """Lo que una persona manda apagar no lo regresa el seguro."""

    def test_no_lleva_marca_del_seguro(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        r = L.desactivar("temu", ["ZZZ-0001-AZL"])
        self.assertEqual(self.sueltas, [])
        self.assertFalse(r["resultados"][0]["seguro_soltado"])
        fuente = inspect.getsource(L)
        self.assertNotIn("cero_inactivar", fuente, "no escribe la marca del seguro")
        self.assertNotIn("sellar(", fuente)

    def test_si_el_seguro_ya_la_tenia_apagada_suelta_su_marca(self):
        self.poner("ZZZ-0001-AZL", "9001", "3/2")
        self.marcas = {"9001"}
        r = L.desactivar("temu", ["ZZZ-0001-AZL"])
        (uno,) = r["resultados"]
        self.assertEqual((uno["resultado"], uno["seguro_soltado"]), (L.YA_APAGADA, True))
        self.assertEqual(self.sueltas, [("ZZZ-0001-AZL", "temu")])
        self.assertEqual(self.llamadas(), [], "y al canal no se le llama: ya estaba apagada")

    def test_la_que_no_se_pudo_apagar_conserva_su_marca(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        self.marcas = {"9001"}
        self.rechaza.add("9001")
        L.desactivar("temu", ["ZZZ-0001-AZL"])
        self.assertEqual(self.sueltas, [])

    def test_si_soltar_falla_el_lote_sigue(self):
        self.poner("ZZZ-0001-AZL", "9001", "3/2")
        self.marcas = {"9001"}
        with mock.patch.object(S, "soltar", side_effect=RuntimeError("kubera caída")):
            r = L.desactivar("temu", ["ZZZ-0001-AZL"])
        self.assertEqual((r["resultados"][0]["resultado"], r["resultados"][0]["seguro_soltado"]),
                         (L.YA_APAGADA, False))

    def test_la_firma_es_la_que_el_seguro_toma_por_una_persona(self):
        # `apagado_en_bloque` cuenta los `2/8→3/2` que NO firmó el seguro: ésta lo es.
        self.assertEqual(L.VIA, "panel_lote")
        self.assertIn("<> 'fanout_cero'", S._SQL_BLOQUE)
        self.assertNotEqual(L.VIA, "fanout_cero")


class Limites(Arnes):
    def test_a_lo_mucho_diez_por_llamada(self):
        with self.assertRaises(ValueError) as cm:
            L.desactivar("temu", [f"ZZZ-03{i:02d}-AZL" for i in range(11)])
        self.assertIn("10", str(cm.exception))
        self.assertEqual(self.orden, [])

    def test_lista_vacia_o_que_no_es_lista(self):
        for mala in ([], ["", "  "], None, "ZZZ-0001-AZL"):
            with self.assertRaises(ValueError):
                L.desactivar("temu", mala)

    def test_repetidos_cuentan_una_vez(self):
        self.poner("ZZZ-0001-AZL", "9001", "2/8")
        r = L.desactivar("temu", ["ZZZ-0001-AZL", " zzz-0001-azl ", "ZZZ-0001-AZL"])
        self.assertEqual((r["pedidos"], self.llamadas()), (1, [("apagar", "9001")]))

    def test_solo_temu_y_tiktok(self):
        for canal in ("mercado_libre", "amazon", "walmart", "general", ""):
            with self.assertRaises(ValueError):
                L.desactivar(canal, ["ZZZ-0001-AZL"])
        self.assertEqual(self.orden, [])


class SoloInactiva(unittest.TestCase):
    def test_el_modulo_no_conoce_ninguna_llamada_de_borrado_ni_de_prender(self):
        fuente = inspect.getsource(L)
        codigo = fuente.split('"""', 2)[2]          # todo menos la cabecera, que sí las nombra
        self.assertEqual(re.findall(r"_prender|\.activar\(|goods\.delete|products/delete|[\"']DELETE[\"']|\.delete\(",
                                    codigo), [])
        self.assertEqual(sorted(set(re.findall(r"\bS\.(_[a-z_]+|[a-z_]+)\(", codigo))),
                         ["_apagar", "_candado", "_dormir", "_espera_s", "_leer_vivo", "_relecturas", "a_la_venta"])

    def test_lo_unico_que_mueve_el_canal_es_apagar(self):
        fuente = inspect.getsource(L.desactivar)
        self.assertEqual(fuente.count("S._apagar("), 1)


class Consultas(unittest.TestCase):
    """El SQL va suplantado en las demás pruebas: aquí se fija lo que pide."""

    def test_la_lista_es_del_canal_y_solo_lo_que_tiene_publicacion(self):
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", return_value=[]) as fa:
            L._publicaciones("temu")
            L._publicaciones("temu", ["zzz-0001-azl", "ZZZ-0002-AZL"])
        todo, parte = fa.call_args_list
        plano = " ".join(todo[0][0].split())
        self.assertIn("from channel.listings l", plano)
        self.assertIn("where l.canal = %(c)s and nullif(l.listing_id, '') is not null", plano)
        self.assertEqual(todo[0][1], {"c": "temu"})
        self.assertIn("upper(l.sku::text) = any(%(s)s)", " ".join(parte[0][0].split()))
        self.assertEqual(parte[0][1], {"c": "temu", "s": ["ZZZ-0001-AZL", "ZZZ-0002-AZL"]})

    def test_la_bitacora_del_panel_es_la_ultima_de_cada_publicacion(self):
        plano = " ".join(L._SQL_PANEL.split())
        self.assertIn("distinct on (detalle->>'listing_id')", plano)
        self.assertIn("where proceso = %(p)s and detalle->>'canal' = %(c)s "
                      "and estado in ('ok', 'orden_enviada', 'sin_confirmar')", plano)
        self.assertIn("order by detalle->>'listing_id', created_at desc, id desc", plano)

    def test_anota_en_process_log_y_ok_solo_si_quedo_apagada(self):
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "execute") as ex:
            L._anotar("temu", {"sku": "ZZZ-0001-AZL", "listing_id": "9001", "antes": "2/8", "despues": "3/2",
                               "resultado": L.APAGADA, "detalle": None}, "abc", "alguien@kubera.mx", 3.21)
            L._anotar("temu", {"sku": "ZZZ-0002-AZL", "listing_id": "9002", "antes": "2/8", "despues": "2/8",
                               "resultado": L.ERROR, "detalle": "x"}, "abc", "", 1.0)
            L._anotar("temu", {"sku": "ZZZ-0003-AZL", "listing_id": "9003", "antes": "3/3", "despues": "3/3",
                               "resultado": L.ORDEN_ENVIADA, "detalle": "x"}, "abc", "", 1.0)
        ok, mal, enviada = ex.call_args_list
        self.assertEqual(enviada[0][1][2], "orden_enviada")
        self.assertIn("insert into ops.process_log", ok[0][0])
        self.assertEqual((ok[0][1][0], ok[0][1][1], ok[0][1][2]), (L.PROCESO, "ZZZ-0001-AZL", "ok"))
        self.assertIn('"antes": "2/8"', ok[0][1][3])
        self.assertIn('"actor": "alguien@kubera.mx"', ok[0][1][3])
        self.assertEqual(mal[0][1][2], L.ERROR)

    def test_que_no_se_pueda_anotar_no_tumba_el_lote(self):
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "execute", side_effect=RuntimeError("kubera caída")):
            L._anotar("temu", {"sku": "X", "resultado": L.APAGADA}, "abc", "", 1.0)

    def test_refleja_firmado_y_solo_si_cambia(self):
        from services import supabase_db as sdb
        vistos = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                vistos.append((" ".join(sql.split()), params))

        with mock.patch.object(sdb, "get_cursor", side_effect=Cursor):
            L._reflejar("temu", "ZZZ-0001-AZL", "9001", "3/2")
        self.assertEqual(vistos[0], ("select set_config('app.via', %s, true)", ("panel_lote",)))
        self.assertIn("update channel.listings set status = %(e)s", vistos[1][0])
        self.assertIn("status is distinct from %(e)s", vistos[1][0])
        self.assertEqual(vistos[1][1], {"e": "3/2", "c": "temu", "s": "ZZZ-0001-AZL", "i": "9001"})


class Rutas(unittest.TestCase):
    def _ruta(self, metodo, path):
        return next(r for r in RUTAS.router.routes if r.path == path and metodo in r.methods)

    def test_desactivar_es_solo_para_administradores(self):
        ruta = self._ruta("POST", "/api/publicaciones/lote/desactivar")
        self.assertIn(RUTAS._admin_del_lote, [d.call for d in ruta.dependant.dependencies])
        from core import rbac
        self.assertEqual(rbac.rol_requerido("POST", "/api/publicaciones/lote/desactivar"), "admin")

    def test_las_dos_rutas_son_sincronas(self):
        # Regla 11: psycopg2 y las llamadas al canal bloquean; como `def` corren en el threadpool.
        for metodo, path in (("GET", "/api/publicaciones/lote"), ("POST", "/api/publicaciones/lote/desactivar")):
            self.assertFalse(inspect.iscoroutinefunction(self._ruta(metodo, path).endpoint), path)

    def _pedir(self, identidad):
        pedido = SimpleNamespace(state=SimpleNamespace(identidad=identidad), method="POST",
                                 url=SimpleNamespace(path="/api/publicaciones/lote/desactivar"))
        return asyncio.run(RUTAS._admin_del_lote(pedido))

    def test_sin_sesion_401_y_sin_ser_admin_403(self):
        with self.assertRaises(HTTPException) as cm:
            self._pedir(SimpleNamespace(autenticado=False, tipo="anonimo", rol="", actor="anonimo"))
        self.assertEqual(cm.exception.status_code, 401)
        for rol in ("operador", "lectura"):
            with self.assertRaises(HTTPException) as cm:
                self._pedir(SimpleNamespace(autenticado=True, tipo="persona", rol=rol, actor="kam@kubera.mx"))
            self.assertEqual(cm.exception.status_code, 403)
            self.assertIn("sólo para administradores", cm.exception.detail)
        with self.assertRaises(HTTPException) as cm:      # una llave de API con rol admin no es una persona
            self._pedir(SimpleNamespace(autenticado=True, tipo="api_key", rol="admin", actor="llave"))
        self.assertEqual(cm.exception.status_code, 403)

    def test_un_admin_pasa_y_su_nombre_llega_al_servicio(self):
        quien = SimpleNamespace(autenticado=True, tipo="persona", rol="admin", actor="jefe@kubera.mx")
        self.assertIs(self._pedir(quien), quien)
        with mock.patch.object(RUTAS.lote, "desactivar", return_value={"ok": True}) as d:
            RUTAS.lote_desactivar(RUTAS.LoteDesactivar(canal="temu", skus=["ZZZ-0001-AZL"]), quien=quien)
        d.assert_called_once_with("temu", ["ZZZ-0001-AZL"], actor="jefe@kubera.mx")

    def test_un_pedido_mal_hecho_es_400_no_500(self):
        quien = SimpleNamespace(actor="jefe@kubera.mx")
        with self.assertRaises(HTTPException) as cm:
            RUTAS.lote_desactivar(RUTAS.LoteDesactivar(canal="amazon", skus=["X"]), quien=quien)
        self.assertEqual(cm.exception.status_code, 400)
        with self.assertRaises(HTTPException) as cm:
            RUTAS.lote_listar(canal="amazon")
        self.assertEqual(cm.exception.status_code, 400)

    def test_listar_trae_los_canales_disponibles(self):
        with mock.patch.object(RUTAS.lote, "listar", return_value={"canal": "temu", "items": []}):
            r = RUTAS.lote_listar(canal="temu")
        self.assertEqual(r["canales"], [{"id": "temu", "nombre": "Temu"}, {"id": "tiktok", "nombre": "TikTok"}])


if __name__ == "__main__":
    unittest.main()
