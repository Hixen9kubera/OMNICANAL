"""
categorias_arbol.py — Búsqueda POR TEXTO en el árbol completo de categorías de
Mercado Libre (`channel.ml_category_tree`, migración 0059).

POR QUÉ EXISTE
El picker de categoría del Estudio buscaba SOLO con `domain_discovery`, que es
un PREDICTOR: contesta "si un producto se llamara así, ¿dónde lo pondría ML?",
no "¿qué categorías se llaman así?". Acierta cuando se teclea el título de un
producto y falla cuando se teclea el nombre de una categoría. Medido en
septiembre de 2026 con la regla vieja:

  · "lavabo"             → MLM31513 (Hogar › Baños › Lavabos) no sale nunca
  · "bocinas"            → MLM2868 (Audio › Bocinas) no sale; sale otra "Bocinas"
  · "otros cosmetologia" → los dos "Otros" de cosmetología no salen nunca

Ahora el picker busca en los dos lados y los mezcla (`mezclar`): primero lo
que COINCIDE con lo tecleado en el árbol, después lo que solo sugiere el
predictor.

LA REGLA DE COINCIDENCIA (así se le explica a una persona)
Salen las categorías donde Mercado Libre acepta publicar cuya ruta completa
contiene TODAS las palabras tecleadas, sin importar acentos, mayúsculas ni
plural; "de", "para", "y"… se ignoran. Primero las que traen más palabras en
su PROPIO nombre y, entre ésas, las que tienen más publicaciones.

UNA SOLA NORMALIZACIÓN
`normalizar` es la única: con ella el cargador (scripts/cargar_arbol_ml.py)
llena name_norm/path_norm y con ella se normaliza lo tecleado. Si cambia,
hay que recargar el árbol — si no, se comparan textos normalizados distinto.

SI LA TABLA NO ESTÁ
Todo lo que lee devuelve vacío y el picker se queda con el predictor, que es
exactamente el comportamiento anterior. Por eso el código puede llegar a
producción antes que la migración sin romper nada.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

log = logging.getLogger("omnicanal.categorias_arbol")

# Palabras que no distinguen una categoría de otra ("Lavabos PARA Baño").
_VACIAS = frozenset(
    "a al con de del e el en la las lo los o para por sin u un una unas unos y".split())
# Pegar un título entero no debe armar un WHERE de 20 condiciones.
_MAX_TERMINOS = 8

_aviso_dado = False


def normalizar(texto: str | None) -> str:
    """Minúsculas, sin acentos (ñ → n), solo [a-z0-9] separados por un espacio."""
    s = unicodedata.normalize("NFD", (texto or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _raiz(t: str) -> str:
    """Plural → raíz, para que 'lavabos' encuentre 'Lavabo' y 'motores' 'Motor'.

    Como la coincidencia es por SUBCADENA, la raíz sigue encontrando la forma
    plural ('soport' está dentro de 'soportes'): se pierde poco y se gana el
    singular."""
    if len(t) > 4 and t.endswith("es"):
        return t[:-2]
    if len(t) > 3 and t.endswith("s"):
        return t[:-1]
    return t


def terminos(q: str | None) -> list[str]:
    """Las palabras de búsqueda: normalizadas, sin vacías, en raíz, sin repetir."""
    salida: list[str] = []
    for t in normalizar(q).split():
        if t in _VACIAS:
            continue
        r = _raiz(t)
        if r not in salida:
            salida.append(r)
    return salida[:_MAX_TERMINOS]


def _resultado(fila: dict[str, Any]) -> dict[str, Any]:
    return {
        "category_id": fila["category_id"],
        "name": fila["name"],
        "path": " > ".join(fila["path_names"]),
        "domain": "",
        "publicable": bool(fila.get("listing_allowed", True)),
    }


def _leer(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """fetch_all que NUNCA revienta: sin base, sin tabla o con error → [].

    El aviso sale una vez por proceso: el picker consulta en cada tecleo y un
    warning por letra taparía los logs sin decir nada nuevo."""
    global _aviso_dado
    from services import supabase_db as sdb
    if not sdb.disponible():
        return []
    try:
        return sdb.fetch_all(sql, params)
    except Exception as exc:  # noqa: BLE001
        if not _aviso_dado:
            log.warning("Árbol de categorías ML no disponible (¿falta la 0059 o la "
                        "carga?); el picker sigue solo con el predictor: %s", exc)
            _aviso_dado = True
        return []


def buscar(q: str, limite: int = 8) -> list[dict[str, Any]]:
    """Categorías publicables cuya ruta contiene todas las palabras de `q`."""
    toks = terminos(q)
    if not toks:
        return []
    params: dict[str, Any] = {"lim": int(limite)}
    en_ruta: list[str] = []
    en_nombre: list[str] = []
    for i, t in enumerate(toks):
        # t es [a-z0-9]+ (sale de normalizar): no trae comodines de LIKE.
        params[f"t{i}"] = f"%{t}%"
        en_ruta.append(f"path_norm like %(t{i})s")
        en_nombre.append(f"(name_norm like %(t{i})s)::int")
    sql = ("select category_id, name, path_names, listing_allowed "
           "from channel.ml_category_tree "
           f"where listing_allowed and {' and '.join(en_ruta)} "
           f"order by ({' + '.join(en_nombre)}) desc, total_items desc nulls last, category_id "
           "limit %(lim)s")
    return [_resultado(f) for f in _leer(sql, params)]


def info(ids: list[str]) -> dict[str, dict[str, Any]]:
    """{category_id: {path, publicable}} de las que estén en el árbol."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    filas = _leer("select category_id, name, path_names, listing_allowed "
                  "from channel.ml_category_tree where category_id = any(%(ids)s)",
                  {"ids": ids})
    return {f["category_id"]: {"path": " > ".join(f["path_names"]),
                               "publicable": bool(f["listing_allowed"])} for f in filas}


