"""
radar_precios.py — Radar de precios por contenedor · F1 (v0): SOLO Mercado Libre,
SOLO LECTURA.

QUÉ CONTESTA
    Para cada SKU con publicación ACTIVA en Mercado Libre: ¿su precio está por
    arriba, por abajo o en línea con lo que cobra el mercado por su búsqueda, y
    hacia dónde convendría moverlo sin bajar del PISO de su clase? La meta del
    plan es recuperar la cifra X (una cifra total por contenedor, decisión de
    Eduardo del 28-sep-2026); por eso el radar mide CONTRIBUCIÓN por pieza (lo
    que cada venta aporta a recuperar el contenedor), no margen sobre costo:
    aquí no entra el costo del producto, entra lo que el canal se queda.

LO QUE NO HACE — y es la mitad de su diseño
    · No escribe NADA: ni costing.costos_finales, ni channel.listings, ni Woo,
      ni marketplaces. Solo SELECT. No llama a ninguna API.
    · No usa `publicaciones_panel.margen_de` ni la regla de mercado del
      Publicador: tiene sus funciones propias (las de abajo), con pruebas.
    · No enciende ni lee LEER_SKU_CONTENEDOR: el contenedor se consulta directo
      a `costing.sku_contenedor` SOLO si la tabla existe (`to_regclass`); si no
      existe (producción hoy), sale del número que trae PILOTO, y solo para
      esos SKUs.

PILOTO (28-sep-2026)
    Mientras PILOTO tenga SKUs, el LISTADO y sus conteos muestran solo esos; el
    universo se sigue armando COMPLETO (misma caché de 10 min) y el recorte va
    encima, en `filtrar`. `?todos=1` quita el recorte y el detalle /{sku}
    contesta para cualquier SKU del universo.

ESTRUCTURA
    1. PARAMS — las perillas. Todas son DECISIÓN, no medición.
    2. Funciones PURAS (probadas en tests/test_radar_precios.py):
       contribucion_de · piso_por_clase · clasificar · referencia_de ·
       premio_calidad · direccion_de · redondear_precio.
    3. Capa SQL: arma el universo (un item por SKU) leyendo channel, enrich y
       costing. BLOQUEANTE: quien la llame desde una corrutina la manda a
       `asyncio.to_thread` (regla 11).
    4. `filtrar` (pura sobre el universo) y `detalle_de` (dos lecturas más).
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from services import costos

log = logging.getLogger("omnicanal.radar_precios")

CANAL = "mercado_libre"
ZONA = "America/Mexico_City"

# ═════════════════════════════════════════════════════════════════════════════
# 1. PARÁMETROS — cada uno es una DECISIÓN, no una medición. Viajan en la
#    respuesta (`parametros`) para que la pantalla los rotule como tales.
# ═════════════════════════════════════════════════════════════════════════════
PARAMS: dict[str, float | int] = {
    # decisión, no medición: IVA de México que se descuenta al precio cobrado.
    "iva": 0.16,
    # decisión, no medición: más de estos días de cobertura = pieza en exceso.
    "horizonte_exceso_dias": 180,
    # decisión, no medición: con menos cobertura que esto (clase normal) no se
    # baja: la pieza vale más vendida después que rematada hoy.
    "cobertura_min_para_bajar": 45,
    # decisión, no medición: contribución mínima (fracción del precio) de la
    # clase normal para fijar su piso.
    "m_seg_normal": 0.10,
    # decisión, no medición: contribución mínima de la clase recompra (lo que
    # hay que dejar para reponer).
    "margen_reposicion": 0.20,
    # decisión, no medición: cuánto más caro que el mercado se justifica por Full.
    "premio_full": 0.05,
    # decisión, no medición: cuánto más caro se justifica por experiencia verde.
    "premio_experiencia_verde": 0.03,
    # decisión, no medición: tope del premio sumado.
    "premio_tope": 0.10,
    # decisión, no medición: ±banda alrededor de la referencia que se considera
    # «en línea con el mercado».
    "banda_mantener": 0.05,
    # decisión, no medición: el movimiento máximo que se sugiere de una vez.
    "paso_max": 0.10,
    # decisión, no medición: rivales mínimos para que la mediana cuente.
    "n_min_referencia": 3,
    # decisión, no medición: antigüedad máxima de la captura de búsqueda.
    "frescura_referencia_dias": 45,
    # decisión, no medición: tasa de comisión cuando no hay ventas medidas.
    "comision_estimada": 0.175,
    # decisión, no medición: si el cuartil alto de los rivales vale más de N
    # veces el cuartil bajo, el término mezcla productos distintos y la mediana
    # no describe NUESTRO producto (28-sep: TEC-1626-BLN tenía rivales de $160
    # a $22,500 y salía «282 % arriba → bajar»).
    "dispersion_max": 3.0,
    # decisión, no medición: una brecha mayor contra la mediana casi siempre es
    # un término que no describe el producto, no un precio equivocado. Se pide
    # revisar el término en vez de sugerir precio.
    "brecha_max": 0.60,
    # decisión, no medición: cobrar al menos esto por debajo del precio de la
    # ficha se lee como promoción activa (ML exige ≥5 % para un descuento).
    "promocion_min": 0.05,
}

DIRECCION_TEXTO = {"subir": "subir", "bajar": "bajar", "mantener": "mantener",
                   "caro_justificado": "caro justificado",
                   "no_competir": "no competir en precio",
                   "sin_referencia": "sin referencia"}

# Los SKUs de clase «recompra» los decide el NEGOCIO; vacío hasta que lo diga.
RECOMPRA: set[str] = set()

# SKUs del piloto (Eduardo, 28-sep-2026): el radar muestra solo estos por ahora.
# SKU → número de contenedor (o None si no se sabe). El número solo se usa
# donde no existe costing.sku_contenedor (producción hoy); con la tabla, manda
# la tabla. Vacío = sin piloto: el listado muestra el universo completo.
PILOTO: dict[str, int | None] = {
    # Cinco casos distintos, todos estables (activos en Full, venden 6+ de 8
    # semanas, sin pausas en 45 días): _radar_f0/piloto_5_skus.md, fuera del repo.
    "HERR-0035-VER": 88,          # Estrella: calidad 93, experiencia verde
    "ORG-0781-AZL-ROS-VER": 73,   # Experiencia mala (roja) aunque ya es barato
    "TEC-1527-MUL": 50,           # Ficha débil (calidad 64) y promoción profunda
    "JUGU-0268-ROS": 74,          # Exceso en Full: ~193 días de Full
    "TEC-0961-BLN": 44,           # Dos cuentas: SANCOR a $99 vende todo, BEKURA a $199 nada
}

DIRECCIONES = ("subir", "bajar", "mantener", "caro_justificado", "no_competir",
               "sin_referencia")
CLASES = ("exceso", "normal", "recompra")

# Los cortes de columna de la tabla de envío de ML (tope de cada tramo de
# precio). El fee estimado es ESCALONADO por precio: la contribución brinca
# hacia abajo al cruzar cada tope, y la bisección del piso tiene que saberlo
# (ver `piso_por_clase`). Se lee de costos para no copiar la tabla.
_TOPES_TRAMO: tuple[float, ...] = tuple(getattr(costos, "_TRAMOS_PRECIO", ()))


def _p(params: dict | None) -> dict:
    return {**PARAMS, **(params or {})}


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _pesos(v: float | None) -> str:
    return "—" if v is None else f"${v:,.0f}"


# ═════════════════════════════════════════════════════════════════════════════
# 2. FUNCIONES PURAS
# ═════════════════════════════════════════════════════════════════════════════

def contribucion_de(precio: Any, *, comision_tasa: Any = None,
                    envio_real: Any = None, peso_kg: Any = None,
                    largo: Any = 0, ancho: Any = 0, alto: Any = 0,
                    devolucion_tasa_pct: Any = None,
                    params: dict | None = None) -> dict[str, Any]:
    """Contribución por pieza al precio `precio` (el COBRADO, con IVA).

        contribución = precio/(1+iva) − comisión − envío − full − publicidad
                       − devolución esperada

    · comisión: `comision_tasa` (medida en ventas: comision/precio_unitario)
      × precio → `real`; si no hay, `comision_estimada` × precio → `estimado`.
      La tasa real se midió sobre el precio CON IVA y aquí se aplica sobre el
      mismo precio, así la base es coherente con lo que ML reporta.
    · envío: `envio_real` (promedio por pieza medido) → `real`; si no, la tabla
      de `costos.calc_fee_envio_ml` con el peso efectivo de la pieza →
      `estimado`; sin peso → `sin_dato` y la contribución NO se calcula (un
      envío inventado la haría optimista sin avisar).
    · devolución: tasa × envío (el retorno cuesta ≈ lo que costó la ida). Sin
      tasa → `None` (sin dato ≠ 0) y no resta.
    · Full y publicidad: SIN DATO (None). Por eso la cifra es siempre una COTA
      SUPERIOR.

    `estado` = real (comisión y envío reales) · parcial (uno) · estimado.
    """
    P = _p(params)
    p = _num(precio)
    desglose: dict[str, Any] = {
        "precio_sin_iva": None, "comision": None, "comision_estado": None,
        "envio": None, "envio_estado": None, "full": None, "publicidad": None,
        "devolucion": None, "devolucion_tasa_pct": _num(devolucion_tasa_pct),
    }
    if p is None or p <= 0:
        return {"valor": None, "estado": "estimado", "desglose": desglose}

    precio_sin_iva = p / (1.0 + P["iva"])
    tasa = _num(comision_tasa)
    if tasa is not None and tasa >= 0:
        comision, com_estado = p * tasa, "real"
    else:
        comision, com_estado = p * P["comision_estimada"], "estimado"

    env = _num(envio_real)
    if env is not None and env >= 0:
        envio, env_estado = env, "real"
    else:
        kg = _num(peso_kg)
        if kg is not None and kg > 0:
            peso_ef, _ = costos._peso_efectivo(kg, _num(largo) or 0,
                                               _num(ancho) or 0, _num(alto) or 0)
            envio, env_estado = costos.calc_fee_envio_ml(peso_ef, p), "estimado"
        else:
            envio, env_estado = None, "sin_dato"

    tasa_dev = _num(devolucion_tasa_pct)
    devolucion = (tasa_dev / 100.0 * envio
                  if (tasa_dev is not None and envio is not None) else None)

    desglose.update({
        "precio_sin_iva": round(precio_sin_iva, 2),
        "comision": round(comision, 2), "comision_estado": com_estado,
        "envio": round(envio, 2) if envio is not None else None,
        "envio_estado": env_estado,
        "devolucion": round(devolucion, 2) if devolucion is not None else None,
    })
    reales = (com_estado == "real") + (env_estado == "real")
    estado = "real" if reales == 2 else ("parcial" if reales == 1 else "estimado")
    if envio is None:
        return {"valor": None, "estado": estado, "desglose": desglose}
    valor = precio_sin_iva - comision - envio - (devolucion or 0.0)
    return {"valor": round(valor, 2), "estado": estado, "desglose": desglose}


def _exigencia(clase: str, params: dict) -> float:
    """Fracción del precio que la contribución debe alcanzar en el piso."""
    if clase == "exceso":
        return 0.0
    if clase == "recompra":
        return float(params["margen_reposicion"])
    return float(params["m_seg_normal"])


def piso_por_clase(clase: str, *, params: dict | None = None,
                   **contrib_kwargs: Any) -> float | None:
    """El precio más bajo desde el cual la contribución cumple la exigencia de
    la clase — exceso ≥ 0 · normal ≥ m_seg_normal×precio · recompra ≥
    margen_reposicion×precio — hallado por BISECCIÓN.

    La contribución no es monótona: el envío estimado es escalonado por precio
    y brinca hacia arriba al cruzar cada tope de tramo (p. ej. $298.99 → $299).
    Por eso el predicado de la bisección pide que se cumpla en el precio Y en
    el arranque de cada tramo que le sigue; dentro de un tramo el envío es fijo
    y la contribución crece con el precio, así que esos arranques son los
    peores puntos. Con eso el predicado sí es monótono y el piso significa
    «de aquí para arriba, siempre cumple».

    `contrib_kwargs` son los de `contribucion_de` sin `precio`. Devuelve None
    si la contribución no se puede calcular (falta envío) o si ningún precio
    razonable cumple.
    """
    P = _p(params)
    req = _exigencia(clase, P)
    arranques = [t + 0.01 for t in _TOPES_TRAMO]

    def ok(x: float) -> bool:
        c = contribucion_de(x, params=P, **contrib_kwargs)["valor"]
        return c is not None and c >= req * x - 1e-9

    def ok_desde(x: float) -> bool:
        # Arriba del último arranque el envío ya no cambia: basta con x.
        return ok(x) and all(ok(a) for a in arranques if a > x)

    hi = 100.0
    while not ok_desde(hi):
        hi *= 2
        if hi > 1e7:
            return None
    lo = 0.01
    if ok_desde(lo):
        return lo
    for _ in range(60):
        mid = (lo + hi) / 2
        if ok_desde(mid):
            hi = mid
        else:
            lo = mid
        if hi - lo < 0.005:
            break
    return round(math.ceil(hi * 100) / 100, 2)


def clasificar(sku: str, *, unidades_90d: Any, stock: Any,
               cobertura_dias: Any, recompra: Iterable[str] | None = None,
               params: dict | None = None) -> str:
    """exceso · recompra · normal, en ese orden de precedencia.

    exceso: 0 unidades en 90 días con stock > 0, o cobertura > horizonte.
    recompra: el SKU está en `recompra` (por defecto RECOMPRA, vacío).
    """
    P = _p(params)
    u = _num(unidades_90d) or 0.0
    s = _num(stock) or 0.0
    cob = _num(cobertura_dias)
    if (u <= 0 and s > 0) or (cob is not None and cob > P["horizonte_exceso_dias"]):
        return "exceso"
    conjunto = {x.upper() for x in (RECOMPRA if recompra is None else recompra)}
    if (sku or "").upper() in conjunto:
        return "recompra"
    return "normal"


def _utc(ts: Any) -> datetime | None:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str) and ts:
        try:
            return _utc(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def referencia_de(resultados: Iterable[dict], *, termino: str | None,
                  ahora: datetime | None = None,
                  params: dict | None = None) -> dict[str, Any]:
    """La referencia de mercado: MEDIANA del precio de los resultados de
    búsqueda del término del SKU, sin los nuestros, capturados hace ≤
    frescura_referencia_dias, con al menos n_min_referencia rivales.

    `resultados`: [{precio, capturado_en, es_nuestro}]. Siempre devuelve las
    mismas llaves; sin referencia, `precio` es None y `motivo` dice por qué:
    sin_termino · pocos_rivales · captura_vieja.
    """
    P = _p(params)
    ahora = _utc(ahora) or datetime.now(timezone.utc)
    base = {"precio": None, "fuente": None, "n": 0, "termino": termino or None,
            "capturado_en": None, "motivo": None, "n_total": 0, "mediana_dudosa": None}
    if not (termino or "").strip():
        return {**base, "motivo": "sin_termino"}
    rivales = [r for r in resultados
               if (_num(r.get("precio")) or 0) > 0 and not r.get("es_nuestro")]
    limite = ahora - timedelta(days=float(P["frescura_referencia_dias"]))
    frescos = [r for r in rivales
               if (_utc(r.get("capturado_en")) or datetime.min.replace(tzinfo=timezone.utc)) >= limite]
    fechas = [f for f in (_utc(r.get("capturado_en")) for r in rivales) if f]
    ultima = max(fechas).isoformat() if fechas else None
    base.update({"n_total": len(rivales), "capturado_en": ultima})
    n_min = int(P["n_min_referencia"])
    if len(frescos) >= n_min:
        precios = sorted(float(r["precio"]) for r in frescos)
        med = statistics.median(precios)
        q1, _, q3 = statistics.quantiles(precios, n=4, method="inclusive")
        if q1 > 0 and q3 / q1 > float(P["dispersion_max"]):
            # Sin referencia, pero la mediana viaja para que se vea POR QUÉ.
            return {**base, "n": len(frescos), "motivo": "rivales_dispersos",
                    "mediana_dudosa": round(med, 2)}
        return {**base, "precio": round(med, 2), "fuente": "busqueda",
                "n": len(frescos)}
    if len(rivales) >= n_min:
        return {**base, "n": len(frescos), "motivo": "captura_vieja"}
    return {**base, "n": len(frescos), "motivo": "pocos_rivales"}


def premio_calidad(*, full: bool, experiencia: str | None, clase: str,
                   params: dict | None = None) -> float:
    """min(tope, premio_full·[Full] + premio_verde·[experiencia verde]). 0 en exceso."""
    P = _p(params)
    if clase == "exceso":
        return 0.0
    premio = (P["premio_full"] if full else 0.0) + \
             (P["premio_experiencia_verde"] if experiencia == "verde" else 0.0)
    return round(min(float(P["premio_tope"]), premio), 4)


def redondear_precio(precio: Any, piso: Any = None) -> int | None:
    """Al entero con terminación 9 más cercano (1,166 → 1,169; 1,163 → 1,159),
    nunca bajo el piso: si cae abajo, el primer …9 que lo alcanza."""
    x = _num(precio)
    if x is None or x <= 0:
        return None
    r = int(math.floor((x + 1) / 10 + 0.5) * 10 - 1)
    if r < 9:
        r = 9
    pi = _num(piso)
    if pi is not None and r < pi:
        r = int(math.ceil((pi + 1) / 10) * 10 - 1)
        if r < pi:
            r += 10
    return r


_MOTIVO_SIN_REF = {
    "sin_termino": "sin término de búsqueda asignado",
    "pocos_rivales": "menos de {n_min} rivales en la búsqueda",
    "captura_vieja": "la captura de búsqueda tiene más de {dias} días",
    "rivales_dispersos": "los rivales de la búsqueda varían demasiado de precio: "
                         "el término mezcla productos (revisar el término)",
}


def direccion_de(*, precio: Any, referencia: Any, premio: float = 0.0,
                 piso: Any = None, clase: str = "normal",
                 cobertura_dias: Any = None, n_rivales: int | None = None,
                 motivo_sin_ref: str | None = None,
                 params: dict | None = None) -> dict[str, Any]:
    """La dirección del precio con la cuenta principal (p = precio cobrado).

    1. sin referencia → sin_referencia.
    2. techo = ref×(1+premio). p > ref×(1+banda) y p > techo:
       piso > techo → no_competir; normal con cobertura < mínima → mantener;
       si no → bajar a max(techo, p×(1−paso), piso).
    3. p > ref×(1+banda) y p ≤ techo → caro_justificado.
    4. p < ref×(1−banda): exceso → mantener; si no → subir a min(techo, p×(1+paso)).
    5. si no → mantener.
    """
    P = _p(params)
    p = _num(precio)
    ref = _num(referencia)
    pi = _num(piso)
    cob = _num(cobertura_dias)
    banda, paso = float(P["banda_mantener"]), float(P["paso_max"])
    out = {"direccion": "sin_referencia", "precio_sugerido": None, "techo": None,
           "posicion_pct": None, "razones": []}

    if ref is None or ref <= 0 or p is None or p <= 0:
        plantilla = _MOTIVO_SIN_REF.get(motivo_sin_ref or "", "sin referencia de mercado")
        out["razones"] = [plantilla.format(n_min=int(P["n_min_referencia"]),
                                           dias=int(P["frescura_referencia_dias"]))]
        if p is None or p <= 0:
            out["razones"] = ["sin precio cobrado"]
        return out

    techo = ref * (1 + premio)
    pos = (p / ref - 1) * 100
    out["techo"] = round(techo, 2)
    out["posicion_pct"] = round(pos, 1)
    rivales = f" de {n_rivales} rivales" if n_rivales else ""
    donde = (f"{abs(pos):.1f} % {'arriba' if pos >= 0 else 'abajo'} de la mediana"
             f"{rivales} ({_pesos(ref)})")
    razones = [donde]
    if abs(pos) > float(P["brecha_max"]) * 100:
        # La posición se sigue mostrando; la dirección no, porque lo más
        # probable es que los rivales no sean el mismo producto.
        out["techo"] = None
        out["razones"] = [donde, "brecha demasiado grande: los rivales probablemente "
                                 "no son el mismo producto (revisar el término)"]
        return out
    if pi is None:
        razones.append("sin piso: falta el envío (peso de la pieza)")

    if p > ref * (1 + banda) and p > techo:
        if pi is not None and pi > techo:
            out.update(direccion="no_competir", razones=razones + [
                f"igualar al mercado baja del piso ({_pesos(pi)} > techo {_pesos(techo)})"])
            return out
        if clase == "normal" and cob is not None and cob < P["cobertura_min_para_bajar"]:
            out.update(direccion="mantener", razones=razones + [
                f"cobertura corta ({cob:.0f} días < {int(P['cobertura_min_para_bajar'])}): "
                "la pieza vale más después"])
            return out
        objetivo = max(techo, p * (1 - paso), pi or 0.0)
        sug = redondear_precio(objetivo, pi)
        if objetivo == p * (1 - paso) and objetivo > techo:
            razones.append(f"bajada topada al {paso * 100:.0f} % por paso")
        if pi is not None and objetivo == pi:
            razones.append(f"el piso ({_pesos(pi)}) no deja bajar más")
        out.update(direccion="bajar", precio_sugerido=sug, razones=razones)
        return out

    if p > ref * (1 + banda):
        out.update(direccion="caro_justificado", razones=razones + [
            f"premio de calidad {premio * 100:.0f} %: techo {_pesos(techo)}"])
        return out

    if p < ref * (1 - banda):
        if clase == "exceso":
            out.update(direccion="mantener", razones=razones + [
                "en exceso: no se sube, conviene mover piezas"])
            return out
        objetivo = min(techo, p * (1 + paso))
        sug = redondear_precio(objetivo, pi)
        if objetivo == p * (1 + paso) and objetivo < techo:
            razones.append(f"subida topada al {paso * 100:.0f} % por paso")
        out.update(direccion="subir", precio_sugerido=sug, razones=razones)
        return out

    out.update(direccion="mantener", razones=razones + [
        f"dentro de ±{banda * 100:.0f} % del mercado"])
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 3. CAPA SQL — BLOQUEANTE (psycopg2). Desde una corrutina: asyncio.to_thread.
#    Solo SELECT. Sin set_session ni SET SESSION (regla 13).
# ═════════════════════════════════════════════════════════════════════════════

# Nuestras publicaciones ACTIVAS de ML. El criterio de «activa» NO se escribe
# aquí: sale de publicaciones_panel.filtro_sql_activas (situacion='active').
_SQL_PUBS = """
select l.sku::text as sku, a.legacy_code as cuenta, l.listing_id as item_id,
       l.price, l.price_sale, l.price_base, l.is_fulfillment, l.logistic_type,
       l.stock_own, l.stock_full, l.category_id
  from channel.listings l
  join core.accounts a on a.id = l.account_id
 where l.canal = 'mercado_libre'
   and nullif(l.listing_id, '') is not null
   and l.sku is not null
   and a.legacy_code is not null
   and {activas}
