"""Pruebas de la revisión de categoría de ML por TEXTO y FOTO (`services/categoria_vectores.py`,
Eduardo 27-oct-2026).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **El semáforo**: verde hasta el lugar 5, ámbar hasta el 20, rojo después; con fotos
   decide la fusión, sin fotos el texto; sin lugar, `sin_senal`.
2. **La consulta** es la de la prueba: instrucción + título[:200] + descripción[:600],
   sin HTML. Las huellas se recortan a 512 y quedan con norma 1.
3. **Las propuestas**: texto+fotos primero, luego texto, sin repetir ni incluir la elegida.
4. **El candado**: solo frena un ROJO, solo con la bandera, y un motivo de ≥10 caracteres
   lo destraba. Sin revisión no frena (decisión de Eduardo: se publica con la de siempre).
5. **Nunca revienta**: sin bandera no hace nada; sin clave, sin huellas, con el tope de
   24 h o con OpenRouter caído queda `sin_senal` y se guarda igual.
6. **El alta**: con el candado, un rojo sin motivo NO llega al pipeline.

Sin red ni BD: httpx va por un transporte falso y las consultas se sustituyen.

    cd backend && python -m unittest tests.test_categoria_vectores -v
"""
from __future__ import annotations

import asyncio
import math
import unittest
from unittest import mock

import httpx

from services import categoria_vectores as cv


def _banderas(**kw):
    base = {"categoria_vec_enabled": True, "categoria_vec_candado": False,
            "categoria_vec_tope_diario_usd": 0.5, "openrouter_api_key": "k"}
    base.update(kw)
    return mock.patch.multiple(cv.settings, **base)


class Semaforo(unittest.TestCase):
    def test_umbrales_con_fotos(self):
        self.assertEqual(cv.color(900, 1, True), "verde")
        self.assertEqual(cv.color(1, 5, True), "verde")
        self.assertEqual(cv.color(1, 6, True), "ambar")
        self.assertEqual(cv.color(1, 20, True), "ambar")
        self.assertEqual(cv.color(1, 21, True), "rojo")

    def test_sin_fotos_decide_el_texto(self):
        self.assertEqual(cv.color(3, None, False), "verde")
        self.assertEqual(cv.color(40, None, False), "rojo")

    def test_sin_lugar_es_sin_senal(self):
        self.assertEqual(cv.color(None, None, False), "sin_senal")
        self.assertEqual(cv.color(3, None, True), "sin_senal")


class Consulta(unittest.TestCase):
    def test_instruccion_titulo_y_descripcion_sin_html(self):
        q = cv.texto_producto("  Compresor  A/C ", "<p>Para <b>Mazda</b> CX-7&nbsp;2.3</p>")
        self.assertTrue(q.startswith(cv.INSTRUCCION))
        self.assertEqual(q[len(cv.INSTRUCCION):], "Compresor A/C. Para Mazda CX-7 2.3")

    def test_sin_descripcion_y_recortes(self):
        q = cv.texto_producto("x" * 300, None)
        self.assertEqual(q[len(cv.INSTRUCCION):], "x" * 200)
        q = cv.texto_producto("t", "d" * 900)
        self.assertEqual(len(q) - len(cv.INSTRUCCION), len("t. ") + 600)

    def test_normalizar_recorta_y_deja_norma_uno(self):
        v = cv.normalizar([3.0, 4.0] + [100.0] * 1000)
        self.assertEqual(len(v), cv.DIMS)
        self.assertAlmostEqual(math.sqrt(sum(x * x for x in v)), 1.0, places=6)
        self.assertIsNone(cv.normalizar([0.0, 0.0]))

    def test_promedio_de_fotos(self):
        v = cv.promedio([[1.0, 0.0], [0.0, 1.0]])
        self.assertAlmostEqual(v[0], v[1])
        self.assertAlmostEqual(math.sqrt(sum(x * x for x in v)), 1.0)
        self.assertIsNone(cv.promedio([]))

    def test_literal(self):
        self.assertEqual(cv.literal([0.5, -0.25]), "[0.500000,-0.250000]")


