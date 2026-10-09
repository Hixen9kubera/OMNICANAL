"""Pruebas del SEGURO «stock 0 ⇒ fuera de la venta» (`fanout_seguro` y sus ganchos en
`fanout_stock`, `temu`, `tiktok` y `fanout_vivo`).

── QUÉ FIJAN ────────────────────────────────────────────
Escribirle 0 a Temu no bastó: ACC-0574-LIL recibió 0 el 1-oct-2026 y aun así vendió
23, 10 y 1 piezas. Con el objetivo en 0 el seguro saca la publicación de la venta y
la regresa cuando vuelve el stock. Tiene que ser aburrido y, sobre todo, no hacer daño:

  1. SIEMPRE ENCENDIDO, SIN INTERRUPTOR (decisión de Brandon, 9-oct-2026): corre
     siempre que el fan-out esté encendido; con el fan-out apagado no lee ni anota
     nada. Donde el fan-out no escribe de verdad cae en ensayo FORZADO: no llama a
     apagar ni a prender y sus filas van con `dry_run`. (`SinInterruptor`)
  2. PRIMERO SE APAGA, LUEGO SE ESCRIBE EL 0, y el 0 se escribe aunque el seguro
     falle. Vale para `escribir` y para `sin_cambio`. Woo ilegible no es 0.
  3. SE DECIDE CON EL ESTADO VIVO: ilegible o que no converge ⇒ error y sin marca.
  4. UNA HERMANA CON STOCK NO SE APAGA; ni un padre, ni FULL, ni un excluido.
  5. SÓLO SE REACTIVA LO PROPIO: sin marca, con el estado movido por otro o ya
     prendida, no se llama al canal. Primero el stock, luego la venta, y sólo con el
     objetivo sostenido arriba de 0.
  6. TOPES por vuelta y por día; sin poder leer el tope no se actúa.
  7. NO ENTRA EN BUCLE: tres errores seguidos lo cortan y el recuperador no reencola
     por sus filas.
  8. SI LA MARCA NO SE PUDO GUARDAR, avisa en rojo y no vuelve a apagar.

Y lo que destapó la revisión del 7-oct-2026 (cada punto tiene su clase al final):
  9. LOS TOPES CUENTAN LLAMADAS, no éxitos, y el intento se sella ANTES de llamar;
     sin poder sellarlo no se llama. Llegar al tope avisa. (`Intentos`, `TopePorVuelta`)
 10. UN REINICIO A MEDIA VUELTA no deja una publicación apagada sin rastro: el
     siguiente censo lee el canal y sella lo que pasó. (`Reconciliar`)
 11. NADA SE QUEDA APAGADO EN SILENCIO: lo apagado que ya tiene stock se dice y se
     lista aunque la reactivación esté apagada. (`ApagadasConStock`, `PanelPendientes`)
 12. REACTIVAR COMPRUEBA que quedó a la venta antes de darlo por hecho; en TikTok
     vigila la auditoría. (`ReactivarVerifica`)
 13. LOS AVISOS NO SE PIERDEN por el candado de uno por tipo por hora. (`AvisosQueNoSePierden`)
 14. LO QUE TEMU MUEVE SOLA no suelta la marca. (`MarcasDeTemu`, `MarcasHuerfanas`)
 15. `soltar` no hace nada con el fan-out apagado (`SoltarConElFanoutApagado`) y, con
     el fan-out apagado, todo es IDÉNTICO a antes de que existiera el seguro
     (`IdenticoApagado`).
 16. EL SQL de la marca, los topes y el corte, que en lo demás va suplantado. (`Consultas`)

── NO SE TOCA NADA REAL ─────────────────────────────────
Los canales, la bitácora, el plan, los escritores de stock, las alertas y el reloj van
suplantados; además, mientras corre este módulo, cualquier intento de tocar la base o
la red revienta. Los SKUs son INVENTADOS: el repo es público.

    cd backend && python -m unittest tests.test_fanout_seguro -v
"""
from __future__ import annotations

import asyncio
import re
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import alertas  # noqa: E402
from services import fanout_read  # noqa: E402
from services import fanout_recuperar as R  # noqa: E402
from services import fanout_seguro as S  # noqa: E402
from services import fanout_stock as F  # noqa: E402
from services import fanout_vivo as V  # noqa: E402
from services import temu as TM  # noqa: E402
from services import tiktok as TK  # noqa: E402

# Las funciones de verdad de los dos clientes, antes de que el arnés las suplante.
_REAL = {"temu.cambiar_venta": TM.cambiar_venta, "tiktok.desactivar": TK.desactivar,
         "tiktok.activar": TK.activar}

# Con qué estados de Temu nace el seguro (lo fija `SinInterruptor`).
TEMU_ESTADOS_DE_FABRICA = "2/8,3/1"

SKU = "ZZZ-0001-AZL"
HERMANA = "ZZZ-0001-ROJ"
G = "900001"        # goodsId inventado
P = "770001"        # product_id inventado
CUENTA = {"temu": "TEMU", "tiktok": "KUBERA"}

_TRAMPAS: list = []


def setUpModule():
    """Mientras corre este módulo, tocar la base o la red revienta."""
    import httpx

    from services import db, wp_db
    from services import supabase_db as sdb

    def trampa(nombre):
        def f(*a, **k):
            raise AssertionError(f"la prueba tocó {nombre}: falta un doble")
        return f

    for mod, nombres in ((sdb, ("fetch_all", "fetch_one", "fetch_scalar", "execute",
                                "execute_returning", "get_cursor")),
                         (db, ("fetch_all", "fetch_one", "execute", "get_cursor")),
                         (wp_db, ("_fetch_all",)),
                         (httpx, ("AsyncClient", "get", "post", "put", "patch"))):
        for n in nombres:
            if hasattr(mod, n):
                p = mock.patch.object(mod, n, side_effect=trampa(f"{mod.__name__}.{n}"))
                p.start()
                _TRAMPAS.append(p)


def tearDownModule():
    for p in _TRAMPAS:
        p.stop()


def accion(canal, actual, objetivo=0, accion="escribir", estado=None, item="", omitido=None,
           fuera=False):
    """Una acción del plan, como la arma `fanout_stock.plan`."""
    return {"canal": canal, "cuenta": CUENTA[canal], "item_id": item or (G if canal == "temu" else P),
            "stock_actual_canal": actual, "objetivo": objetivo, "accion": accion,
            "omitido_por": omitido, "fuera": fuera,
            "estado_canal": estado if estado is not None else ("2/8" if canal == "temu" else "ACTIVATE")}


def plan(*acciones, objetivo=0, sku=SKU):
    return {"sku": sku, "ok": True, "stock_drop": objetivo, "reserva": 0, "objetivo": objetivo,
            "acciones": [dict(a, objetivo=objetivo) for a in acciones]}


class Arnes(unittest.TestCase):
    """Canales, bitácora, reloj y alertas falsos. Todo lo que mueve algo queda en
    `self.llamadas`, EN ORDEN: así se prueba qué pasó antes de qué."""

    AJUSTES = dict(
        # El seguro no tiene interruptor: lo enciende el fan-out (`fanout_enabled`, abajo).
        # Los topes nacen en 0 (sin tope); aquí van puestos para poder probarlos.
        fanout_cero_tope_vuelta=5,
        fanout_cero_tope_dia=20, fanout_cero_tope_reactivar_dia=20, fanout_cero_excluir="",
        fanout_cero_espera_min=30, fanout_cero_temu_estados="2/8",
        fanout_cero_temu_nivel_sku=False, fanout_cero_espera_s=8.0, fanout_cero_relecturas=3,
        fanout_cero_bloque_n=10,
        # El fan-out escribe de verdad (si no, el seguro cae en ensayo forzado).
        fanout_enabled=True, fanout_dry_run=False, fanout_canales="", fanout_temu=True,
        fanout_tiktok=True, supabase_write_fanout_log=True, mysql_enabled=False)

    def setUp(self):
        self.t = [1_000_000.0]
        self.llamadas: list[tuple] = []
        self.orden: list[tuple] = []         # sellos en la bitácora y llamadas al canal, EN ORDEN
        self.suprimir: set = set()           # tipos de aviso que `alertas.avisar` se traga (su candado)
        self.suprimidos: list[tuple] = []
        self.lecturas: list[tuple] = []
        self.filas: list[dict] = []          # lo que llegó a la bitácora
        self.eventos: list[dict] = []        # lo que persistió el fan-out
        self.avisos: list[tuple] = []
        self.reflejos: list[tuple] = []
        self.dormidas: list[float] = []
        self.encolados: list[tuple] = []
        self.canal = {
            ("temu", G): {"estado": "2/8", "stock": 4,
                          "skus": [{"id": 5001, "seller_sku": SKU, "cantidad": 4}]},
            ("tiktok", P): {"estado": "ACTIVATE", "stock": None,
                            "skus": [{"id": "S1", "seller_sku": SKU, "cantidad": 3}]},
        }
        self.ilegibles: set = set()
        self.rancias: dict = {}          # (canal, item) → estados viejos que el canal contesta tras la llamada
        self.canal_falla = False         # la llamada de apagar/prender revienta
        self.canal_obedece = True        # la llamada contesta bien Y el estado cambia
        self.al_activar_tiktok = "PENDING"
        self.al_prender_temu = "2/8"     # en qué estado deja Temu la publicación al pedirle regresar
        self.escritor_ok = (True, "ok")
        self.bitacora_caida = False      # `registrar` revienta
        self.tope_caido = False
        self.usadas_extra: dict = {}
        self.padres: dict = {}
        self.huerfanas: list = []
        self.ajeno = None
        self.ajeno_args: list = []       # con qué se preguntó por el cambio ajeno
        self.bloque = None               # un apagado EN BLOQUE del canal después de la marca
        self.edad: float | None = 3600.0
        self.woo: dict = {}
        self.foto: dict = {}             # SKU → stock de Woo en la foto de stock_watch
        self.censo: dict = {}            # (canal, item) → estado que vio el censo (None = el vivo)
        self.listing: dict = {}          # (canal, SKU) → listing_id que hoy tiene el censo
        self.pend: list = []             # lo que quedó a medias (`pendientes`)
        self.auditoria: list = []        # reactivaciones de TikTok en auditoría

        async def cambiar_venta(goods_id, en_venta, sku_ids=None):
            self.llamadas.append(("temu.venta", str(goods_id), en_venta, sku_ids))
            self.orden.append(("canal", "temu", en_venta))
            if self.canal_falla:
                raise RuntimeError("Temu bg.local.goods.sale.status.set: errorCode=3000032")
            if self.canal_obedece and not sku_ids:
                self.canal[("temu", str(goods_id))]["estado"] = self.al_prender_temu if en_venta else "3/2"
            return {"success": True}

        async def desactivar(pid, token=None, shop_cipher=None):
            self.llamadas.append(("tiktok.desactivar", str(pid)))
            self.orden.append(("canal", "tiktok", False))
            if self.canal_falla:
                raise RuntimeError("TikTok /products/deactivate → code=12052901")
            if self.canal_obedece:
                self.canal[("tiktok", str(pid))]["estado"] = "SELLER_DEACTIVATED"
            return {}

        async def activar(pid, token=None, shop_cipher=None):
            self.llamadas.append(("tiktok.activar", str(pid)))
            self.orden.append(("canal", "tiktok", True))
            if self.canal_falla:
                raise RuntimeError("TikTok /products/activate → code=12052093")
            if self.canal_obedece:
                self.canal[("tiktok", str(pid))]["estado"] = self.al_activar_tiktok
            return {}

        def vivo(c):
            def leer(item):
                self.lecturas.append((c, str(item)))
                if (c, str(item)) in self.ilegibles:
                    return {"ok": False, "motivo": "ConnectError"}
                d = self.canal[(c, str(item))]
                estado = d["estado"]
                ya_se_llamo = any(l[0] != "stock" and l[1] == str(item) for l in self.llamadas)
                if ya_se_llamo and self.rancias.get((c, str(item))):
                    estado = self.rancias[(c, str(item))].pop(0)
                return {"ok": True, "estado": estado, "stock": d["stock"],
                        "skus": [dict(s) for s in d["skus"]]}
            return leer

        def escritor(c):
            def escribir(cuenta, item, cantidad, solo_bajar=False):
                self.llamadas.append(("stock", c, str(item), cantidad))
                if self.escritor_ok[0]:
                    d = self.canal[(c, str(item))]
                    d["stock"] = cantidad if d["stock"] is not None else None
                    for s in d["skus"]:
                        if s["seller_sku"] == SKU:
                            s["cantidad"] = cantidad
                return self.escritor_ok
            return escribir

        def registrar(fila):
            if self.bitacora_caida:
                self.llamadas.append(("registrar-falla",))
                raise RuntimeError("kubera no contesta (prueba)")
            self.filas.append(dict(fila))
            self.orden.append(("sello", fila["accion"]))
            return 1

        def avisar(tipo, texto, nivel="🔴"):
            if tipo in self.suprimir:        # como el candado anti-spam: no sale y lo dice
                self.suprimidos.append((tipo, texto))
                return False
            self.avisos.append((tipo, texto, nivel))
            return True

        def dormir(s):
            self.dormidas.append(s)
            self.t[0] += s

        parches = [
            mock.patch.multiple(S.settings, **self.AJUSTES),
            mock.patch.object(S, "_ahora", side_effect=lambda: self.t[0]),
            mock.patch.object(S, "_dormir", side_effect=dormir),
            mock.patch.object(S, "_vivo_temu", side_effect=vivo("temu")),
            mock.patch.object(S, "_vivo_tiktok", side_effect=vivo("tiktok")),
            mock.patch.object(S, "marca", side_effect=self._marca),
            mock.patch.object(S, "marcas_vigentes", side_effect=self._marcas),
            mock.patch.object(S, "_cortado", side_effect=self._cortado),
            mock.patch.object(S, "usadas_hoy", side_effect=self._usadas),
            mock.patch.object(S, "es_padre", side_effect=lambda sku: self.padres.get(sku, False)),
            mock.patch.object(S, "cambio_ajeno", side_effect=self._ajeno),
            mock.patch.object(S, "apagado_en_bloque", side_effect=lambda c, t: self.bloque),
            mock.patch.object(S, "pendientes",
                              side_effect=lambda c, limite=20: [p for p in self.pend if p["canal"] == c]),
            mock.patch.object(S, "en_auditoria", side_effect=lambda c: list(self.auditoria)),
            mock.patch.object(S, "_ultimas", side_effect=self._ultimas),
            mock.patch.object(S, "edad_con_stock_s", side_effect=lambda sku: self.edad),
            mock.patch.object(S, "_reflejar", side_effect=lambda *a: self.reflejos.append(a)),
            mock.patch.object(S, "huerfanas", side_effect=lambda canal=None: list(self.huerfanas)),
            mock.patch.object(S, "_a_mysql", return_value=False),
            mock.patch.object(fanout_read, "registrar", side_effect=registrar),
            mock.patch.object(alertas, "avisar", side_effect=avisar),
            mock.patch.object(TM, "cambiar_venta", side_effect=cambiar_venta),
            mock.patch.object(TK, "desactivar", side_effect=desactivar),
            mock.patch.object(TK, "activar", side_effect=activar),
            mock.patch.object(TK, "access_token", return_value="t"),
            mock.patch.object(TK, "cipher", return_value="c"),
            mock.patch.object(F, "_en_hilo",
                              side_effect=lambda fabrica, etiqueta, timeout=60: asyncio.run(fabrica())),
            mock.patch.object(F, "_persistir", side_effect=self.eventos.append),
            mock.patch.object(F, "_asegurar_worker"),
            mock.patch.object(F, "_stock_drop", side_effect=lambda sku: self.woo.get(sku)),
            mock.patch.object(F, "encolar",
                              side_effect=lambda sku, motivo="venta": self.encolados.append((sku, motivo))),
            mock.patch.dict(F._ESCRITORES, {"temu": escritor("temu"), "tiktok": escritor("tiktok")}),
            mock.patch.dict(F._ESCRITORES_SOLO_BAJAR,
                            {"temu": escritor("temu"), "tiktok": escritor("tiktok")}),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        for d in (S._esperas, S._vistas, S._candados, S._ultimo, S._huerfanas_avisadas,
                  S._intentos_mem, S._pausas, S._presupuestos, S._reencoladas, S._avisos_pend,
                  S._avisos_dia, S._sueltos):
            d.clear()
        S._olvidar_marcas()

    # ── La bitácora falsa contesta como kubera: de lo que se selló ─────────────

    def _de_marca(self, canal, item):
        return [f for f in self.filas if f["canal"] == canal and f["item_id"] == str(item)
                and not f["dry_run"]]

    def _marca(self, canal, item):
        if self.bitacora_caida:
            raise RuntimeError("kubera no contesta (prueba)")
        m = [f for f in self._de_marca(canal, item)
             if f["accion"] in S._ACC_MARCA and str(f["resultado"]).startswith("ok")]
        return dict(m[-1]) if m and m[-1]["accion"] == S.ACC_INACTIVAR else None

    def _marcas(self, canal=None):
        if self.bitacora_caida:
            raise RuntimeError("kubera no contesta (prueba)")
        vistas = {(f["canal"], f["item_id"]) for f in self.filas}
        fuera = [self._marca(c, i) for c, i in sorted(vistas) if canal in (None, c)]
        return [m for m in fuera if m]

    def _cortado(self, canal, item):
        if self.bitacora_caida:
            raise RuntimeError("kubera no contesta (prueba)")
        d = [f for f in self._de_marca(canal, item)
             if f["accion"] == S.ACC_ERROR
             or (f["accion"] in S._ACC_MARCA and str(f["resultado"]).startswith("ok"))][-3:]
        return len(d) >= 3 and all(f["accion"] == S.ACC_ERROR for f in d)

    def _usadas(self, canal, acc, ensayos=False):
        """Como kubera: los reales son los INTENTOS (`cero_intento`), no los éxitos."""
        if self.tope_caido or self.bitacora_caida:
            raise RuntimeError("kubera no contesta (prueba)")
        if ensayos:
            n = sum(1 for f in self.filas if f["canal"] == canal and f["accion"] == acc and f["dry_run"])
        else:
            que = S._QUE_DE[acc] + " "
            n = sum(1 for f in self.filas if f["canal"] == canal and f["accion"] == S.ACC_INTENTO
                    and not f["dry_run"] and str(f["resultado"]).startswith(que))
        return n + self.usadas_extra.get((canal, acc), 0)

    def _ajeno(self, canal, sku, desde, propios=None):
        self.ajeno_args.append((canal, sku, list(propios or [])))
        return self.ajeno

    def _ultimas(self, canal=None):
        """Como `fanout_seguro._ultimas`: la última fila de marca de cada publicación
        (inactivar o soltar), con lo que dicen de ella el censo y la foto de Woo."""
        if self.bitacora_caida:
            raise RuntimeError("kubera no contesta (prueba)")
        salida = []
        for c, i in sorted({(f["canal"], f["item_id"]) for f in self.filas}):
            if canal not in (None, c):
                continue
            m = [f for f in self._de_marca(c, i)
                 if f["accion"] in S._ACC_MARCA and str(f["resultado"]).startswith("ok")]
            if not m or m[-1]["accion"] not in (S.ACC_INACTIVAR, S.ACC_SOLTAR):
                continue
            u = dict(m[-1])
            actual = self.listing.get((c, u["sku"]), i)
            status = self.censo.get((c, i), self.canal.get((c, i), {}).get("estado"))
            ts = u["ts"] if u["ts"].tzinfo else u["ts"].replace(tzinfo=timezone.utc)
            edad = (datetime.now(timezone.utc) - ts).total_seconds()
            u.update(status=status if actual == i else None, stock_own=0,
                     stock_woo=self.foto.get(u["sku"]), huerfana=actual != i,
                     listing_actual=actual, edad_s=edad, desde="2026-10-07 10:00:00",
                     fuera=not S.a_la_venta(c, status if actual == i else None))
            salida.append(u)
        return salida

    def intentos(self, que=None):
        """Las filas `cero_intento` que llegaron a la bitácora."""
        return [f for f in self.filas if f["accion"] == S.ACC_INTENTO
                and (que is None or str(f["resultado"]).startswith(que + " "))]

    # ── Atajos ────────────────────────────────────────────────────────────────

    def aplicar(self, p, motivo="cambio de stock en Woo"):
        with mock.patch.object(F, "plan", return_value=p):
            F._aplicar(p["sku"], motivo)

    def de(self, acc, ensayo=None):
        return [f for f in self.filas if f["accion"] == acc
                and (ensayo is None or bool(f["dry_run"]) == ensayo)]

    def ventas(self):
        """Las llamadas que sacan o regresan una publicación de la venta."""
        return [l for l in self.llamadas if l[0] != "stock" and l[0] != "registrar-falla"]

    def sembrar_marca(self, canal="temu", resultado="ok (2/8→3/2)", hace_h=2.0, item=None,
                      estado_vivo="3/2", sku=SKU):
        """Como si el seguro la hubiera apagado hace rato."""
        item = item or (G if canal == "temu" else P)
        self.filas.append({
            "ts": datetime.now(timezone.utc) - timedelta(hours=hace_h), "sku": sku,
            "motivo": "cambio de stock en Woo", "dry_run": False, "stock_drop": 0, "objetivo": 0,
            "canal": canal, "cuenta": CUENTA[canal], "item_id": item, "accion": S.ACC_INACTIVAR,
            "stock_canal": 4, "resultado": resultado, "ms": 10})
        self.canal[(canal, item)]["estado"] = estado_vivo
        S._olvidar_marcas()

    def ajustes(self, **k):
        p = mock.patch.multiple(S.settings, **k)
        p.start()
        self.addCleanup(p.stop)

    def en_ensayo(self, *canales):
        """Pone al seguro en ENSAYO en esos canales (en los dos si no se dice). Ya no
        hay ensayo «a pedido»: el único que existe es el forzado, cuando el fan-out no
        le escribe de verdad a ese canal. Aquí se suplanta la respuesta."""
        canales = canales or S.CANALES
        real = S.ensayo
        p = mock.patch.object(S, "ensayo", side_effect=lambda c: (
            (True, ["prueba: ensayo forzado"]) if (c or "").lower() in canales else real(c)))
        p.start()
        self.addCleanup(p.stop)


# ══════════════════════════════════════════════════════════════════════════════

INTERRUPTORES_QUE_YA_NO_EXISTEN = ("fanout_cero_enabled", "fanout_cero_ensayo", "fanout_cero_temu",
                                   "fanout_cero_tiktok", "fanout_cero_reactivar", "fanout_cero_solo_skus")


class SinInterruptor(Arnes):
    """Decisión de Brandon (9-oct-2026): «quitas la variable para inactivar o reactivar
    automáticamente; éste deberá de estar siempre encendido». No queda ninguna variable
    que lo apague, lo ponga en ensayo, le quite un canal, le quite la reactivación o lo
    limite a unos SKUs. Va pegado al fan-out."""

    def test_la_configuracion_ya_no_tiene_los_seis_interruptores(self):
        campos = type(S.settings).model_fields
        self.assertEqual([k for k in INTERRUPTORES_QUE_YA_NO_EXISTEN if k in campos], [])
        # Lo que queda son ajustes finos, cada uno con su valor de fábrica.
        esperado = {
            "fanout_cero_tope_vuelta": 0, "fanout_cero_tope_dia": 0,
            "fanout_cero_tope_reactivar_dia": 0, "fanout_cero_excluir": "",
            "fanout_cero_espera_min": 30, "fanout_cero_temu_estados": TEMU_ESTADOS_DE_FABRICA,
            "fanout_cero_temu_nivel_sku": False, "fanout_cero_espera_s": 8.0,
            "fanout_cero_relecturas": 3, "fanout_cero_bloque_n": 10}
        self.assertEqual({k: campos[k].default for k in esperado}, esperado)
        self.assertEqual(sorted(k for k in campos if k.startswith("fanout_cero_")), sorted(esperado))

    def test_ningun_modulo_lee_ya_esas_variables(self):
        patron = re.compile(r"\b(?:" + "|".join(INTERRUPTORES_QUE_YA_NO_EXISTEN) + r")\b")
        for ruta in ("config.py", "main.py", "services/fanout_seguro.py", "services/fanout_stock.py",
                     "services/fanout_vivo.py", "services/fanout_excedentes.py",
                     "services/pedidos_ml.py", "services/temu_censo.py", "services/tiktok_censo.py",
                     "routers/fanout.py"):
            fuente = (BACKEND / ruta).read_text(encoding="utf-8-sig")
            self.assertEqual(patron.findall(fuente), [], ruta)

    def test_una_variable_vieja_en_el_entorno_ya_no_lo_apaga(self):
        # Si alguna de las seis sigue puesta en Railway (o en un `.env`), no la lee nadie.
        import os
        import subprocess
        codigo = (
            "from config import settings\n"
            "from services import fanout_seguro as S, fanout_stock as F\n"
            "print(S.habilitado(), F.seguro_encendido(), S.ensayo('temu')[0], S.ensayo('tiktok')[0],\n"
            "      hasattr(settings, 'fanout_cero_enabled'))\n")
        entorno = {**os.environ, "FANOUT_ENABLED": "true", "FANOUT_DRY_RUN": "false",
                   "FANOUT_CANALES": "", "FANOUT_TEMU": "true", "FANOUT_TIKTOK": "true",
                   "SUPABASE_WRITE_FANOUT_LOG": "true",
                   "FANOUT_CERO_ENABLED": "false", "FANOUT_CERO_ENSAYO": "true",
                   "FANOUT_CERO_TEMU": "false", "FANOUT_CERO_TIKTOK": "false",
                   "FANOUT_CERO_REACTIVAR": "false", "FANOUT_CERO_SOLO_SKUS": "UNO-0001-AZL"}
        r = subprocess.run([sys.executable, "-c", codigo], cwd=str(BACKEND), env=entorno,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip().splitlines()[-1:], ["True True False False False"], r.stderr[-800:])

    def test_esta_encendido_siempre_que_lo_este_el_fanout(self):
        self.assertTrue(S.habilitado())
        self.assertTrue(F.seguro_encendido())
        self.assertEqual((S.ensayo("temu"), S.ensayo("tiktok")), ((False, []), (False, [])))
        self.ajustes(fanout_enabled=False)
        self.assertFalse(S.habilitado())
        self.assertFalse(F.seguro_encendido())

    def test_se_le_aplica_a_cualquier_sku_y_a_los_dos_canales(self):
        for i, sku in enumerate(("ZZZ-0301-AZL", "ZZZ-0302-AZL", "ZZZ-0303-AZL")):
            g, p = f"93{i:04d}", f"78{i:04d}"
            self.canal[("temu", g)] = {"estado": "2/8", "stock": 4,
                                       "skus": [{"id": 8000 + i, "seller_sku": sku, "cantidad": 4}]}
            self.canal[("tiktok", p)] = {"estado": "ACTIVATE", "stock": None,
                                         "skus": [{"id": f"T{i}", "seller_sku": sku, "cantidad": 3}]}
            self.aplicar(plan(accion("temu", 4, item=g), accion("tiktok", 3, item=p), sku=sku))
        self.assertEqual(len([v for v in self.ventas() if v[0] == "temu.venta"]), 3)
        self.assertEqual(len([v for v in self.ventas() if v[0] == "tiktok.desactivar"]), 3)

    def test_reactiva_siempre_no_hay_bandera(self):
        self.sembrar_marca()
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertEqual(self.ventas(), [("temu.venta", G, True, None)])
        self.assertEqual(self.de(S.ACC_REACTIVAR)[0]["resultado"], "ok (3/2→2/8)")

    def test_ya_no_hay_via_manual(self):
        self.assertFalse(hasattr(S, "aplicar"))
        rutas = (BACKEND / "routers" / "fanout.py").read_text(encoding="utf-8-sig")
        self.assertNotIn("/seguro/aplicar", rutas)
        self.assertNotIn("manual", S.Contexto.__slots__)

    def test_el_freno_es_el_del_fanout_de_cada_canal(self):
        # Apagar el fan-out de UN canal deja al seguro en ensayo en ESE canal: no apaga.
        self.ajustes(fanout_tiktok=False)
        self.aplicar(plan(accion("temu", 4), accion("tiktok", 3, accion="omitir",
                                                    omitido="FANOUT_TIKTOK apagado")))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])
        self.assertEqual(S.ensayo("tiktok"), (True, ["FANOUT_TIKTOK apagado"]))


