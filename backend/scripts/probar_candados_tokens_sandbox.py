"""
probar_candados_tokens_sandbox.py — Los DOS que faltaban para el corte total:
el candado de bodega (PASO 0) y los tokens de ML (PASO 6).

Mismo método que los bloques del paso 3: el sandbox corre con
`MYSQL_ENABLED=false`, así que ya es el mundo de después del retiro.

POR QUÉ ESTOS DOS SON DISTINTOS DE TODO LO ANTERIOR
---------------------------------------------------
Los 25 lectores del paso 3 muestran cosas en una pantalla. Estos dos NO:

  · el candado decide si se **mueve mercancía**
  · los tokens deciden si hay **API de Mercado Libre**

Y los dos comparten el defecto que hace peligroso el corte: **contestan en vez
de fallar**. `_ya_procesada` sin `fanout_log` devuelve `False` —"no lo he
hecho"— y el movimiento se aplica otra vez. `_access_token` sin MySQL devuelve
`None` y las ventas paran sin un solo error en pantalla.

ESTE SCRIPT ESCRIBE EN EL SANDBOX
---------------------------------
Es la única forma de probar un candado: hay que sellarlo y volver a preguntar.
Todo lo que escribe lleva marcas `PRUEBA-` y se limpia al final. **Aborta si el
DSN es el de producción**, y los tokens que usa son cadenas inventadas — nunca
un token real, ni en memoria.

Uso:
  ...python backend/scripts/probar_candados_tokens_sandbox.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    if not p.exists():
        return d
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


_ST = cargar("env.staging")
if not _ST.get("SUPABASE_DB_URL"):
    sys.exit("ABORT: env.staging sin SUPABASE_DB_URL.")
os.environ["SUPABASE_DB_URL"] = _ST["SUPABASE_DB_URL"]
os.environ["MYSQL_ENABLED"] = "false"
os.environ["APP_ENV"] = "staging"

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION y este script ESCRIBE.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")

from services import candados_read, stock_full, tokens_read  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

_ok = True
_OP = "PRUEBA-op-candado-0023"
_CUENTA_T = "PRUEBA-CUENTA"


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def con(bandera: str, valor: bool, fn, *a, **k):
    previo = getattr(settings, bandera)
    setattr(settings, bandera, valor)
    try:
        return fn(*a, **k), None
    except Exception as exc:  # noqa: BLE001
        return None, exc
    finally:
        setattr(settings, bandera, previo)


def limpiar() -> None:
    sdb.execute("delete from ops.fulfillment_operations where operacion_id = %s", (_OP,))
    sdb.execute("delete from ops.fba_watermark where sku = %s", ("PRUEBA-SKU-FBA",))
    sdb.execute("delete from ops.ml_tokens where cuenta = %s", (_CUENTA_T,))


def main() -> None:
    print(f"SANDBOX {_ref[:8]}... · MYSQL_ENABLED={settings.mysql_enabled}")
    limpiar()

    # ══ PASO 0 — el candado de bodega ═══════════════════════════════════════
    print(f"\n{'═' * 70}\n  PASO 0 — el candado que decide si se mueve mercancia\n{'═' * 70}")

    v, e_v = con("supabase_read_candados", False, stock_full._ya_procesada, _OP)
    print(f"  _ya_procesada('{_OP}') con la bandera APAGADA y sin MySQL -> {v}")
    check("apagada sin MySQL: contesta FALSE, o sea «no lo he hecho»",
          e_v is None and v is False,
          "no truena: MIENTE. Y con esa mentira el movimiento se aplica otra vez")

    # 1. Antes de sellar, la respuesta correcta ES False.
    n, e_n = con("supabase_read_candados", True, candados_read.ya_aplicada, _OP)
    check("prendida, operacion nueva: False (correcto, nadie la ha aplicado)",
          e_n is None and n is False, str(e_n))

    # 2. Se sella y la respuesta tiene que cambiar. Esto es LO QUE IMPORTA:
    #    un candado que no recuerda no es un candado.
    candados_read.marcar_aplicada(_OP, "PRUEBA-SKU", "AMAZON", "fba_ingreso")
    n2, e2 = con("supabase_read_candados", True, stock_full._ya_procesada, _OP)
    check("tras sellarla, el candado RECUERDA", e2 is None and n2 is True,
          str(e2) if e2 else "True")

    # 3. Sellar dos veces no debe reventar (los webhooks de ML llegan en rafaga).
    try:
        candados_read.marcar_aplicada(_OP, "PRUEBA-SKU", "AMAZON", "fba_ingreso")
        check("sellar dos veces no truena (los avisos de ML llegan en rafaga)", True)
    except Exception as exc:  # noqa: BLE001
        check("sellar dos veces no truena", False, str(exc))

    # 4. LA RAMA QUE MAS IMPORTA: si la base no contesta, tiene que PROPAGAR.
    #    El `except -> False` de MySQL es lo que este paso viene a quitar.
    original = sdb.fetch_one
    sdb.fetch_one = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("BD caida (simulado)"))
    try:
        _, e3 = con("supabase_read_candados", True, stock_full._ya_procesada, _OP)
        check("con la BD caida PROPAGA, no contesta «no lo he hecho»",
              e3 is not None, f"{type(e3).__name__}" if e3 else "devolvio sin error")
    finally:
        sdb.fetch_one = original

    # 5. La marca de agua del FBA: una COLUMNA, no un texto parseado.
    candados_read.marcar_fba("PRUEBA-SKU-FBA", 42, cuenta="AMAZON")
    m = candados_read.marcas_fba(["PRUEBA-SKU-FBA"])
    check("la marca de agua guarda y devuelve un entero",
          m.get("PRUEBA-SKU-FBA") == 42, str(m))
    candados_read.marcar_fba("PRUEBA-SKU-FBA", 57, cuenta="AMAZON")
    m2 = candados_read.marcas_fba(["PRUEBA-SKU-FBA"])
    check("y AVANZA (siempre pisa: es marca de agua, no historial)",
          m2.get("PRUEBA-SKU-FBA") == 57, str(m2))

    # ══ PASO 6 — los tokens ═════════════════════════════════════════════════
    print(f"\n{'═' * 70}\n  PASO 6 — los tokens sin los que no hay API de ML\n{'═' * 70}")

    from services import meli
    v, e_v = con("supabase_read_tokens", False, meli._access_token, "BEKURA")
    print(f"  _access_token('BEKURA') apagada y sin MySQL -> {'None' if v is None else 'algo'}")
    check("apagada sin MySQL: None. Sin token no hay ventas, ni publicar, ni sync",
          e_v is None and v is None,
          "y tampoco truena — el panel se queda mudo sin decir por que")

    # Un par INVENTADO, cifrado con la misma llave del backend. Nunca uno real.
    f = meli._fernet()
    at, rt = meli._enc(f, "PRUEBA-access-no-es-un-token"), meli._enc(f, "PRUEBA-refresh")
    tokens_read.guardar(_CUENTA_T, at, rt)
    fila = tokens_read.leer(_CUENTA_T)
    check("guardar+leer conserva el par", bool(fila) and fila["access_token"] == at
          and fila["refresh_token"] == rt)
    # El cifrado depende de que exista `DB_ENCRYPTION_KEY`, y en el sandbox no
    # esta. Medido el 19-ago: las 4 filas de PRODUCCION si estan cifradas.
    # Lo que se prueba aqui es la GUARDA, que es lo que hay que probar: si hay
    # llave y el valor no viene cifrado, `guardar` tiene que negarse.
    hay_llave = bool(settings.db_encryption_key)
    print(f"     DB_ENCRYPTION_KEY en este ambiente: {hay_llave} "
          f"(en produccion SI esta: las 4 filas traen prefijo Fernet)")
    try:
        tokens_read.guardar(_CUENTA_T, "esto-no-esta-cifrado", "tampoco")
        nego = False
    except ValueError:
        nego = True
    check("guardar() se NIEGA a escribir un token en claro si hay llave",
          nego == hay_llave,
          "sin llave deja pasar avisando (desarrollo); con llave aborta"
          if not hay_llave else "abortó como debe")
    tokens_read.guardar(_CUENTA_T, at, rt)   # restaurar el par de prueba
    check("el descifrado del backend lo recupera",
          meli._dec(f, fila["access_token"]) == "PRUEBA-access-no-es-un-token")

    # El par SIEMPRE junto: un access nuevo con el refresh viejo ya quemado es
    # el bug que se descubre una semana despues con `invalid_grant`.
    at2 = meli._enc(f, "PRUEBA-access-2")
    tokens_read.guardar(_CUENTA_T, at2, meli._enc(f, "PRUEBA-refresh-2"))
    fila2 = tokens_read.leer(_CUENTA_T)
    check("re-guardar pisa los DOS valores, no uno",
          fila2["access_token"] == at2
          and meli._dec(f, fila2["refresh_token"]) == "PRUEBA-refresh-2")
    check("y avanza updated_at (de eso vive el arbitraje por recencia)",
          fila2["updated_at"] >= fila["updated_at"],
          f"{fila['updated_at']} -> {fila2['updated_at']}")

    # El arbitraje: gana el mas reciente. Se prueba con una fila vieja.
    viejo_ts = datetime.now(timezone.utc) - timedelta(days=3)
    tokens_read.guardar(_CUENTA_T, meli._enc(f, "PRUEBA-viejo"),
                        meli._enc(f, "PRUEBA-refresh-viejo"), cuando=viejo_ts)
    fila3 = tokens_read.leer(_CUENTA_T)
    check("una escritura con fecha vieja NO se queda con una fecha nueva",
          abs((fila3["updated_at"].replace(tzinfo=timezone.utc) - viejo_ts).total_seconds()) < 5,
          f"{fila3['updated_at']}")

    # El censo no puede filtrar nada.
    c = tokens_read.censo()
    filtrado = [x for x in c if any(str(v or "").startswith("gAAAAA") for v in x.values())]
    check("el censo NO expone tokens, solo fechas y huellas", not filtrado,
          str(c[:1]))

    # El prerrequisito que hay que decir en voz alta.
    print(f"\n  MELI_APP_ID definido      : {bool(settings.meli_app_id)}")
    print(f"  MELI_CLIENT_SECRET definido: {bool(settings.meli_client_secret)}")
    if not (settings.meli_app_id and settings.meli_client_secret):
        print("  ⚠ En este ambiente no estan, y sin ellas `_credenciales_refresh` no")
        print("    puede renovar leyendo de kubera. Es PRERREQUISITO en Railway.")

    limpiar()
    print(f"\n  (limpieza: las filas PRUEBA- se borraron del sandbox)")
    print(f"\nRESULTADO: {'los dos candados y los tokens viven sin MySQL' if _ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
