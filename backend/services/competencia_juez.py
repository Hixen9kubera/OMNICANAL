"""
competencia_juez.py — ¿Este «rival» compite de verdad con NUESTRO producto?

── POR QUÉ EXISTE ─────────────────────────────────────────────────────────────
Porque la búsqueda de Mercado Libre devuelve lo que comparte PALABRAS con el
término, no lo que compite con el producto. Caso que lo destapó (29-sep-2026,
zancos para yesero de $2,166): de sus 10 «rivales», 8 eran refacciones de $142 a
$500. El «mínimo del mercado» eran unas correas.

No era un caso raro. Examen de 90 SKUs al azar, 774 rivales, etiquetados por dos
agentes de IA independientes con desempate (NO por personas del equipo: es una
vara razonable, no la verdad revelada):

    mismo producto ........ 48 %      otro producto ... 17 %
    otra gama ............. 23 %      otro paquete .....  7 %
    refacción / accesorio .  3 %      dudoso ...........  1 %

La mitad de lo guardado NO sirve para comparar precio, y 26 de los 90 SKUs se
quedan con menos de 3 rivales reales.

Las reglas de texto no alcanzan: un detector por sustantivo y vocabulario de
refacciones acertó 67 % (falla en sinónimos —«auriculares» = «audífonos»—, en
usos y en paquetes —1 par ≠ 3 pares—). Eso es juicio, y por eso lo hace un LLM.

── EL VEREDICTO ES DE LA PAREJA, NO DEL RIVAL ─────────────────────────────────
`enrich.market_search_results` guarda una fila por (término, rival) y un término
lo comparten varios SKUs: el mismo rival puede ser «mismo» para un SKU y «otro
paquete» para su hermano de 3 piezas. Además esa tabla se BORRA y se reinserta en
cada medición. Por eso el veredicto vive aparte, en `enrich.market_rival_juicio`,
llaveado por (sku, canal, rival) y sin ninguna llave foránea: sobrevive a la
recaptura, y si el mismo rival reaparece bajo otro término no se vuelve a pagar.

── LAS SEIS CLASES, Y CUÁL CUENTA ─────────────────────────────────────────────
    mismo          compite de frente: mismo subtipo, función y gama.
    otro_paquete   el mismo producto con otra cantidad de unidades.
    otra_gama      mismo sustantivo, otro subtipo (tamaño, capacidad, potencia,
                   compatibilidad): comparar precios engañaría.
    refaccion      refacción, accesorio o consumible PARA el producto.
    otro_producto  solo comparte palabras o categoría.
    dudoso         el título no alcanza.

COMPARABLE = solo `mismo`. `otro_paquete` se guarda con sus unidades y se enseña,
pero NO entra a un mínimo ni a una mediana: el precio por pieza no es lineal
(descuento por volumen, umbral de envío gratis) y un paquete de 25 «normalizado»
a 3 movería la referencia hacia donde no está el mercado.

── NO INVENTA, Y NO DA POR BUENO LO VIEJO ─────────────────────────────────────
Un rival que el modelo no contestó, contestó con una clase que no existe o con un
número que nadie pidió queda SIN JUZGAR. Y un veredicto deja de valer —vuelve a la
cola— cuando cambia el título del rival, el nuestro o la versión del prompt. Quien
lee debe tratar «sin juzgar» como «no sé», nunca como «comparable».

── REGLAS DE CONCURRENCIA DEL MÓDULO ──────────────────────────────────────────
  · NUNCA hay un cursor abierto mientras se habla con el LLM: se lee lo
    pendiente, se suelta la conexión, se llama, y se guarda en una transacción
    corta por SKU. El pool de la base tiene 6 conexiones para TODO el backend.
  · Un solo semáforo de MÓDULO (`_turno`) limita las llamadas simultáneas al LLM,
    cuente quien cuente: tres capturas a la vez comparten el mismo tope. Se
    espera CON PLAZO (el `timeout` de quien llama): sin turno, «IA ocupada».
  · Es `threading`, no `asyncio`: esto corre en hilos con su propio loop y un
    semáforo de asyncio se amarra al primero (ya reventó el 29-sep-2026).
  · Todo es SÍNCRONO. Desde una corrutina, `asyncio.to_thread` (regla 11).
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable

from config import settings
from services import ia_json, supabase_db

log = logging.getLogger("omnicanal.competencia.juez")

# Cambiar el prompt cambia el criterio. La versión se guarda con cada veredicto y
# lo pendiente incluye lo que tenga otra: subirla vuelve a cobrar TODO. Subirla
# solo cuando el cambio mueva veredictos de verdad. Cambiar de MODELO no re-juzga
# nada por sí solo: si se quiere, se sube la versión.
VERSION_PROMPT = "j3"

CLASES = ("mismo", "otro_paquete", "otra_gama", "refaccion", "otro_producto", "dudoso")
CANAL = "mercado_libre"

# Rivales por llamada. El examen midió lotes de 5 a 10; listas más largas
# agrandan los efectos de posición y el riesgo de respuesta cortada, así que lo
# que pase de esto se parte en varias llamadas en vez de recortarse en silencio.
TROZO = 12

TABLA_JUICIO = "enrich.market_rival_juicio"
VISTA_TITULO = "enrich.market_sku_titulo_v"

# Llamadas simultáneas al LLM en TODO el proceso (ver reglas del módulo).
_turno = threading.BoundedSemaphore(4)
# El motivo de un lote que no consiguió turno a tiempo (ver `juzgar_lote`).
IA_OCUPADA = "IA ocupada"

# Los ejemplos de este prompt son tipos de producto GENÉRICOS a propósito:
# ninguno sale del examen, para no regalarle respuestas a la mitad con la que se
# mide, y el repo es público — aquí no van títulos ni vendedores reales.
_SYSTEM = """Eres analista de precios de un vendedor de Mercado Libre México.
Recibes UN producto NUESTRO y una lista numerada de publicaciones. Decides, para
CADA una, si sirve para comparar PRECIO contra nuestro producto. Respondes SOLO un
objeto JSON.

PASO 1 — Define en "nuestro" qué es EXACTAMENTE nuestro producto: su subtipo, su
mecanismo o forma de uso, su tamaño o capacidad, para qué modelo o uso es y qué
incluye. Ese subtipo es la vara con la que mides a cada rival.

