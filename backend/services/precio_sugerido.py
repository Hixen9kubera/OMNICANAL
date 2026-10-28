"""
precio_sugerido.py — «Sugerir precio» del Estudio (v0.686.0).

A cuánto se vende de verdad EL MISMO producto en Mercado Libre y qué precio compite sin bajar del piso de
rentabilidad. SOLO SUGIERE: no escribe precios en ningún lado. «Usar este precio» del Estudio solo llena el campo
«Precio oferta» y se guarda con el botón de siempre.

── POR QUÉ ─────────────────────────────────────────────────────────────────────
Prueba del 22-oct-2026 con TEC-0605 (2 pack de micrófonos lavalier, costo $82.08):
  · el botón anterior (`competencia.precio_competencia`) dependía de SerpApi —agotado— y caía a que la IA
    «estimara» sin datos: dijo $399 cuando el mismo modelo se vende entre $96 y $245;
  · la fórmula del panel dijo $273.56 con un envío de $91.70, sacado de medidas que no son medidas;
  · por TÍTULO el juez dejó 47 «iguales» con media $288 (Hollyland, Boya…); por FOTO quedaron 12 del mismo
    modelo, mediana $159. Lo que decide es la foto: calibrado contra una revisión a ojo, 11 de 13 coinciden.

── DE DÓNDE SALE EL PRECIO (v0.696.0) ──────────────────────────────────────────
  1. El CATÁLOGO MAESTRO (`catalogo_precios`, gratis, al abrir el SKU): la media de ML del v3 y lo de Amazon.
  2. Si el SKU no está ahí, el modo COMPLETO ($0.01–0.10, ~30 s–2 min): búsqueda de ML con UN término corto → juez
     por título (`competencia_juez.juzgar_lote`) → juez por FOTO, pareja por pareja (visión por OpenRouter) →
     referencia de los del mismo modelo; y Amazon por SP-API (`amazon_mercado.estudio`, gratis). Corre en segundo
     plano (POST da un `jid`) y queda guardado.
  La regla del precio (Brandon, 28-oct) la aplica el Estudio: un poco abajo de la MEDIA de ML (media × 0.97, hacia
  abajo al peso). El modo rápido (los más vendidos de la categoría filtrados por título) se quitó en v0.696.0: no
  servía. El piso y la utilidad se siguen calculando aquí, pero el Estudio ya no los enseña.

── LA BÚSQUEDA SE PAGA UNA VEZ (v0.688.0) ──────────────────────────────────────
  Usa el caché de términos de Competencia (`enrich.market_search_term` / `market_search_results`): si el término
  se midió hace menos de DIAS_CACHE días con al menos MIN_CACHE resultados, se reusa sin raspar —los productos
  parecidos y las variantes comparten el término corto—. Si no, se mide con la receta de Competencia
  (`competencia_captura.medir_busquedas`: raspa, reseñas, visitas, marca lo nuestro y GUARDA), y así queda para
  el siguiente. Nuestras propias publicaciones salen de los rivales.
  Un término por omisión: el segundo solo si el primero trae menos de MIN_RESULTADOS.

── LO QUE COBRA APIFY (medido en su facturación, 23-oct) ───────────────────────
  ~80 % es PROXY RESIDENCIAL ($8/GB), y Apify lo cobra MINUTOS DESPUÉS de que la corrida termina. Una página de
  búsqueda de ML: $0.0505 con imágenes, $0.038 sin ellas (`sin_media`). Un término bloqueado se reintenta y
  cada intento suma. El costo que se anota al terminar es PRELIMINAR; `competencia_scraper._cobro_final` lo
  corrige a los 3 min y `ultimo` relee el definitivo.

── CUANDO LOS DATOS NO CUADRAN (v0.687.0) ──────────────────────────────────────
  El piso es tan bueno como el costo y el peso del Estudio. VAR-0549-NEG (50 bolsas, 23-oct) salió en $1,139 con
  costo $456.45 y 20 kg, cuando la categoría vende a ~$159. La respuesta trae `categoria` (todos los más vendidos,
  sin filtrar: solo de contexto) y avisa si el envío pesa demasiado o si el piso queda muy arriba de la categoría.

── EL PISO ─────────────────────────────────────────────────────────────────────
    utilidad(P) = P/1.16 − comisión(P) − envío(P) − costo
    piso        = el menor P con utilidad(P) ≥ margen × costo
  Es la regla de `costos.py`, pero con la comisión REAL de FULL Premium (el % de `listing_prices`, cobrado sobre el
  precio CON IVA; ver memoria «comisión ML real») y el envío de la tabla oficial (`costos.calc_fee_envio_ml`) con el
  PESO REAL. Las medidas no se usan: son el CBM reconstruido, y con ellas TEC-0605 «pesaba» 15 kg.

Todo lo que pregunta afuera es GET (ML con nuestro token, que NO se renueva aquí) más lo que cobra: Apify (el
raspado), DeepSeek (el juez por título) y OpenRouter (las fotos). Cada consulta queda en `ops.process_log`
(proceso `precio_sugerido`) con su costo; esa misma fila es la que el Estudio vuelve a mostrar al abrir el SKU.
"""
from __future__ import annotations

import asyncio
import base64
import contextvars
import io
import json
import logging
import math
import re
import statistics
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from config import settings
from services import costos

log = logging.getLogger("omnicanal.precio_sugerido")

