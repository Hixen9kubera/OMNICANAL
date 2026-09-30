"""Pruebas de `competencia_mejora`: proponer un término mejor, MEDIRLO y sugerirlo.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
Este módulo es el único de Competencia que gasta por dos lados a la vez —IA para
proponer y Apify para medir— y el único cuyo resultado puede mover lo que ven los
KAM: aceptar una sugerencia cambia el término del SKU y con él el «precio de
mercado» de Publicaciones. Por eso lo que aquí se fija no es «que funcione», sino
las reglas que evitan pagar de más o cambiar algo sin que una persona lo decida:

  1. A QUIÉN se le busca otro término, y por qué se deja fuera a cada uno de los
     demás. Un SKU con rivales sin juzgar NO entra: la respuesta honesta ahí es
     «no sé», y mejorar lo que quizá está bien cuesta una búsqueda.
  2. QUÉ texto puede llegar a la URL de Mercado Libre (lista blanca) y cuándo dos
     términos son la misma búsqueda (no se paga tres veces por un acento).
  3. QUÉ se le manda al modelo y qué se le acepta de vuelta.
  4. La corrida: se reclama ANTES de pagar, no se mide sin Apify ni con un modelo
     sin precio, un cero que no se puede explicar no quema al candidato, y gana
     solo el que mejora de verdad (3 contra 2 cabe en el ruido).
  5. Nunca cambia un término sola: `mejorar` deja una SUGERENCIA; el cambio lo
     hace `aceptar`, y se niega si la sugerencia ya es vieja.

── SIN RED Y SIN BASE ──────────────────────────────────────────────────────────
Va suplantado todo lo de afuera: `ia_json.completar_json`, `supabase_db` (con la
clase `Base` de abajo, que contesta las lecturas y apunta las escrituras),
`competencia_juez.filas` / `juzgar_skus`, `competencia_captura.medir_busquedas`
(es corrutina: la falsa también), `competencia_scraper.disponible` y
`competencia_store.actualizar_termino`. `competencia_juez.resumen` corre de
verdad: es la cuenta que decide, y suplantarla sería probar el doble. El SQL de
los intentos (`SqlDeVerdad`) y la regla del título (`TituloPorPeriodo`) se
EJECUTAN sobre SQLite en memoria, con la tabla y la subconsulta tal como las
trae la migración 0063.

Los títulos y SKUs de estos ejemplos son INVENTADOS: el repo es público y aquí no
van datos de rivales reales.

    cd backend && python -m unittest tests.test_competencia_mejora -v
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_juez as CJ  # noqa: E402
from services import competencia_mejora as CM  # noqa: E402

SKU = "PRU-0001-NEG"
SKU2 = "PRU-0002-BLA"
SKU3 = "PRU-0003-GRI"
TITULO = "Soporte de pared para cámara de seguridad metálico"
TERMINO = "soporte para camara"
CANDIDATO = "soporte pared camara seguridad"
MODELO = "deepseek-flash"
LOG = "omnicanal.competencia.mejora"


def hace(dias: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=dias)


# ── Fábricas de datos ────────────────────────────────────────────────────────

def rival(sku: str, n: int, clase: str = "otro_producto", *, precio: float = 500.0,
          juzgado: bool = True, nuestro: bool = False, titulo: str | None = None,
          titulo_nuestro: str | None = TITULO) -> dict[str, Any]:
    """Una fila como las que entrega `competencia_juez.filas`: ya trae resueltos
    `cuenta`, `vigente`, `comparable` y `pendiente`, que es lo que lee la mejora."""
    cuenta = (not nuestro) and precio > 0
    return {
        "sku": sku, "termino_id": 10, "externo_id": f"MLM{n:09d}", "posicion": n,
        "titulo": titulo or f"artículo genérico número {n}", "precio": precio,
        "es_nuestro": nuestro, "titulo_nuestro": titulo_nuestro,
        "categoria_nombre": "Soportes", "clase": clase if juzgado else None,
        "motivo": "motivo de prueba" if juzgado else None,
        "unidades_nuestras": 1, "unidades_rival": 1,
        "juzgado_en": hace(1) if juzgado else None, "modelo": MODELO,
        "version_prompt": CJ.VERSION_PROMPT if juzgado else None,
        "cuenta": cuenta, "vigente": juzgado,
        "comparable": cuenta and juzgado and clase == "mismo",
        "pendiente": cuenta and not juzgado,
    }


def rivales(sku: str, mismos: int = 0, otros: int = 0, pendientes: int = 0,
            precios: list[float] | None = None, **kw: Any) -> list[dict[str, Any]]:
    """`mismos` comparables (con `precios`, parejos si no se dan), `otros`
    descartados y `pendientes` sin juzgar."""
    precios = precios or [500.0 + 10 * i for i in range(mismos)]
    out = [rival(sku, i + 1, "mismo", precio=precios[i], **kw) for i in range(mismos)]
    out += [rival(sku, mismos + i + 1, "otro_producto", **kw) for i in range(otros)]
    out += [rival(sku, mismos + otros + i + 1, juzgado=False, **kw) for i in range(pendientes)]
    return out


def fila_universo(sku: str, **cambios: Any) -> dict[str, Any]:
    """Un SKU tal como sale de `_SQL_UNIVERSO`, sano por omisión."""
    return {"sku": sku, "termino_id": 10, "termino_origen": "ia", "termino": TERMINO,
            "estado": "ok", "medido_en": hace(2), "categoria_id": "MLM0001",
            "categoria_nombre": "Soportes", "ruta": "Hogar > Seguridad > Soportes",
            "activa": True, **cambios}


def intento(sku: str, estado: str, *, candidato: str = "Soporte Pared Cámara",
            anterior: str | None = "soporte camara viejo",
            resuelto: datetime | None = None, creado: datetime | None = None,
            ronda: int = 1) -> dict[str, Any]:
    return {"sku": sku, "termino_candidato": candidato, "termino_anterior": anterior,
            "estado": estado, "ronda": ronda, "creado_en": creado or hace(120),
            "resuelto_en": resuelto}


def grupo(skus: dict[str, int] | None = None, *, termino: str = TERMINO, tid: int = 10,
          intentados: list[str] | None = None) -> dict[str, Any]:
    """Un grupo como los que arma `elegibles`. `skus` = {sku: comparables de HOY}
    sobre 10 rivales ya juzgados."""
    miembros = []
    for sku, mismos in (skus or {SKU: 0}).items():
        fs = rivales(sku, mismos=mismos, otros=10 - mismos)
        miembros.append({"sku": sku, "titulo": TITULO, "resumen": CJ.resumen(fs), "filas": fs,
                         "intentados": list(intentados or [])})
    return {"termino_id": tid, "termino": termino, "categoria_id": "MLM0001",
            "categoria_nombre": "Soportes", "ruta": "Hogar > Seguridad > Soportes",
            "skus": miembros}


def respuesta(*candidatos: str, termino_ok: Any = False, usd: float = 0.001) -> dict[str, Any]:
    """Lo que devuelve `ia_json.completar_json` cuando el modelo contesta."""
    return {"ok": True, "usd": usd, "proveedor": "deepseek", "modelo": MODELO,
            "datos": {"diagnostico": "el término es demasiado general",
                      "termino_ok": termino_ok,
                      "candidatos": [{"termino": c, "porque": "nombra el producto exacto"}
                                     for c in candidatos]}}


CAIDA = {"ok": False, "motivo": "DeepSeek no respondió (HTTP 503).", "usd": 0.0,
         "proveedor": "deepseek", "modelo": MODELO, "texto": "", "cortado": False, "uso": {}}


class Base:
    """Suplanta `supabase_db`: contesta las lecturas desde listas y APUNTA las
    escrituras para que la prueba pregunte qué se escribió.

    Reconoce cada consulta por un trozo de su SQL. Una consulta que no conoce
    revienta la prueba a propósito: es preferible a contestar `[]` y dejar pasar
    una lectura nueva sin que nadie la mire."""

    def __init__(self, *, universo: list | None = None, historia: list | None = None,
                 catalogo: list | None = None, tendencias: list | None = None,
                 intento_leido: dict | None = None, ids: dict | None = None,
                 ajenos: set | None = None, concluidos: set | None = None,
                 rondas: dict | None = None, filas_cierre: int = 1) -> None:
        self.universo = universo or []
        self.historia = historia or []
        self.catalogo = catalogo or []
        self.tendencias = tendencias or []
        self.intento_leido = intento_leido     # lo que lee `aceptar`
        self.ids = ids or {}                   # término ya medido → id del catálogo
        self.ajenos = ajenos or set()          # (sku, candidato) que tiene OTRO proceso
        self.concluidos = concluidos or set()  # (sku, candidato) con fila YA concluida
        self.rondas = rondas or {}             # (sku, candidato) → ronda del re-reclamo
        self.filas_cierre = filas_cierre       # rowcount del cierre de una sugerencia
        self.eventos: list[str] = []           # orden: 'reclamo', 'medir'…
        self.lecturas: list[Any] = []          # parámetros de la lectura del universo
        self.escalares: list[Any] = []
        self.reclamos: list[dict[str, Any]] = []
        self.sql_reclamo: str | None = None
        self.iids: dict[tuple[str, str], int] = {}
        self.resueltos: dict[int, tuple] = {}  # iid → (estado, termino_id, comp., total)
        self.con_fecha: dict[int, bool] = {}   # iid → ¿`_resolver` le puso resuelto_en?
        self.devueltas: set[int] = set()       # iid cuya ronda devolvió `_resolver`
        self.renovados: list[tuple] = []       # (estado, sku, candidato) de lo renovado
        self.bitacora: list[dict[str, Any]] = []
        self.cierres: list[dict[str, Any]] = []
        self.hermanas: list[tuple] = []
        self._iid = 100

    # Lecturas ---------------------------------------------------------------
    def fetch_all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        if "market_sku_config cfg" in sql:
            self.lecturas.append(params)
            return self.universo
        if CM.TABLA in sql:
            return self.historia
        if "market_terms" in sql:
            return [{"termino": t} for t in self.tendencias]
        if "market_search_term" in sql:
            return self.catalogo
        raise AssertionError(f"lectura que la prueba no esperaba: {sql[:80]}")

    def fetch_one(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        return self.intento_leido

    def fetch_scalar(self, sql: str, params: Any = None) -> Any:
        self.escalares.append(params)
        return self.ids.get(params[1])

    # Escrituras -------------------------------------------------------------
    def execute_returning(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        sku, _canal, anterior, anterior_id, candidato, _motivo, estado, antes, total = params[:9]
        self.eventos.append("reclamo")
        self.sql_reclamo = sql
        self.reclamos.append({"sku": sku, "candidato": candidato, "estado": estado,
                              "anterior": anterior, "anterior_id": anterior_id,
                              "antes": antes, "total": total})
        if (sku, candidato) in self.ajenos | self.concluidos:
            return None
        self._iid += 1
        self.iids[(sku, candidato)] = self._iid
        return {"id": self._iid, "ronda": self.rondas.get((sku, candidato), 1)}

    def execute(self, sql: str, params: Any = None) -> int:
        if "ops.process_log" in sql:
            origen, accion, estado, detalle, _duracion = params
            self.bitacora.append({"origen": origen, "accion": accion, "estado": estado,
                                  "detalle": json.loads(detalle)})
            return 1
        if "termino_candidato_id = coalesce" in sql:            # _resolver
            estado, tid, comparables, total, con_fecha, iid = params
            self.resueltos[iid] = (estado, tid, comparables, total)
            self.con_fecha[iid] = con_fecha
            if "ronda = ronda - 1" in sql:
                self.devueltas.add(iid)
            return 1
        if "set resuelto_en = now()," in sql:                   # _anotar_desenlace
            estado, _motivo, sku, _canal, candidato = params
            self.renovados.append((estado, sku, candidato))
            return int((sku, candidato) in self.concluidos)
        if "id <> %s" in sql:                                   # aceptar: cierra las demás
            self.hermanas.append(params)
            return 1
        if "resuelto_por = %s where id = %s" in sql:            # _cerrar
            estado, quien, iid = params
            self.cierres.append({"id": iid, "estado": estado, "quien": quien,
                                 "solo_abierta": "and estado = 'medido'" in sql})
            return self.filas_cierre
        raise AssertionError(f"escritura que la prueba no esperaba: {sql[:80]}")

    # Ayudas para las pruebas ------------------------------------------------
    def puesta(self) -> Any:
        return mock.patch.multiple(
            CM.supabase_db, fetch_all=self.fetch_all, fetch_one=self.fetch_one,
            fetch_scalar=self.fetch_scalar, execute=self.execute,
            execute_returning=self.execute_returning)

    def reclamados(self) -> list[tuple[str, str, str]]:
        return [(r["sku"], r["candidato"], r["estado"]) for r in self.reclamos]

    def estado_de(self, sku: str, candidato: str) -> str | None:
        """El estado FINAL del intento, o `None` si nunca se resolvió."""
        return (self.resueltos.get(self.iids.get((sku, candidato), -1)) or (None,))[0]


def correr(grupos: list[dict[str, Any]], *, ia: list[dict[str, Any]], base: Base | None = None,
           medidos: dict[str, int] | None = None, murados: set[str] | None = None,
           filas: dict[int, list] | None = None, apify: bool = True,
           medir_truena: bool = False, tope_usd: float = 1.0, modelo: str = MODELO,
           juez: dict[str, Any] | None = None, **kw: Any) -> SimpleNamespace:
    """Corre `mejorar` con todo lo de afuera suplantado y devuelve los testigos.

    `ia`      respuestas de `completar_json`, una por grupo y en orden; si se pide
              una de más, la prueba revienta (nadie debe llamar al modelo de más).
    `medidos` {candidato: filas que guardó la medición}
    `murados` candidatos que ML no dejó ver
    `filas`   {termino_id: filas de rivales ya juzgados contra cada SKU}, o una
              función (skus, termino_id)
    `juez`    lo que agrega `juzgar_skus` a su respuesta (`detenido`, `fallidos`…)
    """
    base = base or Base()
    t = SimpleNamespace(base=base, lotes=[], juzgados=[], juez_kw=[],
                        ia=mock.Mock(side_effect=list(ia)),
                        asignar=mock.Mock(return_value=True))

    async def medir(terminos, limite=10, bloqueados=None):
        base.eventos.append("medir")
        t.lotes.append(list(terminos))
        if medir_truena:
            raise RuntimeError("Apify no contestó")
        if bloqueados is not None:
            bloqueados |= set(murados or ()) & set(terminos)
        return {x: (medidos or {}).get(x, 0) for x in terminos}

    def juzgar(skus, *, presupuesto, termino_id=None, modelo=None, **resto):
        t.juzgados.append((list(skus), termino_id))
        t.juez_kw.append(resto)
        return {"veredictos": 0, **(juez or {})}

    def filas_de(skus, termino_id=None):
        if callable(filas):
            return filas(skus, termino_id)
        return [f for f in (filas or {}).get(termino_id, []) if f["sku"] in skus]

    t.presupuesto = CJ.Presupuesto(tope_usd)
    with base.puesta(), \
         mock.patch.object(CM.ia_json, "completar_json", t.ia), \
         mock.patch.object(CM.competencia_juez, "juzgar_skus", juzgar), \
         mock.patch.object(CM.competencia_juez, "filas", filas_de), \
         mock.patch.object(CM.competencia_captura, "medir_busquedas", medir), \
         mock.patch.object(CM.competencia_scraper, "disponible", return_value=apify), \
         mock.patch.object(CM.competencia_store, "actualizar_termino", t.asignar):
        t.out = CM.mejorar(grupos, presupuesto=t.presupuesto, modelo=modelo, **kw)
    return t


# ── 1. Texto de los candidatos ───────────────────────────────────────────────

class Llave(unittest.TestCase):
    """`llave`: cuándo dos términos son LA MISMA búsqueda."""

    def test_iguala_mayusculas_acentos_y_guiones(self):
        """El caso del docstring: tres formas de escribir lo mismo no deben
        pagarse como tres búsquedas de Apify."""
        esperado = "bujias iridium"
        for t in ("Bujías Iridium", "bujias iridium", "bujías-iridium", "  BUJIAS   IRIDIUM "):
            with self.subTest(termino=t):
                self.assertEqual(CM.llave(t), esperado)

    def test_los_signos_pasan_a_espacio_y_se_colapsan(self):
        self.assertEqual(CM.llave("soporte/pared_cámara (2 pzs)"), "soporte pared camara 2 pzs")

    def test_vacio_no_revienta(self):
        self.assertEqual(CM.llave(None), "")
        self.assertEqual(CM.llave(""), "")

    def test_terminos_distintos_no_se_confunden(self):
        self.assertNotEqual(CM.llave("soporte para camara"), CM.llave("soporte para celular"))


class Normalizar(unittest.TestCase):
    """`normalizar` es una LISTA BLANCA: la cadena del modelo acaba en una URL de
    Mercado Libre donde «/» y «_» son gramática (el sufijo es `_NoIndex_True`)."""

    def test_un_termino_normal_pasa_en_minusculas(self):
        self.assertEqual(CM.normalizar("  Soporte de Pared para Cámara "),
                         "soporte de pared para cámara")

    def test_rechaza_una_sola_palabra(self):
        """Una palabra suelta es la búsqueda general de la que se quiere salir."""
        self.assertIsNone(CM.normalizar("soporte"))

    def test_rechaza_mas_de_seis_palabras(self):
        """Los títulos largos devuelven cero resultados en ML: ya se midió."""
        self.assertIsNotNone(CM.normalizar("soporte de pared para camara exterior"))
        self.assertIsNone(CM.normalizar("soporte de pared para camara de seguridad"))

    def test_rechaza_mas_de_sesenta_caracteres(self):
        largo = " ".join(["abcdefghijk"] * 6)            # 6 palabras, 71 caracteres
        self.assertEqual(len(largo.split()), 6)
        self.assertGreater(len(largo), 60)
        self.assertIsNone(CM.normalizar(largo))

    def test_quita_la_diagonal_y_el_guion_bajo(self):
        """Si pasaran, el candidato rompería la URL o se comería el sufijo que
        evita el muro de login."""
        limpio = CM.normalizar("soporte/pared_camara_NoIndex_True")
        self.assertEqual(limpio, "soporte pared camara noindex true")
        self.assertNotIn("/", limpio)
        self.assertNotIn("_", limpio)

    def test_quita_comillas_y_signos(self):
        self.assertEqual(CM.normalizar('"soporte, pared; cámara"'), "soporte pared cámara")

    def test_conserva_medidas_y_capacidades(self):
        """Al revés que `competencia_terminos._limpiar`: aquí la medida es justo
        lo que separa una gama de otra."""
        self.assertEqual(CM.normalizar("Set Sartenes 3 piezas"), "set sartenes 3 piezas")
        self.assertEqual(CM.normalizar("mochila de viaje 60 litros"), "mochila de viaje 60 litros")

    def test_lo_que_no_es_texto_no_revienta(self):
        for malo in (None, "", "   ", "///", 7):
            with self.subTest(valor=malo):
                self.assertIsNone(CM.normalizar(malo))


# ── 2. A quién se le busca otro término ──────────────────────────────────────

class Elegibles(unittest.TestCase):
    """`elegibles`: quién entra, y POR QUÉ queda fuera cada uno de los demás."""

    def _elegibles(self, universo, filas=None, historia=None, **kw):
        base = Base(universo=universo, historia=historia or [])
        pedidos: list[list[str]] = []

        def filas_de(skus, termino_id=None):
            pedidos.append(list(skus))
            return [f for s in skus for f in (filas or {}).get(s, [])]

        with base.puesta(), mock.patch.object(CM.competencia_juez, "filas", filas_de):
            e = CM.elegibles(**kw)
        return e, pedidos, base

    FLOJO = staticmethod(lambda sku=SKU: rivales(sku, mismos=1, otros=9))

    def test_un_sku_flojo_y_ya_juzgado_entra(self):
        e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()})
        self.assertEqual(e["fuera"], {})
        self.assertEqual(len(e["grupos"]), 1)
        g = e["grupos"][0]
        self.assertEqual((g["termino_id"], g["termino"]), (10, TERMINO))
        self.assertEqual((g["categoria_id"], g["categoria_nombre"]), ("MLM0001", "Soportes"))
        s = g["skus"][0]
        self.assertEqual((s["sku"], s["titulo"]), (SKU, TITULO))
        self.assertEqual(s["resumen"]["comparables"], 1)
        self.assertEqual(len(s["filas"]), 10)
        self.assertEqual(s["intentados"], [])

    def test_cada_motivo_de_exclusion_se_cuenta_con_su_nombre(self):
        """Un SKU por motivo. El texto es el contrato: `mejorar_sku` se lo enseña
        tal cual a quien apretó el botón, así que «no hizo nada» siempre viene con
        su porqué."""
        viejo = hace(90)
        agotado = [intento(SKU, e, candidato=f"candidato numero {i}", resuelto=viejo)
                   for i, e in enumerate(["sin_mejora", "descartado", "aceptado",
                                          "termino_ok"][:CM.MAX_POR_SKU])]
        casos = [
            ("sin publicación activa", fila_universo(SKU, activa=False), self.FLOJO(), []),
            ("término corregido a mano", fila_universo(SKU, termino_origen="manual"),
             self.FLOJO(), []),
            ("término bloqueado: toca re-medir, no cambiar",
             fila_universo(SKU, estado="bloqueado"), self.FLOJO(), []),
            ("término sin medir", fila_universo(SKU, medido_en=None), self.FLOJO(), []),
            ("medición vieja: toca re-medir", fila_universo(SKU, medido_en=hace(46)),
             self.FLOJO(), []),
            ("la búsqueda no trajo rivales", fila_universo(SKU), [], []),
            ("rivales sin juzgar: primero el juez", fila_universo(SKU),
             rivales(SKU, mismos=1, otros=8, pendientes=1), []),
            ("ya tiene suficientes comparables", fila_universo(SKU),
             rivales(SKU, mismos=CM.UMBRAL, otros=7), []),
            ("ya tiene una sugerencia esperando", fila_universo(SKU), self.FLOJO(),
             [intento(SKU, "medido", resuelto=viejo)]),
            ("agotado: ya se probaron los candidatos permitidos", fila_universo(SKU),
             self.FLOJO(), agotado),
            ("en enfriamiento", fila_universo(SKU), self.FLOJO(),
             [intento(SKU, "sin_mejora", resuelto=hace(10))]),
            ("sin título nuestro", fila_universo(SKU),
             rivales(SKU, mismos=1, otros=9, titulo_nuestro=None), []),
        ]
        self.assertEqual(len(agotado), CM.MAX_POR_SKU)
        for motivo, fila, fs, historia in casos:
            with self.subTest(motivo=motivo):
                e, _, _ = self._elegibles([fila], {SKU: fs}, historia)
                self.assertEqual(e["grupos"], [])
                self.assertEqual(e["fuera"], {motivo: 1})

    def test_los_motivos_se_suman_por_sku(self):
        universo = [fila_universo(SKU, activa=False), fila_universo(SKU2, activa=False),
                    fila_universo(SKU3, medido_en=None)]
        e, _, _ = self._elegibles(universo)
        self.assertEqual(e["fuera"], {"sin publicación activa": 2, "término sin medir": 1})

    def test_lo_descartado_por_su_termino_ni_siquiera_pide_sus_rivales(self):
        """Los primeros cinco motivos se deciden con la fila del universo: no hay
        por qué leer los rivales de un SKU que ya quedó fuera."""
        universo = [fila_universo(SKU, activa=False), fila_universo(SKU2)]
        _, pedidos, _ = self._elegibles(universo, {SKU2: self.FLOJO(SKU2)})
        self.assertEqual(pedidos, [[SKU2]])

    def test_rivales_sin_juzgar_no_disparan_la_mejora_aunque_haya_cero_comparables(self):
        """El caso que la crítica del diseño marcó como bloqueante: 6 juzgados sin
        ningún `mismo` y 4 pendientes NO es «menos de 3 comparables», es «no sé».
        Mejorar ahí paga Apify por un término que quizá está bien."""
        e, _, _ = self._elegibles([fila_universo(SKU)],
                                  {SKU: rivales(SKU, otros=6, pendientes=4)})
        self.assertEqual(e["grupos"], [])
        self.assertEqual(e["fuera"], {"rivales sin juzgar: primero el juez": 1})

    def test_las_variantes_que_comparten_termino_van_en_un_solo_grupo(self):
        """Una propuesta por TÉRMINO: cinco hermanas no deben pagar cinco
        búsquedas casi iguales ni acabar en cinco términos distintos."""
        universo = [fila_universo(SKU), fila_universo(SKU2),
                    fila_universo(SKU3, termino_id=11, termino="filtro pop microfono")]
        e, _, _ = self._elegibles(
            universo, {s: self.FLOJO(s) for s in (SKU, SKU2, SKU3)})
        self.assertEqual(e["fuera"], {})
        self.assertEqual([(g["termino_id"], [s["sku"] for s in g["skus"]])
                          for g in e["grupos"]], [(10, [SKU, SKU2]), (11, [SKU3])])
        self.assertEqual(e["grupos"][1]["termino"], "filtro pop microfono")

    def test_intentados_junta_candidatos_y_terminos_anteriores_por_su_llave(self):
        """Es lo que impide volver a un término anterior: se compara por `llave`,
        así que «Soporte Pared Cámara» y «soporte pared camara» son el mismo.

        Un candidato que quedó en `error` NO entra: la medición o el juicio no
        terminaron, así que sigue siendo proponible. Quemarlo por un fallo de
        Apify sería perder un término que nunca se probó."""
        historia = [intento(SKU, "sin_mejora", candidato="Soporte Pared Cámara",
                            anterior="Soporte-Cámara Viejo", resuelto=hace(90)),
                    intento(SKU, "error", candidato="base para camara ip", anterior=None,
                            resuelto=hace(90))]
        e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, historia)
        self.assertEqual(e["grupos"][0]["skus"][0]["intentados"],
                         ["soporte camara viejo", "soporte pared camara"])

    def test_un_error_o_un_bloqueo_no_cuentan_para_agotar_al_sku(self):
        """«Agotado» son candidatos PROBADOS. Un intento que no se pudo medir no
        probó nada y no debe gastar uno de los cuatro permitidos."""
        historia = [intento(SKU, e, candidato=f"candidato numero {i}", resuelto=hace(90))
                    for i, e in enumerate(["error", "bloqueado", "error", "bloqueado", "error"])]
        e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, historia)
        self.assertEqual(e["fuera"], {})
        self.assertEqual(len(e["grupos"]), 1)

    def test_un_error_o_un_bloqueo_recientes_no_se_vuelven_a_proponer(self):
        """No concluyeron, así que no enfrían ni cuentan; pero durante
        `REINTENTO_DIAS` van a «ya probados». Sin eso el modelo, con temperatura
        0, repetía el mismo candidato muerto en cada corrida y en cada clic, y se
        volvía a pagar su página. Pasado ese plazo se pueden volver a proponer."""
        for estado in ("error", "bloqueado"):
            with self.subTest(estado=estado):
                reciente = [intento(SKU, estado, candidato="Base Pared Cámara",
                                    creado=hace(1))]
                e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, reciente)
                self.assertEqual(e["fuera"], {}, "no enfría ni agota al SKU")
                self.assertIn("base pared camara", e["grupos"][0]["skus"][0]["intentados"])
                viejo = [intento(SKU, estado, candidato="Base Pared Cámara",
                                 creado=hace(CM.REINTENTO_DIAS + 1))]
                e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, viejo)
                self.assertNotIn("base pared camara", e["grupos"][0]["skus"][0]["intentados"])

    def test_un_propuesto_en_curso_no_entra_a_intentados(self):
        """Uno que otro proceso está midiendo AHORA no se esconde del modelo: si lo
        repite, el reclamo lo detecta y la corrida lo dice sin pagar la página."""
        historia = [intento(SKU, "propuesto", candidato="Base Pared Cámara", creado=hace(0.001))]
        e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, historia)
        self.assertNotIn("base pared camara", e["grupos"][0]["skus"][0]["intentados"])

    def test_en_espera_marca_al_sku_con_un_candidato_que_aguarda_su_reintento(self):
        """Un `error` o un `bloqueado` recientes que no concluyeron ESPERAN su
        reintento; `mejorar` lo usa para no enfriar al SKU por eso. Pasado
        `REINTENTO_DIAS` ya no esperan, y un muro que gastó sus rondas tampoco:
        ese ya es respuesta. (Sin enfriamiento, para ver también lo concluido.)"""
        casos = [
            ("sin historia", [], False),
            ("error con la ronda devuelta", [intento(SKU, "error", creado=hace(1), ronda=0)], True),
            ("bloqueado en su ronda 1", [intento(SKU, "bloqueado", creado=hace(1))], True),
            ("error viejo", [intento(SKU, "error", creado=hace(CM.REINTENTO_DIAS + 1))], False),
            ("propuesto en curso", [intento(SKU, "propuesto", creado=hace(0.001))], False),
            ("muro en su última ronda", [intento(SKU, "bloqueado", creado=hace(1),
                                                 ronda=CM.RONDAS_MAX, resuelto=hace(1))], False),
            ("sin_mejora", [intento(SKU, "sin_mejora", creado=hace(1), resuelto=hace(1))], False),
        ]
        for nombre, historia, espera in casos:
            with self.subTest(caso=nombre):
                e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, historia,
                                          respetar_enfriamiento=False)
                self.assertIs(e["grupos"][0]["skus"][0]["en_espera"], espera)

    def test_un_bloqueado_que_agoto_sus_rondas_ya_es_concluyente(self):
        """En la ronda `RONDAS_MAX` el muro ya es la respuesta: se queda
        `bloqueado`, pero enfría, cuenta para agotar y va a «ya probados» para
        siempre. En la ronda 1 sigue siendo reintentable."""
        cerrado = intento(SKU, "bloqueado", candidato="Base Pared Cámara",
                          ronda=CM.RONDAS_MAX, resuelto=hace(1))
        e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, [cerrado])
        self.assertEqual(e["fuera"], {"en enfriamiento": 1})
        e, _, _ = self._elegibles(
            [fila_universo(SKU)], {SKU: self.FLOJO()},
            [{**cerrado, "resuelto_en": hace(CM.ENFRIAMIENTO_DIAS + 1)}])
        self.assertIn("base pared camara", e["grupos"][0]["skus"][0]["intentados"])
        agotado = [intento(SKU, "bloqueado", candidato=f"candidato numero {i}",
                           ronda=CM.RONDAS_MAX, resuelto=hace(90))
                   for i in range(CM.MAX_POR_SKU)]
        e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, agotado)
        self.assertEqual(e["fuera"], {"agotado: ya se probaron los candidatos permitidos": 1})
        abierto = intento(SKU, "bloqueado", candidato="Base Pared Cámara", ronda=1,
                          resuelto=hace(1))
        e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, [abierto])
        self.assertEqual(e["fuera"], {})

    def test_un_sin_candidato_reciente_enfria(self):
        """`sin_candidato` es una respuesta, como `termino_ok`: el SKU descansa."""
        historia = [intento(SKU, "sin_candidato", candidato=TERMINO, anterior=TERMINO,
                            resuelto=hace(1))]
        e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, historia)
        self.assertEqual(e["fuera"], {"en enfriamiento": 1})

    def test_pasado_el_enfriamiento_vuelve_a_entrar(self):
        historia = [intento(SKU, "sin_mejora", resuelto=hace(CM.ENFRIAMIENTO_DIAS + 1))]
        e, _, _ = self._elegibles([fila_universo(SKU)], {SKU: self.FLOJO()}, historia)
        self.assertEqual(len(e["grupos"]), 1)

    def test_las_opciones_abren_cada_puerta(self):
        """El botón del panel pide UN SKU y lo quiere aunque esté pausado, sea
        manual o su medición sea vieja; el script por lotes, no."""
        casos = [
            ({"solo_activos": False}, fila_universo(SKU, activa=False), []),
            ({"incluir_manuales": True}, fila_universo(SKU, termino_origen="manual"), []),
            ({"max_dias": None}, fila_universo(SKU, medido_en=hace(400)), []),
            ({"respetar_enfriamiento": False}, fila_universo(SKU),
             [intento(SKU, "sin_mejora", resuelto=hace(1))]),
        ]
        for opciones, fila, historia in casos:
            with self.subTest(opciones=opciones):
                e, _, _ = self._elegibles([fila], {SKU: self.FLOJO()}, historia, **opciones)
                self.assertEqual(e["fuera"], {})
                self.assertEqual(len(e["grupos"]), 1)

    def test_la_lista_pedida_viaja_a_la_consulta_sin_repetidos(self):
        _, _, base = self._elegibles([], skus=[SKU2, SKU, SKU2, ""])
        self.assertEqual(base.lecturas, [{"canal": "mercado_libre", "skus": [SKU, SKU2]}])
        _, _, base = self._elegibles([])
        self.assertEqual(base.lecturas, [{"canal": "mercado_libre", "skus": None}])


# ── 3. La propuesta ──────────────────────────────────────────────────────────

class ArmarUsuario(unittest.TestCase):
    """`armar_usuario`: lo que ve el modelo para proponer."""

    def _grupo(self):
        fs = [
            rival(SKU, 1, "mismo", titulo="Soporte mural para cámara de vigilancia"),
            rival(SKU, 2, "otro_producto", titulo="Soporte de casco para cámara deportiva"),
            rival(SKU, 3, "refaccion", titulo="Tornillos de repuesto para soporte"),
            rival(SKU, 4, juzgado=False, titulo="Rival que nadie ha juzgado"),
            rival(SKU, 5, "mismo", nuestro=True, titulo="Nuestra propia publicación"),
        ]
        return {"termino_id": 10, "termino": TERMINO, "categoria_id": "MLM0001",
                "categoria_nombre": "Soportes", "ruta": "Hogar > Seguridad > Soportes",
                "skus": [{"sku": SKU, "titulo": TITULO, "resumen": CJ.resumen(fs), "filas": fs,
                          "intentados": ["soporte camara pared", "base para camara"]}]}

    def test_lleva_las_seis_piezas(self):
        texto = CM.armar_usuario(self._grupo(), ["camara de seguridad", "soporte camara ip"])
        lineas = texto.split("\n")
        self.assertIn(f"- {TITULO}", lineas)
        self.assertIn(f"TÉRMINO ACTUAL: {TERMINO}", lineas)
        self.assertIn("CATEGORÍA: Hogar > Seguridad > Soportes", lineas)
        # Comparables: cómo llama el mercado al producto.
        self.assertIn("- Soporte mural para cámara de vigilancia", lineas)
        # Descartados CON su clase: es lo que le dice al modelo qué evitar.
        self.assertIn("- [otro_producto] Soporte de casco para cámara deportiva", lineas)
        self.assertIn("- [refaccion] Tornillos de repuesto para soporte", lineas)
        self.assertIn("BÚSQUEDAS CON DEMANDA EN LA CATEGORÍA: camara de seguridad · "
                      "soporte camara ip", lineas)
        self.assertIn("YA PROBADOS (no repetir): base para camara · soporte camara pared",
                      lineas)

    def test_los_comparables_van_antes_que_los_descartados(self):
        lineas = CM.armar_usuario(self._grupo(), []).split("\n")
        buenos = lineas.index("RIVALES QUE SÍ ERAN EL MISMO PRODUCTO")
        malos = lineas.index("DESCARTADOS (lo que hay que evitar)")
        comparable = lineas.index("- Soporte mural para cámara de vigilancia")
        self.assertTrue(buenos < comparable < malos)

    def test_lo_nuestro_y_lo_sin_juzgar_no_se_le_cuentan_al_modelo(self):
        """Un rival sin veredicto no es ni ejemplo ni contraejemplo; y nuestra
        propia publicación no es «cómo lo llama el mercado»."""
        texto = CM.armar_usuario(self._grupo(), [])
        self.assertNotIn("Rival que nadie ha juzgado", texto)
        self.assertNotIn("Nuestra propia publicación", texto)

    def test_sin_tendencias_ni_probados_esas_lineas_no_aparecen(self):
        g = self._grupo()
        g["skus"][0]["intentados"] = []
        texto = CM.armar_usuario(g, [])
        self.assertNotIn("BÚSQUEDAS CON DEMANDA", texto)
        self.assertNotIn("YA PROBADOS", texto)

    def test_sin_rivales_lo_dice_en_vez_de_dejar_el_hueco(self):
        g = self._grupo()
        g["skus"][0]["filas"] = []
        self.assertEqual(CM.armar_usuario(g, []).split("\n").count("- (ninguno)"), 2)

    def test_las_hermanas_no_repiten_titulos_ni_rivales(self):
        """Las variantes de un grupo comparten término y por eso ven los mismos
        rivales: mandarlos dos veces solo encarece la llamada."""
        g = self._grupo()
        g["skus"].append({**g["skus"][0], "sku": SKU2})
        lineas = CM.armar_usuario(g, []).split("\n")
        self.assertEqual(lineas.count(f"- {TITULO}"), 1)
        self.assertEqual(lineas.count("- Soporte mural para cámara de vigilancia"), 1)

    def test_topa_la_lista_de_descartados(self):
        g = self._grupo()
        g["skus"][0]["filas"] = [rival(SKU, i, "otro_producto") for i in range(1, 31)]
        lineas = CM.armar_usuario(g, []).split("\n")
        self.assertEqual(sum(1 for x in lineas if x.startswith("- [otro_producto]")), 12)


class Proponer(unittest.TestCase):
    """`proponer`: qué se le acepta al modelo. No mide ni escribe."""

    def _proponer(self, res, g=None, tendencias=None):
        base = Base(tendencias=tendencias or [])
        ia = mock.Mock(return_value=res)
        with base.puesta(), mock.patch.object(CM.ia_json, "completar_json", ia):
            p = CM.proponer(g or grupo(), modelo=MODELO)
        return p, ia, base

    def test_devuelve_los_candidatos_ya_normalizados(self):
        p, ia, base = self._proponer(respuesta("Soporte de Pared para Cámara"),
                                     tendencias=["camara de seguridad"])
        self.assertTrue(p["ok"])
        self.assertEqual(p["candidatos"], [{"termino": "soporte de pared para cámara",
                                            "motivo": "nombra el producto exacto"}])
        self.assertEqual(p["diagnostico"], "el término es demasiado general")
        self.assertEqual((p["usd"], p["proveedor"]), (0.001, "deepseek"))
        self.assertFalse(p["termino_ok"])
        # El modelo pedido viaja tal cual: `ia_json` no cambia de modelo a escondidas.
        self.assertEqual(ia.call_args.kwargs["modelo"], MODELO)
        self.assertIn("BÚSQUEDAS CON DEMANDA EN LA CATEGORÍA: camara de seguridad",
                      ia.call_args.args[1])
        self.assertEqual(base.reclamos, [], "proponer no escribe")

    def test_descarta_el_termino_actual_aunque_venga_disfrazado(self):
        """«Soporte para Cámara» ES el término actual: medirlo otra vez es pagar
        por la misma página que ya falló."""
        p, _, _ = self._proponer(respuesta("Soporte para Cámara", "soporte-para-camara",
                                           CANDIDATO))
        self.assertEqual([c["termino"] for c in p["candidatos"]], [CANDIDATO])

    def test_descarta_los_ya_probados(self):
        g = grupo(intentados=["soporte pared camara", "base para camara ip"])
        p, _, _ = self._proponer(respuesta("Soporte Pared Cámara", "base para cámara IP",
                                           CANDIDATO), g)
        self.assertEqual([c["termino"] for c in p["candidatos"]], [CANDIDATO])

    def test_descarta_lo_que_no_tiene_forma_de_termino_y_los_repetidos(self):
        p, _, _ = self._proponer(respuesta("soporte", CANDIDATO, "Soporte Pared Cámara Seguridad"))
        self.assertEqual([c["termino"] for c in p["candidatos"]], [CANDIDATO])

    def test_recorta_a_max_por_grupo_respetando_el_orden(self):
        """El modelo los manda «del mejor al peor»: se miden los primeros, porque
        cada uno es una página de Apify."""
        p, _, _ = self._proponer(respuesta("soporte pared camara", "base camara seguridad",
                                           "brazo camara vigilancia"))
        self.assertEqual(CM.MAX_POR_GRUPO, 2)
        self.assertEqual([c["termino"] for c in p["candidatos"]],
                         ["soporte pared camara", "base camara seguridad"])

    def test_los_ejemplos_del_prompt_pasan_por_normalizar(self):
        """El modelo imita los ejemplos del prompt. Uno de 7 palabras
        («… de pared para cámara de seguridad») lo tiraba `normalizar`, y el grupo
        se quedaba sin candidato en cada corrida."""
        ejemplos = re.findall(r"«([^»]+)»", CM._SYSTEM)
        self.assertGreaterEqual(len(ejemplos), 5)
        for e in ejemplos:
            with self.subTest(ejemplo=e):
                self.assertIsNotNone(CM.normalizar(e))

    def test_termino_ok_solo_si_no_hay_candidatos(self):
        p, _, _ = self._proponer(respuesta(termino_ok=True))
        self.assertTrue(p["termino_ok"])
        self.assertEqual(p["candidatos"], [])

    def test_termino_ok_con_candidatos_se_contradice_y_mandan_los_candidatos(self):
        """Si el modelo dice «está bien» y a la vez propone otro, no se da por
        bueno el término: se mide el candidato y decide la medición."""
        p, _, _ = self._proponer(respuesta(CANDIDATO, termino_ok=True))
        self.assertFalse(p["termino_ok"])
        self.assertEqual([c["termino"] for c in p["candidatos"]], [CANDIDATO])

    def test_termino_ok_con_solo_candidatos_invalidos_si_cuenta(self):
        """Lo que decide es si QUEDÓ algún candidato, no si el modelo mandó uno."""
        p, _, _ = self._proponer(respuesta("Soporte para Cámara", termino_ok=True))
        self.assertTrue(p["termino_ok"])

    def test_termino_ok_tiene_que_ser_el_booleano(self):
        """`"true"` o `1` no son `True`: ante la duda NO se declara bueno un
        término que ya se sabe que trae pocos rivales."""
        for valor in ("true", 1, "sí", None):
            with self.subTest(valor=valor):
                p, _, _ = self._proponer(respuesta(termino_ok=valor))
                self.assertFalse(p["termino_ok"])

    def test_ia_caida_devuelve_ok_false_y_no_inventa(self):
        p, _, _ = self._proponer(CAIDA)
        self.assertFalse(p["ok"])
        self.assertEqual(p["candidatos"], [])
        self.assertFalse(p["termino_ok"])
        self.assertEqual(p["motivo"], CAIDA["motivo"])
        self.assertEqual(p["usd"], 0.0)

    def test_una_respuesta_cortada_cobra_aunque_no_sirva(self):
        """`completar_json` devuelve `ok: False` con su `usd`: el tope de gasto
        tiene que verlo aunque no haya candidatos."""
        p, _, _ = self._proponer({**CAIDA, "motivo": "respuesta cortada", "usd": 0.0004})
        self.assertFalse(p["ok"])
        self.assertEqual(p["usd"], 0.0004)

    def test_una_respuesta_con_otra_forma_no_revienta(self):
        raros = [{"candidatos": "soporte pared camara"}, {"candidatos": ["suelto", 7, None]},
                 {"candidatos": [{"porque": "sin término"}]}, {}]
        for datos in raros:
            with self.subTest(datos=datos):
                p, _, _ = self._proponer({"ok": True, "usd": 0.0, "proveedor": "deepseek",
                                          "datos": datos})
                self.assertTrue(p["ok"])
                self.assertEqual(p["candidatos"], [])

    def test_que_fallen_las_tendencias_no_tumba_la_propuesta(self):
        """Las tendencias son una ayuda: sin ellas se propone igual."""
        ia = mock.Mock(return_value=respuesta(CANDIDATO))
        with mock.patch.object(CM.supabase_db, "fetch_all",
                               side_effect=RuntimeError("base caída")), \
             mock.patch.object(CM.ia_json, "completar_json", ia), \
             self.assertLogs(LOG, "WARNING"):
            p = CM.proponer(grupo(), modelo=MODELO)
        self.assertEqual([c["termino"] for c in p["candidatos"]], [CANDIDATO])
        self.assertNotIn("BÚSQUEDAS CON DEMANDA", ia.call_args.args[1])


# ── 4. La corrida ────────────────────────────────────────────────────────────

class SinDispersion(unittest.TestCase):
    """`_sin_dispersion`: la misma prueba del Radar sobre los comparables."""

    def test_precios_parejos_pasan(self):
        self.assertTrue(CM._sin_dispersion([500.0, 520.0, 540.0]))

    def test_menos_del_umbral_no_describe_un_mercado(self):
        self.assertFalse(CM._sin_dispersion([]))
        self.assertFalse(CM._sin_dispersion([500.0, 510.0]))

    def test_un_cuartil_alto_de_mas_del_triple_no_pasa(self):
        """Tres «comparables» de $100, $100 y $1,000 no son un solo producto."""
        self.assertFalse(CM._sin_dispersion([100.0, 100.0, 1000.0]))

    def test_el_limite_exacto_pasa_y_un_peso_mas_no(self):
        self.assertEqual(CM.DISPERSION_MAX, 3.0)
        self.assertTrue(CM._sin_dispersion([100.0, 100.0, 300.0, 300.0]))
        self.assertFalse(CM._sin_dispersion([100.0, 100.0, 301.0, 301.0]))

    def test_el_orden_de_llegada_no_importa(self):
        self.assertFalse(CM._sin_dispersion([1000.0, 100.0, 100.0]))

    def test_un_cuartil_bajo_en_cero_no_divide_entre_cero(self):
        self.assertFalse(CM._sin_dispersion([0.0, 0.0, 0.0, 500.0]))


class MejorarNoArranca(unittest.TestCase):
    """Las dos condiciones sin las que la corrida no gasta ni un centavo."""

    def test_sin_apify_no_hace_nada_y_lo_dice(self):
        """Sin Apify la medición vuelve vacía y el candidato quedaría quemado
        como «sin mejora» sin haberse probado nunca."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], apify=False)
        self.assertEqual(t.out["detenido"], "Apify no está disponible (falta APIFY_API_KEY)")
        t.ia.assert_not_called()
        self.assertEqual(t.base.reclamos, [])
        self.assertEqual(t.lotes, [])
        self.assertEqual(t.out["sugerencias"], 0)
        self.assertEqual(t.out["usd_ia"], 0.0)

    def test_un_modelo_sin_precio_no_corre(self):
        """El modelo llega de una variable de entorno —texto libre—. Uno sin
        precio dejaría ciego al tope de gasto: sumaría cero mientras cobra."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], modelo="modelo-que-no-existe")
        self.assertEqual(t.out["detenido"], "modelo sin precio: modelo-que-no-existe")
        t.ia.assert_not_called()
        self.assertEqual(t.base.reclamos, [])
        self.assertEqual(t.lotes, [])

    def test_sin_grupos_no_pregunta_por_apify_ni_llama_a_nadie(self):
        t = correr([], ia=[], apify=False)
        self.assertIsNone(t.out["detenido"])
        self.assertEqual((t.out["grupos"], t.out["skus"], t.out["sugerencias"]), (0, 0, 0))
        self.assertEqual(t.lotes, [])

    def test_con_el_tope_de_ia_agotado_no_propone(self):
        t = correr([grupo({SKU: 0, SKU2: 1})], ia=[respuesta(CANDIDATO)], tope_usd=0.0)
        self.assertEqual(t.out["detenido"], "tope de gasto de IA")
        self.assertEqual(t.out["sin_cubrir"], 2)
        t.ia.assert_not_called()
        self.assertEqual(t.base.bitacora[0]["estado"], "parcial")

    def test_el_tope_de_ia_es_acumulativo_entre_grupos(self):
        """Un tope que se estrena en cada grupo nunca se alcanza."""
        g1, g2 = grupo({SKU: 0}), grupo({SKU2: 0, SKU3: 0}, termino="filtro pop microfono", tid=11)
        t = correr([g1, g2], ia=[respuesta(termino_ok=True, usd=0.002)], tope_usd=0.002)
        self.assertEqual(t.ia.call_count, 1)
        self.assertEqual(t.out["detenido"], "tope de gasto de IA")
        self.assertEqual(t.out["sin_cubrir"], 2, "los SKUs del grupo que ya no alcanzó")
        self.assertEqual(t.presupuesto.gastado, 0.002)

    def test_si_las_propuestas_agotan_el_tope_no_se_mide(self):
        """El tope se miraba solo antes de cada propuesta: si ella se lo comía,
        igual se pagaba Apify por un candidato que el juez ya no iba a evaluar.
        Ahora no se mide: el intento queda `error` (reintentable, sin gastar
        ronda) y el SKU, sin cubrir."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO, usd=0.01)], tope_usd=0.005,
                   base=Base(rondas={(SKU, CANDIDATO): CM.RONDAS_MAX}),
                   medidos={CANDIDATO: 10})
        self.assertFalse(t.presupuesto.puede())
        self.assertEqual((t.lotes, t.out["paginas"], t.juzgados), ([], 0, []))
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "error",
                         "ni en su última ronda se cierra algo que no se midió")
        self.assertIn(t.base.iids[(SKU, CANDIDATO)], t.base.devueltas,
                      "no se midió: la ronda que sumó el reclamo se devuelve")
        self.assertEqual((t.out["detenido"], t.out["sin_cubrir"]), ("tope de gasto de IA", 1))
        self.assertEqual(t.base.bitacora[0]["estado"], "parcial")


