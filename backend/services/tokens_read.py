"""
tokens_read.py — Los tokens de Mercado Libre, del lado de kubera (PASO 6).

Gemelas de las cuatro consultas de `meli.py` que hoy hablan con el MySQL que
estamos retirando (`ml_tokens` y `ml_tokens_dashboard`).

LO QUE ESTÁ EN JUEGO
--------------------
Medido el 19-ago con `probar_corte_total.py`: sin MySQL, `meli._access_token()`
devuelve `None`. Y sin token no hay API de Mercado Libre — se van las ventas por
webhook, publicar, el sync de inventario y competencia. **Es el bloqueador más
grande que le queda al retiro del esquema**, y resultó ser código nuestro, no de
un tercero (ver docs/BARRIDO_LECTORES.md).

LA REGLA QUE NO SE PUEDE ROMPER
-------------------------------
**Mercado Libre ROTA el `refresh_token` en cada uso.** Dos procesos que renueven
no se "desincronizan": se invalidan mutuamente, y el siguiente refresh de
cualquiera de los dos muere con `invalid_grant`. De ahí sale toda la forma de
este módulo:

  · ESCRIBIR en los dos lados es SEGURO — es el mismo valor, calculado una vez.
  · RENOVAR desde los dos lados, NO. Por eso aquí no hay ninguna función que
    llame a la API de ML: este módulo solo guarda y lee. El único que renueva
    sigue siendo `meli.refrescar_token`, y sigue siendo uno solo.

ARBITRAJE POR RECENCIA
----------------------
`meli._access_token` compara `updated_at` entre las dos tablas de MySQL y se
queda con la más nueva, porque un tercero podía haber renovado. Esa misma
comparación se conserva aquí extendida a kubera: gana el más reciente de los
tres, venga de donde venga. Mientras haya doble escritura, empatan; el día que
MySQL se apague, kubera gana por default sin que nadie cambie nada.

NUNCA IMPRIME UN TOKEN
----------------------
Ni completo ni parcial. Las funciones de diagnóstico devuelven fechas y huellas
SHA-256 cortas, que sirven para saber si un valor CAMBIÓ sin revelarlo. Es el
mismo criterio de `verificar_tokens_ml.py`.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.tokens_read")


def huella(valor: str | None) -> str:
    """8 hex del SHA-256. Para saber si CAMBIÓ, nunca para reconstruirlo."""
    if not valor:
        return "—"
    return hashlib.sha256(valor.encode()).hexdigest()[:8]


def leer(cuenta: str | None = None) -> dict[str, Any] | None:
    """
    { cuenta, access_token, refresh_token, updated_at } — el más reciente.

    Los tokens vuelven **cifrados**, tal como están guardados: quien llame los
    descifra con la llave Fernet del backend. Así este módulo nunca tiene un
    token en claro en memoria y no hay forma de que se le escape a un log.
    """
    if cuenta:
        return sdb.fetch_one(
            """select cuenta, access_token, refresh_token, updated_at
                 from ops.ml_tokens where cuenta = %s
                order by updated_at desc nulls last limit 1""", (cuenta,))
    return sdb.fetch_one(
        """select cuenta, access_token, refresh_token, updated_at
             from ops.ml_tokens
            order by updated_at desc nulls last limit 1""")


def guardar(cuenta: str, access_token: str, refresh_token: str,
            cuando: datetime | None = None) -> int:
    """
    Guarda el par cifrado. Los dos valores SIEMPRE juntos, nunca uno solo.

    Es a propósito: ML los devuelve juntos en la misma respuesta de refresh, y
    guardar solo uno dejaría un par que no existe —un `access_token` nuevo con
    el `refresh_token` que ya se quemó al pedirlo—. La próxima renovación
    fallaría con `invalid_grant` y nadie sabría por qué.
    """
    # GUARDA CONTRA EL CIFRADO QUE NO OCURRIO.
    # `meli._enc(f, v)` devuelve `v` TAL CUAL cuando no hay llave Fernet — o sea
    # que si `DB_ENCRYPTION_KEY` faltara, `refrescar_token` escribiria los tokens
    # EN CLARO, en MySQL y aqui, sin un solo error. Es el mismo patron que este
    # proyecto persigue desde el principio: una capacidad ausente degradando a un
    # comportamiento equivocado pero callado.
    #
    # Medido el 19-ago: las 4 filas de produccion estan cifradas (prefijo Fernet).
    # Asi que si hay llave configurada y el valor NO viene cifrado, algo se rompio
    # y NO se guarda. Sin llave (ambientes de desarrollo) se deja pasar avisando,
    # porque ahi no hay nada que proteger.
    from config import settings as _s
    en_claro = [n for n, v in (("access", access_token), ("refresh", refresh_token))
                if not str(v or "").startswith("gAAAAA")]
    if en_claro:
        if _s.db_encryption_key:
            raise ValueError(
                f"tokens_read.guardar({cuenta}): {'/'.join(en_claro)} NO viene "
                f"cifrado y SI hay DB_ENCRYPTION_KEY. No se guarda un token en "
                f"claro.")
        log.warning("tokens_read.guardar(%s): sin DB_ENCRYPTION_KEY, se guarda "
                    "%s sin cifrar (ambiente de desarrollo).",
                    cuenta, "/".join(en_claro))

    return sdb.execute(
        """insert into ops.ml_tokens (cuenta, access_token, refresh_token, updated_at)
           values (%s, %s, %s, coalesce(%s, now()))
           on conflict (cuenta) do update set
             access_token  = excluded.access_token,
             refresh_token = excluded.refresh_token,
             updated_at    = excluded.updated_at""",
        (cuenta, access_token, refresh_token, cuando))


def censo() -> list[dict[str, Any]]:
    """Para el arnés: fechas y huellas, jamás valores."""
    filas = sdb.fetch_all(
        "select cuenta, access_token, refresh_token, updated_at from ops.ml_tokens "
        "order by cuenta")
    return [{"cuenta": f["cuenta"], "updated_at": f["updated_at"],
             "h_access": huella(f["access_token"]),
             "h_refresh": huella(f["refresh_token"])} for f in filas]


# ═══════════════════════════════════════════════════════════════════════════
#  TIKTOK — el tercer almacen de credenciales (PASO 6b)
# ═══════════════════════════════════════════════════════════════════════════
# Aparecio en el triaje de los 95 `try/except`, no en el plan. Mismo molde que
# ML, con dos diferencias que importan:
#
# 1. NO hay arbitraje por recencia. En ML se compara `updated_at` entre tres
#    fuentes porque un tercero podia renovar; aqui el unico escritor es nuestro
#    `tiktok.refrescar_y_guardar`. Inventar un arbitraje donde no hay dos
#    escritores es agregar una pieza que nadie puede verificar.
#
# 2. Se guarda MAS que el token: `shop_cipher` es un parametro obligatorio de
#    casi toda la API, y sin el una conexion con token valido contesta
#    "shop_cipher is required". Perder esa columna rompe TikTok disfrazado de
#    problema de permisos.

def tiktok_leer(shop_id: str | None = None) -> dict[str, Any] | None:
    """La fila de una tienda, o la mas reciente. Los tokens vuelven CIFRADOS."""
    cols = ("shop_id, seller_name, open_id, shop_cipher, access_token, "
            "refresh_token, expira, refresh_expira, updated_at")
    if shop_id:
        return sdb.fetch_one(
            f"select {cols} from ops.tiktok_tokens where shop_id = %s",
            (str(shop_id),))
    return sdb.fetch_one(
        f"select {cols} from ops.tiktok_tokens order by updated_at desc limit 1")


def tiktok_listar() -> list[dict[str, Any]]:
    """Todas las tiendas, para el diagnostico. SIN tokens: solo fechas."""
    return sdb.fetch_all(
        """select shop_id, seller_name, expira, refresh_expira, updated_at
             from ops.tiktok_tokens order by updated_at desc""")


def tiktok_guardar(shop_id: str, access_token: str, *, seller_name=None,
                   open_id=None, shop_cipher=None, refresh_token=None,
                   expira=None, refresh_expira=None,
                   cuando: datetime | None = None) -> int:
    """Guarda o actualiza una tienda.

    `shop_cipher` se conserva si viene vacio (`coalesce`): TikTok no siempre lo
    devuelve al renovar, y pisarlo con NULL dejaria la conexion con un token
    bueno y sin poder llamar a nada. Ese es exactamente el modo de fallo
    disfrazado que documenta la migracion 0024.
    """
    from config import settings as _s
    en_claro = [n for n, v in (("access", access_token), ("refresh", refresh_token))
                if v and not str(v).startswith("gAAAAA")]
    if en_claro:
        if _s.db_encryption_key:
            raise ValueError(
                f"tokens_read.tiktok_guardar({shop_id}): {'/'.join(en_claro)} NO "
                f"viene cifrado y SI hay DB_ENCRYPTION_KEY.")
        log.warning("tiktok_guardar(%s): sin DB_ENCRYPTION_KEY, %s sin cifrar.",
                    shop_id, "/".join(en_claro))
    return sdb.execute(
        """insert into ops.tiktok_tokens
             (shop_id, seller_name, open_id, shop_cipher, access_token,
              refresh_token, expira, refresh_expira, updated_at)
           values (%s,%s,%s,%s,%s,%s,%s,%s, coalesce(%s, now()))
           on conflict (shop_id) do update set
             seller_name    = coalesce(excluded.seller_name, tiktok_tokens.seller_name),
             open_id        = coalesce(excluded.open_id, tiktok_tokens.open_id),
             shop_cipher    = coalesce(excluded.shop_cipher, tiktok_tokens.shop_cipher),
             access_token   = excluded.access_token,
             refresh_token  = coalesce(excluded.refresh_token, tiktok_tokens.refresh_token),
             expira         = coalesce(excluded.expira, tiktok_tokens.expira),
             refresh_expira = coalesce(excluded.refresh_expira, tiktok_tokens.refresh_expira),
             updated_at     = excluded.updated_at""",
        (str(shop_id), seller_name, open_id, shop_cipher, access_token,
         refresh_token, expira, refresh_expira, cuando))


def tiktok_censo() -> list[dict[str, Any]]:
    """Fechas y huellas, jamas valores."""
    filas = sdb.fetch_all(
        "select shop_id, shop_cipher, access_token, refresh_token, updated_at "
        "from ops.tiktok_tokens order by shop_id")
    return [{"shop_id": f["shop_id"], "updated_at": f["updated_at"],
             "tiene_cipher": bool(f["shop_cipher"]),
             "h_access": huella(f["access_token"]),
             "h_refresh": huella(f["refresh_token"])} for f in filas]