PASO 2 — Para cada rival recorre estas preguntas EN ORDEN y quédate con la primera
que se cumpla:
1. ¿Es otra cosa, que solo comparte palabras o categoría? -> "otro_producto".
2. ¿Es una refacción, repuesto, accesorio o consumible PARA un producto como el
   nuestro (cuchillas para licuadora, correa para reloj, funda para sillón,
   líquido limpiador para lentes)? -> "refaccion". Excepción: si NUESTRO producto
   es en sí esa refacción o accesorio, los que venden lo mismo son "mismo".
3. ¿Comparte el sustantivo pero es OTRO subtipo: otro mecanismo o forma de uso,
   otro tamaño o capacidad claramente distintos, otra potencia, hecho para otro
   modelo o marca, o le falta (o le sobra) lo que define al nuestro? ->
   "otra_gama". Ejemplos: lámpara de escritorio contra lámpara de techo; mochila
   de viaje de 60 L contra mochila escolar; taladro inalámbrico de 12 V contra
   rotomartillo industrial; funda para un modelo de tableta contra funda para
   otro modelo; repuesto exclusivo de una marca contra uno universal; el producto
   suelto contra un sistema o kit completo que lo incluye.
4. ¿No se puede saber qué es ni con el título completo? -> "dudoso".
5. En cualquier otro caso es "mismo": el mismo subtipo, con la misma función y una
   gama comparable; quien busca el nuestro lo compraría en su lugar.

REGLAS:
- Otra marca, color, diseño o material NO descalifican. Tampoco el precio: un
  competidor más barato o más caro sigue siendo competidor.
- Sé estricto con el subtipo, pero no exijas que sea idéntico: pequeñas
  diferencias de medida o de acabado siguen siendo "mismo".
- Los títulos de Mercado Libre amontonan palabras clave («funda/carcasa/protector
  para celular»): decide por el producto más probable; eso no es "dudoso".
- "unidades": cuántos productos COMPLETOS como el nuestro trae esa publicación
  (1 si no dice; «par» o «2 pzs» del mismo artículo = 2; «pack de 4» = 4). Las
  piezas de un juego que se usa completo (un juego de cubiertos de 24 piezas, un
  kit de bloques para armar, un set de brocas) NO son unidades: eso es 1.
- "unidades_nuestras": lo mismo para NUESTRO paquete. Si el mensaje ya la trae
  como dato fijo, repítela tal cual.
- No clasifiques por cantidad: si el producto es el mismo y solo cambia cuántas
  unidades trae, responde "mismo" y pon sus "unidades". La cuenta la hace el
  sistema.
- "razon": máximo 10 palabras, en español.
- Un veredicto por CADA número de la lista, sin saltarte ninguno ni inventar
  números.

