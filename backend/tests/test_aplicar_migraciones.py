"""Pruebas del runner de migraciones del sandbox (backend/scripts/aplicar_migraciones.py)
y de la fase 0 del reorden de esquemas (0033_…_forma_prod, 0034-0036, 0066, 0067).

SIN BASE: el registro (ops.migraciones) se simula con una conexión falsa que
entiende las pocas sentencias que el runner le manda. Lo que fijan:
  · el plan: base nueva, base existente sin registro, base con registro,
    adopción (frontera, --excepto, huellas de todas las clases, la migración
    sin nada verificable), atrasadas, --recrear y sus requisitos externos;
  · una transacción por archivo con su fila, sin duplicar la de los archivos que
    se registran solos (las tres formas de insert de la casa, con las reales
    0068-0070 de Brandon), el registro tardío de lo que corrió antes de la 0064,
    qué queda si un archivo truena a la mitad, que una fila con OTRO nombre
    deshace el archivo (y que un insert renumerado se rechaza antes de tocar la
    base, en --plan también) y que dos filas propias en una corrida también;
  · --hasta NNNN: corre lo de abajo y deja pendiente lo de arriba;
  · que el begin/commit de un archivo se quita solo en la forma de la casa;
  · main: que --plan no escribe (transacción read only y ROLLBACK), con y sin
    adopción; --recrear (tira también ventas y almacen, registra tarde, y no
    tira nada si faltan requisitos); que con la URL de producción sale ANTES de
    conectar; y que de env.staging solo se leen dos llaves;
  · que los esquemas nuevos están en las tres listas y en el manifiesto (con la
    mudanza de la 0068, sus vistas puente y la 0069/0070, medidos en producción).
"""
from __future__ import annotations

import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

import aplicar_migraciones as A  # noqa: E402
import verificar_rls  # noqa: E402

RAIZ = BACKEND.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"


def _m(nombre: str, carpeta: Path | None = None) -> A.Migracion:
    ruta = (carpeta or Path("x")) / f"{nombre}.sql"
    return A.Migracion(nombre, int(nombre[:4]), ruta)


def _escribir(carpeta: Path, nombre: str, texto: str) -> A.Migracion:
    ruta = carpeta / f"{nombre}.sql"
    ruta.write_text(texto, encoding="utf-8")
    return A.Migracion(nombre, int(nombre[:4]), ruta)


# ═══════════════════════════════════════════════════════════════════════════
# Una base falsa: solo lo que el runner le pregunta a ops.migraciones
# ═══════════════════════════════════════════════════════════════════════════
class BaseFalsa:
    """Las «migraciones» falsas llevan marcas en su texto:
         @CREA_REGISTRO  → nace ops.migraciones (como la 0064)
         @SE_REGISTRA:x  → el archivo inserta su propia fila 'x' (0064-0067)
         @TRUENA         → la sentencia falla
         @AVISA          → deja un NOTICE
    Los archivos REALES también se entienden (sin comentarios): `create table
    if not exists ops.migraciones` y su `insert into ops.migraciones … values
    ('NNNN_…'`. Lo confirmado vive en `filas`/`tabla`; lo de la transacción
    abierta, aparte. El catálogo (`-- catalogo:<clase>`) sale de `catalogo`,
    los requisitos de --recrear de `existentes`, y `tablas_propias` cuenta."""

    def __init__(self, tabla: bool = False, filas: list[dict] | None = None,
                 catalogo: dict | None = None, existentes: set | None = None, tablas_propias: int = 1):
        self.tabla, self.filas = tabla, list(filas or [])
        self._tabla_tx, self._filas_tx = None, []
        self.sentencias: list[str] = []
        self.commits = self.rollbacks = 0
        self.notices: list[str] = []
        self.solo_lectura = False
        self.catalogo = catalogo or {}
        self.existentes = set(existentes or ())
        self.tablas_propias = tablas_propias

    # -- la conexión
    def cursor(self):
        return CursorFalso(self)

    def commit(self):
        if self._tabla_tx:
            self.tabla = True
        self.filas += self._filas_tx
        self._tabla_tx, self._filas_tx = None, []
        self.commits += 1
        self.solo_lectura = False

    def rollback(self):
        self._tabla_tx, self._filas_tx = None, []
        self.rollbacks += 1
        self.solo_lectura = False

    # -- lo que ve la transacción abierta
    def hay_tabla(self) -> bool:
        return bool(self.tabla or self._tabla_tx)

    def todas(self) -> list[dict]:
        return self.filas + self._filas_tx


class CursorFalso:
    def __init__(self, base: BaseFalsa):
        self.b = base
        self._res: list[tuple] = []

    def execute(self, sql: str, params=None):
        b = self.b
        b.sentencias.append(sql)
        s = " ".join(sql.split()).lower()
        clase = re.match(r"-- catalogo:(\w+)", sql)
        if s == "set transaction read only":
            b.solo_lectura = True
            self._res = []
        elif clase and clase.group(1) == "requisitos":
            self._res = [(r, r in b.existentes) for r in params[0]]
        elif clase and clase.group(1) == "propias":
            self._res = [(b.tablas_propias,)]
        elif clase:
            self._res = list(b.catalogo.get(clase.group(1), []))
        elif s.startswith("select pg_advisory_xact_lock"):
            self._res = [(None,)]
        elif s == "select to_regclass('ops.migraciones') is not null":
            self._res = [(b.hay_tabla(),)]
        elif s.startswith("select count(*) from ops.migraciones where migracion"):
            self._res = [(sum(1 for f in b.todas() if f["migracion"] == params[0]),)]
        elif s.startswith("select migracion, count(*) from ops.migraciones"):
            cuenta: dict[str, int] = {}
            for f in b.todas():
                cuenta[f["migracion"]] = cuenta.get(f["migracion"], 0) + 1
            self._res = list(cuenta.items())
        elif s.startswith("insert into ops.migraciones"):
            if b.solo_lectura:
                raise RuntimeError("cannot execute INSERT in a read-only transaction")
            if not b.hay_tabla():
                raise RuntimeError('relation "ops.migraciones" does not exist')
            b._filas_tx.append({"migracion": params[0], "detalle": json.loads(params[1])})
            self._res = []
        else:   # el texto de una migración (o un drop schema de --recrear)
            if b.solo_lectura:
                raise RuntimeError("una migración en una transacción read only")
            if "@TRUENA" in sql:
                raise RuntimeError("42P01: relation does not exist")
            limpio = verificar_rls._sin_comentarios(sql).lower()
            if "@CREA_REGISTRO" in sql or re.search(r"create\s+table\s+if\s+not\s+exists\s+ops\.migraciones", limpio):
                b._tabla_tx = True
            # Las tres formas reales: `values ('…'` (0064-0067), `select '…'` dentro
            # de un DO (0068) y `select '…' … where not exists (… = '…')` (0069,
            # 0070), que no inserta si la fila ya está.
            propias = [(n, False) for n in re.findall(r"@SE_REGISTRA:(\w+)", sql)] + [
                (x.group(2), "not exists" in x.group(3)) for x in re.finditer(
                    r"insert\s+into\s+ops\.migraciones\s*\(\s*migracion\s*,\s*detalle\s*\)\s*"
                    r"(values\s*\(|select)\s*'(\w+)'(.*?);", limpio, re.S)]
            for propia, condicional in propias:
                if not b.hay_tabla():
                    raise RuntimeError('relation "ops.migraciones" does not exist')
                if condicional and any(f["migracion"] == propia for f in b.todas()):
                    continue
                b._filas_tx.append({"migracion": propia, "detalle": {"propia": True}})
            if "@AVISA" in sql:
                b.notices.append("NOTICE:  aviso de prueba\n")
            self._res = []

    def fetchone(self):
        return self._res[0] if self._res else None

    def fetchall(self):
        return list(self._res)


