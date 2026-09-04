"""Pruebas de `datos_por_ids`: el multiget de NUESTRAS publicaciones.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
Dos fallos reportados el 4-sep-2026, con la misma raíz:

1. **La columna «Título de la tienda» vacía todo el mes.**
   `market_listing_metrics` guarda una fila POR MES (la PK lleva `periodo`), y
   desde que el cron de visitas es quien las crea (v0.367.0) su carga sólo
   llevaba visitas: agosto 3,118 filas con título (100%), septiembre 4,741 con
   CERO.

2. **El enlace abría la publicación de la competencia.**
   El panel construía `…/MLM-5305506294-_JM` a mano, y esa forma SIN SLUG ML la
   redirige al CATÁLOGO cuando el item es `catalog_listing` — donde se ve la
   oferta del vendedor que gana la compra, no la nuestra.

Las dos se arreglan con el mismo multiget, y por eso se prueba su PARSEO: la
respuesta de `/items?ids=` no es una lista de items, es una lista de sobres
`{code, body}`, y un sobre que no sea 200 debe ignorarse en vez de colarse como
un item sin datos.

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_ml as ML  # noqa: E402


def sobre(iid: str, titulo: str | None = None, permalink: str | None = None,
          code: int = 200) -> dict:
    cuerpo: dict = {"id": iid}
    if titulo is not None:
        cuerpo["title"] = titulo
    if permalink is not None:
        cuerpo["permalink"] = permalink
    return {"code": code, "body": cuerpo}


class DatosPorIds(unittest.TestCase):
    def _con(self, respuesta):
        return mock.patch.object(ML, "_get", return_value=respuesta)

    def test_devuelve_titulo_y_permalink(self):
        with self._con([sobre("MLM1", "Mesa Plegable", "https://x/MLM-1-mesa-_JM")]):
            out = ML.datos_por_ids(["MLM1"])
        self.assertEqual(out["MLM1"]["titulo"], "Mesa Plegable")
        self.assertEqual(out["MLM1"]["permalink"], "https://x/MLM-1-mesa-_JM")

    def test_ignora_los_sobres_que_no_son_200(self):
        """Un item ajeno vuelve con 403 y NO debe aparecer como si existiera."""
        with self._con([sobre("MLM1", "ok", code=200), sobre("MLM2", code=403)]):
            out = ML.datos_por_ids(["MLM1", "MLM2"])
        self.assertIn("MLM1", out)
        self.assertNotIn("MLM2", out)

    def test_un_item_sin_titulo_no_entra_con_cadena_vacia(self):
        # Mejor ausente que con "" — el COALESCE del upsert conserva lo anterior.
        with self._con([sobre("MLM1", "   "), sobre("MLM2", "bueno")]):
            out = ML.datos_por_ids(["MLM1", "MLM2"])
        self.assertNotIn("MLM1", out)
        self.assertEqual(out["MLM2"]["titulo"], "bueno")

    def test_lo_que_falta_simplemente_no_aparece(self):
        """Lo pedido que ML no devuelve no se inventa: quien llama conserva
        lo que ya tenía."""
        with self._con([sobre("MLM1", "ok")]):
            out = ML.datos_por_ids(["MLM1", "MLM2", "MLM3"])
        self.assertEqual(list(out), ["MLM1"])

    def test_una_respuesta_que_no_es_lista_no_revienta(self):
        for basura in (None, {}, "error"):
            with self._con(basura):
                self.assertEqual(ML.datos_por_ids(["MLM1"]), {})

    def test_parte_en_lotes_de_20(self):
        """El tope es de ML: `/items?ids=` no acepta más. Sin partir, la llamada
        entera falla y se pierden los 4,700 títulos."""
        with mock.patch.object(ML, "_get", return_value=[]) as falso:
            ML.datos_por_ids([f"MLM{i}" for i in range(45)])
        self.assertEqual(falso.call_count, 3, "45 ids → 20 + 20 + 5")
        primeros = falso.call_args_list[0][0][1]["ids"].split(",")
        self.assertEqual(len(primeros), 20)

    def test_lista_vacia_no_llama(self):
        with mock.patch.object(ML, "_get") as falso:
            self.assertEqual(ML.datos_por_ids([]), {})
        falso.assert_not_called()

    def test_descarta_ids_vacios_antes_de_pedir(self):
        with mock.patch.object(ML, "_get", return_value=[]) as falso:
            ML.datos_por_ids(["MLM1", "", None])  # type: ignore[list-item]
        self.assertEqual(falso.call_args[0][1]["ids"], "MLM1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