FORMATO (JSON):
{"nuestro": "<qué es exactamente, máximo 20 palabras>",
 "unidades_nuestras": <entero>,
 "veredictos": [{"i": <número>, "clase": "<clase>", "unidades": <entero>, "razon": "<texto>"}]}"""


# ── Núcleo puro: prompt y lectura de la respuesta ────────────────────────────

def _norm(t: Any) -> str:
    t = unicodedata.normalize("NFKD", str(t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def _entero(v: Any) -> int | None:
    """Entero positivo, o `None`. Un 3.9 NO es un 3: se rechaza."""
    if isinstance(v, bool):
        return None
    if isinstance(v, float):
        if not v.is_integer():
            return None
        v = int(v)
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 < n < 100_000 else None


def _no_positivo(v: Any) -> bool:
    """¿Es un entero ≤ 0? Es la huella de un modelo que numeró desde cero."""
    if isinstance(v, bool):
        return False
    if isinstance(v, float):
        return v.is_integer() and v <= 0
    try:
        return int(str(v).strip()) <= 0
    except (TypeError, ValueError):
        return False


def es_comparable(clase: str | None) -> bool:
    """La ÚNICA definición de «sirve para comparar precio» en Python. La vista
    `enrich.market_rival_comparable_v` repite esta misma regla en SQL."""
    return clase == "mismo"


def armar_usuario(nuestro: dict[str, Any], rivales: list[dict[str, Any]],
                  unidades_nuestras: int | None = None, *, con_precio: bool = False) -> str:
    """El mensaje de un lote. Los rivales van NUMERADOS 1..N: al modelo nunca se
    le pide repetir un id, porque los «normaliza» al copiarlos (ya pasó con un SKU
    que llevaba una comilla) y el cruce exacto se pierde.

    No lleva el término buscado: el veredicto es de la PAREJA de productos, y con
    el término adentro un veredicto reusado bajo otro término habría sido emitido
    con un dato que la llave ignora. `con_precio` existe solo para medir en el
    examen si el precio ayuda; en producción va apagado (ver `juzgar_lote`)."""
    lineas = ["NUESTRO PRODUCTO", f"titulo: {nuestro.get('titulo') or '(sin título)'}"]
    if con_precio and nuestro.get("precio"):
        lineas.append(f"precio: ${float(nuestro['precio']):,.0f} MXN")
    if nuestro.get("categoria"):
        lineas.append(f"categoria: {nuestro['categoria']}")
    if unidades_nuestras:
        lineas.append(f"unidades_nuestras (dato fijo): {unidades_nuestras}")
    lineas += ["", f"RIVALES ({len(rivales)})"]
    for i, r in enumerate(rivales, 1):
        precio = f"${float(r['precio']):,.0f} | " if con_precio and r.get("precio") else ""
        lineas.append(f"{i}. {precio}{str(r.get('titulo') or '').strip()[:160]}")
    lineas += ["", "Responde el JSON con un veredicto por cada número."]
    return "\n".join(lineas)


def leer_veredictos(res: dict[str, Any], n: int) -> dict[str, Any]:
    """Lo que de la respuesta pasa la validación.

    → {unidades_nuestras, veredictos: {i: {clase, unidades_rival, motivo}},
       sospechoso: bool}

    `sospechoso` = el modelo devolvió un número repetido con clases distintas,
    uno fuera de rango, o un 0 sin el N (numeró 0..N-1): señal de que renumeró y
    el resto puede estar corrido. Quien llama descarta el lote y reintenta en vez
    de guardar algo torcido.

    Un `mismo` u `otro_paquete` cuyas unidades no se pueden leer queda fuera: la
    clase de esos dos la decide la cuenta de unidades, y un «3 pzs» leído como 1
    volvería comparable a un paquete de 3.

    Aguanta una respuesta cortada: sin objeto completo, rescata del texto las
    entradas que sí cerraron. Lo que falte queda fuera — sin juzgar."""
    datos = res.get("datos") if res.get("ok") else None
    crudos: list[Any] = []
    un = None
    if isinstance(datos, dict):
        crudos = datos.get("veredictos") if isinstance(datos.get("veredictos"), list) else []
        un = _entero(datos.get("unidades_nuestras"))
    elif res.get("texto"):
        from services.competencia_captura import _objetos_completos  # import tardío

        texto = res["texto"]
        crudos = [o for o in _objetos_completos(texto) if "clase" in o and "i" in o]
        m = re.search(r'"unidades_nuestras"\s*:\s*(\d+)', texto)
        un = _entero(m.group(1)) if m else None
    out: dict[int, dict[str, Any]] = {}
    primera: dict[int, str] = {}     # la primera clase de cada número, se acepte o no
    numeros: set[int] = set()
    sospechoso = cero = False
    for v in crudos:
        if not isinstance(v, dict):
            continue
        i = _entero(v.get("i"))
        if i is None:
            cero = cero or _no_positivo(v.get("i"))
            continue
        numeros.add(i)
        clase = _norm(v.get("clase")).replace(" ", "_")
        if clase not in CLASES:
            continue
        if i > n:
            sospechoso = True
            continue
        if i in primera:
            sospechoso = sospechoso or primera[i] != clase
            continue
        primera[i] = clase
        crudas = v.get("unidades")
        suyas = _entero(crudas)
        # «No dijo» no es «dijo algo ilegible»: un `mismo` sin unidades es una
        # pieza, pero uno con «3 pzs», 0 o 2.5 no se sabe. Y un `otro_paquete`
        # sin unidades legibles no tiene con qué contarse: tomarlo como 1 lo
        # volvería `mismo` —comparable— tirando la única señal que había.
        if clase in ("mismo", "otro_paquete") and suyas is None \
                and (crudas is not None or clase == "otro_paquete"):
            continue
        out[i] = {"clase": clase, "unidades_rival": suyas,
                  "motivo": str(v.get("razon") or "").strip()[:160]}
    # Numerar 0..N-1 no deja ningún número fuera de rango: la huella es el 0 y
    # que falte el N. Con el N presente, un 0 extra (el modelo le dio número a
    # nuestro producto) solo se ignora: tirar ese lote sería tirar uno bueno, y
    # el reintento manda la petición idéntica.
    if cero and n not in numeros:
        sospechoso = True
    return {"unidades_nuestras": un, "veredictos": out, "sospechoso": sospechoso}


def _aplicar_unidades(veredictos: dict[int, dict[str, Any]], un: int | None) -> None:
    """La cuenta de paquetes la hace el CÓDIGO, no el modelo: un `mismo` cuyas
    unidades difieren de las nuestras pasa a `otro_paquete`, y un `otro_paquete`
    con las mismas unidades vuelve a `mismo`. Nuestras unidades valen 1 si el
    título no dice otra cosa; las de un `mismo` que no las dijo, igual (un
    `otro_paquete` sin unidades legibles ni llega aquí: `leer_veredictos` lo deja
    sin juzgar)."""
    nuestras = un or 1
    for v in veredictos.values():
        if v["clase"] not in ("mismo", "otro_paquete"):
            v["unidades_rival"] = None
            continue
        suyas = v["unidades_rival"] or 1
        v["unidades_rival"] = suyas
        v["clase"] = "mismo" if suyas == nuestras else "otro_paquete"


def juzgar_lote(nuestro: dict[str, Any], rivales: list[dict[str, Any]], *,
                modelo: str, razonar: bool = False, unidades_nuestras: int | None = None,
                timeout: float = 120.0, intentos: int = 3,
                con_precio: bool = False) -> dict[str, Any]:
    """Juzga los rivales de UN SKU. No toca la base.

    Parte la lista en trozos de `TROZO`; las unidades de nuestro paquete se
    deciden en el primero y viajan como dato fijo a los demás, para que todos los
    veredictos del SKU usen el mismo divisor.

    El turno del LLM (`_turno`) se espera a lo más `timeout` segundos: quien pidió
    una llamada de 30 s no se queda minutos detrás de otras colgadas. Sin turno,
    lo que falta queda sin juzgar con motivo `IA_OCUPADA`.

    → {ok, unidades_nuestras, veredictos: {i: {...}} (i sobre la lista completa),
       faltan: [i], proveedor, modelo, uso, usd, cortado, motivo, llamadas,
       contestadas}

    `contestadas` = llamadas en las que el proveedor devolvió texto (bueno o
    malo): con 0, lo que falló fue el proveedor; con más, fue la respuesta.
    """
    total: dict[int, dict[str, Any]] = {}
    uso = {"entrada": 0, "cache": 0, "salida": 0}
    usd, llamadas, contestadas, cortado, motivo, proveedor = 0.0, 0, 0, False, None, None
    un = _entero(unidades_nuestras)
    for ini in range(0, len(rivales), TROZO):
        trozo = rivales[ini:ini + TROZO]
        leido: dict[str, Any] = {"veredictos": {}, "unidades_nuestras": None}
        # El motivo es del ÚLTIMO intento de este trozo: si el reintento salió
        # limpio, el «renumeró» del lote tirado ya no explica lo que falte.
        motivo_trozo = None
        # Un lote sospechoso se reintenta UNA vez; si sigue torcido, no se guarda.
        for _ in range(2):
            if not _turno.acquire(timeout=timeout):
                motivo_trozo = IA_OCUPADA
                break
            try:
                res = ia_json.completar_json(
                    _SYSTEM, armar_usuario(nuestro, trozo, un, con_precio=con_precio),
                    modelo=modelo, max_tokens=400 + 70 * len(trozo), razonar=razonar,
                    timeout=timeout, intentos=intentos)
            finally:
                _turno.release()
            llamadas += 1
            contestadas += bool(res.get("ok") or res.get("texto"))
            usd += float(res.get("usd") or 0)
            for k in uso:
                uso[k] += int((res.get("uso") or {}).get(k) or 0)
            cortado = cortado or bool(res.get("cortado"))
            proveedor = res.get("proveedor") or proveedor
            motivo_trozo = res.get("motivo")
            leido = leer_veredictos(res, len(trozo))
            if not leido["sospechoso"]:
                break
            # Sin esto el lote descartado no deja rastro: la llamada fue `ok` y
            # no trae motivo, así que en la bitácora no se distinguiría.
            motivo_trozo = "el modelo renumeró los rivales (lote descartado)"
            leido = {"veredictos": {}, "unidades_nuestras": None}
        motivo = motivo_trozo or motivo
        if motivo_trozo == IA_OCUPADA:
            break           # los trozos que siguen esperarían lo mismo
        if un is None:
            un = leido["unidades_nuestras"]
        for i, v in leido["veredictos"].items():
            total[ini + i] = v
    _aplicar_unidades(total, un)
    faltan = [i for i in range(1, len(rivales) + 1) if i not in total]
    return {"ok": bool(total) or not rivales, "unidades_nuestras": un or 1, "veredictos": total,
            "faltan": faltan, "proveedor": proveedor, "modelo": modelo, "uso": uso,
            "usd": round(usd, 6), "cortado": cortado, "llamadas": llamadas,
            "contestadas": contestadas, "motivo": motivo if faltan else None}


# ── Base de datos ────────────────────────────────────────────────────────────

def tablas_listas() -> bool:
    """¿Existe la tabla de veredictos en ESTA base? Producción no la tiene hasta
    que la migración pase su acta: todo lector y escritor pregunta primero y, sin
    tabla, se comporta como si el juez no existiera."""
    try:
        return supabase_db.fetch_scalar(
            "select to_regclass(%s) is not null and to_regclass(%s) is not null",
            (TABLA_JUICIO, VISTA_TITULO)) is True
    except Exception as exc:                                        # noqa: BLE001
        log.warning("juez: no se pudo comprobar la tabla: %s", exc)
        return False


# Las filas de rivales de un SKU con su veredicto. ES LA MISMA REGLA que la vista
# `enrich.market_rival_comparable_v` (migración 0063); la única diferencia es el
# término: la vista usa el asignado y aquí se puede pedir OTRO (un candidato que
# todavía no se le asigna a nadie). Si se toca una, se toca la otra.
#
# `unidades_sku` NO es parte de esa regla y la vista no la lleva: es el divisor
# YA establecido del SKU, que solo usa el juez. Sale de TODOS sus veredictos con
# el título nuestro de hoy y la versión vigente del prompt, no solo de los de
# este término: un candidato que se mide por primera vez no trae ninguno y, sin
# esto, el modelo decidía otro divisor y el UPDATE de `_guardar` lo imponía a
# todo el SKU. Con otra versión del prompt vuelve a preguntarse (un divisor
# equivocado no se queda pegado para siempre).
_SQL_FILAS = """
select cfg.sku::text as sku, r.termino_id, r.externo_id, r.posicion, r.titulo, r.precio,
       coalesce(r.es_nuestro, false) as es_nuestro, r.capturado_en,
       t.titulo as titulo_nuestro, t.categoria_nombre,
       j.clase, j.motivo, j.unidades_nuestras, j.unidades_rival, j.juzgado_en, j.modelo,
       j.version_prompt,
       (j.sku is not null
          and j.titulo_rival is not distinct from r.titulo
          and j.titulo_nuestro is not distinct from t.titulo) as vigente,
       (select u.unidades_nuestras
          from enrich.market_rival_juicio u
         where u.sku = cfg.sku and u.canal = cfg.canal
           and u.titulo_nuestro = t.titulo and u.version_prompt = %(version)s
           and u.unidades_nuestras is not null
         order by u.juzgado_en desc, u.externo_id
         limit 1) as unidades_sku
  from enrich.market_sku_config cfg
  join enrich.market_search_results r
    on r.termino_id = coalesce(%(tid)s::bigint, cfg.termino_id)
  left join enrich.market_sku_titulo_v t on t.sku = cfg.sku and t.canal = cfg.canal
  left join enrich.market_rival_juicio j
    on j.sku = cfg.sku and j.canal = cfg.canal and j.externo_id = r.externo_id
 where cfg.canal = %(canal)s and cfg.sku = any(%(skus)s::citext[])
 order by cfg.sku, r.posicion nulls last, r.externo_id
