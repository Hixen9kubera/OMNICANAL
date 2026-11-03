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
    """La 0080 con el diseño de Eduardo del 2-nov: publicado se CALCULA, archivado es una BAJA aparte."""

    def setUp(self) -> None:
        self.sql = M80.read_text(encoding="utf-8")
        self.s = " ".join(re.sub(r"--[^\n]*", "", self.sql).split())

    def test_estado_ciclo_es_solo_preparacion(self):
        self.assertIn("check (estado_ciclo in ('draft', 'procesado', 'listo'))", self.s)
        self.assertNotIn("'publicado'", self.s)
        self.assertNotIn("estado_ciclo := 'archivado'", self.s)

    def test_columnas_de_la_baja_y_su_coherencia(self):
        for col in ("archivado_at timestamptz", "archivado_por text", "archivado_motivo text",
                    "archivado_sku_destino citext references core.products (sku)"):
            self.assertIn(f"add column if not exists {col}", self.s)
        self.assertIn("(archivado_at is null) = (archivado_motivo is null)", self.s)
        self.assertIn("(archivado_at is null) = (archivado_por is null)", self.s)
        for m in ("'descontinuado'", "'duplicado'", "'recodificado'", "'prueba'", "'papelera_woo'", "'ya_no_en_woo'"):
            self.assertIn(m, self.s)
        # el destino va si y solo si es recodificado
        self.assertIn("(archivado_sku_destino is null or archivado_motivo = 'recodificado')", self.s)
        self.assertIn("(archivado_motivo is distinct from 'recodificado' or archivado_sku_destino is not null)", self.s)
        self.assertIn("create index if not exists products_archivados_idx on core.products (sku) "
                      "where archivado_at is not null", self.s)

    def test_publicado_se_calcula_y_cuenta_por_familia(self):
        self.assertIn("create or replace function core.publicacion_abierta(p_canal text, p_status text, "
                      "p_situacion text, p_listing_id text)", self.s)
        self.assertIn("create or replace function core.sku_publicado(p_sku citext, p_por_familia boolean default true)",
                      self.s)
        self.assertIn("join channel.listings l on l.sku = pr.parent_sku where pr.sku = p_sku", self.s)
        self.assertIn("join channel.listings l on l.sku = h.sku where h.parent_sku = p_sku", self.s)
        self.assertEqual(self.s.count("core.publicacion_abierta(l.canal, l.status, l.situacion, l.listing_id)"), 3)
        self.assertEqual(self.s.count("p_por_familia and exists"), 2)
        # nadie lo guarda: ninguna columna «publicado» ni relleno que lo escriba
        self.assertNotRegex(self.s, r"add column if not exists publicad")
        self.assertIn("core.sku_publicado(p.sku) as publicado", self.s)

    def test_mercado_libre_es_la_regla_de_catalogo(self):
        from services import channel_read
        sits = ", ".join(f"'{x}'" for x in channel_read._SIT_PUBLICADO_ML)
        self.assertIn(f"when 'mercado_libre' then lower(coalesce(p_situacion, '')) in ({sits})", self.s)
        self.assertIn("select nullif(p_listing_id, '') is not null and case p_canal", self.s)
        # la misma condición que channel_read.sql_publicado_ml
        ml = " ".join(channel_read.sql_publicado_ml("x").split())
        self.assertIn("nullif(l.listing_id, '') is not null", ml)
        self.assertIn(f"lower(coalesce(l.situacion, '')) in ({sits})", ml)

    def test_reglas_de_los_demas_canales(self):
        for regla in ("when 'general' then false",
                      "upper(coalesce(p_situacion, '')) in ('BUYABLE', 'DISCOVERABLE', 'PUBLISHED') "
                      "and upper(coalesce(p_status, '')) not in ('DELETED', 'INVALID')",
                      "in ('ACTIVATE', 'SELLER_DEACTIVATED', 'PLATFORM_DEACTIVATED', 'FREEZE')",
                      "when 'temu' then coalesce(p_status, '') in ('2/8', '3/1', '3/3', '2/4', '3/2')",
                      "when 'walmart' then upper(coalesce(p_status, '')) in ('PUBLISHED', 'UNPUBLISHED')",
                      "else false end"):
            self.assertIn(regla, self.s)

    def test_preparacion_de_arranque(self):
        self.assertIn("create or replace function core.estado_ciclo_inicial(p_status text)", self.s)
        for regla in ("when p_status in ('ready', 'publish') then 'listo'",
                      "when p_status = 'pending' then 'procesado'", "else 'draft'"):
            self.assertIn(regla, self.s)

    def test_relleno_antes_de_default_not_null_y_check_sin_mover_updated_at(self):
        i_relleno = self.s.index("set estado_ciclo = core.estado_ciclo_inicial(status) where estado_ciclo is null")
        self.assertLess(i_relleno, self.s.index("alter column estado_ciclo set default 'draft'"))
        self.assertLess(i_relleno, self.s.index("alter column estado_ciclo set not null"))
        self.assertLess(i_relleno, self.s.index("add constraint products_estado_ciclo_chk"))
        apaga = self.s.index("disable trigger trg_touch_products")
        prende = self.s.index("enable trigger trg_touch_products")
        self.assertLess(apaga, self.s.index("set parent_sku = p.sku"))
        self.assertLess(self.s.index("archivado_motivo = 'papelera_woo' where status in ('trash', 'deleted') "
                                     "and archivado_at is null"), prende)

    def test_familia_desde_wc_parent_id(self):
        self.assertIn("set parent_sku = p.sku from core.products p where h.wc_parent_id is not null "
                      "and p.wc_id = h.wc_parent_id", self.s)
        self.assertIn("set has_variations = true", self.s)

    def test_trigger_de_bajas_nunca_desarchiva(self):
        self.assertIn("if new.status in ('trash', 'deleted') and new.archivado_at is null then "
                      "new.archivado_at := now(); new.archivado_por := 'woo'; new.archivado_motivo := 'papelera_woo';",
                      self.s)
        self.assertNotRegex(self.s, r"archivado_at\s*:=\s*null")
        self.assertIn("create trigger products_archivar before insert or update of status on core.products", self.s)

    def test_vista_con_las_dos_preguntas(self):
        self.assertIn("create or replace view core.products_ciclo_v with (security_invoker = true)", self.s)
        for sit in ("'incoherente'", "'baja_limpia'", "'normal'", "'pendiente'"):
            self.assertIn(sit, self.s)

    def test_candado_y_registro(self):
        self.assertIn("alter table core.products enable row level security", self.s)
        self.assertIn("grant all on core.products to service_role", self.s)
        self.assertIn("revoke all on core.products_ciclo_v from public, anon, authenticated", self.s)
        self.assertIn("grant select on core.products_ciclo_v to service_role", self.s)
        for f in ("core.publicacion_abierta(text, text, text, text)", "core.estado_ciclo_inicial(text)",
                  "core.sku_publicado(citext, boolean)", "core.tg_products_archivar()"):
            self.assertIn(f"revoke all on function {f} from public, anon, authenticated", self.s)
        self.assertIn("'0080_core_products_familia_ciclo'", self.s)
        self.assertFalse(re.search(r"\bdelete\s+from\b", self.s, re.I))


