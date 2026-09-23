"""Los DOS totales de General en `GET /api/canales`.

── POR QUÉ ESTA PRUEBA ────────────────────────────────────────────────────────
El 23-sep-2026 el panel decía 3,035 en la pestaña General y 7,288 en el
encabezado de Omnicanal, y nadie sabía por qué. Son dos listados distintos:

- `total_productos` = vista «productos» (publish/pending/ready/private), SIN
  borradores → 3,035.
- `total_catalogo`  = vista «omnicanal», TODOS los estados → 7,288. La resta
  (4,250) son los borradores que el frontend hace visibles.

Estas pruebas fijan de qué vista sale cada uno, que se piden en paralelo, que
la caída de uno no tumba al otro (queda en None, nunca 0) y que ningún otro
canal trae `total_catalogo`. Además, que ninguna corrutina del router llama
síncrono a la base (regla 11).

Sin red: `woocommerce.listar_productos` y los contadores de cada canal van
simulados.

    cd backend && python -m unittest tests.test_canales_totales -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.marketplaces import Canal  # noqa: E402
from routers import canales as ruta_canales  # noqa: E402
from services import amazon, meli, temu_panel, tiktok_panel, walmart_panel  # noqa: E402
from services import woocommerce  # noqa: E402
from test_regla_11_productos import sincronas_en_corrutinas  # noqa: E402

# Las cifras medidas en producción el 23-sep-2026.
SIN_BORRADORES = 3035
CATALOGO = 7288


class _Base(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(ruta_canales.router)
        self.c = TestClient(app)
        self.llamadas: list[dict] = []
        self.falla: set[str] = set()   # vistas cuya lectura revienta

        async def _listar(**kw):
            self.llamadas.append(kw)
            vista = kw.get("vista", "productos")
            if vista in self.falla:
                raise RuntimeError(f"WordPress caído ({vista})")
            total = CATALOGO if vista == "omnicanal" else SIN_BORRADORES
            return [], total, total

        parches = [
            mock.patch.object(woocommerce, "listar_productos", side_effect=_listar),
            mock.patch.object(meli, "contar_publicados",
                              side_effect=lambda cuenta=None: 40 if cuenta else 100),
            mock.patch.object(amazon, "contar_publicados", return_value=50),
            mock.patch.object(tiktok_panel, "contar_publicados", return_value=30),
            mock.patch.object(temu_panel, "contar_publicados", return_value=20),
            mock.patch.object(walmart_panel, "contar_publicados", return_value=10),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

    def _canales(self, **params) -> dict[str, dict]:
        r = self.c.get("/api/canales", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return {c["id"]: c for c in r.json()}

    def _llamada(self, vista: str) -> dict:
        encontradas = [k for k in self.llamadas if k.get("vista", "productos") == vista]
        self.assertEqual(len(encontradas), 1, self.llamadas)
        return encontradas[0]


class DosTotalesDeGeneral(_Base):
    def test_total_productos_sale_de_la_vista_por_defecto(self):
        general = self._canales()[Canal.GENERAL.value]
        self.assertEqual(general["total_productos"], SIN_BORRADORES)
        llamada = self._llamada("productos")
        # La llamada de siempre: sin vista explícita (la de la pestaña General).
        self.assertNotIn("vista", llamada)
        self.assertEqual((llamada["page"], llamada["per_page"]), (1, 1))

    def test_total_catalogo_sale_de_la_vista_omnicanal(self):
        general = self._canales()[Canal.GENERAL.value]
        self.assertEqual(general["total_catalogo"], CATALOGO)
        # El resto de la llamada, idéntico al de total_productos: mismo
        # listado que el encabezado, solo cambia la vista.
        catalogo = dict(self._llamada("omnicanal"))
        productos = dict(self._llamada("productos"))
        self.assertEqual(catalogo.pop("vista"), "omnicanal")
        self.assertEqual(catalogo, productos)
        self.assertEqual(len(self.llamadas), 2)

    def test_los_dos_totales_van_en_paralelo(self):
        """Cada lectura espera a que la otra ya haya empezado. En serie, la
        primera se queda esperando, vence y cae a None."""
        entraron: set[str] = set()

        async def _listar(**kw):
            vista = kw.get("vista", "productos")
            entraron.add(vista)
            await asyncio.wait_for(_ambas(entraron), timeout=2)
            total = CATALOGO if vista == "omnicanal" else SIN_BORRADORES
            return [], total, total

        async def _ambas(conjunto: set[str]):
            while len(conjunto) < 2:
                await asyncio.sleep(0.01)

        with mock.patch.object(woocommerce, "listar_productos", side_effect=_listar):
            general = self._canales()[Canal.GENERAL.value]
        self.assertEqual(general["total_productos"], SIN_BORRADORES)
        self.assertEqual(general["total_catalogo"], CATALOGO)

    def test_si_falla_el_catalogo_sigue_el_total_de_la_pestana(self):
        self.falla = {"omnicanal"}
        general = self._canales()[Canal.GENERAL.value]
        self.assertIsNone(general["total_catalogo"])   # «—», nunca 0
        self.assertEqual(general["total_productos"], SIN_BORRADORES)

    def test_si_falla_el_total_de_la_pestana_sigue_el_catalogo(self):
        self.falla = {"productos"}
        general = self._canales()[Canal.GENERAL.value]
        self.assertIsNone(general["total_productos"])
        self.assertEqual(general["total_catalogo"], CATALOGO)

    def test_si_fallan_los_dos_ambos_quedan_en_none(self):
        self.falla = {"productos", "omnicanal"}
        general = self._canales()[Canal.GENERAL.value]
        self.assertIsNone(general["total_productos"])
        self.assertIsNone(general["total_catalogo"])

    def test_sin_totales_no_se_lee_nada(self):
        general = self._canales(incluir_totales="false")[Canal.GENERAL.value]
        self.assertIsNone(general["total_productos"])
        self.assertIsNone(general["total_catalogo"])
        self.assertEqual(self.llamadas, [])


class DemasCanales(_Base):
    def test_ningun_otro_canal_trae_total_catalogo(self):
        canales = self._canales()
        otros = {k: v for k, v in canales.items() if k != Canal.GENERAL.value}
        self.assertTrue(otros)
        for cid, c in otros.items():
            self.assertIsNone(c.get("total_catalogo"), cid)

    def test_sus_totales_no_cambian(self):
        canales = self._canales()
        self.assertEqual(canales[Canal.MERCADO_LIBRE.value]["total_productos"], 100)
        self.assertEqual(canales[Canal.AMAZON.value]["total_productos"], 50)
        self.assertEqual(canales[Canal.TIKTOK.value]["total_productos"], 30)
        self.assertEqual(canales[Canal.TEMU.value]["total_productos"], 20)
        self.assertEqual(canales[Canal.WALMART.value]["total_productos"], 10)
        for s in canales[Canal.MERCADO_LIBRE.value]["subcuentas"]:
            self.assertEqual(s["total_productos"], 40, s["id"])


class ReglaOnceCanales(unittest.TestCase):
    def test_ninguna_lectura_sincrona_en_una_corrutina(self):
        fuera = sincronas_en_corrutinas(BACKEND / "routers" / "canales.py")
        self.assertEqual(fuera, [], "Envuélvelas en await asyncio.to_thread(...): "
                                    + ", ".join(fuera))


if __name__ == "__main__":
    unittest.main()
