"""
packing_costos.py — Datos por pieza y costo de importación de un packing list.

Dos cosas distintas viven aquí:

1. **Derivación caja → pieza** (:func:`derivar_por_pieza`). El packing list habla
   de cartones; Mercado Libre y Odoo hablan de piezas. Todo lo "por unidad" sale
   de dividir el cartón entre las unidades que trae.
2. **Costo de importación** (:func:`calcular`), portado de ``kubera/costos/app.py``
   y validado contra ~60 contenedores reales::

       costo_por_m3    = costo_contenedor / total_cbm
       costo_mxn       = costo_usd * tipo_cambio
       costo_cbm_pieza = cbm_por_pieza * costo_por_m3
       costo_unitario  = costo_mxn + costo_cbm_pieza
       costo_total     = costo_unitario * unidades_totales

Las dos partes del costo son de naturaleza distinta y conviene no confundirlas:

  - ``costo_mxn`` es lo que se le pagó al proveedor por la pieza. Sale de la
    factura y solo existe si el archivo es ``CI&PL`` / ``INV&PL``; los ``PL`` a
    secas no lo traen y hay que capturarlo a mano en la UI.
  - ``costo_cbm_pieza`` es el flete prorrateado: el costo del contenedor
    repartido por volumen. Se calcula siempre.

**El denominador es global.** ``costo_por_m3`` divide entre el CBM del contenedor
completo, así que editar el volumen de UN renglón mueve el costo de TODOS. Por eso
:func:`calcular` recibe la lista entera y no hay una versión "por fila": recalcular
una fila sola daría un número silenciosamente equivocado.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("omnicanal.packing.costos")

# Defaults de Kubera (los mismos que costos/app.py). Se pueden sobreescribir por
# packing list desde la UI: el flete real y el tipo de cambio cambian por embarque.
COSTO_CONTENEDOR_DEFAULT = 525_000.0   # MXN, un 40' puesto en bodega
TIPO_CAMBIO_DEFAULT = 19.0             # MXN por USD


def _f(v: Any) -> float:
    """Float tolerante: None / '' / basura → 0.0."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Caja → pieza
# ═══════════════════════════════════════════════════════════════════════════════
# Arriba de este número de piezas por caja, suponer que van en UNA FILA deja de
# tener sentido físico. Con 8 piezas puede ser cierto; con 120 no — nadie acomoda
# 120 estuches en una hilera. El corte es un juicio, no una constante universal.
_MAX_PIEZAS_EN_FILA = 10


def dims_pieza(
    largo_caja: float, ancho_caja: float, alto_caja: float, unidades_por_caja: float,
) -> tuple[float, float, float]:
    """
    Estima las dimensiones de UNA pieza a partir del cartón y las piezas que trae.

    Son las dimensiones que Mercado Libre usa para el peso volumétrico y el fee
    de envío, así que el objetivo es una forma **plausible**, no solo un volumen
    correcto. Dos reglas según cuántas piezas van en la caja:

    - **Pocas piezas (≤10): se divide el lado más largo.** Modela piezas
      formadas en fila, que es como viajan las cajas con pocas unidades.

    - **Muchas piezas (>10): raíz cúbica sobre los tres lados.** Con 120 piezas
      la regla de la fila daba 42×25×**0.38 cm** para unos audífonos — volumen
      correcto, forma imposible, y un peso volumétrico que ML cobraría mal. La
      raíz cúbica da 8.5×5.1×9.3 cm, que sí es un estuche.

    En ambos casos **el volumen se conserva exacto**: L×W×H de la pieza da
    ``cbm_caja / unidades_por_caja``. Lo que cambia es cómo se reparte entre los
    tres lados. Sigue siendo una estimación: si las piezas vienen acomodadas de
    otra forma, corrígelas en la tabla y el solucionador respeta lo capturado.
    """
    lados = [_f(largo_caja), _f(ancho_caja), _f(alto_caja)]
    n = _f(unidades_por_caja)
    if not all(lados) or n <= 0:
        return (0.0, 0.0, 0.0)
    if n <= 1:
        # Una pieza por caja: la pieza ES la caja, sin estimación de por medio.
        return (round(lados[0], 2), round(lados[1], 2), round(lados[2], 2))

    if n <= _MAX_PIEZAS_EN_FILA:
        i_mayor = lados.index(max(lados))
        lados[i_mayor] = lados[i_mayor] / n
    else:
        factor = (1.0 / n) ** (1.0 / 3.0)
        lados = [l * factor for l in lados]
    return (round(lados[0], 2), round(lados[1], 2), round(lados[2], 2))


