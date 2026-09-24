"""
checklist.py — INVENTARIO · Checklist: la validación de ALMACÉN, SKU por SKU.

Brandon, 24-sep-2026: *«crear una tab en inventario llamado CHECKLIST,
únicamente para almacén, ya sea mediante CSV, Excel o MCP; seleccionar que de
las opcionales se puedan poner en obligatorios y crear matriz. Debemos pedir
cajas con piezas. Esta tab es la que va a determinar si cada SKU tiene sus
atributos correctos que Mercado Libre exige, además de pedir dimensiones, cajas
y piezas que almacén tiene. Cada semana se seleccionan ~100 SKUs; al
seleccionarlos se podrá descargar un Excel con las características necesarias
y, una vez llenado, cargarlo con los campos llenos».*

QUÉ CONTESTA
------------
Por cada SKU del LOTE de la semana: ¿está completo para Mercado Libre y para
almacén? «Completo» es la suma de dos listas:

  1. ATRIBUTOS DE ML que se EXIGEN: los obligatorios del propio ML
     (`required` / `catalog_required` de su API pública) MÁS los opcionales que
     el equipo subió a obligatorios en la MATRIZ (`ops.checklist_matriz`).
  2. LO QUE ALMACÉN MIDE Y CUENTA: largo, ancho, alto y peso del producto
     EMPACADO, cajas y piezas por caja (`ops.checklist_almacen`).

DE DÓNDE SALE CADA COSA — solo kubera y la API pública de ML (regla de
Brandon del 17-sep: nada de WordPress)
  · Categoría ML del SKU ... `specs._categorias` (channel.product_category con
                             la precedencia panel > real > predictor, y si no
                             hay, la de la publicación viva).
  · Qué pide la categoría .. `specs_editor._campos_ml` (API pública, 1 h de caché).
  · Lo ya capturado ....... `enrich.channel_content` (cuenta ''), el MISMO sitio
                             que el Publicador y el editor de specs del cajón.
  · Nombre de la categoría  `channel.categories`.
  · Título del SKU ........ `core.products.name`.

DÓNDE SE GUARDA LO QUE SUBE ALMACÉN
  · Atributos → `specs_editor.guardar_sync`, que fusiona por campo y escribe
    la lista COMPLETA (los tres cuidados de su cabecera).
  · Medidas, cajas, piezas → `ops.checklist_almacen` (migración 0058). Es la
    caja «Bodega» del cotejo del Catálogo Maestro, que hasta hoy era NULL
    porque nadie la medía.

DOS DECISIONES QUE CONVIENE SABER
  · UNA CELDA VACÍA NO BORRA NADA. El Excel sale pre-llenado con lo que ya hay;
    si almacén deja algo en blanco se entiende «no lo sé», no «quítalo». Para
    borrar un atributo está el editor del cajón en el Catálogo Maestro.
  · Las medidas NO se escriben como atributos SELLER_PACKAGE_* de ML. Esos
    campos deciden la TARIFA DE ENVÍO que cobra ML: mandarlos es cambiar
    dinero en publicaciones vivas, y eso espera su dale (regla 3).

SI LA MIGRACIÓN 0058 NO ESTÁ APLICADA, la pestaña lo dice en vez de tronar:
todo lo que toca las tablas nuevas levanta `FaltaMigracion` y el router lo
devuelve como `falta_migracion: true`.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from core import actor
from services import specs, specs_editor
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.services.checklist")

CANAL = "mercado_libre"
_ZONA = ZoneInfo("America/Mexico_City")

# Lo que almacén mide y cuenta. (clave, etiqueta, tipo). La clave va en
# minúsculas a propósito: los ids de ML son MAYÚSCULAS, así que en el Excel y
# en el CSV las dos familias nunca chocan.
LOGISTICA: tuple[tuple[str, str, str], ...] = (
    ("largo_cm", "Largo (cm)", "decimal"),
    ("ancho_cm", "Ancho (cm)", "decimal"),
    ("alto_cm", "Alto (cm)", "decimal"),
    ("peso_kg", "Peso (kg)", "decimal"),
    ("cajas", "Cajas", "entero"),
    ("piezas_por_caja", "Piezas por caja", "entero"),
)
_LOG_CLAVES = tuple(k for k, _, _ in LOGISTICA)
_LOG_ETIQUETA = {k: e for k, e, _ in LOGISTICA}
_LOG_TIPO = {k: t for k, _, t in LOGISTICA}

# El orden en que se enseñan y se exigen los atributos.
NIVELES = ("ml", "matriz", "principal", "secundario")
_EXIGIDOS = frozenset({"ml", "matriz"})

_MAX_SKUS_LOTE = 500      # por llamada; la semana típica son ~100
_HILOS_ML = 8             # categorías pedidas a la API de ML en paralelo
_HILOS_GUARDAR = 4        # SKUs guardados en paralelo (el pool de kubera es chico)


class FaltaMigracion(RuntimeError):
    """Las tablas `ops.checklist_*` no existen todavía (migración 0058)."""


def _sin_tabla(exc: Exception) -> bool:
    return (getattr(exc, "pgcode", None) == "42P01"
            or ("does not exist" in str(exc) and "checklist_" in str(exc)))


def _q(sql: str, params: Any = None) -> list[dict[str, Any]]:
    try:
        return sdb.fetch_all(sql, params)
    except Exception as exc:  # noqa: BLE001
        if _sin_tabla(exc):
            raise FaltaMigracion(
                "Falta aplicar la migración 0058 (ops.checklist_*) en kubera.") from exc
        raise


# ══════════════════════════════════════════════════════════════════════════════
# La semana
# ══════════════════════════════════════════════════════════════════════════════

def hoy() -> dt.date:
    return dt.datetime.now(_ZONA).date()


def lunes(fecha: dt.date | None = None) -> dt.date:
    f = fecha or hoy()
    return f - dt.timedelta(days=f.weekday())


def semana_de(texto: str | None) -> dt.date:
    """'2026-09-24' → el lunes de esa semana. Sin nada, la semana de hoy."""
    if not texto:
        return lunes()
    try:
        return lunes(dt.date.fromisoformat(texto.strip()[:10]))
    except ValueError:
        return lunes()


def limpiar_skus(crudo: Iterable[str] | str | None) -> list[str]:
    """Acepta lista o texto pegado de Excel (renglones, comas, tabs, espacios).
    Quita duplicados conservando el orden."""
    if crudo is None:
        return []
    if isinstance(crudo, str):
        partes = re.split(r"[\s,;]+", crudo)
    else:
        partes = [p for x in crudo for p in re.split(r"[\s,;]+", str(x or ""))]
    salida: list[str] = []
    vistos: set[str] = set()
    for p in partes:
        p = p.strip().strip('"').strip("'")
        if p and p.upper() not in vistos:
            vistos.add(p.upper())
            salida.append(p)
    return salida


# ══════════════════════════════════════════════════════════════════════════════
# Lecturas de kubera (sin las tablas nuevas)
# ══════════════════════════════════════════════════════════════════════════════

def _canonicos(skus: list[str]) -> tuple[dict[str, str], list[str]]:
    """Empata lo que se pegó con el SKU real (sin importar mayúsculas).

    Vale cualquier SKU que kubera conozca: `core.products` o una categoría ML
    en `channel.product_category`. Devuelve ({pegado_mayus: real}, desconocidos).
    """
    if not skus:
        return {}, []
    mayus = [s.upper() for s in skus]
    reales: dict[str, str] = {}
    for sql in ("select sku from core.products where upper(sku) = any(%s)",
                "select sku from channel.product_category where upper(sku) = any(%s)"):
        try:
            for r in sdb.fetch_all(sql, (mayus,)):
                reales.setdefault(r["sku"].upper(), r["sku"])
        except Exception as exc:  # noqa: BLE001
            log.warning("checklist: no se pudo verificar SKUs: %s", exc)
    desconocidos = [s for s in skus if s.upper() not in reales]
    return reales, desconocidos


def _titulos(skus: list[str]) -> dict[str, str]:
    if not skus:
        return {}
    try:
        return {r["sku"]: r["name"] for r in sdb.fetch_all(
            "select sku, name from core.products where sku = any(%s)", (skus,))}
    except Exception as exc:  # noqa: BLE001
        log.warning("checklist: títulos no disponibles: %s", exc)
        return {}


def _urls_ml(skus: list[str]) -> dict[str, str]:
    """Una publicación viva de ML por SKU, para que almacén vea el producto."""
    if not skus:
        return {}
    salida: dict[str, str] = {}
    try:
        for r in sdb.fetch_all(
                "select sku, url from channel.listings "
                "where canal = %s and sku = any(%s) and url is not null "
                "order by (status = 'active') desc, updated_at desc",
                (CANAL, skus)):
            salida.setdefault(r["sku"], r["url"])
    except Exception as exc:  # noqa: BLE001
        log.warning("checklist: publicaciones no disponibles: %s", exc)
    return salida


def _nombres_categoria(cats: Iterable[str]) -> dict[str, dict[str, str]]:
    cats = sorted({c for c in cats if c})
    if not cats:
        return {}
    try:
        return {r["category_id"]: {"nombre": r["name"], "ruta": r["path"]}
                for r in sdb.fetch_all(
                    "select category_id, name, path from channel.categories "
                    "where channel_id = %s and category_id = any(%s)", (CANAL, cats))}
    except Exception as exc:  # noqa: BLE001
        log.warning("checklist: nombres de categoría no disponibles: %s", exc)
        return {}


def _valores_ml(skus: list[str]) -> dict[str, dict[str, str]]:
    """Lo ya capturado de ML por SKU: {sku: {ATRIBUTO: valor}}. Una consulta."""
    if not skus:
        return {}
    salida: dict[str, dict[str, str]] = {}
    for r in sdb.fetch_all(
            "select sku, contenido->'atributos' as atributos "
            "from enrich.channel_content "
            "where canal = %s and cuenta = '' and sku = any(%s)", (CANAL, skus)):
        attrs = r.get("atributos")
        if not isinstance(attrs, list):
            continue
        salida[r["sku"]] = {
            specs_editor._clave(a): specs_editor._valor(a)
            for a in attrs if isinstance(a, dict) and specs_editor._clave(a)}
    return salida


def _campos_por_categoria(cats: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """Los atributos de cada categoría, pedidos a ML en paralelo (con caché)."""
    cats = sorted({c for c in cats if c})
    if not cats:
        return {}
    with ThreadPoolExecutor(max_workers=min(_HILOS_ML, len(cats))) as ex:
        return dict(zip(cats, ex.map(specs_editor._campos_ml, cats)))


# ══════════════════════════════════════════════════════════════════════════════
# Las tablas nuevas (0058)
# ══════════════════════════════════════════════════════════════════════════════

def _almacen(skus: list[str]) -> dict[str, dict[str, Any]]:
    if not skus:
        return {}
    salida: dict[str, dict[str, Any]] = {}
    for r in _q("select * from ops.checklist_almacen where sku = any(%s)", (skus,)):
        d = dict(r)
        for k in _LOG_CLAVES:
            v = d.get(k)
            if v is not None:
                d[k] = int(v) if _LOG_TIPO[k] == "entero" else float(v)
        if d.get("capturado_en"):
            d["capturado_en"] = d["capturado_en"].isoformat()
        salida[r["sku"]] = d
    return salida


def almacen_de(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Para el Catálogo Maestro: lo capturado, o {} si la 0058 no existe aún.
    NUNCA truena — el cotejo de cajas no puede caerse por una tabla nueva."""
    try:
        return _almacen(skus)
    except Exception as exc:  # noqa: BLE001
        if not isinstance(exc, FaltaMigracion):
            log.warning("checklist: almacén no disponible: %s", exc)
        return {}


