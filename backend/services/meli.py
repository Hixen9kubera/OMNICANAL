"""
meli.py — Servicio de Mercado Libre (canal principal).

Estrategia híbrida:
  - LECTURA RÁPIDA (UI): join en MySQL de
        productos  +  ml_progress (item_id, url, publicado)
                   +  costos_finales (precio_sugerido, ml_cat_id, comisión)
  - REFRESCO EN VIVO: con el token de `ml_tokens` consultamos la API de ML
        /items/{id}  → precio, available_quantity, logistic_type (FULL), category_id
        /categories/{id} → path_from_root (todos los niveles de categoría)

El "FULL" de Mercado Libre = logistic_type == "fulfillment".
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

import httpx

from config import settings
from services import db

log = logging.getLogger("omnicanal.meli")

_API = "https://api.mercadolibre.com"

# ── Lectura desde el cache (MySQL) ────────────────────────────────────────────

# Mercado Libre opera 2 cuentas: BEKURA (Kubera, default) y SANCORFASHION (San Corpe).
# La consulta parte de ml_progress (los LISTINGS reales de la cuenta) y enriquece
# con la tabla maestra `productos` y con costos_finales. Así el conteo de la
# pestaña coincide con lo realmente publicado en esa cuenta.
_SQL_LISTAR = """
    SELECT  mp.sku                                      AS sku,
            COALESCE(p.wc_id, mp.wc_id)                 AS wc_id,
            p.odoo_id                                   AS odoo_id,
            COALESCE(p.nombre, mp.sku)                  AS nombre,
            p.stock_odoo                                AS stock_odoo,
            p.categorias                                AS categorias,
            COALESCE(cf.precio_sugerido, p.precio)      AS precio,
            cf.precio_base                              AS precio_base,
            cf.ml_cat_id                                AS ml_cat_id,
            mp.cuenta                                   AS cuenta,
            mp.ml_item_id                               AS ml_item_id,
            mp.ml_url                                   AS ml_url,
            mp.success                                  AS publicado
    FROM ml_progress mp
    LEFT JOIN productos      p  ON p.sku  = mp.sku
    LEFT JOIN costos_finales cf ON cf.sku = mp.sku
    WHERE (%(cuenta)s IS NULL OR mp.cuenta = %(cuenta)s)
      AND (%(solo_publicados)s = 0 OR mp.success = 1)
      AND (%(search)s IS NULL OR p.nombre LIKE %(like)s OR mp.sku LIKE %(like)s)
      __ESTADO__
      __SKUS__
    ORDER BY __ORDEN__
    LIMIT %(limit)s OFFSET %(offset)s
"""

_SQL_COUNT = """
    SELECT COUNT(*) AS total
    FROM ml_progress mp
    LEFT JOIN productos p ON p.sku = mp.sku
    WHERE (%(cuenta)s IS NULL OR mp.cuenta = %(cuenta)s)
      AND (%(solo_publicados)s = 0 OR mp.success = 1)
      AND (%(search)s IS NULL OR p.nombre LIKE %(like)s OR mp.sku LIKE %(like)s)
      __ESTADO__
      __SKUS__
