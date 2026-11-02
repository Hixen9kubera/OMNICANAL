"""Pruebas de los candados que Crear hacía SIN DECIRLO (Publicador sin draft, 2-nov-2026).

Eduardo: los SKUs en draft entran directo al Publicador y la pestaña Crear se apaga.
Crear dejaba en Woo el precio de costos, el peso y las medidas y la categoría curada de
kubera; un draft trae lo que dejó Odoo. Lo que estas pruebas fijan:

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
a. **Precio contra costos** (AVISO, no candado): la vista previa de un alta avisa
   «El precio $X no coincide con el de costos $Y» si la diferencia pasa de 2 % o $5
   (lo que sea MÁS). Actualizar una publicación viva no compara nada.
b. **Amazon**: `_confirmar_amazon` no manda el PUT si el precio final es $0 o si
   ninguna fuente da un productType. La regla 2 se respeta: panel e histórico
   mandan sin detectar; la detección por título cuenta; el respaldo `HOME` no.
   «Amazon no contestó» (429, 5xx, timeout) no es «no propuso ninguno»: su motivo
   manda a reintentar, no a fijar un tipo a mano.
c. **Peso y medidas reales en ML**: sin ellos el vendor inventa 1000 g y 30×20×15 cm.
   El alta se frena en el embudo (`publicar_ready._candado_alta_ml`) antes del
   pipeline, y la vista previa contesta que NO se publica (`ok: False` con el motivo:
   sin «se CREARÁ», sin tarjeta de categoría y sin botón de confirmar). Al re-crear
   donde la borraron, el aviso de esa cuenta se reemplaza. El padre sigue ganando.
d. **Categoría curada**: la vista previa la ofrece como candidata con su ruta; no la
   aplica (lo que se revisa y se confirma sigue siendo la del panel).

Sin red ni base: todo lo que saldría se sustituye por dobles.

    cd backend && python -m unittest tests.test_publicar_candados -v
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

import httpx

from services import categoria_vectores as cv
from services import ml_revision_categoria as rc
from services import publicar
from services import publicar_ready as pr

MEDIDAS = {"weight": "1.2", "length": "30", "width": "20", "height": "10"}


def _prod(**cambios):
    base = {"sku": "X-1", "ml_category_id": "MLM3", "wc_categories": [], "stock": 5,
            "title": "Lámpara de mesa", "price": 500.0, "ml_attrs": {}, "wc_attrs": {},
            "meta": {"ml_categoria_id": "MLM3"}, **MEDIDAS}
    base.update(cambios)
    return base


def _attrs_amazon(precio):
    """La forma de `attribute_mapper.build_payload_attributes` (solo lo de precio)."""
    return {
        "item_name": [{"value": "Lámpara", "marketplace_id": "MX"}],
        "list_price": [{"value_with_tax": precio, "currency": "MXN", "marketplace_id": "MX"}],
        "purchasable_offer": [{"currency": "MXN", "marketplace_id": "MX",
                               "our_price": [{"schedule": [{"value_with_tax": precio}]}]}],
    }


_ClienteReal = httpx.AsyncClient


def _cliente_falso(handler):
    """`httpx.AsyncClient` con un transporte falso (nada sale a la red)."""
    def fabrica(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs["base_url"] = "https://sp.prueba"
        return _ClienteReal(*args, **kwargs)
    return fabrica


# ═══════════════════════ a. PRECIO CONTRA COSTOS (AVISO) ═══════════════════════
class AvisoPrecio(unittest.TestCase):
    def test_dentro_del_2_por_ciento_no_avisa(self):
        self.assertIsNone(publicar.aviso_precio_costos(1015, 1000))
        self.assertIsNone(publicar.aviso_precio_costos(980, 1000))

    def test_en_lo_barato_manda_la_tolerancia_de_5_pesos(self):
        # 2 % de $100 son $2; la diferencia de $4 no llega a $5.
        self.assertIsNone(publicar.aviso_precio_costos(104, 100))
        self.assertIsNotNone(publicar.aviso_precio_costos(106, 100))

    def test_fuera_de_tolerancia_avisa_con_los_dos_precios(self):
        a = publicar.aviso_precio_costos(1100, 1000)
        self.assertIn("El precio regular $1,100.00 no coincide con el de Costos $1,000.00", a)
        self.assertIn("+10.0%", a)
        self.assertIsNotNone(publicar.aviso_precio_costos(1021, 1000))   # 21 > 20

    def test_no_nombra_a_crear_ni_da_la_causa_por_hecha(self):
        # Crear ya no está en la barra, y el precio pudo cambiar en Costos después.
        a = publicar.aviso_precio_costos(1100, 1000)
        self.assertNotIn("Crear", a)
        self.assertIn("Costos del Estudio", a)

    def test_sin_costos_o_sin_precio_no_avisa(self):
        self.assertIsNone(publicar.aviso_precio_costos(1100, None))
        self.assertIsNone(publicar.aviso_precio_costos(0, 1000))
        self.assertIsNone(publicar.aviso_precio_costos("abc", 1000))

    def test_el_lector_es_costing_read_y_lee_precio_base(self):
        from services import costing_read
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "disponible", return_value=True), \
             mock.patch.object(costing_read, "finales",
                               return_value={"precio_base": "899.5", "precio_sugerido": 700}) as fin:
            self.assertEqual(publicar._precio_de_costos("X-1"), 899.5)
        fin.assert_called_once_with("X-1")

    def test_sin_kubera_o_si_falla_no_rompe(self):
        from services import costing_read
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "disponible", return_value=False), \
             mock.patch.object(costing_read, "finales") as fin:
            self.assertIsNone(publicar._precio_de_costos("X-1"))
        fin.assert_not_called()
        with mock.patch.object(sdb, "disponible", return_value=True), \
             mock.patch.object(costing_read, "finales", side_effect=RuntimeError("caída")):
            self.assertIsNone(publicar._precio_de_costos("X-1"))


class PrecioEnLaVistaPreviaDeML(unittest.TestCase):
    def test_el_alta_avisa_si_el_precio_no_es_el_de_costos(self):
        r_crear = {"ok": True, "payload": {"price": 1500.0, "pictures": []},
                   "prod": _prod(price=1500.0), "avisos": []}
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=[]), \
             mock.patch.object(publicar, "_payload_crear_ml", mock.AsyncMock(return_value=r_crear)), \
             mock.patch.object(publicar, "_revision_categoria_ml",
                               mock.AsyncMock(return_value={"actual": None})), \
             mock.patch.object(publicar, "_precio_de_costos", return_value=1000.0):
            r = asyncio.run(publicar._preview_ml({"sku": "X-1", "wc_id": 5, "campos": {}}))
        self.assertEqual(r["modo"], "crear")
        self.assertTrue(any("El precio regular $1,500.00 no coincide con el de Costos $1,000.00" in a
                            for a in r["avisos"]), r["avisos"])

    def test_actualizar_una_viva_no_compara_precios(self):
        pubs = [{"cuenta": "BEKURA", "item_id": "MLM1"}, {"cuenta": "SANCORFASHION", "item_id": "MLM2"}]
        vivos = {"BEKURA": {"vivo": True}, "SANCORFASHION": {"vivo": True}}
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=pubs), \
             mock.patch.object(publicar, "_estados_items_ml", mock.AsyncMock(return_value=vivos)), \
             mock.patch.object(publicar, "_precio_de_costos") as costos, \
             mock.patch.object(publicar, "_prod_alta", mock.AsyncMock()) as prod:
            r = asyncio.run(publicar._preview_ml({"sku": "X-1", "wc_id": 5,
                                                  "campos": {"titulo": "Lámpara de mesa"}}))
        self.assertEqual(r["modo"], "actualizar")
        self.assertIsNone(r["categoria"])
        costos.assert_not_called()
        prod.assert_not_awaited()


# ════════════════════════════ b. AMAZON ═════════════════════════════════════════
class AmazonCandados(unittest.TestCase):
    REQ = {"sku": "X-1", "wc_id": 5, "campos": {"titulo": "Lámpara de mesa"}}

    def _confirmar(self, pt_resuelto, propuestos=(), precio=350.0):
        """`propuestos`: lo que contesta Amazon a la búsqueda por título — una lista
        (vacía = no propuso ninguno) o None (no contestó)."""
        enviados = []

        def handler(request):
            enviados.append(json.loads(request.content or b"{}"))
            return httpx.Response(200, json={"status": "ACCEPTED", "issues": []})

        detectar = mock.AsyncMock(
            return_value=None if propuestos is None else list(propuestos))
        schema = mock.AsyncMock(return_value=None)
        with mock.patch("services.amazon._access_token", mock.AsyncMock(return_value="tok")), \
             mock.patch.object(publicar, "_pt_resuelto", return_value=pt_resuelto), \
             mock.patch.object(publicar, "_proponer_product_types", detectar), \
             mock.patch.object(publicar, "_detectar_product_type",
                               mock.AsyncMock(side_effect=AssertionError("publicar no usa el respaldo HOME"))), \
             mock.patch.object(publicar, "_amazon_schema", schema), \
             mock.patch.object(publicar, "_amazon_attrs_final",
                               mock.AsyncMock(return_value=_attrs_amazon(precio))), \
             mock.patch.object(publicar, "_guardar_backlog_amazon") as backlog, \
             mock.patch.object(publicar.httpx, "AsyncClient", _cliente_falso(handler)):
            r = asyncio.run(publicar._confirmar_amazon(dict(self.REQ)))
        return r, enviados, detectar, schema, backlog

    def test_sin_tipo_de_ninguna_fuente_no_se_publica(self):
        r, enviados, detectar, schema, backlog = self._confirmar((None, "auto"), propuestos=[])
        self.assertFalse(r["ok"])
        self.assertEqual(r["motivo"], publicar.MSG_AMZ_SIN_TIPO)
        self.assertEqual(enviados, [])
        schema.assert_not_awaited()
        backlog.assert_not_called()
        detectar.assert_awaited_once()

    def test_si_amazon_no_contesta_el_motivo_es_reintentar(self):
        # 429 / 5xx / timeout no es «no hay tipo»: no se manda a fijarlo a mano
        # (lo del panel gana para siempre, regla 2).
        r, enviados, _d, schema, backlog = self._confirmar((None, "auto"), propuestos=None)
        self.assertFalse(r["ok"])
        self.assertEqual(r["motivo"], publicar.MSG_AMZ_SIN_RESPUESTA)
        self.assertIn("Reintenta", r["motivo"])
        self.assertNotIn("Elígelo", r["motivo"])
        self.assertEqual(enviados, [])
        schema.assert_not_awaited()
        backlog.assert_not_called()

    def test_los_mensajes_citan_el_rotulo_real_del_control(self):
        # `TipoAmazonPicker.tsx` se rotula «Tipo de producto (Amazon)».
        for m in (publicar.MSG_AMZ_SIN_TIPO, publicar.MSG_AMZ_TIPO_POR_DETECTAR):
            self.assertIn("«Tipo de producto (Amazon)»", m)
            self.assertNotIn("productType", m)
            self.assertNotIn("histórico", m)

    def test_el_panel_manda_y_no_se_detecta(self):
        r, enviados, detectar, _s, _b = self._confirmar(("LAMP", "panel"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["product_type"], "LAMP")
        self.assertEqual(enviados[0]["productType"], "LAMP")
        detectar.assert_not_awaited()

    def test_el_historico_tambien_cuenta(self):
        r, enviados, detectar, _s, _b = self._confirmar(("LAMP", "historial"))
        self.assertTrue(r["ok"])
        detectar.assert_not_awaited()

    def test_una_deteccion_real_cuenta_como_fuente(self):
        r, enviados, detectar, _s, _b = self._confirmar((None, "auto"), propuestos=["LAMP", "HOME"])
        self.assertTrue(r["ok"])
        self.assertEqual(enviados[0]["productType"], "LAMP")

    def test_precio_final_en_cero_no_se_publica(self):
        r, enviados, _d, _s, backlog = self._confirmar(("LAMP", "panel"), precio=0.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["motivo"], publicar.MSG_AMZ_SIN_PRECIO)
        self.assertEqual(enviados, [])
        backlog.assert_not_called()

    def test_el_mapeo_de_respaldo_ya_no_inventa_un_peso(self):
        attrs = publicar._amazon_attributes("X-1", {"titulo": "Lámpara"}, "MX")
        self.assertEqual(publicar._precio_amazon(attrs), 0.0)
        attrs = publicar._amazon_attributes("X-1", {"titulo": "Lámpara", "precio_regular": 250}, "MX")
        self.assertEqual(publicar._precio_amazon(attrs), 250.0)

    def test_precio_amazon_lee_la_oferta_y_si_no_la_de_lista(self):
        self.assertEqual(publicar._precio_amazon(_attrs_amazon(410.5)), 410.5)
        solo_lista = {"list_price": [{"value_with_tax": 99.0}]}
        self.assertEqual(publicar._precio_amazon(solo_lista), 99.0)
        self.assertEqual(publicar._precio_amazon({}), 0.0)
        self.assertEqual(publicar._precio_amazon({"purchasable_offer": "basura"}), 0.0)


class DetectorDeTipo(unittest.TestCase):
    def _detectar(self, tipos, **kw):
        def handler(request):
            return httpx.Response(200, json={"productTypes": tipos})
        with mock.patch.object(publicar.httpx, "AsyncClient", _cliente_falso(handler)):
            return asyncio.run(publicar._detectar_product_type("tok", "Lámpara de mesa", "MX", **kw))

    def test_sin_propuestas_el_generador_sigue_con_home(self):
        # `amazon_ia._detectar` no pasa `respaldo`: su comportamiento no cambia.
        self.assertEqual(self._detectar([]), "HOME")

    def test_sin_propuestas_al_publicar_no_hay_tipo(self):
        self.assertIsNone(self._detectar([], respaldo=None))

    def test_con_propuesta_da_la_misma_con_o_sin_respaldo(self):
        self.assertEqual(self._detectar([{"name": "LAMP"}]), "LAMP")
        self.assertEqual(self._detectar([{"name": "LAMP"}], respaldo=None), "LAMP")

    def _proponer(self, handler):
        with mock.patch.object(publicar.httpx, "AsyncClient", _cliente_falso(handler)):
            return asyncio.run(publicar._proponer_product_types("tok", "Lámpara de mesa", "MX"))

    def test_proponer_distingue_ninguno_de_no_contesto(self):
        self.assertEqual(self._proponer(
            lambda r: httpx.Response(200, json={"productTypes": []})), [])
        self.assertEqual(self._proponer(
            lambda r: httpx.Response(200, json={"productTypes": [{"name": "LAMP"}]})), ["LAMP"])
        for status in (429, 500, 401):
            self.assertIsNone(self._proponer(lambda r, s=status: httpx.Response(s)), status)

        def caida(request):
            raise httpx.ConnectTimeout("lento", request=request)
        self.assertIsNone(self._proponer(caida))

    def test_si_amazon_no_contesta_el_generador_sigue_con_home(self):
        def handler(request):
            return httpx.Response(429)
        with mock.patch.object(publicar.httpx, "AsyncClient", _cliente_falso(handler)):
            self.assertEqual(asyncio.run(publicar._detectar_product_type("tok", "x", "MX")), "HOME")


class AmazonVistaPrevia(unittest.TestCase):
    def _preview(self, pt, precio, costos=None):
        with mock.patch.object(publicar, "_pt_resuelto", return_value=pt), \
             mock.patch.object(publicar, "_amazon_attrs_final",
                               mock.AsyncMock(return_value=_attrs_amazon(precio))), \
             mock.patch.object(publicar, "_precio_de_costos", return_value=costos):
            return asyncio.run(publicar._preview_amazon(
                {"sku": "X-1", "wc_id": 5, "campos": {"titulo": "Lámpara de mesa"}}))

    def test_avisa_sin_tipo_y_sin_precio_antes_de_confirmar(self):
        r = self._preview((None, "auto"), 0.0)
        self.assertIn(publicar.MSG_AMZ_TIPO_POR_DETECTAR, r["avisos"])
        self.assertIn(publicar.MSG_AMZ_SIN_PRECIO, r["avisos"])

    def test_compara_el_precio_del_payload_con_costos(self):
        r = self._preview(("LAMP", "panel"), 700.0, costos=500.0)
        self.assertNotIn(publicar.MSG_AMZ_TIPO_POR_DETECTAR, r["avisos"])
        self.assertTrue(any("El precio regular $700.00 no coincide con el de Costos $500.00" in a
                            for a in r["avisos"]), r["avisos"])


# ═══════════════════════ c. PESO Y MEDIDAS REALES EN ML ═══════════════════════
class MedidasML(unittest.TestCase):
    def test_completas_y_creibles_pasan(self):
        self.assertIsNone(pr.faltan_medidas_ml(_prod()))

    def test_sin_peso_lo_nombra(self):
        m = pr.faltan_medidas_ml(_prod(weight=""))
        self.assertIn("peso (kg)", m)
        self.assertNotIn("largo", m)
        self.assertIn("1000 g y 30×20×15 cm", m)

    def test_los_mensajes_empiezan_por_la_consecuencia(self):
        # Lo primero que lee el KAM es que NO se publica; sin jerga («pipeline»).
        for m in (pr.faltan_medidas_ml(_prod(weight="")),
                  pr.faltan_medidas_ml(_prod(weight="500", length="10", width="10", height="10"))):
            self.assertTrue(m.startswith("No se da de alta en Mercado Libre"), m)
            self.assertNotIn("pipeline", m)
            self.assertIn("Costos del Estudio", m)

    def test_cero_o_ausente_cuenta_como_falta(self):
        prod = _prod(length="0")
        prod.pop("height")
        m = pr.faltan_medidas_ml(prod)
        self.assertIn("largo (cm)", m)
        self.assertIn("alto (cm)", m)

    def test_un_texto_que_no_es_numero_dice_por_que(self):
        self.assertIn("«1,5» no es un número", pr.faltan_medidas_ml(_prod(weight="1,5")))

    def test_densidad_fuera_de_la_ventana_del_vendor(self):
        # 500 kg en 10×10×10 cm = 500 g/cm³: el vendor omitiría las medidas.
        m = pr.faltan_medidas_ml(_prod(weight="500", length="10", width="10", height="10"))
        self.assertIn("no cuadran", m)
        self.assertIn("500 g/cm³", m)


class MedidasEnElAltaML(unittest.TestCase):
    """El candado vive en el embudo de toda alta: `publicar_ready.crear_ml`."""

    def _crear(self, prod, hijas=0):
        llamadas = []

        def publicar_(p, token, dry, cuenta):
            llamadas.append(cuenta)
            return {"success": False, "error": "prueba"}

        with mock.patch.object(pr, "configurar"), \
             mock.patch.object(pr, "construir_prod", return_value=prod), \
             mock.patch.object(pr.wp_db, "hijas_vivas", return_value=hijas), \
             mock.patch.object(pr.categoria_vectores, "candado_alta", mock.AsyncMock(return_value=None)), \
             mock.patch.object(pr, "_atributos_ml_para_alta", side_effect=lambda p, c, cu: (p, [])), \
             mock.patch.object(pr.meli, "_access_token", return_value="tok"), \
             mock.patch.object(pr.publisher_core, "publish_product", side_effect=publicar_), \
             mock.patch.object(pr, "MAX_INTENTOS_ML", 1):
            r = asyncio.run(pr.crear_ml("X-1", 1, {}, ["BEKURA", "SANCORFASHION"],
                                        categoria_confirmada="MLM3"))
        return r, llamadas

    def test_sin_medidas_no_llega_al_pipeline(self):
        r, llamadas = self._crear(_prod(weight="", length=""))
        self.assertFalse(r["ok"])
        self.assertIn("peso (kg)", r["motivo"])
        self.assertIn("largo (cm)", r["motivo"])
        self.assertEqual([f["error"] for f in r["resultados"]], [r["motivo"]] * 2)
        self.assertEqual(llamadas, [])

    def test_con_medidas_si_llega(self):
        r, llamadas = self._crear(_prod())
        self.assertEqual(llamadas, ["BEKURA", "SANCORFASHION"])

    def test_el_padre_sigue_ganando(self):
        r, llamadas = self._crear(_prod(weight=""), hijas=3)
        self.assertEqual(r["motivo"], pr.MSG_PADRE)
        self.assertEqual(llamadas, [])

    def test_la_vista_previa_lo_enseña_como_faltante(self):
        with mock.patch.object(pr, "configurar"), \
             mock.patch.object(pr, "construir_prod", return_value=_prod(height="")), \
             mock.patch.object(pr.wp_db, "hijas_vivas", return_value=0), \
             mock.patch.object(pr.publisher_core, "build_payload") as armar:
            r = asyncio.run(pr.preview_crear_ml("X-1", 1, {}, "BEKURA"))
        self.assertFalse(r["ok"])
        self.assertIn("alto (cm)", r["motivo"])
        # `frena`: el alta lo va a rechazar igual (no es «no pude armar la vista previa»).
        self.assertTrue(r["frena"])
        armar.assert_not_called()

    def test_el_stock_en_cero_tambien_frena_la_vista_previa(self):
        with mock.patch.object(pr, "configurar"), \
             mock.patch.object(pr, "construir_prod", return_value=_prod(stock=0)), \
             mock.patch.object(pr.wp_db, "hijas_vivas", return_value=0):
            r = asyncio.run(pr.preview_crear_ml("X-1", 1, {}, "BEKURA"))
        self.assertFalse(r["ok"])
        self.assertTrue(r["frena"])

    def _preview_alta(self, r_crear, prod_alta=None):
        revision = mock.AsyncMock(return_value={"actual": None})
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=[]), \
             mock.patch.object(publicar, "_payload_crear_ml", mock.AsyncMock(return_value=r_crear)), \
             mock.patch.object(publicar, "_prod_alta", mock.AsyncMock(return_value=prod_alta)), \
             mock.patch.object(publicar, "_revision_categoria_ml", revision), \
             mock.patch.object(publicar, "_precio_de_costos", return_value=None):
            r = asyncio.run(publicar._preview_ml({"sku": "X-1", "wc_id": 5,
                                                  "campos": {"titulo": "Lámpara de mesa"}}))
        return r, revision

    def test_un_alta_frenada_dice_que_no_se_publica_y_no_ofrece_confirmar(self):
        # Antes: `ok: True`, «se CREARÁ una nueva publicación» y el motivo como un
        # aviso más, con «Confirmar y publicar» habilitado. El modal del Estudio solo
        # pinta el botón con `ok`; sin él pinta la caja del motivo.
        motivo = pr.faltan_medidas_ml(_prod(weight=""))
        r, revision = self._preview_alta({"ok": False, "motivo": motivo, "frena": True})
        self.assertFalse(r["ok"])
        self.assertEqual(r["motivo"], motivo)
        self.assertNotIn("avisos", r)
        revision.assert_not_awaited()          # sin tarjeta de categoría

    def test_si_no_se_pudo_armar_el_payload_tambien_mide_las_medidas(self):
        r, revision = self._preview_alta(None, prod_alta=_prod(height=""))
        self.assertFalse(r["ok"])
        self.assertIn("alto (cm)", r["motivo"])
        revision.assert_not_awaited()

    def test_un_rechazo_que_no_frena_sigue_siendo_aviso(self):
        # «Faltan datos: categoría ML» no es un candado del alta: la tarjeta de
        # categoría sigue siendo la forma de resolverlo.
        r, revision = self._preview_alta({"ok": False, "motivo": "Faltan datos: categoría ML"},
                                         prod_alta=_prod())
        self.assertTrue(r["ok"])
        self.assertIn("Faltan datos: categoría ML", r["avisos"])
        self.assertTrue(any("se CREARÁ" in a for a in r["avisos"]))
        revision.assert_awaited_once()

    def test_recrear_donde_la_borraron_reemplaza_el_aviso_de_crear(self):
        pubs = [{"cuenta": "BEKURA", "item_id": "MLM1"}, {"cuenta": "SANCORFASHION", "item_id": "MLM2"}]
        estados = {"BEKURA": {"vivo": True}, "SANCORFASHION": {"vivo": False}}
        revision = mock.AsyncMock(return_value={"actual": None})
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=pubs), \
             mock.patch.object(publicar, "_estados_items_ml", mock.AsyncMock(return_value=estados)), \
             mock.patch.object(publicar, "_payload_crear_ml", mock.AsyncMock()) as payload, \
             mock.patch.object(publicar, "_prod_alta", mock.AsyncMock(return_value=_prod(weight=""))), \
             mock.patch.object(publicar, "_revision_categoria_ml", revision), \
             mock.patch.object(publicar, "_precio_de_costos", return_value=None):
            r = asyncio.run(publicar._preview_ml({"sku": "X-1", "wc_id": 5,
                                                  "campos": {"titulo": "Lámpara de mesa"}}))
        payload.assert_not_awaited()
        # Lo vivo se actualiza: la vista previa sigue en pie.
        self.assertTrue(r["ok"])
        self.assertEqual(r["modo"], "actualizar")
        de_sanc = [a for a in r["avisos"] if a.startswith("SANCORFASHION:")]
        self.assertEqual(len(de_sanc), 1, r["avisos"])
        self.assertIn("NO se re-creará", de_sanc[0])
        self.assertIn("peso (kg)", de_sanc[0])
        self.assertFalse(any("se CREARÁ" in a for a in r["avisos"]), r["avisos"])
        # Sin alta no hay categoría que revisar (la tarjeta bloquearía la actualización).
        self.assertIsNone(r["categoria"])
        revision.assert_not_awaited()

    def test_recrear_con_medidas_conserva_el_aviso_y_la_tarjeta(self):
        pubs = [{"cuenta": "BEKURA", "item_id": "MLM1"}, {"cuenta": "SANCORFASHION", "item_id": "MLM2"}]
        estados = {"BEKURA": {"vivo": True}, "SANCORFASHION": {"vivo": False}}
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=pubs), \
             mock.patch.object(publicar, "_estados_items_ml", mock.AsyncMock(return_value=estados)), \
             mock.patch.object(publicar, "_prod_alta", mock.AsyncMock(return_value=_prod())), \
             mock.patch.object(publicar, "_revision_categoria_ml",
                               mock.AsyncMock(return_value={"actual": None})), \
             mock.patch.object(publicar, "_precio_de_costos", return_value=None):
            r = asyncio.run(publicar._preview_ml({"sku": "X-1", "wc_id": 5,
                                                  "campos": {"titulo": "Lámpara de mesa"}}))
        self.assertTrue(any(a.startswith("SANCORFASHION:") and "se CREARÁ" in a
                            for a in r["avisos"]), r["avisos"])
        self.assertEqual(r["categoria"], {"actual": None})


# ═════════════════════════ d. CATEGORÍA CURADA ═════════════════════════════════
class CuradaCandidata(unittest.TestCase):
    DET = {"category_id": "MLM7", "name": "Lámparas", "ruta": "Hogar > Lámparas",
           "niveles": [{"id": "MLM1", "name": "Hogar"}, {"id": "MLM7", "name": "Lámparas"}],
           "publicable": True}

    def test_sin_curada_no_hay_candidata(self):
        self.assertIsNone(asyncio.run(rc.candidata_curada(None)))
        self.assertIsNone(asyncio.run(rc.candidata_curada({"category_id": ""})))

    def test_con_ruta_y_su_fuente_en_palabras(self):
        with mock.patch.object(rc, "_categoria", mock.AsyncMock(return_value=dict(self.DET))):
            c = asyncio.run(rc.candidata_curada(
                {"category_id": "MLM7", "category_name": "Lámparas", "fuente": "panel"}))
        self.assertEqual(c["ruta"], "Hogar > Lámparas")
        self.assertEqual(c["fuente"], "panel")
        self.assertEqual(c["fuente_texto"], rc.FUENTES_CURADA["panel"])

    def test_si_ml_no_contesta_sale_con_el_nombre_de_kubera(self):
        with mock.patch.object(rc, "_categoria", mock.AsyncMock(return_value=None)):
            c = asyncio.run(rc.candidata_curada(
                {"category_id": "MLM7", "category_name": "Lámparas", "fuente": "raro"}))
        self.assertTrue(c["ilegible"])
        self.assertEqual(c["name"], "Lámparas")
        self.assertEqual(c["fuente_texto"], "Curada en kubera")

    def test_el_lector_es_channel_read_y_no_rompe(self):
        from services import channel_read
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "disponible", return_value=False), \
             mock.patch.object(channel_read, "categoria_curada") as leer:
            self.assertIsNone(publicar._categoria_curada_ml("X-1"))
        leer.assert_not_called()
        with mock.patch.object(sdb, "disponible", return_value=True), \
             mock.patch.object(channel_read, "categoria_curada", side_effect=RuntimeError("caída")):
            self.assertIsNone(publicar._categoria_curada_ml("X-1"))
        with mock.patch.object(sdb, "disponible", return_value=True), \
             mock.patch.object(channel_read, "categoria_curada",
                               return_value={"category_id": "MLM7", "fuente": "real"}) as leer:
            self.assertEqual(publicar._categoria_curada_ml("X-1")["category_id"], "MLM7")
        leer.assert_called_once_with("X-1")

    def test_la_revision_la_ofrece_sin_cambiar_la_que_va(self):
        r_crear = {"payload": {"category_id": "MLM3", "title": "Lámpara de mesa", "pictures": []},
                   "prod": _prod()}
        pred = mock.AsyncMock(return_value={"actual": {"category_id": "MLM3"}, "sugeridas": [],
                                            "veredicto": "sin_sugerencia"})
        with mock.patch.object(rc, "revisar", pred), \
             mock.patch.object(cv, "revisar", mock.AsyncMock(return_value=None)), \
             mock.patch.object(publicar, "_categoria_curada_ml",
                               return_value={"category_id": "MLM7", "category_name": "Lámparas",
                                             "fuente": "real"}), \
             mock.patch.object(rc, "_categoria", mock.AsyncMock(return_value=dict(self.DET))):
            rev = asyncio.run(publicar._revision_categoria_ml({"sku": "X-1", "wc_id": 5}, r_crear))
        self.assertEqual(rev["curada"]["category_id"], "MLM7")
        self.assertEqual(rev["curada"]["fuente_texto"], rc.FUENTES_CURADA["real"])
        # Lo que se revisa (y se confirma) sigue siendo la del panel.
        self.assertEqual(rev["actual"]["category_id"], "MLM3")
        self.assertEqual(rev["origen"], "panel")
        pred.assert_awaited_once_with("MLM3", "Lámpara de mesa")
        self.assertIsNotNone(rc.motivo_sin_confirmar("MLM7", "MLM3"))

    def test_sin_curada_la_tarjeta_es_la_de_siempre(self):
        r_crear = {"payload": {"category_id": "MLM3", "title": "t", "pictures": []}, "prod": _prod()}
        with mock.patch.object(rc, "revisar", mock.AsyncMock(return_value={"actual": None, "sugeridas": [],
                                                                           "veredicto": "sin_categoria"})), \
             mock.patch.object(cv, "revisar", mock.AsyncMock(return_value=None)), \
             mock.patch.object(publicar, "_categoria_curada_ml", return_value=None):
            rev = asyncio.run(publicar._revision_categoria_ml({"sku": "X-1", "wc_id": 5}, r_crear))
        self.assertIsNone(rev["curada"])


if __name__ == "__main__":
    unittest.main()
