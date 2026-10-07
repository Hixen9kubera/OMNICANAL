"""
mercado_ml.py — A cuánto vende la competencia en Mercado Libre, y en qué categoría cae cada producto.

Solo la API OFICIAL de Mercado Libre, solo GET. Tres cosas:

1. LO QUE PRODUCCIÓN YA MIDIÓ. El módulo de Competencia del panel guarda, por SKU, los
   rivales que su juez marcó como el mismo producto (`enrich.market_rival_comparable_v`).
   Es una lectura a kubera y es lo mejor que hay: son publicaciones reales del buscador.
   Cubre a los SKUs que el panel ya midió; para el resto está el punto 2.

2. EL CATÁLOGO DE ML. El buscador de publicaciones (`/sites/MLM/search`) contesta 403
   a cualquier aplicación, y la página pública pone un muro de verificación: no se toca.
   Lo que sí está abierto con el token es el buscador del CATÁLOGO
   (`/products/search`) y las ofertas de cada producto (`/products/{id}/items`): se
   busca el término, se leen los diez primeros productos con su precio, y el juez
   separa los que son el mismo producto. Es menos que el buscador (solo ve lo que
   está en catálogo), y por eso cada precio dice de cuál de las dos fuentes salió.

3. LA CATEGORÍA. `domain_discovery/search` es el predictor con el que el propio panel
   categoriza al crear un producto: se le da el título y contesta la categoría. Se usa
   para lo que no tiene categoría confiable (la estimada por el prefijo del SKU, y los
   renglones de packing list sin SKU).

El token se LEE de kubera y nunca se renueva (renovarlo rota el de producción). Si
caduca a media corrida se vuelve a leer una vez; si tampoco sirve, la etapa se
detiene y lo dice: se reanuda donde se quedó.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import tokens_kubera
from comun import Cfg, Red, ahora_iso, aviso, escribir_json, leer_json, sku_norm
from ia_titulos import DeepSeek
from mercado_comun import (cargar, estadistica, firma, grupos_de_busqueda, juzgar, peso_grupo, pesos,
                           resumir)

API = "https://api.mercadolibre.com"
SITIO = "MLM"
RIVALES = 8
HILOS = 3
_CONSULTA = """
    select sku::text as sku, precio::float as precio
      from enrich.market_rival_comparable_v
     where comparable
