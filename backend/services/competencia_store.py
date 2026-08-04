"""
competencia_store.py — Persistencia del módulo de Competencia.

DOS MODOS, y el de lectura se elige solo:

  • PRODUCCIÓN → BD kubera, esquema `propuestas` (vía `competencia_supabase`).
    Se usa en cuanto hay `SUPABASE_DB_URL`. Hace falta porque el sistema de
    archivos de Railway es efímero: con SQLite el tab arrancaba vacío en cada
    deploy.
  • LOCAL → SQLite en `backend/competencia.db`. Sin credenciales, sin riesgo de
    ensuciar la BD de producción, y `sqlite3` es stdlib.

Las ESCRITURAS siguen siendo locales a propósito: la captura corre con un
navegador (Selenium), que no existe en Railway, así que se raspa desde una máquina
del equipo y se sube a `propuestas`. Mezclar los dos caminos de escritura sin que
el equipo apruebe el esquema sería empujar un borrador a producción.

El archivo SQLite está en .gitignore: son datos desechables, no código.

Tres tablas:
  - skus       : los SKUs vigilados, su categoría (con la ruta partida en niveles
                 para poder agrupar) y su término general de búsqueda.
  - corridas   : bitácora de cada medición (cuándo, cuánto costó, qué falló).
  - resultados : la foto vigente. Tres tipos por SKU — 'general', 'titulo' y
                 'categoria'. SIN histórico: `reemplazar_resultados` borra lo
                 anterior de ese (sku, tipo) antes de escribir. Si algún día se
                 quiere la serie, ese es el único método que cambia.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger("omnicanal.competencia.store")

RUTA_DB = Path(__file__).resolve().parent.parent / "competencia.db"

# Separador de la ruta de categorías de Mercado Libre. Es U+203A (›), NO " > "
# como sugiere el comentario del DDL de channel.categories — verificado contra
# categorias_ml y contra la propia tabla de Supabase.
SEP_RUTA = " › "

_DDL = """
CREATE TABLE IF NOT EXISTS skus (
    sku              TEXT PRIMARY KEY,
    nombre           TEXT NOT NULL,
    origen_nombre    TEXT,              -- 'productos' | 'woocommerce'
    -- La HOJA (la categoría real de la publicación)
    categoria_id     TEXT,
    categoria_nombre TEXT,
    -- La RAÍZ (primer nivel del path). Los ids salen de /categories/{id}
    -- path_from_root: categorias_ml guarda los nombres de cat1..cat4 pero NO sus
    -- ids, y para pedir el ranking de un nivel hace falta el id.
    raiz_id          TEXT,              -- p.ej. MLM1747 = Accesorios para Vehículos
    raiz_nombre      TEXT,
    ruta             TEXT,              -- 'Nivel 1 › Nivel 2 › … › Hoja'
    cat1 TEXT, cat2 TEXT, cat3 TEXT, cat4 TEXT,
    -- Foto del CATÁLOGO (WooCommerce, vía _thumbnail_id), no la de la publicación
    -- de ML: es la que cura el equipo y es la misma para las dos tiendas.
    imagen           TEXT,
    ml_item_id       TEXT,              -- nuestra publicación de referencia
    cuenta           TEXT,
    publicado_ml     INTEGER NOT NULL DEFAULT 0,
    termino_general  TEXT,
    termino_origen   TEXT NOT NULL DEFAULT 'ia',   -- 'ia' | 'manual'
    activo           INTEGER NOT NULL DEFAULT 1,
    actualizado_en   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corridas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    periodo      TEXT NOT NULL,
    origen       TEXT NOT NULL DEFAULT 'manual',
    estado       TEXT NOT NULL DEFAULT 'corriendo',
    skus_medidos INTEGER NOT NULL DEFAULT 0,
    resultados   INTEGER NOT NULL DEFAULT 0,
    visitas_ok   INTEGER NOT NULL DEFAULT 0,
    costo_usd    REAL,
    error        TEXT,
    avisos       TEXT NOT NULL DEFAULT '[]',
    creado_en    TEXT NOT NULL,
    terminado_en TEXT
);

CREATE TABLE IF NOT EXISTS resultados (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sku          TEXT NOT NULL,
    tipo         TEXT NOT NULL,     -- 'general' | 'titulo' | 'categoria'
    termino      TEXT,
    periodo      TEXT NOT NULL,
    posicion     INTEGER,
    externo_id   TEXT NOT NULL,
    titulo       TEXT,
    descripcion  TEXT,
    precio       REAL,
    moneda       TEXT NOT NULL DEFAULT 'MXN',
    imagen       TEXT,
    url          TEXT,
    seller       TEXT,
    marca        TEXT,
    visitas_30d  INTEGER,
    vendidos     INTEGER,
    reviews      INTEGER,
    rating       REAL,
    envio_gratis INTEGER,
    es_full      INTEGER,
    es_nuestro   INTEGER NOT NULL DEFAULT 0,
    sku_nuestro  TEXT,
    capturado_en TEXT NOT NULL,
    UNIQUE (sku, tipo, externo_id)
);

-- Una fila por PUBLICACIÓN nuestra (un SKU está publicado en las dos tiendas y
-- cada publicación tiene su propio precio, sus visitas y sus ventas).
CREATE TABLE IF NOT EXISTS publicaciones (
    sku            TEXT NOT NULL,
    cuenta         TEXT NOT NULL,
    -- 'mercado_libre' hoy; cuando entre Amazon, un ASIN es otra fila con
    -- canal='amazon'. Por eso la vista es POR PUBLICACIÓN y no por SKU.
    canal          TEXT NOT NULL DEFAULT 'mercado_libre',
    ml_item_id     TEXT NOT NULL,
    titulo         TEXT,
    precio         REAL,
    url            TEXT,
    imagen         TEXT,
    estado         TEXT,
    visitas_30d    INTEGER,
    unidades_30d   INTEGER,      -- unidades VENDIDAS en 30 días (de los pedidos)
    actualizado_en TEXT NOT NULL,
    PRIMARY KEY (sku, cuenta)
);

