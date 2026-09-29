"""El sync de Amazon completo: estado, rotación por reloj, catálogo sin tope y
descubrimiento (29-sep).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **BUYABLE gana** aunque Amazon lo mande en segundo lugar.
2. **La rotación por reloj visita TODO**: vueltas seguidas leen tajadas contiguas
   y en ⌈n / lote⌉ vueltas se cubre el universo entero, sin repetir, aunque
   se dé la vuelta; con el interruptor apagado, el orden de siempre.
3. **El catálogo se lee completo pese al tope de 1,000**: la ventana que pasa
   de 1,000 se parte; con 2,500 publicaciones simuladas salen las 2,500.
4. **El descubrimiento** registra/refresca solo lo de NUESTRO catálogo, cuenta
   aparte lo que Amazon generó solo, no escribe en seco y nunca lanza.

Sin red ni base de datos.

    cd backend && python -m unittest tests.test_amazon_sync_completo -v
"""
from __future__ import annotations

import asyncio
import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from services import amazon, channel_read, inventario
from services import supabase_db as sdb


class Estado(unittest.TestCase):
    def test_buyable_gana_en_cualquier_lugar(self):
        self.assertEqual(amazon.estado_listing(["DISCOVERABLE", "BUYABLE"]), "BUYABLE")
        self.assertEqual(amazon.estado_listing(["BUYABLE", "DISCOVERABLE"]), "BUYABLE")
        self.assertEqual(amazon.estado_listing(["DISCOVERABLE"]), "DISCOVERABLE")
        self.assertIsNone(amazon.estado_listing([]))
        self.assertEqual(amazon.estado_listing("BUYABLE"), "BUYABLE")

    def test_el_lector_por_lotes_lo_usa(self):
        d = {"summaries": [{"asin": "B0X", "status": ["DISCOVERABLE", "BUYABLE"]}]}
        self.assertEqual(amazon._parsear_listing(d)["estado"], "BUYABLE")


class Rotacion(unittest.TestCase):
    def setUp(self):
        mock.patch.object(inventario.settings, "sync_interval_min", 15).start()
        self.addCleanup(mock.patch.stopall)
        self.filas = [{"sku": f"SKU-{i:04d}"} for i in range(1695)]

    def test_cubre_todo_en_una_vuelta_completa_sin_repetir(self):
        vueltas = math.ceil(1695 / 80)
        vistos: list[str] = []
        for r in range(1000, 1000 + vueltas):
            t = inventario._tajada_reloj(self.filas, 80, ahora=r * 900)
            self.assertEqual(len(t), 80)
            vistos += [x["sku"] for x in t]
        self.assertEqual(len(set(vistos)), 1695)   # todas, aunque la última dé la vuelta

    def test_vueltas_seguidas_leen_tajadas_distintas(self):
        a = inventario._tajada_reloj(self.filas, 80, ahora=5000 * 900)
        b = inventario._tajada_reloj(self.filas, 80, ahora=5001 * 900)
        self.assertFalse({x["sku"] for x in a} & {x["sku"] for x in b})

    def test_universo_chico_sin_duplicados_y_vacio(self):
        pocas = self.filas[:50]
        t = inventario._tajada_reloj(pocas, 80, ahora=12345 * 900)
        self.assertEqual(sorted(x["sku"] for x in t), sorted(x["sku"] for x in pocas))
        self.assertEqual(inventario._tajada_reloj([], 80), [])

    def test_con_el_interruptor_apagado_el_orden_de_siempre(self):
        viejo = datetime(2026, 8, 1)
        with mock.patch.object(inventario.settings, "supabase_read_channel", True), \
             mock.patch.object(inventario.settings, "supabase_read_publicaciones", True), \
             mock.patch.object(channel_read, "universo_amazon",
                               side_effect=lambda: [dict(f) for f in self.filas]), \
             mock.patch.object(channel_read, "vistos_amazon",
                               return_value={f["sku"]: viejo for f in self.filas}), \
             mock.patch.object(inventario.lecturas_fuente, "anotar"):
            with mock.patch.object(inventario.settings, "sync_amazon_rotacion_reloj", False):
                apagado = inventario._pubs_amazon(80)
            with mock.patch.object(inventario.settings, "sync_amazon_rotacion_reloj", True), \
                 mock.patch.object(inventario, "_tajada_reloj", return_value=["TAJADA"]) as taj:
                encendido = inventario._pubs_amazon(80)
        self.assertEqual(len(apagado), 80)
        self.assertEqual(encendido, ["TAJADA"])
        taj.assert_called_once()