class ApagadoNoHaceNada(Arnes):
    def test_con_el_fanout_apagado_ni_lee_ni_anota(self):
        self.ajustes(fanout_enabled=False)
        self.aplicar(plan(accion("temu", 4), accion("tiktok", 3)))
        self.assertEqual(self.llamadas, [("stock", "temu", G, 0), ("stock", "tiktok", P, 0)])
        self.assertEqual((self.lecturas, self.filas, self.avisos), ([], [], []))
        self.assertEqual(len(self.eventos), 1)
        self.assertIsNone(S.abrir(SKU, "x", plan()))

    def test_otros_canales_no_son_asunto_del_seguro(self):
        a = dict(accion("temu", 4), canal="mercado_libre")
        with mock.patch.dict(F._ESCRITORES, {"mercado_libre": lambda c, i, n: (True, "ok")}):
            self.aplicar(plan(a))
        self.assertEqual((self.lecturas, self.filas), ([], []))


class Inactivar(Arnes):
    def test_primero_se_apaga_y_despues_se_escribe_el_cero(self):
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.llamadas, [("temu.venta", G, False, None), ("stock", "temu", G, 0)])
        (f,) = self.de(S.ACC_INACTIVAR)
        self.assertEqual((f["resultado"], f["dry_run"], f["stock_canal"], f["item_id"], f["cuenta"]),
                         ("ok (2/8→3/2)", False, 4, G, "TEMU"))
        self.assertEqual(self.dormidas, [S._ESPERA_S], "espera y relee tras apagar")
        self.assertEqual(self.reflejos, [("temu", SKU, G, "3/2")])
        self.assertEqual([a[0] for a in self.avisos], ["seguro_cero_apago:temu"])

    def test_sus_filas_llevan_el_ts_del_evento_del_fanout(self):
        self.aplicar(plan(accion("temu", 4)))
        (f,) = self.de(S.ACC_INACTIVAR)
        self.assertEqual(f["ts"], self.eventos[0]["ts_dt"])
        self.assertEqual((f["sku"], f["motivo"], f["objetivo"]), (SKU, "cambio de stock en Woo", 0))

    def test_sin_cambio_con_objetivo_cero_tambien_apaga(self):
        # El caso de ACC-0574-LIL: el plan decía «el canal ya tiene 0» y Temu vendió.
        self.canal[("temu", G)]["stock"] = 0
        self.aplicar(plan(accion("temu", 0, accion="sin_cambio", omitido="el canal ya tiene 0")))
        self.assertEqual(self.llamadas, [("temu.venta", G, False, None)])
        self.assertEqual(len(self.de(S.ACC_INACTIVAR)), 1)

    def test_tambien_cuando_el_plan_omite_la_escritura_de_stock(self):
        # Stock del canal desconocido: el fan-out no escribe, pero con objetivo 0 se apaga.
        self.aplicar(plan(accion("temu", None, accion="omitir",
                                 omitido="stock del canal DESCONOCIDO (no se escribe a ciegas)")))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])

    def test_tiktok_se_apaga_igual(self):
        self.aplicar(plan(accion("tiktok", 3)))
        self.assertEqual(self.llamadas, [("tiktok.desactivar", P), ("stock", "tiktok", P, 0)])
        self.assertEqual(self.de(S.ACC_INACTIVAR)[0]["resultado"], "ok (ACTIVATE→SELLER_DEACTIVATED)")
        self.assertEqual(self.de(S.ACC_INACTIVAR)[0]["stock_canal"], 3)

    def test_woo_ilegible_no_es_cero(self):
        sin_stock = {"sku": SKU, "ok": False, "motivo": "sin stock legible en WooCommerce", "acciones": []}
        self.aplicar(sin_stock)
        ctx = S.abrir(SKU, "x", sin_stock)
        S.antes(ctx, dict(accion("temu", 4), objetivo=None))
        S.cerrar(ctx)
        self.assertEqual((self.llamadas, self.lecturas, self.filas), ([], [], []))

    def test_con_objetivo_mayor_que_cero_no_se_apaga(self):
        self.aplicar(plan(accion("temu", 4), objetivo=2))
        self.assertEqual(self.llamadas, [("stock", "temu", G, 2)])
        self.assertEqual(self.filas, [])

    def test_el_cero_se_escribe_aunque_apagar_falle(self):
        self.canal_falla = True
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.llamadas, [("temu.venta", G, False, None), ("stock", "temu", G, 0)])
        (e,) = self.de(S.ACC_ERROR)
        self.assertTrue(e["resultado"].startswith(S.ERR_RECHAZO + ": RuntimeError"))
        self.assertEqual(self.de(S.ACC_INACTIVAR), [], "sin éxito no hay marca")
        self.assertEqual(len(self.intentos("apagar")), 1, "pero el intento sí quedó sellado")
        self.assertIsNone(self._marca("temu", G))

    def test_una_excepcion_del_seguro_no_impide_escribir_ni_persistir(self):
        for paso in ("abrir", "antes", "despues", "cerrar"):
            with self.subTest(paso=paso):
                self.llamadas.clear()
                self.eventos.clear()
                self.canal[("temu", G)]["estado"] = "2/8"
                with mock.patch.object(S, paso, side_effect=RuntimeError("el seguro se rompió")):
                    self.aplicar(plan(accion("temu", 4)))
                self.assertIn(("stock", "temu", G, 0), self.llamadas)
                self.assertEqual(len(self.eventos), 1)

    def test_lectura_en_vivo_ilegible_no_actua(self):
        self.ilegibles.add(("temu", G))
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.llamadas, [("stock", "temu", G, 0)])
        self.assertIn("lectura en vivo ilegible", self.de(S.ACC_ERROR)[0]["resultado"])

    def test_si_no_converge_es_error_y_no_hay_marca(self):
        self.canal_obedece = False          # contesta «ok» pero sigue a la venta
        self.aplicar(plan(accion("temu", 4)))
        self.assertIn("no convergió", self.de(S.ACC_ERROR)[0]["resultado"])
        self.assertEqual((self.de(S.ACC_INACTIVAR), self.reflejos), ([], []))
        self.assertIn(("stock", "temu", G, 0), self.llamadas)
        self.assertEqual(self.dormidas, [S._ESPERA_S] * S._RELECTURAS, "insistió antes de rendirse")
        self.assertEqual(len(self.ventas()), 1, "releer no es volver a llamar")
        # El canal dijo «ok»: si en realidad sí la apagó, quedó sin marca. Se avisa.
        self.assertEqual([a[0] for a in self.avisos], [f"seguro_cero_duda:temu:{SKU}"])

    def test_una_lectura_rancia_no_es_un_fracaso(self):
        # La lectura de Temu va segundos atrás: la primera relectura todavía dice 2/8.
        self.rancias[("temu", G)] = ["2/8"]
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.de(S.ACC_INACTIVAR)[0]["resultado"], "ok (2/8→3/2)")
        self.assertEqual(self.dormidas, [S._ESPERA_S] * 2)
        self.assertEqual(self.de(S.ACC_ERROR), [])

    def test_se_decide_con_el_estado_vivo_no_con_el_censo(self):
        # El censo cree que está a la venta; en vivo alguien ya la bajó: no se toca.
        self.canal[("temu", G)]["estado"] = "3/2"
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [])
        self.assertEqual(self.filas, [], "sin marca propia no hay nada que anotar")

    def test_lo_que_el_censo_da_por_fuera_no_cuesta_ni_una_lectura(self):
        for estado in ("3/2", "4/7", "4/10"):
            self.aplicar(plan(accion("temu", 4, estado=estado)))
        self.aplicar(plan(accion("tiktok", 3, estado="SELLER_DEACTIVATED")))
        self.aplicar(plan(accion("tiktok", 3, estado="DRAFT")))
        self.assertEqual((self.lecturas, self.filas, self.ventas()), ([], [], []))

    def test_sin_estado_en_el_censo_se_lee_en_vivo(self):
        self.aplicar(plan(accion("temu", 4, estado="")))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])

    def test_temu_3_1_espera_su_sondeo(self):
        self.canal[("temu", G)]["estado"] = "3/1"
        self.aplicar(plan(accion("temu", 0, accion="sin_cambio", estado="3/1")))
        self.assertEqual((self.ventas(), self.lecturas), ([], []))
        self.assertIn("3/1", self.de(S.ACC_OMITIR)[0]["resultado"])
        # Con el sondeo hecho, Brandon agrega 3/1 a la variable y ya se apaga.
        self.filas.clear()
        S._vistas.clear()
        self.ajustes(fanout_cero_temu_estados="2/8, 3/1")
        self.aplicar(plan(accion("temu", 0, accion="sin_cambio", estado="3/1")))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])
        self.assertEqual(self.de(S.ACC_INACTIVAR)[0]["resultado"], "ok (3/1→3/2)")

    def test_full_y_lo_que_el_fanout_descarta_no_se_tocan(self):
        full = accion("temu", 4, accion="omitir", fuera=True,
                      omitido="FULL/FBA (bodega del marketplace, no se toca)")
        borrada = accion("tiktok", 3, accion="omitir", fuera=True, estado="DELETED",
                         omitido="status=DELETED — el producto ya no existe en TikTok")
        self.aplicar(plan(full, borrada))
        self.assertEqual((self.llamadas, self.lecturas, self.filas), ([], [], []))

    def test_sin_publicacion_no_hay_fila(self):
        self.aplicar(plan(dict(accion("temu", 4, accion="omitir"), item_id=None)))
        self.assertEqual((self.lecturas, self.filas), ([], []))

    def test_un_sku_padre_no_se_toca(self):
        for veredicto, texto in ((True, "nunca se opera sobre el padre"), (None, "no pude determinar")):
            with self.subTest(padre=veredicto):
                self.filas.clear()
                self.llamadas.clear()
                self.lecturas.clear()
                S._vistas.clear()
                self.padres[SKU] = veredicto
                self.aplicar(plan(accion("temu", 4)))
                self.assertEqual((self.ventas(), self.lecturas), ([], []))
                self.assertIn(texto, self.de(S.ACC_OMITIR)[0]["resultado"])
                self.assertIn(("stock", "temu", G, 0), self.llamadas, "el 0 sí se escribe")

    def test_un_sku_excluido_no_se_toca(self):
        self.ajustes(fanout_cero_excluir=f"OTRO-0001, {SKU.lower()}")
        self.aplicar(plan(accion("temu", 4), accion("tiktok", 3)))
        self.assertEqual((self.ventas(), self.lecturas), ([], []))
        self.assertEqual({f["resultado"] for f in self.de(S.ACC_OMITIR)}, {"excluido (FANOUT_CERO_EXCLUIR)"})

    def test_si_ya_la_apago_el_seguro_no_repite(self):
        self.aplicar(plan(accion("temu", 4)))
        self.llamadas.clear()
        # El censo todavía la da por «a la venta» (pasa cada hora); en vivo ya está fuera.
        self.aplicar(plan(accion("temu", 0, accion="sin_cambio")))
        self.assertEqual(self.ventas(), [])
        self.assertIn("marca vigente", self.de(S.ACC_SIN_CAMBIO)[0]["resultado"])

    def test_si_vuelve_sola_a_la_venta_con_woo_en_cero_se_apaga_otra_vez(self):
        self.aplicar(plan(accion("temu", 4)))
        self.canal[("temu", G)].update(estado="2/8", stock=14)      # Temu la regresó sola
        self.aplicar(plan(accion("temu", 14)))
        self.assertEqual(len(self.de(S.ACC_INACTIVAR)), 2)
        self.assertEqual(self.llamadas[-2:], [("temu.venta", G, False, None), ("stock", "temu", G, 0)])


