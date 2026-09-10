"""
wp_db.py — Lecturas DIRECTAS a la base de datos de WordPress/WooCommerce.

Decisión del usuario (2026-07-02): las CONSULTAS van directo a MySQL (inmunes
al anti-bot del hosting y muchísimo más rápidas); la API REST de Woo queda solo
para ESCRITURAS (crear productos, actualizar stock/costo/status).

Se activa solo: si WPDB_NAME/WPDB_USER/WPDB_PASSWORD están en el .env, todos
los lectores (índice de drafts, catálogo general, lookups del sync) usan esta
vía; si no, cae al método por API de siempre.

Esquema WP relevante:
  {P}posts      : productos (post_type=product) y variaciones (product_variation)
  {P}postmeta   : _sku, _stock, _manage_stock, costo…
  {P}terms / {P}term_taxonomy / {P}term_relationships : categorías (product_cat)
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
from dbutils.pooled_db import PooledDB
from pymysql.cursors import DictCursor

from config import settings

log = logging.getLogger("omnicanal.wp_db")

_pool: PooledDB | None = None
_ok: bool | None = None
_ok_ts: float = 0.0


def _prefix() -> str:
    return settings.wpdb_prefix or "wp_"


def _get_pool() -> PooledDB:
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=4,
            mincached=0,
            maxcached=2,
            blocking=True,
            ping=4,
            host=settings.wpdb_host or settings.db_host,
            port=settings.wpdb_port,
            user=settings.wpdb_user,
            password=settings.wpdb_password,
            database=settings.wpdb_name,
            cursorclass=DictCursor,
            connect_timeout=10,
            read_timeout=60,
            charset="utf8mb4",
            autocommit=True,
        )
    return _pool


@contextmanager
def _cursor() -> Iterator[DictCursor]:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


def _fetch_all(sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
    with _cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def disponible() -> bool:
    """True si hay credenciales de la DB de WordPress y la conexión funciona."""
    global _ok, _ok_ts
    if not (settings.wpdb_name and settings.wpdb_user and settings.wpdb_password):
        return False
    if _ok is not None and (time.time() - _ok_ts) < 300:
        return _ok
    try:
        _fetch_all("SELECT 1")
        _ok = True
    except Exception as exc:  # noqa: BLE001
        log.warning("DB de WordPress no disponible: %s", exc)
        _ok = False
    _ok_ts = time.time()
    return _ok


def _categorias_por_post(ids: list[int]) -> dict[int, list[str]]:
    """{ post_id: [nombres de categoría] } para los productos dados."""
    P = _prefix()
    salida: dict[int, list[str]] = {}
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        ph = ",".join(["%s"] * len(chunk))
        rows = _fetch_all(
            f"""SELECT tr.object_id, t.name
                FROM {P}term_relationships tr
                JOIN {P}term_taxonomy tt
                     ON tt.term_taxonomy_id = tr.term_taxonomy_id
                    AND tt.taxonomy = 'product_cat'
                JOIN {P}terms t ON t.term_id = tt.term_id
                WHERE tr.object_id IN ({ph})""",
            tuple(chunk),
        )
        for r in rows:
            salida.setdefault(r["object_id"], []).append(r["name"])
    return salida


def indice_drafts(aplanar: bool = False) -> list[dict[str, Any]]:
    """
    Todo lo que le toca a CREAR PRODUCTOS en una consulta (reemplaza ~50
    requests HTTP): [{wc_id, sku, nombre, estado, stock, categorias}], más
    recientes primero.

    Incluye `draft` E `inprogress` (regla de Brandon, 29-jul): la pestaña es "lo
    que falta trabajar". Antes traía solo `draft`, así que los ~229 `inprogress`
    no salían aquí y se colaban en Productos, que es justo la pestaña de lo ya
    resuelto.

    Con `aplanar=True` el índice deja de ser de PADRES y pasa a ser de SKUs: los
    productos con variantes vivas salen y entran sus hijas, UNA POR FILA, para
    que cada una se procese por separado (Brandon, 9-sep-2026, a petición de las
    KAM). Las que ya llevan `META_PROCESADA` NO aparecen: ya se fueron a
    Productos por su cuenta, sin esperar a las hermanas.

    Ojo con una cosa que se intentó y se descartó: la primera versión aplanaba
    la pestaña sin marca por variante, y entonces no quedaba dónde disparar el
    alta ni cómo saber qué faltaba. La marca es lo que hace viable el aplanado
    aquí; sin ella, aplanar cierra la puerta de salida.
    """
    P = _prefix()
    if aplanar:
        # `parent_id` sale de la MISMA consulta: resolverlo en una segunda pasada
        # costaba 2.66 s de los 4.8 s del índice entero (medido sobre 7,964 filas).
        rows = _fetch_all(
            f"""SELECT p.ID AS wc_id, p.post_title AS nombre, p.post_status AS estado,
                       {_meta_sub('p.ID', '_sku')} AS sku,
                       {_meta_sub('p.ID', '_stock')} AS stock,
                       0 AS parent_id, p.post_date AS fecha
                  FROM {P}posts p
                 WHERE p.post_type = 'product'
                   AND p.post_status IN ('draft', 'inprogress')
                   AND NOT EXISTS (SELECT 1 FROM {P}posts h
                                    WHERE h.post_parent = p.ID
                                      AND h.post_type = 'product_variation'
                                      AND h.post_status <> 'trash')
                 UNION ALL
                SELECT v.ID AS wc_id, v.post_title AS nombre, pa.post_status AS estado,
                       {_meta_sub('v.ID', '_sku')} AS sku,
                       {_meta_sub('v.ID', '_stock')} AS stock,
                       v.post_parent AS parent_id, pa.post_date AS fecha
                  FROM {P}posts v
                  JOIN {P}posts pa ON pa.ID = v.post_parent
                 WHERE v.post_type = 'product_variation' AND v.post_status <> 'trash'
                   AND ({_estado_efectivo('pa', 'v')}) IN ('draft', 'inprogress')
                 ORDER BY fecha DESC""")
    else:
        rows = _fetch_all(
            f"""SELECT p.ID AS wc_id, p.post_title AS nombre, p.post_status AS estado,
                       sku.meta_value AS sku, stock.meta_value AS stock,
                       0 AS parent_id
                FROM {P}posts p
                LEFT JOIN {P}postmeta sku
                       ON sku.post_id = p.ID AND sku.meta_key = '_sku'
                LEFT JOIN {P}postmeta stock
                       ON stock.post_id = p.ID AND stock.meta_key = '_stock'
                WHERE p.post_type = 'product'
                  AND p.post_status IN ('draft', 'inprogress')
                ORDER BY p.post_date DESC"""
        )
    # La categoría de una variación es la de su PADRE: ninguna de las 7,477 tiene
    # `product_cat` propia. Solo se preguntan las que pueden existir (productos y
    # padres): pedirlas también para las 4,526 variantes era casi un segundo de
    # consulta para recibir vacío.
    padre_de_fila = {int(r["wc_id"]): int(r.get("parent_id") or 0) for r in rows}
    con_categoria = sorted({pid or wid for wid, pid in padre_de_fila.items()})
    cats_por_post = _categorias_por_post(con_categoria)
    cats = {wid: cats_por_post.get(pid or wid, [])
            for wid, pid in padre_de_fila.items()}
    salida = []
    for r in rows:
        stock = r.get("stock")
        try:
            stock = int(float(stock)) if stock not in (None, "") else None
        except (ValueError, TypeError):
            stock = None
        salida.append({
            "wc_id": r["wc_id"],
            "sku": (r.get("sku") or f"WC-{r['wc_id']}").strip(),
            "nombre": r.get("nombre") or "",
            "estado": r.get("estado"),
            "stock": stock,
            "categorias": cats.get(r["wc_id"], []),
        })
    return salida


def indice_catalogo() -> list[dict[str, Any]]:
    """
    Catálogo de productos PADRE con cualquier estado útil (para la vista
    GENERAL y derivados). Excluye basura de WP (trash, auto-draft, inherit).
    """
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT p.ID AS wc_id, p.post_title AS nombre, p.post_status AS estado,
                   sku.meta_value AS sku
            FROM {P}posts p
            LEFT JOIN {P}postmeta sku
                   ON sku.post_id = p.ID AND sku.meta_key = '_sku'
            WHERE p.post_type = 'product'
              AND p.post_status NOT IN ('trash', 'auto-draft', 'inherit')
            ORDER BY p.post_date DESC"""
    )
    return [
        {
            "wc_id": r["wc_id"],
            "sku": (r.get("sku") or f"WC-{r['wc_id']}").strip(),
            "nombre": r.get("nombre") or "",
            "estado": r.get("estado"),
        }
        for r in rows
    ]


def skus_existentes() -> set[str]:
    """
    TODOS los SKUs ocupados en Woo (productos + variaciones, sin papelera),
    en minúsculas. Para el diff Odoo↔Woo del sync de drafts.
    """
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT LOWER(TRIM(sku.meta_value)) AS sku
            FROM {P}posts p
            JOIN {P}postmeta sku
                 ON sku.post_id = p.ID AND sku.meta_key = '_sku'
            WHERE p.post_type IN ('product', 'product_variation')
              AND p.post_status != 'trash'
              AND sku.meta_value IS NOT NULL AND sku.meta_value != ''"""
    )
    return {r["sku"] for r in rows}


def precio_regular_variantes(wc_id: int) -> float | None:
    """
    Precio REGULAR de un padre variable, tomado de sus variaciones (el padre no
    guarda `_regular_price` propio). Devuelve el MÍNIMO de las variantes.

    Sin esto, al publicar un padre variable el precio caía al `_price` del padre
    — que es el de OFERTA (caso real CAM-0030: se publicó en $6,514.97 en vez de
    $7,755.92). La regla de la casa es publicar SIEMPRE con el precio regular.
    """
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT pm.meta_value AS v
            FROM {P}posts p
            JOIN {P}postmeta pm ON pm.post_id = p.ID AND pm.meta_key = '_regular_price'
            WHERE p.post_parent = %s AND p.post_type = 'product_variation'
              AND p.post_status <> 'trash'
              AND pm.meta_value IS NOT NULL AND pm.meta_value <> ''""",
        (int(wc_id),),
    )
    precios: list[float] = []
    for r in rows:
        try:
            precios.append(float(r["v"]))
        except (TypeError, ValueError):
            continue
    return min(precios) if precios else None


