"""
competencia_mejora.py — Cuando el término de búsqueda no trae rivales de verdad,
proponer uno mejor, MEDIRLO, y dejarlo como sugerencia.

── POR QUÉ EXISTE ─────────────────────────────────────────────────────────────
El juez (`competencia_juez`) limpia lo que la búsqueda trae; no puede inventar lo
que no trae. En el examen de 90 SKUs, 26 quedaron con menos de 3 rivales reales y
14 con CERO. Si esos ceros se repartieran al azar serían ~7 %; se midió 16 %: no
es mala suerte, son términos que apuntan a otra cosa. Ejemplos del sandbox:

    «soporte para camara»   para un poste de pared de cámara de seguridad
                            → 10 de 10 eran soportes de cámara de acción
    «filtro pop microfono»  → 9 de 10 eran esponjas cubre-micrófono

Ahí no hay nada que filtrar: hay que buscar distinto.

── LO QUE HACE, EN ORDEN ──────────────────────────────────────────────────────
1. ELEGIR. Solo SKUs cuyo término ya se midió Y se juzgó COMPLETO y aun así
   tienen menos de `UMBRAL` comparables. Con rivales sin juzgar la respuesta
   honesta es «no sé», y no se gasta en mejorar lo que quizá está bien.
2. AGRUPAR POR TÉRMINO. Un término lo comparten varias variantes; se pide UNA
   propuesta por grupo (con todos sus títulos) para no pagar cinco búsquedas casi
   iguales ni dejar a cinco hermanas en cinco términos distintos.
3. PROPONER. El LLM ve cómo llama el mercado al producto (los rivales que SÍ eran
   comparables), qué hay que evitar (los descartados, con su clase) y el
   vocabulario con demanda real de la categoría (/trends). Puede contestar
   «el término está bien, ML mezcla refacciones»: entonces no se cambia nada.
4. RECLAMAR antes de pagar: cada (SKU, candidato) se anota `propuesto`; si otro
   proceso ya lo tiene, no se mide dos veces.
5. MEDIR los candidatos (Apify, en tandas) SIN asignarlos a nadie.
6. JUZGAR los rivales de cada candidato contra cada SKU del grupo.
7. DECIDIR. Gana un candidato solo si da al menos `UMBRAL` comparables, al menos
   `HISTERESIS` más que antes (3 contra 2 cabe en el ruido: la búsqueda rota) y
   sus precios no están dispersos. Queda `medido` = SUGERENCIA.

── NUNCA CAMBIA UN TÉRMINO SOLA ───────────────────────────────────────────────
Cambiar el término de un SKU mueve el «precio de mercado» que ven los KAM en
Publicaciones (esa pantalla promedia los rivales del término asignado) y la
búsqueda con la que se mide la posición orgánica. Por eso aquí solo se SUGIERE;
aplicar es un acto de una persona (`aceptar`), que además queda como corrección
manual para que ninguna corrida automática la pise después.

── NO SE CICLA ────────────────────────────────────────────────────────────────
Hay productos sin 3 rivales iguales en ML. Para no pagarles búsquedas sin fin:
no se repite un candidato ya probado ni se vuelve a un término anterior; un SKU
con `MAX_POR_SKU` candidatos medidos ya no entra; y tras cualquier resolución
descansa `ENFRIAMIENTO_DIAS`. Las respuestas que dejan el término como está
(«el término está bien», «no hay candidato nuevo») también se anotan y también
descansan.

Lo que NO concluyó (cero filas, muro de login, juicio a medias) se puede volver
a probar, pero con freno: no se le vuelve a proponer al modelo durante
`REINTENTO_DIAS`, un cero o un muro recientes del catálogo no se vuelven a pagar,
y en la ronda `RONDAS_MAX` un fallo del propio candidato ya es la respuesta.
Mientras alguno ESPERA su reintento, un «no hay candidato nuevo» no se anota: el
modelo suele repetir justo ese, y anotarlo mandaba al SKU a descansar
`ENFRIAMIENTO_DIAS` por un fallo que no fue del candidato.

Todo es SÍNCRONO (mide con `asyncio.run` adentro): se llama desde un hilo o desde
un `main()` síncrono, nunca desde una corrutina.
"""
from __future__ import annotations

import asyncio
import logging
import re
import statistics
import time
import unicodedata
from typing import Any, Iterable

from config import settings
from services import (
    competencia_captura,
    competencia_juez,
    competencia_scraper,
    competencia_store,
    ia_json,
    supabase_db,
)

log = logging.getLogger("omnicanal.competencia.mejora")

CANAL = competencia_juez.CANAL
TABLA = "enrich.market_termino_intento"

UMBRAL = 3              # comparables mínimos: el mismo número que exige el Radar
HISTERESIS = 2          # un candidato gana solo si suma al menos esto
DISPERSION_MAX = 3.0    # cuartil alto / cuartil bajo: la misma prueba del Radar
MAX_POR_GRUPO = 2       # candidatos que se miden por grupo y por corrida
MAX_POR_SKU = 4         # candidatos medidos en la vida de un SKU
ENFRIAMIENTO_DIAS = 60
TANDA = 20              # URLs por corrida de Apify
RECLAMO_MIN = 30        # un `propuesto` más viejo que esto se puede re-reclamar
REINTENTO_DIAS = 7      # un cero o un muro más recientes que esto no se re-pagan
RONDAS_MAX = 2          # en esta ronda, un fallo del candidato ya es su respuesta

