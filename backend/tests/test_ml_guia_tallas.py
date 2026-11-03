"""Pruebas de la guía de tallas de Mercado Libre (v0.717.0): `services/ml_guia_tallas.py`
y su cableado en `services/publicar_ready.py` y `services/publicar.py`.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Las cuatro reglas medidas el 7-oct-2026 con `POST /items/validate`.** `SIZE_GRID_ID`
   va como value_name; `SIZE_GRID_ROW_ID` viaja siempre; `SIZE` es el texto del renglón
   COPIADO (con su coma o su punto) y el id del renglón también (no es posicional: las
   guías de ML traen huecos); `GENDER` es el de la guía. El vendor mandaba la guía como
   value_id, sin renglón y con `SIZE` de la lista genérica: 998 intentos, 0 éxitos.
2. **Nada adivinado: talla, género y color salen de una fuente del producto.**
   * TALLA: el eje que la variante FIJA. La «Talla» de la ficha del padre no es la suya
     (un padre con «Talla = 40» hacía que la 42 se publicara como 40).
   * COLOR: SOLO la estructura de WooCommerce —el eje propio de la variante, o el atributo
     de un producto simple—; viaja por empate EXACTO o como texto literal. El mapper
     mandaba «Rosa» por «Negro / Rosa», «Coral claro» por «Café», y la meta heredada del
     padre le ganaba al eje: 50 de 50 altas del primer día.
   * GÉNERO: decide la PRIMERA fuente que da UN género válido — atributo de Woo >
     formulario del Estudio > meta de la IA (basta lo que propone la IA). Una fuente con
     varios géneros, o con uno que no es de ML, no decide: se pasa a la siguiente. El
     título NUNCA bloquea: si nombra otro género, se dice en la vista previa.
   * EL FORMULARIO DEL ESTUDIO NO PRUEBA UNA CAPTURA: vuelve sembrado con los atributos
     de Woo y «Mejorar con IA» lo reemplaza entero. Por eso no decide el color (dos
     hermanas «Negro / Rosa» y «Negro / Morado» salían las dos «Negro») ni es la fuente
     más alta del género; lo que diga y no se use, se DICE en la vista previa.
   Sin alguno NO se publica y el mensaje dice qué falta (los faltantes del producto,
   juntos) y cómo se arregla en WooCommerce.
3. **La marca tampoco se rellena.** En moda el vendor manda la primera marca de la lista
   de la categoría (Asics en tenis, 136 de 136 intentos): con guía, viaja la declarada.
4. **Cada cuenta, su guía y su texto.** El mismo SKU es «G» en una cuenta y «L» en la otra.
5. **Si la guía no se puede leer, no se publica sin ella.** Y si lo que no se pudo leer es
   la categoría (no se sabe si exige guía), el alta sale EXACTAMENTE como siempre.
6. **Los reintentos del vendor que quitan `GENDER` o `SIZE_GRID_ID` ya no llegan a ML**
   (publisher_core.py:495-516): la guardia de `create_item` repone el atributo y no repite
   el envío que ML ya rechazó. Los reintentos legítimos sí salen.
7. **Un dominio que no exige guía sale byte a byte igual y con los mensajes de siempre**
   —con la guardia sin instalar Y con la guardia ya instalada, que es como vive
   producción desde la primera alta de moda—. Es un fix sin interruptor: no hay variable
   que lo apague; lo que lo deja inerte es que el dominio no exija guía.
8. **El mensaje de un rechazo sale de las causas `type == "error"`.** Un warning de envío
   tapaba el error real de la guía (fila 4524 de `ml_backlog`, 19-jun) y el del código de
   barras tapaba al de la guía (88 filas).
9. **Regla 11**: la resolución y el candado leen de ML con `requests`; corren en un hilo.
10. **Los frenos fallan CERRADO.** Las seis familias de `FAMILIAS_EN_REVISION` (por su
    SKU o el de su padre) no se publican desde el panel; y si una alta de moda queda sin
    pausar no se crea otra mientras siga `active` (o no se pueda leer). Y si el vendor
    revienta DESPUÉS de crear (su pausa o su descripción), el alta con guía no se
    reintenta: otro `POST /items` sería otra publicación.
11. **`construir_prod` solo AGREGA llaves** (la comparten Temu, TikTok, Amazon y el
    semáforo) y nadie muta `ml_attrs` / `wc_attrs` / `meta` (los comparten las dos cuentas).

Los ids de guías, categorías y SKUs son INVENTADOS (el repo es público). Sin red ni base.

    cd backend && python -m unittest tests.test_ml_guia_tallas -v
"""
from __future__ import annotations

import ast
import asyncio
import copy
import json
import threading
import unittest
from pathlib import Path
from unittest import mock

from services import meli
from services import ml_guia_tallas as G
from services import publicar
from services import publicar_ready as PR
from services import wp_db
from vendor.ml_ready import ml_api, publisher_core, size_chart_mapping, wc_category_mapping

VENDOR_BUILD = publisher_core.build_payload
BACKLOG_REAL = PR._backlog_ml          # noqa: SLF001 — antes de que `Base.setUp` lo sustituya

CAT_TENIS, CAT_ROPA, CAT_TALADRO, CAT_BRA = "MLM900001", "MLM900002", "MLM900003", "MLM900004"
CATS = {
    CAT_TENIS: {"id": CAT_TENIS, "settings": {"catalog_domain": "MLM-SNEAKERS"}},
    CAT_ROPA: {"id": CAT_ROPA, "settings": {"catalog_domain": "MLM-JACKETS_AND_COATS"}},
    CAT_TALADRO: {"id": CAT_TALADRO, "settings": {"catalog_domain": "MLM-ELECTRIC_DRILLS"}},
    CAT_BRA: {"id": CAT_BRA, "settings": {"catalog_domain": "MLM-BRAS"}},
}
MAPA = {
    "BEKURA": {"SNEAKERS:Mujer": "9000001", "SNEAKERS:Hombre": "9000003",
               "BRAS:Mujer": "9000005", "SNEAKERS:Sin género": "9000007"},
    "SANCORFASHION": {"SNEAKERS:Mujer": "9000002", "BRAS:Mujer": "9000006"},
}


def _renglon(guia, n, size, eu=None, pie=None):
    attrs = [{"id": "SIZE", "name": "Talla", "values": [{"name": size}]}]
    if pie:
        attrs.append({"id": "FOOT_LENGTH", "values": [
            {"name": f"{pie} cm", "struct": {"number": pie, "unit": "cm"}}]})
    if eu is not None:
        attrs.append({"id": "EU_SIZE", "values": [
            {"name": f"{eu} EU".replace(".", ","), "struct": {"number": float(eu), "unit": "EU"}}]})
    return {"id": f"{guia}:{n}", "attributes": attrs}


def _guia(gid, dominio, genero, id_genero, renglones):
    return {"id": gid, "names": {"MLM": f"Guia {gid}"}, "domain_id": dominio, "site_id": "MLM",
            "type": "SPECIFIC", "seller_id": 111, "main_attribute_id": "MX_SIZE",
            "attributes": [{"id": "GENDER", "name": "Género",
                            "values": [{"id": id_genero, "name": genero}]}],
            "rows": renglones}


GUIAS = {
    # Con huecos en la numeración a propósito: el id del renglón NO es su posición.
    "9000001": _guia("9000001", "SNEAKERS", "Mujer", "339665", [
        _renglon("9000001", 1, "23 MX", 36, 23), _renglon("9000001", 2, "24 MX", 37, 24),
        _renglon("9000001", 5, "25,5 MX", 38.5, 25.5), _renglon("9000001", 7, "26 MX", 39, 26)]),
    "9000002": _guia("9000002", "SNEAKERS", "Mujer", "339665", [
        _renglon("9000002", 1, "22 MX", 36, 22), _renglon("9000002", 2, "23 MX", 37, 23)]),
    # El mismo EU en dos renglones, como las seis guías de Hombre reales.
    "9000003": _guia("9000003", "SNEAKERS", "Hombre", "339666", [
        _renglon("9000003", 1, "26 MX", 40, 26), _renglon("9000003", 2, "26.5 MX", 40, 26.5),
        _renglon("9000003", 3, "27 MX", 41, 27)]),
    "9000005": _guia("9000005", "BRAS", "Mujer", "339665", [
        _renglon("9000005", 1, "M"), _renglon("9000005", 2, "G"), _renglon("9000005", 3, "XL")]),
    "9000006": _guia("9000006", "BRAS", "Mujer", "339665", [
        _renglon("9000006", 1, "M"), _renglon("9000006", 2, "L"), _renglon("9000006", 3, "XL")]),
    # Sin columna EU, como la 4827537 real.
    "9000007": _guia("9000007", "SNEAKERS", "Sin género", "110461", [
        _renglon("9000007", 1, "23 MX", None, 23), _renglon("9000007", 2, "24 MX", None, 24)]),
}
ATTRS_MODA = [   # forma de /categories/{id}/attributes en una categoría de moda
    {"id": "BRAND", "name": "Marca", "value_type": "string", "tags": {"required": True},
     "values": [{"id": "b1", "name": "Marca Ajena"}, {"id": "b2", "name": "Otra Marca"}]},
    {"id": "GENDER", "name": "Género", "value_type": "list", "tags": {"required": True},
     "values": [{"id": "339665", "name": "Mujer"}, {"id": "339666", "name": "Hombre"},
                {"id": "110461", "name": "Sin género"}]},
    {"id": "SIZE", "name": "Talla", "value_type": "string", "tags": {"required": True},
     "values": [{"id": "s10", "name": "10"}, {"id": "s24", "name": "24"}, {"id": "s37", "name": "37"}]},
    # Como la lista real (51 valores): empieza por «Coral claro» y trae «Rosa» ANTES que
    # «Negro»; no tiene «Café». Por eso el mapper manda «Rosa» por «Negro / Rosa».
    {"id": "COLOR", "name": "Color", "value_type": "string", "tags": {"required": True},
     "values": [{"id": "c1", "name": "Coral claro"}, {"id": "c3", "name": "Rosa"},
                {"id": "c2", "name": "Negro"}]},
    # No es obligatorio en ninguna de las 53 categorías de moda leídas el 7-oct.
    {"id": "MAIN_COLOR", "name": "Color principal", "value_type": "list", "tags": {},
     "values": [{"id": "m1", "name": "Negro"}, {"id": "m2", "name": "Gris"}]},
    {"id": "SIZE_GRID_ID", "value_type": "grid_id", "tags": {}},
    {"id": "SIZE_GRID_ROW_ID", "value_type": "grid_row_id", "tags": {"hidden": True}},
]
ATTRS_TALADRO = [
    {"id": "BRAND", "name": "Marca", "value_type": "string", "tags": {"required": True}},
    {"id": "VOLTAGE", "name": "Voltaje", "value_type": "string", "tags": {}},
]
# Las llaves que `construir_prod` AGREGA para la guía de tallas.
LLAVES_NUEVAS = {"padre_wc_id", "padre_sku", "ejes_propios", "atributos_panel",
                 "sin_inventario_libro"}   # la última es de main (stock del libro), no de la guía


def prod(cat=CAT_TENIS, talla="37", genero="Mujer", color="Negro", *, variante=True,
         panel=None, ejes=None, titulo="Tenis de prueba para correr", meta_color=...,
         genero_woo=None):
    """Un `prod` con la forma que arma `publicar_ready.construir_prod`.

    Por omisión es una VARIACIÓN que fija sus ejes Color y Talla (el caso real de moda) y
    cuyo género lo dejó la IA de Crear en la meta `ml_attr_GENDER` del padre. `ejes` pisa
    los ejes que fija; `panel` es lo que manda el formulario del Estudio ({nombre: valor}:
    a mano, sembrado o de «Mejorar con IA»); `genero_woo` es el atributo «Género» de
    WooCommerce (una opción o una lista); `variante=False` da un producto simple.
    """
    atributos = []
    if color:
        atributos.append({"name": "Color", "options": color if isinstance(color, list) else [color]})
    if talla is not None:
        atributos.append({"name": "Talla", "options": talla if isinstance(talla, list) else [talla]})
    propios = {a["name"].lower(): a["options"][0] for a in atributos if len(a["options"]) == 1}
    if genero_woo:      # del padre: un atributo de ficha, no un eje de la variante
        atributos.append({"name": "Género", "options": (
            genero_woo if isinstance(genero_woo, list) else [genero_woo])})
    p = {"wc_id": 1, "sku": "INV-0001-NEG-37", "title": titulo,
         "price": 499.0, "description": "x", "images": ["https://ejemplo.invalid/a.jpg"],
         "weight": "0.6", "length": "30", "width": "20", "height": "12", "stock": 5,
         "sin_inventario_woo": False, "ml_category_id": cat, "ml_category_name": "Prueba",
         "wc_categories": [], "ml_attrs": {"BRAND": "Ferrahome"},
         "wc_attrs": {a["name"].lower(): a["options"][0] for a in atributos},
         "tags": [], "meta": {}, "attributes": atributos,
         "padre_wc_id": 500 if variante else None,
         "padre_sku": "INV-0001" if variante else "",
         "ejes_propios": (propios if ejes is None else dict(ejes)) if variante else {},
         "atributos_panel": {}}
    heredado = (color[0] if isinstance(color, list) else color) if meta_color is ... else meta_color
    if heredado:
        p["ml_attrs"]["COLOR"] = heredado        # en una variante, la meta es la del PADRE
    if genero:
        p["ml_attrs"]["GENDER"] = genero         # la escribe la IA de Crear
    for nombre, valor in (panel or {}).items():  # como `construir_prod`: pisa wc_attrs
        p["wc_attrs"][nombre.lower()] = valor
        p["atributos_panel"][nombre.lower()] = valor
    return p


def _get_guia(url, headers=None, timeout=None):
    """`requests.get` de mentira: solo conoce las guías. Lo demás (categorías por la
    ruta pública) contesta 404, así que el respaldo sin token no encuentra nada."""
    gid = url.rsplit("/", 1)[-1]
    if "/catalog/charts/" in url and gid in GUIAS:
        return mock.Mock(status_code=200, json=mock.Mock(return_value=copy.deepcopy(GUIAS[gid])))
    return mock.Mock(status_code=404, json=mock.Mock(return_value={"error": "not_found"}))


def _get_con_categorias(url, headers=None, timeout=None):
    """Como `_get_guia`, y además contesta la ruta PÚBLICA de categorías (`/categories/{id}`,
    sin token) desde `CATS`: así la lee quien pregunta por el dominio sin token."""
    cat = url.rsplit("/", 1)[-1]
    if url.endswith(f"/categories/{cat}") and cat in CATS and headers is None:
        return mock.Mock(status_code=200, json=mock.Mock(return_value=copy.deepcopy(CATS[cat])))
    return _get_guia(url, headers, timeout)


def _una_guia(guia):
    """Un `requests.get` que contesta SIEMPRE esa guía."""
    return mock.Mock(status_code=200, json=mock.Mock(return_value=guia))


def _attr(payload, attr_id):
    return [a for a in payload["attributes"] if a["id"] == attr_id]


def _causa(code, tipo="error", message="m"):
    return {"code": code, "type": tipo, "message": message}


def _rechazo(*causas):
    return {"message": "Validation error", "cause": list(causas)}, 400


NO_PAUSADA = {"pausado": False, "status": "active", "sub_status": [], "intentos": 4,
              "http_pausa": 403}


def _candado(*pendientes):
    """El motivo del candado de pausa para esas publicaciones: (item_id, cuenta, estado
    leído en Mercado Libre | None si no se pudo leer)."""
    return G.motivo_moda_activa([{"item_id": i, "cuenta": c, "estado": e} for i, c, e in pendientes])


