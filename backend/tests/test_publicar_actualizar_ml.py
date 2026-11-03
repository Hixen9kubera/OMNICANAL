"""«Actualizar en ML»: cada parte en su propio envío, las dos cuentas independientes (v0.721).

── QUÉ FIJAN ────────────────────────────────────────────
Hasta v0.720 título y atributos iban en UN PUT y la descripción solo salía si ese PUT entraba: un
atributo con un valor que ML no acepta tiraba toda la actualización de la cuenta. Y las fotos y la
categoría no se mandaban nunca. Medido en `ml_backlog`: 39 actualizaciones fallidas del 30-jul al
27-oct por valores tipo lista (`"['Blanco']"`, `"['Hombre'"`).

  1. Una lista (o su repr) se manda como «Blanco, Negro»; los nombres de Woo no viajan como ID.
  2. ML rechaza un atributo → los demás entran, la descripción también, y la cuenta queda en error
     con el atributo nombrado (para corregirlo).
  3. ML contesta 200 y conserva el título (publicación con ventas) → «omitido», no error.
  4. Si una foto no sube, NO se manda el PUT de fotos (borraría las demás de la publicación).
  5. La categoría viaja con los atributos y solo si la persona la confirmó.
  6. Una cuenta que falla no frena a la otra; `partes` apaga lo que no se quiere tocar.

── NO SE TOCA NADA REAL ─────────────────────────────────
ML va por `httpx.MockTransport`; Woo, el token y la bitácora, simulados.

    cd backend && python -m unittest tests.test_publicar_actualizar_ml -v
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import publicar  # noqa: E402

_CLIENTE_REAL = httpx.AsyncClient
ATTR_MALO = {"cause": [{"type": "error", "code": "invalid.item.attribute.values",
                        "message": "Attribute [MAIN_COLOR] is not valid, item values [(null:Azulado)]"}],
             "message": "Validation error", "status": 400}


def _correr_una(cambios, item, responder, fotos_subidas=None):
    """`_update_ml_una` contra un ML simulado. Devuelve (resultado, envíos [(método, ruta, cuerpo)])."""
    envios = []

    def handler(request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content or b"{}")
        envios.append((request.method, request.url.path, cuerpo))
        status, resp = responder(request.method, request.url.path, cuerpo)
        return httpx.Response(status, json=resp)

    def cliente(*a, **kw):
        return _CLIENTE_REAL(*a, transport=httpx.MockTransport(handler), **kw)

    with mock.patch.object(publicar.httpx, "AsyncClient", cliente), \
         mock.patch.object(publicar.meli, "access_token_async", mock.AsyncMock(return_value="TOK")), \
         mock.patch.object(publicar, "_subir_fotos_ml", return_value=fotos_subidas or []):
        r = asyncio.run(publicar._update_ml_una("BEKURA", "MLM1", cambios, item))
    return r, envios


def _estados(r):
    return {p["parte"]: p["estado"] for p in r["partes"]}


class Valores(unittest.TestCase):
    def test_lista_y_su_repr(self):
        self.assertEqual(publicar._valor_ml(["Blanco", "Negro"]), "Blanco, Negro")
        self.assertEqual(publicar._valor_ml("['Blanco', 'Negro']"), "Blanco, Negro")
        self.assertEqual(publicar._valor_ml("['Hombre'"), "Hombre")      # ya partida por comas
        self.assertEqual(publicar._valor_ml("  Mate "), "Mate")
        self.assertEqual(publicar._valor_ml("[]"), "")

    def test_solo_ids_de_ml_sin_repetir(self):
        attrs = publicar._attrs_ml({"atributos": [
            {"nombre": "Peso", "valor": "1 kg"},                  # nombre de Woo: ML lo ignora
            {"nombre": "MAIN_COLOR", "valor": "['Blanco']"},
            {"nombre": "MAIN_COLOR", "valor": "Negro"},           # repetido: gana el primero
            {"nombre": "BRAND", "valor": ""},                     # vacío
        ]})
        self.assertEqual(attrs, [{"id": "MAIN_COLOR", "value_name": "Blanco"}])


class UnaCuenta(unittest.TestCase):
    def test_atributo_rechazado_no_tumba_lo_demas(self):
        def ml(metodo, ruta, cuerpo):
            if ruta == "/items/MLM1" and any(a["id"] == "MAIN_COLOR" for a in cuerpo.get("attributes", [])):
                return 400, ATTR_MALO
            return 200, {"title": cuerpo.get("title", "Nuevo")}

        cambios = {"title": "Nuevo", "description": "Texto",
                   "attributes": [{"id": "MAIN_COLOR", "value_name": "Azulado"},
                                  {"id": "BRAND", "value_name": "Ferrahome"}]}
        r, envios = _correr_una(cambios, {"title": "Viejo"}, ml)
        self.assertEqual(_estados(r), {"titulo": "ok", "atributos": "error", "descripcion": "ok"})
        self.assertFalse(r["ok"])
        self.assertIn("MAIN_COLOR", r["error"])
        # El reintento llevó solo los buenos.
        self.assertIn(("PUT", "/items/MLM1", {"attributes": [{"id": "BRAND", "value_name": "Ferrahome"}]}),
                      envios)
        self.assertTrue(r["explicacion"])

    def test_ml_conserva_el_titulo(self):
        r, _ = _correr_una({"title": "Nuevo"}, {"title": "Viejo", "sold_quantity": 4},
                           lambda m, ruta, c: (200, {"title": "Viejo"}))
        self.assertEqual(_estados(r), {"titulo": "omitido"})
        self.assertTrue(r["ok"])
        self.assertIn("4 venta", r["partes"][0]["detalle"])

    def test_titulo_igual_no_se_envia(self):
        r, envios = _correr_una({"title": "Mismo  título"}, {"title": "mismo título"},
                                lambda *a: (500, {}))
        self.assertEqual(_estados(r), {"titulo": "sin_cambio"})
        self.assertEqual(envios, [])

    def test_family_name_no_se_intenta(self):
        r, envios = _correr_una({"title": "Nuevo"}, {"title": "Viejo", "family_name": "Lámpara"},
                                lambda *a: (500, {}))
        self.assertEqual(_estados(r), {"titulo": "omitido"})
        self.assertEqual(envios, [])

    def test_foto_que_no_sube_no_borra_las_demas(self):
        r, envios = _correr_una({"pictures": ["https://x/1.jpg", "https://x/2.jpg"]}, {},
                                lambda *a: (200, {}), fotos_subidas=["P1"])
        self.assertEqual(_estados(r), {"fotos": "error"})
        self.assertEqual(envios, [])

    def test_fotos_reemplazan_por_id(self):
        r, envios = _correr_una({"pictures": ["https://x/1.jpg", "https://x/2.jpg"]}, {},
                                lambda *a: (200, {}), fotos_subidas=["P1", "P2"])
        self.assertTrue(r["ok"])
        self.assertEqual(envios, [("PUT", "/items/MLM1", {"pictures": [{"id": "P1"}, {"id": "P2"}]})])

    def test_categoria_viaja_con_los_atributos(self):
        attrs = [{"id": "BRAND", "value_name": "Ferrahome"}]
        r, envios = _correr_una({"category_id": "MLM2", "attributes": attrs}, {"category_id": "MLM9"},
                                lambda m, ruta, c: (200, {"category_id": "MLM2"}))
        self.assertEqual(_estados(r), {"categoria": "ok", "atributos": "ok"})
        self.assertEqual(envios, [("PUT", "/items/MLM1", {"category_id": "MLM2", "attributes": attrs})])

    def test_categoria_rechazada_igual_manda_atributos(self):
        def ml(metodo, ruta, cuerpo):
            if "category_id" in cuerpo:
                return 400, {"message": "category_id can not be modified", "status": 400}
            return 200, {}

        r, envios = _correr_una({"category_id": "MLM2", "attributes": [{"id": "BRAND", "value_name": "F"}]},
                                {"category_id": "MLM9"}, ml)
        self.assertEqual(_estados(r), {"categoria": "error", "atributos": "ok"})
        self.assertIn("can not be modified", r["error"])
        self.assertEqual(len(envios), 2)

    def test_descripcion_sin_crear_se_crea(self):
        def ml(metodo, ruta, cuerpo):
            return (404, {}) if metodo == "PUT" else (201, {})

        r, envios = _correr_una({"description": "Texto"}, {}, ml)
        self.assertTrue(r["ok"])
        self.assertEqual([e[0] for e in envios], ["PUT", "POST"])


class DosCuentas(unittest.TestCase):
    CAMPOS = {"titulo": "Lámpara de pie", "descripcion": "Texto",
              "atributos": [{"nombre": "BRAND", "valor": "Ferrahome"}]}
    PUBS = [{"cuenta": "BEKURA", "item_id": "MLM1"}, {"cuenta": "SANCORFASHION", "item_id": "MLM2"}]

    def _confirmar(self, req, update, destino=([], "")):
        async def est(_pubs):
            return {"BEKURA": {"vivo": True, "item": {"category_id": "MLM9"}},
                    "SANCORFASHION": {"vivo": True, "item": {"category_id": "MLM9"}}}

        with mock.patch.object(publicar, "_ml_publicaciones", return_value=self.PUBS), \
             mock.patch.object(publicar, "_estados_items_ml", side_effect=est), \
             mock.patch.object(publicar, "_destino_ml", mock.AsyncMock(return_value=destino)), \
             mock.patch.object(publicar, "_update_ml_una", side_effect=update) as upd, \
             mock.patch.object(publicar, "_guardar_backlog_ml") as backlog:
            r = asyncio.run(publicar._confirmar_ml({"sku": "LAM-1", "wc_id": 1, "campos": self.CAMPOS, **req}))
        return r, upd, backlog

    def test_una_falla_la_otra_entra(self):
        async def update(cuenta, item_id, cambios, item):
            ok = cuenta == "SANCORFASHION"
            return {"cuenta": cuenta, "item_id": item_id, "ok": ok, "error": None if ok else "Fotos: x",
                    "ml_status": 200, "desc_status": 200, "partes": [], "_response": []}

        r, upd, backlog = self._confirmar({}, update)
        self.assertEqual(upd.call_count, 2)
        self.assertFalse(r["ok"])
        self.assertEqual([(x["cuenta"], x["ok"]) for x in r["resultados"]],
                         [("BEKURA", False), ("SANCORFASHION", True)])
        # Las dos quedan en la bitácora, la fallida con su error para corregirla.
        self.assertEqual([c.args[4] for c in backlog.call_args_list], [False, True])
        self.assertEqual(backlog.call_args_list[0].args[5], "Fotos: x")

    def _cambios(self, req, destino=(["https://x/1.jpg"], "MLM2")):
        vistos = []

        async def update(cuenta, item_id, cambios, item):
            vistos.append(cambios)
            return {"cuenta": cuenta, "item_id": item_id, "ok": True, "error": None,
                    "ml_status": 200, "desc_status": 200, "partes": [], "_response": []}

        r, _, _ = self._confirmar(req, update, destino)
        return r, vistos

    def test_partes_apaga_lo_no_marcado(self):
        _, vistos = self._cambios({"partes": ["descripcion"]}, destino=([], ""))
        self.assertEqual(vistos[0]["title"], "")
        self.assertEqual(vistos[0]["attributes"], [])
        self.assertEqual(vistos[0]["description"], "Texto")

    def test_categoria_solo_confirmada(self):
        _, sin = self._cambios({})
        _, con = self._cambios({"categoria_confirmada": "MLM2"})
        self.assertEqual(sin[0]["category_id"], "")
        self.assertEqual(con[0]["category_id"], "MLM2")
        self.assertEqual(con[0]["pictures"], ["https://x/1.jpg"])

    def test_nada_marcado(self):
        r, vistos = self._cambios({"partes": ["fotos"]}, destino=([], ""))
        self.assertFalse(r["ok"])
        self.assertEqual(vistos, [])


class VistaPrevia(unittest.TestCase):
    def test_fotos_ventas_y_categoria_opcional(self):
        async def est(_pubs):
            return {"BEKURA": {"vivo": True, "item": {"title": "Viejo", "sold_quantity": 3, "category_id": "MLM9"}}}

        rev = {"actual": {"category_id": "MLM2"}, "sugeridas": [], "veredicto": "coincide"}
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=[{"cuenta": "BEKURA", "item_id": "MLM1"}]), \
             mock.patch.object(publicar, "_estados_items_ml", side_effect=est), \
             mock.patch.object(publicar, "_destino_ml", mock.AsyncMock(return_value=(["https://x/1.jpg"], "MLM2"))), \
             mock.patch.object(publicar, "_prod_alta", mock.AsyncMock(return_value={"title": "Viejo"})), \
             mock.patch.object(publicar, "_revision_categoria_ml", mock.AsyncMock(return_value=rev)):
            r = asyncio.run(publicar._preview_ml({"sku": "LAM-1", "wc_id": 1, "campos": {"titulo": "Nuevo"}}))
        self.assertEqual(r["modo"], "actualizar")
        self.assertEqual(r["fotos"], ["https://x/1.jpg"])
        self.assertEqual(r["operaciones"]["fotos"], 1)
        self.assertTrue(r["categoria_opcional"])
        self.assertIs(r["categoria"], rev)
        self.assertTrue(any("3 venta" in a for a in r["avisos"]))
        self.assertTrue(any("MOVERLA" in a for a in r["avisos"]))


if __name__ == "__main__":
    unittest.main()