"""


def filas(skus: Iterable[str], termino_id: int | None = None) -> list[dict[str, Any]]:
    """Rivales de esos SKUs con su veredicto. `termino_id=None` = el término que
    cada SKU tiene asignado; con un id, los rivales de ESE término contra cada SKU
    (así se mide un candidato antes de asignarlo).

    Cada fila trae `cuenta` (rival ajeno con precio: lo que entra a un conteo),
    `vigente`, `comparable` y `pendiente` ya resueltos: nadie más los recalcula.
    Y `unidades_sku`, el divisor ya establecido del SKU (ver `_SQL_FILAS`)."""
    skus = sorted({s for s in skus if s})
    if not skus:
        return []
    out = supabase_db.fetch_all(_SQL_FILAS, {"tid": termino_id, "canal": CANAL, "skus": skus,
                                             "version": VERSION_PROMPT})
    for f in out:
        cuenta = (not f["es_nuestro"]) and float(f["precio"] or 0) > 0
        vigente = bool(f["vigente"])
        f["cuenta"] = cuenta
        f["vigente"] = vigente
        f["comparable"] = cuenta and vigente and es_comparable(f["clase"])
        # Pendiente = ajeno con precio, y sin veredicto que valga HOY: nunca se
        # juzgó, cambió un título, o se juzgó con otra versión del prompt.
        f["pendiente"] = cuenta and not (vigente and f["version_prompt"] == VERSION_PROMPT)
    return out


def resumen(filas_sku: list[dict[str, Any]]) -> dict[str, Any]:
    """Conteos de UN SKU sobre sus filas. Es la única cuenta que leen la pantalla,
    el disparador de la mejora de términos y cualquier lector futuro.

    `completo` = no queda nada por juzgar. Solo con `completo` se puede afirmar
    «este SKU tiene N comparables»; si no, la respuesta honesta es «no sé aún»."""
    base = [f for f in filas_sku if f["cuenta"]]
    juzgados = [f for f in base if f["vigente"]]
    fechas = [f["juzgado_en"] for f in juzgados if f["juzgado_en"]]
    por_clase: dict[str, int] = {}
    for f in juzgados:
        por_clase[f["clase"]] = por_clase.get(f["clase"], 0) + 1
    pendientes_n = sum(1 for f in base if f["pendiente"])
    return {"total": len(base), "juzgados": len(juzgados),
            "comparables": sum(1 for f in base if f["comparable"]),
            "pendientes": pendientes_n, "completo": bool(base) and pendientes_n == 0,
            "por_clase": por_clase, "juzgado_en": max(fechas) if fechas else None,
            # Sin título nuestro no hay contra qué juzgar: los pendientes de ese
            # SKU no bajan nunca y hay que decirlo en vez de dejar al usuario
            # apretando «juzgar» en vano.
            "sin_titulo": bool(base) and not any(f.get("titulo_nuestro") for f in base)}


def resumen_sku(sku: str, termino_id: int | None = None) -> dict[str, Any]:
    return resumen(filas([sku], termino_id))


def skus_de_termino(termino: str) -> tuple[int | None, list[str]]:
    """(termino_id, SKUs que lo tienen asignado)."""
    fs = supabase_db.fetch_all(
        "select st.id, cfg.sku::text as sku from enrich.market_search_term st "
        "  left join enrich.market_sku_config cfg "
        "         on cfg.termino_id = st.id and cfg.canal = st.canal "
        " where st.canal = %s and st.termino = %s", (CANAL, (termino or "").strip()))
    if not fs:
        return None, []
    return int(fs[0]["id"]), sorted({f["sku"] for f in fs if f["sku"]})


def skus_con_pendientes(limite: int | None = None) -> list[str]:
    """SKUs con al menos un rival por juzgar, en su término asignado.

    Sin título nuestro —NULL o '' (`core.products.name` admite los dos)— no
    entra: `juzgar_skus` lo salta sin llamar al LLM (`por_juzgar` pide título
    no vacío), y la cola lo volvería a pedir en cada vuelta sin avanzar."""
    fs = supabase_db.fetch_all(
        "select cfg.sku::text as sku, count(*) as n "
        "  from enrich.market_sku_config cfg "
        "  join enrich.market_search_results r on r.termino_id = cfg.termino_id "
        "  left join enrich.market_sku_titulo_v t on t.sku = cfg.sku and t.canal = cfg.canal "
        "  left join enrich.market_rival_juicio j "
        "    on j.sku = cfg.sku and j.canal = cfg.canal and j.externo_id = r.externo_id "
        " where cfg.canal = %s and not coalesce(r.es_nuestro, false) and r.precio > 0 "
        "   and coalesce(t.titulo, '') <> '' "
        "   and (j.sku is null or j.version_prompt is distinct from %s "
        "        or j.titulo_rival is distinct from r.titulo "
        "        or j.titulo_nuestro is distinct from t.titulo) "
        " group by 1 order by 1", (CANAL, VERSION_PROMPT))
    skus = [f["sku"] for f in fs]
    return skus[:limite] if limite else skus


_SQL_GUARDAR = f"""
insert into {TABLA_JUICIO}
   (sku, canal, externo_id, termino_id, clase, unidades_nuestras, unidades_rival, motivo,
    titulo_nuestro, titulo_rival, proveedor, modelo, version_prompt, juzgado_en)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
