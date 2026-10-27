"""
categoria_vectores.py — La categoría de ML revisada por TEXTO y FOTO antes de publicar.

POR QUÉ EXISTE. La tarjeta de categoría de la vista previa (`ml_revision_categoria`,
v0.677.0) compara solo el TÍTULO contra el predictor de ML, y Crear eligió la categoría
con ese mismo predictor: casi siempre sale «coincide» aunque esté mal. Medido el 26-oct:
~7 % de las publicaciones activas en ML están en una categoría equivocada.

QUÉ HACE. Convierte en una HUELLA (vector) lo que es el producto —título con
descripción (qwen3-embedding-8b) y sus 2 primeras fotos (voyage-multimodal-3.5), por
OpenRouter— y la compara contra la huella de TODAS las hojas del árbol de ML
(`channel.ml_category_vec`, migración 0078, la llena `scripts/vectorizar_arbol_ml.py`).
De ahí sale el lugar de la categoría elegida entre las ~10.7k (1 = la más parecida) y:

    verde   la elegida está entre las 5 más parecidas (texto y fotos juntos)
    ambar   entre la 6 y la 20
    rojo    más allá de la 20: el producto no se parece a su categoría
    sin_senal  no se pudo calcular (sin clave, sin huellas, OpenRouter falló, tope de 24 h)

con propuestas («Usar esta») de las más parecidas por texto y por texto+fotos.

LO QUE MIDIÓ LA PRUEBA (26/27-oct, 192 publicaciones revisadas a mano; ver
`RESULTADOS_VECTORES.md` de la sesión): de 50 categorías equivocadas, 41 en rojo, 7 en
ámbar y 2 en verde; de 100 correctas, 72 verdes, 14 ámbar y 14 rojas. La correcta quedó
entre las propuestas (más las del predictor) en 47 de 50. Los umbrales se fijan con el
modo SOMBRA (2 semanas en producción sin frenar nada).

LA FUSIÓN. Cada señal se normaliza por fila (z sobre las ~10.7k hojas) y se suman con el
mismo peso: no se ajustó nada sobre la muestra. Las fotos se promedian. Sin fotos, decide
el texto solo.

EL CANDADO (`CATEGORIA_VEC_CANDADO`). En rojo, `publicar_ready.crear_ml` no da de alta
sin un motivo escrito (o sin cambiar a otra categoría, que regenera la vista previa y la
revisión). Se busca la ÚLTIMA revisión de ese SKU y categoría en el servidor: el cliente no
puede saltárselo omitiendo un id. Si no hay revisión (o fue `sin_senal`), se publica con la
revisión de siempre (decisión de Eduardo, 27-oct).

EN EL ESTUDIO (27-oct-2026). El mismo semáforo se pinta junto al selector de categoría
(`POST /api/publicar/categoria-ml/revision`), para corregirla ANTES de llenar atributos.
Lo ya revisado no se vuelve a pagar: si la misma entrada (categoría, título con descripción
y fotos) se revisó en las últimas 6 h, se devuelve esa revisión (`reusada`). Así abrir el
Estudio dos veces, o el Estudio y luego la vista previa con el mismo texto, cuesta una vez.

Nunca lanza hacia la vista previa ni hacia el alta: lo que falla queda en `sin_senal`.
REGLA 11: la red va con httpx async; la BD (psycopg2) en `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from html import unescape
from typing import Any

import httpx

from config import settings
from core import actor

log = logging.getLogger("omnicanal.categoria_vectores")

API = "https://openrouter.ai/api/v1"
MODELO_TEXTO = "qwen/qwen3-embedding-8b"
MODELO_FOTO = "voyageai/voyage-multimodal-3.5"
DIMS = 512                      # medido: 512 dims rinden igual que 1024 (Matryoshka)
MAX_FOTOS = 2                   # la 3.ª foto casi no agregó nada en la prueba
VERDE_HASTA = 5
AMBAR_HASTA = 20
PROPUESTAS = 3                  # por fuente: texto+fotos y texto
MOTIVO_MIN = 10                 # caracteres de un motivo que diga algo
VIGENCIA_REVISION = "6 hours"   # la revisión que cuenta al dar de alta
TIMEOUT_S = 10.0               # tope TOTAL por señal (texto, fotos): lo más que espera la vista previa
# qwen3 es asimétrico: la consulta lleva instrucción; las rutas del árbol, no.
INSTRUCCION = ("Instruct: Given a product listing from Mercado Libre Mexico, retrieve the "
               "category path where it should be listed\nQuery: ")

MSG_CANDADO = (
    "La revisión de categoría salió en ROJO: el título, la descripción y las fotos no se "
    "parecen a esta categoría. Elige una de las propuestas o escribe por qué esta es la "
    "correcta (mínimo {n} caracteres) y vuelve a confirmar."
)


# ───────────────────────── piezas puras ─────────────────────────

def limpio(s: str | None, n: int) -> str:
    """Texto plano (sin HTML ni espacios de sobra), recortado a `n`."""
    s = unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


def texto_producto(titulo: str | None, descripcion: str | None) -> str:
    """La consulta de texto, igual que en la prueba: título[:200] + descripción[:600]."""
    t = limpio(titulo, 200)
    d = limpio(descripcion, 600)
    return INSTRUCCION + (f"{t}. {d}" if d else t)


def fotos_validas(fotos: list[Any]) -> list[str]:
    """Las primeras MAX_FOTOS URLs http(s) — lo único que se manda a OpenRouter."""
    return [u for u in fotos or [] if isinstance(u, str) and u.startswith("http")][:MAX_FOTOS]


def entrada(categoria_id: str | None, titulo: str | None, descripcion: str | None,
            fotos: list[Any]) -> str:
    """Pura: la firma de lo que se revisa. Misma firma = misma respuesta (no se paga otra vez)."""
    crudo = json.dumps([categoria_id or "", texto_producto(titulo, descripcion), fotos_validas(fotos),
                        MODELO_TEXTO, MODELO_FOTO, DIMS], ensure_ascii=False)
    return hashlib.sha1(crudo.encode("utf-8")).hexdigest()


def normalizar(v: list[float], dims: int = DIMS) -> list[float] | None:
    """Recorta a `dims` (los modelos son Matryoshka) y deja norma 1. None si viene vacío."""
    v = [float(x) for x in (v or [])[:dims]]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else None


def promedio(vs: list[list[float]]) -> list[float] | None:
    """El promedio de varias huellas normalizadas, otra vez con norma 1."""
    vs = [v for v in vs if v]
    if not vs:
        return None
    return normalizar([sum(c) / len(vs) for c in zip(*vs)], len(vs[0]))


def literal(v: list[float]) -> str:
    """Un vector como literal de pgvector: '[0.1,-0.2,…]'."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def color(rango_texto: int | None, rango_fusion: int | None, hay_foto: bool) -> str:
    """Pura: el semáforo por el lugar de la categoría elegida (fusión si hay fotos)."""
    r = rango_fusion if hay_foto else rango_texto
    if r is None:
        return "sin_senal"
    if r <= VERDE_HASTA:
        return "verde"
    if r <= AMBAR_HASTA:
        return "ambar"
    return "rojo"


