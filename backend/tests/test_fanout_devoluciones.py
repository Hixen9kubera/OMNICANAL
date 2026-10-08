"""Pruebas del carril de devoluciones en la trazabilidad de un SKU
(`services/fanout_vivo.py`: `ligar_devoluciones`, `_pasos_devolucion`,
`_armar_devoluciones` y su armado dentro de `historia`).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. La liga: una devolución que LLEGÓ A NUESTRA BODEGA toma la primera subida de
     Odoo dentro de [llegada − holgura, llegada + N días]. La subida es la de la FOTO
     de Odoo, no la de Woo: «Woo 132 -> 134» con foto 134 -> 134 no es reingreso. No
     una bajada, no una subida de Woo, no una de antes de llegar (salvo la holgura del
     cierre de ML y de Temu, y nunca antes de abrirse), no una de fuera de la ventana.
     Una subida de +3 alcanza para tres devoluciones de 1, una de +1 no se cuenta dos
     veces, y un delta que falló y su reintento son UNA subida. Las que van al almacén
     de ML no se ligan con Odoo.
  2. Sin subida: «todavía no se ve» mientras la ventana sigue abierta, «no se ve
     reingreso» cuando ya cerró, y «fuera del periodo» si la ventana empieza antes de
     lo cargado.
  3. Los pasos: la fila con que el trigger registra la PRIMERA vez que se vio una
     devolución (`estado_anterior` NULL) no es un hecho y no es paso, venga del backfill
     o de un webhook; las transiciones sí, aunque digan 'backfill'. Lo que falte sale
     de los hitos de la cabecera.
  4. El armado en `historia`: el renglón de la devolución y la nota en la subida de
     Odoo, el resumen del carril, la ventana configurable (settings, ?liga= y su tope)
     y que una falla al leer una fuente no tira la otra ni la página.
     Caso real del sandbox: MUE-0218-VIN-L, devolución 5574456697 (2 pzs) entregada
     el 10-sep y la foto de Odoo 127 → 129 el 14-sep.
  5. Las consultas: qué columnas y parámetros llevan (el SQL se corrió aparte contra
     el sandbox, solo lectura).

No se llama a kubera: las funciones puras reciben los datos ya leídos y `historia`
corre con `sdb` simulado.

    cd backend && python -m unittest tests.test_fanout_devoluciones -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fanout_vivo as V  # noqa: E402

AHORA = "2026-10-08 12:00:00"


def _dev(id_: str, llegada: str | None, piezas: int = 1, destino: str = "seller_address",
         llego: bool = True, holgura_h: int = 0, no_antes: str | None = None) -> dict:
    return {"id": id_, "nombre": "ML San Corpe", "destino": destino, "llego": llego,
            "llegada": llegada, "piezas": piezas, "holgura_h": holgura_h, "no_antes": no_antes}


def _woo(hora: str, de: int, a: int, origen: str = "odoo", fallo: bool = False,
         woo: tuple[int, int] | None = None) -> dict:
    """Un renglón «woo». `de`/`a` es la foto de Odoo (lo que mide la liga); `woo`, lo
    escrito en Woo si es distinto."""
    wd, wa = woo or (de, a)
    if origen != "odoo":
        return {"tipo": "woo", "hora": hora, "origen": origen, "de": de, "a": a,
                "odoo_de": None, "odoo_a": None, "fallo": fallo}
    return {"tipo": "woo", "hora": hora, "origen": origen, "de": wd, "a": wa,
            "odoo_de": de, "odoo_a": a, "fallo": fallo}


class Liga(unittest.TestCase):
    def test_toma_la_primera_subida_de_odoo_despues_de_llegar(self):
        woo = [_woo("2026-09-09 08:00:00", 130, 132),      # antes de llegar: no
               _woo("2026-09-11 08:00:00", 132, 131),      # bajada: no
               _woo("2026-09-12 08:00:00", 131, 133, "woo"),   # subida de Woo, no de Odoo: no
               _woo("2026-09-13 18:06:02", 132, 134),
               _woo("2026-09-14 08:00:00", 134, 136)]
        por_dev, por_sub = V.ligar_devoluciones([_dev("5574456697", "2026-09-10 12:33:27", 2)], woo, AHORA)
        liga = por_dev["5574456697"]
        self.assertEqual(liga["k"], "reingreso")
        self.assertEqual((liga["hora"], liga["dias"], liga["sube"]), ("2026-09-13 18:06:02", 3, 2))
        self.assertEqual(liga["texto"], "Reingresó a Odoo el 13-sep (+3 d): Odoo 132 → 134.")
        self.assertEqual(por_sub, {3: [{"id": "5574456697", "nombre": "ML San Corpe", "piezas": 2}]})

    def test_subida_mayor_que_la_devolucion_lo_dice(self):
        por_dev, _ = V.ligar_devoluciones([_dev("D1", "2026-09-29 13:02:40")],
                                          [_woo("2026-09-29 14:29:26", 0, 7)], AHORA)
        self.assertEqual(por_dev["D1"]["texto"],
                         "Reingresó a Odoo el 29-sep (el mismo día): Odoo 0 → 7; "
                         "la subida es de +7 y la devolución trae 1.")

    def test_una_subida_reparte_sus_piezas_y_no_se_cuenta_dos_veces(self):
        devs = [_dev("A", "2026-09-01 10:00:00"), _dev("B", "2026-09-01 11:00:00"),
                _dev("C", "2026-09-01 12:00:00"), _dev("D", "2026-09-01 13:00:00")]
        woo = [_woo("2026-09-03 10:00:00", 0, 3), _woo("2026-09-05 10:00:00", 3, 4)]
        por_dev, por_sub = V.ligar_devoluciones(devs, woo, AHORA)
        self.assertEqual([por_dev[x]["hora"][:10] for x in "ABCD"],
                         ["2026-09-03", "2026-09-03", "2026-09-03", "2026-09-05"])
        self.assertEqual([e["id"] for e in por_sub[0]], ["A", "B", "C"])
        self.assertEqual([e["id"] for e in por_sub[1]], ["D"])

    def test_fuera_de_ventana_y_ventana_configurable(self):
        devs = [_dev("A", "2026-09-01 10:00:00")]
        woo = [_woo("2026-09-12 10:00:00", 5, 6)]           # 11 días después
        por_dev, por_sub = V.ligar_devoluciones(devs, woo, AHORA, dias=10)
        self.assertEqual(por_dev["A"]["k"], "sin_reingreso")
        self.assertEqual(por_dev["A"]["texto"],
                         "Llegó a nuestra bodega el 1-sep y no se ve reingreso en Odoo en los 10 días siguientes.")
        self.assertEqual(por_sub, {})
        por_dev, _ = V.ligar_devoluciones(devs, woo, AHORA, dias=12)
        self.assertEqual(por_dev["A"]["k"], "reingreso")

    def test_ventana_abierta_dice_todavia(self):
        por_dev, _ = V.ligar_devoluciones([_dev("A", "2026-10-06 10:00:00")], [], AHORA)
        self.assertEqual(por_dev["A"]["k"], "espera")
        self.assertIn("todavía no se ve reingreso en Odoo", por_dev["A"]["texto"])

    def test_solo_las_que_llegaron_a_nuestra_bodega(self):
        woo = [_woo("2026-09-02 10:00:00", 1, 5)]
        devs = [_dev("FULL", "2026-09-01 10:00:00", destino="warehouse"),
                _dev("CAMINO", None, llego=False),
                _dev("SIN", "2026-09-01 10:00:00", destino=None)]
        por_dev, por_sub = V.ligar_devoluciones(devs, woo, AHORA)
        self.assertEqual((por_dev, por_sub), ({}, {}))

    def test_llegada_sin_fecha_no_se_liga(self):
        por_dev, _ = V.ligar_devoluciones([_dev("A", None)], [_woo("2026-09-02 10:00:00", 1, 2)], AHORA)
        self.assertEqual(por_dev["A"]["k"], "sin_fecha")

    def test_holgura_de_temu_acepta_la_subida_de_antes(self):
        # El vigilante VE la devolución validada después del reingreso en Odoo.
        woo = [_woo("2026-09-10 08:00:00", 2, 3)]
        sin, _ = V.ligar_devoluciones([_dev("T", "2026-09-10 20:00:00")], woo, AHORA)
        con, _ = V.ligar_devoluciones([_dev("T", "2026-09-10 20:00:00", holgura_h=24)], woo, AHORA)
        self.assertEqual(sin["T"]["k"], "sin_reingreso")
        self.assertEqual((con["T"]["k"], con["T"]["dias"]), ("reingreso", 0))

    def test_woo_sube_pero_odoo_no_no_es_reingreso(self):
        # Caso real (MUE-0218-VIN-L, 13-sep 18:06): stock_watch le devolvió a Woo lo que
        # Odoo ya tenía tras una venta en Woo. Woo 132 -> 134, foto de Odoo 134 -> 134.
        woo = [_woo("2026-09-13 18:06:02", 134, 134, woo=(132, 134)),
               _woo("2026-09-14 21:44:00", 127, 129, woo=(127, 129))]
        por_dev, por_sub = V.ligar_devoluciones([_dev("5574456697", "2026-09-10 12:33:27", 2)], woo, AHORA)
        self.assertEqual(por_dev["5574456697"]["texto"], "Reingresó a Odoo el 14-sep (+4 d): Odoo 127 → 129.")
        self.assertEqual(list(por_sub), [1])

    def test_odoo_que_baja_aunque_woo_suba_no_cuenta(self):
        woo = [_woo("2026-09-02 10:00:00", 9, 8, woo=(5, 8))]
        por_dev, por_sub = V.ligar_devoluciones([_dev("A", "2026-09-01 10:00:00")], woo, AHORA)
        self.assertEqual((por_dev["A"]["k"], por_sub), ("sin_reingreso", {}))

    def test_un_delta_que_fallo_y_su_reintento_son_una_subida(self):
        # MUE-0218-VIN-L el 23-sep: 12:49 falló la escritura, 13:10 el reintento; misma foto.
        woo = [_woo("2026-09-23 12:49:00", 120, 122, fallo=True), _woo("2026-09-23 13:10:00", 120, 122)]
        devs = [_dev("A", "2026-09-22 10:00:00", 2), _dev("B", "2026-09-22 11:00:00", 2)]
        por_dev, por_sub = V.ligar_devoluciones(devs, woo, AHORA)
        self.assertEqual((por_dev["A"]["k"], por_dev["A"]["hora"]), ("reingreso", "2026-09-23 12:49:00"))
        self.assertEqual(por_dev["B"]["k"], "sin_reingreso")
        self.assertEqual(list(por_sub), [0])

    def test_dos_subidas_iguales_sin_falla_son_dos(self):
        # Sin la falla no es un reintento: Odoo subió dos veces de 120 a 122 (vendió en medio).
        woo = [_woo("2026-09-23 12:49:00", 120, 122), _woo("2026-09-24 13:10:00", 120, 122)]
        devs = [_dev("A", "2026-09-22 10:00:00", 2), _dev("B", "2026-09-22 11:00:00", 2)]
        por_dev, _ = V.ligar_devoluciones(devs, woo, AHORA)
        self.assertEqual([por_dev[x]["k"] for x in "AB"], ["reingreso", "reingreso"])

    def test_holgura_del_cierre_de_ml_y_tope_de_la_apertura(self):
        # ML cierra hasta ~3 días después de la entrega real: un reingreso 24 h ANTES del
        # cierre vale con la holgura, pero nunca uno de antes de abrirse la devolución.
        woo = [_woo("2026-09-09 12:00:00", 4, 5)]
        sin, _ = V.ligar_devoluciones([_dev("A", "2026-09-10 12:00:00")], woo, AHORA)
        con, _ = V.ligar_devoluciones([_dev("A", "2026-09-10 12:00:00", holgura_h=72)], woo, AHORA)
        tope, _ = V.ligar_devoluciones([_dev("A", "2026-09-10 12:00:00", holgura_h=72,
                                             no_antes="2026-09-09 18:00:00")], woo, AHORA)
        self.assertEqual(sin["A"]["k"], "sin_reingreso")
        self.assertEqual((con["A"]["k"], con["A"]["texto"]),
                         ("reingreso", "Reingresó a Odoo el 9-sep (−1 d): Odoo 4 → 5."))
        self.assertEqual(tope["A"]["k"], "sin_reingreso")

    def test_ventana_antes_de_lo_cargado_no_dice_sin_reingreso(self):
        devs = [_dev("A", "2026-09-01 10:00:00"), _dev("B", "2026-09-20 10:00:00")]
        por_dev, por_sub = V.ligar_devoluciones(devs, [_woo("2026-09-02 10:00:00", 1, 2)], AHORA,
                                                desde_datos="2026-09-05 00:00:00")
        self.assertEqual(por_dev["A"]["k"], "fuera_periodo")
        self.assertIn("amplía los días", por_dev["A"]["texto"])
        self.assertEqual(por_dev["B"]["k"], "sin_reingreso")
        self.assertEqual(por_sub, {})          # la subida no se gasta en la de fuera

    def test_una_subida_grande_si_se_liga_y_lo_dice(self):
        # Comportamiento fijado: una entrada de contenedor (+200) dentro de la ventana se
        # toma para una devolución de 1, y el texto avisa del tamaño.
        por_dev, por_sub = V.ligar_devoluciones([_dev("A", "2026-09-01 10:00:00")],
                                                [_woo("2026-09-02 10:00:00", 10, 210)], AHORA)
        self.assertEqual(por_dev["A"]["texto"],
                         "Reingresó a Odoo el 2-sep (+1 d): Odoo 10 → 210; la subida es de +200 y la devolución trae 1.")
        self.assertEqual(por_sub[0], [{"id": "A", "nombre": "ML San Corpe", "piezas": 1}])


class Pasos(unittest.TestCase):
    BASE = {"abierta": "2026-09-09 20:09:59", "cerrada": "2026-09-10 12:33:27",
            "reembolsada": "2026-09-10 12:33:27", "estado": "reembolsada", "estado_canal": "delivered"}

    def test_backfill_no_es_paso_y_se_completa_con_la_cabecera(self):
        d = {**self.BASE, "historia": [{"estado": "reembolsada", "antes": None, "via": "backfill",
                                        "hora": "2026-10-06 03:59:42"}]}
        pasos = V._pasos_devolucion(d)
        self.assertEqual([(p["texto"], p["hora"], p["de"]) for p in pasos],
                         [("abierta", "2026-09-09 20:09:59", "apertura"),
                          ("entregada", "2026-09-10 12:33:27", "cierre de ML"),
                          ("reembolsada", "2026-09-10 12:33:27", "reembolso")])
        self.assertEqual(V._llegada(d, pasos), (True, "2026-09-10 12:33:27"))

    def test_la_historia_en_vivo_manda(self):
        d = {**self.BASE, "historia": [
            {"estado": "abierta", "antes": None, "via": "webhook", "hora": "2026-09-09 20:10:01"},
            {"estado": "en_transito", "antes": "abierta", "via": "webhook", "hora": "2026-09-09 22:00:00"},
            {"estado": "recibida", "antes": "en_transito", "via": "webhook", "hora": "2026-09-12 09:00:00"}]}
        pasos = V._pasos_devolucion(d)
        self.assertEqual([p["texto"] for p in pasos], ["abierta", "en tránsito", "reembolsada", "entregada"])
        self.assertEqual(V._llegada(d, pasos), (True, "2026-09-12 09:00:00"))

    def test_la_primera_insercion_no_es_paso_venga_de_donde_venga(self):
        # Capturada por primera vez por webhook YA reembolsada, semanas después: esa fila
        # no es el reembolso; el reembolso es el de la cabecera.
        d = {**self.BASE, "historia": [{"estado": "reembolsada", "antes": None, "via": "webhook",
                                        "hora": "2026-11-18 10:00:00"},
                                       {"estado": "recibida", "antes": None, "via": "sondeo",
                                        "hora": "2026-11-18 10:00:00"}]}
        pasos = V._pasos_devolucion(d)
        self.assertEqual([(p["texto"], p["hora"]) for p in pasos],
                         [("abierta", "2026-09-09 20:09:59"), ("entregada", "2026-09-10 12:33:27"),
                          ("reembolsada", "2026-09-10 12:33:27")])
        self.assertEqual(V._llegada(d, pasos), (True, "2026-09-10 12:33:27"))
        self.assertEqual(V._holgura_ml(pasos), 72)

    def test_una_transicion_etiquetada_backfill_si_es_paso(self):
        # `detectado_via` se congela con la primera captura: el barrido que vio el cambio
        # después también dice 'backfill', y el cambio es real.
        d = {**self.BASE, "historia": [{"estado": "recibida", "antes": "en_transito", "via": "backfill",
                                        "hora": "2026-09-10 00:30:00"}]}
        pasos = V._pasos_devolucion(d)
        self.assertEqual([(p["texto"], p["de"]) for p in pasos],
                         [("abierta", "apertura"), ("entregada", "backfill"), ("reembolsada", "reembolso")])
        self.assertEqual(V._llegada(d, pasos), (True, "2026-09-10 00:30:00"))
        self.assertEqual(V._holgura_ml(pasos), 0)

    def test_rechazada_dice_por_que_y_no_llego(self):
        d = {"abierta": "2026-08-22 18:30:17", "cerrada": "2026-09-01 23:39:00", "reembolsada": None,
             "estado": "rechazada", "estado_canal": "expired", "historia": []}
        pasos = V._pasos_devolucion(d)
        self.assertEqual(pasos[-1]["texto"], "rechazada (venció)")
        self.assertEqual(V._llegada(d, pasos), (False, None))

    def test_liga_inicial(self):
        self.assertEqual(V._liga_inicial("reembolsada", "delivered", "warehouse", True)["k"], "full")
        self.assertEqual(V._liga_inicial("abierta", None, None, False)["k"], "sin_destino")
        self.assertIsNone(V._liga_inicial("reembolsada", "delivered", "seller_address", True))
        self.assertEqual(V._liga_inicial("reembolsada", "not_delivered", "seller_address", False)["texto"],
                         "Se reembolsó sin que la caja regresara (no se entregó).")
        self.assertEqual(V._liga_inicial("en_transito", "shipped", "seller_address", False)["k"], "camino")


def _fila_ml(**kw) -> dict:
    base = {"cuenta": "SANCORFASHION", "id": "5574456697", "estado": "reembolsada", "estado_canal": "delivered",
            "destino": "seller_address", "es_fulfillment": False, "venta_contaba": False, "estado_dinero": "refunded",
            "pedido": "2000013", "motivo": "No cumple con las características de la publicación", "piezas": 2,
            "ts": datetime(2026, 9, 10, 2, 9, 59, tzinfo=timezone.utc), "abierta": "2026-09-09 20:09:59",
            "cerrada": "2026-09-10 12:33:27", "reembolsada": "2026-09-10 12:33:27", "historia": []}
    return {**base, **kw}


class Armado(unittest.TestCase):
    def test_renglon_ml_y_temu(self):
        temu = {"canal": "temu", "cuenta": "TEMU", "id": "PO-1", "accion": "cancelada_devuelta", "odoo_name": "S123",
                "piezas": 1, "ts": datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc),
                "vendida": "2026-09-15 10:00:00", "vista": "2026-09-20 12:00:00"}
        ml, t = V._armar_devoluciones([_fila_ml()], [temu])
        self.assertEqual((ml["nombre"], ml["destino_txt"], ml["piezas"], ml["estado_txt"], ml["llego"]),
                         ("ML San Corpe", "a nuestra bodega", 2, "reembolsada", True))
        self.assertEqual(ml["venta_txt"], "la venta ya estaba cancelada: no se resta dos veces")
        self.assertIsNone(ml["liga"])
        self.assertEqual(V._armar_devoluciones([_fila_ml(destino="warehouse")], [])[0]["liga"]["k"], "full")
        self.assertEqual((t["nombre"], t["estado_txt"], t["llego"], t["llegada"], t["holgura_h"]),
                         ("Temu", "devuelta en Odoo", True, "2026-09-20 12:00:00", 72))
        self.assertEqual(t["no_antes"], "2026-09-15 10:00:00")
        self.assertEqual((ml["holgura_h"], ml["no_antes"]), (72, "2026-09-09 20:09:59"))

    def test_venta_contaba_desconocida_y_sin_destino(self):
        d = V._armar_devoluciones([_fila_ml(venta_contaba=None, destino=None)], [])[0]
        self.assertEqual(d["venta_txt"], "no se sabe si la venta contaba")
        self.assertEqual((d["destino_txt"], d["liga"]["k"]), ("sin dato", "sin_destino"))

    def test_resumen(self):
        devs = V._armar_devoluciones([_fila_ml(), _fila_ml(id="B", destino="warehouse", piezas=1),
                                      _fila_ml(id="C", estado="en_transito", estado_canal="shipped", piezas=1)], [])
        woo = [_woo("2026-09-13 18:06:02", 132, 134)]
        V._aplicar_liga(devs, woo, AHORA, 10)
        self.assertEqual(V._resumen_devoluciones(devs),
                         {"n": 3, "piezas": 4, "a_bodega": 1, "reingresaron": 1, "a_full": 1, "en_camino": 1})
        self.assertEqual(woo[0]["liga_txt"], "Coincide con la devolución 5574456697 de ML San Corpe (2 pzs).")
        self.assertIn("no una liga dura", woo[0]["liga_nota"])
        self.assertIn("no una liga dura", devs[0]["liga"]["nota"])


def _fila_log(hora: str, motivo: str, resultado: str, id_: int = 1) -> dict:
    """Un renglón `odoo_delta` de stock_watch en `ops.fanout_log` (hora de CDMX = UTC−6)."""
    ts = datetime.strptime(hora, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) + timedelta(hours=6)
    return {"id": id_, "ts": ts, "hora": hora, "motivo": motivo, "canal": "woocommerce", "cuenta": "",
            "accion": "odoo_delta", "resultado": resultado, "stock_canal": None, "objetivo": None,
            "stock_drop": None, "reciente": False, "item_id": "", "dry_run": False}


class EnHistoria(unittest.TestCase):
    """`historia` completa con la BD simulada: el caso de MUE-0218-VIN-L del sandbox."""

    # Lo que de verdad anotó stock_watch para MUE-0218-VIN-L (sandbox): el 13-sep Woo
    # sube pero Odoo no (foto 134 -> 134); el 14-sep Odoo sí sube, 127 -> 129.
    LOG = [_fila_log("2026-09-14 21:44:00", "delta de Odoo (foto 127 -> 129)", "Woo 127 -> 129", 2),
           _fila_log("2026-09-13 18:06:02", "delta de Odoo (foto 134 -> 134)", "Woo 132 -> 134", 1)]
    # La ventana de la liga: subida a +12 d de la llegada (10-sep 12:33 → 22-sep).
    LOG_12 = [_fila_log("2026-09-22 16:00:00", "delta de Odoo (foto 120 -> 122)", "Woo 120 -> 122")]

    def _correr(self, ml, temu=None, falla=False, log=None, dias=45, falla_temu=False, **kw):
        filas = self.LOG if log is None else log

        def fetch_all(sql, params=None):
            return list(filas) if "from ops.fanout_log" in sql else []
        ml_fn = mock.Mock(side_effect=RuntimeError("sin tabla")) if falla else mock.Mock(return_value=ml)
        temu_fn = (mock.Mock(side_effect=RuntimeError("no existe la columna cuenta")) if falla_temu
                   else mock.Mock(return_value=temu or []))
        with mock.patch.object(V, "reparto", return_value=["tiktok", "temu", "mercado_libre"]), \
             mock.patch.object(V, "_ahora_local", return_value={"ahora": AHORA}), \
             mock.patch.object(V, "_marcas_seguro", return_value={}), \
             mock.patch.object(V, "_sin_orden", return_value={}), \
             mock.patch.object(V, "_causas", return_value={}), \
             mock.patch.object(V, "_devoluciones_ml", ml_fn), \
             mock.patch.object(V, "_devoluciones_temu", temu_fn), \
             mock.patch.object(V.sdb, "fetch_one", return_value={}), \
             mock.patch.object(V.sdb, "fetch_all", side_effect=fetch_all):
            return V.historia("MUE-0218-VIN-L", dias, **kw)

    def test_la_devolucion_y_la_subida_quedan_ligadas(self):
        h = self._correr([_fila_ml()])
        self.assertTrue(h["devoluciones_ok"])
        dev = next(x for x in h["items"] if x["tipo"] == "devolucion")
        sube, falsa = (x for x in h["items"] if x["tipo"] == "woo")
        self.assertEqual(dev["liga"]["texto"], "Reingresó a Odoo el 14-sep (+4 d): Odoo 127 → 129.")
        self.assertEqual(sube["devoluciones"], [{"id": "5574456697", "nombre": "ML San Corpe", "piezas": 2}])
        self.assertEqual((sube["odoo_de"], sube["odoo_a"], sube["de"], sube["a"]), (127, 129, 127, 129))
        self.assertNotIn("devoluciones", falsa)          # Woo 132 -> 134 con Odoo quieto
        self.assertEqual(h["resumen"]["devoluciones"]["reingresaron"], 1)
        # La subida (14-sep) va antes que la devolución (abierta el 9-sep): más nuevo primero.
        self.assertLess(h["items"].index(sube), h["items"].index(dev))
        for k in ("_t", "holgura_h", "no_antes"):
            self.assertNotIn(k, dev)

    def test_si_no_se_leen_las_devoluciones_la_pagina_sigue(self):
        with self.assertLogs("omnicanal.fanout_vivo", level="WARNING"):
            h = self._correr([], falla=True)
        self.assertFalse(h["devoluciones_ok"])
        self.assertEqual(h["devoluciones_fallas"], ["ml"])
        self.assertEqual(h["resumen"]["devoluciones"]["n"], 0)
        self.assertEqual(h["resumen"]["cambios_woo"], 2)
        self.assertNotIn("devoluciones", h["items"][0])

    def test_una_falla_de_temu_no_tira_las_de_ml(self):
        with self.assertLogs("omnicanal.fanout_vivo", level="WARNING"):
            h = self._correr([_fila_ml()], falla_temu=True)
        self.assertEqual((h["devoluciones_ok"], h["devoluciones_fallas"]), (False, ["temu"]))
        self.assertEqual(h["resumen"]["devoluciones"]["reingresaron"], 1)

    def test_solo_con_devoluciones_el_sku_existe(self):
        h = self._correr([_fila_ml()], log=[])
        self.assertTrue(h["existe"])
        self.assertEqual(h["resumen"]["devoluciones"]["n"], 1)

    def test_la_ventana_por_default_sale_de_settings(self):
        from config import settings
        with mock.patch.object(settings, "fanout_devol_liga_dias", 10):
            h = self._correr([_fila_ml()], log=self.LOG_12)
        self.assertEqual(h["resumen"]["devoluciones"]["reingresaron"], 0)
        with mock.patch.object(settings, "fanout_devol_liga_dias", 13):
            h = self._correr([_fila_ml()], log=self.LOG_12)
        self.assertEqual(h["resumen"]["devoluciones"]["reingresaron"], 1)

    def test_liga_dias_manda_sobre_settings(self):
        from config import settings
        with mock.patch.object(settings, "fanout_devol_liga_dias", 10):
            h = self._correr([_fila_ml()], log=self.LOG_12, liga_dias=15)
        self.assertEqual(h["resumen"]["devoluciones"]["reingresaron"], 1)

    def test_la_ventana_se_topa_en_30_dias(self):
        # Llegó (cierre de ML) el 5-sep 12:00; la subida es del 6-oct, +31 d.
        fila = _fila_ml(abierta="2026-09-04 10:00:00", cerrada="2026-09-05 12:00:00",
                        reembolsada="2026-09-05 12:00:00")
        log = [_fila_log("2026-10-06 13:00:00", "delta de Odoo (foto 3 -> 5)", "Woo 3 -> 5")]
        h = self._correr([fila], log=log, liga_dias=99)
        dev = next(x for x in h["items"] if x["tipo"] == "devolucion")
        self.assertEqual(dev["liga"]["k"], "sin_reingreso")
        self.assertIn("en los 30 días siguientes", dev["liga"]["texto"])

    def test_la_llegada_antes_del_periodo_es_fuera_de_periodo(self):
        # Con 14 días, lo cargado empieza el 24-sep: la del 10-sep no se puede juzgar.
        h = self._correr([_fila_ml()], dias=14)
        dev = next(x for x in h["items"] if x["tipo"] == "devolucion")
        self.assertEqual(dev["liga"]["k"], "fuera_periodo")


class Router(unittest.TestCase):
    def test_liga_llega_como_liga_dias(self):
        from routers import fanout as R
        with mock.patch.object(V, "historia", return_value={"ok": True}) as h:
            R.historia(sku="MUE-0218-VIN-L", dias=30, liga=15)
            R.historia(sku="MUE-0218-VIN-L", dias=30, liga=None)
        self.assertEqual(h.call_args_list, [mock.call("MUE-0218-VIN-L", 30, liga_dias=15),
                                            mock.call("MUE-0218-VIN-L", 30, liga_dias=None)])


class Consultas(unittest.TestCase):
    """Qué leen las dos consultas: columnas que consume `_armar_devoluciones` y sus
    parámetros. El SQL se corrió aparte contra el sandbox (solo lectura, ROLLBACK)."""

    def _sql(self, fn) -> tuple[str, dict]:
        with mock.patch.object(V.sdb, "fetch_all", return_value=[]) as fa:
            fn("MUE-0218-VIN-L", 30)
        (sql, params), _ = fa.call_args
        return " ".join(sql.split()), params

    def test_devoluciones_ml(self):
        sql, p = self._sql(V._devoluciones_ml)
        self.assertEqual(p, {"s": "MUE-0218-VIN-L", "d": 30, "z": V._ZONA})
        self.assertIn("join channel.return_items i using (canal, cuenta, external_return_id)", sql)
        self.assertIn("greatest(r.abierta_at, r.cerrada_at, r.reembolsada_at) > now() - make_interval(days => %(d)s)", sql)
        # El destino: la columna o, si viene NULL (captura de main), el crudo de ML.
        self.assertIn("coalesce(r.destino, (", sql)
        self.assertIn("r.payload->'returns'->'shipments'", sql)
        self.assertIn("'seller_address'", sql)
        self.assertIn(r"~ '^\d{4}-\d{2}-\d{2}T'", sql)
        for col in ("r.cuenta", "r.id", "r.estado", "r.estado_canal", "r.destino", "r.es_fulfillment",
                    "r.venta_contaba", "r.estado_dinero", "r.pedido", "r.motivo", "r.piezas", "as ts",
                    "as abierta", "as cerrada", "as reembolsada", "as historia", "'antes', h.estado_anterior"):
            self.assertIn(col, sql)

    def test_devoluciones_temu(self):
        sql, p = self._sql(V._devoluciones_temu)
        self.assertEqual(p, {"s": "MUE-0218-VIN-L", "d": 30, "z": V._ZONA,
                             "a": ["cancelada_revisar", "cancelada_devuelta"]})
        self.assertIn("from ops.odoo_sale_orders o join ops.odoo_sale_order_items i", sql)
        self.assertIn("o.accion = any(%(a)s)", sql)
        for col in ("o.canal", "o.cuenta", "as id", "o.accion", "o.odoo_name", "as piezas", "as ts",
                    "as vendida", "as vista"):
            self.assertIn(col, sql)


if __name__ == "__main__":
    unittest.main()
