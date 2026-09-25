"""
probar_reintentos_ml_sandbox.py — Los reintentos de avisos de venta de ML y el
indicador de /flujo, contra la tabla de verdad (`ops.webhook_events` del sandbox).

LO QUE LAS PRUEBAS UNITARIAS NO PUEDEN DECIR
--------------------------------------------
Que el SQL hace lo que dice: que la espera queda escrita en la fila, que el tope
agota, que la ventana de 48 h deja fuera lo viejo, que "resolver previos" toca
solo los ANTERIORES de la misma orden, y que el indicador cuenta lo que debe.

NADA DE ESTO TOCA MERCADO LIBRE NI WOO
--------------------------------------
`meli.obtener_orden` y `pedidos_ml.sincronizar` están SIMULADOS. Las órdenes son
números inventados (99900000000000xx) y todas las filas llevan `delivery_id`
'PRUEBA-REINT-…'; se borran al final. Aborta si el DSN es el de producción.

Uso (desde la raíz, con `env.staging` ahí):
  python backend/scripts/probar_reintentos_ml_sandbox.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.update({
    "APP_ENV": "staging",
    "MYSQL_ENABLED": "false",
    "SUPABASE_DUAL_WRITE": "true",   # el receptor escribe su fila en ops.webhook_events
    "SYNC_ENABLED": "false",          # sin refresco de ítems: no hay ML de verdad
    "PEDIDOS_WC_ENABLED": "true",
    "ML_WEBHOOK_REINTENTOS_TOPE": "10",
    "ML_WEBHOOK_REINTENTOS_MAX_CREADOS_HORA": "2",   # el freno, chico para verlo actuar
})

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if not settings.supabase_db_url:
    sys.exit("ABORT: env.staging sin SUPABASE_DB_URL.")
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION y este script ESCRIBE.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")

from routers import flujo  # noqa: E402
from routers import webhooks as wh  # noqa: E402
from services import meli, pedidos_ml  # noqa: E402
from services import ml_webhook_reintentos as mr  # noqa: E402
from services import supabase_db as sdb  # noqa: E402
from services import tiktok_webhook_reintentos as tr  # noqa: E402

_ok = True
_PREF = "PRUEBA-REINT-"
_ORD = [f"99900000000000{n:02d}" for n in range(1, 8)]


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f" — {detalle}" if detalle else ""))


def fila(ev_id: int) -> dict:
    return sdb.fetch_one(
        """select procesado, intentos, left(coalesce(resultado,''), 70) resultado,
                  extract(epoch from (next_retry_at - now()))::int espera_s
             from ops.webhook_events where id = %s""", (ev_id,))


def insertar(canal: str, topic: str, external_id: str, n: int, *, procesado=False,
             intentos=0, espera_s: int | None = None, horas_atras: float = 0.0) -> int:
    return int(sdb.execute_returning(
        """insert into ops.webhook_events (env, canal, topic, external_id, delivery_id,
                  payload, procesado, intentos, next_retry_at, recibido_at)
           values (%s, %s, %s, %s, %s, '{}'::jsonb, %s, %s,
                   case when %s::int is null then null
                        else now() + make_interval(secs => %s::int) end,
                   now() - make_interval(secs => %s::int))
           returning id""",
        (settings.app_env, canal, topic, external_id, f"{_PREF}{n}", procesado, intentos,
         espera_s, espera_s, int(horas_atras * 3600)))["id"])


def id_por_delivery(d: str) -> int:
    return int(sdb.fetch_one("select id from ops.webhook_events where delivery_id = %s", (d,))["id"])


def kpi() -> dict:
    return dict(sdb.fetch_one(flujo._SQL_WEBHOOKS_ACCIONABLES, ()))


def limpiar() -> int:
    return sdb.execute("delete from ops.webhook_events where delivery_id like %s", (_PREF + "%",))


_venta_ok = {"ok": True, "wc_order_id": 424242, "accion": "creado", "estado_wc": "processing"}
_venta_falla = {"ok": False, "motivo": "error al crear pedido: [Errno -2] Name or service not known"}

print(f"Sandbox {_ref[:8]}… · ML y Woo simulados · env={settings.app_env}\n")
_INICIO = sdb.fetch_one("select now() t")["t"]
limpiar()
antes = kpi()
try:
    orden_ml = mock.patch.object(meli, "obtener_orden",
                                 new=mock.AsyncMock(return_value={"items": []})).start()
    sinc = mock.patch.object(pedidos_ml, "sincronizar", new=mock.AsyncMock()).start()

    print("1. El receptor: la venta que falla queda PENDIENTE")
    sinc.return_value = _venta_falla
    asyncio.run(wh._procesar_ml(None, {"_id": f"{_PREF}r1", "resource": f"/orders/{_ORD[0]}",
                                       "topic": "orders_v2", "user_id": 1}))
    f1 = fila(id_por_delivery(f"{_PREF}r1"))
    check("procesado=false, 1 intento, reintento en ~2 min",
          not f1["procesado"] and f1["intentos"] == 1 and 100 <= (f1["espera_s"] or 0) <= 120,
          str(f1))
    check("el resultado dice por qué", f1["resultado"].startswith("reintentar · venta"),
          f1["resultado"])

    print("\n2. Un aviso POSTERIOR de la misma orden sale bien → el anterior queda resuelto")
    sinc.return_value = _venta_ok
    asyncio.run(wh._procesar_ml(None, {"_id": f"{_PREF}r2", "resource": f"/orders/{_ORD[0]}",
                                       "topic": "orders_v2", "user_id": 1}))
    f1b, f2 = fila(id_por_delivery(f"{_PREF}r1")), fila(id_por_delivery(f"{_PREF}r2"))
    check("el que falló: resuelto por el posterior",
          f1b["procesado"] and f1b["resultado"].startswith("resuelto por un aviso posterior"),
          f1b["resultado"])
    check("el que salió: procesado con su pedido", f2["procesado"] and "#424242" in f2["resultado"])

    print("\n3. …pero NO resuelve un fallo que llegó DESPUÉS")
    tarde = insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[1]}", 3, intentos=1, espera_s=600)
    sinc.return_value = _venta_ok
    asyncio.run(wh._procesar_ml(None, {"_id": f"{_PREF}r3b", "resource": f"/orders/{_ORD[1]}",
                                       "topic": "orders_v2", "user_id": 1}))
    # El aviso bueno tiene id MAYOR; se simula el orden inverso con uno insertado después:
    posterior = insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[1]}", 4, intentos=1,
                         espera_s=600)
    asyncio.run(wh._procesar_ml(None, {"_id": f"{_PREF}r3c", "resource": f"/orders/{_ORD[1]}",
                                       "topic": "orders_v2", "user_id": 1}))
    check("el anterior quedó resuelto", fila(tarde)["procesado"])
    ok_c = id_por_delivery(f"{_PREF}r3c")
    check("el fallo con id menor al último bueno también (llegó antes que ese)",
          fila(posterior)["procesado"] and posterior < ok_c)
    despues = insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[1]}", 5, intentos=1,
                       espera_s=600)
    mr.resolver_previos(f"/orders/{_ORD[1]}", ok_c)
    check("uno que llegó DESPUÉS del bueno sigue pendiente", not fila(despues)["procesado"])

    print("\n4. El reprocesador: una pasada por orden, espera creciente, tope y ventana")
    a1 = insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[2]}", 6, intentos=1, espera_s=-60)
    a2 = insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[2]}", 7, intentos=2, espera_s=-30)
    b1 = insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[3]}", 8, intentos=3, espera_s=-60)
    tope_ = insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[4]}", 9, intentos=9, espera_s=-60)
    viejo = insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[5]}", 10, intentos=1,
                     espera_s=-60, horas_atras=49)
    futuro = insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[6]}", 11, intentos=1,
                      espera_s=900)
    sinc.reset_mock()
    orden_ml.reset_mock()
    sinc.side_effect = lambda oid, **k: _venta_ok if oid == _ORD[2] else _venta_falla
    r = asyncio.run(mr.reprocesar())
    llamadas = sorted(c.args[0] for c in sinc.await_args_list)
    check("una sola pasada por orden, con el candado de los sondeos",
          llamadas == sorted([_ORD[2], _ORD[3], _ORD[4]])
          and all(c.kwargs.get("reintentable") for c in sinc.await_args_list), str(llamadas))
    check("la orden que sale: sus dos avisos quedan procesados",
          fila(a1)["procesado"] and fila(a2)["procesado"]
          and "reintento ok · pedido WC #424242" in fila(a1)["resultado"])
    fb = fila(b1)
    check("la que falla: 4.º intento, espera de 16 min", not fb["procesado"] and fb["intentos"] == 4
          and 900 <= (fb["espera_s"] or 0) <= 960, str(fb))
    ft = fila(tope_)
    check("al tope: agotada, sin próximo reintento", not ft["procesado"] and ft["intentos"] == 10
          and ft["espera_s"] is None and ft["resultado"].startswith("agotado"), str(ft))
    check("la de hace 49 h no se toca", fila(viejo)["intentos"] == 1)
    check("la que aún no vence no se toca", fila(futuro)["intentos"] == 1)
    print(f"     resumen: {r}")

    print("\n5. TikTok: el aviso que sale bien resuelve los fallos previos de su orden")
    t_prev = insertar("tiktok", "tiktok.1", "585000000000000001", 12, intentos=1, espera_s=-60)
    t_ok = insertar("tiktok", "tiktok.1", "585000000000000001", 13)
    tr.marcar(t_ok, {"ok": True, "accion": "actualizado"})
    check("el previo quedó resuelto", fila(t_prev)["procesado"])

    print("\n6. El indicador de /flujo cuenta solo lo que pide atención")
    base = kpi()
    insertar("tiktok", "tiktok.68", "tipo:68", 14)                       # informativo
    insertar("tiktok", "tiktok.1", "585000000000000002", 15, horas_atras=1)   # interrumpido
    insertar("mercado_libre", "items", "/items/MLM1", 16, horas_atras=0.1)     # en curso (<15 min)
    insertar("mercado_libre", "orders_v2", f"/orders/{_ORD[6]}", 17, intentos=2,
             espera_s=-3600)                                                   # vencido
    d = {k: kpi()[k] - base[k] for k in base}
    check("el aviso de producto de TikTok NO cuenta; el interrumpido sí; el en curso no",
          d["interrumpidos"] == 1, str(d))
    check("el reintento vencido cuenta como vencido", d["por_reintentar"] == 1 and d["vencidos"] == 1,
          str(d))
    texto, estado = flujo._salud_webhooks(kpi())
    print(f"     /flujo diría: «{texto}» → {estado}")

    print("\n7. El FRENO: límite de pedidos creados por hora (aquí 2), guardado en la bitácora")
    from services import alertas
    from services import reintentos_freno as fr
    avisos = mock.patch.object(alertas, "avisar").start()
    fr.FRENO_ML.liberar()                                  # arranque limpio
    limpiar()                                              # sin pendientes de las secciones previas
    evs = [insertar("mercado_libre", "orders_v2", f"/orders/99900000000009{n}", 20 + n,
                    intentos=1, espera_s=-60) for n in range(4)]
    sinc.reset_mock()
    sinc.side_effect = lambda oid, **k: {"ok": True, "wc_order_id": 777, "accion": "creado",
                                         "estado_wc": "processing"}
    r7 = asyncio.run(mr.reprocesar())
    check("crea 2 y se detiene ahí", sinc.await_count == 2 and r7["estado"] == "frenado"
          and r7["creados"] == 2, str(r7))
    check("las otras 2 ventas siguen pendientes, a la vista",
          sum(1 for e in evs if not fila(e)["procesado"]) == 2)
    check("avisa a Slack una sola vez", avisos.call_count == 1,
          (avisos.call_args.args[1][:80] if avisos.called else "sin aviso"))
    reinicio = fr.Freno("ml", "ml_webhook_reintentos_max_creados_hora")
    check("un REINICIO no lo quita: la bitácora dice detenido", reinicio.detenido() is not None)
    sinc.reset_mock()
    r7b = asyncio.run(mr.reprocesar())
    check("mientras está detenido no toca nada", sinc.await_count == 0 and r7b["estado"] == "frenado")
    fr.FRENO_ML.liberar()
    check("liberado: la bitácora ya no lo detiene", reinicio.detenido() is None)
    sinc.side_effect = lambda oid, **k: {"ok": True, "wc_order_id": 777, "accion": "actualizado",
                                         "estado_wc": "processing"}
    r7c = asyncio.run(mr.reprocesar())
    check("liberado, sigue con las pendientes (las actualizaciones no cuentan)",
          r7c["estado"] == "ok" and all(fila(e)["procesado"] for e in evs), str(r7c))
finally:
    mock.patch.stopall()
    sdb.execute("delete from ops.process_log where proceso = 'reintentos_freno' "
                "and created_at >= %s", (_INICIO,))
    n = limpiar()
    print(f"\nLimpieza: {n} filas de prueba borradas · quedan "
          f"{sdb.fetch_one('select count(*) n from ops.webhook_events where delivery_id like %s', (_PREF + '%',))['n']}")

print("\nRESULTADO:", "TODO PASA" if _ok else "HAY FALLAS")
sys.exit(0 if _ok else 1)
