"""
costing_read.py — Lecturas del dominio COSTOS desde la BD kubera (F5, flag
SUPABASE_READ_COSTING).

Cada función devuelve EXACTAMENTE la misma forma que su gemela MySQL de
routers/crear.py; el router decide la fuente (flag) y hace el fallback a MySQL
ante cualquier error — apagar el flag = volver al instante.

Notas de traducción (MySQL → Postgres/kubera):
  - P4: `costing.costos_finales` es POR CANAL (PK sku+canal). El motor actual
    calcula un solo precio ML-céntrico, así que estas lecturas fijan
    canal='mercado_libre' — cuando el motor sea multi-canal, el llamador
    pasará el canal.
  - `productos.nombre` (MySQL) ≡ `core.products.name` (kubera): se alias-ea
    como `nombre` para que la forma no cambie.
  - LIKE de MySQL es case-insensitive (collation); el equivalente en Postgres
    es ILIKE.
  - Los logs de costos (`costos_logs`) NO viajan por aquí: la bitácora sigue
    leyéndose de MySQL en ambas rutas (es cosmética y su destino final es
    ops.process_log — pendiente de F5-bitácoras).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from services import channel_read, embarques, sku_contenedor
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.costing_read")

CANAL = "mercado_libre"

# Mismo contrato de orden que _ORDEN_COSTOS del router.
# El SKU sale de `core.products` (p), que desde v0.213.0 es el lado izquierdo del
# listado; lo de `costos_validados` (v) puede venir NULL, de ahí los NULLS LAST:
# sin ellos, Postgres pone los NULL primero en DESC y los SKUs sin costear
# tapaban el listado por defecto.
ORDEN = {
    "reciente": "v.created_at DESC NULLS LAST, p.sku ASC",
    "sku_asc": "p.sku ASC",
    "sku_desc": "p.sku DESC",
    "costo_desc": "v.costo_total DESC NULLS LAST",
    "costo_asc": "v.costo_total ASC NULLS LAST",
    "contenedor": "v.contenedor ASC NULLS LAST, p.sku ASC",
}


def contenedores() -> list[dict]:
    rows = sdb.fetch_all(
        "select contenedor, count(*) as n from costing.costos_validados "
        "where contenedor is not null and contenedor <> '' "
        "group by contenedor order by contenedor")
    return [{"contenedor": r["contenedor"], "n": int(r["n"])} for r in rows]


def finales(sku: str) -> dict[str, Any] | None:
    return sdb.fetch_one(
        "select * from costing.costos_finales where sku = %s and canal = %s",
        (sku, CANAL))


def validados(sku: str) -> dict[str, Any] | None:
    return sdb.fetch_one(
        "select * from costing.costos_validados where sku = %s", (sku,))


def bloqueado(sku: str) -> dict[str, Any] | None:
    """
    ``{revisado_at, revisado_por}`` si el costo del SKU esta VALIDADO, o None.

    Un costo validado se reconstruyo a mano desde el packing list y no debe
    moverse: el candado real vive en el UPSERT
    (``costing_mirror.upsert_validados``); esto es para poder AVISARLO en la
    pantalla en vez de que el usuario crea que guardo y no paso nada.
    """
    return sdb.fetch_one(
        "select revisado_at, revisado_por from costing.costos_validados "
        " where sku = %s and revisado_at is not null", (sku,))


def detalle(sku: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(finales, validados) del SKU — superset de columnas del par MySQL."""
    return finales(sku), validados(sku)


def pct_comision_categoria(cat_id: str) -> float | None:
    """La comisión más frecuente cacheada para un ml_cat_id (gemela de
    costos._comision_categoria_db). None si nunca se costeó esa categoría."""
    row = sdb.fetch_one(
        """select pct_comision from costing.costos_finales
           where ml_cat_id = %s and pct_comision > 0
           group by pct_comision order by count(*) desc limit 1""",
        (cat_id,))
    return float(row["pct_comision"]) if row and row.get("pct_comision") else None