# Estados CONCLUYENTES: el candidato ya se probó de verdad (no se vuelve a pagar,
# cuenta contra el cupo del SKU y dispara el enfriamiento). `termino_ok` y
# `sin_candidato` llevan como candidato el término ACTUAL: son la respuesta «se
# queda como está». `propuesto`, `error` y `bloqueado` NO lo son: la medición o el
# juicio no llegaron a terminar, así que el candidato queda reintentable y no
# castiga al SKU. Con una excepción, que no cabe en una lista y vive en
# `_concluyo`: un `bloqueado` que ya gastó `RONDAS_MAX` rondas se queda
# `bloqueado` —es lo que se sabe de él— pero cuenta como concluyente. Un `error`
# en esa ronda no se queda: pasa a `sin_mejora` (ver `_resolver`).
_PROBADOS = ("medido", "sin_mejora", "aceptado", "descartado", "termino_ok", "sin_candidato")
_NO_CONCLUYENTES = ("propuesto", "error", "bloqueado")

_SYSTEM = """Eres experto en búsqueda de Mercado Libre México. Un producto NUESTRO tiene un
término de búsqueda que NO está trayendo rivales del mismo producto. Propón
términos mejores. Respondes SOLO un objeto JSON.

Te doy: nuestros títulos, la categoría, el término actual, los rivales que SÍ eran
el mismo producto (así lo llama el mercado), los que se descartaron con su motivo
(lo que hay que EVITAR), palabras que los compradores sí teclean en esa categoría,
y los términos que ya se probaron.

REGLAS DE UN BUEN TÉRMINO:
- Es lo que teclea un comprador que busca ESTE producto: 2 a 6 palabras
  contando artículos y preposiciones, en español de México, minúsculas, con el
  orden natural del habla («casco para moto», no «casco integral moto»: las
  redacciones raras no devuelven resultados). Uno de 7 palabras se descarta.
- Nombra el producto por su sustantivo más preciso. Si el término actual es
  demasiado general («accesorios para baño», «soporte para cámara»), especifícalo.
- Puede llevar UN atributo que distinga a nuestro producto de los descartados
  (uso, tamaño, capacidad, potencia, tipo): «soporte para cámara de seguridad»,
  «parrilla de inducción empotrable».
- Sin marca, sin modelo, sin color, sin códigos.
- No repitas el término actual ni los ya probados.

CUÁNDO NO CAMBIAR NADA:
Si el término actual YA nombra exactamente nuestro producto y lo que se descartó
son sobre todo refacciones o accesorios de ese mismo producto, el problema no es
el término: responde "termino_ok": true y una lista vacía.

FORMATO (JSON):
{"diagnostico": "<por qué falla el término actual, máximo 20 palabras>",
 "termino_ok": <true|false>,
 "candidatos": [{"termino": "<texto>", "porque": "<máximo 12 palabras>"}]}
Máximo 3 candidatos, del mejor al peor."""


# ── Texto de los candidatos ──────────────────────────────────────────────────

def _compuesto(termino: Any) -> str:
    """El texto en NFC: «ñ» y «á» como UN carácter. Si llegan descompuestas (letra
    + tilde combinante), la lista blanca de `normalizar` tira la tilde y deja un
    espacio en medio de la palabra, y `llave` confunde «moño» con «mono»."""
    return unicodedata.normalize("NFC", str(termino or ""))


def llave(termino: Any) -> str:
    """Forma de COMPARAR dos términos: minúsculas, sin acentos, guiones y signos a
    espacio. «Bujías iridium», «bujias iridium» y «bujías-iridium» son la misma
    búsqueda y no deben pagarse tres veces."""
    # La ñ se aparta antes de quitar acentos: NFKD la parte en «n» + tilde, y
    # «moño» y «mono» son búsquedas distintas. Primero se COMPONE (NFC): una ñ
    # que llega ya partida no la encontraría el `replace`.
    t = _compuesto(termino).lower().replace("ñ", "\x00")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).replace("\x00", "ñ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ ]+", " ", t)).strip()


