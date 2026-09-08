"""Pruebas de la diferencia entre «ML no tiene nada» y «ML no nos dejó ver».

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
El 8-sep-2026 Eduardo preguntó por qué «casco integral moto» no daba resultados.
No era que ML no tuviera: el registro de Apify decía que la petición terminaba en
`mercadolibre.com.mx/gz/account-verification` —el muro de login— nueve veces por
corrida, cinco corridas seguidas. En la MISMA corrida y con el mismo proxy,
«tenis hombre» traía 48 resultados.

Los dos finales llegaban al panel idénticos (una lista vacía) y la pantalla
enseñaba «este SKU no tiene competencia directa»: afirmaba como hecho del mercado
algo que era un problema nuestro. Y como no se guardaba nada, el término seguía
PENDIENTE y el siguiente barrido lo volvía a pagar.

Estas pruebas fijan las tres cosas que lo arreglan:

  1. el registro `#error` de Apify se lee en vez de tirarse;
  2. un término bloqueado NO se cuenta como «sin resultados»;
  3. los dos ceros se ESCRIBEN, cada uno por su puerta, para que nadie los
     vuelva a pagar y la pantalla pueda decir cuál fue.

No se llama a Apify ni a la base: el actor y el store van suplantados.

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_captura as CC  # noqa: E402
from services import competencia_scraper as CS  # noqa: E402

URL = "https://listado.mercadolibre.com.mx"
# Los fixtures piden la URL EXACTA que construye el código: si el sufijo se
# quitara de un lado y no del otro, la atribución por URL dejaría de cruzar y
# todos los resultados se perderían en silencio. Por eso se importa, no se copia.
SUF = CS._SUFIJO_BUSQUEDA


def pagina(termino: str, n: int = 2) -> dict:
    """Página que salió bien: la `pageFunction` devolvió `{url, items}`."""
    slug = termino.replace(" ", "-")
    return {"url": f"{URL}/{slug}{SUF}",
            "items": [{"posicion": i + 1,
                       "url": f"https://articulo.mercadolibre.com.mx/MLM-19999999{i}-x-_JM",
                       "titulo": f"{termino} {i}"} for i in range(n)]}


def muro(termino: str, reintentos: int = 8) -> dict:
    """EL REGISTRO REAL de Apify cuando ML manda a verificarse.

    Copiado de la corrida `1G1bYhC2HKVa1lYPV` del 8-sep-2026. Lo importante es
    que NO trae `url` en la raíz —por eso el código viejo lo leía como una página
    sin término— y que la URL pedida sólo vive en `#debug`.
    """
    slug = termino.replace(" ", "-") + SUF
    return {
        "#error": True,
        "#debug": {
            "url": f"{URL}/{slug}",
            "loadedUrl": ("https://www.mercadolibre.com.mx/gz/account-verification"
                          f"?go=https%3A%2F%2Flistado.mercadolibre.com.mx%2F{slug}"),
            "retryCount": reintentos,
            "errorMessages": ["Error: BLOQUEADO"] * (reintentos + 1),
        },
    }


class LeerElRegistroDeError(unittest.TestCase):
    """`_bloqueado_en`: la única prueba de que ML nos bloqueó."""

    def test_devuelve_la_url_pedida_no_la_del_muro(self):
        """La cargada es `/gz/account-verification?go=…` y no se puede cruzar
        con ningún término. La pedida sí."""
        self.assertEqual(CS._bloqueado_en(muro("casco integral moto")),
                         f"{URL}/casco-integral-moto{SUF}")

    def test_una_pagina_buena_no_es_bloqueo(self):
        self.assertIsNone(CS._bloqueado_en(pagina("tenis hombre")))

    def test_sin_debug_no_revienta(self):
        self.assertIsNone(CS._bloqueado_en({"#error": True}))
        self.assertIsNone(CS._bloqueado_en({"#error": True, "#debug": {}}))

    def test_ignora_la_diagonal_final(self):
        """`de_url` se indexa con las URLs sin diagonal; si esto no la quita, el
        cruce falla y el bloqueo se pierde en silencio."""
        m = muro("x")
        m["#debug"]["url"] = f"{URL}/x/"
        self.assertEqual(CS._bloqueado_en(m), f"{URL}/x")


class LaUrlLlevaElSufijo(unittest.TestCase):
    """El sufijo `_NoIndex_True` es lo que evita el muro. Sin él, «casco integral
    moto» murió 8 de 8 veces; con él pasó a la primera, junto con los otros 8
    términos que alguna vez se muraron. Si alguien lo quita —parece decoración—
    los muros vuelven, y vuelven en silencio."""

    def _urls_pedidas(self, terminos):
        vistas = {}

        async def falso(payload, limite_lectura, util):
            vistas["urls"] = [u["url"] for u in payload["startUrls"]]
            return []

        with mock.patch.object(CS, "_con_respaldo", falso):
            asyncio.run(CS.buscar_terminos(terminos))
        return vistas["urls"]

    def test_la_url_lleva_noindex(self):
        u = self._urls_pedidas(["casco integral moto"])[0]
        self.assertEqual(u, f"{URL}/casco-integral-moto_NoIndex_True")

    def test_el_termino_se_recupera_aunque_falte_la_atribucion(self):
        """El respaldo reconstruye el término desde el slug; si no le quita el
        sufijo, guarda «casco integral moto NoIndex True» como si fuera otro
        término y la fila queda huérfana."""
        pag = {"url": f"{URL}/casco-integral-moto_NoIndex_True",
               "items": [{"posicion": 1,
                          "url": "https://articulo.mercadolibre.com.mx/MLM-199999999-x-_JM",
                          "titulo": "un casco"}]}

        async def falso(payload, limite_lectura, util):
            # Se devuelve una URL que NO está en `de_url` para forzar el respaldo.
            return [{**pag, "url": pag["url"].replace("listado", "listado2")}]

        with mock.patch.object(CS, "_con_respaldo", falso):
            out = asyncio.run(CS.buscar_terminos(["casco integral moto"]))
        self.assertEqual(list(out), ["casco integral moto"])


class BuscarTerminosSeparaLosDosCeros(unittest.TestCase):
    """`buscar_terminos` con el actor suplantado."""

    def _correr(self, paginas, terminos):
        bloqueados: set[str] = set()

        async def falso(payload, limite_lectura, util):
            return paginas

        with mock.patch.object(CS, "_con_respaldo", falso):
            out = asyncio.run(CS.buscar_terminos(terminos, bloqueados=bloqueados))
        return out, bloqueados

    def test_el_caso_real_una_buena_y_una_murada(self):
        """La corrida que lo destapó: los dos términos, el mismo proxy, el mismo
        minuto. Uno pasa y el otro no."""
        out, bloq = self._correr(
            [pagina("tenis hombre", n=3), muro("casco integral moto")],
            ["tenis hombre", "casco integral moto"])
        self.assertEqual(len(out["tenis hombre"]), 3)
        self.assertNotIn("casco integral moto", out, "un muro no son resultados")
        self.assertEqual(bloq, {"casco integral moto"})

    def test_sin_el_buzon_no_truena(self):
        """`bloqueados` es opcional: el script de la cola no lo pasa."""
        async def falso(payload, limite_lectura, util):
            return [muro("casco integral moto")]

        with mock.patch.object(CS, "_con_respaldo", falso):
            out = asyncio.run(CS.buscar_terminos(["casco integral moto"]))
        self.assertEqual(out, {})

    def test_un_termino_vacio_de_verdad_no_entra_como_bloqueado(self):
        """Página que cargó bien y no traía tarjetas. Es un cero del MERCADO."""
        out, bloq = self._correr([pagina("cosa rarisima", n=0)], ["cosa rarisima"])
        self.assertEqual(out, {})
        self.assertEqual(bloq, set(), "no se le puede echar la culpa a ML")


class MedirBusquedasEscribeSiempre(unittest.TestCase):
    """El tramo que corta el gasto: los dos ceros se anotan, cada uno por su
    puerta. Antes ninguno se anotaba y el término volvía a la cola."""

    def _correr(self, res_scraper, bloqueados_del_scraper):
        async def falso_buscar(terminos, limite=10, bloqueados=None):
            if bloqueados is not None:
                bloqueados |= bloqueados_del_scraper
            return res_scraper

        buzon: set[str] = set()
        with mock.patch.object(CC.competencia_scraper, "buscar_terminos", falso_buscar), \
             mock.patch.object(CC, "_nuestras_publicaciones", return_value={}), \
             mock.patch.object(CC.competencia_store, "periodo_actual",
                               return_value="2026-09"), \
             mock.patch.object(CC.competencia_store, "reemplazar_busqueda",
                               return_value=0) as guardar, \
             mock.patch.object(CC.competencia_store,
                               "marcar_busqueda_bloqueada") as marcar:
            out = asyncio.run(CC.medir_busquedas(["casco integral moto"],
                                                 bloqueados=buzon))
        return out, guardar, marcar, buzon

    def test_bloqueado_se_marca_bloqueado_y_no_como_vacio(self):
        out, guardar, marcar, buzon = self._correr({}, {"casco integral moto"})
        marcar.assert_called_once_with("casco integral moto")
        guardar.assert_not_called()
        self.assertEqual(out, {"casco integral moto": 0})
        self.assertEqual(buzon, {"casco integral moto"},
                         "el botón necesita el buzón para cambiar el mensaje")

    def test_vacio_de_verdad_se_guarda_como_vacio(self):
        _, guardar, marcar, _ = self._correr({}, set())
        guardar.assert_called_once_with("casco integral moto", "2026-09", [])
        marcar.assert_not_called()

    def test_que_falle_la_anotacion_no_tumba_la_tanda(self):
        """Los otros términos del lote ya se rasparon y hay que guardarlos: un
        error al ANOTAR el vacío no puede llevárselos."""
        async def falso_buscar(terminos, limite=10, bloqueados=None):
            return {}

        with mock.patch.object(CC.competencia_scraper, "buscar_terminos", falso_buscar), \
             mock.patch.object(CC, "_nuestras_publicaciones", return_value={}), \
             mock.patch.object(CC.competencia_store, "periodo_actual",
                               return_value="2026-09"), \
             mock.patch.object(CC.competencia_store, "reemplazar_busqueda",
                               side_effect=RuntimeError("base caída")):
            out = asyncio.run(CC.medir_busquedas(["a", "b"]))
        self.assertEqual(out, {"a": 0, "b": 0})


class ElStoreTieneLaPuerta(unittest.TestCase):
    def test_marcar_busqueda_bloqueada_existe_en_la_fachada(self):
        """La regresión de `sembrar_skus` otra vez: el store cambió de motor y
        una función que «existía» dejó de existir sin que nadie lo notara."""
        self.assertTrue(hasattr(CC.competencia_store, "marcar_busqueda_bloqueada"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
