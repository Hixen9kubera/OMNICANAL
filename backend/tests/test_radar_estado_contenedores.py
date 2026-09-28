"""Radar de precios · F1 — informe por contenedor
(`scripts/radar_estado_contenedores.py`).

Sin base y sin red: las funciones puras con números a mano y `armar()` con un
juego de datos inventado. Ninguna cifra es un X, un pedimento ni un precio de
proveedor reales (el repo es público).

    cd backend && python -m unittest tests.test_radar_estado_contenedores -v
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import radar_estado_contenedores as R  # noqa: E402

D = dt.date


class DsnTest(unittest.TestCase):
    SANDBOX = "postgresql://postgres.yvootpbzaaaabbbbcccc:pw@pooler.example.invalid:6543/postgres"

    def test_sandbox_pasa(self):
        self.assertEqual(R.validar_dsn(self.SANDBOX), self.SANDBOX)

    def test_produccion_aborta_aunque_venga_en_cualquier_parte(self):
        with self.assertRaises(SystemExit) as e:
            R.validar_dsn(self.SANDBOX.replace(":pw@", ":xtukwcvsix@"))
        self.assertNotIn("pw", str(e.exception))
        with self.assertRaises(SystemExit):
            R.validar_dsn(self.SANDBOX.replace("yvootpbz", "tukwcvsi"))

    def test_otro_proyecto_o_vacia_aborta(self):
        with self.assertRaises(SystemExit):
            R.validar_dsn(self.SANDBOX.replace("yvootpbz", "zzzzzzzz"))
        with self.assertRaises(SystemExit):
            R.validar_dsn("")

    def test_puerto_de_sesion(self):
        self.assertIn(":5432/postgres", R.a_puerto_sesion(self.SANDBOX))
        self.assertEqual(R.a_puerto_sesion("postgresql://u:p@h:5432/db"), "postgresql://u:p@h:5432/db")


class AtribucionTest(unittest.TestCase):
    def test_uno_multi_y_sin_contenedor(self):
        a = R.atribuir([
            {"sku": "a-1", "numero": 7, "multi": False},
            {"sku": "M", "numero": 7, "multi": True},
            {"sku": "M", "numero": 9, "multi": True},
            {"sku": "S", "numero": 3, "multi": True},   # marcado multi aunque solo cargó una N
        ])
        self.assertEqual(a["A-1"], 7)
        self.assertEqual(a["M"], R.MULTI)
        self.assertEqual(a["S"], R.MULTI)
        self.assertEqual(R.cubeta_de("a-1", a), 7)
        self.assertEqual(R.cubeta_de("NADA", a), R.SIN_CONTENEDOR)
        self.assertEqual(R.cubeta_de(None, a), R.SIN_CONTENEDOR)


class ContribucionTest(unittest.TestCase):
    def test_comision_real_y_estimada(self):
        self.assertEqual(R.comision_de(116.0, 20.0, 0.3, 0.175), (20.0, "real"))
        self.assertEqual(R.comision_de(100.0, 0, 0.2, 0.175), (20.0, "estimado"))   # 0 = aún no llega
        self.assertAlmostEqual(R.comision_de(100.0, None, None, 0.175)[0], 17.5)

    def test_formula_y_envio_sin_dato(self):
        self.assertAlmostEqual(R.contribucion(116.0, 20.0, 50.0, 0.16), 30.0)
        self.assertAlmostEqual(R.contribucion(116.0, 20.0, None, 0.16), 80.0)   # sin dato no se resta

    def test_estado(self):
        self.assertEqual(R.estado_contribucion("real", "real"), "real")
        self.assertEqual(R.estado_contribucion("real", "estimado"), "parcial")
        self.assertEqual(R.estado_contribucion("estimado", "sin_dato"), "estimado")

    def test_envio_estimado_tabla_ml(self):
        # 1 kg, 10×10×10 (vol 0.2 kg) → peso efectivo 1 kg; $116 cae en el tramo $99–198.99.
        env, est = R.envio_estimado_ml({"peso": 1, "largo": 10, "ancho": 10, "alto": 10}, 1, 116.0)
        self.assertEqual((env, est), (38.0, "tabla"))
        env, est = R.envio_estimado_ml(None, 1, 58.0)                # 0.5 kg por defecto
        self.assertEqual((env, est), (28.5, "tabla_sin_medidas"))

    def test_peso_de_caja_master_usa_el_volumetrico(self):
        caja = {"peso": 871.318, "largo": 101, "ancho": 31, "alto": 18}   # 15 kg/L: imposible para una pieza
        self.assertTrue(R.peso_dudoso(caja, 1.5))
        self.assertFalse(R.peso_dudoso({"peso": 1, "largo": 10, "ancho": 10, "alto": 10}, 1.5))
        env, est = R.envio_estimado_ml(caja, 1, 1500.0, 1.5)
        # volumétrico 101×31×18/5000 = 11.27 kg → fila de 12 kg, tramo desde $999
        self.assertEqual((env, est), (161.5, "tabla_peso_dudoso"))

    def test_promedio_real_por_pieza(self):
        prom = R.promedio_envio_real([("P", 2, 30.0), ("P", 1, 12.0), ("Q", 1, 80.0)], 3)
        self.assertEqual(prom, {"P": 14.0})                           # Q: 1 pieza no alcanza

    def test_venta_sin_envio_toma_el_promedio_real_del_sku(self):
        base = {k: [] for k in ("medidas", "archivo")}
        linea = {"canal": "mercado_libre", "cuenta": "BEKURA", "linea": 1, "item_id": "MLM1",
                 "sku": "P", "precio_unitario": 116, "comision": 10}
        datos = {**base,
                 "ventas_vivo": [{**linea, "external_order_id": "r1", "cantidad": 3, "fecha": D(2026, 8, 1)},
                                 {**linea, "external_order_id": "e1", "cantidad": 2, "fecha": D(2026, 9, 1)}],
                 "envios": [{"cuenta": "BEKURA", "external_order_id": "r1", "costo": 30, "consultado": D(2026, 8, 2)}]}
        ventas = sorted(R.ventas_con_contribucion(datos), key=lambda x: x["fecha"])
        self.assertEqual([(x["envio"], x["envio_estado"]) for x in ventas], [(30.0, "real"), (20.0, "promedio_real")])


class RecuperadoTest(unittest.TestCase):
    SERIE = [(D(2026, 1, 5), 100.0), (D(2026, 9, 1), 50.0), (D(2026, 9, 20), 30.0)]

    def test_desde_liberacion_o_inicio(self):
        inicio = D(2025, 12, 27)
        self.assertEqual(R.desde_efectivo(None, inicio), (inicio, True))
        self.assertEqual(R.desde_efectivo(D(2025, 6, 1), inicio), (inicio, True))
        self.assertEqual(R.desde_efectivo(D(2026, 3, 1), inicio), (D(2026, 3, 1), False))

    def test_recuperado(self):
        self.assertEqual(R.recuperado(self.SERIE, D(2025, 12, 27)), 180.0)
        self.assertEqual(R.recuperado(self.SERIE, D(2026, 3, 1)), 80.0)
        self.assertEqual(R.recuperado(self.SERIE, D(2026, 3, 1), D(2026, 9, 10)), 50.0)

    def test_ritmo(self):
        self.assertAlmostEqual(R.ritmo_semanal(self.SERIE, D(2026, 9, 20), 4), 20.0)
        # Liberado hace 14 días: se divide entre 2 semanas, no entre 4.
        self.assertAlmostEqual(R.ritmo_semanal(self.SERIE, D(2026, 9, 20), 4, D(2026, 9, 7)), 15.0)

    def test_falta(self):
        self.assertIsNone(R.falta_de(None, 500.0))                   # sin X ≠ 0
        self.assertEqual(R.falta_de(1000.0, 400.0), 600.0)
        self.assertEqual(R.falta_de(1000.0, 1400.0), 0.0)

    def test_eta(self):
        self.assertEqual(R.eta_de(600.0, 100.0, D(2026, 9, 20)), (D(2026, 11, 1), 6.0))
        self.assertEqual(R.eta_de(None, 100.0, D(2026, 9, 20)), (None, None))
        self.assertEqual(R.eta_de(0.0, 100.0, D(2026, 9, 20)), (None, None))
        self.assertEqual(R.eta_de(600.0, 0.0, D(2026, 9, 20)), (None, None))

    def test_estado_contenedor(self):
        self.assertEqual(R.estado_contenedor(None, None, 10.0, None, 26), "sin X")
        self.assertEqual(R.estado_contenedor(1000.0, 0.0, 10.0, None, 26), "recuperado")
        self.assertEqual(R.estado_contenedor(1000.0, 600.0, 0.0, None, 26), "sin ritmo")
        self.assertEqual(R.estado_contenedor(1000.0, 600.0, 10.0, 60.0, 26), "lento")
        self.assertEqual(R.estado_contenedor(1000.0, 600.0, 100.0, 6.0, 26), "en curso")


class ClaseTest(unittest.TestCase):
    def test_cobertura_y_clase(self):
        self.assertIsNone(R.cobertura_dias(10, 0))
        self.assertEqual(R.cobertura_dias(90, 0.5), 180.0)
        self.assertEqual(R.clasificar("A", 10, 0, None, 180), "exceso")        # stock sin ventas
        self.assertEqual(R.clasificar("A", 10, 3, 300.0, 180), "exceso")
        self.assertEqual(R.clasificar("A", 10, 45, 20.0, 180), "normal")
        self.assertEqual(R.clasificar("A", 0, 0, None, 180), "normal")         # sin stock no es exceso


class ArchivoXTest(unittest.TestCase):
    def test_plantilla_no_sobrescribe(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "x_contenedores.csv"
            self.assertTrue(R.crear_plantilla_x(ruta, [9, 3, 3]))
            self.assertEqual(ruta.read_text(encoding="utf-8-sig").splitlines(),
                             [",".join(R.COLUMNAS_X), "3,,,,", "9,,,,"])
            ruta.write_text("contenedor,x_total_mxn,fecha_liberacion,fuente,capturado_por\n"
                            "3,\"1,000.50\",2026-03-01,papel,prueba\n9,,,,\n", encoding="utf-8")
            self.assertFalse(R.crear_plantilla_x(ruta, [1, 2]))
            x = R.leer_x(ruta)
            self.assertEqual(x[3]["x"], 1000.5)
            self.assertEqual(x[3]["fecha_liberacion"], D(2026, 3, 1))
            self.assertIsNone(x[9]["x"])                              # vacío = sin dato

    def test_monto_invalido(self):
        self.assertIsNone(R.parsear_monto("  "))
        self.assertEqual(R.parsear_monto("$1,234"), 1234.0)
        with self.assertRaises(ValueError):
            R.parsear_monto("0")

    def test_salida_dentro_del_repo_aborta_antes_de_leer(self):
        with self.assertRaises(SystemExit) as e:
            R.main(["--salida", str(R.ROOT / "backend" / "_no_aqui")])
        self.assertIn("dentro del repo", str(e.exception))
        self.assertFalse((R.ROOT / "backend" / "_no_aqui").exists())


def _datos() -> dict:
    """A y B en el 1; C en el 2; M en el 1 y el 2 (multi); Z sin contenedor."""
    return {
        "sku_contenedor": [
            {"sku": "A", "numero": 1, "codigo": "AAAU0000001", "nivel": "A", "multi": False},
            {"sku": "B", "numero": 1, "codigo": "AAAU0000001", "nivel": "A", "multi": False},
            {"sku": "C", "numero": 2, "codigo": "BBBU0000002", "nivel": "B", "multi": False},
            {"sku": "M", "numero": 1, "codigo": "AAAU0000001", "nivel": "A", "multi": True},
            {"sku": "M", "numero": 2, "codigo": "BBBU0000002", "nivel": "B", "multi": True},
        ],
        "productos": [{"sku": s, "name": f"Producto {s}", "wc_id": i, "wc_parent_id": None}
                      for i, s in enumerate("ABCMZ", start=1)],
        "medidas": [{"sku": "A", "peso": 1, "largo": 10, "ancho": 10, "alto": 10}],
        "ventas_vivo": [
            # Pedido o1 (ML) con dos líneas: el envío real 50 se reparte 116/464 y 348/464.
            {"canal": "mercado_libre", "cuenta": "BEKURA", "external_order_id": "o1", "linea": 1,
             "item_id": "MLM1", "sku": "A", "cantidad": 1, "precio_unitario": 116, "comision": 20,
             "fecha": D(2026, 9, 10)},
            {"canal": "mercado_libre", "cuenta": "BEKURA", "external_order_id": "o1", "linea": 2,
             "item_id": "MLM3", "sku": "C", "cantidad": 3, "precio_unitario": 116, "comision": 60,
             "fecha": D(2026, 9, 10)},
            # TikTok: comisión 0 → estimada; envío sin dato.
            {"canal": "tiktok", "cuenta": "TIKTOK", "external_order_id": "t1", "linea": 1,
             "item_id": "TT1", "sku": "B", "cantidad": 1, "precio_unitario": 116, "comision": 0,
             "fecha": D(2026, 9, 15)},
            # Multi sin envío real ni medidas → 0.5 kg por defecto.
            {"canal": "mercado_libre", "cuenta": "SANCORFASHION", "external_order_id": "o2", "linea": 1,
             "item_id": "MLM4", "sku": "M", "cantidad": 1, "precio_unitario": 58, "comision": 10,
             "fecha": D(2026, 9, 20)},
            {"canal": "mercado_libre", "cuenta": "BEKURA", "external_order_id": "o9", "linea": 1,
             "item_id": "MLM9", "sku": "Z", "cantidad": 1, "precio_unitario": 116, "comision": 10,
             "fecha": D(2026, 9, 1)},
        ],
        "vivo_sin_sku": [{"lineas": 0, "piezas": 0}],
        "envios": [{"cuenta": "BEKURA", "external_order_id": "o1", "costo": 50, "consultado": D(2026, 9, 11)},
                   {"cuenta": "BEKURA", "external_order_id": "o9", "costo": 6, "consultado": D(2026, 9, 2)}],
        # Archivo: 2 piezas de A, envío estimado 38 c/u.
        "archivo": [{"fecha": D(2026, 7, 1), "cuenta": "BEKURA", "item_id": "MLM1", "sku": "A",
                     "units_sold": 2, "revenue": 232, "sale_fee": 40}],
        "listings": [
            {"sku": "A", "canal": "general", "cuenta": "GENERAL", "listing_id": "1", "status": None,
             "situacion": None, "stock_own": 10, "stock_full": None, "stock_fba": None,
             "is_fulfillment": False, "logistic_type": None, "price": None, "price_sale": None},
            {"sku": "A", "canal": "mercado_libre", "cuenta": "BEKURA", "listing_id": "MLM1", "status": "published",
             "situacion": "active", "stock_own": 99, "stock_full": 0, "stock_fba": None,
             "is_fulfillment": True, "logistic_type": "fulfillment", "price": 116, "price_sale": 116},
        ],
        "publicaciones": [
            {"sku": "A", "cuenta": "BEKURA", "ml_item_id": "MLM1", "estado": "active", "precio": 116,
             "visitas_30d": 900, "unidades_30d": 50, "periodo": D(2026, 8, 1)},     # foto vieja
            {"sku": "A", "cuenta": "BEKURA", "ml_item_id": "MLM1", "estado": "active", "precio": 116,
             "visitas_30d": 1000, "unidades_30d": 2, "periodo": D(2026, 9, 1)},
        ],
    }


class ArmarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        x = {1: {"x": 1000.0, "fecha_liberacion": None, "fuente": "prueba", "capturado_por": "t"}}
        cls.r = R.armar(_datos(), x, dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc))
        cls.fila = {f["contenedor"]: f for f in cls.r["contenedores"]}

    def test_corte_e_inicio(self):
        self.assertEqual(self.r["corte"], D(2026, 9, 20))
        self.assertEqual(self.r["inicio"], D(2026, 7, 1))

    def test_contenedor_con_x(self):
        c1 = self.fila[1]
        # A vivo 100−20−12.5 = 67.5 · A archivo 200−40−76 = 84 · B TikTok 100−20.3 = 79.7
        self.assertAlmostEqual(c1["recuperado"], 231.2)
        self.assertAlmostEqual(c1["recuperado_archivo"], 84.0)
        self.assertAlmostEqual(c1["falta"], 768.8)
        self.assertAlmostEqual(c1["ritmo"], (67.5 + 79.7) / 4)
        self.assertEqual(c1["estado"], "en curso")
        self.assertEqual(c1["eta"], D(2027, 2, 14))
        self.assertEqual((c1["skus"], c1["skus_multi"]), (3, 1))
        self.assertTrue(c1["truncada"])
        self.assertAlmostEqual(c1["multi_compartida"], 11.5)          # M: 50−10−28.5, NO sumada
        self.assertEqual(c1["stock"], 10)                              # Woo manda sobre el espejo (99)
        self.assertAlmostEqual(c1["pct_envio_real"], 1 / 4)            # 1 pieza real de 4

    def test_contenedor_sin_x(self):
        c2 = self.fila[2]
        self.assertAlmostEqual(c2["recuperado"], 300 - 60 - 37.5)
        self.assertIsNone(c2["x"])
        self.assertIsNone(c2["falta"])
        self.assertIsNone(c2["eta"])
        self.assertEqual(c2["estado"], "sin X")
        self.assertEqual(c2["alcanza"], "sin X")

    def test_cubetas(self):
        self.assertAlmostEqual(self.fila[R.MULTI]["recuperado"], 11.5)
        self.assertEqual(self.fila[R.MULTI]["skus"], 1)
        self.assertAlmostEqual(self.fila[R.SIN_CONTENEDOR]["recuperado"], 84.0)
        total = sum(f["recuperado"] for f in self.r["contenedores"])
        self.assertAlmostEqual(total, 231.2 + 202.5 + 11.5 + 84.0)    # nada se pierde ni se duplica

    def test_frenan_y_palancas(self):
        frena = {f["sku"]: f for f in self.r["frenan"]}
        self.assertEqual(set(frena), {"A"})                            # 10 pzs a 3/90 por día = 300 d
        self.assertEqual(frena["A"]["clase"], "exceso")
        pal = {(p["sku"], p["palanca"]) for p in self.r["palancas"]}
        self.assertIn(("A", "sin publicación activa en otro canal"), pal)
        self.assertIn(("A", "muchas visitas, poca conversión"), pal)   # la foto más nueva: 2/1000
        self.assertNotIn(("A", "rotación alta sin Full"), pal)
        vis = next(p for p in self.r["palancas"] if p["palanca"] == "muchas visitas, poca conversión")
        self.assertEqual(vis["visitas"], 1000)


if __name__ == "__main__":
    unittest.main()
