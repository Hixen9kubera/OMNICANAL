"""Pruebas de la referencia de Ferraforme (0057, etapa 4).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Alinear es cotejar TEXTO en la misma fila.** Una fila de Ferraforme solo
   ubica al SKU si el original dice lo mismo en las columnas de texto que
   comparten. Los números no cuentan (al descombinar, Alma copia el valor del
   ancla y el CTNS difiere sin que la fila sea otra), y un desfase del
   encabezado corre todas las filas por igual.
2. **Solo se usa tal cual:** el original tiene que estar entre los archivos de
   la escalera, ser la MISMA versión cotejada y ser uno solo.
3. **La foto es la segunda opinión:** si la foto no decide, decide Ferraforme y
   la IA ni se llama; si coinciden, gana la foto (aprobable en lote); si
   discrepan, gana la foto con confianza baja (sin lote) y se dice qué dijo
   Ferraforme.

Sin red.    cd backend && python -m unittest tests.test_packing_ferraforme -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import packing_ferraforme as pf, packing_publicados as pp  # noqa: E402

ENC_ORIG = ("图片 picture", "英文品名 description", "中文品名 description", "箱数 ctns")
ENC_FERRA = ("图片 picture", "sku oddo", "sku", "英文品名 description",
             "中文品名 description", "箱数 ctns")


def _orig(filas: dict[int, tuple], enc: int = 1) -> pf.Tabla:
    return pf.Tabla(enc, list(ENC_ORIG), filas)


def _ferra(filas: dict[int, tuple], enc: int = 1) -> pf.Tabla:
    return pf.Tabla(enc, list(ENC_FERRA), filas)


def _mapa(u):
    return [(x["sku"], x["original_fila"], x["cotejo"]) for x in u]


class Ubicar(unittest.TestCase):
    def test_misma_fila_mismo_texto_alinea(self):
        o = _orig({2: (None, "Chair", "椅子", 1), 3: (None, "Lamp", "灯", 2)})
        f = _ferra({2: (None, "MUE-0302-MAD", "REBI1", "Chair", "椅子", 1.0),
                    3: (None, "ILU-0010-NEG", "REBI2", "Lamp", "灯", 2)})
        u = pf.ubicar(f, o)
        self.assertEqual(_mapa(u), [("MUE-0302-MAD", 2, "fila"), ("ILU-0010-NEG", 3, "fila")])
        self.assertEqual(u[0]["codigo_proveedor"], "REBI1")

    def test_numero_distinto_si_alinea(self):
        # CTNS 1 contra 3: el ancla de una celda descombinada. La fila es la misma.
        o = _orig({2: (None, "Chair", "椅子", 1), 3: (None, "Lamp", "灯", 2)})
        f = _ferra({2: (None, "MUE-0302-MAD", None, "Chair", "椅子", 3),
                    3: (None, "ILU-0010-NEG", None, "Lamp", "灯", 2)})
        self.assertTrue(all(x["alineado"] for x in pf.ubicar(f, o)))

    def test_desfase_de_encabezado(self):
        o = _orig({2: (None, "Chair", "椅子", 1), 3: (None, "Lamp", "灯", 2)}, enc=1)
        f = _ferra({4: (None, "MUE-0302-MAD", None, "Chair", "椅子", 1),
                    5: (None, "ILU-0010-NEG", None, "Lamp", "灯", 2)}, enc=3)
        self.assertEqual(_mapa(pf.ubicar(f, o)),
                         [("MUE-0302-MAD", 2, "fila"), ("ILU-0010-NEG", 3, "fila")])

    def test_en_desorden_se_ubica_por_texto_unico(self):
        # Alma reordenó: ninguna fila coincide en su lugar, pero cada nombre
        # aparece UNA vez en el original.
        o = _orig({2: (None, "Chair", "椅子", 1), 3: (None, "Lamp", "灯", 2),
                   4: (None, "Desk", "桌子", 1)})
        f = _ferra({2: (None, "ILU-0010-NEG", None, "Lamp", "灯", 2),
                    3: (None, "OFI-0020-MAD", None, "Desk", "桌子", 1),
                    4: (None, "MUE-0302-MAD", None, "Chair", "椅子", 1)})
        self.assertEqual(_mapa(pf.ubicar(f, o)), [("ILU-0010-NEG", 3, "texto"),
                                                  ("OFI-0020-MAD", 4, "texto"),
                                                  ("MUE-0302-MAD", 2, "texto")])

    def test_lo_repetido_no_se_asigna(self):
        # Mil renglones "鞋子" que solo cambian de talla: que uno coincida en su
        # fila es casualidad, y por texto no es único. No se asigna nada.
        o = _orig({2: (None, "Shoes", "鞋子", 1), 3: (None, "Shoes", "鞋子", 1)})
        f = _ferra({2: (None, "CAL-0001-NEG-26", None, "Hat", "帽子", 1),
                    3: (None, "CAL-0001-NEG-27", None, "Shoes", "鞋子", 1),
                    4: (None, "CAL-0001-NEG-28", None, "Shoes", "鞋子", 1)})
        self.assertFalse(any(x["alineado"] for x in pf.ubicar(f, o)))

    def test_columna_retocada_no_estorba(self):
        # Alma llenó la guía donde el original traía "/": esa columna se ignora.
        enc_o = list(ENC_ORIG) + ["guia"]
        enc_f = list(ENC_FERRA) + ["guia"]
        o = pf.Tabla(1, enc_o, {2: (None, "Chair", "椅子", 1, "/"), 3: (None, "Lamp", "灯", 2, "/")})
        f = pf.Tabla(1, enc_f, {2: (None, "MUE-0302-MAD", None, "Chair", "椅子", 1, "SE250686648MX"),
                                3: (None, "ILU-0010-NEG", None, "Lamp", "灯", 2, "SE250686649MX")})
        self.assertEqual(_mapa(pf.ubicar(f, o)), [("MUE-0302-MAD", 2, "fila"),
                                                  ("ILU-0010-NEG", 3, "fila")])

    def test_la_columna_de_sku_se_elige_por_contenido(self):
        # "SKU" a secas trae el código del proveedor; el nuestro va en "SKU DOO".
        enc = ["sku", "sku doo", "英文品名 description"]
        f = pf.Tabla(1, enc, {2: ("REBI1", "MUE-0302-MAD", "Chair"),
                              3: ("REBI2", "ILU-0010-NEG", "Lamp")})
        u = pf.ubicar(f, None)
        self.assertEqual([(x["sku"], x["codigo_proveedor"]) for x in u],
                         [("MUE-0302-MAD", "REBI1"), ("ILU-0010-NEG", "REBI2")])

    def test_sin_original_nada_alinea_y_basura_no_es_sku(self):
        f = _ferra({2: (None, "MUE-0302-MAD/CAF", None, "Chair", "椅子", 1),
                    3: (None, 'TEC-1614-NEG-TV7"', None, "TV", "电视", 1),
                    4: (None, "TOTAL", None, None, None, 9),
                    5: (None, None, None, "x", "y", 1)})
        u = pf.ubicar(f, None)
        self.assertEqual([x["sku"] for x in u], ["MUE-0302-MAD/CAF", 'TEC-1614-NEG-TV7"'])
        self.assertFalse(any(x["alineado"] for x in u))

    def test_sin_columna_sku_no_hay_ubicaciones(self):
        self.assertEqual(pf.ubicar(_orig({2: (None, "Chair", "椅子", 1)}), None), [])


class Corchetes(unittest.TestCase):
    """El contenedor 80: la columna trae el genérico ELEC-0116-EST y el SKU bueno
    (corregido por la orden P03487) vive como "[SKU] nombre" en otra celda."""

    def test_se_leen_y_se_marca_la_fuente(self):
        self.assertEqual(
            pf.skus_del_renglon(("ELEC-0116-EST", "[ELEC-0143-DJ6-NEG]", "YC1107-DJ6"), 0),
            [("ELEC-0116-EST", "columna"), ("ELEC-0143-DJ6-NEG", "corchete")])

    def test_no_repite_el_de_la_columna_ni_inventa(self):
        self.assertEqual(pf.skus_del_renglon(("ELEC-0116-EST", "[ELEC-0116-EST] Mezcladora"), 0),
                         [("ELEC-0116-EST", "columna")])
        self.assertEqual(pf.skus_del_renglon(("TOTAL", "[no-sku]", "[ABC]"), 0), [])

    def test_sin_columna_de_sku_bastan_los_corchetes(self):
        self.assertEqual(pf.skus_del_renglon((None, "[TEC-0377-NEG-XL] Casco"), None),
                         [("TEC-0377-NEG-XL", "corchete")])

    def test_cada_corchete_cae_en_su_renglon(self):
        o = pf.Tabla(1, ["英文品名 description", "中文品名 description"],
                     {2: ("Mixer 4", "调音台4"), 3: ("Mixer 6", "调音台6"), 4: ("Mixer 8", "调音台8")})
        f = pf.Tabla(1, ["sku odoo", "producto odoo", "英文品名 description", "中文品名 description"],
                     {2: ("ELEC-0116-EST", "[ELEC-0143-4C-NEG] DJ4", "Mixer 4", "调音台4"),
                      3: ("ELEC-0116-EST", "[ELEC-0143-DJ6-NEG] DJ6", "Mixer 6", "调音台6"),
                      4: ("ELEC-0116-EST", "[ELEC-0143-DJ8-NEG] DJ8", "Mixer 8", "调音台8")})
        u = [(x["sku"], x["original_fila"]) for x in pf.ubicar(f, o) if x["fuente_sku"] == "corchete"]
        self.assertEqual(u, [("ELEC-0143-4C-NEG", 2), ("ELEC-0143-DJ6-NEG", 3), ("ELEC-0143-DJ8-NEG", 4)])


class _Ix:
    """Lo mínimo de un packing_indice.Indice para la escalera."""

    def __init__(self, sha: str, filas: list[int], fotos: dict[int, dict] | None = None):
        self.sha256 = sha
        self.por_fila = {f: {"fila_excel": f} for f in filas}
        self.idx_de_fila = {f - 1: f for f in filas}          # fila_idx → fila_excel
        self.fila_de_idx = {v: k for k, v in self.idx_de_fila.items()}
        self.fotos = fotos or {}
        self.filas = [{"fila_excel": f} for f in filas]
        self.nombre, self.file_id = "PL.xlsx", "fid-A"

    def texto_fila(self, fe):
        return f"renglón {fe}"


def _u(fid="fid-A", sha="a" * 64, fila=34, nombre="PL.xlsx"):
    return {"original_file_id": fid, "original_sha256": sha, "original_fila": fila,
            "original_nombre": nombre}


class CuandoSeUsaFerraforme(unittest.TestCase):
    def test_tal_cual(self):
        (fid, fe, det), nota = pp._ferraforme([_u(fila=34), _u(fila=90)], ["fid-A"],
                                             {"fid-A": _Ix("a" * 64, [34, 90])})
        self.assertEqual((fid, fe, nota), ("fid-A", 34, None))
        self.assertIn("también en 90", det)

    def test_otro_packing_list(self):
        r, nota = pp._ferraforme([_u(fid="fid-B", nombre="OTRO.xlsx")], ["fid-A"],
                                 {"fid-A": _Ix("a" * 64, [34])})
        self.assertIsNone(r)
        self.assertIn("OTRO.xlsx", nota)

    def test_otra_version(self):
        r, nota = pp._ferraforme([_u(sha="b" * 64)], ["fid-A"], {"fid-A": _Ix("a" * 64, [34])})
        self.assertIsNone(r)
        self.assertIn("otra versión", nota)

    def test_dos_archivos_decide_la_foto(self):
        ix = {"fid-A": _Ix("a" * 64, [34]), "fid-B": _Ix("a" * 64, [34])}
        r, nota = pp._ferraforme([_u(), _u(fid="fid-B")], ["fid-A", "fid-B"], ix)
        self.assertIsNone(r)
        self.assertIn("2 packing lists", nota)


class LaFotoEsLaSegundaOpinion(unittest.TestCase):
    def _correr(self, fotos: dict[int, dict], huella: dict | None):
        ix = _Ix("a" * 64, [34, 90], fotos)
        aplicado = {}

        def _aplicar(fila, ix_, *, idx_foto=None, fila_excel=None, **_k):
            aplicado.update(idx_foto=idx_foto, fila_excel=fila_excel)

        with mock.patch.object(pp, "_publicacion_ml", return_value={}), \
             mock.patch.object(pp, "_aplicar_renglon", side_effect=_aplicar), \
             mock.patch.object(pp, "_ia_titulo", side_effect=AssertionError("no debió llamar a la IA")):
            fila = pp._resolver_uno(
                sku="MUE-0302-MAD", padre=None, pubs=[], fids=["fid-A"],
                indices={"fid-A": ix}, huella_odoo=huella, refs=[], guardado={},
                tarifa=7500.0, tc=19.0, usar_ia=True, cache_ml={},
                ubicaciones=[_u(fila=34)])
        return fila, aplicado

    def test_sin_foto_decide_ferraforme_sin_ia(self):
        fila, ap = self._correr({}, None)
        self.assertEqual((fila["estado"], fila["confianza"]), ("ferraforme", "alta"))
        self.assertEqual(ap["fila_excel"], 34)

    def test_foto_en_el_mismo_renglon_gana_la_foto(self):
        huella = {"sha": "S", "dh": 0, "crudo": b""}
        fila, ap = self._correr({33: {"sha": "S", "dh": 0}}, huella)   # idx 33 = fila 34
        self.assertEqual(fila["estado"], "sha256")
        self.assertIn("Ferraforme coincide", fila["detalle"])
        self.assertEqual(fila["confianza"], "alta")

    def test_foto_en_otro_renglon_gana_la_foto_sin_lote(self):
        huella = {"sha": "S", "dh": 0, "crudo": b""}
        fila, ap = self._correr({89: {"sha": "S", "dh": 0}}, huella)   # idx 89 = fila 90
        self.assertEqual((fila["estado"], fila["confianza"]), ("sha256", "baja"))
        self.assertIn("Ferraforme dice el renglón 34", fila["detalle"])
        self.assertEqual(ap["idx_foto"], 89)


if __name__ == "__main__":
    unittest.main()
