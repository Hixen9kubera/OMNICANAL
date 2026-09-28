"""Pruebas de la planeación semanal por tienda («Crear FULL»), su IA, su Excel y la ficha del SKU.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. «En camino» y «esta semana» por TIENDA (ML Kubera, ML San Corpe, FBA, WFS):
     abierta reciente → lo pedido; validada sin cerrar → lo que ML no avisa;
     cerrada → nada; olvidada (+21 días) → se enseña y no se resta.
  2. Los insumos no confunden un hueco con un cero, las PRUEBAS no se restan y
     el ganador agotado trae reemplazos YA PUBLICADOS con stock (mismo modelo,
     luego misma categoría), como pide el prompt estándar.
  3. Lo libre se reparte entre tiendas y luego por almacén sin prometer dos veces.
  4. Con el interruptor APAGADO nada se escribe; encendido: borrador, precio 0,
     socio fijo por tienda, «PRUEBA» en modo prueba, y la clave no duplica.
  5. La guía sólo se adjunta a órdenes del PANEL que siguen en borrador.
  5b. La solicitud original va a la bitácora (ops.process_log), una fila por SKU
      y tienda, sin duplicar; si la bitácora falla, la creación sigue.
  6. Lo que sugiera la IA se valida: fuera de la planeación o sobre lo libre no pasa.
  7. La IA como agente (v0.568.0): tabla compacta con precio, instrucciones de la persona,
     seguimiento con historial y la planeación primero, en caché.
  8. Análisis (v0.570.0): el reemplazo es el siguiente que VENDE y tiene stock; el título se
     compara contra Odoo con la foto; cada orden sin completar dice cuánto lleva.
  9. La SEMANA (v0.583.0): ISO en hora de CDMX; un plan y un chat por semana en la bitácora
     (sin SKU); la planeación se le da a la IA una vez al día y la conversación se rearma con los
     textos exactos (caché de DeepSeek); sólo la semana en curso se planea, un turno a la vez.
 10. La IA arma el plan desde CERO con listas compactas y la recomendación dentro de cada ajuste;
     sin alertas, recomendaciones ni resumen aparte; lo que va escribiendo se valida por pedazos.
 11. «CARGAR FULL CON PROMPT»: el prompt lleva la cuenta, la orden y cada SKU con su publicación;
     sólo va activo en borrador, en su semana y sin guía; en PRUEBA no confirma nada. La guía
     acepta varios PDF y los une en uno.

No se llama a Odoo, kubera ni Mercado Libre: se sustituyen por falsos.

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from datetime import date, datetime, timedelta, timezone  # noqa: F401
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fulfillment_excel as fx  # noqa: E402
from services import fulfillment_full as ff  # noqa: E402
from services import fulfillment_ia as fia  # noqa: E402
from services import fulfillment_semana as fsem  # noqa: E402
from services import fulfillment_sku as fs  # noqa: E402

AHORA = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)      # jueves de la S39
TEXCO, TEXCO2 = 135, 150


def _envio(orden="S1", cuenta="Kubera", hecha=True, dias=3, lineas=(("A", 10, 10, 0),),
           cerrado=False, avisos=True, canal="meli"):
    """lineas: (sku, pedidas, enviadas, llegadas)."""
    creada = AHORA - timedelta(days=dias + 1)
    salida = AHORA - timedelta(days=dias) if hecha else None
    e = {"canal": canal, "cuenta": cuenta, "orden": orden, "salida": f"TEXCO/OUT/{orden}",
         "estado_odoo": "done" if hecha else "assigned", "pedidas": sum(l[1] for l in lineas),
         "piezas": sum(l[2] for l in lineas) if hecha else None,
         "etapas": [{"ts": creada.isoformat()}, {"ts": salida.isoformat()} if salida else None, None, None, None],
         "lineas": [{"sku": s, "pedidas": p, "enviadas": en if hecha else None, "llegadas": ll}
                    for s, p, en, ll in lineas]}
    if hecha and avisos:
        e["cobertura"] = {"fuente": "avisos", "cerrado": cerrado}
    return e


class EnCamino(unittest.TestCase):
    def test_salida_abierta_reciente_cuenta_lo_pedido(self):
        camino, zombis = ff.en_camino([_envio(hecha=False, dias=2)], AHORA)
        self.assertEqual(camino[("meli:Kubera", "A")]["piezas"], 10)
        self.assertEqual(zombis, [])

    def test_orden_olvidada_no_se_resta(self):
        camino, zombis = ff.en_camino([_envio(orden="S26441", hecha=False, dias=40)], AHORA)
        self.assertEqual(camino, {})
        self.assertEqual((zombis[0]["orden"], zombis[0]["tienda"]), ("S26441", "meli:Kubera"))

    def test_validada_sin_cerrar_cuenta_lo_que_falta_por_llegar(self):
        camino, _ = ff.en_camino([_envio(lineas=(("A", 10, 10, 4), ("B", 5, 5, 5)))], AHORA)
        self.assertEqual(camino[("meli:Kubera", "A")]["piezas"], 6)
        self.assertNotIn(("meli:Kubera", "B"), camino, "B ya llegó completo")

    def test_cerrada_no_esta_en_camino(self):
        camino, _ = ff.en_camino([_envio(lineas=(("A", 10, 10, 4),), cerrado=True, dias=15)], AHORA)
        self.assertEqual(camino, {}, "lo que falta en un envío cerrado es lo que ML no recibió")

    def test_fba_sin_avisos_toma_lo_enviado(self):
        camino, _ = ff.en_camino([_envio(canal="amazon", cuenta="San Corpe", avisos=False, dias=5)], AHORA)
        self.assertEqual(camino[("amazon", "A")]["piezas"], 10)

    def test_sin_cuenta_no_entra(self):
        self.assertEqual(ff.en_camino([_envio(cuenta=None, hecha=False, dias=1)], AHORA), ({}, []))


class EstaSemana(unittest.TestCase):
    def test_salieron_esta_semana_mas_por_validar(self):
        salio = _envio(orden="S1", dias=1, lineas=(("A", 10, 10, 0), ("B", 5, 5, 0)))     # miércoles S39
        vieja = _envio(orden="S0", dias=8, lineas=(("C", 7, 7, 0),))                        # S38: no cuenta
        abierta = _envio(orden="S2", hecha=False, dias=0, lineas=(("A", 4, 0, 0), ("D", 3, 0, 0)))
        s = ff.esta_semana([salio, vieja, abierta], AHORA)["meli:Kubera"]
        self.assertEqual((s["envios"], s["piezas"], s["skus"]), (2, 22, 3))
        self.assertEqual(s["salieron"], {"envios": 1, "piezas": 15})
        self.assertEqual(s["por_validar"], {"envios": 1, "piezas": 7})


class Utilidades(unittest.TestCase):
    def test_modelo_base(self):
        self.assertEqual(ff.modelo_base("TEC-0393-ROS"), "TEC-0393")
        self.assertEqual(ff.modelo_base("ACC-0696-ROJ-NEG-140CM"), "ACC-0696")

    def test_parecido_detecta_reciclados(self):
        self.assertLess(ff.parecido("Binoculares 10x50 profesionales", "Faros de niebla LED para auto"), 0.15)
        self.assertGreater(ff.parecido("Audífonos inalámbricos BT 5.3 invisibles",
                                       "AUDIFONOS INVISIBLES VERDADERAMENTE CON BT 5.3"), 0.3)
        self.assertIsNone(ff.parecido("X", "Algo largo aquí"), "con una palabra no se juzga")
        self.assertGreaterEqual(ff.parecido("Flores artificiales rama 5 cabezas", "Flor artificial ramas densas"), 0.5,
                                "por raíz: flores/flor, artificiales/artificial, rama/ramas")

    def test_la_foto_de_ml_en_https_y_completa(self):
        self.assertEqual(ff._foto_ml("http://http2.mlstatic.com/D_935594-MLM107_022026-I.jpg"),
                         "https://http2.mlstatic.com/D_935594-MLM107_022026-O.jpg")
        self.assertIsNone(ff._foto_ml(None))

    def test_medidas_sospechosas_del_prompt(self):
        self.assertTrue(ff.medidas_sospechosas({"largo": 41, "ancho": 60, "alto": 40, "peso": 0.3}))
        self.assertFalse(ff.medidas_sospechosas({"largo": 41, "ancho": 60, "alto": 40, "peso": 2}))
        self.assertFalse(ff.medidas_sospechosas({"largo": 20, "ancho": 10, "alto": 5, "peso": 0.2}))
        self.assertFalse(ff.medidas_sospechosas(None))


class Propuesta(unittest.TestCase):
    def setUp(self):
        ventas = [{"canal": "mercado_libre", "cuenta": "BEKURA", "sku": "TEC-0001-ROS", "vv": 60, "v7": 20,
                   "ultima": date(2026, 9, 23)},
                  {"canal": "mercado_libre", "cuenta": "BEKURA", "sku": "TEC-0002-NEG", "vv": 9, "v7": 1, "ultima": None},
                  {"canal": "mercado_libre", "cuenta": "BEKURA", "sku": "TEC-0002-AZL", "vv": 4, "v7": 1, "ultima": None},
                  {"canal": "mercado_libre", "cuenta": "BEKURA", "sku": "HOG-0100-BLN", "vv": 15, "v7": 2, "ultima": None},
                  {"canal": "mercado_libre", "cuenta": "SANCORFASHION", "sku": "TEC-0001-ROS", "vv": 30, "v7": 7,
                   "ultima": None},
                  {"canal": "amazon", "cuenta": "AMAZON", "sku": "VIA-0024-NEG", "vv": 12, "v7": 3, "ultima": None}]
        pubs = {"meli:Kubera": {
                    "TEC-0001-ROS": {"sku": "TEC-0001-ROS", "listing_id": "MLM1", "url": "u", "situacion": "paused",
                                     "en_almacen": True, "categoria": "MLM10", "stock": 12},
                    "TEC-0002-NEG": {"sku": "TEC-0002-NEG", "listing_id": "MLM2", "url": "u", "situacion": "paused",
                                     "en_almacen": True, "categoria": "MLM20", "stock": 0},
                    "TEC-0002-AZL": {"sku": "TEC-0002-AZL", "listing_id": "MLM3", "url": "u", "situacion": "active",
                                     "en_almacen": True, "categoria": "MLM20", "stock": 4},
                    "HOG-0100-BLN": {"sku": "HOG-0100-BLN", "listing_id": "MLM4", "url": "u", "situacion": "active",
                                     "en_almacen": False, "categoria": "MLM20", "stock": 0},
                    "HOG-0200-NEG": {"sku": "HOG-0200-NEG", "listing_id": "MLM5", "url": "u", "situacion": "active",
                                     "en_almacen": False, "categoria": "MLM20", "stock": 0}},
                "meli:San Corpe": {}, "amazon": {}, "walmart": {}}
        vivos = {"meli:Kubera": {"MLM1": {"estado": "paused", "stock": 0, "logistica": "fulfillment",
                                          "titulo": "Set de brochas rosa", "categoria": "MLM10"}}}
        borradores = [
            {"orden": "S38878", "tienda": "meli:Kubera", "prueba": False, "lineas": [{"sku": "TEC-0001-ROS", "cantidad": 20}]},
            {"orden": "S39000", "tienda": "meli:Kubera", "prueba": True, "lineas": [{"sku": "TEC-0001-ROS", "cantidad": 99}]},
            {"orden": "S37000", "tienda": "meli:Kubera", "prueba": False, "dias": 40,
             "lineas": [{"sku": "TEC-0001-ROS", "cantidad": 7}]},
        ]
        productos = {"TEC-0001-ROS": {"id": 1, "name": "Brochas"}, "TEC-0002-NEG": {"id": 2, "name": "Tablet negra"},
                     "TEC-0002-AZL": {"id": 3, "name": "Tablet azul"}, "HOG-0100-BLN": {"id": 4, "name": "Lámpara"},
                     "HOG-0200-NEG": {"id": 5, "name": "Lámpara negra"}}
        libres = {1: {TEXCO: 30, TEXCO2: -4}, 2: {TEXCO: 0, TEXCO2: 0}, 3: {TEXCO: 0, TEXCO2: 8}, 4: {TEXCO: 50},
                  5: {TEXCO: 99}}
        nombres = {"TEC-0001-ROS": "SET DE BROCHAS ROSA"}
        self.p = ff.armar_propuesta(list(ff.TIENDAS), ventas, pubs, vivos, [_envio(hecha=False, dias=1,
                                    lineas=(("TEC-0001-ROS", 10, 0, 0),))], borradores, productos, libres,
                                    nombres, {}, AHORA, 30)

    def fila(self, tienda, sku):
        return next(f for f in self.p["tiendas"][tienda]["filas"] if f["sku"] == sku)

    def test_insumos_con_ml_en_vivo(self):
        a = self.fila("meli:Kubera", "TEC-0001-ROS")
        self.assertEqual(a["stock"], 0, "ML en vivo (0) manda sobre la copia del sync (12)")
        self.assertTrue(a["verificada"])
        self.assertEqual((a["vv"], a["en_camino"], a["borrador"]), (60, 10, 20),
                         "la prueba (99) no se resta, ni el borrador de 40 días (7)")
        self.assertEqual(a["libre"], {"TEXCO": 30, "TEXCO II": 0}, "libre negativo es cero para planear")
        self.assertEqual(a["nombre"], "SET DE BROCHAS ROSA", "el nombre de Omnicanal, no el de Odoo")

    def test_ganador_agotado_trae_reemplazos_publicados(self):
        g = self.fila("meli:Kubera", "TEC-0002-NEG")
        self.assertEqual([(r["sku"], r["tipo"], r["vendio"]) for r in g["reemplazos"]],
                         [("TEC-0002-AZL", "mismo modelo", 4), ("HOG-0100-BLN", "misma categoría", 15)],
                         "el siguiente que VENDE y tiene stock; HOG-0200-NEG tiene 99 libres pero no vende")

    def test_cada_tienda_con_sus_skus(self):
        self.assertEqual([f["sku"] for f in self.p["tiendas"]["meli:San Corpe"]["filas"]], ["TEC-0001-ROS"])
        fba = self.fila("amazon", "VIA-0024-NEG")
        self.assertIsNone(fba["stock"], "sin publicación FBA registrada no se sabe (no es 0)")
        self.assertIsNone(fba["libre"], "sin producto en Odoo no se sabe")
        self.assertEqual(self.p["tiendas"]["walmart"]["filas"], [])

    def test_semana_y_ventana(self):
        self.assertEqual((self.p["semana"]["semana"], self.p["semana"]["lunes"]), ("S39", "2026-09-21"))
        self.assertEqual((self.p["ventana"]["dias"], self.p["ventana"]["hasta"]), (30, "2026-09-23"))


class Almacenes(unittest.TestCase):
    def _l(self, sku, pid, n):
        return {"sku": sku, "product_id": pid, "cantidad": n}

    def test_un_almacen_que_cubre_todo_gana(self):
        r = ff.repartir_almacenes([self._l("A", 1, 5), self._l("B", 2, 5)],
                                  {1: {TEXCO: 9, TEXCO2: 50}, 2: {TEXCO: 5, TEXCO2: 0}})
        self.assertEqual([p["almacen"] for p in r["partes"]], ["TEXCO"])

    def test_cada_renglon_entero_donde_alcance(self):
        r = ff.repartir_almacenes([self._l("A", 1, 10), self._l("B", 2, 5)],
                                  {1: {TEXCO: 4, TEXCO2: 20}, 2: {TEXCO: 5, TEXCO2: 0}})
        partes = {p["almacen"]: {l["sku"]: l["cantidad"] for l in p["lineas"]} for p in r["partes"]}
        self.assertEqual(partes, {"TEXCO": {"B": 5}, "TEXCO II": {"A": 10}})

    def test_lo_que_no_hay_se_recorta_y_se_dice(self):
        r = ff.repartir_almacenes([self._l("A", 1, 10), self._l("B", 2, 3)],
                                  {1: {TEXCO: 4, TEXCO2: 2}, 2: {TEXCO: 0, TEXCO2: 0}})
        self.assertEqual([(x["sku"], x["van"], x["porque"]) for x in r["recortes"]],
                         [("A", 6, "sólo hay 6 libres en Odoo"), ("B", 0, "Odoo no tiene libre")])

    def test_entre_tiendas_se_reparte_en_proporcion(self):
        pedidos = {"meli:Kubera": [self._l("A", 1, 30)], "meli:San Corpe": [self._l("A", 1, 10)]}
        ajustados, recortes = ff.repartir_entre_tiendas(pedidos, {1: {TEXCO: 20, TEXCO2: 0}})
        self.assertEqual((ajustados["meli:Kubera"][0]["cantidad"], ajustados["meli:San Corpe"][0]["cantidad"]), (15, 5))
        self.assertEqual(len(recortes), 2)


class Limpiar(unittest.TestCase):
    def test_un_renglon_por_sku_y_solo_tiendas_conocidas(self):
        r = ff.limpiar_tiendas([{"tienda": "meli:Kubera", "lineas": [{"sku": " A ", "cantidad": 3, "sugerido": 5},
                                                                     {"sku": "A", "cantidad": 2},
                                                                     {"sku": "B", "cantidad": 0}]},
                                {"tienda": "temu", "lineas": [{"sku": "X", "cantidad": 9}]}])
        self.assertEqual(r, {"meli:Kubera": [{"sku": "A", "cantidad": 5, "sugerido": 5}]},
                         "Temu es DROP: no es tienda de la planeación")


class Buscar(unittest.TestCase):
    """Buscar para agregar: sólo lo PUBLICADO en la tienda; lo que falta se le pregunta a ML."""

    def _buscar(self, texto):
        pubs = [{"codigo": "BEKURA", "sku": s, "listing_id": f"MLM{i}", "url": None, "status": "active",
                 "situacion": "active", "en_almacen": True, "logistic_type": "fulfillment", "category_id": "MLM1",
                 "product_type": None, "stock_full": 3, "stock_fba": 0}
                for i, s in enumerate(["JUGU-0100-ROJ", "JUGU-0100-AZL", "TEC-0001-NEG"])]
        preguntados: list[str] = []

        def fetch_all(sql, params=None):
            return [dict(p) for p in pubs] if sql is ff._SQL_PUBLICACIONES else []

        def en_ml(codigo, sku):
            preguntados.append(sku)
            return []

        with mock.patch.object(ff.sdb, "fetch_all", fetch_all),              mock.patch.object(ff, "verificar_ml", lambda c, ids: {}),              mock.patch.object(ff, "buscar_ml_por_sku", en_ml),              mock.patch.object(ff.odoo_ventas, "productos_por_sku", lambda skus: {}),              mock.patch.object(ff.odoo_ventas, "libre_por_almacen", lambda ids: {}):
            return ff.buscar("meli:Kubera", texto, [], 30, AHORA), preguntados

    def test_varios_skus_aceptan_la_base_de_sus_variantes(self):
        r, preguntados = self._buscar("JUGU-0100, TEC-0001-NEG, NO-EXISTE")
        self.assertEqual([f["sku"] for f in r["filas"]], ["JUGU-0100-AZL", "JUGU-0100-ROJ", "TEC-0001-NEG"],
                         "pegar la base trae sus variantes publicadas; antes la daba por «no publicada»")
        self.assertEqual(r["no_publicados"], ["NO-EXISTE"])
        self.assertEqual(preguntados, ["NO-EXISTE"], "sólo lo que no está en la copia se le pregunta a ML")

    def test_una_tienda_que_no_existe_no_busca(self):
        r = ff.buscar("temu", "JUGU", [], 30, AHORA)
        self.assertFalse(r["ok"], "Temu es DROP: no es tienda de la planeación")


class OdooFalso:
    """Anota cada llamada. Sólo contesta lo que `crear` y `adjuntar_guia` necesitan."""

    def __init__(self, existentes=None, socio=None, orden=None):
        self.llamadas: list[tuple] = []
        self.existentes = existentes or []
        self.socio = socio
        self.orden = orden or {}
        self.siguiente = 900

    def __call__(self, modelo, metodo, args, kwargs=None):
        self.llamadas.append((modelo, metodo, args, kwargs or {}))
        if (modelo, metodo) == ("product.product", "search_read"):
            return [{"id": 1, "default_code": "A", "name": "Producto A"},
                    {"id": 2, "default_code": "B", "name": "Producto B"}]
        if (modelo, metodo) == ("product.product", "read"):
            wid = (kwargs or {}).get("context", {}).get("warehouse")
            return [{"id": 1, "free_qty": 50 if wid == TEXCO else 0},
                    {"id": 2, "free_qty": 0 if wid == TEXCO else 8}]
        if (modelo, metodo) == ("sale.order", "search_read"):
            return self.existentes
        if (modelo, metodo) == ("res.partner", "search_read"):
            return [{"id": self.socio}] if self.socio else []
        if (modelo, metodo) == ("res.partner", "create"):
            return 777
        if (modelo, metodo) == ("sale.order", "create"):
            self.siguiente += 1
            return self.siguiente
        if (modelo, metodo) == ("sale.order", "read"):
            oid = args[0][0]
            if self.orden:
                return [{**self.orden, "id": oid, "meli_etiqueta_file": "12 KB",
                         "meli_etiqueta_filename": self.orden.get("_nombre_pdf")}]
            return [{"id": oid, "name": f"S{oid}", "state": "draft", "picking_ids": [], "order_line": [1]}]
        if (modelo, metodo) == ("sale.order", "write"):
            self.orden.update({k: v for k, v in args[1].items() if k == "client_order_ref"})
            if "meli_etiqueta_filename" in args[1]:
                self.orden["_nombre_pdf"] = args[1]["meli_etiqueta_filename"]
            return True
        raise AssertionError(f"llamada no esperada a Odoo: {modelo}.{metodo}")

    def escrituras(self):
        return [(m, x) for m, x, *_ in self.llamadas if x not in ("search_read", "read", "search", "search_count")]


PEDIDO = [{"tienda": "meli:Kubera", "lineas": [{"sku": "A", "cantidad": 10, "sugerido": 12},
                                               {"sku": "B", "cantidad": 5, "sugerido": 5}]}]


class Crear(unittest.TestCase):
    def _crear(self, odoo, encendido, clave="a1b2c3d4e5", prueba=True):
        publicadas = [{"sku": "A", "listing_id": "MLM1"}, {"sku": "B", "listing_id": "MLM2"}]
        with mock.patch.object(ff.odoo_ventas, "_kw", odoo), \
             mock.patch.object(ff, "habilitado", lambda refrescar=False: encendido), \
             mock.patch.object(ff, "_guardar_solicitud", lambda *a, **k: True), \
             mock.patch.object(ff.sdb, "fetch_all", lambda *a, **k: publicadas), \
             mock.patch.object(ff, "verificar_ml", lambda c, ids: {i: {"estado": "active"} for i in ids}), \
             mock.patch.object(ff.odoo_ventas, "url_orden_publica", lambda: "https://odoo/{id}"):
            return ff.crear(PEDIDO, "brandon@kubera.mx", clave, {"cobertura_dias": 30}, prueba)

    def test_apagado_no_escribe_nada(self):
        odoo = OdooFalso()
        r = self._crear(odoo, encendido=False)
        self.assertEqual(r["accion"], "apagado")
        self.assertEqual(odoo.escrituras(), [], "con el interruptor apagado sólo se lee")
        self.assertEqual({p["almacen"]: p["piezas"] for p in r["tiendas"][0]["partes"]},
                         {"TEXCO": 10, "TEXCO II": 5})

    def test_encendido_crea_borradores_de_prueba_uno_por_almacen(self):
        odoo = OdooFalso()
        r = self._crear(odoo, encendido=True)
        self.assertEqual((r["accion"], r["ok"]), ("creada", True))
        self.assertEqual(odoo.escrituras(), [("res.partner", "create"), ("sale.order", "create"),
                                             ("sale.order", "create")], "nunca action_confirm")
        v = next(x for x in odoo.llamadas if x[:2] == ("sale.order", "create"))[2][0]
        self.assertEqual(v["partner_id"], 777, "el socio fijo de la tienda")
        self.assertIn("· a1b2c3d4e5 ·", v["origin"])
        self.assertTrue(v["origin"].endswith("· PRUEBA"))
        self.assertEqual(v["client_order_ref"], ff.PRUEBA_REF)
        renglon = v["order_line"][0][2]
        self.assertEqual((renglon["price_unit"], renglon["tax_id"]), (0.0, [(6, 0, [])]))
        self.assertEqual([o["orden"] for o in r["tiendas"][0]["ordenes"]], ["S901", "S902"])

    def test_real_no_lleva_prueba(self):
        odoo = OdooFalso()
        self._crear(odoo, encendido=True, prueba=False)
        v = next(x for x in odoo.llamadas if x[:2] == ("sale.order", "create"))[2][0]
        self.assertNotIn("client_order_ref", v)
        self.assertNotIn("PRUEBA", v["origin"])

    def test_la_misma_clave_no_crea_dos_veces(self):
        existentes = [{"id": 5, "name": "S5", "state": "draft", "warehouse_id": [TEXCO, "TEXCO"], "partner_id": [321, "x"]},
                      {"id": 6, "name": "S6", "state": "draft", "warehouse_id": [TEXCO2, "TEXCO II"], "partner_id": [321, "x"]}]
        odoo = OdooFalso(existentes=existentes, socio=321)
        r = self._crear(odoo, encendido=True)
        self.assertEqual(r["accion"], "ya_existia")
        self.assertEqual(odoo.escrituras(), [])

    def test_sin_clave_no_hace_nada(self):
        odoo = OdooFalso()
        self.assertEqual(self._crear(odoo, encendido=True, clave="x")["accion"], "sin_clave")
        self.assertEqual(odoo.llamadas, [])


class Guia(unittest.TestCase):
    PDF = b"%PDF-1.4 guia"

    def _adjuntar(self, orden, encendido=True, numero="76309173", pdf=PDF):
        odoo = OdooFalso(orden=orden)
        with mock.patch.object(ff.odoo_ventas, "_kw", odoo), \
             mock.patch.object(ff, "habilitado", lambda refrescar=False: encendido):
            return ff.adjuntar_guia(10, numero, pdf, "etiquetas.pdf", "brandon@kubera.mx"), odoo

    def test_orden_del_panel_en_borrador(self):
        orden = {"name": "S10", "state": "draft", "origin": "Panel FULLFILMENT · ML Kubera · b · c · TEXCO",
                 "client_order_ref": False}
        r, odoo = self._adjuntar(orden)
        self.assertTrue(r["ok"], r)
        w = next(x for x in odoo.llamadas if x[:2] == ("sale.order", "write"))[2][1]
        self.assertEqual((w["client_order_ref"], w["meli_etiqueta_filename"]), ("76309173", "etiquetas.pdf"))

    def test_en_prueba_el_numero_conserva_la_marca(self):
        orden = {"name": "S10", "state": "draft", "origin": "Panel FULLFILMENT · ML Kubera · b · c · TEXCO · PRUEBA",
                 "client_order_ref": ff.PRUEBA_REF}
        r, odoo = self._adjuntar(orden)
        w = next(x for x in odoo.llamadas if x[:2] == ("sale.order", "write"))[2][1]
        self.assertEqual(w["client_order_ref"], "PRUEBA · 76309173")

    def test_ajena_confirmada_apagado_o_sin_pdf_no_se_toca(self):
        ajena = {"name": "S11", "state": "draft", "origin": "S11", "client_order_ref": False}
        confirmada = {"name": "S12", "state": "sale", "origin": "Panel FULLFILMENT · x", "client_order_ref": False}
        for orden, accion in ((ajena, "ajena"), (confirmada, "confirmada")):
            r, odoo = self._adjuntar(orden)
            self.assertEqual(r["accion"], accion)
            self.assertEqual(odoo.escrituras(), [])
        r, odoo = self._adjuntar(ajena, encendido=False)
        self.assertEqual((r["accion"], odoo.llamadas), ("apagado", []))
        r, odoo = self._adjuntar(ajena, pdf=b"no soy pdf")
        self.assertEqual((r["accion"], odoo.llamadas), ("sin_pdf", []))


class SolicitudEnBitacora(unittest.TestCase):
    """La solicitud original va a ops.process_log: una fila por SKU y tienda, sin duplicar."""

    PREVIA = {"partes": [{"almacen_id": TEXCO, "almacen": "TEXCO",
                          "lineas": [{"sku": "A", "cantidad": 10}, {"sku": "B", "cantidad": 5}]},
                         {"almacen_id": TEXCO2, "almacen": "TEXCO II", "lineas": [{"sku": "A", "cantidad": 2}]}],
              "recortes": [{"tienda": "meli:Kubera", "sku": "B", "pedidas": 8, "van": 6, "porque": "reparto"},
                           {"tienda": "meli:Kubera", "sku": "B", "pedidas": 6, "van": 5,
                            "porque": "sólo hay 5 libres en Odoo"}]}
    PEDIDAS = [{"sku": "A", "cantidad": 12, "sugerido": 12}, {"sku": "B", "cantidad": 8, "sugerido": 5}]
    ORDENES = [{"id": 901, "orden": "S901", "almacen": "TEXCO"}, {"id": 902, "orden": "S902", "almacen": "TEXCO II"}]

    def test_una_fila_por_sku_con_lo_que_fue_y_sus_ordenes(self):
        f = ff.filas_solicitud("a1b2c3d4e5", "meli:Kubera", True, {"cobertura_dias": 30},
                               self.PEDIDAS, self.PREVIA, self.ORDENES)
        self.assertEqual([x["sku"] for x in f], ["A", "B"])
        self.assertEqual(f[0]["ref"], "fulfillment:a1b2c3d4e5:meli:Kubera:A")
        a, b = (x["detalle"] for x in f)
        self.assertEqual((a["solicitado"], a["sugerido"], a["van"]), (12, 12, 12))
        self.assertEqual([(x["almacen"], x["cantidad"], x["orden"]) for x in a["almacenes"]],
                         [("TEXCO", 10, "S901"), ("TEXCO II", 2, "S902")])
        self.assertEqual((a["canal"], a["cuenta"], a["prueba"]), ("mercado_libre", "BEKURA", True))
        self.assertEqual((b["solicitado"], b["sugerido"], b["van"]), (8, 5, 5))
        self.assertEqual(len(b["recortes"]), 2, "el del reparto entre tiendas y el de lo libre, los dos")

    def test_se_escribe_en_la_bitacora_sin_duplicar(self):
        llamadas = []
        with mock.patch.object(ff.sdb, "execute", lambda sql, p=None: llamadas.append((sql, p)) or 2):
            ok = ff._guardar_solicitud("a1b2c3d4e5", "meli:Kubera", False, "brandon@kubera.mx", {},
                                       self.PEDIDAS, self.PREVIA, self.ORDENES)
        self.assertTrue(ok)
        sql, p = llamadas[0]
        self.assertIn("insert into ops.process_log", sql)
        self.assertIn("not exists", sql, "reintentar con la misma clave no duplica")
        self.assertEqual((p["proceso"], p["quien"]), ("fulfillment", "brandon@kubera.mx"))
        self.assertEqual(len(json.loads(p["filas"])), 2)

    def test_si_la_bitacora_falla_la_creacion_sigue(self):
        def revienta(*a, **k):
            raise RuntimeError("kubera no contesta")
        with mock.patch.object(ff.sdb, "execute", revienta):
            self.assertFalse(ff._guardar_solicitud("a1b2c3d4e5", "meli:Kubera", True, "", {},
                                                   self.PEDIDAS, self.PREVIA, self.ORDENES))


class ValidarIA(unittest.TestCase):
    DATOS = {"tiendas": {"meli:Kubera": {
        "renglones": [{"sku": "A", "libre": 10}, {"sku": "B", "libre": 0}],
        "ganadores_agotados": [{"sku": "B", "candidatos": [{"sku": "C", "libre": 7, "tipo": "mismo modelo"}]},
                               {"sku": "D", "candidatos": [{"sku": "C", "libre": 7, "tipo": "misma categoría"}]}]}}}

    def test_listas_compactas_se_validan_y_se_topan(self):
        r = fia.validar({"respuesta": "Armé la semana.",
                         "ajustes": [["meli:Kubera", "A", 25, "vende 8 al día"], ["meli:Kubera", "Z", 3, "?"],
                                     ["temu", "A", 3, "?"], ["meli:Kubera", "C", 4, "candidato"],
                                     ["meli:Kubera", "B", "muchas", "?"]],
                         "reemplazos": [["meli:Kubera", "B", "C", 9, "mismo modelo, con stock"],
                                        ["meli:Kubera", "B", "Q", 2, "inventado"]]}, self.DATOS)
        self.assertEqual([(a["sku"], a["cantidad"]) for a in r["ajustes"]], [("A", 10), ("C", 4)],
                         "A se topa a lo libre; C es un candidato válido")
        self.assertIn("se topó", r["ajustes"][0]["nota"])
        self.assertEqual(r["ajustes"][0]["motivo"], "vende 8 al día", "la recomendación va en el ajuste")
        self.assertEqual([(x["reemplazo"], x["cantidad"], x["tipo_match"]) for x in r["reemplazos"]],
                         [("C", 7, "mismo modelo")], "el reemplazo también se topa y el tipo lo pone el código")
        self.assertEqual(len(r["descartados"]), 4)
        self.assertEqual(set(r), {"respuesta", "ajustes", "reemplazos", "descartados"},
                         "sin recomendaciones, alertas, confirmación ni resumen (Brandon, 28-sep)")

    def test_objetos_y_la_tienda_por_su_nombre_tambien_valen(self):
        datos = {"tiendas": {"meli:Kubera": {"nombre": "ML Kubera", "destino": "meli_bekura",
                                             **self.DATOS["tiendas"]["meli:Kubera"]}}}
        r = fia.validar({"ajustes": [{"tienda": "ML Kubera", "sku": "A", "piezas": 4, "recomendacion": "sube"}],
                         "reemplazos": [{"tienda": "meli_bekura", "agotado": "B", "reemplazo": "C", "cantidad": 2,
                                         "motivo": "ok"}]}, datos)
        self.assertEqual([(a["tienda"], a["cantidad"], a["motivo"]) for a in r["ajustes"]],
                         [("meli:Kubera", 4, "sube")], "la 1ª corrida real descartó todo por escribir «ML Kubera»")
        self.assertEqual([x["tienda"] for x in r["reemplazos"]], ["meli:Kubera"])
        self.assertEqual(r["descartados"], [])

    def test_el_ultimo_ajuste_de_un_sku_manda(self):
        r = fia.validar({"ajustes": [["meli:Kubera", "A", 4, "x"], ["meli:Kubera", "A", 0, "mejor no"]]}, self.DATOS)
        self.assertEqual([(a["sku"], a["cantidad"], a["motivo"]) for a in r["ajustes"]], [("A", 0, "mejor no")])

    def test_un_sku_no_reemplaza_a_dos_agotados(self):
        r = fia.validar({"reemplazos": [["meli:Kubera", "B", "C", 3, "x"], ["meli:Kubera", "D", "C", 3, "y"]]},
                        self.DATOS)
        self.assertEqual([x["agotado"] for x in r["reemplazos"]], ["B"],
                         "el 28-sep propuso TEC-1326-NEG-MOR para dos agotados: su libre se contaba dos veces")
        self.assertIn("ya es el reemplazo de B", r["descartados"][0]["porque"])

    def test_el_esquema_de_claude_exige_la_llave_de_la_tienda(self):
        ajuste = fia.ESQUEMA["properties"]["ajustes"]["items"]["properties"]["tienda"]
        self.assertEqual(ajuste["enum"], ["meli:Kubera", "meli:San Corpe", "amazon", "walmart"])


class IAEnVivo(unittest.TestCase):
    """La respuesta llega por streaming y se aplica a la tabla mientras se escribe."""

    DATOS = ValidarIA.DATOS

    def test_lo_que_lleva_escrito_se_valida_por_pedazos(self):
        completo = json.dumps({"respuesta": "Listo.", "ajustes": [["meli:Kubera", "A", 5, "sube"],
                                                                  ["meli:Kubera", "C", 2, "r"]], "reemplazos": []})
        corte = completo.index('["meli:Kubera", "C"') + 8
        p = fia.parcial(completo[:corte], self.DATOS)
        self.assertEqual(p["respuesta"], "Listo.")
        self.assertEqual([a["sku"] for a in p["ajustes"]], ["A"], "el segundo todavía no termina de llegar")
        self.assertEqual([a["sku"] for a in fia.parcial(completo, self.DATOS)["ajustes"]], ["A", "C"])
        self.assertEqual(fia.parcial('{"respues', self.DATOS), {"respuesta": "", "ajustes": [], "reemplazos": []})
        falso = json.dumps({"respuesta": 'dije "ajustes": [ sin querer', "ajustes": [["meli:Kubera", "A", 1, "x"]]})
        self.assertEqual([a["sku"] for a in fia.parcial(falso, self.DATOS)["ajustes"]], ["A"],
                         "la palabra dentro del texto no confunde la lista")

    def test_el_stream_de_deepseek(self):
        pedazos = [{"model": "deepseek-v4-pro", "choices": [{"delta": {"reasoning_content": "pienso..."}}]},
                   {"choices": [{"delta": {"content": '{"respuesta": "ok", '}}]},
                   {"choices": [{"delta": {"content": '"ajustes": [], "reemplazos": []}'}, "finish_reason": "stop"}]},
                   {"choices": [], "usage": {"prompt_tokens": 100, "prompt_cache_hit_tokens": 60,
                                             "completion_tokens": 30,
                                             "completion_tokens_details": {"reasoning_tokens": 12}}}]
        lineas = [": keep-alive", ""] + [f"data: {json.dumps(p)}" for p in pedazos] + ["data: [DONE]"]
        vistos = []
        s = fia.leer_sse(lineas, lambda texto, razon: vistos.append((len(texto), razon)))
        self.assertEqual(json.loads(s["texto"])["respuesta"], "ok")
        self.assertEqual((s["fin"], s["modelo"], s["razonamiento_chars"]), ("stop", "deepseek-v4-pro", 9))
        self.assertEqual(s["uso"]["prompt_cache_hit_tokens"], 60)
        self.assertEqual(vistos[-1][0], len(s["texto"]), "el último aviso trae todo lo escrito")

    def test_reintenta_sin_razonamiento_si_contesta_vacio(self):
        llamadas = []

        def pedir(cuerpo, al_avanzar):
            llamadas.append(cuerpo)
            texto = "" if len(llamadas) == 1 else '{"respuesta": "ok", "ajustes": [], "reemplazos": []}'
            return {"status": 200, "error": None, "texto": texto, "razonamiento_chars": 0, "fin": "stop",
                    "modelo": "deepseek-v4-pro",
                    "uso": {"prompt_tokens": 100, "prompt_cache_hit_tokens": 60, "completion_tokens": 20}}

        with mock.patch.object(fia, "_pedir", pedir):
            r = fia._llamar_deepseek([{"role": "user", "content": "x"}], "deepseek-v4-pro")
        self.assertEqual(r["respuesta"]["respuesta"], "ok")
        self.assertNotIn("thinking", llamadas[0], "la primera va con el razonamiento por omisión")
        self.assertEqual(llamadas[1]["thinking"], {"type": "disabled"}, "la segunda, sin razonamiento")
        self.assertEqual((llamadas[0]["stream"], llamadas[0]["stream_options"]), (True, {"include_usage": True}))
        self.assertEqual(llamadas[0]["response_format"], {"type": "json_object"})
        self.assertEqual(llamadas[0]["messages"][0]["role"], "system")
        self.assertEqual((r["tokens"]["cache"], r["tokens"]["entrada_sin_cache"]), (60, 40))

    def test_un_error_de_deepseek_se_dice(self):
        with mock.patch.object(fia, "_pedir", lambda c, a: {"status": 401, "error": "llave inválida"}):
            with self.assertRaisesRegex(RuntimeError, "401"):
                fia._llamar_deepseek([], "deepseek-v4-pro")


class ChatDeLaSemana(unittest.TestCase):
    """Un chat por semana, guardado; el plan nace vacío y la planeación se le da una vez al día."""

    DATOS = {"corrida": {"ventana": "30 días", "cobertura_dias": 30, "renglones_pendientes": 2},
             "tiendas": {"meli:Kubera": {"columnas": ["sku", "libre"], "filas": [["A", 10], ["B", 4]],
                                         "ganadores_agotados": []}}}
    LUNES = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)       # lunes de la S40

    def tearDown(self):
        fia._trabajos.clear()

    def test_el_plan_de_ese_momento_va_en_el_mensaje(self):
        vacio = fia.mensaje_turno("", fia.plan_actual([]))
        self.assertIn("vacío", vacio)
        self.assertIn("Sin instrucciones", vacio)
        plan = fia.plan_actual([["meli:Kubera", "B", 4], ["meli:Kubera", "A", 10], ["meli:Kubera", "C", 0],
                                ["temu", "X", 3], "basura"])
        self.assertEqual(plan, {"meli:Kubera": [["A", 10], ["B", 4]]}, "ordenado, sin ceros ni tiendas ajenas")
        m = fia.mensaje_turno("sólo < $300", plan)
        self.assertIn('{"meli:Kubera":[["A",10],["B",4]]}', m)
        self.assertIn("sólo < $300", m)

    def test_la_conversacion_se_rearma_con_los_textos_exactos(self):
        turnos = [{"mensaje": "M1", "salida": "S1"}, {"mensaje": "M2", "salida": "S2"}]
        m = fia.conversacion_openai(self.DATOS, turnos, "M3")
        self.assertEqual([x["role"] for x in m], ["user", "assistant", "user", "assistant", "user"])
        self.assertTrue(m[0]["content"].startswith('{"corrida"'), "la planeación al principio: prefijo en caché")
        self.assertTrue(m[0]["content"].endswith("M1"))
        self.assertEqual([x["content"] for x in m[1:]], ["S1", "M2", "S2", "M3"])
        self.assertEqual(fia.conversacion_openai(self.DATOS, turnos[:1], "M2"), m[:3],
                         "lo que se mandó en el turno 2 es el principio EXACTO del turno 3: DeepSeek lo relee de caché")

    def test_la_tabla_se_escribe_igual_venga_de_la_pantalla_o_de_la_bitacora(self):
        de_la_pantalla = {"tiendas": {"meli:Kubera": {"columnas": ["sku", "libre"], "filas": [["A", 10]]}},
                          "corrida": {"ventana": "30 días", "cobertura_dias": 30}}
        # jsonb no respeta el orden de las llaves: así regresa de la bitácora.
        de_la_bitacora = {"corrida": {"cobertura_dias": 30, "ventana": "30 días"},
                          "tiendas": {"meli:Kubera": {"filas": [["A", 10]], "columnas": ["sku", "libre"]}}}
        self.assertEqual(fia.conversacion_openai(de_la_pantalla, [], "M")[0]["content"],
                         fia.conversacion_openai(de_la_bitacora, [], "M")[0]["content"],
                         "el 28-sep el 2º turno releyó de caché 1,024 de 61,311 tokens por el orden de las llaves")

    def test_la_huella_no_cambia_por_los_pendientes(self):
        otra = {**self.DATOS, "corrida": {**self.DATOS["corrida"], "renglones_pendientes": 5}}
        self.assertEqual(fia._huella(self.DATOS), fia._huella(otra))
        self.assertNotEqual(fia._huella(self.DATOS),
                            fia._huella({**self.DATOS, "corrida": {**self.DATOS["corrida"], "cobertura_dias": 45}}))

    def _correr(self, cuerpo, conv, falla=None, plan_base=None):
        guardados, vistos = [], []
        self.planes = []

        def llamar(mensajes, modelo=None, al_avanzar=None):
            vistos.append(mensajes)
            if falla:
                raise RuntimeError(falla)
            if al_avanzar:
                al_avanzar('{"respuesta": "Armé la semana.", "ajustes": [["meli:Kubera", "A", 6, "sube"]', 1000)
            return {"respuesta": {"respuesta": "Armé la semana.",
                                  "ajustes": [["meli:Kubera", "A", 6, "sube"], ["meli:Kubera", "Z", 1, "?"]],
                                  "reemplazos": []},
                    "texto": "SALIDA", "modelo": "deepseek-v4-pro",
                    "tokens": {"entrada": 100, "entrada_sin_cache": 40, "cache": 60, "salida": 10}}

        def guardar(clave, accion, detalle, quien="", sufijo=""):
            guardados.append((clave, accion, detalle))
            return 77

        def guardar_plan(clave, plan, quien=""):
            self.planes.append((clave, plan, quien))
            return {"ok": True, "version": plan["version"]}

        with mock.patch.object(fia, "_hay_llave", lambda p: True), mock.patch.object(fia, "_llamar", llamar), \
             mock.patch.object(fia.fsem, "conversacion", lambda clave: conv), \
             mock.patch.object(fia.fsem, "guardar", guardar), \
             mock.patch.object(fia.fsem, "ultimo_plan", lambda clave: plan_base), \
             mock.patch.object(fia.fsem, "guardar_plan", guardar_plan), \
             mock.patch.object(fia.settings, "fulfillment_ia_modelo", "deepseek-v4-pro"):
            r = fia.iniciar(cuerpo, "brandon@kubera.mx", self.LUNES)
            if r.get("ok"):
                for _ in range(300):
                    if fia.estado(r["id"])["estado"] != "corriendo":
                        break
                    time.sleep(0.01)
        return r, guardados, vistos

    def test_primer_turno_de_la_semana(self):
        r, guardados, vistos = self._correr({"semana": "2026-S40", "datos": self.DATOS, "plan": [],
                                             "instrucciones": "arma el FULL"}, {"datos": None, "turnos": []})
        self.assertTrue(r["ok"], r)
        e = fia.estado(r["id"])
        self.assertEqual(e["estado"], "listo")
        self.assertEqual([(a["sku"], a["cantidad"]) for a in e["resultado"]["ajustes"]], [("A", 6)])
        self.assertEqual(len(e["resultado"]["descartados"]), 1)
        self.assertEqual([g[1] for g in guardados], ["datos", "turno"], "la planeación de hoy y el turno")
        self.assertEqual(guardados[0][2]["dia"], "2026-09-28")
        turno = guardados[1][2]
        self.assertEqual((turno["estado"], turno["salida"], turno["datos_id"]), ("listo", "SALIDA", 77))
        self.assertIn("vacío", turno["mensaje"], "la semana empieza en cero")
        self.assertEqual(len(vistos[0]), 1, "primer turno: un solo mensaje, con la planeación al principio")
        # El SERVIDOR guarda el plan con lo que propuso: si la persona se salió, al volver ya está.
        self.assertEqual(len(self.planes), 1)
        clave, plan, quien = self.planes[0]
        self.assertEqual((clave, plan["version"], quien), ("2026-S40", 1, "brandon@kubera.mx"))
        self.assertEqual([(x["sku"], x["cantidad"], x["origen"], x["turno"]) for x in plan["entradas"]],
                         [("A", 6, "ia", r["id"])])
        self.assertEqual(e["resultado"]["plan_version"], 1)

    def test_el_resultado_se_suma_al_plan_guardado(self):
        base = {"version": 3, "quitados": [],
                "entradas": [{"tienda": "meli:Kubera", "sku": "B", "cantidad": 4, "origen": "manual"}]}
        self._correr({"semana": "2026-S40", "datos": self.DATOS, "plan": [["meli:Kubera", "B", 4]]},
                     {"datos": None, "turnos": []}, plan_base=base)
        _, plan, _ = self.planes[0]
        self.assertEqual(plan["version"], 4)
        self.assertEqual(sorted((x["sku"], x["cantidad"], x["origen"]) for x in plan["entradas"]),
                         [("A", 6, "ia"), ("B", 4, "manual")], "lo que la persona puso a mano se queda")

    def test_si_la_ia_falla_no_se_toca_el_plan(self):
        self._correr({"semana": "2026-S40", "datos": self.DATOS, "plan": []}, {"datos": None, "turnos": []},
                     falla="DeepSeek contestó 500")
        self.assertEqual(self.planes, [])

    def test_la_actividad_para_el_icono_de_la_pestana(self):
        ahora = time.time()
        base = {"quien": "brandon@kubera.mx", "instrucciones": "", "modelo": "deepseek-v4-pro"}
        fia._trabajos.update({
            "c1": {**base, "estado": "corriendo", "inicio": ahora - 30, "semana": "2026-S40", "fase": "pensando"},
            "l1": {**base, "estado": "listo", "inicio": ahora - 400, "fin": ahora - 100, "semana": "2026-S40",
                   "resultado": {"ajustes": [{"cantidad": 5}, {"cantidad": 0}], "reemplazos": [{"cantidad": 2}]}},
            "l0": {**base, "estado": "listo", "inicio": ahora - 900, "fin": ahora - 800, "semana": "2026-S40",
                   "resultado": {"ajustes": [], "reemplazos": []}},
            "s39": {**base, "estado": "listo", "inicio": ahora - 60, "fin": ahora - 5, "semana": "2026-S39"},
        })
        a = fia.actividad("2026-S40")
        self.assertEqual((a["corriendo"]["id"], a["corriendo"]["fase"]), ("c1", "pensando"))
        self.assertEqual((a["ultimo"]["id"], a["ultimo"]["estado"], a["ultimo"]["skus"], a["ultimo"]["piezas"]),
                         ("l1", "listo", 2, 7), "el último que terminó ESA semana, con lo que puso")
        self.assertEqual(fia.actividad("2026-S41"), {"semana": "2026-S41", "corriendo": None, "ultimo": None})

    def test_seguimiento_del_mismo_dia_reusa_la_planeacion(self):
        conv = {"datos": {"id": 5, "dia": "2026-09-28", "huella": fia._huella(self.DATOS), "datos": self.DATOS},
                "turnos": [{"mensaje": "M1", "salida": "S1"}]}
        r, guardados, vistos = self._correr({"semana": "2026-S40", "datos": self.DATOS,
                                             "plan": [["meli:Kubera", "A", 6]], "instrucciones": "quita A"}, conv)
        self.assertEqual([g[1] for g in guardados], ["turno"], "no se vuelve a guardar la planeación")
        self.assertEqual(guardados[0][2]["datos_id"], 5)
        self.assertEqual([x["content"] for x in vistos[0][1:]], ["S1", guardados[0][2]["mensaje"]])
        self.assertIn('[["A",6]]', guardados[0][2]["mensaje"], "el plan de ese momento viaja en el mensaje")

    def test_otro_dia_otra_planeacion(self):
        conv = {"datos": {"id": 5, "dia": "2026-09-27", "huella": fia._huella(self.DATOS), "datos": self.DATOS},
                "turnos": []}
        _, guardados, _ = self._correr({"semana": "2026-S40", "datos": self.DATOS, "plan": []}, conv)
        self.assertEqual([g[1] for g in guardados], ["datos", "turno"])

    def test_si_la_ia_falla_el_turno_queda_con_su_error(self):
        r, guardados, _ = self._correr({"semana": "2026-S40", "datos": self.DATOS, "plan": []},
                                       {"datos": None, "turnos": []}, falla="DeepSeek contestó 402")
        self.assertEqual(fia.estado(r["id"])["estado"], "error")
        self.assertEqual((guardados[-1][1], guardados[-1][2]["estado"]), ("turno", "error"))
        self.assertIn("402", guardados[-1][2]["error"])

    def test_solo_la_semana_en_curso_y_un_turno_a_la_vez(self):
        with mock.patch.object(fia, "_hay_llave", lambda p: True):
            r = fia.iniciar({"semana": "2026-S39", "datos": self.DATOS}, "b@k.mx", self.LUNES)
            self.assertFalse(r["ok"])
            self.assertIn("semana en curso", r["motivo"])
            fia._trabajos["x1"] = {"estado": "corriendo", "inicio": time.time(), "quien": "cinthya@kubera.mx",
                                   "semana": "2026-S40", "instrucciones": "", "modelo": "deepseek-v4-pro"}
            r = fia.iniciar({"semana": "2026-S40", "datos": self.DATOS}, "b@k.mx", self.LUNES)
        self.assertEqual((r["ok"], r["id"]), (False, "x1"))
        self.assertIn("cinthya", r["motivo"])
        self.assertEqual(fia.corriendo_en("2026-S40")["id"], "x1")

    def test_sin_clave_no_arranca(self):
        with mock.patch.object(fia.settings, "anthropic_api_key", ""), \
             mock.patch.object(fia.settings, "deepseek_api_key", ""):
            self.assertFalse(fia.iniciar({"datos": self.DATOS})["ok"])

    def test_modelo_desconocido_o_sin_llave_no_arranca(self):
        with mock.patch.object(fia.settings, "anthropic_api_key", ""), \
             mock.patch.object(fia.settings, "deepseek_api_key", "x"):
            self.assertIn("No conozco", fia.iniciar({"datos": self.DATOS, "modelo": "gpt-9"})["motivo"])
            self.assertIn("No conozco", fia.iniciar({"datos": self.DATOS, "modelo": "claude-opus-5"})["motivo"],
                          "Claude ya no se ofrece: es caro (Brandon, 27-sep)")

    def test_solo_deepseek_aunque_haya_llave_de_claude(self):
        with mock.patch.object(fia.settings, "fulfillment_ia_modelo", "deepseek-v4-pro"), \
             mock.patch.object(fia.settings, "deepseek_api_key", ""), \
             mock.patch.object(fia.settings, "anthropic_api_key", "x"):
            self.assertFalse(fia.disponible(), "con llave de Claude pero sin DeepSeek, no hay agente")
            self.assertEqual([m["id"] for m in fia.modelos_disponibles()], ["deepseek-v4-pro", "deepseek-flash"])

    def test_costo_del_turno_con_hora_pico(self):
        tokens = {"entrada_sin_cache": 80_000, "cache": 0, "salida": 10_000}
        pico = datetime(2026, 9, 28, 7, 0, tzinfo=timezone.utc)          # lunes 07:00 UTC
        valle = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)        # lunes 18:00 UTC
        self.assertEqual(fia.costo_usd("deepseek-v4-pro", tokens, pico), round((80_000 * 1.32 + 10_000 * 3.96) / 1e6, 4))
        self.assertEqual(fia.costo_usd("deepseek-v4-pro", tokens, valle),
                         round((80_000 * 1.32 + 10_000 * 3.96) / 1e6 / 2, 4), "fuera de pico, la mitad")
        flash = fia.costo_usd("deepseek-flash", {"entrada_sin_cache": 20_000, "cache": 60_000, "salida": 5_000}, pico)
        self.assertEqual(flash, round((20_000 * 0.30 + 60_000 * 0.006 + 5_000 * 1.20) / 1e6, 4))


class SemanaFull(unittest.TestCase):
    """La semana ISO en hora de CDMX y su plan en la bitácora."""

    def test_la_semana_es_la_iso_en_hora_de_cdmx(self):
        domingo = datetime(2026, 9, 28, 5, 30, tzinfo=timezone.utc)      # domingo 27, 23:30 en CDMX
        lunes = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)        # lunes 28, 00:30 en CDMX
        self.assertEqual(fsem.semana_de(domingo)["clave"], "2026-S39")
        s = fsem.semana_de(lunes)
        self.assertEqual((s["clave"], s["lunes"], s["domingo"], s["rango"]),
                         ("2026-S40", "2026-09-28", "2026-10-04", "28 sep – 4 oct"))
        self.assertEqual(fsem.semana_por_clave("2026-S40"), s)
        self.assertEqual(fsem.semana_por_clave("2026-S39")["rango"], "21–27 sep")
        for mala in ("2026-S60", "S40", "", None):
            self.assertIsNone(fsem.semana_por_clave(mala))
        self.assertTrue(fsem.en_semana("2026-09-28T06:30:00+00:00", "2026-S40"))
        self.assertFalse(fsem.en_semana("2026-09-28T05:30:00+00:00", "2026-S40"))
        self.assertFalse(fsem.en_semana(None, "2026-S40"))

    def test_el_plan_se_guarda_limpio(self):
        p = fsem.limpiar_plan({
            "version": 3, "activas": ["meli:Kubera", "temu"], "parametros": {"cobertura_dias": "30", "otra": 5},
            "entradas": [
                {"tienda": "meli:Kubera", "sku": "A", "cantidad": "12", "origen": "ia", "motivo": "x" * 400},
                {"tienda": "meli:Kubera", "sku": "A", "cantidad": 8, "origen": "manual"},
                {"tienda": "temu", "sku": "B", "cantidad": 3},
                {"tienda": "meli:San Corpe", "sku": "C", "cantidad": -4, "origen": "raro", "reemplazo_de": "D",
                 "incluido": False}],
            "quitados": ["meli:Kubera|Z", "basura", "temu|Q"]})
        self.assertEqual([(e["tienda"], e["sku"], e["cantidad"], e["origen"], e["incluido"]) for e in p["entradas"]],
                         [("meli:Kubera", "A", 8, "manual", True), ("meli:San Corpe", "C", 0, "manual", False)],
                         "un renglón por tienda y SKU (manda el último); Temu no entra")
        self.assertEqual(p["entradas"][1]["reemplazo_de"], "D")
        self.assertEqual((p["quitados"], p["activas"], p["parametros"], p["version"]),
                         (["meli:Kubera|Z"], ["meli:Kubera"], {"cobertura_dias": 30}, 3))
        self.assertEqual(len(fsem.limpiar_plan({"entradas": [{"tienda": "amazon", "sku": "X", "motivo": "y" * 900}]})
                             ["entradas"][0]["motivo"]), 300)

    def test_el_turno_de_la_ia_se_aplica_al_plan(self):
        base = {"version": 2, "quitados": ["meli:Kubera|Q"], "entradas": [
            {"tienda": "meli:Kubera", "sku": "A", "cantidad": 5, "origen": "manual", "reemplazo_de": "V"},
            {"tienda": "meli:Kubera", "sku": "B", "cantidad": 3, "origen": "estandar"}]}
        r = {"ajustes": [{"tienda": "meli:Kubera", "sku": "A", "cantidad": 9, "motivo": "vende más"},
                         {"tienda": "meli:Kubera", "sku": "B", "cantidad": 0, "motivo": "ticket alto"},
                         {"tienda": "meli:Kubera", "sku": "Q", "cantidad": 7, "motivo": "lo habían quitado"}],
             "reemplazos": [{"tienda": "meli:San Corpe", "agotado": "X", "reemplazo": "Y", "cantidad": 4,
                             "motivo": "mismo modelo"}]}
        p = fsem.aplicar_turno(base, r, "t9")
        por = {(e["tienda"], e["sku"]): e for e in p["entradas"]}
        a = por[("meli:Kubera", "A")]
        self.assertEqual(p["version"], 3)
        self.assertEqual((a["cantidad"], a["origen"], a["reemplazo_de"], a["turno"], a["motivo"]),
                         (9, "ia", "V", "t9", "vende más"), "conserva de quién es reemplazo")
        self.assertEqual(por[("meli:Kubera", "B")]["cantidad"], 0, "0 = la IA lo saca")
        self.assertNotIn(("meli:Kubera", "Q"), por, "lo que la persona quitó no regresa")
        y = por[("meli:San Corpe", "Y")]
        self.assertEqual((y["reemplazo_de"], y["cantidad"], y["incluido"]), ("X", 4, True))
        self.assertEqual(fsem.aplicar_turno(None, r, "t1")["version"], 1, "sobre una semana vacía")

    def test_cuanto_va_en_un_plan(self):
        r = fsem.resumen_plan({"entradas": [
            {"tienda": "meli:Kubera", "sku": "A", "cantidad": 10, "origen": "ia", "incluido": True},
            {"tienda": "meli:Kubera", "sku": "B", "cantidad": 5, "origen": "manual", "reemplazo_de": "Z",
             "incluido": True},
            {"tienda": "meli:Kubera", "sku": "C", "cantidad": 7, "origen": "ia", "incluido": False},
            {"tienda": "meli:San Corpe", "sku": "D", "cantidad": 0, "origen": "ia", "incluido": True}]})
        self.assertEqual((r["skus"], r["piezas"], r["reemplazos"], r["de_ia"]), (2, 15, 1, 1),
                         "lo des-seleccionado y lo que va en 0 no cuentan")

    def test_la_bitacora_sin_sku_y_con_su_referencia(self):
        llamadas = []
        with mock.patch.object(fsem.sdb, "execute_returning", lambda sql, p=None: llamadas.append((sql, p)) or {"id": 41}):
            self.assertEqual(fsem.guardar("2026-S40", "plan", {"x": 1}, "brandon@kubera.mx", "3"), 41)
        sql, p = llamadas[0]
        self.assertIn("insert into ops.process_log", sql)
        self.assertNotIn("sku", sql.split("values")[0], "sin SKU: no es el «último paso» de ningún producto")
        self.assertEqual((p["p"], p["a"], p["r"], p["q"]),
                         ("fulfillment_semana", "plan", "fulfillment_semana:2026-S40:plan:3", "brandon@kubera.mx"))

    def test_leer_la_semana(self):
        t = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)
        filas = [{"id": 1, "accion": "datos", "actor": "b", "created_at": t, "detalle": {"dia": "2026-09-28"}},
                 {"id": 2, "accion": "turno", "actor": "b", "created_at": t, "detalle": {"id": "t1", "estado": "listo"}},
                 {"id": 3, "accion": "plan", "actor": "b", "created_at": t, "detalle": {"version": 1, "entradas": []}},
                 {"id": 4, "accion": "plan", "actor": "c", "created_at": t, "detalle": {"version": 2, "entradas": []}}]
        vistos = []
        with mock.patch.object(fsem.sdb, "fetch_all", lambda sql, p=None: vistos.append((sql, p)) or filas):
            s = fsem.leer("2026-S40")
        self.assertEqual((s["plan"]["version"], s["plan"]["quien"]), (2, "c"), "manda el último guardado")
        self.assertEqual([x["id"] for x in s["turnos"]], ["t1"])
        self.assertEqual(s["datos"]["dia"], "2026-09-28")
        self.assertIn("detalle - 'datos'", vistos[0][0], "la planeación completa no viaja a la pantalla")
        self.assertEqual(vistos[0][1]["pref"], "fulfillment_semana:2026-S40:%")


    def test_la_conversacion_sale_en_el_orden_en_que_se_guardo(self):
        consultas = []

        def fetch_all(sql, p=None):
            consultas.append(sql)
            return [{"fila": 7, "turno": "b2", "mensaje": "M1", "salida": "S1"},
                    {"fila": 9, "turno": "a1", "mensaje": "M2", "salida": "S2"},
                    {"fila": 10, "turno": "c3", "mensaje": "M3", "salida": None}]

        with mock.patch.object(fsem.sdb, "fetch_one",
                               lambda sql, p=None: {"id": 5, "detalle": {"dia": "2026-09-28", "datos": {"tiendas": {}}}}),              mock.patch.object(fsem.sdb, "fetch_all", fetch_all):
            c = fsem.conversacion("2026-S40")
        self.assertEqual([t["id"] for t in c["turnos"]], ["b2", "a1"], "en orden de la bitácora; sin salida no se rearma")
        self.assertIn("order by l.id", consultas[0],
                      "por la fila, no por el id del turno: con alias «id» el orden salía revuelto")
        self.assertEqual((c["datos"]["id"], c["datos"]["dia"]), (5, "2026-09-28"))


class CargarFullConPrompt(unittest.TestCase):
    """El prompt para que un agente con navegador cargue el FULL en Mercado Libre y suba su guía."""

    ORDEN = {"id": 55, "name": "S38990", "state": "draft", "client_order_ref": False,
             "origin": "Panel FULLFILMENT · ML Kubera · brandon · a1b2c3d4 · TEXCO",
             "create_date": "2026-09-28 16:00:00", "partner_id": [9, "FULL KUBERA"], "warehouse_id": [135, "TEXCO"],
             "order_line": [1, 2], "meli_etiqueta_filename": False}
    MARTES = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)

    def _prompt(self, orden, ahora=MARTES, panel="https://panel.example"):
        def kw(modelo, metodo, args, kwargs=None):
            if (modelo, metodo) == ("sale.order", "read"):
                return [orden]
            if (modelo, metodo) == ("sale.order.line", "read"):
                return [{"product_id": [1, "[TEC-0393-ROS] Audífonos invisibles rosa"], "product_uom_qty": 251.0},
                        {"product_id": [2, "[JUEG-0012-MUL] Torre de madera"], "product_uom_qty": 0.0}]
            raise AssertionError(f"llamada no esperada a Odoo: {modelo}.{metodo}")

        pubs = [{"sku": "TEC-0393-ROS", "listing_id": "MLM111", "en_almacen": False},
                {"sku": "TEC-0393-ROS", "listing_id": "MLM2703304601", "en_almacen": True}]
        with mock.patch.object(ff.odoo_ventas, "_kw", kw), mock.patch.object(ff.sdb, "fetch_all", lambda *a, **k: pubs), \
             mock.patch.object(ff.odoo_ventas, "url_orden_publica", lambda: "https://odoo/{id}"):
            return ff.prompt_ml(55, panel, ahora)

    def test_lleva_cuenta_orden_y_cada_sku_con_su_publicacion(self):
        r = self._prompt(dict(self.ORDEN))
        self.assertTrue(r["ok"] and r["activo"], r)
        p = r["prompt"]
        for texto in ("Kubera (BEKURA)", "S38990", "almacén TEXCO", "S40 (28 sep – 4 oct)",
                      "MLM2703304601 · TEC-0393-ROS · 251 pzs · Audífonos invisibles rosa", "PREGÚNTAME",
                      "https://panel.example/fulfillment", "Planificación de envíos", "Adjuntar guía",
                      "No toques la orden en Odoo"):
            self.assertIn(texto, p)
        self.assertNotIn("JUEG-0012-MUL", p, "un renglón en 0 no se manda")
        self.assertEqual((r["piezas"], r["tienda"], r["lineas"][0]["listing_id"]),
                         (251, "meli:Kubera", "MLM2703304601"), "si hay dos publicaciones, la que ya es FULL")

    def test_la_prueba_no_confirma_nada(self):
        p = self._prompt({**self.ORDEN, "origin": self.ORDEN["origin"] + " · PRUEBA"})["prompt"]
        self.assertIn("ES UNA PRUEBA", p)
        self.assertIn("No lo confirmes", p)
        self.assertNotIn("Adjuntar guía", p)

    def test_cuando_el_boton_no_va_activo(self):
        casos = [({**self.ORDEN, "state": "sale"}, self.MARTES, "ya no está en borrador"),
                 ({**self.ORDEN, "meli_etiqueta_filename": "guia.pdf"}, self.MARTES, "ya tiene su guía"),
                 (dict(self.ORDEN), datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc), "no es de esta semana"),
                 ({**self.ORDEN, "partner_id": [9, "AMAZON FBA"]}, self.MARTES, "es para Mercado Libre")]
        for orden, ahora, porque in casos:
            r = self._prompt(orden, ahora)
            self.assertFalse(r["activo"], porque)
            self.assertIn(porque, r["porque"])
        r = self._prompt({**self.ORDEN, "origin": "S38990"})
        self.assertFalse(r["ok"], "una orden que no creó el panel no tiene prompt")

    def test_una_liga_rara_no_entra_al_prompt(self):
        p = self._prompt(dict(self.ORDEN), panel="javascript:alert(1)")["prompt"]
        self.assertIn(ff.PANEL_POR_OMISION + "/fulfillment", p)
        self.assertNotIn("javascript", p)


@unittest.skipUnless(__import__("importlib.util").util.find_spec("pypdf"), "pypdf no está instalado aquí (en Railway sí: requirements.txt)")
class GuiaVariosPdf(unittest.TestCase):
    @staticmethod
    def _pdf(paginas):
        import io

        from pypdf import PdfWriter
        w = PdfWriter()
        for _ in range(paginas):
            w.add_blank_page(width=200, height=200)
        b = io.BytesIO()
        w.write(b)
        return b.getvalue()

    def test_se_unen_en_uno_y_en_orden(self):
        import io

        from pypdf import PdfReader
        unido = ff.unir_pdfs([self._pdf(1), self._pdf(2)])
        self.assertTrue(unido.startswith(b"%PDF"))
        self.assertEqual(len(PdfReader(io.BytesIO(unido)).pages), 3)
        solo = self._pdf(1)
        self.assertIs(ff.unir_pdfs([solo]), solo, "con uno solo, se queda tal cual")


class Precio(unittest.TestCase):
    def test_ml_en_vivo_manda_y_walmart_trae_monto(self):
        self.assertEqual(ff._precio({"amount": "249.5", "currency": "MXN"}), 249.5)
        self.assertIsNone(ff._precio(None))
        self.assertIsNone(ff._precio(0), "un precio en 0 no es un precio")
        f = ff._fila("meli:Kubera", "A", None, {"listing_id": "MLM1", "precio": 300}, {"precio": 279.0},
                     None, None, None, None, "A", None)
        self.assertEqual(f["precio"], 279.0)
        f = ff._fila("amazon", "A", None, {"listing_id": "B0", "precio": "412.00"}, None,
                     None, None, None, None, "A", None)
        self.assertEqual(f["precio"], 412.0)


class Analisis(unittest.TestCase):
    """Lo que se mudó a Análisis: órdenes sin completar y títulos contra Odoo."""

    def test_cada_salida_abierta_dice_cuanto_lleva(self):
        r = ff.salidas_abiertas([_envio(orden="S1", hecha=False, dias=2),
                                 _envio(orden="S2", hecha=False, dias=30),
                                 _envio(orden="S3", hecha=True)], AHORA)
        self.assertEqual([(x["orden"], x["dias"], x["olvidada"]) for x in r], [("S2", 31, True), ("S1", 3, False)],
                         "la más vieja primero; la validada no está")

    def test_borrador_que_se_resta(self):
        self.assertTrue(ff.se_resta({"tienda": "meli:Kubera", "prueba": False, "dias": 21}))
        self.assertFalse(ff.se_resta({"tienda": "meli:Kubera", "prueba": False, "dias": 22}), "más de 21 días")
        self.assertFalse(ff.se_resta({"tienda": "meli:Kubera", "prueba": True, "dias": 1}), "prueba")
        self.assertFalse(ff.se_resta({"tienda": None, "prueba": False, "dias": 1}), "sin tienda")

    def test_el_titulo_se_compara_contra_odoo_con_su_foto(self):
        vivo = {"titulo": "Monitor portátil para xbox gaming ps5", "imagen": "https://http2.mlstatic.com/x.jpg",
                "estado": "active", "categoria": "MLM1"}
        f = ff._fila("meli:Kubera", "TEC-0492-MUL", None, {"listing_id": "MLM9", "categoria": "MLM1"}, vivo,
                     {"id": 9, "name": "PANTALLA LED P1.86 INTERIOR UHD"}, None, None, None, "Pantalla LED", None)
        self.assertIn("reciclado", f["alertas"])
        self.assertEqual((f["nombre_odoo"], f["imagen_mkt"], f["parecido"]),
                         ("PANTALLA LED P1.86 INTERIOR UHD", "https://http2.mlstatic.com/x.jpg", 0.0))
        self.assertTrue(f["titulo_urgente"], "tampoco se parece al nombre del catálogo («Pantalla LED»)")
        igual = ff._fila("meli:Kubera", "ORG-0561-NEG", None, {"listing_id": "MLM8", "categoria": "MLM1"},
                         {**vivo, "titulo": "Funda protectora para ropa larga negra"},
                         {"id": 8, "name": "PROTECTOR DE ROPA MAS LARGA NEGRO"}, None, None, None,
                         "Organizador plegable para cajuela", None)
        self.assertNotIn("reciclado", igual["alertas"],
                         "ML y Odoo dicen lo mismo aunque el nombre de Omnicanal esté mal")
        self.assertIsNone(igual["imagen_mkt"], "la foto sólo viaja cuando no coinciden")

    def test_fotos_de_odoo_como_data_uri(self):
        def kw(modelo, metodo, args, kwargs=None):
            self.assertEqual((modelo, metodo), ("product.product", "read"))
            return [{"id": 1, "image_128": "iVBORw0KGgo"}, {"id": 2, "image_128": False}]
        with mock.patch.object(ff.odoo_ventas, "productos_por_sku",
                               lambda skus: {"A": {"id": 1}, "B": {"id": 2}}), \
             mock.patch.object(ff.odoo_ventas, "_kw", kw):
            r = ff.imagenes_odoo(["A", "B", "C"])
        self.assertEqual(r, {"A": "data:image/png;base64,iVBORw0KGgo", "B": None, "C": None})


class Excel(unittest.TestCase):
    def test_arma_un_xlsx_con_hoja_por_tienda(self):
        from io import BytesIO
        from openpyxl import load_workbook
        contenido = fx.armar({"semana": "S39", "tiendas": [
            {"tienda": "meli:Kubera", "nombre": "ML Kubera", "totales": {"renglones": 1},
             "renglones": [{"sku": "A", "nombre": "Á con acento", "a_mandar": 5}]}],
            "ganadores": [{"tienda": "ML Kubera", "sku": "B", "candidatos": [{"sku": "C", "tipo": "mismo modelo"}]}],
            "alertas": [{"tienda": "ML Kubera", "sku": "A", "tipo": "reciclado", "detalle": "x"}]})
        wb = load_workbook(BytesIO(contenido))
        self.assertEqual(wb.sheetnames, ["Resumen", "ML Kubera", "Ganadores agotados", "SKUs", "Alertas"])
        self.assertEqual(wb["ML Kubera"]["B2"].value, "Á con acento")
        self.assertEqual(wb["SKUs"]["B2"].value, "A")


class FichaSku(unittest.TestCase):
    def test_semanas_de_venta_seguidas_con_ceros(self):
        hoy = date(2026, 9, 24)
        filas = [{"cuenta": "BEKURA", "date": date(2026, 9, 22), "unidades": 3},
                 {"cuenta": "BEKURA", "date": date(2026, 9, 23), "unidades": 2},
                 {"cuenta": "BEKURA", "date": date(2026, 9, 9), "unidades": 4}]
        s = fs.semanas_de_venta(filas, hoy, n=3)
        self.assertEqual([(x["semana"], x["unidades"]) for x in s["Kubera"]], [("S37", 4), ("S38", 0), ("S39", 5)])

    def test_envios_del_sku(self):
        e1 = _envio(orden="S1", lineas=(("A", 10, 10, 10), ("B", 5, 5, 0)))
        e2 = _envio(orden="S2", dias=1, lineas=(("A", 3, 3, 0),))
        self.assertEqual([x["orden"] for x in fs.envios_del_sku("A", [e1, e2])], ["S2", "S1"])


if __name__ == "__main__":
    unittest.main()
