"""
tokens_kubera.py — Los tokens de acceso de Mercado Libre y TikTok, LEÍDOS de kubera.

Esos dos canales no tienen credencial fija: su token dura horas y producción lo
renueva y lo guarda cifrado en `ops.ml_tokens` / `ops.tiktok_tokens`. Para leer el
canal en vivo hay que tomar el token vigente de ahí. Autorizado por Brandon el
6-oct-2026, expresamente para esta lectura.

LO QUE ESTE ARCHIVO NO HACE, Y NO DEBE HACER NUNCA:

  · RENOVAR. Usar el refresh_token de ML lo ROTA: producción se quedaría con uno
    muerto y pararían las ventas. Por eso aquí ni siquiera se lee esa columna.
  · Guardar. El token vive en memoria lo que dura la corrida; no va a disco ni a
    un log, y `huella()` existe para poder decir «cambió» sin mostrarlo.

Si un token ya caducó, se RELEE una vez (por si producción renovó entretanto). Si
sigue sin servir, el canal se reporta como NO LEÍDO. No se insiste.

REGLA 13 DE LA CASA: una consulta y se cierra. Nada de marcar la sesión.
"""
from __future__ import annotations

import hashlib
from typing import Any

from comun import Cfg

_ML = """
select distinct on (cuenta) cuenta, access_token,
       (updated_at at time zone 'utc') as actualizado
  from ops.ml_tokens
 order by cuenta, updated_at desc nulls last
"""
_TIKTOK = """
select shop_id, seller_name, shop_cipher, access_token,
       (expira at time zone 'utc') as expira,
       (updated_at at time zone 'utc') as actualizado
  from ops.tiktok_tokens
 order by updated_at desc
 limit 1
"""


def _consultar(cfg: Cfg, consulta: str) -> list[dict[str, Any]]:
    import psycopg2
    import psycopg2.extras

    dsn = cfg("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("falta SUPABASE_DB_URL (la base kubera)")
    cn = psycopg2.connect(dsn, connect_timeout=25)
    try:
        cn.autocommit = True
        with cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(consulta)
            return [dict(f) for f in cur.fetchall()]
    finally:
        cn.close()


def _descifrar(cfg: Cfg, valor: Any) -> str | None:
    if not isinstance(valor, str) or not valor:
        return None
    if not valor.startswith("gAAAAA"):
        return valor                          # ya venía en claro
    llave = cfg("DB_ENCRYPTION_KEY")
    if not llave:
        return None
    from cryptography.fernet import Fernet

    try:
        return Fernet(llave.encode()).decrypt(valor.encode()).decode()
    except Exception:  # noqa: BLE001 — sin token legible el canal no se lee, y ya
        return None


def huella(valor: str | None) -> str:
    return hashlib.sha256(valor.encode()).hexdigest()[:8] if valor else "—"


def mercado_libre(cfg: Cfg) -> dict[str, dict[str, Any]]:
    """{cuenta: {"acceso": <token>, "actualizado": fecha}} — el más reciente de cada una."""
    salida: dict[str, dict[str, Any]] = {}
    for f in _consultar(cfg, _ML):
        acceso = _descifrar(cfg, f.get("access_token"))
        if acceso and f.get("cuenta"):
            salida[str(f["cuenta"]).upper()] = {"acceso": acceso,
                                                "actualizado": str(f.get("actualizado"))}
    return salida


def tiktok(cfg: Cfg) -> dict[str, Any] | None:
    filas = _consultar(cfg, _TIKTOK)
    if not filas:
        return None
    f = filas[0]
    acceso = _descifrar(cfg, f.get("access_token"))
    if not acceso:
        return None
    return {"acceso": acceso, "cifra_tienda": f.get("shop_cipher"),
            "tienda": f.get("seller_name"), "expira": str(f.get("expira")),
            "actualizado": str(f.get("actualizado"))}