-- Ranking de más vendidos POR CATEGORÍA (no por SKU): varios SKUs comparten
-- categoría — los 3 de Tapetes son MLM162997 — y guardarlo por SKU lo duplicaría.
-- `nivel` distingue la categoría RAÍZ del path (Accesorios para Vehículos) de la
-- ÚLTIMA (Tapetes), que son las dos vistas que pide el tab.
CREATE TABLE IF NOT EXISTS rankings_categoria (
    categoria_id   TEXT NOT NULL,
    nivel          TEXT NOT NULL,     -- 'raiz' | 'hoja'
    periodo        TEXT NOT NULL,
    posicion       INTEGER NOT NULL,  -- del badge oficial "1º MÁS VENDIDO"
    externo_id     TEXT NOT NULL,
    titulo         TEXT,
    precio         REAL,              -- con descuento (el que se paga)
    precio_lista   REAL,              -- precio base, antes del descuento
    descuento      TEXT,              -- '58% OFF'
    vendidos       INTEGER,           -- cota inferior: ML redondea (+50mil → 50000)
    rating         REAL,              -- 0-5
    seller         TEXT,
    imagen         TEXT,
    url            TEXT,
    visitas_30d    INTEGER,           -- /visits/time_window del item real
    reviews        INTEGER,           -- /reviews/item/{id}
    -- El id que va en el URL (/up/MLMU…, /p/MLM…, articulo…/MLM-…): es EL MISMO
    -- que devuelve /highlights, así que es la llave para unir la ficha raspada con
    -- la posición oficial de la API.
    id_pagina      TEXT,
    tipo           TEXT,              -- 'ITEM' | 'PRODUCT' | 'USER_PRODUCT'
    -- La SUBCATEGORÍA a la que pertenece esta fila. Solo se llena en el ranking
    -- de la raíz, y es lo que permite decir "la subcategoría X es la #1 de toda
    -- la categoría padre". Sale de /products/{id}/items; en las entradas de tipo
    -- ITEM queda NULL porque /items de un ajeno es 403.
    item_categoria_id     TEXT,
    item_categoria_nombre TEXT,
    es_nuestro     INTEGER NOT NULL DEFAULT 0,
    sku_nuestro    TEXT,
    capturado_en   TEXT NOT NULL,
    PRIMARY KEY (categoria_id, nivel, externo_id)
);

-- Resultados del BUSCADOR, guardados POR TÉRMINO y no por SKU.
--
-- Varios SKUs comparten el mismo término general —"colchón memory foam" sirve para
-- todos los colchones del catálogo— así que guardarlo por SKU multiplicaba el
-- trabajo y el gasto: cada copia era otra búsqueda raspada y otra corrida de Apify
-- pagada. Con la tabla por término, un SKU nuevo cuyo término ya se midió obtiene
-- sus resultados sin costo y al instante.
--
-- Medido antes del cambio: 150 filas de tipo 'general' para solo 29 términos.
CREATE TABLE IF NOT EXISTS busquedas (
    termino      TEXT NOT NULL,
    periodo      TEXT NOT NULL,
    posicion     INTEGER NOT NULL,   -- ORGÁNICA: los anuncios no cuentan
    externo_id   TEXT NOT NULL,
    titulo       TEXT,
    precio       REAL,
    precio_lista REAL,
    descuento    TEXT,
    vendidos     INTEGER,
    rating       REAL,
    seller       TEXT,
    imagen       TEXT,
    url          TEXT,
    visitas_30d  INTEGER,
    reviews      INTEGER,            -- gratis por /reviews/item/{id}
    envio_gratis INTEGER,
    es_full      INTEGER,
    catalog_id   TEXT,               -- id de producto de catálogo, si lo tiene
    -- `vendidos` solo llega con el detalle del actor, que cuesta 8x por item
    -- ($0.025 contra $0.003) y NO existe por API para publicaciones ajenas.
    -- Con la consulta barata queda en NULL: es un hueco conocido, no un fallo.
    es_nuestro   INTEGER NOT NULL DEFAULT 0,
    sku_nuestro  TEXT,
    capturado_en TEXT NOT NULL,
    PRIMARY KEY (termino, externo_id)
);

CREATE INDEX IF NOT EXISTS idx_busq_termino ON busquedas (termino, posicion);

-- Términos que la gente ESCRIBE en el buscador de ML, por categoría, ordenados
-- por volumen (/trends/MLM/{cat}). Es el insumo del "término general": cruzados
-- contra nuestro título dicen qué palabras nos faltan para capturar tráfico.
-- ML no publica términos de toda categoría (Bujías y Cartuchos de Turbo dan 404,
-- las mismas que tampoco tienen ranking): ausencia = no hay dato, no es un fallo.
CREATE TABLE IF NOT EXISTS terminos_categoria (
    categoria_id TEXT NOT NULL,
    periodo      TEXT NOT NULL,
    posicion     INTEGER NOT NULL,   -- 1 = el más buscado
    termino      TEXT NOT NULL,
    url          TEXT,
    capturado_en TEXT NOT NULL,
    PRIMARY KEY (categoria_id, termino)
);

