"""
ia_variante.py — Lo que la IA necesita saber cuando el producto es UNA VARIANTE.

EL PROBLEMA, MEDIDO (11-sep-2026)
─────────────────────────────────
Ningún prompt de "Mejorar con IA" decía que el producto era una variante, ni
cuál era su valor en cada eje. General, ML y Amazon ni siquiera metían el SKU.
Y **1,541 variantes tienen el MISMO título que su padre**: `ACC-0234-GALAXYS23ULTRA`
y `ACC-0234-GALAXYS22PLUS` llegaban a la IA como el mismo texto, así que salían
dos fichas idénticas — o una que hablaba de "S22 Plus | S23 Ultra | …".

Peor: los atributos que manda el Estudio son los del PADRE, y en el padre un eje
vale la LISTA de la familia ("3 piezas | 6 piezas"). Así se le pedía a la IA el
contenido de un producto que es las seis cosas a la vez.

LO QUE HACE
───────────
1. `preparar()` — lo llama el router antes de despachar al generador: resuelve
   la variante por su SKU, lee el valor FIJADO de cada eje con
   `wp_db._atributos_de_variacion` (nunca la lista del padre), marca cuáles la
   DISTINGUEN de sus hermanas, sanea los atributos que traiga el contexto y le
   pone la descripción del padre si no trae propia. Deja todo en
   `producto["variante"]`.
2. `bloque()` — el texto que TODOS los generadores inyectan en su prompt. Uno
   solo, a propósito: si cada canal redactara el suyo, un día dirían cosas
   distintas y nadie sabría cuál es el bueno.
3. `sku_padre_para_ia()` — para heredar la CATEGORÍA del padre al generar
   (TikTok, Temu, Walmart), sin tocar lo que lee el publicador.

NUNCA ROMPE: si WordPress no contesta, "Mejorar con IA" sigue como antes, sin
el bloque. Una variante mal descrita es peor que bien descrita, pero mejor que
un botón que no responde.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Any

log = logging.getLogger("omnicanal.ia_variante")

# Separador con el que WooCommerce guarda las opciones de un atributo
# personalizado ("3 piezas | 6 piezas"). Si aparece en el contexto de una
# variante, es la lista de la FAMILIA colándose.
_SEP_LISTA = " | "


def _norm(texto: str) -> str:
    """Nombre de eje comparable: sin `pa_`, sin acentos, minúsculas, sin guiones."""
    t = str(texto or "").strip().lower()
    if t.startswith("pa_"):
        t = t[3:]
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[\s_\-]+", " ", t).strip()


def _nombre_eje(nombre: str) -> str:
    """`pa_color` → `Color`. Los de taxonomía llegan con el slug crudo."""
    n = str(nombre or "").strip()
    if n.lower().startswith("pa_"):
        n = n[3:].replace("-", " ").replace("_", " ").strip().capitalize()
    return n


def _sin_html(texto: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", texto or "")).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Lectura (síncrona: pymysql). Se llama SIEMPRE desde un hilo — regla 11.
# ─────────────────────────────────────────────────────────────────────────────
def _resolver(sku: str, wc_id: int | None) -> tuple[int | None, int | None]:
    """
    (wc_id de la variante, wc_id del padre) — o (x, None) si no es variante.

    El SKU MANDA sobre el `wc_id` que llegue. Medido en el Estudio (v0.498.0):
    al abrir una variante, el front mandaba el `wc_id` del PADRE junto al SKU de
    la variante; fiarse de él haría que `padre_de` contestara "no es variación"
    y el bloque no saliera nunca. El `wc_id` que llega es solo el respaldo para
    cuando el SKU no se encuentra.
    """
    from services import wp_db
    ficha = (wp_db.productos_por_sku([sku]) or {}).get(sku) or {}
    propio = ficha.get("wc_id") or wc_id
    if not propio:
        return None, None
    if ficha.get("wc_id") and wc_id and int(ficha["wc_id"]) != int(wc_id):
        log.info("ia_variante(%s): llegó wc_id=%s pero el SKU es el %s — manda el SKU",
                 sku, wc_id, ficha["wc_id"])
    return int(propio), wp_db.padre_de(int(propio))


def contexto_sync(sku: str, wc_id: int | None = None) -> dict[str, Any] | None:
    """
    El contexto de variante de un SKU, o None si no es variante.

      { sku, wc_id, padre_sku, padre_wc_id, padre_titulo, padre_descripcion,
        descripcion_propia,
        ejes: [{eje, valor, distintivo, opciones_familia}] }

    `ejes` solo trae los que la variante FIJA (un valor). Los que deja en "Any"
    son atributos del producto, no del ejemplar, y no se afirman como suyos.
    Un eje DISTINGUE a la variante cuando en el padre tiene más de una opción:
    `HERR-0029-GRI-3P` fija Color = Gris y Cantidad = 3 piezas, pero si toda la
    familia es gris, lo que la separa de sus hermanas es solo la cantidad.
    """
    from services import wp_db

    sku = (sku or "").strip()
    if not sku or not wp_db.disponible():
        return None
    propio, padre = _resolver(sku, wc_id)
    if not (propio and padre):
        return None

    # LOS EJES SALEN DE LA VARIANTE, NO DEL PADRE. Medido el 11-sep en la prueba
    # en seco: `_atributos_de_variacion` devuelve también los atributos de
    # PRODUCTO del padre (los que valen una sola cosa), y ahí viven residuos
    # como `COMPATIBLE_CELLPHONE = Samsung S22+` en la funda del S23 Ultra
    # (`ACC-0234-GALAXYS23ULTRA`) o `Celular compatible = iPhone 14 Pro Max` en
    # la del ZTE Blade (`TEC-0377-ZTEBLA35`). Decirle a la IA que eso es "valor
    # fijado" la empuja a mentir. Un eje es SOLO lo que la variación guarda en
    # su propia meta `attribute_<slug>`.
    P = wp_db._prefix()  # noqa: SLF001
    propios = {
        str(r["meta_key"])[len("attribute_"):].lower(): str(r["meta_value"] or "").strip()
        for r in wp_db._fetch_all(  # noqa: SLF001
            f"""SELECT meta_key, meta_value FROM {P}postmeta
                 WHERE post_id = %s AND meta_key LIKE 'attribute\\_%%'""", (propio,))
    }
    propios = {s: v for s, v in propios.items() if v}   # vacío = eje "Any"

    # Cuántos valores distintos tiene cada eje ENTRE LAS HERMANAS. Es la medida
    # honesta de "lo que la distingue": el padre de `TEC-0988` declara Voltaje,
    # pero todas sus variantes son 9 V — lo que separa a la rosa es el color.
    # Y hay ejes que el padre ni declara (`attribute_variante` de TEC-0377).
    distintos: dict[str, int] = {}
    if propios:
        ph = ",".join(["%s"] * len(propios))
        for r in wp_db._fetch_all(  # noqa: SLF001
                f"""SELECT m.meta_key, COUNT(DISTINCT m.meta_value) AS n
                      FROM {P}postmeta m
                      JOIN {P}posts v ON v.ID = m.post_id
                                     AND v.post_type = 'product_variation'
                                     AND v.post_status <> 'trash'
                     WHERE v.post_parent = %s AND m.meta_value <> ''
                       AND m.meta_key IN ({ph})
                     GROUP BY m.meta_key""",
                (padre, *[f"attribute_{s}" for s in propios])):
            distintos[str(r["meta_key"])[len("attribute_"):].lower()] = int(r["n"] or 0)

    # El nombre bonito y la etiqueta del término (pa_color: slug → "Gris") los
    # da `_atributos_de_variacion`; si el padre no declara el eje, va crudo.
    bonitos = {_norm(a["name"]): (a["name"], (a.get("options") or [None])[0])
               for a in wp_db._atributos_de_variacion(propio, padre)  # noqa: SLF001
               if len(a.get("options") or []) == 1}
    ejes: list[dict[str, Any]] = []
    for slug, crudo in propios.items():
        nombre, valor = bonitos.get(_norm(slug), (slug, crudo))
        ejes.append({"eje": _nombre_eje(nombre) if nombre != slug
                     else _nombre_eje(f"pa_{slug}" if not slug.startswith("pa_") else slug),
                     "valor": str(valor or crudo),
                     "distintivo": distintos.get(slug, 0) > 1,
                     "opciones_familia": distintos.get(slug, 0)})

    ficha_padre = wp_db.ficha_basica(padre) or {}
    post_padre = wp_db.producto_wp(padre) or {}
    # La descripción: mismo respaldo que `publicar_ready.construir_prod` — la de
    # la variación (`_variation_description`) y, si está vacía, la del padre.
    # `post_content` de una variación está vacío en las 7,477 del catálogo.
    propia = (wp_db.postmeta(propio, ["_variation_description"])
              .get("_variation_description") or "").strip()
    return {
        "sku": sku, "wc_id": propio,
        "padre_sku": ficha_padre.get("sku") or wp_db.sku_padre(sku) or "",
        "padre_wc_id": padre,
        "padre_titulo": (post_padre.get("post_title") or ficha_padre.get("name") or "").strip(),
        "padre_descripcion": (post_padre.get("post_content")
                              or post_padre.get("post_excerpt") or "").strip(),
        "descripcion_propia": propia,
        "ejes": ejes,
    }


def sanear_atributos(atributos: list[dict[str, Any]],
                     ctx: dict[str, Any] | None) -> list[dict[str, Any]]:
    """
    Cambia la LISTA de la familia por el valor FIJADO del mismo eje.

    El Estudio siembra la ficha con los atributos del padre, donde Cantidad vale
    "3 piezas | 6 piezas". Con eso delante la IA escribe un título que ofrece
    las dos cosas. Se reemplaza solo cuando hay valor fijado para ese eje: un
    eje "Any" es de verdad todas sus opciones y se deja como está.
    """
    if not ctx or not atributos:
        return atributos
    fijado = {_norm(e["eje"]): e["valor"] for e in ctx.get("ejes") or []}
    salida = []
    for a in atributos:
        valor = str(a.get("valor") or "")
        if _SEP_LISTA in valor:
            nuevo = fijado.get(_norm(a.get("nombre") or ""))
            if nuevo:
                a = {**a, "valor": nuevo}
        salida.append(a)
    return salida


async def preparar(producto: dict[str, Any]) -> dict[str, Any]:
    """
    Enriquece el contexto del producto si es una variante. Nunca lanza.

    Deja `producto["variante"]` (o None), sanea `atributos` y rellena
    `descripcion` si viene vacía. Todo lo de WordPress va en un hilo: es
    pymysql, y en una corrutina detendría el backend entero (regla 11).
    """
    sku = str(producto.get("sku") or "").strip()
    producto.setdefault("variante", None)
    if not sku:
        return producto
    try:
        ctx = await asyncio.to_thread(contexto_sync, sku, producto.get("wc_id"))
    except Exception as exc:  # noqa: BLE001
        log.warning("ia_variante.preparar(%s): sin contexto de variante: %s", sku, exc)
        return producto
    return aplicar(producto, ctx)


def preparar_sync(producto: dict[str, Any]) -> dict[str, Any]:
    """`preparar` para los endpoints síncronos (ya corren en el threadpool)."""
    sku = str(producto.get("sku") or "").strip()
    producto.setdefault("variante", None)
    if not sku:
        return producto
    try:
        ctx = contexto_sync(sku, producto.get("wc_id"))
    except Exception as exc:  # noqa: BLE001
        log.warning("ia_variante.preparar_sync(%s): sin contexto de variante: %s", sku, exc)
        return producto
    return aplicar(producto, ctx)


def aplicar(producto: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    """Pone el contexto ya leído en el producto (puro, sin red)."""
    if not ctx:
        return producto
    producto["variante"] = ctx
    producto["atributos"] = sanear_atributos(producto.get("atributos") or [], ctx)
    if not str(producto.get("descripcion") or "").strip():
        producto["descripcion"] = ctx.get("descripcion_propia") or ctx.get("padre_descripcion") or ""
    return producto


# ─────────────────────────────────────────────────────────────────────────────
# El texto — uno para todos los canales
# ─────────────────────────────────────────────────────────────────────────────
def texto_bloque(ctx: dict[str, Any] | None) -> str:
    """El bloque VARIANTE del prompt, o "" si el producto no es variante."""
    if not ctx:
        return ""
    padre = ctx.get("padre_sku") or "(sin SKU)"
    titulo = ctx.get("padre_titulo") or ""
    ejes = ctx.get("ejes") or []
    fijados = "; ".join(f"{e['eje']} = {e['valor']}" for e in ejes) or "(ninguno declarado)"
    distintivos = [f"{e['eje']} = {e['valor']}" for e in ejes if e.get("distintivo")]
    lineas = [
        "VARIANTE — LEE ESTO ANTES DE ESCRIBIR",
        f"Este producto es UNA VARIANTE de la familia {padre}"
        + (f" («{titulo}»)" if titulo else "") + f". SKU: {ctx.get('sku')}.",
        f"Valores fijados de esta variante: {fijados}.",
    ]
    if distintivos:
        lineas.append("Lo que la distingue de sus hermanas: " + "; ".join(distintivos) + ".")
    # Los atributos de producto del padre pueden contradecir a la variante (la
    # funda del ZTE Blade hereda «Celular compatible = iPhone 14 Pro Max»).
    lineas.append(
        "Si algún atributo o dato del producto contradice estos valores, MANDAN "
        "los de la variante: el resto es de la familia y puede ser de otra hermana.")
    lineas.append(
        "Escribe SOLO sobre esta variante: el título y los atributos deben llevar "
        "su valor distintivo y NO deben mencionar las otras opciones de la familia "
        "(nada de listas como «3 piezas | 6 piezas» ni «varios modelos»). Si el "
        "título o la descripción actuales son los de la familia, adáptalos a esta "
        "variante.")
    return "\n".join(lineas)


def bloque(producto: dict[str, Any] | None) -> str:
    """`texto_bloque` a partir del producto que ya pasó por `preparar`."""
    return texto_bloque((producto or {}).get("variante"))


# ─────────────────────────────────────────────────────────────────────────────
# Categoría heredada — SOLO para generar, nunca para publicar
# ─────────────────────────────────────────────────────────────────────────────
def sku_padre_para_ia(producto_o_sku: dict[str, Any] | str) -> str:
    """
    SKU del padre, por la ESTRUCTURA de Woo (`post_parent`), para heredar su
    categoría al generar. Síncrono: llamarlo desde un hilo.

    Nunca por prefijo del SKU: `ACC-0234` es la familia de fundas, pero hay
    SKUs reciclados (`EST-0091` es DOS productos distintos) donde el prefijo
    miente. Si el producto ya pasó por `preparar`, se usa lo que leyó; si no,
    se pregunta a WordPress.
    """
    if isinstance(producto_o_sku, dict):
        ctx = producto_o_sku.get("variante") or {}
        if ctx.get("padre_sku"):
            return str(ctx["padre_sku"])
        sku = str(producto_o_sku.get("sku") or "").strip()
    else:
        sku = str(producto_o_sku or "").strip()
    if not sku:
        return ""
    try:
        from services import wp_db
        return wp_db.sku_padre(sku) if wp_db.disponible() else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("ia_variante.sku_padre_para_ia(%s): %s", sku, exc)
        return ""


def aviso_heredada(canal: str, padre: str, categoria: str | None) -> str:
    return (f"Categoría de {canal} HEREDADA del padre {padre} ({categoria}): la "
            f"variante no tiene una propia. Sirve para generar; para publicar, "
            f"elige la categoría de la variante en el panel.")