def _fila(cid, rt, rz, nombres=("A", "B")):
    return {"category_id": cid, "rt": rt, "rz": rz, "rf": rz,
            "path_ids": ["MLM1", cid], "path_names": list(nombres)}


class Propuestas(unittest.TestCase):
    def test_fusion_primero_luego_texto_sin_repetir_ni_la_actual(self):
        filas = [_fila("MLM9", 1, 2), _fila("MLM8", 2, 1), _fila("MLM7", 3, 9),
                 _fila("MLM6", 9, 3), _fila("MLM5", 4, 4), _fila("MLM3", 50, 60)]
        p = cv.propuestas(filas, "MLM3", por_fuente=2)
        self.assertEqual([(x["category_id"], x["fuente"]) for x in p],
                         [("MLM8", "texto_y_fotos"), ("MLM9", "texto_y_fotos"),
                          ("MLM7", "texto"), ("MLM5", "texto")])
        self.assertEqual(p[0]["ruta"], "A > B")
        self.assertEqual(p[0]["niveles"][-1], {"id": "MLM8", "name": "B"})


class RangosUnaSenal(unittest.TestCase):
    def test_solo_fotos_mapea_el_lugar_a_fotos_y_fusion(self):
        crudas = [{"category_id": "MLM9", "r": 1, "s": 0.5, "path_ids": ["MLM9"], "path_names": ["X"]}]
        with mock.patch.object(cv, "_sdb") as sdb:
            sdb.return_value.fetch_all.return_value = crudas
            f = cv.rangos("MLM3", None, [0.1] * 4)[0]
        self.assertEqual((f["rt"], f["rf"], f["rz"]), (None, 1, 1))
        self.assertEqual(sdb.return_value.fetch_all.call_args.args[1]["m"], cv.MODELO_FOTO)

    def test_solo_texto(self):
        crudas = [{"category_id": "MLM9", "r": 2, "s": 0.5, "path_ids": ["MLM9"], "path_names": ["X"]}]
        with mock.patch.object(cv, "_sdb") as sdb:
            sdb.return_value.fetch_all.return_value = crudas
            f = cv.rangos("MLM3", [0.1] * 4, None)[0]
        self.assertEqual((f["rt"], f["rf"], f["rz"]), (2, None, 2))


class Candado(unittest.TestCase):
    def test_solo_frena_rojo_con_bandera_y_sin_motivo(self):
        rojo = {"color": "rojo"}
        self.assertIsNone(cv.motivo_candado(rojo, None, candado=False))
        self.assertIsNone(cv.motivo_candado({"color": "ambar"}, None, candado=True))
        self.assertIsNone(cv.motivo_candado(None, None, candado=True))
        self.assertIn("ROJO", cv.motivo_candado(rojo, "  corto ", candado=True))
        self.assertIsNone(cv.motivo_candado(rojo, "Es un repuesto de taller, no de auto", candado=True))

    def test_candado_alta_anota_la_decision(self):
        with _banderas(categoria_vec_candado=True), \
             mock.patch.object(cv, "ultima_revision", return_value={"id": 7, "color": "verde"}), \
             mock.patch.object(cv, "_anotar_decision") as anotar:
            self.assertIsNone(asyncio.run(cv.candado_alta("X-1", "MLM3", None)))
        anotar.assert_called_once_with(7, None)

    def test_candado_alta_frena_rojo_sin_motivo_y_no_anota(self):
        with _banderas(categoria_vec_candado=True), \
             mock.patch.object(cv, "ultima_revision", return_value={"id": 7, "color": "rojo"}), \
             mock.patch.object(cv, "_anotar_decision") as anotar:
            self.assertIn("ROJO", asyncio.run(cv.candado_alta("X-1", "MLM3", "")))
        anotar.assert_not_called()

    def test_en_sombra_rojo_pasa_y_se_anota(self):
        with _banderas(categoria_vec_candado=False), \
             mock.patch.object(cv, "ultima_revision", return_value={"id": 7, "color": "rojo"}), \
             mock.patch.object(cv, "_anotar_decision") as anotar:
            self.assertIsNone(asyncio.run(cv.candado_alta("X-1", "MLM3", None)))
        anotar.assert_called_once()

    def test_bd_caida_no_frena(self):
        with _banderas(categoria_vec_candado=True), \
             mock.patch.object(cv, "ultima_revision", side_effect=RuntimeError("bd")):
            self.assertIsNone(asyncio.run(cv.candado_alta("X-1", "MLM3", None)))

    def test_sin_bandera_ni_toca_la_bd(self):
        with _banderas(categoria_vec_enabled=False), \
             mock.patch.object(cv, "ultima_revision") as leer:
            self.assertIsNone(asyncio.run(cv.candado_alta("X-1", "MLM3", None)))
        leer.assert_not_called()


