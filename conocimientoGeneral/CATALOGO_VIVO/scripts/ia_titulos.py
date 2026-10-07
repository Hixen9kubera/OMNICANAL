"""
ia_titulos.py — Un título de Mercado Libre, un término de búsqueda y las unidades por
paquete para CADA SKU, con DeepSeek.

Para qué
--------
Sin un título que diga qué es el producto no hay con qué buscar a la competencia. En
Odoo conviven «Lámpara de escritorio LED recargable» con «A bulb», «CALZADO» y
nombres en inglés copiados del packing list. Esta etapa los pone todos en el mismo
idioma, y de paso saca lo que piden las dos etapas de mercado:

  · `t`  título al estilo de Mercado Libre México (máx. 60 caracteres);
  · `q`  término general de búsqueda (2 a 4 palabras, sin marca ni color ni medidas);
  · `u`  cuántos productos completos trae NUESTRO paquete (para no comparar el
         precio de un paquete de 12 contra el de una pieza);
  · `c`  la categoría de Mercado Libre que la IA cree más probable (segunda opinión:
         la categoría oficial la da el predictor de ML en `mercado_ml`).

De dónde salen las instrucciones
--------------------------------
Son las de producción, juntadas en un solo mensaje (v0.621.0):
  · el título, de `ia_generadores._ML_TITULO` y `_MEJORAR["mercado_libre"]`
    (60 caracteres, palabras clave al inicio, sin signos promocionales, y «el
    título actual define QUÉ ES el producto»);
  · el término, de `competencia_terminos._SYSTEM`;
  · las unidades, de la regla del juez de Competencia (`competencia_juez`).

Tres casos
----------
  1. Publicado en Mercado Libre: su título ya es el de ML. Se conserva TAL CUAL
     (`f: "ml"`) y a la IA solo se le piden término y unidades.
  2. No publicado, con nombre usable: la IA mejora el título (`f: "ia"`).
  3. No publicado, sin categoría confiable y con un nombre que no describe: se le
     manda la FOTO (`f: "foto"`). Sin foto, se queda con el título que tenía
     (`f: "actual"`), mejorado solo en forma.

Es reanudable: `datos/titulos.json` se va guardando y lo ya hecho no se vuelve a pedir.
El modelo es `deepseek-flash` con el razonamiento apagado: con él encendido contesta
lo mismo, tarda el triple y cobra el razonamiento como salida.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import ml_contenedores
from comun import Cfg, Red, ahora_iso, aviso, escribir_json, leer_json, sku_norm

URL = "https://api.deepseek.com/chat/completions"
MODELO = "deepseek-flash"
VERSION = "t1"
LOTE = 12
HILOS = 8
LARGO_TITULO = 60

SISTEMA = """Eres experto en publicaciones de Mercado Libre México.
Recibes una lista de productos de un importador. Para CADA uno devuelves:

1. "titulo": un TÍTULO de máximo 60 caracteres (cuéntalos: es el límite de Mercado Libre y es estricto), 100% en español mexicano, con las palabras clave más buscadas al inicio, con palabras cotidianas con las que la gente busca (tenis / lentes / chamarra), sin signos promocionales ni datos de contacto, sin mayúsculas excesivas, sin emojis. El nombre actual define QUÉ ES el producto: no cambies el tipo de producto y no inventes medidas, piezas, color ni material que no vengan en los datos. Si el nombre viene en inglés o en chino, tradúcelo. Si los datos no alcanzan para saber qué es, devuelve el nombre actual corregido en forma y pon "seguro": false.
2. "termino": el TÉRMINO GENERAL de búsqueda: las 2 a 4 palabras que teclearía un comprador que NO conoce la marca ni el modelo. Sin marca, sin modelo, sin medidas, sin color, sin código.
3. "unidades": cuántos productos COMPLETOS trae el paquete que se vende (1 si no dice; «par» o «2 pzs» del mismo artículo = 2; «paquete de 12» = 12). Las piezas de un juego que se usa completo (un juego de cubiertos de 24 piezas, un set de brocas) NO son unidades: eso es 1.
4. "categoria": el nombre de la categoría de Mercado Libre México donde se publicaría (la hoja, por ejemplo «Sábanas» o «Organizadores de Maquillaje»).
5. "seguro": true si con los datos se sabe con certeza qué producto es; false si lo estás suponiendo.

