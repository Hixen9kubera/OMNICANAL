"""Pruebas de `STOCK_WATCH_FUENTE` (`services/stock_watch.py`): odoo | libro.

── POR QUÉ ────────────────────────────────────────────────────────────────────
Desde el 15-oct a las 18:34 UTC Odoo rechaza la cuenta API (salida total de Odoo).
Con la fuente `odoo` cada pasada aborta en silencio. La salida de Woo tomó la ruta
directa (20-oct): en T0 la fuente es el libro de kubera (`libro`). El modo puente
`woo` (v0.654.0) nunca se desplegó y se quitó. La pasada del libro tiene sus
pruebas en tests/test_stock_libro.py (usa el arnés de este archivo).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
 1. ODOO IDÉNTICO: sin la variable (o con `odoo`, o vacía) la pasada hace
    EXACTAMENTE lo de antes; y una lectura de Odoo que se corta a la mitad aborta
    sin guardar nada. (`OdooIdentico`, `OdooParcial`)
 2. UN VALOR DESCONOCIDO —`woo` incluido— SE NIEGA: log, aviso y nada más; una
    errata no mueve a los lectores. `libro` sin libro legible tampoco toca Woo ni
    la foto. (`LibroSeNiega`)
 3. SIN MySQL con `libro`: el scheduler registra el job sin `mysql_enabled`, y
    `kubera_decide`, el rótulo y la resta siguen a la fuente. (`SinMysql`)
 4. AVISO: dos pasadas seguidas sin repartir avisan por Slack; una buena reinicia
    la cuenta. (`AvisoPorSlack`) Y `estado()` no arrastra lo de un freno.
    (`EstadoLimpio`)
 5. LOS LECTORES DEL PANEL: con el Odoo congelado, ni la matriz ni el Dashboard
    pintan «distinto de Odoo». (`LectoresVivo`; Bodegas en test_fanout_bodegas)
 6. LA FOTO con fecha trae `stock_kubera` y el sello. (`FotoLectura`)
 7. CON EL ACTA P2 (stock_watch apagado) el modo dice «apagado» y el panel recibe
    `fuentes.stock_watch_job = "apagado"`: ni «Cada 20 min» ni «modo absoluto». (`PanelConP2`)

Sin red: Woo, kubera, Odoo, Slack y el fan-out van suplantados. Los SKUs son
INVENTADOS (el repo es público).

    cd backend && python -m unittest tests.test_stock_watch_fuente -v
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import alertas, fanout_read, fanout_stock, odoo, stock_watch_read, woocommerce, wp_db  # noqa: E402
from services import stock_watch as sw  # noqa: E402

BASE = {"stock_watch_enabled": True, "stock_watch_solo_registro": False, "stock_watch_tope": 300,
        "stock_watch_absoluto": True, "stock_watch_resta_pendientes": False,
        "supabase_read_stock_watch": False, "supabase_write_stock_watch": False,
        "supabase_write_fanout_log": True, "wpdb_prefix": "wp_"}
CONGELADO = "Odoo (congelado al 15-oct 18:34, incompleto)"
# El PANEL (matriz, Dashboard) rotula Odoo FIJO desde la v0.694.0 (1.8, INVENTARIO FANOUT);
# `stock_watch.odoo_rotulo()` sigue dinámico (CONGELADO).
ROTULO_PANEL = "Odoo (congelado al 15-oct)"


class WooFalso:
    """La base de WordPress: sólo contesta SELECT. Cualquier otra cosa revienta la prueba.

    `filas` = {sku: stock}. `cortar` = cuántas filas devuelve la consulta de datos (menos
    que el COUNT = lectura incompleta). `error` = la conexión se cae en la consulta de datos."""

    def __init__(self, filas: dict[str, int | None], *, cortar: int | None = None,
                 error: Exception | None = None):
        self.filas, self.cortar, self.error = dict(filas), cortar, error
        self.sql: list[str] = []

    def __call__(self, sql: str, params=None):
        self.sql.append(sql)
        if not sql.lstrip().upper().startswith("SELECT"):
            raise AssertionError(f"¡Se intentó ESCRIBIR en Woo!: {sql[:80]}")
        if "COUNT(*)" in sql:
            return [{"n": len(self.filas)}]
        if self.error:
            raise self.error
        filas = [{"sku": s, "stock": "" if v is None else str(v), "ID": i + 1, "post_type": "product",
                  "post_parent": 0, "ptype": "simple"} for i, (s, v) in enumerate(self.filas.items())]
        return filas[:self.cortar] if self.cortar is not None else filas


class FotoFalsa:
    """`ops.stock_watch_photo`: lo que leen y escriben `foto_leer` / `foto_guardar_woo`.

    Como la tabla real: la llave es `citext` (el upsert de «abc» actualiza la fila «ABC» y
    conserva su texto) y cada guardado sella TODAS las filas que escribe con un mismo
    instante (`actualizado`; aquí un entero que sube: 1 = la foto de partida)."""

    def __init__(self, filas: dict[str, dict]):
        self.t = {s: {"stock_woo": f.get("woo"), "stock_odoo": f.get("odoo"),
                      "stock_kubera": f.get("kubera"), "actualizado": 1} for s, f in filas.items()}
        self.guardados = 0
        self.sello = 1

    def _clave(self, s: str) -> str:
        return next((k for k in self.t if k.upper() == s.upper()), s)

    def leer(self, con_fecha: bool = False):
        return {s: {"woo": f["stock_woo"], "odoo": f["stock_odoo"],
                    **({"act": f["actualizado"], "kubera": f["stock_kubera"]} if con_fecha else {})}
                for s, f in self.t.items()}

    def guardar_libro(self, filas, ahora=None):
        self.guardados += 1
        self.sello += 1
        for s, k in filas:
            fila = self.t.setdefault(self._clave(s), {"stock_woo": None, "stock_odoo": None, "stock_kubera": None})
            fila["stock_kubera"], fila["actualizado"] = k, self.sello
        return len(filas)


class Pasada(unittest.TestCase):
    """Arma el mundo falso de una pasada. Todo lo que no debe pasar es un Mock que se revisa."""

    fuente: str | None = "libro"

    def armar(self, woo: WooFalso, foto: FotoFalsa, **ajustes):
        sw._ultimo.clear()
        sw._racha_sin_repartir = 0
        self.woo, self.foto = woo, foto
        self.bitacora: list[dict] = []
        self.encolados: list[tuple[str, str]] = []
        self.avisos: list[tuple] = []
        cfg = {**BASE, **({"stock_watch_fuente": self.fuente} if self.fuente is not None else {}), **ajustes}
        self.prohibidos = {
            "escribir_woo": mock.Mock(side_effect=AssertionError("_escribir_woo")),
            "cliente_woo": mock.Mock(side_effect=AssertionError("woocommerce._client")),
            "foto_guardar_completa": mock.Mock(side_effect=AssertionError("foto_guardar toca stock_odoo")),
            "schema_mysql": mock.Mock(),
            "schema_fanout_mysql": mock.Mock(),
            "mysql_cursor": mock.Mock(side_effect=AssertionError("MySQL")),
            "mysql_fetch": mock.Mock(side_effect=AssertionError("MySQL")),
            "odoo": mock.Mock(side_effect=AssertionError("Odoo")),
        }
        p = self.prohibidos
        parches = [
            mock.patch.multiple(sw.settings, **cfg),
            mock.patch.object(wp_db, "_fetch_all", woo),
            mock.patch.object(stock_watch_read, "foto_leer", foto.leer),
            mock.patch.object(stock_watch_read, "foto_guardar_libro", foto.guardar_libro),
            mock.patch.object(stock_watch_read, "foto_guardar", p["foto_guardar_completa"]),
            mock.patch.object(fanout_read, "registrar", side_effect=lambda f: self.bitacora.append(dict(f))),
            mock.patch.object(fanout_stock, "encolar",
                              side_effect=lambda s, motivo="venta": self.encolados.append((s, motivo))),
            mock.patch.object(alertas, "avisar", side_effect=lambda *a, **k: self.avisos.append(a) or True),
            mock.patch.object(sw, "_escribir_woo", p["escribir_woo"]),
            mock.patch.object(woocommerce, "_client", p["cliente_woo"]),
            mock.patch.object(sw, "_asegurar_schema", p["schema_mysql"]),
            mock.patch.object(fanout_stock, "_asegurar_schema", p["schema_fanout_mysql"]),
            mock.patch.object(sw.db, "get_cursor", p["mysql_cursor"]),
            mock.patch.object(sw.db, "fetch_all", p["mysql_fetch"]),
            mock.patch.object(odoo, "listar_catalogo", p["odoo"]),
        ]
        for x in parches:
            x.start()
            self.addCleanup(x.stop)

    def correr(self, forzar: bool = False) -> dict:
        return asyncio.run(sw.revisar(forzar=forzar))

    def nada_prohibido(self):
        for nombre, m in self.prohibidos.items():
            self.assertFalse(m.called, f"se llamó {nombre}")



# ── 6 · la foto con fecha ────────────────────────────────────────────────────

class FotoLectura(unittest.TestCase):
    def test_la_lectura_con_fecha_trae_el_sello(self):
        filas = [{"sku": "PRB-0001-AZL", "stock_woo": 4, "stock_odoo": None, "stock_kubera": 6,
                  "actualizado": "T1"}]
        with mock.patch.object(stock_watch_read.sdb, "fetch_all", return_value=filas) as fa:
            self.assertEqual(stock_watch_read.foto_leer(True),
                             {"PRB-0001-AZL": {"woo": 4, "odoo": None, "act": "T1", "kubera": 6}})
            self.assertIn("actualizado", fa.call_args[0][0])
            stock_watch_read.foto_leer()
            self.assertNotIn("actualizado", fa.call_args[0][0])      # la de Odoo, la de siempre

    def test_ya_no_hay_foto_de_woo(self):
        self.assertFalse(hasattr(stock_watch_read, "foto_guardar_woo"))
        self.assertFalse(hasattr(sw, "_pasada_woo"))


# ── 7 · un valor desconocido se niega (y `libro` sin libro no toca nada) ─────

class LibroSeNiega(Pasada):
    fuente = "libro"

    def test_libro_sin_libro_legible_no_hace_nada(self):
        # Sin STOCK_LIBRO_DESDE el libro no se lee: ni Woo, ni la foto, ni el fan-out.
        foto = FotoFalsa({"PRB-0001-AZL": {"woo": 5}})
        woo = WooFalso({"PRB-0001-AZL": 1})
        self.armar(woo, foto, fanout_fuente="kubera", stock_libro_desde="")
        with self.assertLogs("omnicanal.stock_watch", "ERROR") as logs:
            r = self.correr()
        self.assertEqual((r["estado"], r["fuente"]), ("libro_no_disponible", "libro"))
        self.assertIn("STOCK_LIBRO_DESDE", logs.output[0])
        self.assertEqual((woo.sql, self.bitacora, self.encolados, foto.guardados), ([], [], [], 0))
        self.nada_prohibido()
        self.assertTrue(sw.estado()["modo"].startswith("libro (reparte"))
        # Y si se queda así, a la segunda avisa.
        self.correr()
        self.assertEqual([a[0] for a in self.avisos], ["stock_watch_sin_repartir"])

    def test_un_valor_desconocido_tampoco_cae_a_odoo_ni_mueve_a_los_lectores(self):
        from services import supabase_db as sdb
        self.armar(WooFalso({}), FotoFalsa({}), stock_watch_fuente=" Kubera ")
        r = self.correr()
        self.assertEqual((r["estado"], r["fuente"]), ("fuente_no_disponible", "kubera"))
        self.nada_prohibido()
        with mock.patch.object(sdb, "disponible", return_value=True):
            self.assertEqual((sw.odoo_vivo(), sw.odoo_rotulo(), sw.kubera_decide()), (True, "Odoo", False))

    def test_vacia_o_con_espacios_es_odoo(self):
        for v in (None, "", "   ", " ODOO "):
            with mock.patch.object(sw.settings, "stock_watch_fuente", v):
                self.assertEqual(sw.fuente(), "odoo", repr(v))

    def test_forzar_no_se_salta_el_apagado(self):
        self.armar(WooFalso({}), FotoFalsa({}), stock_watch_enabled=False, stock_watch_solo_registro=False)
        self.assertEqual(self.correr(forzar=True), {"estado": "apagado"})
        self.nada_prohibido()
        # Apagado y en solo registro, forzar sí corre (no puede mover nada): aquí el
        # libro no se puede leer y se niega, pero ya no dice «apagado».
        self.armar(WooFalso({}), FotoFalsa({}), stock_watch_enabled=False, stock_watch_solo_registro=True,
                   fanout_fuente="kubera", stock_libro_desde="")
        self.assertEqual(self.correr(forzar=True)["estado"], "libro_no_disponible")

    def test_apagado_sigue_apagado(self):
        self.armar(WooFalso({}), FotoFalsa({}), stock_watch_enabled=False, stock_watch_fuente="woo")
        self.assertEqual(self.correr(), {"estado": "apagado"})


# ── 11 · sin MySQL ───────────────────────────────────────────────────────────

class SinMysql(unittest.TestCase):
    def _jobs(self, **cfg) -> list[str]:
        """Corre `scheduler.iniciar()` de verdad con el planificador suplantado y devuelve
        los ids de los jobs que registró."""
        from services import scheduler
        falso = mock.MagicMock()
        with mock.patch.object(scheduler, "AsyncIOScheduler", return_value=falso), \
                mock.patch.object(scheduler, "_scheduler", None), \
                mock.patch.multiple(scheduler.settings, **cfg):
            scheduler.iniciar()
        return [c.kwargs.get("id") for c in falso.add_job.call_args_list]

    def test_el_scheduler_registra_libro_sin_mysql_y_odoo_sigue_pidiendolo(self):
        # Un valor desconocido (`woo` incluido) también registra el job: la pasada se
        # niega y avisa, que es justo lo que tiene que pasar con una errata.
        casos = ((False, True, "libro", True), (False, True, "woo", True), (False, True, "odoo", False),
                 (True, True, "odoo", True), (False, False, "libro", False))
        for mysql, enabled, f, esperado in casos:
            ids = self._jobs(mysql_enabled=mysql, stock_watch_enabled=enabled, stock_watch_fuente=f)
            self.assertEqual("stock_watch" in ids, esperado, (mysql, enabled, f))

    def test_kubera_decide_con_otra_fuente(self):
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "disponible", return_value=True):
            for f, leer, esperado in (("odoo", False, False), ("odoo", True, True),
                                      ("woo", False, False), ("libro", False, True),
                                      ("wooo", False, False), ("   ", False, False)):
                with mock.patch.multiple(sw.settings, stock_watch_fuente=f, supabase_read_stock_watch=leer):
                    self.assertEqual(sw.kubera_decide(), esperado, (f, leer))

    def test_rotulo_y_resta(self):
        with mock.patch.multiple(sw.settings, stock_watch_fuente="odoo", stock_watch_absoluto=True,
                                 stock_watch_resta_pendientes=True):
            self.assertEqual((sw.odoo_rotulo(), sw.resta_pendientes()), ("Odoo", True))
            self.assertIsNone(sw.estado()["odoo_congelado"])
        with mock.patch.multiple(sw.settings, stock_watch_fuente="libro", stock_watch_absoluto=True,
                                 stock_watch_resta_pendientes=True):
            self.assertEqual((sw.odoo_rotulo(), sw.resta_pendientes()), (CONGELADO, False))
            self.assertEqual(sw.estado()["odoo_congelado"], "15-oct 18:34")



# ── 16 · con el acta P2 (stock_watch apagado), el panel no dice que corre ────

class PanelConP2(unittest.TestCase):
    """Las banderas de P2 (STOCK_WATCH_ENABLED=false, SOLO_REGISTRO=true): el pie del Dashboard
    decía «stock_watch en modo absoluto (Odoo master)» y la cadena «Cada 20 min». Ajuste del
    22-oct (MAESTRO): el modo dice «apagado» y el panel lee `fuentes.stock_watch_job`."""

    P2 = {"stock_watch_enabled": False, "stock_watch_solo_registro": True, "stock_watch_fuente": "odoo",
          "stock_watch_absoluto": True, "fanout_fuente": "woo"}

    def test_el_modo_dice_apagado(self):
        with mock.patch.multiple(sw.settings, **self.P2):
            self.assertEqual(sw.estado()["modo"], "apagado (STOCK_WATCH_ENABLED=false)")
        with mock.patch.multiple(sw.settings, **{**self.P2, "stock_watch_enabled": True}):
            self.assertEqual(sw.estado()["modo"], "absoluto (Odoo master)")

    def test_lo_que_lee_el_panel(self):
        from services import fanout_fuentes, fanout_vivo
        antes = dict(fanout_fuentes._estado)
        self.addCleanup(lambda: (fanout_fuentes._estado.clear(), fanout_fuentes._estado.update(antes)))
        with mock.patch.multiple(sw.settings, **self.P2),                 mock.patch.object(fanout_vivo.sdb, "fetch_one", return_value={}):
            fanout_fuentes.al_arrancar(False)            # P2: el scheduler no registra el job
            d = fanout_vivo._stock_watch("2026-10-22")
        self.assertFalse(d["habilitado"])
        self.assertEqual(d["modo"], "apagado (STOCK_WATCH_ENABLED=false)")
        self.assertEqual(d["fuentes"]["stock_watch_job"], "apagado")

# ── 14 · aviso por Slack y estado limpio ─────────────────────────────────────

T0 = "2026-10-15T16:48:47Z"
LIBRO = {"fanout_fuente": "kubera", "stock_libro_desde": T0, "stock_libro_min_skus": 1}


class ConLibroFalso(Pasada):
    """La pasada del libro con `stock_libro.todos` suplantado: `self.libro` = {sku: n};
    `self.caido` = una excepción para lanzar en lugar de leer."""

    def armar_libro(self, foto: dict[str, dict], libro: dict[str, int]):
        from services import stock_libro
        self.libro, self.caido = dict(libro), None
        self.armar(WooFalso({}), FotoFalsa(foto), **LIBRO)

        def todos():
            if self.caido:
                raise self.caido
            return dict(self.libro)
        p = mock.patch.object(stock_libro, "todos", todos)
        p.start()
        self.addCleanup(p.stop)
        return stock_libro


class AvisoPorSlack(ConLibroFalso):
    FOTO = {f"PRB-{i:04d}-NEG": {"kubera": 5} for i in range(20)}

    def test_una_no_avisa_dos_seguidas_si_y_una_buena_reinicia(self):
        sl = self.armar_libro(self.FOTO, {s: 5 for s in self.FOTO})
        self.caido = sl.LibroNoDisponible("caído")
        self.assertEqual(self.correr()["seguidas_sin_repartir"], 1)
        self.assertEqual(self.avisos, [])
        r = self.correr()
        self.assertEqual((r["estado"], r["seguidas_sin_repartir"]), ("libro_no_disponible", 2))
        self.assertEqual(len(self.avisos), 1)
        tipo, texto = self.avisos[0][:2]
        self.assertEqual(tipo, "stock_watch_sin_repartir")
        self.assertIn("2 pasadas seguidas", texto)
        self.caido = None
        r = self.correr()
        self.assertEqual(r["estado"], "ok")
        self.assertNotIn("seguidas_sin_repartir", r)
        self.caido = sl.LibroNoDisponible("otra vez")
        self.correr()
        self.assertEqual(len(self.avisos), 1)        # la racha volvió a empezar en 1

    def test_el_freno_tambien_cuenta(self):
        foto = {f"PRB-{i:04d}-GRI": {"kubera": 10} for i in range(301)}
        self.armar_libro(foto, {s: 9 for s in foto})
        self.correr()
        self.correr()
        self.assertEqual([a[0] for a in self.avisos], ["stock_watch_sin_repartir"])
        self.assertIn("`frenado`", self.avisos[0][1])


class EstadoLimpio(ConLibroFalso):
    def test_tras_un_freno_la_pasada_buena_no_arrastra_cambios(self):
        foto = {f"PRB-{i:04d}-GRI": {"kubera": 10} for i in range(301)}
        self.armar_libro(foto, {s: 9 for s in foto})
        self.assertEqual(self.correr()["cambios"], 301)
        self.libro = {s: 10 for s in foto}           # se revirtió la edición en bloque
        r = self.correr()
        self.assertEqual((r["estado"], r["vendible_cambios"]), ("ok", 0))
        for k in ("cambios", "nota", "seguidas_sin_repartir"):
            self.assertNotIn(k, r)
            self.assertNotIn(k, sw.estado())


# ── 15 · los lectores del panel ──────────────────────────────────────────────

class LectoresVivo(unittest.TestCase):
    """La matriz y el Dashboard (CadenaViva) con el Odoo de la foto congelado: un
    `woo_cambio` sobre un SKU con Odoo 10 / Woo 4 NO es «distinto de Odoo»."""

    FOTO_CONTEOS = {"skus_odoo": 5501, "skus_woo": 13262, "ambos": 5498, "distintos": 7,
                    "sin_odoo_con_piezas": 3, "piezas_sin_odoo": 40, "ultima": "2026-10-15 12:00:00"}

    def _mundo(self, fuente: str):
        from services import fanout_vivo as fv
        self.sqls: list[str] = []

        def fetch_all(sql, params=None):
            self.sqls.append(sql)
            if "stock_odoo <> stock_woo" in sql:
                return [{"sku": "PRB-0002-ROJ", "stock_odoo": 10, "stock_woo": 4}]
            if "from ops.stock_watch_photo where sku::text = any" in sql:
                return [{"sku": s, "stock_odoo": 10, "stock_woo": 4, "actualizado": None,
                         "foto_hora": "2026-10-15 12:00:00"} for s in params["s"]]
            return []

        def fetch_one(sql, params=None):
            self.sqls.append(sql)
            return dict(self.FOTO_CONTEOS) if "skus_odoo" in sql else {}

        parches = [
            mock.patch.multiple(sw.settings, stock_watch_fuente=fuente),
            # Odoo «sigue igual» sólo si su última pasada terminó bien (v0.694.0): sin esto la
            # prueba dependía del `_ultimo` que dejara otra prueba antes.
            mock.patch.dict(sw._ultimo, {"ts": 1.0, "estado": "ok"} if fuente == "odoo" else {}, clear=True),
            mock.patch.multiple(fv.sdb, fetch_all=fetch_all, fetch_one=fetch_one),
            mock.patch.multiple(fv, reparto=lambda: [], _ahora_local=lambda: {"ahora": "2026-10-15 12:00:00"},
                                _coincidencia=lambda rep: ([], []), _causas=lambda p, hoy: {},
                                _skus_perdidos=lambda **k: [], _skus_vivos=lambda rep: ["PRB-0001-AZL"],
                                _woo_leido=lambda skus: {}, _sin_orden=lambda skus: {},
                                _marcas_seguro=lambda: {}, _estado_canales=lambda rep, hoy: [],
                                _stock_watch=lambda hoy: {"ultima": "—", "cambios": 0},
                                _full_sin_regla=lambda: [], _seguro_cero=lambda: None,
                                _atender_seguro=lambda s: [], _serie=lambda rep: [], _pulso_24h=lambda rep: []),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        return fv

    def test_matriz_con_libro_no_marca_distinto_ni_lo_busca(self):
        fv = self._mundo("libro")
        m = fv.matriz()
        self.assertEqual(m["odoo_rotulo"], ROTULO_PANEL)
        self.assertFalse(any("stock_odoo <> stock_woo" in s for s in self.sqls))
        self.assertEqual([(f["sku"], f["dif"], "distinto" in f["tags"]) for f in m["filas"]],
                         [("PRB-0001-AZL", False, False)])

    def test_matriz_con_odoo_sigue_igual(self):
        fv = self._mundo("odoo")
        m = fv.matriz()
        self.assertEqual(m["odoo_rotulo"], ROTULO_PANEL)
        self.assertEqual(sorted((f["sku"], f["dif"]) for f in m["filas"]),
                         [("PRB-0001-AZL", True), ("PRB-0002-ROJ", True)])

    def test_dashboard_con_libro_no_avisa_distintos(self):
        fv = self._mundo("libro")
        r = fv._resumen()
        f = r["foto"]
        self.assertEqual((f["distintos"], f["distintos_por_ventas"], f["peor"]), (0, 0, None))
        self.assertEqual((f["skus_odoo"], f["odoo_rotulo"], f["odoo_congelado"]), (5501, ROTULO_PANEL, "15-oct 18:34"))
        self.assertFalse(any("Odoo distinto" in a["titulo"] for a in r["atender"]))
        self.assertFalse(any("limit 3000" in s for s in self.sqls))    # ni busca los distintos

    def test_dashboard_con_odoo_sigue_igual(self):
        fv = self._mundo("odoo")
        r = fv._resumen()
        self.assertEqual((r["foto"]["distintos"], r["foto"]["odoo_congelado"]), (7, None))
        self.assertTrue(any(a["titulo"] == "7 SKUs con Odoo distinto de Woo" for a in r["atender"]))


# ── 1 · Odoo idéntico ────────────────────────────────────────────────────────

class OdooIdentico(unittest.TestCase):
    """La pasada de Odoo con todo suplantado en su frontera (Odoo, Woo, la foto, la
    bitácora, el fan-out). Lo esperado se sacó corriendo EL MISMO escenario contra la
    `stock_watch.py` de origin/main (facf567): las listas son idénticas."""

    CATALOGO = [{"sku": "PRB-0001-AZL", "stock": 12}, {"sku": "PRB-0002-ROJ", "stock": 4},
                {"sku": "PRB-0003-VER", "stock": -2}, {"sku": "PRB-0004-SOL", "stock": 9},
                {"sku": "PRB-0005-FAL", "stock": 30}]
    WOO = {"PRB-0001-AZL": {"stock": 10, "id": 1, "tipo": "product", "padre": 0, "variable": False},
           "PRB-0002-ROJ": {"stock": 4, "id": 2, "tipo": "product", "padre": 0, "variable": False},
           "PRB-0003-VER": {"stock": 3, "id": 3, "tipo": "product_variation", "padre": 9, "variable": False},
           "PRB-0006-WOO": {"stock": 7, "id": 6, "tipo": "product", "padre": 0, "variable": False},
           "PRB-0005-FAL": {"stock": 1, "id": 5, "tipo": "product", "padre": 0, "variable": False}}
    FOTO = {"PRB-0001-AZL": {"woo": 10, "odoo": 10}, "PRB-0002-ROJ": {"woo": 4, "odoo": 4},
            "PRB-0003-VER": {"woo": 3, "odoo": 3}, "PRB-0006-WOO": {"woo": 8, "odoo": None},
            "PRB-0005-FAL": {"woo": 1, "odoo": 1}}

    def _correr(self, ajustes: dict) -> dict:
        sw._ultimo.clear()
        rastro: dict = {"anotar": [], "encolar": [], "foto": None, "woo": None, "llamadas": []}

        async def escribir(cambios):
            rastro["woo"] = [(s, d) for s, d, _w in cambios]
            return 1, {"PRB-0001-AZL", "PRB-0003-VER"}        # PRB-0005-FAL falla

        def listar(**kw):
            rastro["llamadas"].append(kw)
            return [dict(p) for p in self.CATALOGO]

        cfg = {**BASE, **ajustes}
        with mock.patch.multiple(sw.settings, **cfg), \
                mock.patch.object(sw, "_asegurar_schema", lambda: rastro["llamadas"].append("schema")), \
                mock.patch.object(odoo, "listar_catalogo", side_effect=listar), \
                mock.patch.object(sw, "_leer_woo", lambda: {k: dict(v) for k, v in self.WOO.items()}), \
                mock.patch.object(sw, "_foto", lambda: {k: dict(v) for k, v in self.FOTO.items()}), \
                mock.patch.object(sw, "_guardar_foto", lambda filas: rastro.update(foto=sorted(filas))), \
                mock.patch.object(sw, "_escribir_woo", escribir), \
                mock.patch.object(sw, "_anotar", lambda *a: rastro["anotar"].append(a)), \
                mock.patch.object(fanout_stock, "encolar",
                                  side_effect=lambda s, motivo="venta": rastro["encolar"].append((s, motivo))):
            r = asyncio.run(sw.revisar())
        rastro["estado"] = {k: r.get(k) for k in ("estado", "skus_odoo", "skus_woo", "odoo_deltas",
                                                  "woo_escritos", "woo_cambios", "encolados")}
        return rastro

    ESPERADO = {
        "woo": [("PRB-0001-AZL", 12), ("PRB-0003-VER", 0), ("PRB-0005-FAL", 30)],
        "anotar": [
            ("PRB-0001-AZL", "odoo_delta", "delta de Odoo (foto 10 -> 12)", "Woo 10 -> 12"),
            ("PRB-0003-VER", "odoo_delta", "delta de Odoo (foto 3 -> 0)", "Woo 3 -> 0"),
            ("PRB-0005-FAL", "odoo_delta", "delta de Odoo (foto 1 -> 30)",
             "Woo 1 -> 30 (ESCRITURA FALLÓ — se reintenta la próxima pasada)"),
            ("PRB-0006-WOO", "woo_cambio", "cambio de stock en Woo", "8 -> 7 (replicar a canales)"),
        ],
        "encolar": [("PRB-0006-WOO", "cambio de stock en Woo"), ("PRB-0001-AZL", "delta de Odoo aplicado"),
                    ("PRB-0003-VER", "delta de Odoo aplicado")],
        "foto": [("PRB-0001-AZL", 12, 12), ("PRB-0002-ROJ", 4, 4), ("PRB-0003-VER", 0, 0),
                 ("PRB-0004-SOL", None, 9), ("PRB-0005-FAL", 1, 1), ("PRB-0006-WOO", 7, None)],
        "estado": {"estado": "ok", "skus_odoo": 5, "skus_woo": 5, "odoo_deltas": 3, "woo_escritos": 1,
                   "woo_cambios": 1, "encolados": 3},
    }

    def _igual(self, r: dict):
        for k, v in self.ESPERADO.items():
            self.assertEqual(r[k], v, k)
        # MySQL sigue en su camino: el esquema se asegura y la bitácora va con su copia local
        # (`_anotar` con 4 argumentos = mysql por omisión).
        self.assertEqual(r["llamadas"], ["schema", {"estricto": True}])
        self.assertTrue(all(len(a) == 4 for a in r["anotar"]))

    def test_sin_la_variable(self):
        with mock.patch.object(sw.settings, "stock_watch_fuente", None):
            self._igual(self._correr({}))

    def test_con_odoo(self):
        self._igual(self._correr({"stock_watch_fuente": "odoo"}))

    def test_con_odoo_en_mayusculas_y_espacios(self):
        self._igual(self._correr({"stock_watch_fuente": " ODOO "}))

    def test_con_la_variable_vacia_o_en_espacios(self):
        self._igual(self._correr({"stock_watch_fuente": "   "}))


class OdooParcial(unittest.TestCase):
    """15-oct 18:34: Odoo negó el acceso en el offset 5500 y `listar_catalogo` devolvió
    la lista parcial como si fuera el catálogo. Con `estricto` lanza, y la pasada aborta."""

    def _odoo(self, cortar_en: int):
        lotes = {0: [{"default_code": f"PRB-{i:04d}", "name": "x", "list_price": 1, "free_qty": 3,
                      "qty_available": 3} for i in range(500)]}

        class Modelos:
            def execute_kw(self, db, uid, pw, modelo, metodo, args, kw):
                if kw["offset"] >= cortar_en:
                    raise PermissionError("Access Denied")
                return lotes.get(0) if kw["offset"] < 1000 else []

        return mock.patch.multiple(odoo, _uid=lambda: 7, _models=lambda: Modelos())

    def test_normal_no_cambia(self):
        with self._odoo(cortar_en=10_000):
            self.assertEqual(len(odoo.listar_catalogo()), 1000)
            self.assertEqual(len(odoo.listar_catalogo(estricto=True)), 1000)

    def test_sin_estricto_sigue_devolviendo_lo_parcial(self):
        with self._odoo(cortar_en=500):
            self.assertEqual(len(odoo.listar_catalogo()), 500)

    def test_estricto_lanza(self):
        with self._odoo(cortar_en=500), self.assertRaises(odoo.CatalogoIncompleto) as e:
            odoo.listar_catalogo(estricto=True)
        self.assertIn("offset 500", str(e.exception))

    def _pasada(self, cortar_en: int) -> tuple[dict, mock.Mock, mock.Mock]:
        sw._ultimo.clear()
        guardar, escribir = mock.Mock(), mock.Mock()
        with mock.patch.multiple(sw.settings, **{**BASE, "stock_watch_fuente": "odoo"}), \
                mock.patch.object(sw, "_asegurar_schema", lambda: None), self._odoo(cortar_en=cortar_en), \
                mock.patch.object(sw, "_leer_woo", mock.Mock(side_effect=AssertionError("no debe leer Woo"))), \
                mock.patch.object(sw, "_guardar_foto", guardar), mock.patch.object(sw, "_escribir_woo", escribir):
            r = asyncio.run(sw.revisar())
        return r, guardar, escribir

    def test_la_pasada_aborta_sin_guardar(self):
        r, guardar, escribir = self._pasada(500)
        self.assertEqual(r["estado"], "odoo_lectura_parcial")
        self.assertIn("offset 500", r["nota"])
        self.assertFalse(guardar.called or escribir.called)

    def test_si_falla_en_la_primera_pagina_es_odoo_mudo_como_antes(self):
        with self._odoo(cortar_en=0):
            self.assertEqual(odoo.listar_catalogo(estricto=True), [])
        r, guardar, escribir = self._pasada(0)
        self.assertEqual(r["estado"], "odoo_sin_respuesta")
        self.assertFalse(guardar.called or escribir.called)


if __name__ == "__main__":
    unittest.main()
