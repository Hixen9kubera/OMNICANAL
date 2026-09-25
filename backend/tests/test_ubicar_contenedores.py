"""Pruebas de la carga SKU → contenedor (`costing.sku_contenedor`, 0060).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Los niveles** (`clasificar_sku`): A por Ferraforme alineado o por dos
   familias; B por una sola familia; la OC recibida y el pelón NO cuentan como
   familia; multi cuando cada N tiene documento, y la OC solo documenta si su
   N no es la mayoría del campo de Odoo, su Ferraforme no la contradice y no
   es el mismo recibo que otra OC; conflicto (el lote del 12 capturado como 34
   en Odoo, «OOLU9155398 - cont 98»); refutado (Odoo solo, con un Ferraforme
   de esa N que no trae el SKU, sin excepción por OC) y la marca
   `contradicho_por` en lo que sí se carga; provisional «NNNN-NNNN»; padre de
   Woo sin evidencia.
2. **La lectura de fuentes**: el campo de Odoo (número / pelón / ambiguo, con
   el nombre del producto), costos (sufijo, herencia, basura INHERIT), la N de
   cada OC, que solo los renglones RECIBIDOS son evidencia y que una N ≤ 0 es
   «sin N».
3. **Todo junto** (`armar_evidencia` + `ubicar`) con filas como las de prod.
4. **Los candados**: el destino solo puede ser el sandbox (por la ref del
   usuario o del host), el origen solo «carga_…», el tope de caída, la lectura
   de kubera en una sola foto de solo lectura, y el cliente de Odoo no deja
   salir ningún método que escriba.

Sin red: nada aquí habla con kubera, el sandbox ni Odoo.

    cd backend && python -m unittest tests.test_ubicar_contenedores -v
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import ubicar_contenedores as U  # noqa: E402


def _f(**fuentes) -> dict:
    """_f(ferra_al=[80], oc_rec={34: "notas:numero"}) → {fuente: {N: [ref]}}.
    Una lista da refs vacías; un dict N→vía arma la ref de una OC."""
    salida = {}
    for fuente, valor in fuentes.items():
        if isinstance(valor, dict):
            salida[fuente] = {n: [{"oc": "P00001", "via": via}] for n, via in valor.items()}
        else:
            salida[fuente] = {n: [{}] for n in valor}
    return salida


def _clasificar(fuentes, sku="ACC-0001-NEG", en_catalogo=True, es_padre_woo=False, con_ferraforme=()):
    return U.clasificar_sku(sku, fuentes, en_catalogo=en_catalogo, es_padre_woo=es_padre_woo,
                            ns_con_ferraforme=set(con_ferraforme))


def _niveles(r):
    return {a["numero"]: a["nivel"] for a in r["asignaciones"]}


# ─────────────────────────────────────────────────────────────────────────────
# 1. NIVELES
# ─────────────────────────────────────────────────────────────────────────────
class Niveles(unittest.TestCase):
    def test_a_por_ferraforme_alineado(self):
        r = _clasificar(_f(ferra_al=[80]), con_ferraforme=[80])
        self.assertEqual(r["estado"], "cargar")
        self.assertEqual(_niveles(r), {80: "A"})
        self.assertEqual(r["asignaciones"][0]["fuentes"], ["ferraforme"])
        self.assertFalse(r["multi"])

    def test_a_por_dos_familias(self):
        r = _clasificar(_f(ferra_na=[56], costos=[56]), con_ferraforme=[56])
        self.assertEqual(_niveles(r), {56: "A"})
        self.assertEqual(r["asignaciones"][0]["fuentes"], ["ferraforme", "costos"])
        self.assertEqual(r["asignaciones"][0]["familias"], ["B", "F"])
        # Costos + Odoo con número, sin Ferraforme: también dos familias.
        r = _clasificar(_f(costos=[97], odoo_campo=[97]))
        self.assertEqual(_niveles(r), {97: "A"})

    def test_b_una_sola_familia(self):
        for fuentes in (_f(ferra_na=[80]), _f(costos=[97]), _f(caja=[97]), _f(odoo_campo=[97])):
            with self.subTest(fuentes=list(fuentes)):
                r = _clasificar(fuentes, con_ferraforme=[80])
                self.assertEqual(r["estado"], "cargar")
                self.assertEqual(_niveles(r), {next(iter(next(iter(fuentes.values())))): "B"})

    def test_oc_no_cuenta_como_familia(self):
        # Ferraforme no alineado + su OC: sigue siendo UNA familia → B, no A.
        r = _clasificar(_f(ferra_na=[80], oc_rec={80: "mayoria_ferraforme 30/40"}))
        self.assertEqual(_niveles(r), {80: "B"})
        self.assertEqual(r["asignaciones"][0]["fuentes"], ["ferraforme", "odoo_oc"])
        self.assertEqual(r["asignaciones"][0]["familias"], ["F"])
        # Ni siquiera con la N por texto: la OC apoya, no suma familia.
        r = _clasificar(_f(odoo_campo=[97], oc_rec={97: "encabezado:numero"}))
        self.assertEqual(_niveles(r), {97: "B"})
        # La OC sola no carga.
        r = _clasificar(_f(oc_rec={97: "encabezado:numero"}))
        self.assertEqual((r["estado"], r["motivo"]), ("excluir", "debil"))

    def test_pelon_solo_codigo_es_debil(self):
        r = _clasificar(_f(odoo_pelon=[80]))
        self.assertEqual((r["estado"], r["motivo"]), ("excluir", "debil"))
        r = _clasificar(_f(odoo_pelon=[80], oc_rec={80: "notas:numero"}))
        self.assertEqual(r["motivo"], "debil")
        # Junto a costos NO sube a A, y no aparece entre las fuentes de la fila.
        r = _clasificar(_f(costos=[80], odoo_pelon=[80]))
        self.assertEqual(_niveles(r), {80: "B"})
        self.assertEqual(r["asignaciones"][0]["fuentes"], ["costos"])
        self.assertIn("odoo_pelon", r["asignaciones"][0]["evidencia"])

    def test_refutado_odoo_solo_contra_su_ferraforme(self):
        # Odoo dice 75, el Ferraforme del 75 existe y no trae el SKU.
        r = _clasificar(_f(odoo_campo=[75]), con_ferraforme=[75])
        self.assertEqual((r["estado"], r["motivo"]), ("excluir", "refutado"))
        # Si la N no tiene Ferraforme, no hay con qué refutar: B.
        r = _clasificar(_f(odoo_campo=[97]), con_ferraforme=[75])
        self.assertEqual(_niveles(r), {97: "B"})
        # Sin excepción por OC (el spec no la trae): en una N cuyo Ferraforme no
        # trae el SKU ninguna OC lo documenta, sea cual sea la vía de su N.
        for via in ("notas:numero", "nombre_ferraforme", "mayoria_ferraforme+campo 20+18/30",
                    "mayoria_campo_odoo 30/40"):
            with self.subTest(via=via):
                r = _clasificar(_f(odoo_campo=[75], oc_rec={75: via}), con_ferraforme=[75])
                self.assertEqual((r["motivo"], r["refuta"]), ("refutado", "Ferraforme del 75"))
        # Costos solo no se refuta (el spec la tiene por fuerte), pero queda marcado.
        r = _clasificar(_f(costos=[75]), con_ferraforme=[75])
        self.assertEqual(_niveles(r), {75: "B"})
        self.assertEqual(r["asignaciones"][0]["evidencia"]["contradicho_por"], "Ferraforme del 75")
        # Si el Ferraforme SÍ lo trae, no hay marca.
        r = _clasificar(_f(ferra_na=[75], costos=[75]), con_ferraforme=[75])
        self.assertNotIn("contradicho_por", r["asignaciones"][0]["evidencia"])

    def test_multi_con_documento_en_cada_n(self):
        # Reorden: Ferraforme del 12 y OC recibida del 34.
        r = _clasificar(_f(ferra_al=[12], oc_rec={34: "encabezado:numero"}), con_ferraforme=[12])
        self.assertEqual(r["estado"], "cargar")
        self.assertTrue(r["multi"])
        self.assertEqual(_niveles(r), {12: "A", 34: "B"})
        # Ferraforme de una N y costos de otra: las dos son documento.
        r = _clasificar(_f(ferra_na=[80], costos=[85]))
        self.assertTrue(r["multi"])
        self.assertEqual(_niveles(r), {80: "B", 85: "B"})
        # La caja compartida es familia B pero no documento: otra N por caja es conflicto.
        r = _clasificar(_f(ferra_al=[82], caja=[75], oc_rec={82: "notas:numero"}))
        self.assertEqual((r["motivo"], r["sin_documento"]), ("conflicto", [75]))

    def test_oc_que_no_documenta_su_n_es_conflicto(self):
        # 1. Su N es la mayoría de los OTROS SKUs según el campo de Odoo
        #    (VEH-0180-MET: costos 70, Odoo y P03336 dicen 84).
        for via in ("mayoria_campo_odoo 175/302", "mayoria_ferraforme+campo 254+250/266"):
            with self.subTest(via=via):
                r = _clasificar(_f(costos=[70], odoo_campo=[84], oc_rec={84: via}))
                self.assertEqual((r["motivo"], r["sin_documento"]), ("conflicto", [84]))
                self.assertIn("mayoría", r["oc_no_documenta"][84][0])
        # 2. El Ferraforme de esa N no lo trae (TEC-1033: 47 bien documentado, P02543 del 52).
        r = _clasificar(_f(ferra_al=[47], costos=[47], oc_rec={52: "encabezado:numero"}), con_ferraforme=[47, 52])
        self.assertEqual((r["motivo"], r["sin_documento"]), ("conflicto", [52]))
        self.assertEqual(r["oc_no_documenta"], {52: ["el Ferraforme del 52 no lo trae"]})
        # 3. El mismo recibo en dos OC de dos N («De consumibles a almacenables»).
        fuentes = {"ferra_al": {80: [{}]},
                   "oc_rec": {80: [{"oc": "P03487", "via": "notas:numero", "recibido": 120.0}],
                              88: [{"oc": "P03513", "via": "notas:numero", "recibido": 120.0}]}}
        r = _clasificar(fuentes, con_ferraforme=[80])
        self.assertEqual((r["motivo"], r["sin_documento"]), ("conflicto", [88]))
        self.assertIn("recibió lo mismo (120) que P03487 (del 80)", r["oc_no_documenta"][88][0])
        # Con otra cantidad sí documenta: una reorden de verdad.
        fuentes["oc_rec"][88][0]["recibido"] = 60.0
        r = _clasificar(fuentes, con_ferraforme=[80])
        self.assertEqual((r["estado"], _niveles(r)), ("cargar", {80: "A", 88: "B"}))
        self.assertTrue(r["multi"])
        self.assertEqual(r["asignaciones"][1]["familias"], [])       # B dentro de un multi, sin familia

    def test_multi_solo_de_oc_es_debil(self):
        # Dos OC que documentan dos N, sin ninguna familia: igual que una OC sola.
        fuentes = {"oc_rec": {5: [{"oc": "P1", "via": "encabezado:numero", "recibido": 10.0}],
                              7: [{"oc": "P2", "via": "encabezado:numero", "recibido": 30.0}]}}
        r = _clasificar(fuentes)
        self.assertEqual((r["estado"], r["motivo"]), ("excluir", "debil"))

    def test_oc_documenta(self):
        f = {"oc_rec": {80: [{"oc": "P1", "via": "mayoria_campo_odoo 3/5", "recibido": 5.0},
                             {"oc": "P2", "via": "encabezado:numero", "recibido": 7.0}]}}
        # Basta una OC que documente, aunque otra de la misma N no.
        self.assertEqual(U.oc_documenta(f, 80, set()), (True, []))
        self.assertEqual(U.oc_documenta(f, 81, set()), (False, []))
        self.assertEqual(U.documentos(f, 80, set()), {"oc_rec"})
        self.assertEqual(U.documentos(f, 80, {80}), set())

    def test_conflicto_lote_12_capturado_como_34(self):
        # Ferraforme y costos dicen 12; el campo de Odoo, 34 (el lote mal capturado).
        r = _clasificar(_f(ferra_al=[12], costos=[12], odoo_campo=[34]), con_ferraforme=[12, 34])
        self.assertEqual((r["estado"], r["motivo"]), ("excluir", "conflicto"))
        self.assertEqual(r["sin_documento"], [34])
        self.assertEqual(r["asignaciones"], [])

    def test_conflicto_oolu_94_contra_98(self):
        r = _clasificar(_f(ferra_al=[94], odoo_ambiguo=[94, 98]), con_ferraforme=[94])
        self.assertEqual(r["motivo"], "conflicto")
        self.assertEqual(r["sin_documento"], [98])
        # Un pelón que discrepa también es conflicto (no tiene documento).
        r = _clasificar(_f(costos=[90], odoo_pelon=[70]))
        self.assertEqual(r["motivo"], "conflicto")

    def test_provisional_se_excluye_siempre(self):
        r = _clasificar(_f(ferra_al=[80], costos=[80]), sku="4814-0001", con_ferraforme=[80])
        self.assertEqual((r["estado"], r["motivo"]), ("excluir", "provisional"))
        for sku in ("4814-0001", "0031-0001", "4814-0001-A", " 4814-0001 "):
            self.assertTrue(U.es_provisional(sku), sku)
        for sku in ("ACC-0710-MET", "TEC-0454-GRI", "802G", "MX-7661"):
            self.assertFalse(U.es_provisional(sku), sku)

    def test_padre_woo(self):
        r = _clasificar({}, sku="CALZ-0179", es_padre_woo=True)
        self.assertEqual((r["estado"], r["motivo"]), ("excluir", "padre_woo"))
        # Con evidencia propia se clasifica como cualquiera.
        r = _clasificar(_f(ferra_na=[56], costos=[56]), sku="CALZ-0179", es_padre_woo=True)
        self.assertEqual(_niveles(r), {56: "A"})

    def test_fuera_de_catalogo_y_sin_evidencia(self):
        r = _clasificar(_f(ferra_al=[80]), en_catalogo=False)
        self.assertEqual(r["motivo"], "fuera_de_catalogo")
        r = _clasificar({})
        self.assertEqual(r["motivo"], "sin_evidencia")

    def test_evidencia_y_fila_de_bd(self):
        fuentes = {"ferra_al": {80: [{"archivo": "Contenedor 80 X.xlsx", "fila": 7, "alineado": True}]},
                   "costos": {80: [{"valor": "TGHU6894814 - 80", "via": "sufijo"}]}}
        r = _clasificar(fuentes, con_ferraforme=[80])
        ev = r["asignaciones"][0]["evidencia"]
        self.assertEqual(ev["ferraforme"][0]["fila"], 7)
        self.assertEqual(ev["costos"][0]["valor"], "TGHU6894814 - 80")
        a = {"sku": "ACC-0001-NEG", "numero": 80, "codigo": "TGHU6894814", "nivel": "A",
             "fuentes": ["ferraforme", "costos"], "multi": False, "evidencia": ev, "origen": U.ORIGEN_CARGA_INICIAL}
        fila = U.fila_bd(a)
        self.assertEqual(fila[:6], ("ACC-0001-NEG", 80, "TGHU6894814", "A", ["ferraforme", "costos"], False))
        self.assertIn('"fila": 7', fila[6])
        self.assertEqual(fila[7], "carga_inicial_2026-09")
        # Solo las cuatro etiquetas que acepta el check de la 0060.
        self.assertTrue(set(U.A_BD.values()) <= set(U.FUENTES_BD))


# ─────────────────────────────────────────────────────────────────────────────
# 2. LECTURA DE FUENTES
# ─────────────────────────────────────────────────────────────────────────────
class LecturaFuentes(unittest.TestCase):
    MAPA = {"OOLU9155398": {94}, "TIIU6522619": {12}, "149504112538": {12}}

    def _una(self, texto, serie=()):
        lecs = U.interpretar_odoo(texto, self.MAPA, set(serie), {12, 94})
        self.assertEqual(len(lecs), 1, lecs)
        return lecs[0]

    def test_odoo_codigo_y_numero(self):
        lec = self._una("TIIU6522619 contenedor 12")
        self.assertEqual((lec["fuente"], lec["numero"], lec["via"]), ("odoo_campo", 12, "codigo+numero"))
        lec = self._una("Contenedor 77")
        self.assertEqual((lec["fuente"], lec["numero"]), ("odoo_campo", 77))
        self.assertEqual(lec["via"], "solo_numero(N_nuevo)")

    def test_odoo_pelon(self):
        lec = self._una("TIIU6522619")
        self.assertEqual((lec["fuente"], lec["numero"], lec["via"]), ("odoo_pelon", 12, "codigo"))

    def test_odoo_codigo_y_numero_discrepan(self):
        lec = self._una("OOLU9155398 - cont 98")
        self.assertEqual(lec["fuente"], "odoo_ambiguo")
        self.assertEqual((lec["n_codigo"], lec["n_texto"]), (94, 98))
        # Si el código es de una serie arrastrada, manda el código.
        lec = self._una("OOLU9155398 - cont 98", serie=["OOLU9155398"])
        self.assertEqual((lec["fuente"], lec["numero"]), ("odoo_campo", 94))

    def test_odoo_evidencia_ambigua_da_las_dos_n(self):
        info = {"X-1": {"sku": "X-1", "activo": True, "texto": "OOLU9155398 - cont 98"}}
        ev, ns, _ = U.evidencia_odoo_campo(info, self.MAPA, set())
        self.assertEqual(sorted((e["fuente"], e["numero"]) for e in ev),
                         [("odoo_ambiguo", 94), ("odoo_ambiguo", 98)])
        self.assertEqual(ns, {})       # el ambiguo no vota en la mayoría de las OC

    def test_odoo_variante_activa_con_texto_gana(self):
        prods = [{"id": 1, "default_code": "a-1", "product_tmpl_id": [10, "x"], "active": False, "name": "Motor"},
                 {"id": 2, "default_code": "A-1", "product_tmpl_id": [11, "y"], "active": True,
                  "name": "Monitor curvo gamer 27"}]
        info = U.productos_odoo_por_sku(prods, {10: "Contenedor 5", 11: "Contenedor 6"})
        self.assertEqual(info["A-1"]["texto"], "Contenedor 6")
        # El nombre de Odoo y cuántos productos comparten el SKU van a la evidencia.
        self.assertEqual((info["A-1"]["nombre"], info["A-1"]["productos"]), ("Monitor curvo gamer 27", 2))
        ev, _, _ = U.evidencia_odoo_campo(info, {}, set())
        self.assertEqual(ev[0]["ref"]["nombre_odoo"], "Monitor curvo gamer 27")
        self.assertEqual(ev[0]["ref"]["productos_con_este_sku"], 2)

    def test_palabras_en_comun(self):
        self.assertEqual(U.palabras_en_comun("Monitor curvo gamer 27", "Motor"), 0)
        self.assertEqual(U.palabras_en_comun("Cinturón ancho piel", "CINTURON de piel"), 2)
        self.assertEqual(U.palabras_en_comun("", "Motor"), 0)

    def test_n_cero_es_sin_n(self):
        # «… - 0» y «Contenedor 0» no son contenedores: el check de la 0060 tumbaría la carga.
        lecs = U.interpretar_odoo("ABCU1234565 - 0", {}, set(), set())
        self.assertEqual([x["numero"] for x in lecs], [None])
        lecs = U.interpretar_odoo("Contenedor 0", {}, set(), set())
        self.assertEqual([x["numero"] for x in lecs], [None])
        ev, sin_n = U.evidencia_costos([{"sku": "X-1", "contenedor": "TRHU6540031 - 0"}], {}, {})
        self.assertEqual((ev, [s["sku"] for s in sin_n]), ([], ["X-1"]))
        ev, sin_n = U.evidencia_caja([{"sku": "X-2", "archivo": "Contenedor 0.xlsx"}], {})
        self.assertEqual((ev, [s["sku"] for s in sin_n]), ([], ["X-2"]))
        self.assertIsNone(U.numero_de_archivo("Contenedor 0 ABCU1234565.xlsx"))
        self.assertEqual(U.n_de_texto_oc("Contenedor 0", {}), (None, ""))
        self.assertEqual(U.mapa_codigos([{"numero": 0, "codigos": ["ABCU1234565"]}], []), {})
        # Y si llegara una evidencia con N 0, no se clasifica ni se carga.
        res = U.ubicar([{"sku": "X-1", "fuente": "costos", "numero": 0, "ref": {}}], {"X-1": "X-1"}, set(), set())
        self.assertEqual((res["asignaciones"], res["excluidos"][0]["motivo"]), ([], "sin_evidencia"))

    def test_costos_sufijo_herencia_y_basura(self):
        filas = [{"sku": "A", "contenedor": "TRHU6540031 - 91"},
                 {"sku": "B", "contenedor": "INHERIT(ACC-0703-CAF)"},
                 {"sku": "C", "contenedor": "PRY25-543"},
                 {"sku": "D", "contenedor": "149504112538"},
                 {"sku": "E", "contenedor": ""}]
        ev, sin_n = U.evidencia_costos(filas, {"PRY25-543": 75}, self.MAPA)
        self.assertEqual({(e["sku"], e["numero"], e["ref"]["via"]) for e in ev},
                         {("A", 91, "sufijo"), ("C", 75, "heredado_ferraforme"), ("D", 12, "codigo")})
        self.assertEqual([(s["sku"], s["via"]) for s in sin_n], [("B", "basura_inherit")])

    def test_ferraforme_n_del_nombre_y_oc_del_archivo(self):
        archivos = [{"id": 1, "tipo": "ferraforme", "sha256": "s1", "nombre": "Cont 95 TLLU8977270-P03087.xlsx"},
                    {"id": 2, "tipo": "ferraforme", "sha256": "s2", "nombre": "Contenedor 88 MRKU4831449-P03076.xlsx"}]
        ubic = [{"sku": "X-1", "ferraforme_sha256": "s1", "alineado": True, "ferraforme_fila": 7},
                {"sku": "X-2", "ferraforme_sha256": "s2", "alineado": False, "ferraforme_fila": 3}]
        ev, sin_n = U.evidencia_ferraforme(ubic, archivos, {})
        self.assertEqual({(e["sku"], e["fuente"], e["numero"]) for e in ev},
                         {("X-1", "ferra_al", 95), ("X-2", "ferra_na", 88)})
        self.assertEqual(sin_n, [])
        self.assertEqual(U.ocs_por_archivo(archivos), {"P03087": 95, "P03076": 88})

    def test_n_de_cada_oc(self):
        ordenes = [
            {"id": 1, "name": "P03087", "partner_ref": False, "origin": False, "notes": False, "state": "purchase"},
            {"id": 2, "name": "P03100", "partner_ref": "Contenedor 80", "origin": False, "notes": False,
             "state": "purchase"},
            {"id": 3, "name": "P03101", "partner_ref": False, "origin": False, "notes": False, "state": "purchase"},
            {"id": 4, "name": "P03102", "partner_ref": False, "origin": False,
             "notes": "<p>Complementa P03100</p>", "state": "done"},
            {"id": 5, "name": "P03103", "partner_ref": "Contenedor 81", "origin": False, "notes": False,
             "state": "draft"},
        ]
        lineas = [{"order_id": [3, "P03101"], "product_id": [30 + i, "x"], "product_qty": 1, "qty_received": 1}
                  for i in range(4)]
        sku_por_producto = {30 + i: f"S-{i}" for i in range(4)}
        ns_odoo = {f"S-{i}": {34} for i in range(4)}
        res = U.numeros_de_oc(ordenes, lineas, sku_por_producto, {}, ns_odoo, {}, {"P03087": 95})
        self.assertEqual((res[1]["n"], res[1]["via"]), (95, "nombre_ferraforme"))
        self.assertEqual((res[2]["n"], res[2]["via"]), (80, "encabezado:numero"))
        self.assertEqual(res[3]["n"], 34)
        self.assertTrue(res[3]["via"].startswith("mayoria_campo_odoo"))
        self.assertEqual((res[4]["n"], res[4]["via"]), (80, "complementa:P03100"))
        self.assertEqual(res[5]["n"], 81)      # tiene N, pero en borrador no es evidencia

    def test_solo_renglones_recibidos_y_sin_duplicadas(self):
        ordenes = [{"id": i, "name": f"P0310{i}", "partner_ref": "Contenedor 80", "origin": False,
                    "notes": False, "state": "purchase"} for i in (1, 2)]
        ordenes.append({"id": 3, "name": "P03103", "partner_ref": "Contenedor 81", "origin": False,
                        "notes": False, "state": "draft"})
        lineas = [
            {"order_id": [1, ""], "product_id": [10, ""], "product_qty": 5, "qty_received": 5},
            {"order_id": [1, ""], "product_id": [11, ""], "product_qty": 5, "qty_received": 0},
            # P03102: mismos SKUs y misma N que P03101, pero recibió menos → duplicada.
            {"order_id": [2, ""], "product_id": [10, ""], "product_qty": 5, "qty_received": 1},
            {"order_id": [2, ""], "product_id": [11, ""], "product_qty": 5, "qty_received": 0},
            {"order_id": [3, ""], "product_id": [10, ""], "product_qty": 5, "qty_received": 5},
        ]
        skus = {10: "R-1", 11: "R-2"}
        numeros = U.numeros_de_oc(ordenes, lineas, skus, {}, {}, {}, {})
        self.assertEqual(numeros[2].get("duplicada_de"), "P03101")
        ev, cuenta = U.evidencia_oc(numeros, lineas, skus)
        self.assertEqual([(e["sku"], e["numero"], e["ref"]["oc"]) for e in ev], [("R-1", 80, "P03101")])
        self.assertEqual(cuenta, {"recibidos": 1, "sin_recibir": 1})


# ─────────────────────────────────────────────────────────────────────────────
# 3. TODO JUNTO
# ─────────────────────────────────────────────────────────────────────────────
class Integracion(unittest.TestCase):
    def setUp(self):
        archivos = [
            {"id": 1, "tipo": "ferraforme", "sha256": "f12", "nombre": "149504112538 TIIU6522619 contenedor 12.xlsx"},
            {"id": 2, "tipo": "ferraforme", "sha256": "f75", "nombre": "Contenedor 75 PRY25-543.xlsx"},
        ]
        ubic = [{"sku": f"LOTE-{i:04d}", "ferraforme_sha256": "f12", "alineado": True, "ferraforme_fila": i}
                for i in range(25)]
        ubic += [{"sku": f"FER-{i:04d}", "ferraforme_sha256": "f75", "alineado": False, "ferraforme_fila": i}
                 for i in range(20)]
        ubic.append({"sku": "reorden-1", "ferraforme_sha256": "f12", "alineado": True, "ferraforme_fila": 99})
        costos = [{"sku": f"LOTE-{i:04d}", "contenedor": "149504112538 - 12"} for i in range(25)]
        costos += [{"sku": "REORDEN-1", "contenedor": "TRHU6540031 - 91"},
                   {"sku": "SOLO-COSTOS", "contenedor": "PRY25-543"},
                   {"sku": "4814-0001", "contenedor": "TRHU6540031 - 91"}]
        # Odoo: el lote del 12 capturado como 34; un SKU que Odoo pone en el 75
        # (y el Ferraforme del 75 no lo trae); un pelón; una OC del 12.
        plantillas = [{"id": 1, "container_numbers": "EGSU1664119 - 34"},
                      {"id": 2, "container_numbers": "Contenedor 75"},
                      {"id": 3, "container_numbers": "TIIU6522619"}]
        productos = [{"id": 100 + i, "default_code": f"LOTE-{i:04d}", "product_tmpl_id": [1, ""], "active": True}
                     for i in range(3)]
        productos += [{"id": 200, "default_code": "SOLO-ODOO", "product_tmpl_id": [2, ""], "active": True},
                      {"id": 201, "default_code": "PELON-1", "product_tmpl_id": [3, ""], "active": True},
                      {"id": 202, "default_code": "OC-SOLA", "product_tmpl_id": False, "active": True}]
        ordenes = [{"id": 1, "name": "P09999", "partner_ref": "Contenedor 12", "origin": False, "notes": False,
                    "state": "purchase"}]
        lineas = [{"order_id": [1, ""], "product_id": [202, ""], "product_qty": 4, "qty_received": 4},
                  {"order_id": [1, ""], "product_id": [103, ""], "product_qty": 4, "qty_received": 4}]
        productos.append({"id": 103, "default_code": "LOTE-0003", "product_tmpl_id": False, "active": True})
        odoo = {"plantillas": plantillas, "productos": productos, "ordenes": ordenes, "lineas": lineas}
        self.ev, self.ctx = U.armar_evidencia(ubicaciones=ubic, archivos=archivos, costos=costos, caja=[], odoo=odoo)
        skus = ({u["sku"].upper() for u in ubic} | {c["sku"].upper() for c in costos}
                | {"SOLO-ODOO", "PELON-1", "OC-SOLA", "PADRE-1", "SIN-NADA"})
        catalogo = {s: s for s in skus}
        self.res = U.ubicar(self.ev, catalogo, {"PADRE-1"}, self.ctx["ns_con_ferraforme"],
                            self.ctx["codigo_por_numero"])
        self.por = {r["sku"]: r for r in self.res["por_sku"]}

    def test_ferraformes_con_documento(self):
        self.assertEqual(self.ctx["ns_con_ferraforme"], {12, 75})
        self.assertEqual(self.ctx["codigo_por_numero"][12], "TIIU6522619")

    def test_lote_12_contra_34_es_conflicto(self):
        for i in range(3):
            r = self.por[f"LOTE-{i:04d}"]
            self.assertEqual((r["estado"], r["motivo"]), ("excluir", "conflicto"))
            self.assertEqual(r["sin_documento"], [34])
        # Los del lote que Odoo no tiene mal: A por Ferraforme alineado.
        self.assertEqual(_niveles(self.por["LOTE-0010"]), {12: "A"})
        # LOTE-0003 además llegó en la OC del 12: sigue siendo A, con odoo_oc.
        a = self.por["LOTE-0003"]["asignaciones"][0]
        self.assertEqual((a["nivel"], a["fuentes"]), ("A", ["ferraforme", "costos", "odoo_oc"]))

    def test_resto_de_casos(self):
        self.assertEqual(self.por["SOLO-ODOO"]["motivo"], "refutado")
        self.assertEqual(self.por["PELON-1"]["motivo"], "debil")
        self.assertEqual(self.por["OC-SOLA"]["motivo"], "debil")
        self.assertEqual(self.por["4814-0001"]["motivo"], "provisional")
        self.assertEqual(self.por["PADRE-1"]["motivo"], "padre_woo")
        self.assertEqual(self.por["SIN-NADA"]["motivo"], "sin_evidencia")
        # PRY25-543 hereda el 75 de su Ferraforme: costos sola → B.
        self.assertEqual(_niveles(self.por["SOLO-COSTOS"]), {75: "B"})
        # La reorden: Ferraforme del 12 y costos del 91 → multi, las dos N.
        r = self.por["REORDEN-1"]
        self.assertTrue(r["multi"])
        self.assertEqual(_niveles(r), {12: "A", 91: "B"})

    def test_asignaciones_y_resumen(self):
        filas = [a for a in self.res["asignaciones"] if a["sku"] == "REORDEN-1"]
        self.assertEqual(len(filas), 2)
        self.assertTrue(all(a["multi"] and a["origen"] == "carga_inicial_2026-09" for a in filas))
        # El SKU sale como lo trae el catálogo, no como lo escribió la fuente.
        self.assertNotIn("reorden-1", {a["sku"] for a in self.res["asignaciones"]})
        res = self.res["resumen"]
        self.assertEqual(res["excluidos_por_motivo"]["conflicto"], 3)
        self.assertEqual(res["skus_multi"], 1)
        self.assertEqual(res["filas"], len(self.res["asignaciones"]))
        self.assertEqual(set(res["excluidos_por_motivo"]), set(U.MOTIVOS))


# ─────────────────────────────────────────────────────────────────────────────
# 4. CANDADOS
# ─────────────────────────────────────────────────────────────────────────────
def _script():
    ruta = Path(__file__).resolve().parent.parent / "scripts" / "ubicar_skus_contenedor.py"
    spec = importlib.util.spec_from_file_location("ubicar_skus_contenedor_prueba", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class Candados(unittest.TestCase):
    SANDBOX = "postgresql://postgres.yvootpbz:x@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    PROD = "postgresql://postgres.tukwcvsi:x@aws-0-us-east-1.pooler.supabase.com:6543/postgres"

    def test_destino_solo_sandbox(self):
        self.assertEqual(U.validar_destino(self.SANDBOX), "yvootpbz")
        with self.assertRaises(PermissionError):
            U.validar_destino(self.PROD)
        with self.assertRaises(PermissionError):
            U.validar_destino("postgresql://postgres.otroproyecto:x@host:5432/postgres")
        with self.assertRaises(PermissionError):
            U.validar_destino("")
        # Aunque traiga las dos refs, producción manda: no se escribe.
        with self.assertRaises(PermissionError):
            U.validar_destino(self.SANDBOX + "?application_name=tukwcvsi")
        # La ref es la del USUARIO o del HOST, no una subcadena cualquiera del DSN.
        for dsn in ("postgresql://postgres.abcdefgh:yvootpbz@otrohost:5432/postgres",
                    "postgresql://postgres:x@10.0.0.5:5432/postgres?application_name=yvootpbz",
                    "postgresql://postgres:yvootpbz@db.abcdefgh.supabase.co:5432/postgres"):
            with self.subTest(dsn=dsn), self.assertRaises(PermissionError):
                U.validar_destino(dsn)
        for dsn in ("postgresql://postgres:x@db.yvootpbzabcdefghijkl.supabase.co:5432/postgres",
                    "host=aws-0.pooler.supabase.com port=6543 user=postgres.yvootpbzabc password=x dbname=postgres"):
            with self.subTest(dsn=dsn):
                self.assertEqual(U.validar_destino(dsn), "yvootpbz")
        self.assertEqual(U.ref_de(self.PROD), "tukwcvsi")
        self.assertEqual(U.ref_de("sin dsn"), "")

    def test_origen_solo_de_carga(self):
        self.assertEqual(U.validar_origen("carga_inicial_2026-09"), "carga_inicial_2026-09")
        for origen in ("manual", "", "carga_", "Carga_x", None):
            with self.subTest(origen=origen), self.assertRaises(ValueError):
                U.validar_origen(origen)
        # El script lo revisa ANTES de conectarse al destino.
        s = _script()
        with mock.patch("psycopg2.connect") as conectar:
            with self.assertRaises(ValueError):
                s.aplicar([], self.SANDBOX, "manual", Path("."))
            conectar.assert_not_called()

    def _main(self, *argv):
        s = _script()
        with mock.patch.object(sys, "argv", ["ubicar_skus_contenedor.py", "--salida", ".", *argv]), \
                mock.patch.object(s, "leer_kubera") as leer, mock.patch("psycopg2.connect") as conectar:
            with self.assertRaises(SystemExit) as fin:
                s.main()
            leer.assert_not_called()
            conectar.assert_not_called()
        return str(fin.exception)

    def test_main_rechaza_combinaciones_peligrosas(self):
        self.assertIn("--sin-odoo", self._main("--aplicar", "--sin-odoo", "--acepto-destino", "yvootpbz"))
        self.assertIn("manual", self._main("--origen", "manual"))

    def test_lectura_de_kubera_en_una_sola_foto(self):
        s = _script()
        conn = mock.MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.return_value = []
        with mock.patch("psycopg2.connect", return_value=conn):
            s.leer_kubera(self.PROD)
        sqls = [" ".join(c[0][0].split()) for c in cur.execute.call_args_list]
        self.assertEqual(sqls[0], "set transaction isolation level repeatable read, read only")
        self.assertEqual(len(sqls), 2 + len(s.CONSULTAS_KUBERA))
        conn.rollback.assert_called_once()
        conn.set_session.assert_not_called()           # nunca por sesión (regla 13)
        conn.commit.assert_not_called()

    def test_odoo_nombre_distinto(self):
        s = _script()
        asig = [{"sku": "TEC-2241-MUL", "numero": 31, "evidencia": {"familias": ["O"], "odoo_campo": [
                    {"texto": "X - 31", "nombre_odoo": "Motor", "productos_con_este_sku": 2}]}},
                {"sku": "CIN-1", "numero": 31, "evidencia": {"familias": ["O"], "odoo_campo": [
                    {"texto": "X - 31", "nombre_odoo": "Cinturón de piel"}]}},
                {"sku": "A2", "numero": 31, "evidencia": {"familias": ["B", "O"], "odoo_campo": [
                    {"texto": "X - 31", "nombre_odoo": "Motor"}]}}]
        nombres = {"TEC-2241-MUL": "Monitor curvo gamer 27", "CIN-1": "CINTURON ancho piel", "A2": "Otro"}
        self.assertEqual(s.odoo_nombre_distinto(asig, nombres),
                         [["TEC-2241-MUL", 31, "Monitor curvo gamer 27", "Motor", "X - 31", 2]])

    def test_odoo_solo_lectura(self):
        s = _script()
        proxy = mock.Mock()
        proxy.execute_kw.return_value = [{"id": 1}]
        o = s.OdooSoloLectura("https://odoo.example", "db", "usuario", "secreto", proxy=proxy)
        for metodo in ("write", "create", "unlink", "copy", "action_confirm", "button_cancel", "search"):
            with self.subTest(metodo=metodo):
                with self.assertRaises(PermissionError):
                    o.llamar("product.template", metodo, [1], {"container_numbers": "x"})
        proxy.execute_kw.assert_not_called()
        self.assertEqual(o.llamar("product.template", "search_read", [], fields=["id"]), [{"id": 1}])
        self.assertEqual(proxy.execute_kw.call_args[0][4], "search_read")
        self.assertNotIn("secreto", repr(o))

    def test_script_aplicar_con_bd_falsa(self):
        # Sin red: una conexión falsa. Reemplaza SOLO su origen, carga solo los
        # SKUs que el destino tiene y reporta los que faltan.
        import tempfile
        s = _script()
        asig = [{"sku": "ACC-0001-NEG", "numero": 80, "codigo": "X", "nivel": "A", "fuentes": ["ferraforme"],
                 "multi": False, "evidencia": {}, "origen": U.ORIGEN_CARGA_INICIAL},
                {"sku": "NO-EXISTE", "numero": 80, "codigo": "X", "nivel": "B", "fuentes": ["costos"],
                 "multi": False, "evidencia": {}, "origen": U.ORIGEN_CARGA_INICIAL}]
        conn = mock.MagicMock()
        cur = conn.cursor.return_value
        # to_regclass, y luego cuántas filas ya tiene su origen (1: no hay caída).
        cur.fetchone.side_effect = [("costing.sku_contenedor",), (1,)]
        cur.fetchall.return_value = [("acc-0001-neg",)]
        cur.rowcount = 7
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("psycopg2.connect", return_value=conn), \
                mock.patch("psycopg2.extras.execute_values", return_value=[("ACC-0001-NEG",)]) as ev:
            r = s.aplicar(asig, self.SANDBOX, U.ORIGEN_CARGA_INICIAL, Path(tmp))
            faltan = (Path(tmp) / "faltan_en_destino.csv").read_text(encoding="utf-8-sig")
        sqls = [" ".join(c[0][0].split()) for c in cur.execute.call_args_list]
        self.assertIn("delete from costing.sku_contenedor where origen = %s", sqls)
        borrar = next(c for c in cur.execute.call_args_list if c[0][0].startswith("delete"))
        self.assertEqual(borrar[0][1], (U.ORIGEN_CARGA_INICIAL,))
        filas = ev.call_args[0][2]
        self.assertEqual([f[0] for f in filas], ["ACC-0001-NEG"])
        self.assertIn("on conflict (sku, numero) do nothing", " ".join(ev.call_args[0][1].split()))
        conn.commit.assert_called_once()
        self.assertIn("NO-EXISTE", faltan)
        self.assertEqual((r["borradas_de_su_origen"], r["insertadas"], r["faltan_en_destino_skus"]), (7, 1, 1))

    def test_script_aplicar_tope_de_caida(self):
        # 1 fila nueva contra 12,695 de su origen: aborta sin borrar nada.
        import tempfile
        s = _script()
        asig = [{"sku": "ACC-0001-NEG", "numero": 80, "codigo": "X", "nivel": "A", "fuentes": ["ferraforme"],
                 "multi": False, "evidencia": {}, "origen": U.ORIGEN_CARGA_INICIAL}]
        for permitir, aborta in ((False, True), (True, False)):
            with self.subTest(permitir=permitir):
                conn = mock.MagicMock()
                cur = conn.cursor.return_value
                cur.fetchone.side_effect = [("costing.sku_contenedor",), (12695,)]
                cur.fetchall.return_value = [("ACC-0001-NEG",)]
                with tempfile.TemporaryDirectory() as tmp, \
                        mock.patch("psycopg2.connect", return_value=conn), \
                        mock.patch("psycopg2.extras.execute_values", return_value=[("ACC-0001-NEG",)]) as ev:
                    if aborta:
                        with self.assertRaises(SystemExit) as fin:
                            s.aplicar(asig, self.SANDBOX, U.ORIGEN_CARGA_INICIAL, Path(tmp))
                        self.assertIn("--permitir-caida", str(fin.exception))
                    else:
                        r = s.aplicar(asig, self.SANDBOX, U.ORIGEN_CARGA_INICIAL, Path(tmp), permitir_caida=True)
                        self.assertEqual((r["previas_de_su_origen"], r["insertadas"]), (12695, 1))
                sqls = [c[0][0] for c in cur.execute.call_args_list]
                self.assertEqual(any(q.startswith("delete") for q in sqls), not aborta)
                self.assertEqual(ev.called, not aborta)
                self.assertEqual(conn.commit.called, not aborta)
                if aborta:
                    conn.rollback.assert_called_once()

    def test_script_aplicar_revisa_destino_antes_de_conectar(self):
        s = _script()
        with mock.patch("psycopg2.connect") as conectar:
            with self.assertRaises(PermissionError):
                s.aplicar([], self.PROD, U.ORIGEN_CARGA_INICIAL, Path("."))
            conectar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
