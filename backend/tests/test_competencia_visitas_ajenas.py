"""Pruebas de la puerta de visitas AJENAS (`competencia_ml.visitas_30d_ajena`).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
ML cerró las visitas de publicaciones ajenas: desde ~oct-2026 `/visits/items`,
`/items/visits` y `/items/{id}/visits/time_window` dan 403 «The caller is not
authorized to access this resource» con los tokens de las dos cuentas. La
captura lo tragaba en silencio: la medición del 12-oct guardó 0 de 10 filas con
visitas y nadie se enteró.

1. **El primer 403 de una ajena se anota UNA vez** en `ops.process_log`
   (competencia / visitas_ajenas / denegado) con lo que contestó ML, aunque
   varias llamadas choquen con él a la vez.
2. **Después ya no se pregunta**: las demás ajenas devuelven None sin tocar la
   API. Una tanda de rivales sin medición previa cuesta UNA llamada (la sonda),
   no una por rival.
3. **Las NUESTRAS siguen**: `visitas_30d` no mira la puerta, y las filas propias
   de una búsqueda o un ranking se piden con el token de su cuenta.
4. El cierre caduca: pasado `_TTL_PUERTA_AJENAS` se vuelve a sondear, y un 200
   abre la puerta.
5. El buzón `estado` de `_get` trae el código de la ÚLTIMA respuesta, también
   tras esperar un 429.

Sin red ni base.

    cd backend && python -m unittest tests.test_competencia_visitas_ajenas -v
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from services import competencia_captura as cc
from services import competencia_ml as cm

_NEGADO = ('{"message":"The caller is not authorized to access this resource",'
           '"error":"forbidden","status":403,"cause":[]}')


class _Resp:
    def __init__(self, code: int, cuerpo: dict | None = None, texto: str = ""):
        self.status_code = code
        self._cuerpo = cuerpo
        self.text = texto or json.dumps(cuerpo or {})
        self.headers = {}

    def json(self):
        return self._cuerpo


class _ML:
    """API falsa: las publicaciones de `propias` ({item: cuenta}) dan 200 con el
    token de SU cuenta; todo lo demás es un rival y da 403."""

    def __init__(self, propias: dict[str, str] | None = None, rivales_abiertos=False):
        self.propias = propias or {}
        self.rivales_abiertos = rivales_abiertos
        self.llamadas: list[tuple[str, str]] = []

    def get(self, url, params=None, headers=None, timeout=None):
        token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        item = url.split("/items/")[1].split("/")[0]
        self.llamadas.append((item, token))
        if self.propias.get(item) and token == f"tok-{self.propias[item]}":
            return _Resp(200, {"total_visits": 120, "results": []})
        if self.rivales_abiertos and item not in self.propias:
            return _Resp(200, {"total_visits": 7, "results": []})
        return _Resp(403, texto=_NEGADO)


class _Base(unittest.TestCase):
    def setUp(self):
        cm.reiniciar_puerta_visitas_ajenas()
        self.addCleanup(cm.reiniciar_puerta_visitas_ajenas)
        self.ml = _ML({"MLM100": "BEKURA", "MLM200": "SANCORFASHION"})
        mock.patch.object(cm.requests, "get", side_effect=self.ml.get).start()
        mock.patch.object(cm, "_token",
                          side_effect=lambda c: f"tok-{(c or '').upper()}").start()
        self.bitacora = mock.patch.object(cm.supabase_db, "execute").start()
        self.addCleanup(mock.patch.stopall)

    def filas_bitacora(self):
        return [c for c in self.bitacora.call_args_list
                if "visitas_ajenas" in c.args[0]]


class Puerta(_Base):
    def test_primer_403_cierra_y_anota(self):
        with self.assertLogs("omnicanal.competencia.ml", "WARNING"):
            self.assertIsNone(cm.visitas_30d_ajena("MLM9001"))
        self.assertEqual(cm.puerta_visitas_ajenas(), "cerrada")
        filas = self.filas_bitacora()
        self.assertEqual(len(filas), 1)
        sql, (detalle,) = filas[0].args
        self.assertIn("'competencia'", sql)
        self.assertIn("'denegado'", sql)
        d = json.loads(detalle)
        self.assertEqual(d["http"], 403)
        self.assertEqual(d["item_id"], "MLM9001")
        self.assertEqual(d["cuenta"], "BEKURA")
        self.assertIn("not authorized", d["respuesta"])

    def test_cerrada_no_llama_a_la_api(self):
        cm.visitas_30d_ajena("MLM9001")
        for i in range(30):
            self.assertIsNone(cm.visitas_30d_ajena(f"MLM9{i:03d}5"))
        self.assertEqual(len(self.ml.llamadas), 1)
        self.assertEqual(len(self.filas_bitacora()), 1)

    def test_403_simultaneos_anotan_una_vez(self):
        for _ in range(5):
            cm._cerrar_puerta("MLM9001", "bekura", _NEGADO)
        self.assertEqual(len(self.filas_bitacora()), 1)

    def test_las_propias_siguen_con_la_puerta_cerrada(self):
        cm.visitas_30d_ajena("MLM9001")
        self.assertEqual(cm.visitas_30d("MLM100", "bekura"), 120)
        self.assertEqual(cm.visitas_30d("MLM200", "sancorfashion"), 120)
        self.assertEqual(cm.puerta_visitas_ajenas(), "cerrada")

    def test_200_abre_la_puerta(self):
        self.ml.rivales_abiertos = True
        self.assertEqual(cm.visitas_30d_ajena("MLM9001"), 7)
        self.assertEqual(cm.puerta_visitas_ajenas(), "abierta")
        self.assertEqual(self.filas_bitacora(), [])

    def test_el_cierre_caduca_y_se_vuelve_a_sondear(self):
        cm.visitas_30d_ajena("MLM9001")
        with mock.patch.object(cm, "_TTL_PUERTA_AJENAS", 0):
            self.assertIsNone(cm.puerta_visitas_ajenas())
            cm.visitas_30d_ajena("MLM9002")
        self.assertEqual(len(self.ml.llamadas), 2)
        self.assertEqual(len(self.filas_bitacora()), 2)

    def test_si_falla_la_bitacora_no_revienta(self):
        self.bitacora.side_effect = RuntimeError("kubera caída")
        self.assertIsNone(cm.visitas_30d_ajena("MLM9001"))
        self.assertEqual(cm.puerta_visitas_ajenas(), "cerrada")

    def test_error_de_red_no_cierra(self):
        with mock.patch.object(cm.requests, "get", side_effect=OSError("sin red")):
            self.assertIsNone(cm.visitas_30d_ajena("MLM9001"))
        self.assertIsNone(cm.puerta_visitas_ajenas())
        self.assertEqual(self.filas_bitacora(), [])


class BuzonDeGet(_Base):
    def test_429_y_luego_403_deja_el_403(self):
        resp = iter([_Resp(429), _Resp(403, texto=_NEGADO)])
        with mock.patch.object(cm.requests, "get", side_effect=lambda *a, **k: next(resp)), \
             mock.patch.object(cm.time, "sleep"):
            est: dict = {}
            self.assertIsNone(cm._get("/items/MLM1/visits/time_window", estado=est))
        self.assertEqual(est["code"], 403)
        self.assertIn("not authorized", est["cuerpo"])


class Captura(_Base):
    def test_tanda_de_rivales_cuesta_una_llamada(self):
        ids = [f"MLM90{i:02d}1" for i in range(25)]
        res = asyncio.run(cc.visitas_rivales(ids))
        self.assertEqual(res, [None] * 25)
        self.assertEqual(len(self.ml.llamadas), 1)
        self.assertEqual(len(self.filas_bitacora()), 1)
        # Ya medida, la tanda siguiente no llama.
        asyncio.run(cc.visitas_rivales(ids))
        self.assertEqual(len(self.ml.llamadas), 1)

    def test_rivales_abiertos_se_piden_todos(self):
        self.ml.rivales_abiertos = True
        ids = [f"MLM90{i:02d}1" for i in range(6)]
        self.assertEqual(asyncio.run(cc.visitas_rivales(ids)), [7] * 6)
        self.assertEqual(len(self.ml.llamadas), 6)

    def test_busqueda_conserva_las_nuestras_con_su_cuenta(self):
        filas = [{"externo_id": "MLM9001", "url": "https://x/MLM-9001-a"},
                 {"externo_id": "MLM200", "url": "https://x/MLM-200-b"},
                 {"externo_id": "MLM9002", "url": "https://x/MLM-9002-c"},
                 {"externo_id": "MLM100", "url": "https://x/MLM-100-d"}]
        nuestras = {"MLM100": {"sku": "A-1", "cuenta": "BEKURA"},
                    "MLM200": {"sku": "B-2", "cuenta": "SANCORFASHION"}}
        n = asyncio.run(cc.enriquecer_visitas(filas, nuestras))
        self.assertEqual(n, 2)
        self.assertEqual([f.get("visitas_30d") for f in filas], [None, 120, None, 120])
        # Sonda del rival + las dos nuestras; el segundo rival ya no se pidió.
        self.assertEqual(sorted(self.ml.llamadas),
                         [("MLM100", "tok-BEKURA"), ("MLM200", "tok-SANCORFASHION"),
                          ("MLM9001", "tok-BEKURA")])
        self.assertEqual(len(self.filas_bitacora()), 1)

    def test_ranking_conserva_las_marcadas_como_nuestras(self):
        filas = [{"externo_id": "MLM9001"}, {"externo_id": "MLM100", "es_nuestro": True},
                 {"externo_id": "MLM9002"}]
        with mock.patch.object(cm, "mas_vendidos_categoria", return_value=[]), \
             mock.patch.object(cm, "reviews", return_value=None):
            asyncio.run(cc._enriquecer_ranking("MLM1234", filas))
        self.assertEqual([f.get("visitas_30d") for f in filas], [None, 120, None])
        self.assertEqual(sorted(i for i, _ in self.ml.llamadas), ["MLM100", "MLM9001"])


if __name__ == "__main__":
    unittest.main()