"""


def _clausula_skus(skus_filtro: list[str] | None, prefijo: str) -> tuple[str, dict[str, Any]]:
    """
    Arma "AND (p.nombre LIKE %(sku_0)s OR mp.sku LIKE %(sku_0)s OR ...)" para la
    lista de términos de "Filtrar SKUs" (separados por coma en el frontend; cada
    uno filtra Y busca a la vez: SKU completo, parcial o palabra del nombre).
    """
    terminos = [t.strip() for t in (skus_filtro or []) if t.strip()]
    if not terminos:
        return "", {}
    piezas: list[str] = []
    params: dict[str, Any] = {}
    for i, t in enumerate(terminos):
        clave = f"{prefijo}{i}"
        piezas.append(f"(p.nombre LIKE %({clave})s OR mp.sku LIKE %({clave})s)")
        params[clave] = f"%{t}%"
    return f"AND ({' OR '.join(piezas)})", params


def _normalizar(row: dict[str, Any]) -> dict[str, Any]:
    publicado = bool(row.get("publicado"))
    return {
        "sku": row["sku"],
        "wc_id": row.get("wc_id"),
        "odoo_id": row.get("odoo_id"),
        "nombre": row.get("nombre") or row["sku"],
        "precio": _f(row.get("precio")),
        "precio_base": _f(row.get("precio_base")),
        "stock": row.get("stock_odoo"),
        "estado": "activo" if publicado else "sin publicar",
        "categoria_id": row.get("ml_cat_id"),
        # La ruta completa se resuelve bajo demanda vía API (cacheable);
        # de momento mostramos el id de categoría de ML.
        "categoria_path": [],
        "publicado": publicado,
        "item_id": row.get("ml_item_id"),
        "url": row.get("ml_url"),
        "cuenta": row.get("cuenta"),
        "full": None,          # se completa al refrescar contra la API
        "full_label": "FULL",
        "origen": "db",
    }


# Orden permitido (columnas del SELECT)
_ORDEN_ML = {
    "stock_desc": "p.stock_odoo DESC",
    "stock_asc": "p.stock_odoo ASC",
    "precio_desc": "precio DESC",
    "precio_asc": "precio ASC",
    "reciente": "(mp.success = 1) DESC, mp.updated_at DESC",
}


def listar(
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
    solo_publicados: bool = False,
    cuenta: str | None = None,
    orden: str = "reciente",
    estados: list[str] | None = None,
    skus_filtro: list[str] | None = None,
    solo_activas: bool = False,
    skus_exactos: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """
    Devuelve (items, total) desde el cache MySQL.
    `cuenta` filtra por cuenta de ML (BEKURA=Kubera, SANCORFASHION=San Corpe).
    `orden` ordena por stock/precio. `estados` filtra publicado/inactivo.
    `skus_filtro`: términos separados por coma ("Filtrar SKUs"), filtra Y busca
    a la vez (SKU completo, parcial o palabra del nombre).
    `solo_activas`: sólo lo que se puede comprar HOY (`situacion='active'`),
    criterio de `publicaciones_panel`. Requiere la rejilla de kubera — ver
    `puede_filtrar_activas`.
    `skus_exactos`: la lista la resolvió el sistema (una etapa del flujo, el
    almacén DROP, el costo validado), así que se compara por igualdad y no por
    `ilike`. Solo lo entiende la rejilla de kubera: el respaldo de MySQL no
    puede filtrar exacto, y por eso quien pide una lista del sistema con
    `SUPABASE_READ_PUBLICACIONES` apagado recibe un 503 ANTES de llegar aquí.
    """
    # PASO 3 · BLOQUE 2 (19-ago). La rejilla entera sale de channel.listings.
    # Sin try/except: si kubera no contesta, la tabla debe romperse, no salir
    # vacia con cara de "no hay publicaciones".
    if settings.supabase_read_publicaciones:
        from services import channel_read
        filas, total = channel_read.rejilla_ml(
            page=page, per_page=per_page, search=search,
            solo_publicados=solo_publicados, cuenta=cuenta, orden=orden,
            estados=estados, skus_filtro=skus_filtro,
            solo_activas=solo_activas, skus_exactos=skus_exactos)
        return [_normalizar(f) for f in filas], total

    offset = (page - 1) * per_page
    like = f"%{search}%" if search else None
    # Filtro de estado: publicado (success=1) / inactivo (success<>1)
    estado_sql = ""
    if estados:
        if "publicado" in estados and "inactivo" not in estados:
            estado_sql = " AND mp.success = 1"
        elif "inactivo" in estados and "publicado" not in estados:
            estado_sql = " AND (mp.success IS NULL OR mp.success = 0)"
    order_sql = _ORDEN_ML.get(orden, _ORDEN_ML["reciente"])
    skus_sql, skus_params = _clausula_skus(skus_filtro, "sku_")
    params = {
        "limit": per_page, "offset": offset, "search": search, "like": like,
        "solo_publicados": 1 if solo_publicados else 0, "cuenta": cuenta,
        **skus_params,
    }
    sql = (_SQL_LISTAR.replace("__ESTADO__", estado_sql)
           .replace("__ORDEN__", order_sql).replace("__SKUS__", skus_sql))
    sql_count = _SQL_COUNT.replace("__ESTADO__", estado_sql).replace("__SKUS__", skus_sql)
    # PASO 0 (12-ago-2026): `ml_progress` sigue viva en MySQL, pero el PRECIO
    # sale de kubera. Con el espejo congelado esta vista mostraría el precio
    # del día del corte para siempre. Cuando se ordena POR precio no alcanza
    # con reemplazarlo al final —el ORDER BY del SQL usaría el viejo— así que
    # en ese caso se trae el conjunto filtrado (≤2k filas por cuenta), se le
    # pega el precio vivo y se ordena y pagina aquí.
    from services import costing_read, costing_write
    por_precio = orden in ("precio_desc", "precio_asc")
    try:
        if costing_write.activo() and por_precio:
            sql_todo = (_SQL_LISTAR.replace("__ESTADO__", estado_sql)
                        .replace("__ORDEN__", "mp.sku").replace("__SKUS__", skus_sql))
            crudas = db.fetch_all(sql_todo, {**params, "limit": 100_000, "offset": 0})
            items = _con_precio_kubera([_normalizar(r) for r in crudas],
                                       costing_read)
            items.sort(key=lambda i: (i["precio"] is None, i["precio"] or 0),
                       reverse=(orden == "precio_desc"))
            return items[offset:offset + per_page], len(items)
        rows = db.fetch_all(sql, params)
        total = db.fetch_scalar(sql_count, params) or 0
        items = [_normalizar(r) for r in rows]
        if costing_write.activo():
            items = _con_precio_kubera(items, costing_read)
        return items, int(total)
    except Exception as exc:  # noqa: BLE001
        log.error("Error listando ML desde DB: %s", exc)
        return [], 0


def _con_precio_kubera(items: list[dict[str, Any]], costing_read) -> list[dict[str, Any]]:
    """Pega precio/precio_base/categoría de `costing.costos_finales` sobre las
    filas que salieron de la bitácora. El precio del ESPEJO no se usa."""
    if not items:
        return items
    precios = costing_read.precios_de([i["sku"] for i in items])
    for i in items:
        p = precios.get(i["sku"])
        if not p:
            continue
        if p.get("precio_sugerido") is not None:
            i["precio"] = _f(p["precio_sugerido"])
        i["precio_base"] = _f(p.get("precio_base"))
        i["categoria_id"] = p.get("ml_cat_id") or i.get("categoria_id")
    return items


def puede_filtrar_activas() -> bool:
    """
    Si `solo_activas` se puede contestar en ESTE camino de lectura.

    La rejilla vieja sale de `ml_progress` (MySQL), que sólo sabe `success=1`:
    "el publicador la subió", no "se puede comprar". Ahí el filtro no se puede
    aplicar, y la respuesta correcta es DECIRLO — filtrar por `success` con la
    etiqueta de "activas" sería contar pausadas como activas, que es justo el
    número que este filtro existe para no dar.
    """
    return bool(settings.supabase_read_publicaciones)


def contar_publicados(cuenta: str | None = None) -> int:
    if settings.supabase_read_publicaciones:
        from services import channel_read
        return channel_read.contar_publicados_ml(cuenta)
    try:
        if cuenta:
            return int(db.fetch_scalar(
                "SELECT COUNT(*) FROM ml_progress WHERE success = 1 AND cuenta = %s",
                (cuenta,),
            ) or 0)
        return int(db.fetch_scalar(
            "SELECT COUNT(*) FROM ml_progress WHERE success = 1"
        ) or 0)
    except Exception:  # noqa: BLE001
        return 0


# ── Token OAuth desde DB ──────────────────────────────────────────────────────

def _fernet():
    """Fernet para desencriptar los tokens de ml_tokens (cifrados con DB_ENCRYPTION_KEY)."""
    key = settings.db_encryption_key
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except Exception as exc:  # noqa: BLE001
        log.warning("Fernet no disponible: %s", exc)
        return None


def _access_token(cuenta: str | None = None) -> str | None:
    """
    Lee y DESENCRIPTA el access_token vigente: el MÁS RECIENTE entre
    `ml_tokens_dashboard` (fuente única de verdad — todos los proyectos de ML se
    conectan ahí; ese proceso renueva proactivamente cada ~6 h) y `ml_tokens`
    (que este backend también mantiene al refrescar reactivamente). Comparar
    `updated_at` evita quedarse con una copia vieja si el otro proceso ya renovó.

    Con TOKENS_SOLO_KUBERA no hay arbitraje: el token sale solo de kubera.
    """
    if settings.tokens_solo_kubera:
        return _access_token_kubera(cuenta)
    try:
        candidatos = []
        # PASO 6 (19-ago): kubera entra al MISMO arbitraje por recencia, no lo
        # reemplaza. Mientras haya doble escritura los tres empatan; el dia que
        # MySQL se apague, kubera gana sola y nadie tiene que mover un flag.
        if settings.supabase_read_tokens:
            try:
                from services import tokens_read
                fila = tokens_read.leer(cuenta)
                if fila and fila.get("access_token"):
                    candidatos.append(fila)
            except Exception as exc:  # noqa: BLE001
                log.warning("tokens de kubera no disponibles: %s", exc)
        for tabla in ("ml_tokens_dashboard", "ml_tokens"):
            try:
                if cuenta:
                    row = db.fetch_one(
                        f"SELECT access_token, updated_at FROM {tabla} "
                        f"WHERE cuenta=%s ORDER BY updated_at DESC LIMIT 1", (cuenta,))
                else:
                    row = db.fetch_one(
                        f"SELECT access_token, updated_at FROM {tabla} "
                        f"ORDER BY updated_at DESC LIMIT 1")
                if row and row.get("access_token"):
                    candidatos.append(row)
            except Exception:  # noqa: BLE001
                continue  # la tabla puede no existir en algún entorno
        if not candidatos:
            return None
        mejor = max(candidatos, key=lambda r: r["updated_at"])
        raw = mejor["access_token"]
        # Los tokens están cifrados con Fernet (empiezan con 'gAAAAA').
        f = _fernet()
        if f and isinstance(raw, str) and raw.startswith("gAAAAA"):
            try:
                return f.decrypt(raw.encode()).decode()
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudo desencriptar token ML (%s): %s", cuenta, exc)
                return None
        return raw  # ya venía en claro
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer token ML: %s", exc)
        return None


def _clave(cuenta: str | None) -> str | None:
    """
    La cuenta como está guardada en kubera: en MAYÚSCULAS.

    MySQL compara sin distinguir mayúsculas y Postgres no: `competencia_ml` pide
    'bekura' y en MySQL la hallaba. Sin esto, con el flag esa llamada se quedaría
    sin token — y peor, al renovar crearía una fila 'bekura' al lado de 'BEKURA'.
    """
    return (cuenta or "").strip().upper() or None


def _access_token_kubera(cuenta: str | None) -> str | None:
    """TOKENS_SOLO_KUBERA: el access_token sale SOLO de `ops.ml_tokens`."""
    try:
        from services import tokens_read
        fila = tokens_read.leer(_clave(cuenta))
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer token ML de kubera (%s): %s", cuenta, exc)
        return None
    if not fila or not fila.get("access_token"):
        return None
    try:
        return _dec(_fernet(), fila["access_token"])
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo desencriptar token ML (%s): %s", cuenta, exc)
        return None


# Anti-estampida del refresh: el renovador EXTERNO de tokens (dashboard) se
# detiene a ratos — el sáb 18-jul dejó los tokens morir a las 11:02 CDMX y los
# pedidos pararon 26 h. Con esto, el primer 401 renueva el token AQUÍ (bajo
# candado por cuenta, para que una ráfaga de webhooks no dispare N refreshes:
# ML rota el refresh_token en cada uso y las carreras acaban en invalid_grant).
_refresh_locks: dict[str, "object"] = {}
_refresh_ts: dict[str, float] = {}


async def _renovar_con_candado(cuenta: str) -> str | None:
    import asyncio as _asyncio
    import time as _time
    lock = _refresh_locks.setdefault(cuenta, _asyncio.Lock())
    async with lock:
        # Si otra tarea acaba de renovar (ráfaga), usar ese token sin re-rotar.
        if _time.time() - _refresh_ts.get(cuenta, 0) < 120:
            return await _asyncio.to_thread(_access_token, cuenta)
        nuevo = await _asyncio.to_thread(refrescar_token, cuenta)
        if nuevo:
            _refresh_ts[cuenta] = _time.time()
        return nuevo


async def obtener_orden(order_id: str) -> dict | None:
    """
    Devuelve la orden COMPLETA de ML, normalizada, o None si no se encontró.

    El webhook solo avisa "cambió la orden 123": el precio REAL al que se vendió
    solo existe aquí (`order_items[].unit_price`). El precio del catálogo cambia
    todo el tiempo, así que no sirve para saber en cuánto se vendió algo.

    Prueba el token de cada cuenta (la orden es de uno de los dos vendedores) y
    de paso resuelve el envío, porque `logistic_type == "fulfillment"` es lo que
    distingue una venta FULL (sale del almacén de ML) de una que surtimos nosotros.
    Un 401 = token caducado → se renueva al vuelo y se reintenta (auto-sanado).
    """
    import httpx as _httpx
    for cuenta in ("BEKURA", "SANCORFASHION"):
        token = _access_token(cuenta)
        if not token:
            continue
        try:
            cab = {"Authorization": f"Bearer {token}"}
            async with _httpx.AsyncClient(base_url=_API, timeout=20.0) as cli:
                r = await cli.get(f"/orders/{order_id}", headers=cab)
                if r.status_code == 401:
                    # Token caducado (401). Un 403 NO: significa que la orden es
                    # de la otra cuenta y el token está bien.
                    nuevo = await _renovar_con_candado(cuenta)
                    if nuevo:
                        cab = {"Authorization": f"Bearer {nuevo}"}
                        r = await cli.get(f"/orders/{order_id}", headers=cab)
                if r.status_code != 200:
                    continue  # 404/403 → probablemente es de la otra cuenta
                d = r.json()

                # El envío va aparte: la orden solo trae shipping.id.
                envio: dict = {}
                env_id = (d.get("shipping") or {}).get("id")
                if env_id:
                    try:
                        re_ = await cli.get(f"/shipments/{env_id}", headers=cab)
                        if re_.status_code == 200:
                            s = re_.json()
                            envio = {"id": env_id,
                                     "logistica": s.get("logistic_type"),
                                     "estado": s.get("status"),
                                     "subestado": s.get("substatus")}
                    except Exception:  # noqa: BLE001
                        pass

                items = []
                for oi in d.get("order_items", []):
                    it = oi.get("item") or {}
                    items.append({
                        "item_id": str(it.get("id") or ""),
                        "sku": (it.get("seller_sku") or "").strip(),
                        "titulo": it.get("title") or "",
                        "variacion_id": it.get("variation_id"),
                        "cantidad": int(oi.get("quantity") or 1),
                        # unit_price = lo que REALMENTE pagó el comprador por unidad.
                        "precio_unitario": float(oi.get("unit_price") or 0),
                        "precio_lista": float(oi.get("full_unit_price")
                                              or oi.get("gross_price") or 0),
                        "comision_ml": float(oi.get("sale_fee") or 0),
                    })

                pago = (d.get("payments") or [{}])[0]
                comprador = d.get("buyer") or {}
                return {
                    "id": str(d.get("id")),
                    "cuenta": cuenta,
                    "estado": d.get("status"),
                    "detalle": d.get("status_detail"),
                    "etiquetas": d.get("tags") or [],
                    "fecha": d.get("date_created"),
                    "total": float(d.get("total_amount") or 0),
                    "pagado": float(d.get("paid_amount") or 0),
                    "moneda": d.get("currency_id") or "MXN",
                    "envio_costo": float(d.get("shipping_cost") or 0),
                    "items": items,
                    "envio": envio,
                    "es_full": (envio.get("logistica") == "fulfillment"),
                    "pago_estado": pago.get("status"),
                    "pago_fecha": pago.get("date_approved"),
                    "comprador": {
                        "id": comprador.get("id"),
                        "nick": comprador.get("nickname"),
                        "nombre": comprador.get("first_name"),
                        "apellido": comprador.get("last_name"),
                    },
                }
        except Exception:  # noqa: BLE001
            continue
    return None


async def obtener_orden_items(order_id: str) -> list[str]:
    """Devuelve solo los item_id de una orden (para resincronizar su stock)."""
    orden = await obtener_orden(order_id)
    return [i["item_id"] for i in (orden or {}).get("items", []) if i.get("item_id")]


def _dec(f, v):
    if f and isinstance(v, str) and v.startswith("gAAAAA"):
        return f.decrypt(v.encode()).decode()
    return v


def _enc(f, v):
    return f.encrypt(v.encode()).decode() if f else v


def _credenciales_refresh(cuenta: str) -> tuple[str, str, str] | None:
    """
    Credenciales para renovar el token de una cuenta: (app_id, client_secret,
    refresh_token). Prioriza `ml_tokens_dashboard` (otro proceso las mantiene
    frescas ahí con su propia app; ML ROTA el refresh_token en cada uso, así que
    si ese proceso refrescó primero, el refresh_token de `ml_tokens` queda
    invalidado — por eso ml_tokens_dashboard es la fuente más confiable). Si no
    existe esa fila, cae a MELI_APP_ID/SECRET del entorno + refresh_token de
    ml_tokens.
    """
    f = _fernet()
    try:
        row = db.fetch_one(
            "SELECT app_id, client_secret, refresh_token FROM ml_tokens_dashboard "
            "WHERE cuenta=%s LIMIT 1", (cuenta,))
        if row and row.get("app_id") and row.get("client_secret") and row.get("refresh_token"):
            return (_dec(f, row["app_id"]), _dec(f, row["client_secret"]), _dec(f, row["refresh_token"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer ml_tokens_dashboard(%s): %s", cuenta, exc)

    if settings.meli_app_id and settings.meli_client_secret:
        # PASO 6: con la bandera, el refresh_token sale de kubera. El app_id y el
        # client_secret salen del ENTORNO y no de una tabla — este era el camino
        # de respaldo y pasa a ser el principal. Ver la nota de la migracion
        # 0023: el client_secret de `ml_tokens_dashboard` es el que esta expuesto
        # en el repo `publicador`, y copiarlo a kubera seria esparcirlo.
        if settings.supabase_read_tokens:
            try:
                from services import tokens_read
                fila = tokens_read.leer(cuenta)
                if fila and fila.get("refresh_token"):
                    return (settings.meli_app_id, settings.meli_client_secret,
                            _dec(f, fila["refresh_token"]))
            except Exception as exc:  # noqa: BLE001
                log.warning("refresh_token de kubera no disponible (%s): %s", cuenta, exc)
        try:
            row = db.fetch_one(
                "SELECT refresh_token FROM ml_tokens WHERE cuenta=%s LIMIT 1", (cuenta,))
            if row and row.get("refresh_token"):
                return (settings.meli_app_id, settings.meli_client_secret, _dec(f, row["refresh_token"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo leer ml_tokens(%s): %s", cuenta, exc)
    return None


def refrescar_token(cuenta: str) -> str | None:
    """
    Renueva el access_token de una cuenta ML. Ver `_credenciales_refresh` para de
    dónde salen app_id/secret/refresh_token. Al renovar, persiste el token nuevo
    cifrado en `ml_tokens` (lo lee el pipeline) Y `ml_tokens_dashboard` (si existe
    la fila), para que ambos procesos queden sincronizados con el refresh_token
    vigente — ML lo rota en cada uso, así que si quedan desincronizados el
    siguiente refresh de cualquiera de los dos falla con invalid_grant.

    Con TOKENS_SOLO_KUBERA renueva `_refrescar_solo_kubera`, bajo candado.
    """
    if settings.tokens_solo_kubera:
        return _refrescar_solo_kubera(cuenta)
    creds = _credenciales_refresh(cuenta)
    if not creds:
        log.warning("Sin credenciales para renovar token de %s "
                    "(ni ml_tokens_dashboard ni MELI_APP_ID/SECRET+ml_tokens).", cuenta)
        return None
    app_id, secret, rt = creds
    try:
        import httpx as _httpx
        r = _httpx.post(f"{_API}/oauth/token", data={
            "grant_type": "refresh_token",
            "client_id": app_id,
            "client_secret": secret,
            "refresh_token": rt,
        }, timeout=20)
        if r.status_code != 200:
            _avisar_refresh_fallido(cuenta, r)
            return None
        tok = r.json()
        nuevo = tok["access_token"]
        nuevo_rt = tok.get("refresh_token", rt)

        f = _fernet()
        enc_at, enc_rt = _enc(f, nuevo), _enc(f, nuevo_rt)
        with db.get_cursor() as cur:
            cur.execute(
                "UPDATE ml_tokens SET access_token=%s, refresh_token=%s, updated_at=NOW() WHERE cuenta=%s",
                (enc_at, enc_rt, cuenta),
            )
        try:
            with db.get_cursor() as cur:
                cur.execute(
                    "UPDATE ml_tokens_dashboard SET access_token=%s, refresh_token=%s, "
                    "updated_at=NOW() WHERE cuenta=%s",
                    (enc_at, enc_rt, cuenta),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo sincronizar ml_tokens_dashboard(%s): %s", cuenta, exc)

        # PASO 6: el MISMO par cifrado tambien a kubera. Escribir en los dos
        # lados es seguro; lo que no se puede duplicar es la RENOVACION, y esto
        # no renueva nada: guarda lo que ya se renovo una sola vez, arriba.
        if settings.supabase_write_tokens:
            try:
                from services import tokens_read
                tokens_read.guardar(cuenta, enc_at, enc_rt)
            except Exception as exc:  # noqa: BLE001
                log.warning("no se pudo espejar el token de %s a kubera: %s", cuenta, exc)

        log.info("Token ML %s renovado.", cuenta)
        return nuevo
    except Exception as exc:  # noqa: BLE001
        log.warning("Refresh token ML %s error: %s", cuenta, exc)
        return None


def _avisar_refresh_fallido(cuenta: str, r) -> None:
    log.warning("Refresh token ML %s falló: %s %s", cuenta, r.status_code, r.text[:150])
    try:
        from services import alertas
        alertas.avisar(
            "tokens_ml",
            f"*Refresh de token ML {cuenta} FALLÓ* ({r.status_code}: "
            f"{r.text[:100]}). Si es `invalid_grant`, el refresh_token "
            f"murió y hay que re-autorizar — los pedidos de esa cuenta "
            f"pueden parar.")
    except Exception:  # noqa: BLE001
        pass


# ── TOKENS_SOLO_KUBERA: renovar sin MySQL ─────────────────────────────────────
# Si otro proceso renovó hace menos de esto, su token se reutiliza en vez de
# gastar el refresh_token otra vez. Misma ventana que `_renovar_con_candado`.
_REUSO_S = 120
# Candado por cuenta DENTRO del proceso, antes del de Postgres: así una ráfaga
# de hilos espera aquí y no ocupando las 6 conexiones del pool de kubera.
_hilos_lock = threading.Lock()
_hilos: dict[str, threading.Lock] = {}


def _candado_hilo(cuenta: str) -> threading.Lock:
    with _hilos_lock:
        return _hilos.setdefault(cuenta, threading.Lock())


def _app_de_cuenta(cuenta: str) -> tuple[str, str] | None:
    """
    (app_id, client_secret) para renovar, del ENTORNO y nunca de una tabla:
    primero los de la cuenta (MELI_APP_ID_<CUENTA>, MELI_CLIENT_SECRET_<CUENTA>),
    si no, los globales (MELI_APP_ID/MELI_CLIENT_SECRET, la app de los webhooks).
    """
    app = os.environ.get(f"MELI_APP_ID_{cuenta}", "").strip()
    secreto = os.environ.get(f"MELI_CLIENT_SECRET_{cuenta}", "").strip()
    if app and secreto:
        return app, secreto
    if settings.meli_app_id and settings.meli_client_secret:
        return settings.meli_app_id, settings.meli_client_secret
    return None


def _app_del_token(token: str | None) -> str | None:
    """
    La app que emitió un access_token de ML: `APP_USR-<app_id>-<fecha>-...`.
    El número de app NO es secreto (sale en la URL de autorización); el resto
    del token sí, y aquí no se devuelve nada más.
    """
    partes = (token or "").split("-")
    if len(partes) > 2 and partes[0] == "APP_USR" and partes[1].isdigit():
        return partes[1]
    return None


def _refrescar_solo_kubera(cuenta: str) -> str | None:
    """
    Renueva leyendo y guardando SOLO en kubera, bajo un candado que comparten
    todos los procesos (`tokens_read.candado_renovacion`).

    El par nuevo se guarda antes de soltar el candado: quien espera, al entrar,
    ya lo ve con `edad_s` chica y lo reutiliza en vez de volver a gastar el
    refresh_token. MySQL recibe una copia DESPUÉS, sin decidir nada: es la
    reversa por si el flag se apaga.
    """
    from services import tokens_read
    clave = _clave(cuenta)
    if not clave:
        return None
    app = _app_de_cuenta(clave)
    if not app:
        log.warning("Sin app de ML para renovar %s: faltan MELI_APP_ID_%s y "
                    "MELI_CLIENT_SECRET_%s (o las globales).", clave, clave, clave)
        return None
    app_id, secreto = app
    f = _fernet()
    nuevo = par = None
    with _candado_hilo(clave):
        try:
            with tokens_read.candado_renovacion(clave) as (fila, guardar_par):
                if not fila or not fila.get("refresh_token"):
                    log.warning("Sin refresh_token de %s en kubera; no se puede "
                                "renovar.", clave)
                    return None
                actual = _dec(f, fila.get("access_token"))
                if fila.get("edad_s") is not None and fila["edad_s"] < _REUSO_S:
                    log.info("Token ML %s: otro proceso lo acaba de renovar; "
                             "se reutiliza.", clave)
                    return actual
                emisora = _app_del_token(actual)
                if emisora and emisora != app_id:
                    _avisar_app_distinta(clave, emisora, app_id)
                    return None
                rt = _dec(f, fila["refresh_token"])
                r = httpx.post(f"{_API}/oauth/token", data={
                    "grant_type": "refresh_token",
                    "client_id": app_id,
                    "client_secret": secreto,
                    "refresh_token": rt,
                }, timeout=20)
                if r.status_code != 200:
                    _avisar_refresh_fallido(clave, r)
                    return None
                tok = r.json()
                nuevo = tok["access_token"]
                par = (_enc(f, nuevo), _enc(f, tok.get("refresh_token", rt)))
                guardar_par(*par)
        except Exception as exc:  # noqa: BLE001
            if not par:
                log.warning("Refresh token ML %s error: %s", clave, exc)
                return None
            # ML YA rotó el refresh_token: si este par se pierde, la cuenta
            # queda sin forma de renovar y hay que re-autorizarla a mano.
            log.error("El par nuevo de %s no se guardó bajo el candado (%s); "
                      "se reintenta fuera.", clave, exc)
            try:
                tokens_read.guardar(clave, *par)
            except Exception as exc2:  # noqa: BLE001
                log.error("El par nuevo de %s NO se pudo guardar en kubera: %s",
                          clave, exc2)
                try:
                    from services import alertas
                    alertas.avisar(
                        "tokens_ml",
                        f"*Token ML {clave} renovado pero NO guardado en kubera* "
                        f"({type(exc2).__name__}). ML ya rotó el refresh_token: "
                        f"si la copia de MySQL tampoco quedó, hay que re-autorizar "
                        f"la cuenta.")
                except Exception:  # noqa: BLE001
                    pass
    _espejar_mysql(clave, *par)
    log.info("Token ML %s renovado (solo kubera).", clave)
    return nuevo


def _avisar_app_distinta(cuenta: str, emisora: str, app_id: str) -> None:
    log.warning("Token ML %s es de la app %s y el entorno da la %s: no se "
                "renueva.", cuenta, emisora, app_id)
    try:
        from services import alertas
        alertas.avisar(
            "tokens_ml",
            f"*No se renovó el token ML {cuenta}*: lo emitió la app {emisora} "
            f"y el entorno da la app {app_id}; ML lo rechazaría. Definir "
            f"`MELI_APP_ID_{cuenta}`/`MELI_CLIENT_SECRET_{cuenta}` con los de la "
            f"app {emisora}, o re-autorizar la cuenta con la {app_id}. El token "
            f"vigente vence a las ~6 h de su última renovación.")
    except Exception:  # noqa: BLE001
        pass


def revisar_apps_kubera() -> list[dict[str, Any]]:
    """
    ANTES de encender TOKENS_SOLO_KUBERA: por cada cuenta de `ops.ml_tokens`,
    ¿el entorno da la app que emitió su token? Si no, al vencer el token no se
    podría renovar y esa cuenta se quedaría sin API. Solo lee; devuelve números
    de app y edades, nunca un token ni una clave.
    """
    from services import supabase_db as sdb
    f = _fernet()
    filas = sdb.fetch_all(
        "select cuenta, access_token, extract(epoch from (now() - updated_at))::float "
        "/ 60 as edad_min from ops.ml_tokens order by cuenta")
    salida = []
    for r in filas:
        cuenta = r["cuenta"]
        try:
            emisora = _app_del_token(_dec(f, r["access_token"]))
        except Exception:  # noqa: BLE001 — no descifra: la llave no es la de estos tokens
            emisora = "no-descifra"
        app = _app_de_cuenta(cuenta)
        propia = bool(os.environ.get(f"MELI_APP_ID_{cuenta}", "").strip()
                      and os.environ.get(f"MELI_CLIENT_SECRET_{cuenta}", "").strip())
        salida.append({
            "cuenta": cuenta,
            "app_token": emisora,
            "app_entorno": app[0] if app else None,
            "fuente": "cuenta" if propia else ("global" if app else "ninguna"),
            "coincide": bool(app) and emisora == app[0],
            "edad_min": round(r["edad_min"] or 0),
        })
    return salida


def _espejar_mysql(cuenta: str, enc_at: str, enc_rt: str) -> None:
    """Copia a MySQL para que apagar el flag no deje un refresh_token quemado."""
    if not settings.mysql_enabled:
        return
    for tabla in ("ml_tokens", "ml_tokens_dashboard"):
        try:
            with db.get_cursor() as cur:
                cur.execute(
                    f"UPDATE {tabla} SET access_token=%s, refresh_token=%s, "
                    f"updated_at=NOW() WHERE cuenta=%s", (enc_at, enc_rt, cuenta))
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo copiar el token de %s a %s: %s", cuenta, tabla, exc)


# ── Refresco en vivo contra la API de Mercado Libre ───────────────────────────

async def refrescar_item(item_id: str, cuenta: str | None = None) -> dict[str, Any] | None:
    """Consulta /items/{id} + categoría para obtener precio, stock, FULL y ruta."""
    token = _access_token(cuenta)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(base_url=_API, timeout=20.0) as cli:
            r = await cli.get(f"/items/{item_id}", headers=headers)
            r.raise_for_status()
            item = r.json()
            cat_path = await _category_path(cli, item.get("category_id"), headers)
    except Exception as exc:  # noqa: BLE001
        log.warning("Refresco ML %s falló: %s", item_id, exc)
        return None

    logistic = (item.get("shipping") or {}).get("logistic_type")
    return {
        "item_id": item_id,
        "precio": item.get("price"),
        "stock": item.get("available_quantity"),
        "estado": item.get("status"),
        "categoria_id": item.get("category_id"),
        "categoria_path": cat_path,
        "full": logistic == "fulfillment",
        "full_label": "FULL" if logistic == "fulfillment" else (logistic or ""),
        "url": item.get("permalink"),
    }


async def titulo_y_foto(item_id: str, cuenta: str | None = None) -> dict[str, Any]:
    """
    ``{titulo, foto_url, foto}`` de una publicación: el título y la PRIMERA foto.

    Existe porque :func:`refrescar_item` —que consulta el mismo ``/items/{id}``—
    devuelve precio, stock, estado y categoría pero **no** trae ni ``title`` ni
    ``pictures``: los descarta al armar su diccionario, y agregárselos ahí
    cambiaría la forma que ya consumen el sync y el panel.

    Es el insumo del último peldaño del empate contra el packing list: cuando la
    foto de Odoo no sirve (18% de los publicados no tiene), lo único que queda
    para reconocer el producto es cómo se ve en su propio anuncio.

    Nunca levanta: un fallo aquí solo significa que ese SKU se queda sin ese
    peldaño, no que el análisis completo se caiga.
    """
    token = _access_token(cuenta)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    salida: dict[str, Any] = {"titulo": "", "foto_url": None, "foto": None}
    try:
        async with httpx.AsyncClient(base_url=_API, timeout=30.0) as cli:
            r = await cli.get(f"/items/{item_id}", headers=headers)
            r.raise_for_status()
            item = r.json()
            salida["titulo"] = item.get("title") or ""
            fotos = item.get("pictures") or []
            if not fotos:
                return salida
            url = fotos[0].get("secure_url") or fotos[0].get("url")
            salida["foto_url"] = url
            if not url:
                return salida
            # La foto vive en mlstatic, no en la API: cliente aparte, sin el
            # base_url ni el Bearer (que ahí sobra).
            async with httpx.AsyncClient(timeout=30.0) as cli_img:
                ri = await cli_img.get(url)
                ri.raise_for_status()
                salida["foto"] = ri.content
    except Exception as exc:  # noqa: BLE001
        log.warning("titulo_y_foto %s falló: %s", item_id, exc)
    return salida


async def _category_path(
    cli: httpx.AsyncClient, cat_id: str | None, headers: dict
) -> list[dict[str, Any]]:
    if not cat_id:
        return []
    try:
        r = await cli.get(f"/categories/{cat_id}", headers=headers)
        r.raise_for_status()
        data = r.json()
        return [
            {"id": n.get("id"), "nombre": n.get("name")}
            for n in data.get("path_from_root", [])
        ]
    except Exception:  # noqa: BLE001
        return [{"id": cat_id, "nombre": cat_id}]


def _f(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
