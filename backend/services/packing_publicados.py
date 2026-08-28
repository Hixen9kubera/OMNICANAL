"""
packing_publicados.py — "Validar costo de PRODUCTOS PUBLICADOS EN MERCADO
LIBRE": el Resolver al revés.

El Resolver de siempre es **packing-list-primero**: se carga un xlsx y se
empatan sus renglones contra el catálogo. Este es **SKU-primero**: se parte de
unos SKUs publicados en ML y a cada uno se le busca SU renglón en el packing
list que le toca, para reconstruir su costo desde el papel del embarque.

REGLA CRÍTICA (Brandon): **este proceso aplica ÚNICAMENTE a productos
publicados en Mercado Libre.** Se valida aquí, en el servicio —no en el
botón— y DOS veces: al arrancar el trabajo y otra vez justo antes de escribir.
El filtro de pantalla es comodidad; la regla vive donde no se puede saltar,
porque un `jid` es adivinable por API y la escritura es lo irreversible.

Y las dos validaciones preguntan por **el SKU PEDIDO**, nunca por la variante.
No es un tecnicismo: la publicación de ML cuelga del PADRE y la variante casi
nunca tiene una propia (de 1,220 variantes expandidas, 196 —el 16%—). Si el
guardado revalidara contra la fila —que ya es la variante— 209 de los 305
padres publicados terminarían con CERO variantes escribibles: TEC-0377 correría
103 escaleras con IA para no poder escribir ninguna. Un sujeto distinto en cada
extremo convierte el flujo en un callejón sin salida caro.

LA ESCALERA, por SKU, parando en el primer peldaño que resuelve:

  0. **Foto de Odoo** contra las fotos embebidas del packing list: sha256
     (mismo archivo, el 92% de los casos medidos) y si no, dHash con distancia
     ≤ 8/64 **y margen ≥ 4 bits** sobre el segundo candidato. Sin ese margen,
     dos fotos parecidas empatan las dos y no hay empate confiable.
  1. **Léxico**: se calcula y se REPORTA, no decide. El título de ML está en
     español y el packing list en chino o inglés: contar palabras en común no
     resuelve casi nunca, pero sirve para recortar lo que se le manda al modelo.
  2. **IA**: el título del anuncio contra el texto crudo de los renglones
     (hasta 5 candidatos), y después la foto del anuncio de ML contra las fotos
     de esos renglones. Se exige el segundo eje ``titulo_concuerda``: a 420 px
     una malla de sombra y un estante metálico son dos rejillas grises.

De Odoo se toman SOLO la foto y a qué contenedor pertenece el SKU. Sus números
no se usan para nada (58% se autocontradicen).

QUÉ NO SE REUSA, y por qué no es pereza:
  · ``packing_comparador.guardar()`` escribe a la ``costos_validados`` de
    **MySQL, congelada desde el 13-ago**: "funcionaría" sin efecto y sin pasar
    por el candado. El peor modo de fallo posible, silencioso.
  · ``packing_comparador.candidatos()`` / ``buscar_contenedor()`` leen esa misma
    tabla congelada.
  · ``packing_costos.calcular()`` prorratea el flete sobre el CBM del
    contenedor COMPLETO; aquí hay uno o unos pocos SKUs. Manda la tarifa FIJA.

**Nada se persiste hasta que el usuario confirma.** El trabajo vive en memoria
(3 h) igual que el del Resolver, y el único write es el UPSERT final con la
lista explícita de SKUs aprobados.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import uuid
from typing import Any

from config import settings
from services import (
    channel_read,
    costing_read,
    costing_write,
    meli,
    odoo,
    packing_comparador as comp,
    packing_drive,
    packing_drive_carpeta as carpeta,
    packing_indice,
    packing_resolver,
)

log = logging.getLogger("omnicanal.packing.publicados")

# ── Constantes del negocio ───────────────────────────────────────────────────
TARIFA_MXN_M3 = 7500.0     # flete por m³. FIJA, avalada por Brandon el 21-ago
TIPO_CAMBIO = 19.0
UMBRAL_DHASH = 8           # distancia máxima aceptada, de 64 bits
MARGEN_DHASH = 4           # ventaja mínima sobre el segundo candidato

MODELO_TITULO = "claude-sonnet-4-5"
MODELO_FOTO = comp.MODELO_VISION       # haiku: es la misma pregunta de siempre
_LADO_IA = 420
_LADO_FOTO_UI = 190
_LADO_CAND_UI = 150
_MAX_CANDIDATOS_IA = 5

# Tope de la tanda, medido en CORRIDAS DE ESCALERA (una por variante, dos
# llamadas de modelo cada una) y NO en SKUs pedidos. Es el mismo 200 de antes:
# lo que cambia es qué cuenta. Topar sobre lo pedido no acotaba nada — los 200
# padres de mayor expansión dan 1,011 filas, o sea 2,022 llamadas al modelo.
MAX_FILAS = 200

# Sin persistencia, la memoria es el único almacén. El tope es más bajo que el
# del Resolver (12) porque aquí cada fila arrastra TRES fotos en base64 más las
# de sus candidatos.
_TTL = 60 * 60 * 3
_MAX_TRABAJOS = 6

_trabajos: dict[str, dict[str, Any]] = {}
# Los packing lists ya leídos de cada trabajo, para que corregir un renglón a
# mano no obligue a re-parsear un xlsx de 100 MB. Se purgan con su trabajo.
#
# SE MUTA SIEMPRE BAJO `_lock`, igual que `_trabajos`. Dos hilos distintos lo
# escriben —el del trabajo al terminar y el de `POST /{jid}/archivo` mientras
# aquél sigue corriendo— y sin el candado la asignación final del trabajo se
# comía el índice que el usuario acababa de agregar a mano.
_indices_por_trabajo: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

_PASOS = {
    "encolado": "En cola…",
    "validando": "Verificando que estén publicados en Mercado Libre",
    "expandiendo": "Expandiendo SKUs padre a sus variantes",
    "ruteando": "Buscando a qué packing list pertenece cada SKU",
    "bajando": "Bajando los packing lists",
    "indexando": "Leyendo renglones y fotos",
    "escalera": "Buscándole su renglón a cada SKU",
    "calculando": "Calculando costos",
    "listo": "Listo",
    "error": "Error",
}

_VACIAS = {"de", "la", "el", "con", "para", "y", "en", "por", "a", "del", "los",
           "las", "un", "una", "the", "of", "with", "for", "and", "pza", "pzas",
           "pieza", "piezas"}


# ── Almacén de trabajos (mismo molde que packing_resolver) ───────────────────
def _purgar() -> None:
    """
    Tira lo caducado y lo que sobre del tope. Se llama con el lock tomado.

    Los ÍNDICES se tiran junto con su trabajo, y eso no es limpieza cosmética:
    cada índice se queda con el libro de openpyxl abierto y con las fotos
    crudas de todos sus renglones. Si sobrevivieran a su trabajo, la memoria
    solo crecería —nadie más los va a soltar— y el proceso se comería el
    contenedor a base de packing lists de contenedores ya revisados.

    Por eso también se barren los HUÉRFANOS: un trabajo se puede purgar
    mientras su hilo sigue vivo (caducó o lo empujó el tope de 6), y ese hilo
    todavía va a querer dejar su índice. Barrer solo las llaves que están en
    `_trabajos` deja esas entradas sin dueño y sin nadie que las libere jamás.
    """
    ahora = time.time()
    caducos = [k for k, v in _trabajos.items() if ahora - v.get("creado", 0) > _TTL]
    if len(_trabajos) - len(caducos) > _MAX_TRABAJOS:
        vivos = sorted(((k, v) for k, v in _trabajos.items() if k not in caducos),
                       key=lambda kv: kv[1].get("creado", 0))
        caducos += [k for k, _ in vivos[: len(vivos) - _MAX_TRABAJOS]]
    for k in caducos:
        _trabajos.pop(k, None)
        _indices_por_trabajo.pop(k, None)
    for k in [k for k in _indices_por_trabajo if k not in _trabajos]:
        _indices_por_trabajo.pop(k, None)


def _marcar(jid: str, paso: str, actual: int = 0, total: int = 0, **extra: Any) -> None:
    with _lock:
        t = _trabajos.get(jid)
        if not t:      # purgado a media corrida: no se resucita
            return
        etiqueta = _PASOS.get(paso, paso)
        if total:
            etiqueta = f"{etiqueta} · {actual}/{total}"
        t.update({"paso": paso, "paso_label": etiqueta, "actual": actual,
                  "total": total, "actualizado": time.time(), **extra})


def estado(jid: str) -> dict[str, Any] | None:
    """Estado + resultado. ``None`` si caducó o el backend reinició."""
    with _lock:
        t = _trabajos.get(jid)
        return dict(t) if t else None


def _aviso(jid: str, texto: str) -> None:
    with _lock:
        t = _trabajos.get(jid)
        if t and texto not in t["avisos"]:
            t["avisos"].append(texto)


def _guardar_indices(jid: str, indices: dict[str, Any]) -> bool:
    """
    Deja unos índices en el almacén del trabajo. ``False`` si ya no hay trabajo.

    Es el ÚNICO camino de escritura a ``_indices_por_trabajo``, y hace las dos
    cosas que faltaban: toma el lock (lo escriben el hilo del trabajo y el de
    ``POST /{jid}/archivo`` a la vez) y comprueba que el ``jid`` siga vivo —si
    no lo está, se sueltan los índices y se van con el recolector, en vez de
    quedarse de huérfanos con el libro de openpyxl adentro para siempre.

    MEZCLA, no asigna: cada llave es un ``file_id`` distinto y el que llegó
    primero también sirve.
    """
    if not indices:
        return True
    with _lock:
        if jid not in _trabajos:
            return False
        _indices_por_trabajo.setdefault(jid, {}).update(indices)
    return True


def _indices_de(jid: str) -> dict[str, Any]:
    """Copia superficial de los índices del trabajo (lectura bajo el lock)."""
    with _lock:
        return dict(_indices_por_trabajo.get(jid) or {})


# ── Insumos compartidos ──────────────────────────────────────────────────────
_catalogo_odoo: dict[str, str] = {}
_catalogo_en: float = 0.0
_TTL_CATALOGO = 60 * 30


def _catalogo() -> dict[str, str]:
    """``{SKU: container_numbers}`` de TODO Odoo, cacheado 30 min.

    Se pide entero, no por SKU, porque además de la referencia de contenedor es
    el diccionario con el que se decide si un SKU es HOJA o PADRE: un padre no
    existe como ``default_code`` y hay que salir a buscar sus variantes.
    """
    global _catalogo_odoo, _catalogo_en
    if _catalogo_odoo and (time.time() - _catalogo_en) < _TTL_CATALOGO:
        return _catalogo_odoo
    datos = odoo.contenedores_por_sku()
    if datos:
        _catalogo_odoo, _catalogo_en = datos, time.time()
    return _catalogo_odoo


def expandir(sku: str, cat: dict[str, str]) -> list[str]:
    """
    Un SKU pedido → los SKUs que de verdad se van a costear.

    **Los SKU padre nunca aparecen en un packing list**: el embarque trae la
    variante. Y 305 de los 2,524 publicados en ML SON padres, así que expandir
    no es un caso raro. La expansión va contra Odoo y no contra kubera:
    ``core.products.has_variations`` y ``parent_sku`` vienen vacíos para los
    2,524 (medido), o sea que ahí la pregunta no se puede contestar.
    """
    s = (sku or "").strip().upper()
    if not s:
        return []
    if s in cat:
        return [s]
    hijos = sorted(x for x in cat if x.startswith(s + "-"))
    return hijos or [s]


def referencias(sku: str, cat: dict[str, str],
                cont_kubera: dict[str, str]) -> list[tuple[str, str]]:
    """
    ``[(fuente, referencia_de_contenedor)]`` — TODAS, sin desempatar.

    kubera primero porque su ``contenedor`` ya está normalizado (``CODIGO - N``,
    102 valores distintos) frente al texto libre de Odoo (350, la mayoría
    sucios). Pero no se elige una: se prueban las dos y **desempata la imagen**.
    Coinciden en el 89.6% de los casos donde las dos hablan; en los 162 que se
    contradicen, creerle a una sola es ir al archivo equivocado con seguridad.
    """
    out: list[tuple[str, str]] = []
    if kb := (cont_kubera.get(sku) or "").strip():
        out.append(("kubera", kb))
    if od := (cat.get(sku) or "").strip():
        out.append(("odoo", od))
    return out


def _objetivos(skus: list[str], cat: dict[str, str]) -> list[tuple[str, str]]:
    """
    ``[(SKU pedido, variante a costear)]`` — el sujeto de cada corrida.

    El PEDIDO viaja pegado a su variante durante todo el trabajo porque es el
    único de los dos que la regla de Brandon sabe validar: la publicación de ML
    cuelga del padre. Perderlo de vista a media corrida es lo que dejaba al
    guardado preguntando por un SKU que nunca estuvo en ``channel.listings``.
    """
    out: list[tuple[str, str]] = []
    for s in skus:
        for v in expandir(s, cat):
            out.append((s, v))
    return out


class TopeExcedido(ValueError):
    """La selección expande a más corridas de escalera de las que caben."""

    def __init__(self, filas: int, tope: int, mensaje: str) -> None:
        super().__init__(mensaje)
        self.filas, self.tope = filas, tope


def contar_expandidas(skus: list[str]) -> dict[str, Any]:
    """
    Cuántas CORRIDAS DE ESCALERA implica de verdad esta selección.

    Topar sobre los SKUs pedidos no acota nada, y esa es la trampa: la escalera
    corre una vez por VARIANTE, con dos llamadas de modelo cada una. Los 200
    padres de mayor expansión dan 1,011 filas — TEC-0377 solo, 103—. Quien topa
    (el endpoint) tiene que topar sobre ``filas``, no sobre ``len(skus)``.

    ``catalogo_odoo`` en falso significa que Odoo no contestó y que la cuenta
    está SUBESTIMADA: sin su catálogo un padre parece hoja y se cuenta como 1.
    """
    cat = _catalogo()
    por_sku: dict[str, int] = {}
    for s in skus:
        s = (s or "").strip().upper()
        if s and s not in por_sku:
            por_sku[s] = len(expandir(s, cat))
    filas = sum(por_sku.values())
    mayores = sorted(por_sku.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "pedidos": len(por_sku),
        "filas": filas,
        "tope": MAX_FILAS,
        "excede": filas > MAX_FILAS,
        "mayores": [{"sku": s, "variantes": n} for s, n in mayores[:5] if n > 1],
        "catalogo_odoo": bool(cat),
    }


# ── Pronóstico (no cuesta IA, no crea trabajo) ───────────────────────────────
def preflight(skus: list[str]) -> dict[str, Any]:
    """
    Qué va a pasar con esta selección ANTES de gastar un peso.

    Existe porque la cobertura real no es del 100% y conviene decirlo antes, no
    después: hay SKUs sin contenedor en ninguna fuente, ~18% sin foto en Odoo, y
    los que ya tienen COSTO VALIDADO no se van a mover salvo que se libere el
    candado a propósito.
    """
    pedidos, vistos = [], set()
    omitidos: list[dict[str, Any]] = []
    for s in skus:
        s = (s or "").strip().upper()
        if not s:
            continue
        if s in vistos:
            omitidos.append({"sku": s, "motivo": "duplicado",
                             "detalle": "venía repetido en la selección"})
            continue
        vistos.add(s)
        pedidos.append(s)

    pubs = channel_read.publicados_ml(pedidos)
    cat = _catalogo()
    cont_kubera = costing_read.contenedores_por_sku(pedidos) if pedidos else {}

    vivos = [s for s in pedidos if pubs.get(s)]
    for s in pedidos:
        if not pubs.get(s):
            omitidos.append({
                "sku": s, "motivo": "no_publicado_ml",
                "detalle": "sin publicación viva en Mercado Libre "
                           "(situación active/paused)"})

    # Las variantes también pueden tener su propio contenedor en kubera.
    variantes_de = {s: expandir(s, cat) for s in vivos}
    todas = sorted({v for vs in variantes_de.values() for v in vs})
    # Lo que de verdad va a costar: una corrida de escalera por (pedido,
    # variante). Se anticipa aquí para que la pantalla pueda decir "seleccionaste
    # 40 y son 380 corridas" ANTES de que el arranque lo rechace.
    filas_escalera = sum(len(vs) for vs in variantes_de.values())
    cont_kubera.update(costing_read.contenedores_por_sku(todas) if todas else {})
    con_foto = odoo.skus_con_imagen(todas)
    guardados = costing_read.validados_de(vivos) if vivos else {}
    # El barrido completo, igual que el trabajo: con el corto, un SKU que SÍ
    # tiene packing list saldría como "sin archivo" y el pronóstico estaría
    # desanimando de un flujo que sí puede resolverlo. Se paga una vez cada 6 h
    # (el inventario está cacheado y es compartido con el trabajo).
    inv = carpeta.inventario(completo=True)

    elegibles = []
    for s in vivos:
        variantes = variantes_de[s]
        refs = []
        for v in variantes:
            refs.extend(referencias(v, cat, cont_kubera))
        archivos: set[str] = set()
        for _f, ref in refs:
            archivos.update(fid for fid, _n in carpeta.archivos_de(ref, inv))
        fuentes = {f for f, _ in refs}
        # Dos fuentes que no hablan del mismo contenedor: no es un error, es un
        # aviso — el empate por imagen va a tener que decidir.
        cods = {f: carpeta.codigos_de(r) for f, r in refs}
        desacuerdo = (len(fuentes) > 1
                      and not (cods.get("kubera", set()) & cods.get("odoo", set())))
        g = guardados.get(s) or {}
        elegibles.append({
            "sku": s,
            "publicado_ml": True,
            "cuentas": sorted({p.get("cuenta") or "" for p in pubs[s] if p.get("cuenta")}),
            "situaciones": sorted({(p.get("situacion") or "").lower() for p in pubs[s]}),
            "padre": variantes != [s],
            "variantes": variantes,
            "contenedor": (refs[0][1] if refs else None),
            "fuente_contenedor": ("ambas" if len(fuentes) > 1
                                  else (next(iter(fuentes)) if fuentes else None)),
            "fuentes_en_desacuerdo": bool(desacuerdo),
            "archivos": len(archivos),
            "foto_odoo": any(v in con_foto for v in variantes),
            "revisado_at": (g["revisado_at"].isoformat()
                            if g.get("revisado_at") else None),
        })

    return {
        "elegibles": elegibles,
        "omitidos": omitidos,
        "resumen": {
            "pedidos": len(pedidos),
            "elegibles": len(elegibles),
            "omitidos": len(omitidos),
            "expandidos": len(todas),
            "filas_escalera": filas_escalera,
            "max_filas": MAX_FILAS,
            "excede_tope": filas_escalera > MAX_FILAS,
            "catalogo_odoo": bool(cat),
            "con_contenedor": sum(1 for e in elegibles if e["archivos"]),
            "sin_contenedor": sum(1 for e in elegibles if not e["archivos"]),
            "con_foto_odoo": sum(1 for e in elegibles if e["foto_odoo"]),
            "ya_validados": sum(1 for e in elegibles if e["revisado_at"]),
            "listings_ml_actualizado": channel_read.frescura_listings_ml(),
        },
    }


# ── Arranque ─────────────────────────────────────────────────────────────────
def iniciar(skus: list[str], *, tarifa_mxn_m3: float = TARIFA_MXN_M3,
            tipo_cambio: float = TIPO_CAMBIO,
            usar_ia: bool = True) -> dict[str, Any]:
    """
    Valida contra ML, siembra el trabajo y lo lanza en un hilo.

    La validación de publicados se hace ANTES de devolver el ``jid``, en la
    misma petición: así el usuario ve de inmediato a cuáles se les dijo que no
    y por qué, en vez de descubrirlo diez minutos después.

    Y el tope de la tanda se mide aquí sobre las filas YA EXPANDIDAS. Es un
    respaldo, no la puerta principal —el endpoint topa antes, con
    ``contar_expandidas``—, pero tiene que estar del lado del servicio: quien
    llame por API sin pasar por el botón también gasta IA de verdad.
    """
    pedidos, vistos, omitidos = [], set(), []
    for s in skus:
        s = (s or "").strip().upper()
        if not s:
            continue
        if s in vistos:
            omitidos.append({"sku": s, "motivo": "duplicado",
                             "detalle": "venía repetido en la selección"})
            continue
        vistos.add(s)
        pedidos.append(s)

    pubs = channel_read.publicados_ml(pedidos)
    aceptados = []
    for s in pedidos:
        if pubs.get(s):
            aceptados.append(s)
        else:
            omitidos.append({
                "sku": s, "motivo": "no_publicado_ml",
                "detalle": "sin publicación viva en Mercado Libre "
                           "(situación active/paused)"})
    if not aceptados:
        return {"id": None, "omitidos": omitidos}

    # La expansión se hace ACÁ y viaja al hilo: si se recalculara allá, el
    # trabajo podría correr un número de filas distinto del que se acaba de
    # topar (el catálogo de Odoo se re-cachea cada 30 min y puede moverse en
    # medio). Se topa lo que se va a correr, no una estimación de lo mismo.
    cat = _catalogo()
    objetivos = _objetivos(aceptados, cat)
    if len(objetivos) > MAX_FILAS:
        raise TopeExcedido(
            len(objetivos), MAX_FILAS,
            f"Son {len(aceptados)} SKUs pero se abren en {len(objetivos)} "
            f"variantes, y el máximo por tanda es {MAX_FILAS}. La escalera corre "
            f"una vez por VARIANTE con dos llamadas de IA cada una: un SKU padre "
            f"cuesta lo que pesa toda su descendencia.")

    jid = uuid.uuid4().hex[:12]
    ahora = time.time()
    with _lock:
        _purgar()
        _trabajos[jid] = {
            "id": jid,
            "paso": "encolado", "paso_label": _PASOS["encolado"],
            # `total` cuenta FILAS, que es lo que avanza la barra de la escalera;
            # `pedidos` conserva cuántos SKUs seleccionó el usuario.
            "actual": 0, "total": len(objetivos), "pedidos": len(aceptados),
            "creado": ahora, "actualizado": ahora,
            "creado_en": _iso(ahora), "expira_en": _iso(ahora + _TTL),
            "opciones": {"tarifa_mxn_m3": tarifa_mxn_m3, "tipo_cambio": tipo_cambio,
                         "usar_ia": usar_ia},
            "filas": [], "omitidos": omitidos, "avisos": [],
            "resumen": {}, "guardado": None, "error": None,
        }
    threading.Thread(
        target=_procesar,
        args=(jid, objetivos, cat, pubs, tarifa_mxn_m3, tipo_cambio, usar_ia),
        name=f"pub-{jid}", daemon=True,
    ).start()
    return {"id": jid, "paso": "encolado", "paso_label": _PASOS["encolado"],
            "total": len(objetivos), "pedidos": len(aceptados),
            "omitidos": omitidos}


def _iso(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()


# ── El trabajo ───────────────────────────────────────────────────────────────
def _procesar(jid: str, objetivos: list[tuple[str, str]], cat: dict[str, str],
              pubs: dict[str, list[dict[str, Any]]],
              tarifa: float, tc: float, usar_ia: bool) -> None:
    """
    Corre TODO en este hilo, y eso no es un detalle de estilo.

    openpyxl, `requests` a Drive, psycopg2 vía sdb, el xmlrpc de Odoo y las
    llamadas al modelo son bloqueantes: dentro del event loop congelarían el
    backend ENTERO, no solo a quien llamó (regla 11 de la casa, el apagón de
    cinco horas). El endpoint solo encola; aquí se trabaja.

    ``objetivos`` y ``cat`` llegan ya resueltos desde ``iniciar``: son los
    mismos con los que se midió el tope de la tanda.
    """
    try:
        # 1) Expansión de padres (ya hecha en `iniciar`)
        _marcar(jid, "expandiendo")
        if not cat:
            _aviso(jid, "Odoo no contestó: sin su catálogo no se pueden expandir "
                        "los SKU padre ni usar su foto ni su contenedor.")
        variantes = sorted({v for _p, v in objetivos})

        # 2) Ruteo SKU → contenedor → archivos
        _marcar(jid, "ruteando", 0, len(variantes))
        cont_kubera = costing_read.contenedores_por_sku(variantes)
        # Barrido COMPLETO de la carpeta: son decenas de segundos y aquí sí se
        # pueden pagar (estamos en el hilo del trabajo). El pronóstico usa el
        # corto para no dejar esperando a una pantalla.
        inv = carpeta.inventario(completo=True)
        if not inv:
            _aviso(jid, "No se pudo listar la carpeta de packing lists en Drive. "
                        "Se puede seguir pegando la liga de cada archivo a mano.")
        plan: dict[str, dict[str, Any]] = {}     # file_id → {nombre, skus}
        refs_de: dict[str, list[tuple[str, str]]] = {}
        for v in variantes:
            refs = referencias(v, cat, cont_kubera)
            refs_de[v] = refs
            for _fuente, ref in refs:
                for fid, nombre in carpeta.archivos_de(ref, inv):
                    p = plan.setdefault(fid, {"nombre": nombre, "skus": []})
                    if v not in p["skus"]:
                        p["skus"].append(v)

        # 3) Bajar e indexar cada archivo UNA vez (varios SKUs lo comparten)
        indices: dict[str, packing_indice.Indice] = {}
        total_arch = len(plan)
        for i, (fid, p) in enumerate(plan.items(), start=1):
            _marcar(jid, "bajando", i, total_arch)
            try:
                datos = carpeta.bajar(fid, p["nombre"])
            except packing_drive.DriveError as exc:
                _aviso(jid, f"{p['nombre']}: no se pudo bajar ({exc})")
                continue
            except Exception as exc:  # noqa: BLE001
                _aviso(jid, f"{p['nombre']}: no se pudo bajar ({str(exc)[:120]})")
                continue
            _marcar(jid, "indexando", i, total_arch)
            try:
                ix = packing_indice.indexar(datos, p["nombre"], fid)
            except Exception as exc:  # noqa: BLE001
                _aviso(jid, f"{p['nombre']}: no se pudo leer ({str(exc)[:120]})")
                continue
            indices[fid] = ix
            for a in ix.avisos[:8]:
                _aviso(jid, a)

        # 4) Insumos de identidad y contraste
        fotos_odoo = odoo.imagenes_1920_por_sku(variantes)
        guardados = costing_read.validados_de(variantes)
        huellas_odoo = {s: {"crudo": b, "sha": _sha(b), "dh": packing_indice.dhash(b)}
                        for s, b in fotos_odoo.items()}

        # 5) La escalera
        _marcar(jid, "escalera", 0, len(objetivos))
        filas: list[dict[str, Any]] = []
        cache_ml: dict[str, dict[str, Any]] = {}
        for n, (padre, sku) in enumerate(objetivos, start=1):
            _marcar(jid, "escalera", n, len(objetivos))
            fids = [f for f, p in plan.items() if sku in p["skus"] and f in indices]
            fila = _resolver_uno(
                sku=sku, padre=(padre if padre != sku else None),
                pubs=pubs.get(padre) or pubs.get(sku) or [],
                fids=fids, indices=indices, huella_odoo=huellas_odoo.get(sku),
                refs=refs_de.get(sku) or [], guardado=guardados.get(sku) or {},
                tarifa=tarifa, tc=tc, usar_ia=usar_ia, cache_ml=cache_ml)
            filas.append(fila)
            with _lock:
                if t := _trabajos.get(jid):
                    t["filas"] = list(filas)

        _marcar(jid, "calculando", len(filas), len(filas))
        # Los índices se guardan para las correcciones a mano: re-parsear un
        # xlsx de 100 MB por cada "no, es el renglón 34" sería absurdo.
        #
        # Y se guardan BAJO EL LOCK, comprobando que el trabajo siga vivo:
        #  · si `_purgar` ya lo tiró (caducó, o lo empujó el tope de 6) mientras
        #    este hilo trabajaba, dejar el índice aquí sería una fuga eterna —
        #    nadie más purga esa llave, y arrastra el libro de openpyxl con
        #    todas las fotos crudas;
        #  · y se MEZCLA en vez de asignar, porque `POST /{jid}/archivo` pudo
        #    dejar el suyo mientras corríamos: asignar de golpe se lo comía.
        _guardar_indices(jid, indices)
        with _lock:
            t = _trabajos.get(jid)
            if t:
                t.update({
                    "paso": "listo", "paso_label": _PASOS["listo"],
                    "actual": len(filas), "total": len(filas),
                    "filas": filas, "resumen": _resumen(filas),
                    "actualizado": time.time(),
                })
        log.info("Validador de publicados %s listo: %s", jid, _resumen(filas))

    except Exception as exc:  # noqa: BLE001
        log.exception("Validador de publicados falló")
        _marcar(jid, "error", error=str(exc)[:500])


def _sha(datos: bytes) -> str:
    import hashlib
    return hashlib.sha256(datos).hexdigest()


def _resumen(filas: list[dict[str, Any]]) -> dict[str, int]:
    def n(estado: str) -> int:
        return sum(1 for f in filas if f.get("estado") == estado)
    return {"total": len(filas),
            "resueltos": sum(1 for f in filas
                             if f.get("estado") in ("sha256", "dhash", "ia")),
            "sha256": n("sha256"), "dhash": n("dhash"), "ia": n("ia"),
            "sin_match": n("sin_match"), "sin_insumo": n("sin_insumo"),
            "ya_validados": sum(1 for f in filas if f.get("revisado_at"))}


# ── Un SKU ───────────────────────────────────────────────────────────────────
def _resolver_uno(*, sku: str, padre: str | None, pubs: list[dict[str, Any]],
                  fids: list[str], indices: dict[str, packing_indice.Indice],
                  huella_odoo: dict[str, Any] | None,
                  refs: list[tuple[str, str]], guardado: dict[str, Any],
                  tarifa: float, tc: float, usar_ia: bool,
                  cache_ml: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pub = pubs[0] if pubs else {}
    fila: dict[str, Any] = {
        "sku": sku, "padre": padre,
        "nombre": guardado.get("nombre"),
        "titulo_ml": None, "item_id_ml": pub.get("item_id"),
        "cuenta_ml": pub.get("cuenta"),
        "situacion_ml": (pub.get("situacion") or "").lower() or None,
        "estado": "sin_insumo", "peldano": None, "detalle": "", "confianza": None,
        "lexico": None,
        "fuente": (refs[0][0] if refs else None),
        "ref": (refs[0][1] if refs else None),
        "file_id": None, "archivo": None, "fila_excel": None,
        "producto_chn": None, "grupo": [],
        "precio_usd": None, "piezas_grupo": None, "cbm_pieza": None,
        "cbm_origen": None,
        "peso_total": None, "peso_pieza": None, "flete": None,
        "producto_mxn": None, "costo": None, "origen_prod": None,
        "caja_lwh": None, "pieza_lwh": None,
        "costo_viejo": _f(guardado.get("costo_total")),
        "peso_viejo": _f(guardado.get("peso")),
        "revisado_at": (guardado["revisado_at"].isoformat()
                        if guardado.get("revisado_at") else None),
        "img_odoo": (packing_resolver._miniatura(huella_odoo["crudo"], _LADO_FOTO_UI)
                     if huella_odoo else None),
        "img_ml": None, "img_pl": None,
        "cands_ia": [], "cands_img": {}, "cands_txt": {}, "veredicto": [],
    }

    if not fids:
        fila["detalle"] = ("no hay packing list utilizable para este SKU "
                           "(sin contenedor conocido o el archivo no se pudo leer)")
        return fila

    # ── La publicación de ML: SIEMPRE, no solo cuando la escalera falla ──
    # Antes esto vivía dentro del peldaño de IA, así que un empate por sha256
    # llegaba a la pantalla sin foto de Mercado Libre y no había con qué
    # contrastarlo. Y es al revés de lo que conviene: el empate exacto es
    # justamente el que nadie va a mirar dos veces, así que si resultó ser la
    # foto equivocada —un SKU reciclado, una foto repetida entre renglones— se
    # va derecho al catálogo. Poder ver las tres (Odoo · publicación · packing
    # list) es lo que vuelve auditable la pantalla.
    #
    # Cuesta una llamada por SKU y `cache_ml` la comparte entre variantes del
    # mismo padre; frente a las dos llamadas de modelo del peldaño de IA, es
    # barato.
    _ml = _publicacion_ml(sku, pubs, cache_ml)
    fila["titulo_ml"] = _ml.get("titulo") or None
    if _ml.get("foto"):
        fila["img_ml"] = packing_resolver._miniatura(_ml["foto"], _LADO_FOTO_UI)

    # ── Peldaño 0: la foto de Odoo ──
    mejor = None
    d0_reportado: int | None = None
    if huella_odoo and huella_odoo.get("dh") is not None:
        pares = []
        for f in fids:
            for i, p in indices[f].fotos.items():
                d = 0 if p["sha"] == huella_odoo["sha"] else \
                    packing_indice.distancia(huella_odoo["dh"], p["dh"])
                pares.append((d, f, i, p["sha"]))
        pares.sort(key=lambda x: (x[0], x[1], x[2]))
        if pares:
            d0, f0, i0, sha0 = pares[0]
            # El segundo candidato se busca DEDUPLICANDO por contenido: un
            # packing list que repite la misma foto en varios renglones daría
            # margen 0 y mataría el peldaño sin razón.
            seg = next((d for d, f, i, sha in pares[1:] if sha != sha0), 64)
            d0_reportado = d0
            exacto = d0 == 0 and sha0 == huella_odoo["sha"]
            if exacto or (d0 <= UMBRAL_DHASH and (seg - d0) >= MARGEN_DHASH):
                mejor = (f0, i0,
                         "sha256" if exacto else "dhash",
                         "misma foto (sha256)" if exacto
                         else f"distancia {d0}/64 · 2º a {seg}",
                         0, "alta")

    # ── Peldaño 1 y 2: léxico informativo + IA ──
    cands_ia: list[dict[str, Any]] = []
    veredicto: list[dict[str, Any]] = []
    fid_ia: str | None = None
    if mejor is None and usar_ia:
        ml = _ml                       # ya se trajo arriba, para todos los SKUs
        if ml.get("titulo") and ml.get("foto"):
            tokens = [t for t in _norm(ml["titulo"]).split()
                      if len(t) > 2 and t not in _VACIAS]
            lex = 0
            # Se recorren TODOS los archivos del SKU, sin cortar en el primero:
            # cuando las fuentes de contenedor discrepan, el bueno puede ser el
            # segundo — que es justo el caso que la escalera existe para cubrir.
            for fid in fids:
                ix = indices[fid]
                lex += sum(1 for f in ix.filas
                           if any(t in _norm(ix.texto_fila(f.get("fila_excel")))
                                  for t in tokens))
                cs = _ia_titulo(ml["titulo"], ix)
                if not cs:
                    continue
                vs = _ia_foto(ml["titulo"], ml["foto"], ix,
                              [c.get("fila") for c in cs])
                cands_ia, veredicto, fid_ia = cs, vs, fid
                gana = next((v for v in vs if v.get("mismo_producto")), None)
                if not gana:
                    continue
                idx = ix.fila_de_idx.get(gana.get("fila"))
                if idx is None or idx not in ix.fotos:
                    continue
                # Segundo eje: si el nombre NO concuerda, el empate baja a
                # confianza "baja" y no se puede aprobar en lote.
                confianza = (gana.get("confianza") or "media").lower()
                nota = ""
                if gana.get("titulo_concuerda") is False:
                    confianza, nota = "baja", " · el nombre NO concuerda"
                mejor = (fid, idx, "ia",
                         f"la IA lo confirma ({confianza}){nota}", 2, confianza)
                break
            fila["lexico"] = lex

    fila["cands_ia"] = cands_ia
    fila["veredicto"] = veredicto
    if fid_ia and fid_ia in indices:
        ix = indices[fid_ia]
        fila["cands_txt"] = {str(c.get("fila")): ix.texto_fila(c.get("fila"))
                             for c in cands_ia if c.get("fila")}
        fila["cands_img"] = {}
        for c in cands_ia:
            i = ix.fila_de_idx.get(c.get("fila"))
            if i is not None and i in ix.fotos:
                uri = packing_resolver._miniatura(ix.fotos[i]["crudo"], _LADO_CAND_UI)
                if uri:
                    fila["cands_img"][str(c.get("fila"))] = uri

    if mejor is None:
        fila["estado"] = "sin_match"
        fila["peldano"] = 2 if cands_ia else (0 if huella_odoo else None)
        fila["detalle"] = (
            f"foto de Odoo a {d0_reportado}/64 y la IA no confirmó nada"
            if d0_reportado is not None else
            "sin foto en Odoo y la IA no confirmó nada")
        return fila

    fid, idx, metodo, detalle, peldano, confianza = mejor
    fila.update({"estado": metodo, "peldano": peldano, "detalle": detalle,
                 "confianza": confianza})
    _aplicar_renglon(fila, indices[fid], idx_foto=idx, guardado=guardado,
                     tarifa=tarifa, tc=tc)
    return fila


def _aplicar_renglon(fila: dict[str, Any], ix: packing_indice.Indice, *,
                     idx_foto: int | None = None, fila_excel: int | None = None,
                     guardado: dict[str, Any], tarifa: float, tc: float) -> None:
    """Cuelga de la fila el renglón elegido y sus números."""
    fe = fila_excel if fila_excel is not None else ix.idx_de_fila.get(idx_foto)
    if fe is None:
        fila["detalle"] += " · la foto no está anclada a ningún renglón legible"
        fila["estado"] = "sin_match"
        return
    if idx_foto is None:
        idx_foto = ix.fila_de_idx.get(fe)
    dd = ix.datos(fe)
    costo = packing_indice.costo_de(dd, tarifa, tc,
                                    _f(guardado.get("costo_producto")))
    fila.update({
        "file_id": ix.file_id or None, "archivo": ix.nombre, "fila_excel": fe,
        "producto_chn": ix.texto_fila(fe), "grupo": dd["grupo"],
        "precio_usd": _r(dd["precio_usd"], 4),
        "piezas_grupo": _r(dd["piezas_grupo"], 3),
        "cbm_pieza": _r(dd["cbm_por_pieza"], 6),
        # De dónde salió el volumen: lo necesita la procedencia para no acabar
        # diciendo "no_parseado" de un renglón que sí se parseó.
        "cbm_origen": dd.get("cbm_origen"),
        "caja_lwh": [_r(x, 2) for x in dd["caja_lwh"]],
        "pieza_lwh": [_r(x, 2) for x in dd["pieza_lwh"]],
        "peso_total": _r(dd["peso_total"], 3),
        "peso_pieza": _r(dd["peso_pieza"], 3),
        "flete": costo["flete"], "producto_mxn": costo["producto_mxn"],
        "origen_prod": costo["origen_prod"], "costo": costo["costo"],
        "img_pl": (packing_resolver._miniatura(ix.fotos[idx_foto]["crudo"],
                                               _LADO_FOTO_UI)
                   if idx_foto is not None and idx_foto in ix.fotos else None),
    })
    if not dd["cbm_por_pieza"]:
        fila["detalle"] += " · SIN volumen en el packing list: el flete quedó en 0"


# ── Insumos de la IA ─────────────────────────────────────────────────────────
def _publicacion_ml(sku: str, pubs: list[dict[str, Any]],
                    cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Título y primera foto del anuncio. Se cachea por SKU dentro del trabajo."""
    if sku in cache:
        return cache[sku]
    salida: dict[str, Any] = {"titulo": "", "foto": None}
    for p in pubs:
        item = p.get("item_id")
        if not item:
            continue
        try:
            # asyncio.run es legítimo AQUÍ: estamos en un hilo aparte, no en el
            # event loop del backend.
            datos = asyncio.run(meli.titulo_y_foto(item, p.get("cuenta")))
        except Exception as exc:  # noqa: BLE001
            log.warning("ML %s (%s) no contestó: %s", item, sku, exc)
            continue
        if datos.get("foto"):
            salida = datos
            break
        if datos.get("titulo") and not salida.get("titulo"):
            salida["titulo"] = datos["titulo"]
    cache[sku] = salida
    return salida


