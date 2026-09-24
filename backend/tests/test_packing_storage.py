"""Pruebas de «packing lists en Supabase» (0055/0056, etapa 3).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Bucket primero, Drive de respaldo.** Con PACKING_LEER_STORAGE, `bajar()`
   no toca Drive si el archivo está copiado; si no está, o si Storage falla,
   cae a Drive como antes. Con el flag apagado, ni pregunta al bucket.
2. **Una huella que no cuadra no se lee.** Un objeto que no coincide con su
   sha256 del índice es peor que no tenerlo: se costearía con un papel que nadie
   validó.
3. **La procedencia lleva la huella** solo con el flag (sin la 0056 la columna
   no existe y tiraría el guardado entero), y se escribe aunque venga None.
4. **El piloto de Inventario abre la versión validada** por huella, y sin
   huella sigue por nombre.
5. **Desempacar**: un xlsx TAMBIÉN es un zip; se distingue por
   `xl/workbook.xml`. Un zip con hojas adentro se abre; basura no pasa.

Sin red: nada aquí habla con Drive, Supabase ni Railway.
    cd backend && python -m unittest tests.test_packing_storage -v
"""
from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from services import (costing_write, packing_cajas, packing_drive,  # noqa: E402
                      packing_drive_carpeta as carpeta, packing_storage as st)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import copiar_packing_lists as copia  # noqa: E402


def _xlsx() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active["A1"] = "箱数 CTNS"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class _Flag:
    """PACKING_LEER_STORAGE prendido/apagado solo dentro del `with`."""

    def __init__(self, valor: bool) -> None:
        self.p = mock.patch.object(settings, "packing_leer_storage", valor)

    def __enter__(self):
        return self.p.__enter__()

    def __exit__(self, *a):
        return self.p.__exit__(*a)