class MejorarPropone(unittest.TestCase):
    """Paso 1: qué se hace con lo que contestó el modelo, antes de medir."""

    def test_termino_ok_se_anota_y_no_mide(self):
        """«El término está bien, ML mezcla refacciones»: no se cambia nada, pero
        se ANOTA, para que el SKU descanse en vez de volver a preguntarse en cada
        corrida."""
        t = correr([grupo({SKU: 1, SKU2: 0})], ia=[respuesta(termino_ok=True)])
        self.assertEqual(t.out["termino_ok"], 2)
        self.assertEqual(t.base.reclamados(), [(SKU, TERMINO, "termino_ok"),
                                               (SKU2, TERMINO, "termino_ok")])
        self.assertEqual(t.lotes, [])
        self.assertEqual(t.juzgados, [])
        self.assertEqual(t.out["paginas"], 0)
        self.assertEqual(t.out["sugerencias"], 0)
        self.assertIsNone(t.out["detenido"])

    def test_ia_caida_cuenta_un_error_y_no_reclama(self):
        t = correr([grupo()], ia=[CAIDA])
        self.assertEqual(t.out["errores"], 1)
        self.assertEqual(t.base.reclamos, [])
        self.assertEqual(t.lotes, [])

    def test_sin_candidatos_validos_no_mide_pero_se_anota(self):
        """Antes no dejaba ninguna fila: el mismo grupo volvía a ser elegible y a
        pagar la misma pregunta en cada corrida (con temperatura 0, la misma
        respuesta). Ahora se anota `sin_candidato`, como un `termino_ok`: con el
        término actual de candidato, para que el SKU descanse."""
        t = correr([grupo({SKU: 0, SKU2: 0})], ia=[respuesta("soporte", "Soporte para Cámara")])
        self.assertEqual(t.out["sin_candidato"], 2)
        self.assertEqual(t.base.reclamados(), [(SKU, TERMINO, "sin_candidato"),
                                               (SKU2, TERMINO, "sin_candidato")])
        self.assertEqual(t.lotes, [])
        self.assertEqual(t.juzgados, [])
        self.assertIn("sin_candidato", CM._PROBADOS)

    def test_nada_nuevo_con_un_candidato_en_espera_no_se_anota(self):
        """El modelo repite el candidato que espera su reintento —con temperatura
        0 es lo normal— y `proponer` lo filtra. Anotar `sin_candidato` ahí
        mandaba al SKU 60 días a descansar por una tanda de Apify caída. No se
        escribe nada y se cuenta aparte. Va por GRUPO: la hermana sin nada en
        espera tampoco se anota, porque lo filtrado fue la unión de las dos."""
        g = grupo({SKU: 0, SKU2: 0}, intentados=[CM.llave(CANDIDATO)])
        g["skus"][0]["en_espera"] = True
        t = correr([g], ia=[respuesta(CANDIDATO)])
        self.assertEqual(t.base.reclamos, [])
        self.assertEqual(t.base.renovados, [])
        self.assertEqual((t.out["en_espera"], t.out["sin_candidato"]), (2, 0))
        self.assertEqual((t.lotes, t.juzgados), ([], []))
        self.assertEqual(t.base.bitacora[0]["detalle"]["en_espera"], 2)

    def test_lo_que_espera_no_frena_un_termino_ok_ni_un_candidato_nuevo(self):
        """«El término está bien» sí es una respuesta y se anota. Y un candidato
        que NO espera nada se reclama y se mide como siempre."""
        g = grupo(intentados=[CM.llave(CANDIDATO)])
        g["skus"][0]["en_espera"] = True
        t = correr([g], ia=[respuesta(termino_ok=True)])
        self.assertEqual(t.base.reclamados(), [(SKU, TERMINO, "termino_ok")])
        self.assertEqual((t.out["termino_ok"], t.out["en_espera"]), (1, 0))
        otro = "brazo para camara de vigilancia"
        t = correr([g], ia=[respuesta(CANDIDATO, otro)], medidos={otro: 0})
        self.assertEqual(t.base.reclamados(), [(SKU, otro, "propuesto")])
        self.assertEqual((t.lotes, t.out["en_espera"]), ([[otro]], 0))

    def test_un_desenlace_repetido_renueva_la_fecha_de_su_fila(self):
        """La fila (SKU, término actual) ya existe —un `termino_ok` de hace 61
        días— y el reclamo choca con la llave única. Antes se ignoraba: la fecha
        no se movía y el SKU volvía a pagarse la propuesta en cada corrida."""
        for respuesta_ia, estado in ((respuesta(termino_ok=True), "termino_ok"),
                                     (respuesta("soporte"), "sin_candidato")):
            with self.subTest(estado=estado):
                t = correr([grupo()], ia=[respuesta_ia],
                           base=Base(concluidos={(SKU, TERMINO)}))
                self.assertEqual(t.base.renovados, [(estado, SKU, TERMINO)])
                self.assertEqual(t.out[estado], 1)

    def test_la_renovacion_no_pisa_un_aceptado_ni_toca_lo_abierto(self):
        """Si el término actual fue una sugerencia ACEPTADA, esa fila solo cambia
        de fecha: su estado es el rastro de que una persona la aplicó. Y nada de
        lo que sigue abierto (`propuesto`, `medido`, `error`…) se renueva."""
        sql = []
        with mock.patch.object(CM.supabase_db, "execute_returning", return_value=None), \
             mock.patch.object(CM.supabase_db, "execute",
                               side_effect=lambda s, p=None: sql.append(s) or 1):
            CM._anotar_desenlace(SKU, grupo(), "diagnóstico", grupo()["skus"][0]["resumen"],
                                 MODELO, "termino_ok")
        texto = re.sub(r"\s+", " ", sql[0])
        self.assertIn("estado = case when estado in ('termino_ok', 'sin_candidato') then %s "
                      "else estado end", texto)
        renovables = set(re.findall(r"'(\w+)'", texto.split("and estado in")[-1]))
        self.assertEqual(renovables, {"termino_ok", "sin_candidato", "aceptado",
                                      "sin_mejora", "descartado"})

    def test_lo_que_no_se_pudo_anotar_no_se_cuenta(self):
        """Otro proceso tiene esa fila abierta: ni reclamo ni renovación. El
        contador no puede decir que se anotó."""
        t = correr([grupo()], ia=[respuesta(termino_ok=True)], base=Base(ajenos={(SKU, TERMINO)}))
        self.assertEqual(t.base.renovados, [("termino_ok", SKU, TERMINO)])
        self.assertEqual(t.out["termino_ok"], 0)

    def test_reclama_antes_de_pagar_la_medicion(self):
        """El intento se anota `propuesto` ANTES de llamar a Apify, con el término
        y los conteos de antes: si el proceso muere a media medición queda el
        rastro del gasto, y otro proceso no mide lo mismo."""
        t = correr([grupo({SKU: 1})], ia=[respuesta(CANDIDATO)], medidos={CANDIDATO: 0})
        self.assertEqual(t.base.eventos, ["reclamo", "medir"])
        self.assertEqual(t.base.reclamos, [
            {"sku": SKU, "candidato": CANDIDATO, "estado": "propuesto", "anterior": TERMINO,
             "anterior_id": 10, "antes": 1, "total": 10}])

    def test_lo_que_ya_tiene_otro_proceso_no_se_mide(self):
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], base=Base(ajenos={(SKU, CANDIDATO)}),
                   medidos={CANDIDATO: 10})
        self.assertEqual(t.lotes, [], "no se paga dos veces la misma búsqueda")
        self.assertEqual(t.juzgados, [])
        self.assertEqual(t.out["paginas"], 0)

    def test_si_otro_proceso_lo_tiene_la_corrida_lo_dice(self):
        """Tras un reinicio a media mejora los intentos quedan `propuesto` 30 min.
        Un segundo clic volvía a pagar la propuesta, no medía nada y terminaba con
        todo en cero: el panel decía «la IA no propuso ningún término», que es
        falso. Ahora queda contado y `detenido` lo explica (el panel lo pinta)."""
        t = correr([grupo({SKU: 0, SKU2: 0})], ia=[respuesta(CANDIDATO)],
                   base=Base(ajenos={(SKU, CANDIDATO), (SKU2, CANDIDATO)}))
        self.assertEqual(t.out["ocupados"], 2)
        self.assertIn("otro proceso ya está probando ese candidato", t.out["detenido"])
        self.assertIn(f"{CM.RECLAMO_MIN} min", t.out["detenido"])
        self.assertEqual(t.base.bitacora[0]["estado"], "parcial")

    def test_si_otro_proceso_tiene_solo_una_hermana_las_demas_siguen(self):
        t = correr([grupo({SKU: 0, SKU2: 0})], ia=[respuesta(CANDIDATO)],
                   base=Base(ajenos={(SKU2, CANDIDATO)}), medidos={CANDIDATO: 0})
        self.assertEqual(t.out["ocupados"], 1)
        self.assertIsNone(t.out["detenido"])
        self.assertEqual(t.lotes, [[CANDIDATO]])

    def test_el_mismo_candidato_para_dos_grupos_se_mide_una_vez(self):
        g1, g2 = grupo({SKU: 0}), grupo({SKU2: 0}, termino="base para camara", tid=11)
        t = correr([g1, g2], ia=[respuesta(CANDIDATO), respuesta(CANDIDATO)],
                   medidos={CANDIDATO: 0})
        self.assertEqual(t.lotes, [[CANDIDATO]])
        self.assertEqual(t.out["paginas"], 1)

    def test_dos_grupos_con_el_mismo_candidato_con_y_sin_acento_pagan_una_pagina(self):
        """Misma llave, distinto texto: son dos URLs distintas para Apify y dos
        términos gemelos en el catálogo. Gana el texto del primero que lo pidió."""
        con, sin = "soporte de pared para cámara", "soporte de pared para camara"
        self.assertEqual(CM.llave(con), CM.llave(sin))
        g1, g2 = grupo({SKU: 0}), grupo({SKU2: 0}, termino="base para camara", tid=11)
        t = correr([g1, g2], ia=[respuesta(con), respuesta(sin)], medidos={con: 0})
        self.assertEqual(t.lotes, [[con]])
        self.assertEqual(t.out["paginas"], 1)
        self.assertEqual(t.base.reclamados(), [(SKU, con, "propuesto"), (SKU2, con, "propuesto")])

    def test_mide_en_tandas(self):
        """Apify cobra por corrida: los candidatos de muchos grupos van juntos,
        `TANDA` URLs por corrida, no una corrida por SKU."""
        n = CM.TANDA + 1
        grupos = [grupo({f"PRU-{i:04d}-NEG": 0}, termino=f"termino general {i}", tid=100 + i)
                  for i in range(n)]
        ia = [respuesta(f"candidato distinto numero {i}") for i in range(n)]
        t = correr(grupos, ia=ia, max_paginas=100)
        self.assertEqual([len(x) for x in t.lotes], [CM.TANDA, 1])
        self.assertEqual(t.out["paginas"], n)

    def test_el_tope_de_paginas_deja_sin_cubrir_y_lo_dice(self):
        g1, g2 = grupo({SKU: 0}), grupo({SKU2: 0, SKU3: 0}, termino="base para camara", tid=11)
        t = correr([g1, g2], ia=[respuesta(CANDIDATO), respuesta("brazo camara vigilancia")],
                   medidos={CANDIDATO: 0}, max_paginas=1)
        self.assertEqual(t.lotes, [[CANDIDATO]])
        self.assertEqual(t.out["detenido"], "tope de páginas de Apify")
        self.assertEqual(t.out["sin_cubrir"], 2)
        self.assertEqual(t.base.reclamados(), [(SKU, CANDIDATO, "propuesto")],
                         "lo que no se va a medir tampoco se reclama")
        self.assertEqual(t.base.bitacora[0]["estado"], "parcial")


