"""Pruebas de los LECTORES del fan-out con el libro de kubera (ruta directa de la salida
de Odoo y de Woo, 20-oct-2026): `fanout_vivo`, `fanout_devoluciones`, `routers/fanout`
y los rótulos que lee el panel.

── QUÉ FIJAN ────────────────────────────────────────────
  1. LA LISTA DE CAMBIOS ES UNA: todo lector de los cambios de stock_watch filtra con
     `stock_watch.ACCIONES_CAMBIO` como PARÁMETRO (`accion = any(%(acc)s)`), nunca como
     literal, y `vendible_registro` (el libro en sombra) no es un cambio. (`ListaUnica`)
  2. EL ORIGEN «libro»: el motivo «cambio de vendible en kubera» con la flecha del
     resultado del `vendible_cambio`. (`Origen`)
  3. LA HISTORIA de un SKU: `vendible_cambio` es origen libro; el que lleva «(libro: ya
     no lo trae)» es el «→ 0» de D4 (`sin_numero`, `a` 0, se reparte); una fila VIEJA
     `vendible_sin_numero` es el mismo ítem con `a` None, y `vendible_registro` no entra.
     (`Historia`)
  4. LA ÚLTIMA PASADA: fuente, fuente del fan-out, cambios del vendible (la sombra no
     cuenta), cuántos salieron del libro y si abortó; `fuentes`/`lectura` sólo si
     existen. (`PasadaStockWatch`)
  5. PERDIDOS Y CAUSAS con `vendible_cambio`: «El vendible cambió…». (`PerdidosYCausas`)
  6. RÓTULOS: Odoo FIJO «congelado al 15-oct», `stock_rotulo` y «El libro tiene N».
     (`Rotulos`)
  7. SIN FALSO VERDE: sin PRUEBA de que la pasada de Odoo terminó (hoy aborta desde el
     15-oct) o con el libro, Odoo↔Woo es un dato rotulado («Comparación contra Odoo
     congelado al 15-oct»), ni alarma ni verde. (`SinFalsoVerde`)
  8. LA FOTO QUE NADIE MUEVE se rotula «foto del 15-oct», la lectura del fan-out le gana
     y con el libro se compara contra `stock_kubera`. (`FotoVendible`)
  9. DEVOLUCIONES: `_woo_de` toma la lista y la flecha del RESULTADO; sólo Odoo sube; el
     «→ 0» del libro lleva `sin_numero`. (`Devoluciones`)
 10. EL ROUTER: `odoo/monitor` retirado; `inventario/pendientes` con la misma lista en
     sus dos ramas; `inventario/estado` con los rótulos del contrato. (`Router`)
 11. D4 EN LOS LECTORES: con el libro en marcha, lo que la última pasada del libro no
     tocó vale 0 («el libro no lo trae → 0») en la coincidencia y en la matriz, y se
     cuenta aparte; sin pasada, o un NULL que la pasada selló (sombra), no. (`D4`)
 12. LA FOTO QUIETA: la lectura del fan-out se busca desde que la foto se quedó quieta
     (15-oct 18:34 UTC), no sólo en 3 días. (`FotoQuieta`)
 13. LO QUE NO SE REPARTIÓ NO CUENTA: en sombra ni los que salieron del libro; una
     pasada que abortó, nada; y su «última pasada con cambios» no va en lo que va bien.
     Las vendidas sin orden no «explican» nada contra Odoo congelado. (`NoRepartido`)

Sin red ni base: todo va suplantado (las trampas de `test_fanout_seguro`). Los SKUs son
INVENTADOS: el repo es público.

    cd backend && python -m unittest tests.test_fanout_lectores_libro -v
"""
from __future__ import annotations

import inspect
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from config import settings  # noqa: E402
from services import fanout_devoluciones as FD  # noqa: E402
from services import fanout_read  # noqa: E402
from services import fanout_vivo as V  # noqa: E402
from services import stock_watch as SW  # noqa: E402
from tests import test_fanout_seguro as _TS  # noqa: E402

setUpModule = _TS.setUpModule
tearDownModule = _TS.tearDownModule

SKU = "ZZZ-0700-AZL"
# Las combinaciones que importan: el fan-out en el libro con stock_watch en el libro (T0),
# el libro en sombra (stock_watch en el libro, el fan-out todavía en Woo) y la de HOY en
# producción (FUENTE=odoo, que aborta desde el 15-oct, con el fan-out en Woo). El modo
# puente `woo` de stock_watch ya no existe (ruta directa): se niega a correr.
LIBRO = dict(fanout_fuente="kubera", stock_watch_fuente="libro")
SOMBRA = dict(fanout_fuente="woo", stock_watch_fuente="libro")
HOY = dict(fanout_fuente="woo", stock_watch_fuente="odoo")
CONGELADO = "Odoo (congelado al 15-oct)"
CONTRA = "Comparación contra Odoo congelado al 15-oct"


def con(**ajustes):
    return mock.patch.multiple(settings, **ajustes)


def odoo_termino(estado: str = "ok"):
    """La memoria de stock_watch con una pasada de Odoo que terminó en `estado` (la única
    prueba de que la foto de Odoo/Woo se movió; desde el 15-oct, en producción, aborta)."""
    return mock.patch.dict(SW._ultimo, {"ts": 1.0, "estado": estado})


# ── 1 · una sola lista ───────────────────────────────────────────────────────

class ListaUnica(unittest.TestCase):
    def test_la_lista_es_la_de_stock_watch_y_la_sombra_no_es_cambio(self):
        self.assertEqual(V._acc_cambio(), list(SW.ACCIONES_CAMBIO))
        self.assertIn("vendible_cambio", V._acc_cambio())
        for a in ("vendible_registro", "vendible_sin_numero", "woo_sin_numero", "woo_cambio_registro"):
            self.assertNotIn(a, V._acc_cambio())

    def test_ningun_lector_escribe_la_lista_en_el_sql(self):
        for nombre, fuente in (("fanout_vivo", inspect.getsource(V)), ("_SQL_SUBIDAS", FD._SQL_SUBIDAS),
                               ("_woo_de", inspect.getsource(FD._woo_de))):
            self.assertNotIn("('odoo_delta', 'woo_cambio')", fuente, nombre)
            self.assertNotIn("accion = 'odoo_delta'", fuente, nombre)
        self.assertIn("accion = any(%(acc)s)", FD._SQL_SUBIDAS)