PROCESO = "precio_sugerido"
ML = "https://api.mercadolibre.com"
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
MODELO_FOTOS = "google/gemini-2.5-flash"
CUENTA_ML = "BEKURA"          # cualquiera de las dos sirve para leer: la API no distingue
IVA = costos.IVA_RATE
MARGEN_DEFAULT = 0.30         # margen mínimo sobre el COSTO (Brandon, 23-oct)
MAX_FOTOS = 30                # rivales que pasan al juez de fotos (~$0.0003 cada pareja)
MIN_GRUPO = 3                 # con menos, la mediana no es referencia
UMBRAL_PARECIDO = 0.2         # más vendidos que cuentan como grupo: parecido de título mínimo (Jaccard)
_BANDAS = ((499.99, 300.0), (1000.0, 750.0), (math.inf, 1500.0))   # (hasta, precio con el que se pregunta)
# Búsqueda del modo completo. ML bloqueó el término LARGO (el título casi entero) en las dos primeras consultas
# reales —TEC-0605 y VAR-0549-NEG, 23-oct— y cada bloqueo se reintentaba 8 veces: 3 min 43 s y $0.07 de Apify.
PALABRAS_TERMINO = 5          # el título recortado a sus primeras palabras con significado
REINTENTOS_BUSQUEDA = 3       # Competencia (en lote) sigue con 8
DIAS_CACHE = 7                # una búsqueda guardada de hace menos de 7 días se reusa (Brandon, 23-oct)
MIN_CACHE = 20                # con menos filas guardadas (Competencia suele medir 10) se vuelve a raspar con 48
MIN_RESULTADOS = 15           # el 2.º término solo se busca si el 1.º trae menos que esto
# Avisos de datos que no cuadran (v0.687.0).
UMBRAL_ENVIO = 0.15           # el envío se lleva ≥ 15 % del precio…
FACTOR_ENVIO = 2.0            # …y cuesta ≥ 2× lo de un paquete ligero al mismo precio → revisar el peso
FACTOR_CATEGORIA = 2.5        # piso > 2.5× la mediana de los más vendidos de la categoría → revisar costo y peso

ETIQUETAS = {
    "foto": "Mismo modelo por foto",
    "titulo": "Mismo producto por título",
    "mas_vendidos": "Más vendidos de la categoría",
}
ESTADOS = {
    "compite": "Compite: queda en la mediana del mercado y cubre tu margen.",
    "arriba_mediana": "Para cubrir tu margen hay que quedar arriba de la mediana, pero dentro del rango del mercado.",
    "no_compite": "Con este costo y margen no compite: el piso queda arriba de casi todo el mercado.",
    "sin_mercado": "No hubo comparables suficientes: el precio es solo el piso de rentabilidad.",
    "sin_piso": "No se pudo calcular el piso (falta costo o comisión): el precio es solo la mediana del mercado.",
}


# ── Cuentas puras (con pruebas) ──────────────────────────────────────────────

def resumen(precios: list[float]) -> dict[str, Any] | None:
    """n, mín, P25, mediana, media, P75 y máx. Con menos de 4 precios, P25/P75 son mín/máx."""
    ps = sorted(float(p) for p in precios if p and p > 0)
    if not ps:
        return None
    q1, q3 = (statistics.quantiles(ps, n=4, method="inclusive")[0::2] if len(ps) >= 4 else (ps[0], ps[-1]))
    return {"n": len(ps), "min": round(ps[0], 2), "p25": round(q1, 2), "mediana": round(statistics.median(ps), 2),
            "media": round(statistics.mean(ps), 2), "p75": round(q3, 2), "max": round(ps[-1], 2)}


def abajo9(x: float) -> int:
    """El precio comercial más alto que no pasa de `x`: termina en 9 (182 → 179, 159 → 159)."""
    n = math.floor(x)
    return n - ((n - 9) % 10)


def arriba9(x: float) -> int:
    """El precio comercial más bajo que no queda debajo de `x`: termina en 9 (208 → 209, 209 → 209)."""
    n = math.ceil(x)
    return n + ((9 - n) % 10)


def utilidad(precio: float, costo: float, pct: float, fijo: float, envio: float, iva: float = IVA) -> float:
    """Lo que queda por pieza: precio sin IVA − comisión (sobre el precio CON IVA) − envío − costo."""
    return precio / (1 + iva) - (pct * precio + fijo) - envio - costo


def piso(costo: float, margen: float, comision: Callable[[float], tuple[float, float]],
         envio: Callable[[float], float], tope: float | None = None, iva: float = IVA) -> float | None:
    """
    El menor precio entero con utilidad ≥ margen × costo. Barrido de $1 en $1: la comisión y el envío van por
    tramos (el envío SUBE al cruzar $299), así que la utilidad no siempre crece y una búsqueda binaria podría
    brincar el primer precio bueno. Son unos cuantos miles de cuentas puras, sin red. None si no se alcanza.
    """
    if not costo or costo <= 0:
        return None
    meta = margen * costo
    hasta = int(tope or max(costo * 25, 500))
    for p in range(max(1, math.ceil(costo)), hasta + 1):
        pct, fijo = comision(p)
        if utilidad(p, costo, pct, fijo, envio(p), iva) >= meta:
            return float(p)
    return None


FACTOR_EXTREMO = 2.5          # fuera de [mediana/2.5, mediana×2.5] del propio grupo no cuenta


def limites(precios: list[float], factor: float = FACTOR_EXTREMO) -> tuple[float, float] | None:
    """El rango creíble de un grupo alrededor de SU mediana. Lo que cae fuera no es el mismo producto aunque el
    filtro lo dejara pasar: en TEC-0605 el parecido de título metió un JBL de $705 y un Hollyland de $1,624, y el
    juez por título un Hollyland de $1,104, entre packs de $96–$245."""
    ps_ = [p for p in precios if p and p > 0]
    if len(ps_) < MIN_GRUPO:
        return None
    m = statistics.median(ps_)
    return m / factor, m * factor


def elegir_referencia(grupos: dict[str, list[float]]) -> dict[str, Any] | None:
    """El primer grupo con al menos `MIN_GRUPO` precios, en orden de confianza: foto → título → más vendidos.
    Si ninguno llega, el más grande que tenga algo, marcado `pocos`."""
    orden = [g for g in ("foto", "titulo", "mas_vendidos") if grupos.get(g)]
    for g in orden:
        r = resumen(grupos[g])
        if r and r["n"] >= MIN_GRUPO:
            return {"grupo": g, "etiqueta": ETIQUETAS[g], "pocos": False, **r}
    if orden:
        g = max(orden, key=lambda k: len(grupos[k]))
        r = resumen(grupos[g])
        if r:
            return {"grupo": g, "etiqueta": ETIQUETAS[g], "pocos": True, **r}
    return None


