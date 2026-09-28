"""Checklist ↔ Catálogo Maestro (v0.581): semanas, detalle editable, Ferrahome.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **La palomita de cada semana sale de ops.checklist_lote**: una semana está
   «cargada» si tiene SKUs. 2026 tiene 53 semanas ISO.
2. **Ferrahome se OFRECE**: en los campos con valor por omisión va primero entre
   las sugerencias (ML solo sugiere marcas de terceros), sin tocar la caché
   compartida de `_campos_ml`. A MANUFACTURER solo se le SUGIERE la marca: sigue
   exigido, igual que en specs._estado (el Maestro y el Checklist no se
   contradicen).
3. **Guardar atributos en pantalla sigue las reglas del Excel**: normaliza por
   tipo, un campo vacío no borra, lo que no es de la categoría o está en
   notación científica es error, y con un error no se guarda NADA.
4. **El Catálogo Maestro filtra por la semana del Checklist**: fecha inválida
   = 400; semana sin SKUs = cero filas (no el piloto); los SKUs escritos mandan
   sobre la semana; más de 200 se recortan y se dice cuántos eran.

Sin red ni base de datos.

    cd backend && python -m unittest tests.test_checklist_semanas_editor -v
"""
from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import checklist as ck
from services import specs_editor


class Semanas(unittest.TestCase):
    def _q(self, cargadas):
        def q(sql, params=None):
            if "group by semana" in sql:
                return [{"semana": s, "n": n} for s, n in cargadas.items()]
            if "isoyear" in sql:
                return [{"anio": 2026}]
            if "max(semana)" in sql:
                return [{"s": max(cargadas) if cargadas else None}]
            raise AssertionError(sql)
        return mock.patch.object(ck, "_q", side_effect=q)

    def test_palomita_por_semana_y_53_semanas(self):
        w39 = dt.date(2026, 9, 21)
        with self._q({w39: 100}), \
             mock.patch.object(ck, "hoy", return_value=dt.date(2026, 9, 28)):
            r = ck.semanas_sync(2026)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["semanas"]), 53)
        s39 = r["semanas"][38]
        self.assertEqual((s39["numero"], s39["semana"], s39["skus"], s39["cargada"]),
                         (39, "2026-09-21", 100, True))
        self.assertFalse(r["semanas"][39]["cargada"])
        self.assertTrue(r["semanas"][39]["actual"])        # 28-sep es la 40
        self.assertEqual(r["ultima_cargada"], "2026-09-21")
        self.assertEqual(sum(1 for s in r["semanas"] if s["cargada"]), 1)

    def test_el_anio_pedido_no_se_vuelve_tope(self):
        # Mirar 2030 no debe volver 2030 un año «con lotes»: si no, el selector
        # deja avanzar sin fin.
        with self._q({dt.date(2026, 9, 21): 100}),              mock.patch.object(ck, "hoy", return_value=dt.date(2026, 9, 28)):
            r = ck.semanas_sync(2030)
        self.assertEqual(r["anios"], [2026])
        self.assertEqual(r["anio"], 2030)

    def test_sin_migracion_lo_dice(self):
        with mock.patch.object(ck, "_q", side_effect=ck.FaltaMigracion("0058")):
            r = ck.semanas_sync(2026)
        self.assertFalse(r["ok"])
        self.assertTrue(r["falta_migracion"])

    def test_semana_valida(self):
        self.assertEqual(ck.semana_valida("2026-09-24"), dt.date(2026, 9, 21))
        self.assertIsNone(ck.semana_valida("week 39"))
        self.assertIsNone(ck.semana_valida(""))


def _crudo(campo, obligatorio=False, valores=(), tipo="string", unidades=(), unidad_default=None):
    return {"campo": campo, "etiqueta": campo.title(), "obligatorio": obligatorio,
            "jerarquia": None, "tipo": tipo, "valores": list(valores),
            "unidades": list(unidades), "unidad_default": unidad_default}


