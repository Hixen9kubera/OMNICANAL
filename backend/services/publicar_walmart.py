"""
publicar_walmart.py — Publicar en Walmart MX DESDE EL PANEL, con vista previa.

Pieza 5. `scripts/publicar_walmart.py` publica por TANDAS; esto es el otro
camino: un producto, un botón, y el payload a la vista antes de mandarlo.

EL PAYLOAD ES EL SUYO, LITERAL. Se importan `_item()` y `_sobre()` del script,
que ya están verificados contra feeds reales y llevan dentro cada corrección que
costó un lote (máximo 2 decimales en peso y medidas, la poda de campos por
categoría, la clave SAT, la exención de UPC). Reescribirlos aquí habría creado
una segunda verdad que se desincroniza.

⚠️ LO QUE HACE DISTINTO A ESTE CANAL: EL PRESUPUESTO
────────────────────────────────────────────────────
Walmart admite **10 feeds POR HORA** de `MP_ITEM_INTL`. Un botón que mande un
feed por producto quema la hora en 10 clics, y lo que sigue muere con
`REQUEST_THRESHOLD_VIOLATED` — que es exactamente lo que tumbó 19 de 24
productos **sin que sus datos tuvieran nada malo**.

Por eso este publicador **cuenta antes de mandar**: mira los feeds de la última
hora en `ops.channel_submissions` y se niega cuando no queda cuota, diciendo
desde cuándo se libera. Es el único canal donde el panel tiene que frenar al
usuario, y frenarlo sale más barato que perder el intento.

Y OTRA DIFERENCIA: AQUÍ NO HAY "PUBLICADO" INMEDIATO
────────────────────────────────────────────────────
Walmart contesta con un `feedId`, no con un veredicto. El resultado real llega
minutos después y se consulta por SKU. El panel dice "feed enviado" y no
"publicado", porque dar por bueno el envío fue lo que produjo los "9 feeds sin
fallos" del 4-ago que en realidad fueron cero.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("omnicanal.publicar_walmart")

CANAL = "walmart"
CUENTA = "WALMART"
FEEDS_POR_HORA = 10

# ── EL CANDADO CONTRA REENVÍOS (7-oct-2026) ──────────────────────────────────
# Walmart tumba un SKU reenviado mientras el envío anterior sigue vivo:
#     EXT_DATA_ERROR_56026862530206  "This item is currently under compliance
#     review from your previous submission and cannot be resubmitted until the
#     review is complete, which may take up to 48 hours."
# (en Seller Center: SOURCE_DP_PENDING). Medido en dos formas:
#   · ORG-1078-BLN, 3-oct: reenviado a los 2 MINUTOS, con el primero sin
#     veredicto. El reenvío murió por esto.
#   · ELEC-0146-PC6-NAR, 12-sep: el primero salió SUCCESS; reenviado 77 minutos
#     después, murió por esto. Un SUCCESS no libera: abre la revisión.
VENTANA_ENVIOS_H = 72        # hasta dónde se mira hacia atrás en la bitácora
REVISION_TRAS_EXITO_H = 48   # "which may take up to 48 hours"
GRACIA_SIN_VEREDICTO_MIN = 30


def _presupuesto() -> dict[str, Any]:
    """Cuántos feeds quedan en la hora vigente, según la bitácora."""
    from services import supabase_db as sdb
    try:
        filas = sdb.fetch_all(
            """select count(distinct submission_id) as usados,
                      min(submitted_at) as primero
                 from ops.channel_submissions
                where canal = %s
                  and submitted_at > now() - interval '1 hour'
                  and submission_id is not null and submission_id <> ''""",
            (CANAL,))
        f = (filas or [{}])[0]
        usados = int(f.get("usados") or 0)
        return {"usados": usados, "quedan": max(0, FEEDS_POR_HORA - usados),
                "se_libera": str(f.get("primero") or "")}
    except Exception as exc:  # noqa: BLE001
        # Sin bitácora no se puede medir. Se deja pasar y se avisa: bloquear por
        # no poder contar dejaría el canal inservible ante un fallo de la BD.
        log.warning("publicar_walmart: no se pudo medir el presupuesto: %s", exc)
        return {"usados": None, "quedan": None, "se_libera": None}


async def _producto(sku: str) -> dict[str, Any] | None:
    """El producto de Woo con TODO lo que `_item()` necesita — medidas y peso
    incluidos, que el helper genérico del panel no trae."""
    from services import woocommerce
    try:
        async with woocommerce._client() as cli:  # noqa: SLF001
            r = await cli.get("/products", params={
                "sku": sku, "status": "any",
                "_fields": ("id,name,sku,type,parent_id,price,regular_price,"
                            "sale_price,stock_quantity,status,categories,brands,"
                            "images,description,short_description,attributes,"
                            "permalink,weight,dimensions,date_modified"),
            })
            r.raise_for_status()
            data = r.json()
            if not data:
                return None
            p = data[0]
            # ⚠️ EL PRECIO DE LISTA, Y ES OBLIGATORIO PONERLO AQUÍ.
            # `_item()` lee `p["_precio_lista"]` — una marca que pone `ficha()`
            # del publicador por tandas, NO un campo de WooCommerce. Sin ella
            # cae a su valor por omisión y **el feed sale a $1.00 MXN**:
            # medido, JUG-0004-EST se armaba con `price: 1.0` teniendo 269.05
            # en Woo. Walmart no lo rechazaría — publicaría el producto a un
            # peso, que es de los errores más caros que este panel puede cometer.
            p["_precio_lista"] = await _precio_lista(cli, p)
            # UNA VARIACIÓN NO TRAE LO QUE HEREDA. La REST la devuelve sin
            # categorías, sin la marca ni el material del padre y con sus
            # atributos en `option` (singular). Sin esto el feed de cada
            # variante salía clasificado solo por su nombre y con los respaldos
            # generales: color «Multicolor», material «Plástico».
            if p.get("type") == "variation" and p.get("parent_id"):
                try:
                    rp = await cli.get(f"/products/{p['parent_id']}", params={
                        "_cb": "wmpub",
                        "_fields": ("id,name,sku,type,categories,brands,attributes,"
                                    "description,short_description,weight,"
                                    "dimensions")})
                    if rp.status_code == 200:
                        p = fundir_padre(p, rp.json())
                    else:
                        log.warning("publicar_walmart: padre %s de %s → HTTP %s",
                                    p.get("parent_id"), sku, rp.status_code)
                except Exception as exc:  # noqa: BLE001 — sin padre, sale lo de la variante
                    log.warning("publicar_walmart: padre de %s: %s", sku, exc)
            return p
    except Exception as exc:  # noqa: BLE001
        log.warning("publicar_walmart._producto(%s): %s", sku, exc)
        return None


def fundir_padre(v: dict[str, Any], padre: dict[str, Any] | None) -> dict[str, Any]:
    """
    Una variación con lo que hereda de su padre. Pura: no llama a nadie.

      · categorías, marca y descripción corta → las del padre (la variación no
        tiene).
      · descripción → la propia si la tiene; si no, la del padre.
      · peso y medidas → los propios; lo que falte, del padre (así lo resuelve
        WooCommerce al vender).
      · atributos → los del padre que NO son de variación (marca, material…),
        más los PROPIOS de esta variante.

    ⚠️ Un atributo del padre marcado `variation: true` trae TODAS las opciones
    de la familia (Color: Beige, Café, Lila…). Si la variante no fija el suyo,
    NO se toma el primero de la lista: se omite. Tomarlo publicaría todas las
    hermanas como «Beige».
    """
    if not padre:
        return v
    out = dict(v)

    propios: dict[str, Any] = {}
    for a in v.get("attributes") or []:
        nombre = a.get("name")
        valor = a.get("option")
        if valor in (None, "") and a.get("options"):
            valor = a["options"][0]
        if nombre and valor not in (None, ""):
            propios[str(nombre)] = valor
    por_bajo = {k.lower(): k for k in propios}

    atributos: list[dict[str, Any]] = []
    usados: set[str] = set()
    for a in padre.get("attributes") or []:
        nombre = str(a.get("name") or "")
        if not nombre:
            continue
        mio = por_bajo.get(nombre.lower())
        if mio is not None:
            atributos.append({"name": nombre, "options": [propios[mio]]})
            usados.add(mio)
        elif a.get("variation"):
            continue            # de la familia, y esta variante no lo fija
        elif a.get("options"):
            atributos.append({"name": nombre, "options": list(a["options"])})
    for nombre, valor in propios.items():
        if nombre not in usados:
            atributos.append({"name": nombre, "options": [valor]})
    out["attributes"] = atributos

    out["categories"] = padre.get("categories") or v.get("categories") or []
    out["brands"] = padre.get("brands") or v.get("brands") or []
    if not (v.get("short_description") or "").strip():
        out["short_description"] = padre.get("short_description") or ""
    if not (v.get("description") or "").strip():
        out["description"] = padre.get("description") or ""

    def _pos(x: Any) -> bool:
        try:
            return float(x) > 0
        except (TypeError, ValueError):
            return False

    if not _pos(v.get("weight")) and _pos(padre.get("weight")):
        out["weight"] = padre.get("weight")
    dv, dp = dict(v.get("dimensions") or {}), padre.get("dimensions") or {}
    for k in ("length", "width", "height"):
        if not _pos(dv.get(k)) and _pos(dp.get(k)):
            dv[k] = dp.get(k)
    out["dimensions"] = dv
    out["_padre"] = {"id": padre.get("id"), "sku": padre.get("sku"),
                     "name": padre.get("name")}
    return out


async def _precio_lista(cli: Any, p: dict[str, Any]) -> float | None:
    """
    El precio de LISTA, con la misma regla que el publicador por tandas.

    En un producto variable el padre viene con el precio vacío, así que se
    toma el MAYOR de sus variantes: es el que se anuncia y el que no está
    descontado.
    """
    def _f(v: Any) -> float | None:
        try:
            x = float(v)
            return x if x > 0 else None
        except (TypeError, ValueError):
            return None

    precio = _f(p.get("regular_price"))
    if precio is None and p.get("type") == "variable":
        try:
            rv = await cli.get(f"/products/{p['id']}/variations", params={
                "per_page": 100, "_cb": "wmpub",
                "_fields": "id,sku,regular_price,price"})
            if rv.status_code == 200:
                precios = [x for x in (_f(v.get("regular_price")) or _f(v.get("price"))
                                       for v in rv.json()) if x]
                if precios:
                    precio = max(precios)
        except Exception as exc:  # noqa: BLE001
            log.warning("publicar_walmart._precio_lista(%s): %s", p.get("sku"), exc)
    return precio if precio is not None else _f(p.get("price"))


def clasificar(sku: str, nombre: str, categorias_woo: str
               ) -> tuple[str | None, dict | None, str | None]:
    """
    Qué categoría AUTORIZADA le toca. Devuelve (clave, cfg, motivo del no).

    ⚠️ ESTA ES **LA** REGLA, y vive en un solo lugar a propósito. La usan el
    botón de publicar, el semáforo del panel (`walmart_panel.categoria_esquema`)
    y el generador de contenido con IA (`walmart_ia`). Si cada uno clasificara a
    su manera, el semáforo diría verde sobre unos campos, la IA llenaría los de
    otra categoría y el feed saldría con la clave SAT de una tercera.
    """
    import re
    from scripts.publicar_walmart import (CATEGORIAS_AUTORIZADAS,
                                          CATEGORIAS_POR_CONFIRMAR)
    nombre = nombre or ""
    sku = sku or ""
    cats = categorias_woo or ""
    texto = f"{nombre} {cats}"
    familia = sku.split("-")[0].upper()

    for clave, cfg in CATEGORIAS_AUTORIZADAS.items():
        # ⚠️ EL PREFIJO DEL SKU MANDA cuando la categoría lo declara.
        # `candidatos()` (el publicador por tandas) descarta ahí a los de otra
        # familia AUNQUE el texto coincida, porque el prefijo es la taxonomía
        # real de Kubera y el título miente: media electrónica dice
        # "iluminación" sin ser de hogar. Si el botón clasificara solo por
        # texto, mandaría a una categoría justo los productos que la tanda
        # excluye — y el feed saldría con la clave SAT y la lista blanca de
        # otra. Las dos rutas tienen que decidir igual.
        prefijos = cfg.get("prefijos_sku") or ()
        if prefijos:
            if familia in prefijos:
                return clave, cfg, None
            continue
        # Lo que el TÍTULO dice que no es, no entra aunque sus patrones casen:
        # las categorías de Woo vienen de ML y mezclan ("Pañales" agrupa los
        # de perro). Mismo filtro que `candidatos()` aplica por SQL.
        ex = cfg.get("excluir")
        if ex and re.search(ex, nombre, re.I):
            continue
        pc, pt = cfg.get("patron_categoria"), cfg.get("patron_titulo")
        if (pc and re.search(pc, cats, re.I)) or (pt and re.search(pt, texto, re.I)):
            return clave, cfg, None

    # El "no aplica" dice QUÉ falta, no solo que no se puede: las categorías
    # pendientes no tienen patrones (no se puede adivinar en cuál cae), así que
    # se muestran las dos listas y el dato con el que se decide — la categoría
    # de Woo — para que se vea si el trabajo es pedir un ticket o corregir Woo.
    autorizadas = ", ".join(c["clave_visible"] for c in CATEGORIAS_AUTORIZADAS.values())
    pendientes = ", ".join(f"{c['clave_visible']} ({c.get('skus_esperando', 0)} SKUs)"
                           for c in CATEGORIAS_POR_CONFIRMAR.values()
                           if c.get("skus_esperando"))
    return None, None, (
        f"Ninguna categoría con exención aplica a este producto "
        f"(SKU {sku or 's/n'}, categorías en Woo: {cats.strip() or 'ninguna'}). "
        f"Hoy se puede publicar en: {autorizadas}. "
        f"Esperando su ticket en Seller Center: {pendientes or 'ninguna'}.")




def cfg_de_etiqueta(etiqueta: str | None) -> tuple[str | None, dict | None]:
    """(subCategory, cfg) de una categoría AUTORIZADA por su etiqueta en español,
    o (None, None) si esa etiqueta no tiene exención."""
    from scripts.publicar_walmart import CATEGORIAS_AUTORIZADAS
    for clave, cfg in CATEGORIAS_AUTORIZADAS.items():
        if cfg["clave_visible"] == (etiqueta or ""):
            return clave, cfg
    return None, None


_LEER = object()   # centinela: "la elección del panel no se ha leído todavía"


def resolver_categoria(sku: str, nombre: str, categorias_woo: str,
                       elegida: Any = _LEER
                       ) -> tuple[str | None, dict | None, str | None, str]:
    """
    (subCategory, cfg, motivo del no, origen) — LA precedencia con la que se
    decide dónde se publica un artículo en Walmart.

      1. **La elección del PANEL** para ESTE SKU (`channel.product_category`).
         Manda sobre cualquier detector (regla 2 de la casa).
      2. **Las reglas** de `clasificar()` (prefijo / patrones), solo si nadie
         eligió.

    ⚠️ HASTA EL 28-SEP EL PUBLICADOR SE SALTABA EL PASO 1. `walmart_ia` sí leía
    la elección del panel y este módulo clasificaba por su cuenta: una persona
    elegía «Juguetes», la IA generaba atributos para «Juguetes», el feed salía
    como «Cocina» por las reglas — y el candado de `_aplicar_ia` tiraba los
    atributos en silencio. Tres piezas, tres respuestas distintas.

    Si la elegida NO tiene exención, NO se cae a las reglas: la persona dijo
    qué es el producto, y mandarlo a otra categoría por tener permiso ahí sería
    desobedecerla — además de publicar un artículo mal clasificado que Walmart
    acepta sin dar error. Se bloquea y se dice qué ticket falta.

    La del PADRE no cuenta aquí (`ia_variante.aviso_heredada`): heredar sirve
    para generar el borrador, no para decidir dónde se publica una variante.

    `elegida`: la tanda (`scripts/publicar_walmart.candidatos`) decide cientos
    de SKUs de golpe; lee TODAS las elecciones en una consulta y las pasa aquí,
    en vez de una consulta a kubera por SKU. None = "nadie eligió".
    """
    if elegida is _LEER:
        from services import walmart_panel
        elegida = walmart_panel.categoria_elegida(sku) if sku else None
    if elegida:
        clave, cfg = cfg_de_etiqueta(elegida)
        if cfg:
            return clave, cfg, None, "panel"
        return None, None, (
            f"En el panel se eligió «{elegida}» para {sku}, y esa categoría NO "
            f"tiene exención de UPC en Walmart. No se publica en otra: el producto "
            f"quedaría mal clasificado sin que Walmart lo marque. Opciones: pedir "
            f"el ticket de «{elegida}» en Seller Center, o cambiar la categoría "
            f"en el Estudio si la elección fue un error."), "panel"
    clave, cfg, motivo = clasificar(sku, nombre, categorias_woo)
    return clave, cfg, motivo, ("reglas" if cfg else "")

# ═════════════════════════════════════════════════════════════════════════════
# EL CONTENIDO GENERADO CON IA ENTRA AL FEED
# ═════════════════════════════════════════════════════════════════════════════
# Hasta v0.390.0 `walmart_ia` generaba y guardaba en `enrich.channel_content`, y
# el feed se seguía armando SOLO desde Woo. Esto es lo que los une.
#
# Va AQUÍ y no dentro de `_item()` a propósito: `_item()` es del publicador por
# TANDAS, que corre sin que nadie lo mire. La superposición se aplica después,
# sobre lo que ya armó, y solo en el camino del BOTÓN — donde hay una vista
# previa y una persona viendo qué cambia antes de gastar uno de los 10 feeds.
#
# TRES CANDADOS, y los tres existen por un modo de fallo concreto:
#
#   1. LOS ATRIBUTOS SOLO SI LA CATEGORÍA COINCIDE. El bloque `Visible` es
#      distinto en cada categoría. Si el contenido se generó para "Juguetes" y
#      el feed sale como "Electrónicos", esos atributos van al bloque
#      equivocado — y Walmart no da error por eso: publica mal. El texto sí se
#      usa (título y descripción no dependen de la categoría).
#
#   2. LA IA NO PISA LO QUE SALE DE WOO. Medidas, peso, modelo y talla los tiene
#      el catálogo; que un modelo de lenguaje los "mejore" es justo la clase de
#      dato inventado que aquí se publica sin dar error.
#
#   3. SI WOO CAMBIÓ DESPUÉS, SE AVISA. Un contenido de hace tres semanas puede
#      describir un producto que ya no es ese — pasa con los SKUs reciclados.
#      No se bloquea (a veces el cambio en Woo es irrelevante), se dice.
_DE_WOO_NO_SE_TOCA = frozenset({
    "assembledProductLength", "assembledProductWidth", "assembledProductHeight",
    "assembledProductWeight", "countPerPack", "modelNumber", "size",
})

_TITULO_MAX = 200        # `productName.maxLength`
_DESC_MAX = 3900         # tope de 4000 con margen, igual que `_item()`
_BULLETS_MAX = 5
_BULLET_MAX = 50         # "oraciones breves de 50 caracteres" (literal)


def titulo_con_marca(titulo: str, marca: str | None) -> tuple[str, bool]:
    """
    Quita un «Sin marca» (o «Genérico») del arranque del título y pone la marca
    que SÍ viaja en el feed. Devuelve (título, ¿se tocó?).

    El prompt viejo pedía "[Marca] + [Artículo]…" y, si no reconocía la marca,
    "Sin marca". El feed manda `brand` desde Woo (o la de casa), así que salían
    artículos como «Sin marca Set de Limpieza de Juguete…» con marca Ferrahome
    — publicados así en Walmart (JUGU-0264-ROS, JUGU-0201-MUL, JUGU-0200-VER).
    El prompt ya no lo pide; esto arregla el contenido que quedó guardado.
    """
    import re
    t = (titulo or "").strip()
    m = re.match(r"(?i)^(sin\s+marca|marca\s+gen[eé]rica|gen[eé]ric[oa]|unbranded|"
                 r"no\s+brand)\b[\s\-–—:,·|]*", t)
    if not m:
        return t, False
    resto = t[m.end():].strip()
    if not resto:
        return t, False
    marca = (marca or "").strip()
    if marca and marca.lower() not in ("sin marca", "genérico", "generico"):
        if not resto.lower().startswith(marca.lower()):
            resto = f"{marca} {resto}"
    return resto, True


def _corto(v: Any, n: int = 90) -> str:
    """Un valor cualquiera, legible en una tabla de comparación."""
    if isinstance(v, (list, dict)):
        t = json.dumps(v, ensure_ascii=False)
    else:
        t = str(v if v is not None else "")
    return t if len(t) <= n else t[:n - 1] + "…"


def _aplicar_ia(item: dict[str, Any], doc: dict[str, Any] | None, categoria: str,
                cfg: dict[str, Any], modificado_woo: str | None
                ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """
    Superpone el contenido de la IA sobre el feed ya armado.

    Devuelve (item, comparativa, avisos). La comparativa es lo que ve la persona
    antes de mandar: campo, lo que iría de Woo, lo que iría de la IA.
    """
    comparativa: list[dict[str, Any]] = []
    avisos: list[str] = []
    if not doc or not (doc.get("contenido") or {}):
        return item, comparativa, ["Sin contenido generado con IA: el feed sale "
                                   "tal cual está en WooCommerce."]

    c = doc["contenido"]
    orderable = item.get("Orderable") or {}
    clave = cfg["clave_visible"]
    visible = (item.get("Visible") or {}).get(clave) or {}

    # ── candado 3: ¿el contenido es de antes del último cambio en Woo? ───────
    gen = doc.get("updated_at") or ""
    if gen and modificado_woo and str(modificado_woo) > str(gen)[:19]:
        avisos.append(f"⚠ El contenido se generó el {str(gen)[:16]} y Woo cambió "
                      f"el {str(modificado_woo)[:16]}. Puede describir otra cosa "
                      f"— vuelve a generarlo si el cambio fue de fondo.")

    def anota(campo: str, woo: Any, ia: Any, usado: bool, nota: str = "") -> None:
        comparativa.append({"campo": campo, "de_woo": _corto(woo),
                            "de_ia": _corto(ia), "usado": usado, "nota": nota})

    # ── TEXTO: no depende de la categoría, siempre se puede usar ─────────────
    titulo = (c.get("titulo") or "").strip()
    if titulo:
        titulo, sin_marca = titulo_con_marca(titulo, orderable.get("brand"))
        anota("productName", orderable.get("productName"), titulo, True,
              f"se quitó «Sin marca»: la marca del feed es "
              f"{orderable.get('brand')}" if sin_marca else "")
        orderable["productName"] = titulo[:_TITULO_MAX]

    desc = (c.get("descripcion") or "").strip()
    if desc:
        anota("shortDescription", orderable.get("shortDescription"), desc, True)
        orderable["shortDescription"] = desc[:_DESC_MAX]

    # `keyFeatures` es donde más se gana: lo de Woo era el nombre del producto
    # repetido más "Material: X". Estas son viñetas de beneficio, de 50
    # caracteres, que es lo que Walmart pide literalmente.
    bullets = [str(b).strip() for b in (c.get("bullets") or []) if str(b).strip()]
    if bullets:
        largas = [b for b in bullets if len(b) > _BULLET_MAX]
        buenas = [b[:_BULLET_MAX] for b in bullets][:_BULLETS_MAX]
        anota("keyFeatures", orderable.get("keyFeatures"), buenas, True,
              f"{len(largas)} recortada(s) a {_BULLET_MAX}" if largas else "")
        orderable["keyFeatures"] = buenas

    # La MARCA no se toca: `_item()` la saca del atributo BRAND de Woo, y la IA
    # contesta "Sin marca" cuando no la reconoce. Cambiar identidad por un "no
    # sé" sería empeorar el dato, no mejorarlo.
    if c.get("marca"):
        anota("brand", orderable.get("brand"), c["marca"], False,
              "la marca no se sustituye: se respeta la de Woo")

    # ── ATRIBUTOS: candados 1 y 2 ───────────────────────────────────────────
    atributos = c.get("atributos") or {}
    if isinstance(atributos, list):
        # El Estudio guarda los atributos como [{nombre, valor}] — es la forma
        # de los OTROS canales. Si alguien edita y guarda desde ahí, aquí
        # llegaría una lista y el `.items()` de abajo reventaría a media vista
        # previa con un error que no explica nada. Se acepta la forma ajena en
        # vez de exigir la propia: lo caro no es convertir, es que el botón
        # falle sin decir por qué.
        pares = {}
        for a in atributos:
            if not isinstance(a, dict):
                continue
            k = a.get("nombre") or a.get("name") or a.get("campo")
            if k:
                pares[k] = a.get("valor") if "valor" in a else a.get("value")
        atributos = pares
    if atributos:
        otra = doc.get("categoria") or "otra categoría"
        if (doc.get("categoria") or "") != categoria:
            avisos.append(
                f"Los atributos generados son de «{otra}» y este feed sale como "
                f"«{categoria}»: NO se usan (irían a un bloque que no es el suyo, "
                f"y Walmart publicaría mal sin dar error). El texto sí se "
                f"aprovecha.")
        else:
            blanca = cfg.get("campos_visible")
            for campo, valor in atributos.items():
                if campo in _DE_WOO_NO_SE_TOCA:
                    anota(campo, visible.get(campo), valor, False,
                          "lo pone el catálogo, no la IA")
                    continue
                if blanca and campo not in blanca:
                    anota(campo, visible.get(campo), valor, False,
                          "fuera de la lista blanca de la categoría")
                    continue
                anota(campo, visible.get(campo), valor, True)
                visible[campo] = valor

    item["Orderable"] = orderable
    if item.get("Visible"):
        item["Visible"][clave] = visible
    return item, comparativa, avisos


def _texto_woo(p: dict[str, Any] | None) -> tuple[str, str, str]:
    """(nombre, categorías de Woo, descripción corta sin HTML) de un producto."""
    import re
    p = p or {}
    desc = re.sub(r"<[^>]+>", " ", p.get("short_description") or p.get("description") or "")
    return (p.get("name") or "",
            " ".join(c.get("name") or "" for c in (p.get("categories") or [])),
            re.sub(r"\s+", " ", desc).strip())


async def categoria_de_sku(sku: str) -> dict[str, Any]:
    """
    Lo que el botón de publicar decidiría HOY para este SKU, y de dónde sale.

    Lee el producto con `_producto()` —la MISMA lectura que usa `_armar`— para
    que el selector del Estudio y el semáforo no puedan contestar distinto que
    el feed: las reglas miran el nombre y TODAS las categorías de Woo, y un
    helper que trajera solo la primera categoría clasificaría diferente.
    """
    import asyncio
    from services import walmart_panel
    p = await _producto(sku)
    nombre, cats, _desc = _texto_woo(p)
    r = await asyncio.to_thread(walmart_panel.categoria_actual, sku, nombre, cats)
    return {**r, "sku": sku, "existe_en_woo": bool(p)}


async def sugerir_categoria_de_sku(sku: str, titulo: str = "") -> dict[str, Any]:
    """
    La categoría SUGERIDA por la IA para este SKU, armada con los mismos datos
    que lee `_armar`: el título (o el que trae el Estudio, si la persona lo
    acaba de mejorar), TODAS las categorías de Woo y la descripción corta.
    Sugerencia: no se guarda sola (ver `walmart_panel.sugerir_categoria`).
    """
    from services import walmart_panel
    p = await _producto(sku)
    nombre, cats, desc = _texto_woo(p)
    return await walmart_panel.sugerir_categoria(sku, titulo or nombre, cats, desc)


def _categoria_cfg(p: dict[str, Any]
                   ) -> tuple[str | None, dict | None, str | None, str]:
    """`resolver_categoria()` con lo que trae un producto de WooCommerce."""
    return resolver_categoria(
        p.get("sku") or "", p.get("name") or "",
        " ".join(c.get("name") or "" for c in (p.get("categories") or [])))


async def _armar(req: dict[str, Any]) -> dict[str, Any]:
    """El payload del feed y sus avisos. NO manda nada."""
    import asyncio
    from scripts.publicar_walmart import _item, _sobre

    sku = str(req.get("sku") or "").strip()
    if not sku:
        raise RuntimeError("Falta el SKU.")
    p = await _producto(sku)
    if not p:
        raise RuntimeError(f"El SKU {sku} no existe en WooCommerce.")

    # EL PADRE NUNCA SE PUBLICA (Brandon, 11-sep-2026). El dispatcher de
    # `publicar.py` ya lo frena por `wp_db`; esto es la segunda red para cuando
    # la BD de WordPress no contesta: la REST dice el tipo por sí sola.
    if p.get("type") == "variable":
        from services.publicar_ready import MSG_PADRE
        raise RuntimeError(MSG_PADRE)

    clave, cfg, motivo, origen = await asyncio.to_thread(_categoria_cfg, p)
    if not cfg:
        raise RuntimeError(motivo or "Sin categoría autorizada.")

    imgs = [i.get("src") for i in (p.get("images") or []) if i.get("src")]
    # UNA VARIACIÓN, CON LA MISMA REGLA QUE LOS DEMÁS CANALES. Walmart no pasa
    # por `construir_prod`: lee la REST, que para una variación da su `image`
    # (o la del padre si no tiene) y nada de la galería del padre filtrada. Se
    # arma con `imagenes_variante.para_publicar` —galería propia → solo lo
    # suyo; si no, su principal + lo del padre que no es de una hermana— para
    # que el feed lleve lo mismo que ML y Amazon. Sin BD de WordPress queda lo
    # de la REST, que es lo de siempre.
    if p.get("type") == "variation" and p.get("id"):
        try:
            from services import imagenes_variante, wp_db
            if await asyncio.to_thread(wp_db.disponible):  # regla 11
                r_img = await asyncio.to_thread(
                    imagenes_variante.para_publicar, int(p["id"]))
                if r_img is not None:
                    imgs = r_img[0]
        except Exception as exc:  # noqa: BLE001
            log.warning("publicar_walmart: imágenes de variante %s: %s — se usan "
                        "las de la REST", sku, exc)
    if not imgs:
        raise RuntimeError("Sin imágenes: Walmart exige al menos la principal.")
    # LAS FOTOS VAN POR UN HOST QUE WALMART SÍ PUEDE DESCARGAR. Hasta el 7-oct
    # aquí se mandaba la URL de la galería tal cual, y desde el 17-sep Walmart
    # rechazaba TODAS las de chunche.shop: 38 altas de 38. `preparar` las deja
    # en JPEG ≥ 1000 px en una URL pública y comprueba cada una ANTES de gastar
    # el envío (ver services/imagenes_walmart.py).
    from services import imagenes_walmart
    fotos = await imagenes_walmart.preparar(imgs, sku)
    if not fotos["ok"]:
        raise RuntimeError(fotos["motivo"])
    imgs = fotos["urls"]
    # El candado del precio. `_item()` cae a 1.0 cuando no lo encuentra, y un
    # producto publicado a un peso no da error: se vende.
    if not p.get("_precio_lista"):
        raise RuntimeError(
            f"Sin precio de lista en WooCommerce para {sku}. NO se arma el feed: "
            f"sin él el artículo saldría publicado a $1.00 y Walmart lo aceptaría.")

    notas: dict[str, Any] = {}
    item = await asyncio.to_thread(_item, p, imgs, clave, cfg, notas)

    # EL CONTENIDO DE LA IA, superpuesto sobre lo que ya armo el publicador.
    # `usar_ia: false` en la peticion lo desactiva sin tocar nada mas.
    comparativa: list[dict[str, Any]] = []
    avisos_ia: list[str] = []
    if req.get("usar_ia", True):
        from services import channel_content
        # ⚠️ CUENTA VACÍA, NO `CUENTA`. La PK de `enrich.channel_content` es
        # (sku, canal, cuenta) y `walmart_ia` guarda con cuenta "" — igual que
        # Temu y TikTok. Leer con "WALMART" (que es la cuenta de
        # `ops.channel_submissions`, otra tabla) devolvía None SIEMPRE: el panel
        # decía "sin contenido generado" con el contenido recién guardado, y el
        # feed salía con el texto de Woo mientras todos creían lo contrario.
        doc = await channel_content.leer(sku, CANAL, "")
        item, comparativa, avisos_ia = await asyncio.to_thread(
            _aplicar_ia, item, doc, cfg["clave_visible"], cfg,
            p.get("date_modified"))

    payload = await asyncio.to_thread(_sobre, clave, [item])

    # Lo que la persona TIENE que ver va primero: fotos y datos dudosos.
    avisos: list[str] = (list(fotos["avisos"])
                         + avisos_de_datos(p, item, cfg, notas)
                         + list(avisos_ia))
    pres = await asyncio.to_thread(_presupuesto)
    if pres.get("quedan") is not None:
        avisos.append(f"Presupuesto: quedan {pres['quedan']} de {FEEDS_POR_HORA} "
                      f"feeds en la hora vigente.")
    dims = p.get("dimensions") or {}
    if not any((dims.get("length"), dims.get("width"), dims.get("height"))):
        avisos.append("Sin medidas en Woo: Walmart cobra volumétrico y el flete "
                      "saldría de un valor por omisión.")
    de_donde = ("elegida en el panel" if origen == "panel"
                else "por reglas: nadie la eligió en el panel — revísala en el "
                     "selector del Estudio")
    item0 = (payload.get("MPItem") or [{}])[0]
    sat = (item0.get("Orderable") or {}).get("ProductTaxCode") or cfg.get("clave_sat")
    avisos.append(f"Categoría «{cfg['clave_visible']}» ({de_donde}) · exención "
                  f"{cfg.get('folio_exencion') or 'sin folio'} · SAT {sat}.")
    # UN TICKET NO ES UN FEED. El ticket dice que la puerta está abierta; no
    # dice qué campos exige PRODUCCIÓN (3.11), que ya contradijo al esquema en
    # cuatro categorías. El primer artículo de cada una es un piloto.
    if cfg.get("prueba") == "ticket":
        avisos.append(f"PILOTO: «{cfg['clave_visible']}» está autorizada por "
                      f"escrito pero ningún feed la ha confirmado todavía. Manda "
                      f"este artículo solo, revisa su veredicto y hasta entonces "
                      f"no mandes más de esta categoría.")
    return {"payload": payload, "clave": clave, "cfg": cfg, "origen": origen,
            "avisos": avisos, "presupuesto": pres, "imagenes": len(imgs),
            "fotos_modo": fotos["modo"],
            "comparativa": comparativa,
            "con_ia": bool([c for c in comparativa if c.get("usado")])}


_NOMBRE_RESPALDO = {
    "material": "material", "colorCategory": "color", "activity": "actividad",
    "peso": "peso", "medidas": "medidas",
}


def avisos_de_datos(p: dict[str, Any], item: dict[str, Any], cfg: dict[str, Any],
                    notas: dict[str, Any] | None) -> list[str]:
    """
    Los datos que van a salir publicados y que nadie confirmó. Pura.

    Walmart NO rechaza un dato inventado: lo publica. Por eso lo que salió de
    un respaldo (porque Woo no lo tiene y la IA no lo llenó) y lo que no
    cuadra se dice ANTES de mandar, en vez de descubrirlo en la ficha pública.
    """
    out: list[str] = []
    orderable = item.get("Orderable") or {}
    visible = (item.get("Visible") or {}).get(cfg["clave_visible"]) or {}

    # ── respaldos que sobrevivieron (la IA pudo haberlos corregido) ──────────
    vivos: list[str] = []
    for campo, valor in ((notas or {}).get("respaldos") or {}).items():
        if campo in ("peso", "medidas"):
            vivos.append(f"{_NOMBRE_RESPALDO[campo]} {valor}")
            continue
        actual = visible.get(campo)
        if isinstance(actual, list):
            actual = actual[0] if actual else None
        if actual == valor:
            vivos.append(f"{_NOMBRE_RESPALDO.get(campo, campo)} «{valor}»")
    if vivos:
        out.append("DATOS POR OMISIÓN (WooCommerce no los tiene y nadie los "
                   "llenó): " + ", ".join(vivos) + ". Walmart los publica tal "
                   "cual. Corrígelos en el producto o genera el contenido con IA.")

    # ── peso que no cuadra con lo que cuesta ─────────────────────────────────
    try:
        peso = float((orderable.get("ShippingWeight") or {}).get("measure") or 0)
        precio = float(orderable.get("price") or 0)
    except (TypeError, ValueError):
        peso = precio = 0.0
    if peso >= 10 and precio > 0 and precio / peso < 25:
        d = [float((orderable.get(k) or {}).get("measure") or 0) for k in
             ("ShippingDimensionsDepth", "ShippingDimensionsWidth",
              "ShippingDimensionsHeight")]
        out.append(
            f"⚠ PESO DUDOSO: WooCommerce dice {peso:g} kg ({d[0]:g}×{d[1]:g}×{d[2]:g} cm) "
            f"para un artículo de ${precio:,.2f} — parece el peso de la CAJA "
            f"del proveedor, no el de la pieza. Sale publicado así en la ficha y "
            f"Walmart calcula el flete con él. Corrige peso y medidas en "
            f"WooCommerce antes de mandar.")
    return out


# ═════════════════════════════════════════════════════════════════════════════
# EL ESTADO DE LOS ENVÍOS — el veredicto vuelve a la bitácora
# ═════════════════════════════════════════════════════════════════════════════
# `confirmar` escribe 'ENVIADO' y hasta el 7-oct nadie volvía por el resultado:
# 143 filas se quedaron así, el panel no sabía si un SKU seguía en proceso y la
# única forma de ver por qué rebotó era entrar a Seller Center. Ahora, cada vez
# que alguien abre la vista previa o manda un SKU, se le pregunta a Walmart por
# sus envíos sin veredicto y la respuesta se escribe encima (mismo formato que
# la bitácora de la tanda: `scripts/publicar_walmart.py::_Bitacora.veredicto`).
# Sin job ni cron: se pregunta cuando alguien lo necesita.

_TRADUCE_ERROR = (
    ("0101312", "Walmart no pudo descargar la foto"),
    ("0101149", "la foto principal no se pudo dar de alta"),
    ("56026862530206", "estaba en revisión por un envío anterior"),
    ("55506974520167", "falta la foto adicional"),
    ("72600149546850", "falta un atributo obligatorio"),
)


def _resumen_errores(errores: list[dict[str, Any]]) -> str:
    """
    Los errores de Walmart en una línea. Lo conocido va EN ESPAÑOL y corto (con
    su código, para buscarlo); lo desconocido, tal cual lo dijo Walmart — ahí
    el texto original es la única pista y traducirlo a ojo la borraría.
    """
    partes: list[str] = []
    for e in errores or []:
        codigo = str(e.get("codigo") or "")
        campo = str(e.get("campo") or "")
        msg = str(e.get("mensaje") or "").strip()
        pista = next((t for c, t in _TRADUCE_ERROR if c in codigo), "")
        if "0101149" in codigo and any("0101312" in str(x.get("codigo") or "")
                                       for x in errores):
            continue        # es la consecuencia del 0101312, no otro problema
        if pista:
            # El atributo que falta ES el dato: "falta un atributo obligatorio"
            # sin decir cuál manda a la persona a Seller Center.
            detalle = f": {campo}" if "72600149546850" in codigo and campo else ""
            partes.append(f"{pista}{detalle} ({codigo})")
        else:
            partes.append((f"[{campo}] " if campo else "") + msg[:180]
                          + (f" ({codigo})" if codigo else ""))
    return " · ".join(partes)[:500]


def _envios_recientes(sku: str) -> list[dict[str, Any]]:
    from services import supabase_db as sdb
    return sdb.fetch_all(
        """select id, submission_id, status, success, error_resumen,
                  submitted_at, actor,
                  extract(epoch from (now() - submitted_at)) / 60 as hace_min
             from ops.channel_submissions
            where canal = %s and sku = %s::citext
              and submission_id is not null and submission_id <> ''
              and submitted_at > now() - make_interval(hours => %s)
            order by submitted_at desc
            limit 6""", (CANAL, sku, VENTANA_ENVIOS_H))


def _anotar_veredicto(feed_id: str, sku: str, estado: str, resumen: str) -> int:
    from services import supabase_db as sdb
    gano = estado == "SUCCESS"
    return sdb.execute(
        """update ops.channel_submissions
              set status = %s, success = %s, error_resumen = %s,
                  published_at = case when %s then now() else published_at end
            where canal = %s and submission_id = %s and sku = %s::citext
              and status = 'ENVIADO'""",
        (estado, gano, resumen or None, gano, CANAL, feed_id, sku))


def veredicto_de(feed: dict[str, Any], sku: str) -> tuple[str, list[dict[str, Any]]]:
    """
    (estado, errores) de UN SKU dentro de la respuesta de `walmart.feed_estado`.
    Pura. `estado` es el de Walmart (SUCCESS, DATA_ERROR, SYSTEM_ERROR,
    TIMEOUT_ERROR), o 'EN_PROCESO' si todavía no lo juzga, o 'DESCONOCIDO' si
    la consulta falló.
    """
    if not feed or not feed.get("ok"):
        return "DESCONOCIDO", []
    bajo = (sku or "").strip().lower()
    for a in feed.get("articulos") or []:
        if str(a.get("sku") or "").strip().lower() == bajo:
            est = str(a.get("estado") or "").upper()
            if est in ("", "INPROGRESS", "RECEIVED"):
                return "EN_PROCESO", []
            return est, list(a.get("errores") or [])
    # El feed existe pero aún no lista el artículo: lo está recibiendo.
    if str(feed.get("estado") or "").upper() in ("PROCESSED", "ERROR"):
        return "DESCONOCIDO", []
    return "EN_PROCESO", []


def decidir_reenvio(envios: list[dict[str, Any]]) -> dict[str, Any]:
    """
    ¿Se puede mandar otra vez este SKU? Pura: recibe los envíos recientes YA
    resueltos (`estado`, `hace_min`, `feed_id`, `resumen`), del más nuevo al
    más viejo. Devuelve `{bloquea, motivo, ultimo}`.
    """
    ultimo = envios[0] if envios else None
    for e in envios:
        hace = float(e.get("hace_min") or 0)
        cuando = (f"hace {hace:.0f} min" if hace < 120 else f"hace {hace / 60:.0f} h")
        if e["estado"] == "EN_PROCESO":
            return {"bloquea": True, "ultimo": ultimo, "motivo": (
                f"Walmart todavía está procesando el envío anterior de este SKU "
                f"(feed {e['feed_id']}, {cuando}). Reenviarlo ahora lo rechaza "
                f"—«under compliance review», SOURCE_DP_PENDING en Seller Center— "
                f"y puede alargar la revisión hasta 48 h. Espera el veredicto y "
                f"vuelve a abrir la vista previa: aquí mismo va a salir.")}
        if e["estado"] == "SUCCESS" and hace < REVISION_TRAS_EXITO_H * 60:
            return {"bloquea": True, "ultimo": ultimo, "motivo": (
                f"Walmart ACEPTÓ este SKU {cuando} (feed {e['feed_id']}) y lo "
                f"tiene en revisión de cumplimiento, que tarda hasta "
                f"{REVISION_TRAS_EXITO_H} h. Reenviarlo en ese lapso lo rechaza "
                f"(le pasó a ELEC-0146-PC6-NAR el 12-sep). No hace falta mandarlo "
                f"otra vez: ya entró.")}
        if e["estado"] == "DESCONOCIDO" and hace < GRACIA_SIN_VEREDICTO_MIN:
            return {"bloquea": True, "ultimo": ultimo, "motivo": (
                f"Este SKU se mandó {cuando} (feed {e['feed_id']}) y no se pudo "
                f"consultar su veredicto en Walmart. Por si sigue en proceso, "
                f"espera {GRACIA_SIN_VEREDICTO_MIN} min antes de reenviarlo.")}
    return {"bloquea": False, "ultimo": ultimo, "motivo": None}


async def estado_envios(sku: str) -> dict[str, Any]:
    """
    Los envíos recientes de un SKU con su veredicto al día, y si se puede
    volver a mandar. Lo que estaba 'ENVIADO' se le pregunta a Walmart y se
    escribe encima.
    """
    import asyncio
    from services import walmart
    try:
        filas = await asyncio.to_thread(_envios_recientes, sku)
    except Exception as exc:  # noqa: BLE001
        # Sin bitácora no se puede saber. Igual que el presupuesto: se deja
        # pasar y se avisa, en vez de dejar el canal inservible.
        log.warning("publicar_walmart.estado_envios(%s): %s", sku, exc)
        return {"bloquea": False, "motivo": None, "ultimo": None, "envios": [],
                "sin_bitacora": True}

    envios: list[dict[str, Any]] = []
    consultados = 0
    for f in filas:
        feed_id = f["submission_id"]
        e = {"feed_id": feed_id, "hace_min": float(f.get("hace_min") or 0),
             "estado": str(f.get("status") or "").upper(),
             "resumen": f.get("error_resumen") or "", "actor": f.get("actor")}
        if e["estado"] == "ENVIADO":
            # Tope de 3 consultas por vista: los reenvíos en ráfaga existen
            # (ORG-1078-BLN, JUGU-0049-MUL) pero no más de dos o tres.
            if consultados >= 3:
                e["estado"] = "DESCONOCIDO"
            else:
                consultados += 1
                try:
                    feed = await walmart.feed_estado(feed_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("publicar_walmart: feed %s: %s", feed_id, exc)
                    feed = {"ok": False}
                estado, errores = veredicto_de(feed, sku)
                e["estado"] = estado
                if estado not in ("EN_PROCESO", "DESCONOCIDO"):
                    e["resumen"] = _resumen_errores(errores)
                    try:
                        await asyncio.to_thread(_anotar_veredicto, feed_id, sku,
                                                estado, e["resumen"])
                    except Exception as exc:  # noqa: BLE001
                        log.warning("publicar_walmart: veredicto de %s sin "
                                    "anotar: %s", feed_id, exc)
        envios.append(e)
    return {**decidir_reenvio(envios), "envios": envios}


def aviso_ultimo_envio(estado: dict[str, Any]) -> str | None:
    """Una línea para la vista previa: qué pasó la última vez. Pura."""
    u = estado.get("ultimo")
    if not u:
        return None
    hace = float(u.get("hace_min") or 0)
    cuando = f"hace {hace:.0f} min" if hace < 120 else f"hace {hace / 60:.0f} h"
    if u["estado"] == "SUCCESS":
        return f"Último envío ({cuando}): ACEPTADO por Walmart."
    if u["estado"] == "EN_PROCESO":
        return f"Último envío ({cuando}): Walmart todavía lo está procesando."
    if u["estado"] == "DESCONOCIDO":
        return f"Último envío ({cuando}): no se pudo consultar su veredicto."
    return (f"Último envío ({cuando}): RECHAZADO — {u.get('resumen') or u['estado']}"
            )[:520]


async def reconciliar_veredictos(limite: int = 30) -> dict[str, Any]:
    """
    Le pregunta a Walmart por los feeds que siguen 'ENVIADO' y escribe el
    veredicto de cada artículo. Para vaciar el atraso (143 filas al 7-oct) y
    para que Monitoreo deje de contar como "sin confirmar" lo ya juzgado.
    """
    import asyncio
    from services import supabase_db as sdb, walmart

    def _pendientes() -> list[dict[str, Any]]:
        return sdb.fetch_all(
            """select submission_id, max(submitted_at) as enviado,
                      array_agg(distinct sku::text) as skus
                 from ops.channel_submissions
                where canal = %s and status = 'ENVIADO'
                  and submission_id is not null and submission_id <> ''
                group by submission_id
                order by max(submitted_at) desc
                limit %s""", (CANAL, max(1, min(int(limite), 100))))

    feeds = await asyncio.to_thread(_pendientes)
    out = {"feeds": len(feeds), "resueltos": 0, "en_proceso": 0, "sin_dato": 0,
           "por_estado": {}, "detalle": []}
    for f in feeds:
        feed_id = f["submission_id"]
        try:
            feed = await walmart.feed_estado(feed_id)
        except Exception as exc:  # noqa: BLE001
            feed = {"ok": False, "motivo": str(exc)}
        for sku in f.get("skus") or []:
            estado, errores = veredicto_de(feed, sku)
            if estado == "EN_PROCESO":
                out["en_proceso"] += 1
            elif estado == "DESCONOCIDO":
                out["sin_dato"] += 1
            else:
                resumen = _resumen_errores(errores)
                await asyncio.to_thread(_anotar_veredicto, feed_id, sku, estado,
                                        resumen)
                out["resueltos"] += 1
                out["por_estado"][estado] = out["por_estado"].get(estado, 0) + 1
            out["detalle"].append({"sku": sku, "feed_id": feed_id, "estado": estado})
    return out


async def preview(req: dict[str, Any]) -> dict[str, Any]:
    """El feed REAL antes de mandarlo, sin tocar Walmart."""
    from services import walmart
    sku = str(req.get("sku") or "").strip()
    if not walmart.disponible():
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": "Walmart no está configurado (faltan WM_CLIENT_ID / "
                          "WM_CLIENT_SECRET)."}
    import asyncio
    # El armado (Woo + fotos) y el estado de los envíos (bitácora + Walmart) no
    # se necesitan uno al otro: a la vez, la vista previa tarda lo del más
    # lento y no la suma de los dos.
    armado, envios = await asyncio.gather(_armar(req), estado_envios(sku),
                                          return_exceptions=True)
    if isinstance(envios, BaseException):
        log.warning("publicar_walmart.preview(%s): estado de envíos: %s", sku, envios)
        envios = {"bloquea": False, "motivo": None, "ultimo": None, "envios": []}
    # Qué pasó la última vez, AQUÍ, sin ir a Seller Center — también cuando el
    # feed de hoy no se puede armar: el rechazo anterior suele explicar por qué.
    avisos_envio = [a for a in (aviso_ultimo_envio(envios),) if a]
    if isinstance(armado, BaseException):
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(armado),
                "ultimo_envio": envios.get("ultimo"), "avisos": avisos_envio}

    if envios.get("bloquea"):
        avisos_envio.insert(0, "⛔ NO SE PUEDE MANDAR TODAVÍA: " + envios["motivo"])

    items = armado["payload"].get("MPItem") or [{}]
    item = items[0]
    return {
        "ok": True, "canal": CANAL, "sku": sku,
        "bloqueado": bool(envios.get("bloquea")),
        "ultimo_envio": envios.get("ultimo"),
        "categoria": armado["cfg"]["clave_visible"],
        "categoria_origen": armado.get("origen"),
        "categoria_prueba": armado["cfg"].get("prueba"),
        "product_type": armado["clave"],
        "titulo": (item.get("Orderable") or {}).get("productName")
                  or (item.get("Visible") or {}).get("productName"),
        "payload": armado["payload"],
        "presupuesto": armado["presupuesto"],
        # Lo que ve la persona antes de gastar un feed: campo por campo, que
        # iria de Woo y que iria de la IA. Sin esto, "usa el contenido de IA"
        # seria un acto de fe.
        "comparativa": armado["comparativa"],
        "con_ia": armado["con_ia"],
        "avisos": avisos_envio + armado["avisos"],
    }


async def confirmar(req: dict[str, Any]) -> dict[str, Any]:
    """Manda UN feed con este producto. Devuelve el feedId, no un veredicto."""
    import asyncio

    import httpx

    from services import supabase_db as sdb, walmart

    sku = str(req.get("sku") or "").strip()
    if not walmart.disponible():
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": "Walmart no está configurado."}
    armado, envios = await asyncio.gather(_armar(req), estado_envios(sku),
                                          return_exceptions=True)
    if isinstance(envios, BaseException):
        # Igual que el presupuesto: no poder consultar no inutiliza el canal.
        log.warning("publicar_walmart.confirmar(%s): estado de envíos: %s",
                    sku, envios)
        envios = {"bloquea": False, "motivo": None, "ultimo": None}

    # ── EL CANDADO CONTRA REENVÍOS ──────────────────────────────────────────
    # Antes que todo lo demás: si el SKU sigue vivo en Walmart, mandar otro
    # feed no solo se pierde — alarga la revisión del primero.
    if envios.get("bloquea"):
        return {"ok": False, "canal": CANAL, "sku": sku,
                "ultimo_envio": envios.get("ultimo"), "motivo": envios["motivo"]}
    if isinstance(armado, BaseException):
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(armado)}

    # ── EL CANDADO DEL PRESUPUESTO ──────────────────────────────────────────
    pres = armado["presupuesto"]
    if pres.get("quedan") == 0:
        return {"ok": False, "canal": CANAL, "sku": sku, "presupuesto": pres,
                "motivo": (f"Sin cuota: ya se mandaron {FEEDS_POR_HORA} feeds en la "
                           f"última hora. Mandar otro devuelve "
                           f"REQUEST_THRESHOLD_VIOLATED y el intento se pierde. "
                           f"Se libera a partir de {pres.get('se_libera')}.")}

    crudo = json.dumps(armado["payload"], ensure_ascii=False).encode("utf-8")
    log.info("WALMART feed %s · categoría %s · %d bytes", sku,
             armado["cfg"]["clave_visible"], len(crudo))

    try:
        tk = await walmart.token()
        async with httpx.AsyncClient(timeout=300.0) as cli:
            r = await cli.post(f"{walmart.HOST}/v3/feeds",
                               params={"feedType": "MP_ITEM_INTL"},
                               headers=walmart._cabeceras(tk),  # noqa: SLF001
                               files={"file": ("lote.json", crudo,
                                               "application/json")})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": f"No se pudo mandar el feed: {exc}"}
    if r.status_code != 200:
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": f"Walmart rechazó el envío (HTTP {r.status_code}): "
                          f"{r.text[:200]}"}

    feed_id = r.json().get("feedId", "")

    def _anotar() -> None:
        sdb.execute(
            """insert into ops.channel_submissions
                 (canal, cuenta, sku, submission_id, operacion, status,
                  submitted_at, created_at)
               values (%s, %s, %s::citext, %s, 'alta', 'ENVIADO', now(), now())""",
            (CANAL, CUENTA, sku, feed_id))

    try:
        await asyncio.to_thread(_anotar)
    except Exception as exc:  # noqa: BLE001
        # La bitácora es lo que mide el presupuesto: si falla, el siguiente
        # cálculo saldrá bajo. Se avisa en vez de callarlo.
        log.warning("WALMART: feed %s enviado pero NO anotado: %s", feed_id, exc)

    return {"ok": True, "canal": CANAL, "sku": sku,
            "feed_id": feed_id, "item_id": feed_id,
            "categoria": armado["cfg"]["clave_visible"],
            # `confirmar` arma con el MISMO `_armar` que la vista previa, asi
            # que lo enviado es lo que se enseñó. Se repite aqui para que quede
            # en la respuesta del envio, no solo en la del preview.
            "con_ia": armado["con_ia"],
            "comparativa": armado["comparativa"],
            # NO se dice "publicado": Walmart solo acusó recibo del feed.
            "estado": "ENVIADO",
            "avisos": armado["avisos"] + [
                "Feed enviado. Walmart NO confirma publicación aquí: el veredicto "
                "llega en minutos y se consulta por SKU."]}