class Base(unittest.TestCase):
    """Sin red: ML contesta desde `CATS`, `ATTRS_*` y `GUIAS`; el mapping es `MAPA`."""

    def setUp(self):
        G._dominios.clear()                       # noqa: SLF001
        G._attrs_cat.clear()                      # noqa: SLF001
        G._guias.clear()                          # noqa: SLF001
        publisher_core._attr_cache.clear()        # noqa: SLF001
        publisher_core._sale_terms_cache.clear()  # noqa: SLF001
        PR._desinstalar_guardia()                 # noqa: SLF001
        PR._soltar_candado_moda()                 # noqa: SLF001 — estado de módulo: se limpia
        PR._configurado = False                   # noqa: SLF001
        self.addCleanup(setattr, PR, "_configurado", False)
        self.addCleanup(PR._soltar_candado_moda)  # noqa: SLF001
        self.addCleanup(setattr, publisher_core, "_save_backlog_fn",
                        publisher_core._save_backlog_fn)                    # noqa: SLF001
        # `configurar()` (main, v0.711.0 y v0.712.0) cambia dos nombres del vendor en memoria;
        # se devuelven como estaban para no contaminar las pruebas que miden el vendor solo.
        from vendor.ml_ready import attribute_mapper as ml_mapper
        self.addCleanup(setattr, ml_api, "_ensure_min_size", ml_api._ensure_min_size)  # noqa: SLF001
        self.addCleanup(setattr, ml_mapper, "_get_dimension_value",
                        ml_mapper._get_dimension_value)                     # noqa: SLF001
        self.posts: list[tuple[str, dict]] = []
        self.respuestas: list[tuple[dict, int]] = []
        self.resueltas: list[tuple[str, bool]] = []
        resolver_real = G.resolver

        def resolver(p, cuenta, token):
            # Regla 11: si esto corre en el hilo del event loop, el backend entero espera.
            self.resueltas.append((cuenta, threading.current_thread() is threading.main_thread()))
            return resolver_real(p, cuenta, token)

        def create_item(payload, token):
            self.posts.append((token, copy.deepcopy(payload)))
            return self.respuestas.pop(0) if self.respuestas else ({"id": f"MLM{len(self.posts)}"}, 201)

        for p in (
            mock.patch.object(G, "resolver", side_effect=resolver),
            mock.patch.dict(size_chart_mapping.CHARTS_BY_ACCOUNT, MAPA, clear=True),
            mock.patch.object(G.requests, "get", side_effect=_get_guia),
            mock.patch.object(G.time, "sleep"),
            mock.patch.object(ml_api, "get_category_info",
                              side_effect=lambda c, t: copy.deepcopy(CATS.get(c, {}))),
            mock.patch.object(ml_api, "get_category_attributes", side_effect=lambda c, t: copy.deepcopy(
                ATTRS_TALADRO if c == CAT_TALADRO else ATTRS_MODA)),
            mock.patch.object(ml_api, "get_category_sale_terms", return_value=[]),
            mock.patch.object(ml_api, "preupload_picture", return_value="PIC"),
            mock.patch.object(ml_api, "create_item", side_effect=create_item),
            mock.patch.object(ml_api, "refresh_token", return_value="tok-nuevo"),
            mock.patch.object(ml_api, "pause_item", return_value=200),
            mock.patch.object(ml_api, "update_description", return_value=200),
            mock.patch.object(meli, "_access_token", side_effect=lambda c=None: f"tok-{c}"),
            mock.patch.object(meli, "access_token_async",
                              mock.AsyncMock(side_effect=lambda c=None: f"tok-{c}")),
            mock.patch.object(PR, "_candado_alta_ml", mock.AsyncMock(return_value=None)),
            mock.patch.object(PR, "_backlog_ml"),
            mock.patch.object(PR, "_anotar_pausa_backlog"),
            mock.patch.object(PR, "asegurar_pausado", return_value={
                "pausado": True, "status": "paused", "sub_status": [], "intentos": 0, "http_pausa": 200}),
            mock.patch.object(PR.asyncio, "sleep", mock.AsyncMock()),
            # Lo que main sumó al alta después de v0.717.0 (revisión de categoría por texto
            # y foto, y la depuración de atributos del Estudio) tiene sus propias pruebas:
            # aquí no interviene.
            mock.patch.object(PR.categoria_vectores, "candado_alta", mock.AsyncMock(return_value=None)),
            mock.patch.object(PR, "_atributos_ml_para_alta", side_effect=lambda prod, campos, cuenta: (prod, [])),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.create_item_de_prueba = ml_api.create_item
        # Lo ÚLTIMO en registrarse corre PRIMERO: la guardia se quita antes de que el
        # `patch` de `ml_api.create_item` devuelva el original.
        self.addCleanup(PR._desinstalar_guardia)  # noqa: SLF001

    def crear(self, p, cuentas=None):
        with mock.patch.object(PR, "construir_prod", return_value=p):
            return asyncio.run(PR.crear_ml(p["sku"], 1, {}, cuentas,
                                           categoria_confirmada=p.get("ml_category_id")))

    def preview(self, p, cuenta="BEKURA", cuentas=None):
        with mock.patch.object(PR, "construir_prod", return_value=p):
            return asyncio.run(PR.preview_crear_ml(p["sku"], 1, {}, cuenta, cuentas))


# ═════════════════════════════ funciones puras ═══════════════════════════════

class Normalizar(unittest.TestCase):
    def test_letras(self):
        casos = {"S": "S", "CH": "S", "Chica": "S", "M": "M", "Mediana": "M", "L": "L", "G": "L",
                 "Grande": "L", "XL": "XL", "XG": "XL", "Extra Grande": "XL", "XXL": "2XL",
                 "2XL": "2XL", "2XG": "2XL", "XXXL": "3XL", "xs": "XS", "XCH": "XS", "5XL": "5XL",
                 "40": None, "24 MX": None, "S / M": None, "100 cm": None, "Unico": None,
                 "36B": None, "NEG": None, "": None}
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(G.letra_canonica(texto), esperado)

    def test_el_prefijo_numerico_de_una_letra_solo_vale_desde_dos(self):
        """En tallas extra «0X» y «1X» no son L ni XL: sin equivalencia, se bloquea."""
        for texto in ("0XL", "00XL", "1XL", "1XG", "0XS", "1XCH"):
            with self.subTest(texto=texto):
                self.assertIsNone(G.letra_canonica(texto))
        self.assertEqual(G.letra_canonica("2XL"), "2XL")
        self.assertEqual(G.letra_canonica("10XL"), "10XL")

    def test_numeros(self):
        casos = {"40": 40.0, "23½": 23.5, "24,5": 24.5, " 37 ": 37.0,
                 "24 MX": None, "42-43": None, "M": None, "": None}
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(G.numero(texto), esperado)

    def test_genero_solo_los_siete_de_ml(self):
        casos = {"Hombre": "Hombre", "['Hombre']": "Hombre", "mujer": "Mujer",
                 "Sin genero": "Sin género", "SIN GÉNERO INFANTIL": "Sin género infantil",
                 "ninas": "Niñas", "Bebes": "Bebés",
                 "Unisex": None, "Dama": None, "Hombre, Mujer": None, "": None}
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(G.genero_canonico(texto), esperado)

    def test_la_talla_sale_del_atributo_de_woo_no_del_formulario(self):
        """El Estudio manda «Talla» en `campos.atributos` y `construir_prod` la deja en
        `wc_attrs["talla"]`: ahí puede venir un valor del padre o de otra hermana."""
        p = prod(talla=None, panel={"Talla": "Único"})
        p["ml_attrs"]["size"] = "3XG"
        self.assertEqual(G.talla_de(p), (None, "ausente", []))
        self.assertEqual(G.talla_de(prod(talla="38")), ("38", "ok", []))
        self.assertEqual(G.talla_de(prod(talla="38", variante=False)), ("38", "ok", []))
        self.assertEqual(G.talla_de(prod(talla=["36", "37"], variante=False)),
                         (None, "varias", ["36", "37"]))

    def test_la_talla_de_la_ficha_del_padre_no_es_la_de_la_variante(self):
        """`wp_db._atributos_de_variacion` deja, para el eje que la variación NO fija, las
        opciones del padre: si el padre declara «Talla = 40» de un solo valor, la 42 la
        recibía como suya."""
        de_un_valor = prod(talla="40", ejes={"variante": "NEG-42", "color": "negro"})
        self.assertEqual(G.talla_de(de_un_valor), (None, "del_padre", ["40"]))
        any_ = prod(talla=["40", "41", "42"], ejes={"color": "negro"})   # eje en «cualquiera»
        self.assertEqual(G.talla_de(any_), (None, "del_padre", ["40", "41", "42"]))
        taxonomia = prod(talla=None, ejes={"pa_talla": "37", "pa_color": "negro"})
        taxonomia["attributes"].append({"name": "pa_talla", "options": ["37"]})
        self.assertEqual(G.talla_de(taxonomia), ("37", "ok", []))
        # Sin `padre_wc_id` pero con ejes propios sigue siendo una variación.
        sin_padre = prod(talla="40", ejes={"color": "negro"})
        sin_padre["padre_wc_id"] = None
        self.assertEqual(G.talla_de(sin_padre)[1], "del_padre")

    def test_las_fuentes_del_genero_por_rango(self):
        """1 = el atributo de WooCommerce (cada opción cuenta: `construir_prod` deja en
        `wc_attrs` solo la PRIMERA); 2 = el formulario del Estudio (`atributos_panel`);
        3 = la meta de la IA."""
        self.assertEqual((G.RANGO_WOO, G.RANGO_FORMULARIO, G.RANGO_META), (1, 2, 3))
        p = prod(genero="Mujer", panel={"Género": "Sin género"}, genero_woo=["Hombre", "Mujer"])
        self.assertEqual(G.fuentes_de_genero(p),
                         [(1, ["Hombre", "Mujer"]), (2, ["Sin género"]), (3, ["Mujer"])])
        self.assertEqual(G.fuentes_de_genero(prod(genero=None)), [])
        solo_meta = prod(genero="['Hombre']")
        solo_meta["wc_attrs"]["género"] = "Niñas"      # en wc_attrs y NO en el formulario: no cuenta
        self.assertEqual(G.fuentes_de_genero(solo_meta), [(3, ["['Hombre']"])])
        # La fila que deja «Mejorar con IA» (nombre legible «Género», campo GENDER) es rango 2.
        de_la_ia = prod(genero=None, panel={"Género": "Mujer"})
        self.assertEqual(G.fuentes_de_genero(de_la_ia), [(2, ["Mujer"])])

    def test_el_genero_que_nombra_un_titulo(self):
        """La palabra se COMPARA sin acentos y se devuelve como está escrita (en minúsculas):
        ahora que el título solo informa, la línea de la vista previa es el único lugar donde
        se ve, y decía «nina», «ninos», «bebe»."""
        casos = {
            "Playera para Hombre gris 2XL": ([("Hombre", "hombre")], False),
            "Tenis de DAMA casual": ([("Mujer", "dama")], False),
            "Zapato caballero y dama": ([("Mujer", "dama"), ("Hombre", "caballero")], False),
            "Sandalia para niña": ([("Niñas", "niña")], False),
            "Pijama para NIÑOS": ([("Niños", "niños")], False),
            "Pijama para ninos": ([("Niños", "ninos")], False),     # sin ñ en el título: tal cual
            "Mameluco de bebé": ([("Bebés", "bebé")], False),
            "Mameluco de bebé y niña": ([("Niñas", "niña"), ("Bebés", "bebé")], False),  # acentos sueltos
            "Tenis UNISEX para correr": ([], True),
            "Hombrera acolchada": ([], False),          # palabra entera
            "": ([], False),
            None: ([], False),
        }
        for titulo, esperado in casos.items():
            with self.subTest(titulo=titulo):
                self.assertEqual(G.generos_del_titulo(titulo), esperado)

    def test_el_color_sale_del_eje_propio_o_del_atributo_de_un_simple_y_de_nada_mas(self):
        """Solo decide la ESTRUCTURA de WooCommerce. El formulario del Estudio y la meta no
        deciden; si dicen otro color, no bloquean: va en la nota."""
        eje = G.color_de(prod(color="Negro / Rosa", meta_color="Gris"))
        self.assertEqual((eje["valor"], eje["origen"], eje["estado"]),
                         ("Negro / Rosa", G.ORIGEN_COLOR_EJE, "ok"))
        self.assertEqual(eje["nota"], "la meta ml_attr_COLOR dice «Gris»; la variante es "
                                      "«Negro / Rosa»: se publica «Negro / Rosa»")
        formulario = G.color_de(prod(color="Negro / Rosa", panel={"Color": "Negro con Rosa"}))
        self.assertEqual((formulario["valor"], formulario["origen"]),
                         ("Negro / Rosa", G.ORIGEN_COLOR_EJE))            # manda el eje, no el formulario
        self.assertEqual(formulario["nota"], "el formulario del Estudio dice «Negro con Rosa»; la "
                                             "variante es «Negro / Rosa»: se publica «Negro / Rosa»")
        los_dos = G.color_de(prod(color="Negro / Rosa", meta_color="['Gris']", panel={"COLOR": "Negro"}))
        self.assertEqual(los_dos["nota"], "el formulario del Estudio dice «Negro» y la meta "
                                          "ml_attr_COLOR dice «Gris»; la variante es «Negro / Rosa»: "
                                          "se publica «Negro / Rosa»")
        self.assertEqual(G.color_de(prod(color="Negro / Rosa"))["nota"], "")   # nadie dice otra cosa
        simple = G.color_de(prod(color="Azul", variante=False))
        self.assertEqual((simple["valor"], simple["origen"], simple["nota"]),
                         ("Azul", G.ORIGEN_COLOR_WOO, ""))
        simple_ia = G.color_de(prod(color="Negro / Rosa", variante=False, panel={"Color": "Negro"}))
        self.assertEqual((simple_ia["valor"], simple_ia["origen"]), ("Negro / Rosa", G.ORIGEN_COLOR_WOO))
        self.assertEqual(simple_ia["nota"], "el formulario del Estudio dice «Negro»; el atributo Color "
                                            "de WooCommerce es «Negro / Rosa»: se publica «Negro / Rosa»")
        # NO son fuente: la meta heredada, el formulario, la lista del padre, ni «color principal».
        self.assertEqual(G.color_de(prod(color=None, meta_color="Gris"))["estado"], "ausente")
        solo_formulario = G.color_de(prod(color=None, meta_color=None, panel={"Color": "Negro"}))
        self.assertEqual((solo_formulario["valor"], solo_formulario["estado"]), (None, "ausente"))
        del_padre = G.color_de(prod(color=["Negro", "Rosa"], ejes={"talla": "37"}))
        self.assertEqual((del_padre["valor"], del_padre["estado"]), (None, "del_padre"))
        un_valor_del_padre = G.color_de(prod(color="Negro", ejes={"talla": "37"}))
        self.assertEqual((un_valor_del_padre["valor"], un_valor_del_padre["estado"]),
                         (None, "del_padre"))
        varias = G.color_de(prod(color=["Negro", "Rosa"], variante=False))
        self.assertEqual((varias["valor"], varias["estado"]), (None, "varias"))
        otro = prod(color=None, panel={"Color principal": "Azul"})
        self.assertIsNone(G.color_de(otro)["valor"])

    def test_el_formulario_del_estudio_ni_sembrado_ni_escrito_decide_el_color(self):
        """El Estudio SIEMBRA su formulario con los atributos de WooCommerce
        (`wp_db.metadata_producto`) y los manda de vuelta al publicar; en una variación, la
        semilla del eje que ella no fija es LA PRIMERA opción de la lista del padre. Y
        «Mejorar con IA» lo reemplaza entero. Ninguna de las dos cosas es una captura."""
        del_padre = {"talla": "37"}                       # la variante no fija el Color
        semilla = prod(color=["Negro", "Rosa"], ejes=del_padre, panel={"Color": "Negro"})
        self.assertEqual(G._del_formulario(semilla, ("color",)), [])           # noqa: SLF001
        self.assertEqual(G.color_de(semilla)["estado"], "del_padre")
        otra_opcion = prod(color=["Negro", "Rosa"], ejes=del_padre, panel={"color": " ROSA "})
        self.assertEqual(G.color_de(otra_opcion)["estado"], "del_padre")       # sigue siendo del padre
        # La semilla se reconoce sin mirar mayúsculas ni espacios: no hay nada «del formulario».
        self.assertEqual(G._del_formulario(otra_opcion, ("color",)), [])       # noqa: SLF001
        sembrado = prod(genero=None, genero_woo="Mujer", panel={"Género": " MUJER "})
        self.assertEqual(G.fuentes_de_genero(sembrado), [(1, ["Mujer"])])
        # Y el NOMBRE de la fila se compara entero: «Color principal» no es el Color ni
        # «Tipo de género» el Género (ni para decidir ni para decirlo).
        self.assertEqual(G.color_de(prod(color="Negro", panel={"Color principal": "Azul"}))["nota"], "")
        self.assertEqual(G.fuentes_de_genero(prod(genero=None, panel={"Tipo de género": "Mujer"})), [])
        # Lo que NO es una opción de Woo vino del formulario (de una persona o de la IA):
        # se reconoce para poder DECIRLO, pero tampoco decide.
        escrito = prod(color=["Negro", "Rosa"], ejes=del_padre, panel={"Color": "Rosa pastel"})
        self.assertEqual(G._del_formulario(escrito, ("color",)), ["Rosa pastel"])   # noqa: SLF001
        self.assertEqual((G.color_de(escrito)["valor"], G.color_de(escrito)["estado"]),
                         (None, "del_padre"))
        # Producto simple: la semilla es la lista entera («Negro | Rosa»).
        lista = prod(color=["Negro", "Rosa"], variante=False, panel={"Color": "Negro | Rosa"})
        self.assertEqual(G.color_de(lista)["estado"], "varias")
        # El eje propio sembrado tal cual no cambia nada: sale del eje y no hay nada que decir.
        propio = prod(color="Negro / Rosa", panel={"Color": " negro / rosa "})
        self.assertEqual((G.color_de(propio)["origen"], G.color_de(propio)["nota"]),
                         (G.ORIGEN_COLOR_EJE, ""))
        # Lo mismo con el género: «Hombre | Mujer» en Woo siembra «Hombre», que no es rango 2.
        genero = prod(genero=None, panel={"Género": "Hombre"}, genero_woo=["Hombre", "Mujer"])
        self.assertEqual(G.fuentes_de_genero(genero), [(1, ["Hombre", "Mujer"])])
        self.assertEqual(G.decidir_genero(genero)["codigo"], "sin_genero")   # la semilla no decide
        # Lo que el formulario diga de más y NO sea una opción de Woo sí es otra fuente (2).
        genero["atributos_panel"]["género"] = "Sin género"
        self.assertEqual(G.fuentes_de_genero(genero), [(1, ["Hombre", "Mujer"]), (2, ["Sin género"])])
        self.assertEqual(G.decidir_genero(genero)["genero"], "Sin género")

    def test_un_renglon_con_largo_de_pie_imposible(self):
        """Forma real de `GET /catalog/charts/{id}` (con `struct`), ids inventados."""
        def renglon(mx, pie):
            return {"id": "9000099:6", "attributes": [
                {"id": "SIZE", "values": [{"name": f"{mx} MX", "struct": {"number": float(mx), "unit": "MX"}}]},
                {"id": "FOOT_LENGTH", "values": [{"name": f"{pie} cm", "struct": {"number": float(pie), "unit": "cm"}}]},
                {"id": "MX_SIZE", "values": [{"name": f"{mx} MX", "struct": {"number": float(mx), "unit": "MX"}}]},
                {"id": "EU_SIZE", "values": [{"name": "43 EU", "struct": {"number": 43.0, "unit": "EU"}}]}]}
        self.assertEqual(G.renglon_imposible(renglon(28, 38)), "dice 28 MX y 38 cm de largo de pie")
        self.assertEqual(G.renglon_imposible(renglon(28, 28)), "")
        self.assertEqual(G.renglon_imposible(renglon(28, 30)), "")           # 2 cm: tolerado
        self.assertEqual(G.renglon_imposible(renglon(28, 30.5)),
                         "dice 28 MX y 30.5 cm de largo de pie")
        # Solo cuando trae los DOS datos y la escala es MX en centímetros.
        self.assertEqual(G.renglon_imposible(_renglon("9", 1, "M")), "")
        self.assertEqual(G.renglon_imposible(_renglon("9", 1, "24 MX", 37)), "")
        us = {"id": "9:1", "attributes": [
            {"id": "SIZE", "values": [{"name": "7 US", "struct": {"number": 7.0, "unit": "US"}}]},
            {"id": "FOOT_LENGTH", "values": [{"name": "25 cm", "struct": {"number": 25.0, "unit": "cm"}}]}]}
        self.assertEqual(G.renglon_imposible(us), "")

    def test_el_tipo_de_calzado_que_nombra_el_titulo(self):
        self.assertIn("«sandalia o huarache»", G.aviso_de_tipo("Sandalia de correa para hombre 40", "SNEAKERS"))
        self.assertIn("SNEAKERS", G.aviso_de_tipo("Huaraches de piel", "SNEAKERS"))
        # Como lo escribe el catálogo: 27 SKUs de una familia dicen «ZANDALIAS» y están en tenis.
        self.assertIn("«sandalia o huarache»", G.aviso_de_tipo("ZANDALIAS DE CORRER 36 BEI", "SNEAKERS"))
        self.assertEqual(G.aviso_de_tipo("Zandalia de playa", "SANDALS_AND_CLOGS"), "")
        self.assertIn("«zapato de seguridad»", G.aviso_de_tipo("Zapato de seguridad azul 39", "SNEAKERS"))
        self.assertIn("«bota o botín»", G.aviso_de_tipo("Botín casual", "SANDALS_AND_CLOGS"))
        self.assertIn("«mocasín»", G.aviso_de_tipo("Mocasines de piel", "BOOTS_AND_BOOTIES"))
        self.assertIn("«tacón o zapatilla»", G.aviso_de_tipo("Zapatillas de tacón", "SNEAKERS"))
        # No dice nada: es el tipo del dominio, no nombra ninguno, o no es un dominio de calzado.
        for titulo, dominio in (("Tenis para correr", "SNEAKERS"),
                                ("Bota de seguridad con casquillo", "SAFETY_FOOTWEAR"),
                                ("Calzado deportivo ligero", "SNEAKERS"),
                                ("Tenis tipo bota", "SNEAKERS"),
                                ("Sandalia de playa", "JACKETS_AND_COATS"),
                                ("Botanero de madera", "SNEAKERS")):
            with self.subTest(titulo=titulo):
                self.assertEqual(G.aviso_de_tipo(titulo, dominio), "")
        # «bota de seguridad» nombra el calzado de seguridad, no la bota.
        self.assertIn("«zapato de seguridad»", G.aviso_de_tipo("Bota de seguridad", "BOOTS_AND_BOOTIES"))
        self.assertNotIn("«bota", G.aviso_de_tipo("Bota de seguridad", "SNEAKERS"))

    def test_los_59_dominios_del_7_de_octubre(self):
        self.assertEqual(len(G.DOMINIOS_CON_GUIA), 59)
        for d in ("SNEAKERS", "BRAS", "JACKETS_AND_COATS", "BABIES_FOOTWEAR", "DRESSES"):
            self.assertIn(d, G.DOMINIOS_CON_GUIA)
        self.assertNotIn("ZIPPERS", G.DOMINIOS_CON_GUIA)

    def test_la_huella_no_depende_del_orden_de_los_atributos(self):
        a = {"category_id": "X", "attributes": [{"id": "A", "value_name": "1"}, {"id": "B", "value_id": "2"}]}
        b = {"attributes": [{"value_id": "2", "id": "B"}, {"id": "A", "value_name": "1"}], "category_id": "X"}
        self.assertEqual(G.huella(a), G.huella(b))
        b["attributes"].pop()
        self.assertNotEqual(G.huella(a), G.huella(b))


# ═══════ familias en revisión: no se publican desde el panel (a mano, sin variable) ═══════

class FamiliasEnRevision(Base):
    """`G.FAMILIAS_EN_REVISION` es la lista REAL del código: seis familias de calzado que
    se publican a mano. No hay variable que sume ni que libere: se edita la constante."""

    def test_las_seis_familias(self):
        self.assertEqual(G.FAMILIAS_EN_REVISION, frozenset({
            "CALZ-0134", "CALZ-0129", "CALZ-0127",       # la persona eligió otro renglón en las vivas
            "CALZ-0089", "CALZ-0090", "CALZ-0200"}))     # traen tabla de tallas propia

    def test_el_sku_exacto_la_variante_y_el_padre_frenan_y_el_falso_amigo_no(self):
        f = G.familia_en_revision
        self.assertEqual(f({"sku": "CALZ-0129"}), "CALZ-0129")                 # SKU exacto
        self.assertEqual(f({"sku": "CALZ-0129-NEG-40"}), "CALZ-0129")          # su variante
        self.assertEqual(f({"sku": " calz-0129-neg-40 "}), "CALZ-0129")        # como venga escrito
        # El padre está en la lista aunque el SKU de la variante no lleve su prefijo.
        self.assertEqual(f({"sku": "OTRO-0001-NEG-40", "padre_sku": "CALZ-0129"}), "CALZ-0129")
        # El falso amigo: el prefijo es de FAMILIA («CALZ-0129» o «CALZ-0129-…»), no de texto.
        self.assertIsNone(f({"sku": "CALZ-01290"}))
        self.assertIsNone(f({"sku": "CALZ-01290-NEG-40"}))
        self.assertIsNone(f({"sku": "OTRO-0001-NEG-40", "padre_sku": "CALZ-01290"}))
        self.assertIsNone(f({"sku": "INV-0001-NEG-37", "padre_sku": "INV-0001"}))
        self.assertIsNone(f({"sku": None, "padre_sku": ""}))
        self.assertIsNone(f({}))
        for familia in G.FAMILIAS_EN_REVISION:
            self.assertEqual(f({"sku": f"{familia}-NEG-37"}), familia)

    def test_el_resolvedor_la_frena_sin_leer_la_guia_y_dice_como_salir(self):
        variante = prod()                                   # tenis de Mujer, talla 37: resolvería
        variante["sku"] = "CALZ-0129-NEG-40"
        por_el_padre = prod()
        por_el_padre["padre_sku"] = "CALZ-0134"             # su SKU sigue siendo INV-0001-NEG-37
        sin_genero = prod(genero=None, color=None, talla=None)   # ni se le piden sus faltantes
        sin_genero["sku"] = "CALZ-0200"
        with mock.patch.object(G.requests, "get") as get:
            for p, familia in ((variante, "CALZ-0129"), (por_el_padre, "CALZ-0134"),
                               (sin_genero, "CALZ-0200")):
                for cuenta in ("BEKURA", "SANCORFASHION"):
                    g = G.resolver(p, cuenta, "t")
                    self.assertEqual((g["estado"], g["codigos"]), ("bloqueado", ["guia_en_revision"]))
                    self.assertEqual(g["motivo"], G.MSG_EN_REVISION.format(familia=familia))
            get.assert_not_called()                         # ni se leyó la guía
        motivo = G.MSG_EN_REVISION.format(familia="CALZ-0129")
        self.assertIn("la familia CALZ-0129 está EN REVISIÓN y no se publica desde el panel", motivo)
        self.assertIn("Publícalo a mano en Mercado Libre", motivo)
        self.assertIn("quita CALZ-0129 de FAMILIAS_EN_REVISION", motivo)
        # El falso amigo y las demás familias resuelven como siempre.
        vecina = prod()
        vecina["sku"], vecina["padre_sku"] = "CALZ-01290-NEG-37", "CALZ-01290"
        self.assertEqual(G.resolver(vecina, "BEKURA", "t")["estado"], "ok")
        self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "ok")
        # Fuera de los dominios con guía una familia en revisión sale como siempre.
        taladro = prod(cat=CAT_TALADRO, talla=None, genero=None)
        taladro["sku"] = "CALZ-0129-NEG-40"
        self.assertEqual(G.resolver(taladro, "BEKURA", "t")["estado"], "no_aplica")

    def test_el_alta_no_manda_nada_y_la_vista_previa_no_ofrece_confirmar(self):
        """Las SEIS familias reales, por el camino del alta: ni un POST, ni la guía."""
        with mock.patch.object(G.requests, "get") as get:
            for familia in sorted(G.FAMILIAS_EN_REVISION):
                with self.subTest(familia=familia):
                    p = prod()                              # tenis de Mujer, talla 37: resolvería
                    p["sku"], p["padre_sku"] = f"{familia}-NEG-37", familia
                    motivo = G.MSG_EN_REVISION.format(familia=familia)
                    r = self.crear(p)
                    self.assertFalse(r["ok"])
                    self.assertTrue(all(f["error"] == motivo and f["intentos"] == 0
                                        for f in r["resultados"]))
                    vista = self.preview(p, "BEKURA", ["BEKURA", "SANCORFASHION"])
                    self.assertEqual((vista["ok"], vista["bloqueo_guia"]), (False, True))
                    # Es del PRODUCTO (igual en las dos cuentas): se dice una sola vez.
                    self.assertEqual(vista["motivo"], f"No se puede publicar en Mercado Libre. {motivo}")
            get.assert_not_called()                         # ni se leyó la guía
        self.assertEqual(self.posts, [])                    # ni un POST a Mercado Libre
        ml_api.preupload_picture.assert_not_called()

    def test_tenis_de_hombre_y_las_familias_de_ropa_ya_no_estan_en_revision(self):
        """Decisión de Brandon (9-oct-2026): en tenis de Hombre se usa la guía registrada."""
        hombre = prod(genero="Hombre", talla="41")
        g = G.resolver(hombre, "BEKURA", "t")
        self.assertEqual((g["estado"], g["guia_id"], g["size"]), ("ok", "9000003", "27 MX"))
        self.assertTrue(self.crear(hombre, ["BEKURA"])["ok"])
        self.assertEqual(len(self.posts), 1)
        ropa = prod()
        ropa["sku"], ropa["padre_sku"] = "ROP-0123-NEG-37", "ROP-0123"
        self.assertIsNone(G.familia_en_revision(ropa))
        self.assertEqual(G.resolver(ropa, "BEKURA", "t")["estado"], "ok")


# ═════════════════════════════ el resolvedor ═════════════════════════════════

