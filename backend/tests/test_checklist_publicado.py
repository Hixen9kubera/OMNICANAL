"""Checklist · lo PUBLICADO: los atributos de las publicaciones vivas de ML.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Se lee en lotes de 20, con el token de la cuenta dueña.** El multiget de
   ML (`/items?ids=`) acepta 20 publicaciones por llamada, y con el token de la
   otra cuenta contesta 403 por cada item ajeno.
2. **Manda UNA publicación por SKU**: la `active` (si no, la `paused`) más
   reciente. No se mezclan: un SKU reciclado puede ser otro producto en la otra
   cuenta (EST-0091). Una cerrada no cuenta.
3. **Lo leído se recuerda 30 min**; «fresco» lo vuelve a pedir. Lo que ML no
   devolvió también se recuerda (vacío), para no preguntarlo en cada recarga.
4. **Si ML no contesta, el tablero sigue**: avisa, pausa SOLO a esa cuenta, y
   enseña lo último que se leyó en vez de borrarlo — también con «fresco».
5. **El Checklist nunca renueva tokens**: ante un 401 relee de la base; si no
   hay uno nuevo, se rinde (lo renueva la próxima venta).
6. **Cuenta como lleno** en el tablero, pero kubera manda cuando tiene valor.
7. **La celda que salió en verde, intacta, NO es un cambio** al cargar: se
   compara contra la FOTO que viaja en el propio archivo, no contra ML ni
   contra kubera de nuevo. Una corregida sí es cambio; una que Excel pasó a
   notación científica es error.
8. El «no aplica» de ML (`value_id = -1`) no cuenta como dato.

Sin red ni base de datos.

    cd backend && python -m unittest tests.test_checklist_publicado -v
"""
from __future__ import annotations

import io
import unittest
from unittest import mock

from services import checklist as ck


def _item(iid: str, estado: str, **attrs: str) -> dict:
    return {"code": 200, "body": {"id": iid, "status": estado, "attributes": [
        {"id": k, "value_id": "1", "value_name": v} for k, v in attrs.items()]}}


class _Limpio(unittest.TestCase):
    def setUp(self):
        ck._pub_cache.clear()
        ck._pub_pausa_hasta.clear()
        self.addCleanup(ck._pub_cache.clear)
        self.addCleanup(ck._pub_pausa_hasta.clear)
        self.addCleanup(mock.patch.stopall)


class AtributosDelItem(unittest.TestCase):
    def test_no_aplica_y_vacios_no_cuentan(self):
        body = {"attributes": [
            {"id": "BRAND", "value_id": "9", "value_name": "Ferrahome"},
            {"id": "GTIN", "value_id": "-1", "value_name": None},
            {"id": "MODEL", "value_id": "-1", "value_name": "No aplica"},
            {"id": "COLOR", "value_id": None, "value_name": "  "},
            {"id": "LENGTH", "value_id": None, "value_name": "30 cm"},
        ]}
        self.assertEqual(ck._atributos_item(body), {"BRAND": "Ferrahome", "LENGTH": "30 cm"})


class Multiget(_Limpio):
    def setUp(self):
        super().setUp()
        from services import meli
        self.acceso = mock.patch.object(meli, "_access_token", return_value="tok").start()
        self.releer = mock.patch.object(meli, "releer_token", return_value="nuevo").start()
        self.renovar = mock.patch.object(
            meli, "refrescar_token",
            side_effect=AssertionError("el Checklist no debe renovar tokens")).start()

    def _resp(self, codigo, cuerpo=None):
        r = mock.Mock(status_code=codigo)
        r.json.return_value = cuerpo or []
        r.raise_for_status.side_effect = (
            None if codigo < 400 else RuntimeError(f"HTTP {codigo}"))
        return r

    def test_pide_los_ids_juntos_con_el_token_de_la_cuenta(self):
        with mock.patch("httpx.get", return_value=self._resp(200, [
                _item("MLM1", "active", BRAND="A"),
                {"code": 403, "body": {"message": "forbidden"}}])) as get:
            res = ck._multiget("BEKURA", ["MLM1", "MLM2"])
        self.acceso.assert_called_once_with("BEKURA")
        self.assertEqual(get.call_args.kwargs["params"]["ids"], "MLM1,MLM2")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(res["MLM1"], {"estado": "active", "atributos": {"BRAND": "A"}})
        # El ajeno también queda, vacío: no se vuelve a preguntar.
        self.assertEqual(res["MLM2"], {"estado": None, "atributos": {}})

    def test_401_relee_de_la_base_y_reintenta_sin_renovar(self):
        with mock.patch("httpx.get", side_effect=[
                self._resp(401), self._resp(200, [_item("MLM1", "active")])]) as get:
            ck._multiget("BEKURA", ["MLM1"])
        self.releer.assert_called_once_with("BEKURA")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer nuevo")

    def test_401_sin_token_nuevo_se_rinde(self):
        self.releer.return_value = "tok"          # la base tiene el mismo
        with mock.patch("httpx.get", return_value=self._resp(401)) as get, \
             self.assertRaises(RuntimeError):
            ck._multiget("BEKURA", ["MLM1"])
        self.assertEqual(get.call_count, 1)

    def test_sin_token_levanta(self):
        self.acceso.return_value = None
        with mock.patch("httpx.get") as get, self.assertRaises(RuntimeError):
            ck._multiget("BEKURA", ["MLM1"])
        get.assert_not_called()


