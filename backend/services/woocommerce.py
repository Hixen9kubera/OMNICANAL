"""
woocommerce.py — Cliente de la API REST de WooCommerce (chunche.shop).

WooCommerce es "el centro": la vista GENERAL lista directamente sus productos.
Usamos httpx async para paginar de 40 en 40 y leemos los headers
X-WP-Total / X-WP-TotalPages para la paginación.

También resolvemos la RUTA COMPLETA de categorías (todos los niveles), ya que
el endpoint de productos solo devuelve la categoría asignada sin su cadena de
padres. Cacheamos el árbol de categorías en memoria.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from config import settings

log = logging.getLogger("omnicanal.woocommerce")

_BASE = f"{settings.wc_url.rstrip('/')}/wp-json/wc/v3"
_AUTH = (settings.wc_consumer_key, settings.wc_consumer_secret)

# Cache del árbol de categorías: { cat_id: {"name":..., "parent":...} }
_cat_cache: dict[int, dict[str, Any]] = {}
_cat_cache_ts: float = 0.0
_CAT_TTL = 60 * 30  # 30 min


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_BASE, auth=_AUTH, timeout=30.0)


async def _cargar_categorias() -> dict[int, dict[str, Any]]:
    """Descarga TODAS las categorías y arma { id: {name, parent} }."""
    global _cat_cache, _cat_cache_ts
    if _cat_cache and (time.time() - _cat_cache_ts) < _CAT_TTL:
        return _cat_cache

    cache: dict[int, dict[str, Any]] = {}
    async with _client() as cli:
        page = 1
        while True:
            r = await cli.get(
                "/products/categories",
                params={"per_page": 100, "page": page, "_fields": "id,name,parent"},
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for c in data:
                cache[c["id"]] = {"name": c["name"], "parent": c.get("parent", 0)}
            total_pages = int(r.headers.get("X-WP-TotalPages", page))
            if page >= total_pages:
                break
            page += 1

    _cat_cache = cache
    _cat_cache_ts = time.time()
    log.info("Categorías WooCommerce cacheadas: %d", len(cache))
    return cache


async def ruta_categoria(cat_id: int | None) -> list[dict[str, Any]]:
    """Devuelve la ruta completa [{id,nombre}, ...] desde la raíz hasta la hoja."""
    if not cat_id:
        return []
    cats = await _cargar_categorias()
    ruta: list[dict[str, Any]] = []
    visitados: set[int] = set()
    actual = cat_id
    while actual and actual in cats and actual not in visitados:
        visitados.add(actual)
        nodo = cats[actual]
        ruta.insert(0, {"id": actual, "nombre": nodo["name"]})
        actual = nodo.get("parent") or 0
    return ruta


def _img(producto: dict[str, Any]) -> str | None:
    imgs = producto.get("images") or []
    return imgs[0].get("src") if imgs else None


def _resumen(html: str | None, limite: int = 160) -> str | None:
    """Convierte un HTML corto de WooCommerce en texto plano recortado."""
    if not html:
        return None
    import re
    texto = re.sub(r"<[^>]+>", " ", html)
    texto = re.sub(r"\s+", " ", texto).strip()
    if not texto:
        return None
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def _marca(producto: dict[str, Any]) -> str | None:
    marcas = producto.get("brands") or []
    if marcas:
        return marcas[0].get("name")
    return None


async def _categoria_de_producto(producto: dict[str, Any]) -> list[dict[str, Any]]:
    cats = producto.get("categories") or []
    if not cats:
        return []
    # Tomamos la categoría asignada más específica y resolvemos su ruta completa.
    leaf_id = cats[-1].get("id")
    ruta = await ruta_categoria(leaf_id)
    if ruta:
        return ruta
    # Fallback: lo que venga embebido
    return [{"id": c.get("id"), "nombre": c.get("name")} for c in cats]


# Nota: "draft" NO aparece aquí — los drafts solo se ven en Crear Productos.
_ESTADOS_WC = {
    "publicado": ["publish"],
    "inactivo": ["pending", "inprogress", "ready", "private"],
}
_ORDEN_SQL = {
    "stock_desc": "stock_odoo DESC",
    "stock_asc": "stock_odoo ASC",
    "precio_desc": "precio DESC",
    "precio_asc": "precio ASC",
    "reciente": "updated_at DESC",
}


async def listar_productos(
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
    orden: str = "reciente",
    estados: list[str] | None = None,
    categoria: int | None = None,
    skus: list[str] | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Lista productos paginados. Devuelve (items_normalizados, total, total_pages).

    - `categoria` (id WC) → ruta nativa de WooCommerce (param category).
    - `search` / `estados` / `orden` por stock o precio / `skus` → se resuelven
      contra la tabla maestra `productos` (más potente) y se traen de WooCommerce
      por wc_id.
    - `skus`: términos separados por coma (ya en lista); filtra Y busca a la vez
      (SKU completo, parcial o palabra del nombre) — igual semántica que
      "Filtrar SKUs" en Crear Productos.
    - Sin filtros → listado en vivo de WooCommerce.
    """
    campos = (
        "id,name,sku,price,regular_price,sale_price,stock_quantity,"
        "stock_status,status,type,categories,brands,images,short_description,permalink"
    )
    params: dict[str, Any] = {
        "per_page": per_page,
        "page": page,
        "status": "any",
        "orderby": "date",
        "order": "desc",
        "_fields": campos,
    }

    usa_db = bool(
        search or estados or skus
        or orden in ("stock_desc", "stock_asc", "precio_desc", "precio_asc")
    )

    async with _client() as cli:
        data, total, total_pages = [], 0, 1

        if categoria:
            # Ruta nativa WooCommerce por categoría (+ orden por precio si aplica)
            p = {**params, "category": categoria}
            if orden in ("precio_desc", "precio_asc"):
                p["orderby"] = "price"
                p["order"] = "asc" if orden == "precio_asc" else "desc"
            r = await cli.get("/products", params=p)
            r.raise_for_status()
            data = r.json()
            total = int(r.headers.get("X-WP-Total", len(data)))
            total_pages = int(r.headers.get("X-WP-TotalPages", 1))

        elif usa_db:
            # Filtros/orden avanzados vía tabla `productos` → wc_ids → WooCommerce
            wc_ids, total_db = _buscar_wc_ids_db(search, page, per_page, orden, estados, skus)
            if wc_ids:
                r = await cli.get("/products", params={
                    **params, "include": ",".join(str(i) for i in wc_ids),
                    "per_page": len(wc_ids),
                    # wc_ids YA es el recorte de esta página (LIMIT/OFFSET en SQL);
                    # "page" aquí volvería a paginar sobre ese conjunto ya chico
                    # (mismo bug que en la rama de abajo, corregido igual).
                    "page": 1,
                })
                if r.status_code == 200:
                    by_id = {p["id"]: p for p in r.json()}
                    data = [by_id[i] for i in wc_ids if i in by_id]  # preserva el orden
                    total = total_db
                    total_pages = max(1, (total_db + per_page - 1) // per_page)
            # Fallback SKU exacto / nombre si la DB no resolvió (p. ej. solo search)
            if not data and search:
                if " " not in search.strip():
                    rs = await cli.get("/products", params={**params, "sku": search.strip()})
                    if rs.status_code == 200 and rs.json():
                        data = rs.json()
                        total = int(rs.headers.get("X-WP-Total", len(data)))
                        total_pages = int(rs.headers.get("X-WP-TotalPages", 1))
                if not data:
                    rn = await cli.get("/products", params={**params, "search": search})
                    rn.raise_for_status()
                    data = rn.json()
                    total = int(rn.headers.get("X-WP-Total", len(data)))
                    total_pages = int(rn.headers.get("X-WP-TotalPages", 1))
        else:
            # GENERAL sin filtros: catálogo SIN drafts (los drafts viven solo en
            # la vista Crear Productos). Paginamos sobre el índice cacheado.
            ids_sin_draft = await wc_ids_sin_draft()
            total = len(ids_sin_draft)
            total_pages = max(1, (total + per_page - 1) // per_page)
            pagina_ids = ids_sin_draft[(page - 1) * per_page: page * per_page]
            if pagina_ids:
                r = await cli.get("/products", params={
                    **params,
                    "include": ",".join(str(i) for i in pagina_ids),
                    "per_page": len(pagina_ids),
                    # pagina_ids YA es el recorte de esta página del índice cacheado;
                    # "page" aquí paginaría OTRA VEZ sobre ese conjunto ya de por sí
                    # más chico, dejando vacías todas las páginas > 1 (bug confirmado).
                    "page": 1,
                })
                r.raise_for_status()
                by_id = {p["id"]: p for p in r.json()}
                data = [by_id[i] for i in pagina_ids if i in by_id]

        # Los drafts nunca se muestran en GENERAL, vengan de la ruta que vengan
        # (categoría, búsqueda por SKU exacto, cache DB desactualizado…).
        data = [p for p in data if p.get("status") != "draft"]

        # Variantes de los padres `variable`: misma lectura que Crear Productos.
        variantes_por_prod = await variantes_de_productos(cli, data)

    # Resolvemos categorías en paralelo (cache compartida hace esto barato).
    rutas = await asyncio.gather(*[_categoria_de_producto(p) for p in data])

    items: list[dict[str, Any]] = []
    for p, ruta, variantes in zip(data, rutas, variantes_por_prod):
        precio = _to_float(p.get("price"))
        base = _to_float(p.get("regular_price")) or precio
        items.append(
            {
                "sku": p.get("sku") or f"WC-{p.get('id')}",
                "wc_id": p.get("id"),
                "nombre": p.get("name", ""),
                "imagen": _img(p),
                "marca": _marca(p),
                "descripcion_corta": _resumen(p.get("short_description")),
                "precio": precio,
                "precio_base": base,
                "precio_oferta": _to_float(p.get("sale_price")),
                "costo": None,
                "stock": stock_de_padre(p, variantes),
                "estado": p.get("status"),
                "categoria_path": ruta,
                "categoria_id": ruta[-1]["id"] if ruta else None,
                "publicado": p.get("status") == "publish",
                "url": p.get("permalink"),
                "tipo": p.get("type"),
                "variantes": variantes,
            }
        )

    # Precio/costo FRESCOS directo de MySQL: el GET de WooCommerce puede quedar
    # cacheado por el hosting (anti-bot) justo después de un guardado, mostrando
    # un precio viejo. La DB no tiene ese problema. Para padres `variable` esto
    # también trae el `_price` ya sincronizado desde sus variantes.
    try:
        from services import wp_db
        if wp_db.disponible():
            frescos = wp_db.precios_y_costo_por_wc_id(
                [{"wc_id": it["wc_id"], "tipo": it.get("tipo")} for it in items if it.get("wc_id")])
            for it in items:
                f = frescos.get(it["wc_id"])
                if not f:
                    continue
                if f["precio"] is not None:
                    it["precio"] = f["precio"]
                if f["precio_base"] is not None:
                    it["precio_base"] = f["precio_base"]
                if f["precio_oferta"] is not None:
                    it["precio_oferta"] = f["precio_oferta"]
                it["costo"] = f["costo"]
                # Todas las variantes son la MISMA pieza física (solo cambia
                # color/talla/cantidad) — comparten el costo del padre, igual
                # que el precio se replica a todas al guardar.
                for v in it.get("variantes", []):
                    v["costo"] = it["costo"]
    except Exception as exc:  # noqa: BLE001
        log.warning("listar_productos: no se pudo refrescar precio/costo desde DB: %s", exc)

    return items, total, total_pages


async def listar_categorias(limite: int = 300) -> list[dict[str, Any]]:
    """Categorías de WooCommerce con productos (id, nombre, count), para el filtro."""
    salida: list[dict[str, Any]] = []
    async with _client() as cli:
        page = 1
        while page <= 20 and len(salida) < limite:
            r = await cli.get("/products/categories", params={
                "per_page": 100, "page": page, "orderby": "count", "order": "desc",
                "hide_empty": True, "_fields": "id,name,parent,count",
            })
            if r.status_code != 200:
                break
            data = r.json()
            if not data:
                break
            for c in data:
                if c.get("count", 0) > 0:
                    salida.append({"id": c["id"], "nombre": c["name"],
                                   "parent": c.get("parent", 0), "count": c["count"]})
            if page >= int(r.headers.get("X-WP-TotalPages", page)):
                break
            page += 1
    return salida[:limite]


def _buscar_wc_ids_db(
    search: str | None,
    page: int,
    per_page: int,
    orden: str = "reciente",
    estados: list[str] | None = None,
    skus: list[str] | None = None,
) -> tuple[list[int], int]:
    """
    Resuelve búsqueda parcial + filtro de estado + orden (stock/precio) + lista
    de SKUs/términos contra la tabla maestra `productos` y devuelve (wc_ids de
    la página, total). Habilita en GENERAL: búsqueda parcial, ordenar por
    stock/precio, filtrar por estado (publicado / inactivo) y "Filtrar SKUs"
    (términos separados por coma — cada uno filtra Y busca a la vez: SKU
    completo, parcial o palabra del nombre).
    """
    from services import db  # import local para evitar ciclos

    where = ["wc_id IS NOT NULL"]
    args: list[Any] = []
    if search:
        where.append("(sku LIKE %s OR nombre LIKE %s)")
        like = f"%{search.strip()}%"
        args += [like, like]
    if skus:
        terminos = [t.strip() for t in skus if t.strip()]
        if terminos:
            or_grupo = " OR ".join(["(sku LIKE %s OR nombre LIKE %s)"] * len(terminos))
            where.append(f"({or_grupo})")
            for t in terminos:
                like_t = f"%{t}%"
                args += [like_t, like_t]
    if estados:
        valores: list[str] = []
        for e in estados:
            valores += _ESTADOS_WC.get(e, [])
        if valores:
            where.append(f"status_wc IN ({','.join(['%s'] * len(valores))})")
            args += valores
    where_sql = " AND ".join(where)
    order_sql = _ORDEN_SQL.get(orden, "updated_at DESC")
    offset = (page - 1) * per_page
    try:
        total = int(db.fetch_scalar(
            f"SELECT COUNT(*) FROM productos WHERE {where_sql}", tuple(args)
        ) or 0)
        if not total:
            return [], 0
        rows = db.fetch_all(
            f"""SELECT wc_id FROM productos WHERE {where_sql}
                ORDER BY {order_sql} LIMIT %s OFFSET %s""",
            tuple(args + [per_page, offset]),
        )
        return [r["wc_id"] for r in rows], total
    except Exception as exc:  # noqa: BLE001
        log.warning("Búsqueda DB falló, usando WooCommerce: %s", exc)
        return [], 0


async def imagenes_por_wc_id(wc_ids: list[int]) -> dict[int, str]:
    """
    Trae la imagen principal de WooCommerce para una lista de wc_id en UNA sola
    llamada (param include). Se usa para mostrar imágenes en los canales de
    marketplace (que comparten el mismo producto vía wc_id).
    """
    ids = [str(i) for i in wc_ids if i]
    if not ids:
        return {}
    salida: dict[int, str] = {}
    # WooCommerce limita include/per_page a 100; los lotes vienen de a ≤40.
    async with _client() as cli:
        r = await cli.get(
            "/products",
            params={
                "include": ",".join(ids),
                "per_page": min(len(ids), 100),
                "_fields": "id,images",
            },
        )
        if r.status_code == 200:
            for p in r.json():
                imgs = p.get("images") or []
                if imgs and imgs[0].get("src"):
                    salida[p["id"]] = imgs[0]["src"]
    return salida


def _atributos(producto: dict[str, Any]) -> list[dict[str, Any]]:
    """Normaliza los atributos de WooCommerce a [{nombre, valor}]."""
    salida: list[dict[str, Any]] = []
    for a in producto.get("attributes") or []:
        nombre = a.get("name")
        if not nombre:
            continue
        opciones = a.get("options") or []
        valor = ", ".join(str(o) for o in opciones) if opciones else ""
        salida.append({"nombre": nombre, "valor": valor})
    return salida


# ── Índice del catálogo completo (100% WooCommerce en vivo, cacheado) ──────────
# El status_wc cacheado en la DB está muy desactualizado, y el filtro `status=` de
# la API no es fiable con los estados personalizados (ready/inprogress → HTTP 400).
# Por eso escaneamos el catálogo con status=any y guardamos {wc_id, sku, nombre,
# estado} de TODO. De este índice derivan:
#   - candidatos a crear (estado NO publish/ready)  → vista Crear Productos
#   - catálogo sin drafts                           → vista GENERAL (omnicanal)
_ESTADOS_LISTOS = {"publish", "ready"}   # "ya resuelto" → no es candidato
_cand_cache: list[dict[str, Any]] = []   # catálogo COMPLETO
_cand_cache_ts: float = 0.0
# TTL amplio: cada escaneo son ~90 requests y el hosting bloquea por volumen
# (protección anti-bot); el cache viejo se sirve al instante igual.
_CAND_TTL = 60 * 15  # 15 min
_cand_lock = asyncio.Lock()  # evita que se construya el índice más de una vez a la vez


async def _get_con_reintento(cli: httpx.AsyncClient, url: str, params: dict, intentos: int = 3):
    """GET con reintentos (WooCommerce corta conexiones de forma intermitente)."""
    ultimo: Exception | None = None
    for _ in range(intentos):
        try:
            r = await cli.get(url, params=params)
            if r.status_code == 200:
                return r
            if r.status_code == 403:
                # Challenge anti-bot del hosting (bloqueo por IP): reintentar
                # solo lo empeora. Fallar rápido y dejar que el cache aguante.
                raise RuntimeError(
                    "HTTP 403: el hosting está bloqueando la API (protección anti-bot). "
                    "Suele levantarse solo en 15-30 min de tráfico bajo."
                )
            ultimo = RuntimeError(f"HTTP {r.status_code}")
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            ultimo = exc
        await asyncio.sleep(0.4)
    if ultimo:
        raise ultimo
    return None


async def _escanear_catalogo(
    campos: str, max_paginas: int = 200, status: str = "any"
) -> list[dict[str, Any]] | None:
    """
    Escanea el catálogo de WooCommerce (páginas en paralelo) y devuelve la lista
    cruda de productos con los `campos` pedidos. `status` acepta los estados
    NATIVOS de WordPress (any/draft/pending/publish/private) — los custom
    (inprogress/ready) NO se pueden filtrar aquí (la API los rechaza).
    Devuelve None si WooCommerce no respondió (para distinguir de catálogo vacío).
    """
    base_params = {
        "status": status, "per_page": 100,
        "orderby": "date", "order": "desc",
        "_fields": campos,
    }
    async with _client() as cli:
        # 1ª página: nos dice cuántas páginas hay en total.
        r1 = await _get_con_reintento(cli, "/products", {**base_params, "page": 1})
        if r1 is None:
            return None
        total_pages = min(int(r1.headers.get("X-WP-TotalPages", 1)), max_paginas)
        paginas: list[list[dict[str, Any]]] = [r1.json()]

        # Resto de páginas EN PARALELO (con tope de concurrencia).
        if total_pages > 1:
            sem = asyncio.Semaphore(3)  # concurrencia baja: el hosting bloquea por volumen

            async def _pagina(pg: int) -> list[dict[str, Any]]:
                async with sem:
                    r = await _get_con_reintento(cli, "/products", {**base_params, "page": pg})
                    return r.json() if r is not None else []

            paginas.extend(await asyncio.gather(*[_pagina(pg) for pg in range(2, total_pages + 1)]))

    return [p for data in paginas for p in data]


async def _construir_indice_catalogo() -> list[dict[str, Any]]:
    """Índice del catálogo completo: MySQL directo si está configurado; si no, API."""
    global _cand_cache, _cand_cache_ts

    from services import wp_db
    if wp_db.disponible():
        try:
            filas = await asyncio.to_thread(wp_db.indice_catalogo)
            _cand_cache = filas
            _cand_cache_ts = time.time()
            log.info("Índice de catálogo (MySQL directo): %d productos", len(filas))
            return _cand_cache
        except Exception as exc:  # noqa: BLE001
            log.warning("Índice de catálogo por MySQL falló, uso API: %s", exc)

    productos = await _escanear_catalogo("id,sku,name,status", max_paginas=200)
    if productos is None:
        return _cand_cache  # WooCommerce no respondió: conservamos lo previo

    salida = [
        {
            "wc_id": p["id"],
            "sku": p.get("sku") or f"WC-{p['id']}",
            "nombre": p.get("name", ""),
            "estado": p.get("status"),
        }
        for p in productos
    ]
    _cand_cache = salida
    _cand_cache_ts = time.time()
    log.info("Índice de catálogo (WooCommerce): %d productos", len(salida))
    return salida


async def _refrescar_indice_bg() -> None:
    """Reconstruye el índice en segundo plano (para stale-while-revalidate)."""
    if _cand_lock.locked():
        return
    async with _cand_lock:
        try:
            await _construir_indice_catalogo()
        except Exception as exc:  # noqa: BLE001
            log.warning("Refresco en segundo plano del índice falló: %s", exc)


async def indice_catalogo(refrescar: bool = False) -> list[dict[str, Any]]:
    """
    Devuelve [{wc_id, sku, nombre, estado}] de TODO el catálogo de WooCommerce.

    Cacheado en memoria (TTL 5 min). Como escanear todo el catálogo tarda, usa
    stale-while-revalidate: si el cache expiró pero existe, devuelve lo que hay al
    instante y refresca en segundo plano; solo bloquea cuando el cache está vacío.
    """
    edad = time.time() - _cand_cache_ts
    if _cand_cache and not refrescar:
        if edad >= _CAND_TTL:
            asyncio.create_task(_refrescar_indice_bg())  # refresco en segundo plano
        return _cand_cache

    # Cache vacío (arranque en frío) o refresco forzado → construir ahora.
    async with _cand_lock:
        if _cand_cache and not refrescar and (time.time() - _cand_cache_ts) < _CAND_TTL:
            return _cand_cache  # otra corrutina ya lo construyó mientras esperábamos
        return await _construir_indice_catalogo()


# ── Índice de DRAFTS (GET directo status=draft, cache propio) ──────────────────
# "draft" es un estado NATIVO de WordPress, así que la API lo filtra del lado
# del servidor: solo se piden las páginas de drafts (~la mitad de requests que
# escanear el catálogo completo). Es la fuente de la vista Crear Productos.
_draft_cache: list[dict[str, Any]] = []
_draft_cache_ts: float = 0.0
_draft_lock = asyncio.Lock()


_drafts_completo = False  # ¿el índice ya tiene TODAS las páginas?


def drafts_completo() -> bool:
    return _drafts_completo


def _norm_draft(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "wc_id": p["id"],
        "sku": p.get("sku") or f"WC-{p['id']}",
        "nombre": p.get("name", ""),
        "estado": p.get("status"),
        "stock": p.get("stock_quantity"),
        # nombres de categorías Woo (para que el buscador/filtro matchee por categoría)
        "categorias": [c.get("name", "") for c in (p.get("categories") or [])],
    }


async def _construir_indice_drafts() -> list[dict[str, Any]]:
    """
    Construye el índice de drafts. Vía preferida: consulta DIRECTA a la base
    de WordPress (una consulta, inmune al anti-bot). Fallback: API REST con
    carga progresiva (la primera página queda disponible de inmediato).
    """
    global _draft_cache, _draft_cache_ts, _drafts_completo

    from services import wp_db
    if wp_db.disponible():
        try:
            filas = await asyncio.to_thread(wp_db.indice_drafts)
            _draft_cache = filas
            _draft_cache_ts = time.time()
            _drafts_completo = True
            log.info("Índice de drafts (MySQL directo): %d productos", len(filas))
            return _draft_cache
        except Exception as exc:  # noqa: BLE001
            log.warning("Índice de drafts por MySQL falló, uso API: %s", exc)
    params = {
        "status": "draft", "per_page": 100,
        "orderby": "date", "order": "desc",
        "_fields": "id,sku,name,status,stock_quantity,categories",
    }
    _drafts_completo = False
    async with _client() as cli:
        try:
            r1 = await _get_con_reintento(cli, "/products", {**params, "page": 1})
        except Exception as exc:  # noqa: BLE001
            log.warning("Índice de drafts: página 1 falló: %s", exc)
            _drafts_completo = bool(_draft_cache)
            return _draft_cache
        if r1 is None:
            _drafts_completo = bool(_draft_cache)
            return _draft_cache

        total_pages = min(int(r1.headers.get("X-WP-TotalPages", 1)), 200)
        salida = [_norm_draft(p) for p in r1.json() if p.get("status") == "draft"]
        _draft_cache = list(salida)  # primeras ~100 filas disponibles YA

        if total_pages > 1:
            sem = asyncio.Semaphore(3)  # concurrencia baja: el hosting bloquea por volumen

            async def _pagina(pg: int) -> list[dict[str, Any]]:
                async with sem:
                    try:
                        r = await _get_con_reintento(cli, "/products", {**params, "page": pg})
                        return r.json() if r is not None else []
                    except Exception:  # noqa: BLE001
                        return []

            tareas = [asyncio.create_task(_pagina(pg)) for pg in range(2, total_pages + 1)]
            for t in asyncio.as_completed(tareas):
                data = await t
                salida.extend(_norm_draft(p) for p in data if p.get("status") == "draft")
                _draft_cache = list(salida)  # el índice CRECE conforme llegan páginas

    _draft_cache_ts = time.time()
    _drafts_completo = True
    log.info("Índice de drafts (WooCommerce): %d productos por crear", len(_draft_cache))
    return _draft_cache


async def _refrescar_drafts_bg() -> None:
    if _draft_lock.locked():
        return
    async with _draft_lock:
        try:
            await _construir_indice_drafts()
        except Exception as exc:  # noqa: BLE001
            log.warning("Refresco en segundo plano de drafts falló: %s", exc)


async def indice_candidatos(refrescar: bool = False) -> list[dict[str, Any]]:
    """
    Solo los DRAFTS (GET directo a la API de Woo con status=draft) → vista
    Crear Productos. En cuanto el flujo de creación mueve un producto a
    inprogress/pending, sale de esta vista y aparece en la pestaña Omnicanal.

    Carga PROGRESIVA: en frío se lanza la construcción en segundo plano y se
    devuelve lo que haya en cuanto llega la primera página (~segundos); el
    índice sigue creciendo detrás (consultar `drafts_completo()`).
    Con cache: stale-while-revalidate (se sirve al instante, refresca detrás).
    """
    # Con MySQL directo, leer el índice es 1 consulta rápida (~2s) e inmune al
    # anti-bot: cacheamos solo 20s para que los cambios de status se vean casi
    # al instante. Sin MySQL (escaneo por API, ~90 requests) mantenemos 15 min.
    from services import wp_db
    ttl = 20 if wp_db.disponible() else _CAND_TTL

    edad = time.time() - _draft_cache_ts
    if _draft_cache and _drafts_completo and not refrescar:
        if edad >= ttl:
            asyncio.create_task(_refrescar_drafts_bg())
        return _draft_cache

    # Frío o refresco: construir en segundo plano (si no está ya corriendo) y
    # esperar SOLO a que exista la primera página.
    if not _draft_lock.locked():
        asyncio.create_task(_refrescar_drafts_bg())
    for _ in range(40):  # espera máx ~8 s por la primera página
        if _draft_cache:
            break
        await asyncio.sleep(0.2)
    return _draft_cache


async def drafts_pagina(
    page: int,
    per_page: int,
    search: str | None = None,
    categoria: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    MODO DIRECTO (sin esperar el índice): una página de drafts en 1 request,
    con búsqueda (search=) o filtro de categoría resueltos por WooCommerce.
    Devuelve (filas normalizadas, total según X-WP-Total).
    """
    params: dict[str, Any] = {
        "status": "draft", "per_page": per_page, "page": page,
        "orderby": "date", "order": "desc",
        "_fields": "id,sku,name,status,stock_quantity,categories",
    }
    if search:
        params["search"] = search.strip()

    if categoria:
        # OJO: Woo ignora `status=draft` cuando se combina con `category=`,
        # así que traemos la categoría completa (hasta 300) y filtramos drafts
        # localmente, paginando aquí mismo.
        cats = await _cargar_categorias()
        c = categoria.strip().lower()
        exacta = next((cid for cid, d in cats.items() if d["name"].strip().lower() == c), None)
        parcial = next((cid for cid, d in cats.items() if c in d["name"].strip().lower()), None)
        cat_id = exacta or parcial
        if not cat_id:
            return [], 0
        filas: list[dict[str, Any]] = []
        async with _client() as cli:
            for pg in (1, 2, 3):
                r = await cli.get("/products", params={
                    "status": "any", "category": cat_id,
                    "per_page": 100, "page": pg,
                    "_fields": "id,sku,name,status,stock_quantity,categories",
                })
                if r.status_code != 200:
                    break
                data = r.json()
                filas.extend(_norm_draft(p) for p in data if p.get("status") == "draft")
                if pg >= int(r.headers.get("X-WP-TotalPages", pg)):
                    break
        total = len(filas)
        offset = (page - 1) * per_page
        return filas[offset:offset + per_page], total

    async with _client() as cli:
        r = await cli.get("/products", params=params)
        r.raise_for_status()
        filas = [_norm_draft(p) for p in r.json() if p.get("status") == "draft"]
        total = int(r.headers.get("X-WP-Total", len(filas)))
    return filas, total


async def buscar_drafts(terminos: list[str]) -> list[dict[str, Any]]:
    """
    Búsqueda DIRECTA de drafts en Woo para el filtro multi-SKU: mientras el
    índice progresivo aún no está completo, trae adicionalmente los términos
    pedidos (match exacto por sku= en una llamada + search= por término).
    Devuelve filas normalizadas como las del índice.
    """
    encontrados: dict[int, dict[str, Any]] = {}
    campos = "id,sku,name,status,stock_quantity,categories"
    async with _client() as cli:
        try:
            r = await cli.get("/products", params={
                "sku": ",".join(terminos), "status": "draft",
                "per_page": 100, "_fields": campos,
            })
            if r.status_code == 200:
                for p in r.json():
                    encontrados[p["id"]] = p
        except Exception as exc:  # noqa: BLE001
            log.warning("buscar_drafts sku= falló: %s", exc)
        for t in terminos[:10]:  # tope de búsquedas por request
            try:
                r = await cli.get("/products", params={
                    "search": t, "status": "draft",
                    "per_page": 50, "_fields": campos,
                })
                if r.status_code == 200:
                    for p in r.json():
                        encontrados[p["id"]] = p
            except Exception as exc:  # noqa: BLE001
                log.warning("buscar_drafts search=%s falló: %s", t, exc)
    return [
        _norm_draft(p) for p in encontrados.values() if p.get("status") == "draft"
    ]


def quitar_de_drafts(wc_id: int) -> None:
    """
    Saca un producto del cache de drafts al instante (cuando el flujo de
    creación lo movió a inprogress), sin re-escanear WooCommerce.
    """
    global _draft_cache
    _draft_cache = [p for p in _draft_cache if p["wc_id"] != wc_id]


async def wc_ids_sin_draft() -> list[int]:
    """wc_ids del catálogo EXCEPTO drafts → vista GENERAL (omnicanal)."""
    catalogo = await indice_catalogo()
    return [p["wc_id"] for p in catalogo if p["estado"] != "draft"]


# ── Sincronización Odoo → WooCommerce (drafts) ─────────────────────────────────

async def skus_existentes() -> set[str] | None:
    """
    Devuelve el set de SKUs (normalizados a minúsculas) de TODO el catálogo de
    WooCommerce, cualquier estado. None si WooCommerce no respondió.
    Se usa para el diff con Odoo: lo que no esté aquí se crea como draft.
    """
    productos = await _escanear_catalogo("id,sku")
    if productos is None:
        return None
    return {
        (p.get("sku") or "").strip().lower()
        for p in productos
        if (p.get("sku") or "").strip()
    }


# Cache de SKUs confirmados existentes en Woo aunque NO salgan en el listado de
# /products (variaciones de productos variables, sobre todo). Solo positivos.
_sku_oculto_cache: set[str] = set()


async def filtrar_skus_existentes(skus: list[str]) -> set[str]:
    """
    Devuelve el subconjunto de `skus` (en minúsculas) que SÍ existe en
    WooCommerce aunque el listado de /products no lo muestre — típicamente
    variaciones, cuyos SKUs solo aparecen consultando con el filtro `sku=`.
    Consulta por lotes de 50; los positivos se cachean en memoria.
    """
    limpios = sorted({s.strip() for s in skus if s and s.strip()})
    encontrados = {s.lower() for s in limpios if s.lower() in _sku_oculto_cache}
    por_consultar = [s for s in limpios if s.lower() not in _sku_oculto_cache]
    if not por_consultar:
        return encontrados

    sem = asyncio.Semaphore(3)  # concurrencia baja: el hosting bloquea por volumen
    async with _client() as cli:

        async def _lote(lote: list[str]) -> list[str]:
            async with sem:
                try:
                    r = await _get_con_reintento(cli, "/products", {
                        "sku": ",".join(lote), "status": "any",
                        "per_page": 100, "_fields": "id,sku",
                    })
                except Exception as exc:  # noqa: BLE001
                    # Si un lote falla, lo tratamos como "no existe": el alta
                    # posterior fallará con "SKU duplicado" sin romper nada.
                    log.warning("filtrar_skus_existentes: lote falló: %s", exc)
                    return []
                return [p.get("sku") or "" for p in (r.json() if r else [])]

        lotes = [por_consultar[i:i + 50] for i in range(0, len(por_consultar), 50)]
        resultados = await asyncio.gather(*[_lote(l) for l in lotes])

    for skus_lote in resultados:
        for s in skus_lote:
            if s.strip():
                clave = s.strip().lower()
                _sku_oculto_cache.add(clave)
                encontrados.add(clave)
    return encontrados


def marcar_sku_existente(sku: str) -> None:
    """
    Marca un SKU como ya ocupado en WooCommerce aunque la API no lo liste
    (fila huérfana en wc_product_meta_lookup, variación de padre borrado…).
    Así el plan de drafts deja de proponerlo y no tapa la cola de creación.
    """
    if sku and sku.strip():
        _sku_oculto_cache.add(sku.strip().lower())


async def crear_borradores(items: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Crea productos en WooCommerce como BORRADOR (status=draft) vía
    /products/batch, en lotes de 50. `items`: [{sku, nombre, precio, stock}].
    Devuelve {"creados": [{sku, wc_id}], "errores": [{sku, error}]}.
    """
    creados: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []
    async with _client() as cli:
        for i in range(0, len(items), 50):
            lote = items[i:i + 50]
            payload = {"create": [_borrador_wc(it) for it in lote]}
            try:
                # Un batch de 50 altas puede tardar bastante en Woo compartido.
                r = await cli.post("/products/batch", json=payload, timeout=180.0)
                r.raise_for_status()
                resultados = r.json().get("create", [])
            except Exception as exc:  # noqa: BLE001
                log.warning("crear_borradores: lote %d falló: %s", i // 50 + 1, exc)
                errores.extend({"sku": it["sku"], "error": str(exc)} for it in lote)
                continue
            for it, res in zip(lote, resultados):
                err = res.get("error")
                if err:
                    errores.append({"sku": it["sku"], "error": err.get("message") or str(err)})
                else:
                    creados.append({"sku": it["sku"], "wc_id": res.get("id")})
            log.info(
                "crear_borradores: lote %d procesado (acumulado: %d ok / %d error)",
                i // 50 + 1, len(creados), len(errores),
            )
    return {"creados": creados, "errores": errores}


def _borrador_wc(it: dict[str, Any]) -> dict[str, Any]:
    """Payload de alta en WooCommerce para un producto de Odoo, como draft."""
    p: dict[str, Any] = {
        "name": it.get("nombre") or it["sku"],
        "sku": it["sku"],
        "type": "simple",
        "status": "draft",
    }
    precio = it.get("precio")
    if precio not in (None, "", 0, 0.0):
        p["regular_price"] = f"{float(precio):.2f}"
    stock = it.get("stock")
    if stock is not None:
        p["manage_stock"] = True
        p["stock_quantity"] = max(0, int(stock))
    if it.get("imagen_media_id"):  # imagen de Odoo ya subida a WordPress
        p["images"] = [{"id": it["imagen_media_id"]}]
    if it.get("categoria_wc_id"):  # departamento por prefijo de SKU
        p["categories"] = [{"id": it["categoria_wc_id"]}]
    return p


# ── WordPress Media (imágenes) ─────────────────────────────────────────────────

_MIME_EXT = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/webp": "webp", "image/gif": "gif",
}


async def subir_imagen_wp(nombre: str, data: bytes, mime: str = "image/png") -> tuple[int, str] | None:
    """
    Sube una imagen a la librería de medios de WordPress (Application Password).
    Devuelve (media_id, url) o None si falló. `mime` define el Content-Type y la
    extensión del archivo (por defecto PNG, para no cambiar el flujo existente).
    """
    import unicodedata

    mime = (mime or "image/png").split(";")[0].strip().lower()
    ext = _MIME_EXT.get(mime, "png")
    # Los headers HTTP solo aceptan ASCII: SKUs con Ñ/acentos se transliteran
    # (BAÑ-0488 → BAN-0488) solo para el nombre de archivo.
    ascii_nombre = (
        unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
        or "imagen"
    )
    url = f"{settings.wc_url.rstrip('/')}/wp-json/wp/v2/media"
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_nombre}.{ext}"',
        "Content-Type": mime,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as cli:
            r = await cli.post(
                url, content=data, headers=headers,
                auth=(settings.wp_user, settings.wp_app_password),
            )
        if r.status_code in (200, 201):
            media = r.json()
            return media.get("id"), media.get("source_url")
        log.warning("WP media %s → HTTP %d: %s", nombre, r.status_code, r.text[:120])
    except Exception as exc:  # noqa: BLE001
        log.warning("WP media %s falló: %s", nombre, exc)
    return None


async def asignar_imagenes(asignaciones: list[dict[str, int]]) -> int:
    """
    Asigna imagen principal a productos existentes en UNA llamada batch.
    `asignaciones`: [{"wc_id": ..., "media_id": ...}] (máx. 100 por lote).
    Devuelve cuántos se actualizaron.
    """
    ok = 0
    async with _client() as cli:
        for i in range(0, len(asignaciones), 50):
            lote = asignaciones[i:i + 50]
            payload = {"update": [
                {"id": a["wc_id"], "images": [{"id": a["media_id"]}]} for a in lote
            ]}
            try:
                r = await cli.post("/products/batch", json=payload, timeout=180.0)
                r.raise_for_status()
                ok += sum(1 for u in r.json().get("update", []) if not u.get("error"))
            except Exception as exc:  # noqa: BLE001
                log.warning("asignar_imagenes: lote %d falló: %s", i // 50 + 1, exc)
    return ok


# ── Galería de producto (leer / reemplazar / eliminar) ─────────────────────────
# WooCommerce guarda la galería en el PRODUCTO (el padre si es variable). Una
# variación NO tiene galería propia → siempre se opera sobre el padre. La lista
# `images` viene ordenada: images[0] = portada, el resto = galería.

async def _producto_con_imagenes(cli: httpx.AsyncClient, wc_id: int) -> dict | None:
    r = await cli.get(f"/products/{wc_id}", params={"_fields": "id,type,parent_id,images"})
    if r.status_code == 200:
        return r.json()
    return None


async def galeria_producto(wc_id: int | None, sku: str | None = None) -> dict | None:
    """
    Devuelve la galería COMPLETA (con id y posición de cada imagen) del producto.
    Si el objeto es una variación, resuelve el PADRE (ahí vive la galería).

    Retorna: {wc_id, parent_id, es_variacion, portada:{id,src,position}|None,
              imagenes:[{id,src,position}]}  ó None si no se pudo resolver.
    """
    async with _client() as cli:
        prod: dict | None = None
        if wc_id:
            prod = await _producto_con_imagenes(cli, wc_id)
        # Fallback por SKU (p. ej. si wc_id apunta a una variación, que /products/{id}
        # no devuelve como producto de primer nivel).
        if prod is None and sku:
            r = await cli.get("/products", params={"sku": sku, "_fields": "id,type,parent_id,images"})
            if r.status_code == 200 and r.json():
                prod = r.json()[0]
        if prod is None:
            return None

        parent_id = prod.get("id") or wc_id
        es_var = False
        # Variación → subir al padre para leer su galería.
        if prod.get("type") == "variation" or prod.get("parent_id"):
            pid = prod.get("parent_id") or parent_id
            if pid and pid != prod.get("id"):
                padre = await _producto_con_imagenes(cli, pid)
                if padre:
                    prod = padre
                    parent_id = pid
                    es_var = True

        imgs = [
            {"id": i.get("id"), "src": i.get("src"), "position": i.get("position", idx)}
            for idx, i in enumerate(prod.get("images") or [])
            if i.get("id")
        ]
        return {
            "wc_id": wc_id,
            "parent_id": parent_id,
            "es_variacion": es_var,
            "portada": imgs[0] if imgs else None,
            "imagenes": imgs,
        }


async def reemplazar_imagenes_galeria(parent_id: int, id_map: dict[int, int]) -> dict:
    """
    Reemplaza old_id → new_id en la galería del producto en UN SOLO PUT (evita la
    race condition de escrituras paralelas), y actualiza también:
      - el CSV `commercekit_image_gallery` (best-effort, si el tema lo usa)
      - `image.id` de cada variación hija que apuntaba a una imagen reemplazada.
    `id_map`: {wc_image_id_viejo: wc_image_id_nuevo}.
    """
    res = {"parent_ok": False, "variaciones_ok": 0, "variaciones_fail": 0}
    if not id_map:
        return res
    async with _client() as cli:
        r = await cli.get(f"/products/{parent_id}", params={"_fields": "id,images,meta_data"})
        if r.status_code != 200:
            log.warning("reemplazar_imagenes_galeria: GET %d → %d", parent_id, r.status_code)
            return res
        prod = r.json()

        # 1) images[] del padre, preservando el orden (posición).
        nuevas = [{"id": id_map.get(i.get("id"), i.get("id"))} for i in prod.get("images", []) if i.get("id")]
        body: dict[str, Any] = {"images": nuevas}

        # 2) commercekit_image_gallery (CSV por variante) — best-effort.
        try:
            ck = None
            for m in prod.get("meta_data", []):
                if m.get("key") == "commercekit_image_gallery":
                    ck = m.get("value")
                    break
            if isinstance(ck, dict) and ck:
                nuevo_ck = {}
                for k, csv_str in ck.items():
                    ids = []
                    for x in str(csv_str).split(","):
                        x = x.strip()
                        if x.isdigit():
                            ids.append(str(id_map.get(int(x), int(x))))
                    nuevo_ck[k] = ",".join(ids)
                if nuevo_ck != ck:
                    body["meta_data"] = [{"key": "commercekit_image_gallery", "value": nuevo_ck}]
        except Exception as exc:  # noqa: BLE001
            log.warning("commercekit gallery swap: %s", exc)

        rp = await cli.put(f"/products/{parent_id}", json=body, timeout=90.0)
        res["parent_ok"] = rp.status_code == 200
        if not res["parent_ok"]:
            log.warning("reemplazar_imagenes_galeria: PUT %d → %d %s", parent_id, rp.status_code, rp.text[:160])

        # 3) image.id de variaciones hijas.
        try:
            page = 1
            while True:
                rv = await cli.get(
                    f"/products/{parent_id}/variations",
                    params={"per_page": 100, "page": page, "_fields": "id,image"},
                )
                if rv.status_code != 200:
                    break
                lote = rv.json()
                if not lote:
                    break
                for var in lote:
                    vimg = (var.get("image") or {}).get("id")
                    if vimg in id_map:
                        rvp = await cli.put(
                            f"/products/{parent_id}/variations/{var['id']}",
                            json={"image": {"id": id_map[vimg]}}, timeout=60.0,
                        )
                        if rvp.status_code == 200:
                            res["variaciones_ok"] += 1
                        else:
                            res["variaciones_fail"] += 1
                if len(lote) < 100:
                    break
                page += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("reemplazar_imagenes_galeria variaciones: %s", exc)
    return res


async def eliminar_imagen_galeria(parent_id: int, image_id: int) -> bool:
    """
    Desvincula una imagen de la galería del producto (un solo PUT con la galería
    filtrada). NO borra el attachment de la Media Library, solo lo quita del
    producto. Opera sobre el padre.
    """
    async with _client() as cli:
        r = await cli.get(f"/products/{parent_id}", params={"_fields": "id,images"})
        if r.status_code != 200:
            return False
        prod = r.json()
        nuevas = [{"id": i["id"]} for i in prod.get("images", []) if i.get("id") and i.get("id") != image_id]
        rp = await cli.put(f"/products/{parent_id}", json={"images": nuevas}, timeout=90.0)
        if rp.status_code != 200:
            log.warning("eliminar_imagen_galeria: PUT %d → %d %s", parent_id, rp.status_code, rp.text[:160])
        return rp.status_code == 200


async def variantes_de_productos(
    cli: httpx.AsyncClient, productos: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    """
    Variantes de los productos PADRE (`type=variable`), en paralelo y en el mismo
    orden de entrada. Los que no son variables devuelven [].

    Es la misma lectura que usa la vista "Crear Productos" (era `_variantes()`
    dentro de `productos_por_wc_id`); se extrajo para que Productos y Omnicanal
    muestren exactamente las mismas variantes, con los mismos campos.
    """
    sem = asyncio.Semaphore(3)  # concurrencia baja: el hosting bloquea por volumen

    async def _una(p: dict[str, Any]) -> list[dict[str, Any]]:
        if p.get("type") != "variable":
            return []
        async with sem:
            try:
                rv = await cli.get(f"/products/{p['id']}/variations", params={
                    "per_page": 100,
                    "_fields": "id,sku,attributes,price,stock_quantity,status",
                })
                if rv.status_code != 200:
                    return []
                salida = []
                for v in rv.json():
                    ops = " / ".join(
                        a.get("option") or "" for a in (v.get("attributes") or [])
                        if a.get("option")
                    )
                    salida.append({
                        "sku": v.get("sku") or f"WC-{v['id']}",
                        "nombre": ops or None,
                        "precio": _to_float(v.get("price")),
                        "stock": v.get("stock_quantity"),
                        "estado": v.get("status"),
                    })
                return salida
            except Exception as exc:  # noqa: BLE001
                log.warning("variaciones de wc_id=%s fallaron: %s", p.get("id"), exc)
                return []

    return await asyncio.gather(*[_una(p) for p in productos])


def stock_de_padre(p: dict[str, Any], variantes: list[dict[str, Any]]) -> int | None:
    """
    Los padres `variable` no gestionan stock propio (manage_stock=False → None):
    su stock real es la SUMA de las variaciones. Regla de Crear Productos.
    """
    if p.get("type") == "variable" and variantes:
        return sum((v.get("stock") or 0) for v in variantes)
    return p.get("stock_quantity")


async def productos_por_wc_id(wc_ids: list[int]) -> list[dict[str, Any]]:
    """
    Trae los productos completos de WooCommerce para una lista de wc_id (en UNA
    llamada con `include`) y los devuelve normalizados y EN EL MISMO ORDEN que se
    pidieron. Se usa en la vista "Crear Productos": el índice (qué wc_id) sale de
    la DB, pero todos los datos mostrados vienen en vivo de WooCommerce.
    """
    ids = [int(i) for i in wc_ids if i]
    if not ids:
        return []
    campos = (
        "id,name,sku,price,regular_price,sale_price,stock_quantity,"
        "stock_status,status,type,categories,brands,images,permalink"
    )
    async with _client() as cli:
        r = await cli.get("/products", params={
            "include": ",".join(str(i) for i in ids),
            "per_page": min(len(ids), 100),
            "status": "any",
            "_fields": campos,
        })
        r.raise_for_status()
        data = r.json()

        by_id = {p["id"]: p for p in data}
        ordenados = [by_id[i] for i in ids if i in by_id]  # preserva el orden pedido

        variantes_por_prod = await variantes_de_productos(cli, ordenados)

    rutas = await asyncio.gather(*[_categoria_de_producto(p) for p in ordenados])
    items: list[dict[str, Any]] = []
    for p, ruta, variantes in zip(ordenados, rutas, variantes_por_prod):
        precio = _to_float(p.get("price"))
        base = _to_float(p.get("regular_price")) or precio
        stock = stock_de_padre(p, variantes)
        items.append({
            "sku": p.get("sku") or f"WC-{p.get('id')}",
            "wc_id": p.get("id"),
            "nombre": p.get("name", ""),
            "imagen": _img(p),
            "marca": _marca(p),
            "precio": precio,
            "precio_base": base,
            "stock": stock,
            "situacion": p.get("status"),
            "estado": p.get("status"),
            "categoria_path": ruta,
            "categoria_id": ruta[-1]["id"] if ruta else None,
            "publicado": p.get("status") == "publish",
            "url": p.get("permalink"),
            "tipo": p.get("type"),
            "variantes": variantes,
            "origen": "woocommerce",
        })
    return items


async def obtener_producto_por_sku(sku: str) -> dict[str, Any] | None:
    try:
        async with _client() as cli:
            r = await cli.get("/products", params={"sku": sku, "_fields": "id,name,sku,type,price,regular_price,sale_price,stock_quantity,status,categories,brands,images,description,short_description,attributes,permalink"})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("WooCommerce obtener_producto_por_sku(%s) falló: %s", sku, exc)
        return None
    if not data:
        return None
    p = data[0]
    ruta = await _categoria_de_producto(p)
    imgs = [i.get("src") for i in (p.get("images") or []) if i.get("src")]
    return {
        "sku": p.get("sku") or f"WC-{p.get('id')}",
        "wc_id": p.get("id"),
        "tipo": p.get("type"),
        "nombre": p.get("name", ""),
        "imagen": imgs[0] if imgs else None,
        "imagenes": imgs,
        "marca": _marca(p),
        "descripcion": p.get("description"),
        "descripcion_corta": p.get("short_description"),
        "atributos": _atributos(p),
        "precio": _to_float(p.get("price")),
        "precio_base": _to_float(p.get("regular_price")),
        "precio_oferta": _to_float(p.get("sale_price")),
        "stock": p.get("stock_quantity"),
        "estado": p.get("status"),
        "categoria_path": ruta,
        "categoria_id": ruta[-1]["id"] if ruta else None,
        "url": p.get("permalink"),
    }


async def ping() -> bool:
    try:
        async with _client() as cli:
            r = await cli.get("/products", params={"per_page": 1, "_fields": "id"})
            return r.status_code == 200
    except Exception as exc:  # noqa: BLE001
        log.warning("WooCommerce ping falló: %s", exc)
        return False


def _to_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ── Contenido del producto (título / descripción / atributos) → WooCommerce ─────
async def guardar_contenido_wc(
    wc_id: int,
    titulo: str | None = None,
    descripcion: str | None = None,
    atributos: list[dict[str, Any]] | None = None,
) -> bool:
    """
    Guarda el CONTENIDO editable del producto en WooCommerce (canal General):
      - `titulo`       → name
      - `descripcion`  → description
      - `atributos`    → atributos CUSTOM ([{nombre, valor}]).

    Los atributos por TAXONOMÍA (id>0, incluidos los que definen variaciones) se
    PRESERVAN tal cual; solo se reemplazan los custom (id=0). Así no se rompen las
    variantes de un producto variable.
    """
    payload: dict[str, Any] = {}
    if titulo is not None:
        payload["name"] = titulo
    if descripcion is not None:
        payload["description"] = descripcion

    async with _client() as cli:
        if atributos is not None:
            actuales: list[dict[str, Any]] = []
            r = await cli.get(f"/products/{wc_id}", params={"_fields": "id,attributes"})
            if r.status_code == 200:
                actuales = r.json().get("attributes") or []
            preservar = [a for a in actuales if a.get("id")]  # taxonomía / variación
            custom = [
                {
                    "id": 0,
                    "name": (a.get("nombre") or "").strip(),
                    "options": [str(a.get("valor") or "")],
                    "visible": True,
                    "variation": False,
                }
                for a in atributos
                if (a.get("nombre") or "").strip()
            ]
            payload["attributes"] = preservar + custom

        if not payload:
            return False
        rp = await cli.put(f"/products/{wc_id}", json=payload, timeout=120.0)
        if rp.status_code != 200:
            log.warning("guardar_contenido_wc %d → %d %s", wc_id, rp.status_code, rp.text[:200])
            return False
    return True
