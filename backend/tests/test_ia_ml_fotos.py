"""Pruebas de «Mejorar con IA» de Mercado Libre con UNA foto, el título del packing list y las palabras de ML
(v0.706.0, `ia_generadores._ml_con_foto` y `_mercado_ml`).

── QUÉ FIJAN ────────────────────────────────────────────
30-oct-2026, pedido de Brandon: «que sólo se mande 1 foto con el título que tengamos de packing list o que ya
tengamos» y que el título se parafrasee con las palabras de los más vendidos de ML. Antes solo leía texto.

  1. Va UNA foto a OpenRouter: la principal de la tienda; si no baja, la siguiente; sin tienda, la del packing list.
  2. El mensaje lleva el nombre actual, el título del packing list, los términos más buscados y los títulos de los
     más vendidos de la categoría; el prompt prohíbe inventar.
  3. Las palabras salen de lo que guardó Competencia; lo que falte, de ML en vivo.
  4. El servidor recalcula qué términos cubre el título (`cubiertos`) y avisa si pasa de 60.
  5. Lo que se vio llega a `ml_atributos`; sin llave, sin foto o sin crédito sigue el texto, también con las palabras.
  6. `catalogo.foto_pl` / `titulos_pl` leen los archivos REALES del repo.

── NO SE TOCA NADA REAL ─────────────────────────────────
httpx, la BD (`_guardar_ml`, Competencia) y los atributos (`ml_atributos`) van simulados.

    cd backend && python -m unittest tests.test_ia_ml_fotos -v
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import catalogo, ia_generadores as G, sin_garantia  # noqa: E402


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, "PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, code: int, cuerpo: dict | None = None, content: bytes = b""):
        self.status_code = code
        self._cuerpo = cuerpo or {}
        self.content = content
        self.text = json.dumps(self._cuerpo)

    def json(self):
        return self._cuerpo


def _respuesta_ia(data: dict, costo: float = 0.0004) -> _Resp:
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}],
                       "usage": {"cost": costo}})


class _Cli:
    """httpx.AsyncClient falso: `get` sirve fotos (las de `malas` dan 404), `post` contesta en orden."""

    def __init__(self, respuestas: list, malas: tuple = ()):
        self.cola = iter(respuestas)
        self.malas = malas
        self.enviados: list = []
        self.bajadas: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return None

    async def get(self, url, **_k):
        self.bajadas.append(url)
        return _Resp(404) if url in self.malas else _Resp(200, content=_png())

    async def post(self, url, json=None, headers=None, timeout=None):
        self.enviados.append(json)
        return next(self.cola)


PRODUCTO = {"sku": "TEC-0234-PLA", "nombre": "piedra afilador 1000/6000", "ml_cat_id": "MLM189946",
            "categoria": "Cocina › Afiladores", "descripcion": "Piedra para afilar.",
            "imagenes": ["https://chunche.shop/a.jpg", "https://chunche.shop/b.jpg",
                         "https://chunche.shop/c.jpg", "https://chunche.shop/d.jpg"],
            "atributos": []}

MERCADO = {"categoria": "MLM189946", "terminos": ["piedra para afilar", "afilador de cuchillos", "piedra de agua"],
           "lideres": ["Piedra Para Afilar Cuchillos Doble Cara 1000/6000"],
           "fuente": {"terminos": "competencia", "lideres": "en vivo"}}

BUENA = {"titulo": "Piedra Para Afilar Cuchillos 1000/6000 Con Base",
         "descripcion": "Piedra de doble grano.\n- Grano 1000/6000\n- Garantía de 1 año",
         "lo_que_se_ve": "Piedra rectangular azul y blanca sobre base de goma.",
         "coincide": "Sí", "dudas": ["No se ve el material de la base"],
         "atributos": [{"nombre": "Color", "valor": "Azul"}]}


def _mejorar(cli: _Cli, *, llave="x", pl_foto: bytes | None = b"pl", pl_titulos=("Knife Sharpening Stone",),
             mercado=MERCADO, texto=None, producto=None):
    atributos = mock.AsyncMock(return_value={
        "atributos": {"COLOR": "Azul"}, "meli_attrs": {"principales": [{"id": "COLOR", "name": "Color"}],
                                                      "secundarias": []},
        "faltan": [], "rechazados": [], "avisos": []})
    completar = mock.Mock(return_value=texto or {"ok": True, "proveedor": "deepseek",
                                                 "texto": json.dumps({"titulo": "Texto", "descripcion": "d"})})

    def _cat(_sku, que):
        return list(pl_titulos) if que == "titulos_pl" else pl_foto

    with mock.patch.object(G.settings, "openrouter_api_key", llave), \
            mock.patch.object(G.httpx, "AsyncClient", lambda **_k: cli), \
            mock.patch.object(G, "_del_catalogo", _cat), \
            mock.patch.object(G, "_mercado_ml", mock.AsyncMock(return_value=json.loads(json.dumps(mercado)))), \
            mock.patch.object(G, "_completar", completar), \
            mock.patch.object(G, "_guardar_ml", mock.AsyncMock(return_value=None)), \
            mock.patch("services.ml_atributos.generar_atributos", atributos):
        r = asyncio.run(G.mejorar("mercado_libre", dict(producto or PRODUCTO), cuenta="BEKURA"))
    return r, completar, atributos


def _partes(cli: _Cli) -> list:
    return cli.enviados[0]["messages"][1]["content"]


class UnaFoto(unittest.TestCase):
    def test_manda_solo_la_principal(self):
        cli = _Cli([_respuesta_ia(BUENA)])
        r, completar, _ = _mejorar(cli)
        self.assertTrue(r["ok"])
        completar.assert_not_called()
        self.assertEqual(cli.bajadas, ["https://chunche.shop/a.jpg"])
        partes = _partes(cli)
        self.assertEqual([p["type"] for p in partes], ["text", "image_url", "text"])
        self.assertEqual(partes[0]["text"], "Foto del producto: la principal de la tienda.")
        self.assertEqual(cli.enviados[0]["model"], "openai/gpt-6-luna")
        self.assertEqual(cli.enviados[0]["messages"][0]["content"], G._ML_CON_FOTO)

    def test_si_la_principal_no_baja_usa_la_siguiente(self):
        cli = _Cli([_respuesta_ia(BUENA)], malas=("https://chunche.shop/a.jpg",))
        r, _, _ = _mejorar(cli)
        self.assertEqual(cli.bajadas, ["https://chunche.shop/a.jpg", "https://chunche.shop/b.jpg"])
        self.assertTrue(r["vision"]["foto"].startswith("la 2ª de la tienda"))

    def test_sin_fotos_en_la_tienda_va_la_del_packing_list(self):
        cli = _Cli([_respuesta_ia(BUENA)])
        r, completar, _ = _mejorar(cli, producto={**PRODUCTO, "imagenes": []})
        completar.assert_not_called()
        self.assertEqual(r["vision"]["foto"], "la del renglón del packing list (la tienda no tiene foto)")
        self.assertTrue(_partes(cli)[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))


class ElMensaje(unittest.TestCase):
    def test_lleva_titulos_de_referencia_y_palabras_de_ml(self):
        cli = _Cli([_respuesta_ia(BUENA)])
        _mejorar(cli)
        texto = _partes(cli)[-1]["text"]
        self.assertIn("Nombre actual: piedra afilador 1000/6000", texto)
        self.assertIn("Título del packing list (del proveedor): Knife Sharpening Stone", texto)
        self.assertIn("términos más buscados de la categoría, de más a menos:\n1. piedra para afilar", texto)
        self.assertIn("títulos de los más vendidos de la categoría:\n#1 Piedra Para Afilar Cuchillos", texto)

    def test_el_prompt_prohibe_inventar_y_pide_parafrasear(self):
        self.assertIn("NO INVENTES", G._ML_CON_FOTO)
        self.assertIn("parafrasea el título", G._ML_CON_FOTO)
        self.assertIn(sin_garantia.REGLA, G._ML_CON_FOTO)
        self.assertIn("JSON", G._ML_CON_FOTO)   # el modo JSON de Luna lo exige en el mensaje

    def test_sin_palabras_de_ml_el_mensaje_no_las_inventa(self):
        cli = _Cli([_respuesta_ia(BUENA)])
        _mejorar(cli, mercado={"categoria": "X", "terminos": [], "lideres": [],
                               "fuente": {"terminos": "sin datos", "lideres": "sin datos"}}, pl_titulos=())
        texto = _partes(cli)[-1]["text"]
        self.assertNotIn("PALABRAS DE MERCADO LIBRE", texto)
        self.assertNotIn("packing list", texto)


class LaRespuesta(unittest.TestCase):
    def test_parte_campos_y_terminos_cubiertos(self):
        r, _, _ = _mejorar(_Cli([_respuesta_ia(BUENA)]))
        v = r["vision"]
        self.assertEqual(r["proveedor"], "openrouter")
        self.assertEqual(v["coincide"], "si")
        self.assertEqual(v["dudas"], ["No se ve el material de la base"])
        self.assertEqual(v["costo_usd"], 0.0004)
        self.assertEqual(r["titulos_pl"], ["Knife Sharpening Stone"])
        # «piedra para afilar» SÍ está en el título; «piedra de agua» no.
        self.assertEqual(r["mercado"]["cubiertos"], ["piedra para afilar"])
        self.assertEqual(set(r["campos"]) - {"atributos"}, {"titulo", "descripcion"})
        self.assertNotIn("Garantía", r["campos"]["descripcion"])
        self.assertEqual(r["campos"]["atributos"], [{"nombre": "Color", "campo": "COLOR", "valor": "Azul"}])

    def test_lo_que_se_vio_y_el_packing_list_llegan_a_los_atributos(self):
        _, _, atributos = _mejorar(_Cli([_respuesta_ia(BUENA)]))
        kwargs = atributos.call_args.kwargs
        self.assertTrue(kwargs["caracteristicas_clave"].startswith(
            "Lo que se ve en la foto: Piedra rectangular azul"))
        self.assertIn("Título del packing list: Knife Sharpening Stone", kwargs["caracteristicas_clave"])
        self.assertEqual(kwargs["nombre"], BUENA["titulo"])

    def test_titulo_largo_queda_en_dudas(self):
        r, _, _ = _mejorar(_Cli([_respuesta_ia({**BUENA, "titulo": "Piedra " * 12, "dudas": []})]))
        self.assertTrue(any("Mercado Libre admite 60" in d for d in r["vision"]["dudas"]))

    def test_primer_modelo_sin_titulo_pregunta_al_segundo(self):
        cli = _Cli([_respuesta_ia({"descripcion": "x"}), _respuesta_ia(BUENA)])
        r, _, _ = _mejorar(cli)
        self.assertEqual([b["model"] for b in cli.enviados], ["openai/gpt-6-luna", "google/gemini-2.5-flash"])
        self.assertEqual(r["vision"]["modelo"], "google/gemini-2.5-flash")

    def test_coincide_normaliza(self):
        self.assertEqual(G._coincide("Sí"), "si")
        self.assertEqual(G._coincide(" PARCIAL "), "parcial")
        self.assertEqual(G._coincide("quizá"), "")


class SinFoto(unittest.TestCase):
    def test_sin_llave_lee_el_texto_con_las_palabras(self):
        cli = _Cli([])
        r, completar, _ = _mejorar(cli, llave="")
        completar.assert_called_once()
        system, user, _ = completar.call_args.args
        self.assertIn(G._PALABRAS_ML, system)
        self.assertIn("1. piedra para afilar", user)
        self.assertIn("Knife Sharpening Stone", user)
        self.assertEqual(cli.bajadas, [])
        self.assertIn("OPENROUTER_API_KEY", r["vision"]["motivo"])
        self.assertEqual(r["campos"]["titulo"], "Texto")

    def test_sin_credito_no_reintenta_y_lee_el_texto(self):
        cli = _Cli([_Resp(402, {"error": "sin crédito"})])
        r, completar, _ = _mejorar(cli)
        self.assertEqual(len(cli.enviados), 1)
        completar.assert_called_once()
        self.assertIn("sin crédito", r["vision"]["motivo"])

    def test_sin_ninguna_foto(self):
        cli = _Cli([], malas=tuple(PRODUCTO["imagenes"]))
        r, completar, _ = _mejorar(cli, pl_foto=None)
        completar.assert_called_once()
        self.assertEqual(cli.enviados, [])
        self.assertIn("no tiene foto", r["vision"]["motivo"])

    def test_si_falla_tambien_el_texto_el_parte_viaja(self):
        r, _, _ = _mejorar(_Cli([]), llave="", texto={"ok": False, "motivo": "IA no disponible"})
        self.assertFalse(r["ok"])
        self.assertIn("vision", r)
        self.assertIn("mercado", r)

    def test_otros_canales_no_cambian(self):
        cli = _Cli([])
        completar = mock.Mock(return_value={"ok": True, "texto": json.dumps({"titulo": "T", "descripcion": "D"})})
        with mock.patch.object(G.settings, "openrouter_api_key", "x"), \
                mock.patch.object(G.httpx, "AsyncClient", lambda **_k: cli), \
                mock.patch.object(G, "_completar", completar):
            r = asyncio.run(G.mejorar("general", dict(PRODUCTO)))
        self.assertEqual(cli.bajadas, [])
        self.assertNotIn("vision", r)
        self.assertNotIn(G._PALABRAS_ML, completar.call_args.args[0])


class MarcasYModelos(unittest.TestCase):
    """Regla de Brandon (30-oct-2026): ni marcas ni modelos en la publicación."""

    def test_el_prompt_lo_prohibe_en_los_dos_caminos(self):
        self.assertIn(G._SIN_MARCAS_NI_MODELOS, G._ML_CON_FOTO)
        self.assertIn("compatible con X", G._SIN_MARCAS_NI_MODELOS)
        _, completar, _ = _mejorar(_Cli([]), llave="")
        self.assertIn(G._SIN_MARCAS_NI_MODELOS, completar.call_args.args[0])

    def test_una_marca_pide_una_correccion(self):
        con_marca = {**BUENA, "titulo": "Tenis Para Correr Tipo Adidas Negro"}
        limpio = {**BUENA, "titulo": "Tenis Para Correr Negro Ligeros"}
        cli = _Cli([_respuesta_ia(con_marca, 0.0004), _respuesta_ia(limpio, 0.0005)])
        r, _, _ = _mejorar(cli)
        self.assertEqual(len(cli.enviados), 2)
        correccion = cli.enviados[1]["messages"][-1]["content"]
        self.assertIn("Adidas", correccion)
        self.assertEqual(cli.enviados[1]["messages"][-2]["role"], "assistant")
        self.assertEqual(r["campos"]["titulo"], limpio["titulo"])
        self.assertEqual(r["vision"]["marcas_corregidas"], ["Adidas"])
        self.assertEqual(r["vision"]["costo_usd"], 0.0009)
        self.assertEqual(r["prohibidos"], {"marcas": [], "modelos": []})

    def test_si_la_correccion_no_la_quita_se_reporta(self):
        con_marca = {**BUENA, "titulo": "Tenis Para Correr Tipo Adidas Negro"}
        r, _, _ = _mejorar(_Cli([_respuesta_ia(con_marca), _respuesta_ia(con_marca)]))
        self.assertEqual(r["prohibidos"]["marcas"], ["Adidas"])

    def test_el_modelo_del_packing_list_se_reporta(self):
        con_modelo = {**BUENA, "titulo": "Consola Mezcladora UX-06 De 48W"}
        r, _, _ = _mejorar(_Cli([_respuesta_ia(con_modelo)]), pl_titulos=("UX-06 MIXER",))
        self.assertEqual(r["prohibidos"]["modelos"], ["UX-06"])

    def test_medidas_y_marcas_propias_no_cuentan(self):
        self.assertEqual(G._modelos_en("Foco 60W 24x14x40cm 1080p 4K", "Foco 60W 24x14x40cm 1080p 4K"), [])
        self.assertEqual(G._marcas_en("Organizador Ferrahome De Escritorio"), [])
        # Sin la excepción de compatibilidad de Amazon: en ML tampoco va.
        self.assertEqual(G._marcas_en("Funda compatible con iPhone 15"), ["iPhone"])


class LoQueSalioEnLaPruebaEnVivo(unittest.TestCase):
    """30-oct-2026, tres productos reales: la descripción hablaba de sus fuentes, «lg» contaba como cubierto por
    «pulgadas», «audifonos» no contaba en «Audífonos» y 11 de 15 términos de Audífonos eran marcas."""

    def test_la_descripcion_que_habla_de_sus_fuentes_se_corrige(self):
        con_fuentes = {**BUENA, "descripcion": "La imagen muestra una piedra. Revisa las medidas antes de comprar."}
        cli = _Cli([_respuesta_ia(con_fuentes), _respuesta_ia(BUENA)])
        r, _, _ = _mejorar(cli)
        self.assertEqual(len(cli.enviados), 2)
        self.assertIn("La imagen", cli.enviados[1]["messages"][-1]["content"])
        self.assertNotIn("imagen", r["campos"]["descripcion"])

    def test_si_sigue_hablando_de_sus_fuentes_queda_en_dudas(self):
        con_fuentes = {**BUENA, "descripcion": "Según los datos es de doble grano.", "dudas": []}
        r, _, _ = _mejorar(_Cli([_respuesta_ia(con_fuentes), _respuesta_ia(con_fuentes)]))
        self.assertTrue(any("habla de la foto o de los datos" in d for d in r["vision"]["dudas"]))

    def test_cobertura_por_palabras_completas_y_sin_acentos(self):
        self.assertFalse(G._cubre("lg", "Pantalla portátil 12 pulgadas LED"))
        self.assertTrue(G._cubre("audifonos inalambricos", "Audífonos inalámbricos Bluetooth con estuche"))
        self.assertTrue(G._cubre("tv", "Pantalla LED con TV digital"))

    def test_los_terminos_con_marca_no_se_usan(self):
        with mock.patch("services.competencia_store.terminos_categoria",
                        return_value=[{"termino": "tenis adidas"}, {"termino": "tenis para correr"}]), \
                mock.patch("services.competencia_store.ranking_categoria", return_value=[{"posicion": 1, "titulo": "A"}]):
            m = asyncio.run(G._mercado_ml("MLM1"))
        self.assertEqual(m["terminos"], ["tenis para correr"])
        self.assertEqual(m["con_marca"], ["tenis adidas"])


class PalabrasDeML(unittest.TestCase):
    def _correr(self, guardados, ranking, vivo_terminos=None, vivo_lideres=()):
        with mock.patch("services.competencia_store.terminos_categoria", return_value=guardados), \
                mock.patch("services.competencia_store.ranking_categoria", return_value=ranking), \
                mock.patch("services.competencia_ml.tendencias", return_value=vivo_terminos) as trends, \
                mock.patch.object(G, "_lideres_en_vivo", mock.AsyncMock(return_value=list(vivo_lideres))) as lid:
            m = asyncio.run(G._mercado_ml("MLM1"))
        return m, trends, lid

    def test_primero_lo_que_guardo_competencia(self):
        m, trends, lid = self._correr([{"posicion": 1, "termino": "funda moto"}],
                                      [{"posicion": 2, "titulo": "B"}, {"posicion": 1, "titulo": "A"}])
        self.assertEqual(m["terminos"], ["funda moto"])
        self.assertEqual(m["lideres"], ["A", "B"])
        self.assertEqual(m["fuente"], {"terminos": "competencia", "lideres": "competencia"})
        trends.assert_not_called()
        lid.assert_not_called()

    def test_lo_que_falta_se_pregunta_en_vivo(self):
        m, _, _ = self._correr([], [], vivo_terminos=[{"keyword": "funda moto"}], vivo_lideres=["Funda X"])
        self.assertEqual(m["terminos"], ["funda moto"])
        self.assertEqual(m["lideres"], ["Funda X"])
        self.assertEqual(m["fuente"], {"terminos": "en vivo", "lideres": "en vivo"})

    def test_si_la_bd_falla_no_truena(self):
        with mock.patch("services.competencia_store.terminos_categoria", side_effect=RuntimeError("sin BD")), \
                mock.patch("services.competencia_ml.tendencias", return_value=None), \
                mock.patch.object(G, "_lideres_en_vivo", mock.AsyncMock(return_value=[])):
            m = asyncio.run(G._mercado_ml("MLM1"))
        self.assertEqual(m["fuente"], {"terminos": "sin datos", "lideres": "sin datos"})

    def test_sin_categoria_no_pregunta(self):
        self.assertIsNone(asyncio.run(G._mercado_ml("")))


class ElCatalogoDelRepo(unittest.TestCase):
    """Contra los archivos REALES del repo (`backend/data/catalogo`)."""

    def setUp(self):
        catalogo.olvidar()

    def test_foto_del_renglon(self):
        from PIL import Image
        jpg = catalogo.foto_pl("tec-0234-pla")
        self.assertIsNotNone(jpg)
        self.assertEqual(Image.open(io.BytesIO(jpg)).size, (192, 192))

    def test_titulo_del_renglon(self):
        self.assertEqual(catalogo.titulos_pl("TEC-1277-NEG-48W"), ["UX-06 MIXER"])
        self.assertGreater(len(catalogo._indice_pl()["titulos"]), 10_000)

    def test_sin_dato(self):
        self.assertIsNone(catalogo.foto_pl("NO-EXISTE-0000"))
        self.assertEqual(catalogo.titulos_pl(""), [])


if __name__ == "__main__":
    unittest.main()
