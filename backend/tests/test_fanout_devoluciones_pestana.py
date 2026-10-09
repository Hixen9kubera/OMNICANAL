"""Pruebas de la pestaña «Devoluciones» del fan-out (`services/fanout_devoluciones.py`)
y de sus dos rutas (`GET /api/fanout/devoluciones` y `/devoluciones/sku/{sku}`).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. Los tokens de la nota de Bodega: el «Envío #» de ML de 11 dígitos
     («48096342888//MERCADO LIBRE»), la orden de 16 (2000…), FedEx de 12,
     Paquetexpress y Estafeta; una nota con varias guías; el HTML del chatter.
  2. La normalización de Odoo: hora de CDMX, «IN/…» corto, destino rack/SCRAP por
     ubicación vendible (interna CON almacén), la orden de las del botón «Devolver» y
     la pieza que entra a rack y pasa a SCRAP minutos después (MUE-0133-MET, IN/01318).
  3. La liga dura: envío > guía > orden > «Devolver»; la orden solo si no es ambigua;
     nunca antes de abrirse; un envío cancelado o vencido nunca se liga (5566294012
     cancelada y 5566301542 entregada, misma orden: IN/01226 es solo de la segunda);
     una recepción con otro producto (JUGU-0100-ROJ → IN/01541 recibió DEPO-0181-MET).
  4. La bandeja: grupos, resumen, filtros, orden, textos de cada fila, la gemela que
     sí llegó, la cuenta, la cobertura y Odoo caído (las que llegaron van a «buscar»
     SIN verificar, nada de Odoo cuenta en el resumen).
  5. El cuadre de TEC-0519-NAR-GRI completo (producción, 8-oct): las cuatro subidas de
     Odoo de 14 días con sus recepciones —IN/01453 = 5581207287 (ML aún la da en
     camino); el +3 del 29-sep son IN/01478, 01479 y 01481 con guías que kubera no
     tiene, más IN/01480 a SCRAP; el +2 del 3-oct son IN/01530 (Paquetexpress) e
     IN/01531; el +1 del 8-oct es IN/01562 con la guía FedEx de la 5585383756—, el
     reparto de cada una, el resumen y la 5585338270 que llegó el 2-oct y no entra.
  6. La lectura de Odoo: lista blanca de métodos, caché de 10 min, la última buena con
     aviso tras una falla, campos opcionales; las consultas de kubera (todas `select`)
     y las rutas (`asyncio.to_thread`, la cuenta inválida da 400).
  7. Lo que encontró la revisión (v0.629.0): una relectura a la vez y el freno de un
     minuto para TODO Odoo (bandeja y cuadre), sin fila y con plazo total; Odoo desde la
     apertura más vieja que se muestra; la misma guía a SCRAP y a rack (gana rack); la
     guía con un dígito de más (IN/01167); la liga probable por SKU y sin el reintento
     de una subida llena; las guías de 5…, el teléfono, el número pegado a la
     paquetería y la caja de FULL; el retiro de FULL; SCRAP aparte en el cuadre, las
     de rack sin subida (¿ya pasó stock_watch?), devoluciones ligadas y GUÍAS sin kubera.

Todo con datos reales de producción leídos en solo lectura el 8-oct-2026 (kubera en
transacción `read only` + rollback; Odoo con search_read/read). No se llama a nada:
las funciones puras reciben los datos ya leídos y las lecturas van simuladas.

    cd backend && python -m unittest tests.test_fanout_devoluciones_pestana -v
"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fanout_devoluciones as FD  # noqa: E402

AHORA = "2026-10-08 23:00:00"

# ── Devoluciones de kubera (filas de `_SQL_DEVOLUCIONES`, producción 8-oct) ────────


def _dev(id_: str, sku: str, pedido: str, abierta: str, ts: str, *, cuenta: str = "BEKURA",
         estado: str = "reembolsada", estado_canal: str = "delivered", destino: str = "seller_address",
         refund_at: str = "shipped", texto_ml: str = "", cerrada: str | None = None,
         reembolsada: str | None = None, envios: list | None = None, historia: list | None = None,
         piezas: int = 1, titulo: str = "") -> dict:
    return {"cuenta": cuenta, "id": id_, "estado": estado, "estado_canal": estado_canal, "destino": destino,
            "es_fulfillment": True, "venta_contaba": False, "estado_dinero": "refunded", "pedido": pedido,
            "motivo": "", "sku": sku, "piezas": piezas, "titulo": titulo, "refund_at": refund_at,
            "texto_ml": texto_ml, "ts": datetime.fromisoformat(ts), "abierta": abierta, "cerrada": cerrada,
            "reembolsada": reembolsada, "envios": envios or [], "historia": historia or []}


def _env(envio: str, guia: str | None, status: str = "delivered", destino: str = "seller_address") -> dict:
    return {"envio": envio, "guia": guia, "destino": destino, "status": status}


def _h(estado: str, antes: str | None, ec: str, hora: str, via: str = "webhook") -> dict:
    return {"estado": estado, "antes": antes, "via": via, "ec": ec, "hora": hora}


DEVS = [
    _dev("5589291144", "CAM-0032-MET", "2000018789469140", "2026-10-06 13:32:30", "2026-10-06 19:32:30+00:00",
         estado_canal="shipped", texto_ml="Llega entre el 7 y el 10 de octubre.",
         cerrada="2026-10-06 17:12:29", reembolsada="2026-10-06 17:12:29",
         envios=[_env("48188981488", "ZYDSBX3T6NIWTMFD2NZ6IIEPV4", "shipped")],
         historia=[_h("abierta", None, "opened", "2026-10-06 13:32:32"),
                   _h("en_transito", "abierta", "shipped", "2026-10-06 17:12:30"),
                   _h("reembolsada", "en_transito", "shipped", "2026-10-07 10:14:38")]),
    _dev("5585383756", "TEC-0519-NAR-GRI", "2000018658827574", "2026-09-29 13:30:34", "2026-09-29 19:30:34+00:00",
         texto_ml="Si no nos avisas dentro del plazo cómo llegó el producto, asumiremos que lo recibiste.",
         cerrada="2026-10-03 10:32:07", reembolsada="2026-10-03 10:32:07",
         envios=[_env("48130101482", "383939214207")],
         historia=[_h("abierta", None, "opened", "2026-09-29 13:30:36"),
                   _h("en_transito", "abierta", "shipped", "2026-10-03 10:30:44"),
                   _h("reembolsada", "en_transito", "delivered", "2026-10-08 13:37:49")]),
    _dev("5585338270", "TEC-0519-NAR-GRI", "2000018671160116", "2026-09-29 12:14:32", "2026-09-29 18:14:32+00:00",
         texto_ml="El paquete llegó el viernes 2 de octubre.",
         cerrada="2026-09-29 16:38:09", reembolsada="2026-09-29 16:38:09",
         envios=[_env("48129099447", "131253345055")],
         historia=[_h("abierta", None, "opened", "2026-09-29 12:14:35"),
                   _h("en_transito", "abierta", "shipped", "2026-09-29 16:36:40"),
                   _h("reembolsada", "en_transito", "shipped", "2026-09-30 21:39:58")]),
    _dev("5582365580", "HERR-0035-VER", "2000018586880448", "2026-09-23 18:11:43", "2026-09-24 00:11:43+00:00",
         refund_at="delivered", texto_ml="El paquete llegó el lunes 28 de septiembre.",
         cerrada="2026-10-01 09:15:21", reembolsada="2026-10-01 09:15:21",
         envios=[_env("48088384454", "6LX6A2TRSFMF3AC3RM6GDIBU4A")],
         historia=[_h("abierta", None, "opened", "2026-09-23 18:11:45"),
                   _h("en_transito", "abierta", "shipped", "2026-09-23 18:22:18"),
                   _h("recibida", "en_transito", "delivered", "2026-09-28 12:36:35"),
                   _h("reembolsada", "recibida", "delivered", "2026-10-01 09:15:27")]),
    _dev("5581207287", "TEC-0519-NAR-GRI", "2000018554759822", "2026-09-21 21:28:30", "2026-09-22 03:28:30+00:00",
         estado="en_transito", estado_canal="shipped", refund_at="delivered",
         texto_ml="Llega entre el 24 y el 27 de septiembre.",
         envios=[_env("48072486464", "6U53KKBL7BLEZOG23STYQNR35E", "shipped")],
         historia=[_h("abierta", None, "opened", "2026-09-21 21:28:32"),
                   _h("en_transito", "abierta", "shipped", "2026-09-22 16:01:30")]),
    _dev("5579601302", "JUGU-0100-ROJ", "2000018518224038", "2026-09-18 18:46:06", "2026-09-19 00:46:06+00:00",
         texto_ml="El paquete llegó el miércoles 23 de septiembre.",
         cerrada="2026-09-19 11:17:30", reembolsada="2026-09-19 11:17:30",
         envios=[_env("48050283944", "2W5SUOM42FOMVP6UKEKF2KOAWE")],
         historia=[_h("abierta", None, "opened", "2026-09-18 18:46:08"),
                   _h("en_transito", "abierta", "shipped", "2026-09-19 11:17:32"),
                   _h("reembolsada", "en_transito", "delivered", "2026-09-23 16:58:03")]),
    _dev("5572526388", "VAR-0452-EST", "2000018205381088", "2026-09-06 17:07:20", "2026-09-06 23:07:20+00:00",
         cuenta="SANCORFASHION", destino="warehouse",
         cerrada="2026-09-08 13:09:14", reembolsada="2026-09-11 07:54:54",
         envios=[_env("47949180687", "MEL47949180687FMDOR01", destino="warehouse")],
         historia=[_h("reembolsada", None, "shipped", "2026-09-09 11:17:40", "backfill")]),
    _dev("5569688566", "MUE-0133-MET", "2000017896793716", "2026-09-01 11:17:50", "2026-09-01 17:17:50+00:00",
         cuenta="SANCORFASHION", texto_ml="El paquete llegó el lunes 7 de septiembre.",
         cerrada="2026-09-01 12:29:33", reembolsada="2026-09-01 12:29:33",
         envios=[_env("47907568534", "ZU4RN5GKONMG7KCUFVDGIV2BOQ")],
         historia=[_h("reembolsada", None, "delivered", "2026-09-09 11:17:36", "backfill")]),
    _dev("5566301542", "VEH-0034-VER", "2000018054273922", "2026-08-25 19:49:12", "2026-08-26 01:49:12+00:00",
         cuenta="SANCORFASHION", texto_ml="El paquete llegó el viernes 28 de agosto.",
         cerrada="2026-08-25 20:19:58", reembolsada="2026-08-25 20:19:59",
         envios=[_env("47857816170", "462U5HEK2FOIRBBXGE5MC5FZ2E")],
         historia=[_h("reembolsada", None, "delivered", "2026-09-09 11:17:08", "backfill")]),
    _dev("5566294012", "VEH-0034-VER", "2000018054273922", "2026-08-25 19:31:50", "2026-08-26 01:31:50+00:00",
         cuenta="SANCORFASHION", estado_canal="cancelled", texto_ml="El paquete llegó el viernes 28 de agosto.",
         cerrada="2026-08-25 19:48:06", reembolsada="2026-08-25 19:48:06",
         envios=[_env("47857723358", None, "cancelled")],
         historia=[_h("reembolsada", None, "cancelled", "2026-09-09 11:17:25", "backfill")]),
]

# ── Odoo (producción, 8-oct): recepciones, notas y ubicaciones ────────────────────
VENDORS = [4, "Partner Locations/Vendors"]
CUSTOMERS = [5, "Customers"]
SALIDA = [108661, "TEXCO/Salida"]
SCRAP = [108666, "TEXCO/FERRAFORME/SCRAP"]
S24 = [701080, "TEXCO/FERRAFORME/S/24/N1"]
Q25 = [135635, "TEXCO/FERRAFORME/Q/25/N3"]
Q26N3 = [135641, "TEXCO/FERRAFORME/Q/26/N3"]
Q26N4 = [135642, "TEXCO/FERRAFORME/Q/26/N4"]
S20 = [701056, "TEXCO/FERRAFORME/S/20/N1"]
FERRA = [108658, "TEXCO/FERRAFORME"]
TEXCO = [135, "TEXCO  I"]
LOCS = [{"id": 4, "usage": "supplier", "warehouse_id": False, "complete_name": VENDORS[1]},
        {"id": 5, "usage": "customer", "warehouse_id": False, "complete_name": CUSTOMERS[1]},
        {"id": 108666, "usage": "internal", "warehouse_id": False, "complete_name": SCRAP[1]}] + [
    {"id": l[0], "usage": "internal", "warehouse_id": TEXCO, "complete_name": l[1]}
    for l in (SALIDA, S24, Q25, Q26N3, Q26N4, S20, FERRA)]
# Los códigos son los de Odoo; los ids de TEC-1571-MET y CALZ-0127 no se leyeron (son de relleno).
PRODS = {"TEC-0519-NAR-GRI": 73179, "DEPO-0181-MET": 120905, "MUE-0133-MET": 66570, "VEH-0034-VER": 90460,
         "HERR-0035-VER": 111786, "TEC-1571-MET": 50001, "CALZ-0127-BLN-NEG-ROJ-44": 50002}
DEVOL = [1665440, "DEVOLUCIONES"]

# (id, nombre, date_done UTC, destino, SKU, nota del chatter, folio, ref. cliente, «Devolver»)
RECEPCIONES = [
    (919890, "TEXCO/IN/01069", "2026-08-17 17:48:45", SCRAP, "TEC-1571-MET",
     "Artículo en mal estado (roto) //  #2000017718464180\t47729212750/FOLIO: 01015903/ML", False, False, None),
    (934470, "TEXCO/IN/01226", "2026-08-29 15:21:39", SALIDA, "VEH-0034-VER", None, False, "2000014646604277",
     ([926751, "TEXCO/OUT/05862"], [1444700, "ML 2000018054273922"], [1742780, "Javier Contreras"])),
    (945452, "TEXCO/IN/01318", "2026-09-08 20:08:29", SALIDA, "MUE-0133-MET", None, False, "2000014495252489",
     ([913380, "TEXCO/OUT/05701"], [1440524, "ML 2000017896793716"], [1738559, "Israel galicia"])),
    (948776, "TEXCO/IN/01338", "2026-09-11 15:20:17", SALIDA, "CALZ-0127-BLN-NEG-ROJ-44", None, False,
     "2000014791788039", ([935512, "TEXCO/OUT/06143"], [1447897, "ML 2000018204937856"],
                          [1746022, "Carmen Sánchez Pérez"])),
    (967266, "TEXCO/IN/01453", "2026-09-28 15:00:05", S24, "TEC-0519-NAR-GRI", "48072486464//MERCADO LIBRE",
     "2000018554759822", False, None),
    (971047, "TEXCO/IN/01478", "2026-09-29 20:30:38", Q25, "TEC-0519-NAR-GRI", "48096342888//MERCADO LIBRE",
     "2000015173544153", False, None),
    (971051, "TEXCO/IN/01479", "2026-09-29 20:33:16", Q25, "TEC-0519-NAR-GRI", "48096378372//MERCADO LIBRE",
     "2000015173544155", False, None),
    (971057, "TEXCO/IN/01480", "2026-09-29 20:40:33", SCRAP, "TEC-0519-NAR-GRI", "48075552134//MERCADO LIBRE",
     "2000014993929039", False, None),
    (971058, "TEXCO/IN/01481", "2026-09-29 20:43:40", Q25, "TEC-0519-NAR-GRI", "48075552134//MERCADO LIBRE",
     "2000014993929039", False, None),
    (971280, "TEXCO/IN/01491", "2026-09-29 22:40:40", SCRAP, "HERR-0035-VER", "48088384454//MERCADO LIBRE",
     "2000015153578905", False, None),
    (976603, "TEXCO/IN/01530", "2026-10-03 17:33:21", Q26N3, "TEC-0519-NAR-GRI", "CUL01WE0635094//PAQUETEXPRESS",
     "2000015233518387", False, None),
    (976611, "TEXCO/IN/01531", "2026-10-03 17:35:32", Q26N3, "TEC-0519-NAR-GRI", "48104929380//MERCADO LIBRE",
     "2000015200782469", False, None),
    (980374, "TEXCO/IN/01541", "2026-10-05 23:49:00", Q26N4, "DEPO-0181-MET",
     "INGRESO DE 1 ARTICULO EN BUEN ESTADO, SE INTEGRA A STOCK// #2000015087957193 // 48050283944",
     "#2000015087957193", False, None),
    (985790, "TEXCO/IN/01562", "2026-10-08 22:13:18", S20, "TEC-0519-NAR-GRI", "383939214207//FEDEX",
     "2000018658827574", False, None),
]
# MUE-0133-MET: tras IN/01318 (14:08 CDMX) la pieza volvió por PACK/PICK y pasó a SCRAP a las 14:11.
A_SCRAP = [{"product_id": [66570, "[MUE-0133-MET] Kit soporte"], "quantity": 1.0, "date": "2026-09-08 20:11:37"}]


def _crudo(filtro_sku: str | None = None, a_scrap: list | None = None) -> dict:
    """La lectura de Odoo como la devuelven `leer_odoo_crudo` (todas) y
    `leer_odoo_sku_crudo` (solo un SKU)."""
    picks, movs, msgs = [], [], []
    for pid, nombre, done, dest, sku, nota, folio, ref, devolver in RECEPCIONES:
        if filtro_sku and sku != filtro_sku:
            continue
        ret, venta, socio = devolver or (False, False, DEVOL)
        picks.append({"id": pid, "name": nombre, "date_done": done, "partner_id": socio, "origin": False,
                      "note": False, "return_id": ret, "sale_id": venta, "purchase_id": False,
                      "location_id": CUSTOMERS if devolver else VENDORS, "location_dest_id": dest,
                      "carrier_tracking_ref": False, "ifull_return_withdrawal_folio": folio,
                      "x_studio_referencia_del_cliente": ref})
        movs.append({"id": pid * 10, "picking_id": [pid, nombre], "product_id": [PRODS[sku], f"[{sku}] …"],
                     "quantity": 1.0, "date": done, "location_id": CUSTOMERS if devolver else VENDORS,
                     "location_dest_id": dest, "is_inventory": False, "reference": nombre})
        if nota:
            msgs.append({"res_id": pid, "body": f"<p>{nota}</p>"})
    usados = {m["product_id"][0] for m in movs}
    return {"picks": picks, "movs": movs, "msgs": msgs, "locs": LOCS,
            "prods": [{"id": i, "default_code": s} for s, i in PRODS.items() if i in usados],
            "scrap": A_SCRAP if a_scrap is None else a_scrap,
            "productos": [{"id": PRODS[filtro_sku], "default_code": filtro_sku, "name": "HERRAMIENTA"}]
            if filtro_sku else []}


def _odoo_ok() -> dict:
    return {"ok": True, "recepciones": FD.recepciones_de(_crudo()), "leido": "2026-10-08 22:55:00", "error": None}


# ── ops.fanout_log de TEC-0519-NAR-GRI (producción) ──────────────────────────────

def _log(id_: int, ts: str, canal: str, accion: str, motivo: str, resultado: str, cuenta: str = "",
         stock_canal=None, objetivo=None, sku: str = "TEC-0519-NAR-GRI") -> dict:
    t = datetime.fromisoformat(ts)
    hora = t.astimezone(FD._CDMX).strftime("%Y-%m-%d %H:%M:%S")
    return {"id": id_, "ts": t, "hora": hora, "motivo": motivo, "canal": canal, "cuenta": cuenta,
            "accion": accion, "resultado": resultado, "stock_canal": stock_canal, "objetivo": objetivo,
            "item_id": "", "dry_run": False, "sku": sku}


def _pase(id_: int, ts_delta: str, ts_reparto: str, de: int, a: int) -> list[dict]:
    """Un `odoo_delta` y su reparto: ML FULL en las dos cuentas y Amazon cerrada."""
    return [_log(id_, ts_delta, "woocommerce", "odoo_delta", f"delta de Odoo (foto {de} -> {a})", f"Woo {de} -> {a}",
                 "ODOO"),
            _log(id_ + 1, ts_reparto, "mercado_libre", "omitir", "delta de Odoo aplicado",
                 "FULL/FBA (bodega del marketplace, no se toca)", "SANCORFASHION", 0, a),
            _log(id_ + 2, ts_reparto, "mercado_libre", "omitir", "delta de Odoo aplicado",
                 "FULL/FBA (bodega del marketplace, no se toca)", "BEKURA", 0, a),
            _log(id_ + 3, ts_reparto, "amazon", "omitir", "delta de Odoo aplicado",
                 "situacion=closed (escribirle la REACTIVARÍA)", "", None, a)]


BITACORA = (_pase(149593, "2026-10-08 22:22:57.567124+00:00", "2026-10-08 22:23:05.995935+00:00", 417, 418)
            + _pase(139757, "2026-10-03 17:53:24.844880+00:00", "2026-10-03 17:53:47.727217+00:00", 415, 417)
            + _pase(133104, "2026-09-29 20:49:26.361381+00:00", "2026-09-29 20:49:38.292723+00:00", 412, 415)
            + _pase(130556, "2026-09-28 15:03:10.608681+00:00", "2026-09-28 15:03:16.663372+00:00", 411, 412))
SUBIDAS = [f for f in BITACORA if f["accion"] == "odoo_delta"]


class Tokens(unittest.TestCase):
    def test_envio_de_ml(self):
        t = FD.tokens(FD.texto_plano("<p>48096342888//MERCADO LIBRE</p>"))
        self.assertIn("48096342888", t)
        self.assertEqual(FD.guias(t), [("ml", "48096342888")])
        self.assertEqual(FD.guia_txt(FD.guias(t)), "48096342888")

    def test_orden_envio_y_folio_en_una_nota(self):
        # TEC-1571-MET, IN/01069: la orden, el envío y el folio de Bodega en una línea.
        nota = "ARTÍCULO EN MAL ESTADO (ROTO) //  #2000017718464180\t47729212750/FOLIO: 01015903/ML"
        t = FD.tokens(nota)
        self.assertTrue({"2000017718464180", "47729212750", "01015903"} <= set(t))
        # La orden no es guía y el folio de 8 dígitos tampoco.
        self.assertEqual(FD.guias(t), [("ml", "47729212750")])

    def test_fedex_paquetexpress_y_estafeta(self):
        self.assertEqual(FD.guias(FD.tokens("383939214207//FEDEX")), [("fedex", "383939214207")])
        self.assertEqual(FD.guia_txt(FD.guias(FD.tokens("383939214207//FEDEX"))), "383939214207 (FedEx)")
        self.assertEqual(FD.guias(FD.tokens("CUL01WE0635094//PAQUETEXPRESS")),
                         [("paquetexpress", "CUL01WE0635094")])
        self.assertEqual(FD.guias(FD.tokens("cpe01we0023391001001//PAQUETEXPRESS")),
                         [("paquetexpress", "CPE01WE0023391001001")])
        self.assertEqual(FD.guia_txt(FD.guias(FD.tokens("501505906741C60GIODL7K//ESTAFETA"))),
                         "501505906741C60GIODL7K (Estafeta)")

    def test_varias_guias_y_html(self):
        # IN/01319: un retiro con varias guías; se muestra la primera y cuántas más.
        t = FD.tokens(FD.texto_plano("WAYBILL&nbsp;4483945664 383434722497 <b>383472889457</b> 2000014701771981"))
        self.assertEqual(FD.guias(t), [("fedex", "383434722497"), ("fedex", "383472889457")])
        self.assertEqual(FD.guia_txt(FD.guias(t)), "383434722497 (FedEx) (+1)")
        self.assertIsNone(FD.guia_txt([]))


class Recepciones(unittest.TestCase):
    def setUp(self):
        self.recs = {r["picking"]: r for r in FD.recepciones_de(_crudo())}

    def test_hora_destino_y_lineas(self):
        r = self.recs["IN/01478"]
        self.assertEqual((r["nombre"], r["hora"], r["tipo"], r["destino"], r["guia"]),
                         ("TEXCO/IN/01478", "2026-09-29 14:30:38", "devoluciones", "rack", "48096342888"))
        self.assertEqual(r["lineas"], [{"sku": "TEC-0519-NAR-GRI", "cantidad": 1.0, "destino": "rack", "a_scrap": None}])
        self.assertEqual(self.recs["IN/01480"]["destino"], "scrap")       # interna SIN almacén
        # La orden del folio es un token (la de la 5585383756 en IN/01562).
        self.assertIn("2000018658827574", self.recs["IN/01562"]["tokens"])

    def test_las_del_boton_devolver(self):
        r = self.recs["IN/01226"]
        self.assertEqual((r["tipo"], r["orden_venta"], r["destino"], r["guia"]),
                         ("devolver", "2000018054273922", "rack", None))    # TEXCO/Salida sí es vendible

    def test_rack_que_pasa_a_scrap_enseguida(self):
        r = self.recs["IN/01318"]
        self.assertEqual((r["destino"], r["lineas"][0]["a_scrap"]), ("scrap", "2026-09-08 14:11:37"))
        # Media hora después ya no es corrección de la recepción: queda a rack.
        tarde = [{**A_SCRAP[0], "date": "2026-09-08 20:45:00"}]
        r = {x["picking"]: x for x in FD.recepciones_de(_crudo(a_scrap=tarde))}["IN/01318"]
        self.assertEqual((r["destino"], r["lineas"][0]["a_scrap"]), ("rack", None))


class LigaDura(unittest.TestCase):
    def setUp(self):
        self.recs = FD.recepciones_de(_crudo())
        self.por_dev, self.por_rec = FD.ligar_duro(DEVS, self.recs)

    def _liga(self, id_, sku):
        l = self.por_dev.get(f"{id_}|{sku}")
        return (l["rec"]["picking"], l["por"]) if l else None

    def test_por_envio_guia_orden_y_devolver(self):
        # El envío de la etiqueta gana aunque el folio también traiga su orden.
        self.assertEqual(self._liga("5581207287", "TEC-0519-NAR-GRI"), ("IN/01453", "guia"))
        # La guía FedEx de la paquetería (IN/01562, 8-oct 16:13).
        self.assertEqual(self._liga("5585383756", "TEC-0519-NAR-GRI"), ("IN/01562", "guia"))
        self.assertEqual(self._liga("5582365580", "HERR-0035-VER"), ("IN/01491", "guia"))
        self.assertEqual(self._liga("5569688566", "MUE-0133-MET"), ("IN/01318", "devolver"))
        # Su guía 48129099447 no está en ninguna recepción.
        self.assertIsNone(self._liga("5585338270", "TEC-0519-NAR-GRI"))

    def test_la_gemela_cancelada_no_se_liga(self):
        # Misma orden: 5566294012 (envío cancelado) y 5566301542 (entregada). IN/01226 es de la segunda.
        self.assertEqual(self._liga("5566301542", "VEH-0034-VER"), ("IN/01226", "devolver"))
        self.assertIsNone(self._liga("5566294012", "VEH-0034-VER"))
        self.assertEqual([x["id"] for x in self.por_rec[934470]], ["5566301542"])
        # Vencida, igual.
        vencida = [{**DEVS[8], "estado_canal": "expired"}]
        self.assertEqual(FD.ligar_duro(vencida, self.recs)[0], {})

    def test_la_orden_ambigua_no_liga(self):
        # Dos devoluciones vivas de la misma orden y el mismo SKU: la recepción por orden no es de ninguna.
        r = [x for x in self.recs if x["picking"] == "IN/01226"]
        gemelas = [{**DEVS[8]}, {**DEVS[8], "id": "5566999999", "envios": [_env("47800000000", None)]}]
        self.assertEqual(FD.ligar_duro(gemelas, r)[0], {})
        # Con distinto SKU desempata el producto de la recepción.
        gemelas[1]["sku"] = "OTRO-0001"
        self.assertEqual(list(FD.ligar_duro(gemelas, r)[0]), ["5566301542|VEH-0034-VER"])

    def test_nunca_antes_de_abrirse(self):
        tarde = [{**DEVS[4], "abierta": "2026-09-28 10:30:00"}]       # IN/01453 es de las 09:00
        self.assertEqual(FD.ligar_duro(tarde, self.recs)[0], {})
        holgura = [{**DEVS[4], "abierta": "2026-09-28 09:30:00"}]     # dentro de la hora de holgura
        self.assertEqual(len(FD.ligar_duro(holgura, self.recs)[0]), 1)

    def test_otro_producto(self):
        # La guía de JUGU-0100-ROJ entró en IN/01541 como DEPO-0181-MET.
        self.assertEqual(self._liga("5579601302", "JUGU-0100-ROJ"), ("IN/01541", "guia"))


class Bandeja(unittest.TestCase):
    def _armar(self, odoo=None, cuenta=None, devs=None):
        return FD.armar_bandeja(devs or DEVS, odoo or _odoo_ok(), SUBIDAS, AHORA, 60, cuenta, 10)

    def test_grupos_resumen_filtros_y_orden(self):
        b = self._armar()
        self.assertTrue(b["odoo_ok"])
        self.assertEqual([(f["grupo"], f["id"]) for f in b["filas"]],
                         [("buscar", "5585338270"), ("otro", "5579601302"), ("atrasada", "5581207287"),
                          ("odoo", "5585383756"), ("odoo", "5566301542"), ("scrap", "5582365580"),
                          ("scrap", "5569688566"), ("camino", "5589291144"), ("noregresa", "5566294012")])
        self.assertEqual(b["resumen"], {
            "a_bodega": 9, "piezas": 9, "llegaron": 7, "llegaron_ml": 6, "solo_odoo": 1, "en_odoo_guia": 6,
            "por_guia": 4, "por_venta": 2, "subieron": 3, "scrap": 2, "otro_sku": 1, "buscar": 1, "atrasada": 1,
            "en_camino": 1, "no_regresan": 1, "a_full": 1, "sin_destino": 0})
        self.assertEqual(b["filtros"], [{"id": "todas", "n": 9}, {"id": "buscar", "n": 1}, {"id": "otro", "n": 1},
                                        {"id": "atrasada", "n": 1}, {"id": "odoo", "n": 2}, {"id": "scrap", "n": 2},
                                        {"id": "camino", "n": 1}, {"id": "noregresa", "n": 1}])

    def test_sin_dia_de_llegada_los_dias_cuentan_desde_el_despacho(self):
        # ML la da por entregada sin decir qué día: los días son desde el despacho y el texto lo dice.
        txt = FD._textos_odoo("buscar", None, None, "entregada", "48129099447", 10, True, {},
                              sku="TEC-0519-NAR-GRI", hoy="2026-10-09", gemela=None, desde_llegada=False)
        self.assertEqual(txt, ("Sin recepción con su guía", "Guía 48129099447 · 10 días desde el despacho",
                               "Buscar en Bodega"))

    def test_las_filas_dicen_lo_que_pasa(self):
        f = {x["id"]: x for x in self._armar()["filas"]}
        x = f["5585338270"]
        self.assertEqual((x["estado_ml"], x["estado_txt"], x["reembolsada"], x["guia"], x["despacho"], x["llegada"],
                          x["llegada_de"], x["dias"], x["odoo"], x["probable"]),
                         ("entregada", "entregada", True, "48129099447", "2026-09-29 16:36:40", "2026-10-02",
                          "texto de ML", 6, None, None))
        self.assertEqual((x["pasos_txt"], x["odoo_txt"], x["odoo_sub"], x["accion"]),
                         ("Despachada 29-sep 16:36 · llegó el vie 2-oct · reembolsada al despachar",
                          "Sin recepción con su guía", "Guía 48129099447 · 6 días desde que llegó", "Buscar en Bodega"))
        x = f["5581207287"]
        self.assertEqual(x["odoo"], {"picking": "IN/01453", "hora": "2026-09-28 09:00:05", "destino": "rack",
                                     "por": "guia", "sku_recibido": "TEC-0519-NAR-GRI", "otro_sku": False})
        self.assertEqual((x["estado_txt"], x["pasos_txt"], x["odoo_txt"], x["odoo_sub"], x["accion"], x["dias"]),
                         ("en camino", "Despachada 22-sep 16:01 · ML no ha marcado la entrega",
                          "Recibida · IN/01453 · 28-sep 09:00", "A rack · por guía · Odoo 411 → 412",
                          "Nada en Bodega", 16))
        x = f["5579601302"]
        self.assertEqual((x["odoo"]["otro_sku"], x["odoo"]["sku_recibido"], x["odoo_txt"], x["odoo_sub"], x["accion"]),
                         (True, "DEPO-0181-MET", "IN/01541 recibió otro SKU", "DEPO-0181-MET · 5-oct 17:49",
                          "Revisar con Bodega"))
        x = f["5585383756"]
        self.assertEqual((x["pasos_txt"], x["llegada_de"], x["odoo_txt"], x["odoo_sub"]),
                         ("Despachada 3-oct 10:30 · ML la dio por entregada hoy 13:37 · reembolsada al despachar",
                          "en vivo", "Recibida · IN/01562 · hoy 16:13", "A rack · por guía · Odoo 417 → 418"))
        self.assertEqual(f["5566301542"]["odoo_sub"], "A rack · por «Devolver»")
        self.assertEqual((f["5582365580"]["odoo_txt"], f["5582365580"]["odoo_sub"]),
                         ("A SCRAP · IN/01491 · 29-sep 16:40", "No sube el stock · por guía"))
        self.assertEqual(f["5569688566"]["odoo_sub"],
                         "Entró a rack y pasó a SCRAP a las 14:11: no sube el stock · por «Devolver»")
        x = f["5566294012"]
        self.assertEqual((x["estado_txt"], x["pasos_txt"], x["odoo_txt"], x["odoo_sub"], x["odoo"]),
                         ("cancelada", "Envío cancelado; la 5566301542 de la misma venta sí llegó · reembolsada 25-ago 19:48",
                          "No regresa", "Su gemela entró en IN/01226", None))
        x = f["5589291144"]
        self.assertEqual((x["estado_txt"], x["pasos_txt"], x["odoo_txt"], x["accion"], x["dias"]),
                         ("en camino", "Despachada 6-oct 17:12 · reembolsada al despachar", "Aún no llega", "Esperar", 2))

    def test_la_cuenta(self):
        b = self._armar(cuenta="SANCORFASHION")
        self.assertEqual([f["id"] for f in b["filas"]], ["5566301542", "5569688566", "5566294012"])
        self.assertEqual((b["resumen"]["a_bodega"], b["resumen"]["a_full"]), (3, 1))

    def test_cobertura(self):
        # 14 recepciones, ningún retiro de FULL; 10 guías distintas (IN/01480 e IN/01481 traen la
        # misma); 4 son de kubera.
        self.assertEqual(self._armar()["cobertura"],
                         {"recepciones": 14, "retiros_full": 0, "guias_en_recepciones": 10, "guias_sin_kubera": 6})

    def test_odoo_caido(self):
        b = self._armar(odoo={"ok": False, "error": "Odoo no contestó (timed out)", "leido": None})
        self.assertEqual((b["odoo_ok"], b["odoo_error"], b["odoo_leido"]), (False, "Odoo no contestó (timed out)", None))
        # Las que llegaron según ML van a «buscar», sin verificar; ninguna fila trae Odoo.
        self.assertEqual([(f["grupo"], f["id"]) for f in b["filas"]],
                         [("buscar", "5585383756"), ("buscar", "5585338270"), ("buscar", "5582365580"),
                          ("buscar", "5579601302"), ("buscar", "5569688566"), ("buscar", "5566301542"),
                          ("camino", "5589291144"), ("camino", "5581207287"), ("noregresa", "5566294012")])
        self.assertTrue(all(f["odoo"] is None for f in b["filas"]))
        x = next(f for f in b["filas"] if f["id"] == "5585338270")
        self.assertEqual((x["odoo_txt"], x["odoo_sub"], x["accion"]),
                         ("Sin verificar en Odoo", "Odoo no contestó · Guía 48129099447 · 6 días desde que llegó",
                          "Verificar cuando Odoo conteste"))
        # Sin recepciones con qué descontar, la liga por fecha de v0.628.0 toma el +2 del 3-oct.
        self.assertEqual(x["probable"]["hora"], "2026-10-03 11:53:24")
        r = b["resumen"]
        self.assertEqual({k: r[k] for k in ("en_odoo_guia", "subieron", "scrap", "otro_sku", "atrasada", "solo_odoo")},
                         dict.fromkeys(("en_odoo_guia", "subieron", "scrap", "otro_sku", "atrasada", "solo_odoo"), 0))
        self.assertEqual((r["buscar"], r["llegaron"], r["llegaron_ml"], r["en_camino"]), (6, 6, 6, 2))
        self.assertEqual({f["id"]: f["n"] for f in b["filtros"]}["odoo"], 0)
        self.assertEqual(b["cobertura"], {"recepciones": 0, "retiros_full": 0, "guias_en_recepciones": 0,
                                          "guias_sin_kubera": 0})

    def test_la_lectura_vieja_se_usa_con_aviso(self):
        b = self._armar(odoo={**_odoo_ok(), "error": "Odoo no contestó (x); se muestra la lectura de las 22:55"})
        self.assertTrue(b["odoo_ok"])
        self.assertIn("se muestra la lectura de las 22:55", b["odoo_error"])
        self.assertEqual(b["odoo_leido"], "2026-10-08 22:55:00")


class Cuadre(unittest.TestCase):
    """TEC-0519-NAR-GRI a 14 días, con lo que había en producción el 8-oct a las 23:00."""

    def _armar(self, odoo=None, dias=14):
        odoo = odoo or {"ok": True, "entradas": FD.entradas_sku_de(_crudo("TEC-0519-NAR-GRI", a_scrap=[])),
                        "error": None}
        return FD.armar_detalle("TEC-0519-NAR-GRI", dias, BITACORA, odoo, DEVS, AHORA, "Polipasto eléctrico naranja")

    def test_tec0519_cada_subida_con_sus_recepciones(self):
        d = self._armar()
        self.assertEqual((d["odoo_ok"], d["odoo_de"], d["odoo_a"], d["titulo"]),
                         (True, 411, 418, "Polipasto eléctrico naranja"))
        self.assertEqual([(s["hora"], s["de"], s["a"], s["sube"], [r["picking"] for r in s["recepciones"]])
                          for s in d["subidas"]],
                         [("2026-10-08 16:22:57", 417, 418, 1, ["IN/01562"]),
                          ("2026-10-03 11:53:24", 415, 417, 2, ["IN/01530", "IN/01531"]),
                          ("2026-09-29 14:49:26", 412, 415, 3, ["IN/01478", "IN/01479", "IN/01481"]),
                          ("2026-09-28 09:03:10", 411, 412, 1, ["IN/01453"])])
        # IN/01480 (SCRAP) no se cuelga de la subida de las 14:49: no sube el stock.
        self.assertEqual((d["sin_subida"], [r["picking"] for r in d["a_scrap"]]), ([], ["IN/01480"]))
        rec = {r["picking"]: r for s in d["subidas"] for r in s["recepciones"]} | {r["picking"]: r for r in d["a_scrap"]}
        self.assertEqual(rec["IN/01453"], {
            "clave": "967266·rack", "picking": "IN/01453", "hora": "2026-09-28 09:00:05", "destino": "rack",
            "tipo": "devolucion", "piezas": 1, "guia": "48072486464", "guia_no_reconocida": False,
            "devolucion": {"id": "5581207287", "cuenta": "BEKURA", "sku": "TEC-0519-NAR-GRI"},
            "en_kubera": True, "guias_sin_kubera": 0, "txt": "Devolución 5581207287 · ML Kubera (ML aún la da en camino)"})
        self.assertEqual((rec["IN/01562"]["guia"], rec["IN/01562"]["txt"]),
                         ("383939214207 (FedEx)", "Devolución 5585383756 · ML Kubera"))
        self.assertEqual([(rec[p]["destino"], rec[p]["guia"], rec[p]["en_kubera"], rec[p]["txt"])
                          for p in ("IN/01478", "IN/01479", "IN/01480", "IN/01481", "IN/01530", "IN/01531")],
                         [("rack", "48096342888", False, "No está en kubera"),
                          ("rack", "48096378372", False, "No está en kubera"),
                          ("scrap", "48075552134", False, "A SCRAP: no cuenta para el stock"),
                          ("rack", "48075552134", False, "No está en kubera"),
                          ("rack", "CUL01WE0635094 (Paquetexpress)", False, "No está en kubera"),
                          ("rack", "48104929380", False, "No está en kubera")])
        # El +3 del 29-sep 14:49 no tiene nada de la 5585338270: salió a las 16:36 y su guía no está en Odoo.
        self.assertTrue(all(r["devolucion"] is None for r in d["subidas"][2]["recepciones"]))
        self.assertEqual([rec[p]["guias_sin_kubera"] for p in ("IN/01478", "IN/01530", "IN/01562")], [1, 1, 0])
        cola = " · reparto: omitido en las 3 publicaciones (ML FULL ×2, Amazon cerrada)"
        self.assertEqual([s["reparto_txt"] for s in d["subidas"]],
                         [f"Woo 417 → 418{cola}", f"Woo 415 → 417{cola}", f"Woo 412 → 415{cola}", f"Woo 411 → 412{cola}"])
        # 5 GUÍAS distintas que kubera no tiene (IN/01480 e IN/01481 traen la misma).
        self.assertEqual(d["resumen"], {"entraron": 7, "scrap": 1, "ligadas": 2, "sin_kubera": 5, "retiro_full": 0})
        self.assertEqual(d["pendientes"], [{"id": "5585338270", "cuenta": "BEKURA", "guia": "48129099447",
                                            "txt": "llegó el vie 2-oct"}])

    def test_odoo_caido_en_el_cuadre(self):
        d = self._armar(odoo={"ok": False, "entradas": [], "error": "Odoo no contestó (timed out)"})
        self.assertEqual((d["odoo_ok"], d["odoo_error"], len(d["subidas"])), (False, "Odoo no contestó (timed out)", 4))
        self.assertTrue(all(s["recepciones"] == [] for s in d["subidas"]))
        self.assertEqual(d["resumen"], {"entraron": 0, "scrap": 0, "ligadas": 0, "sin_kubera": 0, "retiro_full": 0})
        self.assertEqual([(p["id"], p["txt"]) for p in d["pendientes"]],
                         [("5585338270", "llegó el vie 2-oct · sin verificar (Odoo no contestó)"),
                          ("5585383756", "entregada hoy · sin verificar (Odoo no contestó)"),
                          ("5581207287", "en camino, despachada el 22-sep 16:01 · sin verificar (Odoo no contestó)")])

    def test_una_recepcion_con_varias_guias_de_kubera(self):
        # Como IN/01319 (un retiro con 6 guías de TEC-0573-MET): la nota trae dos devoluciones del SKU.
        crudo = _crudo("TEC-0519-NAR-GRI", a_scrap=[])
        for m in crudo["msgs"]:
            if m["res_id"] == 985790:
                m["body"] = "<p>383939214207//FEDEX 48129099447//MERCADO LIBRE</p>"
        d = self._armar(odoo={"ok": True, "entradas": FD.entradas_sku_de(crudo), "error": None})
        r = d["subidas"][0]["recepciones"][0]
        # Se muestra la guía de la devolución que se nombra (la FedEx de la 5585383756), no la primera.
        self.assertEqual((r["picking"], r["guia"], r["txt"], r["en_kubera"]),
                         ("IN/01562", "383939214207 (FedEx) (+1)", "Devolución 5585383756 · ML Kubera y 1 más de kubera",
                          True))
        self.assertEqual(d["resumen"]["ligadas"], 3)        # devoluciones, no recepciones
        self.assertEqual(d["pendientes"], [])

    def test_el_periodo_corta_las_subidas(self):
        d = self._armar(dias=7)
        self.assertEqual([s["hora"][:10] for s in d["subidas"]], ["2026-10-08", "2026-10-03"])
        self.assertEqual(d["resumen"], {"entraron": 3, "scrap": 0, "ligadas": 1, "sin_kubera": 2, "retiro_full": 0})


class Subidas(unittest.TestCase):
    def test_una_recepcion_lejos_de_toda_subida_queda_sin_subida(self):
        woo = FD._woo_de([f for f in SUBIDAS])
        cerca = {"id": 1, "hora": "2026-10-03 11:33:21"}
        lejos = {"id": 2, "hora": "2026-10-03 07:00:00"}     # 4 h 53 min antes del +2
        por_sub, sin = FD.asignar_subidas([cerca, lejos], woo)
        self.assertEqual([r["id"] for rs in por_sub.values() for r in rs], [1])
        self.assertEqual(sin, [lejos])

    def test_el_reparto_que_escribe_y_el_que_falla(self):
        w = FD._woo_de([_log(1, "2026-10-08 18:00:00+00:00", "woocommerce", "odoo_delta",
                             "delta de Odoo (foto 3 -> 4)", "Woo 3 -> 4", "ODOO")])[0]
        rep = [_log(2, "2026-10-08 18:00:05+00:00", "tiktok", "escribir", "delta de Odoo aplicado", "ok", "KUBERA", 3, 4),
               _log(3, "2026-10-08 18:00:05+00:00", "mercado_libre", "escribir", "delta de Odoo aplicado",
                    "ERROR 403 forbidden", "BEKURA", 3, 4),
               _log(4, "2026-10-08 18:00:05+00:00", "temu", "sin_cambio", "delta de Odoo aplicado", "igual", "TEMU", 4, 4)]
        self.assertEqual(FD._reparto_de(w, rep + [_log(5, "2026-10-08 18:30:00+00:00", "tiktok", "escribir", "x",
                                                         "ok", "KUBERA", 4, 5)]), rep)
        self.assertEqual(FD.texto_reparto(w, rep),
                         "Woo 3 → 4 · reparto: escrito en TikTok 3 → 4; falló en ML Kubera (403 · sigue en 3); "
                         "Temu ya tenía 4")
        self.assertEqual(FD.texto_reparto({**w, "fallo": True}, []),
                         "stock_watch no pudo escribirlo en Woo · sin reparto en la bitácora")


class LecturaOdoo(unittest.TestCase):
    def setUp(self):
        FD._odoo_cache.update(t=0.0, v=None, leido=None, desde=None)
        FD._odoo_freno.update(motivo=None, t=0.0)
        FD._sku_cache.clear()
        FD._campos_cache.update(t=0.0, v=None)

    def test_solo_metodos_de_lectura(self):
        for metodo in ("write", "create", "unlink", "button_validate", "action_done", "message_post"):
            with self.assertRaises(PermissionError):
                FD._kw_solo_lectura("stock.picking", metodo, [[1]])

    def test_la_lectura_global_pide_lo_que_existe_y_en_bloque(self):
        llamadas = []

        def kw(modelo, metodo, args, kwargs=None):
            llamadas.append((modelo, metodo, args, kwargs))
            if metodo == "fields_get":
                return {c: {"type": "char"} for c in FD._CAMPOS_PICKING if not c.startswith(("ifull_", "x_studio_"))}
            if modelo == "stock.picking":
                return _crudo()["picks"]
            if modelo == "stock.move":
                return _crudo()["movs"] if args[0][0][0] == "picking_id" else A_SCRAP
            return []
        crudo = FD.leer_odoo_crudo("2026-08-09 05:00:00", kw)
        self.assertEqual(len(crudo["picks"]), 14)
        modelo, metodo, args, kwargs = next(c for c in llamadas if c[0] == "stock.picking" and c[1] != "fields_get")
        self.assertEqual(metodo, "search_read")
        self.assertNotIn("ifull_return_withdrawal_folio", kwargs["fields"])       # este Odoo no lo tiene
        self.assertEqual(args[0][:3], [["picking_type_code", "=", "incoming"], ["state", "=", "done"],
                                       ["date_done", ">=", "2026-08-09 05:00:00"]])
        self.assertIn(["partner_id.name", "=", "DEVOLUCIONES"], args[0])
        self.assertIn(["location_id.usage", "=", "customer"], args[0])
        self.assertTrue({m for _mo, m, _a, _k in llamadas} <= FD.METODOS_ODOO)
        self.assertEqual(crudo["scrap"], A_SCRAP)

    def test_la_lectura_por_sku(self):
        llamadas = []

        def kw(modelo, metodo, args, kwargs=None):
            llamadas.append((modelo, metodo, args))
            if metodo == "fields_get":
                return {c: {} for c in FD._CAMPOS_PICKING}
            if modelo == "product.product" and metodo == "search_read":
                return [{"id": 73179, "default_code": "TEC-0519-NAR-GRI", "name": "HERRAMIENTA"}]
            if modelo == "stock.move" and metodo == "search_read":
                return _crudo("TEC-0519-NAR-GRI")["movs"] if "|" in args[0] else []
            if modelo == "stock.picking":
                return _crudo("TEC-0519-NAR-GRI")["picks"]
            return []
        crudo = FD.leer_odoo_sku_crudo("TEC-0519-NAR-GRI", "2026-08-09 05:00:00", kw)
        self.assertEqual(len(crudo["movs"]), 8)
        prod = next(a for m, me, a in llamadas if m == "product.product")
        self.assertEqual(prod[0], [["default_code", "=ilike", "TEC-0519-NAR-GRI"]])
        dom = next(a for m, me, a in llamadas if m == "stock.move")[0]
        # Solo ENTRADAS: de fuera a una interna, o de una interna sin almacén a una vendible.
        self.assertIn(["location_dest_id.usage", "=", "internal"], dom)
        self.assertIn(["location_id.warehouse_id", "=", False], dom)
        sin = FD.leer_odoo_sku_crudo("NO-EXISTE", "2026-08-09 05:00:00", lambda *a, **k: [])
        self.assertEqual(sin["movs"], [])

    def test_cache_falla_y_lectura_vieja(self):
        crudo = _crudo()
        d60 = "2026-08-08 00:00:00"
        with mock.patch.object(FD, "leer_odoo_crudo", side_effect=TimeoutError("timed out")) as leer:
            r = FD.leer_odoo(d60)
            self.assertEqual((r["ok"], r["error"], r["leido"]), (False, "Odoo no contestó (timed out)", None))
            FD.leer_odoo(d60)                                       # dentro del minuto: no reintenta
            self.assertEqual(leer.call_count, 1)
        FD._odoo_freno["t"] = 0.0
        with mock.patch.object(FD, "leer_odoo_crudo", return_value=crudo) as leer:
            r = FD.leer_odoo(d60)
            FD.leer_odoo(d60)                                       # caché de 10 min
            FD.leer_odoo("2026-09-23 00:00:00")                     # la de 60 días cubre la de 14
            self.assertEqual(leer.call_count, 1)
        self.assertTrue(r["ok"])
        self.assertIsNone(r["error"])
        self.assertEqual(len(r["recepciones"]), 14)
        FD._odoo_cache["t"] = time.monotonic() - FD.ODOO_TTL - 1
        with mock.patch.object(FD, "leer_odoo_crudo", side_effect=TimeoutError("timed out")):
            r = FD.leer_odoo(d60)
        self.assertTrue(r["ok"])
        self.assertIn("se muestra la lectura de las", r["error"])
        FD._odoo_cache.update(t=time.monotonic() - FD.ODOO_VIEJA_MAX - 1)
        FD._odoo_freno["t"] = 0.0
        with mock.patch.object(FD, "leer_odoo_crudo", side_effect=TimeoutError("timed out")):
            self.assertFalse(FD.leer_odoo(d60)["ok"])


class Consultas(unittest.TestCase):
    SQL = (FD._SQL_DEVOLUCIONES, FD._SQL_SUBIDAS, FD._SQL_BITACORA, FD._SQL_TITULO, FD._SQL_PASADA)

    def setUp(self):
        FD._kubera_cache.clear()
        FD._sku_cache.clear()

    def test_solo_select(self):
        for sql in self.SQL:
            plano = " ".join(sql.lower().split())
            self.assertTrue(plano.startswith("/* devoluciones:"))
            for palabra in (" insert ", " update ", " delete ", " drop ", " alter ", " truncate "):
                self.assertNotIn(palabra, f" {plano} ")
        sql = " ".join(FD._SQL_DEVOLUCIONES.split())
        self.assertIn("coalesce(r.destino, (", sql)
        self.assertIn("r.canal = 'mercado_libre' and r.abierta_at > now() - make_interval(days => %(d)s)", sql)
        self.assertIn("'envio', s->>'shipment_id', 'guia', s->>'tracking_number'", sql)
        self.assertIn("left join channel.order_items oi", sql)
        self.assertIn(r"~ '^\d{4}-\d{2}-\d{2}T'", sql)
        self.assertIn("sku = any(%(s)s::citext[])", FD._SQL_SUBIDAS)

    def test_la_bandeja_lee_en_bloque(self):
        llamadas = []

        def fetch_all(sql, params=None):
            llamadas.append((sql.split("*/")[0], params))
            return list(DEVS) if "devoluciones:ml" in sql else list(SUBIDAS)
        with mock.patch.object(FD.sdb, "fetch_all", side_effect=fetch_all), \
             mock.patch.object(FD, "leer_odoo", return_value=_odoo_ok()) as odoo, \
             mock.patch.object(FD, "_ahora", return_value=AHORA):
            b = FD.bandeja(60, "bekura")
        # 60 días antes del corte (9-ago 23:00) menos un día, a las 00:00: ninguna fila se abrió antes.
        self.assertEqual(odoo.call_args, mock.call("2026-08-08 00:00:00"))
        self.assertEqual(llamadas[0][1], {"d": 150, "z": FD.ZONA})
        self.assertEqual(llamadas[1][1]["d"], 61)
        self.assertIn("TEC-0519-NAR-GRI", llamadas[1][1]["s"])
        self.assertEqual({f["cuenta"] for f in b["filas"]}, {"BEKURA"})
        with self.assertRaises(ValueError):
            FD.bandeja(60, "OTRA")

    def test_el_cuadre_lee_su_sku(self):
        with mock.patch.object(FD.sdb, "fetch_all", side_effect=lambda sql, p=None: (
                list(BITACORA) if "bitacora" in sql else list(DEVS))) as fa, \
             mock.patch.object(FD.sdb, "fetch_one", side_effect=lambda sql, p=None: (
                 {"ultima": "2026-10-08 22:50:00"} if "pasada" in sql else {"name": "Polipasto"})), \
             mock.patch.object(FD, "leer_odoo_sku", return_value={"ok": True, "error": None, "nombre": "",
                                                                   "entradas": FD.entradas_sku_de(
                                                                       _crudo("TEC-0519-NAR-GRI", a_scrap=[]))}) as o, \
             mock.patch.object(FD, "_ahora", return_value=AHORA):
            d = FD.detalle_sku(" TEC-0519-NAR-GRI ", 14)
        self.assertEqual(o.call_args, mock.call("TEC-0519-NAR-GRI", 60))
        self.assertEqual(fa.call_args_list[0].args[1], {"s": "TEC-0519-NAR-GRI", "d": 15, "z": FD.ZONA})
        self.assertEqual((d["sku"], d["titulo"], len(d["subidas"]), d["pasada"]),
                         ("TEC-0519-NAR-GRI", "Polipasto", 4, "2026-10-08 22:50:00"))


class Router(unittest.TestCase):
    def test_las_rutas_van_en_un_hilo_y_validan_la_cuenta(self):
        from fastapi import HTTPException

        from routers import fanout as R
        with mock.patch.object(FD, "bandeja", return_value={"ok": True}) as b:
            self.assertEqual(asyncio.run(R.devoluciones(dias=30, cuenta=" sancorfashion ")), {"ok": True})
            asyncio.run(R.devoluciones(dias=60, cuenta=None))
        self.assertEqual(b.call_args_list, [mock.call(30, "SANCORFASHION"), mock.call(60, None)])
        with self.assertRaises(HTTPException) as e:
            asyncio.run(R.devoluciones(dias=60, cuenta="TEMU"))
        self.assertEqual(e.exception.status_code, 400)
        with mock.patch.object(FD, "detalle_sku", return_value={"sku": "X"}) as d:
            asyncio.run(R.devoluciones_sku(sku="TEC-0519-NAR-GRI", dias=14))
        self.assertEqual(d.call_args, mock.call("TEC-0519-NAR-GRI", 14))

    def test_quedan_bajo_el_prefijo_admin(self):
        from routers import fanout as R
        rutas = {r.path for r in R.router.routes}
        self.assertTrue({"/api/fanout/devoluciones", "/api/fanout/devoluciones/sku/{sku}"} <= rutas)


# ── v0.629.0: lo que encontró la revisión ────────────────────────────────────────
# Cada caso es de producción (8 y 9-oct) o el patrón real con una devolución de
# relleno donde kubera no la tiene (IN/01480 e IN/01481 traen una guía que no está).

VEH_ID = 50003
GUIA_REPETIDA = _dev("5599999999", "TEC-0519-NAR-GRI", "2000014993929039", "2026-09-24 10:00:00",
                     "2026-09-24 16:00:00+00:00", texto_ml="El paquete llegó el lunes 28 de septiembre.",
                     cerrada="2026-09-24 12:00:00", reembolsada="2026-09-24 12:00:00",
                     envios=[_env("48075552134", "ZZZ")],
                     historia=[_h("abierta", None, "opened", "2026-09-24 10:00:01"),
                               _h("en_transito", "abierta", "shipped", "2026-09-24 12:00:00")])
# VEH-0006-GOM, 2 piezas, llegó el 24-ago: Bodega anotó la guía FedEx con un dígito de más.
VEH = _dev("5558975718", "VEH-0006-GOM", "2000017999999999", "2026-08-20 10:00:00", "2026-08-20 16:00:00+00:00",
           cuenta="SANCORFASHION", texto_ml="El paquete llegó el lunes 24 de agosto.", piezas=2,
           cerrada="2026-08-20 12:00:00", reembolsada="2026-08-20 12:00:00",
           envios=[_env("47860000001", "383142924750")],
           historia=[_h("abierta", None, "opened", "2026-08-20 10:00:01"),
                     _h("en_transito", "abierta", "shipped", "2026-08-20 12:00:00")])


def _agregar(crudo: dict, pid: int, nombre: str, done: str, dest: list, sku: str, nota: str | None,
             piezas: float = 1.0, folio: str | bool = False) -> dict:
    """Una recepción «DEVOLUCIONES» más en un crudo de `_crudo()`."""
    prod = PRODS.get(sku, VEH_ID)
    crudo["picks"].append({"id": pid, "name": nombre, "date_done": done, "partner_id": DEVOL, "origin": False,
                           "note": False, "return_id": False, "sale_id": False, "purchase_id": False,
                           "location_id": VENDORS, "location_dest_id": dest, "carrier_tracking_ref": False,
                           "ifull_return_withdrawal_folio": folio, "x_studio_referencia_del_cliente": False})
    crudo["movs"].append({"id": pid * 10, "picking_id": [pid, nombre], "product_id": [prod, f"[{sku}] …"],
                          "quantity": piezas, "date": done, "location_id": VENDORS, "location_dest_id": dest,
                          "is_inventory": False, "reference": nombre})
    if nota:
        crudo["msgs"].append({"res_id": pid, "body": f"<p>{nota}</p>"})
    if all(p["id"] != prod for p in crudo["prods"]):
        crudo["prods"].append({"id": prod, "default_code": sku})
    return crudo


def _con_in01167(piezas: float = 2.0, otra: bool = False) -> dict:
    crudo = _agregar(_crudo(), 935001, "TEXCO/IN/01167", "2026-08-25 14:54:00", S24, "VEH-0006-GOM",
                     "Artículos en buen estado 3832142924750/FOLIO:1016169/FEDEX", piezas)
    if otra:                                   # otra recepción con el mismo error: ya no es un par único
        _agregar(crudo, 935002, "TEXCO/IN/01168", "2026-08-25 14:59:00", S24, "VEH-0006-GOM",
                 "3831429247500/FEDEX", piezas)
    return crudo


class GuiasRevision(unittest.TestCase):
    def test_envio_de_ml_cuando_la_serie_pase_a_5(self):
        self.assertEqual(FD.guias(FD.tokens("51234567890//MERCADO LIBRE")), [("ml", "51234567890")])

    def test_un_telefono_no_es_fedex(self):
        t = "CLIENTE LLAMO TEL 525512345678"
        self.assertEqual(FD.guias(FD.tokens(t), t), [])
        t = "525512345678//FEDEX"
        self.assertEqual(FD.guias(FD.tokens(t), t), [("fedex", "525512345678")])

    def test_el_numero_pegado_a_la_paqueteria(self):
        casos = {  # notas reales (IN/01476, IN/00962, IN/01526, IN/01183)
            "2000014738593125 1397938851//ESTAFETA": [("estafeta", "1397938851")],
            "Artículo en buen estado. 4313175926/FOLIO:01015464/DHL": [("dhl", "4313175926")],
            "#2000014730322811 PAQUETEXPRESS // 1902252493597": [("paquetexpress", "1902252493597")],
            "CUL01WE0583887 // PAQUETEXPRESS 795758303": [("paquetexpress", "CUL01WE0583887")],
        }
        for nota, esperado in casos.items():
            self.assertEqual(FD.guias(FD.tokens(nota), nota), esperado, nota)
        self.assertEqual(FD.guia_txt([("estafeta", "1397938851")]), "1397938851 (Estafeta)")

    def test_una_caja_de_full_no_es_guia(self):
        # IN/01314: «80144739100-2» es una caja de la bodega de ML, no un envío de 11 dígitos.
        t = "1176546900-12 80144739100-2 47586946148/ML"
        self.assertEqual(FD.guias(FD.tokens(t), t), [("ml", "47586946148")])

    def test_guia_no_reconocida(self):
        self.assertTrue(FD.guia_no_reconocida(" |  | 1003327559 |  | ESTAFETA", []))          # IN/01361
        self.assertFalse(FD.guia_no_reconocida("ARTICULO EN BUEN ESTADO", []))
        self.assertFalse(FD.guia_no_reconocida("2000018370368336//MERCADO LIBRE", []))     # solo la orden

    def test_retiro_de_full(self):
        retiro = "Ingreso de 221 artículos en buen estado // 1263041610-8 1263041610-16 1263041606-10"
        self.assertTrue(FD.es_retiro_full(retiro, []))
        # Una caja y el envío mal escrito (10 dígitos): es una devolución, no un retiro.
        self.assertFalse(FD.es_retiro_full("1196916205-9 4761982756/FOLIO: 01015244/ML", []))
        # Cajas con su envío de devolución (IN/01354): devoluciones de ventas FULL.
        t = "INGRESO DE 15 ARTICULOS // 842103303-3 47905265109 1230887404-4 47906151222"
        self.assertFalse(FD.es_retiro_full(t, FD.guias(FD.tokens(t), t)))


class LigaRevision(unittest.TestCase):
    def test_la_misma_guia_a_scrap_y_a_rack_gana_la_de_rack(self):
        por_dev, por_rec = FD.ligar_duro(DEVS + [GUIA_REPETIDA], FD.recepciones_de(_crudo()))
        l = por_dev["5599999999|TEC-0519-NAR-GRI"]
        self.assertEqual((l["rec"]["picking"], l["por"], l["otras"]), ("IN/01481", "guia", ["IN/01480"]))
        b = FD.armar_bandeja(DEVS + [GUIA_REPETIDA], _odoo_ok(), SUBIDAS, AHORA, 60)
        f = next(x for x in b["filas"] if x["id"] == "5599999999")
        self.assertEqual((f["grupo"], f["odoo"]["destino"], f["odoo_txt"], f["odoo_sub"]),
                         ("odoo", "rack", "Recibida · IN/01481 · 29-sep 14:43",
                          "A rack · por guía · Odoo 412 → 415 · también IN/01480 a SCRAP"))
        self.assertEqual(b["resumen"]["subieron"], 4)
        # El cuadre: la de rack bajo su subida; la de SCRAP aparte, diciendo de quién es la guía.
        d = FD.armar_detalle("TEC-0519-NAR-GRI", 14, BITACORA,
                             {"ok": True, "entradas": FD.entradas_sku_de(_crudo("TEC-0519-NAR-GRI", a_scrap=[])),
                              "error": None}, DEVS + [GUIA_REPETIDA], AHORA)
        r = next(x for x in d["subidas"][2]["recepciones"] if x["picking"] == "IN/01481")
        self.assertEqual(r["devolucion"]["id"], "5599999999")
        self.assertEqual(d["a_scrap"][0]["txt"], "Devolución 5599999999 · ML Kubera · misma guía que IN/01481"
                                                 " · a SCRAP: no cuenta para el stock")
        # Tres DEVOLUCIONES ligadas (5581207287, 5585383756 y ésta), aunque ésta salga en dos recepciones.
        self.assertEqual(d["resumen"]["ligadas"], 3)

    def test_la_guia_con_un_digito_de_mas(self):
        # IN/01167: «3832142924750/FOLIO:1016169/FEDEX»; la guía de la 5558975718 es 383142924750.
        recs = FD.recepciones_de(_con_in01167())
        por_dev, por_rec = FD.ligar_duro(DEVS + [VEH], recs)
        l = por_dev["5558975718|VEH-0006-GOM"]
        self.assertEqual((l["rec"]["picking"], l["por"], l["aprox"]),
                         ("IN/01167", "guia_aprox", ("383142924750", "3832142924750")))
        b = FD.armar_bandeja(DEVS + [VEH], {**_odoo_ok(), "recepciones": recs}, SUBIDAS, AHORA, 60)
        f = next(x for x in b["filas"] if x["id"] == "5558975718")
        self.assertEqual((f["grupo"], f["odoo"]["por"], f["odoo_sub"], f["accion"]),
                         ("odoo", "guia_aprox",
                          "A rack · por guía: la nota trae 3832142924750: un dígito de más que la guía 383142924750",
                          "—"))
        self.assertEqual((b["resumen"]["por_guia"], b["resumen"]["por_venta"]), (5, 2))
        # En el cuadre la guía mal tecleada no cuenta como «guía que kubera no tiene».
        solo_veh = [r for r in FD.entradas_sku_de(_con_in01167()) if "VEH-0006-GOM" in r["skus"]]
        d = FD.armar_detalle("VEH-0006-GOM", 60, [], {"ok": True, "entradas": solo_veh, "error": None},
                             DEVS + [VEH], AHORA)
        r = next(x for x in d["sin_subida"] if x["picking"] == "IN/01167")
        self.assertEqual((r["guia"], r["guias_sin_kubera"], r["en_kubera"], r["txt"]),
                         ("3832142924750 (FedEx)", 0, True, "Devolución 5558975718 · ML San Corpe · la nota trae "
                                                            "3832142924750: un dígito de más que la guía 383142924750"))
        self.assertEqual((d["resumen"]["ligadas"], d["resumen"]["sin_kubera"]), (1, 0))

    def test_la_guia_aproximada_solo_con_par_unico_y_las_mismas_piezas(self):
        for crudo in (_con_in01167(piezas=1.0), _con_in01167(otra=True)):
            por_dev, _ = FD.ligar_duro(DEVS + [VEH], FD.recepciones_de(crudo))
            self.assertNotIn("5558975718|VEH-0006-GOM", por_dev)
        # Los envíos de ML (11 dígitos) son seriales: un dígito de diferencia es OTRO envío.
        vecino = {**VEH, "envios": [_env("48096342889", None)], "sku": "TEC-0519-NAR-GRI", "piezas": 1}
        self.assertNotIn("5558975718|TEC-0519-NAR-GRI", FD.ligar_duro([vecino], FD.recepciones_de(_crudo()))[0])


class BandejaRevision(unittest.TestCase):
    def test_la_probable_es_de_cada_sku(self):
        a = _dev("5600000001", "TEC-0519-NAR-GRI", "2000019999999991", "2026-09-26 10:00:00",
                 "2026-09-26 16:00:00+00:00", texto_ml="El paquete llegó el lunes 28 de septiembre.",
                 cerrada="2026-09-26 12:00:00", reembolsada="2026-09-26 12:00:00",
                 envios=[_env("48999999991", "YYY")],
                 historia=[_h("abierta", None, "opened", "2026-09-26 10:00:01"),
                           _h("en_transito", "abierta", "shipped", "2026-09-26 12:00:00")])
        bit = SUBIDAS + [_log(1, "2026-09-30 18:00:00+00:00", "woocommerce", "odoo_delta",
                              "delta de Odoo (foto 7 -> 8)", "Woo 7 -> 8", "ODOO", sku="HERR-0035-VER")]
        b = FD.armar_bandeja(DEVS + [a, {**a, "sku": "HERR-0035-VER"}], {"ok": False, "error": "x", "leido": None},
                             bit, AHORA, 60)
        p = {x["sku"]: x["probable"]["texto"] for x in b["filas"] if x["id"] == "5600000001"}
        self.assertIn("Odoo 411 → 412", p["TEC-0519-NAR-GRI"])
        self.assertIn("Odoo 7 → 8", p["HERR-0035-VER"])

    def test_el_reintento_de_una_subida_llena_no_se_ofrece(self):
        # El +2 del 3-oct lo explican IN/01530 e IN/01531 (con guía). Si la escritura a Woo
        # falló y se reanotó, el reintento es la MISMA subida: tampoco se ofrece.
        fallo = _log(139757, "2026-10-03 17:53:24+00:00", "woocommerce", "odoo_delta",
                     "delta de Odoo (foto 415 -> 417)", "Woo 415 -> 417 FALLÓ: 500")
        reint = _log(139800, "2026-10-03 18:13:24+00:00", "woocommerce", "odoo_delta",
                     "delta de Odoo (foto 415 -> 417)", "Woo 415 -> 417")
        b = FD.armar_bandeja(DEVS, _odoo_ok(), [fallo, reint], AHORA, 60)
        self.assertIsNone(next(x for x in b["filas"] if x["id"] == "5585338270")["probable"])

    def test_odoo_se_lee_desde_la_apertura_mas_vieja(self):
        # Abierta el 10-sep, ML la da por entregada el 5-oct; Odoo la recibió el 15-sep.
        e = _dev("5600000002", "TEC-0519-NAR-GRI", "2000019999999992", "2026-09-10 10:00:00",
                 "2026-09-10 16:00:00+00:00", refund_at="delivered", envios=[_env("48999999992", "WWW")],
                 historia=[_h("abierta", None, "opened", "2026-09-10 10:00:01"),
                           _h("en_transito", "abierta", "shipped", "2026-09-11 12:00:00"),
                           _h("recibida", "en_transito", "delivered", "2026-10-05 12:00:00")])
        self.assertEqual(FD.desde_odoo(DEVS + [e], AHORA, 14), "2026-09-09 00:00:00")
        # Sin ella, la más vieja es la 5582365580: abierta el 23-sep, llegó el 28.
        self.assertEqual(FD.desde_odoo(DEVS, AHORA, 14), "2026-09-22 00:00:00")
        crudo = _agregar(_crudo(), 1, "TEXCO/IN/09999", "2026-09-15 15:00:00", S24, "TEC-0519-NAR-GRI",
                         "48999999992//MERCADO LIBRE")
        b = FD.armar_bandeja(DEVS + [e], {**_odoo_ok(), "recepciones": FD.recepciones_de(crudo)}, SUBIDAS, AHORA, 14)
        self.assertEqual(next(x for x in b["filas"] if x["id"] == "5600000002")["grupo"], "odoo")

    def test_las_que_ml_no_dice_a_donde_van(self):
        sin = {**DEVS[6], "id": "5572526399", "destino": None}
        b = FD.armar_bandeja(DEVS + [sin], _odoo_ok(), SUBIDAS, AHORA, 60)
        self.assertEqual((b["resumen"]["sin_destino"], b["resumen"]["a_full"], b["resumen"]["a_bodega"]), (1, 1, 9))
        self.assertNotIn("5572526399", {x["id"] for x in b["filas"]})


class CuadreRevision(unittest.TestCase):
    def _armar(self, crudo: dict, bitacora=None, pasada=None, devs=None) -> dict:
        return FD.armar_detalle("TEC-0519-NAR-GRI", 14, BITACORA if bitacora is None else bitacora,
                                {"ok": True, "entradas": FD.entradas_sku_de(crudo), "error": None},
                                devs or DEVS, AHORA, "", pasada)

    def test_a_rack_sin_subida_dice_si_stock_watch_ya_paso(self):
        # IN/01562 se validó a las 16:13; su subida es de las 16:22. Sin esa subida en la bitácora:
        sin_8oct = [f for f in BITACORA if f["id"] < 149593]
        crudo = _crudo("TEC-0519-NAR-GRI", a_scrap=[])
        d = self._armar(crudo, sin_8oct, pasada="2026-10-08 16:10:00")
        self.assertEqual([(r["picking"], r["espera"]) for r in d["sin_subida"]], [("IN/01562", True)])
        d = self._armar(crudo, sin_8oct, pasada="2026-10-08 16:20:00")
        self.assertEqual([(r["picking"], r["espera"]) for r in d["sin_subida"]], [("IN/01562", False)])
        self.assertEqual(d["resumen"]["entraron"], 7)              # sigue contando en «entraron»

    def test_varias_guias_ligada_en_parte(self):
        # Como IN/01296: la guía de una devolución de kubera y otra que kubera no tiene.
        crudo = _crudo("TEC-0519-NAR-GRI", a_scrap=[])
        for m in crudo["msgs"]:
            if m["res_id"] == 985790:
                m["body"] = "<p>47999999999//MERCADO LIBRE 383939214207//FEDEX</p>"
        d = self._armar(crudo)
        r = d["subidas"][0]["recepciones"][0]
        self.assertEqual((r["guia"], r["en_kubera"], r["guias_sin_kubera"], r["txt"]),
                         ("383939214207 (FedEx) (+1)", False, 1,
                          "Devolución 5585383756 · ML Kubera · 1 guía más que kubera no tiene"))
        self.assertEqual((d["resumen"]["ligadas"], d["resumen"]["sin_kubera"]), (2, 6))

    def test_las_ligadas_son_devoluciones_de_este_sku(self):
        # IN/01319 trae 6 de TEC-0573-MET y una de TEC-0551-PLU: el cuadre de TEC-0573 cuenta 6.
        crudo = _crudo("TEC-0519-NAR-GRI", a_scrap=[])
        for m in crudo["msgs"]:
            if m["res_id"] == 985790:
                m["body"] = "<p>383939214207//FEDEX 48129099447 48088384454//MERCADO LIBRE</p>"
        d = self._armar(crudo)
        self.assertEqual(d["subidas"][0]["recepciones"][0]["txt"], "Devolución 5585383756 · ML Kubera y 1 más de kubera")
        self.assertEqual(d["resumen"]["ligadas"], 3)            # sin la 5582365580 de HERR-0035-VER

    def test_retiro_de_full_aparte(self):
        crudo = _agregar(_crudo("TEC-0519-NAR-GRI", a_scrap=[]), 970001, "TEXCO/IN/01474", "2026-09-28 18:00:00",
                         Q25, "TEC-0519-NAR-GRI", "Ingreso de 221 artículos en buen estado // 1263041610-8 "
                         "1263041610-16", piezas=5.0)
        d = self._armar(crudo)
        r = next(x for x in d["sin_subida"] if x["picking"] == "IN/01474")
        self.assertEqual((r["tipo"], r["txt"]),
                         ("retiro_full", "Retiro de FULL: cajas de la bodega de ML, no una devolución de cliente"))
        self.assertEqual((d["resumen"]["entraron"], d["resumen"]["retiro_full"]), (7, 5))

    def test_guia_no_reconocida_en_el_cuadre(self):
        crudo = _agregar(_crudo("TEC-0519-NAR-GRI", a_scrap=[]), 970002, "TEXCO/IN/01361", "2026-09-28 18:00:00",
                         Q25, "TEC-0519-NAR-GRI", "ESTAFETA", folio="1003327559")
        r = next(x for x in self._armar(crudo)["sin_subida"] if x["picking"] == "IN/01361")
        self.assertEqual((r["guia"], r["guia_no_reconocida"], r["txt"]), (None, True, "Guía no reconocida en la nota"))

    def test_los_ajustes_sin_picking_llevan_clave_unica(self):
        # «Cantidad de producto actualizada» ×3 en producción: el nombre se repite, la clave no.
        crudo = _crudo("TEC-0519-NAR-GRI", a_scrap=[])
        INV = [14, "Virtual Locations/Inventory adjustment"]
        crudo["locs"] = crudo["locs"] + [{"id": 14, "usage": "inventory", "warehouse_id": False,
                                          "complete_name": INV[1]}]
        for i in (1, 2):
            crudo["movs"].append({"id": 7000 + i, "picking_id": False, "product_id": [73179, "[TEC-0519-NAR-GRI] …"],
                                  "quantity": 1.0, "date": f"2026-10-0{i + 4} 18:00:00", "location_id": INV,
                                  "location_dest_id": Q25, "is_inventory": True,
                                  "reference": "Cantidad de producto actualizada"})
        ajustes = [r for r in FD.entradas_sku_de(crudo) if r["tipo"] == "ajuste"]
        self.assertEqual([r["clave"] for r in ajustes], ["m7001·rack", "m7002·rack"])
        self.assertEqual({r["picking"] for r in ajustes}, {"Cantidad de producto actualizada"})


class FrenoOdoo(unittest.TestCase):
    D60 = "2026-08-08 00:00:00"

    def setUp(self):
        FD._odoo_cache.update(t=0.0, v=None, leido=None, desde=None)
        FD._odoo_freno.update(motivo=None, t=0.0)
        FD._sku_cache.clear()

    tearDown = setUp

    def test_una_relectura_a_la_vez_y_el_freno_es_de_todo_odoo(self):
        import threading
        llamadas = []

        def lento(desde_utc, kw=None):
            llamadas.append(desde_utc)
            time.sleep(0.3)
            raise TimeoutError("timed out")
        res = []
        with mock.patch.object(FD, "leer_odoo_crudo", side_effect=lento):
            hilos = [threading.Thread(target=lambda: res.append(FD.leer_odoo(self.D60))) for _ in range(4)]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join()
        # Las otras tres esperan la primera, ven su falla y no vuelven a Odoo.
        self.assertEqual((len(llamadas), [r["ok"] for r in res]), (1, [False] * 4))
        with mock.patch.object(FD, "leer_odoo_sku_crudo") as sku:
            r = FD.leer_odoo_sku("TEC-0519-NAR-GRI", 60)
        self.assertEqual((sku.call_count, r["ok"]), (0, False))
        self.assertIn("se reintenta en un minuto", r["error"])

    def test_la_falla_del_cuadre_frena_a_la_bandeja(self):
        with mock.patch.object(FD, "leer_odoo_sku_crudo", side_effect=TimeoutError("timed out")):
            self.assertFalse(FD.leer_odoo_sku("TEC-0519-NAR-GRI", 60)["ok"])
        with mock.patch.object(FD, "leer_odoo_crudo") as leer:
            self.assertFalse(FD.leer_odoo(self.D60)["ok"])
        self.assertEqual(leer.call_count, 0)

    def test_quien_llega_mientras_otra_lee_no_hace_fila(self):
        FD._odoo_cache.update(t=time.monotonic() - FD.ODOO_TTL - 1, v=[], leido="2026-10-08 22:40:00",
                              desde=self.D60)
        FD._odoo_leyendo.acquire()
        try:
            with mock.patch.object(FD, "ODOO_ESPERA", 0.05), mock.patch.object(FD, "leer_odoo_crudo") as leer:
                r = FD.leer_odoo(self.D60)
                self.assertEqual((r["ok"], r["error"]),
                                 (True, "Odoo se está leyendo para otra petición; se muestra la lectura de las 22:40"))
                FD._odoo_cache["desde"] = "2026-09-01 00:00:00"      # la que hay no cubre lo pedido
                r = FD.leer_odoo(self.D60)
                self.assertFalse(r["ok"])
                self.assertIn("se reintenta en un minuto", r["error"])
            self.assertEqual(leer.call_count, 0)
        finally:
            FD._odoo_leyendo.release()

    def test_la_relectura_cubre_lo_que_se_pidio_hace_poco(self):
        with mock.patch.object(FD, "leer_odoo_crudo", return_value=_crudo()) as leer:
            FD.leer_odoo(self.D60)
            FD._odoo_cache["t"] = time.monotonic() - FD.ODOO_TTL - 1
            FD.leer_odoo("2026-09-23 00:00:00")                     # la de 14 días relee desde la de 60
        self.assertEqual([c.args[0] for c in leer.call_args_list], [FD._utc(self.D60)] * 2)

    def test_el_plazo_total_de_la_lectura(self):
        vistos = []
        with mock.patch.object(FD, "_kw_solo_lectura", side_effect=lambda *a, timeout, **k: vistos.append(timeout)):
            kw = FD._kw_con_plazo(1.4, 45.0)
            kw("stock.picking", "search_read", [[]])
            time.sleep(0.5)
            with self.assertRaises(TimeoutError):
                kw("stock.move", "search_read", [[]])
        self.assertTrue(1.3 < vistos[0] <= 1.4)                   # cada llamada lleva lo que queda


if __name__ == "__main__":
    unittest.main()