class MejorarReusa(unittest.TestCase):
    """Un término que ya se midió no se vuelve a pagar."""

    CATALOGO = "Soporte Pared Cámara"

    def _catalogo(self, **cambios):
        return [{"id": 55, "termino": self.CATALOGO, "estado": "ok", "medido_en": hace(3),
                 "resultados": 10, **cambios}]

    def test_un_termino_ya_medido_en_el_catalogo_no_paga_pagina(self):
        """Se cruza por `llave`: el candidato llega sin acento ni mayúsculas y
        aun así encuentra la medición que ya se pagó. Y el reuso no cuenta contra
        el tope de páginas: con el tope en cero se hace igual."""
        t = correr([grupo()], ia=[respuesta("soporte pared camara")],
                   base=Base(catalogo=self._catalogo()), max_paginas=0,
                   filas={55: rivales(SKU, mismos=4, otros=6)})
        self.assertEqual(t.lotes, [], "no se llamó a Apify")
        self.assertEqual((t.out["reusados"], t.out["paginas"]), (1, 0))
        self.assertIsNone(t.out["detenido"])
        self.assertEqual(t.juzgados, [([SKU], 55)])
        # El intento se anota con el texto DEL CATÁLOGO: es el que se asignaría.
        self.assertEqual(t.base.reclamados(), [(SKU, self.CATALOGO, "propuesto")])
        self.assertEqual(t.base.escalares, [], "el id sale del catálogo, no de otra consulta")
        self.assertEqual(t.base.resueltos[t.base.iids[(SKU, self.CATALOGO)]],
                         ("medido", 55, 4, 10))

    def test_una_medicion_vieja_se_paga_de_nuevo(self):
        t = correr([grupo()], ia=[respuesta("soporte pared camara")],
                   base=Base(catalogo=self._catalogo(medido_en=hace(40))),
                   medidos={self.CATALOGO: 0}, reuso_dias=30)
        self.assertEqual(t.lotes, [[self.CATALOGO]])
        self.assertEqual((t.out["reusados"], t.out["paginas"]), (0, 1))

    def test_sin_limite_de_dias_lo_viejo_tambien_se_reusa(self):
        t = correr([grupo()], ia=[respuesta("soporte pared camara")],
                   base=Base(catalogo=self._catalogo(medido_en=hace(400))), reuso_dias=None)
        self.assertEqual(t.lotes, [])
        self.assertEqual(t.out["reusados"], 1)

    def test_un_termino_bloqueado_o_vacio_no_es_una_medicion_reusable(self):
        """«Bloqueado» es «no sabemos qué hay»: reusarlo sería dar por medido lo
        que ML no dejó ver. Con más de `REINTENTO_DIAS`, se vuelve a medir."""
        viejo = hace(CM.REINTENTO_DIAS + 1)
        for estado in ("bloqueado", "vacio", None):
            with self.subTest(estado=estado):
                t = correr([grupo()], ia=[respuesta("soporte pared camara")],
                           base=Base(catalogo=self._catalogo(estado=estado, medido_en=viejo)),
                           medidos={self.CATALOGO: 0})
                self.assertEqual(t.lotes, [[self.CATALOGO]])
                self.assertEqual(t.out["reusados"], 0)

    def test_un_vacio_reciente_del_catalogo_se_cierra_sin_pagar(self):
        """ML dijo «nada» hace un día: volver a medirlo es pagar la misma página
        para oír lo mismo. Es una respuesta: `sin_mejora`, contra ese término."""
        t = correr([grupo()], ia=[respuesta("soporte pared camara")],
                   base=Base(catalogo=self._catalogo(estado="vacio", medido_en=hace(1),
                                                      resultados=0)), max_paginas=0)
        self.assertEqual((t.lotes, t.out["paginas"], t.juzgados), ([], 0, []))
        self.assertEqual(t.base.resueltos[t.base.iids[(SKU, self.CATALOGO)]],
                         ("sin_mejora", 55, 0, 0))
        self.assertEqual((t.out["sin_mejora"], t.out["errores"], t.out["sin_cubrir"]), (1, 0, 0))
        self.assertIsNone(t.out["detenido"], "no gastó página: el tope no lo detuvo")

    def test_un_muro_reciente_del_catalogo_no_se_paga(self):
        """ML muró ese término hace un día: no se paga otra vez. Queda `bloqueado`
        con su ronda —el muro es del término— y se cuenta."""
        cat = self._catalogo(estado="bloqueado", medido_en=hace(1), resultados=0)
        t = correr([grupo()], ia=[respuesta("soporte pared camara")], base=Base(catalogo=cat))
        self.assertEqual((t.lotes, t.out["paginas"]), ([], 0))
        iid = t.base.iids[(SKU, self.CATALOGO)]
        self.assertEqual((t.base.resueltos[iid][0], t.base.con_fecha[iid]), ("bloqueado", False))
        self.assertEqual(t.out["bloqueados"], 1)
        # En su última ronda ya es la respuesta: se queda `bloqueado`, con fecha.
        t = correr([grupo()], ia=[respuesta("soporte pared camara")],
                   base=Base(catalogo=cat, rondas={(SKU, self.CATALOGO): CM.RONDAS_MAX}))
        iid = t.base.iids[(SKU, self.CATALOGO)]
        self.assertEqual((t.base.resueltos[iid][0], t.base.con_fecha[iid]), ("bloqueado", True))


