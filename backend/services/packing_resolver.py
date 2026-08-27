"""
packing_resolver.py — Orquestador del "Resolver" de la pantalla de Costos.

Encadena el pipeline completo para UN packing list y deja el resultado en
memoria para que la UI lo lea, lo edite y decida si lo guarda:

    packing_parser     → renglones + fotos embebidas del xlsx
    packing_sku        → nombre en español y SKU propuesto (DeepSeek + Gemini)
    packing_costos     → caja→pieza y prorrateo del flete por volumen
    packing_comparador → empate con costos_validados y análisis del agente

**Nada se persiste.** No hay tabla, no hay Storage, no hay histórico: es una
herramienta de un solo uso. El único write es el UPSERT final a
``costos_validados``, y solo con lo que el usuario confirme.

Consecuencia práctica de no persistir: los trabajos viven en un diccionario en
memoria y se pierden si el backend reinicia. Por eso se les pone caducidad y un
tope — un contenedor de 1000 renglones con sus fotos en base64 no es poca RAM.

El procesamiento corre en un hilo porque la homologación de un contenedor grande
son ~150 llamadas al LLM: minutos, muy por encima de cualquier timeout HTTP.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import threading
import time
import uuid
from typing import Any

from services import (
    packing_comparador as comp,
    packing_costos,
    packing_parser,
    packing_sku,
    packing_taxonomia as tax,
)

log = logging.getLogger("omnicanal.packing.resolver")

# Sin persistencia, la memoria es el único almacén: hay que acotarla.
_TTL = 60 * 60 * 3        # 3 h: suficiente para revisar un contenedor con calma
_MAX_TRABAJOS = 12
_MAX_FOTO_B64 = 120_000   # ~90 KB por foto; arriba de eso se omite la miniatura

_trabajos: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

_PASOS = {
    "parseando": "Leyendo el Excel",
    "clasificando": "Identificando productos con IA",
    "verificando_fotos": "Verificando variantes por imagen",
    "calculando": "Calculando costos",
    "empatando": "Empatando con los costos actuales",
    "empatando_fotos": "Empatando por reconocimiento de imagen",
    "analizando": "Analizando diferencias",
    "listo": "Listo",
    "error": "Error",
}


def _purgar() -> None:
    """Tira lo viejo y lo que sobre del tope. Se llama con el lock tomado."""
    ahora = time.time()
    for k in [k for k, v in _trabajos.items() if ahora - v.get("creado", 0) > _TTL]:
        _trabajos.pop(k, None)
    if len(_trabajos) > _MAX_TRABAJOS:
        sobran = sorted(_trabajos.items(), key=lambda kv: kv[1].get("creado", 0))
        for k, _ in sobran[: len(_trabajos) - _MAX_TRABAJOS]:
            _trabajos.pop(k, None)


def _marcar(jid: str, paso: str, actual: int = 0, total: int = 0, **extra: Any) -> None:
    with _lock:
        t = _trabajos.get(jid)
        if not t:
            return
        t.update({
            "paso": paso,
            "paso_label": _PASOS.get(paso, paso),
            "actual": actual,
            "total": total,
            "actualizado": time.time(),
            **extra,
        })


def estado(jid: str) -> dict[str, Any] | None:
    """Estado + resultado del trabajo. None si caducó o el backend reinició."""
    with _lock:
        t = _trabajos.get(jid)
        return dict(t) if t else None


# ── Pipeline ─────────────────────────────────────────────────────────────────
def _miniatura(datos: bytes | None, lado: int = 120) -> str | None:
    """
    Foto del renglón como data URI, para enseñarla en la tabla sin Storage.

    Se reduce con Pillow; si no está instalado se manda el original salvo que sea
    muy pesado. Sin esto, un contenedor de 1000 fotos hace la respuesta JSON
    inmanejable.

    ``lado`` es parámetro desde v0.281.0: el validador de publicados enseña las
    fotos más grandes (la comparación la hace un humano de un vistazo, no una
    tabla de 1000 renglones). El default es el de siempre y nadie más se entera.
    """
    if not datos:
        return None
    crudo, mime = datos, "image/png"
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(datos)).convert("RGB")
        im.thumbnail((lado, lado))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=72)
        crudo, mime = buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        pass
    b64 = base64.b64encode(crudo).decode()
    if len(b64) > _MAX_FOTO_B64:
        return None
    return f"data:{mime};base64,{b64}"


def _procesar(jid: str, xlsx: bytes, contenedor_forzado: str | None,
              costo_contenedor: float | None, tipo_cambio: float | None,
              usar_vision: bool) -> None:
    try:
        # 1) Parseo
        _marcar(jid, "parseando")
        leido = packing_parser.leer(xlsx)
        filas, imagenes = leido["filas"], leido["imagenes"]
        avisos = list(leido["avisos"])

        # Los encabezados del packing list MIENTEN: "piezas_totales" suele ser
        # piezas por caja y "peso_unitario" el peso del cartón. Antes de creerle
        # a nada, se contrastan las columnas entre sí (el archivo se valida solo
        # por redundancia). Sin esto, un renglón de audífonos salió con 7.12 kg
        # por pieza y un CBM 37 veces más alto.
        filas = [packing_costos.normalizar_semantica(f) for f in filas]
        for f in filas[:200]:            # tope: no inundar la UI de avisos
            for a in f.get("avisos_semantica") or []:
                if a not in avisos:
                    avisos.append(a)

        # El packing list lista una fila por CAJA. Se agrupan las que son el
        # mismo producto ANTES de todo lo demás: si no, el LLM empata 1,052
        # filas en vez de 133 productos y las cajas se guardan en 1.
        filas, stats_grupo = _consolidar_renglones(filas, imagenes)
        if stats_grupo["agrupados"]:
            avisos.append(
                f"{stats_grupo['renglones_excel']} renglones del Excel se "
                f"agruparon en {stats_grupo['productos']} productos (el packing "
                f"list lista una fila por caja; se sumaron cajas y unidades)."
            )

        with _lock:
            nombre_archivo = _trabajos[jid]["archivo"]
        codigo = contenedor_forzado or packing_parser.contenedor_desde_nombre(nombre_archivo)

        # 2) ¿Este contenedor ya tiene costos capturados?
        encontrados = comp.buscar_contenedor(codigo)
        contenedor_bd = encontrados[0]["contenedor"] if encontrados else ""
        cands = comp.candidatos(contenedor_bd) if contenedor_bd else []
        # Los identificadores provisionales del app viejo (5279-0001) NO sirven
        # como blanco de empate: no existen en Woo, así que no tienen nombre ni
        # foto, y un empate contra ellos haría que guardar() escribiera el costo
        # sobre el provisional en vez del SKU real. Se excluyen y se dice.
        provisionales = [c for c in cands if c.get("provisional")]
        cands = [c for c in cands if not c.get("provisional")]
        if provisionales:
            avisos.append(
                f"{len(provisionales)} de los {len(provisionales) + len(cands)} "
                f"costos capturados de {contenedor_bd} usan identificador "
                f"provisional (tipo {provisionales[0]['sku']}), no SKU de Kubera: "
                "no existen en WooCommerce, así que no tienen nombre ni foto y "
                "quedan FUERA del empate. Esos renglones se tratan como nuevos."
            )
        if not cands:
            avisos.append(
                f"El contenedor {codigo} no tiene costos capturados con SKU real: "
                "todos los renglones se tratan como productos nuevos."
            )

        # 3) Homologación. Aporta el nombre en español (que el empate necesita) y
        #    un SKU propuesto para los renglones que resulten ser nuevos.
        def _prog(paso: str, actual: int, total: int) -> None:
            _marcar(jid, paso, actual, total)

        homologado = packing_sku.homologar_sync(
            filas, imagenes=imagenes, contadores=tax.Contadores(),
            skus_odoo=set(), usar_vision=usar_vision, progreso=_prog,
        )
        filas = homologado["filas"]

        # 4) Costos (caja→pieza + prorrateo del flete)
        _marcar(jid, "calculando")
        filas = [_renombrar(f) for f in filas]
        calculado = packing_costos.calcular(
            filas, costo_contenedor=costo_contenedor, tipo_cambio=tipo_cambio,
        )
        filas, totales = calculado["filas"], calculado["totales"]
        avisos.extend(totales.pop("avisos", []))

        # Miniatura por renglón (data URI; no hay Storage en este flujo)
        for f in filas:
            f["imagen_b64"] = _miniatura(imagenes.get(f.get("fila_idx")))

        # 5) Empate con los SKUs del contenedor
        _marcar(jid, "empatando", 0, len(filas))
        nombres = comp.nombres_de_skus([c["sku"] for c in cands]) if cands else {}
        # Fotos del catálogo: las necesita el empate manual de la UI (ver la del
        # packing junto a la del producto) y también el empate por imagen.
        fotos_catalogo = comp.imagenes_de_skus([c["sku"] for c in cands]) if cands else {}
        empates = asyncio.run(comp.empatar(filas, cands, progreso=_prog))

        # 5b) Segunda pasada POR IMAGEN sobre lo que el texto no resolvió.
        #     El proveedor escribe "Auriculares" y el catálogo "Audífonos
        #     Invisibles Bluetooth": misma cosa, cero palabras en común. La foto
        #     sí lo resuelve, y solo se paga por los renglones que quedaron sueltos.
        if usar_vision and cands:
            reclamados = {e["sku"] for e in empates if e.get("sku")}
            libres = [c for c in cands if c["sku"] not in reclamados]
            pendientes = [(i, f) for i, (f, e) in enumerate(zip(filas, empates))
                          if not e.get("sku")]
            if pendientes and libres:
                _marcar(jid, "empatando_fotos", 0, len(pendientes))
                por_foto = asyncio.run(comp.empatar_por_imagen(
                    pendientes, libres, imagenes, nombres, progreso=_prog,
                ))
                for i, e in por_foto.items():
                    empates[i] = e
                if por_foto:
                    avisos.append(
                        f"{len(por_foto)} renglones se empataron por reconocimiento "
                        "de imagen (el texto no alcanzaba). Revísalos."
                    )

        comparacion = comp.comparar(filas, cands, empates)

        # 6) Agente
        _marcar(jid, "analizando")
        analisis = asyncio.run(comp.analizar(comparacion, contenedor_bd or codigo, totales))

        with _lock:
            t = _trabajos.get(jid)
            if t:
                t.update({
                    "paso": "listo", "paso_label": _PASOS["listo"],
                    "actual": len(filas), "total": len(filas),
                    "contenedor": codigo,
                    "contenedor_bd": contenedor_bd,
                    "contenedores_encontrados": encontrados,
                    # Los SKUs del contenedor, para que la UI ofrezca un
                    # SELECTOR en vez de que el usuario teclee el SKU a ciegas.
                    "candidatos": [
                        {
                            "sku": c["sku"],
                            "nombre": nombres.get(c["sku"], ""),
                            "imagen": fotos_catalogo.get(c["sku"]),
                            "costo_total": float(c.get("costo_total") or 0),
                            "largo": float(c.get("largo") or 0),
                            "ancho": float(c.get("ancho") or 0),
                            "alto": float(c.get("alto") or 0),
                            "peso": float(c.get("peso") or 0),
                            "cajas": float(c.get("cajas") or 0),
                            "piezas_por_caja": float(c.get("piezas_por_caja") or 0),
                        }
                        for c in cands
                    ],
                    "totales": totales,
                    "avisos": avisos,
                    "stats": homologado["stats"],
                    "comparacion": comparacion,
                    "analisis": analisis,
                    "tsv": comp.tsv(comparacion, totales.get("tipo_cambio") or 0),
                    "actualizado": time.time(),
                })
        log.info("Resolver %s listo: %s", codigo, comparacion["resumen"])

    except Exception as exc:  # noqa: BLE001
        log.exception("Resolver falló")
        _marcar(jid, "error", error=str(exc)[:500])


def _consolidar_renglones(
    filas: list[dict[str, Any]], imagenes: dict[int, bytes],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Junta los renglones que son el MISMO producto repetido.

    Los packing lists chinos listan **una fila por CAJA**, no por producto: un
    contenedor real trae 1,052 renglones y solo 133 fotos distintas — un mismo
    mueble aparece 150 veces, cada vez con 1 caja y 1 pieza. Sin este paso el
    resto del pipeline trata cada caja como un producto: le pide al LLM empatar
    1,052 filas contra el catálogo, y escribe ``cajas=1`` en costos_validados
    cuando en realidad eran 150.

    La clave de agrupación es ``(sha256 de la foto, descripción, L, W, H)``.
    Exige que coincidan las tres cosas a propósito: dos productos distintos
    pueden compartir una foto genérica, pero es muy improbable que además
    coincidan descripción y dimensiones al centímetro. Los renglones sin foto se
    agrupan solo por descripción y dimensiones.

    Se suman cajas y unidades; el CBM y el costo por pieza NO se suman —
    son por unidad y el grupo comparte el mismo valor.
    """
    grupos: dict[tuple, dict[str, Any]] = {}
    orden: list[tuple] = []

    for f in filas:
        foto = imagenes.get(f.get("fila_idx"))
        clave = (
            hashlib.sha256(foto).hexdigest() if foto else "",
            (f.get("producto") or "").strip().lower(),
            (f.get("producto_chn") or "").strip(),
            round(float(f.get("largo") or 0), 1),
            round(float(f.get("ancho") or 0), 1),
            round(float(f.get("alto") or 0), 1),
        )
        if clave not in grupos:
            grupos[clave] = {**f, "renglones_origen": 1, "_cbm_total": 0.0}
            orden.append(clave)
            g = grupos[clave]
        else:
            g = grupos[clave]
            g["cajas"] = float(g.get("cajas") or 0) + float(f.get("cajas") or 0)
            g["piezas"] = float(g.get("piezas") or 0) + float(f.get("piezas") or 0)
            g["renglones_origen"] += 1
        # El CBM del grupo se acumula en volumen ABSOLUTO y al final se divide
        # entre las piezas. Quedarse con el cbm_por_pieza del primer renglón
        # perdía volumen cuando los miembros no lo tenían idéntico (cajas
        # compartidas detectadas por merge), y el total del contenedor —que es
        # el denominador del flete— salía movido.
        g["_cbm_total"] += float(f.get("cbm_por_pieza") or 0) * float(f.get("piezas") or 0)

    salida = []
    for k in orden:
        g = grupos[k]
        piezas = float(g.get("piezas") or 0)
        cbm_total = g.pop("_cbm_total")
        if piezas > 0 and cbm_total > 0:
            g["cbm_por_pieza"] = cbm_total / piezas
        salida.append(g)
    stats = {
        "renglones_excel": len(filas),
        "productos": len(salida),
        "agrupados": len(filas) - len(salida),
    }
    if stats["agrupados"]:
        log.info("Consolidación: %d renglones → %d productos.",
                 stats["renglones_excel"], stats["productos"])
    return salida, stats