"""

# Precio COBRADO (price_sale, desde la 0042), visitas y unidades a 30 días.
_SQL_VISTA = """
select sku::text as sku, cuenta, ml_item_id as item_id, titulo, precio,
       precio_lista, visitas_30d, unidades_30d, periodo
  from enrich.market_publicaciones_v
 where canal = 'mercado_libre'
   and ml_item_id = any(%(items)s)
"""

# Stock compartido: Woo es la fuente y el resto espejo → max(), no sum().
# Full vive aparte (bodega de ML): por cuenta el max de stock_full, sumado.
# La forma de competencia_supabase.stock_por_sku (v0.591.0): un JOIN contra la
# lista y una sola pasada por channel.listings. La anterior (subconsulta
# correlacionada por SKU + dos `any()`) daba lo mismo pero recorría la tabla
# dos veces y resolvía el Full fila por fila.
_SQL_STOCK = """
with s(sku) as (select distinct unnest(%(skus)s::citext[])),
l as (select l.sku, l.account_id, l.canal, l.is_fulfillment,
             l.stock_own, l.stock_full
        from channel.listings l join s on l.sku = s.sku),
fa as (select sku, account_id, max(coalesce(stock_full, 0)) as sf
         from l where canal = 'mercado_libre' and is_fulfillment
        group by 1, 2),