on conflict (sku, canal, externo_id) do update set
   termino_id = excluded.termino_id, clase = excluded.clase,
   unidades_nuestras = excluded.unidades_nuestras, unidades_rival = excluded.unidades_rival,
   motivo = excluded.motivo, titulo_nuestro = excluded.titulo_nuestro,
   titulo_rival = excluded.titulo_rival, proveedor = excluded.proveedor,
   modelo = excluded.modelo, version_prompt = excluded.version_prompt,
   juzgado_en = excluded.juzgado_en
"""


def _guardar(sku: str, por_juzgar: list[dict[str, Any]], r: dict[str, Any]) -> int:
    """Una transacción corta por SKU. Las filas van ORDENADAS por rival para que
    dos trabajos que coincidan sobre el mismo SKU tomen los candados en el mismo
    orden y converjan (gana el último) en vez de abrazarse."""
    lote = []
    for i, v in sorted(r["veredictos"].items(), key=lambda kv: por_juzgar[kv[0] - 1]["externo_id"]):
        f = por_juzgar[i - 1]
        lote.append((sku, CANAL, f["externo_id"], f["termino_id"], v["clase"],
                     r["unidades_nuestras"], v["unidades_rival"], v["motivo"] or None,
                     f["titulo_nuestro"], f["titulo"], r.get("proveedor"), r.get("modelo"),
                     VERSION_PROMPT))
    if not lote:
        return 0

    un = r["unidades_nuestras"]

    def escribir() -> None:
        with supabase_db.get_cursor() as cur:
            # Las unidades de NUESTRO paquete son un dato del SKU: si este lote
            # las fija distinto, los veredictos anteriores se alinean para que
            # ninguna cuenta mezcle dos divisores. Y su CLASE se recalcula en la
            # misma sentencia con la regla de `_aplicar_unidades` (mismo ⇔ mismas
            # unidades): cambiar solo el número dejaba `mismo` de dos tamaños de
            # paquete contando a la vez como comparables. Las demás clases no
            # dependen de las unidades y no se tocan.
            cur.execute(f"update {TABLA_JUICIO} set unidades_nuestras = %s, "
                        "       clase = case when clase not in ('mismo', 'otro_paquete') "
                        "                    then clase "
                        "                    when coalesce(unidades_rival, 1) = %s then 'mismo' "
                        "                    else 'otro_paquete' end "
                        " where sku = %s and canal = %s "
                        "   and unidades_nuestras is distinct from %s",
                        (un, un, sku, CANAL, un))
            for fila in lote:
                cur.execute(_SQL_GUARDAR, fila)

    # Con reintento ante una conexión muerta del pooler: estos veredictos YA se
    # pagaron, y perderlos por un corte transitorio es volver a pagarlos. La
    # transacción entera es idempotente (upsert), así que repetirla es seguro.
    supabase_db.reintentar_transitorio(escribir)
    return len(lote)


class Presupuesto:
    """Tope de gasto ACUMULATIVO de una corrida, compartido entre hilos.

    Se pasa el mismo objeto a todas las llamadas de la corrida: un tope que se
    estrena en cada llamada nunca se alcanza."""

    def __init__(self, tope_usd: float) -> None:
        self.tope = float(tope_usd)
        self.gastado = 0.0
        self._lock = threading.Lock()

    def puede(self) -> bool:
        with self._lock:
            return self.gastado < self.tope

    def sumar(self, usd: float) -> None:
        with self._lock:
            self.gastado += float(usd or 0)


def registrar(accion: str, estado: str, detalle: dict[str, Any], duracion_s: float) -> None:
    """Deja el gasto de IA en `ops.process_log`. Nunca revienta: una bitácora que
    tumba el trabajo es peor que no tenerla.

    `accion` es 'juez' o 'terminos' — NUNCA 'raspado': los lectores de costo de
    Apify suman `detalle->>'usd'` con ese filtro y se contaminarían."""
    try:
        supabase_db.execute(
            "insert into ops.process_log (proceso, origen, accion, estado, detalle, duracion_s) "
            "values ('competencia', %s, %s, %s, %s::jsonb, %s)",
            (detalle.get("proveedor") or "ia", accion, estado,
             json.dumps(detalle, default=str), round(duracion_s, 1)))
    except Exception as exc:                                        # noqa: BLE001
        log.warning("juez: no se pudo registrar el gasto: %s", exc)


# Gasto del juez YA PAGADO que aún no está en la bitácora: `juzgar_skus` escribe
# su fila al TERMINAR, así que sin esto una corrida que arranca mientras otra va
# a medias (una vuelta de la cola y el gancho de una captura, que coinciden) lee
# la bolsa sin lo que la otra ya gastó. `gastado_24h('juez')` lo suma. Solo ve
# ESTE proceso (el script por lotes corre aparte) y no vuelve absoluto el tope:
# lo que la primera gaste DESPUÉS de que arranque la segunda no lo ve nadie.
_en_vuelo = {"usd": 0.0, "corridas": 0}
_en_vuelo_lock = threading.Lock()


def gastado_24h(accion: str = "juez") -> float:
    """USD que esa acción lleva en las últimas 24 h, según la bitácora; para
    'juez', más lo que las corridas en curso de este proceso ya pagaron y todavía
    no registran (`_en_vuelo`)."""
    try:
        v = supabase_db.fetch_scalar(
            "select coalesce(sum((detalle->>'usd')::numeric), 0) from ops.process_log "
            " where proceso = 'competencia' and accion = %s "
            "   and created_at >= now() - interval '24 hours'", (accion,))
        total = float(v or 0)
    except Exception as exc:                                        # noqa: BLE001
        log.warning("juez: no se pudo leer el gasto de 24 h: %s", exc)
        return float("inf")     # sin poder medir, no se gasta
    if accion == "juez":
        with _en_vuelo_lock:
            total += _en_vuelo["usd"]
    return total


def juzgar_skus(skus: Iterable[str], *, presupuesto: Presupuesto, termino_id: int | None = None,
                modelo: str | None = None, hilos: int = 4, plazo_s: float | None = None,
                timeout: float = 120.0, intentos: int = 3, origen: str | None = None,
                resultados: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Juzga lo PENDIENTE de esos SKUs y lo guarda. Reentrante e idempotente.

    Se detiene cuando se agota el `presupuesto` o el `plazo_s` de reloj, o cuando
    un SKU no consigue turno del LLM en `timeout` segundos («IA ocupada»); lo que
    no alcanzó queda pendiente para la siguiente pasada. Devuelve el resumen de la
    corrida y deja UNA fila en `ops.process_log`.

    Opcionales, para la cola (`drenar_cola`) y sin efecto para los demás:
    `origen` viaja en el resumen —y con él al detalle de la bitácora— para saber
    quién gastó; `resultados`, si se pasa, se llena con lo que pasó con cada SKU
    que llegó al juez: {pendientes, guardados, llamadas, motivo}."""
    t0 = time.monotonic()
    modelo = modelo or settings.competencia_juez_modelo
    skus = sorted({s for s in skus if s})
    res: dict[str, Any] = {
        "skus": len(skus), "juzgados_skus": 0, "veredictos": 0, "sin_juzgar": 0,
        "llamadas": 0, "usd": 0.0, "entrada": 0, "salida": 0, "fallidos": 0,
        "detenido": None, "modelo": modelo, "proveedor": ia_json.proveedor_de(modelo)}
    if origen:
        res["origen"] = origen
    if not skus:
        return res
    if modelo not in ia_json.MODELOS:
        res["detenido"] = f"modelo sin precio: {modelo}"
        return res

    por_sku: dict[str, list[dict[str, Any]]] = {}
    for f in filas(skus, termino_id):           # la conexión se suelta aquí
        por_sku.setdefault(f["sku"], []).append(f)
    lock = threading.Lock()
    seguidos = {"n": 0}
    mio = {"usd": 0.0}          # lo que ESTA corrida puso en `_en_vuelo`

    def uno(sku: str) -> None:
        fs = por_sku.get(sku) or []
        por_juzgar = [f for f in fs if f["pendiente"] and f["titulo_nuestro"]]
        if not por_juzgar or res["detenido"]:
            return
        if not presupuesto.puede():
            res["detenido"] = "tope de gasto"
            return
        if plazo_s is not None and time.monotonic() - t0 > plazo_s:
            res["detenido"] = "plazo"
            return
        if seguidos["n"] >= 5:
            res["detenido"] = "5 fallos seguidos del proveedor"
            return
        # El divisor del SKU ENTERO, no el de las filas de este término: así un
        # candidato lo hereda y ninguna cuenta mezcla dos (ver `_SQL_FILAS`). Si
        # existe viaja como dato fijo y la respuesta del modelo no lo pisa, ni
        # siquiera cuando no trae el suyo.
        ya = next((f["unidades_sku"] for f in fs if f.get("unidades_sku")), None)
        nuestro = {"titulo": por_juzgar[0]["titulo_nuestro"],
                   "categoria": por_juzgar[0]["categoria_nombre"]}
        r = juzgar_lote(nuestro, por_juzgar, modelo=modelo, unidades_nuestras=ya,
                        timeout=timeout, intentos=intentos)
        presupuesto.sumar(r["usd"])
        with _en_vuelo_lock:
            _en_vuelo["usd"] += float(r["usd"] or 0)
            mio["usd"] += float(r["usd"] or 0)
        n = 0
        no_guardo = False
        if r["veredictos"]:
            try:
                n = _guardar(sku, por_juzgar, r)
            except Exception as exc:                                # noqa: BLE001
                no_guardo = True
                log.warning("juez: no se pudo guardar %s: %s", sku, exc)
        with lock:
            if resultados is not None:
                resultados[sku] = {"pendientes": len(por_juzgar), "guardados": n,
                                   "llamadas": r["llamadas"], "motivo": r.get("motivo")}
            res["llamadas"] += r["llamadas"]
            res["usd"] += r["usd"]
            res["entrada"] += r["uso"]["entrada"]
            res["salida"] += r["uso"]["salida"]
            res["veredictos"] += n
            res["sin_juzgar"] += len(por_juzgar) - n
            res["juzgados_skus"] += bool(n)
            if n:
                seguidos["n"] = 0
            elif no_guardo:
                # El LLM contestó bien; lo que falló fue la BASE. No cuenta como
                # fallo del proveedor, pero cada uno de estos ya se pagó: a la
                # tercera se detiene en vez de seguir gastando a fondo perdido.
                res["sin_guardar"] = res.get("sin_guardar", 0) + 1
                res["ultimo_motivo"] = "no se pudo guardar en la base"
                if res["sin_guardar"] >= 3:
                    res["detenido"] = "la base no está guardando los veredictos"
            elif r.get("motivo") == IA_OCUPADA:
                # Ni siquiera hubo turno: las llamadas de este proceso llevan
                # `timeout` ocupadas. No es culpa del modelo ni del proveedor.
                res["ultimo_motivo"] = IA_OCUPADA
            elif r.get("contestadas"):
                # El proveedor SÍ contestó (y cobró), pero nada pasó la
                # validación: clase fuera del catálogo, lote renumerado dos
                # veces... Eso no es una caída y no suma a los fallos seguidos,
                # que existen para detectar un proveedor caído o sin saldo: una
                # pareja terca al principio de la cola no debe detener la corrida.
                res["fallidos"] += 1
                motivo = r.get("motivo")
                res["ultimo_motivo"] = (f"respuesta inválida del modelo ({motivo})" if motivo
                                        else "respuesta inválida del modelo")
            else:
                res["fallidos"] += 1
                seguidos["n"] += 1
                res["ultimo_motivo"] = r.get("motivo")
            if r.get("motivo") == IA_OCUPADA:
                # Lo que sigue esperaría lo mismo: se detiene y queda pendiente.
                res["detenido"] = res["detenido"] or IA_OCUPADA

    with _en_vuelo_lock:
        _en_vuelo["corridas"] += 1
    try:
        with ThreadPoolExecutor(max_workers=max(1, hilos)) as ex:
            # Lo que devolvió la BASE, no lo pedido: el SKU se empareja por citext
            # y `por_sku` lleva el canónico. Con «sku-a» no se encontraba nada y
            # se respondía «la IA no contestó» sin haberla llamado.
            list(ex.map(uno, sorted(por_sku)))
    finally:
        try:
            res["usd"] = round(res["usd"], 6)
            res["duracion_s"] = round(time.monotonic() - t0, 1)
            if res["llamadas"]:
                registrar("juez", "parcial" if (res["detenido"] or res["fallidos"]
                                                or res.get("sin_guardar")) else "ok",
                          res, res["duracion_s"])
        finally:
            # DESPUÉS de registrar: en el instante entre las dos cosas se cuenta
            # doble (del lado seguro), nunca se deja de contar. Sin corridas vivas
            # vuelve a 0 exacto, sin residuos de redondeo.
            with _en_vuelo_lock:
                _en_vuelo["corridas"] -= 1
                _en_vuelo["usd"] = (_en_vuelo["usd"] - mio["usd"]) if _en_vuelo["corridas"] else 0.0
    return res