# El parser habla del Excel; el resto del pipeline habla del dominio.
_ALIAS = {
    "cajas": "numero_cajas", "piezas": "unidades_totales",
    "largo": "largo_caja", "ancho": "ancho_caja", "alto": "alto_caja",
    "cbm_master": "cbm_caja", "precio_usd": "costo_usd",
}


def _renombrar(fila: dict[str, Any]) -> dict[str, Any]:
    out = dict(fila)
    for viejo, nuevo in _ALIAS.items():
        if viejo in out and nuevo not in out:
            out[nuevo] = out.pop(viejo)
    return out


# ── Entrada pública ──────────────────────────────────────────────────────────
def iniciar(
    xlsx: bytes,
    nombre_archivo: str,
    contenedor: str | None = None,
    costo_contenedor: float | None = None,
    tipo_cambio: float | None = None,
    usar_vision: bool = True,
) -> dict[str, Any]:
    """Arranca el análisis en segundo plano y devuelve ``{id, paso}``."""
    jid = uuid.uuid4().hex[:12]
    with _lock:
        _purgar()
        _trabajos[jid] = {
            "id": jid,
            "archivo": nombre_archivo,
            "paso": "parseando",
            "paso_label": _PASOS["parseando"],
            "actual": 0, "total": 0,
            "creado": time.time(), "actualizado": time.time(),
        }
    threading.Thread(
        target=_procesar,
        args=(jid, xlsx, contenedor, costo_contenedor, tipo_cambio, usar_vision),
        name=f"resolver-{jid}", daemon=True,
    ).start()
    return {"id": jid, "paso": "parseando", "paso_label": _PASOS["parseando"]}