f as (select sku, sum(sf) as sf from fa group by 1)
select l.sku::text as sku, max(l.stock_own) as propio,
       coalesce(max(f.sf), 0) as full_
  from l left join f on f.sku = l.sku
 group by l.sku
"""

# Velocidad a 90 días, todos los canales (el stock propio es compartido). Por
# SKU **o por item de ML**: la venta se registra con el seller_sku de la ORDEN,
# que a veces no es el de channel.listings (medido en el sandbox el 28-sep:
# SIL-0008-NEG vende como «SIL-008-NEG»; 17 de 524 SKUs salían con 0 ventas
# vendiendo). Cada fila se asigna a UN solo SKU en Python (`asignar_ventas`),
# así no se cuenta dos veces.
_SQL_VENTAS = """
select sku::text as sku, item_id, date, sum(units_sold) as unidades
  from channel.sales_daily_completa
 where date >= (now() at time zone 'America/Mexico_City')::date - 90
   and (sku = any(%(skus)s::citext[]) or item_id = any(%(items)s))
 group by 1, 2, 3
"""

# Comisión REAL por (cuenta, item) a 60 días. `order_items.comision` es el
# `sale_fee` de ML × cantidad (pedidos_ml.py) y `precio_unitario` el
# `unit_price` que paga el comprador, CON IVA. Medido en el sandbox el
# 28-sep-2026: comision/(precio_unitario×cantidad) ≈ pct_comision de
# costos_finales (razón mediana 0.997) → la comisión de ML se calcula sobre el
# precio CON IVA. Si además trae el IVA de la propia comisión no se puede
# afirmar desde estos datos; se usa tal como ML la cobra.
_SQL_COMISION = """
select oi.cuenta, oi.item_id,
       sum(oi.comision) as comision,
       sum(oi.precio_unitario * oi.cantidad) as venta,
       count(*) as lineas
  from channel.order_items oi
  join channel.orders o
    on o.canal = oi.canal and o.cuenta = oi.cuenta
   and o.external_order_id = oi.external_order_id
 where oi.canal = 'mercado_libre'
   and o.creado_at >= now() - interval '60 days'
   and coalesce(o.estado_canal, '') <> 'cancelled'
   and oi.comision > 0 and oi.precio_unitario > 0 and oi.cantidad > 0
   and oi.item_id = any(%(items)s)
 group by 1, 2
