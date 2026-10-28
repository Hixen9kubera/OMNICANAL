"""
probar_kubera_primero_sandbox.py — KUBERA PRIMERO y la señal «venta nueva en kubera» (PLAN MAESTRO 2.11) contra el
SANDBOX de Supabase, con el código real (`pedidos_ml._registrar_venta` → `orders_write.guardar` → `kubera_mirror`).
Todo en UNA transacción que termina en ROLLBACK: no deja nada. Nunca toca producción (guardia de ref), ni Woo, ni
MySQL. De `env.staging` solo lee SUPABASE_DB_URL y SUPABASE_PROD_REF (ese archivo trae credenciales de producción).

Lo que las pruebas unitarias no pueden ver —el SQL de verdad contra un Postgres—:
  K1. Registro ANTES de Woo (sin pedido): `nueva_en_kubera`, líneas con su FULL, todas_full false.
  K2. Woo contestó: el segundo registro ya no es nueva y deja el wc_order_id.
  K3. Un re-aviso sin pedido NO vuelve a NULL el wc_order_id (coalesce).
  K4. Cancelar con una línea no FULL → `vendible_cambia`.
  K5. Descancelar → `vendible_cambia` otra vez.
  K6. El esqueleto del reclamo se completa una vez (`orders_write.reclamar` real) y el encabezado queda lleno.
  K7. Un esqueleto de hace 40 días que se completa NO es nueva (el planeador no crea OV vieja), pero sí cambia
      el vendible (el fan-out se entera). Revisión de MAESTRO, #2.
  K8. `estados_wc` y la marca de agua (`ultimo_actualizado`) no cuentan las filas sin pedido. Revisión, #1.
  K10. `liberar` solo borra un esqueleto: una venta completa sin pedido sobrevive (segunda revisión, #8).
  K9. El reintento de una venta «Woo pendiente» (acta 2.11, B0.6), por `pedidos_ml.sincronizar` real con un Woo
      falso: el aviso con Woo caído la deja completa sin pedido y no la suelta; el reintento con Woo caído tampoco;
      con kubera primero apagado (reversa) ni el reintento ni un aviso nuevo la liberan; con Woo de vuelta el
      reintento crea el pedido por la rama huérfana. La señal de venta nueva sale una sola vez.
  La foto con `for no key update` corre en cada registro (K1–K7, K9).

Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_kubera_primero_sandbox.py
"""
from __future__ import annotations

import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REF_KUBERA_PROD = "tukwcvsi"


def _env_staging() -> dict[str, str]:
    """Solo las dos líneas que se necesitan; el resto del archivo no se lee a memoria."""
    for p in (ROOT / "env.staging", ROOT.parent / "OMNICANAL" / "env.staging"):
        if p.exists():
            d = {}
            for s in p.read_text(encoding="utf-8").splitlines():
                k, _, v = s.partition("=")
                if k.strip() in ("SUPABASE_DB_URL", "SUPABASE_PROD_REF"):
                    d[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
            return d
    sys.exit("ABORT: no encontré env.staging (ni en la raíz ni en ../OMNICANAL).")


S = _env_staging()
m = re.search(r"postgres\.([a-z0-9]+):", S.get("SUPABASE_DB_URL", ""))
ref = m.group(1) if m else ""
if not ref or ref.startswith(REF_KUBERA_PROD) or ref == S.get("SUPABASE_PROD_REF", "").strip():
    sys.exit("ABORT: el destino no es el sandbox. Aborto.")

from config import settings  # noqa: E402
from services import kubera_mirror, orders_write, pedidos_ml  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

cn = psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=30)
cn.autocommit = False
CUR = cn.cursor()          # cursor de tuplas, como el del pool
resultados: list[bool] = []


def check(nombre: str, paso: bool, detalle: str = "") -> None:
    resultados.append(paso)
    print(f"  [{'PASA' if paso else 'FALLA'}] {nombre}" + (f" — {detalle}" if detalle else ""), flush=True)


