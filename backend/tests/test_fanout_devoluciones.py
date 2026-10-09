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
  6. El despacho (v0.628.0): con `refund_at='shipped'` el cierre de ML es la SALIDA de
     la caja («reembolsada al despachar»), no la entrega. La llegada es la vista en vivo
     o el día del texto de ML («El paquete llegó el viernes 2 de octubre.», el día
     completo); sin ninguna queda sin fecha. La ventana nunca empieza antes de que la
     caja salió. Caso real de producción: TEC-0519-NAR-GRI, 5585338270, cuyo +3 del
     29-sep 14:49 (otras guías, 1.8 h antes de salir) ya no se liga. Sin `refund_at`
     todo queda como antes; un envío cancelado o vencido no toma subida.
     La revisión (todo con filas reales de producción, 8-oct): la entrega en vivo con
     el despacho es la fila de la historia con `estado_canal='delivered'` (5585383756
     reingresa el 8-oct 16:22, IN/01562 por guía); «en tránsito» y el cierre se funden
     por la diferencia (≤3 min), no por el minuto; el cierre solo es «reembolsada al
     despachar» si el dinero salió en ese instante (FULL 5572526388 reembolsa 66.8 h
     después) y la transición en vivo a `reembolsada` ya no repite el reembolso
     (5576286635); el día del texto cuadra con el día de la semana y no cae más de 60
     días después de salir (ni en el cruce de año); con 'delivered' el día del texto
     manda sobre el cierre − 72 h (5543262662); con la misma llegada va primero la que
     salió antes (5570322560 y 5570201794), y con solo el día la liga no afirma el
     reingreso.

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

    def test_excluir_una_subida_sin_romper_su_reintento(self):
        # v0.629.0: la pestaña Devoluciones excluye las subidas que ya explican recepciones
        # con guía. Se excluye por índice: si se quitara el renglón, su reintento (la
        # escritura a Woo falló y se volvió a anotar idéntica) contaría como subida nueva.
        woo = [_woo("2026-10-03 11:53:24", 415, 417, fallo=True), _woo("2026-10-03 12:13:24", 415, 417)]
        dev = [_dev("5585338270", "2026-10-02")]
        self.assertEqual(V.ligar_devoluciones(dev, woo, AHORA)[0]["5585338270"]["k"], "reingreso")
        self.assertNotEqual(V.ligar_devoluciones(dev, woo, AHORA, excluir={0})[0]["5585338270"]["k"], "reingreso")
        self.assertEqual(V.ligar_devoluciones(dev, woo[1:], AHORA)[0]["5585338270"]["k"], "reingreso")

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


def _fila_tec0519(**kw) -> dict:
    """TEC-0519-NAR-GRI, devolución 5585338270 (producción): ML reembolsa al DESPACHAR
    (`refund_at='shipped'`). Abierta el 29-sep 12:14, en camino a las 16:36, ML la cierra
    a las 16:38 —el reembolso al despachar— y el texto dice que llegó el 2-oct."""
    base = {"cuenta": "SANCORFASHION", "id": "5585338270", "estado": "reembolsada", "estado_canal": "delivered",
            "refund_at": "shipped", "texto_ml": "El paquete llegó el viernes 2 de octubre.",
            "destino": "seller_address", "es_fulfillment": False, "venta_contaba": True, "estado_dinero": "refunded",
            "pedido": "2000013", "motivo": "No cumple con las características de la publicación", "piezas": 1,
            "ts": datetime(2026, 9, 29, 18, 14, 32, tzinfo=timezone.utc), "abierta": "2026-09-29 12:14:32",
            "cerrada": "2026-09-29 16:38:09", "reembolsada": "2026-09-29 16:38:09",
            "historia": [{"estado": "abierta", "antes": None, "via": "webhook", "hora": "2026-09-29 12:14:35"},
                         {"estado": "en_transito", "antes": "abierta", "via": "webhook", "hora": "2026-09-29 16:36:40"},
                         {"estado": "reembolsada", "antes": "en_transito", "via": "webhook", "hora": "2026-09-30 21:39:58"}]}
    return {**base, **kw}


