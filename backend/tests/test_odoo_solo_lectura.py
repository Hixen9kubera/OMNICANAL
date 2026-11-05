"""Pruebas de la guarda de SOLO LECTURA de Odoo (`services/odoo.py`, v0.732.0).

Odoo salió de la operación el 15-oct-2026. El backend llega al endpoint
`object` de Odoo por dos puertas —`odoo._models()` y
`odoo._proxy_con_tiempo("object")`— y las dos devuelven `_ObjetoSoloLectura`,
que deja pasar solo métodos que leen.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. Las lecturas pasan TAL CUAL (mismos argumentos, mismo resultado).
2. Toda escritura se niega ANTES de salir a la red: el proxy de abajo ni se toca.
3. Las dos puertas salen con la guarda; `common` (authenticate) no la necesita.
4. `odoo_ventas._kw`, que usan órdenes, guías, FULL y notas, también queda
   cubierto: no tiene puerta propia.
5. Barrido del código: cada método de Odoo escrito en `services/` y `routers/`
   es una lectura permitida o una escritura conocida. Si alguien agrega una
   lectura nueva (p. ej. `search_fetch`), esta prueba avisa antes de que la
   guarda la niegue en producción.

Sin red: nada aquí habla con Odoo.

    cd backend && python -m unittest tests.test_odoo_solo_lectura -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from services import odoo  # noqa: E402

# Las escrituras que el código todavía sabe pedir (órdenes de venta, guías,
# FULL, notas de combinados). La guarda las niega todas.
ESCRITURAS_CONOCIDAS = {"create", "write", "unlink", "copy", "action_confirm",
                        "action_cancel", "action_assign", "button_validate",
                        "message_post"}


class _ProxyFalso:
    """Registra cada llamada; contesta lo que se le diga."""

    def __init__(self, respuesta=None):
        self.llamadas: list[tuple] = []
        self.respuesta = respuesta

    def execute_kw(self, *args):
        self.llamadas.append(args)
        return self.respuesta

    def authenticate(self, *args):
        self.llamadas.append(("authenticate",) + args)
        return 7


def _args(modelo: str, metodo: str) -> tuple:
    return ("db", 1, "clave", modelo, metodo, [[["id", "=", 1]]], {"fields": ["id"]})


class LecturasPasan(unittest.TestCase):
    def test_cada_lectura_llega_igual_al_proxy(self):
        for metodo in sorted(odoo._METODOS_DE_LECTURA):
            with self.subTest(metodo=metodo):
                falso = _ProxyFalso(respuesta=[{"id": 1}])
                r = odoo._ObjetoSoloLectura(falso).execute_kw(*_args("product.product", metodo))
                self.assertEqual(r, [{"id": 1}])
                self.assertEqual(falso.llamadas, [_args("product.product", metodo)])

    def test_sin_kwargs_tambien_pasa(self):
        falso = _ProxyFalso(respuesta=3)
        r = odoo._ObjetoSoloLectura(falso).execute_kw(
            "db", 1, "clave", "stock.quant", "search_count", [[]])
        self.assertEqual(r, 3)
        self.assertEqual(len(falso.llamadas[0]), 6)


class EscriturasSeNiegan(unittest.TestCase):
    def test_ninguna_escritura_sale(self):
        for metodo in sorted(ESCRITURAS_CONOCIDAS | {"action_done", "toggle_active",
                                                      "write_date", "x"}):
            with self.subTest(metodo=metodo):
                falso = _ProxyFalso()
                with self.assertRaises(odoo.OdooSoloLectura) as e, \
                        self.assertLogs("omnicanal.odoo", "ERROR"):
                    odoo._ObjetoSoloLectura(falso).execute_kw(*_args("sale.order", metodo))
                self.assertEqual(falso.llamadas, [], "la escritura salió al proxy")
                self.assertIn(f"sale.order.{metodo}", str(e.exception))

    def test_la_api_vieja_no_pasa(self):
        falso = _ProxyFalso()
        with self.assertRaises(odoo.OdooSoloLectura):
            odoo._ObjetoSoloLectura(falso).execute(
                "db", 1, "clave", "sale.order", "write", [1], {"note": "x"})
        self.assertEqual(falso.llamadas, [])

    def test_es_runtimeerror(self):
        # Los caminos de una venta atrapan RuntimeError/Exception y siguen:
        # negar una escritura no puede tirar una venta.
        self.assertTrue(issubclass(odoo.OdooSoloLectura, RuntimeError))


class LasDosPuertas(unittest.TestCase):
    def test_models_sale_con_guarda(self):
        falso = _ProxyFalso(respuesta=[])
        with mock.patch.object(odoo.xmlrpc.client, "ServerProxy", return_value=falso):
            m = odoo._models()
            self.assertIsInstance(m, odoo._ObjetoSoloLectura)
            m.execute_kw(*_args("product.product", "search_read"))
            with self.assertRaises(odoo.OdooSoloLectura), self.assertLogs("omnicanal.odoo", "ERROR"):
                m.execute_kw(*_args("sale.order", "create"))
        self.assertEqual(len(falso.llamadas), 1)

    def test_proxy_con_tiempo_object_con_guarda_y_common_sin_ella(self):
        falso = _ProxyFalso()
        with mock.patch.object(odoo.settings, "odoo_url", "https://odoo.prueba"), \
                mock.patch.object(odoo.xmlrpc.client, "ServerProxy", return_value=falso):
            obj = odoo._proxy_con_tiempo("object", 5.0)
            com = odoo._proxy_con_tiempo("common", 5.0)
        self.assertIsInstance(obj, odoo._ObjetoSoloLectura)
        self.assertIs(com, falso)
        self.assertEqual(com.authenticate("db", "u", "p", {}), 7)

    def test_kw_flujo_niega_escrituras(self):
        falso = _ProxyFalso()
        with mock.patch.object(odoo.settings, "odoo_url", "https://odoo.prueba"), \
                mock.patch.object(odoo, "_uid_con_tiempo", return_value=1), \
                mock.patch.object(odoo.xmlrpc.client, "ServerProxy", return_value=falso):
            with self.assertRaises(odoo.OdooSoloLectura), self.assertLogs("omnicanal.odoo", "ERROR"):
                odoo._kw_flujo("product.product", "write", [[1], {"active": False}])
        self.assertEqual(falso.llamadas, [])

    def test_odoo_ventas_kw_tambien(self):
        from services import odoo_ventas
        falso = _ProxyFalso()
        with mock.patch.object(odoo, "_uid", return_value=1), \
                mock.patch.object(odoo.xmlrpc.client, "ServerProxy", return_value=falso):
            for metodo in ("create", "write", "action_confirm", "action_cancel"):
                with self.subTest(metodo=metodo), self.assertRaises(odoo.OdooSoloLectura), \
                        self.assertLogs("omnicanal.odoo", "ERROR"):
                    odoo_ventas._kw("sale.order", metodo, [[1]])
        self.assertEqual(falso.llamadas, [])


class BarridoDelCodigo(unittest.TestCase):
    """Cada `"modelo.de.odoo", "metodo"` escrito en el código vivo es una
    lectura permitida o una escritura conocida."""

    PATRON = re.compile(
        r"""["']((?:product|stock|sale|res|purchase|mrp|crm|ir|account|uom|mail|delivery)"""
        r"""\.[a-z._]+)["'],\s*["']([a-z_]+)["']""")

    def test_ningun_metodo_desconocido(self):
        vistos: dict[str, set[str]] = {}
        for carpeta in ("services", "routers"):
            for archivo in (RAIZ / carpeta).rglob("*.py"):
                texto = archivo.read_text(encoding="utf-8", errors="replace")
                for _modelo, metodo in self.PATRON.findall(texto):
                    vistos.setdefault(metodo, set()).add(archivo.name)
        self.assertIn("search_read", vistos, "el patrón ya no encuentra nada")
        # "metodo" es un nombre de variable que cae en el patrón; no es método.
        desconocidos = {m: sorted(a) for m, a in vistos.items()
                        if m not in odoo._METODOS_DE_LECTURA
                        and m not in ESCRITURAS_CONOCIDAS and m != "metodo"}
        self.assertEqual(desconocidos, {},
                         "Método de Odoo nuevo: si LEE, agrégalo a _METODOS_DE_LECTURA; "
                         "si escribe, la guarda lo negará en producción.")


if __name__ == "__main__":
    unittest.main()