# "Publicado en Mercado Libre" NO se redacta aquí: se pide. La insignia de esta
# tabla, su chip "Solo publicados en ML" y el validador de costos tienen que
# contestar lo MISMO, y cuando cada uno tenía su propio WHERE dejaron de
# hacerlo: la insignia decía "no publicado" de 76 SKUs que el validador sí
# aceptaba. La definición —y el porqué de `situacion` sobre `status`, y el
# porqué de dejar fuera `under_review`— vive en channel_read.
_EXISTE_PUB_ML = channel_read.sql_publicado_ml("p.sku")


def listado(page: int, per_page: int, search: str | None, contenedor: str | None,
            orden: str, skus_lista: list[str],
            sin_costo: bool = False,
            revisado: str | None = None,
            solo_publicados_ml: bool = False,
            embarque: str | None = None) -> tuple[list[dict], int]:
    """
    (rows, total) con las MISMAS columnas/alias que el SELECT MySQL del router.

    Desde v0.213.0 el lado izquierdo es `core.products`, no `costos_validados`.
    Antes la tabla nacía de costos_validados, así que un SKU sin fila ahí era
    INVISIBLE en la pantalla de Costos aunque estuviera publicado y vendiendo —
    y como la pantalla es el único lugar donde se captura, no había manera de
    darlo de alta desde ahí. Medido el 18-ago-2026: 123 SKUs con ventas en 60
    días ($469,546) no aparecían, y 6,461 productos del catálogo no tienen fila
    de costo. Con el LEFT JOIN salen en blanco y se pueden capturar.

    El cruce no pierde nada: los 15,837 de costos_validados tienen producto en
    core.products (verificado, 0 huérfanos).

    `sin_costo=True` deja solo los que NO tienen fila de costo — el filtro para
    trabajar el hueco.

    `embarque` es la clave de `services.embarques` (`n:80`, `c:MRKU3436938`) o
    `sin`. Va AL FINAL y con default: comparar_lecturas_costing.py y
    suite_caos_sandbox.py llaman por posición. Clave desconocida → 0 filas.
    Cada fila gana `embarques` (ver `_embarques_de_filas`).
    """
    where, params = [], []
    if search:
        where.append("(p.sku ilike %s or p.name ilike %s)")
        params += [f"%{search}%", f"%{search}%"]
    if skus_lista:
        or_grupo = " or ".join(["(p.sku ilike %s or p.name ilike %s)"] * len(skus_lista))
        where.append(f"({or_grupo})")
        for t in skus_lista:
            like_t = f"%{t}%"
            params += [like_t, like_t]
    if contenedor:
        # Filtrar por contenedor implica tener fila de costo: el contenedor vive ahí.
        where.append("v.contenedor = %s")
        params.append(contenedor)
    if embarque:
        filtro = _filtro_embarque(embarque)
        if filtro is None:
            return [], 0
        where.append(filtro[0])
        params += filtro[1]
    if sin_costo:
        where.append("v.sku is null")
    # Marca de revisión (0032). `movido` son los que se tocaron DESPUÉS de
    # revisarse: no es un error, es un aviso de que hay que volver a mirarlos.
    if revisado == "si":
        where.append("v.revisado_at is not null")
    elif revisado == "no":
        where.append("v.sku is not null and v.revisado_at is null")
    elif revisado == "movido":
        where.append("v.revisado_at is not null and v.updated_at > v.revisado_at")
    if solo_publicados_ml:
        where.append(_EXISTE_PUB_ML)
    where_sql = ("where " + " and ".join(where)) if where else ""
    orden_sql = ORDEN.get(orden, ORDEN["reciente"])

    total = sdb.fetch_scalar(
        f"select count(*) from core.products p "
        f"left join costing.costos_validados v on v.sku = p.sku {where_sql}",
        tuple(params)) or 0
    offset = (page - 1) * per_page
    rows = sdb.fetch_all(
        f"""select p.sku, p.name as nombre, v.contenedor,
                   v.largo, v.alto, v.ancho, v.peso,
                   v.costo_producto, v.costo_cbm, v.costo_total,
                   f.costo_unitario, f.precio_base, f.precio_sugerido,
                   f.costo_comision, f.costo_fee_envio, f.ml_cat_id,
                   v.revisado_at, v.revisado_por,
                   (v.revisado_at is not null and v.updated_at > v.revisado_at)
                     as revision_movida,
                   {_EXISTE_PUB_ML} as publicado_ml
            from core.products p
            left join costing.costos_validados v on v.sku = p.sku
            left join costing.costos_finales f on f.sku = p.sku and f.canal = %s
            {where_sql} order by {orden_sql} limit %s offset %s""",
        tuple([CANAL] + params + [per_page, offset]))
    _embarques_de_filas(rows)
    return rows, int(total)


