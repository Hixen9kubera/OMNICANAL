"""
Pestaña CATÁLOGO (v0.703.0): el artifact del catálogo vivo en el panel.

  1. El servicio sirve el JSON comprimido y las hojas de fotos; un nombre de hoja raro no lee el disco.
  2. Las rutas son de `operador`, como Competencia.
  3. El archivo del repo NO trae costos: ni `co` ni `usd`, ni el múltiplo de los precios estimados por costo
     (con él se despejaría el costo). El repo es público.
  4. El script que lo arma quita esos campos y truena si queda alguno.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import rbac  # noqa: E402
from routers import catalogo as router  # noqa: E402
from services import catalogo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import catalogo_preparar as prep  # noqa: E402


class Servicio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.viejo = catalogo.CARPETA
        catalogo.CARPETA = Path(self.tmp.name)
        catalogo.olvidar()

    def tearDown(self):
        catalogo.CARPETA = self.viejo
        catalogo.olvidar()
        self.tmp.cleanup()

    def test_sin_archivo_es_sin_catalogo(self):
        with self.assertRaises(catalogo.SinCatalogo):
            catalogo.datos_gz()

    def test_sirve_el_gzip_tal_cual(self):
        crudo = gzip.compress(b'{"R": []}')
        (catalogo.CARPETA / "catalogo.json.gz").write_bytes(crudo)
        self.assertEqual(catalogo.datos_gz(), crudo)
        r = asyncio.run(router.datos())
        self.assertEqual(r.body, crudo)
        self.assertEqual(r.headers["content-encoding"], "gzip")
        self.assertEqual(r.media_type, "application/json")

    def test_hoja_con_nombre_raro_no_lee(self):
        (catalogo.CARPETA / "img").mkdir()
        (catalogo.CARPETA / "img" / "f0.webp").write_bytes(b"RIFF")
        self.assertEqual(catalogo.hoja("f0"), b"RIFF")
        for raro in ("../catalogo.json", "f0.webp", "g1", "f", "f12345", ""):
            with self.assertRaises(catalogo.SinCatalogo, msg=raro):
                catalogo.hoja(raro)

    def test_hoja_que_no_existe_es_404(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(router.imagen("f9"))
        self.assertEqual(cm.exception.status_code, 404)


class Permisos(unittest.TestCase):
    def test_rutas_piden_operador(self):
        for ruta in ("/api/catalogo", "/api/catalogo/img/f0"):
            self.assertEqual(rbac.rol_requerido("GET", ruta), "operador")
            self.assertFalse(rbac.permite("lectura", "GET", ruta))
            self.assertTrue(rbac.permite("operador", "GET", ruta))


class SinCostos(unittest.TestCase):
    def test_limpiar_fila_quita_costo_y_multiplo(self):
        r = {"s": "X-1", "co": 19.0, "usd": 1.2, "em": [68.21, "c", 3.59, "g", 0], "ea": [211.4, "m", 1.78, "h", 0],
             "v3": {"fu": "sin precio", "nt": ["sin fuentes: estimado 3.59× costo", "término equivocado"]},
             "ml": {"m": 10, "v3": {"nt": ["costo bajo"]}}}
        x = prep.limpiar_fila(r)
        self.assertNotIn("co", x)
        self.assertNotIn("usd", x)
        self.assertEqual(x["em"][:3], [68.21, "c", None])          # el precio se queda; el múltiplo no
        self.assertEqual(x["ea"], [211.4, "m", 1.78, "h", 0])      # ML × razón de Amazon no es costo
        self.assertEqual(x["v3"]["nt"], ["término equivocado"])
        self.assertNotIn("nt", x["ml"]["v3"])
        self.assertEqual(r["co"], 19.0, "no debe tocar la fila original")

    def test_comprobar_truena_con_costo(self):
        with self.assertRaises(SystemExit):
            prep.comprobar_sin_costos({"R": [{"s": "A", "co": 5}]})
        with self.assertRaises(SystemExit):
            prep.comprobar_sin_costos({"R": [{"s": "A", "em": [10, "c", 3.59, "g", 0]}]})
        prep.comprobar_sin_costos({"mercado": {"ml": {"edu": {"ref": {"tope_fob": 10}}}}, "v3": {"estimado_costo": 482},
                                   "R": [{"s": "A", "em": [10, "c", None, "g", 0]}]})

    def test_el_archivo_del_repo_no_trae_costos(self):
        ruta = Path(__file__).resolve().parent.parent / "data" / "catalogo" / "catalogo.json.gz"
        if not ruta.is_file():
            self.skipTest("todavía no hay catálogo en el repo")
        doc = json.loads(gzip.decompress(ruta.read_bytes()))
        prep.comprobar_sin_costos(doc)
        self.assertNotIn("multiplo", (doc.get("estimacion") or {}).get("ml") or {})
        self.assertGreater(len(doc["R"]), 10_000)
        fotos = doc["meta"]["fotos"]
        hojas = list((ruta.parent / "img").glob("f*.webp"))
        self.assertEqual(len(hojas), fotos["hojas"])
        self.assertTrue(all(r.get("f") is None or 0 <= r["f"] < fotos["total"] for r in doc["R"]))


if __name__ == "__main__":
    unittest.main()