"""

# Envío REAL por pieza, por (cuenta, item) a 60 días: lo que pagó el vendedor
# (`enrich.order_shipping_cost.costo_vendedor`) en órdenes de UN solo item —con
# varios no hay forma honesta de repartir el envío—, entre las piezas. Un 0 aquí
# es real (el comprador pagó el envío), no un hueco.
_SQL_ENVIO = """
with o1 as (
  select oi.cuenta, oi.external_order_id, min(oi.item_id) as item_id,
         sum(oi.cantidad) as piezas
    from channel.order_items oi
    join channel.orders o
      on o.canal = oi.canal and o.cuenta = oi.cuenta
     and o.external_order_id = oi.external_order_id
   where oi.canal = 'mercado_libre'
     and o.creado_at >= now() - interval '60 days'
     and coalesce(o.estado_canal, '') <> 'cancelled'
   group by 1, 2
  having count(distinct oi.item_id) = 1
)
select o1.cuenta, o1.item_id, sum(s.costo_vendedor) as costo,
       sum(o1.piezas) as piezas, count(*) as ordenes
  from o1
  join enrich.order_shipping_cost s
    on s.cuenta = o1.cuenta and s.external_order_id = o1.external_order_id
 where s.costo_vendedor is not null and o1.piezas > 0
   and o1.item_id = any(%(items)s)
 group by 1, 2