class Publicados(_Limpio):
    def setUp(self):
        super().setUp()
        self.listings = {
            "SKU-1": [("BEKURA", "MLM1"), ("SANCORFASHION", "MLM2")],
            "SKU-2": [("BEKURA", "MLM3")],
        }
        self.pubs = mock.patch.object(
            ck, "_publicaciones_de",
            side_effect=lambda skus: {s.upper(): self.listings[s.upper()] for s in skus
                                      if s.upper() in self.listings}).start()
        self.items = {
            "MLM1": {"estado": "paused", "atributos": {"BRAND": "de-pausada", "MODEL": "M1"}},
            "MLM2": {"estado": "active", "atributos": {"BRAND": "de-activa"}},
            "MLM3": {"estado": "closed", "atributos": {"BRAND": "de-cerrada"}},
        }
        self.caidas: set[str] = set()

        def multiget(cuenta, ids):
            if cuenta in self.caidas:
                raise RuntimeError("timeout")
            return {i: self.items[i] for i in ids}
        self.multiget = mock.patch.object(ck, "_multiget", side_effect=multiget).start()

    def test_manda_una_sola_la_activa_y_la_cerrada_no_cuenta(self):
        salida, info = ck._publicados(["SKU-1", "SKU-2"])
        # MODEL de la pausada NO se cuela: no se mezclan publicaciones.
        self.assertEqual(salida, {"SKU-1": {"BRAND": "de-activa"}})
        self.assertEqual((info["publicaciones"], info["vivas"], info["error"]), (3, 2, None))

    def test_entre_iguales_gana_la_primera_de_kubera(self):
        self.items["MLM2"]["estado"] = "paused"
        salida, _ = ck._publicados(["SKU-1"])
        self.assertEqual(salida["SKU-1"], {"BRAND": "de-pausada", "MODEL": "M1"})

    def test_agrupa_por_cuenta(self):
        ck._publicados(["SKU-1", "SKU-2"])
        llamadas = sorted((c.args[0], sorted(c.args[1])) for c in self.multiget.call_args_list)
        self.assertEqual(llamadas, [("BEKURA", ["MLM1", "MLM3"]), ("SANCORFASHION", ["MLM2"])])

    def test_lotes_de_veinte(self):
        ids = [f"MLM{n}" for n in range(45)]
        self.listings = {f"S{n}": [("BEKURA", i)] for n, i in enumerate(ids)}
        self.items = {i: {"estado": "active", "atributos": {}} for i in ids}
        ck._publicados([f"S{n}" for n in range(45)])
        self.assertEqual(sorted(len(c.args[1]) for c in self.multiget.call_args_list),
                         [5, 20, 20])

    def test_cache_y_fresco(self):
        ck._publicados(["SKU-1"])
        ck._publicados(["SKU-1"])
        self.assertEqual(self.multiget.call_count, 2)          # una por cuenta, una vez
        ck._publicados(["SKU-1"], fresco=True)
        self.assertEqual(self.multiget.call_count, 4)

    def test_cache_vencido_se_vuelve_a_pedir(self):
        ck._publicados(["SKU-2"])
        with mock.patch.object(ck, "_PUB_TTL_S", 0.0):
            ck._publicados(["SKU-2"])
        self.assertEqual(self.multiget.call_count, 2)

    def test_ml_caido_avisa_pausa_y_no_insiste(self):
        self.caidas = {"BEKURA", "SANCORFASHION"}
        salida, info = ck._publicados(["SKU-1"])
        self.assertEqual(salida, {})
        self.assertIn("no contestó", info["error"])
        llamadas = self.multiget.call_count
        salida, info = ck._publicados(["SKU-1"])
        self.assertEqual(self.multiget.call_count, llamadas)   # en pausa
        self.assertIn("no contestó", info["error"])

    def test_la_pausa_es_por_cuenta(self):
        self.caidas = {"BEKURA"}
        ck._publicados(["SKU-1"])
        self.multiget.reset_mock()
        self.caidas = set()
        ck._publicados(["SKU-1"], fresco=True)
        # BEKURA sigue en pausa; SANCORFASHION se vuelve a pedir.
        self.assertEqual([c.args[0] for c in self.multiget.call_args_list], ["SANCORFASHION"])

    def test_sin_respuesta_se_enseña_lo_ultimo_leido(self):
        ck._publicados(["SKU-1"])
        self.caidas = {"BEKURA", "SANCORFASHION"}
        # «Recargar» (fresco) con ML caído: lo publicado NO se borra.
        salida, info = ck._publicados(["SKU-1"], fresco=True)
        self.assertEqual(salida, {"SKU-1": {"BRAND": "de-activa"}})
        self.assertIn("lo último que se leyó", info["error"])
        # Y durante la pausa, igual.
        salida, _ = ck._publicados(["SKU-1"], fresco=True)
        self.assertEqual(salida, {"SKU-1": {"BRAND": "de-activa"}})

    def test_kubera_caida_no_rompe(self):
        self.pubs.side_effect = RuntimeError("pool agotado")
        salida, info = ck._publicados(["SKU-1"])
        self.assertEqual(salida, {})
        self.assertIsNotNone(info["error"])
        self.multiget.assert_not_called()

    def test_sin_skus_no_consulta(self):
        self.assertEqual(ck._publicados([])[0], {})
        self.pubs.assert_not_called()