# ── Embarques (contenedores desde packing lists + costos) ───────────────────
# La agrupación vive en services.embarques; aquí solo lo que toca el listado.
#
# Sin EXISTS correlacionado dentro de un OR: en producción uno así agotó el
# statement_timeout. Un embarque filtra con `p.sku in (select …)` (subplan
# hasheado, sin correlación, porque va dentro del OR de sus dos fuentes).
# «Sin contenedor» SIEMPRE va unido con AND, nunca dentro de un OR: ahí el
# `not exists` correlacionado se vuelve anti-join (medido el 25-sep a volumen de
# prod: ~85-145 ms). No usar `not in`: Postgres solo lo hashea mientras la tabla
# quepa en work_mem (~111k renglones de packing_ubicaciones) y pasado eso cae a
# un subplan lineal que agota el statement_timeout.
_SIN_CONTENEDOR = (
    "coalesce(v.contenedor, '') = '' and not exists "
    "(select 1 from costing.packing_ubicaciones u where u.sku = p.sku)")
_EN_PACKING = ("p.sku in (select u.sku from costing.packing_ubicaciones u "
               "where u.ferraforme_sha256 = any(%s::text[]))")
# La TERCERA fuente, con LEER_SKU_CONTENEDOR: costing.sku_contenedor (0060). Mismo
# molde que el packing (subplan hasheado por `numero`, que tiene índice) y, en
# «sin contenedor», mismo anti-join unido con AND.
_EN_TABLA = ("p.sku in (select sc.sku from costing.sku_contenedor sc "
             "where sc.numero = %s)")
_SIN_TABLA = (" and not exists "
              "(select 1 from costing.sku_contenedor sc where sc.sku = p.sku)")


def _grupos(forzar: bool = False) -> tuple[embarques.Agrupacion, list[dict],
                                           dict[str, dict], dict[int, str | None]]:
    """
    (agrupación, grupos, por_clave, números de la tabla). Sin
    LEER_SKU_CONTENEDOR (o con la tabla caída) los números vienen vacíos y los
    grupos son los de `embarques.agrupacion()` tal cual: v0.574. Con el flag, la
    tabla se SUMA
    (`embarques.sumar_tabla`): "tabla" en `fuentes` y las N que solo ella
    conoce como opción nueva.
    """
    ag = embarques.agrupacion(forzar=forzar) if forzar else embarques.agrupacion()
    tabla = sku_contenedor.numeros(forzar=forzar) if forzar else sku_contenedor.numeros()
    if not tabla:
        return ag, ag.grupos, ag.por_clave, {}
    grupos = embarques.sumar_tabla(ag.grupos, tabla)
    return ag, grupos, {g["clave"]: g for g in grupos}, tabla


def _filtro_embarque(clave: str) -> tuple[str, list] | None:
    """(fragmento WHERE, params) del embarque, o None si la clave no existe."""
    if clave == "sin":
        # «Sin contenedor» también excluye lo que la tabla ubica (con el flag).
        return (_SIN_CONTENEDOR + (_SIN_TABLA if sku_contenedor.numeros() else "")), []
    _, _, por_clave, tabla = _grupos()
    grupo = por_clave.get(clave)
    if grupo is None:
        return None
    partes, params = [], []
    if grupo["valores_costos"]:
        partes.append("v.contenedor = any(%s::text[])")
        params.append(list(grupo["valores_costos"]))
    if grupo["shas"]:
        partes.append(_EN_PACKING)
        params.append(list(grupo["shas"]))
    if grupo.get("numero") is not None and grupo["numero"] in tabla:
        partes.append(_EN_TABLA)
        params.append(int(grupo["numero"]))
    if not partes:
        return "false", []
    return "(" + " or ".join(partes) + ")", params


