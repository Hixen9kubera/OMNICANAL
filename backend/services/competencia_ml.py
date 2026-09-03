"""
competencia_ml.py — Lo que la API de Mercado Libre SÍ da de la competencia.

Probado en vivo contra la API con el token real de BEKURA. El resultado del
sondeo, que es la razón de que este módulo exista y de que además haga falta un
scraper:

  FUNCIONA para publicaciones AJENAS
    GET /visits/items?ids={id}                  → visitas totales (UN id por llamada)
    GET /items/{id}/visits/time_window          → serie diaria de visitas
    GET /highlights/MLM/category/{cat}          → top 20 más vendidos con `position`
    GET /products/search?site_id=MLM&q=…        → productos de catálogo por título
    GET /products/{cpid}/items                  → competidores del mismo producto
    GET /user-products/{MLMU…}                  → nombre/marca/atributos
    GET /reviews/item/{id}                      → reseñas y rating

  BLOQUEADO (403 aun con token válido, en las DOS apps: BEKURA y SANCORFASHION)
    GET /sites/MLM/search                       → NO hay posición orgánica por API
    GET /items/{id} de un competidor            → NO da título, precio ni imagen
    GET /items?ids=… (multiget)                 → 403 por cada item ajeno
    GET /users/{id}/items/search de otro seller

O sea: **la API da la MÉTRICA (visitas) pero no la FICHA (título/imagen/precio/
posición) del competidor**. La ficha la trae ``competencia_scraper.py``; el
pegamento entre ambos es el `item_id`, que se extrae del URL raspado.

Los 403 de arriba NO son errores transitorios: no hay que reintentarlos ni
"arreglarlos" cambiando de cuenta. Se devuelven como vacío y ya.
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

import requests

from config import settings
from services import meli

log = logging.getLogger("omnicanal.competencia.ml")

_API = "https://api.mercadolibre.com"
_TIMEOUT = 25
_CUENTA_DEFAULT = "bekura"

# Del permalink raspado sale el item_id con el que se piden las visitas.
# Cubre las dos formas: /MLM-1234567890-titulo-_JM y /p/MLM12345678
_RE_ITEM = re.compile(r"\bMLM-?(\d{9,12})\b")


def item_id_desde_url(url: str | None) -> str | None:
    """`https://articulo.mercadolibre.com.mx/MLM-2050204991-tapetes-_JM` → `MLM2050204991`."""
    if not url:
        return None
    m = _RE_ITEM.search(url)
    return f"MLM{m.group(1)}" if m else None


# ── Plomería: GET autenticado con reintento de token ─────────────────────────

# CACHÉ DEL TOKEN, y no es una optimización cosmética: es un candado de cuota.
#
# `meli._access_token()` hace DOS consultas a MySQL en cada llamada (compara
# ml_tokens_dashboard contra ml_tokens para quedarse con el más reciente) y no
# cachea. Este módulo lo llamaba en CADA petición a la API de ML. Una captura de
# 84 subcategorías son ~1,900 llamadas —visitas y reseñas de cada fila— o sea
# ~3,800 consultas contra un MySQL compartido de Hostinger que tiene
# `max_connections_per_hour`.
#
# PASÓ DE VERDAD el 12-ago: a media captura MySQL empezó a responder
# «User has exceeded the max_connections_per_hour resource», el token dejó de
# leerse y 537 de 964 filas se guardaron SIN visitas. El log solo decía "Sin
# token de ML", que apunta al lugar equivocado.
#
# El TTL es corto contra una vida de ~6 h del token, y el 401 invalida la caché
# antes de refrescar, así que un token renovado por fuera se recoge enseguida.
_TTL_TOKEN = 300.0          # segundos
# Caché NEGATIVA: si el token no se pudo leer, no reintentar en cada llamada.
# Sin esto el fallo se retroalimenta — MySQL sin cuota hace fallar la lectura, y
# reintentarla 1,900 veces es justo lo que MANTIENE la cuota agotada. Con 30 s se
# recupera rápido cuando la cuota vuelve, sin convertir un tropiezo en un bucle.
_TTL_SIN_TOKEN = 30.0
_cache_token: dict[str, tuple[float, str | None]] = {}

# ESPERAS ANTE UN 429, y por qué existen.
#
# La API de ML limita por aplicación, no por cuenta ni por endpoint, así que
# TODO lo que corre a la vez comparte el mismo cupo: el backend en producción,
# el cron de visitas y el de barrido. Un 429 no significa que algo esté roto —
# significa "ahora no".
#
# COSTÓ TRES DÍAS DE CRON: el 31-ago, 1-sep y 2-sep de 2026 el cron de visitas
# terminó en CRASHED. Medido el 2-sep reproduciendo la corrida entera, la
# respuesta era 429 en 553 de 2,936 llamadas a 16/s desde una laptop, y en
# 1,974 de 2,936 a 70/s desde Railway. Dosis-respuesta limpia: cuanto más
# rápido, más 429.
#
# Y no se veía. El 429 caía en el `log.info` del final de `_get`, que con el
# logger sin configurar (nivel WARNING) NO SE IMPRIME. Los logs salían limpios,
# la llamada devolvía None, y la guarda de sanidad concluía "algo está mal con
# el token de ML" — el lugar equivocado, otra vez.
#
# El backoff además se autorregula: los hilos que esperan dejan de pedir, y eso
# es justo lo que baja el ritmo por debajo del límite sin tener que adivinarlo.
_ESPERAS_429 = (0.5, 1.0, 2.0, 4.0, 8.0)


def _espera_429(r: Any, intento: int) -> float:
    """Lo que pide ML en `Retry-After`, o la espera exponencial con jitter."""
    cab = (r.headers or {}).get("Retry-After") or ""
    try:
        if cab.strip():
            return min(float(cab), 30.0)
    except ValueError:
        pass
    return _ESPERAS_429[intento] + random.uniform(0, 0.4)


def _token(cuenta: str) -> str | None:
    """
    El token de esa cuenta, con caché corta en proceso.

    LA CUENTA SE NORMALIZA A MAYÚSCULAS, y no es cosmético. Este módulo maneja
    la cuenta en minúsculas (`_CUENTA_DEFAULT = "bekura"`, `CUENTAS` en
    `competencia_captura`), pero las dos tablas de tokens la guardan en
    MAYÚSCULAS: `ops.ml_tokens` en kubera y `ml_tokens`/`ml_tokens_dashboard`
    en MySQL.

    Funcionaba de casualidad: **MySQL compara sin distinguir mayúsculas** por su
    collation, así que `WHERE cuenta='bekura'` encontraba 'BEKURA'. **Postgres
    NO** — su columna es `text` y distingue. Resultado medido el 1-sep-2026:

        _access_token('BEKURA')  → TOKEN OK   (solo con Supabase)
        _access_token('bekura')  → nada

    O sea que, con `SUPABASE_READ_TOKENS=true` y todo, **este módulo era el
    único de la app que seguía sacando su token de MySQL** — el resto pide la
    cuenta en mayúsculas (`costos.DEFAULT_ACCOUNT = "BEKURA"`) y sí la halla en
    kubera. El día que F8 retire el esquema viejo, Competencia se habría quedado
    sin token y el síntoma —"sin token de ML"— habría apuntado al lugar
    equivocado.
    """
    import time as _t
    clave = (cuenta or "").upper() or None
    hit = _cache_token.get(clave)
    if hit:
        edad, valor = _t.monotonic() - hit[0], hit[1]
        if edad < (_TTL_TOKEN if valor else _TTL_SIN_TOKEN):
            return valor
    tok = meli._access_token(clave)
    _cache_token[clave] = (_t.monotonic(), tok)
    return tok


def _olvidar_token(cuenta: str) -> None:
    _cache_token.pop((cuenta or '').upper() or None, None)


def _get(ruta: str, params: dict[str, Any] | None = None,
         cuenta: str = _CUENTA_DEFAULT, _reintentado: bool = False,
         _intento_429: int = 0, estado: dict[str, int] | None = None) -> Any | None:
    """
    GET contra la API de ML. Renueva el token una sola vez ante un 401, igual que
    `costos.pct_comision_ml`. Un 403 se registra en DEBUG y no se reintenta: es
    permiso denegado por diseño de ML, no un token caduco.

    `estado` es un buzón OPCIONAL: quien lo pase recibe ahí el código HTTP. Hace
    falta porque `None` colapsa tres cosas distintas —404, 403 y «no se pudo
    preguntar»— y hay al menos un caso donde la diferencia es todo: `tendencias`
    debe escribir «ML no publica términos aquí» ante un 404 y NO TOCAR NADA ante
    un fallo. Quien no lo pase no cambia en nada.

    Un 429 SÍ se reintenta con espera creciente (ver `_ESPERAS_429`): es el
    límite de ritmo de la API, compartido por toda la aplicación, y es la causa
    real de que el cron de visitas se cayera tres días seguidos.
    """
    token = _token(cuenta)
    if not token:
        # Ojo al diagnosticar: la causa más probable NO es que el token expiró,
        # sino que MySQL —donde vive— rechazó la conexión por cuota horaria.
        log.warning("Sin token de ML para la cuenta %s (¿MySQL sin conexiones?)", cuenta)
        return None
    try:
        r = requests.get(f"{_API}{ruta}", params=params,
                         headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        log.warning("ML GET %s error de red: %s", ruta, exc)
        return None

    if estado is not None:
        estado["code"] = r.status_code
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return None
    if r.status_code == 401 and not _reintentado:
        _olvidar_token(cuenta)          # el cacheado ya no sirve
        if meli.refrescar_token(cuenta):
            return _get(ruta, params, cuenta, _reintentado=True)
    if r.status_code == 403:
        # Esperado para items ajenos. Que no ensucie los logs de producción.
        log.debug("ML GET %s → 403 (recurso ajeno, sin permiso)", ruta)
        return None
    if r.status_code == 429:
        if _intento_429 < len(_ESPERAS_429):
            time.sleep(_espera_429(r, _intento_429))
            return _get(ruta, params, cuenta, _reintentado, _intento_429 + 1)
        # WARNING, no INFO: si se abandona una llamada por límite de ritmo, eso
        # tiene que verse en los logs de producción. Ver `_ESPERAS_429`.
        log.warning("ML GET %s → 429 tras %d esperas; se abandona",
                    ruta, len(_ESPERAS_429))
        return None
    log.warning("ML GET %s → %s %s", ruta, r.status_code, r.text[:150])
    return None


# ── Nuestras publicaciones (la autoridad es ML, no nuestra tabla) ───────────

def items_por_sku(sku: str, cuenta: str = _CUENTA_DEFAULT) -> list[str]:
    """
    Los item_id NUESTROS que ML tiene para ese seller_sku, en esa cuenta.

    Es la fuente AUTORITATIVA de qué está publicado, y hace falta: `ml_progress`
    no conoce las publicaciones creadas fuera del pipeline del panel. Caso real —
    MUE-0163-TEL está publicado en las dos tiendas (BEKURA MLM4702363498,
    SANCORFASHION MLM4700224434, la del `/up/MLMU3745821559`) y `ml_progress` no
    tiene ninguna de las dos, así que el panel lo reportaba como "sin publicar".

    Ojo: `/users/{id}/items/search` solo funciona para la PROPIA cuenta del token
    (con un seller ajeno responde 403).
    """
    uid = _user_id(cuenta)
    if not uid:
        return []
    d = _get(f"/users/{uid}/items/search", {"seller_sku": sku, "limit": 50}, cuenta)
    if not isinstance(d, dict):
        return []
    return [i for i in (d.get("results") or []) if i]


_uids: dict[str, int] = {}


def _user_id(cuenta: str) -> int | None:
    """user_id de la cuenta del token, cacheado en proceso."""
    if cuenta in _uids:
        return _uids[cuenta]
    d = _get("/users/me", None, cuenta)
    uid = (d or {}).get("id") if isinstance(d, dict) else None
    if uid:
        _uids[cuenta] = int(uid)
    return _uids.get(cuenta)


def detalle_item(item_id: str, cuenta: str = _CUENTA_DEFAULT) -> dict[str, Any] | None:
    """
    Ficha de una publicación NUESTRA: título, precio, foto y permalink.

    `GET /items/{id}` funciona para los items propios (para los ajenos es 403 —
    de ahí que la competencia necesite el scraper).
    """
    d = _get(f"/items/{item_id}", {
        "attributes": "id,title,price,currency_id,permalink,thumbnail,status,"
                      "sold_quantity,available_quantity",
    }, cuenta)
    if not isinstance(d, dict):
        return None
    foto = d.get("thumbnail") or ""
    return {
        "ml_item_id": d.get("id") or item_id,
        "titulo": d.get("title"),
        "precio": d.get("price"),
        "moneda": d.get("currency_id") or "MXN",
        "url": d.get("permalink"),
        # ML sirve el thumbnail por http; en https evita el bloqueo de contenido mixto.
        "imagen": foto.replace("http://", "https://") if foto else None,
        "estado": d.get("status"),
        # OJO: sold_quantity es el ACUMULADO histórico, no de 30 días. Las
        # unidades del periodo salen de los pedidos, no de aquí.
        "vendidos_historico": d.get("sold_quantity"),
    }


def unidades_vendidas_30d(cuenta: str = _CUENTA_DEFAULT, dias: int = 30,
                          tope_paginas: int = 400) -> dict[str, int]:
    """
    Unidades VENDIDAS por item_id en los últimos `dias`, para una cuenta.
    → { item_id: unidades }

    Es un BARRIDO de los pedidos de la cuenta, no una consulta por SKU, y es a
    propósito: `/orders/search` sí acepta `item=MLM…` (probado: 512 pedidos para
    MLM4700224434) pero igual hay que paginar para SUMAR cantidades, así que
    filtrar por item costaría ~11 páginas POR publicación. Un barrido son ~140
    páginas por cuenta y devuelve TODOS los SKUs de una vez: con 8 SKUs empata,
    con 1,000 es 10× más barato.

    Filtros que NO sirven (probados): `seller_sku`, `order.item.seller_sku` y
    `q=<SKU>` se ignoran o dan 0 — el total sigue siendo el de la cuenta completa.

    Y no se usa `items.sold_quantity` porque ese es el acumulado HISTÓRICO
    (1,159 en MUE-0163-TEL), no el del periodo.
    """
    from datetime import datetime, timedelta, timezone
    uid = _user_id(cuenta)
    if not uid:
        return {}
    hasta = datetime.now(timezone.utc)
    desde = hasta - timedelta(days=dias)

    def fmt(d):
        return d.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

    unidades: dict[str, int] = {}
    limite, offset = 50, 0
    for _ in range(tope_paginas):
        d = _get("/orders/search", {
            "seller": uid,
            "order.date_created.from": fmt(desde),
            "order.date_created.to": fmt(hasta),
            "limit": limite, "offset": offset, "sort": "date_asc",
        }, cuenta)
        if not isinstance(d, dict):
            break
        filas = d.get("results") or []
        for o in filas:
            for it in o.get("order_items") or []:
                iid = (it.get("item") or {}).get("id")
                if iid:
                    unidades[iid] = unidades.get(iid, 0) + int(it.get("quantity") or 0)
        total = (d.get("paging") or {}).get("total") or 0
        offset += limite
        if offset >= total or not filas:
            break
    else:
        log.warning("unidades_vendidas_30d(%s): corté en el tope de %s páginas",
                    cuenta, tope_paginas)
    log.info("unidades_vendidas_30d(%s): %s items con venta", cuenta, len(unidades))
    return unidades


# ── Visitas — el dato central del módulo ─────────────────────────────────────

def visitas(item_id: str, cuenta: str = _CUENTA_DEFAULT) -> int | None:
    """
    Visitas totales acumuladas de CUALQUIER publicación, propia o ajena.

    OJO: `/visits/items` acepta **un solo id por llamada** — mandar dos devuelve
    HTTP 400 "maximum amount of items to query is 1". Nada de multiget aquí.
    """
    d = _get("/visits/items", {"ids": item_id}, cuenta)
    if not isinstance(d, dict):
        return None
    v = d.get(item_id)
    return int(v) if isinstance(v, (int, float)) else None


def visitas_serie(item_id: str, dias: int = 30,
                  cuenta: str = _CUENTA_DEFAULT) -> dict[str, Any] | None:
    """
    Serie diaria de visitas de cualquier publicación.
    → { total, dias: [{fecha, visitas}] }
    """
    d = _get(f"/items/{item_id}/visits/time_window",
             {"last": dias, "unit": "day"}, cuenta)
    if not isinstance(d, dict):
        return None
    puntos = [
        {"fecha": (r.get("date") or "")[:10], "visitas": r.get("total") or 0}
        for r in (d.get("results") or [])
    ]
    puntos.sort(key=lambda p: p["fecha"])
    return {"total": d.get("total_visits") or 0, "dias": puntos}


def visitas_30d(item_id: str, cuenta: str = _CUENTA_DEFAULT) -> int | None:
    """Visitas de los últimos 30 días — la métrica que se guarda cada mes."""
    s = visitas_serie(item_id, 30, cuenta)
    return None if s is None else int(s["total"])


def ruta_categoria(categoria_id: str,
                   cuenta: str = _CUENTA_DEFAULT) -> list[dict[str, str]]:
    """
    La ruta de una categoría CON SUS IDs, de la raíz a la hoja.
    → [{"id": "MLM1747", "nombre": "Accesorios para Vehículos"}, …]

    Hace falta porque `categorias_ml.cat1..cat4` guarda los NOMBRES pero no los
    ids, y para pedir el ranking de un nivel se necesita su id. De aquí sale que
    la raíz de Tapetes sea MLM1747 (Accesorios para Vehículos) y no el nivel 2.
    """
    d = _get(f"/categories/{categoria_id}", None, cuenta)
    if not isinstance(d, dict):
        return []
    return [{"id": n.get("id"), "nombre": n.get("name")}
            for n in (d.get("path_from_root") or []) if n.get("id")]


# ── Más vendidos por categoría ───────────────────────────────────────────────

def mas_vendidos_categoria(categoria_id: str,
                           cuenta: str = _CUENTA_DEFAULT) -> list[dict[str, Any]]:
    """
    Top 20 más vendidos de una categoría, con su posición.

    Devuelve entradas de tres tipos y esa distinción importa:
      - `PRODUCT`      (MLM…)  → producto de catálogo, resuelve con `nombre_producto`
      - `USER_PRODUCT` (MLMU…) → producto de un vendedor, resuelve con `nombre_user_product`
      - `ITEM`         (MLM…)  → publicación suelta; la API NO la deja leer (403),
                                 su ficha solo la trae el scraper
    """
    d = _get(f"/highlights/{settings.ml_site_id}/category/{categoria_id}", None, cuenta)
    if not isinstance(d, dict):
        return []
    out = []
    for c in d.get("content") or []:
        if c.get("id"):
            out.append({"id": c["id"], "posicion": c.get("position"),
                        "tipo": c.get("type")})
    return out


def tendencias(categoria_id: str | None = None,
               cuenta: str = _CUENTA_DEFAULT) -> list[dict[str, Any]]:
    """
    Keywords más buscados del sitio o de una categoría: lo que la gente ESCRIBE
    en el buscador, ordenado por volumen.

    → lista de términos si ML contesta.
    → [] si ML responde 404: NO es un fallo, hay categorías de las que no publica
      nada — Bujías (MLM179785) y Cartuchos de Turbo (MLM458946) dan 404, y son
      exactamente las mismas cuyo /highlights también viene vacío.
    → **None si no se pudo preguntar** (timeout, 429, token caído). Quien llame
      NO debe escribir nada en ese caso: borraría datos buenos por un tropiezo.
    """
    ruta = f"/trends/{settings.ml_site_id}"
    if categoria_id:
        ruta += f"/{categoria_id}"
    est: dict[str, int] = {}
    d = _get(ruta, None, cuenta, estado=est)
    if isinstance(d, list):
        return [{"keyword": t.get("keyword"), "url": t.get("url")}
                for t in d if t.get("keyword")]
    # AQUÍ ESTABA EL HUECO: antes se devolvía [] pasara lo que pasara, así que
    # un timeout o un 429 se leía como «ML no publica términos» y quien llamara
    # BORRABA los términos guardados. El docstring de arriba ya pedía distinguir
    # las dos cosas; el código no daba con qué.
    return [] if est.get("code") == 404 else None


# ── Catálogo: búsqueda por título y competidores del mismo producto ──────────

def productos_por_titulo(query: str, limite: int = 20,
                         cuenta: str = _CUENTA_DEFAULT) -> list[dict[str, Any]]:
    """
    Productos de catálogo que empatan con un título. Es el reemplazo funcional de
    `/sites/MLM/search`, que ML dejó de permitir (403 incluso con token).
    """
    d = _get("/products/search", {
        "site_id": settings.ml_site_id, "status": "active",
        "q": query, "limit": min(limite, 50),
    }, cuenta)
    if not isinstance(d, dict):
        return []
    out = []
    for p in (d.get("results") or [])[:limite]:
        attrs = {a.get("id"): a.get("value_name") for a in (p.get("attributes") or [])}
        out.append({
            "catalog_product_id": p.get("id"),
            "nombre": p.get("name"),
            "domain_id": p.get("domain_id"),
            "marca": attrs.get("BRAND"),
            "modelo": attrs.get("MODEL"),
        })
    return out


def competidores_de_producto(catalog_product_id: str, limite: int = 20,
                             cuenta: str = _CUENTA_DEFAULT) -> list[dict[str, Any]]:
    """
    Las publicaciones que compiten por un mismo producto de catálogo.
    Es la ÚNICA vía por API para sacarle el precio a un competidor.
    """
    d = _get(f"/products/{catalog_product_id}/items", {"limit": min(limite, 50)}, cuenta)
    if not isinstance(d, dict):
        return []
    out = []
    for it in (d.get("results") or [])[:limite]:
        envio = it.get("shipping") or {}
        out.append({
            "externo_id": it.get("item_id"),
            "precio": it.get("price"),
            "precio_lista": it.get("original_price"),
            "moneda": it.get("currency_id") or "MXN",
            # La categoría REAL de la publicación. Es el único lugar por API donde
            # se puede saber a qué subcategoría pertenece un competidor: /items de
            # un ajeno responde 403.
            "categoria_id": it.get("category_id"),
            "seller_id": str(it.get("seller_id")) if it.get("seller_id") else None,
            "envio_gratis": bool(envio.get("free_shipping")),
            "listing_type": it.get("listing_type_id"),
            "tienda_oficial": it.get("official_store_id"),
        })
    return out


def producto_catalogo(catalog_product_id: str,
                      cuenta: str = _CUENTA_DEFAULT) -> dict[str, Any] | None:
    """Ficha del producto de catálogo: nombre, imagen y ganador del buy box."""
    d = _get(f"/products/{catalog_product_id}", None, cuenta)
    if not isinstance(d, dict):
        return None
    bbw = d.get("buy_box_winner") or {}
    fotos = d.get("pictures") or []
    return {
        "catalog_product_id": d.get("id"),
        "nombre": d.get("name"),
        "domain_id": d.get("domain_id"),
        "imagen": (fotos[0].get("url") if fotos else None),
        "url": d.get("permalink") or None,
        "buy_box_item": bbw.get("item_id"),
        "buy_box_precio": bbw.get("price"),
        "buy_box_seller": str(bbw.get("seller_id")) if bbw.get("seller_id") else None,
    }


def nombre_user_product(user_product_id: str,
                        cuenta: str = _CUENTA_DEFAULT) -> dict[str, Any] | None:
    """Resuelve un `MLMU…` de /highlights: nombre, marca y vendedor."""
    d = _get(f"/user-products/{user_product_id}", None, cuenta)
    if not isinstance(d, dict):
        return None
    attrs = {a.get("id"): ((a.get("values") or [{}])[0].get("name"))
             for a in (d.get("attributes") or [])}
    return {
        "externo_id": user_product_id,
        "nombre": d.get("name"),
        "domain_id": d.get("domain_id"),
        "marca": attrs.get("BRAND"),
        "seller_id": str(d.get("user_id")) if d.get("user_id") else None,
    }


def resolver_highlight(entrada: dict[str, Any],
                       cuenta: str = _CUENTA_DEFAULT) -> dict[str, Any]:
    """
    Convierte una entrada de `mas_vendidos_categoria` en una fila de listing.

    Lo que se puede llenar depende del tipo, y el hueco es real:
      - PRODUCT      → nombre, imagen, precio y seller del buy box
      - USER_PRODUCT → nombre, marca y seller (sin precio ni imagen)
      - ITEM         → nada más el id; la ficha la tiene que traer el scraper
    """
    ident, tipo = entrada.get("id"), (entrada.get("tipo") or "").upper()
    fila: dict[str, Any] = {
        "externo_id": ident,
        "posicion": entrada.get("posicion"),
        "tipo_highlight": tipo,
    }
    if tipo == "PRODUCT":
        p = producto_catalogo(ident, cuenta) or {}
        fila.update(titulo=p.get("nombre"), imagen=p.get("imagen"), url=p.get("url"),
                    precio=p.get("buy_box_precio"), seller_id=p.get("buy_box_seller"))
        if p.get("buy_box_item"):
            # El buy box es una publicación real: ese id sí acepta /visits.
            fila["externo_id"] = p["buy_box_item"]
    elif tipo == "USER_PRODUCT":
        up = nombre_user_product(ident, cuenta) or {}
        fila.update(titulo=up.get("nombre"), seller=up.get("marca"),
                    seller_id=up.get("seller_id"))
    return fila


def precio_venta(item_id: str, cuenta: str = _CUENTA_DEFAULT) -> dict[str, Any] | None:
    """
    El precio que el comprador PAGA, y el de lista.

    `/items/{id}.price` es el precio de LISTA y puede estar muy por encima del real:
    medido en CAM-0030-IND, lista $7,755.92 contra $3,294 de venta en BEKURA y
    $3,899 en SANCORFASHION — 58% y 50% de descuento. Mostrar el de lista hace ver
    la brecha contra el mercado mucho peor de lo que es.

    Solo funciona con publicaciones PROPIAS: en ajenas responde 403.
    """
    d = _get(f"/items/{item_id}/sale_price", {"context": "channel_marketplace"}, cuenta)
    if not isinstance(d, dict):
        return None
    return {"precio": d.get("amount"), "precio_lista": d.get("regular_amount")}


# ── Reseñas ──────────────────────────────────────────────────────────────────

def reviews(item_id: str, cuenta: str = _CUENTA_DEFAULT) -> dict[str, Any] | None:
    """Total de reseñas y calificación promedio de cualquier publicación."""
    d = _get(f"/reviews/item/{item_id}", None, cuenta)
    if not isinstance(d, dict):
        return None
    total = (d.get("paging") or {}).get("total")
    return {"reviews": total, "rating": d.get("rating_average")}