"""

# Tasa de devolución a 60 días (piezas). NO `costo_devolucion`: sale 0 cuando
# el dinero es NULL, y sin dato ≠ 0.
_SQL_DEVOL = """
select sku::text as sku, tasa_pct
  from channel.returns_rate_sku
 where canal = 'mercado_libre' and sku = any(%(skus)s::citext[])
"""

_SQL_SALUD = """
select a.legacy_code as cuenta, h.listing_id as item_id, h.metrica, h.estado,
       h.valor, h.nivel
  from enrich.listing_health h
  join core.accounts a on a.id = h.account_id
 where h.canal = 'mercado_libre'
   and h.metrica in ('calidad', 'experiencia')
   and h.listing_id = any(%(items)s)
"""

# costing.sku_contenedor (0060) solo existe en el sandbox: se consulta si la
# tabla existe. El más antiguo (N menor) es la etiqueta. Sin la tabla, el
# contenedor sale de PILOTO (`contenedores_piloto`) y el resto queda en null.
_SQL_CONT = """
select sku::text as sku, min(numero) as numero, count(*) as n,
       bool_or(multi) as multi
  from costing.sku_contenedor
 where sku = any(%(skus)s::citext[])
 group by 1
"""

_SQL_PESO = """
select sku::text as sku, peso, largo, ancho, alto
  from costing.costos_validados
 where sku = any(%(skus)s::citext[])
"""

# La misma unión de publicaciones_panel._SQL_MERCADO (config → término →
# resultados), pero FILA POR FILA: la frescura, el mínimo de rivales y la
# mediana los decide `referencia_de`, que es pura y tiene pruebas.
_SQL_REF = """
select cfg.sku::text as sku, st.termino, r.precio, r.capturado_en,
       coalesce(r.es_nuestro, false) as es_nuestro
  from enrich.market_sku_config cfg
  left join enrich.market_search_term st on st.id = cfg.termino_id
  left join enrich.market_search_results r
         on r.termino_id = cfg.termino_id and r.precio > 0
 where cfg.sku = any(%(skus)s::citext[])
   and cfg.canal = 'mercado_libre'
"""

_SQL_COMPARABLES = """
select r.posicion, r.precio, r.seller, r.rating, r.visitas_30d,
       coalesce(r.es_nuestro, false) as es_nuestro, r.capturado_en, r.titulo
  from enrich.market_sku_config cfg
  join enrich.market_search_results r on r.termino_id = cfg.termino_id
 where cfg.sku = %(sku)s::citext and cfg.canal = 'mercado_libre'
   and r.precio > 0
 order by r.posicion nulls last, r.precio
"""

# Misma regla que _SQL_VENTAS (por SKU o por sus items de ML) y el mismo
# reparto en Python (`asignar_ventas`), para que la serie sume lo que la lista.
_SQL_SERIE = """
select sku::text as sku, item_id, date, sum(units_sold) as unidades
  from channel.sales_daily_completa
 where (sku = %(sku)s::citext or item_id = any(%(items)s))
   and date >= (now() at time zone 'America/Mexico_City')::date - 90
 group by 1, 2, 3
"""

# Contexto (NUNCA referencia): la mediana del top de la categoría, de su
# última captura.
_SQL_CATEGORIA = """
with b as (
  select precio, capturado_en,
         max(capturado_en) over () as ultima
    from enrich.market_bestsellers
   where canal = 'mercado_libre' and categoria_id = %(cat)s
     and precio > 0 and not coalesce(es_nuestro, false)
)
select percentile_cont(0.5) within group (order by precio) as mediana,
       count(*) as n, max(ultima) as capturado_en
  from b where capturado_en >= ultima - interval '1 day'
"""


def _zona_mx():
    """La zona de CDMX; sin tzdata (Windows), UTC−6 fijo: CDMX no tiene
    horario de verano desde 2022."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(ZONA)
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=-6))


def _sdb():
    from services import supabase_db as sdb
    return sdb


def tabla_existe(nombre: str) -> bool:
    """to_regclass: ¿existe la tabla/vista en ESTA base? Sin error si no."""
    try:
        return _sdb().fetch_scalar("select to_regclass(%s) is not null", (nombre,)) is True
    except Exception as exc:  # noqa: BLE001
        log.warning("radar: to_regclass(%s) falló: %s", nombre, exc)
        return False


def depurar_padres(pubs: list[dict]) -> tuple[list[dict], int]:
    """PURA. Quita la fila del SKU PADRE de un item de ML con variaciones.

    En channel.listings un item con variaciones trae una fila por SKU hijo y,
    a veces, otra con el SKU padre (el prefijo: DEC-0014 junto a DEC-0014-BLN,
    con el mismo listing_id). La del padre es la MISMA publicación contada dos
    veces —medido en el sandbox el 28-sep: 20 de los 23 items activos con más
    de un SKU—, y trae stock basura (24,120 en DEC-0014). Se quita solo la
    fila de ESE item; si el padre tiene otra publicación propia, sigue.

    Devuelve (filas depuradas, cuántas se quitaron)."""
    por_item: dict[str, set[str]] = {}
    for f in pubs:
        por_item.setdefault(str(f["item_id"]), set()).add(str(f["sku"]).upper())
    padres = {(item, s) for item, ss in por_item.items() if len(ss) > 1
              for s in ss if any(o != s and o.startswith(s) for o in ss)}
    fuera = [f for f in pubs if (str(f["item_id"]), str(f["sku"]).upper()) in padres]
    return [f for f in pubs if (str(f["item_id"]), str(f["sku"]).upper()) not in padres], len(fuera)


def skus_piloto(piloto: dict[str, Any] | Iterable[str] | None = None) -> list[str]:
    """PURA. Los SKUs del piloto en MAYÚSCULAS, sin repetidos ni vacíos, en el
    orden en que se escribieron. `None` = el PILOTO del módulo."""
    fuente = PILOTO if piloto is None else piloto
    out: list[str] = []
    for s in fuente:
        k = str(s or "").strip().upper()
        if k and k not in out:
            out.append(k)
    return out


def contenedores_piloto(piloto: dict[str, Any] | None = None) -> dict[str, dict]:
    """PURA. {SKU: fila con la forma de _SQL_CONT} para los SKUs del piloto que
    traen número. Es la fuente del contenedor SOLO cuando costing.sku_contenedor
    no existe: un número escrito a mano, uno por SKU, así que nunca es multi."""
    fuente = PILOTO if piloto is None else piloto
    out: dict[str, dict] = {}
    for s, n in fuente.items():
        k = str(s or "").strip().upper()
        if not k or n is None:
            continue
        try:
            numero = int(str(n).strip().upper().removeprefix("C-"))
        except ValueError:
            # Un número mal escrito no tumba el radar: ese SKU sale sin contenedor.
            log.warning("radar: contenedor del piloto ilegible para %s: %r", k, n)
            continue
        out[k] = {"numero": numero, "n": 1, "multi": False, "fuente": "piloto"}
    return out


