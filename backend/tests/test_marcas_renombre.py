"""Pruebas de las MARCAS DE RENOMBRE fuera de las medianas (v0.639.0).

── POR QUÉ ─────────────────────────────────────────────────────────────────────
Eduardo, 12-oct-2026: en Tenis (MLM6585) los genéricos del top cobran $190–$300 y
los de marca $619–$1,420; la mediana salía en $398 cuando lo razonable para
nuestros tenis genéricos ronda $200. Vendemos genéricos: una marca de renombre
no entra a ninguna mediana, promedio ni referencia de precio. Se sigue
mostrando, marcada.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. `detectar`: título, vendedor, tienda oficial «MARCA por X», solo_vendedor,
   negación («para», «compatible», «tipo»…) y la salvedad «para hombre».
   Ajustes de la revisión: «3m» es medida, «galaxy» suelto no es Samsung, un
   accesorio sin «para» no es la marca del dispositivo, «genérico» niega en
   cualquier posición, el vendedor «Por Nike» de la tarjeta, perfumes
   «inspirado en», y lo nuestro nunca es marca.
2. La lista es reemplazable: el código no depende de su contenido.
3. `es_activo`: COMPETENCIA_SIN_MARCAS, encendido por omisión.
4. Los cuatro lugares que calculan un precio de mercado:
   · competencia_store.vista — mediana/mín/máx de la subcategoría sin marcas,
     `mediana_con_marcas`, `n_marcas_excluidas`, `marca_renombre` por fila.
   · routers/competencia.detalle_sku — `marca_renombre` en busqueda_general.
   · radar_precios.referencia_de y contexto_categoria_de — sin marcas, `n_marcas`.
   · publicaciones_panel.agregar_mercado / _adjuntar_mercado — sin marcas, «al
     menos 3» contado sin ellas.
   Y que con COMPETENCIA_SIN_MARCAS=false todo sale como antes.

Sin red ni base: nada aquí habla con kubera ni con Mercado Libre.

    cd backend && python -m unittest tests.test_marcas_renombre -v
    cd backend && python -m pytest tests/test_marcas_renombre.py -q   # si hay pytest
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_store as CS  # noqa: E402
from services import marcas_renombre as MR  # noqa: E402
from services import publicaciones_panel as PP  # noqa: E402
from services import radar_precios as rp  # noqa: E402

AHORA = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
_ENV = "COMPETENCIA_SIN_MARCAS"


def _apagado():
    return mock.patch.dict(os.environ, {_ENV: "false"})


def _encendido():
    return mock.patch.dict(os.environ, {_ENV: "true"})


# El top de Tenis del ejemplo: 8 genéricos de $190–$300 y 5 de marca de
# $619–$1,420 (dos delatados por el VENDEDOR, tres por el título).
GENERICOS = [
    ("Tenis Deportivos Hombre Con Cámara De Aire", "ANTHICHEN", 190),
    ("Tenis Casuales Para Mujer Plataforma Blanca", "ZAPATERIA LUNA", 210),
    ("Tenis Urbanos Unisex Suela Antiderrapante", "MODA FENIX", 229),
    ("Zapatillas Ligeras Para Caminar Transpirables", "TIENDA SOL", 249),
    ("Tenis Escolares Negros Para Niño", "CALZADO RIO", 259),
    ("Tenis De Lona Clásicos Bajos", "OUTLET MX", 279),
    ("Tenis Running Malla Hombre Ligero", "IMPORTADORA ZEN", 299),
    ("Tenis Botín Hombre Casual Piel Sintética", "LOOK FASHION", 300),
]
MARCAS = [
    ("Tenis Vans Classics Old Skool Unisex", "VENDEDOR UNO", 619, "Vans"),
    ("Tenis Smash V2 Blanco", "PUMA", 749, "Puma"),
    ("Tenis Grand Court Hombre", "ADIDAS por Look Sport", 899, "Adidas"),
    ("Tenis Converse Chuck Taylor All Star", "VENDEDOR DOS", 1099, "Converse"),
    ("Tenis 574 Clásico", "NEW BALANCE", 1420, "New Balance"),
]


class Detectar(unittest.TestCase):
    def test_titulo(self):
        self.assertEqual(MR.detectar("Tenis Vans Classics Old Skool Unisex", "X"), "Vans")

    def test_tienda_oficial_por(self):
        self.assertEqual(MR.detectar("Tenis Grand Court", "ADIDAS por Look Sport"), "Adidas")
        self.assertEqual(MR.detectar("Tenis Club C", "REEBOK por Look Trendy"), "Reebok")

    def test_vendedor_es_la_marca(self):
        self.assertEqual(MR.detectar("Tenis 574", "NEW BALANCE"), "New Balance")

    def test_generico_no_es_marca(self):
        self.assertIsNone(MR.detectar("Tenis Deportivos Hombre Con Cámara De Aire",
                                      "ANTHICHEN"))
        for titulo, seller, _ in GENERICOS:
            self.assertIsNone(MR.detectar(titulo, seller), titulo)

    def test_negacion(self):
        self.assertIsNone(MR.detectar("Funda para iPhone 15 Pro", ""))
        self.assertIsNone(MR.detectar("Correa compatible con Apple Watch Serie 8", ""))
        self.assertIsNone(MR.detectar("Tenis tipo Nike Air", ""))
        self.assertIsNone(MR.detectar("Agujetas para tenis Nike", ""))
        # La negación mira ANTES de la marca, no después.
        self.assertEqual(MR.detectar("Tenis Nike Revolution estilo casual", ""), "Nike")

    def test_para_hombre_no_niega(self):
        # La forma más común de titular un tenis de marca en ML.
        self.assertEqual(MR.detectar("Tenis Para Hombre Nike Revolution 6", ""), "Nike")
        self.assertEqual(MR.detectar("Tenis Para Mujer Puma Carina", ""), "Puma")

    def test_solo_vendedor(self):
        self.assertEqual(MR.detectar("Tenis Cloud 5", "On"), "On")
        self.assertIsNone(MR.detectar("Pon on la mesa", ""))
        self.assertIsNone(MR.detectar("Tenis Fila Disruptor", ""))  # «fila» es solo vendedor
        self.assertEqual(MR.detectar("Tenis Disruptor", "FILA"), "Fila")

    def test_alias_de_varias_palabras_y_palabra_completa(self):
        self.assertEqual(MR.detectar("Tenis Air Force 1 Blancos", ""), "Nike")
        self.assertEqual(MR.detectar("Tenis New Balance 530", ""), "New Balance")
        # «vans» dentro de otra palabra no cuenta.
        self.assertIsNone(MR.detectar("Caravanserai decoración", ""))

    def test_normalizar(self):
        self.assertEqual(MR.normalizar("  Cámara  DE   Aire!! "), "camara de aire")
        self.assertEqual(MR.normalizar("Levi's"), "levi s")
        self.assertEqual(MR.normalizar(None), "")

    def test_vacios(self):
        self.assertIsNone(MR.detectar(None, None))
        self.assertIsNone(MR.detectar("", ""))

    # ── Ajustes de la revisión (v0.639.0) ──────────────────────────────────────

    def test_3m_es_medida_no_marca(self):
        # «3m» = 3 metros en miles de cables, extensiones y mangueras genéricas.
        for t in ("Cable Hdmi 3m", "Cable Hdmi 4k Ultra Hd 3m Alta Velocidad",
                  "Extension Electrica 3m Uso Rudo", "Manguera 3m Para Jardin"):
            self.assertIsNone(MR.detectar(t, ""), t)
        # La marca sigue: por su vendedor o por alias inequívocos.
        self.assertEqual(MR.detectar("Cinta Doble Cara", "3M"), "3M")
        self.assertEqual(MR.detectar("Ganchos Adhesivos 3m Command", ""), "3M")
        self.assertEqual(MR.detectar("Cinta Scotch Transparente", ""), "3M")

    def test_galaxy_suelto_no_es_samsung(self):
        # El proyector «Galaxy» de astronauta es un genérico muy vendido.
        self.assertIsNone(MR.detectar("Proyector Galaxy Astronauta Luz Led Estrellas", ""))
        self.assertIsNone(MR.detectar("Lampara Proyector Galaxy Nebulosa", ""))
        self.assertEqual(MR.detectar("Celular Samsung Galaxy A54 128gb", ""), "Samsung")
        self.assertEqual(MR.detectar("Audifonos Galaxy Buds 2 Pro", ""), "Samsung")

    def test_lista_de_compatibilidad_no_es_marca(self):
        # Títulos reales de los más vendidos de ML (revisión de la lista, 12-oct):
        # la marca aparece lejos de «compatible», o con «sirve» / «universal».
        self.assertIsNone(MR.detectar(
            "Cable TIPO C a USB-A 60W PD 3A 2M 1HORA Carga Rapida Compatible Samsung S24 S23 Redmi", "1HORA"))
        self.assertIsNone(MR.detectar("Pastilla De Deshielo Bimetal Refrigerador Sirve Mabe Acros", ""))
        self.assertIsNone(MR.detectar("Control Remoto Universal Aire Acondicionado Panasonic Midea", ""))
        self.assertIsNone(MR.detectar("Kit 4 Varillas Suspension Whirlpool Amortiguadores Lavadora", ""))
        self.assertIsNone(MR.detectar("Compatible Con Todos Los Oral B 8 Cabezales Repuesto Cepillo", ""))
        # La marca sigue saliendo cuando el producto ES de la marca.
        self.assertEqual(MR.detectar("Cepillo Dental Eléctrico Oral B Pro 2000", ""), "Oral-B")
        self.assertEqual(MR.detectar("Smart Tv Samsung 55 Pulgadas Crystal Uhd", ""), "Samsung")

    def test_auditoria_sandbox(self):
        # Falsos positivos reales de las 1,048 detecciones del ranking del sandbox (12-oct).
        self.assertIsNone(MR.detectar(
            "45 W Cargador tipo C Carga Rapida con 2 m cable USB C compatibilidad con Samsung S26 Ultra", ""))
        self.assertIsNone(MR.detectar("65w Cargador Usb Tipo C Para Lenovo,hp,dell,huawei 20v 3.25a", ""))
        self.assertIsNone(MR.detectar("Filtro Agua Refrigerador Samsung Da29-00020", ""))
        self.assertIsNone(MR.detectar("Vaso Para Licuadora T-fal Infinity Force Vidrio T Fal", ""))
        # Personajes con licencia en mercancía de terceros: solo cuenta la tienda oficial.
        self.assertIsNone(MR.detectar("Peluche Pikachu 30 cm Suave", ""))
        self.assertIsNone(MR.detectar("Lampara Disney Stitch Led", ""))
        self.assertEqual(MR.detectar("Muñeca Stitch", "DISNEY por Mattel"), "Disney")
        # «para» seguido solo de uso y público no niega: es como se titulan los tenis de marca.
        self.assertEqual(MR.detectar("Tenis Para Correr Hombre Nike Revolution 7", ""), "Nike")

    def test_accesorio_sin_para_no_es_marca(self):
        # El sustantivo del producto es un accesorio: el dispositivo que nombra
        # no es la marca de lo que se vende.
        for t in ("Funda Uso Rudo iPhone 15 Pro Max Transparente",
                  "Mica Cristal Templado Samsung Galaxy A54",
                  "Cable Usb C A Lightning iPhone Carga Rapida 2m",
                  "Correa Mi Band 8 Silicon",
                  "2 Micas Hidrogel iPhone 14",
                  "Kit De Fundas Samsung A14"):
            self.assertIsNone(MR.detectar(t, ""), t)
        # La tienda oficial de la marca sí cuenta aunque venda un accesorio.
        self.assertEqual(MR.detectar("Cargador Apple 20w Usb C", "Por Apple"), "Apple")
        # Y el accesorio no tapa a la marca cuando el producto es otro.
        self.assertEqual(MR.detectar("Reloj Casio Correa Piel", ""), "Casio")

    def test_se_declara_generico_en_cualquier_posicion(self):
        self.assertIsNone(MR.detectar("Control Inalambrico Ps4 Playstation Generico", ""))
        self.assertIsNone(MR.detectar("Tenis Nike Air Max Replica", ""))
        self.assertEqual(MR.detectar("Tenis Nike Air Max 90", ""), "Nike")

    def test_vendedor_con_el_formato_de_la_tarjeta(self):
        # Lo que guarda el scraper web desde `.poly-component__seller`.
        self.assertEqual(MR.detectar("Tenis Deportivo", "Por Nike"), "Nike")
        self.assertEqual(MR.detectar("Tenis Disruptor", "Por FILA"), "Fila")
        self.assertEqual(MR.detectar("Tenis", "Vendido por FILA"), "Fila")
        self.assertEqual(MR.detectar("Tenis", "Tienda oficial Adidas"), "Adidas")
        self.assertEqual(MR.detectar("Tenis Cloud 5", "Por On"), "On")
        self.assertEqual(MR.detectar("Tenis Grand Court", "Por ADIDAS por Look Sport"), "Adidas")
        self.assertIsNone(MR.detectar("Tenis Urbanos", "Por Tienda Sol"))
        self.assertIsNone(MR.detectar("Tenis Urbanos", "Vendido por MODA FENIX"))

    def test_perfume_inspirado_no_es_la_marca(self):
        self.assertIsNone(MR.detectar("Perfume Inspirado En Carolina Herrera 212 Vip", ""))
        self.assertIsNone(MR.detectar("Perfume Equivalente Chanel No 5", ""))
        self.assertEqual(MR.detectar("Perfume Carolina Herrera 212 Vip Men", ""),
                         "Carolina Herrera")

    def test_etiquetar_lo_nuestro_nunca_es_marca(self):
        filas = [{"titulo": "Tenis Vans Old Skool", "seller": "", "sku_nuestro": "TEN-1"},
                 {"titulo": "Tenis Vans Old Skool", "seller": "", "es_nuestro": True},
                 {"titulo": "Tenis Vans Old Skool", "seller": ""}]
        MR.etiquetar(filas)
        self.assertEqual([f["marca_renombre"] for f in filas], [None, None, "Vans"])


class ListaReemplazable(unittest.TestCase):
    """El JSON se va a reemplazar por uno más completo con el mismo formato."""

    def tearDown(self):
        MR.recargar()

    def test_otra_lista_mismo_formato(self):
        datos = {"marcas": [{"marca": "Acme Pro", "alias": ["acme pro max ultra"],
                             "solo_vendedor": ["acme"]}]}
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "marcas.json"
            ruta.write_text(json.dumps(datos), encoding="utf-8")
            with mock.patch.object(MR, "_ARCHIVO", ruta):
                MR.recargar()
                self.assertEqual(MR.detectar("Tenis Acme Pro Max Ultra 2", ""), "Acme Pro")
                self.assertEqual(MR.detectar("Tenis X", "ACME por Tienda"), "Acme Pro")
                self.assertEqual(MR.detectar("Tenis X", "Acme Pro"), "Acme Pro")
                self.assertIsNone(MR.detectar("Tenis acme", ""))      # solo vendedor
                self.assertIsNone(MR.detectar("Tenis Vans", ""))      # ya no está

    def test_sin_archivo_no_revienta(self):
        with mock.patch.object(MR, "_ARCHIVO", Path("no_existe_marcas.json")):
            MR.recargar()
            self.assertIsNone(MR.detectar("Tenis Vans", "NIKE"))


class EsActivo(unittest.TestCase):
    def test_por_omision_encendido(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV, None)
            self.assertTrue(MR.es_activo())

    def test_valores_que_apagan(self):
        for v in ("false", "0", "no", " FALSE "):
            with mock.patch.dict(os.environ, {_ENV: v}):
                self.assertFalse(MR.es_activo(), v)
        for v in ("true", "1", "si"):
            with mock.patch.dict(os.environ, {_ENV: v}):
                self.assertTrue(MR.es_activo(), v)


# ── competencia_store.vista ────────────────────────────────────────────────────

def _top_tenis():
    filas = []
    todos = [(t, s, p, None) for t, s, p in GENERICOS] + MARCAS
    for i, (titulo, seller, precio, _) in enumerate(todos, 1):
        filas.append({"posicion": i, "titulo": titulo, "seller": seller,
                      "precio": float(precio), "vendidos": 100, "visitas_30d": 1000,
                      "capturado_en": AHORA, "item_categoria_id": "MLM6585",
                      "id_pagina": f"MLM{i}", "sku_nuestro": None})
    return filas


def _vista():
    fila_sku = {"sku": "TEN-0001-NEG", "raiz_id": "MLM1276", "raiz_nombre": "Deportes",
                "categoria_id": "MLM6585", "categoria_nombre": "Tenis",
                "tiendas": [{"canal": "mercado_libre", "precio": 249.0,
                             "estado": "active", "visitas_30d": 10}],
                "unidades_30d": 3}
    rk = {("MLM6585", "hoja"): _top_tenis(), ("MLM1276", "raiz"): _top_tenis()[-3:]}
    with mock.patch.object(CS, "tabla", return_value=[fila_sku]), \
            mock.patch.object(CS, "rankings_por_categoria", return_value=rk), \
            mock.patch.object(CS, "conteo_terminos", return_value={}), \
            mock.patch.object(CS, "stock_por_sku", return_value={}), \
            mock.patch.object(CS, "movimiento_del_top", return_value={}):
        arbol = CS.vista()
    raiz = arbol[0]
    return raiz, raiz["subcategorias"][0], raiz["skus"][0]


class Vista(unittest.TestCase):
    def test_mediana_sin_marcas(self):
        with _encendido():
            raiz, sub, sku = _vista()
        # Genéricos 190…300 → (249 + 259) / 2. Con las marcas era 299.
        self.assertEqual(sub["mediana"], 254.0)
        self.assertEqual(sub["mediana_con_marcas"], 299.0)
        self.assertEqual(sub["n_marcas_excluidas"], 5)
        self.assertEqual((sub["precio_min"], sub["precio_max"]), (190.0, 300.0))
        # La brecha del SKU usa la mediana nueva.
        self.assertEqual(sku["mediana_mercado"], 254.0)
        self.assertEqual(sku["brecha"], round(249 / 254, 2))
        # Cada fila del top dice si es marca; se siguen mostrando todas.
        self.assertEqual(sub["n_ranking"], 13)
        marcas = {x["titulo"]: x["marca_renombre"] for x in sub["top"]}
        for titulo, _, _, marca in MARCAS:
            self.assertEqual(marcas[titulo], marca)
        for titulo, _, _ in GENERICOS:
            self.assertIsNone(marcas[titulo])
        # El top de la raíz también va etiquetado (no tiene mediana).
        self.assertEqual([x["marca_renombre"] for x in raiz["top"]],
                         ["Adidas", "Converse", "New Balance"])

    def test_apagado_sale_como_antes(self):
        with _apagado():
            raiz, sub, sku = _vista()
        self.assertEqual(sub["mediana"], 299.0)
        self.assertEqual(sub["mediana_con_marcas"], 299.0)
        self.assertEqual(sub["n_marcas_excluidas"], 0)
        self.assertEqual((sub["precio_min"], sub["precio_max"]), (190.0, 1420.0))
        self.assertEqual(sku["brecha"], round(249 / 299, 2))
        # La marca se sigue detectando para la pantalla.
        self.assertEqual(sum(1 for x in sub["top"] if x["marca_renombre"]), 5)


# ── routers/competencia.detalle_sku ────────────────────────────────────────────

class DetalleSku(unittest.TestCase):
    def test_busqueda_general_lleva_marca(self):
        from routers import competencia as RC
        general = [{"externo_id": "A", "titulo": "Tenis Vans Old Skool", "seller": "X",
                    "precio": 900.0},
                   {"externo_id": "B", "titulo": "Tenis Urbanos Unisex", "seller": "Y",
                    "precio": 220.0}]
        top = [{"posicion": 1, "titulo": "Tenis 574", "seller": "NEW BALANCE",
                "precio": 1400.0}]
        st = RC.competencia_store
        with mock.patch.object(st, "listar_skus", return_value=[
                {"sku": "TEN-1", "categoria_id": "MLM6585", "termino_general": "tenis"}]), \
                mock.patch.object(st, "publicaciones", return_value=[]), \
                mock.patch.object(st, "terminos_categoria", return_value=[]), \
                mock.patch.object(st, "total_terminos", return_value=0), \
                mock.patch.object(st, "ranking_categoria", return_value=top), \
                mock.patch.object(st, "busqueda", return_value=general), \
                mock.patch.object(st, "estado_termino", return_value=None), \
                mock.patch.object(RC.settings, "competencia_juez_visible", False):
            r = RC.detalle_sku("TEN-1")
        self.assertEqual([x["marca_renombre"] for x in r["busqueda_general"]], ["Vans", None])
        self.assertEqual(r["top"][0]["marca_renombre"], "New Balance")


# ── radar_precios ──────────────────────────────────────────────────────────────

def _res(filas, dias=1):
    return [{"precio": p, "titulo": t, "seller": s, "es_nuestro": False,
             "capturado_en": AHORA - timedelta(days=dias)} for t, s, p in filas]


_GEN4 = [("Tenis Urbanos Unisex", "A", 190), ("Tenis Lona Bajos", "B", 200),
         ("Tenis Escolares Negros", "C", 210), ("Tenis Casual Hombre", "D", 220)]
_MAR2 = [("Tenis Vans Old Skool", "E", 900), ("Tenis Revolution", "NIKE", 1100)]


class Referencia(unittest.TestCase):
    def test_excluye_marcas(self):
        r = rp.referencia_de(_res(_GEN4 + _MAR2), termino="tenis", ahora=AHORA,
                             sin_marcas=True)
        self.assertEqual((r["precio"], r["n"], r["n_total"], r["n_marcas"]),
                         (205.0, 4, 4, 2))
        self.assertIsNone(r["motivo"])

    def test_con_marcas_la_busqueda_salia_dispersa(self):
        # Antes: los dos de marca hacían que la búsqueda pareciera de productos
        # distintos (q3/q1 > 3) y el SKU se quedaba sin referencia.
        r = rp.referencia_de(_res(_GEN4 + _MAR2), termino="tenis", ahora=AHORA,
                             sin_marcas=False)
        self.assertEqual((r["motivo"], r["n_marcas"]), ("rivales_dispersos", 0))

    def test_pocos_sin_marcas(self):
        r = rp.referencia_de(_res(_GEN4[:2] + _MAR2 + [("Tenis Puma Smash", "F", 800)]),
                             termino="tenis", ahora=AHORA, sin_marcas=True)
        self.assertIsNone(r["precio"])
        self.assertEqual((r["motivo"], r["n_marcas"]), ("pocos_rivales", 3))

    def test_apagado_igual_que_antes(self):
        filas = _res(_GEN4 + [("Tenis Vans Old Skool", "E", 300)])
        viejas = [{k: v for k, v in f.items() if k not in ("titulo", "seller")} for f in filas]
        with _apagado():
            r = rp.referencia_de(filas, termino="tenis", ahora=AHORA)
        self.assertEqual(r, rp.referencia_de(viejas, termino="tenis", ahora=AHORA))
        self.assertEqual((r["precio"], r["n"], r["n_marcas"]), (210.0, 5, 0))

    def test_siempre_trae_n_marcas(self):
        self.assertEqual(rp.referencia_de([], termino=None, ahora=AHORA)["n_marcas"], 0)

    def test_sql_ref_trae_titulo_y_vendedor(self):
        self.assertIn("r.titulo", rp._SQL_REF)
        self.assertIn("r.seller", rp._SQL_REF)


class ContextoCategoria(unittest.TestCase):
    def _filas(self):
        u = AHORA - timedelta(hours=2)
        return [{"precio": p, "titulo": t, "seller": s, "capturado_en": u, "ultima": u}
                for t, s, p in GENERICOS + [(t, s, p) for t, s, p, _ in MARCAS]]

    def test_sin_marcas(self):
        c = rp.contexto_categoria_de(self._filas(), sin_marcas=True)
        self.assertEqual((c["mediana"], c["n"], c["n_marcas"]), (254.0, 8, 5))
        self.assertEqual(c["capturado_en"], (AHORA - timedelta(hours=2)).isoformat())

    def test_apagado_como_antes(self):
        with _apagado():
            c = rp.contexto_categoria_de(self._filas())
        self.assertEqual((c["mediana"], c["n"], c["n_marcas"]), (299.0, 13, 0))

    def test_sin_filas_es_none_y_todas_marca_da_mediana_none(self):
        self.assertIsNone(rp.contexto_categoria_de([], sin_marcas=True))
        solo = [{"precio": 900, "titulo": "Tenis Vans", "seller": "X", "ultima": AHORA}]
        c = rp.contexto_categoria_de(solo, sin_marcas=True)
        self.assertEqual((c["mediana"], c["n"], c["n_marcas"]), (None, 0, 1))

    def test_sql_categoria_trae_filas_con_la_misma_ventana(self):
        sql = rp._SQL_CATEGORIA
        self.assertNotIn("percentile_cont", sql)
        for pedazo in ("titulo", "seller", "ultima - interval '1 day'",
                       "not coalesce(es_nuestro, false)", "precio > 0"):
            self.assertIn(pedazo, sql)


# ── publicaciones_panel ────────────────────────────────────────────────────────

def _filas_panel():
    u = AHORA - timedelta(days=2)
    filas = [{"sku": "A", "termino": "tenis", "precio": p, "titulo": t, "seller": s,
              "capturado_en": u} for t, s, p in _GEN4 + _MAR2]
    filas += [{"sku": "B", "termino": "tenis blancos", "precio": p, "titulo": t,
               "seller": s, "capturado_en": u}
              for t, s, p in _GEN4[:2] + _MAR2 + [("Tenis Puma Smash", "F", 800)]]
    return filas


class Panel(unittest.TestCase):
    def test_agregar_sin_marcas(self):
        m = PP.agregar_mercado(_filas_panel(), sin_marcas=True)
        self.assertEqual(set(m), {"A"})          # B: 2 sin marca < 3 → sin referencia
        a = m["A"]
        self.assertEqual((a["promedio"], a["mediana"], a["n"], a["n_marcas"]),
                         (205.0, 205.0, 4, 2))
        self.assertEqual(a["termino"], "tenis")
        self.assertEqual(a["ultima"], AHORA - timedelta(days=2))

    def test_agregar_apagado_como_antes(self):
        with _apagado():
            m = PP.agregar_mercado(_filas_panel())
        self.assertEqual(set(m), {"A", "B"})
        self.assertEqual((m["A"]["n"], m["A"]["n_marcas"]), (6, 0))
        self.assertAlmostEqual(m["A"]["promedio"], (190 + 200 + 210 + 220 + 900 + 1100) / 6)
        self.assertEqual(m["A"]["mediana"], 215.0)
        self.assertEqual(m["B"]["n"], 5)

    def test_adjuntar_mercado_mismas_llaves_y_n_marcas(self):
        items = [{"sku": "A", "canal": "mercado_libre", "costo_unitario": 100},
                 {"sku": "B", "canal": "mercado_libre"},
                 {"sku": "A", "canal": "amazon"}]
        from services import supabase_db
        with _encendido(), \
                mock.patch.object(supabase_db, "fetch_all", return_value=_filas_panel()):
            PP._adjuntar_mercado(items)
        m = items[0]["mercado"]
        self.assertEqual(set(m), {"promedio", "mediana", "n", "n_marcas", "dias", "termino",
                                  "costo_veces", "margen_pct", "ganancia_neta",
                                  "precio_equilibrio", "precio_para_piso",
                                  "juez", "n_fuera_juez"})
        self.assertEqual((m["promedio"], m["n"], m["n_marcas"], m["termino"]),
                         (205.0, 4, 2, "tenis"))
        self.assertEqual(m["costo_veces"], round(100 / 205, 1))
        self.assertNotIn("mercado", items[1])     # <3 sin marcas → sin referencia
        self.assertNotIn("mercado", items[2])     # solo Mercado Libre

    def test_sql_mercado_trae_filas_con_los_mismos_filtros(self):
        sql = PP._SQL_MERCADO
        self.assertNotIn("group by", sql)
        self.assertNotIn("having", sql)
        for pedazo in ("r.titulo", "r.seller", "cfg.canal = 'mercado_libre'",
                       "r.precio > 0", "coalesce(r.es_nuestro, false) = false"):
            self.assertIn(pedazo, sql)

    def test_sql_mercado_usa_la_regla_del_juez_de_la_vista(self):
        # v0.649.0: `juez_vigente`/`juez_comparable` repiten la regla de
        # enrich.market_rival_comparable_v (0063). Si la vista cambia, esto avisa.
        import re
        from pathlib import Path
        plano = lambda s: re.sub(r"\s+", " ", s)
        sql = plano(PP._SQL_MERCADO)
        mig = (Path(__file__).resolve().parents[2] / "supabase/migrations/"
               "0063_enrich_market_rival_juicio.sql").read_text(encoding="utf-8")
        vista = mig[mig.index("create view enrich.market_rival_comparable_v"):]
        vista = plano(vista[:vista.index(";")])
        for pedazo in ("j.titulo_rival is not distinct from r.titulo",
                       "j.titulo_nuestro is not distinct from t.titulo", "j.clase = 'mismo'",
                       "j.sku = cfg.sku and j.canal = cfg.canal and j.externo_id = r.externo_id"):
            self.assertIn(pedazo, vista)
            self.assertIn(pedazo, sql)
        self.assertIn("enrich.market_sku_titulo_v", sql)

if __name__ == "__main__":
    unittest.main()