def propuestas(filas: list[dict[str, Any]], actual: str | None,
               por_fuente: int = PROPUESTAS, principal: str = "texto_y_fotos") -> list[dict[str, Any]]:
    """Las más parecidas por texto+fotos (o la señal que hubo) y por texto, sin repetir ni
    incluir la elegida."""
    out: list[dict[str, Any]] = []
    vistos = {actual} if actual else set()
    for fuente, campo in ((principal, "rz"), ("texto", "rt")):
        n = 0
        for f in sorted(filas, key=lambda x: x.get(campo) or 10**9):
            if n >= por_fuente:
                break
            cid = f["category_id"]
            if cid in vistos or (f.get(campo) or 10**9) > 50:
                continue
            vistos.add(cid)
            n += 1
            nombres = list(f.get("path_names") or [])
            ids = list(f.get("path_ids") or [])
            out.append({
                "category_id": cid,
                "name": nombres[-1] if nombres else cid,
                "ruta": " > ".join(nombres),
                "niveles": [{"id": i, "name": nm} for i, nm in zip(ids, nombres)],
                "publicable": True,
                "fuente": fuente,
                "rango": f.get(campo),
            })
    return out


def motivo_candado(fila: dict[str, Any] | None, motivo: str | None,
                   candado: bool) -> str | None:
    """Pura: motivo para NO dar de alta, o None."""
    if not candado or not fila or fila.get("color") != "rojo":
        return None
    if len((motivo or "").strip()) >= MOTIVO_MIN:
        return None
    return MSG_CANDADO.format(n=MOTIVO_MIN)