class Variantes(Arnes):
    def test_la_regla_de_nivel_es_pura(self):
        una = [{"id": 1, "seller_sku": SKU, "cantidad": 0}]
        dos = una + [{"id": 2, "seller_sku": HERMANA, "cantidad": 5}]
        d = S.decidir_nivel
        self.assertEqual(d("temu", SKU, una)["nivel"], "publicacion")
        self.assertEqual(d("tiktok", SKU, una)["nivel"], "publicacion")
        self.assertIsNone(d("temu", SKU, dos)["nivel"])
        self.assertIn("2 variantes", d("temu", SKU, dos)["motivo"])
        v = d("temu", SKU, dos, nivel_sku_temu=True)
        self.assertEqual((v["nivel"], v["sku_ids"]), ("variante", [1]))
        self.assertIsNone(d("temu", SKU, [{"id": 1, "seller_sku": ""}, {"id": 2, "seller_sku": ""}],
                            nivel_sku_temu=True)["nivel"], "sin saber cuál es la nuestra, ninguna")
        self.assertEqual(d("tiktok", SKU, dos, {HERMANA: 0})["nivel"], "publicacion")
        self.assertIsNone(d("tiktok", SKU, dos, {HERMANA: 5})["nivel"])
        self.assertIsNone(d("tiktok", SKU, dos, {HERMANA: None})["nivel"])
        self.assertIsNone(d("tiktok", SKU, dos, {})["nivel"], "una hermana sin objetivo = ilegible")
        self.assertIsNone(d("tiktok", SKU, [])["nivel"])
        self.assertIsNone(d("tiktok", SKU, [{"id": 9, "seller_sku": "OTRO-0009"}])["nivel"],
                          "la publicación hoy es de otro SKU")

    def _dos_variantes_tiktok(self):
        self.canal[("tiktok", P)]["skus"].append({"id": "S2", "seller_sku": HERMANA, "cantidad": 5})

    def test_tiktok_con_una_hermana_con_stock_no_se_apaga(self):
        self._dos_variantes_tiktok()
        self.woo[HERMANA] = 5
        self.aplicar(plan(accion("tiktok", 3)))
        self.assertEqual(self.ventas(), [])
        self.assertIn(f"la hermana {HERMANA} tiene 5", self.de(S.ACC_OMITIR)[0]["resultado"])
        self.assertIn(("stock", "tiktok", P, 0), self.llamadas)

    def test_tiktok_con_una_hermana_ilegible_no_se_apaga(self):
        self._dos_variantes_tiktok()       # Woo no contesta por la hermana
        self.aplicar(plan(accion("tiktok", 3)))
        self.assertEqual(self.ventas(), [])
        self.assertIn("no tiene stock legible", self.de(S.ACC_OMITIR)[0]["resultado"])

    def test_tiktok_con_todas_las_hermanas_en_cero_si_se_apaga(self):
        self._dos_variantes_tiktok()
        self.woo[HERMANA] = 0
        self.aplicar(plan(accion("tiktok", 3)))
        self.assertEqual(self.ventas(), [("tiktok.desactivar", P)])

    def test_temu_con_varias_variantes_no_se_apaga_sin_su_bandera(self):
        self.canal[("temu", G)]["skus"].append({"id": 5002, "seller_sku": "", "cantidad": None})
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [])
        self.assertIn("2 variantes", self.de(S.ACC_OMITIR)[0]["resultado"])

    def test_temu_por_variante_manda_solo_esa(self):
        self.ajustes(fanout_cero_temu_nivel_sku=True)
        self.canal[("temu", G)]["skus"].append({"id": 5002, "seller_sku": HERMANA, "cantidad": 5})
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, [5001])])
        self.assertEqual(S.leer_marca(self.de(S.ACC_INACTIVAR)[0]["resultado"])["variante"], 5001)
        # El goods sigue «a la venta» (sólo se apagó una variante): no se repite la llamada.
        self.llamadas.clear()
        self.aplicar(plan(accion("temu", 0, accion="sin_cambio")))
        self.assertEqual(self.ventas(), [])
        self.assertIn("variante 5001", self.de(S.ACC_SIN_CAMBIO)[0]["resultado"])
        self.assertEqual(len(self.de(S.ACC_INACTIVAR)), 1)

    def test_si_la_publicacion_es_de_otro_sku_no_se_toca(self):
        self.canal[("temu", G)]["skus"][0]["seller_sku"] = "OTRO-0009-NEG"
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [])
        self.assertIn("otro SKU", self.de(S.ACC_OMITIR)[0]["resultado"])


class Ensayo(Arnes):
    def test_en_ensayo_lee_y_anota_pero_no_apaga(self):
        self.en_ensayo()
        self.aplicar(plan(accion("temu", 4), accion("tiktok", 3)))
        self.assertEqual(self.ventas(), [])
        self.assertEqual(sorted(self.lecturas), [("temu", G), ("tiktok", P)])
        filas = self.de(S.ACC_INACTIVAR)
        self.assertEqual([f["dry_run"] for f in filas], [True, True])
        self.assertEqual(filas[0]["resultado"], "ENSAYO (apagaría; vivo 2/8, ofrece 4)")
        self.assertEqual((self.reflejos, self.avisos), ([], []))
        self.assertIsNone(self._marca("temu", G), "una fila de ensayo no es marca")
        self.assertEqual([l for l in self.llamadas if l[0] == "stock"],
                         [("stock", "temu", G, 0), ("stock", "tiktok", P, 0)])

    def test_ensayo_forzado_si_el_fanout_no_escribe_de_verdad(self):
        casos = {"fanout_dry_run": True, "fanout_temu": False,
                 "fanout_canales": "tiktok,mercado_libre", "supabase_write_fanout_log": False}
        for bandera, valor in casos.items():
            with self.subTest(bandera=bandera):
                self.filas.clear()
                self.llamadas.clear()
                S._vistas.clear()
                self.canal[("temu", G)]["estado"] = "2/8"
                en_ensayo, por_que = None, None

                def a_mysql(fila):     # sin bitácora en kubera, la fila sólo llega aquí
                    if not S.settings.supabase_write_fanout_log:
                        self.filas.append(dict(fila))
                    return True

                with mock.patch.object(S.settings, bandera, valor), \
                        mock.patch.object(S, "_a_mysql", side_effect=a_mysql):
                    en_ensayo, por_que = S.ensayo("temu")
                    ctx = S.abrir(SKU, "x", plan(accion("temu", 4)))
                    S.antes(ctx, accion("temu", 4))
                    S.cerrar(ctx)
                self.assertTrue(en_ensayo and por_que, "ensayo forzado, y dice por qué")
                self.assertEqual(self.ventas(), [], f"{bandera}: no debía apagar")
                self.assertEqual([f["dry_run"] for f in self.de(S.ACC_INACTIVAR)], [True])

    def test_con_el_fanout_apagado_no_hay_ni_ensayo(self):
        # Antes era un ensayo forzado más; ahora el seguro va pegado al fan-out y, con
        # él apagado, no abre contexto: ni lee ni anota.
        self.ajustes(fanout_enabled=False)
        self.assertEqual(S.ensayo("temu"), (True, ["FANOUT_ENABLED apagado"]))
        ctx = S.abrir(SKU, "x", plan(accion("temu", 4)))
        self.assertIsNone(ctx)
        S.antes(ctx, accion("temu", 4))
        S.cerrar(ctx)
        self.assertEqual((self.lecturas, self.filas, self.ventas()), ([], [], []))

    def test_en_ensayo_ninguna_fila_parece_real(self):
        # No sólo la de «apagaría»: las omisiones y los errores también van con dry_run.
        self.en_ensayo()
        self.padres[SKU] = True
        self.aplicar(plan(accion("temu", 4)))                # se omite: SKU padre
        self.padres[SKU] = False
        self.ilegibles.add(("tiktok", P))
        self.aplicar(plan(accion("tiktok", 3)))              # error: lectura ilegible
        self.assertEqual(sorted(f["accion"] for f in self.filas), [S.ACC_ERROR, S.ACC_OMITIR])
        self.assertTrue(all(f["dry_run"] for f in self.filas))

    def test_un_canal_en_ensayo_no_arrastra_al_otro(self):
        self.ajustes(fanout_tiktok=False)                    # el fan-out no le escribe a TikTok
        self.aplicar(plan(accion("temu", 4), accion("tiktok", 3, accion="omitir",
                                                    omitido="FANOUT_TIKTOK apagado")))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])
        por_canal = {f["canal"]: f for f in self.de(S.ACC_INACTIVAR)}
        self.assertEqual((por_canal["temu"]["dry_run"], por_canal["tiktok"]["dry_run"]), (False, True))
        self.assertTrue(por_canal["tiktok"]["resultado"].startswith("ENSAYO"))

    def test_con_todo_encendido_no_esta_en_ensayo(self):
        self.assertEqual(S.ensayo("temu"), (False, []))
        self.assertEqual(S.ensayo("tiktok"), (False, []))

    def test_el_ensayo_no_repite_la_misma_fila_en_24_h(self):
        self.en_ensayo()
        self.aplicar(plan(accion("temu", 4)))
        self.lecturas.clear()
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual((len(self.de(S.ACC_INACTIVAR)), self.lecturas), (1, []))
        self.t[0] += 25 * 3600
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(len(self.de(S.ACC_INACTIVAR)), 2)

    def test_cero_omitir_tampoco_se_repite(self):
        self.padres[SKU] = True
        for _ in range(3):
            self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(len(self.de(S.ACC_OMITIR)), 1)


class Topes(Arnes):
    def _siete(self):
        skus = [f"ZZZ-01{i:02d}-NEG" for i in range(7)]
        planes = {}
        for i, s in enumerate(skus):
            item = f"91{i:04d}"
            self.canal[("temu", item)] = {"estado": "2/8", "stock": 2,
                                           "skus": [{"id": 6000 + i, "seller_sku": s, "cantidad": 2}]}
            planes[s] = plan(accion("temu", 2, item=item), sku=s)
        filas = [{"sku": s, "listing_id": f"91{i:04d}", "status": "2/8", "stock_own": 2}
                 for i, s in enumerate(skus)]
        return filas, planes

    def _barrer(self, filas, planes):
        with mock.patch.object(S, "candidatos", return_value=filas), \
                mock.patch.object(F, "plan", side_effect=lambda sku: planes[sku]):
            return S.barrer("temu")

    def test_siete_candidatos_con_tope_cinco_son_cinco_llamadas(self):
        filas, planes = self._siete()
        r = self._barrer(filas, planes)
        self.assertEqual(len(self.ventas()), 5)
        self.assertEqual(len(self.de(S.ACC_INACTIVAR)), 5)
        omitidas = self.de(S.ACC_OMITIR)
        self.assertEqual([f["resultado"] for f in omitidas], ["tope de la vuelta (5)"] * 2)
        self.assertEqual((r["llamadas"], r["candidatos"]), (5, 7))
        self.assertEqual(len(self.lecturas), 10, "agotado el tope, ya ni se lee el canal")
        self.assertEqual(len([a for a in self.avisos if a[0] == "seguro_cero_apago:temu"]), 1,
                         "una campana por vuelta, no una por publicación")
        # La vuelta siguiente sigue con las que faltaron.
        self.llamadas.clear()
        self._barrer([f for f in filas if self.canal[("temu", f["listing_id"])]["estado"] == "2/8"], planes)
        self.assertEqual(len(self.ventas()), 2)

    def test_las_llamadas_fallidas_tambien_gastan_el_tope_de_la_vuelta(self):
        filas, planes = self._siete()
        self.canal_falla = True
        self._barrer(filas, planes)
        self.assertEqual(len(self.ventas()), 5)

    def test_tope_del_dia_consumido_cero_llamadas(self):
        self.usadas_extra[("temu", S.ACC_INACTIVAR)] = 20
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [])
        self.assertEqual(self.de(S.ACC_OMITIR)[0]["resultado"], "tope del día (20)")
        self.assertIn(("stock", "temu", G, 0), self.llamadas)

    def test_sin_poder_leer_el_tope_no_se_actua(self):
        self.tope_caido = True
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [])
        self.assertIn("no pude leer el tope del día", self.de(S.ACC_ERROR)[0]["resultado"])

    def test_en_ensayo_los_topes_no_cortan_pero_se_anotan(self):
        self.en_ensayo()
        filas, planes = self._siete()
        self._barrer(filas, planes)
        res = [f["resultado"] for f in self.de(S.ACC_INACTIVAR, ensayo=True)]
        self.assertEqual(len(res), 7)
        self.assertEqual([("fuera de tope" in r) for r in res], [False] * 5 + [True] * 2)
        self.assertEqual(self.ventas(), [])


class Reactivar(Arnes):
    def _apagada_por_el_seguro(self, canal="temu"):
        self.aplicar(plan(accion(canal, 4 if canal == "temu" else 3)))
        self.llamadas.clear()
        self.lecturas.clear()

    def _vuelve_stock(self, canal="temu", objetivo=5, acc="escribir", actual=0, estado=None):
        fuera = "3/2" if canal == "temu" else "SELLER_DEACTIVATED"
        self.aplicar(plan(accion(canal, actual, accion=acc, estado=estado or fuera), objetivo=objetivo))

    def test_primero_el_stock_y_despues_la_venta(self):
        self._apagada_por_el_seguro()
        self._vuelve_stock()
        self.assertEqual(self.llamadas, [("stock", "temu", G, 5), ("temu.venta", G, True, None)])
        (f,) = self.de(S.ACC_REACTIVAR)
        self.assertEqual((f["resultado"], f["dry_run"]), ("ok (3/2→2/8)", False))
        self.assertIsNone(self._marca("temu", G), "la marca queda cerrada")
        self.assertEqual(self.reflejos[-1], ("temu", SKU, G, "2/8"))

    def test_lo_que_apago_una_persona_no_se_reactiva(self):
        # Como las 302 de Temu en 3/2 y las 273 de TikTok apagadas a mano el 21-ago.
        self.canal[("temu", G)]["estado"] = "3/2"
        self.canal[("tiktok", P)]["estado"] = "SELLER_DEACTIVATED"
        self.aplicar(plan(accion("temu", 0, estado="3/2"),
                          accion("tiktok", 0, estado="SELLER_DEACTIVATED"), objetivo=5))
        self.assertEqual(self.ventas(), [])
        self.assertEqual(self.filas, [], "sin marca: ni llamada, ni lectura, ni fila")
        self.assertEqual(self.lecturas, [])

    def test_con_marca_pero_alguien_mas_movio_el_estado_se_suelta(self):
        self._apagada_por_el_seguro()
        self.ajeno = {"valor_anterior": "3/2", "valor_nuevo": "2/8", "via": "temu_censo"}
        self._vuelve_stock()
        self.assertEqual(self.ventas(), [])
        self.assertIn("estado movido por temu_censo", self.de(S.ACC_SOLTAR)[0]["resultado"])
        self.assertIsNone(self._marca("temu", G), "soltada: ya no es suya")
        self.assertIn(f"seguro_cero_solto:temu:{SKU}", [a[0] for a in self.avisos])

    def test_si_ya_esta_a_la_venta_la_prendio_otro(self):
        self._apagada_por_el_seguro()
        self.canal[("temu", G)]["estado"] = "2/8"
        self._vuelve_stock(estado="2/8")
        self.assertEqual(self.ventas(), [])
        self.assertEqual(self.de(S.ACC_SOLTAR)[0]["resultado"], "ok (la prendió otro)")

    def test_en_otro_estado_del_que_dejo_el_seguro_se_suelta(self):
        self._apagada_por_el_seguro()
        self.canal[("temu", G)]["estado"] = "4/7"      # incompleta: eso no lo dejó el seguro
        self._vuelve_stock()
        self.assertEqual(self.ventas(), [])
        self.assertIn("estado 4/7 ajeno", self.de(S.ACC_SOLTAR)[0]["resultado"])
        self.assertIn(f"seguro_cero_solto:temu:{SKU}", [a[0] for a in self.avisos])

    def test_si_el_escritor_falla_no_se_reactiva(self):
        self._apagada_por_el_seguro()
        self.escritor_ok = (False, "Temu no aplicó")
        self._vuelve_stock()
        self.assertEqual(self.ventas(), [])
        self.assertIn("esperando stock", self.de(S.ACC_SIN_CAMBIO)[0]["resultado"])
        self.assertIsNotNone(self._marca("temu", G), "la marca sigue: se reintenta después")

    def test_sin_escritura_solo_reactiva_si_el_canal_ya_tiene_el_objetivo(self):
        self._apagada_por_el_seguro()
        self.canal[("temu", G)]["stock"] = 3
        self._vuelve_stock(acc="sin_cambio", actual=5)       # el censo dice 5; en vivo hay 3
        self.assertEqual(self.ventas(), [])
        self.canal[("temu", G)]["stock"] = 5
        self._vuelve_stock(acc="sin_cambio", actual=5)
        self.assertEqual(self.ventas(), [("temu.venta", G, True, None)])

    def test_la_espera_no_reactiva_a_los_10_min_y_si_a_los_30(self):
        self._apagada_por_el_seguro()
        self.edad = 600.0                    # el stock volvió hace 10 min
        self._vuelve_stock()
        self.assertEqual(self.ventas(), [])
        self.assertIn("espera 20 min", self.de(S.ACC_SIN_CAMBIO)[0]["resultado"])
        self.assertEqual(S.esperas_vencidas(), [], "todavía no vence")
        self.t[0] += 20 * 60 + S._MARGEN_ESPERA_S + 1
        self.assertEqual(S.esperas_vencidas(), [SKU], "al vencer, el worker la reencola")
        self.assertEqual(S.esperas_vencidas(), [], "una sola vez")
        self.edad = 1800.0
        self.llamadas.clear()
        self._vuelve_stock(acc="sin_cambio", actual=5)
        self.assertEqual(self.ventas(), [("temu.venta", G, True, None)])

    def test_el_primer_evento_con_stock_arranca_la_espera_completa(self):
        self._apagada_por_el_seguro()
        self.edad = None                     # el evento en curso aún no está en la bitácora
        self._vuelve_stock()
        self.assertEqual(self.ventas(), [])
        self.assertIn("espera 30 min", self.de(S.ACC_SIN_CAMBIO)[0]["resultado"])
        self.assertIn(SKU, S._esperas)

    def test_el_worker_devuelve_a_la_cola_las_esperas_vencidas(self):
        S._esperas[SKU] = self.t[0] - 1
        with mock.patch.object(F, "_pendientes", {}), mock.patch.object(F.time, "sleep", side_effect=[None, SystemExit]):
            with self.assertRaises(SystemExit):
                F._worker()
        self.assertEqual(self.encolados, [(SKU, S.MOTIVO_ESPERA)])

    def test_tope_de_reactivaciones_del_dia(self):
        self._apagada_por_el_seguro()
        self.usadas_extra[("temu", S.ACC_REACTIVAR)] = 20
        self._vuelve_stock()
        self.assertEqual(self.ventas(), [])
        self.assertEqual(self.de(S.ACC_OMITIR)[0]["resultado"], "tope de reactivaciones del día (20)")

    def test_tiktok_en_pending_cuenta_como_exito_y_cierra_la_marca(self):
        self._apagada_por_el_seguro("tiktok")
        self._vuelve_stock("tiktok")
        self.assertEqual(self.llamadas, [("stock", "tiktok", P, 5), ("tiktok.activar", P)])
        self.assertEqual(self.de(S.ACC_REACTIVAR)[0]["resultado"], "ok (SELLER_DEACTIVATED→PENDING)")
        self.assertIsNone(self._marca("tiktok", P))
        # Ya sin marca, el siguiente evento no vuelve a llamar.
        self.llamadas.clear()
        self._vuelve_stock("tiktok", acc="sin_cambio", actual=5, estado="PENDING")
        self.assertEqual(self.ventas(), [])

    def test_si_reactivar_no_converge_es_error_y_la_marca_sigue(self):
        self._apagada_por_el_seguro()
        self.avisos.clear()
        self.canal_obedece = False
        self._vuelve_stock()
        self.assertIn("no convergió", self.de(S.ACC_ERROR)[0]["resultado"])
        self.assertIsNotNone(self._marca("temu", G))
        self.assertEqual([a[0] for a in self.avisos], [f"seguro_cero_duda:temu:{SKU}"])

    def test_con_la_bitacora_muda_no_se_reactiva(self):
        self._apagada_por_el_seguro()
        self.bitacora_caida = True
        self._vuelve_stock()
        self.assertEqual(self.ventas(), [], "«no sé» = no se reactiva")
        self.assertIn(("stock", "temu", G, 5), self.llamadas, "el stock sí se escribe")

    def test_una_marca_ilegible_no_reactiva(self):
        self.sembrar_marca(resultado="ok")
        self._vuelve_stock()
        self.assertEqual(self.ventas(), [])
        self.assertIn("marca ilegible", self.de(S.ACC_OMITIR)[0]["resultado"])

    def test_en_ensayo_no_prende_ni_devuelve_nada_a_la_cola(self):
        self._apagada_por_el_seguro()
        self.en_ensayo()
        self._vuelve_stock()
        self.assertEqual(self.ventas(), [])
        (f,) = self.de(S.ACC_REACTIVAR)
        self.assertTrue(f["dry_run"] and f["resultado"].startswith("ENSAYO (prendería"))
        self.assertIsNotNone(self._marca("temu", G))
        self.edad = 60.0
        S._vistas.clear()
        self._vuelve_stock()
        self.assertEqual(S._esperas, {}, "el ensayo sólo mira")

    def test_leer_marca(self):
        self.assertEqual(S.leer_marca("ok (2/8→3/2)"), {"de": "2/8", "a": "3/2", "variante": None})
        self.assertEqual(S.leer_marca("ok (ACTIVATE→SELLER_DEACTIVATED)")["a"], "SELLER_DEACTIVATED")
        self.assertEqual(S.leer_marca("ok (2/8→2/8 · variante 5001)")["variante"], 5001)
        for mala in ("ok", "ok (manual)", "ENSAYO (apagaría; vivo 2/8, ofrece 2)", "", None, "ok (2/8→)"):
            self.assertIsNone(S.leer_marca(mala), mala)