def postmeta(wc_id: int, keys: list[str]) -> dict[str, Any]:
    """Lee valores de postmeta para un producto: { meta_key: meta_value }."""
    if not keys:
        return {}
    P = _prefix()
    ph = ",".join(["%s"] * len(keys))
    rows = _fetch_all(
        f"""SELECT meta_key, meta_value FROM {P}postmeta
            WHERE post_id = %s AND meta_key IN ({ph})""",
        tuple([wc_id, *keys]),
    )
    return {r["meta_key"]: r["meta_value"] for r in rows}


def pedido_por_ml_order_id(ml_order_id: str) -> int | None:
    """
    `wc_order_id` del pedido que YA tiene esa orden del canal, preguntándole a
    WooCommerce, que es donde el duplicado se vería. None si no existe.

    Último recurso del alta cuando el reclamo se perdió y el ganador nunca
    completó (murió a media petición, p. ej. en el relevo de un deploy): kubera
    no sabe, pero Woo sí. Se toma el más antiguo y se ignora la papelera.
    """
    if not ml_order_id or not disponible():
        return None
    P = _prefix()
    try:
        rows = _fetch_all(
            f"""SELECT MIN(o.id) AS wc_id
                  FROM {P}wc_orders_meta m
                  JOIN {P}wc_orders o ON o.id = m.order_id
                 WHERE m.meta_key = '_ml_order_id' AND m.meta_value = %s
                   AND o.status <> 'trash'""",
            (str(ml_order_id),),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("pedido_por_ml_order_id(%s) falló: %s", ml_order_id, exc)
        return None
    wc = rows[0]["wc_id"] if rows else None
    return int(wc) if wc else None


def padre_de(wc_id: int) -> int | None:
    """
    `post_parent` de una variación, por su propio `wc_id`. None si no es
    variación o no existe.

    Respaldo del webhook cuando el evento no trae `parent_id`: el vínculo vive
    en wp_posts, así que no hace falta que el canal lo mande.
    """
    if not wc_id or not disponible():
        return None
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT post_parent FROM {P}posts
             WHERE ID = %s AND post_type = 'product_variation' LIMIT 1""",
        (int(wc_id),),
    )
    padre = rows[0]["post_parent"] if rows else 0
    return int(padre) if padre else None


def ficha_basica(wc_id: int) -> dict[str, Any] | None:
    """
    { sku, name, status } de un producto por `wc_id`, leído de wp_posts.

    Lo usa el webhook de Woo para registrar al PADRE de una variación: el evento
    de la variante trae `parent_id` pero NADA del padre (ni su SKU ni su
    estado), y sin esto el padre se queda sin acta.
    """
    if not wc_id or not disponible():
        return None
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT sk.meta_value AS sku, p.post_title AS name, p.post_status AS status
              FROM {P}posts p
              LEFT JOIN {P}postmeta sk ON sk.post_id = p.ID AND sk.meta_key = '_sku'
             WHERE p.ID = %s AND p.post_type = 'product'
             LIMIT 1""",
        (int(wc_id),),
    )
    if not rows:
        return None
    sku = (rows[0].get("sku") or "").strip()
    if not sku:
        return None
    return {"sku": sku, "name": (rows[0].get("name") or "").strip() or None,
            "status": rows[0].get("status") or None}