def _embarques_de_filas(rows: list[dict]) -> None:
    """
    Pega a cada fila `embarques: [{clave, etiqueta, fuente}]` — fuente
    `costos` | `packing` | `ambos`. UNA consulta por página a
    packing_ubicaciones (`sku = any`), nada por fila.

    Es decoración: si el packing no se puede leer, las filas van con
    `embarques: None` (el panel pinta el `contenedor` crudo, como en MySQL) en
    vez de tumbar el listado entero.

    Con LEER_SKU_CONTENEDOR la tabla costing.sku_contenedor es la TERCERA fuente
    (una consulta más por página): cada embarque gana además `fuentes` (la
    lista, p. ej. ``["costos", "tabla"]``) y `fuente` puede valer `tabla` (solo
    la tabla lo afirma). Con dos o más fuentes, `fuente` = `ambos`; «packing»
    sigue significando que SOLO el packing lo afirma (la «PL» del panel).
    """
    if not rows:
        return
    try:
        ag, grupos, por_clave, tabla = _grupos()
        skus = [str(r["sku"]) for r in rows if r.get("sku")]
        packing = sdb.fetch_all(
            """select distinct u.sku::text as sku, u.ferraforme_sha256 as sha
                 from costing.packing_ubicaciones u
                where u.sku = any(%s::citext[])""", (skus,)) if skus else []
    except Exception as exc:  # noqa: BLE001 — decoración, no debe tumbar la tabla
        log.warning("embarques por fila no disponibles: %s", exc)
        for r in rows:
            r["embarques"] = None
        return
    # La tabla por SKU, solo con el flag y si sus números se pudieron leer. Si
    # esta lectura falla, `por_sku` devuelve None: ninguna fila gana la fuente
    # «tabla» y la columna enseña lo mismo que en v0.574.
    en_tabla = sku_contenedor.por_sku(skus) if tabla else {}
    shas_por_sku: dict[str, list[str]] = {}
    for f in packing:
        shas_por_sku.setdefault(str(f["sku"]).lower(), []).append(f["sha"])
    posicion = {g["clave"]: i for i, g in enumerate(grupos)}
    for r in rows:
        fuentes: dict[str, set[str]] = {}
        etiquetas: dict[str, str] = {}
        clave = ag.por_valor.get(r.get("contenedor") or "")
        if clave:
            fuentes.setdefault(clave, set()).add("costos")
        for sha in shas_por_sku.get(str(r.get("sku") or "").lower(), []):
            clave = ag.por_sha.get(sha)
            if clave:
                fuentes.setdefault(clave, set()).add("packing")
        for c in sku_contenedor.de(en_tabla, r.get("sku")):
            clave = f"n:{c['numero']}"
            fuentes.setdefault(clave, set()).add("tabla")
            if clave not in por_clave:  # N nueva que la caché de 30 s aún no ve
                etiquetas[clave] = embarques.etiqueta_tabla(c["numero"], c.get("codigo"))
        orden_ = sorted(fuentes.items(), key=lambda kv: posicion.get(kv[0], 0))
        if not tabla:
            r["embarques"] = [
                {"clave": k, "etiqueta": por_clave[k]["etiqueta"],
                 "fuente": "ambos" if len(f) == 2 else next(iter(f))}
                for k, f in orden_]
            continue
        r["embarques"] = [
            {"clave": k,
             "etiqueta": por_clave[k]["etiqueta"] if k in por_clave else etiquetas[k],
             "fuente": "ambos" if len(f) >= 2 else next(iter(f)),
             "fuentes": [x for x in ("costos", "packing", "tabla") if x in f]}
            for k, f in orden_]