def sugerir(ref: dict[str, Any] | None, piso_: float | None) -> dict[str, Any]:
    """
    El precio que compite sin bajar del piso:
      · la mediana del mercado, redondeada hacia abajo a …9, si cubre el piso → «compite»;
      · si no, el piso redondeado hacia arriba: dentro del rango (≤ P75) → «arriba_mediana», fuera → «no_compite».
    """
    if ref is None:
        return {"precio": arriba9(piso_) if piso_ else None, "estado": "sin_mercado"}
    candidato = abajo9(ref["mediana"])
    if piso_ is None:
        return {"precio": candidato, "estado": "sin_piso"}
    if candidato >= piso_:
        return {"precio": candidato, "estado": "compite"}
    precio = arriba9(piso_)
    return {"precio": precio, "estado": "arriba_mediana" if precio <= ref["p75"] else "no_compite"}


_VACIAS = {"para", "con", "los", "las", "del", "por", "una", "uno", "que", "sin", "mas", "the", "and", "for",
           "color", "pack", "pzs", "piezas", "pieza", "set", "kit", "nuevo", "original"}


def palabras(texto: str | None) -> set[str]:
    t = unicodedata.normalize("NFKD", str(texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 3 and w not in _VACIAS}


def parecido(a: str | None, b: str | None) -> float:
    """Jaccard de palabras significativas. Es el filtro del modo GRATIS: burdo a propósito, y la pantalla lo dice."""
    pa, pb = palabras(a), palabras(b)
    return len(pa & pb) / len(pa | pb) if pa and pb else 0.0


def termino_corto(titulo: str | None, n: int = PALABRAS_TERMINO) -> str:
    """El título como búsqueda CORTA: sus primeras `n` palabras con significado, en orden y con sus acentos
    («Bolsas plasticas thank you para regalo y tienda 50 pzas» → «Bolsas plasticas thank you regalo»)."""
    out = []
    for w in str(titulo or "").split():
        if palabras(w):          # tiene al menos una palabra de 3+ letras que no es de relleno
            out.append(w.strip(".,;:()[]\"'«»"))
        if len(out) == n:
            break
    return " ".join(out)


def terminos_busqueda(del_usuario: list[str] | None, de_ia: str | None, titulo: str | None) -> list[str]:
    """Lo que se busca en ML: lo que escribió el usuario (manda, hasta 2); si no, el término de la IA y el título
    recortado, los dos cortos y sin repetir. Nunca el título entero: es el que ML bloquea."""
    propios = [" ".join(t.split()).lower() for t in del_usuario or [] if t and t.strip()]
    if propios:
        return propios[:2]
    # En minúsculas: el caché de Competencia es UNIQUE (canal, termino) sensible a mayúsculas, y «Bolsas…» y
    # «bolsas…» serían dos términos pagados dos veces. A ML le da igual.
    vistos: dict[frozenset[str], str] = {}
    for t in (termino_corto(de_ia, PALABRAS_TERMINO + 1), termino_corto(titulo)):
        clave = frozenset(palabras(t))
        if clave and clave not in vistos:
            vistos[clave] = t.lower()
    return list(vistos.values())[:2]


def revisar_datos(precio: float | None, envio_precio: float | None, envio_ligero: float | None,
                  peso_kg: float | None, piso_: float | None, costo: float | None,
                  categoria: dict[str, Any] | None, estado: str | None) -> list[str]:
    """
    Avisos cuando lo que manda en el piso parece un error de captura, no el mercado:
      · el envío se lleva ≥ UMBRAL_ENVIO del precio Y cuesta ≥ FACTOR_ENVIO × lo de un paquete ligero al mismo
        precio (un producto barato y ligero paga $35 de $209 —17 %— y está bien; 20 kg en 50 bolsas, no);
      · sin mercado o sin competir, el piso queda > FACTOR_CATEGORIA × la mediana de TODOS los más vendidos de la
        categoría.
    """
    out: list[str] = []
    if (precio and envio_precio and envio_ligero and envio_precio / precio >= UMBRAL_ENVIO
            and envio_precio >= FACTOR_ENVIO * envio_ligero):
        donde = (f"se calcula con {peso_kg:g} kg. Revisa el «Peso (kg)» del Estudio" if peso_kg
                 else "revisa el peso del producto")
        out.append(f"El envío (${envio_precio:,.2f}) se lleva el {round(envio_precio / precio * 100)} % del precio: "
                   f"{donde}.")
    med = (categoria or {}).get("mediana")
    if piso_ and med and estado in ("sin_mercado", "no_compite") and piso_ > FACTOR_CATEGORIA * med:
        out.append(f"El piso (${piso_:,.0f}) es {piso_ / med:.1f}× la mediana de los más vendidos de la categoría "
                   f"(${med:,.0f}): revisa el costo (${costo or 0:,.2f}) y el peso antes de usarlo.")
    return out


def leer_veredicto_foto(texto: str | None) -> dict[str, Any]:
    """{veredicto, razon, piezas} de la respuesta del juez de fotos. Lo que no se pueda leer queda «error»."""
    t = str(texto or "")
    try:
        d = json.loads(t[t.find("{"): t.rfind("}") + 1])
    except ValueError:
        return {"veredicto": "error", "razon": "respuesta ilegible", "piezas": None}
    v = str(d.get("veredicto") or "").strip().lower().replace(" ", "_")
    if v not in ("mismo_modelo", "mismo_diseno", "diferente"):
        v = "error"
    try:
        piezas = int(d.get("piezas_foto2")) if d.get("piezas_foto2") not in (None, "") else None
    except (TypeError, ValueError):
        piezas = None
    return {"veredicto": v, "razon": str(d.get("razon") or "")[:120], "piezas": piezas}


def comparable_por_foto(r: dict[str, Any], unidades_nuestras: int | None) -> bool:
    """Entra a la referencia si la foto dice mismo modelo/diseño y, cuando se pudo contar, trae las mismas piezas
    que el nuestro: un 1-pack del mismo modelo a la mitad de precio bajaría la mediana de un 2-pack."""
    if r.get("foto") not in ("mismo_modelo", "mismo_diseno"):
        return False
    piezas = r.get("foto_piezas")
    return not (unidades_nuestras and piezas and piezas != unidades_nuestras)


def peso_de_atributos(atributos: list[dict[str, Any]] | None) -> float | None:
    """El peso real de la ficha de Woo («0.07 kg», «70 g», «1,2 kg»). None si no viene."""
    for a in atributos or []:
        if str(a.get("name") or a.get("nombre") or "").strip().lower() not in ("peso", "peso del producto", "weight"):
            continue
        for v in a.get("options") or [a.get("valor")]:
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|g|gr|gramos|kilos?)?", str(v or "").lower())
            if m:
                x = float(m.group(1).replace(",", "."))
                return round(x / 1000, 4) if (m.group(2) or "kg").startswith("g") else x
    return None