class Reintentos(Arnes):
    def test_tres_errores_seguidos_cortan_los_intentos(self):
        self.canal_falla = True
        for _ in range(3):
            self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(len(self.ventas()), 3)
        self.llamadas.clear()
        for _ in range(4):
            self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [], "ya no se llama al canal")
        self.assertEqual(len(self.de(S.ACC_OMITIR)), 1)
        self.assertIn("3 errores", self.de(S.ACC_OMITIR)[0]["resultado"])
        self.assertEqual(len([a for a in self.avisos if a[0].startswith("seguro_cero_errores:temu")]), 1)
        self.assertEqual(len([l for l in self.llamadas if l[0] == "stock"]), 4, "el 0 se sigue escribiendo")

    def test_soltarla_a_mano_lo_destraba(self):
        self.canal_falla = True
        for _ in range(3):
            self.aplicar(plan(accion("temu", 4)))
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", return_value=[{"listing_id": G, "cuenta": "TEMU"}]):
            r = S.soltar(SKU, "temu")
        self.assertTrue(r["ok"])
        self.assertEqual(self.de(S.ACC_SOLTAR)[0]["resultado"], "ok (manual)")
        self.canal_falla = False
        self.llamadas.clear()
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])

    def test_el_recuperador_no_cuenta_las_filas_del_seguro(self):
        self.assertFalse([a for a in R._ACC_FANOUT if a.startswith("cero_")])
        self.assertFalse(set(R._ACC_FANOUT) & set(S.ACCIONES))
        self.assertFalse([a for a in V._ACC_FANOUT if a.startswith("cero_")],
                         "«el fan-out ya lo procesó» tampoco se decide con las filas del seguro")


class Sellado(Arnes):
    def test_si_la_marca_no_se_guarda_avisa_en_rojo_y_no_vuelve_a_apagar(self):
        self.aplicar(plan(accion("temu", 4)))          # control: aquí sí se guarda
        self.assertEqual(len(self.de(S.ACC_INACTIVAR)), 1)
        self.filas.clear()
        self.llamadas.clear()
        self.avisos.clear()
        self.canal[("temu", G)]["estado"] = "2/8"
        def solo_la_marca(fila):
            # El intento entra; lo que kubera rechaza es la MARCA, segundos después.
            if fila["accion"] == S.ACC_INACTIVAR:
                raise RuntimeError("kubera caída")
            self.filas.append(dict(fila))
            return 1

        with mock.patch.object(fanout_read, "registrar", side_effect=solo_la_marca) as reg:
            self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)], "se apagó UNA vez")
        self.assertEqual(reg.call_count, 1 + S._SELLAR_INTENTOS, "el intento y tres de la marca, ni uno más")
        self.assertEqual([f["accion"] for f in self.filas], [S.ACC_INTENTO],
                         "queda el intento: de ahí la recupera el siguiente censo")
        rojas = [a for a in self.avisos if a[0].startswith("seguro_cero_sin_marca:temu")]
        self.assertEqual(len(rojas), 1)
        self.assertIn("APAGADA", rojas[0][1])
        self.assertEqual(rojas[0][2], "🔴")

    def test_sin_poder_sellar_el_intento_no_se_llama_al_canal(self):
        # El pooler envenenado de la regla 13: kubera contesta lecturas y rechaza
        # escrituras. Antes se apagaban 25 de 25 sin una sola marca.
        with mock.patch.object(fanout_read, "registrar",
                               side_effect=RuntimeError("cannot execute INSERT in a read-only transaction")):
            self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [], "sin bitácora no se actúa")
        self.assertIn(("stock", "temu", G, 0), self.llamadas, "el 0 se escribe igual")
        self.assertEqual(self.canal[("temu", G)]["estado"], "2/8")
        self.assertEqual([a for a in self.avisos if "sin_marca" in a[0]], [], "nada quedó apagado")

    def test_una_fila_de_ensayo_que_no_se_guarda_no_alarma(self):
        self.en_ensayo()
        with mock.patch.object(fanout_read, "registrar", side_effect=RuntimeError("kubera caída")):
            self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.avisos, [])

    def test_las_filas_no_pasan_por_el_espejo_que_se_traga_errores(self):
        with mock.patch.object(fanout_read, "espejar") as espejar:
            self.aplicar(plan(accion("temu", 4)))
        espejar.assert_not_called()
        self.assertEqual(len(self.de(S.ACC_INACTIVAR)), 1)

    def test_sin_bitacora_en_kubera_solo_queda_mysql(self):
        self.ajustes(supabase_write_fanout_log=False)
        with mock.patch.object(S, "_a_mysql", return_value=True) as my:
            self.assertTrue(S.sellar({"sku": SKU, "accion": S.ACC_OMITIR}))
        my.assert_called_once()
        self.assertEqual(self.filas, [])


class Barrido(Arnes):
    FILA = {"sku": SKU, "listing_id": G, "status": "2/8", "stock_own": 2}

    def _barrer(self, p, canal="temu", filas=None):
        with mock.patch.object(S, "candidatos", return_value=filas if filas is not None else [self.FILA]), \
                mock.patch.object(F, "plan", return_value=p):
            return S.barrer(canal)

    def test_el_sql_no_trae_full_y_solo_trae_lo_que_esta_a_la_venta_con_woo_en_cero(self):
        sql = " ".join(S._SQL_BARRIDO.split())
        self.assertIn("not coalesce(l.is_fulfillment, false)", sql)
        self.assertIn("l.status = any(%(apagables)s)", sql)
        self.assertIn("p.stock_woo <= 0", sql)
        self.assertIn("l.listing_id is not null", sql)

    def test_apaga_lo_que_esta_a_la_venta_con_woo_en_cero(self):
        r = self._barrer(plan(accion("temu", 2)))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])
        f = self.de(S.ACC_INACTIVAR)[0]
        self.assertEqual((f["motivo"], f["resultado"]), ("seguro: barrido tras el censo de temu", "ok (2/8→3/2)"))
        self.assertEqual((r["ok"], r[S.ACC_INACTIVAR]), (True, 1))
        self.assertNotIn(("stock", "temu", G, 0), self.llamadas, "el 0 lo bajan los excedentes, después")

    def test_relee_woo_antes_de_actuar(self):
        # La foto decía 0, pero Woo en vivo ya tiene 3: no se toca.
        self._barrer(plan(accion("temu", 2), objetivo=3))
        self.assertEqual((self.ventas(), self.filas), ([], []))
        self._barrer({"sku": SKU, "ok": False, "motivo": "sin stock legible", "acciones": []})
        self.assertEqual((self.ventas(), self.filas), ([], []))

    def test_no_corre_con_el_fanout_apagado_ni_para_otros_canales(self):
        self.assertFalse(S.barrer("mercado_libre")["ok"])
        self.ajustes(fanout_enabled=False)
        r = self._barrer(plan(accion("temu", 2)))
        self.assertEqual(r, {"ok": False, "motivo": S.APAGADO})
        self.assertEqual((self.ventas(), self.lecturas, self.filas), ([], [], []))

    def test_devuelve_a_la_cola_las_marcas_con_stock_de_vuelta(self):
        self.sembrar_marca()
        self.foto[SKU] = 6
        r = self._barrer(plan(), filas=[])
        self.assertEqual(self.encolados, [(SKU, S.MOTIVO_MARCA)])
        self.assertEqual(r["reencolados"], [SKU])

    def test_avisa_de_lo_que_quedo_apagado_sin_marca_tras_un_reinicio(self):
        # El proceso murió entre llamar al canal y sellar: el estado lo firmó el seguro
        # en el historial, pero la bitácora no tiene la fila.
        self.huerfanas = [{"canal": "temu", "sku": SKU, "item_id": G, "de": "2/8", "a": "3/2",
                           "cuando": "2026-10-07 10:00:00"}]
        self._barrer(plan(), filas=[])
        rojas = [a for a in self.avisos if a[0] == f"seguro_cero_sin_marca:temu:{SKU}"]
        self.assertEqual((len(rojas), rojas[0][2]), (1, "🔴"))
        self.avisos.clear()
        self._barrer(plan(), filas=[])
        self.assertEqual(self.avisos, [], "una vez por proceso, no en cada censo")
        # Si lo que el seguro dejó fue «a la venta» (una reactivación sin fila), no es alarma.
        S._huerfanas_avisadas.clear()
        self.huerfanas = [{"canal": "temu", "sku": SKU, "item_id": G, "de": "3/2", "a": "2/8", "cuando": "x"}]
        self._barrer(plan(), filas=[])
        self.assertEqual(self.avisos, [])

    def test_una_marca_con_woo_en_cero_no_se_reencola(self):
        self.sembrar_marca()
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", return_value=[{"sku": SKU, "stock_woo": 0}]):
            self._barrer(plan(), filas=[])
        self.assertEqual(self.encolados, [])



class Censos(unittest.TestCase):
    """El gancho en los dos censos: se barre ANTES de los excedentes (primero se
    apaga, después se baja) y en un HILO, no en el event loop (regla 11)."""

    def _censar(self, canal, encendido=True):
        from services import fanout_excedentes as E
        from services import supabase_db as sdb
        from services import temu_censo, tiktok_censo
        orden: list[tuple] = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                pass

        async def sin_productos(*a, **k):
            return [] if canal == "temu" else {"products": [], "next_page_token": ""}

        def anotar(quien):
            return lambda c: orden.append((quien, c, threading.get_ident())) or {"ok": True}

        parches = [
            mock.patch.object(F, "seguro_encendido", return_value=encendido),
            mock.patch.object(S, "barrer", side_effect=anotar("seguro")),
            mock.patch.object(E, "revisar", side_effect=anotar("excedentes")),
            mock.patch.object(sdb, "fetch_one", return_value={"id": "cuenta"}),
            mock.patch.object(sdb, "fetch_all", return_value=[]),
            mock.patch.object(sdb, "get_cursor", side_effect=Cursor),
            mock.patch.object(TM, "disponible", return_value=True),
            mock.patch.object(TM, "listar_productos", side_effect=sin_productos),
            mock.patch.object(TK, "access_token", return_value="t"),
            mock.patch.object(TK, "cipher", return_value="c"),
            mock.patch.object(TK, "llamar", side_effect=sin_productos),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        salida = asyncio.run((temu_censo if canal == "temu" else tiktok_censo).censar())
        return salida, orden

    def test_barren_antes_de_los_excedentes_y_en_un_hilo(self):
        for canal in ("temu", "tiktok"):
            with self.subTest(canal=canal):
                salida, orden = self._censar(canal)
                self.assertEqual([(q, c) for q, c, _ in orden], [("seguro", canal), ("excedentes", canal)])
                self.assertNotEqual(orden[0][2], threading.get_ident(),
                                    "el barrido corrió en el hilo del event loop")
                self.assertEqual(salida["seguro"], {"ok": True})

    def test_con_el_fanout_apagado_el_censo_ni_menciona_al_seguro(self):
        for canal in ("temu", "tiktok"):
            with self.subTest(canal=canal):
                salida, orden = self._censar(canal, encendido=False)
                self.assertEqual([q for q, _, _ in orden], ["excedentes"])
                self.assertNotIn("seguro", salida)


class Excedentes(Arnes):
    def test_si_el_canal_ofrece_piezas_con_woo_en_cero_apaga_y_despues_baja(self):
        self.canal[("temu", G)]["stock"] = 14
        with mock.patch.object(F, "plan", return_value=plan(accion("temu", 14))):
            r = F.bajar(SKU, "temu", "excedente:temu")
        self.assertEqual(r["resultado"], "bajado")
        self.assertEqual(self.llamadas, [("temu.venta", G, False, None), ("stock", "temu", G, 0)])
        self.assertEqual(self.de(S.ACC_INACTIVAR)[0]["ts"], self.eventos[0]["ts_dt"])

    def test_con_el_fanout_apagado_bajar_hace_lo_de_siempre(self):
        self.ajustes(fanout_enabled=False)
        self.canal[("temu", G)]["stock"] = 14
        with mock.patch.object(F, "plan", return_value=plan(accion("temu", 14))):
            F.bajar(SKU, "temu", "excedente:temu")
        self.assertEqual((self.llamadas, self.filas), ([("stock", "temu", G, 0)], []))


class Clientes(unittest.TestCase):
    """Los dos clientes nuevos, contra un `llamar` falso."""

    def _tiktok(self, data):
        vistos = []

        async def llamar(ruta, token, params=None, cuerpo=None, metodo="GET"):
            vistos.append((ruta, token, params, cuerpo, metodo))
            return data

        return vistos, mock.patch.object(TK, "llamar", side_effect=llamar)

    def test_tiktok_desactivar_y_activar_mandan_un_producto(self):
        for fn, ruta in ((TK.desactivar, "deactivate"), (TK.activar, "activate")):
            vistos, p = self._tiktok({"errors": []})
            with p:
                asyncio.run(fn(P, "tok", "cif"))
            self.assertEqual(vistos, [(f"/product/202309/products/{ruta}", "tok",
                                       {"shop_cipher": "cif"}, {"product_ids": [P]}, "POST")])

    def test_tiktok_con_code_0_y_data_errors_es_error(self):
        vistos, p = self._tiktok({"errors": [{"code": 12052901, "message": "Operation Not Allowed",
                                              "detail": {"product_id": P}}]})
        with p, self.assertRaises(RuntimeError) as cm:
            asyncio.run(TK.desactivar(P, "tok", "cif"))
        self.assertIn("12052901", str(cm.exception))
        vistos, p = self._tiktok({"errors": [{"code": 12052093}]})
        with p, self.assertRaises(RuntimeError):
            asyncio.run(TK.activar(P, "tok", "cif"))

    def test_tiktok_sin_token_no_llama(self):
        vistos, p = self._tiktok({})
        with p, mock.patch.object(TK, "access_token", return_value=None), \
                mock.patch.object(TK, "cipher", return_value=None), self.assertRaises(RuntimeError):
            asyncio.run(TK.desactivar(P))
        self.assertEqual(vistos, [])

    def test_temu_cambiar_venta_arma_el_cuerpo(self):
        vistos = []

        async def llamar(tipo, datos=None, timeout=40.0):
            vistos.append((tipo, datos))
            return {"success": True}

        with mock.patch.object(TM, "llamar", side_effect=llamar):
            asyncio.run(TM.cambiar_venta(G, False))
            asyncio.run(TM.cambiar_venta(G, True))
            asyncio.run(TM.cambiar_venta(G, False, [5001]))
        self.assertEqual(vistos, [
            ("bg.local.goods.sale.status.set", {"goodsId": int(G), "onsale": 0}),
            ("bg.local.goods.sale.status.set", {"goodsId": int(G), "onsale": 1}),
            ("bg.local.goods.sale.status.set", {"goodsId": int(G), "onsale": 0,
                                                "skuIdList": [5001], "operationType": 2})])


class LecturaEnVivo(unittest.TestCase):
    """Los dos lectores, contra `llamar` falso (el del fan-out, que ya existía)."""

    def setUp(self):
        p = mock.patch.object(F, "_en_hilo",
                              side_effect=lambda fabrica, etiqueta, timeout=60: asyncio.run(fabrica()))
        p.start()
        self.addCleanup(p.stop)

    def _temu(self, cubeta_real, estado=(2, 8), existe=True):
        """Un `llamar` que contesta como Temu (medido el 9-oct-2026): el goods sólo sale
        CON SU ESTADO en la cubeta donde vive; en otra cubeta, lista vacía; y sin
        cubeta sale la fila con `status4VO` y `subStatus4VO` en null."""
        vistos: list[dict] = []
        fila = {"goodsId": int(G), "outGoodsSn": SKU, "quantity": 2, "skuIdList": [5001]}

        async def llamar(tipo, datos=None, timeout=40.0):
            self.assertEqual(tipo, "bg.local.goods.list.query")
            vistos.append(dict(datos or {}))
            cubeta = (datos or {}).get("goodsSearchType")
            if not existe:
                return {"goodsList": []}
            if cubeta is None:
                return {"goodsList": [dict(fila, status4VO=None, subStatus4VO=None,
                                           goodsShowSubStatus=3003)]}
            if cubeta != cubeta_real:
                return {"goodsList": []}
            return {"goodsList": [dict(fila, status4VO=estado[0], subStatus4VO=estado[1])]}

        return vistos, mock.patch.object(TM, "llamar", side_effect=llamar)

    def test_temu(self):
        vistos, p = self._temu(1)
        with p, mock.patch.object(TM, "disponible", return_value=True):
            v = S._vivo_temu(G)
        self.assertEqual((v["ok"], v["estado"], v["stock"]), (True, "2/8", 2))
        self.assertEqual(v["skus"], [{"id": 5001, "seller_sku": SKU, "cantidad": 2}])
        self.assertEqual(S._stock_vivo(v, SKU), 2)

    def test_temu_pregunta_con_la_cubeta_o_no_hay_estado(self):
        # El canario del 9-oct: sin `goodsSearchType` la fila llega con el estado en
        # null y el seguro no podía decidir nada. Con la cubeta 1 basta UNA llamada.
        vistos, p = self._temu(1, estado=(3, 1))
        with p, mock.patch.object(TM, "disponible", return_value=True):
            v = S._vivo_temu(G)
        self.assertEqual((v["ok"], v["estado"]), (True, "3/1"))
        self.assertEqual(vistos, [{"pageNo": 1, "pageSize": 10, "goodsIdList": [int(G)],
                                   "goodsSearchType": 1}])
        self.assertIs(type(vistos[0]["goodsSearchType"]), int, "como cadena Temu contesta 3000000")

    def test_temu_en_otra_cubeta_se_encuentra(self):
        vistos, p = self._temu(4, estado=(4, 7))
        with p, mock.patch.object(TM, "disponible", return_value=True):
            v = S._vivo_temu(G)
        self.assertEqual((v["ok"], v["estado"]), (True, "4/7"))
        self.assertEqual([d.get("goodsSearchType") for d in vistos], [1, 4])
        self.assertFalse(S.apagable("temu", v["estado"]), "y desde ahí no se apaga")

    def test_temu_fuera_de_las_cubetas_conocidas_es_ilegible(self):
        vistos, p = self._temu(9)
        with p, mock.patch.object(TM, "disponible", return_value=True):
            v = S._vivo_temu(G)
        self.assertFalse(v["ok"])
        self.assertIn("no está en ninguna cubeta conocida", v["motivo"])
        self.assertEqual([d.get("goodsSearchType") for d in vistos], list(TM.CUBETAS) + [None])

    def test_temu_eliminado_se_distingue(self):
        vistos, p = self._temu(1, existe=False)
        with p, mock.patch.object(TM, "disponible", return_value=True):
            v = S._vivo_temu(G)
        self.assertFalse(v["ok"])
        self.assertIn("no aparece en el listado", v["motivo"])
        self.assertEqual(len(vistos), len(TM.CUBETAS) + 1)

    def test_temu_ilegible(self):
        async def vacio(tipo, datos=None, timeout=40.0):
            return {"goodsList": []}

        async def roto(tipo, datos=None, timeout=40.0):
            raise RuntimeError("Temu: errorCode=5000003 NOT_IN_IP_WHITE_LIST")

        with mock.patch.object(TM, "disponible", return_value=True):
            with mock.patch.object(TM, "llamar", side_effect=vacio):
                self.assertFalse(S._vivo_temu(G)["ok"])
            with mock.patch.object(TM, "llamar", side_effect=roto):
                self.assertFalse(S._vivo_temu(G)["ok"])
        with mock.patch.object(TM, "disponible", return_value=False):
            self.assertFalse(S._vivo_temu(G)["ok"])

    def test_tiktok(self):
        async def llamar(ruta, token, params=None, cuerpo=None, metodo="GET"):
            return {"status": "ACTIVATE", "skus": [
                {"id": "S1", "seller_sku": SKU, "inventory": [
                    {"warehouse_id": "otro", "quantity": 99},
                    {"warehouse_id": F._ALMACEN_VENTAS_TIKTOK, "quantity": 7}]},
                {"id": "S2", "seller_sku": HERMANA, "inventory": []}]}

        with mock.patch.object(TK, "llamar", side_effect=llamar), \
                mock.patch.object(TK, "access_token", return_value="t"), mock.patch.object(TK, "cipher", return_value="c"):
            v = S._vivo_tiktok(P)
        self.assertEqual((v["ok"], v["estado"]), (True, "ACTIVATE"))
        self.assertEqual(v["skus"], [{"id": "S1", "seller_sku": SKU, "cantidad": 7},
                                     {"id": "S2", "seller_sku": HERMANA, "cantidad": None}])
        self.assertEqual(S._stock_vivo(v, SKU), 7, "el del almacén de ventas")

    def test_tiktok_sin_token(self):
        with mock.patch.object(TK, "access_token", return_value=None), mock.patch.object(TK, "cipher", return_value=None):
            self.assertFalse(S._vivo_tiktok(P)["ok"])


class AMano(Arnes):
    """A mano sólo queda SOLTAR una marca. La vía del canario (`aplicar`) se fue con el
    canario: el seguro ya corre solo sobre todo el catálogo."""

    def test_la_agotada_de_temu_se_apaga_de_fabrica(self):
        # `3/1` (agotada) es de donde Temu regresa SOLA a la venta. El canario del
        # 9-oct-2026 midió `3/1→3/2`; desde entonces nace en la lista de apagables.
        self.assertEqual(type(S.settings).model_fields["fanout_cero_temu_estados"].default, "2/8,3/1")
        self.ajustes(fanout_cero_temu_estados="2/8,3/1")
        self.assertEqual(S.estados_temu(), {"2/8", "3/1"})
        self.canal[("temu", G)]["estado"] = "3/1"
        self.canal[("temu", G)]["stock"] = 0
        self.aplicar(plan(accion("temu", 0, accion="sin_cambio", estado="3/1")))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])
        self.assertEqual(self.de(S.ACC_INACTIVAR)[0]["resultado"], "ok (3/1→3/2)")

    def test_el_barrido_busca_tambien_las_agotadas(self):
        self.ajustes(fanout_cero_temu_estados="2/8,3/1")
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", return_value=[]) as fa:
            S.candidatos("temu")
        self.assertEqual(fa.call_args[0][1]["apagables"], ["2/8", "3/1"])

    def test_regresa_a_la_venta_lo_que_apago_desde_agotada(self):
        self.ajustes(fanout_cero_temu_estados="2/8,3/1")
        self.sembrar_marca(resultado="ok (3/1→3/2)")
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertEqual(self.llamadas, [("stock", "temu", G, 5), ("temu.venta", G, True, None)])
        self.assertEqual(self.de(S.ACC_REACTIVAR)[0]["resultado"], "ok (3/2→2/8)")

    def test_soltar_no_toca_el_canal_y_cierra_la_marca(self):
        self.sembrar_marca()
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", return_value=[]):
            r = S.soltar(SKU, "temu")
        self.assertTrue(r["ok"])
        self.assertEqual((self.ventas(), self.lecturas), ([], []))
        self.assertIsNone(self._marca("temu", G))
        with mock.patch.object(sdb, "fetch_all", return_value=[]):
            self.assertFalse(S.soltar("ZZZ-9999-NEG", "temu")["ok"])
        self.assertFalse(S.soltar(SKU, "amazon")["ok"])