def hojas_bajo(category_id: str, limite: int = 30) -> tuple[list[dict[str, Any]], int]:
    """Las categorías publicables DEBAJO de una rama, ordenadas como el árbol.

    Para cuando alguien pega el ID de una rama (un breadcrumb cortado:
    "Equipos de Cosmetología >"): ML no acepta publicar ahí, pero debajo están
    sus ocho hojas publicables. Devuelve (filas, total)."""
    filas = _leer("select category_id, name, path_names, listing_allowed, "
                  "count(*) over () as total from channel.ml_category_tree "
                  "where listing_allowed and path_ids @> array[%(cid)s]::text[] "
                  "and category_id <> %(cid)s order by path_names limit %(lim)s",
                  {"cid": category_id, "lim": int(limite)})
    return [_resultado(f) for f in filas], (int(filas[0]["total"]) if filas else 0)


def mezclar(arbol: list[dict[str, Any]], sugeridas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Primero lo que coincide en el árbol, después lo que solo sugiere ML.

    Una categoría que sale en los dos lados se queda en su lugar del árbol,
    marcada `sugerida` y con el dominio legible de ML ("Lavabos para baño")
    en vez de vacío. Pura: sin red ni base, para poder probarla."""
    por_id = {s["category_id"]: s for s in sugeridas}
    salida = []
    for a in arbol:
        s = por_id.get(a["category_id"])
        salida.append({**a, "sugerida": s is not None,
                       "domain": (s or {}).get("domain") or a.get("domain") or ""})
    ya = {a["category_id"] for a in arbol}
    salida += [{**s, "sugerida": True} for s in sugeridas if s["category_id"] not in ya]
    return salida


def filas_desde_arbol(arbol: dict[str, Any]) -> tuple[list[tuple], dict[str, int]]:
    """Del JSON de /sites/MLM/categories/all a filas de channel.ml_category_tree.

    Orden de la tupla: category_id, name, parent_id, root_id, path_ids,
    path_names, is_leaf, listing_allowed, total_items, catalog_domain,
    name_norm, path_norm. `path_from_root` de ML incluye a la propia
    categoría; si algún día no la trae, se agrega y se cuenta en
    `ruta_corregida` para que el cargador lo diga en vez de callarlo."""
    filas: list[tuple] = []
    corregidas = 0
    for clave, n in arbol.items():
        cid = n.get("id") or clave
        nombre = (n.get("name") or "").strip()
        ruta = [(p.get("id"), (p.get("name") or "").strip())
                for p in (n.get("path_from_root") or []) if p.get("id")]
        if not ruta or ruta[-1][0] != cid:
            ruta.append((cid, nombre))
            corregidas += 1
        ids = [r[0] for r in ruta]
        nombres = [r[1] for r in ruta]
        s = n.get("settings") or {}
        filas.append((
            cid, nombre, ids[-2] if len(ids) > 1 else None, ids[0], ids, nombres,
            not n.get("children_categories"), bool(s.get("listing_allowed")),
            n.get("total_items_in_this_category"), s.get("catalog_domain"),
            normalizar(nombre), normalizar(" ".join(nombres)),
        ))
    return filas, {"ruta_corregida": corregidas}