def _norm(t: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", t)


def _cliente_ia():
    if not settings.anthropic_api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _ia_titulo(titulo: str, ix: packing_indice.Indice) -> list[dict[str, Any]]:
    """
    Renglones que PODRÍAN ser este producto, por texto.

    Se le da el texto CRUDO de cada renglón, no la columna "producto": hay
    archivos donde esa columna trae el material ('plastics', 'EVA', 'nylon') y
    creerle al encabezado deja al modelo sin nada que reconocer.
    """
    cli = _cliente_ia()
    if cli is None:
        return []
    catalogo = "\n".join(
        f"{f.get('fila_excel')}|{ix.texto_fila(f.get('fila_excel'))}"
        for f in ix.filas)
    prompt = (
        f'Producto buscado (título de su publicación en Mercado Libre México):\n'
        f'"{titulo}"\n\nCatálogo de un packing list chino, una línea por renglón, '
        f'formato FILA|los textos de esa fila (nombre, material, uso) separados '
        f'por | .\n\nDevuelve SOLO JSON con los renglones que podrían ser ESE '
        f'MISMO producto, del más probable al menos:\n'
        f'{{"candidatos":[{{"fila":19,"por_que":"...","confianza":"alta|media|baja"}}]}}\n'
        f'Máximo {_MAX_CANDIDATOS_IA}. Lista vacía si ninguno encaja. Los nombres '
        f'chinos son más precisos que los ingleses.\n\nCATÁLOGO:\n{catalogo}')
    try:
        r = cli.messages.create(model=MODELO_TITULO, max_tokens=1200,
                                messages=[{"role": "user", "content": prompt}])
        data = comp._parse_json(r.content[0].text) or {}
        cands = data.get("candidatos") if isinstance(data, dict) else None
        return [c for c in (cands or []) if isinstance(c, dict) and c.get("fila")]
    except Exception as exc:  # noqa: BLE001
        log.warning("IA de título falló: %s", str(exc)[:160])
        return []


def _ia_foto(titulo: str, foto_ml: bytes, ix: packing_indice.Indice,
             filas_cand: list[Any]) -> list[dict[str, Any]]:
    """
    Veredicto visual: la foto del anuncio contra las de los renglones candidatos.

    Se pide ``titulo_concuerda`` aparte de ``mismo_producto`` a propósito: es el
    contrapeso que ya usa ``packing_comparador``, porque a esta resolución dos
    productos distintos pueden verse igual y el modelo lo afirma con seguridad.
    """
    cli = _cliente_ia()
    if cli is None:
        return []
    ml = comp._preparar(foto_ml, _LADO_IA)
    if not ml:
        return []
    partes: list[dict[str, Any]] = [
        {"type": "text", "text": f'Producto buscado: "{titulo}". Foto de su publicación:'},
        {"type": "image", "source": {"type": "base64", "media_type": ml[1],
                                     "data": ml[0]}},
    ]
    usados = 0
    for fe in (filas_cand or [])[:_MAX_CANDIDATOS_IA]:
        idx = ix.fila_de_idx.get(fe)
        if idx is None or idx not in ix.fotos:
            continue
        prep = comp._preparar(ix.fotos[idx]["crudo"], _LADO_IA)
        if not prep:
            continue
        partes.append({"type": "text",
                       "text": f"Renglón {fe} — {ix.texto_fila(fe)}:"})
        partes.append({"type": "image", "source": {
            "type": "base64", "media_type": prep[1], "data": prep[0]}})
        usados += 1
    if not usados:
        return []
    partes.append({"type": "text", "text":
        "Las fotos del packing list son de fábrica (otro fondo, otro ángulo, a "
        "veces con texto): NO serán idénticas. Juzga si es el MISMO producto "
        "físico.\nDevuelve SOLO JSON: "
        '{"veredicto":[{"fila":19,"mismo_producto":true,"titulo_concuerda":true,'
        '"confianza":"alta|media|baja","por_que":"..."}]}, de más a menos '
        "probable. `titulo_concuerda` es si el nombre del renglón describe el "
        "mismo tipo de producto que el título buscado."})
    try:
        r = cli.messages.create(model=MODELO_FOTO, max_tokens=1200,
                                messages=[{"role": "user", "content": partes}])
        data = comp._parse_json(r.content[0].text) or {}
        vs = data.get("veredicto") if isinstance(data, dict) else None
        return [v for v in (vs or []) if isinstance(v, dict) and v.get("fila")]
    except Exception as exc:  # noqa: BLE001
        log.warning("IA de foto falló: %s", str(exc)[:160])
        return []


# ── Correcciones a mano ──────────────────────────────────────────────────────
def corregir_fila(jid: str, sku: str, file_id: str | None,
                  fila_excel: int) -> dict[str, Any] | None:
    """
    "No es ese renglón, es el 34."

    Se re-lee el archivo (viene de la caché en disco, no de Drive) y se recalcula
    SOLO ese SKU: aquí el flete no se prorratea entre renglones, así que mover
    uno no mueve a los demás — al revés que en el Resolver clásico.
    """
    with _lock:
        t = _trabajos.get(jid)
        if not t:
            return None
        fila = next((f for f in t["filas"] if f["sku"] == sku.strip().upper()), None)
        if fila is None:
            return None
        opciones = dict(t["opciones"])
        fid = (file_id or fila.get("file_id") or "").strip()
        archivo = fila.get("archivo") or ""
    if not fid:
        return None

    ix = _indice_cacheado(jid, fid, archivo)
    if ix is None:
        return None
    if fila_excel not in ix.por_fila:
        raise ValueError(f"El renglón {fila_excel} no existe en {ix.nombre}.")

    guardado = costing_read.validados_de([sku])
    nueva = dict(fila)
    nueva.update({"fuente": "manual", "confianza": "alta",
                  "detalle": "elegido a mano"})
    if nueva["estado"] in ("sin_match", "sin_insumo"):
        # Un renglón que eligió un humano SÍ es un empate; si se dejara en
        # "sin_match" el guardado lo saltaría para siempre.
        nueva["estado"], nueva["peldano"] = "ia", 2
    _aplicar_renglon(nueva, ix, fila_excel=fila_excel,
                     guardado=guardado.get(sku.strip().upper()) or {},
                     tarifa=opciones["tarifa_mxn_m3"], tc=opciones["tipo_cambio"])
    with _lock:
        t = _trabajos.get(jid)
        if not t:
            return None
        for i, f in enumerate(t["filas"]):
            if f["sku"] == nueva["sku"]:
                t["filas"][i] = nueva
                break
        t["resumen"] = _resumen(t["filas"])
    return nueva


def _indice_cacheado(jid: str, file_id: str,
                     nombre: str) -> packing_indice.Indice | None:
    """El índice de un archivo, sin volver a bajarlo ni re-parsearlo si se puede.

    La bajada y el parseo quedan FUERA del lock a propósito: son red y disco, y
    tenerlo tomado ahí congelaría el polling de todos los demás trabajos.
    """
    if ix := _indices_de(jid).get(file_id):
        return ix
    try:
        datos = carpeta.bajar(file_id, nombre)
        ix = packing_indice.indexar(datos, nombre or file_id, file_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo re-indexar %s: %s", nombre or file_id, exc)
        return None
    # Si el trabajo caducó mientras bajábamos, el índice se usa esta vez y no se
    # guarda: guardarlo sería dejar un huérfano que nadie va a liberar.
    _guardar_indices(jid, {file_id: ix})
    return ix


def agregar_archivo(jid: str, sku: str, url: str) -> dict[str, Any] | None:
    """
    Escape para los SKUs sin contenedor en ninguna fuente: la liga del packing
    list a mano. Se baja, se indexa y se le corre la escalera a ese SKU solo.
    """
    sku = (sku or "").strip().upper()
    with _lock:
        t = _trabajos.get(jid)
        if not t:
            return None
        fila = next((f for f in t["filas"] if f["sku"] == sku), None)
        if fila is None:
            return None
        opciones = dict(t["opciones"])
        pub = {"item_id": fila.get("item_id_ml"), "cuenta": fila.get("cuenta_ml"),
               "situacion": fila.get("situacion_ml")}

    if not packing_drive.es_url_drive(url):
        raise packing_drive.DriveError("Por ahora solo se aceptan ligas de Google Drive.")
    datos, nombre = packing_drive.descargar(url)
    fid, _ = packing_drive.extraer_id(url)
    ix = packing_indice.indexar(datos, nombre, fid)
    if not _guardar_indices(jid, {fid: ix}):
        return None       # el trabajo caducó mientras bajábamos el archivo

    fotos = odoo.imagenes_1920_por_sku([sku])
    huella = None
    if b := fotos.get(sku):
        huella = {"crudo": b, "sha": _sha(b), "dh": packing_indice.dhash(b)}
    guardado = (costing_read.validados_de([sku]) or {}).get(sku) or {}
    nueva = _resolver_uno(
        sku=sku, padre=fila.get("padre"), pubs=[pub] if pub.get("item_id") else [],
        fids=[fid], indices={fid: ix}, huella_odoo=huella,
        refs=[("manual", nombre)], guardado=guardado,
        tarifa=opciones["tarifa_mxn_m3"], tc=opciones["tipo_cambio"],
        usar_ia=opciones["usar_ia"], cache_ml={})
    nueva["fuente"] = "manual"
    with _lock:
        t = _trabajos.get(jid)
        if not t:
            return None
        for i, f in enumerate(t["filas"]):
            if f["sku"] == sku:
                t["filas"][i] = nueva
                break
        t["resumen"] = _resumen(t["filas"])
    return nueva


# ── Guardado (lo ÚNICO que escribe) ──────────────────────────────────────────
# `cbm_origen` de este módulo → el vocabulario que acepta la tabla. Son dos
# preguntas distintas metidas en una columna, así que el orden importa: si el
# cartón se COMPARTE eso manda, porque es lo que obliga a repartir el flete y lo
# que hace falta saber para releer el número. Solo cuando la caja es de un solo
# SKU tiene sentido decir de qué columna salió su volumen.
_CBM_ORIGEN = {
    "volumen_total": "total_volume",
    "columna_caja": "caja_propia",
    "medidas": "caja_propia",
    "sin_datos": "sin_datos",
}


# Igual que `carpeta.RE_COD` pero con las ramas ordenadas de MÁS a MENOS
# específica, y es local a propósito: `RE_COD` lo usa el emparejado de archivos,
# donde compara por prefijo y un dígito de menos no le duele. Aquí sí duele,
# porque el código truncado se vuelve parte de la LLAVE de la tabla.
_RE_CONTENEDOR = re.compile(
    r"(?:SZLS\d{6,9}"          # el prefijo propio, antes que la forma genérica
    r"|[A-Z]{4}[A-Z0-9]{8,14}"  # ONEYNB5BEK841700 y parientes
    r"|[A-Z]{4}\d{6,7}"         # el contenedor estándar: MRKU3436938
    r"|\d{9,12})")


def _contenedor_de(archivo: str, ref: str) -> str:
    """
    El código de contenedor que le corresponde a ESTE archivo.

    Se saca del NOMBRE DEL ARCHIVO, no de la referencia con la que se salió a
    buscar, y la razón es que a veces no son el mismo embarque: la escalera
    prueba las referencias de kubera y de Odoo, y el renglón puede aparecer en
    el archivo de la otra. JUGU-0039-MUL se resolvió en
    ``SZLS50224700=CI&PL`` mientras Odoo decía ``TXGU7222939 contenedor 7``.
    Guardar el segundo junto al primero sería una procedencia que se contradice
    sola, y el punto de esta tabla es poder volver al renglón.

    La referencia queda de respaldo, y solo si trae un código reconocible: el
    texto libre de Odoo (``MRKU3436938 contenedor 9``) no sirve tal cual porque
    ``contenedor_base`` solo quita el sufijo ``- N``, así que ese ruido se
    quedaría pegado en la llave y el mismo embarque abriría dos filas.

    Y se toma el match MÁS LARGO, no el primero: ``RE_COD`` es una alternancia
    y en Python gana la rama que empata antes, no la que empata más. Con
    ``SZLS50214600`` la rama genérica ``[A-Z]{4}\\d{6,7}`` se lleva
    ``SZLS5021460`` y se come el último dígito — un código truncado abre una
    fila nueva por cada variante y rompe justo lo que la llave pretende unir.
    """
    for cand in (archivo, ref):
        cands = [m.group(0) for m in _RE_CONTENEDOR.finditer((cand or "").upper())]
        if cands:
            return max(cands, key=len)
    return comp.normalizar_contenedor(ref or "")


def _registrar_procedencia(sku: str, fila: dict[str, Any]) -> None:
    """Deja el archivo y los renglones de los que salió el costo de este SKU."""
    archivo = (fila.get("archivo") or "").strip()
    contenedor = _contenedor_de(archivo, fila.get("ref") or "")
    # `grupo` trae el cartón COMPARTIDO; vacío significa caja propia, y entonces
    # el renglón es uno: el suyo. La tabla exige al menos uno.
    renglones = [int(r) for r in (fila.get("grupo") or []) if r] or (
        [int(fila["fila_excel"])] if fila.get("fila_excel") else [])
    if not (archivo and contenedor and renglones):
        raise ValueError("falta archivo, contenedor o renglón")

    compartida = len(renglones) > 1
    origen = ("caja_compartida" if compartida
              else _CBM_ORIGEN.get(fila.get("cbm_origen") or "", "no_parseado"))
    piezas = fila.get("piezas_grupo")
    cbm_pieza = fila.get("cbm_pieza")
    costing_write.guardar_caja_compartida(
        sku, contenedor, archivo, renglones,
        piezas_grupo=float(piezas) if piezas else None,
        # El CBM del CARTÓN, que es lo que se repartió: por pieza × piezas.
        cbm_grupo=(round(float(cbm_pieza) * float(piezas), 6)
                   if cbm_pieza and piezas else None),
        cbm_origen=origen,
        nota=f"validador de publicados · {fila.get('estado') or '—'}"
             + (f" · peldaño {fila['peldano']}" if fila.get("peldano") else ""))


def guardar(jid: str, skus: list[str],
            liberar_candado: list[str] | None = None) -> dict[str, Any] | None:
    """
    UPSERT a ``costing.costos_validados`` de los SKUs que el humano aprobó.

    EL ORDEN IMPORTA y es la trampa de todo este flujo: el candado de COSTO
    VALIDADO vive DENTRO del SQL de ``costing_mirror.upsert_validados``
    (``where costos_validados.revisado_at is null``). Si el SKU está marcado, el
    UPDATE se descarta **en silencio, sin error**: el usuario ve "guardado" y el
    costo sigue siendo el viejo. Por eso: liberar → escribir → marcar.

    Va a kubera y NO a MySQL (``escribir_mysql`` es un noop deliberado): la
    ``costos_validados`` de MySQL está congelada desde el 13-ago y escribir ahí
    no mueve ningún precio.
    """
    with _lock:
        t = _trabajos.get(jid)
        if not t:
            return None
        if t.get("paso") != "listo":
            raise RuntimeError("El análisis todavía no termina.")
        filas = {f["sku"]: dict(f) for f in t["filas"]}

    pedidos = [s.strip().upper() for s in (skus or []) if (s or "").strip()]
    liberar = {s.strip().upper() for s in (liberar_candado or [])}
    escritos, detalle, saltados, errores = 0, [], [], []

    # La regla crítica, otra vez y justo antes de escribir: el trabajo vive
    # hasta 3 h y una publicación pudo cerrarse en el ínterin. Además el `jid`
    # es adivinable por API, y esto es lo irreversible.
    #
    # Se pregunta por el SKU **PEDIDO**, no por el de la fila. Cuando el pedido
    # era un PADRE, las filas son sus VARIANTES, y la variante casi nunca tiene
    # publicación propia: quien está en `channel.listings` es el padre. Validar
    # la variante aquí —cuando `iniciar` había validado el padre— hacía que las
    # dos puertas preguntaran por sujetos distintos, y la de salida rechazaba
    # todo lo que la de entrada había dejado pasar. Medido: de 305 padres
    # publicados, 209 terminaban con CERO variantes escribibles (TEC-0377
    # gastaba 103 corridas de IA para no escribir nada).
    sujeto = {s: ((filas.get(s) or {}).get("padre") or s) for s in pedidos}
    vivos = channel_read.publicados_ml(sorted(set(sujeto.values())))

    for sku in pedidos:
        fila = filas.get(sku)
        if fila is None:
            saltados.append({"sku": sku, "motivo": "sin_costo",
                             "detalle": "ese SKU no está en este análisis"})
            continue
        if not vivos.get(sujeto[sku]):
            # El motivo tiene que decir la verdad: si el sujeto es el padre, lo
            # que se cerró es la publicación del padre, no "la de este SKU".
            quien = sujeto[sku]
            saltados.append({
                "sku": sku, "motivo": "no_publicado_ml",
                "detalle": ("ya no tiene publicación viva en Mercado Libre"
                            if quien == sku else
                            f"su SKU padre {quien} ya no tiene publicación viva "
                            f"en Mercado Libre")})
            continue
        if comp.es_provisional(sku):
            saltados.append({"sku": sku, "motivo": "sku_provisional",
                             "detalle": "identificador provisional, no es un SKU "
                                        "de Kubera"})
            continue
        if fila.get("estado") in ("sin_match", "sin_insumo") or not fila.get("costo"):
            saltados.append({"sku": sku, "motivo": "sin_costo",
                             "detalle": "no se le encontró renglón en el packing list"})
            continue
        pieza = fila.get("pieza_lwh") or []
        if not (len(pieza) == 3 and all(_f(x) for x in pieza)):
            # Un cero aquí infla el peso volumétrico en CADA venta de ese SKU.
            saltados.append({"sku": sku, "motivo": "sin_dimensiones",
                             "detalle": "el renglón no trae medidas de caja"})
            continue

        bloqueo = costing_read.bloqueado(sku)
        if bloqueo and sku not in liberar:
            cuando = bloqueo.get("revisado_at")
            saltados.append({
                "sku": sku, "motivo": "ya_validado",
                "detalle": f"tiene COSTO VALIDADO del "
                           f"{cuando.date().isoformat() if cuando else '—'}; "
                           f"márcalo para liberar el candado"})
            continue

        # ── La escritura, y por qué el candado se restaura ───────────────────
        # El candado vive DENTRO del UPSERT (`where revisado_at is null`), así
        # que para reescribir un SKU ya validado hay que soltarlo antes. Eso
        # abre una ventana: si la escritura de en medio falla, el SKU se queda
        # DESBLOQUEADO y el siguiente "Regenerar y guardar" de la pantalla de
        # Costos le pisa el costo reconstruido a mano — justo lo que el candado
        # existe para impedir.
        #
        # Y el fallo no siempre grita: `guardar_validados` pasa por `_escribir`,
        # que con kubera caída ATRAPA el error, encola el evento y retorna
        # normal. Por eso la confirmación no puede ser "no explotó", sino el
        # valor de retorno de `marcar_revisado`, que devuelve `None` cuando no
        # encontró fila que marcar. Sin eso el panel decía "guardado" con kubera
        # vacía y el candado quitado.
        solté = False
        try:
            if bloqueo:
                costing_write.marcar_revisado(sku, False)
                solté = True
            costing_write.guardar_validados(
                sku,
                {"largo": _f(pieza[0]), "ancho": _f(pieza[1]), "alto": _f(pieza[2]),
                 "peso": _f(fila.get("peso_pieza")) or None,
                 "costo_producto": _f(fila.get("producto_mxn")),
                 "costo_cbm": _f(fila.get("flete")),
                 "costo_total": _f(fila.get("costo"))},
                escribir_mysql=lambda: None,
                accion="resolver-publicados",
                origen="panel-costos")
            confirmado = costing_write.marcar_revisado(sku, True)
            if not confirmado:
                # No hay fila en kubera: la escritura no llegó (o se encoló).
                # Contarlo como escrito sería mentirle al usuario.
                raise RuntimeError(
                    "la escritura no llegó a kubera (el costeo se encoló para "
                    "reintento); no se marcó como validado")
            solté = False          # el candado quedó puesto de nuevo
        except Exception as exc:  # noqa: BLE001
            log.exception("no se pudo guardar el costo de %s", sku)
            aviso = str(exc)[:200]
            if solté:
                # Compensación: devolver el candado que este bloque quitó.
                try:
                    costing_write.marcar_revisado(sku, True)
                except Exception:  # noqa: BLE001
                    log.exception("además, no se pudo restaurar el candado de %s", sku)
                    aviso += (" · ATENCIÓN: el candado de COSTO VALIDADO quedó "
                              "QUITADO y no se pudo restaurar")
            errores.append({"sku": sku, "error": aviso})
            continue

        escritos += 1

        # ── De dónde salió este costo ────────────────────────────────────────
        # El costo por sí solo es una cifra sin origen: si mañana no cuadra, no
        # hay cómo volver al renglón que lo produjo. Aquí queda el archivo y los
        # renglones exactos. Va DESPUÉS de contar el escrito y en su propio try
        # porque es RASTRO, no el dato: que falle la bitácora no puede tumbar un
        # costo que ya quedó bien guardado.
        try:
            _registrar_procedencia(sku, fila)
        except Exception as exc:  # noqa: BLE001
            log.warning("procedencia de %s no registrada: %s", sku, exc)

        detalle.append({"sku": sku, "costo": fila.get("costo"),
                        "costo_anterior": fila.get("costo_viejo")})

    resultado = {"escritos": escritos, "detalle": detalle,
                 "saltados": saltados, "errores": errores}
    with _lock:
        if t := _trabajos.get(jid):
            t["guardado"] = {**resultado, "cuando": time.time()}
    return resultado


# ── Utilidades ───────────────────────────────────────────────────────────────
def _f(v: Any) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _r(v: Any, dec: int) -> float | None:
    x = _f(v)
    return round(x, dec) if x else None
