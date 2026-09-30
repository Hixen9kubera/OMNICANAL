"""Pruebas de la COLA del juez de rivales (`competencia_juez.drenar_cola` y su
job en `scheduler.py`).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
Sin la cola, en producción casi nada se juzgaría: el gancho solo corre tras un
«Medir» a mano y el script por lotes tiene candado de sandbox. La cola es un job
que cada N minutos toma lo pendiente, y por eso tiene que ser aburrida:

  1. EXISTE SOLO CON LA BANDERA, y su trabajo va en `asyncio.to_thread`: lee la
     base y habla con el LLM, y nada de eso puede detener el loop (regla 11).
  2. NO HACE NADA sin la tabla de veredictos ni sin saldo en la bolsa de 24 h
     (la misma del gancho, el botón y la mejora), y lo dice UNA vez por vuelta.
  3. RESPETA SU TAMAÑO: hasta `max_skus` SKUs, 2 hilos y un techo por vuelta.
  4. NO SE ATASCA: `skus_con_pendientes` ordena por SKU, y 40 SKUs tercos al
     principio se volverían a pagar en cada vuelta sin que lo de atrás avanzara.
     Lo intentado que quedó con pendientes se enfría 24 h.
  5. SE DETIENE Y LO DICE (tope, proveedor caído, plazo); la siguiente vuelta
     vuelve a intentar, y lo que no alcanzó a intentar NO se enfría.
  6. EL TOPE ES DE TRABAJO NUEVO, y dice cuánto puede pasarse: lo que otra
     corrida del proceso ya pagó y aún no registra cuenta en la bolsa, y lo que
     está en vuelo (hasta 2 SKUs, con todos sus trozos) termina.

── NO SE LLAMA A LA IA NI A LA BASE ────────────────────────────────────────────
`ia_json.completar_json` y `supabase_db` van suplantados (y `_post_con_plazo`,
por si algo se escapara: suplantar `httpx.post` NO corta la red de DeepSeek).
Los SKUs y títulos son INVENTADOS: el repo es público.

    cd backend && python -m unittest tests.test_competencia_juez_cola -v
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import sys
import threading
import time
import types
import unittest
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import competencia_juez as J  # noqa: E402

MODELO = "deepseek-flash"
NUESTRO = "Lámpara de escritorio LED plegable"
LOG_JUEZ = "omnicanal.competencia.juez"


def skus(n: int) -> list[str]:
    return [f"SKU-{i:02d}" for i in range(1, n + 1)]


# ── El LLM, suplantado ───────────────────────────────────────────────────────

def respuesta(n: int, clase: str = "mismo", usd: float = 0.001) -> dict:
    return {"ok": True, "texto": "", "cortado": False, "proveedor": "deepseek", "modelo": MODELO,
            "datos": {"nuestro": "lámpara de escritorio", "unidades_nuestras": 1,
                      "veredictos": [{"i": i, "clase": clase, "unidades": 1, "razon": "igual"}
                                     for i in range(1, n + 1)]},
            "uso": {"entrada": 100, "cache": 0, "salida": 50}, "usd": usd}


def fallo() -> dict:
    return {"ok": False, "motivo": "DeepSeek no respondió (HTTP 503: upstream).",
            "proveedor": "deepseek", "modelo": MODELO, "texto": "", "cortado": False,
            "uso": {}, "usd": 0.0}


class LLM:
    """Contesta bien, salvo a los SKUs `tercos`: a esos les responde con una
    clase que no existe (el proveedor SÍ contestó y cobró, pero nada pasa la
    validación). Apunta a qué SKU juzgó cada llamada."""

    def __init__(self, tercos=(), usd=0.001, caido=False, antes=None):
        self.tercos = set(tercos)
        self.usd = usd
        self.caido = caido
        self.antes = antes           # algo que correr antes de contestar (un reloj)
        self.llamadas: list[str] = []

    def __call__(self, system, user, **kw):
        sku = re.search(r"^titulo: .* (SKU-\d+)$", user, re.MULTILINE).group(1)
        self.llamadas.append(sku)
        if self.antes:
            self.antes()
        if self.caido:
            return fallo()
        n = int(re.search(r"RIVALES \((\d+)\)", user).group(1))
        return respuesta(n, "mismo_producto" if sku in self.tercos else "mismo", self.usd)


# ── La base, suplantada CON memoria ──────────────────────────────────────────

def fila(sku: str, k: int) -> dict:
    """Una fila CRUDA de `_SQL_FILAS`: un rival sin veredicto. El título nuestro
    nombra al SKU para que el LLM suplantado sepa a quién juzga."""
    return {"sku": sku, "termino_id": 11, "externo_id": f"RIVAL-{sku}-{k}", "posicion": k,
            "titulo": f"Lámpara de escritorio modelo {k}", "precio": 250.0, "es_nuestro": False,
            "capturado_en": dt.datetime(2026, 9, 30, 12, 0),
            "titulo_nuestro": f"{NUESTRO} {sku}", "categoria_nombre": "Iluminación",
            "clase": None, "motivo": None, "unidades_nuestras": None, "unidades_rival": None,
            "juzgado_en": None, "modelo": None, "version_prompt": None, "vigente": False,
            "unidades_sku": None}


class BaseViva:
    """`supabase_db` de mentira que RECUERDA: lo guardado deja de estar pendiente
    y la bitácora alimenta a `gastado_24h`. Así varias vueltas de la cola se ven
    como se verían en producción."""

    def __init__(self, lista, por_sku=2, tabla=True, sin_titulo=()):
        self.filas = [fila(s, k) for s in lista for k in range(1, por_sku + 1)]
        # Título nuestro '' (no NULL): `skus_con_pendientes` de aquí los deja
        # pasar igual, que es lo que haría un SQL que discrepara de `filas()`.
        for f in self.filas:
            if f["sku"] in sin_titulo:
                f["titulo_nuestro"] = ""
        self.juzgados: dict[tuple[str, str], str] = {}
        self.bitacora: list[dict] = []
        self.tabla = tabla
        self._lock = threading.Lock()

    def pendientes(self) -> list[str]:
        return sorted({f["sku"] for f in self.filas
                       if (f["sku"], f["externo_id"]) not in self.juzgados})

    def fetch_scalar(self, sql, params=None):
        if "to_regclass" in sql:
            return self.tabla
        if "ops.process_log" in sql:
            return sum(float(d.get("usd") or 0) for d in self.bitacora)
        raise AssertionError(f"consulta inesperada: {sql}")

    def fetch_all(self, sql, params=None):
        if "count(*)" in sql:                       # skus_con_pendientes
            return [{"sku": s, "n": 1} for s in self.pendientes()]
        out = []
        for f in self.filas:                        # filas()
            if f["sku"] not in params["skus"]:
                continue
            g = dict(f)
            clase = self.juzgados.get((f["sku"], f["externo_id"]))
            if clase:
                g.update(vigente=True, clase=clase, version_prompt=J.VERSION_PROMPT)
            out.append(g)
        return out

    @contextmanager
    def get_cursor(self):
        tx: list[tuple] = []
        cur = mock.Mock()
        cur.execute.side_effect = lambda sql, p=None: tx.append((sql, p))
        yield cur
        with self._lock:
            for sql, p in tx:
                if "insert into" in sql:
                    self.juzgados[(p[0], p[2])] = p[4]

    def execute(self, sql, params=None):
        self.bitacora.append(json.loads(params[3]))
        return 1

    @contextmanager
    def puesta(self, llm):
        with mock.patch.multiple(J.supabase_db, fetch_all=self.fetch_all,
                                 fetch_scalar=self.fetch_scalar, get_cursor=self.get_cursor,
                                 execute=self.execute), \
                mock.patch.object(J.ia_json, "completar_json", llm):
            yield


class ConReloj(unittest.TestCase):
    """Enfriamiento en blanco y un reloj que la prueba mueve a mano. Además
    `_post_con_plazo` truena: si algo llegara a la red de DeepSeek, se nota."""

    def setUp(self):
        J._enfriando.clear()
        self.addCleanup(J._enfriando.clear)
        self.reloj = {"t": 1_000.0}
        for p in (mock.patch.object(J, "time", types.SimpleNamespace(
                      monotonic=lambda: self.reloj["t"])),
                  mock.patch.object(J.ia_json, "_post_con_plazo",
                                    side_effect=AssertionError("¡salió a la red!"))):
            p.start()
            self.addCleanup(p.stop)

    def vuelta(self, base, llm, **kw):
        kw.setdefault("max_skus", 40)
        kw.setdefault("tope_diario", 1.0)
        with base.puesta(llm), \
                mock.patch.object(J.settings, "competencia_juez_modelo", MODELO):
            return J.drenar_cola(**kw)


# ═════════════════════════════════════════════════════════════════════════════
# 1. El job del scheduler
# ═════════════════════════════════════════════════════════════════════════════

class _SchedFalso:
    """AsyncIOScheduler de mentira: apunta los add_job y no arranca nada."""

    def __init__(self, *a, **k):
        self.jobs: list[tuple[tuple, dict]] = []

    def add_job(self, *a, **k):
        self.jobs.append((a, k))

    def start(self):
        pass


class JobDelScheduler(unittest.TestCase):
    def _jobs(self, encendido: bool, **ajustes):
        """Los jobs que registra `iniciar()`. Los números van fijos (no los del
        entorno de quien corre la prueba), salvo los que la prueba cambie."""
        from services import scheduler as sch
        ajustes = {"competencia_juez_enabled": encendido, "competencia_juez_cola_min": 30,
                   "competencia_juez_cola_skus": 40, "competencia_juez_tope_diario_usd": 1.0,
                   **ajustes}
        viejo = sch._scheduler
        sch._scheduler = None
        try:
            with mock.patch.object(sch, "AsyncIOScheduler", _SchedFalso), \
                    mock.patch.multiple(sch.settings, **ajustes):
                sch.iniciar()
                return {k.get("id"): (a, k) for a, k in sch._scheduler.jobs}
        finally:
            sch._scheduler = viejo

    def test_solo_con_la_bandera(self):
        self.assertNotIn("competencia_juez_cola", self._jobs(False))
        a, k = self._jobs(True)["competencia_juez_cola"]
        self.assertEqual(a[1], "interval")
        self.assertEqual(k["minutes"], 30)
        self.assertEqual((k["max_instances"], k["coalesce"]), (1, True))
        self.assertIsNotNone(k["next_run_time"].tzinfo, "con zona: el scheduler va en UTC")

    def test_la_bandera_nace_apagada_y_los_numeros_por_omision(self):
        campos = type(J.settings).model_fields
        self.assertIs(campos["competencia_juez_enabled"].default, False)
        self.assertEqual(campos["competencia_juez_cola_min"].default, 30)
        self.assertEqual(campos["competencia_juez_cola_skus"].default, 40)

    def test_el_intervalo_sale_de_la_configuracion_con_piso(self):
        _, k = self._jobs(True, competencia_juez_cola_min=45)["competencia_juez_cola"]
        self.assertEqual(k["minutes"], 45)
        _, k = self._jobs(True, competencia_juez_cola_min=0)["competencia_juez_cola"]
        self.assertEqual(k["minutes"], 5, "un 0 no vuelve la cola un bucle")

    def test_su_gracia_de_arranque_no_la_comparte_ningun_otro_job(self):
        _, k = self._jobs(True)["competencia_juez_cola"]
        gracia = (k["next_run_time"] - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(400 < gracia <= 420, gracia)
        fuente = (BACKEND / "services" / "scheduler.py").read_text(encoding="utf-8")
        todas = Counter(int(n) * (60 if u == "minutes" else 1)
                        for u, n in re.findall(r"timedelta\((seconds|minutes)=(\d+)\)", fuente))
        self.assertEqual(todas[420], 1)

    # Números que NO son los de omisión: un 30/40/1.0 escrito a mano en el job
    # pasaría igual con los de omisión, y el acta puede bajar el tope (p. ej. a
    # 0.50 el primer día) sin que nada garantizara que la cola lo obedece.
    AJUSTES = {"competencia_juez_cola_min": 20, "competencia_juez_cola_skus": 7,
               "competencia_juez_tope_diario_usd": 0.37}

    def test_log_de_arranque_dice_cada_cuanto_y_con_que_tope(self):
        with self.assertLogs("omnicanal.scheduler", "INFO") as cm:
            self._jobs(True, **self.AJUSTES)
        linea = next(m for m in cm.output if "Cola del juez" in m)
        self.assertIn("cada 20 min", linea)
        self.assertIn("7 SKUs", linea)
        self.assertIn("0.37 USD", linea)

    def test_el_job_va_en_to_thread_y_no_detiene_el_loop(self):
        """El trabajo de verdad (base + LLM) tarda. Si el job lo llamara en el
        loop, el latido de al lado no daría un solo paso mientras tanto. Y le
        pasa lo CONFIGURADO, no números fijos."""
        a, _ = self._jobs(True, **self.AJUSTES)["competencia_juez_cola"]
        job = a[0]
        visto: dict = {}

        def lento(**kw):
            visto.update(kw, hilo=threading.get_ident())
            time.sleep(0.3)
            return {}

        async def correr():
            latidos = 0
            fin = asyncio.Event()

            async def latir():
                nonlocal latidos
                while not fin.is_set():
                    latidos += 1
                    await asyncio.sleep(0.01)

            tarea = asyncio.create_task(latir())
            await asyncio.sleep(0)
            await job()
            fin.set()
            await tarea
            return latidos

        with mock.patch.object(J, "drenar_cola", lento):
            latidos = asyncio.run(correr())
        self.assertGreaterEqual(latidos, 10, "el loop se quedó quieto: no va en to_thread")
        self.assertNotEqual(visto.pop("hilo"), threading.get_ident())
        self.assertEqual(visto, {"max_skus": 7, "tope_diario": 0.37, "plazo_s": 600.0})


# ═════════════════════════════════════════════════════════════════════════════
# 2. Cuándo NO hace nada
# ═════════════════════════════════════════════════════════════════════════════

class NoHaceNada(ConReloj):
    def _drenar(self, *, tablas=True, gastado=0.0, pendientes=(), **kw):
        juzgar = mock.Mock()
        buscar = mock.Mock(return_value=list(pendientes))
        with mock.patch.object(J, "tablas_listas", return_value=tablas), \
                mock.patch.object(J, "gastado_24h", return_value=gastado), \
                mock.patch.object(J, "skus_con_pendientes", buscar), \
                mock.patch.object(J, "juzgar_skus", juzgar):
            kw.setdefault("max_skus", 40)
            kw.setdefault("tope_diario", 1.0)
            r = J.drenar_cola(**kw)
        return r, juzgar, buscar

    def test_sin_tablas_ni_siquiera_busca_y_lo_dice_una_vez(self):
        with self.assertLogs(LOG_JUEZ, "INFO") as cm:
            r, juzgar, buscar = self._drenar(tablas=False, pendientes=["SKU-01"])
        self.assertEqual(r["motivo"], "sin tablas")
        buscar.assert_not_called()
        juzgar.assert_not_called()
        self.assertEqual(len(cm.records), 1)

    def test_sin_saldo_en_la_bolsa_de_24_h(self):
        with self.assertLogs(LOG_JUEZ, "INFO") as cm:
            r, juzgar, buscar = self._drenar(gastado=0.995, pendientes=["SKU-01"])
        self.assertEqual(r["motivo"], "tope diario")
        buscar.assert_not_called()
        juzgar.assert_not_called()
        self.assertEqual(len(cm.records), 1)
        self.assertIn("tope diario alcanzado", cm.output[0])

    def test_gastado_de_mas_tampoco(self):
        r, juzgar, _ = self._drenar(gastado=1.3, pendientes=["SKU-01"])
        self.assertEqual(r["motivo"], "tope diario")
        juzgar.assert_not_called()

    def test_sin_poder_medir_el_gasto_no_se_gasta(self):
        """`gastado_24h` devuelve infinito cuando no puede leer la bitácora."""
        with self.assertLogs(LOG_JUEZ, "INFO") as cm:
            r, juzgar, _ = self._drenar(gastado=float("inf"), pendientes=["SKU-01"])
        self.assertEqual(r["motivo"], "gasto sin medir")
        juzgar.assert_not_called()
        self.assertEqual(len(cm.records), 1)

    def test_nada_pendiente_es_silencio(self):
        """Es el estado normal tras el backfill: nada en INFO cada media hora."""
        with self.assertNoLogs(LOG_JUEZ, "INFO"):
            r, juzgar, _ = self._drenar(pendientes=[])
        self.assertEqual(r["motivo"], "nada pendiente")
        juzgar.assert_not_called()

    def test_una_falla_no_revienta_el_job(self):
        with mock.patch.object(J, "tablas_listas", return_value=True), \
                mock.patch.object(J, "gastado_24h", return_value=0.0), \
                mock.patch.object(J, "skus_con_pendientes",
                                  side_effect=RuntimeError("connection already closed")), \
                self.assertLogs(LOG_JUEZ, "WARNING") as cm:
            r = J.drenar_cola(max_skus=40, tope_diario=1.0)
        self.assertEqual(r["motivo"], "error")
        self.assertIn("connection already closed", cm.output[0])


# ═════════════════════════════════════════════════════════════════════════════
# 3. Lo que le pide a `juzgar_skus`
# ═════════════════════════════════════════════════════════════════════════════

class LoQuePide(ConReloj):
    def _drenar(self, pendientes, *, gastado=0.0, resultado=None, detenido=None, **kw):
        llamadas = []

        def juzgar(lote, **k):
            llamadas.append((list(lote), k))
            # Por omisión cada SKU del lote quedó completo; `resultado` lo cambia.
            k["resultados"].update(
                {s: {"pendientes": 1, "guardados": 1, "llamadas": 1, "motivo": None}
                 for s in lote} if resultado is None else resultado)
            return {"veredictos": 0, "sin_juzgar": 0, "usd": 0.0, "detenido": detenido}

        with mock.patch.object(J, "tablas_listas", return_value=True), \
                mock.patch.object(J, "gastado_24h", return_value=gastado), \
                mock.patch.object(J, "skus_con_pendientes", return_value=list(pendientes)), \
                mock.patch.object(J, "juzgar_skus", juzgar):
            kw.setdefault("max_skus", 40)
            kw.setdefault("tope_diario", 1.0)
            r = J.drenar_cola(**kw)
        return r, llamadas

    def test_respeta_max_skus_y_el_orden_de_la_cola(self):
        r, llamadas = self._drenar(skus(100), plazo_s=900.0)
        self.assertEqual(len(llamadas), 1, "una sola tanda por vuelta")
        lote, k = llamadas[0]
        self.assertEqual(lote, skus(40))
        self.assertEqual((k["hilos"], k["plazo_s"], k["origen"]), (2, 900.0, "cola"),
                         "2 hilos: deja 2 de los 4 turnos al gancho y al botón")
        self.assertEqual((r["pendientes_skus"], r["skus"]), (100, 40))

    def test_max_skus_distinto(self):
        _, llamadas = self._drenar(skus(100), max_skus=7)
        self.assertEqual(llamadas[0][0], skus(7))

    def test_el_presupuesto_es_lo_que_queda_con_techo_por_vuelta(self):
        _, llamadas = self._drenar(skus(3), gastado=0.97)
        self.assertAlmostEqual(llamadas[0][1]["presupuesto"].tope, 0.03)
        _, llamadas = self._drenar(skus(3), gastado=0.0)
        self.assertEqual(llamadas[0][1]["presupuesto"].tope, J.COLA_TOPE_VUELTA_USD)

    def test_que_se_enfria_y_que_no(self):
        _, llamadas = self._drenar(skus(5), resultado={
            "SKU-01": {"pendientes": 3, "guardados": 0, "llamadas": 1, "motivo": "renumeró"},
            "SKU-02": {"pendientes": 3, "guardados": 2, "llamadas": 1, "motivo": None},
            "SKU-03": {"pendientes": 3, "guardados": 3, "llamadas": 1, "motivo": None},
            "SKU-04": {"pendientes": 3, "guardados": 0, "llamadas": 0, "motivo": J.IA_OCUPADA},
            "SKU-05": {"pendientes": 30, "guardados": 12, "llamadas": 1, "motivo": J.IA_OCUPADA},
        })
        self.assertEqual(set(J._enfriando), {"SKU-01", "SKU-02"},
                         "terco y a medias sí; completo no; sin turno no es culpa del SKU")
        self.assertEqual(J._enfriando["SKU-01"], self.reloj["t"] + J.COLA_ENFRIAMIENTO_S)

    def test_lo_visitado_sin_nada_que_juzgar_tambien_se_enfria(self):
        """Sin detención la vuelta visitó todo el lote: el SKU que no volvió en
        `resultados` no tenía nada que juzgar (sin título, p. ej.). Sin
        enfriarlo, ocuparía su lugar en cada vuelta para siempre."""
        r, _ = self._drenar(skus(3), resultado={
            "SKU-01": {"pendientes": 2, "guardados": 2, "llamadas": 1, "motivo": None}})
        self.assertEqual(set(J._enfriando), {"SKU-02", "SKU-03"})
        self.assertEqual(r["enfriados"], 2)

    def test_con_detencion_lo_no_visitado_no_se_enfria(self):
        """Con detención (plazo, tope, proveedor) el que falta en `resultados`
        ni se intentó: la siguiente vuelta debe empezar por él."""
        for motivo in ("plazo", "tope de gasto", "5 fallos seguidos del proveedor",
                       J.IA_OCUPADA):
            J._enfriando.clear()
            self._drenar(skus(3), detenido=motivo, resultado={
                "SKU-01": {"pendientes": 2, "guardados": 2, "llamadas": 1, "motivo": None}})
            self.assertEqual(J._enfriando, {}, motivo)

    def test_lo_enfriado_se_salta_y_vence_solo(self):
        J._enfriando.update({"SKU-01": self.reloj["t"] + 10, "SKU-02": self.reloj["t"] - 1})
        r, llamadas = self._drenar(skus(4))
        self.assertEqual(llamadas[0][0], ["SKU-02", "SKU-03", "SKU-04"])
        self.assertEqual(r["en_enfriamiento"], 1)
        self.assertNotIn("SKU-02", J._enfriando, "lo vencido se purga")

    def test_todo_en_enfriamiento_no_llama_a_nadie(self):
        J._enfriando.update({s: self.reloj["t"] + 60 for s in skus(3)})
        with self.assertNoLogs(LOG_JUEZ, "INFO"):
            r, llamadas = self._drenar(skus(3))
        self.assertEqual(llamadas, [])
        self.assertEqual((r["motivo"], r["en_enfriamiento"]), ("todo en enfriamiento", 3))


# ═════════════════════════════════════════════════════════════════════════════
# 4. Anti-atasco, de punta a punta (juez real, base y LLM suplantados)
# ═════════════════════════════════════════════════════════════════════════════

class AntiAtasco(ConReloj):
    def test_cuarenta_tercos_al_principio_no_detienen_la_cola(self):
        """50 SKUs; los primeros 40 el modelo nunca los contesta bien. Sin el
        enfriamiento la segunda vuelta volvería a pagar esos 40 y los otros 10 no
        se juzgarían jamás.

        El reloj avanza 30 min entre vueltas, como en producción: con el reloj
        quieto, un enfriamiento de 60 s pasaba esta prueba y en producción
        repagaba a los 40 en cada vuelta."""
        self.assertGreaterEqual(J.COLA_ENFRIAMIENTO_S, 24 * 3600,
                                "menos de un día: un terco se repagaría varias veces al día")
        base = BaseViva(skus(50))
        llm = LLM(tercos=skus(40))
        t0 = self.reloj["t"]

        r1 = self.vuelta(base, llm)
        self.assertEqual(sorted(llm.llamadas), skus(40))
        self.assertEqual((r1["veredictos"], r1["enfriados"], r1["detenido"]), (0, 40, None),
                         "respuestas inválidas no son «proveedor caído»: no se detiene")

        llm.llamadas.clear()
        self.reloj["t"] += 30 * 60
        r2 = self.vuelta(base, llm)
        self.assertEqual(sorted(llm.llamadas), skus(50)[40:],
                         "la segunda vuelta NO repaga a los tercos: juzga a los otros 10")
        self.assertEqual((r2["veredictos"], r2["en_enfriamiento"]), (20, 40))
        self.assertEqual(base.pendientes(), skus(40))

        llm.llamadas.clear()
        self.reloj["t"] += 30 * 60
        r3 = self.vuelta(base, llm)
        self.assertEqual(llm.llamadas, [], "solo quedan tercos enfriándose: nadie paga")
        self.assertEqual(r3["motivo"], "todo en enfriamiento")

        self.reloj["t"] = t0 + J.COLA_ENFRIAMIENTO_S + 1
        llm.llamadas.clear()
        self.vuelta(base, llm)
        self.assertEqual(sorted(llm.llamadas), skus(40), "a las 24 h se reintentan")

    def test_cuarenta_sin_titulo_al_principio_no_detienen_la_cola(self):
        """Título nuestro '' en los primeros 40: `juzgar_skus` los salta SIN
        llamar al LLM, así que no entran a `resultados`. Sin enfriarlos, cada
        vuelta pediría los mismos 40 gratis y los otros 10 no llegarían nunca
        (y el log diría «40 SKUs → 0 veredictos» cada media hora)."""
        base = BaseViva(skus(50), sin_titulo=skus(40))
        llm = LLM()

        r1 = self.vuelta(base, llm)
        self.assertEqual(llm.llamadas, [], "sin título no hay contra qué juzgar")
        self.assertEqual((r1["veredictos"], r1["enfriados"]), (0, 40))

        self.reloj["t"] += 30 * 60
        r2 = self.vuelta(base, llm)
        self.assertEqual(sorted(llm.llamadas), skus(50)[40:])
        self.assertEqual(r2["veredictos"], 20)

        llm.llamadas.clear()
        self.reloj["t"] += 30 * 60
        with self.assertNoLogs(LOG_JUEZ, "INFO"):
            r3 = self.vuelta(base, llm)
        self.assertEqual((llm.llamadas, r3["motivo"]), ([], "todo en enfriamiento"))

    def test_la_bitacora_dice_que_gasto_la_cola_y_ese_gasto_cuenta(self):
        base = BaseViva(skus(3))
        r = self.vuelta(base, LLM(usd=0.002))
        self.assertEqual(len(base.bitacora), 1)
        self.assertEqual(base.bitacora[0]["origen"], "cola")
        self.assertAlmostEqual(base.bitacora[0]["usd"], 0.006)
        self.assertEqual((r["veredictos"], r["usd"]), (6, 0.006))
        self.assertEqual(base.pendientes(), [])
        # La misma bolsa: con el tope ya gastado por esta vuelta, la siguiente
        # no busca nada aunque vuelva a haber pendientes.
        base.filas += [fila("SKU-99", 1)]
        llm = LLM()
        r = self.vuelta(base, llm, tope_diario=0.006)
        self.assertEqual((r["motivo"], llm.llamadas), ("tope diario", []))

    def test_los_demas_llamadores_no_cambian(self):
        """`origen` y `resultados` son opcionales: sin ellos, ni la bitácora ni el
        resumen llevan nada nuevo (gancho, botón, mejora y script)."""
        base = BaseViva(skus(2))
        with base.puesta(LLM()):
            r = J.juzgar_skus(skus(2), presupuesto=J.Presupuesto(1.0), modelo=MODELO, hilos=1)
        self.assertNotIn("origen", r)
        self.assertNotIn("origen", base.bitacora[0])
        self.assertEqual(r["veredictos"], 4)


class SinTituloEnElSql(unittest.TestCase):
    def test_la_cola_no_pide_skus_con_titulo_vacio(self):
        """`t.titulo is not null` dejaba pasar ''; `juzgar_skus` pide título no
        vacío (`por_juzgar`). Las dos reglas tienen que ser la misma."""
        with mock.patch.object(J.supabase_db, "fetch_all", return_value=[]) as leer:
            J.skus_con_pendientes()
        sql = leer.call_args.args[0]
        self.assertRegex(sql, r"coalesce\(t\.titulo,\s*''\)\s*<>\s*''")
        self.assertNotRegex(sql, r"t\.titulo\s+is\s+not\s+null")


# ═════════════════════════════════════════════════════════════════════════════
# 5. Cuando `juzgar_skus` se detiene
# ═════════════════════════════════════════════════════════════════════════════

class Detenido(ConReloj):
    def test_proveedor_caido_detiene_la_vuelta_y_la_siguiente_reintenta(self):
        base = BaseViva(skus(20))
        llm = LLM(caido=True)
        with self.assertLogs(LOG_JUEZ, "INFO") as cm:
            r1 = self.vuelta(base, llm, hilos=1)
        self.assertEqual(r1["detenido"], "5 fallos seguidos del proveedor")
        self.assertEqual(llm.llamadas, skus(5))
        linea = next(m for m in cm.output if "cola del juez:" in m)
        self.assertIn("se detuvo: 5 fallos seguidos del proveedor", linea)
        self.assertIn("HTTP 503", linea)
        self.assertEqual(base.bitacora[0]["origen"], "cola")

        # Los 5 que sí se intentaron esperan 24 h (demora, no pérdida: así 5
        # SKUs que el proveedor rechace siempre no frenan la cola); los que la
        # vuelta no alcanzó a intentar NO se enfrían.
        self.assertEqual(set(J._enfriando), set(skus(5)))
        llm.llamadas.clear()
        self.reloj["t"] += 30 * 60                  # la vuelta siguiente, de verdad
        r2 = self.vuelta(base, llm, hilos=1)
        self.assertEqual(llm.llamadas, skus(10)[5:], "la siguiente vuelta vuelve a intentar")
        self.assertEqual(r2["detenido"], "5 fallos seguidos del proveedor")

    def test_plazo_lo_no_alcanzado_sigue_en_la_cabeza(self):
        """Cada llamada «tarda» 40 s de un reloj falso; con 60 s de plazo entran
        dos SKUs. Los demás ni se intentaron: la siguiente vuelta empieza por
        ellos, no los salta."""
        base = BaseViva(skus(6))

        def avanza():
            self.reloj["t"] += 40.0

        llm = LLM(antes=avanza)
        with self.assertLogs(LOG_JUEZ, "INFO") as cm:
            r1 = self.vuelta(base, llm, hilos=1, plazo_s=60.0)
        self.assertEqual((r1["detenido"], r1["veredictos"], r1["enfriados"]), ("plazo", 4, 0))
        self.assertIn("se detuvo: plazo", cm.output[-1])
        self.assertEqual(J._enfriando, {})
        llm.llamadas.clear()
        self.vuelta(base, llm, hilos=1, plazo_s=60.0)
        self.assertEqual(llm.llamadas, ["SKU-03", "SKU-04"])

    def test_techo_por_vuelta(self):
        """Una vuelta sola no se come la bolsa del día: con llamadas de $0.0625
        el techo de $0.10 deja pasar dos (la segunda entra con $0.0625 gastados)."""
        base = BaseViva(skus(10))
        llm = LLM(usd=0.0625)
        r = self.vuelta(base, llm, hilos=1)
        self.assertEqual(llm.llamadas, skus(2))
        self.assertEqual((r["detenido"], r["usd"]), ("tope de gasto", 0.125))
        self.assertEqual(J._enfriando, {}, "lo juzgado quedó completo; lo demás ni se intentó")

    def test_con_dos_hilos_se_pasa_a_lo_mas_por_lo_que_esta_en_vuelo(self):
        """Con los 2 hilos de omisión: el techo se revisa antes de cada SKU, así
        que la vuelta SÍ puede pasarlo, pero solo por los (hasta 2) SKUs que ya
        estaban en vuelo. Es lo que dicen config, README y el acta."""
        base = BaseViva(skus(10))
        llm = LLM(usd=0.0625)
        r = self.vuelta(base, llm)
        self.assertEqual(r["detenido"], "tope de gasto")
        self.assertGreater(r["usd"], J.COLA_TOPE_VUELTA_USD, "el techo no es absoluto")
        self.assertLessEqual(r["usd"], J.COLA_TOPE_VUELTA_USD + 2 * 0.0625)
        self.assertLessEqual(len(llm.llamadas), 3)

    def test_el_sku_en_vuelo_termina_todos_sus_trozos_y_no_se_enfria(self):
        """Un SKU con TROZO+1 rivales son dos llamadas, y las dos se hacen aunque
        la primera ya pase el techo. Cortar entre trozos lo dejaría a medias, y
        el anti-atasco lo enfriaría 24 h como si fuera terco."""
        base = BaseViva(skus(3), por_sku=J.TROZO + 1)
        llm = LLM(usd=0.06)
        r = self.vuelta(base, llm, hilos=1)
        self.assertEqual(llm.llamadas, ["SKU-01", "SKU-01"])
        self.assertEqual(r["detenido"], "tope de gasto")
        self.assertAlmostEqual(r["usd"], 0.12)
        self.assertEqual(base.pendientes(), ["SKU-02", "SKU-03"], "SKU-01 quedó completo")
        self.assertEqual(J._enfriando, {})


# ═════════════════════════════════════════════════════════════════════════════
# 6. Lo que otra corrida ya pagó y todavía no registra
# ═════════════════════════════════════════════════════════════════════════════

class GastoEnVuelo(ConReloj):
    """`juzgar_skus` escribe su fila en la bitácora al TERMINAR. Sin
    `_en_vuelo`, una vuelta de la cola que arranca con el gancho a medias leía la
    bolsa sin lo que el gancho ya había pagado."""

    def test_la_cola_ve_lo_que_el_gancho_ya_pago_y_no_ha_registrado(self):
        base = BaseViva(skus(4))
        a_medias, sigue = threading.Event(), threading.Event()
        detenido = {"ya": False}
        llamadas: list[str] = []

        def llm(system, user, **kw):
            sku = re.search(r"^titulo: .* (SKU-\d+)$", user, re.MULTILINE).group(1)
            llamadas.append(sku)
            if sku == "SKU-02" and not detenido["ya"]:
                # El gancho, a medias: SKU-01 ya pagado, SKU-02 en el aire.
                detenido["ya"] = True
                a_medias.set()
                sigue.wait(5)
            return respuesta(int(re.search(r"RIVALES \((\d+)\)", user).group(1)), usd=0.04)

        def gancho():
            J.juzgar_skus(skus(2), presupuesto=J.Presupuesto(1.0), modelo=MODELO, hilos=1)

        with base.puesta(llm), mock.patch.object(J.settings, "competencia_juez_modelo", MODELO):
            hilo = threading.Thread(target=gancho)
            hilo.start()
            try:
                self.assertTrue(a_medias.wait(5))
                self.assertEqual(base.bitacora, [], "el gancho todavía no registra")
                self.assertAlmostEqual(J.gastado_24h(), 0.04)
                self.assertEqual(J.gastado_24h("terminos"), 0.0, "solo la bolsa del juez")
                llamadas.clear()
                r = J.drenar_cola(max_skus=40, tope_diario=0.045)
            finally:
                sigue.set()
                hilo.join(5)
        self.assertEqual((r["motivo"], llamadas), ("tope diario", []),
                         "sin _en_vuelo la cola veía 0.045 libres y juzgaba SKU-02..04")
        # Al terminar, lo pagado pasa a la bitácora y sale de `_en_vuelo`: ni se
        # pierde ni se cuenta dos veces.
        self.assertEqual(J._en_vuelo, {"usd": 0.0, "corridas": 0})
        self.assertAlmostEqual(base.bitacora[0]["usd"], 0.08)
        with base.puesta(llm):
            self.assertAlmostEqual(J.gastado_24h(), 0.08)

    def test_una_corrida_que_revienta_no_deja_gasto_fantasma(self):
        """Si `_en_vuelo` no se soltara, la bolsa quedaría inflada hasta el
        próximo reinicio y la cola y el gancho dejarían de juzgar sin avisar."""
        base = BaseViva(skus(2))

        def llm(system, user, **kw):
            if "SKU-02" in user:
                raise RuntimeError("se cayó a media corrida")
            return respuesta(int(re.search(r"RIVALES \((\d+)\)", user).group(1)), usd=0.03)

        with base.puesta(llm), self.assertRaises(RuntimeError):
            J.juzgar_skus(skus(2), presupuesto=J.Presupuesto(1.0), modelo=MODELO, hilos=1)
        self.assertEqual(J._en_vuelo, {"usd": 0.0, "corridas": 0})
        self.assertAlmostEqual(base.bitacora[0]["usd"], 0.03, "lo pagado sí queda registrado")


if __name__ == "__main__":
    unittest.main()