class Resolver(Base):
    def test_calzado_con_talla_eu_copia_el_renglon(self):
        g = G.resolver(prod(), "BEKURA", "tok")
        self.assertEqual((g["estado"], g["guia_id"], g["renglon_id"], g["size"], g["como"]),
                         ("ok", "9000001", "9000001:2", "24 MX", "EU"))
        self.assertEqual((g["genero"], g["genero_id"], g["marca"]), ("Mujer", "339665", "Ferrahome"))

    def test_el_id_no_es_posicional_y_el_texto_va_con_su_coma(self):
        g = G.resolver(prod(talla="38½"), "BEKURA", "tok")
        self.assertEqual((g["renglon_id"], g["size"]), ("9000001:5", "25,5 MX"))

    def test_la_segunda_cuenta_usa_su_guia_y_su_texto(self):
        a, b = G.resolver(prod(), "BEKURA", "t"), G.resolver(prod(), "SANCORFASHION", "t")
        self.assertEqual((a["guia_id"], a["size"]), ("9000001", "24 MX"))
        self.assertEqual((b["guia_id"], b["size"]), ("9000002", "23 MX"))

    def test_brasier_la_L_es_G_en_una_cuenta_y_L_en_la_otra(self):
        a = G.resolver(prod(cat=CAT_BRA, talla="L"), "BEKURA", "t")
        b = G.resolver(prod(cat=CAT_BRA, talla="L"), "SANCORFASHION", "t")
        self.assertEqual((a["size"], a["como"], b["size"], b["como"]), ("G", "letra", "L", "texto"))

    def test_brasier_talla_que_no_tiene_la_guia(self):
        g = G.resolver(prod(cat=CAT_BRA, talla="S"), "BEKURA", "t")
        self.assertEqual(g["codigo"], "talla_sin_renglon")
        self.assertIn("M, G, XL", g["motivo"])

    def test_una_talla_1xl_no_es_la_xl_de_la_guia(self):
        self.assertEqual(G.resolver(prod(cat=CAT_BRA, talla="1XL"), "BEKURA", "t")["codigo"],
                         "talla_sin_renglon")
        self.assertEqual(G.resolver(prod(cat=CAT_BRA, talla="XL"), "BEKURA", "t")["estado"], "ok")

    # ── género ───────────────────────────────────────────────────────────────
    def test_sin_genero_bloquea_y_dice_para_cuales_hay_guia(self):
        g = G.resolver(prod(genero=None), "BEKURA", "t")
        self.assertEqual((g["estado"], g["codigo"]), ("bloqueado", "sin_genero"))
        self.assertIn("SNEAKERS", g["motivo"])
        self.assertIn("uno de: Mujer, Hombre, Sin género.", g["motivo"])      # los de la CATEGORÍA
        self.assertIn("hay guía registrada para: Mujer, Hombre, Sin género", g["motivo"])
        self.assertNotIn("hay guía registrada", g["motivo_base"])            # la pista va aparte
        self.assertIn(G.SALIDA_GENERO, g["motivo"])                          # cómo se arregla

    def test_todo_bloqueo_de_genero_dice_como_se_arregla_en_woocommerce(self):
        """La salida es UNA: el atributo «Género» de WooCommerce con una sola opción (lo
        único que una persona fijó de verdad)."""
        self.assertIn("en WooCommerce, deja en el producto el atributo «Género» con UNA sola opción",
                      G.SALIDA_GENERO)
        solo_bebes = [{"id": "GENDER", "values": [{"id": "1", "name": "Bebés"}]}]
        casos = {
            "sin_genero": prod(genero=None),
            "genero_invalido": prod(genero="Unisex"),
        }
        for codigo, p in casos.items():
            with self.subTest(codigo=codigo):
                g = G.resolver(p, "BEKURA", "t")
                self.assertEqual((g["estado"], g["codigo"]), ("bloqueado", codigo))
                self.assertIn(G.SALIDA_GENERO, g["motivo"])
        G._attrs_cat.clear()                                                    # noqa: SLF001
        with mock.patch.object(ml_api, "get_category_attributes", return_value=solo_bebes):
            fuera = G.resolver(prod(genero="Hombre"), "BEKURA", "t")
        self.assertEqual(fuera["codigo"], "genero_fuera_de_categoria")
        self.assertIn(G.SALIDA_GENERO, fuera["motivo"])

    def test_si_no_se_saben_los_generos_de_la_categoria_se_listan_los_siete(self):
        with mock.patch.object(ml_api, "get_category_attributes", return_value=[]):
            g = G.resolver(prod(genero=None), "BEKURA", "t")
            self.assertIn("Sin género infantil", g["motivo"])
            self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "ok")   # y no bloquea
        self.assertNotIn(CAT_TENIS, G._attrs_cat)                               # noqa: SLF001

    def test_genero_que_no_es_de_ml_bloquea(self):
        g = G.resolver(prod(genero="Unisex"), "BEKURA", "t")
        self.assertEqual(g["codigo"], "genero_invalido")
        self.assertIn("«Unisex»", g["motivo"])
        self.assertIn("lo propuso la IA al crear el producto", g["motivo"])

    def test_el_genero_del_formulario_manda_sobre_la_meta_de_la_ia_y_se_rotula_con_la_verdad(self):
        """Crear dejó «Mujer» en la meta y el formulario del Estudio dice «Sin género»: decide
        el formulario (rango 2) y la meta solo se menciona. Pero el rótulo NO dice que lo
        capturó una persona: «Mejorar con IA» llena ese mismo formulario."""
        g = G.resolver(prod(genero="Mujer", talla="24 MX", panel={"Género": "sin genero"}), "BEKURA", "t")
        self.assertEqual((g["estado"], g["genero"], g["genero_rango"], g["guia_id"]),
                         ("ok", "Sin género", 2, "9000007"))
        self.assertEqual(g["origen_genero"], "formulario del Estudio; puede venir de «Mejorar con IA»: "
                                             "revísalo; la meta de Crear decía «Mujer»")
        linea = G.aviso(g)
        self.assertIn("Género Sin género (formulario del Estudio; puede venir de «Mejorar con IA»: "
                      "revísalo; la meta de Crear decía «Mujer»)", linea)
        self.assertNotIn("capturado", linea)

    def test_el_genero_que_deja_mejorar_con_ia_no_se_rotula_como_captura(self):
        """El hallazgo: sin atributo «Género» en WooCommerce, la fila que «Mejorar con IA»
        mete al formulario (`{nombre: Género, campo: GENDER, valor: Mujer}`) entraba como la
        fuente más alta y rotulada «capturado en el panel»."""
        p = prod(genero=None, panel={"Género": "Mujer"})
        g = G.resolver(p, "BEKURA", "t")
        self.assertEqual((g["estado"], g["genero"], g["genero_rango"]), ("ok", "Mujer", 2))
        self.assertEqual(g["origen_genero"], G.ORIGEN_FORMULARIO)
        self.assertIn("Mejorar con IA", G.aviso(g))
        self.assertNotIn("capturado", G.aviso(g))

    def test_el_atributo_de_woo_manda_sobre_el_formulario_y_sobre_la_meta(self):
        g = G.resolver(prod(genero="Hombre", genero_woo="Mujer"), "BEKURA", "t")   # la meta de la IA dice otra cosa
        self.assertEqual((g["estado"], g["genero"], g["genero_rango"]), ("ok", "Mujer", 1))
        self.assertEqual(g["origen_genero"],
                         "atributo Género de WooCommerce; la meta de Crear decía «Hombre»")
        # El formulario dice OTRO género (a mano o de «Mejorar con IA»): no decide ni bloquea, se dice.
        h = G.resolver(prod(genero="Hombre", panel={"GENDER": "Sin género"}, genero_woo="Mujer"),
                       "BEKURA", "t")
        self.assertEqual((h["estado"], h["genero"], h["genero_rango"], h["guia_id"]),
                         ("ok", "Mujer", 1, "9000001"))
        self.assertEqual(h["origen_genero"],
                         "atributo Género de WooCommerce; el formulario del Estudio decía «Sin género»; "
                         "la meta de Crear decía «Hombre»")
        self.assertIn("Género Mujer (atributo Género de WooCommerce; el formulario del Estudio decía "
                      "«Sin género»; la meta de Crear decía «Hombre»)", G.aviso(h))

    def test_una_fuente_con_varios_generos_no_decide_y_se_pasa_a_la_siguiente(self):
        """Un atributo de WooCommerce con dos opciones ya no bloquea por sí solo: no decide."""
        p = prod(genero=None, genero_woo=["Mujer", "Hombre"])
        self.assertEqual(p["wc_attrs"]["género"], "Mujer")   # lo que deja construir_prod: la primera
        g = G.resolver(p, "BEKURA", "t")                     # y no hay otra fuente: falta el género
        self.assertEqual((g["estado"], g["codigo"]), ("bloqueado", "sin_genero"))
        self.assertIn("falta el GÉNERO (el atributo Género de WooCommerce trae «Mujer», «Hombre» y no "
                      "decide). Mercado Libre exige", g["motivo"])
        self.assertIn(G.SALIDA_GENERO, g["motivo"])
        # Con la meta de la IA detrás decide la meta, y lo de WooCommerce se dice.
        con_meta = G.resolver(prod(genero="Hombre", talla="41", genero_woo=["Mujer", "Hombre"]),
                              "BEKURA", "t")
        self.assertEqual((con_meta["estado"], con_meta["genero"], con_meta["genero_rango"]),
                         ("ok", "Hombre", 3))
        self.assertEqual(con_meta["origen_genero"],
                         "lo propuso la IA al crear el producto: revísalo; el atributo Género de "
                         "WooCommerce trae «Mujer», «Hombre» y no decide")
        # El formulario del Estudio va antes que la meta.
        con_formulario = G.resolver(prod(genero="Hombre", talla="24 MX", genero_woo=["Mujer", "Hombre"],
                                         panel={"Género": "Sin género"}), "BEKURA", "t")
        self.assertEqual((con_formulario["estado"], con_formulario["genero"],
                          con_formulario["genero_rango"]), ("ok", "Sin género", 2))
        self.assertEqual(con_formulario["origen_genero"],
                         "formulario del Estudio; puede venir de «Mejorar con IA»: revísalo; el atributo "
                         "Género de WooCommerce trae «Mujer», «Hombre» y no decide; la meta de Crear "
                         "decía «Hombre»")
        # Dos metas que no cuadran tampoco deciden.
        metas = prod(genero="Mujer")
        metas["ml_attrs"]["gender"] = "Hombre"           # ml_attr_GENDER y ml_attr_gender no cuadran
        m = G.resolver(metas, "BEKURA", "t")
        self.assertEqual(m["codigo"], "sin_genero")
        self.assertIn("(la meta de Crear trae «Mujer», «Hombre» y no decide)", m["motivo"])

    def test_un_genero_que_no_es_de_ml_no_decide_y_solo_bloquea_si_ninguna_fuente_da_uno(self):
        # «Dama» en el formulario (o en WooCommerce) no decide: decide la meta de la IA, y se dice.
        arriba = G.resolver(prod(genero="Mujer", panel={"Género": "Dama"}), "BEKURA", "t")
        self.assertEqual((arriba["estado"], arriba["genero"], arriba["genero_rango"]), ("ok", "Mujer", 3))
        self.assertEqual(arriba["origen_genero"], "lo propuso la IA al crear el producto: revísalo; el "
                                                  "formulario del Estudio trae «Dama» y no decide")
        de_woo = G.resolver(prod(genero="Mujer", genero_woo="Dama"), "BEKURA", "t")
        self.assertEqual((de_woo["estado"], de_woo["genero"], de_woo["genero_rango"]), ("ok", "Mujer", 3))
        self.assertIn("el atributo Género de WooCommerce trae «Dama» y no decide", de_woo["origen_genero"])
        # Si NINGUNA fuente da un género de ML se bloquea, y se dice el primero que no lo es.
        ninguna = G.resolver(prod(genero="Unisex", panel={"Género": "Dama"}), "BEKURA", "t")
        self.assertEqual((ninguna["estado"], ninguna["codigo"]), ("bloqueado", "genero_invalido"))
        self.assertIn("el género «Dama» (formulario del Estudio; puede venir de «Mejorar con IA») "
                      "no es un género de Mercado Libre", ninguna["motivo"])
        self.assertIn(G.SALIDA_GENERO, ninguna["motivo"])
        solo_woo = G.resolver(prod(genero=None, genero_woo="Dama"), "BEKURA", "t")
        self.assertEqual(solo_woo["codigo"], "genero_invalido")
        self.assertIn("el género «Dama» (atributo Género de WooCommerce)", solo_woo["motivo"])
        # Una fuente posterior que no es de ML no estorba a la que decide: se dice.
        abajo = G.resolver(prod(genero="Unisex", panel={"Género": "Mujer"}), "BEKURA", "t")
        self.assertEqual((abajo["estado"], abajo["genero"]), ("ok", "Mujer"))
        self.assertIn("la meta de Crear decía «Unisex»", abajo["origen_genero"])
        mas_abajo = G.resolver(prod(genero="Unisex", panel={"Género": "Dama"}, genero_woo="Mujer"),
                               "BEKURA", "t")
        self.assertEqual((mas_abajo["estado"], mas_abajo["genero"], mas_abajo["genero_rango"]),
                         ("ok", "Mujer", 1))

    def test_la_meta_de_la_ia_se_rotula_con_la_verdad(self):
        g = G.resolver(prod(genero="['Mujer']"), "BEKURA", "t")       # la huella de la IA
        self.assertEqual((g["estado"], g["genero"], g["genero_rango"]), ("ok", "Mujer", 3))
        self.assertEqual(g["origen_genero"], "lo propuso la IA al crear el producto: revísalo")

    def test_el_titulo_nunca_bloquea_solo_se_dice(self):
        """Basta lo que propone la IA (Brandon, 9-oct-2026). 25 de 469 SKUs con género no
        dicen lo que dice su título («PLAYERA PARA HOMBRE» con «Sin género» en 14 hermanas
        de 15): ya no se frenan; la línea de la vista previa lo dice y nada más."""
        titulo = "Tenis para HOMBRE gris claro"
        g = G.resolver(prod(genero="Sin género", talla="24 MX", titulo=titulo), "BEKURA", "t")
        self.assertEqual((g["estado"], g["genero"], g["genero_rango"], g["guia_id"]),
                         ("ok", "Sin género", 3, "9000007"))
        self.assertEqual(g["origen_genero"],
                         "lo propuso la IA al crear el producto: revísalo; el título dice «hombre»")
        self.assertIn("Género Sin género (lo propuso la IA al crear el producto: revísalo; el título "
                      "dice «hombre»)", G.aviso(g))
        # …venga el género del formulario del Estudio…
        f = G.resolver(prod(genero=None, talla="24 MX", titulo=titulo, panel={"Género": "Sin género"}),
                       "BEKURA", "t")
        self.assertEqual((f["estado"], f["genero"], f["genero_rango"]), ("ok", "Sin género", 2))
        self.assertEqual(f["origen_genero"], G.ORIGEN_FORMULARIO + "; el título dice «hombre»")
        # …o del atributo de WooCommerce (hay productos «para hombre» que son unisex).
        woo = prod(genero=None, titulo=titulo)
        woo["attributes"].append({"name": "pa_genero", "options": ["Mujer"]})
        w = G.resolver(woo, "BEKURA", "t")
        self.assertEqual((w["estado"], w["genero"], w["genero_rango"]), ("ok", "Mujer", 1))
        self.assertEqual(w["origen_genero"], "atributo Género de WooCommerce; el título dice «hombre»")
        # Y el alta SALE, con el género que se decidió (antes: ni un POST).
        self.assertTrue(self.crear(prod(genero="Sin género", talla="24 MX", titulo=titulo), ["BEKURA"])["ok"])
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(_attr(self.posts[0][1], "GENDER"),
                         [{"id": "GENDER", "value_id": "110461", "value_name": "Sin género"}])
        # El título confirma, o no nombra ninguno: no hay nada que decir.
        for mudo in ("Tenis de dama casual", "Tenis casual"):
            self.assertEqual(G.resolver(prod(titulo=mudo), "BEKURA", "t")["origen_genero"], G.ORIGEN_META)
        # «unisex» es compatible con «Sin género», aunque además nombre a los dos.
        unisex = prod(genero="Sin género", talla="24 MX", titulo="Tenis unisex para hombre y mujer")
        self.assertEqual(G.resolver(unisex, "BEKURA", "t")["origen_genero"], G.ORIGEN_META)
        # Nombra dos y el decidido es solo uno de ellos: se dice el otro.
        dos = G.resolver(prod(genero="Mujer", titulo="Tenis para hombre y mujer"), "BEKURA", "t")
        self.assertEqual((dos["estado"], dos["origen_genero"]),
                         ("ok", G.ORIGEN_META + "; el título dice «hombre»"))

    def test_un_genero_que_la_categoria_no_admite_dice_que_es_la_categoria(self):
        """413 SKUs están en «Calzado para Bebés» (solo «Bebés») con tallas de adulto."""
        solo_bebes = [{"id": "GENDER", "values": [{"id": "1", "name": "Bebés"}]}]
        with mock.patch.object(ml_api, "get_category_attributes", return_value=solo_bebes):
            g = G.resolver(prod(genero="Hombre"), "BEKURA", "t")
        self.assertEqual(g["codigo"], "genero_fuera_de_categoria")
        self.assertIn("ahí solo vale «Bebés»", g["motivo"])
        self.assertIn("Revisa la categoría", g["motivo"])

    # ── color ────────────────────────────────────────────────────────────────
    def test_un_color_compuesto_viaja_literal_no_el_del_detalle(self):
        """«Negro / Rosa» salía «Rosa» (el primero de la lista que es subcadena)."""
        g = G.resolver(prod(color="Negro / Rosa", meta_color="Gris"), "BEKURA", "t")
        self.assertEqual((g["estado"], g["color"], g["color_id"], g["color_origen"]),
                         ("ok", "Negro / Rosa", "", "eje Color de la variante"))
        self.assertEqual(g["main_color"], {"accion": "quitar"})

    def test_cafe_viaja_como_cafe_no_como_el_primero_de_la_lista(self):
        g = G.resolver(prod(color="Café", meta_color=None), "BEKURA", "t")
        self.assertEqual((g["color"], g["color_id"]), ("Café", ""))

    def test_un_color_de_la_lista_viaja_con_su_id_solo_por_empate_exacto(self):
        g = G.resolver(prod(color="  NEGRO "), "BEKURA", "t")
        self.assertEqual((g["color"], g["color_id"]), ("Negro", "c2"))
        self.assertEqual(g["main_color"], {"accion": "igualar", "value_id": "m1", "value_name": "Negro"})
        rosa = G.resolver(prod(color="Rosa palo"), "BEKURA", "t")       # «Rosa» es subcadena: NO
        self.assertEqual((rosa["color"], rosa["color_id"]), ("Rosa palo", ""))

    def test_la_meta_heredada_del_padre_no_le_gana_al_eje_de_la_variante(self):
        g = G.resolver(prod(color="Negro", meta_color="Rosa"), "BEKURA", "t")
        self.assertEqual((g["color"], g["color_id"]), ("Negro", "c2"))

    def test_el_eje_color_de_la_variante_manda_sobre_el_formulario(self):
        """El formulario del Estudio NO decide el color (a mano o de «Mejorar con IA», no se
        distinguen): manda el eje que la variante fija en WooCommerce, y lo otro se dice."""
        g = G.resolver(prod(color="Negro / Rosa", panel={"COLOR": "Rosa"}), "BEKURA", "t")
        self.assertEqual((g["estado"], g["color"], g["color_id"], g["color_origen"]),
                         ("ok", "Negro / Rosa", "", "eje Color de la variante"))
        self.assertEqual(g["color_nota"], "el formulario del Estudio dice «Rosa»; la variante es "
                                          "«Negro / Rosa»: se publica «Negro / Rosa»")
        # Un producto SIMPLE con su Color en WooCommerce: igual.
        simple = G.resolver(prod(color="Negro / Rosa", variante=False, panel={"Color": "Negro"}),
                            "BEKURA", "t")
        self.assertEqual((simple["estado"], simple["color"], simple["color_origen"]),
                         ("ok", "Negro / Rosa", "atributo Color de WooCommerce"))

    def test_dos_hermanas_con_el_mismo_color_en_el_formulario_no_salen_iguales(self):
        """El hallazgo bloqueante: «Mejorar con IA» deja «Negro» en el formulario de
        «Negro / Rosa» y de «Negro / Morado» (su lista de COLOR no tiene compuestos). Con el
        formulario por encima del eje salían las dos «Negro», misma guía, renglón y talla:
        dos publicaciones idénticas."""
        rosa = prod(color="Negro / Rosa", panel={"Color": "Negro"})
        morado = prod(color="Negro / Morado", panel={"Color": "Negro"})
        morado["sku"] = "INV-0001-NEG/MOR-37"
        for cuenta in ("BEKURA", "SANCORFASHION"):
            a, b = G.resolver(rosa, cuenta, "t"), G.resolver(morado, cuenta, "t")
            self.assertEqual((a["estado"], b["estado"]), ("ok", "ok"))
            self.assertEqual((a["renglon_id"], a["size"]), (b["renglon_id"], b["size"]))   # misma talla…
            self.assertEqual((a["color"], b["color"]), ("Negro / Rosa", "Negro / Morado"))  # …otro color
        self.crear(rosa, ["BEKURA"])
        self.crear(morado, ["BEKURA"])
        self.assertEqual([_attr(p, "COLOR") for _, p in self.posts],
                         [[{"id": "COLOR", "value_name": "Negro / Rosa"}],
                          [{"id": "COLOR", "value_name": "Negro / Morado"}]])

    def test_sin_una_fuente_del_color_no_se_publica(self):
        """Sin color el mapper mandaría el primero de su lista («Coral claro»); y una meta
        heredada, o la lista del padre, no son de ESTA variante."""
        casos = (
            (prod(color=None, meta_color=None), "esta variante no tiene COLOR propio."),
            (prod(color=None, meta_color="Gris"),
             "la meta ml_attr_COLOR «Gris» no cuenta: es la del producto padre"),
            (prod(color=["Negro", "Rosa"], ejes={"talla": "37"}),
             "el Color que hay, Negro, Rosa, es el del producto padre"),
            (prod(color="Negro", ejes={"talla": "37"}),
             "el Color que hay, Negro, es el del producto padre"),
            (prod(color=None, meta_color="Gris", variante=False),
             "la meta ml_attr_COLOR «Gris» no cuenta: la propuso la IA"),
            (prod(color=["Negro", "Rosa"], variante=False),
             "el atributo «Color» trae 2 opciones: Negro, Rosa"),
            # El formulario del Estudio (a mano o de «Mejorar con IA») tampoco es fuente:
            # ni solo, ni junto a la lista del padre, ni en un producto simple.
            (prod(color=None, meta_color=None, panel={"Color": "Negro"}),
             "esta variante no tiene COLOR propio (el formulario del Estudio dice «Negro»: tampoco cuenta)"),
            (prod(color=["Negro", "Rosa"], ejes={"talla": "37"}, panel={"Color": "Rosa pastel"}),
             "el Color que hay, Negro, Rosa, es el del producto padre; el formulario del Estudio "
             "dice «Rosa pastel»: tampoco cuenta"),
            (prod(color=None, meta_color="Gris", variante=False, panel={"Color": "Negro"}),
             "la meta ml_attr_COLOR «Gris» no cuenta: la propuso la IA; el formulario del Estudio "
             "dice «Negro»: tampoco cuenta"),
        )
        for p, frase in casos:
            with self.subTest(frase=frase):
                g = G.resolver(p, "BEKURA", "t")
                self.assertEqual((g["estado"], g["codigo"]), ("bloqueado", "color_no_fijado"))
                self.assertIn(frase, g["motivo"])
                self.assertIn("Cómo se arregla: en WooCommerce", g["motivo"])   # se fija en WooCommerce
        variante = G.resolver(prod(color=None, meta_color=None), "BEKURA", "t")["motivo"]
        self.assertIn("fíjalo en esta variante", variante)
        self.assertIn("sus ejes: talla", variante)
        self.assertIn("ni del formulario del Estudio", variante)
        simple = G.resolver(prod(color=None, meta_color=None, variante=False), "BEKURA", "t")["motivo"]
        self.assertIn("deja un solo valor en el atributo «Color» del producto", simple)
        self.assertIn("ni del formulario del Estudio", simple)
        # La SEMILLA del formulario (una opción que WooCommerce ya lista, con otras mayúsculas)
        # no se reporta como algo que «dice el formulario».
        semilla = G.resolver(prod(color=["Negro", "Rosa"], ejes={"talla": "37"}, panel={"color": " ROSA "}),
                             "BEKURA", "t")["motivo"]
        self.assertNotIn("el formulario del Estudio dice", semilla)
        # Una llave que el mapper SÍ llevaba a COLOR («color principal») tampoco es fuente.
        otra = prod(color=None, meta_color=None)
        otra["wc_attrs"]["color principal"] = "Azul"
        self.assertEqual(G.resolver(otra, "BEKURA", "t")["codigo"], "color_no_fijado")

    # ── talla ────────────────────────────────────────────────────────────────
    def test_la_talla_del_padre_bloquea_el_caso_de_la_objecion(self):
        """Padre con el eje «Variante» (NEG-40 | NEG-41 | NEG-42) + «Talla = 40» + «Color =
        Negro»; la variante NEG-42 solo fija `attribute_variante`. Salía como talla 40."""
        p = prod(talla="40", genero="Hombre", color="Negro", ejes={"variante": "NEG-42"},
                 panel={"Variante": "NEG-42", "Talla": "40", "Color": "Negro"})   # la siembra del Estudio
        p["attributes"].insert(0, {"name": "Variante", "options": ["NEG-42"]})
        g = G.resolver(p, "BEKURA", "t")
        self.assertEqual(g["estado"], "bloqueado")
        self.assertEqual(g["codigos"], ["color_no_fijado", "talla_no_fijada"])   # los dos son del padre
        self.assertIn("la Talla que hay (40) es la del producto padre", g["motivo"])
        self.assertIn("La variante debe fijar un EJE de talla", g["motivo"])
        self.assertIn("sus ejes: variante", g["motivo"])
        self.assertIn("el Color que hay, Negro, es el del producto padre", g["motivo"])
        # Escribir otro color en el formulario del Estudio NO lo arregla: se fija en WooCommerce.
        p["atributos_panel"]["color"] = "Negro mate"
        igual = G.resolver(p, "BEKURA", "t")
        self.assertEqual(igual["codigos"], ["color_no_fijado", "talla_no_fijada"])
        self.assertIn("el formulario del Estudio dice «Negro mate»: tampoco cuenta", igual["motivo"])
        # Con el eje Color fijado en la variante, lo que queda es la talla.
        p["ejes_propios"]["color"] = "Negro"
        self.assertEqual(G.resolver(p, "BEKURA", "t")["codigos"], ["talla_no_fijada"])

    def test_sin_talla_pide_un_eje_de_variacion_no_agregar_un_atributo(self):
        g = G.resolver(prod(talla=None), "BEKURA", "t")
        self.assertEqual(g["codigo"], "sin_talla")
        self.assertIn("eje de variación «Talla»", g["motivo"])
        self.assertIn("«usado para variaciones»", g["motivo"])
        simple = G.resolver(prod(talla=None, variante=False), "BEKURA", "t")
        self.assertEqual(simple["codigo"], "sin_talla")
        self.assertIn("agrega el atributo «Talla» al producto con un solo valor", simple["motivo"])

    def test_eje_talla_sin_fijar_bloquea(self):
        g = G.resolver(prod(talla=["36", "37", "38"]), "BEKURA", "t")      # «cualquiera» en la variante
        self.assertEqual(g["codigo"], "talla_no_fijada")
        self.assertIn("36, 37, 38", g["motivo"])
        simple = G.resolver(prod(talla=["36", "37", "38"], variante=False), "BEKURA", "t")
        self.assertEqual(simple["codigo"], "talla_no_fijada")
        self.assertIn("trae 3 opciones (36, 37, 38)", simple["motivo"])

    def test_los_faltantes_del_producto_se_dicen_juntos(self):
        g = G.resolver(prod(genero=None, color=None, talla=None, meta_color=None), "BEKURA", "t")
        self.assertEqual(g["codigos"], ["sin_genero", "color_no_fijado", "sin_talla"])
        self.assertEqual(g["codigo"], "sin_genero")
        for frase in ("falta el GÉNERO", "no tiene COLOR propio", "no tiene TALLA propia"):
            self.assertIn(frase, g["motivo"])
        self.assertEqual(g["motivo"].count("Guía de tallas:"), 1)

    # ── guía y renglón ───────────────────────────────────────────────────────
    def test_ropa_sin_guia_dice_cuenta_dominio_y_genero(self):
        g = G.resolver(prod(cat=CAT_ROPA, talla="M", genero="Hombre"), "BEKURA", "t")
        self.assertEqual(g["codigo"], "sin_guia")
        self.assertIn("BEKURA · JACKETS_AND_COATS · Hombre", g["motivo"])
        self.assertIn("no hay ninguna guía de JACKETS_AND_COATS", g["motivo"])

    def test_talla_fuera_de_la_guia_lista_renglones_y_tallas_eu(self):
        g = G.resolver(prod(talla="43"), "BEKURA", "t")
        self.assertEqual(g["codigo"], "talla_sin_renglon")
        self.assertIn("23 MX, 24 MX, 25,5 MX, 26 MX", g["motivo"])
        self.assertIn("36, 37, 38.5, 39", g["motivo"])

    def test_un_numero_suelto_nunca_se_empata_contra_mx(self):
        """El número del SKU es EU (115 de 130 publicaciones vivas; 0 son MX)."""
        self.assertEqual(G.resolver(prod(talla="24"), "BEKURA", "t")["codigo"], "talla_sin_renglon")

    def test_la_talla_escrita_como_el_renglon_empata_por_texto(self):
        g = G.resolver(prod(talla="24 mx"), "BEKURA", "t")
        self.assertEqual((g["estado"], g["size"], g["como"]), ("ok", "24 MX", "texto"))

    def test_eu_en_dos_renglones_no_se_elige(self):
        g = G.resolver(prod(talla="40", genero="Hombre"), "BEKURA", "t")
        self.assertEqual(g["codigo"], "talla_ambigua")
        self.assertIn("«26 MX» (9000003:1)", g["motivo"])
        self.assertIn("«26.5 MX» (9000003:2)", g["motivo"])
        self.assertIn("En el Estudio", g["motivo"])
        self.assertNotIn("a la vez en la columna EU y en la MX", g["motivo"])

    def test_el_estudio_elige_entre_los_renglones_que_le_tocan_a_la_talla(self):
        p = prod(talla="40", genero="Hombre", panel={"Talla": "26,5 mx"})   # lo escrito en el Estudio
        g = G.resolver(p, "BEKURA", "t")
        self.assertEqual((g["estado"], g["renglon_id"], g["size"], g["a_mano"]),
                         ("ok", "9000003:2", "26.5 MX", True))
        # Se dice, y sin afirmar que lo tecleó una persona: «Mejorar con IA» llena ese formulario.
        self.assertIn("(renglón elegido en el Estudio entre los que le tocan; lo escrito ahí puede "
                      "venir de «Mejorar con IA»: revísalo)", G.aviso(g))

    def test_el_estudio_no_abre_un_renglon_que_no_es_de_esa_talla(self):
        p = prod(talla="40", genero="Hombre", panel={"Talla": "27 MX"})     # existe, pero es el EU 41
        self.assertEqual(G.resolver(p, "BEKURA", "t")["codigo"], "talla_ambigua")
        q = prod(talla="41", genero="Hombre", panel={"Talla": "26 MX"})     # un solo renglón: manda Woo
        g = G.resolver(q, "BEKURA", "t")
        self.assertEqual((g["estado"], g["size"], g["a_mano"], g["talla_estudio"]),
                         ("ok", "27 MX", False, "26 MX"))
        self.assertIn("En el Estudio la Talla dice «26 MX»: no se usó", G.aviso(g))
        r = prod(talla="37", genero="Sin género", panel={"Talla": "24 MX"})  # guía sin EU: tampoco
        self.assertEqual(G.resolver(r, "BEKURA", "t")["codigo"], "talla_sin_renglon")

    def test_un_numero_que_esta_en_la_columna_eu_y_en_la_mx_de_la_misma_guia_no_se_elige(self):
        """Pasa en guías infantiles (EU 21–35 contra MX 13–22): no se sabe la escala."""
        infantil = _guia("9000001", "SNEAKERS", "Mujer", "339665", [
            _renglon("9000001", 1, "14 MX", 22, 14), _renglon("9000001", 2, "15 MX", 23, 15),
            _renglon("9000001", 9, "22 MX", 34, 22)])
        with mock.patch.object(G.requests, "get", return_value=_una_guia(infantil)):
            g = G.resolver(prod(talla="22"), "BEKURA", "t")
            self.assertEqual(g["codigo"], "talla_ambigua")
            self.assertIn("«14 MX» (9000001:1), «22 MX» (9000001:9)", g["motivo"])
            self.assertIn("a la vez en la columna EU y en la MX de esta guía", g["motivo"])
            # La salida humana es la de siempre: el texto exacto del renglón en el Estudio.
            elegida = G.resolver(prod(talla="22", panel={"Talla": "22 MX"}), "BEKURA", "t")
            self.assertEqual((elegida["estado"], elegida["renglon_id"], elegida["como"], elegida["a_mano"]),
                             ("ok", "9000001:9", "MX", True))
            # Un número que solo está en EU sigue saliendo por EU; uno que solo está en MX, no sale.
            self.assertEqual(G.resolver(prod(talla="23"), "BEKURA", "t")["renglon_id"], "9000001:2")
            self.assertEqual(G.resolver(prod(talla="15"), "BEKURA", "t")["codigo"], "talla_sin_renglon")

    def test_un_renglon_con_un_dato_imposible_no_se_usa(self):
        """Caso real (ids inventados): un renglón «28 MX» con 38 cm de largo de pie. Es la
        tabla que ve el comprador."""
        rota = copy.deepcopy(GUIAS["9000001"])
        rota["rows"][1] = _renglon("9000001", 2, "24 MX", 37, 34)        # 24 MX y 34 cm
        with mock.patch.object(G.requests, "get", return_value=_una_guia(rota)):
            g = G.resolver(prod(), "BEKURA", "t")
            self.assertEqual((g["estado"], g["codigo"]), ("bloqueado", "guia_no_sirve"))
            self.assertIn("el renglón 9000001:2 dice 24 MX y 34 cm de largo de pie; "
                          "corrígelo en Mercado Libre", g["motivo"])
            self.assertNotIn("size_chart_mapping.py", g["motivo"])       # el arreglo es en ML
            # Los demás renglones de la misma guía sí se usan.
            self.assertEqual(G.resolver(prod(talla="36"), "BEKURA", "t")["estado"], "ok")

    def test_guia_sin_columna_eu_lo_dice(self):
        g = G.resolver(prod(talla="37", genero="Sin género"), "BEKURA", "t")
        self.assertEqual(g["codigo"], "talla_sin_renglon")
        self.assertIn("no tiene columna EU", g["motivo"])

    def test_si_ml_no_entrega_la_guia_se_bloquea_tras_un_reintento(self):
        for fallo in (mock.Mock(status_code=503), mock.Mock(status_code=429)):
            G._guias.clear()                                       # noqa: SLF001
            with mock.patch.object(G.requests, "get", return_value=fallo) as get, \
                    self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
                g = G.resolver(prod(), "BEKURA", "t")
            self.assertEqual((g["estado"], g["codigo"]), ("bloqueado", "guia_ilegible"))
            self.assertEqual(get.call_count, 2)
        with mock.patch.object(G.requests, "get", side_effect=OSError("sin red")) as get, \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            g = G.resolver(prod(), "BEKURA", "t")
        self.assertEqual((g["codigo"], get.call_count), ("guia_ilegible", 2))
        self.assertEqual(G._guias, {})                                         # noqa: SLF001

    def test_401_403_y_404_no_se_reintentan_y_dicen_que_son(self):
        """Medido el 7-oct sin token: una guía SPECIFIC contesta 404 «chart_not_found»
        a quien no puede verla; un 404 no prueba que la guía se borró."""
        for status, frase in ((401, "el token de la cuenta venció"),
                              (403, "no tiene permiso para leer guías"),
                              (404, "no existe, o que esta cuenta no puede verla")):
            G._guias.clear()                                       # noqa: SLF001
            with mock.patch.object(G.requests, "get", return_value=mock.Mock(status_code=status)) as get, \
                    self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
                g = G.resolver(prod(), "BEKURA", "t")
            self.assertEqual((g["codigo"], get.call_count), ("guia_ilegible", 1))
            self.assertIn(f"HTTP {status}", g["motivo"])
            self.assertIn(frase, g["motivo"])

    def test_el_401_no_promete_que_el_token_se_renueva_solo(self):
        """En moda el alta ya no llega al POST que le renovaba el token al vendor."""
        with mock.patch.object(G.requests, "get", return_value=mock.Mock(status_code=401)), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            motivo = G.resolver(prod(), "BEKURA", "t")["motivo"]
        self.assertNotIn("se renueva solo", motivo)
        self.assertIn("En moda este paso no lo renueva", motivo)
        self.assertIn("cuando entra un pedido de esa cuenta o al publicar en ella algo que no es de moda",
                      motivo)
        self.assertIn("después se puede reintentar", motivo)

    def test_una_guia_que_no_es_la_que_dice_el_mapping_no_se_usa(self):
        base = GUIAS["9000001"]
        cambios = (
            ({"type": "STANDARD"}, "SPECIFIC"),
            ({"chart_status": "INACTIVE"}, "baja"),
            ({"domain_id": "BOOTS_AND_BOOTIES"}, "dominio"),
            ({"attributes": [{"id": "GENDER", "values": [{"id": "339666", "name": "Hombre"}]}]}, "género"),
            ({"attributes": base["attributes"] + [{"id": "AGE_GROUP", "values": [{"name": "Adultos"}]}]},
             "AGE_GROUP"),
            ({"attributes": base["attributes"] + [{"id": "BRAND", "values": [{"name": "Otra Marca"}]}]},
             "es de la marca «Otra Marca»"),
        )
        for cambio, frase in cambios:
            with self.subTest(cambio=list(cambio)):
                G._guias.clear()                                   # noqa: SLF001
                mala = {**copy.deepcopy(base), **cambio}
                with mock.patch.object(G.requests, "get", return_value=_una_guia(mala)):
                    g = G.resolver(prod(), "BEKURA", "t")
                self.assertEqual(g["codigo"], "guia_no_sirve")
                self.assertIn(frase, g["motivo"])
                self.assertIn("Corrige backend/vendor/ml_ready/size_chart_mapping.py.", g["motivo"])

    def test_una_guia_con_la_marca_del_producto_en_la_cabecera_si_se_usa(self):
        """La doc de ML del 9-jul-2026 pide BRAND en la cabecera de las guías nuevas."""
        con_marca = copy.deepcopy(GUIAS["9000001"])
        con_marca["attributes"].append({"id": "BRAND", "values": [{"name": "FERRAHOME"}]})
        with mock.patch.object(G.requests, "get", return_value=_una_guia(con_marca)):
            self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "ok")

    def test_dominio_que_no_exige_guia_no_lee_ninguna(self):
        with mock.patch.object(G.requests, "get") as get, \
                mock.patch.object(ml_api, "get_category_attributes") as attrs:
            g = G.resolver(prod(cat=CAT_TALADRO, talla=None, genero=None, color=None), "BEKURA", "t")
        self.assertEqual(g["estado"], "no_aplica")
        get.assert_not_called()
        attrs.assert_not_called()

    def test_categoria_ilegible_es_desconocido_y_el_fallo_no_se_recuerda(self):
        with mock.patch.object(ml_api, "get_category_info", return_value={}), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "desconocido")
        with mock.patch.object(ml_api, "get_category_info", side_effect=OSError("sin red")), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "desconocido")
        self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "ok")

    def test_con_el_token_vencido_la_categoria_se_lee_por_la_ruta_publica(self):
        """`/categories/{id}` contesta 200 sin token (medido el 7-oct-2026)."""
        def publico(url, headers=None, timeout=None):
            if url.endswith(f"/categories/{CAT_TENIS}"):
                self.assertIsNone(headers)               # sin Authorization
                return mock.Mock(status_code=200, json=mock.Mock(return_value=CATS[CAT_TENIS]))
            return _get_guia(url, headers, timeout)
        with mock.patch.object(ml_api, "get_category_info", return_value={}), \
                mock.patch.object(G.requests, "get", side_effect=publico):
            g = G.resolver(prod(), "BEKURA", "t")
        self.assertEqual((g["estado"], g["dominio"]), ("ok", "SNEAKERS"))

    def test_guia_y_categoria_se_leen_una_sola_vez(self):
        with mock.patch.object(G.requests, "get", side_effect=_get_guia) as get, \
                mock.patch.object(ml_api, "get_category_info", side_effect=lambda c, t: CATS[c]) as cat, \
                mock.patch.object(ml_api, "get_category_attributes", return_value=ATTRS_MODA) as attrs:
            for _ in range(3):
                G.resolver(prod(), "BEKURA", "t")
        self.assertEqual((get.call_count, cat.call_count, attrs.call_count), (1, 1, 1))

    def test_la_cache_de_la_guia_caduca_a_los_diez_minutos(self):
        with mock.patch.object(G.requests, "get", side_effect=_get_guia) as get:
            G.resolver(prod(), "BEKURA", "t")
            with mock.patch.object(G.time, "monotonic", return_value=G.time.monotonic() + G.TTL_GUIA_S + 1):
                G.resolver(prod(), "BEKURA", "t")
        self.assertEqual(get.call_count, 2)

    def test_antes_de_bloquear_por_el_contenido_de_una_guia_en_cache_se_relee_una_vez(self):
        """Quien agregó el renglón que el mensaje le pidió no espera 10 minutos."""
        corta = copy.deepcopy(GUIAS["9000001"])
        corta["rows"] = corta["rows"][:1]                    # todavía sin el renglón del EU 37
        respuestas = [_una_guia(corta), _una_guia(copy.deepcopy(GUIAS["9000001"]))]
        with mock.patch.object(G.requests, "get", side_effect=respuestas) as get:
            self.assertEqual(G.resolver(prod(talla="36"), "BEKURA", "t")["estado"], "ok")    # llena la caché
            g = G.resolver(prod(), "BEKURA", "t")            # de caché daría «sin renglón»: relee
        self.assertEqual((g["estado"], g["renglon_id"], get.call_count), ("ok", "9000001:2", 2))

    def test_la_relectura_es_una_sola_y_solo_en_el_camino_de_bloqueo(self):
        with mock.patch.object(G.requests, "get", side_effect=_get_guia) as get:
            G.resolver(prod(), "BEKURA", "t")                            # 1 lectura (llena la caché)
            self.assertEqual(G.resolver(prod(talla="43"), "BEKURA", "t")["codigo"], "talla_sin_renglon")
            self.assertEqual(get.call_count, 2)                          # releyó UNA vez y bloqueó
            self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "ok")
            self.assertEqual(get.call_count, 2)                          # un `ok` de caché no relee
        G._guias.clear()                                                 # noqa: SLF001
        with mock.patch.object(G.requests, "get", side_effect=_get_guia) as get:
            G.resolver(prod(talla="43"), "BEKURA", "t")                  # lectura fresca: no relee
        self.assertEqual(get.call_count, 1)
        # Si la relectura falla, se queda el veredicto de la copia que había.
        with mock.patch.object(G.requests, "get", return_value=mock.Mock(status_code=503)):
            self.assertEqual(G.resolver(prod(talla="43"), "BEKURA", "t")["codigo"], "talla_sin_renglon")

    def test_no_muta_prod_y_nunca_lanza(self):
        for p in (prod(), prod(genero=None, color=None, talla=None),
                  prod(color="Negro / Rosa", meta_color="Gris", panel={"Género": "Mujer"})):
            antes = copy.deepcopy(p)
            for cuenta in ("BEKURA", "SANCORFASHION"):   # las dos cuentas comparten el mismo dict
                G.resolver(p, cuenta, "t")
            self.assertEqual(p, antes)
        with mock.patch.object(G, "renglones_para", side_effect=RuntimeError("bum")), \
                self.assertLogs("omnicanal.ml_guia_tallas", "ERROR"):
            g = G.resolver(prod(), "BEKURA", "t")
        self.assertEqual((g["estado"], g["codigo"]), ("bloqueado", "error_interno"))   # ya se sabía que exige guía
        with mock.patch.object(G, "categoria_efectiva", side_effect=RuntimeError("bum")), \
                self.assertLogs("omnicanal.ml_guia_tallas", "ERROR"):
            self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "desconocido")

    def test_la_linea_de_la_vista_previa(self):
        self.assertEqual(
            G.aviso(G.resolver(prod(), "BEKURA", "t")),
            "BEKURA · guía de tallas 9000001 «Guia 9000001»: talla 37 → renglón 9000001:2 «24 MX» "
            "(por la columna EU de la guía; pie de 24 cm). Género Mujer (lo propuso la IA al crear "
            "el producto: revísalo), marca Ferrahome.")
        bloqueada = G.aviso(G.resolver(prod(genero=None), "BEKURA", "t"))
        self.assertTrue(bloqueada.startswith("BEKURA · NO se publicará. Guía de tallas: falta el GÉNERO."))
        self.assertEqual(G.aviso({"estado": "no_aplica"}), "")
        self.assertEqual(G.aviso({"estado": "desconocido"}), "")

    def test_una_linea_de_log_por_resolucion(self):
        with self.assertLogs("omnicanal.ml_guia_tallas", "INFO") as logs:
            G.resolver(prod(), "BEKURA", "t")
            G.resolver(prod(genero=None), "BEKURA", "t")
            G.resolver(prod(talla="43"), "BEKURA", "t")          # de caché + relectura: UNA línea
        de_guia = [m for m in logs.output if "guia_tallas sku=" in m]
        self.assertEqual(len(de_guia), 3)
        self.assertTrue("cuenta=BEKURA" in de_guia[0] and "estado=ok" in de_guia[0]
                        and "renglon=9000001:2" in de_guia[0] and "color='Negro'" in de_guia[0])
        self.assertIn("estado=bloqueado codigo=sin_genero", de_guia[1])
        self.assertIn("estado=bloqueado codigo=talla_sin_renglon", de_guia[2])