class Ferrahome(unittest.TestCase):
    def test_va_primero_y_no_ensucia_la_cache(self):
        crudos = [_crudo("BRAND", True, ["Allen & Heath", "Behringer"])]
        cache = crudos[0]["valores"]
        campos = ck.campos_de("MLM1", crudos, {}, {"BRAND": "Ferrahome"})
        self.assertEqual(campos[0]["valores"], ["Ferrahome", "Allen & Heath", "Behringer"])
        self.assertEqual(campos[0]["nivel"], "auto")
        self.assertEqual(cache, ["Allen & Heath", "Behringer"])   # la caché intacta

    def test_no_se_repite_si_ml_ya_lo_sugiere(self):
        crudos = [_crudo("BRAND", True, ["Behringer", "Ferrahome"])]
        campos = ck.campos_de("MLM1", crudos, {}, {"BRAND": "Ferrahome"})
        self.assertEqual(campos[0]["valores"], ["Ferrahome", "Behringer"])

    def test_manufacturer_sigue_exigido_pero_se_le_sugiere_la_marca(self):
        reqs = {("mercado_libre", "MLM1"): [{"campo": "BRAND", "default": "Ferrahome"}]}
        with mock.patch.object(ck.specs, "_requisitos", return_value=reqs):
            auto = ck._automaticos(["MLM1"])
        self.assertEqual(auto["MLM1"], {"BRAND": "Ferrahome"})
        crudos = [_crudo("MANUFACTURER", True, ["Sony"])]
        m = ck.campos_de("MLM1", crudos, {}, auto["MLM1"])[0]
        self.assertEqual((m["nivel"], m["exigido"], m["por_omision"]), ("ml", True, None))
        self.assertEqual(m["valores"], ["Ferrahome", "Sony"])
        self.assertEqual(crudos[0]["valores"], ["Sony"])

    def test_el_cajon_del_maestro_tambien_la_ofrece(self):
        crudos = [_crudo("BRAND", True, ["Allen & Heath"]), _crudo("MODEL", True)]
        cache = crudos[0]["valores"]
        with mock.patch.object(specs_editor.specs, "_categorias",
                               return_value={"SKU-1": {"mercado_libre": {"categoria": "MLM1"}}}), \
             mock.patch.object(specs_editor.specs, "matriz",
                               return_value=[{"campo": "BRAND", "default": "Ferrahome",
                                              "fuente": "api", "obligatorio": True}]), \
             mock.patch.object(specs_editor, "_campos_ml", return_value=crudos), \
             mock.patch.object(specs_editor, "_guardados_sync", return_value=[]):
            r = specs_editor.editor_sync("SKU-1", "mercado_libre")
        marca = next(c for c in r["campos"] if c["campo"] == "BRAND")
        self.assertEqual((marca["por_omision"], marca["valores"][0]), ("Ferrahome", "Ferrahome"))
        self.assertIsNone(next(c for c in r["campos"] if c["campo"] == "MODEL")["por_omision"])
        self.assertEqual(cache, ["Allen & Heath"])

    def test_el_cajon_sugiere_la_marca_en_manufacturer_sin_volverlo_automatico(self):
        crudos = [_crudo("MANUFACTURER", True, ["Sony"])]
        with mock.patch.object(specs_editor.specs, "_categorias",
                               return_value={"SKU-1": {"mercado_libre": {"categoria": "MLM1"}}}),              mock.patch.object(specs_editor.specs, "matriz",
                               return_value=[{"campo": "BRAND", "default": "Ferrahome",
                                              "fuente": "api", "obligatorio": True}]),              mock.patch.object(specs_editor, "_campos_ml", return_value=crudos),              mock.patch.object(specs_editor, "_guardados_sync", return_value=[]):
            r = specs_editor.editor_sync("SKU-1", "mercado_libre")
        fab = r["campos"][0]
        self.assertEqual((fab["por_omision"], fab["valores"]), (None, ["Ferrahome", "Sony"]))


def _campo(campo, nivel="ml", tipo="string", valores=(), unidades=(), unidad_default=None):
    return {"campo": campo, "etiqueta": campo.title(), "tipo": tipo, "jerarquia": None,
            "unidad_default": unidad_default, "valores": list(valores),
            "unidades": list(unidades), "nivel": nivel,
            "exigido": nivel in ("ml", "matriz"), "por_omision": None}


