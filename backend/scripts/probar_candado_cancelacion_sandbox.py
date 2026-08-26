"""
probar_candado_cancelacion_sandbox.py — El candado de la CANCELACION sobrevive
al corte.

QUE SE ARREGLO
--------------
`pedidos_ml` pregunta `_ya_compensado` en DOS lugares. Uno le pasaba la cuenta y
el numero de pedido; el otro —el de la cancelacion, linea 567— solo el `wc_id`.
Y sin cuenta ni order_id la funcion **ni intenta kubera**: se va derecho al MySQL
que estamos retirando, con bandera encendida o sin ella.

El dia del corte eso contestaba `False` por el `except`. Sin esa respuesta no se
toma la foto previa, sin foto no corre la reversion, y Woo se queda con piezas
que repuso dos veces.

QUE PRUEBA ESTO
---------------
El sandbox corre con `MYSQL_ENABLED=false`: es el mundo de despues del corte, sin
simular nada. Con un pedido compensado sembrado en kubera:

  forma ARREGLADA (con cuenta y order_id) -> True   ... contesta bien sin MySQL
  forma VIEJA     (solo wc_id)            -> False  ... el defecto, a la vista

La segunda no es un fallo de la prueba: es la demostracion de por que habia que
cambiar la llamada. Si algun dia las dos dan lo mismo, alguien le devolvio a
`_ya_compensado` un camino a MySQL y hay que mirar por que.

Deja el sandbox como lo encontro.

Uso:
  ...python backend/scripts/probar_candado_cancelacion_sandbox.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CANAL, _CUENTA, _EXT, _WC = "mercado_libre", "PRUEBA-CANDADO", "999-prueba-cancel", 99999901
_ok = True


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def main() -> None:
    S = cargar("env.staging")
    os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["SUPABASE_READ_CANDADOS"] = "true"
    os.environ["MYSQL_ENABLED"] = "false"          # el mundo de despues del corte
    from services import pedidos_ml                # noqa: E402
    from config import settings                    # noqa: E402
    print(f"sandbox · MYSQL_ENABLED={settings.mysql_enabled} · "
          f"READ_CANDADOS={settings.supabase_read_candados}\n")
    if settings.mysql_enabled:
        sys.exit("ABORT: MYSQL_ENABLED quedo encendido; la prueba no valdria.")

    pg = psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=25)
    pg.autocommit = True
    try:
        with pg.cursor() as c:
            c.execute("""select count(*) from channel.orders
                          where canal=%s and cuenta=%s and external_order_id=%s""",
                      (_CANAL, _CUENTA, _EXT))
            if c.fetchone()[0]:
                sys.exit(f"ABORT: el sandbox ya tiene el pedido de prueba {_EXT}.")
            c.execute("""insert into channel.orders
                           (canal, cuenta, external_order_id, wc_order_id,
                            stock_compensado_at)
                         values (%s,%s,%s,%s, now())""",
                      (_CANAL, _CUENTA, _EXT, _WC))

        print("── pedido COMPENSADO sembrado en kubera, MySQL apagado ──")
        arreglada = pedidos_ml._ya_compensado(_WC, _CUENTA, _EXT)
        vieja = pedidos_ml._ya_compensado(_WC)
        check("forma ARREGLADA (wc_id + cuenta + order_id) contesta True",
              arreglada is True, f"devolvio {arreglada!r}")
        check("forma VIEJA (solo wc_id) contesta False — el defecto",
              vieja is False, f"devolvio {vieja!r}; si dice True es que "
                              f"_ya_compensado recupero un camino a MySQL")

        print("\n── y tras REVERTIR, el mismo pedido vuelve a ser compensable ──")
        with pg.cursor() as c:
            c.execute("""update channel.orders set stock_revertido_at = now()
                          where canal=%s and cuenta=%s and external_order_id=%s""",
                      (_CANAL, _CUENTA, _EXT))
        tras_revertir = pedidos_ml._ya_compensado(_WC, _CUENTA, _EXT)
        check("compensado y luego revertido -> False", tras_revertir is False,
              f"devolvio {tras_revertir!r}")
    finally:
        with pg.cursor() as c:
            c.execute("""delete from channel.orders
                          where canal=%s and cuenta=%s and external_order_id=%s""",
                      (_CANAL, _CUENTA, _EXT))
        pg.close()
        print("\n(sandbox devuelto a como estaba)")

    print(f"\nRESULTADO: {'todo en verde' if _ok else 'HAY FALLAS'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