def asignar_ventas(filas: Iterable[dict], item_a_sku: dict[str, str],
                   skus: set[str]) -> dict[str, float]:
    """PURA. Unidades por SKU nuestro. Cada fila cuenta para el SKU de su item
    de ML cuando el item es nuestro y de UN solo SKU (cubre la venta registrada
    con otro seller_sku); si no, para su propio SKU si es nuestro; si no, para
    nadie. Una fila nunca cuenta dos veces. `item_a_sku` trae solo los items
    inequívocos (ver `construir_universo`)."""
    out: dict[str, float] = {}
    for f in filas:
        dueno = item_a_sku.get(str(f.get("item_id") or ""))
        if dueno is None:
            s = str(f.get("sku") or "").upper()
            dueno = s if s in skus else None
        if dueno is not None:
            out[dueno] = out.get(dueno, 0.0) + (_num(f.get("unidades")) or 0.0)
    return out


def _experiencia(fila: dict | None) -> str | None:
    if fila is None:
        return None
    if (fila.get("estado") or "") != "medida":
        return "sin_datos"
    return {"bueno": "verde", "medio": "amarilla", "malo": "roja"}.get(
        fila.get("nivel") or "", "sin_datos")


def _mejor_vista(filas: list[dict]) -> dict[tuple[str, str], dict]:
    """(cuenta, item) → la fila del último periodo (corte de mes: dos filas)."""
    out: dict[tuple[str, str], dict] = {}
    for f in filas:
        k = (str(f["cuenta"]).upper(), str(f["item_id"]))
        prev = out.get(k)
        if prev is None or (f.get("periodo") or date.min) > (prev.get("periodo") or date.min):
            out[k] = f
    return out


def construir_universo(*, ahora: datetime | None = None,
                       params: dict | None = None,
                       recompra: Iterable[str] | None = None,
                       piloto: dict[str, int | None] | None = None) -> dict[str, Any]:
    """BLOQUEANTE. Un item por SKU con publicación activa de ML, ya con
    contribución, clase, piso, referencia y dirección. Solo SELECT.

    Siempre el universo COMPLETO: el recorte del piloto lo hace `filtrar`.
    `piloto` (None = PILOTO) solo aporta el contenedor cuando no hay tabla."""
    from services import publicaciones_panel as pp
    sdb = _sdb()
    P = _p(params)
    ahora = _utc(ahora) or datetime.now(timezone.utc)

    activas = pp.filtro_sql_activas(CANAL, alias="l")
    if activas is None:  # pragma: no cover — ML sí decide por `situacion`
        raise RuntimeError("publicaciones_panel no sabe qué es una activa de ML")
    donde, p_act = activas
    pubs, padres_fuera = depurar_padres(
        sdb.fetch_all(_SQL_PUBS.format(activas=donde), p_act))

    tablas = {t: tabla_existe(t) for t in (
        "channel.returns_rate_sku", "enrich.listing_health",
        "costing.sku_contenedor", "enrich.order_shipping_cost")}

    skus = sorted({f["sku"].upper() for f in pubs})
    items = sorted({str(f["item_id"]) for f in pubs})
    vacio = {"generado_en": ahora.isoformat(), "items": [], "tablas": tablas,
             "parametros": P, "recompra_n": len(RECOMPRA if recompra is None else recompra),
             "notas": {"filas_padre_descartadas": padres_fuera}}
    if not skus:
        return vacio

    vista = _mejor_vista(sdb.fetch_all(_SQL_VISTA, {"items": items}))
    stock = {f["sku"].upper(): f for f in sdb.fetch_all(_SQL_STOCK, {"skus": skus})}
    skus_de_item: dict[str, set[str]] = {}
    for f in pubs:
        skus_de_item.setdefault(str(f["item_id"]), set()).add(f["sku"].upper())
    item_a_sku = {i: next(iter(ss)) for i, ss in skus_de_item.items() if len(ss) == 1}
    ventas = asignar_ventas(sdb.fetch_all(_SQL_VENTAS, {"skus": skus, "items": items}),
                            item_a_sku, set(skus))
    comis = {(f["cuenta"].upper(), str(f["item_id"])): f
             for f in sdb.fetch_all(_SQL_COMISION, {"items": items})}
    envios = ({(f["cuenta"].upper(), str(f["item_id"])): f
               for f in sdb.fetch_all(_SQL_ENVIO, {"items": items})}
              if tablas["enrich.order_shipping_cost"] else {})
    devol = ({f["sku"].upper(): _num(f["tasa_pct"])
              for f in sdb.fetch_all(_SQL_DEVOL, {"skus": skus})}
             if tablas["channel.returns_rate_sku"] else {})
    salud: dict[tuple[str, str, str], dict] = {}
    if tablas["enrich.listing_health"]:
        for f in sdb.fetch_all(_SQL_SALUD, {"items": items}):
            salud[(f["cuenta"].upper(), str(f["item_id"]), f["metrica"])] = f
    cont = ({f["sku"].upper(): {**f, "fuente": "tabla"}
             for f in sdb.fetch_all(_SQL_CONT, {"skus": skus})}
            if tablas["costing.sku_contenedor"] else contenedores_piloto(piloto))
    peso = {f["sku"].upper(): f for f in sdb.fetch_all(_SQL_PESO, {"skus": skus})}
    ref_filas: dict[str, list[dict]] = {}
    terminos: dict[str, str | None] = {}
    for f in sdb.fetch_all(_SQL_REF, {"skus": skus}):
        k = f["sku"].upper()
        terminos[k] = terminos.get(k) or f.get("termino")
        if f.get("precio") is not None:
            ref_filas.setdefault(k, []).append(f)

    por_sku: dict[str, list[dict]] = {}
    for f in pubs:
        por_sku.setdefault(f["sku"].upper(), []).append(f)

    salida = []
    for sku, filas in por_sku.items():
        salida.append(_armar_item(
            sku, filas, vista=vista, stock=stock.get(sku), u90=ventas.get(sku, 0.0),
            comis=comis, envios=envios, tasa_dev=devol.get(sku), salud=salud,
            cont=cont.get(sku), peso=peso.get(sku), termino=terminos.get(sku),
            ref_filas=ref_filas.get(sku, []), ahora=ahora, params=P,
            recompra=recompra, skus_de_item=skus_de_item))
    salida.sort(key=_clave_orden)
    return {**vacio, "items": salida, "_item_a_sku": item_a_sku}