def _matriz(cats: Iterable[str]) -> dict[str, dict[str, bool]]:
    cats = sorted({c for c in cats if c})
    if not cats:
        return {}
    salida: dict[str, dict[str, bool]] = {}
    for r in _q("select categoria_id, campo, obligatorio from ops.checklist_matriz "
                "where canal = %s and categoria_id = any(%s)", (CANAL, cats)):
        salida.setdefault(r["categoria_id"], {})[r["campo"]] = bool(r["obligatorio"])
    return salida


def _guardar_almacen(filas: dict[str, dict[str, Any]], fuente: str, por: str) -> int:
    """UPSERT por SKU. Solo pisa las columnas que traen valor (coalesce): una
    captura parcial no borra lo que ya se había medido."""
    if not filas:
        return 0
    cols = ", ".join(_LOG_CLAVES)
    marcas = ", ".join(["%s"] * len(_LOG_CLAVES))
    sets = ", ".join(f"{k} = coalesce(excluded.{k}, ops.checklist_almacen.{k})"
                     for k in _LOG_CLAVES)
    sql = (f"insert into ops.checklist_almacen (sku, {cols}, fuente, capturado_por, "
           f"capturado_en) values (%s, {marcas}, %s, %s, now()) "
           f"on conflict (sku) do update set {sets}, fuente = excluded.fuente, "
           f"capturado_por = excluded.capturado_por, capturado_en = now()")

    def _hacer() -> int:
        with sdb.get_cursor() as cur:
            for sku, vals in filas.items():
                cur.execute(sql, (sku, *[vals.get(k) for k in _LOG_CLAVES],
                                  fuente, por or None))
        return len(filas)
    try:
        return sdb.reintentar_transitorio(_hacer)
    except Exception as exc:  # noqa: BLE001
        if _sin_tabla(exc):
            raise FaltaMigracion(
                "Falta aplicar la migración 0058 (ops.checklist_*) en kubera.") from exc
        raise