def _campo(campo, nivel, tipo="string"):
    return {"campo": campo, "etiqueta": campo.title(), "tipo": tipo, "jerarquia": None,
            "unidad_default": None, "valores": [], "unidades": [], "nivel": nivel,
            "exigido": nivel in ("ml", "matriz"), "por_omision": None}


def _ctx(valores=None, publicados=None, almacen=None):
    campos = [_campo("BRAND", "ml"), _campo("MODEL", "ml"), _campo("COLOR", "principal"),
              _campo("GTIN", "principal")]
    return {
        "cats_por_sku": {"SKU-1": {"categoria": "MLM1", "fuente": "panel"}},
        "campos": {"MLM1": campos}, "matriz": {}, "nombres": {},
        "valores": {"SKU-1": valores or {}},
        "publicados": {"SKU-1": publicados or {}},
        "publicados_info": {},
        "almacen": {"SKU-1": almacen if almacen is not None else {
            k: 1 for k in ck._LOG_CLAVES}},
        "titulos": {},
    }


class Evaluar(unittest.TestCase):
    def test_lo_publicado_llena(self):
        f = ck._evaluar("SKU-1", _ctx(valores={"MODEL": "X"},
                                      publicados={"BRAND": "B", "COLOR": "Rojo"}))
        self.assertEqual(f["estado"], "completo")
        self.assertEqual((f["exigidos_llenos"], f["exigidos_publicados"]), (2, 1))
        self.assertEqual(f["opcionales_llenos"], 1)

    def test_kubera_manda_sobre_lo_publicado(self):
        ctx = _ctx(valores={"BRAND": "Ferrahome"}, publicados={"BRAND": "Otra"})
        self.assertEqual(ck._valor_efectivo(ctx, "SKU-1", "BRAND"), ("Ferrahome", "kubera"))
        self.assertEqual(ck._valor_efectivo(ctx, "sku-1", "MODEL"), ("", ""))

    def test_sin_nada_falta(self):
        f = ck._evaluar("SKU-1", _ctx())
        self.assertEqual([x["campo"] for x in f["faltan_ml"]], ["BRAND", "MODEL"])
        self.assertEqual(f["exigidos_publicados"], 0)


