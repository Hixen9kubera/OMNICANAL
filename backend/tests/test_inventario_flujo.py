"""Pruebas del «Flujo del SKU» (`services/inventario_flujo.py`).

La barra que estrenó esta foto arriba de /inventario se quitó; lo que queda
vivo es la FOTO y sus endpoints (`/flujo`, `/flujo/skus`, y `/flujo/canal` con
el sello, que se prueban en `test_flujo_omnicanal.py`). Estas pruebas fijan la
foto, no una pantalla.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Paridad foto = tabla.** La tarjeta «3 de 4» y la columna Validado bodega
   no pueden contradecirse (decisión D2). Se corre la tabla REAL
   (`odoo.detalle_por_sku`, `ubicaciones_por_sku`, `variantes_por_sku`,
   `miniaturas_por_sku` → `inventario_maestro._fila`) y la foto REAL
   (`catalogo_productos` & cía. → `estados_bodega` → `armar_foto`) contra el
   MISMO Odoo de mentira, que entiende los dominios que ambas mandan.
2. **Orden de rutas.** `/flujo` registrada después de `{sku:path}` se resolvería
   como la ficha del SKU «flujo»: 404 sin error en logs.
3. **Permiso.** Todo /api/inventario queda con rol lectura.
4–6. **Vacío no es cero.** Catálogo vacío, fuente caída y caída brusca no
   sobreescriben la foto con ceros creíbles.
7. **Un armado a la vez**, un hilo de lectura por fuente y el reintento corto
   relee solo la fuente caída, con espera creciente.
8, 11. **La consulta a kubera**: FULL solo de Mercado Libre y JOIN por citext.
9. **Sin dinero** en ninguna llave.
10. **Contrato** de `GET /flujo/skus`.

Sin red: nada aquí habla con Odoo, kubera ni Railway.

    cd backend && python -m unittest tests.test_inventario_flujo -v
"""
from __future__ import annotations

import re
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import rbac  # noqa: E402
from routers import inventario as ruta_inv  # noqa: E402
from services import inventario_flujo as invf  # noqa: E402
from services import inventario_maestro as inv  # noqa: E402
from services import odoo, packing_cajas  # noqa: E402

T0 = datetime(2026, 9, 15, 15, 40, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# UN ODOO DE MENTIRA que entiende los dominios que mandan la tabla y la foto
# ─────────────────────────────────────────────────────────────────────────────

_IGNORADO = object()   # Odoo 17 ignora `image_1920 != False` sobre product.product


def _hoja(valor, op, esperado) -> bool:
    if valor is _IGNORADO:
        return True
    if isinstance(valor, list):          # many2one [id, nombre]: se compara el id
        valor = valor[0] if valor else False
    if op == "=":
        return valor == esperado
    if op == "!=":
        return valor != esperado
    if op == ">":
        return (valor or 0) > esperado
    if op == "in":
        return valor in esperado
    if op == "=like":
        if not valor:
            return False
        patron = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c)
                         for c in esperado)
        return re.fullmatch(patron, valor) is not None
    raise AssertionError(f"operador no soportado por el doble: {op}")


def _cumple(dominio, obtener) -> bool:
    pos = 0

    def uno() -> bool:
        nonlocal pos
        tok = dominio[pos]
        pos += 1
        if tok == "|":
            a = uno()
            b = uno()
            return a or b
        if tok == "&":
            a = uno()
            b = uno()
            return a and b
        campo, op, esperado = tok
        return _hoja(obtener(campo), op, esperado)

    resultado = True
    while pos < len(dominio):
        r = uno()
        resultado = resultado and r
    return resultado


class OdooDeMentira:
    def __init__(self, productos, plantillas_con_imagen, quants, ubicaciones):
        self.productos = sorted(productos, key=lambda p: p["id"])
        self.tmpl_img = set(plantillas_con_imagen)
        self.quants = quants
        self.ubicaciones = ubicaciones   # id → (usage, almacén, nombre completo)
        self.llamadas: list[tuple] = []

    # ── campos ───────────────────────────────────────────────────────────────
    def _nombre(self, p) -> str:
        return f"[{p['code']}] Producto {p['id']}" if p.get("code") else f"Producto {p['id']}"

    def _producto(self, p, campo):
        t = p["tmpl"]
        valores = {
            "id": p["id"],
            "default_code": p.get("code") or False,
            "name": f"Producto {p['id']}",
            "product_tmpl_id": [t, f"Plantilla {t}"],
            "active": p.get("active", True),
            "qty_available": p.get("qty", 0.0),
            "free_qty": p.get("free", p.get("qty", 0.0)),
            "image_variant_1920": b"img" if p.get("img") else False,
            "product_tmpl_id.image_1920": b"img" if t in self.tmpl_img else False,
            "image_256": "b64" if (p.get("img") or t in self.tmpl_img) else False,
            "image_1920": _IGNORADO,
            "incoming_qty": 0.0, "outgoing_qty": 0.0, "container_numbers": False,
            "sale_ok": True, "create_date": False, "write_date": False,
            "categ_id": False, "units_per_master_box": False, "cbm_master_box": False,
        }
        return valores[campo]

    def _quant(self, q, campo):
        p = next(x for x in self.productos if x["id"] == q["product"])
        loc = self.ubicaciones[q["loc"]]
        valores = {
            "id": q["id"],
            "product_id": [p["id"], self._nombre(p)],
            # búsqueda por ruta many2one: sin filtro de archivados, como Odoo
            "product_id.default_code": p.get("code") or False,
            "location_id": [q["loc"], loc[2]],
            "location_id.usage": loc[0],
            "quantity": q["qty"],
            "reserved_quantity": q.get("res", 0.0),
        }
        return valores[campo]

    # ── RPC ──────────────────────────────────────────────────────────────────
    def execute_kw(self, db, uid, pw, modelo, metodo, args, kwargs=None):
        kwargs = kwargs or {}
        self.llamadas.append((modelo, metodo, args, kwargs))
        if modelo == "product.product":
            activos_solo = (kwargs.get("context") or {}).get("active_test", True)
            regs = [p for p in self.productos if p.get("active", True) or not activos_solo]
            dominio = args[0]
            hallados = [p for p in regs
                        if _cumple(dominio, lambda c, p=p: self._producto(p, c))]
            if metodo == "search":
                return [p["id"] for p in hallados]
            if metodo == "search_read":
                if kwargs.get("limit"):
                    hallados = hallados[:kwargs["limit"]]
                campos = args[1] if len(args) > 1 else kwargs.get("fields")
                return [{"id": p["id"], **{c: self._producto(p, c) for c in campos}}
                        for p in hallados]
        if modelo == "stock.quant":
            dominio = args[0]
            hallados = [q for q in self.quants
                        if _cumple(dominio, lambda c, q=q: self._quant(q, c))]
            if metodo == "search_read":
                campos = args[1] if len(args) > 1 else kwargs.get("fields")
                return [{"id": q["id"], **{c: self._quant(q, c) for c in campos}}
                        for q in hallados]
            if metodo == "read_group":
                assert args[2] == ["product_id"] and kwargs.get("lazy") is False
                grupos: dict[int, dict] = {}
                for q in hallados:
                    pid = q["product"]
                    g = grupos.setdefault(pid, {"product_id": self._quant(q, "product_id"),
                                                "quantity": 0.0, "__count": 0})
                    g["quantity"] += q["qty"]
                    g["__count"] += 1
                return list(grupos.values())
        if modelo == "stock.location" and metodo == "read":
            return [{"id": i, "usage": self.ubicaciones[i][0],
                     "warehouse_id": ([1, self.ubicaciones[i][1]]
                                      if self.ubicaciones[i][1] else False)}
                    for i in args[0]]
        raise AssertionError(f"llamada no soportada por el doble: {modelo}.{metodo}")