def conteos_embarques(grupos: list[dict],
                      tabla: dict[int, str | None] | None = None) -> dict[str, dict[str, int]]:
    """
    ``{clave: {n, sin_costo}}`` de TODOS los grupos más `sin`, en UNA consulta.

    `n` = SKUs DISTINTOS de core.products que caen en el grupo por cualquiera
    de las dos fuentes (el listado nace de core.products: los SKUs del packing
    que no existen ahí no se cuentan). `sin_costo` = cuántos de ellos no tienen
    fila en costos_validados. Un SKU en dos contenedores cuenta en los dos: los
    conteos por opción no suman el total, y es correcto.

    `count(*)` ya son SKUs distintos: `miembros` es un UNION (sin repetidos por
    (clave, sku), con la igualdad de citext) y products/costos_validados tienen
    sku único. `count(distinct p.sku)` daba lo mismo pero obligaba a ordenar
    por citext (~540 ms contra ~285 ms a volumen de prod).

    Con LEER_SKU_CONTENEDOR (`tabla` = ``{N: código}``; None = leerlo aquí) la
    tabla costing.sku_contenedor es una TERCERA rama de `miembros` y «sin» deja
    fuera a los SKUs que ella ubica. Sin el flag, la consulta es la de v0.574.
    """
    if tabla is None:
        tabla = sku_contenedor.numeros()
    cl_c, val_c, cl_p, sha_p, cl_t, num_t = [], [], [], [], [], []
    for g in grupos:
        for v in g["valores_costos"]:
            cl_c.append(g["clave"])
            val_c.append(v)
        for s in g["shas"]:
            cl_p.append(g["clave"])
            sha_p.append(s)
        if tabla and g.get("numero") is not None and g["numero"] in tabla:
            cl_t.append(g["clave"])
            num_t.append(int(g["numero"]))
    cte_tabla = union_tabla = sin_tabla = ""
    params: tuple = (cl_c, val_c, cl_p, sha_p)
    if tabla:
        cte_tabla = """, g_tabla as (
                select * from unnest(%s::text[], %s::int[]) as t(clave, numero)
            )"""
        union_tabla = """
                union
                select g.clave, sc.sku
                  from g_tabla g
                  join costing.sku_contenedor sc on sc.numero = g.numero"""
        sin_tabla = _SIN_TABLA
        params = (cl_c, val_c, cl_p, sha_p, cl_t, num_t)
    filas = sdb.fetch_all(
        f"""with g_costos as (
                select * from unnest(%s::text[], %s::text[]) as t(clave, valor)
            ), g_packing as (
                select * from unnest(%s::text[], %s::text[]) as t(clave, sha)
            ){cte_tabla}, miembros as (
                select g.clave, v.sku
                  from g_costos g
                  join costing.costos_validados v on v.contenedor = g.valor
                union
                select g.clave, u.sku
                  from g_packing g
                  join costing.packing_ubicaciones u on u.ferraforme_sha256 = g.sha{union_tabla}
            )
            select m.clave, count(*) as n,
                   count(*) filter (where v.sku is null) as sin_costo
              from miembros m
              join core.products p on p.sku = m.sku
              left join costing.costos_validados v on v.sku = p.sku
             group by m.clave
            union all
            select 'sin' as clave, count(*) as n,
                   count(*) filter (where v.sku is null) as sin_costo
              from core.products p
              left join costing.costos_validados v on v.sku = p.sku
             where {_SIN_CONTENEDOR}{sin_tabla}""",
        params)
    return {f["clave"]: {"n": int(f["n"] or 0), "sin_costo": int(f["sin_costo"] or 0)}
            for f in filas}