class MejorarDecide(unittest.TestCase):
    """Pasos 2 y 3: medir, juzgar y decidir. Aquí está el dinero."""

    def _con_candidato(self, antes, despues, **kw):
        """Un grupo, un candidato medido con id 77, y sus rivales ya juzgados."""
        return correr([grupo(antes)], ia=[respuesta(CANDIDATO)], base=Base(ids={CANDIDATO: 77}),
                      medidos={CANDIDATO: 10}, filas={77: despues}, **kw)

    def test_las_constantes_de_la_regla(self):
        """Cambiarlas cambia cuántas búsquedas se pagan y cuántas sugerencias
        salen: que sea una decisión, no un descuido."""
        self.assertEqual((CM.UMBRAL, CM.HISTERESIS), (3, 2))

    def test_un_candidato_bloqueado_queda_bloqueado(self):
        """ML no nos dejó ver: no es «sin mejora» (no se probó) y queda
        reintentable. Tampoco se juzga nada: no hay filas nuevas."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], medidos={CANDIDATO: 0},
                   murados={CANDIDATO})
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "bloqueado")
        self.assertEqual(t.out["bloqueados"], 1)
        self.assertEqual((t.out["sin_mejora"], t.out["sugerencias"], t.out["errores"]), (0, 0, 0))
        self.assertEqual(t.juzgados, [])
        self.assertEqual(t.base.escalares, [])

    def test_un_candidato_sin_filas_queda_error_no_sin_mejora(self):
        """Cero filas sin bloqueo puede ser «ML no tiene nada» o «Apify no
        volvió», y desde aquí no se distingue. NO es concluyente: si quedara
        `sin_mejora` el candidato se quemaría sin haberse probado."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], medidos={CANDIDATO: 0})
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "error")
        self.assertEqual(t.out["errores"], 1)
        self.assertEqual(t.out["sin_mejora"], 0)
        self.assertEqual(t.juzgados, [])

    def test_si_la_tanda_truena_el_candidato_queda_error_y_la_pagina_se_cuenta(self):
        with self.assertLogs(LOG, "WARNING"):
            t = correr([grupo()], ia=[respuesta(CANDIDATO)], medir_truena=True)
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "error")
        self.assertEqual(t.out["paginas"], 1, "lo que se mandó a medir se cuenta aunque falle")
        self.assertEqual(t.out["errores"], 1)

    def test_medido_pero_sin_termino_ok_en_el_catalogo_es_error(self):
        """La medición dijo que guardó filas, pero el catálogo no tiene el término
        como `ok`: no hay contra qué juzgar."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], medidos={CANDIDATO: 10})
        self.assertEqual(t.base.escalares, [("mercado_libre", CANDIDATO)])
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "error")
        self.assertEqual(t.juzgados, [])

    def test_gana_y_queda_como_sugerencia_sin_cambiar_el_termino(self):
        t = self._con_candidato({SKU: 0}, rivales(SKU, mismos=3, otros=7))
        self.assertEqual(t.juzgados, [([SKU], 77)], "se juzga CONTRA el término candidato")
        self.assertEqual(t.base.resueltos[t.base.iids[(SKU, CANDIDATO)]], ("medido", 77, 3, 10))
        self.assertEqual(t.out["sugerencias"], 1)
        self.assertEqual(t.out["detalle"],
                         [{"sku": SKU, "termino": CANDIDATO, "antes": 0, "despues": 3}])
        self.assertEqual((t.out["paginas"], t.out["sin_mejora"], t.out["errores"]), (1, 0, 0))
        self.assertIsNone(t.out["detenido"])
        # NUNCA cambia un término sola: eso mueve el precio de mercado de los KAM.
        t.asignar.assert_not_called()

    def test_tres_contra_dos_no_gana(self):
        """La búsqueda rota ~2 de 10 rivales entre capturas: pasar de 2 a 3
        comparables cabe en el ruido y no justifica mover el término."""
        t = self._con_candidato({SKU: 2}, rivales(SKU, mismos=3, otros=7))
        self.assertEqual(t.base.resueltos[t.base.iids[(SKU, CANDIDATO)]],
                         ("sin_mejora", 77, 3, 10))
        self.assertEqual((t.out["sugerencias"], t.out["sin_mejora"]), (0, 1))
        self.assertEqual(t.out["detalle"], [])

    def test_gana_justo_en_antes_mas_histeresis(self):
        t = self._con_candidato({SKU: 1}, rivales(SKU, mismos=3, otros=7))
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "medido")

    def test_cuatro_contra_dos_si_gana(self):
        t = self._con_candidato({SKU: 2}, rivales(SKU, mismos=4, otros=6))
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "medido")

    def test_menos_del_umbral_no_gana_aunque_mejore(self):
        """De 0 a 2 es mejor, pero con 2 comparables el Radar sigue sin poder dar
        una referencia: no vale un cambio de término."""
        t = self._con_candidato({SKU: 0}, rivales(SKU, mismos=2, otros=8))
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "sin_mejora")
        self.assertEqual(t.out["sugerencias"], 0)

    def test_con_precios_dispersos_no_gana(self):
        """Tres «comparables» con precios de $100 a $1,000 no describen un solo
        producto: el candidato no arregló nada."""
        t = self._con_candidato(
            {SKU: 0}, rivales(SKU, mismos=3, otros=7, precios=[100.0, 100.0, 1000.0]))
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "sin_mejora")

    def test_con_rivales_sin_juzgar_no_gana(self):
        """Si al juez no le alcanzó para juzgar todos los rivales del candidato,
        «3 comparables» no es una cuenta cerrada: no gana.

        Pero TAMPOCO pierde. «Sin juzgar» es «no sé», y cerrarlo como sin_mejora
        quemaría para siempre un candidato ya pagado en Apify que nadie evaluó.
        Queda en `error`: reintentable, y sin castigar al SKU."""
        t = self._con_candidato({SKU: 0}, rivales(SKU, mismos=3, otros=6, pendientes=1))
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "error")
        self.assertEqual((t.out["sugerencias"], t.out["sin_mejora"], t.out["errores"]), (0, 0, 1))

    def test_cada_sku_del_grupo_se_decide_contra_su_propio_antes(self):
        """El veredicto es de la PAREJA: el mismo candidato le sirve a la variante
        que tenía 0 comparables y no a la que ya tenía 2."""
        despues = rivales(SKU, mismos=3, otros=7) + rivales(SKU2, mismos=3, otros=7)
        t = self._con_candidato({SKU: 0, SKU2: 2}, despues)
        self.assertEqual(t.juzgados, [([SKU, SKU2], 77)])
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "medido")
        self.assertEqual(t.base.estado_de(SKU2, CANDIDATO), "sin_mejora")
        self.assertEqual((t.out["sugerencias"], t.out["sin_mejora"]), (1, 1))

    def test_de_dos_candidatos_que_ganan_queda_una_sola_sugerencia(self):
        """Dos filas `medido` para el mismo SKU dejarían ambigua la sugerencia que
        ve el panel: se queda la que da más comparables."""
        otro = "brazo camara vigilancia"
        for orden in ([CANDIDATO, otro], [otro, CANDIDATO]):
            with self.subTest(orden=orden):
                t = correr([grupo()], ia=[respuesta(*orden)],
                           base=Base(ids={CANDIDATO: 77, otro: 78}),
                           medidos={CANDIDATO: 10, otro: 10},
                           filas={77: rivales(SKU, mismos=3, otros=7),
                                  78: rivales(SKU, mismos=5, otros=5)})
                self.assertEqual(t.base.estado_de(SKU, otro), "medido")
                self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "sin_mejora")
                self.assertEqual(t.out["sugerencias"], 1)
                self.assertEqual(t.out["detalle"][0]["termino"], otro)

    def test_sin_rivales_contables_es_sin_mejora_no_error(self):
        """Todo lo que trajo el candidato es NUESTRO o no tiene precio: no queda
        nada pendiente, así que sí se probó. Antes caía en `error` (el resumen dice
        `completo=False` con cero contables) y se reintentaba sin fin."""
        t = self._con_candidato({SKU: 0}, rivales(SKU, mismos=4, nuestro=True))
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "sin_mejora")
        self.assertEqual((t.out["sin_mejora"], t.out["errores"]), (1, 0))


class MejorarRondas(unittest.TestCase):
    """Lo que no concluyó se reintenta, pero no para siempre: en la ronda
    `RONDAS_MAX` un fallo DEL CANDIDATO ya es su respuesta. Lo que falla de
    nuestro lado no gasta ronda."""

    ULTIMA = {(SKU, CANDIDATO): CM.RONDAS_MAX}

    def test_el_reclamo_suma_ronda_y_no_rereclama_un_muro_cerrado(self):
        """Se lee el SQL: sin base no se puede ejecutar el `on conflict`."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], medidos={CANDIDATO: 0})
        sql = re.sub(r"\s+", " ", t.base.sql_reclamo)
        self.assertIn("ronda = enrich.market_termino_intento.ronda + case when "
                      "enrich.market_termino_intento.estado = 'propuesto' then 0 else 1 end", sql,
                      "un propuesto huérfano nunca se midió: recogerlo no gasta ronda")
        self.assertIn(f"(enrich.market_termino_intento.estado = 'bloqueado' and "
                      f"enrich.market_termino_intento.ronda < {CM.RONDAS_MAX})", sql)
        self.assertIn("returning id, ronda", sql)

    def test_el_rereclamo_lleva_el_termino_de_hoy(self):
        """Si el término del SKU cambió desde el intento anterior, la fila
        re-reclamada seguía con el viejo y `aceptar` descartaba la sugerencia
        buena por «vieja»."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], medidos={CANDIDATO: 0})
        sql = re.sub(r"\s+", " ", t.base.sql_reclamo)
        actualiza = sql.split("do update")[1].split(" where ")[0]
        for columna in ("termino_anterior", "termino_anterior_id"):
            self.assertIn(f"{columna} = excluded.{columna}", actualiza)
        for columna in ("comparables_despues", "total_despues", "resuelto_por"):
            self.assertIn(f"{columna} = null", actualiza, "no hereda lo de la ronda anterior")

    def test_un_cero_en_la_ultima_ronda_es_sin_mejora(self):
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], base=Base(rondas=self.ULTIMA),
                   medidos={CANDIDATO: 0})
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "sin_mejora")
        self.assertTrue(t.base.con_fecha[t.base.iids[(SKU, CANDIDATO)]])
        self.assertEqual((t.out["sin_mejora"], t.out["errores"]), (1, 0))

    def test_un_cero_en_la_primera_ronda_sigue_siendo_error(self):
        """Y se queda con su ronda: el cero es del candidato."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], medidos={CANDIDATO: 0})
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "error")
        self.assertFalse(t.base.con_fecha[t.base.iids[(SKU, CANDIDATO)]])
        self.assertEqual(t.base.devueltas, set())

    def test_un_muro_en_la_ultima_ronda_se_queda_bloqueado_con_fecha(self):
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], base=Base(rondas=self.ULTIMA),
                   medidos={CANDIDATO: 0}, murados={CANDIDATO})
        iid = t.base.iids[(SKU, CANDIDATO)]
        self.assertEqual((t.base.resueltos[iid][0], t.base.con_fecha[iid]), ("bloqueado", True))
        self.assertEqual(t.out["bloqueados"], 1)

    def test_si_la_tanda_truena_no_gasta_ronda(self):
        """El término ni volvió: no es culpa del candidato. No se cierra, y la
        ronda que sumó el reclamo se DEVUELVE: antes se quedaba sumada y el
        candidato llegaba a su última ronda con una sola prueba de verdad."""
        with self.assertLogs(LOG, "WARNING"):
            t = correr([grupo()], ia=[respuesta(CANDIDATO)], base=Base(rondas=self.ULTIMA),
                       medir_truena=True)
        self.assertEqual(t.base.estado_de(SKU, CANDIDATO), "error")
        self.assertEqual(t.base.devueltas, {t.base.iids[(SKU, CANDIDATO)]})

    def test_un_juicio_a_medias_gasta_ronda_solo_si_el_juez_corrio_entero(self):
        """Un rival que el modelo dejó sin contestar en un juicio que terminó
        limpio es del candidato; uno que quedó sin juzgar porque el juez se
        detuvo, falló o no pudo guardar, no."""
        casos = [({}, "sin_mejora"), ({"detenido": "tope de gasto"}, "error"),
                 ({"fallidos": 1}, "error"), ({"sin_guardar": 1}, "error")]
        for juez, esperado in casos:
            with self.subTest(juez=juez):
                t = correr([grupo()], ia=[respuesta(CANDIDATO)],
                           base=Base(ids={CANDIDATO: 77}, rondas=self.ULTIMA),
                           medidos={CANDIDATO: 10}, juez=juez,
                           filas={77: rivales(SKU, mismos=3, otros=6, pendientes=1)})
                self.assertEqual(t.base.estado_de(SKU, CANDIDATO), esperado)
                self.assertEqual(bool(t.base.devueltas), esperado == "error",
                                 "solo el fallo de nuestro lado devuelve la ronda")