class BajarPrimeroDelBucket(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(carpeta, "_CACHE_DIR", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)

    def test_copiado_no_toca_drive(self):
        with _Flag(True), \
             mock.patch.object(st, "bajar_vigente", return_value=b"DEL-BUCKET") as sb, \
             mock.patch.object(packing_drive, "descargar_id",
                               side_effect=AssertionError("no debió ir a Drive")):
            self.assertEqual(carpeta.bajar("fid-1", "x.xlsx"), b"DEL-BUCKET")
        sb.assert_called_once_with("fid-1")

    def test_no_copiado_cae_a_drive(self):
        with _Flag(True), mock.patch.object(st, "bajar_vigente", return_value=None), \
             mock.patch.object(packing_drive, "descargar_id",
                               return_value=(b"DE-DRIVE", "x.xlsx")) as dd:
            self.assertEqual(carpeta.bajar("fid-2", "x.xlsx"), b"DE-DRIVE")
        dd.assert_called_once()

    def test_storage_caido_cae_a_drive(self):
        with _Flag(True), \
             mock.patch.object(st, "bajar_vigente", side_effect=st.StorageError("503")), \
             mock.patch.object(packing_drive, "descargar_id",
                               return_value=(b"DE-DRIVE", "x.xlsx")):
            self.assertEqual(carpeta.bajar("fid-3", "x.xlsx"), b"DE-DRIVE")

    def test_flag_apagado_ni_pregunta_al_bucket(self):
        with _Flag(False), \
             mock.patch.object(st, "bajar_vigente",
                               side_effect=AssertionError("no debió ir al bucket")), \
             mock.patch.object(packing_drive, "descargar_id",
                               return_value=(b"DE-DRIVE", "x.xlsx")):
            self.assertEqual(carpeta.bajar("fid-4", "x.xlsx"), b"DE-DRIVE")

    def test_inventario_suma_lo_copiado_solo_con_flag(self):
        with mock.patch.object(st, "inventario", return_value={"fid-b": "B.xlsx"}):
            with _Flag(True):
                self.assertEqual(carpeta._copiados(), {"fid-b": "B.xlsx"})
            with _Flag(False):
                self.assertEqual(carpeta._copiados(), {})


class HuellaQueNoCuadra(unittest.TestCase):
    def test_se_rechaza(self):
        fila = {"ruta": "original/abc.xlsx", "sha256": hashlib.sha256(b"bueno").hexdigest()}
        with mock.patch.object(st, "bajar", return_value=b"alterado"):
            with self.assertRaises(st.StorageError):
                st._bajar_verificado(fila)

    def test_la_buena_pasa(self):
        fila = {"ruta": "original/abc.xlsx", "sha256": hashlib.sha256(b"bueno").hexdigest()}
        with mock.patch.object(st, "bajar", return_value=b"bueno"):
            self.assertEqual(st._bajar_verificado(fila), b"bueno")


class _Cursor:
    def __init__(self) -> None:
        self.sql: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return {"sku": "ABC-0001-NEG", "contenedor_base": "MRKU4831449",
                "renglones": [34], "archivo": "MRKU4831449 PL.xlsx"}


class ProcedenciaConHuella(unittest.TestCase):
    def _guardar(self, flag: bool, sha: str | None) -> _Cursor:
        cur = _Cursor()
        ctx = mock.MagicMock()
        ctx.__enter__.return_value = cur
        with _Flag(flag), mock.patch.object(costing_write.sdb, "get_cursor", return_value=ctx):
            costing_write.guardar_caja_compartida(
                "ABC-0001-NEG", "MRKU4831449 - 88", "MRKU4831449 PL.xlsx", [34],
                archivo_sha256=sha)
        return cur

    def test_con_flag_escribe_la_huella(self):
        sha = "a" * 64
        cur = self._guardar(True, sha)
        self.assertEqual(len(cur.sql), 2)
        self.assertIn("set archivo_sha256", cur.sql[1][0])
        self.assertEqual(cur.sql[1][1], (sha, "ABC-0001-NEG", "MRKU4831449"))

    def test_con_flag_y_sin_huella_la_limpia(self):
        cur = self._guardar(True, None)
        self.assertEqual(cur.sql[1][1][0], None)

    def test_sin_flag_no_toca_la_columna(self):
        cur = self._guardar(False, "a" * 64)
        self.assertEqual(len(cur.sql), 1)
        self.assertNotIn("archivo_sha256", cur.sql[0][0])


class ContenedorAlGuardar(unittest.TestCase):
    """El validador escribe de qué embarque salió el costo, con la forma de la
    casa ("TGHU6894814 - 80"), y nadie más lo borra al recalcular."""

    def test_el_upsert_conserva_el_contenedor_si_no_se_manda(self):
        from services import costing_mirror
        cur = _Cursor()
        costing_mirror.upsert_validados(cur, "ABC-0001-NEG", {"largo": 1, "alto": 1, "ancho": 1,
                                                              "peso": 1, "costo_producto": 1,
                                                              "costo_cbm": 1, "costo_total": 2})
        sql, params = cur.sql[0]
        self.assertIn("contenedor = coalesce(excluded.contenedor, costos_validados.contenedor)", sql)
        self.assertIsNone(params["contenedor"])

    def _resolver(self, respuestas, flag=True):
        from services import packing_publicados as pp
        with _Flag(flag), mock.patch.object(pp, "_consulta_uno", side_effect=respuestas):
            return pp._contenedor_para_costos("TGHU6894814", {})

    def test_usa_la_forma_que_ya_tienen_sus_companeros(self):
        self.assertEqual(self._resolver([{"contenedor": "TGHU6894814 - 80"}]), "TGHU6894814 - 80")

    def test_si_nadie_lo_tiene_el_numero_sale_de_ferraforme(self):
        self.assertEqual(self._resolver([None, {"nombre": " TGHU6894814 PL contenedor 80.xlsx"}]),
                         "TGHU6894814 - 80")

    def test_sin_flag_ni_companeros_va_el_codigo(self):
        self.assertEqual(self._resolver([None], flag=False), "TGHU6894814")

    def test_sin_codigo_no_hay_contenedor(self):
        from services import packing_publicados as pp
        self.assertIsNone(pp._contenedor_para_costos("", {}))


class PilotoAbreLaVersionValidada(unittest.TestCase):
    def test_con_huella_va_al_bucket(self):
        pidx = mock.Mock()
        drive = mock.Mock(side_effect=AssertionError("no debió ir a Drive"))
        with mock.patch.object(st, "bajar_por_huella", return_value=b"VERSION") as bh:
            packing_cajas._indexar("A.xlsx", "MRKU4831449", {}, drive, pidx, "b" * 64)
        bh.assert_called_once_with("b" * 64)
        pidx.indexar.assert_called_once_with(b"VERSION", "A.xlsx")
        drive.bajar.assert_not_called()

    def test_sin_huella_sigue_por_nombre(self):
        pidx, drive = mock.Mock(), mock.Mock()
        drive.archivos_de.return_value = [("fid-9", "A.xlsx")]
        drive.bajar.return_value = b"DE-DRIVE"
        with mock.patch.object(st, "bajar_por_huella",
                               side_effect=AssertionError("sin huella no hay bucket")):
            packing_cajas._indexar("A.xlsx", "MRKU4831449", {}, drive, pidx, "")
        drive.bajar.assert_called_once_with("fid-9", "A.xlsx")


class Desempacar(unittest.TestCase):
    def test_xlsx_es_xlsx_aunque_sea_zip(self):
        datos = _xlsx()
        self.assertEqual(copia.desempacar(datos), [(datos, None, "xlsx")])

    def test_zip_con_hoja_adentro(self):
        hoja, buf = _xlsx(), io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("MRSU8489194 contenedor 44.xlsx", hoja)
            z.writestr("__MACOSX/._MRSU8489194 contenedor 44.xlsx", b"basura")
            z.writestr("~$bloqueo.xlsx", b"basura")
        self.assertEqual(copia.desempacar(buf.getvalue()),
                         [(hoja, "MRSU8489194 contenedor 44.xlsx", "xlsx")])

    def test_una_pagina_de_drive_no_pasa(self):
        with self.assertRaises(ValueError):
            copia.desempacar(b"<!DOCTYPE html><html>Solicitar acceso</html>")

    def test_codigo_de_contenedor(self):
        # El match MÁS LARGO: la forma genérica se comería el último dígito.
        self.assertEqual(copia.codigo("SZLS50213900=CIPL(1) (1).xlsx"), "SZLS50213900")
        self.assertEqual(copia.codigo("Cont 95 TLLU8977270-P03087.xlsx"), "TLLU8977270")
        self.assertIsNone(copia.codigo("027F655826=CI&PL"))

    def test_mismo_patron_que_caja_compartida(self):
        # Si divergen, packing_archivos y caja_compartida dejan de juntarse.
        from services import packing_publicados
        self.assertEqual(copia.CODIGO.pattern, packing_publicados._RE_CONTENEDOR.pattern)


if __name__ == "__main__":
    unittest.main()