def _silencio(*_a, **_k):
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Los archivos y su forma
# ═══════════════════════════════════════════════════════════════════════════
class ArchivosTest(unittest.TestCase):
    def test_orden_de_siempre_con_numeros_repetidos(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("0002_b", "0001_a", "0004_z", "0004_a", "0033_ops_odoo_sale_orders",
                      "0033_ops_odoo_sale_orders_forma_prod", "0034_costing_caja"):
                (Path(d) / f"{n}.sql").write_text("select 1;", encoding="utf-8")
            nombres = [m.nombre for m in A.listar_migraciones(Path(d))]
        self.assertEqual(nombres, ["0001_a", "0002_b", "0004_a", "0004_z", "0033_ops_odoo_sale_orders",
                                   "0033_ops_odoo_sale_orders_forma_prod", "0034_costing_caja"])

    def test_nombre_que_el_check_rechazaria(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "0070_Mayusculas.sql").write_text("select 1;", encoding="utf-8")
            with self.assertRaises(ValueError):
                A.listar_migraciones(Path(d))

    def test_los_archivos_reales_cumplen_el_check_y_el_orden(self):
        ms = A.listar_migraciones(MIGRACIONES)
        self.assertTrue(all(A.NOMBRE_RE.match(m.nombre) for m in ms))
        nombres = [m.nombre for m in ms]
        i = nombres.index("0033_ops_odoo_sale_orders")
        # El ajuste de forma va JUSTO después de la 0033 y antes de la 0034-0036.
        self.assertEqual(nombres[i:i + 5], ["0033_ops_odoo_sale_orders", "0033_ops_odoo_sale_orders_forma_prod",
                                            "0034_costing_caja_compartida", "0035_ops_automatizacion_flags",
                                            "0036_ops_odoo_bitacora"])
        # La fase 0 (0066, 0067) y lo de Brandon del 9-oct (0068-0070), seguidas y en orden.
        i = nombres.index("0066_esquemas_ventas_almacen")
        self.assertEqual(nombres[i:i + 5], ["0066_esquemas_ventas_almacen",
                                            "0067_ops_quitar_devoluciones_vs_canal_v",
                                            "0068_mudanza_ov_a_ventas_y_almacen", "0069_almacen_locations",
                                            "0070_almacen_historial_movimientos"])
        # Un número, un archivo, de la 0064 en adelante (las repetidas son de antes).
        desde_64 = [m.numero for m in ms if m.numero >= 64]
        self.assertEqual(len(desde_64), len(set(desde_64)))

    def test_sin_control_de_transaccion_va_tal_cual(self):
        t = "create table if not exists ops.x (id int);\n"
        self.assertEqual(A.en_una_transaccion(t), t)

    def test_la_pareja_begin_commit_se_quita_sin_mover_lineas(self):
        t = ("-- encabezado\nbegin;\n\nset local lock_timeout = '3s';\n"
             "create or replace function ops.f() returns trigger language plpgsql as $$\n"
             "begin\n  return new;\nend;\n$$;\n"
             "do $$\nbegin\n  perform 1;\nend $$;\n"
             "COMMIT;  -- fin\n-- pie\n")
        out = A.en_una_transaccion(t, "x.sql")
        self.assertEqual(out.count("\n"), t.count("\n"))
        self.assertNotRegex(out, r"(?im)^\s*begin\s*;")
        self.assertNotRegex(out, r"(?im)^\s*commit\s*;")
        # Lo de plpgsql queda intacto: `begin` sin `;` y `end;` dentro del cuerpo.
        self.assertIn("begin\n  return new;\nend;\n$$;", out)
        self.assertIn("end $$;", out)

    def test_formas_que_no_caben_en_una_transaccion(self):
        for t in ("begin;\nselect 1;\ncommit;\nbegin;\nselect 2;\ncommit;\n",
                  "begin;\nselect 1;\nrollback;\n",
                  "select 1;\ncommit;\n",
                  "commit;\nbegin;\n"):
            with self.subTest(t=t), self.assertRaises(ValueError):
                A.en_una_transaccion(t, "x.sql")

    def test_todos_los_archivos_reales_tienen_forma_soportada(self):
        for m in A.listar_migraciones(MIGRACIONES):
            texto = m.ruta.read_text(encoding="utf-8")
            with self.subTest(m=m.nombre):
                out = A.en_una_transaccion(texto, m.ruta.name)
                self.assertFalse(list(A._CONTROL.finditer(out)))

    def test_sha_igual_con_crlf_o_lf(self):
        self.assertEqual(A.huella_sha("a\r\nb\r\n"), A.huella_sha("a\nb\n"))


# ═══════════════════════════════════════════════════════════════════════════
# El plan
# ═══════════════════════════════════════════════════════════════════════════
CADENA = [_m(n) for n in ("0001_esquema", "0033_ops_odoo_sale_orders", "0046_actor", "0051_modo",
                          "0063_juez", "0064_ops_ordenes_venta", "0065_ops_inventario_kubera",
                          "0066_esquemas_ventas_almacen", "0067_ops_quitar_vista")]
SANDBOX_HOY = {"0064_ops_ordenes_venta": 1, "0065_ops_inventario_kubera": 1}


class PlanTest(unittest.TestCase):
    def test_base_nueva_corre_todo(self):
        p = A.armar_plan(CADENA, None)
        self.assertFalse(p.registro_existe)
        self.assertEqual(p.correr, CADENA)
        self.assertIsNone(p.bloqueo)

    def test_sin_registro_pero_con_tablas_no_es_base_nueva(self):
        p = A.armar_plan(CADENA, None, base_con_tablas=True)
        self.assertTrue(p.base_con_tablas)
        self.assertEqual(p.correr, [])
        self.assertIn("--recrear", p.bloqueo)
        self.assertIn("stock_texco", p.bloqueo)

    def test_recrear_ignora_el_registro_y_no_adopta(self):
        p = A.armar_plan(CADENA, SANDBOX_HOY, recrear=True)
        self.assertEqual(p.correr, CADENA)
        with self.assertRaises(ValueError):
            A.armar_plan(CADENA, SANDBOX_HOY, recrear=True, adoptar_hasta=63)

    def test_sin_registro_no_hay_contra_que_adoptar(self):
        with self.assertRaises(ValueError):
            A.armar_plan(CADENA, None, adoptar_hasta=63)

    def test_sandbox_de_hoy_sin_adoptar_se_detiene(self):
        p = A.armar_plan(CADENA, SANDBOX_HOY)
        self.assertEqual(p.frontera, 65)
        self.assertEqual([m.nombre for m in p.saltar], ["0064_ops_ordenes_venta", "0065_ops_inventario_kubera"])
        self.assertEqual([m.nombre for m in p.atrasadas],
                         ["0001_esquema", "0033_ops_odoo_sale_orders", "0046_actor", "0051_modo", "0063_juez"])
        self.assertIn("--adoptar-hasta", p.bloqueo)

    def test_incluir_atrasadas_corre_en_orden_de_archivo(self):
        p = A.armar_plan(CADENA, SANDBOX_HOY, incluir_atrasadas=True)
        self.assertIsNone(p.bloqueo)
        self.assertEqual(p.correr, [m for m in CADENA if m.nombre not in SANDBOX_HOY])

    def test_adoptar_hasta_0063_con_excepto(self):
        p = A.armar_plan(CADENA, SANDBOX_HOY, adoptar_hasta=63, excepto=["0046_actor", "0051_modo"])
        self.assertEqual([m.nombre for m in p.adoptar], ["0001_esquema", "0033_ops_odoo_sale_orders", "0063_juez"])
        self.assertEqual([m.nombre for m in p.excluidas], ["0046_actor", "0051_modo"])
        self.assertEqual(p.correr, [])      # adoptar NO ejecuta nada
        self.assertIsNone(p.bloqueo)

    def test_despues_de_adoptar_solo_quedan_las_nuevas(self):
        registro = dict(SANDBOX_HOY, **{n: 1 for n in ("0001_esquema", "0033_ops_odoo_sale_orders",
                                                       "0046_actor", "0051_modo", "0063_juez")})
        p = A.armar_plan(CADENA, registro)
        self.assertIsNone(p.bloqueo)
        self.assertEqual([m.nombre for m in p.correr], ["0066_esquemas_ventas_almacen", "0067_ops_quitar_vista"])
        self.assertEqual(p.atrasadas, [])

    def test_lo_excluido_queda_atrasado(self):
        registro = dict(SANDBOX_HOY, **{n: 1 for n in ("0001_esquema", "0033_ops_odoo_sale_orders", "0063_juez")})
        p = A.armar_plan(CADENA, registro)
        self.assertEqual([m.nombre for m in p.atrasadas], ["0046_actor", "0051_modo"])
        self.assertIsNotNone(p.bloqueo)
        p = A.armar_plan(CADENA, registro, incluir_atrasadas=True)
        self.assertEqual([m.nombre for m in p.correr],
                         ["0046_actor", "0051_modo", "0066_esquemas_ventas_almacen", "0067_ops_quitar_vista"])

    def test_no_se_adopta_por_encima_de_la_frontera(self):
        with self.assertRaises(ValueError):
            A.armar_plan(CADENA, SANDBOX_HOY, adoptar_hasta=66)

    def test_excepto_mal_escrito_o_sin_adoptar(self):
        with self.assertRaises(ValueError):
            A.armar_plan(CADENA, SANDBOX_HOY, adoptar_hasta=63, excepto=["0046_actr"])
        with self.assertRaises(ValueError):
            A.armar_plan(CADENA, SANDBOX_HOY, excepto=["0046_actor"])

    def test_registro_vacio_corre_todo_y_no_adopta(self):
        p = A.armar_plan(CADENA, {})
        self.assertIsNone(p.frontera)
        self.assertEqual(p.correr, CADENA)
        self.assertIsNone(p.bloqueo)
        with self.assertRaises(ValueError):
            A.armar_plan(CADENA, {}, adoptar_hasta=63)

    def test_filas_sin_archivo_se_reportan(self):
        p = A.armar_plan(CADENA, dict(SANDBOX_HOY, **{"0062_market_series": 1}), incluir_atrasadas=True)
        self.assertEqual(p.huerfanas, ["0062_market_series"])

    def test_hasta_corre_solo_lo_de_abajo_y_pospone_lo_de_arriba(self):
        """El sandbox del 9-oct: registro hasta la 0067; recibe la 0068 y la 0069, no la 0070."""
        cadena = CADENA + [_m("0068_mudanza"), _m("0069_locations"), _m("0070_historial")]
        registro = {m.nombre: 1 for m in CADENA}
        p = A.armar_plan(cadena, registro, hasta=69)
        self.assertEqual([m.nombre for m in p.correr], ["0068_mudanza", "0069_locations"])
        self.assertEqual([m.nombre for m in p.pospuestas], ["0070_historial"])
        self.assertIsNone(p.bloqueo)
        # Sin --hasta, la próxima corrida toma lo pospuesto.
        registro.update({"0068_mudanza": 1, "0069_locations": 1})
        self.assertEqual([m.nombre for m in A.armar_plan(cadena, registro).correr], ["0070_historial"])
        # --hasta no salta atrasadas: siguen bloqueando aunque queden arriba.
        p = A.armar_plan(cadena, {"0065_ops_inventario_kubera": 1, "0070_historial": 1}, hasta=64)
        self.assertIsNotNone(p.bloqueo)
        # Base nueva: también recorta.
        self.assertEqual(A.armar_plan(cadena, None, hasta=1).correr, [cadena[0]])
        for kw in ({"recrear": True}, {"adoptar_hasta": 63}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                A.armar_plan(cadena, registro, hasta=69, **kw)


class RegistroPropioTest(unittest.TestCase):
    """Las migraciones que se registran solas (0064-0070) y el nombre con que lo hacen."""

    def test_las_tres_formas_de_la_casa(self):
        valores = "insert into ops.migraciones (migracion, detalle)\nvalues ('0066_a', jsonb_build_object('esquemas', 'ventas'));"
        en_do = ("do $m$\ndeclare n int := 0;\nbegin\n  insert into ops.migraciones (migracion, detalle)\n"
                 "  select '0068_b', jsonb_build_object('tablas_movidas', n)\n"
                 "  where to_regclass('ops.migraciones') is not null;\nend\n$m$;")
        guarda = ("insert into ops.migraciones (migracion, detalle)\nselect '0069_c', jsonb_build_object('x', 1)\n"
                  " where not exists (select 1 from ops.migraciones where migracion = '0069_c');")
        self.assertEqual(A.registros_propios(valores), ["0066_a"])
        self.assertEqual(A.registros_propios(en_do), ["0068_b"])
        self.assertEqual(A.registros_propios(guarda), ["0069_c"])
        # Un comentario que lo menciona no cuenta; leer el registro tampoco.
        self.assertEqual(A.registros_propios("-- insert into ops.migraciones values ('0001_x');\n"
                                             "select count(*) from ops.migraciones where migracion = '0001_x';"), [])

    def test_otro_nombre_truena_y_el_propio_no(self):
        m = _m("0072_cuarentena")
        self.assertTrue(A.revisar_registro_propio(m, "insert into ops.migraciones (migracion, detalle) "
                                                     "values ('0072_cuarentena', null);"))
        self.assertFalse(A.revisar_registro_propio(m, "create table ops.x (id int);"))
        with self.assertRaises(ValueError) as cm:
            A.revisar_registro_propio(m, "insert into ops.migraciones (migracion, detalle) "
                                         "values ('0070_cuarentena', null);")
        self.assertIn("0070_cuarentena", str(cm.exception))

    def test_las_reales_se_registran_con_el_nombre_de_su_archivo(self):
        """Toda migración real que inserta en ops.migraciones lo hace con SU nombre.
        Es la red para las que vienen renumeradas (limpieza y cuarentena)."""
        solas = []
        for m in A.listar_migraciones(MIGRACIONES):
            texto = m.ruta.read_text(encoding="utf-8")
            with self.subTest(m=m.nombre):
                if A.revisar_registro_propio(m, texto):
                    self.assertEqual(A.registros_propios(texto), [m.nombre])
                    solas.append(m.nombre)
        self.assertTrue({"0064_ops_ordenes_venta", "0065_ops_inventario_kubera", "0066_esquemas_ventas_almacen",
                         "0067_ops_quitar_devoluciones_vs_canal_v", "0068_mudanza_ov_a_ventas_y_almacen",
                         "0069_almacen_locations", "0070_almacen_historial_movimientos"} <= set(solas))

    def test_huellas_bloquean_la_adopcion_a_ciegas(self):
        p = A.armar_plan(CADENA, SANDBOX_HOY, adoptar_hasta=63)
        A.aplicar_huellas(p, {"0046_actor": ["ops.channel_submissions.actor"],
                              "0001_esquema": ["ops.task_queue"]}, [])
        self.assertIn("0046_actor", p.bloqueo)
        p = A.armar_plan(CADENA, SANDBOX_HOY, adoptar_hasta=63, excepto=["0046_actor"])
        A.aplicar_huellas(p, {"0046_actor": ["x"], "0001_esquema": ["ops.task_queue"]}, ["0001_esquema"],
                          ["0033_ops_odoo_sale_orders", "0046_actor"])
        self.assertIsNone(p.bloqueo)
        self.assertEqual(list(p.sin_huella), ["0001_esquema"])   # la excluida ya no cuenta
        self.assertEqual(p.reemplazadas, ["0033_ops_odoo_sale_orders"])
        with self.assertRaises(ValueError):
            A.aplicar_huellas(p, {}, ["0099_no_esta"])


class HuellasTest(unittest.TestCase):
    def test_lo_que_crea_y_sigue_vivo(self):
        with tempfile.TemporaryDirectory() as d:
            c = Path(d)
            ms = [
                _escribir(c, "0001_a", "create table ops.t1 (id int); alter table ops.t1 enable row level security;\n"
                                      "create table ops.muerta (id int);\ncreate schema viejo;\n"
                                      "create table viejo.t3 (id int);"),
                _escribir(c, "0002_b", "alter table ops.t1 add column if not exists actor text;\n"
                                      "alter table ops.t1 add column x int, add column y int;\n"
                                      "create or replace view ops.v1 with (security_invoker = on) as select 1;"),
                _escribir(c, "0003_c", "drop table if exists ops.muerta;\nalter table ops.t1 drop column if exists y;\n"
                                      "alter schema viejo rename to nuevo;"),
            ]
            e = A.huellas_esperadas(ms)
        self.assertEqual(e["0001_a"]["relaciones"], {("ops", "t1"), ("nuevo", "t3")})
        self.assertEqual(e["0001_a"]["rls"], {("ops", "t1")})
        self.assertEqual(e["0002_b"]["relaciones"], {("ops", "v1")})
        self.assertEqual(e["0002_b"]["invoker"], {("ops", "v1")})
        self.assertEqual(e["0002_b"]["columnas"], {("ops", "t1", "actor"), ("ops", "t1", "x")})
        self.assertEqual(e["0003_c"]["ausentes"], {("rel", "ops", "muerta"), ("col", "ops", "t1", "y")})
        cat = A.Catalogo(relaciones={("ops", "t1"), ("nuevo", "t3")}, columnas={("ops", "t1", "x")},
                         rls={("ops", "t1")}, esquemas={"nuevo"})
        falta = A.faltantes_en_base(e, cat, ["0001_a", "0002_b", "0003_c"])
        self.assertEqual(falta, {"0002_b": ["ops.v1", "ops.t1.actor"]})
        # La que tiró algo que sigue vivo no está aplicada.
        cat.relaciones.add(("ops", "muerta"))
        cat.columnas.add(("ops", "t1", "y"))
        falta = A.faltantes_en_base(e, cat, ["0003_c"])
        self.assertEqual(falta, {"0003_c": ["sigue viva ops.t1.y", "sigue viva ops.muerta"]})

    def _cadena(self, *archivos):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        ms = [_escribir(Path(d.name), n, texto) for n, texto in archivos]
        return ms, A.huellas_esperadas(ms)

    def _plan_adopcion(self, ms, cat, sin_huella_ok=()):
        """La adopción de TODO lo de `ms` contra el catálogo `cat`."""
        registro = {"0099_frontera": 1}
        p = A.armar_plan(ms, registro, adoptar_hasta=98)
        e = A.huellas_esperadas(ms)
        nombres = [m.nombre for m in p.adoptar]
        A.aplicar_huellas(p, A.faltantes_en_base(e, cat, nombres), list(sin_huella_ok), A.reemplazadas(e, nombres))
        return p

    def test_una_migracion_que_solo_prende_rls_bloquea_si_no_esta(self):
        ms, e = self._cadena(("0001_t", "create table ops.t (id int);"),
                             ("0002_rls", "alter table ops.t enable row level security;"))
        self.assertEqual(e["0002_rls"]["rls"], {("ops", "t")})
        self.assertEqual(e["0001_t"]["rls"], set())
        p = self._plan_adopcion(ms, A.Catalogo(relaciones={("ops", "t")}))
        self.assertEqual(p.sin_huella, {"0002_rls": ["rls ops.t"]})
        self.assertIn("0002_rls", p.bloqueo)
        p = self._plan_adopcion(ms, A.Catalogo(relaciones={("ops", "t")}, rls={("ops", "t")}))
        self.assertIsNone(p.bloqueo)

    def test_invoker_lo_responde_quien_lo_dejo_y_un_replace_lo_quita(self):
        ms, e = self._cadena(("0001_v", "create view ops.v with (security_invoker = on) as select 1;"),
                             ("0002_replace", "create or replace view ops.v as select 2;"),
                             ("0003_reblinda", "alter view ops.v set (security_invoker = on);"))
        self.assertEqual(e["0003_reblinda"]["invoker"], {("ops", "v")})
        self.assertEqual(e["0002_replace"]["relaciones"], {("ops", "v")})
        self.assertEqual(e["0001_v"]["invoker"] | e["0001_v"]["relaciones"], set())
        self.assertEqual(A.reemplazadas(e, ["0001_v", "0002_replace"]), ["0001_v"])
        falta = A.faltantes_en_base(e, A.Catalogo(relaciones={("ops", "v")}), ["0003_reblinda"])
        self.assertEqual(falta, {"0003_reblinda": ["invoker ops.v"]})

    def test_funciones_por_argumentos_y_cuerpo(self):
        ms, e = self._cadena(
            ("0001_f", "create or replace function ops.purgar(dias int default 3, lote int default 20000)\n"
                       "returns bigint language plpgsql as $$\nbegin\n  return 1;  -- uno\nend;\n$$;"),
            ("0002_g", "create function ops.g(a numeric(10, 2), out b int, c text default 'x,y') returns int\n"
                       "language sql as $f$ select 1 $f$;"),
            ("0003_f3", "drop function if exists ops.purgar(int, int);\n"
                        "create or replace function ops.purgar(dias int default 3, lote int default 20000,\n"
                        "  bajo int default 90) returns bigint language plpgsql\n"
                        "set search_path = pg_catalog, pg_temp\nas $$\nbegin\n  return 3;\nend;\n$$;"))
        self.assertEqual(e["0002_g"]["funciones"], {("ops", "g", 2): "select 1"})
        self.assertEqual(e["0003_f3"]["funciones"], {("ops", "purgar", 3): "begin return 3; end;"})
        self.assertEqual(e["0003_f3"]["ausentes"], {("fn", "ops", "purgar", 2)})
        self.assertEqual(A.reemplazadas(e, ["0001_f"]), ["0001_f"])
        # El sandbox del 8-oct: sigue la de 2 argumentos, no está la de 3.
        cat = A.Catalogo(funciones={("ops", "purgar", 2): ["begin return 1; end;"], ("ops", "g", 2): ["select 1"]})
        self.assertEqual(A.faltantes_en_base(e, cat, ["0002_g", "0003_f3"]),
                         {"0003_f3": ["fn ops.purgar/3", "sigue viva fn ops.purgar/2"]})
        # Mismo número de argumentos, otro cuerpo: tampoco está aplicada.
        cat = A.Catalogo(funciones={("ops", "purgar", 3): ["begin return 1; end;"]})
        self.assertEqual(A.faltantes_en_base(e, cat, ["0003_f3"]), {"0003_f3": ["fn ops.purgar/3 (otro cuerpo)"]})

    def test_indices_triggers_constraints_policies_y_renombres(self):
        ms, e = self._cadena(
            ("0001_t", "create table channel.returns (id int, a int);\n"
                       "create index idx_returns_orden on channel.returns (a);\n"
                       "create policy leen on channel.returns for select using (true);"),
            ("0002_aparta", "alter table channel.returns rename to returns_v0;\n"
                            "alter index if exists channel.idx_returns_orden rename to idx_returns_v0_orden;\n"
                            "create table channel.returns (id int, b int);\n"
                            "create index if not exists idx_returns_orden on channel.returns (b);\n"
                            "drop trigger if exists t_guarda on channel.returns;\n"
                            "create trigger t_guarda before update on channel.returns for each row execute function ops.f();\n"
                            "alter table channel.returns rename column b to c, add constraint returns_c_ck check (c > 0);"))
        self.assertEqual(e["0001_t"]["indices"], set())   # lo renombró la 0002
        self.assertEqual(e["0001_t"]["politicas"], {("channel", "returns_v0", "leen")})
        self.assertEqual(e["0002_aparta"]["indices"], {("channel", "returns_v0", "idx_returns_v0_orden"),
                                                       ("channel", "returns", "idx_returns_orden")})
        self.assertEqual(e["0002_aparta"]["triggers"], {("channel", "returns", "t_guarda")})
        self.assertEqual(e["0002_aparta"]["columnas"], {("channel", "returns", "c")})
        self.assertEqual(e["0002_aparta"]["restricciones"], {("channel", "returns", "returns_c_ck")})
        cat = A.Catalogo(relaciones={("channel", "returns"), ("channel", "returns_v0")},
                         columnas={("channel", "returns", "b")},
                         indices={("channel", "returns_v0", "idx_returns_v0_orden")})
        self.assertEqual(A.faltantes_en_base(e, cat, ["0002_aparta"]), {"0002_aparta": [
            "channel.returns.c", "índice channel.returns.idx_returns_orden", "trigger channel.returns.t_guarda",
            "constraint channel.returns.returns_c_ck"]})

    def test_si_una_posterior_tira_y_recrea_la_tabla_lo_de_la_anterior_no_cuenta(self):
        ms, e = self._cadena(
            ("0001_t", "create table ops.t (id int);"),
            ("0002_idx", "create index ix_t on ops.t (id);\nalter table ops.t add column x int;"),
            ("0003_recrea", "drop table if exists ops.t;\ncreate table ops.t (id int);"))
        self.assertEqual((e["0002_idx"]["indices"], e["0002_idx"]["columnas"]), (set(), set()))
        self.assertEqual(A.reemplazadas(e, ["0001_t", "0002_idx"]), ["0001_t", "0002_idx"])

    def test_sin_nada_verificable_bloquea_salvo_que_se_nombre(self):
        ms, e = self._cadena(("0001_t", "create table ops.t (id int);"),
                             ("0002_grants", "grant select on ops.t to service_role;\n"
                                             "comment on table ops.t is 'x';\ninsert into ops.t values (1);"))
        self.assertEqual(A.faltantes_en_base(e, A.Catalogo(relaciones={("ops", "t")}), ["0002_grants"]),
                         {"0002_grants": [A.SIN_HUELLA]})
        p = self._plan_adopcion(ms, A.Catalogo(relaciones={("ops", "t")}))
        self.assertIn("0002_grants", p.bloqueo)
        p = self._plan_adopcion(ms, A.Catalogo(relaciones={("ops", "t")}), sin_huella_ok=["0002_grants"])
        self.assertIsNone(p.bloqueo)

    def test_esquemas(self):
        ms, e = self._cadena(("0001_s", "create schema if not exists ventas;\ncreate schema viejo;"),
                             ("0002_r", "alter schema viejo rename to nuevo;"))
        self.assertEqual(e["0001_s"]["esquemas"], {"ventas", "nuevo"})
        self.assertEqual(A.faltantes_en_base(e, A.Catalogo(esquemas={"ventas"}), ["0001_s"]),
                         {"0001_s": ["esquema nuevo"]})

    def test_el_esquema_lo_responde_quien_lo_creo(self):
        """La 0068-0070 repiten `create schema if not exists almacen`: no le quitan
        la huella a la 0066 (antes «ganaba la última» y la 0066 quedaba vacía)."""
        ms, e = self._cadena(("0001_crea", "create schema if not exists almacen;"),
                             ("0002_repite", "create schema if not exists almacen;\ncreate table almacen.t (id int);"),
                             ("0003_tira", "drop schema if exists viejo cascade;"),
                             ("0004_viejo", "create schema viejo;"))
        self.assertEqual(e["0001_crea"]["esquemas"], {"almacen"})
        self.assertEqual(e["0002_repite"]["esquemas"], set())
        self.assertEqual(e["0004_viejo"]["esquemas"], {"viejo"})
        ms, e = self._cadena(("0001_a", "create schema x;"), ("0002_b", "drop schema x;"),
                             ("0003_c", "create schema if not exists x;"))
        self.assertEqual((e["0001_a"]["esquemas"], e["0003_c"]["esquemas"]), (set(), {"x"}))

    def test_cadena_real_0068_a_0070(self):
        """La mudanza de Brandon: las tablas de la 0064/0065 viven en ventas/almacen,
        en ops quedan 9 vistas puente con security_invoker (de la 0068), y la 0070
        deja almacen.historial_movimientos y le quita archivado_odoo a locations."""
        e = A.huellas_esperadas(A.listar_migraciones(MIGRACIONES))
        puentes = {("ops", t) for t in ("almacenes", "almacenes_hist", "stock_almacen", "stock_mov", "ov_folio",
                                        "ov_ordenes", "ov_lineas", "ov_mensajes", "ov_archivos")}
        self.assertEqual(e["0068_mudanza_ov_a_ventas_y_almacen"]["relaciones"], puentes)
        self.assertEqual(e["0068_mudanza_ov_a_ventas_y_almacen"]["invoker"], puentes)
        # La 0071 (editar una confirmada) vuelve a definir dos de las 14 guardias que
        # recreó la 0068, ya con su puerta: la huella de una función es de la ÚLTIMA
        # que la define, así que a la 0068 le quedan 12 y esas dos son de la 0071.
        self.assertEqual(len(e["0068_mudanza_ov_a_ventas_y_almacen"]["funciones"]), 12)
        self.assertEqual(set(e["0071_ov_editar_confirmada"]["funciones"]),
                         {("ops", "tg_ov_ordenes_guarda", 0), ("ops", "tg_ov_lineas_guarda", 0)})
        for cuerpo in e["0071_ov_editar_confirmada"]["funciones"].values():
            self.assertIn("app.ov_edicion", cuerpo)
        self.assertTrue({("ventas", "ov_ordenes"), ("ventas", "ov_folio"), ("almacen", "almacenes"),
                         ("almacen", "almacenes_hist")} <= e["0064_ops_ordenes_venta"]["relaciones"])
        self.assertTrue({("almacen", "stock_almacen"), ("almacen", "stock_mov")}
                        <= e["0065_ops_inventario_kubera"]["relaciones"])
        self.assertEqual(e["0066_esquemas_ventas_almacen"]["esquemas"], {"ventas", "almacen"})
        self.assertEqual(e["0069_almacen_locations"]["relaciones"], {("almacen", "locations")})
        self.assertEqual(e["0070_almacen_historial_movimientos"]["relaciones"],
                         {("almacen", "historial_movimientos")})
        self.assertIn(("col", "almacen", "locations", "archivado_odoo"),
                      e["0070_almacen_historial_movimientos"]["ausentes"])
        self.assertIn("ventas", A.esquemas_de(e))
        self.assertIn("almacen", A.esquemas_de(e))

    def test_cadena_real_la_bitacora_es_de_la_0036_y_la_vista_ya_no_esta(self):
        e = A.huellas_esperadas(A.listar_migraciones(MIGRACIONES))
        self.assertIn(("ops", "odoo_sale_orders"), e["0036_ops_odoo_bitacora"]["relaciones"])
        self.assertIn(("ops", "odoo_sale_order_items"), e["0036_ops_odoo_bitacora"]["relaciones"])
        self.assertIn(("ops", "automatizacion_flags"), e["0035_ops_automatizacion_flags"]["relaciones"])
        self.assertIn(("costing", "caja_compartida"), e["0034_costing_caja_compartida"]["relaciones"])
        self.assertNotIn(("ops", "devoluciones_vs_canal_v"), e["0065_ops_inventario_kubera"]["relaciones"])
        self.assertIn(("ops", "channel_submissions", "actor"), e["0046_actor_channel_submissions"]["columnas"])

    def test_cadena_real_lo_que_el_sandbox_no_tenia_el_8_oct(self):
        """Las cuatro que la sonda vieja dejaba pasar con la huella vacía."""
        e = A.huellas_esperadas(A.listar_migraciones(MIGRACIONES))
        self.assertEqual(e["0043_blindaje_enrich"]["rls"], {("enrich", "market_highlights")})
        self.assertEqual(e["0043_blindaje_enrich"]["invoker"], {("enrich", "market_categoria_prioridad_v")})
        self.assertEqual(e["0044_blindaje_market_highlights_hist"]["rls"], {("enrich", "market_highlights_hist")})
        self.assertEqual(e["0045_reblindaje_market_publicaciones_v"]["invoker"], {("enrich", "market_publicaciones_v")})
        self.assertEqual(set(e["0050_retencion_webhooks_por_canal"]["funciones"]), {("ops", "purgar_webhook_events", 3)})
        self.assertEqual(e["0050_retencion_webhooks_por_canal"]["ausentes"], {("fn", "ops", "purgar_webhook_events", 2)})
        self.assertIn(("enrich", "market_skus_v"), e["0025_blindaje_rls"]["invoker"])
        self.assertEqual(e["0066_esquemas_ventas_almacen"]["esquemas"], {"ventas", "almacen"})
        self.assertEqual(e["0067_ops_quitar_devoluciones_vs_canal_v"]["ausentes"],
                         {("rel", "ops", "devoluciones_vs_canal_v")})
        # La única sin nada verificable: la 0014 (un rename condicional dentro de un DO).
        self.assertEqual([n for n, x in e.items() if not A._tiene_huella(x) and not x["sentencias"]],
                         ["0014_retiro_propuestas"])

    def test_cadena_real_requisitos_externos_de_recrear(self):
        req = A.requisitos_externos(A.listar_migraciones(MIGRACIONES))
        self.assertEqual(set(req.values()), {"0025_blindaje_rls"})
        self.assertEqual(set(req), {("propuestas_retirado", t) for t in (
            "competencia_busquedas", "competencia_rankings_categoria", "competencia_terminos_categoria",
            "competencia_publicaciones_v", "competencia_skus_v")} | {("public", "packing_lists"),
                                                                     ("public", "packing_list_items")})


# ═══════════════════════════════════════════════════════════════════════════
# Ejecutar y adoptar contra la base falsa
# ═══════════════════════════════════════════════════════════════════════════
class EjecutarTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.c = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def _plan(self, *ms):
        return A.Plan(registro_existe=False, correr=list(ms))

    def test_base_nueva_registro_tardio_y_sin_duplicar_la_propia(self):
        ms = [_escribir(self.c, "0001_a", "create table ops.a (id int);"),
              _escribir(self.c, "0002_b", "begin;\ncreate table ops.b (id int);\ncommit;\n"),
              _escribir(self.c, "0064_ops_ordenes_venta",
                        "begin;\n-- @CREA_REGISTRO\n-- @SE_REGISTRA:0064_ops_ordenes_venta\ncommit;\n"),
              _escribir(self.c, "0065_c", "create table ops.c (id int);"),
              _escribir(self.c, "0066_d", "begin;\n-- @SE_REGISTRA:0066_d\ncommit;\n")]
        b = BaseFalsa()
        hechas = A.ejecutar(b, self._plan(*ms), eco=_silencio)
        self.assertEqual(hechas, [m.nombre for m in ms])
        self.assertEqual(b.commits, 5)                      # una transacción por archivo
        filas = [(f["migracion"], f["detalle"].get("modo", "propia")) for f in b.filas]
        self.assertEqual(filas, [("0064_ops_ordenes_venta", "propia"),
                                 ("0001_a", "registro_tardio"), ("0002_b", "registro_tardio"),
                                 ("0065_c", "ejecutada"), ("0066_d", "propia")])
        tardia = next(f for f in b.filas if f["migracion"] == "0001_a")["detalle"]
        self.assertEqual(tardia["registrada_con"], "0064_ops_ordenes_venta")
        self.assertEqual(tardia["por"], "aplicar_migraciones.py")
        self.assertRegex(tardia["sha256"], r"^[0-9a-f]{64}$")
        # El begin/commit del archivo no llegó a la base: el runner pone la transacción.
        enviados = [s for s in b.sentencias if "create table ops.b" in s]
        self.assertNotRegex(enviados[0], r"(?im)^\s*(begin|commit)\s*;")

    def test_cada_archivo_con_su_fila_en_la_misma_transaccion(self):
        b = BaseFalsa(tabla=True, filas=[{"migracion": "0064_x", "detalle": {}}])
        m = _escribir(self.c, "0066_y", "create schema if not exists ventas;")
        A.ejecutar(b, self._plan(m), eco=_silencio)
        self.assertEqual(b.commits, 1)
        self.assertEqual([f["migracion"] for f in b.filas], ["0064_x", "0066_y"])
        self.assertEqual(b.filas[-1]["detalle"]["modo"], "ejecutada")

    def test_si_truena_a_la_mitad_se_deshace_ese_archivo_y_se_detiene(self):
        b = BaseFalsa(tabla=True)
        ms = [_escribir(self.c, "0066_ok", "select 1;"),
              _escribir(self.c, "0067_mal", "begin;\n-- @SE_REGISTRA:0067_mal\n@TRUENA;\ncommit;\n"),
              _escribir(self.c, "0068_nunca", "select 3;")]
        with self.assertRaises(A.FalloMigracion) as cm:
            A.ejecutar(b, self._plan(*ms), eco=_silencio)
        self.assertEqual(cm.exception.migracion.nombre, "0067_mal")
        self.assertEqual(cm.exception.hechas, ["0066_ok"])
        self.assertEqual([f["migracion"] for f in b.filas], ["0066_ok"])     # sin fila de la que falló
        self.assertEqual((b.commits, b.rollbacks), (1, 1))
        self.assertFalse(any("select 3" in s for s in b.sentencias))       # la siguiente no corrió

    def test_si_falla_antes_de_la_0064_no_queda_registro(self):
        b = BaseFalsa()
        ms = [_escribir(self.c, "0001_a", "select 1;"), _escribir(self.c, "0002_b", "@TRUENA;")]
        with self.assertRaises(A.FalloMigracion) as cm:
            A.ejecutar(b, self._plan(*ms), eco=_silencio)
        self.assertTrue(cm.exception.sin_registro)
        self.assertFalse(b.tabla)
        self.assertEqual(b.filas, [])

    def test_si_falla_despues_de_la_0064_si_hay_registro(self):
        b = BaseFalsa()
        ms = [_escribir(self.c, "0001_a", "select 1;"),
              _escribir(self.c, "0064_x", "-- @CREA_REGISTRO\n-- @SE_REGISTRA:0064_x"),
              _escribir(self.c, "0065_y", "@TRUENA;")]
        with self.assertRaises(A.FalloMigracion) as cm:
            A.ejecutar(b, self._plan(*ms), eco=_silencio)
        self.assertFalse(cm.exception.sin_registro)
        self.assertEqual([f["migracion"] for f in b.filas], ["0064_x", "0001_a"])

    def test_una_fila_con_otro_nombre_deshace_el_archivo(self):
        """Renumerado de 0069 a 0068 sin cambiar su insert: la fila fantasma
        movería la frontera y no se podría borrar."""
        b = BaseFalsa(tabla=True, filas=[{"migracion": "0067_x", "detalle": {}}])
        ms = [_escribir(self.c, "0068_x", "begin;\n-- @SE_REGISTRA:0069_x\ncommit;\n"),
              _escribir(self.c, "0070_nunca", "select 'no';")]
        with self.assertRaises(A.FalloMigracion) as cm:
            A.ejecutar(b, self._plan(*ms), eco=_silencio)
        self.assertIn("0069_x", str(cm.exception.exc))
        self.assertEqual([f["migracion"] for f in b.filas], ["0067_x"])
        self.assertEqual((b.commits, b.rollbacks), (0, 1))

    def test_las_reales_0068_a_0070_se_registran_solas_sin_duplicar(self):
        """Las de Brandon traen su propio insert (la 0068 dentro de un DO, sin
        guarda; la 0069 y la 0070 con `where not exists`): una fila cada una, la
        suya, y el runner no agrega otra."""
        reales = {m.nombre: m for m in A.listar_migraciones(MIGRACIONES)}
        ms = [reales[n] for n in ("0068_mudanza_ov_a_ventas_y_almacen", "0069_almacen_locations",
                                  "0070_almacen_historial_movimientos")]
        b = BaseFalsa(tabla=True, filas=[{"migracion": "0067_ops_quitar_devoluciones_vs_canal_v", "detalle": {}}])
        ecos: list[str] = []
        hechas = A.ejecutar(b, A.Plan(registro_existe=True, correr=ms), eco=ecos.append)
        self.assertEqual(hechas, [m.nombre for m in ms])
        self.assertEqual([(f["migracion"], f["detalle"]) for f in b.filas[1:]],
                         [(m.nombre, {"propia": True}) for m in ms])
        self.assertEqual(b.commits, 3)
        self.assertEqual(sum("la puso el propio archivo" in x for x in ecos), 3)

    def test_dos_filas_propias_en_una_corrida_se_deshace(self):
        """ops.migraciones no tiene único sobre el nombre: el runner es el que no duplica."""
        b = BaseFalsa(tabla=True, filas=[{"migracion": "0070_x", "detalle": {}}])
        m = _escribir(self.c, "0071_x", "begin;\n-- @SE_REGISTRA:0071_x\n-- @SE_REGISTRA:0071_x\ncommit;\n")
        with self.assertRaises(A.FalloMigracion) as cm:
            A.ejecutar(b, self._plan(m), eco=_silencio)
        self.assertIn("2 filas", str(cm.exception.exc))
        self.assertEqual([f["migracion"] for f in b.filas], ["0070_x"])
        self.assertEqual((b.commits, b.rollbacks), (0, 1))

    def test_insert_con_otro_nombre_se_rechaza_sin_mandarlo(self):
        """La cuarentena, renumerada 0070 → 0072, con su insert todavía en 0070:
        se rechaza ANTES de mandar el texto a la base."""
        b = BaseFalsa(tabla=True, filas=[{"migracion": "0071_x", "detalle": {}}])
        m = _escribir(self.c, "0072_ops_stock_watch_cuarentena",
                      "begin;\nalter table ops.t add column c int;\n"
                      "insert into ops.migraciones (migracion, detalle)\n"
                      "select '0070_ops_stock_watch_cuarentena', '{}'::jsonb\n"
                      " where not exists (select 1 from ops.migraciones where migracion = '0070_ops_stock_watch_cuarentena');\n"
                      "commit;\n")
        with self.assertRaises(A.FalloMigracion) as cm:
            A.ejecutar(b, self._plan(m), eco=_silencio)
        self.assertIn("0070_ops_stock_watch_cuarentena", str(cm.exception.exc))
        self.assertFalse(any("alter table" in s for s in b.sentencias))
        self.assertEqual(len(b.filas), 1)

    def test_otra_corrida_ya_la_registro(self):
        b = BaseFalsa(tabla=True, filas=[{"migracion": "0066_y", "detalle": {}}])
        m = _escribir(self.c, "0066_y", "select 'no debe correr';")
        hechas = A.ejecutar(b, self._plan(m), eco=_silencio)
        self.assertEqual(hechas, [])
        self.assertFalse(any("no debe correr" in s for s in b.sentencias))
        self.assertEqual(len(b.filas), 1)

    def test_los_avisos_del_archivo_se_imprimen(self):
        b = BaseFalsa(tabla=True)
        dichos = []
        A.ejecutar(b, self._plan(_escribir(self.c, "0066_y", "-- @AVISA")), eco=dichos.append)
        self.assertTrue(any("aviso de prueba" in d for d in dichos))
        self.assertEqual(b.notices, [])

    def test_adoptar_no_ejecuta_y_va_en_una_transaccion(self):
        b = BaseFalsa(tabla=True, filas=[{"migracion": "0064_x", "detalle": {}},
                                         {"migracion": "0002_b", "detalle": {}}])
        ms = [_escribir(self.c, "0001_a", "@TRUENA si se ejecuta"), _escribir(self.c, "0002_b", "@TRUENA"),
              _escribir(self.c, "0003_c", "@TRUENA")]
        p = A.Plan(registro_existe=True, adoptar=ms, adoptar_hasta=63, sin_huella_ok=["0003_c"])
        n = A.adoptar(b, p, eco=_silencio)
        self.assertEqual(n, 2)                              # la 0002 ya tenía fila
        self.assertEqual(b.commits, 1)
        nuevas = b.filas[2:]
        self.assertEqual([f["migracion"] for f in nuevas], ["0001_a", "0003_c"])
        self.assertTrue(all(f["detalle"]["modo"] == "adoptada" and f["detalle"]["hasta"] == "0063" for f in nuevas))
        self.assertTrue(nuevas[1]["detalle"]["sin_huella_ok"])
        self.assertFalse(any("@TRUENA" in s for s in b.sentencias))


# ═══════════════════════════════════════════════════════════════════════════
# main: --plan no escribe; el candado; el entorno
# ═══════════════════════════════════════════════════════════════════════════
class MainTest(unittest.TestCase):
    URL = "postgresql://postgres.yvootpbzprueba:x@aws-0-us-east-1.pooler.supabase.com:6543/postgres"

    def _correr(self, argv, base, url=None):
        salida = io.StringIO()
        with mock.patch.object(A, "cargar_env", return_value={"SUPABASE_DB_URL": url or self.URL}), \
             mock.patch.object(A, "conectar", return_value=base) as conectar, \
             mock.patch.object(A, "_watchdog"), mock.patch.object(A.socket, "setdefaulttimeout"), \
             mock.patch.object(A, "paridad", return_value=0) as paridad, \
             redirect_stdout(salida):
            try:
                rc = A.main(argv)
            except SystemExit as exc:
                rc = exc
        self.conectar = conectar
        return rc, salida.getvalue(), paridad

    @staticmethod
    def _catalogo_completo(esperadas) -> dict:
        """Un catálogo falso que tiene TODO lo que la cadena real espera."""
        cat: dict[str, set] = {k: set() for k in ("relaciones", "columnas", "funciones", "indices", "triggers",
                                                  "restricciones", "politicas", "esquemas")}
        rls, invoker, rels = set(), set(), set()
        for e in esperadas.values():
            rels |= e["relaciones"] | e["rls"] | e["invoker"]
            rels |= {(s, t) for s, t, _ in e["columnas"] | e["indices"] | e["triggers"] | e["restricciones"]
                     | e["politicas"]}
            rls |= e["rls"]
            invoker |= e["invoker"]
            cat["columnas"] |= e["columnas"]
            cat["funciones"] |= {(s, f, n, cuerpo or "") for (s, f, n), cuerpo in e["funciones"].items()}
            for clase in ("indices", "triggers", "restricciones", "politicas"):
                cat[clase] |= e[clase]
            cat["esquemas"] |= {(s,) for s in e["esquemas"]}
        cat["relaciones"] = {(s, t, (s, t) in rls, "security_invoker=on" if (s, t) in invoker else "")
                             for s, t in rels}
        return {k: sorted(v) for k, v in cat.items()}

    def test_plan_de_adopcion_lee_el_catalogo_en_read_only(self):
        nombres = [m.nombre for m in A.listar_migraciones(MIGRACIONES)]
        hoy = [{"migracion": "0064_ops_ordenes_venta", "detalle": {}},
               {"migracion": "0065_ops_inventario_kubera", "detalle": {}}]
        # Catálogo vacío: todo falta → BLOQUEADO, sin escribir.
        b = BaseFalsa(tabla=True, filas=hoy)
        rc, out, paridad = self._correr(["--plan", "--adoptar-hasta", "0063"], b)
        self.assertEqual(rc, 1)
        self.assertIn("BLOQUEADO", out)
        self.assertIn("SIN HUELLA: 0051_modo_publicacion — channel.publication_mode", out)
        self.assertEqual(b.sentencias[0], "set transaction read only")
        self.assertTrue(any(s.startswith("-- catalogo:relaciones") for s in b.sentencias))
        self.assertEqual((b.commits, len(b._filas_tx)), (0, 0))
        paridad.assert_not_called()
        # Catálogo con todo: solo la 0014 (nada verificable) pide nombrarse.
        cat = self._catalogo_completo(A.huellas_esperadas(A.listar_migraciones(MIGRACIONES)))
        b = BaseFalsa(tabla=True, filas=hoy, catalogo=cat)
        rc, out, _ = self._correr(["--plan", "--adoptar-hasta", "0063"], b)
        self.assertEqual(rc, 1)
        self.assertIn("BLOQUEADO: 1 migración(es)", out)
        self.assertIn("0014_retiro_propuestas", out)
        b = BaseFalsa(tabla=True, filas=hoy, catalogo=cat)
        rc, out, _ = self._correr(["--plan", "--adoptar-hasta", "0063", "--sin-huella-ok", "0014_retiro_propuestas"], b)
        self.assertEqual(rc, 0)
        self.assertIn(f"ADOPTA sin ejecutar ({sum(1 for n in nombres if n < '0064')}, <= 0063)", out)
        self.assertEqual(b.commits, 0)

    def test_adoptar_por_main_escribe_en_una_transaccion(self):
        cat = self._catalogo_completo(A.huellas_esperadas(A.listar_migraciones(MIGRACIONES)))
        b = BaseFalsa(tabla=True, catalogo=cat, filas=[{"migracion": "0064_ops_ordenes_venta", "detalle": {}},
                                                       {"migracion": "0065_ops_inventario_kubera", "detalle": {}}])
        rc, out, paridad = self._correr(["--adoptar-hasta", "0063", "--excepto", "0046_actor_channel_submissions",
                                         "--sin-huella-ok", "0014_retiro_propuestas"], b)
        self.assertEqual(rc, 0)
        self.assertEqual(b.commits, 1)
        modos = {f["detalle"].get("modo") for f in b.filas[2:]}
        self.assertEqual(modos, {"adoptada"})
        self.assertNotIn("0046_actor_channel_submissions", [f["migracion"] for f in b.filas])
        paridad.assert_called_once()

    def test_recrear_tira_tambien_ventas_y_almacen_y_registra_tarde(self):
        req = A.requisitos_externos(A.listar_migraciones(MIGRACIONES))
        b = BaseFalsa(existentes={f"{s}.{t}" for s, t in req})
        rc, out, paridad = self._correr(["--recrear"], b)
        self.assertEqual(rc, 0, out)
        for esq in A.ESQUEMAS_PROPIOS:
            self.assertIn(f"drop schema if exists {esq} cascade", b.sentencias)
        self.assertIn("drop schema if exists ventas cascade", b.sentencias)
        self.assertIn("drop schema if exists almacen cascade", b.sentencias)
        nombres = [m.nombre for m in A.listar_migraciones(MIGRACIONES)]
        tardias = [f["migracion"] for f in b.filas if f["detalle"].get("modo") == "registro_tardio"]
        self.assertEqual(tardias, [n for n in nombres if n < "0064"])
        self.assertEqual(sorted(f["migracion"] for f in b.filas), sorted(nombres))   # una fila cada una
        paridad.assert_called_once()

    def test_recrear_sin_requisitos_no_tira_nada(self):
        for argv in (["--recrear"], ["--plan", "--recrear"]):
            b = BaseFalsa(existentes={"public.packing_lists"})
            with self.subTest(argv=argv):
                rc, out, paridad = self._correr(argv, b)
                self.assertEqual(rc, 1)
                self.assertIn("BLOQUEADO: --recrear tronaría", out)
                self.assertIn("propuestas_retirado.competencia_busquedas (0025_blindaje_rls)", out)
                self.assertNotIn("public.packing_lists (", out)
                self.assertFalse(any(s.startswith("drop schema") for s in b.sentencias))
                self.assertEqual(b.commits, 0)
                self.assertEqual(b.sentencias[0], "set transaction read only")
                paridad.assert_not_called()

    def test_sin_registro_y_con_tablas_se_detiene(self):
        b = BaseFalsa(tablas_propias=12)
        rc, out, paridad = self._correr([], b)
        self.assertEqual(rc, 1)
        self.assertIn("no es una base nueva", out)
        self.assertIn("BLOQUEADO", out)
        self.assertEqual(b.commits, 0)
        b = BaseFalsa(tablas_propias=0)
        rc, out, _ = self._correr(["--plan"], b)
        self.assertEqual(rc, 0)
        self.assertIn("base nueva, corre todo", out)

    def test_fallo_sin_registro_lo_dice(self):
        b = BaseFalsa(tablas_propias=0)
        real = A.listar_migraciones(MIGRACIONES)
        with tempfile.TemporaryDirectory() as d:
            ms = [_escribir(Path(d), "0001_a", "select 1;"), _escribir(Path(d), "0002_b", "@TRUENA;")]
            with mock.patch.object(A, "listar_migraciones", return_value=ms):
                rc, out, _ = self._correr([], b)
        self.assertIsInstance(rc, SystemExit)
        self.assertIn("NO tiene fila", str(rc.code))
        self.assertIn("--recrear", str(rc.code))
        self.assertTrue(real)

    def test_con_la_url_de_produccion_sale_antes_de_conectar(self):
        b = BaseFalsa()
        for argv in (["--plan"], ["--recrear"], ["--verificar-solo"], ["--adoptar-hasta", "0063"]):
            with self.subTest(argv=argv):
                rc, _, _ = self._correr(argv, b, url="postgresql://postgres.tukwcvsiabcd:x@h:6543/postgres")
                self.assertIsInstance(rc, SystemExit)
                self.assertIn("PRODUCCIÓN", str(rc.code))
                self.conectar.assert_not_called()
        self.assertEqual(b.sentencias, [])

    def test_plan_lee_en_read_only_y_no_escribe(self):
        b = BaseFalsa(tabla=True, filas=[{"migracion": n, "detalle": {}}
                                         for n in [m.nombre for m in A.listar_migraciones(MIGRACIONES)]
                                         if n < "0066"])
        rc, out, paridad = self._correr(["--plan"], b)
        self.assertEqual(rc, 0)
        self.assertEqual(b.sentencias[0], "set transaction read only")
        self.assertEqual((b.commits, len(b._filas_tx)), (0, 0))
        self.assertGreaterEqual(b.rollbacks, 1)
        self.assertIn("CORRE    0066_esquemas_ventas_almacen", out)
        self.assertIn("CORRE    0067_ops_quitar_devoluciones_vs_canal_v", out)
        paridad.assert_not_called()

    def test_plan_bloqueado_sale_con_1(self):
        b = BaseFalsa(tabla=True, filas=[{"migracion": "0064_ops_ordenes_venta", "detalle": {}},
                                         {"migracion": "0065_ops_inventario_kubera", "detalle": {}}])
        rc, out, _ = self._correr(["--plan"], b)
        self.assertEqual(rc, 1)
        self.assertIn("BLOQUEADO", out)
        self.assertEqual(b.commits, 0)

    def test_aplicar_corre_lo_nuevo_y_verifica(self):
        b = BaseFalsa(tabla=True, filas=[{"migracion": n, "detalle": {}}
                                         for n in [m.nombre for m in A.listar_migraciones(MIGRACIONES)]
                                         if n < "0068"])
        # Las reales 0068-0071 se registran solas (la base falsa entiende sus tres
        # formas de insert; la 0071 usa `values`, como la 0064): una fila cada una, la suya.
        rc, out, paridad = self._correr([], b)
        self.assertEqual(rc, 0)
        nuevas = ["0068_mudanza_ov_a_ventas_y_almacen", "0069_almacen_locations",
                  "0070_almacen_historial_movimientos", "0071_ov_editar_confirmada"]
        self.assertEqual([f["migracion"] for f in b.filas[-4:]], nuevas)
        self.assertEqual([f["detalle"] for f in b.filas[-4:]], [{"propia": True}] * 4)
        for n in nuevas:
            self.assertEqual(sum(1 for f in b.filas if f["migracion"] == n), 1, n)
        self.assertEqual(b.commits, 4)
        paridad.assert_called_once()

    def _sandbox_del_9_oct(self) -> BaseFalsa:
        return BaseFalsa(tabla=True, filas=[{"migracion": n, "detalle": {}}
                                            for n in [m.nombre for m in A.listar_migraciones(MIGRACIONES)]
                                            if n < "0068"])

    def test_plan_hasta_0069(self):
        b = self._sandbox_del_9_oct()
        rc, out, paridad = self._correr(["--plan", "--hasta", "0069"], b)
        self.assertEqual(rc, 0)
        self.assertIn("CORRE    0068_mudanza_ov_a_ventas_y_almacen  (se registra sola)", out)
        self.assertIn("CORRE    0069_almacen_locations  (se registra sola)", out)
        self.assertIn("NO CORRE (--hasta 0069): 0070_almacen_historial_movimientos", out)
        self.assertNotIn("CORRE    0070", out)
        self.assertEqual((b.commits, len(b._filas_tx)), (0, 0))
        self.assertEqual(b.sentencias[0], "set transaction read only")
        paridad.assert_not_called()

    def test_aplicar_hasta_0069_deja_la_0070_pendiente(self):
        b = self._sandbox_del_9_oct()
        rc, out, _ = self._correr(["--hasta", "0069"], b)
        self.assertEqual(rc, 0)
        self.assertEqual([f["migracion"] for f in b.filas[-2:]],
                         ["0068_mudanza_ov_a_ventas_y_almacen", "0069_almacen_locations"])
        self.assertFalse(any(f["migracion"].startswith("0070") for f in b.filas))
        self.assertFalse(any("historial_movimientos" in s for s in b.sentencias))
        self.assertEqual(b.commits, 2)
        # La siguiente corrida (sin --hasta) solo tiene la 0070.
        rc, out, _ = self._correr(["--plan"], b)
        self.assertIn("CORRE    0070_almacen_historial_movimientos", out)
        self.assertNotIn("CORRE    0068", out)

    def test_plan_con_un_insert_renumerado_aborta_sin_escribir(self):
        with tempfile.TemporaryDirectory() as d:
            ms = [_escribir(Path(d), "0071_limpieza_fase1",
                            "begin;\ninsert into ops.migraciones (migracion, detalle)\n"
                            "values ('0070_limpieza_fase1', null);\ncommit;\n")]
            b = BaseFalsa(tabla=True, filas=[{"migracion": "0070_x", "detalle": {}}])
            with mock.patch.object(A, "listar_migraciones", return_value=ms):
                rc, out, paridad = self._correr(["--plan"], b)
        self.assertIsInstance(rc, SystemExit)
        self.assertIn("0070_limpieza_fase1", str(rc.code))
        self.assertEqual((b.commits, len(b._filas_tx)), (0, 0))
        self.assertFalse(any("values ('0070_limpieza" in s for s in b.sentencias))
        paridad.assert_not_called()

    def test_candado_de_produccion(self):
        for env in ({"SUPABASE_DB_URL": "postgresql://postgres.tukwcvsiabc:x@h:6543/postgres"},
                    {"SUPABASE_DB_URL": "postgresql://postgres.otraref:x@h:6543/postgres",
                     "SUPABASE_PROD_REF": "otraref"},
                    {"SUPABASE_DB_URL": "no es un dsn"}):
            with self.subTest(env=env), self.assertRaises(SystemExit):
                A.ref_sandbox(env)
        self.assertEqual(A.ref_sandbox({"SUPABASE_DB_URL": self.URL}), "yvootpbzprueba")

    def test_de_env_staging_solo_se_leen_dos_llaves(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "env.staging"
            p.write_text("# comentario\nMYSQL_PASSWORD=secreto\nSUPABASE_DB_URL=\"postgresql://postgres.yvoo:x@h/p\"\n"
                         "WOO_SECRET=otro\nSUPABASE_PROD_REF=tukwcvsi # prod\n", encoding="utf-8")
            with mock.patch.dict(A.os.environ, {"WOO_KEY": "del entorno"}, clear=False):
                vals = A.cargar_env(p)
        self.assertEqual(set(vals) - {"SUPABASE_DB_URL", "SUPABASE_PROD_REF"}, set())
        self.assertEqual(vals["SUPABASE_DB_URL"], "postgresql://postgres.yvoo:x@h/p")
        self.assertEqual(vals["SUPABASE_PROD_REF"], "tukwcvsi")

    def test_argumentos_incoherentes(self):
        for argv in (["--excepto", "0046_x"], ["--verificar-solo", "--plan"],
                     ["--adoptar-hasta", "0063", "--recrear"], ["--adoptar-hasta", "63a"],
                     ["--adoptar-hasta", "0063", "--incluir-atrasadas"], ["--hasta", "0069", "--recrear"],
                     ["--hasta", "0069", "--adoptar-hasta", "0063"], ["--verificar-solo", "--hasta", "0069"],
                     ["--hasta", "69x"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), \
                 redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                A.argumentos(argv)


# ═══════════════════════════════════════════════════════════════════════════
# Fase 0: los esquemas nuevos en todas partes, y las migraciones nuevas
# ═══════════════════════════════════════════════════════════════════════════
class Fase0Test(unittest.TestCase):
    def test_esquemas_en_las_listas(self):
        for esq in ("ventas", "almacen"):
            self.assertIn(esq, A.ESQUEMAS_PROPIOS)
            self.assertIn(esq, verificar_rls.ESQUEMAS_NEGOCIO)
        flujo = (BACKEND / "routers" / "flujo.py").read_text(encoding="utf-8")
        lista = re.search(r"^ESQUEMAS = \((.*?)\)", flujo, re.S | re.M).group(1)
        self.assertIn('"ventas"', lista)
        self.assertIn('"almacen"', lista)

    def test_manifiesto(self):
        crudo = (RAIZ / "supabase" / "schema_manifest.json").read_text(encoding="utf-8")
        man = json.loads(crudo)
        # El formato de la casa: indent=1, ascii, llaves ordenadas, sin salto final.
        self.assertEqual(json.dumps(man, indent=1, ensure_ascii=True, sort_keys=True), crudo.replace("\r\n", "\n"))
        esq = man["esquemas"]
        self.assertNotIn("devoluciones_vs_canal_v", esq["ops"])
        self.assertEqual(man["resumen"], {k: len(v) for k, v in esq.items()})
        # La 0068 (en producción desde el 9-oct): las tablas viven en ventas/almacen y
        # en ops queda la VISTA puente del mismo nombre (`select *`, security_invoker):
        # mismas columnas en el mismo orden, todas «null» (una vista no guarda
        # NOT NULL), sin constraints ni RLS propios. Sus triggers son de la tabla.
        mudadas = {"ov_folio": "ventas", "ov_ordenes": "ventas", "ov_lineas": "ventas", "ov_mensajes": "ventas",
                   "ov_archivos": "ventas", "almacenes": "almacen", "almacenes_hist": "almacen",
                   "stock_almacen": "almacen", "stock_mov": "almacen"}
        self.assertEqual(set(esq["ventas"]), {t for t, s in mudadas.items() if s == "ventas"})
        for t, s in mudadas.items():
            with self.subTest(t=t):
                tabla, puente = esq[s][t], esq["ops"][t]
                self.assertTrue(tabla["rls"])
                self.assertEqual(puente, {"columnas": [c.rsplit(":", 1)[0] + ":null" for c in tabla["columnas"]],
                                          "constraints": {}, "rls": False})
                self.assertIn(f"{s}.{t}", man["triggers"])
                self.assertNotIn(f"ops.{t}", man["triggers"])
        # La 0069 y la 0070: locations sin archivado_odoo (la quitó la 0070) y el historial.
        self.assertTrue({"locations", "historial_movimientos"} <= set(esq["almacen"]))
        self.assertFalse(any(c.startswith("archivado_odoo:") for c in esq["almacen"]["locations"]["columnas"]))
        self.assertTrue(esq["almacen"]["historial_movimientos"]["rls"])

    def test_0066_y_0067_se_registran_solas_con_el_nombre_de_su_archivo(self):
        for nombre in ("0066_esquemas_ventas_almacen", "0067_ops_quitar_devoluciones_vs_canal_v",
                       "0064_ops_ordenes_venta", "0065_ops_inventario_kubera"):
            texto = (MIGRACIONES / f"{nombre}.sql").read_text(encoding="utf-8")
            with self.subTest(nombre=nombre):
                self.assertIn(f"insert into ops.migraciones (migracion, detalle)\nvalues ('{nombre}'",
                              texto.replace("\r\n", "\n"))

    def test_0066_permisos_como_ops(self):
        t = verificar_rls._sin_comentarios(
            (MIGRACIONES / "0066_esquemas_ventas_almacen.sql").read_text(encoding="utf-8")).lower()
        t = " ".join(t.split())
        for frase in ("create schema if not exists ventas", "create schema if not exists almacen",
                      "grant usage on schema ventas, almacen to service_role",
                      "alter default privileges for role postgres in schema ventas, almacen grant all on tables to service_role",
                      "alter default privileges for role postgres in schema ventas, almacen grant all on sequences to service_role",
                      "comment on schema ventas", "comment on schema almacen"):
            self.assertIn(frase, t)
        for prohibido in ("anon", "authenticated", "drop ", "set schema", "cascade"):
            self.assertNotIn(prohibido, t)

    def test_0067_solo_quita_la_vista(self):
        t = " ".join(verificar_rls._sin_comentarios(
            (MIGRACIONES / "0067_ops_quitar_devoluciones_vs_canal_v.sql").read_text(encoding="utf-8")).lower().split())
        self.assertIn("drop view if exists ops.devoluciones_vs_canal_v;", t)
        self.assertNotIn("cascade", t)
        self.assertEqual(t.count("drop "), 1)

    def test_blindaje_estatico_en_verde_y_la_vista_fuera(self):
        modelo = verificar_rls.Esquema()
        for f in sorted(MIGRACIONES.glob("*.sql")):
            modelo.aplicar(f.read_text(encoding="utf-8"), f.name)
        self.assertNotIn(("ops", "devoluciones_vs_canal_v"), modelo.vistas)
        for t in (("ops", "odoo_sale_orders"), ("ops", "odoo_sale_order_items"),
                  ("ops", "automatizacion_flags"), ("costing", "caja_compartida")):
            self.assertTrue(modelo.tablas[t]["protegido"], t)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(verificar_rls.revisar_estatico(), 0)


if __name__ == "__main__":
    unittest.main()