def juzgar_termino(termino: str, *, presupuesto: Presupuesto, **kw: Any) -> dict[str, Any]:
    """Juzga los rivales de ese término contra cada SKU que lo tiene asignado.
    Es lo que se engancha después de una captura."""
    _, skus = skus_de_termino(termino)
    return juzgar_skus(skus, presupuesto=presupuesto, **kw)


# ── La cola: lo pendiente, sin esperar a que alguien vuelva a medir ─────────
#
# Sin ella el juez solo corre tras un «Medir» (a mano: ~35 términos al mes), con
# el botón de admin o con el script por lotes, que tiene candado de SANDBOX. En
# producción eso deja 12,736 pares (1,462 SKUs) que nadie juzgaría nunca, y un
# par vuelve a quedar pendiente cada vez que cambia un título, se re-mide su
# término o se sube `VERSION_PROMPT`. La cola es un job del scheduler (solo con
# COMPETENCIA_JUEZ_ENABLED) que en cada vuelta toma los primeros SKUs con
# pendientes y los juzga: la primera pasada ES el backfill (~12.7k pares ≈ $0.91
# a precio de lista, medido en el sandbox) y después es el mantenimiento. Gasta
# de la MISMA bolsa de 24 h que el gancho (`gastado_24h`), que también descuenta
# lo que el juez gasta por el botón y la mejora, aunque esos dos no la consultan.