def _armar_item(sku: str, filas: list[dict], *, vista, stock, u90, comis, envios,
                tasa_dev, salud, cont, peso, termino, ref_filas, ahora, params,
                recompra, skus_de_item=None) -> dict[str, Any]:
    P = params
    pz = peso or {}
    contrib_base = {"peso_kg": pz.get("peso"), "largo": pz.get("largo"),
                    "ancho": pz.get("ancho"), "alto": pz.get("alto"),
                    "devolucion_tasa_pct": tasa_dev}

    # Una entrada por cuenta. Gemelas (dos items del mismo SKU en una cuenta):
    # se queda el que más vende (empate → más visitas) y se anotan los demás.
    por_cuenta: dict[str, list[dict]] = {}
    for f in filas:
        cuenta = str(f["cuenta"]).upper()
        v = vista.get((cuenta, str(f["item_id"]))) or {}
        cobrado = _num(v.get("precio")) or _num(f.get("price_sale")) or _num(f.get("price"))
        lista = _num(v.get("precio_lista")) or _num(f.get("price_base")) or _num(f.get("price"))
        c = comis.get((cuenta, str(f["item_id"])))
        tasa = (float(c["comision"]) / float(c["venta"])
                if c and _num(c.get("venta")) else None)
        e = envios.get((cuenta, str(f["item_id"])))
        env_real = (float(e["costo"]) / float(e["piezas"])
                    if e and _num(e.get("piezas")) else None)
        full = bool(f.get("is_fulfillment")) or (f.get("logistic_type") == "fulfillment")
        cal = salud.get((cuenta, str(f["item_id"]), "calidad"))
        exp = salud.get((cuenta, str(f["item_id"]), "experiencia"))
        contrib = contribucion_de(cobrado, comision_tasa=tasa, envio_real=env_real,
                                  params=P, **contrib_base)
        por_cuenta.setdefault(cuenta, []).append({
            "cuenta": cuenta, "item_id": str(f["item_id"]),
            "precio": lista, "precio_cobrado": cobrado, "full": full,
            "experiencia": _experiencia(exp),
            "calidad": (int(round(float(cal["valor"])))
                        if cal and _num(cal.get("valor")) is not None else None),
            "visitas_30d": v.get("visitas_30d"), "unidades_30d": v.get("unidades_30d"),
            "contribucion": contrib,
            "_titulo": v.get("titulo"), "_categoria": f.get("category_id"),
            "_tasa": tasa, "_env_real": env_real,
        })

    def _peso_venta(c: dict) -> tuple:
        return (c.get("unidades_30d") or 0, c.get("visitas_30d") or 0)

    cuentas = []
    for cuenta, lst in por_cuenta.items():
        lst.sort(key=_peso_venta, reverse=True)
        elegida = dict(lst[0])
        if len(lst) > 1:
            elegida["gemelas"] = [x["item_id"] for x in lst[1:]]
        cuentas.append(elegida)
    cuentas.sort(key=_peso_venta, reverse=True)
    principal = cuentas[0]

    st = stock or {}
    propio = int(_num(st.get("propio")) or 0)
    en_full = int(_num(st.get("full_")) or 0)
    total_stock = max(propio, 0) + max(en_full, 0)
    ventas_dia = (u90 or 0.0) / 90.0
    cobertura = (round(total_stock / ventas_dia) if ventas_dia > 0
                 else (0 if total_stock <= 0 else None))
    clase = clasificar(sku, unidades_90d=u90, stock=total_stock,
                       cobertura_dias=cobertura, recompra=recompra, params=P)

    ref = referencia_de(ref_filas, termino=termino, ahora=ahora, params=P)
    premio = premio_calidad(full=principal["full"], experiencia=principal["experiencia"],
                            clase=clase, params=P)
    piso = piso_por_clase(clase, params=P, comision_tasa=principal["_tasa"],
                          envio_real=principal["_env_real"], **contrib_base)
    dire = direccion_de(precio=principal["precio_cobrado"], referencia=ref["precio"],
                        premio=premio, piso=piso, clase=clase, cobertura_dias=cobertura,
                        n_rivales=ref["n"], motivo_sin_ref=ref["motivo"], params=P)
    # EN PROMOCIÓN: ML cobra menos que el precio de la ficha. Su regla (F0,
    # _radar_f0/F0_politicas.md): subir el precio de la ficha quita el
    # descuento y bajarlo por debajo del promocional elimina la promoción. En el
    # sandbox del 23-sep eran 324 de 505 SKUs (64 %): es la forma normal de
    # vender, así que el radar NO se calla — el precio sugerido es el COBRADO y
    # se avisa que el cambio va por la promoción, nunca por la ficha.
    lista, cobrado = _num(principal.get("precio")), _num(principal.get("precio_cobrado"))
    en_promocion = bool(lista and cobrado and cobrado < lista * (1 - float(P["promocion_min"])))
    if en_promocion and dire["precio_sugerido"]:
        dire = {**dire, "razones": [
            f"en promoción de ML (cobra {_pesos(cobrado)} de {_pesos(lista)}): el cambio va "
            "por la promoción, no por el precio de la ficha (moverla la quita)"]
            + dire["razones"]}
    razones = list(dire["razones"])
    if principal.get("gemelas"):
        razones.append(f"{len(principal['gemelas']) + 1} publicaciones gemelas en "
                       f"{principal['cuenta']}: se toma la que más vende")
    if cobertura is None and total_stock > 0:
        razones.append("sin ventas en 90 días")
    # Un item de ML con DOS SKUs que no son padre e hijo (en el sandbox: 3, p. ej.
    # SIL-0008-NEG y SIL-008-NEG en MLM4781717282). No se adivina cuál es el
    # bueno: cada venta cuenta para el SKU con que se registró y aquí se avisa.
    comparte = sorted({o for f in filas for o in (skus_de_item or {}).get(str(f["item_id"]), set())
                       if o != sku})
    if comparte:
        razones.append(f"comparte publicación con {', '.join(comparte)}")

    titulo = next((c["_titulo"] for c in cuentas if c.get("_titulo")), None)
    categoria = principal.get("_categoria")
    publicas = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cuentas]
    contenedor = str(cont["numero"]) if cont and cont.get("numero") is not None else None
    multi = bool(cont and ((cont.get("n") or 0) > 1 or cont.get("multi")))

    return {
        "sku": sku, "titulo": titulo, "contenedor": contenedor,
        "contenedor_multi": multi,
        # tabla (costing.sku_contenedor) · piloto (número escrito en PILOTO) · None
        "contenedor_fuente": (cont or {}).get("fuente") if contenedor else None,
        "clase": clase,
        "cuenta_principal": principal["cuenta"], "cuentas": publicas,
        "referencia": {k: ref[k] for k in ("precio", "fuente", "n", "termino",
                                           "capturado_en", "motivo", "n_total",
                                           "mediana_dudosa")},
        "en_promocion": en_promocion,
        "posicion_pct": dire["posicion_pct"],
        "contribucion": principal["contribucion"]["valor"],
        "contribucion_estado": principal["contribucion"]["estado"],
        "piso": piso, "techo": dire["techo"],
        "premio_calidad_pct": round(premio * 100, 1),
        "stock": total_stock, "stock_detalle": {"propio": propio, "full": en_full},
        "ventas_dia": round(ventas_dia, 2), "cobertura_dias": cobertura,
        "direccion": dire["direccion"], "precio_sugerido": dire["precio_sugerido"],
        "razones": razones, "comparte_item_con": comparte,
        "_categoria": categoria,
    }


def _clave_orden(it: dict) -> tuple:
    """Piezas en stock × contribución, descendente; sin contribución al final."""
    c = it.get("contribucion")
    if c is None:
        return (1, 0.0, it["sku"])
    return (0, -(it.get("stock") or 0) * c, it["sku"])