class FalloNuestroNoEnfria(unittest.TestCase):
    """Tres clics seguidos en «Buscar mejor término», con la base entre uno y
    otro. Juntar dos arreglos (lo reciente va a «ya probados» y `sin_candidato`
    deja fila) resucitó el defecto de R10: la tanda de Apify truena, al segundo
    clic el modelo repite sus candidatos, `proponer` los filtra y se anotaba
    `sin_candidato`, que enfriaba al SKU 60 días sin haber probado nada."""

    C2 = "brazo para camara de vigilancia"

    def _elegibles(self, historia: list[dict[str, Any]]) -> dict[str, Any]:
        """Como la llama `mejorar_sku`."""
        base = Base(universo=[fila_universo(SKU)], historia=historia)
        flojo = rivales(SKU, mismos=0, otros=10)
        with base.puesta(), mock.patch.object(CM.competencia_juez, "filas",
                                              lambda skus, termino_id=None: list(flojo)):
            return CM.elegibles([SKU], solo_activos=False, incluir_manuales=True, max_dias=None)

    @staticmethod
    def _historia(base: Base) -> list[dict[str, Any]]:
        """Las filas que dejó una corrida, como las leería la siguiente."""
        filas = []
        for r in base.reclamos:
            iid = base.iids.get((r["sku"], r["candidato"]))
            if iid is None:
                continue
            estado = (base.resueltos.get(iid) or (r["estado"],))[0]
            concluyo = estado not in CM._NO_CONCLUYENTES
            filas.append(intento(r["sku"], estado, candidato=r["candidato"],
                                 anterior=r["anterior"], creado=hace(0),
                                 resuelto=hace(0) if concluyo else None,
                                 ronda=0 if iid in base.devueltas else 1))
        return filas

    def test_apify_caido_y_un_modelo_que_repite_no_enfrian_al_sku(self):
        # Clic 1: la tanda truena. Los dos quedan `error` con la ronda devuelta.
        with self.assertLogs(LOG, "WARNING"):
            t1 = correr([grupo()], ia=[respuesta(CANDIDATO, self.C2)], medir_truena=True)
        historia = self._historia(t1.base)
        self.assertEqual([(x["estado"], x["ronda"]) for x in historia], [("error", 0)] * 2)

        # Clic 2: esperan su reintento, y el modelo los repite.
        e = self._elegibles(historia)
        self.assertEqual(e["fuera"], {})
        g, = e["grupos"]
        self.assertTrue(g["skus"][0]["en_espera"])
        t2 = correr([g], ia=[respuesta(CANDIDATO, self.C2)])
        self.assertEqual(t2.base.reclamos, [], "no hubo respuesta: no se anota nada")
        self.assertEqual((t2.out["en_espera"], t2.out["sin_candidato"], t2.out["paginas"]),
                         (1, 0, 0))

        # Clic 3: el SKU sigue elegible, sin enfriamiento.
        e = self._elegibles(historia + self._historia(t2.base))
        self.assertEqual((len(e["grupos"]), e["fuera"]), (1, {}))

        # Pasado `REINTENTO_DIAS`, los dos vuelven a ser proponibles.
        e = self._elegibles([{**x, "creado_en": hace(CM.REINTENTO_DIAS + 1)} for x in historia])
        s = e["grupos"][0]["skus"][0]
        self.assertFalse(s["en_espera"])
        self.assertFalse({CM.llave(CANDIDATO), CM.llave(self.C2)} & set(s["intentados"]))