# ───────────────────────── OpenRouter ─────────────────────────

async def _embeddings(cli: httpx.AsyncClient, modelo: str,
                      entradas: list[Any]) -> tuple[list[list[float]], float]:
    """→ (vectores normalizados en el orden de `entradas`, costo USD). Lanza si falla."""
    r = await cli.post("/embeddings", json={"model": modelo, "input": entradas})
    r.raise_for_status()
    d = r.json()
    datos = sorted(d.get("data") or [], key=lambda x: x.get("index", 0))
    if len(datos) != len(entradas):
        raise ValueError(f"{modelo}: {len(datos)} vectores para {len(entradas)} entradas")
    costo = float(((d.get("usage") or {}).get("cost")) or 0.0)
    return [normalizar(x.get("embedding") or []) for x in datos], costo


async def huella(titulo: str | None, descripcion: str | None,
                 fotos: list[str]) -> dict[str, Any]:
    """Huella del texto y de las fotos. Una foto que falla no tumba al texto."""
    out: dict[str, Any] = {"texto": None, "foto": None, "n_fotos": 0, "costo": 0.0, "errores": []}
    fotos = fotos_validas(fotos)
    headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"}
    async with httpx.AsyncClient(base_url=API, timeout=TIMEOUT_S, headers=headers) as cli:
        tareas = [_embeddings(cli, MODELO_TEXTO, [texto_producto(titulo, descripcion)])]
        if fotos:
            tareas.append(_embeddings(cli, MODELO_FOTO, [
                {"content": [{"type": "image_url", "image_url": {"url": u}}]} for u in fotos]))
        # El timeout de httpx es por etapa (conectar, leer…); wait_for pone el tope TOTAL por señal.
        res = await asyncio.gather(*(asyncio.wait_for(t, TIMEOUT_S) for t in tareas),
                                   return_exceptions=True)
    if isinstance(res[0], BaseException):
        out["errores"].append(f"texto: {type(res[0]).__name__}: {str(res[0])[:160]}")
    else:
        out["texto"] = res[0][0][0]
        out["costo"] += res[0][1]
    if len(res) > 1:
        if isinstance(res[1], BaseException):
            out["errores"].append(f"fotos: {type(res[1]).__name__}: {str(res[1])[:160]}")
        else:
            out["foto"] = promedio(res[1][0])
            out["n_fotos"] = len(res[1][0])
            out["costo"] += res[1][1]
    return out


# ───────────────────────── base de datos (síncrono) ─────────────────────────

def _sdb():
    from services import supabase_db
    return supabase_db


def huellas_listas() -> bool:
    """¿Hay huellas del árbol para los dos modelos? (sin tabla = no)."""
    try:
        n = _sdb().fetch_scalar(
            "select count(distinct modelo) from channel.ml_category_vec where modelo = any(%s)",
            ([MODELO_TEXTO, MODELO_FOTO],))
        return int(n or 0) == 2
    except Exception as exc:  # noqa: BLE001
        log.info("huellas de categorías no disponibles: %s", exc)
        return False