class AvisoDeVenta(Arnes):
    def _fecha(self, minutos_tras_la_marca):
        marca = self._marca("temu", G)["ts"]
        return (marca + timedelta(minutes=minutos_tras_la_marca)).isoformat()

    def test_una_venta_posterior_a_la_marca_avisa_en_rojo(self):
        self.sembrar_marca(hace_h=5)
        n = S.aviso_venta("TEMU", [SKU], self._fecha(60), "PO-TEST-1")
        self.assertEqual(n, 1)
        tipo, texto, nivel = self.avisos[-1]
        self.assertEqual((tipo, nivel), (f"seguro_cero_venta:temu:{SKU}", "🔴"))
        self.assertIn("estando apagada", texto)

    def test_una_venta_de_los_primeros_15_min_no_avisa(self):
        self.sembrar_marca(hace_h=5)
        self.assertEqual(S.aviso_venta("TEMU", [SKU], self._fecha(10), "PO-TEST-2"), 0)
        self.assertEqual(S.aviso_venta("TEMU", [SKU], self._fecha(-30), "PO-TEST-3"), 0)
        self.assertEqual(self.avisos, [])

    def test_sin_marca_o_en_otro_canal_no_avisa(self):
        ahora = datetime.now(timezone.utc).isoformat()
        self.assertEqual(S.aviso_venta("TEMU", [SKU], ahora), 0)
        self.sembrar_marca(hace_h=5)
        self.assertEqual(S.aviso_venta("TIKTOK", [SKU], ahora), 0)
        self.assertEqual(S.aviso_venta("BEKURA", [SKU], ahora), 0)
        self.assertEqual(S.aviso_venta("TEMU", ["OTRO-0001-AZL"], ahora), 0)
        self.assertEqual(S.aviso_venta("TEMU", [SKU], None), 0)
        self.assertEqual(self.avisos, [])

    def test_nunca_lanza_ni_con_la_bitacora_muda(self):
        self.bitacora_caida = True
        self.assertEqual(S.aviso_venta("TEMU", [SKU], datetime.now(timezone.utc).isoformat()), 0)
        self.assertEqual(S.aviso_venta("TEMU", [SKU], "no-es-fecha"), 0)

    def test_pedidos_lo_llama_en_un_hilo_y_solo_en_ventas_nuevas(self):
        fuente = (BACKEND / "services" / "pedidos_ml.py").read_text(encoding="utf-8")
        i = fuente.index("fanout_seguro.aviso_venta")
        bloque = fuente[i - 400:i + 200]
        self.assertIn("await asyncio.to_thread(fanout_seguro.aviso_venta", bloque)
        self.assertIn('accion == "creado"', bloque)
        self.assertIn("fanout_stock.seguro_encendido()", bloque)


class Panel(unittest.TestCase):
    """Lo que pinta la página: sin filas del seguro, igual que antes."""

    def _fila(self, acc, resultado, dry=False, canal="temu", stock=4, objetivo=0):
        return {"accion": acc, "resultado": resultado, "dry_run": dry, "canal": canal,
                "stock_canal": stock, "objetivo": objetivo}

    def test_sin_filas_del_seguro_la_celda_es_la_de_siempre(self):
        casos = [[], [self._fila("escribir", "ok")], [self._fila("escribir", "ERROR: HTTP 403")],
                 [self._fila("sin_cambio", "el canal ya tiene 0")],
                 [self._fila("omitir", "FULL/FBA (bodega del marketplace, no se toca)")],
                 [self._fila("escribir", "DRY-RUN (no se escribió)")]]
        for filas in casos:
            self.assertEqual(V._celda_evento(filas), V._celda_reparto(filas))

    def test_apagada_por_stock_0(self):
        c = V._celda_evento([self._fila("escribir", "ok"), self._fila("cero_inactivar", "ok (2/8→3/2)")])
        self.assertEqual((c["k"], c["texto"]), ("apag", "apagada por stock 0"))
        self.assertIn("2/8→3/2", c["detalle"])
        solo = V._celda_evento([self._fila("cero_inactivar", "ok (2/8→3/2)")])
        self.assertEqual(solo["k"], "apag")
        self.assertEqual(V._tono({"temu": solo}), "ok")

    def test_un_rechazo_del_stock_sigue_mandando(self):
        c = V._celda_evento([self._fila("escribir", "ERROR: HTTP 403"),
                             self._fila("cero_inactivar", "ok (2/8→3/2)")])
        self.assertEqual(c["k"], "mal")
        self.assertIn("seguro", c["detalle"])

    def test_un_error_del_seguro_no_pinta_el_canal_como_rechazo(self):
        c = V._celda_evento([self._fila("escribir", "ok"), self._fila("cero_error", "ERROR: no convergió")])
        self.assertEqual(c["k"], "ok")
        self.assertIn("no convergió", c["detalle"])
        self.assertEqual(V._celda_evento([self._fila("cero_error", "ERROR: x")])["k"], "omit")

    def test_el_ensayo_se_ve_como_simulacion(self):
        c = V._celda_evento([self._fila("cero_inactivar", "ENSAYO (apagaría; vivo 2/8, ofrece 2)", dry=True)])
        self.assertEqual((c["k"], c["texto"]), ("sim", "apagaría (ensayo)"))
        con = V._celda_evento([self._fila("sin_cambio", "el canal ya tiene 0", stock=0),
                               self._fila("cero_inactivar", "ENSAYO (apagaría; vivo 2/8, ofrece 0)", dry=True)])
        self.assertEqual(con["k"], "igual")
        self.assertIn("ENSAYO", con["detalle"])

    def test_reactivada(self):
        c = V._celda_evento([self._fila("escribir", "ok", stock=0, objetivo=5),
                             self._fila("cero_reactivar", "ok (3/2→2/8)", objetivo=5)])
        self.assertEqual(c["k"], "ok")
        self.assertTrue(c["texto"].startswith("reactivada"))

    def test_la_matriz_dice_apagada_solo_si_sigue_fuera_de_la_venta(self):
        l = {"canal": "temu", "status": "3/2", "stock_own": 0, "act": "2026-10-07 10:00:00"}
        marca = {"desde": "2026-10-07 09:00:00"}
        c = V._celda_matriz(l, None, 0, "2026-10-07", marca)
        self.assertEqual((c["k"], c["p"]), ("apag", True))
        self.assertIn("apagada por stock 0", c["s"])
        self.assertNotEqual(V._celda_matriz(dict(l, status="2/8"), None, 0, "2026-10-07", marca)["k"], "apag")
        self.assertEqual(V._celda_matriz(l, None, 0, "2026-10-07"),
                         V._celda_matriz(l, None, 0, "2026-10-07", None), "sin marca, la de siempre")

    def test_el_origen_del_cambio(self):
        self.assertEqual(V._origen("seguro: barrido tras el censo de temu", None)[0], "otro")
        self.assertTrue(V._origen("seguro: reactivar tras espera", None)[1].startswith("seguro stock 0"))

    def test_las_acciones_del_panel_son_las_del_seguro(self):
        self.assertEqual(sorted(V._ACC_CERO), sorted(S.ACCIONES))
        self.assertTrue(all(len(a) <= 20 for a in S.ACCIONES), "caben en el VARCHAR(20) de MySQL")


# ══════════════════════════════════════════════════════════════════════════════
# Lo que encontró la revisión del 7-oct-2026. Una clase por hueco.

def _varios(arnes, n=7):
    """`n` SKUs de Temu a la venta, ofreciendo 2 piezas con Woo en 0."""
    skus = [f"ZZZ-02{i:02d}-NEG" for i in range(n)]
    planes, filas = {}, []
    for i, s in enumerate(skus):
        item = f"92{i:04d}"
        arnes.canal[("temu", item)] = {"estado": "2/8", "stock": 2,
                                       "skus": [{"id": 7000 + i, "seller_sku": s, "cantidad": 2}]}
        planes[s] = plan(accion("temu", 2, item=item), sku=s)
        filas.append({"sku": s, "listing_id": item, "status": "2/8", "stock_own": 2})
    return skus, filas, planes


def _barrer(filas=(), planes=None, canal="temu"):
    with mock.patch.object(S, "candidatos", return_value=list(filas)), \
            mock.patch.object(F, "plan", side_effect=lambda sku: (planes or {})[sku]):
        return S.barrer(canal)


class Intentos(Arnes):
    """Los topes cuentan LLAMADAS al canal —salgan bien o no— y el rastro se sella
    ANTES de llamar: un contador de éxitos se quedaba en 0/20 con 60 llamadas hechas."""

    def test_el_intento_se_sella_antes_de_llamar_al_canal(self):
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.orden[:2], [("sello", S.ACC_INTENTO), ("canal", "temu", False)])
        (i,) = self.intentos("apagar")
        self.assertEqual((i["resultado"], i["dry_run"], i["item_id"]), ("apagar (vivo 2/8)", False, G))
        self.assertEqual(S.leer_intento(i["resultado"]), {"que": "apagar", "de": "2/8", "variante": None})
        self.assertIsNone(S.leer_intento("ok (2/8→3/2)"))

    def test_al_reactivar_tambien_se_sella_antes(self):
        self.sembrar_marca()
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        sellos_y_canal = [o for o in self.orden if o[0] == "canal" or o[1] == S.ACC_INTENTO]
        self.assertEqual(sellos_y_canal, [("sello", S.ACC_INTENTO), ("canal", "temu", True)])
        self.assertEqual(self.intentos("prender")[0]["resultado"], "prender (vivo 3/2)")

    def test_el_ensayo_no_sella_intentos(self):
        self.en_ensayo()
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.intentos(), [])

    def test_lo_que_no_converge_tambien_gasta_el_tope_del_dia(self):
        self.ajustes(fanout_cero_tope_dia=3, fanout_cero_tope_vuelta=99)
        self.canal_obedece = False               # Temu contesta «ok» y sigue diciendo 2/8
        _skus, filas, planes = _varios(self)
        with mock.patch.object(S, "_NO_CONVERGE_MAX", 99):      # sin la pausa: aquí se prueba el tope
            _barrer(filas, planes)
        self.assertEqual(len(self.ventas()), 3, "tres llamadas, aunque ninguna se haya visto converger")
        self.assertEqual(len(self.intentos("apagar")), 3)
        self.assertEqual(self.de(S.ACC_INACTIVAR), [])
        self.assertEqual([f["resultado"] for f in self.de(S.ACC_OMITIR)], ["tope del día (3)"] * 4)

    def test_un_rechazo_del_canal_tambien_gasta_el_tope(self):
        self.ajustes(fanout_cero_tope_dia=2, fanout_cero_tope_vuelta=99)
        self.canal_falla = True
        _skus, filas, planes = _varios(self)
        _barrer(filas, planes)
        self.assertEqual(len(self.ventas()), 2)

    def test_el_tope_no_depende_de_que_kubera_lleve_la_cuenta(self):
        # kubera contesta el conteo en 0: manda lo que ESTE proceso sabe que llamó.
        self.ajustes(fanout_cero_tope_dia=2, fanout_cero_tope_vuelta=99)
        _skus, filas, planes = _varios(self)
        with mock.patch.object(S, "usadas_hoy", return_value=0):
            _barrer(filas, planes)
        self.assertEqual(len(self.ventas()), 2)

    def test_el_piso_en_memoria_cuenta_tambien_los_rechazos(self):
        self.ajustes(fanout_cero_tope_dia=2, fanout_cero_tope_vuelta=99)
        self.canal_falla = True
        _skus, filas, planes = _varios(self)
        with mock.patch.object(S, "usadas_hoy", return_value=0):
            _barrer(filas, planes)
        self.assertEqual(len(self.ventas()), 2, "se cuenta al llamar, no al terminar bien")

    def test_el_piso_en_memoria_es_por_dia(self):
        S._contar_intento("temu", S.INTENTO_APAGAR)
        with mock.patch.object(S, "usadas_hoy", return_value=0):
            self.assertEqual(S._usadas("temu", S.ACC_INACTIVAR), 1)
            self.assertEqual(S._usadas("tiktok", S.ACC_INACTIVAR), 0)
            self.assertEqual(S._usadas("temu", S.ACC_REACTIVAR), 0)
            self.t[0] += 2 * 86400
            self.assertEqual(S._usadas("temu", S.ACC_INACTIVAR), 0, "otro día, cuenta nueva")
        with mock.patch.object(S, "usadas_hoy", return_value=9):
            self.assertEqual(S._usadas("temu", S.ACC_INACTIVAR), 9, "manda el mayor")

    def test_llegar_al_tope_del_dia_avisa_en_rojo_una_sola_vez(self):
        self.ajustes(fanout_cero_tope_dia=2, fanout_cero_tope_vuelta=99)
        _skus, filas, planes = _varios(self)
        _barrer(filas, planes)
        (tope,) = [a for a in self.avisos if a[0] == "seguro_cero_tope:temu"]
        self.assertEqual(tope[2], "🔴")
        self.assertIn("tope de 2 apagados", tope[1])
        _barrer(filas[2:], planes)
        self.assertEqual(len([a for a in self.avisos if a[0] == "seguro_cero_tope:temu"]), 1)
        self.assertEqual(len(self.ventas()), 2, "y sigue sin apagar más")

    def test_el_tope_de_reactivaciones_tambien_avisa(self):
        self.ajustes(fanout_cero_tope_reactivar_dia=1)
        self.usadas_extra[("temu", S.ACC_REACTIVAR)] = 1
        self.sembrar_marca()
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertEqual(self.ventas(), [])
        self.assertEqual(self.de(S.ACC_OMITIR)[0]["resultado"], "tope de reactivaciones del día (1)")
        self.assertIn("seguro_cero_tope_reactivar:temu", [a[0] for a in self.avisos])

    def test_dos_publicaciones_del_mismo_sku_no_se_brincan_el_tope(self):
        # Con 19/20 usadas, la segunda publicación del MISMO evento no veía a la primera.
        self.usadas_extra[("temu", S.ACC_INACTIVAR)] = 19
        g2 = "900002"
        self.canal[("temu", g2)] = {"estado": "2/8", "stock": 4,
                                    "skus": [{"id": 5002, "seller_sku": SKU, "cantidad": 4}]}
        self.aplicar(plan(accion("temu", 4), accion("temu", 4, item=g2)))
        self.assertEqual(len(self.ventas()), 1)

    def test_dos_que_no_convergen_seguidas_pausan_el_canal(self):
        self.canal_obedece = False
        _skus, filas, planes = _varios(self)
        _barrer(filas, planes)
        self.assertEqual(len(self.ventas()), 2, "a la segunda sin confirmar deja de llamar")
        pausas = [f["resultado"] for f in self.de(S.ACC_OMITIR)]
        self.assertEqual(len(pausas), 5)
        self.assertTrue(all(p.startswith("pausa: Temu no confirmó 2 apagados seguidos") for p in pausas))
        (aviso,) = [a for a in self.avisos if a[0] == "seguro_cero_pausa:temu"]
        self.assertEqual(aviso[2], "🔴")
        self.assertEqual(S._en_pausa("tiktok"), 0, "la pausa es del canal que no confirma")
        # Pasada la hora se vuelve a intentar.
        self.t[0] += S._PAUSA_S + 1
        self.llamadas.clear()
        self.canal_obedece = True
        _barrer(filas[2:3], planes)
        self.assertEqual(len(self.ventas()), 1)

    def test_una_que_si_converge_borra_la_cuenta_de_la_pausa(self):
        self.assertFalse(S._no_convergio("temu"))
        S._convergio("temu")
        self.assertFalse(S._no_convergio("temu"), "no eran seguidas")
        self.assertTrue(S._no_convergio("temu"))
        self.assertGreater(S._en_pausa("temu"), 0)

    def test_la_espera_y_las_relecturas_se_ajustan_sin_deploy(self):
        self.ajustes(fanout_cero_espera_s=2.0, fanout_cero_relecturas=1)
        self.canal_obedece = False
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.dormidas, [2.0])


class TopePorVuelta(Arnes):
    """El tope «por vuelta» es del censo entero —barrido Y excedentes—, y los eventos
    sueltos llevan el mismo número por hora."""

    def test_los_excedentes_del_mismo_censo_gastan_el_tope_de_la_vuelta(self):
        _skus, filas, planes = _varios(self)
        _barrer(filas, planes)                     # el barrido apaga 5
        self.assertEqual(len(self.ventas()), 5)
        self.llamadas.clear()
        for f in filas[5:]:                        # y enseguida, los excedentes de las otras 2
            with mock.patch.object(F, "plan", return_value=planes[f["sku"]]):
                F.bajar(f["sku"], "temu", "excedente:temu")
        self.assertEqual(self.ventas(), [], "antes eran 5 + 15 en un solo censo")
        self.assertEqual([l for l in self.llamadas if l[0] == "stock"],
                         [("stock", "temu", "920005", 0), ("stock", "temu", "920006", 0)],
                         "el stock sí se baja")

    def test_el_presupuesto_es_del_canal_y_caduca(self):
        _skus, filas, planes = _varios(self)
        _barrer(filas, planes)
        self.assertIsNone(S._presupuesto_de("excedente:tiktok"))
        self.assertIsNone(S._presupuesto_de("cambio de stock en Woo"))
        self.assertEqual(S._presupuesto_de("excedente:temu")["reales"], 5)
        self.t[0] += S._PRESUPUESTO_S + 1
        self.assertIsNone(S._presupuesto_de("excedente:temu"))

    def test_los_eventos_sueltos_llevan_el_tope_por_hora(self):
        skus, _filas, planes = _varios(self)
        for s in skus:
            self.aplicar(planes[s])
        self.assertEqual(len(self.ventas()), 5, "40 SKUs que caían a 0 eran 40 llamadas en una tanda")
        self.assertEqual([f["resultado"] for f in self.de(S.ACC_OMITIR)],
                         ["tope por hora (5): la apaga el barrido del siguiente censo"] * 2)
        self.assertEqual(len([l for l in self.llamadas if l[0] == "stock"]), 7, "el 0 se escribe a todas")
        self.t[0] += S._HORA_S + 1
        self.llamadas.clear()
        self.aplicar(planes[skus[5]])
        self.assertEqual(len(self.ventas()), 1, "pasada la hora, sigue")

    def test_el_barrido_no_gasta_el_tope_por_hora_de_los_eventos(self):
        skus, filas, planes = _varios(self)
        _barrer(filas[:5], planes)
        self.assertEqual(S._sueltos_en_la_hora("temu"), 0)
        self.llamadas.clear()
        self.aplicar(planes[skus[5]])
        self.assertEqual(len(self.ventas()), 1)
        self.assertEqual(S._sueltos_en_la_hora("temu"), 1)