def _mundo_paridad() -> OdooDeMentira:
    ubic = {100: ("internal", "TEXCO", "TEXCO/FERRAFORME/J/28/N1"),
            101: ("internal", "", "TEXCO/FERRAFORME/SCRAP"),
            102: ("internal", "TEXCO", "TEXCO/STAGE"),
            103: ("internal", "", "TEXCO/FERRAFORME/CUARENTENA")}
    productos = [
        # SKU simple sin foto
        dict(id=1, code="SIM-0001-NEG", tmpl=10, qty=5, free=5),
        # variante por PLANTILLA (una con foto propia, otra sin nada)
        dict(id=2, code="VAR-0002-ROJ", tmpl=20, qty=2, free=2, img=True),
        dict(id=3, code="VAR-0002-AZL", tmpl=20),
        # variante solo por CÓDIGO BASE (plantillas distintas)
        dict(id=4, code="BAS-0003-NEG", tmpl=30),
        dict(id=5, code="BAS-0003-NEG-B", tmpl=31),
        # free_qty = 0 con stock a la mano
        dict(id=6, code="RES-0004-NEG", tmpl=40, qty=3, free=0),
        # quant negativo
        dict(id=7, code="NEG-0005-NEG", tmpl=50, qty=-2, free=-2),
        # código duplicado: activo con stock, archivado con la foto
        dict(id=8, code="DUP-0006-NEG", tmpl=60, qty=4, free=4),
        dict(id=9, code="DUP-0006-NEG", tmpl=61, active=False),
        # hermano de plantilla SIN código: también cuenta como variante
        dict(id=10, code="COD-0007-NEG", tmpl=70, qty=1, free=1),
        dict(id=11, code="", tmpl=70),
        # solo en SCRAP: tiene ubicación (D4), no tiene stock vendible
        dict(id=12, code="SCR-0008-NEG", tmpl=80),
        # `JUGU-1153` NO es hermano por código de `JUGU-1153-MET` (=like exige
        # el guion), pero `JUGU-1153-MET` SÍ lo es de `JUGU-1153`
        dict(id=13, code="JUGU-1153", tmpl=90, qty=1, free=1),
        dict(id=14, code="JUGU-1153-MET", tmpl=91, qty=1, free=1),
        # quant en cero: no cuenta como ubicación
        dict(id=15, code="CER-0009-NEG", tmpl=95),
        # solo archivado, con todo: la tabla lo ve igual
        dict(id=16, code="ARC-0010-NEG", tmpl=96, active=False, qty=2, free=2),
        # +5 y −5: dos quants ≠ 0 que suman cero
        dict(id=17, code="ZER-0011-NEG", tmpl=97),
        # duplicado donde el ARCHIVADO tiene más stock: la fila juzga al activo
        dict(id=18, code="DUP-0013-NEG", tmpl=98, qty=1, free=0),
        dict(id=19, code="DUP-0013-NEG", tmpl=99, active=False, qty=9, free=9),
        # duplicado ACTIVO/ACTIVO: el de más quants internos los tiene en
        # CUARENTENA (interna sin almacén, fuera de qty_available); la fila
        # juzga al de 5 piezas en rack, y la foto tiene que juzgar ese mismo
        dict(id=20, code="DUP-0014-NEG", tmpl=100, qty=0, free=0),
        dict(id=21, code="DUP-0014-NEG", tmpl=101, qty=5, free=5),
    ]
    tmpl_img = {30, 40, 50, 61, 70, 80, 90, 91, 95, 96, 97, 98, 100, 101}
    quants = [
        dict(id=1, product=1, loc=100, qty=5), dict(id=2, product=2, loc=100, qty=2),
        dict(id=3, product=6, loc=100, qty=3, res=3), dict(id=4, product=7, loc=100, qty=-2),
        dict(id=5, product=8, loc=100, qty=4), dict(id=6, product=10, loc=100, qty=1),
        dict(id=7, product=12, loc=101, qty=2), dict(id=8, product=13, loc=102, qty=1),
        dict(id=9, product=14, loc=100, qty=1), dict(id=10, product=15, loc=100, qty=0),
        dict(id=11, product=16, loc=100, qty=2), dict(id=12, product=17, loc=100, qty=5),
        dict(id=13, product=17, loc=102, qty=-5),
        dict(id=14, product=18, loc=100, qty=1, res=1), dict(id=15, product=19, loc=100, qty=9),
        dict(id=16, product=20, loc=103, qty=10), dict(id=17, product=21, loc=100, qty=5),
    ]
    return OdooDeMentira(productos, tmpl_img, quants, ubic)


def _con_odoo(falso: OdooDeMentira):
    """Conecta el doble a las DOS vías de odoo.py: la de la tabla (`_uid`,
    `_models`) y la de la foto (`_uid_con_tiempo`, `_proxy_con_tiempo`)."""
    return [mock.patch.object(odoo, "_uid", return_value=1),
            mock.patch.object(odoo, "_models", return_value=falso),
            mock.patch.object(odoo, "_uid_con_tiempo", return_value=1),
            mock.patch.object(odoo, "_proxy_con_tiempo", return_value=falso)]


