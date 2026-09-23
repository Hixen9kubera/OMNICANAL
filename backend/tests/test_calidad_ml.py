"""Calidad y experiencia de compra de publicaciones ML
(`services/calidad_ml.py`, migración 0053: `enrich.listing_health` y su serie
diaria `enrich.listing_health_hist`).

Sin red y sin base: ML se simula con `httpx.MockTransport` y la base con
parches sobre `sdb`. Los crudos de `normalizar` son recortes de respuestas
REALES de `GET /item/{id}/performance` (22-sep-2026, una "medium"/"Estándar"
y una "good"/"Profesional"), con las variables justas para cubrir pendiente,
completada y score flotante. Los de `normalizar_experiencia` son recortes de
las respuestas REALES de la experiencia de compra del mismo día (roja,
naranja, verde y gris), escritos aquí: la prueba no lee ningún archivo.

    cd backend && python -m unittest tests.test_calidad_ml -v
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import io
import json
import logging
import socket
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import calidad_ml  # noqa: E402

ML = "https://api.mercadolibre.com"


def _regla(key, status, title, label, link):
    return {"key": key, "status": status, "progress": 1 if status == "COMPLETED" else 0,
            "mode": "OPPORTUNITY", "calculated_at": "2026-09-22T01:39:59Z",
            "wordings": {"title": title, "label": label, "link": link}}


def _var(key, status, score, title, regla):
    return {"key": key, "status": status, "score": score,
            "calculated_at": "2026-09-22T01:39:59Z", "title": title, "rules": [regla]}


# ── Recorte REAL de la "medium" / "Estándar" (MLM3451327389) ────────────────
CRUDO_MEDIUM = {
    "entity_type": "USER_PRODUCT",
    "entity_id": "MLMU5081808139",
    "score": 60,
    "level": "medium",
    "calculated_at": "2026-09-22T01:40:00.109Z",
    "level_wording": "Estándar",
    "buckets": [
        {"key": "USER_PRODUCT", "type": "USER_PRODUCT", "status": "PENDING",
         "score": 89.41177, "title": "Datos del producto",
         "calculated_at": "2026-09-22T01:39:59Z",
         "variables": [
             _var("UP_SHORTS", "PENDING", 0, "Crea un video para no perder ventas",
                  _regla("UP_HAS_SHORTS", "PENDING",
                         "Los videos tienen que ser de hasta un minuto, en formato "
                         "vertical y se van a publicar en todas tus variantes.",
                         "Crear video",
                         "https://www.mercadolibre.com.mx/video/creator/upload?"
                         "user_product_id=MLMU5081808139&item_id=MLM3451327389")),
             _var("UP_TITLE", "COMPLETED", 100,
                  "Mejora el título o usa nuestra sugerencia para atraer a más compradores",
                  _regla("UP_TITLE_LENGTH_MIN", "COMPLETED",
                         "Suma más detalles, el título debe tener al menos 3 palabras.",
                         "Agregar detalles",
                         "https://www.mercadolibre.com.mx/publicaciones/MLMU5081808139/"
                         "modificar/omni/variation/dominio/title-default")),
         ]},
        {"key": "MLM3451327389", "type": "ITEM", "status": "PENDING", "score": 18,
         "title": "Condiciones de venta", "calculated_at": "2026-09-22T01:39:59Z",
         "variables": [
             _var("UP_FREE_SHIPPING", "PENDING", 0,
                  "Ofrece envío gratis para que tu publicación sea más competitiva",
                  _regla("UP_HAS_FREE_SHIPPING", "PENDING",
                         "Ofrece envío gratis para que tu publicación sea más competitiva.",
                         "Ofrecer envío",
                         "https://www.mercadolibre.com.mx/syi/core/modify?"
                         "taskId=shipping_task&itemId=MLM3451327389")),
             _var("UP_PROMOTIONS", "PENDING", 0,
                  "Participa de una promoción para recibir más visitas",
                  _regla("UP_HAS_PROMOTIONS", "PENDING",
                         "Participa de una promoción para recibir más visitas",
                         "Participar",
                         "https://www.mercadolibre.com.mx/publicaciones/listado/promos?"
                         "search=MLM3451327389")),
             _var("UP_FINANCING", "COMPLETED", 100,
                  "Elige Premium para ofrecer meses sin intereses y tener una "
                  "publicación más competitiva",
                  _regla("UP_LISTING_TYPE_PREMIUM", "COMPLETED",
                         "Elige Premium para ofrecer meses sin intereses y tener una "
                         "publicación más competitiva.", "Sumar meses",
                         "https://www.mercadolibre.com.mx/syi/core/modify?"
                         "taskId=listing_types_task&itemId=MLM3451327389")),
             _var("UP_PRICE", "PENDING", 0,
                  "Optimiza el precio para que sea más competitivo",
                  _regla("UP_PRICE_PER_QUANTITY", "PENDING",
                         "Agrega precios mayoristas", "Agregar precios mayoristas",
                         "https://www.mercadolibre.com.mx/syi/core/modify?"
                         "taskId=prices_task&itemId=MLM3451327389")),
         ]},
    ],
}

# ── Recorte REAL de la "good" / "Profesional" (MLM3474705047) ───────────────
CRUDO_GOOD = {
    "entity_type": "USER_PRODUCT",
    "entity_id": "MLMU5126028589",
    "score": 81,
    "level": "good",
    "calculated_at": "2026-09-22T03:06:56.654Z",
    "level_wording": "Profesional",
    "buckets": [
        {"key": "USER_PRODUCT", "type": "USER_PRODUCT", "status": "PENDING",
         "score": 88.607605, "title": "Datos del producto",
         "calculated_at": "2026-09-22T03:06:56Z",
         "variables": [
             _var("UP_TECHNICAL_SPECIFICATIONS_MAIN", "COMPLETED", 100,
                  "Corrige las características para recibir menos preguntas y devoluciones",
                  _regla("UP_TS_MAIN_QUANTITY", "COMPLETED",
                         "Completa las características principales.", "Completar",
                         "https://www.mercadolibre.com.mx/publicaciones/MLMU5126028589/"
                         "modificar/omni/variation/dominio/techspecs-primary")),
             _var("UP_SHORTS", "PENDING", 0, "Crea un video para no perder ventas",
                  _regla("UP_HAS_SHORTS", "PENDING",
                         "Los videos tienen que ser de hasta un minuto, en formato "
                         "vertical y se van a publicar en todas tus variantes.",
                         "Crear video",
                         "https://www.mercadolibre.com.mx/video/creator/upload?"
                         "user_product_id=MLMU5126028589&item_id=MLM3474705047")),
         ]},
        {"key": "MLM3474705047", "type": "ITEM", "status": "PENDING",
         "score": 70.58824, "title": "Condiciones de venta",
         "calculated_at": "2026-09-22T03:06:56Z",
         "variables": [
             _var("UP_FREE_SHIPPING", "COMPLETED", 100,
                  "Ofrece envío gratis para que tu publicación sea más competitiva",
                  _regla("UP_HAS_FREE_SHIPPING", "COMPLETED",
                         "Ofrece envío gratis para que tu publicación sea más competitiva.",
                         "Ofrecer envío",
                         "https://www.mercadolibre.com.mx/syi/core/modify?"
                         "taskId=shipping_task&itemId=MLM3474705047")),
             _var("UP_PROMOTIONS", "PENDING", 0,
                  "Participa de una promoción para recibir más visitas",
                  _regla("UP_HAS_PROMOTIONS", "PENDING",
                         "Participa de una promoción para recibir más visitas",
                         "Participar",
                         "https://www.mercadolibre.com.mx/publicaciones/listado/promos?"
                         "search=MLM3474705047")),
         ]},
    ],
}

# Los dos 400 reales.
PAUSADA_400 = {"message": "Entity not calculated: Only status active is supported",
               "error": "bad_request", "status": 400}
CATALOGO_400 = {"message": "Entity not calculated: Product items are not supported",
                "error": "bad_request", "status": 400}


# ── Experiencia de compra: recortes REALES (22-sep-2026) de
#    GET /reputation/items/{id}/purchase_experience/integrators?locale=es_MX ──
_SUBTITULOS_GENERICOS = [
    {"order": 0, "text": "Comparamos tu desempeño con otros vendedores en base a:\n"
                         " Reclamos que recibiste\n Demoras en el envío\n"
                         " Cancelaciones que tú realices"},
    {"order": 1, "text": "También analizamos preguntas y respuestas, opiniones y tu "
                         "mensajería para identificar problemas con el producto."},
]


def _exp(reputation, consecuencia="", accion="", razon=(), recomendaciones=(),
         ia="Generado por inteligencia artificial", status=None, up_id="MLMU1"):
    crudo = {
        "actions": [{"order": 0, "text": "Modificar publicación"}],
        "ai_generated": {"order": 0, "text": ia},
        "consequence": {"title": {"order": 0, "text": consecuencia}},
        "freeze": {"text": ""},
        "principal_actionable": {"order": 0, "text": accion},
        "reasoning": {"title": {"order": 0, "text": "¿Por qué tengo este desempeño?"
                                if razon else ""},
                      "subtitles": [{"order": i, "text": t} for i, t in enumerate(razon)]},
        "recommendations": {"title": {"order": 0, "text": "Recomendaciones para mejorar:"
                                      if recomendaciones else ""},
                            "subtitles": [{"order": i, "text": t}
                                          for i, t in enumerate(recomendaciones)]},
        "reputation": reputation,
        "subtitles": list(_SUBTITULOS_GENERICOS),
        "title": {"text": "Experiencia de compra"},
        "up_id": up_id,
    }
    if status is not None:
        crudo["status"] = status
    return crudo


EXP_ROJO = _exp(
    {"color": "red", "text": "Mala", "value": 30},
    consecuencia="Tienes muy baja exposición. Podríamos anular tu publicación si "
                 "sigues brindando mala experiencia.",
    accion="Aclara en la publicación con fotos y accesorios.",
    razon=["Como no tenemos suficiente información, lo calculamos a partir de tus "
           "ventas de productos en la misma categoría. Tuviste reclamos por problemas "
           "de producto por encima del promedio de la categoría."],
    recomendaciones=["Aclara en la publicación con fotos detalladas y la lista de "
                     "accesorios incluidos para que coincida con lo que envías."],
    status={"id": "active"}, up_id="MLMU3994413616")
EXP_NARANJA = _exp(
    {"color": "orange", "text": "Media", "value": 65},
    consecuencia="La experiencia que brinda tu publicación afecta tu exposición.",
    accion="Evita enviar unidades que no coincidan con la publicación.",
    razon=["Como no tenemos suficiente información, lo calculamos a partir de tus "
           "ventas de productos en la misma categoría. En esta categoría recibiste "
           "más reclamos que el promedio."],
    recomendaciones=["Revisa las unidades antes de enviarlas al depósito de Full.",
                     "Actualiza la publicación para que el voltaje y el modelo "
                     "coincidan con lo que envías."],
    status={"id": "active"}, up_id="MLMU3897466548")
EXP_VERDE = _exp(
    {"color": "green", "text": "Buena", "value": 75},
    consecuencia="Asegúrate de evitar problemas en tus próximas ventas para cuidar "
                 "tu exposición.",
    accion="Evita enviar unidades 110V al depósito de Full.",
    razon=["Tuviste reclamos por debajo del umbral de la categoría. Por eso la "
           "evaluación final fue Buena en lugar de Excelente."],
    recomendaciones=["Revisa las unidades antes de enviarlas al depósito de Full.",
                     "Corrige la publicación para que voltaje y potencia coincidan."],
    status={"id": "active"}, up_id="MLMU3937163973")
# El gris: sin text, value -1 y TODOS los textos vacíos (ni status trae).
EXP_GRIS = _exp({"color": "gray", "value": -1}, ia="", up_id="MLMU3914871324")
# ML la tiene pausada aunque kubera la crea activa (3 casos, por out_of_stock).
EXP_PAUSADA = dict(EXP_ROJO, status={"id": "paused",
                                     "text": "Tu publicación está inactiva."})


def _es_experiencia(req: httpx.Request) -> bool:
    return req.url.path.startswith("/reputation/items/")


def _item_de(req: httpx.Request) -> str:
    partes = req.url.path.split("/")
    return partes[3] if _es_experiencia(req) else partes[2]


class _ErrPg(Exception):
    def __init__(self, pgcode: str):
        super().__init__(f"pgcode {pgcode}")
        self.pgcode = pgcode


class _Ctx:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *a):
        return False


def _run(coro):
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════════════
# normalizar
# ═════════════════════════════════════════════════════════════════════════════

class Normalizar(unittest.TestCase):
    def test_medium_real(self):
        n = calidad_ml.normalizar(CRUDO_MEDIUM)
        self.assertEqual(n["score"], 60)
        self.assertEqual(n["level"], "medium")
        self.assertEqual(n["level_wording"], "Estándar")
        self.assertEqual(n["calculated_at"], "2026-09-22T01:40:00.109Z")
        self.assertEqual(n["user_product_id"], "MLMU5081808139")
        self.assertEqual(n["n_pendientes"], 4)
        self.assertEqual(set(n), {"score", "level", "level_wording", "calculated_at",
                                  "user_product_id", "buckets", "n_pendientes"})

        b0, b1 = n["buckets"]
        self.assertEqual(b0["clave"], "USER_PRODUCT")
        self.assertEqual(b0["tipo"], "USER_PRODUCT")
        self.assertEqual(b0["titulo"], "Datos del producto")
        self.assertEqual(b0["score"], 89)                  # 89.41177 redondeado
        self.assertEqual(b0["estado"], "PENDING")
        self.assertEqual(b0["n_variables"], 2)
        self.assertEqual([p["clave"] for p in b0["pendientes"]], ["UP_SHORTS"])
        self.assertEqual(b0["pendientes"][0]["accion"], "Crear video")
        self.assertTrue(b0["pendientes"][0]["titulo"].startswith("Los videos"))

        self.assertEqual(b1["clave"], "MLM3451327389")
        self.assertEqual(b1["tipo"], "ITEM")
        self.assertEqual(b1["score"], 18)
        self.assertEqual(b1["n_variables"], 4)
        # En el orden de ML, sin la completada (UP_FINANCING).
        self.assertEqual([p["clave"] for p in b1["pendientes"]],
                         ["UP_FREE_SHIPPING", "UP_PROMOTIONS", "UP_PRICE"])
        envio = b1["pendientes"][0]
        self.assertEqual(envio, {
            "clave": "UP_FREE_SHIPPING",
            "titulo": "Ofrece envío gratis para que tu publicación sea más competitiva.",
            "accion": "Ofrecer envío",
            "link": "https://www.mercadolibre.com.mx/syi/core/modify?"
                    "taskId=shipping_task&itemId=MLM3451327389",
        })

    def test_good_real_scores_flotantes(self):
        n = calidad_ml.normalizar(CRUDO_GOOD)
        self.assertEqual((n["score"], n["level"], n["level_wording"]),
                         (81, "good", "Profesional"))
        self.assertEqual([b["score"] for b in n["buckets"]], [89, 71])
        self.assertEqual(n["n_pendientes"], 2)
        self.assertEqual([p["accion"] for b in n["buckets"] for p in b["pendientes"]],
                         ["Crear video", "Participar"])

    def test_variable_sin_reglas_usa_su_titulo_y_nada_se_inventa(self):
        n = calidad_ml.normalizar({
            "score": 42.6, "level": "x", "buckets": [
                {"key": "K", "type": "ITEM", "status": "PENDING", "score": None,
                 "title": "T", "variables": [
                     {"key": "V", "status": "PENDING", "title": "Hazlo", "rules": []}]}]})
        self.assertEqual(n["score"], 43)
        self.assertIsNone(n["level_wording"])
        self.assertIsNone(n["buckets"][0]["score"])        # None, nunca 0
        self.assertEqual(n["buckets"][0]["pendientes"],
                         [{"clave": "V", "titulo": "Hazlo", "accion": None, "link": None}])

    def test_basura_no_revienta(self):
        for crudo in ({}, None, {"score": "abc", "buckets": "no"},
                      {"score": 150}, {"calculated_at": "ayer"}):
            n = calidad_ml.normalizar(crudo)
            self.assertIsNone(n["score"])
            self.assertIsNone(n["calculated_at"])
            self.assertEqual(n["n_pendientes"], 0)


class NormalizarExperiencia(unittest.TestCase):
    CLAVES = {"exp_estado", "exp_valor", "exp_color", "exp_texto", "exp_consecuencia",
              "exp_accion_principal", "exp_razon", "exp_recomendaciones", "exp_ia",
              "exp_por_categoria", "exp_status_ml", "exp_status_texto"}

    def test_rojo_real(self):
        e = calidad_ml.normalizar_experiencia(EXP_ROJO)
        self.assertEqual(set(e), self.CLAVES)
        self.assertEqual((e["exp_estado"], e["exp_valor"], e["exp_color"], e["exp_texto"]),
                         ("medida", 30, "red", "Mala"))
        self.assertTrue(e["exp_consecuencia"].startswith("Tienes muy baja exposición"))
        self.assertEqual(e["exp_accion_principal"],
                         "Aclara en la publicación con fotos y accesorios.")
        self.assertEqual(len(e["exp_razon"]), 1)
        self.assertEqual(len(e["exp_recomendaciones"]), 1)
        self.assertIs(e["exp_ia"], True)
        # «…a partir de tus ventas de productos en la misma categoría…»
        self.assertIs(e["exp_por_categoria"], True)
        self.assertEqual(e["exp_status_ml"], "active")
        self.assertIsNone(e["exp_status_texto"])

    def test_naranja_real(self):
        e = calidad_ml.normalizar_experiencia(EXP_NARANJA)
        self.assertEqual((e["exp_estado"], e["exp_valor"], e["exp_color"], e["exp_texto"]),
                         ("medida", 65, "orange", "Media"))
        self.assertEqual(e["exp_consecuencia"],
                         "La experiencia que brinda tu publicación afecta tu exposición.")
        self.assertEqual(len(e["exp_recomendaciones"]), 2)
        self.assertIs(e["exp_por_categoria"], True)

    def test_verde_real_no_es_por_categoria(self):
        e = calidad_ml.normalizar_experiencia(EXP_VERDE)
        self.assertEqual((e["exp_estado"], e["exp_valor"], e["exp_color"], e["exp_texto"]),
                         ("medida", 75, "green", "Buena"))
        # Habla de «la categoría» pero no de «la MISMA categoría»: se calculó
        # con la publicación.
        self.assertIs(e["exp_por_categoria"], False)
        self.assertEqual(e["exp_recomendaciones"][0],
                         "Revisa las unidades antes de enviarlas al depósito de Full.")

    def test_gris_real_es_sin_datos_nunca_cero(self):
        e = calidad_ml.normalizar_experiencia(EXP_GRIS)
        self.assertEqual(e["exp_estado"], "sin_datos")
        self.assertIsNone(e["exp_valor"])                  # no -1, no 0
        self.assertEqual(e["exp_color"], "gray")
        # Todos los textos vacíos → None / listas vacías.
        for k in ("exp_texto", "exp_consecuencia", "exp_accion_principal",
                  "exp_status_ml", "exp_status_texto"):
            self.assertIsNone(e[k], k)
        self.assertEqual((e["exp_razon"], e["exp_recomendaciones"]), ([], []))
        self.assertIs(e["exp_ia"], False)
        self.assertIs(e["exp_por_categoria"], False)

    def test_value_negativo_sin_gris_tambien_es_sin_datos(self):
        e = calidad_ml.normalizar_experiencia({"reputation": {"color": "green",
                                                              "value": -1}})
        self.assertEqual((e["exp_estado"], e["exp_valor"]), ("sin_datos", None))

    def test_pausada_por_ml(self):
        e = calidad_ml.normalizar_experiencia(EXP_PAUSADA)
        self.assertEqual((e["exp_status_ml"], e["exp_status_texto"]),
                         ("paused", "Tu publicación está inactiva."))

    def test_orden_de_ml_y_vacios_fuera(self):
        e = calidad_ml.normalizar_experiencia({
            "reputation": {"color": "red", "text": "Mala", "value": 30.4},
            "reasoning": {"subtitles": [{"order": 2, "text": "tercero"},
                                        {"order": 0, "text": "primero"},
                                        {"order": 1, "text": "  "},
                                        "basura", {"text": "sin orden"}]},
            "recommendations": {"subtitles": "no es lista"}})
        self.assertEqual(e["exp_valor"], 30)
        self.assertEqual(e["exp_razon"], ["primero", "tercero", "sin orden"])
        self.assertEqual(e["exp_recomendaciones"], [])
        self.assertIsNone(e["exp_ia"])                     # sin nodo: no se sabe

    def test_numeros_desbordados_no_revientan(self):
        # `1e400` llega del JSON como inf; "Infinity" es como lo serializan
        # muchos backends; un entero de 400 dígitos no cabe en un float. Nada
        # de eso es un valor: None, nunca OverflowError.
        crudo = json.loads('{"reputation": {"color": "red", "text": "Mala", '
                           '"value": 1e400}}')
        self.assertEqual(crudo["reputation"]["value"], float("inf"))
        for valor in (crudo["reputation"]["value"], "Infinity", "-Infinity", "NaN",
                      float("-inf"), float("nan"), 10 ** 400, -(10 ** 400)):
            e = calidad_ml.normalizar_experiencia(
                {"reputation": {"color": "red", "value": valor}})
            self.assertEqual((e["exp_estado"], e["exp_valor"]), (None, None), valor)
        self.assertIsNone(calidad_ml._redondo(1e400))
        self.assertIsNone(calidad_ml._redondo("Infinity"))
        self.assertEqual(calidad_ml._redondo("30.4"), 30)
        # La calidad pasa por el mismo _redondo: score inf = sin score legible.
        self.assertIsNone(calidad_ml.normalizar({"score": 1e400})["score"])

    def test_order_de_400_digitos_ordena_sin_desbordar(self):
        enorme = 10 ** 400
        crudo = json.loads(json.dumps({
            "reputation": {"color": "red", "value": 30},
            "reasoning": {"subtitles": [{"order": enorme, "text": "enorme"},
                                        {"order": -enorme, "text": "negativo"},
                                        {"order": 1, "text": "uno"},
                                        {"order": float("nan"), "text": "nan"},
                                        {"text": "sin orden"}]},
            "recommendations": {"subtitles": [{"order": 1e400, "text": "inf"},
                                              {"order": 2, "text": "dos"}]}}))
        e = calidad_ml.normalizar_experiencia(crudo)
        self.assertEqual(e["exp_razon"], ["negativo", "uno", "enorme", "nan", "sin orden"])
        self.assertEqual(e["exp_recomendaciones"], ["dos", "inf"])
        self.assertEqual((e["exp_estado"], e["exp_valor"]), ("medida", 30))

    def test_ilegible_no_tiene_estado(self):
        # medir lo cuenta como error de experiencia y no escribe exp_*.
        for crudo in ({}, None, {"reputation": "x"}, {"reputation": {"color": "red"}},
                      {"reputation": {"value": 30}},
                      {"reputation": {"color": "red", "value": "abc"}},
                      {"reputation": {"color": "red", "value": 150}}):
            e = calidad_ml.normalizar_experiencia(crudo)
            self.assertIsNone(e["exp_estado"], crudo)
            self.assertIsNone(e["exp_valor"], crudo)


# ═════════════════════════════════════════════════════════════════════════════
# medir (httpx.MockTransport)
# ═════════════════════════════════════════════════════════════════════════════

class _BaseMedir(unittest.TestCase):
    def setUp(self):
        self.peticiones: list[httpx.Request] = []

    def _con(self, respuestas, experiencia=None):
        """Parcha el cliente con un transporte falso. `respuestas` contesta la
        CALIDAD y `experiencia` la experiencia de compra: funciones (request,
        n-ésima llamada a ese item EN ESA API) → httpx.Response. Sin
        `experiencia`, ML contesta la verde real (200)."""
        cuenta_por_item: dict[tuple[bool, str], int] = {}
        if experiencia is None:
            def experiencia(req, n):
                return httpx.Response(200, json=EXP_VERDE)

        def handler(request: httpx.Request) -> httpx.Response:
            self.peticiones.append(request)
            clave = (_es_experiencia(request), _item_de(request))
            cuenta_por_item[clave] = cuenta_por_item.get(clave, 0) + 1
            f = experiencia if clave[0] else respuestas
            return f(request, cuenta_por_item[clave])

        def fabrica():
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ML)
        return mock.patch.object(calidad_ml, "_nuevo_cliente", side_effect=fabrica)

    def _de_calidad(self):
        return [r for r in self.peticiones if not _es_experiencia(r)]

    def _de_experiencia(self):
        return [r for r in self.peticiones if _es_experiencia(r)]


class Medir(_BaseMedir):
    def test_200_escribe_medida(self):
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_MEDIUM)):
            filas, conteo = _run(calidad_ml.medir([("MLM3451327389", "BEKURA")],
                                                  {"BEKURA": "tok-b"}))
        self.assertEqual(conteo, {"ok": 1, "no_calculada": 0, "limitada_429": 0,
                                  "sin_token": 0, "error": 0,
                                  "exp_ok": 1, "exp_sin_datos": 0, "exp_error": 0,
                                  "exp_rechazada": 0})
        self.assertEqual(len(filas), 1)
        f = filas[0]
        self.assertEqual((f["item_id"], f["cuenta"], f["estado"], f["score"]),
                         ("MLM3451327389", "BEKURA", "medida", 60))
        self.assertEqual(f["user_product_id"], "MLMU5081808139")
        self.assertIsNone(f["motivo"])
        req = self._de_calidad()[0]
        self.assertEqual(req.url.path, "/item/MLM3451327389/performance")
        self.assertEqual(req.headers["Authorization"], "Bearer tok-b")

    def test_400_escribe_no_calculada_con_motivo(self):
        def resp(req, n):
            iid = req.url.path.split("/")[2]
            return httpx.Response(400, json=PAUSADA_400 if iid == "MLM1" else CATALOGO_400)
        with self._con(resp):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA"), ("MLM2", "BEKURA")],
                                                  {"BEKURA": "t"}))
        self.assertEqual(conteo["no_calculada"], 2)
        por_id = {f["item_id"]: f for f in filas}
        self.assertEqual(por_id["MLM1"]["estado"], "no_calculada")
        self.assertIsNone(por_id["MLM1"]["score"])
        self.assertEqual(por_id["MLM1"]["buckets"], [])
        self.assertEqual(por_id["MLM1"]["motivo"],
                         "Entity not calculated: Only status active is supported")
        self.assertEqual(por_id["MLM2"]["motivo"],
                         "Entity not calculated: Product items are not supported")

    def test_400_ajeno_es_error_sin_fila_y_su_message_no_va_al_log(self):
        # Solo «Entity not calculated…» es respuesta de calidad. Un 400 de otra
        # cosa (token malformado) es «no pude preguntar»: sin fila, y el
        # `message` —que puede traer el token— no se registra.
        secreto = "xyz"

        def resp(req, n):
            iid = req.url.path.split("/")[2]
            if iid == "MLM1":
                return httpx.Response(400, json={
                    "message": f"Malformed access_token: {secreto}",
                    "error": "bad_request", "status": 400})
            return httpx.Response(400, text=f"Malformed access_token: {secreto}")
        with self._con(resp), \
                self.assertLogs("omnicanal.calidad_ml", level="DEBUG") as logs:
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA"), ("MLM2", "BEKURA")],
                                                  {"BEKURA": "t"}))
        self.assertEqual(filas, [])
        self.assertEqual(conteo["error"], 2)
        self.assertEqual(conteo["no_calculada"], 0)
        self.assertTrue(logs.output)
        for linea in logs.output:
            self.assertNotIn(secreto, linea)
            self.assertNotIn("Malformed", linea)
        for rec in logs.records:
            self.assertNotIn(secreto, rec.getMessage())

    def test_motivo_400_solo_la_lista_blanca(self):
        def r(**kw):
            return httpx.Response(400, **kw)
        self.assertEqual(calidad_ml._motivo_400(r(json=CATALOGO_400)),
                         CATALOGO_400["message"])
        self.assertIsNone(calidad_ml._motivo_400(r(json={"message": "Invalid item_id"})))
        self.assertIsNone(calidad_ml._motivo_400(r(json={"error": "bad_request"})))
        self.assertIsNone(calidad_ml._motivo_400(r(json=["Entity not calculated"])))
        self.assertIsNone(calidad_ml._motivo_400(r(text="Entity not calculated: x")))

    def test_401_sin_renovar_no_escribe_ni_renueva(self):
        with self._con(lambda req, n: httpx.Response(401, json={"message": "invalid"})):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")],
                                                  {"BEKURA": "viejo"}, renovar=None))
        self.assertEqual(filas, [])
        self.assertEqual(conteo["sin_token"], 1)
        self.assertEqual(conteo["ok"], 0)
        self.assertEqual(len(self.peticiones), 1)          # sin reintento ni experiencia

    def test_401_con_renovar_renueva_una_vez_y_reintenta(self):
        renovar = mock.AsyncMock(return_value="nuevo")

        def resp(req, n):
            if req.headers["Authorization"] == "Bearer viejo":
                return httpx.Response(401)
            return httpx.Response(200, json=CRUDO_GOOD)
        tokens = {"SANCORFASHION": "viejo"}
        with self._con(resp):
            filas, conteo = _run(calidad_ml.medir(
                [("MLM1", "SANCORFASHION"), ("MLM2", "SANCORFASHION"),
                 ("MLM3", "SANCORFASHION")], tokens, renovar=renovar))
        self.assertEqual(conteo["ok"], 3)
        self.assertEqual({f["level"] for f in filas}, {"good"})
        # Una sola renovación para las tres, aunque las tres chocaron con el 401.
        renovar.assert_awaited_once_with("SANCORFASHION")
        self.assertEqual(tokens["SANCORFASHION"], "nuevo")  # se actualiza en sitio

    def test_401_si_la_renovacion_falla_no_escribe(self):
        renovar = mock.AsyncMock(return_value=None)
        tokens = {"BEKURA": "t"}
        with self._con(lambda req, n: httpx.Response(401)):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], tokens,
                                                  renovar=renovar))
        self.assertEqual((filas, conteo["sin_token"]), ([], 1))
        renovar.assert_awaited_once()
        self.assertNotIn("BEKURA", tokens, "muerta para el resto del barrido")

    def test_401_persistente_tras_renovar_mata_la_cuenta(self):
        # El token renovado también da 401: la cuenta muere, su token sale de
        # `tokens` y las demás publicaciones ni preguntan ni renuevan.
        renovar = mock.AsyncMock(return_value="nuevo")
        tokens = {"BEKURA": "viejo", "SANCORFASHION": "s"}

        def resp(req, n):
            if req.headers["Authorization"] == "Bearer s":
                return httpx.Response(200, json=CRUDO_GOOD)
            return httpx.Response(401)
        pares = [(f"MLM{i}", "BEKURA") for i in range(30)] + [("MLM99", "SANCORFASHION")]
        with self._con(resp):
            filas, conteo = _run(calidad_ml.medir(pares, tokens, renovar=renovar))
        renovar.assert_awaited_once_with("BEKURA")
        self.assertEqual(conteo["sin_token"], 30)
        self.assertEqual(conteo["ok"], 1)
        self.assertEqual([f["item_id"] for f in filas], ["MLM99"])
        self.assertNotIn("BEKURA", tokens)
        self.assertEqual(tokens["SANCORFASHION"], "s")
        bekura = [r for r in self.peticiones if r.headers["Authorization"] != "Bearer s"]
        # A lo más las que ya estaban en vuelo (semáforo) × 2 intentos.
        self.assertLessEqual(len(bekura), 2 * calidad_ml._EN_PARALELO)
        # Ninguna publicación de la cuenta muerta llegó a preguntar la experiencia.
        self.assertFalse([r for r in bekura if _es_experiencia(r)])

    def test_renovar_que_devuelve_el_mismo_token_mata_sin_reintentar(self):
        renovar = mock.AsyncMock(return_value="t")
        tokens = {"BEKURA": "t"}
        with self._con(lambda req, n: httpx.Response(401)):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], tokens,
                                                  renovar=renovar))
        self.assertEqual((filas, conteo["sin_token"]), ([], 1))
        renovar.assert_awaited_once()
        self.assertEqual(len(self.peticiones), 1, "mismo token: no se reintenta")
        self.assertNotIn("BEKURA", tokens)

    def test_un_token_ya_renovado_en_el_barrido_no_se_renueva_otra_vez(self):
        # Tanda siguiente del mismo barrido: el token vigente salió de renovar
        # en una tanda anterior (`renovados`) y ahora da 401.
        renovar = mock.AsyncMock(return_value="otro")
        tokens = {"BEKURA": "renovado-antes"}
        with self._con(lambda req, n: httpx.Response(401)):
            filas, conteo = _run(calidad_ml.medir(
                [("MLM1", "BEKURA")], tokens, renovar=renovar,
                renovados={"renovado-antes"}))
        self.assertEqual((filas, conteo["sin_token"]), ([], 1))
        renovar.assert_not_awaited()
        self.assertNotIn("BEKURA", tokens)

    def test_429_con_retry_after_reintenta(self):
        def resp(req, n):
            if n == 1:
                return httpx.Response(429, headers={"Retry-After": "2"})
            return httpx.Response(200, json=CRUDO_MEDIUM)
        dormir = mock.AsyncMock()
        with self._con(resp), mock.patch.object(calidad_ml.asyncio, "sleep", dormir):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], {"BEKURA": "t"}))
        self.assertEqual(conteo["ok"], 1)
        self.assertEqual(len(filas), 1)
        dormir.assert_awaited_once_with(2.0)

    def test_429_retry_after_con_tope_y_escalera_con_jitter(self):
        def resp(req, n):
            if n == 1:
                return httpx.Response(429, headers={"Retry-After": "600"})
            if n == 2:
                return httpx.Response(429)
            return httpx.Response(200, json=CRUDO_MEDIUM)
        dormir = mock.AsyncMock()
        with self._con(resp), mock.patch.object(calidad_ml.asyncio, "sleep", dormir), \
                mock.patch.object(calidad_ml.random, "uniform", return_value=0.1):
            _, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], {"BEKURA": "t"}))
        self.assertEqual(conteo["ok"], 1)
        esperas = [c.args[0] for c in dormir.await_args_list]
        self.assertEqual(esperas[0], 30.0)                 # tope de Retry-After
        self.assertAlmostEqual(esperas[1], 1.1)            # 2º escalón (1.0) + jitter

    def test_429_agotado_no_escribe(self):
        dormir = mock.AsyncMock()
        with self._con(lambda req, n: httpx.Response(429)), \
                mock.patch.object(calidad_ml.asyncio, "sleep", dormir):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], {"BEKURA": "t"}))
        self.assertEqual((filas, conteo["limitada_429"]), ([], 1))
        self.assertEqual(dormir.await_count, len(calidad_ml._ESPERAS_429))

    def test_5xx_red_y_cuerpo_raro_son_error_sin_fila(self):
        def resp(req, n):
            iid = req.url.path.split("/")[2]
            if iid == "MLM1":
                return httpx.Response(503)
            if iid == "MLM2":
                raise httpx.ConnectError("caída")
            if iid == "MLM3":
                return httpx.Response(200, content=b"<html>")
            return httpx.Response(200, json={"score": None, "level": "medium"})
        with self._con(resp),                 self.assertLogs("omnicanal.calidad_ml", level="WARNING") as logs:
            filas, conteo = _run(calidad_ml.medir(
                [("MLM1", "B"), ("MLM2", "B"), ("MLM3", "B"), ("MLM4", "B")], {"B": "t"}))
        self.assertIn("MLM4", logs.output[0])              # 200 sin score: se avisa
        self.assertEqual(filas, [])
        self.assertEqual(conteo["error"], 4)

    def test_cuenta_sin_token_ni_pregunta_y_repetidos_una_vez(self):
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_GOOD)):
            filas, conteo = _run(calidad_ml.medir(
                [("MLM1", "BEKURA"), ("MLM1", "BEKURA"), ("MLM9", "SANCORFASHION")],
                {"BEKURA": "t"}))
        self.assertEqual(conteo["ok"], 1)
        self.assertEqual(conteo["sin_token"], 1)
        self.assertEqual(len(self._de_calidad()), 1)
        self.assertEqual(len(self._de_experiencia()), 1)
        self.assertEqual([f["item_id"] for f in filas], ["MLM1"])


_EXP_KEYS = NormalizarExperiencia.CLAVES


class MedirExperiencia(_BaseMedir):
    """Las dos APIs por publicación, UN escritor: la calidad decide si hay fila
    y la experiencia solo suma sus `exp_*` cuando sale bien."""

    def test_las_dos_bien_fila_completa(self):
        def exp(req, n):
            iid = _item_de(req)
            return httpx.Response(200, json={"MLM1": EXP_ROJO, "MLM2": EXP_GRIS,
                                             "MLM3": EXP_NARANJA}[iid])
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_MEDIUM), exp):
            filas, conteo = _run(calidad_ml.medir(
                [("MLM1", "BEKURA"), ("MLM2", "BEKURA"), ("MLM3", "BEKURA")],
                {"BEKURA": "tok-b"}))
        self.assertEqual((conteo["ok"], conteo["exp_ok"], conteo["exp_sin_datos"],
                          conteo["exp_error"]), (3, 2, 1, 0))
        por_id = {f["item_id"]: f for f in filas}
        rojo = por_id["MLM1"]
        # Calidad y experiencia en LA MISMA fila.
        self.assertEqual((rojo["estado"], rojo["score"]), ("medida", 60))
        self.assertEqual((rojo["exp_estado"], rojo["exp_valor"], rojo["exp_color"],
                          rojo["exp_texto"], rojo["exp_por_categoria"]),
                         ("medida", 30, "red", "Mala", True))
        self.assertEqual((por_id["MLM2"]["exp_estado"], por_id["MLM2"]["exp_valor"]),
                         ("sin_datos", None))
        self.assertEqual(por_id["MLM3"]["exp_valor"], 65)
        req = next(r for r in self._de_experiencia() if _item_de(r) == "MLM1")
        self.assertEqual(req.url.path,
                         "/reputation/items/MLM1/purchase_experience/integrators")
        self.assertEqual(req.url.params["locale"], "es_MX")     # sin él, 400
        self.assertEqual(req.headers["Authorization"], "Bearer tok-b")

    def test_no_calculada_tambien_pregunta_la_experiencia(self):
        # El 400 conocido ES respuesta de calidad: hay fila, y la experiencia
        # dice si ML la tiene pausada.
        with self._con(lambda req, n: httpx.Response(400, json=PAUSADA_400),
                       lambda req, n: httpx.Response(200, json=EXP_PAUSADA)):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], {"BEKURA": "t"}))
        self.assertEqual((conteo["no_calculada"], conteo["exp_ok"]), (1, 1))
        self.assertEqual(filas[0]["estado"], "no_calculada")
        self.assertEqual(filas[0]["exp_status_ml"], "paused")

    def test_calidad_bien_experiencia_falla_fila_sin_exp(self):
        dormir = mock.AsyncMock()

        def exp(req, n):
            iid = _item_de(req)
            if iid == "MLM1":
                return httpx.Response(500)
            if iid == "MLM2":
                return httpx.Response(400, json={"message": "locale requerido"})
            if iid == "MLM3":
                return httpx.Response(429)
            if iid == "MLM4":
                return httpx.Response(200, json={"title": {"text": "sin reputation"}})
            if iid == "MLM5":
                return httpx.Response(200, json={"reputation": {"color": "red"}})
            if iid == "MLM6":
                return httpx.Response(200, content=b"<html>")
            raise httpx.ConnectError("caída")
        pares = [(f"MLM{i}", "BEKURA") for i in range(1, 8)]
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_GOOD), exp), \
                mock.patch.object(calidad_ml.asyncio, "sleep", dormir), \
                self.assertLogs("omnicanal.calidad_ml", level="DEBUG") as logs:
            filas, conteo = _run(calidad_ml.medir(pares, {"BEKURA": "t"}))
        # El 400 (MLM2) es un «no» de ML: rechazada, no error. El resto (500,
        # 429 agotado, sin reputation, reputation ilegible, no-JSON, red) es
        # «no pude preguntar».
        self.assertEqual((conteo["ok"], conteo["exp_ok"], conteo["exp_sin_datos"],
                          conteo["exp_error"], conteo["exp_rechazada"]), (7, 0, 0, 6, 1))
        self.assertEqual(len(filas), 7)
        for f in filas:
            # Las columnas de calidad, completas; NINGUNA exp_*: guardar
            # conserva las que ya había.
            self.assertEqual((f["estado"], f["score"]), ("medida", 81))
            self.assertFalse(_EXP_KEYS & set(f), f["item_id"])
        # El cuerpo de un 400 de la experiencia jamás va al log.
        self.assertFalse([linea for linea in logs.output if "locale requerido" in linea])

    def test_calidad_falla_sin_fila_aunque_la_experiencia_saliera(self):
        def cal(req, n):
            iid = _item_de(req)
            if iid == "MLM1":
                return httpx.Response(503)
            if iid == "MLM2":
                return httpx.Response(401)
            return httpx.Response(429)
        dormir = mock.AsyncMock()
        with self._con(cal, lambda req, n: httpx.Response(200, json=EXP_ROJO)), \
                mock.patch.object(calidad_ml.asyncio, "sleep", dormir):
            filas, conteo = _run(calidad_ml.medir(
                [("MLM1", "B"), ("MLM2", "S"), ("MLM3", "K")],
                {"B": "t", "S": "t", "K": "t"}, renovar=None))
        self.assertEqual(filas, [])
        self.assertEqual((conteo["error"], conteo["sin_token"], conteo["limitada_429"]),
                         (1, 1, 1))
        self.assertEqual((conteo["exp_ok"], conteo["exp_error"]), (0, 0))
        # Ni se preguntó: su respuesta se tiraría igual y el cupo es compartido.
        self.assertEqual(self._de_experiencia(), [])

    def test_401_en_experiencia_mata_la_cuenta(self):
        tokens = {"BEKURA": "t", "SANCORFASHION": "s"}

        def exp(req, n):
            if req.headers["Authorization"] == "Bearer t":
                return httpx.Response(401)
            return httpx.Response(200, json=EXP_VERDE)
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_MEDIUM), exp):
            filas, conteo = _run(calidad_ml.medir(
                [("MLM1", "BEKURA"), ("MLM9", "SANCORFASHION")], tokens, renovar=None))
        # La calidad de MLM1 sí salió: su fila va, sin experiencia.
        self.assertEqual((conteo["ok"], conteo["exp_ok"], conteo["exp_error"]), (2, 1, 1))
        por_id = {f["item_id"]: f for f in filas}
        self.assertFalse(_EXP_KEYS & set(por_id["MLM1"]))
        self.assertEqual(por_id["MLM9"]["exp_color"], "green")
        # …pero la cuenta murió para el resto del barrido, igual que con un
        # 401 de la calidad.
        self.assertNotIn("BEKURA", tokens)
        self.assertEqual(tokens["SANCORFASHION"], "s")
        antes = len(self.peticiones)
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_MEDIUM)):
            filas2, conteo2 = _run(calidad_ml.medir([("MLM2", "BEKURA")], tokens,
                                                    renovar=None))
        self.assertEqual((filas2, conteo2["sin_token"]), ([], 1))
        self.assertEqual(len(self.peticiones), antes, "tanda siguiente: ni pregunta")

    def test_401_en_experiencia_renueva_y_la_renovacion_sirve_a_las_dos(self):
        renovar = mock.AsyncMock(return_value="nuevo")
        tokens = {"BEKURA": "viejo"}

        def exp(req, n):
            if req.headers["Authorization"] == "Bearer viejo":
                return httpx.Response(401)
            return httpx.Response(200, json=EXP_NARANJA)
        # La calidad todavía acepta el token viejo; la experiencia no.
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_MEDIUM), exp):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], tokens,
                                                  renovar=renovar))
        renovar.assert_awaited_once_with("BEKURA")
        self.assertEqual(tokens["BEKURA"], "nuevo")
        self.assertEqual((conteo["ok"], conteo["exp_ok"]), (1, 1))
        self.assertEqual(filas[0]["exp_color"], "orange")

    def test_token_renovado_por_la_calidad_no_se_renueva_por_la_experiencia(self):
        # La calidad renovó (viejo → nuevo) y la experiencia rechaza también el
        # nuevo: una sola renovación por cuenta y barrido, y la cuenta muere.
        renovar = mock.AsyncMock(return_value="nuevo")
        tokens = {"BEKURA": "viejo"}

        def cal(req, n):
            if req.headers["Authorization"] == "Bearer viejo":
                return httpx.Response(401)
            return httpx.Response(200, json=CRUDO_MEDIUM)
        with self._con(cal, lambda req, n: httpx.Response(401)):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], tokens,
                                                  renovar=renovar))
        renovar.assert_awaited_once_with("BEKURA")
        self.assertEqual((conteo["ok"], conteo["exp_error"]), (1, 1))
        self.assertEqual(len(filas), 1)
        self.assertFalse(_EXP_KEYS & set(filas[0]))
        self.assertNotIn("BEKURA", tokens)

    def test_4xx_definitivos_de_la_experiencia_son_rechazada(self):
        # 400/403/404: ML contestó que no (catálogo, sin permiso, ruta
        # cambiada). Cuentan en exp_rechazada, no en exp_error; la calidad va
        # sin exp_*. Un 5xx sigue siendo exp_error.
        codigos = {"MLM1": 400, "MLM2": 403, "MLM3": 404, "MLM4": 503}
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_MEDIUM),
                       lambda req, n: httpx.Response(codigos[_item_de(req)],
                                                     json={"message": "no"})):
            filas, conteo = _run(calidad_ml.medir(
                [(i, "BEKURA") for i in codigos], {"BEKURA": "t"}))
        self.assertEqual((conteo["ok"], conteo["exp_ok"], conteo["exp_rechazada"],
                          conteo["exp_error"]), (4, 0, 3, 1))
        self.assertEqual(len(filas), 4)
        for f in filas:
            self.assertFalse(_EXP_KEYS & set(f), f["item_id"])
        # Una sola petición de experiencia por ítem: el «no» no se reintenta.
        self.assertEqual(len(self._de_experiencia()), 4)

    def test_value_1e400_en_la_experiencia_cuesta_solo_ese_item(self):
        def exp(req, n):
            if _item_de(req) == "MLM2":
                return httpx.Response(
                    200, content=b'{"reputation": {"color": "red", "value": 1e400}}',
                    headers={"Content-Type": "application/json"})
            return httpx.Response(200, json=EXP_ROJO)
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_MEDIUM), exp),                 self.assertLogs("omnicanal.calidad_ml", level="WARNING") as logs:
            filas, conteo = _run(calidad_ml.medir(
                [("MLM1", "BEKURA"), ("MLM2", "BEKURA"), ("MLM3", "BEKURA")],
                {"BEKURA": "t"}))
        self.assertEqual((conteo["ok"], conteo["exp_ok"], conteo["exp_error"]), (3, 2, 1))
        self.assertTrue(any("reputation ilegible" in linea for linea in logs.output))
        por_id = {f["item_id"]: f for f in filas}
        self.assertFalse(_EXP_KEYS & set(por_id["MLM2"]))
        self.assertEqual(por_id["MLM1"]["exp_valor"], 30)

    def test_excepcion_inesperada_cuesta_solo_ese_item(self):
        # Una respuesta que la normalización no previó (aquí, forzada) cuesta
        # ESE ítem: error si fue en la calidad, exp_error si fue en la
        # experiencia. Los demás se miden y ninguna tarea queda huérfana.
        real_cal, real_exp = calidad_ml.normalizar, calidad_ml.normalizar_experiencia

        def cal(crudo):
            if crudo.get("entity_id") == "BOOM":
                raise OverflowError("cannot convert float infinity to integer")
            return real_cal(crudo)

        def exp_n(crudo):
            if crudo.get("boom"):
                raise KeyError("inesperada")
            return real_exp(crudo)

        def calidad(req, n):
            if _item_de(req) == "MLM2":
                return httpx.Response(200, json=dict(CRUDO_MEDIUM, entity_id="BOOM"))
            return httpx.Response(200, json=CRUDO_MEDIUM)

        def experiencia(req, n):
            if _item_de(req) == "MLM3":
                return httpx.Response(200, json=dict(EXP_ROJO, boom=True))
            return httpx.Response(200, json=EXP_ROJO)
        pares = [(f"MLM{i}", "BEKURA") for i in range(1, 6)]
        with self._con(calidad, experiencia), \
                mock.patch.object(calidad_ml, "normalizar", side_effect=cal), \
                mock.patch.object(calidad_ml, "normalizar_experiencia",
                                  side_effect=exp_n), \
                self.assertLogs("omnicanal.calidad_ml", level="WARNING") as logs:
            filas, conteo = _run(calidad_ml.medir(pares, {"BEKURA": "t"}))
        self.assertEqual((conteo["ok"], conteo["error"], conteo["exp_ok"],
                          conteo["exp_error"]), (4, 1, 3, 1))
        por_id = {f["item_id"]: f for f in filas}
        self.assertEqual(sorted(por_id), ["MLM1", "MLM3", "MLM4", "MLM5"])
        self.assertFalse(_EXP_KEYS & set(por_id["MLM3"]))   # calidad sí, exp no
        self.assertEqual(por_id["MLM5"]["exp_valor"], 30)
        # Al log solo el tipo, nunca el mensaje.
        self.assertTrue(any("OverflowError" in linea for linea in logs.output))
        self.assertFalse(any("infinity" in linea for linea in logs.output))

    def test_gather_no_deja_tareas_vivas_si_una_revienta(self):
        # Red de seguridad: aunque algo escape de `_una`, el gather espera a
        # TODAS antes de cerrar el cliente y cuenta la que tronó como error.
        vivas_al_cerrar: list[int] = []
        real_gather = asyncio.gather

        async def boom():
            raise RuntimeError("se escapó")

        def gather(*aws, **kw):
            self.assertTrue(kw.get("return_exceptions"))
            aws = list(aws)
            aws[0].close()                     # la corrutina real de MLM1
            aws[0] = boom()
            return real_gather(*aws, **kw)

        class _Cli(httpx.AsyncClient):
            async def __aexit__(self, *a):
                vivas_al_cerrar.append(sum(
                    1 for t in asyncio.all_tasks() if t is not asyncio.current_task()))
                return await super().__aexit__(*a)

        def fabrica():
            return _Cli(transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json=EXP_ROJO if _es_experiencia(req)
                                           else CRUDO_MEDIUM)), base_url=ML)
        with mock.patch.object(calidad_ml, "_nuevo_cliente", side_effect=fabrica), \
                mock.patch.object(calidad_ml.asyncio, "gather", side_effect=gather),                 self.assertLogs("omnicanal.calidad_ml", level="WARNING") as logs:
            filas, conteo = _run(calidad_ml.medir(
                [("MLM1", "B"), ("MLM2", "B"), ("MLM3", "B")], {"B": "t"}))
        self.assertTrue(any("RuntimeError" in linea for linea in logs.output))
        self.assertFalse(any("se escapó" in linea for linea in logs.output))
        self.assertEqual((conteo["error"], conteo["ok"], len(filas)), (1, 2, 2))
        self.assertEqual(vivas_al_cerrar, [0])

    def test_429_de_la_experiencia_espera_como_la_calidad(self):
        def exp(req, n):
            if n == 1:
                return httpx.Response(429, headers={"Retry-After": "3"})
            return httpx.Response(200, json=EXP_ROJO)
        dormir = mock.AsyncMock()
        with self._con(lambda req, n: httpx.Response(200, json=CRUDO_MEDIUM), exp), \
                mock.patch.object(calidad_ml.asyncio, "sleep", dormir):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], {"BEKURA": "t"}))
        dormir.assert_awaited_once_with(3.0)
        self.assertEqual(conteo["exp_ok"], 1)
        self.assertEqual(filas[0]["exp_valor"], 30)


# ═════════════════════════════════════════════════════════════════════════════
# filtro_sql
# ═════════════════════════════════════════════════════════════════════════════

class FiltroSql(unittest.TestCase):
    def test_level_tal_cual(self):
        frag, params = calidad_ml.filtro_sql("medium")
        self.assertTrue(frag.startswith("exists ("))
        self.assertIn("from enrich.listing_health lq_f", frag)
        self.assertIn("lq_f.listing_id = l.listing_id", frag)
        # La llave completa: el mismo listing_id en otra cuenta u otro canal
        # no cruza.
        self.assertIn("lq_f.canal = l.canal", frag)
        self.assertIn("lq_f.account_id = l.account_id", frag)
        self.assertIn("lq_f.metrica = 'calidad'", frag)
        self.assertIn("lq_f.estado = 'medida'", frag)
        self.assertIn("lq_f.detalle ->> 'level' = %(calidad_ml)s", frag)
        self.assertEqual(params, {"calidad_ml": "medium"})

    def test_no_calculada(self):
        frag, params = calidad_ml.filtro_sql("no_calculada", alias="x")
        self.assertTrue(frag.startswith("exists ("))
        self.assertIn("lq_f.listing_id = x.listing_id", frag)
        self.assertIn("lq_f.account_id = x.account_id", frag)
        self.assertIn("lq_f.estado = %(calidad_ml)s", frag)
        self.assertNotIn("level", frag)
        self.assertEqual(params, {"calidad_ml": "no_calculada"})

    def test_sin_medir(self):
        frag, _ = calidad_ml.filtro_sql("sin_medir")
        self.assertTrue(frag.startswith("not exists ("))
        self.assertIn("lq_f.listing_id = l.listing_id", frag)
        self.assertIn("lq_f.metrica = 'calidad'", frag)
        self.assertNotIn("estado", frag)

    def test_nunca_interpola_el_valor(self):
        frag, params = calidad_ml.filtro_sql("x' or 1=1 --")
        self.assertNotIn("1=1", frag)
        self.assertEqual(params["calidad_ml"], "x' or 1=1 --")

    def test_invalidos(self):
        for valor, alias in (("", "l"), ("   ", "l"), (None, "l"), ("medium", "l;drop"),
                             ("medium", "L"), ("m" * 65, "l")):
            with self.assertRaises(ValueError):
                calidad_ml.filtro_sql(valor, alias=alias)


class FiltroSqlExperiencia(unittest.TestCase):
    def test_color_tal_cual(self):
        frag, params = calidad_ml.filtro_sql_experiencia("red")
        self.assertTrue(frag.startswith("exists ("))
        self.assertIn("from enrich.listing_health lq_e", frag)
        self.assertIn("lq_e.listing_id = l.listing_id", frag)
        self.assertIn("lq_e.account_id = l.account_id", frag)
        self.assertIn("lq_e.canal = l.canal", frag)
        self.assertIn("lq_e.metrica = 'experiencia'", frag)
        self.assertIn("lq_e.estado = 'medida'", frag)
        self.assertIn("lq_e.detalle ->> 'color' = %(experiencia_ml)s", frag)
        self.assertEqual(params, {"experiencia_ml": "red"})

    def test_sin_datos(self):
        frag, params = calidad_ml.filtro_sql_experiencia("sin_datos", alias="x")
        self.assertTrue(frag.startswith("exists ("))
        self.assertIn("lq_e.listing_id = x.listing_id", frag)
        self.assertIn("lq_e.estado = %(experiencia_ml)s", frag)
        self.assertNotIn("color", frag)
        self.assertEqual(params, {"experiencia_ml": "sin_datos"})

    def test_sin_medir_incluye_filas_sin_experiencia(self):
        # Sin ninguna fila, o con calidad pero sin fila de experiencia (aún no
        # se pregunta): las dos son «sin medir».
        frag, _ = calidad_ml.filtro_sql_experiencia("sin_medir")
        self.assertTrue(frag.startswith("not exists ("))
        self.assertIn("lq_e.metrica = 'experiencia'", frag)
        self.assertNotIn("estado", frag)

    def test_se_combina_con_el_de_calidad(self):
        # AND en el mismo WHERE: alias de subconsulta y parámetro distintos.
        f_cal, p_cal = calidad_ml.filtro_sql("medium")
        f_exp, p_exp = calidad_ml.filtro_sql_experiencia("orange")
        self.assertIn("lq_f", f_cal)
        self.assertNotIn("lq_f", f_exp)
        self.assertFalse(set(p_cal) & set(p_exp))

    def test_nunca_interpola_el_valor(self):
        frag, params = calidad_ml.filtro_sql_experiencia("x' or 1=1 --")
        self.assertNotIn("1=1", frag)
        self.assertEqual(params["experiencia_ml"], "x' or 1=1 --")

    def test_invalidos(self):
        for valor, alias in (("", "l"), ("   ", "l"), (None, "l"), ("red", "l;drop"),
                             ("red", "L"), ("r" * 65, "l"), (3, "l")):
            with self.assertRaises(ValueError):
                calidad_ml.filtro_sql_experiencia(valor, alias=alias)


# ═════════════════════════════════════════════════════════════════════════════
# Base de datos: guardar, objetivo, lecturas, conteo
# ═════════════════════════════════════════════════════════════════════════════

# uuids de mentira de core.accounts (la forma de `ids_de_cuentas`).
_ID_BEKURA = "00000000-0000-4000-8000-0000000000b1"
_ID_SANCOR = "00000000-0000-4000-8000-0000000000c2"
_CUENTAS = {"BEKURA": _ID_BEKURA, "SANCORFASHION": _ID_SANCOR}


def _fila(item_id, cuenta="BEKURA", estado="medida", score=60, **extra):
    base = {"item_id": item_id, "cuenta": cuenta, "estado": estado, "score": score,
            "level": "medium", "level_wording": "Estándar",
            "calculated_at": "2026-09-22T01:40:00.109Z",
            "user_product_id": "MLMU1", "buckets": [], "n_pendientes": 0, "motivo": None}
    base.update(extra)
    return base


def _ancha(crudo, item_id="MLM1", cuenta="BEKURA", exp=None):
    """Una fila de `medir` de verdad: calidad medida + (opcional) experiencia."""
    n = calidad_ml.normalizar(crudo)
    f = calidad_ml._fila_medida(item_id, cuenta, n)
    if exp is not None:
        f.update(calidad_ml.normalizar_experiencia(exp))
    return f


def _no_calculada(item_id="MLM2", cuenta="BEKURA", exp=None):
    f = calidad_ml._fila_no_calculada(item_id, cuenta, PAUSADA_400["message"])
    if exp is not None:
        f.update(calidad_ml.normalizar_experiencia(exp))
    return f


def _fecha(v):
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v


def _como_bd(item_id, m, capturado_en):
    """Una fila de `_filas_salud` tal como la devolvería psycopg2 al leer
    `listing_health`: numeric → Decimal, timestamptz → datetime, jsonb → objeto
    Python recién parseado."""
    return {"listing_id": item_id, "metrica": m["metrica"], "estado": m["estado"],
            "valor": None if m["valor"] is None else Decimal(m["valor"]).quantize(
                Decimal("0.01")),
            "nivel_canal": m["nivel_canal"], "n_pendientes": m["n_pendientes"],
            "pendientes": json.loads(json.dumps(m["pendientes"])),
            "detalle": json.loads(json.dumps(m["detalle"])),
            "motivo": m["motivo"], "medido_en": _fecha(m["medido_en"]),
            "capturado_en": capturado_en}


def _vieja(f, cap, cap_e):
    """La MISMA fila ancha tal como la devolvía la tabla de antes de la 0053
    (`enrich.listing_quality`): lo que `_armar` recibía y la pantalla veía."""
    v = {**f, "calculated_at": _fecha(f.get("calculated_at")), "capturado_en": cap,
         "n_pendientes": int(f.get("n_pendientes") or 0)}
    if calidad_ml._con_experiencia(f):
        v["exp_capturado_en"] = cap_e
        if f["exp_estado"] != calidad_ml.EXP_MEDIDA:
            v["exp_valor"] = None
    else:
        v.update(dict.fromkeys(calidad_ml._COLS_EXP), exp_razon=[],
                 exp_recomendaciones=[], exp_capturado_en=None)
    return v


class _BaseGuardar(unittest.TestCase):
    def _guardar(self, filas, cuentas=_CUENTAS):
        cursor = mock.MagicMock()
        with mock.patch.object(calidad_ml.sdb, "get_cursor", return_value=_Ctx(cursor)), \
                mock.patch("psycopg2.extras.execute_values") as ev:
            n = calidad_ml.guardar(filas, cuentas)
        return n, cursor, ev

    def _lote(self, ev):
        """Las filas del lote como dicts, en el orden de `_COLS_SALUD`."""
        _, _sql, valores = ev.call_args[0][:3]
        return [dict(zip(calidad_ml._COLS_SALUD, v)) for v in valores]

    def _por_llave(self, ev):
        return {(f["account_id"], f["listing_id"], f["metrica"]): f
                for f in self._lote(ev)}


class Guardar(_BaseGuardar):
    def test_deduplica_marca_via_y_un_solo_lote(self):
        n, cursor, ev = self._guardar([_fila("MLM1", score=10), _fila("MLM2"),
                                       _fila("MLM1", score=77)])
        self.assertEqual(n, 2)                              # publicaciones
        primera = cursor.execute.call_args_list[0][0][0]
        self.assertIn("set_config('app.via', 'calidad_ml', true)", primera)
        ev.assert_called_once()
        lote = self._lote(ev)
        self.assertEqual([(f["listing_id"], f["metrica"]) for f in lote],
                         [("MLM1", "calidad"), ("MLM2", "calidad")])
        self.assertEqual(lote[0]["valor"], 77)              # gana la última
        self.assertEqual({(f["canal"], f["account_id"]) for f in lote},
                         {("mercado_libre", _ID_BEKURA)})
        plantilla = ev.call_args.kwargs["template"]
        self.assertEqual(plantilla.count("%s"), len(calidad_ml._COLS_SALUD))
        for cast in ("::uuid", "::numeric", "::smallint", "::jsonb", "::timestamptz"):
            self.assertIn(cast, plantilla)
        todo = " ".join(str(c) for c in cursor.execute.call_args_list).lower()
        self.assertNotIn("set_session", todo)
        self.assertNotIn("read only", todo)

    def test_vacio_no_toca_la_base(self):
        with mock.patch.object(calidad_ml.sdb, "get_cursor") as gc, \
                mock.patch.object(calidad_ml.sdb, "fetch_all") as fa:
            self.assertEqual(calidad_ml.guardar([]), 0)
            self.assertEqual(calidad_ml.guardar([{"item_id": "", "cuenta": "B",
                                                  "estado": "medida"}]), 0)
            # Un estado que no es de calidad no llega al lote (el check lo
            # tumbaría entero).
            self.assertEqual(calidad_ml.guardar([_fila("MLM1", estado="raro")]), 0)
        gc.assert_not_called()
        fa.assert_not_called()                  # ni las cuentas se leen

    def test_sin_cuentas_las_lee_una_vez(self):
        cursor = mock.MagicMock()
        with mock.patch.object(calidad_ml.sdb, "fetch_all", return_value=[
                    {"legacy_code": "BEKURA", "id": _ID_BEKURA},
                    {"legacy_code": "sancorfashion ", "id": _ID_SANCOR}]) as fa, \
                mock.patch.object(calidad_ml.sdb, "get_cursor", return_value=_Ctx(cursor)), \
                mock.patch("psycopg2.extras.execute_values") as ev:
            n = calidad_ml.guardar([_fila("MLM1"), _fila("MLM2", cuenta="SANCORFASHION")])
        self.assertEqual(n, 2)
        fa.assert_called_once()
        sql, params = fa.call_args[0]
        self.assertIn("core.accounts", sql)
        self.assertIn("legacy_code", sql)
        self.assertEqual(params, {"canal": "mercado_libre"})
        self.assertEqual({f["listing_id"]: f["account_id"] for f in self._lote(ev)},
                         {"MLM1": _ID_BEKURA, "MLM2": _ID_SANCOR})

    def test_cuenta_sin_id_no_se_guarda_y_avisa(self):
        with self.assertLogs("omnicanal.calidad_ml", level="WARNING") as logs:
            n, _cursor, ev = self._guardar([_fila("MLM1"), _fila("MLM2", cuenta="OTRA")])
        self.assertEqual(n, 1)
        self.assertEqual([f["listing_id"] for f in self._lote(ev)], ["MLM1"])
        self.assertIn("OTRA", logs.output[0])

    def test_llave_multicanal_el_mismo_listing_en_otra_cuenta_no_se_pisa(self):
        # Mismo listing_id en las dos cuentas: DOS publicaciones, dos filas por
        # métrica, cada una con su account_id. La PK del upsert las separa.
        n, _cursor, ev = self._guardar([
            _fila("MLM1", cuenta="BEKURA", score=50),
            _fila("MLM1", cuenta="SANCORFASHION", score=90)])
        self.assertEqual(n, 2)
        por = self._por_llave(ev)
        self.assertEqual(por[(_ID_BEKURA, "MLM1", "calidad")]["valor"], 50)
        self.assertEqual(por[(_ID_SANCOR, "MLM1", "calidad")]["valor"], 90)
        sql = " ".join(ev.call_args[0][1].split())
        self.assertIn("on conflict (canal, account_id, listing_id, metrica) do update", sql)
        self.assertIn("on conflict (canal, account_id, listing_id, metrica, dia) do update",
                      sql)
        # La huella anterior se busca por la llave COMPLETA, no por listing_id.
        self.assertIn("p.canal = a.canal and p.account_id = a.account_id "
                      "and p.listing_id = a.listing_id and p.metrica = a.metrica", sql)

    def test_calidad_medida_por_metrica(self):
        f = _ancha(CRUDO_MEDIUM, "MLM3451327389")
        _n, _c, ev = self._guardar([f])
        (cal,) = self._lote(ev)
        self.assertEqual((cal["metrica"], cal["estado"], cal["valor"], cal["nivel"],
                          cal["nivel_canal"], cal["n_pendientes"], cal["motivo"],
                          cal["medido_en"]),
                         ("calidad", "medida", 60, "medio", "Estándar", 4, None,
                          "2026-09-22T01:40:00.109Z"))
        pend = json.loads(cal["pendientes"])
        # Cada pendiente UNA vez, en el orden de ML, con su grupo y sin nulos.
        self.assertEqual([(p["grupo"], p["clave"]) for p in pend], [
            ("USER_PRODUCT", "UP_SHORTS"),
            ("MLM3451327389", "UP_FREE_SHIPPING"), ("MLM3451327389", "UP_PROMOTIONS"),
            ("MLM3451327389", "UP_PRICE")])
        self.assertEqual(len(pend), f["n_pendientes"])
        self.assertFalse([p for p in pend if None in p.values()])
        det = json.loads(cal["detalle"])
        self.assertEqual(set(det), {"level", "level_wording", "user_product_id", "grupos"})
        self.assertEqual((det["level"], det["level_wording"], det["user_product_id"]),
                         ("medium", "Estándar", "MLMU5081808139"))
        self.assertEqual([g["clave"] for g in det["grupos"]],
                         ["USER_PRODUCT", "MLM3451327389"])
        for g in det["grupos"]:
            # Los pendientes NO se duplican dentro de los grupos.
            self.assertEqual(set(g), {"clave", "tipo", "titulo", "score", "estado",
                                      "n_variables"})
        self.assertIn("envío gratis", cal["pendientes"])      # ensure_ascii=False

    def test_no_calculada_sin_numero_ni_pendientes(self):
        _n, _c, ev = self._guardar([_no_calculada("MLM2")])
        (cal,) = self._lote(ev)
        self.assertEqual((cal["estado"], cal["valor"], cal["nivel"], cal["nivel_canal"],
                          cal["n_pendientes"], cal["medido_en"]),
                         ("no_calculada", None, None, None, None, None))
        self.assertEqual((json.loads(cal["pendientes"]), json.loads(cal["detalle"])),
                         ([], {}))
        self.assertEqual(cal["motivo"], PAUSADA_400["message"])

    def test_experiencia_por_metrica_y_sin_datos_nunca_0(self):
        e_rojo = calidad_ml.normalizar_experiencia(EXP_ROJO)
        e_gris = calidad_ml.normalizar_experiencia(EXP_GRIS)
        _n, _c, ev = self._guardar([
            _fila("MLM1"),                                  # la experiencia falló
            _fila("MLM2", **e_rojo),
            _fila("MLM3", **e_gris),
            # Aunque algo le pusiera un 0, sin_datos no lleva número.
            _fila("MLM5", **{**e_gris, "exp_valor": 0}),
            _fila("MLM4", exp_estado=None, exp_valor=77),   # ilegible: no cuenta
        ])
        por = self._por_llave(ev)
        # Sin experiencia leída: NO hay fila de experiencia (la de antes se
        # conserva intacta y no se escribe su historia).
        for iid in ("MLM1", "MLM4"):
            self.assertIn((_ID_BEKURA, iid, "calidad"), por)
            self.assertNotIn((_ID_BEKURA, iid, "experiencia"), por)
        rojo = por[(_ID_BEKURA, "MLM2", "experiencia")]
        self.assertEqual((rojo["estado"], rojo["valor"], rojo["nivel"], rojo["nivel_canal"],
                          rojo["n_pendientes"], rojo["motivo"], rojo["medido_en"]),
                         ("medida", 30, "malo", "Mala", None, None, None))
        self.assertEqual(json.loads(rojo["pendientes"]), [])
        det = json.loads(rojo["detalle"])
        self.assertEqual(tuple(det), calidad_ml._DETALLE_EXP)
        self.assertEqual((det["color"], det["ia"], det["por_categoria"], det["status_ml"]),
                         ("red", True, True, "active"))
        self.assertEqual(det["razon"], e_rojo["exp_razon"])
        self.assertIn("misma categoría", rojo["detalle"])   # ensure_ascii=False
        for iid in ("MLM3", "MLM5"):
            gris = por[(_ID_BEKURA, iid, "experiencia")]
            self.assertEqual((gris["estado"], gris["valor"], gris["nivel"]),
                             ("sin_datos", None, None), iid)
            det_g = json.loads(gris["detalle"])
            # Las nueve llaves siempre, aunque valgan null: huella estable.
            self.assertEqual(set(det_g), set(calidad_ml._DETALLE_EXP))
            self.assertEqual(det_g["color"], "gray")

    def test_niveles_normalizados(self):
        casos = {"good": "bueno", "medium": "medio", "bad": "malo", "otro": None}
        for level, nivel in casos.items():
            self.assertEqual(calidad_ml._salud_calidad(_fila("MLM1", level=level))["nivel"],
                             nivel, level)
        for color, nivel in {"green": "bueno", "orange": "medio", "yellow": "medio",
                             "red": "malo", "violeta": None}.items():
            f = _fila("MLM1", exp_estado="medida", exp_valor=50, exp_color=color)
            self.assertEqual(calidad_ml._salud_experiencia(f)["nivel"], nivel, color)


class GuardarHistoria(_BaseGuardar):
    """La historia diaria vive en el SQL (una sentencia): aquí se fija su forma.
    Su COMPORTAMIENTO real (una fila por día, la segunda del día reemplaza,
    huella igual → NULL, cambia → se guarda) lo prueba contra el sandbox
    `test_calidad_ml_sandbox.py`."""

    def _sql(self):
        _n, _c, ev = self._guardar([_fila("MLM1")])
        return " ".join(ev.call_args[0][1].split())

    def test_una_sola_sentencia_actual_mas_historia(self):
        sql = self._sql()
        self.assertTrue(sql.startswith("with v ("))
        self.assertEqual(sql.count("values %s"), 1)
        self.assertIn("insert into enrich.listing_health as h", sql)
        self.assertIn("insert into enrich.listing_health_hist as hh", sql)
        self.assertEqual(sql.count("insert into"), 2)
        self.assertNotIn("update enrich", sql)
        self.assertNotIn("delete", sql)
        # La historia copia lo que el upsert DEJÓ en la tabla (returning).
        self.assertIn("from actual a", sql)
        self.assertIn("capturado_en = excluded.capturado_en", sql)

    def test_dia_de_mexico_y_una_fila_por_dia(self):
        sql = self._sql()
        hoy = "(now() at time zone 'America/Mexico_City')::date"
        self.assertIn(f"a.metrica, {hoy}, a.estado", sql)
        # Segunda medición del mismo día: reemplaza la fila del día.
        self.assertIn("on conflict (canal, account_id, listing_id, metrica, dia) "
                      "do update set", sql)
        for c in ("estado", "valor", "nivel", "nivel_canal", "n_pendientes", "huella",
                  "pendientes", "detalle", "capturado_en"):
            self.assertIn(f"{c} = excluded.{c}", sql)

    def test_huella_contra_el_dia_guardado_anterior(self):
        sql = self._sql()
        self.assertIn("md5(h.pendientes::text || h.detalle::text) as huella", sql)
        # El día ANTERIOR, nunca el de hoy: si no, la segunda medición del
        # día se compararía consigo misma y tiraría el jsonb.
        self.assertIn("and p.dia < (now() at time zone 'America/Mexico_City')::date "
                      "order by p.dia desc limit 1", sql)
        self.assertIn("case when ant.huella is distinct from a.huella "
                      "then a.pendientes end", sql)
        self.assertIn("case when ant.huella is distinct from a.huella "
                      "then a.detalle end", sql)

    def test_toda_la_tanda_en_una_transaccion(self):
        cursor = mock.MagicMock()
        ctx = mock.MagicMock()
        ctx.__enter__.return_value = cursor
        ctx.__exit__.return_value = False
        with mock.patch.object(calidad_ml.sdb, "get_cursor", return_value=ctx) as gc, \
                mock.patch("psycopg2.extras.execute_values") as ev:
            calidad_ml.guardar([_fila(f"MLM{i}", **calidad_ml.normalizar_experiencia(
                EXP_ROJO)) for i in range(150)], _CUENTAS)
        gc.assert_called_once()                 # un cursor = una transacción
        ev.assert_called_once()
        self.assertEqual(len(ev.call_args[0][2]), 300)      # 150 × 2 métricas
        self.assertEqual(ev.call_args.kwargs["page_size"], calidad_ml._PAGINA)


class Objetivo(unittest.TestCase):
    def test_sql_activas_hoy_en_mexico(self):
        capt = {}

        def fetch_all(sql, params):
            capt["sql"], capt["params"] = sql, params
            return [{"item_id": "MLM1", "cuenta": "BEKURA"}]
        with mock.patch.object(calidad_ml.sdb, "fetch_all", side_effect=fetch_all):
            out = calidad_ml.objetivo(0, cuenta="bekura")
        self.assertEqual(out, [{"item_id": "MLM1", "cuenta": "BEKURA"}])
        sql, p = " ".join(capt["sql"].split()), capt["params"]
        self.assertIn("channel.listings", sql)
        self.assertNotIn("listing_quality", sql)
        self.assertIn("left join enrich.listing_health c on c.canal = l.canal "
                      "and c.account_id = l.account_id and c.listing_id = l.listing_id "
                      "and c.metrica = 'calidad'", sql)
        self.assertIn("left join enrich.listing_health e on e.canal = l.canal "
                      "and e.account_id = l.account_id and e.listing_id = l.listing_id "
                      "and e.metrica = 'experiencia'", sql)
        self.assertIn("at time zone 'America/Mexico_City'", sql)
        self.assertIn("lower(coalesce(l.situacion,'')) = any(%(pp_activas)s)", sql)
        self.assertIn("group by 1, 2", sql)
        self.assertEqual(p["pp_activas"], ["active"])
        self.assertEqual(p["cuenta"], "BEKURA")
        self.assertIsNone(p["limite"])                     # 0 = sin tope

    def test_reincluye_las_filas_sin_experiencia_de_hoy(self):
        # Entra si NO tiene calidad de hoy, o si su experiencia no es de hoy
        # (no existe, o falló hoy) Y su calidad tiene más de 3 horas: tope de
        # ~3-4 reintentos al día, no 13.
        with mock.patch.object(calidad_ml.sdb, "fetch_all", return_value=[]) as fa:
            calidad_ml.objetivo(None)
        sql = " ".join(fa.call_args[0][0].split())
        hoy = "(now() at time zone 'America/Mexico_City')::date"
        self.assertIn("c.listing_id is null", sql)
        self.assertIn(f"(c.capturado_en at time zone 'America/Mexico_City')::date <> {hoy}",
                      sql)
        self.assertIn(
            "or ((e.capturado_en is null "
            f"or (e.capturado_en at time zone 'America/Mexico_City')::date <> {hoy}) "
            "and c.capturado_en < now() - interval '3 hours'))", sql)
        self.assertEqual(calidad_ml._REINTENTO_EXP_H, 3)
        # Sin calidad / calidad vieja / (experiencia vieja Y calidad de hace
        # >3 h): tres ramas de un OR; la de la experiencia exige el intervalo.
        donde = sql[sql.index("and (%(forzar)s or c.listing_id is null"):sql.index("group by")]
        # «forzar» abre el OR; luego las tres ramas de siempre.
        self.assertEqual(donde.count(" or "), 4)
        self.assertEqual(donde.count(" and c.capturado_en < now() - interval "), 1)
        self.assertIn("order by max(c.capturado_en) asc nulls first, "
                      "max(e.capturado_en) asc nulls first", sql)

    def test_limite_positivo_pasa(self):
        with mock.patch.object(calidad_ml.sdb, "fetch_all", return_value=[]) as fa:
            calidad_ml.objetivo(25)
        self.assertEqual(fa.call_args[0][1]["limite"], 25)
        self.assertIsNone(fa.call_args[0][1]["cuenta"])
        # El job nunca fuerza: solo el sembrador del sandbox.
        self.assertIs(fa.call_args[0][1]["forzar"], False)

    def test_forzar_viaja_como_parametro(self):
        with mock.patch.object(calidad_ml.sdb, "fetch_all", return_value=[]) as fa:
            calidad_ml.objetivo(None, forzar=True)
        self.assertIs(fa.call_args[0][1]["forzar"], True)


class Lecturas(unittest.TestCase):
    def setUp(self):
        calidad_ml._AVISADO_SIN_TABLA = False

    def test_sin_tabla_lanza_no_disponible_con_un_solo_aviso(self):
        # «No pude leer» NO es «sin medir»: sin la 0053 no se devuelve {} (que
        # quien llama pintaría como «sin medir»), se lanza CalidadNoDisponible.
        with mock.patch.object(calidad_ml.sdb, "disponible", return_value=True), \
                mock.patch.object(calidad_ml.sdb, "fetch_all",
                                  side_effect=_ErrPg("42P01")), \
                self.assertLogs("omnicanal.calidad_ml", level="WARNING") as logs:
            with self.assertRaises(calidad_ml.CalidadNoDisponible):
                calidad_ml.resumen_por_items(["MLM1"])
            with self.assertRaises(calidad_ml.CalidadNoDisponible):
                calidad_ml.resumen_por_items(["MLM2"])
            with self.assertRaises(calidad_ml.CalidadNoDisponible):
                calidad_ml.detalle_por_items(["MLM3"])
        self.assertEqual(len(logs.records), 1)

    def test_sin_base_lanza_no_disponible_y_sin_ids_es_vacio(self):
        with mock.patch.object(calidad_ml.sdb, "disponible", return_value=False), \
                mock.patch.object(calidad_ml.sdb, "fetch_all") as fa:
            with self.assertRaises(calidad_ml.CalidadNoDisponible):
                calidad_ml.resumen_por_items(["MLM1"])
            with self.assertRaises(calidad_ml.CalidadNoDisponible):
                calidad_ml.detalle_por_items(["MLM1"])
        fa.assert_not_called()
        with mock.patch.object(calidad_ml.sdb, "disponible", return_value=True), \
                mock.patch.object(calidad_ml.sdb, "fetch_all") as fa:
            self.assertEqual(calidad_ml.resumen_por_items([]), {})
        fa.assert_not_called()

    def test_consulta_que_corre_sin_filas_es_vacio(self):
        with mock.patch.object(calidad_ml.sdb, "disponible", return_value=True), \
                mock.patch.object(calidad_ml.sdb, "fetch_all", return_value=[]):
            self.assertEqual(calidad_ml.resumen_por_items(["MLM1"]), {})
            self.assertEqual(calidad_ml.detalle_por_items(["MLM1"]), {})

    def test_sin_columnas_tambien_es_no_disponible(self):
        # Una tabla que no es la de la 0053 (42703, UndefinedColumn). Tampoco
        # es «sin medir».
        with mock.patch.object(calidad_ml.sdb, "disponible", return_value=True), \
                mock.patch.object(calidad_ml.sdb, "fetch_all",
                                  side_effect=_ErrPg("42703")), \
                self.assertLogs("omnicanal.calidad_ml", level="WARNING") as logs:
            with self.assertRaises(calidad_ml.CalidadNoDisponible) as cm:
                calidad_ml.resumen_por_items(["MLM1"])
            with self.assertRaises(calidad_ml.CalidadNoDisponible):
                calidad_ml.detalle_por_items(["MLM1"])
        self.assertIn("0053", str(cm.exception))
        self.assertIn("listing_health", str(cm.exception))
        self.assertEqual(len(logs.records), 1)

    def test_otro_error_si_sube(self):
        with mock.patch.object(calidad_ml.sdb, "disponible", return_value=True), \
                mock.patch.object(calidad_ml.sdb, "fetch_all",
                                  side_effect=_ErrPg("57014")):
            with self.assertRaises(_ErrPg):
                calidad_ml.resumen_por_items(["MLM1"])

    _CAP = datetime(2026, 9, 22, 11, 5, tzinfo=timezone.utc)
    _CAP_E = datetime(2026, 9, 22, 11, 6, tzinfo=timezone.utc)

    def _anchas(self):
        """Las filas de `medir` del caso: medida con experiencia roja pausada,
        no calculada sin experiencia, medida (good) con experiencia gris."""
        return {"MLM3451327389": _ancha(CRUDO_MEDIUM, "MLM3451327389", exp=EXP_PAUSADA),
                "MLM2": _no_calculada("MLM2"),
                "MLM3": _ancha(CRUDO_GOOD, "MLM3", exp=EXP_GRIS)}

    def _filas_bd(self, anchas):
        """Lo que leería el SELECT de `_leer`: una fila por métrica."""
        filas = []
        for iid, f in anchas.items():
            for m in calidad_ml._filas_salud(f):
                cap = self._CAP if m["metrica"] == "calidad" else self._CAP_E
                filas.append(_como_bd(iid, m, cap))
        return filas

    def _leer(self, filas):
        capt = {}

        def fetch_all(sql, params):
            capt.setdefault("sql", []).append(sql)
            capt["params"] = params
            return filas
        with mock.patch.object(calidad_ml.sdb, "disponible", return_value=True), \
                mock.patch.object(calidad_ml.sdb, "fetch_all", side_effect=fetch_all):
            res = calidad_ml.resumen_por_items(["MLM3451327389", "MLM2", "MLM2", None,
                                                "MLM3"])
            det = calidad_ml.detalle_por_items(["MLM3451327389", "MLM2", "MLM3"])
        return res, det, capt

    def test_resumen_y_detalle(self):
        res, det, capt = self._leer(self._filas_bd(self._anchas()))
        self.assertEqual(capt["params"], {"canal": "mercado_libre",
                                          "ids": ["MLM2", "MLM3", "MLM3451327389"],
                                          "metricas": ["calidad", "experiencia"]})
        sql_res, sql_det = (" ".join(s.split()) for s in capt["sql"])
        self.assertIn("from enrich.listing_health", sql_res)
        self.assertIn("listing_id = any(%(ids)s)", sql_res)
        # El resumen no carga los textos largos de la experiencia.
        self.assertIn("detalle - 'razon' - 'recomendaciones' as detalle", sql_res)
        self.assertNotIn("'razon'", sql_det)

        # La forma de siempre: {id: {"calidad": …, "experiencia": …}}.
        self.assertEqual(set(res["MLM3451327389"]), {"calidad", "experiencia"})
        r = res["MLM3451327389"]["calidad"]
        self.assertEqual(set(r), {"estado", "score", "level", "level_wording",
                                  "n_pendientes", "pendientes_top", "capturado_en",
                                  "calculated_at", "motivo"})
        # El motivo viaja también en el resumen (lista y mosaico).
        self.assertIsNone(r["motivo"])
        self.assertEqual(res["MLM2"]["calidad"]["motivo"], PAUSADA_400["message"])
        self.assertEqual((r["score"], r["level"], r["level_wording"], r["n_pendientes"]),
                         (60, "medium", "Estándar", 4))
        self.assertIs(type(r["score"]), int)               # no Decimal ni 60.0
        # Del bucket de MENOR score (Condiciones de venta, 18) primero; tope 2.
        self.assertEqual([p["accion"] for p in r["pendientes_top"]],
                         ["Ofrecer envío", "Participar"])
        self.assertTrue(r["pendientes_top"][0]["link"].startswith("https://"))
        self.assertEqual(r["capturado_en"], "2026-09-22T11:05:00+00:00")
        self.assertEqual(r["calculated_at"], "2026-09-22T01:40:00.109000+00:00")
        c2 = res["MLM2"]["calidad"]
        self.assertEqual((c2["estado"], c2["score"], c2["level"], c2["n_pendientes"],
                          c2["pendientes_top"]), ("no_calculada", None, None, 0, []))

        # Experiencia medida (roja), en el resumen.
        e = res["MLM3451327389"]["experiencia"]
        self.assertEqual(set(e), {"estado", "valor", "color", "texto", "consecuencia",
                                  "accion_principal", "por_categoria", "capturado_en"})
        self.assertEqual((e["estado"], e["valor"], e["color"], e["texto"],
                          e["por_categoria"]), ("medida", 30, "red", "Mala", True))
        self.assertIs(type(e["valor"]), int)
        self.assertTrue(e["consecuencia"].startswith("Tienes muy baja exposición"))
        self.assertEqual(e["accion_principal"],
                         "Aclara en la publicación con fotos y accesorios.")
        self.assertEqual(e["capturado_en"], "2026-09-22T11:06:00+00:00")
        # Sin fila de experiencia → sin_medir y TODO en None (nada que pintar
        # como 0).
        e2 = res["MLM2"]["experiencia"]
        self.assertEqual(e2["estado"], "sin_medir")
        self.assertEqual({k: v for k, v in e2.items() if k != "estado"},
                         dict.fromkeys(set(e2) - {"estado"}))
        # Sin datos: el valor es None, nunca 0.
        e3 = res["MLM3"]["experiencia"]
        self.assertEqual((e3["estado"], e3["valor"], e3["color"]),
                         ("sin_datos", None, "gray"))

        d = det["MLM3451327389"]
        self.assertEqual(len(d["calidad"]["buckets"]), 2)
        self.assertEqual(d["calidad"]["user_product_id"], "MLMU5081808139")
        self.assertEqual(det["MLM2"]["calidad"]["motivo"], PAUSADA_400["message"])
        self.assertEqual(det["MLM2"]["calidad"]["buckets"], [])
        de = d["experiencia"]
        self.assertEqual(set(de), set(e) | {"razon", "recomendaciones", "ia",
                                            "status_ml", "status_texto"})
        self.assertEqual(len(de["razon"]), 1)
        self.assertEqual(len(de["recomendaciones"]), 1)
        self.assertIs(de["ia"], True)
        self.assertEqual((de["status_ml"], de["status_texto"]),
                         ("paused", "Tu publicación está inactiva."))
        de2 = det["MLM2"]["experiencia"]
        self.assertEqual((de2["estado"], de2["razon"], de2["recomendaciones"],
                          de2["ia"], de2["status_ml"]),
                         ("sin_medir", [], [], None, None))
        de3 = det["MLM3"]["experiencia"]
        self.assertEqual((de3["razon"], de3["ia"]), ([], False))

    def test_paridad_con_la_forma_de_antes(self):
        # Guardar en formato largo y leer de vuelta da EXACTAMENTE lo que
        # daba la fila ancha de antes de la 0053 (lista, mosaico y cajón no
        # cambian). Cubre medida/no_calculada × medida/sin_datos/sin_medir.
        anchas = self._anchas()
        anchas["MLM4"] = _ancha(CRUDO_GOOD, "MLM4", exp=EXP_NARANJA)
        anchas["MLM5"] = _no_calculada("MLM5", exp=EXP_VERDE)
        res, det, _ = self._leer(self._filas_bd(anchas))
        for iid, f in anchas.items():
            vieja = _vieja(f, self._CAP, self._CAP_E)
            self.assertEqual(res[iid], calidad_ml._armar(vieja, detalle=False), iid)
            self.assertEqual(det[iid], calidad_ml._armar(vieja, detalle=True), iid)

    def test_sin_datos_con_un_0_guardado_sale_null(self):
        # La 0053 no impide un 0 en `valor` de una sin_datos: la lectura tampoco
        # lo deja pasar.
        filas = self._filas_bd({"MLM3": _ancha(CRUDO_GOOD, "MLM3", exp=EXP_GRIS)})
        for f in filas:
            if f["metrica"] == "experiencia":
                f["valor"] = Decimal("0.00")
        res, det, _ = self._leer(filas)
        self.assertIsNone(res["MLM3"]["experiencia"]["valor"])
        self.assertIsNone(det["MLM3"]["experiencia"]["valor"])

    def test_jsonb_como_texto_y_experiencia_huerfana(self):
        # psycopg2 devuelve el jsonb como texto si alguien lo castea: se lee
        # igual. Una experiencia SIN calidad (no la escribe nadie: van en el
        # mismo lote) no inventa una publicación.
        filas = self._filas_bd({"MLM3": _ancha(CRUDO_GOOD, "MLM3", exp=EXP_GRIS)})
        for f in filas:
            f["pendientes"] = json.dumps(f["pendientes"])
            f["detalle"] = json.dumps(f["detalle"])
        huerfana = dict(filas[-1], listing_id="MLM9")
        res, det, _ = self._leer(filas + [huerfana])
        self.assertNotIn("MLM9", res)
        self.assertEqual(det["MLM3"]["calidad"]["level"], "good")
        self.assertEqual(len(det["MLM3"]["calidad"]["buckets"]), 2)
        self.assertEqual(det["MLM3"]["experiencia"]["color"], "gray")

    def test_buckets_se_rearman_con_sus_pendientes(self):
        f = _ancha(CRUDO_MEDIUM, "MLM3451327389")
        cal = calidad_ml._salud_calidad(f)
        rearmados = calidad_ml._buckets(cal["detalle"]["grupos"], cal["pendientes"])
        self.assertEqual(rearmados, f["buckets"])


class Conteo(unittest.TestCase):
    def setUp(self):
        calidad_ml._AVISADO_SIN_TABLA = False

    def test_agrega_por_nivel_desde_los_datos(self):
        t1 = datetime(2026, 9, 22, 11, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc)
        filas = [
            {"estado": None, "level": None, "level_wording": None, "n": 5,
             "score_medio": None, "ultima": None},
            {"estado": "medida", "level": "medium", "level_wording": "Estándar", "n": 36,
             "score_medio": 61.0, "ultima": t1},
            {"estado": "medida", "level": "good", "level_wording": "Profesional", "n": 73,
             "score_medio": 82.0, "ultima": t2},
            {"estado": "no_calculada", "level": None, "level_wording": None, "n": 11,
             "score_medio": None, "ultima": t1},
        ]
        with mock.patch.object(calidad_ml.sdb, "disponible", return_value=True), \
                mock.patch.object(calidad_ml.sdb, "fetch_all", return_value=filas) as fa:
            c = calidad_ml.conteo("sancorfashion")
        self.assertEqual(c["niveles"], [
            {"level": "good", "level_wording": "Profesional", "n": 73},
            {"level": "medium", "level_wording": "Estándar", "n": 36},
        ])
        self.assertEqual((c["no_calculada"], c["sin_medir"], c["total_activas"]),
                         (11, 5, 125))
        self.assertEqual(c["ultima_captura"], "2026-09-22T12:30:00+00:00")
        sql, params = fa.call_args[0]
        self.assertIn("pp_activas", sql)
        self.assertEqual(params["cuenta"], "SANCORFASHION")
        # Filas sin experiencia: todas «sin medir».
        self.assertEqual(c["experiencia"], {"colores": [], "sin_datos": 0,
                                            "sin_medir": 125})

    def test_sql_las_dos_metricas_lado_a_lado(self):
        with mock.patch.object(calidad_ml.sdb, "fetch_all", return_value=[]) as fa:
            c = calidad_ml.conteo(None)
        sql = " ".join(fa.call_args[0][0].split())
        self.assertNotIn("listing_quality", sql)
        self.assertIn("select distinct l.canal, l.account_id, l.listing_id", sql)
        self.assertIn("left join enrich.listing_health c on c.canal = x.canal "
                      "and c.account_id = x.account_id and c.listing_id = x.listing_id "
                      "and c.metrica = 'calidad'", sql)
        self.assertIn("left join enrich.listing_health e on e.canal = x.canal "
                      "and e.account_id = x.account_id and e.listing_id = x.listing_id "
                      "and e.metrica = 'experiencia'", sql)
        # Los alias de siempre: el agregado en Python no cambió.
        for alias in ("c.estado,", "c.detalle ->> 'level' as level",
                      "c.nivel_canal as level_wording", "e.estado as exp_estado",
                      "e.detalle ->> 'color' as exp_color", "e.nivel_canal as exp_texto",
                      "avg(c.valor)::float as score_medio",
                      "avg(e.valor)::float as exp_medio",
                      "max(c.capturado_en) as ultima"):
            self.assertIn(alias, sql)
        self.assertIn("group by 1, 2, 3, 4, 5, 6", sql)
        # Sin activas, ceros de verdad (la consulta sí corrió).
        self.assertEqual((c["total_activas"], c["sin_medir"], c["niveles"]), (0, 0, []))

    def test_experiencia_por_color_desde_los_datos(self):
        # Las filas vienen agrupadas por (calidad × experiencia): la calidad se
        # suma a través de los colores y la experiencia a través de los niveles.
        def f(estado, level, wording, exp_estado, color, texto, n, score, exp_medio):
            return {"estado": estado, "level": level, "level_wording": wording,
                    "exp_estado": exp_estado, "exp_color": color, "exp_texto": texto,
                    "n": n, "score_medio": score, "exp_medio": exp_medio,
                    "ultima": None}
        filas = [
            f(None, None, None, None, None, None, 4, None, None),             # sin fila
            f("medida", "good", "Profesional", "medida", "green", "Buena", 20, 82.0, 90.0),
            f("medida", "good", "Profesional", "medida", "red", "Mala", 7, 80.0, 30.0),
            f("medida", "medium", "Estándar", "medida", "green", "Buena", 4, 60.0, 75.0),
            f("medida", "medium", "Estándar", "medida", "orange", "Media", 17, 61.0, 58.0),
            f("medida", "medium", "Estándar", "medida", "red", "Mala", 12, 59.0, 30.0),
            f("medida", "medium", "Estándar", "sin_datos", "gray", None, 90, 60.0, None),
            f("no_calculada", None, None, "medida", "orange", "Media", 1, None, 65.0),
            f("medida", "good", "Profesional", None, None, None, 5, 83.0, None),  # sin exp
        ]
        with mock.patch.object(calidad_ml.sdb, "fetch_all", return_value=filas):
            c = calidad_ml.conteo(None)
        # Del valor promedio más BAJO al más alto: «Mala», «Media», «Buena».
        self.assertEqual(c["experiencia"]["colores"], [
            {"color": "red", "texto": "Mala", "n": 19},
            {"color": "orange", "texto": "Media", "n": 18},
            {"color": "green", "texto": "Buena", "n": 24},
        ])
        self.assertEqual(c["experiencia"]["sin_datos"], 90)
        self.assertEqual(c["experiencia"]["sin_medir"], 4 + 5)
        # La calidad no cambia por partir las filas por color.
        self.assertEqual(c["niveles"], [
            {"level": "good", "level_wording": "Profesional", "n": 32},
            {"level": "medium", "level_wording": "Estándar", "n": 123},
        ])
        self.assertEqual((c["no_calculada"], c["sin_medir"], c["total_activas"]),
                         (1, 4, 160))

    def test_sin_tabla_no_inventa_ceros_sube_para_el_503(self):
        with mock.patch.object(calidad_ml.sdb, "fetch_all",
                               side_effect=_ErrPg("42P01")), \
                self.assertLogs("omnicanal.calidad_ml", level="WARNING"):
            with self.assertRaises(_ErrPg):
                calidad_ml.conteo(None)


# ═════════════════════════════════════════════════════════════════════════════
# El job
# ═════════════════════════════════════════════════════════════════════════════

class Job(unittest.TestCase):
    def setUp(self):
        calidad_ml._estado["fase"] = "inactivo"
        # Las cuentas (legacy_code → account_id) se leen UNA vez por barrido.
        self.ids_cuentas = mock.Mock(name="ids_de_cuentas", return_value=dict(_CUENTAS))
        parche = mock.patch.object(calidad_ml, "ids_de_cuentas", self.ids_cuentas)
        parche.start()
        self.addCleanup(parche.stop)

    def test_corrida_programada_no_hace_nada_con_el_flag_apagado(self):
        disparo = mock.AsyncMock()
        with mock.patch.object(calidad_ml, "refrescar_en_fondo", disparo), \
                mock.patch.object(calidad_ml.settings, "sync_enabled", True), \
                mock.patch.object(calidad_ml.settings, "calidad_ml_enabled", False), \
                mock.patch.object(calidad_ml.settings, "calidad_ml_hora_utc", 0):
            _run(calidad_ml.corrida_programada())
        disparo.assert_not_awaited()

    def test_corrida_programada_obedece_sync_enabled(self):
        disparo = mock.AsyncMock()
        with mock.patch.object(calidad_ml, "refrescar_en_fondo", disparo), \
                mock.patch.object(calidad_ml.settings, "sync_enabled", False), \
                mock.patch.object(calidad_ml.settings, "calidad_ml_enabled", True), \
                mock.patch.object(calidad_ml.settings, "calidad_ml_hora_utc", 0):
            _run(calidad_ml.corrida_programada())
        disparo.assert_not_awaited()

    def test_corrida_programada_espera_la_hora(self):
        disparo = mock.AsyncMock()
        with mock.patch.object(calidad_ml, "refrescar_en_fondo", disparo), \
                mock.patch.object(calidad_ml.settings, "sync_enabled", True), \
                mock.patch.object(calidad_ml.settings, "calidad_ml_enabled", True), \
                mock.patch.object(calidad_ml.settings, "calidad_ml_hora_utc", 24):
            _run(calidad_ml.corrida_programada())
        disparo.assert_not_awaited()

    def test_corrida_programada_dispara_con_todo_encendido(self):
        disparo = mock.AsyncMock()
        with mock.patch.object(calidad_ml, "refrescar_en_fondo", disparo), \
                mock.patch.object(calidad_ml.settings, "sync_enabled", True), \
                mock.patch.object(calidad_ml.settings, "calidad_ml_enabled", True), \
                mock.patch.object(calidad_ml.settings, "calidad_ml_hora_utc", 0), \
                mock.patch.object(calidad_ml.settings, "calidad_ml_por_corrida", 0):
            _run(calidad_ml.corrida_programada())
        disparo.assert_awaited_once_with(None, "programada")

    def test_barrido_completo_con_renovador_con_candado_y_bitacora(self):
        lista = [{"item_id": f"MLM{i}", "cuenta": "BEKURA" if i % 2 else "SANCORFASHION"}
                 for i in range(5)]
        medir = mock.AsyncMock(side_effect=lambda pares, tokens, renovar=None,
                               renovados=None: (
            [_fila(i, c) for i, c in pares],
            {"ok": len(pares), "no_calculada": 0, "limitada_429": 0,
             "sin_token": 0, "error": 0}))
        bitacora = mock.Mock(name="_bitacora")
        guardar = mock.Mock(name="guardar", side_effect=lambda filas, cuentas: len(filas))
        hilos: list[str] = []
        real_to_thread = asyncio.to_thread

        async def espia(fn, *a, **k):
            hilos.append(getattr(fn, "__name__", repr(fn)))
            return await real_to_thread(fn, *a, **k)

        async def correr():
            est = await calidad_ml.refrescar_en_fondo(limite=None, motivo="manual")
            self.assertEqual(est["fase"], "arrancando")
            # Mientras corre, un segundo disparo no encola otro.
            otro = await calidad_ml.refrescar_en_fondo(limite=None, motivo="manual")
            self.assertEqual(otro["fase"], "arrancando")
            await asyncio.gather(*list(calidad_ml._tareas))

        with mock.patch.object(calidad_ml, "objetivo", return_value=lista) as obj, \
                mock.patch.object(calidad_ml.meli, "_access_token",
                                  side_effect=lambda c: f"tok-{c}"), \
                mock.patch.object(calidad_ml, "medir", medir), \
                mock.patch.object(calidad_ml, "guardar", guardar), \
                mock.patch.object(calidad_ml, "_bitacora", bitacora), \
                mock.patch.object(calidad_ml.asyncio, "to_thread", espia):
            _run(correr())
        obj.assert_called_once_with(None)
        self.assertEqual(medir.await_count, 1)
        _, kwargs = medir.await_args
        self.assertIs(kwargs["renovar"], calidad_ml.meli._renovar_con_candado)
        self.assertIsInstance(kwargs["renovados"], set)
        self.assertEqual(guardar.call_count, 1)
        # Cada lote se guarda con el mapa de cuentas del barrido, leído una vez.
        self.assertEqual(guardar.call_args[0][1], _CUENTAS)
        self.ids_cuentas.assert_called_once_with()
        estado, detalle, _dur = bitacora.call_args[0]
        self.assertEqual(estado, "ok")
        self.assertEqual((detalle["objetivo"], detalle["ok"], detalle["guardadas"]),
                         (5, 5, 5))
        self.assertEqual(calidad_ml.estado()["fase"], "listo")
        # Regla 11: la base y el token, siempre en hilo.
        for nombre in ("objetivo", "ids_de_cuentas", "_access_token", "guardar",
                       "_bitacora"):
            self.assertIn(nombre, " ".join(hilos))

    def test_401_persistente_renueva_una_vez_por_cuenta_en_todo_el_barrido(self):
        # 100 publicaciones de dos cuentas, en tandas de 20 (5 tandas), y ML
        # contesta 401 a todo aun con el token renovado. `renovar` corre UNA
        # vez por cuenta en todo el barrido, no una por tanda ni por item.
        lista = [{"item_id": f"MLM{i}", "cuenta": "BEKURA" if i % 2 else "SANCORFASHION"}
                 for i in range(100)]
        n_renov: dict[str, int] = {}

        async def renovar(cuenta):
            n_renov[cuenta] = n_renov.get(cuenta, 0) + 1
            return f"nuevo-{cuenta}-{n_renov[cuenta]}"
        renovador = mock.AsyncMock(side_effect=renovar)
        peticiones: list[httpx.Request] = []

        def handler(request):
            peticiones.append(request)
            return httpx.Response(401)

        def fabrica():
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ML)
        bitacora = mock.Mock()
        guardar = mock.Mock(side_effect=lambda filas, cuentas: len(filas))
        with mock.patch.object(calidad_ml, "objetivo", return_value=lista), \
                mock.patch.object(calidad_ml.meli, "_access_token",
                                  side_effect=lambda c: f"tok-{c}"), \
                mock.patch.object(calidad_ml.meli, "_renovar_con_candado", renovador), \
                mock.patch.object(calidad_ml, "_nuevo_cliente", side_effect=fabrica), \
                mock.patch.object(calidad_ml, "_TANDA", 20), \
                mock.patch.object(calidad_ml, "guardar", guardar), \
                mock.patch.object(calidad_ml, "_bitacora", bitacora):
            async def correr():
                await calidad_ml.refrescar_en_fondo()
                await asyncio.gather(*list(calidad_ml._tareas))
            _run(correr())
        self.assertEqual(n_renov, {"BEKURA": 1, "SANCORFASHION": 1})
        estado, detalle, _ = bitacora.call_args[0]
        self.assertEqual(estado, "parcial")
        self.assertEqual((detalle["sin_token"], detalle["ok"], detalle["guardadas"]),
                         (100, 0, 0))
        # Solo la primera tanda pregunta; las otras cuatro nacen sin token.
        self.assertLessEqual(len(peticiones), 40)
        # Cinco tandas, UNA lectura de cuentas.
        self.assertEqual(guardar.call_count, 5)
        self.ids_cuentas.assert_called_once_with()

    def test_experiencia_con_error_es_parcial_y_se_reporta(self):
        lista = [{"item_id": "MLM1", "cuenta": "BEKURA"},
                 {"item_id": "MLM2", "cuenta": "BEKURA"}]
        medir = mock.AsyncMock(return_value=(
            [_fila("MLM1"), _fila("MLM2")],
            {"ok": 2, "no_calculada": 0, "limitada_429": 0, "sin_token": 0,
             "error": 0, "exp_ok": 1, "exp_sin_datos": 0, "exp_error": 1}))
        bitacora = mock.Mock()
        with mock.patch.object(calidad_ml, "objetivo", return_value=lista), \
                mock.patch.object(calidad_ml.meli, "_access_token", return_value="t"), \
                mock.patch.object(calidad_ml, "medir", medir), \
                mock.patch.object(calidad_ml, "guardar", side_effect=lambda f, cuentas: len(f)), \
                mock.patch.object(calidad_ml, "_bitacora", bitacora):
            async def correr():
                await calidad_ml.refrescar_en_fondo()
                await asyncio.gather(*list(calidad_ml._tareas))
            _run(correr())
        estado, detalle, _ = bitacora.call_args[0]
        # La fila quedó sin experiencia de hoy (vuelve a entrar): parcial.
        self.assertEqual(estado, "parcial")
        self.assertEqual((detalle["exp_ok"], detalle["exp_error"], detalle["guardadas"]),
                         (1, 1, 2))
        est = calidad_ml.estado()
        self.assertEqual((est["fase"], est["exp_error"]), ("listo", 1))
        self.assertIn("experiencia: 1 medidas", est["detalle"])
        self.assertIn("1 con error", est["detalle"])

    def test_403_de_experiencia_es_rechazada_y_la_bitacora_queda_ok(self):
        # Barrido real (medir sin mock): la calidad sale y la experiencia
        # contesta 403 en una. Es un «no» de ML: exp_rechazada, y la bitácora
        # queda 'ok' porque todo lo demás salió.
        lista = [{"item_id": "MLM1", "cuenta": "BEKURA"},
                 {"item_id": "MLM2", "cuenta": "BEKURA"}]

        def handler(req):
            if _es_experiencia(req):
                if _item_de(req) == "MLM2":
                    return httpx.Response(403, json={"message": "forbidden"})
                return httpx.Response(200, json=EXP_ROJO)
            return httpx.Response(200, json=CRUDO_MEDIUM)

        def fabrica():
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ML)
        bitacora = mock.Mock()
        with mock.patch.object(calidad_ml, "objetivo", return_value=lista), \
                mock.patch.object(calidad_ml.meli, "_access_token", return_value="t"), \
                mock.patch.object(calidad_ml, "_nuevo_cliente", side_effect=fabrica), \
                mock.patch.object(calidad_ml, "guardar", side_effect=lambda f, cuentas: len(f)), \
                mock.patch.object(calidad_ml, "_bitacora", bitacora):
            async def correr():
                await calidad_ml.refrescar_en_fondo()
                await asyncio.gather(*list(calidad_ml._tareas))
            _run(correr())
        estado, detalle, _ = bitacora.call_args[0]
        self.assertEqual(estado, "ok")
        self.assertEqual((detalle["ok"], detalle["exp_ok"], detalle["exp_rechazada"],
                          detalle["exp_error"], detalle["guardadas"]), (2, 1, 1, 0, 2))
        est = calidad_ml.estado()
        self.assertEqual((est["fase"], est["exp_rechazada"]), ("listo", 1))
        self.assertIn("1 rechazadas por ML", est["detalle"])

    def test_la_experiencia_sigue_el_302_al_user_product(self):
        # Medido en producción: la ruta por item redirige (302) a la del user
        # product en el mismo host. El cliente real tiene que seguirla.
        lista = [{"item_id": "MLM1", "cuenta": "BEKURA"}]
        vistas = []

        def handler(req):
            vistas.append((req.url.path, req.headers.get("authorization")))
            if req.url.path.startswith("/reputation/items/"):
                return httpx.Response(302, headers={
                    "location": "https://api.mercadolibre.com/reputation/user_products/"
                                "MLMU1/purchase_experience/integrators?locale=es_MX"})
            if req.url.path.startswith("/reputation/user_products/"):
                return httpx.Response(200, json=EXP_ROJO)
            return httpx.Response(200, json=CRUDO_MEDIUM)

        real = calidad_ml._nuevo_cliente

        def fabrica():
            cli = real()
            cli._transport = httpx.MockTransport(handler)
            return cli
        with mock.patch.object(calidad_ml, "_nuevo_cliente", side_effect=fabrica):
            filas, conteo = _run(calidad_ml.medir([("MLM1", "BEKURA")], {"BEKURA": "t"}))
        self.assertEqual((conteo["exp_ok"], conteo["exp_error"]), (1, 0))
        self.assertEqual(filas[0]["exp_valor"], 30)
        # El token viajó también a la ruta redirigida (mismo host).
        self.assertIn(("/reputation/user_products/MLMU1/purchase_experience/integrators",
                       "Bearer t"), vistas)

    def test_rechazo_masivo_de_la_experiencia_es_parcial(self):
        # Si ML empieza a rechazar la experiencia de casi todo (p. ej. cambió
        # los parámetros que exige), el barrido no puede decir «ok».
        lista = [{"item_id": f"MLM{i}", "cuenta": "BEKURA"} for i in range(12)]

        def handler(req):
            if _es_experiencia(req):
                return httpx.Response(400, json={"message": "Missing or invalid locale"})
            return httpx.Response(200, json=CRUDO_MEDIUM)

        def fabrica():
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ML)
        bitacora = mock.Mock()
        with mock.patch.object(calidad_ml, "objetivo", return_value=lista),                 mock.patch.object(calidad_ml.meli, "_access_token", return_value="t"),                 mock.patch.object(calidad_ml, "_nuevo_cliente", side_effect=fabrica),                 mock.patch.object(calidad_ml, "guardar", side_effect=lambda f, cuentas: len(f)),                 mock.patch.object(calidad_ml, "_bitacora", bitacora):
            async def correr():
                await calidad_ml.refrescar_en_fondo()
                await asyncio.gather(*list(calidad_ml._tareas))
            _run(correr())
        estado, detalle, _ = bitacora.call_args[0]
        self.assertEqual(estado, "parcial")
        self.assertTrue(detalle["rechazo_masivo"])
        self.assertEqual(detalle["exp_rechazada"], 12)

    def test_sin_token_de_una_cuenta_es_parcial(self):
        lista = [{"item_id": "MLM1", "cuenta": "BEKURA"}]
        bitacora = mock.Mock()
        with mock.patch.object(calidad_ml, "objetivo", return_value=lista), \
                mock.patch.object(calidad_ml.meli, "_access_token", return_value=None), \
                mock.patch.object(calidad_ml, "guardar", return_value=0), \
                mock.patch.object(calidad_ml, "_bitacora", bitacora):
            async def correr():
                await calidad_ml.refrescar_en_fondo()
                await asyncio.gather(*list(calidad_ml._tareas))
            _run(correr())
        estado, detalle, _ = bitacora.call_args[0]
        self.assertEqual(estado, "parcial")
        self.assertEqual(detalle["cuentas_sin_token"], ["BEKURA"])
        self.assertEqual(detalle["sin_token"], 1)

    def test_sin_cuentas_no_pregunta_a_ml_y_es_error(self):
        # Sin el mapa de cuentas no hay dónde escribir: no se le pregunta nada
        # a ML (sus respuestas se tirarían) y el barrido queda en error.
        self.ids_cuentas.side_effect = RuntimeError("kubera no contesta")
        medir = mock.AsyncMock()
        bitacora = mock.Mock()
        with mock.patch.object(calidad_ml, "objetivo",
                               return_value=[{"item_id": "MLM1", "cuenta": "BEKURA"}]),                 mock.patch.object(calidad_ml, "medir", medir),                 mock.patch.object(calidad_ml.meli, "_access_token") as tok,                 mock.patch.object(calidad_ml, "_bitacora", bitacora):
            async def correr():
                await calidad_ml.refrescar_en_fondo()
                await asyncio.gather(*list(calidad_ml._tareas))
            _run(correr())
        medir.assert_not_awaited()
        tok.assert_not_called()
        self.assertEqual(calidad_ml.estado()["fase"], "error")
        self.assertIn("cuentas", calidad_ml.estado()["detalle"])
        self.assertEqual(bitacora.call_args[0][0], "parcial")

    def test_nada_que_medir_no_anota(self):
        bitacora = mock.Mock()
        with mock.patch.object(calidad_ml, "objetivo", return_value=[]), \
                mock.patch.object(calidad_ml, "_bitacora", bitacora):
            async def correr():
                await calidad_ml.refrescar_en_fondo(motivo="programada")
                await asyncio.gather(*list(calidad_ml._tareas))
            _run(correr())
        bitacora.assert_not_called()
        self.assertEqual(calidad_ml.estado()["fase"], "listo")


class _SchedFalso:
    """AsyncIOScheduler de mentira: apunta los add_job y no arranca nada."""
    def __init__(self, *a, **k):
        self.jobs: list[tuple[tuple, dict]] = []

    def add_job(self, *a, **k):
        self.jobs.append((a, k))

    def start(self):
        pass


class SchedulerYRutas(unittest.TestCase):
    def _jobs(self, sync, calidad):
        from services import scheduler as sch
        viejo = sch._scheduler
        sch._scheduler = None
        try:
            with mock.patch.object(sch, "AsyncIOScheduler", _SchedFalso),                     mock.patch.object(sch.settings, "sync_enabled", sync),                     mock.patch.object(sch.settings, "calidad_ml_enabled", calidad),                     mock.patch.object(sch.settings, "calidad_ml_min", 60):
                sch.iniciar()
                return {k.get("id"): (a, k) for a, k in sch._scheduler.jobs}
        finally:
            sch._scheduler = viejo

    def test_job_solo_con_las_dos_llaves(self):
        self.assertNotIn("calidad_ml", self._jobs(sync=True, calidad=False))
        self.assertNotIn("calidad_ml", self._jobs(sync=False, calidad=True))
        a, k = self._jobs(sync=True, calidad=True)["calidad_ml"]
        self.assertIs(a[0], calidad_ml.corrida_programada)
        self.assertEqual(a[1], "interval")
        self.assertEqual(k["minutes"], 60)
        self.assertEqual((k["max_instances"], k["coalesce"]), (1, True))
        self.assertIsNotNone(k["next_run_time"].tzinfo)

    def _cliente(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import sync as ruta_sync
        app = FastAPI()
        app.include_router(ruta_sync.router)
        return TestClient(app)

    def test_rutas_de_sync_exigen_las_dos_llaves(self):
        cli = self._cliente()
        disparo = mock.AsyncMock(return_value={"fase": "arrancando"})
        with mock.patch.object(calidad_ml, "refrescar_en_fondo", disparo),                 mock.patch.object(calidad_ml.settings, "sync_enabled", True),                 mock.patch.object(calidad_ml.settings, "calidad_ml_enabled", False):
            r = cli.post("/api/sync/calidad-ml")
            self.assertEqual(r.json(), {"ok": False, "motivo": "CALIDAD_ML_ENABLED apagado"})
            g = cli.get("/api/sync/calidad-ml").json()
            self.assertFalse(g["encendido"])
            self.assertIn("fase", g["estado"])
        with mock.patch.object(calidad_ml, "refrescar_en_fondo", disparo),                 mock.patch.object(calidad_ml.settings, "sync_enabled", False),                 mock.patch.object(calidad_ml.settings, "calidad_ml_enabled", True):
            r = cli.post("/api/sync/calidad-ml")
            self.assertFalse(r.json()["ok"])
        disparo.assert_not_awaited()
        with mock.patch.object(calidad_ml, "refrescar_en_fondo", disparo),                 mock.patch.object(calidad_ml.settings, "sync_enabled", True),                 mock.patch.object(calidad_ml.settings, "calidad_ml_enabled", True):
            r = cli.post("/api/sync/calidad-ml?limite=20")
        self.assertEqual(r.json(), {"ok": True, "estado": {"fase": "arrancando"}})
        disparo.assert_awaited_once_with(limite=20, motivo="manual")


# ═════════════════════════════════════════════════════════════════════════════
# Regla 11 estática y candados del sembrador
# ═════════════════════════════════════════════════════════════════════════════

class Regla11Estatica(unittest.TestCase):
    """Dentro de un `async def` de calidad_ml nada bloqueante se llama directo:
    ni `sdb.*`, ni `meli.*` síncrono, ni las funciones de base del módulo, ni
    `time.sleep`. Pasarlas como argumento a `asyncio.to_thread` sí vale."""

    BLOQUEANTES = {"objetivo", "guardar", "resumen_por_items", "detalle_por_items",
                   "conteo", "_bitacora", "_leer", "ids_de_cuentas", "_escribir_lote"}

    def test_nada_bloqueante_en_corrutinas(self):
        arbol = ast.parse((BACKEND / "services" / "calidad_ml.py").read_text(
            encoding="utf-8"))
        malas = []
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.AsyncFunctionDef):
                continue
            for llamada in ast.walk(nodo):
                if not isinstance(llamada, ast.Call):
                    continue
                f = llamada.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    if f.value.id == "sdb" or (f.value.id == "time" and f.attr == "sleep") \
                            or (f.value.id == "meli" and f.attr != "_renovar_con_candado"):
                        malas.append(f"{nodo.name}: {f.value.id}.{f.attr}")
                elif isinstance(f, ast.Name) and f.id in self.BLOQUEANTES:
                    malas.append(f"{nodo.name}: {f.id}")
        self.assertEqual(malas, [])


def _cargar_sembrador():
    antes = socket.getdefaulttimeout()
    spec = importlib.util.spec_from_file_location(
        "calidad_ml_sandbox", BACKEND / "scripts" / "calidad_ml_sandbox.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    socket.setdefaulttimeout(antes)
    return mod


class Sembrador(unittest.TestCase):
    RUTA = BACKEND / "scripts" / "calidad_ml_sandbox.py"

    def test_nunca_renueva_ni_marca_la_sesion(self):
        src = self.RUTA.read_text(encoding="utf-8")
        self.assertIn("renovar=None", src)
        self.assertNotIn("_renovar_con_candado", src)
        self.assertNotIn("refrescar_token", src)
        self.assertIn('cur.execute("set transaction read only")', src)
        bajo = src.lower()
        self.assertNotIn("set_session", bajo)
        self.assertNotIn("default_transaction_read_only", bajo)
        self.assertNotIn("session characteristics", bajo)

    def test_aborta_si_el_destino_es_produccion(self):
        mod = _cargar_sembrador()
        stdin = mock.Mock(side_effect=AssertionError("no debió leer secretos"))
        with mock.patch.object(mod, "_leer_archivo_env", return_value={
                "SUPABASE_DB_URL": "postgresql://postgres.tukwcvsiabcdef:x@h:6543/postgres"}), \
                mock.patch.object(mod, "_leer_stdin", stdin), \
                mock.patch.object(sys, "argv", ["calidad_ml_sandbox.py"]),                 contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                mod.main()
        self.assertIn("PRODUCCIÓN", str(cm.exception.code))
        stdin.assert_not_called()

    def test_aborta_si_el_dsn_de_stdin_no_es_produccion(self):
        mod = _cargar_sembrador()
        with mock.patch.object(mod, "_leer_archivo_env", return_value={
                "SUPABASE_DB_URL": "postgresql://postgres.yvootpbzabc:x@h:6543/postgres"}), \
                mock.patch.object(mod, "_leer_stdin", return_value={
                    "PROD_SUPABASE_DB_URL":
                        "postgresql://postgres.yvootpbzabc:x@h:6543/postgres",
                    "DB_ENCRYPTION_KEY": "k"}), \
                mock.patch.object(mod, "_leer_tokens") as tokens, \
                mock.patch.object(sys, "argv", ["calidad_ml_sandbox.py"]),                 contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                mod.main()
        self.assertIn("PROD_SUPABASE_DB_URL", str(cm.exception.code))
        tokens.assert_not_called()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    unittest.main()