"""


class Token:
    """El token de ML, leído de kubera. Se relee UNA vez por caída; nunca se renueva."""

    def __init__(self, cfg: Cfg):
        self.cfg, self._lock, self._relecturas = cfg, threading.Lock(), 0
        self.valor = self._leer()

    def _leer(self) -> str:
        cuentas = tokens_kubera.mercado_libre(self.cfg)
        tok = next((v["acceso"] for v in cuentas.values() if v.get("acceso")), None)
        if not tok:
            raise RuntimeError("no hay token de Mercado Libre legible en kubera")
        return tok

    def cabecera(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.valor}"}

    def caducado(self, usado: str) -> bool:
        """True si se pudo leer uno nuevo y vale la pena reintentar."""
        with self._lock:
            if self.valor != usado:
                return True                      # otro hilo ya lo releyó
            if self._relecturas >= 6:
                return False
            self._relecturas += 1
            nuevo = self._leer()
            cambio = nuevo != self.valor
            self.valor = nuevo
            return cambio


def _get(red: Red, tok: Token, ruta: str, **params: Any) -> Any:
    for _ in range(2):
        usado = tok.valor
        r = red.get(API + ruta, params=params, headers={"Authorization": f"Bearer {usado}"})
        if r.status_code == 401 and tok.caducado(usado):
            continue
        return r
    return r


def _medido_por_produccion(cfg: Cfg) -> dict[str, dict[str, float]]:
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(cfg("SUPABASE_DB_URL"), connect_timeout=25)
    try:
        cn.autocommit = True
        with cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_CONSULTA)
            filas = cur.fetchall()
    finally:
        cn.close()
    por_sku: dict[str, list[float]] = {}
    for f in filas:
        if f["precio"] and f["precio"] > 0:
            por_sku.setdefault(sku_norm(f["sku"]), []).append(f["precio"])
    return {s: estadistica(p) for s, p in por_sku.items()}


def _buscar(red: Red, tok: Token, q: str) -> list[dict[str, Any]] | None:
    r = _get(red, tok, "/products/search", status="active", site_id=SITIO, q=q, limit=RIVALES + 4)
    if r.status_code == 401:
        raise PermissionError("el token de Mercado Libre caducó y kubera todavía no tiene uno nuevo")
    if r.status_code != 200:
        return None
    rivales = []
    for p in (r.json().get("results") or [])[:RIVALES + 4]:
        o = _get(red, tok, f"/products/{p['id']}/items", limit=3)
        if o.status_code != 200:
            continue                               # sin ofertas activas: no hay precio que leer
        ofertas = [x for x in (o.json().get("results") or []) if x.get("price")]
        if not ofertas:
            continue
        rivales.append({"id": p["id"], "t": p.get("name") or "", "p": float(ofertas[0]["price"])})
        if len(rivales) >= RIVALES:
            break
    return rivales


def extraer(cfg: Cfg, salida: Path, limite: int = 0) -> dict[str, Any]:
    ruta = salida / "datos" / "mercado_ml.json"
    doc = cargar(ruta)
    doc.setdefault("busquedas", {})
    try:
        doc["produccion"] = _medido_por_produccion(cfg)
        doc["produccion_leido"] = ahora_iso()
    except Exception as exc:  # noqa: BLE001
        aviso(f"mercado ML: no se pudo leer lo medido por producción ({type(exc).__name__}: {str(exc)[:120]})")
        doc.setdefault("produccion", {})
    grupos, de_sku = grupos_de_busqueda(salida)
    doc["sku"] = de_sku
    ya = doc["produccion"]
    # Primero los grupos donde NINGÚN SKU tiene ya precio medido por producción.
    pendientes = [(c, g) for c, g in grupos.items()
                  if (doc["grupos"].get(c) or {}).get("h") != firma(g)
                  and not all(sku_norm(s) in ya for s in g["skus"])]
    w = pesos(salida)
    pendientes.sort(key=lambda par: -peso_grupo(par[1], w))     # primero lo que más piezas tiene detrás
    if limite:
        pendientes = pendientes[:limite]
    aviso(f"mercado ML: {len(ya)} SKUs ya medidos por producción · {len(grupos)} grupos de búsqueda · "
          f"{len(pendientes)} por buscar")
    # Despacio a propósito: el token es el de la aplicación de producción y su cuota es compartida.
    tok, red, ia = Token(cfg), Red(rps=4.0, timeout=40.0), DeepSeek(cfg)
    candado = threading.Lock()
    estado = {"hechos": 0, "alto": ""}

    def guardar() -> None:
        doc["actualizado"], doc["uso_ia"], doc["costo_ia_usd"] = ahora_iso(), ia.uso, ia.costo_usd()
        escribir_json(ruta, doc)

    def uno(par: tuple[str, dict[str, Any]]) -> None:
        clave, g = par
        if estado["alto"]:
            return
        with candado:
            rivales = doc["busquedas"].get(g["q"])
        if rivales is None:
            try:
                rivales = _buscar(red, tok, g["q"])
            except PermissionError as exc:
                estado["alto"] = str(exc)
                return
            if rivales is None:
                return                             # la búsqueda falló: se reintenta en otra corrida
            with candado:
                doc["busquedas"][g["q"]] = rivales
        veredictos = juzgar(ia, g["t"], g["u"], rivales) if rivales else []
        if rivales and not veredictos:
            return
        fila = resumir(g["u"], rivales, veredictos)
        fila.update({"q": g["q"], "h": firma(g)})
        with candado:
            doc["grupos"][clave] = fila
            estado["hechos"] += 1
            if estado["hechos"] % 100 == 0:
                guardar()
                con = sum(1 for x in doc["grupos"].values() if x.get("n"))
                aviso(f"mercado ML: {estado['hechos']}/{len(pendientes)} grupos · {con} con precio · "
                      f"{red.cuenta.get('api.mercadolibre.com', 0):,} lecturas · IA ≈ ${ia.costo_usd():.3f}")

    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        list(pool.map(uno, pendientes))
    guardar()
    if estado["alto"]:
        aviso(f"mercado ML: DETENIDO — {estado['alto']}. Vuelve a correr la etapa: sigue donde se quedó.")
    con = sum(1 for x in doc["grupos"].values() if x.get("n"))
    return {"skus_medidos_por_produccion": len(ya), "grupos": len(grupos), "grupos_buscados": len(doc["grupos"]),
            "grupos_con_precio": con, "detenido": estado["alto"] or None,
            "lecturas_ml": red.cuenta.get("api.mercadolibre.com", 0), "costo_ia_usd": ia.costo_usd()}


# ── La categoría, con el predictor de ML ──────────────────────────────────────

def predecir_categorias(cfg: Cfg, salida: Path, limite: int = 0) -> dict[str, Any]:
    titulos = (leer_json(salida / "datos" / "titulos.json") or {}).get("skus") or {}
    cat = leer_json(salida / "datos.json") or {"P": []}
    confiable = {sku_norm(r["s"]) for r in cat["P"] if "k" in r and r.get("kq") != "p"}
    arbol = leer_json(salida / "cache" / "ml_arbol.json") or {}
    ruta = salida / "datos" / "categorias_pred.json"
    doc = leer_json(ruta) or {"por_titulo": {}}
    hechos: dict[str, Any] = doc["por_titulo"]
    faltan: dict[str, str] = {}                    # título normalizado → título
    for sku, x in titulos.items():
        if sku_norm(sku) in confiable or not x.get("t"):
            continue
        llave = re.sub(r"\s+", " ", x["t"]).strip().lower()
        if llave not in hechos:
            faltan[llave] = x["t"]
    pendientes = list(faltan.items())[:limite] if limite else list(faltan.items())
    aviso(f"categorías: {len(hechos)} títulos ya resueltos · {len(pendientes)} por preguntar al predictor de ML")
    tok, red = Token(cfg), Red(rps=4.0, timeout=30.0)
    candado = threading.Lock()
    cuenta = {"n": 0}

    def uno(par: tuple[str, str]) -> None:
        llave, titulo = par
        r = _get(red, tok, f"/sites/{SITIO}/domain_discovery/search", limit=3, q=titulo)
        if r.status_code != 200:
            return
        res = r.json() or []
        fila: dict[str, Any] = {}
        if res:
            cid = res[0].get("category_id")
            nodo = arbol.get(cid) or {}
            camino = [str(p.get("name") or "").strip() for p in nodo.get("path_from_root") or []]
            fila = {"id": cid, "ruta": camino or [res[0].get("category_name") or ""]}
        with candado:
            hechos[llave] = fila
            cuenta["n"] += 1
            if cuenta["n"] % 500 == 0:
                escribir_json(ruta, doc)
                aviso(f"categorías: {cuenta['n']}/{len(pendientes)}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(uno, pendientes))
    doc["actualizado"] = ahora_iso()
    escribir_json(ruta, doc)
    return {"titulos_resueltos": len(hechos), "con_categoria": sum(1 for v in hechos.values() if v.get("id")),
            "sin_respuesta": sum(1 for v in hechos.values() if not v.get("id"))}