# Relaciones que ligan los campos de un renglón. Un packing list trae un
# subconjunto distinto en cada proveedor, así que en vez de un orden fijo se
# declaran las ecuaciones y se resuelven las que se puedan.
#
#   cajas × unidades_por_caja = unidades_totales      (dos cualesquiera → la tercera)
#   L_caja × W_caja × H_caja / 1e6 = cbm_caja         (dims ↔ volumen)
#   cbm_caja  / unidades_por_caja = cbm_por_pieza
#   peso_caja / unidades_por_caja = peso_unidad
#   dims_caja + unidades_por_caja → dims_pieza        (estimación, ver dims_pieza)
#
# Todo lo que el usuario haya tocado a mano (campos_editados) es DATO, no
# incógnita: se usa para resolver el resto y nunca se sobreescribe.
_DERIVABLES = (
    "unidades_por_caja", "unidades_totales", "numero_cajas", "cbm_caja",
    "cbm_por_pieza", "peso_unidad", "largo_pieza", "ancho_pieza", "alto_pieza",
)


def _cuadra(a: float, b: float, tolerancia: float = 0.05) -> bool:
    """¿Dos números dicen lo mismo, con holgura para el redondeo del proveedor?"""
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= tolerancia


def normalizar_semantica(fila: dict[str, Any]) -> dict[str, Any]:
    """
    Decide si cada columna es POR CAJA o POR PIEZA — sin creerle a su nombre.

    Este es el problema central de los packing lists chinos: los encabezados
    mienten. En un archivo real, ``piezas_totales`` valía 120 cuando el total
    era 4,440, y ``peso_unitario_kg`` valía 7.12 kg cuando la pieza pesaba 59
    gramos. Leer esos nombres al pie de la letra multiplicó el CBM por pieza
    por 37 y convirtió unos audífonos de 4 USD en un costo de $184.

    En vez de mapear nombres —que cambian con cada proveedor— se usa la
    REDUNDANCIA del archivo. Un packing list repite la misma información de
    varias formas, y esas repeticiones se contradicen si la lectura es errónea:

        L×W×H/1e6  ≟ cbm_master          → ¿las dims son de la caja?
        cbm × cajas ≟ cbm_total          → ¿el cbm es por caja?
        peso × cajas ≟ peso_bruto        → ¿el peso es por caja?
        piezas × cajas ≟ cantidad_total  → ¿"piezas" son por caja?
        valor_total ÷ precio_usd         → el total de piezas, sin discusión

    Cada prueba que cuadra es una confirmación independiente. Lo que no se puede
    probar se deja como está y se anota en ``avisos_semantica``: es mejor un dato
    sin tocar que una corrección inventada.
    """
    s = dict(fila)
    notas: list[str] = []

    cajas = _f(s.get("cajas") or s.get("numero_cajas"))
    piezas = _f(s.get("piezas") or s.get("unidades_totales"))
    cantidad_total = _f(s.get("cantidad_total"))
    valor_total = _f(s.get("valor_total"))
    precio = _f(s.get("precio_usd") or s.get("costo_usd"))
    peso_bruto = _f(s.get("peso_bruto_fila"))
    peso = _f(s.get("peso_caja"))
    cbm_master = _f(s.get("cbm_master") or s.get("cbm_caja"))
    cbm_total_fila = _f(s.get("cbm_total_fila"))

    # ── 1. ¿Cuál es el total REAL de piezas? ──
    # El importe de la línea es el testigo más confiable: valor_total ÷ precio
    # no depende de cómo se llamen las columnas.
    total_real = 0.0
    if valor_total > 0 and precio > 0:
        total_real = valor_total / precio
        origen_total = "valor_total ÷ precio_usd"
    elif cantidad_total > 0:
        total_real = cantidad_total
        origen_total = "columna de cantidad total"
    elif piezas > 0 and cajas > 0 and cantidad_total <= 0:
        # Sin testigo externo, se conserva lo leído.
        total_real = 0.0
        origen_total = ""

    if total_real > 0 and piezas > 0 and cajas > 0:
        if _cuadra(piezas * cajas, total_real):
            # "piezas" era POR CAJA: el total es piezas × cajas.
            s["unidades_por_caja"] = round(piezas, 4)
            s["unidades_totales"] = round(total_real, 2)
            s["piezas"] = round(total_real, 2)
            # Con una sola caja, "por caja" y "total" son el mismo número: no se
            # corrigió nada y avisarlo sería ruido en cada renglón del archivo.
            if cajas > 1:
                notas.append(
                    f"La columna de piezas ({piezas:.0f}) son piezas POR CAJA, no el "
                    f"total: {piezas:.0f} × {cajas:.0f} cajas = {total_real:.0f} "
                    f"(confirmado por {origen_total})."
                )
        elif _cuadra(piezas, total_real):
            s["unidades_totales"] = round(piezas, 2)
        else:
            notas.append(
                f"El total de piezas no cuadra: la columna dice {piezas:.0f} pero "
                f"{origen_total} da {total_real:.0f}. Se usó {total_real:.0f}."
            )
            s["unidades_totales"] = round(total_real, 2)
            s["piezas"] = round(total_real, 2)

    # ── 2. ¿El peso es por caja o por pieza? ──
    if peso > 0 and cajas > 0 and peso_bruto > 0:
        if _cuadra(peso * cajas, peso_bruto):
            s["peso_caja"] = peso        # confirmado: es por caja
        elif _cuadra(peso * _f(s.get("unidades_totales")), peso_bruto):
            # Era por pieza: se sube a caja para que la cadena siga igual.
            upc = _f(s.get("unidades_por_caja")) or 1
            s["peso_caja"] = round(peso * upc, 4)
            notas.append(f"El peso {peso} kg era POR PIEZA, no por caja.")

    # ── 3. ¿El CBM declarado es de la caja master? ──
    largo, ancho, alto = (_f(s.get("largo") or s.get("largo_caja")),
                          _f(s.get("ancho") or s.get("ancho_caja")),
                          _f(s.get("alto") or s.get("alto_caja")))
    if largo and ancho and alto and cbm_master > 0:
        if _cuadra(largo * ancho * alto / 1_000_000, cbm_master):
            s["cbm_caja"] = cbm_master   # dims y cbm hablan de la misma caja
        else:
            notas.append(
                f"Las dimensiones ({largo:.0f}×{ancho:.0f}×{alto:.0f} = "
                f"{largo*ancho*alto/1_000_000:.4f} m³) no cuadran con el CBM "
                f"declarado ({cbm_master}). Revisa cuál de los dos es correcto."
            )
    if cbm_master > 0 and cajas > 0 and cbm_total_fila > 0:
        if not _cuadra(cbm_master * cajas, cbm_total_fila):
            notas.append(
                f"El CBM total del renglón ({cbm_total_fila}) no es "
                f"{cbm_master} × {cajas:.0f} cajas."
            )

    s["avisos_semantica"] = notas
    return s


