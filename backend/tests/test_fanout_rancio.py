"""Pruebas de los «SIN CAMBIO» RANCIOS de TikTok y Temu (`fanout_stock._rancios` y
`_verificar`, bandera FANOUT_VERIFICAR_RANCIO).

── EL DEFECTO (medido el 9-oct-2026 en producción) ─────────────
`plan` decide «el canal ya tiene N» contra `channel.listings.stock_own`, que en TikTok
y Temu sólo refresca el censo (cada 60 min). Si Woo regresa al número viejo dentro de
esa ventana —una venta que stock_watch devuelve antes de que Odoo tenga la orden, o
el rebote de la foto de Odoo—, el canal se queda con lo último que le escribimos.
60 días: 163 casos, 139 con el canal ABAJO que nadie cerraba.

── QUÉ FIJAN ────────────────────────────────────────────
  1. N→N−k→N (el canal quedó ABAJO) y N→N+k→N (ARRIBA), en TikTok y en Temu: el
     segundo «sin cambio» se verifica en vivo y se escribe; el tercero ya no.
  2. Si en vivo ya tiene el objetivo, no se escribe y queda «verificado en vivo».
  3. SIN ESCRITURA NUESTRA RECIENTE NO SE VERIFICA: lo que Temu apartó y el censo ya
     vio no se sube. Tampoco si la escritura cae fuera de la ventana, si dejó el mismo
     número, si el censo ya releyó la publicación después, ni para SUBIR tras una
     «venta …». Lo que el plan OMITIÓ (stock desconocido, borrador) nunca se toca.
  3b. LA DIRECCIÓN: si lo último que le dejamos es MÁS que el objetivo, sólo se baja
     (`solo_bajar`): un apartado de Temu dentro de la ventana no se suelta.
  4. Con la bandera apagada, la ventana en 0 o sin bitácora en kubera: idéntico a hoy,
     y ni se le pregunta a kubera. En dry-run y en ENSAYO se anota lo que verificaría,
     sin llamar al canal.
  5. Mercado Libre y Amazon nunca se verifican; un canal con su candado apagado,
     tampoco.
  6. Un error del canal (o de kubera) no rompe la cola: queda en la bitácora —con lo
     último que le escribimos como `stock_canal`— y los demás destinos siguen.
  7. El seguro de stock 0 reactiva lo que apagó cuando la verificación deja el stock.
  8. La trazabilidad lo dice («· en vivo»), la matriz toma la verificación como el
     último valor conocido, y `plan` no cambió con el refactor de los candados.

── NO SE TOCA NADA REAL ─────────────────────────────────
Woo, `channel.listings`, la bitácora de kubera y las APIs de TikTok y Temu van
suplantadas; los escritores son los de verdad, hablando con un canal falso. La
bitácora falsa contesta la consulta como Postgres (ventana, `dry_run`, acción y la
última por publicación); el SQL de verdad se probó contra el sandbox. Los SKUs son
INVENTADOS: el repo es público.

    cd backend && python -m unittest tests.test_fanout_rancio -v
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import channel_read  # noqa: E402
from services import fanout_seguro as S  # noqa: E402
from services import fanout_stock as F  # noqa: E402
from services import fanout_vivo as V  # noqa: E402
from services import supabase_db as sdb  # noqa: E402
from services import temu as TM  # noqa: E402
from services import tiktok as TK  # noqa: E402
from tests import test_fanout_seguro as _TS  # noqa: E402
from tests.test_fanout_seguro import Arnes, accion, plan  # noqa: E402

G_SEGURO = _TS.G

# Mientras corre este módulo, tocar la base o la red revienta (las trampas del seguro).
setUpModule = _TS.setUpModule
tearDownModule = _TS.tearDownModule

SKU = "ZZZ-0002-VRD"
G = "900002"        # goodsId inventado
P = "770002"        # product_id inventado
ML = "MLM0000002"
CUENTA = {"tiktok": "KUBERA", "temu": "TEMU", "mercado_libre": "BEKURA", "amazon": "SANCORFASHION"}
ITEM = {"tiktok": P, "temu": G, "mercado_libre": ML, "amazon": SKU}
T0 = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


def ultima_conocida(bitacora: list[dict], params: dict, ahora: datetime,
                    censo: dict[str, datetime] | None = None) -> list[dict]:
    """Lo que contesta Postgres a `fanout_stock._SQL_ULTIMA_CONOCIDA`: la última fila
    (por id) de cada publicación que sea una escritura «ok» o una verificación, de ese
    SKU y esos canales, sin dry-run y dentro de la ventana; y si el censo de ese canal
    (`channel.listings.updated_at`, en `censo`) pasó más de `m` segundos después."""
    desde = ahora - timedelta(hours=float(params["h"]))
    prefijo = params["v"].rstrip("%")
    ultimas: dict[tuple, dict] = {}
    for f in sorted(bitacora, key=lambda x: x["id"]):
        if f["sku"].upper() != params["s"].upper() or f["canal"] not in params["c"] or f["dry_run"]:
            continue
        if f["ts"] <= desde:
            continue
        r = f["resultado"] or ""
        if (f["accion"] == "escribir" and r.lower().startswith("ok")) or \
                (f["accion"] == "sin_cambio" and r.startswith(prefijo)):
            ultimas[(f["canal"], f["item_id"])] = f
    margen = timedelta(seconds=float(params["m"]))
    return [{"canal": f["canal"], "item_id": f["item_id"], "objetivo": f["objetivo"], "ts": f["ts"],
             "motivo": f.get("motivo"),
             "releida": bool(censo and censo.get(f["canal"]) and censo[f["canal"]] > f["ts"] + margen)}
            for f in ultimas.values()]


class Arnes2(unittest.TestCase):
    """Woo, el censo (`channel.listings`), la bitácora y los dos canales, falsos. Los
    escritores de TikTok y Temu son los de verdad."""

    AJUSTES = dict(fanout_verificar_rancio=True, fanout_verificar_rancio_h=2.0,
                   supabase_write_fanout_log=True, fanout_canales="", fanout_tiktok=True,
                   fanout_temu=True, fanout_reserva=0)

    def setUp(self):
        self.ahora = T0
        self.woo = 5
        self.censo = {"tiktok": 5, "temu": 5, "mercado_libre": 5}   # lo que dice channel.listings
        self.vivo = {"tiktok": 5, "temu": 5}                        # lo que tiene el canal de verdad
        self.falla: dict[str, str] = {}
        self.api: list[tuple] = []          # cada llamada a un canal
        self.ml: list[tuple] = []           # escrituras a Mercado Libre
        self.bitacora: list[dict] = []
        self.eventos: list[dict] = []
        self.consultas: list[dict] = []
        self.kubera_caida = False
        self.censo_ts: dict[str, datetime] = {}   # channel.listings.updated_at: cuándo pasó el censo
        self.estado = {"tiktok": "ACTIVATE", "temu": "2/8"}
        self.vende_al_editar = 0            # Temu: piezas que se venden entre la escritura y la relectura

        async def tk_llamar(ruta, token, params, cuerpo=None, metodo="GET"):
            self.api.append(("tiktok", metodo))
            if "tiktok" in self.falla:
                raise RuntimeError(self.falla["tiktok"])
            if ruta.endswith("/inventory/update"):
                self.vivo["tiktok"] = cuerpo["skus"][0]["inventory"][0]["quantity"]
                return {}
            return {"skus": [{"id": "S1", "inventory": [
                {"warehouse_id": "999", "quantity": 0},          # el de devoluciones
                {"warehouse_id": F._ALMACEN_VENTAS_TIKTOK, "quantity": self.vivo["tiktok"]}]}]}

        async def tm_llamar(tipo, datos):
            self.api.append(("temu", tipo))
            if "temu" in self.falla:
                raise RuntimeError(self.falla["temu"])
            if tipo == "bg.local.goods.list.query":
                return {"goodsList": [{"goodsId": int(G), "quantity": self.vivo["temu"],
                                       "skuIdList": [222]}]}
            self.vivo["temu"] += datos["skuStockChangeList"][0]["stockDiff"] - self.vende_al_editar
            return {"operateResult": True, "skuStockEditStatusInfoList": [{"stockEditStatus": True}]}

        def escribir_ml(cuenta, item, cantidad, solo_bajar=False):
            self.ml.append((item, cantidad))
            return True, "ok"

        def inventario(skus):
            filas = {}
            for canal, n in self.censo.items():
                filas[f"{canal}|{CUENTA[canal]}"] = {
                    "canal": canal, "cuenta": CUENTA[canal], "item_id": ITEM[canal],
                    "stock_real": n, "es_full": False, "stock_full": 0, "stock_fba": 0,
                    "estado_canal": self.estado.get(canal),
                    "situacion": "active" if canal == "mercado_libre" else None}
            return {SKU: filas}

        def persistir(evento):
            """Como `_persistir` + `fanout_read.registrar`: una fila por acción."""
            self.eventos.append(evento)
            for a in evento["acciones"]:
                self.bitacora.append({
                    "id": len(self.bitacora) + 1, "ts": self.ahora, "sku": evento["sku"],
                    "motivo": evento["motivo"], "dry_run": bool(evento["dry_run"]),
                    "objetivo": evento["objetivo"], "canal": a["canal"], "cuenta": a["cuenta"],
                    "item_id": str(a["item_id"] or ""), "accion": a["accion"],
                    "stock_canal": a.get("stock_actual_canal"),
                    "resultado": str(a.get("resultado") or a.get("omitido_por") or "")})

        def fetch_all(sql, params=None):
            if sql is not F._SQL_ULTIMA_CONOCIDA:
                raise AssertionError(f"la prueba hizo otra consulta: {sql[:80]}")
            self.consultas.append(dict(params))
            if self.kubera_caida:
                raise RuntimeError("kubera no contesta (prueba)")
            return ultima_conocida(self.bitacora, params, self.ahora, self.censo_ts)

        parches = [
            mock.patch.multiple(F.settings, **self.AJUSTES),
            mock.patch.object(F, "seguro_encendido", return_value=False),
            mock.patch.object(F, "dry_run", return_value=False),
            mock.patch.object(F, "_stock_drop", side_effect=lambda sku: self.woo),
            mock.patch.object(F, "_amazon_en_vivo", side_effect=AssertionError("Amazon no es destino aquí")),
            mock.patch.object(F, "_persistir", side_effect=persistir),
            mock.patch.object(channel_read, "leer_inventario", side_effect=inventario),
            mock.patch.object(sdb, "fetch_all", side_effect=fetch_all),
            mock.patch.object(TK, "llamar", side_effect=tk_llamar),
            mock.patch.object(TK, "access_token", return_value="t"),
            mock.patch.object(TK, "cipher", return_value="c"),
            mock.patch.object(TM, "llamar", side_effect=tm_llamar),
            mock.patch.object(TM, "disponible", return_value=True),
            mock.patch.object(F, "_en_hilo", side_effect=lambda fabrica, etiqueta, timeout=60: asyncio.run(fabrica())),
            mock.patch("time.sleep"),
            mock.patch.dict(F._temu_escrito_en, {}, clear=True),
            mock.patch.dict(F._ESCRITORES, {"mercado_libre": escribir_ml}),
            mock.patch.dict(F._contadores, {k: 0 for k in F._contadores}),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

    # ── Atajos ────────────────────────────────────────────────────────────────

    def evento(self, woo: int, minutos: float = 10.0,
               motivo: str = "cambio de stock en Woo") -> dict[str, dict]:
        """Woo cambia a `woo` y el fan-out reparte, `minutos` después del anterior.
        Devuelve lo que quedó en la bitácora, por canal."""
        self.ahora += timedelta(minutes=minutos)
        self.woo = woo
        F._aplicar(SKU, motivo)
        # `resultado` como lo guarda la bitácora (`_persistir`).
        return {a["canal"]: {**a, "resultado": a.get("resultado") or a.get("omitido_por")}
                for a in self.eventos[-1]["acciones"]}

    def fila(self, canal: str, accion: str, objetivo: int, resultado: str = "ok",
             hace_min: float = 30.0, dry_run: bool = False, motivo: str = "delta de Odoo aplicado"):
        """Una fila vieja en la bitácora, como si la hubiera dejado otro evento."""
        self.bitacora.append({
            "id": len(self.bitacora) + 1, "ts": self.ahora - timedelta(minutes=hace_min),
            "sku": SKU, "motivo": motivo, "dry_run": dry_run, "objetivo": objetivo,
            "canal": canal, "cuenta": CUENTA[canal], "item_id": ITEM[canal], "accion": accion,
            "stock_canal": None, "resultado": resultado})

    def del_canal(self, canal: str) -> list[tuple]:
        return [x for x in self.api if x[0] == canal]


# ══════════════════════════════════════════════════════════════════════════════

class Rancios(Arnes2):
    """1 · Los dos patrones medidos, en los dos canales."""

    def _ida_y_vuelta(self, canal: str, intermedio: int):
        # Woo 5 → `intermedio`: el plan escribe (el censo dice 5).
        d = self.evento(intermedio)
        self.assertEqual((d[canal]["accion"], d[canal]["resultado"]), ("escribir", "ok"))
        self.assertEqual(self.vivo[canal], intermedio)
        # Woo regresa a 5 antes de que el censo vea el `intermedio`: «sin cambio»
        # rancio. Hoy se quedaba así; ahora se verifica y se escribe.
        self.api.clear()
        d = self.evento(5)
        self.assertEqual(self.vivo[canal], 5, f"{canal} quedó con el número de Woo")
        self.assertEqual(d[canal]["accion"], "escribir")
        self.assertEqual(d[canal]["resultado"],
                         f"ok ({F.VERIFICADO}: {intermedio}→5; el censo decía 5)")
        self.assertEqual(d[canal]["stock_actual_canal"], intermedio, "lo leído en vivo, no el censo")
        # El siguiente «sin cambio» ya sabe que le dejamos 5: ni lo lee.
        self.api.clear()
        d = self.evento(5)
        self.assertEqual((d[canal]["accion"], d[canal]["resultado"]), ("sin_cambio", "el canal ya tiene 5"))
        self.assertEqual(self.api, [])

    def test_venta_y_regreso_de_odoo_tiktok(self):
        self._ida_y_vuelta("tiktok", 4)

    def test_venta_y_regreso_de_odoo_temu(self):
        self._ida_y_vuelta("temu", 4)

    def test_rebote_hacia_arriba_tiktok(self):
        self._ida_y_vuelta("tiktok", 8)

    def test_rebote_hacia_arriba_temu(self):
        self._ida_y_vuelta("temu", 8)

    def test_los_dos_canales_en_el_mismo_evento_con_una_sola_consulta(self):
        self.evento(4)
        self.consultas.clear()
        d = self.evento(5)
        self.assertEqual((self.vivo["tiktok"], self.vivo["temu"]), (5, 5))
        self.assertEqual(len(self.consultas), 1)
        self.assertEqual(self.consultas[0]["c"], ["temu", "tiktok"])
        self.assertTrue(all(d[c]["resultado"].startswith(f"ok ({F.VERIFICADO}") for c in ("tiktok", "temu")))
        self.assertEqual(F._contadores["verificados"], 2)

    def test_temu_escrito_hace_menos_de_90_s_espera_y_relee(self):
        # Dentro de 90 s de nuestra escritura la lectura de Temu no es de fiar: aunque
        # ya diga el objetivo, el escritor espera y relee antes de darlo por bueno.
        self.evento(4)
        self.vivo["temu"] = 5
        with mock.patch("time.sleep") as dormir:
            d = self.evento(5, minutos=0.5)
        self.assertEqual(d["temu"]["accion"], "sin_cambio")
        self.assertTrue(d["temu"]["resultado"].startswith(F.VERIFICADO))
        dormir.assert_called_with(F._TEMU_ESPERA_S)


class YaTenia(Arnes2):
    """2 · Si el canal ya tiene el objetivo, no se escribe y queda dicho."""

    def test_tiktok_y_temu_ya_tenian_el_objetivo(self):
        for canal in ("tiktok", "temu"):
            self.fila(canal, "escribir", 4)          # le escribimos 4 hace 30 min…
        # …pero en vivo ya tienen 5 (otro lo movió, o la venta se canceló).
        d = self.evento(5)
        for canal in ("tiktok", "temu"):
            with self.subTest(canal=canal):
                self.assertEqual(d[canal]["accion"], "sin_cambio")
                self.assertEqual(d[canal]["resultado"],
                                 f"{F.VERIFICADO}: el canal ya tiene 5 (lo último que le escribimos fue 4)")
        self.assertNotIn(("tiktok", "POST"), self.api, "TikTok: sin escritura")
        self.assertNotIn(("temu", "bg.local.goods.stock.edit"), self.api, "Temu: sin escritura")
        self.assertEqual(F._contadores["sin_cambio"], 3, "TikTok, Temu y ML")
        # La verificación cuenta como lo último que sabemos: no se repite.
        self.api.clear()
        self.evento(5)
        self.assertEqual(self.api, [])

    def test_el_escritor_de_tiktok_con_verificar(self):
        self.vivo["tiktok"] = 6
        self.assertEqual(F._escribir_tiktok("KUBERA", P, 6, verificar=True),
                         (True, f"{F.YA_TENIA} 6 en vivo)"))
        self.assertEqual(F._escribir_tiktok("KUBERA", P, 2, verificar=True), (True, "ok (6→2)"))
        self.assertEqual(self.vivo["tiktok"], 2)

    def test_el_escritor_de_tiktok_sin_verificar_escribe_a_ciegas_como_siempre(self):
        self.vivo["tiktok"] = 6
        self.assertEqual(F._escribir_tiktok("KUBERA", P, 6), (True, "ok (6)"))
        self.assertIn(("tiktok", "POST"), self.api)

    def test_temu_sigue_contestando_lo_mismo(self):
        self.assertEqual(F._escribir_temu("TEMU", G, 5), (True, "ok (ya tenía 5 en vivo)"))
        self.assertEqual(F._escribir_temu("TEMU", G, 5, verificar=True), (True, "ok (ya tenía 5 en vivo)"))


class NoVerifica(Arnes2):
    """3 · Sin escritura nuestra reciente con OTRO número, no se le pregunta al canal."""

    def test_apartado_de_temu_sin_escritura_nuestra_no_se_sube(self):
        # Como ACC-0768-EST: Temu apartó una pieza de un pedido sin pagar. El censo
        # vio 5 antes; en vivo hay 4. Nosotros no le escribimos nada.
        self.vivo["temu"] = 4
        d = self.evento(5)
        self.assertEqual((d["temu"]["accion"], d["temu"]["resultado"]), ("sin_cambio", "el canal ya tiene 5"))
        self.assertEqual(self.vivo["temu"], 4, "lo apartado sigue apartado")
        self.assertEqual(self.api, [], "ni siquiera se lee")

    def test_escritura_reciente_con_el_mismo_numero_no_verifica(self):
        self.fila("temu", "escribir", 5)
        self.vivo["temu"] = 4
        self.evento(5)
        self.assertEqual(self.api, [])

    def test_escritura_fuera_de_la_ventana_no_verifica(self):
        self.fila("temu", "escribir", 4, hace_min=3 * 60)
        self.fila("tiktok", "escribir", 4, hace_min=3 * 60)
        self.vivo.update(temu=4, tiktok=4)
        self.evento(5)
        self.assertEqual(self.api, [])
        self.assertEqual(self.consultas[0]["h"], 2.0)

    def test_la_ventana_se_ajusta_sin_deploy(self):
        self.fila("temu", "escribir", 4, hace_min=3 * 60)
        self.vivo["temu"] = 4
        with mock.patch.object(F.settings, "fanout_verificar_rancio_h", 4.0):
            self.evento(5, minutos=0)
        self.assertEqual(self.vivo["temu"], 5)

    def test_lo_que_no_cuenta_como_escritura_nuestra(self):
        self.fila("temu", "escribir", 4, resultado="ERROR: Temu no aplicó")
        self.fila("temu", "escribir", 4, resultado="DRY-RUN (no se escribió)")
        self.fila("temu", "escribir", 4, dry_run=True)
        self.fila("temu", "omitir", 4, resultado="FANOUT_TEMU apagado")
        self.fila("temu", "sin_cambio", 4, resultado="el canal ya tiene 4")
        self.vivo["temu"] = 4
        self.evento(5)
        self.assertEqual(self.api, [])

    def test_la_escritura_de_los_excedentes_si_cuenta(self):
        self.fila("temu", "escribir", 3, hace_min=20)       # `bajar`: excedente 8→3
        self.vivo["temu"] = 3
        self.evento(5)
        self.assertEqual(self.vivo["temu"], 5)

    def test_cuenta_la_ultima_no_la_primera(self):
        self.fila("temu", "escribir", 4, hace_min=50)
        self.fila("temu", "escribir", 5, hace_min=20)       # después le dejamos 5
        self.vivo["temu"] = 4                               # y Temu apartó una
        self.evento(5)
        self.assertEqual(self.api, [], "lo último que le dejamos es lo del censo")


class Candados(Arnes2):
    """3 · Los candados que pidió la revisión, contra los casos que los motivaron."""

    def test_lo_que_el_plan_omitio_no_se_toca_aunque_haya_escritura_reciente(self):
        # Sin el filtro `accion == "sin_cambio"` de `_rancios`: con el stock DESCONOCIDO,
        # `int(None)` tumbaba el evento entero (y con él la escritura a ML); con un
        # borrador, se le escribía stock a lo que el plan omitió a propósito.
        for estado, stock, por in ((None, None, "DESCONOCIDO"), ("5/None", 5, "Borrador")):
            with self.subTest(por=por):
                self.bitacora.clear()
                self.api.clear()
                self.fila("temu", "escribir", 4, hace_min=20)
                self.fila("tiktok", "escribir", 4, hace_min=20)
                self.vivo.update(temu=4, tiktok=4)
                self.censo["temu"], self.estado["temu"] = stock, estado or "2/8"
                d = self.evento(5)
                self.assertEqual(d["temu"]["accion"], "omitir")
                self.assertIn(por, d["temu"]["resultado"])
                self.assertEqual(self.del_canal("temu"), [], "a Temu ni se le lee")
                self.assertEqual(self.vivo["temu"], 4)
                self.assertEqual(len(self.eventos[-1]["acciones"]), 3, "el evento terminó con sus 3 filas")
                self.assertEqual(self.vivo["tiktok"], 5, "TikTok sí se verificó")

    def test_si_el_censo_la_releyo_despues_no_se_verifica(self):
        # Como JUGU-0089-PLA / DEC-0015-MOR: le escribimos 4 y DESPUÉS el censo leyó el
        # canal; lo que dice la libreta ya no es viejo, lo movió el canal solo.
        self.evento(4)
        self.vivo["temu"] = 5                                   # Temu lo movió solo…
        self.censo_ts["temu"] = self.ahora + timedelta(minutes=20)   # …y el censo lo vio
        self.api.clear()
        d = self.evento(5, minutos=30)
        self.assertEqual((d["temu"]["accion"], d["temu"]["resultado"]), ("sin_cambio", "el canal ya tiene 5"))
        self.assertEqual(self.del_canal("temu"), [])
        self.assertEqual(self.vivo["tiktok"], 5, "TikTok, sin censo después, sí")

    def test_un_censo_dentro_del_margen_no_cuenta_como_relectura(self):
        # El censo LEE antes de escribir la libreta: uno que la escribió 2 min después de
        # nuestra escritura pudo haber leído el canal antes.
        self.evento(4)
        self.censo_ts["temu"] = self.ahora + timedelta(seconds=F._CENSO_MARGEN_S - 60)
        d = self.evento(5, minutos=30)
        self.assertEqual(d["temu"]["accion"], "escribir")
        self.assertEqual(self.vivo["temu"], 5)

    def test_tras_una_venta_no_se_sube(self):
        # El patrón «venta + regreso por el delta de Odoo» (ACC-0250-NEG ×12): la pieza
        # sí se vendió; Woo la regresa porque Odoo aún no tiene la orden. Antes el
        # defecto dejaba el canal en N−1 por accidente; la regla lo respeta a propósito.
        self.evento(4, motivo="venta TEMU PO-128-0000000000000001")
        self.api.clear()
        d = self.evento(5, minutos=3, motivo="delta de Odoo aplicado")
        for canal in ("tiktok", "temu"):
            with self.subTest(canal=canal):
                self.assertEqual((d[canal]["accion"], d[canal]["resultado"]), ("sin_cambio", "el canal ya tiene 5"))
        self.assertEqual((self.api, self.vivo), ([], {"tiktok": 4, "temu": 4}))
        self.assertEqual(F._contadores["verificados"], 0)

    def test_tras_una_venta_si_se_baja(self):
        # La venta no excusa tener el canal ARRIBA: bajar siempre es seguro.
        self.fila("temu", "escribir", 8, hace_min=20, motivo="venta TIKTOK 586255595716904474")
        self.vivo["temu"] = 8
        d = self.evento(5)
        self.assertEqual(d["temu"]["resultado"], f"ok ({F.VERIFICADO}: 8→5; el censo decía 5)")
        self.assertEqual(self.vivo["temu"], 5)


class Direccion(Arnes2):
    """3b · Si lo último que le dejamos es MÁS que el objetivo, sólo se baja: un apartado
    de Temu dentro de la ventana (DEC-0015-MOR, 25 piezas el 2-oct) no se suelta."""

    def test_apartado_dentro_de_la_ventana_no_se_suelta(self):
        self.evento(8)                                   # rebote de Odoo: les escribimos 8
        self.vivo.update(temu=4, tiktok=3)               # Temu apartó 4; TikTok vendió 5
        self.api.clear()
        d = self.evento(5)
        for canal, tiene in (("temu", 4), ("tiktok", 3)):
            with self.subTest(canal=canal):
                self.assertEqual(d[canal]["accion"], "sin_cambio")
                self.assertEqual(d[canal]["resultado"],
                                 f"{F.VERIFICADO}: el canal tiene {tiene}, no se le sube a 5 "
                                 "(lo último que le escribimos fue 8)")
                self.assertEqual(d[canal]["stock_actual_canal"], tiene, "lo que se leyó")
        self.assertEqual(self.vivo, {"temu": 4, "tiktok": 3}, "nada se subió")
        self.assertNotIn(("temu", "bg.local.goods.stock.edit"), self.api)
        self.assertNotIn(("tiktok", "POST"), self.api)
        # Y la verificación cuenta como lo último que sabemos: no se relee en cada evento.
        self.api.clear()
        self.evento(5)
        self.assertEqual(self.api, [])

    def test_bajar_y_ya_tenia_el_objetivo(self):
        self.evento(8)
        self.vivo.update(temu=5, tiktok=5)
        d = self.evento(5)
        for canal in ("temu", "tiktok"):
            with self.subTest(canal=canal):
                self.assertEqual((d[canal]["accion"], d[canal]["resultado"]),
                                 ("sin_cambio", f"{F.VERIFICADO}: el canal ya tiene 5 "
                                                "(lo último que le escribimos fue 8)"))

    def test_temu_baja_y_la_relectura_queda_debajo(self):
        self.evento(8)
        self.vende_al_editar = 1                         # una venta entre la escritura y la relectura
        d = self.evento(5)
        self.assertEqual((d["temu"]["accion"], d["temu"]["resultado"]),
                         ("escribir", f"ok ({F.VERIFICADO}: 8→4, no se sube a 5; el censo decía 5)"))
        self.assertEqual(self.vivo["temu"], 4)

    def test_el_escritor_recibe_la_direccion(self):
        llamadas = []

        def escritor(cuenta, item, cantidad, solo_bajar=False, verificar=False):
            llamadas.append((cantidad, solo_bajar, verificar))
            return True, f"{F.YA_TENIA} {cantidad} en vivo)"
        with mock.patch.dict(F._ESCRITORES_VERIFICAR, {"temu": escritor}):
            self.fila("temu", "escribir", 8)
            self.evento(5)                               # 8 → 5: bajar
            self.fila("temu", "escribir", 2)
            self.evento(5)                               # 2 → 5: subir
        self.assertEqual(llamadas, [(5, True, True), (5, False, True)])

    def test_lo_que_le_llega_al_seguro(self):
        visto = []

        def seguro(paso, *args):
            if paso == "abrir":
                return "ctx"
            if paso == "despues" and args[1].get("canal") == "temu":
                visto.append((args[1]["resultado"].split(":")[0], args[2]))
        with mock.patch.object(F, "_seguro", side_effect=seguro):
            self.fila("temu", "escribir", 8)
            self.vivo["temu"] = 4
            self.evento(5)                               # no se le sube → None
            self.fila("temu", "escribir", 2)
            self.vivo["temu"] = 2
            self.evento(5, minutos=1)                    # se le escribe → True
            self.fila("temu", "escribir", 2)
            self.falla["temu"] = "x"
            self.evento(5, minutos=1)                    # error → False
        self.assertEqual(visto, [(F.VERIFICADO, None), (f"ok ({F.VERIFICADO}", True),
                                 (f"ERROR ({F.VERIFICADO})", False)])


class Ensayo(Arnes2):
    """4b · FANOUT_VERIFICAR_RANCIO_ENSAYO: con la bandera apagada, anota lo que
    verificaría y no llama al canal."""

    AJUSTES = dict(Arnes2.AJUSTES, fanout_verificar_rancio=False, fanout_verificar_rancio_ensayo=True)

    def test_anota_sin_llamar_al_canal(self):
        self.evento(4)
        self.evento(9)
        self.api.clear()
        d = self.evento(5)
        self.assertEqual(self.api, [])
        self.assertEqual(self.vivo, {"tiktok": 9, "temu": 9}, "el defecto sigue: es un ensayo")
        for canal in ("tiktok", "temu"):
            with self.subTest(canal=canal):
                self.assertEqual((d[canal]["accion"], d[canal]["resultado"]),
                                 ("sin_cambio", f"ENSAYO ({F.VERIFICARIA}, sólo bajar: "
                                                "lo último que le escribimos fue 9)"))
        # No cuenta como «lo último que le dejamos»: el siguiente evento lo vuelve a anotar.
        d = self.evento(5)
        self.assertTrue(d["temu"]["resultado"].startswith(f"ENSAYO ({F.VERIFICARIA}"))
        self.assertEqual(F._contadores["verificados"], 0)

    def test_con_la_bandera_encendida_el_ensayo_no_estorba(self):
        self.evento(4)
        with mock.patch.object(F.settings, "fanout_verificar_rancio", True):
            d = self.evento(5)
        self.assertEqual(d["temu"]["resultado"], f"ok ({F.VERIFICADO}: 4→5; el censo decía 5)")

    def test_sin_bandera_ni_ensayo_ni_se_le_pregunta_a_kubera(self):
        self.evento(4)
        with mock.patch.object(F.settings, "fanout_verificar_rancio_ensayo", False):
            self.evento(5)
        self.assertEqual(self.consultas, [])


class IdenticoApagado(Arnes2):
    """4 · Con la bandera apagada, en dry-run o sin bitácora en kubera: como hoy."""

    def _rancio(self) -> dict[str, dict]:
        self.evento(4)
        self.api.clear()
        self.consultas.clear()
        return self.evento(5)

    def _como_hoy(self, d: dict[str, dict]):
        for canal in ("tiktok", "temu"):
            with self.subTest(canal=canal):
                self.assertEqual(self.vivo[canal], 4, "el defecto sigue: así era")
                self.assertEqual((d[canal]["accion"], d[canal]["resultado"]),
                                 ("sin_cambio", "el canal ya tiene 5"))
        self.assertEqual((self.api, self.consultas), ([], []), "ni canal ni kubera")

    def test_bandera_apagada(self):
        with mock.patch.object(F.settings, "fanout_verificar_rancio", False):
            d = self._rancio()
        self._como_hoy(d)
        self.assertEqual(F._contadores["verificados"], 0)

    def test_bandera_apagada_la_fila_es_la_del_plan_tal_cual(self):
        with mock.patch.object(F.settings, "fanout_verificar_rancio", False):
            self.evento(4)
            self.woo = 5
            p = F.plan(SKU)
            self.evento(5)
        self.assertEqual(self.eventos[-1]["acciones"], p["acciones"])

    def test_sin_bitacora_en_kubera(self):
        with mock.patch.object(F.settings, "supabase_write_fanout_log", False):
            d = self._rancio()
        self._como_hoy(d)

    def test_ventana_en_cero_no_verifica(self):
        # 0 apaga (antes era «0 → 2 h» por el `or 2.0`), y un negativo o ilegible, también.
        with mock.patch.object(F.settings, "fanout_verificar_rancio_h", 0):
            self._como_hoy(self._rancio())
        dudoso = [{"canal": "temu", "cuenta": "TEMU", "item_id": G, "accion": "sin_cambio",
                   "stock_actual_canal": 5, "objetivo": 5}]
        for h in (0.0, -1, None, "x"):
            with self.subTest(h=h), mock.patch.object(F.settings, "fanout_verificar_rancio_h", h):
                self.assertEqual(F._rancios(SKU, dudoso), {})
        self.assertEqual(self.consultas, [])

    def test_en_dry_run_anota_lo_que_verificaria_sin_llamar_al_canal(self):
        self.evento(4)
        self.api.clear()
        self.consultas.clear()
        with mock.patch.object(F, "dry_run", return_value=True):
            d = self.evento(5)
        self.assertEqual(self.api, [], "el canal ni se lee")
        self.assertEqual(len(self.consultas), 1, "sí se le pregunta a kubera")
        for canal in ("tiktok", "temu"):
            with self.subTest(canal=canal):
                self.assertEqual((d[canal]["accion"], d[canal]["resultado"]),
                                 ("sin_cambio", f"DRY-RUN ({F.VERIFICARIA}: lo último que le escribimos fue 4)"))
        self.assertEqual(self.vivo, {"tiktok": 4, "temu": 4})
        self.assertEqual(F._contadores["verificados"], 0)


class Canales(Arnes2):
    """5 · Sólo TikTok y Temu, y sólo con su candado encendido."""

    def test_mercado_libre_nunca_se_verifica(self):
        self.fila("mercado_libre", "escribir", 4)
        d = self.evento(5)
        self.assertEqual((d["mercado_libre"]["accion"], self.ml), ("sin_cambio", []))

    def test_amazon_nunca_se_verifica(self):
        filas = [{"canal": "amazon", "cuenta": "SANCORFASHION", "item_id": SKU, "accion": "sin_cambio",
                  "stock_actual_canal": 5, "objetivo": 5, "omitido_por": "el canal ya tiene 5"},
                 {"canal": "mercado_libre", "cuenta": "BEKURA", "item_id": ML, "accion": "sin_cambio",
                  "stock_actual_canal": 5, "objetivo": 5, "omitido_por": "el canal ya tiene 5"}]
        self.fila("amazon", "escribir", 4)
        self.assertEqual(F._rancios(SKU, filas), {})
        self.assertEqual(self.consultas, [], "ni se le pregunta a kubera")

    def test_canal_con_su_candado_apagado_no_se_verifica(self):
        self.evento(4)
        self.api.clear()
        with mock.patch.object(F.settings, "fanout_tiktok", False):
            d = self.evento(5)
        self.assertEqual((self.vivo["tiktok"], d["tiktok"]["accion"]), (4, "sin_cambio"))
        self.assertEqual(self.del_canal("tiktok"), [])
        self.assertEqual(self.vivo["temu"], 5, "Temu sí")
        with mock.patch.object(F.settings, "fanout_canales", "mercado_libre,tiktok"):
            self.evento(4)
            self.api.clear()
            self.evento(5)
        self.assertEqual(self.del_canal("temu"), [], "fuera de FANOUT_CANALES")


class Errores(Arnes2):
    """6 · Un error no rompe la cola y queda en la bitácora."""

    def test_error_del_canal_en_la_lectura_en_vivo(self):
        self.evento(4)
        self.falla["tiktok"] = "TikTok /product/202309/products → code=36009004"
        d = self.evento(5)
        self.assertEqual(d["tiktok"]["accion"], "escribir")
        self.assertTrue(d["tiktok"]["resultado"].startswith(f"ERROR ({F.VERIFICADO}): RuntimeError"))
        # `stock_canal` = lo último que le escribimos, no el censo (que es el objetivo):
        # la trazabilidad dice «error · sigue en 4», no «sigue en 5».
        fila = [f for f in self.bitacora if f["canal"] == "tiktok"][-1]
        self.assertEqual(fila["stock_canal"], 4)
        celda = V._celda_reparto([{**fila, "objetivo": 5}])
        self.assertEqual(celda["texto"], "error · sigue en 4")
        self.assertEqual(self.vivo["temu"], 5, "el otro destino siguió")
        self.assertEqual(F._contadores["errores"], 1)
        # Con el canal de vuelta, el siguiente evento lo arregla (el error no cuenta
        # como «lo último que le dejamos»).
        del self.falla["tiktok"]
        self.evento(5)
        self.assertEqual(self.vivo["tiktok"], 5)

    def test_error_de_temu(self):
        self.evento(4)
        self.falla["temu"] = "Temu bg.local.goods.list.query: errorCode=3000032"
        d = self.evento(5)
        self.assertTrue(d["temu"]["resultado"].startswith(f"ERROR ({F.VERIFICADO}):"))
        self.assertEqual(self.vivo["tiktok"], 5)

    def test_tiktok_sin_el_almacen_de_ventas_no_escribe_a_ciegas(self):
        async def sin_almacen(ruta, token, params, cuerpo=None, metodo="GET"):
            self.api.append(("tiktok", metodo))
            return {"skus": [{"id": "S1", "inventory": []}]}
        with mock.patch.object(TK, "llamar", side_effect=sin_almacen):
            ok, det = F._escribir_tiktok("KUBERA", P, 5, verificar=True)
        self.assertEqual((ok, det), (False, "stock vivo de TikTok ilegible (no se verifica a ciegas)"))
        self.assertNotIn(("tiktok", "POST"), self.api)

    def test_kubera_caida_deja_el_sin_cambio_como_siempre(self):
        self.evento(4)
        self.api.clear()
        self.kubera_caida = True
        with self.assertLogs("omnicanal.fanout", "WARNING") as avisos:
            d = self.evento(5)
        self.assertEqual((d["temu"]["accion"], self.api), ("sin_cambio", []))
        self.assertIn("no pude leer la última escritura", avisos.output[0])

    def test_un_escritor_que_revienta_tampoco_rompe_la_cola(self):
        self.evento(4)
        with mock.patch.dict(F._ESCRITORES_VERIFICAR, {"temu": mock.Mock(side_effect=ValueError("x"))}):
            d = self.evento(5)
        self.assertEqual(d["temu"]["resultado"], f"ERROR ({F.VERIFICADO}): ValueError: x")
        self.assertEqual(self.vivo["tiktok"], 5)


# ══════════════════════════════════════════════════════════════════════════════

class SeguroReactiva(Arnes):
    """7 · El seguro de stock 0 la apagó y le escribió el 0; Woo vuelve antes del
    censo. Hoy: «esperando stock» hasta el censo + el reencolado diario. Con la
    verificación: el stock se escribe y la regresa a la venta en el mismo evento."""

    AJUSTES = dict(Arnes.AJUSTES, fanout_verificar_rancio=True, fanout_verificar_rancio_h=2.0)

    def setUp(self):
        super().setUp()
        self.lectura_atrasada = False

        def fetch_all(sql, params=None):
            if sql is not F._SQL_ULTIMA_CONOCIDA:
                raise AssertionError(f"la prueba hizo otra consulta: {sql[:80]}")
            bitacora = [{"id": i, "ts": datetime.now(timezone.utc), "sku": e["sku"],
                         "dry_run": e["dry_run"], "objetivo": e["objetivo"], "canal": a["canal"],
                         "item_id": str(a["item_id"]), "accion": a["accion"],
                         "resultado": str(a.get("resultado") or a.get("omitido_por") or "")}
                        for i, (e, a) in enumerate((e, a) for e in self.eventos for a in e["acciones"])]
            return ultima_conocida(bitacora, params, datetime.now(timezone.utc))

        def verificador(c):
            def verificar(cuenta, item, cantidad, solo_bajar=False, verificar=False):
                self.llamadas.append(("verifica", c, str(item), cantidad))
                d = self.canal[(c, str(item))]
                vivo = d["stock"] if d["stock"] is not None else d["skus"][0]["cantidad"]
                if vivo == cantidad:
                    return True, f"{F.YA_TENIA} {cantidad} en vivo)"
                if self.lectura_atrasada:       # Temu: su lectura va ~5 s detrás de la escritura
                    return True, f"ok ({vivo}→{cantidad})"
                d["stock"] = cantidad if d["stock"] is not None else None
                for s in d["skus"]:
                    s["cantidad"] = cantidad
                return True, f"ok ({vivo}→{cantidad})"
            return verificar

        for p in (mock.patch.object(sdb, "fetch_all", side_effect=fetch_all),
                  mock.patch.dict(F._ESCRITORES_VERIFICAR,
                                  {"temu": verificador("temu"), "tiktok": verificador("tiktok")})):
            p.start()
            self.addCleanup(p.stop)

    def _apagada_y_vuelve(self):
        self.aplicar(plan(accion("temu", 4)))                    # Woo 0: la apaga y escribe 0
        self.assertIsNotNone(self._marca("temu", G_SEGURO))
        self.llamadas.clear()
        # Woo vuelve a 4 antes del censo: la libreta sigue en 4 ⇒ el plan dice «sin cambio».
        self.aplicar(plan(accion("temu", 4, accion="sin_cambio", estado="3/2",
                                 omitido="el canal ya tiene 4"), objetivo=4))

    def test_reactiva_en_el_mismo_evento(self):
        self._apagada_y_vuelve()
        self.assertEqual(self.llamadas, [("verifica", "temu", G_SEGURO, 4), ("temu.venta", G_SEGURO, True, None)])
        self.assertEqual(self.de(S.ACC_REACTIVAR)[0]["resultado"], "ok (3/2→2/8)")
        self.assertIsNone(self._marca("temu", G_SEGURO), "la marca queda cerrada")
        (fila,) = self.eventos[-1]["acciones"]
        self.assertEqual((fila["accion"], fila["resultado"]),
                         ("escribir", f"ok ({F.VERIFICADO}: 0→4; el censo decía 4)"))

    def test_reactiva_aunque_la_lectura_del_canal_vaya_atras(self):
        # El seguro relee el canal antes de prender; si esa lectura aún no ve el stock
        # recién escrito, lo que decide es que la verificación dio «ok».
        self.lectura_atrasada = True
        self._apagada_y_vuelve()
        self.assertEqual(self.llamadas, [("verifica", "temu", G_SEGURO, 4), ("temu.venta", G_SEGURO, True, None)])

    def test_sin_la_bandera_espera_al_censo_como_hoy(self):
        self.ajustes(fanout_verificar_rancio=False)
        self._apagada_y_vuelve()
        self.assertEqual(self.ventas(), [])
        self.assertIn("esperando stock (vivo 0, objetivo 4)", self.de(S.ACC_SIN_CAMBIO)[0]["resultado"])


# ══════════════════════════════════════════════════════════════════════════════

class Trazabilidad(unittest.TestCase):
    """8 · La celda lo dice; las demás quedan igual."""

    def _celda(self, accion, resultado, stock=4, objetivo=5):
        return V._celda_reparto([{"accion": accion, "resultado": resultado, "stock_canal": stock,
                                  "objetivo": objetivo}])

    def test_escrita_tras_verificar(self):
        r = f"ok ({F.VERIFICADO}: 4→5; el censo decía 5)"
        self.assertEqual(self._celda("escribir", r), {"k": "ok", "texto": "4 → 5 · en vivo", "detalle": r})

    def test_ya_tenia_tras_verificar(self):
        r = f"{F.VERIFICADO}: el canal ya tiene 5 (lo último que le escribimos fue 4)"
        self.assertEqual(self._celda("sin_cambio", r, stock=5),
                         {"k": "igual", "texto": "ya tenía 5 · en vivo", "detalle": r})

    def test_error_tras_verificar(self):
        c = self._celda("escribir", f"ERROR ({F.VERIFICADO}): RuntimeError: x")
        self.assertEqual(c["k"], "mal")
        self.assertEqual(c["texto"], "error · sigue en 4", "lo último que le escribimos, no el objetivo")
        self.assertIn(F.VERIFICADO, c["detalle"])

    def test_no_se_le_sube(self):
        r = f"{F.VERIFICADO}: el canal tiene 4, no se le sube a 5 (lo último que le escribimos fue 8)"
        self.assertEqual(self._celda("sin_cambio", r, stock=4),
                         {"k": "igual", "texto": "tiene 4 · no se sube · en vivo", "detalle": r})

    def test_verificaria_en_dry_run_o_ensayo(self):
        for pre in ("DRY-RUN", "ENSAYO"):
            r = f"{pre} ({F.VERIFICARIA}: lo último que le escribimos fue 4)"
            self.assertEqual(self._celda("sin_cambio", r, stock=5),
                             {"k": "igual", "texto": "ya tenía 5 · verificaría", "detalle": r})

    def test_las_de_siempre_no_cambian(self):
        self.assertEqual(self._celda("escribir", "ok"), {"k": "ok", "texto": "4 → 5"})
        self.assertEqual(self._celda("sin_cambio", "el canal ya tiene 5", stock=5),
                         {"k": "igual", "texto": "ya tenía 5"})


class Matriz(unittest.TestCase):
    """8 · La matriz y el rastro toman una verificación como el último valor conocido:
    antes seguían pintando «escrito X» como pendiente hasta el censo."""

    T = datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc)
    L = {"canal": "temu", "status": "2/8", "stock_own": 5, "updated_at": T, "act": "2026-10-09 10:00:00"}
    HOY = "2026-10-09"

    def _w(self, **campos):
        return {"ts": self.T + timedelta(minutes=40), "hora": "2026-10-09 10:40:00", **campos}

    def test_ya_tenia_en_vivo(self):
        w = self._w(accion="sin_cambio", objetivo=5, stock_canal=5,
                    resultado=f"{F.VERIFICADO}: el canal ya tiene 5 (lo último que le escribimos fue 8)")
        c = V._celda_matriz(self.L, w, 5, self.HOY)
        self.assertEqual((c["k"], c["v"]), ("igual", "5"))
        self.assertIn("verificado", c["s"])

    def test_no_se_le_sube_vale_lo_leido(self):
        w = self._w(accion="sin_cambio", objetivo=5, stock_canal=4,
                    resultado=f"{F.VERIFICADO}: el canal tiene 4, no se le sube a 5 (lo último que le escribimos fue 8)")
        c = V._celda_matriz(self.L, w, 5, self.HOY)
        self.assertEqual((c["k"], c["v"], c["d"]), ("menos", "4", "−1"))

    def test_un_sin_cambio_cualquiera_no_cuenta(self):
        # La libreta de una fila «sin cambio» normal ES el censo: no es más nueva.
        w = self._w(accion="sin_cambio", objetivo=5, stock_canal=5, resultado="el canal ya tiene 5")
        c = V._celda_matriz(dict(self.L, stock_own=8), w, 5, self.HOY)
        self.assertEqual((c["k"], c["v"]), ("mas", "8"))

    def test_los_sql_y_el_rastro_la_incluyen(self):
        self.assertEqual(V._VERIFICADO, F.VERIFICADO)
        filtro = "(accion = 'sin_cambio' and resultado like 'verificado en vivo%%')"
        self.assertIn(filtro, " ".join(V._SQL_VIVAS.split()))
        self.assertIn("coalesce(stock_canal, objetivo)", V._SQL_VIVAS)
        self.assertIn(filtro, " ".join(inspect.getsource(V.matriz).split()))
        self.assertIn("startswith(_VERIFICADO)", inspect.getsource(V.historia))


class PlanIgual(unittest.TestCase):
    """8 · Los candados de `plan` salieron a `_bloqueo_canal` sin cambiar una letra."""

    def _plan(self, **ajustes):
        filas = {SKU: {f"{c}|{CUENTA[c]}": {
            "canal": c, "cuenta": CUENTA[c], "item_id": ITEM[c], "stock_real": 3, "es_full": False,
            "estado_canal": {"tiktok": "ACTIVATE", "temu": "2/8"}.get(c),
            "situacion": "active" if c in ("mercado_libre", "walmart") else None}
            for c in ("tiktok", "temu", "mercado_libre")}}
        filas[SKU]["walmart|X"] = {"canal": "walmart", "cuenta": "X", "item_id": "W1", "stock_real": 3,
                                   "es_full": False, "situacion": "active"}
        base = dict(fanout_canales="", fanout_tiktok=True, fanout_temu=True, fanout_reserva=0)
        with mock.patch.multiple(F.settings, **{**base, **ajustes}), \
                mock.patch.object(F, "seguro_encendido", return_value=False), \
                mock.patch.object(F, "_stock_drop", return_value=5), \
                mock.patch.object(channel_read, "leer_inventario", return_value=filas):
            return {a["canal"]: (a["accion"], a["omitido_por"]) for a in F.plan(SKU)["acciones"]}

    def test_los_textos_de_siempre(self):
        self.assertEqual(self._plan(), {
            "tiktok": ("escribir", None), "temu": ("escribir", None),
            "mercado_libre": ("escribir", None),
            "walmart": ("omitir", "sin escritor implementado para 'walmart'")})
        p = self._plan(fanout_canales="mercado_libre,walmart", fanout_tiktok=False, fanout_temu=False)
        self.assertEqual(p["tiktok"], ("omitir", "canal 'tiktok' no habilitado en FANOUT_CANALES"))
        p = self._plan(fanout_tiktok=False, fanout_temu=False)
        self.assertEqual(p["tiktok"], ("omitir", "FANOUT_TIKTOK apagado — el escritor está listo, falta encenderlo"))
        self.assertEqual(p["temu"], ("omitir", "FANOUT_TEMU apagado — el escritor está listo, falta encenderlo"))

    def test_sin_cambio_va_antes_que_los_candados(self):
        # Por eso `_rancios` le pregunta a `_bloqueo_canal` por su cuenta.
        with mock.patch.object(F, "_reserva", return_value=2):      # objetivo 3 = lo del censo
            p = self._plan(fanout_tiktok=False)
        self.assertEqual(p["tiktok"], ("sin_cambio", "el canal ya tiene 3"))


class Consulta(unittest.TestCase):
    """El SQL va suplantado arriba: aquí se fija su texto y lo que se le pasa."""

    def test_texto(self):
        sql = " ".join(F._SQL_ULTIMA_CONOCIDA.split())
        for pedazo in ("from ops.fanout_log", "sku = %(s)s", "canal = any(%(c)s)", "not dry_run",
                       "ts > now() - %(h)s * interval '1 hour'",
                       "(accion = 'escribir' and resultado ilike 'ok%%')",
                       "(accion = 'sin_cambio' and resultado like %(v)s)",
                       "distinct on (canal, item_id)", "order by canal, item_id, id desc",
                       "u.motivo", "from channel.listings l",
                       "l.sku = %(s)s and l.canal = u.canal and l.listing_id = u.item_id",
                       "l.updated_at > u.ts + %(m)s * interval '1 second') as releida"):
            self.assertIn(pedazo, sql)

    def test_parametros(self):
        a = {"canal": "tiktok", "cuenta": "KUBERA", "item_id": P, "accion": "sin_cambio",
             "stock_actual_canal": 5, "objetivo": 5}
        with mock.patch.multiple(F.settings, fanout_verificar_rancio=True, fanout_verificar_rancio_h=2.0,
                                 supabase_write_fanout_log=True, fanout_canales="", fanout_tiktok=True), \
                mock.patch.object(sdb, "fetch_all", return_value=[]) as fa:
            self.assertEqual(F._rancios(SKU, [a]), {})
        fa.assert_called_once_with(F._SQL_ULTIMA_CONOCIDA,
                                   {"s": SKU, "c": ["tiktok"], "h": 2.0, "v": "verificado en vivo%", "m": 300})


if __name__ == "__main__":
    unittest.main()