def sku_padre(sku: str) -> str:
    """
    SKU del producto PADRE de una variación, resuelto por la ESTRUCTURA de
    WooCommerce (`post_parent`), no por el nombre del SKU: `CAM-0030-IND` →
    `CAM-0030`. Devuelve "" si el SKU no es una variación o no se encuentra.
    """
    if not sku or not disponible():
        return ""
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT padre.meta_value AS sku_padre
              FROM {P}postmeta hijo
              JOIN {P}posts    v     ON v.ID = hijo.post_id
                                    AND v.post_type = 'product_variation'
              JOIN {P}postmeta padre ON padre.post_id = v.post_parent
                                    AND padre.meta_key = '_sku'
             WHERE hijo.meta_key = '_sku' AND hijo.meta_value = %s
             LIMIT 1""",
        (sku,),
    )
    return str(rows[0]["sku_padre"]) if rows and rows[0].get("sku_padre") else ""


def skus_padre(skus: list[str]) -> dict[str, str]:
    """
    `sku_padre` en LOTE: { sku_variante: sku_padre } en una sola consulta.
    Los SKUs que no son variación simplemente no aparecen en el diccionario.
    """
    limpios = [s.strip() for s in (skus or []) if s and s.strip()]
    if not limpios or not disponible():
        return {}
    P = _prefix()
    salida: dict[str, str] = {}
    for i in range(0, len(limpios), 800):
        chunk = limpios[i:i + 800]
        ph = ",".join(["%s"] * len(chunk))
        try:
            rows = _fetch_all(
                f"""SELECT hijo.meta_value AS sku_hijo, padre.meta_value AS sku_padre
                      FROM {P}postmeta hijo
                      JOIN {P}posts    v     ON v.ID = hijo.post_id
                                            AND v.post_type = 'product_variation'
                      JOIN {P}postmeta padre ON padre.post_id = v.post_parent
                                            AND padre.meta_key = '_sku'
                     WHERE hijo.meta_key = '_sku'
                       AND hijo.meta_value IN ({ph})""",
                tuple(chunk),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("skus_padre falló: %s", exc)
            return salida
        for r in rows:
            hijo, padre = r.get("sku_hijo"), r.get("sku_padre")
            if hijo and padre:
                salida[str(hijo)] = str(padre)
    return salida


def expandir_con_padres(terminos: list[str]) -> tuple[list[str], dict[str, str]]:
    """
    Los buscadores del panel indexan solo productos PADRE (`post_type='product'`)
    y matchean con "el término CABE dentro del SKU". El SKU completo de una
    variante (`ACC-0069-ROS-2XL`) es más LARGO que el de su padre (`ACC-0069`),
    así que nunca cabe: pegar variantes devolvía cero resultados sin explicar por
    qué (reporte del 5-ago con 20 SKUs; 15 eran variantes).

    Aquí se traduce cada variante a su padre por ESTRUCTURA (`post_parent`), no
    por el nombre del SKU, y se AÑADE a la lista (no se reemplaza: el término
    original sigue siendo válido como búsqueda parcial).

    Devuelve (términos expandidos, { variante: padre }) — el mapa sirve para
    explicarle al usuario qué se tradujo.
    """
    limpios = [t.strip() for t in (terminos or []) if t and t.strip()]
    if not limpios:
        return [], {}
    # Solo se busca padre de lo que PUEDA ser un SKU: la caja de búsqueda
    # también recibe texto libre ("disfraz de bruja"), y ahí la consulta sobra.
    candidatos = [t for t in limpios if " " not in t]
    mapa = skus_padre(candidatos) if candidatos else {}
    vistos = {t.upper() for t in limpios}
    salida = list(limpios)
    for padre in mapa.values():
        if padre.upper() not in vistos:
            vistos.add(padre.upper())
            salida.append(padre)
    return salida, mapa


def precios_y_costo_por_wc_id(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """
    Precio activo, precio regular, precio oferta y costo — leídos DIRECTO de
    MySQL (no REST), para no depender del caché/anti-bot del hosting justo
    después de guardar. `items`: [{"wc_id": int, "tipo": str|None}, ...].

    Simples: se leen de su propio postmeta (_price/_regular_price/_sale_price/costo).
    Padres `variable`: este hosting NO persiste el rango regular/oferta en el
    padre (solo `_price`, ya sincronizado desde las variantes) — así que
    precio_base/precio_oferta se resuelven como el MÍNIMO entre sus variantes
    (mismo criterio que usa WooCommerce para el precio activo mostrado).

    Además, de cada padre se devuelve `costo_variantes` = {sku_variante: costo}
    con el `costo` PROPIO que tenga cada variación en su postmeta. Hay familias
    donde las variantes NO son la misma pieza (tallas de colchón, capacidades)
    y su costo real difiere del padre; quien lo pinte decide qué hacer con las
    que no traen costo propio (hoy: heredan el del padre).
    """
    if not items:
        return {}
    P = _prefix()

    def _f(v: Any) -> float | None:
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    ids_todos = [it["wc_id"] for it in items if it.get("wc_id")]
    padres = [it["wc_id"] for it in items if it.get("tipo") == "variable" and it.get("wc_id")]

    salida: dict[int, dict[str, Any]] = {}
    for i in range(0, len(ids_todos), 500):
        chunk = ids_todos[i:i + 500]
        ph = ",".join(["%s"] * len(chunk))
        rows = _fetch_all(
            f"""SELECT post_id, meta_key, meta_value FROM {P}postmeta
                WHERE post_id IN ({ph})
                  AND meta_key IN ('_price', '_regular_price', '_sale_price', 'costo')""",
            tuple(chunk),
        )
        agg: dict[int, dict[str, Any]] = {}
        for r in rows:
            agg.setdefault(r["post_id"], {})[r["meta_key"]] = r["meta_value"]
        for wc_id, d in agg.items():
            salida[wc_id] = {
                "precio": _f(d.get("_price")),
                "precio_base": _f(d.get("_regular_price")),
                "precio_oferta": _f(d.get("_sale_price")),
                "costo": _f(d.get("costo")),
                "costo_variantes": {},
                "precios_variantes": {},
            }
    for wc_id in ids_todos:
        salida.setdefault(wc_id, {"precio": None, "precio_base": None, "precio_oferta": None,
                                  "costo": None, "costo_variantes": {}, "precios_variantes": {}})

    if padres:
        ph = ",".join(["%s"] * len(padres))
        var_rows = _fetch_all(
            f"""SELECT ID, post_parent, post_status FROM {P}posts
                WHERE post_parent IN ({ph}) AND post_type = 'product_variation'""",
            tuple(padres),
        )
        por_padre: dict[int, list[int]] = {}
        for r in var_rows:
            por_padre.setdefault(r["post_parent"], []).append(r["ID"])
        # Las de la papelera siguen contando para el rango del padre, como
        # siempre, pero NO entran a `precios_variantes`: ese mapa va por SKU, y
        # Woo deja que una variación viva repita el SKU de una borrada — la
        # borrada podría pisarle el precio y el wc_id.
        vivas = {r["ID"] for r in var_rows if r.get("post_status") != "trash"}
        var_ids = [vid for ids in por_padre.values() for vid in ids]
        vm: dict[int, dict[str, Any]] = {}
        for i in range(0, len(var_ids), 500):
            chunk = var_ids[i:i + 500]
            ph2 = ",".join(["%s"] * len(chunk))
            vm_rows = _fetch_all(
                f"""SELECT post_id, meta_key, meta_value FROM {P}postmeta
                    WHERE post_id IN ({ph2})
                      AND meta_key IN ('_regular_price', '_sale_price', 'costo', '_sku')""",
                tuple(chunk),
            )
            for r in vm_rows:
                vm.setdefault(r["post_id"], {})[r["meta_key"]] = r["meta_value"]
        for padre_id, var_ids_de_padre in por_padre.items():
            regs = [x for x in (_f(vm.get(v, {}).get("_regular_price")) for v in var_ids_de_padre) if x is not None]
            ofes = [x for x in (_f(vm.get(v, {}).get("_sale_price")) for v in var_ids_de_padre) if x is not None]
            if padre_id not in salida:
                continue
            if regs and salida[padre_id]["precio_base"] is None:
                salida[padre_id]["precio_base"] = min(regs)
            if ofes and salida[padre_id]["precio_oferta"] is None:
                salida[padre_id]["precio_oferta"] = min(ofes)
            # Costo propio por variante, indexado por SKU (la clave con la que
            # viajan las variantes en el payload; no llevan wc_id).
            salida[padre_id]["costo_variantes"] = {
                str(vm.get(v, {}).get("_sku")): _f(vm.get(v, {}).get("costo"))
                for v in var_ids_de_padre
                if vm.get(v, {}).get("_sku") and _f(vm.get(v, {}).get("costo")) is not None
            }
            # Precio regular y de oferta PROPIOS de cada variante, con su wc_id
            # (vista de árbol, Eduardo 10-sep-2026). Hasta la v0.479 solo
            # viajaban al padre, resumidos como el mínimo del rango. El wc_id no
            # es decorativo: el Estudio escribe y publica con él, y abrir una
            # variante con el del padre la haría escribirle encima al padre.
            salida[padre_id]["precios_variantes"] = {
                str(vm[v]["_sku"]): {
                    "wc_id": v,
                    "precio_base": _f(vm[v].get("_regular_price")),
                    "precio_oferta": _f(vm[v].get("_sale_price")),
                }
                for v in var_ids_de_padre
                if v in vivas and vm.get(v, {}).get("_sku")
            }

    return salida


def _parse_product_attributes(serializado: str | None) -> list[dict[str, Any]]:
    """
    Parsea la postmeta `_product_attributes` de WooCommerce (PHP serializado).
    Estructura: { slug: {name, value, position, is_visible, is_taxonomy, ...} }.
    Devuelve [{nombre, valor}] en orden de `position`.
    """
    if not serializado:
        return []
    try:
        import phpserialize
        data = phpserialize.loads(
            serializado.encode("utf-8", "surrogatepass"),
            decode_strings=True, array_hook=dict,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo parsear _product_attributes: %s", exc)
        return []

    attrs: list[tuple[int, str, str]] = []
    for slug, meta in (data or {}).items():
        if not isinstance(meta, dict):
            continue
        # Atributos por taxonomía (pa_*) guardan términos, no un value plano:
        # se omiten aquí (se editan desde WooCommerce).
        if meta.get("is_taxonomy"):
            continue
        nombre = str(meta.get("name") or slug)
        valor = str(meta.get("value") or "")
        try:
            pos = int(meta.get("position") or 0)
        except (ValueError, TypeError):
            pos = 0
        attrs.append((pos, nombre, valor))
    attrs.sort(key=lambda x: x[0])
    return [{"nombre": n, "valor": v} for _, n, v in attrs]


def atributos(wc_id: int) -> list[dict[str, Any]]:
    """Atributos del producto ([{nombre, valor}]) desde `_product_attributes`."""
    metas = postmeta(wc_id, ["_product_attributes"])
    return _parse_product_attributes(metas.get("_product_attributes"))


def _ids_de_imagen(post_id: int) -> list[int]:
    """Los ids de adjunto de UN post: su miniatura primero, luego su galería."""
    metas = postmeta(post_id, ["_thumbnail_id", "_product_image_gallery"])
    ids: list[int] = []
    if metas.get("_thumbnail_id") and str(metas["_thumbnail_id"]).isdigit():
        ids.append(int(metas["_thumbnail_id"]))
    for x in str(metas.get("_product_image_gallery") or "").split(","):
        x = x.strip()
        if x.isdigit() and int(x) not in ids:
            ids.append(int(x))
    return ids


def imagenes(wc_id: int) -> list[str]:
    """
    URLs de imágenes del producto: miniatura primero, después la galería.

    LA GALERÍA DE UNA VARIACIÓN VIVE EN SU PADRE, Y ESO NO ES UN DETALLE.
    WooCommerce le da a cada variación su propia `_thumbnail_id` —la foto de
    ESE color— pero `_product_image_gallery` solo existe en el producto padre.
    Al leer un único post, una variante devolvía UNA imagen mientras el panel
    mostraba nueve, y nadie lo notaba hasta ver el anuncio publicado.

    Medido el 7-sep-2026 sobre los SKUs que Brandon mandó a Amazon:

        104732  padre CAM-0030    miniatura + galería de 8
        104741  CAM-0030-QUE      solo su miniatura
        25109   padre TEC-0935    miniatura + galería de 3
        25203   TEC-0935-ROS      solo su miniatura

    El ORDEN importa: Amazon toma la primera como imagen principal, así que la
    propia de la variante va delante —es la del color que se vende— y las del
    padre la siguen. Los ids repetidos se descartan.

    Para un producto simple `padre_de` devuelve None y esto se comporta igual
    que antes.
    """
    ids = _ids_de_imagen(wc_id)
    padre = padre_de(wc_id)
    if padre:
        for i in _ids_de_imagen(padre):
            if i not in ids:
                ids.append(i)
    if not ids:
        return []
    P = _prefix()
    ph = ",".join(["%s"] * len(ids))
    rows = _fetch_all(
        f"SELECT ID, guid FROM {P}posts WHERE ID IN ({ph}) AND post_type = 'attachment'",
        tuple(ids),
    )
    por_id = {r["ID"]: r["guid"] for r in rows if r.get("guid")}
    return [por_id[i] for i in ids if i in por_id]


def stock_producto(wc_id: int) -> int | None:
    m = postmeta(wc_id, ["_stock"])
    v = m.get("_stock")
    try:
        return int(float(v)) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def metadata_producto(wc_id: int) -> dict[str, Any]:
    """
    Toda la metadata del Estudio para un producto, leída del postmeta (fuente de
    verdad de lo que está publicado en WooCommerce).

    SI ES UNA VARIACIÓN, lo que no tiene propio lo pone el padre, y la respuesta
    trae `heredado` con la lista de campos que vinieron de ahí — el Estudio los
    marca para que nadie confunda «la categoría que elegí para este SKU» con «la
    que estaba puesta en la familia». La distinción importa: la categoría del
    padre MANDA al publicar, y hay padres con la categoría equivocada
    (`VEH-0315` y `VEH-0316` apuntan a "Autos y Camionetas" teniendo hijas que
    son bombas de dirección y soportes de motor). Ver `_NO_HEREDA` para lo que
    NO se hereda nunca: SKU, existencias y códigos de barras.
    """
    claves = [
        "_regular_price", "_sale_price", "_price", "costo",
        "_stock", "_stock_odoo",
        "url_alibaba", "alibaba_price", "comentario_revision", "revision_producto_ok",
        "_weight", "_length", "_width", "_height",
        # LAS DOS LLAVES DE CATEGORÍA, y en este orden por algo: `ml_categoria_id`
        # la escribe el PICKER DEL PANEL (elección humana) y es la que MANDA al
        # publicar (`publicar_ready.py:448`); `ml_category_id` la escribe el
        # predictor de Crear. El Estudio leía solo la del predictor y por eso
        # enseñaba "sin categoría" en productos que sí tenían una elegida —
        # `VEH-0315` y `VEH-0316` entre ellos. Con las variantes publicándose por
        # su cuenta eso pasa de incómodo a peligroso: la pantalla decía "no hay
        # categoría" y el publicador habría mandado MLM1744 ("Autos y
        # Camionetas") a 47 bombas de dirección y soportes de motor.
        "ml_categoria_id", "ml_category_id", "ml_categoria_path",
        "ml_categoria_niveles",
        "ml_categoria_nivel_1", "ml_categoria_nivel_2", "ml_categoria_nivel_3",
        "ml_categoria_nivel_4", "ml_categoria_nivel_5",
        "_product_attributes",
        "_barcode", "_gtin",  # código de barras / GTIN (lo lee el publisher ML)
    ]
    m = postmeta(wc_id, claves)
    heredado: list[str] = []
    padre_id = padre_de(wc_id)
    if padre_id:
        mp = postmeta(padre_id, claves)
        for k in claves:
            if k in _NO_HEREDA:
                continue
            if str(m.get(k) or "").strip():
                continue
            if str(mp.get(k) or "").strip():
                m[k] = mp[k]
                heredado.append(k)

    def _f(k: str) -> float | None:
        v = m.get(k)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    niveles = [
        m[f"ml_categoria_nivel_{i}"].strip()
        for i in range(1, 6)
        if (m.get(f"ml_categoria_nivel_{i}") or "").strip()
    ]
    if not niveles:
        # El picker del panel guarda los niveles como JSON en `ml_categoria_niveles`
        # ([{id, name}, …]); las columnas `_nivel_N` las escribe el otro camino.
        # Sin este respaldo, un producto categorizado desde el panel se veía sin
        # ruta (VEH-0315 / VEH-0316).
        try:
            import json as _json
            niveles = [str(n.get("name") or "").strip()
                       for n in _json.loads(m.get("ml_categoria_niveles") or "[]")
                       if str(n.get("name") or "").strip()]
        except Exception:  # noqa: BLE001
            niveles = []
    def _i(k: str) -> int | None:
        v = _f(k)
        return int(v) if v is not None else None

    return {
        "dinero": {
            "costo": _f("costo"),
            "precio_regular": _f("_regular_price"),
            "precio_oferta": _f("_sale_price"),
            "peso": _f("_weight"),
            "largo": _f("_length"),
            "ancho": _f("_width"),
            "alto": _f("_height"),
        },
        "stock": _i("_stock_odoo") if _i("_stock_odoo") is not None else _i("_stock"),
        "alibaba_url": m.get("url_alibaba"),
        "alibaba_precio": _f("alibaba_price"),
        "producto_correcto": m.get("comentario_revision"),
        "gtin": (m.get("_barcode") or m.get("_gtin") or "").strip() or None,
        # La del PANEL gana, igual que al publicar. Si la pantalla enseñara la
        # del predictor y se publicara con la otra, la revisión previa no serviría
        # de nada — que es justo lo que hay que evitar cuando una variante hereda
        # la categoría de su padre.
        "categoria_ml": {
            "category_id": (str(m.get("ml_categoria_id") or "").strip()
                            or m.get("ml_category_id")),
            "ruta": m.get("ml_categoria_path"),
            "niveles": niveles,
        } if (m.get("ml_categoria_id") or m.get("ml_category_id") or niveles) else None,
        # Los atributos de una variación son los que ELLA fija, no la lista de la
        # familia: el padre `VEH-0315` declara `Modelo = "07 | 08 | 09 | …"` con
        # las 28 opciones, y mostrar eso en la ficha de una sola pieza es falso.
        "atributos": ([{"nombre": a["name"], "valor": (a["options"] or [""])[0]}
                       for a in _atributos_de_variacion(wc_id, padre_id)]
                      if padre_id else
                      _parse_product_attributes(m.get("_product_attributes"))),
        "es_variacion": bool(padre_id),
        "padre_wc_id": padre_id,
        "heredado": heredado,
    }


def producto_wp(wc_id: int) -> dict[str, Any] | None:
    """Fila de `{P}posts` del producto: título, descripción larga y corta."""
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT post_title, post_content, post_excerpt, post_status
            FROM {P}posts WHERE ID = %s LIMIT 1""",
        (wc_id,),
    )
    return rows[0] if rows else None