# ── Trabajos en segundo plano (mismo molde que competencia_trabajos) ─────────

_TTL = 60 * 30
_trabajos: dict[str, dict[str, Any]] = {}
_por_sku: dict[str, str] = {}
_lock = threading.Lock()
PASOS = {
    "encolado": "En cola…",
    "datos": "Leyendo el producto…",
    "mas_vendidos": "Leyendo los más vendidos de la categoría…",
    "buscando": "Buscando en Mercado Libre (alrededor de 1 min)…",
    "juez_titulo": "Separando los que son el mismo producto…",
    "juez_fotos": "Comparando fotos…",
    "amazon": "Buscando en Amazon…",
    "calculando": "Calculando comisión, piso y sugerencia…",
    "listo": "Listo",
    "error": "Error",
}


def _marcar(jid: str, paso: str, **extra: Any) -> None:
    with _lock:
        t = _trabajos.get(jid)
        if t:
            t.update({"paso": paso, "paso_label": PASOS.get(paso, paso), "actualizado": time.time(), **extra})


def estado(jid: str) -> dict[str, Any] | None:
    with _lock:
        t = _trabajos.get(jid)
        return dict(t) if t else None


def arrancar_completo(entrada: dict[str, Any]) -> dict[str, Any]:
    """Lanza el modo completo y devuelve el trabajo. Si ya hay uno VIVO para el SKU, devuelve ese: un segundo clic
    no vuelve a pagar."""
    sku = entrada["sku"]
    ahora = time.time()
    with _lock:
        for k in [k for k, v in _trabajos.items() if ahora - v["creado"] > _TTL and v["paso"] in ("listo", "error")]:
            _trabajos.pop(k, None)
        vivo = _trabajos.get(_por_sku.get(sku, ""))
        if vivo and vivo["paso"] not in ("listo", "error"):
            return dict(vivo)
        jid = uuid.uuid4().hex[:12]
        _trabajos[jid] = {"id": jid, "sku": sku, "paso": "encolado", "paso_label": PASOS["encolado"],
                          "creado": ahora, "actualizado": ahora, "error": None, "resultado": None}
        _por_sku[sku] = jid
        copia = dict(_trabajos[jid])
    ctx = contextvars.copy_context()   # hereda el actor de la sesión (la firma de la bitácora)
    threading.Thread(target=ctx.run, args=(_correr_completo, jid, entrada), name=f"precio-{jid}", daemon=True).start()
    return copia


def _correr_completo(jid: str, entrada: dict[str, Any]) -> None:
    try:
        res = asyncio.run(completo(entrada, lambda paso: _marcar(jid, paso)))
        _marcar(jid, "listo", resultado=res)
    except Exception as exc:  # noqa: BLE001 — un hilo que revienta en silencio deja el panel esperando
        log.exception("precio sugerido (completo) de %s falló", entrada.get("sku"))
        _marcar(jid, "error", error=str(exc)[:200])


# ── Lo que se pregunta afuera ────────────────────────────────────────────────

async def _get(cli: httpx.AsyncClient, ruta: str, **params: Any) -> Any:
    for intento in range(4):
        try:
            r = await cli.get(ruta, params=params)
        except httpx.HTTPError:
            await asyncio.sleep(1 + intento)
            continue
        if r.status_code == 429:
            await asyncio.sleep(1.5 * (intento + 1))
            continue
        return r.json() if r.status_code == 200 else None
    return None


async def _cliente_ml() -> httpx.AsyncClient | None:
    from services import meli

    tk = await meli.access_token_async(CUENTA_ML)
    if not tk:
        return None
    return httpx.AsyncClient(base_url=ML, headers={"Authorization": f"Bearer {tk}"}, timeout=30)


async def comisiones(cli: httpx.AsyncClient, cat: str) -> list[dict[str, Any]]:
    """La comisión de FULL Premium por TRAMO de precio (escalones en $500 y en $1,000), de `listing_prices`."""
    async def una(hasta: float, precio: float) -> dict[str, Any] | None:
        d = await _get(cli, "/sites/MLM/listing_prices", price=precio, category_id=cat, listing_type_id="gold_pro",
                       logistic_type="fulfillment", currency_id="MXN")
        d = d[0] if isinstance(d, list) and d else d
        det = (d or {}).get("sale_fee_details") or {}
        if det.get("percentage_fee") is None:
            return None
        return {"hasta": None if math.isinf(hasta) else hasta, "pct": float(det["percentage_fee"]) / 100,
                "fijo": float(det.get("fixed_fee") or 0)}

    bandas = await asyncio.gather(*(una(h, p) for h, p in _BANDAS))
    return [b for b in bandas if b]


def comision_de(bandas: list[dict[str, Any]]) -> Callable[[float], tuple[float, float]]:
    def f(precio: float) -> tuple[float, float]:
        for b in bandas:
            if b["hasta"] is None or precio <= b["hasta"]:
                return b["pct"], b["fijo"]
        return bandas[-1]["pct"], bandas[-1]["fijo"]
    return f


def envio_de(peso_kg: float | None, fijo: float | None) -> Callable[[float], float]:
    """Envío por pieza: tabla oficial con el peso real; sin peso, el que tenga el panel (fijo); sin nada, 0."""
    if peso_kg:
        return lambda precio: costos.calc_fee_envio_ml(peso_kg, precio)
    return lambda precio: float(fijo or 0)