# ── 2 · el origen «libro» ────────────────────────────────────────────────────

class Origen(unittest.TestCase):
    def test_cambio_de_vendible_en_kubera(self):
        fila = {"resultado": "7 -> 5 (replicar a canales)"}
        self.assertEqual(V._origen("cambio de vendible en kubera", fila), ("libro", "Vendible 7 → 5", 7, 5))
        self.assertEqual(V._origen("cambio de vendible en kubera", None),
                         ("libro", "cambio del vendible", None, None))
        # D4: el SKU que sale del libro llega a la cola igual, con su «→ 0».
        self.assertEqual(V._origen("cambio de vendible en kubera", {"resultado": "7 -> 0 (replicar a canales)"}),
                         ("libro", "Vendible 7 → 0", 7, 0))

    def test_los_de_antes_no_cambian(self):
        self.assertEqual(V._origen("cambio de stock en Woo", {"resultado": "4 -> 3 (replicar a canales)"}),
                         ("woo", "Woo 4 → 3", 4, 3))
        self.assertEqual(V._origen("delta de Odoo aplicado", {"resultado": "Woo 4 -> 6"})[:2],
                         ("odoo", "Odoo 4 → 6"))
        self.assertEqual(V._origen("venta BEKURA 2000001", None)[:2], ("venta", "venta en ML Kubera"))

    def test_el_evento_del_libro_busca_su_renglon_con_la_lista(self):
        ts = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
        llamadas = []

        def fetch_all(sql, params=None):
            llamadas.append((sql, params))
            return [{"sku": SKU, "ts": ts, "woo_ts": ts, "accion": "vendible_cambio",
                     "resultado": "7 -> 5 (replicar a canales)", "woo_hora": "12:00:00", "total_s": 4.0}]
        with mock.patch.object(V.sdb, "fetch_all", side_effect=fetch_all):
            r = V._woo_de([{"sku": SKU, "ts": ts, "motivo": "cambio de vendible en kubera"},
                           {"sku": "ZZZ-0701-ROJ", "ts": ts, "motivo": "venta BEKURA 2000001"}])
        (sql, params), = llamadas
        self.assertIn("w.accion = any(%(acc)s)", sql)
        self.assertEqual((params["acc"], params["s"]), (list(SW.ACCIONES_CAMBIO), [SKU]))
        self.assertEqual(V._origen("cambio de vendible en kubera", r[(SKU, ts)])[:2], ("libro", "Vendible 7 → 5"))


# ── 3 · la historia de un SKU ────────────────────────────────────────────────

def _fila(hora: str, accion: str, motivo: str, resultado: str, id_: int) -> dict:
    """Un renglón de stock_watch en `ops.fanout_log` (hora de CDMX = UTC−6)."""
    ts = datetime.strptime(hora, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) + timedelta(hours=6)
    return {"id": id_, "ts": ts, "hora": hora, "motivo": motivo, "canal": "woocommerce", "cuenta": "ODOO",
            "accion": accion, "resultado": resultado, "stock_canal": None, "objetivo": None,
            "stock_drop": None, "reciente": True, "item_id": "", "dry_run": False}


class Historia(unittest.TestCase):
    # Lo que anota `stock_watch._pasada_libro`, más reciente primero (como la consulta).
    LOG = [_fila("2026-10-21 12:50:00", "vendible_cambio", "vendible 5 -> 0 (libro: no lo trae)",
                 "5 -> 0 (replicar a canales)", 6),
           # Una fila VIEJA de la versión D3 (ya no se anota; puede quedar en la bitácora).
           _fila("2026-10-21 12:40:00", "vendible_sin_numero", "el libro ya no trae el SKU",
                 "5 -> sin número (nada que repartir: los canales se quedan con lo que tenían)", 4),
           _fila("2026-10-21 12:30:00", "vendible_registro", "vendible 9 -> 0 (libro: no lo trae)",
                 "9 -> 0 (replicar a canales)", 5),
           _fila("2026-10-21 12:20:00", "vendible_registro", "vendible 9 -> 8 (libro)",
                 "9 -> 8 (replicar a canales)", 3),
           _fila("2026-10-21 12:00:00", "vendible_cambio", "vendible 7 -> 5 (libro)",
                 "7 -> 5 (replicar a canales)", 2),
           _fila("2026-10-14 10:00:00", "woo_cambio", "cambio de stock en Woo", "8 -> 7 (replicar a canales)", 1)]

    def correr(self, ajustes=HOY):
        def fetch_all(sql, params=None):
            return list(self.LOG) if "from ops.fanout_log" in sql else []
        with con(**ajustes),                 mock.patch.object(V, "reparto", return_value=["tiktok", "temu", "mercado_libre"]),                 mock.patch.object(V, "_ahora_local", return_value={"ahora": "2026-10-21 13:00:00"}),                 mock.patch.object(V, "_marcas_seguro", return_value={}),                 mock.patch.object(V, "_sin_orden", return_value={}),                 mock.patch.object(V, "_causas", return_value={}),                 mock.patch.object(V, "_devoluciones", return_value=([], [])),                 mock.patch.object(V.sdb, "fetch_one", return_value={}),                 mock.patch.object(V.sdb, "fetch_all", side_effect=fetch_all):
            return V.historia(SKU, 14)

    def test_el_vendible_es_origen_libro_y_la_salida_del_libro_va_a_cero(self):
        h = self.correr()
        woo = [x for x in h["items"] if x["tipo"] == "woo"]
        self.assertEqual([(x["origen"], x["de"], x["a"], bool(x.get("sin_numero")), x.get("texto")) for x in woo],
                         [("libro", 5, 0, True, "El libro no trae el SKU → 0"),
                          ("libro", 5, None, True, "El libro ya no trae el SKU → sin número"),
                          ("libro", 7, 5, False, None), ("woo", 8, 7, False, None)])
        self.assertEqual(woo[0]["motivo"], "vendible 5 -> 0 (libro: no lo trae)")
        # El «→ 0» de D4 sí es un cambio (se reparte); la fila vieja sin número, no.
        self.assertEqual((h["resumen"]["cambios_woo"], h["resumen"]["sin_numero"]), (3, 2))

    def test_la_sombra_no_es_un_cambio(self):
        h = self.correr()
        self.assertFalse(any(x.get("de") == 9 or "9 -> " in str(x.get("motivo")) for x in h["items"]))

    def test_una_subida_del_libro_no_se_liga_a_una_devolucion(self):
        # Ni con la foto de Odoo puesta: su motivo no dice por qué subió el vendible.
        woo = [{"origen": "libro", "hora": "2026-10-21 12:00:00", "de": 5, "a": 9, "odoo_de": 5, "odoo_a": 9}]
        self.assertEqual(V._subidas_odoo(woo), [])

    def test_el_reparto_sin_numero_de_woo(self):
        # La rama None de `fanout_stock.plan` (sólo con Woo): un `sin_destinos` con «sin
        # stock legible». Su ítem de reparto lleva `sin_numero`; uno sin publicaciones, no.
        sin = dict(_fila("2026-10-21 12:10:00", "sin_destinos", "cambio de stock en Woo",
                         "sin stock legible en WooCommerce", 7), canal="", cuenta="")
        nada = dict(_fila("2026-10-21 12:15:00", "sin_destinos", "cambio de stock en Woo",
                          "sin publicaciones vivas", 8), canal="", cuenta="")
        viejo = self.LOG
        self.LOG = [nada, sin]
        try:
            h = self.correr()
        finally:
            self.LOG = viejo
        rep = sorted((x["hora"], x["sin_numero"], x["sin_destinos"]) for x in h["items"] if x["tipo"] == "reparto")
        self.assertEqual(rep, [("2026-10-21 12:10:00", True, True), ("2026-10-21 12:15:00", False, True)])

    def test_los_rotulos(self):
        self.assertEqual((self.correr()["odoo_rotulo"], self.correr()["stock_rotulo"]), (CONGELADO, "Woo"))
        self.assertEqual(self.correr(LIBRO)["stock_rotulo"], "Libro de kubera")