# ══════════════════════════════════════════════════════════════════════════════
# Los campos de una categoría, ya con su NIVEL
# ══════════════════════════════════════════════════════════════════════════════

def _nivel(c: dict[str, Any], promovidos: dict[str, bool]) -> str:
    """ml > matriz > principal > secundario.

    SECUNDARIO = jerarquía `ITEM` de ML: clave SAT, unidad de medida SAT, IVA,
    IEPS, número de pedimento, nombre en factura. Son datos FISCALES de la
    venta, no del producto: almacén no los conoce y se pliegan. Todo lo demás
    (llaves del catálogo, GTIN, características de familia) es PRINCIPAL.
    Medido el 24-sep: `relevance` vale 1 en TODOS los visibles, no separa nada.
    """
    if c.get("obligatorio"):
        return "ml"
    if promovidos.get(c["campo"]):
        return "matriz"
    if c.get("jerarquia") == "ITEM":
        return "secundario"
    return "principal"


def campos_de(cat: str, crudos: list[dict[str, Any]],
              promovidos: dict[str, bool]) -> list[dict[str, Any]]:
    """COPIAS de los campos de la caché de ML, con `nivel` y `exigido`.
    Copias: la lista de `_campos_ml` es compartida entre peticiones."""
    salida = []
    for c in crudos:
        d = {k: c.get(k) for k in ("campo", "etiqueta", "tipo", "jerarquia",
                                   "unidad_default")}
        d["valores"] = list(c.get("valores") or [])
        d["unidades"] = list(c.get("unidades") or [])
        d["nivel"] = _nivel(c, promovidos)
        d["exigido"] = d["nivel"] in _EXIGIDOS
        salida.append(d)
    salida.sort(key=lambda x: NIVELES.index(x["nivel"]))
    return salida


# ══════════════════════════════════════════════════════════════════════════════
# El tablero
# ══════════════════════════════════════════════════════════════════════════════

def _vacio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _contexto(skus: list[str]) -> dict[str, Any]:
    """Todo lo que hace falta para evaluar y exportar un grupo de SKUs, en
    pocas consultas: categorías, campos, matriz, valores y almacén."""
    info = specs._categorias(skus) if skus else {}
    cats_por_sku = {s: (info.get(s) or {}).get(CANAL) or {} for s in skus}
    cats = {v.get("categoria") for v in cats_por_sku.values() if v.get("categoria")}
    crudos = _campos_por_categoria(cats)
    matriz = _matriz(cats)
    campos = {c: campos_de(c, crudos.get(c) or [], matriz.get(c) or {}) for c in cats}
    return {
        "cats_por_sku": cats_por_sku,
        "campos": campos,
        "matriz": matriz,
        "nombres": _nombres_categoria(cats),
        "valores": _valores_ml(skus),
        "almacen": _almacen(skus),
        "titulos": _titulos(skus),
    }


def _evaluar(sku: str, ctx: dict[str, Any]) -> dict[str, Any]:
    cat_info = ctx["cats_por_sku"].get(sku) or {}
    cat = cat_info.get("categoria")
    campos = (ctx["campos"].get(cat) or []) if cat else []
    valores = ctx["valores"].get(sku) or {}
    alm = ctx["almacen"].get(sku) or {}
    nombre = (ctx["nombres"].get(cat) or {}) if cat else {}

    exigidos = [c for c in campos if c["exigido"]]
    faltan_ml = [{"campo": c["campo"], "etiqueta": c["etiqueta"], "nivel": c["nivel"]}
                 for c in exigidos if _vacio(valores.get(c["campo"]))]
    opcionales = [c for c in campos if not c["exigido"]]
    faltan_alm = [k for k in _LOG_CLAVES if alm.get(k) is None]

    if not cat:
        estado = "sin_categoria"
    elif not campos:
        estado = "sin_lista"
    elif not faltan_ml and not faltan_alm:
        estado = "completo"
    else:
        estado = "incompleto"

    cajas, ppc = alm.get("cajas"), alm.get("piezas_por_caja")
    return {
        "sku": sku,
        "titulo": ctx["titulos"].get(sku),
        "categoria": cat,
        "categoria_fuente": cat_info.get("fuente"),
        "categoria_nombre": nombre.get("nombre"),
        "categoria_ruta": nombre.get("ruta"),
        "exigidos_total": len(exigidos),
        "exigidos_llenos": len(exigidos) - len(faltan_ml),
        "faltan_ml": faltan_ml,
        "opcionales_total": len(opcionales),
        "opcionales_llenos": sum(1 for c in opcionales
                                 if not _vacio(valores.get(c["campo"]))),
        "almacen": {k: alm.get(k) for k in (*_LOG_CLAVES, "fuente",
                                             "capturado_por", "capturado_en")},
        "faltan_almacen": faltan_alm,
        "piezas_total": cajas * ppc if cajas is not None and ppc is not None else None,
        "estado": estado,
    }