async def mas_vendidos(cli: httpx.AsyncClient, cat: str) -> list[dict[str, Any]]:
    """El top 20 de la categoría (`/highlights`) con precio: el más bajo de quienes venden ese producto de
    catálogo. Las publicaciones sueltas de otros vendedores dan 403 y se quedan fuera."""
    top = (await _get(cli, f"/highlights/MLM/category/{cat}") or {}).get("content") or []
    sem = asyncio.Semaphore(5)

    async def uno(h: dict[str, Any]) -> dict[str, Any] | None:
        if h.get("type") != "PRODUCT":
            return None
        async with sem:
            prod, items = await asyncio.gather(_get(cli, f"/products/{h['id']}"),
                                               _get(cli, f"/products/{h['id']}/items", limit=10))
        precios = [x.get("price") for x in (items or {}).get("results") or [] if x.get("price")]
        if not prod or not precios:
            return None
        foto = next((p.get("url") for p in prod.get("pictures") or [] if p.get("url")), None)
        return {"id": h["id"], "posicion": h.get("position"), "titulo": prod.get("name"), "precio": float(min(precios)),
                "imagen": foto, "url": f"https://www.mercadolibre.com.mx/p/{h['id']}", "fuente": "mas_vendidos"}

    return [x for x in await asyncio.gather(*(uno(h) for h in top)) if x]


def _miniatura(contenido: bytes) -> str | None:
    """JPEG de 512 px en data-URL: el juez no necesita más y así cuesta menos."""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(contenido)).convert("RGB")
        im.thumbnail((512, 512))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


async def _bajar_miniatura(cli: httpx.AsyncClient, url: str | None) -> str | None:
    if not url:
        return None
    try:
        r = await cli.get(url, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    except httpx.HTTPError:
        return None
    return await asyncio.to_thread(_miniatura, r.content) if r.status_code == 200 else None


_JUEZ_FOTOS = (
    "Comparas la FOTO 1 (nuestro producto) contra la FOTO 2 (un competidor) para decidir si sirven para comparar "
    "precio.\nClasifica con UNA de estas tres palabras:\n"
    "- mismo_modelo: es el mismo producto físico (misma forma, mismas piezas principales, mismos detalles visibles como "
    "pantalla, botones o conectores). Puede cambiar el color, la marca impresa, el fondo o los accesorios.\n"
    "- mismo_diseno: mismo tipo y misma forma general de las piezas principales, pero con alguna diferencia visible de "
    "modelo (otra pantalla, otro receptor, otro estuche, otra proporción).\n"
    "- diferente: otra forma o construcción de las piezas principales, otro tipo de producto, otra gama (profesional vs "
    "básico) o no se puede ver bien el producto.\n"
    "No uses el precio: solo lo que se ve. Cuenta también cuántas piezas PRINCIPALES iguales trae la FOTO 2 (por "
    "ejemplo, 2 micrófonos; no cuentes accesorios), o null si no se puede saber.\n"
    'Responde SOLO JSON: {"veredicto": "mismo_modelo|mismo_diseno|diferente", "piezas_foto2": <entero o null>, '
    '"razon": "<máx 12 palabras>"}'
)


async def juez_fotos(nuestra_url: str, rivales: list[dict[str, Any]]) -> dict[str, Any]:
    """Nuestra foto contra la de cada rival, de una pareja a la vez (con todas en una sola imagen el modelo dio por
    iguales a 41 de 61). Escribe `foto`, `foto_razon` y `foto_piezas` en cada rival. → {usd, motivo}"""
    if not settings.openrouter_api_key:
        return {"usd": 0.0, "motivo": "Sin llave de OpenRouter: no se compararon fotos."}
    usd = 0.0
    motivo = None
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=60) as cli:
        nuestra = await _bajar_miniatura(cli, nuestra_url)
        if not nuestra:
            return {"usd": 0.0, "motivo": "No se pudo leer la foto del producto."}

        async def uno(r: dict[str, Any]) -> None:
            nonlocal usd, motivo
            async with sem:
                suya = await _bajar_miniatura(cli, r.get("imagen"))
                if not suya:
                    r.update(foto="error", foto_razon="sin foto del rival")
                    return
                body = {"model": MODELO_FOTOS, "max_tokens": 150, "temperature": 0, "messages": [
                    {"role": "system", "content": _JUEZ_FOTOS},
                    {"role": "user", "content": [{"type": "text", "text": "FOTO 1 = nuestro producto. FOTO 2 = competidor."},
                                                 {"type": "image_url", "image_url": {"url": nuestra}},
                                                 {"type": "image_url", "image_url": {"url": suya}}]}]}
                try:
                    x = await cli.post(OPENROUTER, json=body, timeout=90, headers={
                        "Authorization": f"Bearer {settings.openrouter_api_key}", "X-Title": "Kubera Omnicanal"})
                except httpx.HTTPError as exc:
                    r.update(foto="error", foto_razon=type(exc).__name__)
                    return
            if x.status_code in (401, 402):
                motivo = ("OpenRouter sin crédito: recargar en openrouter.ai/settings/credits"
                          if x.status_code == 402 else "OpenRouter rechazó la llave")
                r.update(foto="error", foto_razon=motivo)
                return
            d = x.json() if x.status_code == 200 else {}
            usd += float((d.get("usage") or {}).get("cost") or 0)
            texto = ((d.get("choices") or [{}])[0].get("message") or {}).get("content")
            v = leer_veredicto_foto(texto)
            r.update(foto=v["veredicto"], foto_razon=v["razon"], foto_piezas=v["piezas"])

        await asyncio.gather(*(uno(r) for r in rivales))
    return {"usd": round(usd, 6), "motivo": motivo}


# ── Datos del SKU, bitácora y caché ──────────────────────────────────────────

def _del_cache(termino: str) -> dict[str, Any] | None:
    """
    BLOQUEANTE (kubera, SELECT). La búsqueda guardada del término, si sirve: medida hace menos de DIAS_CACHE días
    y con al menos MIN_CACHE filas, o medida 'vacio' (ML no tenía nada: no se vuelve a pagar). → {filas, dias}.
    None = hay que raspar (no existe, es vieja, quedó bloqueada, tiene pocas filas, o kubera no contestó).
    """
    from services import competencia_store

    try:
        est = competencia_store.estado_termino(termino)
        if not est or est.get("dias") is None or int(est["dias"]) >= DIAS_CACHE:
            return None
        if est.get("estado") == "vacio":
            return {"filas": [], "dias": int(est["dias"])}
        if est.get("estado") not in (None, "ok") or int(est.get("resultados") or 0) < MIN_CACHE:
            return None
        filas = competencia_store.busqueda(est["termino"], 48)
    except Exception as exc:  # noqa: BLE001 — sin caché se raspa, como antes
        log.warning("precio sugerido: no se pudo leer el caché de %r: %s", termino, exc)
        return None
    return {"filas": filas, "dias": int(est["dias"])} if len(filas) >= MIN_CACHE else None