# ═══════════════════════ contra el `build_payload` REAL ══════════════════════

class PayloadDelVendor(Base):
    def _con(self, p, cuenta="BEKURA"):
        payload = VENDOR_BUILD(dict(p), "tok", True, cuenta)
        return G.aplicar(payload, G.resolver(p, cuenta, "tok"), cuenta)

    def test_lo_que_el_vendor_manda_solo(self):
        """Línea base: los rellenos que este cambio no deja salir."""
        p = VENDOR_BUILD(prod(genero=None), "tok", True, "BEKURA")
        self.assertEqual(_attr(p, "BRAND"), [{"id": "BRAND", "value_id": "b1"}])          # marca ajena
        self.assertEqual(_attr(p, "GENDER"), [{"id": "GENDER", "value_id": "339665"}])    # "Mujer" inventado
        self.assertEqual(_attr(p, "SIZE"), [{"id": "SIZE", "value_id": "s37"}])
        self.assertEqual(_attr(p, "SIZE_GRID_ROW_ID"), [])
        sin_color = VENDOR_BUILD(prod(color=None, meta_color=None), "tok", True, "BEKURA")
        self.assertEqual(_attr(sin_color, "COLOR"), [{"id": "COLOR", "value_id": "c1"}])  # "Coral claro"
        # El color: por subcadena («Rosa»), por «el primero» («Coral claro»), y la meta
        # heredada del padre por encima del eje de la variante.
        compuesto = VENDOR_BUILD(prod(color="Negro / Rosa"), "tok", True, "BEKURA")
        self.assertEqual(_attr(compuesto, "COLOR"), [{"id": "COLOR", "value_id": "c3"}])  # "Rosa"
        cafe = VENDOR_BUILD(prod(color="Café"), "tok", True, "BEKURA")
        self.assertEqual(_attr(cafe, "COLOR"), [{"id": "COLOR", "value_id": "c1"}])       # "Coral claro"
        heredada = VENDOR_BUILD(prod(color="Negro", meta_color="Rosa"), "tok", True, "BEKURA")
        self.assertEqual(_attr(heredada, "COLOR"), [{"id": "COLOR", "value_id": "c3"}])   # la del padre

    def test_dominio_sin_guia_sale_byte_a_byte_igual(self):
        p = prod(cat=CAT_TALADRO, talla=None, genero=None)
        hoy = json.dumps(VENDOR_BUILD(dict(p), "tok", True, "BEKURA"), sort_keys=True, ensure_ascii=False)
        self.assertEqual(json.dumps(self._con(p), sort_keys=True, ensure_ascii=False), hoy)
        vista = self.preview(p, "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual(json.dumps(vista["payload"], sort_keys=True, ensure_ascii=False), hoy)

    def test_con_guia_cambian_seis_atributos_y_nada_mas(self):
        p = prod()
        hoy = VENDOR_BUILD(dict(p), "tok", True, "BEKURA")
        con = self._con(p)
        self.assertEqual(G.ATRIBUTOS_QUE_PISA,
                         ("BRAND", "GENDER", "SIZE", "COLOR", "SIZE_GRID_ID", "SIZE_GRID_ROW_ID"))
        self.assertEqual(_attr(con, "BRAND"), [{"id": "BRAND", "value_name": "Ferrahome"}])
        self.assertEqual(_attr(con, "GENDER"), [{"id": "GENDER", "value_id": "339665", "value_name": "Mujer"}])
        self.assertEqual(_attr(con, "SIZE"), [{"id": "SIZE", "value_name": "24 MX"}])
        self.assertEqual(_attr(con, "COLOR"), [{"id": "COLOR", "value_id": "c2", "value_name": "Negro"}])
        self.assertEqual(_attr(con, "SIZE_GRID_ID"), [{"id": "SIZE_GRID_ID", "value_name": "9000001"}])
        self.assertEqual(_attr(con, "SIZE_GRID_ROW_ID"),
                         [{"id": "SIZE_GRID_ROW_ID", "value_name": "9000001:2"}])

        def sin_los_seis(payload):
            return {**payload, "attributes": [a for a in payload["attributes"]
                                              if a["id"] not in G.ATRIBUTOS_QUE_PISA]}
        self.assertEqual(sin_los_seis(con), sin_los_seis(hoy))

    def test_el_color_que_viaja_es_el_de_la_variante(self):
        compuesto = self._con(prod(color="Negro / Rosa", meta_color="Gris"))
        self.assertEqual(_attr(compuesto, "COLOR"), [{"id": "COLOR", "value_name": "Negro / Rosa"}])
        cafe = self._con(prod(color="Café", meta_color=None))
        self.assertEqual(_attr(cafe, "COLOR"), [{"id": "COLOR", "value_name": "Café"}])
        heredada = self._con(prod(color="Negro", meta_color="Rosa"))
        self.assertEqual(_attr(heredada, "COLOR"), [{"id": "COLOR", "value_id": "c2", "value_name": "Negro"}])

    def test_el_color_principal_del_vendor_no_contradice_al_color(self):
        """`MAIN_COLOR` sale de una meta del PADRE (985 de 1,001 intentos lo llevaban)."""
        def con_principal(color, principal):
            p = prod(color=color)
            p["ml_attrs"]["MAIN_COLOR"] = principal
            hoy = VENDOR_BUILD(dict(p), "tok", True, "BEKURA")
            return p, hoy, self._con(p)

        _, hoy, igual = con_principal("Negro", "Gris")               # empata exacto: se iguala
        self.assertEqual(_attr(hoy, "MAIN_COLOR"), [{"id": "MAIN_COLOR", "value_id": "m2"}])     # "Gris"
        self.assertEqual(_attr(igual, "MAIN_COLOR"),
                         [{"id": "MAIN_COLOR", "value_id": "m1", "value_name": "Negro"}])
        _, hoy, fuera = con_principal("Negro / Rosa", "Gris")        # no empata y no es obligatorio
        self.assertEqual(len(_attr(hoy, "MAIN_COLOR")), 1)
        self.assertEqual(_attr(fuera, "MAIN_COLOR"), [])
        # Si el vendor NO lo manda, no se inventa.
        self.assertEqual(_attr(self._con(prod(color="Negro")), "MAIN_COLOR"), [])
        # Obligatorio y sin dato: se deja como lo manda el vendor (y la vista previa lo dice).
        obligatorio = copy.deepcopy(ATTRS_MODA)
        next(a for a in obligatorio if a["id"] == "MAIN_COLOR")["tags"] = {"required": True}
        G._attrs_cat.clear()                                         # noqa: SLF001
        publisher_core._attr_cache.clear()                           # noqa: SLF001
        with mock.patch.object(ml_api, "get_category_attributes", return_value=obligatorio):
            p, hoy, dejado = con_principal("Negro / Rosa", "Gris")
            guia = G.resolver(p, "BEKURA", "tok")
        self.assertEqual(guia["main_color"], {"accion": "dejar"})
        self.assertEqual(_attr(dejado, "MAIN_COLOR"), _attr(hoy, "MAIN_COLOR"))
        self.assertIn("se queda como lo manda el publicador («Gris»)", G.aviso_de_color(guia, hoy))

    def test_aplicar_dos_veces_da_lo_mismo(self):
        for p in (prod(), prod(color="Negro / Rosa", meta_color="Gris")):
            p["ml_attrs"]["MAIN_COLOR"] = "Gris"
            guia = G.resolver(p, "BEKURA", "tok")
            una = G.aplicar(VENDOR_BUILD(dict(p), "tok", True, "BEKURA"), guia, "BEKURA")
            dos = G.aplicar(copy.deepcopy(una), guia, "BEKURA")
            self.assertEqual(una, dos)
            self.assertEqual(G.huella(una), G.huella(dos))

    def test_sin_family_name_no_se_arma_una_publicacion_a_medias(self):
        p = prod()
        guia = G.resolver(p, "BEKURA", "tok")
        with mock.patch.object(ml_api, "get_category_info", return_value={}):   # el vendor no pudo leer
            payload = VENDOR_BUILD(dict(p), "tok", True, "BEKURA")
        with self.assertRaises(G.GuiaTallasError) as error:
            G.aplicar(payload, guia, "BEKURA")
        self.assertEqual(error.exception.codigo, "sin_family")

    def test_la_guia_de_una_cuenta_no_se_aplica_en_la_otra(self):
        p = prod()
        guia = G.resolver(p, "BEKURA", "tok")
        with self.assertRaises(G.GuiaTallasError) as error:
            G.aplicar(VENDOR_BUILD(dict(p), "tok", True, "SANCORFASHION"), guia, "SANCORFASHION")
        self.assertEqual(error.exception.codigo, "otra_cuenta")
        otra = VENDOR_BUILD(dict(prod(cat=CAT_BRA, talla="M")), "tok", True, "BEKURA")
        with self.assertRaises(G.GuiaTallasError) as error:
            G.aplicar(otra, guia, "BEKURA")
        self.assertEqual(error.exception.codigo, "otra_categoria")


# ════════════ `construir_prod` solo AGREGA llaves (lo comparten 5 canales) ═══

class ConstruirProd(unittest.TestCase):
    """`construir_prod` la usan Mercado Libre, Temu, TikTok, Amazon y el semáforo del
    Estudio: las cuatro llaves de la guía de tallas son ADITIVAS y el resto del dict es el
    de siempre. `ESPERADO` es lo que devolvía en `2a88e60` (v0.623.0) con estos mismos
    dobles (comparado corriendo las dos versiones lado a lado)."""

    META = {"_regular_price": "499", "_price": "450", "_stock": "7", "_weight": "0.6",
            "_length": "30", "_width": "20", "_height": "12",
            "ml_categoria_id": "MLM900001",
            "ml_categoria_niveles": '[{"name": "Calzado"}, {"name": "Tenis"}]',
            "ml_attr_GENDER": "Mujer", "ml_attr_COLOR": "Gris",
            "attribute_pa_color": "negro-rosa", "attribute_talla": "37",
            "attribute_pa_material": "", "attribute_estilo": "del-padre"}
    HEREDADAS = {"ml_categoria_id", "ml_categoria_niveles", "ml_attr_GENDER", "ml_attr_COLOR",
                 "_weight", "_length", "_width", "_height", "attribute_estilo"}
    ATRIBUTOS = [{"name": "pa_color", "options": ["Negro / Rosa"]},
                 {"name": "Talla", "options": ["37"]},
                 {"name": "Material", "options": ["Sintético", "Textil"]}]
    CAMPOS = {"titulo": "", "descripcion": "", "atributos": [
        {"nombre": "Género", "valor": "Sin género"}, {"nombre": "Vacío", "valor": "  "}]}
    ESPERADO = {
        "wc_id": 10, "sku": "INV-0001-NEG/ROS-37",
        "title": "Tenis de prueba - Negro / Rosa, 37", "price": 499.0, "description": "Corto",
        "images": ["https://ejemplo.invalid/a.jpg"],
        "weight": "0.6", "length": "30", "width": "20", "height": "12",
        "stock": 7, "sin_inventario_woo": False,
        "ml_category_id": "MLM900001", "ml_category_name": "Calzado > Tenis",
        "wc_categories": [],
        "ml_attrs": {"GENDER": "Mujer", "COLOR": "Gris"},
        "wc_attrs": {"pa_color": "Negro / Rosa", "talla": "37", "material": "Sintético",
                     "género": "Sin género"},
        "tags": [{"name": "tenis"}],
        "meta": META,
        "attributes": ATRIBUTOS,
        "regular_price": 499.0, "name": "Tenis de prueba - Negro / Rosa, 37",
        "short_description": "Corto", "stock_quantity": 7,
        "dimensions": {"length": "30", "width": "20", "height": "12"},
    }

    def _construir(self, padre=500, meta=None, heredadas=None, campos=None):
        self.meta = copy.deepcopy(self.META if meta is None else meta)
        self.atributos = copy.deepcopy(self.ATRIBUTOS)
        self.campos = copy.deepcopy(self.CAMPOS if campos is None else campos)
        self.metas_del_padre = mock.Mock(return_value={"_sku": " INV-0001 "})
        wp = PR.wp_db
        with mock.patch.object(wp, "producto_wp", return_value={
                "post_title": "Tenis de prueba - Negro / Rosa, 37", "post_content": "",
                "post_excerpt": "<p>Corto</p>"}), \
                mock.patch.object(wp, "postmeta_con_herencia", return_value=(
                    self.meta, set(self.HEREDADAS if heredadas is None else heredadas))), \
                mock.patch.object(wp, "atributos_wc", return_value=self.atributos), \
                mock.patch.object(wp, "padre_de", return_value=padre), \
                mock.patch.object(wp, "postmeta", self.metas_del_padre), \
                mock.patch.object(wp, "precio_regular_variantes", return_value=None), \
                mock.patch.object(wp, "categorias_wc", return_value=[]), \
                mock.patch.object(wp, "tags_wc", return_value=[{"name": "tenis"}]), \
                mock.patch.object(PR, "_imagenes_para", return_value=["https://ejemplo.invalid/a.jpg"]):
            return PR.construir_prod("INV-0001-NEG/ROS-37", 10, self.campos)

    def test_solo_agrega_llaves_y_el_resto_del_dict_es_identico(self):
        p = self._construir()
        self.assertEqual(set(p) - set(self.ESPERADO), LLAVES_NUEVAS)
        self.assertEqual({k: v for k, v in p.items() if k not in LLAVES_NUEVAS}, self.ESPERADO)
        # …y en el mismo orden, con las nuevas al FINAL.
        self.assertEqual([k for k in p if k != "sin_inventario_libro"],   # esa la puso main en medio
                         [*self.ESPERADO, "padre_wc_id", "padre_sku", "ejes_propios", "atributos_panel"])

    def test_las_cuatro_llaves_nuevas(self):
        p = self._construir()
        self.assertEqual(p["padre_wc_id"], 500)
        # El SKU del padre, leído por su id (UNA lectura): lo mira `FAMILIAS_EN_REVISION`.
        self.assertEqual(p["padre_sku"], "INV-0001")
        self.metas_del_padre.assert_called_once_with(500, ["_sku"])
        # Los `attribute_*` que la variación tiene CON valor; los heredados del padre, no.
        self.assertEqual(p["ejes_propios"], {"pa_color": "negro-rosa", "talla": "37"})
        self.assertEqual(p["atributos_panel"], {"género": "Sin género"})
        self.assertEqual(G.ejes_propios(p), ["color", "talla"])
        self.assertTrue(G.es_variacion(p))
        self.assertEqual(G.talla_de(p), ("37", "ok", []))
        self.assertEqual(G.color_de(p)["valor"], "Negro / Rosa")
        # El formulario del Estudio es rango 2 (WooCommerce no trae «Género»); la meta, 3.
        self.assertEqual(G.fuentes_de_genero(p), [(2, ["Sin género"]), (3, ["Mujer"])])

    def test_un_producto_simple_no_tiene_padre_ni_ejes(self):
        simple = {k: v for k, v in self.META.items() if not k.startswith("attribute_")}
        p = self._construir(padre=None, meta=simple, heredadas=set(), campos={})
        self.assertEqual((p["padre_wc_id"], p["padre_sku"], p["ejes_propios"], p["atributos_panel"]),
                         (None, "", {}, {}))
        self.metas_del_padre.assert_not_called()           # sin padre no hay lectura de más
        self.assertFalse(G.es_variacion(p))

    def test_no_muta_lo_que_recibe_ni_comparte_los_dicts_nuevos(self):
        p = self._construir()
        self.assertEqual(self.meta, self.META)             # ni la meta…
        self.assertEqual(self.atributos, self.ATRIBUTOS)   # …ni los atributos…
        self.assertEqual(self.campos, self.CAMPOS)         # …ni lo que mandó el panel
        self.assertIsNot(p["ejes_propios"], p["meta"])
        self.assertIsNot(p["atributos_panel"], p["wc_attrs"])

    def test_el_dict_sigue_siendo_serializable(self):
        json.dumps(self._construir(), ensure_ascii=False)


# ══════════════════════════ el alta real (`crear_ml`) ════════════════════════

class Alta(Base):
    def test_cada_cuenta_sale_con_su_guia_y_su_talla(self):
        r = self.crear(prod())
        self.assertTrue(r["ok"])
        self.assertEqual(sorted(r), ["canal", "modo", "ok", "registrado_en", "resultados"])
        por_token = dict(self.posts)
        self.assertEqual(_attr(por_token["tok-BEKURA"], "SIZE_GRID_ROW_ID")[0]["value_name"], "9000001:2")
        self.assertEqual(_attr(por_token["tok-BEKURA"], "SIZE"), [{"id": "SIZE", "value_name": "24 MX"}])
        self.assertEqual(_attr(por_token["tok-BEKURA"], "SIZE_GRID_ID"),
                         [{"id": "SIZE_GRID_ID", "value_name": "9000001"}])       # nunca value_id
        self.assertEqual(_attr(por_token["tok-BEKURA"], "BRAND"), [{"id": "BRAND", "value_name": "Ferrahome"}])
        self.assertEqual(_attr(por_token["tok-SANCORFASHION"], "SIZE_GRID_ID")[0]["value_name"], "9000002")
        self.assertEqual(_attr(por_token["tok-SANCORFASHION"], "SIZE"), [{"id": "SIZE", "value_name": "23 MX"}])

    def test_el_color_de_la_variante_es_el_que_llega_a_ml(self):
        self.crear(prod(color="Negro / Rosa", meta_color="Gris"))
        for _, payload in self.posts:
            self.assertEqual(_attr(payload, "COLOR"), [{"id": "COLOR", "value_name": "Negro / Rosa"}])

    def test_ni_la_vista_previa_ni_el_alta_mutan_el_prod_que_comparten_las_dos_cuentas(self):
        """`ml_attrs`, `wc_attrs` y `meta` son los MISMOS dicts para BEKURA y SANCORFASHION
        (el alta le pasa al vendor una copia superficial): si la primera cuenta los tocara,
        la segunda publicaría otra cosa."""
        p = prod(color="Negro / Rosa", meta_color="Gris", panel={"Género": "Mujer"})
        p["ml_attrs"]["MAIN_COLOR"] = "Gris"
        p["meta"] = {"_barcode": "", "ml_attr_COLOR": "Gris"}
        antes = copy.deepcopy(p)
        self.preview(p, "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual(p, antes)
        self.assertTrue(self.crear(p)["ok"])
        self.assertEqual(p, antes)
        self.assertEqual(len(self.posts), 2)

    def test_lo_que_ensena_la_vista_previa_es_lo_que_sale_en_el_alta(self):
        p = prod(color="Negro / Rosa", meta_color="Gris")
        p["ml_attrs"]["MAIN_COLOR"] = "Gris"
        vista = self.preview(p, "BEKURA", ["BEKURA", "SANCORFASHION"])["payload"]
        self.crear(p, ["BEKURA"])

        def los_de_la_guia(payload):
            return sorted((a for a in payload["attributes"]
                           if a["id"] in G.ATRIBUTOS_QUE_PISA + G.ATRIBUTOS_QUE_AJUSTA),
                          key=lambda a: a["id"])
        self.assertEqual(los_de_la_guia(vista), los_de_la_guia(self.posts[0][1]))
        self.assertEqual(len(los_de_la_guia(vista)), 6)          # MAIN_COLOR «Gris» se quitó

    def test_la_resolucion_no_corre_en_el_event_loop(self):
        self.crear(prod())
        self.assertEqual(sorted(self.resueltas), [("BEKURA", False), ("SANCORFASHION", False)])

    def test_dominio_sin_guia_manda_lo_mismo_que_el_vendor_y_no_instala_la_guardia(self):
        p = prod(cat=CAT_TALADRO, talla=None, genero=None)
        esperado = json.dumps(VENDOR_BUILD(dict(p), "tok-BEKURA", False, "BEKURA"), sort_keys=True)
        r = self.crear(p, ["BEKURA"])
        self.assertTrue(r["ok"])
        self.assertEqual(sorted(r), ["canal", "modo", "ok", "registrado_en", "resultados"])
        self.assertEqual(sorted(r["resultados"][0]), ["cuenta", "error", "estado_ml", "explicacion",
                                                      "intentos", "item_id", "ml_status", "ok", "pausado",
                                                      "url"])
        self.assertEqual(json.dumps(self.posts[0][1], sort_keys=True), esperado)
        self.assertIsNot(ml_api.create_item, PR._create_item_con_guia)      # noqa: SLF001 — ni la guardia
        self.assertIs(PR._create_item_original, self.create_item_de_prueba)  # noqa: SLF001 — el vendor, tras el anotador

    def test_fuera_de_los_dominios_con_guia_los_mensajes_y_los_reintentos_son_los_de_siempre(self):
        desconocido = _rechazo(_causa("item.price.invalid", message="price too low"))
        self.respuestas = [desconocido, desconocido, desconocido]
        fila = self.crear(prod(cat=CAT_TALADRO, talla=None, genero=None), ["BEKURA"])["resultados"][0]
        # Desde v0.682.0 (main) un 400 con causas es VALIDACIÓN: no se repite y se explica.
        self.assertEqual((fila["intentos"], len(self.posts)), (1, 1))
        self.assertIn("price too low", fila["error"])

    def test_una_cuenta_bloqueada_no_frena_a_la_otra_ni_toca_a_ml(self):
        r = self.crear(prod(genero="Hombre", talla="41"))        # Hombre: solo BEKURA tiene guía
        filas = {f["cuenta"]: f for f in r["resultados"]}
        self.assertTrue(filas["BEKURA"]["ok"])
        self.assertEqual((filas["SANCORFASHION"]["ok"], filas["SANCORFASHION"]["intentos"]), (False, 0))
        self.assertIn("no hay guía registrada para SANCORFASHION · SNEAKERS · Hombre",
                      filas["SANCORFASHION"]["error"])
        self.assertEqual([token for token, _ in self.posts], ["tok-BEKURA"])

    def test_alta_parcial_es_ok_con_aviso_y_el_modal_solo_ofrece_cerrar(self):
        """Con `ok: False` el modal dejaba «Confirmar» vivo: el segundo clic era un UPDATE
        de la publicación recién creada (sin compuertas) o un duplicado, y la bitácora de
        persona contaba como «fallido» un alta que sí creó una publicación."""
        r = self.crear(prod(genero="Hombre", talla="41"))
        self.assertEqual((r["ok"], r["parcial"]), (True, True))
        aviso = G.MSG_PARCIAL_HECHA.format(listas="BEKURA", faltaron="SANCORFASHION")
        self.assertEqual(r["avisos"], [aviso])
        filas = {f["cuenta"]: f for f in r["resultados"]}
        self.assertEqual(filas["SANCORFASHION"]["aviso"], aviso)     # el modal lo pinta en su renglón
        self.assertNotIn("aviso", filas["BEKURA"])
        from routers import publicar as ruta
        estado, detalle = ruta._leer_resultado(r)                    # noqa: SLF001 — bitácora de persona
        self.assertEqual(estado, "ok")
        self.assertEqual([(f["cuenta"], f["ok"]) for f in detalle["resultados"]],
                         [("SANCORFASHION", False), ("BEKURA", True)])

    def test_no_es_parcial_si_ninguna_se_creo_o_si_una_fallo_en_ml(self):
        nada = self.crear(prod(genero=None))                         # las dos frenadas: nada se creó
        self.assertFalse(nada["ok"])
        self.assertNotIn("parcial", nada)
        self.respuestas = [_rechazo(_causa("item.price.invalid"))]   # una se intentó y ML la rechazó
        fallo = self.crear(prod(genero="Hombre", talla="41"))
        self.assertFalse(fallo["ok"])
        self.assertNotIn("parcial", fallo)
        with mock.patch.object(meli, "_access_token", side_effect=lambda c=None: None if c == "SANCORFASHION" else "t"):
            sin_token = self.crear(prod())                           # sin token NO es «anunciada»
        self.assertFalse(sin_token["ok"])
        self.assertNotIn("parcial", sin_token)
        # Una creada + una frenada por la guía + una que falló por OTRA cosa: no es parcial.
        with mock.patch.object(meli, "_access_token", side_effect=lambda c=None: None if c == "TERCERA" else f"tok-{c}"):
            mezcla = self.crear(prod(genero="Hombre", talla="41"), ["SANCORFASHION", "BEKURA", "TERCERA"])
        self.assertEqual([f["ok"] for f in mezcla["resultados"]], [False, True, False])
        self.assertFalse(mezcla["ok"])
        self.assertNotIn("parcial", mezcla)

    def test_sin_genero_no_sale_aunque_el_mapper_rellenaria_mujer(self):
        r = self.crear(prod(genero=None))
        self.assertEqual(self.posts, [])
        self.assertTrue(all("falta el GÉNERO" in f["error"] for f in r["resultados"]))
        ml_api.preupload_picture.assert_not_called()             # ni una imagen subida

    def test_sin_talla_o_color_propios_no_sale(self):
        """Nunca se publica con talla, género o color que no salgan de una fuente del producto."""
        casos = (prod(talla="40", ejes={"variante": "NEG-42", "color": "negro"}),     # talla del padre
                 prod(color=["Negro", "Rosa"], ejes={"talla": "37"}),                 # color del padre
                 prod(color=["Negro", "Rosa"], ejes={"talla": "37"},                  # …sembrado en el
                      panel={"Color": "Negro"}),                                      # formulario del Estudio
                 prod(color=None, meta_color="Gris"),                                 # solo la meta heredada
                 prod(color=None, meta_color=None, panel={"Color": "Negro"}),         # solo el formulario
                 prod(color=["Negro", "Rosa"], ejes={"talla": "37"},                  # el formulario con algo
                      panel={"Color": "Rosa pastel"}),                                # que Woo no lista
                 prod(genero=None),                                                   # sin género
                 prod(genero=None, genero_woo=["Mujer", "Hombre"]),                   # Woo no dice cuál
                 prod(genero="Unisex"))                                               # no es de ML
        for p in casos:
            self.posts.clear()
            r = self.crear(p)
            self.assertEqual(self.posts, [])
            self.assertTrue(all(not f["ok"] and f["intentos"] == 0 for f in r["resultados"]))
        ml_api.preupload_picture.assert_not_called()

    def test_ropa_sin_guia_no_sale_y_dice_cual_falta(self):
        r = self.crear(prod(cat=CAT_ROPA, talla="M", genero="Hombre"), ["BEKURA"])
        self.assertEqual(self.posts, [])
        self.assertIn("BEKURA · JACKETS_AND_COATS · Hombre", r["resultados"][0]["error"])

    def test_si_la_guia_no_se_puede_leer_no_se_publica_sin_ella(self):
        """Ni con 5xx, ni sin red, ni con el token vencido: en un dominio que exige guía,
        si ML o la red fallan al leerla NO se publica."""
        fallos = (mock.Mock(status_code=503), mock.Mock(status_code=401), OSError("sin red"),
                  mock.Mock(status_code=200, json=mock.Mock(return_value={"no": "es una guía"})))
        for fallo in fallos:
            G._guias.clear()                                     # noqa: SLF001
            self.posts.clear()
            patch = (mock.patch.object(G.requests, "get", side_effect=fallo) if isinstance(fallo, Exception)
                     else mock.patch.object(G.requests, "get", return_value=fallo))
            with patch, self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
                r = self.crear(prod(), ["BEKURA"])
            self.assertEqual(self.posts, [])
            self.assertIn("no se pudo leer la guía 9000001", r["resultados"][0]["error"])
            self.assertEqual(r["resultados"][0]["intentos"], 0)
        ml_api.preupload_picture.assert_not_called()

    def test_categoria_ilegible_el_alta_sale_como_siempre_y_el_error_lo_dice(self):
        lecturas = {"n": 0}

        def info(c, t):
            lecturas["n"] += 1                    # falla SOLO la primera: la del resolvedor
            return {} if lecturas["n"] == 1 else copy.deepcopy(CATS[c])
        self.respuestas = [_rechazo(_causa("invalid.fashion_grid.grid_id.values")),
                           _rechazo(_causa("missing.fashion_grid.grid_id.values"))]
        with mock.patch.object(ml_api, "get_category_info", side_effect=info), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            r = self.crear(prod(), ["BEKURA"])
        fila = r["resultados"][0]
        self.assertEqual((fila["ok"], fila["intentos"]), (False, 1))
        self.assertIn("no se pudo leer la categoría MLM900001", fila["error"])
        self.assertEqual(_attr(self.posts[0][1], "SIZE"), [{"id": "SIZE", "value_id": "s37"}])   # payload de hoy

    def test_un_rechazo_de_guia_que_el_vendor_no_conoce_no_se_reintenta_y_se_explica(self):
        self.respuestas = [_rechazo(_causa("invalid.fashion_grid.size.values", message="Attribute [SIZE] is not valid"),
                                    _causa("shipping.lost_me1_by_user", "warning"))]
        fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["ok"], fila["intentos"], len(self.posts)), (False, 1, 1))
        self.assertIn("GUIA_DE_TALLAS_RECHAZADA (invalid.fashion_grid.size.values)", fila["error"])
        self.assertIn("guía 9000001, renglón 9000001:2, SIZE «24 MX», GENDER «Mujer», COLOR «Negro»",
                      fila["error"])
        self.assertNotIn("ME1", fila["error"])
        self.assertNotIn("ME2", fila["error"])

    def test_con_guia_un_400_con_causa_de_error_no_se_repite_tres_veces(self):
        self.respuestas = [_rechazo(_causa("item.attribute.invalid", message="Attribute [MAIN_COLOR] is not valid"))]
        fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["intentos"], len(self.posts)), (1, 1))
        self.assertEqual(fila["error"], "Mercado Libre rechazó el alta — item.attribute.invalid: "
                                        "Attribute [MAIN_COLOR] is not valid")

    def test_payload_sin_family_name_no_se_envia_y_se_reintenta_una_sola_vez(self):
        """Con el token vencido y la guía en caché, cada intento repetía los pre-uploads
        (con 10 fotos, 30) para fallar igual. Ahora: un reintento, no dos."""
        lecturas = {"n": 0}

        def info(c, t):
            lecturas["n"] += 1                    # el resolvedor lee bien; el vendor ya no
            return copy.deepcopy(CATS[c]) if lecturas["n"] == 1 else {}
        with mock.patch.object(ml_api, "get_category_info", side_effect=info), \
                self.assertLogs("omnicanal.publicar_ready", "WARNING"):
            fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["ok"], fila["intentos"], self.posts), (False, 2, []))
        self.assertIn("sin family_name", fila["error"])
        self.assertEqual(ml_api.preupload_picture.call_count, 2)

    def test_un_error_determinista_de_la_guia_no_se_reintenta(self):
        """Si la guía resuelta es de otra cuenta o de otra categoría, otro intento da lo mismo."""
        for mensaje, codigo in ((G.ERR_OTRA_CUENTA, "otra_cuenta"), (G.ERR_OTRA_CATEGORIA, "otra_categoria")):
            llamadas = mock.Mock(side_effect=G.GuiaTallasError(mensaje.format(esperada="A", recibida="B"), codigo))
            with mock.patch.object(PR, "_publicar_con_guia", llamadas):
                fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
            self.assertEqual((fila["ok"], fila["intentos"], llamadas.call_count), (False, 1, 1))
            self.assertIn("No se envía", fila["error"])
        self.assertEqual(self.posts, [])


