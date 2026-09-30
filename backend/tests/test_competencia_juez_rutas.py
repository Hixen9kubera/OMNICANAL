"""Pruebas de cómo se ENGANCHA el juez de rivales: al trabajo de captura, al
detalle de un SKU y a las rutas que gastan o escriben.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
`competencia_juez` y `competencia_mejora` tienen sus propias pruebas. Estas son de
las tres costuras por donde esa capa toca lo que ya estaba vivo, y en las tres el
riesgo es el mismo: que una capa NUEVA y opcional rompa —o encarezca— algo que
hoy funciona.

  (a) `competencia_trabajos`. Dos clics sobre el mismo término devolvían dos
      trabajos: dos raspados pagados — y la purga no puede tirar lo que sigue
      corriendo, porque con él se va su clave. Y el juez corre DESPUÉS de marcar
      `listo`: un juez lento, apagado, sin tablas o que truena no puede hacer
      creer que la medición —que ya se pagó y ya se guardó— falló.

  (b) `routers.competencia._juicio_de`. El detalle de un SKU lo abren los KAM. Con
      la bandera apagada o sin la tabla (producción no la tiene hasta el acta) la
      respuesta queda EXACTAMENTE como antes, y nunca da 500 por una capa que es
      informativa.

  (c) Las rutas de `juez_router`. Caen bajo `/api/competencia`, que para el RBAC
      es de «operador»: la única barrera real es el `Depends(solo_admin)` del
      router. Y con el código en producción y la bandera apagada no hacen nada.

  (d) `main`: el registro del backend no escribe la llave de Apify, que viaja en
      la URL de cada petición.

── SIN RED Y SIN BASE ──────────────────────────────────────────────────────────
El hilo del trabajo no corre (se suplanta `threading` dentro del módulo y se
llama a `_correr` a mano), la medición es una corrutina falsa y el juez, la
mejora y `tablas_listas` van suplantados. Las banderas se mueven con
`mock.patch.object(settings, ...)`, que las devuelve a su valor al salir. La
sonda de (d) corre en un proceso aparte y con un transporte falso de httpx.

Los títulos y SKUs son INVENTADOS: el repo es público.

    cd backend && python -m unittest tests.test_competencia_juez_rutas -v
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from config import settings  # noqa: E402
from routers import competencia as RC  # noqa: E402
from services import competencia_juez as CJ  # noqa: E402
from services import competencia_trabajos as CT  # noqa: E402

SKU = "PRU-0001-NEG"
TERMINO = "soporte para camara"
LOG_TRABAJOS = "omnicanal.competencia.trabajos"
LOG_ROUTER = "omnicanal.routers.competencia"
LOG_CANDADO = "omnicanal.investigacion"


class _TrabajosLimpios(unittest.TestCase):
    """El almacén de trabajos es un dict de MÓDULO: cada prueba lo recibe vacío y
    lo deja vacío, para que ninguna herede el trabajo «vivo» de la anterior."""

    def setUp(self):
        CT._trabajos.clear()
        CT._por_clave.clear()
        self.addCleanup(CT._trabajos.clear)
        self.addCleanup(CT._por_clave.clear)

    @contextlib.contextmanager
    def sin_hilos(self):
        """Suplanta `threading` DENTRO del módulo: el trabajo se registra pero su
        hilo no arranca. El `_lock` ya está creado y no se toca."""
        with mock.patch.object(CT, "threading") as falso:
            yield falso.Thread


# ── (a) competencia_trabajos ─────────────────────────────────────────────────

class ArrancarNoDuplica(_TrabajosLimpios):
    """Un trabajo VIVO por clave: el segundo POST recibe el `jid` del primero."""

    def test_dos_arranques_del_mismo_termino_devuelven_el_mismo_jid(self):
        """Dos personas, dos pestañas, o el botón que se reactiva cuando el sondeo
        se rinde a los 6 minutos: un solo raspado pagado."""
        with self.sin_hilos() as hilo:
            uno = CT.arrancar(TERMINO)
            dos = CT.arrancar(TERMINO)
        self.assertEqual(uno["id"], dos["id"])
        self.assertEqual(hilo.call_count, 1, "un solo hilo = un solo raspado")
        self.assertEqual(len(CT._trabajos), 1)

    def test_sigue_siendo_el_mismo_mientras_raspa(self):
        with self.sin_hilos() as hilo:
            uno = CT.arrancar(TERMINO)
            CT._marcar(uno["id"], "raspando")
            dos = CT.arrancar(TERMINO)
        self.assertEqual(uno["id"], dos["id"])
        self.assertEqual(dos["paso"], "raspando", "devuelve el estado ACTUAL del que corre")
        self.assertEqual(hilo.call_count, 1)

    def test_terminado_el_trabajo_un_nuevo_arranque_si_es_otro(self):
        """El candado es por trabajo VIVO, no para siempre: volver a medir un
        término ya medido es una decisión legítima."""
        for final in ("listo", "error"):
            with self.subTest(paso=final), self.sin_hilos() as hilo:
                uno = CT.arrancar(f"{TERMINO} {final}")
                CT._marcar(uno["id"], final)
                dos = CT.arrancar(f"{TERMINO} {final}")
                self.assertNotEqual(uno["id"], dos["id"])
                self.assertEqual(hilo.call_count, 2)

    def test_terminos_distintos_son_trabajos_distintos(self):
        with self.sin_hilos():
            self.assertNotEqual(CT.arrancar(TERMINO)["id"], CT.arrancar("filtro pop")["id"])

    def test_la_mejora_de_un_sku_tampoco_se_duplica(self):
        """«Buscar mejor término» paga IA y hasta dos búsquedas de Apify."""
        with self.sin_hilos() as hilo:
            uno = CT.arrancar_mejora(SKU)
            dos = CT.arrancar_mejora(SKU)
        self.assertEqual(uno["id"], dos["id"])
        self.assertEqual(hilo.call_count, 1)

    def test_una_busqueda_y_una_mejora_no_comparten_clave(self):
        """Las claves llevan prefijo: un término que se llame igual que un SKU no
        debe recibir el `jid` de la mejora de ese SKU."""
        with self.sin_hilos():
            self.assertNotEqual(CT.arrancar(SKU)["id"], CT.arrancar_mejora(SKU)["id"])

    def test_el_hilo_recibe_el_trabajo_y_su_termino(self):
        with self.sin_hilos() as hilo:
            t = CT.arrancar(TERMINO)
        args = hilo.call_args.kwargs["args"]
        self.assertEqual(args, (CT._correr, t["id"], TERMINO))
        self.assertEqual(t["paso"], "encolado")
        self.assertIsNone(t["juez"])


class LaPurgaNoTiraLoVivo(_TrabajosLimpios):
    """`_purgar` decidía solo por la edad y por el tope, sin mirar el paso: tiraba
    trabajos que seguían corriendo y con ellos su clave. El siguiente POST del
    mismo término ya no recibía el `jid` del que corría: arrancaba otro hilo y
    volvía a pagar. Lo vivo solo se tira cuando ya es un zombi."""

    def _vivo(self, termino=TERMINO, *, edad=0.0):
        t = CT.arrancar(termino)
        CT._marcar(t["id"], "raspando")
        CT._trabajos[t["id"]]["creado"] -= edad
        return t["id"]

    def test_un_trabajo_vivo_de_mas_de_media_hora_no_se_tira(self):
        """Basta con que haga cola tras las dos corridas simultáneas de Apify."""
        with self.sin_hilos() as hilo:
            jid = self._vivo(edad=CT._TTL + 1)
            otra = CT.arrancar(TERMINO)
        self.assertEqual(otra["id"], jid)
        self.assertEqual(hilo.call_count, 1, "un solo hilo = un solo raspado")

    def test_el_tope_no_tira_al_vivo_aunque_sea_el_mas_viejo(self):
        """41 arranques mientras uno sigue vivo: el recorte elige entre los
        terminados, no entre todos."""
        with self.sin_hilos() as hilo:
            jid = self._vivo(edad=60)
            for i in range(CT._MAX + 1):
                CT._marcar(CT.arrancar(f"otro termino {i}")["id"], "listo")
            otra = CT.arrancar(TERMINO)
        self.assertEqual(otra["id"], jid)
        self.assertEqual(hilo.call_count, CT._MAX + 2)
        self.assertLessEqual(len(CT._trabajos), CT._MAX + 1)

    def test_el_recorte_tira_primero_lo_terminado_mas_viejo(self):
        """El control: con puros terminados el tope sigue funcionando igual."""
        with self.sin_hilos():
            primero = CT.arrancar("termino viejo")["id"]
            CT._marcar(primero, "listo")
            CT._trabajos[primero]["creado"] -= 60
            for i in range(CT._MAX + 1):
                CT._marcar(CT.arrancar(f"otro termino {i}")["id"], "listo")
        self.assertIsNone(CT.estado(primero))
        # La purga corre ANTES de registrar el nuevo: queda el tope más el que llegó.
        self.assertEqual(len(CT._trabajos), CT._MAX + 1)

    def test_lo_terminado_si_caduca_a_la_media_hora(self):
        with self.sin_hilos():
            jid = CT.arrancar(TERMINO)["id"]
            CT._marcar(jid, "listo")
            CT._trabajos[jid]["creado"] -= CT._TTL + 1
            CT.arrancar("otro termino")
        self.assertIsNone(CT.estado(jid))
        self.assertNotIn(f"busq:{TERMINO}", CT._por_clave)

    def test_un_zombi_si_se_tira_y_suelta_su_clave(self):
        """Vivo pero con más de tres medias horas: ya no va a terminar, y quedarse
        con la clave impediría medir ese término para siempre."""
        with self.sin_hilos() as hilo:
            jid = self._vivo(edad=CT._ZOMBI + 1)
            otra = CT.arrancar(TERMINO)
        self.assertNotEqual(otra["id"], jid)
        self.assertIsNone(CT.estado(jid))
        self.assertEqual(hilo.call_count, 2)


class JuezTrasLaCaptura(_TrabajosLimpios):
    """El juez va DESPUÉS de `listo` y nunca cambia el paso del trabajo."""

    def _correr(self, *, n=10, enabled=True, tablas=True, gastado=0.0, juzgar=None,
                murado=False, medir_truena=False):
        juzgar = juzgar or mock.Mock(return_value={"veredictos": 8, "sin_juzgar": 2})

        async def medir(terminos, limite=10, bloqueados=None):
            if medir_truena:
                raise RuntimeError("Apify no contestó")
            if murado and bloqueados is not None:
                bloqueados.update(terminos)
            return {t: n for t in terminos}

        with self.sin_hilos():
            jid = CT.arrancar(TERMINO)["id"]
        with mock.patch.object(settings, "competencia_juez_enabled", enabled), \
             mock.patch.object(settings, "competencia_juez_tope_diario_usd", 1.0), \
             mock.patch.object(CT.competencia_captura, "medir_busquedas", medir), \
             mock.patch.object(CT.competencia_juez, "tablas_listas",
                               return_value=tablas) as listas, \
             mock.patch.object(CT.competencia_juez, "gastado_24h",
                               return_value=gastado) as gasto, \
             mock.patch.object(CT.competencia_juez, "juzgar_termino", juzgar):
            CT._correr(jid, TERMINO)
        return SimpleNamespace(estado=CT.estado(jid), juzgar=juzgar, listas=listas,
                               gasto=gasto, jid=jid)

    def test_con_la_bandera_apagada_no_corre_ni_toca_la_base(self):
        """Con el código en producción y todo apagado, la captura se comporta como
        antes de que el juez existiera: ni siquiera pregunta si hay tablas."""
        t = self._correr(enabled=False)
        t.juzgar.assert_not_called()
        t.listas.assert_not_called()
        t.gasto.assert_not_called()
        self.assertEqual((t.estado["paso"], t.estado["filas"]), ("listo", 10))
        self.assertIsNone(t.estado["juez"])

    def test_sin_filas_nuevas_no_corre(self):
        """`n == 0`: o ML no tiene nada, o nos bloqueó y se conservan las filas
        VIEJAS. En ninguno de los dos hay una captura de hoy que juzgar."""
        t = self._correr(n=0)
        t.juzgar.assert_not_called()
        t.listas.assert_not_called()
        self.assertEqual(t.estado["paso"], "listo")
        self.assertTrue(t.estado["vacio"])
        self.assertIsNone(t.estado["juez"])

    def test_un_termino_bloqueado_tampoco_se_juzga(self):
        t = self._correr(n=0, murado=True)
        t.juzgar.assert_not_called()
        self.assertTrue(t.estado["bloqueado"])
        self.assertEqual(t.estado["paso"], "listo")

    def test_sin_tablas_no_corre(self):
        """Producción no tiene `market_rival_juicio` hasta que la migración pase
        su acta: encender la bandera antes no debe reventar nada."""
        t = self._correr(tablas=False)
        t.juzgar.assert_not_called()
        t.gasto.assert_not_called()
        self.assertEqual(t.estado["paso"], "listo")
        self.assertIsNone(t.estado["juez"])

    def test_si_el_juez_lanza_el_paso_sigue_listo_y_juez_queda_fallo(self):
        """La medición ya se pagó y ya está guardada. Un `error` aquí haría que
        el usuario volviera a apretar el botón y a pagar."""
        juzgar = mock.Mock(side_effect=RuntimeError("DeepSeek no respondió"))
        with self.assertLogs(LOG_TRABAJOS, "WARNING"):
            t = self._correr(juzgar=juzgar)
        juzgar.assert_called_once()
        self.assertEqual(t.estado["paso"], "listo")
        self.assertEqual(t.estado["juez"], "fallo")
        self.assertEqual(t.estado["filas"], 10)
        self.assertIsNone(t.estado["error"])

    def test_con_el_tope_diario_agotado_juez_queda_tope(self):
        """El tope es UNA bolsa con el lote, el botón y la mejora: si el lote ya
        gastó el día, el enganche no juzga — y lo dice en el log, porque en el
        panel solo se ven rivales «sin juzgar» y se leería como gancho roto."""
        with self.assertLogs(LOG_TRABAJOS, "INFO") as logs:
            t = self._correr(gastado=1.0)
        t.juzgar.assert_not_called()
        self.assertEqual(t.estado["juez"], "tope")
        self.assertEqual(t.estado["paso"], "listo")
        self.assertIn("tope diario alcanzado (1.00 de 1.00 USD", "\n".join(logs.output))

    def test_si_el_juicio_se_detuvo_queda_parcial_con_el_motivo(self):
        """Un juicio que se detuvo por plazo, tope o fallos NO es `listo`: los SKUs
        que no alcanzó ni se intentaron y `sin_juzgar` no los cuenta. Antes quedaba
        `juez: 'listo', juez_pendientes: 0` con rivales sin juzgar."""
        t = self._correr(juzgar=mock.Mock(return_value={
            "veredictos": 3, "sin_juzgar": 0, "detenido": "plazo"}))
        self.assertEqual(t.estado["paso"], "listo")
        self.assertEqual((t.estado["juez"], t.estado["juez_detenido"]), ("parcial", "plazo"))
        self.assertEqual(t.estado["juez_veredictos"], 3)

    def test_sin_detenerse_queda_listo_y_sin_motivo(self):
        t = self._correr(juzgar=mock.Mock(return_value={
            "veredictos": 3, "sin_juzgar": 0, "detenido": None}))
        self.assertEqual((t.estado["juez"], t.estado["juez_detenido"], t.estado["juez_motivo"]),
                         ("listo", None, None))

    def test_si_quedaron_rivales_sin_juzgar_queda_parcial_con_la_cuenta_y_el_motivo(self):
        """Llegar al final NO es juzgarlo todo. Con la IA caída 1 a 4 veces (a la 5ª
        se detiene), una respuesta inválida o un SKU que la base no guardó, el
        trabajo quedaba `listo` y el panel pintaba «ya juzgados» en verde junto a
        «10 sin juzgar». El motivo va sin el cuerpo del error del proveedor: el
        trabajo lo lee cualquiera que mida, no solo un admin."""
        casos = [
            ("la IA no contestó", 0, 10, "DeepSeek no respondió (HTTP 401: cuerpo del proveedor).",
             "DeepSeek no respondió"),
            ("respuesta inválida", 6, 4, "respuesta inválida del modelo (clase desconocida)",
             "respuesta inválida del modelo"),
            ("la base no guardó", 8, 2, "no se pudo guardar en la base",
             "no se pudo guardar en la base"),
            ("el modelo contestó de menos", 8, 2, None, None),
        ]
        for caso, veredictos, sin_juzgar, ultimo, motivo in casos:
            with self.subTest(caso):
                CT._trabajos.clear()
                CT._por_clave.clear()
                t = self._correr(juzgar=mock.Mock(return_value={
                    "veredictos": veredictos, "sin_juzgar": sin_juzgar, "detenido": None,
                    "fallidos": 1, "ultimo_motivo": ultimo}))
                e = t.estado
                self.assertEqual(e["paso"], "listo", "la medición ya se pagó y ya se guardó")
                self.assertEqual((e["juez"], e["juez_detenido"], e["juez_motivo"]),
                                 ("parcial", None, motivo))
                self.assertEqual((e["juez_veredictos"], e["juez_pendientes"]),
                                 (veredictos, sin_juzgar))
                self.assertNotIn("HTTP", json.dumps(e, ensure_ascii=False))

    def test_el_panel_espera_lo_que_el_juez_puede_tardar(self):
        """El plazo se revisa ANTES de cada SKU, no durante: el que arranca en el
        segundo 59 hace su trozo entero, que espera turno hasta `timeout` y llama
        hasta otro tanto, dos veces si el lote sale sospechoso. El panel preguntaba
        75 s y soltaba «Juzgar rivales» con el juez todavía mandando esas mismas
        parejas: doble pago. Los 15 s de más son las lecturas a la base y el
        primer sondeo."""
        pagina = Path(__file__).resolve().parents[2] / "frontend" / "app" / "competencia" / "page.tsx"
        if not pagina.exists():
            self.skipTest("el frontend no está junto al backend")
        kw = self._correr().juzgar.call_args.kwargs
        peor = kw["plazo_s"] + 2 * (kw["timeout"] + kw["timeout"])
        texto = pagina.read_text(encoding="utf-8")
        vueltas = int(re.search(r"^const JUEZ_VUELTAS = (\d+);", texto, re.M).group(1))
        sondeo = int(re.search(r"^const SONDEO_MS = (\d+);", texto, re.M).group(1)) / 1000
        self.assertGreaterEqual(vueltas * sondeo, peor + 15,
                                f"el panel pregunta {vueltas * sondeo:.0f} s tras `listo` y el "
                                f"juez puede tardar {peor:.0f} s")

    def test_si_no_se_puede_medir_el_gasto_no_se_gasta(self):
        """`gastado_24h` devuelve infinito cuando la bitácora no contesta: sin
        poder medir el tope, el camino automático no corre."""
        t = self._correr(gastado=float("inf"))
        t.juzgar.assert_not_called()
        self.assertEqual(t.estado["juez"], "tope")

    def test_cuando_corre_deja_sus_conteos_y_usa_lo_que_queda_del_tope(self):
        t = self._correr(gastado=0.25)
        self.assertEqual(t.estado["paso"], "listo")
        # 2 sin juzgar: no es `listo` (ver la prueba de los que quedaron sin juzgar).
        self.assertEqual(t.estado["juez"], "parcial")
        self.assertEqual((t.estado["juez_veredictos"], t.estado["juez_pendientes"]), (8, 2))
        self.assertEqual(t.juzgar.call_args.args, (TERMINO,))
        kw = t.juzgar.call_args.kwargs
        self.assertIsInstance(kw["presupuesto"], CJ.Presupuesto)
        self.assertAlmostEqual(kw["presupuesto"].tope, 0.75)
        # Colgado de una captura, el juez no puede retener el hilo: plazo de reloj
        # y una sola llamada corta por lote. Lo que no alcance queda pendiente.
        self.assertEqual(kw["plazo_s"], CT._JUEZ_PLAZO_S)
        self.assertEqual(kw["timeout"], CT._JUEZ_TIMEOUT_S)
        self.assertEqual(kw["intentos"], 1)

    def test_listo_se_marca_antes_de_que_el_juez_empiece(self):
        """El contrato con el panel: el sondeo se rinde a los 6 minutos y el
        raspado solo ya midió 178 s. Si el juez fuera ANTES de `listo`, un juez
        lento dejaría al usuario sin ver la medición que ya pagó."""
        visto = {}

        def juzgar(termino, **kw):
            e = next(iter(CT._trabajos.values()))
            visto.update(paso=e["paso"], filas=e["filas"], juez=e["juez"])
            return {"veredictos": 1, "sin_juzgar": 0}

        self._correr(juzgar=mock.Mock(side_effect=juzgar))
        self.assertEqual(visto, {"paso": "listo", "filas": 10, "juez": "corriendo"})

    def test_si_la_medicion_falla_el_trabajo_es_error_y_el_juez_no_corre(self):
        with self.assertLogs(LOG_TRABAJOS, "WARNING"):
            t = self._correr(medir_truena=True)
        t.juzgar.assert_not_called()
        self.assertEqual(t.estado["paso"], "error")
        self.assertIn("Apify no contestó", t.estado["error"])


class LaMejoraLeDiceAlPanel(_TrabajosLimpios):
    """`_correr_mejora` copia al trabajo SOLO las llaves que lee el panel
    (`CompetenciaMejoraResultado` en types.ts): el `detalle` trae SKUs y términos
    y los costos no se enseñan. `en_espera` sí viaja: sin ella, «la IA solo repitió
    candidatos que esperan su reintento» se contaba como «no propuso nada». Las
    demás llaves nuevas (`ocupados`, `usd_ia`, `usd_propuestas`) no hacen falta:
    con UN SKU, `ocupados` solo es 1 cuando nada se reclamó, y eso ya llega como
    `detenido`."""

    PANEL = {"ok", "motivo", "sugerencias", "termino_ok", "sin_mejora", "bloqueados",
             "errores", "en_espera", "paginas", "detenido"}

    def test_en_espera_llega_al_panel(self):
        from services import competencia_mejora as CM

        with self.sin_hilos():
            jid = CT.arrancar_mejora(SKU)["id"]
        with mock.patch.object(CM, "mejorar_sku", return_value={
                "ok": True, "sugerencias": 0, "en_espera": 1, "sin_candidato": 0}):
            CT._correr_mejora(jid, SKU)
        e = CT.estado(jid)
        self.assertEqual(set(e["resultado"]), self.PANEL)
        self.assertEqual(e["resultado"]["en_espera"], 1)

    def test_el_reclamo_ajeno_llega_como_detenido(self):
        """Otro proceso tiene el candidato, o quedó a medias: sin `detenido` el
        botón acababa en «la IA no propuso nada», que es falso."""
        from services import competencia_mejora as CM

        motivo = ("otro proceso ya está probando ese candidato (o quedó a medias): "
                  "reintenta en 30 min")
        with self.sin_hilos():
            jid = CT.arrancar_mejora(SKU)["id"]
        with mock.patch.object(CM, "mejorar_sku", return_value={
                "ok": True, "sugerencias": 0, "ocupados": 1, "detenido": motivo,
                "usd_ia": 0.001, "usd_propuestas": 0.001,
                "detalle": [{"sku": SKU, "termino": "termino inventado"}]}) as mejorar:
            CT._correr_mejora(jid, SKU)
        e = CT.estado(jid)
        self.assertEqual(mejorar.call_args.args, (SKU,))
        self.assertEqual(e["paso"], "listo")
        self.assertEqual(set(e["resultado"]), self.PANEL)
        self.assertEqual((e["resultado"]["ok"], e["resultado"]["detenido"]), (True, motivo))


# ── (b) _juicio_de ───────────────────────────────────────────────────────────

def _rival(externo_id, clase="mismo", *, vigente=True, precio=500.0):
    """Una fila como las de `competencia_juez.filas`."""
    return {"sku": SKU, "externo_id": externo_id, "clase": clase, "motivo": f"motivo {clase}",
            "unidades_rival": 1, "juzgado_en": None, "precio": precio, "cuenta": True,
            "vigente": vigente, "comparable": vigente and clase == "mismo",
            "pendiente": not vigente}


def _pintadas():
    """Las filas de `busqueda_general` que el detalle ya iba a devolver."""
    return [{"externo_id": "MLM1", "titulo": "artículo genérico uno", "precio": 500.0},
            {"externo_id": "MLM2", "titulo": "artículo genérico dos", "precio": 120.0},
            {"externo_id": "MLM3", "titulo": "artículo genérico tres", "precio": 510.0},
            {"externo_id": "MLM9", "titulo": "artículo que el juez no conoce", "precio": 90.0}]


class JuicioDe(unittest.TestCase):
    """`_juicio_de`: la capa informativa del detalle de un SKU."""

    def _llamar(self, general, *, visible=True, tablas=True, filas=None, escritura=False,
                sugerencia=None):
        filas = filas if filas is not None else mock.Mock(return_value=[])
        sugerencia = sugerencia or mock.Mock(return_value=None)
        with mock.patch.object(settings, "competencia_juez_visible", visible), \
             mock.patch.object(settings, "competencia_juez_escritura", escritura), \
             mock.patch.object(RC.competencia_juez, "tablas_listas",
                               return_value=tablas) as listas, \
             mock.patch.object(RC.competencia_juez, "filas", filas), \
             mock.patch.object(RC.competencia_mejora, "sugerencia", sugerencia):
            r = RC._juicio_de(SKU, general)
        return r, listas, filas, sugerencia

    def test_con_la_bandera_apagada_devuelve_vacio_y_no_toca_nada(self):
        """La respuesta del detalle queda EXACTAMENTE como antes: ni una llave
        nueva en las filas, ni una consulta a la base."""
        general = _pintadas()
        antes = copy.deepcopy(general)
        r, listas, filas, sugerencia = self._llamar(general, visible=False)
        self.assertEqual(r, {})
        self.assertEqual(general, antes)
        listas.assert_not_called()
        filas.assert_not_called()
        sugerencia.assert_not_called()

    def test_sin_tablas_devuelve_vacio_y_las_filas_quedan_intactas(self):
        """Producción hoy: el código llega a main antes que la migración. Sin
        esta guardia, abrir CUALQUIER SKU en Competencia daría 500."""
        general = _pintadas()
        antes = copy.deepcopy(general)
        r, _, filas, sugerencia = self._llamar(general, tablas=False)
        self.assertEqual(r, {})
        self.assertEqual(general, antes)
        filas.assert_not_called()
        sugerencia.assert_not_called()

    def test_si_filas_lanza_devuelve_vacio_y_no_revienta(self):
        general = _pintadas()
        antes = copy.deepcopy(general)
        with self.assertLogs(LOG_ROUTER, "WARNING"):
            r, _, _, _ = self._llamar(
                general, filas=mock.Mock(side_effect=RuntimeError("relation does not exist")))
        self.assertEqual(r, {})
        self.assertEqual(general, antes)

    def test_si_tablas_listas_lanza_tampoco_revienta(self):
        general = _pintadas()
        with mock.patch.object(settings, "competencia_juez_visible", True), \
             mock.patch.object(RC.competencia_juez, "tablas_listas",
                               side_effect=RuntimeError("pool agotado")), \
             self.assertLogs(LOG_ROUTER, "WARNING"):
            self.assertEqual(RC._juicio_de(SKU, general), {})

    def test_si_la_sugerencia_lanza_no_queda_un_veredicto_a_medias(self):
        """La sugerencia se lee al FINAL, con las filas ya anotadas: si truena, el
        detalle no puede salir con insignias pero sin el resumen que las explica."""
        general = _pintadas()
        with self.assertLogs(LOG_ROUTER, "WARNING"):
            r, _, _, _ = self._llamar(
                general, filas=mock.Mock(return_value=[_rival("MLM1")]),
                sugerencia=mock.Mock(side_effect=RuntimeError("base caída")))
        self.assertEqual(r, {})
        self.assertTrue(all("veredicto" not in g for g in general))

    def test_con_veredictos_anexa_clase_y_motivo_a_cada_fila(self):
        filas = mock.Mock(return_value=[
            _rival("MLM1", "mismo"), _rival("MLM2", "refaccion"),
            _rival("MLM3", "mismo", vigente=False)])
        general = _pintadas()
        self._llamar(general, filas=filas)
        filas.assert_called_once_with([SKU])
        por_id = {g["externo_id"]: g for g in general}
        self.assertEqual((por_id["MLM1"]["veredicto"], por_id["MLM1"]["veredicto_motivo"]),
                         ("mismo", "motivo mismo"))
        self.assertEqual(por_id["MLM1"]["veredicto_unidades"], 1)
        self.assertEqual((por_id["MLM2"]["veredicto"], por_id["MLM2"]["veredicto_motivo"]),
                         ("refaccion", "motivo refaccion"))
        # Lo que se pintaba antes sigue ahí, sin tocar.
        self.assertEqual((por_id["MLM1"]["titulo"], por_id["MLM1"]["precio"]),
                         ("artículo genérico uno", 500.0))

    def test_un_veredicto_vencido_o_ausente_se_pinta_como_sin_juzgar(self):
        """«Sin juzgar» es «no sé», nunca «comparable»: un veredicto emitido
        contra un título que ya cambió no se enseña como si valiera hoy."""
        filas = mock.Mock(return_value=[_rival("MLM3", "mismo", vigente=False)])
        general = _pintadas()
        self._llamar(general, filas=filas)
        por_id = {g["externo_id"]: g for g in general}
        for externo_id in ("MLM3", "MLM9"):
            with self.subTest(externo_id=externo_id):
                self.assertIsNone(por_id[externo_id]["veredicto"])
                self.assertIsNone(por_id[externo_id]["veredicto_motivo"])
                self.assertIsNone(por_id[externo_id]["veredicto_unidades"])

    def test_el_resumen_sale_de_las_filas_pintadas(self):
        """El juez conoce 5 rivales del SKU (3 `mismo`), pero la pantalla pinta
        los 3 primeros. El «N de M» tiene que contar lo que se ve: si saliera de
        todo lo juzgado diría «3 comparables» sobre una lista que enseña 1."""
        filas = mock.Mock(return_value=[
            _rival("MLM1", "mismo"), _rival("MLM2", "refaccion"),
            _rival("MLM3", "mismo", vigente=False),
            _rival("MLM4", "mismo"), _rival("MLM5", "mismo")])
        r, _, _, _ = self._llamar(_pintadas(), filas=filas)
        juez = r["juez"]
        self.assertEqual((juez["total"], juez["juzgados"], juez["comparables"]), (3, 2, 1))
        self.assertEqual(juez["pendientes"], 1)
        self.assertFalse(juez["completo"])
        self.assertEqual(juez["por_clase"], {"mismo": 1, "refaccion": 1})

    def test_devuelve_la_sugerencia_abierta_y_si_se_puede_escribir(self):
        abierta = {"id": 7, "termino_candidato": "soporte pared camara seguridad"}
        for escritura in (False, True):
            with self.subTest(escritura=escritura):
                r, _, _, sugerencia = self._llamar(
                    _pintadas(), filas=mock.Mock(return_value=[_rival("MLM1")]),
                    escritura=escritura, sugerencia=mock.Mock(return_value=abierta))
                sugerencia.assert_called_once_with(SKU)
                self.assertEqual(r["termino_sugerido"], abierta)
                self.assertIs(r["juez"]["puede_escribir"], escritura)

    def test_sin_rivales_pintados_devuelve_un_resumen_en_ceros(self):
        r, _, _, _ = self._llamar([], filas=mock.Mock(return_value=[_rival("MLM1")]))
        self.assertEqual((r["juez"]["total"], r["juez"]["comparables"]), (0, 0))
        self.assertFalse(r["juez"]["completo"])
        self.assertIsNone(r["termino_sugerido"])


# ── (c) Las rutas ────────────────────────────────────────────────────────────

BASE = "/api/competencia/juez"
RUTAS = [
    ("POST", f"{BASE}/juzgar", {"sku": SKU}),
    ("POST", f"{BASE}/mejorar", {"sku": SKU}),
    ("GET", f"{BASE}/trabajo/x", None),
    ("POST", f"{BASE}/sugerencias/7/aceptar", None),
    ("POST", f"{BASE}/sugerencias/7/descartar", None),
]
# Las que gastan o escriben. El GET solo lee el estado de un trabajo en memoria.
ESCRITURAS = [r for r in RUTAS if r[0] == "POST"]
ADMIN = SimpleNamespace(autenticado=True, tipo="persona", rol="admin", actor="admin@prueba")


class RutasDelJuez(unittest.TestCase):
    """Las rutas de `juez_router` sobre una app mínima que solo monta el router de
    Competencia: sin middleware de identidad, como si hubiera «fallado abierto»."""

    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(RC.router)
        self.cli = TestClient(self.app)

    def _pedir(self, metodo, ruta, cuerpo):
        return self.cli.request(metodo, ruta, json=cuerpo)

    def _como_admin(self):
        self.app.dependency_overrides[RC.solo_admin] = lambda: ADMIN
        self.addCleanup(self.app.dependency_overrides.clear)

    @contextlib.contextmanager
    def _ambiente(self, *, escritura=True, tablas=True):
        """Banderas y servicios suplantados. Devuelve los testigos."""
        resumen = {"total": 10, "juzgados": 10, "comparables": 4, "pendientes": 0,
                   "completo": True, "por_clase": {"mismo": 4, "otro_producto": 6},
                   "juzgado_en": None}
        with mock.patch.object(settings, "competencia_juez_escritura", escritura), \
             mock.patch.object(RC.competencia_juez, "tablas_listas",
                               return_value=tablas) as listas, \
             mock.patch.object(RC.competencia_juez, "juzgar_skus", return_value={
                 "detenido": None, "veredictos": 7, "sin_juzgar": 1}) as juzgar, \
             mock.patch.object(RC.competencia_juez, "resumen_sku",
                               return_value=resumen) as resumen_sku, \
             mock.patch.object(RC.competencia_trabajos, "arrancar_mejora", return_value={
                 "id": "abc123", "paso": "encolado", "sku": SKU}) as arrancar, \
             mock.patch.object(RC.competencia_trabajos, "estado",
                               return_value=None) as estado, \
             mock.patch.object(RC.competencia_mejora, "aceptar", return_value={
                 "ok": True, "sku": SKU, "termino": "soporte pared camara"}) as aceptar, \
             mock.patch.object(RC.competencia_mejora, "descartar",
                               return_value={"ok": True}) as descartar:
            yield SimpleNamespace(listas=listas, juzgar=juzgar, resumen_sku=resumen_sku,
                                  arrancar=arrancar, estado=estado, aceptar=aceptar,
                                  descartar=descartar, resumen=resumen)

    def _nada_corrio(self, t):
        for nombre in ("juzgar", "arrancar", "estado", "aceptar", "descartar"):
            getattr(t, nombre).assert_not_called()

    # ── El candado ──────────────────────────────────────────────────────────

    def test_la_lista_de_rutas_de_esta_prueba_esta_completa(self):
        """Si alguien agrega una ruta al juez, esta prueba se lo recuerda: la
        ruta nueva tiene que entrar a `RUTAS` y pasar por los candados de abajo.

        Se leen del esquema OpenAPI y no de `app.routes`: la forma de esa lista
        cambia entre versiones de FastAPI (los routers incluidos ya no se aplanan)."""
        reales = {(m.upper(), ruta) for ruta, ops in self.app.openapi()["paths"].items()
                  if ruta.startswith(BASE) for m in ops}
        esperadas = {("POST", f"{BASE}/juzgar"), ("POST", f"{BASE}/mejorar"),
                     ("GET", f"{BASE}/trabajo/{{jid}}"),
                     ("POST", f"{BASE}/sugerencias/{{intento_id}}/aceptar"),
                     ("POST", f"{BASE}/sugerencias/{{intento_id}}/descartar")}
        self.assertEqual(reales, esperadas)
        self.assertEqual(len(RUTAS), len(esperadas))

    def test_el_candado_esta_en_el_router_no_ruta_por_ruta(self):
        """Puesto a nivel de ROUTER no se puede olvidar en una ruta nueva."""
        self.assertTrue(any(d.dependency is RC.solo_admin
                            for d in RC.juez_router.dependencies))

    def test_sin_sesion_todas_dan_401_y_ninguna_hace_nada(self):
        """Con las banderas ENCENDIDAS y las tablas listas: lo único que detiene
        a un anónimo es el candado."""
        with self._ambiente() as t, self.assertLogs(LOG_CANDADO, "WARNING"):
            for metodo, ruta, cuerpo in RUTAS:
                with self.subTest(ruta=f"{metodo} {ruta}"):
                    self.assertEqual(self._pedir(metodo, ruta, cuerpo).status_code, 401)
            self._nada_corrio(t)

    def test_sin_ser_admin_todas_dan_403(self):
        """`/api/competencia` es de «operador» para el RBAC: un KAM con sesión
        llega hasta aquí, y aquí se detiene. Una llave de máquina, también."""
        operador = SimpleNamespace(autenticado=True, tipo="persona", rol="operador",
                                   actor="kam@prueba")
        lectura = SimpleNamespace(autenticado=True, tipo="persona", rol="lectura",
                                  actor="lector@prueba")
        maquina = SimpleNamespace(autenticado=True, tipo="maquina", rol="admin", actor="llave")
        for quien in (operador, lectura, maquina):
            with self._ambiente() as t, \
                 mock.patch("routers.investigacion.core_identidad.resolver",
                            new=mock.AsyncMock(return_value=quien)), \
                 self.assertLogs(LOG_CANDADO, "WARNING"):
                for metodo, ruta, cuerpo in RUTAS:
                    with self.subTest(quien=quien.actor, ruta=f"{metodo} {ruta}"):
                        self.assertEqual(self._pedir(metodo, ruta, cuerpo).status_code, 403)
                self._nada_corrio(t)

    # ── Las banderas ────────────────────────────────────────────────────────

    def test_con_la_escritura_apagada_dan_409_sin_tocar_la_base(self):
        """El estado de producción el día que el código llegue a main: un admin
        con sesión aprieta el botón y no pasa nada —ni IA, ni Apify, ni base."""
        self._como_admin()
        with self._ambiente(escritura=False) as t:
            for metodo, ruta, cuerpo in ESCRITURAS:
                with self.subTest(ruta=ruta):
                    r = self._pedir(metodo, ruta, cuerpo)
                    self.assertEqual(r.status_code, 409)
                    self.assertIn("apagado", r.json()["detail"])
            self._nada_corrio(t)
            t.listas.assert_not_called()

    def test_sin_tablas_dan_409_aunque_la_bandera_este_encendida(self):
        self._como_admin()
        with self._ambiente(tablas=False) as t:
            for metodo, ruta, cuerpo in ESCRITURAS:
                with self.subTest(ruta=ruta):
                    r = self._pedir(metodo, ruta, cuerpo)
                    self.assertEqual(r.status_code, 409)
                    self.assertIn("tablas", r.json()["detail"])
            self._nada_corrio(t)

    # ── Cada ruta llama a lo suyo ───────────────────────────────────────────

    def test_juzgar_llama_al_juez_con_un_tope_chico_y_devuelve_el_resumen(self):
        self._como_admin()
        with self._ambiente() as t:
            r = self._pedir("POST", f"{BASE}/juzgar", {"sku": SKU})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"veredictos": 7, "sin_juzgar": 1, "juez": t.resumen})
        self.assertEqual(t.juzgar.call_args.args, ([SKU],))
        kw = t.juzgar.call_args.kwargs
        self.assertIsInstance(kw["presupuesto"], CJ.Presupuesto)
        self.assertLessEqual(kw["presupuesto"].tope, 0.05, "un clic cuesta centavos")
        # Alguien está mirando: llamadas cortas, no los 120 s × 3 del lote.
        self.assertEqual((kw["timeout"], kw["intentos"]), (30.0, 1))
        t.resumen_sku.assert_called_once_with(SKU)
        t.arrancar.assert_not_called()

    def test_juzgar_detenido_da_409_con_el_motivo(self):
        self._como_admin()
        with self._ambiente() as t:
            t.juzgar.return_value = {"detenido": "modelo sin precio: x", "veredictos": 0,
                                     "sin_juzgar": 0}
            r = self._pedir("POST", f"{BASE}/juzgar", {"sku": SKU})
        self.assertEqual(r.status_code, 409)
        self.assertIn("modelo sin precio", r.json()["detail"])
        t.resumen_sku.assert_not_called()

    def test_sin_fallos_ni_nada_sin_guardar_no_culpa_a_la_ia(self):
        """Cero veredictos con pendientes, pero la IA no falló ni la base dejó de
        guardar: no había qué mandar cuando corrió (otra captura trajo rivales justo
        entonces). «La IA no contestó» es solo para los fallos del proveedor."""
        self._como_admin()
        with self._ambiente() as t:
            t.juzgar.return_value = {"detenido": None, "veredictos": 0, "sin_juzgar": 0,
                                     "fallidos": 0}
            t.resumen_sku.return_value = {**t.resumen, "pendientes": 3, "completo": False,
                                          "sin_titulo": False}
            r = self._pedir("POST", f"{BASE}/juzgar", {"sku": SKU})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["veredictos"], r.json()["juez"]["pendientes"]), (0, 3))

    def test_mejorar_arranca_el_trabajo_y_devuelve_su_jid(self):
        """No mide en línea: una búsqueda de Apify tarda minutos y la petición
        del navegador se caería con el trabajo ya pagado."""
        self._como_admin()
        with self._ambiente() as t:
            r = self._pedir("POST", f"{BASE}/mejorar", {"sku": SKU})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], "abc123")
        t.arrancar.assert_called_once_with(SKU)
        t.juzgar.assert_not_called()

    def test_un_sku_vacio_da_422_y_no_arranca_nada(self):
        """Río abajo, un SKU vacío no es «ninguno» sino «todos»: `elegibles([''])`
        se queda sin filtro y la mejora corría —y pagaba— sobre el catálogo entero,
        respondiendo `ok` para el SKU ''. El panel nunca lo manda; la API sí podía."""
        self._como_admin()
        for vacio in ("", "   "):
            for ruta in (f"{BASE}/mejorar", f"{BASE}/juzgar"):
                with self.subTest(sku=repr(vacio), ruta=ruta), self._ambiente() as t:
                    r = self._pedir("POST", ruta, {"sku": vacio})
                    self.assertEqual(r.status_code, 422)
                    self._nada_corrio(t)

    def test_el_sku_llega_sin_espacios_alrededor(self):
        self._como_admin()
        with self._ambiente() as t:
            self._pedir("POST", f"{BASE}/mejorar", {"sku": f"  {SKU} "})
        t.arrancar.assert_called_once_with(SKU)

    def test_trabajo_devuelve_el_estado_o_404_si_ya_no_existe(self):
        """Es de solo lectura sobre memoria: no exige la bandera de escritura,
        pero sí el candado de admin (ver arriba)."""
        self._como_admin()
        with self._ambiente(escritura=False) as t:
            self.assertEqual(self._pedir("GET", f"{BASE}/trabajo/x", None).status_code, 404)
            t.estado.assert_called_once_with("x")
            t.estado.return_value = {"id": "x", "paso": "mejorando", "sku": SKU}
            r = self._pedir("GET", f"{BASE}/trabajo/x", None)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["paso"], "mejorando")

    def test_aceptar_pasa_el_id_y_quien_acepta(self):
        """Aceptar mueve el precio de mercado que ven los KAM: queda con nombre."""
        self._como_admin()
        with self._ambiente() as t:
            r = self._pedir("POST", f"{BASE}/sugerencias/7/aceptar", None)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "sku": SKU, "termino": "soporte pared camara"})
        t.aceptar.assert_called_once_with(7, "admin@prueba")
        t.descartar.assert_not_called()

    def test_aceptar_devuelve_el_codigo_que_dio_la_mejora(self):
        self._como_admin()
        casos = [(404, "Esa sugerencia no existe."),
                 (409, "El término del SKU cambió después de la sugerencia; se descartó.")]
        for codigo, motivo in casos:
            with self.subTest(codigo=codigo), self._ambiente() as t:
                t.aceptar.return_value = {"ok": False, "codigo": codigo, "motivo": motivo}
                r = self._pedir("POST", f"{BASE}/sugerencias/7/aceptar", None)
                self.assertEqual(r.status_code, codigo)
                self.assertEqual(r.json()["detail"], motivo)

    def test_descartar_pasa_el_id_y_quien_descarta(self):
        self._como_admin()
        with self._ambiente() as t:
            r = self._pedir("POST", f"{BASE}/sugerencias/7/descartar", None)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        t.descartar.assert_called_once_with(7, "admin@prueba")
        t.aceptar.assert_not_called()

    def test_descartar_una_sugerencia_cerrada_da_409(self):
        self._como_admin()
        with self._ambiente() as t:
            t.descartar.return_value = {"ok": False, "codigo": 409,
                                        "motivo": "Esa sugerencia ya no está abierta."}
            r = self._pedir("POST", f"{BASE}/sugerencias/7/descartar", None)
        self.assertEqual(r.status_code, 409)

    def test_un_id_que_no_es_numero_no_llega_a_la_mejora(self):
        self._como_admin()
        with self._ambiente() as t:
            r = self._pedir("POST", f"{BASE}/sugerencias/abc/aceptar", None)
        self.assertEqual(r.status_code, 422)
        t.aceptar.assert_not_called()


# ── (d) La llave de Apify y los logs ─────────────────────────────────────────

# Corre en un proceso APARTE: importar `main` configura el registro del proceso
# entero (`basicConfig` a INFO) y ensuciaría la salida de todas las demás suites.
# Dentro, una petición a Apify por un transporte falso —sin red— con la llave en
# la URL, como la manda `competencia_scraper._correr_actor`.
_SONDA_LOGS = r"""
import json, logging
import httpx
import main  # noqa: F401  (el registro queda como en Railway)
vistos = []
class Anotar(logging.Handler):
    def emit(self, rec):
        vistos.append(rec.getMessage())
