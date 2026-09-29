"""Pruebas del Radar de precios · F1 (services/radar_precios.py).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Contribución**: con datos reales, estimada, parcial; sin peso ni envío real
   NO se inventa (valor None); devolución sin tasa es None, no 0.
2. **Piso por clase** (bisección): exceso < normal < recompra; la exigencia se
   cumple en el piso; con el envío ESCALONADO se cumple en todo precio ≥ piso;
   sin envío → None.
3. **Clase**: exceso (0 ventas con stock, o cobertura larga), recompra, normal.
4. **Referencia**: sin término, pocos rivales, captura vieja, los nuestros no
   cuentan, mediana.
5. **Dirección**: cada una de las seis, cobertura corta, exceso sin premio.
6. **Redondeo** a terminación 9, nunca bajo el piso.
7. **Filtrar** (pura): conteos sin el filtro de dirección, página.
8. **Acceso**: la regla RBAC es admin y ninguna otra la tapa; el router solo
   declara GET y rechaza sin sesión (401) o sin rol admin (403).
9. **Stock**: la consulta es la forma rápida de v0.591.0 (JOIN contra la
   lista, sin subconsulta correlacionada ni `any()`).
10. **Piloto**: con SKUs de piloto el listado, los conteos y los contenedores
   se recortan a ellos; `todos` (y `?todos=1`/`?todos=true`) lo quita; sin
   costing.sku_contenedor el contenedor sale de PILOTO y, con la tabla, manda
   la tabla.

Sin red: nada aquí habla con kubera ni con ningún marketplace.

    cd backend && python -m unittest tests.test_radar_precios -v
    cd backend && python -m pytest tests/test_radar_precios.py -q   # si hay pytest

Escritas con unittest (como el resto de tests/): el venv del backend no trae
pytest, y así corren con los dos.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import costos  # noqa: E402
from services import radar_precios as rp  # noqa: E402

AHORA = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)


def _res(precios, dias=1, nuestro=False):
    return [{"precio": p, "capturado_en": AHORA - timedelta(days=dias),
             "es_nuestro": nuestro} for p in precios]


def _item(sku, direccion, cuenta="BEKURA", clase="normal", cont=None, ref=None):
    contrib = {"valor": 10.0, "estado": "real",
               "desglose": {"comision_estado": "real", "envio_estado": "estimado"}}
    return {"sku": sku, "titulo": f"titulo {sku}", "contenedor": cont,
            "contenedor_multi": False, "clase": clase, "cuenta_principal": cuenta,
            "cuentas": [{"cuenta": cuenta, "contribucion": contrib}],
            "referencia": {"precio": ref}, "direccion": direccion,
            "_categoria": "MLM1"}


class Contribucion(unittest.TestCase):
    def test_real(self):
        c = rp.contribucion_de(1160, comision_tasa=0.18, envio_real=100,
                               devolucion_tasa_pct=5)
        d = c["desglose"]
        self.assertAlmostEqual(d["precio_sin_iva"], 1000.0, places=2)
        self.assertAlmostEqual(d["comision"], 208.8, places=2)
        self.assertEqual((d["envio"], d["envio_estado"]), (100, "real"))
        self.assertAlmostEqual(d["devolucion"], 5.0, places=2)
        self.assertIsNone(d["full"])
        self.assertIsNone(d["publicidad"])
        self.assertAlmostEqual(c["valor"], 1000 - 208.8 - 100 - 5, places=2)
        self.assertEqual(c["estado"], "real")

    def test_estimada_usa_tabla_de_envio(self):
        c = rp.contribucion_de(1160, peso_kg=1.0)
        d = c["desglose"]
        self.assertEqual(d["comision_estado"], "estimado")
        self.assertAlmostEqual(d["comision"], 1160 * rp.PARAMS["comision_estimada"], places=2)
        self.assertEqual(d["envio_estado"], "estimado")
        self.assertEqual(d["envio"], costos.calc_fee_envio_ml(1.0, 1160))
        self.assertEqual(c["estado"], "estimado")
        self.assertAlmostEqual(c["valor"], 1000 - 1160 * 0.175 - d["envio"], places=2)

    def test_parcial(self):
        self.assertEqual(rp.contribucion_de(1160, comision_tasa=0.18, peso_kg=1.0)["estado"],
                         "parcial")
        self.assertEqual(rp.contribucion_de(1160, envio_real=80)["estado"], "parcial")

    def test_sin_envio_no_se_inventa(self):
        c = rp.contribucion_de(1160, comision_tasa=0.18)
        self.assertIsNone(c["valor"])
        self.assertEqual(c["desglose"]["envio_estado"], "sin_dato")
        self.assertIsNone(c["desglose"]["envio"])

    def test_devolucion_sin_tasa_es_none_no_cero(self):
        c = rp.contribucion_de(1160, comision_tasa=0.18, envio_real=100)
        self.assertIsNone(c["desglose"]["devolucion"])
        self.assertAlmostEqual(c["valor"], 1000 - 208.8 - 100, places=2)

    def test_envio_real_cero_es_dato(self):
        c = rp.contribucion_de(250, comision_tasa=0.18, envio_real=0)
        self.assertEqual(c["desglose"]["envio_estado"], "real")
        self.assertAlmostEqual(c["valor"], 250 / 1.16 - 45, places=2)


class Piso(unittest.TestCase):
    def test_por_clase_lineal(self):
        kw = {"comision_tasa": 0.18, "envio_real": 100}
        coef = 1 / 1.16 - 0.18
        exceso = rp.piso_por_clase("exceso", **kw)
        normal = rp.piso_por_clase("normal", **kw)
        recompra = rp.piso_por_clase("recompra", **kw)
        self.assertAlmostEqual(exceso, 100 / coef, delta=0.02)
        self.assertAlmostEqual(normal, 100 / (coef - 0.10), delta=0.02)
        self.assertAlmostEqual(recompra, 100 / (coef - 0.20), delta=0.02)
        self.assertTrue(exceso < normal < recompra)
        for clase, piso, req in (("exceso", exceso, 0.0), ("normal", normal, 0.10),
                                 ("recompra", recompra, 0.20)):
            with self.subTest(clase=clase):
                v = rp.contribucion_de(piso, **kw)["valor"]
                self.assertGreaterEqual(v, req * piso - 0.01)

    def test_envio_escalonado_cumple_de_ahi_para_arriba(self):
        kw = {"peso_kg": 3.0}
        piso = rp.piso_por_clase("normal", **kw)
        self.assertIsNotNone(piso)
        p = piso
        while p < 2500:
            v = rp.contribucion_de(p, **kw)["valor"]
            self.assertGreaterEqual(v, 0.10 * p - 0.01, msg=f"precio {p}")
            p += 0.5
        # Y justo abajo del piso ya no cumple (es el MÁS bajo).
        self.assertLess(rp.contribucion_de(piso - 0.05, **kw)["valor"],
                        0.10 * (piso - 0.05))

    def test_sin_envio_es_none(self):
        self.assertIsNone(rp.piso_por_clase("normal", comision_tasa=0.18))


class Clase(unittest.TestCase):
    def test_clasificar(self):
        self.assertEqual(rp.clasificar("A", unidades_90d=0, stock=5, cobertura_dias=None), "exceso")
        self.assertEqual(rp.clasificar("A", unidades_90d=10, stock=100, cobertura_dias=900), "exceso")
        self.assertEqual(rp.clasificar("A", unidades_90d=90, stock=30, cobertura_dias=30), "normal")
        self.assertEqual(rp.clasificar("a", unidades_90d=90, stock=30, cobertura_dias=30,
                                       recompra={"A"}), "recompra")
        # Sin stock y sin ventas no es exceso: no hay piezas que mover.
        self.assertEqual(rp.clasificar("A", unidades_90d=0, stock=0, cobertura_dias=0), "normal")
        self.assertEqual(rp.RECOMPRA, set())


class Ventas(unittest.TestCase):
    def test_asignar_por_item_y_por_sku_sin_contar_doble(self):
        filas = [
            # la venta entra con el seller_sku de la orden, no el de la publicación
            {"sku": "SIL-008-NEG", "item_id": "MLM1", "unidades": 59},
            {"sku": "SIL-0008-NEG", "item_id": "MLM1", "unidades": 1},
            # otro canal: solo por SKU
            {"sku": "sil-0008-neg", "item_id": "AMZ9", "unidades": 2},
            # ni item ni SKU nuestros: no cuenta para nadie
            {"sku": "OTRO", "item_id": "MLM7", "unidades": 5},
        ]
        u = rp.asignar_ventas(filas, {"MLM1": "SIL-0008-NEG"}, {"SIL-0008-NEG"})
        self.assertEqual(u, {"SIL-0008-NEG": 62.0})

    def test_depurar_padres_quita_solo_la_fila_del_padre_en_ese_item(self):
        pubs = [{"sku": "DEC-0014", "item_id": "MLM9"},
                {"sku": "DEC-0014-BLN", "item_id": "MLM9"},
                {"sku": "DEC-0014", "item_id": "MLM5"},       # publicación propia: sigue
                {"sku": "ABC-1", "item_id": "MLM7"},
                {"sku": "XYZ-2", "item_id": "MLM7"}]          # dos SKUs sin prefijo: siguen
        filas, fuera = rp.depurar_padres(pubs)
        self.assertEqual(fuera, 1)
        self.assertNotIn({"sku": "DEC-0014", "item_id": "MLM9"}, filas)
        self.assertEqual(len(filas), 4)


class Referencia(unittest.TestCase):
    def test_mediana_excluye_nuestros(self):
        filas = _res([100, 120, 140, 160]) + _res([10], nuestro=True)
        r = rp.referencia_de(filas, termino="lampara", ahora=AHORA)
        self.assertAlmostEqual(r["precio"], 130.0)
        self.assertEqual((r["n"], r["fuente"], r["motivo"]), (4, "busqueda", None))

    def test_sin_termino(self):
        r = rp.referencia_de(_res([1, 2, 3]), termino=None, ahora=AHORA)
        self.assertIsNone(r["precio"])
        self.assertEqual(r["motivo"], "sin_termino")

    def test_pocos_rivales(self):
        r = rp.referencia_de(_res([100, 110]) + _res([1, 2], nuestro=True),
                             termino="x", ahora=AHORA)
        self.assertIsNone(r["precio"])
        self.assertEqual(r["motivo"], "pocos_rivales")

    def test_captura_vieja(self):
        r = rp.referencia_de(_res([100, 110, 120], dias=60), termino="x", ahora=AHORA)
        self.assertIsNone(r["precio"])
        self.assertEqual((r["motivo"], r["n_total"]), ("captura_vieja", 3))

    def test_rivales_dispersos_no_dan_referencia(self):
        # El caso real del 28-sep: la búsqueda mezclaba productos de $160 a $22,500.
        r = rp.referencia_de(_res([160, 298, 339, 635, 735, 1172, 3065, 22500]),
                             termino="x", ahora=AHORA)
        self.assertIsNone(r["precio"])
        self.assertEqual(r["motivo"], "rivales_dispersos")
        self.assertGreater(r["mediana_dudosa"], 0)

    def test_dispersion_moderada_si_da_referencia(self):
        r = rp.referencia_de(_res([900, 1000, 1100, 1200, 1400]), termino="x", ahora=AHORA)
        self.assertEqual((r["precio"], r["motivo"]), (1100.0, None))


class Direccion(unittest.TestCase):
    def test_brecha_enorme_no_sugiere_precio(self):
        d = rp.direccion_de(precio=2797, referencia=732, premio=0.0, piso=50,
                            clase="exceso", cobertura_dias=None, n_rivales=10)
        self.assertEqual(d["direccion"], "sin_referencia")
        self.assertIsNone(d["precio_sugerido"])
        self.assertAlmostEqual(d["posicion_pct"], 282.1)
        self.assertIn("revisar el término", d["razones"][-1])

    def test_brecha_abajo_tambien_cuenta(self):
        d = rp.direccion_de(precio=300, referencia=1000, piso=100, clase="normal",
                            cobertura_dias=100)
        self.assertEqual(d["direccion"], "sin_referencia")

    def test_sin_referencia(self):
        d = rp.direccion_de(precio=100, referencia=None, motivo_sin_ref="captura_vieja")
        self.assertEqual(d["direccion"], "sin_referencia")
        self.assertIsNone(d["precio_sugerido"])
        self.assertIn("45 días", d["razones"][0])

    def test_subir_topada_por_paso(self):
        d = rp.direccion_de(precio=900, referencia=1000, premio=0.0, piso=500,
                            clase="normal", cobertura_dias=100, n_rivales=7)
        self.assertEqual(d["direccion"], "subir")
        self.assertEqual(d["precio_sugerido"], 989)   # min(1000, 990) → …9
        self.assertAlmostEqual(d["posicion_pct"], -10.0)
        self.assertIn("7 rivales", d["razones"][0])

    def test_bajar(self):
        d = rp.direccion_de(precio=1200, referencia=1000, premio=0.0, piso=800,
                            clase="normal", cobertura_dias=100)
        self.assertEqual(d["direccion"], "bajar")
        self.assertEqual(d["precio_sugerido"], 1079)  # max(1000, 1080, 800) → …9
        self.assertAlmostEqual(d["techo"], 1000)

    def test_bajar_nunca_bajo_el_piso(self):
        d = rp.direccion_de(precio=1100, referencia=1000, premio=0.0, piso=999.5,
                            clase="normal", cobertura_dias=100)
        self.assertEqual(d["direccion"], "bajar")
        self.assertGreaterEqual(d["precio_sugerido"], 999.5)
        self.assertEqual(d["precio_sugerido"] % 10, 9)

    def test_no_competir(self):
        d = rp.direccion_de(precio=1300, referencia=1000, premio=0.0, piso=1100,
                            clase="normal", cobertura_dias=100)
        self.assertEqual(d["direccion"], "no_competir")
        self.assertTrue(any("piso" in r for r in d["razones"]))

    def test_mantener_por_cobertura_corta(self):
        d = rp.direccion_de(precio=1200, referencia=1000, premio=0.0, piso=800,
                            clase="normal", cobertura_dias=20)
        self.assertEqual(d["direccion"], "mantener")
        self.assertTrue(any("cobertura corta" in r for r in d["razones"]))

    def test_caro_justificado(self):
        d = rp.direccion_de(precio=1080, referencia=1000, premio=0.10, piso=800,
                            clase="normal", cobertura_dias=100)
        self.assertEqual(d["direccion"], "caro_justificado")
        self.assertAlmostEqual(d["techo"], 1100)

    def test_exceso_sin_premio(self):
        premio = rp.premio_calidad(full=True, experiencia="verde", clase="exceso")
        self.assertEqual(premio, 0.0)
        d = rp.direccion_de(precio=1080, referencia=1000, premio=premio, piso=500,
                            clase="exceso", cobertura_dias=400)
        self.assertEqual(d["direccion"], "bajar")     # sin premio no hay «justificado»

    def test_exceso_abajo_del_mercado_mantiene(self):
        d = rp.direccion_de(precio=800, referencia=1000, premio=0.0, piso=500,
                            clase="exceso", cobertura_dias=400)
        self.assertEqual(d["direccion"], "mantener")

    def test_mantener_en_banda(self):
        d = rp.direccion_de(precio=1030, referencia=1000, premio=0.0, piso=500,
                            clase="normal", cobertura_dias=100)
        self.assertEqual(d["direccion"], "mantener")
        self.assertIsNone(d["precio_sugerido"])

    def test_premio_calidad_y_tope(self):
        self.assertAlmostEqual(rp.premio_calidad(full=True, experiencia="verde",
                                                 clase="normal"), 0.08)
        self.assertEqual(rp.premio_calidad(full=False, experiencia="amarilla",
                                           clase="normal"), 0.0)
        tope = rp.premio_calidad(full=True, experiencia="verde", clase="normal",
                                 params={"premio_full": 0.08,
                                         "premio_experiencia_verde": 0.05})
        self.assertAlmostEqual(tope, 0.10)


class Redondeo(unittest.TestCase):
    def test_terminacion_9(self):
        for entra, sale in ((1166, 1169), (1163, 1159), (1161.2, 1159), (1165, 1169), (1169, 1169),
                            (1170, 1169), (55, 59), (3, 9)):
            with self.subTest(entra=entra):
                self.assertEqual(rp.redondear_precio(entra), sale)

    def test_nunca_bajo_el_piso(self):
        self.assertEqual(rp.redondear_precio(1161, piso=1165), 1169)
        self.assertEqual(rp.redondear_precio(1161, piso=1170), 1179)
        self.assertIsNone(rp.redondear_precio(None))


# Sin piloto: estas pruebas miden el filtrado sobre el universo entero.
@mock.patch.dict(rp.PILOTO, {}, clear=True)
class Filtrar(unittest.TestCase):
    def test_conteos_sin_filtro_de_direccion_y_pagina(self):
        uni = {"generado_en": "x", "tablas": {}, "items": [
            _item("A", "subir", ref=100), _item("B", "bajar", ref=100),
            _item("C", "subir", cuenta="SANCORFASHION", cont="94"),
            _item("D", "mantener", clase="exceso", cont="80")]}
        r = rp.filtrar(uni, direccion="subir", limite=1, pagina=1)
        self.assertEqual((r["conteos"]["subir"], r["conteos"]["bajar"]), (2, 1))
        self.assertEqual((r["total"], len(r["items"])), (2, 1))
        self.assertNotIn("_categoria", r["items"][0])
        self.assertEqual(r["contenedores"], ["80", "94"])
        self.assertEqual(r["completitud"]["comision_real_pct"], 100.0)
        self.assertEqual(r["completitud"]["devolucion"], "sin_dato")
        r2 = rp.filtrar(uni, cuenta="sancorfashion")
        self.assertEqual([i["sku"] for i in r2["items"]], ["C"])
        self.assertEqual(rp.filtrar(uni, contenedor="C-94")["total"], 1)
        self.assertEqual(rp.filtrar(uni, q="titulo d")["items"][0]["sku"], "D")


class Acceso(unittest.TestCase):
    def test_regla_rbac_admin_y_sin_prefijo_que_la_tape(self):
        from core import rbac
        self.assertIn(("GET", "/api/radar-precios", "admin"), rbac.REGLAS)
        self.assertEqual(rbac.rol_requerido("GET", "/api/radar-precios"), "admin")
        self.assertEqual(rbac.rol_requerido("GET", "/api/radar-precios/HOG-0412-LAM"), "admin")
        tapan = [r for r in rbac.REGLAS
                 if r[0] == "GET" and "/api/radar-precios".startswith(r[1])
                 and r[1] != "/api/radar-precios"]
        self.assertEqual(tapan, [])

    def test_router_solo_get_y_exige_admin_con_sesion(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import radar_precios as ruta

        metodos = {m for r in ruta.router.routes for m in r.methods}
        self.assertTrue(metodos <= {"GET", "HEAD"}, metodos)

        app = FastAPI()
        app.include_router(ruta.router)
        cli = TestClient(app)
        anonimo = SimpleNamespace(autenticado=False, tipo="", rol="", actor="?")
        operador = SimpleNamespace(autenticado=True, tipo="persona", rol="operador",
                                   actor="kam@x")
        maquina = SimpleNamespace(autenticado=True, tipo="maquina", rol="admin",
                                  actor="llave")
        with mock.patch("routers.investigacion.core_identidad.resolver",
                        new=mock.AsyncMock(return_value=anonimo)), \
             mock.patch.object(rp, "construir_universo") as cu:
            self.assertEqual(cli.get("/api/radar-precios").status_code, 401)
            self.assertEqual(cli.get("/api/radar-precios/ABC").status_code, 401)
            cu.assert_not_called()
        for quien in (operador, maquina):
            with self.subTest(tipo=quien.tipo, rol=quien.rol), \
                 mock.patch("routers.investigacion.core_identidad.resolver",
                            new=mock.AsyncMock(return_value=quien)), \
                 mock.patch.object(rp, "construir_universo") as cu:
                self.assertEqual(cli.get("/api/radar-precios").status_code, 403)
                cu.assert_not_called()


class Promocion(unittest.TestCase):
    def test_parametro_existe_y_es_decision(self):
        # El candado de promoción vive en _armar_item (necesita precio de ficha y
        # cobrado); aquí se fija el umbral para que no cambie sin querer.
        self.assertEqual(rp.PARAMS["promocion_min"], 0.05)
        self.assertEqual(rp.DIRECCION_TEXTO["subir"], "subir")


class StockSQL(unittest.TestCase):
    def test_forma_rapida_join_contra_la_lista(self):
        import re
        sql = " ".join(rp._SQL_STOCK.split()).lower()
        # La lista entra UNA vez, como CTE, y se junta con channel.listings.
        self.assertEqual(rp._SQL_STOCK.count("%(skus)s"), 1)
        self.assertIn("with s(sku) as (select distinct unnest(%(skus)s::citext[]))", sql)
        self.assertIn("join s on l.sku = s.sku", sql)
        # Ni subconsulta escalar en el SELECT (la correlacionada de antes) ni any().
        self.assertIsNone(re.search(r",\s*\(\s*select", sql), sql)
        self.assertNotIn("any(", sql)
        self.assertNotIn("where f.sku = l.sku", sql)
        # La salida no cambia de nombre: sku, propio, full_.
        for col in ("as sku", "as propio", "as full_"):
            self.assertIn(col, sql)


def _fake_sdb(*, con_tabla_cont: bool, cont_filas=()):
    """Un supabase_db de mentira para construir_universo: contesta por consulta
    y anota cuáles se hicieron. Sin red."""
    llamadas: list[tuple[str, object]] = []
    pubs = [
        {"sku": "AAA-0001-NEG", "cuenta": "BEKURA", "item_id": "MLM1", "price": 500,
         "price_sale": 500, "price_base": 500, "is_fulfillment": True,
         "logistic_type": "fulfillment", "stock_own": 10, "stock_full": 5,
         "category_id": "MLM9"},
        {"sku": "BBB-0002-BLN", "cuenta": "SANCORFASHION", "item_id": "MLM2", "price": 800,
         "price_sale": 800, "price_base": 800, "is_fulfillment": False,
         "logistic_type": "cross_docking", "stock_own": 3, "stock_full": 0,
         "category_id": "MLM9"},
    ]

    def fetch_all(sql, params=None):
        llamadas.append((sql, params))
        if sql.lstrip().startswith("select l.sku::text as sku, a.legacy_code"):
            return pubs
        if sql is rp._SQL_STOCK:
            return [{"sku": "AAA-0001-NEG", "propio": 10, "full_": 5},
                    {"sku": "BBB-0002-BLN", "propio": 3, "full_": 0}]
        if sql is rp._SQL_PESO:
            return [{"sku": "AAA-0001-NEG", "peso": 1.0, "largo": 10, "ancho": 10, "alto": 10}]
        if sql is rp._SQL_CONT:
            return list(cont_filas)
        return []

    def fetch_scalar(sql, params=None):
        return bool(con_tabla_cont) if params == ("costing.sku_contenedor",) else False

    return SimpleNamespace(fetch_all=fetch_all, fetch_scalar=fetch_scalar,
                           fetch_one=lambda *a, **k: None), llamadas


class Piloto(unittest.TestCase):
    UNI = {"generado_en": "x", "tablas": {}, "items": [
        _item("A", "subir", ref=100, cont="94"), _item("B", "bajar", ref=100, cont="80"),
        _item("C", "subir", cuenta="SANCORFASHION", cont="94"),
        _item("D", "mantener", clase="exceso", cont="77")]}

    def test_modulo_trae_dict_y_vacio_no_recorta(self):
        self.assertIsInstance(rp.PILOTO, dict)
        with mock.patch.dict(rp.PILOTO, {}, clear=True):
            r = rp.filtrar(self.UNI)
        self.assertEqual(r["total"], 4)
        self.assertEqual(r["piloto"], {"activo": False, "skus": [], "n": 0,
                                       "total_universo": 4, "faltan": []})

    def test_por_defecto_recorta_lista_conteos_y_contenedores(self):
        with mock.patch.dict(rp.PILOTO, {"b": 80, " D ": None, "ZZZ-9": 12}, clear=True):
            r = rp.filtrar(self.UNI)
        self.assertEqual(sorted(i["sku"] for i in r["items"]), ["B", "D"])
        self.assertEqual(r["total"], 2)
        self.assertEqual((r["conteos"]["subir"], r["conteos"]["bajar"],
                          r["conteos"]["mantener"]), (0, 1, 1))
        self.assertEqual(r["contenedores"], ["77", "80"])
        self.assertEqual(r["piloto"], {"activo": True, "skus": ["B", "D", "ZZZ-9"],
                                       "n": 3, "total_universo": 4, "faltan": ["ZZZ-9"]})
        # Los demás filtros siguen funcionando DENTRO del piloto.
        with mock.patch.dict(rp.PILOTO, {"B": 80, "D": None}, clear=True):
            self.assertEqual(rp.filtrar(self.UNI, direccion="subir")["total"], 0)
            self.assertEqual(rp.filtrar(self.UNI, clase="exceso")["total"], 1)

    def test_todos_quita_el_recorte(self):
        with mock.patch.dict(rp.PILOTO, {"B": 80, "D": None}, clear=True):
            r = rp.filtrar(self.UNI, todos=True)
        self.assertEqual(r["total"], 4)
        self.assertEqual((r["conteos"]["subir"], r["conteos"]["bajar"]), (2, 1))
        self.assertEqual(r["contenedores"], ["77", "80", "94"])
        self.assertEqual(r["piloto"]["activo"], False)
        self.assertEqual(r["piloto"]["skus"], ["B", "D"])     # sigue diciendo cuál es

    def test_piloto_explicito_manda_sobre_el_modulo(self):
        with mock.patch.dict(rp.PILOTO, {"A": None}, clear=True):
            r = rp.filtrar(self.UNI, piloto=["c"])
        self.assertEqual([i["sku"] for i in r["items"]], ["C"])

    def test_helpers(self):
        self.assertEqual(rp.skus_piloto({"a": 1, "A": 2, "": 3, "b": None}), ["A", "B"])
        self.assertEqual(rp.contenedores_piloto({"x-1": 94, "Y": None}),
                         {"X-1": {"numero": 94, "n": 1, "multi": False, "fuente": "piloto"}})
        # «C-80» se entiende; basura no tumba el radar (ese SKU queda sin contenedor).
        with self.assertLogs("omnicanal.radar_precios", level="WARNING"):
            self.assertEqual(rp.contenedores_piloto({"A": "C-80", "B": "ochenta"}),
                             {"A": {"numero": 80, "n": 1, "multi": False, "fuente": "piloto"}})

    def test_contenedor_desde_piloto_sin_tabla(self):
        fake, llamadas = _fake_sdb(con_tabla_cont=False)
        with mock.patch.object(rp, "_sdb", return_value=fake):
            uni = rp.construir_universo(ahora=AHORA, piloto={"aaa-0001-neg": 94})
        por = {it["sku"]: it for it in uni["items"]}
        self.assertEqual(set(por), {"AAA-0001-NEG", "BBB-0002-BLN"})   # universo COMPLETO
        self.assertEqual((por["AAA-0001-NEG"]["contenedor"],
                          por["AAA-0001-NEG"]["contenedor_multi"],
                          por["AAA-0001-NEG"]["contenedor_fuente"]), ("94", False, "piloto"))
        self.assertEqual((por["BBB-0002-BLN"]["contenedor"],
                          por["BBB-0002-BLN"]["contenedor_fuente"]), (None, None))
        self.assertFalse(uni["tablas"]["costing.sku_contenedor"])
        self.assertNotIn(rp._SQL_CONT, [s for s, _ in llamadas])       # sin tabla, no se consulta
        stock = [p for s, p in llamadas if s is rp._SQL_STOCK]
        self.assertEqual(stock, [{"skus": ["AAA-0001-NEG", "BBB-0002-BLN"]}])
        self.assertEqual(por["AAA-0001-NEG"]["stock_detalle"], {"propio": 10, "full": 5})

    def test_con_tabla_manda_la_tabla(self):
        fake, llamadas = _fake_sdb(con_tabla_cont=True, cont_filas=[
            {"sku": "AAA-0001-NEG", "numero": 80, "n": 2, "multi": True}])
        with mock.patch.object(rp, "_sdb", return_value=fake):
            uni = rp.construir_universo(ahora=AHORA, piloto={"AAA-0001-NEG": 94,
                                                             "BBB-0002-BLN": 12})
        por = {it["sku"]: it for it in uni["items"]}
        self.assertEqual((por["AAA-0001-NEG"]["contenedor"],
                          por["AAA-0001-NEG"]["contenedor_multi"],
                          por["AAA-0001-NEG"]["contenedor_fuente"]), ("80", True, "tabla"))
        # La tabla no lo tiene: null, NO el número del piloto.
        self.assertIsNone(por["BBB-0002-BLN"]["contenedor"])
        self.assertIn(rp._SQL_CONT, [s for s, _ in llamadas])


class PilotoRouter(unittest.TestCase):
    def test_todos_por_query(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import radar_precios as ruta

        app = FastAPI()
        app.include_router(ruta.router)
        cli = TestClient(app)
        admin = SimpleNamespace(autenticado=True, tipo="persona", rol="admin",
                                actor="admin@x")
        uni = Piloto.UNI
        ruta._universo = None
        try:
            with mock.patch("routers.investigacion.core_identidad.resolver",
                            new=mock.AsyncMock(return_value=admin)), \
                 mock.patch.object(rp, "construir_universo", return_value=uni) as cu, \
                 mock.patch.dict(rp.PILOTO, {"B": 80, "D": None}, clear=True):
                r = cli.get("/api/radar-precios")
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual((r.json()["total"], r.json()["piloto"]["activo"]), (2, True))
                for q in ("todos=1", "todos=true"):
                    with self.subTest(q=q):
                        r = cli.get(f"/api/radar-precios?{q}")
                        self.assertEqual(r.status_code, 200, r.text)
                        self.assertEqual((r.json()["total"], r.json()["piloto"]["activo"]),
                                         (4, False))
                # El universo se arma UNA vez; el recorte va encima de la caché.
                self.assertEqual(cu.call_count, 1)
        finally:
            ruta._universo = None
            ruta._detalles.clear()


if __name__ == "__main__":
    unittest.main()