# Una vuelta normal (40 SKUs, ~350 pares) cuesta ~$0.025. Este techo es para lo
# anormal —un SKU con cientos de rivales, un modelo que cobra de más—. Se revisa
# ANTES de cada SKU, no entre trozos: a partir de él la vuelta no empieza SKUs
# nuevos, y los que están en vuelo (hasta 2, con TODOS sus trozos) terminan, así
# que puede pasarse por centavos. Cortar entre trozos dejaría SKUs a medias que el
# anti-atasco enfriaría 24 h como si fueran tercos.
COLA_TOPE_VUELTA_USD = 0.10

# ANTI-ATASCO. `skus_con_pendientes` ordena por SKU: si los primeros de la lista
# son pares que el modelo nunca contesta bien (o que el proveedor rechaza
# siempre, p. ej. por su filtro de contenido), cada vuelta los volvería a pagar y
# lo de atrás no avanzaría jamás. Un SKU que se INTENTÓ (hubo llamada) y quedó
# con pendientes se salta durante este enfriamiento; uno que quedó completo sale
# de la lista. No cuenta el que no consiguió turno («IA ocupada»): no es suyo.
# Tampoco se escapa el que la vuelta visitó y no tenía NADA que juzgar (p. ej.
# sin título nuestro, si el SQL de `skus_con_pendientes` y `filas()` llegaran a
# discrepar): sin llamada no entra a `resultados` y, sin enfriarlo, ocuparía un
# lugar del lote para siempre, gratis pero sin dejar avanzar a nadie.
#
# Con el proveedor caído también se enfrían: la vuelta se detiene a los 5 fallos
# seguidos y esos 5 esperan 24 h. Es demora, no pérdida, y es lo que evita que 5
# SKUs que el proveedor rechaza siempre frenen la cola para siempre.
#
# Vive en MEMORIA del proceso: un reinicio (cada deploy, cada variable de
# Railway) la olvida y esos SKUs se reintentan una vez más. Olvidar cuesta
# centavos; no tenerla costaba la cola entera.
COLA_ENFRIAMIENTO_S = 24 * 3600
_enfriando: dict[str, float] = {}       # SKU → time.monotonic() hasta el que se salta
_enfriando_lock = threading.Lock()


