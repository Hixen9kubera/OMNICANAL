"""
f_categorias.py — A qué CATEGORÍA DE MERCADO pertenece cada producto.

El panel ya decidió la categoría de Mercado Libre de cada producto trabajado, y la
dejó en dos lugares:

  · en la propia publicación de ML (`category_id`, que ya trae `ml.json`);
  · en WooCommerce, como una categoría cuyo NOMBRE es el de la hoja de ML y cuya
    DESCRIPCIÓN dice `ML: MLM123456` (así las crea `crear_producto.py`).

OJO: las categorías de WooCommerce NO forman árbol. Son ~1,700 hojas sueltas, todas
en la raíz. Agrupar por ellas no sirve para contestar «qué tenemos». Lo que sí
sirve es el árbol de Mercado Libre: 31 raíces («Hogar, Muebles y Jardín», «Ropa,
Bolsas y Calzado»…) y debajo sus ramas.

Esta etapa resuelve cada id de ML a su ruta completa y deja `datos/categorias.json`:

    { "MLM189363": ["Belleza y Cuidado Personal", "Artículos de Peluquería", …] }

De dónde sale la ruta, en orden:
  1. `cache/ml_arbol.json` — el árbol entero (`/sites/MLM/categories/all`, un solo
     GET de ~30 MB que pide token). Si alguien ya lo bajó, se usa.
  2. `GET /categories/{id}` — PÚBLICO, sin token, uno por categoría. Se cachea.
"""
from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path
from typing import Any

from comun import Cfg, Red, ahora_iso, aviso, escribir_json, leer_json

RE_ML = re.compile(r"\bMLM\d+\b")
# Raíces de ML que no son mercancía: un producto nunca cae ahí, aunque el nombre
# coincida («Tecnología» existe en ML… dentro de Servicios › Servicios de Reparación).
RAICES_QUE_NO_SON_PRODUCTO = {"Servicios", "Inmuebles", "Autos, Motos y Otros"}
ALIAS_DE_RAIZ = {"Mascotas": "Animales y Mascotas"}


def _norm(texto: str | None) -> str:
    plano = unicodedata.normalize("NFD", (texto or "").strip().lower())
    return "".join(c for c in plano if unicodedata.category(c) != "Mn")


def _mapa_woo(cfg: Cfg) -> dict[str, str]:
    """{nombre de la categoría en WooCommerce: id de ML}, leyendo su descripción."""
    base = cfg("WC_URL").rstrip("/") + "/wp-json/wc/v3"
    auth = (cfg("WC_CONSUMER_KEY") or cfg("WC_KEY"), cfg("WC_CONSUMER_SECRET") or cfg("WC_SECRET"))
    red = Red(rps=2.0, timeout=60.0)
    mapa: dict[str, str] = {}
    pagina = 1
    while True:
        r = red.get(base + "/products/categories", auth=auth,
                    params={"per_page": 100, "page": pagina, "_fields": "id,name,description",
                            "_cb": str(time.time())},
                    headers={"User-Agent": "Mozilla/5.0 (catalogo-vivo; solo lectura)"})
        if r.status_code != 200:
            raise RuntimeError(f"WooCommerce categorías: HTTP {r.status_code}")
        lote = r.json()
        for c in lote:
            m = RE_ML.search(c.get("description") or "") or RE_ML.search(c.get("name") or "")
            if m and c.get("name"):
                mapa[c["name"]] = m.group(0)
        if pagina >= int(r.headers.get("x-wp-totalpages") or 1) or not lote:
            break
        pagina += 1
    return mapa