class Despacho(unittest.TestCase):
    """`refund_at='shipped'`: el cierre de ML es la SALIDA de la caja, no la entrega.
    Con la v0.627.0 se tomaba como entrega con 72 h de holgura hacia atrás y la liga
    agarraba subidas de Odoo de antes de que la caja saliera."""

    # Lo que anotó stock_watch para TEC-0519-NAR-GRI (producción): el +3 del 29-sep
    # 14:49 son recepciones de OTRAS guías, 1.8 h antes de que la caja saliera; el +1
    # del 8-oct 16:22 es TEXCO/IN/01562, con la guía FEDEX de la 5585383756.
    WOO = [_woo("2026-09-29 14:49:26", 412, 415), _woo("2026-10-03 11:53:24", 415, 417),
           _woo("2026-10-08 16:22:57", 417, 418)]
    SI_NO_AVISAS = ("Si no nos avisas dentro del plazo cómo llegó el producto, asumiremos que lo "
                    "recibiste como esperabas.")

    def _ligar(self, filas, woo, ahora=AHORA):
        devs = V._armar_devoluciones(filas, [])
        V._aplicar_liga(devs, woo, ahora, 10)
        return devs

    def _otra(self, **kw) -> dict:
        """La 5585383756 (misma SKU), tal cual en producción: salió el 3-oct 10:30 y el
        8-oct 13:37 ML la dio por entregada. Su texto todavía no dice qué día llegó, y
        kubera saltó de `en_transito` a `reembolsada`: la entrega solo está en el
        `estado_canal` de esa fila."""
        return _fila_tec0519(**{
            "id": "5585383756", "abierta": "2026-09-29 13:30:34", "cerrada": "2026-10-03 10:32:07",
            "reembolsada": "2026-10-03 10:32:07", "texto_ml": self.SI_NO_AVISAS,
            "historia": [{"estado": "abierta", "antes": None, "via": "webhook", "ec": "opened", "hora": "2026-09-29 13:30:36"},
                         {"estado": "en_transito", "antes": "abierta", "via": "webhook", "ec": "shipped",
                          "hora": "2026-10-03 10:30:44"},
                         {"estado": "reembolsada", "antes": "en_transito", "via": "webhook", "ec": "delivered",
                          "hora": "2026-10-08 13:37:49"}], **kw})

    def test_tec0519_la_subida_de_antes_del_despacho_no_se_liga(self):
        woo = [dict(w) for w in self.WOO]
        dev, dev2 = self._ligar([_fila_tec0519(), self._otra()], woo)
        # «En tránsito» (16:36:40) y el cierre (16:38:09) son el mismo despacho: 89 s.
        # La transición en vivo a `reembolsada` del 30-sep no es otro reembolso.
        self.assertEqual([(p["texto"], p["hora"], p["de"]) for p in dev["pasos"]],
                         [("abierta", "2026-09-29 12:14:32", "apertura"),
                          ("en tránsito · reembolsada al despachar", "2026-09-29 16:36:40", "webhook"),
                          ("kubera la vio reembolsada", "2026-09-30 21:39:58", "webhook"),
                          ("entregada", "2026-10-02", "texto de ML")])
        self.assertEqual((dev["llego"], dev["llegada"], dev["holgura_h"], dev["no_antes"], dev["refund_at"]),
                         (True, "2026-10-02", 0, "2026-09-29 16:36:40", "shipped"))
        # Toma el +2 del 3-oct, que por guía TAMPOCO es suyo (IN/01530 e IN/01531): su
        # guía no aparece en Odoo. Ningún ajuste de ventana lo arregla —falta la liga
        # dura por guía—; por eso el texto no afirma el reingreso y la nota lo avisa.
        self.assertEqual((dev["liga"]["k"], dev["liga"]["hora"]), ("reingreso", "2026-10-03 11:53:24"))
        self.assertEqual(dev["liga"]["texto"],
                         "Llegó a nuestra bodega el 2-oct (según ML); la primera subida de Odoo desde ese día es "
                         "la del 3-oct (+1 d): Odoo 415 → 417; la subida es de +2 y la devolución trae 1.")
        self.assertEqual(dev["liga"]["nota"], V._NOTA_LIGA_DIA)
        self.assertEqual(woo[1]["liga_nota"], V._NOTA_LIGA_DIA)
        self.assertNotIn("devoluciones", woo[0])         # el +3 de otras guías queda libre
        # La 5585383756: la entrega vista en vivo, con hora y sin holgura, y su reingreso
        # verdadero (IN/01562, la guía de su envío), con la nota de siempre.
        self.assertEqual([(p["texto"], p["hora"], p["de"]) for p in dev2["pasos"]],
                         [("abierta", "2026-09-29 13:30:34", "apertura"),
                          ("en tránsito · reembolsada al despachar", "2026-10-03 10:30:44", "webhook"),
                          ("entregada", "2026-10-08 13:37:49", "webhook")])
        self.assertEqual((dev2["llego"], dev2["llegada"], dev2["holgura_h"]), (True, "2026-10-08 13:37:49", 0))
        self.assertEqual((dev2["liga"]["k"], dev2["liga"]["hora"], dev2["liga"]["texto"]),
                         ("reingreso", "2026-10-08 16:22:57", "Reingresó a Odoo el 8-oct (el mismo día): Odoo 417 → 418."))
        self.assertEqual((dev2["liga"]["nota"], woo[2]["liga_nota"]), (V._NOTA_LIGA, V._NOTA_LIGA))
        # Sin la subida del 3-oct, la del 29-sep tampoco: queda en espera.
        dev, = self._ligar([_fila_tec0519()], [dict(self.WOO[0])])
        self.assertEqual((dev["liga"]["k"], dev["liga"]["texto"]),
                         ("espera", "Llegó a nuestra bodega el 2-oct (según ML) y todavía no se ve reingreso en Odoo."))

    def test_sin_entrega_en_vivo_ni_texto_queda_sin_fecha(self):
        # Como TEC-0573-MET 5570485726: entregada, pero la historia es solo el backfill
        # (sin transición con 'delivered') y el texto es el de la revisión. Ni el cierre
        # ni el reembolso son la entrega, y la salida tampoco sirve de respaldo.
        fila = self._otra(historia=[{"estado": "reembolsada", "antes": None, "via": "backfill", "ec": "delivered",
                                     "hora": "2026-10-08 13:37:49"}])
        dev, = self._ligar([fila], [dict(w) for w in self.WOO])
        self.assertEqual([p["texto"] for p in dev["pasos"]], ["abierta", "reembolsada al despachar"])
        self.assertEqual((dev["llego"], dev["llegada"], dev["liga"]["k"]), (True, None, "sin_fecha"))
        self.assertIn("ML no dice qué día (reembolsó al despachar", dev["liga"]["texto"])
        # En camino (la fila en vivo dice 'shipped'): no es la entrega.
        camino = self._otra(estado_canal="shipped", historia=self._otra()["historia"][:2])
        self.assertEqual(V._llegada(camino, V._pasos_devolucion(camino)), (False, None))

    def test_misma_llegada_va_primero_la_que_salio_antes(self):
        # TEC-0573-MET: las dos dicen que llegaron el jueves 10-sep. La 5570322560 salió
        # el 5-sep 09:33 y la 5570201794 a las 12:03; por guía, el +1 del 11-sep es de
        # la 5570322560 (IN/01340). Antes desempataba el id y se lo llevaba la otra.
        def fila(id_, abierta, cierre):
            return _fila_tec0519(id=id_, abierta=abierta, cerrada=cierre, reembolsada=cierre,
                                 texto_ml="El paquete llegó el jueves 10 de septiembre.",
                                 historia=[{"estado": "reembolsada", "antes": None, "via": "backfill", "ec": "shipped",
                                            "hora": "2026-09-09 11:17:39"}])
        woo = [_woo("2026-09-11 10:23:01", 20, 21), _woo("2026-09-15 16:44:09", 21, 22)]
        a, b = self._ligar([fila("5570201794", "2026-09-02 08:59:48", "2026-09-05 12:03:58"),
                            fila("5570322560", "2026-09-02 12:22:18", "2026-09-05 09:33:55")], woo, ahora="2026-10-08 12:00:00")
        self.assertEqual((a["llegada"], b["llegada"]), ("2026-09-10", "2026-09-10"))
        self.assertEqual({d["id"]: d["liga"]["hora"] for d in (a, b)},
                         {"5570322560": "2026-09-11 10:23:01", "5570201794": "2026-09-15 16:44:09"})

    def test_sin_refund_at_es_el_comportamiento_de_antes(self):
        # Sin `refund_at` (capturas viejas, Temu) todo queda como en la v0.627.0: el
        # cierre es la entrega, con 72 h hacia atrás, y el texto de ML no se lee.
        dev, = self._ligar([_fila_tec0519(refund_at=None, historia=[])], [dict(w) for w in self.WOO])
        self.assertEqual([(p["texto"], p["de"]) for p in dev["pasos"]],
                         [("abierta", "apertura"), ("entregada", "cierre de ML"), ("reembolsada", "reembolso")])
        self.assertEqual((dev["llegada"], dev["holgura_h"], dev["no_antes"]),
                         ("2026-09-29 16:38:09", 72, "2026-09-29 12:14:32"))
        self.assertEqual(dev["liga"]["hora"], "2026-09-29 14:49:26")
        # Y sin cierre, la llegada es la del reembolso.
        d = {**_fila_tec0519(refund_at=None, historia=[], cerrada=None)}
        self.assertEqual(V._llegada(d, V._pasos_devolucion(d)), (True, "2026-09-29 16:38:09"))

    def test_refund_delivered_con_cierre_conserva_las_72_h(self):
        # Sin el día en el texto, la llegada es el cierre con sus 72 h hacia atrás.
        fila = _fila_tec0519(refund_at="delivered", texto_ml=self.SI_NO_AVISAS,
                             abierta="2026-09-25 10:00:00", cerrada="2026-10-02 12:00:00",
                             reembolsada="2026-10-02 12:00:00", historia=[])
        woo = [_woo("2026-09-30 09:00:00", 4, 5)]           # 51 h antes del cierre
        dev, = self._ligar([fila], woo)
        self.assertEqual([(p["texto"], p["de"]) for p in dev["pasos"]],
                         [("abierta", "apertura"), ("entregada", "cierre de ML"), ("reembolsada", "reembolso")])
        self.assertEqual((dev["llegada"], dev["holgura_h"]), ("2026-10-02 12:00:00", 72))
        self.assertEqual(dev["liga"]["texto"], "Reingresó a Odoo el 30-sep (−2 d): Odoo 4 → 5.")
        # Pero nunca antes de que la caja salió: vista en camino el 1-oct, ya no vale.
        fila["historia"] = [{"estado": "en_transito", "antes": "abierta", "via": "webhook", "hora": "2026-10-01 08:00:00"}]
        dev, = self._ligar([fila], [_woo("2026-09-30 09:00:00", 4, 5)])
        self.assertEqual((dev["no_antes"], dev["liga"]["k"]), ("2026-10-01 08:00:00", "espera"))

    def test_refund_delivered_el_dia_del_texto_manda_sobre_el_cierre(self):
        # La 5543262662 (producción): ML dice que llegó el jueves 23-jul y
        # cerró el 28-jul 21:38, 5.9 días después: más que las 72 h. Con el día del texto
        # la ventana empieza ese día, sin holgura.
        fila = _fila_tec0519(id="5543262662", refund_at="delivered", texto_ml="El paquete llegó el jueves 23 de julio.",
                             abierta="2026-07-14 08:38:53", cerrada="2026-07-28 21:38:43",
                             reembolsada="2026-07-28 21:38:44",
                             historia=[{"estado": "reembolsada", "antes": None, "via": "backfill", "ec": "delivered",
                                        "hora": "2026-09-09 11:15:39"}])
        dev, = self._ligar([fila], [_woo("2026-07-24 10:00:00", 4, 5)], ahora="2026-08-20 12:00:00")
        self.assertEqual([(p["texto"], p["hora"], p["de"]) for p in dev["pasos"]],
                         [("abierta", "2026-07-14 08:38:53", "apertura"), ("entregada", "2026-07-23", "texto de ML"),
                          ("reembolsada", "2026-07-28 21:38:44", "reembolso")])
        self.assertEqual((dev["llegada"], dev["holgura_h"], dev["liga"]["hora"]), ("2026-07-23", 0, "2026-07-24 10:00:00"))
        # Un día DESPUÉS del cierre no puede ser la entrega: vuelve el cierre con 72 h.
        tarde = {**fila, "texto_ml": "El paquete llegó el jueves 30 de julio."}
        self.assertEqual([(p["texto"], p["de"]) for p in V._pasos_devolucion(tarde)][1], ("entregada", "cierre de ML"))

    def test_llegada_en_vivo_sin_holgura(self):
        # Vista `recibida` en vivo: manda sobre el texto, con hora y sin holgura.
        fila = _fila_tec0519(historia=_fila_tec0519()["historia"] + [
            {"estado": "recibida", "antes": "reembolsada", "via": "webhook", "hora": "2026-10-02 09:00:00"}])
        woo = [_woo("2026-10-02 08:00:00", 4, 5), _woo("2026-10-02 10:00:00", 5, 6)]
        dev, = self._ligar([fila], woo)
        self.assertEqual([p["de"] for p in dev["pasos"] if p["estado"] == "recibida"], ["webhook"])
        self.assertEqual((dev["llegada"], dev["holgura_h"]), ("2026-10-02 09:00:00", 0))
        self.assertEqual(dev["liga"]["texto"], "Reingresó a Odoo el 2-oct (el mismo día): Odoo 5 → 6.")

    def test_llegada_por_texto_vale_el_dia_completo(self):
        # Solo se sabe el día: vale desde sus 00:00 y la ventana cierra al FIN del día + N.
        fila = _fila_tec0519()
        temprano, = self._ligar([fila], [_woo("2026-10-02 07:00:00", 4, 5)])
        self.assertEqual(temprano["liga"]["texto"],
                         "Llegó a nuestra bodega el 2-oct (según ML); la primera subida de Odoo desde ese día es "
                         "la del 2-oct (el mismo día): Odoo 4 → 5.")
        tarde, = self._ligar([fila], [_woo("2026-10-12 23:00:00", 4, 5)], ahora="2026-10-20 12:00:00")
        fuera, = self._ligar([fila], [_woo("2026-10-13 00:30:00", 4, 5)], ahora="2026-10-20 12:00:00")
        self.assertEqual((tarde["liga"]["k"], tarde["liga"]["dias"]), ("reingreso", 10))
        self.assertEqual((fuera["liga"]["k"], fuera["liga"]["texto"]),
                         ("sin_reingreso", "Llegó a nuestra bodega el 2-oct (según ML) y no se ve reingreso en Odoo "
                                           "en los 10 días siguientes."))

    def test_el_dia_del_texto(self):
        f = V._llegada_texto
        self.assertEqual(f("El paquete llegó el viernes 2 de octubre.", "2026-09-29 16:36:40"), "2026-10-02")
        self.assertEqual(f("Llegó el lunes 7 de septiembre", "2026-09-03 11:09:38"), "2026-09-07")
        self.assertEqual(f("Entendimos que recibiste el producto como esperabas. Llegó el miércoles 9 de septiembre.",
                           "2026-09-04 11:55:00"), "2026-09-09")
        self.assertEqual(f("El paquete llegó el sábado 2 de enero.", "2026-12-30 10:00:00"), "2027-01-02")
        self.assertIsNone(f("El paquete llegó el lunes 31 de agosto.", "2026-09-02 10:00:00"))   # antes de salir
        self.assertIsNone(f("Llega entre el 24 y el 27 de septiembre.", "2026-09-22 16:01:30"))  # promesa, no llegada
        self.assertIsNone(f("Si no nos avisas dentro del plazo cómo llegó el producto, asumiremos que lo recibiste "
                            "como esperabas.", "2026-10-03 10:30:44"))
        self.assertIsNone(f("El paquete llegó el viernes 2 de octubre.", None))
        self.assertEqual(f("llegó el 2 de octubre", "2026-09-29 16:36:40"), "2026-10-02")          # sin día de la semana
        # El cruce de año hacia atrás: salió el 4-ene y el texto dice 31-dic (antes de
        # salir). No es el 31-dic de ese año, 12 meses en el futuro.
        self.assertIsNone(f("El paquete llegó el jueves 31 de diciembre.", "2027-01-04 10:00:00"))
        self.assertIsNone(f("El paquete llegó el lunes 2 de octubre.", "2026-09-29 16:36:40"))  # el 2-oct es viernes
        self.assertIsNone(f("El paquete llegó el viernes 2 de octubre.", "2026-07-01 10:00:00"))  # 93 días después
        # `hasta`: con 'delivered', no después del cierre.
        self.assertIsNone(f("El paquete llegó el viernes 2 de octubre.", "2026-09-25 10:00:00", hasta="2026-10-01 09:15:21"))
        self.assertEqual(f("El paquete llegó el viernes 2 de octubre.", "2026-09-25 10:00:00", hasta="2026-10-02 12:00:00"),
                         "2026-10-02")

    def test_en_transito_y_cierre_en_el_mismo_instante_son_un_paso(self):
        # TEC-0471-NAR-ARC200, 5576286635 (producción): en camino 13:54:06, cierre y
        # reembolso 13:54:04; kubera lo vio `reembolsada` al día siguiente (el mismo
        # reembolso, no otro) y ML dice que llegó el 21.
        fila = _fila_tec0519(id="5576286635", abierta="2026-09-13 09:35:33", cerrada="2026-09-17 13:54:04",
                             reembolsada="2026-09-17 13:54:04", texto_ml="El paquete llegó el lunes 21 de septiembre.",
                             historia=[{"estado": "abierta", "antes": None, "via": "webhook", "ec": "pending",
                                        "hora": "2026-09-13 09:35:35"},
                                       {"estado": "en_transito", "antes": "abierta", "via": "webhook", "ec": "shipped",
                                        "hora": "2026-09-17 13:54:06"},
                                       {"estado": "reembolsada", "antes": "en_transito", "via": "webhook", "ec": "shipped",
                                        "hora": "2026-09-18 15:50:12"}])
        self.assertEqual([(p["texto"], p["hora"]) for p in V._pasos_devolucion(fila)],
                         [("abierta", "2026-09-13 09:35:33"),
                          ("en tránsito · reembolsada al despachar", "2026-09-17 13:54:06"),
                          ("kubera la vio reembolsada", "2026-09-18 15:50:12"),
                          ("entregada", "2026-09-21")])
        # Si el dinero no se soltó, el cierre no es un reembolso.
        sin_dinero = {**fila, "reembolsada": None, "estado": "recibida", "historia": fila["historia"][:2]}
        self.assertEqual([p["texto"] for p in V._pasos_devolucion(sin_dinero)],
                         ["abierta", "en tránsito · cerrada al despachar", "entregada"])
        # Cruzar el minuto no los separa: FULL 5589172343, en camino 11:48:00 y cierre
        # 11:47:58 (2 s).
        cruza = _fila_tec0519(estado_canal="shipped", abierta="2026-10-06 10:32:07", cerrada="2026-10-06 11:47:58",
                              reembolsada="2026-10-06 11:47:58",
                              historia=[{"estado": "en_transito", "antes": "abierta", "via": "webhook", "ec": "shipped",
                                         "hora": "2026-10-06 11:48:00"}])
        self.assertEqual([p["texto"] for p in V._pasos_devolucion(cruza)],
                         ["abierta", "en tránsito · reembolsada al despachar"])
        # En camino (todavía `shipped`): el despacho se ve, la entrega no.
        camino = _fila_tec0519(estado_canal="shipped", historia=[])
        self.assertEqual([p["texto"] for p in V._pasos_devolucion(camino)], ["abierta", "reembolsada al despachar"])
        self.assertEqual(V._llegada(camino, V._pasos_devolucion(camino)), (False, None))

    def test_envio_cancelado_o_vencido_no_toma_subida(self):
        # Nunca viajaron (envío cancelado, sin guía): el cierre es la cancelación, no un
        # despacho, y el «llegó el…» de su texto no es de esta caja.
        cancelada = _fila_tec0519(id="5561218658", estado="rechazada", estado_canal="cancelled", reembolsada=None,
                                  abierta="2026-08-16 16:32:07", cerrada="2026-08-21 16:52:50",
                                  texto_ml="El paquete llegó el lunes 31 de agosto.", historia=[])
        vencida = _fila_tec0519(id="5567979757", estado="abierta", estado_canal="expired", reembolsada=None,
                                abierta="2026-08-28 20:06:08", cerrada="2026-09-08 21:56:40",
                                texto_ml="Como el comprador no te envió el producto, cancelamos la devolución y te dimos el dinero.",
                                historia=[])
        woo = [_woo("2026-08-31 10:00:00", 4, 5), _woo("2026-09-09 10:00:00", 5, 6)]
        devs = self._ligar([cancelada, vencida], woo)
        self.assertEqual([(d["llego"], d["liga"]["k"]) for d in devs], [(False, "no_regresa"), (False, "no_regresa")])
        self.assertEqual([p["texto"] for p in devs[0]["pasos"]], ["abierta", "rechazada (cancelada)"])
        self.assertNotIn("devoluciones", woo[0])
        self.assertNotIn("devoluciones", woo[1])

    def test_full_que_reembolsa_al_revisar_no_reembolsa_al_despachar(self):
        # FULL 5572526388 (producción): `refund_at='shipped'`, pero ML cerró el 8-sep
        # 13:09 (el despacho) y reembolsó el 11-sep 07:54, 66.8 h después, al revisar la
        # caja en su almacén («Validamos que es el producto correcto…»). El cierre es
        # «cerrada al despachar» y el reembolso, su propio paso.
        fila = _fila_tec0519(id="5572526388", destino="warehouse", es_fulfillment=True,
                             abierta="2026-09-06 17:07:20", cerrada="2026-09-08 13:09:14",
                             reembolsada="2026-09-11 07:54:54",
                             texto_ml="Validamos que es el producto correcto y está en buenas condiciones.",
                             historia=[{"estado": "reembolsada", "antes": None, "via": "backfill", "ec": "shipped",
                                        "hora": "2026-09-09 11:17:40"}])
        dev, = self._ligar([fila], [])
        self.assertEqual([(p["estado"], p["texto"], p["hora"], p["de"]) for p in dev["pasos"]],
                         [("abierta", "abierta", "2026-09-06 17:07:20", "apertura"),
                          ("cerrada", "cerrada al despachar", "2026-09-08 13:09:14", "cierre de ML"),
                          ("reembolsada", "reembolsada", "2026-09-11 07:54:54", "reembolso")])
        self.assertEqual((dev["llego"], dev["llegada"], dev["liga"]["k"]), (True, None, "full"))


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
                    "as abierta", "as cerrada", "as reembolsada", "as historia", "'antes', h.estado_anterior",
                    "r.refund_at", "r.texto_ml", "'ec', h.estado_canal"):
            self.assertIn(col, sql)
        # Cuándo reembolsa ML y lo que le dice al vendedor: del crudo.
        self.assertIn("r.payload->'returns'->>'refund_at' as refund_at", sql)
        self.assertIn("r.payload->'detalle'->>'description' as texto_ml", sql)

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
