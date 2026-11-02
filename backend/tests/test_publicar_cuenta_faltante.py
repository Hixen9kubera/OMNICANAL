"""Crear en Mercado Libre la cuenta que FALTA (`crear_faltantes`).

── QUÉ FIJAN ────────────────────────────────────────────
Lote del 2-nov-2026: 12 SKUs quedaron publicados en UNA cuenta (ML rechazó la foto o pidió GTIN solo
en la otra) y el panel, con una publicación viva, solo actualizaba: no había cómo crear la que faltaba.

  1. Con `crear_faltantes`, un SKU vivo en BEKURA se actualiza ahí y se CREA en SANCORFASHION.
  2. Sin `crear_faltantes`, igual que antes: solo actualiza (un SKU puede estar en una cuenta a propósito).
  3. Vivo en las dos: no se crea nada aunque se pida.
  4. Borrada en una y faltante en la otra: se crean las dos que no están vivas.
  5. Un SKU padre nunca abre cuentas.

── NO SE TOCA NADA REAL ─────────────────────────────────
ML, Woo y la bitácora van simulados.

    cd backend && python -m unittest tests.test_publicar_cuenta_faltante -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import publicar  # noqa: E402

CAMPOS = {"titulo": "Collar isabelino para mascota talla M", "descripcion": "x",
          "atributos": [{"nombre": "COLOR", "campo": "COLOR", "valor": "Negro"}]}


def _req(**extra):
    return {"sku": "ORG-0722-NEG-M", "wc_id": 168412, "campos": CAMPOS,
            "categoria_confirmada": "MLM126303", **extra}


class CuentaFaltante(unittest.TestCase):
    def _correr(self, pubs, estados, req):
        creadas = []

        async def crear(sku, wc_id, campos, cuentas=None, **kw):
            creadas.append(list(cuentas or ["BEKURA", "SANCORFASHION"]))
            return {"resultados": [{"cuenta": c, "item_id": f"MLM{i}", "ok": True, "error": None,
                                    "ml_status": 201} for i, c in enumerate(cuentas or [])]}

        async def update(cuenta, item_id, *a):
            return {"cuenta": cuenta, "item_id": item_id, "ok": True, "error": None, "ml_status": 200,
                    "desc_status": 200, "_response": {}}

        async def est(_pubs):
            return estados

        with mock.patch.object(publicar, "_ml_publicaciones", return_value=pubs), \
             mock.patch.object(publicar, "_estados_items_ml", side_effect=est), \
             mock.patch.object(publicar, "_update_ml_una", side_effect=update), \
             mock.patch.object(publicar, "_guardar_backlog_ml"), \
             mock.patch.object(publicar, "_crear_ml", side_effect=crear):
            r = asyncio.run(publicar._confirmar_ml(req))
        return r, creadas

    def test_crea_la_que_falta(self):
        r, creadas = self._correr([{"cuenta": "SANCORFASHION", "item_id": "MLM1"}], {}, _req(crear_faltantes=True))
        self.assertEqual(creadas, [["BEKURA"]])
        self.assertEqual([(x["cuenta"], x.get("modo")) for x in r["resultados"]],
                         [("SANCORFASHION", None), ("BEKURA", "crear")])
        self.assertTrue(r["ok"])

    def test_sin_pedirlo_solo_actualiza(self):
        r, creadas = self._correr([{"cuenta": "SANCORFASHION", "item_id": "MLM1"}], {}, _req())
        self.assertEqual(creadas, [])
        self.assertEqual([x["cuenta"] for x in r["resultados"]], ["SANCORFASHION"])

    def test_vivo_en_las_dos_no_crea(self):
        pubs = [{"cuenta": "SANCORFASHION", "item_id": "MLM1"}, {"cuenta": "BEKURA", "item_id": "MLM2"}]
        _, creadas = self._correr(pubs, {}, _req(crear_faltantes=True))
        self.assertEqual(creadas, [])

    def test_borrada_y_faltante(self):
        pubs = [{"cuenta": "SANCORFASHION", "item_id": "MLM1"}]
        _, creadas = self._correr(pubs, {"SANCORFASHION": {"vivo": False}}, _req(crear_faltantes=True))
        self.assertEqual(creadas, [["SANCORFASHION", "BEKURA"]])

    def test_padre_no_abre_cuentas(self):
        r, creadas = self._correr([{"cuenta": "SANCORFASHION", "item_id": "MLM1"}], {},
                                  _req(crear_faltantes=True, _hijas=["ORG-0722-NEG-M"]))
        self.assertEqual(creadas, [])

    def test_faltantes_respeta_el_orden_de_cuentas(self):
        self.assertEqual(publicar._cuentas_faltantes_ml({"crear_faltantes": True},
                                                        [{"cuenta": "BEKURA"}]), ["SANCORFASHION"])
        self.assertEqual(publicar._cuentas_faltantes_ml({"crear_faltantes": True}, []), [])
        self.assertEqual(publicar._cuentas_faltantes_ml({}, [{"cuenta": "BEKURA"}]), [])


if __name__ == "__main__":
    unittest.main()
