"""La salud de publicaciones (`services/calidad_ml.py`) contra el SANDBOX: el
SQL de verdad de la migración 0053 (`enrich.listing_health` y
`enrich.listing_health_hist`).

`test_calidad_ml.py` fija la FORMA de la sentencia con parches; aquí se prueba
lo que solo Postgres puede decir:

  · la historia guarda UNA fila por (publicación, métrica, día de México) y una
    segunda medición del mismo día la REEMPLAZA;
  · huella igual a la del día guardado anterior → `pendientes`/`detalle` NULL;
    si cambia, se guardan (y una segunda medición del día del cambio NO los
    tira: compara contra ayer, no contra sí misma);
  · una vuelta sin experiencia no toca la experiencia ni escribe su historia;
  · la llave es multicanal: el mismo listing_id en otra cuenta u otro canal es
    otra fila;
  · sin_datos se guarda sin número (NUNCA 0), y lo que se escribe se lee de
    vuelta con las formas de siempre.

APAGADA POR DEFECTO: la suite corre sin red. Se enciende con
`OMNI_PRUEBAS_SANDBOX=1` y toma `SUPABASE_DB_URL` de `env.staging` (raíz del
repo). Se NIEGA a correr si ese DSN no es el sandbox (yvootpbz) o si es la BD
kubera de producción (tukwcvsi / SUPABASE_PROD_REF).

Nada queda escrito: cada prueba corre en UNA transacción (puerto 5432, la
conexión es nuestra) que termina en ROLLBACK, con listing_ids de mentira
(`MLMPRUEBA…`). La sesión NUNCA se marca read-only (regla 13).

    cd backend && OMNI_PRUEBAS_SANDBOX=1 python -m unittest tests.test_calidad_ml_sandbox -v
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import calidad_ml  # noqa: E402

_ENCENDIDA = os.environ.get("OMNI_PRUEBAS_SANDBOX") == "1"
_REF_SANDBOX = "yvootpbz"
_REF_PROD = "tukwcvsi"
_HOY = "(now() at time zone 'America/Mexico_City')::date"


def _dsn_sandbox() -> str:
    """El DSN del sandbox, en modo sesión (5432). Aborta ante cualquier duda."""
    valores: dict[str, str] = {}
    ruta = BACKEND.parent / "env.staging"
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            valores[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    url = valores.get("SUPABASE_DB_URL", "")
    m = re.search(r"postgres\.([a-z0-9]+):", url)
    ref = m.group(1) if m else ""
    prod = valores.get("SUPABASE_PROD_REF", "").strip()
    if (not ref.startswith(_REF_SANDBOX) or _REF_PROD in url
            or (prod and ref == prod)):
        raise RuntimeError("SUPABASE_DB_URL de env.staging no es el sandbox: "
                           "no se corre nada")
    return re.sub(r"(pooler\.supabase\.com):6543\b", r"\1:5432", url)


# ── Crudos de ML recortados (misma forma que los reales del 22-sep) ─────────

def _crudo_calidad(score, pendientes_item=("UP_FREE_SHIPPING",)):
    def var(key, status):
        return {"key": key, "status": status, "score": 0, "title": key,
                "rules": [{"key": key, "status": status, "wordings": {
                    "title": f"Titulo {key}", "label": f"Accion {key}",
                    "link": f"https://www.mercadolibre.com.mx/{key}"}}]}
    return {
        "entity_type": "USER_PRODUCT", "entity_id": "MLMUPRUEBA1", "score": score,
        "level": "medium", "level_wording": "Estándar",
        "calculated_at": "2026-09-22T01:40:00.109Z",
        "buckets": [
            {"key": "USER_PRODUCT", "type": "USER_PRODUCT", "status": "PENDING",
             "score": 89.4, "title": "Datos del producto",
             "variables": [var("UP_SHORTS", "PENDING"), var("UP_PICS", "COMPLETED")]},
            {"key": "ITEM", "type": "ITEM", "status": "PENDING", "score": 18,
             "title": "Condiciones de venta",
             "variables": [var(k, "PENDING") for k in pendientes_item]},
        ],
    }


_EXP_ROJA = {"reputation": {"color": "red", "text": "Mala", "value": 30},
             "consequence": {"title": {"text": "Tienes muy baja exposición."}},
             "principal_actionable": {"text": "Actualiza la publicación."},
             "reasoning": {"subtitles": [{"order": 0, "text": "En la misma categoría."}]},
             "recommendations": {"subtitles": [{"order": 0, "text": "Añade fotos."}]},
             "ai_generated": {"text": "Generado por IA"},
             "status": {"id": "active"}}
_EXP_GRIS = {"reputation": {"color": "gray", "value": -1},
             "status": {"id": "active"}}


def _fila(item_id, cuenta="BEKURA", score=60, exp=_EXP_ROJA, **kw):
    f = calidad_ml._fila_medida(item_id, cuenta,
                                calidad_ml.normalizar(_crudo_calidad(score, **kw)))
    if exp is not None:
        f.update(calidad_ml.normalizar_experiencia(exp))
    return f


@unittest.skipUnless(_ENCENDIDA, "prueba contra el sandbox: OMNI_PRUEBAS_SANDBOX=1")
class SaludEnSandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        from psycopg2.extras import RealDictCursor

        cls.cn = psycopg2.connect(_dsn_sandbox(), connect_timeout=20,
                                  cursor_factory=RealDictCursor)
        cls.cn.autocommit = False
        cur = cls.cn.cursor()
        cur.execute("select to_regclass('enrich.listing_health') as a, "
                    "to_regclass('enrich.listing_health_hist') as h")
        r = cur.fetchone()
        if not (r["a"] and r["h"]):
            cls.cn.rollback()
            cls.cn.close()
            raise unittest.SkipTest("el sandbox no tiene la 0053 aplicada")
        cur.execute("select channel_id, legacy_code, id::text as id from core.accounts "
                    "where legacy_code in ('BEKURA', 'SANCORFASHION', 'AMAZON')")
        cls.ids = {(f["channel_id"], f["legacy_code"]): f["id"] for f in cur.fetchall()}
        cls.cn.rollback()
        cls.cuentas = {"BEKURA": cls.ids[("mercado_libre", "BEKURA")],
                       "SANCORFASHION": cls.ids[("mercado_libre", "SANCORFASHION")]}

    @classmethod
    def tearDownClass(cls):
        cls.cn.close()

    def setUp(self):
        self.cur = self.cn.cursor()
        self.iid = f"MLMPRUEBA{uuid.uuid4().hex[:12].upper()}"

    def tearDown(self):
        # NADA queda escrito en el sandbox.
        self.cn.rollback()

    # ── ayudas ──────────────────────────────────────────────────────────────
    def _guardar(self, filas):
        valores, n = calidad_ml._valores_salud(calidad_ml._unicas(filas), self.cuentas)
        calidad_ml._escribir_lote(self.cur, valores)
        return n

    def _hist(self, iid=None, metrica="calidad"):
        self.cur.execute(
            f"""select dia, dia = {_HOY} as es_hoy, estado, valor, nivel, huella,
                       pendientes, detalle, capturado_en
                  from enrich.listing_health_hist
                 where listing_id = %s and metrica = %s order by dia""",
            (iid or self.iid, metrica))
        return self.cur.fetchall()

    def _actual(self, iid=None, metrica="calidad", canal="mercado_libre"):
        self.cur.execute(
            """select account_id::text as account_id, estado, valor, nivel, nivel_canal,
                      n_pendientes, pendientes, detalle, capturado_en,
                      md5(pendientes::text || detalle::text) as huella
                 from enrich.listing_health
                where canal = %s and listing_id = %s and metrica = %s
                order by account_id""", (canal, iid or self.iid, metrica))
        return self.cur.fetchall()

    def _a_ayer(self):
        """La historia de la publicación, un día atrás (como si fuera de ayer)."""
        self.cur.execute("update enrich.listing_health_hist set dia = dia - 1 "
                         "where listing_id = %s", (self.iid,))

    # ── pruebas ─────────────────────────────────────────────────────────────
    def test_primera_medicion_actual_e_historia_con_jsonb(self):
        self.assertEqual(self._guardar([_fila(self.iid)]), 1)
        (cal,) = self._actual()
        self.assertEqual((cal["estado"], cal["valor"], cal["nivel"], cal["nivel_canal"],
                          cal["n_pendientes"]),
                         ("medida", Decimal("60.00"), "medio", "Estándar", 2))
        self.assertEqual([p["grupo"] for p in cal["pendientes"]], ["USER_PRODUCT", "ITEM"])
        (h,) = self._hist()
        self.assertTrue(h["es_hoy"])
        # Primer día de la serie: el jsonb va, y la huella es la de Postgres
        # sobre lo que quedó en la tabla actual.
        self.assertEqual(h["huella"], cal["huella"])
        self.assertEqual((h["pendientes"], h["detalle"]), (cal["pendientes"], cal["detalle"]))
        self.assertEqual(h["capturado_en"], cal["capturado_en"])
        (e,) = self._hist(metrica="experiencia")
        self.assertIsNotNone(e["detalle"])
        self.assertEqual((e["estado"], e["valor"], e["nivel"]),
                         ("medida", Decimal("30.00"), "malo"))

    def test_segunda_medicion_del_mismo_dia_reemplaza(self):
        self._guardar([_fila(self.iid, score=60)])
        self._guardar([_fila(self.iid, score=75,
                             pendientes_item=("UP_PRICE", "UP_PROMOTIONS"))])
        hist = self._hist()
        self.assertEqual(len(hist), 1, "una fila por día")
        (h,) = hist
        self.assertEqual(h["valor"], Decimal("75.00"))
        (cal,) = self._actual()
        self.assertEqual(h["huella"], cal["huella"])
        self.assertEqual([p["clave"] for p in h["pendientes"]],
                         ["UP_SHORTS", "UP_PRICE", "UP_PROMOTIONS"])
        self.assertEqual(len(self._hist(metrica="experiencia")), 1)

    def test_huella_igual_a_la_de_ayer_guarda_null(self):
        self._guardar([_fila(self.iid)])
        self._a_ayer()
        self._guardar([_fila(self.iid)])            # nada cambió
        ayer, hoy = self._hist()
        self.assertFalse(ayer["es_hoy"])
        self.assertTrue(hoy["es_hoy"])
        self.assertEqual(hoy["huella"], ayer["huella"])
        self.assertIsNone(hoy["pendientes"])
        self.assertIsNone(hoy["detalle"])
        # Los escalares van siempre.
        self.assertEqual((hoy["estado"], hoy["valor"]), ("medida", Decimal("60.00")))
        e_ayer, e_hoy = self._hist(metrica="experiencia")
        self.assertIsNone(e_hoy["detalle"])
        self.assertIsNotNone(e_ayer["detalle"])

    def test_huella_que_cambia_se_guarda_aunque_se_mida_dos_veces(self):
        self._guardar([_fila(self.iid)])
        self._a_ayer()
        cambio = _fila(self.iid, pendientes_item=("UP_PRICE",))
        self._guardar([cambio])
        _ayer, hoy = self._hist()
        self.assertNotEqual(hoy["huella"], _ayer["huella"])
        self.assertEqual([p["clave"] for p in hoy["pendientes"]], ["UP_SHORTS", "UP_PRICE"])
        # Segunda medición del día del cambio, idéntica: compara contra AYER
        # (no contra sí misma), así que el jsonb del día del cambio se queda.
        self._guardar([cambio])
        _ayer, hoy = self._hist()
        self.assertIsNotNone(hoy["pendientes"])
        self.assertIsNotNone(hoy["detalle"])
        # Y si en el mismo día vuelve a ser como ayer: NULL (se reconstruye
        # con el último jsonb no NULL, que es el de ayer).
        self._guardar([_fila(self.iid)])
        _ayer, hoy = self._hist()
        self.assertEqual(hoy["huella"], _ayer["huella"])
        self.assertIsNone(hoy["pendientes"])

    def test_sin_experiencia_no_la_toca_ni_escribe_su_historia(self):
        self._guardar([_fila(self.iid)])
        self._a_ayer()
        self.cur.execute("update enrich.listing_health set capturado_en = capturado_en "
                         "- interval '1 day' where listing_id = %s and metrica = "
                         "'experiencia'", (self.iid,))
        (antes,) = self._actual(metrica="experiencia")
        self._guardar([_fila(self.iid, score=80, exp=None)])   # la experiencia falló
        (despues,) = self._actual(metrica="experiencia")
        self.assertEqual(despues, antes, "la experiencia de antes se conserva intacta")
        self.assertEqual([h["es_hoy"] for h in self._hist(metrica="experiencia")], [False])
        # La calidad sí se escribió, con su día.
        self.assertEqual([h["es_hoy"] for h in self._hist()], [False, True])
        self.assertEqual(self._actual()[0]["valor"], Decimal("80.00"))

    def test_llave_multicanal(self):
        # El mismo listing_id en Amazon (fila puesta a mano) y en las DOS
        # cuentas de ML: tres filas de calidad que no se pisan.
        self.cur.execute(
            """insert into enrich.listing_health
                 (canal, account_id, listing_id, metrica, estado, valor, capturado_en)
               values ('amazon', %s, %s, 'calidad', 'medida', 42, now() - interval '2 days')""",
            (self.ids[("amazon", "AMAZON")], self.iid))
        self.assertEqual(self._guardar([_fila(self.iid, cuenta="BEKURA", score=50),
                                        _fila(self.iid, cuenta="SANCORFASHION", score=90)]),
                         2)
        ml = self._actual()
        self.assertEqual({(f["account_id"], f["valor"]) for f in ml},
                         {(self.cuentas["BEKURA"], Decimal("50.00")),
                          (self.cuentas["SANCORFASHION"], Decimal("90.00"))})
        (amz,) = self._actual(canal="amazon")
        self.assertEqual(amz["valor"], Decimal("42.00"))
        self.cur.execute("select count(*) as n from enrich.listing_health_hist "
                         "where listing_id = %s and metrica = 'calidad'", (self.iid,))
        self.assertEqual(self.cur.fetchone()["n"], 2)       # Amazon no es de este escritor

    def test_sin_datos_nunca_0(self):
        self._guardar([_fila(self.iid, exp=_EXP_GRIS)])
        (e,) = self._actual(metrica="experiencia")
        self.assertEqual((e["estado"], e["valor"], e["nivel"], e["n_pendientes"]),
                         ("sin_datos", None, None, None))
        self.assertEqual(set(e["detalle"]), set(calidad_ml._DETALLE_EXP))
        (h,) = self._hist(metrica="experiencia")
        self.assertEqual((h["estado"], h["valor"]), ("sin_datos", None))

    def test_se_lee_de_vuelta_con_las_formas_de_siempre(self):
        otra = f"{self.iid}B"
        self._guardar([_fila(self.iid), _fila(otra, exp=_EXP_GRIS),
                       calidad_ml._fila_no_calculada(f"{self.iid}C", "BEKURA",
                                                     "Entity not calculated: x")])

        def fetch_all(sql, params=None):
            self.cur.execute(sql, params)
            return [dict(r) for r in self.cur.fetchall()]
        with mock.patch.object(calidad_ml.sdb, "disponible", return_value=True), \
                mock.patch.object(calidad_ml.sdb, "fetch_all", side_effect=fetch_all):
            res = calidad_ml.resumen_por_items([self.iid, otra, f"{self.iid}C"])
            det = calidad_ml.detalle_por_items([self.iid])
        c = res[self.iid]["calidad"]
        self.assertEqual((c["estado"], c["score"], c["level"], c["level_wording"],
                          c["n_pendientes"]), ("medida", 60, "medium", "Estándar", 2))
        self.assertIs(type(c["score"]), int)
        self.assertEqual([p["accion"] for p in c["pendientes_top"]],
                         ["Accion UP_FREE_SHIPPING", "Accion UP_SHORTS"])
        e = res[self.iid]["experiencia"]
        self.assertEqual((e["estado"], e["valor"], e["color"], e["texto"],
                          e["por_categoria"]), ("medida", 30, "red", "Mala", True))
        self.assertEqual((res[otra]["experiencia"]["estado"],
                          res[otra]["experiencia"]["valor"]), ("sin_datos", None))
        nc = res[f"{self.iid}C"]
        self.assertEqual((nc["calidad"]["estado"], nc["calidad"]["n_pendientes"],
                          nc["experiencia"]["estado"]), ("no_calculada", 0, "sin_medir"))
        d = det[self.iid]
        self.assertEqual([b["clave"] for b in d["calidad"]["buckets"]], ["USER_PRODUCT", "ITEM"])
        self.assertEqual([[p["clave"] for p in b["pendientes"]]
                          for b in d["calidad"]["buckets"]],
                         [["UP_SHORTS"], ["UP_FREE_SHIPPING"]])
        self.assertEqual(d["experiencia"]["razon"], ["En la misma categoría."])
        self.assertEqual(json.loads(json.dumps(d)), d)        # serializable tal cual


if __name__ == "__main__":
    unittest.main()