class _AmazonFalso:
    """searchListingsItems con 2,500 publicaciones y el tope de 1,000 por consulta."""

    def __init__(self, n=2500, primer_429=False):
        base = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self.pubs = [{"sku": f"P-{i:05d}", "creado": base + timedelta(hours=17 * i)}
                     for i in range(n)]
        self.pendiente_429 = primer_429
        self.llamadas = 0

    def __call__(self, *a, **k):
        falso = self

        class Cliente:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *e):
                return False

            async def get(self, ruta, params=None, headers=None):
                falso.llamadas += 1
                if falso.pendiente_429:
                    falso.pendiente_429 = False
                    return mock.Mock(status_code=429, text="")
                p = params or {}
                sel = falso.pubs
                if p.get("createdAfter"):
                    a = datetime.fromisoformat(p["createdAfter"].replace("Z", "+00:00"))
                    b = datetime.fromisoformat(p["createdBefore"].replace("Z", "+00:00"))
                    sel = [x for x in sel if a <= x["creado"] < b]
                ofs = int(p.get("pageToken") or 0)
                tam = int(p.get("pageSize") or 20)
                visibles = sel[:1000]                       # el tope de Amazon
                pag = visibles[ofs:ofs + tam]
                sig = str(ofs + tam) if ofs + tam < len(visibles) else None
                cuerpo = {"numberOfResults": len(sel),
                          "items": [{"sku": x["sku"], "summaries": [
                              {"asin": "B" + x["sku"][2:], "status": ["BUYABLE"]}]} for x in pag],
                          "pagination": {"nextToken": sig} if sig else {}}
                return mock.Mock(status_code=200, json=lambda c=cuerpo: c)
        return Cliente()


class Catalogo(unittest.TestCase):
    def setUp(self):
        mock.patch.object(amazon, "_access_token", new=mock.AsyncMock(return_value="tok")).start()
        mock.patch.object(amazon.asyncio, "sleep", new=mock.AsyncMock()).start()
        self.addCleanup(mock.patch.stopall)

    def test_lee_las_2500_pese_al_tope_de_1000(self):
        falso = _AmazonFalso()
        with mock.patch.object(amazon.httpx, "AsyncClient", falso):
            r = asyncio.run(amazon.listar_publicaciones())
        self.assertEqual((r["total"], len(r["items"]), r["completo"]), (2500, 2500, True))
        self.assertEqual(r["items"][0]["estado"], "BUYABLE")

    def test_un_429_se_espera_y_se_sigue(self):
        falso = _AmazonFalso(n=30, primer_429=True)
        with mock.patch.object(amazon.httpx, "AsyncClient", falso):
            r = asyncio.run(amazon.listar_publicaciones())
        self.assertEqual(len(r["items"]), 30)
        self.assertTrue(r["completo"])


class Descubrir(unittest.TestCase):
    def setUp(self):
        items = [{"sku": s, "asin": "B" + s, "precio": 10.0, "estado": "BUYABLE",
                  "stock_real": 3, "stock_fba": None, "es_fba": False}
                 for s in ("CAM-0030-MAT", "SIL-008-GRI", "8Z-86S1-B5IM", "YA-ESTABA")]
        mock.patch.object(amazon, "listar_publicaciones", new=mock.AsyncMock(
            return_value={"items": items, "total": 4, "completo": True})).start()
        self.fetch = mock.patch.object(sdb, "fetch_all", side_effect=lambda sql, *a: (
            [{"sku": s} for s in ("CAM-0030-MAT", "SIL-008-GRI", "YA-ESTABA")]
            if "core.products" in sql else [{"sku": "YA-ESTABA"}])).start()
        self.upsert = mock.patch.object(inventario, "_upsert_async",
                                        new=mock.AsyncMock(side_effect=lambda f: len(f))).start()
        self.addCleanup(mock.patch.stopall)

    def test_en_seco_solo_cuenta(self):
        r = asyncio.run(inventario.descubrir_amazon(aplicar=False))
        self.assertTrue(r["ok"])
        self.assertEqual((r["del_catalogo"], r["nuevas"], r["fuera_de_catalogo"]), (3, 2, 1))
        self.assertEqual(r["nuevas_muestra"], ["CAM-0030-MAT", "SIL-008-GRI"])
        self.assertEqual(r["fuera_muestra"], ["8Z-86S1-B5IM"])
        self.upsert.assert_not_awaited()

    def test_aplicando_escribe_solo_lo_del_catalogo(self):
        r = asyncio.run(inventario.descubrir_amazon(aplicar=True))
        filas = [f for c in self.upsert.await_args_list for f in c.args[0]]
        self.assertEqual(sorted(f["sku"] for f in filas), ["CAM-0030-MAT", "SIL-008-GRI", "YA-ESTABA"])
        self.assertEqual(r["escritas"], 3)
        f = next(f for f in filas if f["sku"] == "CAM-0030-MAT")
        self.assertEqual((f["canal"], f["item_id"], f["situacion"], f["stock_real"]),
                         ("amazon", "BCAM-0030-MAT", "BUYABLE", 3))

    def test_si_amazon_falla_no_lanza_ni_escribe(self):
        with mock.patch.object(amazon, "listar_publicaciones",
                               new=mock.AsyncMock(side_effect=RuntimeError("Amazon 500"))):
            r = asyncio.run(inventario.descubrir_amazon(aplicar=True))
        self.assertFalse(r["ok"])
        self.assertIn("Amazon 500", r["error"])
        self.upsert.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
