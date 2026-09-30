"""Pruebas de `competencia_juez`: ¿este «rival» compite de verdad con NUESTRO
producto?

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
La búsqueda de Mercado Libre devuelve lo que comparte PALABRAS con el término, no
lo que compite con el producto: en el examen de 90 SKUs la mitad de los «rivales»
guardados no servía para comparar precio. El juez le pregunta a un LLM, y un LLM
falla de maneras que una regla no: se salta números, inventa otros, renumera,
escribe «Refacción» con acento, se queda sin tokens a media lista. Si cualquiera
de esas cosas llegara a la tabla, la pantalla afirmaría «este rival es el mismo
producto» sobre el rival de al lado.

Estas pruebas amarran lo que lo impide:

  1. EL MENSAJE. Rivales numerados 1..N (al modelo nunca se le pide repetir un
     id), sin el término buscado (el veredicto es de la pareja de productos) y
     sin precio (un competidor barato sigue siendo competidor).
  2. LA LECTURA NO INVENTA. Clase fuera del catálogo, índice con decimales, cero
     o negativo, unidades ilegibles de un `mismo`/`otro_paquete`: fuera. Índice
     que nadie pidió, repetido con otra clase, o un 0 sin el N (numeró desde
     cero): el lote es SOSPECHOSO —el modelo renumeró— y se tira entero tras un
     reintento.
  3. LA CUENTA DE PAQUETES LA HACE EL CÓDIGO, no el modelo, y con UN solo divisor
     por SKU aunque la lista se parta en varias llamadas o se mida otro término.
  4. «SIN JUZGAR» ES «NO SÉ». `pendiente`, `comparable` y `completo` salen de un
     solo lugar; con pendientes nadie puede afirmar cuántos rivales reales hay.
  5. EL GASTO TIENE TOPE, y el tope es ACUMULATIVO: presupuesto, plazo de reloj,
     modelo con precio y cinco fallos seguidos detienen la corrida.
  6. UN SKU NO TUMBA A LOS DEMÁS, y nunca hay un cursor abierto mientras se habla
     con el LLM (el pool tiene 6 conexiones para todo el backend).

── NO SE LLAMA A LA IA NI A LA BASE ────────────────────────────────────────────
`ia_json.completar_json` y `supabase_db` van suplantados. Los títulos son
INVENTADOS y genéricos: el repo es público y aquí no van rivales reales.

    cd backend && python -m unittest tests.test_competencia_juez -v
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import sys
import threading
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_juez as J  # noqa: E402

MODELO = "deepseek-flash"
NUESTRO = "Lámpara de escritorio LED plegable"
CATEGORIA = "Iluminación"


# ── Respuestas del LLM, suplantadas ──────────────────────────────────────────

def v(i, clase="mismo", unidades=1, razon="mismo tipo de lámpara"):
    return {"i": i, "clase": clase, "unidades": unidades, "razon": razon}


def respuesta(veredictos, un=1, usd=0.001, **extra):
    """Lo que devuelve `ia_json.completar_json` cuando todo sale bien."""
    return {"ok": True, "texto": "", "cortado": False, "proveedor": "deepseek", "modelo": MODELO,
            "datos": {"nuestro": "lámpara de escritorio plegable", "unidades_nuestras": un,
                      "veredictos": veredictos},
            "uso": {"entrada": 100, "cache": 0, "salida": 50}, "usd": usd, **extra}


def fallo(motivo="DeepSeek no respondió (HTTP 503: upstream).", **extra):
    return {"ok": False, "motivo": motivo, "proveedor": "deepseek", "modelo": MODELO,
            "texto": "", "cortado": False, "uso": {}, "usd": 0.0, **extra}


def n_rivales(user: str) -> int:
    return int(re.search(r"RIVALES \((\d+)\)", user).group(1))


def numerados(user: str) -> dict[int, str]:
    """Las líneas «N. título» del mensaje, tal como las ve el modelo."""
    return {int(i): t for i, t in re.findall(r"^(\d+)\. (.*)$", user, re.MULTILINE)}


def contesta(clase="mismo", un=1, usd=0.001, unidades=1):
    """Un modelo obediente: un veredicto por cada número de la lista."""
    def falso(system, user, **kw):
        return respuesta([v(i, clase, unidades) for i in range(1, n_rivales(user) + 1)],
                         un=un, usd=usd)
    return falso


def rivales(n: int) -> list[dict]:
    return [{"titulo": f"Lámpara de escritorio modelo {i}", "precio": 200.0 + i}
            for i in range(1, n + 1)]


def juzgar(lista, llm, **kw):
    kw.setdefault("modelo", MODELO)
    with mock.patch.object(J.ia_json, "completar_json", llm):
        return J.juzgar_lote({"titulo": NUESTRO, "categoria": CATEGORIA}, lista, **kw)


# ── La base, suplantada ──────────────────────────────────────────────────────

def fila(sku="SKU-A", externo_id="RIVAL-01", *, titulo="Lámpara de escritorio con brazo flexible",
         precio=300.0, es_nuestro=False, vigente=False, clase=None, version=None,
         titulo_nuestro=NUESTRO, unidades_nuestras=None, unidades_rival=None, juzgado_en=None,
         termino_id=11, posicion=1, unidades_sku=None):
    """Una fila CRUDA de `_SQL_FILAS`: un rival sin veredicto, salvo que se diga.
    `unidades_sku` es el divisor que la base encontró en TODO el SKU (título de
    hoy y versión vigente), no el de esta fila."""
    return {"sku": sku, "termino_id": termino_id, "externo_id": externo_id, "posicion": posicion,
            "titulo": titulo, "precio": precio, "es_nuestro": es_nuestro,
            "capturado_en": dt.datetime(2026, 9, 30, 12, 0), "titulo_nuestro": titulo_nuestro,
            "categoria_nombre": CATEGORIA, "clase": clase, "motivo": None,
            "unidades_nuestras": unidades_nuestras, "unidades_rival": unidades_rival,
            "juzgado_en": juzgado_en, "modelo": MODELO if clase else None,
            "version_prompt": version, "vigente": vigente, "unidades_sku": unidades_sku}


def juzgada(sku="SKU-A", externo_id="RIVAL-01", clase="mismo", **kw):
    """Una fila con veredicto vigente y de la versión actual del prompt."""
    kw.setdefault("version", J.VERSION_PROMPT)
    kw.setdefault("juzgado_en", dt.datetime(2026, 9, 30, 13, 0))
    return fila(sku, externo_id, vigente=True, clase=clase, **kw)


def leer_filas(crudas, skus=("SKU-A",), termino_id=None):
    with mock.patch.object(J.supabase_db, "fetch_all", return_value=crudas) as leer:
        return J.filas(skus, termino_id), leer


class BaseFalsa:
    """`supabase_db` suplantada: lo que devuelve la lectura y lo que se escribe."""

    def __init__(self, filas):
        self.filas = filas
        self.lecturas = []           # (sql, params) de cada fetch_all
        self.transacciones = []      # una lista de (sql, params) por transacción CONFIRMADA
        self.bitacora = []           # (sql, params) de execute: ops.process_log
        self.abiertos = 0            # cursores abiertos en este instante
        self.no_guarda = set()       # SKUs cuya transacción truena

    def fetch_all(self, sql, params=None):
        self.lecturas.append((sql, params))
        return [dict(f) for f in self.filas if f["sku"] in params["skus"]]

    @contextmanager
    def get_cursor(self):
        tx = []

        def ejecutar(sql, params=None):
            if self.no_guarda & {p for p in params or () if isinstance(p, str)}:
                raise RuntimeError("connection already closed")
            tx.append((sql, params))

        cur = mock.Mock()
        cur.execute.side_effect = ejecutar
        self.abiertos += 1
        try:
            yield cur
            self.transacciones.append(tx)
        finally:
            self.abiertos -= 1

    def execute(self, sql, params=None):
        self.bitacora.append((sql, params))
        return 1

    def inserts(self):
        return [p for tx in self.transacciones for sql, p in tx if "insert into" in sql]


def correr(base, llm, skus, **kw):
    kw.setdefault("presupuesto", J.Presupuesto(1.0))
    kw.setdefault("modelo", MODELO)
    kw.setdefault("hilos", 1)
    with mock.patch.object(J.supabase_db, "fetch_all", base.fetch_all), \
         mock.patch.object(J.supabase_db, "get_cursor", base.get_cursor), \
         mock.patch.object(J.supabase_db, "execute", base.execute), \
         mock.patch.object(J.ia_json, "completar_json", llm):
        return J.juzgar_skus(skus, **kw)


def pendientes_de(*skus, por_sku=2):
    """`por_sku` rivales sin juzgar para cada SKU, con un título nuestro que
    nombra al SKU (así un LLM suplantado puede saber a quién está juzgando)."""
    return [fila(s, f"RIVAL-{s}-{k}", titulo=f"Lámpara de escritorio modelo {k}", posicion=k,
                 titulo_nuestro=f"{NUESTRO} {s}")
            for s in skus for k in range(1, por_sku + 1)]


# ── El mensaje ───────────────────────────────────────────────────────────────

class ArmarUsuario(unittest.TestCase):
    def test_numera_de_1_a_n_en_orden(self):
        """El cruce de vuelta es por POSICIÓN en la lista: al modelo no se le pide
        repetir un id, porque los «normaliza» al copiarlos y el cruce se pierde."""
        msg = J.armar_usuario({"titulo": NUESTRO}, rivales(4))
        self.assertEqual(numerados(msg), {i: f"Lámpara de escritorio modelo {i}"
                                          for i in range(1, 5)})
        self.assertIn("RIVALES (4)", msg)
        self.assertIn(f"titulo: {NUESTRO}", msg)

    def test_no_viajan_ids_ni_vendedor(self):
        lista = [{"titulo": "Lámpara de mesa", "externo_id": "RIVAL-SECRETO",
                  "seller": "VENDEDOR-INVENTADO", "precio": 10}]
        msg = J.armar_usuario({"titulo": NUESTRO}, lista)
        self.assertNotIn("RIVAL-SECRETO", msg)
        self.assertNotIn("VENDEDOR-INVENTADO", msg)

    def test_no_lleva_el_termino_buscado(self):
        """El veredicto se reusa si el rival reaparece bajo OTRO término: con el
        término adentro se habría emitido con un dato que la llave ignora."""
        msg = J.armar_usuario({"titulo": NUESTRO, "termino": "termino-que-no-viaja",
                               "termino_general": "termino-que-no-viaja"}, rivales(2))
        self.assertNotIn("termino-que-no-viaja", msg)
        self.assertNotIn("termino", msg.lower().replace("unidades_nuestras", ""))

    def test_no_lleva_precio_por_omision(self):
        """Ni el nuestro ni el de los rivales. El precio no descalifica: con él a
        la vista el modelo tiende a marcar «otra gama» lo que solo es más barato."""
        msg = J.armar_usuario({"titulo": NUESTRO, "precio": 1234.0},
                              [{"titulo": "Lámpara de mesa", "precio": 987.0}])
        self.assertNotIn("$", msg)
        self.assertNotIn("precio", msg)
        self.assertNotIn("1,234", msg)
        self.assertNotIn("987", msg)

    def test_con_precio_solo_si_se_pide(self):
        """Existe para medir en el examen si el precio ayuda; producción no lo usa."""
        msg = J.armar_usuario({"titulo": NUESTRO, "precio": 1234.0},
                              [{"titulo": "Lámpara de mesa", "precio": 987.0}], con_precio=True)
        self.assertIn("precio: $1,234 MXN", msg)
        self.assertIn("1. $987 | Lámpara de mesa", msg)

    def test_lleva_la_categoria(self):
        msg = J.armar_usuario({"titulo": NUESTRO, "categoria": CATEGORIA}, rivales(1))
        self.assertIn(f"categoria: {CATEGORIA}", msg)
        self.assertNotIn("categoria:", J.armar_usuario({"titulo": NUESTRO}, rivales(1)))

    def test_lleva_las_unidades_como_dato_fijo(self):
        """Así todos los trozos de un SKU —y todas sus recapturas— usan el mismo
        divisor en vez de que cada llamada lo adivine por su cuenta."""
        msg = J.armar_usuario({"titulo": NUESTRO}, rivales(1), 3)
        self.assertIn("unidades_nuestras (dato fijo): 3", msg)
        self.assertNotIn("dato fijo", J.armar_usuario({"titulo": NUESTRO}, rivales(1)))
        self.assertNotIn("dato fijo", J.armar_usuario({"titulo": NUESTRO}, rivales(1), None))

    def test_recorta_titulos_largos_y_aguanta_vacios(self):
        msg = J.armar_usuario({"titulo": None}, [{"titulo": "x" * 500}, {"titulo": None}, {}])
        self.assertIn("(sin título)", msg)
        self.assertEqual(numerados(msg), {1: "x" * 160, 2: "", 3: ""})


# ── La lectura de la respuesta ───────────────────────────────────────────────

class Entero(unittest.TestCase):
    def test_un_3_9_no_es_un_3(self):
        """`int(3.9)` da 3: un índice con decimales se colaría como el rival 3."""
        for malo in (3.9, "3.9", 0, -1, "0", "-2", True, False, None, "", "dos", [], 100_000):
            self.assertIsNone(J._entero(malo), repr(malo))

    def test_lo_que_si_es_un_entero(self):
        for bueno, esperado in ((1, 1), (12, 12), ("7", 7), (" 7 ", 7), (2.0, 2), (99_999, 99_999)):
            self.assertEqual(J._entero(bueno), esperado, repr(bueno))


class LeerVeredictos(unittest.TestCase):
    def leer(self, veredictos, n=3, un=1):
        return J.leer_veredictos(respuesta(veredictos, un=un), n)

    def test_objeto_limpio(self):
        r = self.leer([v(1, "mismo", 1, "lámpara igual"), v(2, "refaccion", 1, "es un foco"),
                       v(3, "otra_gama", 1, "es de techo")], un=2)
        self.assertEqual(r["unidades_nuestras"], 2)
        self.assertIs(r["sospechoso"], False)
        self.assertEqual(r["veredictos"], {
            1: {"clase": "mismo", "unidades_rival": 1, "motivo": "lámpara igual"},
            2: {"clase": "refaccion", "unidades_rival": 1, "motivo": "es un foco"},
            3: {"clase": "otra_gama", "unidades_rival": 1, "motivo": "es de techo"}})

    def test_clase_con_acento_mayusculas_o_espacio(self):
        """El modelo escribe la clase como la diría, no como está en el catálogo."""
        r = self.leer([v(1, "Refacción"), v(2, "otro paquete"), v(3, "  OTRA   Gama ")])
        self.assertEqual([r["veredictos"][i]["clase"] for i in (1, 2, 3)],
                         ["refaccion", "otro_paquete", "otra_gama"])
        self.assertIs(r["sospechoso"], False)

    def test_clase_fuera_del_catalogo_se_descarta(self):
        """Queda SIN JUZGAR: no se adivina la más parecida. Y no es sospechoso:
        una palabra rara no indica que el resto de la lista esté corrido."""
        r = self.leer([v(1, "mismo"), v(2, "parecido"), v(3, None), v(3, "")])
        self.assertEqual(list(r["veredictos"]), [1])
        self.assertIs(r["sospechoso"], False)

    def test_todas_las_clases_del_catalogo_pasan(self):
        r = J.leer_veredictos(respuesta([v(i, c) for i, c in enumerate(J.CLASES, 1)]),
                              len(J.CLASES))
        self.assertEqual([x["clase"] for x in r["veredictos"].values()], list(J.CLASES))

    def test_indice_con_decimales_cero_o_negativo_se_rechaza(self):
        """Ninguno entra. Con el N presente tampoco es sospechoso: un 0 junto a
        1..N es el modelo dándole número a nuestro producto (ver abajo)."""
        r = self.leer([v(1.5), v(0), v(-1), v(True), v("dos"), v(None), v(2), v(3)])
        self.assertEqual(list(r["veredictos"]), [2, 3])
        self.assertIs(r["sospechoso"], False)

    def test_indice_como_texto_o_float_entero_pasa(self):
        r = self.leer([v("1"), v(2.0)])
        self.assertEqual(list(r["veredictos"]), [1, 2])

    def test_indice_fuera_de_rango_marca_sospechoso(self):
        """Un número que nadie pidió = el modelo renumeró (contó nuestro
        producto, se saltó uno): el resto puede estar corrido."""
        r = self.leer([v(1), v(2), v(4)], n=3)
        self.assertIs(r["sospechoso"], True)
        self.assertNotIn(4, r["veredictos"])

    def test_numerar_desde_cero_marca_sospechoso(self):
        """0..N-1 no deja ningún número fuera de rango: sin esto los veredictos
        se guardaban corridos un lugar (el del rival 1 en el 2...) y el último
        rival quedaba «sin contestar». La huella es el 0 y que falte el N."""
        for i0 in (0, "0", 0.0, -1):
            with self.subTest(primero=i0):
                r = self.leer([v(i0, "otro_producto"), v(1), v(2, "refaccion")], n=3)
                self.assertIs(r["sospechoso"], True)

    def test_un_cero_con_la_lista_completa_solo_se_ignora(self):
        """El modelo le puso número a nuestro producto y luego contestó 1..N
        bien: tirar ese lote sería tirar uno bueno, y el reintento manda la
        petición idéntica (ese SKU no se juzgaría nunca)."""
        r = self.leer([v(0, "mismo"), v(1, "otro_producto"), v(2), v(3, "refaccion")], n=3)
        self.assertIs(r["sospechoso"], False)
        self.assertEqual([r["veredictos"][i]["clase"] for i in (1, 2, 3)],
                         ["otro_producto", "mismo", "refaccion"])

    def test_numerar_desde_cero_se_reintenta_y_no_se_guarda_corrido(self):
        """El caso completo por `juzgar_lote`: la verdad de los rivales 1..4 es
        [otro_producto, mismo, mismo, refaccion] y el modelo contesta con 0..3."""
        reales = ["otro_producto", "mismo", "mismo", "refaccion"]
        llm = mock.Mock(return_value=respuesta([v(i, reales[i]) for i in range(4)]))
        r = juzgar(rivales(4), llm)
        self.assertEqual(llm.call_count, 2, "sospechoso: un reintento")
        self.assertEqual((r["veredictos"], r["faltan"]), ({}, [1, 2, 3, 4]))
        self.assertIn("renumer", r["motivo"])

    def test_numerar_desde_cero_tambien_se_ve_en_el_rescate(self):
        cortada = ('{"veredictos": [{"i": 0, "clase": "otro_producto"}, '
                   '{"i": 1, "clase": "mismo"}, {"i": 2, "clase": "mismo"}, {"i": 3, "cla')
        r = J.leer_veredictos(fallo("respuesta cortada", texto=cortada, cortado=True), 4)
        self.assertIs(r["sospechoso"], True)

    def test_repetido_con_clase_distinta_marca_sospechoso(self):
        r = self.leer([v(1, "mismo"), v(1, "refaccion"), v(2)])
        self.assertIs(r["sospechoso"], True)
        self.assertEqual(r["veredictos"][1]["clase"], "mismo", "se queda el primero")

    def test_repetido_igual_no_es_sospechoso(self):
        r = self.leer([v(1, "mismo"), v(1, "Mismo"), v(2)])
        self.assertIs(r["sospechoso"], False)
        self.assertEqual(list(r["veredictos"]), [1, 2])

    def test_lo_que_falta_queda_fuera_sin_inventarse(self):
        r = self.leer([v(1), v(3)], n=3)
        self.assertEqual(list(r["veredictos"]), [1, 3])
        self.assertIs(r["sospechoso"], False)

    def test_unidades_y_razon_se_limpian(self):
        """En las clases que no se cuentan por unidades, un número raro solo se
        pierde (se tiraría de todas formas): el veredicto sigue valiendo."""
        r = self.leer([v(1, "refaccion", unidades="3.9", razon="  " + "x" * 400),
                       v(2, "otra_gama", unidades=0, razon=None), v(3, unidades="4")])
        self.assertIsNone(r["veredictos"][1]["unidades_rival"])
        self.assertEqual(len(r["veredictos"][1]["motivo"]), 160)
        self.assertIsNone(r["veredictos"][2]["unidades_rival"])
        self.assertEqual(r["veredictos"][2]["motivo"], "")
        self.assertEqual(r["veredictos"][3]["unidades_rival"], 4)

    def test_mismo_con_unidades_ilegibles_queda_sin_juzgar(self):
        """«No dijo» vale 1; «dijo algo que no es un entero» no se sabe. Leído
        como 1, un paquete de 3 («3 pzs») salía `mismo` y comparable."""
        for mala in ("3 pzs", 0, 2.5, "", "varias", -2):
            with self.subTest(unidades=mala):
                r = self.leer([v(1, "mismo", unidades=mala), v(2, "mismo", unidades=None),
                               v(3, "mismo", unidades=2)])
                self.assertEqual(sorted(r["veredictos"]), [2, 3])
                self.assertIsNone(r["veredictos"][2]["unidades_rival"], "ausente: no se inventa")
                self.assertIs(r["sospechoso"], False)

    def test_otro_paquete_sin_unidades_legibles_queda_sin_juzgar(self):
        """Sin unidades no hay con qué contarlo: tomarlo como 1 lo volvía `mismo`
        (comparable) tirando la única señal de que era otro paquete."""
        for mala in (None, "muchas", 0):
            with self.subTest(unidades=mala):
                r = self.leer([v(1, "otro_paquete", unidades=mala),
                               v(2, "otro_paquete", unidades=6)], n=2)
                self.assertEqual(list(r["veredictos"]), [2])
        sin_llave = {"i": 1, "clase": "otro_paquete", "razon": "trae varias"}
        self.assertEqual(self.leer([sin_llave], n=1)["veredictos"], {})

    def test_un_numero_ilegible_repetido_sigue_contando_como_renumerar(self):
        """El veredicto que se descarta por unidades sigue siendo «lo que dijo del
        rival 1»: si luego dice otra clase para el 1, el lote es sospechoso."""
        r = self.leer([v(1, "mismo", unidades="3 pzs"), v(1, "refaccion"), v(2), v(3)])
        self.assertIs(r["sospechoso"], True)

    def test_formas_raras_no_revientan(self):
        for datos in ({}, {"veredictos": None}, {"veredictos": {"1": "mismo"}},
                      {"veredictos": ["mismo", None, 3, ["i", 1]]},
                      {"veredictos": [{"clase": "mismo"}], "unidades_nuestras": "muchas"}):
            r = J.leer_veredictos({"ok": True, "datos": datos}, 3)
            self.assertEqual(r, {"unidades_nuestras": None, "veredictos": {},
                                 "sospechoso": False}, datos)

    def test_un_fallo_sin_texto_no_trae_nada(self):
        r = J.leer_veredictos(fallo(), 3)
        self.assertEqual(r, {"unidades_nuestras": None, "veredictos": {}, "sospechoso": False})

    def test_rescata_lo_que_cerro_de_una_respuesta_cortada(self):
        """12 de 18 es mejor que tirar el lote ya pagado: se recuperan las
        entradas enteras y las unidades nuestras, y la que quedó a medias no."""
        cortada = ('{"nuestro": "lámpara de escritorio plegable", "unidades_nuestras": 2, '
                   '"veredictos": [{"i": 1, "clase": "mismo", "unidades": 2, "razon": "igual"}, '
                   '{"i": 2, "clase": "Refacción", "unidades": 1, "razon": "es un foco"}, '
                   '{"i": 3, "clase": "mis')
        r = J.leer_veredictos(fallo("respuesta cortada", texto=cortada, cortado=True), 3)
        self.assertEqual(r["unidades_nuestras"], 2)
        self.assertEqual(r["veredictos"], {
            1: {"clase": "mismo", "unidades_rival": 2, "motivo": "igual"},
            2: {"clase": "refaccion", "unidades_rival": 1, "motivo": "es un foco"}})
        self.assertIs(r["sospechoso"], False)

    def test_el_rescate_pasa_por_la_misma_validacion(self):
        cortada = ('{"veredictos": [{"i": 1, "clase": "mismo"}, {"i": 9, "clase": "mismo"}, '
                   '{"i": 2, "clase": "inventada"}, {"i": 3, "cla')
        r = J.leer_veredictos(fallo("respuesta cortada", texto=cortada, cortado=True), 3)
        self.assertEqual(list(r["veredictos"]), [1])
        self.assertIs(r["sospechoso"], True, "el 9 no estaba en la lista")
        self.assertIsNone(r["unidades_nuestras"])


class AplicarUnidades(unittest.TestCase):
    """La cuenta de paquetes la hace el CÓDIGO. Al modelo se le pide «mismo» más
    las unidades; si además opina sobre el paquete, su opinión no manda."""

    def aplicar(self, clase, unidades_rival, un):
        vs = {1: {"clase": clase, "unidades_rival": unidades_rival, "motivo": ""}}
        J._aplicar_unidades(vs, un)
        return vs[1]["clase"], vs[1]["unidades_rival"]

    def test_mismo_con_unidades_distintas_pasa_a_otro_paquete(self):
        self.assertEqual(self.aplicar("mismo", 3, 1), ("otro_paquete", 3))
        self.assertEqual(self.aplicar("mismo", 1, 2), ("otro_paquete", 1))

    def test_otro_paquete_con_unidades_iguales_vuelve_a_mismo(self):
        self.assertEqual(self.aplicar("otro_paquete", 2, 2), ("mismo", 2))

    def test_mismo_con_unidades_iguales_se_queda(self):
        self.assertEqual(self.aplicar("mismo", 4, 4), ("mismo", 4))

    def test_otro_paquete_de_verdad_se_queda(self):
        self.assertEqual(self.aplicar("otro_paquete", 12, 1), ("otro_paquete", 12))

    def test_unidades_nulas_valen_1(self):
        """Un título que no dice cantidad es UNA pieza: el caso normal."""
        self.assertEqual(self.aplicar("mismo", None, None), ("mismo", 1))
        self.assertEqual(self.aplicar("mismo", None, 1), ("mismo", 1))
        self.assertEqual(self.aplicar("mismo", 1, None), ("mismo", 1))
        self.assertEqual(self.aplicar("mismo", None, 2), ("otro_paquete", 1))
        self.assertEqual(self.aplicar("mismo", 2, None), ("otro_paquete", 2))

    def test_las_demas_clases_pierden_las_unidades(self):
        """«3 focos de repuesto» no son 3 unidades de nuestra lámpara: guardarlas
        invitaría a alguien a dividir un precio que no se puede comparar."""
        for clase in ("otra_gama", "refaccion", "otro_producto", "dudoso"):
            self.assertEqual(self.aplicar(clase, 3, 1), (clase, None))
            self.assertEqual(self.aplicar(clase, 1, 1), (clase, None))


class EsComparable(unittest.TestCase):
    def test_solo_mismo(self):
        """`otro_paquete` se guarda y se enseña, pero NO entra a un mínimo ni a una
        mediana: el precio por pieza no es lineal."""
        for clase in J.CLASES:
            self.assertEqual(J.es_comparable(clase), clase == "mismo", clase)
        for otro in (None, "", "Mismo", "otro"):
            self.assertIs(J.es_comparable(otro), False)


# ── Un lote: trozos, reintento y sumas ───────────────────────────────────────

class JuzgarLote(unittest.TestCase):
    def test_parte_en_trozos_y_reindexa(self):
        """Cada trozo se numera desde 1; el veredicto tiene que volver a SU rival
        en la lista completa. El modelo suplantado devuelve como razón el título
        que vio en cada número: si el reindexado se corriera uno, no cuadraría."""
        def eco(system, user, **kw):
            return respuesta([v(i, razon=t) for i, t in numerados(user).items()])

        llm = mock.Mock(side_effect=eco)
        n = 2 * J.TROZO + 3
        r = juzgar(rivales(n), llm)
        self.assertEqual(llm.call_count, 3)
        self.assertEqual([n_rivales(c.args[1]) for c in llm.call_args_list],
                         [J.TROZO, J.TROZO, 3])
        self.assertEqual(sorted(r["veredictos"]), list(range(1, n + 1)))
        for i in range(1, n + 1):
            self.assertEqual(r["veredictos"][i]["motivo"], f"Lámpara de escritorio modelo {i}")
        self.assertEqual(r["faltan"], [])
        self.assertIs(r["ok"], True)
        self.assertEqual(r["llamadas"], 3)

    def test_un_trozo_justo_es_una_sola_llamada(self):
        llm = mock.Mock(side_effect=contesta())
        self.assertEqual(juzgar(rivales(J.TROZO), llm)["llamadas"], 1)
        self.assertEqual(juzgar(rivales(J.TROZO + 1), llm)["llamadas"], 2)

    def test_las_unidades_del_primer_trozo_viajan_al_segundo(self):
        """Un SKU, un divisor. El segundo trozo contesta otra cosa (1) a propósito:
        no manda, porque el dato ya quedó fijo en el primero."""
        def falso(system, user, **kw):
            un = 1 if "dato fijo" in user else 2
            return respuesta([v(i, "mismo", 2) for i in range(1, n_rivales(user) + 1)], un=un)

        llm = mock.Mock(side_effect=falso)
        r = juzgar(rivales(J.TROZO + 2), llm)
        primero, segundo = (c.args[1] for c in llm.call_args_list)
        self.assertNotIn("dato fijo", primero)
        self.assertIn("unidades_nuestras (dato fijo): 2", segundo)
        self.assertEqual(r["unidades_nuestras"], 2)
        self.assertEqual({x["clase"] for x in r["veredictos"].values()}, {"mismo"},
                         "rivales de 2 piezas contra las 2 nuestras, en los dos trozos")

    def test_las_unidades_ya_establecidas_mandan_sobre_el_modelo(self):
        llm = mock.Mock(side_effect=contesta(un=1, unidades=3))
        r = juzgar(rivales(2), llm, unidades_nuestras=3)
        self.assertIn("unidades_nuestras (dato fijo): 3", llm.call_args.args[1])
        self.assertEqual(r["unidades_nuestras"], 3)
        self.assertEqual(r["veredictos"][1]["clase"], "mismo")

    def test_sin_unidades_del_modelo_valen_1(self):
        r = juzgar(rivales(2), mock.Mock(side_effect=contesta(un=None, unidades=None)))
        self.assertEqual(r["unidades_nuestras"], 1)
        self.assertEqual(r["veredictos"][1], {"clase": "mismo", "unidades_rival": 1,
                                              "motivo": "mismo tipo de lámpara"})

    def test_la_cuenta_de_paquetes_se_aplica(self):
        llm = mock.Mock(return_value=respuesta(
            [v(1, "mismo", 1), v(2, "mismo", 6), v(3, "otro_paquete", 1), v(4, "refaccion", 6)],
            un=1))
        r = juzgar(rivales(4), llm)
        self.assertEqual([r["veredictos"][i]["clase"] for i in (1, 2, 3, 4)],
                         ["mismo", "otro_paquete", "mismo", "refaccion"])
        self.assertIsNone(r["veredictos"][4]["unidades_rival"])

    def test_otro_paquete_sin_unidades_no_se_vuelve_mismo(self):
        """Con nuestras unidades en 1, un `otro_paquete` sin unidades salía `mismo`
        (1 = 1) y contaba como comparable. Ahora queda sin juzgar."""
        llm = mock.Mock(return_value=respuesta(
            [v(1, "otro_paquete", None), v(2, "mismo", "3 pzs"), v(3, "mismo", None)], un=1))
        r = juzgar(rivales(3), llm)
        self.assertEqual(r["faltan"], [1, 2])
        self.assertEqual(r["veredictos"], {3: {"clase": "mismo", "unidades_rival": 1,
                                               "motivo": "mismo tipo de lámpara"}})

    def test_contestadas_distingue_la_respuesta_mala_del_proveedor_caido(self):
        """Con 0, el que falló fue el proveedor; con más, fue lo que contestó."""
        caido = juzgar(rivales(2), mock.Mock(return_value=fallo()))
        malo = juzgar(rivales(2), mock.Mock(return_value=respuesta([v(1, "parecido")])))
        cortado = juzgar(rivales(2), mock.Mock(return_value=fallo(
            "respuesta cortada", texto='{"veredictos": [{"i": 1, "cl', cortado=True)))
        self.assertEqual((caido["contestadas"], malo["contestadas"], cortado["contestadas"]),
                         (0, 1, 1))

    def test_el_turno_se_espera_con_plazo(self):
        """Sin turno en `timeout` segundos, el lote queda sin juzgar con motivo:
        quien pidió 30 s no se queda minutos detrás de llamadas colgadas."""
        ocupado = mock.Mock()
        ocupado.acquire.return_value = False
        llm = mock.Mock(side_effect=contesta())
        with mock.patch.object(J, "_turno", ocupado):
            r = juzgar(rivales(J.TROZO + 2), llm, timeout=30.0)
        ocupado.acquire.assert_called_once_with(timeout=30.0)
        ocupado.release.assert_not_called()
        llm.assert_not_called()
        self.assertEqual((r["veredictos"], r["llamadas"], r["contestadas"], r["ok"]),
                         ({}, 0, 0, False))
        self.assertEqual(r["faltan"], list(range(1, J.TROZO + 3)))
        self.assertEqual(r["motivo"], J.IA_OCUPADA)

    def test_el_turno_se_devuelve_aunque_la_llamada_reviente(self):
        turno = threading.BoundedSemaphore(1)
        with mock.patch.object(J, "_turno", turno), self.assertRaises(RuntimeError):
            juzgar(rivales(1), mock.Mock(side_effect=RuntimeError("imprevisto")))
        self.assertIs(turno.acquire(blocking=False), True, "el turno quedó libre")

    def test_lote_sospechoso_se_reintenta_una_vez(self):
        torcido = respuesta([v(1), v(2), v(3), v(4)])          # el 4 no existe
        limpio = respuesta([v(1), v(2, "refaccion"), v(3)])
        llm = mock.Mock(side_effect=[torcido, limpio])
        r = juzgar(rivales(3), llm)
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(r["llamadas"], 2)
        self.assertEqual([r["veredictos"][i]["clase"] for i in (1, 2, 3)],
                         ["mismo", "refaccion", "mismo"], "vale el reintento, no el torcido")
        self.assertEqual(r["usd"], 0.002, "las dos llamadas se pagaron")

    def test_si_sigue_sospechoso_no_se_guarda_nada(self):
        """Ni siquiera lo que «se veía bien»: si el modelo renumeró, no se sabe a
        qué rival corresponde cada veredicto."""
        torcido = respuesta([v(1, "mismo"), v(1, "refaccion"), v(2), v(3)])
        llm = mock.Mock(return_value=torcido)
        r = juzgar(rivales(3), llm)
        self.assertEqual(llm.call_count, 2, "un reintento, no un ciclo")
        self.assertEqual(r["veredictos"], {})
        self.assertEqual(r["faltan"], [1, 2, 3])
        self.assertIs(r["ok"], False)
        self.assertEqual(r["usd"], 0.002)

    def test_un_trozo_sospechoso_no_tira_los_demas(self):
        def falso(system, user, **kw):
            n = n_rivales(user)
            extra = [v(n + 5)] if n == J.TROZO else []        # solo el primer trozo se tuerce
            return respuesta([v(i) for i in range(1, n + 1)] + extra)

        r = juzgar(rivales(J.TROZO + 2), mock.Mock(side_effect=falso))
        self.assertEqual(r["faltan"], list(range(1, J.TROZO + 1)))
        self.assertEqual(sorted(r["veredictos"]), [J.TROZO + 1, J.TROZO + 2])
        self.assertEqual(r["llamadas"], 3)

    def test_faltan_reporta_lo_no_contestado(self):
        llm = mock.Mock(return_value=respuesta([v(1), v(3)]))
        r = juzgar(rivales(4), llm)
        self.assertEqual(r["faltan"], [2, 4])
        self.assertEqual(sorted(r["veredictos"]), [1, 3])
        self.assertIs(r["ok"], True, "lo contestado sirve y se guarda")
        self.assertEqual(llm.call_count, 1, "saltarse números no es renumerar: no se reintenta")

    def test_proveedor_caido_no_inventa_y_dice_por_que(self):
        llm = mock.Mock(return_value=fallo("DeepSeek no respondió (HTTP 503: upstream)."))
        r = juzgar(rivales(3), llm)
        self.assertIs(r["ok"], False)
        self.assertEqual(r["veredictos"], {})
        self.assertEqual(r["faltan"], [1, 2, 3])
        self.assertIn("503", r["motivo"])
        self.assertEqual(llm.call_count, 1, "los reintentos de red ya los hizo ia_json")
        self.assertEqual(r["usd"], 0.0)

    def test_respuesta_cortada_guarda_lo_que_cerro(self):
        cortada = ('{"unidades_nuestras": 1, "veredictos": [{"i": 1, "clase": "mismo", '
                   '"unidades": 1, "razon": "a"}, {"i": 2, "clase": "dudoso", "unidades": 1, '
                   '"razon": "b"}, {"i": 3, "cl')
        llm = mock.Mock(return_value=fallo("respuesta cortada", texto=cortada, cortado=True,
                                           usd=0.0007, uso={"entrada": 300, "salida": 470}))
        r = juzgar(rivales(3), llm)
        self.assertEqual(sorted(r["veredictos"]), [1, 2])
        self.assertEqual(r["faltan"], [3])
        self.assertIs(r["cortado"], True)
        self.assertEqual(r["motivo"], "respuesta cortada")
        self.assertEqual(r["usd"], 0.0007)

    def test_suma_usd_y_uso_de_todas_las_llamadas(self):
        llm = mock.Mock(side_effect=contesta(usd=0.0012))
        r = juzgar(rivales(2 * J.TROZO + 1), llm)
        self.assertEqual(r["llamadas"], 3)
        self.assertEqual(r["usd"], 0.0036)
        self.assertEqual(r["uso"], {"entrada": 300, "cache": 0, "salida": 150})
        self.assertEqual((r["proveedor"], r["modelo"]), ("deepseek", MODELO))
        self.assertIs(r["cortado"], False)
        self.assertIsNone(r["motivo"], "sin faltantes no hay motivo que reportar")

    def test_sin_rivales_no_se_llama_a_nadie(self):
        llm = mock.Mock()
        r = juzgar([], llm)
        llm.assert_not_called()
        self.assertIs(r["ok"], True)
        self.assertEqual((r["veredictos"], r["faltan"], r["llamadas"], r["usd"]), ({}, [], 0, 0.0))

    def test_lo_que_se_le_pide_al_llm(self):
        """Producción va SIN precio, con el modelo pedido y con el tope de tokens
        creciendo con la lista (una lista larga no debe cortarse por el tope)."""
        llm = mock.Mock(side_effect=contesta())
        juzgar(rivales(5), llm, modelo="deepseek-v4-pro", timeout=30.0, intentos=1)
        (system, user), kw = llm.call_args
        self.assertIs(system, J._SYSTEM)
        self.assertNotIn("$", user)
        self.assertEqual((kw["modelo"], kw["timeout"], kw["intentos"], kw["razonar"]),
                         ("deepseek-v4-pro", 30.0, 1, False))
        chico = kw["max_tokens"]
        juzgar(rivales(J.TROZO), llm)
        self.assertGreater(llm.call_args.kwargs["max_tokens"], chico)


# ── Lectura de filas y resumen ───────────────────────────────────────────────

class Filas(unittest.TestCase):
    def test_sin_skus_no_toca_la_base(self):
        for vacio in ([], [None, ""], iter(())):
            fs, leer = leer_filas([fila()], skus=vacio)
            self.assertEqual(fs, [])
            leer.assert_not_called()

    def test_lo_que_se_le_pide_a_la_base(self):
        _, leer = leer_filas([], skus=["SKU-B", "SKU-A", "SKU-B", None], termino_id=77)
        sql, params = leer.call_args.args
        self.assertEqual(params, {"tid": 77, "canal": "mercado_libre", "skus": ["SKU-A", "SKU-B"],
                                  "version": J.VERSION_PROMPT})
        self.assertIn(J.TABLA_JUICIO, sql)

    def test_cuenta_es_rival_ajeno_con_precio(self):
        fs, _ = leer_filas([fila(), fila(externo_id="R2", es_nuestro=True),
                            fila(externo_id="R3", precio=None), fila(externo_id="R4", precio=0)])
        self.assertEqual([f["cuenta"] for f in fs], [True, False, False, False])

    def test_vigente_es_bool_aunque_la_base_diga_null(self):
        fs, _ = leer_filas([fila(vigente=None), juzgada(externo_id="R2")])
        self.assertEqual([f["vigente"] for f in fs], [False, True])

    def test_pendiente_nunca_juzgado(self):
        fs, _ = leer_filas([fila()])
        self.assertIs(fs[0]["pendiente"], True)
        self.assertIs(fs[0]["comparable"], False, "sin juzgar es «no sé», nunca comparable")

    def test_pendiente_porque_cambio_un_titulo(self):
        """La base devuelve `vigente = false` cuando el título guardado ya no es
        el de hoy: el veredicto existe pero habla de otro texto."""
        fs, _ = leer_filas([fila(clase="mismo", version=J.VERSION_PROMPT, vigente=False)])
        self.assertIs(fs[0]["pendiente"], True)
        self.assertIs(fs[0]["comparable"], False)

    def test_pendiente_por_version_vieja_del_prompt(self):
        """Subir `VERSION_PROMPT` re-juzga todo. La vista SQL no conoce la versión
        y sigue dando el veredicto viejo por vigente hasta que se rejuzga; aquí
        pasa lo mismo («si se toca una, se toca la otra»), pero la fila queda
        PENDIENTE y por eso el SKU deja de estar `completo`."""
        fs, _ = leer_filas([juzgada(version="j0-vieja"), juzgada(externo_id="R2", version=None)])
        self.assertEqual([f["pendiente"] for f in fs], [True, True])
        self.assertEqual([f["vigente"] for f in fs], [True, True])
        self.assertIs(J.resumen(fs)["completo"], False)

    def test_vigente_y_version_actual_no_esta_pendiente(self):
        fs, _ = leer_filas([juzgada(clase="mismo"), juzgada(externo_id="R2", clase="refaccion")])
        self.assertEqual([f["pendiente"] for f in fs], [False, False])
        self.assertEqual([f["comparable"] for f in fs], [True, False])

    def test_lo_que_no_cuenta_nunca_esta_pendiente_ni_es_comparable(self):
        """Nuestra propia publicación y los rivales sin precio no se juzgan (no
        se paga por ellos) y no entran a ningún conteo aunque tengan veredicto."""
        fs, _ = leer_filas([fila(es_nuestro=True), fila(externo_id="R2", precio=None),
                            juzgada(externo_id="R3", es_nuestro=True),
                            juzgada(externo_id="R4", precio=0)])
        self.assertEqual([f["pendiente"] for f in fs], [False] * 4)
        self.assertEqual([f["comparable"] for f in fs], [False] * 4)

    def test_comparable_solo_con_clase_mismo(self):
        fs, _ = leer_filas([juzgada(externo_id=f"R{i}", clase=c) for i, c in enumerate(J.CLASES)])
        self.assertEqual([f["clase"] for f in fs if f["comparable"]], ["mismo"])


class Resumen(unittest.TestCase):
    def resumir(self, crudas):
        fs, _ = leer_filas(crudas)
        return J.resumen(fs)

    def test_conteos(self):
        r = self.resumir([
            juzgada(externo_id="R1", clase="mismo", juzgado_en=dt.datetime(2026, 9, 29, 8, 0)),
            juzgada(externo_id="R2", clase="mismo", juzgado_en=dt.datetime(2026, 9, 30, 9, 0)),
            juzgada(externo_id="R3", clase="refaccion"),
            juzgada(externo_id="R4", clase="otro_paquete", juzgado_en=None),
            fila(externo_id="R5"),                                # sin juzgar
            fila(externo_id="R6", es_nuestro=True),               # nuestra: no cuenta
            juzgada(externo_id="R7", clase="mismo", precio=None),  # sin precio: no cuenta
        ])
        self.assertEqual((r["total"], r["juzgados"], r["comparables"], r["pendientes"]),
                         (5, 4, 2, 1))
        self.assertEqual(r["por_clase"], {"mismo": 2, "refaccion": 1, "otro_paquete": 1})
        self.assertIs(r["completo"], False)
        self.assertEqual(r["juzgado_en"], dt.datetime(2026, 9, 30, 13, 0))

    def test_completo_cuando_no_queda_nada_por_juzgar(self):
        r = self.resumir([juzgada(externo_id="R1", clase="mismo"),
                          juzgada(externo_id="R2", clase="otro_producto"),
                          fila(externo_id="R3", es_nuestro=True)])
        self.assertIs(r["completo"], True)
        self.assertEqual((r["total"], r["comparables"], r["pendientes"]), (2, 1, 0))

    def test_un_solo_pendiente_quita_el_completo(self):
        """Con 9 de 10 juzgados no se puede afirmar «este SKU tiene N
        comparables»: el décimo puede ser el que falta."""
        crudas = [juzgada(externo_id=f"R{i}", clase="otra_gama") for i in range(9)]
        r = self.resumir(crudas + [fila(externo_id="R9")])
        self.assertIs(r["completo"], False)
        self.assertEqual(r["pendientes"], 1)

    def test_sin_rivales_no_es_completo(self):
        """Una búsqueda vacía no es «juzgado y con cero comparables»."""
        for crudas in ([], [fila(es_nuestro=True)], [fila(precio=None)]):
            r = self.resumir(crudas)
            self.assertEqual((r["total"], r["completo"], r["juzgado_en"]), (0, False, None))

    def test_resumen_sku_lee_y_resume(self):
        with mock.patch.object(J.supabase_db, "fetch_all",
                               return_value=[juzgada(), fila(externo_id="R2")]) as leer:
            r = J.resumen_sku("SKU-A", 9)
        self.assertEqual((r["total"], r["comparables"], r["pendientes"]), (2, 1, 1))
        self.assertEqual(leer.call_args.args[1]["skus"], ["SKU-A"])
        self.assertEqual(leer.call_args.args[1]["tid"], 9)


class SkusDeTermino(unittest.TestCase):
    def test_termino_que_no_existe(self):
        with mock.patch.object(J.supabase_db, "fetch_all", return_value=[]):
            self.assertEqual(J.skus_de_termino("lampara escritorio"), (None, []))

    def test_termino_sin_skus_asignados(self):
        """Un candidato recién medido: existe en el catálogo y nadie lo tiene."""
        with mock.patch.object(J.supabase_db, "fetch_all",
                               return_value=[{"id": 7, "sku": None}]):
            self.assertEqual(J.skus_de_termino("lampara escritorio"), (7, []))

    def test_devuelve_id_y_skus_sin_repetir(self):
        crudas = [{"id": 7, "sku": "SKU-B"}, {"id": 7, "sku": "SKU-A"}, {"id": 7, "sku": "SKU-B"}]
        with mock.patch.object(J.supabase_db, "fetch_all", return_value=crudas) as leer:
            self.assertEqual(J.skus_de_termino("  lampara escritorio "),
                             (7, ["SKU-A", "SKU-B"]))
        self.assertEqual(leer.call_args.args[1], ("mercado_libre", "lampara escritorio"))

    def test_juzgar_termino_juzga_a_quien_lo_tiene_asignado(self):
        p = J.Presupuesto(1.0)
        with mock.patch.object(J, "skus_de_termino", return_value=(7, ["SKU-A", "SKU-B"])), \
             mock.patch.object(J, "juzgar_skus", return_value={"veredictos": 3}) as corre:
            r = J.juzgar_termino("lampara escritorio", presupuesto=p, hilos=2, plazo_s=60.0)
        self.assertEqual(r, {"veredictos": 3})
        corre.assert_called_once_with(["SKU-A", "SKU-B"], presupuesto=p, hilos=2, plazo_s=60.0)


# ── Tope de gasto, bitácora y guardia de tabla ───────────────────────────────

class PresupuestoAcumulativo(unittest.TestCase):
    def test_puede_mientras_no_llegue_al_tope(self):
        p = J.Presupuesto(0.01)
        self.assertIs(p.puede(), True)
        p.sumar(0.006)
        self.assertIs(p.puede(), True)
        p.sumar(0.004)
        self.assertIs(p.puede(), False, "llegar al tope ya es no poder")

    def test_tope_cero_o_negativo_nunca_puede(self):
        self.assertIs(J.Presupuesto(0).puede(), False)
        self.assertIs(J.Presupuesto(-1).puede(), False)
        self.assertIs(J.Presupuesto(float("-inf")).puede(), False)

    def test_sumar_aguanta_none(self):
        p = J.Presupuesto(1.0)
        p.sumar(None)
        p.sumar(0)
        self.assertEqual(p.gastado, 0.0)

    def test_no_pierde_sumas_entre_hilos(self):
        p = J.Presupuesto(1000.0)
        arranque = threading.Barrier(8)

        def gastar():
            arranque.wait()
            for _ in range(2000):
                p.sumar(0.25)

        hilos = [threading.Thread(target=gastar) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(p.gastado, 8 * 2000 * 0.25)

    def test_el_mismo_objeto_acumula_entre_corridas(self):
        """Un tope que se estrena en cada llamada nunca se alcanza: el script por
        lotes llama una vez por término, con cientos de términos pendientes."""
        p = J.Presupuesto(0.0015)
        llm = mock.Mock(side_effect=contesta(usd=0.001))
        base = BaseFalsa(pendientes_de("SKU-A", "SKU-B", "SKU-C"))
        r1 = correr(base, llm, ["SKU-A", "SKU-B"], presupuesto=p)
        self.assertIsNone(r1["detenido"])
        self.assertAlmostEqual(p.gastado, 0.002)
        r2 = correr(base, llm, ["SKU-C"], presupuesto=p)
        self.assertEqual(r2["detenido"], "tope de gasto")
        self.assertEqual(llm.call_count, 2, "la segunda corrida no gastó nada")


class Registrar(unittest.TestCase):
    def test_deja_una_fila_con_accion_y_usd(self):
        with mock.patch.object(J.supabase_db, "execute") as ex:
            J.registrar("juez", "ok", {"proveedor": "deepseek", "usd": 0.0123, "skus": 4}, 12.34)
        ex.assert_called_once()
        sql, params = ex.call_args.args
        self.assertIn("ops.process_log", sql)
        self.assertIn("'competencia'", sql)
        self.assertEqual(params[:3], ("deepseek", "juez", "ok"))
        self.assertEqual(json.loads(params[3]), {"proveedor": "deepseek", "usd": 0.0123, "skus": 4})
        self.assertEqual(params[4], 12.3)

    def test_sin_proveedor_el_origen_es_ia(self):
        with mock.patch.object(J.supabase_db, "execute") as ex:
            J.registrar("terminos", "parcial", {"usd": 0.0}, 1.0)
        self.assertEqual(ex.call_args.args[1][:3], ("ia", "terminos", "parcial"))

    def test_nunca_lanza(self):
        """Una bitácora que tumba el trabajo es peor que no tenerla."""
        with mock.patch.object(J.supabase_db, "execute",
                               side_effect=RuntimeError("base caída")):
            self.assertIsNone(J.registrar("juez", "ok", {"usd": 1}, 1.0))
        with mock.patch.object(J.supabase_db, "execute") as ex:
            self.assertIsNone(J.registrar("juez", "ok", None, 1.0))          # detalle roto
            self.assertIsNone(J.registrar("juez", "ok", {"usd": 1}, "mucho"))  # duración rota
            J.registrar("juez", "ok", {"cuando": dt.datetime(2026, 9, 30), "x": {1, 2}}, 1.0)
        self.assertEqual(ex.call_count, 1, "lo no serializable se guarda como texto")


class Gastado24h(unittest.TestCase):
    def test_devuelve_lo_que_suma_la_bitacora(self):
        with mock.patch.object(J.supabase_db, "fetch_scalar", return_value="0.4200") as leer:
            self.assertEqual(J.gastado_24h(), 0.42)
        sql, params = leer.call_args.args
        self.assertEqual(params, ("juez",))
        self.assertIn("24 hours", sql)

    def test_cuenta_solo_la_accion_pedida(self):
        """El costo de Apify vive en la misma tabla con accion 'raspado': si se
        mezclaran, el juez se detendría por un gasto que no es suyo."""
        with mock.patch.object(J.supabase_db, "fetch_scalar", return_value=0) as leer:
            J.gastado_24h("terminos")
        self.assertEqual(leer.call_args.args[1], ("terminos",))

    def test_sin_filas_es_cero(self):
        with mock.patch.object(J.supabase_db, "fetch_scalar", return_value=None):
            self.assertEqual(J.gastado_24h(), 0.0)

    def test_si_no_puede_medir_devuelve_infinito(self):
        """Sin poder medir, no se gasta: infinito agota cualquier tope diario."""
        with mock.patch.object(J.supabase_db, "fetch_scalar",
                               side_effect=RuntimeError("base caída")):
            g = J.gastado_24h()
        self.assertEqual(g, float("inf"))
        self.assertIs(J.Presupuesto(1.0 - g).puede(), False)


class TablasListas(unittest.TestCase):
    def test_true_solo_si_la_base_dice_true(self):
        with mock.patch.object(J.supabase_db, "fetch_scalar", return_value=True) as leer:
            self.assertIs(J.tablas_listas(), True)
        self.assertEqual(leer.call_args.args[1], (J.TABLA_JUICIO, J.VISTA_TITULO))
        for otro in (False, None, 1, "t"):
            with mock.patch.object(J.supabase_db, "fetch_scalar", return_value=otro):
                self.assertIs(J.tablas_listas(), False, repr(otro))

    def test_false_si_la_consulta_lanza(self):
        """Producción no tiene la tabla hasta que la migración pase su acta: sin
        tabla —o sin base— todo se comporta como si el juez no existiera."""
        with mock.patch.object(J.supabase_db, "fetch_scalar",
                               side_effect=RuntimeError("base caída")):
            self.assertIs(J.tablas_listas(), False)


# ── La corrida completa ──────────────────────────────────────────────────────

class JuzgarSkusNoGastaDeMas(unittest.TestCase):
    def test_sin_skus_no_lee_ni_llama(self):
        base, llm = BaseFalsa([]), mock.Mock()
        r = correr(base, llm, [None, ""])
        self.assertEqual((r["skus"], r["llamadas"], r["detenido"]), (0, 0, None))
        self.assertEqual((base.lecturas, base.bitacora), ([], []))
        llm.assert_not_called()

    def test_sin_pendientes_no_llama_al_llm(self):
        """Reentrante: volver a correr sobre lo ya juzgado cuesta una lectura."""
        base = BaseFalsa([juzgada("SKU-A", "R1"), juzgada("SKU-A", "R2", clase="refaccion"),
                          fila("SKU-A", "R3", es_nuestro=True), fila("SKU-A", "R4", precio=None)])
        llm = mock.Mock()
        r = correr(base, llm, ["SKU-A"])
        llm.assert_not_called()
        self.assertEqual((r["llamadas"], r["veredictos"], r["usd"], r["detenido"]),
                         (0, 0, 0.0, None))
        self.assertEqual(base.transacciones, [])
        self.assertEqual(base.bitacora, [], "sin llamadas no hay gasto que registrar")

    def test_sin_titulo_nuestro_no_se_juzga(self):
        """Sin saber qué es NUESTRO producto no hay contra qué comparar."""
        base = BaseFalsa([fila("SKU-A", "R1", titulo_nuestro=None),
                          fila("SKU-A", "R2", titulo_nuestro="")])
        llm = mock.Mock()
        correr(base, llm, ["SKU-A"])
        llm.assert_not_called()

    def test_modelo_sin_precio_no_corre(self):
        base, llm = BaseFalsa(pendientes_de("SKU-A")), mock.Mock()
        r = correr(base, llm, ["SKU-A"], modelo="gpt-inventado")
        self.assertEqual(r["detenido"], "modelo sin precio: gpt-inventado")
        llm.assert_not_called()
        self.assertEqual((base.lecturas, base.transacciones, base.bitacora), ([], [], []))

    def test_el_modelo_sale_de_la_configuracion_si_no_se_pasa(self):
        """Texto libre de una variable de entorno: mal escrito, no corre."""
        base, llm = BaseFalsa(pendientes_de("SKU-A")), mock.Mock(side_effect=contesta())
        with mock.patch.object(J.settings, "competencia_juez_modelo", "deepseek-flahs"):
            r = correr(base, llm, ["SKU-A"], modelo=None)
        self.assertEqual(r["detenido"], "modelo sin precio: deepseek-flahs")
        llm.assert_not_called()
        with mock.patch.object(J.settings, "competencia_juez_modelo", "deepseek-v4-pro"):
            r = correr(base, llm, ["SKU-A"], modelo=None)
        self.assertEqual(r["modelo"], "deepseek-v4-pro")
        self.assertEqual(llm.call_args.kwargs["modelo"], "deepseek-v4-pro")

    def test_el_modelo_por_omision_tiene_precio(self):
        """Si el valor por omisión de `config.py` no estuviera en `MODELOS`, el
        juez no correría NUNCA y nadie vería un error: solo «detenido»."""
        omision = type(J.settings).model_fields["competencia_juez_modelo"].default
        self.assertIn(omision, J.ia_json.MODELOS)

    def test_presupuesto_agotado_no_llama(self):
        base, llm = BaseFalsa(pendientes_de("SKU-A", "SKU-B")), mock.Mock()
        r = correr(base, llm, ["SKU-A", "SKU-B"], presupuesto=J.Presupuesto(0))
        self.assertEqual(r["detenido"], "tope de gasto")
        llm.assert_not_called()
        self.assertEqual(base.bitacora, [])

    def test_el_presupuesto_corta_a_media_corrida(self):
        p = J.Presupuesto(0.0015)
        base = BaseFalsa(pendientes_de("SKU-A", "SKU-B", "SKU-C", "SKU-D"))
        llm = mock.Mock(side_effect=contesta(usd=0.001))
        r = correr(base, llm, ["SKU-A", "SKU-B", "SKU-C", "SKU-D"], presupuesto=p)
        self.assertEqual(llm.call_count, 2, "el segundo cruza el tope; el tercero ya no entra")
        self.assertEqual(r["detenido"], "tope de gasto")
        self.assertEqual((r["juzgados_skus"], r["veredictos"]), (2, 4))
        self.assertAlmostEqual(p.gastado, 0.002)
        self.assertEqual(base.bitacora[0][1][2], "parcial")

    def test_plazo_de_reloj(self):
        """Colgado de una captura el juez tiene 60 s: lo que no alcance queda
        pendiente. Cada llamada suplantada «tarda» 40 s de un reloj falso."""
        reloj = {"t": 1000.0}

        def lento(system, user, **kw):
            reloj["t"] += 40.0
            return contesta()(system, user, **kw)

        base = BaseFalsa(pendientes_de("SKU-A", "SKU-B", "SKU-C"))
        llm = mock.Mock(side_effect=lento)
        with mock.patch.object(J, "time", types.SimpleNamespace(monotonic=lambda: reloj["t"])):
            r = correr(base, llm, ["SKU-A", "SKU-B", "SKU-C"], plazo_s=60.0)
        self.assertEqual(llm.call_count, 2, "a los 0 s y a los 40 s entra; a los 80 s ya no")
        self.assertEqual(r["detenido"], "plazo")
        self.assertEqual(r["juzgados_skus"], 2)
        self.assertEqual(r["duracion_s"], 80.0)
        self.assertEqual(len(base.transacciones), 2, "lo juzgado antes del plazo SÍ se guarda")

    def test_sin_plazo_no_hay_corte_por_reloj(self):
        reloj = {"t": 0.0}

        def lento(system, user, **kw):
            reloj["t"] += 10_000.0
            return contesta()(system, user, **kw)

        base = BaseFalsa(pendientes_de("SKU-A", "SKU-B"))
        with mock.patch.object(J, "time", types.SimpleNamespace(monotonic=lambda: reloj["t"])):
            r = correr(base, mock.Mock(side_effect=lento), ["SKU-A", "SKU-B"])
        self.assertIsNone(r["detenido"])
        self.assertEqual(r["juzgados_skus"], 2)

    def test_cinco_fallos_seguidos_detienen(self):
        """Proveedor caído o sin saldo: seguir es quemar minutos (y, si cobra los
        intentos, dinero) sin guardar un solo veredicto."""
        skus = [f"SKU-{i}" for i in range(1, 9)]
        base, llm = BaseFalsa(pendientes_de(*skus)), mock.Mock(return_value=fallo())
        r = correr(base, llm, skus)
        self.assertEqual(llm.call_count, 5)
        self.assertEqual(r["detenido"], "5 fallos seguidos del proveedor")
        self.assertEqual((r["fallidos"], r["veredictos"], r["juzgados_skus"]), (5, 0, 0))
        self.assertIn("503", r["ultimo_motivo"])
        self.assertEqual(base.transacciones, [])
        self.assertEqual(len(base.bitacora), 1)
        self.assertEqual(base.bitacora[0][1][2], "parcial")

    def test_un_exito_reinicia_la_cuenta_de_fallos(self):
        """Son fallos SEGUIDOS: cuatro, un acierto y otros cuatro no detienen."""
        skus = [f"SKU-{i}" for i in range(1, 10)]

        def intermitente(system, user, **kw):
            return contesta()(system, user, **kw) if "SKU-5" in user else fallo()

        base, llm = BaseFalsa(pendientes_de(*skus)), mock.Mock(side_effect=intermitente)
        r = correr(base, llm, skus)
        self.assertEqual(llm.call_count, 9)
        self.assertIsNone(r["detenido"])
        self.assertEqual((r["fallidos"], r["juzgados_skus"]), (8, 1))

    def test_respuestas_invalidas_no_son_fallos_del_proveedor(self):
        """Cinco SKUs cuyo único rival el modelo contesta con una clase que no
        existe: el proveedor SÍ contestó (y cobró). Antes contaban como «5 fallos
        seguidos del proveedor», la corrida se detenía y, como la cola va en
        orden de SKU, lo de atrás no avanzaba nunca."""
        tercos = [f"A-0{i}" for i in range(1, 6)]
        base = BaseFalsa(pendientes_de(*tercos, "Z-99", por_sku=1))

        def llm(system, user, **kw):
            return respuesta([v(1, "mismo" if "Z-99" in user else "mismo_producto")])

        r = correr(base, mock.Mock(side_effect=llm), tercos + ["Z-99"])
        self.assertIsNone(r["detenido"])
        self.assertEqual((r["fallidos"], r["juzgados_skus"], r["veredictos"]), (5, 1, 1))
        self.assertEqual({p[0] for p in base.inserts()}, {"Z-99"})
        self.assertEqual(r["ultimo_motivo"], "respuesta inválida del modelo")
        self.assertEqual(base.bitacora[0][1][2], "parcial")

    def test_sin_turno_se_detiene_sin_culpar_a_nadie(self):
        """La IA del proceso lleva `timeout` ocupada: no es el modelo ni el
        proveedor, y los SKUs que siguen esperarían lo mismo. Queda pendiente."""
        ocupado = mock.Mock()
        ocupado.acquire.return_value = False
        base, llm = BaseFalsa(pendientes_de("SKU-A", "SKU-B", "SKU-C")), mock.Mock()
        with mock.patch.object(J, "_turno", ocupado):
            r = correr(base, llm, ["SKU-A", "SKU-B", "SKU-C"], timeout=30.0)
        llm.assert_not_called()
        self.assertEqual(ocupado.acquire.call_count, 1, "a la primera se detiene")
        self.assertEqual((r["detenido"], r["ultimo_motivo"]), (J.IA_OCUPADA, J.IA_OCUPADA))
        self.assertEqual((r["fallidos"], r["veredictos"], r["sin_juzgar"]), (0, 0, 2))
        self.assertEqual(base.transacciones, [])


class JuzgarSkusGuarda(unittest.TestCase):
    def test_guarda_por_sku_y_registra_una_sola_fila(self):
        base = BaseFalsa([
            fila("SKU-A", "RIVAL-B", titulo="Foco de repuesto para lámpara", posicion=1),
            fila("SKU-A", "RIVAL-A", titulo="Lámpara de escritorio con pinza", posicion=2),
            fila("SKU-B", "RIVAL-C", titulo="Lámpara de escritorio con reloj", posicion=1,
                 termino_id=12),
        ])

        def por_titulo(system, user, **kw):
            return respuesta([v(i, "refaccion" if "Foco" in t else "mismo", razon=t[:20])
                              for i, t in numerados(user).items()], usd=0.0011)

        r = correr(base, mock.Mock(side_effect=por_titulo), ["SKU-A", "SKU-B"])

        self.assertEqual(len(base.transacciones), 2, "una transacción corta por SKU")
        tx_a = base.transacciones[0]
        self.assertIn("update", tx_a[0][0])
        self.assertEqual(tx_a[0][1], (1, 1, "SKU-A", "mercado_libre", 1),
                         "primero alinea las unidades nuestras del SKU (y su clase)")
        self.assertEqual([p[2] for _, p in tx_a[1:]], ["RIVAL-A", "RIVAL-B"],
                         "ordenadas por rival: dos trabajos toman los candados igual")
        self.assertEqual(tx_a[1][1], (
            "SKU-A", "mercado_libre", "RIVAL-A", 11, "mismo", 1, 1, "Lámpara de escritori",
            NUESTRO, "Lámpara de escritorio con pinza", "deepseek", MODELO, J.VERSION_PROMPT))
        self.assertEqual(tx_a[2][1][2:10], (
            "RIVAL-B", 11, "refaccion", 1, None, "Foco de repuesto par",
            NUESTRO, "Foco de repuesto para lámpara"),
            "el veredicto viaja con SU rival aunque el orden de guardado cambie")
        self.assertEqual(base.transacciones[1][1][1][:4], ("SKU-B", "mercado_libre", "RIVAL-C", 12))

        self.assertEqual((r["skus"], r["juzgados_skus"], r["veredictos"], r["sin_juzgar"]),
                         (2, 2, 3, 0))
        self.assertEqual((r["llamadas"], r["usd"], r["fallidos"], r["detenido"]),
                         (2, 0.0022, 0, None))
        self.assertEqual((r["entrada"], r["salida"]), (200, 100))

        self.assertEqual(len(base.bitacora), 1, "UNA fila por corrida, no una por SKU")
        sql, params = base.bitacora[0]
        self.assertIn("ops.process_log", sql)
        self.assertEqual(params[:3], ("deepseek", "juez", "ok"))
        detalle = json.loads(params[3])
        self.assertEqual((detalle["usd"], detalle["veredictos"], detalle["modelo"]),
                         (0.0022, 3, MODELO))

    def test_un_fallo_al_guardar_no_tumba_a_los_demas(self):
        base = BaseFalsa(pendientes_de("SKU-A", "SKU-B", "SKU-C"))
        base.no_guarda = {"SKU-B"}
        p = J.Presupuesto(1.0)
        llm = mock.Mock(side_effect=contesta(usd=0.001))
        r = correr(base, llm, ["SKU-A", "SKU-B", "SKU-C"], presupuesto=p)
        self.assertEqual(llm.call_count, 3)
        self.assertEqual({tx[1][1][0] for tx in base.transacciones}, {"SKU-A", "SKU-C"})
        # El LLM contestó bien: lo que falló fue la BASE. No es un fallo del
        # proveedor (no suma a `fallidos` ni a los 5 seguidos), pero se cuenta
        # aparte porque ese veredicto ya se pagó.
        self.assertEqual((r["juzgados_skus"], r["veredictos"], r["sin_juzgar"], r["fallidos"]),
                         (2, 4, 2, 0))
        self.assertEqual(r["sin_guardar"], 1)
        self.assertIsNone(r["detenido"])
        self.assertAlmostEqual(p.gastado, 0.003, msg="lo que no se guardó también se pagó")
        self.assertEqual(r["usd"], 0.003)
        self.assertEqual(base.bitacora[0][1][2], "parcial")

    def test_solo_lo_pendiente_viaja_y_se_guarda(self):
        """Lo ya juzgado no se vuelve a pagar, y sus unidades —un dato del SKU—
        viajan fijas para que el rival nuevo use el mismo divisor."""
        base = BaseFalsa([
            juzgada("SKU-A", "R1", titulo="Lámpara de escritorio par", unidades_nuestras=2,
                    unidades_rival=2, unidades_sku=2),
            fila("SKU-A", "R2", titulo="Lámpara de escritorio suelta", posicion=2,
                 unidades_sku=2),
            fila("SKU-A", "R3", titulo="Nuestra propia publicación", es_nuestro=True, posicion=3,
                 unidades_sku=2),
        ])
        llm = mock.Mock(side_effect=contesta(un=1, unidades=1))
        r = correr(base, llm, ["SKU-A"])
        user = llm.call_args.args[1]
        self.assertEqual(numerados(user), {1: "Lámpara de escritorio suelta"})
        self.assertIn("unidades_nuestras (dato fijo): 2", user)
        self.assertIn(f"categoria: {CATEGORIA}", user)
        self.assertEqual([(p[2], p[4], p[5], p[6]) for p in base.inserts()],
                         [("R2", "otro_paquete", 2, 1)])
        self.assertEqual((r["veredictos"], r["sin_juzgar"]), (1, 0))

    def test_un_candidato_hereda_el_divisor_del_sku(self):
        """El término candidato trae solo rivales nuevos: ninguna fila suya dice
        el divisor, pero el SKU ya lo tiene (2, de su término asignado). Antes el
        modelo decidía otro (1) y el UPDATE se lo imponía a todo el SKU: el
        «antes» y el «después» de la mejora se contaban con dos varas."""
        base = BaseFalsa([fila("SKU-A", f"NUEVO-{k}", titulo=f"Par de lámparas modelo {k}",
                               posicion=k, termino_id=99, unidades_sku=2) for k in (1, 2)])
        llm = mock.Mock(return_value=respuesta([v(1, "mismo", 2), v(2, "mismo", 1)], un=1))
        correr(base, llm, ["SKU-A"], termino_id=99)
        self.assertIn("unidades_nuestras (dato fijo): 2", llm.call_args.args[1])
        update, *inserts = base.transacciones[0]
        self.assertEqual(update[1], (2, 2, "SKU-A", "mercado_libre", 2))
        self.assertEqual([(p[2], p[4], p[5], p[6]) for _, p in inserts],
                         [("NUEVO-1", "mismo", 2, 2), ("NUEVO-2", "otro_paquete", 2, 1)])

    def test_sin_unidades_del_modelo_el_divisor_establecido_no_se_pisa(self):
        """Una respuesta que no trae `unidades_nuestras` no es un «1» que pise lo
        guardado: el `un or 1` reetiquetaba todo el SKU con el UPDATE."""
        base = BaseFalsa([fila("SKU-A", "NUEVO-1", unidades_sku=3)])
        llm = mock.Mock(return_value=respuesta([v(1, "mismo", 3)], un=None))
        correr(base, llm, ["SKU-A"])
        update, insert = base.transacciones[0]
        self.assertEqual(update[1][:2], (3, 3), "nunca un 1 por omisión")
        self.assertEqual((insert[1][4], insert[1][5], insert[1][6]), ("mismo", 3, 3))

    def test_una_version_nueva_del_prompt_vuelve_a_preguntar_el_divisor(self):
        """Un divisor equivocado (24 piezas de un juego contadas como unidades)
        quedaba pegado: se tomaba de cualquier fila vigente sin mirar la versión y
        ni subir `VERSION_PROMPT` lo corregía; el SKU se quedaba con 0
        comparables. La base ya no lo da por establecido (`unidades_sku` vacío)."""
        base = BaseFalsa([juzgada("SKU-A", f"R{k}", clase="otro_paquete", version="j-vieja",
                                  unidades_nuestras=24, unidades_rival=1, posicion=k)
                          for k in (1, 2, 3)])
        llm = mock.Mock(side_effect=contesta(un=1, unidades=1))
        correr(base, llm, ["SKU-A"])
        self.assertNotIn("dato fijo", llm.call_args.args[1])
        self.assertEqual([(p[4], p[5], p[6]) for p in base.inserts()], [("mismo", 1, 1)] * 3)

    def test_el_sku_con_otra_capitalizacion_si_se_juzga(self):
        """La base empareja el SKU por citext y devuelve el canónico. Antes se
        buscaba con el texto pedido: 0 llamadas, 0 veredictos, y la ruta decía
        «la IA no contestó» sin haberla llamado."""
        base = BaseFalsa(pendientes_de("SKU-A"))
        base.fetch_all = lambda sql, params=None: [
            dict(f) for f in base.filas if f["sku"].lower() in {s.lower() for s in params["skus"]}]
        llm = mock.Mock(side_effect=contesta())
        r = correr(base, llm, ["sku-a"])
        self.assertEqual(llm.call_count, 1)
        self.assertEqual((r["veredictos"], r["juzgados_skus"], r["sin_juzgar"]), (2, 1, 0))
        self.assertEqual({p[0] for p in base.inserts()}, {"SKU-A"}, "con el SKU de la base")
        self.assertEqual(base.transacciones[0][0][1][2], "SKU-A")

    def test_respuesta_parcial_guarda_lo_que_hay(self):
        base = BaseFalsa(pendientes_de("SKU-A", por_sku=3))
        llm = mock.Mock(return_value=respuesta([v(1), v(3, "dudoso")]))
        r = correr(base, llm, ["SKU-A"])
        self.assertEqual([p[2] for p in base.inserts()], ["RIVAL-SKU-A-1", "RIVAL-SKU-A-3"])
        self.assertEqual((r["veredictos"], r["sin_juzgar"], r["fallidos"]), (2, 1, 0))

    def test_nunca_hay_un_cursor_abierto_mientras_habla_el_llm(self):
        """El pool tiene 6 conexiones para TODO el backend: una llamada de 10 a
        90 s con el cursor abierto deja sin conexión a las ventas."""
        base = BaseFalsa(pendientes_de("SKU-A", "SKU-B", "SKU-C"))
        vistos = []

        def espia(system, user, **kw):
            vistos.append(base.abiertos)
            return contesta()(system, user, **kw)

        correr(base, mock.Mock(side_effect=espia), ["SKU-A", "SKU-B", "SKU-C"])
        self.assertEqual(vistos, [0, 0, 0])
        self.assertEqual(len(base.transacciones), 3)

    def test_el_termino_candidato_se_pasa_a_la_lectura(self):
        """Así se mide un término ANTES de asignárselo a nadie."""
        base = BaseFalsa(pendientes_de("SKU-A"))
        correr(base, mock.Mock(side_effect=contesta()), ["SKU-A", "SKU-A"], termino_id=99)
        self.assertEqual(len(base.lecturas), 1, "una sola lectura para todos los SKUs")
        self.assertEqual(base.lecturas[0][1], {"tid": 99, "canal": "mercado_libre",
                                               "skus": ["SKU-A"], "version": J.VERSION_PROMPT})

    def test_timeout_e_intentos_llegan_al_llm(self):
        base, llm = BaseFalsa(pendientes_de("SKU-A")), mock.Mock(side_effect=contesta())
        correr(base, llm, ["SKU-A"], timeout=30.0, intentos=1)
        self.assertEqual((llm.call_args.kwargs["timeout"], llm.call_args.kwargs["intentos"]),
                         (30.0, 1))

    def test_con_varios_hilos_no_se_pierde_ni_se_duplica_nada(self):
        skus = [f"SKU-{i:02d}" for i in range(1, 13)]
        base, p = BaseFalsa(pendientes_de(*skus)), J.Presupuesto(1.0)
        r = correr(base, mock.Mock(side_effect=contesta(usd=0.001)), skus, presupuesto=p, hilos=4)
        self.assertEqual((r["juzgados_skus"], r["veredictos"], r["llamadas"]), (12, 24, 12))
        self.assertEqual(sorted(tx[1][1][0] for tx in base.transacciones), skus)
        self.assertEqual(r["usd"], 0.012)
        self.assertAlmostEqual(p.gastado, 0.012)
        self.assertEqual(len(base.bitacora), 1)

    def test_guardar_sin_veredictos_no_abre_transaccion(self):
        base = BaseFalsa([])
        with mock.patch.object(J.supabase_db, "get_cursor", base.get_cursor):
            n = J._guardar("SKU-A", [fila()], {"veredictos": {}, "unidades_nuestras": 1})
        self.assertEqual((n, base.transacciones), (0, []))


class DivisorDelSku(unittest.TestCase):
    """`unidades_sku` de `_SQL_FILAS`: la subconsulta corre DE VERDAD sobre una
    tabla en memoria (SQLite la entiende tal cual). Sale de TODOS los veredictos
    del SKU con el título nuestro de hoy y la versión vigente, no de las filas
    del término pedido: así un candidato hereda el divisor y una versión nueva
    del prompt lo vuelve a preguntar."""

    def divisor(self, juicios: list[tuple], titulo: str = NUESTRO) -> Any:
        sub = re.search(r"(\(select u\.unidades_nuestras.*?\)) as unidades_sku", J._SQL_FILAS,
                        re.DOTALL)
        self.assertIsNotNone(sub)
        cn = sqlite3.connect(":memory:")
        self.addCleanup(cn.close)
        cn.execute("attach ':memory:' as enrich")
        cn.execute(f"create table {J.TABLA_JUICIO} (sku text, canal text, externo_id text, "
                   "unidades_nuestras integer, titulo_nuestro text, version_prompt text, "
                   "juzgado_en text)")
        cn.executemany(f"insert into {J.TABLA_JUICIO} values (?, ?, ?, ?, ?, ?, ?)", juicios)
        # Solo el SKU (cfg) y nuestro título de hoy (t) vienen de afuera.
        sql = (f"select {sub.group(1)} from (select 'SKU-A' as sku, '{J.CANAL}' as canal) cfg, "
               "(select :titulo as titulo) t").replace("%(version)s", ":version")
        return cn.execute(sql, {"titulo": titulo, "version": J.VERSION_PROMPT}).fetchone()[0]

    RUIDO = [
        ("SKU-A", J.CANAL, "R3", 24, NUESTRO, "j-vieja", "2026-10-01"),      # otra versión
        ("SKU-A", J.CANAL, "R4", 5, "Otro título nuestro", J.VERSION_PROMPT, "2026-10-01"),
        ("SKU-B", J.CANAL, "R5", 7, NUESTRO, J.VERSION_PROMPT, "2026-10-01"),  # otro SKU
        ("SKU-A", "otro_canal", "R6", 8, NUESTRO, J.VERSION_PROMPT, "2026-10-01"),
        ("SKU-A", J.CANAL, "R7", None, NUESTRO, J.VERSION_PROMPT, "2026-10-01"),
    ]

    def test_toma_el_del_sku_con_titulo_y_version_de_hoy(self):
        """De cualquier rival y cualquier término (la tabla ni los guarda aquí);
        si quedaran dos divisores de antes, el más reciente."""
        juicios = [("SKU-A", J.CANAL, "R1", 2, NUESTRO, J.VERSION_PROMPT, "2026-09-29"),
                   ("SKU-A", J.CANAL, "R2", 3, NUESTRO, J.VERSION_PROMPT, "2026-09-30")]
        self.assertEqual(self.divisor(juicios + self.RUIDO), 3)

    def test_con_otra_version_u_otro_titulo_no_hay_divisor_establecido(self):
        self.assertIsNone(self.divisor(self.RUIDO))
        self.assertIsNone(self.divisor([]))


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 39), "SQLite sin IS DISTINCT FROM")
class AlinearUnidadesReclasifica(unittest.TestCase):
    """El UPDATE con que `_guardar` alinea `unidades_nuestras` corre DE VERDAD
    sobre una tabla en memoria: SQLite entiende la sentencia tal cual (solo
    cambia `%s` por `?`). Antes cambiaba el número y dejaba la clase: un `mismo`
    de 2 piezas contra nuestras 1 seguía contando como comparable."""

    def alinear(self, un: int) -> list[tuple]:
        base = BaseFalsa([])
        r = {"veredictos": {1: {"clase": "refaccion", "unidades_rival": None, "motivo": ""}},
             "unidades_nuestras": un, "proveedor": "deepseek", "modelo": MODELO}
        with mock.patch.object(J.supabase_db, "get_cursor", base.get_cursor):
            J._guardar("SKU-A", [fila()], r)
        sql, params = base.transacciones[0][0]
        self.assertTrue(sql.startswith("update"))
        cn = sqlite3.connect(":memory:")
        self.addCleanup(cn.close)
        cn.execute("attach ':memory:' as enrich")
        cn.execute(f"create table {J.TABLA_JUICIO} (sku text, canal text, externo_id text, "
                   "clase text, unidades_nuestras integer, unidades_rival integer)")
        cn.executemany(f"insert into {J.TABLA_JUICIO} values (?, ?, ?, ?, ?, ?)", [
            ("SKU-A", J.CANAL, "X", "mismo", 2, 2),
            ("SKU-A", J.CANAL, "Y", "otro_paquete", 2, 1),
            ("SKU-A", J.CANAL, "Z", "refaccion", 2, None),
            ("SKU-A", J.CANAL, "W", "otra_gama", 2, None),
            ("SKU-B", J.CANAL, "X", "mismo", 2, 2),
        ])
        cn.execute(sql.replace("%s", "?"), params)
        return cn.execute(f"select sku, externo_id, clase, unidades_nuestras, unidades_rival "
                          f"  from {J.TABLA_JUICIO} order by sku, externo_id").fetchall()

    def test_la_clase_se_recalcula_con_el_numero(self):
        self.assertEqual(self.alinear(1), [
            ("SKU-A", "W", "otra_gama", 1, None),
            ("SKU-A", "X", "otro_paquete", 1, 2),     # era `mismo` de 2 contra 2
            ("SKU-A", "Y", "mismo", 1, 1),            # era `otro_paquete` de 1 contra 2
            ("SKU-A", "Z", "refaccion", 1, None),
            ("SKU-B", "X", "mismo", 2, 2),            # otro SKU: no se toca
        ])

    def test_con_el_mismo_divisor_no_cambia_nada(self):
        antes = [("SKU-A", "W", "otra_gama", 2, None), ("SKU-A", "X", "mismo", 2, 2),
                 ("SKU-A", "Y", "otro_paquete", 2, 1), ("SKU-A", "Z", "refaccion", 2, None),
                 ("SKU-B", "X", "mismo", 2, 2)]
        self.assertEqual(self.alinear(2), antes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