def completar(fila: dict[str, Any]) -> dict[str, Any]:
    """
    Rellena lo que falte a partir de lo que haya. Es el corazón de la captura.

    Los packing lists no traen las mismas columnas: unos dan cajas y piezas por
    caja, otros solo el total de piezas, otros el volumen de la caja sin sus
    dimensiones. Esta función acepta cualquier combinación y deduce el resto, en
    cualquier dirección — si das cajas y total, saca las piezas por caja; si das
    piezas por caja y total, saca las cajas.

    El objetivo final siempre es el mismo: **dimensiones y peso POR PIEZA**, que
    es lo que Mercado Libre necesita para cobrar el envío y lo único que
    ``costos_validados`` guarda.

    Devuelve la fila con dos listas de auditoría:
      ``derivados``  — qué campos calculó (la UI los puede marcar en gris).
      ``faltantes``  — qué sigue sin poder deducirse y hay que capturar a mano.
    """
    editados = set(fila.get("campos_editados") or [])
    s = dict(fila)
    derivados: list[str] = []

    def dato(campo: str) -> float:
        return _f(s.get(campo))

    def poner(campo: str, valor: float, dec: int = 4) -> None:
        """Escribe solo si el campo es incógnita: lo editado a mano es sagrado."""
        if campo in editados or valor <= 0:
            return
        if _f(s.get(campo)) > 0:
            return
        s[campo] = round(valor, dec)
        derivados.append(campo)

    # ── 1. Cantidades: cajas × unidades_por_caja = unidades_totales ──
    # Se resuelve la que falte, sea cual sea. Dos vueltas porque una deducción
    # puede habilitar la siguiente (p. ej. sacar upc y luego el peso por unidad).
    for _ in range(2):
        cajas, upc, total = dato("numero_cajas"), dato("unidades_por_caja"), dato("unidades_totales")
        if upc <= 0 and cajas > 0 and total > 0:
            poner("unidades_por_caja", total / cajas)
        elif total <= 0 and cajas > 0 and upc > 0:
            poner("unidades_totales", cajas * upc, dec=2)
        elif cajas <= 0 and total > 0 and upc > 0:
            poner("numero_cajas", total / upc, dec=2)

    # Una sola caja es el caso degenerado más común de los PL simples: si hay
    # total de piezas y nada más, se asume 1 caja con todo dentro. Es explícito
    # y se marca como derivado para que se vea que fue una suposición.
    if dato("numero_cajas") <= 0 and dato("unidades_totales") > 0 and dato("unidades_por_caja") <= 0:
        poner("numero_cajas", 1, dec=0)
        poner("unidades_por_caja", dato("unidades_totales"))

    upc = dato("unidades_por_caja")

    # ── 2. Volumen de la caja: dims ↔ cbm, en ambos sentidos ──
    largo, ancho, alto = dato("largo_caja"), dato("ancho_caja"), dato("alto_caja")
    if dato("cbm_caja") <= 0 and largo and ancho and alto:
        poner("cbm_caja", largo * ancho * alto / 1_000_000, dec=6)
    cbm_caja = dato("cbm_caja")

    # ── 3. Por pieza ──
    # El parser ya pudo resolver cbm_por_pieza con su tabla de prioridades
    # (incluidas las cajas compartidas por celdas merged). Solo se recalcula si
    # falta, o si el usuario tocó algo de la caja y no el resultado.
    if "cbm_por_pieza" not in editados:
        recalcular = dato("cbm_por_pieza") <= 0 or bool(
            editados & {"cbm_caja", "unidades_por_caja", "largo_caja",
                        "ancho_caja", "alto_caja", "unidades_totales", "numero_cajas"}
        )
        if recalcular and cbm_caja > 0 and upc > 0:
            s["cbm_por_pieza"] = round(cbm_caja / upc, 6)
            s["cbm_origen"] = "caja_entre_unidades"
            derivados.append("cbm_por_pieza")

    poner("peso_unidad", dato("peso_caja") / upc if upc > 0 else 0)

    if not (editados & {"largo_pieza", "ancho_pieza", "alto_pieza"}):
        lp, ap, hp = dims_pieza(largo, ancho, alto, upc)
        if lp:
            for campo, val in (("largo_pieza", lp), ("ancho_pieza", ap), ("alto_pieza", hp)):
                if _f(s.get(campo)) <= 0:
                    s[campo] = val
                    derivados.append(campo)

    # ── 4. Qué sigue faltando ──
    # Solo se listan los que BLOQUEAN el guardado o el envío de ML; el resto es
    # informativo. Sin dimensiones de pieza no se puede escribir en
    # costos_validados; sin peso, ML calcula mal el envío.
    faltantes: list[str] = []
    if not (dato("largo_pieza") and dato("ancho_pieza") and dato("alto_pieza")):
        faltantes.append("dimensiones de pieza")
    if dato("peso_unidad") <= 0:
        faltantes.append("peso por pieza")
    if dato("unidades_totales") <= 0:
        faltantes.append("unidades")

    s["derivados"] = sorted(set(derivados))
    s["faltantes"] = faltantes
    return s