def extraer(cfg: Cfg, salida: Path) -> dict[str, Any]:
    datos = salida / "datos"
    woo = leer_json(datos / "woo.json") or {}
    if woo and not woo.get("categoria_ml"):
        woo["categoria_ml"] = _mapa_woo(cfg)
        escribir_json(datos / "woo.json", woo)
        aviso(f"categorías: {len(woo['categoria_ml'])} categorías de WooCommerce con su id de ML")
    mapa_woo: dict[str, str] = woo.get("categoria_ml") or {}

    # Qué ids hacen falta: los de las publicaciones de ML y los de WooCommerce
    necesarios: set[str] = set(mapa_woo.values())
    for f in (leer_json(datos / "ml.json") or {}).get("filas") or []:
        if f.get("categoria"):
            necesarios.add(f["categoria"])

    rutas: dict[str, list[str]] = leer_json(salida / "cache" / "ml_categorias.json") or {}
    arbol = leer_json(salida / "cache" / "ml_arbol.json")

    # Cuatro de cada diez categorías de WooCommerce NO traen el id de ML en la
    # descripción (las creó otro flujo). Su nombre sí es el de una categoría de ML,
    # pero casi siempre AMBIGUO: «Tenis» existe siete veces en el árbol (el calzado y
    # el deporte). Aquí solo se dejan las CANDIDATAS, de la más poblada a la menos;
    # cuál es la buena lo decide `pagina.py`, que conoce el producto.
    if arbol and woo:
        por_nombre: dict[str, list[str]] = {}
        for cid, nodo in arbol.items():
            raiz = ((nodo.get("path_from_root") or [{}])[0]).get("name")
            if raiz not in RAICES_QUE_NO_SON_PRODUCTO:
                por_nombre.setdefault(_norm(nodo.get("name")), []).append(cid)
        usados = {n for f in (woo.get("filas") or {}).values()
                  for n in (f.get("cats") or f.get("ruta") or [])}
        candidatas: dict[str, list[str]] = {}
        for nombre in usados - set(mapa_woo):
            ids = por_nombre.get(_norm(nombre)) or []
            if ids:
                ids.sort(key=lambda i: -(arbol[i].get("total_items_in_this_category") or 0))
                candidatas[nombre] = ids[:12]
                necesarios.update(ids[:12])
        woo["categoria_candidatas"] = candidatas
        escribir_json(datos / "woo.json", woo)
        aviso(f"categorías: {len(candidatas)} categorías de WooCommerce ubicadas por NOMBRE en el árbol de ML")
    if arbol:
        # El nombre de cada eslabón se toma del NODO vigente y sin espacios sobrantes:
        # ML trae «Deportes y Fitness » con un espacio al final, y la misma raíz salía
        # partida en dos renglones.
        def _nombre(eslabon: dict[str, Any]) -> str:
            return str((arbol.get(eslabon.get("id")) or eslabon).get("name") or "").strip()

        for cid in necesarios:
            nodo = arbol.get(cid)
            if nodo:
                rutas[cid] = [_nombre(p) for p in nodo.get("path_from_root") or []]
    faltan = sorted(necesarios - set(rutas))
    if faltan:
        aviso(f"categorías: {len(faltan)} rutas por resolver en la API pública de ML")
        red = Red(rps=4.0, timeout=30.0)
        for n, cid in enumerate(faltan, 1):
            r = red.get(f"https://api.mercadolibre.com/categories/{cid}")
            if r.status_code == 200:
                rutas[cid] = [str(p.get("name") or "").strip()
                              for p in r.json().get("path_from_root") or []]
            if n % 200 == 0:
                aviso(f"categorías: {n}/{len(faltan)}")
    # La raíz MLM1071 se llama «Mascotas» en el árbol y «Animales y Mascotas» en la
    # lista de raíces de la misma API. Se deja el nombre que ML enseña al público.
    for ruta in rutas.values():
        if ruta and ruta[0] in ALIAS_DE_RAIZ:
            ruta[0] = ALIAS_DE_RAIZ[ruta[0]]
    escribir_json(salida / "cache" / "ml_categorias.json", rutas)
    utiles = {cid: rutas[cid] for cid in necesarios if rutas.get(cid)}
    escribir_json(datos / "categorias.json", utiles)
    raices = sorted({r[0] for r in utiles.values() if r})
    aviso(f"categorías: {len(utiles)} de {len(necesarios)} resueltas · {len(raices)} raíces")
    return {"leido": ahora_iso(), "resueltas": len(utiles), "pedidas": len(necesarios),
            "sin_resolver": len(necesarios) - len(utiles), "raices": raices}