CREATE INDEX IF NOT EXISTS idx_term_cat ON terminos_categoria (categoria_id, posicion);
CREATE INDEX IF NOT EXISTS idx_rank_cat ON rankings_categoria (categoria_id, nivel, posicion);
CREATE INDEX IF NOT EXISTS idx_res_sku_tipo ON resultados (sku, tipo, posicion);
CREATE INDEX IF NOT EXISTS idx_res_nuestro  ON resultados (sku, tipo, es_nuestro);
CREATE INDEX IF NOT EXISTS idx_skus_cat     ON skus (categoria_id);
"""


def disponible() -> bool:
    """Siempre: en el peor caso está el archivo local, no hay credencial que falte."""
    return True


def _remoto():
    """
    El lector de la BD kubera, o None si no hay `SUPABASE_DB_URL`.

    Se resuelve en cada llamada y no al importar: la variable puede aparecer
    después (Railway aplica cambios de entorno sin rebuild) y no queremos un
    proceso condenado a SQLite por el orden de arranque.
    """
    try:
        from services import competencia_supabase
        return competencia_supabase if competencia_supabase.disponible() else None
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo usar la lectura remota, sigo en local: %s", exc)
        return None


def periodo_actual(hoy: date | None = None) -> str:
    return (hoy or date.today()).replace(day=1).isoformat()


def _con() -> sqlite3.Connection:
    c = sqlite3.connect(RUTA_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


_schema_ok = False


def asegurar_schema() -> None:
    global _schema_ok
    if _schema_ok:
        return
    with _con() as c:
        c.executescript(_DDL)
        # CREATE TABLE IF NOT EXISTS no agrega columnas a una tabla que ya existe,
        # y esta base ya tiene datos de las corridas de prueba. Sin esto, las
        # columnas nuevas solo aparecerían borrando el archivo.
        for tabla, col, tipo in (
            ("rankings_categoria", "reviews", "INTEGER"),
            ("rankings_categoria", "id_pagina", "TEXT"),
            ("rankings_categoria", "tipo", "TEXT"),
            ("rankings_categoria", "item_categoria_id", "TEXT"),
            ("rankings_categoria", "item_categoria_nombre", "TEXT"),
            ("busquedas", "reviews", "INTEGER"),
            ("busquedas", "envio_gratis", "INTEGER"),
            ("busquedas", "es_full", "INTEGER"),
            ("busquedas", "catalog_id", "TEXT"),
        ):
            tiene = {r["name"] for r in c.execute(f"PRAGMA table_info({tabla})")}
            if col not in tiene:
                c.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
                log.info("schema: %s.%s agregada", tabla, col)
    _schema_ok = True


def _filas(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# ── SKUs vigilados ───────────────────────────────────────────────────────────

_CAMPOS_SKU = ("sku", "nombre", "origen_nombre", "categoria_id", "categoria_nombre",
               "raiz_id", "raiz_nombre",
               "ruta", "cat1", "cat2", "cat3", "cat4", "imagen", "ml_item_id",
               "cuenta", "publicado_ml", "termino_general", "termino_origen", "activo")


def guardar_skus(skus: list[dict[str, Any]]) -> int:
    """
    Alta/actualización de los SKUs vigilados.

    El término general NO se pisa si ya lo corrigió una persona
    (termino_origen='manual'): la corrección humana gana sobre cualquier
    propuesta posterior de la IA.
    """
    asegurar_schema()
    listos = [s for s in skus if s.get("sku") and s.get("nombre")]
    if not listos:
        return 0
    ahora = _ahora()
    with _con() as c:
        previos = {r["sku"]: dict(r) for r in c.execute(
            "SELECT sku, termino_general, termino_origen, ml_item_id, cuenta FROM skus")}
        for s in listos:
            f = {k: s.get(k) for k in _CAMPOS_SKU}
            f["publicado_ml"] = 1 if s.get("ml_item_id") else 0
            f["activo"] = 1 if s.get("activo", True) else 0
            f["termino_origen"] = s.get("termino_origen") or "ia"
            prev = previos.get(s["sku"])
            # No degradar lo que ML ya confirmó: la siembra sale de ml_progress,
            # que no conoce las publicaciones hechas fuera del pipeline del panel
            # (caso MUE-0163-TEL). Si ya hay item confirmado y la siembra no trae
            # ninguno, se conserva el que estaba.
            if prev and prev.get("ml_item_id") and not f["ml_item_id"]:
                f["ml_item_id"] = prev["ml_item_id"]
                f["cuenta"] = prev.get("cuenta") or f["cuenta"]
                f["publicado_ml"] = 1
            if prev and prev["termino_origen"] == "manual":
                f["termino_general"] = prev["termino_general"]
                f["termino_origen"] = "manual"
            elif not f["termino_general"] and prev:
                f["termino_general"] = prev["termino_general"]
            c.execute(
                f"INSERT INTO skus ({','.join(_CAMPOS_SKU)}, actualizado_en) "
                f"VALUES ({','.join(['?'] * len(_CAMPOS_SKU))}, ?) "
                f"ON CONFLICT(sku) DO UPDATE SET "
                + ", ".join(f"{k}=excluded.{k}" for k in _CAMPOS_SKU if k != "sku")
                + ", actualizado_en=excluded.actualizado_en",
                [f[k] for k in _CAMPOS_SKU] + [ahora],
            )
    return len(listos)


def listar_skus(solo_activos: bool = True) -> list[dict[str, Any]]:
    r = _remoto()
    if r:
        return r.listar_skus(solo_activos)
    return _listar_skus_local(solo_activos)


def _listar_skus_local(solo_activos: bool = True) -> list[dict[str, Any]]:
    asegurar_schema()
    sql = "SELECT * FROM skus"
    if solo_activos:
        sql += " WHERE activo = 1"
    sql += " ORDER BY raiz_nombre, categoria_nombre, sku"
    with _con() as c:
        return _filas(c.execute(sql))


def actualizar_termino(sku: str, termino: str) -> bool:
    """Corrección manual: marca origen='manual' para que la IA no lo vuelva a pisar."""
    asegurar_schema()
    with _con() as c:
        cur = c.execute(
            "UPDATE skus SET termino_general = ?, termino_origen = 'manual', "
            "actualizado_en = ? WHERE sku = ?", (termino.strip(), _ahora(), sku))
        return cur.rowcount > 0


# ── Corridas ─────────────────────────────────────────────────────────────────

def abrir_corrida(periodo: str | None = None, origen: str = "manual") -> int:
    asegurar_schema()
    with _con() as c:
        cur = c.execute(
            "INSERT INTO corridas (periodo, origen, estado, creado_en) "
            "VALUES (?, ?, 'corriendo', ?)",
            (periodo or periodo_actual(), origen, _ahora()))
        return int(cur.lastrowid)


def cerrar_corrida(corrida_id: int | None, skus_medidos: int, resultados: int,
                   visitas_ok: int, costo_usd: float | None = None,
                   error: str | None = None, avisos: list[str] | None = None) -> None:
    if not corrida_id:
        return
    asegurar_schema()
    with _con() as c:
        c.execute(
            "UPDATE corridas SET estado=?, skus_medidos=?, resultados=?, visitas_ok=?, "
            "costo_usd=?, error=?, avisos=?, terminado_en=? WHERE id=?",
            ("error" if error else "listo", skus_medidos, resultados, visitas_ok,
             costo_usd, error, json.dumps(avisos or [], ensure_ascii=False),
             _ahora(), corrida_id))


def ultima_corrida() -> dict[str, Any] | None:
    asegurar_schema()
    with _con() as c:
        filas = _filas(c.execute("SELECT * FROM corridas ORDER BY id DESC LIMIT 1"))
    if not filas:
        return None
    f = filas[0]
    try:
        f["avisos"] = json.loads(f.get("avisos") or "[]")
    except Exception:  # noqa: BLE001
        f["avisos"] = []
    return f


# ── Resultados ───────────────────────────────────────────────────────────────

_CAMPOS_RES = ("sku", "tipo", "termino", "periodo", "posicion", "externo_id",
               "titulo", "descripcion", "precio", "moneda", "imagen", "url",
               "seller", "marca", "visitas_30d", "vendidos", "reviews", "rating",
               "envio_gratis", "es_full", "es_nuestro", "sku_nuestro")


def reemplazar_resultados(sku: str, tipo: str, periodo: str,
                          filas: list[dict[str, Any]],
                          termino: str | None = None) -> int:
    """
    Borra la medición anterior de (sku, tipo) y escribe la nueva, en una
    transacción. Aquí se materializa el "sin histórico".
    """
    asegurar_schema()
    ahora = _ahora()
    listas = []
    # El scraper PUEDE repetir el mismo externo_id dentro de una búsqueda (el
    # actor devuelve el item en más de una página). Sin este dedup, el INSERT
    # revienta con UNIQUE constraint y se pierde la corrida entera — pasó en la
    # prueba de MUE-0163-TEL. Se conserva la PRIMERA aparición, que es la de
    # mejor posición orgánica.
    vistos: set[str] = set()
    for f in filas:
        ident = f.get("externo_id")
        if not ident or ident in vistos:
            continue
        vistos.add(ident)
        d = {k: f.get(k) for k in _CAMPOS_RES}
        d.update(sku=sku, tipo=tipo, periodo=periodo,
                 termino=termino if termino is not None else f.get("termino"))
        d["moneda"] = d.get("moneda") or "MXN"
        for b in ("envio_gratis", "es_full"):
            d[b] = None if d[b] is None else (1 if d[b] else 0)
        d["es_nuestro"] = 1 if d.get("es_nuestro") else 0
        if d.get("titulo"):
            d["titulo"] = str(d["titulo"])[:500]
        if d.get("descripcion"):
            d["descripcion"] = str(d["descripcion"])[:2000]
        listas.append([d[k] for k in _CAMPOS_RES] + [ahora])
    with _con() as c:
        c.execute("DELETE FROM resultados WHERE sku = ? AND tipo = ?", (sku, tipo))
        if listas:
            c.executemany(
                f"INSERT INTO resultados ({','.join(_CAMPOS_RES)}, capturado_en) "
                f"VALUES ({','.join(['?'] * (len(_CAMPOS_RES) + 1))})", listas)
    return len(listas)


def resultados(sku: str, tipo: str | None = None,
               limite: int | None = None) -> list[dict[str, Any]]:
    r = _remoto()
    if r:
        return r.resultados(sku, tipo, limite)
    asegurar_schema()
    sql = "SELECT * FROM resultados WHERE sku = ?"
    params: list[Any] = [sku]
    if tipo:
        sql += " AND tipo = ?"
        params.append(tipo)
    sql += " ORDER BY tipo, posicion IS NULL, posicion"
    if limite:
        sql += f" LIMIT {int(limite)}"
    with _con() as c:
        return _filas(c.execute(sql, params))


def top_categoria(sku: str, limite: int = 10) -> list[dict[str, Any]]:
    """
    Los más vendidos de la categoría del SKU: el ranking OFICIAL de Mercado Libre
    (`/highlights`), que ya viene ordenado por posición.
    """
    asegurar_schema()
    with _con() as c:
        return _filas(c.execute(
            "SELECT * FROM resultados WHERE sku = ? AND tipo = 'categoria' "
            "ORDER BY posicion IS NULL, posicion LIMIT ?", (sku, limite)))


def posiciones(sku: str | None = None) -> list[dict[str, Any]]:
    """
    Mi posición y el contexto de precios por SKU y tipo de medición. SQLite no
    tiene percentile_cont, así que la mediana se calcula en Python (son decenas
    de filas por medición, no vale un window function).
    """
    asegurar_schema()
    sql = "SELECT * FROM resultados"
    params: list[Any] = []
    if sku:
        sql += " WHERE sku = ?"
        params.append(sku)
    with _con() as c:
        filas = _filas(c.execute(sql, params))

    grupos: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in filas:
        grupos.setdefault((f["sku"], f["tipo"]), []).append(f)

    out = []
    for (s, tipo), rs in grupos.items():
        mios = [r for r in rs if r["es_nuestro"]]
        rivales = [r for r in rs if not r["es_nuestro"] and r.get("precio") is not None]
        precios = sorted(r["precio"] for r in rivales)
        mediana = None
        if precios:
            n = len(precios)
            mediana = (precios[n // 2] if n % 2
                       else (precios[n // 2 - 1] + precios[n // 2]) / 2)
        visitas_rivales = [r["visitas_30d"] for r in rs
                           if not r["es_nuestro"] and r.get("visitas_30d") is not None]
        out.append({
            "sku": s, "tipo": tipo,
            "termino": rs[0].get("termino"),
            "periodo": rs[0].get("periodo"),
            "total_resultados": len(rs),
            "mi_posicion": min((r["posicion"] for r in mios
                                if r.get("posicion") is not None), default=None),
            "mi_precio": next((r["precio"] for r in mios if r.get("precio")), None),
            "mis_visitas_30d": next((r["visitas_30d"] for r in mios
                                     if r.get("visitas_30d") is not None), None),
            "mis_vendidos": next((r["vendidos"] for r in mios
                                  if r.get("vendidos") is not None), None),
            "precio_mediana_rivales": mediana,
            "visitas_max_rival": max(visitas_rivales, default=None),
        })
    return out


_CAMPOS_PUB = ("sku", "cuenta", "canal", "ml_item_id", "titulo", "precio", "url",
               "imagen", "estado", "visitas_30d", "unidades_30d")


def guardar_publicaciones(filas: list[dict[str, Any]]) -> int:
    """
    Upsert de NUESTRAS publicaciones con su ficha y sus métricas de 30 días. Una
    fila por (sku, cuenta): el mismo SKU está publicado en las dos tiendas y cada
    publicación tiene su propio precio, sus visitas y sus ventas.

    `COALESCE` en el UPDATE: un refresco parcial (p. ej. solo visitas) no debe
    borrar el precio ni las unidades que ya escribió otro paso.
    """
    asegurar_schema()
    listas = [[f.get(k) for k in _CAMPOS_PUB] + [_ahora()] for f in filas
              if f.get("sku") and f.get("cuenta") and f.get("ml_item_id")]
    if not listas:
        return 0
    sets = ", ".join(f"{k}=COALESCE(excluded.{k}, {k})"
                     for k in _CAMPOS_PUB if k not in ("sku", "cuenta"))
    with _con() as c:
        c.executemany(
            f"INSERT INTO publicaciones ({','.join(_CAMPOS_PUB)}, actualizado_en) "
            f"VALUES ({','.join(['?'] * (len(_CAMPOS_PUB) + 1))}) "
            f"ON CONFLICT(sku, cuenta) DO UPDATE SET {sets}, "
            f"actualizado_en=excluded.actualizado_en", listas)
    return len(listas)


def marcar_publicadas(pubs: list[dict[str, Any]]) -> int:
    """
    Corrige `publicado_ml` y el item de referencia con lo que ML acaba de decir.

    Hace falta porque la siembra los tomó de `ml_progress`, que no conoce las
    publicaciones hechas fuera del pipeline del panel — un SKU activo en ML salía
    marcado "sin publicar". Se prefiere el item de BEKURA como referencia por
    consistencia con el resto del panel.
    """
    asegurar_schema()
    por_sku: dict[str, dict[str, str]] = {}
    for p in pubs:
        if p.get("sku") and p.get("ml_item_id"):
            por_sku.setdefault(p["sku"], {})[p.get("cuenta") or ""] = p["ml_item_id"]
    if not por_sku:
        return 0
    ahora = _ahora()
    with _con() as c:
        for sku, por_cuenta in por_sku.items():
            cuenta = "BEKURA" if "BEKURA" in por_cuenta else next(iter(por_cuenta))
            c.execute(
                "UPDATE skus SET ml_item_id = ?, cuenta = ?, publicado_ml = 1, "
                "actualizado_en = ? WHERE sku = ?",
                (por_cuenta[cuenta], cuenta, ahora, sku))
    return len(por_sku)


def publicaciones(sku: str | None = None) -> list[dict[str, Any]]:
    """
    Las publicaciones tal como están en la tabla, MÁS la conversión calculada.

    La conversión se agrega aquí y no solo en `tabla()` porque quien lea esta
    función directamente (el detalle de un SKU) también la necesita: sin ella el
    frontend imprimía "undefined%".
    """
    r = _remoto()
    if r:
        return r.publicaciones(sku)
    asegurar_schema()
    sql = "SELECT * FROM publicaciones"
    params: list[Any] = []
    if sku:
        sql += " WHERE sku = ?"
        params.append(sku)
    sql += " ORDER BY sku, cuenta"
    with _con() as c:
        filas = _filas(c.execute(sql, params))
    for f in filas:
        vis, uni = f.get("visitas_30d"), f.get("unidades_30d")
        # Con 0 visitas la conversión es INDEFINIDA, no 0%: un cero se lee como
        # "convierte mal" cuando en realidad nadie vio la publicación.
        f["conversion_30d"] = round(uni / vis * 100, 2) if vis and uni is not None else None
    return filas


_CAMPOS_RANK = ("categoria_id", "nivel", "periodo", "posicion", "externo_id",
                "titulo", "precio", "precio_lista", "descuento", "vendidos",
                "rating", "seller", "imagen", "url", "visitas_30d", "reviews",
                "id_pagina", "tipo", "item_categoria_id", "item_categoria_nombre",
                "es_nuestro", "sku_nuestro")


def reemplazar_ranking(categoria_id: str, nivel: str, periodo: str,
                       filas: list[dict[str, Any]]) -> int:
    """
    Borra el ranking anterior de esa (categoría, nivel) y escribe el nuevo.
    Sin histórico, igual que `resultados`: es la foto del mes.
    """
    asegurar_schema()
    ahora = _ahora()
    listas, vistos = [], set()
    for f in filas:
        ident = f.get("externo_id")
        if not ident or ident in vistos or f.get("posicion") is None:
            continue
        vistos.add(ident)
        d = {k: f.get(k) for k in _CAMPOS_RANK}
        d.update(categoria_id=categoria_id, nivel=nivel, periodo=periodo)
        d["es_nuestro"] = 1 if d.get("es_nuestro") else 0
        listas.append([d[k] for k in _CAMPOS_RANK] + [ahora])
    with _con() as c:
        c.execute("DELETE FROM rankings_categoria WHERE categoria_id = ? AND nivel = ?",
                  (categoria_id, nivel))
        if listas:
            c.executemany(
                f"INSERT INTO rankings_categoria ({','.join(_CAMPOS_RANK)}, capturado_en) "
                f"VALUES ({','.join(['?'] * (len(_CAMPOS_RANK) + 1))})", listas)
    return len(listas)


def ranking_categoria(categoria_id: str, nivel: str | None = None,
                      limite: int = 10) -> list[dict[str, Any]]:
    r = _remoto()
    if r:
        return r.ranking_categoria(categoria_id, nivel, limite)
    asegurar_schema()
    sql = "SELECT * FROM rankings_categoria WHERE categoria_id = ?"
    params: list[Any] = [categoria_id]
    if nivel:
        sql += " AND nivel = ?"
        params.append(nivel)
    sql += f" ORDER BY posicion LIMIT {int(limite)}"
    with _con() as c:
        return _filas(c.execute(sql, params))


def reemplazar_terminos(categoria_id: str, periodo: str,
                        terminos: list[dict[str, Any]]) -> int:
    """Foto del mes de los términos más buscados de una categoría. Sin histórico."""
    asegurar_schema()
    ahora = _ahora()
    listas, vistos = [], set()
    for i, t in enumerate(terminos, start=1):
        termino = (t.get("termino") or t.get("keyword") or "").strip()
        if not termino or termino in vistos:
            continue
        vistos.add(termino)
        listas.append([categoria_id, periodo, t.get("posicion") or i,
                       termino, t.get("url"), ahora])
    with _con() as c:
        c.execute("DELETE FROM terminos_categoria WHERE categoria_id = ?", (categoria_id,))
        if listas:
            c.executemany(
                "INSERT INTO terminos_categoria "
                "(categoria_id, periodo, posicion, termino, url, capturado_en) "
                "VALUES (?,?,?,?,?,?)", listas)
    return len(listas)


def _cubre(termino: str, titulos: list[str]) -> bool:
    """
    ¿Alguno de nuestros títulos contiene TODAS las palabras del término?

    Por palabras y no por substring: ML busca por tokens, y "funda para moto"
    debe dar positivo con un título que diga "Funda Impermeable Para Moto" aunque
    la frase exacta no aparezca.
    """
    palabras = [p for p in termino.lower().split() if p]
    return any(all(p in t for p in palabras) for t in titulos)


def terminos_categoria(categoria_id: str,
                       titulos_por_tienda: dict[str, str] | None = None,
                       limite: int = 20) -> list[dict[str, Any]]:
    """
    Los términos más buscados de la categoría, marcando cuáles cubre cada tienda.

    La cobertura se calcula POR TIENDA porque el título lo es: MUE-0163-TEL se
    llama "Malla De Tela 6x4m Para Jardin…" en BEKURA y "Lona Sombra Reforzada
    4x6m Para Exterior…" en SANCORFASHION, así que cada una responde a búsquedas
    distintas. Colapsarlas en un solo ✓ esconde justo la decisión: qué título
    arreglar y en cuál tienda.

    `cubierto` queda como el OR de las tiendas (¿alguna nos hace encontrables?) y
    `cubierto_por` dice cuáles.
    """
    r = _remoto()
    if r:
        return r.terminos_categoria(categoria_id, titulos_por_tienda, limite)
    asegurar_schema()
    with _con() as c:
        filas = _filas(c.execute(
            f"SELECT * FROM terminos_categoria WHERE categoria_id = ? "
            f"ORDER BY posicion LIMIT {int(limite)}", (categoria_id,)))
    porv = {k: (v or "").lower() for k, v in (titulos_por_tienda or {}).items() if v}
    for f in filas:
        if not porv:
            f["cubierto"], f["cubierto_por"] = None, []
            continue
        quienes = [cuenta for cuenta, t in porv.items() if _cubre(f["termino"], [t])]
        f["cubierto"] = bool(quienes)
        f["cubierto_por"] = quienes
    return filas


def total_terminos(categoria_id: str) -> int:
    """Cuántos términos publica ML para la categoría (0 = no publica ninguno)."""
    r = _remoto()
    if r:
        return r.total_terminos(categoria_id)
    asegurar_schema()
    with _con() as c:
        return c.execute("SELECT COUNT(*) FROM terminos_categoria WHERE categoria_id = ?",
                         (categoria_id,)).fetchone()[0]


def tabla() -> list[dict[str, Any]]:
    """
    Una fila por SKU con sus tres posiciones y el desglose POR TIENDA.

    La conversión se calcula aquí y no en la base: es unidades/visitas, y solo
    tiene sentido si HAY visitas. Con 0 visitas no es 0% — es indefinida, y la UI
    debe mostrar "—" en vez de un cero que se lee como "convierte mal".
    """
    skus = listar_skus()
    pos = {(p["sku"], p["tipo"]): p for p in posiciones()}
    pubs: dict[str, list[dict[str, Any]]] = {}
    for p in publicaciones():
        pubs.setdefault(p["sku"], []).append(p)

    out = []
    for s in skus:
        fila = dict(s)
        mis = pubs.get(s["sku"], [])
        tiendas = []
        for p in mis:
            vis, uni = p.get("visitas_30d"), p.get("unidades_30d")
            tiendas.append({
                "cuenta": p["cuenta"],
                "canal": p.get("canal") or "mercado_libre",
                "ml_item_id": p["ml_item_id"],
                # El título es POR TIENDA y suele ser distinto: MUE-0163-TEL es
                # "Malla De Tela 6x4m…" en BEKURA y "Lona Sombra Reforzada 4x6m…"
                # en SANCORFASHION. Con títulos distintos, la cobertura de términos
                # de búsqueda también es distinta por tienda.
                "titulo": p.get("titulo"),
                "url": p.get("url"),
                "imagen": p.get("imagen"),
                "precio": p.get("precio"),
                "estado": p.get("estado"),
                "visitas_30d": vis,
                "unidades_30d": uni,
                "conversion_30d": (round(uni / vis * 100, 2)
                                   if vis and uni is not None else None),
            })
        fila["tiendas"] = tiendas

        v = [t["visitas_30d"] for t in tiendas if t["visitas_30d"] is not None]
        u = [t["unidades_30d"] for t in tiendas if t["unidades_30d"] is not None]
        fila["visitas_30d"] = sum(v) if v else None
        fila["unidades_30d"] = sum(u) if u else None
        fila["conversion_30d"] = (round(sum(u) / sum(v) * 100, 2)
                                  if v and sum(v) and u else None)
        # La foto y el precio de referencia: los de BEKURA si existe, si no la primera.
        # La foto del catálogo (Woo) manda; la de la publicación de ML es respaldo.
        if not fila.get("imagen"):
            ref = next((t for t in tiendas if t["cuenta"] == "BEKURA"),
                       tiendas[0] if tiendas else None)
            fila["imagen"] = (ref or {}).get("imagen")
        fila["actualizado"] = max((p["actualizado_en"] for p in mis), default=None)

        for tipo, prefijo in (("general", "gen"), ("titulo", "tit"), ("categoria", "cat")):
            pp = pos.get((s["sku"], tipo)) or {}
            fila[f"pos_{prefijo}"] = pp.get("mi_posicion")
            fila[f"total_{prefijo}"] = pp.get("total_resultados")
        fila["periodo"] = (pos.get((s["sku"], "general")) or {}).get("periodo")
        out.append(fila)
    return out


_CAMPOS_BUSQ = ("termino", "periodo", "posicion", "externo_id", "titulo", "precio",
                "precio_lista", "descuento", "vendidos", "rating", "seller",
                "imagen", "url", "visitas_30d", "reviews", "envio_gratis",
                "es_full", "catalog_id", "es_nuestro", "sku_nuestro")


def reemplazar_busqueda(termino: str, periodo: str,
                        filas: list[dict[str, Any]]) -> int:
    """Foto del mes de UN término. Sin histórico, como el resto del módulo."""
    asegurar_schema()
    ahora = _ahora()
    listas, vistos = [], set()
    for f in filas:
        ident = f.get("externo_id")
        if not ident or ident in vistos:
            continue
        vistos.add(ident)
        d = {k: f.get(k) for k in _CAMPOS_BUSQ}
        d.update(termino=termino, periodo=periodo)
        d["es_nuestro"] = 1 if d.get("es_nuestro") else 0
        listas.append([d[k] for k in _CAMPOS_BUSQ] + [ahora])
    with _con() as c:
        c.execute("DELETE FROM busquedas WHERE termino = ?", (termino,))
        if listas:
            c.executemany(
                f"INSERT INTO busquedas ({','.join(_CAMPOS_BUSQ)}, capturado_en) "
                f"VALUES ({','.join(['?'] * (len(_CAMPOS_BUSQ) + 1))})", listas)
    return len(listas)


def busqueda(termino: str, limite: int = 5) -> list[dict[str, Any]]:
    """Los resultados guardados de un término. Vacío = no se ha medido."""
    if not termino:
        return []
    r = _remoto()
    if r:
        return r.busqueda(termino, limite)
    asegurar_schema()
    with _con() as c:
        return _filas(c.execute(
            f"SELECT * FROM busquedas WHERE termino = ? ORDER BY posicion LIMIT {int(limite)}",
            (termino,)))


def terminos_medidos() -> set[str]:
    """Qué términos ya tienen búsqueda. Es lo que evita volver a pagar por ellos."""
    r = _remoto()
    if r:
        return r.terminos_medidos()
    asegurar_schema()
    with _con() as c:
        return {x["termino"] for x in _filas(c.execute(
            "SELECT DISTINCT termino FROM busquedas"))}


def rankings_por_categoria() -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Todo el ranking de una vez, agrupado por (categoria_id, nivel)."""
    r = _remoto()
    if r:
        return r.rankings_por_categoria()
    asegurar_schema()
    with _con() as c:
        filas = _filas(c.execute("SELECT * FROM rankings_categoria ORDER BY posicion"))
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in filas:
        out.setdefault((f["categoria_id"], f["nivel"]), []).append(f)
    return out