# ── 4 · la última pasada de stock_watch ──────────────────────────────────────

class PasadaStockWatch(unittest.TestCase):
    # Lo que deja `stock_watch._pasada_libro` en memoria: 3 cambios, 1 de ellos un SKU que
    # salió del libro (a 0).
    EST = {"ts": 1.0, "estado": "ok", "fuente": "libro", "habilitado": True,
           "modo": "libro (reparte los cambios del vendible de kubera; ni Woo ni Odoo)",
           "odoo_deltas": 0, "woo_cambios": 0, "vendible_cambios": 3, "vendible_sin_numero": 1,
           "solo_registra": False}

    def correr(self, est: dict, fila: dict | None = None, ajustes=LIBRO) -> dict:
        self.llamadas = []

        def fetch_one(sql, params=None):
            self.llamadas.append((sql, params))
            return dict(fila or {})
        with con(**ajustes), mock.patch.object(SW, "estado", return_value=est),                 mock.patch.object(V.sdb, "fetch_one", side_effect=fetch_one):
            return V._stock_watch("2026-10-21")

    def test_en_memoria_cuenta_el_vendible(self):
        r = self.correr(self.EST)
        self.assertEqual((r["fuente"], r["fuente_fanout"], r["cambios"], r["vendible_cambios"],
                          r["vendible_sin_numero"], r["estado"], r["aborta"]),
                         ("libro", "kubera", 3, 3, 1, "ok", False))
        # `cambios` = odoo_deltas + woo_cambios + vendible_cambios (contrato).
        self.assertEqual(self.correr({**self.EST, "odoo_deltas": 2, "woo_cambios": 1})["cambios"], 6)

    def test_la_sombra_no_es_cambio(self):
        r = self.correr({**self.EST, "solo_registra": True}, ajustes=SOMBRA)
        self.assertEqual((r["cambios"], r["vendible_cambios"], r["vendible_registro"], r["fuente_fanout"]),
                         (0, 0, 3, "woo"))

    def test_se_ve_si_aborta(self):
        r = self.correr({**self.EST, "estado": "libro_no_disponible", "nota": "sin vista. Pasada abortada",
                         "seguidas_sin_repartir": 2})
        self.assertEqual((r["aborta"], r["nota"], r["seguidas_sin_repartir"]),
                         (True, "sin vista. Pasada abortada", 2))
        # Lo de hoy en producción: FUENTE=odoo con la API de Odoo cortada.
        self.assertTrue(self.correr({**self.EST, "estado": "odoo_sin_respuesta", "fuente": "odoo"}, ajustes=HOY)
                        ["aborta"])
        self.assertFalse(self.correr({**self.EST, "estado": "foto_base"})["aborta"])

    def test_la_fuente_del_fanout_con_errata(self):
        r = self.correr(self.EST, ajustes=dict(fanout_fuente="kubra", stock_watch_fuente="libro"))
        self.assertIsNone(r["fuente_fanout"])
        self.assertIn("kubra", r["fuente_fanout_error"])

    def test_sin_memoria_se_deduce_de_la_bitacora_con_la_lista(self):
        r = self.correr({"estado": "sin_correr", "fuente": "libro"},
                        {"ultimo": "2026-10-21 12:00:00", "cambios": 4, "vendible_cambios": 2,
                         "vendible_sin_numero": 1})
        self.assertEqual((r["en_memoria"], r["cambios"], r["vendible_cambios"], r["vendible_sin_numero"],
                          r["estado"], r["aborta"], r["ultima"]), (False, 4, 2, 1, None, False, "hoy 12:00"))
        (sql, params), = self.llamadas
        self.assertEqual(params["acc"], list(SW.ACCIONES_CAMBIO))
        # Los que salieron del libro se cuentan por su MOTIVO (D4), no por una acción aparte
        # (`vendible_sin_numero` ya no se anota) ni por la sombra.
        self.assertEqual((params["vend"], params["sale"]), ("vendible_cambio", "(libro: no lo trae)"))
        self.assertIn("strpos(motivo, %(sale)s) > 0", sql)
        self.assertNotIn("vendible_sin_numero'", sql)
        self.assertNotIn("vendible_registro", sql)
        self.assertNotIn("accion in (", sql)

    def test_fuentes_y_lectura_viajan_en_el_dashboard(self):
        from services import fanout_fuentes, fanout_stock
        t0 = dict(LIBRO, stock_watch_solo_registro=False, stock_watch_enabled=True)
        with mock.patch.dict(fanout_fuentes._estado, clear=True):   # sin pasar por el scheduler
            r = self.correr(self.EST, ajustes=t0)
            # `STOCK_WATCH_SOLO_REGISTRO` nace encendida: un T0 que la olvida no es T0.
            sin_encolar = self.correr(self.EST, ajustes=dict(t0, stock_watch_solo_registro=True))
            mal = self.correr(self.EST, ajustes=dict(fanout_fuente="kubera", stock_watch_fuente="odoo"))
        self.assertEqual((r["fuentes"]["ok"], r["fuentes"]["stock_watch_fuente"], r["fuentes"]["fanout_fuente"]),
                         (True, "libro", "kubera"))
        self.assertFalse(mal["fuentes"]["ok"])
        self.assertEqual((sin_encolar["fuentes"]["ok"], sin_encolar["fuentes"]["momento"]),
                         (False, "t0_sin_encolar"))
        self.assertEqual(r["lectura"], fanout_stock.lectura_estado())
        self.assertEqual(set(r["lectura"]), {"racha", "avisado", "ultima", "en_reintento"})

    def test_si_fallan_no_tumban_el_dashboard(self):
        from services import fanout_fuentes, fanout_stock
        with mock.patch.object(fanout_fuentes, "estado", side_effect=RuntimeError("sin fuentes (prueba)")),                 mock.patch.object(fanout_stock, "lectura_estado",
                                  side_effect=RuntimeError("sin lectura (prueba)")),                 self.assertLogs("omnicanal.fanout_vivo", level="WARNING"):
            r = self.correr(self.EST)
        self.assertEqual(r["fuentes"], {"error": "RuntimeError: sin fuentes (prueba)"})
        self.assertEqual(r["lectura"], {"error": "RuntimeError: sin lectura (prueba)"})
        self.assertEqual(r["cambios"], 3)      # lo demás del Dashboard sigue


