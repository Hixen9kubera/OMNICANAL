"""Pruebas del RECUPERADOR del fan-out (`fanout_recuperar.revisar` y su job).

── QUÉ FIJAN ────────────────────────────────────────────
La cola del fan-out vive en memoria; el 29-sep un despliegue a media cola tiró 69
de 72 cambios y nadie los volvió a repartir. El recuperador busca en la bitácora
los cambios sin reparto y los reencola. Tiene que ser aburrido:

  1. EXISTE SOLO CON SUS DOS BANDERAS (la suya y la del fan-out), nace apagado y
     su trabajo va en `asyncio.to_thread` (regla 11: lee la base).
  2. NO HACE NADA si el fan-out está apagado o si la bitácora no se escribe en
     kubera (sin ella, todo cambio parecería sin repartir).
  3. REENCOLA UNA SOLA VEZ cada cambio (sku, ts): si su evento nunca llega a la
     bitácora, no entra en bucle. Un cambio NUEVO del mismo SKU sí vuelve a entrar.
  4. SE SALTA lo que ya está en la cola.
  5. AL ARRANCAR la gracia baja a 3 min (la cola está recién vacía).
  6. LO QUE EL FAN-OUT VIO Y NO REPARTIÓ (`sin_numero`, `lectura_fallida`) no se da
     por repartido ni se reencola en cada vuelta: `sin_numero` sólo se cuenta;
     `lectura_fallida` vuelve UNA vez si no está en la cola. Los dos se cuentan aparte
     y, tras 3 vueltas seguidas, se avisan (y se avisa cuando se vacían).
  7. UNA MARCA `sin_numero` DE LA OTRA FUENTE no vale (D4): la de Woo, leyendo hoy el
     libro (o al revés), se reencola como cualquier cambio: con la fuente de hoy ese
     SKU sí tiene número (con el libro, 0 si no lo trae).
  8. LA LECTURA FALLIDA VARADA (más vieja que la ventana de `candidatos`) no se pierde:
     se cuenta y, fuera de la cola, vuelve una vez; el «ya no quedan» no se anuncia con
     SKUs esperando reintento ni en una vuelta ciega. (`Varadas`)

── NO SE TOCA LA BASE ───────────────────────────────────
`candidatos` va suplantado y `encolar_varios` también. Los SKUs son INVENTADOS:
el repo es público.

    cd backend && python -m unittest tests.test_fanout_recuperar -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import fanout_recuperar as R  # noqa: E402
from services import fanout_stock as F  # noqa: E402

AJUSTES = {"fanout_recuperar_enabled": True, "fanout_recuperar_min": 10, "fanout_recuperar_horas": 6,
           "fanout_recuperar_gracia_min": 15, "fanout_recuperar_tope": 300,
           "supabase_write_fanout_log": True}
HACE = datetime.now(timezone.utc) - timedelta(minutes=40)


def _fila(sku: str, ts: datetime = HACE) -> dict:
    return {"sku": sku, "ts": ts}


class _Recuperador(unittest.TestCase):
    """El recuperador aislado: candidatos, cola y avisos falsos."""

    def setUp(self):
        R._ya.clear()
        R._ultimo.clear()
        self.encolados: list[tuple[list[str], str]] = []
        self.parches = [
            mock.patch.multiple(R.settings, **AJUSTES),
            mock.patch.object(F, "habilitado", return_value=True),
            mock.patch.object(F, "encolar_varios", side_effect=lambda s, motivo: self.encolados.append((list(s), motivo))),
            mock.patch.dict(F._pendientes, {}, clear=True),
            # Al arrancar también busca verificaciones de Temu a medias (test_fanout_cola).
            mock.patch.object(R, "diferidas_recientes", return_value=[]),
            # Qué candidatos el fan-out SÍ vio y no repartió (clase `SinRepartir`): ninguno.
            mock.patch.object(R, "sin_repartir", return_value={}),
            # Lecturas fallidas más viejas que la ventana (clase `Varadas`): ninguna.
            mock.patch.object(R, "lecturas_varadas", return_value=[]),
            mock.patch.object(R, "_en_reintento", return_value=0),
            mock.patch.dict(R._rachas, {k: 0 for k in R._rachas}),
            mock.patch.dict(R._avisado, {k: None for k in R._avisado}),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in reversed(self.parches):
            p.stop()

    def _correr(self, filas, **kw):
        with mock.patch.object(R, "candidatos", return_value=filas) as cand:
            r = R.revisar(**kw)
        return r, cand


class Vuelta(_Recuperador):
    def test_reencola_lo_que_nunca_se_repartio_con_su_motivo(self):
        r, _ = self._correr([_fila("SKU-UNO"), _fila("SKU-DOS")])
        self.assertTrue(r["ok"])
        self.assertEqual(self.encolados, [(["SKU-UNO", "SKU-DOS"], R.MOTIVO)])
        self.assertEqual((r["candidatos"], r["encolados"]), (2, 2))

    def test_el_mismo_cambio_no_se_reencola_dos_veces(self):
        self._correr([_fila("SKU-UNO")])
        r, _ = self._correr([_fila("SKU-UNO")])
        self.assertEqual(r["encolados"], 0, "si su evento no llegó a la bitácora, no entra en bucle")
        self.assertEqual(len(self.encolados), 1)

    def test_un_cambio_nuevo_del_mismo_sku_si_vuelve_a_entrar(self):
        self._correr([_fila("SKU-UNO")])
        r, _ = self._correr([_fila("SKU-UNO", HACE + timedelta(minutes=20))])
        self.assertEqual(r["encolados"], 1)

    def test_se_salta_lo_que_ya_esta_en_la_cola(self):
        F._pendientes["SKU-UNO"] = {"listo_en": 0, "motivo": "venta"}
        r, _ = self._correr([_fila("SKU-UNO"), _fila("SKU-DOS")])
        self.assertEqual(self.encolados, [(["SKU-DOS"], R.MOTIVO)])
        self.assertEqual(r["encolados"], 1)

    def test_sin_candidatos_no_encola_nada(self):
        r, _ = self._correr([])
        self.assertTrue(r["ok"])
        self.assertEqual(self.encolados, [])

    def test_gracia_normal_y_de_arranque(self):
        _, cand = self._correr([])
        self.assertEqual(cand.call_args.args, (6, 15, 300))
        _, cand = self._correr([], arranque=True)
        self.assertEqual(cand.call_args.args, (6, R.GRACIA_ARRANQUE_MIN, 300),
                         "al arrancar la cola está vacía: recupera hasta lo de hace 3 min")

    def test_con_la_bandera_apagada_no_hace_nada(self):
        with mock.patch.object(R.settings, "fanout_recuperar_enabled", False):
            r, cand = self._correr([_fila("SKU-UNO")])
        self.assertFalse(r["ok"])
        cand.assert_not_called()
        self.assertEqual(self.encolados, [])

    def test_con_el_fanout_apagado_no_hace_nada(self):
        with mock.patch.object(F, "habilitado", return_value=False):
            r, cand = self._correr([_fila("SKU-UNO")])
        self.assertFalse(r["ok"])
        cand.assert_not_called()

    def test_sin_bitacora_en_kubera_no_hace_nada(self):
        with mock.patch.object(R.settings, "supabase_write_fanout_log", False):
            r, cand = self._correr([_fila("SKU-UNO")])
        self.assertFalse(r["ok"])
        self.assertIn("bitácora", r["motivo"])
        cand.assert_not_called()

    def test_estado_dice_la_ultima_vuelta(self):
        self._correr([_fila("SKU-UNO")])
        est = R.estado()
        self.assertTrue(est["habilitado"])
        self.assertEqual(est["ultima_vuelta"]["encolados"], 1)
        self.assertEqual(est["ultima_vuelta"]["muestra"], ["SKU-UNO"])


class _SchedFalso:
    """AsyncIOScheduler de mentira: apunta los add_job y no arranca nada."""

    def __init__(self, *a, **k):
        self.jobs: list[tuple[tuple, dict]] = []

    def add_job(self, *a, **k):
        self.jobs.append((a, k))

    def start(self):
        pass


class JobDelScheduler(unittest.TestCase):
    def _jobs(self, recuperar: bool, fanout: bool = True, **ajustes):
        from services import scheduler as sch
        ajustes = {"fanout_recuperar_enabled": recuperar, "fanout_enabled": fanout,
                   "fanout_recuperar_min": 10, **ajustes}
        viejo = sch._scheduler
        sch._scheduler = None
        try:
            with mock.patch.object(sch, "AsyncIOScheduler", _SchedFalso), \
                    mock.patch.multiple(sch.settings, **ajustes):
                sch.iniciar()
                return {k.get("id"): (a, k) for a, k in sch._scheduler.jobs}
        finally:
            sch._scheduler = viejo

    def test_solo_con_sus_dos_banderas(self):
        self.assertNotIn("fanout_recuperar", self._jobs(False))
        self.assertNotIn("fanout_recuperar", self._jobs(True, fanout=False))
        jobs = self._jobs(True)
        a, k = jobs["fanout_recuperar"]
        self.assertEqual(a[1], "interval")
        self.assertEqual(k["minutes"], 10)
        self.assertEqual((k["max_instances"], k["coalesce"]), (1, True))
        self.assertIsNotNone(k["next_run_time"].tzinfo, "con zona: el scheduler va en UTC")
        a, k = jobs["fanout_recuperar_arranque"]
        self.assertEqual(a[1], "date")
        self.assertEqual(k["kwargs"], {"arranque": True})
        gracia = (k["run_date"] - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(100 < gracia <= 120, gracia)

    def test_el_intervalo_tiene_piso(self):
        _, k = self._jobs(True, fanout_recuperar_min=0)["fanout_recuperar"]
        self.assertEqual(k["minutes"], 5, "un 0 no vuelve el barrido un bucle")

    def test_la_bandera_nace_apagada(self):
        campos = type(R.settings).model_fields
        self.assertIs(campos["fanout_recuperar_enabled"].default, False)
        self.assertEqual(campos["fanout_recuperar_horas"].default, 6)
        self.assertEqual(campos["fanout_recuperar_gracia_min"].default, 15)

    def test_el_trabajo_va_en_un_hilo(self):
        a, _ = self._jobs(True)["fanout_recuperar"]
        job = a[0]
        from services import scheduler as sch
        llamado = mock.AsyncMock()
        with mock.patch.object(sch.asyncio, "to_thread", llamado):
            asyncio.run(job(arranque=True))
        llamado.assert_awaited_once()
        self.assertIs(llamado.await_args.args[0], R.revisar)
        self.assertEqual(llamado.await_args.args[1], True)


class SinRepartir(_Recuperador):
    """Candidatos que el fan-out VIO y no repartió: sin número o con la lectura fallida."""

    def setUp(self):
        super().setUp()
        self.avisos: list[tuple] = []
        from services import alertas
        p = mock.patch.object(alertas, "avisar_estado",
                              side_effect=lambda *a, **k: self.avisos.append((a, k)) or True)
        p.start()
        self.addCleanup(p.stop)

    def _correr_con(self, filas, vistos, **kw):
        with mock.patch.object(R, "sin_repartir", return_value=vistos):
            return self._correr(filas, **kw)[0]

    def _tipos(self):
        return [(a[0], a[1]) for a, _k in self.avisos]

    def test_sin_numero_se_cuenta_y_no_se_reencola_nunca(self):
        filas = [_fila("SKU-UNO"), _fila("SKU-DOS")]
        for _ in range(2):
            r = self._correr_con(filas, {"SKU-UNO": "sin_numero"})
            self.assertEqual(r["sin_numero"], 1, "cada vuelta lo vuelve a contar: sigue sin repartir")
            self.assertEqual(r["sin_repartir"]["sin_numero"]["texto"], "sin repartir por falta de número: 1")
            self.assertEqual(r["sin_repartir"]["sin_numero"]["muestra"], ["SKU-UNO"])
        self.assertEqual(self.encolados, [(["SKU-DOS"], R.MOTIVO)], "sólo el que nunca llegó al fan-out")

    def test_lectura_fallida_fuera_de_la_cola_vuelve_una_vez(self):
        filas = [_fila("SKU-UNO")]
        with mock.patch.object(R.settings, "fanout_fuente", "kubera"):
            r = self._correr_con(filas, {"SKU-UNO": "lectura_fallida"})
        self.assertEqual(self.encolados, [(["SKU-UNO"], R.MOTIVO_LECTURA)])
        self.assertEqual((r["lectura_fallida"], r["reencolados_lectura"], r["encolados"]), (1, 1, 1))
        self.assertEqual(r["sin_repartir"]["lectura_fallida"]["texto"], "lectura del libro fallida: 1")
        with mock.patch.object(R.settings, "fanout_fuente", "woo"):
            r = self._correr_con(filas, {"SKU-UNO": "lectura_fallida"})
        self.assertEqual(len(self.encolados), 1, "no en cada vuelta")
        self.assertEqual((r["lectura_fallida"], r["encolados"]), (1, 0), "pero se sigue contando")
        # Con Woo también hay lecturas fallidas (`fanout_stock.LecturaFallida`): no son «del libro».
        self.assertEqual(r["sin_repartir"]["lectura_fallida"]["texto"], "lectura del número fallida: 1")
        self.assertTrue(R.MOTIVO_LECTURA.startswith("recuperado"), "la matriz lo rotula «recuperado»")

    def test_lectura_fallida_con_su_reintento_en_la_cola_no_se_toca(self):
        F._pendientes["SKU-UNO"] = {"listo_en": 9e18, "motivo": "venta"}
        r = self._correr_con([_fila("SKU-UNO")], {"SKU-UNO": "lectura_fallida"})
        self.assertEqual(self.encolados, [])
        self.assertEqual(r["lectura_fallida"], 1)

    def test_avisa_a_la_tercera_vuelta_seguida_y_cuando_se_vacia(self):
        filas = [_fila("SKU-UNO")]
        self._correr_con(filas, {"SKU-UNO": "sin_numero"})
        self._correr_con(filas, {"SKU-UNO": "sin_numero"})
        self.assertNotIn(("fanout_recuperar_sin_numero", "sin_repartir"), self._tipos())
        self._correr_con(filas, {"SKU-UNO": "sin_numero"})
        self.assertIn(("fanout_recuperar_sin_numero", "sin_repartir"), self._tipos())
        texto = next(a[2] for a, _k in self.avisos if a[1] == "sin_repartir")
        self.assertIn("SKU-UNO", texto)
        self.assertIn("sin repartir por falta de número", texto)
        self.avisos.clear()
        self._correr_con([], {})
        self.assertIn(("fanout_recuperar_sin_numero", "ok"), self._tipos())
        self.avisos.clear()
        self._correr_con([], {})
        self.assertEqual(self.avisos, [], "con todo en orden no vuelve a preguntar")

    def test_una_vuelta_limpia_corta_la_racha(self):
        filas = [_fila("SKU-UNO")]
        self._correr_con(filas, {"SKU-UNO": "lectura_fallida"})
        self._correr_con(filas, {"SKU-UNO": "lectura_fallida"})
        self._correr_con([], {})
        self._correr_con(filas, {"SKU-UNO": "lectura_fallida"})
        self.assertNotIn(("fanout_recuperar_lectura_fallida", "sin_repartir"), self._tipos())
        self.assertEqual(R.estado()["sin_repartir"]["lectura_fallida"]["vueltas_seguidas"], 1)

    def test_si_no_se_puede_leer_la_marca_se_reencola_como_siempre(self):
        with mock.patch.object(R, "sin_repartir", side_effect=RuntimeError("kubera caída")):
            r, _ = self._correr([_fila("SKU-UNO")])
        self.assertTrue(r["ok"])
        self.assertEqual(self.encolados, [(["SKU-UNO"], R.MOTIVO)])

    def test_el_estado_lo_dice(self):
        self._correr_con([_fila("SKU-UNO"), _fila("SKU-DOS")], {"SKU-UNO": "sin_numero", "SKU-DOS": "lectura_fallida"})
        est = R.estado()
        self.assertEqual(est["sin_repartir"]["sin_numero"]["n"], 1)
        self.assertEqual(est["sin_repartir"]["lectura_fallida"]["n"], 1)
        self.assertEqual(est["aviso_tras_vueltas"], 3)


class Varadas(SinRepartir):
    """La lectura fallida que dura más que `FANOUT_RECUPERAR_HORAS` (el libro caído 7 h y
    un despliegue a la hora 7): `candidatos` ya no la ve, `lecturas_varadas` sí."""

    VIEJA = datetime.now(timezone.utc) - timedelta(hours=7)

    def _vuelta(self, filas, varadas, vistos=None):
        with mock.patch.object(R, "lecturas_varadas", return_value=varadas):
            return self._correr_con(filas, vistos or {})

    def test_fuera_de_la_cola_vuelve_una_vez_y_se_sigue_contando(self):
        varadas = [_fila("SKU-VIEJO", self.VIEJA)]
        r = self._vuelta([], varadas)
        self.assertEqual(self.encolados, [(["SKU-VIEJO"], R.MOTIVO_LECTURA)])
        self.assertEqual((r["lectura_fallida"], r["reencolados_lectura"], r["varadas"]), (1, 1, 1))
        r = self._vuelta([], varadas)
        self.assertEqual(len(self.encolados), 1, "una vez por (sku, ts)")
        self.assertEqual(r["lectura_fallida"], 1, "pero sigue sin repartir")

    def test_con_su_reintento_en_la_cola_sólo_se_cuenta(self):
        F._pendientes["SKU-VIEJO"] = {"listo_en": 9e18, "motivo": "venta"}
        r = self._vuelta([], [_fila("SKU-VIEJO", self.VIEJA)])
        self.assertEqual(self.encolados, [])
        self.assertEqual(r["lectura_fallida"], 1)

    def test_no_se_cuenta_dos_veces_con_candidatos(self):
        r = self._vuelta([_fila("SKU-UNO")], [_fila("SKU-UNO", self.VIEJA)], {"SKU-UNO": "lectura_fallida"})
        self.assertEqual((r["lectura_fallida"], self.encolados), (1, [(["SKU-UNO"], R.MOTIVO_LECTURA)]))

    def test_sin_la_ventana_no_se_anuncia_un_resuelto_falso(self):
        varadas = [_fila("SKU-VIEJO", self.VIEJA)]
        for _ in range(3):
            self._vuelta([], varadas)
        self.assertIn(("fanout_recuperar_lectura_fallida", "sin_repartir"), self._tipos())
        self.avisos.clear()
        # El cambio salió de la ventana de 6 h, pero la última fila sigue siendo la falla.
        self._vuelta([], varadas)
        self.assertNotIn(("fanout_recuperar_lectura_fallida", "ok"), self._tipos())

    def test_con_skus_esperando_reintento_no_dice_que_ya_no_quedan(self):
        varadas = [_fila("SKU-VIEJO", self.VIEJA)]
        for _ in range(3):
            self._vuelta([], varadas)
        self.avisos.clear()
        with mock.patch.object(R, "_en_reintento", return_value=2):
            self._vuelta([], [])
        self.assertEqual(self.avisos, [])
        self._vuelta([], [])
        self.assertIn(("fanout_recuperar_lectura_fallida", "ok"), self._tipos())

    def test_una_vuelta_ciega_ni_cuenta_ni_anuncia(self):
        for _ in range(3):
            self._vuelta([], [_fila("SKU-VIEJO", self.VIEJA)])
        self.avisos.clear()
        with mock.patch.object(R, "sin_repartir", side_effect=RuntimeError("kubera caída")):
            r, _ = self._correr([])
        self.assertTrue(r["ciego"])
        self.assertEqual(self.avisos, [])
        with mock.patch.object(R, "lecturas_varadas", side_effect=RuntimeError("kubera caída")):
            r, _ = self._correr([])
        self.assertTrue(r["ciego"])
        self.assertEqual(self.avisos, [])


class ConsultaVaradas(unittest.TestCase):
    def test_la_consulta(self):
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", return_value=[]) as fa:
            R.lecturas_varadas(7, 300)
        sql, params = fa.call_args.args
        self.assertEqual((params["lf"], params["d"], params["n"]), ("lectura_fallida", 7, 300))
        self.assertEqual(params["a"], ["escribir", "omitir", "sin_cambio", "sin_destinos", "sin_numero"])
        self.assertIn("not exists", sql)
        self.assertIn("e.ts > lf.ts", sql)
        self.assertIn("coalesce(e.canal, '') <> 'woocommerce'", sql)


class ConsultaSinRepartir(unittest.TestCase):
    """`sin_repartir`: la última fila sin_numero / lectura_fallida POSTERIOR al cambio."""

    def _con(self, filas_bd):
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", return_value=filas_bd) as fa:
            return fa

    def test_clasifica_por_la_ultima_fila_posterior_al_cambio(self):
        from services import supabase_db as sdb
        cambio = HACE
        bd = [{"sku": "SKU-UNO", "accion": "sin_numero", "ts": cambio + timedelta(seconds=6)},
              {"sku": "SKU-DOS", "accion": "lectura_fallida", "ts": cambio - timedelta(minutes=5)},
              {"sku": "SKU-TRES", "accion": "lectura_fallida", "ts": cambio + timedelta(minutes=1)}]
        filas = [_fila("sku-uno", cambio), _fila("SKU-DOS", cambio), _fila("SKU-TRES", cambio), _fila("SKU-4", cambio)]
        with mock.patch.object(sdb, "fetch_all", return_value=bd) as fa:
            r = R.sin_repartir(filas, 6)
        self.assertEqual(r, {"sku-uno": "sin_numero", "SKU-TRES": "lectura_fallida"},
                         "la de SKU-DOS es de ANTES del cambio: ese cambio nunca llegó al fan-out")
        sql, params = fa.call_args.args
        self.assertEqual(params["a"], ["sin_numero", "lectura_fallida"])
        self.assertEqual(params["s"], ["SKU-4", "SKU-DOS", "SKU-TRES", "SKU-UNO"])
        self.assertEqual(params["h"], 6)
        self.assertIn("distinct on (upper(sku::text))", sql)
        self.assertIn("ts desc, id desc", sql)

    def test_sin_candidatos_no_consulta(self):
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", side_effect=AssertionError("consultó")):
            self.assertEqual(R.sin_repartir([], 6), {})

    def _marcas(self, fuente: str) -> tuple[dict, str]:
        """Cuatro SKUs con su última marca POSTERIOR al cambio, leyendo `fuente`."""
        from services import supabase_db as sdb
        despues = HACE + timedelta(seconds=6)
        bd = [{"sku": "SKU-WOO", "accion": "sin_numero", "ts": despues, "resultado": F.SIN_NUMERO_WOO},
              {"sku": "SKU-LIB", "accion": "sin_numero", "ts": despues, "resultado": F.SIN_NUMERO_LIBRO},
              {"sku": "SKU-RARO", "accion": "sin_numero", "ts": despues, "resultado": "otro texto"},
              {"sku": "SKU-LEC", "accion": "lectura_fallida", "ts": despues,
               "resultado": "lectura del libro fallida (reintento en 60 s): caída"}]
        filas = [_fila(s) for s in ("SKU-WOO", "SKU-LIB", "SKU-RARO", "SKU-LEC")]
        with mock.patch.object(R.settings, "fanout_fuente", fuente), \
                mock.patch.object(sdb, "fetch_all", return_value=bd) as fa:
            return R.sin_repartir(filas, 6), fa.call_args.args[0]

    def test_leyendo_el_libro_la_marca_de_woo_no_vale(self):
        # D4: con el libro ese SKU tiene número (0 si no lo trae): reencolarlo SÍ lo cambia.
        r, sql = self._marcas("kubera")
        self.assertEqual(r, {"SKU-LIB": "sin_numero", "SKU-RARO": "sin_numero", "SKU-LEC": "lectura_fallida"})
        self.assertIn("resultado", sql, "la fuente de la marca se sabe por su texto")

    def test_leyendo_woo_la_marca_del_libro_no_vale(self):
        r, _ = self._marcas("woo")
        self.assertEqual(r, {"SKU-WOO": "sin_numero", "SKU-RARO": "sin_numero", "SKU-LEC": "lectura_fallida"})

    def test_con_una_errata_cuenta_como_el_libro(self):
        # `lee_libro` dice «sí» con una errata: el fan-out se niega (lectura fallida) en vez de
        # leer Woo, así que la marca de Woo tampoco vale.
        r, _ = self._marcas("kubra")
        self.assertNotIn("SKU-WOO", r)


class MarcaDeOtraFuenteEnLaVuelta(_Recuperador):
    """La vuelta completa con la consulta real de `sin_repartir` (la base, falsa)."""

    def test_la_marca_de_woo_se_reencola_una_vez_al_leer_el_libro(self):
        from services import alertas
        from services import supabase_db as sdb
        bd = [{"sku": "SKU-WOO", "accion": "sin_numero", "ts": HACE + timedelta(seconds=6),
               "resultado": F.SIN_NUMERO_WOO}]
        with mock.patch.object(R, "sin_repartir", side_effect=_SIN_REPARTIR_REAL), \
                mock.patch.object(R.settings, "fanout_fuente", "kubera"), \
                mock.patch.object(alertas, "avisar_estado", return_value=True), \
                mock.patch.object(sdb, "fetch_all", return_value=bd):
            r, _ = self._correr([_fila("SKU-WOO")])
            self.assertEqual(self.encolados, [(["SKU-WOO"], R.MOTIVO)])
            self.assertEqual(r["sin_numero"], 0, "no se cuenta como «sin repartir por falta de número»")
            r, _ = self._correr([_fila("SKU-WOO")])
        self.assertEqual(len(self.encolados), 1, "una vez por (sku, ts), como cualquier cambio")


_SIN_REPARTIR_REAL = R.sin_repartir


if __name__ == "__main__":
    unittest.main()