def _ctx(valores=None, publicados=None):
    campos = [_campo("MODEL"), _campo("LENGTH", "principal", "number_unit",
                                        unidades=["cm", "mm"], unidad_default="cm"),
              _campo("IS_FOLDABLE", "principal", "boolean"),
              _campo("COLOR", "principal", valores=["Negro", "Rojo"])]
    return {"cats_por_sku": {"SKU-1": {"categoria": "MLM1"}}, "campos": {"MLM1": campos},
            "matriz": {}, "nombres": {"MLM1": {"nombre": "Micrófonos"}},
            "valores": {"SKU-1": valores or {}}, "publicados": {"SKU-1": publicados or {}},
            "publicados_info": {}, "almacen": {"SKU-1": {}}, "titulos": {}}


class Detalle(unittest.TestCase):
    def test_trae_valor_y_publicado_por_campo(self):
        with mock.patch.object(ck, "_canonicos", return_value=({"SKU-1": "SKU-1"}, [])), \
             mock.patch.object(ck, "_contexto",
                               return_value=_ctx({"MODEL": "M1"}, {"COLOR": "Negro"})):
            r = ck.detalle_sync("sku-1")
        por = {c["campo"]: c for c in r["campos"]}
        self.assertEqual((r["sku"], r["categoria_nombre"]), ("SKU-1", "Micrófonos"))
        self.assertEqual((por["MODEL"]["valor"], por["MODEL"]["publicado"]), ("M1", None))
        self.assertEqual((por["COLOR"]["valor"], por["COLOR"]["publicado"]), ("", "Negro"))

    def test_el_valor_sale_sin_espacios(self):
        # La pantalla compara contra él: con «M1 » marcaba un cambio que nadie hizo.
        with mock.patch.object(ck, "_canonicos", return_value=({"SKU-1": "SKU-1"}, [])),              mock.patch.object(ck, "_contexto", return_value=_ctx({"MODEL": "M1 "})):
            r = ck.detalle_sync("SKU-1")
        self.assertEqual(next(c for c in r["campos"] if c["campo"] == "MODEL")["valor"], "M1")
        self.assertEqual(r["fila"]["sku"], "SKU-1")

    def test_sku_desconocido(self):
        with mock.patch.object(ck, "_canonicos", return_value=({}, ["NOPE"])):
            self.assertFalse(ck.detalle_sync("NOPE")["ok"])


class GuardarAtributos(unittest.TestCase):
    def setUp(self):
        mock.patch.object(ck, "_canonicos", return_value=({"SKU-1": "SKU-1"}, [])).start()
        self.ctx = mock.patch.object(ck, "_contexto", return_value=_ctx({"MODEL": "M1"})).start()
        self.guardar = mock.patch.object(ck.specs_editor, "guardar_sync",
                                         return_value={"ok": True}).start()
        # La fila se re-evalúa con lo publicado de la caché: sin red aquí.
        mock.patch.object(ck, "_publicados", return_value=({}, {})).start()
        self.addCleanup(mock.patch.stopall)

    def test_normaliza_y_manda_solo_lo_que_cambio(self):
        r = ck.guardar_atributos_sync("SKU-1", {
            "MODEL": "M1",            # igual a lo guardado: no viaja
            "LENGTH": "30",           # sin unidad: la que ML asume
            "IS_FOLDABLE": "si",      # → Sí
            "COLOR": "negro",         # → Negro, como lo escribe ML
            "EMPTY": "",              # vacío: se ignora (no borra)
        })
        self.assertTrue(r["ok"])
        (sku, canal, valores, etiquetas), _ = self.guardar.call_args
        self.assertEqual(valores, {"LENGTH": "30 cm", "IS_FOLDABLE": "Sí", "COLOR": "Negro"})
        self.assertEqual(r["guardados"], 3)
        self.assertEqual([a["campo"] for a in r["avisos"]], ["LENGTH"])
        # La fila vuelve re-evaluada con lo recién guardado: MODEL ya estaba,
        # así que no falta nada exigido.
        self.assertEqual(r["fila"]["faltan_ml"], [])
        self.assertEqual(self.ctx.call_count, 1)        # una sola lectura

    def test_un_error_no_guarda_nada(self):
        r = ck.guardar_atributos_sync("SKU-1", {"COLOR": "Rojo", "IS_FOLDABLE": "quizá",
                                                "GTIN": "7.50123E+12", "INVENTADO": "x"})
        self.assertFalse(r["ok"])
        # GTIN no es de esta categoría (aquí): también es error.
        self.assertEqual(sorted(e["campo"] for e in r["errores"]),
                         ["GTIN", "INVENTADO", "IS_FOLDABLE"])
        self.guardar.assert_not_called()

    def test_notacion_cientifica_es_error(self):
        ctx = _ctx()
        ctx["campos"]["MLM1"].append(_campo("GTIN", "principal"))
        self.ctx.return_value = ctx
        r = ck.guardar_atributos_sync("SKU-1", {"GTIN": "7.50123E+12"})
        self.assertFalse(r["ok"])
        self.assertIn("notación científica", r["errores"][0]["motivo"])
        self.guardar.assert_not_called()

    def test_sin_categoria_no_guarda(self):
        ctx = _ctx()
        ctx["cats_por_sku"] = {"SKU-1": {}}
        self.ctx.return_value = ctx
        r = ck.guardar_atributos_sync("SKU-1", {"MODEL": "X"})
        self.assertFalse(r["ok"])
        self.guardar.assert_not_called()

    def test_sin_cambios_no_escribe(self):
        r = ck.guardar_atributos_sync("SKU-1", {"MODEL": "M1", "COLOR": ""})
        self.assertEqual((r["ok"], r["guardados"]), (True, 0))
        self.guardar.assert_not_called()

    def test_si_la_base_falla_lo_dice(self):
        self.guardar.return_value = {"ok": False, "motivo": "kubera caída"}
        r = ck.guardar_atributos_sync("SKU-1", {"MODEL": "M2"})
        self.assertEqual((r["ok"], r["motivo"]), (False, "kubera caída"))