def embarques_con_conteos() -> dict[str, Any]:
    """
    Cuerpo de `GET /api/crear/costos/_embarques`. Siempre rehace la agrupación
    (y con eso refresca la caché de 30 s del listado). Los grupos con n = 0
    (todos sus SKUs fuera de core.products) no se devuelven.

    Con LEER_SKU_CONTENEDOR, `fuentes` puede traer "tabla" y aparecen las N que
    solo conoce costing.sku_contenedor (`fuentes: ["tabla"]`).
    """
    _, grupos, _, tabla = _grupos(forzar=True)
    conteos = conteos_embarques(grupos, tabla)
    salida = []
    for g in grupos:
        c = conteos.get(g["clave"]) or {"n": 0, "sin_costo": 0}
        if c["n"] <= 0:
            continue
        salida.append({"clave": g["clave"], "etiqueta": g["etiqueta"],
                       "numero": g["numero"], "codigos": g["codigos"],
                       "n": c["n"], "sin_costo": c["sin_costo"],
                       "fuentes": g["fuentes"]})
    sin = conteos.get("sin") or {"n": 0, "sin_costo": 0}
    return {"embarques": salida,
            "sin_contenedor": {"clave": "sin", "n": sin["n"],
                               "sin_costo": sin["sin_costo"]},
            "generado_en": datetime.now(timezone.utc).isoformat()}


# ── Lotes para la vista Crear Productos (paso 0, 12-ago-2026) ───────────────
# Gemelas de creacion._costos_por_sku / _contenedores_por_sku. Van en lotes de
# 800 como las originales: el límite de placeholders es el mismo aquí.

def costos_por_sku(skus: list[str]) -> dict[str, float]:
    """{ sku: costo_unitario } (respaldo costo_producto), como el par MySQL."""
    salida: dict[str, float] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        idx = {s.lower(): s for s in chunk}  # citext: la llave la pone el llamador
        for r in sdb.fetch_all(
            """select sku::text as sku, costo_unitario, costo_producto
                 from costing.costos_finales
                where sku = any(%s::citext[]) and canal = %s""",
                (chunk, CANAL)):
            costo = r.get("costo_unitario") or r.get("costo_producto")
            if costo:
                salida[idx.get(r["sku"].lower(), r["sku"])] = float(costo)
    return salida


def contenedores_por_sku(skus: list[str]) -> dict[str, str]:
    """{ sku: nº de contenedor } desde costing.costos_validados."""
    salida: dict[str, str] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        idx = {s.lower(): s for s in chunk}
        for r in sdb.fetch_all(
            """select sku::text as sku, contenedor
                 from costing.costos_validados
                where sku = any(%s::citext[])
                  and nullif(contenedor, '') is not null""", (chunk,)):
            if r.get("contenedor"):
                salida[idx.get(r["sku"].lower(), r["sku"])] = r["contenedor"]
    return salida


