"""Pruebas de «Ofertas de ML» en PRICING (services/pricing_ofertas.py + routers/pricing.py, v0.672.0).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Acceso**: ver lo puede quien ve PRICING; tomar y quitar, SOLO admin (y sin
   sesión, 401), sin llegar al servicio.
2. **Lo medido en vivo el 20-oct** queda escrito como regla:
   · relámpago sin promotion_id y con piezas; el `price` de su candidata es el sugerido;
   · campaña propia: con descuento individual activo NO entra (y no se llama a ML);
   · relámpago activa no se quita (y no se llama a ML).
3. **Rango y piezas** se revisan antes del POST.
4. **Tomar** manda el cuerpo de la doc, comprueba en ML y anota en la bitácora.

Sin red: nada aquí habla con ML ni con kubera.

    cd backend && python -m unittest tests.test_pricing_ofertas -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import pricing as ruta  # noqa: E402
from services import pricing_ofertas as po  # noqa: E402

ADMIN = SimpleNamespace(autenticado=True, tipo="persona", rol="admin", actor="brandon@x")
INVITADO = SimpleNamespace(autenticado=True, tipo="persona", rol="operador", actor="andy@x")
ANONIMO = SimpleNamespace(autenticado=False, tipo="", rol="", actor="?")
PEDIDO = {"item": "MLM3160816867", "cuenta": "SANCORFASHION", "tipo": "LIGHTNING", "promo_id": "LGH-MLM1000",
          "precio": 148.41, "stock": 5, "motivo": "campana_canal"}


def _cliente() -> TestClient:
    app = FastAPI()
    app.include_router(ruta.router)
    return TestClient(app)


def _como(quien):
    return mock.patch("routers.investigacion.core_identidad.resolver", new=mock.AsyncMock(return_value=quien))


class Acceso(unittest.TestCase):
    def test_ver_lo_puede_el_invitado_tomar_y_quitar_no(self):
        with _como(INVITADO), mock.patch("core.rbac.ve_pricing", return_value=True), \
             mock.patch.object(po, "listar", new=mock.AsyncMock(return_value={"filas": []})) as listar, \
             mock.patch.object(po, "tomar") as tomar, mock.patch.object(po, "quitar") as quitar:
            cli = _cliente()
            self.assertEqual(cli.get("/api/pricing/ofertas?cuenta=BEKURA").status_code, 200)
            self.assertEqual(cli.post("/api/pricing/ofertas/tomar", json=PEDIDO).status_code, 403)
            self.assertEqual(cli.post("/api/pricing/ofertas/quitar", json=PEDIDO).status_code, 403)
        listar.assert_awaited_once()
        tomar.assert_not_called()
        quitar.assert_not_called()

    def test_sin_sesion_401(self):
        with _como(ANONIMO), mock.patch.object(po, "listar") as listar:
            cli = _cliente()
            self.assertEqual(cli.get("/api/pricing/ofertas?cuenta=BEKURA").status_code, 401)
            self.assertEqual(cli.post("/api/pricing/ofertas/tomar", json=PEDIDO).status_code, 401)
        listar.assert_not_called()

    def test_admin_llega_al_servicio_y_los_errores_son_400(self):
        with _como(ADMIN), mock.patch.object(po, "tomar", new=mock.AsyncMock(return_value={"estado": "pending"})) as tomar:
            r = _cliente().post("/api/pricing/ofertas/tomar", json=PEDIDO)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(tomar.await_args[0][:6], ("MLM3160816867", "SANCORFASHION", "LIGHTNING", "LGH-MLM1000", 148.41, 5))
        with _como(ADMIN):
            self.assertEqual(_cliente().get("/api/pricing/ofertas?cuenta=OTRA").status_code, 400)
            self.assertEqual(_cliente().post("/api/pricing/ofertas/tomar", json={**PEDIDO, "tipo": "DOD"}).status_code, 400)
            self.assertEqual(_cliente().post("/api/pricing/ofertas/quitar", json={**PEDIDO, "promo_id": "x;drop"}).status_code, 400)

    def test_rbac_post_pricing_es_admin(self):
        from core import rbac
        self.assertEqual(rbac.rol_requerido("POST", "/api/pricing/ofertas/tomar"), "admin")
        self.assertEqual(rbac.rol_requerido("POST", "/api/pricing/ofertas/quitar"), "admin")


class Reglas(unittest.TestCase):
    PROMO_RELAMPAGO = {"id": "LGH-MLM1000", "type": "LIGHTNING", "name": ""}

    def test_candidata_relampago_su_price_es_el_sugerido(self):
        x = {"id": "MLM1", "status": "candidate", "price": 148.41, "original_price": 220, "min_discounted_price": 66,
             "stock": {"min": 5, "max": 25}}
        f = po.fila_de("SANCORFASHION", self.PROMO_RELAMPAGO, x)
        self.assertEqual((f["sugerido"], f["precio"], f["min"], f["stock_min"], f["stock_max"]), (148.41, None, 66.0, 5, 25))
        prog = po.fila_de("SANCORFASHION", self.PROMO_RELAMPAGO, {"id": "MLM1", "status": "pending", "price": 229,
                                                                 "start_date": "2026-10-22T00:00:00", "finish_date": "2026-10-22T11:59:59",
                                                                 "stock": {"remaining_stock": 5}})
        self.assertEqual((prog["precio"], prog["sugerido"], prog["inicio"], prog["fin"], prog["restante"]),
                         (229.0, None, "2026-10-22 00:00", "2026-10-22 11:59", 5))

    def test_hora_con_zona_se_pasa_a_cdmx(self):
        self.assertEqual(po._hora("2026-11-13T04:00:00Z"), "2026-11-12 22:00")      # Buen Fin
        self.assertEqual(po._hora("2026-11-12T22:00:00-06:00"), "2026-11-12 22:00")

    def test_campana_propia_con_descuento_individual_no_puede(self):
        fila = {"estado": "candidate", "tipo": "SELLER_CAMPAIGN", "promo_id": "C-MLM1592062"}
        con_rango = [{"id": "C-MLM1592062", "type": "SELLER_CAMPAIGN", "status": "candidate", "max_discounted_price": 1129.41}]
        self.assertEqual(po.bloqueo(fila, {"status": "active"}, con_rango), (True, None))
        con_pd = [{"id": "C-MLM1592062", "type": "SELLER_CAMPAIGN", "status": "candidate"},
                  {"type": "PRICE_DISCOUNT", "status": "started", "price": 406}]
        puede, motivo = po.bloqueo(fila, {"status": "active"}, con_pd)
        self.assertFalse(puede)
        self.assertIn("descuento individual a $406.00", motivo)
        puede, motivo = po.bloqueo(fila, {"status": "active"}, [con_pd[0]])
        self.assertIsNone(puede)                       # sin rango y sin descuento individual: no probado, se deja intentar
        self.assertIn("no le da rango", motivo)
        self.assertEqual(po.bloqueo(fila, {"status": "active"}, None), (None, None))   # no se revisó

    def test_relampago_sin_piezas_o_pausada_no_puede(self):
        fila = {"estado": "candidate", "tipo": "LIGHTNING", "stock_min": 5}
        self.assertEqual(po.bloqueo(fila, {"status": "active", "stock": 5}, None), (True, None))
        self.assertFalse(po.bloqueo(fila, {"status": "active", "stock": 2}, None)[0])
        self.assertFalse(po.bloqueo(fila, {"status": "paused", "stock": 50}, None)[0])
        self.assertEqual(po.bloqueo({**fila, "estado": "pending"}, {"status": "paused"}, None), (None, None))

    def test_rango_y_piezas_antes_del_post(self):
        deal = {"tipo": "DEAL", "min": 139.8, "max": 353.08}
        po.validar_toma(deal, 353.08, None, 10)
        for malo in (0, 139.0, 353.5):
            with self.subTest(precio=malo), self.assertRaises(po.ErrorOferta):
                po.validar_toma(deal, malo, None, 10)
        rel = {"tipo": "LIGHTNING", "min": 44, "max": 198, "stock_min": 5, "stock_max": 25}
        po.validar_toma(rel, 148.41, 5, 25)
        for piezas, disp in ((None, 25), (4, 25), (26, 30), (10, 8)):
            with self.subTest(piezas=piezas, disp=disp), self.assertRaises(po.ErrorOferta):
                po.validar_toma(rel, 148.41, piezas, disp)

    def test_cuerpo_de_cada_tipo(self):
        self.assertEqual(po.cuerpo_toma("LIGHTNING", "LGH-MLM1000", 148.411, 5),
                         {"promotion_type": "LIGHTNING", "deal_price": 148.41, "stock": 5})       # sin promotion_id
        self.assertEqual(po.cuerpo_toma("DEAL", "P-MLM17923024", 353.08, None),
                         {"promotion_id": "P-MLM17923024", "promotion_type": "DEAL", "deal_price": 353.08})

    def test_no_candidates_se_explica(self):
        r = httpx.Response(400, json={"message": "No candidates found for item"})
        self.assertIn("descuento individual", po.traducir("SELLER_CAMPAIGN", r))
        self.assertIn("ya no la ofrece", po.traducir("DEAL", r))


class _Cli:
    """Cliente falso: registra POST/DELETE; los GET los contesta `_leer_item` parchado."""

    def __init__(self):
        self.llamadas = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, params=None, json=None):
        self.llamadas.append(("POST", url, json))
        return httpx.Response(201, json={"price": json["deal_price"]})

    async def delete(self, url, params=None):
        self.llamadas.append(("DELETE", url, params))
        return httpx.Response(200)


def _correr(coro):
    return asyncio.run(coro)


class TomarYQuitar(unittest.TestCase):
    def setUp(self):
        po.olvidar()
        self.cli = _Cli()
        p = mock.patch.object(po.pp, "_cliente", new=mock.AsyncMock(return_value=self.cli))
        p.start()
        self.addCleanup(p.stop)
        a = mock.patch.object(po, "_anotar")
        self.anotar = a.start()
        self.addCleanup(a.stop)
        s = mock.patch.object(po.asyncio, "sleep", new=mock.AsyncMock())
        s.start()
        self.addCleanup(s.stop)

    def _lecturas(self, *promos):
        it = {"id": "MLM1", "available_quantity": 20, "status": "active"}
        xs = list(promos)

        async def leer(cli, item):
            return it, (xs.pop(0) if len(xs) > 1 else xs[0]), 300.0
        return mock.patch.object(po, "_leer_item", new=leer)

    def test_campana_propia_con_descuento_individual_no_llama_a_ml(self):
        pr = [{"id": "C-MLM1", "type": "SELLER_CAMPAIGN", "status": "candidate"},
              {"type": "PRICE_DISCOUNT", "status": "started", "price": 280}]
        with self._lecturas(pr), self.assertRaises(po.ErrorOferta) as e:
            _correr(po.tomar("MLM123456789", "BEKURA", "SELLER_CAMPAIGN", "C-MLM1", 290))
        self.assertIn("descuento individual", str(e.exception))
        self.assertEqual(self.cli.llamadas, [])

    def test_relampago_activa_no_se_quita(self):
        pr = [{"id": "LGH-MLM1000", "type": "LIGHTNING", "status": "started", "price": 229}]
        with self._lecturas(pr), self.assertRaises(po.ErrorOferta) as e:
            _correr(po.quitar("MLM123456789", "BEKURA", "LIGHTNING", "LGH-MLM1000"))
        self.assertIn("ya empezó", str(e.exception))
        self.assertEqual(self.cli.llamadas, [])

    def test_tomar_buen_fin_manda_el_cuerpo_comprueba_y_anota(self):
        antes = [{"id": "P-MLM17923024", "type": "DEAL", "status": "candidate", "min_discounted_price": 139.8,
                  "max_discounted_price": 353.08},
                 {"type": "PRICE_DISCOUNT", "status": "started", "price": 364}]      # Buen Fin SÍ acepta con descuento individual
        despues = [{"id": "P-MLM17923024", "type": "DEAL", "status": "pending", "price": 353.08}, antes[1]]
        with self._lecturas(antes, despues):
            r = _correr(po.tomar("MLM123456789", "BEKURA", "DEAL", "P-MLM17923024", 353.08, sku="TEC-0281-GRI", quien="brandon@x"))
        self.assertEqual(self.cli.llamadas, [("POST", "/seller-promotions/items/MLM123456789",
                                              {"promotion_id": "P-MLM17923024", "promotion_type": "DEAL", "deal_price": 353.08})])
        self.assertEqual((r["estado"], r["precio"]), ("pending", 353.08))
        args = self.anotar.call_args[0]
        self.assertEqual((args[0], args[1], args[2], args[4]), ("TEC-0281-GRI", "tomar", "ok", "brandon@x"))

    def test_quitar_campana_manda_promotion_id_y_relampago_no(self):
        camp = [{"id": "C-MLM1", "type": "SELLER_CAMPAIGN", "status": "started", "price": 1129.41}]
        with self._lecturas(camp, [{"id": "C-MLM1", "type": "SELLER_CAMPAIGN", "status": "candidate"}]):
            r = _correr(po.quitar("MLM123456789", "BEKURA", "SELLER_CAMPAIGN", "C-MLM1"))
        self.assertEqual(r["estado"], "candidate")
        self.assertEqual(self.cli.llamadas[-1][2], {"promotion_type": "SELLER_CAMPAIGN", "app_version": "v2", "promotion_id": "C-MLM1"})
        rel = [{"id": "LGH-MLM1000", "type": "LIGHTNING", "status": "pending", "price": 148.41}]
        with self._lecturas(rel, [{"id": "LGH-MLM1000", "type": "LIGHTNING", "status": "candidate"}]):
            _correr(po.quitar("MLM123456789", "SANCORFASHION", "LIGHTNING", "LGH-MLM1000"))
        self.assertEqual(self.cli.llamadas[-1][2], {"promotion_type": "LIGHTNING", "app_version": "v2"})

    def test_ya_inscrita_no_se_vuelve_a_tomar(self):
        pr = [{"id": "P-MLM1", "type": "DEAL", "status": "pending", "price": 300}]
        with self._lecturas(pr), self.assertRaises(po.ErrorOferta) as e:
            _correr(po.tomar("MLM123456789", "BEKURA", "DEAL", "P-MLM1", 300))
        self.assertIn("Ya está inscrita", str(e.exception))
        self.assertEqual(self.cli.llamadas, [])


if __name__ == "__main__":
    unittest.main()
