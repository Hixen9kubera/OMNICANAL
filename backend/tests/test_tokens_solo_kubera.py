"""Pruebas de TOKENS_SOLO_KUBERA: los tokens de ML sin MySQL.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Con el flag, el token sale SOLO de kubera.** Ni una consulta a MySQL, ni
   siquiera si kubera no contesta: entonces no hay token (None), no un token
   viejo de otra tabla. Y la cuenta se busca en MAYÚSCULAS, como está guardada
   (MySQL no distinguía; Postgres sí).
2. **Renovar pasa por el candado y respeta a quien acaba de renovar.** Si la
   fila tiene menos de 2 min, se reutiliza sin gastar el refresh_token.
3. **No se intenta renovar con la app equivocada.** El token dice qué app lo
   emitió; si el entorno da otra, ML lo rechazaría: se avisa y no se llama.
4. **La app y la clave salen del entorno**: primero las de la cuenta, luego las
   globales. Nunca de una tabla.
5. **El par nuevo no se pierde**: si no se pudo guardar bajo el candado, se
   guarda fuera. ML ya rotó el refresh_token y perderlo = re-autorizar a mano.
6. **MySQL recibe copia** (la reversa si se apaga el flag), salvo que MySQL
   esté apagado en ese ambiente.
7. **Sin el flag, nada cambia.**
8. El vigilante de tokens rancios mira CADA cuenta en kubera.

Sin red ni base de datos.

    cd backend && python -m unittest tests.test_tokens_solo_kubera -v
"""
from __future__ import annotations

import contextlib
import os
import unittest
from unittest import mock

from cryptography.fernet import Fernet

from services import alertas, meli, tokens_read
from services import supabase_db as sdb

_F = Fernet(Fernet.generate_key())
_APP_JOSE = "1446854968053102"
_APP_NUESTRA = "8902165405612832"
_TOKEN_JOSE = f"APP_USR-{_APP_JOSE}-092412-prueba-111"


def enc(v: str) -> str:
    return _F.encrypt(v.encode()).decode()


def dec(v: str) -> str:
    return _F.decrypt(v.encode()).decode()


class _Candado:
    """Doble de `tokens_read.candado_renovacion`: entrega la fila y apunta lo guardado."""

    def __init__(self, fila):
        self.fila = fila
        self.cuentas: list[str] = []
        self.guardados: list[tuple[str, str]] = []
        self.falla_guardar: Exception | None = None

    @contextlib.contextmanager
    def __call__(self, cuenta):
        self.cuentas.append(cuenta)

        def guardar_par(at, rt):
            if self.falla_guardar:
                raise self.falla_guardar
            self.guardados.append((at, rt))

        yield self.fila, guardar_par


class _Base(unittest.TestCase):
    def setUp(self):
        mock.patch.object(meli, "_fernet", return_value=_F).start()
        mock.patch.object(meli.settings, "tokens_solo_kubera", True).start()
        mock.patch.object(meli.settings, "meli_app_id", _APP_NUESTRA).start()
        mock.patch.object(meli.settings, "meli_client_secret", "secreto-global").start()
        # MySQL NO se consulta con el flag: si alguien lo intenta, truena.
        self.mysql = mock.patch.object(
            meli.db, "fetch_one", side_effect=AssertionError("leyó MySQL")).start()
        mock.patch.dict(os.environ, {}, clear=False).start()
        for k in [k for k in os.environ if k.startswith(("MELI_APP_ID_", "MELI_CLIENT_SECRET_"))]:
            del os.environ[k]
        self.addCleanup(mock.patch.stopall)


class AccessToken(_Base):
    def test_sale_solo_de_kubera_y_en_mayusculas(self):
        with mock.patch.object(tokens_read, "leer",
                               return_value={"access_token": enc(_TOKEN_JOSE)}) as leer:
            self.assertEqual(meli._access_token("bekura"), _TOKEN_JOSE)
        leer.assert_called_once_with("BEKURA")
        self.mysql.assert_not_called()

    def test_sin_fila_es_none(self):
        with mock.patch.object(tokens_read, "leer", return_value=None):
            self.assertIsNone(meli._access_token("BEKURA"))
        self.mysql.assert_not_called()

    def test_kubera_caida_es_none_y_no_cae_a_mysql(self):
        with mock.patch.object(tokens_read, "leer", side_effect=RuntimeError("caída")):
            self.assertIsNone(meli._access_token("BEKURA"))
        self.mysql.assert_not_called()

    def test_sin_cuenta_pide_la_mas_reciente(self):
        with mock.patch.object(tokens_read, "leer",
                               return_value={"access_token": enc("tok")}) as leer:
            self.assertEqual(meli._access_token(None), "tok")
        leer.assert_called_once_with(None)

    def test_sin_flag_sigue_el_arbitraje_con_mysql(self):
        self.mysql.side_effect = None
        from datetime import datetime
        self.mysql.return_value = {"access_token": enc("de-mysql"),
                                   "updated_at": datetime(2026, 9, 24)}
        with mock.patch.object(meli.settings, "tokens_solo_kubera", False), \
             mock.patch.object(meli.settings, "supabase_read_tokens", False):
            self.assertEqual(meli._access_token("BEKURA"), "de-mysql")
        self.assertTrue(self.mysql.called)