async def buscar(terminos: list[str], bloqueados: set[str],
                 avisos: list[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Cada término: del caché si sirve; si no, se mide con la receta de Competencia y queda guardado.
    → ({término: filas}, {término reusado: días que tiene})."""
    from services import competencia_captura

    crudos: dict[str, list[dict[str, Any]]] = {}
    reusados: dict[str, int] = {}
    faltan: list[str] = []
    for t in terminos:
        c = await asyncio.to_thread(_del_cache, t)
        if c is None:
            faltan.append(t)
        else:
            crudos[t], reusados[t] = c["filas"], c["dias"]
    if faltan:
        medidas: dict[str, list[dict[str, Any]]] = {}
        try:
            await competencia_captura.medir_busquedas(faltan, limite=48, bloqueados=bloqueados,
                                                      reintentos=REINTENTOS_BUSQUEDA, filas_out=medidas)
        except Exception as exc:  # noqa: BLE001 — lo raspado (ya pagado) se usa aunque no se haya guardado
            log.warning("precio sugerido: la búsqueda no se guardó en el caché: %s", exc)
            avisos.append("La búsqueda no se pudo guardar en el caché de Competencia: la próxima se vuelve a pagar.")
        crudos.update(medidas)
    return crudos, reusados


def _costo_panel(sku: str) -> dict[str, Any]:
    """BLOQUEANTE (kubera, SELECT). Costo, categoría y envío del panel para el SKU (canal mercado_libre)."""
    from services import supabase_db as sdb

    try:
        return sdb.fetch_one(
            "select costo_unitario, ml_cat_id, costo_fee_envio from costing.costos_finales "
            "where sku = %s and canal = 'mercado_libre' order by updated_at desc nulls last limit 1", (sku,)) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("precio sugerido: no se pudo leer el costo de %s: %s", sku, exc)
        return {}


async def datos_sku(entrada: dict[str, Any]) -> dict[str, Any]:
    """Lo que se ve en el Estudio manda (título, costo, categoría, foto); lo que falte sale del panel y de Woo."""
    from services import woocommerce

    sku = entrada["sku"]
    panel, prod = await asyncio.gather(asyncio.to_thread(_costo_panel, sku), woocommerce.obtener_producto_por_sku(sku))
    prod = prod or {}   # ya normalizada por woocommerce: nombre, imagenes (URLs), atributos [{nombre, valor}]
    imagenes = [u for u in prod.get("imagenes") or [] if u]
    peso = entrada.get("peso_kg") or peso_de_atributos(prod.get("atributos"))
    costo = entrada.get("costo") or panel.get("costo_unitario")
    return {
        "sku": sku,
        "titulo": entrada.get("titulo") or prod.get("nombre") or sku,
        "costo": float(costo) if costo else None,
        "ml_cat_id": entrada.get("ml_cat_id") or panel.get("ml_cat_id"),
        "imagen": entrada.get("imagen") or (imagenes[0] if imagenes else None),
        "peso_kg": float(peso) if peso else None,
        "envio_panel": float(panel["costo_fee_envio"]) if panel.get("costo_fee_envio") else None,
        "margen": float(entrada.get("margen") if entrada.get("margen") is not None else MARGEN_DEFAULT),
    }


def _anotar(res: dict[str, Any], duracion: float) -> None:
    """BLOQUEANTE (kubera). La bitácora de cada consulta, con su costo; el Estudio la relee como caché."""
    from core import actor as core_actor
    from services import supabase_db as sdb

    try:
        sdb.execute(
            "INSERT INTO ops.process_log (proceso, origen, sku, accion, estado, detalle, duracion_s, actor) "
            "VALUES (%s, 'panel', %s, %s, %s, %s::jsonb, %s, %s)",
            (PROCESO, res["sku"], res["modo"], "ok" if res.get("ok") else "error",
             json.dumps(res, ensure_ascii=False, default=str), round(duracion, 1), core_actor.actual() or None))
    except Exception as exc:  # noqa: BLE001 — la bitácora nunca tumba la sugerencia
        log.warning("precio sugerido: no se pudo anotar %s: %s", res.get("sku"), exc)


def ultimo(sku: str) -> dict[str, Any] | None:
    """BLOQUEANTE (kubera, SELECT). La última consulta COMPLETA del SKU, para no volver a pagar al abrir el Estudio.
    Las del modo rápido (quitado en v0.696.0) no cuentan: TEC-0605-NEG-2PZ tenía una de hace 5 días que, por ser la
    más reciente, le ganaba al catálogo maestro ($156 contra $372)."""
    from services import supabase_db as sdb

    fila = sdb.fetch_one(
        "select detalle, created_at from ops.process_log where proceso = %s and sku = %s and estado = 'ok' "
        "and accion = 'completo' order by id desc limit 1", (PROCESO, sku))
    if not fila:
        return None
    d = fila["detalle"] if isinstance(fila["detalle"], dict) else json.loads(fila["detalle"])
    cc = d.get("costo_consulta") or {}
    if cc.get("apify_runs") and not cc.get("apify_final"):
        # Al guardarse, Apify todavía no cobraba el proxy: se relee el cobro definitivo.
        g = _cobro_de_corridas(cc["apify_runs"])
        if g:
            cc.update(apify=g["usd"], apify_final=g["final"])
            cc["total"] = total_consulta(cc)
    return {**d, "consultado": fila["created_at"].isoformat() if fila.get("created_at") else d.get("consultado")}


def _sumar_apify(filas: list[dict[str, Any]]) -> dict[str, Any]:
    dets = [f["detalle"] if isinstance(f["detalle"], dict) else json.loads(f["detalle"]) for f in filas]
    return {"usd": round(sum(float(d.get("usd") or 0) for d in dets), 6),
            "runs": [d["run_id"] for d in dets if d.get("run_id")],
            "final": all(d.get("usd_final") for d in dets)}


def _gasto_apify_desde(t0: datetime) -> dict[str, Any]:
    """BLOQUEANTE (kubera, SELECT). Lo que Apify cobró desde `t0` según `competencia_scraper._registrar_gasto`,
    con los ids de sus corridas. `final` = Apify ya asentó el cobro de todas (el proxy llega minutos después)."""
    from services import supabase_db as sdb

    try:
        return _sumar_apify(sdb.fetch_all(
            "select detalle from ops.process_log "
            "where proceso = 'competencia' and origen = 'apify' and created_at >= %s", (t0,)))
    except Exception:  # noqa: BLE001
        return {"usd": 0.0, "runs": [], "final": True}


def _cobro_de_corridas(runs: list[str]) -> dict[str, Any] | None:
    """BLOQUEANTE (kubera, SELECT). El cobro de esas corridas tal como está HOY en la bitácora (ya corregido por
    `_cobro_final` si pasaron sus 3 minutos)."""
    from services import supabase_db as sdb

    try:
        filas = sdb.fetch_all(
            "select detalle from ops.process_log where proceso = 'competencia' and origen = 'apify' "
            "and detalle->>'run_id' = any(%s)", (list(runs),))
    except Exception:  # noqa: BLE001
        return None
    return _sumar_apify(filas) if filas else None


def total_consulta(cc: dict[str, Any]) -> float:
    return round(sum(float(cc.get(k) or 0) for k in ("apify", "juez", "fotos")), 6)


# ── El modo completo ─────────────────────────────────────────────────────────

async def _cerrar(d: dict[str, Any], modo: str, rivales: list[dict[str, Any]], grupos: dict[str, list[float]],
                  cli: httpx.AsyncClient | None, avisos: list[str], costo_consulta: dict[str, float],
                  extra: dict[str, Any], top: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Comisión real, piso, referencia y sugerencia: el cierre del modo completo. `top` son los más vendidos de
    la categoría SIN filtrar: su resumen viaja como `categoria`, solo de contexto (no entra a la sugerencia)."""
    bandas = await comisiones(cli, d["ml_cat_id"]) if cli and d["ml_cat_id"] else []
    if not bandas:
        avisos.append("No se pudo leer la comisión de ML para la categoría: sin piso de rentabilidad.")
    envio = envio_de(d["peso_kg"], d["envio_panel"])
    origen_envio = "peso" if d["peso_kg"] else ("panel" if d["envio_panel"] else "sin_dato")
    if origen_envio == "panel":
        avisos.append("La ficha no trae el peso: el envío es el que calculó el panel (puede venir de medidas que no "
                      "son reales).")
    elif origen_envio == "sin_dato":
        avisos.append("Sin peso ni envío del panel: el piso no incluye envío.")
    comision = comision_de(bandas) if bandas else None
    piso_ = piso(d["costo"], d["margen"], comision, envio) if (comision and d["costo"]) else None
    if not d["costo"]:
        avisos.append("El SKU no tiene costo en el panel: sin piso de rentabilidad.")
    # Extremos: cada grupo se recorta alrededor de su propia mediana, y el rival recortado se marca.
    for g, precios in list(grupos.items()):
        lim = limites(precios)
        if not lim:
            continue
        grupos[g] = [p for p in precios if lim[0] <= p <= lim[1]]
        for r in rivales:
            if g in (r.get("_grupos") or []) and not lim[0] <= r["precio"] <= lim[1]:
                r["_grupos"].remove(g)
                r["extremo"] = True
    ref = elegir_referencia(grupos)
    s = sugerir(ref, piso_)
    precio = s["precio"]
    desglose = None
    if precio and comision and d["costo"]:
        pct, fijo = comision(precio)
        u = utilidad(precio, d["costo"], pct, fijo, envio(precio))
        desglose = {"comision": round(pct * precio + fijo, 2), "pct": pct, "envio": round(envio(precio), 2),
                    "iva": round(precio - precio / (1 + IVA), 2), "costo": d["costo"], "utilidad": round(u, 2),
                    "pct_sobre_costo": round(u / d["costo"] * 100, 1)}
    categoria = resumen([t["precio"] for t in top or [] if t.get("precio")])
    avisos.extend(revisar_datos(precio, envio(precio) if precio else None,
                                costos.calc_fee_envio_ml(0.1, precio) if precio else None,
                                d["peso_kg"], piso_, d["costo"], categoria, s["estado"]))
    for r in rivales:
        r["en_referencia"] = bool(ref and r.get("_grupos") and ref["grupo"] in r["_grupos"])
        r.pop("_grupos", None)
    costo_consulta["total"] = total_consulta(costo_consulta)
    return {
        "ok": True, "modo": modo, "sku": d["sku"], "titulo": d["titulo"], "imagen": d["imagen"], "costo": d["costo"],
        "ml_cat_id": d["ml_cat_id"], "margen": d["margen"], "peso_kg": d["peso_kg"], "envio_origen": origen_envio,
        # Con la comisión por tramo y la fila de envío, la pantalla recalcula la utilidad de cualquier precio que se
        # teclee sin volver a preguntar (misma fórmula que `utilidad`).
        "comision": bandas, "tabla_envio": costos._fila_tarifa_ml(d["peso_kg"]) if d["peso_kg"] else None,
        "tramos_envio": list(costos._TRAMOS_PRECIO), "envio_fijo": None if d["peso_kg"] else d["envio_panel"],
        "iva": IVA, "piso": piso_, "referencia": ref, "categoria": categoria,
        "sugerido": {**s, "texto": ESTADOS[s["estado"]], "desglose": desglose},
        "grupos": {g: resumen(v) for g, v in grupos.items()}, "rivales": rivales, "avisos": avisos,
        "costo_consulta": costo_consulta, "consultado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **extra,
    }


async def completo(entrada: dict[str, Any], avisar: Callable[[str], None] = lambda _p: None) -> dict[str, Any]:
    """$0.01–0.10: búsqueda (del caché de Competencia o raspada) + juez por título + juez por foto + comisión real +
    piso. Lo caro es Apify, y solo cuando hay que raspar (ver «LO QUE COBRA APIFY» arriba)."""
    from services import amazon_mercado, catalogo_precios, competencia_juez, competencia_terminos

    t0, desde = time.monotonic(), datetime.now(timezone.utc)
    avisar("datos")
    d = await datos_sku(entrada)
    avisos: list[str] = []
    gasto = {"apify": 0.0, "juez": 0.0, "fotos": 0.0}

    # La palabra clave del catálogo maestro ya es corta y en español: va TAL CUAL (recortarla le quitaría «uv» a
    # «linterna uv recargable») y con ella no se le pregunta a la IA. El título recortado queda de 2.º término.
    q_catalogo = ((await asyncio.to_thread(catalogo_precios.por_sku, d["sku"])).get("amazon") or {}).get("q")
    propios = any(t and t.strip() for t in entrada.get("terminos") or [])
    terminos = terminos_busqueda(entrada.get("terminos"), None, d["titulo"])
    if not propios and q_catalogo:
        terminos = list(dict.fromkeys([" ".join(q_catalogo.split()).lower(),
                                       *terminos_busqueda(None, None, d["titulo"])]))[:2]
    elif not propios:
        prop = await asyncio.to_thread(competencia_terminos.proponer, [{"sku": d["sku"], "nombre": d["titulo"]}])
        terminos = terminos_busqueda(None, prop.get(d["sku"]), d["titulo"])

    cli = await _cliente_ml()
    try:
        avisar("mas_vendidos")
        top = await mas_vendidos(cli, d["ml_cat_id"]) if (cli and d["ml_cat_id"]) else []

        avisar("buscando")
        bloqueados: set[str] = set()
        # Lo que escribió el usuario se busca completo; lo propuesto, de uno en uno: el 2.º solo si hace falta.
        buscados = terminos if propios else terminos[:1]
        crudos, reusados = await buscar(buscados, bloqueados, avisos)
        if not propios and terminos[1:] and sum(len(v) for v in crudos.values()) < MIN_RESULTADOS:
            mas, mas_reusados = await buscar(terminos[1:], bloqueados, avisos)
            crudos.update(mas)
            reusados.update(mas_reusados)
            buscados = terminos
        if bloqueados:
            avisos.append(f"ML bloqueó la búsqueda de: {', '.join(sorted(bloqueados))} "
                          f"({REINTENTOS_BUSQUEDA} intentos). Si faltan comparables, prueba con otro término.")
        vistos: dict[str, dict[str, Any]] = {}
        propias = 0
        for termino, filas in crudos.items():
            for f in filas:
                if f.get("es_nuestro"):          # nuestra propia publicación no es competencia
                    propias += 1
                    continue
                if f.get("externo_id") and f.get("precio") and f["externo_id"] not in vistos:
                    vistos[f["externo_id"]] = {"id": f["externo_id"], "titulo": f.get("titulo"),
                                               "precio": float(f["precio"]), "imagen": f.get("imagen"),
                                               "url": f.get("url"), "posicion": f.get("posicion"),
                                               "termino": termino, "fuente": "busqueda"}
        rivales = list(vistos.values())
        ids_top = {t["id"] for t in top}
        for r in rivales:   # el id de una búsqueda puede ser el de la publicación o el del producto de catálogo (/p/…)
            r["mas_vendido"] = r["id"] in ids_top or any(f"/p/{i}" in (r.get("url") or "") for i in ids_top)
        if not rivales:
            avisos.append("La búsqueda en ML no trajo publicaciones con precio.")

        avisar("juez_titulo")
        juez = await asyncio.to_thread(
            competencia_juez.juzgar_lote, {"titulo": d["titulo"]}, rivales,
            modelo=settings.competencia_juez_modelo) if rivales else {"veredictos": {}, "usd": 0, "unidades_nuestras": 1}
        gasto["juez"] = float(juez.get("usd") or 0)
        for i, v in (juez.get("veredictos") or {}).items():
            rivales[i - 1].update(clase_titulo=v["clase"], motivo_titulo=v["motivo"], unidades=v["unidades_rival"])
        unidades = juez.get("unidades_nuestras") or 1

        avisar("juez_fotos")
        candidatos = sorted((r for r in rivales if r.get("clase_titulo") in ("mismo", "otro_paquete")),
                            key=lambda r: r.get("posicion") or 999)[:MAX_FOTOS]
        if candidatos and d["imagen"]:
            jf = await juez_fotos(d["imagen"], candidatos)
            gasto["fotos"] = jf["usd"]
            if jf["motivo"]:
                avisos.append(jf["motivo"])
        elif not d["imagen"]:
            avisos.append("El SKU no tiene foto: no se compararon fotos.")

        for r in rivales:
            r["_grupos"] = [g for g, si in (("titulo", r.get("clase_titulo") == "mismo"),
                                            ("foto", comparable_por_foto(r, unidades))) if si]
        grupos = {"foto": [r["precio"] for r in rivales if "foto" in r["_grupos"]],
                  "titulo": [r["precio"] for r in rivales if "titulo" in r["_grupos"]],
                  "mas_vendidos": [t["precio"] for t in top if parecido(d["titulo"], t["titulo"]) >= UMBRAL_PARECIDO]}

        # Amazon no cobra (SP-API) y no debe tumbar lo de ML: si falla, se avisa.
        avisar("amazon")
        az = None
        try:
            az = await amazon_mercado.estudio(q_catalogo or (buscados[0] if buscados else termino_corto(d["titulo"])),
                                              d["titulo"], unidades)
            gasto["juez"] += az.pop("usd", 0.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("precio sugerido: Amazon de %s falló: %s", d["sku"], exc)
            avisos.append(f"No se pudo leer Amazon: {exc}")

        avisar("calculando")
        raspados = [t for t in buscados if t not in reusados]
        g = (await asyncio.to_thread(_gasto_apify_desde, desde) if raspados
             else {"usd": 0.0, "runs": [], "final": True})
        gasto.update(apify=g["usd"], apify_runs=g["runs"], apify_final=g["final"])
        res = await _cerrar(d, "completo", rivales, grupos, cli, avisos, gasto,
                            {"terminos": buscados, "cache": reusados, "propias_fuera": propias,
                             "unidades_nuestras": unidades, "mas_vendidos": top, "amazon": az}, top=top)
    finally:
        if cli:
            await cli.aclose()
    await asyncio.to_thread(_anotar, res, time.monotonic() - t0)
    return res
