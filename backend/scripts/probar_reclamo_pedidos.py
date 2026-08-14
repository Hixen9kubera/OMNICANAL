"""
probar_reclamo_pedidos.py — Pruebas del RECLAMO ANTES DE CREAR contra el SANDBOX
de Supabase. Nunca toca la BD kubera de producción (guardia de ref) ni MySQL.

QUÉ SE PRUEBA. El 14-ago-2026 la orden 2000017937146172 quedó duplicada en Woo
(#123068 y #123069, 3 s de diferencia) UN SEGUNDO después del relevo de
contenedores de un deploy: `pedidos_ml._locks` vive en la memoria de un proceso
y no cruza al otro, y el registro se escribía DESPUÉS de crear en Woo, así que
al proceso viejo lo mataron con el pedido ya creado y sin rastro en kubera.

`orders_write.reclamar` gana el derecho a crear con un insert atómico sobre la
PK (canal, cuenta, external_order_id), que sí cruza procesos.

  R1. El primero GANA el reclamo.
  R2. El segundo lo PIERDE (aunque `wc_order_id` siga NULL): es lo que impide
      el segundo pedido.
  R3. Ganar deja la fila reclamada con wc_order_id NULL — la señal de
      "reclamado, aún sin pedido".
  R4. `liberar` suelta un reclamo SIN pedido (si no, el siguiente aviso vería
      "ya reclamada" y la venta se perdería en silencio).
  R5. `liberar` NO borra una fila YA COMPLETADA — sería borrar una venta.
  R6. Tras completar, el reclamo deja de estar libre y `wc_order_id_previo`
      contesta el id real (el camino "ya existía": actualizar, no crear).

Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_reclamo_pedidos.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REF_KUBERA_PROD = "tukwcvsi"
CANAL, CUENTA, ORDEN = "mercado_libre", "BEKURA", "ZZZ-RECLAMO-1"


def env(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    for s in p.read_text(encoding="utf-8").splitlines():
        if s.strip() and not s.strip().startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return d


S = env("env.staging")
resultados: list[bool] = []


def check(nombre: str, paso: bool, detalle: str = "") -> None:
    resultados.append(paso)
    print(f"  [{'PASA' if paso else 'FALLA'}] {nombre}"
          + (f" — {detalle}" if detalle else ""), flush=True)


def guardia_sandbox(url: str) -> str:
    m = re.search(r"postgres\.([a-z0-9]+):", url or "")
    ref = m.group(1) if m else ""
    if not ref:
        sys.exit("ABORT: no pude extraer ref del sandbox.")
    if ref.startswith(REF_KUBERA_PROD) or ref == S.get("SUPABASE_PROD_REF", "").strip():
        sys.exit("ABORT: el destino no es el sandbox. Aborto.")
    return ref


def q(sql: str, params=()):
    with psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=15) as cn:
        with cn.cursor() as c:
            c.execute(sql, params)
            if c.description:
                cols = [d[0] for d in c.description]
                return [dict(zip(cols, r)) for r in c.fetchall()]
    return None


def limpiar() -> None:
    q("delete from channel.orders where external_order_id = %s", (ORDEN,))


def main() -> None:
    ref = guardia_sandbox(S["SUPABASE_DB_URL"])
    print(f"RECLAMO ANTES DE CREAR contra sandbox {ref[:8]}…\n", flush=True)
    os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["SUPABASE_WRITE_ORDERS"] = "true"

    from services import orders_write
    assert orders_write.activo(), "el corte de orders no quedó activo en sandbox"

    limpiar()
    try:
        # R1 / R2 — el reclamo es exclusivo
        check("R1 el primero GANA el reclamo",
              orders_write.reclamar(CANAL, CUENTA, ORDEN) is True)
        check("R2 el segundo lo PIERDE (esto evita el 2º pedido)",
              orders_write.reclamar(CANAL, CUENTA, ORDEN) is False)

        # R3 — la fila reclamada nace sin pedido
        f = q("""select wc_order_id from channel.orders
                  where canal=%s and cuenta=%s and external_order_id=%s""",
              (CANAL, CUENTA, ORDEN))
        check("R3 el reclamo deja wc_order_id NULL",
              len(f) == 1 and f[0]["wc_order_id"] is None,
              f"filas={len(f)} wc_order_id={f[0]['wc_order_id'] if f else '—'}")

        # R4 — soltar un reclamo sin pedido
        orders_write.liberar(CANAL, CUENTA, ORDEN)
        f = q("select 1 from channel.orders where external_order_id=%s", (ORDEN,))
        check("R4 liberar suelta el reclamo SIN pedido", not f)
        check("R4b tras liberar, se puede volver a reclamar",
              orders_write.reclamar(CANAL, CUENTA, ORDEN) is True)

        # R5 — completar y comprobar que liberar ya NO borra
        q("""update channel.orders set wc_order_id=999001, estado_wc='processing'
              where canal=%s and cuenta=%s and external_order_id=%s""",
          (CANAL, CUENTA, ORDEN))
        orders_write.liberar(CANAL, CUENTA, ORDEN)
        f = q("""select wc_order_id from channel.orders
                  where external_order_id=%s""", (ORDEN,))
        check("R5 liberar NO borra una venta ya completada",
              len(f) == 1 and f[0]["wc_order_id"] == 999001,
              f"wc_order_id={f[0]['wc_order_id'] if f else '—'}")

        # R6 — el camino "ya existía"
        check("R6 el reclamo sigue tomado tras completar",
              orders_write.reclamar(CANAL, CUENTA, ORDEN) is False)
        check("R6b wc_order_id_previo contesta el id real",
              orders_write.wc_order_id_previo(ORDEN) == 999001,
              f"devolvió {orders_write.wc_order_id_previo(ORDEN)}")
    finally:
        limpiar()

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)}")
    sys.exit(0 if ok == len(resultados) else 1)


if __name__ == "__main__":
    main()
