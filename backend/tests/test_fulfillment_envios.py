"""Pruebas de las reglas que clasifican las salidas de Odoo en FULLFILMENT.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
El sondeo del 14-sep-2026 (docs/FULLFILMENT_SONDEO_DESTINOS.md) midió que el
filtro viejo por subcadena del socio contaba 1,065 salidas como "envío a FULL"
y solo 229 lo eran, y que en Odoo ningún campo dice la cuenta de ML. Estas
pruebas fijan lo que lo arregla:

  1. canal por el nombre del socio, con AMAZON partido por piezas (≥40 = FBA;
     menos = venta MFN, fuera) y los "WALMART #…" fuera (son ventas S2H);
  2. cuenta por quien CREÓ la orden (Thalia 152 → San Corpe, Cinthya 153 →
     Kubera) y NINGUNA cuenta para cualquier otro creador;
  3. el número de envío normalizado desde los formatos que teclean las KAM;
  4. nada que no se midió se rellena: recibidas y solicitadas van en None, y un
     envío de ML sin número es «sinEnlazar», no un cero.

No se llama a Odoo: `armar()` recibe las filas tal como las devuelve XML-RPC.

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fulfillment_envios as fe  # noqa: E402


def _picking(pid, socio, sale_id, estado="done", validada="2026-06-30 18:22:11"):
    return {"id": pid, "name": f"TEXCO/OUT/{pid:05d}", "state": estado, "origin": f"S{sale_id}",
            "partner_id": [9000 + pid, socio], "sale_id": [sale_id, f"S{sale_id}"],
            "picking_type_id": [1, "TEXCO: Órdenes de entrega"],
            "create_date": "2026-06-18 06:27:58",
            "date_done": validada if estado == "done" else False, "scheduled_date": False}


def _orden(sale_id, creador_id, creador, ref):
    return {"id": sale_id, "name": f"S{sale_id}", "create_uid": [creador_id, creador],
            "user_id": [creador_id, creador], "client_order_ref": ref,
            "create_date": "2026-06-18 06:27:58", "state": "sale"}


def _mov(pid, sku, pedidas, hechas, estado="done"):
    return {"picking_id": [pid, "x"], "product_id": [1, f"[{sku}] Producto {sku}"],
            "product_qty": pedidas, "quantity": hechas, "state": estado}


class Canal(unittest.TestCase):
    def test_full_y_mercado_libre_exacto(self):
        self.assertEqual(fe.clasificar_canal("FULL", 1), "meli")
        self.assertEqual(fe.clasificar_canal("  full 59126318", 1), "meli")
        self.assertEqual(fe.clasificar_canal("MERCADO LIBRE", 1), "meli")
        # un comprador de ML NO es un envío a FULL
        self.assertIsNone(fe.clasificar_canal("Octavio Hernandez / Mercado Libre", 1))

    def test_amazon_se_parte_por_piezas(self):
        self.assertEqual(fe.clasificar_canal("AMAZON", 47), "amazon")
        self.assertEqual(fe.clasificar_canal("Amazon", 40), "amazon")
        self.assertIsNone(fe.clasificar_canal("AMAZON", 7), "1–7 piezas son ventas MFN")

    def test_wfs_si_walmart_no(self):
        self.assertEqual(fe.clasificar_canal("WFS 0029563GDM", 304), "walmart")
        self.assertIsNone(fe.clasificar_canal("WALMART #609124072844847", 1))


class Cuenta(unittest.TestCase):
    def test_por_quien_creo_la_orden(self):
        self.assertEqual(fe.asignar_cuenta("meli", "FULL", 152, "Thalia")[0], "San Corpe")
        self.assertEqual(fe.asignar_cuenta("meli", "FULL", 153, "Cinthya")[0], "Kubera")

    def test_otro_creador_no_se_adivina(self):
        cuenta, regla = fe.asignar_cuenta("meli", "FULL", 154, "Vale")
        self.assertIsNone(cuenta)
        self.assertIn("Vale", regla)

    def test_mercado_libre_exacto_es_kubera(self):
        self.assertEqual(fe.asignar_cuenta("meli", "MERCADO LIBRE", 176, "Nancy Cruz")[0], "Kubera")

    def test_el_socio_fijo_manda_sobre_el_creador(self):
        # «Crear FULL» crea como José Enrique (151): la cuenta la dice el socio.
        self.assertEqual(fe.asignar_cuenta("meli", "FULL KUBERA", 151, "José Enrique")[0], "Kubera")
        self.assertEqual(fe.asignar_cuenta("meli", " full  san corpe ", 151, "José Enrique")[0], "San Corpe")
        # …y gana aunque la capture la KAM de la otra cuenta.
        self.assertEqual(fe.asignar_cuenta("meli", "FULL KUBERA", 152, "Thalia")[0], "Kubera")
        self.assertEqual(fe.clasificar_canal("FULL SAN CORPE", 10), "meli")

    def test_quien_armo_una_orden_del_panel(self):
        o = {"create_uid": [151, "José Enrique"],
             "origin": "Panel FULLFILMENT · FULL Kubera · brandon · a1b2c3d4 · TEXCO"}
        self.assertEqual(fe.quien_armo(o), "Panel · brandon")
        self.assertEqual(fe.quien_armo({"create_uid": [153, "Cinthya"], "origin": "S1"}), "Cinthya")
        self.assertIsNone(fe.quien_armo({}))

    def test_fba_y_wfs(self):
        self.assertEqual(fe.asignar_cuenta("amazon", "AMAZON", 176, "Nancy Cruz")[0], "San Corpe")
        self.assertIsNone(fe.asignar_cuenta("walmart", "WFS 0029563GDM", 153, "Cinthya")[0])


class Numero(unittest.TestCase):
    def test_formatos_de_ml(self):
        for ref in ("75652884", "Envío #70688003", "ENVIO #62934496", "FULL KUBERA #62377191",
                    "KUBERA ENVIO FULL #63685881"):
            self.assertRegex(fe.numero_envio("meli", ref, "FULL")[0], r"^\d{8}$", ref)
        self.assertEqual(fe.numero_envio("meli", "Envío #70688003", "FULL"), ("70688003", "referencia"))
        self.assertEqual(fe.numero_envio("meli", None, "FULL 59126318"), ("59126318", "socio"))
        self.assertEqual(fe.numero_envio("meli", None, "FULL"), (None, None))

    def test_wfs_y_fba(self):
        self.assertEqual(fe.numero_envio("walmart", None, "WFS 0029563GDM")[0], "0029563GDM")
        self.assertEqual(fe.numero_envio("amazon", "fba15xkq2abc", "AMAZON")[0], "FBA15XKQ2ABC")
        self.assertIsNone(fe.numero_envio("amazon", None, "AMAZON")[0])


class Armar(unittest.TestCase):
    def setUp(self):
        self.pickings = [
            _picking(1, "FULL", 30942),                        # Cinthya con número
            _picking(2, "FULL", 38407, estado="waiting"),      # Cinthya sin número, abierta
            _picking(3, "AMAZON", 34541),                      # venta MFN: fuera
            _picking(4, "AMAZON", 38241),                      # FBA
            _picking(5, "FULL", 23676),                        # Vale: sin cuenta
        ]
        self.ordenes = {o["id"]: o for o in [
            _orden(30942, 153, "Cinthya", "Envío #70165348"),
            _orden(38407, 153, "Cinthya", ""),
            _orden(34541, 176, "Nancy Cruz", ""),
            _orden(38241, 176, "Nancy Cruz", ""),
            _orden(23676, 154, "Vale", "#62835925"),
        ]}
        self.movs = [
            _mov(1, "MUE-0073-GRI", 546, 546), _mov(1, "JUGU-0100-ROJ", 90, 90),
            _mov(2, "TEC-1", 63, 0, estado="assigned"),
            _mov(3, "TEC-0502-MET", 1, 1),
            _mov(4, "VIA-0024-NEG", 211, 211),
            _mov(5, "ORG-1", 60, 60),
        ]
        self.d = fe.armar(self.pickings, self.ordenes, self.movs)
        self.por_orden = {e["orden"]: e for e in self.d["envios"]}

    def test_la_venta_mfn_se_cuenta_pero_no_es_envio(self):
        self.assertNotIn("S34541", self.por_orden)
        self.assertEqual(self.d["excluidas"]["venta_amazon_mfn"], 1)

    def test_envio_completo(self):
        e = self.por_orden["S30942"]
        self.assertEqual((e["canal"], e["cuenta"], e["envio"], e["estado"]),
                         ("meli", "Kubera", "70165348", "salio"))
        self.assertEqual((e["piezas"], e["pedidas"], e["salida"]), (636, 636, "TEXCO/OUT/00001"))
        self.assertIn("Cinthya", e["cuenta_regla"])

    def test_lo_no_medido_va_en_none(self):
        e = self.por_orden["S30942"]
        # El rail son 5 etapas: las dos de Odoo y las tres que llena kubera.
        self.assertEqual(len(e["etapas"]), 5)
        self.assertIsNotNone(e["etapas"][0], "orden de venta")
        self.assertIsNotNone(e["etapas"][1], "salida validada")
        self.assertTrue(all(x is None for x in e["etapas"][2:]),
                        "recibido/activo/1ª venta las llena kubera, no `armar`")
        self.assertIsNone(e["cajas"])

    def test_abierta_sin_numero_es_sin_enlazar_y_sin_piezas_enviadas(self):
        e = self.por_orden["S38407"]
        self.assertEqual(e["estado"], "sinEnlazar")
        self.assertIsNone(e["piezas"], "una salida abierta no ha enviado nada: None, no 0")
        self.assertEqual(e["pedidas"], 63)
        self.assertIsNone(e["etapas"][1], "sin validar no hay fecha de salida")

    def test_fba_y_sin_cuenta(self):
        self.assertEqual(self.por_orden["S38241"]["estado"], "fbaSinLectura")
        self.assertIsNone(self.por_orden["S23676"]["cuenta"])
        self.assertEqual(self.d["resumen"]["meli:sin_asignar"]["salidas"], 1)

    def test_resumen_por_cuenta(self):
        r = self.d["resumen"]
        self.assertEqual(r["meli"]["salidas"], 3)
        self.assertEqual(r["meli:Kubera"]["hechas"], 1)
        self.assertEqual(r["meli:Kubera"]["abiertas"], 1)
        self.assertEqual(r["meli:Kubera"]["piezas_enviadas"], 636)
        self.assertEqual(r["meli:Kubera"]["piezas_abiertas"], 63)
        # jueves 18-jun 00:27 CDMX (06:27 UTC) → índice 3
        self.assertEqual(r["meli:Kubera"]["dias_orden"][3], 2)



class LoQueOdooNoSurtio(unittest.TestCase):
    """S38407 (18-sep): bodega validó sin tener TEC-1661-NEG-5C y Odoo CANCELÓ ese
    renglón (10 pedidas, 0 hechas). El panel filtraba los cancelados y el SKU
    desaparecía. En una salida validada se ve con su 0; en una abierta, un
    cancelado es un renglón que la KAM quitó y no se pide."""

    def setUp(self):
        pickings = [_picking(1, "FULL", 38407), _picking(2, "FULL", 38500, estado="waiting")]
        ordenes = {o["id"]: o for o in [_orden(38407, 153, "Cinthya", ""),
                                        _orden(38500, 153, "Cinthya", "")]}
        movs = [
            _mov(1, "EST-0055-MET", 35, 35),
            _mov(1, "TEC-1661-NEG-5C", 10, 0, estado="cancel"),
            _mov(1, "TEC-1661-NEG-5C", 0, 0, estado="cancel"),     # el renglón en 0 que deja Odoo
            _mov(1, "CUNA-0009-BLN", 0, 0, estado="cancel"),       # la KAM lo dejó en 0
            _mov(2, "TEC-1", 20, 0, estado="assigned"),
            _mov(2, "TEC-2", 5, 0, estado="cancel"),                # quitado antes de validar
        ]
        d = fe.armar(pickings, ordenes, movs)
        self.e = {x["orden"]: x for x in d["envios"]}

    def test_el_cancelado_de_una_salida_validada_se_ve_con_su_cero(self):
        e = self.e["S38407"]
        por_sku = {l["sku"]: l for l in e["lineas"]}
        self.assertEqual((por_sku["TEC-1661-NEG-5C"]["pedidas"], por_sku["TEC-1661-NEG-5C"]["enviadas"],
                          por_sku["TEC-1661-NEG-5C"]["faltante_odoo"]), (10, 0, 10))
        self.assertEqual((e["pedidas"], e["piezas"], e["faltante_odoo"]), (45, 35, 10))

    def test_los_renglones_en_cero_van_al_final(self):
        skus = [l["sku"] for l in self.e["S38407"]["lineas"]]
        self.assertEqual(skus[-1], "CUNA-0009-BLN")
        self.assertEqual(self.e["S38407"]["lineas"][-1]["pedidas"], 0)

    def test_en_una_salida_abierta_el_cancelado_no_se_pide(self):
        e = self.e["S38500"]
        self.assertEqual([l["sku"] for l in e["lineas"]], ["TEC-1"])
        self.assertEqual(e["pedidas"], 20)
        self.assertIsNone(e["faltante_odoo"], "sin validar, un 0 no es faltante")


if __name__ == "__main__":
    unittest.main()