# ═══════════ candado de pausa: una alta de moda ACTIVA frena las que siguen ══

class CandadoDePausa(Base):
    def _estado(self, status):
        """`_estado_item` de mentira, que además anota en qué hilo corrió (regla 11)."""
        self.lecturas: list[tuple[str, str, bool]] = []

        def estado(item_id, token):
            self.lecturas.append((item_id, token, threading.current_thread() is threading.main_thread()))
            return status, []
        return mock.patch.object(PR, "_estado_item", side_effect=estado)

    def _dejar_una_activa(self):
        with mock.patch.object(PR, "asegurar_pausado", return_value=dict(NO_PAUSADA)), \
                self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            r = self.crear(prod())
        self.posts.clear()
        return r

    def test_si_una_alta_con_guia_queda_activa_no_se_sigue_con_la_otra_cuenta(self):
        r = self._dejar_una_activa()
        primera, segunda = r["resultados"]                      # el orden del vendor: SANCORFASHION, BEKURA
        self.assertEqual((primera["cuenta"], primera["ok"], primera["pausado"]), ("SANCORFASHION", True, False))
        self.assertIn("páusala a mano", primera["aviso"])
        self.assertEqual((segunda["cuenta"], segunda["ok"], segunda["intentos"]), ("BEKURA", False, 0))
        self.assertEqual(segunda["error"], _candado(("MLM1", "SANCORFASHION", "active")))
        # El mensaje dice el estado REAL que se leyó, no un «ACTIVA» fijo.
        self.assertIn("se creó en Mercado Libre y NO quedó pausado: MLM1 en SANCORFASHION (su estado "
                      "en Mercado Libre es «active»). No se crea nada más de moda hasta revisarlo",
                      segunda["error"])
        self.assertEqual((r["ok"], r["parcial"]), (True, True))  # una se creó: el modal ofrece «Cerrar»

    def test_las_siguientes_altas_con_guia_se_bloquean_en_el_pre_vuelo(self):
        self._dejar_una_activa()
        otro = prod()
        otro["sku"] = "INV-0002-NEG-37"
        with self._estado("active"):
            r = self.crear(otro)
        self.assertEqual(self.posts, [])
        ml_api.preupload_picture.reset_mock()
        self.assertTrue(all(f["error"] == _candado(("MLM1", "SANCORFASHION", "active"))
                            and f["intentos"] == 0 for f in r["resultados"]))
        self.assertFalse(r["ok"])
        # UNA lectura por publicación y por llamada, con el token de la cuenta dueña, fuera del event loop.
        self.assertEqual(self.lecturas, [("MLM1", "tok-SANCORFASHION", False)])

    def test_el_candado_se_suelta_solo_cuando_la_publicacion_ya_esta_pausada(self):
        self._dejar_una_activa()
        with self._estado(None):                                 # no se pudo leer: se queda (falla cerrado)
            r = self.crear(prod())
        self.assertFalse(r["ok"])
        self.assertEqual(self.posts, [])
        # …y el mensaje dice lo que de verdad se sabe: que NO se pudo leer.
        self.assertTrue(all(f["error"] == _candado(("MLM1", "SANCORFASHION", None)) for f in r["resultados"]))
        self.assertIn("MLM1 en SANCORFASHION (no se pudo leer su estado en Mercado Libre)",
                      r["resultados"][0]["error"])
        with self._estado("paused"), self.assertLogs("omnicanal.publicar_ready", "INFO"):
            r = self.crear(prod())
        self.assertTrue(r["ok"])
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(len(self.lecturas), 1)
        with self._estado("active"):                             # ya suelto: ni se vuelve a leer
            self.assertTrue(self.crear(prod())["ok"])
        self.assertEqual(self.lecturas, [])

    def test_que_estados_de_ml_sueltan_el_candado(self):
        """Cualquiera que NO sea `active`: pausada, cerrada, en revisión, inactiva… ya no
        está vendiendo sin que nadie la vea. (Antes solo `paused` y `closed`: una
        publicación que ML dejara `under_review` frenaba TODA la moda hasta reiniciar el
        backend, con un mensaje que pedía pausar algo que no se puede pausar.) Sin
        lectura, o si sigue `active`, se queda."""
        for status, suelta in (("active", False), (None, False), ("", False),
                               ("paused", True), ("closed", True), ("under_review", True),
                               ("inactive", True), ("not_yet_active", True), ("payment_required", True)):
            with self.subTest(status=status):
                PR._soltar_candado_moda()                                       # noqa: SLF001
                self._dejar_una_activa()
                with self._estado(status):
                    libre = asyncio.run(PR._candado_moda()) is None             # noqa: SLF001
                self.assertEqual(libre, suelta)
                self.assertEqual(PR._MODA_ACTIVA == {}, suelta)                 # noqa: SLF001
                with self._estado(status):                       # y el alta siguiente hace lo mismo
                    self.assertEqual(self.crear(prod())["ok"], suelta)
                self.posts.clear()

    def test_si_leer_el_token_o_el_estado_revienta_el_candado_se_queda(self):
        """Falla CERRADO también con una EXCEPCIÓN, no solo con token o estado `None`:
        hoy `access_token_async` y `_estado_item` se tragan sus errores, pero el `except`
        de `_candado_moda` es lo único que lo garantiza el día que dejen de hacerlo."""
        self._dejar_una_activa()
        revienta = mock.AsyncMock(side_effect=RuntimeError("kubera caída"))
        with mock.patch.object(meli, "access_token_async", revienta), self._estado("paused"), \
                self.assertLogs("omnicanal.publicar_ready", "WARNING"):
            self.assertFalse(self.crear(prod())["ok"])
        self.assertEqual((self.posts, self.lecturas), ([], []))
        with mock.patch.object(PR, "_estado_item", side_effect=OSError("sin red")), \
                self.assertLogs("omnicanal.publicar_ready", "WARNING"):
            self.assertFalse(self.crear(prod())["ok"])
            vista = self.preview(prod(), "BEKURA", ["BEKURA"])
        self.assertEqual((vista["ok"], vista["bloqueo_guia"]), (False, True))
        self.assertEqual(self.posts, [])
        self.assertEqual(list(PR._MODA_ACTIVA), ["MLM1"])                       # noqa: SLF001

        async def solo_la_duena_revienta(c=None):      # el candado lo puso SANCORFASHION
            if c == "SANCORFASHION":
                raise RuntimeError("kubera caída")
            return f"tok-{c}"
        with mock.patch.object(meli, "access_token_async", solo_la_duena_revienta), \
                self.assertLogs("omnicanal.publicar_ready", "WARNING"):
            vista = self.preview(prod(), "BEKURA", ["BEKURA"])
        self.assertEqual((vista["ok"], vista["bloqueo_guia"]), (False, True))

    def test_el_candado_es_un_conjunto_pausar_la_ultima_no_suelta_a_la_primera(self):
        """Dos personas publican moda a la vez y las dos quedan sin pausar. Con UNA casilla
        solo se recordaba la última: al pausarla el candado se soltaba con la primera
        todavía activa."""
        with self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            PR._poner_candado_moda("MLM-A", "BEKURA", "INV-0001", "active")            # noqa: SLF001
            motivo = PR._poner_candado_moda("MLM-B", "SANCORFASHION", "INV-0002", None)   # noqa: SLF001
        self.assertEqual(motivo, _candado(("MLM-A", "BEKURA", "active"), ("MLM-B", "SANCORFASHION", None)))
        estados = {"MLM-A": "active", "MLM-B": "paused"}
        leidas = []

        def estado(item_id, token):
            leidas.append((item_id, token))
            return estados[item_id], []
        with mock.patch.object(PR, "_estado_item", side_effect=estado), \
                self.assertLogs("omnicanal.publicar_ready", "INFO"):
            r = self.crear(prod())                               # se pausó la ÚLTIMA: sigue frenado
            self.assertEqual(self.posts, [])
            self.assertTrue(all(f["error"] == _candado(("MLM-A", "BEKURA", "active"))
                                for f in r["resultados"]))
            self.assertEqual(sorted(leidas), [("MLM-A", "tok-BEKURA"), ("MLM-B", "tok-SANCORFASHION")])
            self.assertEqual(list(PR._MODA_ACTIVA), ["MLM-A"])                  # noqa: SLF001 — B ya se soltó
            leidas.clear()
            self.assertFalse(self.crear(prod())["ok"])           # B ya no se vuelve a leer
            self.assertEqual(leidas, [("MLM-A", "tok-BEKURA")])
            estados["MLM-A"] = "under_review"                    # ya no está `active`
            self.assertTrue(self.crear(prod())["ok"])
        self.assertEqual(PR._MODA_ACTIVA, {})                                   # noqa: SLF001
        self.assertEqual(len(self.posts), 2)

    def test_si_la_verificacion_de_pausa_revienta_cuenta_como_no_pausada(self):
        """`ml_api.pause_item` solo atrapa Timeout: con un ConnectionError a media
        verificación `crear_ml` reventaba con la publicación YA creada, y el candado se
        quedaba sin poner (la siguiente alta de moda salía con una quizá activa)."""
        with mock.patch.object(PR, "asegurar_pausado", side_effect=ConnectionError("se cayó la red")), \
                self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            r = self.crear(prod())                               # no lanza
        primera, segunda = r["resultados"]
        self.assertEqual((primera["cuenta"], primera["ok"], primera["item_id"], primera["pausado"],
                          primera["estado_ml"]), ("SANCORFASHION", True, "MLM1", False, None))
        self.assertIn("quedó en estado desconocido: páusala a mano en Mercado Libre (MLM1)", primera["aviso"])
        PR._anotar_pausa_backlog.assert_called_once()                           # noqa: SLF001
        self.assertEqual((segunda["cuenta"], segunda["ok"], segunda["intentos"]), ("BEKURA", False, 0))
        self.assertEqual(segunda["error"], _candado(("MLM1", "SANCORFASHION", None)))
        self.assertEqual(len(self.posts), 1)                     # no siguió con la otra cuenta
        self.assertEqual(list(PR._MODA_ACTIVA), ["MLM1"])                       # noqa: SLF001
        self.assertEqual((r["ok"], r["parcial"]), (True, True))
        self.posts.clear()
        with self._estado("active"):                             # y la siguiente alta de moda se frena
            self.assertFalse(self.crear(prod())["ok"])
        self.assertEqual(self.posts, [])

    def test_fuera_de_moda_una_excepcion_al_pausar_sigue_como_siempre(self):
        """El `try` es SOLO de las altas con guía: un taladro revienta como antes, sin
        candado."""
        taladro = prod(cat=CAT_TALADRO, talla=None, genero=None)
        with mock.patch.object(PR, "asegurar_pausado", side_effect=ConnectionError("se cayó la red")):
            with self.assertRaises(ConnectionError):
                self.crear(taladro)
        self.assertEqual(PR._MODA_ACTIVA, {})                                   # noqa: SLF001

    # ── la pausa del VENDOR (la primera, dentro de `publish_product`) ────────────
    def test_si_la_pausa_del_vendor_revienta_no_se_crea_otra_publicacion(self):
        """La PRIMERA pausa tras crear la hace el vendor (publisher_core.py:679) y
        `ml_api.pause_item` solo atrapa Timeout. Con un ConnectionError ahí `publish_product`
        lanzaba con la publicación YA creada y el bucle de reintentos de `crear_ml` hacía
        OTRO `POST /items`: con la red caída, tres publicaciones de la misma talla, activas,
        sin fila en `ml_backlog` y sin candado (medido el 9-oct-2026)."""
        cae = ConnectionError("se cayó la red")
        with mock.patch.object(ml_api, "pause_item", side_effect=cae), \
                mock.patch.object(ml_api, "update_description", side_effect=cae), \
                mock.patch.object(PR, "asegurar_pausado", side_effect=cae), \
                self.assertLogs("omnicanal.publicar_ready", "ERROR") as logs:
            r = self.crear(prod(), ["BEKURA"])                   # no lanza
        fila = r["resultados"][0]
        self.assertEqual(len(self.posts), 1)                     # antes: 3
        self.assertEqual((fila["ok"], fila["item_id"], fila["intentos"], fila["ml_status"],
                          fila["pausado"], fila["estado_ml"]), (True, "MLM1", 1, 201, False, None))
        self.assertEqual(fila["url"], "https://articulo.mercadolibre.com.mx/MLM-1")
        self.assertTrue(any("Mercado Libre YA creó MLM1" in m and "no se reintenta el alta" in m
                            for m in logs.output))
        # La pausa no se da por hecha: se verifica como siempre y, si no queda, hay candado.
        self.assertEqual(list(PR._MODA_ACTIVA), ["MLM1"])                       # noqa: SLF001
        self.assertEqual(fila["aviso"],
                         "La publicación se creó pero quedó en estado desconocido: páusala a mano en "
                         "Mercado Libre (MLM1). El publicador se interrumpió después de crearla "
                         "(ConnectionError) y no se pudo subir su descripción: revísala en Mercado "
                         "Libre (MLM1).")
        # La fila de `ml_backlog` / `ml_progress` que el vendor no llegó a escribir: sin ella
        # el panel no sabe que la publicación existe.
        PR._backlog_ml.assert_called_once()                                     # noqa: SLF001
        clave, entrada = PR._backlog_ml.call_args.args                          # noqa: SLF001
        self.assertEqual(clave, "BEKURA:INV-0001-NEG-37")
        self.assertEqual((entrada["cuenta"], entrada["wc_id"], entrada["ml_item_id"], entrada["ml_url"],
                          entrada["ml_status"], entrada["desc_status"], entrada["pics_preuploaded"]),
                         ("BEKURA", 1, "MLM1", fila["url"], 201, None, 1))
        self.assertEqual((entrada["result"]["success"], entrada["result"]["ml_item_id"],
                          entrada["result"].get("error")), (True, "MLM1", None))
        self.assertEqual(entrada["ml_response"], {"id": "MLM1"})
        self.assertEqual(G.huella(entrada["payload"]), G.huella(self.posts[0][1]))   # el que SALIÓ
        self.posts.clear()
        with self._estado("active"):                             # y la siguiente alta de moda se frena
            self.assertFalse(self.crear(prod())["ok"])
        self.assertEqual(self.posts, [])

    def test_si_la_red_se_cae_una_sola_vez_el_alta_termina_completa(self):
        """La red se cae SOLO en la pausa del vendor (o solo en su descripción): antes eran
        dos publicaciones, la primera huérfana —sin pausar, sin bitácora— y el modal decía
        éxito con la segunda. Ahora es una, con su descripción, pausada y anotada."""
        cae = ConnectionError("se cayó la red")
        for nombre, falla, llamadas in (("pause_item", cae, 1), ("update_description", [cae, 200], 2)):
            with self.subTest(revienta=nombre):
                self.posts.clear()
                PR._backlog_ml.reset_mock()                                     # noqa: SLF001
                ml_api.update_description.reset_mock()
                with mock.patch.object(ml_api, nombre, side_effect=falla) as doble, \
                        self.assertLogs("omnicanal.publicar_ready", "ERROR"):
                    r = self.crear(prod(), ["BEKURA"])
                    descripcion = doble if nombre == "update_description" else ml_api.update_description
                    self.assertEqual(descripcion.call_count, llamadas)
                    descripcion.assert_called_with("MLM1", "x", "tok-BEKURA")   # la que faltaba
                fila = r["resultados"][0]
                self.assertEqual(len(self.posts), 1)             # antes: 2
                self.assertEqual((r["ok"], fila["ok"], fila["item_id"], fila["intentos"], fila["pausado"]),
                                 (True, True, "MLM1", 1, True))
                self.assertNotIn("aviso", fila)                  # quedó como la habría dejado el vendor
                PR._backlog_ml.assert_called_once()                             # noqa: SLF001
                self.assertEqual(PR._backlog_ml.call_args.args[1]["desc_status"], 200)   # noqa: SLF001
                self.assertEqual(PR._MODA_ACTIVA, {})                           # noqa: SLF001

    def test_si_la_descripcion_no_sube_se_dice_aunque_la_publicacion_quede_pausada(self):
        """Quedó pausada (no hay candado ni aviso de pausa), pero sin descripción: la fila lo
        dice, porque quien la active después la activaría así."""
        with mock.patch.object(ml_api, "update_description", side_effect=ConnectionError("se cayó la red")), \
                self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["ok"], fila["item_id"], fila["pausado"], len(self.posts)),
                         (True, "MLM1", True, 1))
        self.assertEqual(fila["aviso"], "El publicador se interrumpió después de crearla (ConnectionError) "
                                        "y no se pudo subir su descripción: revísala en Mercado Libre (MLM1).")
        self.assertIsNone(PR._backlog_ml.call_args.args[1]["desc_status"])      # noqa: SLF001
        self.assertEqual(PR._MODA_ACTIVA, {})                                   # noqa: SLF001
        # Un producto SIN descripción no tiene nada que subir: ni se intenta ni se avisa.
        self.posts.clear()
        sin = prod()
        sin["description"] = ""
        with mock.patch.object(ml_api, "pause_item", side_effect=ConnectionError("se cayó la red")), \
                mock.patch.object(ml_api, "update_description") as descripcion, \
                self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            fila = self.crear(sin, ["BEKURA"])["resultados"][0]
        descripcion.assert_not_called()
        self.assertEqual((fila["ok"], len(self.posts)), (True, 1))
        self.assertNotIn("aviso", fila)

    def test_solo_se_rescata_lo_que_mercado_libre_ya_creo(self):
        """Si el vendor lanza SIN publicación creada (aquí: tras un 400 de GTIN, al buscar el
        código de barras), nada cambia: la excepción llega a `crear_ml`, que reintenta. Creada
        es lo mismo que para el vendor: un 201; un `id` en el cuerpo de un rechazo no cuenta."""
        gtin = _rechazo(_causa("item.attribute.missing_conditional_required", message="GTIN is required"))
        gtin[0]["id"] = "NO-ES-UNA-PUBLICACION"
        self.respuestas = [gtin, gtin, gtin]
        with mock.patch.object(ml_api, "search_gtin_in_catalog", side_effect=ConnectionError("se cayó la red")), \
                self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["ok"], fila["item_id"], fila["intentos"], len(self.posts)), (False, "", 3, 3))
        self.assertEqual(fila["error"], "ConnectionError: se cayó la red")
        PR._backlog_ml.assert_not_called()                                      # noqa: SLF001
        PR.asegurar_pausado.assert_not_called()
        self.assertEqual(PR._MODA_ACTIVA, {})                                   # noqa: SLF001

    def test_fuera_de_moda_la_pausa_del_vendor_que_revienta_sigue_como_siempre(self):
        """El rescate es SOLO de las altas con guía: un dominio sin guía corre el vendor sin
        envoltorio, como siempre. Ahí el defecto es PREVIO a este fix (igual en v0.625.0) y
        sigue: está anotado en el manual (sección 11), no arreglado."""
        taladro = prod(cat=CAT_TALADRO, talla=None, genero=None)
        with mock.patch.object(ml_api, "pause_item", side_effect=ConnectionError("se cayó la red")), \
                self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            fila = self.crear(taladro, ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["ok"], fila["intentos"], len(self.posts)), (False, 3, 3))
        self.assertEqual(fila["error"], "ConnectionError: se cayó la red")
        PR._backlog_ml.assert_not_called()                                      # noqa: SLF001
        self.assertIsNot(ml_api.create_item, PR._create_item_con_guia)      # noqa: SLF001 — ni la guardia
        self.assertIs(PR._create_item_original, self.create_item_de_prueba)  # noqa: SLF001 — el vendor, tras el anotador

    def test_sin_token_de_la_cuenta_duena_el_candado_se_queda(self):
        self._dejar_una_activa()
        with mock.patch.object(meli, "access_token_async", mock.AsyncMock(return_value=None)), \
                self._estado("paused"):
            r = self.crear(prod())
        self.assertFalse(r["ok"])
        self.assertEqual((self.posts, self.lecturas), ([], []))

    def test_la_vista_previa_dice_que_el_candado_va_a_frenar(self):
        self._dejar_una_activa()
        with self._estado("active"):
            r = self.preview(prod(), "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual((r["ok"], r["bloqueo_guia"]), (False, True))
        self.assertEqual(r["motivo"], "No se puede publicar en Mercado Libre. "
                         + _candado(("MLM1", "SANCORFASHION", "active")))
        self.assertEqual(r["avisos"][-1], _candado(("MLM1", "SANCORFASHION", "active")))
        self.assertEqual([hilo for _, _, hilo in self.lecturas], [False])
        with self._estado("paused"), self.assertLogs("omnicanal.publicar_ready", "INFO"):
            self.assertTrue(self.preview(prod(), "BEKURA", ["BEKURA"])["ok"])

    def test_nada_de_esto_corre_fuera_de_moda(self):
        """Un alta sin guía ni pone ni mira el candado, quede como quede su pausa."""
        taladro = prod(cat=CAT_TALADRO, talla=None, genero=None)
        with mock.patch.object(PR, "asegurar_pausado", return_value=dict(NO_PAUSADA)):
            r = self.crear(taladro)
        self.assertTrue(r["ok"])
        self.assertEqual(len(self.posts), 2)                     # siguió con la segunda cuenta
        self.assertEqual(PR._MODA_ACTIVA, {})                    # noqa: SLF001
        self.posts.clear()
        self._dejar_una_activa()                                 # ahora SÍ hay candado de moda
        with self._estado("active"):
            self.assertTrue(self.crear(taladro)["ok"])           # y el taladro sale igual
            self.assertTrue(self.preview(taladro, "BEKURA", ["BEKURA"])["ok"])
        self.assertEqual((len(self.posts), self.lecturas), (2, []))

    def test_una_cuenta_bloqueada_por_la_guia_no_consulta_el_candado(self):
        self._dejar_una_activa()
        with self._estado("active"):
            r = self.crear(prod(genero=None))                    # se bloquea antes: no hay nada que crear
        self.assertEqual(self.lecturas, [])
        self.assertTrue(all("falta el GÉNERO" in f["error"] for f in r["resultados"]))


# ═════════════ la guardia de `ml_api.create_item` (reintentos del vendor) ════

class Guardia(Base):
    def test_sin_alta_con_guia_en_curso_es_el_create_item_del_vendor(self):
        PR._instalar_guardia()                                               # noqa: SLF001
        self.assertIs(ml_api.create_item, PR._create_item_con_guia)          # noqa: SLF001
        payload = {"category_id": "X", "attributes": [{"id": "SIZE", "value_id": "s37"}]}
        antes = copy.deepcopy(payload)                   # copia hecha ANTES de llamar
        self.assertEqual(ml_api.create_item(payload, "tok")[1], 201)
        self.assertEqual(payload, antes)                 # ni un atributo tocado en sitio
        self.assertEqual(self.posts, [("tok", antes)])
        PR._instalar_guardia()                                               # noqa: SLF001 — idempotente
        self.assertIs(PR._create_item_vendor, self.create_item_de_prueba)    # noqa: SLF001
        PR._desinstalar_guardia()                                            # noqa: SLF001
        self.assertIs(ml_api.create_item, self.create_item_de_prueba)

    def test_sin_contexto_la_guardia_no_toca_el_payload(self):
        """Con los mismos ids que la guardia pisa cuando SÍ hay guía."""
        PR._instalar_guardia()                                               # noqa: SLF001
        payload = {"category_id": "X", "title": "t", "family_name": "f", "attributes": [
            {"id": "SELLER_SKU", "value_name": "A"}, {"id": "PART_NUMBER", "value_name": "A"},
            {"id": "GENDER", "value_id": "1"}, {"id": "BRAND", "value_id": "b1"},
            {"id": "COLOR", "value_id": "c1"}, {"id": "MAIN_COLOR", "value_id": "m2"},
            {"id": "SIZE", "value_id": "s37"}, {"id": "SIZE_GRID_ID", "value_id": "9"}]}
        antes = copy.deepcopy(payload)
        ml_api.create_item(payload, "tok")
        self.assertEqual(payload, antes)
        self.assertEqual(self.posts, [("tok", antes)])

    def test_con_la_guardia_instalada_un_alta_sin_guia_sale_byte_a_byte_igual(self):
        """Así vive producción desde la primera alta de moda hasta el siguiente reinicio:
        TODA alta de ML (taladros, sillas…) pasa por la guardia."""
        self.assertTrue(self.crear(prod(), ["BEKURA"])["ok"])                # moda: instala la guardia
        self.assertIs(ml_api.create_item, PR._create_item_con_guia)          # noqa: SLF001
        self.posts.clear()
        p = prod(cat=CAT_TALADRO, talla=None, genero=None)
        esperado = json.dumps(VENDOR_BUILD(dict(p), "tok-BEKURA", False, "BEKURA"), sort_keys=True)
        r = self.crear(p, ["BEKURA"])
        self.assertTrue(r["ok"])
        self.assertEqual(json.dumps(self.posts[0][1], sort_keys=True), esperado)
        self.assertEqual(sorted(r), ["canal", "modo", "ok", "registrado_en", "resultados"])
        # …su vista previa es la del vendor, sin líneas nuevas…
        vista = self.preview(p, "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual(sorted(vista), ["avisos", "cuenta", "ok", "payload", "prod"])
        self.assertEqual(vista["avisos"], [])
        self.assertEqual(json.dumps(vista["payload"], sort_keys=True),
                         json.dumps(VENDOR_BUILD(dict(p), "tok-BEKURA", True, "BEKURA"), sort_keys=True))
        # …con sus mensajes y sus reintentos de siempre (los de main: un 400 con causas no
        # se repite desde v0.682.0 y dice por qué).
        self.posts.clear()
        rechazo = _rechazo(_causa("item.price.invalid", message="price too low"))
        self.respuestas = [rechazo, rechazo, rechazo]
        fila = self.crear(p, ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["intentos"], len(self.posts)), (1, 1))
        self.assertIn("price too low", fila["error"])

    def test_el_reintento_que_quita_el_genero_no_llega_a_ml(self):
        """publisher_core.py:495-504: ante `invalid.title.gender` quita GENDER y reenvía.
        Con guía, GENDER tiene que ser el de la guía: ese segundo POST no debe salir."""
        self.respuestas = [_rechazo(_causa("invalid.title.gender", message="title vs gender"))]
        fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["ok"], fila["intentos"], len(self.posts)), (False, 1, 1))
        self.assertEqual(len(_attr(self.posts[0][1], "GENDER")), 1)
        self.assertIn("TITLE_GENDER_MISMATCH", fila["error"])

    def test_el_reintento_que_quita_la_guia_tampoco(self):
        """publisher_core.py:507-516: ante un rechazo de la guía la quita y reenvía; eso
        convertía «la guía se mandó mal» en «falta la guía» (737 filas del backlog)."""
        self.respuestas = [_rechazo(_causa("invalid.fashion_grid.grid_id.values", message="grid not valid"))]
        fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["intentos"], len(self.posts)), (1, 1))
        self.assertIn("GUIA_DE_TALLAS_RECHAZADA (invalid.fashion_grid.grid_id.values)", fila["error"])
        self.assertNotIn("missing", fila["error"])

    def test_un_reintento_legitimo_si_sale_y_lleva_la_guia(self):
        self.respuestas = [_rechazo(_causa("missing.seller.package.dimensions"))]
        fila = self.crear(prod(color="Negro / Rosa"), ["BEKURA"])["resultados"][0]
        self.assertTrue(fila["ok"])
        self.assertEqual(len(self.posts), 2)
        segundo = self.posts[1][1]
        self.assertEqual(_attr(segundo, "SELLER_PACKAGE_WEIGHT"), [{"id": "SELLER_PACKAGE_WEIGHT", "value_name": "1000 g"}])
        self.assertEqual(_attr(segundo, "SIZE_GRID_ROW_ID")[0]["value_name"], "9000001:2")
        self.assertEqual(_attr(segundo, "SIZE"), [{"id": "SIZE", "value_name": "24 MX"}])
        self.assertEqual(_attr(segundo, "COLOR"), [{"id": "COLOR", "value_name": "Negro / Rosa"}])

    def test_tras_un_401_o_un_5xx_el_mismo_payload_si_se_reenvia(self):
        self.respuestas = [({"message": "invalid_token"}, 401), ({"message": "boom"}, 500)]
        fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertTrue(fila["ok"])
        self.assertEqual([t for t, _ in self.posts], ["tok-BEKURA", "tok-nuevo", "tok-nuevo"])
        self.assertEqual(G.huella(self.posts[0][1]), G.huella(self.posts[2][1]))

    def test_el_contexto_se_quita_aunque_el_vendor_reviente(self):
        p = prod()
        guia = G.resolver(p, "BEKURA", "tok")
        with mock.patch.object(publisher_core, "publish_product", side_effect=RuntimeError("bum")):
            with self.assertRaises(RuntimeError):
                PR._publicar_con_guia(dict(p), "tok", "BEKURA", guia)        # noqa: SLF001
        self.assertIsNone(PR._CTX_ALTA.get())                                # noqa: SLF001

    def test_dos_altas_a_la_vez_no_se_pasan_el_contexto(self):
        """Un alta de moda y una de taladros al mismo tiempo: cada hilo ve SU contexto."""
        moda, taladro = prod(), prod(cat=CAT_TALADRO, talla=None, genero=None)
        taladro["sku"] = "INV-0009"

        async def las_dos():
            with mock.patch.object(PR, "construir_prod", side_effect=lambda sku, w, c: (
                    taladro if sku == "INV-0009" else moda)):
                return await asyncio.gather(
                    PR.crear_ml(moda["sku"], 1, {}, ["BEKURA"], categoria_confirmada=moda["ml_category_id"]),
                    PR.crear_ml("INV-0009", 2, {}, ["BEKURA"], categoria_confirmada=taladro["ml_category_id"]))
        a, b = asyncio.run(las_dos())
        self.assertTrue(a["ok"] and b["ok"])
        por_sku = {next(x["value_name"] for x in payload["attributes"] if x["id"] == "SELLER_SKU"): payload
                   for _, payload in self.posts}
        self.assertEqual(len(_attr(por_sku[moda["sku"]], "SIZE_GRID_ROW_ID")), 1)
        self.assertEqual(json.dumps(por_sku["INV-0009"], sort_keys=True),
                         json.dumps(VENDOR_BUILD(dict(taladro), "tok-BEKURA", False, "BEKURA"), sort_keys=True))

    def test_la_bitacora_guarda_el_mismo_mensaje_que_el_modal(self):
        r = {"success": False, "error": "NEEDS_MANUAL_CONFIG: ME1_INACTIVO (activar Mercado Envíos 1 en dashboard ML)",
             "ml_response": _rechazo(_causa("invalid.fashion_grid.size.values"),
                                     _causa("shipping.lost_me1_by_user", "warning"))[0]}
        self.assertEqual(PR._error_de_bitacora(r, "BEKURA"), r["error"])     # noqa: SLF001 — sin alta con guía
        guia = G.resolver(prod(), "BEKURA", "tok")
        marca = PR._CTX_ALTA.set({"guia": guia, "cuenta": "BEKURA", "sku": "X",   # noqa: SLF001
                                  "ultimo": None})
        try:
            texto = PR._error_de_bitacora(r, "BEKURA")                       # noqa: SLF001
            self.assertEqual(PR._error_de_bitacora({"success": True}, "BEKURA"), None)   # noqa: SLF001
        finally:
            PR._CTX_ALTA.reset(marca)                                        # noqa: SLF001
        self.assertIn("GUIA_DE_TALLAS_RECHAZADA (invalid.fashion_grid.size.values)", texto)
        self.assertNotIn("ME1", texto)

    def test_el_gancho_real_de_ml_backlog_guarda_la_etiqueta_verdadera(self):
        """`Base` sustituye `PR._backlog_ml` entero: aquí corre el REAL (con la base de
        mentira) durante un alta con guía, y fuera de ella."""
        guia = G.resolver(prod(), "BEKURA", "t")
        resp = _rechazo(_causa("invalid.fashion_grid.size.values"),
                        _causa("shipping.lost_me1_by_user", "warning"))[0]
        del_vendor = "NEEDS_MANUAL_CONFIG: ME1_INACTIVO (activar Mercado Envíos 1 en dashboard ML)"
        entry = {"cuenta": "BEKURA", "wc_id": 1, "payload": {}, "ml_response": resp, "ml_status": 400,
                 "result": {"success": False, "error": del_vendor, "ml_response": resp}}
        from services import kubera_mirror

        def guardar():
            cur = mock.MagicMock()
            cur.lastrowid = 7
            cm = mock.MagicMock()
            cm.__enter__.return_value = cur
            with mock.patch.object(PR.db, "get_cursor", return_value=cm), \
                    mock.patch.object(kubera_mirror, "espejar") as espejo:
                BACKLOG_REAL("BEKURA:X", copy.deepcopy(entry))
            return cur.execute.call_args.args[1][7], espejo.call_args.args[5]["error_resumen"]   # columna `error`

        marca = PR._CTX_ALTA.set({"guia": guia, "cuenta": "BEKURA", "sku": "X",   # noqa: SLF001
                                  "ultimo": None})
        try:
            guardado, espejado = guardar()
        finally:
            PR._CTX_ALTA.reset(marca)                                        # noqa: SLF001
        self.assertIn("GUIA_DE_TALLAS_RECHAZADA (invalid.fashion_grid.size.values)", guardado)
        self.assertNotIn("ME1", guardado)
        self.assertEqual(espejado, guardado)
        guardado, espejado = guardar()                           # sin alta con guía: lo de main,
        self.assertTrue(guardado.startswith(del_vendor))         # el del vendor con su porqué (v0.682.0)
        self.assertEqual(espejado, guardado)