# Nombre anterior, conservado para no romper llamadas existentes.
derivar_por_pieza = completar


# ═══════════════════════════════════════════════════════════════════════════════
# Costo de importación
# ═══════════════════════════════════════════════════════════════════════════════
def cbm_total_fila(fila: dict[str, Any]) -> float:
    """
    CBM que ocupa la fila completa: ``cbm_por_pieza * unidades_totales``.

    Se calcula desde el CBM por pieza en vez de leer la columna "Total Volume" del
    Excel, para que al editar unidades o dimensiones en la UI el total siga al dato
    editado y no al del archivo original.
    """
    return _f(fila.get("cbm_por_pieza")) * _f(fila.get("unidades_totales"))


def calcular(
    filas: list[dict[str, Any]],
    costo_contenedor: float | None = None,
    tipo_cambio: float | None = None,
    derivar: bool = True,
) -> dict[str, Any]:
    """
    Calcula el costo de cada fila y los totales del contenedor.

    Devuelve ``{filas, totales}``. ``filas`` es una lista nueva (no muta la
    entrada) con los campos por pieza derivados y ``costo_mxn``,
    ``costo_cbm_pieza``, ``costo_unitario`` y ``costo_total`` añadidos.
    """
    costo_contenedor = _f(costo_contenedor) or COSTO_CONTENEDOR_DEFAULT
    tipo_cambio = _f(tipo_cambio) or TIPO_CAMBIO_DEFAULT

    base = [derivar_por_pieza(f) for f in filas] if derivar else [dict(f) for f in filas]

    cbms = [cbm_total_fila(f) for f in base]
    total_cbm = sum(cbms)
    # Sin volumen no hay prorrateo posible: el flete queda en 0 y se avisa, en vez
    # de reventar por división entre cero o repartir a ciegas.
    costo_por_m3 = (costo_contenedor / total_cbm) if total_cbm > 0 else 0.0

    salida: list[dict[str, Any]] = []
    total_unidades = 0.0
    total_costo = 0.0
    filas_sin_costo = 0

    for fila, cbm_fila in zip(base, cbms):
        unidades = _f(fila.get("unidades_totales"))
        costo_usd = _f(fila.get("costo_usd"))
        if costo_usd <= 0:
            filas_sin_costo += 1

        costo_mxn = costo_usd * tipo_cambio
        costo_cbm_pieza = _f(fila.get("cbm_por_pieza")) * costo_por_m3
        costo_unitario = costo_mxn + costo_cbm_pieza
        costo_total = costo_unitario * unidades

        total_unidades += unidades
        total_costo += costo_total
        salida.append({
            **fila,
            "cbm_total_fila": round(cbm_fila, 6),
            "costo_mxn": round(costo_mxn, 2),
            "costo_cbm_pieza": round(costo_cbm_pieza, 4),
            "costo_unitario": round(costo_unitario, 2),
            "costo_total": round(costo_total, 2),
        })

    avisos: list[str] = []
    if total_cbm <= 0:
        avisos.append("El contenedor quedó con 0 CBM: no se puede prorratear el "
                      "flete. Revisa dimensiones y unidades.")
    if filas_sin_costo:
        avisos.append(f"{filas_sin_costo} de {len(filas)} renglones no tienen costo "
                      "USD; su costo unitario solo incluye el flete.")
    sin_dims = sum(1 for f in salida if not _f(f.get("largo_pieza")))
    if sin_dims:
        avisos.append(f"{sin_dims} renglones no tienen dimensiones de pieza: sin "
                      "ellas Mercado Libre no puede calcular el envío.")

    # Tres conteos distintos que conviene no mezclar: un contenedor con 17
    # renglones puede tener 12 SKUs (el proveedor repite productos en varias
    # líneas) y 7 productos (varios SKUs son variantes del mismo).
    skus = {(f.get("sku") or "").strip() for f in salida}
    skus.discard("")
    productos = {(f.get("sku_base") or f.get("sku") or "").strip() for f in salida}
    productos.discard("")

    totales = {
        "costo_contenedor": round(costo_contenedor, 2),
        "tipo_cambio": tipo_cambio,
        "total_cbm": round(total_cbm, 4),
        "costo_por_m3": round(costo_por_m3, 2),
        "total_unidades": int(total_unidades),
        "total_filas": len(filas),
        "total_skus": len(skus),
        "total_productos": len(productos),
        "costo_total": round(total_costo, 2),
        "filas_sin_costo": filas_sin_costo,
        "avisos": avisos,
    }
    return {"filas": salida, "totales": totales}