@contextmanager
def get_cursor():
    yield CUR              # sin commit: todo muere en el ROLLBACK


def fetch_one(sql, params=None):
    CUR.execute(sql, params)
    fila = CUR.fetchone()
    return dict(zip([d[0] for d in CUR.description], fila)) if fila else None


def fetch_all(sql, params=None):
    CUR.execute(sql, params)
    return [dict(zip([d[0] for d in CUR.description], f)) for f in CUR.fetchall()]


def ex(sql, p=None):
    CUR.execute(sql, p)
    return CUR.fetchall()


ORDEN = {"id": "ZZZ-KPRIMERO-1", "cuenta": "BEKURA", "estado": "paid", "total": 499.0, "es_full": False,
         "items": [{"sku": "ACC-0001-AZL", "cantidad": 2, "item_id": "MLM1", "precio_unitario": 249.5,
                    "comision_ml": 40.0, "titulo": "prueba"},
                   {"sku": "ACC-0002-DOR", "cantidad": 1, "item_id": "MLM2", "precio_unitario": 0,
                    "comision_ml": 0, "titulo": "prueba"}]}
AHORA = datetime.now(timezone.utc)
parches = [mock.patch.object(sdb, "get_cursor", get_cursor),
           mock.patch.object(sdb, "fetch_one", fetch_one),
           mock.patch.object(sdb, "fetch_all", fetch_all),
           mock.patch.object(sdb, "disponible", return_value=True),
           mock.patch.object(orders_write, "activo", return_value=True),
           mock.patch.object(kubera_mirror, "activo", return_value=True),
           mock.patch.object(settings, "orders_espejo_inverso", False)]
for p in parches:
    p.start()