def _openrouter(estado=200, n_fotos=2):
    def manejar(req: httpx.Request) -> httpx.Response:
        if estado != 200:
            return httpx.Response(estado, json={"error": "caído"})
        import json as _j
        cuerpo = _j.loads(req.content)
        n = len(cuerpo["input"])
        base = [1.0] + [0.0] * 1023
        return httpx.Response(200, json={
            "data": [{"index": i, "embedding": base} for i in range(n)],
            "usage": {"cost": 0.0001 * n}})
    return httpx.MockTransport(manejar)


class Revisar(unittest.TestCase):
    def setUp(self):
        self._cliente = httpx.AsyncClient
        # Sin revisión igual guardada: cada prueba revisa de verdad (ver `Reuso`).
        p = mock.patch.object(cv, "revision_igual", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def _con(self, transporte):
        original = self._cliente
        return mock.patch.object(cv.httpx, "AsyncClient",
                                 lambda **kw: original(transport=transporte, **kw))

    def _revisar(self, fotos=("https://f/1.jpg", "https://f/2.jpg"), filas=None, **banderas):
        filas = filas if filas is not None else [_fila("MLM9", 1, 1), _fila("MLM3", 4, 30)]
        guardado = {}

        def guardar(f):
            guardado.update(f)
            return 11

        with _banderas(**banderas), \
             mock.patch.object(cv, "huellas_listas", return_value=True), \
             mock.patch.object(cv, "gastado_24h", return_value=0.0), \
             mock.patch.object(cv, "rangos", return_value=filas) as rangos, \
             mock.patch.object(cv, "_guardar", side_effect=guardar):
            r = asyncio.run(cv.revisar("X-1", 5, "MLM3", "Compresor", "<p>A/C</p>", list(fotos)))
        return r, guardado, rangos

    def test_sin_bandera_no_hace_nada(self):
        with _banderas(categoria_vec_enabled=False):
            self.assertIsNone(asyncio.run(cv.revisar("X-1", 5, "MLM3", "t", "d", [])))

    def test_con_fotos_decide_la_fusion_y_guarda(self):
        with self._con(_openrouter()):
            r, g, rangos = self._revisar()
        self.assertEqual(r["color"], "rojo")              # fusión 30 > 20
        self.assertEqual((r["rango_texto"], r["rango_fusion"], r["n_fotos"]), (4, 30, 2))
        self.assertEqual(r["revision_id"], 11)
        self.assertEqual([p["category_id"] for p in r["propuestas"]], ["MLM9"])
        self.assertAlmostEqual(g["costo_usd"], 0.0003)     # 1 texto + 2 fotos
        self.assertEqual(g["color"], "rojo")
        self.assertIsNotNone(rangos.call_args.args[2])     # pasó la huella de las fotos

    def test_lo_guardado_se_puede_pasar_a_json(self):
        import json
        from decimal import Decimal
        filas = [_fila("MLM9", 1, 1), {**_fila("MLM3", 4, 30), "st": Decimal("0.4123"), "sf": None}]
        with self._con(_openrouter()):
            _, g, _ = self._revisar(filas=filas)
        json.dumps(g["detalle"])
        json.dumps(g["propuestas"])
        self.assertEqual(g["detalle"]["similitud"]["texto"], 0.4123)

    def test_sin_fotos_decide_el_texto(self):
        with self._con(_openrouter()):
            r, g, rangos = self._revisar(fotos=())
        self.assertEqual(r["color"], "verde")             # texto 4
        self.assertIsNone(r["rango_fusion"])
        self.assertIsNone(rangos.call_args.args[2])

    def test_openrouter_caido_es_sin_senal_y_se_guarda(self):
        with self._con(_openrouter(estado=502)):
            r, g, _ = self._revisar()
        self.assertEqual(r["color"], "sin_senal")
        self.assertEqual(r["motivo_sin_senal"], "OpenRouter no contestó")
        self.assertEqual(g["color"], "sin_senal")

    def test_si_el_texto_no_contesta_decide_con_las_fotos(self):
        def manejar(req):
            import json as _j
            cuerpo = _j.loads(req.content)
            if cuerpo["model"] == cv.MODELO_TEXTO:
                raise httpx.ReadTimeout("lento")
            n = len(cuerpo["input"])
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0] + [0.0] * 1023}
                                                      for i in range(n)], "usage": {"cost": 0.0002 * n}})
        filas = [{**_fila("MLM9", None, 1), "rf": 1}, {**_fila("MLM3", None, 3), "rf": 3}]
        with self._con(httpx.MockTransport(manejar)):
            r, g, rangos = self._revisar(filas=filas)
        self.assertEqual(r["color"], "verde")
        self.assertTrue(r["solo_fotos"])
        self.assertIsNone(rangos.call_args.args[1])        # sin huella de texto
        self.assertEqual([p["fuente"] for p in r["propuestas"]], ["fotos"])
        self.assertAlmostEqual(g["costo_usd"], 0.0004)     # se cuenta lo que sí se pagó

    def test_sin_clave_no_llama(self):
        with self._con(_openrouter()):
            r, g, rangos = self._revisar(openrouter_api_key="")
        self.assertEqual(r["color"], "sin_senal")
        rangos.assert_not_called()

    def test_tope_de_24h(self):
        with _banderas(), \
             mock.patch.object(cv, "huellas_listas", return_value=True), \
             mock.patch.object(cv, "gastado_24h", return_value=0.6), \
             mock.patch.object(cv, "huella") as huella, \
             mock.patch.object(cv, "_guardar", return_value=1):
            r = asyncio.run(cv.revisar("X-1", 5, "MLM3", "t", "d", []))
        self.assertEqual(r["motivo_sin_senal"], "tope de gasto de 24 h")
        huella.assert_not_called()

    def test_categoria_sin_huella(self):
        with self._con(_openrouter()):
            r, _, _ = self._revisar(filas=[_fila("MLM9", 1, 1)])
        self.assertEqual(r["color"], "sin_senal")
        self.assertIn("huella", r["motivo_sin_senal"])
        self.assertEqual([p["category_id"] for p in r["propuestas"]], ["MLM9"])

    def test_guarda_el_origen_y_la_firma_de_la_entrada(self):
        with self._con(_openrouter()):
            _, g, _ = self._revisar()
        self.assertEqual(g["detalle"]["origen"], "vista_previa")
        self.assertEqual(g["detalle"]["entrada"],
                         cv.entrada("MLM3", "Compresor", "<p>A/C</p>", ["https://f/1.jpg", "https://f/2.jpg"]))