# ── 5 · perdidos y causas ────────────────────────────────────────────────────

class PerdidosYCausas(unittest.TestCase):
    AHORA = datetime(2026, 10, 21, 19, 0, tzinfo=timezone.utc)      # 13:00 CDMX

    def test_los_perdidos_con_la_lista(self):
        llamadas = []

        def fetch_all(sql, params=None):
            llamadas.append((sql, params))
            return [{"sku": SKU}]
        with mock.patch.object(V, "_cola_viva", return_value=({}, None, True)), \
                mock.patch.object(V.sdb, "fetch_all", side_effect=fetch_all):
            self.assertEqual(V._skus_perdidos(), [SKU])
        (sql, params), = llamadas
        self.assertIn("accion = any(%(acc)s)", sql)
        self.assertEqual(params["acc"], list(SW.ACCIONES_CAMBIO))
        self.assertNotIn("vendible_registro", params["acc"])

    def causa(self, accion: str, ajustes=LIBRO, reparto_despues: bool = False,
              motivo: str = "vendible 7 -> 5 (libro)", resultado: str = "7 -> 5 (replicar a canales)") -> dict:
        cambio = self.AHORA - timedelta(minutes=40)
        hora = (cambio - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")

        def fetch_all(sql, params=None):
            p = params or {}
            if p.get("acc") == list(SW.ACCIONES_CAMBIO):
                return [{"sku": SKU, "ts": cambio, "accion": accion, "resultado": resultado,
                         "motivo": motivo, "hora": hora}]
            if not reparto_despues:
                return []
            if "unnest" in sql:        # lo que contestó el canal en ese reparto
                return [{"sku": SKU, "canal": "tiktok", "cuenta": "KUBERA", "accion": "sin_cambio",
                         "resultado": "igual", "stock_canal": 5, "objetivo": 5}]
            return [{"sku": SKU, "ts": cambio + timedelta(seconds=30), "hora": hora}]

        def fetch_one(sql, params=None):
            return {"n": self.AHORA} if "now()" in sql else {}
        celdas = [{"sku": SKU, "canal": "tiktok", "cuenta": "KUBERA",
                   "listing": {"canal": "tiktok", "status": "ACTIVATE"}}]
        with con(**ajustes), mock.patch.object(V, "_cola_viva", return_value=({}, None, True)), \
                mock.patch.object(V.sdb, "fetch_all", side_effect=fetch_all), \
                mock.patch.object(V.sdb, "fetch_one", side_effect=fetch_one):
            return V._causas(celdas, "2026-10-21")[(SKU, "tiktok", "KUBERA")]

    def test_el_vendible_cambio_y_el_fanout_nunca_lo_proceso(self):
        c = self.causa("vendible_cambio")
        self.assertEqual(c["c"], "perdido")
        self.assertTrue(c["d"].startswith("El vendible cambió de 7 a 5 hoy a las 12:20"), c["d"])

    def test_el_libro_dejo_de_traerlo(self):
        c = self.causa("vendible_cambio", motivo="vendible 7 -> 0 (libro: no lo trae)",
                       resultado="7 -> 0 (replicar a canales)")
        self.assertTrue(c["d"].startswith("El vendible cambió de 7 a 0 (el libro no trae el SKU) hoy"), c["d"])

    def test_woo_se_sigue_llamando_woo(self):
        self.assertTrue(self.causa("woo_cambio", HOY, motivo="cambio de stock en Woo")["d"]
                        .startswith("Woo cambió de 7 a 5"))

    def test_quedo_igual_que_el_libro(self):
        c = self.causa("vendible_cambio", reparto_despues=True)
        self.assertEqual(c["c"], "canal")
        self.assertIn("igual que el libro; después el canal cambió", c["d"])
        self.assertIn("igual que Woo;", self.causa("woo_cambio", HOY, reparto_despues=True)["d"])


# ── 6 · rótulos ──────────────────────────────────────────────────────────────

class Rotulos(unittest.TestCase):
    def test_odoo_fijo_y_stock_segun_el_fanout(self):
        for ajustes, stock in ((HOY, "Woo"), (SOMBRA, "Woo"), (LIBRO, "Libro de kubera"),
                               # una errata no lee Woo: el fan-out se niega (`stock_libro.lee_libro`)
                               (dict(fanout_fuente="kubra", stock_watch_fuente="libro"), "Libro de kubera")):
            with con(**ajustes):
                self.assertEqual(V._rotulos(), {"odoo_rotulo": CONGELADO, "stock_rotulo": stock}, ajustes)

    def test_celda_apagada_que_ya_tiene_stock(self):
        L = {"canal": "temu", "status": "3/2", "stock_own": 0, "act": "2026-10-21 10:00:00"}
        marca = {"desde": "2026-10-21 09:00:00"}
        with con(**LIBRO):
            self.assertEqual(V._celda_matriz(L, None, 50, "2026-10-21", marca)["s"],
                             "apagada por el seguro · Libro de kubera ya tiene 50")
        with con(**HOY):
            self.assertEqual(V._celda_matriz(L, None, 50, "2026-10-21", marca)["s"],
                             "apagada por el seguro · Woo ya tiene 50")

    def test_aviso_de_apagadas_que_ya_tienen_stock(self):
        una = {"canal": "temu", "sku": SKU, "woo": 1500, "desde": "x"}
        with con(**LIBRO):
            (a,) = V._atender_seguro({"con_stock": [una], "soltadas": [], "reactivar": False})
        self.assertIn(f"Temu · {SKU}: Libro de kubera tiene 1,500.", a["texto"])
        with con(**HOY):
            (a,) = V._atender_seguro({"con_stock": [una], "soltadas": [], "reactivar": False})
        self.assertIn(f"Temu · {SKU}: Woo tiene 1,500.", a["texto"])


# ── 7 · sin falso verde ──────────────────────────────────────────────────────

class SinFalsoVerde(unittest.TestCase):
    """El Dashboard con la foto del 15-oct: 3 SKUs con piezas en Woo y sin Odoo, y 7 de
    5,498 con Odoo distinto de Woo."""

    CONTEOS = {"skus_odoo": 5501, "skus_woo": 13262, "ambos": 5498, "distintos": 7,
               "sin_odoo_con_piezas": 3, "piezas_sin_odoo": 40, "ultima": "2026-10-15 12:34:00"}

    def resumen(self, ajustes: dict, de_mas: list[dict] | None = None) -> dict:
        self.sqls: list[str] = []

        def fetch_all(sql, params=None):
            self.sqls.append(sql)
            if "stock_odoo <> stock_woo" in sql:
                return [{"sku": "ZZZ-0702-ROJ", "stock_odoo": 10, "stock_woo": 4}]
            return []

        def fetch_one(sql, params=None):
            return dict(self.CONTEOS) if "skus_odoo" in sql else {}
        with con(**ajustes), mock.patch.object(V.sdb, "fetch_all", side_effect=fetch_all), \
                mock.patch.object(V.sdb, "fetch_one", side_effect=fetch_one), \
                mock.patch.multiple(V, reparto=lambda: ["temu"],
                                    _ahora_local=lambda: {"ahora": "2026-10-21 13:00:00"},
                                    _coincidencia=lambda rep: ([], list(de_mas or [])),
                                    _estado_canales=lambda rep, hoy: [],
                                    _stock_watch=lambda hoy: {"ultima": "—", "cambios": 0},
                                    _full_sin_regla=lambda: [], _seguro_cero=lambda: None,
                                    _serie=lambda rep: [], _pulso_24h=lambda rep: []):
            return V._resumen()

    def test_con_el_libro_odoo_es_un_dato_aun_con_fuente_odoo(self):
        r = self.resumen(dict(fanout_fuente="kubera", stock_watch_fuente="odoo"))
        self.assertFalse(any("Odoo" in a["titulo"] for a in r["atender"]))
        self.assertFalse(any("coinciden" in b for b in r["bien"]))
        self.assertEqual([c["titulo"] for c in r["contra_odoo"]],
                         ["3 SKUs con piezas en Woo y sin producto en Odoo",
                          "Odoo y Woo coinciden en 5,491 de 5,498 productos"])
        self.assertTrue(all(c["rotulo"] == CONTRA for c in r["contra_odoo"]))
        f = r["foto"]
        self.assertEqual((f["sin_odoo_con_piezas"], f["distintos"], f["contra_odoo"]["sin_odoo_con_piezas"],
                          f["contra_odoo"]["piezas_sin_odoo"]), (0, 0, 3, 40))
        self.assertFalse(any("limit 3000" in s for s in self.sqls))      # ni busca los distintos
        self.assertEqual((r["odoo_rotulo"], r["stock_rotulo"], f["odoo_rotulo"]),
                         (CONGELADO, "Libro de kubera", CONGELADO))

    def test_hoy_en_produccion_tambien(self):
        # FUENTE=odoo con la pasada abortando desde el 15-oct (o sin memoria: el sandbox):
        # la columna Odoo de la foto no se mueve y no hay con qué dar alarma ni verde.
        for memoria in (None, "odoo_sin_respuesta", "odoo_lectura_parcial"):
            with (odoo_termino(memoria) if memoria else mock.patch.dict(SW._ultimo, {})):
                r = self.resumen(HOY)
            self.assertEqual(len(r["contra_odoo"]), 2, memoria)
            self.assertFalse(any("Odoo" in a["titulo"] for a in r["atender"]), memoria)
            self.assertFalse(any("coinciden" in b for b in r["bien"]), memoria)
        r = self.resumen(SOMBRA)
        self.assertEqual(len(r["contra_odoo"]), 2)

    def test_con_prueba_de_que_odoo_respondio_queda_como_antes_pero_el_rotulo_es_fijo(self):
        with odoo_termino("ok"):
            r = self.resumen(HOY)
        self.assertEqual(r["contra_odoo"], [])
        self.assertIn("3 SKUs con piezas en Woo y sin producto en Odoo", [a["titulo"] for a in r["atender"]])
        self.assertTrue(any(b.startswith("Odoo y Woo coinciden") for b in r["bien"]))
        self.assertEqual((r["odoo_rotulo"], r["foto"]["odoo_rotulo"], r["stock_rotulo"]), (CONGELADO, CONGELADO, "Woo"))
        # ...y con el fan-out en el libro, ni así.
        with odoo_termino("ok"):
            self.assertEqual(len(self.resumen(dict(HOY, fanout_fuente="kubera"))["contra_odoo"]), 2)

    def test_lo_que_se_ofrece_de_mas_contra_el_libro_y_la_foto_congelada(self):
        d = {"canal": "temu", "sku": SKU, "cuenta": None, "valor": 4, "stock_woo": 1, "foto_congelada": True}
        r = self.resumen(LIBRO, [d])
        self.assertEqual(r["atender"][0]["titulo"], f"Temu ofrece 4 de {SKU} con el libro en 1 (foto del 15-oct)")
        r = self.resumen(HOY, [dict(d, foto_congelada=False)])
        self.assertEqual(r["atender"][0]["titulo"], f"Temu ofrece 4 de {SKU} con Woo en 1")


# ── 8 · la foto que nadie mueve ──────────────────────────────────────────────

class FotoVendible(unittest.TestCase):
    FT = {"stock_woo": 4, "stock_kubera": 9, "actualizado": datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc),
          "foto_hora": "2026-10-21 12:00:00"}
    # Más viejo que el `actualizado` de la foto.
    LEIDO = {"ts": datetime(2026, 10, 21, 17, 0, tzinfo=timezone.utc), "valor": 6, "hora": "2026-10-21 11:00:00"}

    def vig(self, ajustes: dict, leido: dict | None = None):
        with con(**ajustes):
            return V._woo_vigente(self.FT, leido, "2026-10-21")

    def test_hoy_la_foto_de_woo_es_del_15_oct(self):
        # FUENTE=odoo aborta desde el 15-oct: `stock_woo` no se mueve y su `actualizado`
        # no dice nada del número. Cualquier lectura del fan-out le gana.
        self.assertEqual(self.vig(HOY), (4, "foto del 15-oct"))
        self.assertEqual(self.vig(HOY, self.LEIDO), (6, "leído hoy 11:00"))
        with odoo_termino("odoo_sin_respuesta"):
            self.assertEqual(self.vig(HOY), (4, "foto del 15-oct"))
        # La sombra del libro reescribe `actualizado` y no toca `stock_woo`: también.
        self.assertEqual(self.vig(SOMBRA), (4, "foto del 15-oct"))

    def test_con_prueba_de_que_odoo_respondio_la_foto_vale(self):
        with odoo_termino("ok"):
            self.assertEqual(self.vig(HOY), (4, "foto hoy 12:00"))
            self.assertEqual(self.vig(HOY, self.LEIDO), (4, "foto hoy 12:00"))     # la foto es más nueva

    def test_con_el_libro_se_compara_contra_stock_kubera(self):
        self.assertEqual(self.vig(LIBRO), (9, "foto hoy 12:00"))
        self.assertEqual(self.vig(LIBRO, self.LEIDO), (9, "foto hoy 12:00"))
        self.assertEqual(self.vig(dict(fanout_fuente="kubera", stock_watch_fuente="odoo")),
                         (9, "foto sin pasada del libro"))

    def test_la_consulta_de_la_coincidencia_sigue_a_la_foto(self):
        with con(**LIBRO):
            sql, viva = V._sql_vivas()
        self.assertTrue(viva)
        self.assertIn("then 0 else p.stock_kubera end end as stock_woo", sql)
        self.assertNotIn("{", sql.replace("%(c)s", ""))
        with con(**HOY):
            sql, viva = V._sql_vivas()
        self.assertFalse(viva)
        self.assertIn("else p.stock_woo end as stock_woo", sql)
        self.assertIn("or not false)", sql)
        # Se descarta la publicación SIN número (ni foto ni lectura), no la de la columna vacía.
        for ajustes in (LIBRO, HOY):
            with con(**ajustes):
                sql, _ = V._sql_vivas()
            self.assertIn("left join ops.stock_watch_photo p on p.sku = l.sku", sql)
            self.assertIn("v as (select * from v0 where stock_woo is not null)", sql)
            self.assertNotIn("p.stock_woo is not null", sql)
            self.assertNotIn("p.stock_kubera is not null", sql)

    def test_la_coincidencia_marca_lo_comparado_contra_la_foto_congelada(self):
        def fetch_all(sql, params=None):
            if "group by canal" in sql:
                return [{"canal": "temu", "vivas": 3, "iguales": 1, "de_mas": 1, "de_menos": 1, "de_foto": 2}]
            return [{"canal": "temu", "sku": SKU, "valor": 4, "stock_woo": 1, "de_foto": True}]
        for memoria, esperado in ((None, (2, True)), ("ok", (0, False))):
            with con(**HOY), mock.patch.object(V.sdb, "fetch_all", side_effect=fetch_all), \
                    (odoo_termino(memoria) if memoria else mock.patch.dict(SW._ultimo, {})):
                barras, distintas = V._coincidencia(["temu"])
            self.assertEqual((barras[0]["foto_congelada"], distintas[0]["foto_congelada"]), esperado, memoria)
            self.assertNotIn("de_foto", distintas[0])