# ── El SQL de los intentos, ejecutado ────────────────────────────────────────

MIGRACIONES = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def _tabla_de_la_migracion() -> str:
    """El `create table` de `market_termino_intento` TAL COMO lo trae la
    migración, traducido a SQLite. Así un estado que falte en su `check` (o una
    columna que el código use y la tabla no tenga) truena aquí y no en la base."""
    ruta = sorted(MIGRACIONES.glob("*_enrich_market_rival_juicio.sql"))[-1]
    m = re.search(r"create table if not exists enrich\.market_termino_intento \(.*?\n\);",
                  ruta.read_text(encoding="utf-8"), re.S)
    t = m.group(0).replace("enrich.", "").replace(
        "bigint generated by default as identity primary key", "integer primary key autoincrement")
    t = re.sub(r"timestamptz(\s+not null)?\s+default now\(\)", r"text\1 default (datetime('now'))", t)
    return t.replace("timestamptz", "text").replace("citext", "text")


class Lite:
    """`supabase_db` sobre SQLite en memoria: EJECUTA el SQL en vez de apuntarlo.
    SQLite comparte con Postgres lo que aquí decide (`on conflict … do update …
    where … returning`); lo que es solo dialecto (`now()`, `interval`, los
    `::tipo`, el esquema) se traduce en `_t`."""

    def __init__(self) -> None:
        self.cn = sqlite3.connect(":memory:")
        self.cn.row_factory = sqlite3.Row
        self.cn.execute(_tabla_de_la_migracion())
        self.cn.execute("create table market_sku_config (sku text, canal text, termino_id integer)")

    @staticmethod
    def _t(sql: str) -> str:
        sql = re.sub(r"\benrich\.", "", sql).replace("%s", "?")
        sql = re.sub(r"::\w+", "", sql)
        sql = re.sub(r"now\(\)\s*-\s*interval\s*'(\d+) minutes'", r"datetime('now', '-\1 minutes')",
                     sql)
        return sql.replace("now()", "datetime('now')")

    def fetch_one(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        f = self.cn.execute(self._t(sql), params or ()).fetchone()
        return dict(f) if f else None

    def execute(self, sql: str, params: Any = None) -> int:
        return self.cn.execute(self._t(sql), params or ()).rowcount

    def execute_returning(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        return self.fetch_one(sql, params)

    def puesta(self) -> Any:
        return mock.patch.multiple(CM.supabase_db, fetch_one=self.fetch_one,
                                   execute=self.execute, execute_returning=self.execute_returning)

    # Ayudas para las pruebas ------------------------------------------------
    def asignar(self, sku: str, termino_id: int) -> None:
        """El término que el SKU tiene HOY (lo que lee `aceptar`)."""
        self.cn.execute("delete from market_sku_config where sku = ?", (sku,))
        self.cn.execute("insert into market_sku_config values (?, 'mercado_libre', ?)",
                        (sku, termino_id))

    def fila(self, iid: int) -> dict[str, Any]:
        return dict(self.cn.execute(
            "select *, resuelto_en > datetime('now', '-1 hour') as recien "
            "  from market_termino_intento where id = ?", (iid,)).fetchone())

    def filas(self) -> list[dict[str, Any]]:
        return [dict(f) for f in self.cn.execute("select * from market_termino_intento")]

    def envejecer(self, iid: int, *, creado_min: int = 0, resuelto_dias: int = 0) -> None:
        if creado_min:
            self.cn.execute("update market_termino_intento set creado_en = datetime('now', ?) "
                            " where id = ?", (f"-{creado_min} minutes", iid))
        if resuelto_dias:
            self.cn.execute("update market_termino_intento set resuelto_en = datetime('now', ?) "
                            " where id = ?", (f"-{resuelto_dias} days", iid))


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 35, 0),
                     "hace falta SQLite 3.35 o más (on conflict … returning)")
