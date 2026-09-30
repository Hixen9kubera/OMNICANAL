"""Pruebas del sync de ML que alimenta channel.listings (v0.597.0).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
Tres defectos medidos en producción el 22-sep-2026, cada uno detrás de su flag
(todos nacen APAGADOS; apagado = comportamiento idéntico al de antes):

  1. SYNC_ML_ROTACION_RELOJ — el lote "lo más rancio primero" se queda clavado en
     las mismas 80 publicaciones, porque releer algo que no cambió no mueve
     `updated_at`. La tajada por reloj recorre el universo entero, una vez por
     vuelta, y lo nunca visto tiene turno preferente UNA vez por proceso.
  2. CHANNEL_DUENO_ESTABLE — dos items que declaran el mismo SKU se turnan la
     fila en cada ronda. La condición del upsert solo deja cambiar de item a
     uno en estado estrictamente mejor.
  3. CHANNEL_GEMELAS_SITUACION — dos SKUs que reclaman el mismo item: la fila
     que el sync no escribe se queda `active` para siempre. Se le copia la
     situación (solo la situación) después de escribir la otra.

Lo que depende de Postgres (el CASE del rango, el trigger de historia) se
prueba contra el sandbox en `scripts/probar_ml_sync_sandbox.py`.

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from services import channel_mirror as cm  # noqa: E402
from services import inventario as inv  # noqa: E402

CUENTA_UUID = "00000000-0000-0000-0000-00000000b0ca"


def _ids(n: int) -> list[str]:
    return [f"MLM{1000 + k}" for k in range(n)]


class _CursorFalso:
    def __init__(self) -> None:
        self.llamadas: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.llamadas.append((" ".join(str(sql).split()), params))


def _fila(**extra):
    base = {"sku": "TEC-0409-NEG", "canal": "mercado_libre", "cuenta": "BEKURA",
            "item_id": "MLM2670283481", "precio": 100.0, "precio_base": 100.0,
            "precio_venta": None, "stock_real": 0, "stock_full": 0,
            "stock_fba": None, "es_full": 1, "logistica": "fulfillment",
            "situacion": "paused", "moneda": "MXN", "fecha_publicacion": None}
    base.update(extra)
    return base


# ── 1. Rotación por reloj ────────────────────────────────────────────────────

class TajadaPorReloj(unittest.TestCase):
    def setUp(self):
        inv._intentados.clear()
        self.periodo = settings.sync_interval_min * 60

    def test_una_vuelta_recorre_todo_el_universo(self):
        ids = _ids(250)
        vistos = {i: datetime(2026, 9, 1) for i in ids}
        leidos: set[str] = set()
        rondas = -(-len(ids) // 80)                       # ⌈250/80⌉ = 4
        for r in range(rondas):
            leidos.update(inv._tajada_por_reloj("BEKURA", ids, vistos, 80,
                                                ahora=(1000 + r) * self.periodo))
        self.assertEqual(leidos, set(ids))

    def test_rondas_seguidas_son_contiguas_y_sin_repetir(self):
        ids = _ids(250)
        vistos = {i: datetime(2026, 9, 1) for i in ids}
        a = inv._tajada_por_reloj("BEKURA", ids, vistos, 80, ahora=1000 * self.periodo)
        b = inv._tajada_por_reloj("BEKURA", ids, vistos, 80, ahora=1001 * self.periodo)
        self.assertEqual(len(a), 80)
        self.assertFalse(set(a) & set(b))

    def test_misma_hora_misma_tajada_aunque_reinicie(self):
        ids = _ids(250)
        vistos = {i: datetime(2026, 9, 1) for i in ids}
        a = inv._tajada_por_reloj("BEKURA", ids, vistos, 80, ahora=1234 * self.periodo)
        inv._intentados.clear()                           # "reinicio"
        b = inv._tajada_por_reloj("BEKURA", list(reversed(ids)), vistos, 80,
                                  ahora=1234 * self.periodo + 60)
        self.assertEqual(a, b)                            # no depende del orden de ML

    def test_universo_menor_que_el_lote_sin_repetidos(self):
        ids = _ids(30)
        vistos = {i: datetime(2026, 9, 1) for i in ids}
        t = inv._tajada_por_reloj("BEKURA", ids, vistos, 80, ahora=7 * self.periodo)
        self.assertEqual(sorted(t), sorted(ids))

    def test_nunca_visto_tiene_turno_una_sola_vez(self):
        ids = _ids(250)
        hermanos = ids[200:205]                           # nunca tendrán fila
        vistos = {i: datetime(2026, 9, 1) for i in ids if i not in hermanos}
        r0 = inv._tajada_por_reloj("BEKURA", ids, vistos, 80, ahora=1000 * self.periodo)
        self.assertTrue(set(hermanos) <= set(r0))        # 1ª ronda: al frente
        r1 = inv._tajada_por_reloj("BEKURA", ids, vistos, 80, ahora=1001 * self.periodo)
        # 2ª ronda: ya no hay preferencia; la ronda es EXACTAMENTE su tajada
        u, inicio = sorted(ids), (1001 * 80) % 250
        self.assertEqual(r1, [u[(inicio + k) % 250] for k in range(80)])

    def test_nunca_vistos_acotados_por_ronda(self):
        ids = _ids(300)
        t = inv._tajada_por_reloj("BEKURA", ids, {}, 80, ahora=5 * self.periodo)
        self.assertEqual(len(t), 80 + inv._NUEVOS_POR_RONDA)
        self.assertEqual(len(set(t)), len(t))

    def test_cuentas_independientes(self):
        ids = _ids(100)
        inv._tajada_por_reloj("BEKURA", ids, {}, 10, ahora=5 * self.periodo)
        t = inv._tajada_por_reloj("SANCORFASHION", ids, {}, 10, ahora=5 * self.periodo)
        self.assertEqual(len(t), 10 + inv._NUEVOS_POR_RONDA)

    def test_universo_vacio(self):
        self.assertEqual(inv._tajada_por_reloj("BEKURA", [], {}, 80), [])


class LoteDesdeMl(unittest.TestCase):
    """El defecto de hoy, reproducido: con `vistos` que no cambia (releer una
    pausada que no cambió no mueve `updated_at`), el lote de siempre es el
    MISMO en cada ronda; el de reloj avanza."""

    def setUp(self):
        inv._intentados.clear()
        self.ids = _ids(400)
        # 60 activas recién tocadas (precios_venta) y 340 pausadas rancias
        self.vistos = {i: (datetime(2026, 9, 22) if k < 60 else datetime(2026, 8, 24))
                       for k, i in enumerate(self.ids)}
        self.parches = [
            mock.patch.object(inv, "_universo_ml", mock.AsyncMock(return_value=self.ids)),
            mock.patch("services.channel_read.vistos_ml", return_value=self.vistos),
            mock.patch("services.channel_read.vivas_ml", return_value=[]),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()

    def _lote(self, ahora: float) -> list[str]:
        with mock.patch("time.time", return_value=ahora):
            lote = asyncio.run(inv._lote_desde_ml(None, "BEKURA", "tok", 80))
        return [x["ml_item_id"] for x in lote]

    def test_apagado_el_lote_se_queda_clavado(self):
        p = settings.sync_interval_min * 60
        with mock.patch.object(settings, "sync_ml_rotacion_reloj", False):
            a, b = self._lote(1000 * p), self._lote(1001 * p)
        self.assertEqual(a, b)
        self.assertFalse(set(a) & set(self.ids[:60]))     # ninguna activa entra

    def test_encendido_recorre_activas_y_pausadas(self):
        p = settings.sync_interval_min * 60
        leidos: set[str] = set()
        with mock.patch.object(settings, "sync_ml_rotacion_reloj", True):
            for r in range(5):                            # ⌈400/80⌉
                leidos.update(self._lote((1000 + r) * p))
        self.assertEqual(leidos, set(self.ids))


# ── 2. Dueño estable ─────────────────────────────────────────────────────────

class CondicionDuenoEstable(unittest.TestCase):
    def test_apagado_no_agrega_nada(self):
        with mock.patch.object(settings, "channel_dueno_estable", False):
            self.assertEqual(cm._condicion_dueno_estable("mercado_libre"), "")

    def test_solo_mercado_libre(self):
        with mock.patch.object(settings, "channel_dueno_estable", True):
            self.assertEqual(cm._condicion_dueno_estable("amazon"), "")
            self.assertEqual(cm._condicion_dueno_estable("tiktok"), "")

    def test_encendido_exige_mismo_item_o_mejor_estado(self):
        with mock.patch.object(settings, "channel_dueno_estable", True):
            c = " ".join(cm._condicion_dueno_estable("mercado_libre").split())
        self.assertTrue(c.startswith("and ("))
        self.assertIn("excluded.listing_id = listings.listing_id", c)
        self.assertIn("nullif(excluded.listing_id, '') is null", c)
        self.assertIn("nullif(listings.listing_id, '') is null", c)
        # estrictamente mayor: en empate se queda el que ya estaba
        self.assertIn("end) > (case lower(coalesce(listings.situacion", c)

    def test_reclamo_compartido_suelta_en_empate_pero_nunca_a_peor(self):
        """v0.598.0: si OTRA fila reclama el mismo item, el empate deja cambiar;
        un item peor, no. (El caso real se prueba contra Postgres en el
        sandbox: CAM-0030-IND con el item de MAT.)"""
        with mock.patch.object(settings, "channel_dueno_estable", True):
            c = " ".join(cm._condicion_dueno_estable("mercado_libre").split())
        self.assertIn("end) >= (case lower(coalesce(listings.situacion", c)
        self.assertIn("exists (select 1 from channel.listings o", c)
        self.assertIn("o.listing_id = listings.listing_id and o.sku <> listings.sku", c)
        self.assertIn("o.account_id = listings.account_id", c)
        self.assertEqual(c.count("("), c.count(")"))

    def test_rango(self):
        r = cm._rango_situacion("x")
        for estado, rango in (("active", 3), ("paused", 2), ("under_review", 1)):
            self.assertIn(f"when '{estado}' then {rango}", r)
        self.assertIn("else 0 end", r)


# ── 3. Situación a las gemelas ───────────────────────────────────────────────

class SituacionAGemelas(unittest.TestCase):
    def _copiar(self, flag: bool, fila: dict, canal="mercado_libre"):
        cur = _CursorFalso()
        with mock.patch.object(settings, "channel_gemelas_situacion", flag):
            cm._copiar_situacion_a_gemelas(cur, canal, CUENTA_UUID, fila["sku"], fila)
        return cur.llamadas

    def test_apagado_no_escribe(self):
        self.assertEqual(self._copiar(False, _fila()), [])

    def test_copia_solo_la_situacion_a_las_otras_filas_del_item(self):
        (sql, params), = self._copiar(True, _fila(sku="TEC-0879-NEG-L",
                                                  item_id="MLM3030073339"))
        self.assertTrue(sql.startswith("update channel.listings set situacion = %s"))
        self.assertIn("listing_id = %s and sku <> %s", sql)
        self.assertIn("situacion is distinct from %s", sql)
        self.assertNotIn("stock", sql)
        self.assertEqual(params, ("paused", "mercado_libre", CUENTA_UUID,
                                  "MLM3030073339", "TEC-0879-NEG-L", "paused"))

    def test_no_en_amazon(self):
        self.assertEqual(self._copiar(True, _fila(canal="amazon"), canal="amazon"), [])

    def test_sin_situacion_o_sin_item_no_hay_que_copiar(self):
        self.assertEqual(self._copiar(True, _fila(situacion=None)), [])
        self.assertEqual(self._copiar(True, _fila(item_id="")), [])


class EscribirTanda(unittest.TestCase):
    def _escribir(self, gemelas: bool, dueno: bool, filas):
        cur = _CursorFalso()
        with mock.patch.object(cm, "_cuenta_uuid", return_value=CUENTA_UUID), \
             mock.patch.object(settings, "channel_gemelas_situacion", gemelas), \
             mock.patch.object(settings, "channel_dueno_estable", dueno):
            cm.escribir_tanda(cur, filas)
        return [sql for sql, _ in cur.llamadas]

    def test_apagados_el_sql_es_el_de_siempre(self):
        sqls = self._escribir(False, False, [_fila()])
        self.assertEqual(len(sqls), 2)                    # identidad + upsert
        self.assertTrue(sqls[1].endswith("coalesce(listings.date_published, excluded.date_published))"))

    def test_encendidos_upsert_con_condicion_y_luego_gemelas(self):
        sqls = self._escribir(True, True, [_fila()])
        self.assertEqual(len(sqls), 3)
        self.assertIn("insert into channel.listings", sqls[1])
        self.assertIn("excluded.listing_id = listings.listing_id", sqls[1])
        self.assertTrue(sqls[2].startswith("update channel.listings set situacion"))

    def test_amazon_no_cambia_aunque_esten_encendidos(self):
        fila = _fila(canal="amazon", cuenta="", item_id="B0TEST", situacion="BUYABLE")
        sqls = self._escribir(True, True, [fila])
        self.assertEqual(len(sqls), 2)
        self.assertNotIn("excluded.listing_id = listings.listing_id", sqls[1])


# ── 4. El aviso escribe en la fila dueña (v0.597.0) ──────────────────────────

class AvisoFilaDuena(unittest.TestCase):
    """DEPO-0001 (padre) y DEPO-0001-AZL (hijo) apuntan al mismo item y ML
    declara el hijo. `dueno_de_item_ml` es un `limit 1` sin orden: aquí devuelve
    el padre, que es el caso que dejaba atrasada a la dueña."""

    ITEM = {"id": "MLM3097440065", "seller_custom_field": "DEPO-0001-AZL",
            "status": "paused", "available_quantity": 0, "price": 259,
            "shipping": {"logistic_type": "fulfillment"}}

    def _refrescar(self, encendido: bool, item: dict, suyas: list[str]) -> str:
        escritas: list[dict] = []

        async def _upsert(rows):
            escritas.extend(rows)
            return len(rows)

        async def _leer(cli, item_id, token, cuenta):
            return item

        async def _token(cuenta):
            return "tok"

        with mock.patch.object(settings, "channel_gemelas_situacion", encendido), \
             mock.patch.object(settings, "supabase_read_publicaciones", True), \
             mock.patch.object(inv.channel_read, "dueno_de_item_ml",
                               return_value={"sku": "DEPO-0001", "cuenta": "BEKURA"}), \
             mock.patch.object(inv.channel_read, "skus_de_item_ml", return_value=suyas) as skus, \
             mock.patch.object(inv.meli, "access_token_async", _token), \
             mock.patch.object(inv, "_leer_ml_item", _leer), \
             mock.patch.object(inv, "_upsert_async", _upsert):
            r = asyncio.run(inv.refrescar_ml_item_id("MLM3097440065"))
        self.assertTrue(r["ok"])
        self.assertEqual(len(escritas), 1)
        self.skus_consultado = skus.called
        return escritas[0]["sku"]

    def test_apagado_escribe_donde_caiga_el_limit_1(self):
        self.assertEqual(self._refrescar(False, self.ITEM, ["DEPO-0001", "DEPO-0001-AZL"]),
                         "DEPO-0001")
        self.assertFalse(self.skus_consultado)

    def test_encendido_escribe_en_la_del_sku_que_declara_ml(self):
        self.assertEqual(self._refrescar(True, self.ITEM, ["DEPO-0001", "DEPO-0001-AZL"]),
                         "DEPO-0001-AZL")

    def test_no_se_muda_a_una_fila_que_no_apunta_a_este_item(self):
        # ML declara un SKU que existe en el panel pero para OTRA publicación:
        # eso es identidad, no se decide aquí.
        self.assertEqual(self._refrescar(True, self.ITEM, ["DEPO-0001"]), "DEPO-0001")

    def test_item_sin_sku_legible_se_queda_como_estaba(self):
        item = {**self.ITEM, "seller_custom_field": None}
        self.assertEqual(self._refrescar(True, item, ["DEPO-0001", "DEPO-0001-AZL"]),
                         "DEPO-0001")
        self.assertFalse(self.skus_consultado)

    def test_si_ya_toco_la_duena_no_consulta_nada(self):
        item = {**self.ITEM, "seller_custom_field": "depo-0001"}
        self.assertEqual(self._refrescar(True, item, ["DEPO-0001", "DEPO-0001-AZL"]),
                         "DEPO-0001")
        self.assertFalse(self.skus_consultado)


if __name__ == "__main__":
    unittest.main()