class SinTope(Arnes):
    """Decisión de Brandon (9-oct-2026): «puede ser que en un día se acaben 20 SKUs de
    un jalón; déjalo sin tope». Los topes nacen en 0 y 0 es SIN TOPE: ni por vuelta, ni
    por hora, ni por día, ni al reactivar."""

    def setUp(self):
        super().setUp()
        self.ajustes(fanout_cero_tope_vuelta=0, fanout_cero_tope_dia=0,
                     fanout_cero_tope_reactivar_dia=0)

    def test_los_tres_topes_nacen_en_cero(self):
        campos = type(S.settings).model_fields
        self.assertEqual([campos[k].default for k in (
            "fanout_cero_tope_vuelta", "fanout_cero_tope_dia", "fanout_cero_tope_reactivar_dia")],
            [0, 0, 0])

    def test_un_barrido_apaga_veinte_de_un_jalon(self):
        _skus, filas, planes = _varios(self, n=20)
        salida = _barrer(filas, planes)
        self.assertEqual(len(self.ventas()), 20)
        self.assertEqual(len(self.de(S.ACC_INACTIVAR)), 20)
        self.assertEqual(self.de(S.ACC_OMITIR), [])
        self.assertEqual(salida["llamadas"], 20)
        self.assertEqual([a for a in self.avisos if a[0].startswith("seguro_cero_tope")], [])

    def test_veinte_eventos_sueltos_en_la_misma_hora_tambien(self):
        skus, _filas, planes = _varios(self, n=20)
        for s in skus:
            self.aplicar(planes[s])
        self.assertEqual(len(self.ventas()), 20)
        self.assertEqual(self.de(S.ACC_OMITIR), [])

    def test_los_excedentes_del_mismo_censo_tampoco_se_frenan(self):
        _skus, filas, planes = _varios(self, n=8)
        _barrer(filas[:6], planes)
        self.llamadas.clear()
        for f in filas[6:]:
            with mock.patch.object(F, "plan", return_value=planes[f["sku"]]):
                F.bajar(f["sku"], "temu", "excedente:temu")
        self.assertEqual(len(self.ventas()), 2)

    def test_sin_tope_no_depende_de_poder_leer_la_cuenta_del_dia(self):
        # Con tope, no poder leer la cuenta en kubera detiene el apagado. Sin tope no
        # hay nada que leer: esa consulta caída no deja publicaciones a la venta.
        self.tope_caido = True
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])
        self.assertEqual(self.de(S.ACC_ERROR), [])

    def test_reactivar_tampoco_tiene_tope(self):
        self.tope_caido = True
        self.usadas_extra[("temu", S.ACC_REACTIVAR)] = 500
        self.sembrar_marca()
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertEqual(self.ventas(), [("temu.venta", G, True, None)])
        self.assertEqual(self.de(S.ACC_OMITIR), [])

    def test_un_numero_mayor_que_cero_vuelve_a_poner_el_tope(self):
        self.ajustes(fanout_cero_tope_dia=3)
        _skus, filas, planes = _varios(self)
        _barrer(filas, planes)
        self.assertEqual(len(self.ventas()), 3)

    def test_el_intento_se_sigue_sellando_antes_y_sin_sello_no_se_llama(self):
        _skus, filas, planes = _varios(self, n=3)
        _barrer(filas, planes)
        self.assertEqual(len(self.intentos("apagar")), 3)
        self.llamadas.clear()
        self.bitacora_caida = True
        with mock.patch.object(S, "_cortado", return_value=False):
            self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.ventas(), [], "sin bitácora no se llama al canal, con o sin tope")

    def test_el_estado_dice_que_no_hay_tope(self):
        with mock.patch.object(S, "_ultimas", return_value=[]), \
                mock.patch.object(S, "usadas_hoy", return_value=0), \
                mock.patch.object(S, "candidatos", return_value=[]), \
                mock.patch.object(S, "huerfanas", return_value=[]):
            self.assertEqual(S.resumen_panel()["tope_dia"], 0)
            topes = S.estado()["topes"]
        self.assertEqual((topes["vuelta"], topes["hora_fuera_de_censo"], topes["dia"],
                          topes["reactivar_dia"]), (0, 0, 0, 0))


class SoloInactivaNuncaBorra(Arnes):
    """Lo que pidió probar Brandon el 9-oct-2026: el seguro INACTIVA la publicación
    —queda en el canal, apagada— y nunca la borra. Las únicas dos llamadas que mueven
    algo son `bg.local.goods.sale.status.set {onsale: 0|1}` (Temu) y
    `/product/202309/products/deactivate|activate` (TikTok)."""

    def test_al_caer_a_cero_solo_cambia_el_estado_de_venta(self):
        self.aplicar(plan(accion("temu", 4), accion("tiktok", 3)))
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None), ("tiktok.desactivar", P)])
        # La publicación sigue existiendo en el canal, con su estado de «apagada a mano».
        self.assertEqual(self.canal[("temu", G)]["estado"], "3/2")
        self.assertEqual(self.canal[("tiktok", P)]["estado"], "SELLER_DEACTIVATED")
        self.assertEqual({(c, e) for c, e in S._APAGADO_A_MANO.items()},
                         {("temu", "3/2"), ("tiktok", "SELLER_DEACTIVATED")})

    def test_el_resultado_se_confirma_releyendo_la_publicacion(self):
        # «ok (2/8→3/2)» sólo se sella si la publicación SE VUELVE A LEER en el canal y
        # está apagada: una que hubiera desaparecido no se puede releer y no da «ok».
        self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.de(S.ACC_INACTIVAR)[0]["resultado"], "ok (2/8→3/2)")
        self.assertGreaterEqual(self.lecturas.count(("temu", G)), 2, "antes y después de llamar")

    def test_si_tras_apagar_ya_no_se_puede_leer_no_se_da_por_hecho(self):
        original = self.canal[("temu", G)]

        async def cambiar_venta(goods_id, en_venta, sku_ids=None):
            self.llamadas.append(("temu.venta", str(goods_id), en_venta, sku_ids))
            self.ilegibles.add(("temu", str(goods_id)))        # como si ya no existiera
            return {"success": True}

        with mock.patch.object(TM, "cambiar_venta", side_effect=cambiar_venta):
            self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.de(S.ACC_INACTIVAR), [], "sin relectura no hay «ok» ni marca")
        self.assertTrue(self.de(S.ACC_ERROR)[0]["resultado"].startswith(S.ERR_NO_CONVERGE))
        self.assertIs(self.canal[("temu", G)], original)

    def test_las_llamadas_reales_son_las_de_estado_de_venta(self):
        temu, tiktok = [], []

        async def llamar_temu(tipo, datos=None, timeout=40.0):
            temu.append((tipo, datos))
            return {"success": True}

        async def llamar_tiktok(ruta, token, params=None, cuerpo=None, metodo="GET"):
            tiktok.append((ruta, cuerpo, metodo))
            return {"errors": []}

        with mock.patch.object(TM, "cambiar_venta", side_effect=_REAL["temu.cambiar_venta"]), \
                mock.patch.object(TK, "desactivar", side_effect=_REAL["tiktok.desactivar"]), \
                mock.patch.object(TK, "activar", side_effect=_REAL["tiktok.activar"]), \
                mock.patch.object(TM, "llamar", side_effect=llamar_temu), \
                mock.patch.object(TK, "llamar", side_effect=llamar_tiktok):
            S._apagar("temu", G)
            S._apagar("tiktok", P)
            S._prender("temu", G)
            S._prender("tiktok", P)
        self.assertEqual(temu, [
            ("bg.local.goods.sale.status.set", {"goodsId": int(G), "onsale": 0}),
            ("bg.local.goods.sale.status.set", {"goodsId": int(G), "onsale": 1})])
        self.assertEqual(tiktok, [
            ("/product/202309/products/deactivate", {"product_ids": [P]}, "POST"),
            ("/product/202309/products/activate", {"product_ids": [P]}, "POST")])

    def test_el_modulo_no_conoce_ninguna_llamada_de_borrado(self):
        import inspect
        import re
        prohibido = re.compile(r"goods\.delete|products/delete|/delete\b|[\"']DELETE[\"']|\.delete\(|"
                               r"recycle|bg\.local\.goods\.(?!list\.query|sale\.status\.set)[a-z.]+",
                               re.IGNORECASE)
        fuente = inspect.getsource(S)
        self.assertEqual(prohibido.findall(fuente), [])
        # Y de los dos clientes sólo usa estas cinco funciones (más leer la configuración).
        usados = set(re.findall(r"\b(?:tm|tk)\.([a-zA-Z_]+)", fuente))
        self.assertEqual(usados, {"disponible", "llamar", "cambiar_venta", "CUBETAS",
                                  "access_token", "cipher", "desactivar", "activar"})
        # `llamar` sólo para LEER: el listado de Temu y el producto de TikTok.
        self.assertEqual(set(re.findall(r"tm\.llamar\(\s*\"([^\"]+)\"", fuente)),
                         {"bg.local.goods.list.query"})
        self.assertEqual(re.findall(r"tk\.llamar\(\s*f?\"([^\"]+)\"", fuente),
                         ["/product/202309/products/{item_id}"])


class Reconciliar(Arnes):
    """Lo que quedó A MEDIAS: un reinicio entre llamar al canal y sellar ya no deja
    una publicación apagada sin rastro."""

    def _pend(self, accion=None, resultado=None, intento="apagar (vivo 2/8)"):
        return {"canal": "temu", "item_id": G, "sku": SKU, "cuenta": "TEMU",
                "accion": accion or S.ACC_INTENTO, "resultado": resultado or intento, "intento": intento}

    def test_un_reinicio_a_media_vuelta_no_deja_la_publicacion_apagada_sin_rastro(self):
        self.canal[("temu", G)]["estado"] = "3/2"      # Temu sí la apagó; el contenedor murió antes de sellar
        self.pend = [self._pend()]
        r = _barrer()
        (m,) = self.de(S.ACC_INACTIVAR)
        self.assertEqual((m["resultado"], m["dry_run"]), ("ok (2/8→3/2) · confirmada tarde", False))
        self.assertEqual(S.leer_marca(m["resultado"]), {"de": "2/8", "a": "3/2", "variante": None})
        self.assertIsNotNone(self._marca("temu", G), "ya tiene marca: el seguro la puede regresar")
        self.assertEqual(self.ventas(), [], "reconciliar sólo lee")
        self.assertEqual(r["confirmadas_tarde"], 1)
        self.assertEqual(self.reflejos[-1], ("temu", SKU, G, "3/2"))
        self.assertIn("seguro_cero_apago:temu", [a[0] for a in self.avisos])
        # Y con esa marca, cuando vuelve el stock, la regresa.
        self.pend = []
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertIn(("temu.venta", G, True, None), self.llamadas)

    def test_el_proceso_muere_justo_despues_de_llamar_al_canal(self):
        # Un despliegue a media vuelta: el hilo del fan-out muere en la espera que
        # sigue a la llamada. Antes: Temu en 3/2, 0 filas, 0 avisos, y nadie la regresaba.
        with mock.patch.object(S, "_dormir", side_effect=KeyboardInterrupt),                 self.assertRaises(KeyboardInterrupt):
            self.aplicar(plan(accion("temu", 4)))
        self.assertEqual(self.canal[("temu", G)]["estado"], "3/2", "Temu sí la apagó")
        (i,) = self.filas
        self.assertEqual((i["accion"], i["resultado"]), (S.ACC_INTENTO, "apagar (vivo 2/8)"),
                         "lo único que quedó es el intento, sellado ANTES de llamar")
        self.pend = [self._pend(intento=i["resultado"])]        # lo que `pendientes` devuelve 3 min después
        _barrer()
        self.assertEqual(self._marca("temu", G)["resultado"], "ok (2/8→3/2) · confirmada tarde")
        self.assertEqual(len(self.ventas()), 1, "sin volver a llamar al canal")

    def test_un_intento_que_no_cambio_nada_se_cierra_como_error(self):
        self.pend = [self._pend()]                     # sigue en 2/8
        r = _barrer()
        (e,) = self.de(S.ACC_ERROR)
        self.assertTrue(e["resultado"].startswith(S.ERR_SIN_CIERRE))
        self.assertEqual(self.de(S.ACC_INACTIVAR), [])
        self.assertEqual(r["sin_cierre"], 1)

    def test_un_no_convergio_que_ya_se_ve_se_adopta(self):
        self.canal[("temu", G)]["estado"] = "3/2"
        self.pend = [self._pend(S.ACC_ERROR, S.ERR_NO_CONVERGE + ": tras apagar sigue en 2/8")]
        _barrer()
        self.assertEqual(self.de(S.ACC_INACTIVAR)[0]["resultado"], "ok (2/8→3/2) · confirmada tarde")
        self.assertIsNotNone(self._marca("temu", G))

    def test_lo_que_temu_agoto_sola_no_se_da_por_apagado_por_el_seguro(self):
        # Con el 0 ya escrito Temu la pasa sola a 3/1: verla ahí no prueba nada.
        self.canal[("temu", G)]["estado"] = "3/1"
        self.pend = [self._pend()]                     # intento sin cierre
        _barrer()
        self.assertEqual(self.de(S.ACC_INACTIVAR), [], "no se inventa una marca")
        self.assertTrue(self.de(S.ACC_ERROR)[0]["resultado"].startswith(S.ERR_SIN_CIERRE))
        self.filas.clear()
        self.pend = [self._pend(S.ACC_ERROR, S.ERR_NO_CONVERGE + ": tras apagar sigue en 2/8")]
        _barrer()
        self.assertEqual(self.filas, [])

    def test_un_no_convergio_que_sigue_igual_no_anota_nada(self):
        self.pend = [self._pend(S.ACC_ERROR, S.ERR_NO_CONVERGE + ": tras apagar sigue en 2/8")]
        _barrer()
        self.assertEqual(self.filas, [], "lo reintenta el barrido con sus reglas; no se le suma otro error")

    def test_un_prender_confirmado_tarde_cierra_la_marca(self):
        self.sembrar_marca()
        self.canal[("temu", G)]["estado"] = "2/8"
        self.pend = [self._pend(intento="prender (vivo 3/2)")]
        _barrer()
        (f,) = self.de(S.ACC_REACTIVAR)
        self.assertEqual(f["resultado"], "ok (3/2→2/8) · confirmada tarde")
        self.assertIsNone(self._marca("temu", G))
        self.assertIn("seguro_cero_prendio:temu", [a[0] for a in self.avisos])

    def test_una_lectura_ilegible_se_deja_para_el_siguiente_censo(self):
        self.ilegibles.add(("temu", G))
        self.pend = [self._pend()]
        r = _barrer()
        self.assertEqual(self.filas, [])
        self.assertEqual(r["reconciliar_ilegible"], 1)

    def test_si_la_bitacora_no_contesta_el_barrido_sigue(self):
        with mock.patch.object(S, "pendientes", side_effect=RuntimeError("kubera no contesta")):
            r = _barrer([Barrido.FILA], {SKU: plan(accion("temu", 2))})
        self.assertEqual(r["error"], 1)
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)], "apagar lo que toca no depende de eso")

    def test_la_marca_que_no_entro_se_recupera_en_el_siguiente_censo(self):
        def solo_la_marca(fila):
            if fila["accion"] == S.ACC_INACTIVAR and not fila["resultado"].endswith("tarde"):
                raise RuntimeError("kubera caída")
            self.filas.append(dict(fila))
            return 1
        with mock.patch.object(fanout_read, "registrar", side_effect=solo_la_marca):
            self.aplicar(plan(accion("temu", 4)))          # apagó; la marca no entró; el intento sí
            self.assertIsNone(self._marca("temu", G))
            (i,) = self.intentos("apagar")
            self.pend = [self._pend(intento=i["resultado"])]
            _barrer()
        self.assertEqual(self._marca("temu", G)["resultado"], "ok (2/8→3/2) · confirmada tarde")


class ApagadasConStock(Arnes):
    """Lo que el seguro saca de la venta NO se queda fuera en silencio cuando vuelve
    el stock. El seguro reactiva solo; donde no puede (en ensayo, o si algo se lo
    impide por horas) lo dice."""

    def test_en_ensayo_no_reactiva_y_el_barrido_lo_dice_una_vez_al_dia(self):
        self.sembrar_marca()                                # el seguro la apagó: 3/2
        self.en_ensayo()                                    # el fan-out dejó de escribirle de verdad
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=15))     # vuelve el stock
        self.assertEqual(self.ventas(), [])
        (ens,) = self.de(S.ACC_REACTIVAR)
        self.assertTrue(ens["dry_run"] and ens["resultado"].startswith("ENSAYO (prendería"))
        self.foto[SKU] = 15
        r = _barrer()
        (f,) = self.de(S.ACC_SIN_CAMBIO)
        self.assertTrue(f["resultado"].startswith(S.CON_STOCK + ": Woo ya tiene 15"))
        self.assertIn("corre en ensayo", f["resultado"])
        self.assertIn("reactivarla a mano", f["resultado"])
        self.assertFalse(f["dry_run"])
        (a,) = [x for x in self.avisos if x[0] == "seguro_cero_con_stock:temu"]
        self.assertIn(f"{SKU} (Woo 15)", a[1])
        self.assertEqual(r["con_stock"], [SKU])
        self.assertEqual(self.encolados, [], "en ensayo no se devuelve a la cola")
        # El siguiente censo del mismo día no repite ni la fila ni el aviso…
        self.avisos.clear()
        _barrer()
        self.assertEqual(len(self.de(S.ACC_SIN_CAMBIO)), 1)
        self.assertEqual(self.avisos, [])
        # …y al día siguiente, si sigue igual, lo vuelve a decir.
        self.t[0] += 86400 + 60
        _barrer()
        self.assertEqual(len(self.de(S.ACC_SIN_CAMBIO)), 2)
        self.assertEqual([a[0] for a in self.avisos], ["seguro_cero_con_stock:temu"])

    def test_si_ya_vende_o_woo_sigue_en_cero_no_dice_nada(self):
        self.sembrar_marca()
        self.en_ensayo()
        self.foto[SKU] = 0
        _barrer()
        self.foto[SKU] = 9
        self.censo[("temu", G)] = "2/8"                     # alguien ya la prendió
        _barrer()
        del self.foto[SKU]                                  # Woo ilegible
        self.censo[("temu", G)] = "3/2"
        _barrer()
        self.assertEqual((self.de(S.ACC_SIN_CAMBIO), self.avisos), ([], []))

    def test_reactivando_solo_avisa_de_la_que_lleva_horas(self):
        self.sembrar_marca()
        self.foto[SKU] = 9
        self.edad = 1800.0                                  # media hora con stock: el seguro va en camino
        _barrer()
        self.assertEqual(self.de(S.ACC_SIN_CAMBIO), [])
        self.edad = 7 * 3600.0                              # 7 h con stock y sigue apagada: algo se lo impide
        _barrer()
        (f,) = self.de(S.ACC_SIN_CAMBIO)
        self.assertIn("lleva 7 h con stock", f["resultado"])
        self.assertIn("seguro_cero_con_stock:temu", [a[0] for a in self.avisos])

    def test_el_pie_y_la_ruta_de_estado_las_listan(self):
        self.sembrar_marca()
        self.foto[SKU] = 15
        r = S.resumen_panel()
        self.assertEqual([(m["canal"], m["sku"], m["woo"]) for m in r["con_stock"]], [("temu", SKU, 15)])
        self.assertEqual((r["canales"]["temu"]["con_stock"], r["canales"]["tiktok"]["con_stock"]), (1, 0))
        e = S.estado()
        self.assertEqual([(m["sku"], m["woo"], m["fuera"]) for m in e["con_stock"]], [(SKU, 15, True)])
        self.foto[SKU] = 0
        self.assertEqual(S.resumen_panel()["con_stock"], [])