class SqlDeVerdad(unittest.TestCase):
    """El SQL de `_reclamar`, `_resolver`, `_anotar_desenlace` y `aceptar`,
    EJECUTADO sobre la tabla de la migración. Las demás pruebas leen su texto o
    suplantan la base; estas miran lo que de verdad queda en la fila."""

    A = {"termino": TERMINO, "termino_id": 10}
    B = {"termino": "base para camara de seguridad", "termino_id": 22}
    R = {"comparables": 1, "total": 10}

    def setUp(self) -> None:
        self.lite = Lite()
        puesta = self.lite.puesta()
        puesta.start()
        self.addCleanup(puesta.stop)

    def reclamar(self, anterior: dict[str, Any] | None = None) -> tuple[int, int] | None:
        return CM._reclamar(SKU, anterior or self.A, CANDIDATO, "nombra el producto", self.R,
                            MODELO)

    def test_un_rereclamo_lleva_el_termino_de_hoy_y_su_sugerencia_se_acepta(self):
        """El término del SKU cambió entre dos rondas del mismo candidato (una
        persona lo corrigió). La fila re-reclamada conservaba el anterior de la
        primera ronda, y `aceptar` descartaba la sugerencia buena por «vieja»:
        409, y el candidato quemado con 60 días de enfriamiento."""
        self.lite.asignar(SKU, 10)
        iid, _ = self.reclamar()
        CM._resolver(iid, "bloqueado", ronda=1)
        self.lite.asignar(SKU, 22)
        self.assertEqual(self.reclamar(self.B), (iid, 2))
        CM._resolver(iid, "medido", termino_id=77, despues={"comparables": 4, "total": 10})
        f = self.lite.fila(iid)
        self.assertEqual((f["termino_anterior"], f["termino_anterior_id"]), (self.B["termino"], 22))
        with mock.patch.object(CM.competencia_store, "actualizar_termino",
                               return_value=True) as asignar:
            r = CM.aceptar(iid, "admin")
        self.assertTrue(r["ok"], r)
        asignar.assert_called_once_with(SKU, CANDIDATO)
        self.assertEqual(self.lite.fila(iid)["estado"], "aceptado")

    def test_un_cero_se_cierra_en_su_ultima_ronda_y_ya_no_se_rereclama(self):
        iid, ronda = self.reclamar()
        self.assertEqual(CM._resolver(iid, "error", ronda=ronda), "error")
        self.assertIsNone(self.lite.fila(iid)["resuelto_en"], "reintentable")
        self.assertEqual(self.reclamar(), (iid, CM.RONDAS_MAX))
        self.assertEqual(CM._resolver(iid, "error", ronda=CM.RONDAS_MAX), "sin_mejora")
        self.assertTrue(self.lite.fila(iid)["recien"])
        self.assertIsNone(self.reclamar(), "lo que concluyó ya no se vuelve a pagar")

    def test_un_muro_se_cierra_en_su_ultima_ronda_y_ya_no_se_rereclama(self):
        iid, ronda = self.reclamar()
        CM._resolver(iid, "bloqueado", ronda=ronda)
        self.assertFalse(CM._concluyo(self.lite.fila(iid)), "en su primera ronda, reintentable")
        _, ronda = self.reclamar()
        self.assertEqual(CM._resolver(iid, "bloqueado", ronda=ronda), "bloqueado")
        f = self.lite.fila(iid)
        self.assertEqual((f["estado"], f["ronda"], f["recien"]), ("bloqueado", CM.RONDAS_MAX, 1))
        self.assertTrue(CM._concluyo(f))
        self.assertIsNone(self.reclamar())

    def test_un_fallo_de_nuestro_lado_no_gasta_ronda(self):
        """La tanda de Apify tronó en la primera ronda: el candidato no se probó.
        El reclamo ya había sumado la ronda y se quedaba sumada, así que en la
        siguiente un cero ya lo cerraba como `sin_mejora`: una sola prueba de
        verdad en vez de `RONDAS_MAX`."""
        iid, _ = self.reclamar()
        self.assertEqual(CM._resolver(iid, "error"), "error")
        self.assertEqual(self.reclamar(), (iid, 1), "es su primera ronda de verdad")
        self.assertEqual(CM._resolver(iid, "error", ronda=1), "error")
        self.assertEqual(self.reclamar(), (iid, 2))
        self.assertEqual(CM._resolver(iid, "error", ronda=2), "sin_mejora")

    def test_un_propuesto_huerfano_se_recoge_sin_gastar_ronda(self):
        iid, _ = self.reclamar()
        self.assertIsNone(self.reclamar(), "en curso: lo tiene otro proceso")
        self.lite.envejecer(iid, creado_min=CM.RECLAMO_MIN + 1)
        self.assertEqual(self.reclamar(), (iid, 1))

    def test_un_desenlace_repetido_renueva_su_fecha(self):
        """Un `termino_ok` de hace 61 días: el segundo chocaba con la llave única,
        no escribía nada y el SKU volvía a pagarse la propuesta en cada corrida.
        Y `sin_candidato` tiene que caber en el `check` de la migración."""
        self.assertTrue(CM._anotar_desenlace(SKU, self.A, "bien", self.R, MODELO, "termino_ok"))
        (f,) = self.lite.filas()
        self.lite.envejecer(f["id"], resuelto_dias=61)
        self.assertTrue(CM._anotar_desenlace(SKU, self.A, "nada nuevo", self.R, MODELO,
                                             "sin_candidato"))
        (g,) = self.lite.filas()
        self.assertEqual((g["id"], g["estado"], g["motivo"]), (f["id"], "sin_candidato", "nada nuevo"))
        self.assertTrue(self.lite.fila(g["id"])["recien"])

    def test_la_renovacion_no_pisa_un_aceptado(self):
        """El término actual fue una sugerencia ACEPTADA: su fila solo cambia de
        fecha. Su estado es el rastro de que una persona la aplicó."""
        iid, _ = CM._reclamar(SKU, self.A, TERMINO, None, self.R, MODELO, estado="termino_ok")
        self.lite.cn.execute("update market_termino_intento set estado = 'aceptado' where id = ?",
                             (iid,))
        self.lite.envejecer(iid, resuelto_dias=61)
        self.assertTrue(CM._anotar_desenlace(SKU, self.A, "bien", self.R, MODELO, "termino_ok"))
        f = self.lite.fila(iid)
        self.assertEqual((f["estado"], f["recien"]), ("aceptado", 1))

    def test_lo_abierto_no_se_renueva(self):
        """Un `propuesto` en curso de otro proceso con el término actual: ni se
        reclama ni se renueva."""
        CM._reclamar(SKU, self.A, TERMINO, None, self.R, MODELO)
        self.assertFalse(CM._anotar_desenlace(SKU, self.A, "bien", self.R, MODELO, "termino_ok"))
        (f,) = self.lite.filas()
        self.assertEqual((f["estado"], f["resuelto_en"]), ("propuesto", None))


class TituloPorPeriodo(unittest.TestCase):
    """La regla de `market_sku_titulo_v` (misma migración): el título contra el
    que se juzga —y contra el que la mejora arma su propuesta— es el del periodo
    MÁS RECIENTE del SKU, y la cuenta solo desempata dentro de ese periodo.

    Ordenaba por cuenta ANTES que por periodo: una publicación cerrada hace
    meses en la primera cuenta (o un SKU reciclado) le ganaba al título vigente
    de la otra, y los rivales se juzgaban contra otro producto. SQLite no tiene
    `lateral`: se ejecuta la subconsulta de la migración como escalar."""

    def _titulos(self, filas: list[tuple]) -> dict[str, str]:
        ruta = sorted(MIGRACIONES.glob("*_enrich_market_rival_juicio.sql"))[-1]
        m = re.search(r"create view enrich\.market_sku_titulo_v as.*?left join lateral \((.*?)\)"
                      r"\s*m on true;", ruta.read_text(encoding="utf-8"), re.S)
        interna = m.group(1).replace("enrich.", "")
        cn = sqlite3.connect(":memory:")
        cn.execute("create table market_skus_v (sku text, canal text, nombre text)")
        cn.execute("create table market_listing_metrics "
                   "(sku text, canal text, cuenta text, periodo text, title text)")
        cn.executemany("insert into market_skus_v values (?, 'mercado_libre', 'nombre del catálogo')",
                       sorted({(f[0],) for f in filas}))
        cn.executemany("insert into market_listing_metrics values (?, 'mercado_libre', ?, ?, ?)",
                       filas)
        return dict(cn.execute(
            f"select v.sku, coalesce(({interna}), v.nombre) from market_skus_v v").fetchall())

    def test_gana_el_periodo_mas_reciente_aunque_sea_de_la_segunda_cuenta(self):
        t = self._titulos([(SKU, "", "2026-05-01", "título de mayo sin cuenta"),
                           (SKU, "BEKURA", "2026-06-01", "título del producto anterior"),
                           (SKU, "SANCORFASHION", "2026-09-01", "título del producto actual")])
        self.assertEqual(t[SKU], "título del producto actual")

    def test_en_el_mismo_periodo_desempata_la_cuenta(self):
        """Fijo, para que el título no alterne entre corridas: alternar devolvería
        a la cola (y se volvería a pagar) todas las parejas del SKU."""
        t = self._titulos([(SKU, "SANCORFASHION", "2026-09-01", "título de la segunda"),
                           (SKU, "BEKURA", "2026-09-01", "título de la primera")])
        self.assertEqual(t[SKU], "título de la primera")

    def test_un_titulo_vacio_no_cuenta_y_sin_ninguno_queda_el_del_catalogo(self):
        t = self._titulos([(SKU, "BEKURA", "2026-09-01", ""),
                           (SKU, "SANCORFASHION", "2026-08-01", "título de agosto"),
                           (SKU2, "BEKURA", "2026-09-01", None)])
        self.assertEqual((t[SKU], t[SKU2]), ("título de agosto", "nombre del catálogo"))