class EtlArchivaEnLaBaja(unittest.TestCase):
    def test_el_etl_archiva_en_archivado_at_y_no_en_estado_ciclo(self):
        src = (RAIZ / "backend" / "scripts" / "etl_core_products_v2.py").read_text(encoding="utf-8")
        self.assertIn("column_name = 'archivado_at'", src)
        self.assertIn("where wc_id is not null and archivado_at is null", src)
        self.assertIn("set archivado_at = now(), archivado_por = 'etl', ", src)
        self.assertIn("archivado_motivo = 'ya_no_en_woo' where sku = any(%s) and archivado_at is null", src)
        self.assertNotIn("estado_ciclo = 'archivado'", src)
        self.assertNotIn("estado_ciclo <> 'archivado'", src)


class Manifiesto(unittest.TestCase):
    def test_core_products(self):
        man = json.loads((RAIZ / "supabase" / "schema_manifest.json").read_text(encoding="utf-8"))
        det = man["esquemas"]["core"]["products"]
        self.assertEqual(det["columnas"][-6:], [
            "estado_ciclo:text:notnull", "procesada_at:timestamp with time zone:null",
            "archivado_at:timestamp with time zone:null", "archivado_por:text:null", "archivado_motivo:text:null",
            "archivado_sku_destino:USER-DEFINED:null"])
        self.assertIn("almacen_en:timestamp with time zone:null", det["columnas"])
        self.assertEqual(det["constraints"]["CHECK"], 5)
        self.assertEqual(det["constraints"]["FOREIGN KEY"], 2)
        self.assertIn("products_archivar", man["triggers"]["core.products"])
        self.assertIn("products_ciclo_v", man["esquemas"]["core"])


if __name__ == "__main__":
    unittest.main()