def actualizar_empate(jid: str, indice: int, sku: str | None) -> dict[str, Any] | None:
    """
    Corrige a mano el SKU de un renglón y recalcula su comparación.

    Es lo que pasa cuando el usuario arregla un empate del LLM: hay que rehacer
    el cruce de ESE renglón contra el costo actual del SKU nuevo, no solo cambiar
    la etiqueta.
    """
    with _lock:
        t = _trabajos.get(jid)
        if not t or "comparacion" not in t:
            return None
        filas = t["comparacion"]["filas"]
        if not (0 <= indice < len(filas)):
            return None
        contenedor_bd = t.get("contenedor_bd") or ""

    sku = (sku or "").strip() or None
    if sku and comp.es_provisional(sku):
        # El buscador del catálogo sale de WooCommerce, así que por la UI no
        # puede llegar un provisional; por la API pelona sí. Misma invariante que
        # en guardar(): un provisional nunca es un blanco válido.
        log.warning("empate manual rechazado: %s es identificador provisional", sku)
        return None
    actual = None
    if sku and contenedor_bd:
        encontrado = [c for c in comp.candidatos(contenedor_bd)
                      if c["sku"] == sku and not c.get("provisional")]
        actual = encontrado[0] if encontrado else None

    with _lock:
        t = _trabajos.get(jid)
        if not t:
            return None
        fila = t["comparacion"]["filas"][indice]
        fila["sku"] = sku
        if actual is None:
            fila["actual"], fila["estado"], fila["diferencia"] = None, "nuevo", None
        else:
            viejo = comp._f(actual.get("costo_total"))
            nuevo = comp._f(fila["nuevo"]["costo_total"])
            diff = ((nuevo - viejo) / viejo) if viejo > 0 else None
            fila["actual"] = {
                "costo_producto": comp._f(actual.get("costo_producto")),
                "costo_cbm": comp._f(actual.get("costo_cbm")),
                "costo_total": viejo,
                "largo": comp._f(actual.get("largo")),
                "ancho": comp._f(actual.get("ancho")),
                "alto": comp._f(actual.get("alto")),
                "peso": comp._f(actual.get("peso")),
                "cajas": comp._f(actual.get("cajas")),
                "piezas_por_caja": comp._f(actual.get("piezas_por_caja")),
            }
            fila["diferencia"] = round(diff, 4) if diff is not None else None
            fila["estado"] = ("revisar" if diff is not None
                              and abs(diff) >= comp.UMBRAL_ALERTA else "igual")
        # Los contadores del resumen y el TSV se rehacen: si no, la cabecera
        # seguiría diciendo "3 nuevos" después de que el usuario empató dos.
        f = t["comparacion"]["filas"]
        t["comparacion"]["resumen"].update({
            "nuevos": sum(1 for x in f if x["estado"] == "nuevo"),
            "revisar": sum(1 for x in f if x["estado"] == "revisar"),
            "iguales": sum(1 for x in f if x["estado"] == "igual"),
        })
        t["tsv"] = comp.tsv(t["comparacion"], (t.get("totales") or {}).get("tipo_cambio") or 0)
        return dict(fila)