def agrupar_por_sku(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Consolida las filas que comparten SKU (el proveedor repite el mismo producto
    en varios renglones del packing list).

    El costo USD se promedia **ponderado por unidades**, no aritméticamente: si 900
    piezas vinieron a $2 y 100 a $10, el costo del SKU es $2.80, no $6. El CBM por
    pieza se pondera igual, por la misma razón.
    """
    acc: dict[str, dict[str, Any]] = {}
    for f in filas:
        sku = (f.get("sku") or "").strip()
        if not sku:
            continue
        unidades = _f(f.get("unidades_totales"))
        e = acc.setdefault(sku, {
            "sku": sku,
            "sku_base": f.get("sku_base") or "",
            "variante": f.get("variante"),
            "nombre": f.get("nombre") or f.get("producto") or "",
            "subcategoria": f.get("subcategoria"),
            "imagen_url": f.get("imagen_url"),
            "largo_pieza": f.get("largo_pieza"),
            "ancho_pieza": f.get("ancho_pieza"),
            "alto_pieza": f.get("alto_pieza"),
            "peso_unidad": f.get("peso_unidad"),
            "unidades_totales": 0.0,
            "numero_cajas": 0.0,
            "cbm_total": 0.0,
            "costo_total": 0.0,
            "_costo_x_unidades": 0.0,
            "_unidades_con_costo": 0.0,
            "filas": 0,
        })
        e["unidades_totales"] += unidades
        e["numero_cajas"] += _f(f.get("numero_cajas"))
        e["cbm_total"] += _f(f.get("cbm_total_fila")) or cbm_total_fila(f)
        e["costo_total"] += _f(f.get("costo_total"))
        e["filas"] += 1
        if not e["imagen_url"] and f.get("imagen_url"):
            e["imagen_url"] = f["imagen_url"]
        costo = _f(f.get("costo_usd"))
        if costo > 0 and unidades > 0:
            e["_costo_x_unidades"] += costo * unidades
            e["_unidades_con_costo"] += unidades

    salida: list[dict[str, Any]] = []
    for e in acc.values():
        unidades = e["unidades_totales"]
        con_costo = e.pop("_unidades_con_costo")
        costo_usd = (e.pop("_costo_x_unidades") / con_costo) if con_costo > 0 else 0.0
        salida.append({
            **e,
            "unidades_totales": int(unidades),
            "numero_cajas": int(e["numero_cajas"]),
            "cbm_total": round(e["cbm_total"], 4),
            "cbm_por_pieza": round(e["cbm_total"] / unidades, 6) if unidades > 0 else 0.0,
            "costo_usd": round(costo_usd, 4),
            "costo_total": round(e["costo_total"], 2),
            "costo_unitario": round(e["costo_total"] / unidades, 2) if unidades > 0 else 0.0,
        })
    salida.sort(key=lambda x: x["sku"])
    return salida
