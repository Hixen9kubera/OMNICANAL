"""La marca de un alta en ML es la DECLARADA, no la primera de la lista de la categoría.

── QUÉ FIJAN ────────────────────────────────────────────
`attribute_mapper.build_attributes` mandaba la PRIMERA marca sugerida cuando «Ferrahome» no estaba en la lista
(1,015 altas desde marzo; en Tiendas de Campaña, «12 Survivors»). `publicar_ready._con_marca_declarada` la
regresa a la declarada después de que el vendor arma el envío.

    cd backend && python -m unittest tests.test_publicar_marca_declarada -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import publicar_ready as PR  # noqa: E402
from vendor.ml_ready import publisher_core  # noqa: E402

CAT = "MLM8161"
LISTA = [{"id": "BRAND", "values": [{"id": "111", "name": "12 Survivors"}, {"id": "222", "name": "Ferrahome"}]}]


def _payload(brand: dict | None) -> dict:
    attrs = [{"id": "SELLER_SKU", "value_name": "ORG-0941-AZL"}]
    if brand is not None:
        attrs.insert(0, brand)
    return {"category_id": CAT, "attributes": attrs}


def _brand(payload: dict) -> dict:
    return next(a for a in payload["attributes"] if a["id"] == "BRAND")


class MarcaDeclarada(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(publisher_core._attr_cache, {CAT: LISTA})   # noqa: SLF001
        p.start()
        self.addCleanup(p.stop)

    def test_la_primera_de_la_lista_se_cambia_por_ferrahome(self):
        pl = PR._con_marca_declarada(_payload({"id": "BRAND", "value_id": "111"}), {"ml_attrs": {}})  # noqa: SLF001
        self.assertEqual(_brand(pl), {"id": "BRAND", "value_name": "Ferrahome"})

    def test_la_marca_declarada_del_producto_manda(self):
        prod = {"ml_attrs": {"BRAND": "Chevrolet"}}
        pl = PR._con_marca_declarada(_payload({"id": "BRAND", "value_id": "111"}), prod)  # noqa: SLF001
        self.assertEqual(_brand(pl)["value_name"], "Chevrolet")

    def test_si_el_vendor_ya_mando_la_declarada_de_la_lista_no_se_toca(self):
        pl = PR._con_marca_declarada(_payload({"id": "BRAND", "value_id": "222"}), {"ml_attrs": {}})  # noqa: SLF001
        self.assertEqual(_brand(pl), {"id": "BRAND", "value_id": "222"})

    def test_si_ya_venia_como_texto_no_se_toca(self):
        pl = PR._con_marca_declarada(_payload({"id": "BRAND", "value_name": "FERRAHOME"}), {})  # noqa: SLF001
        self.assertEqual(_brand(pl), {"id": "BRAND", "value_name": "FERRAHOME"})

    def test_sin_marca_se_agrega(self):
        pl = PR._con_marca_declarada(_payload(None), {})  # noqa: SLF001
        self.assertEqual(_brand(pl), {"id": "BRAND", "value_name": "Ferrahome"})

    def test_sin_payload_no_revienta(self):
        self.assertIsNone(PR._con_marca_declarada(None, {}))  # noqa: SLF001


class Instalacion(unittest.TestCase):
    def setUp(self):
        actual = publisher_core.build_payload
        self.addCleanup(setattr, publisher_core, "build_payload", actual)
        # Otra prueba pudo dejar instalado el envoltorio (estado de módulo): se parte del vendor.
        self.original = PR._build_payload_vendor if actual is PR._build_payload_con_marca else actual  # noqa: SLF001
        publisher_core.build_payload = self.original

    def test_instalar_dos_veces_envuelve_una_sola(self):
        PR._instalar_marca_declarada()  # noqa: SLF001
        PR._instalar_marca_declarada()  # noqa: SLF001
        self.assertIs(publisher_core.build_payload, PR._build_payload_con_marca)  # noqa: SLF001
        self.assertIs(PR._build_payload_vendor, self.original)  # noqa: SLF001

    def test_el_envoltorio_arregla_lo_que_arma_el_vendor(self):
        vendor = mock.Mock(return_value=_payload({"id": "BRAND", "value_id": "111"}))
        with mock.patch.object(PR, "_build_payload_vendor", vendor), \
                mock.patch.dict(publisher_core._attr_cache, {CAT: LISTA}):   # noqa: SLF001
            pl = PR._build_payload_con_marca({"sku": "X", "ml_attrs": {}}, "tok", True, "BEKURA")  # noqa: SLF001
        vendor.assert_called_once_with({"sku": "X", "ml_attrs": {}}, "tok", dry_run=True, cuenta="BEKURA")
        self.assertEqual(_brand(pl)["value_name"], "Ferrahome")

    def test_si_arreglar_la_marca_revienta_sale_lo_del_vendor(self):
        original = _payload({"id": "BRAND", "value_id": "111"})
        with mock.patch.object(PR, "_build_payload_vendor", mock.Mock(return_value=original)), \
                mock.patch.object(PR, "_con_marca_declarada", side_effect=RuntimeError("bum")), \
                self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            self.assertIs(PR._build_payload_con_marca({"sku": "X"}, "tok"), original)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