class TopeTotal(unittest.TestCase):
    """TIMEOUT_S es el tope TOTAL por señal (httpx solo lo aplica por etapa)."""

    def test_texto_lento_se_corta_y_quedan_las_fotos(self):
        async def embeddings(cli, modelo, entradas):
            if modelo == cv.MODELO_TEXTO:
                await asyncio.sleep(2)
            return [[1.0] + [0.0] * 511 for _ in entradas], 0.0002

        with _banderas(), mock.patch.object(cv, "TIMEOUT_S", 0.05),              mock.patch.object(cv, "_embeddings", side_effect=embeddings):
            h = asyncio.run(cv.huella("Compresor", "A/C", ["https://f/1.jpg"]))
        self.assertIsNone(h["texto"])
        self.assertIsNotNone(h["foto"])
        self.assertTrue(h["errores"][0].startswith("texto: TimeoutError"))


class Reuso(unittest.TestCase):
    """Lo ya revisado (misma categoría, texto y fotos, < 6 h) no se vuelve a pagar."""

    def test_la_firma_cambia_con_lo_que_se_revisa(self):
        f = cv.entrada("MLM3", "Compresor", "A/C", ["https://f/1.jpg", "https://f/2.jpg"])
        self.assertEqual(f, cv.entrada("MLM3", " Compresor ", "<p>A/C</p>", ["https://f/1.jpg", "https://f/2.jpg"]))
        self.assertNotEqual(f, cv.entrada("MLM4", "Compresor", "A/C", ["https://f/1.jpg", "https://f/2.jpg"]))
        self.assertNotEqual(f, cv.entrada("MLM3", "Compresor 12V", "A/C", ["https://f/1.jpg", "https://f/2.jpg"]))
        self.assertNotEqual(f, cv.entrada("MLM3", "Compresor", "A/C", ["https://f/2.jpg", "https://f/1.jpg"]))
        # La 3.ª foto no se manda, así que no cambia la respuesta.
        self.assertEqual(f, cv.entrada("MLM3", "Compresor", "A/C",
                                       ["https://f/1.jpg", "https://f/2.jpg", "https://f/3.jpg"]))

    def test_reusa_sin_llamar_a_openrouter_ni_guardar(self):
        previa = {"id": 7, "color": "rojo", "rango_texto": 40, "rango_fotos": 90, "rango_fusion": 61,
                  "n_fotos": 2, "solo_fotos": False,
                  "propuestas": [{"category_id": "MLM9", "fuente": "texto_y_fotos", "rango": 1}]}
        with _banderas(), \
             mock.patch.object(cv, "revision_igual", return_value=previa) as igual, \
             mock.patch.object(cv, "huella") as huella, \
             mock.patch.object(cv, "_guardar") as guardar:
            r = asyncio.run(cv.revisar("X-1", 5, "MLM3", "Compresor", "A/C", ["https://f/1.jpg"],
                                       origen="estudio"))
        huella.assert_not_called()
        guardar.assert_not_called()
        self.assertEqual(igual.call_args.args[:2], ("X-1", "MLM3"))
        self.assertTrue(r["reusada"])
        self.assertEqual((r["color"], r["rango_fusion"], r["revision_id"]), ("rojo", 61, 7))
        self.assertEqual(r["propuestas"][0]["category_id"], "MLM9")

    def test_sin_sku_no_busca_ni_guarda(self):
        with _banderas(openrouter_api_key=""), \
             mock.patch.object(cv, "revision_igual") as igual, \
             mock.patch.object(cv, "_guardar") as guardar:
            r = asyncio.run(cv.revisar(None, None, "MLM3", "t", "d", []))
        igual.assert_not_called()
        guardar.assert_not_called()
        self.assertEqual(r["color"], "sin_senal")