# ═════════════════════════════════════════════════════════════════════════════
# 4. RESPUESTAS
# ═════════════════════════════════════════════════════════════════════════════

def _publico(it: dict) -> dict:
    return {k: v for k, v in it.items() if not k.startswith("_")}


def filtrar(universo: dict, *, cuenta: str | None = None, clase: str | None = None,
            direccion: str | None = None, contenedor: str | None = None,
            q: str | None = None, limite: int = 200, pagina: int = 1,
            ambiente: str | None = None, todos: bool = False,
            piloto: dict[str, Any] | Iterable[str] | None = None) -> dict[str, Any]:
    """PURA. La respuesta de GET /api/radar-precios sobre un universo ya armado.

    Los conteos por dirección se calculan con TODOS los filtros menos el de
    dirección (las tarjetas son el filtro de dirección).

    PILOTO: si hay SKUs de piloto (`piloto`, None = PILOTO) y no se pidió
    `todos`, TODO lo de abajo —lista, conteos, completitud y la lista de
    contenedores— se calcula solo sobre ellos. `piloto.faltan` son los del
    piloto que no están en el universo (perdieron su publicación activa)."""
    universo_items = universo.get("items") or []
    pil = skus_piloto(piloto)
    pil_activo = bool(pil) and not todos
    presentes = {it["sku"] for it in universo_items}
    items = ([it for it in universo_items if it["sku"] in set(pil)]
             if pil_activo else universo_items)
    bloque_piloto = {"activo": pil_activo, "skus": pil, "n": len(pil),
                     "total_universo": len(universo_items),
                     "faltan": [s for s in pil if s not in presentes]}
    cta = (cuenta or "").strip().upper() or None
    qq = (q or "").strip().lower() or None
    cont = (contenedor or "").strip() or None
    if cont and cont.upper().startswith("C-"):
        cont = cont[2:]

    def pasa(it: dict) -> bool:
        if cta and not any(c["cuenta"] == cta for c in it["cuentas"]):
            return False
        if clase and it["clase"] != clase:
            return False
        if cont and it.get("contenedor") != cont:
            return False
        if qq and qq not in it["sku"].lower() and qq not in (it.get("titulo") or "").lower():
            return False
        return True

    base = [it for it in items if pasa(it)]
    conteos = {d: 0 for d in DIRECCIONES}
    for it in base:
        conteos[it["direccion"]] = conteos.get(it["direccion"], 0) + 1
    final = [it for it in base if not direccion or it["direccion"] == direccion]

    n = len(base) or 1
    def _pct(pred: Callable[[dict], bool]) -> float:
        return round(100.0 * sum(1 for it in base if pred(it)) / n, 1) if base else 0.0

    def _princ(it: dict) -> dict:
        return next(c for c in it["cuentas"] if c["cuenta"] == it["cuenta_principal"])

    tablas = universo.get("tablas") or {}
    completitud = {
        "comision_real_pct": _pct(lambda it: _princ(it)["contribucion"]["desglose"]["comision_estado"] == "real"),
        "envio_real_pct": _pct(lambda it: _princ(it)["contribucion"]["desglose"]["envio_estado"] == "real"),
        "envio_sin_dato_pct": _pct(lambda it: _princ(it)["contribucion"]["desglose"]["envio_estado"] == "sin_dato"),
        "publicidad": "sin_dato", "full": "sin_dato",
        "devolucion": "estimado" if tablas.get("channel.returns_rate_sku") else "sin_dato",
        "cota_superior": True,
    }
    limite = max(1, min(int(limite or 200), 1000))
    pagina = max(1, int(pagina or 1))
    ini = (pagina - 1) * limite
    contenedores = sorted({it["contenedor"] for it in items if it.get("contenedor")},
                          key=lambda s: (len(s), s))
    return {
        "generado_en": universo.get("generado_en"),
        "ambiente": ambiente,
        "parametros": {**(universo.get("parametros") or PARAMS),
                       "recompra_n": universo.get("recompra_n", 0)},
        "completitud": completitud,
        "conteos": conteos,
        "total": len(final),
        "con_referencia": sum(1 for it in final if it["referencia"]["precio"] is not None),
        "pagina": pagina, "limite": limite,
        "contenedores": contenedores,
        "tablas": tablas,
        "notas": universo.get("notas") or {},
        "piloto": bloque_piloto,
        "items": [_publico(it) for it in final[ini:ini + limite]],
    }


def detalle_de(universo: dict, sku: str) -> dict[str, Any] | None:
    """BLOQUEANTE. El item del SKU más comparables, serie de 90 días y el
    contexto de la categoría. None si el SKU no tiene publicación activa de ML
    (el router contesta 404)."""
    clave = (sku or "").strip().upper()
    it = next((x for x in universo.get("items") or [] if x["sku"] == clave), None)
    if it is None:
        return None
    sdb = _sdb()
    comparables = []
    for f in sdb.fetch_all(_SQL_COMPARABLES, {"sku": clave}):
        comparables.append({
            "posicion": f.get("posicion"), "precio": _num(f.get("precio")),
            # ML califica de 1 a 5: un 0 en la búsqueda es «sin calificación».
            "vendedor": f.get("seller"),
            "rating": (_num(f.get("rating")) if (_num(f.get("rating")) or 0) > 0 else None),
            "reviews": None,  # la búsqueda no guarda reseñas
            "visitas_30d": f.get("visitas_30d"),
            "es_nuestro": bool(f.get("es_nuestro")),
            "titulo": f.get("titulo"),
            "capturado_en": (_utc(f.get("capturado_en")).isoformat()
                             if _utc(f.get("capturado_en")) else None),
        })

    hoy = datetime.now(_zona_mx()).date()
    items = sorted({c["item_id"] for c in it["cuentas"]}
                   | {g for c in it["cuentas"] for g in c.get("gemelas", [])})
    mapa = universo.get("_item_a_sku") or {}
    skus = {x["sku"] for x in universo.get("items") or []}
    por_dia: dict[date, int] = {}
    for f in sdb.fetch_all(_SQL_SERIE, {"sku": clave, "items": items}):
        if asignar_ventas([f], mapa, skus).get(clave):
            por_dia[f["date"]] = por_dia.get(f["date"], 0) + int(_num(f["unidades"]) or 0)
    serie = [{"fecha": (hoy - timedelta(days=d)).isoformat(),
              "unidades": por_dia.get(hoy - timedelta(days=d), 0)}
             for d in range(89, -1, -1)]

    contexto = None
    cat = it.get("_categoria")
    if cat:
        f = sdb.fetch_one(_SQL_CATEGORIA, {"cat": cat})
        if f and f.get("n"):
            contexto = {"categoria_id": cat, "mediana": round(float(f["mediana"]), 2),
                        "n": int(f["n"]),
                        "capturado_en": (_utc(f.get("capturado_en")).isoformat()
                                         if _utc(f.get("capturado_en")) else None)}
    return {**_publico(it), "generado_en": universo.get("generado_en"),
            "comparables": comparables, "serie_90d": serie,
            "contexto_categoria": contexto}