try:
    oid = ORDEN["id"]

    def reg(estado, wc, orden=ORDEN, ident=oid, skus=("ACC-0001-AZL", "ACC-0002-DOR")):
        return pedidos_ml._registrar_venta(ident, dict(orden, id=ident), estado, wc, 80.0, list(skus), AHORA, AHORA)

    s1 = reg("processing", None)
    check("K1 registro antes de Woo: nueva_en_kubera", s1["nueva_en_kubera"] and not s1["vendible_cambia"])
    check("K1 líneas con su FULL; todas_full false",
          [ln["sku"] for ln in s1["lineas"]] == ["ACC-0001-AZL", "ACC-0002-DOR"] and s1["todas_full"] is False
          and s1["wc_order_id"] is None)
    s2 = reg("processing", 777)
    check("K2 Woo contestó: ya no es nueva; wc 777", not s2["nueva_en_kubera"] and not s2["vendible_cambia"]
          and s2["wc_order_id"] == 777)
    check("K3 un re-aviso sin pedido no vuelve a NULL el wc", reg("processing", None)["wc_order_id"] == 777)
    s4 = reg("cancelled", 777)
    check("K4 cancelar con línea no FULL → vendible_cambia", s4["vendible_cambia"] and not s4["nueva_en_kubera"])
    check("K5 descancelar → vendible_cambia", reg("processing", 777)["vendible_cambia"])
    gano = orders_write.reclamar("mercado_libre", "BEKURA", "ZZZ-KPRIMERO-2")
    esq = ex("select skus is null, comision is null from channel.orders where external_order_id = 'ZZZ-KPRIMERO-2'")
    check("K6 el reclamo deja un esqueleto", gano and esq == [(True, True)], str(esq))
    s6 = reg("processing", None, ident="ZZZ-KPRIMERO-2", skus=("ACC-0001-AZL",))
    f = ex("select skus::text, comision from channel.orders where external_order_id = 'ZZZ-KPRIMERO-2'")[0]
    check("K6 el esqueleto se completa una vez (nueva) con su encabezado",
          s6["nueva_en_kubera"] and f[0] is not None and f[1] is not None, str(f))
    ex("""insert into channel.orders (canal, cuenta, external_order_id, creado_at, registrado_at, actualizado_at)
          values ('mercado_libre', 'BEKURA', 'ZZZ-KPRIMERO-3', now() - interval '40 days',
                  now() - interval '40 days', now() - interval '40 days') returning 1""")
    s7 = reg("processing", None, ident="ZZZ-KPRIMERO-3", skus=("ACC-0001-AZL",))
    check("K7 un esqueleto de hace 40 días no es nueva pero sí cambia el vendible",
          not s7["nueva_en_kubera"] and s7["vendible_cambia"], str({k: s7[k] for k in ("nueva_en_kubera",
                                                                                      "vendible_cambia")}))
    ex("""insert into channel.orders (canal, cuenta, external_order_id, creado_at, actualizado_at)
          values ('mercado_libre', 'BEKURA', 'ZZZ-KPRIMERO-4', now(), now() + interval '1 day') returning 1""")
    previos = orders_write.estados_wc(("BEKURA",))
    check("K8 estados_wc trae la venta con pedido y no las que no lo tienen",
          "ZZZ-KPRIMERO-1" in previos and "ZZZ-KPRIMERO-2" not in previos and "ZZZ-KPRIMERO-4" not in previos)
    marca = orders_write.ultimo_actualizado("BEKURA")
    futura = ex("select actualizado_at from channel.orders where external_order_id = 'ZZZ-KPRIMERO-4'")[0][0]
    check("K8 la marca de agua no cuenta la fila sin pedido (aunque sea la más nueva)",
          marca is not None and marca < futura.astimezone(timezone.utc).replace(tzinfo=None), f"{marca} < {futura}")

    # K9 · El reintento de una venta «Woo pendiente» (acta 2.11, B0.6), por `pedidos_ml.sincronizar` REAL: el
    # reclamo, el registro y la señal van contra este Postgres; Woo es un falso que primero no contesta.
    import asyncio

    from services import alertas, fanout_stock

    class _Resp:
        def __init__(self, cuerpo):
            self.status_code, self._c, self.text = 201, cuerpo, ""

        def json(self):
            return self._c

    class _Woo:
        caido = True

        def __call__(self, *a, **k):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, ruta, json=None):
            if self.caido:
                raise pedidos_ml.httpx.ConnectError("Woo no contesta (simulado)")
            return _Resp({"id": 888})

    woo, encolados = _Woo(), []
    liberar_real = orders_write.liberar
    liberados: list = []
    payload = {"status": "processing", "_skus": ["ACC-0001-AZL", "ACC-0002-DOR"], "_sin_mapear": [],
               "_comision": 80.0}
    k9 = "ZZZ-KPRIMERO-9"
    parches_k9 = [
        mock.patch.object(pedidos_ml, "_schema_ok", True),
        mock.patch.object(pedidos_ml, "construir_payload", mock.AsyncMock(side_effect=lambda *a, **k: dict(payload))),
        mock.patch.object(pedidos_ml, "_pedido_en_woo", mock.AsyncMock(return_value=None)),
        mock.patch.object(pedidos_ml, "_ya_compensado", return_value=False),
        mock.patch.object(pedidos_ml.httpx, "AsyncClient", woo),
        mock.patch.object(pedidos_ml.asyncio, "sleep", mock.AsyncMock()),
        mock.patch.object(orders_write, "liberar",
                          side_effect=lambda *a: (liberados.append(a), liberar_real(*a))[1]),
        mock.patch.object(fanout_stock, "encolar_varios", side_effect=lambda skus, **k: encolados.append(skus)),
        mock.patch.object(fanout_stock, "seguro_encendido", return_value=False),
        mock.patch("services.odoo_ventas.canales_posibles", return_value=()),
        mock.patch.object(alertas, "avisar", return_value=True),
        mock.patch.object(settings, "fanout_fuente", "kubera"),
    ]
    for p in parches_k9:
        p.start()
    try:
        def sinc(kubera_primero=True, reintentable=True):
            with mock.patch.object(settings, "pedidos_kubera_primero", kubera_primero):
                return asyncio.run(pedidos_ml.sincronizar(k9, orden=dict(ORDEN, id=k9), reintentable=reintentable))

        def fila9():
            f = ex("select skus is null, wc_order_id, estado_wc from channel.orders where external_order_id = %s",
                   (k9,))
            return f[0] if f else None

        r = sinc(reintentable=False)                                   # el aviso: Woo no contesta
        check("K9 aviso con Woo caído: la venta queda completa en kubera, sin pedido y con su estado; no se suelta",
              not r["ok"] and fila9() == (False, None, 'processing') and not liberados, str(fila9()))
        check("K9 aviso: la señal de venta nueva sale UNA vez, con los SKUs de la orden (no es FULL)",
              encolados == [["ACC-0001-AZL", "ACC-0002-DOR"]], str(encolados))
        r = sinc()                                                     # el reintento, Woo sigue caído
        check("K9 reintento con Woo caído: la venta sigue, sin `liberar` y sin repetir la señal",
              not r["ok"] and fila9() == (False, None, 'processing') and not liberados and len(encolados) == 1)
        sinc(kubera_primero=False)                                     # reversa: el reintento sin la bandera
        sinc(kubera_primero=False, reintentable=False)                 # reversa: un aviso nuevo sin la bandera
        check("K9 reversa (kubera primero apagado): ni el reintento ni un aviso nuevo la liberan",
              fila9() == (False, None, 'processing') and not liberados, str(liberados))
        woo.caido = False
        r = sinc()                                                     # el reintento, Woo ya contesta
        check("K9 reintento con Woo de vuelta: pedido 888 por la rama huérfana, una sola fila, señal sin repetir",
              r["ok"] and fila9() == (False, 888, "processing") and len(encolados) == 1
              and ex("select count(*) from channel.orders where external_order_id = %s", (k9,))[0][0] == 1,
              str((r.get("ok"), fila9(), encolados)))
    finally:
        for p in parches_k9:
            p.stop()

    # K10 · `liberar` solo suelta un ESQUELETO (segunda revisión de MAESTRO, #8): una venta completa sin pedido
    # (kubera primero, «Woo pendiente») no se borra aunque alguien llame a `liberar` con su llave.
    orders_write.liberar("mercado_libre", "BEKURA", k9)
    orders_write.reclamar("mercado_libre", "BEKURA", "ZZZ-KPRIMERO-10")
    reg("processing", None, ident="ZZZ-KPRIMERO-11", skus=("ACC-0001-AZL",))
    orders_write.liberar("mercado_libre", "BEKURA", "ZZZ-KPRIMERO-11")
    orders_write.liberar("mercado_libre", "BEKURA", "ZZZ-KPRIMERO-10")
    vivas = {r[0] for r in ex("select external_order_id from channel.orders where external_order_id in "
                              "('ZZZ-KPRIMERO-9', 'ZZZ-KPRIMERO-10', 'ZZZ-KPRIMERO-11')")}
    check("K10 liberar borra el esqueleto y no toca la venta completa sin pedido",
          vivas == {"ZZZ-KPRIMERO-9", "ZZZ-KPRIMERO-11"}, str(sorted(vivas)))
finally:
    for p in parches:
        p.stop()
    cn.rollback()
    cn.close()

with psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=15) as cn2, cn2.cursor() as c2:
    c2.execute("set transaction read only")
    c2.execute("select count(*) from channel.orders where external_order_id like 'ZZZ-KPRIMERO-%%'")
    quedan = c2.fetchone()[0]
    cn2.rollback()
check("tras el ROLLBACK no queda ninguna fila de prueba", quedan == 0, str(quedan))
print(f"\nRESULTADO: {sum(resultados)}/{len(resultados)} PASAN")
sys.exit(0 if all(resultados) else 1)