def conteo_terminos() -> dict[str, int]:
    """Cuántos términos hay por categoría, en una sola consulta."""
    r = _remoto()
    if r:
        return r.conteo_terminos()
    asegurar_schema()
    with _con() as c:
        return {f["categoria_id"]: f["n"] for f in _filas(c.execute(
            "SELECT categoria_id, COUNT(*) AS n FROM terminos_categoria GROUP BY 1"))}


def vista(canal: str = "mercado_libre") -> list[dict[str, Any]]:
    """
    El árbol que pinta el tab: raíz → subcategorías → nuestros SKUs.

    Un nivel por cada cosa que se pidió ver:
      raíz         top de más vendidos de la categoría padre
      subcategoría conteo de NUESTROS SKUs (el filtro) + su top y sus términos
      SKU          la brecha contra el mercado, visible sin filtrar nada

    La BRECHA es la columna que manda. El precio solo no dice nada; $1,053 contra
    una mediana de $199 sí. Se calcula contra la MEDIANA del ranking de la
    subcategoría y no contra el promedio: un solo producto de $1,189 en el top 20
    de Tapetes movería el promedio y taparía el problema.
    """
    filas = [f for f in tabla()
             if any((t.get("canal") or "mercado_libre") == canal for t in f["tiendas"])
             or not f["tiendas"]]

    raices: dict[str, dict[str, Any]] = {}
    for f in filas:
        rid = f.get("raiz_id") or "sin_categoria"
        r = raices.setdefault(rid, {
            "raiz_id": f.get("raiz_id"),
            "raiz_nombre": f.get("raiz_nombre") or "Sin categoría",
            "subcategorias": {},
            "skus": [],
        })
        cid = f.get("categoria_id") or "sin_categoria"
        sub = r["subcategorias"].setdefault(cid, {
            "categoria_id": f.get("categoria_id"),
            "categoria_nombre": f.get("categoria_nombre") or "Sin categoría",
            "n_skus": 0,
        })
        sub["n_skus"] += 1
        r["skus"].append(f)

    # Precargados: con ~900 subcategorías, pedir el ranking y el conteo de términos
    # una por una eran ~1,800 viajes a la base por carga de página.
    rk = rankings_por_categoria()
    nterm = conteo_terminos()

    out = []
    for r in raices.values():
        # Ranking y términos de la raíz.
        r["top"] = rk.get((r["raiz_id"], "raiz"), [])[:10] if r["raiz_id"] else []
        r["terminos_raiz"] = nterm.get(r["raiz_id"], 0) if r["raiz_id"] else 0

        for cid, sub in r["subcategorias"].items():
            top = rk.get((cid, "hoja"), [])[:20] if sub["categoria_id"] else []
            precios = sorted(x["precio"] for x in top if x.get("precio"))
            sub["top"] = top
            sub["n_ranking"] = len(top)
            sub["mediana"] = (precios[len(precios) // 2] if len(precios) % 2
                              else (precios[len(precios) // 2 - 1] +
                                    precios[len(precios) // 2]) / 2) if precios else None
            sub["precio_min"] = precios[0] if precios else None
            sub["precio_max"] = precios[-1] if precios else None
            sub["n_terminos"] = nterm.get(cid, 0) if sub["categoria_id"] else 0
            # ML no publica ranking NI términos de toda categoría (Bujías,
            # Cartuchos de Turbo). Hay que decirlo, no pintar celdas vacías.
            sub["sin_datos_ml"] = not top and not sub["n_terminos"]
            # Volumen del NICHO: lo que se vende al mes en toda la subcategoría,
            # sumando el top. `vendidos` es cota inferior (ML redondea a "+50mil"),
            # así que sirve para ordenar nichos, no como cifra exacta.
            sub["volumen_mercado"] = sum(x["vendidos"] or 0 for x in top) or None
            # POSICIÓN DE LA SUBCATEGORÍA EN EL TOP DEL PADRE. Es el criterio más
            # fuerte de oportunidad: si el #1 de toda la categoría vive en nuestra
            # subcategoría, ahí está la pelea que importa. Se une por la categoría
            # que trae cada fila del ranking raíz, no por id de publicación: el
            # padre y la hoja rankean publicaciones distintas del mismo nicho.
            sub["pos_en_raiz"] = min(
                (x["posicion"] for x in r["top"] if x.get("item_categoria_id") == cid),
                default=None)

        for f in r["skus"]:
            sub = r["subcategorias"].get(f.get("categoria_id") or "sin_categoria", {})
            med = sub.get("mediana")
            f["pos_en_raiz"] = sub.get("pos_en_raiz")
            f["volumen_mercado"] = sub.get("volumen_mercado")
            # ¿ESTAMOS en el top de nuestra subcategoría? Es el logro que la vista
            # debe celebrar con una corona: significa que una de nuestras
            # publicaciones aparece entre los más vendidos del nicho. Se busca por
            # `sku_nuestro`, que `_marcar` llena cruzando el ranking contra nuestras
            # publicaciones — no por título, que difiere por tienda.
            mias = [x["posicion"] for x in (sub.get("top") or [])
                    if x.get("sku_nuestro") == f["sku"]]
            f["posicion_top"] = min(mias) if mias else None
            f["en_top"] = bool(mias)
            # El precio de referencia: el más bajo que realmente cobramos.
            precios = [t["precio"] for t in f["tiendas"] if t.get("precio")]
            f["precio_ref"] = min(precios) if precios else None
            f["mediana_mercado"] = med
            f["brecha"] = (round(f["precio_ref"] / med, 2)
                           if med and f["precio_ref"] else None)
            f["sin_datos_ml"] = sub.get("sin_datos_ml", False)
            f["n_terminos"] = sub.get("n_terminos", 0)
            # La trampa que ya apareció dos veces: la publicación PAUSADA es la que
            # tiene el tráfico y las ventas. Se marca aquí para que la vista lo grite.
            act = [t for t in f["tiendas"] if t.get("estado") == "active"]
            pau = [t for t in f["tiendas"] if t.get("estado") == "paused"]
            f["pausada_es_la_que_vende"] = bool(
                pau and max((t["visitas_30d"] or 0) for t in pau) >
                max([(t["visitas_30d"] or 0) for t in act] or [0]))

        # ── Nuestros 5 con más oportunidad ──────────────────────────────────
        # Manda la posición de su subcategoría en el top del padre (#1, luego #2…):
        # competir donde vive el más vendido de toda la categoría es la mejor
        # apuesta. Los que no aparecen ahí se ordenan por el volumen del nicho, que
        # es el otro camino a "puede competir por volumen de venta", y al final por
        # lo que ya vendemos.
        r["oportunidad"] = sorted(
            r["skus"],
            key=lambda f: (f.get("pos_en_raiz") or 99,
                           -(f.get("volumen_mercado") or 0),
                           -(f.get("unidades_30d") or 0)))[:5]

        # Cada subcategoría carga SUS SKUs y su path completo: es lo que se abre al
        # desplegar la fila (primero el top del nicho, luego los nuestros).
        for cid, sub in r["subcategorias"].items():
            mios = [f for f in r["skus"] if (f.get("categoria_id") or "sin_categoria") == cid]
            sub["skus"] = mios
            sub["ruta"] = next((f.get("ruta") for f in mios if f.get("ruta")), None)

        r["subcategorias"] = sorted(
            r["subcategorias"].values(),
            key=lambda s: (s.get("pos_en_raiz") or 99,
                           -(s.get("volumen_mercado") or 0),
                           s["categoria_nombre"]))
        r["skus"].sort(key=lambda f: (f.get("categoria_nombre") or "", f["sku"]))
        r["n_skus"] = len(r["skus"])
        r["n_publicaciones"] = sum(len(f["tiendas"]) for f in r["skus"])
        out.append(r)
    out.sort(key=lambda r: (-r["n_skus"], r["raiz_nombre"]))
    return out


def _ahora() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
