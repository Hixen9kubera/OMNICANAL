"""
probar_cable_autoria.py — Comprueba que el nombre de quien pide llega hasta los
triggers de historial, y que NO se le pega a la conexión del pooler.

POR QUÉ NO ES UNA PRUEBA DE CI
------------------------------
Necesita una base. Se corre a mano contra el SANDBOX antes de tocar producción.
La prueba que sí vive en CI es `verificar_rls.py`, que no necesita nada.

POR QUÉ EXISTE LA PRUEBA 3
--------------------------
Es la que de verdad importa. `set_config(..., true)` es LOCAL a la transacción;
si alguien lo cambiara por un `SET` de sesión, el nombre se quedaría pegado a una
conexión COMPARTIDA del pooler 6543 y lo heredaría el siguiente cliente — o sea,
la venta que registre el backend quedaría firmada por la última persona que usó
el panel. Es el mismo mecanismo del candado de solo-lectura que tumbó las
escrituras en agosto, pero en vez de romper, MIENTE. Y una bitácora que miente se
consulta igual y se le cree.

Correr contra el sandbox, con el pooler transaccional (6543), que es donde la
fuga sería posible:

    export SUPABASE_DB_URL=...   # el del sandbox (env.staging)
    backend/.venv/Scripts/python.exe backend/scripts/probar_cable_autoria.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import actor          # noqa: E402
from services import supabase_db as sdb   # noqa: E402

# `where false` no toca una sola fila, pero es una sentencia de ESCRITURA de
# verdad: sirve para probar el camino que dispara los triggers sin ensuciar nada.
NO_OP_ESCRITURA = "update ops.process_log set estado = estado where false"

fallas = 0


def _check(nombre: str, obtenido, esperado) -> None:
    global fallas
    ok = obtenido == esperado
    if not ok:
        fallas += 1
    print(f"  [{'OK ' if ok else 'MAL'}] {nombre}: {obtenido!r} (esperado {esperado!r})")


def leer_marca() -> str:
    with sdb.get_cursor() as cur:
        cur.execute("select coalesce(current_setting('app.usuario', true), '(vacio)') as u")
        return cur.fetchone()["u"]


def main() -> int:
    if not os.environ.get("SUPABASE_DB_URL"):
        print("ABORTO: falta SUPABASE_DB_URL (usa el del sandbox).", file=sys.stderr)
        return 2

    print("=== 1. con actor, la transacción lo ve ===")
    actor.fijar("prueba@kubera.mx")
    _check("dentro de la transacción", leer_marca(), "prueba@kubera.mx")

    print("\n=== 2. sin actor, no se manda nada ===")
    actor.limpiar()
    # Ojo con esperar "(vacio)" exacto: la prueba 1 ya tocó el GUC en esa
    # conexión del pool, así que a partir de aquí Postgres lo reporta como
    # cadena VACÍA en vez de "sin definir". Lo que se mide es que no haya nombre.
    _check("sin actor no queda nombre", leer_marca() in ("", "(vacio)"), True)

    print("\n=== 3. ANTI-FUGA: el nombre no sobrevive a su transacción ===")
    actor.fijar("no-debe-fugarse@kubera.mx")
    leer_marca()          # transacción marcada, ya commiteada
    actor.limpiar()
    vistos = {leer_marca() for _ in range(8)}
    # No se exige "(vacio)" exacto: Postgres deja el GUC en cadena VACÍA (no sin
    # definir) una vez que se tocó en la sesión. Lo que se exige es que ninguna
    # conexión nueva vea el nombre de la persona anterior.
    _check("8 cursores nuevos no ven el nombre anterior",
           all(v in ("", "(vacio)") for v in vistos), True)
    print(f"        valores crudos: {vistos}")

    print("\n=== 4. sobrevive el salto a un hilo (core.actor.en_hilo) ===")

    async def en_hilo():
        actor.fijar("desde-el-hilo@kubera.mx")
        caja: dict[str, str] = {}
        listo = threading.Event()

        def trabajo():
            caja["visto"] = leer_marca()
            listo.set()

        actor.en_hilo(trabajo)
        await asyncio.get_running_loop().run_in_executor(None, listo.wait, 30)
        return caja.get("visto")

    _check("dentro del hilo", asyncio.run(en_hilo()), "desde-el-hilo@kubera.mx")

    print("\n=== 5. control: run_in_executor pelado SÍ pierde el contexto ===")

    async def pelado():
        actor.fijar("no-deberia-verse@kubera.mx")
        caja: dict[str, str] = {}

        def trabajo():
            caja["visto"] = actor.actual()

        await asyncio.get_running_loop().run_in_executor(None, trabajo)
        return caja.get("visto")

    crudo = asyncio.run(pelado())
    _check("el hilo pelado no ve al actor (por eso existe en_hilo)", crudo, "")

    print("\n=== 6. una ESCRITURA queda firmada ===")
    actor.fijar("escribe@kubera.mx")
    with sdb.get_cursor() as cur:
        cur.execute(NO_OP_ESCRITURA)
        cur.execute("select current_setting('app.usuario', true) as u")
        _check("firma visible en la transacción que escribe",
               cur.fetchone()["u"], "escribe@kubera.mx")
    actor.limpiar()

    print("\n=== 7. la COLA del espejo kubera conserva la firma ===")
    # El caso que casi se escapa, y el que más importa: por aquí van las ALTAS
    # DE PRODUCTO. `kubera_mirror` no lanza el trabajo, lo ENCOLA, y lo recoge un
    # hilo daemon que arrancó mucho antes de que existiera esta petición. Y
    # encima usa su PROPIO pool, que no pasa por `supabase_db.get_cursor`. Eran
    # dos fugas encadenadas: la primera pierde el contexto, la segunda nunca
    # manda la firma. Con cualquiera de las dos, las creaciones salen sin firmar.
    #
    # Necesita KUBERA_DB_URL apuntando al SANDBOX y el espejo encendido; si no,
    # se omite en vez de fallar.
    import time
    from services import kubera_mirror as km

    if not (os.environ.get("KUBERA_DB_URL") and km.activo("crear_logs")):
        print("  [--] omitida: falta KUBERA_DB_URL o el espejo está apagado")
    else:
        sku_p = "ZZ-PRUEBA-COLA"
        actor.fijar("prueba.cola@kubera.mx")
        km.espejar("prueba", "probar_cable_autoria", "crear_logs",
                   "ops.process_log", "insert",
                   {"sku": sku_p, "estado": "ok", "paso": "alta de prueba"},
                   clave=sku_p)
        actor.limpiar()          # la petición se fue; el worker sigue trabajando
        fila = None
        for _ in range(40):
            time.sleep(0.5)
            fila = sdb.fetch_one(
                "select actor from ops.process_log where sku::text = %s "
                "order by id desc limit 1", (sku_p,))
            if fila:
                break
        _check("la fila que escribió el hilo de la cola va firmada",
               (fila or {}).get("actor"), "prueba.cola@kubera.mx")
        sdb.execute("delete from ops.process_log where sku::text = %s", (sku_p,))
        print("        (fila de prueba borrada)")

    print(f"\nRESULTADO: {'TODO BIEN' if fallas == 0 else f'{fallas} FALLA(S)'}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
