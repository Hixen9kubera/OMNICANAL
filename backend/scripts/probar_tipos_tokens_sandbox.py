"""
probar_tipos_tokens_sandbox.py — Las gemelas devuelven el MISMO TIPO de fecha
que MySQL, no solo el mismo valor.

POR QUE EXISTE
--------------
El 25-ago se encendio `SUPABASE_READ_TOKENS` en produccion y Mercado Libre se
quedo sin token durante ~3.5 minutos. El valor estaba perfecto; el TIPO no.

MySQL entrega `datetime` sin zona. Postgres entrega `timestamptz`, con zona.
Python no compara los dos. Y `meli._access_token` los mete en la misma lista:

    mejor = max(candidatos, key=lambda r: r["updated_at"])

Eso reventaba, el `except` de mas afuera se lo tragaba y la funcion devolvia
**None** — ningun token, no uno viejo.

Las pruebas que ya existian comparaban VALORES y por eso no lo vieron. Esta
compara TIPOS, y ademas reproduce el `max()` exacto que fallo: si un dia alguien
quita el `at time zone 'utc'`, esta prueba se pone roja antes que produccion.

Deja el sandbox como lo encontro.

Uso:
  ...python backend/scripts/probar_tipos_tokens_sandbox.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CUENTA = "PRUEBA-TIPOS"
_SHOP = "0000-prueba-tipos"
_FALSO = "gAAAAA-PRUEBA-NO-ES-UN-TOKEN-REAL"
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


def zona(v) -> str:
    if not isinstance(v, datetime):
        return "no-es-fecha"
    return "CON zona" if v.tzinfo is not None else "sin zona"


def main() -> None:
    E, S = cargar(".env"), cargar("env.staging")

    # ── Como vienen las fechas de MySQL (la referencia) ─────────────────────
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    with my.cursor() as c:
        c.execute("SELECT cuenta, access_token, updated_at FROM ml_tokens LIMIT 1")
        fila_my = c.fetchone()
    my.close()
    if not fila_my:
        sys.exit("ABORT: MySQL no tiene tokens de ML; sin referencia no hay prueba.")
    print(f"referencia MySQL: updated_at viene {zona(fila_my['updated_at'])}\n")

    # El backend tiene que apuntar al SANDBOX, no a produccion.
    import os
    os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["SUPABASE_READ_TOKENS"] = "true"
    os.environ["SUPABASE_WRITE_TOKENS"] = "true"
    from services import tokens_read  # noqa: E402

    pg = psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=25)
    pg.autocommit = True
    try:
        with pg.cursor() as c:
            for tabla, clave, val in (("ops.ml_tokens", "cuenta", _CUENTA),
                                      ("ops.tiktok_tokens", "shop_id", _SHOP)):
                c.execute(f"select count(*) from {tabla} where {clave}=%s", (val,))
                if c.fetchone()[0]:
                    sys.exit(f"ABORT: {tabla} ya tiene {val}; borralo antes de probar.")
            c.execute("""insert into ops.ml_tokens
                           (cuenta, access_token, refresh_token, updated_at)
                         values (%s,%s,%s, now())""", (_CUENTA, _FALSO, _FALSO))
            c.execute("""insert into ops.tiktok_tokens
                           (shop_id, seller_name, access_token, refresh_token,
                            expira, refresh_expira, updated_at)
                         values (%s,'PRUEBA',%s,%s, now()+interval '7 days',
                                 now()+interval '90 days', now())""",
                      (_SHOP, _FALSO, _FALSO))

        # ── 1. cada fecha que sale de las gemelas ───────────────────────────
        print("── 1. las fechas de kubera salen SIN zona, como MySQL ──")
        ml = tokens_read.leer(_CUENTA)
        check("leer(): updated_at sin zona", ml and zona(ml["updated_at"]) == "sin zona",
              zona(ml["updated_at"]) if ml else "no devolvio fila")
        cen = [f for f in tokens_read.censo() if f["cuenta"] == _CUENTA]
        check("censo(): updated_at sin zona",
              bool(cen) and zona(cen[0]["updated_at"]) == "sin zona")
        tt = tokens_read.tiktok_leer(_SHOP)
        for col in ("expira", "refresh_expira", "updated_at"):
            check(f"tiktok_leer(): {col} sin zona",
                  tt is not None and zona(tt[col]) == "sin zona",
                  zona(tt[col]) if tt else "no devolvio fila")
        li = [f for f in tokens_read.tiktok_listar() if str(f["shop_id"]) == _SHOP]
        for col in ("expira", "refresh_expira", "updated_at"):
            check(f"tiktok_listar(): {col} sin zona",
                  bool(li) and zona(li[0][col]) == "sin zona")
        tc = [f for f in tokens_read.tiktok_censo() if str(f["shop_id"]) == _SHOP]
        check("tiktok_censo(): updated_at sin zona",
              bool(tc) and zona(tc[0]["updated_at"]) == "sin zona")

        # ── 2. el max() que reviento en produccion ──────────────────────────
        print("\n── 2. el arbitraje por recencia de meli._access_token ──")
        candidatos = [ml, fila_my]           # kubera + MySQL, la mezcla exacta
        try:
            mejor = max(candidatos, key=lambda r: r["updated_at"])
            check("max() sobre kubera + MySQL no revienta", True,
                  f"gano {mejor.get('cuenta')}")
        except TypeError as exc:
            check("max() sobre kubera + MySQL no revienta", False, str(exc))

        # ── 3. la comparacion de vigencia de tiktok.estado ──────────────────
        print("\n── 3. la comparacion de vigencia de tiktok.estado ──")
        ahora = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            check("expira > ahora (naive) no revienta", bool(tt["expira"] > ahora),
                  f"vigente hasta {tt['expira']}")
        except TypeError as exc:
            check("expira > ahora (naive) no revienta", False, str(exc))
    finally:
        with pg.cursor() as c:
            c.execute("delete from ops.ml_tokens where cuenta=%s", (_CUENTA,))
            c.execute("delete from ops.tiktok_tokens where shop_id=%s", (_SHOP,))
        pg.close()
        print("\n(sandbox devuelto a como estaba)")

    print(f"\nRESULTADO: {'todo en verde' if _ok else 'HAY FALLAS'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