def normalizar(termino: Any) -> str | None:
    """El candidato tal como se va a buscar, o `None` si no tiene forma de término.

    Es una lista blanca: la cadena del LLM va a parar a una URL de Mercado Libre
    donde «/» y «_» son gramática (el sufijo es `_NoIndex_True`), así que solo
    pasan letras, dígitos y espacios. A diferencia de `competencia_terminos.
    _limpiar`, CONSERVA medidas y capacidades: son justo lo que separa una gama de
    otra («set sartenes 3 piezas» no es «set sartenes»)."""
    t = _compuesto(termino).lower().strip()
    t = re.sub(r"[^0-9a-záéíóúüñ ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    palabras = t.split()
    if not 2 <= len(palabras) <= 6 or len(t) > 60:
        return None
    return t


# ── Lectura: a quién hay que mejorarle el término ────────────────────────────

_SQL_UNIVERSO = """
select cfg.sku::text as sku, cfg.termino_id, cfg.termino_origen,
       st.termino, st.estado, st.medido_en,
       v.categoria_id, v.categoria_nombre, v.ruta,
       exists (select 1 from channel.listings l
                where l.sku = cfg.sku and l.canal = cfg.canal
                  and l.situacion = 'active') as activa
  from enrich.market_sku_config cfg
  join enrich.market_search_term st on st.id = cfg.termino_id
  join enrich.market_skus_v v on v.sku = cfg.sku and v.canal = cfg.canal
 where cfg.canal = %(canal)s and coalesce(cfg.activo, true)
   and (%(skus)s::citext[] is null or cfg.sku = any(%(skus)s::citext[]))
"""

_SQL_HISTORIA = f"""
select sku::text as sku, termino_candidato, termino_anterior, estado, ronda, creado_en,
       resuelto_en
  from {TABLA} where canal = %s and sku = any(%s::citext[])
"""


def _historia(skus: list[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    if skus:
        for f in supabase_db.fetch_all(_SQL_HISTORIA, (CANAL, skus)):
            out.setdefault(f["sku"], []).append(f)
    return out


def _concluyo(x: dict[str, Any]) -> bool:
    """¿Este intento ya dio su respuesta? Lo de `_PROBADOS`, más un `bloqueado`
    de la ronda `RONDAS_MAX`: a un muro siempre se le cuenta la ronda, así que
    en esa ronda `_resolver` ya lo cerró."""
    return x["estado"] in _PROBADOS or (
        x["estado"] == "bloqueado" and (x.get("ronda") or 1) >= RONDAS_MAX)


def _reciente(x: dict[str, Any]) -> bool:
    """Un `error` o un `bloqueado` de los últimos `REINTENTO_DIAS` que NO
    concluyó: ESPERA su reintento. No se le vuelve a proponer al modelo todavía:
    con temperatura 0 lo repetiría en cada corrida y cada clic, y se pagaría otra
    vez lo mismo. Un muro que ya gastó `RONDAS_MAX` rondas no espera nada."""
    return (x["estado"] in ("error", "bloqueado") and not _concluyo(x)
            and x.get("creado_en") is not None
            and time.time() - x["creado_en"].timestamp() < REINTENTO_DIAS * 86400)


def elegibles(skus: Iterable[str] | None = None, *, solo_activos: bool = True,
              incluir_manuales: bool = False, max_dias: int | None = 45,
              respetar_enfriamiento: bool = True) -> dict[str, Any]:
    """Los grupos (por término) de SKUs a los que vale la pena buscarles otro
    término, y POR QUÉ se dejó fuera a los demás.

    → {grupos: [{termino_id, termino, categoria_id, categoria_nombre, ruta,
                 skus: [{sku, titulo, resumen, filas, intentados, en_espera}]}],
       fuera: {motivo: n}}

    `en_espera`: el SKU tiene un candidato que no concluyó y aguarda su
    reintento (ver `_reciente`). `mejorar` lo usa para no enfriarlo por eso.
    """
    pedido = sorted({s for s in (skus or []) if s}) or None
    universo = supabase_db.fetch_all(_SQL_UNIVERSO, {"canal": CANAL, "skus": pedido})
    fuera: dict[str, int] = {}

    def no(motivo: str) -> None:
        fuera[motivo] = fuera.get(motivo, 0) + 1

    candidatos = []
    for u in universo:
        if solo_activos and not u["activa"]:
            no("sin publicación activa")
        elif u["termino_origen"] == "manual" and not incluir_manuales:
            no("término corregido a mano")
        elif u["estado"] == "bloqueado":
            no("término bloqueado: toca re-medir, no cambiar")
        elif u["medido_en"] is None:
            no("término sin medir")
        elif max_dias is not None and (time.time() - u["medido_en"].timestamp()) > max_dias * 86400:
            no("medición vieja: toca re-medir")
        else:
            candidatos.append(u)

    por_sku: dict[str, list[dict[str, Any]]] = {}
    for f in competencia_juez.filas([u["sku"] for u in candidatos]):
        por_sku.setdefault(f["sku"], []).append(f)
    historia = _historia([u["sku"] for u in candidatos])

    grupos: dict[int, dict[str, Any]] = {}
    for u in candidatos:
        fs = por_sku.get(u["sku"]) or []
        r = competencia_juez.resumen(fs)
        h = historia.get(u["sku"], [])
        if not r["total"]:
            no("la búsqueda no trajo rivales")
        elif not r["completo"]:
            no("rivales sin juzgar: primero el juez")
        elif r["comparables"] >= UMBRAL:
            no("ya tiene suficientes comparables")
        elif any(x["estado"] == "medido" for x in h):
            no("ya tiene una sugerencia esperando")
        elif sum(1 for x in h if _concluyo(x)) >= MAX_POR_SKU:
            no("agotado: ya se probaron los candidatos permitidos")
        elif respetar_enfriamiento and any(
                _concluyo(x) and x["resuelto_en"]
                and (time.time() - x["resuelto_en"].timestamp()) < ENFRIAMIENTO_DIAS * 86400
                for x in h):
            no("en enfriamiento")
        elif not fs[0]["titulo_nuestro"]:
            no("sin título nuestro")
        else:
            g = grupos.setdefault(u["termino_id"], {
                "termino_id": u["termino_id"], "termino": u["termino"],
                "categoria_id": u["categoria_id"], "categoria_nombre": u["categoria_nombre"],
                "ruta": u["ruta"], "skus": []})
            g["skus"].append({
                "sku": u["sku"], "titulo": fs[0]["titulo_nuestro"], "resumen": r, "filas": fs,
                # Lo CONCLUYENTE, para siempre; un error o un bloqueo, solo
                # mientras es reciente: pasado `REINTENTO_DIAS` se puede volver a
                # proponer (no se probó de verdad).
                "intentados": sorted({llave(x["termino_candidato"]) for x in h
                                      if _concluyo(x) or _reciente(x)}
                                     | {llave(x["termino_anterior"]) for x in h
                                        if x["termino_anterior"]}),
                "en_espera": any(_reciente(x) for x in h)})
    return {"grupos": list(grupos.values()), "fuera": fuera}


def _tendencias(categoria_id: str | None, limite: int = 15) -> list[str]:
    if not categoria_id:
        return []
    try:
        fs = supabase_db.fetch_all(
            "select e.termino from enrich.market_terms t, "
            "       jsonb_array_elements_text(t.terminos) with ordinality as e(termino, ord) "
            " where t.canal = %s and t.categoria_id = %s order by e.ord limit %s",
            (CANAL, categoria_id, limite))
        return [f["termino"] for f in fs]
    except Exception as exc:                                        # noqa: BLE001
        log.warning("mejora: no se pudieron leer las tendencias de %s: %s", categoria_id, exc)
        return []


# ── Propuesta ────────────────────────────────────────────────────────────────

def armar_usuario(grupo: dict[str, Any], tendencias: list[str]) -> str:
    """El mensaje de un grupo. Los títulos de rivales viajan al LLM pero NO se
    guardan ni se registran en ningún log: el repo y sus bitácoras no llevan
    datos de terceros."""
    titulos = list(dict.fromkeys(s["titulo"] for s in grupo["skus"]))[:5]
    buenos: list[str] = []
    malos: list[str] = []
    for s in grupo["skus"]:
        for f in s["filas"]:
            if not f["cuenta"]:
                continue
            if f["comparable"]:
                buenos.append(f["titulo"])
            elif f["vigente"]:
                malos.append(f"[{f['clase']}] {f['titulo']}")
    intentados = sorted({t for s in grupo["skus"] for t in s["intentados"]})
    lineas = ["NUESTROS TÍTULOS"] + [f"- {t}" for t in titulos]
    if grupo.get("categoria_nombre"):
        lineas += ["", f"CATEGORÍA: {grupo.get('ruta') or grupo['categoria_nombre']}"]
    lineas += ["", f"TÉRMINO ACTUAL: {grupo['termino']}", "",
               "RIVALES QUE SÍ ERAN EL MISMO PRODUCTO"]
    lineas += [f"- {t[:120]}" for t in list(dict.fromkeys(buenos))[:6]] or ["- (ninguno)"]
    lineas += ["", "DESCARTADOS (lo que hay que evitar)"]
    lineas += [f"- {t[:130]}" for t in list(dict.fromkeys(malos))[:12]] or ["- (ninguno)"]
    if tendencias:
        lineas += ["", "BÚSQUEDAS CON DEMANDA EN LA CATEGORÍA: " + " · ".join(tendencias)]
    if intentados:
        lineas += ["", "YA PROBADOS (no repetir): " + " · ".join(intentados)]
    lineas += ["", "Responde el JSON."]
    return "\n".join(lineas)


def proponer(grupo: dict[str, Any], *, modelo: str) -> dict[str, Any]:
    """Candidatos para un grupo. → {ok, termino_ok, diagnostico, candidatos:
    [{termino, motivo}], usd, proveedor, motivo}. No mide ni escribe."""
    res = ia_json.completar_json(
        _SYSTEM, armar_usuario(grupo, _tendencias(grupo.get("categoria_id"))),
        modelo=modelo, max_tokens=500)
    base = {"ok": bool(res.get("ok")), "usd": float(res.get("usd") or 0),
            "proveedor": res.get("proveedor"), "termino_ok": False, "diagnostico": "",
            "candidatos": [], "motivo": res.get("motivo")}
    if not res.get("ok"):
        return base
    d = res["datos"]
    vistos = {llave(grupo["termino"])} | {t for s in grupo["skus"] for t in s["intentados"]}
    candidatos = []
    for c in d.get("candidatos") if isinstance(d.get("candidatos"), list) else []:
        if not isinstance(c, dict):
            continue
        t = normalizar(c.get("termino"))
        if not t or llave(t) in vistos:
            continue
        vistos.add(llave(t))
        candidatos.append({"termino": t, "motivo": str(c.get("porque") or "").strip()[:160]})
    return {**base, "termino_ok": d.get("termino_ok") is True and not candidatos,
            "diagnostico": str(d.get("diagnostico") or "").strip()[:200],
            "candidatos": candidatos[:MAX_POR_GRUPO]}


# ── Escritura de intentos ────────────────────────────────────────────────────

def _reclamar(sku: str, anterior: dict[str, Any], candidato: str, motivo: str,
              r: dict[str, Any], modelo: str, estado: str = "propuesto") -> tuple[int, int] | None:
    """Anota el intento ANTES de gastar. → (id, ronda); `None` = otro proceso ya
    lo tiene, o esa fila ya concluyó."""
    f = supabase_db.execute_returning(
        f"insert into {TABLA} (sku, canal, termino_anterior, termino_anterior_id, "
        "   termino_candidato, motivo, estado, comparables_antes, total_antes, modelo, "
        "   resuelto_en) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "        case when %s = 'propuesto' then null else now() end) "
        "on conflict (sku, canal, termino_candidato) do update "
        "   set creado_en = now(), motivo = excluded.motivo, estado = excluded.estado, "
        "       resuelto_en = excluded.resuelto_en, resuelto_por = null, "
        # El término del SKU pudo cambiar desde el intento anterior (una persona
        # lo corrigió, o se aceptó otra sugerencia): la fila tiene que llevar el
        # de HOY, o `aceptar` la daría por vieja y la descartaría.
        "       termino_anterior = excluded.termino_anterior, "
        "       termino_anterior_id = excluded.termino_anterior_id, "
        "       comparables_antes = excluded.comparables_antes, "
        "       total_antes = excluded.total_antes, modelo = excluded.modelo, "
        "       comparables_despues = null, total_despues = null, "
        # Una ronda más, salvo que se esté recogiendo un `propuesto` huérfano:
        # ese intento nunca llegó a medirse.
        f"      ronda = {TABLA}.ronda + case when {TABLA}.estado = 'propuesto' then 0 else 1 end "
        # Solo se re-reclama lo que NO es concluyente: un `propuesto` que quedó
        # colgado (el proceso murió a media medición), un `error`, o un
        # `bloqueado` al que aún le quedan rondas.
        f" where ({TABLA}.estado = 'propuesto' "
        f"        and {TABLA}.creado_en < now() - interval '{RECLAMO_MIN} minutes') "
        f"    or {TABLA}.estado = 'error' "
        f"    or ({TABLA}.estado = 'bloqueado' and {TABLA}.ronda < {RONDAS_MAX}) "
        "returning id, ronda",
        (sku, CANAL, anterior["termino"], anterior["termino_id"], candidato, motivo or None,
         estado, r["comparables"], r["total"], modelo, estado))
    return (int(f["id"]), int(f.get("ronda") or 1)) if f else None


def _resolver(intento_id: int, estado: str, *, termino_id: int | None = None,
              despues: dict[str, Any] | None = None, ronda: int | None = None) -> str:
    """Cierra un intento y devuelve el estado con que quedó.

    `ronda` se pasa SOLO cuando el fallo es del candidato: cero filas, el muro
    de ML, o un juicio que corrió entero y aun así dejó rivales sin contestar.
    Lo que falla de nuestro lado (la tanda de Apify que truena, el tope de gasto,
    el proveedor caído) no gasta ronda: el reclamo ya la había sumado, así que
    aquí se DEVUELVE. Sin eso, un candidato cuya primera ronda tronó de nuestro
    lado llegaba a la última con una sola prueba de verdad. En la ronda
    `RONDAS_MAX` un fallo del candidato ya es la respuesta: un `error` se cierra
    como `sin_mejora` y un `bloqueado` se queda `bloqueado` pero con fecha, que
    es lo que lo vuelve concluyente (`_concluyo`)."""
    cierra = ronda is not None and ronda >= RONDAS_MAX and estado in ("error", "bloqueado")
    if cierra and estado == "error":
        estado = "sin_mejora"
    devuelve = estado == "error" and ronda is None
    supabase_db.execute(
        f"update {TABLA} set estado = %s, termino_candidato_id = coalesce(%s, termino_candidato_id), "
        "   comparables_despues = %s, total_despues = %s, "
        "   resuelto_en = case when %s then now() else null end "
        + ("   , ronda = ronda - 1 " if devuelve else "")
        + " where id = %s",
        (estado, termino_id, (despues or {}).get("comparables"), (despues or {}).get("total"),
         cierra or estado not in _NO_CONCLUYENTES, intento_id))
    return estado


def _anotar_desenlace(sku: str, g: dict[str, Any], motivo: str, r: dict[str, Any],
                      modelo: str, estado: str) -> bool:
    """`termino_ok` o `sin_candidato`: la corrida no deja nada que medir. Se anota
    con el término ACTUAL como candidato para que el SKU descanse.

    Esa fila puede existir ya (un desenlace anterior, o el término actual fue una
    sugerencia aceptada). Entonces solo se RENUEVA su fecha —y su estado, si era
    otro desenlace—: sin eso, pasado el enfriamiento el SKU volvía a ser elegible
    y se le volvía a pagar la propuesta en cada corrida. Un `aceptado` conserva
    su estado. → ¿quedó anotado?"""
    if _reclamar(sku, g, g["termino"], motivo, r, modelo, estado=estado):
        return True
    return bool(supabase_db.execute(
        f"update {TABLA} set resuelto_en = now(), "
        "   estado = case when estado in ('termino_ok', 'sin_candidato') then %s else estado end, "
        "   motivo = case when estado in ('termino_ok', 'sin_candidato') then %s else motivo end "
        " where sku = %s and canal = %s and termino_candidato = %s "
        "   and estado in ('termino_ok', 'sin_candidato', 'aceptado', 'sin_mejora', 'descartado')",
        (estado, motivo or None, sku, CANAL, g["termino"])))


def _catalogo() -> dict[str, dict[str, Any]]:
    """El catálogo de términos por `llave`: para reusar una medición que ya se
    pagó aunque difiera en mayúsculas o acentos. Si hay gemelos, gana el medido
    más reciente."""
    out: dict[str, dict[str, Any]] = {}
    for f in supabase_db.fetch_all(
            "select id, termino, estado, medido_en, resultados from enrich.market_search_term "
            " where canal = %s order by medido_en nulls first", (CANAL,)):
        out[llave(f["termino"])] = f
    return out


def _sin_dispersion(precios: list[float]) -> bool:
    """La misma prueba del Radar: si el cuartil alto vale más de tres veces el
    bajo, esos «comparables» no describen un solo producto."""
    if len(precios) < UMBRAL:
        return False
    q1, _, q3 = statistics.quantiles(sorted(precios), n=4, method="inclusive")
    return q1 > 0 and q3 / q1 <= DISPERSION_MAX


# ── La corrida ───────────────────────────────────────────────────────────────

def mejorar(grupos: list[dict[str, Any]], *, presupuesto: competencia_juez.Presupuesto,
            max_paginas: int = 40, modelo: str | None = None,
            reuso_dias: int | None = 30, timeout: float = 120.0,
            intentos: int = 3) -> dict[str, Any]:
    """Propone, mide, juzga y deja sugerencias para esos grupos. SÍNCRONA.

    Dos topes: `presupuesto` (USD de IA, acumulativo) y `max_paginas` (búsquedas
    nuevas en Apify: es lo caro, y su costo real solo se sabe al liquidar). Lo que
    no alcance se dice en `sin_cubrir`. `timeout` e `intentos` van al juez de los
    candidatos: el script por lotes aguanta los largos; el botón pide cortos.

    `usd_ia` es todo lo que se gastó en IA (propuestas + juez de candidatos) y
    `usd_propuestas`, solo las propuestas: es lo que va como `usd` a la bitácora.
    `en_espera` son SKUs cuya propuesta no dejó nada nuevo mientras un candidato
    suyo aguarda su reintento: a diferencia de `sin_candidato`, no se anotan.
    """
    t0 = time.monotonic()
    modelo = modelo or settings.competencia_juez_modelo
    out: dict[str, Any] = {
        "grupos": len(grupos), "skus": sum(len(g["skus"]) for g in grupos),
        "termino_ok": 0, "sin_candidato": 0, "en_espera": 0, "paginas": 0, "reusados": 0,
        "bloqueados": 0,
        "errores": 0, "sugerencias": 0, "sin_mejora": 0, "ocupados": 0,
        "usd_ia": 0.0, "usd_propuestas": 0.0,
        "sin_cubrir": 0, "detenido": None, "modelo": modelo, "detalle": []}
    if modelo not in ia_json.MODELOS:
        out["detenido"] = f"modelo sin precio: {modelo}"
        return out
    if grupos and not competencia_scraper.disponible():
        # Sin Apify la medición vuelve vacía y el candidato quedaría quemado como
        # «sin mejora» sin haberse probado nunca.
        out["detenido"] = "Apify no está disponible (falta APIFY_API_KEY)"
        return out

    fallo = True
    try:
        catalogo = _catalogo()
        plan: list[dict[str, Any]] = []   # {grupo, candidato, motivo, intentos:{sku:id},
        #                                     rondas:{sku:n}, tid}
        por_medir: list[str] = []
        # El texto elegido para cada llave en ESTA corrida. El catálogo se leyó al
        # empezar y no sabe que dos grupos pidieron lo mismo con y sin acento: sin
        # esto se pagaban dos páginas y quedaban dos términos gemelos.
        planeados: dict[str, str] = {}

        # ── 1. Proponer y reclamar ──────────────────────────────────────────────
        for k, g in enumerate(grupos):
            tope = ("tope de gasto de IA" if not presupuesto.puede() else
                    "tope de páginas de Apify" if len(por_medir) >= max_paginas > 0 else None)
            if tope:
                # Sin presupuesto o sin páginas ya no tiene caso seguir PROPONIENDO:
                # cada propuesta cuesta y su candidato no se podría medir. Se SUMA a
                # lo que ya quedó sin cubrir y se conserva el primer motivo.
                out["detenido"] = out["detenido"] or tope
                out["sin_cubrir"] += sum(len(x["skus"]) for x in grupos[k:])
                break
            p = proponer(g, modelo=modelo)
            presupuesto.sumar(p["usd"])
            out["usd_propuestas"] += p["usd"]
            out["usd_ia"] += p["usd"]
            if not p["ok"]:
                out["errores"] += 1
                continue
            if p["termino_ok"] or not p["candidatos"]:
                # «El término está bien» o «no hay nada nuevo que probar»: no se
                # mide nada, pero se ANOTA para que el SKU descanse en vez de volver
                # a pagarse la misma pregunta en cada corrida.
                if not p["termino_ok"] and any(s.get("en_espera") for s in g["skus"]):
                    # Salvo que el grupo tenga un candidato esperando su reintento.
                    # Con temperatura 0 el modelo casi siempre repite justo ese, y
                    # `proponer` lo filtra: «nada nuevo» ahí no es una respuesta.
                    # Anotarlo enfriaba al SKU `ENFRIAMIENTO_DIAS` por una tanda de
                    # Apify caída y le quitaba su segunda ronda a un cero o un muro.
                    # Va por GRUPO porque lo que se filtra es la unión de todos. No
                    # se escribe nada: cuesta una propuesta por corrida y, pasado
                    # `REINTENTO_DIAS`, el candidato vuelve a proponerse y se mide.
                    out["en_espera"] += len(g["skus"])
                    continue
                estado = "termino_ok" if p["termino_ok"] else "sin_candidato"
                out[estado] += sum(_anotar_desenlace(s["sku"], g, p["diagnostico"], s["resumen"],
                                                     modelo, estado) for s in g["skus"])
                continue
            sin_cupo, atendido = False, False
            for c in p["candidatos"]:
                conocido = catalogo.get(llave(c["termino"]))
                texto = (conocido["termino"] if conocido
                         else planeados.setdefault(llave(c["termino"]), c["termino"]))
                edad = (time.time() - conocido["medido_en"].timestamp()
                        if conocido and conocido["medido_en"] else None)
                reusable = bool(conocido and conocido["estado"] == "ok" and edad is not None
                                and (reuso_dias is None or edad <= reuso_dias * 86400))
                # Un cero o un muro RECIENTES del catálogo ya son la respuesta:
                # medirlos otra vez es pagar la misma página para oír lo mismo.
                reciente = (conocido["estado"] if conocido and edad is not None
                            and conocido["estado"] in ("vacio", "bloqueado")
                            and edad <= REINTENTO_DIAS * 86400 else None)
                if (not reusable and not reciente and texto not in por_medir
                        and len(por_medir) >= max_paginas):
                    # Este candidato ya no cabe en el tope de páginas. El grupo
                    # sigue con los que sí (uno reusable es gratis), pero se dice.
                    out["detenido"] = out["detenido"] or "tope de páginas de Apify"
                    sin_cupo = True
                    continue
                reclamados: dict[str, int] = {}
                rondas: dict[str, int] = {}
                for s in g["skus"]:
                    rec = _reclamar(s["sku"], g, texto, c["motivo"], s["resumen"], modelo)
                    if rec:
                        reclamados[s["sku"]], rondas[s["sku"]] = rec
                out["ocupados"] += len(g["skus"]) - len(reclamados)
                if not reclamados:
                    # Otro proceso lo tiene, o quedó `propuesto` en una corrida que
                    # murió a la mitad. Sin decirlo, el botón acababa en «la IA no
                    # propuso nada», que es falso.
                    out["detenido"] = out["detenido"] or (
                        "otro proceso ya está probando ese candidato (o quedó a medias): "
                        f"reintenta en {RECLAMO_MIN} min")
                    continue
                atendido = True
                if reciente == "vacio":
                    for iid in reclamados.values():
                        _resolver(iid, "sin_mejora", termino_id=conocido["id"],
                                  despues={"comparables": 0, "total": 0})
                    out["sin_mejora"] += len(reclamados)
                elif reciente == "bloqueado":
                    # Sin pagar, pero con su ronda: el muro es del término.
                    out["bloqueados"] += 1
                    for sku, iid in reclamados.items():
                        _resolver(iid, "bloqueado", ronda=rondas[sku])
                else:
                    plan.append({"grupo": g, "candidato": texto, "motivo": c["motivo"],
                                 "intentos": reclamados, "rondas": rondas,
                                 "tid": conocido["id"] if reusable else None})
                    if reusable:
                        out["reusados"] += 1
                    elif texto not in por_medir:
                        por_medir.append(texto)
            if sin_cupo and not atendido:
                # UNA vez por grupo, y solo si ninguno de sus candidatos se atendió.
                out["sin_cubrir"] += len(g["skus"])

        # ── 2. Medir lo nuevo, en tandas ────────────────────────────────────────
        if por_medir and not presupuesto.puede():
            # Las propuestas se comieron el tope: el juez ya no correría y medir
            # sería pagar Apify por candidatos que nadie va a evaluar. Se sueltan
            # como `error` —sin gastar ronda: no es culpa del candidato— y se dice.
            out["detenido"] = out["detenido"] or "tope de gasto de IA"
            sueltos = [x for x in plan if x["tid"] is None]
            plan = [x for x in plan if x["tid"] is not None]
            for x in sueltos:
                for iid in x["intentos"].values():
                    _resolver(iid, "error")
            out["sin_cubrir"] += len({s for x in sueltos for s in x["intentos"]}
                                     - {s for x in plan for s in x["intentos"]})
            por_medir = []
        bloqueados: set[str] = set()
        medidos: dict[str, int] = {}
        for ini in range(0, len(por_medir), TANDA):
            lote = por_medir[ini:ini + TANDA]
            try:
                medidos.update(asyncio.run(
                    competencia_captura.medir_busquedas(lote, bloqueados=bloqueados)))
            except Exception as exc:                                    # noqa: BLE001
                log.warning("mejora: la tanda de medición falló: %s", exc)
            out["paginas"] += len(lote)

        # ── 3. Juzgar cada candidato contra cada SKU y decidir ──────────────────
        mejor: dict[str, dict[str, Any]] = {}
        for item in plan:
            cand, tid, rondas = item["candidato"], item["tid"], item["rondas"]
            if tid is None:
                if cand in bloqueados:
                    out["bloqueados"] += 1
                    for sku, iid in item["intentos"].items():
                        _resolver(iid, "bloqueado", ronda=rondas[sku])
                    continue
                tid = supabase_db.fetch_scalar(
                    "select id from enrich.market_search_term "
                    " where canal = %s and termino = %s and estado = 'ok' and resultados > 0",
                    (CANAL, cand)) if medidos.get(cand) else None
                if not tid:
                    # Cero filas sin bloqueo: puede ser que ML no tenga nada o que la
                    # corrida de Apify no volviera. No se puede distinguir desde aquí,
                    # así que la primera vez NO es concluyente y queda reintentable;
                    # en la ronda `RONDAS_MAX` ya es la respuesta. Si la tanda entera
                    # tronó, el término ni volvió: eso no gasta ronda.
                    cuenta = medidos.get(cand) == 0
                    finales = [_resolver(iid, "error", ronda=rondas[sku] if cuenta else None)
                               for sku, iid in item["intentos"].items()]
                    out["sin_mejora"] += finales.count("sin_mejora")
                    out["errores"] += int("error" in finales)
                    continue
            skus = sorted(item["intentos"])
            rj = competencia_juez.juzgar_skus(skus, presupuesto=presupuesto, termino_id=tid,
                                              modelo=modelo, timeout=timeout, intentos=intentos)
            out["usd_ia"] += float(rj.get("usd") or 0)
            if rj.get("detenido"):
                out["detenido"] = out["detenido"] or f"juez: {rj['detenido']}"
            # Un juicio que corrió entero —sin tope ni plazo, sin fallos del
            # proveedor ni de la base— y aun así dejó rivales sin contestar es un
            # fallo del candidato: ese sí gasta ronda.
            limpio = not (rj.get("detenido") or rj.get("fallidos") or rj.get("sin_guardar"))
            por_sku: dict[str, list[dict[str, Any]]] = {}
            for f in competencia_juez.filas(skus, tid):
                por_sku.setdefault(f["sku"], []).append(f)
            antes = {s["sku"]: s["resumen"] for s in item["grupo"]["skus"]}
            for sku in skus:
                fs = por_sku.get(sku) or []
                r = competencia_juez.resumen(fs)
                if r["pendientes"]:
                    # El juez no alcanzó a juzgar todos los rivales del candidato
                    # (tope, proveedor caído, un rival sin contestar). «Sin juzgar» es
                    # «no sé»: NO se cierra como sin_mejora —eso quemaría un candidato
                    # ya pagado en Apify sin haberlo evaluado— sino como `error`,
                    # que queda reintentable y no castiga al SKU.
                    #
                    # Cero pendientes con cero rivales contables NO es esto: el
                    # candidato sí se probó y solo trae lo nuestro o sin precio. Esa
                    # es una respuesta, y se cierra abajo como sin_mejora.
                    final = _resolver(item["intentos"][sku], "error", termino_id=tid, despues=r,
                                      ronda=rondas[sku] if limpio else None)
                    out["errores" if final == "error" else "sin_mejora"] += 1
                    continue
                precios = [float(f["precio"]) for f in fs if f["comparable"]]
                gana = (r["comparables"] >= UMBRAL
                        and r["comparables"] >= antes[sku]["comparables"] + HISTERESIS
                        and _sin_dispersion(precios))
                anterior = mejor.get(sku)
                if gana and (not anterior or r["comparables"] > anterior["resumen"]["comparables"]):
                    if anterior:
                        _resolver(anterior["iid"], "sin_mejora", termino_id=anterior["tid"],
                                  despues=anterior["resumen"])
                        out["sin_mejora"] += 1
                    mejor[sku] = {"iid": item["intentos"][sku], "tid": tid, "resumen": r,
                                  "termino": cand, "antes": antes[sku]["comparables"]}
                else:
                    _resolver(item["intentos"][sku], "sin_mejora", termino_id=tid, despues=r)
                    out["sin_mejora"] += 1
        for sku, m in mejor.items():
            _resolver(m["iid"], "medido", termino_id=m["tid"], despues=m["resumen"])
            out["sugerencias"] += 1
            out["detalle"].append({"sku": sku, "termino": m["termino"], "antes": m["antes"],
                                   "despues": m["resumen"]["comparables"]})
        fallo = False

    finally:
        # En un `finally`: si algo revienta a media corrida, las propuestas y las
        # páginas de Apify ya se pagaron y tienen que quedar en la bitácora, y con
        # estado `error`: una corrida que murió no terminó «ok».
        out["usd_ia"] = round(out["usd_ia"], 6)
        out["usd_propuestas"] = round(out["usd_propuestas"], 6)
        out["duracion_s"] = round(time.monotonic() - t0, 1)
        # El `usd` de esta fila son SOLO las propuestas: lo que gastó el juez en
        # los candidatos ya lo registró `juzgar_skus` en su propia fila 'juez', y
        # quien sumara las dos lo contaría dos veces. Va aparte, como `usd_juez`.
        competencia_juez.registrar(
            "terminos", "error" if fallo else "parcial" if out["detenido"] else "ok",
            {k: v for k, v in out.items() if k not in ("detalle", "usd_ia")}
            | {"usd": out["usd_propuestas"],
               "usd_juez": round(out["usd_ia"] - out["usd_propuestas"], 6)},
            out["duracion_s"])
    return out


def mejorar_sku(sku: str, *, presupuesto: competencia_juez.Presupuesto,
                incluir_manuales: bool = True, timeout: float = 30.0,
                intentos: int = 2) -> dict[str, Any]:
    """Lo mismo para UN SKU, desde el botón del panel. Devuelve además por qué no
    se hizo nada, si no se hizo nada.

    Con plazos CORTOS para el juez: el botón comparte el turno de la IA con la
    ruta en línea, y con los del script (120 s × 3) un proveedor colgado lo
    retenía hasta ~6 min."""
    sku = (sku or "").strip()
    if not sku:
        # `elegibles` lee una lista vacía como «todos»: un SKU en blanco lanzaba
        # la mejora sobre el catálogo entero.
        return {"ok": False, "motivo": "falta el SKU", "sugerencias": 0}
    e = elegibles([sku], solo_activos=False, incluir_manuales=incluir_manuales, max_dias=None)
    if not e["grupos"]:
        return {"ok": False, "motivo": next(iter(e["fuera"]), "no es elegible"), "sugerencias": 0}
    return {"ok": True, **mejorar(e["grupos"], presupuesto=presupuesto, max_paginas=MAX_POR_GRUPO,
                                  timeout=timeout, intentos=intentos)}


# ── La sugerencia y su aceptación ────────────────────────────────────────────

def sugerencia(sku: str) -> dict[str, Any] | None:
    """La sugerencia ABIERTA de un SKU (el intento `medido`), o `None`."""
    return supabase_db.fetch_one(
        f"select id, termino_anterior, termino_candidato, motivo, comparables_antes, "
        f"       total_antes, comparables_despues, total_despues, creado_en "
        f"  from {TABLA} where sku = %s and canal = %s and estado = 'medido' "
        "  order by comparables_despues desc nulls last, creado_en desc limit 1", (sku, CANAL))


def aceptar(intento_id: int, quien: str | None = None) -> dict[str, Any]:
    """Una persona acepta la sugerencia: el término del SKU cambia y queda como
    corrección MANUAL (nadie automático la pisa después).

    Se niega —sin tocar nada— si la sugerencia ya no está abierta o si el término
    del SKU cambió desde que se sugirió: una sugerencia vieja no debe pisar una
    corrección más nueva."""
    i = supabase_db.fetch_one(
        f"select i.id, i.sku::text as sku, i.estado, i.termino_anterior_id, "
        f"       i.termino_candidato, cfg.termino_id as termino_actual_id "
        f"  from {TABLA} i left join enrich.market_sku_config cfg "
        "         on cfg.sku = i.sku and cfg.canal = i.canal where i.id = %s", (intento_id,))
    if not i:
        return {"ok": False, "codigo": 404, "motivo": "Esa sugerencia no existe."}
    if i["estado"] != "medido":
        return {"ok": False, "codigo": 409, "motivo": "Esa sugerencia ya no está abierta."}
    if i["termino_actual_id"] != i["termino_anterior_id"]:
        _cerrar(intento_id, "descartado", quien)
        return {"ok": False, "codigo": 409,
                "motivo": "El término del SKU cambió después de la sugerencia; se descartó."}
    if not competencia_store.actualizar_termino(i["sku"], i["termino_candidato"]):
        return {"ok": False, "codigo": 409, "motivo": "No se pudo cambiar el término del SKU."}
    _cerrar(intento_id, "aceptado", quien)
    supabase_db.execute(
        f"update {TABLA} set estado = 'descartado', resuelto_en = now(), resuelto_por = %s "
        " where sku = %s and canal = %s and estado = 'medido' and id <> %s",
        (quien, i["sku"], CANAL, intento_id))
    return {"ok": True, "sku": i["sku"], "termino": i["termino_candidato"]}


def descartar(intento_id: int, quien: str | None = None) -> dict[str, Any]:
    n = _cerrar(intento_id, "descartado", quien, solo_abierta=True)
    return ({"ok": True} if n else
            {"ok": False, "codigo": 409, "motivo": "Esa sugerencia ya no está abierta."})


def _cerrar(intento_id: int, estado: str, quien: str | None, solo_abierta: bool = False) -> int:
    return supabase_db.execute(
        f"update {TABLA} set estado = %s, resuelto_en = now(), resuelto_por = %s where id = %s"
        + (" and estado = 'medido'" if solo_abierta else ""), (estado, quien, intento_id))