# ═══════════════════════════ la vista previa ═════════════════════════════════

class VistaPrevia(Base):
    def test_una_linea_por_cuenta_y_el_payload_de_la_primera_que_si_sale(self):
        r = self.preview(prod(genero="Hombre", talla="41"), "BEKURA", ["SANCORFASHION", "BEKURA"])
        self.assertEqual((r["ok"], r["cuenta"]), (True, "BEKURA"))
        self.assertEqual(len(r["avisos"]), 5)
        self.assertTrue(r["avisos"][0].startswith("SANCORFASHION · NO se publicará."))
        self.assertTrue(r["avisos"][1].startswith("BEKURA · guía de tallas 9000003"))
        self.assertTrue(r["avisos"][2].startswith("Si confirmas, se creará SOLO en BEKURA."))
        self.assertEqual(r["avisos"][3], "El payload de abajo es el de BEKURA.")
        self.assertEqual(r["avisos"][4], "Color: saldrá como «Negro» (eje Color de la variante).")
        self.assertEqual(_attr(r["payload"], "SIZE_GRID_ROW_ID")[0]["value_name"], "9000003:3")
        self.assertEqual({hilo for _, hilo in self.resueltas}, {False})

    def test_la_linea_del_color_dice_que_sale_y_que_habria_mandado_el_vendor(self):
        p = prod(color="Negro / Rosa", meta_color="Gris")
        p["ml_attrs"]["MAIN_COLOR"] = "Gris"
        r = self.preview(p, "BEKURA", ["BEKURA"])
        color = [a for a in r["avisos"] if a.startswith("Color:")]
        self.assertEqual(color, [
            "Color: saldrá como «Negro / Rosa» (eje Color de la variante; texto libre: no está en "
            "la lista de la categoría). La meta ml_attr_COLOR dice «Gris»; la variante es «Negro / "
            "Rosa»: se publica «Negro / Rosa». El publicador habría mandado «Coral claro». El color "
            "principal (MAIN_COLOR) que habría mandado el publicador («Gris») no sale de este "
            "producto y se quita."])
        self.assertEqual(_attr(r["payload"], "COLOR"), [{"id": "COLOR", "value_name": "Negro / Rosa"}])
        self.assertEqual(_attr(r["payload"], "MAIN_COLOR"), [])

    def test_si_el_formulario_dice_otro_color_no_bloquea_se_dice_y_sale_el_de_la_variante(self):
        """«Mejorar con IA» dejó «Rosa» en el formulario de la variante «Negro / Rosa»: ni se
        publica «Rosa» ni se bloquea. La línea dice las dos cosas y cuál sale."""
        p = prod(color="Negro / Rosa", panel={"Color": "Rosa"})
        r = self.preview(p, "BEKURA", ["BEKURA"])
        self.assertTrue(r["ok"])
        self.assertIn(
            "Color: saldrá como «Negro / Rosa» (eje Color de la variante; texto libre: no está en la "
            "lista de la categoría). El formulario del Estudio dice «Rosa»; la variante es «Negro / "
            "Rosa»: se publica «Negro / Rosa». El publicador habría mandado «Rosa».", r["avisos"])
        self.assertEqual(_attr(r["payload"], "COLOR"), [{"id": "COLOR", "value_name": "Negro / Rosa"}])
        self.assertFalse(any("capturado" in a for a in r["avisos"]))
        self.assertTrue(self.crear(p, ["BEKURA"])["ok"])         # y el alta manda lo mismo
        self.assertEqual(_attr(self.posts[0][1], "COLOR"), [{"id": "COLOR", "value_name": "Negro / Rosa"}])
        # Sin nada que diga otra cosa, la línea no lleva nota.
        limpio = self.preview(prod(color="Negro"), "BEKURA", ["BEKURA"])
        self.assertIn("Color: saldrá como «Negro» (eje Color de la variante).", limpio["avisos"])

    def test_la_meta_heredada_que_el_vendor_habria_mandado_se_dice(self):
        r = self.preview(prod(color="Negro", meta_color="Rosa"), "BEKURA", ["BEKURA"])
        self.assertIn("Color: saldrá como «Negro» (eje Color de la variante). La meta ml_attr_COLOR "
                      "dice «Rosa»; la variante es «Negro»: se publica «Negro». El publicador habría "
                      "mandado «Rosa».", r["avisos"])

    def test_si_ninguna_cuenta_puede_salir_no_hay_nada_que_confirmar(self):
        r = self.preview(prod(genero=None), "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual((r["ok"], r["bloqueo_guia"], len(r["avisos"])), (False, True, 2))
        self.assertTrue(r["motivo"].startswith("No se puede publicar en Mercado Libre. Guía de tallas: falta el GÉNERO."))
        self.assertEqual(r["motivo"].count("falta el GÉNERO"), 1)            # del producto: una sola vez
        self.assertIn("En BEKURA hay guía registrada para", r["motivo"])
        self.assertIn("En SANCORFASHION hay guía registrada para", r["motivo"])
        otra = self.preview(prod(cat=CAT_ROPA, talla="M", genero="Hombre"), "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertIn("BEKURA: Guía de tallas: no hay guía registrada para BEKURA", otra["motivo"])
        self.assertIn("SANCORFASHION: Guía de tallas: no hay guía registrada para SANCORFASHION", otra["motivo"])
        color = self.preview(prod(color=None, meta_color="Gris"), "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual(color["motivo"].count("no tiene COLOR propio"), 1)

    def test_la_cuenta_sin_token_va_dentro_del_motivo_cuando_si_se_bloquea(self):
        """Con `ok: False` el modal solo pinta `motivo`: en `avisos` nadie la vería."""
        sin_bekura = mock.AsyncMock(side_effect=lambda c=None: None if c == "BEKURA" else f"tok-{c}")
        with mock.patch.object(meli, "access_token_async", sin_bekura):
            r = self.preview(prod(genero="Hombre", talla="41"), "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual((r["ok"], r["bloqueo_guia"]), (False, True))
        self.assertIn("SANCORFASHION: Guía de tallas: no hay guía registrada", r["motivo"])
        self.assertTrue(r["motivo"].endswith(
            "BEKURA: sin token de Mercado Libre en este momento; no se revisó su guía de tallas "
            "(vuelve a intentar)."))

    def test_una_cuenta_sin_token_no_bloquea_por_si_sola(self):
        nadie = mock.AsyncMock(return_value=None)
        with mock.patch.object(meli, "access_token_async", nadie), \
                mock.patch.object(meli, "_access_token", return_value=None):
            r = self.preview(prod(genero=None), "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual(r, {"ok": False, "motivo": "Sin token de Mercado Libre para BEKURA."})
        self.assertEqual(self.resueltas, [])

    def test_dominio_sin_guia_es_la_vista_previa_de_siempre(self):
        p = prod(cat=CAT_TALADRO, talla=None, genero=None)
        r = self.preview(p, "BEKURA", ["SANCORFASHION"])        # re-crear solo una: hoy también enseña BEKURA
        self.assertEqual(sorted(r), ["avisos", "cuenta", "ok", "payload", "prod"])
        self.assertEqual(r["avisos"], [])
        self.assertEqual((r["ok"], r["cuenta"]), (True, "BEKURA"))

    def test_el_titulo_que_ya_trae_la_talla_o_el_color_se_avisa(self):
        r = self.preview(prod(titulo="Tenis de prueba Negro talla 37"), "BEKURA", ["BEKURA"])
        titulo = [a for a in r["avisos"] if a.startswith("Título:")]
        self.assertEqual(len(titulo), 1)
        self.assertIn("la talla («37») y el color («Negro»)", titulo[0])
        self.assertEqual([a for a in self.preview(prod(), "BEKURA", ["BEKURA"])["avisos"]
                          if a.startswith("Título:")], [])

    def test_el_aviso_del_titulo_sigue_sirviendo_con_el_titulo_propio_de_la_variante(self):
        """Desde v0.623.0 cada variante manda SU título («Padre - Negro / Rosa, 37» o el que
        le puso Crear), que suele traer color y talla; `family_name` son sus primeros 60."""
        propio = prod(color="Negro / Rosa", titulo="Tenis casual de dama - Negro / Rosa, 37")
        r = self.preview(propio, "BEKURA", ["BEKURA"])
        self.assertEqual(r["payload"]["family_name"], "Tenis casual de dama - Negro / Rosa, 37")
        titulo = [a for a in r["avisos"] if a.startswith("Título:")]
        self.assertEqual(len(titulo), 1)
        self.assertIn("la talla («37») y el color («Negro»)", titulo[0])
        largo = prod(color="Negro / Rosa",
                     titulo="Tenis casual de dama con suela antiderrapante y plantilla suave Negro Rosa talla 37")
        cortado = self.preview(largo, "BEKURA", ["BEKURA"])       # la talla quedó fuera de los 60
        self.assertEqual([a for a in cortado["avisos"] if a.startswith("Título:")], [])

    def test_el_tipo_de_calzado_del_titulo_se_avisa_sin_bloquear(self):
        r = self.preview(prod(titulo="Sandalia de correa casual"), "BEKURA", ["BEKURA"])
        self.assertTrue(r["ok"])
        tipo = [a for a in r["avisos"] if a.startswith("Categoría:")]
        self.assertEqual(tipo, [G.MSG_TIPO.format(tipos="«sandalia o huarache»", dominio="SNEAKERS")])
        self.assertEqual([a for a in self.preview(prod(), "BEKURA", ["BEKURA"])["avisos"]
                          if a.startswith("Categoría:")], [])

    def test_la_vista_previa_no_lee_tokens_en_el_event_loop(self):
        """Regla 11: el token de cada cuenta se pide con el lector asíncrono."""
        llamadas = []

        def sinc(c=None):
            llamadas.append((c, threading.current_thread() is threading.main_thread()))
            return f"tok-{c}"
        with mock.patch.object(meli, "_access_token", side_effect=sinc):
            self.preview(prod(genero="Hombre", talla="41"), "BEKURA", ["SANCORFASHION", "BEKURA"])
        # Solo se tolera la lectura síncrona que YA existía (la de la cuenta del payload).
        self.assertEqual([c for c, en_loop in llamadas if en_loop], ["BEKURA"])
        self.assertEqual(meli.access_token_async.await_count, 2)

    def test_el_modal_recibe_las_lineas_de_la_guia(self):
        """`publicar._preview_ml` solo leía `payload` y `motivo` de lo que devuelve el adaptador."""
        doble = mock.AsyncMock(return_value={"ok": False, "motivo": "M", "avisos": ["A1", "A2"]})
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=[]), \
                mock.patch.object(publicar, "_payload_crear_ml", doble):
            r = asyncio.run(publicar._preview_ml({"sku": "INV-0001", "campos": {}}))   # noqa: SLF001
        self.assertTrue(r["ok"])
        self.assertEqual(r["avisos"][-3:], ["M", "A1", "A2"])
        self.assertIsNone(doble.await_args.args[1])              # sin publicaciones: se crean las dos

    def test_el_modal_no_ofrece_confirmar_si_la_guia_bloquea_todas_las_cuentas(self):
        """Va como HTTP 200 con ok False (el modal quita el botón); un HTTPException se
        pintaría como «No se pudo generar la vista previa»."""
        doble = mock.AsyncMock(return_value={"ok": False, "bloqueo_guia": True, "motivo": "POR QUÉ",
                                             "avisos": ["A1"]})
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=[]), \
                mock.patch.object(publicar, "_payload_crear_ml", doble):
            r = asyncio.run(publicar._preview_ml({"sku": "INV-0001", "campos": {}}))   # noqa: SLF001
        self.assertEqual(r, {"ok": False, "canal": "mercado_libre", "sku": "INV-0001",
                             "modo": "crear", "motivo": "POR QUÉ"})

    def _viva_y_eliminada(self, doble, wc_id=None, meta=None):
        """`_preview_ml` con BEKURA viva y SANCORFASHION eliminada. Con `wc_id` y `meta`, la
        pregunta barata («¿el dominio exige guía?») corre de verdad sobre esas metas."""
        pubs = [{"cuenta": "BEKURA", "item_id": "MLM1"}, {"cuenta": "SANCORFASHION", "item_id": "MLM2"}]
        estados = mock.AsyncMock(return_value={"BEKURA": {"vivo": True, "status": "active"},
                                               "SANCORFASHION": {"vivo": False, "status": "closed"}})
        self.metas = mock.Mock(return_value=(dict(meta or {}), set()))
        self.cats_wc = mock.Mock(return_value=[])
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=pubs), \
                mock.patch.object(publicar, "_estados_items_ml", estados), \
                mock.patch.object(publicar, "_payload_crear_ml", doble), \
                mock.patch.object(wp_db, "postmeta_con_herencia", self.metas), \
                mock.patch.object(wp_db, "categorias_wc", self.cats_wc), \
                mock.patch.object(publicar, "_prod_alta", mock.AsyncMock(return_value={
                    "weight": 0.9, "length": 32, "width": 22, "height": 12})), \
                mock.patch.object(publicar, "_revision_categoria_ml", mock.AsyncMock(return_value=None)), \
                mock.patch.object(publicar, "_aviso_precio_alta_ml", mock.AsyncMock(return_value=None)):
            # (main) la re-creación pasa antes por el candado de medidas: con medidas reales sí va
            return asyncio.run(publicar._preview_ml(                                    # noqa: SLF001
                {"sku": "INV-0001", "wc_id": wc_id, "campos": {"titulo": "Tenis"}}))

    def test_con_una_cuenta_viva_y_otra_eliminada_tambien_se_resuelve_la_que_se_re_crea(self):
        """`_confirmar_ml` actualiza la viva y RE-CREA la eliminada: esa alta pasa por el
        pre-vuelo, y la vista previa no decía nada de su guía."""
        doble = mock.AsyncMock(return_value={"ok": True, "payload": {"pictures": []}, "cuenta": "SANCORFASHION",
                                             "avisos": ["SANCORFASHION · guía de tallas 9000002 …", "Color: …"]})
        r = self._viva_y_eliminada(doble)
        self.assertEqual((r["ok"], r["modo"], r["payload"]), (True, "actualizar", None))   # ni modo ni payload cambian
        self.assertEqual(r["avisos"][-2:], ["SANCORFASHION · guía de tallas 9000002 …", "Color: …"])
        self.assertEqual(doble.await_args.args[1], ["SANCORFASHION"])
        bloqueada = mock.AsyncMock(return_value={"ok": False, "bloqueo_guia": True, "motivo": "M",
                                                 "avisos": ["SANCORFASHION · NO se publicará. Falta X"]})
        r = self._viva_y_eliminada(bloqueada)
        self.assertEqual((r["ok"], r["modo"]), (True, "actualizar"))             # la viva sí se actualiza
        self.assertEqual(r["avisos"][-1], "SANCORFASHION · NO se publicará. Falta X")
        # Con su `wc_id` (así llega del modal) y una categoría de moda: igual se resuelve.
        with mock.patch.object(G.requests, "get", side_effect=_get_con_categorias):
            r = self._viva_y_eliminada(doble, wc_id=10, meta={"ml_categoria_id": CAT_TENIS})
        self.assertEqual(r["avisos"][-2:], ["SANCORFASHION · guía de tallas 9000002 …", "Color: …"])
        self.assertEqual(doble.await_count, 2)
        self.assertEqual(doble.await_args.args[1], ["SANCORFASHION"])

    def test_con_viva_y_eliminada_fuera_de_moda_no_se_arma_ningun_payload(self):
        """Resolver la guía de la que se re-crea cuesta `construir_prod` + el dry-run del
        vendor, y en un taladro no agrega ni una línea: primero se pregunta lo barato."""
        doble = mock.AsyncMock(return_value={"ok": True, "payload": {"pictures": []}, "avisos": ["NO DEBE VERSE"]})
        hilos = []
        real = PR._dominio_exige_guia                                           # noqa: SLF001

        def barata(wc_id):
            hilos.append(threading.current_thread() is threading.main_thread())
            return real(wc_id)
        with mock.patch.object(PR, "_dominio_exige_guia", side_effect=barata), \
                mock.patch.object(PR, "construir_prod") as construir, \
                mock.patch.object(publisher_core, "build_payload") as dry_run, \
                mock.patch.object(G.requests, "get", side_effect=_get_con_categorias) as get:
            r = self._viva_y_eliminada(doble, wc_id=10, meta={"ml_categoria_id": CAT_TALADRO})
            # Lo único que se lee de ML: la categoría, por su ruta pública, la PRIMERA vez.
            self.assertEqual([c.args[0].split("/categories/")[-1] for c in get.call_args_list],
                             [CAT_TALADRO])
            self.metas.assert_called_once_with(10)               # UNA lectura de metas
            self.cats_wc.assert_not_called()
            self._viva_y_eliminada(doble, wc_id=10, meta={"ml_categoria_id": CAT_TALADRO})
            self.assertEqual(get.call_count, 1)                  # el dominio de una categoría se recuerda
        doble.assert_not_awaited()
        construir.assert_not_called()
        dry_run.assert_not_called()
        meli.access_token_async.assert_not_awaited()             # ni un token
        ml_api.get_category_info.assert_not_called()             # ni la lectura con token
        self.assertEqual(hilos, [False, False])                  # regla 11: en un hilo
        self.assertEqual((r["ok"], r["modo"], len(r["avisos"])), (True, "actualizar", 1))

    def test_la_pregunta_barata_usa_la_misma_categoria_que_construir_prod(self):
        """`alta_exige_guia`: la del panel manda sobre la del predictor; sin ninguna, el
        mapeo de la categoría de WooCommerce; y si no se sabe, no hay nada que resolver."""
        def exige(meta, cats_wc=(), mapeo=None):
            with mock.patch.object(wp_db, "postmeta_con_herencia", return_value=(dict(meta), set())), \
                    mock.patch.object(wp_db, "categorias_wc", return_value=list(cats_wc)), \
                    mock.patch.object(wc_category_mapping, "load_mapping", return_value=mapeo or {}), \
                    mock.patch.object(G.requests, "get", side_effect=_get_con_categorias):
                return asyncio.run(PR.alta_exige_guia(10))
        self.assertIs(exige({"ml_categoria_id": CAT_TENIS, "ml_category_id": CAT_TALADRO}), True)
        self.assertIs(exige({"ml_categoria_id": CAT_TALADRO, "ml_category_id": CAT_TENIS}), False)
        self.assertIs(exige({"ml_category_id": CAT_BRA}), True)
        self.assertIs(exige({}), False)                          # sin categoría: nada que resolver
        self.assertIs(exige({}, [{"id": 77, "name": "Brasieres"}], {77: CAT_BRA}), True)
        with mock.patch.object(ml_api, "get_category_info") as con_token:
            self.assertIs(exige({"ml_categoria_id": "MLM999999"}), False)   # ML no devolvió la categoría
            con_token.assert_not_called()                        # sin token: solo la ruta pública
        # Sin `wc_id`, o si la pregunta barata revienta: que decida el camino completo.
        self.assertIs(asyncio.run(PR.alta_exige_guia(None)), True)
        with mock.patch.object(wp_db, "postmeta_con_herencia", side_effect=OSError("sin base")), \
                self.assertLogs("omnicanal.publicar_ready", "WARNING"):
            self.assertIs(asyncio.run(PR.alta_exige_guia(10)), True)


# ══════════════════════ el mensaje de un rechazo de ML ═══════════════════════

class Etiqueta(unittest.TestCase):
    def _r(self, *causas, status=400):
        return {"success": False, "error": "x", "ml_status": status,
                "ml_response": {"message": "Validation error", "cause": list(causas)}}

    def test_sin_causas_de_error_se_queda_la_del_vendor(self):
        self.assertIsNone(G.etiqueta_error({"success": False, "error": "datos_insuficientes"}, None, "BEKURA"))
        self.assertIsNone(G.etiqueta_error(self._r(_causa("a", "warning")), None, "BEKURA"))
        self.assertIsNone(G.etiqueta_error({"ml_response": {"cause": 374}}, None, "BEKURA"))

    def test_el_warning_de_envio_ya_no_tapa_al_error_real(self):
        e = G.etiqueta_error(self._r(
            _causa("item.attribute.invalid", message="Value name of attribute SIZE_GRID_ID was not provided"),
            _causa("shipping.lost_me1_by_user", "warning")), {"estado": "no_aplica", "dominio": "X"}, "BEKURA")
        self.assertIn("GUIA_DE_TALLAS (item.attribute.invalid)", e)
        self.assertNotIn("ME1", e)

    def test_el_error_real_de_envio_conserva_el_texto_que_traduce_el_adaptador(self):
        e = G.etiqueta_error(self._r(_causa("shipping.lost_me1_by_user")), None, "BEKURA")
        self.assertIn("ME1_INACTIVO (activar Mercado Envíos 1 en dashboard ML)", e)
        self.assertIn("CATEGORIA_SIN_ME2", PR._traducir_error_ml(e, {}))               # noqa: SLF001

    def test_el_codigo_de_barras_ya_no_tapa_a_la_guia(self):
        e = G.etiqueta_error(self._r(
            _causa("item.attribute.missing_conditional_required", message="The attributes [GTIN] are required"),
            _causa("missing.fashion_grid.grid_id.values")),
            {"estado": "desconocido", "category_id": "MLM1", "motivo": "TimeoutError"}, "BEKURA")
        self.assertIn("GTIN_INVALIDO: cuenta BEKURA", e)
        self.assertIn("no se pudo leer la categoría MLM1", e)

    def test_un_error_que_nadie_conoce_sale_con_su_codigo_y_su_mensaje(self):
        e = G.etiqueta_error(self._r(_causa("item.price.invalid", message="price too low")), None, "BEKURA")
        self.assertEqual(e, "Mercado Libre rechazó el alta — item.price.invalid: price too low")

    def test_causa_sin_type_cuenta_como_error_y_cause_puede_venir_como_dict(self):
        e = G.etiqueta_error({"ml_response": {"cause": {"code": "x.y", "message": "m"}}}, None, "BEKURA")
        self.assertEqual(e, "Mercado Libre rechazó el alta — x.y: m")

    def test_definitivo_es_un_400_con_causa_de_error(self):
        self.assertTrue(G.es_rechazo_definitivo(self._r(_causa("x.y"))))
        self.assertFalse(G.es_rechazo_definitivo(self._r(_causa("x.y", "warning"))))
        self.assertFalse(G.es_rechazo_definitivo(self._r(_causa("x.y"), status=500)))
        self.assertFalse(G.es_rechazo_definitivo({"success": False, "error": "TimeoutError: x"}))
        self.assertTrue(G.es_rechazo_de_guia(self._r(_causa("invalid.fashion_grid.seller_id.values"))))
        self.assertFalse(G.es_rechazo_de_guia(self._r(_causa("invalid.fashion_grid.size.values", "warning"))))


# ═══════════ regla 11, estática: nada que espere a la red en el event loop ═══

class ReglaOnceEstatica(unittest.TestCase):
    """Lee el AST de `publicar_ready.py` (no lo ejecuta): en sus corrutinas, lo que lee de
    ML o de la base para la guía de tallas solo aparece como argumento de
    `asyncio.to_thread`, nunca llamado en línea. Las pruebas con hilos de arriba fijan los
    caminos que se ejercitan; ésta mira el archivo entero."""

    BLOQUEANTES = {"resolver", "_publicar_con_guia", "_terminar_alta_creada", "_estado_item",
                   "asegurar_pausado",
                   "_anotar_pausa_backlog", "construir_prod", "build_payload",
                   "publish_product", "_leer_guia", "_valores_de_categoria", "_dominio_de",
                   "_dominio_exige_guia", "dominio_exige_guia", "postmeta_con_herencia",
                   "categorias_wc"}

    def test_lo_bloqueante_de_la_guia_va_en_un_hilo(self):
        fuente = (Path(PR.__file__)).read_text(encoding="utf-8")
        en_linea, en_hilo = [], set()
        for fn in ast.walk(ast.parse(fuente)):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                nombre = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if nombre in self.BLOQUEANTES:
                    en_linea.append(f"{fn.name}:{n.lineno} {nombre}")
                if nombre == "to_thread" and n.args:
                    a = n.args[0]
                    en_hilo.add(a.attr if isinstance(a, ast.Attribute) else getattr(a, "id", ""))
        self.assertEqual(en_linea, [], "Envuélvelas en await asyncio.to_thread(...)")
        for nombre in ("resolver", "_publicar_con_guia", "_estado_item", "asegurar_pausado",
                       "construir_prod", "build_payload", "_publicar_anotando", "_dominio_exige_guia"):
            self.assertIn(nombre, en_hilo)

    def test_el_modulo_de_la_guia_no_tiene_corrutinas(self):
        """`ml_guia_tallas` es síncrono a propósito (como el vendor): quien lo llama desde
        una corrutina lo manda a un hilo. Si alguien le agrega un `async def`, esta
        prueba obliga a revisar la regla 11 ahí también."""
        fuente = Path(G.__file__).read_text(encoding="utf-8")
        self.assertEqual([n.name for n in ast.walk(ast.parse(fuente))
                          if isinstance(n, ast.AsyncFunctionDef)], [])


# ═════════════════════════════════════════════════════════════════════════════
# PRUEBAS QUE DE VERDAD PRUEBAN (revisión del 7-oct-2026).
# Un revisor aparte escribió 147 versiones ingenuas del código y 44 pasaban las pruebas
# de arriba con todo en verde: la familia en revisión que el resolvedor no mira, la vista previa
# que arma el payload de BEKURA cuando la que sale es la otra cuenta, la guía leída sin
# el token de su cuenta, la media talla truncada, la marca de la casa en vez de la
# declarada… El código hacía lo correcto; lo que faltaba era la prueba. Cada una de las
# de abajo dice qué versión ingenua tumba.
# ═════════════════════════════════════════════════════════════════════════════

class _Congelado(dict):
    """Un dict que revienta si alguien lo escribe (para «no muta el prod compartido»)."""

    def _no(self, *a, **k):
        raise AssertionError("se intentó ESCRIBIR en el prod que comparten las dos cuentas")

    __setitem__ = __delitem__ = pop = popitem = clear = update = setdefault = _no


class _ListaCongelada(list):
    def _no(self, *a, **k):
        raise AssertionError("se intentó ESCRIBIR en una lista del prod compartido")

    __setitem__ = __delitem__ = append = extend = insert = pop = remove = clear = sort = reverse = _no
    __iadd__ = _no


def congelar(x):
    if isinstance(x, dict):
        return _Congelado({k: congelar(v) for k, v in x.items()})
    if isinstance(x, list):
        return _ListaCongelada(congelar(v) for v in x)
    return x


# ══════════ la marca DECLARADA y el id del género que SÍ es el de la guía ══════

class MarcaDeclarada(Base):
    """`prod()` declara «Ferrahome», que ES `publisher_core.DEFAULT_BRAND`: con ese dato
    «con guía viaja la declarada» no se distinguía de «viaja siempre la de la casa», y la
    rama «sin marca declarada» no la ejecutaba ninguna prueba."""

    def test_la_marca_declarada_viaja_hasta_el_post_aunque_no_sea_la_de_la_casa(self):
        p = prod()
        p["ml_attrs"]["BRAND"] = "Marca Propia"
        self.assertNotEqual(publisher_core.DEFAULT_BRAND, "Marca Propia")
        g = G.resolver(p, "BEKURA", "t")
        self.assertEqual((g["estado"], g["marca"]), ("ok", "Marca Propia"))
        self.assertTrue(self.crear(p, ["BEKURA"])["ok"])
        self.assertEqual(_attr(self.posts[0][1], "BRAND"),
                         [{"id": "BRAND", "value_name": "Marca Propia"}])

    def test_la_meta_manda_sobre_el_atributo_y_sin_ninguna_es_la_de_la_casa(self):
        los_dos = prod()
        los_dos["ml_attrs"]["BRAND"] = "De La Meta"
        los_dos["wc_attrs"]["marca"] = "Del Atributo"
        self.assertEqual(G.marca_de(los_dos), "De La Meta")       # como el mapper del vendor
        solo_atributo = prod()
        del solo_atributo["ml_attrs"]["BRAND"]
        solo_atributo["wc_attrs"]["Marca"] = "['Del Atributo']"   # con la huella de la IA
        self.assertEqual(G.marca_de(solo_atributo), "Del Atributo")
        ninguna = prod()
        del ninguna["ml_attrs"]["BRAND"]
        self.assertEqual(G.marca_de(ninguna), publisher_core.DEFAULT_BRAND)
        g = G.resolver(ninguna, "BEKURA", "t")
        self.assertEqual((g["estado"], g["marca"]), ("ok", publisher_core.DEFAULT_BRAND))


class IdDelGenero(Base):
    """El `value_id` de GENDER solo se afirmaba para «Mujer», que es el PRIMER valor de la
    lista de la categoría: tomar «el primero» pasaba todo (y es justo el relleno del
    mapper que este cambio corrige). 79 de las 159 publicaciones vivas de moda NO son de
    Mujer."""

    def test_el_id_del_genero_es_el_de_la_guia_no_el_primero_de_la_categoria(self):
        p = prod(genero="Hombre", talla="41")
        g = G.resolver(p, "BEKURA", "t")
        self.assertEqual((g["estado"], g["genero"], g["genero_id"]), ("ok", "Hombre", "339666"))
        self.assertTrue(self.crear(p, ["BEKURA"])["ok"])
        self.assertEqual(_attr(self.posts[0][1], "GENDER"),
                         [{"id": "GENDER", "value_id": "339666", "value_name": "Hombre"}])
        sin = G.resolver(prod(genero="Sin género", talla="24 MX"), "BEKURA", "t")
        self.assertEqual((sin["estado"], sin["genero"], sin["genero_id"]),
                         ("ok", "Sin género", "110461"))


# ═══════════ «Color de suela» no es el Color; «Tipo de talla» no es la Talla ══

class NombreDeAtributoExacto(Base):
    def test_un_atributo_que_solo_contiene_la_palabra_no_es_el_color_ni_la_talla(self):
        """El nombre del atributo se compara ENTERO, nunca por subcadena (que es el
        defecto del mapper del vendor con el color)."""
        simple = prod(color=None, talla=None, variante=False, meta_color=None)
        simple["attributes"] += [{"name": "Color de suela", "options": ["Blanco"]},
                                 {"name": "Tipo de talla", "options": ["Estándar"]},
                                 {"name": "pa_color-del-forro", "options": ["Rojo"]}]
        self.assertEqual(G.color_de(simple)["estado"], "ausente")
        self.assertEqual(G.talla_de(simple), (None, "ausente", []))
        g = G.resolver(simple, "BEKURA", "t")
        self.assertEqual(g["codigos"], ["color_no_fijado", "sin_talla"])
        self.assertEqual(self.crear(simple)["ok"], False)
        self.assertEqual(self.posts, [])


# ═══════════════ «unisex» en el título y el género repetido ═══════════════════

class GeneroUnisexYRepetido(Base):
    def test_unisex_en_el_titulo_no_calla_un_genero_que_no_es_sin_genero(self):
        """«unisex» solo es compatible con «Sin género»: con «Mujer» y «Tenis unisex para
        hombre» se publica (el título ya no bloquea), pero la línea lo DICE."""
        g = G.resolver(prod(genero="Mujer", titulo="Tenis unisex para hombre"), "BEKURA", "t")
        self.assertEqual((g["estado"], g["genero"]), ("ok", "Mujer"))
        self.assertEqual(g["origen_genero"], G.ORIGEN_META + "; el título dice «hombre»")
        # …venga de la meta de Crear o del formulario del Estudio.
        f = G.resolver(prod(genero=None, panel={"Género": "Mujer"},
                            titulo="Tenis unisex para hombre"), "BEKURA", "t")
        self.assertEqual((f["estado"], f["origen_genero"]),
                         ("ok", G.ORIGEN_FORMULARIO + "; el título dice «hombre»"))

    def test_unisex_es_compatible_con_sin_genero_infantil(self):
        d = G.decidir_genero(prod(genero="Sin género infantil",
                                  titulo="Tenis unisex para niños y niñas"))
        self.assertEqual((d["genero"], d["codigo"], d["rango"], d["origen"]),
                         ("Sin género infantil", "", 3, G.ORIGEN_META))
        # Sin «unisex», el título que nombra a los dos se dice (y tampoco bloquea).
        e = G.decidir_genero(prod(genero="Sin género infantil", titulo="Tenis para niños y niñas"))
        self.assertEqual((e["genero"], e["origen"]),          # con su ñ, como está en el título
                         ("Sin género infantil", G.ORIGEN_META + "; el título dice «niñas», «niños»"))
        # La línea de la vista previa lo enseña igual: «niña», «niños», «bebé» (antes «nina»…).
        g = G.resolver(prod(titulo="Tenis para NIÑA, niños y bebé"), "BEKURA", "t")
        self.assertIn("Género Mujer (lo propuso la IA al crear el producto: revísalo; el título dice "
                      "«niña», «niños», «bebé»)", G.aviso(g))

    def test_el_mismo_genero_repetido_en_una_fuente_si_decide(self):
        metas = prod(genero="Mujer")
        metas["ml_attrs"]["gender"] = "mujer"            # ml_attr_GENDER y ml_attr_gender dicen lo mismo
        d = G.decidir_genero(metas)
        self.assertEqual((d["genero"], d["codigo"]), ("Mujer", ""))
        woo = prod(genero=None, genero_woo=["Mujer", "MUJER"])
        self.assertEqual(G.decidir_genero(woo)["genero"], "Mujer")
        self.assertEqual(G.resolver(metas, "BEKURA", "t")["estado"], "ok")


# ═══════════════════════════ renglones y guías raras ══════════════════════════

class RenglonesYGuias(Base):
    def test_un_pie_mucho_MENOR_que_la_talla_tambien_es_imposible(self):
        def renglon(mx, pie):
            return {"id": "9000099:1", "attributes": [
                {"id": "SIZE", "values": [{"name": f"{mx} MX", "struct": {"number": float(mx), "unit": "MX"}}]},
                {"id": "FOOT_LENGTH", "values": [{"name": f"{pie} cm", "struct": {"number": float(pie), "unit": "cm"}}]}]}
        self.assertEqual(G.renglon_imposible(renglon(28, 18)), "dice 28 MX y 18 cm de largo de pie")
        self.assertEqual(G.renglon_imposible(renglon(28, 26)), "")           # 2 cm por abajo: tolerado
        self.assertEqual(G.renglon_imposible(renglon(28, 25.5)), "dice 28 MX y 25.5 cm de largo de pie")

    def test_una_talla_entera_no_empata_con_la_media_talla_eu_ni_al_reves(self):
        """La guía de prueba trae EU 36, 37, 38.5 y 39: «38» NO es «38.5» ni «39.5» es «39»."""
        self.assertEqual(G.resolver(prod(talla="38"), "BEKURA", "t")["codigo"], "talla_sin_renglon")
        self.assertEqual(G.resolver(prod(talla="39.5"), "BEKURA", "t")["codigo"], "talla_sin_renglon")
        self.assertEqual(G.resolver(prod(talla="38.5"), "BEKURA", "t")["renglon_id"], "9000001:5")
        self.assertEqual(G.resolver(prod(talla="39"), "BEKURA", "t")["renglon_id"], "9000001:7")

    def test_una_guia_declarada_para_dos_generos_no_se_usa(self):
        doble = copy.deepcopy(GUIAS["9000001"])
        doble["attributes"] = [{"id": "GENDER", "values": [{"id": "339666", "name": "Hombre"},
                                                           {"id": "339665", "name": "Mujer"}]}]
        with mock.patch.object(G.requests, "get", return_value=_una_guia(doble)):
            g = G.resolver(prod(), "BEKURA", "t")
        self.assertEqual(g["codigo"], "guia_no_sirve")
        self.assertIn("es para el género «Hombre, Mujer», no para «Mujer»", g["motivo"])

    def test_una_guia_sin_renglones_lo_dice(self):
        vacia = copy.deepcopy(GUIAS["9000001"])
        vacia["rows"] = []
        with mock.patch.object(G.requests, "get", return_value=_una_guia(vacia)):
            g = G.resolver(prod(), "BEKURA", "t")
        self.assertEqual(g["codigo"], "guia_no_sirve")
        self.assertIn("no tiene renglones", g["motivo"])

    def test_si_ml_devuelve_otra_guia_que_la_pedida_no_se_usa_ni_se_guarda(self):
        otra = copy.deepcopy(GUIAS["9000002"])               # se pidió la 9000001
        with mock.patch.object(G.requests, "get", return_value=_una_guia(otra)), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            g = G.resolver(prod(), "BEKURA", "t")
        self.assertEqual(g["codigo"], "guia_ilegible")
        self.assertIn("la respuesta no tiene la forma de una guía", g["motivo"])
        self.assertEqual(G._guias, {})                                         # noqa: SLF001

    def test_el_estudio_elige_por_texto_EXACTO_no_por_subcadena(self):
        """«6 MX» está contenido en «26 MX» y «5 MX» en «26.5 MX»: no son esos renglones."""
        for escrito in ("6 MX", "5 MX"):
            p = prod(talla="40", genero="Hombre", panel={"Talla": escrito})
            self.assertEqual(G.resolver(p, "BEKURA", "t")["codigo"], "talla_ambigua", escrito)


# ═══════ la categoría que se resuelve es la que el VENDOR va a usar ═══════════

class CategoriaEfectiva(Base):
    def test_si_la_categoria_de_woo_cambia_la_de_ml_se_resuelve_con_esa(self):
        """publisher_core.py:185-196: la categoría WC (su «ML: MLM…») le gana a la meta
        guardada. Todas las pruebas usaban `wc_categories: []`."""
        p = prod(cat=CAT_TALADRO, talla="M")                 # la meta guardada dice taladros
        p["wc_categories"] = [{"id": 77, "name": "Brasieres"}]
        with mock.patch.object(wc_category_mapping, "load_mapping", return_value={77: CAT_BRA}):
            self.assertEqual(G.categoria_efectiva(p), CAT_BRA)
            g = G.resolver(p, "BEKURA", "t")
            self.assertEqual((g["estado"], g["category_id"], g["dominio"], g["size"]),
                             ("ok", CAT_BRA, "BRAS", "M"))
            payload = VENDOR_BUILD(dict(p), "tok", True, "BEKURA")
            self.assertEqual(payload["category_id"], CAT_BRA)      # el vendor arma con la misma…
            G.aplicar(payload, g, "BEKURA")                         # …y no hay «otra categoría»
            self.assertEqual(_attr(payload, "SIZE_GRID_ID"), [{"id": "SIZE_GRID_ID", "value_name": "9000005"}])
        with mock.patch.object(wc_category_mapping, "load_mapping", return_value={77: None}):
            self.assertEqual(G.categoria_efectiva(p), CAT_TALADRO)  # sin mapeo: la guardada
        with mock.patch.object(wc_category_mapping, "load_mapping", return_value={77: CAT_TALADRO}):
            self.assertEqual(G.categoria_efectiva(p), CAT_TALADRO)  # «same»


# ═════════════════════════ lo que dice la categoría ═══════════════════════════

class LoQueDiceLaCategoria(Base):
    def test_main_color_sin_lista_de_valores_se_iguala_con_el_texto(self):
        sin_lista = copy.deepcopy(ATTRS_MODA)
        next(a for a in sin_lista if a["id"] == "MAIN_COLOR")["values"] = []
        with mock.patch.object(ml_api, "get_category_attributes", return_value=sin_lista):
            g = G.resolver(prod(color="Negro / Rosa"), "BEKURA", "t")
        self.assertEqual(g["main_color"], {"accion": "igualar", "value_id": "", "value_name": "Negro / Rosa"})
        payload = {"category_id": CAT_TENIS, "family_name": "f",
                   "attributes": [{"id": "MAIN_COLOR", "value_name": "Gris"}]}
        G.aplicar(payload, g, "BEKURA")
        self.assertEqual(_attr(payload, "MAIN_COLOR"), [{"id": "MAIN_COLOR", "value_name": "Negro / Rosa"}])

    def test_los_atributos_de_la_categoria_tambien_se_leen_por_la_ruta_publica(self):
        def publico(url, headers=None, timeout=None):
            if url.endswith(f"/categories/{CAT_TENIS}/attributes"):
                self.assertIsNone(headers)
                return mock.Mock(status_code=200, json=mock.Mock(return_value=[
                    {"id": "GENDER", "values": [{"id": "1", "name": "Bebés"}]}]))
            return _get_guia(url, headers, timeout)
        with mock.patch.object(ml_api, "get_category_attributes", return_value=[]), \
                mock.patch.object(G.requests, "get", side_effect=publico):
            g = G.resolver(prod(genero="Hombre"), "BEKURA", "t")
        self.assertEqual(g["codigo"], "genero_fuera_de_categoria")

    def test_una_categoria_sin_lista_de_generos_no_bloquea_el_genero(self):
        sin_genero = [a for a in copy.deepcopy(ATTRS_MODA) if a["id"] != "GENDER"]
        with mock.patch.object(ml_api, "get_category_attributes", return_value=sin_genero):
            g = G.resolver(prod(), "BEKURA", "t")
        self.assertEqual((g["estado"], g["genero"]), ("ok", "Mujer"))


# ═════════════════════ la caché de la guía y la relectura ═════════════════════

class CacheYRelectura(Base):
    def test_la_cache_caduca_a_los_diez_minutos_de_LEIDA_aunque_se_use_en_medio(self):
        t0 = 1000.0
        with mock.patch.object(G.requests, "get", side_effect=_get_guia) as get:
            with mock.patch.object(G.time, "monotonic", return_value=t0):
                G.resolver(prod(), "BEKURA", "t")
            with mock.patch.object(G.time, "monotonic", return_value=t0 + G.TTL_GUIA_S - 100):
                G.resolver(prod(), "BEKURA", "t")                    # de caché
            self.assertEqual(get.call_count, 1)
            with mock.patch.object(G.time, "monotonic", return_value=t0 + G.TTL_GUIA_S + 1):
                G.resolver(prod(), "BEKURA", "t")                    # 10 min desde que se LEYÓ
        self.assertEqual(get.call_count, 2)

    def test_tambien_se_relee_antes_de_bloquear_por_renglon_imposible(self):
        rota = copy.deepcopy(GUIAS["9000001"])
        rota["rows"][1] = _renglon("9000001", 2, "24 MX", 37, 34)        # 24 MX y 34 cm
        respuestas = [_una_guia(rota), _una_guia(copy.deepcopy(GUIAS["9000001"]))]
        with mock.patch.object(G.requests, "get", side_effect=respuestas) as get:
            self.assertEqual(G.resolver(prod(talla="36"), "BEKURA", "t")["estado"], "ok")   # llena la caché
            g = G.resolver(prod(), "BEKURA", "t")             # de caché: renglón imposible → relee
        self.assertEqual((g["estado"], g["renglon_id"], get.call_count), ("ok", "9000001:2", 2))

    def test_tambien_se_relee_antes_de_bloquear_por_talla_ambigua(self):
        repetida = copy.deepcopy(GUIAS["9000001"])
        repetida["rows"].append(_renglon("9000001", 9, "24.5 MX", 37, 24.5))   # el EU 37 dos veces
        respuestas = [_una_guia(repetida), _una_guia(copy.deepcopy(GUIAS["9000001"]))]
        with mock.patch.object(G.requests, "get", side_effect=respuestas) as get:
            self.assertEqual(G.resolver(prod(talla="36"), "BEKURA", "t")["estado"], "ok")
            g = G.resolver(prod(), "BEKURA", "t")
        self.assertEqual((g["estado"], g["renglon_id"], get.call_count), ("ok", "9000001:2", 2))

    def test_la_copia_releida_queda_en_la_cache(self):
        corta = copy.deepcopy(GUIAS["9000001"])
        corta["rows"] = corta["rows"][:1]
        respuestas = [_una_guia(corta), _una_guia(copy.deepcopy(GUIAS["9000001"]))]
        with mock.patch.object(G.requests, "get", side_effect=respuestas) as get:
            G.resolver(prod(talla="36"), "BEKURA", "t")
            self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "ok")     # releyó
            self.assertEqual(G.resolver(prod(talla="39"), "BEKURA", "t")["estado"], "ok")   # ya está en caché
        self.assertEqual(get.call_count, 2)


# ══════════════ cada guía se lee con el token de SU cuenta ════════════════════

class TokenDeLaCuenta(Base):
    def test_la_guia_se_lee_con_el_token_de_la_cuenta_que_se_va_a_crear(self):
        """Medido el 7-oct-2026: sin token, `GET /catalog/charts/{id}` de una guía SPECIFIC
        contesta 404 «chart_not_found». `_get_guia` no mira la cabecera; aquí sí se mira."""
        vistos = []

        def get(url, headers=None, timeout=None):
            if "/catalog/charts/" in url:
                vistos.append((url.rsplit("/", 1)[-1], (headers or {}).get("Authorization")))
            return _get_guia(url, headers, timeout)

        esperado = [("9000001", "Bearer tok-BEKURA"), ("9000002", "Bearer tok-SANCORFASHION")]
        with mock.patch.object(G.requests, "get", side_effect=get):
            self.assertTrue(self.crear(prod())["ok"])
        self.assertEqual(sorted(vistos), esperado)
        vistos.clear()
        G._guias.clear()                                                       # noqa: SLF001
        with mock.patch.object(G.requests, "get", side_effect=get):
            self.assertTrue(self.preview(prod(), "BEKURA", ["BEKURA", "SANCORFASHION"])["ok"])
        self.assertEqual(sorted(vistos), esperado)


# ══════════ un dominio nuevo registrado en el mapping exige guía ══════════════

class DominioNuevo(Base):
    def test_un_dominio_que_no_esta_en_la_lista_pero_si_en_el_mapping_exige_guia(self):
        nueva = {"id": "MLM900009", "settings": {"catalog_domain": "MLM-DOMINIO_NUEVO"}}
        with mock.patch.dict(CATS, {"MLM900009": nueva}), \
                mock.patch.dict(size_chart_mapping.CHARTS_BY_ACCOUNT["BEKURA"],
                                {"DOMINIO_NUEVO:Mujer": "9000001"}):
            self.assertNotIn("DOMINIO_NUEVO", G.DOMINIOS_CON_GUIA)
            g = G.resolver(prod(cat="MLM900009", genero=None), "BEKURA", "t")
            self.assertEqual((g["estado"], g["codigo"]), ("bloqueado", "sin_genero"))
            # …en CUALQUIER cuenta, aunque la guía esté registrada solo en una.
            h = G.resolver(prod(cat="MLM900009"), "SANCORFASHION", "t")
            self.assertEqual((h["estado"], h["codigo"]), ("bloqueado", "sin_guia"))


# ═══ la guardia: reintentos legítimos que NO tocan atributos, y dos rechazos ═══

class GuardiaConOtrosReintentos(Base):
    """El único reintento legítimo probado era el de dimensiones, que cambia `attributes`.
    El vendor tiene dos que no los tocan (fotos chicas y `sale_terms`): si la huella mirara
    solo los atributos, esos reenvíos «serían el mismo» y el alta fallaría."""

    def test_el_reintento_de_fotos_chicas_si_sale_aunque_no_cambie_ningun_atributo(self):
        """publisher_core.py:475-492: ante `item.pictures.invalid_size` re-sube las fotos y
        reenvía; solo cambia `pictures`."""
        self.respuestas = [_rechazo(_causa("item.pictures.invalid_size"))]
        with mock.patch.object(ml_api, "preupload_picture", side_effect=[None, "PIC2"]):
            fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertTrue(fila["ok"])
        self.assertEqual([p["pictures"] for _, p in self.posts],
                         [[{"source": "https://ejemplo.invalid/a.jpg"}], [{"id": "PIC2"}]])
        self.assertEqual(G.huella({**self.posts[0][1], "pictures": []}),
                         G.huella({**self.posts[1][1], "pictures": []}))      # SOLO cambiaron las fotos
        self.assertEqual(_attr(self.posts[1][1], "SIZE_GRID_ROW_ID")[0]["value_name"], "9000001:2")

    def test_el_reintento_de_sale_terms_si_sale_aunque_no_cambie_ningun_atributo(self):
        """publisher_core.py:573-596: ante `sale_term.invalid_value_id` corrige el `value_id`
        de WARRANTY_TYPE EN SITIO, dentro de `payload['sale_terms']`, y reenvía."""
        self.respuestas = [_rechazo(_causa(
            "sale_term.invalid_value_id",
            message="Sale term WARRANTY_TYPE has an invalid value_id. Allowed values are [9000099]"))]
        fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertTrue(fila["ok"])
        self.assertEqual([[t for t in p["sale_terms"] if t["id"] == "WARRANTY_TYPE"] for _, p in self.posts],
                         [[{"id": "WARRANTY_TYPE", "value_id": "6150835"}],
                          [{"id": "WARRANTY_TYPE", "value_id": "9000099"}]])
        self.assertEqual(G.huella({**self.posts[0][1], "sale_terms": []}),
                         G.huella({**self.posts[1][1], "sale_terms": []}))    # SOLO cambió sale_terms
        self.assertEqual(_attr(self.posts[1][1], "SIZE_GRID_ROW_ID")[0]["value_name"], "9000001:2")

    def test_la_huella_ve_lo_que_no_son_atributos(self):
        a = {"category_id": "X", "pictures": [{"source": "u"}], "attributes": [{"id": "A", "value_name": "1"}]}
        b = {**a, "pictures": [{"id": "PIC"}]}
        self.assertNotEqual(G.huella(a), G.huella(b))

    def test_tras_dos_rechazos_distintos_lo_que_no_se_repite_es_el_ULTIMO_envio(self):
        """Fotos chicas (el vendor re-sube y reenvía) y después `invalid.title.gender` (el
        vendor quita GENDER y reenvía): al reponerlo, el envío es idéntico al SEGUNDO, que es
        el que ML acaba de rechazar. La guardia recuerda el último envío, no el primero."""
        self.respuestas = [_rechazo(_causa("item.pictures.invalid_size")),
                           _rechazo(_causa("invalid.title.gender", message="title vs gender"))]
        with mock.patch.object(ml_api, "preupload_picture", side_effect=[None, "PIC2"]):
            fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["ok"], fila["intentos"], len(self.posts)), (False, 1, 2))
        self.assertIn("TITLE_GENDER_MISMATCH", fila["error"])
        self.assertEqual(len(_attr(self.posts[1][1], "GENDER")), 1)

    def test_el_payload_que_guarda_ml_backlog_es_el_que_se_envio(self):
        """La guardia aplica la guía EN SITIO sobre el dict del vendor, que es el mismo que
        `publish_product` le pasa a `save_backlog`: la bitácora trae lo que de verdad salió,
        también cuando el último reintento del vendor (quitar GENDER) no llegó a ML."""
        self.assertTrue(self.crear(prod(), ["BEKURA"])["ok"])
        guardado = PR._backlog_ml.call_args.args[1]["payload"]               # noqa: SLF001
        self.assertEqual(G.huella(guardado), G.huella(self.posts[0][1]))
        self.assertEqual(_attr(guardado, "SIZE_GRID_ROW_ID"),
                         [{"id": "SIZE_GRID_ROW_ID", "value_name": "9000001:2"}])
        self.assertEqual(_attr(guardado, "SIZE"), [{"id": "SIZE", "value_name": "24 MX"}])
        self.posts.clear()
        self.respuestas = [_rechazo(_causa("invalid.title.gender", message="title vs gender"))]
        self.assertFalse(self.crear(prod(), ["BEKURA"])["ok"])
        guardado = PR._backlog_ml.call_args.args[1]["payload"]               # noqa: SLF001
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(G.huella(guardado), G.huella(self.posts[0][1]))
        self.assertEqual(len(_attr(guardado, "GENDER")), 1)


