"""El multiget de ML: de `/items?ids=` a `/items/bulk?ids=` (v0.621.0).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
ML deprecó `/items?ids=` (convive con `/items/bulk?ids=` hasta el 25-oct-2026),
y el reemplazo contesta con OTRA FORMA. Las respuestas de abajo copian la forma
medida el 6-oct-2026 con 5 ids (uno propio, dos de la otra cuenta = 403, uno
inexistente y uno ajeno borrado = 404); los ids y los datos son INVENTADOS.

1. **La ruta y los params.** Por omisión `/items/bulk` y SIN `attributes`;
   con `ML_ITEMS_BULK=false`, `/items` con `attributes` como siempre.
2. **`normalizar` devuelve la forma legada `{code, body}`** desde cualquiera de
   las tres formas, con el código POR ID (200/403/404) y el body de error como
   el del legado (`id` y `status` numérico), en el orden en que llegó.
3. **Las trampas medidas no se cuelan en silencio:** `attributes` sin `body.`
   devuelve `[{"id"}]` para todos (no son 200 vacíos); con `body.*` cada fallo
   es `{}` (no se inventa un 404); un 200 con `body: {}` no es una ficha vacía.
   Se descartan y se avisa en el log. Una respuesta que no es lista también
   avisa (salvo None), y el Checklist la convierte en fallo: pausa la cuenta y
   enseña lo último leído, como en v0.620.0.
4. **Cada llamador da lo MISMO con la respuesta de bulk que con la legada**:
   Checklist, títulos de Competencia, ficha de ML (los 403/404 se siguen
   guardando como ficha vacía), Crear FULL, inventario FULL de un envío y los
   cuatro scripts. `alinear_ml_drop` sigue dejando fuera a los 403/404 aunque
   el caché (congelado) diga «active».
5. **El reintento tras un 401 sigue en bulk** (misma ruta, sin `attributes`) y
   con la política de token de cada sitio: el Checklist relee de la base, la
   ficha renueva con candado async, Crear FULL y el inventario FULL renuevan.
6. **La ficha de ML no bloquea el event loop** (regla 11): la base va en un
   hilo; si regresa a la corrutina, la prueba se cae.

Sin red ni base de datos.

    cd backend && python -m unittest tests.test_ml_multiget -v
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import logging
import sys
import time
import unittest
from unittest import mock

from config import Settings, settings
from services import ml_multiget as MG

PROPIA = "MLM1000000001"          # 200
OTRA_1 = "MLM1000000002"          # 403: de la otra cuenta
OTRA_2 = "MLM1000000003"          # 403
NO_EXISTE = "MLM0000000009"       # 404
BORRADA = "MLM1000000005"         # 404: ajena y borrada
IDS = [PROPIA, OTRA_1, OTRA_2, NO_EXISTE, BORRADA]
CODIGOS = {PROPIA: 200, OTRA_1: 403, OTRA_2: 403, NO_EXISTE: 404, BORRADA: 404}
_PROHIBIDO = "Access to the requested resource is forbidden"

ITEM = {
    "id": PROPIA, "site_id": "MLM", "title": "Mesa plegable de prueba",
    "status": "active", "sub_status": [], "available_quantity": 7, "price": 199.5,
    "category_id": "MLM0001", "inventory_id": "INV00001", "seller_custom_field": None,
    "date_created": "2025-01-02T03:04:05.000Z",
    "permalink": "https://articulo.mercadolibre.com.mx/MLM-1000000001-mesa-de-prueba-_JM",
    "thumbnail": "http://http2.mlstatic.com/D_000000-MLM0000000000_000000-I.jpg",
    "shipping": {"logistic_type": "xd_drop_off", "mode": "me2", "free_shipping": False},
    "attributes": [
        {"id": "BRAND", "name": "Marca", "value_id": "111", "value_name": "Marca Inventada",
         "values": [{"id": "111", "name": "Marca Inventada"}]},
        {"id": "PACKAGE_WEIGHT", "name": "Peso", "value_id": None, "value_name": "1250 g",
         "values": [{"id": None, "name": "1250 g"}]},
        {"id": "SELLER_SKU", "name": "SKU", "value_id": None, "value_name": "SKU-PRUEBA-01",
         "values": [{"id": None, "name": "SKU-PRUEBA-01"}]},
        {"id": "GTIN", "name": "GTIN", "value_id": "-1", "value_name": None, "values": []},
    ],
    "pictures": [{"id": "000000-MLM0000000000_000000"}], "variations": [],
}


def _recortado(campos: str | None) -> dict:
    if not campos:
        return copy.deepcopy(ITEM)
    quedan = {c.strip() for c in campos.split(",")} | {"id"}
    return {k: copy.deepcopy(v) for k, v in ITEM.items() if k in quedan}


def _mensaje(iid: str) -> str:
    return _PROHIBIDO if CODIGOS[iid] == 403 else f"Item with id {iid} not found"


def legado(campos: str | None = None, orden: list[str] | None = None) -> list[dict]:
    """`/items?ids=` (con `attributes`, ML ya recortaba el body del 200)."""
    salida = []
    for iid in orden or [OTRA_1, BORRADA, NO_EXISTE, PROPIA, OTRA_2]:
        if CODIGOS[iid] == 200:
            salida.append({"code": 200, "body": _recortado(campos)})
        else:
            salida.append({"code": CODIGOS[iid], "body": {
                "id": iid, "message": _mensaje(iid),
                "error": "access_denied" if CODIGOS[iid] == 403 else "not_found",
                "status": CODIGOS[iid], "cause": None if CODIGOS[iid] == 403 else []}})
    return salida


def bulk(orden: list[str] | None = None) -> list[dict]:
    """`/items/bulk?ids=` sin attributes: item completo; el fallo, SIN body."""
    salida = []
    for iid in orden or [PROPIA, OTRA_2, OTRA_1, NO_EXISTE, BORRADA]:
        if CODIGOS[iid] == 200:
            salida.append({"id": iid, "status_code": 200, "body": copy.deepcopy(ITEM)})
        else:
            salida.append({"id": iid, "status_code": CODIGOS[iid],
                           "error": {"message": _mensaje(iid)}})
    return salida


def bulk_con_body(campos: str) -> list[dict]:
    """`/items/bulk` con attributes=body.*: el 200 recortado; cada fallo, {}."""
    return [{"body": _recortado(campos)}, {}, {}, {}, {}]


def bulk_plano() -> list[dict]:
    """`/items/bulk` con attributes SIN `body.`: el filtro se come el sobre."""
    return [{"id": iid} for iid in [PROPIA, OTRA_2, OTRA_1, NO_EXISTE, BORRADA]]


def _resp(cuerpo, codigo: int = 200):
    r = mock.Mock(status_code=codigo)
    r.json.return_value = copy.deepcopy(cuerpo)
    r.raise_for_status.side_effect = None if codigo < 400 else RuntimeError(f"HTTP {codigo}")
    r.headers = {"content-type": "application/json"}
    return r


VIEJO, NUEVO = "viejo", "nuevo"     # el token vencido y el renovado


def _get_por_token(respuesta, llamadas: list):
    """`httpx.get` falso que contesta 401 al token VENCIDO y `respuesta` a
    cualquier otro: un sitio que no renueva se queda sin datos."""
    def get(url, params=None, headers=None, timeout=None):
        auth = (headers or {}).get("Authorization")
        llamadas.append((url, dict(params or {}), auth))
        return _resp(None, 401) if auth == f"Bearer {VIEJO}" else _resp(respuesta)
    return get


def _fuera_del_loop(valor=None):
    """Doble de algo que va a la base: falla si lo llaman DENTRO del event loop
    en vez de mandarlo a un hilo (regla 11)."""
    def fn(*a, **kw):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return copy.deepcopy(valor)
        raise AssertionError("llamada síncrona dentro del event loop (regla 11)")
    return fn


def _modo(bulk_: bool):
    return mock.patch.object(settings, "ml_items_bulk", bulk_)


def _por_id(sobres: list[dict]) -> dict[str, dict]:
    return {s["body"]["id"]: s for s in sobres}


# ── 1. Ruta y params ─────────────────────────────────────────────────────────

class RutaYParams(unittest.TestCase):
    def test_nace_encendido(self):
        self.assertIs(Settings.model_fields["ml_items_bulk"].default, True)

    def test_encendido_va_a_bulk_y_nunca_manda_attributes(self):
        with _modo(True):
            self.assertEqual(MG.ruta(), "/items/bulk")
            self.assertEqual(MG.params(["A", "B"], "id,status"), {"ids": "A,B"})
            self.assertEqual(MG.params(["A"], "body.id,body.status"), {"ids": "A"})

    def test_apagado_es_el_legado_tal_cual(self):
        with _modo(False):
            self.assertEqual(MG.ruta(), "/items")
            self.assertEqual(MG.params(["A", "B"], "id,status"),
                             {"ids": "A,B", "attributes": "id,status"})
            self.assertEqual(MG.params(["A"]), {"ids": "A"})

    def test_ids_ya_unidos_pasan_igual(self):
        with _modo(True):
            self.assertEqual(MG.params("A,B,C"), {"ids": "A,B,C"})


# ── 2 y 3. normalizar ────────────────────────────────────────────────────────

class Normalizar(unittest.TestCase):
    CAMPOS = "id,status,price"

    def test_el_legado_pasa_igual(self):
        resp = legado(self.CAMPOS)
        self.assertEqual(MG.normalizar(resp, self.CAMPOS), resp)

    def test_bulk_queda_en_la_forma_legada_con_el_codigo_por_id(self):
        sobres = _por_id(MG.normalizar(bulk(), self.CAMPOS))
        self.assertEqual({i: s["code"] for i, s in sobres.items()}, CODIGOS)
        self.assertEqual(sobres[PROPIA]["body"],
                         {"id": PROPIA, "status": "active", "price": 199.5})
        self.assertEqual(sobres[OTRA_1]["body"], {
            "id": OTRA_1, "message": _PROHIBIDO, "error": "access_denied", "status": 403})
        self.assertEqual(sobres[BORRADA]["body"]["status"], 404)
        self.assertEqual(sobres[BORRADA]["body"]["error"], "not_found")

    def test_bulk_equivale_al_legado(self):
        viejo = _por_id(MG.normalizar(legado(self.CAMPOS), self.CAMPOS))
        nuevo = _por_id(MG.normalizar(bulk(), self.CAMPOS))
        self.assertEqual(set(viejo), set(nuevo))
        for iid in IDS:
            v, n = viejo[iid], nuevo[iid]
            self.assertEqual(v["code"], n["code"], iid)
            if v["code"] == 200:
                self.assertEqual(v["body"], n["body"])
            else:     # el legado trae además `cause`; nadie lo lee
                for k in ("id", "message", "error", "status"):
                    self.assertEqual(v["body"][k], n["body"][k], (iid, k))

    def test_conserva_el_orden_de_la_respuesta_no_el_del_pedido(self):
        orden = [BORRADA, PROPIA, OTRA_2, NO_EXISTE, OTRA_1]
        sobres = MG.normalizar(bulk(orden))
        self.assertEqual([s["body"]["id"] for s in sobres], orden)

    def test_sin_campos_el_item_completo(self):
        sobres = _por_id(MG.normalizar(bulk()))
        self.assertEqual(sobres[PROPIA]["body"], ITEM)

    def test_campos_con_prefijo_body_y_anidados(self):
        sobres = _por_id(MG.normalizar(bulk(), "body.id,body.shipping.logistic_type"))
        self.assertEqual(sobres[PROPIA]["body"], {"id": PROPIA, "shipping": ITEM["shipping"]})

    def test_bulk_con_body_no_inventa_los_fallos(self):
        """Con attributes=body.* cada fallo es {}: no se sabe de qué id es, así
        que no aparece (no se inventa un 404)."""
        with self.assertLogs("omnicanal.ml_multiget", logging.WARNING) as avisos:
            sobres = MG.normalizar(bulk_con_body("id,status,price"), "id,status,price")
        self.assertEqual(sobres, [{"code": 200, "body": {
            "id": PROPIA, "status": "active", "price": 199.5}}])
        self.assertIn("4 de 5", avisos.output[0])

    def test_attributes_sin_body_no_se_cuelan_como_200_vacios(self):
        """La trampa más cara: `[{"id"}]` para TODOS. Si se leyera como un item
        sin datos, la ficha de ML guardaría 5 fichas vacías y el Checklist todo
        «no viva»."""
        with self.assertLogs("omnicanal.ml_multiget", logging.WARNING):
            self.assertEqual(MG.normalizar(bulk_plano(), "id,status"), [])

    def test_lo_que_ml_no_devolvio_no_aparece(self):
        self.assertEqual([s["body"]["id"] for s in MG.normalizar(bulk([PROPIA]))], [PROPIA])

    def test_un_200_sin_item_o_un_fallo_sin_id_se_descartan(self):
        """Un 200 con `body: {}` tampoco es dato: leído como item vacío, la
        ficha de ML guardaría la ficha en blanco y pisaría el peso medido."""
        with self.assertLogs("omnicanal.ml_multiget", logging.WARNING) as avisos:
            self.assertEqual(MG.normalizar([
                {"id": PROPIA, "status_code": 200},
                {"id": PROPIA, "status_code": 200, "body": {}},
                {"status_code": 404, "error": {"message": "x"}}], "id,title"), [])
        self.assertIn("3 de 3", avisos.output[0])

    def test_lo_que_no_es_lista_da_vacio_y_avisa(self):
        """El cambio de forma de ARRIBA (un objeto en vez de la lista) es el más
        silencioso: da [] pero deja el aviso."""
        for basura in ({"results": []}, {}, "error", 42):
            with self.assertLogs("omnicanal.ml_multiget", logging.WARNING) as avisos:
                self.assertEqual(MG.normalizar(basura), [])
            self.assertIn("respuesta sin forma conocida", avisos.output[0])

    def test_none_da_vacio_sin_avisar(self):
        """None es el error que el cliente ya registró (`competencia_ml._get`)."""
        with self.assertNoLogs("omnicanal.ml_multiget", logging.WARNING):
            self.assertEqual(MG.normalizar(None), [])


# ── 4. Cada llamador: bulk da lo mismo que el legado ─────────────────────────

class _Sitio(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)

    def _reintento_en_bulk(self, llamadas: list):
        """[(ruta, params, Authorization)]: la llamada del 401 y su reintento van
        las dos a /items/bulk, sin `attributes`, la segunda con el token nuevo."""
        self.assertEqual([a for _, _, a in llamadas], [f"Bearer {VIEJO}", f"Bearer {NUEVO}"])
        for ruta, par, _ in llamadas:
            self.assertTrue(ruta.endswith("/items/bulk"), ruta)
            self.assertEqual(set(par), {"ids"})
            self.assertEqual(sorted(par["ids"].split(",")), sorted(IDS))


class SitioChecklist(_Sitio):
    def _correr(self, modo: bool, respuesta):
        from services import checklist as ck
        from services import meli
        mock.patch.object(meli, "_access_token", return_value="tok").start()
        with _modo(modo), mock.patch("httpx.get", return_value=_resp(respuesta)) as get:
            return ck._multiget("BEKURA", IDS), get.call_args

    def test_401_relee_el_token_y_reintenta_en_bulk(self):
        from services import checklist as ck
        from services import meli
        viejo, _ = self._correr(False, legado("id,status,attributes"))
        llamadas: list = []
        mock.patch.object(meli, "_access_token", return_value=VIEJO).start()
        releer = mock.patch.object(meli, "releer_token", return_value=NUEVO).start()
        mock.patch.object(meli, "refrescar_token",
                          side_effect=AssertionError("el Checklist no renueva")).start()
        with _modo(True), mock.patch("httpx.get", side_effect=_get_por_token(bulk(), llamadas)):
            nuevo = ck._multiget("BEKURA", IDS)
        self.assertEqual(nuevo, viejo)
        releer.assert_called_once_with("BEKURA")
        self._reintento_en_bulk(llamadas)

    def test_respuesta_sin_lista_levanta(self):
        """v0.620.0 se caía con un AttributeError; si ahora diera {} en silencio,
        `_publicados` no pausaría la cuenta."""
        with self.assertRaisesRegex(RuntimeError, "sin lista"):
            self._correr(True, {"results": []})

    def test_respuesta_sin_lista_no_pisa_lo_ultimo_leido(self):
        """Por `_publicados`: la cuenta se pausa, se avisa «no contestó» y se
        enseña lo último leído. Sin el candado, cada id quedaría «no viva» y
        se guardaría 30 min encima de lo bueno."""
        from services import checklist as ck
        from services import meli
        mock.patch.object(meli, "_access_token", return_value="tok").start()
        mock.patch.object(ck, "_publicaciones_de",
                          return_value={"SKU-PRUEBA-01": [("BEKURA", PROPIA)]}).start()
        leido = {"estado": "active", "atributos": {"BRAND": "Marca Inventada"}}
        vencido = time.monotonic() - 2 * ck._PUB_TTL_S
        with mock.patch.dict(ck._pub_cache, {PROPIA: (vencido, leido)}, clear=True), \
             mock.patch.dict(ck._pub_pausa_hasta, clear=True), _modo(True), \
             mock.patch("httpx.get", return_value=_resp({"results": []})), \
             self.assertLogs("omnicanal.services.checklist", logging.WARNING):
            salida, info = ck._publicados(["SKU-PRUEBA-01"])
            self.assertEqual(ck._pub_cache[PROPIA], (vencido, leido))
            self.assertIn("BEKURA", ck._pub_pausa_hasta)
        self.assertEqual(salida, {"SKU-PRUEBA-01": {"BRAND": "Marca Inventada"}})
        self.assertIn("no contestó", info["error"])

    def test_bulk_da_lo_mismo_que_el_legado(self):
        campos = "id,status,attributes"
        viejo, llamada_v = self._correr(False, legado(campos))
        nuevo, llamada_n = self._correr(True, bulk())
        self.assertEqual(nuevo, viejo)
        self.assertEqual(nuevo[PROPIA], {"estado": "active", "atributos": {
            "BRAND": "Marca Inventada", "PACKAGE_WEIGHT": "1250 g",
            "SELLER_SKU": "SKU-PRUEBA-01"}})
        self.assertEqual(nuevo[OTRA_1], {"estado": None, "atributos": {}})
        self.assertTrue(llamada_n.args[0].endswith("/items/bulk"))
        self.assertEqual(llamada_n.kwargs["params"], {"ids": ",".join(IDS)})
        self.assertTrue(llamada_v.args[0].endswith("/items"))
        self.assertEqual(llamada_v.kwargs["params"]["attributes"], campos)


class SitioCompetencia(_Sitio):
    def _correr(self, modo: bool, respuesta):
        from services import competencia_ml as ML
        with _modo(modo), mock.patch.object(ML, "_get", return_value=copy.deepcopy(respuesta)) as g:
            return ML.datos_por_ids(IDS, "bekura"), g.call_args

    def test_bulk_da_lo_mismo_que_el_legado(self):
        viejo, llamada_v = self._correr(False, legado("id,title,permalink"))
        nuevo, llamada_n = self._correr(True, bulk())
        self.assertEqual(nuevo, viejo)
        self.assertEqual(nuevo, {PROPIA: {"titulo": ITEM["title"], "permalink": ITEM["permalink"]}})
        self.assertEqual(llamada_n.args[0], "/items/bulk")
        self.assertNotIn("attributes", llamada_n.args[1])
        self.assertEqual(llamada_v.args[0], "/items")


def _cliente_async(respuesta, llamadas: list):
    class Cliente:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, ruta, params=None, headers=None):
            auth = (headers or {}).get("Authorization")
            llamadas.append((ruta, dict(params or {}), auth))
            # Al token VENCIDO, 401: un sitio que no renueva se queda sin datos.
            return _resp(None, 401) if auth == f"Bearer {VIEJO}" else _resp(respuesta)
    return Cliente


class SitioFichaMl(_Sitio):
    def _correr(self, modo: bool, respuesta, token: str = "tok"):
        from services import ficha_ml, meli
        guardadas: list = []
        llamadas: list = []
        guardar = _fuera_del_loop()

        def ficha_guardar(filas):
            guardar()
            guardadas.extend(filas)
        # La base, solo desde un hilo: si regresa a la corrutina, esto revienta.
        mock.patch.object(ficha_ml, "_asegurar_tabla", side_effect=_fuera_del_loop()).start()
        mock.patch.object(ficha_ml, "leer", side_effect=_fuera_del_loop({})).start()
        mock.patch.object(meli, "access_token_async", mock.AsyncMock(return_value=token)).start()
        mock.patch.object(meli, "_access_token",
                          side_effect=AssertionError("síncrono en la corrutina")).start()
        self.renovar = mock.patch.object(meli, "_renovar_con_candado",
                                         mock.AsyncMock(return_value=NUEVO)).start()
        mock.patch.object(settings, "supabase_write_margenes", True).start()
        mock.patch("services.margenes_read.ficha_guardar", side_effect=ficha_guardar).start()
        with _modo(modo), mock.patch("httpx.AsyncClient", _cliente_async(respuesta, llamadas)):
            n = asyncio.run(ficha_ml.completar([("BEKURA", i) for i in IDS]))
        return n, sorted(guardadas), llamadas

    def test_401_renueva_con_candado_y_reintenta_en_bulk(self):
        viejo = self._correr(False, legado("id,title,attributes"))
        n, guardadas, llamadas = self._correr(True, bulk(), token=VIEJO)
        self.assertEqual((n, guardadas), viejo[:2])
        self.renovar.assert_awaited_once_with("BEKURA")
        self._reintento_en_bulk(llamadas)

    def test_bulk_guarda_lo_mismo_que_el_legado(self):
        viejo = self._correr(False, legado("id,title,attributes"))
        nuevo = self._correr(True, bulk())
        self.assertEqual(nuevo[:2], viejo[:2])
        n, guardadas, llamadas = nuevo
        self.assertEqual(n, 5)
        fichas = {g[0]: g for g in guardadas}
        self.assertEqual(fichas[PROPIA], (PROPIA, "BEKURA", ITEM["title"], 1250.0, True))
        # Los 403/404 se siguen guardando VACÍOS: no se repreguntan en una semana.
        self.assertEqual(fichas[BORRADA], (BORRADA, "BEKURA", "", None, False))
        self.assertEqual(llamadas, [("/items/bulk", {"ids": ",".join(IDS)}, "Bearer tok")])
        self.assertEqual(viejo[2][0][1]["attributes"], "id,title,attributes")

    def test_attributes_planos_no_pisan_los_pesos(self):
        """Si ML devolviera la forma del filtro plano, nada se guarda (antes de
        `normalizar`, las 5 se habrían guardado vacías, borrando el peso medido)."""
        with self.assertLogs("omnicanal.ml_multiget", logging.WARNING):
            n, guardadas, _ = self._correr(True, bulk_plano())
        self.assertEqual((n, guardadas), (0, []))


class SitioVerificarFull(_Sitio):
    def _correr(self, modo: bool, respuesta):
        from services import fulfillment_full as ff
        mock.patch.object(ff, "_token_ml", return_value="tok").start()
        with _modo(modo), mock.patch("httpx.get", return_value=_resp(respuesta)) as get:
            return ff.verificar_ml("BEKURA", IDS), get.call_args

    def test_bulk_da_lo_mismo_que_el_legado(self):
        campos = "id,status,sub_status,available_quantity,shipping,title,category_id,price,thumbnail"
        viejo, llamada_v = self._correr(False, legado(campos))
        nuevo, llamada_n = self._correr(True, bulk())
        self.assertEqual(nuevo, viejo)
        self.assertEqual(list(nuevo), [PROPIA])          # los 403/404: «sin verificar»
        self.assertEqual(nuevo[PROPIA]["stock"], 7)
        self.assertEqual(nuevo[PROPIA]["logistica"], "xd_drop_off")
        self.assertTrue(nuevo[PROPIA]["imagen"].startswith("https://"))
        self.assertTrue(llamada_n.args[0].endswith("/items/bulk"))
        self.assertNotIn("attributes", llamada_n.kwargs["params"])
        self.assertTrue(llamada_v.args[0].endswith("/items"))

    def test_401_renueva_y_reintenta_en_bulk(self):
        from services import fulfillment_full as ff
        campos = "id,status,sub_status,available_quantity,shipping,title,category_id,price,thumbnail"
        viejo, _ = self._correr(False, legado(campos))
        llamadas: list = []
        token = mock.patch.object(
            ff, "_token_ml", side_effect=lambda c, renovar=False: NUEVO if renovar else VIEJO).start()
        with _modo(True), mock.patch("httpx.get", side_effect=_get_por_token(bulk(), llamadas)):
            nuevo = ff.verificar_ml("BEKURA", IDS)
        self.assertEqual(nuevo, viejo)
        token.assert_called_with("BEKURA", renovar=True)
        self._reintento_en_bulk(llamadas)


class SitioInventarioFull(_Sitio):
    def _correr(self, modo: bool, respuesta, token: str = "tok"):
        from services import fulfillment_ml_inventario as fmi
        fmi._cache.clear()
        self.addCleanup(fmi._cache.clear)
        multiget: list = []

        def falso(ruta, tk, params=None):
            if ruta.startswith("/inventories/"):
                return 200, {"total": 5, "available_quantity": 3, "not_available_quantity": 2,
                             "not_available_detail": [{"status": "withdrawal", "quantity": 2}]}
            multiget.append((ruta, dict(params or {}), f"Bearer {tk}"))
            return (401, None) if tk == VIEJO else (200, copy.deepcopy(respuesta))
        self.token = mock.patch.object(
            fmi, "_token", side_effect=lambda c, renovar=False: NUEVO if renovar else token).start()
        with _modo(modo), mock.patch.object(fmi, "_get", side_effect=falso):
            return fmi.inventario("BEKURA", IDS), multiget

    def test_bulk_da_lo_mismo_que_el_legado(self):
        viejo, llamadas_v = self._correr(False, legado("id,inventory_id"))
        nuevo, llamadas_n = self._correr(True, bulk())
        self.assertEqual(nuevo, viejo)
        self.assertEqual(nuevo[PROPIA]["inventario"], "INV00001")
        self.assertEqual(nuevo[PROPIA]["detalle"], {"withdrawal": 2})
        self.assertIsNone(nuevo[OTRA_1])                 # «sin dato», como antes
        self.assertEqual(llamadas_n[0][:2], ("/items/bulk", {"ids": ",".join(IDS)}))
        self.assertEqual(llamadas_v[0][0], "/items")

    def test_401_renueva_y_reintenta_en_bulk(self):
        viejo, _ = self._correr(False, legado("id,inventory_id"))
        nuevo, llamadas = self._correr(True, bulk(), token=VIEJO)
        self.assertEqual(nuevo, viejo)
        self.token.assert_called_with("BEKURA", renovar=True)
        self._reintento_en_bulk(llamadas)


# ── Los scripts manuales ─────────────────────────────────────────────────────

def _importar_script(nombre: str):
    """Importa un script SIN sus efectos globales: dos apagan el logging de
    WARNING para todo el proceso y uno reconfigura `sys.stdout`."""
    import importlib
    previo = logging.root.manager.disable
    salida = sys.stdout
    try:
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        return importlib.import_module(f"scripts.{nombre}")
    finally:
        sys.stdout = salida
        logging.disable(previo)


class SitioAlinearDrop(_Sitio):
    def _correr(self, modo: bool, respuesta):
        ad = _importar_script("alinear_ml_drop")
        from services import meli
        mock.patch.object(meli, "_access_token", return_value="tok").start()
        # El caché (congelado desde el 13-ago) dice «active» para TODAS.
        filas = [{"sku": f"SKU-{n}", "cuenta": "BEKURA", "item_id": iid,
                  "stock_real": 3, "situacion": "active"} for n, iid in enumerate(IDS)]
        with _modo(modo), mock.patch("httpx.get", return_value=_resp(respuesta)) as get, \
             mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            return ad._refrescar_en_vivo(filas), get.call_args

    def test_los_403_y_404_siguen_fuera_aunque_el_cache_diga_active(self):
        viejo, llamada_v = self._correr(False, legado("id,available_quantity,status"))
        nuevo, llamada_n = self._correr(True, bulk())
        self.assertEqual(nuevo, viejo)
        self.assertEqual([(f["item_id"], f["stock_real"], f["_vivo"]) for f in nuevo],
                         [(PROPIA, 7, True)])
        self.assertTrue(llamada_n.args[0].endswith("/items/bulk"))
        self.assertTrue(llamada_v.args[0].endswith("/items"))


class SitioHuerfanas(_Sitio):
    def _correr(self, modo: bool, respuesta):
        hm = _importar_script("sincronizar_ml_huerfanas")
        from services import db, meli, wp_db
        mock.patch.object(meli, "_access_token",
                          side_effect=lambda c: "tok" if c == "BEKURA" else None).start()
        mock.patch.object(hm, "_ids_en_ml", return_value=list(IDS)).start()
        mock.patch.object(db, "fetch_all", return_value=[]).start()
        mock.patch.object(wp_db, "_prefix", return_value="wp_").start()
        mock.patch.object(wp_db, "_fetch_all",
                          return_value=[{"sku": "SKU-PRUEBA-01", "s": "2"}]).start()
        with _modo(modo), mock.patch("httpx.get", return_value=_resp(respuesta)) as get, \
             mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            return hm.huerfanas(), get.call_args

    def test_bulk_da_lo_mismo_que_el_legado(self):
        viejo, _ = self._correr(False, legado("id,status,available_quantity,attributes,shipping"))
        nuevo, llamada = self._correr(True, bulk())
        self.assertEqual(nuevo, viejo)
        self.assertEqual(nuevo, [{"cuenta": "BEKURA", "item_id": PROPIA, "sku": "SKU-PRUEBA-01",
                                  "situacion": "active", "actual": 7,
                                  "logistica": "xd_drop_off", "objetivo": 2}])
        self.assertTrue(llamada.args[0].endswith("/items/bulk"))


class _Cursor:
    def __init__(self, filas):
        self.filas = filas

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **kw):
        pass

    def fetchall(self):
        return self.filas


class _Conexion:
    def __init__(self, filas):
        self.filas = filas

    def cursor(self, **kw):
        return _Cursor(self.filas)

    def commit(self):
        raise AssertionError("el dry-run no escribe")

    def close(self):
        pass


def _cliente_sync(respuesta, llamadas: list):
    class Cliente:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, ruta, headers=None, params=None):
            llamadas.append((ruta, dict(params or {})))
            return _resp(respuesta)
    return Cliente


class SitioBackfillFecha(_Sitio):
    def _correr(self, modo: bool, respuesta):
        bf = _importar_script("backfill_fecha_publicacion_ml")
        from services import meli
        huecos = [{"sku": f"SKU-{n}", "account_id": "cuenta-1", "listing_id": iid,
                   "cuenta": "BEKURA"} for n, iid in enumerate(IDS)]
        llamadas: list = []
        mock.patch.object(meli, "_access_token", return_value="tok").start()
        mock.patch.object(bf, "cargar", return_value={
            "SUPABASE_DB_URL": "postgresql://postgres.abcdefgh:x@localhost:5432/db"}).start()
        mock.patch.object(bf.psycopg2, "connect", return_value=_Conexion(huecos)).start()
        mock.patch.object(sys, "argv", ["backfill"]).start()        # dry-run
        texto = io.StringIO()
        with _modo(modo), mock.patch("httpx.Client", _cliente_sync(respuesta, llamadas)), \
             mock.patch("time.sleep"), contextlib.redirect_stdout(texto):
            bf.main()
        return texto.getvalue(), llamadas

    def test_bulk_da_lo_mismo_que_el_legado(self):
        viejo, llamadas_v = self._correr(False, legado("id,date_created"))
        nuevo, llamadas_n = self._correr(True, bulk())
        self.assertEqual(nuevo, viejo)
        self.assertIn("resueltos: 1   sin resolver: 4", nuevo)
        self.assertIn(ITEM["date_created"], nuevo)
        self.assertEqual(llamadas_n[0][0], "/items/bulk")
        self.assertEqual(llamadas_v[0], ("/items", {"ids": ",".join(IDS),
                                                    "attributes": "id,date_created"}))


class SitioReporteSync(_Sitio):
    def _correr(self, modo: bool, respuesta):
        rs = _importar_script("reporte_sync_desde_ml")
        llamadas: list = []
        mock.patch.object(rs.meli, "_access_token",
                          side_effect=lambda c: "tok" if c == "BEKURA" else None).start()
        mock.patch.object(rs.db, "fetch_all", return_value=[]).start()
        mock.patch.object(rs.inventario, "_universo_ml",
                          mock.AsyncMock(return_value=list(IDS))).start()
        texto = io.StringIO()
        with _modo(modo), mock.patch("httpx.AsyncClient", _cliente_async(respuesta, llamadas)), \
             contextlib.redirect_stdout(texto):
            asyncio.run(rs.main())
        return texto.getvalue(), llamadas

    def test_bulk_da_lo_mismo_que_el_legado(self):
        orden = [OTRA_1, PROPIA, BORRADA, NO_EXISTE, OTRA_2]   # el listado sigue el orden
        viejo, llamadas_v = self._correr(False, legado(orden=orden))
        nuevo, llamadas_n = self._correr(True, bulk(orden))
        self.assertEqual(nuevo, viejo)
        self.assertIn(f"+ {PROPIA}  SKU-PRUEBA-01", nuevo)
        # Los 403/404 se siguen listando como «sin SKU legible», igual que antes.
        self.assertIn(f"sin SKU legible (no se escribirian): {OTRA_1}, {BORRADA}", nuevo)
        self.assertEqual(llamadas_n, [("/items/bulk", {"ids": ",".join(IDS)}, "Bearer tok")])
        self.assertEqual(llamadas_v, [("/items", {"ids": ",".join(IDS)}, "Bearer tok")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