class MaestroPorSemana(unittest.TestCase):
    def setUp(self):
        from routers import inventario as ruta
        app = FastAPI()
        app.include_router(ruta.router)
        self.c = TestClient(app)
        self.inv = ruta.inv
        self.filas = mock.patch.object(ruta.inv, "filas",
                                       side_effect=lambda skus: [{"sku": s} for s in (skus or ["PILOTO"])]).start()
        mock.patch.object(ruta.inv, "resumen", return_value={}).start()
        self.lote = mock.patch.object(ck, "skus_de_semana", return_value=["A-1", "B-2"]).start()
        self.addCleanup(mock.patch.stopall)

    def test_trae_el_lote_de_la_semana(self):
        r = self.c.get("/api/inventario", params={"semana": "2026-09-24"}).json()
        self.assertEqual([f["sku"] for f in r["items"]], ["A-1", "B-2"])
        self.assertEqual((r["semana"], r["etiqueta"], r["semana_total"], r["es_piloto"]),
                         ("2026-09-21", "Week 39", 2, False))
        self.lote.assert_called_once_with(dt.date(2026, 9, 21))

    def test_fecha_invalida_es_400(self):
        self.assertEqual(self.c.get("/api/inventario", params={"semana": "week39"}).status_code, 400)

    def test_semana_vacia_no_es_el_piloto(self):
        self.lote.return_value = []
        r = self.c.get("/api/inventario", params={"semana": "2026-09-21"}).json()
        self.assertEqual((r["items"], r["semana_total"]), ([], 0))
        self.filas.assert_not_called()

    def test_los_skus_escritos_mandan(self):
        r = self.c.get("/api/inventario", params={"semana": "2026-09-21", "skus": "Z-9"}).json()
        self.assertEqual([f["sku"] for f in r["items"]], ["Z-9"])
        self.lote.assert_not_called()
        self.assertNotIn("semana", r)

    def test_mas_de_200_se_recortan_y_se_dice(self):
        self.lote.return_value = [f"S-{i}" for i in range(250)]
        r = self.c.get("/api/inventario", params={"semana": "2026-09-21"}).json()
        self.assertEqual((len(r["items"]), r["semana_total"]), (200, 250))

    def test_sin_nada_sigue_el_piloto(self):
        r = self.c.get("/api/inventario").json()
        self.assertTrue(r["es_piloto"])
        self.lote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