def revisados_por_sku(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    ``{ sku: {revisado_at, revisado_por, movida} }`` para los SKUs YA revisados.

    Gemela por lotes de `contenedores_por_sku`: mismos 800 por tanda (el límite
    de placeholders es el mismo) y misma forma de índice para el citext.

    Devuelve SOLO los revisados. Un SKU ausente del dict = sin revisar, que es
    el caso mayoritario (15,838 de 15,838 al abrir la migración 0032) y que no
    tiene sentido acarrear como miles de valores nulos hasta el navegador.
    """
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        idx = {s.lower(): s for s in chunk}
        for r in sdb.fetch_all(
            """select sku::text as sku, revisado_at, revisado_por,
                      (updated_at > revisado_at) as movida
                 from costing.costos_validados
                where sku = any(%s::citext[]) and revisado_at is not null""",
                (chunk,)):
            salida[idx.get(r["sku"].lower(), r["sku"])] = {
                "revisado_at": r["revisado_at"],
                "revisado_por": r.get("revisado_por"),
                "movida": bool(r.get("movida")),
            }
    return salida


def skus_revisados(limite: int = 2000) -> list[str]:
    """
    TODOS los SKUs con la marca de costo validado (migración 0032).

    Al revés que ``revisados_por_sku``, que pregunta por un lote conocido: aquí
    no hay lote, la pregunta es "¿cuáles están validados?" — la que necesita el
    filtro de Omnicanal para poder PAGINAR sobre ese subconjunto. Filtrar
    después de traer la página daría totales mentirosos.

    El ``limite`` no es una optimización, es un candado. Del otro lado la lista
    se convierte en un término LIKE por SKU (``woocommerce._buscar_wc_ids_db``),
    así que 2,000 validados ya son 4,000 comodines en un solo WHERE contra el
    MySQL de WordPress. Hoy hay **19** marcados y el catálogo son ~15,800, o sea
    que falta muchísimo para acercarse; cuando se acerque, lo que toca no es
    subir el número sino cambiar ese camino por un ``IN`` de SKUs exactos.
    El llamador AVISA cuando el tope corta — un filtro que devuelve de menos sin
    decirlo es peor que no tenerlo.
    """
    filas = sdb.fetch_all(
        """select sku::text as sku
             from costing.costos_validados
            where revisado_at is not null
            order by revisado_at desc
            limit %s""",
        (limite,))
    return [f["sku"] for f in filas]


def validados_de(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    ``{ sku: {nombre, contenedor, costo_*, dimensiones, revisado_at, ...} }``.

    El costo GUARDADO de un lote de SKUs, con el nombre del producto pegado.
    Es lo que el validador de publicados necesita para tres cosas a la vez:
    contrastar el costo nuevo contra el viejo, saber si el candado de COSTO
    VALIDADO ya está puesto (``revisado_at``), y conservar el costo de producto
    cuando el packing list viene sin precios.

    Sale de `core.products` por la izquierda, igual que el listado: un SKU sin
    fila de costo tiene que aparecer con todo en nulo, no desaparecer.
    """
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 800):
        chunk = [str(s) for s in skus[i:i + 800] if s]
        if not chunk:
            continue
        idx = {s.lower(): s for s in chunk}
        for r in sdb.fetch_all(
            """select p.sku::text as sku, p.name as nombre, v.contenedor,
                      v.largo, v.alto, v.ancho, v.peso,
                      v.costo_producto, v.costo_cbm, v.costo_total,
                      v.revisado_at, v.revisado_por,
                      (v.sku is not null) as tiene_costo
                 from core.products p
                 left join costing.costos_validados v on v.sku = p.sku
                where p.sku = any(%s::citext[])""", (chunk,)):
            salida[idx.get(r["sku"].lower(), r["sku"])] = dict(r)
    return salida


def costos_todos() -> dict[str, float]:
    """
    { sku: costo_unitario } (respaldo costo_producto) de TODO el catálogo.

    Gemela de `sync_woo._costos_finales`, que es la que EMPUJA el costo a la
    meta del producto en WooCommerce. Leerlo del espejo congelado no daría un
    dato viejo y ya: al comparar contra Woo vería una diferencia y le
    ESCRIBIRÍA el valor viejo encima, deshaciendo cada recálculo del panel.
    """
    salida: dict[str, float] = {}
    for r in sdb.fetch_all(
        """select sku::text as sku, costo_unitario, costo_producto
             from costing.costos_finales where canal = %s""", (CANAL,)):
        costo = r.get("costo_unitario") or r.get("costo_producto")
        if costo:
            salida[r["sku"]] = round(float(costo), 2)
    return salida


def precios_de(skus: list[str]) -> dict[str, dict[str, Any]]:
    """{ sku: {precio_sugerido, precio_base, ml_cat_id} } para el listado ML."""
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        idx = {s.lower(): s for s in chunk}
        for r in sdb.fetch_all(
            """select sku::text as sku, precio_sugerido, precio_base, ml_cat_id
                 from costing.costos_finales
                where sku = any(%s::citext[]) and canal = %s""", (chunk, CANAL)):
            salida[idx.get(r["sku"].lower(), r["sku"])] = {
                "precio_sugerido": r.get("precio_sugerido"),
                "precio_base": r.get("precio_base"),
                "ml_cat_id": r.get("ml_cat_id")}
    return salida