class ReactivarVerifica(Arnes):
    """Antes de dar una reactivación por buena se comprueba que QUEDÓ a la venta."""

    def test_temu_que_no_queda_a_la_venta_no_se_da_por_reactivada(self):
        self.sembrar_marca()
        self.al_prender_temu = "3/1"            # obedece a medias: sale de 3/2 y cae en «agotada»
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertIn(("temu.venta", G, True, None), self.llamadas)
        self.assertEqual(self.de(S.ACC_REACTIVAR), [], "no se anota como reactivada")
        self.assertEqual(self.de(S.ACC_SOLTAR), [])
        (e,) = self.de(S.ACC_ERROR)
        self.assertIn("quedó en 3/1, NO a la venta", e["resultado"])
        self.assertIsNotNone(self._marca("temu", G), "la marca NO se cierra: sigue siendo del seguro")
        self.assertEqual(self.reflejos, [], "ese estado no se firma como dejado por el seguro")
        tipos = [a[0] for a in self.avisos]
        self.assertIn(f"seguro_cero_no_quedo:temu:{SKU}", tipos)
        self.assertNotIn("seguro_cero_prendio:temu", tipos, "no se anuncia «regresó a la venta»")
        # Sigue en la lista de «apagadas por el seguro con stock de vuelta»…
        self.foto[SKU] = 5
        self.assertEqual([m["sku"] for m in S.resumen_panel()["con_stock"]], [SKU])
        # …no se le vuelve a pedir desde ese estado…
        self.llamadas.clear()
        self.aplicar(plan(accion("temu", 5, accion="sin_cambio", estado="3/1"), objetivo=5))
        self.assertEqual(self.ventas(), [])
        # …y si Temu sólo iba tarde, el siguiente censo la ve a la venta y cierra la marca.
        self.canal[("temu", G)]["estado"] = "2/8"
        self.pend = [{"canal": "temu", "item_id": G, "sku": SKU, "cuenta": "TEMU", "accion": S.ACC_ERROR,
                      "resultado": e["resultado"], "intento": "prender (vivo 3/2)"}]
        _barrer()
        self.assertEqual(self.de(S.ACC_REACTIVAR)[0]["resultado"], "ok (3/2→2/8) · confirmada tarde")
        self.assertIsNone(self._marca("temu", G))
        self.assertIn("seguro_cero_prendio:temu", [a[0] for a in self.avisos])

    def test_el_error_de_no_quedo_cierra_su_intento(self):
        # Si no, el siguiente censo lo tomaría por «el proceso se cortó a media vuelta».
        self.sembrar_marca()
        self.al_prender_temu = "3/3"
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertTrue(self.de(S.ACC_ERROR)[0]["resultado"].startswith(S.ERR_NO_CONVERGE + ":"))

    def test_las_soltadas_que_siguen_fuera_de_la_venta_se_listan(self):
        self.sembrar_marca()
        self.canal[("temu", G)]["estado"] = "4/7"       # un estado que no dejó el seguro ni pone Temu sola
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertEqual(len(self.de(S.ACC_SOLTAR)), 1)
        self.foto[SKU] = 5
        self.assertEqual([x["sku"] for x in S.soltadas()], [SKU])
        self.assertEqual([(x["sku"], x["woo"]) for x in S.resumen_panel()["soltadas"]], [(SKU, 5)])
        self.assertEqual([x["sku"] for x in S.estado()["soltadas"]], [SKU])
        self.canal[("temu", G)]["estado"] = "2/8"       # alguien la prendió: ya no es tarea
        self.assertEqual(S.soltadas(), [])

    def test_temu_que_si_queda_a_la_venta_se_anuncia(self):
        self.sembrar_marca()
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertEqual(self.de(S.ACC_REACTIVAR)[0]["resultado"], "ok (3/2→2/8)")
        self.assertIn("seguro_cero_prendio:temu", [a[0] for a in self.avisos])

    def test_tiktok_en_auditoria_no_se_anuncia_como_de_vuelta(self):
        self.sembrar_marca(canal="tiktok", resultado="ok (ACTIVATE→SELLER_DEACTIVATED)",
                           estado_vivo="SELLER_DEACTIVATED")
        self.aplicar(plan(accion("tiktok", 0, estado="SELLER_DEACTIVATED"), objetivo=5))
        self.assertEqual(self.de(S.ACC_REACTIVAR)[0]["resultado"], "ok (SELLER_DEACTIVATED→PENDING)")
        tipos = [a[0] for a in self.avisos]
        self.assertIn("seguro_cero_pidio:tiktok", tipos)
        self.assertNotIn("seguro_cero_prendio:tiktok", tipos)
        (pidio,) = [a for a in self.avisos if a[0] == "seguro_cero_pidio:tiktok"]
        self.assertIn("todavía NO venden", pidio[1])

    def _auditoria(self, status, horas):
        return {"sku": SKU, "cuenta": "KUBERA", "item_id": P, "status": status, "stock_own": 5,
                "resultado": "ok (SELLER_DEACTIVATED→PENDING)", "edad_s": horas * 3600.0}

    def test_si_la_auditoria_de_tiktok_la_rechaza_avisa_en_rojo(self):
        self.auditoria = [self._auditoria("FAILED", 2)]
        _barrer(canal="tiktok")
        (e,) = self.de(S.ACC_ERROR)
        self.assertTrue(e["resultado"].startswith(S.ERR_AUDITORIA))
        self.assertIn("FAILED", e["resultado"])
        (a,) = [x for x in self.avisos if x[0] == f"seguro_cero_auditoria:tiktok:{SKU}"]
        self.assertEqual(a[2], "🔴")
        self.avisos.clear()
        _barrer(canal="tiktok")                  # el mismo día no lo repite
        self.assertEqual((len(self.de(S.ACC_ERROR)), self.avisos), (1, []))
        self.assertEqual(self.ventas(), [], "sólo avisa: no la vuelve a tocar")

    def test_aprobada_o_todavia_en_plazo_no_dice_nada(self):
        self.auditoria = [self._auditoria("ACTIVATE", 2), self._auditoria("PENDING", 3)]
        _barrer(canal="tiktok")
        self.assertEqual((self.filas, self.avisos), ([], []))
        self.auditoria = [self._auditoria("PENDING", 30)]
        _barrer(canal="tiktok")
        self.assertIn("lleva 30 h en auditoría", self.de(S.ACC_ERROR)[0]["resultado"])

    def test_la_auditoria_solo_se_revisa_en_tiktok(self):
        self.auditoria = [self._auditoria("FAILED", 2)]
        _barrer(canal="temu")
        self.assertEqual(self.filas, [])


class AvisosQueNoSePierden(Arnes):
    """`alertas.avisar` deja pasar UN aviso por tipo cada 60 minutos. El segundo SKU
    de la hora no salía nunca."""

    def test_el_segundo_apagado_de_la_hora_viaja_en_el_siguiente_aviso(self):
        skus, _filas, planes = _varios(self, 3)
        self.aplicar(planes[skus[0]])
        self.suprimir.add("seguro_cero_apago:temu")        # el candado: ya salió uno esta hora
        self.aplicar(planes[skus[1]])
        self.aplicar(planes[skus[2]])
        self.assertEqual(len([a for a in self.avisos if a[0] == "seguro_cero_apago:temu"]), 1)
        self.suprimir.clear()                               # pasó la hora
        _barrer()                                           # el barrido empuja lo que faltaba por decir
        ultimo = [a for a in self.avisos if a[0] == "seguro_cero_apago:temu"][-1]
        self.assertIn("apagó 2 en Temu", ultimo[1])
        self.assertIn(skus[1], ultimo[1])
        self.assertIn(skus[2], ultimo[1])
        self.assertNotIn(skus[0], ultimo[1], "el que ya salió no se repite")
        self.avisos.clear()
        _barrer()
        self.assertEqual(self.avisos, [], "y ya dicho, no se vuelve a decir")

    def test_cada_soltada_avisa_con_su_propio_tipo(self):
        skus, filas, _planes = _varios(self, 2)
        for s, f in zip(skus, filas):
            self.sembrar_marca(item=f["listing_id"], sku=s, estado_vivo="4/7")
            self.aplicar(plan(accion("temu", 0, item=f["listing_id"], estado="4/7"), objetivo=5, sku=s))
        self.assertEqual(sorted(a[0] for a in self.avisos if a[0].startswith("seguro_cero_solto")),
                         sorted(f"seguro_cero_solto:temu:{s}" for s in skus))

    def test_lo_pendiente_no_crece_sin_fin(self):
        self.suprimir.add("seguro_cero_apago:temu")
        S._emitir_avisos({"apago": [("temu", f"SKU-{i}") for i in range(S._AVISOS_MAX + 50)]})
        self.assertEqual(len(S._avisos_pend[("apago", "temu")]), S._AVISOS_MAX)


class AvisosConElCandadoReal(unittest.TestCase):
    """Con el `alertas.avisar` DE VERDAD —su candado de 60 min por tipo— y Slack
    suplantado. El resto de la suite suplanta `avisar`, y así no se veía el hueco."""

    def setUp(self):
        self.enviados: list[str] = []
        self.t = [2_000_000.0]
        prueba = self

        class EnElActo:                  # en vez de un hilo: el envío corre ya (y no manda nada)
            def __init__(self, target=None, args=(), daemon=None, **k):
                self.f, self.a = target, args

            def start(self):
                self.f(*self.a)

        for p in (mock.patch.object(alertas, "disponible", return_value=True),
                  mock.patch.object(alertas, "_persistente", return_value=False),
                  mock.patch.object(alertas, "_post_slack",
                                    side_effect=lambda texto, url: prueba.enviados.append(texto)),
                  mock.patch.object(alertas, "_webhook_de", return_value="x"),
                  mock.patch.object(alertas.threading, "Thread", EnElActo),
                  mock.patch.object(alertas.time, "time", side_effect=lambda: prueba.t[0]),
                  mock.patch.object(S, "_ahora", side_effect=lambda: prueba.t[0])):
            p.start()
            self.addCleanup(p.stop)
        for d in (alertas._ultimo_envio, alertas._suprimidas, S._avisos_pend, S._avisos_dia):
            d.clear()

    def _evento(self, clave, *dato):
        avisos = S._avisos_nuevos()
        avisos[clave].append(dato)
        S._emitir_avisos(avisos)         # así cierra CADA evento del fan-out

    def test_tres_apagados_en_una_hora_salen_los_tres(self):
        for sku in ("AAA-0001", "BBB-0002", "CCC-0003"):
            self._evento("apago", "temu", sku)
        self.assertEqual(len(self.enviados), 1, "el candado deja pasar uno por hora")
        self.assertIn("AAA-0001", self.enviados[0])
        self.t[0] += 61 * 60
        S._emitir_avisos(S._avisos_nuevos(), vaciar=True)       # el barrido del siguiente censo
        self.assertEqual(len(self.enviados), 2)
        self.assertIn("apagó 2 en Temu", self.enviados[1])
        self.assertIn("BBB-0002", self.enviados[1])
        self.assertIn("CCC-0003", self.enviados[1])

    def test_lo_mismo_con_las_que_regresan_y_con_las_que_ya_tienen_stock(self):
        for clave in ("prendio", "pidio", "con_stock"):
            self._evento(clave, "tiktok", "AAA-0001")
            self._evento(clave, "tiktok", "BBB-0002")
        self.assertEqual(len(self.enviados), 3)
        self.t[0] += 61 * 60
        S._emitir_avisos(S._avisos_nuevos(), vaciar=True)
        self.assertEqual(len([t for t in self.enviados if "BBB-0002" in t]), 3, "ningún SKU se pierde")

    def test_tres_soltadas_en_una_hora_salen_las_tres(self):
        for sku in ("AAA-0001", "BBB-0002", "CCC-0003"):
            self._evento("solto", "temu", sku, "la encontró en 4/7 y el seguro la había dejado en 3/2")
        self.assertEqual(len(self.enviados), 3)
        self.assertTrue(all(s in " ".join(self.enviados) for s in ("AAA-0001", "BBB-0002", "CCC-0003")))
        self.assertEqual(alertas._suprimidas, {})

    def test_el_tope_avisa_una_vez_aunque_se_alcance_veinte_veces(self):
        for _ in range(20):
            self._evento("tope", "temu", S.ACC_INACTIVAR, 5)
        self.assertEqual(len(self.enviados), 1)
        self.assertTrue(self.enviados[0].startswith("🔴"))


class MarcasDeTemu(Arnes):
    """Lo que Temu mueve por su cuenta (o lo que el censo ve del propio seguro) no
    suelta la marca: una vez suelta, el seguro ya no la regresa."""

    def test_lo_que_mueve_temu_sola_no_suelta_la_marca(self):
        self.sembrar_marca()
        self.canal[("temu", G)]["estado"] = "3/3"          # Temu la pasó de 3/2 a 3/3
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertEqual(self.ventas(), [], "desde 3/3 no se le pide regresar")
        self.assertEqual(self.de(S.ACC_SOLTAR), [])
        self.assertIsNotNone(self._marca("temu", G), "la marca se conserva")
        (f,) = self.de(S.ACC_SIN_CAMBIO)
        self.assertTrue(f["resultado"].startswith(S.MOVIDA + ": Temu la pasó sola a 3/3"))
        self.assertEqual([a for a in self.avisos if "solto" in a[0]], [])
        # Sigue siendo del seguro: con stock, sale en «apagadas con stock de vuelta».
        self.foto[SKU] = 5
        self.assertEqual([m["sku"] for m in S.con_stock(S.apagadas())], [SKU])
        # Y cuando Temu la regresa SOLA a la venta, la marca se cierra sin dejar tarea.
        self.canal[("temu", G)]["estado"] = "2/8"
        self.aplicar(plan(accion("temu", 5, accion="sin_cambio", estado="2/8"), objetivo=5))
        self.assertEqual(self.de(S.ACC_SOLTAR)[0]["resultado"], "ok (la prendió otro)")
        self.assertEqual(self.ventas(), [])
        self.assertEqual([a for a in self.avisos if "solto" in a[0]], [])

    def test_los_estados_de_la_plataforma_no_cuentan_como_cambio_ajeno(self):
        self.sembrar_marca()
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertEqual(self.ajeno_args[-1], ("temu", SKU, ["3/2", "3/1", "3/3"]))
        self.assertEqual(S._propios("tiktok", "SELLER_DEACTIVATED"), ["SELLER_DEACTIVATED"])

    def test_un_cambio_ajeno_que_la_dejo_a_la_venta_y_sigue_ahi_no_deja_tarea(self):
        self.sembrar_marca()
        self.canal[("temu", G)]["estado"] = "2/8"          # Temu la regresó sola de 3/3
        self.ajeno = {"valor_anterior": "3/3", "valor_nuevo": "2/8", "via": "temu_censo"}
        self.aplicar(plan(accion("temu", 5, accion="sin_cambio", estado="2/8"), objetivo=5))
        self.assertIn("estado movido por temu_censo", self.de(S.ACC_SOLTAR)[0]["resultado"])
        self.assertEqual((self.ventas(), self.avisos), ([], []))

    def test_un_apagado_en_bloque_posterior_suelta_en_vez_de_regresar(self):
        # 21-ago: 306 publicaciones de Temu apagadas a mano en un minuto. Lo que el
        # seguro ya tenía apagado no cambia de estado: el rastro está en las demás.
        self.sembrar_marca()
        self.bloque = {"n": 306, "hora": "2026-08-21 23:00"}
        self.aplicar(plan(accion("temu", 0, estado="3/2"), objetivo=5))
        self.assertEqual(self.ventas(), [], "regresarla iría contra la pausa")
        self.assertIn("apagado en bloque: 306", self.de(S.ACC_SOLTAR)[0]["resultado"])
        self.assertIn(f"seguro_cero_solto:temu:{SKU}", [a[0] for a in self.avisos])


class MarcasHuerfanas(Arnes):
    """Si el censo cambia la fila del SKU a OTRA publicación, la marca de la que se
    apagó queda huérfana: no se reencola (el stock iría a la otra) y se avisa."""

    def _apagada_en_tiktok(self):
        self.sembrar_marca(canal="tiktok", resultado="ok (ACTIVATE→SELLER_DEACTIVATED)",
                           estado_vivo="SELLER_DEACTIVATED")
        self.foto[SKU] = 8

    def test_la_huerfana_no_se_reencola_y_avisa_una_vez_al_dia(self):
        self._apagada_en_tiktok()
        self.listing[("tiktok", SKU)] = "770999"            # el censo ahora apunta a un borrador viejo
        for _ in range(3):
            _barrer(canal="tiktok")
        self.assertEqual(self.encolados, [], "antes: 3 barridos, 3 reencolados, sin fin")
        avisos = [a for a in self.avisos if a[0] == f"seguro_cero_huerfana:tiktok:{SKU}"]
        self.assertEqual(len(avisos), 1)
        self.assertIn("770999", avisos[0][1])
        self.assertEqual(self.de(S.ACC_SIN_CAMBIO), [], "ni se cuenta como «apagada con stock»: no es esa publicación")

    def test_una_misma_marca_se_reencola_a_lo_mucho_una_vez_al_dia(self):
        self._apagada_en_tiktok()
        for _ in range(3):
            _barrer(canal="tiktok")
        self.assertEqual(self.encolados, [(SKU, S.MOTIVO_MARCA)])
        self.t[0] += S._REENCOLAR_S + 1
        _barrer(canal="tiktok")
        self.assertEqual(len(self.encolados), 2)

    def test_en_ensayo_el_barrido_no_devuelve_nada_a_la_cola(self):
        self.en_ensayo()
        self._apagada_en_tiktok()
        _barrer(canal="tiktok")
        self.assertEqual(self.encolados, [])


class SoltarConElFanoutApagado(Arnes):
    def test_con_el_fanout_apagado_no_va_a_la_base_ni_anota(self):
        self.ajustes(fanout_enabled=False)
        self.sembrar_marca()
        antes = len(self.filas)
        with mock.patch.object(S, "marcas_vigentes", side_effect=AssertionError("fue a la base")):
            r = S.soltar(SKU, "temu")
        self.assertEqual(r, {"ok": False, "motivo": S.APAGADO})
        self.assertEqual(len(self.filas), antes)

    def test_sin_marca_ni_corte_no_hay_nada_que_soltar(self):
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", return_value=[{"listing_id": G, "cuenta": "TEMU"}]):
            r = S.soltar(SKU, "temu")
        self.assertFalse(r["ok"])
        self.assertIn("no hay nada que soltar", r["motivo"])
        self.assertEqual(self.filas, [], "antes sellaba una fila por cada publicación del SKU")

    def test_dice_por_que_solto_cada_una(self):
        self.sembrar_marca()
        from services import supabase_db as sdb
        with mock.patch.object(sdb, "fetch_all", return_value=[{"listing_id": G, "cuenta": "TEMU"}]):
            r = S.soltar(SKU, "temu")
        self.assertEqual(r["soltadas"], [{"item_id": G, "por": "marca vigente", "sellada": True}])
        self.assertEqual(S.soltadas(), [], "soltada A MANO: no es tarea pendiente")


class PrefiltroDeTemu(Arnes):
    def test_los_estados_que_pueden_volver_solos_dejan_una_fila_al_dia_y_ni_una_lectura(self):
        for estado in ("3/1", "3/3", "2/4"):
            with self.subTest(estado=estado):
                self.filas.clear()
                S._vistas.clear()
                p = plan(accion("temu", 0, accion="sin_cambio", estado=estado))
                self.aplicar(p)
                self.aplicar(p)
                (f,) = self.de(S.ACC_OMITIR)
                self.assertTrue(f["resultado"].startswith(f"Temu {estado} "))
                self.assertIn("sin sondear", f["resultado"])
        self.assertEqual((self.lecturas, self.ventas()), ([], []))

    def test_si_el_censo_no_sabe_y_en_vivo_esta_en_3_3_tambien_se_anota(self):
        self.canal[("temu", G)]["estado"] = "3/3"
        self.aplicar(plan(accion("temu", 0, accion="sin_cambio", estado="")))
        self.assertIn("Temu 3/3", self.de(S.ACC_OMITIR)[0]["resultado"])
        self.assertEqual(self.ventas(), [])


class ExcedentesSellan(Arnes):
    def test_la_marca_se_sella_aunque_el_escritor_conteste_que_no_se_baja(self):
        self.escritor_ok = (True, F.NO_BAJA + ": el canal ya bajó solo")
        with mock.patch.object(F, "plan", return_value=plan(accion("temu", 4))):
            r = F.bajar(SKU, "temu", "excedente:temu")
        self.assertEqual(r["resultado"], "sin_cambio")
        self.assertEqual(self.ventas(), [("temu.venta", G, False, None)])
        self.assertEqual(len(self.de(S.ACC_INACTIVAR)), 1, "sin evento del fan-out, las filas se sellan igual")
        self.assertIsNotNone(self._marca("temu", G))