def postmeta_todo(wc_id: int) -> dict[str, Any]:
    """
    TODO el postmeta del producto: { meta_key: meta_value }.

    El pipeline de publicaciones_ready espera `prod['meta']` con todas las claves
    (necesita `_barcode`, `_gtin`, `ml_category_id`, `ml_attr_*`, …), no una lista
    fija como `postmeta()`.
    """
    P = _prefix()
    rows = _fetch_all(
        f"SELECT meta_key, meta_value FROM {P}postmeta WHERE post_id = %s",
        (wc_id,),
    )
    return {r["meta_key"]: r["meta_value"] for r in rows}


def categorias_wc(wc_id: int) -> list[dict[str, Any]]:
    """
    Categorías WC del producto en forma REST: [{id, name, slug}].

    `wc_category_mapping.resolve_ml_category_from_wc` las usa para detectar que
    una KAM cambió la categoría en el admin de WooCommerce.
    """
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT t.term_id AS id, t.name, t.slug
            FROM {P}term_relationships tr
            JOIN {P}term_taxonomy tt
                 ON tt.term_taxonomy_id = tr.term_taxonomy_id
                AND tt.taxonomy = 'product_cat'
            JOIN {P}terms t ON t.term_id = tt.term_id
            WHERE tr.object_id = %s""",
        (wc_id,),
    )
    return [{"id": r["id"], "name": r["name"], "slug": r["slug"]} for r in rows]


def tags_wc(wc_id: int) -> list[dict[str, str]]:
    """Tags WC del producto en forma REST: [{name}]. Amazon los usa para bullets."""
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT t.name
            FROM {P}term_relationships tr
            JOIN {P}term_taxonomy tt
                 ON tt.term_taxonomy_id = tr.term_taxonomy_id
                AND tt.taxonomy = 'product_tag'
            JOIN {P}terms t ON t.term_id = tt.term_id
            WHERE tr.object_id = %s""",
        (wc_id,),
    )
    return [{"name": r["name"]} for r in rows]


def _terminos_taxonomia(wc_id: int, taxonomia: str) -> list[str]:
    """Valores de un atributo por taxonomía (pa_color, pa_material, …)."""
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT t.name
            FROM {P}term_relationships tr
            JOIN {P}term_taxonomy tt
                 ON tt.term_taxonomy_id = tr.term_taxonomy_id
                AND tt.taxonomy = %s
            JOIN {P}terms t ON t.term_id = tt.term_id
            WHERE tr.object_id = %s""",
        (taxonomia, wc_id),
    )
    return [r["name"] for r in rows]


def atributos_wc(wc_id: int) -> list[dict[str, Any]]:
    """
    Atributos en forma REST de WooCommerce: [{name, options}].

    Incluye los dos tipos, porque `_parse_product_attributes` descarta los de
    taxonomía y ahí viven color/material/talla — justo lo que Amazon
    (`_extract_pa_attrs`) y ML (`build_secondary_attributes`) necesitan.

    SI ES UNA VARIACIÓN, manda el valor FIJADO en ella, no la lista del padre.
    Y esto no es un detalle de presentación: `_product_attributes` solo existe en
    el padre (0 de 7,477 variaciones lo tienen) y ahí `Modelo` vale
    "07 | 08 | 09 | …" — las 28 opciones de la familia. `build_attributes` toma
    `options[0]`, así que publicar `VEH-0315-03` heredando la lista tal cual le
    pondría el modelo de OTRA pieza. La variación sí sabe cuál es el suyo: lo
    guarda en su meta `attribute_<slug>`.
    """
    padre = padre_de(wc_id)
    if padre:
        return _atributos_de_variacion(wc_id, padre)
    serializado = postmeta(wc_id, ["_product_attributes"]).get("_product_attributes")
    if not serializado:
        return []
    try:
        import phpserialize
        data = phpserialize.loads(
            serializado.encode("utf-8", "surrogatepass"),
            decode_strings=True, array_hook=dict,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo parsear _product_attributes (%s): %s", wc_id, exc)
        return []

    salida: list[dict[str, Any]] = []
    for slug, meta in (data or {}).items():
        if not isinstance(meta, dict):
            continue
        nombre = str(meta.get("name") or slug)
        if meta.get("is_taxonomy"):
            opciones = _terminos_taxonomia(wc_id, nombre)
        else:
            crudo = str(meta.get("value") or "")
            opciones = [v.strip() for v in crudo.split("|") if v.strip()]
        if opciones:
            salida.append({"name": nombre, "options": opciones})
    return salida


def _atributos_de_variacion(wc_id: int, padre: int) -> list[dict[str, Any]]:
    """
    Atributos de UNA variación en forma REST: el valor que ella fija, con el
    nombre bonito que declara el padre.

    Cada eje que la variación pincha vive en su meta `attribute_<slug>`. Los de
    taxonomía (`attribute_pa_color`) guardan el SLUG del término y hay que
    traducirlo a su etiqueta; los personalizados traen el texto literal. Es la
    misma traducción que hace `variantes_por_padre` para pintar el nombre de la
    variante, aquí en singular.

    Los ejes que el padre declara pero la variación NO fija (los que valen "Any"
    en WooCommerce) se dejan con las opciones del padre: son atributos del
    producto, no del ejemplar.
    """
    P = _prefix()
    metas = {r["meta_key"]: r["meta_value"] for r in _fetch_all(
        f"""SELECT meta_key, meta_value FROM {P}postmeta
             WHERE post_id = %s AND meta_key LIKE 'attribute\\_%%'""", (wc_id,))}
    fijados: dict[str, str] = {}
    for clave, valor in metas.items():
        v = str(valor or "").strip()
        if v:
            fijados[clave[len("attribute_"):].lower()] = v

    # Etiquetas de los que son taxonomía: (taxonomy, slug) -> nombre legible
    etiquetas: dict[tuple[str, str], str] = {}
    taxos = {(s, v) for s, v in fijados.items() if s.startswith("pa_")}
    if taxos:
        pht = ",".join(["%s"] * len({t for t, _ in taxos}))
        phs = ",".join(["%s"] * len({s for _, s in taxos}))
        for r in _fetch_all(
            f"""SELECT tt.taxonomy, t.slug, t.name
                  FROM {P}terms t
                  JOIN {P}term_taxonomy tt ON tt.term_id = t.term_id
                 WHERE tt.taxonomy IN ({pht}) AND t.slug IN ({phs})""",
                tuple(sorted({t for t, _ in taxos})) + tuple(sorted({s for _, s in taxos}))):
            etiquetas[(r["taxonomy"], r["slug"])] = r["name"]

    salida: list[dict[str, Any]] = []
    for eje in atributos_wc(padre):          # el padre da nombre y orden
        slug = str(eje["name"]).lower()
        valor = fijados.get(slug) or fijados.get(f"pa_{slug}")
        if valor is None:
            salida.append(eje)               # eje "Any": queda la lista del padre
            continue
        salida.append({"name": eje["name"],
                       "options": [etiquetas.get((f"pa_{slug}", valor), valor)]})
    return salida


def productos_por_sku(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    Lookup masivo por SKU para el sync stock+costo (reemplaza ~270 requests):
    { sku: {wc_id, tipo, parent_id, stock, manage_stock, costo} }.
    """
    P = _prefix()
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 500):
        chunk = skus[i:i + 500]
        ph = ",".join(["%s"] * len(chunk))
        rows = _fetch_all(
            f"""SELECT p.ID AS wc_id, p.post_type AS tipo, p.post_parent AS parent_id,
                       sku.meta_value AS sku,
                       stock.meta_value AS stock,
                       manage.meta_value AS manage_stock,
                       costo.meta_value AS costo
                FROM {P}posts p
                JOIN {P}postmeta sku
                     ON sku.post_id = p.ID AND sku.meta_key = '_sku'
                LEFT JOIN {P}postmeta stock
                     ON stock.post_id = p.ID AND stock.meta_key = '_stock'
                LEFT JOIN {P}postmeta manage
                     ON manage.post_id = p.ID AND manage.meta_key = '_manage_stock'
                LEFT JOIN {P}postmeta costo
                     ON costo.post_id = p.ID AND costo.meta_key = 'costo'
                WHERE p.post_type IN ('product', 'product_variation')
                  AND p.post_status != 'trash'
                  AND sku.meta_value IN ({ph})""",
            tuple(chunk),
        )
        for r in rows:
            stock = r.get("stock")
            try:
                stock = int(float(stock)) if stock not in (None, "") else None
            except (ValueError, TypeError):
                stock = None
            salida[r["sku"].strip()] = {
                "wc_id": r["wc_id"],
                "tipo": "variation" if r["tipo"] == "product_variation" else "product",
                "parent_id": r.get("parent_id") or None,
                "stock": stock,
                "manage_stock": (r.get("manage_stock") == "yes"),
                "costo": r.get("costo"),
            }
    return salida