# ── 9 · devoluciones ─────────────────────────────────────────────────────────

def _bit(id_: int, hora: str, accion: str, motivo: str, resultado: str) -> dict:
    return {"id": id_, "hora": hora, "ts": None, "canal": "woocommerce", "accion": accion,
            "motivo": motivo, "resultado": resultado}


class Devoluciones(unittest.TestCase):
    BIT = [_bit(1, "2026-10-14 10:00:00", "odoo_delta", "delta de Odoo (foto 3 -> 5)", "Woo 3 -> 5"),
           _bit(2, "2026-10-21 11:00:00", "vendible_cambio", "vendible 5 -> 7 (libro)", "5 -> 7 (replicar a canales)"),
           _bit(3, "2026-10-21 11:20:00", "vendible_registro", "vendible 7 -> 8 (libro)", "7 -> 8 (replicar a canales)"),
           _bit(4, "2026-10-21 11:40:00", "vendible_sin_numero", "el libro ya no trae el SKU",
                "7 -> sin número (nada que repartir: los canales se quedan con lo que tenían)"),
           _bit(5, "2026-10-16 09:00:00", "woo_cambio", "cambio de stock en Woo", "5 -> 4 (replicar a canales)"),
           _bit(6, "2026-10-21 12:00:00", "vendible_cambio", "vendible 7 -> 0 (libro: no lo trae)",
                "7 -> 0 (replicar a canales)")]

    def test_woo_de_con_la_lista_y_la_flecha_del_resultado(self):
        w = FD._woo_de(self.BIT)
        self.assertEqual([(x["id"], x["origen"], x["de"], x["a"], x["odoo_de"], x["odoo_a"], bool(x.get("sin_numero")))
                          for x in w],
                         [(1, "odoo", 3, 5, 3, 5, False), (5, "woo", 5, 4, None, None, False),
                          (2, "libro", 5, 7, None, None, False), (6, "libro", 7, 0, None, None, True)])

    def test_solo_odoo_sube(self):
        w = FD._woo_de(self.BIT)
        self.assertEqual([(w[i]["id"], sube) for i, _t, sube in V._subidas_odoo(w)], [(1, 2)])
        por_sub, sin = FD.asignar_subidas([{"id": 9, "hora": "2026-10-21 10:50:00", "destino": "rack"}], w)
        self.assertEqual((dict(por_sub), [r["id"] for r in sin]), ({}, [9]))


