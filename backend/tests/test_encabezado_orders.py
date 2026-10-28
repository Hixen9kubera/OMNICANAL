"""Pruebas del ENCABEZADO de ``channel.orders`` (PLAN MAESTRO 2.2 y 2.6, 28-oct-2026).

Desde v0.176.0 (14-ago) ``orders_write.reclamar`` deja un esqueleto (solo la llave y
``creado_at``) y el ``guardar`` del ganador no lo completaba: 33,002 encabezados sin
``skus``/``comision`` y 30,702 FULL marcados como no FULL, medido en producción.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **El conflicto completa el esqueleto UNA vez.** ``skus IS NULL`` es la marca:
   ``skus`` toma lo que llega, ``es_fulfillment`` también, y la comisión acepta un
   NULL como «nunca observado» (antes solo el 0).
2. **Una fila completa no cambia**: ``skus`` y ``es_fulfillment`` quedan como están.
3. **``creado_at`` y ``registrado_at`` no se reescriben**: el libro y la tanda de T0
   leen ``creado_at`` (la hora del reclamo) y ``registrado_at`` es solo del INSERT.
4. **Ningún código nombra ``registrado_at``**: vive de su default (0079), así que la
   migración y el código se despliegan en cualquier orden.
5. **La 0079** agrega la columna SIN default y luego pone el default (las filas viejas
   quedan en NULL, no con la hora de la migración), lleva su candado y su fila en
   ``ops.migraciones``, y el manifiesto la trae.

La conducta contra una base de verdad se ensaya en el sandbox (transacción con
ROLLBACK); aquí, sin red ni base, se fija el contrato del SQL.
    cd backend && python -m unittest tests.test_encabezado_orders -v
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import kubera_mirror, orders_write  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
M79 = RAIZ / "supabase" / "migrations" / "0079_channel_orders_registrado_at.sql"
MANIFIESTO = RAIZ / "supabase" / "schema_manifest.json"


class CursorQueAnota:
    def __init__(self) -> None:
        self.sql: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))


def _upsert_orders(payload: dict) -> tuple[str, tuple]:
    cur = CursorQueAnota()
    kubera_mirror._up_channel_orders(cur, payload)
    (sql, params), = cur.sql
    return sql, params


def _set_del_conflicto(sql: str) -> dict[str, str]:
    """{columna: expresión} del DO UPDATE SET."""
    cuerpo = sql.split("do update set", 1)[1]
    partes, nivel, actual = [], 0, ""
    for ch in cuerpo:
        nivel += ch == "("
        nivel -= ch == ")"
        if ch == "," and nivel == 0:
            partes.append(actual)
            actual = ""
        else:
            actual += ch
    partes.append(actual)
    return {p.split("=", 1)[0].strip(): p.split("=", 1)[1].strip() for p in partes}


PAYLOAD = {"external_order_id": 2000012345, "canal": "mercado_libre", "cuenta": "BEKURA",
           "wc_order_id": 170001, "estado_canal": "paid", "estado_wc": "processing",
           "total": 499.0, "comision": 87.33, "es_fulfillment": True,
           "skus": ["ACC-0001-AZL"], "creado_at": "2026-10-28T16:00:00+00:00"}


class ElConflictoCompletaElEsqueleto(unittest.TestCase):
    def setUp(self) -> None:
        self.sql, self.params = _upsert_orders(PAYLOAD)
        self.set = _set_del_conflicto(self.sql)

    def test_skus_se_llena_solo_si_es_nulo(self):
        self.assertEqual(self.set["skus"], "coalesce(channel.orders.skus, excluded.skus)")

    def test_full_se_toma_solo_del_esqueleto(self):
        self.assertEqual(
            self.set["es_fulfillment"],
            "case when channel.orders.skus is null then excluded.es_fulfillment "
            "else channel.orders.es_fulfillment end")

    def test_la_comision_nula_cuenta_como_nunca_observada(self):
        self.assertEqual(
            self.set["comision"],
            "case when coalesce(channel.orders.comision, 0) = 0 then excluded.comision "
            "else channel.orders.comision end")

    def test_el_total_sigue_igual(self):
        self.assertEqual(
            self.set["total"],
            "case when coalesce(channel.orders.total, 0) = 0 then excluded.total "
            "else channel.orders.total end")

    def test_creado_at_y_registrado_at_no_se_reescriben(self):
        self.assertNotIn("creado_at", self.set)
        self.assertNotIn("registrado_at", self.set)
        self.assertNotIn("registrado_at", self.sql)

    def test_el_insert_sigue_llevando_lo_que_trae_la_venta(self):
        self.assertIn("es_fulfillment, skus, creado_at)", self.sql)
        self.assertIs(self.params[8], True)
        self.assertEqual(self.params[9], ["ACC-0001-AZL"])

    def test_sin_skus_va_un_arreglo_vacio_no_nulo(self):
        # Un NULL dejaría la fila marcada como esqueleto para siempre.
        _, params = _upsert_orders({**PAYLOAD, "skus": None})
        self.assertEqual(params[9], [])

    def test_lo_de_siempre_sigue_en_el_conflicto(self):
        self.assertEqual(set(self.set), {"wc_order_id", "estado_canal", "estado_wc", "total",
                                         "comision", "es_fulfillment", "skus", "actualizado_at"})


class ElReclamoNoNombraRegistradoAt(unittest.TestCase):
    def test_reclamar_vive_del_default(self):
        fuente = Path(orders_write.__file__).read_text(encoding="utf-8")
        insert = re.search(r'"insert into channel\.orders \(canal, cuenta, external_order_id, "\s*'
                           r'"\s*creado_at, actualizado_at\) "', fuente)
        self.assertIsNotNone(insert, "el INSERT del reclamo cambió: revisar registrado_at")

    def test_ningun_escritor_nombra_registrado_at(self):
        backend = RAIZ / "backend"
        for f in list((backend / "services").glob("*.py")) + list((backend / "routers").glob("*.py")):
            texto = f.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"(insert into|update)\s+channel\.orders\b[^;]*?registrado_at",
                                 texto, re.I | re.S):
                self.fail(f"{f.name} escribe registrado_at: {m.group(0)[:80]}")


class Migracion0079(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = M79.read_text(encoding="utf-8")
        self.sentencias = [" ".join(s.split()) for s in
                           re.sub(r"--[^\n]*", "", self.sql).split(";") if s.strip()]

    def test_columna_sin_default_y_luego_el_default(self):
        alta = self.sentencias.index(
            "alter table channel.orders add column if not exists registrado_at timestamptz")
        default = self.sentencias.index(
            "alter table channel.orders alter column registrado_at set default now()")
        self.assertLess(alta, default)

    def test_candado_en_la_misma_migracion(self):
        self.assertIn("alter table channel.orders enable row level security", self.sentencias)
        self.assertIn("grant all on channel.orders to service_role", self.sentencias)

    def test_no_toca_datos(self):
        self.assertFalse([s for s in self.sentencias if re.match(r"(update|delete)\b", s, re.I)])

    def test_se_registra_con_su_nombre(self):
        self.assertIn("'0079_channel_orders_registrado_at'", self.sql)

    def test_el_manifiesto_trae_la_columna_al_final(self):
        man = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
        cols = man["esquemas"]["channel"]["orders"]["columnas"]
        self.assertEqual(cols[-1], "registrado_at:timestamp with time zone:null")


if __name__ == "__main__":
    unittest.main()