class ScriptInformeFinal(unittest.TestCase):
    """`scripts/competencia_mejorar_terminos.informe_final`: lo que el operador
    lee al terminar el lote y el código de salida. Se carga el script por su
    ruta; importarlo no toca `config` ni la red."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util

        ruta = Path(__file__).resolve().parents[1] / "scripts" / "competencia_mejorar_terminos.py"
        spec = importlib.util.spec_from_file_location("competencia_mejorar_terminos_prueba", ruta)
        cls.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.script)

    def _informe(self, agotado: bool = False, **cuentas: Any) -> tuple[int, str]:
        out = {k: 0 for k in ("paginas", "reusados", "termino_ok", "sin_candidato", "en_espera",
                              "sugerencias", "sin_mejora", "bloqueados", "ocupados", "errores",
                              "sin_cubrir")}
        out.update(usd_ia=0.0, usd_propuestas=0.0, detenido=None, detalle=[])
        out.update(cuentas)
        with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as salida:
            codigo = self.script.informe_final(CM, out, gastado=out["usd_ia"], tope=0.5,
                                               agotado=agotado)
        return codigo, salida.getvalue()

    def test_una_propuesta_contestada_no_es_un_proveedor_caido(self):
        """Un grupo cuya propuesta falló y otro cuyo candidato se cerró sin pagar
        (un cero reciente del catálogo): el modelo SÍ contestó. Antes salía con 1
        diciendo que el proveedor no contestó ninguna. Una propuesta que solo
        repitió candidatos en espera también es una respuesta."""
        for cuenta in ("sin_mejora", "bloqueados", "ocupados", "en_espera"):
            with self.subTest(cuenta=cuenta):
                codigo, texto = self._informe(errores=1, **{cuenta: 1})
                self.assertEqual(codigo, 0)
                self.assertNotIn("no contestó ninguna", texto)
        codigo, texto = self._informe(errores=2)
        self.assertEqual(codigo, 1)
        self.assertIn("no contestó ninguna", texto)

    def test_el_gasto_separa_propuestas_y_juez(self):
        """`usd_ia` ya trae al juez: presentarlo como «propuestas» lo contaba doble."""
        _, texto = self._informe(usd_ia=0.005, usd_propuestas=0.001)
        self.assertIn("propuestas $0.0010 + juez de candidatos $0.0040", texto)

    def test_ya_no_dice_que_un_sin_mejora_no_vale(self):
        """Desde que el juicio a medias queda en `error`, un `sin_mejora` sí es una
        respuesta. El aviso viejo mandaba a desconfiar de resultados válidos."""
        for agotado, errores in ((True, 0), (False, 1)):
            with self.subTest(agotado=agotado):
                _, texto = self._informe(agotado=agotado, errores=errores, paginas=1)
                self.assertNotIn("no vale", texto)
                self.assertNotIn("comparables_despues = 0", texto)
                self.assertIn("«errores»", texto)


class MejorarRegistra(unittest.TestCase):
    """El gasto queda en `ops.process_log`, y con la acción correcta."""

    def test_registra_con_accion_terminos(self):
        """NUNCA 'raspado': los lectores del costo de Apify suman `detalle->>'usd'`
        con ese filtro y el gasto de IA los contaminaría."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO, usd=0.0012)],
                   base=Base(ids={CANDIDATO: 77}), medidos={CANDIDATO: 10},
                   filas={77: rivales(SKU, mismos=3, otros=7)})
        self.assertEqual(len(t.base.bitacora), 1)
        b = t.base.bitacora[0]
        self.assertEqual((b["accion"], b["estado"]), ("terminos", "ok"))
        d = b["detalle"]
        self.assertEqual(d["usd"], 0.0012)
        self.assertEqual(d["usd"], t.out["usd_ia"])
        self.assertEqual((d["grupos"], d["skus"], d["paginas"], d["sugerencias"]), (1, 1, 1, 1))
        self.assertEqual(d["modelo"], MODELO)

    def test_la_bitacora_no_lleva_el_detalle_por_sku(self):
        """`detalle` trae SKUs y términos de la corrida; a la bitácora va solo el
        resumen con los conteos."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], base=Base(ids={CANDIDATO: 77}),
                   medidos={CANDIDATO: 10}, filas={77: rivales(SKU, mismos=3, otros=7)})
        self.assertTrue(t.out["detalle"])
        self.assertNotIn("detalle", t.base.bitacora[0]["detalle"])

    def test_una_corrida_detenida_queda_parcial(self):
        t = correr([grupo()], ia=[respuesta(CANDIDATO)], max_paginas=0)
        self.assertEqual(t.out["detenido"], "tope de páginas de Apify")
        self.assertEqual([(b["accion"], b["estado"]) for b in t.base.bitacora],
                         [("terminos", "parcial")])

    def test_el_usd_de_la_fila_son_solo_las_propuestas(self):
        """El juez de los candidatos ya deja su propia fila 'juez' en la
        bitácora: si esta también lo sumara en `usd`, quien sume las dos filas lo
        contaría dos veces. El resultado sí trae el total (`usd_ia`), que es lo
        que el script compara contra el tope."""
        t = correr([grupo()], ia=[respuesta(CANDIDATO, usd=0.001)], base=Base(ids={CANDIDATO: 77}),
                   medidos={CANDIDATO: 10}, filas={77: rivales(SKU, mismos=3, otros=7)},
                   juez={"usd": 0.004})
        d = t.base.bitacora[0]["detalle"]
        self.assertEqual((d["usd"], d["usd_juez"]), (0.001, 0.004))
        self.assertNotIn("usd_ia", d)
        self.assertEqual((t.out["usd_propuestas"], t.out["usd_ia"]), (0.001, 0.005))

    def test_una_corrida_que_revienta_queda_en_error(self):
        """Antes quedaba 'ok': el `finally` registraba el gasto, pero nada decía
        que la corrida había muerto con intentos colgados."""
        def muerta(skus, termino_id=None):
            raise RuntimeError("conexión muerta")

        base = Base(ids={CANDIDATO: 77})
        with self.assertRaises(RuntimeError):
            correr([grupo()], ia=[respuesta(CANDIDATO)], base=base, medidos={CANDIDATO: 10},
                   filas=muerta)
        self.assertEqual([(b["accion"], b["estado"]) for b in base.bitacora],
                         [("terminos", "error")])


class MejorarUnSku(unittest.TestCase):
    """`mejorar_sku`: el botón del panel."""

    def test_si_no_es_elegible_dice_por_que_y_no_gasta(self):
        fuera = {"grupos": [], "fuera": {"en enfriamiento": 1}}
        with mock.patch.object(CM, "elegibles", return_value=fuera) as e, \
             mock.patch.object(CM, "mejorar") as m:
            r = CM.mejorar_sku(SKU, presupuesto=CJ.Presupuesto(0.05))
        self.assertEqual(r, {"ok": False, "motivo": "en enfriamiento", "sugerencias": 0})
        m.assert_not_called()
        # A mano se permite lo que el lote no: pausado, manual y medición vieja.
        e.assert_called_once_with([SKU], solo_activos=False, incluir_manuales=True,
                                  max_dias=None)

    def test_un_sku_que_no_esta_vigilado_tambien_contesta(self):
        with mock.patch.object(CM, "elegibles", return_value={"grupos": [], "fuera": {}}), \
             mock.patch.object(CM, "mejorar") as m:
            r = CM.mejorar_sku(SKU, presupuesto=CJ.Presupuesto(0.05))
        self.assertEqual((r["ok"], r["motivo"]), (False, "no es elegible"))
        m.assert_not_called()

    def test_si_es_elegible_mide_a_lo_mas_los_candidatos_de_un_grupo(self):
        """Y con plazos CORTOS para el juez: el botón comparte el turno de la IA
        con la ruta en línea, y con los del script un proveedor colgado lo
        retenía minutos."""
        g = [grupo()]
        p = CJ.Presupuesto(0.05)
        with mock.patch.object(CM, "elegibles", return_value={"grupos": g, "fuera": {}}), \
             mock.patch.object(CM, "mejorar", return_value={"sugerencias": 1}) as m:
            r = CM.mejorar_sku(SKU, presupuesto=p)
        m.assert_called_once_with(g, presupuesto=p, max_paginas=CM.MAX_POR_GRUPO,
                                  timeout=30.0, intentos=2)
        self.assertEqual(r, {"ok": True, "sugerencias": 1})

    def test_un_sku_en_blanco_no_lanza_la_mejora_sobre_todo_el_catalogo(self):
        """`elegibles` lee una lista vacía como «todos»: `mejorar_sku('')` le
        pagaba propuestas y páginas a SKUs que nadie pidió."""
        for vacio in ("", "   ", None):
            with self.subTest(sku=vacio), \
                 mock.patch.object(CM, "elegibles") as e, mock.patch.object(CM, "mejorar") as m:
                r = CM.mejorar_sku(vacio, presupuesto=CJ.Presupuesto(0.05))
                self.assertEqual(r, {"ok": False, "motivo": "falta el SKU", "sugerencias": 0})
                e.assert_not_called()
                m.assert_not_called()

    def test_el_sku_llega_sin_espacios(self):
        with mock.patch.object(CM, "elegibles", return_value={"grupos": [], "fuera": {}}) as e:
            CM.mejorar_sku(f"  {SKU} ", presupuesto=CJ.Presupuesto(0.05))
        self.assertEqual(e.call_args.args[0], [SKU])

    def test_los_plazos_del_juez_llegan_a_juzgar_skus(self):
        """Por omisión, los largos del script; el botón pasa los suyos."""
        for kw, esperado in (({}, {"timeout": 120.0, "intentos": 3}),
                             ({"timeout": 30.0, "intentos": 2}, {"timeout": 30.0, "intentos": 2})):
            with self.subTest(kw=kw):
                t = correr([grupo()], ia=[respuesta(CANDIDATO)], base=Base(ids={CANDIDATO: 77}),
                           medidos={CANDIDATO: 10}, filas={77: rivales(SKU, mismos=3, otros=7)},
                           **kw)
                self.assertEqual(t.juez_kw, [esperado])


# ── 5. La sugerencia y su aceptación ─────────────────────────────────────────

class Aceptar(unittest.TestCase):
    """`aceptar`: el único lugar donde el término de un SKU cambia, y lo hace una
    persona. Se niega —sin tocar nada— cuando la sugerencia ya no aplica."""

    def _intento(self, **cambios):
        return {"id": 7, "sku": SKU, "estado": "medido", "termino_anterior_id": 10,
                "termino_candidato": CANDIDATO, "termino_actual_id": 10, **cambios}

    def _aceptar(self, leido, asigna=True, quien="admin@prueba"):
        base = Base(intento_leido=leido)
        asignar = mock.Mock(return_value=asigna)
        with base.puesta(), \
             mock.patch.object(CM.competencia_store, "actualizar_termino", asignar):
            r = CM.aceptar(7, quien)
        return r, base, asignar

    def test_404_si_la_sugerencia_no_existe(self):
        r, base, asignar = self._aceptar(None)
        self.assertEqual((r["ok"], r["codigo"]), (False, 404))
        asignar.assert_not_called()
        self.assertEqual((base.cierres, base.hermanas), ([], []))

    def test_409_si_ya_no_esta_abierta(self):
        """Doble clic, o dos personas: la segunda no vuelve a aplicar nada."""
        for estado in ("aceptado", "descartado", "sin_mejora", "propuesto", "error"):
            with self.subTest(estado=estado):
                r, base, asignar = self._aceptar(self._intento(estado=estado))
                self.assertEqual((r["ok"], r["codigo"]), (False, 409))
                asignar.assert_not_called()
                self.assertEqual((base.cierres, base.hermanas), ([], []))

    def test_409_y_se_descarta_si_el_termino_del_sku_cambio(self):
        """El 1 se sugiere A→B; el 5 alguien corrige a mano a C; el 10 un admin
        pulsa Aceptar en la tarjeta vieja. B no debe pisar a C, y la sugerencia
        se cierra para que no vuelva a ofrecerse."""
        r, base, asignar = self._aceptar(self._intento(termino_actual_id=99))
        self.assertEqual((r["ok"], r["codigo"]), (False, 409))
        asignar.assert_not_called()
        self.assertEqual(base.cierres, [{"id": 7, "estado": "descartado",
                                         "quien": "admin@prueba", "solo_abierta": False}])
        self.assertEqual(base.hermanas, [])

    def test_si_el_sku_ya_no_tiene_termino_asignado_tampoco_se_aplica(self):
        r, _, asignar = self._aceptar(self._intento(termino_actual_id=None))
        self.assertEqual(r["codigo"], 409)
        asignar.assert_not_called()

    def test_acepta_cambia_el_termino_y_cierra_las_otras_sugerencias(self):
        r, base, asignar = self._aceptar(self._intento())
        self.assertEqual(r, {"ok": True, "sku": SKU, "termino": CANDIDATO})
        # `actualizar_termino` es la corrección MANUAL: nadie automático la pisa.
        asignar.assert_called_once_with(SKU, CANDIDATO)
        self.assertEqual(base.cierres, [{"id": 7, "estado": "aceptado",
                                         "quien": "admin@prueba", "solo_abierta": False}])
        self.assertEqual(base.hermanas, [("admin@prueba", SKU, "mercado_libre", 7)])

    def test_si_el_termino_no_se_pudo_cambiar_la_sugerencia_no_queda_aceptada(self):
        """`aceptado` solo si la escritura devolvió True: una sugerencia marcada
        como aplicada que no se aplicó sería un registro falso."""
        r, base, asignar = self._aceptar(self._intento(), asigna=False)
        self.assertEqual((r["ok"], r["codigo"]), (False, 409))
        asignar.assert_called_once()
        self.assertEqual((base.cierres, base.hermanas), ([], []))


class Descartar(unittest.TestCase):
    def _descartar(self, filas):
        base = Base(filas_cierre=filas)
        with base.puesta():
            r = CM.descartar(7, "admin@prueba")
        return r, base

    def test_descarta_solo_una_sugerencia_abierta(self):
        r, base = self._descartar(1)
        self.assertEqual(r, {"ok": True})
        self.assertEqual(base.cierres, [{"id": 7, "estado": "descartado",
                                         "quien": "admin@prueba", "solo_abierta": True}])

    def test_409_si_ya_no_estaba_abierta(self):
        """El cierre lleva `and estado = 'medido'`: descartar una sugerencia ya
        aceptada no debe borrar el rastro de que se aplicó."""
        r, base = self._descartar(0)
        self.assertEqual((r["ok"], r["codigo"]), (False, 409))
        self.assertTrue(base.cierres[0]["solo_abierta"])


class Sugerencia(unittest.TestCase):
    def test_lee_solo_el_intento_medido_del_sku(self):
        """«Sugerencia abierta» = estado `medido`. Si se leyera cualquier intento,
        el panel ofrecería aceptar un candidato que perdió."""
        fila = {"id": 7, "termino_candidato": CANDIDATO}
        leer = mock.Mock(return_value=fila)
        with mock.patch.object(CM.supabase_db, "fetch_one", leer):
            self.assertEqual(CM.sugerencia(SKU), fila)
        sql, params = leer.call_args.args
        self.assertIn("estado = 'medido'", sql)
        self.assertEqual(params, (SKU, "mercado_libre"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