# ── 10 · el router ───────────────────────────────────────────────────────────

class D4(unittest.TestCase):
    """Con el fan-out en el libro y la pasada del libro corriendo (T0), un SKU que el
    libro nunca trajo (sin `stock_kubera`) y que un canal ofrece: el fan-out le reparte 0."""

    PASADA = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
    VIEJA = datetime(2026, 10, 15, 18, 34, tzinfo=timezone.utc)

    def vig(self, ft: dict, leido: dict | None = None, ajustes=LIBRO, pasada=PASADA):
        with con(**ajustes):
            vend = V._foto_vendible()
            return V._woo_vigente(ft, leido, "2026-10-21", vend, pasada)

    def test_lo_que_el_libro_no_trae_vale_cero(self):
        nunca = {"stock_woo": 12, "stock_kubera": None, "actualizado": self.VIEJA}
        self.assertEqual(self.vig(nunca), (0, V.LIBRO_NO_LO_TRAE))
        self.assertEqual(self.vig({}), (0, V.LIBRO_NO_LO_TRAE), "ni fila en la foto")

    def test_una_lectura_posterior_le_gana_y_una_anterior_no(self):
        nunca = {"stock_woo": 12, "stock_kubera": None, "actualizado": self.VIEJA}
        despues = {"ts": self.PASADA + timedelta(minutes=5), "valor": 3, "hora": "2026-10-21 12:05:00"}
        antes = {"ts": self.PASADA - timedelta(hours=1), "valor": 12, "hora": "2026-10-21 11:00:00"}
        self.assertEqual(self.vig(nunca, despues), (3, "leído hoy 12:05"))
        self.assertEqual(self.vig(nunca, antes), (0, V.LIBRO_NO_LO_TRAE), "la de Woo antes de la pasada no")

    def test_sin_pasada_o_con_el_null_que_la_pasada_sello_no(self):
        nunca = {"stock_woo": 12, "stock_kubera": None, "actualizado": self.VIEJA}
        self.assertEqual(self.vig(nunca, pasada=None)[0], None, "sin pasada no se sabe qué trae el libro")
        # La sombra conserva el NULL de lo que no repartió y lo sella con la pasada.
        sellado = {"stock_woo": 12, "stock_kubera": None, "actualizado": self.PASADA,
                   "foto_hora": "2026-10-21 12:00:00"}
        self.assertEqual(self.vig(sellado)[0], None)

    def test_sin_el_libro_en_marcha_no(self):
        nunca = {"stock_woo": 12, "stock_kubera": None, "actualizado": self.VIEJA}
        self.assertEqual(self.vig(nunca, ajustes=SOMBRA), (12, "foto del 15-oct"), "el fan-out reparte Woo")
        self.assertEqual(self.vig(nunca, ajustes=dict(fanout_fuente="kubera", stock_watch_fuente="odoo"))[0],
                         None, "sin pasada del libro no hay D4")

    def test_la_pasada_sólo_se_consulta_con_el_libro_en_marcha(self):
        llamadas = []

        def fetch_one(sql, params=None):
            llamadas.append(sql)
            return {"t": self.PASADA}
        with mock.patch.object(V.sdb, "fetch_one", side_effect=fetch_one):
            with con(**LIBRO):
                self.assertEqual(V._pasada_libro(V._foto_vendible()), self.PASADA)
            with con(**SOMBRA):
                self.assertIsNone(V._pasada_libro(V._foto_vendible()))
        self.assertEqual(len(llamadas), 1)
        self.assertIn("where stock_kubera is not null", llamadas[0])

    def test_la_coincidencia_lo_cuenta_aparte(self):
        def fetch_all(sql, params=None):
            if "group by canal" in sql:
                return [{"canal": "temu", "vivas": 3, "iguales": 1, "de_mas": 2, "de_menos": 0, "de_foto": 3,
                         "no_trae": 1}]
            return [{"canal": "temu", "sku": SKU, "valor": 5, "stock_woo": 0, "de_foto": True, "no_trae": True}]
        with con(**LIBRO), mock.patch.object(V.sdb, "fetch_all", side_effect=fetch_all):
            barras, distintas = V._coincidencia(["temu"])
        self.assertEqual((barras[0]["libro_no_trae"], barras[0]["de_mas"]), (1, 2))
        self.assertTrue(distintas[0]["libro_no_trae"])
        self.assertNotIn("no_trae", distintas[0])

    def test_el_pendiente_lo_dice(self):
        d = {"canal": "temu", "sku": SKU, "cuenta": None, "valor": 5, "stock_woo": 0, "libro_no_trae": True}
        r = SinFalsoVerde.resumen(SinFalsoVerde(), LIBRO, [d])
        self.assertEqual(r["atender"][0]["titulo"], f"Temu ofrece 5 de {SKU} con el libro en 0 (el libro no lo trae)")