# ═══════════ el resolvedor no ESCRIBE en el prod (ni lo «normaliza») ══════════

class NoMutaDeVerdad(Base):
    """`test_no_muta_prod_y_nunca_lanza` compara copias: una escritura que deja el MISMO
    valor (el género ya normalizado) no se nota. Aquí el prod revienta si lo escriben."""

    def test_el_resolvedor_y_la_vista_previa_no_escriben_en_el_prod(self):
        casos = (prod(genero="['Mujer']"), prod(genero="mujer"),
                 prod(genero="Hombre", talla="24 MX", panel={"Género": "Sin género"}),
                 prod(genero="Hombre", genero_woo="Mujer", panel={"Género": "Sin género"}),
                 prod(color="Negro / Rosa", meta_color="Gris", panel={"Color": "Rosa"}))
        for p in casos:
            antes = copy.deepcopy(p)
            duro = congelar(p)
            for cuenta in ("BEKURA", "SANCORFASHION"):
                g = G.resolver(duro, cuenta, "t")
                self.assertNotEqual(g["codigo"], "error_interno", g["motivo"])
            self.assertEqual(G.resolver(duro, "BEKURA", "t")["estado"], "ok")
            self.assertEqual(duro, antes)
            for cuenta in ("BEKURA", "SANCORFASHION"):          # y sin congelar: byte a byte
                G.resolver(p, cuenta, "t")
            self.assertEqual(p, antes)


# ═══ la vista previa enseña el payload de la cuenta que SÍ sale (no siempre BEKURA) ═