logging.getLogger().addHandler(Anotar(level=logging.DEBUG))
logging.getLogger("omnicanal.sonda").info("la sonda si escucha INFO")
falso = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
with httpx.Client(transport=falso) as cli:
    cli.post("https://api.apify.com/v2/acts/actor~x/runs",
             params={"token": "apify_api_LLAVE_FALSA", "memory": 2048})
print(json.dumps(vistos))
"""


class LaLlaveDeApifyNoVaALosLogs(unittest.TestCase):
    """«Buscar mejor término» mide en Apify desde el backend, y la llave de Apify
    viaja en la URL. Con el registro en INFO, httpx escribía cada petición con su
    URL completa: tres líneas con la llave por medición en los logs de Railway (el
    botón Medir y el cron ya lo hacían; la mejora sumaba un disparador)."""

    def test_httpx_no_escribe_la_url_con_la_llave(self):
        corrida = subprocess.run(
            [sys.executable, "-c", _SONDA_LOGS], cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, encoding="utf-8", timeout=120, env=os.environ.copy())
        self.assertEqual(corrida.returncode, 0, corrida.stderr[-2000:])
        vistos = json.loads(corrida.stdout.strip().splitlines()[-1])
        self.assertIn("la sonda si escucha INFO", vistos, "sin esto la prueba no probaría nada")
        self.assertFalse([m for m in vistos if "LLAVE_FALSA" in m], vistos)


if __name__ == "__main__":
    unittest.main(verbosity=2)