class _ConParches(unittest.TestCase):
    def setUp(self):
        invf._foto = None
        invf._hilos_fuente.clear()
        self.addCleanup(invf._hilos_fuente.clear)
        # Ninguna prueba debe lanzar el armado real en un hilo de fondo.
        p = mock.patch.object(invf, "calentar_en_fondo", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        # La cuarta fuente (`canales`) no habla con nadie en las pruebas: sin
        # esto cada armado la daría por caída y `_fuentes_a_leer` la reintentaría.
        pc = mock.patch.object(invf, "_leer_canales",
                               side_effect=lambda: _canales_crudo())
        pc.start()
        self.addCleanup(pc.stop)
        self.addCleanup(setattr, invf, "_foto", None)

    def parchar(self, parches):
        for p in parches:
            p.start()
            self.addCleanup(p.stop)


# ─────────────────────────────────────────────────────────────────────────────
# 1 · PARIDAD
# ─────────────────────────────────────────────────────────────────────────────

class ParidadFotoTabla(_ConParches):
    def setUp(self):
        super().setUp()
        self.falso = _mundo_paridad()
        self.parchar(_con_odoo(self.falso))
        odoo._ubicaciones_cache.cache_clear()
        self.addCleanup(odoo._ubicaciones_cache.cache_clear)
        self.codigos = sorted({p["code"] for p in self.falso.productos if p["code"]})
        # PL-0012-NEG existe solo en kubera (packing_list_only)
        self.skus = self.codigos + ["PL-0012-NEG"]

    def _tabla(self) -> dict[str, tuple[str, str, str]]:
        od = odoo.detalle_por_sku(self.skus)
        ubis = odoo.ubicaciones_por_sku(self.skus)
        herm = odoo.variantes_por_sku(self.skus)
        imgs = odoo.miniaturas_por_sku(self.skus)
        salida = {}
        for s in self.skus:
            fila = inv._fila(s, None, od.get(s), None, [], None, imgs.get(s),
                             ubis.get(s, []), herm.get(s, []))
            salida[s] = tuple(inv._punto(fila, k) for k in ("ubicacion", "stock", "foto"))
        return salida

    def _crudo_foto(self) -> dict:
        self.falso.llamadas.clear()
        # La lectura REAL de la fuente, no una copia a mano de sus llamadas.
        return invf._leer_odoo()

    def test_estados_por_sku_iguales_a_la_tabla(self):
        tabla = self._tabla()
        crudo = self._crudo_foto()
        flujo = invf.estados_bodega(crudo["catalogo"], crudo["quants"],
                                    crudo["libres"], crudo["fotos"], self.skus,
                                    existencias=crudo["existencias"])
        self.assertEqual(flujo, tabla)
        # Que la prueba no sea vacía: los casos del plan dan lo que deben.
        esperado = {
            "SIM-0001-NEG": ("listo", "listo", "falta"),
            "VAR-0002-ROJ": ("listo", "listo", "espera"),
            "VAR-0002-AZL": ("falta", "falta", "na"),
            "BAS-0003-NEG": ("falta", "falta", "espera"),
            "BAS-0003-NEG-B": ("falta", "falta", "na"),
            "RES-0004-NEG": ("listo", "falta", "listo"),
            "NEG-0005-NEG": ("listo", "falta", "listo"),
            "DUP-0006-NEG": ("listo", "listo", "listo"),
            "COD-0007-NEG": ("listo", "listo", "espera"),
            "SCR-0008-NEG": ("listo", "falta", "listo"),
            "JUGU-1153": ("listo", "listo", "espera"),
            "JUGU-1153-MET": ("listo", "listo", "listo"),
            "CER-0009-NEG": ("falta", "falta", "listo"),
            "ARC-0010-NEG": ("listo", "listo", "listo"),
            "ZER-0011-NEG": ("listo", "falta", "listo"),
            "PL-0012-NEG": ("falta", "falta", "falta"),
            "DUP-0013-NEG": ("listo", "falta", "listo"),
            "DUP-0014-NEG": ("listo", "listo", "listo"),
        }
        self.assertEqual(tabla, esperado)

    def test_duplicado_activo_activo_se_desempata_como_la_tabla(self):
        crudo = self._crudo_foto()
        # Solo se piden existencias de los empatados en `active`: DUP-0006 y
        # DUP-0013 tienen un activo y un archivado, y ahí gana el activo.
        self.assertEqual(invf.ids_para_desempate(crudo["catalogo"]), [20, 21])
        self.assertEqual(crudo["existencias"], {20: (0.0, 0.0), 21: (5.0, 5.0)})
        # Con la suma de quants internos (10 en cuarentena contra 5 en rack) la
        # la foto juzgaba al producto 20 y decía «falta».
        self.assertGreater(crudo["quants"][20], crudo["quants"][21])
        sin = invf.estados_bodega(crudo["catalogo"], crudo["quants"], crudo["libres"],
                                  crudo["fotos"], ["DUP-0014-NEG"],
                                  existencias={20: (10.0, 10.0)})
        self.assertEqual(sin["DUP-0014-NEG"][1], "falta", "la prueba distingue el caso")
        con = invf.estados_bodega(crudo["catalogo"], crudo["quants"], crudo["libres"],
                                  crudo["fotos"], ["DUP-0014-NEG"],
                                  existencias=crudo["existencias"])
        self.assertEqual(con["DUP-0014-NEG"], self._tabla()["DUP-0014-NEG"])

    def test_armar_foto_cuenta_lo_mismo_que_la_tabla(self):
        tabla = self._tabla()
        crudo = self._crudo_foto()
        filas_kubera = [{"sku": s, "recibido": False, "costo_validado": False,
                         "en_full": False} for s in self.skus]
        foto = invf.armar_foto(invf.Lectura(datos=filas_kubera, generado=T0),
                               invf.Lectura(datos=crudo, generado=T0),
                               invf.Lectura(datos=(set(), 0.0), generado=T0),
                               None, ahora=T0)
        tres = sorted(s for s, e in tabla.items() if e == ("listo", "listo", "listo"))
        self.assertEqual(list(foto.listas["bodega_3de4"]), tres)
        self.assertEqual(foto.requisitos["ubicacion"],
                         sum(1 for e in tabla.values() if e[0] == "listo"))
        self.assertEqual(foto.requisitos["stock"],
                         sum(1 for e in tabla.values() if e[1] == "listo"))
        self.assertEqual(foto.requisitos["foto"],
                         sum(1 for e in tabla.values() if e[2] == "listo"))
        self.assertEqual(foto.requisitos["foto_espera"],
                         sum(1 for e in tabla.values() if e[2] == "espera"))
        # 4 de 4 es vacío por construcción: specs sigue en espera en la tabla.
        self.assertEqual(foto.listas["validado_bodega"], ())

    def test_la_foto_no_pide_binarios_ni_image_1920_a_secas(self):
        self._crudo_foto()
        prohibidos = {"image_1920", "image_256", "image_variant_1920"}
        for modelo, metodo, args, kwargs in self.falso.llamadas:
            campos = set(kwargs.get("fields") or []) | set(
                args[1] if len(args) > 1 and metodo == "search_read" else [])
            self.assertFalse(campos & prohibidos, f"{modelo}.{metodo} pidió {campos}")
            hojas = [t[0] for t in (args[0] if args else []) if isinstance(t, list)]
            self.assertNotIn("image_1920", hojas)
            self.assertNotIn("image_256", hojas)
            if modelo == "product.product":
                self.assertEqual((kwargs.get("context") or {}).get("active_test"), False,
                                 "la tabla ve archivados; la foto también")
        hojas_foto = [t[0] for t in odoo._DOMINIO_FOTO if isinstance(t, list)]
        self.assertEqual(hojas_foto, ["image_variant_1920", "product_tmpl_id.image_1920"])


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures pequeños para el resto
# ─────────────────────────────────────────────────────────────────────────────

def _odoo_crudo(n: int = 100, con_quants: int | None = None) -> dict:
    con_quants = n if con_quants is None else con_quants
    catalogo = [{"id": i, "default_code": f"SKU-{i:04d}-NEG", "tmpl_id": 1000 + i,
                 "active": True} for i in range(1, n + 1)]
    return {"catalogo": catalogo,
            "quants": {i: 1.0 for i in range(1, con_quants + 1)},
            "libres": set(range(1, n + 1)),
            "fotos": set(range(1, n + 1))}


def _kubera_filas(n: int = 100) -> list[dict]:
    """El molde de `_SQL_KUBERA`, con las tres columnas nuevas.

    `con_renglon` hasta el 80 para que 61–80 den «sin cajas» y 81–100 «sin
    renglón», que son huecos distintos. SKU-0002 y SKU-0003 cuelgan de SKU-0001
    en Woo: eso lo vuelve un PADRE y da material para el modo padre del sello.
    """
    hijos = {"SKU-0002-NEG": 1001, "SKU-0003-NEG": 1001}
    filas = []
    for i in range(1, n + 1):
        sku = f"SKU-{i:04d}-NEG"
        filas.append({"sku": sku, "recibido": i <= 60,
                      "costo_validado": i <= 5, "en_full": 50 < i <= 70,
                      "con_renglon": i <= 80,
                      "wc_id": 1000 + i, "wc_parent_id": hijos.get(sku)})
    filas.append({"sku": "ROP-0695-BEI-m", "recibido": True,
                  "costo_validado": False, "en_full": False,
                  "con_renglon": True, "wc_id": 9001, "wc_parent_id": None})
    return filas


def _canales_crudo(n: int = 40) -> dict:
    """El molde de `_leer_canales`: filas de `channel.listings` ya evaluadas."""
    filas = []
    for i in range(1, n + 1):
        sku = f"SKU-{i:04d}-NEG"
        filas.append({"sku": sku, "canal": "mercado_libre", "cuenta": "BEKURA",
                      "publicado": True, "activa": i % 2 == 0,
                      "full": 50 < i <= 70, "en_catalogo": True})
        filas.append({"sku": sku, "canal": "tiktok", "cuenta": "",
                      "publicado": i <= 10, "activa": i <= 10,
                      "full": False, "en_catalogo": True})
    return {"filas": filas, "sin_activas": []}


def _foto(kubera=None, odoo_=None, drop=None, anterior=None, ahora=T0,
          canales=None):
    lk = kubera if isinstance(kubera, invf.Lectura) else invf.Lectura(
        datos=_kubera_filas() if kubera is None else kubera, generado=ahora)
    lo = odoo_ if isinstance(odoo_, invf.Lectura) else invf.Lectura(
        datos=_odoo_crudo() if odoo_ is None else odoo_, generado=ahora)
    ld = drop if isinstance(drop, invf.Lectura) else invf.Lectura(
        datos=({"SKU-0001-NEG", "SKU-0002-NEG", "NO-EXISTE"}, 0.0)
        if drop is None else drop, generado=ahora)
    lc = canales if isinstance(canales, invf.Lectura) else invf.Lectura(
        datos=_canales_crudo() if canales is None else canales, generado=ahora)
    return invf.armar_foto(lk, lo, ld, anterior, ahora=ahora, canales=lc)


def _etapa(resp: dict, clave: str) -> dict:
    if resp["carril"]["clave"] == clave:
        return resp["carril"]
    return next(e for e in resp["etapas"] if e["clave"] == clave)


# ─────────────────────────────────────────────────────────────────────────────
# 2 · 3 · RUTAS Y PERMISO
# ─────────────────────────────────────────────────────────────────────────────

def _cliente() -> TestClient:
    app = FastAPI()
    app.include_router(ruta_inv.router)
    return TestClient(app)


class OrdenDeRutas(_ConParches):
    def test_flujo_no_llega_a_la_ficha(self):
        with mock.patch.object(ruta_inv.inv, "filas",
                               side_effect=AssertionError("llegó a la ficha")) as filas, \
                mock.patch.object(invf, "conteos", return_value={"marca": "flujo"}), \
                mock.patch.object(invf, "skus_de_etapa", return_value={"marca": "skus"}):
            c = _cliente()
            r1 = c.get("/api/inventario/flujo")
            r2 = c.get("/api/inventario/flujo/skus?etapa=en_full")
        self.assertEqual((r1.status_code, r1.json()), (200, {"marca": "flujo"}))
        self.assertEqual((r2.status_code, r2.json()), (200, {"marca": "skus"}))
        filas.assert_not_called()

    def test_las_comodines_siguen_vivas(self):
        with mock.patch.object(ruta_inv.inv, "filas", return_value=[]) as filas, \
                mock.patch.object(ruta_inv.inv, "movimientos",
                                  return_value={"sku": "CALZ-0194-BLN/AZL-40"}) as movs:
            c = _cliente()
            self.assertEqual(c.get("/api/inventario/CALZ-0194-BLN/AZL-40").status_code, 404)
            r = c.get("/api/inventario/CALZ-0194-BLN/AZL-40/movimientos")
        filas.assert_called_once()
        movs.assert_called_once()
        self.assertEqual(r.status_code, 200)


class Permiso(unittest.TestCase):
    def test_rutas_nuevas_con_rol_lectura(self):
        self.assertEqual(rbac.rol_requerido("GET", "/api/inventario/flujo"), "lectura")
        self.assertEqual(rbac.rol_requerido("GET", "/api/inventario/flujo/skus"), "lectura")


# ─────────────────────────────────────────────────────────────────────────────
# 4 · 5 · 6 · FUENTES CAÍDAS, VACÍAS O SOSPECHOSAS
# ─────────────────────────────────────────────────────────────────────────────

class CatalogoVacio(_ConParches):
    def _refrescar_con_catalogo_vacio(self):
        def kw(modelo, metodo, args, kwargs=None, **_):
            if modelo == "product.product" and metodo == "search_read":
                return []
            raise AssertionError("no debió pasar del catálogo")
        with mock.patch.object(invf, "_leer_kubera", return_value=_kubera_filas()), \
                mock.patch.object(odoo, "_kw_flujo", side_effect=kw), \
                mock.patch.object(invf, "_leer_drop", return_value=({"SKU-0001-NEG"}, 0.0)):
            self.assertTrue(invf.refrescar(motivo="prueba"))

    def test_catalogo_vacio_es_falla_y_conserva_lo_anterior(self):
        invf._foto = _foto(ahora=invf._ahora() - timedelta(minutes=31))
        antes = invf.conteos()
        self._refrescar_con_catalogo_vacio()
        resp = invf.conteos()
        self.assertFalse(resp["fuentes"]["odoo"]["ok"])
        self.assertTrue(resp["fuentes"]["odoo"]["vieja"])
        self.assertIn("vacío", resp["fuentes"]["odoo"]["error"])
        vb = _etapa(resp, "validado_bodega")
        self.assertTrue(vb["vieja"])
        self.assertEqual(vb["sub"]["n"], _etapa(antes, "validado_bodega")["sub"]["n"])
        self.assertEqual(vb["requisitos"][0]["n"], 100)
        self.assertEqual(resp["estado"], "vieja")
        # kubera y DROP sí se refrescaron
        self.assertTrue(resp["fuentes"]["kubera"]["ok"])

    def test_catalogo_vacio_sin_anterior_es_sin_dato(self):
        self._refrescar_con_catalogo_vacio()
        resp = invf.conteos()
        vb = _etapa(resp, "validado_bodega")
        self.assertIsNone(vb["n"])
        self.assertEqual(vb["estado"], "sin_dato")
        self.assertIsNone(vb["sub"]["n"])
        self.assertIn("sin dato de odoo", vb["motivo"])
        self.assertIsNone(_etapa(resp, "recibido")["desglose"][0]["n"],
                          "un cruce con Odoo sin dato no puede salir en cero")
        self.assertEqual(_etapa(resp, "recibido")["n"], 61)

    def test_lo_anterior_de_mas_de_2_h_ya_no_se_sirve(self):
        viejisima = _foto(ahora=T0 - timedelta(hours=3))
        foto = _foto(odoo_=invf.Lectura(error="RuntimeError: caído", generado=T0),
                     anterior=viejisima)
        self.assertIsNone(foto.fuentes["odoo"].datos)
        self.assertFalse(foto.fuentes["odoo"].vieja)


class KuberaCaido(_ConParches):
    def test_etapas_de_kubera_sin_dato_y_las_de_odoo_medidas(self):
        invf._foto = _foto(kubera=invf.Lectura(error="OperationalError: sin red",
                                               generado=T0))
        with mock.patch.object(invf, "_ahora", return_value=T0):
            resp = invf.conteos()
        for clave in ("recibido", "en_full", "costo_validado", "listo_envio"):
            e = _etapa(resp, clave)
            self.assertIsNone(e["n"], clave)
            self.assertEqual(e["estado"], "sin_dato", clave)
            self.assertIn("kubera", e["motivo"])
        vb = _etapa(resp, "validado_bodega")
        self.assertEqual((vb["n"], vb["estado"]), (0, "bloqueado"))
        self.assertEqual(vb["sub"]["n"], 100)
        self.assertIn("códigos de Odoo", vb["motivo"])
        drop = _etapa(resp, "en_drop")
        self.assertEqual((drop["n"], drop["estado"]), (3, "medido"))
        self.assertEqual(drop["desglose"][0]["n"], 2)
        self.assertIsNone(resp["universo"]["n"])


class CaidaBrusca(_ConParches):
    def test_caida_de_mas_de_50_por_ciento_se_marca_y_conserva(self):
        anterior = _foto()
        self.assertEqual(anterior.requisitos["ubicacion"], 100)
        nueva = _foto(odoo_=_odoo_crudo(con_quants=30), anterior=anterior,
                      ahora=T0 + timedelta(minutes=30))
        f = nueva.fuentes["odoo"]
        self.assertTrue(f.sospechosa)
        self.assertTrue(f.vieja)
        self.assertFalse(f.ok)
        self.assertIn("ubicacion 100 → 30", f.error)
        self.assertEqual(nueva.requisitos["ubicacion"], 100, "se conserva lo anterior")
        self.assertEqual(f.generado, anterior.fuentes["odoo"].generado)

    def test_la_caida_se_acepta_si_la_siguiente_lectura_la_repite(self):
        anterior = _foto()
        sospechosa = _foto(odoo_=_odoo_crudo(con_quants=30), anterior=anterior,
                           ahora=T0 + timedelta(minutes=30))
        confirmada = _foto(odoo_=_odoo_crudo(con_quants=31), anterior=sospechosa,
                           ahora=T0 + timedelta(minutes=60))
        f = confirmada.fuentes["odoo"]
        self.assertTrue(f.ok)
        self.assertFalse(f.sospechosa)
        self.assertEqual(confirmada.requisitos["ubicacion"], 31)

    def test_drop_que_sirve_su_cache_vencida_sale_vieja(self):
        foto = _foto(drop=({"SKU-0001-NEG"}, invf.TTL_S + 60))
        f = foto.fuentes["odoo_drop"]
        self.assertTrue(f.vieja)
        self.assertFalse(f.ok)
        self.assertEqual(f.generado, T0 - timedelta(seconds=invf.TTL_S + 60))


# ─────────────────────────────────────────────────────────────────────────────
# 7 · CONCURRENCIA
# ─────────────────────────────────────────────────────────────────────────────

class Concurrencia(_ConParches):
    def test_dos_refrescar_simultaneos_arman_una_vez(self):
        entro = threading.Event()
        suelta = threading.Event()

        def kubera_lento():
            entro.set()
            suelta.wait(5)
            return _kubera_filas()

        resultados: list[bool] = []
        with mock.patch.object(invf, "_leer_kubera", side_effect=kubera_lento) as kub, \
                mock.patch.object(invf, "_leer_odoo", return_value=_odoo_crudo()), \
                mock.patch.object(invf, "_leer_drop", return_value=(set(), 0.0)):
            a = threading.Thread(target=lambda: resultados.append(
                invf.refrescar(motivo="A")))
            a.start()
            self.assertTrue(entro.wait(5))
            self.assertFalse(invf.refrescar(motivo="B"), "no espera: se va")
            suelta.set()
            a.join(5)
        self.assertEqual(resultados, [True])
        self.assertEqual(kub.call_count, 1)
        self.assertIsNotNone(invf._foto)

    def test_calentar_no_lanza_un_segundo_hilo(self):
        mock.patch.stopall()   # quitar el parche de calentar_en_fondo de setUp
        suelta = threading.Event()

        def kubera_lento():
            suelta.wait(5)
            return _kubera_filas()

        with mock.patch.object(invf.settings, "inventario_flujo_enabled", True), \
                mock.patch.object(invf, "_leer_kubera", side_effect=kubera_lento) as kub, \
                mock.patch.object(invf, "_leer_odoo", return_value=_odoo_crudo()), \
                mock.patch.object(invf, "_leer_drop", return_value=(set(), 0.0)):
            self.assertTrue(invf.calentar_en_fondo(motivo="uno"))
            self.assertFalse(invf.calentar_en_fondo(motivo="dos"))
            suelta.set()
            invf._hilo.join(5)
        self.assertEqual(kub.call_count, 1)

    def test_apagado_no_arma_nada(self):
        mock.patch.stopall()
        with mock.patch.object(invf.settings, "inventario_flujo_enabled", False), \
                mock.patch.object(invf, "refrescar") as ref:
            self.assertFalse(invf.calentar_en_fondo(forzar=True))
            resp = invf.conteos()
        ref.assert_not_called()
        self.assertEqual(resp["estado"], "calentando")
        self.assertIn("INVENTARIO_FLUJO_ENABLED", resp["motivo"])

    def test_una_fuente_colgada_no_detiene_el_armado(self):
        with self.assertRaises(TimeoutError):
            invf._con_limite(lambda: time.sleep(2), 0.1)

    def test_fuente_colgada_deja_un_solo_hilo_zombi(self):
        suelta = threading.Event()
        self.addCleanup(suelta.set)
        with mock.patch.object(invf, "_leer_kubera", return_value=_kubera_filas()), \
                mock.patch.object(invf, "_leer_odoo", return_value=_odoo_crudo()), \
                mock.patch.object(invf, "_leer_drop",
                                  side_effect=lambda: suelta.wait(10)) as drop, \
                mock.patch.object(invf, "_limite_fuente_s", return_value=0.1):
            for _ in range(5):
                self.assertTrue(invf.refrescar(motivo="colgada", forzar=True))
        self.assertEqual(drop.call_count, 1, "no se lanza otro hilo para la misma fuente")
        f = invf._foto.fuentes["odoo_drop"]
        self.assertFalse(f.ok)
        self.assertIn("sigue colgada", f.error)
        self.assertEqual(f.fallas, 5)
        self.assertTrue(invf._foto.fuentes["kubera"].ok)
        # Cuando la lectura colgada por fin termina, la siguiente vuelve a leer.
        zombi = invf._hilos_fuente["odoo_drop"]
        suelta.set()
        zombi.join(5)
        with mock.patch.object(invf, "_leer_kubera", return_value=_kubera_filas()), \
                mock.patch.object(invf, "_leer_odoo", return_value=_odoo_crudo()), \
                mock.patch.object(invf, "_leer_drop",
                                  return_value=({"SKU-0001-NEG"}, 0.0)) as drop2:
            self.assertTrue(invf.refrescar(motivo="recupera", forzar=True))
        drop2.assert_called_once()
        self.assertTrue(invf._foto.fuentes["odoo_drop"].ok)


class ReintentoSoloLoCaido(_ConParches):
    """Una fuente caída no rearma las sanas cada 5 min, y su reintento se
    espacia: 5, 10, 20 min… hasta el TTL."""

    def _con_drop_caido(self, ahora):
        return _foto(drop=invf.Lectura(error="RuntimeError: caído", generado=ahora),
                     ahora=ahora)

    def test_solo_se_relee_la_fuente_caida(self):
        foto = self._con_drop_caido(T0)
        self.assertEqual(invf._fuentes_a_leer(foto, T0 + timedelta(seconds=299)), ())
        self.assertEqual(invf._fuentes_a_leer(foto, T0 + timedelta(seconds=301)),
                         ("odoo_drop",))
        self.assertEqual(invf._fuentes_a_leer(foto, T0 + timedelta(seconds=invf.TTL_S)),
                         invf.FUENTES)

    def test_refrescar_conserva_tal_cual_las_fuentes_sanas(self):
        anterior = self._con_drop_caido(T0)
        invf._foto = anterior
        ahora = T0 + timedelta(seconds=301)
        with mock.patch.object(invf, "_ahora", return_value=ahora), \
                mock.patch.object(invf, "_leer_kubera",
                                  side_effect=AssertionError("releyó kubera")), \
                mock.patch.object(invf, "_leer_odoo",
                                  side_effect=AssertionError("releyó el catálogo de Odoo")), \
                mock.patch.object(invf, "_leer_canales",
                                  side_effect=AssertionError("releyó los canales")), \
                mock.patch.object(invf, "_leer_drop",
                                  return_value=({"SKU-0001-NEG"}, 0.0)) as drop:
            self.assertTrue(invf.refrescar(motivo="reintento"))
        drop.assert_called_once()
        nueva = invf._foto
        self.assertIs(nueva.fuentes["kubera"], anterior.fuentes["kubera"])
        self.assertIs(nueva.fuentes["odoo"], anterior.fuentes["odoo"])
        self.assertTrue(nueva.fuentes["odoo_drop"].ok)
        self.assertEqual(nueva.fuentes["odoo_drop"].fallas, 0)
        self.assertEqual(nueva.listas["en_drop"], ("SKU-0001-NEG",))
        self.assertEqual(nueva.listas["recibido"], anterior.listas["recibido"])
        self.assertEqual(nueva.cruces["en_drop.cumple_3de4"], 1)
        # Con todo sano ya no hay nada que leer hasta el TTL.
        with mock.patch.object(invf, "_ahora", return_value=ahora + timedelta(seconds=60)):
            self.assertFalse(invf.refrescar(motivo="nada"))

    def test_caida_persistente_espacia_los_reintentos(self):
        self.assertEqual([invf._espera_reintento(n) for n in range(1, 7)],
                         [300, 600, 1200, 1800, 1800, 1800])
        foto = self._con_drop_caido(T0)
        intento = T0
        for fallas, espera in ((1, 300), (2, 600), (3, 1200), (4, 1800)):
            self.assertEqual(foto.fuentes["odoo_drop"].fallas, fallas)
            antes = intento + timedelta(seconds=espera - 1)
            self.assertNotIn("odoo_drop", invf._fuentes_a_leer(foto, antes))
            intento = intento + timedelta(seconds=espera)
            self.assertIn("odoo_drop", invf._fuentes_a_leer(foto, intento))
            foto = invf.armar_foto(None, None,
                                   invf.Lectura(error="RuntimeError: caído", generado=intento),
                                   foto, ahora=intento)

    def test_forzar_relee_todo(self):
        invf._foto = self._con_drop_caido(T0)
        with mock.patch.object(invf, "_ahora", return_value=T0 + timedelta(seconds=10)), \
                mock.patch.object(invf, "_leer_kubera", return_value=_kubera_filas()) as kub, \
                mock.patch.object(invf, "_leer_odoo", return_value=_odoo_crudo()) as od, \
                mock.patch.object(invf, "_leer_drop", return_value=(set(), 0.0)) as dr:
            self.assertTrue(invf.refrescar(motivo="scheduler", forzar=True))
        for m in (kub, od, dr):
            m.assert_called_once()

    def test_transporte_xmlrpc_con_timeout_sin_conectar(self):
        for clase in (odoo._TransporteConTiempo, odoo._TransporteSeguroConTiempo):
            conn = clase(7).make_connection("odoo.invalid")
            self.assertEqual(conn.timeout, 7)
            self.assertIsNone(conn.sock, "crear la conexión no debe abrir el socket")


# ─────────────────────────────────────────────────────────────────────────────
# 8 · 11 · LA CONSULTA A KUBERA
# ─────────────────────────────────────────────────────────────────────────────

class ConsultaKubera(_ConParches):
    def test_full_solo_cuenta_mercado_libre_con_stock_full(self):
        sql = " ".join(invf._SQL_KUBERA.split())
        ml = re.search(r"ml as \((.*?)\)\s*select", sql, re.IGNORECASE)
        self.assertIsNotNone(ml)
        self.assertRegex(ml.group(1),
                         r"where canal = 'mercado_libre' and stock_full > 0")
        self.assertIn("(ml.sku is not null) as en_full", sql)
        # Una fila de Amazon con stock_full > 0 no entra: el filtro de canal es
        # parte del WHERE, no una suma posterior.
        self.assertNotRegex(ml.group(1), r"\bor\b")

    def test_join_por_citext_nativo(self):
        sql = " ".join(invf._SQL_KUBERA.split())
        self.assertIn("left join cv on cv.sku = p.sku", sql)
        self.assertIn("left join ml on ml.sku = p.sku", sql)
        self.assertEqual(sql.count("::text"), 1, "solo el cast de salida")
        self.assertIn("p.sku::text as sku", sql)

    def test_sku_con_minuscula_cuenta_en_recibido_con_su_escritura(self):
        invf._foto = _foto()
        self.assertIn("ROP-0695-BEI-m", invf._foto.listas["recibido"])
        with mock.patch.object(invf, "_ahora", return_value=T0):
            r = invf.skus_de_etapa("recibido", 1, 200, "rop-0695-bei-M")
        self.assertEqual(r["skus"], ["ROP-0695-BEI-m"])

    def test_leer_kubera_usa_timeout_local_y_nunca_set_session(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = [{"sku": "A", "recibido": True,
                                         "costo_validado": False, "en_full": False}]

        class _Ctx:
            def __enter__(self):
                return cursor

            def __exit__(self, *a):
                return False

        with mock.patch.object(invf.sdb, "disponible", return_value=True), \
                mock.patch.object(invf.sdb, "get_cursor", return_value=_Ctx()):
            filas = invf._leer_kubera()
        self.assertEqual(filas[0]["sku"], "A")
        primera = cursor.execute.call_args_list[0][0][0]
        self.assertIn("set_config('statement_timeout'", primera)
        self.assertIn(", true)", primera)
        todo = " ".join(str(c) for c in cursor.execute.call_args_list).lower()
        self.assertNotIn("set_session", todo)
        self.assertNotIn("read only", todo)


# ─────────────────────────────────────────────────────────────────────────────
# 9 · SIN DINERO
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


class SinDinero(_ConParches):
    def _revisar(self, resp):
        for ruta, llave in _llaves(resp):
            for palabra in _PALABRAS_DINERO:
                self.assertNotIn(palabra, str(llave).lower(), ruta)

    def test_ninguna_llave_lleva_dinero(self):
        self._revisar(invf.conteos())                      # calentando
        self._revisar(invf.skus_de_etapa("recibido"))
        invf._foto = _foto()
        with mock.patch.object(invf, "_ahora", return_value=T0):
            self._revisar(invf.conteos())
            for etapa in sorted(invf.ETAPAS_FILTRABLES):
                self._revisar(invf.skus_de_etapa(etapa, 1, 40, None))


# ─────────────────────────────────────────────────────────────────────────────
# 10 · CONTRATO DE /flujo/skus
# ─────────────────────────────────────────────────────────────────────────────

class ContratoSkus(_ConParches):
    def setUp(self):
        super().setUp()
        self.c = _cliente()
        p = mock.patch.object(invf, "_ahora", return_value=T0)
        p.start()
        self.addCleanup(p.stop)

    def test_restock_y_desconocida_son_400(self):
        r = self.c.get("/api/inventario/flujo/skus?etapa=restock")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"], "restock: etapa por definir, sin lista")
        self.assertEqual(self.c.get("/api/inventario/flujo/skus?etapa=xyz").status_code, 400)

    def test_validacion_de_parametros_es_422(self):
        for qs in ("etapa=recibido&per_page=201", "etapa=recibido&page=0",
                   "etapa=recibido&per_page=0", "etapa=recibido&q=" + "x" * 61, ""):
            self.assertEqual(self.c.get(f"/api/inventario/flujo/skus?{qs}").status_code,
                             422, qs)

    def test_calentando_es_200_con_total_null(self):
        r = self.c.get("/api/inventario/flujo/skus?etapa=recibido")
        self.assertEqual(r.status_code, 200)
        cuerpo = r.json()
        self.assertEqual((cuerpo["estado"], cuerpo["total"], cuerpo["skus"]),
                         ("calentando", None, []))

    def test_etapa_medida_vacia_es_200_con_total_cero(self):
        invf._foto = _foto()
        for etapa in ("validado_bodega", "listo_envio"):
            cuerpo = self.c.get(f"/api/inventario/flujo/skus?etapa={etapa}").json()
            self.assertEqual((cuerpo["total"], cuerpo["skus"]), (0, []), etapa)
            self.assertEqual(cuerpo["estado"], "listo")

    def test_fuente_sin_dato_es_200_con_total_null(self):
        invf._foto = _foto(kubera=invf.Lectura(error="X: caído", generado=T0))
        cuerpo = self.c.get("/api/inventario/flujo/skus?etapa=en_full").json()
        self.assertEqual((cuerpo["estado"], cuerpo["total"]), ("sin_dato", None))

    def test_q_sin_distinguir_mayusculas(self):
        invf._foto = _foto()
        cuerpo = self.c.get("/api/inventario/flujo/skus?etapa=recibido&q=sku-000").json()
        self.assertEqual(cuerpo["total"], 9)
        self.assertEqual(cuerpo["q"], "sku-000")
        self.assertTrue(all("SKU-000" in s for s in cuerpo["skus"]))

    def test_paginacion_estable_por_sku(self):
        invf._foto = _foto()
        primera = self.c.get("/api/inventario/flujo/skus?etapa=en_full&per_page=7").json()
        self.assertEqual((primera["total"], primera["total_pages"]), (20, 3))
        vistos: list[str] = []
        for page in range(1, primera["total_pages"] + 1):
            cuerpo = self.c.get(
                f"/api/inventario/flujo/skus?etapa=en_full&per_page=7&page={page}").json()
            self.assertEqual(cuerpo["generado"], primera["generado"])
            vistos += cuerpo["skus"]
        self.assertEqual(vistos, sorted(vistos))
        self.assertEqual(len(vistos), len(set(vistos)))
        self.assertEqual(vistos, list(invf._foto.listas["en_full"]))
        fuera = self.c.get("/api/inventario/flujo/skus?etapa=en_full&per_page=7&page=9").json()
        self.assertEqual(fuera["skus"], [])

    def test_conteos_trae_falta_definicion_y_desgloses(self):
        invf._foto = _foto()
        resp = self.c.get("/api/inventario/flujo").json()
        self.assertEqual(resp["estado"], "listo")
        self.assertEqual([e["clave"] for e in resp["etapas"]],
                         ["recibido", "validado_bodega", "listo_envio", "en_full",
                          "en_drop", "restock"])
        for e in resp["etapas"] + [resp["carril"]]:
            self.assertTrue(e["falta"], e["clave"])
            self.assertTrue(e["definicion"], e["clave"])
        rec = _etapa(resp, "recibido")
        self.assertEqual((rec["n"], rec["estado"]), (61, "proxy"))
        self.assertEqual(rec["desglose"], [{"clave": "fuera_de_odoo",
                                            "titulo": "No existen en Odoo activo", "n": 1}])
        restock = _etapa(resp, "restock")
        self.assertEqual((restock["n"], restock["estado"], restock["filtrable"]),
                         (None, "por_definir", False))
        vb = _etapa(resp, "validado_bodega")
        self.assertFalse(vb["filtrable"])
        self.assertEqual([r["clave"] for r in vb["requisitos"]],
                         ["ubicacion", "stock", "foto", "specs"])
        self.assertEqual(vb["requisitos"][3], {"clave": "specs", "titulo": "Specs",
                                               "estado": "por_definir", "n": None,
                                               "definicion": "Matriz por categoría sin definir"})
        self.assertTrue(vb["sub"]["filtrable"])
        full = _etapa(resp, "en_full")
        self.assertEqual({d["clave"]: d["n"] for d in full["desglose"]},
                         {"cumple_3de4": 20, "fuera_de_odoo": 0})
        self.assertEqual(_etapa(resp, "listo_envio")["desglose"][0]["n"], 60)
        self.assertEqual(resp["carril"]["n"], 5)
        self.assertEqual(resp["universo"]["n"], 101)


# ─────────────────────────────────────────────────────────────────────────────
# ODOO: LAS LECTURAS NUEVAS LANZAN · PACKING: UN SOLO HILO DE CALENTADO
# ─────────────────────────────────────────────────────────────────────────────

def _almacen_falso(modelo, metodo, args, kwargs=None, **_):
    if modelo == "stock.warehouse":
        return ([{"id": 1, "name": "DROP OFF", "view_location_id": [50, "DROP"]}]
                if args[0][0][2] == "DROP" else [])
    if modelo == "stock.quant":
        assert ["location_id", "child_of", 50] in args[0]
        return [{"id": 1, "product_id": [1, "[abc-0001-neg] Uno"]},
                {"id": 2, "product_id": [2, "[XYZ-0002-NEG] Dos"]},
                {"id": 3, "product_id": [3, "Sin código"]}]
    raise AssertionError(f"{modelo}.{metodo}")


class LecturasNuevasDeOdoo(unittest.TestCase):
    def _sin_via_vieja(self):
        # `estado_almacen` no debe tocar la vía sin timeout ni el `_uid()` que
        # guarda `None` en caché.
        return (mock.patch.object(odoo, "_uid", side_effect=AssertionError("usó _uid()")),
                mock.patch.object(odoo, "_models",
                                  side_effect=AssertionError("usó _models() sin timeout")))

    def test_estado_almacen_lanza_sin_lectura_buena(self):
        a, b = self._sin_via_vieja()
        with a, b, mock.patch.object(odoo, "_kw_flujo",
                                     side_effect=TimeoutError("sin respuesta")), \
                mock.patch.dict(odoo._almacen_cache, {}, clear=True):
            with self.assertRaises(TimeoutError):
                odoo.estado_almacen("DROP")

    def test_estado_almacen_que_no_existe_lanza(self):
        with mock.patch.object(odoo, "_kw_flujo", side_effect=_almacen_falso), \
                mock.patch.dict(odoo._almacen_cache, {}, clear=True):
            with self.assertRaises(RuntimeError):
                odoo.estado_almacen("NOPE")

    def test_estado_almacen_devuelve_la_edad_de_su_cache(self):
        a, b = self._sin_via_vieja()
        with a, b, mock.patch.object(odoo, "_kw_flujo",
                                     side_effect=TimeoutError("sin respuesta")), \
                mock.patch.dict(odoo._almacen_cache,
                                {"DROP": (time.monotonic() - 2000, ["A", "B"])}, clear=True):
            skus, edad = odoo.estado_almacen("drop")
        self.assertEqual(skus, {"A", "B"})
        self.assertGreaterEqual(edad, 2000)

    def test_estado_almacen_lee_con_timeout_y_comparte_la_cache(self):
        a, b = self._sin_via_vieja()
        with a, b, mock.patch.object(odoo, "_kw_flujo", side_effect=_almacen_falso) as kw, \
                mock.patch.dict(odoo._almacen_cache, {}, clear=True):
            skus, edad = odoo.estado_almacen("drop")
            self.assertEqual((skus, edad), ({"ABC-0001-NEG", "XYZ-0002-NEG"}, 0.0))
            self.assertEqual(kw.call_count, 2)
            # `skus_por_almacen` (productos.py) ve la misma caché sin preguntar.
            self.assertEqual(odoo.skus_por_almacen("DROP"), ["ABC-0001-NEG", "XYZ-0002-NEG"])
            # Y con la caché fresca, `estado_almacen` tampoco vuelve a preguntar.
            odoo.estado_almacen("DROP")
            self.assertEqual(kw.call_count, 2)

    def test_skus_por_almacen_sigue_igual(self):
        falso = mock.MagicMock()
        falso.execute_kw.side_effect = (
            lambda db, uid, pw, modelo, metodo, args, kwargs=None:
            _almacen_falso(modelo, metodo, args, kwargs))
        with mock.patch.object(odoo, "_uid", return_value=1), \
                mock.patch.object(odoo, "_models", return_value=falso), \
                mock.patch.dict(odoo._almacen_cache, {}, clear=True):
            self.assertEqual(odoo.skus_por_almacen("drop"), ["ABC-0001-NEG", "XYZ-0002-NEG"])
            self.assertEqual(odoo.skus_por_almacen("NOPE"), [])
            self.assertNotIn("NOPE", odoo._almacen_cache)
            # Falla con caché: sirve lo último bueno, como siempre.
            odoo._almacen_cache["DROP"] = (time.monotonic() - 5000, ["VIEJO"])
            falso.execute_kw.side_effect = OSError("red")
            self.assertEqual(odoo.skus_por_almacen("DROP"), ["VIEJO"])

    def test_existencias_por_id(self):
        with mock.patch.object(odoo, "_kw_flujo") as kw:
            self.assertEqual(odoo.existencias_por_id([]), {})
            kw.assert_not_called()
            kw.return_value = [{"id": 7, "qty_available": 3.0, "free_qty": False}]
            self.assertEqual(odoo.existencias_por_id([7, 7]), {7: (3.0, 3.0)})
            args, kwargs = kw.call_args[0], kw.call_args[1]
            self.assertEqual(args[3]["context"], {"active_test": False})
            self.assertEqual(args[3]["fields"], ["qty_available", "free_qty"])
            self.assertIn("timeout", kwargs)
            kw.return_value = []
            with self.assertRaises(RuntimeError):
                odoo.existencias_por_id([7])

    def test_vacios_son_falla(self):
        for fn in (odoo.catalogo_productos, odoo.quants_internos_por_producto,
                   odoo.ids_con_stock_libre, odoo.ids_con_foto):
            with mock.patch.object(odoo, "_kw_flujo", return_value=[]):
                with self.assertRaises(RuntimeError, msg=fn.__name__):
                    fn()

    def test_sin_url_lanza(self):
        with mock.patch.object(odoo.settings, "odoo_url", ""):
            with self.assertRaises(RuntimeError):
                odoo._proxy_con_tiempo("object", 5)


class PackingUnSoloHilo(unittest.TestCase):
    def setUp(self):
        packing_cajas._calentando.clear()
        packing_cajas._cola.clear()
        self.addCleanup(packing_cajas._calentando.clear)
        self.addCleanup(packing_cajas._cola.clear)

    def test_paginas_seguidas_no_lanzan_hilos_sin_tope(self):
        suelta = threading.Event()
        activos = {"ahora": 0, "max": 0}
        lotes: list[list[str]] = []
        candado = threading.Lock()

        def resolver(skus):
            with candado:
                activos["ahora"] += 1
                activos["max"] = max(activos["max"], activos["ahora"])
            lotes.append(list(skus))
            suelta.wait(5)
            with candado:
                activos["ahora"] -= 1
            return {}

        with mock.patch.object(packing_cajas, "_resolver", side_effect=resolver):
            packing_cajas.por_sku(["P1-A", "P1-B"])
            time.sleep(0.05)
            packing_cajas.por_sku(["P2-A"])
            packing_cajas.por_sku(["P3-A", "P1-A"])
            # Encolados, no perdidos: la fila sigue diciendo «leyendo».
            self.assertEqual(packing_cajas.calentando(["P1-A", "P2-A", "P3-A"]),
                             {"P1-A", "P2-A", "P3-A"})
            suelta.set()
            limite = time.monotonic() + 5
            while packing_cajas.calentando(["P1-A", "P2-A", "P3-A"]) and \
                    time.monotonic() < limite:
                time.sleep(0.02)
        self.assertEqual(activos["max"], 1, "nunca dos calentados a la vez")
        self.assertEqual(lotes, [["P1-A", "P1-B"], ["P2-A", "P3-A"]],
                         "lo encolado se funde en UNA pasada")
        self.assertEqual(packing_cajas.calentando(["P1-A", "P2-A", "P3-A"]), set())
        # El semáforo queda libre para el siguiente (el hilo lo suelta un
        # instante después de vaciar `_calentando`).
        limite = time.monotonic() + 5
        libre = False
        while not libre and time.monotonic() < limite:
            libre = packing_cajas._semaforo.acquire(blocking=False)
            if not libre:
                time.sleep(0.02)
        self.assertTrue(libre)
        packing_cajas._semaforo.release()


if __name__ == "__main__":
    unittest.main(verbosity=2)