class RutaEstudio(unittest.TestCase):
    """`POST /api/publicar/categoria-ml/revision`: el semáforo junto al selector del Estudio."""

    def test_revisa_con_origen_estudio(self):
        from routers import publicar as rp
        vec = mock.AsyncMock(return_value={"color": "verde"})
        req = rp.RevisionCategoriaIn(sku="X-1", wc_id=5, categoria_id="MLM3", titulo="Compresor",
                                     descripcion="A/C", fotos=["https://f/1.jpg"])
        with mock.patch.object(cv, "revisar", vec):
            r = asyncio.run(rp.revision_categoria_ml(req))
        self.assertEqual(r, {"vectores": {"color": "verde"}})
        vec.assert_awaited_once_with("X-1", 5, "MLM3", "Compresor", "A/C", ["https://f/1.jpg"],
                                     origen="estudio")

    def test_apagada_contesta_none(self):
        from routers import publicar as rp
        req = rp.RevisionCategoriaIn(sku="X-1", categoria_id="MLM3")
        with _banderas(categoria_vec_enabled=False):
            self.assertEqual(asyncio.run(rp.revision_categoria_ml(req)), {"vectores": None})


class VistaPrevia(unittest.TestCase):
    """`publicar._revision_categoria_ml` suma `vectores` con la descripción y las 2 primeras fotos."""

    def test_pasa_descripcion_y_dos_fotos_y_lo_adjunta(self):
        from services import ml_revision_categoria as rc
        from services import publicar
        r_crear = {"payload": {"category_id": "MLM3", "title": "Compresor",
                               "pictures": [{"source": "https://f/1.jpg"}, {"source": "https://f/2.jpg"},
                                            {"source": "https://f/3.jpg"}]},
                   "prod": {"description": "<p>A/C</p>", "images": ["https://f/9.jpg"],
                            "meta": {"ml_categoria_id": "MLM3"}}}
        pred = mock.AsyncMock(return_value={"actual": {"category_id": "MLM3"}, "sugeridas": [],
                                            "veredicto": "coincide"})
        vec = mock.AsyncMock(return_value={"color": "rojo"})
        with mock.patch.object(rc, "revisar", pred), mock.patch.object(cv, "revisar", vec):
            rev = asyncio.run(publicar._revision_categoria_ml({"sku": "X-1", "wc_id": 5}, r_crear))
        self.assertEqual(rev["vectores"], {"color": "rojo"})
        self.assertEqual(rev["origen"], "panel")
        self.assertEqual(rev["imagen"], "https://f/1.jpg")
        vec.assert_awaited_once_with("X-1", 5, "MLM3", "Compresor", "<p>A/C</p>",
                                     ["https://f/1.jpg", "https://f/2.jpg"])


