"""Pruebas de la lectura del LIBRO de kubera (`FANOUT_FUENTE=kubera`, `STOCK_WATCH_FUENTE=libro`).

── POR QUÉ ────────────────────────────────────────────────────────────────────
La ruta directa del plan de salida de Woo (20-oct) saca a Woo del camino del stock
en T0: el fan-out deja de leer el `_stock` de Woo y reparte el vendible del libro
(`services/stock_libro.py`), y `stock_watch` vigila ese número en vez del de Woo.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
 1. EL NÚMERO: Σ libre_vendible de las bodegas del libro − ventas DROP sin OV
    confirmada desde el corte, nunca negativo; un SKU que el libro no trae vale 0
    (D4). Y el SQL lee lo que el contrato dice. (`Numero`, `ContratoSql`)
 2. FALLA CERRADA: errata en la fuente, sin STOCK_LIBRO_DESDE o sin zona, vista con
    nombre raro o inexistente, ninguna bodega que cuente, libro vacío o a medio
    cargar (piso de cobertura), la base caída → `LibroNoDisponible`; nunca Woo,
    nunca 0. (`FallaCerrada`, `Cobertura`)
 3. EL FAN-OUT lee el libro con `kubera` y Woo con `woo`, sin tocar WPDB en el
    primer caso; el plan dice de dónde no pudo leer. (`FanoutLee`)
 4. LOS LECTORES DE LA FOTO (seguro, excedentes, espejo del DROP, panel) eligen
    `stock_kubera` con `kubera`. (`LectoresDeLaFoto`)
 5. LA PASADA DEL LIBRO en `stock_watch`: anota `vendible_cambio` con la flecha que
    leen la matriz y el recuperador, encola «cambio de vendible en kubera», guarda
    `stock_kubera` y NUNCA lee ni escribe Woo. La primera pasada mide contra el
    último `stock_woo`. (`PasadaLibro`)
 6. SIN EL FAN-OUT EN EL LIBRO, SÓLO REGISTRA (la sombra): con `FANOUT_FUENTE=woo`
    o con SOLO_REGISTRO no encola, y la foto conserva lo no repartido —nunca un
    número de Woo en `stock_kubera`—. (`Sombra`)
 7. FRENOS: tope, piso, bitácora apagada, libro ilegible, errata del fan-out.
    (`FrenosLibro`)
 8. LO QUE SALE DEL LIBRO VA A 0 (D4): se anota como `vendible_cambio` «X -> 0», se
    encola y su `stock_kubera` queda en 0; nunca deja el número viejo. (`Ausentes`)
 9. PUBLICAR nace con el número del libro y explica por qué no se publica.
    (`Publicar`)
10. LA COMPARACIÓN DE T0 clasifica bien. (`Comparacion`)
11. STOCK_FULL no escribe Woo con el fan-out en el libro. (`StockFull`)

Sin red: kubera, Woo y el fan-out van suplantados. Los SKUs son INVENTADOS (el repo
es público).

    cd backend && python -m unittest tests.test_stock_libro -v
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import (fanout_excedentes, fanout_seguro, fanout_stock, publicar_ready,  # noqa: E402
                      stock_libro, stock_watch_read, wp_db)
from services import stock_watch as sw  # noqa: E402
from services import supabase_db as sdb  # noqa: E402
from tests import test_stock_watch_fuente as _F  # noqa: E402

T0 = "2026-10-22T15:00:00Z"
LIBRO = {"fanout_fuente": "kubera", "stock_libro_desde": T0,
         "stock_libro_vista": "almacen.existencias_bodega_v", "stock_libro_min_skus": 1}
_FLECHA = re.compile(r"(-?\d+)\s*->\s*(-?\d+)")     # = fanout_vivo._FLECHA


class KuberaFalsa:
    """`supabase_db.fetch_one/fetch_all` para el libro. `libre` = {sku: Σ libre_vendible},
    `pend` = {sku: piezas DROP sin OV}. `vista` = ¿existe?, `bodegas` = ¿alguna cuenta?"""

    def __init__(self, libre: dict[str, int], pend: dict[str, int] | None = None, *,
                 vista: bool = True, bodegas: bool = True, error: Exception | None = None,
                 vacias: tuple[str, ...] = ()):
        self.libre, self.pend = libre, pend or {}
        self.vista, self.bodegas, self.error, self.vacias = vista, bodegas, error, vacias
        self.sql: list[tuple[str, object]] = []

    def _filtra(self, d: dict[str, int], params) -> dict[str, int]:
        if isinstance(params, dict) and "s" in params:
            return {k: v for k, v in d.items() if k in params["s"]}
        return d

    def fetch_one(self, sql, params=None):
        self.sql.append((sql, params))
        if self.error:
            raise self.error
        if "con_piezas" in sql:
            return {"con_piezas": sum(1 for v in self.libre.values() if v > 0)}
        return {"r": "almacen.existencias_bodega_v" if self.vista else None}

    def fetch_all(self, sql, params=None):
        self.sql.append((sql, params))
        if self.error:
            raise self.error
        if "from almacen.almacenes where" in sql:
            return [{"codigo": "B1"}] if self.bodegas else []
        if "count(b.sku) as filas" in sql:
            return [{"codigo": "B1", "filas": len(self.libre)}] + [{"codigo": c, "filas": 0} for c in self.vacias]
        if "libre_vendible" in sql:
            return [{"sku": k, "libre": v} for k, v in self._filtra(self.libre, params).items()]
        if "channel.order_items" in sql:
            return [{"sku": k, "pzs": v} for k, v in self._filtra(self.pend, params).items()]
        raise AssertionError(f"consulta inesperada: {sql[:80]}")

    def parches(self):
        return [mock.patch.object(sdb, "fetch_one", self.fetch_one),
                mock.patch.object(sdb, "fetch_all", self.fetch_all)]


class ConLibro(unittest.TestCase):
    def libro(self, kub: KuberaFalsa, **cfg):
        stock_libro._cobertura.clear()
        self.addCleanup(stock_libro._cobertura.clear)
        for p in [mock.patch.multiple(stock_libro.settings, **{**LIBRO, **cfg}), *kub.parches()]:
            p.start()
            self.addCleanup(p.stop)
        return kub


# ── 1 · el número ────────────────────────────────────────────────────────────

class Numero(ConLibro):
    def test_libre_menos_pendientes_nunca_negativo(self):
        self.libro(KuberaFalsa({"PRB-0001-AZL": 10, "PRB-0002-ROJ": 2, "PRB-0003-VRD": 0},
                               {"PRB-0001-AZL": 3, "PRB-0002-ROJ": 5, "PRB-0009-NEG": 4}))
        self.assertEqual(stock_libro.todos(), {"PRB-0001-AZL": 7, "PRB-0002-ROJ": 0, "PRB-0003-VRD": 0})

    def test_el_que_el_libro_no_trae_vale_cero(self):
        # D4: con el libro disponible, «no lo conozco» es 0, nunca el número viejo.
        self.libro(KuberaFalsa({"PRB-0001-AZL": 4}, {"PRB-0009-NEG": 1}))
        self.assertEqual(stock_libro.numero("PRB-0009-NEG"), 0)
        self.assertEqual(stock_libro.numero("prb-0001-azl"), 4)
        self.assertEqual(stock_libro.numeros(["PRB-0001-AZL", "PRB-0009-NEG"]),
                         {"PRB-0001-AZL": 4, "PRB-0009-NEG": 0})
        self.assertEqual(stock_libro.numeros([]), {})
        self.assertNotIn("PRB-0009-NEG", stock_libro.todos())     # todos(): sólo lo que trae

    def test_desglose_trae_las_dos_mitades(self):
        self.libro(KuberaFalsa({"PRB-0001-AZL": 10}, {"PRB-0001-AZL": 3, "PRB-0009-NEG": 1}))
        self.assertEqual(stock_libro.desglose(),
                         {"PRB-0001-AZL": {"libre": 10, "pendientes": 3},
                          "PRB-0009-NEG": {"libre": None, "pendientes": 1}})

    def test_un_sku_filtra_en_mayusculas(self):
        kub = self.libro(KuberaFalsa({"PRB-0001-AZL": 4}))
        stock_libro.numero("prb-0001-azl")
        self.assertEqual([p["s"] for _s, p in kub.sql if isinstance(p, dict) and "s" in p],
                         [["PRB-0001-AZL"], ["PRB-0001-AZL"]])


class ContratoSql(unittest.TestCase):
    def test_libre_solo_de_bodegas_del_libro_que_cuentan(self):
        sql = stock_libro._SQL_LIBRE
        self.assertIn("sum(b.libre_vendible)", sql)
        self.assertIn("a.fuente = 'kubera' and a.cuenta_para_woo and b.fuente = 'kubera'", sql)
        self.assertNotIn("stock_almacen", sql)          # incluye cuarentena y scrap

    def test_pendientes_drop_sin_ov_desde_t0(self):
        sql = stock_libro._SQL_PENDIENTES
        self.assertIn("o.creado_at >= %(desde)s", sql)
        self.assertIn("not coalesce(i.es_fulfillment, false)", sql)     # el renglón, no el encabezado
        self.assertNotIn("o.es_fulfillment", sql)
        self.assertIn("coalesce(o.estado_wc, '') <> 'cancelled'", sql)
        self.assertIn("v.mp_canal = o.canal and v.mp_orden = o.external_order_id", sql)
        self.assertIn("v.mp_cuenta = upper(btrim(o.cuenta))", sql)          # ML: un id en dos cuentas
        self.assertIn("v.estado in ('confirmada', 'entregada')", sql)


# ── 2 · falla cerrada ────────────────────────────────────────────────────────

class FallaCerrada(ConLibro):
    def _lanza(self, kub: KuberaFalsa | None = None, texto: str = "", **cfg):
        self.libro(kub or KuberaFalsa({"PRB-0001-AZL": 4}), **cfg)
        with self.assertRaises(stock_libro.LibroNoDisponible) as c:
            stock_libro.todos()
        self.assertIn(texto, str(c.exception))

    def test_sin_t0(self):
        self._lanza(texto="STOCK_LIBRO_DESDE", stock_libro_desde="")

    def test_t0_sin_zona_o_basura(self):
        self._lanza(texto="zona", stock_libro_desde="2026-10-22T15:00:00")
        self._lanza(texto="ISO", stock_libro_desde="mañana")

    def test_vista_con_nombre_raro(self):
        self._lanza(texto="esquema.vista", stock_libro_vista="almacen.v; drop table x")

    def test_vista_inexistente(self):
        self._lanza(KuberaFalsa({}, vista=False), "no existe")

    def test_ninguna_bodega_cuenta(self):
        self._lanza(KuberaFalsa({"PRB-0001-AZL": 4}, bodegas=False), "T0 todavía no ocurre")

    def test_la_base_cae(self):
        self._lanza(KuberaFalsa({}, error=ConnectionError("pooler")), "pooler")

    def test_errata_en_la_fuente(self):
        with mock.patch.object(stock_libro.settings, "fanout_fuente", "kubra"):
            with self.assertRaises(stock_libro.LibroNoDisponible):
                stock_libro.fuente_fanout()
            self.assertTrue(stock_libro.lee_libro())    # el lector se niega, no lee Woo

    def test_vacia_es_woo(self):
        for v in (None, "", "  ", " WOO "):
            with mock.patch.object(stock_libro.settings, "fanout_fuente", v):
                self.assertEqual(stock_libro.fuente_fanout(), "woo", repr(v))
                self.assertFalse(stock_libro.lee_libro())


class Cobertura(ConLibro):
    LIBRE = {"PRB-0001-AZL": 4, "PRB-0002-ROJ": 0, "PRB-0003-VRD": 2}

    def test_libro_completo_por_debajo_del_piso(self):
        self.libro(KuberaFalsa(dict(self.LIBRE)), stock_libro_min_skus=3)
        with self.assertRaises(stock_libro.LibroNoDisponible) as c:
            stock_libro.todos()
        self.assertIn("2 SKUs con piezas y el mínimo es 3", str(c.exception))

    def test_un_sku_tambien_respeta_el_piso(self):
        self.libro(KuberaFalsa(dict(self.LIBRE)), stock_libro_min_skus=3)
        with self.assertRaises(stock_libro.LibroNoDisponible):
            stock_libro.numero("PRB-0001-AZL")

    def test_el_piso_se_mide_una_vez_por_ventana(self):
        kub = self.libro(KuberaFalsa(dict(self.LIBRE)), stock_libro_min_skus=2)
        self.assertEqual(stock_libro.numero("PRB-0001-AZL"), 4)
        self.assertEqual(stock_libro.numero("PRB-0003-VRD"), 2)
        self.assertEqual(sum(1 for q, _p in kub.sql if "con_piezas" in q), 1)

    def test_una_bodega_sin_filas_tumba_el_libro(self):
        # Dos bodegas completas pasan solas el piso global; la tercera, ausente, mandaría sus
        # SKUs a 0 (D4). El piso por bodega lo atrapa, también en la lectura de un SKU.
        self.libro(KuberaFalsa(dict(self.LIBRE), vacias=("TEX2",)), stock_libro_min_skus=1)
        with self.assertRaises(stock_libro.LibroNoDisponible) as c:
            stock_libro.numero("PRB-0001-AZL")
        self.assertIn("TEX2", str(c.exception))
        with self.assertRaises(stock_libro.LibroNoDisponible):
            stock_libro.todos()

    def test_el_piso_por_bodega_no_se_apaga_con_cero(self):
        self.libro(KuberaFalsa(dict(self.LIBRE), vacias=("DROP",)), stock_libro_min_skus=0)
        with self.assertRaises(stock_libro.LibroNoDisponible):
            stock_libro.numero("PRB-0001-AZL")

    def test_cero_lo_apaga(self):
        kub = self.libro(KuberaFalsa({"PRB-0001-AZL": 0}), stock_libro_min_skus=0)
        self.assertEqual(stock_libro.numero("PRB-0001-AZL"), 0)
        self.assertFalse(any("con_piezas" in q for q, _p in kub.sql))

    def test_por_omision_pide_4000(self):
        self.assertEqual(stock_libro.settings.model_fields["stock_libro_min_skus"].default, 4000)


# ── 3 · el fan-out lee ───────────────────────────────────────────────────────

class FanoutLee(ConLibro):
    def test_con_kubera_lee_el_libro_y_no_toca_wpdb(self):
        self.libro(KuberaFalsa({"PRB-0001-AZL": 6}, {"PRB-0001-AZL": 1}))
        with mock.patch.object(wp_db, "_fetch_all", side_effect=AssertionError("WPDB")):
            self.assertEqual(fanout_stock._stock_drop("PRB-0001-AZL"), 5)
            self.assertEqual(fanout_stock._stock_drop("PRB-0009-NEG"), 0)
            self.assertEqual(fanout_stock._stock_drop_varios(["PRB-0001-AZL", "PRB-0009-NEG"]),
                             {"PRB-0001-AZL": 5, "PRB-0009-NEG": 0})

    def test_el_plan_dice_de_donde_no_leyo(self):
        # Con el libro ya no hay «sin número» (D4); el rótulo sigue por si `_stock_drop`
        # devolviera None (Woo, o un cambio futuro).
        self.libro(KuberaFalsa({"PRB-0001-AZL": 3}))
        with mock.patch.object(fanout_stock, "_stock_drop", return_value=None):
            p = fanout_stock.plan("PRB-0009-NEG")
        self.assertEqual((p["ok"], p["motivo"]), (False, "sin stock legible en el libro de kubera"))

    def test_libro_caido_lanza_igual_que_wpdb(self):
        self.libro(KuberaFalsa({}, error=ConnectionError("pooler")))
        with mock.patch.object(wp_db, "_fetch_all", side_effect=AssertionError("WPDB")):
            with self.assertRaises(stock_libro.LibroNoDisponible):
                fanout_stock._stock_drop("PRB-0001-AZL")

    def test_con_woo_lee_wpdb(self):
        with mock.patch.object(stock_libro.settings, "fanout_fuente", "woo"), \
                mock.patch.object(wp_db, "_prefix", return_value="wp_"), \
                mock.patch.object(wp_db, "_fetch_all", return_value=[{"stock": "8"}]) as fa, \
                mock.patch.object(sdb, "fetch_all", side_effect=AssertionError("libro")):
            self.assertEqual(fanout_stock._stock_drop("PRB-0001-AZL"), 8)
            self.assertTrue(fa.called)


# ── 4 · los lectores de la foto ──────────────────────────────────────────────

class LectoresDeLaFoto(unittest.TestCase):
    def _sql(self, fn, fuente: str) -> str:
        with mock.patch.object(stock_libro.settings, "fanout_fuente", fuente), \
                mock.patch.object(sdb, "fetch_all", return_value=[]) as fa:
            fn()
        return fa.call_args[0][0]

    def test_columna(self):
        for fuente, col in (("kubera", "stock_kubera"), ("woo", "stock_woo"), ("errata", "stock_kubera")):
            with mock.patch.object(stock_libro.settings, "fanout_fuente", fuente):
                self.assertEqual(stock_watch_read.columna_vendible(), col)

    def test_seguro_excedentes_y_espejo(self):
        for fn in (lambda: fanout_seguro.candidatos("mercado_libre"),
                   lambda: fanout_excedentes.candidatos("mercado_libre", 10),
                   stock_watch_read.drop_leer):
            k, w = self._sql(fn, "kubera"), self._sql(fn, "woo")
            self.assertIn("stock_kubera", k)
            self.assertNotIn("stock_woo is not null", k)
            self.assertNotIn("stock_kubera", w)


# ── 5–8 · la pasada del libro en stock_watch ─────────────────────────────────

class FotoLibro(_F.FotoFalsa):
    def guardar_libro(self, filas, ahora=None):
        self.guardados += 1
        self.sello += 1
        for s, k in filas:
            fila = self.t.setdefault(self._clave(s), {"stock_woo": None, "stock_odoo": None,
                                                      "stock_kubera": None})
            fila["stock_kubera"], fila["actualizado"] = k, self.sello
        return len(filas)


class PasadaLibro(_F.Pasada):
    fuente = "libro"

    def armar_libro(self, numeros: dict[str, int], foto: dict[str, dict], **ajustes):
        self.woo = _F.WooFalso({})
        self.foto_l = FotoLibro(foto)
        self.armar(self.woo, self.foto_l, **{**LIBRO, **ajustes})
        for p in (mock.patch.object(stock_libro, "todos", return_value=dict(numeros)),
                  mock.patch.object(stock_watch_read, "foto_guardar_libro", self.foto_l.guardar_libro)):
            p.start()
            self.addCleanup(p.stop)

    def filas(self, accion: str) -> list[dict]:
        return [f for f in self.bitacora if f["accion"] == accion]

    def test_reparte_cambios_del_libro(self):
        self.armar_libro({"PRB-0001-AZL": 3, "PRB-0002-ROJ": 9, "PRB-0003-VRD": 4},
                         {"PRB-0001-AZL": {"woo": 8, "kubera": 5},
                          "PRB-0002-ROJ": {"woo": 1, "kubera": 7},
                          "PRB-0003-VRD": {"woo": 4, "kubera": 4}})
        r = self.correr()
        self.assertEqual((r["estado"], r["fuente"], r["vendible_cambios"], r["encolados"]),
                         ("ok", "libro", 2, 2))
        f = self.filas("vendible_cambio")
        self.assertEqual([(x["sku"], x["motivo"], x["resultado"]) for x in f],
                         [("PRB-0001-AZL", "vendible 5 -> 3 (libro)", "5 -> 3 (replicar a canales)"),
                          ("PRB-0002-ROJ", "vendible 7 -> 9 (libro)", "7 -> 9 (replicar a canales)")])
        self.assertTrue(all(x["canal"] == "woocommerce" and not x["dry_run"] for x in f))
        self.assertEqual([_FLECHA.search(x["resultado"]).groups() for x in f], [("5", "3"), ("7", "9")])
        self.assertEqual(self.encolados, [("PRB-0001-AZL", "cambio de vendible en kubera"),
                                          ("PRB-0002-ROJ", "cambio de vendible en kubera")])
        self.assertTrue(all(m.startswith("cambio de vendible en kubera") for _s, m in self.encolados))
        # La foto: stock_kubera al día, stock_woo intacto, Woo ni se leyó.
        self.assertEqual({s: (x["stock_woo"], x["stock_kubera"]) for s, x in self.foto_l.t.items()},
                         {"PRB-0001-AZL": (8, 3), "PRB-0002-ROJ": (1, 9), "PRB-0003-VRD": (4, 4)})
        self.assertEqual(self.woo.sql, [])
        self.nada_prohibido()
        self.assertIn("vendible_cambio", sw.ACCIONES_CAMBIO)

    def test_primera_pasada_mide_contra_el_ultimo_woo(self):
        self.armar_libro({"PRB-0001-AZL": 8, "PRB-0002-ROJ": 2, "PRB-0004-AMA": 1},
                         {"PRB-0001-AZL": {"woo": 8}, "PRB-0002-ROJ": {"woo": 5},
                          "PRB-0003-VRD": {"woo": 6}, "PRB-0005-GRS": {"woo": None},
                          "PRB-0006-CAF": {"woo": 0}})
        self.correr()
        self.assertEqual([(x["sku"], x["resultado"]) for x in self.filas("vendible_cambio")],
                         [("PRB-0002-ROJ", "5 -> 2 (replicar a canales)"),
                          ("PRB-0004-AMA", "sin número -> 1 (replicar a canales)"),
                          ("PRB-0003-VRD", "6 -> 0 (replicar a canales)")])
        # D4: lo que el libro no trae vale 0. Con número en Woo es un cambio; sin número
        # o en 0, sólo se siembra el 0 (el seguro y los excedentes ya lo ven).
        self.assertEqual({s: self.foto_l.t[s]["stock_kubera"] for s in
                          ("PRB-0001-AZL", "PRB-0003-VRD", "PRB-0005-GRS", "PRB-0006-CAF")},
                         {"PRB-0001-AZL": 8, "PRB-0003-VRD": 0, "PRB-0005-GRS": 0, "PRB-0006-CAF": 0})
        self.assertNotIn("PRB-0005-GRS", [s for s, _m in self.encolados])

    def test_la_siembra_no_dispara_el_piso(self):
        # Tras sembrar 0 a lo que el libro no trae, la foto tiene «número» en casi todo; el
        # piso se mide en SKUs CON PIEZAS y la segunda pasada no se frena.
        foto = {f"PRB-{i:04d}-AZL": {"woo": 1} for i in range(10)}
        foto.update({f"PRB-{i:04d}-NEG": {"woo": None} for i in range(30)})
        self.armar_libro({f"PRB-{i:04d}-AZL": 1 for i in range(10)}, foto)
        self.assertEqual(self.correr()["estado"], "ok")
        self.assertEqual(self.correr()["estado"], "ok")

    def test_el_recuperador_lo_ve(self):
        import inspect
        from services import fanout_read, fanout_recuperar
        self.assertIn("accion = any(%(c)s)", inspect.getsource(fanout_recuperar.candidatos))
        self.assertTrue({"vendible_cambio", "vendible_registro", "vendible_sin_numero"}
                        <= set(fanout_read._ACCIONES_INVENTARIO))


class Sombra(PasadaLibro):
    def _sombra(self, **aj):
        self.armar_libro({"PRB-0001-AZL": 3, "PRB-0002-ROJ": 2},
                         {"PRB-0001-AZL": {"woo": 8, "kubera": 5}, "PRB-0002-ROJ": {"woo": 6}}, **aj)
        r = self.correr()
        self.assertEqual((r["estado"], r["encolados"], self.encolados), ("ok", 0, []))
        f = self.filas("vendible_registro")
        self.assertEqual(len(f), 2)
        self.assertTrue(all(x["dry_run"] for x in f))
        self.assertEqual(self.filas("vendible_cambio"), [])
        # Lo no repartido conserva su stock_kubera (NULL incluido): nunca el número de Woo.
        self.assertEqual({s: x["stock_kubera"] for s, x in self.foto_l.t.items()},
                         {"PRB-0001-AZL": 5, "PRB-0002-ROJ": None})
        self.assertEqual(self.woo.sql, [])
        self.nada_prohibido()

    def test_fanout_todavia_en_woo(self):
        self._sombra(fanout_fuente="woo")

    def test_solo_registro(self):
        self._sombra(stock_watch_solo_registro=True)


class FrenosLibro(PasadaLibro):
    def _nada(self, r: dict, estado: str):
        self.assertEqual(r["estado"], estado)
        self.assertEqual((self.encolados, self.foto_l.guardados, self.woo.sql), ([], 0, []))

    def test_tope_frena_y_forzar_pasa(self):
        nums = {f"PRB-{i:04d}-AZL": 1 for i in range(301)}
        self.armar_libro(nums, {s: {"woo": 2, "kubera": 2} for s in nums})
        self._nada(self.correr(), "frenado")
        self.assertEqual([f["accion"] for f in self.bitacora], ["stock_watch_freno"])
        r = self.correr(forzar=True)
        self.assertEqual((r["estado"], r["encolados"]), ("ok", 301))

    def test_piso(self):
        foto = {f"PRB-{i:04d}-AZL": {"kubera": 1} for i in range(20)}
        self.armar_libro({f"PRB-{i:04d}-AZL": 1 for i in range(10)}, foto)
        self._nada(self.correr(), "libro_parcial")

    def test_bitacora_apagada(self):
        self.armar_libro({"PRB-0001-AZL": 1}, {}, supabase_write_fanout_log=False)
        self._nada(self.correr(), "bitacora_apagada")

    def test_libro_ilegible(self):
        self.armar_libro({}, {})
        with mock.patch.object(stock_libro, "todos",
                               side_effect=stock_libro.LibroNoDisponible("sin vista")):
            self._nada(self.correr(), "libro_no_disponible")

    def test_errata_del_fanout(self):
        self.armar_libro({"PRB-0001-AZL": 1}, {}, fanout_fuente="kubra")
        self._nada(self.correr(), "fanout_fuente_invalida")


class Ausentes(PasadaLibro):
    def test_lo_que_el_libro_deja_de_traer_va_a_cero(self):
        self.armar_libro({"PRB-0001-AZL": 4},
                         {"PRB-0001-AZL": {"kubera": 4}, "PRB-0002-ROJ": {"woo": 3, "kubera": 6},
                          "PRB-0003-VRD": {"kubera": 0}})
        r = self.correr(forzar=True)    # 1 de 3 está por debajo del piso
        self.assertEqual((r["vendible_sin_numero"], r["vendible_cambios"]), (1, 1))
        self.assertEqual(self.encolados, [("PRB-0002-ROJ", "cambio de vendible en kubera")])
        self.assertEqual([(f["sku"], f["accion"], f["motivo"], f["resultado"]) for f in self.bitacora],
                         [("PRB-0002-ROJ", "vendible_cambio", "vendible 6 -> 0 (libro: no lo trae)",
                           "6 -> 0 (replicar a canales)")])
        self.assertEqual(self.foto_l.t["PRB-0002-ROJ"]["stock_kubera"], 0)
        self.assertEqual(self.foto_l.t["PRB-0002-ROJ"]["stock_woo"], 3)
        self.assertEqual(self.foto_l.t["PRB-0003-VRD"]["stock_kubera"], 0)   # ya en 0: no es cambio

    def test_en_sombra_conserva_su_numero(self):
        self.armar_libro({"PRB-0001-AZL": 4}, {"PRB-0001-AZL": {"kubera": 4}, "PRB-0002-ROJ": {"kubera": 6}},
                         fanout_fuente="woo")
        self.correr(forzar=True)
        self.assertEqual([f["accion"] for f in self.bitacora], ["vendible_registro"])
        self.assertEqual(self.encolados, [])
        self.assertEqual(self.foto_l.t["PRB-0002-ROJ"]["stock_kubera"], 6)


# ── 9 · publicar ─────────────────────────────────────────────────────────────

class Publicar(unittest.TestCase):
    def test_motivos(self):
        m = publicar_ready._motivo_sin_stock
        self.assertEqual(m({"stock": 0, "sin_inventario_libro": publicar_ready.MSG_SIN_STOCK_LIBRO,
                            "sin_inventario_woo": False}), publicar_ready.MSG_SIN_STOCK_LIBRO)
        self.assertEqual(m({"stock": 0, "sin_inventario_libro": None, "sin_inventario_woo": True}),
                         publicar_ready.MSG_SIN_INVENTARIO_WOO)


# ── 10 · la comparación de T0 ────────────────────────────────────────────────

def _script():
    ruta = BACKEND / "scripts" / "comparar_libro_vs_woo.py"
    spec = importlib.util.spec_from_file_location("comparar_libro_vs_woo", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Comparacion(unittest.TestCase):
    def test_clasifica(self):
        c = _script().clasificar(
            {"PRB-0001-AZL": {"libre": 5, "pendientes": 1}, "PRB-0002-ROJ": {"libre": 3, "pendientes": 0},
             "PRB-0003-VRD": {"libre": 2, "pendientes": 0}, "PRB-0009-NEG": {"libre": None, "pendientes": 2}},
            {"PRB-0001-AZL": 4, "PRB-0002-ROJ": 1, "PRB-0003-VRD": None, "PRB-0004-AMA": 7,
             "PRB-0005-GRS": 0})
        self.assertEqual([(f["sku"], f["categoria"], f["woo"], f["libro"]) for f in c],
                         [("PRB-0002-ROJ", "distinto", 1, 3), ("PRB-0003-VRD", "solo_libro", None, 2),
                          ("PRB-0004-AMA", "solo_woo", 7, None)])


class StockFull(unittest.TestCase):
    def test_con_el_libro_siempre_registra(self):
        from services import stock_full
        for fuente, var, esperado in (("kubera", False, True), ("woo", False, False), ("woo", True, True)):
            with mock.patch.multiple(stock_full.settings, fanout_fuente=fuente,
                                     full_watch_solo_registro=var):
                self.assertEqual(stock_full.solo_registro(), esperado, (fuente, var))
        with mock.patch.object(stock_full.settings, "fanout_fuente", "kubera"):
            self.assertTrue(stock_full._destino().startswith("el libro"))


class Explicados(unittest.TestCase):
    def test_la_magnitud_tiene_que_cuadrar(self):
        m = _script()
        exp = m.leer_explicados([{"sku": "prb-0001-azl", "motivo": "cancelación tardía",
                                  "woo_menos_libro": "14"},
                                 {"sku": "PRB-0002-ROJ", "motivo": "conteo"}])
        dif = {"sku": "PRB-0001-AZL", "woo": 20, "libro": 6}
        self.assertEqual(m.explicacion(dif, exp["PRB-0001-AZL"]), "cancelación tardía")
        self.assertEqual(m.explicacion({**dif, "woo": 26}, exp["PRB-0001-AZL"]), "")
        self.assertEqual(m.explicacion({"sku": "PRB-0002-ROJ", "woo": 1, "libro": 9},
                                       exp["PRB-0002-ROJ"]), "conteo")
        self.assertEqual(m.explicacion(dif, None), "")


if __name__ == "__main__":
    unittest.main()