class FotoQuieta(unittest.TestCase):
    def _sql_leido(self, ajustes, memoria=None) -> str:
        with con(**ajustes), mock.patch.object(V.sdb, "fetch_all", return_value=[]) as fa, \
                (odoo_termino(memoria) if memoria else mock.patch.dict(SW._ultimo, {}, clear=True)):
            V._woo_leido([SKU])
        return fa.call_args.args[0]

    def test_la_lectura_se_busca_desde_que_la_foto_se_quedo_quieta(self):
        sql = self._sql_leido(HOY)
        self.assertIn("timestamptz '2026-10-15 18:34:00+00'", sql)
        self.assertNotIn("3 days", sql)
        with con(**HOY), mock.patch.dict(SW._ultimo, {}, clear=True):
            vivas, _ = V._sql_vivas()
        self.assertIn("and ts > timestamptz '2026-10-15 18:34:00+00'", vivas)

    def test_con_la_foto_viva_tres_dias(self):
        self.assertIn("now() - interval '3 days'", self._sql_leido(HOY, "ok"))
        self.assertIn("now() - interval '3 days'", self._sql_leido(LIBRO))


class NoRepartido(unittest.TestCase):
    EST = PasadaStockWatch.EST

    def correr(self, est, ajustes=LIBRO):
        return PasadaStockWatch.correr(PasadaStockWatch(), est, ajustes=ajustes)

    def test_en_sombra_ni_los_que_salieron_del_libro(self):
        r = self.correr({**self.EST, "solo_registra": True}, ajustes=SOMBRA)
        self.assertEqual((r["cambios"], r["vendible_cambios"], r["vendible_sin_numero"], r["vendible_registro"]),
                         (0, 0, 0, 3))

    def test_una_pasada_frenada_no_repartio_nada(self):
        r = self.correr({**self.EST, "estado": "frenado", "cambios": 3, "nota": "3 cambios (tope 2)"})
        self.assertEqual((r["aborta"], r["cambios"], r["vendible_cambios"], r["vendible_sin_numero"], r["nota"]),
                         (True, 0, 0, 0, "3 cambios (tope 2)"))

    def test_lo_que_va_bien_no_enseña_la_pasada_vieja_si_aborta(self):
        def resumen(sw):
            with mock.patch.object(V, "_stock_watch", return_value=sw):
                with con(**HOY), mock.patch.object(V.sdb, "fetch_all", return_value=[]), \
                        mock.patch.object(V.sdb, "fetch_one", return_value={}), \
                        mock.patch.multiple(V, reparto=lambda: ["temu"],
                                            _ahora_local=lambda: {"ahora": "2026-10-21 13:00:00"},
                                            _coincidencia=lambda rep: ([], []),
                                            _estado_canales=lambda rep, hoy: [],
                                            _full_sin_regla=lambda: [], _seguro_cero=lambda: None,
                                            _serie=lambda rep: [], _pulso_24h=lambda rep: []):
                    return V._resumen()
        ok = resumen({"ultima": "15-oct 12:34", "cambios": 4, "aborta": False})
        self.assertTrue(any(b.startswith("Última pasada") for b in ok["bien"]))
        aborta = resumen({"ultima": "15-oct 12:34", "cambios": 0, "aborta": True})
        self.assertFalse(any(b.startswith("Última pasada") for b in aborta["bien"]))

    def test_vendidas_sin_orden_no_explican_contra_odoo_congelado(self):
        so = {"piezas": 2, "ventas": 1, "canales": "temu", "edad": "3 h"}
        foto = {"stock_odoo": 10, "stock_woo": 8, "stock_kubera": None, "actualizado": None, "foto_hora": None}

        def historia(memoria):
            with con(**HOY, stock_watch_resta_pendientes=True), \
                    (odoo_termino(memoria) if memoria else mock.patch.dict(SW._ultimo, {}, clear=True)), \
                    mock.patch.object(SW, "resta_pendientes", return_value=True), \
                    mock.patch.object(V, "reparto", return_value=["temu"]), \
                    mock.patch.object(V, "_ahora_local", return_value={"ahora": "2026-10-21 13:00:00"}), \
                    mock.patch.object(V, "_marcas_seguro", return_value={}), \
                    mock.patch.object(V, "_sin_orden", return_value={SKU: so}), \
                    mock.patch.object(V, "_causas", return_value={}), \
                    mock.patch.object(V, "_devoluciones", return_value=([], [])), \
                    mock.patch.object(V.sdb, "fetch_one", return_value=foto), \
                    mock.patch.object(V.sdb, "fetch_all", return_value=[]):
                return V.historia(SKU, 14)
        self.assertFalse(historia(None)["sin_orden"]["explica"], "Odoo congelado: no explica nada")
        self.assertTrue(historia("ok")["sin_orden"]["explica"], "con Odoo vivo, como antes")