def tablero_sync(semana_txt: str | None) -> dict[str, Any]:
    semana = semana_de(semana_txt)
    base = {"semana": semana.isoformat(),
            "semana_fin": (semana + dt.timedelta(days=6)).isoformat(),
            "campos_almacen": [{"campo": k, "etiqueta": e} for k, e, _ in LOGISTICA]}
    try:
        semanas = [{"semana": r["semana"].isoformat(), "skus": r["n"]} for r in _q(
            "select semana, count(*) n from ops.checklist_lote "
            "group by semana order by semana desc limit 26")]
        lote = _q("select sku, agregado_por, agregado_en from ops.checklist_lote "
                  "where semana = %s order by agregado_en, sku", (semana,))
        skus = [r["sku"] for r in lote]
        ctx = _contexto(skus)
    except FaltaMigracion as exc:
        return {**base, "ok": False, "falta_migracion": True, "motivo": str(exc),
                "semanas": [], "filas": [], "categorias": [],
                "resumen": _resumen([])}

    urls = _urls_ml(skus)
    filas = []
    for r in lote:
        f = _evaluar(r["sku"], ctx)
        f["url_ml"] = urls.get(r["sku"])
        f["agregado_por"] = r["agregado_por"]
        f["agregado_en"] = r["agregado_en"].isoformat() if r["agregado_en"] else None
        filas.append(f)

    por_cat: dict[str, int] = {}
    for f in filas:
        if f["categoria"]:
            por_cat[f["categoria"]] = por_cat.get(f["categoria"], 0) + 1
    categorias = sorted((
        {"categoria": c, "nombre": (ctx["nombres"].get(c) or {}).get("nombre"),
         "skus": n,
         "obligatorios_ml": sum(1 for x in ctx["campos"].get(c) or [] if x["nivel"] == "ml"),
         "promovidos": sum(1 for x in ctx["campos"].get(c) or [] if x["nivel"] == "matriz")}
        for c, n in por_cat.items()), key=lambda x: (-x["skus"], x["nombre"] or ""))

    return {**base, "ok": True, "falta_migracion": False, "motivo": None,
            "semanas": semanas, "filas": filas, "categorias": categorias,
            "resumen": _resumen(filas)}


def _resumen(filas: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(filas),
        "completos": sum(1 for f in filas if f["estado"] == "completo"),
        "incompletos": sum(1 for f in filas if f["estado"] == "incompleto"),
        "sin_categoria": sum(1 for f in filas if f["estado"] in ("sin_categoria", "sin_lista")),
        "faltan_ml": sum(1 for f in filas if f["faltan_ml"]),
        "faltan_almacen": sum(1 for f in filas if f["faltan_almacen"]),
    }


# ══════════════════════════════════════════════════════════════════════════════
# El lote de la semana
# ══════════════════════════════════════════════════════════════════════════════

def agregar_sync(semana_txt: str | None, crudo: Iterable[str] | str) -> dict[str, Any]:
    semana = semana_de(semana_txt)
    pegados = limpiar_skus(crudo)
    if len(pegados) > _MAX_SKUS_LOTE:
        return {"ok": False, "motivo": f"Son {len(pegados)} SKUs; el máximo por "
                                       f"carga es {_MAX_SKUS_LOTE}."}
    reales, desconocidos = _canonicos(pegados)
    skus = [reales[s.upper()] for s in pegados if s.upper() in reales]
    por = actor.actual() or None
    nuevos = 0
    if skus:
        sql = ("insert into ops.checklist_lote (semana, sku, agregado_por) "
               "values (%s, %s, %s) on conflict (semana, sku) do nothing")

        def _hacer() -> int:
            n = 0
            with sdb.get_cursor() as cur:
                for s in skus:
                    cur.execute(sql, (semana, s, por))
                    n += cur.rowcount
            return n
        try:
            nuevos = sdb.reintentar_transitorio(_hacer)
        except Exception as exc:  # noqa: BLE001
            if _sin_tabla(exc):
                return {"ok": False, "falta_migracion": True,
                        "motivo": "Falta aplicar la migración 0058 en kubera."}
            raise
    return {"ok": True, "semana": semana.isoformat(), "agregados": nuevos,
            "ya_estaban": len(skus) - nuevos, "desconocidos": desconocidos}


def quitar_sync(semana_txt: str | None, crudo: Iterable[str] | str) -> dict[str, Any]:
    semana = semana_de(semana_txt)
    skus = limpiar_skus(crudo)
    if not skus:
        return {"ok": True, "quitados": 0}
    try:
        n = sdb.execute("delete from ops.checklist_lote where semana = %s and sku = any(%s)",
                        (semana, skus))
    except Exception as exc:  # noqa: BLE001
        if _sin_tabla(exc):
            return {"ok": False, "falta_migracion": True,
                    "motivo": "Falta aplicar la migración 0058 en kubera."}
        raise
    return {"ok": True, "quitados": n}


# ══════════════════════════════════════════════════════════════════════════════
# La matriz
# ══════════════════════════════════════════════════════════════════════════════

def matriz_sync(cat: str) -> dict[str, Any]:
    crudos = specs_editor._campos_ml(cat)
    nombre = _nombres_categoria([cat]).get(cat) or {}
    base = {"categoria": cat, "nombre": nombre.get("nombre"), "ruta": nombre.get("ruta")}
    try:
        promovidos = _matriz([cat]).get(cat) or {}
    except FaltaMigracion as exc:
        return {**base, "ok": False, "falta_migracion": True, "motivo": str(exc),
                "campos": []}
    campos = campos_de(cat, crudos, promovidos)
    return {**base, "ok": True, "falta_migracion": False,
            "motivo": None if campos else
            "Mercado Libre no contestó qué pide esta categoría; intenta en un momento.",
            "campos": campos}


def guardar_matriz_sync(cat: str, cambios: dict[str, bool]) -> dict[str, Any]:
    """Sube (o baja) opcionales a obligatorios. Los obligatorios de ML no se
    tocan: bajarlos no serviría de nada, ML los rechazaría igual."""
    crudos = {c["campo"]: c for c in specs_editor._campos_ml(cat)}
    if not crudos:
        return {"ok": False, "motivo": "Mercado Libre no contestó qué pide esta "
                                       "categoría; no se guarda a ciegas."}
    validos = {k: bool(v) for k, v in (cambios or {}).items()
               if k in crudos and not crudos[k].get("obligatorio")}
    if not validos:
        return {"ok": True, "guardados": 0}
    por = actor.actual() or None
    sql = ("insert into ops.checklist_matriz (canal, categoria_id, campo, obligatorio, "
           "actualizado_por) values (%s, %s, %s, %s, %s) "
           "on conflict (canal, categoria_id, campo) do update set "
           "obligatorio = excluded.obligatorio, "
           "actualizado_por = excluded.actualizado_por, actualizado_en = now()")

    def _hacer() -> int:
        with sdb.get_cursor() as cur:
            for campo, ob in validos.items():
                cur.execute(sql, (CANAL, cat, campo, ob, por))
        return len(validos)
    try:
        n = sdb.reintentar_transitorio(_hacer)
    except Exception as exc:  # noqa: BLE001
        if _sin_tabla(exc):
            return {"ok": False, "falta_migracion": True,
                    "motivo": "Falta aplicar la migración 0058 en kubera."}
        raise
    return {"ok": True, "guardados": n}


# ══════════════════════════════════════════════════════════════════════════════
# Normalizar lo que escribe almacén
# ══════════════════════════════════════════════════════════════════════════════

