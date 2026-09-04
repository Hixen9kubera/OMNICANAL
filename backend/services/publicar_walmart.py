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
            return data[0] if data else None
    except Exception as exc:  # noqa: BLE001
        log.warning("publicar_walmart._producto(%s): %s", sku, exc)
        return None


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
        anota("productName", orderable.get("productName"), titulo, True)
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


def _categoria_cfg(p: dict[str, Any]) -> tuple[str | None, dict | None, str | None]:
    """`clasificar()` con lo que trae un producto de WooCommerce."""
    return clasificar(
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

    clave, cfg, motivo = await asyncio.to_thread(_categoria_cfg, p)
    if not cfg:
        raise RuntimeError(motivo or "Sin categoría autorizada.")

    imgs = [i.get("src") for i in (p.get("images") or []) if i.get("src")]
    if not imgs:
        raise RuntimeError("Sin imágenes: Walmart exige al menos la principal.")

    item = await asyncio.to_thread(_item, p, imgs, clave, cfg)

    # EL CONTENIDO DE LA IA, superpuesto sobre lo que ya armo el publicador.
    # `usar_ia: false` en la peticion lo desactiva sin tocar nada mas.
    comparativa: list[dict[str, Any]] = []
    avisos_ia: list[str] = []
    if req.get("usar_ia", True):
        from services import channel_content
        doc = await channel_content.leer(sku, CANAL, CUENTA)
        item, comparativa, avisos_ia = await asyncio.to_thread(
            _aplicar_ia, item, doc, cfg["clave_visible"], cfg,
            p.get("date_modified"))

    payload = await asyncio.to_thread(_sobre, clave, [item])

    avisos: list[str] = list(avisos_ia)
    pres = await asyncio.to_thread(_presupuesto)
    if pres.get("quedan") is not None:
        avisos.append(f"Presupuesto: quedan {pres['quedan']} de {FEEDS_POR_HORA} "
                      f"feeds en la hora vigente.")
    dims = p.get("dimensions") or {}
    if not any((dims.get("length"), dims.get("width"), dims.get("height"))):
        avisos.append("Sin medidas en Woo: Walmart cobra volumétrico y el flete "
                      "saldría de un valor por omisión.")
    avisos.append(f"Categoría «{cfg['clave_visible']}» · exención "
                  f"{cfg.get('folio_exencion') or 'sin folio'} · SAT "
                  f"{cfg.get('clave_sat')}.")
    return {"payload": payload, "clave": clave, "cfg": cfg,
            "avisos": avisos, "presupuesto": pres, "imagenes": len(imgs),
            "comparativa": comparativa,
            "con_ia": bool([c for c in comparativa if c.get("usado")])}


async def preview(req: dict[str, Any]) -> dict[str, Any]:
    """El feed REAL antes de mandarlo, sin tocar Walmart."""
    from services import walmart
    sku = str(req.get("sku") or "").strip()
    if not walmart.disponible():
        return {"ok": False, "canal": CANAL, "sku": sku,
                "motivo": "Walmart no está configurado (faltan WM_CLIENT_ID / "
                          "WM_CLIENT_SECRET)."}
    try:
        armado = await _armar(req)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(exc)}

    items = armado["payload"].get("MPItem") or [{}]
    item = items[0]
    return {
        "ok": True, "canal": CANAL, "sku": sku,
        "categoria": armado["cfg"]["clave_visible"],
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
        "avisos": armado["avisos"],
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
    try:
        armado = await _armar(req)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "canal": CANAL, "sku": sku, "motivo": str(exc)}

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