class Router(unittest.TestCase):
    def test_inventario_estado_con_los_rotulos_del_contrato(self):
        from routers import fanout as rf
        with con(**HOY):
            r = rf.inventario_estado()
        self.assertEqual((r["odoo_rotulo"], r["stock_rotulo"]), (CONGELADO, "Woo"))
        with con(**LIBRO):
            self.assertEqual(rf.inventario_estado()["stock_rotulo"], "Libro de kubera")

    def test_odoo_monitor_retirado(self):
        from routers import fanout as RF
        self.assertFalse(hasattr(RF, "odoo_monitor"))
        self.assertFalse(any(str(getattr(r, "path", "")).endswith("/odoo/monitor") for r in RF.router.routes))

    def test_pendientes_en_mysql_con_la_misma_lista(self):
        from routers import fanout as RF
        from services import db
        llamadas = []
        with mock.patch.multiple(RF.settings, supabase_read_fanout_log=False), \
                mock.patch.object(db, "fetch_all", side_effect=lambda sql, p=None: llamadas.append((sql, p)) or []):
            r = RF.inventario_pendientes(limite=7)
        (sql, params), = llamadas
        self.assertIn("WHERE accion IN %s", sql)
        self.assertEqual(params, (tuple(fanout_read._ACCIONES_INVENTARIO), 7))
        self.assertIn("vendible_cambio", params[0])
        self.assertEqual(r, {"eventos": [], "total": 0})


if __name__ == "__main__":
    unittest.main()