_ENTRADAS = (
    "numero_cajas", "unidades_por_caja", "unidades_totales",
    "largo_caja", "ancho_caja", "alto_caja", "peso_caja", "cbm_caja", "costo_usd",
    "largo_pieza", "ancho_pieza", "alto_pieza", "peso_unidad", "cbm_por_pieza",
)


def capturar(jid: str, indice: int, campos: dict[str, Any]) -> dict[str, Any] | None:
    """
    Captura datos de un renglón y vuelve a derivar todo lo que dependa de ellos.

    Es lo que hace utilizable un packing list incompleto: das lo que el archivo
    trae —cajas y piezas por caja, o solo el total, o el volumen sin
    dimensiones— y el solucionador deduce el resto hasta llegar a dimensiones y
    peso POR PIEZA, que es lo único que ``costos_validados`` guarda.

    Recalcula el contenedor completo, no solo el renglón: el flete se prorratea
    sobre el CBM total, así que cambiar las unidades de una fila mueve el costo
    de todas.
    """
    with _lock:
        t = _trabajos.get(jid)
        if not t or "comparacion" not in t:
            return None
        filas = t["comparacion"]["filas"]
        if not (0 <= indice < len(filas)):
            return None
        totales_previos = dict(t.get("totales") or {})

    limpio = {k: v for k, v in campos.items() if k in _ENTRADAS and v is not None}
    if not limpio:
        return None

    with _lock:
        fila = _trabajos[jid]["comparacion"]["filas"][indice]
        nuevo = dict(fila.get("nuevo") or {})
        nuevo.update(limpio)
        # Lo capturado a mano es DATO: se marca para que el solucionador lo use
        # como entrada y nunca lo sobreescriba con una derivación.
        editados = sorted(set(fila.get("campos_editados") or []) | set(limpio))
        fila["nuevo"] = nuevo
        fila["campos_editados"] = editados
        filas_todas = [dict(f) for f in _trabajos[jid]["comparacion"]["filas"]]

    # Re-derivar y recalcular el contenedor entero.
    crudas = [{**f["nuevo"], "campos_editados": f.get("campos_editados") or [],
               "unidades_totales": f["nuevo"].get("unidades", f["nuevo"].get("unidades_totales"))}
              for f in filas_todas]
    recalc = packing_costos.calcular(
        crudas,
        costo_contenedor=totales_previos.get("costo_contenedor"),
        tipo_cambio=totales_previos.get("tipo_cambio"),
    )

    with _lock:
        t = _trabajos.get(jid)
        if not t:
            return None
        for f, r in zip(t["comparacion"]["filas"], recalc["filas"]):
            f["nuevo"].update({
                k: r.get(k) for k in
                ("largo", "ancho", "alto", "peso", "cajas", "piezas_por_caja",
                 "costo_producto", "costo_cbm", "costo_total", "costo_usd", "unidades")
                if k in r
            })
            f["nuevo"].update({
                "largo": r.get("largo_pieza") or f["nuevo"].get("largo"),
                "ancho": r.get("ancho_pieza") or f["nuevo"].get("ancho"),
                "alto": r.get("alto_pieza") or f["nuevo"].get("alto"),
                "peso": r.get("peso_unidad") or f["nuevo"].get("peso"),
                "cajas": r.get("numero_cajas") or f["nuevo"].get("cajas"),
                "piezas_por_caja": r.get("unidades_por_caja") or f["nuevo"].get("piezas_por_caja"),
                "unidades": r.get("unidades_totales") or f["nuevo"].get("unidades"),
                "costo_producto": r.get("costo_mxn", f["nuevo"].get("costo_producto")),
                "costo_cbm": r.get("costo_cbm_pieza", f["nuevo"].get("costo_cbm")),
                "costo_total": r.get("costo_unitario", f["nuevo"].get("costo_total")),
            })
            f["faltantes"] = r.get("faltantes") or []
        t["totales"] = recalc["totales"]
        t["tsv"] = comp.tsv(t["comparacion"], (t.get("totales") or {}).get("tipo_cambio") or 0)
        return {"fila": dict(t["comparacion"]["filas"][indice]),
                "totales": recalc["totales"]}