class Importar(unittest.TestCase):
    def _importar(self, filas: list[tuple], ctx, publicado: bool = True):
        cab = "sku,campo,valor,origen,publicado\n" if publicado else "sku,campo,valor\n"
        cuerpo = "".join(",".join(f) + "\n" for f in filas)
        with mock.patch.object(ck, "_canonicos", return_value=({"SKU-1": "SKU-1"}, [])), \
             mock.patch.object(ck, "_contexto", return_value=ctx) as contexto:
            r = ck.importar_sync((cab + cuerpo).encode("utf-8"), "lote.csv", aplicar=False)
        # La carga no le pregunta nada a ML: compara contra la foto del archivo.
        self.assertFalse(contexto.call_args.kwargs.get("con_publicado", True))
        return r

    def test_celda_verde_intacta_no_es_cambio(self):
        r = self._importar([("SKU-1", "BRAND", "Marca Viva", "publicacion", "Marca Viva"),
                            ("SKU-1", "COLOR", "Rojo", "", "")], _ctx())
        self.assertTrue(r["ok"])
        self.assertEqual(r["sin_cambios"], 1)
        self.assertEqual([(c["campo"], c["despues"]) for c in r["cambios"]],
                         [("COLOR", "Rojo")])

    def test_verde_intacta_no_pisa_lo_capturado_despues(self):
        # Se descargó con BRAND vacío en kubera (salió verde) y ANTES de cargar
        # alguien capturó «Ferrahome» en el cajón: la celda intacta no lo pisa.
        r = self._importar([("SKU-1", "BRAND", "Marca Viva", "publicacion", "Marca Viva")],
                           _ctx(valores={"BRAND": "Ferrahome"}))
        self.assertEqual((r["cambios"], r["sin_cambios"]), ([], 1))

    def test_verde_corregida_si_es_cambio(self):
        r = self._importar([("SKU-1", "BRAND", "Ferrahome", "publicacion", "Marca Viva")],
                           _ctx())
        self.assertEqual([(c["campo"], c["antes"], c["despues"]) for c in r["cambios"]],
                         [("BRAND", "", "Ferrahome")])

    def test_sin_foto_el_valor_se_toma_como_escrito(self):
        # Un CSV armado por un script (sin columna «publicado»): lo que trae va.
        r = self._importar([("SKU-1", "BRAND", "Marca Viva")], _ctx(), publicado=False)
        self.assertEqual([c["despues"] for c in r["cambios"]], ["Marca Viva"])

    def test_excel_reescribio_el_numero(self):
        r = self._importar([("SKU-1", "COLOR", "1.5", "publicacion", "1.50")], _ctx())
        self.assertEqual((r["cambios"], r["sin_cambios"]), ([], 1))

    def test_notacion_cientifica_es_error(self):
        r = self._importar([("SKU-1", "GTIN", "7.50123E+12", "", "")], _ctx())
        self.assertEqual(r["cambios"], [])
        self.assertIn("notación científica", r["errores"][0]["motivo"])


class FotoDelExcel(unittest.TestCase):
    def test_la_hoja_oculta_viaja_y_no_se_lee_como_datos(self):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Micrófonos"
        ws.append(["sku", "titulo", "sistema", "BRAND"])
        ws.append(["SKU", "Producto", "En sistema", "Marca"])
        ws.append(["no lo cambies", "", "", "Obligatorio ML"])
        ws.append(["SKU-1", "Micrófono", "—", "Marca Viva"])
        foto = wb.create_sheet("_publicado")
        foto.sheet_state = "hidden"
        foto.append(["sku", "campo", "valor"])
        foto.append(["SKU-1", "BRAND", "Marca Viva"])
        buf = io.BytesIO()
        wb.save(buf)
        leida: dict = {}
        celdas, errores = ck._celdas_xlsx(buf.getvalue(), leida)
        self.assertEqual(errores, [])
        self.assertEqual(celdas, [("SKU-1", "BRAND", "Marca Viva", "Micrófonos", 4)])
        self.assertEqual(leida, {("SKU-1", "BRAND"): "Marca Viva"})


if __name__ == "__main__":
    unittest.main()
