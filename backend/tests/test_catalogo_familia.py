"""Pruebas de FAMILIA Y CICLO DEL CATÁLOGO (PLAN MAESTRO 3.2 / F0.3, 28-oct-2026).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **El ETL deriva la familia** de `wc_parent_id` en cada corrida (`derivar_familia`): padre = fila de la que
   cuelga alguna; `parent_sku` = el sku del padre; una variante cuyo padre no está en el padrón se cuenta.
   Va a CAMPOS y no a CAMPOS_SEAM (no rompe la racha de seam_gap).
2. **El archivado del ETL tiene frenos** (`a_archivar`): con una lectura de Woo incompleta o con demasiados
   de golpe NO archiva y dice por qué.
3. **La 0080**: columnas, el mapeo en TRES funciones (el único lugar), relleno antes de default/NOT NULL/CHECK,
   el trigger de bajas, el candado y su fila en ops.migraciones.
4. **El manifiesto** trae `core.products` re-medida en producción más lo de la 0080.

Sin red ni base:
    cd backend && python -m unittest tests.test_catalogo_familia -v
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "backend"))
_spec = importlib.util.spec_from_file_location("etl_v2", RAIZ / "backend" / "scripts" / "etl_core_products_v2.py")
ETL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ETL)
M80 = RAIZ / "supabase" / "migrations" / "0080_core_products_familia_ciclo.sql"


def reg(sku, wc=None, padre=None):
    return {"sku": sku, "wc_id": wc, "wc_parent_id": padre, "has_variations": False}


class Familia(unittest.TestCase):
    def test_padre_variantes_y_huerfana(self):
        regs = [reg("CAM-01", 10), reg("CAM-01-S", 11, 10), reg("CAM-01-M", 12, 10),
                reg("SIMPLE", 20), reg("VAR-SIN-PADRE", 31, 99), reg("PACKING")]
        r = ETL.derivar_familia(regs)
        por = {x["sku"]: x for x in regs}
        self.assertTrue(por["CAM-01"]["has_variations"])
        self.assertEqual((por["CAM-01-S"]["parent_sku"], por["CAM-01-M"]["parent_sku"]), ("CAM-01", "CAM-01"))
        self.assertFalse(por["SIMPLE"]["has_variations"])
        self.assertIsNone(por["SIMPLE"]["parent_sku"])
        self.assertIsNone(por["VAR-SIN-PADRE"]["parent_sku"])
        self.assertEqual((r["padres"], r["variaciones"], r["variaciones_sin_padre_en_padron"]), (1, 3, 1))

    def test_has_variations_que_venia_en_true_se_recalcula(self):
        r = reg("X", 5)
        r["has_variations"] = True                     # la fuente retirada `productos` lo traía
        ETL.derivar_familia([r])
        self.assertFalse(r["has_variations"])

    def test_campos(self):
        self.assertIn("parent_sku", ETL.CAMPOS)
        self.assertIn("has_variations", ETL.CAMPOS)
        self.assertFalse({"parent_sku", "has_variations"} & set(ETL.CAMPOS_SEAM))


class Archivar(unittest.TestCase):
    def test_lo_que_woo_ya_no_tiene(self):
        faltan, motivo = ETL.a_archivar([("A", 1), ("B", 2), ("C", 3)], {1, 3, 9})
        self.assertIsNone(motivo)
        self.assertEqual(faltan, ["B"])

    def test_lectura_incompleta_no_archiva(self):
        vivos = [(f"S{i}", i) for i in range(100)]
        faltan, motivo = ETL.a_archivar(vivos, set(range(50)))
        self.assertEqual(faltan, [])
        self.assertIn("incompleta", motivo)

    def test_demasiados_de_golpe_no_archiva(self):
        vivos = [(f"S{i}", i) for i in range(1000)]
        woo = set(range(1000 - ETL.ARCHIVAR_MAX - 1)) | set(range(5000, 5000 + ETL.ARCHIVAR_MAX + 1))
        faltan, motivo = ETL.a_archivar(vivos, woo)
        self.assertEqual(faltan, [])
        self.assertIn("de golpe", motivo)

    def test_padron_vacio(self):
        self.assertEqual(ETL.a_archivar([], set()), ([], None))


class Migracion0080(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = M80.read_text(encoding="utf-8")
        self.s = " ".join(re.sub(r"--[^\n]*", "", self.sql).split())

    def test_la_publicacion_cuenta_por_familia(self):
        self.assertIn("create or replace function core.sku_publicado(p_sku citext)", self.s)
        self.assertIn("join channel.listings l on l.sku = pr.parent_sku where pr.sku = p_sku", self.s)
        self.assertIn("join channel.listings l on l.sku = h.sku where h.parent_sku = p_sku", self.s)
        self.assertEqual(self.s.count("core.publicacion_abierta(l.canal, l.status, l.situacion)"), 3)
        self.assertIn("set estado_ciclo = core.estado_ciclo_inicial(p.status, core.sku_publicado(p.sku))", self.s)
        # la familia se llena ANTES que el ciclo: sku_publicado lee parent_sku
        self.assertLess(self.s.index("set parent_sku = p.sku"), self.s.index("set estado_ciclo ="))

    def test_el_mapeo_vive_en_sus_funciones(self):
        self.assertIn("create or replace function core.publicacion_abierta(p_canal text, p_status text, "
                      "p_situacion text)", self.s)
        self.assertIn("create or replace function core.estado_ciclo_inicial(p_status text, p_publicada boolean)",
                      self.s)
        for regla in ("when 'general' then false", "<> 'closed'", "<> 'DELETED'", "<> '5/None'",
                      "when p_publicada then 'publicado'", "when p_status = 'ready' then 'listo'",
                      "else 'draft'"):
            self.assertIn(regla, self.s)

    def test_relleno_antes_de_default_not_null_y_check(self):
        i_relleno = self.s.index("set estado_ciclo = core.estado_ciclo_inicial(")
        self.assertLess(i_relleno, self.s.index("alter column estado_ciclo set default 'draft'"))
        self.assertLess(i_relleno, self.s.index("alter column estado_ciclo set not null"))
        self.assertLess(i_relleno, self.s.index("add constraint products_estado_ciclo_chk"))
        self.assertIn("where p.estado_ciclo is null", self.s)

    def test_familia_desde_wc_parent_id(self):
        self.assertIn("set parent_sku = p.sku from core.products p where h.wc_parent_id is not null "
                      "and p.wc_id = h.wc_parent_id", self.s)
        self.assertIn("set has_variations = true", self.s)

    def test_trigger_de_bajas(self):
        self.assertIn("if new.status in ('trash', 'deleted') then new.estado_ciclo := 'archivado'", self.s)
        self.assertIn("create trigger products_archivar before insert or update of status on core.products", self.s)

    def test_candado_y_registro(self):
        self.assertIn("alter table core.products enable row level security", self.s)
        self.assertIn("grant all on core.products to service_role", self.s)
        self.assertIn("'0080_core_products_familia_ciclo'", self.s)
        self.assertFalse(re.search(r"\bdelete\s+from\b", self.s, re.I))


class Manifiesto(unittest.TestCase):
    def test_core_products(self):
        man = json.loads((RAIZ / "supabase" / "schema_manifest.json").read_text(encoding="utf-8"))
        cols = man["esquemas"]["core"]["products"]["columnas"]
        self.assertEqual(cols[-2:], ["estado_ciclo:text:notnull", "procesada_at:timestamp with time zone:null"])
        self.assertIn("almacen_en:timestamp with time zone:null", cols)
        self.assertIn("products_archivar", man["triggers"]["core.products"])


if __name__ == "__main__":
    unittest.main()