class VistaPreviaDeLaCuentaQueSale(Base):
    def _con_la_primera_categoria_ilegible(self):
        """Falla SOLO la primera lectura de la categoría: esa cuenta queda «desconocido»."""
        lecturas = {"n": 0}

        def info(c, t):
            lecturas["n"] += 1
            return {} if lecturas["n"] == 1 else copy.deepcopy(CATS[c])
        return mock.patch.object(ml_api, "get_category_info", side_effect=info)

    def test_si_bekura_no_puede_y_sancorfashion_si_el_payload_es_el_de_sancorfashion(self):
        """Es el caso de la primera alta recomendada (una familia que solo resuelve en
        SANCORFASHION): todas las pruebas terminaban enseñando el payload de BEKURA, que es
        además la cuenta por omisión."""
        p = prod(talla="22 MX")                  # ese renglón solo existe en la guía de SANCORFASHION
        r = self.preview(p, "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual((r["ok"], r["cuenta"]), (True, "SANCORFASHION"))
        self.assertEqual(_attr(r["payload"], "SIZE_GRID_ID"), [{"id": "SIZE_GRID_ID", "value_name": "9000002"}])
        self.assertEqual(_attr(r["payload"], "SIZE_GRID_ROW_ID"),
                         [{"id": "SIZE_GRID_ROW_ID", "value_name": "9000002:1"}])
        self.assertTrue(r["avisos"][0].startswith("BEKURA · NO se publicará."))
        self.assertIn("Si confirmas, se creará SOLO en SANCORFASHION.", " ".join(r["avisos"]))
        self.assertIn("El payload de abajo es el de SANCORFASHION.", r["avisos"])
        # …y el alta hace exactamente eso.
        alta = self.crear(p)
        self.assertEqual([t for t, _ in self.posts], ["tok-SANCORFASHION"])
        self.assertEqual(G.huella({"attributes": _attr(self.posts[0][1], "SIZE_GRID_ROW_ID")}),
                         G.huella({"attributes": _attr(r["payload"], "SIZE_GRID_ROW_ID")}))
        self.assertEqual((alta["ok"], alta["parcial"]), (True, True))

    def test_la_cuenta_sin_token_se_menciona_cuando_la_otra_si_sale(self):
        sin_bekura = mock.AsyncMock(side_effect=lambda c=None: None if c == "BEKURA" else f"tok-{c}")
        with mock.patch.object(meli, "access_token_async", sin_bekura):
            r = self.preview(prod(), "BEKURA", ["BEKURA", "SANCORFASHION"])
        self.assertEqual((r["ok"], r["cuenta"]), (True, "SANCORFASHION"))
        self.assertIn(G.MSG_AVISO_SIN_TOKEN.format(cuenta="BEKURA"), r["avisos"])
        self.assertEqual(_attr(r["payload"], "SIZE_GRID_ID"), [{"id": "SIZE_GRID_ID", "value_name": "9000002"}])

    def test_si_una_cuenta_no_se_pudo_revisar_el_payload_es_el_de_la_que_si_salio(self):
        with self._con_la_primera_categoria_ilegible(), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            r = self.preview(prod(), "BEKURA", ["SANCORFASHION", "BEKURA"])
        self.assertEqual((r["ok"], r["cuenta"]), (True, "BEKURA"))
        self.assertEqual(_attr(r["payload"], "SIZE_GRID_ROW_ID")[0]["value_name"], "9000001:2")

    def test_una_cuenta_que_no_se_pudo_revisar_no_cuenta_como_bloqueada(self):
        """«desconocido» = el alta de siempre: la vista previa la enseña (sin guía) y dice
        de la otra por qué no sale; no quita «Confirmar»."""
        with self._con_la_primera_categoria_ilegible(), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            r = self.preview(prod(genero=None), "BEKURA", ["SANCORFASHION", "BEKURA"])
        self.assertEqual((r["ok"], r["cuenta"]), (True, "SANCORFASHION"))
        self.assertNotIn("bloqueo_guia", r)
        self.assertTrue(r["avisos"][0].startswith("BEKURA · NO se publicará. Guía de tallas: falta el GÉNERO."))
        self.assertEqual(_attr(r["payload"], "SIZE_GRID_ROW_ID"), [])            # el payload de siempre

    def test_si_el_payload_de_la_vista_previa_no_cuadra_se_dice_y_no_revienta(self):
        lecturas = {"n": 0}

        def info(c, t):
            lecturas["n"] += 1                    # el resolvedor lee bien; el vendor ya no
            return copy.deepcopy(CATS[c]) if lecturas["n"] == 1 else {}
        with mock.patch.object(ml_api, "get_category_info", side_effect=info):
            r = self.preview(prod(), "BEKURA", ["BEKURA"])
        self.assertFalse(r["ok"])
        self.assertIn("sin family_name", r["motivo"])
        self.assertTrue(r["avisos"][0].startswith("BEKURA · guía de tallas 9000001"))


# ═════ el cable entre el modal y el adaptador (iba siempre doblado) ═══════════

class CableDelModal(Base):
    """`publicar._payload_crear_ml` no lo ejecutaba ninguna prueba: en todas era un
    `AsyncMock`. Que ignorara `cuentas`, o que la llamada volviera a ser la de antes de este
    cambio (un mal merge en un archivo que tocan varios chats), pasaba en verde."""

    def test_payload_crear_ml_pasa_las_cuentas_donde_se_va_a_crear(self):
        doble = mock.AsyncMock(return_value={"ok": True})
        req = {"sku": "INV-0001", "wc_id": 1, "campos": {}}
        with mock.patch.object(PR, "preview_crear_ml", doble), \
                mock.patch.object(wp_db, "disponible", return_value=True):
            asyncio.run(publicar._payload_crear_ml(req))                        # noqa: SLF001
            self.assertEqual(doble.await_args.args[3:], ("BEKURA", ["BEKURA", "SANCORFASHION"]))
            asyncio.run(publicar._payload_crear_ml(req, ["SANCORFASHION"]))     # noqa: SLF001
            self.assertEqual(doble.await_args.args[3:], ("BEKURA", ["SANCORFASHION"]))

    def test_de_punta_a_punta_el_modal_trae_una_linea_por_cuenta(self):
        """Sin doblar `_payload_crear_ml` ni `preview_crear_ml`: del modal a la guía."""
        p = prod()
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=[]), \
                mock.patch.object(wp_db, "disponible", return_value=True), \
                mock.patch.object(PR, "construir_prod", return_value=p):
            r = asyncio.run(publicar._preview_ml({"sku": p["sku"], "wc_id": 1, "campos": {}}))   # noqa: SLF001
        self.assertTrue(r["ok"])
        lineas = [a for a in r["avisos"] if "· guía de tallas" in a]
        self.assertEqual([linea.split(" · ")[0] for linea in lineas], ["BEKURA", "SANCORFASHION"])
        self.assertEqual(_attr(r["payload"], "SIZE_GRID_ROW_ID")[0]["value_name"], "9000001:2")

    def test_con_una_sola_cuenta_eliminada_se_resuelve_solo_la_que_se_re_crea(self):
        """`_confirmar_ml` (sin vivas) crea SOLO en las eliminadas: la vista previa igual."""
        doble = mock.AsyncMock(return_value={"ok": True, "payload": {"pictures": []}, "avisos": [],
                                             "prod": {"sku": "INV-0001"}})
        pubs = [{"cuenta": "SANCORFASHION", "item_id": "MLM2"}]
        estados = mock.AsyncMock(return_value={"SANCORFASHION": {"vivo": False, "status": "closed"}})
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=pubs), \
                mock.patch.object(publicar, "_estados_items_ml", estados), \
                mock.patch.object(publicar, "_payload_crear_ml", doble):
            r = asyncio.run(publicar._preview_ml({"sku": "INV-0001", "campos": {"titulo": "Tenis"}}))   # noqa: SLF001
        self.assertEqual(r["modo"], "crear")
        self.assertEqual(doble.await_args.args[1], ["SANCORFASHION"])


# ═══ de poco riesgo (equivalentes hoy), pero baratos de fijar ═════════════════

class DePocoRiesgo(Base):
    def test_si_lo_escrito_en_el_estudio_empata_con_dos_renglones_tampoco_se_elige(self):
        gemelos = _guia("9000003", "SNEAKERS", "Hombre", "339666", [
            _renglon("9000003", 1, "26 MX", 40, 26), _renglon("9000003", 4, "26 MX", 40, 26)])
        with mock.patch.object(G.requests, "get", return_value=_una_guia(gemelos)):
            g = G.resolver(prod(talla="40", genero="Hombre", panel={"Talla": "26 MX"}), "BEKURA", "t")
        self.assertEqual(g["codigo"], "talla_ambigua")

    def test_la_talla_eu_tambien_se_lee_del_nombre_si_no_trae_struct(self):
        """452 de 452 EU_SIZE reales traen `struct.number`; el respaldo por nombre existe y
        no tenía prueba."""
        sin_struct = copy.deepcopy(GUIAS["9000001"])
        for r in sin_struct["rows"]:
            for a in r["attributes"]:
                if a["id"] == "EU_SIZE":
                    a["values"] = [{"name": a["values"][0]["name"]}]       # «37 EU», «38,5 EU»
        with mock.patch.object(G.requests, "get", return_value=_una_guia(sin_struct)):
            self.assertEqual(G.resolver(prod(), "BEKURA", "t")["renglon_id"], "9000001:2")
            self.assertEqual(G.resolver(prod(talla="38.5"), "BEKURA", "t")["renglon_id"], "9000001:5")

    def test_el_contexto_de_la_guardia_tambien_se_quita_cuando_el_alta_SALE_BIEN(self):
        """En producción corre dentro de `asyncio.to_thread` (que copia el contexto), así que
        hoy no se nota; llamada directa, sí."""
        PR.configurar()
        p = prod()
        guia = G.resolver(p, "BEKURA", "tok-BEKURA")
        r = PR._publicar_con_guia(dict(p), "tok-BEKURA", "BEKURA", guia)        # noqa: SLF001
        self.assertTrue(r["success"])
        self.assertIsNone(PR._CTX_ALTA.get())                                   # noqa: SLF001
        self.assertEqual(len(self.posts), 1)

    def test_el_vendor_recibe_una_COPIA_del_prod_tambien_en_el_alta_con_guia(self):
        """Hoy el vendor no escribe en `prod`, pero las dos cuentas comparten el dict: si un
        día lo hace, la segunda cuenta no debe ver lo que dejó la primera."""
        p = prod()
        antes = copy.deepcopy(p)
        vistos = []

        def publish(prod_, token, dry_run, cuenta):
            vistos.append(prod_["title"])
            prod_["title"] = f"TOCADO POR {cuenta}"          # un vendor que escribe en su prod
            return {"success": True, "ml_item_id": f"MLM-{cuenta}", "ml_status": 201}
        with mock.patch.object(publisher_core, "publish_product", side_effect=publish):
            self.assertTrue(self.crear(p)["ok"])
        self.assertEqual(vistos, [antes["title"], antes["title"]])
        self.assertEqual(p, antes)

    def test_sin_categoria_no_aplica_y_no_lee_nada(self):
        with mock.patch.object(G.requests, "get") as get, \
                mock.patch.object(ml_api, "get_category_info") as info:
            g = G.resolver(prod(cat=""), "BEKURA", "t")
        self.assertEqual((g["estado"], g["codigo"], g["category_id"]), ("no_aplica", "", ""))
        get.assert_not_called()
        info.assert_not_called()


# ═══ las etiquetas y la bitácora NUNCA rompen un alta (ni con una cuenta ya creada) ═

class LasEtiquetasNoRompenElAlta(Base):
    """`es_rechazo_de_guia`, `es_rechazo_definitivo` y `etiqueta_error` corren DENTRO de
    `crear_ml`, después de que la primera cuenta ya creó su publicación. Si una respuesta
    rara de Mercado Libre las hiciera lanzar, el alta reventaba y el modal decía «Error de
    conexión» con una publicación viva que nadie anotó."""

    def _revientan(self):
        return (mock.patch.object(G, "es_rechazo_de_guia", side_effect=RuntimeError("bum")),
                mock.patch.object(G, "es_rechazo_definitivo", side_effect=RuntimeError("bum")),
                mock.patch.object(G, "etiqueta_error", side_effect=RuntimeError("bum")))

    def test_fuera_de_moda_el_alta_termina_con_lo_de_siempre(self):
        rechazo = _rechazo(_causa("item.price.invalid", message="price too low"))
        self.respuestas = [({"id": "MLM1"}, 201), rechazo, rechazo, rechazo]
        taladro = prod(cat=CAT_TALADRO, talla=None, genero=None)
        a, b, c = self._revientan()
        with a, b, c, self.assertLogs("omnicanal.publicar_ready", "ERROR") as logs:
            r = self.crear(taladro)                              # no lanza
        primera, segunda = r["resultados"]
        self.assertEqual((primera["ok"], primera["item_id"]), (True, "MLM1"))    # la creada no se pierde
        self.assertEqual((segunda["ok"], segunda["intentos"]), (False, 1))     # v0.682.0: no se repite
        self.assertIn("price too low", segunda["error"])                         # y dice por qué
        self.assertTrue(any("se sigue con lo de siempre" in m for m in logs.output))

    def test_en_moda_el_alta_termina_y_se_queda_la_etiqueta_del_vendor(self):
        rechazo = _rechazo(_causa("item.attribute.invalid", message="Attribute [MAIN_COLOR] is not valid"))
        self.respuestas = [({"id": "MLM1"}, 201), rechazo, rechazo, rechazo]
        a, b, c = self._revientan()
        with a, b, c, self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            r = self.crear(prod())                               # no lanza
        primera, segunda = r["resultados"]
        self.assertEqual((primera["ok"], primera["item_id"], primera["pausado"]), (True, "MLM1", True))
        self.assertEqual((segunda["ok"], segunda["intentos"]), (False, 1))     # v0.682.0: no se repite
        self.assertIn("MAIN_COLOR", segunda["error"])                            # y dice por qué
        self.assertFalse(r["ok"])

    def test_sin_romper_devuelve_la_omision_y_deja_el_traceback(self):
        self.assertEqual(PR._sin_romper(lambda x: x + 1, 0, 1), 2)              # noqa: SLF001

        def falla(_):
            raise ValueError("bum")
        with self.assertLogs("omnicanal.publicar_ready", "ERROR") as logs:
            self.assertEqual(PR._sin_romper(falla, "omisión", 1), "omisión")    # noqa: SLF001
        self.assertIn("falla reventó", logs.output[0])
        self.assertIn("ValueError: bum", logs.output[0])


# ═════════════════════════════════════════════════════════════════════════════
# LO QUE LA SIMPLIFICACIÓN DEJÓ SIN PRUEBA (revisión del 9-oct-2026).
# 80 versiones ingenuas de lo que cambió al quitar las variables; 13 pasaban la suite en
# verde. Cada prueba de abajo dice cuál tumba.
# ═════════════════════════════════════════════════════════════════════════════

# El mapping REAL del vendor, leído al importar (antes de que `Base.setUp` lo sustituya).
MAPPING_REAL = {c: dict(m) for c, m in size_chart_mapping.CHARTS_BY_ACCOUNT.items()}


def _falla(fallo):
    """`requests.get` que falla: un status (Mock) o una excepción de red."""
    if isinstance(fallo, Exception):
        return mock.patch.object(G.requests, "get", side_effect=fallo)
    return mock.patch.object(G.requests, "get", return_value=fallo)


# ═════════ familias en revisión: el padre por PREFIJO, y el producto simple ═════════

class FamiliaPorElPadreYSimple(Base):
    def test_el_padre_frena_por_prefijo_y_como_venga_escrito(self):
        """Tumba «el padre solo por igualdad» (FAM-16) y «el SKU del padre sin normalizar»
        (FAM-17): la única prueba del padre usaba `padre_sku == "CALZ-0129"`, exacto y en
        mayúsculas. La regla es la MISMA para los dos SKUs: igual, o «<familia>-…»."""
        f = G.familia_en_revision
        self.assertEqual(f({"sku": "OTRO-0001-NEG-40", "padre_sku": "CALZ-0129-NEG"}), "CALZ-0129")
        self.assertEqual(f({"sku": "OTRO-0001-NEG-40", "padre_sku": " calz-0129 "}), "CALZ-0129")
        self.assertEqual(f({"sku": "", "padre_sku": "calz-0200-neg"}), "CALZ-0200")
        # El falso amigo también por el padre, con sufijo.
        self.assertIsNone(f({"sku": "OTRO-0001-NEG-40", "padre_sku": "CALZ-01290-NEG"}))
        # …y por el camino del resolvedor (tenis de Mujer, talla 37: resolvería).
        p = prod()
        p["padre_sku"] = "calz-0134-neg"
        g = G.resolver(p, "BEKURA", "t")
        self.assertEqual((g["estado"], g["codigos"]), ("bloqueado", ["guia_en_revision"]))
        self.assertEqual(g["motivo"], G.MSG_EN_REVISION.format(familia="CALZ-0134"))

    def test_un_producto_simple_de_la_familia_tambien_se_frena(self):
        """Tumba «solo se frenan las variaciones» (R2-01): todas las pruebas del resolvedor
        y del alta usaban una VARIACIÓN (`prod()` trae `padre_wc_id`). `CALZ-0089` tiene un
        solo pendiente: si es un producto simple, es justo este caso."""
        simple = prod(variante=False)                     # resolvería: Color y Talla de una opción
        self.assertEqual(G.resolver(simple, "BEKURA", "t")["estado"], "ok")
        simple["sku"] = "CALZ-0089"
        self.assertFalse(G.es_variacion(simple))
        with mock.patch.object(G.requests, "get") as get:
            g = G.resolver(simple, "BEKURA", "t")
            r = self.crear(simple)
            get.assert_not_called()
        self.assertEqual((g["estado"], g["codigos"]), ("bloqueado", ["guia_en_revision"]))
        self.assertFalse(r["ok"])
        self.assertEqual(self.posts, [])

    def test_una_tabla_de_tallas_propia_ya_no_frena_fuera_de_las_seis_familias(self):
        """Decisión 1 de Brandon (tabla estándar): tumba «todo producto con
        `_kubera_size_chart` sigue en revisión» (R2-02). La prueba de las ROP solo cambiaba
        el SKU; la tabla propia vive en la META."""
        ropa = prod()
        ropa["sku"], ropa["padre_sku"] = "ROP-0123-NEG-37", "ROP-0123"
        ropa["meta"] = {"_kubera_size_chart": '[{"talla": "37", "largo": "24"}]'}
        g = G.resolver(ropa, "BEKURA", "t")
        self.assertEqual((g["estado"], g["guia_id"]), ("ok", "9000001"))
        self.assertTrue(self.crear(ropa, ["BEKURA"])["ok"])
        self.assertEqual(len(self.posts), 1)


# ═════════ tenis de Hombre: en las DOS cuentas (la de riesgo es SANCORFASHION) ═════════

class TenisDeHombreEnLasDosCuentas(Base):
    def test_sale_en_cada_cuenta_con_su_guia_registrada(self):
        """Tumba «tenis de Hombre sigue frenado solo en SANCORFASHION» (TEN-02): en `MAPA`
        esa cuenta no tiene guía de Hombre, así que la prueba de la decisión 3 solo
        ejercitaba BEKURA —y la cuenta donde la decisión pesa (27 vivas con otra guía) es
        la otra."""
        sancor = _guia("9000004", "SNEAKERS", "Hombre", "339666", [      # noqa: SLF001
            _renglon("9000004", 1, "25 MX", 40, 25),                     # noqa: SLF001
            _renglon("9000004", 2, "26 MX", 41, 26)])                    # noqa: SLF001
        hombre = prod(genero="Hombre", talla="41")
        with mock.patch.dict(size_chart_mapping.CHARTS_BY_ACCOUNT["SANCORFASHION"],
                             {"SNEAKERS:Hombre": "9000004"}), \
                mock.patch.dict(GUIAS, {"9000004": sancor}):
            a = G.resolver(hombre, "BEKURA", "t")
            b = G.resolver(hombre, "SANCORFASHION", "t")
            r = self.crear(hombre)
        self.assertEqual((a["estado"], a["guia_id"], a["size"]), ("ok", "9000003", "27 MX"))
        self.assertEqual((b["estado"], b["guia_id"], b["size"]), ("ok", "9000004", "26 MX"))
        self.assertTrue(r["ok"])
        self.assertNotIn("parcial", r)
        por_token = dict(self.posts)
        self.assertEqual(sorted(por_token), ["tok-BEKURA", "tok-SANCORFASHION"])
        self.assertEqual(_attr(por_token["tok-SANCORFASHION"], "SIZE_GRID_ROW_ID"),   # noqa: SLF001
                         [{"id": "SIZE_GRID_ROW_ID", "value_name": "9000004:2"}])
        self.assertEqual(_attr(por_token["tok-SANCORFASHION"], "GENDER"),             # noqa: SLF001
                         [{"id": "GENDER", "value_id": "339666", "value_name": "Hombre"}])

    def test_el_mapping_real_tiene_guia_de_tenis_de_hombre_en_las_dos_cuentas(self):
        """Ancla de configuración (sin ids: el repo es público): «se usa la guía
        registrada» solo es verdad mientras haya una registrada en cada cuenta."""
        for cuenta in ("BEKURA", "SANCORFASHION"):
            with self.subTest(cuenta=cuenta):
                self.assertTrue((MAPPING_REAL.get(cuenta) or {}).get("SNEAKERS:Hombre"))


# ═════════ género: lo que la reescritura dejó sin prueba ═════════

class GeneroLoQueFaltaba(Base):
    def test_una_fuente_con_un_genero_valido_y_otro_que_no_lo_es_no_decide(self):
        """Tumba «dentro de una fuente se ignoran los valores que no son de ML» (GEN-11):
        ninguna prueba mezclaba un válido y un inválido en la MISMA fuente. «Mujer | Dama»
        en WooCommerce son DOS opciones: no decide (la regla es UNA sola opción)."""
        solo = G.resolver(prod(genero=None, genero_woo=["Mujer", "Dama"]), "BEKURA", "t")
        self.assertEqual((solo["estado"], solo["codigo"]), ("bloqueado", "genero_invalido"))
        self.assertIn("el género «Dama» (atributo Género de WooCommerce)", solo["motivo"])
        # Con la meta de la IA detrás decide la META, no «la opción válida» de WooCommerce.
        con_meta = G.resolver(prod(genero="Hombre", talla="41", genero_woo=["Mujer", "Dama"]),
                              "BEKURA", "t")
        self.assertEqual((con_meta["estado"], con_meta["genero"], con_meta["genero_rango"]),
                         ("ok", "Hombre", 3))
        self.assertEqual(con_meta["origen_genero"],
                         G.ORIGEN_META + "; el atributo Género de WooCommerce trae «Mujer», «Dama» "
                                         "y no decide")
        self.assertTrue(self.crear(prod(genero="Hombre", talla="41", genero_woo=["Mujer", "Dama"]),
                                   ["BEKURA"])["ok"])
        self.assertEqual(_attr(self.posts[0][1], "GENDER"),                           # noqa: SLF001
                         [{"id": "GENDER", "value_id": "339666", "value_name": "Hombre"}])

    def test_el_titulo_no_es_una_fuente_del_genero(self):
        """Tumba «sin ninguna fuente, el título decide» (GEN-15). El título ya no bloquea,
        pero tampoco DECIDE: las fuentes son tres. Todas las pruebas de `sin_genero` usaban
        un título que no nombra ningún género."""
        for titulo in ("Tenis para HOMBRE gris claro", "Tenis de dama casual"):
            with self.subTest(titulo=titulo):
                p = prod(genero=None, titulo=titulo)
                g = G.resolver(p, "BEKURA", "t")
                self.assertEqual((g["estado"], g["codigo"]), ("bloqueado", "sin_genero"))
                self.assertFalse(self.crear(p)["ok"])
        self.assertEqual(self.posts, [])
        ml_api.preupload_picture.assert_not_called()
        # Tampoco desempata a un atributo de WooCommerce con dos opciones.
        dos = G.decidir_genero(prod(genero=None, genero_woo=["Mujer", "Hombre"],
                                    titulo="Tenis para hombre"))
        self.assertEqual((dos["genero"], dos["codigo"]), (None, "sin_genero"))

    def test_una_fuente_posterior_que_dice_lo_mismo_no_se_anota(self):
        """Tumba «toda fuente posterior se anota como "decía"» (GEN-17): es el caso MÁS
        común (atributo de Woo + la misma meta de la IA) y la línea diría «…; la meta de
        Crear decía «Mujer»», que se lee como una contradicción que no existe."""
        g = G.resolver(prod(genero="Mujer", genero_woo="Mujer"), "BEKURA", "t")
        self.assertEqual((g["estado"], g["genero_rango"], g["origen_genero"]), ("ok", 1, G.ORIGEN_WOO))
        f = G.resolver(prod(genero="['mujer']", panel={"Género": "Mujer"}), "BEKURA", "t")
        self.assertEqual((f["genero_rango"], f["origen_genero"]), (2, G.ORIGEN_FORMULARIO))
        # De una fuente posterior con dos valores solo se dice el que NO coincide.
        metas = prod(genero="Mujer", genero_woo="Mujer")
        metas["ml_attrs"]["gender"] = "Hombre"
        self.assertEqual(G.decidir_genero(metas)["origen"],
                         G.ORIGEN_WOO + "; la meta de Crear decía «Hombre»")


# ═════════ categoría ilegible / dominio fuera de la lista: el alta de SIEMPRE ═════════

class SinGuiaResueltaEsElAltaDeSiempre(Base):
    """`con_guia` y las dos condiciones que antes llevaban `guia_activa and (…)` se
    reescribieron al quitar la bandera. Con la categoría ilegible (`desconocido`) o un
    dominio que no está en la lista (`no_aplica`) todo tiene que seguir como siempre,
    salvo UNA cosa: un rechazo de la guía no se reintenta y se explica."""

    def _ilegible_para_el_resolvedor(self):
        lecturas = {"n": 0}

        def info(c, t):
            lecturas["n"] += 1                    # falla SOLO la primera: la del resolvedor
            return {} if lecturas["n"] == 1 else copy.deepcopy(CATS[c])
        return mock.patch.object(ml_api, "get_category_info", side_effect=info)

    def test_con_la_categoria_ilegible_no_hay_guardia_ni_etiqueta_nueva_y_se_reintenta_igual(self):
        """Tumba «desconocido se trata como alta con guía» (VEN-10): la única prueba del alta
        con la categoría ilegible usaba un rechazo que el vendor ya corta solo."""
        rechazo = _rechazo(_causa("item.price.invalid", message="price too low"))   # noqa: SLF001
        self.respuestas = [rechazo, rechazo, rechazo]
        with self._ilegible_para_el_resolvedor(), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        # Lo de siempre de main: un 400 con causas no se repite (v0.682.0) y se explica.
        self.assertEqual((fila["intentos"], len(self.posts)), (1, 1))
        self.assertIn("price too low", fila["error"])
        self.assertIsNot(ml_api.create_item, PR._create_item_con_guia)       # noqa: SLF001 — ni la guardia

    def test_con_la_categoria_ilegible_ni_se_mira_ni_se_pone_el_candado_de_pausa(self):
        """La otra mitad de VEN-10: una alta «desconocido» que queda sin pausar no pone el
        candado de moda (no se sabe que sea de moda), y uno ya puesto no la frena."""
        with self._ilegible_para_el_resolvedor(), \
                mock.patch.object(PR, "asegurar_pausado", return_value=dict(NO_PAUSADA)), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            r = self.crear(prod(), ["BEKURA"])
        self.assertTrue(r["resultados"][0]["ok"])
        self.assertEqual(PR._MODA_ACTIVA, {})                                 # noqa: SLF001

    def test_con_la_categoria_ilegible_un_rechazo_de_guia_no_se_reintenta_y_se_explica(self):
        """Tumba «`es_rechazo_de_guia` solo cuenta con guía resuelta» (VEN-12 y R2-04): con
        `grid_id.values` el vendor corta solo (NEEDS_MANUAL_CONFIG) y la condición nunca se
        ejercitaba; con un código que el vendor NO conoce, sí."""
        rechazo = _rechazo(_causa("invalid.fashion_grid.size.values",               # noqa: SLF001
                                      message="Attribute [SIZE] is not valid"))
        self.respuestas = [rechazo, rechazo, rechazo]
        with self._ilegible_para_el_resolvedor(), \
                self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            fila = self.crear(prod(), ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["ok"], fila["intentos"], len(self.posts)), (False, 1, 1))
        self.assertIn("GUIA_DE_TALLAS (invalid.fashion_grid.size.values)", fila["error"])
        self.assertIn("no se pudo leer la categoría MLM900001", fila["error"])

    def test_un_dominio_fuera_de_la_lista_que_ml_rechaza_por_guia_dice_que_hay_que_agregarlo(self):
        """El «falla hacia lo de hoy» de `DOMINIOS_CON_GUIA` (ML agrega un dominio): un
        intento, y el mensaje dice qué hacer. No lo ejercitaba ninguna prueba."""
        rechazo = _rechazo(_causa("missing.fashion_grid.row_id.values",            # noqa: SLF001
                                      message="grid row required"))
        self.respuestas = [rechazo, rechazo, rechazo]
        taladro = prod(cat=CAT_TALADRO, talla=None, genero=None)
        fila = self.crear(taladro, ["BEKURA"])["resultados"][0]
        self.assertEqual((fila["ok"], fila["intentos"], len(self.posts)), (False, 1, 1))
        self.assertIn("GUIA_DE_TALLAS (missing.fashion_grid.row_id.values)", fila["error"])
        self.assertIn("el dominio ELECTRIC_DRILLS no está en la lista de dominios con guía",
                      fila["error"])
        self.assertIsNot(ml_api.create_item, PR._create_item_con_guia)       # noqa: SLF001 — ni la guardia


# ═════════ sin red: tampoco la vista previa, ni con la caché vencida ═════════

class SinRedSigueBloqueando(Base):
    def test_si_la_guia_no_se_puede_leer_la_vista_previa_no_ofrece_confirmar(self):
        """Tumba «con la guía ilegible la vista previa enseña el payload del vendor»
        (RED-05): el fallo de red solo se probaba en el resolvedor y en el alta."""
        for fallo in (mock.Mock(status_code=503), OSError("sin red")):
            G._guias.clear()                                                  # noqa: SLF001
            with _falla(fallo), self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
                r = self.preview(prod(), "BEKURA", ["BEKURA", "SANCORFASHION"])
            self.assertEqual((r["ok"], r.get("bloqueo_guia")), (False, True))
            self.assertNotIn("payload", r)
            self.assertIn("BEKURA: Guía de tallas: no se pudo leer la guía 9000001", r["motivo"])
            self.assertIn("SANCORFASHION: Guía de tallas: no se pudo leer la guía 9000002", r["motivo"])

    def test_con_la_cache_vencida_un_fallo_al_leer_la_guia_sigue_bloqueando(self):
        """Tumba «sin red se usa la copia vencida de la caché» (RED-06 y RED-07): todas las
        pruebas del fallo de red vaciaban la caché antes. A los diez minutos la copia ya
        no vale: si Mercado Libre no entrega la guía, no se publica."""
        t0 = 1000.0
        with mock.patch.object(G.time, "monotonic", return_value=t0):
            self.assertEqual(G.resolver(prod(), "BEKURA", "t")["estado"], "ok")   # llena la caché
        for fallo in (mock.Mock(status_code=503), mock.Mock(status_code=429), OSError("sin red")):
            with mock.patch.object(G.time, "monotonic", return_value=t0 + G.TTL_GUIA_S + 1), \
                    _falla(fallo) as get, self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
                g = G.resolver(prod(), "BEKURA", "t")
            self.assertEqual((g["estado"], g["codigo"], get.call_count),
                             ("bloqueado", "guia_ilegible", 2))
        # …y por el camino del alta (la copia vencida sigue en `_guias`: se le atrasa la hora).
        cuando, guia = G._guias["9000001"]                                    # noqa: SLF001
        G._guias["9000001"] = (G.time.monotonic() - G.TTL_GUIA_S - 1, guia)   # noqa: SLF001
        with _falla(OSError("sin red")), self.assertLogs("omnicanal.ml_guia_tallas", "WARNING"):
            r = self.crear(prod(), ["BEKURA"])
        self.assertEqual(self.posts, [])
        self.assertIn("no se pudo leer la guía 9000001", r["resultados"][0]["error"])
        self.assertEqual(r["resultados"][0]["intentos"], 0)


if __name__ == "__main__":
    unittest.main()
