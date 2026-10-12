"""
Pruebas del módulo del catálogo de mayoreo. Sin red: Odoo es un doble en memoria y Slack no se llama.

    python -m unittest discover -s tests        (desde catalogo_mayoreo/)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import pedidos as P  # noqa: E402

CODIGO = "TDA-PRUE-BA22"
TEX, TEX2 = 135, 150
ALMACENES = [(TEX, "TEXCO"), (TEX2, "TEXCO II")]


def catalogo_json() -> dict:
    return {"version": "20261012-120000", "corte": "2026-10-12", "generado": "2026-10-12T12:00:00-06:00", "moneda": "MXN",
            "fotos": {"lado": 256, "cols": 8, "por_hoja": 32, "hojas": 1, "fotos": 3},
            "columnas": ["sku", "titulo", "k", "precio", "tipo_precio", "piezas", "foto", "en_odoo"],
            "K": [["Ropa", "Calzado"], ["Hogar", "Cocina"]],
            "P": [["CALZ-1-NEG-41", "Tenis negro 41", 0, 700.0, "c", 30, 0, 1],
                  ["CALZ-1-NEG-42", "Tenis negro 42", 0, 700.0, "c", 50, 0, 1],
                  ["MUE-1-MUL", "Taza con tapa", 1, 273.62, "p", 7200, 1, 1],
                  ["OFI-9-NEG", "Protector de cabecera", 1, 186.0, "e", 40, 2, 0],
                  ["DEC-9-AMA", "Arreglo floral", 1, None, None, 100, -1, 1]]}


def cuerpo(*skus: str, version: str = "20261012-120000", **extra) -> dict:
    piezas = {f[0]: f[5] for f in catalogo_json()["P"]}
    return {"codigo_tienda": CODIGO, "llave": "llave-de-prueba-0001", "version": version,
            "lineas": [{"sku": s, "cantidad": piezas[s]} for s in skus], **extra}


class OdooDeMentira:
    """Lo que `servicio.procesar` le pide a Odoo, en memoria. Confirmar aparta: el libre baja."""

    def __init__(self, productos: dict[str, int], libre: dict[int, dict[int, float]], confirma: bool = True):
        self.productos, self.libre, self.confirma = productos, libre, confirma
        self.ordenes: list[dict] = []
        self.falla_en: int | None = None

    def productos_por_sku(self, skus):
        return {s: [self.productos[s]] for s in skus if s in self.productos}

    def libre_por_almacen(self, ids, almacenes):
        return {i: {a: self.libre.get(i, {}).get(a, 0.0) for a, _n in almacenes} for i in ids}

    def ordenes_del_folio(self, folio):
        return [{"id": o["id"], "name": o["name"], "state": o["state"], "client_order_ref": o["client_order_ref"]}
                for o in self.ordenes if o["client_order_ref"].startswith(folio + "#") and o["state"] != "cancel"]

    def lineas_de(self, ids):
        return [{"order_id": [o["id"], o["name"]], "product_id": [ln[2]["product_id"], ""], "product_uom_qty": ln[2]["product_uom_qty"]}
                for o in self.ordenes if o["id"] in ids for ln in o["order_line"]]

    def crear_orden(self, valores):
        if self.falla_en == len(self.ordenes) + 1:
            raise RuntimeError("Odoo se cayó a media creación")
        orden = {**valores, "id": 900 + len(self.ordenes), "name": f"S{39001 + len(self.ordenes)}", "state": "draft"}
        self.ordenes.append(orden)
        return orden["id"]

    def confirmar(self, orden_id):
        orden = next(o for o in self.ordenes if o["id"] == orden_id)
        if not self.confirma:
            return
        orden["state"] = "sale"
        for _a, _b, ln in orden["order_line"]:
            self.libre[ln["product_id"]][orden["warehouse_id"]] -= ln["product_uom_qty"]

    def leer_orden(self, orden_id):
        orden = next(o for o in self.ordenes if o["id"] == orden_id)
        return {"name": orden["name"], "state": orden["state"], "amount_total": 0.0}


class Validar(unittest.TestCase):
    def setUp(self):
        self.cat = P.Catalogo.de_json(catalogo_json())

    def test_acepta_lotes_completos_y_toma_el_precio_del_catalogo(self):
        c = cuerpo("CALZ-1-NEG-41", "OFI-9-NEG", notas="  entregar \n el lunes ")
        c["lineas"][0]["precio"] = 1.0                      # un precio del navegador se ignora
        llave, notas, lineas, subs = P.validar(c, self.cat)
        self.assertEqual((llave, notas, subs), ("llave-de-prueba-0001", "entregar el lunes", []))
        self.assertEqual([(ln.sku, ln.pedido, ln.precio) for ln in lineas], [("CALZ-1-NEG-41", 30, 700.0), ("OFI-9-NEG", 40, 186.0)])

    def _rechazo(self, c) -> P.Rechazo:
        with self.assertRaises(P.Rechazo) as r:
            P.validar(c, self.cat)
        return r.exception

    def test_no_se_venden_piezas_sueltas(self):
        c = cuerpo("CALZ-1-NEG-41")
        c["lineas"][0]["cantidad"] = 5
        self.assertEqual((self._rechazo(c).status, self._rechazo(c).codigo), (422, "pedido_invalido"))

    def test_catalogo_viejo_pide_recargar(self):
        c = cuerpo("CALZ-1-NEG-41", version="20261001-000000")
        c["lineas"][0]["cantidad"] = 28                    # el lote de un corte anterior
        self.assertEqual((self._rechazo(c).status, self._rechazo(c).codigo), (409, "catalogo_viejo"))

    def test_con_catalogo_viejo_pero_lotes_iguales_pasa(self):
        self.assertEqual(len(P.validar(cuerpo("CALZ-1-NEG-41", version="otro"), self.cat)[2]), 1)

    def test_rechaza_lo_que_no_se_puede_pedir(self):
        casos = {"sin precio": cuerpo("DEC-9-AMA"), "repetido": cuerpo("CALZ-1-NEG-41", "CALZ-1-NEG-41"),
                 "sin llave": {**cuerpo("CALZ-1-NEG-41"), "llave": "x"}, "vacío": {**cuerpo(), "lineas": []},
                 "fuera del catálogo": {**cuerpo(), "lineas": [{"sku": "NO-EXISTE", "cantidad": 1}]},
                 "cantidad booleana": {**cuerpo(), "lineas": [{"sku": "CALZ-1-NEG-41", "cantidad": True}]}}
        for nombre, c in casos.items():
            with self.subTest(nombre):
                self.assertEqual(self._rechazo(c).status, 422)

    def test_tope_de_renglones(self):
        c = {**cuerpo(), "lineas": [{"sku": f"X-{i}", "cantidad": 1} for i in range(P.MAX_LINEAS + 1)]}
        self.assertEqual((self._rechazo(c).status, self._rechazo(c).codigo), (413, "limite_pedido"))

    def test_folio_estable_por_tienda_y_llave(self):
        h = P.huella_codigo(" tda-prue-ba22 ")
        self.assertEqual(h, P.huella_codigo(CODIGO))
        self.assertEqual(P.folio_de(h, "a" * 12), P.folio_de(h, "a" * 12))
        self.assertNotEqual(P.folio_de(h, "a" * 12), P.folio_de(h, "b" * 12))
        self.assertRegex(P.folio_de(h, "a" * 12), r"^MAY-[A-Z2-7]{8}$")


class Repartir(unittest.TestCase):
    def _linea(self, pedido: int) -> P.Linea:
        return P.Linea(sku="A", pedido=pedido, precio=10.0, titulo="a")

    def test_toma_de_los_dos_almacenes_hasta_el_lote(self):
        ln = self._linea(50)
        partes = P.repartir([ln], {"A": [7]}, {7: {TEX: 20, TEX2: 100}}, ALMACENES)
        self.assertEqual({a: [(r.sku, pid, n) for r, pid, n in v] for a, v in partes.items()}, {TEX: [("A", 7, 20)], TEX2: [("A", 7, 30)]})

    def test_lo_que_no_existe_en_odoo_no_se_reparte(self):
        ln = self._linea(50)
        self.assertEqual(P.repartir([ln], {}, {}, ALMACENES), {})
        self.assertFalse(ln.en_odoo)

    def test_un_reintento_solo_pide_lo_que_falta(self):
        ln = self._linea(50)
        ln.colocado.append(("S39001", 20))                  # TEXCO ya quedó en el intento anterior
        partes = P.repartir([ln], {"A": [7]}, {7: {TEX: 0, TEX2: 100}}, ALMACENES)
        self.assertEqual([(a, n) for a, v in partes.items() for _r, _p, n in v], [(TEX2, 30)])

    def test_libre_negativo_cuenta_como_cero(self):
        self.assertEqual(P.repartir([self._linea(5)], {"A": [7]}, {7: {TEX: -3, TEX2: 0}}, ALMACENES), {})


class Cerrar(unittest.TestCase):
    def _lineas(self):
        completa = P.Linea("A", 10, 1.0, "a", en_odoo=True, colocado=[("S1", 10)])
        corta = P.Linea("B", 10, 1.0, "b", en_odoo=True, colocado=[("S1", 4)])
        agotada = P.Linea("C", 10, 1.0, "c", en_odoo=True)
        manual = P.Linea("D", 10, 1.0, "d", en_odoo=False)
        return completa, corta, agotada, manual

    def test_confirmada_dice_lo_que_odoo_dejo(self):
        completa, corta, agotada, manual = self._lineas()
        self.assertEqual(P.cerrar([completa, manual], True, {"S1": "sale"}), "apartado")
        self.assertEqual(P.cerrar([completa, corta, agotada, manual], True, {"S1": "sale"}), "parcial")
        self.assertEqual([(ln.estado, ln.apartado) for ln in (completa, corta, agotada, manual)],
                         [("apartado", 10), ("parcial", 4), ("sin_existencia", 0), ("por_confirmar", None)])

    def test_si_odoo_no_confirmo_no_se_dice_apartado(self):
        completa, *_ = self._lineas()
        self.assertEqual(P.cerrar([completa], True, {"S1": "draft"}), "en_revision")
        self.assertEqual((completa.estado, completa.apartado), ("en_revision", None))

    def test_en_borrador_todo_va_a_revision(self):
        completa, corta, agotada, manual = self._lineas()
        self.assertEqual(P.cerrar([completa, corta, agotada, manual], False, {"S1": "draft"}), "en_revision")

    def test_sin_nada_en_odoo(self):
        _, _, agotada, manual = self._lineas()
        self.assertEqual(P.cerrar([agotada], True, {}), "sin_existencia")
        self.assertEqual(P.cerrar([manual], True, {}), "por_confirmar")
        self.assertEqual(P.cerrar([agotada, manual], False, {}), "por_confirmar")


class Avisos(unittest.TestCase):
    def test_lo_que_no_existe_en_odoo_va_completo_aunque_no_quepa_en_un_mensaje(self):
        lineas = [P.Linea(f"SKU-{i:04d}", 10, 5.0, "Producto de prueba " + "x" * 40, en_odoo=False, estado="por_confirmar")
                  for i in range(300)]
        mensajes = P.mensajes_slack("Tienda Uno", "MAY-ABCDEFGH", "por_confirmar", lineas, [], "", [])
        self.assertGreater(len(mensajes), 1)
        self.assertTrue(all(len(m) <= 3600 for m in mensajes))
        junto = "\n".join(mensajes)
        self.assertTrue(all(f"`SKU-{i:04d}`" in junto for i in range(300)))
        self.assertIn("NO EXISTEN EN ODOO", junto)
        self.assertIn("Tienda Uno", mensajes[0])


class Camino(unittest.TestCase):
    """`servicio.procesar` de punta a punta contra el Odoo de mentira."""

    def setUp(self):
        import servicio as S
        self.S = S
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.avisos: list[list[str]] = []
        self._avisar = S.avisar
        S.avisar = lambda cfg, mensajes: self.avisos.append(mensajes) or True
        self.addCleanup(lambda: setattr(S, "avisar", self._avisar))
        self.tienda = S.Tienda("Tienda Uno", P.huella_codigo(CODIGO), partner_id=55)
        self.cat = P.Catalogo.de_json(catalogo_json())

    def _cfg(self, confirmar: bool):
        return self.S.Config(datos=Path(self.tmp.name), pedidos="activo", confirmar=confirmar, almacenes=ALMACENES)

    def _pedido(self):
        return P.validar(cuerpo("CALZ-1-NEG-41", "CALZ-1-NEG-42", "MUE-1-MUL", "OFI-9-NEG"), self.cat)

    def _odoo(self, **kw) -> OdooDeMentira:
        return OdooDeMentira({"CALZ-1-NEG-41": 1, "CALZ-1-NEG-42": 2, "MUE-1-MUL": 3},
                             {1: {TEX: 30, TEX2: 0}, 2: {TEX: 12, TEX2: 20}, 3: {TEX: 0, TEX2: 0}}, **kw)

    def test_aparta_lo_que_hay_y_deja_lo_demas_por_confirmar(self):
        odoo, (_llave, notas, lineas, subs) = self._odoo(), self._pedido()
        r = self.S.procesar(self._cfg(True), self.tienda, "MAY-AAAAAAAA", notas, lineas, subs, odoo=odoo)
        self.assertEqual(r["estado"], "parcial")
        self.assertEqual({ln["sku"]: (ln["estado"], ln["apartado"]) for ln in r["lineas"]},
                         {"CALZ-1-NEG-41": ("apartado", 30), "CALZ-1-NEG-42": ("parcial", 32),
                          "MUE-1-MUL": ("sin_existencia", 0), "OFI-9-NEG": ("por_confirmar", None)})
        self.assertEqual((r["piezas"], r["total"], r["ordenes"]), (62, 43400.0, ["S39001", "S39002"]))
        self.assertEqual([(o["client_order_ref"], o["warehouse_id"], o["partner_id"], o["state"]) for o in odoo.ordenes],
                         [("MAY-AAAAAAAA#1", TEX, 55, "sale"), ("MAY-AAAAAAAA#2", TEX2, 55, "sale")])
        self.assertEqual([(ln[2]["product_id"], ln[2]["product_uom_qty"], ln[2]["price_unit"]) for ln in odoo.ordenes[0]["order_line"]],
                         [(1, 30, 700.0), (2, 12, 700.0)])
        self.assertIn("`OFI-9-NEG` × 40", "\n".join(self.avisos[0]))
        self.assertEqual(self.S.guardado(self._cfg(True), "MAY-AAAAAAAA"), r)

    def test_en_borrador_no_dice_apartado(self):
        odoo, (_llave, notas, lineas, subs) = self._odoo(), self._pedido()
        r = self.S.procesar(self._cfg(False), self.tienda, "MAY-BBBBBBBB", notas, lineas, subs, odoo=odoo)
        self.assertEqual((r["estado"], r["piezas"], [o["state"] for o in odoo.ordenes]), ("en_revision", 0, ["draft", "draft"]))

    def test_si_odoo_no_confirma_se_reporta_como_quedo(self):
        odoo, (_llave, notas, lineas, subs) = self._odoo(confirma=False), self._pedido()
        r = self.S.procesar(self._cfg(True), self.tienda, "MAY-CCCCCCCC", notas, lineas, subs, odoo=odoo)
        self.assertEqual(r["estado"], "en_revision")

    def test_reintentar_tras_una_caida_crea_solo_lo_que_falta(self):
        odoo = self._odoo()
        odoo.falla_en = 2                                   # la primera orden nace; la segunda truena
        _llave, notas, lineas, subs = self._pedido()
        with self.assertRaises(RuntimeError):
            self.S.procesar(self._cfg(True), self.tienda, "MAY-DDDDDDDD", notas, lineas, subs, odoo=odoo)
        self.assertIsNone(self.S.guardado(self._cfg(True), "MAY-DDDDDDDD"))
        odoo.falla_en = None
        _llave, notas, lineas, subs = self._pedido()         # el navegador reenvía el mismo pedido
        r = self.S.procesar(self._cfg(True), self.tienda, "MAY-DDDDDDDD", notas, lineas, subs, odoo=odoo)
        self.assertEqual([o["client_order_ref"] for o in odoo.ordenes], ["MAY-DDDDDDDD#1", "MAY-DDDDDDDD#2"])
        self.assertEqual({ln["sku"]: ln["apartado"] for ln in r["lineas"]}["CALZ-1-NEG-42"], 32)
        self.assertEqual(sum(ln[2]["product_uom_qty"] for o in odoo.ordenes for ln in o["order_line"]), 62)


class Rutas(unittest.TestCase):
    """La aplicación completa: subir un catálogo, verlo y mandar pedidos."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.update({
            "MAYOREO_DATOS": cls.tmp.name, "MAYOREO_PEDIDOS": "activo", "MAYOREO_CONFIRMAR": "true",
            "MAYOREO_ADMIN_KEY": "llave-de-administracion-de-prueba", "SLACK_WEBHOOK_CATALOGO": "https://hooks.invalid/x",
            "ODOO_URL": "https://odoo.invalid", "ODOO_DB": "d", "ODOO_USER": "u", "ODOO_PASSWORD": "p",
            "MAYOREO_TIENDAS": json.dumps([{"nombre": "Tienda Uno", "hash": P.huella_codigo(CODIGO), "partner_id": 55},
                                           {"nombre": "Tienda Baja", "hash": P.huella_codigo("TDA-BAJA-0000"), "partner_id": 56, "activa": False}])})
        from fastapi.testclient import TestClient

        import app as A
        import servicio as S
        cls.A, cls.S = A, S
        cls.odoo = OdooDeMentira({"CALZ-1-NEG-41": 1, "CALZ-1-NEG-42": 2, "MUE-1-MUL": 3},
                                 {1: {TEX: 30, TEX2: 0}, 2: {TEX: 50, TEX2: 0}, 3: {TEX: 7200, TEX2: 0}})
        cls._odoo, cls._avisar = S.Odoo, S.avisar
        S.Odoo = lambda *a, **k: cls.odoo
        cls.avisos = []
        S.avisar = lambda cfg, mensajes: cls.avisos.append(mensajes) or True
        cls.c = TestClient(A.app)

    @classmethod
    def tearDownClass(cls):
        cls.S.Odoo, cls.S.avisar = cls._odoo, cls._avisar
        cls.tmp.cleanup()

    def setUp(self):
        self.A._envios._cubos.clear()
        self.A._fallos._cubos.clear()

    def _subir(self):
        adm = {"X-Admin-Key": "llave-de-administracion-de-prueba"}
        self.assertEqual(self.c.put("/api/mayoreo/admin/archivo/fotos/m000.jpg", content=b"\xff\xd8\xff\xe0jpeg", headers=adm).status_code, 200)
        self.assertEqual(self.c.put("/api/mayoreo/admin/archivo/catalogo.json", content=json.dumps(catalogo_json()), headers=adm).status_code, 200)
        return self.c.post("/api/mayoreo/admin/publicar", headers=adm)

    def test_1_sin_catalogo_no_hay_nada_que_ver(self):
        self.assertEqual(self.c.get("/catalogo.json").status_code, 503)
        self.assertEqual(self.c.get("/health").json(), {"ok": True, "catalogo": None})

    def test_2_la_administracion_pide_su_llave_y_no_deja_subir_cualquier_cosa(self):
        self.assertEqual(self.c.put("/api/mayoreo/admin/archivo/catalogo.json", content=b"{}").status_code, 401)
        adm = {"X-Admin-Key": "llave-de-administracion-de-prueba"}
        for ruta in ("index.html", "fotos/../../app.py", "fotos/m1.jpg", "pedidos/MAY-AAAAAAAA.json"):
            self.assertIn(self.c.put(f"/api/mayoreo/admin/archivo/{ruta}", content=b"x", headers=adm).status_code, (400, 404), ruta)
        self.assertEqual(self.c.put("/api/mayoreo/admin/archivo/fotos/m000.jpg", content=b"<html>", headers=adm).status_code, 400)
        self.assertEqual(self.c.post("/api/mayoreo/admin/publicar", headers=adm).status_code, 409)     # antesala incompleta

    def test_3_publicar_pone_el_catalogo_a_la_vista(self):
        r = self._subir()
        self.assertEqual((r.status_code, r.json()["productos"]), (200, 5))
        self.assertEqual(self.c.get("/catalogo.json").json()["version"], "20261012-120000")
        self.assertEqual(self.c.get("/fotos/m000.jpg?v=1").status_code, 200)
        self.assertEqual(self.c.get("/fotos/m999.jpg").status_code, 404)
        self.assertEqual(self.c.get("/api/mayoreo/estado").json(),
                         {"pedidos": "activo", "flujo": "instante", "vigencia": "", "max_lineas": 2000, "catalogo": "20261012-120000"})
        pagina = self.c.get("/")
        self.assertIn("KUBERA MAYOREO", pagina.text)
        self.assertIn("noindex", pagina.headers["x-robots-tag"])

    def test_4_codigos(self):
        self.assertEqual(self.c.post("/api/mayoreo/pedidos", json={**cuerpo("CALZ-1-NEG-41"), "codigo_tienda": "TDA-NOPE-0000"}).json()["detail"]["codigo"], "codigo_invalido")
        self.assertEqual(self.c.post("/api/mayoreo/pedidos", json={**cuerpo("CALZ-1-NEG-41"), "codigo_tienda": "TDA-BAJA-0000"}).status_code, 403)
        for _ in range(4):
            self.c.post("/api/mayoreo/pedidos", json={**cuerpo("CALZ-1-NEG-41"), "codigo_tienda": "x"})
        r = self.c.post("/api/mayoreo/pedidos", json=cuerpo("CALZ-1-NEG-41"))      # ni con el código bueno: ya se frenó
        self.assertEqual((r.status_code, r.json()["detail"]["codigo"]), (429, "limite_peticiones"))
        self.assertEqual(self.odoo.ordenes, [])

    def test_5_un_pedido_aparta_y_reenviarlo_no_duplica(self):
        c = cuerpo("CALZ-1-NEG-41", "MUE-1-MUL", "OFI-9-NEG", subcategorias=[{"categoria": "Hogar", "subcategoria": "Cocina"}])
        r = self.c.post("/api/mayoreo/pedidos", json=c)
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual((d["estado"], d["piezas"], d["ordenes"]), ("apartado", 7230, ["S39001"]))
        self.assertRegex(d["folio"], r"^MAY-[A-Z2-7]{8}$")
        self.assertEqual(self.c.post("/api/mayoreo/pedidos", json=c).json(), d)
        self.assertEqual(len(self.odoo.ordenes), 1)
        self.assertEqual(len(self.avisos), 1)
        self.assertIn("Subcategorías completas: Cocina (Hogar)", self.avisos[0][0])
        lista = self.c.get("/api/mayoreo/admin/pedidos", headers={"X-Admin-Key": "llave-de-administracion-de-prueba"}).json()
        self.assertEqual([(p["folio"], p["tienda"], p["estado"]) for p in lista], [(d["folio"], "Tienda Uno", "apartado")])

    def test_6_cuerpo_demasiado_grande(self):
        self.assertEqual(self.c.post("/api/mayoreo/pedidos", content=b"x" * 500_000, headers={"Content-Type": "application/json"}).status_code, 413)


class Pausa(unittest.TestCase):
    def test_pedir_activo_a_medio_configurar_deja_en_pausa(self):
        import servicio as S
        cfg = S.Config.del_entorno({"MAYOREO_PEDIDOS": "activo", "ODOO_URL": "https://o"})
        self.assertEqual(cfg.modo(), "pausa")
        self.assertEqual(len(cfg.faltantes()), 3)
        self.assertEqual(S.Config.del_entorno({}).modo(), "demo")
        self.assertEqual(S.Config.del_entorno({"MAYOREO_TIENDAS": "esto no es json", "MAYOREO_PEDIDOS": "activo"}).tiendas, [])


if __name__ == "__main__":
    unittest.main()