class IdenticoApagado(unittest.TestCase):
    """Con el fan-out apagado (y con él el seguro) nada cambia respecto a antes de que
    el seguro existiera: ni una llave de más, ni una importación, ni una pregunta."""

    FILAS = {"ZZZ-0001-AZL": {
        "temu|TEMU": {"canal": "temu", "cuenta": "TEMU", "item_id": G, "stock_real": 3,
                      "es_full": False, "estado_canal": "2/8", "situacion": None},
        "tiktok|KUBERA": {"canal": "tiktok", "cuenta": "KUBERA", "item_id": P, "stock_real": 0,
                          "es_full": False, "estado_canal": "DELETED", "situacion": None},
        "mercado_libre|BEKURA": {"canal": "mercado_libre", "cuenta": "BEKURA", "item_id": "MLM1",
                                 "stock_real": 2, "es_full": True, "estado_canal": None,
                                 "situacion": "active"}}}
    DE_SIEMPRE = {"canal", "cuenta", "item_id", "stock_actual_canal", "omitido_por"}

    def _destinos(self, encendido):
        from services import channel_read
        with mock.patch.object(channel_read, "leer_inventario", return_value=self.FILAS), \
                mock.patch.object(F, "seguro_encendido", return_value=encendido):
            return {d["canal"]: d for d in F._destinos(SKU)}

    def test_apagado_el_destino_no_lleva_ni_una_llave_de_mas(self):
        for d in self._destinos(False).values():
            self.assertEqual(set(d), self.DE_SIEMPRE)

    def test_encendido_lleva_el_estado_del_censo_y_que_esta_fuera(self):
        d = self._destinos(True)
        self.assertEqual({c: set(x) - self.DE_SIEMPRE for c, x in d.items()},
                         {c: {"estado_canal", "fuera"} for c in d})
        self.assertEqual((d["temu"]["estado_canal"], d["temu"]["fuera"]), ("2/8", False))
        self.assertTrue(d["tiktok"]["fuera"], "borrada: el seguro no la mira")
        self.assertTrue(d["mercado_libre"]["fuera"], "FULL: el seguro no la mira")

    def test_el_seguro_no_toca_lo_que_destinos_marca_fuera(self):
        d = self._destinos(True)["tiktok"]
        ctx = S.Contexto(SKU, "prueba", 0, 0)
        with mock.patch.object(F, "seguro_encendido", return_value=True), \
                mock.patch.object(S, "_inactivar", side_effect=AssertionError("tocó un destino descartado")):
            S.antes(ctx, {**d, "objetivo": 0})
        self.assertEqual(ctx.filas, [])

    def test_con_el_fanout_apagado_ni_se_importa_el_modulo(self):
        import os
        import subprocess
        codigo = (
            "import sys\n"
            "from unittest import mock\n"
            "from services import fanout_stock as F\n"
            "a = {'canal': 'temu', 'cuenta': 'TEMU', 'item_id': '1', 'stock_actual_canal': 3,\n"
            "     'objetivo': 0, 'accion': 'escribir', 'omitido_por': None}\n"
            "p = {'sku': 'X', 'ok': True, 'stock_drop': 0, 'reserva': 0, 'objetivo': 0, 'acciones': [a]}\n"
            "with mock.patch.object(F, 'plan', return_value=p), mock.patch.object(F, '_persistir'), \\\n"
            "        mock.patch.object(F, 'dry_run', return_value=False), \\\n"
            "        mock.patch.dict(F._ESCRITORES, {'temu': lambda *a, **k: (True, 'ok')}), \\\n"
            "        mock.patch.dict(F._ESCRITORES_SOLO_BAJAR, {'temu': lambda *a, **k: (True, 'ok')}):\n"
            "    F._aplicar('X', 'prueba')\n"
            "    F.bajar('X', 'temu', 'excedente:temu')\n"
            "print('IMPORTADO' if 'services.fanout_seguro' in sys.modules else 'NO-IMPORTADO')\n")
        # El fan-out apagado EXPLÍCITO (manda sobre cualquier `.env`): el seguro va
        # pegado a él.
        entorno = {**os.environ, "FANOUT_ENABLED": "false"}
        r = subprocess.run([sys.executable, "-c", codigo], cwd=str(BACKEND), env=entorno,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip().splitlines()[-1:], ["NO-IMPORTADO"], r.stderr[-800:])

    def test_apagado_el_worker_no_le_pregunta_nada_al_seguro(self):
        def un_tic():
            with mock.patch.object(S, "esperas_vencidas", return_value=[]) as esperas,                     mock.patch.object(F, "_pendientes", {}),                     mock.patch.object(F.time, "sleep", side_effect=[None, SystemExit]):
                try:
                    F._worker()
                except SystemExit:
                    pass
            return esperas.call_count

        with mock.patch.object(F, "seguro_encendido", return_value=False):
            self.assertEqual(un_tic(), 0)
        with mock.patch.object(F, "seguro_encendido", return_value=True):
            self.assertGreaterEqual(un_tic(), 1, "encendido sí: así vuelven a la cola las esperas vencidas")

    def test_los_censos_preguntan_si_corre_antes_de_importar(self):
        for censo in ("temu_censo", "tiktok_censo"):
            fuente = (BACKEND / "services" / f"{censo}.py").read_text(encoding="utf-8")
            i = fuente.index("from services import fanout_seguro")
            self.assertIn("if fanout_stock.seguro_encendido():", fuente[i - 120:i], censo)


class Consultas(unittest.TestCase):
    """El SQL que hace DURABLES la marca y los topes va suplantado en las demás
    pruebas: aquí se fija su texto y lo que se le pasa (sin tocar la base)."""

    def setUp(self):
        from services import supabase_db as sdb
        self.sdb = sdb

    @staticmethod
    def _plano(sql):
        return " ".join(str(sql).split())

    def test_la_marca_solo_cuenta_filas_reales_y_con_ok(self):
        sql = self._plano(S._SQL_MARCA)
        self.assertIn("accion in ('cero_inactivar','cero_reactivar','cero_soltar')", sql)
        self.assertIn("dry_run = false and resultado like 'ok%%'", sql)
        self.assertIn("order by canal, item_id, ts desc, id desc", sql)
        with mock.patch.object(self.sdb, "fetch_all", return_value=[
                {"accion": S.ACC_REACTIVAR, "resultado": "ok (3/2→2/8)"}]) as fa:
            self.assertIsNone(S.marca("temu", G), "la última fila la cerró: no hay marca")
        self.assertEqual(fa.call_args[0][1], {"canal": "temu", "item": G})
        self.assertIn("and canal = %(canal)s and item_id = %(item)s", self._plano(fa.call_args[0][0]))

    def test_el_tope_cuenta_los_intentos_de_hoy_y_de_ese_canal(self):
        with mock.patch.object(self.sdb, "fetch_one", return_value={"n": 7}) as fo:
            self.assertEqual(S.usadas_hoy("temu", S.ACC_INACTIVAR), 7)
            self.assertEqual(S.usadas_hoy("tiktok", S.ACC_REACTIVAR), 7)
            self.assertEqual(S.usadas_hoy("temu", S.ACC_INACTIVAR, ensayos=True), 7)
        zona = "America/Mexico_City"
        self.assertEqual([c[0][1] for c in fo.call_args_list], [
            {"a": "cero_intento", "d": False, "r": "apagar %", "c": "temu", "z": zona},
            {"a": "cero_intento", "d": False, "r": "prender %", "c": "tiktok", "z": zona},
            {"a": "cero_inactivar", "d": True, "r": "%", "c": "temu", "z": zona}])
        sql = self._plano(fo.call_args[0][0])
        self.assertIn("accion = %(a)s and canal = %(c)s and dry_run = %(d)s", sql)
        self.assertIn("resultado like %(r)s", sql)
        self.assertIn("ts >= ((now() at time zone %(z)s)::date)::timestamp at time zone %(z)s", sql)

    def test_el_corte_mira_los_tres_ultimos_de_24_horas(self):
        sql = self._plano(S._SQL_CORTADO)
        self.assertIn("canal = %(c)s and item_id = %(i)s and dry_run = false", sql)
        self.assertIn("ts > now() - interval '24 hours'", sql)
        self.assertIn("order by ts desc, id desc limit 3", sql)
        err, ok = {"accion": S.ACC_ERROR}, {"accion": S.ACC_INACTIVAR}
        for filas, esperado in (([err, err, err], True), ([err, err], False), ([err, ok, err], False)):
            with mock.patch.object(self.sdb, "fetch_all", return_value=filas) as fa:
                self.assertEqual(S._cortado("temu", G), esperado)
        self.assertEqual(fa.call_args[0][1], {"c": "temu", "i": G, "a": S._ACC_MARCA + [S.ACC_ERROR]})
        self.assertNotIn(S.ACC_INTENTO, fa.call_args[0][1]["a"], "un intento no es ni éxito ni error")

    def test_lo_que_quedo_a_medias(self):
        sql = self._plano(S._SQL_PENDIENTES)
        self.assertIn("dry_run = false", sql)
        self.assertIn("u.accion = 'cero_intento' and u.ts < now() - interval '3 minutes'", sql)
        self.assertIn("u.ts > now() - interval '6 hours'", sql)
        with mock.patch.object(self.sdb, "fetch_all", return_value=[]) as fa:
            S.pendientes("temu")
        p = fa.call_args[0][1]
        self.assertEqual((p["c"], p["n"]), ("temu", S._RECONCILIAR_MAX))
        self.assertEqual((p["e1"], p["e2"], p["e3"]),
                         (S.ERR_RECHAZO + "%", S.ERR_NO_CONVERGE + "%", S.ERR_SIN_CIERRE + "%"))

    def test_el_cambio_ajeno_ignora_lo_propio_y_lo_del_seguro(self):
        sql = self._plano(S._SQL_AJENO)
        self.assertIn("coalesce(h.detectado_via, '') <> 'fanout_cero'", sql)
        self.assertIn("coalesce(h.valor_nuevo, '') <> all(%(p)s::text[])", sql)
        with mock.patch.object(self.sdb, "fetch_one", return_value=None) as fo:
            S.cambio_ajeno("temu", SKU, "t", ["3/2", "3/1", "3/3"])
            S.cambio_ajeno("temu", SKU, "t")
        self.assertEqual([c[0][1]["p"] for c in fo.call_args_list], [["3/2", "3/1", "3/3"], []])

    def test_el_apagado_en_bloque(self):
        sql = self._plano(S._SQL_BLOQUE)
        self.assertIn("h.valor_nuevo = %(off)s and h.valor_anterior = any(%(venta)s)", sql)
        self.assertIn("coalesce(h.detectado_via, '') <> 'fanout_cero'", sql)
        self.assertIn("having count(*) >= %(n)s", sql)
        with mock.patch.object(S.settings, "fanout_cero_bloque_n", 10), \
                mock.patch.object(self.sdb, "fetch_one", return_value={"n": 280, "hora": "x"}) as fo:
            self.assertEqual(S.apagado_en_bloque("tiktok", "t")["n"], 280)
            S.apagado_en_bloque("temu", "t")
            self.assertIsNone(S.apagado_en_bloque("temu", None), "sin marca no hay desde cuándo")
        p = [c[0][1] for c in fo.call_args_list]
        self.assertEqual([(x["off"], x["venta"], x["n"]) for x in p],
                         [("SELLER_DEACTIVATED", ["ACTIVATE"], 10), ("3/2", ["2/8"], 10)])
        with mock.patch.object(S.settings, "fanout_cero_bloque_n", 0), \
                mock.patch.object(self.sdb, "fetch_one", side_effect=AssertionError("fue a la base")):
            self.assertIsNone(S.apagado_en_bloque("temu", "t"), "en 0 la guarda no existe")

    def test_las_reactivaciones_en_auditoria(self):
        filas = [{"sku": SKU, "item_id": P, "resultado": "ok (SELLER_DEACTIVATED→PENDING)", "status": "FAILED"},
                 {"sku": SKU, "item_id": "x", "resultado": "ok (SELLER_DEACTIVATED→ACTIVATE)", "status": "ACTIVATE"},
                 {"sku": SKU, "item_id": "y", "resultado": "ok (3/2→2/8) · confirmada tarde", "status": "2/8"}]
        with mock.patch.object(self.sdb, "fetch_all", return_value=filas) as fa:
            self.assertEqual([f["item_id"] for f in S.en_auditoria("tiktok")], [P])
        self.assertEqual(fa.call_args[0][1], {"canal": "tiktok", "dias": S._AUDITORIA_DIAS})
        sql = self._plano(fa.call_args[0][0])
        self.assertIn("where m.accion = 'cero_reactivar'", sql)
        self.assertIn("ts > now() - make_interval(days => %(dias)s)", sql)

    def test_las_ultimas_marcas_traen_el_censo_y_la_foto_de_woo(self):
        fila = {"canal": "temu", "sku": SKU, "item_id": G, "accion": S.ACC_INACTIVAR, "status": "3/2",
                "stock_woo": 4, "huerfana": False, "resultado": "ok (2/8→3/2)", "edad_s": 60.0}
        vende = dict(fila, item_id="x", status="2/8")
        suelta = dict(fila, item_id="y", accion=S.ACC_SOLTAR, resultado="ok (estado 4/7 ajeno)", status="4/7")
        a_mano = dict(suelta, item_id="z", resultado="ok (manual)")
        vieja = dict(suelta, item_id="w", edad_s=(S._SOLTADAS_DIAS + 1) * 86400.0)
        with mock.patch.object(self.sdb, "fetch_all", return_value=[fila, vende, suelta, a_mano, vieja]) as fa:
            ultimas = S._ultimas("temu")
        sql = self._plano(fa.call_args[0][0])
        self.assertIn("left join ops.stock_watch_photo p on p.sku = m.sku::citext", sql)
        self.assertIn("l.listing_id = m.item_id", sql)
        self.assertIn("where m.accion in ('cero_inactivar', 'cero_soltar')", sql)
        self.assertEqual([m["item_id"] for m in S.apagadas(ultimas=ultimas)], [G, "x"])
        self.assertEqual([m["item_id"] for m in S.con_stock(S.apagadas(ultimas=ultimas))], [G],
                         "la que ya vende no es pendiente")
        self.assertEqual([m["item_id"] for m in S.soltadas(ultimas=ultimas)], ["y"],
                         "ni las soltadas a mano ni las viejas")

    def test_el_reflejo_y_las_huerfanas_firman_como_el_seguro(self):
        import inspect
        self.assertIn("set_config('app.via', 'fanout_cero', true)", inspect.getsource(S._reflejar))
        self.assertIn("detectado_via = 'fanout_cero'", inspect.getsource(S.huerfanas))


class PanelPendientes(unittest.TestCase):
    """Lo apagado por el seguro que YA tiene stock es una tarea, no un estado en reposo."""

    L = {"canal": "temu", "status": "3/2", "stock_own": 0, "act": "2026-10-07 10:00:00"}
    MARCA = {"desde": "2026-10-07 09:00:00"}
    HOY = "2026-10-07"

    def test_apagada_y_woo_ya_tiene_stock_es_pendiente(self):
        c = V._celda_matriz(self.L, None, 50, self.HOY, self.MARCA)
        self.assertEqual((c["k"], c["p"]), ("apagpend", True))
        self.assertIn("Woo ya tiene 50", c["s"])
        self.assertEqual(V._celda_matriz(self.L, None, 0, self.HOY, self.MARCA)["k"], "apag")
        self.assertEqual(V._celda_matriz(self.L, None, None, self.HOY, self.MARCA)["k"], "apag")
        self.assertNotIn(V._celda_matriz(dict(self.L, status="2/8"), None, 50, self.HOY, self.MARCA)["k"],
                         ("apag", "apagpend"), "si ya vende, es una celda normal")

    def test_un_rechazo_del_escritor_se_ve_aunque_este_apagada(self):
        antes = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
        l = dict(self.L, updated_at=antes)
        w = {"ts": antes + timedelta(minutes=5), "resultado": "ERROR: HTTP 403", "hora": "2026-10-07 10:05:00"}
        self.assertEqual(V._celda_matriz(l, w, 50, self.HOY, self.MARCA)["k"], "rech")
        ok = dict(w, resultado="ok", objetivo=50)
        c = V._celda_matriz(l, ok, 50, self.HOY, self.MARCA)
        self.assertEqual((c["k"], c["v"]), ("apagpend", "50"), "y enseña el stock que sí se escribió")

    def test_sin_marca_todo_sigue_como_antes(self):
        for woo in (0, 50, None):
            self.assertEqual(V._celda_matriz(self.L, None, woo, self.HOY),
                             V._celda_matriz(self.L, None, woo, self.HOY, None))
            self.assertNotIn(V._celda_matriz(self.L, None, woo, self.HOY)["k"], ("apag", "apagpend"))

    def test_que_atender(self):
        self.assertEqual(V._atender_seguro(None), [])
        self.assertEqual(V._atender_seguro({"con_stock": [], "soltadas": [], "reactivar": False}), [])
        una = {"canal": "temu", "sku": SKU, "woo": 1500, "desde": "x"}
        (a,) = V._atender_seguro({"con_stock": [una], "soltadas": [], "reactivar": False})
        self.assertEqual((a["nivel"], a["matriz"]), ("hoy", True))
        self.assertEqual(a["titulo"], "1 publicación apagada por el seguro de stock 0 ya tiene stock")
        self.assertIn(f"Temu · {SKU}: Woo tiene 1,500.", a["texto"])
        self.assertIn("corre en ensayo", a["texto"])
        self.assertIn("reactivarlas a mano", a["texto"])
        dos = V._atender_seguro({"con_stock": [una, dict(una, sku="OTRO")], "reactivar": True,
                                 "soltadas": [dict(una, por="ok (estado 4/7 ajeno)")]})
        self.assertEqual(dos[0]["titulo"], "2 publicaciones apagadas por el seguro de stock 0 ya tienen stock")
        self.assertIn("y 1 más", dos[0]["texto"])
        self.assertNotIn("a mano", dos[0]["texto"])
        self.assertIn("soltó sigue fuera de la venta con stock", dos[1]["titulo"])

    def test_el_rastro_sabe_si_el_cero_se_escribio(self):
        def fila(acc, resultado):
            return {"accion": acc, "resultado": resultado, "dry_run": False, "canal": "temu",
                    "stock_canal": 4, "objetivo": 0}
        con = V._celda_evento([fila("escribir", "ok"), fila("cero_inactivar", "ok (2/8→3/2)")])
        self.assertEqual((con["k"], con["escrito"]), ("apag", True))
        sin = V._celda_evento([fila("sin_cambio", "el canal ya tiene 0"), fila("cero_inactivar", "ok (2/8→3/2)")])
        self.assertEqual((sin["k"], sin["escrito"]), ("apag", False))
        self.assertNotIn("escrito", V._celda_evento([fila("escribir", "ok")]), "sin el seguro, la celda de siempre")

    def test_la_trazabilidad_no_llama_espera_a_lo_que_no_lo_es(self):
        def fila(resultado):
            return {"accion": "cero_sin_cambio", "resultado": resultado, "dry_run": False,
                    "canal": "temu", "stock_canal": 0, "objetivo": None}
        self.assertEqual(sorted(V._CERO_PREFIJO), sorted([S.CON_STOCK, S.MOVIDA]),
                         "los prefijos del panel son los del seguro")
        c = V._celda_evento([fila(S.CON_STOCK + ": Woo ya tiene 15 y sigue fuera de la venta (3/2)")])
        self.assertEqual((c["k"], c["texto"]), ("omit", "apagada y ya con stock"))
        self.assertEqual(V._celda_evento([fila(S.MOVIDA + ": Temu la pasó sola a 3/3")])["texto"],
                         "seguro: la movió el canal")
        self.assertEqual(V._celda_evento([fila("espera 20 min: el stock debe sostenerse")])["texto"],
                         "seguro: espera")

    def test_el_intento_no_pinta_nada_en_el_panel(self):
        self.assertNotIn(S.ACC_INTENTO, V._ACC_CERO)
        self.assertNotIn(S.ACC_INTENTO, V._ACC_EVENTO)
        self.assertNotIn(S.ACC_INTENTO, R._ACC_FANOUT, "ni lo reencola el recuperador")
        self.assertLessEqual(len(S.ACC_INTENTO), 20, "cabe en el VARCHAR(20) de MySQL")


if __name__ == "__main__":
    unittest.main()