class AppDeCuenta(_Base):
    def test_primero_la_de_la_cuenta(self):
        with mock.patch.dict(os.environ, {"MELI_APP_ID_BEKURA": _APP_JOSE,
                                          "MELI_CLIENT_SECRET_BEKURA": "s-jose"}):
            self.assertEqual(meli._app_de_cuenta("BEKURA"), (_APP_JOSE, "s-jose"))
            self.assertEqual(meli._app_de_cuenta("SANCORFASHION"),
                             (_APP_NUESTRA, "secreto-global"))

    def test_incompleta_cae_a_la_global(self):
        with mock.patch.dict(os.environ, {"MELI_APP_ID_BEKURA": _APP_JOSE}):
            self.assertEqual(meli._app_de_cuenta("BEKURA"), (_APP_NUESTRA, "secreto-global"))

    def test_sin_ninguna_es_none(self):
        with mock.patch.object(meli.settings, "meli_app_id", ""):
            self.assertIsNone(meli._app_de_cuenta("BEKURA"))


class AppDelToken(unittest.TestCase):
    def test_lee_el_numero_de_app(self):
        self.assertEqual(meli._app_del_token(_TOKEN_JOSE), _APP_JOSE)

    def test_lo_que_no_es_token_de_ml_es_none(self):
        for t in (None, "", "PRUEBA-access", "APP_USR-abc-1-2", "APP_USR"):
            self.assertIsNone(meli._app_del_token(t), t)


class Renovar(_Base):
    def setUp(self):
        super().setUp()
        self.candado = _Candado({"cuenta": "BEKURA", "access_token": enc(_TOKEN_JOSE),
                                 "refresh_token": enc("rt-viejo"), "edad_s": 7 * 3600.0})
        mock.patch.object(tokens_read, "candado_renovacion", self.candado).start()
        self.avisar = mock.patch.object(alertas, "avisar").start()
        self.espejo = mock.patch.object(meli, "_espejar_mysql").start()
        self.post = mock.patch.object(meli.httpx, "post").start()
        self.post.return_value = mock.Mock(status_code=200, json=lambda: {
            "access_token": f"APP_USR-{_APP_JOSE}-092418-nuevo-222",
            "refresh_token": "rt-nuevo"})

    def _con_app_de_jose(self):
        return mock.patch.dict(os.environ, {"MELI_APP_ID_BEKURA": _APP_JOSE,
                                            "MELI_CLIENT_SECRET_BEKURA": "s-jose"})

    def test_renueva_con_la_app_de_la_cuenta_y_guarda_bajo_candado(self):
        with self._con_app_de_jose():
            nuevo = meli.refrescar_token("bekura")
        self.assertEqual(nuevo, f"APP_USR-{_APP_JOSE}-092418-nuevo-222")
        self.assertEqual(self.candado.cuentas, ["BEKURA"])
        datos = self.post.call_args.kwargs["data"]
        self.assertEqual((datos["client_id"], datos["client_secret"], datos["refresh_token"]),
                         (_APP_JOSE, "s-jose", "rt-viejo"))
        (at, rt), = self.candado.guardados
        self.assertEqual((dec(at), dec(rt)), (nuevo, "rt-nuevo"))
        self.espejo.assert_called_once_with("BEKURA", at, rt)
        self.mysql.assert_not_called()

    def test_reusa_si_otro_proceso_acaba_de_renovar(self):
        self.candado.fila["edad_s"] = 5.0
        with self._con_app_de_jose():
            self.assertEqual(meli.refrescar_token("BEKURA"), _TOKEN_JOSE)
        self.post.assert_not_called()
        self.assertEqual(self.candado.guardados, [])

    def test_no_llama_a_ml_con_la_app_equivocada(self):
        # Sin variables por cuenta, el entorno da la 8902 y el token es de la 1446.
        self.assertIsNone(meli.refrescar_token("BEKURA"))
        self.post.assert_not_called()
        texto = self.avisar.call_args.args[1]
        self.assertIn(_APP_JOSE, texto)
        self.assertIn(_APP_NUESTRA, texto)
        self.assertIn("MELI_APP_ID_BEKURA", texto)

    def test_si_ml_rechaza_no_guarda_nada_y_avisa(self):
        self.post.return_value = mock.Mock(status_code=400, text='{"error":"invalid_grant"}')
        with self._con_app_de_jose():
            self.assertIsNone(meli.refrescar_token("BEKURA"))
        self.assertEqual(self.candado.guardados, [])
        self.espejo.assert_not_called()
        self.assertIn("invalid_grant", self.avisar.call_args.args[1])

    def test_sin_refresh_token_no_llama_a_ml(self):
        self.candado.fila = None
        with self._con_app_de_jose():
            self.assertIsNone(meli.refrescar_token("BEKURA"))
        self.post.assert_not_called()

    def test_si_no_se_guardo_bajo_candado_se_guarda_fuera(self):
        self.candado.falla_guardar = RuntimeError("conexión perdida")
        with self._con_app_de_jose(), \
             mock.patch.object(tokens_read, "guardar") as guardar:
            nuevo = meli.refrescar_token("BEKURA")
        self.assertIsNotNone(nuevo)
        cuenta, at, rt = guardar.call_args.args
        self.assertEqual((cuenta, dec(at), dec(rt)), ("BEKURA", nuevo, "rt-nuevo"))
        self.espejo.assert_called_once()

    def test_sin_flag_no_entra_al_camino_nuevo(self):
        with mock.patch.object(meli.settings, "tokens_solo_kubera", False), \
             mock.patch.object(meli, "_credenciales_refresh", return_value=None), \
             mock.patch.object(meli, "_refrescar_solo_kubera") as nuevo:
            self.assertIsNone(meli.refrescar_token("BEKURA"))
        nuevo.assert_not_called()