class CrearMlConCandado(unittest.TestCase):
    """El candado vive en el embudo de toda alta: `publicar_ready.crear_ml`."""

    def _crear(self, color, motivo):
        from services import publicar_ready as pr
        prod = {"sku": "X-1", "ml_category_id": "MLM3", "wc_categories": [], "stock": 5,
                "title": "t", "price": 10.0}
        llamadas = []

        def publicar(p, token, dry, cuenta):
            llamadas.append(cuenta)
            return {"success": False, "error": "prueba"}

        with _banderas(categoria_vec_candado=True), \
             mock.patch.object(cv, "ultima_revision", return_value={"id": 3, "color": color}), \
             mock.patch.object(cv, "_anotar_decision"), \
             mock.patch.object(pr, "configurar"), \
             mock.patch.object(pr, "_candado_alta_ml", mock.AsyncMock(return_value=None)), \
             mock.patch.object(pr, "construir_prod", return_value=prod), \
             mock.patch.object(pr, "_stock_real", return_value=5), \
             mock.patch.object(pr.meli, "_access_token", return_value="tok"), \
             mock.patch.object(pr.publisher_core, "publish_product", side_effect=publicar), \
             mock.patch.object(pr, "MAX_INTENTOS_ML", 1):
            r = asyncio.run(pr.crear_ml("X-1", 1, {}, ["BEKURA"], categoria_confirmada="MLM3",
                                        categoria_motivo=motivo))
        return r, llamadas

    def test_rojo_sin_motivo_no_llega_al_pipeline(self):
        r, llamadas = self._crear("rojo", None)
        self.assertFalse(r["ok"])
        self.assertIn("ROJO", r["motivo"])
        self.assertEqual(llamadas, [])

    def test_rojo_con_motivo_si_llega(self):
        r, llamadas = self._crear("rojo", "Lo revisé: es herramienta de taller")
        self.assertEqual(llamadas, ["BEKURA"])

    def test_verde_llega(self):
        r, llamadas = self._crear("verde", None)
        self.assertEqual(llamadas, ["BEKURA"])


if __name__ == "__main__":
    unittest.main()