def variantes_por_padre(padres: list[int]) -> dict[int, list[dict[str, Any]]]:
    """
    Variantes de padres `variable` leídas DIRECTO de wp_posts/wp_postmeta en
    3-4 queries POR LOTE — sustituye el N+1 de REST `/products/{id}/variations`
    del listado (una llamada por padre, semáforo 3, era lo que arrastraba a la
    vista Productos). MISMA forma que arma variantes_de_productos:
    {sku, nombre, precio, stock, estado}.

    `nombre` = opciones unidas con " / " en el ORDEN de los atributos del padre
    (postmeta `_product_attributes`), igual que el REST de Woo. Los atributos
    por taxonomía (attribute_pa_*) guardan el SLUG del término: se traduce a su
    etiqueta con wp_terms/wp_term_taxonomy; los custom traen el texto literal.
    """
    if not padres:
        return {}
    P = _prefix()
    ph = ",".join(["%s"] * len(padres))
    var_rows = _fetch_all(
        f"""SELECT ID, post_parent, post_status FROM {P}posts
            WHERE post_parent IN ({ph}) AND post_type = 'product_variation'
              AND post_status <> 'trash'
            ORDER BY post_parent, menu_order, ID""",
        tuple(padres))
    if not var_rows:
        return {p: [] for p in padres}
    var_ids = [r["ID"] for r in var_rows]

    metas: dict[int, dict[str, str]] = {}
    for i in range(0, len(var_ids), 500):
        chunk = var_ids[i:i + 500]
        ph2 = ",".join(["%s"] * len(chunk))
        for r in _fetch_all(
            f"""SELECT post_id, meta_key, meta_value FROM {P}postmeta
                WHERE post_id IN ({ph2})
                  AND (meta_key IN ('_sku', '_price', '_stock')
                       OR meta_key LIKE 'attribute\\_%%')""",
            tuple(chunk)):
            metas.setdefault(r["post_id"], {})[r["meta_key"]] = r["meta_value"]

    # Etiquetas de términos de taxonomía: (taxonomy, slug) -> name
    pares = {(k[len("attribute_"):], v)
             for m in metas.values() for k, v in m.items()
             if k.startswith("attribute_pa_") and v}
    etiquetas: dict[tuple[str, str], str] = {}
    if pares:
        taxos = sorted({t for t, _ in pares})
        slugs = sorted({s for _, s in pares})
        pht = ",".join(["%s"] * len(taxos))
        phs = ",".join(["%s"] * len(slugs))
        for r in _fetch_all(
            f"""SELECT tt.taxonomy, t.slug, t.name
                FROM {P}terms t
                JOIN {P}term_taxonomy tt ON tt.term_id = t.term_id
                WHERE tt.taxonomy IN ({pht}) AND t.slug IN ({phs})""",
            tuple(taxos) + tuple(slugs)):
            etiquetas[(r["taxonomy"], r["slug"])] = r["name"]

    # Orden de atributos del padre (posición en _product_attributes), como REST
    orden_padre: dict[int, dict[str, int]] = {}
    for r in _fetch_all(
        f"""SELECT post_id, meta_value FROM {P}postmeta
            WHERE post_id IN ({ph}) AND meta_key = '_product_attributes'""",
        tuple(padres)):
        try:
            import phpserialize
            data = phpserialize.loads(
                str(r["meta_value"]).encode("utf-8", "surrogatepass"),
                decode_strings=True, array_hook=dict) or {}
            # ORDEN DE INSERCIÓN del dict serializado, no `position`: es lo que
            # respeta el REST de Woo (los empates de position rompían el orden
            # de "Negro / 5 canales", caso TEC-1661).
            orden_padre[r["post_id"]] = {
                str(slug): i for i, slug in enumerate(data.keys())}
        except Exception:  # noqa: BLE001
            orden_padre[r["post_id"]] = {}

    def _f(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    salida: dict[int, list[dict[str, Any]]] = {p: [] for p in padres}
    for r in var_rows:
        m = metas.get(r["ID"], {})
        pos = orden_padre.get(r["post_parent"], {})
        ops = []
        for k, v in m.items():
            if not k.startswith("attribute_") or not v:
                continue
            slug_attr = k[len("attribute_"):]
            texto = (etiquetas.get((slug_attr, v), v)
                     if slug_attr.startswith("pa_") else v)
            ops.append((pos.get(slug_attr, 999), slug_attr, str(texto)))
        ops.sort(key=lambda x: (x[0], x[1]))
        stock = m.get("_stock")
        salida[r["post_parent"]].append({
            "sku": m.get("_sku") or f"WC-{r['ID']}",
            "nombre": " / ".join(t for _, _, t in ops) or None,
            "precio": _f(m.get("_price")),
            "stock": int(float(stock)) if stock not in (None, "") else None,
            "estado": r["post_status"],
        })
    return salida


def categorias_producto() -> dict[int, dict[str, Any]]:
    """
    TODO el árbol de categorías de WooCommerce en UNA consulta (perf 05-ago):
    { term_id: {name, parent} }, mismo contrato que _cargar_categorias() de
    woocommerce.py — que en frío paginaba ~15 llamadas REST en serie (~12 s
    tras cada deploy). Los nombres se des-escapan (&amp; → &) como los del REST.
    """
    import html
    P = _prefix()
    return {
        int(r["term_id"]): {"name": html.unescape(str(r["name"])),
                            "parent": int(r["parent"] or 0)}
        for r in _fetch_all(
            f"""SELECT t.term_id, t.name, tt.parent
                FROM {P}terms t
                JOIN {P}term_taxonomy tt ON tt.term_id = t.term_id
                WHERE tt.taxonomy = 'product_cat'""")
    }


def imagenes_por_wc_id(wc_ids: list[int]) -> dict[int, str]:
    """
    URL de la imagen principal por `wc_id`, **incluidas las VARIANTES**.

    Existe porque el REST `/products?include=` NO devuelve variaciones — está
    documentado en `woocommerce.listar_productos` ("el include no devuelve, p.
    ej. variantes"). En las vistas POR CANAL el listado es por publicación, y lo
    publicado casi siempre es la variante, así que `imagenes_por_wc_id` (que va
    por REST) devolvía vacío y TODA variante salía con el recuadro gris: 7,411
    tarjetas, y 6,882 de ellas con su foto propia esperando en la librería de
    medios (medido el 26-ago-2026).

    Una variante sin `_thumbnail_id` propio HEREDA la del padre, que es lo mismo
    que hace WooCommerce al pintar la ficha (529 variantes están en ese caso).

    Tres queries por lote, no una por id: es el mismo criterio que
    `variantes_por_padre`, que existe justamente para no volver al N+1.
    """
    ids = [int(i) for i in wc_ids if i]
    if not ids:
        return {}
    P = _prefix()

    def _thumbs(post_ids: list[int]) -> dict[int, int]:
        salida: dict[int, int] = {}
        for i in range(0, len(post_ids), 500):
            chunk = post_ids[i:i + 500]
            ph = ",".join(["%s"] * len(chunk))
            for r in _fetch_all(
                f"""SELECT post_id, meta_value FROM {P}postmeta
                    WHERE meta_key = '_thumbnail_id' AND post_id IN ({ph})
                      AND meta_value NOT IN ('', '0')""",
                tuple(chunk)):
                try:
                    salida[int(r["post_id"])] = int(r["meta_value"])
                except (TypeError, ValueError):
                    continue
        return salida

    propio = _thumbs(ids)

    # Los que no tienen imagen propia: se hereda la del padre (si lo tienen).
    huerfanos = [i for i in ids if i not in propio]
    padre_de: dict[int, int] = {}
    if huerfanos:
        for i in range(0, len(huerfanos), 500):
            chunk = huerfanos[i:i + 500]
            ph = ",".join(["%s"] * len(chunk))
            for r in _fetch_all(
                f"""SELECT ID, post_parent FROM {P}posts
                    WHERE ID IN ({ph}) AND post_parent <> 0""",
                tuple(chunk)):
                padre_de[int(r["ID"])] = int(r["post_parent"])
        thumbs_padre = _thumbs(sorted(set(padre_de.values())))
        for hijo, padre in padre_de.items():
            if padre in thumbs_padre:
                propio[hijo] = thumbs_padre[padre]

    if not propio:
        return {}

    # Adjunto -> URL. `guid` es la URL que WordPress guarda para el archivo;
    # si viniera vacía se arma desde `_wp_attached_file`.
    adjuntos = sorted(set(propio.values()))
    urls: dict[int, str] = {}
    base = _base_uploads()
    for i in range(0, len(adjuntos), 500):
        chunk = adjuntos[i:i + 500]
        ph = ",".join(["%s"] * len(chunk))
        for r in _fetch_all(
            f"""SELECT p.ID, p.guid,
                       (SELECT meta_value FROM {P}postmeta
                         WHERE post_id = p.ID AND meta_key = '_wp_attached_file'
                         LIMIT 1) AS archivo
                  FROM {P}posts p WHERE p.ID IN ({ph})""",
            tuple(chunk)):
            u = (r.get("guid") or "").strip()
            if not u and r.get("archivo") and base:
                u = f"{base}/{str(r['archivo']).lstrip('/')}"
            if u:
                urls[int(r["ID"])] = u

    return {wc: urls[adj] for wc, adj in propio.items() if adj in urls}


def _base_uploads() -> str:
    """`https://…/wp-content/uploads`, de las opciones de WordPress."""
    P = _prefix()
    try:
        filas = _fetch_all(
            f"""SELECT option_name, option_value FROM {P}options
                WHERE option_name IN ('siteurl', 'upload_url_path', 'upload_path')""")
    except Exception:  # noqa: BLE001
        return ""
    o = {r["option_name"]: (r["option_value"] or "").strip() for r in filas}
    if o.get("upload_url_path"):
        return o["upload_url_path"].rstrip("/")
    if o.get("siteurl"):
        return f"{o['siteurl'].rstrip('/')}/wp-content/uploads"
    return ""


def maestro_por_sku(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    Todo lo que la pestaña INVENTARIO necesita de WooCommerce, en 3 consultas.

    ``{ sku: {wc_id, tipo, parent_id, parent_sku, parent_status, parent_titulo,
              status, titulo, creado, modificado, stock, gestiona_stock,
              n_galeria, tiene_portada, n_hijas} }``

    Por qué existe teniendo ya `productos_por_sku`: la pestaña necesita el
    ESTADO y las FECHAS, que son la única señal viva de "en qué etapa va este
    SKU y desde cuándo". WooCommerce lleva una escalera curada de cuatro
    peldaños —`draft` → `pending` → `ready` → `publish`— donde `ready` no es de
    WordPress (apareció el 8-feb-2026) y este repo ya la entiende
    (`woocommerce._ESTADOS_LISTOS`). Sin `post_status` no hay tablero.

    Y trae `parent_status` por una razón medida: **una variación `publish` bajo
    un padre que no está publicado NO SE VE en la tienda**, y son 4,498 de las
    7,329 variaciones publicadas. Contarlas como "publicadas" sobreestima lo
    visible 5.4 veces. La pestaña tiene que poder decir "publicada, pero su
    padre está en borrador".
    """
    limpios = [s.strip() for s in skus if s and s.strip()]
    if not limpios:
        return {}
    P = _prefix()
    salida: dict[str, dict[str, Any]] = {}

    for i in range(0, len(limpios), 500):
        chunk = limpios[i:i + 500]
        ph = ",".join(["%s"] * len(chunk))
        filas = _fetch_all(
            f"""SELECT p.ID AS wc_id, p.post_type AS tipo, p.post_parent AS parent_id,
                       p.post_status AS status, p.post_title AS titulo,
                       p.post_date AS creado, p.post_modified AS modificado,
                       sku.meta_value AS sku,
                       stock.meta_value AS stock,
                       manage.meta_value AS gestiona,
                       thumb.meta_value AS thumb,
                       gal.meta_value AS galeria
                FROM {P}posts p
                JOIN {P}postmeta sku
                     ON sku.post_id = p.ID AND sku.meta_key = '_sku'
                LEFT JOIN {P}postmeta stock
                     ON stock.post_id = p.ID AND stock.meta_key = '_stock'
                LEFT JOIN {P}postmeta manage
                     ON manage.post_id = p.ID AND manage.meta_key = '_manage_stock'
                LEFT JOIN {P}postmeta thumb
                     ON thumb.post_id = p.ID AND thumb.meta_key = '_thumbnail_id'
                LEFT JOIN {P}postmeta gal
                     ON gal.post_id = p.ID AND gal.meta_key = '_product_image_gallery'
                WHERE p.post_type IN ('product', 'product_variation')
                  AND p.post_status <> 'trash'
                  AND sku.meta_value IN ({ph})""",
            tuple(chunk),
        )
        for r in filas:
            try:
                stock = int(float(r["stock"])) if r.get("stock") not in (None, "") else None
            except (TypeError, ValueError):
                stock = None
            galeria = [x for x in (r.get("galeria") or "").split(",") if x.strip()]
            salida[str(r["sku"]).strip()] = {
                "wc_id": int(r["wc_id"]),
                "tipo": "variacion" if r["tipo"] == "product_variation" else "producto",
                "parent_id": int(r["parent_id"]) if r.get("parent_id") else None,
                "parent_sku": None,
                "parent_status": None,
                "parent_titulo": None,
                "status": r.get("status") or "",
                "titulo": r.get("titulo") or "",
                "creado": r.get("creado"),
                "modificado": r.get("modificado"),
                "stock": stock,
                "gestiona_stock": (r.get("gestiona") == "yes"),
                "tiene_portada": bool((r.get("thumb") or "").strip()),
                "n_galeria": len(galeria),
                "n_hijas": 0,
            }

    ids = [d["wc_id"] for d in salida.values()]
    padres = sorted({d["parent_id"] for d in salida.values() if d["parent_id"]})

    # 2ª consulta: la ficha del padre de cada variación.
    if padres:
        ph = ",".join(["%s"] * len(padres))
        for r in _fetch_all(
                f"""SELECT p.ID, p.post_status, p.post_title, sku.meta_value AS sku
                    FROM {P}posts p
                    LEFT JOIN {P}postmeta sku
                         ON sku.post_id = p.ID AND sku.meta_key = '_sku'
                    WHERE p.ID IN ({ph})""", tuple(padres)):
            for d in salida.values():
                if d["parent_id"] == int(r["ID"]):
                    d["parent_sku"] = (r.get("sku") or "").strip() or None
                    d["parent_status"] = r.get("post_status") or None
                    d["parent_titulo"] = r.get("post_title") or None

    # 3ª consulta: cuántas hijas tiene cada uno (así se sabe si ES padre; el
    # nombre del SKU no lo dice y `core.products.has_variations` está en FALSE
    # en las 22,389 filas, incluidos los 1,501 padres de verdad).
    if ids:
        ph = ",".join(["%s"] * len(ids))
        for r in _fetch_all(
                f"""SELECT post_parent, COUNT(*) AS n FROM {P}posts
                    WHERE post_type = 'product_variation' AND post_status <> 'trash'
                      AND post_parent IN ({ph})
                    GROUP BY post_parent""", tuple(ids)):
            for d in salida.values():
                if d["wc_id"] == int(r["post_parent"]):
                    d["n_hijas"] = int(r["n"])

    return salida


# ─────────────────────────────────────────────────────────────────────────────
# APLANADO: la variante como FILA PROPIA
#
# Decisión de Brandon (9-sep-2026): un producto variable deja de verse como una
# fila —la del padre, con sus hijas anidadas— y cada variante pasa a ser su
# propia fila, con su SKU, su precio, su stock y su foto. El padre DESAPARECE:
# «el padre es un SKU que no existe» (no se puede comprar ni publicar).
#
# POR QUÉ ESTO VIVE EN MySQL Y NO EN LA REST. `GET /products?include=<id>` NO
# devuelve variaciones: probado el 9-sep contra producción, `include=14711`
# (variante viva) contesta `[]` mientras `include=11459` (su padre) sí trae la
# fila. Curiosamente `?sku=TEC-0664-ROS` SÍ la devuelve — la REST de Woo es
# inconsistente entre `include` y `sku`. Si el índice del listado devolviera
# ids de variante sin más, la pantalla saldría VACÍA, no mal.
#
# EL ESTADO LO MANDA EL PADRE, y no es un capricho: el `post_status` de una
# variación significa «esta combinación está habilitada», no «está viva en la
# tienda» —una hija `publish` de un padre `draft` no se puede comprar—. Medido:
# 4,522 de las 7,477 variantes están en `publish` colgando de un padre en
# `draft`. Filtrar por el estado propio de la hija llenaría la pestaña
# Productos de mercancía que nadie terminó de crear.
# ─────────────────────────────────────────────────────────────────────────────

# LA MARCA DE VARIANTE PROCESADA (Brandon, 9-sep-2026).
#
# Las KAM piden procesar cada variante POR SEPARADO en Crear Productos, y que la
# que ya está lista se vaya sola a Productos «para publicarla, sin esperar a las
# hermanas». En WooCommerce eso no se puede decir con el `post_status`: una
# variación no tiene estado propio que signifique «trabajada» —el suyo dice si
# la combinación está habilitada— y el del padre es de la familia entera.
#
# Así que el alta le pone ESTA meta a la variante al terminar, con la fecha. Es
# lo único que distingue una variante lista de una pendiente, y de ahí sale el
# «estado efectivo» que usan los dos índices: una variante marcada se comporta
# como `pending` aunque su padre siga en `draft`, así que desaparece de Crear y
# aparece en Productos ella sola.
#
# El padre NO se toca al marcar. Se promueve solo al PUBLICAR, y de eso ya se
# encarga `publicar_ready` (mueve el padre a `publish` cuando se publica una
# variante de un padre en borrador).
META_PROCESADA = "_crear_procesada_at"

# Estados que deja pasar cada vista. Espeja `woocommerce.VISTAS`; se repite aquí
# para que el SQL no dependa de importar el módulo de arriba (ciclo de imports).
_VISTAS_SQL: dict[str, set[str] | None] = {
    "productos": {"publish", "pending", "ready", "private"},
    "crear": {"draft", "inprogress"},
    "omnicanal": None,
}

# Estados del filtro explícito del panel. Espeja `woocommerce._ESTADOS_WC`.
_ESTADOS_PANEL: dict[str, set[str]] = {
    "publicado": {"publish"},
    "inactivo": {"pending", "inprogress", "ready", "private"},
}


def _meta_sub(alias_id: str, clave: str) -> str:
    """
    Subconsulta correlacionada para una meta, con `MIN(meta_id)` — la MISMA fila
    que devuelve `get_post_meta($id, $key, true)` en WordPress.

    No es preferencia de estilo: un LEFT JOIN multiplica la fila cuando hay más
    de una meta con esa clave, y eso pasa de verdad. El padre `VEH-0315` tiene
    28 filas `_price` (WooCommerce guarda una por variante para poder ordenar
    por rango de precio) y 398 SKUs del catálogo tienen `_price` repetido. Ese
    JOIN ya duplicó productos en pantalla una vez (COC-0153, ago-2026).
    """
    P = _prefix()
    return (f"(SELECT m.meta_value FROM {P}postmeta m "
            f"WHERE m.post_id = {alias_id} AND m.meta_key = '{clave}' "
            f"ORDER BY m.meta_id LIMIT 1)")


def _estado_efectivo(alias_padre: str, alias_hija: str) -> str:
    """
    Expresión SQL con el estado que le toca a una fila-VARIANTE.

    Normalmente es el del padre: el `post_status` de una variación dice si la
    combinación está habilitada, no en qué punto del trabajo está el producto
    (4,522 de las 7,477 son hijas `publish` de padres en `draft`).

    La excepción es una variante YA PROCESADA colgando de un padre en borrador:
    ésa vale `pending`, que es un estado de Productos. Es lo que hace que una
    variante trabajada salga de Crear y se pueda publicar sin esperar a sus
    hermanas — la regla que pidió Brandon. Si el padre ya está en un estado de
    Productos, la marca no cambia nada.
    """
    return (f"CASE WHEN {alias_padre}.post_status IN ('draft', 'inprogress') "
            f"      AND {_meta_sub(alias_hija + '.ID', META_PROCESADA)} IS NOT NULL "
            f"     THEN 'pending' ELSE {alias_padre}.post_status END")


def indice_plano(
    vista: str = "productos",
    search: str | None = None,
    skus: list[str] | None = None,
    estados: list[str] | None = None,
    orden: str = "reciente",
    page: int = 1,
    per_page: int = 40,
) -> tuple[list[dict[str, Any]], int]:
    """
    Índice del catálogo APLANADO: productos SIN variantes vivas + variaciones.

    Devuelve ([{wc_id, tipo, parent_id}], total) ya paginado. `tipo` es
    "product" o "product_variation": quien hidrata decide por dónde traer cada
    fila (REST para los productos, `variantes_como_productos` para las hijas).

    El universo, medido el 9-sep-2026:

        vista        sueltos   variantes    total    (antes)
        productos      2,346       2,951    5,297     2,908
        crear          3,438       4,526    7,964     4,380
        omnicanal      5,784       7,477   13,261     7,288

    «Sueltos» = productos que NO tienen ninguna variante viva. Los 1,504 padres
    que sí las tienen desaparecen y los reemplazan sus 7,477 hijas.

    El filtro de estado se aplica AL PADRE cuando la fila es una variante (ver
    el bloque de arriba). Por eso el `JOIN` con `pa`: sin él, las 4,522 hijas
    `publish` de padres en `draft` se colarían en Productos.
    """
    if not disponible():
        return [], 0
    P = _prefix()

    permitidos = _VISTAS_SQL.get(vista, _VISTAS_SQL["productos"])
    if estados:                      # filtro explícito del panel
        pedidos: set[str] = set()
        for e in estados:
            pedidos |= _ESTADOS_PANEL.get(e, set())
        permitidos = pedidos if permitidos is None else (permitidos & pedidos)

    def _cond_estado(expr: str, trash_alias: str) -> tuple[str, list[Any]]:
        """`expr` es la expresión de estado de la fila (la columna del producto, o
        el estado EFECTIVO si es variante). `None` = la vista no filtra."""
        if permitidos is None:
            return f"{trash_alias}.post_status <> 'trash'", []
        if not permitidos:           # intersección vacía: no pasa nadie
            return "1 = 0", []
        ph = ",".join(["%s"] * len(permitidos))
        return f"({expr}) IN ({ph})", sorted(permitidos)

    est_p, arg_est_p = _cond_estado("p.post_status", "p")
    est_v, arg_est_v = _cond_estado(_estado_efectivo("pa", "v"), "pa")

    # Búsqueda: sobre el SKU y el título de LA PROPIA FILA, y además sobre el
    # título del padre cuando es variante — ahí vive el nombre real del producto
    # (la hija se llama "Christmas hat - Verde", y 1,541 de 7,477 ni siquiera
    # llevan sufijo: repiten el título del padre tal cual).
    #
    # Aquí NO se usa `expandir_con_padres`: esa función traduce variante → padre
    # porque los buscadores solo indexaban padres. Con el listado aplanado la
    # variante ES la fila, así que traducirla la escondería justo cuando por fin
    # puede mostrarse.
    terminos = [t.strip() for t in (([search] if search else []) + list(skus or []))
                if t and t.strip()]

    sub_stock_p, sub_precio_p = _meta_sub("p.ID", "_stock"), _meta_sub("p.ID", "_price")
    sub_stock_v, sub_precio_v = _meta_sub("v.ID", "_stock"), _meta_sub("v.ID", "_price")

    sql_p = f"""
        SELECT p.ID AS wc_id, 'product' AS tipo, 0 AS parent_id,
               p.post_date AS fecha,
               {sub_stock_p} AS stock, {sub_precio_p} AS precio
          FROM {P}posts p
          LEFT JOIN {P}postmeta sk ON sk.post_id = p.ID AND sk.meta_key = '_sku'
         WHERE p.post_type = 'product' AND p.post_status <> 'trash'
           AND {est_p}
           AND NOT EXISTS (SELECT 1 FROM {P}posts h
                            WHERE h.post_parent = p.ID
                              AND h.post_type = 'product_variation'
                              AND h.post_status <> 'trash')
    """
    sql_v = f"""
        SELECT v.ID AS wc_id, 'product_variation' AS tipo,
               v.post_parent AS parent_id, pa.post_date AS fecha,
               {sub_stock_v} AS stock, {sub_precio_v} AS precio
          FROM {P}posts v
          JOIN {P}posts pa ON pa.ID = v.post_parent AND pa.post_status <> 'trash'
          LEFT JOIN {P}postmeta sk ON sk.post_id = v.ID AND sk.meta_key = '_sku'
         WHERE v.post_type = 'product_variation' AND v.post_status <> 'trash'
           AND {est_v}
    """
    args_p: list[Any] = list(arg_est_p)
    args_v: list[Any] = list(arg_est_v)
    if terminos:
        grupo_p = " OR ".join(
            ["(sk.meta_value LIKE %s OR p.post_title LIKE %s)"] * len(terminos))
        sql_p += f" AND ({grupo_p})"
        for t in terminos:
            args_p += [f"%{t}%", f"%{t}%"]
        grupo_v = " OR ".join(
            ["(sk.meta_value LIKE %s OR v.post_title LIKE %s OR pa.post_title LIKE %s)"]
            * len(terminos))
        sql_v += f" AND ({grupo_v})"
        for t in terminos:
            args_v += [f"%{t}%", f"%{t}%", f"%{t}%"]

    orden_sql = {
        "stock_desc":  "CAST(COALESCE(stock, 0) AS SIGNED) DESC",
        "stock_asc":   "CAST(COALESCE(stock, 0) AS SIGNED) ASC",
        "precio_desc": "CAST(COALESCE(precio, 0) AS DECIMAL(12,2)) DESC",
        "precio_asc":  "CAST(COALESCE(precio, 0) AS DECIMAL(12,2)) ASC",
    }.get(orden, "fecha DESC")

    union = f"({sql_p}) UNION ALL ({sql_v})"
    args = tuple(args_p + args_v)
    try:
        # COUNT y PÁGINA EN PARALELO. Las dos recorren la misma UNION y cada una
        # cuesta lo mismo; en serie, Omnicanal tardaba 2.8 s. Es el mismo truco
        # que ya usa `woocommerce._buscar_wc_ids_wp` y por el mismo motivo: el
        # costo dominante es la ida y vuelta a Hostinger, no la CPU.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_total = ex.submit(_fetch_all, f"SELECT COUNT(*) n FROM ({union}) u", args)
            f_filas = ex.submit(
                _fetch_all,
                # `wc_id DESC` de desempate: sin él, dos filas con el mismo valor
                # de orden pueden salir distinto en páginas distintas y una se
                # repite mientras otra no aparece nunca.
                f"SELECT wc_id, tipo, parent_id FROM ({union}) u "
                f"ORDER BY {orden_sql}, wc_id DESC LIMIT %s OFFSET %s",
                args + (per_page, max(0, (page - 1) * per_page)))
            total = f_total.result()[0]["n"]
            filas = f_filas.result()
    except Exception as exc:  # noqa: BLE001
        log.warning("indice_plano falló: %s", exc)
        return [], 0
    return ([{"wc_id": int(r["wc_id"]), "tipo": r["tipo"],
              "parent_id": int(r["parent_id"] or 0) or None} for r in filas],
            int(total))


def hermanas_pendientes(parent_ids: list[int]) -> dict[int, dict[str, int]]:
    """
    { padre_id: {"total": n, "pendientes": n} } — cuántas variantes vivas tiene
    cada familia y cuántas siguen SIN procesar.

    Es el «se indicará qué variantes faltan por procesar» de Brandon: una
    variante lista se va a Productos sola, y esto es lo que evita que sus
    hermanas se pierdan de vista. No bloquea nada — es información.
    """
    ids = sorted({int(i) for i in (parent_ids or []) if i})
    if not ids or not disponible():
        return {}
    P = _prefix()
    salida: dict[int, dict[str, int]] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join(["%s"] * len(chunk))
        try:
            for r in _fetch_all(
                f"""SELECT v.post_parent AS padre, COUNT(*) AS total,
                           SUM({_meta_sub('v.ID', META_PROCESADA)} IS NULL) AS pendientes
                      FROM {P}posts v
                     WHERE v.post_parent IN ({ph})
                       AND v.post_type = 'product_variation'
                       AND v.post_status <> 'trash'
                     GROUP BY v.post_parent""", tuple(chunk)):
                salida[int(r["padre"])] = {"total": int(r["total"] or 0),
                                           "pendientes": int(r["pendientes"] or 0)}
        except Exception as exc:  # noqa: BLE001
            log.warning("hermanas_pendientes falló: %s", exc)
            return salida
    return salida


def variantes_como_productos(wc_ids: list[int]) -> dict[int, dict[str, Any]]:
    """
    Variaciones vestidas con la MISMA forma que un producto de la REST de Woo:
    { wc_id: {id, name, sku, price, regular_price, sale_price, stock_quantity,
              status, type, categories, brands, images, short_description,
              description, permalink, parent_id} }.

    Así el listado las mezcla con los productos reales y TODO lo de abajo
    —normalización, ruta de categoría, precios frescos, distintivo DROP OFF,
    chips de revisión— sigue funcionando sin enterarse. La alternativa era
    tocar cada uno de esos pasos.

    QUÉ ES PROPIO Y QUÉ SE HEREDA. Medido sobre las 7,477 variantes vivas
    (9-sep-2026), con subconsulta correlacionada:

        campo            propio   hereda del padre
        SKU               7,475          2 (sin SKU: 9271 y 9212)
        stock             7,475          0   ← ninguna hereda stock
        precio            6,041      1,436
        miniatura         6,899        578
        galería             210      7,267
        descripción       3,348      4,129   (meta `_variation_description`)
        categoría             0      7,477   ← una variación NO tiene categoría
        estado                —      7,477   ← decisión de Brandon

    Ojo con dos que suelen darse por sentadas y NO lo son: `post_content` de una
    variación está vacío en las 7,477 (la descripción vive en la meta
    `_variation_description`, no en el post), y de las 1,436 sin precio propio
    solo 495 tienen un padre con precio — 941 se quedan sin precio en ninguna
    parte, y eso hay que verlo en pantalla, no rellenarlo con un cero.

    El ORDEN de las imágenes es el de `imagenes()` y por la misma razón: la
    miniatura propia va PRIMERO porque es la foto de ESE color, y detrás la del
    padre. No se repiten ids.
    """
    ids = [int(i) for i in (wc_ids or []) if i]
    if not ids or not disponible():
        return {}
    P = _prefix()

    filas: list[dict[str, Any]] = []
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        ph = ",".join(["%s"] * len(chunk))
        try:
            filas += _fetch_all(
                f"""SELECT v.ID, v.post_parent, v.post_title, v.menu_order,
                           pa.post_title  AS padre_titulo,
                           pa.post_status AS padre_estado,
                           pa.post_content AS padre_contenido,
                           pa.post_excerpt AS padre_extracto
                      FROM {P}posts v
                      JOIN {P}posts pa ON pa.ID = v.post_parent
                     WHERE v.ID IN ({ph})
                       AND v.post_type = 'product_variation'""",
                tuple(chunk))
        except Exception as exc:  # noqa: BLE001
            log.warning("variantes_como_productos falló: %s", exc)
            return {}
    if not filas:
        return {}

    hijas = [int(r["ID"]) for r in filas]
    padres = sorted({int(r["post_parent"]) for r in filas})
    CLAVES = ("_sku", "_price", "_regular_price", "_sale_price", "_stock",
              "_thumbnail_id", "_product_image_gallery", "_variation_description")
    metas: dict[int, dict[str, str]] = {}
    todos = hijas + padres
    for i in range(0, len(todos), 400):
        chunk = todos[i:i + 400]
        ph = ",".join(["%s"] * len(chunk))
        phk = ",".join(["%s"] * len(CLAVES))
        for r in _fetch_all(
            # `ORDER BY meta_id` + `setdefault`: se queda la PRIMERA fila de cada
            # clave, que es la que WordPress considera «el» valor. Mismo motivo
            # que `_meta_sub` — hay claves repetidas de verdad.
            f"""SELECT post_id, meta_key, meta_value FROM {P}postmeta
                 WHERE post_id IN ({ph}) AND meta_key IN ({phk})
                 ORDER BY meta_id""",
                tuple(chunk) + CLAVES):
            metas.setdefault(int(r["post_id"]), {}).setdefault(
                r["meta_key"], r["meta_value"])

    # Categorías y marcas: SIEMPRE del padre (una variación no tiene ninguna de
    # las dos — verificado: 0 de 7,477 tienen `product_cat` propia).
    cats: dict[int, list[dict[str, Any]]] = {}
    marcas: dict[int, list[dict[str, Any]]] = {}
    if padres:
        php = ",".join(["%s"] * len(padres))
        for taxo, destino in (("product_cat", cats), ("product_brand", marcas)):
            try:
                for r in _fetch_all(
                    f"""SELECT tr.object_id, t.term_id AS id, t.name, t.slug
                          FROM {P}term_relationships tr
                          JOIN {P}term_taxonomy tt
                               ON tt.term_taxonomy_id = tr.term_taxonomy_id
                              AND tt.taxonomy = %s
                          JOIN {P}terms t ON t.term_id = tt.term_id
                         WHERE tr.object_id IN ({php})""",
                        (taxo,) + tuple(padres)):
                    destino.setdefault(int(r["object_id"]), []).append(
                        {"id": r["id"], "name": r["name"], "slug": r["slug"]})
            except Exception as exc:  # noqa: BLE001
                # Sin `product_brand` instalado esto revienta; la fila sigue
                # siendo válida sin marca.
                log.debug("taxonomía %s no disponible: %s", taxo, exc)

    # URLs de las imágenes de golpe
    def _ids_img(mid: dict[str, str]) -> list[int]:
        out: list[int] = []
        t = str(mid.get("_thumbnail_id") or "").strip()
        if t.isdigit() and int(t):
            out.append(int(t))
        for x in str(mid.get("_product_image_gallery") or "").split(","):
            x = x.strip()
            if x.isdigit() and int(x) not in out:
                out.append(int(x))
        return out

    todas_img: list[int] = []
    for r in filas:
        for i in _ids_img(metas.get(int(r["ID"]), {})) + \
                 _ids_img(metas.get(int(r["post_parent"]), {})):
            if i not in todas_img:
                todas_img.append(i)
    url_img: dict[int, str] = {}
    for i in range(0, len(todas_img), 400):
        chunk = todas_img[i:i + 400]
        ph = ",".join(["%s"] * len(chunk))
        for r in _fetch_all(
            f"SELECT ID, guid FROM {P}posts WHERE ID IN ({ph}) AND post_type='attachment'",
                tuple(chunk)):
            if r.get("guid"):
                url_img[int(r["ID"])] = r["guid"]

    base = (settings.wc_url or "").rstrip("/")
    salida: dict[str, Any] = {}
    for r in filas:
        vid, pid = int(r["ID"]), int(r["post_parent"])
        mv, mp = metas.get(vid, {}), metas.get(pid, {})

        def _val(clave: str) -> str | None:
            """Propio si tiene contenido; si no, el del padre."""
            v = str(mv.get(clave) or "").strip()
            if v:
                return v
            p = str(mp.get(clave) or "").strip()
            return p or None

        # Nombre: el título de la variación ya viene como "Padre - Opción", pero
        # 1,541 repiten el del padre sin sufijo. En ésas se pega el SKU para que
        # dos filas hermanas no se lean idénticas en pantalla.
        titulo = (r.get("post_title") or "").strip()
        padre_titulo = (r.get("padre_titulo") or "").strip()
        sku = str(mv.get("_sku") or "").strip()
        if not titulo:
            titulo = padre_titulo
        if titulo == padre_titulo and sku:
            titulo = f"{titulo} — {sku}"

        ids_im = _ids_img(mv)
        for i in _ids_img(mp):
            if i not in ids_im:
                ids_im.append(i)

        salida[vid] = {
            "id": vid,
            "parent_id": pid,
            "sku": sku,                       # "" si no tiene: quien normaliza pone WC-<id>
            "name": titulo,
            "type": "variation",
            # EL ESTADO ES EL DEL PADRE (decisión de Brandon, 9-sep). El propio
            # de la hija dice si la combinación está habilitada, no si está viva.
            "status": r.get("padre_estado"),
            "price": _val("_price"),
            "regular_price": _val("_regular_price"),
            "sale_price": _val("_sale_price"),
            # El stock NO se hereda: las 7,475 con SKU tienen el suyo y
            # `_manage_stock='yes'`. Heredar el del padre sería inventar
            # existencias — un padre variable ni siquiera gestiona stock.
            "stock_quantity": _a_entero(mv.get("_stock")),
            "categories": cats.get(pid, []),
            "brands": marcas.get(pid, []),
            "images": [{"src": url_img[i]} for i in ids_im if i in url_img],
            "short_description": (mv.get("_variation_description")
                                  or r.get("padre_extracto") or ""),
            "description": (mv.get("_variation_description")
                            or r.get("padre_contenido") or ""),
            # `?p=<id>` en vez de armar el permalink a mano: WordPress redirige
            # solo a la URL canónica, sea cual sea la estructura de enlaces.
            "permalink": f"{base}/?p={pid}" if base else None,
        }
    return salida


def _a_entero(v: Any) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# HERENCIA DE POSTMETA: lo que una variante necesita para poder PUBLICARSE
#
# Decisión de Brandon (9-sep-2026): «que herede del padre, ya que se puede
# modificar la categoría en producto antes de publicar».
#
# El porqué, medido sobre los 36 SKUs que mandó ese día:
#
#     meta                 en la VARIANTE   solo en el PADRE
#     _regular_price / _price      36/36            0
#     _stock                       36/36            0
#     ml_categoria_id               0/36           36     ← sin esto NO se publica
#     _product_attributes           0/36           36
#     _weight / _length / …         0/36            2
#
# O sea: la variante tiene lo COMERCIAL (precio, stock) y el padre tiene lo de
# PUBLICAR. Sin herencia, `publicar_ready` frena con «falta categoría ML»
# (publicar_ready.py:520) en las 7,477 variantes del catálogo.
#
# ⚠️ HEREDAR NO ES GRATIS Y NO SIEMPRE ES CORRECTO. La categoría del padre es
# una ELECCIÓN HUMANA que MANDA al publicar (regla de la casa; caso
# TEC-1812-NEG), así que un padre mal categorizado se propaga a todas sus hijas
# de golpe y en silencio. Medido el mismo día: `VEH-0315` y `VEH-0316` tienen
# `ml_categoria_id = MLM1744` = "Autos, Motos y Otros > Autos y Camionetas" —la
# categoría para vender un AUTOMÓVIL— y sus 47 hijas son bombas de dirección y
# soportes de motor. ML acepta esa categoría (`listing_allowed=True`), así que
# no fallaría: publicaría refacciones anunciadas como coches. Por eso el Estudio
# marca lo heredado (`heredadas`) en vez de presentarlo como propio: quien
# publica tiene que VER que esa categoría no la eligió para ese SKU.
# ─────────────────────────────────────────────────────────────────────────────

# Metas que NUNCA se heredan, y el motivo de cada grupo:
_NO_HEREDA: frozenset[str] = frozenset({
    # Identidad de la fila.
    "_sku",
    # Existencias: heredarlas INVENTA mercancía. Además un padre variable ni
    # siquiera gestiona stock (`_manage_stock='no'`), así que lo que se heredaría
    # es un vacío o el total de la familia.
    "_stock", "_manage_stock", "_stock_status", "_stock_odoo", "_backorders",
    # Códigos de barras: cada variante lleva el SUYO por definición. Heredarlos
    # publicaría el MISMO GTIN en N publicaciones distintas de ML.
    "_barcode", "_gtin", "global_unique_id",
    # Historial y reputación del padre: en la hija no significan nada.
    "total_sales", "_wc_average_rating", "_wc_review_count",
    # La foto se resuelve aparte y con orden propio (ver `imagenes()`): la
    # miniatura de la variante va PRIMERO porque es la de ESE color, y detrás la
    # galería del padre. Heredar `_thumbnail_id` rompería ese orden.
    "_thumbnail_id",
})


def postmeta_con_herencia(wc_id: int) -> tuple[dict[str, Any], set[str]]:
    """
    Postmeta de un producto y, si es una VARIACIÓN, lo que le falta rellenado
    con lo del padre.

    Devuelve `(metas, heredadas)` — `heredadas` son las claves que vinieron del
    padre, para que el Estudio pueda distinguirlas de las propias.

    Para un producto simple `padre_de` devuelve None y esto se comporta
    exactamente igual que `postmeta_todo`: mismas claves, ningún heredado.
    """
    propias = postmeta_todo(wc_id)
    padre = padre_de(wc_id)
    if not padre:
        return propias, set()

    heredadas: set[str] = set()
    for clave, valor in postmeta_todo(padre).items():
        if clave in _NO_HEREDA:
            continue
        # Solo rellena HUECOS: una meta propia con contenido siempre gana.
        if str(propias.get(clave) or "").strip():
            continue
        if not str(valor or "").strip():
            continue
        propias[clave] = valor
        heredadas.add(clave)
    return propias, heredadas