_NUM = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*([^\d\s].*?)?\s*$")
_SI = {"si", "sí", "s", "yes", "y", "true", "verdadero", "1", "x"}
_NO = {"no", "n", "false", "falso", "0"}


def _texto(v: Any) -> str:
    """Lo que trae una celda, como texto. 12.0 → '12'."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Sí" if v else "No"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:g}"
    return str(v).strip()


def _normalizar_logistica(campo: str, crudo: str) -> tuple[Any, str | None]:
    """(valor, error). Acepta '12,5', '12.5 cm', '3 kg'."""
    m = _NUM.match(crudo)  # el \s de Python ya cubre el espacio duro de Excel
    if not m:
        return None, f"«{crudo}» no es un número"
    n = float(m.group(1).replace(",", "."))
    if _LOG_TIPO[campo] == "entero":
        if not n.is_integer():
            return None, f"«{crudo}» debe ser un número entero"
        n = int(n)
        if campo == "piezas_por_caja" and n <= 0:
            return None, "las piezas por caja deben ser más de 0"
        if n < 0:
            return None, "no puede ser negativo"
        return n, None
    if n <= 0:
        return None, "debe ser mayor que 0"
    return round(n, 3), None


def _normalizar_ml(c: dict[str, Any], crudo: str) -> tuple[str | None, str | None, str | None]:
    """(valor, error, aviso) según el tipo que declara ML."""
    tipo = c.get("tipo") or "string"
    if tipo == "boolean":
        t = crudo.strip().lower()
        if t in _SI:
            return "Sí", None, None
        if t in _NO:
            return "No", None, None
        return None, f"«{crudo}» debe ser Sí o No", None
    if tipo == "number":
        m = _NUM.match(crudo)
        if not m or m.group(2):
            return None, f"«{crudo}» debe ser un número", None
        return m.group(1).replace(",", "."), None, None
    if tipo == "number_unit":
        m = _NUM.match(crudo)
        if not m:
            return None, f"«{crudo}» debe ser número y unidad (p. ej. 12 cm)", None
        numero = m.group(1).replace(",", ".")
        unidad = (m.group(2) or "").strip()
        permitidas = c.get("unidades") or []
        if not unidad:
            # Sin unidad se usa la que ML mismo asume (`default_unit`), o la
            # única permitida. NUNCA «la primera de la lista»: en Peso máximo
            # soportado es `g`, y «20» de un corral son 20 kg, no 20 g.
            por_omision = c.get("unidad_default") or (
                permitidas[0] if len(permitidas) == 1 else None)
            if not permitidas:
                return numero, None, None
            if not por_omision:
                return None, (f"falta la unidad ({', '.join(permitidas)})"), None
            return (f"{numero} {por_omision}", None,
                    f"sin unidad: se usó {por_omision}, la que Mercado Libre asume")
        if permitidas:
            igual = next((u for u in permitidas if u.lower() == unidad.lower()), None)
            if not igual:
                return None, (f"unidad «{unidad}» no válida; ML acepta "
                              f"{', '.join(permitidas)}"), None
            unidad = igual
        return f"{numero} {unidad}", None, None
    valores = c.get("valores") or []
    if valores:
        igual = next((v for v in valores if v.lower() == crudo.lower()), None)
        if igual:
            return igual, None, None
        if tipo == "list":
            return crudo, None, "no está en la lista de Mercado Libre; puede rechazarlo"
    return crudo, None, None


# ══════════════════════════════════════════════════════════════════════════════
# La descarga: Excel y CSV
# ══════════════════════════════════════════════════════════════════════════════

# Los colores del canal: el amarillo de Mercado Libre para lo que ML exige.
_COLOR = {
    "ml": "FFE600", "matriz": "FDBA74", "principal": "E2E8F0",
    "secundario": "F1F5F9", "almacen": "BFDBFE", "id": "E5E7EB",
}
_HUECO = {"ml": "FFF9C4", "matriz": "FFEDD5", "almacen": "DBEAFE"}
_NIVEL_TXT = {"ml": "Obligatorio ML", "matriz": "Obligatorio (matriz)",
              "principal": "Opcional", "secundario": "Opcional · facturación"}


def _skus_export(semana_txt: str | None, sel: str | None) -> tuple[dt.date, list[str]]:
    semana = semana_de(semana_txt)
    elegidos = limpiar_skus(sel)
    if elegidos:
        return semana, elegidos
    return semana, [r["sku"] for r in _q(
        "select sku from ops.checklist_lote where semana = %s order by agregado_en, sku",
        (semana,))]


def _grupos(skus: list[str], ctx: dict[str, Any]) -> list[tuple[str | None, list[str]]]:
    """SKUs agrupados por categoría ML (una hoja por categoría)."""
    por: dict[str | None, list[str]] = {}
    for s in skus:
        por.setdefault((ctx["cats_por_sku"].get(s) or {}).get("categoria"), []).append(s)

    def llave(item):
        c = item[0]
        return (c is None, ((ctx["nombres"].get(c) or {}).get("nombre") or c or ""))
    return sorted(por.items(), key=llave)


def _nombre_hoja(texto: str, usados: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", texto or "Hoja").strip()[:28] or "Hoja"
    nombre, i = base, 2
    while nombre.lower() in usados:
        nombre = f"{base[:25]} ({i})"
        i += 1
    usados.add(nombre.lower())
    return nombre


def _pista(c: dict[str, Any]) -> str:
    partes = [_NIVEL_TXT[c["nivel"]]]
    if c.get("tipo") == "boolean":
        partes.append("Sí / No")
    elif c.get("tipo") == "number_unit" and c.get("unidades"):
        partes.append("número + " + "/".join(c["unidades"][:4])
                      + (f" (sin unidad = {c['unidad_default']})"
                         if c.get("unidad_default") else ""))
    elif c.get("tipo") == "number":
        partes.append("número")
    elif c.get("valores"):
        partes.append("elige de la lista")
    return " · ".join(partes)


def excel_sync(semana_txt: str | None, sel: str | None) -> tuple[bytes, str]:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    semana, skus = _skus_export(semana_txt, sel)
    ctx = _contexto(skus)

    wb = Workbook()
    ins = wb.active
    ins.title = "Instrucciones"
    listas = wb.create_sheet("_listas")
    listas.sheet_state = "hidden"
    col_lista = [0]

    fill = lambda c: PatternFill("solid", start_color=c, end_color=c)  # noqa: E731
    fino = Side(style="thin", color="CBD5E1")
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)
    negrita = Font(bold=True, color="0F172A")

    def lista_ref(valores: list[str]) -> str:
        col_lista[0] += 1
        letra = get_column_letter(col_lista[0])
        for i, v in enumerate(valores[:500], start=1):
            listas.cell(row=i, column=col_lista[0], value=v)
        return f"'_listas'!${letra}$1:${letra}${min(len(valores), 500)}"

    usados = {"instrucciones", "_listas"}
    indice = []
    for cat, grupo in _grupos(skus, ctx):
        campos = (ctx["campos"].get(cat) or []) if cat else []
        nombre_cat = ((ctx["nombres"].get(cat) or {}).get("nombre") or cat) if cat else None
        ws = wb.create_sheet(_nombre_hoja(nombre_cat or "Sin categoría ML", usados))
        indice.append((ws.title, nombre_cat or "— sin categoría ML —", cat or "",
                       len(grupo), sum(1 for c in campos if c["exigido"])))

        columnas: list[dict[str, Any]] = [
            {"clave": "sku", "titulo": "SKU", "pista": "no lo cambies", "nivel": "id", "ancho": 20},
            {"clave": "titulo", "titulo": "Producto", "pista": "solo referencia",
             "nivel": "id", "ancho": 42},
        ]
        columnas += [{"clave": k, "titulo": e, "pista": "Almacén · " + (
            "entero" if t == "entero" else "número"), "nivel": "almacen", "ancho": 13,
            "tipo_log": t} for k, e, t in LOGISTICA]
        columnas += [{"clave": c["campo"], "titulo": c["etiqueta"], "pista": _pista(c),
                      "nivel": c["nivel"], "ancho": max(14, min(34, len(c["etiqueta"] or "") + 4)),
                      "campo": c} for c in campos]

        for j, col in enumerate(columnas, start=1):
            ws.cell(row=1, column=j, value=col["clave"])
            h = ws.cell(row=2, column=j, value=col["titulo"])
            h.font = Font(bold=True, color="0F172A" if col["nivel"] != "ml" else "1E1B4B")
            h.fill = fill(_COLOR[col["nivel"]])
            h.alignment = Alignment(wrap_text=True, vertical="center")
            h.border = borde
            p = ws.cell(row=3, column=j, value=col["pista"])
            p.font = Font(italic=True, size=9, color="475569")
            p.fill = fill(_COLOR[col["nivel"]])
            p.alignment = Alignment(wrap_text=True, vertical="top")
            p.border = borde
            ws.column_dimensions[get_column_letter(j)].width = col["ancho"]
        ws.row_dimensions[1].hidden = True
        ws.row_dimensions[2].height = 34
        ws.row_dimensions[3].height = 30
        ws.freeze_panes = "C4"

        ultima = 3 + len(grupo)
        for i, sku in enumerate(grupo, start=4):
            valores = ctx["valores"].get(sku) or {}
            alm = ctx["almacen"].get(sku) or {}
            for j, col in enumerate(columnas, start=1):
                clave = col["clave"]
                if clave == "sku":
                    v: Any = sku
                elif clave == "titulo":
                    v = ctx["titulos"].get(sku) or ""
                elif col["nivel"] == "almacen":
                    v = alm.get(clave)
                else:
                    v = valores.get(clave) or None
                celda = ws.cell(row=i, column=j, value=v)
                celda.border = borde
                if clave == "sku":
                    celda.font = negrita
                if _vacio(v) and col["nivel"] in _HUECO:
                    celda.fill = fill(_HUECO[col["nivel"]])

        # Validaciones: sugerencias con AVISO (ML acepta texto libre en casi
        # todo) y números estrictos en lo de almacén.
        for j, col in enumerate(columnas, start=1):
            letra = get_column_letter(j)
            rango = f"{letra}4:{letra}{max(ultima, 4)}"
            if col["nivel"] == "almacen":
                if col["tipo_log"] == "entero":
                    dv = DataValidation(type="whole", operator="greaterThanOrEqual",
                                        formula1="0", allow_blank=True,
                                        error="Escribe un número entero", errorTitle="Almacén")
                else:
                    dv = DataValidation(type="decimal", operator="greaterThan",
                                        formula1="0", allow_blank=True,
                                        error="Escribe un número mayor que 0",
                                        errorTitle="Almacén")
            elif "campo" in col and col["campo"].get("tipo") == "boolean":
                dv = DataValidation(type="list", formula1='"Sí,No"', allow_blank=True)
            elif "campo" in col and col["campo"].get("valores"):
                dv = DataValidation(type="list", formula1=lista_ref(col["campo"]["valores"]),
                                    allow_blank=True, errorStyle="warning",
                                    error="No está en la lista de Mercado Libre. "
                                          "¿Seguro?", errorTitle="Mercado Libre")
            else:
                continue
            ws.add_data_validation(dv)
            dv.add(rango)

        # Los de facturación se agrupan PLEGADOS: están para quien los sepa,
        # sin tapar lo que sí se exige.
        sec = [j for j, col in enumerate(columnas, start=1) if col["nivel"] == "secundario"]
        if sec:
            ws.column_dimensions.group(get_column_letter(sec[0]), get_column_letter(sec[-1]),
                                       hidden=True, outline_level=1)
        ws.sheet_properties.outlinePr.summaryRight = False

    _instrucciones(ins, semana, len(skus), indice, fill, negrita)
    if not col_lista[0]:
        listas.cell(row=1, column=1, value="")
    # La hoja de listas al final: oculta, pero que no quede entre las de trabajo.
    wb.move_sheet(listas, offset=len(wb.sheetnames) - 1 - wb.sheetnames.index("_listas"))

    buf = io.BytesIO()
    wb.save(buf)
    nombre = f"checklist_almacen_{semana.isoformat()}_{len(skus)}skus.xlsx"
    return buf.getvalue(), nombre


def _instrucciones(ws, semana: dt.date, n: int, indice: list, fill, negrita) -> None:
    from openpyxl.styles import Alignment, Font

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 48
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 10
    ws.column_dimensions["E"].width = 14
    ws["A1"] = "CHECKLIST DE ALMACÉN — Mercado Libre"
    ws["A1"].font = Font(bold=True, size=16, color="1E1B4B")
    ws["A2"] = (f"Semana del {semana.strftime('%d/%m/%Y')} · {n} SKUs · generado "
                f"{dt.datetime.now(_ZONA).strftime('%d/%m/%Y %H:%M')}"
                + (f" por {actor.actual()}" if actor.actual() else ""))
    ws["A2"].font = Font(color="475569")

    pasos = [
        "1. Cada hoja es una categoría de Mercado Libre. Llena las celdas de color.",
        "2. AZUL = almacén: medidas del producto EMPACADO (cm y kg), cuántas CAJAS "
        "hay y cuántas PIEZAS trae cada caja.",
        "3. AMARILLO = lo que Mercado Libre exige. NARANJA = lo que el equipo "
        "decidió exigir (matriz).",
        "4. Las columnas grises son opcionales. Las de FACTURACIÓN (clave SAT, "
        "IVA, IEPS, pedimento) vienen plegadas: no son de almacén.",
        "5. NO cambies la columna SKU ni borres el renglón oculto 1: así se sabe "
        "qué es cada cosa al cargarlo.",
        "6. Una celda vacía NO borra nada. Lo que ya estaba capturado viene "
        "pre-llenado: corrígelo si está mal.",
        "7. Guarda y cárgalo en Omnicanal → Inventario → Checklist → «Cargar "
        "Excel». Antes de guardar te enseña qué va a cambiar.",
    ]
    for i, t in enumerate(pasos, start=4):
        ws.cell(row=i, column=1, value=t).alignment = Alignment(wrap_text=False)

    fila = 4 + len(pasos) + 1
    ws.cell(row=fila, column=1, value="Colores").font = negrita
    for k, t in (("almacen", "Almacén (medidas, cajas, piezas)"),
                 ("ml", "Obligatorio de Mercado Libre"),
                 ("matriz", "Obligatorio por la matriz del equipo"),
                 ("principal", "Opcional del producto"),
                 ("secundario", "Opcional de facturación (plegado)")):
        fila += 1
        ws.cell(row=fila, column=1, value="").fill = fill(_COLOR[k])
        ws.cell(row=fila, column=2, value=t)

    fila += 2
    for j, t in enumerate(("Hoja", "Categoría de Mercado Libre", "ID", "SKUs",
                           "Exigidos"), start=1):
        ws.cell(row=fila, column=j, value=t).font = negrita
    for hoja, nombre, cid, cuantos, exig in indice:
        fila += 1
        for j, v in enumerate((hoja, nombre, cid, cuantos, exig), start=1):
            ws.cell(row=fila, column=j, value=v)


def csv_sync(semana_txt: str | None, sel: str | None) -> tuple[bytes, str]:
    """Formato LARGO: un renglón por (SKU, campo). Es el que sirve para
    automatizar (un script o un agente lo llena sin conocer las hojas)."""
    semana, skus = _skus_export(semana_txt, sel)
    ctx = _contexto(skus)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sku", "producto", "categoria_id", "categoria", "campo", "etiqueta",
                "nivel", "tipo", "unidades", "valor"])
    for sku in skus:
        cat = (ctx["cats_por_sku"].get(sku) or {}).get("categoria")
        nombre = ((ctx["nombres"].get(cat) or {}).get("nombre") or "") if cat else ""
        titulo = ctx["titulos"].get(sku) or ""
        alm = ctx["almacen"].get(sku) or {}
        for k, e, t in LOGISTICA:
            v = alm.get(k)
            w.writerow([sku, titulo, cat or "", nombre, k, e, "almacen", t, "",
                        "" if v is None else _texto(v)])
        valores = ctx["valores"].get(sku) or {}
        for c in ((ctx["campos"].get(cat) or []) if cat else []):
            w.writerow([sku, titulo, cat, nombre, c["campo"], c["etiqueta"], c["nivel"],
                        c.get("tipo") or "", "/".join(c.get("unidades") or []),
                        valores.get(c["campo"], "")])
    nombre = f"checklist_almacen_{semana.isoformat()}_{len(skus)}skus.csv"
    # BOM: sin él, Excel en español abre los acentos como basura.
    return ("﻿" + buf.getvalue()).encode("utf-8"), nombre


# ══════════════════════════════════════════════════════════════════════════════
# La carga
# ══════════════════════════════════════════════════════════════════════════════

def _celdas_xlsx(datos: bytes) -> tuple[list[tuple[str, str, str, str, int]], list[dict]]:
    """[(sku, campo, valor, hoja, fila)], errores de estructura."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(datos), data_only=True, read_only=True)
    celdas, errores = [], []
    for ws in wb.worksheets:
        if ws.title.lower() in ("instrucciones", "_listas"):
            continue
        filas = ws.iter_rows(values_only=True)
        claves: list[str] | None = None
        n_fila = 0
        for n_fila, fila in enumerate(filas, start=1):
            if claves is None:
                if fila and _texto(fila[0]).lower() == "sku" and n_fila <= 5:
                    claves = [_texto(x) for x in fila]
                    inicio = n_fila + 3   # claves, títulos, pistas
                elif n_fila > 5:
                    break
                continue
            if n_fila < inicio or not fila:
                continue
            sku = _texto(fila[0]) if fila else ""
            if not sku:
                continue
            for j, clave in enumerate(claves):
                if j == 0 or not clave or clave in ("titulo",):
                    continue
                v = _texto(fila[j]) if j < len(fila) else ""
                if v:
                    celdas.append((sku, clave, v, ws.title, n_fila))
        if claves is None:
            errores.append({"sku": None, "campo": None, "hoja": ws.title, "fila": None,
                            "motivo": "La hoja no trae el renglón de claves (el oculto "
                                      "1). Descarga la plantilla desde el panel."})
    wb.close()
    return celdas, errores