def drenar_cola(*, max_skus: int, tope_diario: float, plazo_s: float | None = None,
                hilos: int = 2, timeout: float = 60.0, intentos: int = 2) -> dict[str, Any]:
    """UNA vuelta de la cola: juzga lo pendiente de hasta `max_skus` SKUs con lo
    que quede del tope de 24 h, y con `COLA_TOPE_VUELTA_USD` de techo para SKUs
    NUEVOS (los que estén en vuelo al llegar a él terminan: puede pasarse por
    centavos). SÍNCRONA —desde el scheduler va en `asyncio.to_thread`, regla 11—
    y NUNCA lanza: un job que revienta deja una traza de APScheduler que nadie
    busca.

    `hilos=2` deja 2 de los 4 turnos del semáforo `_turno` para el gancho y el
    botón (si el gancho usa los suyos, que también son 2, el botón espera turno).
    `plazo_s` se revisa antes de cada SKU, no durante: el que ya arrancó termina
    todos sus trozos, así que quien llama deja margen hasta la vuelta siguiente.
    Si `juzgar_skus` se detiene (tope, 5 fallos del proveedor, plazo), la vuelta
    termina ahí y lo dice en el log; la siguiente vuelve a intentar.

    → {motivo, pendientes_skus, en_enfriamiento, skus, veredictos, sin_juzgar,
       usd, detenido, enfriados}. `motivo` dice por qué NO se juzgó nada."""
    out: dict[str, Any] = {"motivo": None, "pendientes_skus": 0, "en_enfriamiento": 0,
                           "skus": 0, "veredictos": 0, "sin_juzgar": 0, "usd": 0.0,
                           "detenido": None, "enfriados": 0}
    try:
        if not tablas_listas():
            out["motivo"] = "sin tablas"
            log.info("cola del juez: sin %s en esta base (o sin poder comprobarla); "
                     "no se juzga nada", TABLA_JUICIO)
            return out
        gastado = gastado_24h()
        restante = float(tope_diario) - gastado
        if restante < 0.01:
            if math.isfinite(gastado):
                out["motivo"] = "tope diario"
                log.info("cola del juez: tope diario alcanzado (%.4f de %.2f USD en 24 h, gancho, "
                         "botón y mejora incluidos); sigue cuando la ventana libere saldo",
                         gastado, tope_diario)
            else:
                out["motivo"] = "gasto sin medir"
                log.info("cola del juez: no se pudo medir el gasto de 24 h; sin medir no se gasta")
            return out

        ahora = time.monotonic()
        with _enfriando_lock:
            for s in [s for s, hasta in _enfriando.items() if hasta <= ahora]:
                del _enfriando[s]
            enfriando = set(_enfriando)
        pendientes = skus_con_pendientes()
        elegibles = [s for s in pendientes if s not in enfriando]
        out["pendientes_skus"] = len(pendientes)
        out["en_enfriamiento"] = len(pendientes) - len(elegibles)
        lote = elegibles[:max(1, int(max_skus))]
        if not lote:
            out["motivo"] = "todo en enfriamiento" if pendientes else "nada pendiente"
            # Es el estado normal tras el backfill: nada en INFO cada media hora.
            log.debug("cola del juez: %s (%d SKUs con pendientes)", out["motivo"], len(pendientes))
            return out

        por_sku: dict[str, dict[str, Any]] = {}
        r = juzgar_skus(lote, presupuesto=Presupuesto(min(restante, COLA_TOPE_VUELTA_USD)),
                        hilos=hilos, plazo_s=plazo_s, timeout=timeout, intentos=intentos,
                        origen="cola", resultados=por_sku)
        ahora = time.monotonic()
        with _enfriando_lock:
            for sku, x in por_sku.items():
                if x["guardados"] >= x["pendientes"]:
                    _enfriando.pop(sku, None)
                elif x["llamadas"] and x["motivo"] != IA_OCUPADA:
                    _enfriando[sku] = ahora + COLA_ENFRIAMIENTO_S
                    out["enfriados"] += 1
            if not r["detenido"]:
                # Sin detención la vuelta visitó TODO el lote: el que falta en
                # `resultados` no tenía nada que juzgar (ver ANTI-ATASCO).
                for sku in lote:
                    if sku not in por_sku:
                        _enfriando[sku] = ahora + COLA_ENFRIAMIENTO_S
                        out["enfriados"] += 1
        out.update(skus=len(lote), veredictos=r["veredictos"], sin_juzgar=r["sin_juzgar"],
                   usd=r["usd"], detenido=r["detenido"])
        detenido = ""
        if r["detenido"]:
            detenido = f" — se detuvo: {r['detenido']}"
            if r.get("ultimo_motivo"):
                detenido += f" ({r['ultimo_motivo']})"
        log.info("cola del juez: %d SKUs → %d veredictos, %d sin juzgar, $%.4f; quedaban %d SKUs "
                 "con pendientes (%d en enfriamiento), %d más se enfrían 24 h%s",
                 len(lote), r["veredictos"], r["sin_juzgar"], r["usd"], len(pendientes),
                 out["en_enfriamiento"], out["enfriados"], detenido)
    except Exception as exc:                                        # noqa: BLE001
        out["motivo"] = "error"
        log.warning("cola del juez: la vuelta falló (sigue en la próxima): %s", exc)
    return out