Si un producto trae "titulo_ml", ese ya es su título publicado: repítelo tal cual en "titulo" y solo resuelve lo demás.

Respondes SOLO un objeto JSON, con un elemento por CADA sku recibido, sin saltarte ninguno:
{"productos": [{"sku": "...", "titulo": "...", "termino": "...", "unidades": 1, "categoria": "...", "seguro": true}]}"""

SISTEMA_FOTO = SISTEMA + """

En este mensaje cada producto viene con su FOTO, en el mismo orden de la lista. El nombre actual no describe el producto: guíate por la foto para decir qué es, y usa el nombre solo como pista."""

RE_CJK = re.compile(r"[㐀-鿿]")
GENERICOS = {"calzado", "ropa", "zapatos", "tenis", "bolsa", "bolsas", "juguete", "juguetes", "varios",
             "producto", "accesorios", "accesorio", "herramienta", "herramientas", "articulo", "artículo"}
INGLES = re.compile(r"\b(the|with|for|and|set|pcs|holder|kit|light|lamp|box|bag|toy|shoes?|cover|rack|"
                    r"stand|bottle|ribbon|bulb|cable|case|tool|brush|mat|pad)\b", re.I)


def clave_renglon(titulo: str) -> str:
    """La llave de un renglón sin SKU: `PL:` + huella de su nombre. Mismo nombre, misma llave."""
    t = re.sub(r"\s+", " ", (titulo or "")).strip().lower()
    return "PL:" + hashlib.sha1(t.encode()).hexdigest()[:10].upper() if t else ""


def nombre_pobre(nombre: str) -> bool:
    """¿Este nombre NO alcanza para saber qué es el producto?"""
    n = re.sub(r"\s+", " ", (nombre or "")).strip()
    if len(n) < 8 or len(n.split()) < 2:
        return True
    if RE_CJK.search(n):
        return True
    if n.lower() in GENERICOS:
        return True
    palabras = n.split()
    # todo en inglés y corto («A bulb», «Satin ribbon», «Glass Vase Set»)
    return len(palabras) <= 4 and bool(INGLES.search(n)) and not re.search(r"[áéíóúñ]", n.lower())


def _recortar(titulo: str) -> tuple[str, bool]:
    t = re.sub(r"\s+", " ", (titulo or "")).strip().strip('"«»')
    if len(t) <= LARGO_TITULO:
        return t, False
    corto = t[:LARGO_TITULO + 1]
    corto = corto[:corto.rfind(" ")] if " " in corto[20:] else t[:LARGO_TITULO]
    return corto.rstrip(" ,;-/"), True


def _firma(p: dict[str, Any]) -> str:
    base = "|".join(str(p.get(k) or "") for k in ("nombre", "titulo_ml", "titulo_pl", "categoria", "con_foto"))
    return hashlib.sha1((VERSION + base).encode()).hexdigest()[:10]


class DeepSeek:
    def __init__(self, cfg: Cfg):
        self.llave = cfg("DEEPSEEK_API_KEY")
        if not self.llave:
            raise RuntimeError("falta DEEPSEEK_API_KEY (ponla en scripts/.env, que no se sube)")
        self.red = Red(rps=12.0, timeout=120.0)
        self.uso = {"entrada": 0, "entrada_cache": 0, "salida": 0, "llamadas": 0}
        self._lock = threading.Lock()

    def json(self, sistema: str, usuario: Any, max_tokens: int, temperatura: float = 0.3) -> dict[str, Any]:
        r = self.red.lectura_sin_get(URL, headers={"Authorization": f"Bearer {self.llave}"}, json={
            "model": MODELO, "temperature": temperatura, "max_tokens": max_tokens,
            "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]})
        if r.status_code != 200:
            raise RuntimeError(f"DeepSeek HTTP {r.status_code}: {r.text[:200]}")
        cuerpo = r.json()
        u = cuerpo.get("usage") or {}
        with self._lock:
            self.uso["llamadas"] += 1
            self.uso["entrada"] += u.get("prompt_cache_miss_tokens", u.get("prompt_tokens", 0)) or 0
            self.uso["entrada_cache"] += u.get("prompt_cache_hit_tokens", 0) or 0
            self.uso["salida"] += u.get("completion_tokens", 0) or 0
        texto = ((cuerpo.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        try:
            return json.loads(texto)
        except ValueError:
            m = re.search(r"\{.*\}", texto, re.S)
            return json.loads(m.group(0)) if m else {}

    def costo_usd(self) -> float:
        # Tarifa fuera de hora pico de deepseek-flash (7-oct-2026), USD por millón de tokens.
        u = self.uso
        return round(u["entrada"] * 0.15 / 1e6 + u["entrada_cache"] * 0.003 / 1e6 + u["salida"] * 0.60 / 1e6, 4)


def _renglon(p: dict[str, Any]) -> dict[str, Any]:
    r: dict[str, Any] = {"sku": p["sku"], "nombre": p["nombre"]}
    for k in ("titulo_ml", "titulo_pl", "categoria"):
        if p.get(k):
            r[k] = p[k]
    return r


def _anotar(salida: dict[str, Any], p: dict[str, Any], x: dict[str, Any], fuente: str) -> None:
    titulo, recortado = _recortar(p["titulo_ml"] if p.get("titulo_ml") else (x.get("titulo") or p["nombre"]))
    try:
        unidades = max(1, int(float(x.get("unidades") or 1)))
    except (TypeError, ValueError):
        unidades = 1
    fila = {"t": titulo, "q": re.sub(r"\s+", " ", str(x.get("termino") or "")).strip().lower()[:60],
            "u": unidades, "c": str(x.get("categoria") or "").strip()[:80],
            "f": "ml" if p.get("titulo_ml") else fuente, "h": p["_firma"]}
    if x.get("seguro") is False:
        fila["d"] = 1                      # la IA avisó que está suponiendo
    if recortado:
        fila["r"] = 1
    salida[p["sku"]] = fila


def generar(cfg: Cfg, salida: Path, limite: int = 0) -> dict[str, Any]:
    cat = leer_json(salida / "datos.json")
    odoo = leer_json(salida / "datos" / "odoo.json")
    if not cat or not odoo:
        raise RuntimeError("faltan datos.json / datos/odoo.json: corre antes `odoo` y `pagina`")
    pl = leer_json(salida / "datos" / "pl.json") or {"validados": []}
    rutas = cat["K"]
    odoo_id = {sku_norm(f["sku"]): f["id"] for f in odoo["filas"] if f.get("foto")}
    titulo_pl: dict[str, str] = {}
    for v in pl["validados"]:
        for f in v["filas"]:
            if f["sku"] and f["titulo"] and not RE_CJK.search(f["titulo"]):
                titulo_pl.setdefault(f["sku"], f["titulo"][:100])

    productos: dict[str, dict[str, Any]] = {}
    for r in cat["P"]:
        sku = sku_norm(r["s"])
        if sku in productos:
            continue
        p: dict[str, Any] = {"sku": sku, "nombre": (r.get("n") or "").strip()[:140]}
        for ev in r.get("pe") or []:
            if str(ev[0]).startswith("Mercado Libre") and len(ev) > 3 and ev[3]:
                p["titulo_ml"] = ev[3][:120]
                break
        if "k" in r and r.get("kq") != "p":
            p["categoria"] = " › ".join(x for x in rutas[r["k"]] if x)
        if titulo_pl.get(sku):
            p["titulo_pl"] = titulo_pl[sku]
        confiable = "k" in r and r.get("kq") != "p"
        if not p.get("titulo_ml") and not confiable and nombre_pobre(p["nombre"]) and sku in odoo_id:
            p["con_foto"] = odoo_id[sku]
        productos[sku] = p
    # SKUs que bodega anotó en un packing list y Odoo no conoce: también llevan título.
    for sku, t in titulo_pl.items():
        productos.setdefault(sku, {"sku": sku, "nombre": t, "titulo_pl": t})
    inv = leer_json(salida / "datos" / "inventario_pl.json") or {}
    # SKUs que bodega anotó en un packing list y que NO tienen nombre en ningún sistema (Odoo no los
    # conoce y su renglón del validado viene vacío o en chino). Se titulan con lo que se sepa del
    # renglón, de lo más firme a lo menos: la revisión a mano y el nombre traducido del archivo de
    # precios de Eduardo (si está), el texto del renglón tal cual, y si no hay texto, su foto.
    indice = leer_json(salida / "datos" / "pl_indice.json") or {}
    edu = ml_contenedores.cargar(salida / "datos" / "eduardo_ml", indice)
    fila_pl: dict[tuple, dict[str, Any]] = {}
    for tipo, lista in (("o", pl.get("originales") or []), ("v", pl.get("validados") or [])):
        for a in lista:
            for f in a["filas"]:
                fila_pl[(tipo, a["id"], f["fila"] + 1)] = f
    cache_pl = Path(indice.get("cache") or "")
    for sku, x in (inv.get("skus") or {}).items():
        if sku in productos or "pl" not in x:
            continue
        textos: list[str] = []

        def suma(texto: Any) -> None:
            limpio = re.sub(r"\s+", " ", str(texto or "")).strip()
            if limpio and limpio.upper() != sku and limpio not in textos:
                textos.append(limpio)

        if edu:
            suma((edu["revisado"].get(sku) or {}).get("que_es"))
            nombre = ((edu["precios"].get(sku) or {}).get("nombre") or "").strip()
            if nombre and not re.fullmatch(r"[A-Z0-9/\- .#]+", nombre):      # no un código repetido
                suma(nombre)
            for ln in (edu["por_sku"].get(sku) or [])[:2]:
                suma(ln["desc"])
        foto = None
        for ren in x.get("ren") or []:
            lugares = [("v", ren.get("av"), ren.get("fv"))] if ren.get("av") is not None else []
            lugares += [("o", ren.get("ao"), fo) for fo in ren.get("fo") or []]
            for tipo, arch, fila in lugares:
                f = fila_pl.get((tipo, arch, fila))
                if not f:
                    continue
                suma(f.get("titulo"))
                suma(f.get("titulo_chn"))
                jpg = cache_pl / "thumbs" / f"{tipo}_{arch}_{fila - 1}.jpg"
                if foto is None and jpg.exists():
                    foto = str(jpg)
        if not textos and not foto:
            continue                                   # ni texto ni foto: no hay con qué
        p = {"sku": sku, "nombre": " / ".join(textos)[:220] or sku}
        if foto and not textos:
            p["foto_pl"] = foto
        productos[sku] = p
    # Renglones de packing list a los que NADIE les puso SKU (contenedores que bodega no
    # validó): también son mercancía comprada y también hay que saber qué son. Van por
    # TÍTULO, no por renglón: el mismo nombre repetido en veinte renglones se pide una vez.
    for r in inv.get("renglones_sin_sku") or []:
        clave = clave_renglon(r.get("t") or "")
        if clave:
            productos.setdefault(clave, {"sku": clave, "nombre": r["t"][:140]})
    for p in productos.values():
        p["_firma"] = _firma(p)

    ruta = salida / "datos" / "titulos.json"
    doc = leer_json(ruta) or {}
    hechos: dict[str, Any] = doc.get("skus") or {}
    pendientes = [p for p in productos.values() if (hechos.get(p["sku"]) or {}).get("h") != p["_firma"]]
    if limite:
        pendientes = pendientes[:limite]
    con_foto = [p for p in pendientes if p.get("con_foto") or p.get("foto_pl")]
    de_texto = [p for p in pendientes if not (p.get("con_foto") or p.get("foto_pl"))]
    aviso(f"títulos: {len(productos)} SKUs · ya hechos {len(productos) - len(pendientes)} · "
          f"por hacer {len(de_texto)} con texto y {len(con_foto)} con foto")
    ia = DeepSeek(cfg)
    candado = threading.Lock()
    fallas: list[str] = []
    cuenta = {"lotes": 0}

    def guardar() -> None:
        escribir_json(ruta, {"modelo": MODELO, "version": VERSION, "actualizado": ahora_iso(),
                             "uso": ia.uso, "costo_usd_estimado": ia.costo_usd(), "skus": hechos})

    def resolver(lote: list[dict[str, Any]], sistema: str, usuario: Any, fuente: str, intento: int = 0) -> None:
        try:
            res = ia.json(sistema, usuario, max_tokens=250 + 110 * len(lote))
        except Exception as exc:  # noqa: BLE001
            res = {}
            if intento:
                fallas.append(f"{lote[0]['sku']}: {str(exc)[:120]}")
        por_sku = {sku_norm(x.get("sku")): x for x in (res.get("productos") or []) if isinstance(x, dict)}
        faltan = []
        with candado:
            for p in lote:
                x = por_sku.get(p["sku"])
                if x and (x.get("titulo") or p.get("titulo_ml")):
                    _anotar(hechos, p, x, fuente)
                else:
                    faltan.append(p)
            cuenta["lotes"] += 1
            if cuenta["lotes"] % 25 == 0:
                guardar()
                aviso(f"títulos: {len(hechos)} listos · {ia.uso['llamadas']} llamadas · ≈ ${ia.costo_usd():.3f} USD")
        if faltan and not intento:                  # los que la IA se saltó: de uno en uno, una vez
            for p in faltan:
                if fuente == "foto":
                    foto_uno(p, 1)
                else:
                    resolver([p], SISTEMA, json.dumps({"productos": [_renglon(p)]}, ensure_ascii=False), fuente, 1)
        elif faltan:
            with candado:
                for p in faltan:                    # no hubo forma: se queda con lo que tenía
                    _anotar(hechos, p, {"titulo": p["nombre"]}, "actual")

    def lote_texto(lote: list[dict[str, Any]]) -> None:
        resolver(lote, SISTEMA, json.dumps({"productos": [_renglon(p) for p in lote]}, ensure_ascii=False), "ia")

    carpeta_fotos = salida / "cache" / "img" / "odoo"

    def foto_uno(p: dict[str, Any], intento: int = 0) -> None:
        if p.get("foto_pl"):                         # la foto del renglón del packing list
            de_pl = Path(p["foto_pl"])
            datos = de_pl.read_bytes() if de_pl.exists() else None
        else:
            jpg = carpeta_fotos / f"{p['con_foto']}.jpg"
            grande = salida / "cache" / "img" / "odoo256" / f"{p['con_foto']}.jpg"
            datos = grande.read_bytes() if grande.exists() else (jpg.read_bytes() if jpg.exists() else None)
        if datos is None:
            resolver([p], SISTEMA, json.dumps({"productos": [_renglon(p)]}, ensure_ascii=False), "actual", 1)
            return
        usuario = [{"type": "text", "text": json.dumps({"productos": [_renglon(p)]}, ensure_ascii=False)},
                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(datos).decode()}}]
        resolver([p], SISTEMA_FOTO, usuario, "foto", intento or 1)

    # Para la foto se pide a Odoo la de 256 px: con la miniatura de 72 no se distingue un modelo de otro.
    if con_foto:
        from f_odoo import Odoo

        destino = salida / "cache" / "img" / "odoo256"
        destino.mkdir(parents=True, exist_ok=True)
        faltantes = [p["con_foto"] for p in con_foto
                     if p.get("con_foto") and not (destino / f"{p['con_foto']}.jpg").exists()]
        if faltantes:
            o = Odoo(cfg)
            for i in range(0, len(faltantes), 50):
                for f in o.leer("product.product", [["id", "in", faltantes[i:i + 50]]], ["image_256"],
                                context={"active_test": False}):
                    if f.get("image_256"):
                        (destino / f"{f['id']}.jpg").write_bytes(base64.b64decode(f["image_256"]))
            aviso(f"títulos: {len(faltantes)} fotos de 256 px pedidas a Odoo")

    lotes = [de_texto[i:i + LOTE] for i in range(0, len(de_texto), LOTE)]
    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        list(pool.map(lote_texto, lotes))
        list(pool.map(foto_uno, con_foto))
    guardar()
    por_fuente: dict[str, int] = {}
    for f in hechos.values():
        por_fuente[f["f"]] = por_fuente.get(f["f"], 0) + 1
    aviso(f"títulos: {len(hechos)} SKUs · {por_fuente} · {ia.uso} · ≈ ${ia.costo_usd():.3f} USD")
    return {"skus": len(hechos), "por_fuente": por_fuente, "dudosos": sum(1 for f in hechos.values() if f.get("d")),
            "recortados": sum(1 for f in hechos.values() if f.get("r")), "uso": ia.uso,
            "costo_usd_estimado": ia.costo_usd(), "fallas": fallas[:20]}
