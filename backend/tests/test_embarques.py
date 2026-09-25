"""Pruebas de los EMBARQUES del filtro de Costos (packing lists + costos).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **El extractor** (`services.embarques`): códigos ISO 6346 con dígito
   verificador, el dedazo de 5 letras (OOULU → OOLU), referencias (guía, BL,
   027F, SZLS, ONE*, COSU, PRY25-543) y el número de contenedor de Kubera. Con
   los NOMBRES REALES de los packing lists y de `costos_validados.contenedor`.
2. **La agrupación**: el mismo embarque con dos códigos se junta por N; una
   referencia sin N la hereda de su Ferraforme; N discrepantes no se adivinan;
   costos sin packing sigue en el filtro; Ferraforme sin N se llave por código.
3. **La caché** de 30 s y que `forzar` la rehace.
4. **El SQL**: el filtro de embarque, «sin contenedor» y los conteos, SIN
   EXISTS correlacionado (en prod agotó el statement_timeout).
5. **El router**: `/costos/_embarques` no cae en `/costos/{sku}`, y sin kubera
   (rama MySQL) el filtro de embarque es 503, no la lista completa.

Sin red: nada aquí habla con kubera ni con MySQL.

    cd backend && python -m unittest tests.test_embarques -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import crear as ruta_crear  # noqa: E402
from services import costing_read  # noqa: E402
from services import embarques as emb  # noqa: E402


def _sql(llamada) -> str:
    return " ".join(llamada[0][0].split())


SHA_1 = "1" * 64
SHA_2 = "2" * 64
SHA_3 = "3" * 64
SHA_O = "a" * 64


def _ferra(sha, nombre, miembro=None, originales=(), skus=10):
    return {"sha": sha, "nombre": nombre, "miembro": miembro,
            "originales": list(originales), "skus": skus}


def _por_clave(grupos):
    return {g["clave"]: g for g in grupos}


# ─────────────────────────────────────────────────────────────────────────────
# 1. EXTRACTOR
# ─────────────────────────────────────────────────────────────────────────────
class Extractor(unittest.TestCase):
    def test_iso_pegado_a_texto(self):
        # Sin límite izquierdo ni derecho de letras: el nombre pega el código.
        self.assertEqual(emb.codigos("EITU1380423-ListaEmpaque"), ["EITU1380423"])
        self.assertEqual(emb.codigos("TGBU7274837Lista de empaque"), ["TGBU7274837"])

    def test_prefijo_de_cinco_letras_se_corrige(self):
        # Un dedazo en el nombre: se quita UNA letra hasta que valide.
        self.assertEqual(emb.codigos("ooulu9155398"), ["OOLU9155398"])
        self.assertEqual(emb.codigos("PHPCU4654366 PL contenedor 84"), ["HPCU4654366"])
        self.assertEqual(emb.numero("PHPCU4654366 PL contenedor 84"), 84)

    def test_iso_valido_antes_que_referencia(self):
        self.assertEqual(emb.codigos("PRY25-617 HPCU4654366 PL"),
                         ["HPCU4654366", "PRY25-617"])
        self.assertIsNone(emb.numero("PRY25-617 HPCU4654366 PL"))
        # contenedor_base guardado dice SZLS50216600: el ISO del nombre es otro.
        self.assertEqual(emb.codigos("SZLS50216600=CIPL(1)  FFAU4457148"),
                         ["FFAU4457148", "SZLS50216600"])

    def test_guia_iso_y_numero(self):
        t = "256059868 TRHU6215242 contenedor 1"
        self.assertEqual(emb.codigos(t), ["TRHU6215242", "256059868"])
        self.assertEqual(emb.numero(t), 1)
        t = "149504112538 TIIU6522619 contenedor 12"
        self.assertEqual(emb.codigos(t), ["TIIU6522619", "149504112538"])
        self.assertEqual(emb.numero(t), 12)

    def test_cont_abreviado(self):
        self.assertEqual(emb.numero("Cont 95 …"), 95)
        self.assertEqual(emb.numero("Cont. 95 lista"), 95)
        self.assertEqual(emb.numero("mi_contenedor_80.xlsx"), 80)
        self.assertEqual(emb.codigos("Cont 95 …"), [])

    def test_referencia_027f(self):
        self.assertEqual(emb.codigos("027F655823=C&PL"), ["027F655823"])

    def test_cosu_no_suelta_un_iso_de_adentro(self):
        t = "COSU6422667920 FSCU8687417 contenedor 29"
        self.assertEqual(emb.codigos(t), ["FSCU8687417", "COSU6422667920"])
        self.assertEqual(emb.numero(t), 29)
        self.assertEqual(emb.codigos("COSU6422667920"), ["COSU6422667920"])

    def test_iso_que_no_valida_va_despues(self):
        # OULU9155398 no valida y no hay letra de más que quitar: se acepta,
        # pero detrás de los válidos.
        self.assertFalse(emb.iso_valido("OULU9155398"))
        self.assertEqual(emb.codigos("OULU9155398 TRHU6215242"),
                         ["TRHU6215242", "OULU9155398"])

    def test_digito_verificador(self):
        for c in ("CSQU3054383", "TGBU7274837", "EITU1380423", "OOLU9155398",
                  "HPCU4654366", "FFAU4457148", "TRHU6215242", "MRKU3436938",
                  "UETU7935912", "BEAU6268641", "TCNU3635850", "TGHU6894814"):
            self.assertTrue(emb.iso_valido(c), c)
        self.assertFalse(emb.iso_valido("CSQU3054384"))
        self.assertFalse(emb.iso_valido("PRY25-543"))

    def test_sin_duplicados(self):
        self.assertEqual(emb.codigos("TGBU7274837 tgbu7274837 TGBU7274837Lista"),
                         ["TGBU7274837"])


class SepararValorCostos(unittest.TestCase):
    def test_sufijo_espaciado_siempre(self):
        self.assertEqual(emb.separar_valor_costos("256059868 - 1"), ("256059868", 1))
        self.assertEqual(emb.separar_valor_costos("UETU7935912 - 103"), ("UETU7935912", 103))
        # La basura también: su N manda y el texto se enseña tal cual.
        self.assertEqual(emb.separar_valor_costos("INHERIT(ACC-0703-CAF) - 54"),
                         ("INHERIT(ACC-0703-CAF)", 54))

    def test_sufijo_pegado_solo_con_codigo_reconocido(self):
        self.assertEqual(emb.separar_valor_costos("COSU6422667920-29"),
                         ("COSU6422667920", 29))
        self.assertEqual(emb.separar_valor_costos("ONEYNB5BEK841700-24"),
                         ("ONEYNB5BEK841700", 24))
        # PRY25-543 y LEX25-510 son referencias enteras, no base+N.
        self.assertEqual(emb.separar_valor_costos("PRY25-543"), ("PRY25-543", None))
        self.assertEqual(emb.separar_valor_costos("LEX25-510"), ("LEX25-510", None))

    def test_sin_sufijo(self):
        self.assertEqual(emb.separar_valor_costos("MRKU3436938"), ("MRKU3436938", None))
        self.assertEqual(emb.separar_valor_costos("  MRKU3436938 "), ("MRKU3436938", None))


# ─────────────────────────────────────────────────────────────────────────────
# 2. AGRUPACIÓN
# ─────────────────────────────────────────────────────────────────────────────
class Agrupar(unittest.TestCase):
    def test_guia_de_costos_y_iso_del_ferraforme_se_juntan_por_n(self):
        grupos = emb.agrupar(
            [_ferra(SHA_1, "256059868 TRHU6215242 contenedor 1.xlsx",
                    originales=["256059868=CIPL.xlsx"], skus=560)],
            [{"valor": "256059868 - 1", "skus": 578}])
        self.assertEqual(len(grupos), 1)
        g = grupos[0]
        self.assertEqual(g["clave"], "n:1")
        self.assertEqual(g["numero"], 1)
        self.assertEqual(g["etiqueta"], "1 · TRHU6215242 · 256059868")
        self.assertEqual(g["codigos"], ["TRHU6215242", "256059868"])
        self.assertEqual(g["valores_costos"], ["256059868 - 1"])
        self.assertEqual(g["shas"], [SHA_1])
        self.assertEqual(g["fuentes"], ["costos", "packing"])

    def test_referencia_sin_n_hereda_la_de_su_ferraforme(self):
        # PRY25-543 solo aparece en el ORIGINAL emparejado: igual se liga.
        grupos = emb.agrupar(
            [_ferra(SHA_1, "CSQU3054383 contenedor 75.xlsx",
                    originales=["PRY25-543 CIPL.xlsx"])],
            [{"valor": "PRY25-543", "skus": 40}])
        g = _por_clave(grupos)
        self.assertEqual(list(g), ["n:75"])
        self.assertEqual(g["n:75"]["valores_costos"], ["PRY25-543"])
        self.assertIn("PRY25-543", g["n:75"]["codigos"])
        self.assertEqual(g["n:75"]["etiqueta"], "75 · CSQU3054383 · PRY25-543")

    def test_herencia_por_texto_compacto(self):
        # «LEX25 510» con espacio en el nombre: el texto compacto lo contiene.
        grupos = emb.agrupar(
            [_ferra(SHA_1, "Lista LEX25 - 510 cont 74.xlsx")],
            [{"valor": "LEX25-510", "skus": 5}])
        self.assertEqual([g["clave"] for g in grupos], ["n:74"])

    def test_un_bl_con_dos_ferraforme_da_dos_grupos(self):
        grupos = emb.agrupar(
            [_ferra(SHA_1, "149504230930 TIIU6522619 contenedor 64.xlsx"),
             _ferra(SHA_2, "149504230930 Contenedor 68.xlsx")],
            [{"valor": "149504230930 - 68", "skus": 90}])
        g = _por_clave(grupos)
        self.assertEqual([x["clave"] for x in grupos], ["n:68", "n:64"])   # mayor arriba
        self.assertEqual(g["n:68"]["fuentes"], ["costos", "packing"])
        self.assertEqual(g["n:68"]["valores_costos"], ["149504230930 - 68"])
        self.assertEqual(g["n:68"]["shas"], [SHA_2])
        self.assertEqual(g["n:64"]["fuentes"], ["packing"])
        self.assertEqual(g["n:64"]["valores_costos"], [])

    def test_n_discrepantes_no_se_adivinan(self):
        # El BL sin N lo reclaman el 64 y el 68: se queda con su código.
        grupos = emb.agrupar(
            [_ferra(SHA_1, "149504230930 contenedor 64.xlsx"),
             _ferra(SHA_2, "149504230930 contenedor 68.xlsx")],
            [{"valor": "149504230930", "skus": 3}])
        g = _por_clave(grupos)
        self.assertIn("c:149504230930", g)
        self.assertEqual(g["c:149504230930"]["valores_costos"], ["149504230930"])
        self.assertEqual(g["n:64"]["valores_costos"], [])
        self.assertEqual(g["n:68"]["valores_costos"], [])

    def test_costos_sin_packing_sigue_en_el_filtro(self):
        grupos = emb.agrupar([], [{"valor": "BEAU6268641 - 97", "skus": 312}])
        self.assertEqual(len(grupos), 1)
        g = grupos[0]
        self.assertEqual((g["clave"], g["etiqueta"]), ("n:97", "97 · BEAU6268641"))
        self.assertEqual(g["fuentes"], ["costos"])
        self.assertEqual(g["shas"], [])

    def test_ferraforme_sin_n_se_llave_por_codigo(self):
        grupos = emb.agrupar(
            [_ferra(SHA_1, "MRKU3436938 PL.xlsx", originales=["027F655823=C&PL.xlsx"])],
            [{"valor": "MRKU3436938", "skus": 12}])
        self.assertEqual(len(grupos), 1)
        g = grupos[0]
        self.assertEqual(g["clave"], "c:MRKU3436938")
        self.assertIsNone(g["numero"])
        self.assertEqual(g["etiqueta"], "MRKU3436938 · 027F655823")
        self.assertEqual(g["fuentes"], ["costos", "packing"])

    def test_n_reclamada_por_dos_valores_la_basura_no_es_codigo(self):
        grupos = emb.agrupar(
            [], [{"valor": "INHERIT(ACC-0703-CAF) - 54", "skus": 300},
                 {"valor": "TCNU3635850 - 54", "skus": 20}])
        self.assertEqual(len(grupos), 1)
        g = grupos[0]
        # La basura pesa más, pero no es código: ni etiqueta ni `codigos`.
        self.assertEqual(g["etiqueta"], "54 · TCNU3635850")
        self.assertEqual(g["codigos"], ["TCNU3635850"])
        # El filtro sí la necesita: el valor crudo se conserva.
        self.assertEqual(g["valores_costos"],
                         ["INHERIT(ACC-0703-CAF) - 54", "TCNU3635850 - 54"])

    def test_base_de_costos_aporta_los_codigos_que_trae_dentro(self):
        grupos = emb.agrupar([], [{"valor": "OOULU9155398 - 9", "skus": 5},
                                  {"valor": "BASURA SIN CODIGO", "skus": 2}])
        g = {x["clave"]: x for x in grupos}
        # Dedazo de cinco letras corregido, igual que en los Ferraforme.
        self.assertEqual(g["n:9"]["codigos"], ["OOLU9155398"])
        self.assertEqual(g["n:9"]["etiqueta"], "9 · OOLU9155398")
        # Sin N ni código: la clave (su base) es lo único que lo nombra.
        self.assertEqual(g["c:BASURA SIN CODIGO"]["codigos"], [])
        self.assertEqual(g["c:BASURA SIN CODIGO"]["etiqueta"], "BASURA SIN CODIGO")

    def test_etiqueta_maximo_dos_codigos_y_peso(self):
        grupos = emb.agrupar(
            [_ferra(SHA_1, "SZLS50216600 FFAU4457148 contenedor 88.xlsx", skus=100)],
            [{"valor": "SZLS50216600 - 88", "skus": 1507},
             {"valor": "027F655823 - 88", "skus": 3}])
        g = grupos[0]
        self.assertEqual(g["etiqueta"], "88 · FFAU4457148 · SZLS50216600")
        self.assertEqual(g["codigos"], ["FFAU4457148", "SZLS50216600", "027F655823"])

    def test_dedazo_de_cinco_letras_en_la_etiqueta(self):
        grupos = emb.agrupar([_ferra(SHA_1, "OOULU9155398 contenedor 90.xlsx")], [])
        self.assertEqual(grupos[0]["etiqueta"], "90 · OOLU9155398")

    def test_n_en_el_nombre_del_zip_no_en_el_miembro(self):
        grupos = emb.agrupar(
            [_ferra(SHA_1, "Cont 95 Ferraforme.zip", miembro="HPCU4654366.xlsx")], [])
        self.assertEqual(grupos[0]["clave"], "n:95")
        self.assertEqual(grupos[0]["etiqueta"], "95 · HPCU4654366")

    def test_orden_con_n_mayor_primero_luego_alfabetico(self):
        grupos = emb.agrupar(
            [_ferra(SHA_1, "CSQU3054383 contenedor 12.xlsx"),
             _ferra(SHA_2, "TGBU7274837 contenedor 103.xlsx")],
            [{"valor": "MRKU3436938", "skus": 1}, {"valor": "BEAU6268641", "skus": 1},
             {"valor": "UETU7935912 - 80", "skus": 1}])
        self.assertEqual([g["clave"] for g in grupos],
                         ["n:103", "n:80", "n:12", "c:BEAU6268641", "c:MRKU3436938"])

    def test_indexar(self):
        ag = emb.indexar(emb.agrupar(
            [_ferra(SHA_1, "256059868 TRHU6215242 contenedor 1.xlsx")],
            [{"valor": "256059868 - 1", "skus": 5}]), 7.0)
        self.assertEqual(ag.por_valor, {"256059868 - 1": "n:1"})
        self.assertEqual(ag.por_sha, {SHA_1: "n:1"})
        self.assertIn("n:1", ag.por_clave)
        self.assertEqual(ag.generado, 7.0)


# ─────────────────────────────────────────────────────────────────────────────
# 3. LECTURA Y CACHÉ
# ─────────────────────────────────────────────────────────────────────────────
class Lectura(unittest.TestCase):
    def test_leer_fuentes_arma_nombres_y_cuenta_todo(self):
        def _fa(sql, params=None):
            s = " ".join(sql.split())
            if "from costing.packing_ubicaciones u" in s:
                return [{"sha": SHA_1, "skus": 7, "originales": [SHA_O]}]
            if "from costing.packing_archivos a" in s:
                return [
                    {"tipo": "ferraforme", "sha": SHA_1, "nombre": "viejo.xlsx",
                     "miembro": None},
                    {"tipo": "ferraforme", "sha": SHA_1,
                     "nombre": "256059868 TRHU6215242 contenedor 1.xlsx", "miembro": None},
                    {"tipo": "original", "sha": SHA_O, "nombre": "256059868.zip",
                     "miembro": "CIPL.xlsx"},
                ]
            if "from costing.costos_validados" in s:
                return [{"valor": "256059868 - 1", "skus": 578}]
            raise AssertionError(s)

        with mock.patch.object(emb.sdb, "fetch_all", side_effect=_fa) as fa:
            ferras, valores = emb.leer_fuentes()
        self.assertEqual(ferras, [{
            "sha": SHA_1, "nombre": "256059868 TRHU6215242 contenedor 1.xlsx",
            "miembro": None, "originales": ["256059868.zip CIPL.xlsx"], "skus": 7}])
        self.assertEqual(valores, [{"valor": "256059868 - 1", "skus": 578}])
        ubic = _sql(fa.call_args_list[0])
        # TODAS las filas: el Ferraforme afirma el contenedor aunque no esté alineado.
        self.assertNotIn("alineado", ubic)
        self.assertIn('count(distinct lower(u.sku::text) collate "C") as skus', ubic)
        self.assertIn("group by u.ferraforme_sha256", ubic)
        arch = _sql(fa.call_args_list[1])
        self.assertIn("where a.sha256 = any(%s::text[])", arch)
        # No se usa el contenedor_base guardado (está mal en varios).
        self.assertNotIn("contenedor_base", arch)
        self.assertEqual(sorted(fa.call_args_list[1][0][1][0]), [SHA_1, SHA_O])
        cost = _sql(fa.call_args_list[2])
        self.assertIn("where contenedor is not null and contenedor <> ''", cost)

    def test_cache_30_s_y_forzar(self):
        emb.limpiar_cache()
        self.addCleanup(emb.limpiar_cache)
        fuentes = ([], [{"valor": "BEAU6268641 - 97", "skus": 1}])
        with mock.patch.object(emb, "leer_fuentes", return_value=fuentes) as lf, \
                mock.patch.object(emb, "time") as reloj:
            reloj.monotonic.side_effect = [100.0, 110.0, 131.0, 132.0]
            a = emb.agrupacion()
            b = emb.agrupacion()                 # 10 s después: caché
            self.assertIs(a, b)
            self.assertEqual(lf.call_count, 1)
            emb.agrupacion()                     # 31 s: se rehace
            self.assertEqual(lf.call_count, 2)
            emb.agrupacion(forzar=True)          # 1 s después, pero forzado
            self.assertEqual(lf.call_count, 3)


# ─────────────────────────────────────────────────────────────────────────────
# 4. SQL DEL LISTADO Y DE LOS CONTEOS
# ─────────────────────────────────────────────────────────────────────────────
def _agrupacion_prueba():
    return emb.indexar(emb.agrupar(
        [_ferra(SHA_1, "256059868 TRHU6215242 contenedor 1.xlsx"),
         _ferra(SHA_2, "149504230930 contenedor 64.xlsx")],
        [{"valor": "256059868 - 1", "skus": 578},
         {"valor": "BEAU6268641 - 97", "skus": 312}]))


class ListadoEmbarque(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(costing_read.embarques, "agrupacion",
                              return_value=_agrupacion_prueba())
        p.start()
        self.addCleanup(p.stop)

    def _listar(self, embarque, filas=()):
        with mock.patch.object(costing_read.sdb, "fetch_scalar", return_value=len(filas)) as fs, \
                mock.patch.object(costing_read.sdb, "fetch_all",
                                  side_effect=[list(filas), []]) as fa:
            rows, total = costing_read.listado(
                1, 40, None, None, "reciente", [], False, None, False, embarque)
        return rows, total, fs, fa

    def test_grupo_con_las_dos_fuentes(self):
        _, _, fs, fa = self._listar("n:1")
        cuenta = _sql(fs.call_args)
        frag = ("(v.contenedor = any(%s::text[]) or p.sku in (select u.sku from "
                "costing.packing_ubicaciones u where u.ferraforme_sha256 = any(%s::text[])))")
        self.assertIn(frag, cuenta)
        self.assertIn(frag, _sql(fa.call_args_list[0]))
        self.assertNotIn("exists", cuenta)
        self.assertEqual(fs.call_args[0][1], (["256059868 - 1"], [SHA_1]))
        # La consulta de filas lleva el canal antes y la paginación después.
        self.assertEqual(fa.call_args_list[0][0][1],
                         ("mercado_libre", ["256059868 - 1"], [SHA_1], 40, 0))

    def test_grupo_solo_costos_o_solo_packing(self):
        _, _, fs, _ = self._listar("n:97")
        self.assertIn("where (v.contenedor = any(%s::text[]))", _sql(fs.call_args))
        self.assertNotIn("packing_ubicaciones", _sql(fs.call_args))
        _, _, fs, _ = self._listar("n:64")
        self.assertIn("where (p.sku in (select u.sku from costing.packing_ubicaciones u "
                      "where u.ferraforme_sha256 = any(%s::text[])))", _sql(fs.call_args))
        self.assertEqual(fs.call_args[0][1], ([SHA_2],))

    def test_sin_contenedor(self):
        _, _, fs, fa = self._listar("sin")
        cuenta = _sql(fs.call_args)
        self.assertIn("coalesce(v.contenedor, '') = '' and not exists (select 1 "
                      "from costing.packing_ubicaciones u where u.sku = p.sku)", cuenta)
        self.assertNotIn(" not in ", cuenta)
        self.assertEqual(fs.call_args[0][1], ())

    def test_clave_desconocida_da_cero_sin_consultar(self):
        rows, total, fs, fa = self._listar("n:9999")
        self.assertEqual((rows, total), ([], 0))
        fs.assert_not_called()
        fa.assert_not_called()

    def test_llamada_por_posicion_de_los_scripts_no_cambia(self):
        # comparar_lecturas_costing.py y suite_caos_sandbox.py: 6 posicionales.
        with mock.patch.object(costing_read.sdb, "fetch_scalar", return_value=0) as fs, \
                mock.patch.object(costing_read.sdb, "fetch_all", return_value=[]):
            self.assertEqual(costing_read.listado(1, 1, None, None, "sku_asc", []), ([], 0))
        self.assertNotIn("packing_ubicaciones", _sql(fs.call_args))
        self.assertNotIn("where", _sql(fs.call_args))

    def test_embarques_por_fila_una_consulta_por_pagina(self):
        filas = [{"sku": "A-1", "contenedor": "256059868 - 1"},
                 {"sku": "B-2", "contenedor": "BEAU6268641 - 97"},
                 {"sku": "C-3", "contenedor": None},
                 {"sku": "D-4", "contenedor": "raro"}]
        packing = [{"sku": "a-1", "sha": SHA_1},     # citext: vuelve en otra caja
                   {"sku": "C-3", "sha": SHA_2}]
        with mock.patch.object(costing_read.sdb, "fetch_scalar", return_value=4), \
                mock.patch.object(costing_read.sdb, "fetch_all",
                                  side_effect=[filas, packing]) as fa:
            rows, _ = costing_read.listado(1, 40, None, None, "reciente", [])
        self.assertEqual(fa.call_count, 2)
        q = _sql(fa.call_args_list[1])
        self.assertIn("from costing.packing_ubicaciones u where u.sku = any(%s::citext[])", q)
        self.assertEqual(fa.call_args_list[1][0][1], (["A-1", "B-2", "C-3", "D-4"],))
        por = {r["sku"]: r["embarques"] for r in rows}
        self.assertEqual(por["A-1"], [{"clave": "n:1", "etiqueta": "1 · TRHU6215242 · 256059868",
                                       "fuente": "ambos"}])
        self.assertEqual(por["B-2"], [{"clave": "n:97", "etiqueta": "97 · BEAU6268641",
                                       "fuente": "costos"}])
        self.assertEqual(por["C-3"], [{"clave": "n:64", "etiqueta": "64 · 149504230930",
                                       "fuente": "packing"}])
        self.assertEqual(por["D-4"], [])

    def test_embarques_por_fila_caidos_no_tumban_el_listado(self):
        filas = [{"sku": "A-1", "contenedor": "256059868 - 1"}]
        with mock.patch.object(costing_read.sdb, "fetch_scalar", return_value=1), \
                mock.patch.object(costing_read.sdb, "fetch_all",
                                  side_effect=[filas, RuntimeError("sin packing")]):
            rows, total = costing_read.listado(1, 40, None, None, "reciente", [])
        self.assertEqual(total, 1)
        self.assertIsNone(rows[0]["embarques"])


class Conteos(unittest.TestCase):
    def test_una_sola_consulta_sin_exists_en_or(self):
        ag = _agrupacion_prueba()
        with mock.patch.object(costing_read.sdb, "fetch_all", return_value=[]) as fa:
            costing_read.conteos_embarques(ag.grupos)
        self.assertEqual(fa.call_count, 1)
        sql = _sql(fa.call_args)
        # El único EXISTS es el anti-join de «sin» (unido con AND), nunca dentro de un OR.
        self.assertEqual(sql.count("exists"), 1)
        self.assertNotIn("or exists", sql)
        self.assertNotIn("or not exists", sql)
        self.assertIn("unnest(%s::text[], %s::text[]) as t(clave, valor)", sql)
        self.assertIn("unnest(%s::text[], %s::text[]) as t(clave, sha)", sql)
        self.assertIn("join costing.costos_validados v on v.contenedor = g.valor", sql)
        self.assertIn("join costing.packing_ubicaciones u on u.ferraforme_sha256 = g.sha", sql)
        self.assertIn("join core.products p on p.sku = m.sku", sql)
        self.assertIn("select m.clave, count(*) as n, "
                      "count(*) filter (where v.sku is null) as sin_costo", sql)
        self.assertNotIn("count(distinct", sql)
        self.assertIn("union all select 'sin' as clave", sql)
        self.assertIn("where coalesce(v.contenedor, '') = '' and not exists (select 1 "
                      "from costing.packing_ubicaciones u where u.sku = p.sku)", sql)
        cl_c, val_c, cl_p, sha_p = fa.call_args[0][1]
        self.assertEqual(list(zip(cl_c, val_c)),
                         [("n:97", "BEAU6268641 - 97"), ("n:1", "256059868 - 1")])
        self.assertEqual(list(zip(cl_p, sha_p)), [("n:64", SHA_2), ("n:1", SHA_1)])

    def test_respuesta_quita_los_grupos_vacios(self):
        ag = _agrupacion_prueba()
        filas = [{"clave": "n:1", "n": 578, "sin_costo": 4},
                 {"clave": "n:64", "n": 0, "sin_costo": 0},
                 {"clave": "sin", "n": 3747, "sin_costo": 2900}]
        with mock.patch.object(costing_read.embarques, "agrupacion", return_value=ag) as ga, \
                mock.patch.object(costing_read.sdb, "fetch_all", return_value=filas):
            r = costing_read.embarques_con_conteos()
        ga.assert_called_once_with(forzar=True)          # siempre rehace la caché
        self.assertEqual([e["clave"] for e in r["embarques"]], ["n:1"])   # n:97 y n:64 = 0
        self.assertEqual(r["embarques"][0], {
            "clave": "n:1", "etiqueta": "1 · TRHU6215242 · 256059868", "numero": 1,
            "codigos": ["TRHU6215242", "256059868"], "n": 578, "sin_costo": 4,
            "fuentes": ["costos", "packing"]})
        self.assertEqual(r["sin_contenedor"], {"clave": "sin", "n": 3747, "sin_costo": 2900})
        self.assertTrue(r["generado_en"].endswith("+00:00"))


# ─────────────────────────────────────────────────────────────────────────────
# 5. ROUTER
# ─────────────────────────────────────────────────────────────────────────────
class Router(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(ruta_crear.router)
        self.c = TestClient(app)

    def test_mysql_no_filtra_por_embarque(self):
        with mock.patch.object(ruta_crear.settings, "supabase_read_costing", False), \
                mock.patch.object(ruta_crear.db, "fetch_all") as fa, \
                mock.patch.object(ruta_crear.db, "fetch_scalar") as fs:
            r = self.c.get("/api/crear/costos", params={"embarque": "n:80"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("packing", r.json()["detail"])
        fa.assert_not_called()
        fs.assert_not_called()

    def test_mysql_sin_embarque_trae_embarques_null(self):
        fila = {"sku": "A-1", "nombre": "x", "contenedor": "UETU7935912 - 103",
                "largo": None, "alto": None, "ancho": None, "peso": None,
                "costo_producto": None, "costo_cbm": None, "costo_total": None,
                "costo_unitario": None, "precio_base": None, "precio_sugerido": None,
                "costo_comision": None, "costo_fee_envio": None, "ml_cat_id": None}
        with mock.patch.object(ruta_crear.settings, "supabase_read_costing", False), \
                mock.patch.object(ruta_crear.db, "fetch_all", return_value=[fila]), \
                mock.patch.object(ruta_crear.db, "fetch_scalar", return_value=1):
            r = self.c.get("/api/crear/costos")
        self.assertEqual(r.status_code, 200)
        item = r.json()["items"][0]
        self.assertIsNone(item["embarques"])
        self.assertEqual(item["contenedor"], "UETU7935912 - 103")

    def test_kubera_pasa_el_embarque_y_las_filas(self):
        fila = {"sku": "A-1", "nombre": "x", "contenedor": "256059868 - 1",
                "largo": None, "alto": None, "ancho": None, "peso": None,
                "costo_producto": None, "costo_cbm": None, "costo_total": None,
                "costo_unitario": None, "precio_base": None, "precio_sugerido": None,
                "ml_cat_id": None,
                "embarques": [{"clave": "n:1", "etiqueta": "1 · TRHU6215242",
                               "fuente": "ambos"}]}
        with mock.patch.object(ruta_crear.settings, "supabase_read_costing", True), \
                mock.patch.object(ruta_crear.costing_read, "listado",
                                  return_value=([fila], 1)) as li:
            r = self.c.get("/api/crear/costos", params={"embarque": "n:1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(li.call_args.kwargs, {"embarque": "n:1"})
        self.assertEqual(r.json()["items"][0]["embarques"], fila["embarques"])

    def test_embarques_sin_kubera_es_503_y_no_cae_en_el_detalle(self):
        # Si la ruta con parámetro la capturara, buscaría la bitácora de un SKU
        # llamado «_embarques» en MySQL y contestaría 404, no este 503.
        with mock.patch.object(ruta_crear.settings, "supabase_read_costing", False), \
                mock.patch.object(ruta_crear.db, "fetch_all", return_value=[]) as fa:
            r = self.c.get("/api/crear/costos/_embarques")
        self.assertEqual(r.status_code, 503)
        self.assertIn("embarques", r.json()["detail"])
        fa.assert_not_called()

    def test_embarques_con_kubera(self):
        cuerpo = {"embarques": [], "sin_contenedor": {"clave": "sin", "n": 0, "sin_costo": 0},
                  "generado_en": "2026-09-25T00:00:00+00:00"}
        with mock.patch.object(ruta_crear.settings, "supabase_read_costing", True), \
                mock.patch.object(ruta_crear.costing_read, "embarques_con_conteos",
                                  return_value=cuerpo) as ec:
            r = self.c.get("/api/crear/costos/_embarques")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), cuerpo)
        ec.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