def guardar(
    jid: str,
    solo_skus: list[str] | None = None,
    editados: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Escribe a ``costos_validados`` los renglones del trabajo.

    ``editados`` son los valores FINALES que dejó el usuario en la tabla, uno por
    índice de renglón. Se escriben esos, no los que salieron del packing list:
    la pantalla existe justamente para corregir una dimensión mal estimada antes
    de que se vuelva un fee de envío equivocado. Lo que el usuario no tocó llega
    igual que se calculó.

    ``solo_skus`` acota qué se guarda: es lo que permite aceptar todo menos los
    que el agente marcó para revisión.
    """
    with _lock:
        t = _trabajos.get(jid)
        if not t or "comparacion" not in t:
            return None
        filas = [dict(f) for f in t["comparacion"]["filas"]]
        contenedor = t.get("contenedor_bd") or t.get("contenedor") or ""

    # Overrides del usuario, por índice de renglón.
    for e in editados or []:
        try:
            i = int(e.get("indice"))
        except (TypeError, ValueError):
            continue
        if not (0 <= i < len(filas)):
            continue
        fila = filas[i] = dict(filas[i])
        if e.get("sku") is not None:
            fila["sku"] = (e.get("sku") or "").strip() or None
        valores = dict(fila.get("nuevo") or {})
        for campo in ("largo", "ancho", "alto", "peso", "costo_producto",
                      "costo_cbm", "costo_total", "cajas", "piezas_por_caja"):
            if e.get(campo) is not None:
                valores[campo] = e[campo]
        fila["nuevo"] = valores

    if solo_skus is not None:
        permitidos = set(solo_skus)
        filas = [f for f in filas if (f.get("sku") or "") in permitidos]

    resultado = comp.guardar(filas, contenedor)
    with _lock:
        if t := _trabajos.get(jid):
            t["guardado"] = {**resultado, "cuando": time.time()}
    return resultado