def gastado_24h() -> float:
    """USD de las revisiones de las últimas 24 h. Sin poder medir = infinito (no se gasta)."""
    try:
        v = _sdb().fetch_scalar(
            "select coalesce(sum(costo_usd), 0) from channel.ml_revision_categoria "
            " where creado_at >= now() - interval '24 hours'")
        return float(v or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("revisión de categoría: no se pudo leer el gasto de 24 h: %s", exc)
        return float("inf")


_SQL_RANGOS = """
with t as (
    select category_id, -(vec operator(extensions.<#>) %(qt)s::extensions.halfvec) as s
      from channel.ml_category_vec where modelo = %(mt)s),
f as (
    select category_id, -(vec operator(extensions.<#>) %(qf)s::extensions.halfvec) as s
      from channel.ml_category_vec where modelo = %(mf)s),
j as (
    select t.category_id, t.s as st, f.s as sf from t join f using (category_id)),
z as (
    select category_id, st, sf,
           (st - avg(st) over ()) / nullif(stddev_pop(st) over (), 0) as zt,
           (sf - avg(sf) over ()) / nullif(stddev_pop(sf) over (), 0) as zf
      from j),
r as (
    select category_id, st, sf,
           rank() over (order by st desc)       as rt,
           rank() over (order by sf desc)       as rf,
           rank() over (order by zt + zf desc)  as rz
      from z)
select r.category_id, r.rt, r.rf, r.rz, round(r.st::numeric, 4) as st, round(r.sf::numeric, 4) as sf,
       tr.path_ids, tr.path_names
  from r left join channel.ml_category_tree tr using (category_id)
 where r.rt <= %(k)s or r.rz <= %(k)s or r.category_id = %(cid)s
"""

_SQL_RANGOS_UNA = """
with t as (
    select category_id, -(vec operator(extensions.<#>) %(q)s::extensions.halfvec) as s
      from channel.ml_category_vec where modelo = %(m)s),
r as (
    select category_id, s, rank() over (order by s desc) as r from t)
select r.category_id, r.r, round(r.s::numeric, 4) as s, tr.path_ids, tr.path_names
  from r left join channel.ml_category_tree tr using (category_id)
 where r.r <= %(k)s or r.category_id = %(cid)s
"""


def rangos(categoria_id: str, qt: list[float] | None, qf: list[float] | None,
           k: int = 10) -> list[dict[str, Any]]:
    """Las k más parecidas (texto y fusión) más la elegida, con su lugar entre todas.

    Con una sola señal (el texto, o las fotos si el texto no contestó) `rz` es esa señal."""
    if qt and qf:
        return _sdb().fetch_all(_SQL_RANGOS, {"qt": literal(qt), "mt": MODELO_TEXTO, "qf": literal(qf),
                                              "mf": MODELO_FOTO, "k": k, "cid": categoria_id or ""})
    es_texto = bool(qt)
    filas = _sdb().fetch_all(_SQL_RANGOS_UNA, {"q": literal(qt if es_texto else qf),
                                               "m": MODELO_TEXTO if es_texto else MODELO_FOTO,
                                               "k": k, "cid": categoria_id or ""})
    return [{"category_id": f["category_id"], "path_ids": f["path_ids"], "path_names": f["path_names"],
             "rt": f["r"] if es_texto else None, "rf": None if es_texto else f["r"], "rz": f["r"],
             "st": f["s"] if es_texto else None, "sf": None if es_texto else f["s"]} for f in filas]


def _guardar(fila: dict[str, Any]) -> int | None:
    from psycopg2.extras import Json
    try:
        r = _sdb().execute_returning(
            "insert into channel.ml_revision_categoria "
            " (sku, wc_id, categoria_id, color, rango_texto, rango_fotos, rango_fusion, n_fotos, "
            "  propuestas, detalle, costo_usd, creado_por) "
            "values (%(sku)s, %(wc_id)s, %(categoria_id)s, %(color)s, %(rango_texto)s, %(rango_fotos)s, "
            "        %(rango_fusion)s, %(n_fotos)s, %(propuestas)s, %(detalle)s, %(costo_usd)s, %(creado_por)s) "
            "returning id",
            {**fila, "propuestas": Json(fila["propuestas"]), "detalle": Json(fila["detalle"])})
        return int(r["id"]) if r else None
    except Exception as exc:  # noqa: BLE001
        log.warning("revisión de categoría (%s): no se pudo guardar: %s", fila.get("sku"), exc)
        return None


def ultima_revision(sku: str, categoria_id: str) -> dict[str, Any] | None:
    return _sdb().fetch_one(
        "select id, color, rango_fusion, rango_texto from channel.ml_revision_categoria "
        " where sku = %s and categoria_id = %s "
        f"   and creado_at >= now() - interval '{VIGENCIA_REVISION}' "
        " order by creado_at desc limit 1", (sku, categoria_id))


def revision_igual(sku: str, categoria_id: str, firma: str) -> dict[str, Any] | None:
    """La última revisión vigente con la MISMA entrada y con señal. None si no hay o si falla."""
    try:
        return _sdb().fetch_one(
            "select id, color, rango_texto, rango_fotos, rango_fusion, n_fotos, propuestas, "
            "       coalesce((detalle->>'solo_fotos')::boolean, false) as solo_fotos "
            "  from channel.ml_revision_categoria "
            " where sku = %s and categoria_id = %s and detalle->>'entrada' = %s "
            "   and color <> 'sin_senal' "
            f"   and creado_at >= now() - interval '{VIGENCIA_REVISION}' "
            " order by creado_at desc limit 1", (sku, categoria_id, firma))
    except Exception as exc:  # noqa: BLE001
        log.info("revisión de categoría (%s): no se pudo buscar una igual: %s", sku, exc)
        return None


def _anotar_decision(revision_id: int, motivo: str | None) -> None:
    _sdb().execute(
        "update channel.ml_revision_categoria "
        "   set decision = 'confirmada', motivo = nullif(%s, ''), decidido_por = %s, decidido_at = now() "
        " where id = %s",
        ((motivo or "").strip()[:1000], actor.actual() or None, revision_id))


# ───────────────────────── lo que llaman la vista previa y el alta ─────────────────────────

async def revisar(sku: str | None, wc_id: Any, categoria_id: str | None,
                  titulo: str | None, descripcion: str | None,
                  fotos: list[str], origen: str = "vista_previa") -> dict[str, Any] | None:
    """La revisión por texto y foto para la tarjeta (`origen`: vista_previa | estudio).
    None con la bandera apagada. Nunca lanza."""
    if not settings.categoria_vec_enabled:
        return None
    cid = (categoria_id or "").strip()
    base: dict[str, Any] = {"color": "sin_senal", "rango_texto": None, "rango_fotos": None,
                            "rango_fusion": None, "n_fotos": 0, "propuestas": [],
                            "candado": bool(settings.categoria_vec_candado), "motivo_sin_senal": None,
                            "solo_fotos": False, "revision_id": None, "reusada": False}
    firma = entrada(cid, titulo, descripcion, fotos)
    detalle: dict[str, Any] = {"modelos": {"texto": MODELO_TEXTO, "foto": MODELO_FOTO}, "dims": DIMS,
                               "umbrales": {"verde": VERDE_HASTA, "ambar": AMBAR_HASTA},
                               "origen": origen, "entrada": firma}
    if cid and sku and (previa := await asyncio.to_thread(revision_igual, str(sku), cid, firma)):
        # Lo mismo ya se revisó (el Estudio y luego la vista previa, o abrir dos veces): no se paga
        # otra vez ni se guarda otra fila; la decisión del alta se anota en esa misma.
        base.update({k: previa[k] for k in ("color", "rango_texto", "rango_fotos", "rango_fusion",
                                            "n_fotos", "solo_fotos")})
        base.update({"propuestas": list(previa["propuestas"] or []), "revision_id": int(previa["id"]),
                     "reusada": True})
        return base
    costo = 0.0
    try:
        if not cid:
            base["motivo_sin_senal"] = "sin categoría"
        elif not settings.openrouter_api_key:
            base["motivo_sin_senal"] = "sin clave de OpenRouter"
        elif not await asyncio.to_thread(huellas_listas):
            base["motivo_sin_senal"] = "sin huellas del árbol"
        elif (g := await asyncio.to_thread(gastado_24h)) >= settings.categoria_vec_tope_diario_usd:
            base["motivo_sin_senal"] = "tope de gasto de 24 h" if math.isfinite(g) else "gasto sin medir"
        else:
            h = await huella(titulo, descripcion, fotos)
            costo = h["costo"]
            detalle["errores"] = h["errores"]
            if h["texto"] is None and h["foto"] is None:
                base["motivo_sin_senal"] = "OpenRouter no contestó"
            else:
                # Si el texto no contestó pero las fotos sí (ya pagadas), se revisa con las fotos.
                filas = await asyncio.to_thread(rangos, cid, h["texto"], h["foto"])
                propia = next((f for f in filas if f["category_id"] == cid), None)
                hay_foto = h["foto"] is not None
                base.update({
                    "n_fotos": h["n_fotos"],
                    "rango_texto": (propia or {}).get("rt"),
                    "rango_fotos": (propia or {}).get("rf") if hay_foto else None,
                    "rango_fusion": (propia or {}).get("rz") if hay_foto else None,
                    "propuestas": propuestas(filas, cid,
                                             principal="texto_y_fotos" if h["texto"] is not None else "fotos"),
                    "solo_fotos": h["texto"] is None,
                })
                base["color"] = color(base["rango_texto"], base["rango_fusion"], hay_foto)
                if propia is None:
                    base["motivo_sin_senal"] = "la categoría no tiene huella (¿rama o nueva en ML?)"
                # numeric de Postgres llega como Decimal: a float, o el jsonb no se arma.
                detalle["similitud"] = {k: (float(v) if v is not None else None) for k, v in
                                        (("texto", (propia or {}).get("st")),
                                         ("fotos", (propia or {}).get("sf")))}
    except Exception as exc:  # noqa: BLE001
        log.warning("revisión de categoría por vectores (%s) falló: %s", sku, exc)
        base["color"] = "sin_senal"
        base["motivo_sin_senal"] = f"error: {type(exc).__name__}"
    detalle["motivo_sin_senal"] = base["motivo_sin_senal"]
    detalle["solo_fotos"] = base["solo_fotos"]
    if sku:
        base["revision_id"] = await asyncio.to_thread(_guardar, {
            "sku": str(sku), "wc_id": int(wc_id) if str(wc_id or "").isdigit() else None,
            "categoria_id": cid or None, "color": base["color"],
            "rango_texto": base["rango_texto"], "rango_fotos": base["rango_fotos"],
            "rango_fusion": base["rango_fusion"], "n_fotos": base["n_fotos"],
            "propuestas": base["propuestas"], "detalle": detalle,
            "costo_usd": round(costo, 6), "creado_por": actor.actual() or None,
        })
    return base


async def candado_alta(sku: str, categoria_id: str, motivo: str | None) -> str | None:
    """
    Al dar de alta: anota la decisión en la última revisión de ese SKU y categoría y,
    con el candado encendido, frena un ROJO sin motivo. Motivo para NO dar de alta, o None.
    Sin revisión vigente (o si la BD falla) no frena: se publica con la revisión de siempre.
    """
    if not settings.categoria_vec_enabled or not sku or not categoria_id:
        return None
    try:
        fila = await asyncio.to_thread(ultima_revision, str(sku), str(categoria_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("candado de categoría (%s): no se pudo leer la revisión: %s", sku, exc)
        return None
    bloqueo = motivo_candado(fila, motivo, bool(settings.categoria_vec_candado))
    if bloqueo:
        return bloqueo
    if fila:
        try:
            await asyncio.to_thread(_anotar_decision, int(fila["id"]), motivo)
        except Exception as exc:  # noqa: BLE001
            log.warning("candado de categoría (%s): no se pudo anotar la decisión: %s", sku, exc)
    return None
