"""Pruebas de la «opción B»: el flujo del SKU dentro de /omnicanal.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **El sello por fila** (`inventario_flujo.sello`): en qué etapa está un SKU,
   qué le falta y —sobre todo— cuándo NO se puede afirmar nada. Una fuente
   caída pinta «sin dato», nunca «no hay».
2. **La lista de una etapa y su traducción a wc_id**, incluida la expansión del
   SKU padre a sus variantes.
3. **Los conteos del stepper**, con el NÚMERO y el CLIC separados: una caída de
   `channel.listings` deja las cifras en `null` y el filtro pulsable.
4. **La cuarta fuente** `canales`: reglas de inclusión, predicados interpolados
   y que su caída no toca a las otras tres.
5. **El contrato de `/flujo` NO cambia**: sigue viendo tres fuentes.
6. **`GET /api/productos?etapa=`**: 400 antes de tocar nada, 503 con motivo en
   vez de «0 productos», y `filtro_etapa` como prueba de que se aplicó.
7-8. **El filtro exacto en SQL** y el **modo estricto** de General.
9. La ruta nueva no cae en la comodín.
10. Sin dinero en ninguna llave.

Sin red: nada aquí habla con Odoo, kubera, WordPress ni Railway.

    cd backend && python -m unittest tests.test_flujo_omnicanal -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import inventario as ruta_inv  # noqa: E402
from routers import productos as ruta_prod  # noqa: E402
from services import channel_read, inventario_flujo as invf  # noqa: E402
from services import temu_panel, tiktok_panel, walmart_panel, woocommerce, wp_db  # noqa: E402
from tests.test_inventario_flujo import T0, _kubera_filas, _odoo_crudo  # noqa: E402

# La función REAL, guardada antes de que ninguna prueba la parche: hace falta
# para comprobar que su `[], 0` de siempre sigue ahí sin `estricto`.
_LISTAR_TIKTOK = tiktok_panel.listar


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

def _odoo_omni() -> dict:
    """El catálogo de `_odoo_crudo` (SKU-0001…0100) más los casos raros:

      · `SOLO-ODOO`   — tres cuadros en listo pero FUERA de core.products, así
                        que no entra en la lista `bodega_3de4`. NO es escritura
                        distinta: es el hueco del seam, y el sello lo nombra
                        aparte.
      · `DIST-0001`   — el mismo caso pero SÍ en core.products, escrito ahí
                        `Dist-0001`: ése sí es «SKU escrito distinto en Odoo».
      · `HERM-0001-A/B` — dos variantes de la misma plantilla: ninguna llega a
                        foto «listo», esperan la foto de bodega.
      · `ARCH-0001`   — archivado.
      · `Amb-0001` / `AMB-0001` — el mismo código con dos escrituras: no se
                        adivina cuál es.

    `SOLO-ODOO` además trae EMPAQUE MASTER: es el caso que fija el recorte D6
    de la unión de Recibido —tiene con qué contar por el lado de Odoo, pero no
    está en `core.products`, así que NO entra en la lista de la etapa— aunque
    su sello sí pueda decir «recibido por Odoo», que es la verdad de ese SKU.
    """
    crudo = _odoo_crudo(100, con_quants=40)
    catalogo = list(crudo["catalogo"])
    quants = dict(crudo["quants"])
    libres = set(crudo["libres"])
    fotos = set(crudo["fotos"])

    def _mas(id_: int, code: str, tmpl: int, *, activo=True, quant=True,
             libre=True, foto=True, caja=None):
        catalogo.append({"id": id_, "default_code": code, "tmpl_id": tmpl,
                         "active": activo, "piezas_por_caja": caja})
        if quant:
            quants[id_] = 1.0
        if libre:
            libres.add(id_)
        if foto:
            fotos.add(id_)

    _mas(501, "SOLO-ODOO", 2501, caja=6.0)
    _mas(502, "HERM-0001-A", 2502)
    _mas(503, "HERM-0001-B", 2502)          # hermana por plantilla → foto espera
    _mas(504, "ARCH-0001", 2504, activo=False)
    _mas(505, "Amb-0001", 2505)
    _mas(506, "AMB-0001", 2506)
    _mas(507, "DIST-0001", 2507)            # kubera lo escribe `Dist-0001`
    return {"catalogo": catalogo, "quants": quants, "libres": libres,
            "fotos": fotos}


def _canales_filas() -> dict:
    """Filas de `channel.listings` ya evaluadas, como las devuelve
    `_leer_canales`. Incluye a propósito las que cada rejilla DESCARTA."""
    filas: list[dict] = []

    def _fila(sku, canal, cuenta="", publicado=True, activa=False, full=False,
              en_catalogo=True):
        filas.append({"sku": sku, "canal": canal, "cuenta": cuenta,
                      "publicado": publicado, "activa": activa, "full": full,
                      "en_catalogo": en_catalogo})

    for i in range(1, 61):                       # ML · BEKURA
        _fila(f"SKU-{i:04d}-NEG", "mercado_libre", "BEKURA",
              publicado=i <= 45, activa=i % 2 == 0, full=51 <= i <= 55)
    for i in range(1, 21):                       # ML · SANCORFASHION
        _fila(f"SKU-{i:04d}-NEG", "mercado_libre", "SANCORFASHION",
              publicado=True, activa=True)
    for i in range(51, 56):                      # el mismo SKU en las dos
        _fila(f"SKU-{i:04d}-NEG", "mercado_libre", "SANCORFASHION", full=True)
    # ML sin cuenta: la rejilla hace `join core.accounts`, así que NO se pagina.
    # ML sin cuenta: la rejilla hace `join core.accounts`, así que NO se pagina.
    _fila("SKU-0099-NEG", "mercado_libre", "")
    for i in range(1, 31):                       # Amazon (sin join a products)
        _fila(f"SKU-{i:04d}-NEG", "amazon", publicado=i <= 25, activa=i <= 25)
    _fila("FUERA-DEL-CATALOGO", "amazon", en_catalogo=False)
    for i in range(1, 11):                       # TikTok (join a products)
        _fila(f"SKU-{i:04d}-NEG", "tiktok", publicado=i <= 3, activa=i <= 3)
    _fila("FUERA-DEL-CATALOGO", "tiktok", en_catalogo=False)
    for i in range(1, 6):                        # Temu: todas son publicaciones
        _fila(f"SKU-{i:04d}-NEG", "temu", publicado=True, activa=i <= 2)
    for i in range(1, 8):                        # Walmart
        _fila(f"SKU-{i:04d}-NEG", "walmart", publicado=i <= 4, activa=i <= 4)
    return {"filas": filas, "sin_activas": []}


def _foto_omni(*, kubera=True, odoo=True, drop=True, canales=True,
               ahora=T0) -> invf.Foto:
    """La foto completa, con cualquiera de las cuatro fuentes apagada."""
    def _lec(dato, vivo):
        return (invf.Lectura(datos=dato, generado=ahora) if vivo
                else invf.Lectura(error="RuntimeError: caída", generado=ahora))

    return invf.armar_foto(
        _lec(_kubera_filas(), kubera),
        _lec(_odoo_omni(), odoo),
        _lec(({"SKU-0001-NEG", "SKU-0002-NEG", "NO-EXISTE"}, 0.0), drop),
        None, ahora=ahora, canales=_lec(_canales_filas(), canales))


def _fp(foto: invf.Foto | None = None, *, estado="listo", ahora=T0) -> invf.FotoPeticion:
    foto = _foto_omni(ahora=ahora) if foto is None else foto
    return invf.FotoPeticion(foto, invf._Contexto(foto, ahora), estado, None,
                             invf._iso(foto.generado))


class _Base(unittest.TestCase):
    def setUp(self):
        invf._foto = None
        self.addCleanup(setattr, invf, "_foto", None)
        p = mock.patch.object(invf, "calentar_en_fondo", return_value=False)
        p.start()
        self.addCleanup(p.stop)


# ─────────────────────────────────────────────────────────────────────────────
# 1 · EL SELLO
# ─────────────────────────────────────────────────────────────────────────────

class SelloPuro(_Base):
    def setUp(self):
        super().setUp()
        self.fp = _fp()

    def _sello(self, sku, **kw):
        return invf.sello(self.fp, sku, **kw)

    # ── recibido ─────────────────────────────────────────────────────────────
    def test_recibido_si(self):
        s = self._sello("SKU-0010-NEG")
        self.assertEqual(s["pasos"]["recibido"]["estado"], "si")
        self.assertEqual(s["pasos"]["recibido"]["motivo"], None)

    def test_recibido_no_distingue_sin_cajas_de_sin_renglon(self):
        # Ninguno de los dos tiene empaque en Odoo (`EMPAQUE_ODOO` son 56–60 y
        # 73–77),
        # así que el «no» sigue siendo un «no» con la regla nueva y el motivo
        # sigue describiendo el lado accionable, el del packing list.
        con = self._sello("SKU-0070-NEG")      # tiene renglón, sin cajas
        sin = self._sello("SKU-0090-NEG")      # ni renglón
        self.assertEqual(con["pasos"]["recibido"], {
            "estado": "no", "motivo": "sin_cajas", "fuente": None,
            "vieja": False, "generado": "2026-09-15T15:40:00Z"})
        self.assertEqual(sin["pasos"]["recibido"]["motivo"], "sin_renglon")
        # El texto NOMBRA LAS DOS MITADES: llenar el packing list no es el único
        # camino, declarar el empaque en Odoo también cuenta.
        self.assertIn("Recibido (sin cajas en costos ni empaque en Odoo)",
                      con["le_falta"])
        self.assertIn("Recibido (no tiene renglón en costos ni empaque en Odoo)",
                      sin["le_falta"])

    def test_sin_kubera_recibido_es_sin_dato_y_no_no(self):
        s = invf.sello(_fp(_foto_omni(kubera=False)), "SKU-0001-NEG")
        self.assertEqual(s["pasos"]["recibido"]["estado"], "sin_dato")
        self.assertEqual(s["en_catalogo"], None)
        self.assertIn("sin dato de kubera", s["le_falta"])

    def test_recibido_dice_de_cual_de_las_dos_columnas_sale(self):
        """`fuente` separa «viene en el packing list del embarque» de «Odoo sabe
        cómo se empaca»: dos evidencias distintas del mismo casillero, y ninguna
        de las dos afirma que la mercancía entró a la bodega."""
        for sku, fuente in (("SKU-0010-NEG", "packing_list"),
                            ("SKU-0073-NEG", "odoo"),
                            ("SKU-0058-NEG", "ambas")):
            paso = self._sello(sku)["pasos"]["recibido"]
            self.assertEqual((paso["estado"], paso["fuente"]), ("si", fuente), sku)
        ninguna = self._sello("SKU-0070-NEG")["pasos"]["recibido"]
        self.assertEqual((ninguna["estado"], ninguna["fuente"]), ("no", None))

    def test_el_empaque_de_odoo_solo_cambia_la_etapa_y_borra_el_renglon(self):
        # SKU-0073 tiene renglón en costos SIN cajas: antes del 18-sep caía en
        # «ninguna» y `le_falta` pedía Recibido. Con el empaque declarado la
        # etapa es Recibido y ese renglón desaparece; lo de bodega sigue igual.
        s = self._sello("SKU-0073-NEG")
        self.assertEqual(s["etapa"], "recibido")
        self.assertEqual([t for t in s["le_falta"] if t.startswith("Recibido")], [])
        self.assertIn("ubicación", s["le_falta"], "bodega no cambia")

    def test_odoo_caido_deja_recibido_en_sin_dato_y_no_en_no(self):
        """Con Odoo caído solo se sabe la mitad de la regla. Un SKU que SÍ viene
        en el packing list se puede afirmar igual —basta una de las dos—; uno
        que no, no: podría traer empaque declarado y nadie lo sabría."""
        fp = _fp(_foto_omni(odoo=False))
        con_pl = invf.sello(fp, "SKU-0010-NEG")["pasos"]["recibido"]
        self.assertEqual((con_pl["estado"], con_pl["fuente"]), ("si", "packing_list"))
        sin_pl = invf.sello(fp, "SKU-0070-NEG")
        self.assertEqual(sin_pl["pasos"]["recibido"]["estado"], "sin_dato")
        self.assertIsNone(sin_pl["pasos"]["recibido"]["fuente"])
        # El hueco es de Odoo y el renglón de bodega ya lo dice: no se repite,
        # y sobre todo no se acusa a kubera de una caída que no es suya.
        self.assertIn("sin dato de Odoo", sin_pl["le_falta"])
        self.assertNotIn("sin dato de kubera", sin_pl["le_falta"])

    def test_el_empaque_fuera_de_core_products_no_entra_en_la_lista(self):
        """D6: el universo es core.products. SOLO-ODOO trae empaque master pero
        no está en el catálogo de kubera: su sello puede decir «recibido por
        Odoo», que es la verdad de ese SKU, y aun así NO cuenta en la etapa. El
        hueco es del seam y `le_falta` lo nombra en vez de esconderlo."""
        fp = _fp()
        s = invf.sello(fp, "SOLO-ODOO")
        self.assertEqual(s["pasos"]["recibido"]["fuente"], "odoo")
        self.assertIs(s["en_catalogo"], False)
        self.assertNotIn("SOLO-ODOO", fp.foto.listas["recibido"])
        self.assertIn("no está en el catálogo de kubera", s["le_falta"])

    def test_la_frescura_de_recibido_es_la_de_la_fuente_mas_vieja(self):
        """El paso lo contestan DOS fuentes, así que su fecha es la de la más
        vieja: enseñar la de kubera a secas diría que el dato es más fresco de
        lo que es (`destino` ya hacía lo mismo con kubera y DROP)."""
        vieja = invf.armar_foto(
            invf.Lectura(datos=_kubera_filas(), generado=T0),
            invf.Lectura(datos=_odoo_omni(), generado=T0), None, None, ahora=T0)
        nueva = invf.armar_foto(
            invf.Lectura(datos=_kubera_filas(), generado=T0 + timedelta(minutes=30)),
            invf.Lectura(error="RuntimeError: caída",
                         generado=T0 + timedelta(minutes=30)),
            None, vieja, ahora=T0 + timedelta(minutes=30))
        self.assertTrue(nueva.fuentes["odoo"].vieja)
        paso = invf.sello(_fp(nueva, ahora=T0 + timedelta(minutes=30)),
                          "SKU-0010-NEG")["pasos"]["recibido"]
        self.assertIs(paso["vieja"], True)
        self.assertEqual(paso["generado"], "2026-09-15T15:40:00Z",
                         "la de Odoo, que es la vieja")

    # ── bodega ───────────────────────────────────────────────────────────────
    def test_cuadros_copiados_de_odoo(self):
        s = self._sello("SKU-0010-NEG", canal="mercado_libre")
        b = s["pasos"]["bodega"]
        self.assertEqual((b["ubicacion"], b["stock"], b["foto"], b["specs"]),
                         ("listo", "listo", "listo", "espera"))
        self.assertEqual((b["n_listo"], b["en_odoo"], b["archivado"]),
                         (3, True, False))
        self.assertEqual(b["codigo_odoo"], "SKU-0010-NEG")

    def test_foto_en_espera_dice_foto_de_bodega_y_no_foto(self):
        espera = self._sello("HERM-0001-A")
        falta = self._sello("SKU-0041-NEG")    # sin quant: ubicación falta
        self.assertEqual(espera["pasos"]["bodega"]["foto"], "espera")
        self.assertIn("foto de bodega", espera["le_falta"])
        self.assertNotIn("foto", espera["le_falta"])
        self.assertEqual(falta["pasos"]["bodega"]["ubicacion"], "falta")
        self.assertIn("ubicación", falta["le_falta"])

    def test_fuera_de_odoo_no_es_cero_de_cuatro(self):
        s = self._sello("ROP-0695-BEI-m")
        b = s["pasos"]["bodega"]
        self.assertIs(b["en_odoo"], False)
        self.assertEqual(b["codigo_odoo"], None)
        self.assertIn("no existe en Odoo", s["le_falta"])
        # No se enumeran los cuadros: el hueco es que no existe, no que falte foto.
        self.assertNotIn("ubicación", s["le_falta"])

    def test_archivado_se_marca_aparte(self):
        b = self._sello("ARCH-0001")["pasos"]["bodega"]
        self.assertIs(b["en_odoo"], True)
        self.assertIs(b["archivado"], True)

    def test_odoo_caido_nunca_dice_sin_etapa_del_flujo(self):
        fp = _fp(_foto_omni(odoo=False))
        s = invf.sello(fp, "SKU-0090-NEG")     # sin recibido, sin full, sin drop
        self.assertEqual(s["etapa"], "sin_dato")
        self.assertEqual(s["etapa_texto"], "Sin dato del flujo")
        self.assertEqual(s["pasos"]["bodega"]["ubicacion"], "sin_dato")
        self.assertEqual(s["pasos"]["bodega"]["n_listo"], None)
        self.assertIn("sin dato de Odoo", s["le_falta"])

    # ── resolución del código ────────────────────────────────────────────────
    def test_citext_resuelve_por_la_escritura_de_kubera(self):
        s = self._sello("rop-0695-bei-M")
        self.assertEqual(s["pasos"]["recibido"]["estado"], "si")
        self.assertIs(s["en_catalogo"], True)

    def test_codigo_ambiguo_no_se_adivina(self):
        b = self._sello("amb-0001")["pasos"]["bodega"]
        self.assertEqual(b["codigo_odoo"], None)
        self.assertEqual(b["ubicacion"], "sin_dato")

    # ── etapa ────────────────────────────────────────────────────────────────
    def test_prioridad_de_etapa(self):
        self.assertEqual(self._sello("SKU-0002-NEG")["etapa"], "en_drop")
        self.assertEqual(self._sello("SKU-0055-NEG")["etapa"], "en_full")
        self.assertEqual(self._sello("SKU-0030-NEG")["etapa"], "bodega_3de4")
        self.assertEqual(self._sello("SKU-0045-NEG")["etapa"], "recibido")
        self.assertEqual(self._sello("SKU-0090-NEG")["etapa"], "ninguna")

    def test_en_full_lleva_ml_fuera_de_mercado_libre(self):
        for canal in ("amazon", "tiktok", "temu", "walmart"):
            self.assertEqual(self._sello("SKU-0055-NEG", canal=canal)["etapa_texto"],
                             "En FULL (ML)", canal)
        for canal in ("general", "mercado_libre"):
            self.assertEqual(self._sello("SKU-0055-NEG", canal=canal)["etapa_texto"],
                             "En FULL", canal)

    def test_escritura_distinta_no_cuenta_como_3de4(self):
        # SÍ está en core.products (como `Dist-0001`) y Odoo lo escribe
        # `DIST-0001`: la intersección de la foto es sensible a mayúsculas y lo
        # deja fuera de la lista. Ésa es la única causa de escritura.
        filas = _kubera_filas() + [{"sku": "Dist-0001", "recibido": True,
                                    "costo_validado": False, "en_full": False,
                                    "con_renglon": True, "wc_id": 9002,
                                    "wc_parent_id": None}]
        foto = invf.armar_foto(
            invf.Lectura(datos=filas, generado=T0),
            invf.Lectura(datos=_odoo_omni(), generado=T0),
            invf.Lectura(datos=({"SKU-0001-NEG"}, 0.0), generado=T0),
            None, ahora=T0,
            canales=invf.Lectura(datos=_canales_filas(), generado=T0))
        s = invf.sello(_fp(foto), "Dist-0001")
        b = s["pasos"]["bodega"]
        self.assertEqual((b["ubicacion"], b["stock"], b["foto"]),
                         ("listo", "listo", "listo"))
        self.assertIs(b["escritura_distinta"], True)
        self.assertNotEqual(s["etapa"], "bodega_3de4")
        self.assertIn("SKU escrito distinto en Odoo (DIST-0001)", s["le_falta"])

    def test_fuera_de_core_products_no_se_acusa_de_escritura(self):
        # `SOLO-ODOO` está IDÉNTICO en los dos lados: queda fuera de
        # `bodega_3de4` porque la lista se cruza contra core.products, no por
        # cómo se escribe. Acusarlo mandaría a corregir un nombre que ya casa.
        s = self._sello("SOLO-ODOO")
        b = s["pasos"]["bodega"]
        self.assertEqual((b["ubicacion"], b["stock"], b["foto"]),
                         ("listo", "listo", "listo"))
        self.assertIs(b["escritura_distinta"], False)
        self.assertIs(s["en_catalogo"], False)
        self.assertNotEqual(s["etapa"], "bodega_3de4")
        self.assertNotIn("SKU escrito distinto en Odoo (SOLO-ODOO)", s["le_falta"])
        self.assertIn("no está en el catálogo de kubera", s["le_falta"])

    # ── destino ──────────────────────────────────────────────────────────────
    def test_full_cuentas_sale_de_canales(self):
        self.assertEqual(self._sello("SKU-0055-NEG")["pasos"]["destino"]["full_cuentas"],
                         ["BEKURA", "SANCORFASHION"])
        sin = invf.sello(_fp(_foto_omni(canales=False)), "SKU-0055-NEG")
        self.assertIsNone(sin["pasos"]["destino"]["full_cuentas"],
                          "sin canales es «no sé», no «en ninguna»")

    def test_destino_sin_dato_se_declara_en_el_texto(self):
        s = invf.sello(_fp(_foto_omni(drop=False)), "SKU-0045-NEG")
        d = s["pasos"]["destino"]
        self.assertEqual((d["full"], d["drop"]), (False, None))
        self.assertIs(d["sin_dato"], True)
        self.assertTrue(s["etapa_texto"].endswith(" · destino sin dato"))

    # ── padres y SKUs sintéticos ─────────────────────────────────────────────
    def test_padre_no_se_juzga_con_las_reglas_de_una_pieza(self):
        s = self._sello("SKU-0001-NEG")
        self.assertEqual(s["etapa"], "padre")
        self.assertEqual(s["le_falta"], [])
        for paso in ("recibido", "listo"):
            self.assertEqual(s["pasos"][paso]["estado"], "na")
        self.assertEqual(s["pasos"]["bodega"]["ubicacion"], "na")
        self.assertEqual(s["variantes"],
                         {"total": 2, "recibido": 2, "bodega_3de4": 2,
                          "en_full": 0, "en_fba": 0, "en_drop": 1, "sin_dato": 0})
        self.assertTrue(s["etapa_texto"].startswith("Padre · "))
        self.assertIn("de 2", s["etapa_texto"])

    def test_sku_sintetico_y_vacio_no_llevan_sello(self):
        self.assertIsNone(self._sello("WC-123"))
        self.assertIsNone(self._sello(""))
        self.assertIsNone(self._sello("wc-99"))

    def test_sin_foto_usable_no_hay_sello(self):
        self.assertIsNone(invf.sello(invf.FOTO_APAGADA, "SKU-0001-NEG"))
        calentando = invf.FotoPeticion(None, None, "calentando", "…", None)
        self.assertIsNone(invf.sello(calentando, "SKU-0001-NEG"))

    def test_resumen_variantes_cuenta_pertenencia(self):
        sellos = [self._sello(f"SKU-{i:04d}-NEG") for i in (2, 30, 55, 90)]
        self.assertEqual(invf.resumen_variantes(sellos),
                         {"total": 4, "recibido": 3, "bodega_3de4": 2,
                          "en_full": 1, "en_fba": 0, "en_drop": 1, "sin_dato": 0})

    def test_le_falta_nunca_habla_de_destino_costo_ni_restock(self):
        prohibidas = ("costo", "precio", "flete", "margen", "FULL", "DROP",
                      "restock")
        for sku in ("SKU-0001-NEG", "SKU-0055-NEG", "SKU-0090-NEG",
                    "ROP-0695-BEI-m", "SOLO-ODOO", "HERM-0001-A"):
            for texto in self._sello(sku)["le_falta"]:
                for palabra in prohibidas:
                    if palabra in ("costo",) and "en costos" in texto:
                        continue      # «sin cajas en costos» es el motivo, no un monto
                    self.assertNotIn(palabra, texto, f"{sku}: {texto}")


# ─────────────────────────────────────────────────────────────────────────────
# 2 · LISTA DE ETAPA, PADRES E IDS
# ─────────────────────────────────────────────────────────────────────────────

class ListaEtapaYIds(_Base):
    def test_lista_por_estado(self):
        fp = _fp()
        self.assertEqual(len(invf.lista_etapa(fp, "recibido").skus_mayus), 66,
                         "61 del packing list ∪ 10 con empaque de Odoo (5 nuevos)")
        self.assertEqual(invf.lista_etapa(fp, "recibido").estado, "listo")
        sin_k = invf.lista_etapa(_fp(_foto_omni(kubera=False)), "recibido")
        self.assertEqual(sin_k.estado, "sin_dato")
        self.assertIn("sin dato de kubera", sin_k.motivo)
        self.assertEqual(sin_k.skus_mayus, frozenset())

    def test_general_exige_kubera_aunque_la_etapa_sea_de_odoo(self):
        fp = _fp(_foto_omni(kubera=False))
        self.assertEqual(invf.lista_etapa(fp, "bodega_3de4").estado, "listo")
        self.assertEqual(
            invf.lista_etapa(fp, "bodega_3de4", requiere_kubera=True).estado,
            "sin_dato")

    def test_apagada_y_calentando_se_distinguen(self):
        self.assertEqual(invf.lista_etapa(invf.FOTO_APAGADA, "recibido").estado,
                         "apagado")
        cal = invf.FotoPeticion(None, None, "calentando", "armando", None)
        self.assertEqual(invf.lista_etapa(cal, "recibido").estado, "calentando")

    def test_ids_woo_colapsan_la_variante_al_padre(self):
        fp = _fp()
        prod, filas = invf.ids_woo(fp, {"SKU-0002-NEG", "SKU-0003-NEG"})
        self.assertEqual(prod, [1001], "las dos variantes son UN producto de Woo")
        self.assertEqual(filas, [1002, 1003])

    def test_expandir_padres_suma_las_variantes(self):
        fp = _fp()
        self.assertEqual(invf.expandir_padres(fp, ["sku-0001-neg"]),
                         {"SKU-0001-NEG", "SKU-0002-NEG", "SKU-0003-NEG"})
        # Un SKU que no es padre se queda como está.
        self.assertEqual(invf.expandir_padres(fp, ["SKU-0030-NEG"]),
                         {"SKU-0030-NEG"})

    def test_flag_apagado_devuelve_la_constante_sin_tocar_la_foto(self):
        centinela = object()
        invf._foto = centinela
        with mock.patch.object(invf.settings, "inventario_flujo_enabled", False), \
                mock.patch.object(invf, "calentar_en_fondo",
                                  side_effect=AssertionError("calentó")):
            fp = invf.foto_para_peticion()
        self.assertIs(fp, invf.FOTO_APAGADA)
        self.assertEqual(fp.estado, "apagado")
        self.assertIs(invf._foto, centinela, "no se tocó la foto")


# ─────────────────────────────────────────────────────────────────────────────
# 3 · CONTEOS POR CANAL
# ─────────────────────────────────────────────────────────────────────────────

def _conteo(foto=None, *, canal="mercado_libre", cuenta=None, criterio="todas",
            aplanado=False, lee_publicaciones=True, flag=True):
    return invf._conteos_canal(_foto_omni() if foto is None else foto, T0,
                               canal=canal, cuenta=cuenta, criterio=criterio,
                               aplanado=aplanado,
                               lee_publicaciones=lee_publicaciones, flag=flag)


def _etapa(resp, clave):
    if resp["carril"]["clave"] == clave:
        return resp["carril"]
    return next(e for e in resp["etapas"] if e["clave"] == clave)


class BodegaDelCanal(_Base):
    """Cada pestaña pinta SU bodega del marketplace, o ninguna (Eduardo, 17-sep).

    FULL es de Mercado Libre y FBA de Amazon: son bodegas distintas, con dato
    distinto. TikTok y Temu despachan de nuestro almacén y de Walmart WFS no hay
    dato, así que ahí el segmento no existe — un cero se leería como «ninguno»
    cuando lo cierto es «no se mide»."""

    def _claves(self, canal):
        return [e["clave"] for e in _conteo(canal=canal)["etapas"]]

    def test_ml_y_general_pintan_full(self):
        for canal in ("mercado_libre", "general"):
            self.assertIn("en_full", self._claves(canal), canal)
            self.assertNotIn("en_fba", self._claves(canal), canal)

    def test_amazon_pinta_fba_y_no_full(self):
        claves = self._claves("amazon")
        self.assertIn("en_fba", claves)
        self.assertNotIn("en_full", claves)
        # Y va en el MISMO lugar del camino que ocupaba FULL: entre Listo y DROP.
        self.assertEqual(claves.index("en_fba"), claves.index("en_drop") - 1)
        self.assertEqual(_etapa(_conteo(canal="amazon"), "en_fba")["titulo"], "En FBA")

    def test_los_canales_sin_bodega_no_la_pintan(self):
        for canal in ("tiktok", "temu", "walmart"):
            claves = self._claves(canal)
            self.assertNotIn("en_full", claves, canal)
            self.assertNotIn("en_fba", claves, canal)
            # El resto del camino sigue completo: lo que falta es la bodega.
            self.assertEqual(claves, ["recibido", "bodega_3de4", "validado_bodega",
                                      "listo_envio", "en_drop", "restock"], canal)

    def test_el_sello_dice_fba_y_full_por_separado(self):
        # SKU-0071: solo FBA. SKU-0069: en las dos, y gana FULL.
        solo_fba = invf.sello(_fp(), "SKU-0071-NEG", canal="amazon")
        self.assertEqual(solo_fba["etapa"], "en_fba")
        self.assertEqual(solo_fba["etapa_texto"], "En FBA")
        self.assertIs(solo_fba["pasos"]["destino"]["fba"], True)
        self.assertIs(solo_fba["pasos"]["destino"]["full"], False)
        # En las dos bodegas: la etapa la gana FULL, pero en la pestaña de
        # Amazon el texto empieza por SU bodega y menciona la otra.
        ambas = invf.sello(_fp(), "SKU-0069-NEG", canal="amazon")
        self.assertEqual(ambas["etapa"], "en_full")
        self.assertEqual(ambas["etapa_texto"], "En FBA · también FULL (ML)")
        self.assertEqual(invf.sello(_fp(), "SKU-0069-NEG",
                                    canal="mercado_libre")["etapa_texto"], "En FULL")
        # Fuera de Amazon, «En FBA» lleva de quién es la bodega.
        fuera = invf.sello(_fp(), "SKU-0071-NEG", canal="tiktok")
        self.assertEqual(fuera["etapa_texto"], "En FBA (Amazon)")


class ConteosCanal(_Base):
    def test_ml_con_cuenta_cuenta_publicaciones(self):
        r = _conteo(cuenta="BEKURA")
        self.assertEqual(r["total"], 60)
        self.assertEqual(r["unidad"], "publicacion")
        self.assertIs(r["unidad_aprox"], False)
        self.assertEqual(_etapa(r, "recibido")["n"], 60)
        self.assertEqual(_etapa(r, "bodega_3de4")["n"], 40)
        self.assertEqual(_etapa(r, "en_full")["n"], 10, "51–60 de los 51–70")
        self.assertEqual(_etapa(r, "en_drop")["n"], 2)
        self.assertEqual(_etapa(r, "costo_validado")["n"], 5)
        self.assertEqual(r["carril"]["param"], "revisado")

    def test_ml_sin_cuenta_suma_las_dos(self):
        r = _conteo()
        self.assertEqual(r["total"], 85, "60 de BEKURA + 25 de SANCOR")
        self.assertEqual(_etapa(r, "recibido")["n"], 85,
                         "en publicaciones, no en SKUs")
        self.assertEqual(r["cuenta"], None)

    def test_los_criterios_siguen_a_la_lista(self):
        self.assertEqual(_conteo(cuenta="BEKURA", criterio="publicados")["total"], 45)
        self.assertEqual(_conteo(cuenta="BEKURA", criterio="activas")["total"], 30)

    def test_temu_con_publicados_declara_que_cuenta_todas(self):
        r = _conteo(canal="temu", criterio="publicados")
        self.assertEqual(r["criterio_efectivo"], "todas")
        self.assertEqual(r["total"], 5)

    def test_activas_se_degrada_donde_el_canal_no_sabe(self):
        foto = _foto_omni()
        crudo = _canales_filas()
        crudo["sin_activas"] = ["walmart"]
        foto = invf.armar_foto(
            invf.Lectura(datos=_kubera_filas(), generado=T0),
            invf.Lectura(datos=_odoo_omni(), generado=T0),
            invf.Lectura(datos=(set(), 0.0), generado=T0), None, ahora=T0,
            canales=invf.Lectura(datos=crudo, generado=T0))
        r = _conteo(foto, canal="walmart", criterio="activas")
        self.assertEqual(r["criterio_efectivo"], "todas")
        self.assertEqual(r["total"], 7)

    def test_reglas_de_inclusion_de_cada_rejilla(self):
        # ML sin cuenta no se pagina; TikTok hace join a core.products.
        self.assertEqual(_conteo()["total"], 85)
        self.assertEqual(_conteo(canal="tiktok")["total"], 10)
        # Amazon no filtra por catálogo: la fila de fuera SÍ cuenta.
        self.assertEqual(_conteo(canal="amazon")["total"], 31)

    def test_general_cuenta_productos_de_woo_y_lo_declara(self):
        r = _conteo(canal="general")
        self.assertEqual(r["unidad"], "producto_woo")
        self.assertIs(r["unidad_aprox"], True)
        self.assertIsNone(r["total"], "el total lo manda la rejilla, no el conteo")
        # Los 66 SKUs de Recibido son 64 productos de Woo: SKU-0002 y SKU-0003
        # colapsan en su padre (1001), que además es Recibido por sí mismo.
        self.assertEqual(_etapa(r, "recibido")["n"], 64)

    def test_general_aplanado_cuenta_filas_sin_padres(self):
        r = _conteo(canal="general", aplanado=True)
        self.assertEqual(r["unidad"], "fila_woo")
        self.assertEqual(_etapa(r, "recibido")["n"], 65, "el padre no es fila")

    def test_canales_caido_apaga_la_cifra_pero_no_el_clic(self):
        r = _conteo(_foto_omni(canales=False), cuenta="BEKURA")
        self.assertIsNone(r["total"])
        for clave in ("recibido", "bodega_3de4", "en_full", "en_drop"):
            e = _etapa(r, clave)
            self.assertIsNone(e["n"], clave)
            self.assertIn("sin conteo por canal", e["n_motivo"], clave)
            self.assertIs(e["clicable"], True, clave)

    def test_general_sin_kubera_deja_todas_las_cifras_en_null(self):
        r = _conteo(_foto_omni(kubera=False), canal="general")
        for clave in ("recibido", "bodega_3de4", "en_full", "en_drop"):
            e = _etapa(r, clave)
            self.assertIsNone(e["n"], clave)
            self.assertIn("no hay wc_id", e["n_motivo"], clave)
        self.assertIs(_etapa(r, "en_drop")["clicable"], True)
        self.assertIs(_etapa(r, "bodega_3de4")["clicable"], False,
                      "sin wc_id no hay cómo filtrar General por la etapa")

    def test_canal_que_no_puede_filtrar_no_es_clicable(self):
        shein = _conteo(canal="shein")
        self.assertEqual(shein["estado"], "sin_dato")
        self.assertIn("datos de ejemplo", shein["motivo"])
        mysql = _conteo(lee_publicaciones=False)
        self.assertEqual(mysql["estado"], "sin_dato")
        for r in (shein, mysql):
            for e in r["etapas"] + [r["carril"]]:
                self.assertIs(e["clicable"], False, e["clave"])
                self.assertIsNone(e["n"], e["clave"])

    def test_flag_apagado_y_calentando_dejan_en_drop_y_el_carril(self):
        apagado = _conteo(flag=False)
        self.assertEqual(apagado["estado"], "apagado")
        calentando = invf._conteos_canal(None, T0, canal="mercado_libre",
                                         cuenta=None, criterio="todas",
                                         aplanado=False, lee_publicaciones=True,
                                         flag=True)
        self.assertEqual(calentando["estado"], "calentando")
        for r in (apagado, calentando):
            self.assertIsNone(r["total"])
            self.assertIs(_etapa(r, "en_drop")["clicable"], True)
            self.assertIs(r["carril"]["clicable"], True)
            self.assertIs(_etapa(r, "recibido")["clicable"], False)
            self.assertIsNone(_etapa(r, "recibido")["n"])

    def test_bloqueadas_y_por_definir(self):
        r = _conteo(cuenta="BEKURA")
        self.assertEqual(_etapa(r, "validado_bodega")["n"], 0)
        self.assertIn("specs", _etapa(r, "validado_bodega")["motivo"])
        listo = _etapa(r, "listo_envio")
        self.assertIsNone(listo["n"])
        self.assertEqual(listo["n_sin_specs"], 40, "Recibido ∩ 3 de 4 en BEKURA")
        restock = _etapa(r, "restock")
        self.assertIsNone(restock["n"])
        self.assertIs(restock["clicable"], False)

    def test_catalogo_lleva_las_cifras_del_universo(self):
        r = _conteo(cuenta="BEKURA")
        self.assertEqual(r["catalogo"]["recibido"], 66)
        self.assertEqual(r["catalogo"]["recibido_y_3de4"], 40)
        self.assertEqual(r["ttl_s"], invf.TTL_S)
        self.assertEqual(r["generado"], "2026-09-15T15:40:00Z")


# ─────────────────────────────────────────────────────────────────────────────
# 4 · LA CUARTA FUENTE
# ─────────────────────────────────────────────────────────────────────────────

class FuenteCanales(_Base):
    def test_derivar_agrupa_por_canal_cuenta_y_criterio(self):
        d = invf._derivar_canales(_canales_filas())
        self.assertEqual(sum(d.publicaciones[("mercado_libre", "BEKURA", "todas")].values()), 60)
        self.assertEqual(d.publicaciones[("mercado_libre", "*", "todas")]["SKU-0055-NEG"], 2)
        self.assertNotIn("SKU-0099-NEG", d.publicaciones[("mercado_libre", "*", "todas")])
        self.assertEqual(d.full_cuentas["SKU-0055-NEG"], ("BEKURA", "SANCORFASHION"))
        self.assertNotIn("FUERA-DEL-CATALOGO",
                         d.publicaciones[("tiktok", "*", "todas")])
        self.assertIn("FUERA-DEL-CATALOGO",
                      d.publicaciones[("amazon", "*", "todas")])

    def test_tamanos_vigila_el_universo_de_cada_canal(self):
        d = invf._derivar_canales(_canales_filas())
        self.assertEqual(d.tamanos()["tiktok"], 10)
        self.assertEqual(d.tamanos()["walmart"], 7)

    def _con_anterior(self, filas: dict, anterior: invf.Foto) -> invf.Foto:
        return invf.armar_foto(
            invf.Lectura(datos=_kubera_filas(), generado=T0),
            invf.Lectura(datos=_odoo_omni(), generado=T0),
            invf.Lectura(datos=({"SKU-0001-NEG"}, 0.0), generado=T0),
            anterior, ahora=T0, canales=invf.Lectura(datos=filas, generado=T0))

    def test_un_canal_que_desaparece_es_la_caida_mas_sospechosa(self):
        # Las llaves de `DatosCanales.tamanos()` son DINÁMICAS: el canal que cae
        # a cero filas no aparece en la lectura nueva. Mirando solo las llaves
        # nuevas nunca se comparaba y el desplome total —el peor— era el único
        # que pasaba como bueno.
        completa = _foto_omni()
        sin_ml = {"filas": [f for f in _canales_filas()["filas"]
                            if f["canal"] != "mercado_libre"],
                  "sin_activas": []}
        f = self._con_anterior(sin_ml, completa).fuentes["canales"]
        self.assertTrue(f.sospechosa)
        self.assertIn("mercado_libre 60 → 0", f.error)
        self.assertIs(f.datos, completa.fuentes["canales"].datos,
                      "se conserva la lectura anterior")

    def test_la_misma_caida_repetida_se_acepta(self):
        # La guarda retiene UNA vez: si la siguiente lectura repite la cifra, ya
        # no es un tropiezo del sync y se cree.
        completa = _foto_omni()
        sin_ml = {"filas": [f for f in _canales_filas()["filas"]
                            if f["canal"] != "mercado_libre"],
                  "sin_activas": []}
        sospechosa = self._con_anterior(sin_ml, completa)
        f = self._con_anterior(sin_ml, sospechosa).fuentes["canales"]
        self.assertFalse(f.sospechosa)
        self.assertTrue(f.ok)

    def test_sql_interpola_los_predicados_de_cada_rejilla(self):
        sql, params, sin_activas = invf._sql_canales()
        una = " ".join(sql.split())
        self.assertIn("from channel.listings l", una)
        self.assertIn("left join core.accounts a on a.id = l.account_id", una)
        self.assertIn("left join core.products p on p.sku = l.sku", una)
        self.assertIn("where l.canal <> 'general'", una)
        self.assertIn(" ".join(channel_read._PUB_ML.split()), una)
        self.assertIn("when 'temu' then true", una)
        self.assertIn(tiktok_panel.filtro_sql_publicado("l", "pub_tiktok")[0], una)
        self.assertIn(walmart_panel.filtro_sql_publicado("l", "pub_walmart")[0], una)
        self.assertEqual(params["pub_tiktok"], tiktok_panel.ESTADO_VIVO)
        self.assertIn("act_mercado_libre", params)
        self.assertEqual(sin_activas, [])

    def test_leer_canales_usa_timeout_local_y_nunca_set_session(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = [{"sku": "A", "canal": "amazon",
                                         "cuenta": "", "publicado": True,
                                         "activa": False, "full": False,
                                         "en_catalogo": True}]

        class _Ctx:
            def __enter__(self):
                return cursor

            def __exit__(self, *a):
                return False

        with mock.patch.object(invf.sdb, "disponible", return_value=True), \
                mock.patch.object(invf.sdb, "get_cursor", return_value=_Ctx()):
            crudo = invf._leer_canales()
        self.assertEqual(crudo["filas"][0]["sku"], "A")
        primera = cursor.execute.call_args_list[0][0][0]
        self.assertIn("set_config('statement_timeout'", primera)
        self.assertIn(", true)", primera)
        todo = " ".join(str(c) for c in cursor.execute.call_args_list).lower()
        self.assertNotIn("set_session", todo)
        self.assertNotIn("read only", todo)

    def test_canales_vacio_es_falla_no_cero(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = []

        class _Ctx:
            def __enter__(self):
                return cursor

            def __exit__(self, *a):
                return False

        with mock.patch.object(invf.sdb, "disponible", return_value=True), \
                mock.patch.object(invf.sdb, "get_cursor", return_value=_Ctx()), \
                self.assertRaises(RuntimeError):
            invf._leer_canales()

    def test_una_falla_de_canales_deja_sanas_a_las_otras_tres(self):
        foto = _foto_omni(canales=False)
        self.assertFalse(foto.fuentes["canales"].ok)
        for n in invf.FUENTES_BARRA:
            self.assertTrue(foto.fuentes[n].ok, n)
        self.assertEqual(len(foto.listas["recibido"]), 66)


# ─────────────────────────────────────────────────────────────────────────────
# 5 · EL CONTRATO DE `/flujo` (CATÁLOGO ENTERO) NO CAMBIA
# ─────────────────────────────────────────────────────────────────────────────

class ContratoInventarioIntacto(_Base):
    def test_flujo_sigue_viendo_tres_fuentes(self):
        invf._foto = _foto_omni()
        with mock.patch.object(invf, "_ahora", return_value=T0):
            r = invf.conteos()
        self.assertEqual(set(r["fuentes"]), {"kubera", "odoo", "odoo_drop"})
        self.assertEqual(r["estado"], "listo")

    def test_canales_vieja_no_pone_vieja_la_foto(self):
        # Una lectura sospechosa de canales conserva la anterior y marca `vieja`.
        anterior = _foto_omni()
        foto = invf.armar_foto(
            invf.Lectura(datos=_kubera_filas(), generado=T0),
            invf.Lectura(datos=_odoo_omni(), generado=T0),
            invf.Lectura(datos=({"SKU-0001-NEG"}, 0.0), generado=T0),
            anterior, ahora=T0,
            canales=invf.Lectura(error="RuntimeError: caída", generado=T0))
        self.assertTrue(foto.fuentes["canales"].vieja)
        invf._foto = foto
        with mock.patch.object(invf, "_ahora", return_value=T0):
            self.assertEqual(invf.conteos()["estado"], "listo")

    def test_skus_de_etapa_no_cambia(self):
        invf._foto = _foto_omni()
        with mock.patch.object(invf, "_ahora", return_value=T0):
            r = invf.skus_de_etapa("en_full", 1, 5)
        self.assertEqual(r["total"], 20)
        self.assertEqual(r["skus"][0], "SKU-0051-NEG")


# ─────────────────────────────────────────────────────────────────────────────
# 6 · GET /api/productos?etapa=
# ─────────────────────────────────────────────────────────────────────────────

def _cliente_productos() -> TestClient:
    app = FastAPI()
    app.include_router(ruta_prod.router)
    return TestClient(app)


class ProductosEtapa(_Base):
    def setUp(self):
        super().setUp()
        self.c = _cliente_productos()
        invf._foto = _foto_omni()
        self.llamadas: dict[str, dict] = {}

        def _panel(page=1, per_page=40, search=None, solo_publicados=False, **kw):
            self.llamadas["panel"] = kw
            return ([{"sku": "SKU-0055-NEG", "nombre": "x", "wc_id": 1055}], 7)

        def _ml(page=1, per_page=40, search=None, solo_publicados=False,
                cuenta=None, **kw):
            self.llamadas["ml"] = {**kw, "cuenta": cuenta}
            return ([{"sku": "SKU-0055-NEG", "nombre": "x", "wc_id": 1055}], 1)

        async def _woo(**kw):
            self.llamadas["woo"] = kw
            return ([{"sku": "SKU-0002-NEG", "nombre": "x", "wc_id": 1002,
                      "variantes": []}], 1, 1)

        self.panel, self.ml, self.woo = _panel, _ml, _woo
        parches = [
            mock.patch.object(ruta_prod.woocommerce, "listar_productos",
                              side_effect=_woo),
            mock.patch.object(ruta_prod.woocommerce, "imagenes_por_wc_id",
                              new=mock.AsyncMock(return_value={})),
            mock.patch.object(ruta_prod.meli, "listar", side_effect=_ml),
            mock.patch.object(ruta_prod.amazon, "listar", side_effect=_ml),
            mock.patch.object(tiktok_panel, "listar", side_effect=_panel),
            mock.patch.object(temu_panel, "listar", side_effect=_panel),
            mock.patch.object(walmart_panel, "listar", side_effect=_panel),
            mock.patch.object(ruta_prod.costing_read, "revisados_por_sku",
                              return_value={}),
            mock.patch.object(ruta_prod.costing_read, "validados_de",
                              return_value={}),
            mock.patch.object(ruta_prod.costing_read, "skus_revisados",
                              return_value=["SKU-0002-NEG", "SKU-0055-NEG"]),
            mock.patch.object(ruta_prod.odoo, "estado_almacen",
                              return_value=({"SKU-0002-NEG", "SKU-0055-NEG"}, 0.0)),
            mock.patch.object(ruta_prod.odoo, "skus_por_almacen",
                              return_value=["SKU-0002-NEG"]),
            mock.patch.object(ruta_prod.channel_read, "hijos_por_wc_id",
                              return_value={}),
            mock.patch.object(ruta_prod.channel_read, "rutas_de", return_value={}),
            mock.patch.object(ruta_prod.inventario, "leer_inventario",
                              return_value={}),
            mock.patch.object(ruta_prod.presencia, "presencia_por_sku",
                              return_value={}),
            mock.patch.object(ruta_prod.wp_db, "disponible", return_value=True),
            mock.patch.object(invf.settings, "inventario_flujo_enabled", True),
            mock.patch.object(invf.settings, "supabase_read_publicaciones", True),
            mock.patch.object(invf, "_ahora", return_value=T0),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

    def _get(self, **params):
        params.setdefault("canal", "tiktok")
        params.setdefault("vista", "omnicanal")
        return self.c.get("/api/productos", params=params)

    # ── 400 ──────────────────────────────────────────────────────────────────
    def test_etapas_invalidas_dan_400_con_su_motivo(self):
        casos = {
            "validado_bodega": "usa etapa=bodega_3de4",
            "listo_envio": "Depende de Validado bodega 4 de 4",
            "restock": "etapa por definir, sin lista",
            "costo_validado": "usa revisado=true",
            "inventada": "etapa desconocida",
        }
        for etapa, texto in casos.items():
            r = self._get(etapa=etapa)
            self.assertEqual(r.status_code, 400, etapa)
            self.assertIn(texto, r.json()["detail"], etapa)
        self.assertNotIn("panel", self.llamadas, "400 antes de cualquier I/O")

    # ── total y forma ────────────────────────────────────────────────────────
    def test_el_total_sale_del_subconjunto(self):
        r = self._get(etapa="en_full")
        self.assertEqual(r.status_code, 200)
        cuerpo = r.json()
        self.assertEqual(cuerpo["paginacion"]["total"], 7)
        self.assertEqual(cuerpo["filtro_etapa"],
                         {"etapa": "en_full", "fuente": "foto",
                          "generado": "2026-09-15T15:40:00Z", "vieja": False,
                          "n_skus": 20})
        self.assertEqual(cuerpo["flujo_estado"], "listo")
        self.assertEqual(cuerpo["flujo_generado"], "2026-09-15T15:40:00Z")
        # Los 20 SKUs de En FULL bajan exactos y en mayúsculas.
        self.assertEqual(len(self.llamadas["panel"]["skus_filtro"]), 20)
        self.assertIs(self.llamadas["panel"]["estricto"], True)

    def test_ml_y_amazon_reciben_skus_exactos(self):
        for canal in ("mercado_libre", "amazon"):
            self._get(canal=canal, etapa="bodega_3de4")
            self.assertIs(self.llamadas["ml"]["skus_exactos"], True, canal)
            self.assertEqual(len(self.llamadas["ml"]["skus_filtro"]), 40, canal)

    def test_general_baja_por_ids_y_sin_skus(self):
        r = self._get(canal="general", etapa="recibido")
        self.assertEqual(r.status_code, 200)
        kw = self.llamadas["woo"]
        self.assertIsNone(kw["skus"], "la lista viaja como ids, no como SKUs")
        self.assertEqual(len(kw["ids_productos"]), 64)
        self.assertEqual(len(kw["ids_filas"]), 66)
        self.assertIs(kw["estricto"], True)

    def test_general_en_drop_baja_por_sku_exacto(self):
        self._get(canal="general", etapa="en_drop")
        kw = self.llamadas["woo"]
        self.assertEqual(kw["skus"], ["SKU-0002-NEG", "SKU-0055-NEG"])
        self.assertIs(kw["skus_exactos"], True)
        self.assertIs(kw["estricto"], True)
        self.assertIsNone(kw["ids_productos"])

    # ── 503 ──────────────────────────────────────────────────────────────────
    def test_shein_y_mysql_no_pueden_filtrar(self):
        r = self._get(canal="shein", etapa="en_full")
        self.assertEqual(r.status_code, 503)
        self.assertIn("datos de ejemplo", r.json()["detail"])
        with mock.patch.object(invf.settings, "supabase_read_publicaciones", False):
            r2 = self._get(canal="mercado_libre", etapa="en_full")
        self.assertEqual(r2.status_code, 503)
        self.assertIn("SUPABASE_READ_PUBLICACIONES", r2.json()["detail"])

    def test_foto_calentando_no_llama_al_canal(self):
        invf._foto = None
        r = self._get(etapa="recibido")
        self.assertEqual(r.status_code, 503)
        self.assertIn("se está armando", r.json()["detail"])
        self.assertIn("Quita el filtro de etapa", r.json()["detail"])
        self.assertNotIn("panel", self.llamadas)

    def test_flag_apagado_bloquea_la_foto_pero_no_en_drop(self):
        with mock.patch.object(invf.settings, "inventario_flujo_enabled", False):
            r = self._get(etapa="recibido")
            self.assertEqual(r.status_code, 503)
            self.assertIn("INVENTARIO_FLUJO_ENABLED=false", r.json()["detail"])
            r2 = self._get(etapa="en_drop")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["filtro_etapa"]["fuente"], "odoo_drop")
        self.assertEqual(r2.json()["flujo_estado"], "apagado")
        self.assertIsNone(r2.json()["items"][0]["flujo"], "sin foto no hay sello")

    def test_modo_legado_conserva_los_dos_chips_de_siempre(self):
        # Producción viene con la foto APAGADA y `SUPABASE_READ_PUBLICACIONES`
        # en false: ahí /omnicanal pinta los dos chips de siempre y el de DROP
        # manda `etapa=en_drop`. Si las guardas de «este canal no filtra
        # exacto» corrieran igual, el primer día del despliegue el chip
        # quedaría roto en Shein, ML y Amazon — que hoy contestan 200.
        with mock.patch.object(invf.settings, "inventario_flujo_enabled", False), \
                mock.patch.object(invf.settings, "supabase_read_publicaciones",
                                  False):
            for canal in ("shein", "mercado_libre", "amazon"):
                self.assertEqual(self._get(canal=canal, etapa="en_drop").status_code,
                                 200, canal)
                self.assertEqual(
                    self._get(canal=canal, revisado=True).status_code, 200, canal)
            # La etapa de FOTO sí se niega: nace exacta y no hay lista que dar.
            self.assertEqual(self._get(canal="shein", etapa="recibido").status_code,
                             503)

    def test_con_la_foto_encendida_el_contrato_no_cambia(self):
        # El mismo chip, con el stepper en pantalla, sigue dando 503 donde el
        # canal no puede filtrar exacto (contrato_api §503, casos 1 y 2).
        r = self._get(canal="shein", etapa="en_drop")
        self.assertEqual(r.status_code, 503)
        self.assertIn("datos de ejemplo", r.json()["detail"])
        with mock.patch.object(invf.settings, "supabase_read_publicaciones", False):
            r2 = self._get(canal="mercado_libre", etapa="en_drop")
        self.assertEqual(r2.status_code, 503)

    def test_en_drop_declara_la_edad_de_la_lectura_de_odoo(self):
        # `estado_almacen` sirve su última lectura buena sin tope de edad
        # cuando Odoo falla: afirmar `vieja: false` diría que la foto del
        # almacén es de ahora cuando puede ser de hace horas.
        with mock.patch.object(ruta_prod.odoo, "estado_almacen",
                               return_value=({"SKU-0002-NEG"}, invf.TTL_S + 60)):
            vieja = self._get(etapa="en_drop")
        self.assertIs(vieja.json()["filtro_etapa"]["vieja"], True)
        self.assertIs(self._get(etapa="en_drop").json()["filtro_etapa"]["vieja"],
                      False)

    def test_odoo_caido_da_503_y_no_cero_productos(self):
        with mock.patch.object(ruta_prod.odoo, "estado_almacen",
                               side_effect=RuntimeError("Odoo no contesta")):
            r = self._get(etapa="en_drop")
        self.assertEqual(r.status_code, 503)
        self.assertIn("almacén DROP OFF", r.json()["detail"])
        self.assertIn("Quita el filtro En DROP", r.json()["detail"])

    def test_dependencia_sin_dato_explica_cual(self):
        invf._foto = _foto_omni(odoo=False)
        r = self._get(etapa="bodega_3de4")
        self.assertEqual(r.status_code, 503)
        self.assertIn("sin dato de odoo", r.json()["detail"])

    def test_recibido_sin_odoo_da_503_y_no_una_lista_a_medias(self):
        """Recibido depende de las DOS fuentes desde el 18-sep. Con Odoo caído
        la lista de kubera existe pero le faltan los que solo trae el empaque:
        filtrar con ella diría «no hay» de productos que sí están en la etapa.
        Sigue siendo filtrable —con las dos fuentes sanas contesta 200—, lo que
        cambia es que ahora también se niega cuando Odoo no está."""
        invf._foto = _foto_omni(odoo=False)
        r = self._get(etapa="recibido")
        self.assertEqual(r.status_code, 503)
        self.assertIn("sin dato de odoo", r.json()["detail"])
        self.assertIn("Quita el filtro de etapa", r.json()["detail"])
        invf._foto = _foto_omni()
        self.assertEqual(self._get(etapa="recibido").status_code, 200)

    def test_el_canal_que_falla_con_etapa_da_503_y_sin_ella_sigue_como_hoy(self):
        # Con el panel REAL: su `except` devuelve `[], 0` ante cualquier falla,
        # y con una etapa puesta eso diría «ninguno en esta etapa».
        with mock.patch.object(tiktok_panel, "listar", _LISTAR_TIKTOK),                 mock.patch.object(tiktok_panel.sdb, "fetch_all",
                                  side_effect=RuntimeError("kubera no contesta")):
            con = self._get(etapa="en_full")
            sin = self._get()
        self.assertEqual(con.status_code, 503)
        self.assertIn("No se pudo leer tiktok con el filtro.", con.json()["detail"])
        self.assertEqual(sin.status_code, 200, "sin etapa, exactamente como hoy")
        self.assertEqual(sin.json()["paginacion"]["total"], 0)

    def test_general_sin_wordpress_lo_dice(self):
        with mock.patch.object(ruta_prod.wp_db, "disponible", return_value=False):
            r = self._get(canal="general", etapa="recibido")
        self.assertEqual(r.status_code, 503)
        self.assertIn("Sin la base de WordPress", r.json()["detail"])

    def test_general_con_filtro_exacto_fallido_da_503(self):
        async def _lanza(**kw):
            raise wp_db.FiltroExactoNoAplicable("Hostinger cortó")

        with mock.patch.object(ruta_prod.woocommerce, "listar_productos",
                               side_effect=_lanza):
            r = self._get(canal="general", etapa="en_drop")
        self.assertEqual(r.status_code, 503)
        self.assertIn("WordPress no respondió con el filtro exacto",
                      r.json()["detail"])

    def test_foto_que_lanza_no_rompe_el_listado(self):
        with mock.patch.object(invf, "foto_para_peticion",
                               side_effect=RuntimeError("boom")):
            r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["flujo_estado"], "apagado")
        self.assertIsNone(r.json()["items"][0]["flujo"])

    # ── intersección con los demás filtros ───────────────────────────────────
    def test_la_etapa_se_cruza_con_los_skus_escritos(self):
        r = self._get(etapa="en_full", skus="sku-0055-neg, SKU-0001-NEG")
        self.assertEqual(r.json()["filtro_etapa"]["n_skus"], 1)
        self.assertEqual(self.llamadas["panel"]["skus_filtro"], ["SKU-0055-NEG"])

    def test_un_termino_parcial_con_etapa_da_cero_sin_llamar_al_canal(self):
        r = self._get(etapa="en_full", skus="SKU-005")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["paginacion"]["total"], 0)
        self.assertEqual(r.json()["filtro_etapa"]["n_skus"], 0)
        self.assertNotIn("panel", self.llamadas)

    def test_en_general_el_sku_padre_arrastra_a_sus_hijos(self):
        r = self._get(canal="general", etapa="recibido", skus="SKU-0001-NEG")
        self.assertEqual(r.json()["filtro_etapa"]["n_skus"], 3)
        self.assertEqual(self.llamadas["woo"]["ids_productos"], [1001])

    def test_revisado_mas_etapa_intersecta(self):
        r = self._get(etapa="en_full", revisado="true")
        self.assertEqual(r.json()["filtro_etapa"]["n_skus"], 1)
        self.assertEqual(self.llamadas["panel"]["skus_filtro"], ["SKU-0055-NEG"])

    def test_revisado_en_omnicanal_es_lista_del_sistema(self):
        self._get(etapa=None, revisado="true")
        self.assertIs(self.llamadas["panel"]["estricto"], True)
        self._get(vista="productos", revisado="true")
        self.assertIs(self.llamadas["panel"]["estricto"], False,
                      "fuera de omnicanal, exactamente como hoy")

    def test_drop_off_legado_se_comporta_como_hoy(self):
        r = self._get(vista="productos", drop_off="true")
        self.assertEqual(r.status_code, 200)
        self.assertIs(self.llamadas["panel"]["estricto"], False)
        self.assertIsNone(r.json()["filtro_etapa"])

    def test_filtro_etapa_viaja_tambien_en_las_salidas_tempranas(self):
        with mock.patch.object(ruta_prod.costing_read, "skus_revisados",
                               return_value=[]):
            r = self._get(etapa="en_full", revisado="true")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["paginacion"]["total"], 0)
        self.assertEqual(r.json()["filtro_etapa"]["n_skus"], 0)

    # ── sello ────────────────────────────────────────────────────────────────
    def test_cada_item_y_cada_variante_llevan_su_sello(self):
        async def _woo_padre(**kw):
            return ([{"sku": "SKU-0001-NEG", "nombre": "p", "wc_id": 1001,
                      "variantes": [{"sku": "SKU-0002-NEG", "nombre": "v"},
                                    {"sku": "WC-77", "nombre": "sin sku"}]}], 1, 1)

        with mock.patch.object(ruta_prod.woocommerce, "listar_productos",
                               side_effect=_woo_padre):
            r = self._get(canal="general")
        it = r.json()["items"][0]
        self.assertEqual(it["flujo"]["etapa"], "padre")
        self.assertEqual(it["flujo"]["variantes"]["total"], 2)
        self.assertEqual(it["variantes"][0]["flujo"]["etapa"], "en_drop")
        self.assertIsNone(it["variantes"][1]["flujo"])

    def test_un_sello_que_lanza_no_tumba_el_listado(self):
        with mock.patch.object(invf, "sello", side_effect=RuntimeError("boom")):
            r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["items"][0]["flujo"])

    # ── regla 11 y distintivo DROP ───────────────────────────────────────────
    def test_nada_bloqueante_se_llama_en_la_corrutina(self):
        espia = mock.Mock(wraps=ruta_prod.asyncio.to_thread)
        with mock.patch.object(ruta_prod.asyncio, "to_thread", espia):
            self._get(canal="general", etapa="recibido")
        vistas = {c.args[0] for c in espia.call_args_list if c.args}
        for fn in (ruta_prod.presencia.presencia_por_sku,
                   ruta_prod.wp_db.disponible,
                   ruta_prod.odoo.estado_almacen):
            self.assertIn(fn, vistas, getattr(fn, "__name__", fn))
        for canal, fn in (("mercado_libre", ruta_prod.meli.listar),
                          ("amazon", ruta_prod.amazon.listar),
                          ("tiktok", tiktok_panel.listar),
                          ("temu", temu_panel.listar),
                          ("walmart", walmart_panel.listar)):
            espia.reset_mock()
            with mock.patch.object(ruta_prod.asyncio, "to_thread", espia):
                self._get(canal=canal)
            vistas = {c.args[0] for c in espia.call_args_list if c.args}
            self.assertIn(fn, vistas, canal)
            self.assertIn(ruta_prod.inventario.leer_inventario, vistas, canal)

    def test_el_distintivo_drop_reusa_la_lectura_de_la_etapa(self):
        with mock.patch.object(ruta_prod.odoo, "estado_almacen",
                               return_value=({"SKU-0055-NEG"}, 0.0)) as ea:
            r = self._get(etapa="en_drop")
        ea.assert_called_once()
        self.assertIs(r.json()["items"][0]["drop_off"], True)


# ─────────────────────────────────────────────────────────────────────────────
# 7 · EL FILTRO EXACTO EN SQL
# ─────────────────────────────────────────────────────────────────────────────

class FiltrosSqlExactos(unittest.TestCase):
    def test_channel_read_con_exactos_usa_lower_y_minusculas(self):
        sql, params = channel_read._filtros(
            "base", search=None, solo_publicados=False, cuenta=None,
            estados=None, skus_filtro=["ROP-0001", "rop-0002"],
            pub_expr="1=1", skus_exactos=True)
        self.assertIn("lower(l.sku::text) = any(%(skus_exactos)s)", sql)
        self.assertEqual(params["skus_exactos"], ["rop-0001", "rop-0002"])
        self.assertNotIn("ilike", sql)

    def test_sin_exactos_el_ilike_sigue_igual(self):
        sql, params = channel_read._filtros(
            "base", search=None, solo_publicados=False, cuenta=None,
            estados=None, skus_filtro=["ROP"], pub_expr="1=1")
        self.assertIn("l.sku::text ilike %(sku_0)s", sql)
        self.assertEqual(params["sku_0"], "%ROP%")

    def test_los_paneles_comparan_sin_mayusculas(self):
        for modulo in (tiktok_panel, temu_panel, walmart_panel):
            capturado = {}

            def _fetch(sql, params=None, _c=capturado):
                _c.setdefault("sql", []).append(" ".join(sql.split()))
                _c["params"] = params
                return [{"n": 0}]

            with mock.patch.object(modulo.sdb, "fetch_all", side_effect=_fetch):
                modulo.listar(skus_filtro=["Rop-0001"])
            for sql in capturado["sql"]:
                self.assertIn("lower(l.sku::text) = any(%(skus)s)", sql,
                              modulo.CANAL)
            self.assertEqual(capturado["params"]["skus"], ["rop-0001"],
                             modulo.CANAL)

    def test_los_paneles_con_estricto_relanzan(self):
        for modulo in (tiktok_panel, temu_panel, walmart_panel):
            with mock.patch.object(modulo.sdb, "fetch_all",
                                   side_effect=RuntimeError("kubera")):
                self.assertEqual(modulo.listar(skus_filtro=["A"]), ([], 0),
                                 modulo.CANAL)
                with self.assertRaises(RuntimeError, msg=modulo.CANAL):
                    modulo.listar(skus_filtro=["A"], estricto=True)

    def test_predicados_publicados_compartidos(self):
        self.assertEqual(tiktok_panel.filtro_sql_publicado("x", "k"),
                         ("x.status = %(k)s", {"k": "ACTIVATE"}))
        self.assertEqual(walmart_panel.filtro_sql_publicado("x", "k"),
                         ("upper(x.status) = %(k)s", {"k": "PUBLISHED"}))
        self.assertEqual(channel_read.filtro_sql_publicado("mercado_libre")[0],
                         channel_read._PUB_ML)
        expr, params = channel_read.filtro_sql_publicado("amazon")
        self.assertEqual(expr, channel_read._PUB_AMZ)
        self.assertEqual(set(params), {"pub", "viva"})

    def test_buscar_wc_ids_con_ids_va_por_la_pk(self):
        capt = {}

        def _fetch(sql, args=None):
            capt.setdefault("sql", []).append(" ".join(sql.split()))
            capt["args"] = args
            return [{"n": 2}] if "COUNT" in sql else [{"ID": 7}]

        with mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch), \
                mock.patch.object(wp_db, "disponible", return_value=True):
            ids, total = woocommerce._buscar_wc_ids_wp(
                None, 1, 40, "reciente", None, None, "omnicanal",
                ids_productos=[9, 8, 9])
        self.assertEqual((ids, total), ([7], 2))
        self.assertIn("p.ID IN (%s,%s)", capt["sql"][0])
        self.assertNotIn("meta_value", capt["sql"][0])

    def test_buscar_wc_ids_con_estricto_relanza(self):
        with mock.patch.object(wp_db, "_fetch_all",
                               side_effect=RuntimeError("Hostinger")), \
                mock.patch.object(wp_db, "disponible", return_value=True):
            self.assertEqual(
                woocommerce._buscar_wc_ids_wp(None, 1, 40, "reciente", None,
                                              None, "omnicanal",
                                              ids_productos=[1]),
                ([], 0))
            with self.assertRaises(wp_db.FiltroExactoNoAplicable):
                woocommerce._buscar_wc_ids_wp(None, 1, 40, "reciente", None,
                                              None, "omnicanal",
                                              ids_productos=[1], estricto=True)

    def test_skus_padre_estricto_no_devuelve_un_mapa_a_medias(self):
        with mock.patch.object(wp_db, "disponible", return_value=True), \
                mock.patch.object(wp_db, "_fetch_all",
                                  side_effect=RuntimeError("corte")):
            self.assertEqual(wp_db.skus_padre(["A"]), {})
            with self.assertRaises(wp_db.FiltroExactoNoAplicable):
                wp_db.skus_padre(["A"], estricto=True)

    def test_indice_plano_por_ids_y_por_skus_exactos(self):
        capt = {}

        def _fetch(sql, args=None):
            capt.setdefault("sql", []).append(" ".join(sql.split()))
            return [{"n": 0}] if "COUNT" in sql else []

        with mock.patch.object(wp_db, "disponible", return_value=True), \
                mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch):
            wp_db.indice_plano("omnicanal", None, None, None, "reciente", 1, 40,
                               ids=[3, 4])
            por_ids = capt["sql"][0]
            capt["sql"].clear()
            wp_db.indice_plano("omnicanal", None, ["A-1"], None, "reciente", 1,
                               40, skus_exactos=True)
            exactos = capt["sql"][0]
        self.assertIn("p.ID IN (%s,%s)", por_ids)
        self.assertIn("v.ID IN (%s,%s)", por_ids)
        self.assertNotIn("LIKE", por_ids)
        self.assertEqual(exactos.count("sk.meta_value IN (%s)"), 2)
        self.assertNotIn("LIKE", exactos)

    def test_indice_plano_suma_la_busqueda_con_and(self):
        # La caja de búsqueda no la reemplaza la etapa: escribir «bolsa» con
        # Recibido puesto tiene que dar las bolsas DE esa etapa. Antes, con
        # `ids` la búsqueda ni llegaba al SQL, y con `skus_exactos` entraba como
        # un elemento más del `IN`, donde ningún término tecleado casa.
        capt = {}

        def _fetch(sql, args=None):
            capt.setdefault("sql", []).append(" ".join(sql.split()))
            capt.setdefault("args", []).append(args)
            return [{"n": 0}] if "COUNT" in sql else []

        with mock.patch.object(wp_db, "disponible", return_value=True), \
                mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch):
            wp_db.indice_plano("omnicanal", "bolsa", None, None, "reciente", 1,
                               40, ids=[3, 4])
            por_ids, args_ids = capt["sql"][0], capt["args"][0]
            capt["sql"].clear()
            capt["args"].clear()
            wp_db.indice_plano("omnicanal", "bolsa", ["A-1"], None, "reciente",
                               1, 40, skus_exactos=True)
            exactos, args_ex = capt["sql"][0], capt["args"][0]
        self.assertIn("p.ID IN (%s,%s)", por_ids)
        self.assertIn("AND (sk.meta_value LIKE %s OR p.post_title LIKE %s)", por_ids)
        self.assertIn("%bolsa%", args_ids)
        self.assertEqual(exactos.count("sk.meta_value IN (%s)"), 2)
        self.assertIn("A-1", args_ex)
        self.assertNotIn("bolsa", args_ex, "el término tecleado NO va en el IN")
        self.assertIn("%bolsa%", args_ex)

    def test_indice_plano_sin_lista_del_sistema_no_cambia(self):
        # Con la lista TECLEADA, búsqueda y SKUs siguen siendo el mismo saco
        # unido por OR: es lo que «Filtrar SKUs» promete y no se toca.
        capt = {}

        def _fetch(sql, args=None):
            capt.setdefault("sql", []).append(" ".join(sql.split()))
            return [{"n": 0}] if "COUNT" in sql else []

        with mock.patch.object(wp_db, "disponible", return_value=True), \
                mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch):
            wp_db.indice_plano("omnicanal", "bolsa", ["A-1"], None, "reciente",
                               1, 40)
        una = capt["sql"][0]
        self.assertIn("(sk.meta_value LIKE %s OR p.post_title LIKE %s) OR "
                      "(sk.meta_value LIKE %s OR p.post_title LIKE %s)", una)

    def test_indice_plano_estricto_relanza(self):
        with mock.patch.object(wp_db, "disponible", return_value=True), \
                mock.patch.object(wp_db, "_fetch_all",
                                  side_effect=RuntimeError("corte")):
            self.assertEqual(
                wp_db.indice_plano("omnicanal", None, None, None, "reciente",
                                   1, 40, ids=[1]), ([], 0))
            with self.assertRaises(wp_db.FiltroExactoNoAplicable):
                wp_db.indice_plano("omnicanal", None, None, None, "reciente",
                                   1, 40, ids=[1], estricto=True)


# ─────────────────────────────────────────────────────────────────────────────
# 8 · GENERAL EN MODO ESTRICTO
# ─────────────────────────────────────────────────────────────────────────────

class _Respuesta:
    def __init__(self, cuerpo, status=200, headers=None):
        self._cuerpo = cuerpo
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._cuerpo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Cliente:
    """Un `_client()` de WooCommerce de mentira: registra cada GET."""

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.llamadas: list[dict] = []

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, ruta, params=None):
        self.llamadas.append({"ruta": ruta, "params": params or {}})
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class WooEstricto(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parches = [
            mock.patch.object(wp_db, "disponible", return_value=True),
            mock.patch.object(
                woocommerce, "variantes_de_productos",
                new=mock.AsyncMock(side_effect=lambda cli, data: [[] for _ in data])),
            mock.patch.object(wp_db, "precios_y_costo_por_wc_id",
                              return_value={}),
            mock.patch.object(woocommerce, "_categoria_de_producto",
                              new=mock.AsyncMock(return_value=[])),
        ]
        for p in self.parches:
            p.start()
            self.addCleanup(p.stop)

    def _con(self, respuestas):
        cli = _Cliente(list(respuestas))
        p = mock.patch.object(woocommerce, "_client", cli)
        p.start()
        self.addCleanup(p.stop)
        return cli

    async def test_con_lista_del_sistema_no_se_pide_el_complemento_por_sku(self):
        cli = self._con([_Respuesta([{"id": 1, "sku": "A-1"}])])
        with mock.patch.object(woocommerce, "_buscar_wc_ids_db",
                               return_value=([1], 1)):
            items, total, _ = await woocommerce.listar_productos(
                skus=["A-1", "A-2"], skus_exactos=True, estricto=True,
                vista="omnicanal")
        self.assertEqual(total, 1)
        self.assertEqual(len(cli.llamadas), 1, "una sola llamada: sin ?sku=")
        self.assertNotIn("sku", cli.llamadas[0]["params"])

    async def test_drop_off_legado_conserva_el_complemento(self):
        # `drop_off=` de /productos viaja con `skus_exactos` desde antes de esta
        # entrega y SIN `estricto`: esa ruta queda congelada por contrato, así
        # que el complemento de la página 1 sigue rescatando lo que la consulta
        # de WordPress no resolvió.
        cli = self._con([_Respuesta([{"id": 1, "sku": "A-1"}]),
                         _Respuesta([{"id": 2, "sku": "A-2", "status": "publish",
                                      "type": "simple"}])])
        with mock.patch.object(woocommerce, "_buscar_wc_ids_db",
                               return_value=([1], 1)):
            items, total, _ = await woocommerce.listar_productos(
                skus=["A-1", "A-2"], skus_exactos=True, vista="productos")
        self.assertEqual(len(cli.llamadas), 2)
        self.assertIn("sku", cli.llamadas[1]["params"])
        self.assertEqual(total, 2)

    async def test_la_correccion_del_fantasma_sigue_viva_con_ids(self):
        # El COUNT dice 5, el include devuelve 4 y caben en una página: el total
        # real es lo que se pudo pintar, no lo que la consulta contó.
        cli = self._con([_Respuesta([{"id": i, "sku": f"A-{i}"} for i in range(4)])])
        with mock.patch.object(woocommerce, "_buscar_wc_ids_db",
                               return_value=([0, 1, 2, 3, 4], 5)):
            items, total, paginas = await woocommerce.listar_productos(
                per_page=40, ids_productos=[0, 1, 2, 3, 4], vista="omnicanal")
        self.assertEqual((len(items), total, paginas), (4, 4, 1))
        self.assertEqual(len(cli.llamadas), 1)

    async def test_sin_exactos_el_complemento_sigue_como_hoy(self):
        cli = self._con([_Respuesta([{"id": 1, "sku": "A-1"}]),
                         _Respuesta([{"id": 2, "sku": "A-2", "status": "publish",
                                      "type": "simple"}])])
        with mock.patch.object(woocommerce, "_buscar_wc_ids_db",
                               return_value=([1], 1)):
            items, total, _ = await woocommerce.listar_productos(
                skus=["A-1", "A-2"], vista="omnicanal")
        self.assertEqual(len(cli.llamadas), 2)
        self.assertIn("sku", cli.llamadas[1]["params"])
        self.assertEqual(total, 2)

    async def test_el_respaldo_por_busqueda_no_resucita_el_catalogo(self):
        cli = self._con([_Respuesta([])])
        with mock.patch.object(woocommerce, "_buscar_wc_ids_db",
                               return_value=([], 0)):
            items, total, _ = await woocommerce.listar_productos(
                search="bolsas", ids_productos=[7], estricto=True,
                vista="omnicanal")
        self.assertEqual((items, total), ([], 0))
        self.assertEqual(cli.llamadas, [], "ni una llamada ?search=")

    async def test_el_rest_caido_con_estricto_lo_dice(self):
        import httpx
        for fallo in (httpx.ConnectError("sin red"), _Respuesta([], status=500)):
            cli = self._con([fallo])
            with mock.patch.object(woocommerce, "_buscar_wc_ids_db",
                                   return_value=([1], 1)):
                with self.assertRaises(wp_db.FiltroExactoNoAplicable):
                    await woocommerce.listar_productos(
                        ids_productos=[1], estricto=True, vista="omnicanal")

    async def test_el_aplanado_a_medias_con_estricto_lanza(self):
        cli = self._con([_Respuesta([{"id": 1, "sku": "A-1"}])])
        with mock.patch.object(wp_db, "indice_plano",
                               return_value=([{"wc_id": 1, "tipo": "product",
                                               "parent_id": None},
                                              {"wc_id": 2,
                                               "tipo": "product_variation",
                                               "parent_id": 1}], 2)), \
                mock.patch.object(wp_db, "variantes_como_productos",
                                  side_effect=RuntimeError("MySQL cortó")):
            with self.assertRaises(wp_db.FiltroExactoNoAplicable):
                await woocommerce.listar_productos(
                    aplanar=True, ids_filas=[1, 2], estricto=True,
                    vista="omnicanal")
        self.assertEqual(len(cli.llamadas), 1)

    async def test_sin_wp_db_y_estricto_no_se_toca_la_maestra(self):
        self._con([])
        with mock.patch.object(wp_db, "disponible", return_value=False), \
                mock.patch.object(woocommerce, "_buscar_wc_ids_wp",
                                  side_effect=AssertionError("no debió llamarse")):
            with self.assertRaises(wp_db.FiltroExactoNoAplicable):
                await woocommerce.listar_productos(
                    skus=["A-1"], skus_exactos=True, estricto=True,
                    vista="omnicanal")


# ─────────────────────────────────────────────────────────────────────────────
# 9 · LA RUTA NUEVA
# ─────────────────────────────────────────────────────────────────────────────

class RutasFlujoCanal(_Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(ruta_inv.router)
        self.c = TestClient(app)

    def test_no_cae_en_la_ficha_del_sku(self):
        with mock.patch.object(ruta_inv.inv, "filas",
                               side_effect=AssertionError("llegó a la ficha")), \
                mock.patch.object(invf, "conteos_canal",
                                  return_value={"marca": "canal"}):
            r = self.c.get("/api/inventario/flujo/canal?canal=tiktok")
        self.assertEqual((r.status_code, r.json()), (200, {"marca": "canal"}))

    def test_canal_desconocido_y_criterio_invalido(self):
        r = self.c.get("/api/inventario/flujo/canal?canal=marte")
        self.assertEqual(r.status_code, 400)
        self.assertIn("canal desconocido", r.json()["detail"])
        r2 = self.c.get("/api/inventario/flujo/canal?canal=tiktok&criterio=x")
        self.assertEqual(r2.status_code, 422)

    def test_la_ruta_nueva_queda_con_rol_lectura(self):
        from core import rbac
        self.assertEqual(rbac.rol_requerido("GET", "/api/inventario/flujo/canal"),
                         "lectura")

    def test_una_falla_del_conteo_es_502(self):
        with mock.patch.object(invf, "conteos_canal",
                               side_effect=RuntimeError("boom")):
            r = self.c.get("/api/inventario/flujo/canal?canal=tiktok")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["detail"],
                         "No se pudo leer el conteo del flujo por canal")

    def test_responde_con_la_foto_en_memoria(self):
        invf._foto = _foto_omni()
        with mock.patch.object(invf, "_ahora", return_value=T0), \
                mock.patch.object(invf.settings, "inventario_flujo_enabled", True), \
                mock.patch.object(invf.settings, "supabase_read_publicaciones", True):
            r = self.c.get("/api/inventario/flujo/canal"
                           "?canal=mercado_libre&cuenta=BEKURA&criterio=publicados")
        cuerpo = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(cuerpo["total"], 45)
        self.assertEqual(cuerpo["criterio_efectivo"], "publicados")
        self.assertEqual(set(cuerpo["fuentes"]),
                         {"kubera", "odoo", "odoo_drop", "canales"})


# ─────────────────────────────────────────────────────────────────────────────
# 10 · SIN DINERO
# ─────────────────────────────────────────────────────────────────────────────

_PALABRAS_DINERO = ("costo", "precio", "flete", "margen")


def _llaves(obj, ruta="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{ruta}.{k}", k
            yield from _llaves(v, f"{ruta}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _llaves(v, f"{ruta}[{i}]")


class SinDineroOmnicanal(_Base):
    """Todo /api/inventario queda con rol lectura: de aquí salen SKUs, conteos
    y fechas — jamás costo, precio, flete ni margen. `costo_validado` es la
    única excepción y viaja como VALOR (la clave de la etapa), no como llave."""

    def _revisar(self, obj):
        for ruta, llave in _llaves(obj):
            for palabra in _PALABRAS_DINERO:
                self.assertNotIn(palabra, str(llave).lower(), ruta)

    def test_ninguna_llave_del_stepper_ni_del_sello_lleva_dinero(self):
        foto = _foto_omni()
        self._revisar(invf._conteos_canal(None, T0, canal="general", cuenta=None,
                                          criterio="todas", aplanado=False,
                                          lee_publicaciones=True, flag=True))
        for canal in ("general", "mercado_libre", "tiktok"):
            self._revisar(_conteo(foto, canal=canal))
        fp = _fp(foto)
        for sku in ("SKU-0001-NEG", "SKU-0055-NEG", "ROP-0695-BEI-m"):
            self._revisar(invf.sello(fp, sku))
        self._revisar(invf.resumen_variantes([invf.sello(fp, "SKU-0002-NEG")]))


if __name__ == "__main__":
    unittest.main()