class RevisionPrevia(_Base):
    def _filas(self):
        return [{"cuenta": "BEKURA", "access_token": enc(_TOKEN_JOSE), "edad_min": 30.0},
                {"cuenta": "SANCORFASHION", "access_token": enc(f"APP_USR-{_APP_NUESTRA}-1-2-3"),
                 "edad_min": 5.0}]

    def test_dice_que_cuenta_no_se_podria_renovar(self):
        with mock.patch.object(sdb, "fetch_all", return_value=self._filas()):
            res = {r["cuenta"]: r for r in meli.revisar_apps_kubera()}
        self.assertFalse(res["BEKURA"]["coincide"])          # token 1446, entorno 8902
        self.assertEqual(res["BEKURA"]["fuente"], "global")
        self.assertTrue(res["SANCORFASHION"]["coincide"])

    def test_con_la_app_de_la_cuenta_coincide(self):
        with mock.patch.object(sdb, "fetch_all", return_value=self._filas()), \
             mock.patch.dict(os.environ, {"MELI_APP_ID_BEKURA": _APP_JOSE,
                                          "MELI_CLIENT_SECRET_BEKURA": "s-jose"}):
            res = {r["cuenta"]: r for r in meli.revisar_apps_kubera()}
        self.assertTrue(res["BEKURA"]["coincide"])
        self.assertEqual(res["BEKURA"]["fuente"], "cuenta")

    def test_no_devuelve_tokens_ni_claves(self):
        with mock.patch.object(sdb, "fetch_all", return_value=self._filas()):
            texto = repr(meli.revisar_apps_kubera())
        self.assertNotIn("APP_USR", texto)
        self.assertNotIn("secreto-global", texto)


class EspejoMysql(_Base):
    def test_con_mysql_apagado_no_lo_toca(self):
        with mock.patch.object(meli.settings, "mysql_enabled", False), \
             mock.patch.object(meli.db, "get_cursor") as cur:
            meli._espejar_mysql("BEKURA", "a", "r")
        cur.assert_not_called()

    def test_copia_a_las_dos_tablas_aunque_una_falle(self):
        tablas, llamadas = [], []

        @contextlib.contextmanager
        def cursor():
            llamadas.append(1)
            if len(llamadas) == 1:          # la primera tabla falla
                raise RuntimeError("MySQL sin conexiones")
            c = mock.Mock()
            c.execute.side_effect = lambda sql, p: tablas.append(sql.split()[1])
            yield c

        with mock.patch.object(meli.settings, "mysql_enabled", True), \
             mock.patch.object(meli.db, "get_cursor", cursor):
            meli._espejar_mysql("BEKURA", "a", "r")
        self.assertEqual(tablas, ["ml_tokens_dashboard"])


class VigilanteRancios(unittest.TestCase):
    def setUp(self):
        mock.patch.object(alertas.settings, "tokens_solo_kubera", True).start()
        self.avisar = mock.patch.object(alertas, "avisar").start()
        self.addCleanup(mock.patch.stopall)

    def test_avisa_por_cuenta_no_por_el_maximo(self):
        filas = [{"cuenta": "BEKURA", "horas": 2.0}, {"cuenta": "SANCORFASHION", "horas": 13.2}]
        with mock.patch.object(sdb, "fetch_all", return_value=filas):
            alertas._revisar_tokens_rancios()
        texto = self.avisar.call_args.args[1]
        self.assertIn("SANCORFASHION hace 13 h", texto)
        self.assertNotIn("BEKURA", texto)

    def test_frescas_no_avisa(self):
        with mock.patch.object(sdb, "fetch_all",
                               return_value=[{"cuenta": "BEKURA", "horas": 5.9}]):
            alertas._revisar_tokens_rancios()
        self.avisar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
