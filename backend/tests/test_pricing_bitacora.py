"""Pruebas de la bitácora de PRICING (services/pricing_bitacora.py + routers/pricing.py, v0.673.0).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Qué se guarda de cada movimiento** (`plan`, puro): cambio de precio aplicado,
   fallido y regresado, fallido sin cambio, deshacer (con campañas re-metidas),
   tomar y quitar ofertas. El precio base es el TACHADO de ML, no `item.price`
   (ORG-0448-VER, 19-oct: price $108, tachado $350).
2. **El contexto va plano** (el esquema rechaza objetos anidados y números raros).
3. **No se pide motivo** (v0.674.0, Brandon: «solamente debe guardar lo que se hizo»):
   aplicar, tomar y quitar pasan sin él, y la bitácora lleva lo que veía la pantalla.
4. **Una fila ya pasada no se vuelve a escribir** (process_log_id es único).

Sin red ni base: lo que sí escribe en un Postgres con las reglas reales de la 0074 se
probó aparte (Documents/omnicanal_pg_bitacora/probar_escritor.py).

    cd backend && python -m unittest tests.test_pricing_bitacora -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import pricing as ruta  # noqa: E402
from services import pricing_bitacora as pb  # noqa: E402

CDMX = timezone(timedelta(hours=-6))
ADMIN = SimpleNamespace(autenticado=True, tipo="persona", rol="admin", actor="brandon@x")

PD_VIEJO = {"tipo": "PRICE_DISCOUNT", "id": None, "estado": "started", "precio": 97.0, "inicio": "2026-10-15T00:00:00",
            "fin": "2026-11-11T23:59:59"}
PD_NUEVO = {"tipo": "PRICE_DISCOUNT", "id": None, "estado": "started", "precio": 90.0, "inicio": "2026-10-19T00:00:00",
            "fin": "2026-11-16T23:59:59"}
DEAL = {"tipo": "DEAL", "id": "P-MLM17611046", "estado": "started", "precio": 108.0, "inicio": "2026-08-17T00:00:00-06:00"}
ANTES = {"precio": 108.0, "tachado": 350.0, "cobra": 97.0, "promos": [DEAL, PD_VIEJO]}
DESPUES = {"precio": 108.0, "tachado": 350.0, "cobra": 90.0, "promos": [DEAL, PD_NUEVO]}
T = datetime(2026, 10, 19, 18, 49, 42, tzinfo=timezone.utc)


def _fila(proceso, accion, estado, detalle, id_=7):
    return {"id": id_, "proceso": proceso, "origen": "pricing", "accion": accion, "estado": estado, "sku": "ORG-0448-VER",
            "actor": "brandon@kubera.mx", "created_at": T, "duracion_s": None, "detalle": detalle}


class Plan(unittest.TestCase):
    def test_cambio_aplicado_el_del_19_oct(self):
        d = {"item": "MLM6111233174", "cuenta": "SANCORFASHION", "antes": ANTES, "despues": DESPUES, "quitadas": [],
             "pedido": {"precio": 350.0, "pct": 74.2857, "salir": False, "hasta": None}}
        p = pb.plan(_fila("precio", "aplicar", "ok", d))
        self.assertEqual((p["tipo"], p["estado"], p["resultado"]), ("editar_precio", "aplicada", "ok"))
        self.assertEqual((p["antes_base"], p["antes_cobrado"], p["despues_base"], p["despues_cobrado"]), (350.0, 97.0, 350.0, 90.0))
        self.assertEqual((p["pedido"]["base"], p["pedido"]["cobrado"]), (350.0, 90.0))
        self.assertAlmostEqual(p["pedido"]["pct"], 0.742857, places=5)
        self.assertEqual([o["precio"] for o in p["terminan"]], [97.0])       # el descuento viejo terminó
        self.assertEqual([o["precio"] for o in p["empiezan"]], [90.0])       # y empezó el nuevo; la campaña de ML no se toca
        self.assertIsNone(p["motivo"])                                       # el del 19-oct no tenía motivo: va vacío

    def test_cambio_que_fallo_y_se_regreso(self):
        d = {"item": "MLM1", "cuenta": "BEKURA", "antes": ANTES, "pedido": {"precio": 4083, "pct": 10}, "error": "fuera de rango",
             "resultado": "regresada", "bitacora": {"motivo": "sin_ventas", "recomendacion": "min_comp", "precio_recomendado": 3675}}
        p = pb.plan(_fila("precio", "aplicar", "error", d))
        self.assertEqual((p["estado"], p["resultado"], p["error"]), ("error", "regresada", "fuera de rango"))
        self.assertEqual((p["motivo"], p["recomendacion"], p["precio_recomendado"]), ("sin_ventas", "min_comp", 3675.0))
        self.assertEqual((p["empiezan"], p["terminan"], p["despues_base"]), ([], [], None))

    def test_precio_normal_rechazado_no_cambio_nada(self):
        d = {"item": "MLM1", "cuenta": "BEKURA", "antes": ANTES, "pedido": {"precio": 500, "pct": None},
             "error": "403", "resultado": "error"}
        p = pb.plan(_fila("precio", "aplicar", "error", d))
        self.assertEqual((p["estado"], p["resultado"]), ("error", "error"))

    def test_deshacer_con_campana_re_metida(self):
        camp = {"tipo": "SELLER_CAMPAIGN", "id": "C-MLM1592062", "precio": 429.0, "inicio": "2026-10-01T00:00:00"}
        d = {"item": "MLM1", "cuenta": "BEKURA", "deshace": 5, "antes_del_cambio": ANTES, "ahora": DESPUES,
             "despues": {**ANTES, "promos": [DEAL, {**PD_VIEJO, "inicio": "2026-10-20T10:00:00"}]}, "vueltas": [camp],
             "avisos": [], "bitacora": {"motivo": "prueba"}}
        p = pb.plan(_fila("precio", "deshacer", "ok", d))
        self.assertEqual((p["tipo"], p["deshace_log_id"], p["estado"]), ("deshacer", 5, "aplicada"))
        self.assertEqual((p["pedido"]["base"], p["pedido"]["cobrado"], p["antes_cobrado"], p["despues_cobrado"]), (350.0, 97.0, 90.0, 97.0))
        self.assertIn("C-MLM1592062", [o["id"] for o in p["empiezan"]])
        self.assertEqual([o["precio"] for o in p["terminan"]], [90.0])

    def test_deshacer_parcial_guarda_el_aviso(self):
        d = {"item": "MLM1", "cuenta": "BEKURA", "deshace": 5, "antes_del_cambio": ANTES, "ahora": DESPUES, "despues": DESPUES,
             "avisos": ["ML no aceptó el descuento de antes"]}
        p = pb.plan(_fila("precio", "deshacer", "parcial", d))
        self.assertEqual((p["estado"], p["resultado"], p["error"]), ("parcial", "parcial", "ML no aceptó el descuento de antes"))

    def test_tomar_relampago_con_su_horario(self):
        d = {"item": "MLM3160816867", "cuenta": "SANCORFASHION", "tipo": "LIGHTNING", "promo_id": "LGH-MLM1000",
             "cuerpo": {"promotion_type": "LIGHTNING", "deal_price": 148.41, "stock": 5},
             "antes": {"cobra": 153.0, "tachado": 220.0, "normal": 220.0, "entrada": {"status": "candidate"}},
             "despues": {"cobra": 153.0, "tachado": 220.0, "normal": 220.0, "inicio": "2026-10-22 00:00", "fin": "2026-10-22 11:59",
                         "entrada": {"status": "pending", "price": 148.41, "ref_id": "OFFER-1"}},
             "bitacora": {"motivo": "campana_canal", "contexto": {"stock": 25}}}
        p = pb.plan(_fila("ofertas", "tomar", "ok", d))
        self.assertEqual((p["tipo"], p["estado"], p["pedido"]["cobrado"], p["antes_base"]), ("entrar_campana", "aplicada", 148.41, 220.0))
        o = p["empiezan"][0]
        self.assertEqual((o["tipo"], o["id"], o["inicio"], o["oferta_canal_id"]), ("LIGHTNING", "LGH-MLM1000", "2026-10-22 00:00", "OFFER-1"))
        self.assertEqual(pb.plan(_fila("ofertas", "tomar", "sin_confirmar", d))["estado"], "parcial")

    def test_quitar_que_fallo_no_toca_ofertas(self):
        d = {"item": "MLM1", "cuenta": "BEKURA", "tipo": "DEAL", "promo_id": "P-MLM17923024", "error": "no",
             "antes": {"cobra": 364.0, "entrada": {"status": "pending"}}}
        p = pb.plan(_fila("ofertas", "quitar", "error", d))
        self.assertEqual((p["tipo"], p["estado"], p["empiezan"], p["terminan"]), ("salir_campana", "error", [], []))

    def test_lo_que_no_es_de_pricing_no_se_registra(self):
        self.assertIsNone(pb.plan(_fila("precio", "vista", "ok", {"item": "MLM1", "cuenta": "BEKURA"})))
        self.assertIsNone(pb.plan({**_fila("otro", "aplicar", "ok", {"item": "MLM1", "cuenta": "BEKURA"})}))

    def test_motivo_raro_no_pasa_al_esquema(self):
        d = {"item": "MLM1", "cuenta": "BEKURA", "antes": ANTES, "despues": DESPUES, "pedido": {"precio": 350},
             "bitacora": {"motivo": "DROP TABLE", "recomendacion": "Subir 5%"}}
        p = pb.plan(_fila("precio", "aplicar", "ok", d))
        self.assertEqual((p["motivo"], p["recomendacion"]), (None, None))


class Puras(unittest.TestCase):
    def test_contexto_plano(self):
        c = pb.contexto_plano({"stock": 25, "conv": 0.012, "cal": "bueno", "ok": True, "nada": None,
                               "anidado": {"x": 1}, "lista": [1], "raro": float("nan"), "largo": "x" * 900})
        self.assertEqual(set(c), {"stock", "conv", "cal", "ok", "nada", "raro", "largo"})
        self.assertIsNone(c["raro"])
        self.assertEqual(len(c["largo"]), 500)
        self.assertIsNone(pb.contexto_plano("no"))

    def test_precio_base_es_el_tachado(self):
        self.assertEqual(pb.base_de({"precio": 108.0, "tachado": 350.0}), 350.0)
        self.assertEqual(pb.base_de({"precio": 108.0, "tachado": None}), 108.0)
        self.assertIsNone(pb.base_de(None))


class Idempotente(unittest.TestCase):
    def test_una_fila_ya_pasada_no_se_vuelve_a_escribir(self):
        sdb = SimpleNamespace(fetch_one=mock.Mock(return_value={"id": 41, "estado": "aplicada"}), execute_returning=mock.Mock(),
                              execute=mock.Mock(), fetch_all=mock.Mock())
        d = {"item": "MLM1", "cuenta": "BEKURA", "antes": ANTES, "despues": DESPUES, "pedido": {"precio": 350}}
        import services.supabase_db  # noqa: F401 — que exista el atributo que se parcha (no abre conexión)
        with mock.patch("services.supabase_db", sdb):
            self.assertEqual(pb.registrar(_fila("precio", "aplicar", "ok", d)), 41)
        sdb.execute_returning.assert_not_called()
        sdb.execute.assert_not_called()

    def test_tras_anotar_nunca_lanza(self):
        with mock.patch.object(pb, "registrar", side_effect=RuntimeError("kubera caída")), \
             mock.patch.object(pb, "rellenar", side_effect=RuntimeError("kubera caída")):
            pb.tras_anotar({"id": 1})


def _cliente() -> TestClient:
    app = FastAPI()
    app.include_router(ruta.router)
    return TestClient(app)


def _como(quien):
    return mock.patch("routers.investigacion.core_identidad.resolver", new=mock.AsyncMock(return_value=quien))


class Rutas(unittest.TestCase):
    def test_aplicar_y_tomar_sin_motivo_llevan_lo_que_veia_la_pantalla(self):
        precio = {"cuenta": "BEKURA", "sku": "X", "precio": 500, "pct": 10, "contexto": {"stock": 3},
                  "recomendacion": "sv_nuevo", "foto": "2026-10-14 18:25"}
        oferta = {"item": "MLM123456789", "cuenta": "BEKURA", "tipo": "DEAL", "promo_id": "P-MLM17923024", "precio": 300,
                  "contexto": {"cobra_hoy": 364}}
        with _como(ADMIN), mock.patch.object(ruta.pp, "aplicar", new=mock.AsyncMock(return_value={})) as aplicar,              mock.patch.object(ruta.po, "tomar", new=mock.AsyncMock(return_value={"estado": "pending"})) as tomar,              mock.patch.object(ruta.po, "quitar", new=mock.AsyncMock(return_value={"estado": "candidate"})):
            cli = _cliente()
            self.assertEqual(cli.post("/api/pricing/precio/MLM123456789", json=precio).status_code, 200)
            self.assertEqual(cli.post("/api/pricing/ofertas/tomar", json=oferta).status_code, 200)
            self.assertEqual(cli.post("/api/pricing/ofertas/quitar", json=oferta).status_code, 200)
            self.assertEqual(cli.get("/api/pricing/bitacora/motivos").status_code, 404)   # ya no hay catálogo que pedir
        import time
        for _ in range(50):          # el trabajo del editor corre en segundo plano
            if aplicar.await_count:
                break
            time.sleep(0.02)
        b = aplicar.await_args.kwargs["bitacora"]
        self.assertNotIn("motivo", b)
        self.assertEqual((b["contexto"], b["recomendacion"], b["pantalla"]), ({"stock": 3}, "sv_nuevo", "pricing"))
        self.assertEqual(tomar.await_args.kwargs["bitacora"]["contexto"], {"cobra_hoy": 364})

    def test_deshacer_pasa(self):
        with _como(ADMIN), mock.patch.object(ruta.pp, "deshacer", new=mock.AsyncMock(return_value={})):
            r = _cliente().post("/api/pricing/precio/MLM123456789/deshacer", json={"cuenta": "BEKURA"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