def _celdas_csv(datos: bytes) -> tuple[list[tuple[str, str, str, str, int]], list[dict]]:
    texto = datos.decode("utf-8-sig", errors="replace")
    muestra = texto[:4096]
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel
    lector = csv.DictReader(io.StringIO(texto), dialect=dialecto)
    cab = {(c or "").strip().lower() for c in (lector.fieldnames or [])}
    if not {"sku", "campo", "valor"} <= cab:
        return [], [{"sku": None, "campo": None, "hoja": "CSV", "fila": None,
                     "motivo": "El CSV necesita las columnas sku, campo y valor "
                               "(formato largo, el que descarga el panel)."}]
    celdas = []
    for n, r in enumerate(lector, start=2):
        r = {(k or "").strip().lower(): (v or "") for k, v in r.items()}
        sku, campo, valor = r["sku"].strip(), r["campo"].strip(), r["valor"].strip()
        if sku and campo and valor:
            celdas.append((sku, campo, valor, "CSV", n))
    return celdas, []


def importar_sync(datos: bytes, nombre_archivo: str, aplicar: bool) -> dict[str, Any]:
    ext = (nombre_archivo or "").lower().rsplit(".", 1)[-1]
    fuente = "csv" if ext == "csv" else "excel"
    try:
        if ext in ("xlsx", "xlsm"):
            celdas, errores = _celdas_xlsx(datos)
        elif ext == "csv":
            celdas, errores = _celdas_csv(datos)
        else:
            return {"ok": False, "motivo": "Sube el Excel (.xlsx) o el CSV que "
                                           "descargaste del panel."}
    except Exception as exc:  # noqa: BLE001
        log.warning("checklist: no se pudo leer %s: %s", nombre_archivo, exc)
        return {"ok": False, "motivo": f"No se pudo leer el archivo: {exc}"}

    avisos: list[dict[str, Any]] = []
    pegados = limpiar_skus([c[0] for c in celdas])
    reales, desconocidos = _canonicos(pegados)
    for s in desconocidos:
        errores.append({"sku": s, "campo": None, "hoja": None, "fila": None,
                        "motivo": "SKU que kubera no conoce; se ignoró."})
    skus = [reales[s.upper()] for s in pegados if s.upper() in reales]
    try:
        ctx = _contexto(skus)
    except FaltaMigracion as exc:
        return {"ok": False, "falta_migracion": True, "motivo": str(exc)}

    ml: dict[str, dict[str, str]] = {}
    etiquetas: dict[str, dict[str, str]] = {}
    alm: dict[str, dict[str, Any]] = {}
    cambios: list[dict[str, Any]] = []
    sin_cambios = 0
    vistos: set[tuple[str, str]] = set()

    for pegado, campo, crudo, hoja, fila in celdas:
        sku = reales.get(pegado.upper())
        if not sku:
            continue
        donde = {"sku": sku, "campo": campo, "hoja": hoja, "fila": fila}
        if (sku, campo) in vistos:
            avisos.append({**donde, "motivo": "el campo viene repetido; se usó el primero"})
            continue
        vistos.add((sku, campo))

        if campo in _LOG_CLAVES:
            valor, error = _normalizar_logistica(campo, crudo)
            if error:
                errores.append({**donde, "motivo": f"{_LOG_ETIQUETA[campo]}: {error}"})
                continue
            antes = (ctx["almacen"].get(sku) or {}).get(campo)
            if antes is not None and float(antes) == float(valor):
                sin_cambios += 1
                continue
            alm.setdefault(sku, {})[campo] = valor
            cambios.append({"sku": sku, "campo": campo, "etiqueta": _LOG_ETIQUETA[campo],
                            "antes": _texto(antes), "despues": _texto(valor),
                            "tipo": "almacen"})
            continue

        cat = (ctx["cats_por_sku"].get(sku) or {}).get("categoria")
        if not cat:
            errores.append({**donde, "motivo": "el SKU no tiene categoría de Mercado "
                                               "Libre; sus atributos no se guardan."})
            continue
        c = next((x for x in ctx["campos"].get(cat) or [] if x["campo"] == campo), None)
        if not c:
            avisos.append({**donde, "motivo": f"{campo} ya no está en la categoría "
                                              f"{cat}; se ignoró."})
            continue
        antes = (ctx["valores"].get(sku) or {}).get(campo, "")
        # La celda que nadie tocó (viene pre-llenada) NO se normaliza: si el
        # valor guardado no trae unidad, «arreglarlo» aquí sería inventar un
        # cambio que almacén no hizo.
        if crudo == antes:
            sin_cambios += 1
            continue
        valor, error, aviso = _normalizar_ml(c, crudo)
        if error:
            errores.append({**donde, "motivo": f"{c['etiqueta']}: {error}"})
            continue
        if aviso:
            avisos.append({**donde, "motivo": f"{c['etiqueta']}: {aviso}"})
        if antes == valor:
            sin_cambios += 1
            continue
        ml.setdefault(sku, {})[campo] = valor
        etiquetas.setdefault(sku, {})[campo] = c["etiqueta"]
        cambios.append({"sku": sku, "campo": campo, "etiqueta": c["etiqueta"],
                        "antes": antes, "despues": valor, "tipo": "ml"})

    salida = {"ok": True, "aplicado": False, "archivo": nombre_archivo,
              "skus": len({c["sku"] for c in cambios}), "cambios": cambios,
              "errores": errores, "avisos": avisos, "sin_cambios": sin_cambios}
    if not aplicar or not cambios:
        return salida

    fallidos: list[dict[str, str]] = []
    guardados_ml = 0

    def _uno(sku: str) -> tuple[str, dict[str, Any]]:
        return sku, specs_editor.guardar_sync(sku, CANAL, ml[sku], etiquetas.get(sku))

    if ml:
        with ThreadPoolExecutor(max_workers=min(_HILOS_GUARDAR, len(ml))) as ex:
            for sku, res in ex.map(_uno, list(ml)):
                if res.get("ok"):
                    guardados_ml += 1
                else:
                    fallidos.append({"sku": sku, "motivo": res.get("motivo") or "?"})
    guardados_alm = 0
    if alm:
        try:
            guardados_alm = _guardar_almacen(alm, fuente, actor.actual())
        except FaltaMigracion as exc:
            fallidos.append({"sku": "—", "motivo": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.warning("checklist: almacén no se guardó: %s", exc)
            fallidos.append({"sku": "—", "motivo": f"medidas/cajas: {exc}"})
    log.info("checklist: %s cargó %s — %d SKUs ML, %d de almacén, %d fallidos",
             actor.actual() or "?", nombre_archivo, guardados_ml, guardados_alm,
             len(fallidos))
    return {**salida, "aplicado": True,
            "guardados": {"ml": guardados_ml, "almacen": guardados_alm,
                          "fallidos": fallidos}}


def guardar_almacen_sync(sku: str, valores: dict[str, Any]) -> dict[str, Any]:
    """Captura desde la pantalla: los seis campos de almacén de UN SKU."""
    reales, _ = _canonicos([sku])
    real = reales.get(sku.upper())
    if not real:
        return {"ok": False, "motivo": f"{sku} no existe en kubera."}
    fila: dict[str, Any] = {}
    for k in _LOG_CLAVES:
        crudo = _texto((valores or {}).get(k))
        if not crudo:
            continue
        v, error = _normalizar_logistica(k, crudo)
        if error:
            return {"ok": False, "motivo": f"{_LOG_ETIQUETA[k]}: {error}"}
        fila[k] = v
    if not fila:
        return {"ok": True, "guardados": 0}
    try:
        _guardar_almacen({real: fila}, "panel", actor.actual())
    except FaltaMigracion as exc:
        return {"ok": False, "falta_migracion": True, "motivo": str(exc)}
    return {"ok": True, "guardados": len(fila)}
